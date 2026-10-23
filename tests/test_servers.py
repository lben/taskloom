"""Server and file blocks against a real SSH server on localhost."""

import os
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import paramiko
import pytest

from sshserver import PASSWORD, SSHTestServer, free_port
from taskloom import ssh
from taskloom.config import Home
from taskloom.scheduler import Scheduler

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the test server runs commands in /bin/sh")


def _scheduler_command() -> str:
    """How the keeper starts Taskloom on the test SSH server.

    That server runs the command with HOME set to its own folder, so Python would not see this
    checkout or the packages installed for the user running the test.
    """
    root = Path(__file__).resolve().parents[1]
    return f"PYTHONPATH={root} PYTHONUSERBASE={Path.home() / '.local'} {sys.executable} -m taskloom.cli"


def _kill_from_pid_file(path):
    """Kill a process started by Start Remote Process: the app and the small shell that runs it."""
    import subprocess

    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return
    members = subprocess.run(["sh", "-c", ssh.processes(str(pid))], capture_output=True, text=True).stdout.split()
    if members:
        subprocess.run(["kill", "-KILL", *members], capture_output=True)


def test_watchdog_flow_restarts_a_killed_app(env, server):
    port = free_port()
    flow = env.write("watchdog.yaml", f"""
        blocks:
          check:
            type: servers.health_check
            config: {{url: "http://127.0.0.1:{port}/", connection: appserver, pid_file: "~/app/app.pid", timeout: 2}}
          restart:
            type: servers.start_process
            config:
              connection: appserver
              command: "{sys.executable} -m http.server {port} --bind 127.0.0.1"
              workdir: "~"
              log_file: "~/app/app.log"
              pid_file: "~/app/app.pid"
              check_after: 1
          recheck:
            type: servers.health_check
            config: {{url: "http://127.0.0.1:{port}/", connection: appserver, pid_file: "~/app/app.pid", timeout: 5}}
            retry: {{policy: fixed, delay: 1s, max_attempts: 5}}
        edges:
          - check.unhealthy -> restart.after
          - restart.pid -> recheck.after
    """)
    pid_file = server.home_path / "app" / "app.pid"
    try:
        first = env.taskloom("run", flow)
        assert first.returncode == 0, first.stderr
        run_id, _ = env.last_run()
        assert env.block_status(run_id) == {"check": "success", "restart": "success", "recheck": "success"}
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).status == 200

        _kill_from_pid_file(pid_file)  # the app dies
        time.sleep(0.5)
        second = env.taskloom("run", flow)

        assert second.returncode == 0, second.stderr
        run_id, _ = env.last_run()
        assert env.block_status(run_id) == {"check": "success", "restart": "success", "recheck": "success"}
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).status == 200

        third = env.taskloom("run", flow)  # healthy now: nothing to restart
        run_id, _ = env.last_run()
        assert third.returncode == 0 and env.block_status(run_id)["restart"] == "skipped"
    finally:
        _kill_from_pid_file(pid_file)


def test_keeper_starts_and_restarts_the_server_scheduler(env, server):
    remote_home = server.home_path / ".taskloom"
    env.write("home/settings.yaml", f"""
        keeper:
          - connection: appserver
            command: "{_scheduler_command()}"
            home: "~/.taskloom"
    """)
    scheduler = Scheduler(Home(env.home))
    heartbeat, pid_file = remote_home / "scheduler.heartbeat", remote_home / "scheduler.pid"
    try:
        scheduler.check_servers()
        assert scheduler.messages.get_nowait()[0] == "Server scheduler restarted"
        deadline = time.monotonic() + 30
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert heartbeat.exists()

        scheduler.check_servers()  # running: nothing to do
        assert scheduler.messages.empty()

        first_pid = pid_file.read_text().strip()
        _kill_from_pid_file(pid_file)
        time.sleep(0.5)
        scheduler.check_servers()

        assert scheduler.messages.get_nowait()[0] == "Server scheduler restarted"
        assert pid_file.read_text().strip() != first_pid
    finally:
        _kill_from_pid_file(pid_file)


