"""The scheduler service through its real paths, beyond on-time starts and misfire (tests/test_scheduler_on_time.py):
a flow's overlap setting, max_concurrent_runs, scheduled.yaml and the flow files it lists, notifications (desktop
and email), a run whose process dies, the keeper, starting at login, one scheduler per Taskloom folder, its
heartbeat / scheduler.pid / scheduler.version files, and the editor's side (the Scheduler service button, starting
by itself, the restart offer when an older scheduler runs)."""

import datetime as dt
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import sysconfig
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox, QPushButton  # noqa: E402

from conftest import REPO  # noqa: E402
from sshserver import free_port  # noqa: E402
from taskloom import __version__  # noqa: E402
from taskloom.config import Home  # noqa: E402
from taskloom.editor.safety import pid_alive  # noqa: E402
from taskloom.editor.window import MainWindow  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.scheduler import Scheduler, acquire_lock, paused, running_scheduler_version, set_paused  # noqa: E402

T0 = dt.datetime(2026, 1, 5, 6, 30)  # a Monday
NO_KEYRING = "keyring.backends.fail.Keyring"
REAL_START_SCHEDULER = MainWindow.start_scheduler  # (the win fixture replaces it: no real service by default)


@pytest.fixture(autouse=True)
def no_credential_store(monkeypatch):
    """Never the real credential store: not for the runs the scheduler starts, nor in this process."""
    from taskloom import config

    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", NO_KEYRING)
    monkeypatch.setattr(config, "_keyring", lambda: None)


# --- helpers ---------------------------------------------------------------------------------------------------

def write(path: Path, text: str) -> Path:
    """A file written as Taskloom reads it: UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def at(clock: str) -> dt.datetime:
    return dt.datetime.combine(T0.date(), dt.time.fromisoformat(clock))


def flow(cron: str = "* * * * *", *, wait: float = 0, code: str = "pass", misfire: str = "run_once", **top) -> str:
    """A scheduled flow (flow-level schedule): its as-of time is the parameter `when`; it waits `wait` seconds,
    then runs the Python `code`. `top`: more top-level settings, e.g. overlap="queue"."""
    return yaml.safe_dump({
        **top,
        "params": {"when": {"type": "datetime", "expr": "now()"}},
        "triggers": [{"schedule": {"cron": cron, "misfire": misfire}}],
        "blocks": {"pause": {"type": "logic.wait", "config": {"seconds": wait}},
                   "work": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": code}}},
        "edges": ["pause.value -> work.after"],
    }, sort_keys=False)


def schedule_block_flow(cron: str = "0 * * * *", misfire: str = "run_once", active: bool = True) -> str:
    """A flow started by a Schedule block (as the editor saves it)."""
    return yaml.safe_dump({
        "params": {"when": {"type": "datetime", "expr": "now()"}},
        "blocks": {"hourly": {"type": "trigger.schedule", "config": {"cron": cron, "misfire": misfire, "active": active}},
                   "note": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}}},
        "edges": ["hourly.start -> note.after"],
    }, sort_keys=False)


# Its time (once a year) never comes during a test.
YEARLY = """
    blocks:
      yearly:
        type: trigger.schedule
        config: {cron: "0 2 1 1 *"}
      work:
        type: logic.python
        config: {inputs: [], outputs: [], code: "pass"}
    edges:
      - yearly.start -> work.after
"""


def service(env, flows: dict[str, str]) -> tuple[Scheduler, list]:
    """The scheduler service's loop (what `taskloom scheduler` runs) over these scheduled flows ({file: text});
    the notifications it shows are recorded as (title, message)."""
    home = Home(env.home)
    for name, text in flows.items():
        home.set_scheduled(write(env.root / name, text), True)
    notes = []
    return Scheduler(home, lambda title, message: notes.append((title, message))), notes


def runs(env) -> list[dict]:
    """The runs in history, in the order they started: the flow, its as-of time (HH:MM), status, start and end."""
    History(env.home / "history.db").close()  # the tables exist even before the first run
    rows = env.query("SELECT id, flow, params, status, started, finished FROM runs ORDER BY id")
    return [{"id": i, "flow": name, "when": (json.loads(params).get("when") or "")[11:16], "status": status,
             "started": dt.datetime.fromisoformat(started),
             "finished": dt.datetime.fromisoformat(finished) if finished else None}
            for i, name, params, status, started, finished in rows]


def overlapping(done: list[dict]) -> list[tuple[str, str]]:
    """The pairs of these runs that were going at the same time."""
    return [(a["when"], b["when"]) for n, a in enumerate(done) for b in done[n + 1:]
            if a["started"] < b["finished"] and b["started"] < a["finished"]]


def emails(smtp) -> list[tuple[str, str]]:
    return [(str(m["Subject"]), str(m["To"])) for m in smtp.messages]


def wait_for(condition, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def start_service(root: Path, *args, stdout=subprocess.DEVNULL) -> subprocess.Popen:
    """`taskloom scheduler --headless` for the Taskloom folder `root`, as from a terminal (it prints what it
    tells you on `stdout`)."""
    return subprocess.Popen([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless", *args],
                            env={**os.environ, "TASKLOOM_HOME": str(root), "PYTHON_KEYRING_BACKEND": NO_KEYRING},
                            stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.DEVNULL)


TRAY_SERVICE = """
import json, os

from PySide6.QtWidgets import QSystemTrayIcon

from taskloom.cli import run_cli


def shown(self, title, message, *rest):  # a tray message: written down
    with open(os.environ["TRAY_MESSAGES"], "a", encoding="utf-8") as f:
        f.write(json.dumps([title, message]) + "\\n")


QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)  # the offscreen platform has no tray
QSystemTrayIcon.showMessage = shown
run_cli(["scheduler"])
"""


def start_tray_service(root: Path, messages: Path) -> subprocess.Popen:
    """`taskloom scheduler`, the tray service (as at login or started by the editor), for the Taskloom folder
    `root`; the messages its tray icon shows are written to `messages`."""
    return subprocess.Popen([sys.executable, "-c", TRAY_SERVICE],
                            env={**os.environ, "TASKLOOM_HOME": str(root), "PYTHON_KEYRING_BACKEND": NO_KEYRING,
                                 "QT_QPA_PLATFORM": "offscreen", "TRAY_MESSAGES": str(messages)},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tray_messages(messages: Path) -> list[tuple[str, str]]:
    """The (title, message) of each tray message shown so far."""
    try:
        lines = messages.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [tuple(json.loads(line)) for line in lines if line.endswith("]")]


def stop_service(root: Path, proc: subprocess.Popen):
    """Stop it as the editor turns it off: SIGTERM to the process in scheduler.pid (on Windows that ends it; a
    venv's python.exe there is a launcher, so the scheduler's own process is the one in the file)."""
    if proc.poll() is None:
        try:
            pid = int((root / "scheduler.pid").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pid = None
        if pid and pid != os.getpid() and pid_alive(pid):
            os.kill(pid, signal.SIGTERM)
        else:
            proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)


def heartbeat(root: Path) -> str | None:
    """The time of the scheduler's latest check; None: none yet (or it is being written this instant)."""
    try:
        return (root / "scheduler.heartbeat").read_text(encoding="utf-8") or None
    except OSError:
        return None


