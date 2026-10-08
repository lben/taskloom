"""Schedules and the scheduler, with real runner processes."""

import datetime as dt
import json
import sys
import time

import pytest

from taskloom.config import Home
from taskloom.history import History
from taskloom.scheduler import Scheduler, set_start_at_login, startup_script
from taskloom.schedule import Cron, Trigger

T0 = dt.datetime(2026, 1, 5, 6, 30)  # a Monday


def test_cron_next_times():
    friday_evening = dt.datetime(2026, 10, 9, 18, 0)
    assert Cron("0 7 * * MON-FRI").next_after(friday_evening) == dt.datetime(2026, 10, 12, 7, 0)
    assert Cron("*/15 * * * *").next_after(dt.datetime(2026, 10, 8, 10, 7)) == dt.datetime(2026, 10, 8, 10, 15)
    assert Cron("30 6 1 * *").next_after(dt.datetime(2026, 10, 8)) == dt.datetime(2026, 11, 1, 6, 30)
    once = Trigger({"schedule": {"at": "2026-10-08 15:30"}})
    assert once.due(dt.datetime(2026, 10, 8, 9), dt.datetime(2026, 10, 9)) == [dt.datetime(2026, 10, 8, 15, 30)]
    assert once.next_after(dt.datetime(2026, 10, 8, 16)) is None


def _scheduler(env, flow_text, notes):
    flow = env.write("flow.yaml", flow_text)
    home = Home(env.home)
    home.set_scheduled(flow, True)
    return Scheduler(home, lambda title, message: notes.append((title, message))), flow


def _runs(env):
    history = History(env.home / "history.db")
    runs = history.runs()
    history.close()
    return [(json.loads(r["params"])["when"], r["status"]) for r in reversed(runs)]


HOURLY = """
    params:
      when: {{type: datetime, expr: "now()"}}
    triggers:
      - schedule: {{cron: "0 * * * *", misfire: {misfire}}}
    blocks:
      note:
        type: logic.python
        config: {{inputs: [], outputs: [], code: "pass"}}
"""


@pytest.mark.parametrize("misfire, expected", [
    ("skip", []),
    ("run_once", ["2026-01-05 09:00:00"]),
    ("catch_up", ["2026-01-05 07:00:00", "2026-01-05 08:00:00", "2026-01-05 09:00:00"]),
])
def test_missed_runs_follow_the_misfire_policy(env, misfire, expected):
    notes = []
    scheduler, _ = _scheduler(env, HOURLY.format(misfire=misfire), notes)
    scheduler.tick(T0)  # first sight of the schedule: nothing is missed yet
    scheduler.tick(T0 + dt.timedelta(hours=3))  # 07:00, 08:00 and 09:00 were missed

    scheduler.wait_for_runs()

    assert _runs(env) == [(when, "success") for when in expected]
    if misfire == "skip":
        assert notes == [("Missed runs skipped", "flow: 3 run(s) were due while the scheduler was off")]


def test_on_time_runs_and_overlap_skip(env):
    notes = []
    scheduler, _ = _scheduler(env, """
        params:
          when: {type: datetime, expr: "now()"}
        triggers:
          - schedule: {cron: "* * * * *"}
        blocks:
          pause:
            type: logic.wait
            config: {seconds: 3}
    """, notes)
    scheduler.tick(T0)
    scheduler.tick(T0 + dt.timedelta(minutes=1))  # due now: starts
    scheduler.tick(T0 + dt.timedelta(minutes=2))  # due again while still running: skipped (overlap: skip)

    scheduler.wait_for_runs()

    assert _runs(env) == [("2026-01-05 06:31:00", "success")]
    assert notes == [("Run skipped", "flow: the previous run is still going")]


def test_failed_scheduled_run_is_notified(env):
    notes = []
    scheduler, _ = _scheduler(env, """
        triggers:
          - schedule: {cron: "* * * * *"}
        blocks:
          boom:
            type: logic.python
            config: {inputs: [], code: "raise RuntimeError('disk full')"}
    """, notes)
    scheduler.tick(T0)
    scheduler.tick(T0 + dt.timedelta(minutes=1))
    scheduler.wait_for_runs()
    scheduler.tick(T0 + dt.timedelta(minutes=1, seconds=30))  # reports finished runs

    assert notes == [("flow failed", "boom: RuntimeError: disk full")]


@pytest.mark.skipif(sys.platform == "win32", reason="stops the scheduler with SIGTERM")
def test_only_one_scheduler_runs_per_home(env):
    first = env.popen("scheduler", "--headless")
    try:
        deadline = time.monotonic() + 30
        while not (env.home / "scheduler.heartbeat").exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        second = env.taskloom("scheduler", "--headless", timeout=30)

        assert second.returncode == 1
        assert "already running" in second.stderr
    finally:
        first.terminate()
        assert first.wait(timeout=30) == 0


def test_start_at_login_writes_a_startup_script(env, monkeypatch):
    monkeypatch.setenv("APPDATA", str(env.root / "AppData"))
    home = Home(env.home)

    path = set_start_at_login(home, True)

    assert path.parent.name == "Startup" and path.read_text() == startup_script(home)
    assert "scheduler" in path.read_text() and str(env.home) in path.read_text()
    set_start_at_login(home, False)
    assert not path.exists()


def test_cli_as_of_computes_parameters_for_that_time(env):
    flow = env.write("flow.yaml", """
        params:
          day: {type: date, expr: "today() - bdays(1)"}
        blocks:
          note:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
    """)
    result = env.taskloom("run", flow, "--as-of", "2026-01-05 07:00")

    assert result.returncode == 0, result.stderr
    assert env.query("SELECT params FROM runs") == [('{"day": "2026-01-02"}',)]

