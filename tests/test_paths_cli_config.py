"""The command line and the per-user settings through their real paths: `taskloom run|validate|check|scheduler|
editor|env|connection|secret` (their help, exit codes, output and the errors for a wrong command line), the
Taskloom folder ($TASKLOOM_HOME or ~/.taskloom) and what it holds, settings.yaml, connections.yaml, secrets
(`taskloom secret set`, `secret:` references, the private secrets.yaml), your own blocks in the blocks folder
and installed block plugins."""

import datetime as dt
import json
import os
import sqlite3
import stat
import subprocess
import sys
import sysconfig
import textwrap
import time
from pathlib import Path

import pytest
import yaml

from conftest import REPO, events

T0 = dt.datetime(2026, 1, 5, 6, 30)  # a Monday


# --- helpers ---------------------------------------------------------------------------------------------------

def write(env, text: str, name: str = "flow.yaml") -> Path:
    """A file under the test folder, written as Taskloom reads it: UTF-8."""
    path = env.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def runs_recorded(env) -> int:
    """No history.db, or one without a runs table yet (the scheduler service creates only its own), is no run."""
    if not (env.home / "history.db").exists() or not env.query(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"):
        return 0
    return env.query("SELECT count(*) FROM runs")[0][0]


def told_clearly(told: dict, name: str) -> list[str]:
    """What is unclear in what each command ({command: (result, expected exit code, None: not 0)}) told about the
    file `name`: it should end with that exit code and an error naming the file, never a traceback."""
    problems = []
    for command, (result, code) in told.items():
        wrong_code = result.returncode == 0 if code is None else result.returncode != code
        if wrong_code or "error: " not in result.stderr or name not in result.stderr:
            problems.append(f"{command}: exit {result.returncode}, {result.stderr.strip()[-300:]!r}")
        elif "Traceback" in result.stderr:
            problems.append(f"{command}: a traceback")
    return problems


def taskloom_in(env, cwd: Path, *args, **kw) -> subprocess.CompletedProcess:
    """`taskloom ARGS` run from the folder `cwd` (env.taskloom always runs from the test folder)."""
    return subprocess.run([sys.executable, "-m", "taskloom.cli", *map(str, args)], env=env.env, cwd=cwd,
                          capture_output=True, text=True, timeout=120, **kw)


def secret_set(env, name: str, value: str) -> subprocess.CompletedProcess:
    """`taskloom secret set NAME`, typing VALUE. Without a terminal of its own (a new session), getpass reads
    the value from stdin."""
    return env.taskloom("secret", "set", name, input=value + "\n", start_new_session=True)


def scheduler(env, flows: dict[str, str]):
    """The scheduler service's loop (as `taskloom scheduler` runs it) over these scheduled flows ({name: text})."""
    from taskloom.config import Home
    from taskloom.scheduler import Scheduler

    home = Home(env.home)
    for name, text in flows.items():
        home.set_scheduled(write(env, text, name), True)
    notes = []
    return Scheduler(home, lambda title, message: notes.append((title, message))), notes


TOUCH = """
    blocks:
      start: {type: trigger.manual}
      a:
        type: logic.python
        config: {inputs: [], outputs: [], code: "open('ran.txt', 'w').write('x')"}
    edges:
      - start.start -> a.after
"""

GREET_BLOCK = """
    from taskloom import Block, fields, ports


    class Greet(Block):
        type_id = "my.greet"
        title = "Greet"
        description = "Says hello to whoever you name."
        inputs = {"name": ports.Any()}
        outputs = {"text": ports.Any()}
        config = {"greeting": fields.Text(default="Hello")}

        def run(self, ctx, name):
            ctx.log(f"greeting {name}")
            return {"text": f"{self.config.greeting}, {name}!"}
"""

GREET_FLOW = """
    blocks:
      person:
        type: logic.python
        config: {inputs: [], code: "output = 'Ana'"}
      greet:
        type: my.greet
      save:
        type: logic.python
        config: {inputs: [text], outputs: [], code: "open('greeting.txt', 'w', encoding='utf-8').write(text)"}
    edges:
      - person.output -> greet.name
      - greet.text -> save.text
"""

SSH_FLOW = """
    blocks:
      remote:
        type: servers.ssh_command
        config: {connection: appserver, command: "echo hi"}
"""


# --- the command line ------------------------------------------------------------------------------------------

def test_help_names_every_command_and_option_and_version_says_which_taskloom_this_is(env):
    from taskloom import __version__

    main = env.taskloom("--help")
    assert main.returncode == 0, main.stderr
    for command in ("run", "validate", "check", "scheduler", "editor", "connection", "env", "secret"):
        assert command in main.stdout
    helps = {}
    for command in ("run", "validate", "check", "scheduler", "editor", "connection", "connection test", "env",
                    "env create", "env info", "secret", "secret set"):
        shown = env.taskloom(*command.split(), "--help")
        assert shown.returncode == 0 and shown.stdout.startswith(f"usage: taskloom {command}"), shown.stderr
        helps[command] = shown.stdout
    for command, options in (("run", ["--param", "--json-events", "--as-of", "--only", "--from", "--inputs-from",
                                      "--input", "--trigger", "--notify", "--interactive"]),
                             ("check", ["--fix"]), ("scheduler", ["--headless", "--at-login", "--unless-paused"]),
                             ("env create", ["--python"])):
        assert [o for o in options if o not in helps[command]] == [], helps[command]

    version = env.taskloom("--version")
    assert version.returncode == 0 and version.stdout.strip() == f"taskloom {__version__}"


@pytest.mark.parametrize("args, wrong", [
    (["frobnicate", "flow.yaml"], "frobnicate"),
    (["run", "flow.yaml", "--nope"], "--nope"),
], ids=["unknown command", "unknown option"])
def test_a_command_line_taskloom_cannot_read_is_refused_with_its_usage(env, args, wrong):
    write(env, TOUCH)

    result = env.taskloom(*args)

    assert result.returncode == 2, result.stderr
    assert "usage: taskloom" in result.stderr and wrong in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert not (env.root / "ran.txt").exists() and runs_recorded(env) == 0


def test_a_flow_path_that_is_a_folder_is_an_error_not_a_crash(env):
    (env.root / "flows").mkdir()

    for command in ("validate", "run"):
        result = env.taskloom(command, env.root / "flows")
        assert result.returncode == 2 and result.stderr.startswith("error: cannot read"), result.stderr
        assert "flows" in result.stderr and "Traceback" not in result.stderr
    assert runs_recorded(env) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_a_flow_file_you_may_not_read_is_an_error_not_a_crash(env):
    flow = write(env, TOUCH)
    flow.chmod(0)
    try:
        flow.read_bytes()
    except PermissionError:
        pass
    else:
        flow.chmod(0o644)
        pytest.skip("running as a user who can read any file")
    try:
        for command in ("validate", "run"):
            result = env.taskloom(command, flow)
            assert result.returncode == 2 and result.stderr.startswith("error: cannot read"), result.stderr
            assert "Traceback" not in result.stderr
    finally:
        flow.chmod(0o644)
    assert not (env.root / "ran.txt").exists() and runs_recorded(env) == 0


def test_run_reports_progress_on_stderr_or_as_json_lines_on_stdout(env):
    flow = write(env, """
        name: nightly
        blocks:
          ok:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
          boom:
            type: logic.python
            config: {inputs: [], outputs: [], code: "raise RuntimeError('disk full')"}
    """)

    plain = env.taskloom("run", flow)
    as_json = env.taskloom("run", flow, "--json-events")

    assert plain.returncode == 1 and as_json.returncode == 1
    assert plain.stdout == ""
    for text in ("run #1 of nightly started", "[ok] success", "[boom] failed · RuntimeError: disk full", "run #1 failed"):
        assert text in plain.stderr, plain.stderr
    lines = as_json.stdout.splitlines()
    assert lines and len(events(as_json.stdout)) == len(lines), as_json.stdout  # nothing but JSON on stdout
    found = events(as_json.stdout)
    assert (found[0]["event"], found[0]["run_id"], found[0]["flow"]) == ("run_started", 2, "nightly")
    assert {e["block"]: e["status"] for e in found if e["event"] == "block_finished"} == {"ok": "success",
                                                                                         "boom": "failed"}
    assert (found[-1]["event"], found[-1]["status"]) == ("run_finished", "failed")
    assert as_json.stderr == ""  # with --json-events, stderr only carries problems
    assert env.query("SELECT id, status FROM runs ORDER BY id") == [(1, "failed"), (2, "failed")]


@pytest.mark.parametrize("trigger", ["nightyl", "a"], ids=["a block that does not exist", "not a start block"])
def test_a_trigger_that_names_no_start_block_of_the_flow_is_refused(env, trigger):
    # --trigger: "the Schedule block that started this run"; --only/--from refuse a block that does not exist.
    flow = write(env, TOUCH)

    result = env.taskloom("run", flow, "--trigger", trigger)

    assert result.returncode == 2 and trigger in result.stderr, (result.returncode, result.stderr)
    assert runs_recorded(env) == 0


def test_check_without_names_checks_the_scheduled_flows(env):
    nothing = env.taskloom("check")
    assert nothing.returncode == 0 and "no flows to check" in nothing.stdout, nothing.stderr

    good = write(env, """
        blocks:
          nightly:
            type: trigger.schedule
            config: {cron: "0 7 * * *"}
          a:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - nightly.start -> a.after
    """, "good.yaml")
    gone = env.root / "gone.yaml"
    (env.home / "scheduled.yaml").write_text(yaml.safe_dump({"flows": [str(good), str(gone)]}), encoding="utf-8")

    result = env.taskloom("check")

    assert result.returncode == 2, result.stdout + result.stderr
    assert f"{good}: OK" in result.stdout
    assert f"{gone}:" in result.stdout and "needs you: cannot read it" in result.stdout
    assert "Traceback" not in result.stderr


def test_scheduler_at_login_on_and_off_from_the_command_line(env):
    env.env["APPDATA"] = str(env.root / "AppData")  # where Windows keeps the Startup folder (never the real one)

    on = env.taskloom("scheduler", "--at-login", "on")
    assert on.returncode == 0 and on.stdout.startswith("start at login enabled: "), on.stderr
    [script] = (env.root / "AppData").rglob("*.cmd")
    assert script.parent.name == "Startup" and str(script) in on.stdout
    assert f"TASKLOOM_HOME={env.home.resolve()}" in script.read_text(encoding="utf-8")

    off = env.taskloom("scheduler", "--at-login", "off")

    assert off.returncode == 0 and off.stdout.startswith("start at login disabled: "), off.stderr
    assert not script.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows always has a Startup folder")
def test_scheduler_at_login_where_there_is_no_startup_folder_says_so_without_a_crash(env):
    env.env.pop("APPDATA", None)

    result = env.taskloom("scheduler", "--at-login", "on")

    assert result.returncode != 0 and "supported on Windows only" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, result.stderr


STAND_IN_CLI = """
import json, os, sys

# Stands in for Taskloom in the program the Startup script starts: it writes down what it was started with.
with open(os.environ["STARTED_WITH"] + ".part", "w", encoding="utf-8") as f:
    json.dump({"TASKLOOM_HOME": os.environ.get("TASKLOOM_HOME"), "args": sys.argv[1:]}, f)
os.replace(os.environ["STARTED_WITH"] + ".part", os.environ["STARTED_WITH"])
"""


@pytest.mark.skipif(sys.platform != "win32", reason="the Startup folder, and cmd.exe that runs its scripts, are Windows'")
@pytest.mark.parametrize("accented", ["Taskloom folder", "install folder"])
def test_start_at_login_starts_the_scheduler_also_when_a_folder_name_has_an_accent(env, accented):
    # README: `taskloom scheduler --at-login on` adds the scheduler to your Startup folder; unzip Taskloom anywhere
    # you can write. E.g. for a user José: C:\Users\José\.taskloom, or Taskloom unzipped in his Downloads.
    user = env.root / "Users" / "José"
    home, python = env.home, Path(sys.executable)
    if accented == "Taskloom folder":
        home = user / ".taskloom"
    else:
        install = user / "Downloads" / "Taskloom"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(install)], check=True, capture_output=True,
                       timeout=120)
        python = install / "Scripts" / "python.exe"
    paths = sysconfig.get_paths()
    env.env.update(APPDATA=str(env.root / "AppData"), TASKLOOM_HOME=str(home),
                   PYTHONPATH=os.pathsep.join([str(REPO), paths["purelib"], paths["platlib"]]))
    on = subprocess.run([str(python), "-m", "taskloom.cli", "scheduler", "--at-login", "on"], env=env.env,
                        cwd=env.root, capture_output=True, text=True, timeout=120)
    assert on.returncode == 0, on.stderr
    [script] = (env.root / "AppData").rglob("*.cmd")
    # At login Windows runs the script with cmd.exe, in a console of its own. What it starts here is a stand-in that
    # writes down how it was started: nothing real starts.
    stand_in = env.root / "stand-in" / "taskloom"
    stand_in.mkdir(parents=True)
    (stand_in / "__init__.py").write_text("", encoding="utf-8")
    (stand_in / "cli.py").write_text(STAND_IN_CLI, encoding="utf-8")
    started_with = env.root / "started-with.json"
    at_login = {k: v for k, v in env.env.items() if k != "TASKLOOM_HOME"}
    at_login.update(PYTHONPATH=str(stand_in.parent), STARTED_WITH=str(started_with))
    hidden = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=subprocess.SW_HIDE)

    try:
        subprocess.run(["cmd.exe", "/c", str(script)], env=at_login, cwd=env.root, capture_output=True, timeout=30,
                       creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=hidden)
    except subprocess.TimeoutExpired:  # e.g. "Windows cannot find ...", waiting for someone to click OK
        pytest.fail(f"the Startup script could not start Taskloom: {script.read_bytes()!r}")
    deadline = time.monotonic() + 30
    while not started_with.exists() and time.monotonic() < deadline:  # started detached
        time.sleep(0.1)

    assert started_with.exists(), f"the Startup script started nothing: {script.read_bytes()!r}"
    assert json.loads(started_with.read_text(encoding="utf-8")) == {
        "TASKLOOM_HOME": str(home.resolve()), "args": ["scheduler", "--unless-paused"]}, script.read_bytes()