def sleeper() -> subprocess.Popen:
    """A process that is running (and is not this test's own)."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


HOLDS_THE_LOCK = """
import os, sys, time
from taskloom.config import Home
from taskloom.scheduler import acquire_lock

home = Home.default()
lock = acquire_lock(home)  # as a scheduler service does while it runs
(home.root / "scheduler.pid").write_text(str(os.getpid()), encoding="utf-8")
if len(sys.argv) > 1:  # the Taskloom it is from (an older one writes none)
    (home.root / "scheduler.version").write_text(f"{sys.argv[1]} {os.getpid()}", encoding="utf-8")
(home.root / "stand-in.ready").write_text("ready", encoding="utf-8")
time.sleep(120)
"""


def stand_in_service(root: Path, version: str | None = None) -> subprocess.Popen:
    """A process that is, to the editor and the keeper, a scheduler service of `version` running for the Taskloom
    folder `root` (it holds the folder's lock and wrote scheduler.pid); it hangs: it never checks anything."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "stand-in.ready").unlink(missing_ok=True)
    proc = subprocess.Popen([sys.executable, "-c", HOLDS_THE_LOCK, *([version] if version else [])],
                            env={**os.environ, "TASKLOOM_HOME": str(root)},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_for(lambda: (root / "stand-in.ready").exists() or proc.poll() is not None):
        proc.kill()
    assert (root / "stand-in.ready").exists(), "the stand-in service did not start"
    return proc


def end(proc: subprocess.Popen, root: Path | None = None):
    """Make sure a process this test started (and, with `root`, the process in its scheduler.pid) is gone."""
    if root is not None:
        try:
            pid = int((root / "scheduler.pid").read_text(encoding="utf-8"))
            if pid != os.getpid() and pid_alive(pid):
                os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=15)


# --- overlap: a run due while the previous one is still going -------------------------------------------------------

@pytest.mark.parametrize("overlap", ["queue", "allow"])
def test_a_run_due_while_the_last_one_is_going_follows_the_flows_overlap_setting(env, overlap):
    # README: "overlap: skip  # if the previous run is still going: skip | queue | allow"; the Flow tab says
    # queue = "run it after the current one", allow = "run both at the same time". (skip: test_scheduler.py)
    scheduler, notes = service(env, {"flow.yaml": flow(wait=6 if overlap == "allow" else 2, overlap=overlap)})
    scheduler.tick(T0)
    for minutes in (1, 2, 3):  # due at 06:31 and, while that run still goes, at 06:32 and 06:33
        scheduler.tick(T0 + dt.timedelta(minutes=minutes))
    scheduler.wait_for_runs()

    done = runs(env)
    assert [r["status"] for r in done] == ["success"] * 3, notes
    assert notes == []  # nothing skipped, nothing failed
    if overlap == "queue":  # one after another, in the order they were due
        assert [r["when"] for r in done] == ["06:31", "06:32", "06:33"]
        assert overlapping(done) == []
    else:  # all three at the same time
        assert sorted(r["when"] for r in done) == ["06:31", "06:32", "06:33"]
        assert len(overlapping(done)) == 3, done


def test_the_overlap_chosen_in_the_flow_tab_is_saved_for_the_scheduler(win, tmp_path):
    # The Flow tab: "If a scheduled run starts while the last one is still going": skip the new run / run it after
    # the current one / run both at the same time.
    from taskloom.flow import load_flow

    path = write(tmp_path / "nightly.yaml", YEARLY)
    tab = win.open_flow(path)
    choice = win.flow_settings.overlap

    for value in ("queue", "allow", "skip"):
        choice.setCurrentIndex(choice.findData(value))
        choice.activated.emit(choice.currentIndex())
        assert win.save(tab)
        assert load_flow(path).overlap == value


# --- max_concurrent_runs ---------------------------------------------------------------------------------------------

def test_catch_up_runs_that_wait_for_a_free_slot_still_go_one_after_another(env):
    # README: "catch_up: one run for each time, oldest first, one after another"; settings.yaml:
    # max_concurrent_runs ("Scheduled runs at once" in Tools -> Settings).
    write(env.home / "settings.yaml", "max_concurrent_runs: 2\n")
    scheduler, notes = service(env, {
        "busy_a.yaml": flow("0 * * * *", wait=3), "busy_b.yaml": flow("0 * * * *", wait=3),
        "report.yaml": flow("0 * * * *", wait=1, misfire="catch_up")})
    scheduler.tick(T0)
    scheduler.tick(T0 + dt.timedelta(hours=3))  # 07:00, 08:00 and 09:00 came: busy_a and busy_b take both slots
    scheduler.wait_for_runs()

    done = runs(env)
    assert {r["status"] for r in done} == {"success"}, notes
    for run in done:  # never more than max_concurrent_runs at once
        going = [r for r in done if r["started"] <= run["started"] < r["finished"]]
        assert len(going) <= 2, going
    report = sorted((r for r in done if r["flow"] == "report"), key=lambda r: r["when"])
    assert [r["when"] for r in report] == ["07:00", "08:00", "09:00"]
    assert overlapping(report) == [], "catch-up runs of one flow went at the same time"


@pytest.mark.parametrize("overlap", ["skip", "queue"])
def test_a_flow_whose_run_waits_for_a_free_slot_never_runs_twice_at_once(env, overlap):
    # README: "overlap: skip  # if the previous run is still going: skip | queue | allow": only allow runs two of
    # a flow's runs at the same time.
    write(env.home / "settings.yaml", "max_concurrent_runs: 2\n")
    scheduler, notes = service(env, {
        "busy_a.yaml": flow(wait=4), "busy_b.yaml": flow(wait=4), "report.yaml": flow(wait=2, overlap=overlap)})
    scheduler.tick(at("06:30:00"))
    scheduler.tick(at("06:31:00"))  # busy_a and busy_b start; report's run waits for a free slot
    scheduler.tick(at("06:32:00"))  # report is due again while its 06:31 run has not finished
    scheduler.wait_for_runs()

    report = [r for r in runs(env) if r["flow"] == "report"]
    assert report and {r["status"] for r in report} == {"success"}, notes
    assert overlapping(report) == [], f"two runs of report at once with overlap: {overlap}"
    if overlap == "queue":
        assert sorted(r["when"] for r in report) == ["06:31", "06:32"]


# --- scheduled.yaml and the flow files it lists ------------------------------------------------------------------------

def test_flows_added_to_or_taken_off_scheduled_yaml_count_from_the_next_check(env):
    # README: saved flows with a Schedule block are listed in scheduled.yaml ("removing the block takes the flow
    # off"); the service rereads at its checks, and "a new or changed schedule counts from that moment".
    scheduler, notes = service(env, {"first.yaml": flow()})
    home = Home(env.home)
    scheduler.tick(at("10:00:10"))
    home.set_scheduled(write(env.root / "second.yaml", flow()), True)  # added while the service runs
    for clock in ("10:01:05", "10:02:05"):
        scheduler.tick(at(clock))
        scheduler.wait_for_runs()
    home.set_scheduled(env.root / "first.yaml", False)  # taken off
    scheduler.tick(at("10:03:05"))
    scheduler.wait_for_runs()

    assert sorted((r["flow"], r["when"]) for r in runs(env)) == [
        ("first", "10:01"), ("first", "10:02"), ("second", "10:02"), ("second", "10:03")], notes
    assert {r["status"] for r in runs(env)} == {"success"} and notes == []


@pytest.mark.parametrize("change", ["deleted", "renamed", "not YAML", "a wrong overlap", "its schedule removed"])
def test_a_scheduled_flow_file_that_goes_wrong_is_told_once_and_the_other_flows_still_run(env, change):
    # README: scheduled.yaml lists the flows the scheduler runs; "the scheduler service ... notifies you"; the
    # scheduler rereads changed flow files at its checks.
    scheduler, notes = service(env, {"good.yaml": flow(), "other.yaml": flow()})
    other = env.root / "other.yaml"
    scheduler.tick(at("10:00:10"))
    if change == "deleted":
        other.unlink()
    elif change == "renamed":
        other.rename(env.root / "other-renamed.yaml")
    elif change == "not YAML":
        write(other, "blocks: [unclosed\n")
    elif change == "a wrong overlap":
        write(other, flow(overlap="sometimes"))
    else:  # its schedule taken out of the file (not through the editor)
        write(other, yaml.safe_dump({k: v for k, v in yaml.safe_load(flow()).items() if k != "triggers"}))

    try:
        for clock in ("10:01:05", "10:01:20", "10:02:05"):
            scheduler.tick(at(clock))
            scheduler.wait_for_runs()
    except Exception as e:
        pytest.fail(f"the scheduler service stops: {type(e).__name__}: {e}")

    done = runs(env)
    assert [(r["flow"], r["when"]) for r in done] == [("good", "10:01"), ("good", "10:02")], notes
    told = [message for title, message in notes if title == "Scheduled flow cannot be read"]
    if change == "its schedule removed":
        assert told == [] and notes == []  # nothing is wrong: it is just not scheduled any more
    else:
        assert len(told) == 1 and "other.yaml" in told[0], notes
        if change == "a wrong overlap":
            assert "overlap" in told[0], told

    # Put right, it is read again and runs at its times.
    if change == "renamed":
        (env.root / "other-renamed.yaml").rename(other)
    else:
        write(other, flow())
    later = time.time() + 10
    os.utime(other, (later, later))  # (a changed file, also where file times are coarse)
    for clock in ("10:03:05", "10:04:00"):
        scheduler.tick(at(clock))
        scheduler.wait_for_runs()
    assert "10:04" in [r["when"] for r in runs(env) if r["flow"] == "other"], notes


@pytest.mark.parametrize("misfire", ["run_once", "catch_up", "skip"])
@pytest.mark.parametrize("way", ["Active unticked", "taken off the schedule"])
def test_a_schedule_turned_back_on_counts_from_that_moment(env, way, misfire):
    # README: "a new or changed schedule counts from that moment (nothing before it counts as missed)"; the
    # Schedule block's Active: "Untick it and this schedule does not start the flow"; saving a flow without a
    # Schedule block takes it off scheduled.yaml ("removing the block takes the flow off").
    scheduler, notes = service(env, {"flow.yaml": schedule_block_flow(misfire=misfire)})
    path, home = env.root / "flow.yaml", Home(env.home)

    def turn(on: bool, clock: str):
        if way == "Active unticked":
            write(path, schedule_block_flow(misfire=misfire, active=on))
            later = time.time() + (20 if on else 10)
            os.utime(path, (later, later))  # (a changed file, also where file times are coarse)
        else:
            home.set_scheduled(path, on)
        scheduler.tick(at(clock))

    scheduler.tick(at("06:30:00"))
    turn(False, "06:40:00")
    for clock in ("08:10:00", "11:10:00"):  # 07:00 to 11:00 come while it is off
        scheduler.tick(at(clock))
    turn(True, "11:35:00")  # turned back on
    scheduler.wait_for_runs()

    assert [r["when"] for r in runs(env)] == [], "times from while it was off started runs"
    assert [n for n in notes if n[0] == "Missed runs skipped"] == []
    scheduler.tick(at("12:00:05"))
    scheduler.wait_for_runs()
    assert [r["when"] for r in runs(env)] == ["12:00"]


def test_a_scheduled_yaml_taskloom_cannot_read_is_told_and_the_service_goes_on(env):
    # README "Your files": scheduled.yaml, the flows the scheduler runs (a file you may edit). As for a mistake in
    # settings.yaml (scheduler._at_limit: "you are told, and the service goes on"), the loop of
    # `taskloom scheduler` must not end: it would stop running every scheduled flow, silently for the tray service.
    scheduler, notes = service(env, {"flow.yaml": flow()})
    scheduler.tick(at("10:00:10"))
    listed = (env.home / "scheduled.yaml").read_text(encoding="utf-8")
    write(env.home / "scheduled.yaml", "flows: [unclosed\n")  # e.g. edited by hand

    try:
        scheduler.tick(at("10:01:05"))
        scheduler.seconds_to_next_check(at("10:01:05"))
    except Exception as e:
        pytest.fail(f"the scheduler service stops: {type(e).__name__}: {e}")
    assert any("scheduled.yaml" in title + message for title, message in notes), notes

    write(env.home / "scheduled.yaml", listed)  # put right
    scheduler.tick(at("10:02:05"))
    scheduler.wait_for_runs()
    assert "10:02" in [r["when"] for r in runs(env)]


# --- notifications -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("notify_on, flow_notify, emailed_to", [
    (["failure", "skipped"], None, "me@example.com"),
    (["failure"], None, None),
    (["failure"], {"when": ["skipped"], "to": ["team@example.com"]}, "team@example.com"),
], ids=["settings ask for skipped runs", "settings do not", "the flow's notify asks"])
def test_skipped_runs_are_told_on_the_desktop_and_emailed_as_the_notify_settings_say(env, smtp, notify_on, flow_notify,
                                                                                     emailed_to):
    # README: "Scheduled runs then email you on failure (or success / skipped runs). A flow can override this";
    # Tools -> Settings: "Email me when ... runs are skipped".
    write(env.home / "settings.yaml", yaml.safe_dump(
        {"notify_email": "me@example.com", "smtp_connection": "mailer", "notify_on": notify_on}))
    top = {"notify": flow_notify} if flow_notify else {}
    scheduler, notes = service(env, {"flow.yaml": flow(wait=3, misfire="skip", **top)})
    scheduler.tick(at("06:30:00"))
    scheduler.tick(at("06:33:00"))  # 06:31 and 06:32 were missed (skip); 06:33 is on time and starts
    scheduler.tick(at("06:34:00"))  # due while that run still goes: skipped (overlap: skip, the default)
    scheduler.wait_for_runs()

    titles = [title for title, _ in notes]
    assert titles == ["Missed runs skipped", "Run skipped"], notes
    expected = [] if emailed_to is None else [("[Taskloom] Missed runs skipped", emailed_to),
                                              ("[Taskloom] Run skipped", emailed_to)]
    assert sorted(emails(smtp)) == expected


