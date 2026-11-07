"""The scheduler service's agreed promises (docs/promises/scheduler.yaml) where the older scheduler tests leave a
part unchecked: several services started at once (SCH-1), every time a schedule the editor builds has through a
whole year (SCH-5, SCH-7), generated schedules, settings and check times with gaps (SCH-6, SCH-7, SCH-9, SCH-11,
SCH-12), a schedule changed just before its time (SCH-8), Skip's 2 minutes (SCH-9), a crash the flow asks to be
emailed about (SCH-13) and the real service through mistakes in its files (SCH-14)."""

import datetime as dt
import io
import itertools
import json
import re
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
from taskloom.launch import run_alive
from taskloom.schedule import Every, Trigger, plan_cron
from taskloom.scheduler import NOT_DUE, Scheduler, acquire_lock
from taskloom.scheduler import _unlock as unlock
from tests.test_paths_scheduler import flow as timed_flow
from tests.test_paths_scheduler import heartbeat, runs, service, wait_for, write
from tests.test_paths_scheduler import start_service as start_headless
from tests.test_scheduler_new_promises import Said, claim_its_time
from tests.test_scheduler_start_at import TRAY_SERVICE, start_at_clock, stop_service, wait_until

SECOND, MINUTE = dt.timedelta(seconds=1), dt.timedelta(minutes=1)
EARLY = dt.timedelta(seconds=5)  # SCH-8: a run's process is started this long before its time


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


# --- SCH-6, SCH-7, SCH-9, SCH-11, SCH-12: generated schedules, settings and check times --------------------------------
# The service's loop (Scheduler.tick at a given clock time, as `taskloom scheduler` runs it) over generated scheduled
# flows, Max concurrent runs, and check times with gaps: a second or so apart while it is on, minutes while the PC
# sleeps or the service is off. Each run's process is replaced at the process boundary by a FakeRun (real processes are
# too slow for hundreds of examples): it says it began and succeeded, and has finished when the service checks on it for
# the n-th time after its run began; as the system tells it (run_alive), its process is going until then. One the
# service starts early, for a time to come (--if-scheduled), waits for that time and then claims it as `taskloom run`
# does (claim_its_time: the real claim, on the simulation's clock, a service being on while it holds the folder's lock);
# it begins only if it has it, else it records nothing and ends with NOT_DUE. Its time has come by the service's first
# check at or after it (the PC may have slept meanwhile: it then wakes with the service). The service turned off and on
# again is a new Scheduler for the same Taskloom folder: the old one stops just after its last check, the new one starts
# at its first. The runs going finish while it is off; a run started early whose time comes before the new one starts
# finds no service on, and one whose time comes later reaches it with the new service on, which may have started a run
# for that time too: both claim it, in the order the scenario draws (either can happen).

@dataclass
class Started:
    """A run's process the service started: its flow, Schedule block and time, the life of the service that started
    it, whether it was started early to claim its time (--if-scheduled), and the order (among all starts, beginnings
    and ends) in which its process started, its run began (None: not, or not yet) and it was seen finished."""
    life: int
    flow: str
    block: str
    as_of: dt.datetime
    began: int
    claims: bool = False
    begun: int | None = None
    ended: int | None = None

    def going_at(self, other: "Started") -> bool:
        """Going when the process of `other` started. A process counts from its start for the service that started it
        (it counts it as going from then); for a later service, from when its run began (an early run that has not
        begun is no run of its flow yet)."""
        start = self.began if self.life == other.life else self.begun
        return start is not None and start < other.began and (self.ended is None or self.ended > other.began)


class FakeRun:
    """Stands in for `taskloom run ...` at the process boundary (see above)."""

    def __init__(self, simulation, args):
        self.simulation, self.args, self.returncode = simulation, args, None
        flow = Path(args[args.index("run") + 1]).stem
        self.record = Started(simulation.life, flow, args[args.index("--trigger") + 1],
                              start_at_clock(args[args.index("--start-at") + 1]), next(simulation.moments),
                              claims="--if-scheduled" in args)
        simulation.started.append(self.record)
        simulation.fakes.append(self)
        self.pid = 2 ** 30 + len(simulation.started)  # (no process has it)
        self.checks_left = simulation.lasts[flow]
        self.saying = [json.dumps({"event": "run_started", "run_id": len(simulation.started)}) + "\n",
                       json.dumps({"event": "run_finished", "status": "success"}) + "\n"]
        if self.record.claims:
            self.stdout = Said()
        else:
            self.record.begun = self.record.began
            self.stdout = io.StringIO("".join(self.saying))

    def reach_its_time(self, clock: dt.datetime):
        """Started early, it reaches its time, with this PC's clock showing `clock`, and claims it."""
        why = claim_its_time(self.args, self.simulation.home, clock)
        if why:
            self.stdout.say(f"not begun: {why}\n")
            self.end(NOT_DUE)
        else:
            self.record.begun = next(self.simulation.moments)
            self.stdout.say(*self.saying)

    def poll(self):
        if self.returncode is None and self.record.begun is not None:
            self.checks_left -= 1
            if self.checks_left <= 0:
                self.end(0)
        return self.returncode

    def end(self, code: int):
        self.returncode = code
        self.record.ended = next(self.simulation.moments)
        if isinstance(self.stdout, Said):
            self.stdout.end()


