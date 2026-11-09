"""The scheduler service's agreed promises (docs/promises/scheduler.yaml) where the older scheduler tests leave a
part unchecked: several services started at once (SCH-1), every time a schedule the editor builds has through a
whole year (SCH-5, SCH-7), generated schedules, settings, check times with gaps and what you do meanwhile (restarts,
schedules saved while the service is off, runs started yourself, turning the service off and on again: SCH-4, SCH-6 to
SCH-12, SCH-16), a schedule changed while its run's program loads (SCH-8), Skip's 2 minutes (SCH-9), a crash the flow
asks to be emailed about (SCH-13) and the real service through mistakes in its files (SCH-14)."""

import contextlib
import datetime as dt
import io
import itertools
import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from taskloom.config import Home
from taskloom.editor.docs import EVERY_CHOICES
from taskloom.flow import load_flow, real_path
from taskloom.history import History
from taskloom.history import connect as history_connect
from taskloom.launch import run_alive
from taskloom.schedule import Every, Trigger, plan_cron
from taskloom.scheduler import (NOT_DUE, Scheduler, acquire_lock, forget_schedules, request_turn_off, runs_going,
                                set_paused)
from taskloom.scheduler import _unlock as unlock
from tests.test_paths_scheduler import flow as timed_flow
from tests.test_paths_scheduler import heartbeat, runs, service, wait_for, write
from tests.test_paths_scheduler import start_service as start_headless
from tests.test_scheduler_at_its_time import SLOW_LOAD
from tests.test_scheduler_new_promises import Said, claim_its_time, clock_shows
from tests.test_scheduler_start_at import TRAY_SERVICE, start_at_clock, stop_service, wait_until

SECOND, MINUTE = dt.timedelta(seconds=1), dt.timedelta(minutes=1)


def at(clock: str) -> dt.datetime:
    return dt.datetime.combine(dt.date(2026, 1, 5), dt.time.fromisoformat(clock))  # a Monday


# --- SCH-1: one service per Taskloom folder, also when several start at once -----------------------------------------

def start_said(env, n: int, tray: bool) -> subprocess.Popen:
    """start_service (test_scheduler_start_at) with what the process says on stderr written to service<n>.err."""
    command = [sys.executable, "-c", TRAY_SERVICE] if tray else [sys.executable, "-m", "taskloom.cli", "scheduler",
                                                                  "--headless"]
    extra = {"QT_QPA_PLATFORM": "offscreen", "TRAY_MESSAGES": str(env.root / "tray-messages.jsonl")} if tray else {}
    with open(env.root / f"service{n}.err", "w", encoding="utf-8") as err:
        return subprocess.Popen(command, env={**env.env, **extra}, cwd=env.root, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=err)


@pytest.mark.invariant("SCH-1")
def test_of_several_services_started_at_the_same_moment_only_one_runs(env):
    """Logging in, opening the editor and clicking On at the same moment: two tray services (as the editor and the
    login start it) and one from a terminal, for one Taskloom folder. Exactly one runs and checks; the others end at
    once, saying one is already running."""
    procs = [start_said(env, 0, tray=True), start_said(env, 1, tray=True), start_said(env, 2, tray=False)]
    try:
        wait_for(lambda: sum(p.poll() is None for p in procs) <= 1, timeout=60)
        going = [p for p in procs if p.poll() is None]
        assert len(going) == 1, f"{len(going)} scheduler services run for one Taskloom folder"
        assert wait_for(lambda: heartbeat(env.home), timeout=30), "the one left does not check"
        assert going[0].poll() is None
        for n, proc in enumerate(procs):
            if proc is not going[0]:
                said = (env.root / f"service{n}.err").read_text(encoding="utf-8", errors="replace")
                assert proc.returncode == 1 and "already running" in said, said
    finally:
        for proc in procs:
            stop_service(env, proc)


# --- SCH-5, SCH-7: every time of every schedule the editor builds, through the leap year 2028 -------------------------

YEAR_START, YEAR_END = dt.datetime(2028, 1, 1), dt.datetime(2029, 1, 1)


def every_set_of_days() -> list[list[int]]:
    """Every choice of the Schedule block's day boxes (at least one day): cron's weekdays, 0 is Sunday."""
    return [list(days) for n in range(1, 8) for days in itertools.combinations(range(7), n)]


PLANS = {
    "hourly": [{"kind": "hourly", "minute": m} for m in range(60)],
    "daily": [{"kind": "daily", "hour": h, "minute": m} for h in range(24) for m in range(60)],
    "weekdays": [{"kind": "weekdays", "hour": h, "minute": m} for h in range(24) for m in range(60)],
    "weekly": [{"kind": "weekly", "days": days, "hour": h, "minute": (7 * h + 11 * i) % 60}
               for i, days in enumerate(every_set_of_days()) for h in range(24)],
    "monthly": [{"kind": "monthly", "day": d, "hour": h, "minute": (7 * h + 11 * d) % 60}
                for d in range(1, 32) for h in range(24)],
}
# One plan of each kind is also asked, at every minute of the year, for its next time.
EVERY_MINUTE = {"hourly": {"kind": "hourly", "minute": 59}, "daily": {"kind": "daily", "hour": 23, "minute": 59},
                "weekdays": {"kind": "weekdays", "hour": 0, "minute": 0},
                "weekly": {"kind": "weekly", "days": [0, 6], "hour": 12, "minute": 30},
                "monthly": {"kind": "monthly", "day": 31, "hour": 23, "minute": 59}}


def oracle(plan: dict) -> list[dt.datetime]:
    """The plan's times from 2028-01-01 to 2029-02-28, worked out day by day: does the day match, then each hour
    and minute in its sets."""
    every_day = {"hourly": lambda d: True, "daily": lambda d: True, "weekdays": lambda d: d.isoweekday() <= 5,
                 # isoweekday: Monday 1 ... Sunday 7; % 7 makes Sunday 0, as the plan's days count
                 "weekly": lambda d: d.isoweekday() % 7 in plan["days"],
                 "monthly": lambda d: d.day == plan["day"]}[plan["kind"]]
    hours = set(range(24)) if plan["kind"] == "hourly" else {plan["hour"]}
    times, day = [], dt.date(2028, 1, 1)
    while day <= dt.date(2029, 2, 28):
        if every_day(day):
            times += [dt.datetime(day.year, day.month, day.day, hour, minute)
                      for hour in sorted(hours) for minute in [plan["minute"]]]
        day += dt.timedelta(days=1)
    return times


def first_difference(got: list, expected: list) -> str | None:
    for n, (g, e) in enumerate(zip(got, expected)):
        if g != e:
            return f"time #{n + 1}: {g} where {e} was expected"
    return f"{len(got)} times where {len(expected)} were expected" if len(got) != len(expected) else None


def disagreement(trigger: Trigger, expected: list[dt.datetime], due_from: dt.datetime, start=None,
                 every_minute_at: int | None = None) -> str | None:
    """Where `trigger` disagrees with `expected` (all its times from before 2028 to after it, oldest first): over
    2028 as the service asks (Trigger.due from `due_from` to the year's last second), its next time asked at each
    time of 2028 and the second before it, and with `every_minute_at` at that second of every minute of 2028. `start`:
    where an every-N schedule counts from."""
    in_year = [t for t in expected if YEAR_START <= t < YEAR_END]
    wrong = first_difference(trigger.due(due_from, YEAR_END - SECOND), in_year)
    if wrong:
        return f"over 2028: {wrong}"
    first = expected.index(in_year[0])
    for n, t in enumerate(in_year):
        for asked, answer in ((t - SECOND, t), (t, expected[first + n + 1])):
            if (got := trigger.next_after(asked, start)) != answer:
                return f"after {asked}: {got} where {answer} was expected"
    if every_minute_at is not None:
        n, asked = 0, YEAR_START + every_minute_at * SECOND
        while asked < YEAR_END:
            while expected[n] <= asked:
                n += 1
            if (got := trigger.next_after(asked, start)) != expected[n]:
                return f"after {asked}: {got} where {expected[n]} was expected"
            asked += MINUTE
    return None


@pytest.mark.invariant("SCH-5", every="its time: which minutes of 2028 are a plan's times, and its next time, for each "
                       "plan the editor builds: hourly at each minute 0-59; daily and Monday to Friday at each of the "
                       "1440 times of day; each of the 127 sets of weekdays at 24 times; each day 1-31 at 24 times")
