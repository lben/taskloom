"""Real use and edge paths of the Servers blocks: SSH Command, Start Remote Process, Stop Remote Process,
Health Check and Remote Extract, run with `taskloom run` against a real SSH server on localhost.

The "server" is this machine: its home is a temporary folder, so the pids it reports are local pids.
"""

import asyncio
import http.server
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import datetime

import asyncssh
import pytest
import yaml

from conftest import REPO
from sshserver import PASSWORD, USER, SSHTestServer, free_port
from test_servers import _kill_from_pid_file

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the test server runs commands in /bin/sh")

PY = sys.executable


# --- helpers ------------------------------------------------------------------------------------------

def _write_flow(env, blocks, edges=(), **extra):
    path = env.root / "flow.yaml"
    path.write_text(yaml.safe_dump({**extra, "blocks": blocks, "edges": list(edges)}, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    return path


def _run(env, blocks, edges=(), **extra):
    """Run the flow with `taskloom run`; returns the process result, the run id and each block's status."""
    result = env.taskloom("run", _write_flow(env, blocks, edges, **extra))
    run_id = env.last_run()[0]
    return result, run_id, env.block_status(run_id)


def _error(env, run_id, block):
    return env.query("SELECT error FROM blocks WHERE run_id=? AND block=?", run_id, block)[0][0]


def _logs(env, run_id, block):
    return env.query("SELECT level, message FROM logs WHERE run_id=? AND block=?", run_id, block)


def _took(env, run_id, block) -> float:
    """Seconds the block ran, as run history recorded it (so a slow start of `taskloom` does not count)."""
    [(began, ended)] = env.query("SELECT started, finished FROM blocks WHERE run_id=? AND block=?", run_id, block)
    return (datetime.fromisoformat(ended) - datetime.fromisoformat(began)).total_seconds()


def _save(name):
    """A Python Code block that writes what reaches its input to a JSON file next to the flow."""
    return {"type": "logic.python", "config": {
        "outputs": [], "code": f"import json\nopen({name!r}, 'w', encoding='utf-8').write(json.dumps(input))"}}


def _saved(env, name):
    return json.loads((env.root / name).read_text(encoding="utf-8"))


def _ssh(command, **config):
    return {"type": "servers.ssh_command", "config": {"connection": "appserver", "command": command, **config}}


def _start(command, **config):
    return {"type": "servers.start_process", "config": {
        "connection": "appserver", "command": command, "pid_file": "~/app/app.pid", "log_file": "~/app/app.log",
        "check_after": 0.5, **config}}


def _stop(**config):
    return {"type": "servers.stop_process", "config": {"connection": "appserver", "pid_file": "~/app/app.pid", **config}}


def _health(**config):
    return {"type": "servers.health_check", "config": config}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _running(pattern: str) -> bool:
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def _gone(pattern: str, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while _running(pattern):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def _pkill(pattern: str):
    subprocess.run(["pkill", "-KILL", "-f", pattern], capture_output=True)


def _files_holding(root, text):
    """Files under root (secrets.yaml aside, which holds it on purpose) whose bytes contain text."""
    data = text.encode()
    return [p for p in root.rglob("*") if p.is_file() and p.name != "secrets.yaml" and data in p.read_bytes()]


@contextmanager
def _web():
    """A local web app: GET /<code> answers with that status code."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(int(self.path.strip("/") or 200))
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@contextmanager
def _silent_port():
    """A port that accepts connections and then never says anything."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    try:
        yield s.getsockname()[1]
    finally:
        s.close()


def _archive(path, files: dict):
    """A .zip, .tar, .tgz or .tar.gz holding files {name: text}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(path, "w") as z:
            for name, text in files.items():
                z.writestr(name, text.encode())
        return
    with tarfile.open(path, "w" if path.name.endswith(".tar") else "w:gz") as t:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


class _KeyServer(SSHTestServer):
    """The test server, but it lets in only the holder of one key (no passwords)."""

    def __init__(self, home, client_key, port=None):
        super().__init__(home, port=port)
        self.client_key = client_key

    def start(self):
        allowed = self.client_key.public_data

        class KeyOnly(asyncssh.SSHServer):
            def begin_auth(self, username):
                return True

            def public_key_auth_supported(self):
                return True

            def validate_public_key(self, username, key):
                return username == USER and key.public_data == allowed

        self._thread.start()

        async def listen():
            return await asyncssh.listen("127.0.0.1", self.port, server_factory=KeyOnly, server_host_keys=[self.host_key],
                                         process_factory=self._process, encoding="utf-8")

        self._acceptor = asyncio.run_coroutine_threadsafe(listen(), self._loop).result(10)
        return self


class _OneCommandServer(SSHTestServer):
    """The test server, but like some servers it refuses a second command on the same connection."""

    def start(self):
        home = self.home

        async def run_command(stdin, stdout, stderr):
            proc = await asyncio.create_subprocess_shell(
                stdout.channel.get_command(), cwd=home, env={**os.environ, "HOME": home},
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await proc.communicate()
            stdout.write(out.decode(errors="replace"))
            stderr.write(err.decode(errors="replace"))
            stdout.channel.exit(proc.returncode)

        class OneSession(asyncssh.SSHServer):
            sessions = 0

            def begin_auth(self, username):
                return True

            def password_auth_supported(self):
                return True

            def validate_password(self, username, password):
                return username == USER and password == PASSWORD

            def session_requested(self):
                self.sessions += 1
                return run_command if self.sessions == 1 else False

        self._thread.start()

        async def listen():
            return await asyncssh.listen("127.0.0.1", self.port, server_factory=OneSession,
                                         server_host_keys=[self.host_key], encoding="utf-8")

        self._acceptor = asyncio.run_coroutine_threadsafe(listen(), self._loop).result(10)
        return self


# --- SSH Command ----------------------------------------------------------------------------------------

def test_ssh_command_gives_what_it_printed_and_its_exit_code(env, server):
    result, run_id, status = _run(env, {
        "cmd": _ssh("echo 'line 1'\necho 'café ✓ 日本'\necho 'a warning' >&2"),
        "out": _save("stdout.json"),
        "code": _save("code.json"),
    }, ["cmd.stdout -> out.input", "cmd.exit_code -> code.input"])

    assert result.returncode == 0, result.stderr
    assert status == {"cmd": "success", "out": "success", "code": "success"}
    assert _saved(env, "stdout.json") == "line 1\ncafé ✓ 日本\n"
    assert _saved(env, "code.json") == 0
    logs = _logs(env, run_id, "cmd")
    assert ("INFO", "line 1\ncafé ✓ 日本") in logs
    assert ("WARNING", "a warning") in logs


def test_ssh_command_that_fails_stops_the_flow_with_its_exit_code_and_stderr(env, server):
    result, run_id, status = _run(env, {
        "cmd": _ssh("echo partial; echo 'no such table: sales' >&2; exit 3"),
        "out": _save("stdout.json"),
    }, ["cmd.stdout -> out.input"])

    assert result.returncode == 1
    assert status == {"cmd": "failed", "out": "skipped"}
    assert _error(env, run_id, "cmd") == "RuntimeError: command exited with code 3: no such table: sales"
    assert not (env.root / "stdout.json").exists()


def test_ssh_command_with_fail_on_error_off_passes_the_exit_code_on(env, server):
    result, run_id, status = _run(env, {
        "cmd": _ssh("echo partial; echo bad >&2; exit 3", fail_on_error=False),
        "out": _save("stdout.json"),
        "code": _save("code.json"),
    }, ["cmd.stdout -> out.input", "cmd.exit_code -> code.input"])

    assert result.returncode == 0, result.stderr
    assert status == {"cmd": "success", "out": "success", "code": "success"}
    assert _saved(env, "stdout.json") == "partial\n"
    assert _saved(env, "code.json") == 3


def test_ssh_command_fills_in_parameters_and_keeps_doubled_braces(env, server):
    blocks = {"cmd": _ssh("echo report-{day}; echo x | awk '{{print \"got\", $1}}'"), "out": _save("stdout.json")}
    edges = ["cmd.stdout -> out.input"]
    params = {"day": {"type": "text", "value": "2026-10-02"}}

    result, _, _ = _run(env, blocks, edges, params=params)
    assert result.returncode == 0, result.stderr
    assert _saved(env, "stdout.json") == "report-2026-10-02\ngot x\n"

    again = env.taskloom("run", env.root / "flow.yaml", "--param", "day=2026-01-05")
    assert again.returncode == 0, again.stderr
    assert _saved(env, "stdout.json") == "report-2026-01-05\ngot x\n"


def test_ssh_command_with_single_braces_is_refused_before_anything_runs(env, server):
    flow = _write_flow(env, {"cmd": _ssh("touch ran.txt; echo ${HOME}")})

    result = env.taskloom("run", flow)

    assert result.returncode == 2
    assert "cmd: Command:" in result.stderr and "HOME" in result.stderr
    assert not (server.home_path / "ran.txt").exists()


def test_ssh_command_large_output_arrives_complete_and_in_order(env, server):
    code = "import sys\nfor i in range(200000): print(i)\nsys.stderr.write('e' * 300000)"
    (server.home_path / "big.py").write_text(code)
    result, _, status = _run(env, {"cmd": _ssh(f"{PY} big.py"), "out": _save("stdout.json")}, ["cmd.stdout -> out.input"])

    assert result.returncode == 0, result.stderr[-2000:]
    assert status["cmd"] == "success"
    assert _saved(env, "stdout.json") == "".join(f"{i}\n" for i in range(200000))


def test_ssh_command_timeout_stops_waiting_for_the_server(env, server):
    try:
        result, run_id, status = _run(env, {"cmd": {**_ssh("sleep 9.137; echo too late"), "timeout": "1s"}})

        assert result.returncode == 1
        assert status == {"cmd": "failed"}
        assert _error(env, run_id, "cmd") == "TimeoutError: timed out after 1 s"
        assert _took(env, run_id, "cmd") < 6
    finally:
        _pkill("sleep 9.137")


def test_cancelling_a_run_stops_a_waiting_ssh_command(env, server):
    """Stop (the editor) or Ctrl+C: the run and the block waiting for the server end as cancelled. Done a few
    times, because how the block ends depends on timing."""
    flow = _write_flow(env, {"cmd": _ssh("sleep 9.241")})
    ended = []
    try:
        for _ in range(6):
            proc = env.popen("run", flow, "--interactive", "--json-events")
            try:
                for line in proc.stdout:
                    if line.startswith("{") and json.loads(line)["event"] == "block_started":
                        break
                time.sleep(0.3)
                started = time.monotonic()
                proc.stdin.write(json.dumps({"cancel": True}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=20)
                assert proc.returncode == 130
                assert time.monotonic() - started < 5
            finally:
                proc.kill()
                proc.communicate()
            run_id, run_status = env.last_run()
            ended.append((run_status, env.block_status(run_id)["cmd"], _error(env, run_id, "cmd")))
    finally:
        _pkill("sleep 9.241")

    assert ended == [("cancelled", "cancelled", None)] * 6


# --- connections, passwords and keys ----------------------------------------------------------------------

def test_a_password_the_server_prints_never_reaches_logs_or_history(env, server):
    (server.home_path / "app.conf").write_text(f"db_user=app\ndb_password={PASSWORD}\n")
    result, run_id, status = _run(env, {
        "cmd": _ssh("cat app.conf"),
        "count": {"type": "logic.python", "config": {"code": "output = len(input)"}},
        "save": _save("count.json"),
        "fail": {"type": "logic.python", "config": {"outputs": [], "code": "raise ValueError('cannot use ' + input)"}},
        "leak": _ssh("cat app.conf >&2; exit 1"),  # in its warning and in its own error
    }, ["cmd.stdout -> count.input", "count.output -> save.input", "cmd.stdout -> fail.input"])

    assert result.returncode == 1
    assert status == {"cmd": "success", "count": "success", "save": "success", "fail": "failed", "leak": "failed"}
    assert _saved(env, "count.json") == len(f"db_user=app\ndb_password={PASSWORD}\n")  # the value itself got through
    assert PASSWORD not in result.stdout + result.stderr
    assert _files_holding(env.home, PASSWORD) == []
    assert "***" in _error(env, run_id, "fail")
    assert "db_password=***" in _error(env, run_id, "leak")


def test_a_wrong_password_fails_clearly_and_is_never_shown(env, server):
    env.write("home/secrets.yaml", "appserver_password: not-the-password\n").chmod(0o600)
    result, run_id, status = _run(env, {"cmd": _ssh("echo hi")})

    assert result.returncode == 1
    assert status == {"cmd": "failed"}
    assert "Authentication failed" in _error(env, run_id, "cmd")
    assert "not-the-password" not in result.stdout + result.stderr
    assert _files_holding(env.home, "not-the-password") == []


def test_a_missing_secret_says_how_to_set_it(env, server):
    env.write("home/secrets.yaml", "other: x\n").chmod(0o600)
    result, run_id, _ = _run(env, {"cmd": _ssh("echo hi")})

    assert result.returncode == 1
    error = _error(env, run_id, "cmd")
    assert "secret 'appserver_password' is not set" in error
    assert "taskloom secret set appserver_password" in error


def test_a_secrets_file_others_can_read_is_refused(env, server):
    (env.home / "secrets.yaml").chmod(0o644)
    result, run_id, _ = _run(env, {"cmd": _ssh("echo hi")})

    assert result.returncode == 1
    assert "must be readable only by you (chmod 600)" in _error(env, run_id, "cmd")


def test_every_server_block_refuses_a_missing_or_non_ssh_connection_before_running(env, server):
    with open(env.home / "connections.yaml", "a") as f:
        f.write("mailer:\n  kind: smtp\n  host: 127.0.0.1\n")
    blocks = {
        "cmd": _ssh("touch ran.txt", connection="mailer"),
        "start": _start("touch ran.txt", connection="nosuch"),
        "stop": _stop(connection="mailer"),
        "check": _health(connection="nosuch", pid_file="~/app/app.pid"),
        "extract": {"type": "servers.remote_extract", "config": {"connection": "mailer", "archive": "a.zip",
                                                                 "destination": "out"}},
    }

    result = env.taskloom("run", _write_flow(env, blocks))

    assert result.returncode == 2
    for block in ("cmd", "stop", "extract"):
        assert f"{block}: Connection: connection 'mailer' is smtp, expected ssh" in result.stderr
    for block in ("start", "check"):
        assert f"{block}: Connection: no connection named 'nosuch' in connections.yaml" in result.stderr
    assert not (server.home_path / "ran.txt").exists()


def test_an_ssh_connection_without_a_user_name_fails_clearly(env, server):
    with open(env.home / "connections.yaml", "a") as f:
        f.write(f"nouser:\n  kind: ssh\n  host: 127.0.0.1\n  port: {server.port}\n  password: 'secret:appserver_password'\n")
    result, run_id, _ = _run(env, {"cmd": _ssh("echo hi", connection="nouser")})

    assert result.returncode == 1
    assert "SSH connection needs 'username'" in _error(env, run_id, "cmd")


def test_a_server_nobody_listens_on_fails_quickly_and_names_the_port(env, server):
    port = free_port()
    with open(env.home / "connections.yaml", "a") as f:
        f.write(f"down:\n  kind: ssh\n  host: 127.0.0.1\n  port: {port}\n  username: {USER}\n"
                f"  password: 'secret:appserver_password'\n")
    result, run_id, _ = _run(env, {"cmd": _ssh("echo hi", connection="down")})

    assert result.returncode == 1
    assert str(port) in _error(env, run_id, "cmd")
    assert _took(env, run_id, "cmd") < 5


def test_a_server_that_never_answers_is_bounded_by_the_block_timeout(env, server):
    with _silent_port() as port:
        with open(env.home / "connections.yaml", "a") as f:
            f.write(f"silent:\n  kind: ssh\n  host: 127.0.0.1\n  port: {port}\n  username: {USER}\n"
                    f"  password: 'secret:appserver_password'\n")
        result, run_id, _ = _run(env, {"cmd": {**_ssh("echo hi", connection="silent"), "timeout": "2s"}})

        assert result.returncode == 1
        assert _error(env, run_id, "cmd") == "TimeoutError: timed out after 2 s"
        assert _took(env, run_id, "cmd") < 6


def test_logging_in_with_a_key_file_instead_of_a_password(env, tmp_path):
    (tmp_path / "srv").mkdir()
    key = asyncssh.generate_private_key("ssh-ed25519")
    pc_home = tmp_path / "pc"
    key_file = pc_home / "keys" / "id_test"
    key_file.parent.mkdir(parents=True)
    key_file.write_bytes(key.export_private_key("openssh"))
    key_file.chmod(0o600)
    env.env["HOME"] = str(pc_home)  # so ~ in key_file is this folder, and no real key is ever tried
    srv = _KeyServer(tmp_path / "srv", key).start()
    try:
        env.write("home/connections.yaml", f"""
            keyed:
              kind: ssh
              host: 127.0.0.1
              port: {srv.port}
              username: {USER}
              key_file: ~/keys/id_test
            nokey:
              kind: ssh
              host: 127.0.0.1
              port: {srv.port}
              username: {USER}
              key_file: ~/keys/missing
        """)
        tested = env.taskloom("connection", "test", "keyed")
        assert tested.returncode == 0, tested.stderr
        assert f"Connected to 127.0.0.1 as {USER}" in tested.stdout

        result, run_id, status = _run(env, {
            "cmd": _ssh("echo logged in with a key", connection="keyed"), "out": _save("stdout.json"),
            "bad": _ssh("echo hi", connection="nokey")}, ["cmd.stdout -> out.input"])
        assert status == {"cmd": "success", "out": "success", "bad": "failed"}
        assert _saved(env, "stdout.json") == "logged in with a key\n"
        assert str(pc_home / "keys" / "missing") in _error(env, run_id, "bad")
    finally:
        srv.stop()


def test_the_server_key_is_remembered_and_a_changed_one_is_refused(env, tmp_path):
    (tmp_path / "srv").mkdir()
    env.write("home/secrets.yaml", f"appserver_password: {PASSWORD}\n").chmod(0o600)
    first = SSHTestServer(tmp_path / "srv").start()
    env.write("home/connections.yaml", first.connection())
    try:
        result, _, status = _run(env, {"cmd": _ssh("echo hi")})
        assert status == {"cmd": "success"}, result.stderr
    finally:
        first.stop()
    known_hosts = env.home / "known_hosts"
    remembered = known_hosts.read_text()
    assert f"[127.0.0.1]:{first.port}" in remembered

    impostor = SSHTestServer(tmp_path / "srv", port=first.port).start()  # same address, another key
    try:
        result, run_id, status = _run(env, {"cmd": _ssh("touch ran.txt")})
    finally:
        impostor.stop()
    assert result.returncode == 1 and status == {"cmd": "failed"}
    assert "does not match" in _error(env, run_id, "cmd")
    assert not (tmp_path / "srv" / "ran.txt").exists()
    assert known_hosts.read_text() == remembered


def test_a_server_that_allows_one_command_per_connection_still_starts_checks_and_stops_apps(env, tmp_path):
    home = tmp_path / "srv"
    home.mkdir()
    env.write("home/secrets.yaml", f"appserver_password: {PASSWORD}\n").chmod(0o600)
    srv = _OneCommandServer(home).start()
    env.write("home/connections.yaml", srv.connection())
    pid_file = home / "app" / "app.pid"
    try:
        result, _, status = _run(env, {"start": _start("sleep 600.367")})
        assert status == {"start": "success"}, result.stderr

        result, run_id, status = _run(env, {
            "check": _health(connection="appserver", pid_file="~/app/app.pid"), "r": _save("result.json"),
            "stop": _stop(), "s": _save("stopped.json"),
        }, ["check.result -> r.input", "check.result -> stop.after", "stop.stopped -> s.input"])

        assert status == {"check": "success", "r": "success", "stop": "success", "s": "success"}, result.stderr
        assert _saved(env, "result.json")["healthy"] is True
        assert _saved(env, "stopped.json") is True
        assert _gone("sleep 600.367")
    finally:
        srv.stop()
        _kill_from_pid_file(pid_file)
        _pkill("sleep 600.367")


def test_an_ssh_connection_made_in_the_connections_window_logs_in_and_runs_flows(env, server, win, qtbot, monkeypatch):
    import keyring.core
    from keyring.backends import fail
    from PySide6.QtWidgets import QDialogButtonBox, QInputDialog

    from taskloom.editor.dialogs import ConnectionsDialog

    monkeypatch.setattr(keyring.core, "_keyring_backend", fail.Keyring())  # never the real credential store
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("box", True)))
    dialog = ConnectionsDialog(win.home, win, new_kind="ssh")
    dialog.edits["host"].setText("127.0.0.1")
    dialog.edits["port"].setText(str(server.port))
    dialog.edits["username"].setText(USER)
    dialog.password.setText(PASSWORD)

    dialog.test_button.click()  # Save and test connection
    qtbot.waitUntil(lambda: "✓" in dialog.test_result.text() or "✕" in dialog.test_result.text(), timeout=30_000)
    assert f"Connected to 127.0.0.1 as {USER}" in dialog.test_result.text()
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()

    text = (env.home / "connections.yaml").read_text()
    assert PASSWORD not in text
    assert yaml.safe_load(text)["box"] == {"kind": "ssh", "host": "127.0.0.1", "port": server.port,
                                           "username": USER, "password": "secret:box_password"}
    result, _, status = _run(env, {"cmd": _ssh("echo hi", connection="box")})
    assert status == {"cmd": "success"}, result.stderr


def test_an_ssh_command_set_up_in_properties_and_run_from_the_editor(env, server, smtp, win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QComboBox, QFileDialog, QLineEdit

    from test_editor import add_from_palette, drag_port_to_port, wait_done

    flow_path = env.root / "remote.yaml"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(flow_path), ""))
    tab = win.new_flow()
    add_from_palette(win, "SSH Command")
    [block] = [b for b in tab.doc.blocks if b != "start"]
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)

    combo = next(c for c in win.properties.findChildren(QComboBox) if c.findData("appserver") >= 0)
    assert combo.findData("mailer") < 0  # only SSH connections are offered
    combo.setCurrentIndex(combo.findData("appserver"))
    combo.activated.emit(combo.currentIndex())
    command = next(e for e in win.properties.findChildren(QLineEdit) if e.toolTip() == "Runs in your login shell on the server")
    command.setText("echo hello from $(basename \"$HOME\")")
    command.editingFinished.emit()
    drag_port_to_port(tab, "start", "start", block, "after")
    assert win.save(tab)

    win.run_flow(tab)
    wait_done(qtbot, tab)

    assert tab.scene.nodes[block].status == "success"
    assert "hello from server" in win.log.text.toPlainText()
    saved = yaml.safe_load(flow_path.read_text())["blocks"][block]["config"]
    assert saved["connection"] == "appserver"
    assert saved["command"] == "echo hello from $(basename \"$HOME\")"


def test_server_block_settings_of_the_wrong_type_are_refused_before_anything_runs(env, server):
    blocks = {
        "cmd": _ssh("touch ran.txt", fail_on_error="no"),
        "start": _start("touch ran.txt", check_after="soon"),
        "stop": _stop(grace="later"),
        "check": _health(connection="appserver", port="http"),
        "nocommand": {"type": "servers.ssh_command", "config": {"connection": "appserver"}},
    }

    result = env.taskloom("run", _write_flow(env, blocks))

    assert result.returncode == 2
    for block, field in (("cmd", "Fail on error"), ("start", "Check after"), ("stop", "Grace"), ("check", "Port")):
        assert f"{block}: {field}: " in result.stderr, (block, result.stderr)
    assert "nocommand: Command is required" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (server.home_path / "ran.txt").exists()


# --- Start Remote Process -------------------------------------------------------------------------------

def test_start_process_keeps_the_app_running_after_taskloom_disconnects(env, server):
    pid_file = server.home_path / "run" / "web.pid"
    port = free_port()
    (server.home_path / "site").mkdir()
    (server.home_path / "site" / "index.html").write_text("hello from the site")
    try:
        result, run_id, status = _run(env, {
            "start": _start(f"{PY} -m http.server {port} --bind 127.0.0.1", workdir="~/site",
                            log_file="~/logs/web/site.log", pid_file="~/run/web.pid", check_after=2),
            "pid": _save("pid.json"),
        }, ["start.pid -> pid.input"])

        assert result.returncode == 0, result.stderr
        assert status == {"start": "success", "pid": "success"}
        pid = _saved(env, "pid.json")
        assert pid == int(pid_file.read_text())
        assert _alive(pid)  # Taskloom has disconnected and exited: the app is still there
        assert any("still running after 2 s" in m for _, m in _logs(env, run_id, "start"))
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as page:  # served from workdir
            assert page.read() == b"hello from the site"
        assert '"GET / HTTP/1.1" 200' in (server.home_path / "logs" / "web" / "site.log").read_text()
    finally:
        _kill_from_pid_file(pid_file)


def test_start_and_stop_with_the_default_folders_and_files(env, server):
    pid_file = server.home_path / ".taskloom" / "apps" / "app.pid"
    try:
        result, _, status = _run(env, {"start": {"type": "servers.start_process", "config": {
            "connection": "appserver", "command": "pwd; sleep 600.311"}}})
        assert status == {"start": "success"}, result.stderr
        assert _alive(int(pid_file.read_text()))
        assert str(server.home_path) in (server.home_path / ".taskloom" / "apps" / "app.log").read_text().splitlines()

        result, _, status = _run(env, {"stop": {"type": "servers.stop_process", "config": {"connection": "appserver"}},
                                       "stopped": _save("stopped.json")}, ["stop.stopped -> stopped.input"])
        assert status == {"stop": "success", "stopped": "success"}, result.stderr
        assert _saved(env, "stopped.json") is True
        assert _gone("sleep 600.311")
    finally:
        _kill_from_pid_file(pid_file)
        _pkill("sleep 600.311")


def test_start_process_removes_a_final_ampersand_and_still_keeps_track(env, server):
    pid_file = server.home_path / "app" / "app.pid"
    try:
        result, run_id, status = _run(env, {"start": _start("sleep 600.421 &")})
        assert status == {"start": "success"}, result.stderr
        assert any("removed the & at the end" in m for _, m in _logs(env, run_id, "start"))
        assert _running("sleep 600.421")

        result, _, status = _run(env, {"stop": _stop(), "out": _save("stopped.json")}, ["stop.stopped -> out.input"])
        assert status == {"stop": "success", "out": "success"}, result.stderr
        assert _saved(env, "stopped.json") is True  # what it tracked is the app itself
        assert _gone("sleep 600.421")
    finally:
        _kill_from_pid_file(pid_file)
        _pkill("sleep 600.421")


def test_start_process_keeps_quotes_unicode_and_spaces(env, server):
    folder = server.home_path / "my apps" / "v 2 é"
    folder.mkdir(parents=True)
    pid_file = server.home_path / "my run" / "app 1.pid"
    command = "printf '%s\\n' \"it's \\\"quoted\\\" é ✓ $HOME\"; pwd; sleep 600.533"
    try:
        result, _, status = _run(env, {"start": _start(command, workdir="~/my apps/v 2 é", log_file="~/my logs/app 1.log",
                                                       pid_file="~/my run/app 1.pid")})
        assert status == {"start": "success"}, result.stderr
        assert (server.home_path / "my logs" / "app 1.log").read_text(encoding="utf-8").splitlines() == [
            f"it's \"quoted\" é ✓ {server.home_path}", str(folder)]
        assert _alive(int(pid_file.read_text()))

        result, _, status = _run(env, {"stop": _stop(pid_file="~/my run/app 1.pid"), "out": _save("stopped.json")},
                                 ["stop.stopped -> out.input"])
        assert status == {"stop": "success", "out": "success"}, result.stderr
        assert _saved(env, "stopped.json") is True
        assert _gone("sleep 600.533")
    finally:
        _kill_from_pid_file(pid_file)
        _pkill("sleep 600.533")


def test_start_process_with_the_commands_own_log_file_as_the_readme_shows(env, server):
    app = server.home_path / "app"
    app.mkdir()
    script = app / "run.sh"
    pid_file = server.home_path / ".taskloom" / "apps" / "app.pid"
    command = "cd app && ./run.sh >> app.log 2>&1"
    start = {"type": "servers.start_process", "config": {"connection": "appserver", "command": command,
                                                          "check_after": 0.5}}
    try:
        script.write_text("#!/bin/sh\necho serving\nexec sleep 600.613\n")
        script.chmod(0o755)
        result, _, status = _run(env, {"start": start})
        assert status == {"start": "success"}, result.stderr
        assert (app / "app.log").read_text() == "serving\n"
        assert _running("sleep 600.613")

        result, _, status = _run(env, {"stop": {"type": "servers.stop_process", "config": {"connection": "appserver"}},
                                       "out": _save("stopped.json")}, ["stop.stopped -> out.input"])
        assert status == {"stop": "success", "out": "success"}, result.stderr
        assert _saved(env, "stopped.json") is True
        assert _gone("sleep 600.613")

        script.write_text("#!/bin/sh\necho 'error: settings.ini is missing'\nexit 2\n")
        result, run_id, status = _run(env, {"start": start})
        assert status == {"start": "failed"}
        error = _error(env, run_id, "start")
        assert "exited with code 2" in error
        assert "writes its output to its own file" in error  # points to app.log, where the reason is
    finally:
        _kill_from_pid_file(pid_file)
        _pkill("sleep 600.613")


def test_start_process_of_a_command_that_finishes_at_once_says_to_untick_must_keep_running(env, server):
    result, run_id, status = _run(env, {"start": _start("echo service started")})

    assert result.returncode == 1 and status == {"start": "failed"}
    error = _error(env, run_id, "start")
    assert "exit code 0" in error and "must_keep_running" in error
    assert "service started" in error  # the last lines of its log


def test_start_process_in_a_folder_that_does_not_exist_fails_and_says_why(env, server):
    result, run_id, status = _run(env, {"start": _start("sleep 9.647", workdir="~/no such folder")})

    assert result.returncode == 1 and status == {"start": "failed"}
    error = _error(env, run_id, "start")
    assert "no such folder" in error
    assert not _running("sleep 9.647")


def test_start_process_checks_after_check_after_seconds(env, server):
    pid_file = server.home_path / "app" / "app.pid"
    try:
        result, _, status = _run(env, {"start": _start("sleep 1.5; exit 4", check_after=0.2)})
        assert status == {"start": "success"}, result.stderr  # still running when checked

        result, run_id, status = _run(env, {"start": _start("sleep 0.3; exit 4", check_after=1.5)})
        assert status == {"start": "failed"}
        assert "the command exited with code 4 right after starting" in _error(env, run_id, "start")
    finally:
        _kill_from_pid_file(pid_file)


def test_start_process_that_crashes_shows_the_exit_code_and_last_log_lines(env, server):
    result, run_id, status = _run(env, {"start": _start("echo starting; echo 'error: port 8501 in use' >&2; exit 3")})

    assert result.returncode == 1 and status == {"start": "failed"}
    error = _error(env, run_id, "start")
    assert "exited with code 3 right after starting" in error
    assert "Last lines of ~/app/app.log" in error and "error: port 8501 in use" in error


def test_must_keep_running_off_accepts_a_command_that_finishes_but_not_one_that_fails(env, server):
    pid_file = server.home_path / "app" / "app.pid"
    try:
        result, run_id, status = _run(env, {"start": _start("echo service started", must_keep_running=False),
                                            "pid": _save("pid.json")}, ["start.pid -> pid.input"])
        assert status == {"start": "success", "pid": "success"}, result.stderr
        assert _saved(env, "pid.json") == int(pid_file.read_text())
        assert any("finished with exit code 0" in m and "service started" in m for _, m in _logs(env, run_id, "start"))

        result, run_id, status = _run(env, {"start": _start("echo cannot start; exit 2", must_keep_running=False)})
        assert status == {"start": "failed"}
        assert "exited with code 2" in _error(env, run_id, "start")

        result, _, status = _run(env, {"start": _start("sleep 9.751", must_keep_running=False)})
        assert status == {"start": "success"}, result.stderr
    finally:
        _kill_from_pid_file(pid_file)
        _pkill("sleep 9.751")


def test_start_process_with_a_pid_file_relative_to_the_login_folder_and_another_workdir(env, server):
    (server.home_path / "work").mkdir()
    pid_file = server.home_path / "app.pid"
    try:
        result, run_id, status = _run(env, {"start": _start("echo service started", workdir="~/work", pid_file="app.pid",
                                                            log_file="app.log", must_keep_running=False)})
        assert status == {"start": "success"}, _error(env, run_id, "start")

        result, run_id, status = _run(env, {"start": _start("exit 3", workdir="~/work", pid_file="app.pid",
                                                            log_file="app.log")})
        assert status == {"start": "failed"}
        assert "exited with code 3" in _error(env, run_id, "start")
    finally:
        _kill_from_pid_file(pid_file)


# --- Stop Remote Process --------------------------------------------------------------------------------

def test_stop_process_when_nothing_is_running_says_so(env, server):
    dead = subprocess.Popen(["true"])
    dead.wait()
    (server.home_path / "app").mkdir()
    (server.home_path / "app" / "dead.pid").write_text(f"{dead.pid}\n")
    (server.home_path / "app" / "garbage.pid").write_text("not a pid\n")
    (server.home_path / "app" / "empty.pid").write_text("")
    cases = {"missing": "~/app/missing.pid", "dead": "~/app/dead.pid", "garbage": "~/app/garbage.pid",
             "empty": "~/app/empty.pid"}
    blocks, edges = {}, []
    for name, path in cases.items():
        blocks[name] = _stop(pid_file=path)
        blocks[f"{name}_out"] = _save(f"{name}.json")
        edges.append(f"{name}.stopped -> {name}_out.input")

    result, run_id, status = _run(env, blocks, edges)

    assert result.returncode == 0, result.stderr
    assert set(status.values()) == {"success"}
    for name in cases:
        assert _saved(env, f"{name}.json") is False, name
        assert ("INFO", "the process was not running") in _logs(env, run_id, name)


def test_stop_process_stops_everything_the_command_started(env, server):
    pid_file = server.home_path / "app" / "app.pid"
    try:
        result, _, status = _run(env, {"start": _start("sleep 600.811 & sleep 600.812")})
        assert status == {"start": "success"}, result.stderr
        assert _running("sleep 600.811") and _running("sleep 600.812")

        result, _, status = _run(env, {"stop": _stop(), "out": _save("stopped.json")}, ["stop.stopped -> out.input"])
        assert status == {"stop": "success", "out": "success"}, result.stderr
        assert _saved(env, "stopped.json") is True
        assert _gone("sleep 600.811") and _gone("sleep 600.812")
    finally:
        _kill_from_pid_file(pid_file)
        _pkill("sleep 600.81")


def test_stop_process_waits_grace_seconds_for_an_app_that_takes_a_moment_to_stop(env, server):
    (server.home_path / "slowstop.py").write_text(
        "import pathlib, signal, sys, time\n"
        "def stop(*_):\n"
        "    time.sleep(0.8)\n"
        "    pathlib.Path('cleaned-up.txt').write_text('yes')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "time.sleep(600)\n")
    pid_file = server.home_path / "app" / "app.pid"
    try:
        result, _, status = _run(env, {"start": _start(f"{PY} slowstop.py")})
        assert status == {"start": "success"}, result.stderr

        result, run_id, status = _run(env, {"stop": _stop(grace=5)})
        assert status == {"stop": "success"}, result.stderr
        assert (server.home_path / "cleaned-up.txt").read_text() == "yes"  # it was let to finish
        assert not any(level == "WARNING" for level, _ in _logs(env, run_id, "stop"))
        assert _gone("slowstop.py")
    finally:
        _kill_from_pid_file(pid_file)
        _pkill("slowstop.py")


def test_stop_process_forces_an_app_that_ignores_the_request_after_grace(env, server):
    (server.home_path / "stubborn.py").write_text(
        "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(600)\n")
    pid_file = server.home_path / "app" / "app.pid"
    try:
        result, _, status = _run(env, {"start": _start(f"{PY} stubborn.py")})
        assert status == {"start": "success"}, result.stderr

        result, run_id, status = _run(env, {"stop": _stop(grace=1), "out": _save("stopped.json")},
                                      ["stop.stopped -> out.input"])

        assert status == {"stop": "success", "out": "success"}, result.stderr
        assert _saved(env, "stopped.json") is True
        assert _gone("stubborn.py")
        assert any(level == "WARNING" for level, _ in _logs(env, run_id, "stop"))  # it had to be forced
        assert _took(env, run_id, "stop") < 8  # forced after 1 s, not after the default 10 s
    finally:
        _kill_from_pid_file(pid_file)
        _pkill("stubborn.py")


# --- Health Check ---------------------------------------------------------------------------------------

def _health_flow(**config):
    return ({"check": _health(**config), "on_healthy": _save("healthy.json"), "on_unhealthy": _save("unhealthy.json"),
             "result": _save("result.json")},
            ["check.healthy -> on_healthy.input", "check.unhealthy -> on_unhealthy.input", "check.result -> result.input"])


def test_health_check_of_a_web_address_that_answers(env):
    with _web() as base:
        blocks, edges = _health_flow(url="{base}/200")
        result, run_id, status = _run(env, blocks, edges, params={"base": {"type": "text", "value": base}})

    assert result.returncode == 0, result.stderr
    assert status == {"check": "success", "on_healthy": "success", "on_unhealthy": "skipped", "result": "success"}
    expected = {"healthy": True, "checks": ["url"], "problems": []}
    assert _saved(env, "healthy.json") == expected
    assert _saved(env, "result.json") == expected
    assert ("INFO", "healthy (url)") in _logs(env, run_id, "check")


def test_health_check_of_a_web_address_with_another_status_is_unhealthy(env):
    with _web() as base:
        blocks, edges = _health_flow(url=f"{base}/503")
        result, run_id, status = _run(env, blocks, edges)

    assert result.returncode == 0, result.stderr  # unhealthy is an answer, not a failure
    assert status == {"check": "success", "on_healthy": "skipped", "on_unhealthy": "success", "result": "success"}
    expected = {"healthy": False, "checks": ["url"], "problems": [f"{base}/503 returned 503, expected 200"]}
    assert _saved(env, "unhealthy.json") == expected
    assert _saved(env, "result.json") == expected
    assert any(level == "WARNING" and "503" in m for level, m in _logs(env, run_id, "check"))


def test_health_check_expect_status(env):
    with _web() as base:
        result, _, status = _run(env, {
            "not_found": _health(url=f"{base}/404", expect_status=404), "nf": _save("not_found.json"),
            "wants_204": _health(url=f"{base}/200", expect_status=204), "w": _save("wants_204.json"),
        }, ["not_found.result -> nf.input", "wants_204.result -> w.input"])

    assert result.returncode == 0, result.stderr
    assert _saved(env, "not_found.json")["healthy"] is True
    assert _saved(env, "wants_204.json") == {"healthy": False, "checks": ["url"],
                                             "problems": [f"{base}/200 returned 200, expected 204"]}


def test_health_check_of_a_web_address_nobody_answers_is_unhealthy_within_the_timeout(env):
    closed = f"http://127.0.0.1:{free_port()}/"
    with _silent_port() as port:
        silent = f"http://127.0.0.1:{port}/"
        result, run_id, status = _run(env, {
            "closed": _health(url=closed), "c": _save("closed.json"),
            "silent": _health(url=silent, timeout=1), "s": _save("silent.json"),
        }, ["closed.unhealthy -> c.input", "silent.unhealthy -> s.input"])

    assert result.returncode == 0, result.stderr
    assert status == {"closed": "success", "c": "success", "silent": "success", "s": "success"}
    [problem] = _saved(env, "closed.json")["problems"]
    assert problem.startswith(closed)
    [problem] = _saved(env, "silent.json")["problems"]
    assert problem.startswith(silent) and "timed out" in problem
    assert _took(env, run_id, "silent") < 5  # its own 1 s timeout, not the default 10 s


def test_health_check_of_a_web_address_that_answers_something_else_than_http_is_unhealthy(env, server):
    # e.g. the port of another program (here the SSH server, which answers with its banner)
    url = f"http://127.0.0.1:{server.port}/"
    blocks, edges = _health_flow(url=url, timeout=3)
    result, run_id, status = _run(env, blocks, edges)

    assert status["check"] == "success", _error(env, run_id, "check")
    assert status["on_unhealthy"] == "success"
    assert _saved(env, "result.json")["healthy"] is False


def test_health_check_of_a_pid_file(env, server):
    pid_file = server.home_path / "app" / "app.pid"
    dead = subprocess.Popen(["true"])
    dead.wait()
    try:
        result, _, status = _run(env, {"start": _start("sleep 600.911")})
        assert status == {"start": "success"}, result.stderr
        (server.home_path / "app" / "dead.pid").write_text(f"{dead.pid}\n")

        result, _, status = _run(env, {
            "running": _health(connection="appserver", pid_file="~/app/app.pid"), "r": _save("running.json"),
            "missing": _health(connection="appserver", pid_file="~/app/missing.pid"), "m": _save("missing.json"),
            "dead": _health(connection="appserver", pid_file="~/app/dead.pid"), "d": _save("dead.json"),
        }, ["running.result -> r.input", "missing.result -> m.input", "dead.result -> d.input"])

        assert result.returncode == 0, result.stderr
        assert _saved(env, "running.json") == {"healthy": True, "checks": ["pid"], "problems": []}
        assert _saved(env, "missing.json") == {"healthy": False, "checks": ["pid"],
                                               "problems": ["no running process for ~/app/missing.pid"]}
        assert _saved(env, "dead.json") == {"healthy": False, "checks": ["pid"],
                                            "problems": ["no running process for ~/app/dead.pid"]}
    finally:
        _kill_from_pid_file(pid_file)
        _pkill("sleep 600.911")


def test_health_check_of_a_port(env, server):
    closed = free_port()
    with _silent_port() as open_port:
        result, _, status = _run(env, {
            "open": _health(connection="appserver", port=open_port), "o": _save("open.json"),
            "closed": _health(connection="appserver", port=closed), "c": _save("closed.json"),
        }, ["open.result -> o.input", "closed.result -> c.input"])

    assert result.returncode == 0, result.stderr
    assert _saved(env, "open.json") == {"healthy": True, "checks": ["port"], "problems": []}
    report = _saved(env, "closed.json")
    assert report["healthy"] is False and report["checks"] == ["port"]
    [problem] = report["problems"]
    assert problem.startswith(f"port {closed} on 127.0.0.1: ")


def test_health_check_with_every_check_reports_each_problem(env, server):
    closed = free_port()
    with _web() as base:
        blocks, edges = _health_flow(url=f"{base}/200", connection="appserver", pid_file="~/app/app.pid", port=closed)
        result, _, status = _run(env, blocks, edges)

    assert result.returncode == 0, result.stderr
    assert status["on_unhealthy"] == "success" and status["on_healthy"] == "skipped"
    report = _saved(env, "result.json")
    assert report["healthy"] is False
    assert report["checks"] == ["url", "pid", "port"]
    assert len(report["problems"]) == 2
    assert report["problems"][0] == "no running process for ~/app/app.pid"
    assert report["problems"][1].startswith(f"port {closed} on 127.0.0.1")


def test_the_check_app_health_example_says_whether_the_app_is_healthy(env, server):
    flow = yaml.safe_load((REPO / "examples" / "check-app-health.yaml").read_text())
    pid_file = server.home_path / "app" / "app.pid"
    try:
        with _web() as base:
            flow["blocks"]["check"]["config"]["url"] = f"{base}/200"  # instead of server01.example.com
            path = env.root / "check-app-health.yaml"
            path.write_text(yaml.safe_dump(flow, sort_keys=False))

            result = env.taskloom("run", path)
            assert result.returncode == 0, result.stderr
            run_id, _ = env.last_run()
            assert ("INFO", "Healthy: False. Checked: url, pid. Problems: ['no running process for ~/app/app.pid']") in \
                _logs(env, run_id, "show")

            result, _, status = _run(env, {"start": _start("sleep 600.443")})
            assert status == {"start": "success"}, result.stderr
            result = env.taskloom("run", path)
            assert result.returncode == 0, result.stderr
            run_id, _ = env.last_run()
            assert ("INFO", "Healthy: True. Checked: url, pid. Problems: none") in _logs(env, run_id, "show")
    finally:
        _kill_from_pid_file(pid_file)
        _pkill("sleep 600.443")


def test_health_check_configuration_mistakes_fail_clearly(env, server):
    result, run_id, status = _run(env, {
        "nothing": _health(),
        "pid_alone": _health(pid_file="~/app/app.pid"),
        "port_alone": _health(port=8080),
    })

    assert result.returncode == 1
    assert status == {"nothing": "failed", "pid_alone": "failed", "port_alone": "failed"}
    assert _error(env, run_id, "nothing") == "ValueError: set at least one check: URL, PID file or Port"
    assert _error(env, run_id, "pid_alone") == "ValueError: the PID file and Port checks need a Connection"
    assert _error(env, run_id, "port_alone") == "ValueError: the PID file and Port checks need a Connection"


# --- Remote Extract -------------------------------------------------------------------------------------

FILES = {"app/run.sh": "#!/bin/sh\necho café ✓\n", "app/data/read me.txt": "notes\n"}


@pytest.mark.parametrize("name", ["app.tar.gz", "app.tgz", "app.tar", "app.zip", "APP.ZIP"])
def test_remote_extract_unpacks_each_kind_of_archive(env, server, name):
    _archive(server.home_path / "upload" / name, FILES)

    result, _, status = _run(env, {
        "extract": {"type": "servers.remote_extract", "config": {
            "connection": "appserver", "archive": f"~/upload/{name}", "destination": "~/deploy/v1"}},
        "folder": _save("folder.json"),
    }, ["extract.folder -> folder.input"])

    assert result.returncode == 0, result.stderr
    assert status == {"extract": "success", "folder": "success"}
    assert _saved(env, "folder.json") == "~/deploy/v1"
    for path, text in FILES.items():
        assert (server.home_path / "deploy" / "v1" / path).read_text() == text


@pytest.mark.parametrize("name", ["app.tar.gz", "app.zip"])
def test_remote_extract_replaces_the_files_of_an_older_version(env, server, name):
    _archive(server.home_path / name, {"app/run.sh": "version 2\n"})
    (server.home_path / "app").mkdir()
    (server.home_path / "app" / "run.sh").write_text("version 1\n")

    result, run_id, status = _run(env, {"extract": {"type": "servers.remote_extract", "config": {
        "connection": "appserver", "archive": f"~/{name}", "destination": "~"}}})

    assert status == {"extract": "success"}, result.stderr
    assert (server.home_path / "app" / "run.sh").read_text() == "version 2\n"
    assert _took(env, run_id, "extract") < 10  # never waits for an answer to "replace?"


def test_remote_extract_into_a_folder_named_with_a_parameter(env, server):
    _archive(server.home_path / "upload" / "app-2.4.zip", FILES)

    result, _, status = _run(env, {
        "extract": {"type": "servers.remote_extract", "config": {
            "connection": "appserver", "archive": "~/upload/app-{version}.zip", "destination": "~/releases/{version}"}},
        "folder": _save("folder.json"),
    }, ["extract.folder -> folder.input"], params={"version": {"type": "text", "value": "2.4"}})

    assert status == {"extract": "success", "folder": "success"}, result.stderr
    assert _saved(env, "folder.json") == "~/releases/2.4"
    for path, text in FILES.items():
        assert (server.home_path / "releases" / "2.4" / path).read_text() == text


def test_remote_extract_with_spaces_in_the_paths(env, server):
    _archive(server.home_path / "my uploads" / "app v2.tar.gz", FILES)

    result, _, status = _run(env, {"extract": {"type": "servers.remote_extract", "config": {
        "connection": "appserver", "archive": "~/my uploads/app v2.tar.gz", "destination": "~/my apps/app v2"}}})

    assert status == {"extract": "success"}, result.stderr
    for path, text in FILES.items():
        assert (server.home_path / "my apps" / "app v2" / path).read_text() == text


def test_remote_extract_of_a_missing_or_broken_archive_says_why(env, server):
    (server.home_path / "broken.zip").write_text("this is not a zip file")
    (server.home_path / "broken.tar.gz").write_text("this is not a tar file")
    blocks = {name: {"type": "servers.remote_extract", "config": {
        "connection": "appserver", "archive": f"~/{name}", "destination": "~/out"}}
        for name in ("missing.tar.gz", "missing.zip", "broken.zip", "broken.tar.gz")}
    blocks = {name.replace(".", "_"): spec for name, spec in blocks.items()}

    result, run_id, status = _run(env, blocks)

    assert result.returncode == 1
    assert set(status.values()) == {"failed"}
    for block, spec in blocks.items():
        error = _error(env, run_id, block)
        prefix = f"RuntimeError: could not extract {spec['config']['archive']}: "
        assert error.startswith(prefix) and error[len(prefix):].strip(), error  # with the reason


# --- a whole deployment, as in examples/deploy-runner.yaml ------------------------------------------------

def test_deploy_flow_stops_the_old_version_unpacks_starts_and_checks_the_new_one(env, server):
    port = free_port()
    pid_file = server.home_path / "app" / "app.pid"
    blocks = {
        "stop_old": _stop(),
        "extract": {"type": "servers.remote_extract", "config": {
            "connection": "appserver", "archive": "~/upload/site.tar.gz", "destination": "~/app"}},
        "start": _start(f"{PY} -m http.server {port} --bind 127.0.0.1", workdir="~/app/site", check_after=2),
        "running": _health(url=f"http://127.0.0.1:{port}/", connection="appserver", pid_file="~/app/app.pid"),
        "stopped": _save("stopped.json"),
        "report": _save("report.json"),
    }
    edges = ["stop_old.stopped -> extract.after", "extract.folder -> start.after", "start.pid -> running.after",
             "stop_old.stopped -> stopped.input", "running.result -> report.input"]
    try:
        for version, was_running in (("v1", False), ("v2", True)):
            _archive(server.home_path / "upload" / "site.tar.gz", {"site/index.html": f"site {version}"})
            result, _, status = _run(env, blocks, edges)

            assert result.returncode == 0, result.stderr
            assert set(status.values()) == {"success"}, status
            assert _saved(env, "stopped.json") is was_running
            assert _saved(env, "report.json") == {"healthy": True, "checks": ["url", "pid"], "problems": []}
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as page:
                assert page.read().decode() == f"site {version}"
    finally:
        _kill_from_pid_file(pid_file)
