"""Scheduled runs start on time, and the Schedule misfire setting is followed exactly."""

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

import pytest

from taskloom.config import Home
from taskloom.history import History
from taskloom.scheduler import Scheduler

DAY = dt.date(2026, 1, 5)


def at(clock: str) -> dt.datetime:
    return dt.datetime.combine(DAY, dt.time.fromisoformat(clock))


def every_minute(misfire: str = "run_once", source: str = "block") -> str:
    """A per-minute flow, scheduled by a Schedule block or by the flow-level `triggers:` list."""
    if source == "block":
        return f"""
            params:
              when: {{type: datetime, expr: "now()"}}
            blocks:
              every_minute:
                type: trigger.schedule
                config: {{cron: "* * * * *", misfire: {misfire}}}
              note:
                type: logic.python
                config: {{inputs: [], outputs: [], code: "pass"}}
            edges:
              - every_minute.start -> note.after
        """
    return f"""
        params:
          when: {{type: datetime, expr: "now()"}}
        triggers:
          - schedule: {{cron: "* * * * *", misfire: {misfire}}}
        blocks:
          note:
            type: logic.python
            config: {{inputs: [], outputs: [], code: "pass"}}
    """


def schedule(env, text: str) -> Home:
    flow = env.write("flow.yaml", text)
    home = Home(env.home)
    home.set_scheduled(flow, True)
    return home


def runs(env) -> list[dict]:
    """The runs in history, oldest first; "when" is the as-of time (what now() was)."""
    History(env.home / "history.db").close()  # the tables exist even before the first run
    rows = env.query("SELECT id, params, status, started, finished, pid FROM runs ORDER BY id")
    return [{"id": i, "when": json.loads(p).get("when"), "status": s, "started": st, "finished": f, "pid": pid}
            for i, p, s, st, f, pid in rows]


def whens(env) -> list[str]:
    return [r["when"][11:16] for r in runs(env)]


def missed_notes(notes) -> list[str]:
    return [message for title, message in notes if title == "Missed runs skipped"]


# --- 2, 3, 4, 5: what runs at each check ------------------------------------------------------------


@pytest.mark.parametrize("source", ["block", "flow"])
@pytest.mark.parametrize("misfire, after_restart, skipped", [
    ("run_once", ["10:48"], None),
    ("skip", ["10:48"], 3),  # 10:45, 10:46 and 10:47 were missed; 10:48 is on time
    ("catch_up", ["10:45", "10:46", "10:47", "10:48"], None),
], ids=["run_once", "skip", "catch_up"])
def test_after_a_restart_the_misfire_setting_is_followed_exactly(env, source, misfire, after_restart, skipped):
    """The reported case: per-minute schedule, checks every 15 s, off after 10:44:52, on again at 10:48:07."""
    notes = []
    home = schedule(env, every_minute(misfire, source))
    before = Scheduler(home, lambda title, message: notes.append((title, message)))
    for clock in ("10:43:52", "10:44:07", "10:44:22", "10:44:37", "10:44:52"):
        before.tick(at(clock))
    before.wait_for_runs()
    assert whens(env) == ["10:44"]

    after = Scheduler(home, lambda title, message: notes.append((title, message)))  # turned on again
    after.tick(at("10:48:07"))
    after.wait_for_runs()
    assert whens(env) == ["10:44"] + after_restart

    for clock in ("10:48:22", "10:48:37", "10:48:52"):
        after.tick(at(clock))
        after.wait_for_runs()
    assert whens(env) == ["10:44"] + after_restart  # nothing more at the following checks

    after.tick(at("10:49:00"))
    after.wait_for_runs()
    assert whens(env) == ["10:44"] + after_restart + ["10:49"]
    assert {r["status"] for r in runs(env)} == {"success"}
    if skipped:
        (message,) = missed_notes(notes)
        assert re.search(rf"\b{skipped}\b", message), message
    else:
        assert missed_notes(notes) == []
    if misfire == "catch_up":  # one after another, oldest first
        caught_up = runs(env)[1:5]
        for previous, following in zip(caught_up, caught_up[1:]):
            assert following["started"] >= previous["finished"]


def test_each_schedule_block_starts_its_own_runs_at_its_own_times(env):
    home = schedule(env, """
        params:
          when: {type: datetime, expr: "now()"}
        blocks:
          hourly:
            type: trigger.schedule
            config: {cron: "0 * * * *"}
          half_past:
            type: trigger.schedule
            config: {cron: "30 * * * *"}
          load:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
          report:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - hourly.start -> load.after
          - half_past.start -> report.after
    """)
    scheduler = Scheduler(home)
    for clock in ("06:30:10", "07:00:05", "07:30:05", "08:00:05"):
        scheduler.tick(at(clock))
        scheduler.wait_for_runs()

    started_by = []
    for run in runs(env):
        status = env.block_status(run["id"])
        started_by.append((run["when"][11:16], [b for b in ("hourly", "half_past") if status.get(b) == "success"]))
    assert started_by == [("07:00", ["hourly"]), ("07:30", ["half_past"]), ("08:00", ["hourly"])]