def test_a_scheduled_run_that_fails_is_told_once_on_the_desktop_and_emailed_once(env, smtp):
    # README: notify_email, smtp_connection, notify_on (failure by default): "Scheduled runs then email you on
    # failure"; the scheduler service "notifies you about failed or skipped runs".
    write(env.home / "settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    scheduler, notes = service(env, {"flow.yaml": flow(code="raise RuntimeError('disk full')")})
    scheduler.tick(T0)
    scheduler.tick(T0 + dt.timedelta(minutes=1))
    scheduler.wait_for_runs()
    scheduler.tick(T0 + dt.timedelta(minutes=1, seconds=30))

    assert [r["status"] for r in runs(env)] == ["failed"]
    (subject, to), = emails(smtp)  # the run's own email: not a second one from the scheduler
    assert to == "me@example.com" and "flow" in subject and "failed" in subject, subject
    assert "disk full" in smtp.messages[0].get_body(("plain",)).get_content()
    (title, message), = notes  # what failed, also when the email went out
    assert "flow" in title and "failed" in title and "disk full" in message, notes


def test_a_run_whose_process_dies_is_recorded_failed_told_and_does_not_hold_up_the_next_run(env, smtp):
    # history.mark_crashed: "Record a run whose process died without finishing"; scheduler._poll: "it crashed, so
    # it could not email about itself".
    write(env.home / "settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    scheduler, notes = service(env, {"flow.yaml": flow(code="import os\nos._exit(3)")})
    scheduler.tick(T0)
    scheduler.tick(T0 + dt.timedelta(minutes=1))
    scheduler.wait_for_runs()

    (run,) = runs(env)
    assert run["status"] == "failed" and run["finished"] is not None
    error = env.query("SELECT status, error FROM blocks WHERE run_id=? AND block='work'", run["id"])
    assert error == [("failed", "the run process stopped unexpectedly")]
    (title, message), = notes
    assert "flow" in title and "3" in message, notes
    (subject, to), = emails(smtp)
    assert to == "me@example.com" and "flow" in subject and "crashed" in subject, subject

    scheduler.tick(T0 + dt.timedelta(minutes=2))  # its next time: it runs (no run of it is still going)
    scheduler.wait_for_runs()
    assert [r["when"] for r in runs(env)] == ["06:31", "06:32"]


def test_a_run_that_prints_bytes_that_are_not_text_is_still_reported_as_it_ended(env):
    # What runs inside a run's process can write straight to its output, which the scheduler reads: e.g. Java (a
    # database query's JDBC driver) in the system's own encoding, cp1252 on most Windows PCs, or a C library. The
    # run succeeded: it must be told as such (no false "failed" or "crashed"), and its output must still be read
    # (else the run hangs once the pipe is full). 0x81 and 0x90 are neither UTF-8 nor cp1252 text.
    tool = ("import os\n"
            "os.write(1, b'caf\\x81 \\x90\\n')\n"
            "for i in range(2000):\n"
            "    os.write(1, b'.' * 100 + b'\\n')\n")
    scheduler, notes = service(env, {"flow.yaml": flow(code=tool)})
    try:
        scheduler.tick(T0)
        scheduler.tick(T0 + dt.timedelta(minutes=1))
        scheduler.wait_for_runs(timeout=20)
        scheduler.tick(T0 + dt.timedelta(minutes=1, seconds=30))

        assert [r["status"] for r in runs(env)] == ["success"], "the run never finished"
        assert notes == []  # nothing failed
    finally:
        for run in scheduler.runs:
            run.proc.kill()
            run.proc.wait(timeout=15)


@pytest.mark.parametrize("problem", ["mail server down", "no such connection"])
def test_a_notification_email_that_cannot_be_sent_is_told_and_the_service_goes_on(env, problem):
    # scheduler._tell: "A desktop notification, plus an email if the flow's notify settings ask for this event";
    # elsewhere Taskloom names a missing connection: "no connection named 'X' in connections.yaml".
    write(env.home / "connections.yaml", f"mailer: {{kind: smtp, host: 127.0.0.1, port: {free_port()}, "
                                         "sender: taskloom@example.com}\n")
    write(env.home / "settings.yaml", "notify_email: me@example.com\nnotify_on: [skipped]\nsmtp_connection: "
                                      + ("mailer\n" if problem == "mail server down" else "nosuch\n"))
    scheduler, notes = service(env, {"flow.yaml": flow(wait=2)})
    scheduler.tick(T0)
    scheduler.tick(T0 + dt.timedelta(minutes=1))
    try:
        scheduler.tick(T0 + dt.timedelta(minutes=2))  # skipped: the previous run still goes
    except Exception as e:
        pytest.fail(f"the scheduler service stops: {type(e).__name__}: {e}")
    scheduler.wait_for_runs()

    assert [title for title, _ in notes][:1] == ["Run skipped"], notes
    cannot = [message for title, message in notes if title == "Cannot send email"]
    assert len(cannot) == 1, notes
    if problem == "no such connection":
        assert "nosuch" in cannot[0] and "connection" in cannot[0].lower(), cannot


def test_an_email_the_mail_server_never_answers_does_not_hold_up_the_other_flows(env):
    # README: "Each run starts at its time (within a second)". A mail server that takes the connection and never
    # answers (e.g. the wrong port, behind a firewall) must not keep the other scheduled flows from starting.
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen()
    write(env.home / "connections.yaml", f"mailer: {{kind: smtp, host: 127.0.0.1, port: {silent.getsockname()[1]}, "
                                         "sender: taskloom@example.com}\n")
    write(env.home / "settings.yaml", "notify_email: me@example.com\nnotify_on: [skipped]\nsmtp_connection: mailer\n")
    scheduler, notes = service(env, {"a_slow.yaml": flow(wait=8), "b_other.yaml": flow("32 6 * * *")})
    scheduler.tick(at("06:30:00"))
    scheduler.tick(at("06:31:00"))  # a_slow starts
    check = threading.Thread(target=scheduler.tick, args=(at("06:32:00"),), daemon=True)
    try:
        check.start()  # a_slow is due while its run goes: skipped, and emailed about; b_other is due
        on_time = wait_for(lambda: sorted(r["flow"] for r in runs(env)) == ["a_slow", "b_other"], timeout=6)
    finally:
        silent.setblocking(False)
        try:
            while True:  # the mail server hangs up: the email fails, the check goes on
                silent.accept()[0].close()
        except OSError:
            pass
        silent.close()
        check.join(timeout=30)
        scheduler.wait_for_runs()

    assert on_time, f"b_other started only after the email to a silent mail server gave up: {notes}"


@pytest.mark.parametrize("refused", ["a connection no longer in connections.yaml", "a block type that does not load"])
def test_a_scheduled_run_taskloom_refuses_is_told_emailed_and_tried_again_at_its_next_time(env, smtp, refused):
    # README: the service "notifies you about failed or skipped runs"; "Scheduled runs then email you on failure".
    # `taskloom run` refuses such a flow before a run starts (exit code 2, "no connection named 'X' in
    # connections.yaml", "unknown block type 'X'").
    write(env.home / "settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    if refused == "a connection no longer in connections.yaml":
        work, reason = {"type": "servers.ssh_command", "config": {"connection": "appserver", "command": "uptime"}}, \
            "appserver"
    else:  # a block file of yours that no longer loads (e.g. a library it imports was uninstalled)
        write(env.home / "blocks" / "report_tools.py", "import nosuchlib\n")
        work, reason = {"type": "custom.report", "config": {}}, "custom.report"
    refused_flow = yaml.safe_dump({"triggers": [{"schedule": {"cron": "* * * * *"}}], "blocks": {"work": work}})
    scheduler, notes = service(env, {"refused.yaml": refused_flow, "other.yaml": flow()})
    scheduler.tick(T0)
    try:
        for minute in (1, 2):
            scheduler.tick(T0 + dt.timedelta(minutes=minute))
            scheduler.wait_for_runs()
    except Exception as e:
        pytest.fail(f"the scheduler service stops: {type(e).__name__}: {e}")

    told = [f"{title}: {message}" for title, message in notes if "refused" in title]
    assert len(told) == 2 and all(reason in t for t in told), notes  # at each of its times, with the reason
    mailed = [m for m in smtp.messages if "refused" in str(m["Subject"])]
    assert [str(m["To"]) for m in mailed] == ["me@example.com"] * 2, emails(smtp)
    assert all(reason in m.get_body(("plain",)).get_content() for m in mailed)
    assert [(r["flow"], r["when"]) for r in runs(env)] == [("other", "06:31"), ("other", "06:32")], notes


def test_the_tray_service_shows_failed_and_skipped_runs_as_tray_messages(env):
    # README: the scheduler service "keeps running in the background (its icon is in the tray) ... and notifies
    # you about failed or skipped runs"; scheduler.py: "`taskloom scheduler` shows a tray icon with notifications".
    now = dt.datetime.now()
    home = Home(env.home)
    # (their times are half an hour and half a day away: none comes during the test)
    broken = flow(f"{(now.minute + 30) % 60} * * * *", code="raise RuntimeError('disk full')")
    home.set_scheduled(write(env.root / "broken.yaml", broken), True)
    home.set_scheduled(write(env.root / "report.yaml", flow(f"0 {(now.hour + 12) % 24} * * *", misfire="skip")), True)
    Scheduler(home).tick(now - dt.timedelta(days=2))  # on since then: their times came while it was off
    shown = env.root / "tray-messages.jsonl"
    proc = start_tray_service(env.home, shown)
    try:
        assert wait_for(lambda: len(tray_messages(shown)) >= 2, timeout=45), tray_messages(shown)
    finally:
        stop_service(env.home, proc)
        wait_for(lambda: runs(env) and runs(env)[0]["status"] != "running", timeout=20)

    told = sorted(tray_messages(shown))
    assert [title for title, _ in told] == ["Missed runs skipped", "broken failed"], told
    assert "report" in told[0][1] and "disk full" in told[1][1], told


# --- stopping the service: the runs already going --------------------------------------------------------------------

def test_runs_already_going_finish_when_the_scheduler_service_is_turned_off(env):
    # The editor's question when you turn it off: "Runs already going continue."; the service says when it stops:
    # "N run(s) still going continue on their own".
    path = write(env.root / "flow.yaml", flow("0 * * * *", wait=5, code="open('finished.txt', 'w').write('x')"))
    home = Home(env.home)
    home.set_scheduled(path, True)
    Scheduler(home).tick(dt.datetime.now() - dt.timedelta(hours=2))  # on since then: on start, its latest time runs
    proc = start_service(env.home)
    try:
        assert wait_for(lambda: runs(env) and env.query(
            "SELECT 1 FROM blocks WHERE block='pause' AND status='running'")), "no run started"
        stop_service(env.home, proc)  # turned off while the run goes
        wait_for(lambda: runs(env)[0]["status"] != "running", timeout=20)

        assert [r["status"] for r in runs(env)] == ["success"], env.query("SELECT block, status, error FROM blocks")
        assert (env.root / "finished.txt").exists()
    finally:
        end(proc, env.home)


# --- one scheduler per Taskloom folder; scheduler.pid, scheduler.version, the heartbeat ---------------------------------

def test_one_scheduler_service_runs_per_taskloom_folder(env):
    # scheduler.acquire_lock: "only one scheduler runs per home"; README: one scheduler service runs every scheduled
    # flow (of its Taskloom folder).
    other_root = env.root / "other-home"
    first, other = start_service(env.home), None
    try:
        assert wait_for(lambda: heartbeat(env.home)), "the scheduler did not start"
        again = subprocess.run([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"],
                               env={**os.environ, "TASKLOOM_HOME": str(env.home)}, capture_output=True, text=True,
                               timeout=30)
        assert again.returncode == 1, again.stdout + again.stderr
        assert "already running" in again.stderr and "Traceback" not in again.stderr, again.stderr

        other = start_service(other_root)  # another Taskloom folder has a scheduler of its own
        assert wait_for(lambda: heartbeat(other_root)), "the other folder's scheduler did not start"
        assert other.poll() is None and first.poll() is None
    finally:
        stop_service(env.home, first)
        if other is not None:
            stop_service(other_root, other)

    (env.home / "scheduler.heartbeat").unlink()
    after = start_service(env.home)  # once it stopped, one starts again
    try:
        assert wait_for(lambda: heartbeat(env.home)), "no scheduler could start after the first stopped"
    finally:
        stop_service(env.home, after)


def test_a_scheduler_started_at_login_while_on_runs_and_says_which_taskloom_it_is(env):
    # README: starting at login runs `taskloom scheduler --unless-paused`; scheduler.serve: the keeper checks
    # scheduler.pid, the editor scheduler.version; the keeper counts a heartbeat older than 2 minutes as hung.
    proc = start_service(env.home, "--unless-paused")
    try:
        beats = []
        assert wait_for(lambda: beats.append(heartbeat(env.home)) or beats[-1]), "it did not start (it is not off)"
        pid = int((env.home / "scheduler.pid").read_text(encoding="utf-8"))
        version, version_pid = (env.home / "scheduler.version").read_text(encoding="utf-8").split()
        assert pid_alive(pid) and int(version_pid) == pid and version == __version__
        if sys.platform != "win32":  # (on Windows a venv's python.exe starts the real process)
            assert pid == proc.pid
        assert running_scheduler_version(Home(env.home)) == __version__
        first = beats[-1]
        assert abs(int(first) - time.time()) < 30, first
        assert wait_for(lambda: heartbeat(env.home) not in (None, first), timeout=25), "the heartbeat is not renewed"
    finally:
        stop_service(env.home, proc)


# --- the keeper --------------------------------------------------------------------------------------------------------

KEEPER_TASKLOOM = """#!/bin/sh
echo "$TASKLOOM_HOME $*" > "$HOME/keeper-started.txt"
"""
POSIX_SERVER = pytest.mark.skipif(sys.platform == "win32", reason="the test SSH server runs commands in /bin/sh")


def keeper(env, command: str = "~/taskloom/taskloom"):
    """The keeper setting as Tools -> Settings writes it: the server `appserver`, the Taskloom command there."""
    write(env.home / "settings.yaml", yaml.safe_dump(
        {"keeper": [{"connection": "appserver", "command": command, "home": "~/.taskloom"}]}))


def fake_taskloom(server) -> Path:
    """A `taskloom` on the server that only records that it was started."""
    fake = server.home_path / "taskloom" / "taskloom"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text(KEEPER_TASKLOOM, encoding="utf-8")
    fake.chmod(0o755)
    return server.home_path / "keeper-started.txt"


def check_servers(scheduler):
    scheduler.check_servers()  # the keeper thread's check
    scheduler.tick(T0)  # the scheduler's loop tells you what the keeper found


@POSIX_SERVER
def test_the_keeper_leaves_a_server_scheduler_that_is_running_alone(env, server):
    # scheduler._remote_alive: "Its process is running and its heartbeat is recent".
    started = fake_taskloom(server)
    keeper(env)
    remote = server.home_path / ".taskloom"
    remote.mkdir()
    running = sleeper()
    (remote / "scheduler.pid").write_text(str(running.pid), encoding="utf-8")
    (remote / "scheduler.heartbeat").write_text(str(int(time.time())), encoding="utf-8")
    scheduler, notes = service(env, {})
    try:
        check_servers(scheduler)

        time.sleep(1)  # (it would start detached)
        assert notes == [] and not started.exists() and running.poll() is None
    finally:
        end(running)


@POSIX_SERVER
@pytest.mark.parametrize("state", ["its heartbeat is old", "its process is gone"])
def test_the_keeper_starts_a_server_scheduler_that_is_hung_or_gone(env, server, state):
    # README: the keeper "checks it every 5 minutes and starts it again after a server reboot";
    # scheduler._remote_alive: "a hung scheduler counts as dead".
    started = fake_taskloom(server)
    keeper(env)
    remote = server.home_path / ".taskloom"
    remote.mkdir()
    process = sleeper()
    if state == "its process is gone":
        end(process)
        beat = int(time.time())
    else:  # running, but it has not checked for 10 minutes
        beat = int(time.time()) - 600
    (remote / "scheduler.pid").write_text(str(process.pid), encoding="utf-8")
    (remote / "scheduler.heartbeat").write_text(str(beat), encoding="utf-8")
    scheduler, notes = service(env, {})
    try:
        check_servers(scheduler)

        assert wait_for(started.exists, timeout=20), notes
        assert started.read_text(encoding="utf-8").split()[1:] == ["scheduler", "--headless"]
        assert [n for n in notes if "appserver" in n[1]], notes
    finally:
        end(process)


@POSIX_SERVER
@pytest.mark.parametrize("how", ["--headless", "tray"])
def test_the_scheduler_service_keeps_the_servers_scheduler_running_and_says_so(env, server, how):
    # README: "list the server in the keeper setting (Tools -> Settings) on your PC: your scheduler checks it every
    # 5 minutes and starts it again after a server reboot"; Tools -> Settings: "Servers whose scheduler this computer
    # keeps running". Here no scheduler runs on the server (none ever ran there).
    started = fake_taskloom(server)
    keeper(env)
    said_file = env.root / "service-said.txt"
    if how == "tray":
        proc = start_tray_service(env.home, said_file)

        def said():
            return [f"{title}: {message}" for title, message in tray_messages(said_file)]
    else:
        with open(said_file, "w", encoding="utf-8") as out:
            proc = start_service(env.home, stdout=out)

        def said():  # what it printed: "<date> <time> <title>: <message>"
            return [line.split(" ", 2)[-1] for line in said_file.read_text(encoding="utf-8").splitlines()]
    try:
        assert wait_for(started.exists, timeout=30), f"the server's scheduler was not started: {said()}"
        assert started.read_text(encoding="utf-8").split()[1:] == ["scheduler", "--headless"]
        assert wait_for(lambda: [s for s in said() if "appserver: " in s and "started" in s], timeout=30), said()
    finally:
        stop_service(env.home, proc)


@POSIX_SERVER
@pytest.mark.parametrize("folder", ["~/other-taskloom", "an absolute path with a space"])
def test_the_keeper_checks_and_starts_the_scheduler_of_the_taskloom_folder_set_for_the_server(env, server, folder):
    # Tools -> Settings, the keeper's table: "Taskloom folder there"; README: "Everything per-user lives in
    # ~/.taskloom (or $TASKLOOM_HOME)"; one scheduler per Taskloom folder.
    started = fake_taskloom(server)
    if folder == "~/other-taskloom":
        remote = server.home_path / "other-taskloom"
    else:
        remote = server.home_path / "Taskloom data"
        folder = str(remote)
    write(env.home / "settings.yaml", yaml.safe_dump(
        {"keeper": [{"connection": "appserver", "command": "~/taskloom/taskloom", "home": folder}]}))
    default = server.home_path / ".taskloom"  # a scheduler runs there for the default folder: not this one
    default.mkdir()
    other_scheduler, this_scheduler = sleeper(), sleeper()
    (default / "scheduler.pid").write_text(str(other_scheduler.pid), encoding="utf-8")
    (default / "scheduler.heartbeat").write_text(str(int(time.time())), encoding="utf-8")
    scheduler, notes = service(env, {})
    try:
        check_servers(scheduler)
        assert wait_for(started.exists, timeout=20), f"no scheduler was started for {folder}: {notes}"
        assert started.read_text(encoding="utf-8").strip() == f"{remote} scheduler --headless"

        # Once a scheduler runs for that folder, it is left alone.
        started.unlink()
        remote.mkdir(exist_ok=True)
        (remote / "scheduler.pid").write_text(str(this_scheduler.pid), encoding="utf-8")
        (remote / "scheduler.heartbeat").write_text(str(int(time.time())), encoding="utf-8")
        notes.clear()
        check_servers(scheduler)
        time.sleep(1)  # (it would start detached)
        assert notes == [] and not started.exists()
    finally:
        end(other_scheduler)
        end(this_scheduler)


@POSIX_SERVER
def test_the_keeper_does_not_say_it_started_a_server_scheduler_whose_command_does_not_exist(env, server):
    # Tools -> Settings: "Taskloom command on the server"; the keeper's message "the scheduler was not running and
    # was started" must be true, else you are told it could not be started (here: every 5 minutes, forever).
    keeper(env, command="~/taskloom/no-such-taskloom")
    scheduler, notes = service(env, {})

    check_servers(scheduler)

    about = [f"{title}: {message}" for title, message in notes if "appserver" in message]
    assert about, notes
    assert [n for n in about if "was started" in n] == [], about


@POSIX_SERVER
def test_the_keeper_replaces_a_hung_server_scheduler_or_says_it_could_not(env, server):
    # scheduler._remote_alive: "a hung scheduler counts as dead"; the hung one still holds the folder's lock, so a
    # second one cannot start (acquire_lock: "only one scheduler runs per home").
    remote = server.home_path / ".taskloom"
    hung = stand_in_service(remote)  # running, but it no longer checks anything
    (remote / "scheduler.heartbeat").write_text(str(int(time.time()) - 600), encoding="utf-8")
    keeper(env, command=f"{shlex.quote(sys.executable)} -m taskloom.cli")  # the real Taskloom on the server
    scheduler, notes = service(env, {})
    try:
        checked = time.time()
        check_servers(scheduler)
        running_again = wait_for(lambda: int(heartbeat(remote) or 0) >= checked - 1, timeout=20)

        said_started = [n for n in notes if "was started" in n[1]]
        log = remote / "scheduler.log"
        assert [n for n in notes if "appserver" in n[1]], notes
        assert bool(said_started) == running_again, (notes, "a scheduler checks there again" if running_again
                                                     else "no scheduler checks there",
                                                     log.read_text(encoding="utf-8") if log.exists() else "")
    finally:
        end(hung)
        try:  # a scheduler the keeper started there
            replaced = int((remote / "scheduler.pid").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            replaced = None
        if replaced and replaced not in (hung.pid, os.getpid()) and pid_alive(replaced):
            os.kill(replaced, signal.SIGTERM)
            wait_for(lambda: not pid_alive(replaced), timeout=15)


@pytest.mark.parametrize("problem", ["server not reachable", "no such connection"])
def test_a_server_the_keeper_cannot_check_is_told_clearly(env, problem):
    # scheduler.check_servers reports "Cannot check server"; elsewhere Taskloom names a missing connection:
    # "no connection named 'X' in connections.yaml".
    if problem == "server not reachable":
        write(env.home / "connections.yaml", f"appserver: {{kind: ssh, host: 127.0.0.1, port: {free_port()}, "
                                             "username: tester, password: not-needed}\n")
    keeper(env)
    scheduler, notes = service(env, {})

    try:
        check_servers(scheduler)
    except Exception as e:
        pytest.fail(f"the keeper stops checking servers: {type(e).__name__}: {e}")

    (title, message), = notes
    assert title == "Cannot check server" and "appserver" in message, notes
    if problem == "no such connection":
        assert "connection" in message.lower(), message


def test_a_mistake_in_settings_does_not_stop_the_keeper_for_good(env):
    # The keeper thread of `taskloom scheduler` (scheduler._keeper_loop) ends for good if a check raises; as for
    # the other mistakes in settings (scheduler._at_limit: "you are told, and the service goes on").
    write(env.home / "connections.yaml", f"appserver: {{kind: ssh, host: 127.0.0.1, port: {free_port()}, "
                                         "username: tester, password: not-needed}\n")
    write(env.home / "settings.yaml", "keeper: [{connection: appserver, command: taskloom}]\nkeep_run: 5\n")
    scheduler, notes = service(env, {})

    try:
        check_servers(scheduler)
    except Exception as e:
        pytest.fail(f"the keeper stops checking servers: {type(e).__name__}: {e}")
    assert any("keep_run" in message for _, message in notes), notes

    keeper(env)  # put right: the next check checks the server
    notes.clear()
    check_servers(scheduler)
    assert [title for title, message in notes if "appserver" in message] == ["Cannot check server"], notes


# --- start at login ----------------------------------------------------------------------------------------------------

class _OnWindows:
    """What window.py sees as `sys` while the editor opens as the packaged Windows Taskloom."""
    platform = "win32"

    def __getattr__(self, name):
        return getattr(sys, name)


@pytest.mark.parametrize("script, switched", [
    ("an older Taskloom's, for this folder", True),
    ("a newer Taskloom's", False),
    ("another folder's", False),
], ids=["older", "newer", "another folder"])
def test_opening_the_editor_switches_starting_at_login_to_this_version(win, qtbot, tmp_path, monkeypatch, script,
                                                                       switched):
    # README: "starting at login switches to the newest version you opened". Only the packaged Taskloom on Windows
    # does it, so the editor is opened as that one.
    from taskloom.editor import window as window_module
    from taskloom.scheduler import startup_file, startup_script

    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    home = Home(tmp_path / "other-home")
    path = startup_file()
    path.parent.mkdir(parents=True)
    text = startup_script(home)
    if script == "a newer Taskloom's":
        text = text.replace(f"rem Taskloom {__version__}", "rem Taskloom 999.0.0")
    else:
        text = text.replace(f"rem Taskloom {__version__}", "rem Taskloom 0.1.0")
    if script == "another folder's":
        text = text.replace(str(home.root), str(tmp_path / "elsewhere"))
    path.write_text(text, encoding="utf-8", newline="\r\n")

    with monkeypatch.context() as m:
        m.setattr(window_module, "frozen", lambda: True)
        m.setattr(window_module, "sys", _OnWindows())
        opened = window_module.MainWindow(home)
    qtbot.addWidget(opened)

    after = path.read_text(encoding="utf-8")
    if switched:
        assert after == startup_script(home) and f"rem Taskloom {__version__}" in after
        assert __version__ in opened.statusBar().currentMessage()
    else:
        assert after == text


def test_the_settings_box_start_at_login_adds_and_removes_this_folders_startup_script(win, monkeypatch, tmp_path):
    # README: "On Windows, Tools -> Settings -> Start the scheduler service when I log in (or `taskloom scheduler
    # --at-login on`) adds it to your Startup folder". Elsewhere the box is disabled ("Windows only"), so the
    # dialog is opened as on Windows.
    from taskloom.editor import dialogs
    from taskloom.scheduler import startup_file, startup_script

    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(dialogs, "sys", _OnWindows())

    def saved_with(ticked: bool) -> bool:  # opened again: whether the box shows ticked
        dialog = dialogs.SettingsDialog(win.home, win)
        assert dialog.at_login.isEnabled()
        dialog.at_login.setChecked(ticked)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
        return dialogs.SettingsDialog(win.home, win).at_login.isChecked()

    assert not dialogs.SettingsDialog(win.home, win).at_login.isChecked()
    assert saved_with(True)
    assert startup_file().read_text(encoding="utf-8") == startup_script(win.home)  # starts this folder's
    assert not saved_with(False)
    assert not startup_file().exists()


STAND_IN_CLI = """
import json, os, sys

# Stands in for Taskloom in the program the Startup script starts: it writes down what it was started with.
with open(os.environ["STARTED_WITH"] + ".part", "w", encoding="utf-8") as f:
    json.dump({"TASKLOOM_HOME": os.environ.get("TASKLOOM_HOME"), "args": sys.argv[1:]}, f)
os.replace(os.environ["STARTED_WITH"] + ".part", os.environ["STARTED_WITH"])
"""


@pytest.mark.skipif(sys.platform != "win32", reason="the Startup folder, and cmd.exe that runs its scripts, are Windows'")
def test_start_at_login_starts_the_scheduler_from_a_folder_whose_name_has_an_ampersand(env):
    # README: unzip Taskloom "anywhere you can write"; `taskloom scheduler --at-login on` adds the scheduler to your
    # Startup folder. A folder such as C:\Tools\R&D\Taskloom: cmd.exe reads an unquoted & as "then run".
    install = env.root / "Tools" / "R&D" / "Taskloom"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(install)], check=True, capture_output=True,
                   timeout=120)
    python = install / "Scripts" / "python.exe"
    paths = sysconfig.get_paths()
    env.env.update(APPDATA=str(env.root / "AppData"),
                   PYTHONPATH=os.pathsep.join([str(REPO), paths["purelib"], paths["platlib"]]))
    on = subprocess.run([str(python), "-m", "taskloom.cli", "scheduler", "--at-login", "on"], env=env.env,
                        cwd=env.root, capture_output=True, text=True, timeout=120)
    assert on.returncode == 0, on.stderr
    [script] = (env.root / "AppData").rglob("*.cmd")
    # At login Windows runs the script with cmd.exe; what it starts here only writes down how it was started.
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
    wait_for(started_with.exists)  # started detached

    assert started_with.exists(), f"the Startup script started nothing: {script.read_bytes()!r}"
    assert json.loads(started_with.read_text(encoding="utf-8")) == {
        "TASKLOOM_HOME": str(env.home.resolve()), "args": ["scheduler", "--unless-paused"]}, script.read_bytes()


# --- the editor's side -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("state, starts", [
    ("flows scheduled", [False]),
    ("turned off", []),
    ("already on", []),
    ("nothing scheduled", []),
    ("scheduled.yaml unreadable", []),
], ids=["flows scheduled", "turned off", "already on", "nothing scheduled", "scheduled.yaml unreadable"])
def test_the_editor_starts_the_service_by_itself_when_it_opens_with_flows_scheduled(win, qtbot, tmp_path, monkeypatch,
                                                                                    state, starts):
    # README: "It starts by itself when you save a scheduled flow and when the editor opens with flows scheduled";
    # turned off, "it does not start by itself ... until you turn it on again".
    home = Home(tmp_path / "other-home")
    if state != "nothing scheduled":
        home.set_scheduled(write(tmp_path / "nightly.yaml", YEARLY).resolve(), True)
    if state == "turned off":
        set_paused(home, True)
    if state == "scheduled.yaml unreadable":
        write(home.root / "scheduled.yaml", "flows: [unclosed\n")
    started = []
    monkeypatch.setattr(MainWindow, "start_scheduler", lambda self, by_you=False: started.append(by_you))
    lock = acquire_lock(home) if state == "already on" else None
    try:
        opened = MainWindow(home)
        qtbot.addWidget(opened)
        opened.show()
        checked = []
        QTimer.singleShot(0, lambda: checked.append(True))  # after what the window does once it is on screen
        qtbot.waitUntil(lambda: bool(checked), timeout=10_000)
        qtbot.wait(300)
    finally:
        if lock is not None:
            lock.close()

    assert started == starts


def test_saving_a_scheduled_flow_while_scheduled_yaml_is_unreadable_says_so(win, tmp_path, monkeypatch):
    # README: saving a flow with a Schedule block lists it in scheduled.yaml. A scheduled.yaml Taskloom cannot read
    # (edited by hand) must be told plainly, not end the save in an unexpected error.
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(" ".join(map(str, a[1:3])))))
    write(win.home.root / "scheduled.yaml", "flows: [unclosed\n")
    path = write(tmp_path / "nightly.yaml", YEARLY)
    tab = win.open_flow(path)
    tab.doc.move_blocks({"work": (10, 10)})

    try:
        saved = win.save(tab)
    except Exception as e:
        pytest.fail(f"saving ends in an unexpected error: {type(e).__name__}: {e}")

    assert saved and not tab.doc.dirty
    told = warned + [win.statusBar().currentMessage()]
    assert any("scheduled.yaml" in t for t in told), told


