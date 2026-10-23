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
from taskloom.scheduler import EARLY_START, Scheduler

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


def test_the_scheduler_waits_until_just_before_the_next_due_time_and_at_most_15_seconds(env):
    """It wakes EARLY_START before a due time: that run's process starts then, to begin at its time."""
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
        now = at(clock) - EARLY_START  # each clock as long before as its runs' processes start early
        scheduler.tick(now)
        waited = scheduler.seconds_to_next_check(now)
        assert seconds <= waited + 1e-6 and waited <= min(seconds + 0.01, 15), (clock, waited)  # never before
    scheduler.wait_for_runs()


# --- 1: real time, in the tray scheduler and in `taskloom scheduler --headless` ---------------------


PROBE = """
import itertools
import json
import os
import subprocess
import time

if os.environ.get("TEST_PROCESS_STARTS"):
    _count = itertools.count()

    class RecordedPopen(subprocess.Popen):
        def __init__(self, args, *rest, **kwargs):
            super().__init__(args, *rest, **kwargs)
            record = {"time": time.time(), "args": [str(a) for a in args] if isinstance(args, list) else [str(args)]}
            path = os.path.join(os.environ["TEST_PROCESS_STARTS"], f"{os.getpid()}-{next(_count)}")
            with open(path + ".tmp", "w") as f:
                json.dump(record, f)
            os.replace(path + ".tmp", path)  # never read half-written

    subprocess.Popen = RecordedPopen
"""


@pytest.fixture
def process_starts(env):
    """A folder where the processes started with env.env record each process they start (by a sitecustomize
    module): its command line, and time.time() once it has started."""
    probe, starts = env.root / "probe", env.root / "starts"
    probe.mkdir()
    starts.mkdir()
    (probe / "sitecustomize.py").write_text(PROBE)
    env.env.update(PYTHONPATH=os.pathsep.join(filter(None, [str(probe), env.env.get("PYTHONPATH")])),
                   TEST_PROCESS_STARTS=str(starts))
    return starts


def started_runs(env, starts) -> list[tuple[dt.datetime, float]]:
    """(as-of time, when the scheduler started its process) of each run so far that history has. Matched by the
    as-of time the scheduler passes (on Windows a venv's python.exe is a launcher: the run's own process id differs)."""
    ran = {dt.datetime.fromisoformat(r["when"]) for r in runs(env) if r["when"]}
    found = []
    for record in sorted(starts.glob("*-*[0-9]")):
        started = json.loads(record.read_text())
        args = started["args"]
        if "run" in args and "--as-of" in args:
            as_of = dt.datetime.fromisoformat(args[args.index("--as-of") + 1])
            if as_of in ran:
                found.append((as_of, started["time"]))
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


def test_the_tray_scheduler_starts_a_runs_process_a_few_seconds_before_its_time(env, process_starts):
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
    ahead = due.timestamp() - process_started  # a few seconds early: loaded by its time, it begins then
    assert 0 < ahead < 15, f"its process started {ahead:.2f} s before {due}"


@pytest.mark.skipif(sys.platform == "win32", reason="stops the scheduler with SIGTERM")
def test_the_headless_scheduler_starts_a_runs_process_a_few_seconds_before_its_time(env, process_starts):
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
    ahead = due.timestamp() - process_started  # a few seconds early: loaded by its time, it begins then
    assert 0 < ahead < 15, f"its process started {ahead:.2f} s before {due}"


def _release(env):
    (env.root / "go.txt").write_text("go")


def _hold_until_released(misfire: str) -> str:
    """A per-minute flow whose run waits until go.txt appears next to the flow."""
    return f"""
        params:
          when: {{type: datetime, expr: "now()"}}
        overlap: skip
        blocks:
          every_minute:
            type: trigger.schedule
            config: {{cron: "* * * * *", misfire: {misfire}}}
          hold:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                import time
                from pathlib import Path
                while not Path("go.txt").exists():
                    time.sleep(0.05)
        edges:
          - every_minute.start -> hold.after
    """