@pytest.mark.parametrize("kind", PLANS)
def test_each_plan_the_editor_builds_has_exactly_its_times_through_2028(kind):
    """SCH-5's "its time", over the leap year 2028 (2028-01-01 00:00 to 2028-12-31 23:59, and the next time after
    it): the times the service computes for a plan of the Schedule block's When (its cron line from
    taskloom.schedule.plan_cron, as the editor saves it) are exactly the plan's. Plans: hourly at each minute 0-59;
    daily and Monday to Friday at each of the 1440 times of day; some days of the week: each of the 127 sets of days
    at 24 times (each hour; every minute 0-59 among them); monthly: each day 1-31 at 24 times (each hour; every minute
    among them). For each plan: its times over the year as the service asks, and its next time asked at each of them
    and the second before; for one plan of each kind also its next time asked at 30 s past every minute of 2028."""
    plans = PLANS[kind]
    assert {p["minute"] for p in plans} == set(range(60))  # the range the docstring states
    assert kind == "hourly" or {p["hour"] for p in plans} == set(range(24))
    assert kind != "weekly" or len({tuple(p["days"]) for p in plans}) == 127
    assert kind != "monthly" or {p["day"] for p in plans} == set(range(1, 32))
    for plan, every_minute_at in [(plan, None) for plan in plans] + [(EVERY_MINUTE[kind], 30)]:
        trigger = Trigger({"schedule": {"cron": plan_cron(plan)}})
        wrong = disagreement(trigger, oracle(plan), due_from=YEAR_START - SECOND, every_minute_at=every_minute_at)
        assert wrong is None, f"{plan} ({plan_cron(plan)}): {wrong}"


@pytest.mark.invariant("SCH-5", every="its time, for a one-off time at each minute of 2028")
def test_a_one_off_time_is_its_only_time_for_every_minute_of_2028():
    """SCH-5's "its time" for One time: each minute of 2028 (2028-01-01 00:00 to 2028-12-31 23:59) as the time of a
    one-off schedule (as the editor saves it, "YYYY-MM-DD HH:MM"): over 2028 it is the schedule's only time, it is
    the next time a second before it, and there is none from it on."""
    when = YEAR_START
    while when < YEAR_END:
        trigger = Trigger({"schedule": {"at": f"{when:%Y-%m-%d %H:%M}"}})
        got = (trigger.due(YEAR_START - SECOND, YEAR_END - SECOND), trigger.next_after(when - SECOND),
               trigger.next_after(when))
        assert got == ([when], when, None), f"at {when:%Y-%m-%d %H:%M}: {got}"
        when += MINUTE


@pytest.mark.invariant("SCH-5", every="its time, for each Every choice of the editor (1 minute to 12 hours), asked at "
                       "every minute of 2028")
@pytest.mark.invariant("SCH-7", every="each Every choice of the editor (1 minute to 12 hours), from when first seen, "
                       "asked at every minute of 2028")
@pytest.mark.parametrize("every", EVERY_CHOICES)
def test_an_every_n_schedule_has_its_times_every_n_from_when_first_seen_through_2028(every):
    """SCH-7, and SCH-5's "its time", for each Every choice of the Schedule block: first seen by the service at
    2028-06-15 13:47:14, its times are that moment plus or minus whole multiples of N (not on the clock's :00), over
    2028 (2028-01-01 00:00:00 to 2028-12-31 23:59:59) as the service counts on from one of them, its next time asked
    at each of them and the second before, and at second 14 of every minute of 2028 (so at each time itself too)."""
    seen = dt.datetime(2028, 6, 15, 13, 47, 14)
    n = Every(every).interval
    times, t = [], seen
    while t >= YEAR_START - n:
        t -= n
    while t < YEAR_END + 2 * n:
        times.append(t)
        t += n
    before_2028 = max(t for t in times if t < YEAR_START)
    wrong = disagreement(Trigger({"schedule": {"every": every}}), times, due_from=before_2028, start=seen,
                         every_minute_at=14)
    assert wrong is None, f"every {every}: {wrong}"


# --- SCH-4, SCH-6 to SCH-9, SCH-11, SCH-12, SCH-16: generated schedules, settings, check times and what you do -------
# The service's loop (Scheduler.tick at a given clock time, as `taskloom scheduler` runs it) over generated scheduled
# flows, Max concurrent runs, and check times with gaps: a second or so apart while it is on, minutes while the PC
# sleeps or the service is off. Each run's process is replaced at the process boundary by a FakeRun (real processes are
# too slow for hundreds of examples). It loads for a number of the checks to come (its flow's `loads`; 0: it has loaded
# by the end of the check that started it); then, as `taskloom run` does, one whose command line says --if-scheduled
# claims what that names (claim_its_time: the real claim, made from its command line as the real run makes it, on the
# simulation's clock, a service being on while it holds the folder's lock) and begins only if it has it, else it records
# nothing and ends with NOT_DUE; one whose command line claims nothing begins (never before its time). A run that begins
# records itself in the run history (its process, the moment it began, its Schedule block and schedule, that the
# service started it) and says it began and succeeded; it has finished when it has been checked on for the n-th time
# since (its flow's `lasts`), and records its end. As the system tells it (run_alive), its process is going until then.
# A run whose process has loaded at a check begins just before that check reads the run history, or just after
# (`after_check`: either can happen). A time has come by the service's first check at or after it (the PC may have slept
# meanwhile: it then wakes with the service). Between two checks, one of these may happen (each step's event):
# - "restart": the service stops (a crash, a PC restart) just after its last check, and a new Scheduler for the same
#   Taskloom folder starts at the next check. The runs going finish while it is off, or go on under the new one
#   (`outlive`: either can happen); a run still loading goes on, and claims under the new service, which may have
#   started a run for that time too: of two loaded at the same check, the older claims first, or the newer
#   (`older_first`: either can happen);
# - "saved": as a restart, and while no service is on (halfway to the next check) you save a flow with its first
#   Schedule block's timing changed, as the editor saves it (the flow file, then forget_schedules);
# - "by hand": halfway to the next check, you start a run of a flow yourself: it begins at once (it records itself in
#   the run history, with no schedule) and goes on for a number of the service's checks;
# - "off and on": halfway to the next check, you turn the service off (the dialog lists the flows with runs going:
#   runs_going; you choose Stop now or Let it finish for each; Proceed: request_turn_off) and at once on again; the
#   service checks next at the next check ("not seen"), or once in between ("seen": if that check ends it, a new one
#   starts as you turn it on), or no service runs as you click Proceed ("no service": it stopped just after its last
#   check, as for a restart, and one starts at the next check).
# The services' notices are kept, with the clock time of the check that told them; each moment (a process starting, a
# run beginning, a process ending, a check, a Proceed) is kept with the clock time then.

@dataclass
class Started:
    """A run's process the service started: its flow, Schedule block and time, the schedule as that block had it then
    (((kind, value), misfire)) and which of the block's schedules that is (0: as the scenario set it up; 1, 2, ...: as
    you saved it since), the life of the service that started it, whether it claims what its command line names
    (--if-scheduled), and the order (among all starts, beginnings and ends) in which its process started, its run
    began (None: not, or not yet) and it was seen finished."""
    life: int
    flow: str
    block: str
    as_of: dt.datetime
    began: int
    schedule: tuple = (("cron", "* * * * *"), "run_once")
    version: int = 0
    claims: bool = False
    begun: int | None = None
    ended: int | None = None

    def going_at(self, other: "Started") -> bool:
        """Going when `other` started. For the service that started a process, its run counts from that start (it
        counts it as going from then); for a later service, from when it began. So of two runs of different services,
        the earlier service's run counts from when it began, and the later one's from its process's start. A process
        whose run never began is no run."""
        if self.begun is None or other.begun is None:
            return False
        start = self.began if self.life >= other.life else self.begun
        at = other.began if other.life >= self.life else other.begun
        return start < at and (self.ended is None or self.ended > at)

    def alive_at(self, other: "Started") -> bool:
        """Its process was going when the process of `other` started (whichever service started it, begun or not)."""
        return self.began < other.began and (self.ended is None or self.ended > other.began)


