"""The scheduler service: a Catch up question whose missed runs are dropped, a time exactly 2 minutes late beside a
catch-up going, Stop now for a run going, and a newer Taskloom's service waiting beside an older one's (when it takes
over), checked where mutation testing found the other tests let a planted bug through, or could not reach the code
(docs/promises/scheduler.mutants.yaml). Each test keeps a promise of docs/promises/scheduler.yaml or
docs/promises/updates.yaml, or a behaviour the README states.

The service is the real Scheduler, made as `taskloom scheduler` makes it and checked at chosen clock times; each run's
process is replaced at the process boundary by a stand-in that claims its time as the real run does, and records itself
in the run history as it begins (Begins, in tests/test_scheduler_mutants_ends.py). Here a stand-in's stdin is as Popen
gives it (Piped). A question about missed runs is asked through the service's `ask`, and closed through its `withdraw`,
which the tray service shows and closes as a window.

The newer service waiting beside an older one is the real `serve`, called in this process as `taskloom scheduler
--headless` calls it (beside_older). What cannot be real in this process is stood in for, and only that: the older
service is this test holding the folder's lock (Older), its process a number no process has, which os.kill answers
here; the waiting service's clock and its sleep between checks are the test's (each sleep lasts until the next check
the test plans); once it serves, it is stopped (SIGTERM) before its first check."""

import contextlib
import datetime as dt
import gc
import io
import os
import signal
import sqlite3
import subprocess
import threading
import time
import types
from pathlib import Path

import pytest

from taskloom import __version__
from taskloom.config import Home
from taskloom.flow import load_flow
from taskloom.history import History
from taskloom.history import connect as history_connect
from taskloom.scheduler import (LIST_UNREADABLE, STATE_TABLES, STOP_SECONDS, Scheduler, _unlock, acquire_lock,
                                forget_schedules, paused, request_turn_off, serve)
from tests.test_scheduler_held_times import check, clock, one_off
from tests.test_scheduler_mutants_ends import Begins, Stand, at, tick
from tests.test_scheduler_mutants_state import record_run
from tests.test_scheduler_mutants_times import began
from tests.test_scheduler_new_promises import (EVERY_TEN, FIRST_SIGHT, HOURLY, NO_TZ_ON_WINDOWS, REAL_POPEN, FakeRuns,
                                               check_after, clock_shows, flow_yaml, one_at_a_time, schedule, statuses,
                                               write)
from tests.test_scheduler_new_promises import berlin  # noqa: F401  (a fixture: the local time zone is Europe/Berlin)

ASKED = check_after(150)  # 2026-01-06 01:05: the service is back after 150 times of EVERY_TEN were missed


class Piped(Begins):
    """Begins (see there), whose stdin is as Popen gives it: a pipe the service writes to only if the service started
    the process with one (stdin=subprocess.PIPE); otherwise None, and the process reads the service's own stdin. Each
    signal the service sends it is recorded (`signals`), and ends it at once: a run that Stop now has already told to
    stop (SIGTERM, which it takes as a request to cancel) ends at once on a second one, as on a second Ctrl+C."""

    def __init__(self, args: list[str], runs: "Stands", home: Path, piped: bool):
        super().__init__(args, runs, home)
        self.piped, self.signals = piped, []
        if not piped:
            self.stdin = None

    def reach_its_time(self) -> bool:
        if self.piped:
            return super().reach_its_time()
        self.stdin = io.StringIO()  # (Run reads here whether it was asked to stop before it began: with no pipe, no)
        try:
            return super().reach_its_time()
        finally:
            self.stdin = None

    def asked(self) -> str:
        """What the service wrote on its stdin."""
        return self.stdin.getvalue() if self.stdin is not None else ""

    def terminate(self):
        self.signals.append("SIGTERM")
        super().kill()

    def kill(self):
        self.signals.append("SIGKILL")
        super().kill()


class Stands(Stand):
    def popen(self, args, *rest, **kwargs):
        if not (isinstance(args, list) and "run" in map(str, args) and "--start-at" in map(str, args)):
            return REAL_POPEN(args, *rest, **kwargs)
        run = Piped([str(a) for a in args], self, Path((kwargs.get("env") or os.environ)["TASKLOOM_HOME"]),
                    kwargs.get("stdin") == subprocess.PIPE)
        self.started.append(run)
        return run