# --- the Taskloom folder ---------------------------------------------------------------------------------------

def test_without_TASKLOOM_HOME_your_files_are_in_dot_taskloom_in_your_home_folder(env):
    user_home = env.root / "user home"
    folder = user_home / ".taskloom"
    write(env, GREET_BLOCK, "user home/.taskloom/blocks/greet.py")
    del env.env["TASKLOOM_HOME"]
    env.env["HOME"] = env.env["USERPROFILE"] = str(user_home)  # the home folder on Linux/macOS and on Windows
    flow = write(env, GREET_FLOW)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert (env.root / "greeting.txt").read_text(encoding="utf-8") == "Hello, Ana!"  # its blocks folder's block
    with sqlite3.connect(folder / "history.db") as db:
        assert db.execute("SELECT id, status FROM runs").fetchall() == [(1, "success")]
    assert not (env.home / "history.db").exists()


def test_a_new_relative_TASKLOOM_HOME_is_made_where_you_run_from_and_holds_every_run_result_and_log_line(env):
    work = env.root / "work"
    work.mkdir()
    flow = write(env, """
        name: Informe
        blocks:
          start: {type: trigger.manual}
          say:
            type: logic.python
            config: {inputs: [], code: "print('hello from say'); output = 42"}
        edges:
          - start.start -> say.after
    """, "my flows/Informe é.yaml")
    env.env["TASKLOOM_HOME"] = str(Path("data") / "Taskloom é folder")

    result = taskloom_in(env, work, "run", flow)

    assert result.returncode == 0, result.stderr
    folder = work / "data" / "Taskloom é folder"
    with sqlite3.connect(folder / "history.db") as db:
        assert db.execute("SELECT id, flow, flow_path, status FROM runs").fetchall() == [
            (1, "Informe", str(flow.resolve()), "success")]
        assert dict(db.execute("SELECT block, status FROM blocks WHERE run_id=1").fetchall()) == {
            "start": "success", "say": "success"}
        assert ("say", "hello from say") in db.execute("SELECT block, message FROM logs WHERE run_id=1").fetchall()
    assert (folder / "runs" / "1").is_dir()
    assert not (env.home / "history.db").exists()


