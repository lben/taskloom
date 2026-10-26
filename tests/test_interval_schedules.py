"""'Every N minutes' (or hours) schedules repeat from when they are switched on, not on the clock's minute marks.

The user's case: a 1-minute schedule switched on at second 14 runs at second 14 (a run cannot be prepared ahead,
so it may begin a second or two late), then every minute at second 14. Switched on is the moment the scheduler
service first sees the schedule as it is now. Clock-based plans (and any cron line) stay on the clock.
"""

import datetime as dt
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from PySide6.QtWidgets import QComboBox, QLineEdit

from taskloom import __version__
from taskloom.config import Home
from taskloom.history import History
from taskloom.scheduler import Scheduler, acquire_lock
from tests.test_editor import write_flow
from tests.test_paths_editor_editing import clean, field, save_as, saved, select_only, settle
from tests.test_scheduler_start_at import (assert_began_at, record_process_starts, start_service, stop_service,
                                           wait_until_finished)
from tests.test_scheduler_start_at import runs as history_runs
from tests.test_scheduler_start_at import write
from tests.test_service_off import plain

DAY = dt.date(2026, 1, 5)
README = Path(__file__).resolve().parents[1] / "README.md"


# --- helpers ---------------------------------------------------------------------------------------------------

def at(clock: str) -> dt.datetime:
    return dt.datetime.combine(DAY, dt.time.fromisoformat(clock))


def flow_with(**schedules) -> str:
    """A flow (as the editor saves it) whose Schedule blocks, name -> config, each start a block of their own."""
    blocks, edges = {}, []
    for name, config in schedules.items():
        blocks[name] = {"type": "trigger.schedule", "config": config}
        blocks[f"{name}_work"] = {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}}
        edges.append(f"{name}.start -> {name}_work.after")
    return yaml.safe_dump({"blocks": blocks, "edges": edges}, sort_keys=False)


def save(path: Path, text: str):
    """Save a flow again, as the editor does; its file's modified time always changes (coarse file clocks)."""
    before = path.stat().st_mtime_ns
    path.write_text(text, encoding="utf-8")
    if path.stat().st_mtime_ns == before:
        os.utime(path, ns=(before + 10_000_000, before + 10_000_000))


def scheduled(env, text: str) -> tuple[Path, Home]:
    History(env.home / "history.db").close()  # its tables exist before any run
    flow = write(env.root / "report.yaml", text)
    home = Home(env.home)
    home.set_scheduled(flow, True)
    return flow, home


def record_runs(monkeypatch) -> list[tuple[str | None, dt.datetime]]:
    """(Schedule block, the time the run begins: its --start-at) of each run process the scheduler starts, in the
    order it starts them."""
    started = []

    class Recorded(subprocess.Popen):
        def __init__(self, args, *rest, **kwargs):
            if isinstance(args, list) and "--start-at" in args:
                block = args[args.index("--trigger") + 1] if "--trigger" in args else None
                started.append((block, dt.datetime.fromisoformat(args[args.index("--start-at") + 1])))
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Recorded)
    return started


def service(home: Home) -> Scheduler:
    """The scheduler service's loop; what it tells is kept in .told (shown when a test fails)."""
    told = []
    scheduler = Scheduler(home, lambda title, message: told.append(f"{title}: {message}"))
    scheduler.told = told
    return scheduler


def tick(scheduler: Scheduler, clock: str):
    """A check of the service at `clock` on DAY; the runs it starts finish before the next one."""
    scheduler.tick(at(clock))
    scheduler.wait_for_runs()


def clocks(started) -> list[str]:
    return [f"{when:%H:%M:%S}" for _, when in started]


def statuses(env) -> list[str]:
    return [status for (status,) in env.query("SELECT status FROM runs ORDER BY id")]


# --- 1, 2: every N minutes or hours, from the moment the service first sees it ------------------------------------