@pytest.fixture
def runs(monkeypatch) -> Stands:
    stand_ins = Stands()
    monkeypatch.setattr(subprocess, "Popen", stand_ins.popen)
    yield stand_ins
    stand_ins.end()


def questions(asked: list, withdrawn: list) -> dict:
    """The service's `ask` and `withdraw`, which keep each question asked ((flow, count, answer function)) in `asked`
    and each answer function whose question was withdrawn in `withdrawn`."""
    return {"ask": lambda flow, count, answer: asked.append((flow, count, answer)), "withdraw": withdrawn.append}


def told_to(said: list):
    return lambda title, message: said.append(f"{title}: {message}")


# --- SCH-9: a Catch up question whose missed runs are dropped --------------------------------------------------------

@pytest.mark.invariant("SCH-9")
def test_an_answer_given_just_before_its_missed_runs_are_dropped_runs_none_and_the_check_goes_on(env, runs):
    """backlog (every 10 minutes, Catch up) missed 150 times, and you are asked about them. You answer Run only the
    latest 5 in that window, and before the service's next check (it checks every second) you save backlog with its
    schedule changed to every 15 minutes: its missed runs are dropped. At that check the question no longer applies
    (README: the window closes and asks nothing more): it is withdrawn with the answer function it came with, your
    answer runs none of the dropped runs and tells you nothing about them, and the check goes on as usual (a check that
    fails is told as one: "A check failed")."""
    path = schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN)})["backlog"]
    said, asked, withdrawn = [], [], []
    service = runs.service(env.home, told_to(said), **questions(asked, withdrawn))
    check(service, runs, FIRST_SIGHT)
    check(service, runs, ASKED)
    assert [(flow, count) for flow, count, _ in asked] == [("backlog", 150)], f"not arranged: {said}"
    answer = asked[0][2]

    answer(5)  # Run only the latest 5
    write(path, flow_yaml({"cron": "*/15 * * * *", "misfire": "catch_up"}))  # you save it, as the editor saves it
    with clock_shows(ASKED + dt.timedelta(seconds=30)):
        forget_schedules(Home(env.home), path, load_flow(path).triggers)
    try:
        tick(service, runs, ASKED + dt.timedelta(minutes=1))
    except Exception as e:
        pytest.fail(f"the service's check failed after you answered and saved: {type(e).__name__}: {e}")
    check(service, runs, ASKED + dt.timedelta(minutes=1))

    assert withdrawn == [answer], (
        f"its missed runs were dropped, yet the question was not withdrawn with its own answer function: {withdrawn}")
    assert runs.of("backlog") == [], f"runs of backlog began for {[r.start_at for r in runs.of('backlog')]}"
    assert [t for t in said if "backlog" in t] == [], f"told about the dropped missed runs: {said}"
    assert len(asked) == 1, f"asked again: {[(flow, count) for flow, count, _ in asked]}"


@pytest.mark.invariant("SCH-4")
@pytest.mark.invariant("SCH-9")
def test_stop_now_closes_the_question_of_its_flow_also_while_scheduled_yaml_cannot_be_read(env, runs):
    """backlog (every 10 minutes, Catch up): its 00:10 run is going, a long one, when the service is back the next day
    at 01:05: 149 times were missed, and you are asked about them. Then scheduled.yaml gets a mistake (edited by hand),
    and you are told the service cannot read it. You turn the service off with Stop now for backlog: its waiting runs
    are dropped (SCH-4), so the question about its missed runs no longer applies, and its window closes (README: If
    those missed runs are dropped before you answer, ... or choose Stop now for its flow, the window closes) at the
    service's next check, while its run asked to stop is still going: withdrawn with the answer function it came
    with."""
    path = schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN)})["backlog"]
    runs.recording.add("backlog")
    runs.holding.add("backlog")  # (its run goes on)
    said, asked, withdrawn = [], [], []
    service = runs.service(env.home, told_to(said), **questions(asked, withdrawn))
    for moment in (FIRST_SIGHT, dt.datetime(2026, 1, 5, 0, 10), ASKED):
        check(service, runs, moment)
    assert statuses(env.home, "backlog") == ["running"], f"not arranged: backlog's 00:10 run is not going: {said}"
    assert [(flow, count) for flow, count, _ in asked] == [("backlog", 149)], f"not arranged: {said}"
    write(env.home / "scheduled.yaml", "flows: [unclosed\n")  # e.g. edited by hand
    check(service, runs, ASKED + dt.timedelta(seconds=10))
    assert [t for t in said if t.startswith(LIST_UNREADABLE)], f"not arranged: not told of the mistake: {said}"

    request_turn_off(Home(env.home), {str(path): "stop"})  # Proceed, with Stop now for backlog
    check(service, runs, ASKED + dt.timedelta(seconds=11))

    assert not service.off, "the service turned off before backlog's run, asked to stop, had ended"
    assert withdrawn == [asked[0][2]], (
        f"Stop now dropped backlog's missed runs, yet the window of the question about them was not closed with its "
        f"own answer function: {withdrawn}")