class FakeRun:
    """Stands in for `taskloom run ...` at the process boundary (see above)."""

    def __init__(self, simulation, args):
        self.simulation, self.args, self.returncode = simulation, args, None
        flow = Path(args[args.index("run") + 1]).stem
        block = args[args.index("--trigger") + 1]
        versions = simulation.versions[(flow, block)]
        self.record = Started(simulation.life, flow, block, start_at_clock(args[args.index("--start-at") + 1]),
                              simulation.moment(), schedule=versions[-1][1], version=len(versions) - 1,
                              claims="--if-scheduled" in args)
        simulation.started.append(self.record)
        simulation.fakes.append(self)
        self.pid = 2 ** 30 + len(simulation.started)  # (no process has it)
        self.loading = simulation.loads[flow]  # (the checks to come before it has loaded)
        self.checks_left = simulation.lasts[flow]
        self.run_id = None
        self.stdin, self.stdout = io.StringIO(), Said()  # (what the service asks of it, and what it says)

    def begin(self, clock: dt.datetime):
        """Its run begins, with this PC's clock showing `clock`: it records itself in the run history as `taskloom
        run` does (History.start_run: its process, the moment it began, its Schedule block, and that the scheduler
        service started it, for the schedule its command line names), and says so."""
        simulation, args = self.simulation, self.args
        flow_path = real_path(Path(args[args.index("run") + 1]))
        schedule = args[args.index("--scheduled") + 1] if "--scheduled" in args[:-1] else None
        self.run_id = simulation.history.start_run(self.record.flow, str(flow_path), {}, clock.isoformat(),
                                                   started_by=self.record.block, schedule=schedule)
        simulation.db.execute("UPDATE runs SET pid=?, began=? WHERE id=?", (self.pid, clock.timestamp(), self.run_id))
        self.record.begun = simulation.moment()
        self.stdout.say(json.dumps({"event": "run_started", "run_id": self.run_id}) + "\n",
                        json.dumps({"event": "run_finished", "status": "success"}) + "\n")

    def reach_its_time(self, clock: dt.datetime):
        """It has loaded, with this PC's clock showing `clock` (its time has come), and claims."""
        why = claim_its_time(self.args, self.simulation.home, clock)
        if why:
            self.stdout.say(f"not begun: {why}\n")
            self.end(NOT_DUE)
        else:
            self.begin(clock)

    def poll(self):
        if self.returncode is None and self.record.begun is not None:
            self.checks_left -= 1
            if self.checks_left <= 0:
                self.end(0)
        return self.returncode

    def kill(self):
        if self.returncode is None:
            self.end(-9)

    terminate = kill

    def end(self, code: int):
        self.returncode = code
        self.record.ended = self.simulation.moment()
        if self.run_id is not None and code == 0:  # (its run records its end)
            self.simulation.history({"event": "run_finished", "run_id": self.run_id,
                                     "ts": self.simulation.clock.isoformat(), "status": "success"})
        self.stdout.end()


class HandRun:
    """A run of a flow you started yourself (Run in the editor, `taskloom run` in a terminal): it begins at once,
    recording itself in the run history as `taskloom run` does (its process, the moment it began; no schedule), goes
    on until it has been checked on `lasts` times by the service's checks, and records its end."""

    def __init__(self, simulation, flow: str, path: Path, lasts: int):
        self.simulation, self.flow, self.checks_left = simulation, flow, lasts
        self.pid = 2 ** 29 + len(simulation.by_hand)  # (no process has it)
        self.run_id = simulation.history.start_run(flow, str(real_path(path)), {}, simulation.clock.isoformat())
        simulation.db.execute("UPDATE runs SET pid=?, began=? WHERE id=?",
                              (self.pid, simulation.clock.timestamp(), self.run_id))
        self.begun, self.ended = simulation.moment(), None
        simulation.by_hand.append(self)

    def checked(self):
        """The service has checked once more: it ends once it has been checked on `lasts` times."""
        if self.ended is None:
            self.checks_left -= 1
            if self.checks_left <= 0:
                self.ended = self.simulation.moment()
                self.simulation.history({"event": "run_finished", "run_id": self.run_id,
                                         "ts": self.simulation.clock.isoformat(), "status": "success"})

    def going_at(self, moment: int) -> bool:
        return self.begun < moment and (self.ended is None or self.ended > moment)

    def going_then(self, clocks: dict, when: dt.datetime) -> bool:
        return clocks[self.begun] <= when and (self.ended is None or clocks[self.ended] >= when)


@dataclass
class Off:
    """You turned the service off: the moment you clicked Proceed (with this PC's clock then), your choice for each
    flow listed ({flow: "finish" | "stop"}), the clock time of the service's last check before it (None: none), and
    the moment you turned it on again."""
    moment: int
    clock: dt.datetime
    choices: dict
    handled: dt.datetime | None
    on: int | None = None


@dataclass
class Simulation:
    home: Path  # the Taskloom folder
    lasts: dict  # flow -> how many of the service's checks its runs last
    loads: dict  # flow -> how many checks its runs' processes take to load (0: they begin by the end of the check)
    older_first: bool = False  # (see reach_times)
    after_check: bool = False  # (see move_on)
    outlive: bool = False  # the runs going when the service stops go on (else they finish while it is off)
    history: History | None = None  # the run history, as runs record themselves in it
    db: sqlite3.Connection | None = None
    clock: dt.datetime | None = None  # this PC's clock: the time of the latest check (or of what you do)
    life: int = 0
    moments: itertools.count = field(default_factory=itertools.count)
    started: list = field(default_factory=list)
    fakes: list = field(default_factory=list)
    by_hand: list = field(default_factory=list)  # the runs you started yourself (HandRun)
    offs: list = field(default_factory=list)  # each time you turned the service off (Off)
    paths: dict = field(default_factory=dict)  # flow -> its file
    specs: dict = field(default_factory=dict)  # flow -> its spec as it is now (see flow_file)
    # (flow, block) -> its schedules: (when you saved it, None as the scenario set it up; ((kind, value), misfire))
    versions: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)  # (life, clock time, moment) of each check but those while turning off
    clocks: dict = field(default_factory=dict)  # moment -> this PC's clock then
    notices: list = field(default_factory=list)  # (clock time, title, message) of what the services told

    def moment(self) -> int:
        """The next moment (in the order of starts, beginnings, ends, checks and Proceeds), at this PC's clock now."""
        moment = next(self.moments)
        self.clocks[moment] = self.clock
        return moment

    def told(self, title: str, message: str):
        self.notices.append((self.clock, title, message))

    def came(self, run: Started, latest: bool = False) -> dt.datetime:
        """The check at which the run's time came: the first at or after it, which for a time missed is the first
        after it. A run still waiting its turn when the service stops is saved and started by the service after it
        (SCH-16): it came at that check of the earlier one. A time for which a run's process did not begin may come
        again; at the latest (`latest`) at the first check after every such process ended, and never after the check
        that started the run."""
        checks = self.checks
        tried = [r for r in self.started if (r.flow, r.block, r.as_of) == (run.flow, run.block, run.as_of)
                 and r.begun is None and r.began < run.began]
        if tried and latest:
            ends = [r.ended for r in tried]
            started = max((t for _, t, moment in checks if moment < run.began), default=checks[0][1])
            later = [t for _, t, moment in checks if None not in ends and moment > max(ends) and t >= run.as_of]
            return min(later + [started])
        come = [t for _, t, _ in checks if t >= run.as_of]
        assert come, (f"{described(run)}: its process started at {self.clocks[run.began]:%H:%M:%S.%f}, before its time, "
                      f"and no check came after it (SCH-5: no run's process starts before its time)")
        return min(come)

    def counts_from(self, flow: str, block: str, version: int) -> dt.datetime:
        """Where one of a Schedule block's schedules counts from: the first check, for the one the scenario set up;
        for one you saved since, your save, for a time on the clock (its times after the save come, also while no
        service is on), and the first check after it for Every N (it counts from when the service first sees it)."""
        since, ((kind, _), _) = self.versions[(flow, block)][version]
        if since is None:
            return self.checks[0][1]
        return since if kind == "cron" else min(t for _, t, _ in self.checks if t >= since)

    def move_on(self, now: dt.datetime | None):
        """The run processes reach this check (at `now`; None: as time goes on, with no check): those loading load, and
        one the service asked to stop (on its stdin) before it began ends without beginning. Those loaded begin
        (claiming, reach_times) before the service's check reads the run history, or, with `after_check`, just after
        that check (begun)."""
        for fake in self.fakes:
            if fake.returncode is None and fake.loading > 0:
                fake.loading -= 1
            if fake.returncode is None and fake.record.begun is None and "cancel" in fake.stdin.getvalue():
                fake.end(130)  # (asked to stop before it began: it does not begin, and records nothing)
        if not self.after_check or now is None:
            self.begun(now)

    def begun(self, now: dt.datetime | None):
        """The runs loaded begin (see begin_loaded and reach_times)."""
        self.begin_loaded(now)
        self.reach_times(now)

    def begin_loaded(self, now: dt.datetime | None):
        """The runs whose processes have loaded and that claim nothing begin once their time (--start-at) has come by
        `now` (None: each one, as time goes on): a run never begins before it."""
        for fake in self.fakes:
            if (fake.returncode is None and fake.loading == 0 and not fake.record.claims and fake.record.begun is None
                    and (now is None or fake.record.as_of <= now)):
                fake.begin(max(self.clock, fake.record.as_of))

    def reach_times(self, now: dt.datetime | None, before: bool = False):
        """The runs whose processes have loaded and that claim, whose time has come by `now` (before it, with
        `before`; None: each one, as time goes on), claim, in the order of their times; of two for the same time, the
        one an earlier service started claims first if `older_first`, else the one the service now on started."""
        come = [fake for fake in self.fakes if fake.record.claims and fake.record.begun is None
                and fake.returncode is None and fake.loading == 0
                and (now is None or fake.record.as_of < now or (fake.record.as_of == now and not before))]
        order = (lambda f: f.record.life) if self.older_first else (lambda f: f.record.life != self.life)
        for fake in sorted(come, key=lambda f: (f.record.as_of, order(f), f.record.began)):
            fake.reach_its_time(fake.record.as_of if now is None else now)

    def alive(self, pid: int, began: float | None) -> bool:
        """Whether a run's process is still going, as the system tells it (launch.run_alive): a stand-in's, or a run
        you started yourself, until it has ended."""
        fakes = [fake for fake in self.fakes if fake.pid == pid]
        if fakes:
            return fakes[0].returncode is None
        yours = [run for run in self.by_hand if run.pid == pid]
        return yours[0].ended is None if yours else run_alive(pid, began)

    def earlier(self) -> list:
        """The processes an earlier service started that are still going: none of this service's checks polls them."""
        return [fake for fake in self.fakes if fake.record.life < self.life and fake.returncode is None]

    def save(self, i: int, schedule: tuple):
        """You save flow i (of those there are) with its first Schedule block's timing changed to `schedule`, as the
        editor saves it: the flow file, then what the scheduler keeps of its schedules (forget_schedules), at this PC's
        clock. The same timing again changes nothing."""
        flow = f"flow{i % len(self.paths)}"
        if self.versions[(flow, "s0")][-1][1][0] == schedule[0]:
            return
        self.specs[flow] = {**self.specs[flow], "schedules": [schedule] + self.specs[flow]["schedules"][1:]}
        path = self.paths[flow]
        path.write_text(flow_file(self.specs[flow]), encoding="utf-8")
        with clock_shows(self.clock):
            forget_schedules(Home(self.home), path, load_flow(path).triggers)
        self.versions[(flow, "s0")].append((self.clock, schedule))

    def proceed(self, stops: list[bool]):
        """You turn the service off: the dialog lists the flows with runs going (runs_going), and for flow i you choose
        Stop now if stops[i], else Let it finish (the default); you click Proceed (request_turn_off)."""
        choices = {spelled: "stop" if stops[int(name.removeprefix("flow")) % len(stops)] else "finish"
                   for spelled, name in runs_going(Home(self.home))}
        handled = max((t for _, t, _ in self.checks), default=None)
        names = {str(path): flow for flow, path in self.paths.items()}
        self.offs.append(Off(self.moment(), self.clock, {names.get(k, Path(k).stem): v for k, v in choices.items()},
                             handled))
        request_turn_off(Home(self.home), choices)

    def turn_on(self):
        """You turn the service on again (in the editor, or on its tray icon)."""
        set_paused(Home(self.home), False)
        self.offs[-1].on = self.moment()