@dataclass
class Simulation:
    home: Path  # the Taskloom folder
    lasts: dict  # flow -> how many of the service's checks its runs last
    older_first: bool = False  # (see reach_times)
    life: int = 0
    moments: itertools.count = field(default_factory=itertools.count)
    started: list = field(default_factory=list)
    fakes: list = field(default_factory=list)
    checks: list = field(default_factory=list)  # (life, clock time, moment) of each check

    def came(self, run: Started, latest: bool = False) -> dt.datetime:
        """The check at which the run's time came: the first that starts runs due by 5 s after it (SCH-8), which for
        a time missed is the first after it. A run still waiting its turn when the service stops is saved and started
        by the service after it (SCH-16): it came at that check of the earlier one. A time for which an early run did
        not begin comes again: at the earliest at the first check after that run ended, or at a later service's first
        check; at the latest (`latest`), at the first check after every such run ended."""
        tried = [r for r in self.started if (r.flow, r.block, r.as_of) == (run.flow, run.block, run.as_of)
                 and r.begun is None and r.began < run.began]
        checks = self.checks
        if tried and latest:
            if any(r.ended is None for r in tried):
                return dt.datetime.max
            end = max(r.ended for r in tried)
            checks = [check for check in checks if check[2] > end]
        elif tried:
            last = max(tried, key=lambda r: r.began)
            checks = [(life, t, moment) for life, t, moment in checks if life > last.life
                      or (life == last.life and last.ended is not None and moment > last.ended)]
        return min((t for _, t, _ in checks if t + EARLY >= run.as_of), default=dt.datetime.max)

    def reach_times(self, now: dt.datetime | None, before: bool = False):
        """The runs started early whose time has come by `now` (before it, with `before`; None: each one, as time goes
        on) reach it and claim it, in the order of their times; of two for the same time, the one an earlier service
        started claims first if `older_first`, else the one the service now on started."""
        come = [fake for fake in self.fakes if fake.record.claims and fake.record.begun is None
                and fake.returncode is None and (now is None or fake.record.as_of < now
                                                 or (fake.record.as_of == now and not before))]
        order = (lambda f: f.record.life) if self.older_first else (lambda f: f.record.life != self.life)
        for fake in sorted(come, key=lambda f: (f.record.as_of, order(f), f.record.began)):
            fake.reach_its_time(fake.record.as_of if now is None else now)

    def alive(self, pid: int, began: float | None) -> bool:
        """Whether a run's process is still going, as the system tells it (launch.run_alive): a stand-in's, until it
        has ended."""
        fakes = [fake for fake in self.fakes if fake.pid == pid]
        return fakes[0].returncode is None if fakes else run_alive(pid, began)

    def earlier(self) -> list:
        """The processes an earlier service started that are still going: none of this service's checks polls them."""
        return [fake for fake in self.fakes if fake.record.life < self.life and fake.returncode is None]


SCHEDULE = st.tuples(st.one_of(st.tuples(st.just("cron"), st.sampled_from(["* * * * *", "*/2 * * * *", "*/5 * * * *",
                                                                           "0 * * * *"])),
                               st.tuples(st.just("every"), st.sampled_from(["1 minute", "2 minutes", "3 minutes"]))),
                     st.sampled_from(["skip", "run_once", "catch_up"]))
FLOWS = st.lists(st.fixed_dictionaries({"schedules": st.lists(SCHEDULE, min_size=1, max_size=2),
                                        "overlap": st.sampled_from(["skip", "queue", "allow"]),
                                        "lasts": st.integers(1, 4)}), min_size=1, max_size=3)
# (milliseconds to the next check, the service turned off and on again in between)
STEPS = st.lists(st.tuples(st.one_of(st.integers(200, 75_000), st.integers(120_000, 1_200_000)),
                           st.sampled_from([False, False, False, True])), min_size=1, max_size=20)