# --- SCH-9, SCH-11: a time exactly 2 minutes late, while its catch-up goes --------------------------------------------

@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-11")
def test_with_overlap_allow_a_time_exactly_2_minutes_late_waits_for_its_catch_up_going(env, runs):
    """tenly runs every 10 minutes (Catch up, Overlap allow), each run going on a long time. The service first sees it
    at 00:00:30 and next checks at 00:13:00: 00:10 was missed, and its catch-up run starts. The next check is at
    00:22:00, exactly 2 minutes after 00:20: only a time less than 2 minutes late is on time (SCH-9), so 00:20 was
    missed too, and it waits for the catch-up going: the missed runs of a Catch up schedule always go one at a time,
    even with Overlap allow (README: Overlaps). It starts once that run has ended."""
    schedule(env.home, env.root, {"tenly": flow_yaml(EVERY_TEN, overlap="allow")})
    said = []
    service = runs.service(env.home, told_to(said))
    runs.holding.add("tenly")
    for moment in ("00:00:30", "00:13:00", "00:22:00"):
        check(service, runs, clock(moment))
    assert began(runs.of("tenly")) == ["s1 00:10:00"], (
        f"with tenly's catch-up run for 00:10 going, a run began at 00:22:00 for its 00:20 time, exactly 2 minutes "
        f"late: {began(runs.of('tenly'))}; told: {said}")

    runs.holding.discard("tenly")  # (each run ends)
    check(service, runs, clock("00:22:30"))
    assert began(runs.of("tenly")) == ["s1 00:10:00", "s1 00:20:00"], f"told: {said}"
    assert one_at_a_time(runs.of("tenly")) == [], "a missed run of a Catch up schedule started alongside its catch-up"


# --- SCH-4: Stop now for a run going ---------------------------------------------------------------------------------

@pytest.mark.invariant("SCH-4")
def test_stop_now_asks_a_run_going_on_its_stdin_and_forces_it_only_seconds_later(env, runs, monkeypatch):
    """nightly's run began at 07:00 and is going. You turn the service off with Stop now for nightly (Proceed), which
    has told its process to stop already (SIGTERM: it cancels; here the stand-in has no process to tell). The service,
    at its next checks, asks the run to stop on its stdin, as a scheduled run reads cancel requests (`taskloom run
    --scheduled`), and sends it no signal while it cancels: a second SIGTERM would end it at once. Only once it is still
    going STOP_SECONDS later, by the service's clock, is it forced to end; it is recorded as cancelled (README: a run
    that does not stop within a few seconds is forced to end, and is recorded as cancelled all the same)."""
    passed = [0.0]  # (seconds the service's clock is ahead)
    monkeypatch.setattr("taskloom.scheduler.time", types.SimpleNamespace(
        **{**vars(time), "monotonic": lambda: time.monotonic() + passed[0]}))
    path = schedule(env.home, env.root, {"nightly": flow_yaml(one_off(clock("07:00:00")))})["nightly"]
    runs.recording.add("nightly")
    runs.holding.add("nightly")  # (it is still cancelling)
    said = []
    service = runs.service(env.home, told_to(said))
    at(service, runs, clock("07:00:00"))
    [run] = runs.of("nightly")
    assert statuses(env.home, "nightly") == ["running"], f"not arranged: nightly's run is not going: {said}"

    request_turn_off(Home(env.home), {str(path): "stop"})
    tick(service, runs, clock("07:00:05"))
    passed[0] = STOP_SECONDS - 1
    tick(service, runs, clock("07:00:10"))
    assert "cancel" in run.asked(), f"Stop now did not ask nightly's run to stop on its stdin: {run.asked()!r}"
    assert run.signals == [] and run.returncode is None, (
        f"nightly's run, asked to stop, was given no time to cancel: the service sent it {run.signals}")

    passed[0] = STOP_SECONDS + 1
    tick(service, runs, clock("07:00:12"))
    tick(service, runs, clock("07:00:13"))
    assert run.signals == ["SIGKILL"], f"still going {STOP_SECONDS} s after it was asked, it was sent {run.signals}"
    assert statuses(env.home, "nightly") == ["cancelled"], statuses(env.home, "nightly")