@pytest.mark.parametrize("every, checks, expected", [
    # the user's case: switched on at second 14, then each minute at second 14 (not at :00, nor at the minute marks)
    ("1 minute", ["10:00:14", "10:00:29", "10:01:00", "10:01:10", "10:01:25", "10:02:10"],
     ["10:00:14", "10:01:14", "10:02:14"]),
    ("2 hours", ["09:30:40", "10:00:00", "11:00:00", "11:30:36", "11:30:50", "13:30:36"],
     ["09:30:40", "11:30:40", "13:30:40"]),
], ids=["1 minute", "2 hours"])
def test_an_every_n_schedule_runs_when_first_seen_then_every_n_from_that_second(env, monkeypatch, every, checks,
                                                                                 expected):
    started = record_runs(monkeypatch)
    _, home = scheduled(env, flow_with(check={"every": every}))
    scheduler = service(home)
    for clock in checks:
        tick(scheduler, clock)

    assert clocks(started) == expected, scheduler.told  # the later ones requested early, to begin at their time
    assert statuses(env) == ["success"] * len(expected)


def test_a_cron_line_typed_by_hand_stays_on_the_clock_next_to_an_every_n_minutes_schedule(env, monkeypatch):
    started = record_runs(monkeypatch)
    scheduled(env, flow_with(every_five={"every": "5 minutes"}, on_the_clock={"cron": "*/5 * * * *"}))
    scheduler = service(Home(env.home))
    for clock in ("10:02:14", "10:04:56", "10:07:10", "10:09:56"):
        tick(scheduler, clock)

    assert [(block, f"{when:%H:%M:%S}") for block, when in started] == [
        ("every_five", "10:02:14"), ("on_the_clock", "10:05:00"), ("every_five", "10:07:14"),
        ("on_the_clock", "10:10:00")], scheduler.told
    assert statuses(env) == ["success"] * 4


# --- 4: off and on again keeps the starting point; a change starts it again ---------------------------------------

@pytest.mark.parametrize("misfire, after_restart", [
    ("run_once", ["10:20:14"]),  # one run, for the most recent missed time
    ("skip", []),  # none (the most recent was more than 2 minutes before it was on again)
    ("catch_up", ["10:10:14", "10:15:14", "10:20:14"]),  # each, in order
])
def test_turning_the_service_off_and_on_keeps_the_starting_point_and_follows_misfire(env, monkeypatch, misfire,
                                                                                     after_restart):
    started = record_runs(monkeypatch)
    _, home = scheduled(env, flow_with(check={"every": "5 minutes", "misfire": misfire}))
    before = service(home)
    for clock in ("10:00:14", "10:05:10"):
        tick(before, clock)
    assert clocks(started) == ["10:00:14", "10:05:14"], before.told

    after = service(home)  # off after 10:05:14, on again at 10:23:00: 10:10:14, 10:15:14 and 10:20:14 were missed
    for clock in ("10:23:00", "10:23:15", "10:25:10"):
        tick(after, clock)

    assert clocks(started) == ["10:00:14", "10:05:14"] + after_restart + ["10:25:14"], after.told  # from 10:00:14
    assert statuses(env) == ["success"] * len(started)


@pytest.mark.parametrize("change", ["every changed", "active unticked and ticked again",
                                    "taken off the scheduler and scheduled again"])
def test_changing_n_or_turning_the_schedule_off_and_on_starts_it_again_from_then(env, monkeypatch, change):
    started = record_runs(monkeypatch)
    flow, home = scheduled(env, flow_with(check={"every": "5 minutes"}))
    scheduler = service(home)
    tick(scheduler, "10:00:14")
    if change == "every changed":
        save(flow, flow_with(check={"every": "2 minutes"}))
    elif change == "active unticked and ticked again":
        save(flow, flow_with(check={"every": "5 minutes", "active": False}))
        tick(scheduler, "10:02:00")
        save(flow, flow_with(check={"every": "5 minutes", "active": True}))
    else:
        home.set_scheduled(flow, False)
        tick(scheduler, "10:02:00")
        home.set_scheduled(flow, True)
    # Seen again at 10:03:40: it runs then, and counts from then (10:05:14, from the old start, does not run).
    next_check, next_run = ("10:05:36", "10:05:40") if change == "every changed" else ("10:08:36", "10:08:40")
    for clock in ("10:03:40", "10:05:10", next_check):
        tick(scheduler, clock)

    assert clocks(started) == ["10:00:14", "10:03:40", next_run], scheduler.told
    assert statuses(env) == ["success"] * 3


# --- 2, 3: in real time, saved while the service is on --------------------------------------------------------------