# --- settings.yaml ---------------------------------------------------------------------------------------------

def test_an_unknown_setting_is_refused_naming_it(env):
    env.write("home/settings.yaml", "keep_run: 5\n")  # keep_runs, mistyped
    flow = write(env, TOUCH)

    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert result.returncode == 2 and "keep_run" in result.stderr, result.stderr
        assert "settings.yaml" in result.stderr and "Traceback" not in result.stderr
    assert not (env.root / "ran.txt").exists() and runs_recorded(env) == 0


def test_without_history_days_a_run_is_kept_365_days(env):
    # README: every run's values are kept in Run history for `history_days` (365 by default).
    flow = write(env, """
        blocks:
          say:
            type: logic.python
            config: {inputs: [], code: "print('a line in the log'); output = 42"}
    """)
    for _ in range(2):
        assert env.taskloom("run", flow).returncode == 0
    now = dt.datetime.now()
    for run_id, days_ago in ((1, 366), (2, 364)):
        env.query("UPDATE runs SET started=? WHERE id=?",
                  (now - dt.timedelta(days=days_ago)).isoformat(timespec="milliseconds"), run_id)

    ran = env.taskloom("run", flow)  # forgets the runs older than history_days as it ends

    assert ran.returncode == 0, ran.stderr
    assert env.query("SELECT id FROM runs ORDER BY id") == [(2,), (3,)]
    for table in ("blocks", "logs"):
        assert env.query(f"SELECT DISTINCT run_id FROM {table} ORDER BY run_id") == [(2,), (3,)], table


