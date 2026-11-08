"""TLA+ trace validation of the scheduler service: the real code's behaviours are behaviours of the checked model.

    python packaging/tla_trace.py --out DIR [--traces N] [--jobs J]

It drives the real scheduler service through generated scenarios that fit the model's bounds
(specs/formal/SchedulerService.cfg): the Scheduler's checks, the runs' claims (claim_time, in History.start_run),
turning the service off and on (request_turn_off, set_paused) and saving a changed schedule (forget_schedules). Run
processes are stand-ins at the process boundary, as in the scheduler tests. Each step is recorded as the model action
it is, with the state the code shows just after it, mapped onto the model's variables (the refinement mapping:
specs/formal/README.md, "Trace validation"). Each trace is written as a TLA+ module (DIR/trace-NNN/SchedulerTrace.tla)
and checked by TLC against specs/formal/TraceSchedulerService.tla (its log: DIR/trace-NNN/tlc.log). Then a planted
divergence, a trace in which a refused claim is recorded as begun, must not match. The last line says how many traces
match and which of the model's actions they take. Exit 0 only if every trace matches and the planted one does not.

TLC is tla2tools.jar 1.7.4, from $TLA2TOOLS_JAR or ~/.local/share/tlaplus/1.7.4/tla2tools.jar, run by `java`.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # (the scheduler tests' stand-in helpers)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # (those helpers' module imports Qt)
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"  # (never the real credential store)

import pytest  # noqa: E402
import yaml  # noqa: E402

from taskloom import cli  # noqa: E402
from taskloom import scheduler as service  # noqa: E402
from taskloom.config import Home  # noqa: E402
from taskloom.flow import load_flow, real_path  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.history import connect as history_connect  # noqa: E402
from tests.test_scheduler_new_promises import Said, clock_shows, run_command_line  # noqa: E402

SPEC = ROOT / "specs" / "formal"
JAR = Path.home() / ".local" / "share" / "tlaplus" / "1.7.4" / "tla2tools.jar"
FLOWS = ("f1", "f2")
# A flow's schedule (version 0) and the same schedule as you saved it changed (version 1): two texts with the same
# two times, 06:05 and 06:10, the model's times 1 and 2.
CRON = ("5,10 6 * * *", "5-10/5 6 * * *")
# Where the clock is at each of the model's ticks (on a Monday): 06:00 (tick 0, as the flows were saved), then each
# time. Each step moves it on by STEP within its tick, so every check at a tick sees its time on time (within the
# 2 minutes), and a later tick sees it late.
TICKS = tuple(dt.datetime(2026, 1, 5, 6, minute).astimezone() for minute in (0, 5, 10))
STEP = dt.timedelta(milliseconds=100)
WAITS = {"now": "time", "flow": "queue", "slot": "slot"}  # waiting_runs.waits -> the model's row
ACTIONS = ("Tick", "ServiceStart", "ServiceCrash", "Poll", "PlanFlow", "StartNext", "Claim (begins)",
           "Claim (refused)", "KillLoading", "RunFinish", "KillRun", "ManualBegin", "ManualEnd", "ChangeSchedule",
           "Proceed", "TurnOn")
TRACES = 200  # enough to take each action (see the summary line)


# --- stand-in run processes ------------------------------------------------------------------------------------------

class Stdin:
    """A stand-in run's stdin: the service asks it to stop there (Scheduler._stop)."""

    def __init__(self, process: "Process"):
        self.process = process

    def write(self, text: str):
        if "cancel" in text:
            self.process.stopped()

    def flush(self):
        pass