@pytest.mark.invariant("SCH-1")
def test_a_service_starting_while_another_process_takes_its_turn_waits_for_it_and_starts_just_after(env):
    """Whoever takes the Taskloom folder's lock takes it in turn (scheduler.turn): a service starting while another
    process holds its turn for a moment (e.g. a run's claim looking whether the service is on) waits for it, without
    failing, and has the lock as soon as the turn is free: not a second later."""
    import threading

    from taskloom.scheduler import _lock, _unlock, acquire_lock

    other = _lock(env.home / "scheduler.turn")  # (another process holding its turn)
    assert other is not None, "not arranged: the turn was taken"
    threading.Timer(0.1, _unlock, [other]).start()
    began = time.monotonic()
    lock = acquire_lock(Home(env.home))
    waited = time.monotonic() - began
    lock.close()
    assert 0.05 <= waited < 0.6, f"the service had the lock {waited:.2f} s after it asked, the turn free after 0.1 s"


# --- UP-8, SCH-1: a newer Taskloom's service waiting beside an older one's -------------------------------------------
# What the waiting service must call (the implementer's contract here, beside tests/test_updates.py's): it waits between
# its checks with taskloom.scheduler's `time.sleep`; it reads this PC's clock through taskloom.scheduler's `dt`
# (datetime.now) or `time` (time.time, time.monotonic); it stops the older service with os.kill(<its scheduler.pid>,
# SIGTERM); once it holds the folder, it sets its signal handlers with taskloom.scheduler's `signal.signal`.

UTC = dt.timezone.utc
SECOND, MINUTE = dt.timedelta(seconds=1), dt.timedelta(minutes=1)
OLD_PID = 2 ** 30 - 7  # the older service's process: no process has this number (os.kill is answered for it here)
START = dt.datetime(2026, 6, 15, 8, 0, tzinfo=UTC)  # (no clock change near it)


class Older:
    """An older Taskloom's scheduler service holding the Taskloom folder, played by this test: it has taken the folder's
    lock as `taskloom scheduler` takes it, and its scheduler.pid and scheduler.version name its process (OLD_PID) and
    an older Taskloom (0.0.1). Told to stop (SIGTERM to its process), it ends: it lets go of the lock. `stopped`: the
    moments (by the waiting service's clock) it was told to stop."""

    def __init__(self, home: Path, clock: types.SimpleNamespace):
        self.clock, self.stopped = clock, []
        self.lock = acquire_lock(Home(home))
        write(home / "scheduler.pid", str(OLD_PID))
        write(home / "scheduler.version", f"0.0.1 {OLD_PID}")

    def signalled(self, sig):
        if sig == signal.SIGTERM:
            self.stopped.append(self.clock.now)
            self.end()

    def end(self):
        if self.lock is not None:
            _unlock(self.lock)
            self.lock = None