def test_a_paused_schedule_block_never_starts_a_run(env):
    notes = []
    home = schedule(env, """
        blocks:
          every_minute:
            type: trigger.schedule
            config: {cron: "* * * * *", misfire: catch_up, active: false}
          note:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - every_minute.start -> note.after
    """)
    scheduler = Scheduler(home, lambda title, message: notes.append((title, message)))
    for clock in ("10:00:00", "10:01:00", "10:05:30"):
        scheduler.tick(at(clock))
    Scheduler(home, lambda title, message: notes.append((title, message))).tick(at("11:00:00"))  # after a restart

    assert runs(env) == [] and notes == []


@pytest.mark.parametrize("misfire, expected, skipped", [
    ("run_once", ["08:00"], None),
    ("catch_up", ["08:00"], None),
    ("skip", [], 1),
], ids=["run_once", "catch_up", "skip"])
def test_a_one_off_time_missed_while_the_scheduler_was_off(env, misfire, expected, skipped):
    notes = []
    home = schedule(env, f"""
        params:
          when: {{type: datetime, expr: "now()"}}
        blocks:
          once:
            type: trigger.schedule
            config: {{at: "2026-01-05 08:00", misfire: {misfire}}}
          note:
            type: logic.python
            config: {{inputs: [], outputs: [], code: "pass"}}
        edges:
          - once.start -> note.after
    """)
    Scheduler(home, lambda title, message: notes.append((title, message))).tick(at("06:30:00"))
    after = Scheduler(home, lambda title, message: notes.append((title, message)))  # on again after 08:00
    for clock in ("09:30:00", "09:30:15", "10:00:00"):
        after.tick(at(clock))
        after.wait_for_runs()

    assert whens(env) == expected
    if skipped:
        (message,) = missed_notes(notes)
        assert re.search(rf"\b{skipped}\b", message), message
    else:
        assert missed_notes(notes) == []


def test_an_edited_schedule_starts_from_now(env):
    home = schedule(env, every_minute("catch_up").replace('"* * * * *"', '"0 * * * *"'))
    Scheduler(home).tick(at("10:00:30"))
    env.write("flow.yaml", every_minute("catch_up"))  # edited while the scheduler was off

    after = Scheduler(home)
    after.tick(at("10:30:10"))  # the edited schedule is seen for the first time: nothing was missed
    after.wait_for_runs()
    assert runs(env) == []
    after.tick(at("10:31:00"))
    after.wait_for_runs()
    assert whens(env) == ["10:31"]


# --- 1: how long the scheduler waits before its next check --------------------------------------


def test_the_scheduler_waits_until_the_next_due_time_and_at_most_15_seconds(env):
    home = Home(env.home)
    scheduler = Scheduler(home)
    assert scheduler.seconds_to_next_check(at("10:00:00")) == pytest.approx(15)  # nothing is scheduled

    blocks = env.write("blocks.yaml", """
        blocks:
          report:
            type: trigger.schedule
            config: {cron: "30 * * * *"}
          paused:
            type: trigger.schedule
            config: {cron: "14 * * * *", active: false}
          note:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - report.start -> note.after
          - paused.start -> note.after
    """)
    flow_level = env.write("flow_level.yaml", """
        triggers:
          - schedule: {cron: "15 * * * *"}
        blocks:
          note:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
    """)
    env.write("not_scheduled.yaml", """
        triggers:
          - schedule: {cron: "13 * * * *"}
        blocks:
          note:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
    """)
    home.set_scheduled(blocks, True)
    home.set_scheduled(flow_level, True)

    for clock, seconds in [
        ("10:00:00", 15),  # the next due time is more than 15 s away
        ("10:12:50", 15),  # 10:13 is the time of a flow that is not scheduled
        ("10:13:50", 15),  # 10:14 is the time of a paused Schedule block
        ("10:14:50.250", 9.75),  # the flow-level trigger at 10:15
        ("10:15:00", 15),
        ("10:29:57", 3),  # the Schedule block at 10:30
        ("10:29:59.999", 0.001),
    ]:
        now = at(clock)
        scheduler.tick(now)
        waited = scheduler.seconds_to_next_check(now)
        assert seconds <= waited + 1e-6 and waited <= min(seconds + 0.01, 15), (clock, waited)  # never before
    scheduler.wait_for_runs()