def run_processes_of(starts: Path, flow: Path) -> list[tuple[float, dt.datetime]]:
    """(when the service started it, by time.time(); its --start-at) of each run process of `flow`, oldest first."""
    found = []
    for record in starts.glob("*-*[0-9]"):
        started = json.loads(record.read_text(encoding="utf-8"))
        args = started["args"]
        if "run" in args and "--start-at" in args and Path(args[args.index("run") + 1]).resolve() == flow.resolve():
            found.append((started["time"], dt.datetime.fromisoformat(args[args.index("--start-at") + 1])))
    return sorted(found)


def finished_runs(env, count: int, until: float) -> list[dict]:
    """The finished runs in history, oldest first, once there are `count` of them (or time.time() reaches `until`)."""
    while True:
        done = [r for r in history_runs(env) if r["status"] != "running"]
        if len(done) >= count or time.time() > until:
            return done
        time.sleep(0.1)


def test_saved_while_the_service_is_on_every_minute_runs_at_once_then_each_minute_at_that_second(env):
    """The user's case, with the tray service (as on Windows): a scheduled flow's Schedule block changed to every
    1 minute and saved. The service notices within about a second (it used to take up to 15 s), the first run
    begins right then, and the next begins exactly a minute after that moment, on time."""
    starts = record_process_starts(env)
    flow, _ = scheduled(env, flow_with(check={"cron": "0 2 1 1 *"}))  # (once a year: never during the test)
    service = start_service(env, tray=True)
    try:
        heartbeat = env.home / "scheduler.heartbeat"
        deadline = time.monotonic() + 60
        while not heartbeat.exists() and service.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert heartbeat.exists(), "the scheduler service did not check"
        saved_from = time.time()  # just after its first check
        save(flow, flow_with(check={"every": "1 minute"}))
        saved_to = time.time()
        assert finished_runs(env, 1, until=saved_to + 20), "no run began in the 20 s after the save"
        done = finished_runs(env, 2, until=saved_to + 80)
    finally:
        stop_service(env, service)
        wait_until_finished(env)

    assert len(done) >= 2, f"not two runs in the 80 s after the save: {done}"
    first, second = done[:2]
    assert (first["status"], second["status"]) == ("success", "success"), done
    (_, switched_on), (second_process, second_time) = run_processes_of(starts, flow)[:2]
    # 3: the service saw the save within about a second (to the second: the start may be a whole second)
    assert saved_from - 1 <= switched_on.timestamp() <= saved_to + 2, \
        f"switched on {switched_on.timestamp() - saved_to:+.2f} s after the save"
    # 2: the first run begins right then, within a few seconds of the save
    assert first["started"].timestamp() - saved_to <= 5, f"the first run began {first['started']}, saved {saved_to}"
    # 2: the next one minute after that moment, to the second, begun on time: its process started early
    assert second_time == switched_on + dt.timedelta(minutes=1), (switched_on, second_time)
    assert second_process < second_time.timestamp(), "the second run's process started only at its time"
    assert_began_at(env, second, second_time)


# --- 1: the When builder ----------------------------------------------------------------------------------------

def how_often(win) -> QComboBox:
    return next(c for c in win.properties.findChildren(QComboBox) if c.findText("Custom (a cron line)") >= 0)


def pick(box: QComboBox, text: str):
    box.setCurrentIndex(box.findText(text))
    box.activated.emit(box.currentIndex())
    settle()


def test_the_when_builders_every_n_minutes_choice_saves_every_n_minutes_not_a_cron_line(win, tmp_path, monkeypatch):
    tab = win.new_flow()
    tab.doc.add_block("trigger.schedule", 0, 200)
    select_only(tab, "schedule")

    def config() -> dict:
        return tab.doc.blocks["schedule"].get("config") or {}

    try:
        box = how_often(win)
        minutes = next(box.itemText(i) for i in range(box.count()) if "minute" in box.itemText(i).lower())
        pick(box, minutes)
        pick(field(win, "Every"), "5 minutes")
        assert config().get("every") == "5 minutes" and "cron" not in config(), config()

        save_as(monkeypatch, tmp_path / "check.yaml")
        assert win.save(tab)
        on_file = saved(tmp_path / "check.yaml")["blocks"]["schedule"]["config"]
        assert on_file.get("every") == "5 minutes" and "cron" not in on_file, on_file
        assert win.home.scheduled_flows() == [(tmp_path / "check.yaml").resolve()]

        select_only(tab)
        select_only(tab, "schedule")  # shown again: as the choice it was saved with
        assert how_often(win).currentText() == minutes and field(win, "Every").currentText() == "5 minutes"

        pick(how_often(win), "Every hour")  # a clock-based plan: a cron line again, as today
        assert config().get("cron") == "0 * * * *" and "every" not in config(), config()
    finally:
        clean(win)