SCHEDULE = st.tuples(st.one_of(st.tuples(st.just("cron"), st.sampled_from(["* * * * *", "*/2 * * * *", "*/5 * * * *",
                                                                           "0 * * * *"])),
                               st.tuples(st.just("every"), st.sampled_from(["1 minute", "2 minutes", "3 minutes"]))),
                     st.sampled_from(["skip", "run_once", "catch_up"]))
FLOWS = st.lists(st.fixed_dictionaries({"schedules": st.lists(SCHEDULE, min_size=1, max_size=2),
                                        "overlap": st.sampled_from(["skip", "queue", "allow"]),
                                        "lasts": st.integers(1, 4), "loads": st.integers(0, 2)}),
                 min_size=1, max_size=3)
# What happens between two checks (see above): (event, its details).
EVENTS = st.one_of(st.just(("restart",)),
                   st.tuples(st.just("saved"), st.integers(0, 2), SCHEDULE),
                   st.tuples(st.just("by hand"), st.integers(0, 2), st.integers(1, 4)),
                   st.tuples(st.just("off and on"), st.lists(st.booleans(), min_size=3, max_size=3),
                             st.sampled_from(["not seen", "seen", "no service"])))
# (milliseconds to the next check, what happens before it: nothing, or an event)
STEPS = st.lists(st.tuples(st.one_of(st.integers(200, 75_000), st.integers(120_000, 1_200_000)),
                           st.one_of(st.none(), st.none(), st.none(), EVENTS)), min_size=1, max_size=20)


def scenarios(limits=(None, 1, 2, 3)):
    """Also `older_first`: whether, of two runs for the same time that have loaded at the same check, one started by
    a service since stopped and one by the service now on, the older one claims it first (either can happen);
    `after_check`: whether a run whose process has loaded at a check begins just before that check reads the run
    history, or just after it (either can happen); and `outlive`: whether the runs going as the service stops go on
    under the new one, or finish while it is off."""
    return st.fixed_dictionaries({"flows": FLOWS, "limit": st.sampled_from(limits), "steps": STEPS,
                                  "first_check": st.integers(0, 3_599_999), "older_first": st.booleans(),
                                  "after_check": st.booleans(), "outlive": st.booleans()})


GENERATED = settings(max_examples=200, deadline=None, database=None, suppress_health_check=[HealthCheck.too_slow])


@contextlib.contextmanager
def on(env):
    """The scheduler service is on, as `taskloom scheduler` makes it: it has taken the Taskloom folder's lock before
    making its Scheduler (made inside this), and holds it until it stops (at the end of this)."""
    lock = acquire_lock(Home(env.home))
    try:
        yield
    finally:
        unlock(lock)


def flow_file(spec: dict) -> str:
    """A flow as the editor saves it: a Schedule block per schedule (s0, s1), each starting a block of its own."""
    blocks, edges = {}, []
    for i, ((kind, value), misfire) in enumerate(spec["schedules"]):
        blocks[f"s{i}"] = {"type": "trigger.schedule", "config": {kind: value, "misfire": misfire}}
        blocks[f"work{i}"] = {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}}
        edges.append(f"s{i}.start -> work{i}.after")
    return yaml.safe_dump({"overlap": spec["overlap"], "blocks": blocks, "edges": edges}, sort_keys=False)