BROKEN_FILES = {
    "not YAML": "keep_runs: [5\n".encode("utf-8"),
    # Windows PowerShell 5 (`echo ... > file`, Out-File) writes UTF-16.
    "UTF-16, as Windows PowerShell writes": "calendar: US\n".encode("utf-16"),
    "a list": b"- keep_runs: 5\n",
}


@pytest.mark.parametrize("content", list(BROKEN_FILES.values()), ids=list(BROKEN_FILES))
@pytest.mark.parametrize("name", ["settings.yaml", "connections.yaml"])
def test_a_settings_or_connections_file_taskloom_cannot_read_is_an_error_naming_it(env, name, content):
    env.write("home/connections.yaml", "appserver: {kind: ssh, host: 127.0.0.1, username: me}\n")
    (env.home / name).write_bytes(content)
    flow = write(env, SSH_FLOW)

    told = {"validate": (env.taskloom("validate", flow), 2), "run": (env.taskloom("run", flow), 2)}
    if name == "connections.yaml":
        told["connection test"] = (env.taskloom("connection", "test", "appserver"), 1)

    assert told_clearly(told, name) == []
    assert runs_recorded(env) == 0


def test_notify_on_written_as_one_event_still_emails_or_says_what_is_wrong(env, smtp):
    # README: set notify_email, smtp_connection and notify_on; notify_email may be one address or a list.
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\nnotify_on: success\n")
    flow = write(env, "name: weekly\n" + textwrap.dedent(TOUCH))

    result = env.taskloom("run", flow, "--notify")

    assert result.returncode in (0, 2), result.stderr
    assert len(smtp.messages) == 1 or "notify_on" in result.stderr, (smtp.messages, result.stderr)