def test_the_keeper_stops_a_hung_scheduler_so_another_can_start(env, server):
    """A hung scheduler still holds the folder lock. Starting another one failed at once and the hung
    process was left running, so the server stayed without a scheduler that checks."""
    remote = server.home_path / ".taskloom"
    remote.mkdir()
    script = textwrap.dedent("""
        import os, time
        from taskloom.config import Home
        from taskloom.scheduler import acquire_lock
        home = Home.default()
        acquire_lock(home)
        (home.root / "scheduler.pid").write_text(str(os.getpid()), encoding="utf-8")
        (home.root / "scheduler.heartbeat").write_text(str(int(time.time()) - 600), encoding="utf-8")
        (home.root / "stand-in.ready").write_text("ready", encoding="utf-8")
        time.sleep(120)
    """)
    hung = subprocess.Popen(
        [sys.executable, "-c", script],
        env={**os.environ, "TASKLOOM_HOME": str(remote), "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    env.write("home/settings.yaml", f"""
        keeper:
          - connection: appserver
            command: "{_scheduler_command()}"
            home: "~/.taskloom"
    """)
    scheduler = Scheduler(Home(env.home))
    try:
        deadline = time.monotonic() + 10
        while not (remote / "stand-in.ready").exists() and hung.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (remote / "stand-in.ready").exists()
        checked = time.time()
        scheduler.check_servers()
        title, message = scheduler.messages.get_nowait()
        assert title == "Server scheduler restarted", (title, message)
        assert "was started" in message
        deadline = time.monotonic() + 5
        while hung.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert hung.poll() is not None  # the hung process was stopped, not left holding the lock
        beat = int((remote / "scheduler.heartbeat").read_text(encoding="utf-8"))
        assert beat >= checked - 1
    finally:
        if hung.poll() is None:
            hung.kill()
        hung.wait(timeout=15)
        _kill_from_pid_file(remote / "scheduler.pid")


def test_the_keeper_leaves_an_unrelated_process_in_the_pid_file(env, server):
    """A stale heartbeat must not let the keeper kill whatever pid happens to be in the file."""
    remote = server.home_path / ".taskloom"
    remote.mkdir()
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    (remote / "scheduler.pid").write_text(str(sleeper.pid), encoding="utf-8")
    (remote / "scheduler.heartbeat").write_text(str(int(time.time()) - 600), encoding="utf-8")
    env.write("home/settings.yaml", f"""
        keeper:
          - connection: appserver
            command: "{_scheduler_command()}"
            home: "~/.taskloom"
    """)
    scheduler = Scheduler(Home(env.home))
    try:
        scheduler.check_servers()
        title, message = scheduler.messages.get_nowait()
        assert title == "Server scheduler restarted", (title, message)
        assert sleeper.poll() is None
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
        sleeper.wait(timeout=15)
        _kill_from_pid_file(remote / "scheduler.pid")


def test_ssh_command_output_and_failure(env, server):
    flow = env.write("cmd.yaml", """
        blocks:
          ok:
            type: servers.ssh_command
            config: {connection: appserver, command: "echo hello from $HOME"}
          save:
            type: logic.python
            config: {outputs: [], code: "open('out.txt', 'w').write(input)"}
          bad:
            type: servers.ssh_command
            config: {connection: appserver, command: "echo oops >&2; exit 4"}
        edges:
          - ok.stdout -> save.input
    """)
    result = env.taskloom("run", flow)

    assert result.returncode == 1
    assert (env.root / "out.txt").read_text() == f"hello from {server.home_path}\n"
    run_id, _ = env.last_run()
    assert env.query("SELECT error FROM blocks WHERE run_id=? AND block='bad'", run_id) == [
        ("RuntimeError: command exited with code 4: oops",)]


def test_copy_move_and_delete_between_this_machine_and_a_server(env, server):
    (env.root / "out").mkdir()
    for name in ("a.csv", "b.csv", "notes.txt"):
        (env.root / "out" / name).write_text(name)
    flow = env.write("files.yaml", """
        blocks:
          upload:
            type: files.copy
            config: {source: "out/*.csv", destination_server: appserver, destination: "~/incoming/"}
          download:
            type: files.copy
            config: {source_server: appserver, source: "~/incoming", destination: "back/"}
          archive:
            type: files.move
            config: {source_server: appserver, source: "~/incoming/a.csv", destination_server: appserver,
                     destination: "~/archive/a_old.csv"}
          clean:
            type: files.delete
            config: {server: appserver, path: "~/incoming/*.csv"}
        edges:
          - upload.files -> download.after
          - download.files -> archive.after
          - archive.files -> clean.after
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (env.root / "back" / "incoming").iterdir()) == ["a.csv", "b.csv"]
    assert (server.home_path / "archive" / "a_old.csv").read_text() == "a.csv"
    assert list((server.home_path / "incoming").iterdir()) == []


def test_a_changed_server_key_is_refused(tmp_path):
    (tmp_path / "srv").mkdir()
    home = Home(tmp_path / "home")
    conn = {"host": "127.0.0.1", "port": free_port(), "username": "tester", "password": PASSWORD}
    first = SSHTestServer(tmp_path / "srv", port=conn["port"]).start()
    ssh.connect(conn, home).close()  # trusted on first use
    first.stop()

    impostor = SSHTestServer(tmp_path / "srv", port=conn["port"]).start()  # same address, new key
    try:
        with pytest.raises(paramiko.BadHostKeyException):
            ssh.connect(conn, home)
    finally:
        impostor.stop()


def test_connection_test_reports_success_and_a_wrong_password(env, server, smtp):
    ok = env.taskloom("connection", "test", "appserver")
    assert ok.returncode == 0 and "Connected to 127.0.0.1 as tester" in ok.stdout
    assert env.taskloom("connection", "test", "mailer").returncode == 0

    env.write("home/secrets.yaml", "appserver_password: wrong\n").chmod(0o600)
    bad = env.taskloom("connection", "test", "appserver")
    assert bad.returncode == 1 and "Authentication" in bad.stderr


def test_servers_that_allow_one_command_per_connection(env, server, monkeypatch):
    """Some servers refuse a second session on the same SSH connection (ChannelException 'Connect failed')."""
    import yaml

    from taskloom.engine import run_flow
    from taskloom.flow import load_flow
    from taskloom.registry import discover

    original = paramiko.SSHClient.exec_command

    def one_session_per_connection(self, *args, **kwargs):
        self._sessions = getattr(self, "_sessions", 0) + 1
        if self._sessions > 1:
            raise paramiko.ChannelException(2, "Connect failed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(paramiko.SSHClient, "exec_command", one_session_per_connection)
    pid_file = server.home_path / "app" / "app.pid"
    flow = env.root / "flow.yaml"
    flow.write_text(yaml.safe_dump({
        "blocks": {
            "start": {"type": "servers.start_process", "config": {
                "connection": "appserver", "command": "sleep 60", "pid_file": "~/app/app.pid",
                "log_file": "~/app/app.log", "check_after": 0.5}},
            "check": {"type": "servers.health_check", "config": {"connection": "appserver", "pid_file": "~/app/app.pid"}},
            "stop": {"type": "servers.stop_process", "config": {"connection": "appserver", "pid_file": "~/app/app.pid"}},
        },
        "edges": ["start.done -> check.after", "check.healthy -> stop.after"],
    }))
    try:
        home = Home(env.home)
        result = run_flow(load_flow(flow), discover(None), home)
        from taskloom.history import History
        history = History(home.history_db)
        blocks = history.blocks(result.run_id)
        history.close()
        assert result.status == "success", blocks
        assert {b: i["status"] for b, i in blocks.items()} == {"start": "success", "check": "success", "stop": "success"}

        # The keeper also runs two commands per server: it must cope too.
        env.write("home/settings.yaml", "keeper:\n  - {connection: appserver, command: '" + _scheduler_command() + "', home: '~/.taskloom'}\n")
        scheduler = Scheduler(home)
        scheduler.check_servers()
        assert scheduler.messages.get_nowait()[0] == "Server scheduler restarted"
    finally:
        _kill_from_pid_file(pid_file)
        _kill_from_pid_file(server.home_path / ".taskloom" / "scheduler.pid")


def _start_flow(env, command, **config):
    import yaml

    flow = env.root / "start.yaml"
    flow.write_text(yaml.safe_dump({"blocks": {"start": {"type": "servers.start_process", "config": {
        "connection": "appserver", "command": command, "pid_file": "~/app/app.pid", "log_file": "~/app/app.log",
        "check_after": 0.5, **config}}}}))
    result = env.taskloom("run", flow)
    run_id, _ = env.last_run()
    error = env.query("SELECT error FROM blocks WHERE run_id=?", run_id)[0][0]
    return result.returncode, error


@pytest.mark.parametrize("command", [
    "cd ~ && sleep 60 >> mine.log 2>&1",   # a chain, with its own output file
    "sleep 60 >> mine.log 2>&1 &",         # ending with &: the block keeps track anyway
])
def test_start_process_runs_commands_as_written(env, server, command):
    pid_file = server.home_path / "app" / "app.pid"
    try:
        code, error = _start_flow(env, command)
        assert code == 0, error
    finally:
        _kill_from_pid_file(pid_file)


def test_start_process_explains_a_command_that_ends(env, server):
    code, error = _start_flow(env, "echo started a service >> mine.log 2>&1")
    assert code == 1 and "finished right away (exit code 0)" in error and "its own file" in error

    code, error = _start_flow(env, "echo started a service", must_keep_running=False)
    assert code == 0

    code, error = _start_flow(env, "echo cannot find the app; exit 3")
    assert code == 1 and "exited with code 3" in error and "cannot find the app" in error


def test_stop_process_stops_the_app_not_just_its_shell(env, server):
    import subprocess

    import yaml

    pid_file = server.home_path / "app" / "app.pid"
    try:
        assert _start_flow(env, "sleep 61")[0] == 0
        assert subprocess.run(["pgrep", "-f", "sleep 61"], capture_output=True).returncode == 0
        flow = env.root / "stop.yaml"
        flow.write_text(yaml.safe_dump({"blocks": {"stop": {"type": "servers.stop_process", "config": {
            "connection": "appserver", "pid_file": "~/app/app.pid"}}}}))
        assert env.taskloom("run", flow).returncode == 0
        assert subprocess.run(["pgrep", "-f", "sleep 61"], capture_output=True).returncode == 1
    finally:
        _kill_from_pid_file(pid_file)


def test_stop_process_forces_an_app_that_ignores_the_stop_signal(env, server):
    import subprocess
    import sys as _sys

    import yaml

    pid_file = server.home_path / "app" / "app.pid"
    stubborn = f"{_sys.executable} -c 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(62)'"
    try:
        assert _start_flow(env, stubborn)[0] == 0
        assert subprocess.run(["pgrep", "-f", "time.sleep\\(62\\)"], capture_output=True).returncode == 0
        flow = env.root / "stop.yaml"
        flow.write_text(yaml.safe_dump({"blocks": {"stop": {"type": "servers.stop_process", "config": {
            "connection": "appserver", "pid_file": "~/app/app.pid", "grace": 1}}}}))
        result = env.taskloom("run", flow)

        assert result.returncode == 0, result.stderr
        assert "did not stop in 1 s; forcing them" in result.stderr
        assert subprocess.run(["pgrep", "-f", "time.sleep\\(62\\)"], capture_output=True).returncode == 1
    finally:
        _kill_from_pid_file(pid_file)
        subprocess.run(["pkill", "-KILL", "-f", "time.sleep\\(62\\)"], capture_output=True)
