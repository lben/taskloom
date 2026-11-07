"""The scheduler: runs scheduled flows, and keeps the scheduler on servers alive.

`taskloom scheduler` shows a tray icon with notifications on a desktop;
`taskloom scheduler --headless` is the same loop without a window, for servers.
Each run is a separate `taskloom run` process, like runs from the editor.

The service works in moments (UTC): a schedule's clock time is when this PC's clock shows it, and every N minutes or
hours is real elapsed time, so a clock change neither moves nor loses a run (taskloom.schedule). Runs waiting their
turn are saved in the history database (waiting_runs) until they start: however the service stops, they start once it
is back.
"""

from __future__ import annotations

import bisect
import contextlib
import datetime as dt
import html
import json
import math
import os
import queue
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, mail, ssh
from .config import Home
from .flow import FlowError, load_flow, real_path
from .history import History
from .history import connect as history_connect
from .launch import pid_alive, runner_command, windowed_command
from .report import Html

UTC = dt.timezone.utc
TICK_SECONDS = 1  # it checks at least this often: a schedule saved (or changed) applies within a second
# A run's process starts this long before its time: loaded by then, it begins at its time.
EARLY_START = dt.timedelta(seconds=5)
NOT_DUE = 3  # the exit code of a run started early that did not begin (see why_not_due)
ON_TIME_GRACE = dt.timedelta(minutes=2)  # later than this, a run counts as missed
ASK_OVER = 100  # more missed Catch up times than this: you are asked first (with no one to ask, the latest 100 run)
STOP_SECONDS = 6  # a run asked to stop that is still going this long after is forced to end
HEARTBEAT_STALE_SECONDS = 120
KEEPER_INTERVAL_SECONDS = 300
KEEPER_START_SECONDS = 30  # how long the keeper waits for a server scheduler it started to check
KEEPER_STOP_SECONDS = 5  # how long it waits for a hung one asked to stop, before it forces it
RUNS_FILE = "scheduler.runs"  # the runs going, as the service last saw them (see runs_going)
TURN_OFF_FILE = "scheduler.turnoff"  # you turned the service off: what to do with each flow's runs going


@dataclass
class _Run:
    flow: str
    as_of: dt.datetime | None  # the moment it is for (None: a run going from before the service restarted)
    proc: subprocess.Popen
    block: str | None = None  # the Schedule block it is for
    schedule: str = ""  # that schedule as it was when it started, e.g. "cron 0 7 * * *"
    run_id: int | None = None
    status: str | None = None
    errors: list = field(default_factory=list)  # what failed: "<block>: <error>"
    output: list = field(default_factory=list)  # its last lines that are not events (e.g. why it was refused)
    reader: threading.Thread | None = None
    stopping: float | None = None  # when it was asked to stop (time.monotonic())
    adopted: bool = False  # started before the service restarted: it tells about itself


@dataclass
class _Waiting:
    """A run waiting its turn, saved in the history database (waiting_runs) until it starts."""
    id: int  # (in the order they came)
    flow: str
    as_of: dt.datetime  # the moment it is for
    block: str | None
    schedule: str
    waits: str  # for "slot": a free slot (max_concurrent_runs); "flow": its flow's runs going; "answer": your answer