WAIT_FLOW = """
    triggers:
      - schedule: {cron: "* * * * *"}
    blocks:
      pause:
        type: logic.wait
        config: {seconds: 3}
"""


@pytest.mark.parametrize("limit, overlap", [(None, True), (1, False)], ids=["no limit", "one at a time"])
def test_max_concurrent_runs_limits_how_many_scheduled_runs_go_at_once(env, monkeypatch, limit, overlap):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")  # the runs it starts
    if limit is not None:
        env.write("home/settings.yaml", f"max_concurrent_runs: {limit}\n")
    service, notes = scheduler(env, {"first.yaml": WAIT_FLOW, "second.yaml": WAIT_FLOW})

    service.tick(T0)
    service.tick(T0 + dt.timedelta(minutes=1))  # both are due
    service.wait_for_runs()

    runs = env.query("SELECT started, finished, status FROM runs ORDER BY started")
    assert [status for _, _, status in runs] == ["success", "success"], notes
    (_, first_finished, _), (second_started, _, _) = runs
    assert (second_started < first_finished) == overlap, runs


@pytest.mark.parametrize("settings, named", [
    ("max_concurrent_runs: lots\n", "max_concurrent_runs"),
    ("keep_run: 5\n", "keep_run"),
    ("keep_runs: [5\n", "settings.yaml"),
], ids=["max_concurrent_runs not a number", "an unknown setting", "not YAML"])
def test_a_mistake_in_settings_does_not_stop_the_scheduler_service(env, monkeypatch, settings, named):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    service, notes = scheduler(env, {"flow.yaml": WAIT_FLOW.replace("seconds: 3", "seconds: 0")})
    service.tick(T0)
    env.write("home/settings.yaml", settings)  # e.g. edited by hand while the service runs

    try:
        service.tick(T0 + dt.timedelta(minutes=1))  # due: the loop of `taskloom scheduler` ends if this raises
        service.wait_for_runs()
        service.tick(T0 + dt.timedelta(minutes=1, seconds=30))  # reports finished runs
    except Exception as e:
        pytest.fail(f"the scheduler service stops: {type(e).__name__}: {e}")
    assert runs_recorded(env) == 1 or any(named in message for _, message in notes), notes