def simulate(root: Path, scenario: dict) -> Simulation:
    home = Home(root / "home")
    home.root.mkdir(parents=True)
    if scenario["limit"]:
        (home.root / "settings.yaml").write_text(f"max_concurrent_runs: {scenario['limit']}\n", encoding="utf-8")
    simulation = Simulation(home.root, lasts={}, loads={}, older_first=scenario["older_first"],
                            after_check=scenario["after_check"], outlive=scenario["outlive"])
    for i, spec in enumerate(scenario["flows"]):
        path = root / f"flow{i}.yaml"
        path.write_text(flow_file(spec), encoding="utf-8")
        home.set_scheduled(path, True)
        simulation.lasts[path.stem], simulation.loads[path.stem] = spec["lasts"], spec["loads"]
        simulation.paths[path.stem], simulation.specs[path.stem] = path, spec
        for j, schedule in enumerate(spec["schedules"]):
            simulation.versions[(path.stem, f"s{j}")] = [(None, schedule)]
    now = at("06:00:00") + dt.timedelta(milliseconds=scenario["first_check"])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subprocess, "Popen", lambda args, *rest, **kwargs: FakeRun(simulation, args))
        patch.setattr("taskloom.scheduler.run_alive", simulation.alive)
        lock = acquire_lock(home)  # the service is on: it holds the folder's lock, as `taskloom scheduler` does
        simulation.history, simulation.db = History(home.history_db), history_connect(home.history_db)
        try:
            service = Scheduler(home, simulation.told)

            def stops():  # the service stops (not turned off by you) just after its last check
                nonlocal lock
                unlock(lock)
                lock = None
                for fake in simulation.fakes:
                    if fake.returncode is None and fake.record.begun is not None and not simulation.outlive:
                        fake.end(0)  # (its runs going finish while it is off)
                simulation.reach_times(simulation.clock, before=True)  # (no service is on)

            def starts():  # a new service starts (you turned it on, or it starts by itself)
                nonlocal lock, service
                simulation.life += 1
                lock = acquire_lock(home)
                service = Scheduler(home, simulation.told)

            for gap, event in [(0, None)] + scenario["steps"]:
                kind = event and event[0]
                halfway, now = now + dt.timedelta(milliseconds=gap // 2), now + dt.timedelta(milliseconds=gap)
                if kind in ("restart", "saved") or (kind == "off and on" and event[2] == "no service"):
                    stops()
                simulation.clock = halfway
                if kind == "saved":
                    simulation.save(event[1], event[2])
                elif kind == "by hand":
                    flow = f"flow{event[1] % len(simulation.paths)}"
                    HandRun(simulation, flow, simulation.paths[flow], event[2])
                elif kind == "off and on":
                    simulation.proceed(event[1])
                    if event[2] == "seen":  # the service checks once, and sees it (it plans no time then: checks)
                        service.tick(halfway)
                        if service.off:  # (nothing it lets finish is going: it has ended)
                            unlock(lock)
                            lock = None
                    simulation.turn_on()
                    if lock is None and event[2] == "seen":
                        starts()
                simulation.clock = now
                if lock is None:
                    starts()
                simulation.move_on(now)
                simulation.checks.append((simulation.life, now, simulation.moment()))
                service.tick(now)
                simulation.begun(now)  # (by the end of this check)
                for fake in simulation.earlier():
                    fake.poll()
                for run in simulation.by_hand:
                    run.checked()
            for _ in range(5000):  # the runs going and those waiting their turn all finish (no new time comes)
                simulation.move_on(None)
                if not (service.runs or any(service.queued.values()) or service.waiting or simulation.earlier()
                        or any(run.ended is None for run in simulation.by_hand)):
                    break
                service.tick(now)
                simulation.begin_loaded(None)
                for fake in simulation.earlier():
                    fake.poll()
                for run in simulation.by_hand:
                    run.checked()
            else:
                raise AssertionError("runs still going or waiting after 5000 checks")
        finally:
            if lock is not None:
                unlock(lock)
            for fake in simulation.fakes:
                if fake.returncode is None:
                    fake.end(-9)
            simulation.history.close()
            simulation.db.close()
    return simulation


def setting(scenario: dict, run: Started) -> tuple[str, str, str]:
    """(overlap, the kind of its schedule, its misfire) of the flow and the schedule of `run`."""
    spec = scenario["flows"][int(run.flow.removeprefix("flow"))]
    (kind, _), misfire = run.schedule
    return spec["overlap"], kind, misfire


def described(run: Started) -> str:
    return f"{run.flow}/{run.block}{f' (saved #{run.version})' if run.version else ''} {run.as_of:%H:%M:%S}"


# An Every 1 minute schedule first seen at 06:00:30: its run for then starts at once, and the service stops and starts
# again while its process loads (a check). Then the run's process has loaded: it begins (or does not, as the service
# that started it is not the one on), lasting 4 checks; at 06:01:30.2 the flow's next time comes (Overlap skip, Max
# concurrent runs 1).
LOADING_ACROSS_RESTART = {"flows": [{"schedules": [(("every", "1 minute"), "run_once")], "overlap": "skip", "lasts": 4,
                                     "loads": 1}],
                          "limit": 1, "first_check": 30_000, "steps": [(200, ("restart",)), (60_000, None)],
                          "older_first": False, "after_check": False, "outlive": False}
# A flow with two Every 1 minute schedules (Overlap queue), first seen at 06:00:30: its first schedule's run for then
# starts at once, and the second's waits behind it. The service stops and starts again while that run's process
# loads; the new service's first check, at 06:00:30.2, starts the waiting run (it reads the run history before the
# loading run has begun, and so finds no run of the flow going); the loading run's process then has loaded, and begins
# (or does not, as the service that started it is not the one on). Max concurrent runs 1.
LOADING_AS_THE_SERVICE_CHECKS = {"flows": [{"schedules": [(("every", "1 minute"), "run_once"),
                                                          (("every", "1 minute"), "run_once")],
                                            "overlap": "queue", "lasts": 4, "loads": 1}],
                                 "limit": 1, "first_check": 30_000, "steps": [(200, ("restart",)), (200, None)],
                                 "older_first": False, "after_check": True, "outlive": False}
# An Every 1 minute Catch up schedule first seen at 06:00 (its run for then starts at once); the service stops and
# starts again two minutes later while that run's process loads. The new service's first check catches up 06:01 and
# 06:02 (it reads the run history before the loading run has begun); that run then begins (or does not).
CAUGHT_UP_AS_THE_SERVICE_CHECKS = {"flows": [{"schedules": [(("every", "1 minute"), "catch_up")], "overlap": "skip",
                                              "lasts": 1, "loads": 1}],
                                   "limit": None, "first_check": 0, "steps": [(120_000, ("restart",))],
                                   "older_first": False, "after_check": True, "outlive": False}
CAUGHT_UP_LOADING = {**LOADING_ACROSS_RESTART, "flows": [{"schedules": [(("every", "1 minute"), "catch_up")],
                                                          "overlap": "skip", "lasts": 4, "loads": 1}]}
# A run's process loads (06:00, an Every 1 minute schedule seen then) as you start a run of the flow yourself, at
# 06:00:00.25; the scheduled run's process has loaded at the next check, at 06:00:00.5, and claims (SCH-11).
BY_HAND_WHILE_LOADING = {"flows": [{"schedules": [(("every", "1 minute"), "run_once")], "overlap": "skip", "lasts": 1,
                                    "loads": 1}],
                         "limit": None, "first_check": 0,
                         "steps": [(500, ("by hand", 0, 3)), (1_000, None), (1_000, None), (1_000, None)],
                         "older_first": False, "after_check": False, "outlive": False}
# A run's process loads (06:00, as above) as you turn the service off and on again, at 06:00:00.25: before the service
# checks, or after it has checked once (SCH-8).
OFF_AND_ON_WHILE_LOADING = [{**BY_HAND_WHILE_LOADING, "steps": [(500, ("off and on", [False] * 3, seen)), (1_000, None)]}
                            for seen in ("not seen", "seen")]
# A Catch up flow (Overlap queue) seen at 06:00, its run for then going (4 checks); at 06:03 its runs for 06:01, 06:02
# and 06:03 wait behind it. You turn the service off with Stop now for it, and on again: before the service checks, or
# with no service running as you click Proceed (it stopped just after 06:03) (SCH-4).
STOP_NOW_THEN_ON = [{"flows": [{"schedules": [(("every", "1 minute"), "catch_up")], "overlap": "queue", "lasts": 4,
                                "loads": 0}],
                     "limit": None, "first_check": 0,
                     "steps": [(180_000, None), (500, ("off and on", [True] * 3, how)), (1_000, None)],
                     "older_first": False, "after_check": False, "outlive": True}
                    for how in ("not seen", "no service")]
# A Catch up schedule (every minute, Overlap queue) seen at 06:00; the service stops, and while it is off you save the
# flow with the schedule changed (at 06:01:40): every 2 minutes on the clock, or every 2 minutes from when the service
# first sees it; the service is back at 06:03:20. On the clock, 06:02 came after the save while the service was off: a
# missed time, which Catch up runs (SCH-9, SCH-10); Every N counts from 06:03:20, when the service first sees it
# (SCH-7).
SAVED_WHILE_OFF = [{"flows": [{"schedules": [(("cron", "* * * * *"), "catch_up")], "overlap": "queue", "lasts": 1,
                               "loads": 0}],
                    "limit": None, "first_check": 0, "steps": [(200_000, ("saved", 0, (timing, "catch_up")))],
                    "older_first": False, "after_check": False, "outlive": False}
                   for timing in (("cron", "*/2 * * * *"), ("every", "2 minutes"))]
# Found on CI (Linux). Every minute, Misfire Skip: flow0 (its program loads for a check) and flow1 (two schedules,
# Overlap queue), checked every 2 minutes. At 06:04:00.2 flow0's run for 06:04 is on time, but it is saved as waiting
# for a slot behind flow1's waiting run, with no Max concurrent runs; its process starts at once. You start flow0
# yourself at 06:05:00.2, so its claim at 06:06:00.2 is refused. Then it must not run, 2 minutes late (SCH-9).
SKIP_REFUSED_BEHIND_A_WAITING_RUN = {
    "flows": [{"schedules": [(("cron", "* * * * *"), "skip")], "overlap": "skip", "lasts": 1, "loads": 1},
              {"schedules": [(("cron", "* * * * *"), "skip"), (("cron", "* * * * *"), "skip")], "overlap": "queue",
               "lasts": 1, "loads": 0}],
    "limit": None, "steps": [(200, None), (120_000, None), (120_000, None), (120_000, ("by hand", 0, 1))],
    "first_check": 0, "older_first": False, "after_check": False, "outlive": False}
# Found on CI (Windows). The same, with the claim refused as you turn the service off and on again before it checks
# (06:12:00.001): flow1's run for 06:11, saved as waiting for a slot behind flow0's, must not run at 06:13 (SCH-9).
SKIP_REFUSED_AFTER_OFF_AND_ON = {
    "flows": [{"schedules": [(("cron", "* * * * *"), "skip"), (("cron", "* * * * *"), "skip")], "overlap": "queue",
               "lasts": 1, "loads": 0},
              {"schedules": [(("cron", "* * * * *"), "skip"), (("cron", "* * * * *"), "skip")], "overlap": "skip",
               "lasts": 1, "loads": 1}],
    "limit": None,
    "steps": [(200, None), (1874, None), (2999, None), (5690, None), (6067, None), (6091, None), (22901, None),
              (611059, None), (3120, ("saved", 0, (("cron", "* * * * *"), "skip"))),
              (120_000, ("off and on", [False, False, False], "not seen"))],
    "first_check": 0, "older_first": False, "after_check": False, "outlive": False}


@pytest.mark.invariant("SCH-6")
@GENERATED
@given(scenario=scenarios())
@example(scenario=LOADING_ACROSS_RESTART)
@example(scenario=LOADING_AS_THE_SERVICE_CHECKS)
def test_generated_each_due_time_starts_at_most_once(tmp_path_factory, scenario):
    """No two runs begin for the same time of a schedule (a run's process that did not begin, as it could not claim
    its time, is not a run)."""
    started = simulate(tmp_path_factory.mktemp("service"), scenario).started
    keys = [(run.flow, run.block, run.version, run.as_of) for run in started if run.begun is not None]
    assert len(keys) == len(set(keys)), f"began twice: {sorted(k for k in set(keys) if keys.count(k) > 1)}"


@pytest.mark.invariant("SCH-11")
@GENERATED
@given(scenario=scenarios())
@example(scenario=LOADING_ACROSS_RESTART)
@example(scenario=LOADING_AS_THE_SERVICE_CHECKS)
@example(scenario=BY_HAND_WHILE_LOADING)
@example(scenario={**BY_HAND_WHILE_LOADING, "flows": [{**BY_HAND_WHILE_LOADING["flows"][0], "overlap": "queue"}]})
def test_generated_two_runs_of_a_flow_go_at_once_only_if_its_overlap_is_allow(tmp_path_factory, scenario):
    """A scheduled run begins alongside another run of its flow only if its Overlap is allow: another scheduled run,
    or a run you started yourself that is going as it begins (also one you started while its program loaded)."""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)
    started = simulation.started
    for run in started:
        if setting(scenario, run)[0] != "allow":
            also = [described(r) for r in started if r.flow == run.flow and r.going_at(run)]
            assert not also, f"{described(run)} started while {also} went"
            yours = [f"your run of {h.flow} from {simulation.clocks[h.begun]:%H:%M:%S.%f}" for h in simulation.by_hand
                     if h.flow == run.flow and run.begun is not None and h.going_at(run.begun)]
            assert not yours, f"{described(run)} began while {yours} went"


