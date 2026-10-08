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


def run(client: paramiko.SSHClient, command: str, cancelled=lambda: False) -> tuple[int, str, str]:
    """Run a shell command; returns (exit code, stdout, stderr). Stops waiting if cancelled()."""
    _, stdout, stderr = client.exec_command(command)
    channel = stdout.channel
    out, err = bytearray(), bytearray()
    while True:
        while channel.recv_ready():
            out += channel.recv(65536)
        while channel.recv_stderr_ready():
            err += channel.recv_stderr(65536)
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        if cancelled():
            channel.close()
            raise InterruptedError("cancelled")
        time.sleep(0.05)
    return channel.recv_exit_status(), out.decode(errors="replace"), err.decode(errors="replace")


def remote_path(path: str) -> str:
    """Shell form of a remote path: ~ becomes "$HOME" (which, unlike ~, also expands
    after `NAME=` and inside sh -c), the rest is quoted."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def start_detached(command: str, workdir: str, log_file: str, pid_file: str) -> str:
    """Shell script that starts `command` in the background so it outlives the SSH session.

    Uses setsid when available (Linux) so the process leaves the session; writes its PID.
    """
    inner = f"cd {remote_path(workdir)} && exec {command}"
    launch = f"nohup sh -c {shlex.quote(inner)} >> {remote_path(log_file)} 2>&1 < /dev/null &"
    return (
        f"mkdir -p \"$(dirname {remote_path(log_file)})\" \"$(dirname {remote_path(pid_file)})\" && "
        f"if command -v setsid >/dev/null 2>&1; then setsid {launch} else {launch} fi; "
        f"echo $! > {remote_path(pid_file)}; echo $!"
    )


def sftp_path(sftp: paramiko.SFTPClient, path: str) -> str:
    """SFTP has no ~; paths starting with ~/ are relative to the login folder."""
    if path == "~":
        return sftp.normalize(".")
    return path[2:] if path.startswith("~/") else path
