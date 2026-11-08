"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml), around a run's time:
a run waiting its turn starts in its turn, is dropped if its schedule goes off, and waits, saved, while you turn the
service off (SCH-4, SCH-10, SCH-12); a run whose program is still loading as you turn the service off does not begin,
whether it was started at its time or from the line (SCH-4, SCH-8); a schedule turned off and on again while the
service is off starts afresh (SCH-10); with Overlap skip, a time is skipped only if a run of its flow is going at that
time (SCH-11); a time less than 2 minutes late still runs when the schedule's next time is a few seconds away (SCH-9);
a mistake in the settings never stops the keeper, and is told once, saying what the service does meanwhile (SCH-14);
the list of scheduled flows is never read half-written (SCH-6, SCH-7); and the README explains these cases (SCH-17).

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
from tests.test_scheduler_start_at import record_process_starts, stop_service  # noqa: E402
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


def began_within_5_seconds(run: dict, moment: dt.datetime) -> bool:
    """SCH-5: recorded as started at `moment`, never before it (to a clock tick) and at most 5 s after it."""
    return -0.02 <= (run["started"] - moment).total_seconds() <= 5.0


def first_check(env, service, log, before: dt.datetime):
    """The service has checked (its heartbeat), early enough to set the test up before `before`."""
    beat = env.home / "scheduler.heartbeat"
    assert wait_until(lambda: beat.exists() or service.poll() is not None, 30) and beat.exists(), \
        f"the scheduler service did not check:\n{printed(log)}"
    assert dt.datetime.now() < before, "the service took too long to start: the test cannot be set up in time"


# --- SCH-4, SCH-8, SCH-10, SCH-12: a run waiting its turn, and the service or its schedule turned off -----------------