class Process:
    """Stands in for a `taskloom run ... --if-scheduled` process the service started (subprocess.Popen). It reads its
    command line with `taskloom run`'s own parser. It loads until the driver lets it claim, and then claims as the real
    run does: History.start_run with cli._claim (claim_time), one transaction; refused, it records nothing and ends
    with NOT_DUE. Its run ends when the driver says (finish, or end without recording: killed), or at once when asked
    to stop (on its stdin, or by SIGTERM): one that has begun records itself cancelled, as `taskloom run` does."""

    def __init__(self, driver: "Driver", args: list[str]):
        self.driver = driver
        self.args = run_command_line(args, driver.home.root)
        service_id, row, epoch, _ = self.args.if_scheduled
        self.due = driver.due(self.args.flow, self.args.scheduled, dt.datetime.fromisoformat(self.args.start_at))
        self.life, self.epoch, self.row = driver.life_of[service_id], int(epoch), int(row)
        self.pid = 2 ** 30 + len(driver.processes)  # (no process has it)
        self.run_id = self.returncode = None
        self.stdin, self.stdout = Stdin(self), Said()

    def claim(self) -> bool:
        driver = self.driver
        flow = load_flow(real_path(Path(self.args.flow)))
        claim = cli._claim(driver.home, flow, self.args, dt.datetime.fromisoformat(self.args.start_at))
        try:
            with contextlib.redirect_stderr(io.StringIO()):  # (why it did not begin)
                self.run_id = driver.history.start_run(flow.name, str(flow.path), {}, driver.clock.isoformat(),
                                                       started_by=self.args.trigger, schedule=self.args.scheduled,
                                                       claim=claim)
        except SystemExit:
            self.end(service.NOT_DUE)
            return False
        driver.db.execute("UPDATE runs SET pid=?, began=? WHERE id=?", (self.pid, driver.clock.timestamp(), self.run_id))
        driver.begun[self.run_id] = (self.due, self.life)
        self.stdout.say(json.dumps({"event": "run_started", "run_id": self.run_id}) + "\n")
        return True

    def finish(self, status: str = "success"):
        self.driver.history({"event": "run_finished", "run_id": self.run_id, "ts": self.driver.clock.isoformat(),
                             "status": status})
        self.stdout.say(json.dumps({"event": "run_finished", "status": status}) + "\n")
        self.end(cli.EXIT_OK if status == "success" else cli.EXIT_CANCELLED)

    def stopped(self):
        if self.returncode is None and self.run_id is not None:
            self.finish("cancelled")
        self.end(cli.EXIT_CANCELLED)

    def end(self, code: int):
        if self.returncode is None:
            self.returncode = code
            self.stdout.end()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):  # (forced to end: it records nothing)
        self.end(-9)

    terminate = kill


class Traced(service.Scheduler):
    """The real Scheduler, telling the driver what each step of its check did: its poll (the model's Poll), each
    schedule's transaction (PlanFlow, with the runs it starts for their time), and each saved run it starts
    afterwards (StartNext)."""

    def __init__(self, driver: "Driver"):
        self.driver, self.planning, self.checked = driver, False, False
        super().__init__(driver.home)

    def _poll(self):
        super()._poll()
        self.checked = True
        self.driver.polled(self)

    def _check(self, path, *rest):
        self.planning = True
        try:
            super()._check(path, *rest)
        finally:
            self.planning = False
        self.driver.planned(path)

    def _start(self, saved):
        super()._start(saved)
        if not self.planning:
            self.driver.record("StartNext", due=self.driver.due(saved.flow, saved.schedule, saved.as_of))


# --- the driver: one trace -------------------------------------------------------------------------------------------