@pytest.mark.invariant("SCH-12")
@GENERATED
@given(scenario=scenarios(limits=(1, 2, 3)))
@example(scenario=LOADING_ACROSS_RESTART)
@example(scenario=LOADING_AS_THE_SERVICE_CHECKS)
def test_generated_no_more_runs_go_at_once_than_max_concurrent_runs(tmp_path_factory, scenario):
    started = simulate(tmp_path_factory.mktemp("service"), scenario).started
    for run in started:
        going = [described(r) for r in started if r.going_at(run)]
        assert len(going) < scenario["limit"], f"{described(run)} started while {going} went"


@pytest.mark.invariant("SCH-12")
@GENERATED
@given(scenario=scenarios(limits=(1, 2, 3)))
def test_generated_runs_waiting_for_a_free_slot_start_in_the_order_they_came(tmp_path_factory, scenario):
    """A run whose time came at an earlier check starts before one whose time came later, unless it waits for its
    own flow's run going (Overlap queue, or a catch-up one at a time, or any Overlap but allow while a run of its flow
    you started yourself goes) rather than for a free slot: a process of its flow was going then (whichever service
    started it), or your run; or the later one started while the service was turning off, and the earlier one's flow
    was not one you let finish. (When a time came again, as a run's process did not begin for it, is known only within
    bounds: the earlier run is taken at its latest, the later at its earliest.)"""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)
    started = simulation.started

    def turning_off(later: Started, flow: str) -> bool:
        return any(off.moment < later.began < off.on and off.choices.get(flow) != "finish" for off in simulation.offs)

    for later in started:
        for earlier in started:
            if (earlier.life == later.life and later.began < earlier.began
                    and simulation.came(earlier, latest=True) < simulation.came(later)):
                overlap, _, misfire = setting(scenario, earlier)
                waits_for_its_flow = (overlap != "allow" or misfire == "catch_up") and (
                    any(r.flow == earlier.flow and r.alive_at(later) for r in started)
                    or any(h.flow == earlier.flow and h.going_at(later.began) for h in simulation.by_hand))
                assert waits_for_its_flow or turning_off(later, earlier.flow), (
                    f"{described(earlier)} (came by {simulation.came(earlier, latest=True):%H:%M:%S}) waited for a "
                    f"free slot, and {described(later)} (came at {simulation.came(later):%H:%M:%S}) started before it")


@pytest.mark.invariant("SCH-9")
@GENERATED
@given(scenario=scenarios())
@example(scenario=CAUGHT_UP_LOADING)
@example(scenario=CAUGHT_UP_AS_THE_SERVICE_CHECKS)
def test_generated_catch_up_runs_go_oldest_first_one_at_a_time(tmp_path_factory, scenario):
    """Catch up's runs of a schedule start oldest first and never two at once (a process whose run did not begin is no
    run). With Overlap allow (a new time may go alongside a run going), this holds for the times missed: those that
    came together at one check, already past."""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)

    def missed_together(run: Started) -> tuple:
        came = simulation.came(run)
        return (came, run.as_of < came)

    runs = [r for r in simulation.started if r.begun is not None]
    for run in runs:
        overlap, _, misfire = setting(scenario, run)
        if misfire != "catch_up" or (overlap == "allow" and not missed_together(run)[1]):
            continue
        ours = [r for r in runs if (r.flow, r.block, r.version) == (run.flow, run.block, run.version)
                and (overlap != "allow" or missed_together(r) == missed_together(run))]
        newer = [described(r) for r in ours if r.began < run.began and r.as_of > run.as_of]
        assert not newer, f"{described(run)} started after the newer {newer}"
        also = [described(r) for r in ours if r.going_at(run)]
        assert not also, f"{described(run)} started while {also} went"


def times_of(schedule: tuple, first: dt.datetime, until: dt.datetime) -> list[dt.datetime]:
    """The times of a generated schedule ((kind, value), misfire) from where it counts from (`first`) to `until`,
    worked out from its plan: every N minutes from first sight, to the second (that moment first); a cron line at its
    minutes after `first` (each minute, every 2nd or 5th minute of the hour, or minute 0)."""
    (kind, value), _ = schedule
    if kind == "every":
        times, t, n = [], first.replace(microsecond=0), dt.timedelta(minutes=int(value.split()[0]))
        while t <= until:
            times.append(t)
            t += n
        return times
    step = {"* * * * *": 1, "*/2 * * * *": 2, "*/5 * * * *": 5, "0 * * * *": 60}[value]
    times, t = [], first.replace(second=0, microsecond=0) + MINUTE
    while t <= until:
        if t.minute % step == 0:
            times.append(t)
        t += MINUTE
    return times


def dropped_by_stop_now(simulation: Simulation, flow: str, t: dt.datetime) -> bool:
    """You chose Stop now for the flow as you turned the service off, after the time `t` had come at a check: its run
    for `t` was going, or waiting its turn, then (SCH-4: stopped, or dropped)."""
    return any(off.choices.get(flow) == "stop" and off.handled is not None and t <= off.handled
               for off in simulation.offs)


# A Catch up schedule whose runs wait for the flow's run going (Overlap queue), its runs' time lost if a process for it
# did not begin and nothing ran it after: the run of 06:00:30 still loading as the service stops and starts again.
NONE_LOST = [{**CAUGHT_UP_LOADING, "flows": [{**CAUGHT_UP_LOADING["flows"][0], "overlap": "queue"}]}]


@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-10")
@pytest.mark.invariant("SCH-11")
@pytest.mark.invariant("SCH-16")
@GENERATED
@given(scenario=scenarios())
@example(scenario=NONE_LOST[0])
@example(scenario=SAVED_WHILE_OFF[0])
@example(scenario=SAVED_WHILE_OFF[1])
@example(scenario={**OFF_AND_ON_WHILE_LOADING[0],
                   "flows": [{**OFF_AND_ON_WHILE_LOADING[0]["flows"][0], "schedules": [(("every", "1 minute"),
                                                                                        "catch_up")]}]})
