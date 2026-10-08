"""Server and file blocks against a real SSH server on localhost."""

import os
import signal
import sys
import time
import urllib.request

import paramiko
import pytest

from sshserver import PASSWORD, SSHTestServer, free_port
from taskloom import ssh
from taskloom.config import Home
from taskloom.scheduler import Scheduler

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the test server runs commands in /bin/sh")


def _kill_from_pid_file(path):
    try:
        os.kill(int(path.read_text().strip()), signal.SIGKILL)
    except (OSError, ValueError):
        pass


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
            command: "{sys.executable} -m taskloom.cli"
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
            config: {source: "out/*.csv", destination: "appserver:~/incoming/"}
          download:
            type: files.copy
            config: {source: "appserver:~/incoming", destination: "back/"}
          archive:
            type: files.move
            config: {source: "appserver:~/incoming/a.csv", destination: "appserver:~/archive/a_old.csv"}
          clean:
            type: files.delete
            config: {path: "appserver:~/incoming/*.csv"}
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