class Driver:
    """A Taskloom folder with the flows f1 and f2 (each one schedule, at the model's times 1 and 2; Max concurrent
    runs 1), the steps you, the service and the run processes take in it, and the trace: each step as the model's
    action, with the state the code shows after it (shown)."""

    def __init__(self, root: Path, overlap: dict, misfire: dict, choose=random.choice):
        self.home = Home(root / "home")
        self.home.root.mkdir(parents=True)
        (self.home.root / "settings.yaml").write_text("max_concurrent_runs: 1\n", encoding="utf-8")
        self.overlap, self.misfire, self.choose = overlap, misfire, choose  # (choose: your choices at Proceed)
        self.now, self.steps = 0, 0
        self.lives, self.life_of, self.scheduler, self.lock = 0, {}, None, None
        self.processes, self.by_hand, self.begun = [], {}, {}  # (begun: run id -> (due, life of its process))
        self.kills = self.manuals = self.changes = self.offs = 0
        self.proceeding, self.events = False, []
        History(self.home.history_db).close()
        self.paths = {f: (root / f"{f}.yaml").resolve() for f in FLOWS}
        for f, path in self.paths.items():
            self.write_flow(f, CRON[0])
            self.home.set_scheduled(path, True)
        with clock_shows(self.clock):  # (saved by the editor at 06:00: its times count from then)
            for path in self.paths.values():
                service.forget_schedules(self.home, path, load_flow(path).triggers)
        self.history, self.db = History(self.home.history_db), history_connect(self.home.history_db)
        self.start = self.last = self.shown()

    @property
    def clock(self) -> dt.datetime:
        return TICKS[self.now] + self.steps * STEP

    def write_flow(self, f: str, cron: str, active: bool = True):
        config = {"cron": cron, "misfire": self.misfire[f], **({} if active else {"active": False})}
        self.paths[f].write_text(yaml.safe_dump({"overlap": self.overlap[f], "blocks": {
            "s0": {"type": "trigger.schedule", "config": config},
            "work": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}}},
            "edges": ["s0.start -> work.after"]}, sort_keys=False), encoding="utf-8")

    # --- the mapping from the code's state to the model's variables ---

    def due(self, flow, schedule: str, moment: dt.datetime) -> tuple:
        """The model's due time <<flow, version, time>> of a run of `flow` (its file) for `schedule` at `moment`."""
        ticks = [t for t in (1, 2) if TICKS[t] == moment]
        if len(ticks) != 1:
            raise AssertionError(f"{moment} is not one of the model's times")
        return Path(flow).stem, CRON.index(schedule.removeprefix("cron ")), ticks[0]

    def shown(self) -> dict:
        """The model's variables as the code shows them now (see specs/formal/README.md, "Trace validation")."""
        db, root = self.db, self.home.root
        paused = (root / "scheduler.paused").exists()
        choices = {Path(k).stem: v for k, v in service._turn_off_choices(self.home).items()} if paused else {}
        on = [Path(p).stem for p in self.home.scheduled_flows()]
        configs = {f: yaml.safe_load(path.read_text(encoding="utf-8"))["blocks"]["s0"]["config"]
                   for f, path in self.paths.items()}
        last = {f: {"v": -1, "t": 0} for f in FLOWS}
        for key, when in db.execute("SELECT trigger, last FROM schedule_state"):
            path, _, text = key.split("#", 2)
            f = Path(path).stem
            if last[f]["v"] != -1:
                raise AssertionError(f"{f} has two schedule states")
            last[f] = {"v": CRON.index(text.removeprefix("cron ")),
                       "t": max(t for t in (0, 1, 2) if TICKS[t] <= service._moment(when))}
        rows = set()
        for flow, schedule, due, waits in db.execute("SELECT flow, schedule, due, waits FROM waiting_runs"):
            rows.add((self.due(flow, schedule, service._moment(due)), WAITS[waits]))
        runs, manual = set(), {f: False for f in FLOWS}
        for run_id, flow_path, status, schedule, pid in db.execute(
                "SELECT id, flow_path, status, schedule, pid FROM runs"):
            going = status == "running" and self.alive(pid, None)
            if schedule is None:
                manual[Path(flow_path).stem] |= going
            else:
                due, life = self.begun[run_id]
                runs.add((due, "going" if going else "died" if status == "running" else "over", life))
        on_record = [row[0] for row in db.execute("SELECT id FROM service_on")]
        s = self.scheduler
        return {
            "now": self.now, "lives": self.lives, "lock": self.life_of[s.id] if self.lock else 0,
            "svcOn": self.life_of[on_record[0]] if on_record else 0,
            "paused": paused, "choice": {f: choices.get(f, "none") for f in FLOWS},
            "epoch": service._epoch(db),
            "ver": {f: CRON.index(configs[f]["cron"]) for f in FLOWS},
            "schedOn": {f: configs[f].get("active", True) and f in on for f in FLOWS},
            "last": last, "rows": rows, "runs": runs, "manualGoing": manual,
            "loading": {(p.due, p.life, p.epoch) for p in self.loading()},
            "adopted": {self.begun[r.run_id][0] for r in s.runs if r.as_of is None} if s else set(),
            "turning": s is not None and s.turning_off is not None,
            "checked": s is not None and s.checked,
        }

    def record(self, kind: str, **args):
        self.last = self.shown()
        self.events.append({"kind": kind, **args, "state": self.last})

    # --- what the code calls: the process boundary, the clock, the service's steps ---

    def popen(self, args, *rest, **kwargs):
        args = [str(a) for a in args]
        if "run" not in args or "--if-scheduled" not in args:
            raise AssertionError(f"the service started something else: {args}")
        self.processes.append(Process(self, args))
        return self.processes[-1]

    def alive(self, pid, began) -> bool:  # (launch.run_alive)
        return any(p.pid == pid and p.returncode is None for p in self.processes) or any(
            r["pid"] == pid and not r["ended"] for r in self.by_hand.values())

    def kill(self, pid, sig):  # (os.kill: SIGTERM to a run, by Proceed's Stop now or by the service)
        process = next((p for p in self.processes if p.pid == pid), None)
        if process is None:
            raise ProcessLookupError(pid)  # (never a real process)
        going = process.run_id is not None and process.returncode is None
        process.stopped()
        if self.proceeding and going:  # Proceed stops it itself: it ends (cancelled) before the service sees the turn-off
            self.record("RunFinish", due=process.due)

    def stop_runs_going(self, home, flows):  # (request_turn_off, after its transaction: Proceed is done)
        self.record("Proceed")
        self.proceeding = True
        try:
            STOP_RUNS_GOING(home, flows)
        finally:
            self.proceeding = False

    def polled(self, scheduler: Traced):
        if scheduler.off:  # turned off, and nothing it lets finish is left: the service ends (serve) and lets go of
            service._unlock(self.lock)  # the lock; its check does nothing more
            self.lock, self.scheduler = None, None
        self.record("Poll")

    def planned(self, path):
        if self.shown() != self.last:  # (a schedule with nothing due changes nothing: no step)
            self.record("PlanFlow", flow=Path(path).stem)

    @contextlib.contextmanager
    def patched(self):
        def tell_now(*args, **kwargs):  # (an email sent at once: the service's turn-off never waits for one)
            sending = TELL(*args, **kwargs)
            sending.join()
            return sending

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(subprocess, "Popen", self.popen)
            patch.setattr(service, "run_alive", self.alive)
            patch.setattr(service, "os", types.SimpleNamespace(**{**vars(os), "kill": self.kill}))
            patch.setattr(service, "_stop_runs_going", self.stop_runs_going)
            patch.setattr(service, "tell", tell_now)
            try:
                yield
            finally:
                for p in self.processes:
                    p.end(-9)
                if self.lock is not None:
                    service._unlock(self.lock)
                    self.scheduler._db.close()
                self.history.close()
                self.db.close()

    # --- the steps (each one of the model's actions, or a check: Poll, PlanFlow, StartNext) ---

    def loading(self) -> list[Process]:
        return [p for p in self.processes if p.run_id is None and p.returncode is None]

    def going(self) -> list[Process]:
        return [p for p in self.processes if p.run_id is not None and p.returncode is None]

    def enabled(self) -> list[tuple]:
        """The steps that can come next, weighted, within the model's bounds. The clock does not move on while a run
        for its time loads under the service on: the code refuses it after 2 minutes, which the model does not have
        (README, "Trace validation")."""
        steps = []
        now_rows = {row for (row,) in self.db.execute("SELECT id FROM waiting_runs WHERE waits='now'")}
        on = self.life_of[self.scheduler.id] if self.lock else None
        if self.now < 2 and not any(p.life == on and p.row in now_rows for p in self.loading()):
            steps.append((1, "Tick"))
        paused = (self.home.root / "scheduler.paused").exists()
        if self.lock is None and not paused and self.lives < 2:
            steps.append((3, "ServiceStart"))
        if self.lock is not None:
            steps += [(1, "ServiceCrash"), (6, "Check")]
        steps += [(4, "Claim", p) for p in self.loading()]
        steps += [(2, "RunFinish", p) for p in self.going()]
        if self.kills < 1:
            steps += [(0.5, "KillLoading", p) for p in self.loading()] + [(0.5, "KillRun", p) for p in self.going()]
        if self.manuals < 1:
            steps += [(0.5, "ManualBegin", f) for f in FLOWS]
        steps += [(1, "ManualEnd", f) for f, r in self.by_hand.items() if not r["ended"]]
        if self.changes < 1:
            steps += [(0.5, "ChangeSchedule", f, off) for f in FLOWS if self.last["schedOn"][f] for off in (False, True)]
        if not paused and self.offs < 1:
            steps.append((0.5, "Proceed"))
        if paused:
            steps.append((1, "TurnOn"))
        return steps

    def step(self, name: str, *args):
        self.steps += 1
        with clock_shows(self.clock):
            getattr(self, name)(*args)

    def Tick(self):
        self.now, self.steps = self.now + 1, 0
        self.record("Tick")

    def ServiceStart(self):  # (as `taskloom scheduler` starts: it takes the lock, then makes its Scheduler)
        self.lives += 1
        self.lock = service.acquire_lock(self.home)
        self.scheduler = Traced(self)
        self.life_of[self.scheduler.id] = self.lives
        self.record("ServiceStart")

    def ServiceCrash(self):  # (the service's process dies: the system lets go of its lock; its runs go on)
        service._unlock(self.lock)
        self.scheduler._db.close()
        self.lock = self.scheduler = None
        self.record("ServiceCrash")

    def Check(self):
        scheduler = self.scheduler
        scheduler.tick(self.clock)
        if self.scheduler is None:  # (it turned off)
            scheduler._db.close()

    def Claim(self, process: Process):
        begins = process.claim()
        self.record("Claim", due=process.due, life=process.life, begins=begins)

    def KillLoading(self, process: Process):
        process.kill()
        self.kills += 1
        self.record("KillLoading", due=process.due, life=process.life)

    def RunFinish(self, process: Process):
        process.finish()
        self.record("RunFinish", due=process.due)

    def KillRun(self, process: Process):
        process.kill()
        self.kills += 1
        self.record("KillRun", due=process.due)

    def ManualBegin(self, f: str):  # (you run it in the editor: it records itself as it begins)
        run_id = self.history.start_run(f, str(self.paths[f]), {}, self.clock.isoformat())
        self.by_hand[f] = {"id": run_id, "pid": 2 ** 29 + len(self.by_hand), "ended": False}
        self.db.execute("UPDATE runs SET pid=?, began=? WHERE id=?",
                        (self.by_hand[f]["pid"], self.clock.timestamp(), run_id))
        self.manuals += 1
        self.record("ManualBegin", flow=f)

    def ManualEnd(self, f: str):
        self.history({"event": "run_finished", "run_id": self.by_hand[f]["id"], "ts": self.clock.isoformat(),
                      "status": "success"})
        self.by_hand[f]["ended"] = True
        self.record("ManualEnd", flow=f)

    def ChangeSchedule(self, f: str, off: bool):  # (the editor saves the flow, its schedule changed or turned off)
        self.write_flow(f, CRON[1] if not off else CRON[0], active=not off)
        service.forget_schedules(self.home, self.paths[f], load_flow(self.paths[f]).triggers)
        self.changes += 1
        self.record("ChangeSchedule", flow=f)

    def Proceed(self):  # (the dialog lists the flows with runs going; you choose for each, and click Proceed)
        choices = {spelled: self.choose(["finish", "stop"]) for spelled, _ in service.runs_going(self.home)}
        self.offs += 1
        service.request_turn_off(self.home, choices)  # (recorded as it stops the runs: stop_runs_going)

    def TurnOn(self):
        service.set_paused(self.home, False)
        self.record("TurnOn")