def test_turning_the_service_off_asks_first_and_no_keeps_it_on(win, qtbot, monkeypatch):
    # README: the Scheduler service button "shows whether it is on or off; click it to turn it off ... then no
    # scheduled flow runs, and it does not start by itself". It asks first.
    asked = []
    answer = [QMessageBox.No]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[1]) or answer[0]))
    proc = start_service(win.home.root)  # (started from a terminal)
    try:
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=30_000)

        win.scheduler_action.trigger()  # No: it stays on
        assert asked == ["Turn off the scheduler service?"]
        qtbot.wait(500)
        assert proc.poll() is None and not paused(win.home) and win.scheduler_running()

        answer[0] = QMessageBox.Yes
        win.scheduler_action.trigger()  # Yes: off, and it stays off
        proc.wait(timeout=15)
        assert paused(win.home)
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service off", timeout=10_000)
    finally:
        end(proc, win.home.root)


def test_turning_the_service_on_starts_it_and_it_starts_by_itself_again(win, qtbot, monkeypatch):
    # README: turned off, "it does not start by itself (also not at login), until you turn it on again"; the
    # button shows whether it is on or off.
    started = []

    class Recorded(subprocess.Popen):  # the service the editor starts outlives it: stopped at the end
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            started.append(self)

    monkeypatch.setattr(subprocess, "Popen", Recorded)
    monkeypatch.setattr(win, "start_scheduler", types.MethodType(REAL_START_SCHEDULER, win))  # the real one
    set_paused(win.home, True)  # you turned it off
    assert win.scheduler_action.text() == "Scheduler service off"

    win.scheduler_action.trigger()  # turn it on
    try:
        qtbot.waitUntil(win.scheduler_running, timeout=30_000)
        assert not paused(win.home)  # it starts by itself again (when a flow is saved, the editor opens, at login)
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=10_000)
    finally:
        for proc in started:
            wait_for(lambda: proc.poll() is not None or (win.home.root / "scheduler.pid").exists(), timeout=30)
            end(proc, win.home.root)
        wait_for(lambda: not win.scheduler_running(), timeout=15)