def test_generated_a_catch_up_schedule_whose_runs_wait_their_turn_loses_none_of_its_times(tmp_path_factory, scenario):
    """A Catch up schedule of a flow whose Overlap is queue or allow, as it is now (you may have saved it changed): a
    run begins for each of its times, from where it counts from (as the scenario set it up: the service's first check;
    saved on the clock: your save, also while the service was off; saved Every N: the first check after your save) to
    the last check. Missed ones are caught up (SCH-9); one that came while a run of the flow went (also one you started
    yourself) waits for it or goes alongside (SCH-11); runs waiting their turn when the service stopped start when it
    is back (SCH-16); also when a run's process for that time did not begin, as you turned the service off and on
    again; none but those you chose Stop now for (SCH-4)."""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)
    last = simulation.checks[-1][1]
    for (flow, block), versions in simulation.versions.items():
        spec, version = scenario["flows"][int(flow.removeprefix("flow"))], len(versions) - 1
        schedule = versions[-1][1]
        if spec["overlap"] == "skip" or schedule[1] != "catch_up":
            continue
        began = {r.as_of for r in simulation.started if (r.flow, r.block, r.version) == (flow, block, version)
                 and r.begun is not None}
        lost = [f"{t:%H:%M:%S}" for t in times_of(schedule, simulation.counts_from(flow, block, version), last)
                if t not in began and not dropped_by_stop_now(simulation, flow, t)]
        assert not lost, f"{flow}/{block}{f' (saved #{version})' if version else ''}: no run began for {lost}"


@pytest.mark.invariant("SCH-11")
@GENERATED
@given(scenario=scenarios())
@example(scenario=BY_HAND_WHILE_LOADING)
def test_generated_a_time_is_skipped_only_if_a_run_of_its_flow_goes_at_that_time(tmp_path_factory, scenario):
    """Overlap skip skips a time only while a run of its flow goes (also one you started yourself), or waits its turn
    (which the service counts as going): for each time you are told was skipped ("Run skipped"), one of the flow's times
    due at that check (since the check before it, or one whose run's process did not begin) that no run began for had a
    run of the flow going, or waiting its turn, at that time (at that check, for a time that had passed)."""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)
    clocks = simulation.clocks

    def going_or_waiting(run: Started, when: dt.datetime) -> bool:
        """The run's process was going at `when`, or its time had come and it waited its turn then."""
        return (clocks[run.began] <= when and (run.ended is None or clocks[run.ended] >= when)
                or simulation.came(run) <= when < clocks[run.began])

    def dropped_later(flow: str, clock: dt.datetime) -> bool:
        """You saved the flow changed, or chose Stop now for it, after `clock`: its runs waiting then may have been
        dropped without beginning, so whether they waited cannot be told here."""
        return (any(since is not None and since >= clock for (name, _), versions in simulation.versions.items()
                    if name == flow for since, _ in versions)
                or any(off.choices.get(flow) == "stop" and off.clock >= clock for off in simulation.offs))

    told = {}
    for clock, title, message in simulation.notices:
        if title == "Run skipped" and not dropped_later(message.split(":")[0], clock):
            told.setdefault((clock, message.split(":")[0]), []).append(message)
    for (clock, flow), messages in told.items():
        before = max((t for _, t, _ in simulation.checks if t < clock), default=simulation.checks[0][1] - MINUTE)
        ours = [r for r in simulation.started if r.flow == flow]
        # (an Every N schedule first seen at this check has its first time then, to the second: SCH-7; that can be
        # the second of the check before it)
        due = {(block, version, t) for (name, block), versions in simulation.versions.items() if name == flow
               for version, (_, schedule) in enumerate(versions)
               for first in [simulation.counts_from(flow, block, version)]
               for t in times_of(schedule, first, clock)
               if t > before or (first > before and t == first.replace(microsecond=0))}
        due |= {(r.block, r.version, r.as_of) for r in ours if r.begun is None and r.as_of <= clock}
        skipped = sorted((block, version, t) for block, version, t in due
                         if not any((r.block, r.version, r.as_of) == (block, version, t) and r.begun is not None
                                    for r in ours))
        going_then = [(block, t) for block, version, t in skipped
                      if any((r.block, r.version, r.as_of) != (block, version, t)
                             and going_or_waiting(r, max(t, clock)) for r in ours)
                      or any(h.flow == flow and h.going_then(clocks, max(t, clock)) for h in simulation.by_hand)]
        assert len(going_then) >= len(messages), (
            f"told at {clock:%H:%M:%S.%f} that {len(messages)} time(s) of {flow} were skipped, but of its times due "
            f"then that did not run, {[f'{b} {t:%H:%M:%S}' for b, _, t in skipped]}, only "
            f"{[f'{b} {t:%H:%M:%S}' for b, t in going_then]} had a run of {flow} going (or waiting) at that time; its "
            f"runs: "
            f"{[(described(r), f'{clocks[r.began]:%H:%M:%S.%f}', r.ended and f'{clocks[r.ended]:%H:%M:%S.%f}')
                for r in ours]}")


@pytest.mark.invariant("SCH-9")
@GENERATED
@given(scenario=scenarios())
@example(scenario=SKIP_REFUSED_BEHIND_A_WAITING_RUN)
@example(scenario=SKIP_REFUSED_AFTER_OFF_AND_ON)
def test_generated_with_misfire_skip_no_run_starts_more_than_2_minutes_after_its_time(tmp_path_factory, scenario):
    """Skip runs none of the times missed, and a time less than 2 minutes late still runs: with Misfire Skip, the
    service starts no run (its process) more than 2 minutes after its time, unless that run waited its turn: behind its
    flow's runs going (Overlap queue), for a free slot (Max concurrent runs), or in the line when a service before this
    one stopped (SCH-16: a service before it had started a process for that time, which did not begin). (A run whose
    process the service started in time and that begins later, its process still loading, is not one.)"""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)
    for run in simulation.started:
        overlap, _, misfire = setting(scenario, run)
        if misfire != "skip" or run.begun is None or overlap == "queue" or scenario["limit"] or any(
                (r.flow, r.block, r.version, r.as_of) == (run.flow, run.block, run.version, run.as_of)
                and r.life < run.life for r in simulation.started):
            continue
        late = simulation.clocks[run.began] - run.as_of
        assert late <= 2 * MINUTE, f"{described(run)}: its run began, its process started {late} after its time"


@pytest.mark.invariant("SCH-7")
@GENERATED
@given(scenario=scenarios())
@example(scenario=SAVED_WHILE_OFF[1])
def test_generated_every_n_runs_keep_the_rhythm_from_when_the_service_first_saw_them(tmp_path_factory, scenario):
    """Through sleeps, the service off and on again, and every Misfire and Overlap: an Every N schedule's runs are
    all at whole multiples of N after the first check (to the second) that saw it as it is (also one you saved while
    the service was off)."""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)
    for run in simulation.started:
        _, kind, _ = setting(scenario, run)
        if kind == "every":
            seen = simulation.counts_from(run.flow, run.block, run.version).replace(microsecond=0)
            n = Every(run.schedule[0][1]).interval
            assert (run.as_of - seen) % n == dt.timedelta(0), f"{described(run)}: not a multiple of {n} after {seen}"


@pytest.mark.invariant("SCH-8")
@GENERATED
@given(scenario=scenarios())
@example(scenario=OFF_AND_ON_WHILE_LOADING[0])
@example(scenario=OFF_AND_ON_WHILE_LOADING[1])
def test_generated_a_run_still_loading_as_you_turn_the_service_off_does_not_begin(tmp_path_factory, scenario):
    """A run whose program was still loading as you clicked Proceed does not begin, also when you turned the service
    on again before it had loaded (SCH-8). Only a run of a flow you let finish may begin, and only while the service is
    still off (a waiting run of a flow you let finish still runs, SCH-4). Its time is not lost: it follows Misfire once the service is on (a run for it may begin then,
    from a process started after you turned it on)."""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)
    for off in simulation.offs:
        began = [described(run) for run in simulation.started if run.began < off.moment
                 and run.begun is not None and run.begun > off.moment
                 and not (off.choices.get(run.flow) == "finish" and (off.on is None or run.begun < off.on))]
        assert not began, (f"you turned the service off at {off.clock:%H:%M:%S.%f} (choices {off.choices}), and "
                           f"{began}, whose program was loading then, began after it")


@pytest.mark.invariant("SCH-4")
@GENERATED
@given(scenario=scenarios())
@example(scenario=STOP_NOW_THEN_ON[0])
@example(scenario=STOP_NOW_THEN_ON[1])
def test_generated_stop_now_drops_the_flows_waiting_runs_for_good(tmp_path_factory, scenario):
    """Once you click Proceed with Stop now for a flow, its runs waiting their turn (and its runs still loading) never
    begin: also when you turn the service on again before it has looked, and when no service was running as you
    clicked Proceed. No run of that flow begins after Proceed for a time that had come by the service's last check
    before it."""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)
    for off in simulation.offs:
        began = [described(run) for run in simulation.started if off.choices.get(run.flow) == "stop"
                 and run.begun is not None and run.begun > off.moment and off.handled is not None
                 and run.as_of <= off.handled]
        assert not began, (f"you chose Stop now as you turned the service off at {off.clock:%H:%M:%S.%f}, and its "
                           f"runs {began}, whose time had come by {off.handled:%H:%M:%S.%f}, began after it")