STOP_RUNS_GOING, TELL = service._stop_runs_going, service.tell


# --- traces as TLA+ modules, and TLC ---------------------------------------------------------------------------------

def generate(seed: int, root: Path) -> dict:
    """A generated trace: flows' Overlap and Misfire, and 20 to 45 steps, chosen by `seed`."""
    rng = random.Random(seed)
    overlap = {f: rng.choice(["skip", "queue"]) for f in FLOWS}
    misfire = {f: rng.choice(["skip", "catch_up"]) for f in FLOWS}
    driver = Driver(root, overlap, misfire, rng.choice)
    with driver.patched():
        for _ in range(rng.randint(20, 45)):
            steps = driver.enabled()
            if not steps:  # (the bounds are used up)
                break
            _, name, *args = rng.choices(steps, [weight for weight, *_ in steps])[0]
            driver.step(name, *args)
    return {"name": f"seed {seed}", "overlap": overlap, "misfire": misfire, "start": driver.start,
            "events": driver.events}


def refused_claim(root: Path) -> dict:
    """A trace in which a run's claim is refused: the service starts f1's run for its time; you start f1 by hand while
    its program loads; it claims, and is refused (SCH-11: a run of its flow is going); your run ends."""
    overlap, misfire = {"f1": "skip", "f2": "queue"}, {"f1": "skip", "f2": "skip"}
    driver = Driver(root, overlap, misfire)
    with driver.patched():
        for name in ("ServiceStart", "Check", "Tick", "Check"):
            driver.step(name)
        driver.step("ManualBegin", "f1")
        driver.step("Claim", driver.loading()[0])
        driver.step("ManualEnd", "f1")
    return {"name": "a refused claim", "overlap": overlap, "misfire": misfire, "start": driver.start,
            "events": driver.events}