KEEPER_TASKLOOM = """#!/bin/sh
echo "$TASKLOOM_HOME $*" > "$HOME/keeper-started.txt"
"""


@pytest.mark.skipif(sys.platform == "win32", reason="the test server runs commands in /bin/sh")
def test_the_keeper_starts_the_scheduler_on_a_server_where_it_is_not_running(env, server):
    # README: list the server in the keeper setting; the editor's Settings write {connection, command, home}.
    fake = server.home_path / "taskloom" / "taskloom"
    fake.parent.mkdir()
    fake.write_text(KEEPER_TASKLOOM, encoding="utf-8")
    fake.chmod(0o755)
    env.write("home/settings.yaml", "keeper:\n- {connection: appserver, command: ~/taskloom/taskloom, home: ~/.taskloom}\n")
    service, notes = scheduler(env, {})

    service.check_servers()  # the keeper thread's check
    service.tick(T0)  # the scheduler's loop tells you what the keeper found

    started = server.home_path / "keeper-started.txt"
    deadline = time.monotonic() + 20
    while not started.exists() and time.monotonic() < deadline:  # it starts detached
        time.sleep(0.1)
    assert started.read_text(encoding="utf-8").strip() == f"{server.home_path}/.taskloom scheduler --headless"
    assert [n for n in notes if "appserver" in " ".join(n) and "started" in " ".join(n)], notes


def test_a_keeper_entry_written_as_just_a_connection_name_is_reported_not_a_crash(env):
    env.write("home/settings.yaml", "keeper: [appserver]\n")  # "list the server in the keeper setting"
    service, notes = scheduler(env, {})

    try:
        service.check_servers()  # the keeper thread of `taskloom scheduler` ends for good if this raises
    except Exception as e:
        pytest.fail(f"the keeper stops checking servers: {type(e).__name__}: {e}")
    service.tick(T0)  # the scheduler's loop tells you what the keeper found

    assert [n for n in notes if "appserver" in " ".join(n)], notes


# --- connections.yaml ------------------------------------------------------------------------------------------

def test_a_connection_of_another_kind_or_an_unknown_kind_is_refused(env):
    env.write("home/connections.yaml", """
        appserver: {kind: smtp, host: 127.0.0.1, sender: me@example.com}
        files: {kind: ftp, host: 127.0.0.1}
    """)
    flow = write(env, SSH_FLOW)

    checked = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow)
    tested = env.taskloom("connection", "test", "files")

    for result in (checked, ran):
        assert result.returncode == 2 and "connection 'appserver' is smtp, expected ssh" in result.stderr, result.stderr
    assert tested.returncode == 1 and "unknown connection kind 'ftp'" in tested.stderr, tested.stderr
    assert runs_recorded(env) == 0