# --- 1: real time, in the tray scheduler and in `taskloom scheduler --headless` ---------------------


PROBE = """
import os
import subprocess
import time

if os.environ.get("TEST_PROCESS_STARTS"):
    class RecordedPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            with open(os.path.join(os.environ["TEST_PROCESS_STARTS"], str(self.pid)), "w") as f:
                f.write(repr(time.time()))

    subprocess.Popen = RecordedPopen
"""


@pytest.fixture
def process_starts(env):
    """A folder where the scheduler processes started with env.env write when they start each
    process: <the process id> holds its time.time() (by a sitecustomize module)."""
    probe, starts = env.root / "probe", env.root / "starts"
    probe.mkdir()
    starts.mkdir()
    (probe / "sitecustomize.py").write_text(PROBE)
    env.env.update(PYTHONPATH=os.pathsep.join(filter(None, [str(probe), env.env.get("PYTHONPATH")])),
                   TEST_PROCESS_STARTS=str(starts))
    return starts


def started_runs(env, starts) -> list[tuple[dt.datetime, float]]:
    """(as-of time, when the scheduler started its process) of each run so far."""
    found = []
    for run in runs(env):
        start_file = starts / str(run["pid"])
        if run["pid"] and start_file.exists():
            found.append((dt.datetime.fromisoformat(run["when"]), float(start_file.read_text())))
    return found


def wait_for_clock(ok):
    """Wait until the second of the minute (with its fraction) suits the test."""
    deadline = time.monotonic() + 30
    while not ok((now := dt.datetime.now()).second + now.microsecond / 1e6):
        assert time.monotonic() < deadline
        time.sleep(0.05)


def next_minute(t: dt.datetime) -> dt.datetime:
    return t.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)


def wait_until_finished(env, timeout=30):
    deadline = time.monotonic() + timeout
    while any(r["status"] == "running" for r in runs(env)) and time.monotonic() < deadline:
        time.sleep(0.2)


def first_run(env, starts, service) -> tuple[dt.datetime, list]:
    """Wait for the scheduler's first check, then for the run due the next minute: (due, started_runs)."""
    heartbeat = env.home / "scheduler.heartbeat"
    deadline = time.monotonic() + 60
    while not heartbeat.exists() and service.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert heartbeat.exists(), "the scheduler did not start"
    due = next_minute(dt.datetime.fromtimestamp(heartbeat.stat().st_mtime))  # after its first check
    found = []
    while not found and dt.datetime.now() < due + dt.timedelta(seconds=16):
        time.sleep(0.1)
        found = started_runs(env, starts)
    return due, found


TRAY_SCHEDULER = """
import datetime as dt
import time

from PySide6.QtWidgets import QSystemTrayIcon

import taskloom.scheduler  # noqa: F401 (loaded before the clock is right)
from taskloom.cli import run_cli

QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)  # the offscreen platform has no tray
# Start where a check every 15 s from the start would come 4 to 11 s after the minute.
while not 4 <= (dt.datetime.now().second + dt.datetime.now().microsecond / 1e6) % 15 <= 11:
    time.sleep(0.05)
run_cli(["scheduler"])
"""


def test_the_tray_scheduler_starts_a_run_within_a_second_of_its_time(env, process_starts):
    """The reported case: the countdown reached 0 at hh:mm:00 but the run started at about hh:mm:13."""
    schedule(env, every_minute())
    service = subprocess.Popen([sys.executable, "-c", TRAY_SCHEDULER], env={**env.env, "QT_QPA_PLATFORM": "offscreen"},
                               cwd=env.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        due, found = first_run(env, process_starts, service)
    finally:
        service.terminate()
        service.wait(timeout=30)
        wait_until_finished(env)

    assert found, "no run started"
    (as_of, process_started), = found
    assert as_of == due
    assert 0 <= process_started - due.timestamp() < 1.0, f"started {process_started - due.timestamp():.2f} s after {due}"


@pytest.mark.skipif(sys.platform == "win32", reason="stops the scheduler with SIGTERM")
def test_the_headless_scheduler_starts_a_run_within_a_second_of_its_time(env, process_starts):
    schedule(env, every_minute())
    wait_for_clock(lambda second: 2 <= second <= 50)  # it starts (and first checks) well before the minute
    service = env.popen("scheduler", "--headless")
    try:
        due, found = first_run(env, process_starts, service)
    finally:
        service.terminate()
        assert service.wait(timeout=30) == 0
        wait_until_finished(env)

    assert found, "no run started"
    (as_of, process_started), = found
    assert as_of == due
    assert 0 <= process_started - due.timestamp() < 1.0, f"started {process_started - due.timestamp():.2f} s after {due}"