def planted(trace: dict) -> tuple[dict, int]:
    """The trace up to its first refused claim, with that claim recorded as begun, as a claim that begins shows it (its
    run going, started by its process's service; its row and its process gone); and that event's number. Only the
    model's rule for the claim can tell it from a claim that began."""
    n = next(n for n, e in enumerate(trace["events"], 1) if e["kind"] == "Claim" and not e["begins"])
    claim = trace["events"][n - 1]
    state = {**claim["state"], "runs": claim["state"]["runs"] | {(claim["due"], "going", claim["life"])},
             "rows": {(due, kind) for due, kind in claim["state"]["rows"] if due != claim["due"]}}
    return {**trace, "name": f"{trace['name']}, recorded as begun",
            "events": trace["events"][:n - 1] + [{**claim, "begins": True, "state": state}]}, n


def tla(value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, str)):
        return json.dumps(value)
    if isinstance(value, tuple):
        return "<<" + ", ".join(map(tla, value)) + ">>"
    if isinstance(value, (set, frozenset)):
        return "{" + ", ".join(sorted(map(tla, value))) + "}"
    if isinstance(value, dict):
        return "[" + ", ".join(f"{k} |-> {tla(v)}" for k, v in value.items()) + "]"
    raise TypeError(f"no TLA+ value for {value!r}")