# --- SCH-12: a run waiting for a free slot is not overtaken ------------------------------------------------------------

@pytest.mark.invariant("SCH-12")
def test_a_run_waiting_for_a_free_slot_starts_before_a_later_run_of_a_busy_flow(env):
    """Max concurrent runs 1. busy (Overlap queue) starts at 06:31; report's 06:31 run waits for the slot. busy's
    06:32 time comes while its 06:31 run still goes, so it waits for that run. When busy's 06:31 run ends, report's
    run, which came first, starts next; busy's 06:32 run after it."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    with on(env):
        scheduler, notes = service(env, {"busy.yaml": timed_flow(wait=2, overlap="queue"),
                                         "report.yaml": timed_flow("31 6 * * *")})
        scheduler.tick(at("06:30:00"))
        scheduler.tick(at("06:31:00"))
        scheduler.tick(at("06:32:00"))
        scheduler.wait_for_runs()

    assert [(r["flow"], r["when"], r["status"]) for r in runs(env)] == [
        ("busy", "06:31", "success"), ("report", "06:31", "success"), ("busy", "06:32", "success")], notes


# --- SCH-8: a schedule changed while its run's program loads -----------------------------------------------------------

def block_flow(config: dict) -> str:
    """A flow its Schedule block `hourly` (as the editor saves it, with `config` as its When) starts; its as-of time
    is the parameter `when`."""
    return yaml.safe_dump({
        "params": {"when": {"type": "datetime", "expr": "now()"}},
        "blocks": {"hourly": {"type": "trigger.schedule", "config": config},
                   "note": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}}},
        "edges": ["hourly.start -> note.after"],
    }, sort_keys=False)


@pytest.mark.invariant("SCH-8")
def test_a_run_still_loading_does_not_begin_if_its_schedule_is_changed_meanwhile(env):
    """Four flows due at the same minute; the service (on: this test holds its lock) starts their runs at that minute,
    and each run's program takes a few seconds to load (a block file slow to import). Meanwhile three are saved with
    their schedule changed: to another minute, with another minute added, and from every minute to every 2 minutes.
    Their runs do not begin: nothing is recorded or told for them. The unchanged one begins."""
    write(env.home / "blocks" / "slow_load.py", SLOW_LOAD)
    now = dt.datetime.now()
    due = now.replace(second=0, microsecond=0) + MINUTE
    if (due - now).total_seconds() < 15:  # room for the first check and its run (its program loads for 4 s)
        due += MINUTE
    minute, other = due.minute, (due.minute + 30) % 60
    before = {"unchanged": {"cron": f"{minute} * * * *"}, "moved": {"cron": f"{minute} * * * *"},
              "added": {"cron": f"{minute} * * * *"}, "every": {"every": "1 minute"}}
    after = {"moved": {"cron": f"{other} * * * *"}, "added": {"cron": f"{minute},{other} * * * *"},
             "every": {"every": "2 minutes"}}
    with on(env):
        scheduler, notes = service(env, {f"{name}.yaml": block_flow(config) for name, config in before.items()})
        scheduler.tick(due - MINUTE)  # first seen a minute before (the every-minute one runs then)
        scheduler.wait_for_runs()
        wait_until(due)
        scheduler.tick(dt.datetime.now())  # all four are due: their processes start, and load
        started = dt.datetime.now()
        for name, config in after.items():
            write(env.root / f"{name}.yaml", block_flow(config))
        assert dt.datetime.now() < started + 2 * SECOND, "changed too late to tell: their programs may have loaded"
        scheduler.wait_for_runs(timeout=60)

    at_due = {r["flow"]: r["status"] for r in runs(env) if r["when"] == f"{due:%H:%M}"}
    assert at_due.pop("unchanged", None) == "success", f"the unchanged schedule did not run at {due:%H:%M}"
    assert at_due == {}, f"runs whose schedule changed while they loaded, at {due:%H:%M}, began: {at_due}"
    assert notes == []


# --- SCH-9: Skip still runs a time less than 2 minutes late ------------------------------------------------------------

@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("late, ran, missed", [
    (dt.timedelta(minutes=1, seconds=59), ["07:00"], None),
    (dt.timedelta(minutes=2), [], 1),
    (dt.timedelta(minutes=2, seconds=1), [], 1),
], ids=["1 min 59 s late", "2 min late", "2 min 1 s late"])
def test_skip_still_runs_a_time_less_than_2_minutes_late(env, late, ran, missed):
    with on(env):
        scheduler, notes = service(env, {"flow.yaml": timed_flow("0 * * * *", misfire="skip")})
        scheduler.tick(at("06:30:00"))
    with on(env):  # off over 07:00, and on again
        on_again = Scheduler(Home(env.home), lambda title, message: notes.append((title, message)))
        on_again.tick(at("07:00:00") + late)
        on_again.wait_for_runs()

    assert [r["when"] for r in runs(env)] == ran, notes
    told = [message for title, message in notes if title == "Missed runs skipped"]
    if missed:
        (message,) = told
        assert re.search(rf"\b{missed}\b", message), message
    else:
        assert told == []


# --- SCH-13: a crash, emailed about if the flow asks ---------------------------------------------------------------------

@pytest.mark.invariant("SCH-13")
def test_a_crashed_run_is_emailed_about_only_when_its_flow_asks_for_failure_emails(env, smtp):
    """Two scheduled flows whose run's process dies: one's notify asks for failure emails (to the team), the other's
    only for success. Both are recorded as crashed (a failed run whose error says so) and told on the desktop as
    crashed; only the first is emailed, to the address its flow names."""
    write(env.home / "settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    crash = "import os\nos._exit(3)"
    with on(env):
        scheduler, notes = service(env, {
            "asks.yaml": timed_flow(code=crash, notify={"when": ["failure"], "to": ["team@example.com"]}),
            "does_not.yaml": timed_flow(code=crash, notify={"when": ["success"]})})
        scheduler.tick(at("06:30:00"))
        scheduler.tick(at("06:31:00"))
        scheduler.wait_for_runs()

    assert sorted((r["flow"], r["status"], r["finished"] is not None) for r in runs(env)) == [
        ("asks", "failed", True), ("does_not", "failed", True)]
    [(error,)] = env.query("SELECT DISTINCT error FROM blocks WHERE block='work'")
    assert "crashed" in error, error
    assert sorted(title for title, _ in notes) == ["asks crashed", "does_not crashed"], notes
    assert [(str(m["Subject"]), str(m["To"])) for m in smtp.messages] == [("[Taskloom] asks crashed",
                                                                          "team@example.com")]


# --- SCH-14: the real service through mistakes in its files ------------------------------------------------------------

@pytest.mark.invariant("SCH-14")
def test_the_service_started_with_mistakes_in_its_files_tells_you_and_goes_on(env):
    """`taskloom scheduler --headless` started while scheduled.yaml cannot be read and settings.yaml has a mistake
    (as a hand edit can leave them): it keeps running and checking, says what is wrong, and once the list is put
    right runs the flow it lists, telling you about the settings."""
    write(env.home / "settings.yaml", "max_concurrent_runs: lots\n")
    write(env.home / "scheduled.yaml", "flows: [unclosed\n")
    flow_path = write(env.root / "check.yaml", block_flow({"every": "1 minute"}))  # runs once the service sees it
    told_file = env.root / "service.log"

    def told() -> str:
        return told_file.read_text(encoding="utf-8", errors="replace") if told_file.exists() else ""

    with open(told_file, "w", encoding="utf-8") as out:
        proc = start_headless(env.home, stdout=out)
    try:
        assert wait_for(lambda: "The list of scheduled flows cannot be read" in told(), timeout=30), told()
        beat = heartbeat(env.home)
        assert wait_for(lambda: heartbeat(env.home) not in (None, beat), timeout=10), "it stopped checking"
        write(env.home / "scheduled.yaml", yaml.safe_dump({"flows": [str(flow_path)]}))  # put right
        assert wait_for(lambda: [r["status"] for r in runs(env)] == ["success"], timeout=30), told()
        assert wait_for(lambda: "Mistake in the settings" in told(), timeout=10), told()
        assert "max_concurrent_runs" in told() and proc.poll() is None
    finally:
        stop_service(env, proc)
