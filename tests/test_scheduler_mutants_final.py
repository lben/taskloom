"""The scheduler service: a Catch up question whose missed runs are dropped, a time exactly 2 minutes late beside a
catch-up going, and Stop now for a run going, checked where mutation testing found the other tests let a planted bug
through (docs/promises/scheduler.mutants.yaml). Each test keeps a promise of docs/promises/scheduler.yaml or a behaviour
the README states.

The service is the real Scheduler, made as `taskloom scheduler` makes it and checked at chosen clock times; each run's
process is replaced at the process boundary by a stand-in that claims its time as the real run does, and records itself
in the run history as it begins (Begins, in tests/test_scheduler_mutants_ends.py). Here a stand-in's stdin is as Popen
gives it (Piped). A question about missed runs is asked through the service's `ask`, and closed through its `withdraw`,
which the tray service shows and closes as a window."""

import datetime as dt
import io
import os
import subprocess
import time
import types
from pathlib import Path

import pytest

from taskloom.config import Home
from taskloom.flow import load_flow
from taskloom.scheduler import LIST_UNREADABLE, STOP_SECONDS, forget_schedules, request_turn_off
from tests.test_scheduler_held_times import check, clock, one_off
from tests.test_scheduler_mutants_ends import Begins, Stand, at, tick
from tests.test_scheduler_mutants_times import began
from tests.test_scheduler_new_promises import (EVERY_TEN, FIRST_SIGHT, REAL_POPEN, check_after, clock_shows, flow_yaml,
                                               one_at_a_time, schedule, statuses, write)

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