def module(trace: dict) -> str:
    events = ",\n    ".join(tla(e) for e in trace["events"])
    return (f"---- MODULE SchedulerTrace ----\n"
            f"\\* Generated by packaging/tla_trace.py ({trace['name']}); checked by TraceSchedulerService.tla.\n"
            f"EXTENDS Integers\n"
            f"Setup == {tla({'overlap': trace['overlap'], 'misfire': trace['misfire'], 'state': trace['start']})}\n"
            f"Trace == <<\n    {events}\n>>\n====\n")


def jar() -> Path:
    return Path(os.environ.get("TLA2TOOLS_JAR") or JAR)


def check(trace: dict, folder: Path, config: str = "TraceSchedulerService.cfg") -> dict:
    """TLC's verdict on the trace (written to folder/SchedulerTrace.tla; TLC's log: folder/tlc.log): {matched, and
    if not, unmatched: the number of the first event no behaviour of the model matches (0: the start)}. `config`: the
    name of TLC's configuration file, in specs/formal or in `folder` (TLC finds it by its name there)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SchedulerTrace.tla").write_text(module(trace), encoding="utf-8")
    command = ["java", "-XX:+UseParallelGC", "-Xmx1g", f"-DTLA-Library={folder}", "-cp", str(jar()), "tlc2.TLC",
               "-workers", "1", "-metadir", str(folder / "states"), "-config", config,
               str(SPEC / "TraceSchedulerService.tla")]
    logs = []
    for _ in range(3):  # (TLC 1.7.4 now and then fails to parse a spec it parses the next time: not a verdict)
        done = subprocess.run(command, cwd=folder, capture_output=True, text=True, errors="replace", timeout=1800)
        logs.append(done.stdout + done.stderr)
        (folder / "tlc.log").write_text("\n".join(logs), encoding="utf-8")
        shutil.rmtree(folder / "states", ignore_errors=True)
        if done.returncode == 12 and "Invariant TraceIncomplete is violated" in logs[-1]:
            return {"matched": True}
        depth = re.search(r"depth of the complete state graph search is (\d+)", logs[-1])
        if done.returncode == 0 and "No error has been found" in logs[-1] and depth:
            return {"matched": False, "unmatched": int(depth.group(1))}  # (the state at depth k matched k - 1 events)
        if "Parsing or semantic analysis failed" not in logs[-1]:
            break
    raise RuntimeError(f"TLC did not check {folder / 'SchedulerTrace.tla'} (exit {done.returncode}): see "
                       f"{folder / 'tlc.log'}")


def kinds(trace: dict) -> set[str]:
    return {f"Claim ({'begins' if e['begins'] else 'refused'})" if e["kind"] == "Claim" else e["kind"]
            for e in trace["events"]}


def described(trace: dict, n: int) -> str:
    if n == 0:
        return "its start"
    e = trace["events"][n - 1]
    return f"event {n} of {len(trace['events'])}, " + " ".join(
        [e["kind"]] + [f"{k}={tla(v)}" for k, v in e.items() if k not in ("kind", "state")])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--traces", type=int, default=TRACES)
    p.add_argument("--jobs", type=int, default=4)
    a = p.parse_args(argv)
    if not jar().is_file() or shutil.which("java") is None:
        print(f"cannot check: TLC needs java on the PATH and {jar()} (or $TLA2TOOLS_JAR)")
        return 2
    started, out = time.monotonic(), a.out.resolve()
    shutil.rmtree(out, ignore_errors=True)
    traces = [generate(seed, out / "code" / f"trace-{seed:03d}") for seed in range(a.traces)]
    real = refused_claim(out / "code" / "refused-claim")
    planted_trace, n = planted(real)
    folders = [out / f"trace-{seed:03d}" for seed in range(a.traces)] + [out / "refused-claim", out / "planted"]
    with ThreadPoolExecutor(a.jobs) as pool:
        verdicts = list(pool.map(check, traces + [real, planted_trace], folders))
    shutil.rmtree(out / "code", ignore_errors=True)
    failed = False
    for trace, verdict, folder in zip(traces + [real], verdicts, folders):
        if not verdict["matched"]:
            failed = True
            print(f"{folder.name} ({trace['name']}) does not match the model: no behaviour of it matches "
                  f"{described(trace, verdict['unmatched'])} (see {folder / 'tlc.log'})")
    if verdicts[-1]["matched"] or verdicts[-1]["unmatched"] != n:
        failed = True
        print(f"the planted divergence ({planted_trace['name']}) was not caught at its event {n}: {verdicts[-1]}")
    covered = set().union(*(kinds(t) for t, v in zip(traces, verdicts) if v["matched"]))
    matched = sum(v["matched"] for v in verdicts[:len(traces)])
    missing = [k for k in ACTIONS if k not in covered]
    print(f"{len(traces)} traces, " + ("all match the model" if matched == len(traces) else
                                         f"{matched} match the model, {len(traces) - matched} do not")
          + f"; actions covered: {', '.join(k for k in ACTIONS if k in covered)}"
          + (f"; NOT covered: {', '.join(missing)}" if missing else "")
          + f"; a planted divergence (a refused claim recorded as begun) is {'not ' * verdicts[-1]['matched']}caught"
          + f" ({time.monotonic() - started:.0f} s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
