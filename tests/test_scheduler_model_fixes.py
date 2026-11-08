"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml), in the four cases the
formal model of the service (specs/formal) showed the first design breaks:

- a schedule on the clock saved, or turned on, while the service is off: its times after the save that came before the
  service saw them follow Misfire (SCH-9), its times before the save neither run nor count (SCH-10), and an Every N
  schedule still counts from when the service first sees it (SCH-7);
- a run you start yourself while a scheduled run of the flow is still loading, or waiting its turn: the scheduled run
  follows the flow's Overlap (SCH-11, read as its words say);
- the service turned off and on again while a run's program loads: that run does not begin, and its time follows
  Misfire (SCH-8), but a run waiting its turn of a flow you let finish still begins (SCH-4);
- Stop now, then the service turned on again before it has looked, or Proceed with no service running: the flow's
  waiting runs are dropped all the same, and its runs going are stopped and recorded as cancelled (SCH-4).

Saving goes through the real editor window, and a run you start yourself is a real `taskloom run` process. The
scheduled times use the service's checks (Scheduler.tick) on a chosen clock, each service made as `taskloom scheduler`
makes it (the Taskloom folder's lock first: FakeRuns.service), with each scheduled run's process stood in for at the
process boundary by a stand-in that claims as the real run does, from its command line (FakeRuns; a run's program
still loading: test_scheduler_while_off.Runs). Where the behaviour crosses processes in real time, the real service
(`taskloom scheduler --headless`, or started from the editor's button), real run processes and real editor dialogs are
used; a run's program slow to load is a block file in the Taskloom folder slow to import."""

import datetime as dt
import gc
import os
import re
import signal
import subprocess
import time
import types

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from taskloom.config import Home  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.scheduler import request_turn_off, set_paused  # noqa: E402
from tests.test_editor import REAL_START_SCHEDULER  # noqa: E402
from tests.test_interval_schedules import run_processes_of  # noqa: E402
from tests.test_paths_editor_editing import field, select_only, type_in  # noqa: E402
from tests.test_scheduler_at_its_time import SLOW_LOAD, clock, one_off  # noqa: E402
from tests.test_scheduler_manual_runs import flow_text, let_go, soon, started_by_hand  # noqa: E402
from tests.test_scheduler_mistakes_and_counts import headless, printed, told  # noqa: E402
from tests.test_scheduler_new_promises import (HOURLY, NAMES, TURN_OFF, WAIT_A_MINUTE,  # noqa: E402
                                               answer_when_shown, begins, drain, end_runs, ended, flow_yaml, going,
                                               history, hold_until, pump, says, schedule, statuses, stop_the_service,
                                               utc, wait_until, write)
from tests.test_scheduler_start_at import record_process_starts, stop_service  # noqa: E402
from tests.test_scheduler_while_off import Runs, has_loaded  # noqa: E402

SECOND, MINUTE = dt.timedelta(seconds=1), dt.timedelta(minutes=1)


@pytest.fixture
def fake_runs(monkeypatch) -> Runs:
    """The scheduled runs' processes replaced by stand-ins (FakeRuns; the next run of a flow in .loading is still
    loading until the test says it has loaded: has_loaded); a run you start yourself is real. The service made last
    with them stops at the end."""
    runs = Runs()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


@pytest.fixture
def your_runs(env):
    """Cleanup: the runs you started end, and none outlives the test."""
    yield
    write(env.root / "nightly.go", "go")
    end_runs(env.home)


def about(said: list[str], flow: str) -> list[str]:
    return [t for t in said if flow in t]


def missed_said(said: list[str], flow: str) -> list[str]:
    return [t for t in about(said, flow) if re.search(r"missed", t, re.IGNORECASE)]


def clear_of_a_minute():
    """Waits until this PC's clock is at least 2 s away from a whole minute: a save then is plainly before or after
    each minute's time."""
    while not 2 <= dt.datetime.now().second <= 57:
        time.sleep(0.1)


# --- SCH-9, SCH-10, SCH-7: a schedule saved, or turned on, while the service is off ---------------------------------

@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-10")
@pytest.mark.parametrize("misfire, ran, missed", [("skip", [3], 2), ("run_once", [3], None), ("catch_up", [1, 2, 3], None)],
                         ids=["Skip", "Run once", "Catch up"])
def test_a_cron_line_changed_while_the_service_is_off_follows_misfire_for_its_times_after_the_save(
        win, tmp_path, fake_runs, misfire, ran, missed):
    """report runs on the cron line */2 * * * * (every other minute); the service first saw it 10 minutes ago and has
    been off since. In the editor you change its cron line to * * * * * (every minute) and save, at S. The service is
    back 2.5 minutes after M1, the first whole minute after S: M1, M2 and M3 (one minute apart) came after your save,
    while the service was off. They were missed, and follow Misfire (SCH-9): Skip runs M3 (30 s late, so on time) and
    tells you 2 were missed; Run once runs one run, for M3; Catch up runs M1, M2 and M3, oldest first. The minutes from
    before the save, while the service was off, neither run nor count as missed (SCH-10)."""
    home = win.home
    path = schedule(home.root, tmp_path, {"report": flow_yaml({"cron": "*/2 * * * *", "misfire": misfire})})["report"]
    said = []
    seen = (dt.datetime.now() - 10 * MINUTE).replace(microsecond=0)
    drain(fake_runs.service(home.root, lambda title, message: said.append(f"{title}: {message}")), seen, fake_runs)
    fake_runs.stop()  # (it is off from then on)
    gc.collect()  # (its hold on the history file goes with it)
    tab = win.open_flow(path)
    select_only(tab, "s1")
    clear_of_a_minute()
    type_in(field(win, "Cron line"), "* * * * *")
    assert win.save(tab)
    saved = dt.datetime.now()
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["blocks"]["s1"]["config"]["cron"] == "* * * * *", \
        "not arranged: the cron line was not changed and saved"
    first = saved.replace(second=0, microsecond=0) + MINUTE
    times = [first + k * MINUTE for k in range(3)]
    back = fake_runs.service(home.root, lambda title, message: said.append(f"{title}: {message}"))
    drain(back, times[-1] + 30 * SECOND, fake_runs)

    expected = [times[k - 1] for k in ran]
    assert begins(fake_runs.of("report")) == utc(expected), (
        f"saved at {saved:%H:%M:%S} while the service was off, and the service back at {times[-1] + 30 * SECOND:%H:%M:%S}"
        f" ({misfire}): runs began for {begins(fake_runs.of('report'))}, not for "
        f"{[f'{t:%H:%M}' for t in expected]}; told: {said}")
    if missed:
        assert [t for t in missed_said(said, "report") if says(t, missed)], \
            f"not told that {missed} runs were missed (the ones after the save only): {said}"
    else:
        assert missed_said(said, "report") == [], f"told of missed runs: {said}"


@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("misfire, late, runs", [("skip", 3 * MINUTE, False), ("skip", 30 * SECOND, True),
                                                 ("run_once", 3 * MINUTE, True)],
                         ids=["Skip, back 3 minutes after it", "Skip, back 30 s after it",
                              "Run once, back 3 minutes after it"])
def test_a_one_off_time_turned_on_while_the_service_is_off_follows_misfire_when_the_service_is_back(
        win, tmp_path, fake_runs, misfire, late, runs):
    """report has one Schedule block, a one-off time T, 20 s from now, with Active unticked (so it is not scheduled).
    While the service is off, you tick Active in the editor and save: report is scheduled. T comes, and the service is
    back after it: T came after your save, while the service was off, so it was missed and follows Misfire (SCH-9):
    Skip runs none and tells you 1 was missed, unless T is less than 2 minutes late, when it still runs; Run once runs
    it, once."""
    home = win.home
    History(home.history_db).close()
    due = (dt.datetime.now() + 20 * SECOND).replace(microsecond=0)
    path = write(tmp_path / "report.yaml", flow_yaml(one_off(due, misfire=misfire, active=False))).resolve()
    tab = win.open_flow(path)
    select_only(tab, "s1")
    field(win, "Active").setChecked(True)
    assert win.save(tab)
    saved = dt.datetime.now()
    assert saved < due and path in home.scheduled_flows(), f"not arranged: report was not scheduled before {due}"
    said = []
    back = fake_runs.service(home.root, lambda title, message: said.append(f"{title}: {message}"))
    drain(back, due + late, fake_runs)

    ran = begins(fake_runs.of("report"))
    if runs:
        assert ran == utc([due]) and missed_said(said, "report") == [], (
            f"turned on at {saved:%H:%M:%S} while the service was off, and the service back {late} after T "
            f"({due:%H:%M:%S}, {misfire}): runs began for {ran}, not once for T; told: {said}")
    else:
        assert ran == [] and [t for t in missed_said(said, "report") if says(t, 1)], (
            f"turned on at {saved:%H:%M:%S} while the service was off, and the service back {late} after T "
            f"({due:%H:%M:%S}, Skip): runs began for {ran}, and you were told {about(said, 'report')}, not that 1 "
            f"was missed")


@pytest.mark.invariant("SCH-7")
@pytest.mark.invariant("SCH-9")
def test_an_every_n_schedule_turned_on_while_the_service_is_off_counts_from_when_the_service_first_sees_it(
        win, tmp_path, fake_runs):
    """minutely runs every 1 minute (Misfire Catch up), with Active unticked. While the service is off, you tick
    Active in the editor and save; the service first sees it 3.5 minutes later. An Every N schedule counts from when the
    service first sees it (SCH-7): its first run is then, the next a minute after, and nothing before was missed."""
    home = win.home
    History(home.history_db).close()
    path = write(tmp_path / "minutely.yaml",
                 flow_yaml({"every": "1 minute", "misfire": "catch_up", "active": False})).resolve()
    tab = win.open_flow(path)
    select_only(tab, "s1")
    field(win, "Active").setChecked(True)
    assert win.save(tab) and path in home.scheduled_flows(), "not arranged: minutely was not scheduled"
    seen = (dt.datetime.now() + 3.5 * MINUTE).replace(microsecond=0)
    said = []
    back = fake_runs.service(home.root, lambda title, message: said.append(f"{title}: {message}"))
    drain(back, seen, fake_runs)
    drain(back, seen + MINUTE, fake_runs)

    assert begins(fake_runs.of("minutely")) == utc([seen, seen + MINUTE]), (
        f"turned on while the service was off, and first seen by it at {seen:%H:%M:%S}: runs began for "
        f"{begins(fake_runs.of('minutely'))}, not at {seen:%H:%M:%S} and a minute after; told: {said}")
    assert missed_said(said, "minutely") == [], f"told of missed runs: {said}"


# --- SCH-11: a run you start yourself while a scheduled run of the flow loads, or waits its turn ----------------------

@pytest.mark.invariant("SCH-11")
@pytest.mark.parametrize("overlap", ["skip", "queue", "allow"])
def test_a_scheduled_run_still_loading_as_you_start_a_run_of_its_flow_yourself_follows_its_overlap(
        env, fake_runs, your_runs, overlap):
    """nightly's one-off time T comes, and the service starts its run; while that run's program loads, you start
    nightly yourself from a terminal (`taskloom run nightly.yaml`), and your run begins first. The scheduled run then
    has loaded. A second run goes at once only if Overlap is Allow (SCH-11): Skip, the scheduled run does not begin,
    and you are told T was skipped as your run is going; Queue, it does not begin while your run goes, and T runs, once,
    after your run has ended; Allow, both go."""
    due = soon()
    path = schedule(env.home, env.root, {"nightly": flow_text(one_off(due), overlap, "nightly")})["nightly"]
    said = []
    service = fake_runs.service(env.home, lambda title, message: said.append(f"{title}: {message}"))
    fake_runs.loading.add("nightly")
    service.tick(due - 20 * SECOND)  # (it first sees the schedule)
    service.tick(due)  # nightly's run starts: its program loads
    (scheduled,) = fake_runs.of("nightly")
    mine = started_by_hand(env, path)  # you start nightly yourself: your run begins
    began = has_loaded(fake_runs, scheduled)  # the scheduled run's program has loaded: it claims as it begins
    for moment in (due + SECOND, due + 2 * SECOND):
        drain(service, moment, fake_runs)
    while_yours = begins(fake_runs.of("nightly"))
    yours_going = mine.poll() is None
    let_go(env, "nightly", mine)  # your run ends
    drain(service, due + 10 * SECOND, fake_runs)

    assert yours_going, "not arranged: your run had ended before the scheduled run had loaded"
    if overlap == "allow":
        assert began and while_yours == utc([due]), \
            f"Overlap Allow: T's run did not begin alongside yours: runs began for {while_yours}; told: {said}"
        return
    assert not began and while_yours == [], (
        f"Overlap {overlap}: the scheduled run for T ({due:%H:%M:%S}), which loaded as your run of nightly began, "
        f"began alongside yours: runs began for {while_yours}; told: {said}")
    if overlap == "skip":
        skipped = [t for t in about(said, "nightly") if re.search(r"skip", t, re.IGNORECASE)]
        assert skipped and begins(fake_runs.of("nightly")) == [], \
            f"Overlap Skip: T was not skipped and told: runs began for {begins(fake_runs.of('nightly'))}; told: {said}"
    else:
        assert begins(fake_runs.of("nightly")) == utc([due]), (
            f"Overlap Queue: once your run ended, T ({due:%H:%M:%S}) did not run, once: runs began for "
            f"{begins(fake_runs.of('nightly'))}; told: {said}")


@pytest.mark.invariant("SCH-11")
@pytest.mark.parametrize("overlap", ["skip", "queue", "allow"])
def test_a_run_waiting_for_a_free_slot_does_not_begin_while_a_run_of_its_flow_you_started_goes(
        env, fake_runs, your_runs, overlap):
    """Max concurrent runs 1. other's run (every 60 minutes: it runs as the service first sees it) holds the only slot
    when nightly's one-off time T comes, so nightly's run waits its turn. You start nightly yourself from a terminal,
    and your run is going when other's run ends and the slot is free. Unless nightly's Overlap is Allow, its waiting run
    does not begin while yours goes (SCH-11); its time is not lost unseen: once your run has ended, it runs, or you
    have been told it was skipped. Allow: it begins as the slot is free."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    due = soon()
    paths = schedule(env.home, env.root, {"nightly": flow_text(one_off(due), overlap, "nightly"),
                                          "other": flow_text(HOURLY, "skip", "other")})
    said = []
    service = fake_runs.service(env.home, lambda title, message: said.append(f"{title}: {message}"))
    fake_runs.holding.add("other")  # its run goes on until let go
    service.tick(due - 20 * SECOND)  # other's run begins, as the service first sees it
    service.tick(due)  # nightly's time: its run waits for the slot
    assert [r.flow for r in fake_runs.started] == ["other"], f"not arranged: nightly's run did not wait: {said}"
    mine = started_by_hand(env, paths["nightly"])
    fake_runs.holding.discard("other")  # other's run ends: the slot is free
    for moment in (due + SECOND, due + 2 * SECOND, due + 3 * SECOND):
        drain(service, moment, fake_runs)
    while_yours = begins(fake_runs.of("nightly"))
    yours_going = mine.poll() is None
    let_go(env, "nightly", mine)
    drain(service, due + 10 * SECOND, fake_runs)
    after = begins(fake_runs.of("nightly"))

    assert yours_going, "not arranged: your run had ended before the slot was free"
    if overlap == "allow":
        assert while_yours == utc([due]), \
            f"Overlap Allow: nightly's waiting run did not begin as the slot was free: {while_yours}; told: {said}"
        return
    assert while_yours == [], (f"Overlap {overlap}: nightly's run waiting for the slot began while the run of nightly "
                               f"you started was going: {while_yours}; told: {said}")
    skipped = [t for t in about(said, "nightly") if re.search(r"skip", t, re.IGNORECASE)]
    assert after == utc([due]) or (after == [] and skipped), \
        f"once your run had ended, T ({due:%H:%M:%S}) neither ran once nor was told skipped: {after}; told: {said}"


# --- SCH-8, SCH-9, SCH-4: the service turned off and on again while a run's program loads -----------------------------

@pytest.mark.invariant("SCH-8")
@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-6")
@pytest.mark.parametrize("misfire", ["skip", "run_once"], ids=["Skip", "Run once"])
@pytest.mark.parametrize("checked", [False, True], ids=["on again before the service checked",
                                                       "the service checked in between"])
def test_a_run_loading_as_you_turn_the_service_off_and_on_again_does_not_begin_and_its_time_runs_once(
        env, fake_runs, misfire, checked):
    """report's one-off time 07:00 comes, and the service starts its run. While its program loads, you turn the
    service off (Proceed: no run is going, so it only asked to confirm) and on again: before the service has checked,
    or after it has checked once. The run then has loaded: it does not begin, and nothing is recorded for it (SCH-8:
    the service was turned off while it loaded). Its time is not lost: once the service is on, 07:00 follows Misfire
    (SCH-9), and it is less than 2 minutes late, so it still runs, once (Skip too), and nothing is told missed."""
    schedule(env.home, env.root, {"report": flow_yaml(one_off(clock("07:00:00"), misfire=misfire))})
    said = []
    service = fake_runs.service(env.home, lambda title, message: said.append(f"{title}: {message}"))
    fake_runs.loading.add("report")
    service.tick(clock("06:59:30"))
    service.tick(clock("07:00:00"))  # its run starts: its program loads
    (first,) = fake_runs.of("report")
    request_turn_off(Home(env.home), {})  # Proceed
    if checked:
        service.tick(clock("07:00:01"))
    set_paused(Home(env.home), False)  # you turn it on again
    began = has_loaded(fake_runs, first)
    if service.off:  # (the check ended it: turning it on started a new one)
        service = fake_runs.service(env.home, lambda title, message: said.append(f"{title}: {message}"))
    for moment in ("07:00:02", "07:00:03", "07:00:05"):
        drain(service, clock(moment), fake_runs)

    assert not began, (f"report's run, whose program was loading as you turned the service off and on again, began: "
                       f"told: {said}")
    assert begins(fake_runs.of("report")) == utc([clock("07:00")]), (
        f"once the service was on again, 07:00 (5 s late, {misfire}) did not run, once: runs began for "
        f"{begins(fake_runs.of('report'))}; told: {said}")
    assert missed_said(said, "report") == [], f"told of missed runs: {said}"


@pytest.mark.invariant("SCH-8")
@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-6")
def test_a_run_loading_as_you_turn_the_real_service_off_and_on_again_does_not_begin_and_its_time_runs_once(env):
    """The real service (`taskloom scheduler --headless`) and real runs, whose programs take a few seconds to load (a
    block file slow to import). report (every 60 minutes, Misfire Skip) is scheduled: the service runs it as it first
    sees it, at T. While that run's program loads, you turn the service off (Proceed) and, 2.5 s after its program
    started, on again. That run does not begin (SCH-8); T follows Misfire once the service is on: less than 2 minutes
    late, it still runs, once, from a run's program started after you turned the service on, and nothing is told
    missed."""
    write(env.home / "blocks" / "slow_load.py", SLOW_LOAD)
    starts = record_process_starts(env)
    log = env.root / "service.log"
    service = headless(env, log)
    try:
        beat = env.home / "scheduler.heartbeat"
        assert wait_until(lambda: beat.exists() or service.poll() is not None, 60) and beat.exists(), \
            f"the scheduler service did not check:\n{printed(log)}"
        path = schedule(env.home, env.root, {"report": flow_yaml({**HOURLY, "misfire": "skip"})})["report"]
        assert wait_until(lambda: run_processes_of(starts, path), 30), \
            f"no run process was started for report:\n{printed(log)}"
        ((first, due),) = run_processes_of(starts, path)
        request_turn_off(Home(env.home), {})  # Proceed
        wait_until(lambda: time.time() >= first + 2.5, 5)
        set_paused(Home(env.home), False)  # you turn it on again (as the editor's button does while it runs)
        on_again = time.time()
        loading = statuses(env.home, "report") == []
        wait_until(lambda: statuses(env.home, "report") == ["success"], 40)
        time.sleep(2)  # (a second run for T would have begun by now)
        runs = [r for r in history(env.home) if r["flow"] == "report"]
        processes = run_processes_of(starts, path)
        said = told(log)
    finally:
        stop_service(env, service)
        end_runs(env.home)

    assert loading, f"not arranged: report's run had begun before you turned the service on again: {runs}"
    assert len(runs) == 1 and runs[0]["status"] == "success", \
        f"T ({due:%H:%M:%S}) did not run exactly once: {runs}\n{printed(log)}"
    after = [started for started, at in processes if at == due and started > on_again]
    assert after and runs[0]["started"].timestamp() >= min(after), (
        f"the run for T began from the program that was loading as you turned the service off and on again (no "
        f"program for T was started after you turned it on: {processes}):\n{printed(log)}")
    assert missed_said(said, "report") == [], f"told of missed runs: {said}"


@pytest.mark.invariant("SCH-4")
@pytest.mark.invariant("SCH-8")
def test_a_waiting_run_of_a_flow_you_let_finish_still_begins_while_the_service_turns_off(env, fake_runs):
    """Max concurrent runs 2. weekly (Overlap allow) has two Schedule blocks, each every 60 minutes (each runs as the
    service first sees it, at 07:00); blocker (every 60 minutes too) holds the second slot, so weekly's second run
    waits its turn. blocker's run ends; the service starts weekly's waiting run, and while its program loads you turn
    the service off: the dialog lists weekly (a run of it going), Let it finish; Proceed. Its waiting run still begins
    (SCH-4: Let it finish, its runs waiting their turn still run), and the service turns off once weekly's runs are
    done."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 2\n")
    paths = schedule(env.home, env.root, {"blocker": flow_yaml(HOURLY),
                                          "weekly": flow_yaml(HOURLY, HOURLY, overlap="allow")})
    said = []
    service = fake_runs.service(env.home, lambda title, message: said.append(f"{title}: {message}"))
    fake_runs.holding.update({"blocker", "weekly"})  # their runs go on until let go
    service.tick(clock("07:00:00"))
    assert sorted(r.flow for r in fake_runs.started) == ["blocker", "weekly"], \
        f"not arranged: weekly's second run did not wait for a slot: {said}"
    fake_runs.loading.add("weekly")
    fake_runs.holding.discard("blocker")  # blocker's run ends
    service.tick(clock("07:00:01"))  # weekly's waiting run starts: its program loads
    waiting = fake_runs.of("weekly")[1:]
    assert len(waiting) == 1, f"not arranged: weekly's waiting run did not start as the slot was free: {said}"
    request_turn_off(Home(env.home), {str(paths["weekly"]): "finish"})  # Proceed, Let it finish
    service.tick(clock("07:00:02"))
    began = has_loaded(fake_runs, waiting[0])
    fake_runs.holding.discard("weekly")  # weekly's runs finish
    for moment in ("07:00:03", "07:00:04", "07:00:05"):
        service.tick(clock(moment))

    assert began, f"weekly's waiting run (Let it finish) did not begin as the service turned off: told: {said}"
    assert service.off, "the service did not turn off once the runs you let finish were done"


# --- SCH-4: Stop now, then on again before the service looked, or with no service running -----------------------------

@pytest.mark.invariant("SCH-4")
def test_stop_now_then_on_again_before_the_service_has_looked_drops_the_flows_waiting_runs(env, fake_runs):
    """backlog (Catch up, every minute, Overlap queue) was first seen at 07:00:30 and ran then; its run goes on. At
    07:03:30 its runs for 07:01:30, 07:02:30 and 07:03:30 wait behind it. You turn the service off: the dialog lists
    backlog, you choose Stop now, Proceed; and you turn it on again at once, before the service has checked. Stop now
    holds: backlog's waiting runs are dropped, and never begin."""
    paths = schedule(env.home, env.root, {"backlog": flow_yaml({"every": "1 minute", "misfire": "catch_up"},
                                                               overlap="queue")})
    said = []
    service = fake_runs.service(env.home, lambda title, message: said.append(f"{title}: {message}"))
    fake_runs.holding.add("backlog")  # its run goes on until let go
    service.tick(clock("07:00:30"))
    service.tick(clock("07:03:30"))
    assert begins(fake_runs.of("backlog")) == utc([clock("07:00:30")]), f"not arranged: {said}"
    request_turn_off(Home(env.home), {str(paths["backlog"]): "stop"})  # Proceed, with Stop now for backlog
    set_paused(Home(env.home), False)  # on again, before the service has looked
    fake_runs.holding.discard("backlog")  # (its run going ends, so that nothing holds the waiting ones back)
    for moment in ("07:03:31", "07:03:32", "07:03:33", "07:03:40"):
        drain(service, clock(moment), fake_runs)

    assert begins(fake_runs.of("backlog")) == utc([clock("07:00:30")]), (
        f"you chose Stop now for backlog and turned the service on again before it looked, yet its waiting runs began: "
        f"runs began for {begins(fake_runs.of('backlog'))}; told: {said}")


@pytest.mark.invariant("SCH-4")
def test_stop_now_with_no_service_running_stops_the_flows_runs_and_drops_its_waiting_ones(win, tmp_path, monkeypatch):
    """Two flows have a run going (started by the real service, from the editor's button): nightly_load, whose second
    run waits behind its first (Overlap queue), and weekly_report. You click the button to turn the service off; while
    the dialog is open, the service crashes (it is killed). You choose Stop now for nightly_load (Let it finish for
    weekly_report) and click Proceed, with no service running. Stop now holds all the same (SCH-4): nightly_load's run
    going is stopped and recorded as cancelled, and its waiting run is dropped: it never begins, also once you turn the
    service on again. weekly_report's run goes on and finishes."""
    home = win.home.root
    schedule(home, tmp_path, {
        "nightly_load": flow_yaml(HOURLY, HOURLY, work=WAIT_A_MINUTE, overlap="queue"),
        "weekly_report": flow_yaml(HOURLY, work=hold_until("weekly_report"))})
    asked = []  # a plain Yes/No question instead of the dialog: answered No
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[1:3]) or QMessageBox.No))
    pump(lambda: False, 0.3)  # (the editor's own start-up checks run first)
    monkeypatch.setattr(win, "start_scheduler", types.MethodType(REAL_START_SCHEDULER, win))  # the real service
    answer = {"timer": QTimer()}
    try:
        win.toggle_scheduler()  # off: the button turns it on
        assert pump(lambda: going(home) == {"nightly_load", "weekly_report"}, 30), history(home)
        pump(lambda: False, 1.5)  # (a check later: nightly_load's second run waits its turn)
        service = int((home / "scheduler.pid").read_text(encoding="utf-8"))

        def crash() -> bool:  # (while the dialog is open)
            os.kill(service, getattr(signal, "SIGKILL", signal.SIGTERM))
            return wait_until(lambda: ended(service), 15)

        answer = answer_when_shown(TURN_OFF, NAMES, choose={"nightly_load": "Stop now"}, button="Proceed", wait=0.5,
                                   observe=crash)
        win.toggle_scheduler()  # on: the button turns it off
        pump(lambda: answer["done"], 15)
        assert answer["seen"] is not None and answer["closed"] == [] and answer["observed"], \
            f"not arranged: no dialog answered with the service crashed meanwhile: {answer}; asked: {asked}"

        stopped = pump(lambda: statuses(home, "nightly_load") == ["cancelled"], 20)
        recorded = statuses(home, "nightly_load")
        write(tmp_path / "weekly_report.go", "go")  # weekly_report's run can finish now
        finished = pump(lambda: statuses(home, "weekly_report") == ["success"], 30)
        win.toggle_scheduler()  # you turn the service on again
        pump(lambda: False, 6)  # (a waiting run kept would have begun by now)
        later = statuses(home, "nightly_load")
    finally:
        write(tmp_path / "weekly_report.go", "go")
        stop_the_service(home)
        end_runs(home)
        answer["timer"].stop()

    assert stopped, (f"Stop now with no service running as you clicked Proceed: nightly_load's run going was not "
                     f"stopped and recorded as cancelled: {recorded}")
    assert finished, f"weekly_report's run (Let it finish) did not finish: {history(home)}"
    assert later == ["cancelled"], (f"nightly_load's waiting run (Stop now) began once you turned the service on "
                                    f"again: {later}")