@pytest.mark.parametrize("answer", ["Yes", "No"])
def test_an_older_scheduler_service_is_offered_a_restart_once_and_restarted_on_yes(win, qtbot, monkeypatch, answer):
    # README: "the editor then offers to restart it with the newer version".
    asked, started = [], []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[1:3]) or getattr(QMessageBox, answer)))
    monkeypatch.setattr(win, "start_scheduler", lambda by_you=False: started.append(by_you))
    old = stand_in_service(win.home.root, "0.1.0")
    try:
        qtbot.waitUntil(lambda: bool(asked), timeout=10_000)
        (title, text), = asked
        assert title == "Restart the scheduler service?" and "0.1.0" in text and __version__ in text, text
        assert "0.1.0" in win.scheduler_action.toolTip()
        if answer == "Yes":  # the old one stops, then this version's starts
            old.wait(timeout=15)
            qtbot.waitUntil(lambda: started == [False], timeout=20_000)
        else:
            qtbot.wait(4500)  # the next checks (every 2 s) ask no more
            assert old.poll() is None and started == [] and len(asked) == 1
    finally:
        end(old, win.home.root)


def test_a_newer_scheduler_service_is_not_offered_a_restart(win, qtbot, monkeypatch):
    # README: the restart is offered when a newer Taskloom opens than the one the service runs.
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[1:3]) or QMessageBox.No))
    newer = stand_in_service(win.home.root, "999.0.0")
    try:
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=10_000)
        qtbot.wait(2500)  # another check
        assert asked == [] and "older" not in win.scheduler_action.toolTip()
    finally:
        end(newer, win.home.root)