def beside_older(env, first: dt.datetime, then: list) -> dict:
    """This version's scheduler service starts (serve, as `taskloom scheduler --headless` calls it) at `first` (this
    PC's clock, aware) while an older Taskloom's holds the Taskloom folder (Older), and waits beside it. Its later
    checks come at the moments `then` lists, each with what happens just before it (or None); between two of them
    nothing changes, as if it slept through the checks in between. After the last, you turn the service off
    (Proceed), so one still waiting gives up. Once it serves, it is stopped (SIGTERM) before its first check. What
    happened: `stopped`, the moments the older one was told to stop; `served`, the moments this one began to serve,
    each with what scheduler.pid and scheduler.version then said and whether the older one had let go of the folder;
    `ended`, None if serve returned 0, else how it ended."""
    home, planned = Home(env.home), iter(then)
    clock = types.SimpleNamespace(now=first)
    began_at, real_kill, real_dup, held = time.monotonic(), os.kill, os.dup, []
    older = Older(env.home, clock)
    happened = {"stopped": older.stopped, "served": [], "ended": None}

    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime.fromtimestamp(clock.now.timestamp(), tz)  # (as datetime.now: fold=1 in a repeated hour)

    def sleep(seconds):
        step = next(planned, None)
        if step is None:
            assert not paused(home), "turned off, the waiting service went on waiting"
            request_turn_off(home, {})
            return
        clock.now, change = step
        if change is not None:
            change()

    def kill(pid, sig):
        return older.signalled(sig) if pid == OLD_PID else real_kill(pid, sig)

    def dup(fd):  # (the service's hold on the folder's lock until its process ends: let go at the end of the test)
        held.append(real_dup(fd))
        return held[-1]

    def handler(sig, stop):
        if sig == signal.SIGTERM:  # (stopped before its first check)
            happened["served"].append((clock.now, (env.home / "scheduler.pid").read_text(),
                                       (env.home / "scheduler.version").read_text(), older.lock is None))
            stop(sig, None)

    watchdog = threading.Timer(30, request_turn_off, [home, {}])  # (one that never sleeps as planned still ends)
    watchdog.start()
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("taskloom.scheduler.dt", types.SimpleNamespace(**{**vars(dt), "datetime": Clock}))
            patch.setattr("taskloom.scheduler.time", types.SimpleNamespace(**{
                **vars(time), "sleep": sleep, "time": lambda: clock.now.timestamp(),
                "monotonic": lambda: began_at + (clock.now - first).total_seconds()}))
            patch.setattr("taskloom.scheduler.signal", types.SimpleNamespace(**{**vars(signal), "signal": handler}))
            patch.setattr(os, "kill", kill)
            patch.setattr(os, "dup", dup)
            try:
                code = serve(home, headless=True)
                happened["ended"] = None if code == 0 else f"exit code {code}"
            except Exception as e:
                happened["ended"] = f"{type(e).__name__}: {e}"
    finally:
        watchdog.cancel()
        older.end()
        for fd in held:
            os.close(fd)
    return happened


def ends(env, run_id: int):
    """The run ends: it records its end in the run history, as `taskloom run` does."""
    def end():
        history = History(env.home / "history.db")
        try:
            history({"event": "run_finished", "run_id": run_id, "status": "success",
                     "ts": dt.datetime.now().isoformat()})
        finally:
            history.close()
    return end


def took_over(moments: list) -> list:
    """What beside_older's `stopped`, `served` and `ended` are when the newer service takes over at the checks
    `moments` (none: it never does): the older service told to stop at them, and this one serving from them, holding
    the folder (scheduler.pid and scheduler.version say this process and this version), the older one having let go
    first; serve returned 0."""
    me = os.getpid()
    return [moments, [(moment, str(me), f"{__version__} {me}", True) for moment in moments], None]


def utc(moments: list) -> list[str]:
    return [f"{moment:%H:%M:%S} UTC" for moment in moments]


@NO_TZ_ON_WINDOWS
@pytest.mark.invariant("UP-8")
@pytest.mark.invariant("SCH-1")
@pytest.mark.parametrize("since", [dt.datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
                                   dt.datetime(2026, 3, 29, 0, 50, tzinfo=UTC)], ids=["clocks go back", "clocks go on"])
