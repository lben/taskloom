"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml), around a run's time:
a run waiting its turn that the service starts before its time does not begin if, before then, you turn the service off
or its schedule goes off (SCH-4, SCH-8), nor does a run started at its time that is still loading as you turn the
service off (SCH-4); a schedule turned off and on again while the service is off starts afresh
(SCH-10); with Overlap skip, a time is skipped only if a run of its flow is going at that time (SCH-11); a time less
than 2 minutes late still runs when the schedule's next time is a few seconds away (SCH-9); a mistake in the settings
never stops the keeper, and is told once, saying what the service does meanwhile (SCH-14); the list of scheduled flows
is never read half-written (SCH-6, SCH-7); and the README explains these cases (SCH-17).

What crosses processes at a run's time uses the real service (`taskloom scheduler --headless`) and real run processes,
in real time. Where a test needs chosen clock times, it drives the service's checks (Scheduler.tick) on that clock, with
each run's process replaced at the process boundary by a stand-in that claims its time as the real run does
(test_scheduler_new_promises.FakeRuns), and each service made as `taskloom scheduler` makes it: the Taskloom folder's
lock first, held while it lives (FakeRuns.service); turning a schedule off and on is done in the real editor window."""

import datetime as dt
import os
import re
import subprocess
import sys
import time

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sshserver import free_port  # noqa: E402
from taskloom.config import Home  # noqa: E402
from taskloom.scheduler import request_turn_off, set_paused  # noqa: E402
from tests.test_paths_editor_editing import field, select_only  # noqa: E402
from tests.test_paths_scheduler import keeper  # noqa: E402
from tests.test_scheduler_mistakes_and_counts import headless, printed, python_words  # noqa: E402
from tests.test_scheduler_mistakes_and_counts import told as service_said  # noqa: E402
from tests.test_interval_schedules import run_processes_of  # noqa: E402
from tests.test_scheduler_new_promises import (HOURLY, FakeRuns, begins, drain, end_runs, flow_yaml,  # noqa: E402
                                               history, hold_until, says, schedule, schedules_prose, statuses, utc,
                                               wait_until, write)
from tests.test_scheduler_start_at import record_process_starts, run_processes, stop_service  # noqa: E402
from tests.test_scheduler_start_at import wait_until as clock_reaches  # noqa: E402

TODAY = dt.date(2026, 1, 5)
YEARLY = {"cron": "0 2 1 1 *"}  # (its time never comes in a test)
SECONDS = dt.timedelta(seconds=1)
LATER = dt.timedelta(minutes=3)  # turned on again this long after a time: more than 2 minutes, so a missed time


@pytest.fixture
def fake_runs(monkeypatch) -> FakeRuns:
    """`taskloom run` processes replaced by stand-ins (see FakeRuns); the service made last with them stops at the
    end."""
    runs = FakeRuns()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


def clock(text: str) -> dt.datetime:
    """That time of day on TODAY (HH:MM or HH:MM:SS), on this PC's clock."""
    return dt.datetime.combine(TODAY, dt.time.fromisoformat(text))


def one_off(moment: dt.datetime, **config) -> dict:
    """A Schedule block's config for the one-off time `moment` (to the second, on this PC's clock)."""
    return {"at": f"{moment:%Y-%m-%d %H:%M:%S}", "misfire": "run_once", **config}


def runs_for(home, flow: str, moment: dt.datetime) -> list[dict]:
    """The runs of `flow` in history whose as-of time is `moment`."""
    return [r for r in history(home)
            if r["flow"] == flow and r["when"] and dt.datetime.fromisoformat(r["when"]) == moment]


def began_within_2_seconds(run: dict, moment: dt.datetime) -> bool:
    """SCH-5: recorded as started at `moment`, never before it (to a clock tick) and at most 2 s after it."""
    return -0.02 <= (run["started"] - moment).total_seconds() <= 2.0


def first_check(env, service, log, before: dt.datetime):
    """The service has checked (its heartbeat), early enough to set the test up before `before`."""
    beat = env.home / "scheduler.heartbeat"
    assert wait_until(lambda: beat.exists() or service.poll() is not None, 30) and beat.exists(), \
        f"the scheduler service did not check:\n{printed(log)}"
    assert dt.datetime.now() < before, "the service took too long to start: the test cannot be set up in time"


