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

    assert notes == [("flow failed", "boom: RuntimeError: disk full (line 1)")]


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
        from taskloom import __version__
        from taskloom.scheduler import running_scheduler_version
        assert running_scheduler_version(Home(env.home)) == __version__  # the editor can tell its version
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



@pytest.mark.parametrize("plan,cron,words", [
    ({"kind": "minutes", "every": 5}, "*/5 * * * *", "Every 5 minutes"),
    ({"kind": "minutes", "every": 1}, "* * * * *", "Every minute"),
    ({"kind": "hourly", "minute": 15}, "15 * * * *", "Every hour at :15"),
    ({"kind": "daily", "hour": 7, "minute": 0}, "0 7 * * *", "Every day at 07:00"),
    ({"kind": "weekdays", "hour": 7, "minute": 30}, "30 7 * * MON-FRI", "Monday to Friday at 07:30"),
    ({"kind": "weekly", "days": [1, 3, 5], "hour": 18, "minute": 5}, "5 18 * * MON,WED,FRI", "Mon, Wed, Fri at 18:05"),
    ({"kind": "weekly", "days": [0], "hour": 9, "minute": 0}, "0 9 * * SUN", "Sun at 09:00"),
    ({"kind": "monthly", "day": 1, "hour": 6, "minute": 0}, "0 6 1 * *", "Day 1 of every month at 06:00"),
])
def test_schedule_plans_turn_into_cron_and_back(plan, cron, words):
    from taskloom.schedule import cron_plan, describe_cron, plan_cron

    assert plan_cron(plan) == cron
    assert cron_plan(cron) == plan
    assert describe_cron(cron) == words


def test_a_cron_line_no_plan_covers_is_custom():
    from taskloom.schedule import cron_plan, describe_cron

    assert cron_plan("0 7-17/2 * * MON-FRI") is None
    assert describe_cron("0 7-17/2 * * MON-FRI") == "Custom: 0 7-17/2 * * MON-FRI"
    assert cron_plan("0 7 * * 1-5") == {"kind": "weekdays", "hour": 7, "minute": 0}  # numbers read too


def test_a_start_at_login_script_says_which_version_starts(env, monkeypatch):
    from taskloom import __version__
    from taskloom.scheduler import startup_script_outdated

    home = Home(env.home)
    script = startup_script(home)

    assert f"rem Taskloom {__version__}" in script and not startup_script_outdated(script, home)
    older = script.replace(f"rem Taskloom {__version__}", "rem Taskloom 0.1.0")
    assert startup_script_outdated(older, home)
    assert startup_script_outdated(script.replace(f"rem Taskloom {__version__}\r\n", "").replace(
        f"rem Taskloom {__version__}\n", ""), home)  # from before versions were written in it
    newer = script.replace(f"rem Taskloom {__version__}", "rem Taskloom 999.0.0")
    assert not startup_script_outdated(newer, home)  # a newer Taskloom set it: leave it
    assert not startup_script_outdated(older.replace(str(env.home), "C:\\elsewhere"), home)  # another folder's


def test_a_flow_this_scheduler_cannot_read_names_its_version(env):
    from taskloom import __version__

    flow = env.write("flow.yaml", """
        some_future_setting: 1
        blocks:
          nightly:
            type: trigger.schedule
            config: {cron: "* * * * *"}
    """)
    home = Home(env.home)
    home.set_scheduled(flow, True)
    told = []
    Scheduler(home, lambda title, message: told.append((title, message))).tick(T0)

    (title, message), = told
    assert title == "Scheduled flow cannot be read"
    assert f"this scheduler is Taskloom {__version__}" in message and "restart the scheduler from it" in message


def test_starting_at_login_keeps_a_paused_scheduler_service_off(env):
    from taskloom.scheduler import set_paused

    home = Home(env.home)
    assert "scheduler --unless-paused" in startup_script(home)
    set_paused(home, True)

    result = env.taskloom("scheduler", "--headless", "--unless-paused", timeout=30)

    assert result.returncode == 0 and "paused" in result.stdout
    assert not (env.home / "scheduler.heartbeat").exists()  # it did not start