@pytest.mark.parametrize("connection, missing", [
    ("{kind: ssh, username: me}", "host"),
    ("{kind: ssh, host: 127.0.0.1}", "username"),
    ("{kind: smtp, sender: me@example.com}", "host"),
], ids=["ssh without a host", "ssh without a user", "smtp without a host"])
def test_connection_test_names_what_a_connection_is_missing(env, connection, missing):
    env.write("home/connections.yaml", f"server: {connection}\n")

    tested = env.taskloom("connection", "test", "server", timeout=60)

    assert tested.returncode == 1 and tested.stderr.startswith("error: "), tested.stderr
    assert f"'{missing}'" in tested.stderr and "Traceback" not in tested.stderr, tested.stderr


# --- secrets ---------------------------------------------------------------------------------------------------

SECRET_FLOW = """
    blocks:
      token:
        type: logic.ask_user
        config: {prompt: "API token?", kind: password, secret: api_token}
      use:
        type: logic.python
        config:
          inputs: [answer]
          outputs: []
          code: "open('same.txt', 'w').write(str(answer == params['expected']))"
    edges:
      - token.answer -> use.answer
    params:
      expected: {type: text, value: ""}
"""


@pytest.mark.skipif(sys.platform == "win32", reason="getpass reads the Windows console, not stdin; POSIX file modes")
def test_secret_set_makes_the_folder_and_a_new_value_replaces_the_old_one_keeping_the_others(env):
    env.env["TASKLOOM_HOME"] = str(env.root / "new home")  # not made yet
    secrets = env.root / "new home" / "secrets.yaml"
    flow = write(env, SECRET_FLOW)

    first = secret_set(env, "api_token", "first-Value 1")
    other = secret_set(env, "other", "kept")
    second = secret_set(env, "api_token", "second Välue: #2")
    ran = env.taskloom("run", flow, "--param", "expected=second Välue: #2")

    for result in (first, other, second):
        assert result.returncode == 0 and f"saved secret '{result.args[-1]}' in {secrets}" in result.stdout, result.stderr
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert yaml.safe_load(secrets.read_text(encoding="utf-8")) == {"api_token": "second Välue: #2", "other": "kept"}
    for result, value in ((first, "first-Value 1"), (second, "second Välue: #2")):
        assert value not in result.stdout + result.stderr  # never shown
    assert ran.returncode == 0, ran.stderr
    assert (env.root / "same.txt").read_text() == "True"  # Ask User used the stored secret, the newest value


@pytest.mark.skipif(sys.platform == "win32", reason="getpass reads the Windows console, not stdin; POSIX file modes")
def test_secret_set_never_leaves_a_secret_in_a_file_others_can_read(env):
    secrets = env.write("home/secrets.yaml", "old: x\n")
    secrets.chmod(0o644)  # e.g. made by hand

    result = secret_set(env, "api_token", "Tok3n-value-9")

    readable_by_others = stat.S_IMODE(secrets.stat().st_mode) & (stat.S_IRWXG | stat.S_IRWXO)
    assert not (readable_by_others and "Tok3n-value-9" in secrets.read_text(encoding="utf-8")), (
        result.returncode, oct(stat.S_IMODE(secrets.stat().st_mode)), result.stdout)


@pytest.mark.parametrize("content", list(BROKEN_FILES.values()), ids=list(BROKEN_FILES))
def test_a_secrets_file_taskloom_cannot_read_is_an_error_naming_it_and_is_left_as_it_was(env, content):
    from sshserver import free_port

    env.write("home/connections.yaml", f"appserver: {{kind: ssh, host: 127.0.0.1, port: {free_port()}, "
                                       "username: me, password: 'secret:appserver_password'}\n")
    secrets = env.home / "secrets.yaml"
    secrets.write_bytes(content)
    secrets.chmod(0o600)
    flow = write(env, SECRET_FLOW)

    ran = env.taskloom("run", flow)
    told = {"connection test": (env.taskloom("connection", "test", "appserver", timeout=60), 1)}
    if sys.platform != "win32":  # getpass reads the Windows console, not stdin
        told["secret set"] = (secret_set(env, "api_token", "new-value"), None)

    run_id, status = env.last_run()
    [(error,)] = env.query("SELECT error FROM blocks WHERE run_id=? AND block='token'", run_id)
    problems = told_clearly(told, "secrets.yaml")
    if not (ran.returncode == 1 and status == "failed" and "secrets.yaml" in (error or "")):
        problems.append(f"run: exit {ran.returncode}, Ask User's error does not name secrets.yaml: {error!r}")
    assert problems == []
    assert secrets.read_bytes() == content  # nothing in it is lost


