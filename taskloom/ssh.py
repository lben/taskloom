"""SSH and SFTP to servers, with host keys checked like `ssh` does with accept-new."""

from __future__ import annotations

import shlex
import time
from pathlib import Path

import paramiko


class _AcceptNewHostKey(paramiko.MissingHostKeyPolicy):
    """Trust a server the first time and remember its key; a changed key is refused by paramiko."""

    def __init__(self, known_hosts: Path):
        self.known_hosts = known_hosts

    def missing_host_key(self, client, hostname, key):
        client.get_host_keys().add(hostname, key.get_name(), key)
        self.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        client.save_host_keys(str(self.known_hosts))


def connect(conn: dict, home, timeout: float = 20) -> paramiko.SSHClient:
    """Open an SSH connection from a resolved `kind: ssh` connection (host, port, username, password or key_file)."""
    for key in ("host", "username"):
        if not conn.get(key):
            raise ValueError(f"SSH connection needs '{key}'")
    known_hosts = home.root / "known_hosts"
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(_AcceptNewHostKey(known_hosts))
    client.connect(
        conn["host"], port=int(conn.get("port", 22)), username=conn["username"],
        password=conn.get("password"), key_filename=str(Path(conn["key_file"]).expanduser()) if conn.get("key_file") else None,
        timeout=timeout, allow_agent=False, look_for_keys=not conn.get("password"),
    )
    return client


def run(client: paramiko.SSHClient, command: str, check_cancelled=lambda: None) -> tuple[int, str, str]:
    """Run a shell command; returns (exit code, stdout, stderr). Stops waiting when check_cancelled() raises."""
    _, stdout, stderr = client.exec_command(command)
    channel = stdout.channel
    try:
        out, err = bytearray(), bytearray()
        while True:
            while channel.recv_ready():
                out += channel.recv(65536)
            while channel.recv_stderr_ready():
                err += channel.recv_stderr(65536)
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            check_cancelled()
            time.sleep(0.05)
        return channel.recv_exit_status(), out.decode(errors="replace"), err.decode(errors="replace")
    finally:
        channel.close()  # some servers allow only one open session per connection


class Session:
    """An SSH connection for several commands. Some servers refuse a second command on the same
    connection ("Connect failed"); then it reconnects and tries again once."""

    def __init__(self, conn: dict, home):
        self.conn, self.home = conn, home
        self.client = connect(conn, home)

    def run(self, command: str, check_cancelled=lambda: None) -> tuple[int, str, str]:
        try:
            return run(self.client, command, check_cancelled)
        except paramiko.ChannelException:
            self.client.close()
            self.client = connect(self.conn, self.home)
            return run(self.client, command, check_cancelled)

    def close(self):
        self.client.close()


def remote_path(path: str) -> str:
    """Shell form of a remote path: ~ becomes "$HOME" (which, unlike ~, also expands
    after `NAME=` and inside sh -c), the rest is quoted."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def exit_file(pid_file: str) -> str:
    return f"{pid_file}.exit"


def start_detached(command: str, workdir: str, log_file: str, pid_file: str) -> str:
    """Shell script that starts `command` in the background so it outlives the SSH session.

    The command runs as written (chains with && or ;, its own >> redirections) inside a small
    shell whose PID goes to `pid_file`; with setsid (Linux) that shell leads its own process
    group, so stopping it stops everything the command started. When the command ends, its exit
    code goes to `<pid_file>.exit`.
    """
    # In a subshell, so an `exit` in the command (or a failed cd) does not skip recording its exit code,
    # and the exit file lands where the pid and log files do: a relative path is in the login folder.
    inner = f"(\ncd {remote_path(workdir)} || exit 1\n{command}\n)\necho $? > {remote_path(exit_file(pid_file))}"
    launch = f"nohup sh -c {shlex.quote(inner)} >> {remote_path(log_file)} 2>&1 < /dev/null &"
    return (
        f"mkdir -p \"$(dirname {remote_path(log_file)})\" \"$(dirname {remote_path(pid_file)})\" && "
        f"rm -f {remote_path(exit_file(pid_file))} && "
        f"if command -v setsid >/dev/null 2>&1; then setsid {launch} else {launch} fi; "
        f"echo $! > {remote_path(pid_file)}; echo $!"
    )


def processes(pid: str) -> str:
    """Shell that prints every process belonging to what start_detached started: the shell, its
    process group (with setsid) and all its descendants."""
    return (f"_tl_tree() {{ echo \"$1\"; for c in $(pgrep -P \"$1\"); do _tl_tree \"$c\"; done; }}; "
            f"{{ _tl_tree {pid}; pgrep -g {pid}; }} 2>/dev/null | sort -u")


def alive(pids: list[str]) -> str:
    """Shell that prints which of these processes are still running."""
    return f"for p in {' '.join(pids)}; do kill -0 \"$p\" 2>/dev/null && echo \"$p\"; done; true"


def sftp_path(sftp: paramiko.SFTPClient, path: str) -> str:
    """SFTP has no ~; paths starting with ~/ are relative to the login folder."""
    if path == "~":
        return sftp.normalize(".")
    return path[2:] if path.startswith("~/") else path