def scenarios(limits=(None, 1, 2, 3)):
    """Also `older_first`: whether, of two runs started early for the same time, one by a service since turned off
    and on again and one by the service now on, the older one claims it first (either can happen)."""
    return st.fixed_dictionaries({"flows": FLOWS, "limit": st.sampled_from(limits), "steps": STEPS,
                                  "first_check": st.integers(0, 3_599_999), "older_first": st.booleans()})


GENERATED = settings(max_examples=200, deadline=None, database=None, suppress_health_check=[HealthCheck.too_slow])


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
    simulation = Simulation(home.root, lasts={}, older_first=scenario["older_first"])
    for i, spec in enumerate(scenario["flows"]):
        path = root / f"flow{i}.yaml"
        path.write_text(flow_file(spec), encoding="utf-8")
        home.set_scheduled(path, True)
        simulation.lasts[path.stem] = spec["lasts"]
    now = at("06:00:00") + dt.timedelta(milliseconds=scenario["first_check"])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subprocess, "Popen", lambda args, *rest, **kwargs: FakeRun(simulation, args))
        patch.setattr("taskloom.scheduler.run_alive", simulation.alive)
        lock = acquire_lock(home)  # the service is on: it holds the folder's lock, as `taskloom scheduler` does
        try:
            service = Scheduler(home)
            for gap, turned_off in [(0, False)] + scenario["steps"]:
                now += dt.timedelta(milliseconds=gap)
                if turned_off:  # it stopped just after its last check; the new one starts now
                    unlock(lock)
                    for fake in simulation.fakes:
                        if fake.returncode is None and fake.record.begun is not None:
                            fake.end(0)  # (its runs going finish while it is off)
                    simulation.reach_times(now, before=True)  # (no service is on)
                    simulation.life += 1
                    lock = acquire_lock(home)
                    service = Scheduler(home)
                simulation.reach_times(now)
                simulation.checks.append((simulation.life, now, next(simulation.moments)))
                service.tick(now)
                for fake in simulation.earlier():
                    fake.poll()
            for _ in range(5000):  # the runs going and those waiting their turn all finish (no new time comes)
                simulation.reach_times(None)
                if not (service.runs or any(service.queued.values()) or service.waiting or simulation.earlier()):
                    break
                service.tick(now)
                for fake in simulation.earlier():
                    fake.poll()
            else:
                raise AssertionError("runs still going or waiting after 5000 checks")
        finally:
            unlock(lock)
            for fake in simulation.fakes:
                if fake.returncode is None:
                    fake.end(-9)
    return simulation


def setting(scenario: dict, run: Started) -> tuple[str, str, str]:
    """(overlap, the kind of its schedule, its misfire) of the flow and Schedule block of `run`."""
    spec = scenario["flows"][int(run.flow.removeprefix("flow"))]
    (kind, _), misfire = spec["schedules"][int(run.block.removeprefix("s"))]
    return spec["overlap"], kind, misfire


def described(run: Started) -> str:
    return f"{run.flow}/{run.block} {run.as_of:%H:%M:%S}"


# The service turned off and on again in the seconds before a time whose run's process it had started early: the new
# one starts a run for that time too, and both reach it (a cron time; an Every N time, counted from first sight); the
# new service's run claims it first, or the old one's.
RESTARTED_JUST_BEFORE = [
    {"flows": [{"schedules": [(("cron", "* * * * *"), "run_once")], "overlap": "skip", "lasts": 1}], "limit": None,
     "first_check": 57_000, "steps": [(1_000, True), (5_000, False)]},  # 06:00:57; 06:00:58 (06:01 to come); 06:01:03
    {"flows": [{"schedules": [(("every", "1 minute"), "catch_up")], "overlap": "allow", "lasts": 1}], "limit": None,
     "first_check": 0, "steps": [(57_000, False), (1_000, True), (5_000, False)]},  # 06:00, 06:00:57, :58, 06:01:03
]
RESTARTED_JUST_BEFORE = [{**scenario, "older_first": older_first} for scenario in RESTARTED_JUST_BEFORE
                         for older_first in (False, True)]
# As the first of those, with the older run claiming 06:01 first, that run lasting 4 checks and a check at 06:02:03
# (the flow's next time comes while it may still go), and Max concurrent runs 1.
STILL_GOING = {"flows": [{"schedules": [(("cron", "* * * * *"), "run_once")], "overlap": "skip", "lasts": 4}],
               "limit": 1, "first_check": 57_000, "steps": [(1_000, True), (5_000, False), (60_000, False)],
               "older_first": True}