# --- your own blocks and block plugins -------------------------------------------------------------------------

def test_running_a_flow_whose_block_file_has_a_mistake_says_what_the_mistake_is(env):
    write(env, textwrap.dedent(GREET_BLOCK) + "\nundefined_helper()\n", "home/blocks/greet.py")
    flow = write(env, GREET_FLOW)

    checked = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow)

    for result in (checked, ran):
        assert result.returncode == 2 and "unknown block type 'my.greet'" in result.stderr, result.stderr
        assert "greet.py" in result.stderr and "undefined_helper" in result.stderr, result.stderr
    assert runs_recorded(env) == 0


def test_a_block_without_a_description_is_valid_with_a_warning_and_one_without_run_fails(env):
    env.write("home/blocks/greet.py", GREET_BLOCK.replace(
        '        description = "Says hello to whoever you name."\n', ""))
    env.write("home/blocks/idle.py", """
        from taskloom import Block, ports


        class Idle(Block):
            type_id = "my.idle"
            description = "Has no run()."
            outputs = {"value": ports.Any()}
    """)
    flow = write(env, GREET_FLOW)
    idle = write(env, "blocks:\n  idle: {type: my.idle}\n", "idle.yaml")

    checked = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow)
    failed = env.taskloom("run", idle)

    assert checked.returncode == 0 and checked.stdout.strip() == "flow: OK (3 blocks)", checked.stderr
    assert "warning: block my.greet has no description" in checked.stderr
    assert ran.returncode == 0, ran.stderr
    assert (env.root / "greeting.txt").read_text(encoding="utf-8") == "Hello, Ana!"
    assert failed.returncode == 1, failed.stderr
    run_id, status = env.last_run()
    assert status == "failed" and env.block_status(run_id) == {"idle": "failed"}


PLUGIN = """
    from taskloom import Block, ports


    class Hello(Block):
        type_id = "plugin.hello"
        description = "Says where it comes from."
        outputs = {"text": ports.Any()}

        def run(self, ctx):
            return {"text": "from the plugin"}
"""

PLUGIN_FLOW = """
    blocks:
      hello:
        type: plugin.hello
      save:
        type: logic.python
        config: {inputs: [text], outputs: [], code: "open('said.txt', 'w').write(text)"}
    edges:
      - hello.text -> save.text
"""


def test_installed_block_plugins_are_found_a_broken_one_is_reported_and_your_blocks_replace_them(env):
    site = env.root / "site"
    write(env, PLUGIN, "site/taskloom_test_plugin.py")
    write(env, "raise ImportError('needs a library that is not installed')\n", "site/taskloom_test_broken_plugin.py")
    info = site / "taskloom_test_plugin-1.0.dist-info"
    write(env, "Metadata-Version: 2.1\nName: taskloom-test-plugin\nVersion: 1.0\n", f"site/{info.name}/METADATA")
    write(env, "[taskloom.blocks]\nhello = taskloom_test_plugin\nbroken = taskloom_test_broken_plugin\n",
          f"site/{info.name}/entry_points.txt")
    # Installed: the package and its entry points are on the path.
    env.env["PYTHONPATH"] = os.pathsep.join(p for p in (str(site), env.env.get("PYTHONPATH")) if p)
    flow = write(env, PLUGIN_FLOW)

    checked = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow)
    said = (env.root / "said.txt").read_text()
    env.write("home/blocks/hello.py", PLUGIN.replace("from the plugin", "from my block"))
    replaced = env.taskloom("run", flow)

    assert checked.returncode == 0, checked.stderr
    assert "warning: could not load block plugin taskloom_test_broken_plugin" in checked.stderr
    assert "needs a library that is not installed" in checked.stderr
    assert ran.returncode == 0 and said == "from the plugin", ran.stderr
    assert replaced.returncode == 0 and (env.root / "said.txt").read_text() == "from my block", replaced.stderr