def test_the_hour_a_newer_service_waits_beside_an_older_one_is_an_hour_on_a_night_the_clocks_change(
        env, berlin, since):
    """In Berlin the clocks change at 01:00 UTC: on 2026-10-25 from 03:00 CEST back to 02:00 CET (02:00-03:00 comes
    twice), on 2026-03-29 from 02:00 CET on to 03:00 CEST (02:00-03:00 is skipped). This version's service starts beside
    an older one's at `since` while three runs of yours go: no quiet moment. The first run ends 20 minutes later: not an
    hour yet, it waits on. The second ends 61 minutes after it began waiting: it takes over at that check, though the
    third still goes. By this PC's clock: back, it begins at 02:30 CEST, the first ends at 02:50 CEST, the second at
    02:31 CET (the clock shows a minute more); on, it begins at 01:50 CET, the first ends at 03:10 CEST (the clock shows
    80 minutes more), the second at 03:51 CEST."""
    runs = [record_run(env.home, name, env.root / f"{name}.yaml", None) for name in ("first", "second", "third")]
    early, late = since + 20 * MINUTE, since + 61 * MINUTE
    happened = beside_older(env, since, [(since + SECOND, None), (early, ends(env, runs[0])), (early + SECOND, None),
                                         (late, ends(env, runs[1])), (late + SECOND, None)])

    assert happened["ended"] is None, f"the waiting service ended: {happened['ended']}"
    assert happened["stopped"] == [late], (
        f"it began waiting at {since:%H:%M} UTC, runs ended 20 and 61 minutes later; it stopped the older service at "
        f"{utc(happened['stopped'])}, not once at {late:%H:%M} UTC, the first check after a run ended an hour on")
    assert [happened["stopped"], happened["served"], happened["ended"]] == took_over([late])


@pytest.mark.invariant("UP-8")
@pytest.mark.invariant("SCH-1")
@pytest.mark.parametrize("name, broken, mended", [
    ("settings.yaml", "max_concurrent_runs: [2\n", "max_concurrent_runs: 2\n"),
    ("scheduled.yaml", "flows: [\n", "flows: []\n")], ids=["settings", "scheduled flows"])
def test_a_newer_service_waiting_beside_an_older_one_waits_on_while_a_yaml_file_is_broken_by_hand(
        env, name, broken, mended):
    """Nothing goes or is scheduled, but `name` has been broken by hand (YAML that cannot be read) as this version's
    service starts beside an older one's: it cannot tell whether it is a quiet moment, so it waits on through its checks
    without ending (as the running service's checks go on: charter C10), and the older one goes on alone. Once you mend
    the file, it takes over at the next check: one service all along, never none."""
    write(env.home / name, broken)
    start = dt.datetime.now(UTC).replace(microsecond=0)
    mend = start + 3 * SECOND
    happened = beside_older(env, start, [(start + SECOND, None), (start + 2 * SECOND, None),
                                         (mend, lambda: write(env.home / name, mended)), (mend + SECOND, None)])

    assert happened["ended"] is None, f"while {name} could not be read, the waiting service ended: {happened['ended']}"
    assert happened["stopped"] == [mend], (
        f"it stopped the older service at {utc(happened['stopped'])}; {name} was mended at {mend:%H:%M:%S} UTC")
    assert [happened["stopped"], happened["served"], happened["ended"]] == took_over([mend])


def scheduled_at(env, name: str, moment: dt.datetime, *before: str) -> Path:
    """The flow `name`, its one schedule a one-off time at `moment` (Catch up), listed in scheduled.yaml after the flows
    `before` (each a file Taskloom cannot read)."""
    texts = {other: "blocks: [\n" for other in before}
    texts[name] = flow_yaml({"at": f"{moment.astimezone():%Y-%m-%d %H:%M:%S}", "misfire": "catch_up"})
    return schedule(env.home, env.root, texts)[name]


def run_going(env) -> dict:
    """A run of yours goes; it ends before the check 2 seconds on."""
    return {2: ends(env, record_run(env.home, "manual", env.root / "manual.yaml", None))}


def run_number_reused(env):
    """The run history says a run of yours goes, but its process has ended and its number is another program's now,
    one that began after the run (this test's process)."""
    run_id = record_run(env.home, "manual", env.root / "manual.yaml", None)
    with contextlib.closing(sqlite3.connect(env.home / "history.db", timeout=30)) as db:
        db.execute("UPDATE runs SET began=1 WHERE id=?", (run_id,))
        db.commit()


def run_waiting(env) -> dict:
    """The older service has a run waiting its turn (saved in its state, waiting_runs; its flow since taken off the
    scheduled flows); it goes before the check 2 seconds on, and ends."""
    path = write(env.root / "later.yaml", flow_yaml(HOURLY))
    with contextlib.closing(history_connect(env.home / "history.db")) as db, db:
        for table in STATE_TABLES:
            db.execute(table)
        db.execute("INSERT INTO waiting_runs (flow, block, schedule, due, waits) VALUES (?, ?, ?, ?, ?)",
                   (str(path), "s1", "every 60 minutes", (START - MINUTE).isoformat(), "slot"))

    def goes():
        with contextlib.closing(history_connect(env.home / "history.db")) as db, db:
            db.execute("DELETE FROM waiting_runs")
    return {2: goes}