CAUGHT_UP_STILL_GOING = {**STILL_GOING, "flows": [{"schedules": [(("cron", "* * * * *"), "catch_up")],
                                                   "overlap": "skip", "lasts": 4}]}  # (the same, Catch up)


@pytest.mark.invariant("SCH-6")
@GENERATED
@given(scenario=scenarios())
@example(scenario=RESTARTED_JUST_BEFORE[0])
@example(scenario=RESTARTED_JUST_BEFORE[1])
@example(scenario=RESTARTED_JUST_BEFORE[2])
@example(scenario=RESTARTED_JUST_BEFORE[3])
def test_generated_each_due_time_starts_at_most_once(tmp_path_factory, scenario):
    """No two runs begin for the same time of a schedule (a run's process started early that did not begin, as it
    could not claim its time, is not a run)."""
    started = simulate(tmp_path_factory.mktemp("service"), scenario).started
    keys = [(run.flow, run.block, run.as_of) for run in started if run.begun is not None]
    assert len(keys) == len(set(keys)), f"began twice: {sorted(k for k in set(keys) if keys.count(k) > 1)}"


@pytest.mark.invariant("SCH-11")
@GENERATED
@given(scenario=scenarios())
@example(scenario=STILL_GOING)
@example(scenario={**STILL_GOING, "older_first": False})
def test_generated_two_runs_of_a_flow_go_at_once_only_if_its_overlap_is_allow(tmp_path_factory, scenario):
    started = simulate(tmp_path_factory.mktemp("service"), scenario).started
    for run in started:
        if setting(scenario, run)[0] != "allow":
            also = [described(r) for r in started if r.flow == run.flow and r.going_at(run)]
            assert not also, f"{described(run)} started while {also} went"


@pytest.mark.invariant("SCH-12")
@GENERATED
@given(scenario=scenarios(limits=(1, 2, 3)))
@example(scenario=STILL_GOING)
@example(scenario={**STILL_GOING, "older_first": False})
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
    own flow's run going (Overlap queue, or a catch-up one at a time) rather than for a free slot. (When a time came
    again, as an early run did not begin for it, is known only within bounds: the earlier run is taken at its latest,
    the later at its earliest.)"""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)
    started = simulation.started
    for later in started:
        for earlier in started:
            if (earlier.life == later.life and later.began < earlier.began
                    and simulation.came(earlier, latest=True) < simulation.came(later)):
                overlap, _, misfire = setting(scenario, earlier)
                waits_for_its_flow = (overlap != "allow" or misfire == "catch_up") and any(
                    r.flow == earlier.flow and r.going_at(later) for r in started)
                assert waits_for_its_flow, (
                    f"{described(earlier)} (came by {simulation.came(earlier, latest=True):%H:%M:%S}) waited for a "
                    f"free slot, and {described(later)} (came at {simulation.came(later):%H:%M:%S}) started before it")


@pytest.mark.invariant("SCH-9")
@GENERATED
@given(scenario=scenarios())
@example(scenario=CAUGHT_UP_STILL_GOING)
@example(scenario={**CAUGHT_UP_STILL_GOING, "older_first": False})
def test_generated_catch_up_runs_go_oldest_first_one_at_a_time(tmp_path_factory, scenario):
    """Catch up's runs of a schedule start oldest first and never two at once. With Overlap allow (a new time may go
    alongside a run going), this holds for the times missed: those that came together at one check, already past."""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)

    def missed_together(run: Started) -> tuple:
        came = simulation.came(run)
        return (came, run.as_of < came)

    for run in simulation.started:
        overlap, _, misfire = setting(scenario, run)
        if misfire != "catch_up" or (overlap == "allow" and not missed_together(run)[1]):
            continue
        ours = [r for r in simulation.started if (r.flow, r.block) == (run.flow, run.block)
                and (overlap != "allow" or missed_together(r) == missed_together(run))]
        newer = [described(r) for r in ours if r.began < run.began and r.as_of > run.as_of]
        assert not newer, f"{described(run)} started after the newer {newer}"
        also = [described(r) for r in ours if r.going_at(run)]
        assert not also, f"{described(run)} started while {also} went"


@pytest.mark.invariant("SCH-7")
@GENERATED
@given(scenario=scenarios())
def test_generated_every_n_runs_keep_the_rhythm_from_when_the_service_first_saw_them(tmp_path_factory, scenario):
    """Through sleeps, the service off and on again, and every Misfire and Overlap: an Every N schedule's runs are
    all at whole multiples of N after the first check (to the second) that saw it."""
    simulation = simulate(tmp_path_factory.mktemp("service"), scenario)
    seen = simulation.checks[0][1].replace(microsecond=0)
    for run in simulation.started:
        _, kind, _ = setting(scenario, run)
        if kind == "every":
            spec = scenario["flows"][int(run.flow.removeprefix("flow"))]
            n = Every(spec["schedules"][int(run.block.removeprefix("s"))][0][1]).interval
            assert (run.as_of - seen) % n == dt.timedelta(0), f"{described(run)}: not a multiple of {n} after {seen}"


# --- SCH-12: a run waiting for a free slot is not overtaken ------------------------------------------------------------

@pytest.mark.invariant("SCH-12")
def test_a_run_waiting_for_a_free_slot_starts_before_a_later_run_of_a_busy_flow(env):
    """Max concurrent runs 1. busy (Overlap queue) starts at 06:31; report's 06:31 run waits for the slot. busy's
    06:32 time comes while its 06:31 run still goes, so it waits for that run. When busy's 06:31 run ends, report's
    run, which came first, starts next; busy's 06:32 run after it."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    scheduler, notes = service(env, {"busy.yaml": timed_flow(wait=2, overlap="queue"),
                                     "report.yaml": timed_flow("31 6 * * *")})
    scheduler.tick(at("06:30:00"))
    scheduler.tick(at("06:31:00"))
    scheduler.tick(at("06:32:00"))
    scheduler.wait_for_runs()

    assert [(r["flow"], r["when"], r["status"]) for r in runs(env)] == [
        ("busy", "06:31", "success"), ("report", "06:31", "success"), ("busy", "06:32", "success")], notes