@pytest.mark.parametrize("misfire, expected, skipped", [
    ("run_once", ["06:33"], None),
    ("skip", ["06:33"], 2),  # 06:31 and 06:32 were missed; 06:33 is the on-time run, started early
    ("catch_up", ["06:31", "06:32", "06:33"], None),
])
def test_a_check_in_the_early_window_keeps_the_on_time_run(env, misfire, expected, skipped):
    """A check a few seconds before the minute used to start the previous minute and then skip this one.

    The previous run had just been requested, overlap skip (the default) refused the on-time run, and `last`
    had already moved past it, so that minute never ran.
    """
    notes = []
    home = schedule(env, every_minute(misfire))
    scheduler = Scheduler(home, lambda title, message: notes.append((title, message)))
    scheduler.tick(at("06:30:00"))  # first sight: nothing is due yet
    scheduler.tick(at("06:32:56"))  # 5 s early window reaches 06:33; 06:31 and 06:32 are already due
    scheduler.wait_for_runs()

    assert whens(env) == expected, notes
    if skipped:
        (message,) = missed_notes(notes)
        assert re.search(rf"\b{skipped}\b", message), message
    else:
        assert missed_notes(notes) == []
    assert [n for n in notes if n[0] == "Run skipped"] == []


def test_catch_up_while_a_run_is_going_does_not_drop_the_oldest_time(env):
    """catch_up queues every time but the first behind the run it just started. The first still used overlap
    skip, so with a run already going the oldest missed time was skipped and only the later ones ran."""
    notes = []
    home = schedule(env, _hold_until_released("catch_up"))
    scheduler = Scheduler(home, lambda title, message: notes.append((title, message)))
    try:
        scheduler.tick(at("06:30:00"))
        scheduler.tick(at("06:31:00"))  # 06:31 starts and holds
        scheduler.tick(at("06:33:00"))  # 06:32 and 06:33 are due while 06:31 is still going
        _release(env)
        scheduler.wait_for_runs(timeout=30)
    finally:
        _release(env)
        for run in list(scheduler.runs):
            if run.proc.poll() is None:
                run.proc.kill()

    assert whens(env) == ["06:31", "06:32", "06:33"], notes
    assert [n for n in notes if n[0] == "Run skipped"] == []


def test_one_catch_up_time_while_a_run_is_going_still_follows_overlap_skip(env):
    """overlap skip still applies when a single new time comes due. catch_up queues a backlog, not every tick."""
    notes = []
    home = schedule(env, _hold_until_released("catch_up"))
    scheduler = Scheduler(home, lambda title, message: notes.append((title, message)))
    try:
        scheduler.tick(at("06:30:00"))
        scheduler.tick(at("06:31:00"))
        scheduler.tick(at("06:32:00"))  # only 06:32, and 06:31 has not finished
        assert any(title == "Run skipped" for title, _ in notes), notes
        _release(env)
        scheduler.wait_for_runs(timeout=30)
    finally:
        _release(env)
        for run in list(scheduler.runs):
            if run.proc.poll() is None:
                run.proc.kill()

    assert whens(env) == ["06:31"], notes


def test_queued_catch_up_runs_still_happen_after_the_scheduler_restarts(env):
    """Accepted catch-up runs used to live only in memory, while `last` was already moved past them, so a
    restart dropped every run that had not started."""
    notes = []
    home = schedule(env, """
        params:
          when: {type: datetime, expr: "now()"}
        blocks:
          hourly:
            type: trigger.schedule
            config: {cron: "0 * * * *", misfire: catch_up}
          note:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - hourly.start -> note.after
    """)
    first = Scheduler(home, lambda title, message: notes.append((title, message)))
    first.tick(at("06:30:00"))
    first.tick(at("09:30:00"))  # 07:00 starts; 08:00 and 09:00 wait for it
    queued = [when for times in first.queued.values() for when, _ in times]
    assert [t.strftime("%H:%M") for t in queued] == ["08:00", "09:00"]
    # The scheduler process is gone: its in-memory queue goes with it. The runs it already started keep going.
    restarted = Scheduler(home, lambda title, message: notes.append((title, message)))
    restarted.tick(at("09:30:00"))
    restarted.wait_for_runs()
    for run in list(first.runs):
        if run.proc.poll() is None:
            run.proc.wait(timeout=30)

    # 07:00 was already started by the first scheduler. A restart does not adopt that process, so its
    # history row can be written after the 08:00 run the new scheduler starts itself.
    assert sorted(whens(env)) == ["07:00", "08:00", "09:00"], (whens(env), notes)
