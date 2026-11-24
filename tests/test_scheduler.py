"""Schedules and the scheduler, with real runner processes."""

import datetime as dt
import json
import sys
import time

import pytest

from taskloom.config import Home
from taskloom.history import History
from taskloom.scheduler import Scheduler
from taskloom.schedule import Cron, Trigger
from tests.test_scheduler_promises import on

T0 = dt.datetime(2026, 1, 5, 6, 30)  # a Monday


@pytest.mark.invariant("SCH-5")
def test_cron_next_times():
    friday_evening = dt.datetime(2026, 10, 9, 18, 0)
    assert Cron("0 7 * * MON-FRI").next_after(friday_evening) == dt.datetime(2026, 10, 12, 7, 0)
    assert Cron("*/15 * * * *").next_after(dt.datetime(2026, 10, 8, 10, 7)) == dt.datetime(2026, 10, 8, 10, 15)
    assert Cron("30 6 1 * *").next_after(dt.datetime(2026, 10, 8)) == dt.datetime(2026, 11, 1, 6, 30)
    once = Trigger({"schedule": {"at": "2026-10-08 15:30"}})
    assert once.due(dt.datetime(2026, 10, 8, 9), dt.datetime(2026, 10, 9)) == [dt.datetime(2026, 10, 8, 15, 30)]
    assert once.next_after(dt.datetime(2026, 10, 8, 16)) is None


def _scheduler(env, flow_text, notes):
    """The scheduler service over this flow; made inside on(env), as `taskloom scheduler` makes it: the lock first."""
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
@pytest.mark.invariant("SCH-9")
def test_missed_runs_follow_the_misfire_policy(env, misfire, expected):
    notes = []
    with on(env):
        scheduler, _ = _scheduler(env, HOURLY.format(misfire=misfire), notes)
        scheduler.tick(T0)  # first sight of the schedule: nothing is missed yet
        scheduler.tick(T0 + dt.timedelta(hours=3))  # 07:00, 08:00 and 09:00 were missed

        scheduler.wait_for_runs()

    assert _runs(env) == [(when, "success") for when in expected]
    if misfire == "skip":
        assert notes == [("Missed runs skipped", "flow: 3 run(s) were due while the scheduler was off")]


@pytest.mark.invariant("SCH-11")
def test_on_time_runs_and_overlap_skip(env):
    notes = []
    with on(env):
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
    with on(env):
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
@pytest.mark.invariant("SCH-1")
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


@pytest.mark.invariant("SCH-3")
def test_start_at_login_puts_its_entry_in_the_windows_startup_folder_for_this_taskloom_folder(env, tmp_path,
                                                                                              monkeypatch):
    """README: Start the scheduler service when I log in "adds it to your Startup folder; no admin needed". On Windows
    that folder is %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup, and it is there already (turning it
    on twice works too). The entry is a shortcut, Taskloom Scheduler.lnk (UP-2): it starts this Taskloom's windowed
    program (no console window), in its folder, for this Taskloom folder, the folder's name kept as it is: a letter
    outside ASCII and a % in it. (`taskloom scheduler --at-login on` of the packaged Windows Taskloom, as in
    tests/test_updates.py; `off` is tests/test_paths_cli_config.py's.)"""
    from taskloom import __version__
    from tests.test_updates import Packaged, entry_for, login_args, read_entry, unpacked, windows_folders

    folders = windows_folders(tmp_path, monkeypatch)
    folders.startup.parent.mkdir(parents=True)
    root = env.root / "100% José"
    env.env["TASKLOOM_HOME"] = str(root)
    taskloom = Packaged(env, folders, unpacked(tmp_path / "Downloads" / "taskloom", __version__))

    for _ in range(2):
        on = taskloom.run("scheduler", "--at-login", "on")
        assert on.returncode == 0, on.stdout + on.stderr

    assert sorted(folders.startup.parent.iterdir()) == [folders.startup]
    assert read_entry(folders.startup) == entry_for(taskloom.folder, login_args(root.resolve()))


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


@pytest.mark.parametrize("entry", ["older", "from before versions were written", "newer", "another folder's"])
def test_a_start_at_login_entry_says_which_version_starts(env, tmp_path, monkeypatch, entry):
    """The Startup entry says which version it starts by its program's folder (its VERSION file). A start of this
    version (`taskloom run` of the packaged Windows Taskloom, as in tests/test_updates.py) points it at itself when it
    starts an older one (0.1.0), or one whose folder has no VERSION (from before versions were written there); a newer
    Taskloom set it: left as it is. Another Taskloom folder's entry stays that folder's (its arguments are kept)."""
    from taskloom import __version__
    from tests.test_updates import HELLO, Packaged, entry_for, login_args, read_entry, unpacked, windows_folders, \
        write_entry

    folders = windows_folders(tmp_path, monkeypatch)
    this = unpacked(folders.programs / __version__, __version__)
    version = "999.0.0" if entry == "newer" else "0.1.0"
    other = unpacked(folders.programs / version, version)
    if entry == "from before versions were written":
        (other / "VERSION").unlink()
    home = env.root / "elsewhere" if entry == "another folder's" else env.home
    write_entry(folders.startup, other / "taskloomw.exe", login_args(home), other)

    done = Packaged(env, folders, this).run("run", env.write("hello.yaml", HELLO))

    assert done.returncode == 0, done.stdout + done.stderr
    assert read_entry(folders.startup) == entry_for(other if entry == "newer" else this, login_args(home))


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
    with on(env):  # (made as `taskloom scheduler` makes it: the lock first)
        Scheduler(home, lambda title, message: told.append((title, message))).tick(T0)

    (title, message), = told
    assert title == "Scheduled flow cannot be read"
    assert f"this scheduler is Taskloom {__version__}" in message and "restart the scheduler from it" in message


@pytest.mark.invariant("SCH-2")
def test_starting_at_login_keeps_a_paused_scheduler_service_off(env, tmp_path, monkeypatch):
    from taskloom import __version__
    from taskloom.scheduler import set_paused
    from tests.test_updates import Packaged, read_entry, unpacked, windows_folders

    home = Home(env.home)
    folders = windows_folders(tmp_path, monkeypatch)
    on = Packaged(env, folders, unpacked(tmp_path / "Downloads" / "taskloom", __version__)).run(
        "scheduler", "--at-login", "on")
    assert on.returncode == 0, on.stdout + on.stderr
    at_login = read_entry(folders.startup)  # the Startup entry (a shortcut, UP-2): what it starts Taskloom with
    assert at_login is not None and at_login.arguments.endswith(" scheduler --unless-paused"), at_login
    set_paused(home, True)

    result = env.taskloom("scheduler", "--headless", "--unless-paused", timeout=30)

    assert result.returncode == 0 and "scheduler service is off" in result.stdout
    assert not (env.home / "scheduler.heartbeat").exists()  # it did not start