# --- SCH-4, SCH-8: a run from the waiting line, started before its time ---------------------------------------------

@pytest.mark.invariant("SCH-4")
@pytest.mark.invariant("SCH-8")
@pytest.mark.invariant("SCH-5")
@pytest.mark.parametrize("change", ["nothing", "the service turned off", "Active unticked"])
def test_a_run_from_the_waiting_line_started_before_its_time_begins_only_if_its_time_is_still_its_own(env, monkeypatch,
                                                                                                     change):
    """max_concurrent_runs 1. alpha's run (every 60 minutes: it runs as the service first sees it) holds the only slot
    when beta's one-off time T (Misfire Skip) comes within the service's early start, so beta's run waits its turn.
    alpha finishes about 4 s before T, and the service starts beta's run from the line, before T. Then, 1.2 s before T,
    you turn the service off (Proceed: with no runs going it only asked you to confirm), or you untick beta's Active
    and save (its only schedule: the editor takes the flow off scheduled.yaml), or nothing changes. Turned off or
    unticked, nothing is recorded for beta: its run does not begin (SCH-4: once you click Proceed nothing new starts;
    SCH-8). With nothing changed, beta's run begins at T, once (SCH-5).

    Turned off, beta had no run going, so the dialog did not list it, and SCH-4 says: "Flows not listed keep their
    waiting runs: they are saved and start when you turn the service back on." So when you turn the service on again,
    3 minutes after T (on a chosen clock from then on, its run's process stood in for), beta's saved run starts, once.
    It is not a missed time: with Skip, a time missed by more than 2 minutes would not run, and you would be told."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    starts = record_process_starts(env)
    due = (dt.datetime.now() + dt.timedelta(seconds=14)).replace(microsecond=0)
    paths = schedule(env.home, env.root, {"alpha": flow_yaml(HOURLY, work=hold_until("alpha")),
                                          "beta": flow_yaml(one_off(due, misfire="skip"))})
    log = env.root / "service.log"
    service = headless(env, log)
    try:
        first_check(env, service, log, before=due - 7 * SECONDS)
        assert wait_until(lambda: statuses(env.home, "alpha") == ["running"], 5), \
            f"alpha's run did not begin as the service first saw it:\n{printed(log)}"
        clock_reaches(due - 4.2 * SECONDS)  # beta's time came within the early start while alpha's run went: it waits
        assert statuses(env.home, "alpha") == ["running"], "alpha's run did not hold the slot until now"
        (env.root / "alpha.go").write_text("go", encoding="utf-8")  # alpha's run finishes: the slot is free
        clock_reaches(due - 1.2 * SECONDS)
        assert statuses(env.home, "alpha") == ["success"], "alpha's run did not finish in time: the test is not set up"
        if change == "the service turned off":
            request_turn_off(Home(env.home), {})  # Proceed, as the editor's button and the tray icon send it
        elif change == "Active unticked":  # saved, as the editor saves it
            write(paths["beta"], flow_yaml(one_off(due, misfire="skip", active=False)))
            Home(env.home).set_scheduled(paths["beta"], False)
        changed = dt.datetime.now()
        early = run_processes(starts, {"beta": paths["beta"]}, due, until=dt.datetime.now() + 0.2 * SECONDS)
        clock_reaches(due + 5 * SECONDS)
        beta = [r for r in history(env.home) if r["flow"] == "beta"]
    finally:
        stop_service(env, service)
        end_runs(env.home)

    assert changed < due - 0.5 * SECONDS, "changed too late to tell"
    if change == "nothing":
        assert len(beta) == 1 and beta[0]["status"] == "success" and began_within_2_seconds(beta[0], due), (
            f"beta's run, from the waiting line, did not begin once at {due:%H:%M:%S}: {beta}\n{printed(log)}")
    else:
        started = (f"its run's process was started {due.timestamp() - early['beta']:.1f} s before its time"
                   if early.get("beta", changed.timestamp()) < changed.timestamp()
                   else "no run process was started for it before then")
        recorded = [(r["status"], f"started {r['started']:%H:%M:%S.%f}") for r in beta]
        assert beta == [], (f"{change} 1.2 s before {due:%H:%M:%S} ({started}), yet beta's run began and was "
                            f"recorded: {recorded}\n{printed(log)}")
    if change == "the service turned off":
        runs = FakeRuns()  # (from here on, on a chosen clock: the run processes are stood in for)
        monkeypatch.setattr(subprocess, "Popen", runs.popen)
        set_paused(Home(env.home), False)  # you turn the service on again, 3 minutes after T
        told = []
        try:
            drain(runs.service(env.home, lambda title, message: told.append(f"{title}: {message}")), due + LATER, runs)
        finally:
            runs.end()
        assert begins(runs.of("beta")) == utc([due]), (
            f"turned on again {LATER} after {due:%H:%M:%S}, beta's saved waiting run did not start once: runs began "
            f"for {[r.start_at for r in runs.of('beta') if r.not_begun is None]}; told: {told}")
        assert [t for t in told if re.search(r"missed|skipped", t, re.IGNORECASE)] == [], told


@pytest.mark.invariant("SCH-4")
@pytest.mark.invariant("SCH-8")
@pytest.mark.parametrize("turned_off", [False, True], ids=["nothing changes", "the service turned off"])
def test_a_run_waiting_for_its_flows_run_and_started_before_its_time_does_not_begin_once_the_service_is_off(
        env, fake_runs, turned_off):
    """Overlap queue: report has one-off times 07:00 and 07:02 (two Schedule blocks; 07:02's Misfire is Skip). At
    07:01:56, 07:02 comes within the service's early start while the 07:00 run is still going: it waits for that run.
    The 07:00 run finishes at 07:01:57, and the service starts the 07:02 run from the line, before its time. You turn
    the service off (Proceed: no runs going, so report is not listed) at once: the 07:02 run does not begin (SCH-4,
    SCH-8), and it stays a saved waiting run ("Flows not listed keep their waiting runs: they are saved and start when
    you turn the service back on"): turned on again at 07:05, it starts, once, and is not a missed time (with Skip, a
    time missed by more than 2 minutes would not run, and you would be told). With nothing changed, it begins at
    07:02."""
    schedule(env.home, env.root, {"report": flow_yaml(one_off(clock("07:00")), one_off(clock("07:02"), misfire="skip"),
                                                      overlap="queue")})
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    fake_runs.holding.add("report")  # its runs go on until let go
    for moment in ("06:59:30", "06:59:57", "07:00:00", "07:01:56"):
        service.tick(clock(moment))
    first = fake_runs.of("report")[0]
    assert begins([first]) == utc([clock("07:00")]) and first.returncode is None, \
        f"the 07:00 run did not begin and go on: {told}"
    fake_runs.holding.discard("report")  # the 07:00 run finishes
    service.tick(clock("07:01:57"))
    if turned_off:
        request_turn_off(Home(env.home), {})
    for moment in ("07:01:58", "07:02:00", "07:02:01", "07:02:05"):
        service.tick(clock(moment))

    ran = ["07:00"] if turned_off else ["07:00", "07:02"]
    assert begins(fake_runs.of("report")) == utc([clock(t) for t in ran]), (
        f"{'turned off at 07:01:57' if turned_off else 'nothing changed'}: the runs that began are for "
        f"{[r.start_at for r in fake_runs.of('report') if r.not_begun is None]}, not {ran}; told: {told}")
    if turned_off:
        assert service.off, "the service did not turn off"
        set_paused(Home(env.home), False)  # you turn the service on again
        back = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
        drain(back, clock("07:05"), fake_runs)
        assert begins(fake_runs.of("report")) == utc([clock("07:00"), clock("07:02")]), (
            f"turned on again at 07:05, the saved 07:02 run did not start once: the runs that began are for "
            f"{[r.start_at for r in fake_runs.of('report') if r.not_begun is None]}; told: {told}")
        assert [t for t in told if re.search(r"missed|skipped", t, re.IGNORECASE)] == [], told


# A block file in the Taskloom folder whose libraries are slow to import: each run's process takes this long to load.
SLOW_LOAD = "import time\n\ntime.sleep(4)\n"


@pytest.mark.invariant("SCH-4")
@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("misfire", ["skip", "run_once"], ids=["Skip", "Run once"])
def test_a_run_started_at_its_time_and_still_loading_when_you_turn_the_service_off_does_not_begin(env, monkeypatch,
                                                                                                   misfire):
    """A run whose process the service starts at its time, not before it: report (every 60 minutes, Misfire
    `misfire`) is scheduled while the service is on, so its first time is when the service sees it, and its run's
    process starts then. That process takes a few seconds to load (a block file slow to import). While it loads, you
    turn the service off (Proceed: no run is going, so it only asked you to confirm). SCH-4: once you click Proceed,
    nothing new starts: the run does not begin, and nothing is recorded for it. Its time is not lost: turned on again 3
    minutes later (on a chosen clock from then on, its run's process stood in for), that time is a missed one, as its
    Misfire says (SCH-9): Skip runs none and tells you 1 was missed; Run once runs it, once."""
    write(env.home / "blocks" / "slow_load.py", SLOW_LOAD)
    starts = record_process_starts(env)
    log = env.root / "service.log"
    service = headless(env, log)
    try:
        beat = env.home / "scheduler.heartbeat"
        assert wait_until(lambda: beat.exists() or service.poll() is not None, 60) and beat.exists(), \
            f"the scheduler service did not check:\n{printed(log)}"
        path = schedule(env.home, env.root, {"report": flow_yaml({**HOURLY, "misfire": misfire})})["report"]
        assert wait_until(lambda: run_processes_of(starts, path), 30), \
            f"no run process was started for report:\n{printed(log)}"
        ((started, due),) = run_processes_of(starts, path)
        loading = statuses(env.home, "report") == []  # (its process has not begun the run yet)
        request_turn_off(Home(env.home), {})  # Proceed
        clicked = time.time()
        wait_until(lambda: service.poll() is not None, 30)  # (it turns off once that run's process has ended)
        recorded = [(r["status"], f"started {r['started']:%H:%M:%S.%f}") for r in history(env.home)]
    finally:
        stop_service(env, service)
        end_runs(env.home)

    assert started >= due.timestamp(), f"report's run process was started before its time {due:%H:%M:%S}, not at it"
    assert loading and clicked - started < 3, \
        f"you clicked Proceed {clicked - started:.1f} s after the run's process started: not while it was loading"
    assert recorded == [], (
        f"you clicked Proceed {clicked - started:.1f} s after report's run for {due:%H:%M:%S} was started, at its "
        f"time, while it was still loading; yet it began and was recorded: {recorded}\n{printed(log)}")

    runs = FakeRuns()  # (from here on, on a chosen clock: the run processes are stood in for)
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    set_paused(Home(env.home), False)  # you turn the service on again, 3 minutes after that time
    told = []
    try:
        drain(runs.service(env.home, lambda title, message: told.append(f"{title}: {message}")), due + LATER, runs)
    finally:
        runs.end()
    ran, about = begins(runs.of("report")), [t for t in told if "report" in t]
    if misfire == "skip":
        assert ran == [] and [t for t in about if says(t, 1) and re.search(r"missed", t, re.IGNORECASE)], (
            f"turned on again {LATER} after {due:%H:%M:%S}, with Skip: runs began for {ran}, and you were told "
            f"{about}, not that 1 was missed")
    else:
        assert ran == utc([due]) and about == [], (
            f"turned on again {LATER} after {due:%H:%M:%S}, with Run once: runs began for {ran}, not once for that "
            f"time; told: {about}")


# --- SCH-10: a schedule turned off and on again while the service is off ---------------------------------------------

@pytest.mark.invariant("SCH-10")
@pytest.mark.invariant("SCH-7")
@pytest.mark.parametrize("misfire, other", [("catch_up", None), ("run_once", None), ("skip", None),
                                            ("catch_up", YEARLY)],
                         ids=["Catch up", "Run once", "Skip", "Catch up, the flow's other schedule still on"])
def test_a_schedule_turned_off_and_on_again_while_the_service_is_off_starts_afresh(win, tmp_path, fake_runs, misfire,
                                                                                  other):
    """minutely (every 1 minute) was first seen by the service 5 min 30 s ago, and ran then; you turned the service
    off. In the editor you untick its Schedule block's Active and save, then tick it again and save (with no other
    schedule the flow is taken off scheduled.yaml and put back; with one, it stays listed). You turn the service on: the
    schedule starts afresh, as a new one: its first run is when the service first sees it again, and the 5 times from
    before then neither run nor count as missed."""
    home = win.home
    path = schedule(home.root, tmp_path, {"minutely": flow_yaml({"every": "1 minute", "misfire": misfire},
                                                                *([other] if other else []))})["minutely"]
    told = []
    seen = (dt.datetime.now() - dt.timedelta(minutes=5, seconds=30)).replace(microsecond=0)
    before = fake_runs.service(home.root, lambda title, message: told.append(f"{title}: {message}"))
    drain(before, seen, fake_runs)  # the service first sees it: its first run is then (it begins, and ends)
    request_turn_off(home, {})  # you turn the service off (no runs going: it only asked to confirm)
    before.tick(seen + SECONDS)
    assert before.off and begins(fake_runs.of("minutely")) == utc([seen]), told
    fake_runs.stop()  # (off, it has ended)

    tab = win.open_flow(path)
    for active in (False, True):  # Active unticked and saved, then ticked again and saved
        select_only(tab, "s1")
        field(win, "Active").setChecked(active)
        assert win.save(tab)
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["blocks"]["s1"]["config"].get("active", True) is active
    set_paused(home, False)  # you turn the service on
    on = dt.datetime.now().replace(microsecond=0)
    drain(fake_runs.service(home.root, lambda title, message: told.append(f"{title}: {message}")), on, fake_runs)
    fake_runs.stop()

    ran = begins(fake_runs.of("minutely"))[1:]
    assert ran == utc([on]), (f"turned off and on again while the service was off, minutely did not start afresh: "
                              f"once the service was on, runs began for {ran}, not only {utc([on])}; told: {told}")
    assert [t for t in told if re.search(r"missed", t, re.IGNORECASE)] == [], told


# --- SCH-11: Overlap skip, judged at the time --------------------------------------------------------------------

@pytest.mark.invariant("SCH-11")
@pytest.mark.invariant("SCH-5")
def test_with_overlap_skip_a_time_is_skipped_only_if_a_run_of_its_flow_is_going_at_that_time(env):
    """Two flows with Overlap skip, each with the one-off times T1 and T2 = T1 + 9 s; their T1 runs go on until let go.
    early_end's T1 run is let finish 3 s before T2, late_end's 3 s after T2. At T2 a run of late_end is going: its T2 is
    skipped, and you are told. No run of early_end is going at T2: its T2 run begins then (at most 2 s late, SCH-5),
    and nothing is told about it."""
    t1 = (dt.datetime.now() + dt.timedelta(seconds=12)).replace(microsecond=0)
    t2 = t1 + 9 * SECONDS
    schedule(env.home, env.root, {name: flow_yaml(one_off(t1), one_off(t2), work=hold_until(name), overlap="skip")
                                  for name in ("early_end", "late_end")})
    log = env.root / "service.log"
    service = headless(env, log)
    try:
        first_check(env, service, log, before=t1 - 6 * SECONDS)
        clock_reaches(t2 - 3 * SECONDS)
        assert [statuses(env.home, name) for name in ("early_end", "late_end")] == [["running"]] * 2, \
            f"the T1 runs did not begin and go on:\n{printed(log)}"
        (env.root / "early_end.go").write_text("go", encoding="utf-8")
        clock_reaches(t2 + 3 * SECONDS)
        assert statuses(env.home, "late_end") == ["running"], "late_end's T1 run was not going at T2"
        (env.root / "late_end.go").write_text("go", encoding="utf-8")
        clock_reaches(t2 + 6 * SECONDS)
        early_end, late_end = runs_for(env.home, "early_end", t2), runs_for(env.home, "late_end", t2)
        (t1_run,) = runs_for(env.home, "early_end", t1)
        said = service_said(log)
    finally:
        stop_service(env, service)
        end_runs(env.home)

    assert t1_run["finished"] is not None and t1_run["finished"] < t2 - 0.5 * SECONDS, \
        f"early_end's T1 run had not finished before T2: {t1_run}"
    skipped = {name: [m for m in said if f"{name}:" in m and re.search(r"skip", m, re.IGNORECASE)]
               for name in ("early_end", "late_end")}
    ahead = (t2 - t1_run["finished"]).total_seconds()
    assert len(early_end) == 1 and early_end[0]["status"] == "success" and skipped["early_end"] == [], (
        f"early_end's T1 run finished {ahead:.1f} s before T2 {t2:%H:%M:%S}, yet its T2 run did not run: {early_end}; "
        f"told: {skipped['early_end']}")
    assert began_within_2_seconds(early_end[0], t2), f"early_end's T2 run did not begin at {t2:%H:%M:%S}: {early_end}"
    assert late_end == [] and skipped["late_end"], \
        f"late_end's T2, with its T1 run going then, was not skipped and told: {late_end}; told: {said}"


# --- SCH-9: a time less than 2 minutes late, with the next time a few seconds away -----------------------------------

@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("misfire", ["skip", "run_once"], ids=["Skip", "Run once"])
def test_a_time_less_than_2_minutes_late_still_runs_when_the_next_time_is_a_few_seconds_away(env, fake_runs,
                                                                                           misfire):
    """An every-minute cron line. The service saw it at 10:00:30 and was then off; it is back at 10:01:56, when 10:01
    is 56 s late (less than 2 minutes) and 10:02 is 4 s away (its run's process starts early, to begin on time). Skip:
    a time less than 2 minutes late still runs, so 10:01 runs and none is told as missed. Run once: one run, for the
    latest missed time, 10:01. Either way 10:02 runs at its time too (it is not missed)."""
    schedule(env.home, env.root, {"minutely": flow_yaml({"cron": "* * * * *", "misfire": misfire})})
    told = []
    fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}")).tick(clock("10:00:30"))
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))  # back on
    for moment in ("10:01:56", "10:01:57", "10:01:59", "10:02:00", "10:02:01", "10:02:03"):
        service.tick(clock(moment))

    assert begins(fake_runs.of("minutely")) == utc([clock("10:01"), clock("10:02")]), (
        f"back at 10:01:56 ({misfire}), the runs that began are for "
        f"{[r.start_at for r in fake_runs.of('minutely') if r.not_begun is None]}, not 10:01 (56 s late) and 10:02; "
        f"told: {told}")
    assert [t for t in told if re.search(r"missed|skipped", t, re.IGNORECASE)] == [], told


# --- SCH-14: a mistake in the settings ------------------------------------------------------------------------------

@pytest.mark.invariant("SCH-14")
def test_a_keeper_setting_that_is_not_a_list_is_told_as_a_mistake_in_the_settings_and_the_keeper_goes_on(env,
                                                                                                         fake_runs):
    """settings.yaml says `keeper: yes`. The keeper's check (its thread repeats it every 5 minutes) tells you, in plain
    words, that the keeper setting in the settings is a mistake, and does not end: once the setting is put right (as
    Tools → Settings writes it), its next check checks the server listed."""
    write(env.home / "connections.yaml", f"appserver: {{kind: ssh, host: 127.0.0.1, port: {free_port()}, "
                                         "username: tester, password: not-needed}\n")
    write(env.home / "settings.yaml", "keeper: yes\n")
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    try:
        service.check_servers()
    except Exception as e:
        pytest.fail(f"the keeper's check stops, and with it the keeper, for good: {type(e).__name__}: {e}")
    service.tick(clock("10:00:00"))  # (the service's check tells you what the keeper found)
    about = [t for t in told if "keeper" in t.lower()]
    assert len(about) == 1 and "settings" in about[0].lower(), f"not told once of the mistaken keeper setting: {told}"
    assert python_words(about[0]) == [], f"not in plain words: {about[0]}"

    keeper(env)  # put right
    told.clear()
    service.check_servers()
    service.tick(clock("10:05:00"))
    assert [t for t in told if "appserver" in t], f"once put right, the keeper did not check the server: {told}"


def settings_notices(told: list[str]) -> list[str]:
    return [t for t in told if "settings" in t.lower()]


def says_runs_at_once_not_applied(text: str) -> bool:
    """Whether the text says that Scheduled runs at once (max_concurrent_runs) is not applied meanwhile."""
    named = re.search(r"scheduled runs at once|max_concurrent_runs", text, re.IGNORECASE)
    not_applied = re.search(r"\b(not|isn't|aren't)\b[^.]*\b(applied|apply|used|in effect|enforced)\b|\bignored\b"
                            r"|\bunlimited\b|\bno limit\b", text, re.IGNORECASE)
    return bool(named and not_applied)


@pytest.mark.invariant("SCH-14")
def test_a_mistake_in_the_settings_is_told_once_until_settings_yaml_changes_saying_runs_at_once_is_not_applied(
        env, fake_runs):
    """settings.yaml says max_concurrent_runs: 1 and has a key Taskloom does not know (keep_run_for, misspelt). The
    service goes on: as it starts (the keeper's first check, then its checks), three flows each start a run, all at
    once. You are told once, not at each check or run start: the notice names settings.yaml and says that Scheduled
    runs at once is not applied until it is put right. Put right, the limit applies again (a fourth flow's run waits
    for the three, then starts), and nothing more is told. Changed to another mistake, you are told once more."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\nkeep_run_for: 30 days\n")
    schedule(env.home, env.root, {name: flow_yaml(HOURLY) for name in ("first", "second", "third")})
    fake_runs.holding.update({"first", "second", "third", "fourth", "fifth"})  # their runs go on until let go
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    service.check_servers()  # (the keeper's first check, as the service starts)
    for moment in ("10:00:00", "10:00:01", "10:00:02"):
        service.tick(clock(moment))

    going = sorted(r.flow for r in fake_runs.started if r.returncode is None)
    assert going == ["first", "second", "third"], f"the service did not go on: runs going {going}; told: {told}"
    notices = settings_notices(told)
    assert len(notices) == 1, f"not told once of the mistake in settings.yaml, over 3 checks and 3 run starts: {told}"
    assert "settings.yaml" in notices[0] and says_runs_at_once_not_applied(notices[0]), (
        f"the notice does not name settings.yaml and say that Scheduled runs at once is not applied until it is put "
        f"right: {notices[0]}")

    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")  # put right
    schedule(env.home, env.root, {"fourth": flow_yaml(HOURLY)})
    service.tick(clock("10:00:03"))
    service.tick(clock("10:00:04"))
    assert fake_runs.of("fourth") == [], "put right, Scheduled runs at once 1 is not applied: a fourth run started"
    fake_runs.holding -= {"first", "second", "third"}
    service.tick(clock("10:00:05"))
    service.tick(clock("10:00:06"))
    assert [r.alongside for r in fake_runs.of("fourth")] == [[]], "the fourth flow's run did not start on its own"
    assert settings_notices(told) == notices, f"told again with settings.yaml put right: {told}"

    write(env.home / "settings.yaml", "max_concurrent_runs: lots\n")  # another mistake
    schedule(env.home, env.root, {"fifth": flow_yaml(HOURLY)})
    for moment in ("10:00:07", "10:00:08", "10:00:09"):
        service.tick(clock(moment))
    again = settings_notices(told)[1:]
    assert len(again) == 1 and says_runs_at_once_not_applied(again[0]), \
        f"settings.yaml changed to another mistake: not told once, saying the limit is not applied: {told}"


# --- SCH-6, SCH-7: the list of scheduled flows, saved while the service checks ---------------------------------------

SAVES = """
import sys
from pathlib import Path

from taskloom.config import Home

home, flow, times = Home(Path(sys.argv[1])), Path(sys.argv[2]), int(sys.argv[3])
for i in range(times):  # as the editor saves a flow: scheduled, then not
    home.set_scheduled(flow, i % 2 == 0)
"""


@pytest.mark.invariant("SCH-6")
@pytest.mark.invariant("SCH-7")
def test_the_service_never_reads_the_list_of_scheduled_flows_half_written(env, fake_runs):
    """hourly (every 1 hour) is listed in scheduled.yaml all along, and ran when the service first saw it. Meanwhile
    another process saves the list 4000 times, as the editor does when you save flows (here it schedules another flow
    and takes it off again), while the service checks over and over, as its loop does. hourly runs no second time: it
    keeps counting from when the service first saw it (SCH-7), which a check that read the list half-written (empty)
    would forget, starting it again "now"; and a time runs once (SCH-6). Nothing is told: the list is never seen
    with a mistake in it."""
    paths = schedule(env.home, env.root, {"hourly": flow_yaml({"every": "1 hour"}), "other": flow_yaml(YEARLY)})
    Home(env.home).set_scheduled(paths["other"], False)
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    service.tick(dt.datetime.now())
    times, checks = 4000, 0
    saver = subprocess.Popen([sys.executable, "-c", SAVES, str(env.home), str(paths["other"]), str(times)],
                             env=env.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             text=True)
    try:
        while saver.poll() is None:
            now = dt.datetime.now()
            service.tick(now)
            service.seconds_to_next_check(now)
            checks += 1
    finally:
        if saver.poll() is None:
            saver.kill()
        _, error = saver.communicate(timeout=30)
    service.tick(dt.datetime.now())

    assert saver.returncode == 0, error
    hourly = [r.start_at for r in fake_runs.of("hourly")]
    assert len(hourly) == 1 and told == [], (
        f"over {checks} checks while the list was saved {times} times, hourly began {len(hourly)} times "
        f"({hourly[:3]}...), and the service told {len(told)} things ({told[:2]}...)")


# --- SCH-17: the README on one service per folder and on mistakes ---------------------------------------------------

@pytest.mark.invariant("SCH-17")
@pytest.mark.parametrize("case, patterns", [
    ("one service per Taskloom folder: starting another does nothing (SCH-1)",
     [r"taskloom folder", r"\bonly one\b|\bone\b[^.]*\bper\b|\bone scheduler service\b", r"\banother\b|\bsecond\b",
      r"\bdoes nothing\b|\bnothing happens\b|\bno effect\b|\bdoes not start\b|\bdoesn't start\b"]),
    ("an entry in scheduled.yaml that is not a flow's path is left out, and the rest run (SCH-14)",
     [r"scheduled\.yaml", r"\bnot a flow|\bisn't a flow", r"\bleft out\b|\bskipped\b|\bignored\b",
      r"\b(other|rest)\b[^.]*\bruns?\b"]),
    ("a list that cannot be read starts no scheduled flow until it is put right (SCH-14)",
     [r"scheduled\.yaml|list of scheduled flows", r"cannot be read|can't be read|unreadable|cannot read",
      r"\bno scheduled flow\b|\bnothing\b|\bnone of\b", r"put right|fixed|corrected"]),
    ("a mistake in the settings is told, and Scheduled runs at once is not applied meanwhile (SCH-14)",
     [r"settings", r"\bmistake|\bwrong\b", r"scheduled runs at once",
      r"\b(not|isn't)\b[^.]*\b(applied|used|in effect|enforced)\b|\bignored\b|\bunlimited\b"]),
    ("a mail server that doesn't answer is told, and the service goes on (SCH-14)",
     [r"mail server|smtp", r"(doesn't|does not|never|not) answer|unreachable|cannot be reached|can't be reached",
      r"\btold\b|notif|message", r"goes on|keeps (running|going|checking)|continues|holds nothing up"]),
], ids=["one service per folder", "an entry not a flow", "a list that cannot be read", "a mistake in the settings",
        "a mail server that does not answer"])
def test_the_readme_explains_one_service_per_folder_and_what_a_mistake_does(case, patterns):
    """README.md's Schedules section (also the editor's guide) explains, in one paragraph each, these cases where the
    scheduler might surprise you, as the user asked that edge cases be clearly documented."""
    explained = [p for p in schedules_prose() if all(re.search(pattern, p) for pattern in patterns)]

    assert explained, f"no paragraph of the README's Schedules section explains {case} (looked for {patterns})"