def test_the_settings_dialog_writes_the_scheduler_settings_the_service_reads(win, monkeypatch, tmp_path):
    # Tools -> Settings: "Scheduled runs at once" (0: unlimited), "Email notifications to", "Send them with",
    # "Email me when", and the keeper's servers ("Add server": SSH connection, Taskloom command, folder there).
    from taskloom.editor.dialogs import SettingsDialog

    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (on Windows it also sets starting at login)
    dialog = SettingsDialog(win.home, win)
    dialog.max_runs.setValue(2)
    dialog.notify_email.setText("me@example.com")
    dialog.smtp.setCurrentText("mailer")
    dialog.events["failure"].setChecked(False)
    dialog.events["skipped"].setChecked(True)
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Add server").click()
    dialog.keeper.item(0, 0).setText("appserver")
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()

    settings = win.home.settings()
    assert settings["max_concurrent_runs"] == 2
    assert (settings["notify_email"], settings["smtp_connection"], settings["notify_on"]) == \
           ("me@example.com", "mailer", ["skipped"])
    assert settings["keeper"] == [{"connection": "appserver", "command": "~/taskloom/taskloom", "home": "~/.taskloom"}]

    dialog = SettingsDialog(win.home, win)
    assert dialog.max_runs.value() == 2 and dialog.keeper.rowCount() == 1
    dialog.max_runs.setValue(0)  # "unlimited"
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
    assert win.home.settings()["max_concurrent_runs"] is None