@pytest.mark.invariant("SCH-4")
@pytest.mark.invariant("SCH-10")
@pytest.mark.invariant("SCH-12")
@pytest.mark.parametrize("change", ["nothing", "the service turned off", "Active unticked"])
def test_a_run_waiting_for_a_free_slot_starts_in_its_turn_unless_its_schedule_goes_off_and_waits_while_off(
        env, fake_runs, change):
    """max_concurrent_runs 1. alpha's run (every 60 minutes: it runs as the service first sees it, at 07:00:00) holds
    the only slot when beta's one-off time 07:00:10 (Misfire Skip) comes, so beta's run waits its turn (SCH-12). At
    07:00:11 you turn the service off (Proceed: the dialog lists alpha, whose run is going, with Let it finish, the
    default; beta has no run going and is not listed), or you untick beta's Active and save (its only schedule: the
    editor takes the flow off scheduled.yaml), or nothing changes. alpha's run then finishes at 07:00:12.

    Nothing changed: beta's run starts once the slot is free, once. Active unticked: beta's waiting run is dropped (it
    would not run as it was) and nothing is recorded for it. Turned off: once you click Proceed nothing new starts, so
    beta's run does not start, and the service turns off once alpha's run is done; but beta is a flow not listed, and
    SCH-4 says: "Flows not listed keep their waiting runs: they are saved and start when you turn the service back
    on". Turned on again 3 minutes later, beta's saved run starts, once, and it is not a missed time: with Skip, a time
    missed by more than 2 minutes would not run, and you would be told."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    due = clock("07:00:10")
    paths = schedule(env.home, env.root, {"alpha": flow_yaml(HOURLY), "beta": flow_yaml(one_off(due, misfire="skip"))})
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    fake_runs.holding.add("alpha")  # its run goes on until let go
    for moment in ("07:00:00", "07:00:05", "07:00:10"):
        service.tick(clock(moment))
    assert [r.flow for r in fake_runs.started] == ["alpha"], f"not arranged: beta's run did not wait its turn: {told}"
    if change == "the service turned off":
        request_turn_off(Home(env.home), {str(paths["alpha"]): "finish"})  # Proceed, as the dialog sends it
    elif change == "Active unticked":  # saved, as the editor saves it
        write(paths["beta"], flow_yaml(one_off(due, misfire="skip", active=False)))
        Home(env.home).set_scheduled(paths["beta"], False)
    service.tick(clock("07:00:11"))
    fake_runs.holding.discard("alpha")  # alpha's run finishes: the slot is free
    for moment in ("07:00:12", "07:00:13", "07:00:14", "07:00:20"):
        service.tick(clock(moment))

    beta = begins(fake_runs.of("beta"))
    if change == "nothing":
        assert beta == utc([due]), f"beta's waiting run did not start once when the slot was free: {beta}; {told}"
        return
    assert beta == [], f"{change} at 07:00:11, yet beta's waiting run began: {beta}; told: {told}"
    if change == "Active unticked":
        return
    assert service.off, "the service did not turn off once the run you let finish was done"
    set_paused(Home(env.home), False)  # you turn the service on again, 3 minutes after beta's time
    drain(fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}")), due + LATER,
          fake_runs)
    assert begins(fake_runs.of("beta")) == utc([due]), (
        f"turned on again {LATER} after {due:%H:%M:%S}, beta's saved waiting run did not start once: runs began for "
        f"{begins(fake_runs.of('beta'))}; told: {told}")
    assert [t for t in told if re.search(r"missed|skipped", t, re.IGNORECASE)] == [], told


@pytest.mark.invariant("SCH-4")
@pytest.mark.invariant("SCH-8")
@pytest.mark.parametrize("turned_off", [False, True], ids=["nothing changes", "the service turned off"])
def test_a_waiting_run_still_loading_when_you_turn_the_service_off_does_not_begin_and_starts_when_it_is_on_again(
        env, fake_runs, turned_off):
    """Overlap queue: report has one-off times 07:00 and 07:02 (two Schedule blocks; 07:02's Misfire is Skip). 07:02
    comes while the 07:00 run is still going: it waits for that run. The 07:00 run finishes just after 07:02, and the
    service starts the 07:02 run from the line; its program is still loading when you turn the service off (Proceed:
    no run is going, so report is not listed). It does not begin (SCH-4: once you click Proceed nothing new starts;
    SCH-8), and it stays a saved waiting run ("Flows not listed keep their waiting runs: they are saved and start when
    you turn the service back on"): turned on again at 07:05, it starts, once, and is not a missed time (with Skip, a
    time missed by more than 2 minutes would not run, and you would be told). With nothing changed, it begins then."""
    schedule(env.home, env.root, {"report": flow_yaml(one_off(clock("07:00")), one_off(clock("07:02"), misfire="skip"),
                                                      overlap="queue")})
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    fake_runs.holding.add("report")  # its runs go on until let go
    for moment in ("06:59:30", "07:00:00", "07:01:56", "07:02:00"):
        service.tick(clock(moment))
    first = fake_runs.of("report")[0]
    assert begins([first]) == utc([clock("07:00")]) and first.returncode is None and len(fake_runs.of("report")) == 1, \
        f"the 07:00 run did not begin and go on, with 07:02 waiting for it: {told}"
    fake_runs.holding.discard("report")  # the 07:00 run finishes
    service.tick(clock("07:02:01"))  # (the 07:02 run starts from the line: its program loads until the next check)
    assert len(fake_runs.of("report")) == 2, f"not arranged: the 07:02 run was not started from the line: {told}"
    if turned_off:
        request_turn_off(Home(env.home), {})
    for moment in ("07:02:02", "07:02:03", "07:02:05"):
        service.tick(clock(moment))

    ran = ["07:00"] if turned_off else ["07:00", "07:02"]
    assert begins(fake_runs.of("report")) == utc([clock(t) for t in ran]), (
        f"{'turned off at 07:02:01' if turned_off else 'nothing changed'}: the runs that began are for "
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
    skipped, and you are told. No run of early_end is going at T2: its T2 run begins then (at most 5 s late, SCH-5),
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
    assert began_within_5_seconds(early_end[0], t2), f"early_end's T2 run did not begin at {t2:%H:%M:%S}: {early_end}"
    assert late_end == [] and skipped["late_end"], \
        f"late_end's T2, with its T1 run going then, was not skipped and told: {late_end}; told: {said}"


# --- SCH-9: a time less than 2 minutes late, with the next time a few seconds away -----------------------------------

@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("misfire", ["skip", "run_once"], ids=["Skip", "Run once"])
def test_a_time_less_than_2_minutes_late_still_runs_when_the_next_time_is_a_few_seconds_away(env, fake_runs,
                                                                                           misfire):
    """An every-minute cron line. The service saw it at 10:00:30 and was then off; it is back at 10:01:56, when 10:01
    is 56 s late (less than 2 minutes) and 10:02 is 4 s away. Skip:
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
    ("a schedule on the clock saved or turned on while the service is off: its times after the save that the service "
     "did not see come follow Misfire, while Every N counts from when the service first sees it (SCH-7, SCH-9, SCH-10)",
     [r"\bservice (?:is|was) off\b", r"\b(?:after|since) (?:you )?(?:sav|switch|turn|tick)\w*",
      r"\bmisfire\b|\bmissed\b", r"\bevery n\b|\bevery few\b", r"\bfirst (?:sees|saw)\b|\bpicks? it up\b"]),
    ("a run you start yourself while a scheduled run of the flow is still loading: the scheduled run follows Overlap "
     "(SCH-11)",
     [r"\byourself\b|\bby hand\b", r"\bload", r"\boverlap\b", r"\bskip", r"\bqueue|\bwaits?\b"]),
    ("turned off and on again while a run's program is loading: it does not begin, and its time follows Misfire "
     "(SCH-8, SCH-9)",
     [r"\bturn\w* (?:the service |it )?off\b",
      r"\bload\w*\b[^.]*\b(?:on again|back on)\b|\b(?:on again|back on)\b[^.]*\bload",
      r"\bdoes not begin\b|\bdoesn't begin\b|\bnot begin\b|\bnever begins?\b", r"\bmisfire\b|\bmissed\b"]),
    ("Stop now holds when you turn the service on again before it has looked, and when no service is running as you "
     "click Proceed: the flow's waiting runs are dropped, its runs going are stopped and recorded as cancelled (SCH-4)",
     [r"\bstop now\b", r"\bno (?:scheduler )?service (?:is )?(?:on|running)\b|\bcrash\w*|\bnot running\b",
      r"\bwaiting runs?\b", r"\bdropped\b|\bnever (?:begin|start|run)s?\b", r"\bcancelled\b"]),
], ids=["one service per folder", "an entry not a flow", "a list that cannot be read", "a mistake in the settings",
        "a mail server that does not answer", "a schedule saved while the service is off",
        "a run by hand while a scheduled run loads", "turned off and on while a run loads",
        "Stop now, then on again or with no service running"])
def test_the_readme_explains_one_service_per_folder_and_what_a_mistake_does(case, patterns):
    """README.md's Schedules section (also the editor's guide) explains, in one paragraph each, these cases where the
    scheduler might surprise you, as the user asked that edge cases be clearly documented."""
    explained = [p for p in schedules_prose() if all(re.search(pattern, p) for pattern in patterns)]

    assert explained, f"no paragraph of the README's Schedules section explains {case} (looked for {patterns})"


@pytest.mark.invariant("SCH-17")
@pytest.mark.invariant("SCH-5")
def test_the_readme_does_not_say_a_runs_program_is_started_early():
    """A run begins at its time, never before it, and no run's program is started before its time (SCH-5): no
    paragraph of the README's Schedules section says the service starts a run's program (or process) early."""
    early = [p for p in schedules_prose()
             if re.search(r"\b(?:start|load)\w*\b[^.]*\bearly\b|\bearly\b[^.]*\b(?:start|load)\w*\b", p)]

    assert early == [], f"the README's Schedules section still says runs are started early: {early}"