class _Adopted:
    """The process of a run started before the service restarted: not its child, so known only by its PID."""

    def __init__(self, pid: int):
        self.pid, self.returncode, self.stdin = pid, None, None

    def poll(self):
        if self.returncode is None and not pid_alive(self.pid):
            self.returncode = 0
        return self.returncode

    def terminate(self):  # (a run cancels on SIGTERM; on Windows this ends it at once)
        with contextlib.suppress(OSError):
            os.kill(self.pid, signal.SIGTERM)

    def kill(self):
        with contextlib.suppress(OSError):
            os.kill(self.pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def _moment(text: str) -> dt.datetime:
    """A moment saved as ISO text; one saved without its UTC offset (by an earlier Taskloom) is a local time."""
    return dt.datetime.fromisoformat(text).astimezone(UTC)


class Scheduler:
    def __init__(self, home: Home, notify=None, ask=None):
        """`ask(flow name, count, answer)`, when more than ASK_OVER times of a Catch up schedule were missed: it
        returns at once, and answer(limit) is called later: None runs them all, N only the latest N. Without it (a
        server, no one to ask), the latest ASK_OVER run, and you are told how many were missed."""
        self.home = home
        self.notify = notify or (lambda title, message: None)
        self.ask = ask
        self.runs: list[_Run] = []
        self.queued: dict[str, list[_Waiting]] = {}  # per flow, waiting for its runs going (in the order they came)
        self.waiting: list[_Waiting] = []  # waiting for a free slot (max_concurrent_runs), in the order they came
        self.asking: dict[tuple, list[_Waiting]] = {}  # per schedule (flow, block, schedule): missed, asked about
        self.turning_off: dict[str, str] | None = None  # once you turned it off: {flow: "finish" | "stop"}
        self.off = False  # turned off, and nothing it lets finish is going or waiting: the service ends
        self.messages: queue.Queue = queue.Queue()  # from the keeper thread and the emails being sent
        self._answers: dict[tuple, int | None] = {}  # your answers about missed runs, applied at the next check
        self._flows: dict[str, tuple[float, object]] = {}
        self._sending: list[threading.Thread] = []  # emails being sent
        self._list_error: str | None = None  # why scheduled.yaml cannot be read (told once)
        self._runs_told: list | None = None  # what scheduler.runs says
        home.root.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            (home.root / TURN_OFF_FILE).unlink(missing_ok=True)  # (one for a service that has gone)
        self._db = history_connect(home.history_db)
        self._db.execute("CREATE TABLE IF NOT EXISTS schedule_state (trigger TEXT PRIMARY KEY, last TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS waiting_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                         "flow TEXT NOT NULL, block TEXT, schedule TEXT NOT NULL, due TEXT NOT NULL, waits TEXT NOT NULL)")
        self._restore()

    # --- state ------------------------------------------------------------------

    def _last(self, key: str) -> dt.datetime | None:
        row = self._db.execute("SELECT last FROM schedule_state WHERE trigger=?", (key,)).fetchone()
        return _moment(row[0]) if row else None

    def _set_last(self, key: str, when: dt.datetime):
        self._db.execute("INSERT INTO schedule_state VALUES (?, ?) ON CONFLICT (trigger) DO UPDATE SET last=excluded.last",
                         (key, when.isoformat()))

    @contextlib.contextmanager
    def _changes(self):
        """Changes to the saved runs, made together: all or none, and quicker than one by one."""
        with self._db:  # (commits at the end, or rolls back)
            self._db.execute("BEGIN")
            yield self._db

    def _save(self, flow: str, times: list, block: str | None, schedule: str, waits: str) -> list[_Waiting]:
        """Runs of one schedule waiting their turn (see _Waiting.waits), saved until they start."""
        with self._changes() as db:
            return [_Waiting(db.execute("INSERT INTO waiting_runs (flow, block, schedule, due, waits) VALUES "
                                        "(?, ?, ?, ?, ?)", (flow, block, schedule, t.isoformat(), waits)).lastrowid,
                             flow, t, block, schedule, waits) for t in times]

    def _forget(self, entries: list[_Waiting]):
        """Waiting runs that start now, or are dropped."""
        with self._changes() as db:
            db.executemany("DELETE FROM waiting_runs WHERE id=?", [(entry.id,) for entry in entries])

    def _restore(self):
        """The runs waiting their turn that a service before this one saved, and the runs it started still going."""
        for row in self._db.execute("SELECT id, flow, due, block, schedule, waits FROM waiting_runs ORDER BY id"):
            entry = _Waiting(row[0], row[1], _moment(row[2]), *row[3:])
            if entry.waits == "answer":
                self.asking.setdefault((entry.flow, entry.block, entry.schedule), []).append(entry)
            elif entry.waits == "flow":
                self.queued.setdefault(entry.flow, []).append(entry)
            else:
                self.waiting.append(entry)
        for schedule, backlog in list(self.asking.items()):
            self._ask_about(schedule, len(backlog))
        self._adopt()

    def _adopt(self):
        """The runs the service before this one started that are still going (as it said in scheduler.runs, and as the
        history says): they count as going, for Overlap, Max concurrent runs and catching up one at a time."""
        try:
            told = json.loads((self.home.root / RUNS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):  # none (or not written by a service)
            return
        history = History(self.home.history_db)
        try:
            running = {run["id"]: run["pid"] for run in history.running()}
        finally:
            history.close()
        for run in told if isinstance(told, list) else []:
            pid = running.get(run.get("run")) if isinstance(run, dict) else None
            if pid and pid != os.getpid() and pid_alive(pid):
                self.runs.append(_Run(str(run.get("flow")), None, _Adopted(pid), run.get("block"),
                                      str(run.get("schedule")), run_id=run["run"], adopted=True))

    def _busy(self, path: str) -> bool:
        """A run of this flow is going, queued, or waiting for a free slot."""
        return (any(r.flow == path and r.stopping is None for r in self.runs) or bool(self.queued.get(path))
                or any(w.flow == path for w in self.waiting))

    def _catching_up(self, schedule: tuple) -> bool:
        """A run of this schedule ((flow, block, schedule)) is going or waiting its turn."""
        return (any((r.flow, r.block, r.schedule) == schedule and r.stopping is None for r in self.runs)
                or any((w.flow, w.block, w.schedule) == schedule
                       for w in self.waiting + [w for line in self.queued.values() for w in line]))

    def _load(self, path: Path):
        """The flow, cached until the file changes; None (and one notification) if it cannot be read."""
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = -1.0
        cached = self._flows.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            flow = load_flow(path)
        except FlowError as e:
            flow = None
            newer = any(m.startswith(("unknown top-level key", "unsupported flow format")) or ": unknown key" in m
                        for m in e.errors)
            hint = (f" (this scheduler is Taskloom {__version__}: if a newer Taskloom saved the flow, restart the "
                    "scheduler from it)") if newer else ""
            self.notify("Scheduled flow cannot be read", f"{path.name}: {e.errors[0]}{hint}")
        self._flows[str(path)] = (mtime, flow)
        return flow

    def _name(self, path: str) -> str:
        flow = self._load(Path(path))
        return flow.name if flow else Path(path).stem

    # --- the loop -----------------------------------------------------------------

    def tick(self, now: dt.datetime | None = None):
        """A check at `now`, this PC's clock time (as datetime.now() gives it: fold=1 in the hour the clock shows a
        second time)."""
        now = (now or dt.datetime.now()).astimezone(UTC)
        self._poll()
        paths = self._scheduled_flows() if self.turning_off is None else None  # (turned off: nothing new starts)
        on, unreadable = set(), []
        for path in paths or []:
            flow = self._load(path)
            if flow is None:  # (its schedules keep their state until it can be read again)
                unreadable.append(f"{path}#")
                continue
            self._stop_superseded(str(path), flow, now)
            for i, trigger in enumerate(flow.triggers):
                key = schedule_key(path, i, trigger)
                on.add(key)
                self._check(str(path), flow, trigger, key, now)
        if paths is not None:
            # A schedule that is off (its Schedule block's Active unticked, its flow not in scheduled.yaml) is
            # forgotten: on again, it starts from then as a new one does, and the times while it was off neither
            # run nor count as missed.
            stored = {row[0] for row in self._db.execute("SELECT trigger FROM schedule_state")}
            off = [key for key in stored - on if not key.startswith(tuple(unreadable))]
            self._db.executemany("DELETE FROM schedule_state WHERE trigger=?", [(key,) for key in off])
        self._write_heartbeat()

    def _check(self, path: str, flow, trigger, key: str, now: dt.datetime):
        """The runs of one of the flow's schedules due since its last check, and those due in the next EARLY_START
        (their processes start early, to begin on time), as its Misfire and the flow's Overlap say."""
        last = self._last(key)
        if last is None:  # a new schedule starts from now; nothing is "missed"
            # Every N minutes or hours: its first run is now (to the second), the next ones N after it.
            last = now.replace(microsecond=0) - trigger.every if trigger.every else now
            self._set_last(key, last)
        due = trigger.due(last, now + EARLY_START)  # since the previous check, and the ones soon
        if not due:
            return
        # Times in the early window are the newest in this check (the run that should begin on time). Misfire
        # applies to them together with the times already due. Asking for every soon time as well as the newest past
        # time dropped the on-time run: the past one had just started, and overlap skip then refused the soon one.
        past, soon = [t for t in due if t <= now], [t for t in due if t > now]
        # The newest time handled: nothing comes between it and now + EARLY_START, and an every-N schedule counts on
        # from it (one of its times).
        self._set_last(key, due[-1])
        schedule = (path, trigger.block, trigger.text)
        if schedule in self.asking or (trigger.misfire == "catch_up" and len(past) > ASK_OVER):
            self._backlog(schedule, due, len(past))
            return
        planned = self._misfire(flow, trigger, past, soon, now)
        # A catch-up of more than one time runs one after another, including behind a run that is already going
        # (force_queue on every time but the first used to skip the oldest). With Overlap allow, a missed time also
        # waits for this schedule's catch-up still going: catch-up runs go one at a time.
        behind = trigger.misfire == "catch_up" and (len(planned) > 1 or (
            flow.overlap == "allow" and any(t < now for t in past) and self._catching_up(schedule)))
        for as_of in planned:
            self._request(path, flow, as_of, trigger.block, trigger.text, behind)

    def _misfire(self, flow, trigger, past: list, soon: list, now: dt.datetime) -> list:
        """The times to run of those due, as the schedule's Misfire says."""
        if trigger.misfire == "run_once":
            return (past + soon)[-1:]  # one run, for the most recent time
        if trigger.misfire == "catch_up":
            return past + soon
        # skip: only the on-time run. The one we start early is that run; older times were missed.
        if soon:
            planned, missed = soon[:1], len(past)
        elif past and now - past[-1] <= ON_TIME_GRACE:
            planned, missed = past[-1:], len(past) - 1
        else:
            planned, missed = [], len(past)
        if missed:
            self._tell(flow, "skipped", "Missed runs skipped",
                       f"{flow.name}: {missed} run(s) were due while the scheduler was off")
        return planned

    def _backlog(self, schedule: tuple, due: list, missed: int):
        """More than ASK_OVER missed times of a Catch up schedule: they wait (saved) for your answer, and so do its
        times that come meanwhile; other flows run as usual."""
        asked = schedule in self.asking
        self.asking.setdefault(schedule, []).extend(self._save(schedule[0], due, schedule[1], schedule[2], "answer"))
        if not asked:
            self._ask_about(schedule, missed)

    def _ask_about(self, schedule: tuple, count: int):
        if self.ask is None:  # (a server: no one to ask)
            self._answer(schedule, ASK_OVER)
            return

        def answer(limit: int | None):  # (applied at the next check)
            self._answers[schedule] = limit

        self.ask(self._name(schedule[0]), count, answer)

    def _answer(self, schedule: tuple, limit: int | None):
        """About a schedule's missed runs: None runs them all, N only the latest N; the older ones count as missed,
        and you are told how many. They run oldest first, one at a time."""
        backlog = self.asking.pop(schedule, [])
        dropped = backlog[:max(0, len(backlog) - limit)] if limit is not None else []
        kept = backlog[len(dropped):]
        with self._changes() as db:
            db.executemany("DELETE FROM waiting_runs WHERE id=?", [(entry.id,) for entry in dropped])
            db.executemany("UPDATE waiting_runs SET waits='flow' WHERE id=?", [(entry.id,) for entry in kept])
        for entry in kept:
            entry.waits = "flow"
        path = schedule[0]
        self.queued[path] = sorted(self.queued.get(path, []) + kept, key=lambda entry: entry.id)
        if dropped:
            name = self._name(path)
            self._tell(self._load(Path(path)), "skipped", "Missed runs skipped", (
                f"{name}: you chose to run only the latest {limit} of {len(backlog)} missed runs; the other "
                f"{len(dropped)} are not run" if self.ask else
                f"{name}: {len(backlog)} runs were due while the scheduler was off, more than {ASK_OVER}: the latest "
                f"{limit} run, oldest first, and the other {len(dropped)} are missed"))

    def _scheduled_flows(self) -> list[Path] | None:
        """The flows in scheduled.yaml; None (and one notification, until that changes) if it cannot be read."""
        try:
            paths = self.home.scheduled_flows()
        except (OSError, ValueError) as e:  # e.g. edited by hand: you are told, and the service goes on
            if str(e) != self._list_error:
                self.notify("The list of scheduled flows cannot be read",
                            f"{e}\n(no scheduled flow starts until it is put right)")
            self._list_error = str(e)
            return None
        self._list_error = None
        return paths

    def _request(self, path: str, flow, as_of: dt.datetime, block: str | None, schedule: str, behind: bool = False):
        """A run of the flow's schedule for the moment `as_of`. A run waiting for a free slot counts as going.
        `behind`: it waits for the flow's runs going whatever its Overlap (a catch-up runs one at a time)."""
        if self._busy(path):
            policy = "queue" if behind else flow.overlap
            if policy == "skip":
                self._tell(flow, "skipped", "Run skipped", f"{flow.name}: the previous run is still going")
                return
            if policy == "queue":
                self.queued.setdefault(path, []).extend(self._save(path, [as_of], block, schedule, "flow"))
                return
        if self.waiting or self._at_limit():
            self.waiting.extend(self._save(path, [as_of], block, schedule, "slot"))
        else:
            self._start(path, as_of, block, schedule)

    def _at_limit(self) -> bool:
        """As many runs are going as max_concurrent_runs allows. A mistake in the settings holds no run back
        (a run that cannot go either says why): you are told, and the service goes on."""
        try:
            limit = self.home.settings()["max_concurrent_runs"]
            if limit and not str(limit).isdigit():
                raise ValueError(f"settings.yaml: max_concurrent_runs must be a whole number, got {limit!r}")
        except (OSError, ValueError) as e:
            self.notify("Mistake in the settings", str(e))
            return False
        return bool(limit) and len(self.runs) >= int(limit)

    def _start(self, path: str, as_of: dt.datetime, block: str | None, schedule: str):
        # Started early, it loads, then waits for its time (a time that has passed: it begins at once); then it begins
        # only if the service is on and the schedule is still this one with this time (--if-scheduled). It goes on if
        # the service stops; only a request on its stdin stops it (--stoppable).
        command = runner_command() + ["run", path, "--json-events", "--notify", "--stoppable",
                                      "--as-of", f"{as_of.astimezone():%Y-%m-%d %H:%M:%S}",
                                      "--start-at", _start_at(as_of), "--if-scheduled", schedule]
        if block:
            command += ["--trigger", block]
        # errors="replace": a program the run starts (Java, a console tool) may write bytes that are not text in
        # this encoding straight to the output; reading must go on, or the run hangs once the pipe is full.
        proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            errors="replace",
            env={**os.environ, "TASKLOOM_HOME": str(self.home.root)},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        run = _Run(path, as_of, proc, block, schedule)
        run.reader = threading.Thread(target=self._read, args=(run,), daemon=True)
        run.reader.start()
        self.runs.append(run)

    def _stop_superseded(self, path: str, flow, now: dt.datetime):
        """A run started early for a schedule of the flow that has changed (or gone) since does not begin: it is stopped
        now, so it does not hold up the changed schedule, which asks for that time itself."""
        current = {(trigger.block, trigger.text) for trigger in flow.triggers}
        for run in self.runs:
            if (run.flow == path and run.as_of is not None and run.as_of > now and run.stopping is None
                    and (run.block, run.schedule) not in current):
                self._stop(run)

    def _stop(self, run: _Run):
        """Asks the run to stop, as the editor's Stop does; _poll forces it if it is still going STOP_SECONDS later."""
        run.stopping = time.monotonic()
        try:
            if run.proc.stdin is None:  # (one from before the service restarted)
                run.proc.terminate()
            else:
                run.proc.stdin.write(json.dumps({"cancel": True}) + "\n")
                run.proc.stdin.flush()
        except OSError:  # it has just ended
            pass

    def turn_off(self, choices: dict[str, str]):
        """You turned the service off (request_turn_off): it stays off, and nothing new starts. For each flow in
        `choices`, "stop": its runs going stop (recorded as cancelled) and its waiting runs are dropped; "finish": its
        runs going finish, and its waiting runs still run. The runs going of other flows finish too; their waiting runs
        stay saved, to start when you turn it on again. Once nothing it lets finish is going or waiting, it is off."""
        set_paused(self.home, True)
        self.turning_off = dict(choices)
        stop = {path for path, choice in choices.items() if choice == "stop"}
        for run in self.runs:
            if run.flow in stop and run.stopping is None:
                self._stop(run)
        dropped = ([w for w in self.waiting if w.flow in stop] + [w for path in stop for w in self.queued.pop(path, [])]
                   + [w for schedule in list(self.asking) if schedule[0] in stop for w in self.asking.pop(schedule)])
        self.waiting = [w for w in self.waiting if w.flow not in stop]
        self._forget(dropped)

    @staticmethod
    def _read(run: _Run):
        for line in run.proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = None
            if not isinstance(event, dict):  # not an event: what the run or a program it started printed
                run.output = (run.output + [line.rstrip()])[-20:]
                continue
            if event.get("event") == "run_started":
                run.run_id = event["run_id"]
            elif event.get("event") == "block_finished" and event.get("status") == "failed":
                run.errors.append(f"{event['block']}: {event.get('error')}")
            elif event.get("event") == "run_finished":
                run.status = event["status"]

    def _poll(self):
        self._take_requests()
        for run in [r for r in self.runs if r.proc.poll() is not None]:
            self.runs.remove(run)
            self._ended(run)
        for run in self.runs:  # asked to stop, and still going: forced
            if run.stopping is not None and time.monotonic() - run.stopping > STOP_SECONDS:
                run.proc.kill()
        self._next_in_line()
        self._tell_runs()
        while not self.messages.empty():
            self.notify(*self.messages.get())
        if (self.turning_off is not None and not self.runs and not self._finishing()
                and not any(t.is_alive() for t in self._sending)):  # (its last emails sent)
            self.off = True

    def _take_requests(self):
        """Your answers about missed runs, and a request to turn the service off (request_turn_off)."""
        while self._answers:
            self._answer(*self._answers.popitem())
        request = self.home.root / TURN_OFF_FILE
        try:
            choices = json.loads(request.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # none (or being written: read at the next check)
            return
        with contextlib.suppress(OSError):  # (if it cannot go now, the next check applies it again: no harm)
            request.unlink()
        self.turn_off(choices if isinstance(choices, dict) else {})

    def _ended(self, run: _Run):
        """A run's process has ended: one that died without finishing is recorded so (as cancelled if you stopped it),
        and you are told what did not succeed (a crash also by email, as the flow asks)."""
        if run.reader is not None:
            run.reader.join(5)  # it has read the last lines once the process has exited
        if run.status is None and run.run_id is not None:
            history = History(self.home.history_db)
            history.mark_crashed(run.run_id, dt.datetime.now().isoformat(timespec="milliseconds"),
                                 cancelled=run.stopping is not None)
            history.close()
        # Nothing to tell for one you stopped, one from before the service restarted (it told about itself), or one
        # that did not begin, as its schedule or the service was turned off before its time.
        if (run.status == "success" or run.stopping is not None or run.adopted
                or (run.run_id is None and run.proc.returncode == NOT_DUE)):
            return
        name = Path(run.flow).stem
        detail = (run.errors or run.output or [f"exit code {run.proc.returncode}"])[-1]
        self.notify(f"{name} {run.status or 'failed'}", detail)
        if run.status is None:  # it crashed, so it could not email about itself
            self._tell(self._load(Path(run.flow)), "failure", f"{name} crashed", detail, desktop=False)

    def _next_in_line(self):
        """Runs waiting their turn start in the order they came: a flow's next one waiting for its runs joins the line
        for a free slot when none of its runs is going and none that came before it is in that line. While turning
        off, only those of the flows you let finish start."""
        for path, line in self.queued.items():
            if line and not any(r.flow == path for r in self.runs) and not any(
                    w.flow == path and w.id < line[0].id for w in self.waiting):
                bisect.insort(self.waiting, line.pop(0), key=lambda entry: entry.id)
        for entry in list(self.waiting):
            if self.turning_off is not None and self.turning_off.get(entry.flow) != "finish":
                continue
            if self._at_limit():
                break
            self.waiting.remove(entry)
            self._forget([entry])
            self._start(entry.flow, entry.as_of, entry.block, entry.schedule)

    def _finishing(self) -> bool:
        """While turning off: runs of the flows you let finish still wait their turn."""
        finish = {path for path, choice in self.turning_off.items() if choice == "finish"}
        return any(w.flow in finish for w in self.waiting) or any(self.queued.get(path) for path in finish)

    def _tell_runs(self):
        """scheduler.runs: the runs going, for turning the service off flow by flow (runs_going), and for the next
        service if this one stops while they go (they count as going for it)."""
        going = [{"flow": r.flow, "name": self._name(r.flow), "run": r.run_id, "block": r.block,
                  "schedule": r.schedule} for r in self.runs]
        if going != self._runs_told:
            part = self.home.root / (RUNS_FILE + ".part")
            try:
                part.write_text(json.dumps(going), encoding="utf-8")
                os.replace(part, self.home.root / RUNS_FILE)  # (never read half-written)
            except OSError:  # e.g. on Windows, being read this instant: written at the next check
                return
            self._runs_told = going

    def _tell(self, flow, event: str, title: str, message: str, desktop: bool = True):
        """A desktop notification, plus an email if the flow's notify settings ask for this event. The email is
        sent on a thread of its own, so a mail server slow to answer holds up no run (a problem is told after)."""
        if desktop:
            self.notify(title, message)
        flow_notify = flow.notify if flow else None

        def send():
            try:
                mail.send_notice(self.home, flow_notify, event, f"[Taskloom] {title}",
                                 Html(f"<p>{html.escape(message)}</p>"))
            except Exception as e:
                self.messages.put(("Cannot send email", str(e)))

        sending = threading.Thread(target=send, daemon=True)
        sending.start()
        self._sending = [t for t in self._sending if t.is_alive()] + [sending]

    def _write_heartbeat(self):
        (self.home.root / "scheduler.heartbeat").write_text(str(int(time.time())))

    def wait_for_runs(self, timeout: float = 120):
        """Poll until every started and queued run has finished and every email is sent (used by tests)."""
        deadline = time.monotonic() + timeout
        while (self.runs or any(self.queued.values()) or self.waiting
               or any(t.is_alive() for t in self._sending)) and time.monotonic() < deadline:
            time.sleep(0.2)
            self._poll()
        self._poll()  # what the last emails told

    def seconds_to_next_check(self, now: dt.datetime) -> float:
        """How long the loop waits after its check at `now`: until EARLY_START before the soonest due time it has
        not started yet, so that run begins on time, and at most TICK_SECONDS (edited flows, finished and queued
        runs)."""
        now = now.astimezone(UTC)
        times = [t for path in self._scheduled_flows() or [] if (flow := self._load(path))
                 for i, trigger in enumerate(flow.triggers)
                 if (t := trigger.next_after(now + EARLY_START,
                                             self._last(schedule_key(path, i, trigger)) if trigger.every else None))]
        seconds = (min(times) - EARLY_START - now).total_seconds() if times else TICK_SECONDS
        return max(0.0, min(TICK_SECONDS, seconds))

    # --- keeping server schedulers alive ----------------------------------------------

    def check_servers(self):
        """For each server in the keeper setting: start its scheduler if it is not running, replacing a hung one."""
        try:
            entries = self.home.settings()["keeper"] or []
        except (OSError, ValueError) as e:  # you are told, and the keeper goes on checking
            self.messages.put(("Mistake in the settings", str(e)))
            return
        for entry in entries:
            name = entry.get("connection") if isinstance(entry, dict) else entry
            try:
                if not isinstance(entry, dict):
                    raise ValueError("write a keeper entry as {connection: ..., command: ..., home: ...} "
                                     "(Tools → Settings writes it so)")
                remote_home = entry.get("home", "~/.taskloom")
                connections = self.home.connections()
                if name not in connections:
                    raise ValueError(f"no connection named '{name}' in connections.yaml")
                session = ssh.Session(self.home.resolve_secrets(connections[name]), self.home)
                try:
                    if self._remote_alive(session, remote_home):
                        continue
                    # A hung scheduler still holds the folder's lock, so a new one could not start: it is stopped first.
                    holders = self._lock_holders(session, remote_home)
                    if holders:
                        self._stop_lock_holders(session, remote_home, holders)
                    command = f"env TASKLOOM_HOME={ssh.remote_path(remote_home)} {entry['command']} scheduler --headless"
                    heartbeat = ssh.remote_path(f"{remote_home}/scheduler.heartbeat")
                    # (without the old heartbeat, a heartbeat says that the one started now checks)
                    code, _, err = session.run(f"rm -f {heartbeat} && " + ssh.start_detached(
                        command, "~", f"{remote_home}/scheduler.log", f"{remote_home}/scheduler.pid"))
                    if code != 0:
                        raise RuntimeError(err.strip())
                    problem = self._start_problem(session, remote_home)
                    if problem:
                        if holders is None:
                            problem += (f"; the keeper cannot tell which process holds its lock (to stop a hung one): "
                                        f"{name} has neither /proc nor lsof")
                        self.messages.put(("Server scheduler not started",
                                           f"{name}: the scheduler could not be started: {problem}"))
                    else:
                        self.messages.put(("Server scheduler restarted", f"{name}: " + (
                            "the scheduler was hung: it was stopped and a new one was started" if holders
                            else "the scheduler was not running and was started")))
                finally:
                    session.close()
            except Exception as e:
                self.messages.put(("Cannot check server", f"{name}: {e}"))

    @staticmethod
    def _remote_alive(session, remote_home: str) -> bool:
        """Its process is running and its heartbeat is recent (a hung scheduler counts as dead)."""
        pid_file, heartbeat = (ssh.remote_path(f"{remote_home}/{name}") for name in ("scheduler.pid", "scheduler.heartbeat"))
        # A heartbeat read empty may be one being written this instant (every second): read again, as a scheduler
        # taken for hung is stopped.
        _, out, _ = session.run(f'kill -0 "$(cat {pid_file} 2>/dev/null)" 2>/dev/null && echo up || echo down; '
                                f'beat="$(cat {heartbeat} 2>/dev/null)"; '
                                f'[ -n "$beat" ] || {{ sleep 1; beat="$(cat {heartbeat} 2>/dev/null)"; }}; '
                                f'echo "$beat"; date +%s')
        lines = out.split()
        numbers = [int(x) for x in lines[1:] if x.isdigit()]
        return lines[:1] == ["up"] and len(numbers) == 2 and numbers[1] - numbers[0] < HEARTBEAT_STALE_SECONDS

    @staticmethod
    def _lock_holders(session, remote_home: str) -> list[str] | None:
        """The processes on the server that hold the folder's scheduler.lock (a scheduler keeps it open while it
        holds the lock), told from the lock file itself through /proc/<pid>/fd or lsof, never from a process name or
        scheduler.pid; None when the server has neither."""
        lock = ssh.remote_path(f"{remote_home}/scheduler.lock")
        _, out, _ = session.run(
            f'{{ if [ -d /proc/self/fd ]; then for fd in /proc/[0-9]*/fd/*; do [ "$fd" -ef {lock} ] && '
            f'p="${{fd#/proc/}}" && echo "${{p%%/*}}"; done; '
            f"elif command -v lsof >/dev/null; then lsof -t {lock}; else echo unknown; fi; }} 2>/dev/null")
        pids = out.split()
        return None if "unknown" in pids else list(dict.fromkeys(p for p in pids if p.isdigit()))

    @classmethod
    def _stop_lock_holders(cls, session, remote_home: str, pids: list[str]):
        """Asks the processes `pids` that hold the folder's lock to stop (SIGTERM), and forces (SIGKILL) only those
        that still hold it KEEPER_STOP_SECONDS later. It waits for them to end (at most KEEPER_STOP_SECONDS after each
        signal), not only to let go of the lock: a scheduler the keeper started writes its exit code as it ends, which
        must not be read as the new one's."""
        def wait_until_ended(of: list[str]):
            alive = "; ".join(f"kill -0 {p} 2>/dev/null && echo {p}" for p in of)
            deadline = time.monotonic() + KEEPER_STOP_SECONDS
            while time.monotonic() < deadline:
                time.sleep(0.5)
                if not session.run(alive)[1].split():
                    return

        session.run(f"kill {' '.join(pids)}")
        wait_until_ended(pids)
        if still := [p for p in cls._lock_holders(session, remote_home) or [] if p in pids]:
            session.run(f"kill -9 {' '.join(still)}")
            wait_until_ended(still)

    @staticmethod
    def _start_problem(session, remote_home: str) -> str | None:
        """None once the scheduler just started has checked (written its heartbeat); else why it has not."""
        exit_file, heartbeat, log = (ssh.remote_path(f"{remote_home}/{name}")
                                     for name in (ssh.exit_file("scheduler.pid"), "scheduler.heartbeat", "scheduler.log"))
        for _ in range(KEEPER_START_SECONDS):
            time.sleep(1)
            _, out, _ = session.run(f'echo "$(cat {exit_file} 2>/dev/null)"; echo "$(cat {heartbeat} 2>/dev/null)"; '
                                    f"tail -n 1 {log} 2>/dev/null")
            exited, beat, last = [part.strip() for part in (out.split("\n", 2) + ["", ""])[:3]]
            if exited:
                return f"it stopped at once (exit code {exited})" + (f": {last}" if last else "")
            if beat:
                return None
        return f"it has not checked anything {KEEPER_START_SECONDS} s after it started (see {remote_home}/scheduler.log)"


def _keeper_loop(scheduler: Scheduler, stop: threading.Event):
    while not stop.is_set():
        scheduler.check_servers()
        stop.wait(KEEPER_INTERVAL_SECONDS)


def paused(home: Home) -> bool:
    """You turned the scheduler service off: it does not start by itself until you turn it on."""
    return (home.root / "scheduler.paused").exists()


def set_paused(home: Home, value: bool):
    marker = home.root / "scheduler.paused"
    if value:
        home.root.mkdir(parents=True, exist_ok=True)
        marker.write_text("turned off by you\n")
    else:
        marker.unlink(missing_ok=True)


def running_scheduler_version(home: Home) -> str | None:
    """The version of the Taskloom whose scheduler wrote scheduler.pid last; None if it is older than the
    first version that says (or none ever ran)."""
    try:
        pid = (home.root / "scheduler.pid").read_text().strip()
        version, _, version_pid = (home.root / "scheduler.version").read_text().strip().partition(" ")
    except OSError:
        return None
    return version if version_pid == pid else None


def version_tuple(version: str | None) -> tuple:
    """'0.14.1' -> (0, 14, 1), to compare versions; () when unknown (older than any)."""
    try:
        return tuple(int(part) for part in str(version).split(".")[:3])
    except ValueError:
        return ()


def acquire_lock(home: Home):
    """Hold an exclusive lock for the life of this process, so only one scheduler runs per home."""
    home.root.mkdir(parents=True, exist_ok=True)
    handle = open(home.root / "scheduler.lock", "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)  # every process must lock the same byte
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError(f"a scheduler is already running for {home.root}") from None
    return handle


def schedule_key(path, i: int, trigger) -> str:
    """The row of schedule_state that holds the state of the scheduled flow `path`'s schedule flow.triggers[i]:
    changed, or turned off and on again, a schedule is a new one."""
    return f"{path}#{trigger.block or i}#{trigger.text}"


def counted_from(home: Home, path, triggers: list) -> list[dt.datetime | None]:
    """For each of the scheduled flow `path`'s schedules, where the scheduler service counts it from if it repeats
    every N minutes or hours: one of its times, the newest the service ran (or started early), on this PC's clock (as
    the editor shows times). None for the others, and while the service has not seen that schedule as it is now."""
    return [t.astimezone().replace(tzinfo=None) if t else None for t in _counted_from(home, path, triggers)]


def _counted_from(home: Home, path, triggers: list) -> list[dt.datetime | None]:
    """counted_from, as moments (UTC)."""
    keys = [schedule_key(path, i, t) if t.every else None for i, t in enumerate(triggers)]
    if not any(keys) or not home.history_db.exists():
        return [None] * len(keys)
    db = history_connect(home.history_db)
    try:
        rows = [db.execute("SELECT last FROM schedule_state WHERE trigger=?", (key,)).fetchone() if key else None
                for key in keys]
    except sqlite3.OperationalError:  # no service has made its table yet
        rows = [None] * len(keys)
    finally:
        db.close()
    return [_moment(row[0]) if row else None for row in rows]


def why_not_due(home: Home, path: Path, block: str | None, schedule: str, when: dt.datetime) -> str | None:
    """Why a run the scheduler service started early, for the time `when` (a local time, or with its UTC offset) of
    the flow `path`'s Schedule block `block` (None: its flow-level schedule) as that schedule was then (`schedule`,
    e.g. "cron 0 7 * * *"), does not begin at that time: the service was turned off, or that schedule no longer runs
    then (Active unticked, changed, even if it still has that time, the flow taken off scheduled.yaml: a changed
    schedule asks for its times itself). None: it begins."""
    try:
        acquire_lock(home).close()  # nobody holds it: no scheduler service runs
        return "the scheduler service is off"
    except RuntimeError:
        pass
    try:
        listed = [p for p in home.scheduled_flows() if real_path(p) == path]
        triggers = load_flow(path).triggers if listed else []
    except (OSError, ValueError, FlowError) as e:
        return f"{path.name} cannot be read: {e}"
    starts = _counted_from(home, listed[0], triggers) if listed else []
    moment = when.astimezone(UTC)
    if any(t.block == block and t.text == schedule and t.next_after(moment - dt.timedelta(seconds=1), start) == moment
           for t, start in zip(triggers, starts)):
        return None
    return f"{path.name} is not scheduled at {when.astimezone():%Y-%m-%d %H:%M} any more"


def _start_at(when: dt.datetime) -> str:
    """`taskloom run --start-at` for the moment `when`: its time on this PC's clock, with its UTC offset when the
    clock shows that time twice (the night it goes back), so that it names that one moment."""
    local = when.astimezone()
    clock = local.replace(tzinfo=None)
    once = clock.replace(fold=0).timestamp() == clock.replace(fold=1).timestamp()
    return (clock if once else local).isoformat(timespec="seconds")


def runs_going(home: Home) -> list[tuple[str, str]]:
    """The flows with runs going in the scheduler service, as it last said: (flow file, flow name), each flow once,
    in the order their runs started. For turning the service off flow by flow (request_turn_off)."""
    try:
        told = json.loads((home.root / RUNS_FILE).read_text(encoding="utf-8"))
        return list(dict.fromkeys((run["flow"], run["name"]) for run in told))
    except (OSError, ValueError, TypeError, KeyError):  # none, or not as the service writes it
        return []


def request_turn_off(home: Home, choices: dict[str, str]):
    """Turns the scheduler service off; it stays off until you turn it on. `choices`: for each flow with runs going
    (runs_going), "finish" (let its runs finish; its waiting runs still run) or "stop" (stop them now; its waiting runs
    are dropped); empty when none go. The service applies it within a second, and ends once what it lets finish is
    done (see Scheduler.turn_off)."""
    set_paused(home, True)
    (home.root / TURN_OFF_FILE).write_text(json.dumps(choices), encoding="utf-8")


def serve(home: Home, headless: bool) -> int:
    with acquire_lock(home):  # held until the scheduler stops
        # The keeper checks this PID, however the scheduler was started.
        (home.root / "scheduler.pid").write_text(str(os.getpid()))
        # The editor checks which Taskloom this is (with the PID: an older scheduler writes no version).
        (home.root / "scheduler.version").write_text(f"{__version__} {os.getpid()}")
        stop = threading.Event()
        if not headless and _tray_available():
            return _serve_tray(home, stop)

        def notify(title, message):
            print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {title}: {message}", flush=True)

        scheduler = Scheduler(home, notify)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        threading.Thread(target=_keeper_loop, args=(scheduler, stop), daemon=True).start()
        notify("Scheduler started", str(home.root))
        while not stop.is_set() and not scheduler.off:
            now = dt.datetime.now()
            scheduler.tick(now)
            stop.wait(scheduler.seconds_to_next_check(now))
        notify("Scheduler stopped", "turned off by you" if scheduler.off else
               f"{len(scheduler.runs)} run(s) still going continue on their own")
        return 0


def _tray_available() -> bool:
    try:
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    except ImportError:
        return False
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    return QSystemTrayIcon.isSystemTrayAvailable()


def _serve_tray(home: Home, stop: threading.Event) -> int:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    app = QApplication.instance()
    pix = QPixmap(32, 32)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#2c5d8f"))
    painter.setPen(QColor("#2c5d8f"))
    painter.drawRoundedRect(3, 3, 26, 26, 7, 7)
    painter.end()
    tray = QSystemTrayIcon(QIcon(pix))
    tray.setToolTip("Taskloom scheduler service: runs your scheduled flows at their times")
    scheduler = Scheduler(home, lambda title, message: tray.showMessage(title, message))
    menu = QMenu()
    open_editor = QAction("Open editor", menu)
    open_editor.triggered.connect(lambda: subprocess.Popen(windowed_command() + ["editor"],
                                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    quit_action = QAction("Turn off the scheduler service", menu)
    quit_action.triggered.connect(lambda: (set_paused(home, True), app.quit()))  # you turned it off: it stays off
    menu.addAction(open_editor)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.messageClicked.connect(open_editor.trigger)
    tray.show()
    threading.Thread(target=_keeper_loop, args=(scheduler, stop), daemon=True).start()
    timer = QTimer(singleShot=True, timerType=Qt.PreciseTimer)

    def check():
        now = dt.datetime.now()
        timer.start(TICK_SECONDS * 1000)  # the next check, also if this one fails
        scheduler.tick(now)
        if scheduler.off:  # turned off (Scheduler.turn_off), and what it let finish is done
            timer.stop()
            app.quit()
            return
        timer.start(math.ceil(scheduler.seconds_to_next_check(now) * 1000))

    timer.timeout.connect(check)
    check()
    try:
        return app.exec()
    finally:
        stop.set()


def startup_file() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("starting the scheduler at login is supported on Windows only")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Taskloom Scheduler.cmd"


def startup_script(home: Home) -> str:
    """A Startup-folder script (no admin needed) that starts the scheduler without a console window."""
    # The program is quoted, so a folder named e.g. R&D is not read as two commands.
    program = " ".join(f'"{_in_script(a)}"' for a in windowed_command())
    # cmd.exe reads a script in the console's code page (e.g. 437): the lines after chcp are read as the UTF-8
    # they are written in, so a folder such as C:\Users\José is not garbled.
    return (f'@echo off\nchcp 65001 >nul\nrem Taskloom {__version__}\nset "TASKLOOM_HOME={_in_script(home.root)}"\n'
            f'start "" {program} scheduler --unless-paused\n')  # unless you turned it off: then it stays off


def _in_script(text) -> str:
    """Text as a cmd.exe script writes it: also inside quotes, cmd.exe reads %NAME% as a variable."""
    return str(text).replace("%", "%%")


def startup_script_outdated(text: str, home: Home) -> bool:
    """A start-at-login script for this Taskloom folder that starts an older Taskloom than this one."""
    if f'TASKLOOM_HOME={_in_script(home.root)}"' not in text:
        return False  # another Taskloom folder's: not ours to change
    import re

    found = re.search(r"^rem Taskloom (\S+)", text, re.MULTILINE)
    return version_tuple(found.group(1) if found else None) < version_tuple(__version__)


def set_start_at_login(home: Home, enabled: bool) -> Path:
    path = startup_file()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(startup_script(home), encoding="utf-8", newline="\r\n")  # Windows line ends
    elif path.exists():
        path.unlink()
    return path