def come_not_run(env):
    """A one-off time came 30 seconds ago, and the older service has not run it yet (the flow was saved 10 minutes
    before, so the time is due)."""
    path = scheduled_at(env, "came", START - 30 * SECOND)
    with clock_shows(START - 10 * MINUTE):
        forget_schedules(Home(env.home), path, load_flow(path).triggers)


def older_checked(env, *moments: dt.datetime):
    """A scheduler service before this one, made as `taskloom scheduler` makes one, checked at `moments` (its runs
    stand-ins that have finished by its next check: FakeRuns), and has stopped since."""
    lock, runs = acquire_lock(Home(env.home)), FakeRuns()
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(subprocess, "Popen", runs.popen)
            service = Scheduler(Home(env.home))
            for moment in moments:
                service.tick(moment)
    finally:
        runs.end()
        _unlock(lock)
    gc.collect()  # (that service's hold on the history file goes with it)


def come_and_run(env):
    """A one-off time came 30 seconds ago, and the older service has run it (it checked from 10 minutes before)."""
    scheduled_at(env, "came", START - 30 * SECOND)
    older_checked(env, START - 10 * MINUTE, START - 30 * SECOND, START - 29 * SECOND)


def every_seen(env):
    """An Every 60 minutes schedule the older service first saw (and ran) a minute ago: its next time is 59 minutes
    away."""
    schedule(env.home, env.root, {"hourly": flow_yaml(HOURLY)})
    older_checked(env, START - MINUTE, START - MINUTE + SECOND)


QUIET = {  # what is going, waiting or due as the newer service starts: (arranged so, the check it takes over at)
    "nothing goes, waits or is scheduled": (lambda env: None, 0),
    "a run of yours goes, then ends": (run_going, 2),
    "a run the history says goes, its number another program's now": (run_number_reused, 0),
    "a run waits its turn, then goes": (run_waiting, 2),
    "a time due in a minute": (lambda env: scheduled_at(env, "soon", START + MINUTE), None),
    "a time due in exactly 2 minutes": (lambda env: scheduled_at(env, "soon", START + 2 * MINUTE), None),
    "a time due in 2 minutes and a second": (lambda env: scheduled_at(env, "soon", START + 2 * MINUTE + SECOND), 0),
    "a time due in a minute, after a flow that cannot be read": (
        lambda env: scheduled_at(env, "soon", START + MINUTE, "broken"), None),
    "a time come 30 s ago, not run yet": (come_not_run, None),
    "a time come 30 s ago, run by the older service": (come_and_run, 0),
    "an Every N schedule not seen yet": (lambda env: schedule(env.home, env.root, {"hourly": flow_yaml(HOURLY)}), None),
    "an Every N schedule seen a minute ago": (every_seen, 0),
}


@pytest.mark.invariant("UP-8")
@pytest.mark.invariant("SCH-1")
@pytest.mark.parametrize("case", list(QUIET))
def test_a_newer_service_takes_over_from_an_older_one_only_at_a_quiet_moment(env, case):
    """This version's service starts beside an older one's and checks each second: it takes over at the first quiet
    moment, when no run goes (yours or the service's, their processes still going), none waits its turn, and no
    scheduled time is due within 2 minutes (a time exactly 2 minutes away is; one that has come counts until the service
    has run it; an Every N schedule the service has not seen yet is due at once; a flow that cannot be read is left
    out, as the service leaves it out). Until then it waits, and the older one goes on alone."""
    arrange, at_check = QUIET[case]
    changes = arrange(env)
    changes = changes if isinstance(changes, dict) else {}
    happened = beside_older(env, START, [(START + i * SECOND, changes.get(i)) for i in (1, 2)])

    expected = [] if at_check is None else [START + at_check * SECOND]
    assert happened["ended"] is None, f"the waiting service ended: {happened['ended']}"
    assert happened["stopped"] == expected, (
        f"{case}: it stopped the older service at {utc(happened['stopped'])}, not at {utc(expected)} (it started at "
        f"{START:%H:%M:%S} UTC and checked each second)")
    assert [happened["stopped"], happened["served"], happened["ended"]] == took_over(expected)