def test_a_cron_line_like_every_5_minutes_typed_by_hand_stays_a_custom_cron_line(win, tmp_path):
    tab = win.open_flow(write_flow(tmp_path / "clock.yaml", flow_with(on_the_clock={"cron": "*/5 * * * *"})))
    select_only(tab, "on_the_clock")

    assert how_often(win).currentText() == "Custom (a cron line)"
    line = next(e for e in win.properties.findChildren(QLineEdit) if e.toolTip().startswith("minute hour"))
    assert line.text() == "*/5 * * * *"
    assert tab.doc.blocks["on_the_clock"]["config"] == {"cron": "*/5 * * * *"} and not tab.doc.dirty


# --- 5: the countdown under the Schedule block ------------------------------------------------------------------

@pytest.mark.parametrize("every, interval, seen_ago", [
    ("5 minutes", dt.timedelta(minutes=5), dt.timedelta(seconds=168)),  # next in 00:02:12
    ("2 hours", dt.timedelta(hours=2), dt.timedelta(minutes=30)),  # next in 01:30:00
])
def test_the_countdown_counts_to_the_next_run_as_the_service_runs_it(win, qtbot, tmp_path, every, interval, seen_ago):
    flow = write_flow(tmp_path / "check.yaml", flow_with(check={"every": every}))
    win.home.set_scheduled(flow.resolve(), True)
    lock = acquire_lock(win.home)  # a scheduler service of this Taskloom is on
    (win.home.root / "scheduler.pid").write_text(str(os.getpid()), encoding="utf-8")
    (win.home.root / "scheduler.version").write_text(f"{__version__} {os.getpid()}", encoding="utf-8")
    service = Scheduler(win.home)
    tab = win.open_flow(flow)

    def under() -> str:
        return tab.scene.nodes["check"].countdown

    def shown() -> str:
        return f"{under()} {plain(tab.scene.nodes['check'].countdown_area.toolTip())}"

    def not_seen_yet():  # saved, the service on, but it has not seen it yet: it starts when the service picks it up
        assert not under().startswith("next run in") and "pick" in shown().lower(), shown()

    def counting_down():
        assert under().startswith("next run in"), shown()

    def off():
        assert under() == "scheduler service off", shown()

    try:
        qtbot.waitUntil(not_seen_yet, timeout=10_000)

        seen = dt.datetime.now().replace(microsecond=0) - seen_ago
        service.tick(seen)  # the service saw it then (and ran it then)
        qtbot.waitUntil(counting_down, timeout=10_000)
        text, tip, now = under(), shown(), dt.datetime.now()
        found = re.fullmatch(r"next run in (?:(\d+)d )?(\d\d):(\d\d):(\d\d)", text)
        assert found, text
        days, hours, minutes, seconds = (int(part or 0) for part in found.groups())
        left = ((days * 24 + hours) * 60 + minutes) * 60 + seconds
        expected = (seen + interval - now).total_seconds()
        assert abs(left - expected) <= 2, f"{text}: the next run is {expected:.0f} s away"
        for upcoming in (seen + interval, seen + 2 * interval):  # the next few run times, with seconds
            assert f"{upcoming:%H:%M:%S}" in tip, tip

        lock.close()  # the service is off
        qtbot.waitUntil(off, timeout=10_000)
    finally:
        lock.close()
        service.wait_for_runs()


# --- 6: README ----------------------------------------------------------------------------------------------------

def test_the_readme_schedules_section_explains_every_n_and_on_the_clock():
    text = README.read_text(encoding="utf-8")
    section = text[text.index("## Schedules"):]
    section = section[:section.index("\n## ", 1)]

    assert re.search(r"every: *\d+ *(minutes?|hours?)", section), section  # as in the flow file
    assert "clock" in section.lower(), section