# --- SCH-8: a schedule changed in the seconds before its time ----------------------------------------------------------

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
def test_a_run_started_early_does_not_begin_if_its_schedule_is_changed_before_its_time(env):
    """Four flows due at the same minute; the service (on: this test holds its lock) starts their runs a few seconds
    early. Before that minute three are saved with their schedule changed: to another minute, with another minute
    added, and from every minute to every 2 minutes. Their early runs do not begin: nothing is recorded or told for
    them. The unchanged one begins."""
    now = dt.datetime.now()
    due = now.replace(second=0, microsecond=0) + MINUTE
    if (due - now).total_seconds() < 10:  # room for the first check and its run before the early start
        due += MINUTE
    minute, other = due.minute, (due.minute + 30) % 60
    before = {"unchanged": {"cron": f"{minute} * * * *"}, "moved": {"cron": f"{minute} * * * *"},
              "added": {"cron": f"{minute} * * * *"}, "every": {"every": "1 minute"}}
    after = {"moved": {"cron": f"{other} * * * *"}, "added": {"cron": f"{minute},{other} * * * *"},
             "every": {"every": "2 minutes"}}
    scheduler, notes = service(env, {f"{name}.yaml": block_flow(config) for name, config in before.items()})
    lock = acquire_lock(Home(env.home))  # the scheduler service is on
    try:
        scheduler.tick(due - MINUTE)  # first seen a minute before (the every-minute one runs then)
        scheduler.wait_for_runs()
        wait_until(due - 4.5 * SECOND)
        scheduler.tick(dt.datetime.now())  # all four are due within 5 s: their processes start
        for name, config in after.items():
            write(env.root / f"{name}.yaml", block_flow(config))
        assert dt.datetime.now() < due - SECOND, "changed too late to tell"
        scheduler.wait_for_runs(timeout=60)
    finally:
        lock.close()

    at_due = {r["flow"]: r["status"] for r in runs(env) if r["when"] == f"{due:%H:%M}"}
    assert at_due.pop("unchanged", None) == "success", f"the unchanged schedule did not run at {due:%H:%M}"
    assert at_due == {}, f"runs whose schedule changed before {due:%H:%M} began: {at_due}"
    assert notes == []


# --- SCH-9: Skip still runs a time less than 2 minutes late ------------------------------------------------------------

@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("late, ran, missed", [
    (dt.timedelta(minutes=1, seconds=59), ["07:00"], None),
    (dt.timedelta(minutes=2, seconds=1), [], 1),
], ids=["1 min 59 s late", "2 min 1 s late"])
def test_skip_still_runs_a_time_less_than_2_minutes_late(env, late, ran, missed):
    scheduler, notes = service(env, {"flow.yaml": timed_flow("0 * * * *", misfire="skip")})
    scheduler.tick(at("06:30:00"))
    on_again = Scheduler(Home(env.home), lambda title, message: notes.append((title, message)))  # off over 07:00
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
