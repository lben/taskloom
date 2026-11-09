"""The scheduler: runs scheduled flows, and keeps the scheduler on servers alive.

`taskloom scheduler` shows a tray icon with notifications on a desktop;
`taskloom scheduler --headless` is the same loop without a window, for servers.
Each run is a separate `taskloom run` process, like runs from the editor.

The service works in moments (UTC): a schedule's clock time is when this PC's clock shows it, and every N minutes or
hours is real elapsed time, so a clock change neither moves nor loses a run (taskloom.schedule). Runs waiting their
turn are saved in the history database (waiting_runs) until they begin: however the service stops, they start once it
is back. Every run the service starts, for its time (at it, or just after it) or after waiting its turn (missed, caught
up, queued, for a free slot), is saved first (a row of waiting_runs, saved as its time is handled in schedule_state, so
a time runs once), and claims that row as it begins, in one transaction with its own row in the run history
(claim_time), only while the service that started it is the one on (service_on) and has not been turned off since
(service_epoch). Refused, nothing is lost: a run for its time leaves that time due again (a missed time, if it comes to
that), a run that waited its turn stays saved. Every run that begins is in the run history, where the service on finds
those it did not start (Scheduler._adopt).
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
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, mail, ssh
from .config import Home, _write_whole
from .flow import FlowError, load_flow, real_path
from .history import History
from .history import connect as history_connect
from .launch import run_alive, runner_command, windowed_command
from .report import Html

UTC = dt.timezone.utc
TICK_SECONDS = 1  # it checks at least this often: a schedule saved (or changed) applies within a second
NOT_DUE = 3  # the exit code of a run the service started that did not begin (see claim_time)
SERVICE_OFF = "the scheduler service is off"  # why it did not begin, when that is why (see claim_time)
ON_TIME_GRACE = dt.timedelta(minutes=2)  # a time this late, or later, counts as missed: one less late runs on time
ASK_OVER = 100  # more missed Catch up times than this: you are asked first (with no one to ask, the latest 100 run)
STOP_SECONDS = 6  # a run asked to stop that is still going this long after is forced to end
HEARTBEAT_STALE_SECONDS = 120
KEEPER_INTERVAL_SECONDS = 300
KEEPER_START_SECONDS = 30  # how long the keeper waits for a server scheduler it started to check
KEEPER_STOP_SECONDS = 5  # how long it waits for a hung one asked to stop, before it forces it
LIST_UNREADABLE = "The list of scheduled flows cannot be read"  # (what you are told: no scheduled flow starts then)
# The service's state in the history database: each schedule's newest time handled; the runs it saved (_Waiting); the
# service on (its Scheduler.id; none while a service starts, acquire_lock, or before any has); and how many times you
# turned the service off or on again (its epoch: a run claims only in the epoch the service started it in).
STATE_TABLES = ("CREATE TABLE IF NOT EXISTS schedule_state (trigger TEXT PRIMARY KEY, last TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS waiting_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, flow TEXT NOT NULL, "
                "block TEXT, schedule TEXT NOT NULL, due TEXT NOT NULL, waits TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS service_on (id TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS service_epoch (epoch INTEGER NOT NULL)")


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
    waiting: _Waiting | None = None  # its row in waiting_runs, to claim as it begins (None: a run going from before)
    started: dt.datetime | None = None  # when its process started, by this PC's clock (its --if-scheduled says it)


@dataclass
class _Waiting:
    """A run the service is to start, or has started, saved in the history database (waiting_runs) until it begins."""
    id: int  # (in the order they came)
    flow: str
    as_of: dt.datetime  # the moment it is for
    block: str | None
    schedule: str
    # What it waits for. "slot": a free slot (max_concurrent_runs); "flow": its flow's runs going; "missed": your answer
    # about the schedule's missed runs (it is one of them); "answer": that answer too (it is not missed: it runs after
    # them). "now": nothing, a run for its time, started at it or just after it: it does not begin once you turned the
    # service off, nor if its program took more than ON_TIME_GRACE to load; its time is then due again
    # (Scheduler._check: a missed time, if it comes to that).
    waits: str


class _Adopted:
    """The process of a run started before the service restarted: not its child, so known only by its process id and
    the moment its run began, as its run history says (another program may have that id now: see run_alive)."""

    def __init__(self, pid: int | None, began: float | None):
        self.pid, self.began, self.returncode, self.stdin = pid, began, None, None

    def poll(self):
        if self.returncode is None and not run_alive(self.pid, self.began):
            self.returncode = 0
        return self.returncode

    def terminate(self):  # (a run cancels on SIGTERM; on Windows this ends it at once)
        self._signal(signal.SIGTERM)

    def kill(self):
        self._signal(getattr(signal, "SIGKILL", signal.SIGTERM))

    def _signal(self, sig):
        if self.poll() is None:  # (never another program's process)
            with contextlib.suppress(OSError):
                os.kill(self.pid, sig)


def _moment(text: str) -> dt.datetime:
    """A moment saved as ISO text; one saved without its UTC offset is a local time."""
    return dt.datetime.fromisoformat(text).astimezone(UTC)


class Scheduler:
    def __init__(self, home: Home, notify=None, ask=None):
        """`ask(flow name, count, answer)`, when more than ASK_OVER times of a Catch up schedule were missed: it
        returns at once, and answer(limit) is called later: None runs them all, N only the latest N. Without it (a
        server, no one to ask), the latest ASK_OVER run, and you are told how many were missed. Made, it is the service
        on (the table service_on): the runs a service before it started no longer begin (see claim_time)."""
        self.home = home
        self.id = uuid.uuid4().hex  # (the runs it starts say it, to claim what they are for: see claim_time)
        self.notify = notify or (lambda title, message: None)
        self.ask = ask
        self.runs: list[_Run] = []
        self.yours: set[str] = set()  # the flows with a run going that you started yourself (see _adopt)
        self.queued: dict[str, list[_Waiting]] = {}  # per flow, waiting for its runs going (in the order they came)
        self.waiting: list[_Waiting] = []  # waiting for a free slot (max_concurrent_runs), in the order they came
        self.asking: dict[tuple, list[_Waiting]] = {}  # per schedule (flow, block, schedule): missed, asked about
        self.again: dict[tuple, list[_Waiting]] = {}  # per schedule: runs for their time that did not begin
        self.turning_off: dict[str, str] | None = None  # once you turned it off: {flow: "finish" | "stop"}
        self.off = False  # turned off, and nothing it lets finish is going or waiting: the service ends
        self.messages: queue.Queue = queue.Queue()  # from the keeper thread and the emails being sent
        self._answers: dict[tuple, int | None] = {}  # your answers about missed runs, applied at the next check
        self._questions: dict[tuple, object] = {}  # per schedule asked about: the answer function its question has
        self._to_ask: set[tuple] = set()  # schedules to ask about at the end of this check (see _ask_about)
        self._flows: dict[str, tuple[float, object]] = {}
        self._sending: list[threading.Thread] = []  # emails being sent
        self._list_error: tuple | None = None  # what is wrong with scheduled.yaml, as told (once, until it changes)
        self._settings_told: bytes | None = None  # settings.yaml with a mistake, as told (once, until it changes)
        self._settings_lock = threading.Lock()  # (the keeper's thread reads the settings too)
        home.root.mkdir(parents=True, exist_ok=True)
        self._db = history_connect(home.history_db)
        for table in STATE_TABLES:
            self._db.execute(table)
        with self._changes() as db:
            db.execute("DELETE FROM service_on")
            db.execute("INSERT INTO service_on VALUES (?)", (self.id,))
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
        """Changes to the saved state, made together: all or none, and quicker than one by one; inside changes made
        together already (a check: see _check), part of those."""
        if self._db.in_transaction:
            yield self._db
            return
        with self._db:  # (commits at the end, or rolls back)
            self._db.execute("BEGIN IMMEDIATE")  # (no other process writes between its reads and its writes)
            yield self._db

    def _save(self, flow: str, times: list, block: str | None, schedule: str, waits: str,
              row: int | None = None) -> list[_Waiting]:
        """Runs of one schedule waiting their turn (see _Waiting.waits), saved until they start. `row`: the row a time
        due again had (one time), so it keeps its place in the line."""
        with self._changes() as db:
            return [_Waiting(db.execute("INSERT INTO waiting_runs (id, flow, block, schedule, due, waits) VALUES "
                                        "(?, ?, ?, ?, ?, ?)", (row, flow, block, schedule, t.isoformat(),
                                                               waits)).lastrowid,
                             flow, t, block, schedule, waits) for t in times]

    def _forget(self, entries: list[_Waiting]):
        """Waiting runs that are dropped."""
        with self._changes() as db:
            db.executemany("DELETE FROM waiting_runs WHERE id=?", [(entry.id,) for entry in entries])

    def _restore(self):
        """The runs waiting their turn that a service before this one saved (also those whose process it had started:
        that process does not begin now, see claim_time), and its times whose run for them did not begin: due again at
        the first check, as Misfire and Overlap say then. You are asked about a schedule's missed runs after the first
        check, which adds the times missed since."""
        for row in self._db.execute("SELECT id, flow, due, block, schedule, waits FROM waiting_runs ORDER BY id"):
            entry = _Waiting(row[0], row[1], _moment(row[2]), *row[3:])
            if entry.waits == "now":
                self.again.setdefault((entry.flow, entry.block, entry.schedule), []).append(entry)
            elif entry.waits in ("missed", "answer"):
                self.asking.setdefault((entry.flow, entry.block, entry.schedule), []).append(entry)
            else:
                self._put_in_line(entry)
        self._to_ask.update(self.asking)

    def _put_in_line(self, entry: _Waiting):
        """A run waiting its turn takes its place (by when it came) in its line: its flow's (queued), or the one for a
        free slot (waiting)."""
        line = self.queued.setdefault(entry.flow, []) if entry.waits == "flow" else self.waiting
        bisect.insort(line, entry, key=lambda waiting: waiting.id)

    def _line(self) -> list[_Waiting]:
        """The runs waiting their turn in either line, in the order they came."""
        return sorted(self.waiting + [entry for line in self.queued.values() for entry in line],
                      key=lambda waiting: waiting.id)

    def _adopt(self):
        """The runs going that a service started and this one does not know of, as the run history has them (each run
        records itself there as it begins): a service before this one's, also one that began just before this one
        started. Ours are known by their run ids, and those that have not said theirs yet by the flow file and schedule
        they record (not by their processes: on Windows the process a run records may be the child of the one this
        service started, a venv's python.exe being a launcher). They count as going, for Overlap, Max concurrent runs
        and catching up one at a time. One whose process has gone, or is another program's now (after a restart), is
        recorded as crashed at the next check, and you are told. Also the flows with a run going that you started
        yourself (its process still going): they count for Overlap only (`yours`, see _busy); whoever started them sees
        to them. (Done at each check.)"""
        ids = {run.run_id for run in self.runs}
        unsaid = {(str(real_path(run.flow)), run.schedule) for run in self.runs if run.run_id is None}
        listed, self.yours = _listed_spellings(self.home), set()
        for run in _running(self.home):
            if (run["id"] in ids or (run["scheduled"] and (run["flow_path"], run["schedule"]) in unsaid)
                    or not (run["scheduled"] or run_alive(run["pid"], run["began"]))):
                continue
            flow = listed.get(run["flow_path"], run["flow_path"])
            if run["scheduled"]:
                self.runs.append(_Run(flow, None, _Adopted(run["pid"], run["began"]), run["started_by"] or None,
                                      run["schedule"], run_id=run["id"]))
            else:
                self.yours.add(flow)

    def _busy(self, path: str, came: int | None = None) -> str:
        """Why a new run of this flow would go alongside another: a run of it is going (also one you started yourself,
        or one asked to stop that has not ended yet), or one that came before it is waiting its turn (`came`: the row
        of a time due again, which keeps its place); "" when none is."""
        if any(r.flow == path for r in self.runs):
            return "the previous run is still going"
        if path in self.yours:
            return "a run of it that you started is still going"
        if any(w.flow == path and (came is None or w.id < came) for w in self._line()):
            return "the previous run is still waiting its turn"
        return ""

    def _catching_up(self, schedule: tuple) -> bool:
        """A run of this schedule ((flow, block, schedule)) is going or waiting its turn."""
        return any((r.flow, r.block, r.schedule) == schedule for r in self.runs + self._line())

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
        loading = self._loading_too_long()  # (their flows' times are decided once that program has ended)
        paths = self._scheduled_flows() if self.turning_off is None else None  # (turned off: nothing new starts)
        on, schedules, unreadable, checks = set(), set(), [], []
        for path in paths or []:
            flow = self._load(path)
            if flow is None:  # (its schedules keep their state until it can be read again)
                unreadable.append(f"{path}#")
                continue
            for i, trigger in enumerate(flow.triggers):
                key = schedule_key(path, i, trigger)
                on.add(key)
                schedules.add((str(path), trigger.block, trigger.text))
                if str(path) not in loading:
                    checks.append((str(path), flow, trigger, key))
        # The times due again are decided first: the runs of their flows that came after them wait for that
        # (_may_start), and none of this check's new times starts before those that came earlier.
        checks.sort(key=lambda check: (check[0], check[2].block, check[2].text) not in self.again)
        for path, flow, trigger, key in checks:
            self._check(path, flow, trigger, key, now)
        if paths is not None:
            # A schedule that is off (its Schedule block's Active unticked, its flow not in scheduled.yaml) is
            # forgotten, and so are its runs waiting their turn (they would not begin: see claim_time): on again, it
            # starts from then as a new one does, and the times while it was off neither run nor count as missed.
            stored = {row[0] for row in self._db.execute("SELECT trigger FROM schedule_state")}
            off = [key for key in stored - on if not key.startswith(tuple(unreadable))]
            self._db.executemany("DELETE FROM schedule_state WHERE trigger=?", [(key,) for key in off])
            self._drop_waiting(lambda w: (w.flow, w.block, w.schedule) not in schedules
                               and not f"{w.flow}#".startswith(tuple(unreadable)))
        if self.turning_off is None:  # (turned off: you are asked once it is on again)
            for schedule in self._to_ask & set(self.asking):
                self._ask_about(schedule)
            self._to_ask.clear()
        # Only now, with the times due again decided, the runs waiting their turn start, in the order they came.
        self._next_in_line()
        self._write_heartbeat()

    def _loading_too_long(self) -> set[str]:
        """The flows with a run for its time that has not begun and whose program started more than ON_TIME_GRACE ago,
        by this PC's clock (as claim_time reads it): it can never begin (e.g. the PC slept while it loaded). Not begun:
        its saved row is still there, read once a claim being written has been (a run that claimed it has begun, though
        the service may not have heard so yet; a claim made after that reads a later clock, and is refused). Each such
        program is asked to stop, once (_poll forces it STOP_SECONDS later); once it has ended, its time is due again
        (_ended), and the next check decides it with the times that came meanwhile, as Misfire says."""
        clock = dt.datetime.now(UTC)
        late = [run for run in self.runs if run.waiting is not None and run.waiting.waits == "now"
                and run.run_id is None and clock - run.started > ON_TIME_GRACE]
        if late:
            with self._changes() as db:  # (BEGIN IMMEDIATE: after a claim being written)
                late = [run for run in late
                        if db.execute("SELECT 1 FROM waiting_runs WHERE id=?", (run.waiting.id,)).fetchone()]
        for run in late:
            if run.stopping is None:
                self._stop(run)
        return {run.flow for run in late}

    def _drop_waiting(self, which):
        """Drops the saved runs not going yet that `which(entry)` says (from the history database, and its lines)."""
        dropped = [w for w in self._line() if which(w)]
        for schedule in [schedule for schedule, entries in self.again.items() if entries and which(entries[0])]:
            dropped += self.again.pop(schedule)
        for schedule in [schedule for schedule, entries in self.asking.items() if entries and which(entries[0])]:
            dropped += self.asking.pop(schedule)
            self._questions.pop(schedule, None)
        if dropped:
            self.waiting = [w for w in self.waiting if not which(w)]
            self.queued = {path: [w for w in line if not which(w)] for path, line in self.queued.items()}
            self._forget(dropped)

    def _drop_gone(self):
        """Forgets the saved runs whose row is gone: the saved rows are the truth. Whoever deleted a row has dealt with
        it and told you (the editor's save of a changed schedule: forget_schedules; Stop now: request_turn_off; each
        deletes a schedule's rows together). Read before the service decides anything about its saved runs, in that
        decision's transaction when it has one, so no row deleted before a decision counts in it."""
        saved = {row[0] for row in self._db.execute("SELECT id FROM waiting_runs")}
        self._drop_waiting(lambda entry: entry.id not in saved)

    def _check(self, path: str, flow, trigger, key: str, now: dt.datetime):
        """The runs of one of the flow's schedules due since its last check, as its Misfire and the flow's Overlap say;
        and its times whose run for them did not begin (refused: again), as the missed times they may be now. The times
        it deals with are handled (schedule_state) together with what it does about them (each run saved, waiting_runs,
        to claim as it begins: claim_time; skipped; missed)."""
        with self._changes():
            self._drop_gone()
            last = self._last(key)
            if last is None:  # a new schedule starts from now; nothing is "missed"
                # Every N minutes or hours: its first run is now (to the second), the next ones N after it.
                last = now.replace(microsecond=0) - trigger.every if trigger.every else now
                self._set_last(key, last)
            schedule = (path, trigger.block, trigger.text)
            again = self.again.pop(schedule, [])
            self._forget(again)  # (each due again, with the times after it: saved anew as planned now)
            due = sorted({*trigger.due(last, now), *(entry.as_of for entry in again)})
            if not due:
                return
            missed = [t for t in due if now - t >= ON_TIME_GRACE]  # (the oldest of those due)
            if schedule in self.asking or (trigger.misfire == "catch_up" and len(missed) > ASK_OVER):
                self._backlog(schedule, missed, due[len(missed):])
            else:
                planned = self._misfire(flow, trigger, due, now)
                # A catch-up of more than one time runs one after another, including behind a run that is already
                # going. With Overlap allow, a missed time also waits for this schedule's catch-up still going:
                # catch-up runs go one at a time.
                behind = trigger.misfire == "catch_up" and (len(planned) > 1 or (
                    flow.overlap == "allow" and due[0] < now and self._catching_up(schedule)))
                came = {entry.as_of: entry.id for entry in again}  # (a time due again keeps its place: _request)
                for as_of in planned:  # (one on time starts for its time; a missed one waits its turn)
                    self._request(path, flow, as_of, trigger.block, trigger.text, behind, now - as_of < ON_TIME_GRACE,
                                  came.get(as_of))
            if due[-1] > last:  # (the newest time handled; an every-N schedule counts on from it, one of its times)
                self._set_last(key, due[-1])

    def _misfire(self, flow, trigger, due: list, now: dt.datetime) -> list:
        """The times to run of those due, as the schedule's Misfire says."""
        if trigger.misfire == "run_once":
            return due[-1:]  # one run, for the most recent
        if trigger.misfire == "catch_up":
            return due
        # skip: the newest time still runs if less than ON_TIME_GRACE late; older ones were missed.
        on_time = [t for t in due[-1:] if now - t < ON_TIME_GRACE]
        if missed := len(due) - len(on_time):
            self._tell(flow, "skipped", "Missed runs skipped",
                       f"{flow.name}: {missed} run(s) were due while the scheduler was off")
        return on_time

    def _backlog(self, schedule: tuple, missed: list, later: list):
        """More than ASK_OVER missed times of a Catch up schedule: they wait (saved) for your answer, and so do its
        times that are not missed (`later`: the on-time one and those that come while the service is on and checking):
        they run after the missed runs you choose. Times missed before you answer (the PC slept, or the service was off)
        are missed times too: you are asked again, with how many were missed in all. Other flows run as usual."""
        path, block, text = schedule
        self.asking.setdefault(schedule, []).extend(self._save(path, missed, block, text, "missed")
                                                    + self._save(path, later, block, text, "answer"))
        if missed:
            self._to_ask.add(schedule)

    def _ask_about(self, schedule: tuple):
        """Asks you about the schedule's missed runs, with how many there are now. Asked again, the question has the
        same answer function: it replaces the one shown before (see _serve_tray)."""
        if self.ask is None:  # (a server: no one to ask)
            self._answer(schedule, ASK_OVER)
            return

        def answer(limit: int | None):  # (applied at the next check)
            self._answers[schedule] = limit

        count = sum(entry.waits == "missed" for entry in self.asking[schedule])
        self.ask(self._name(schedule[0]), count, self._questions.setdefault(schedule, answer))

    def _answer(self, schedule: tuple, limit: int | None):
        """About a schedule's missed runs: None runs them all, N only the latest N; the older ones count as missed,
        and you are told how many. They run with its times that are not missed, oldest first, one at a time."""
        with self._changes() as db:
            self._drop_gone()
            backlog = self.asking.pop(schedule, [])
            self._questions.pop(schedule, None)
            missed = [entry for entry in backlog if entry.waits == "missed"]
            dropped = missed[:max(0, len(missed) - limit)] if limit is not None else []
            gone = {entry.id for entry in dropped}
            kept = [entry for entry in backlog if entry.id not in gone]
            db.executemany("DELETE FROM waiting_runs WHERE id=?", [(entry.id,) for entry in dropped])
            db.executemany("UPDATE waiting_runs SET waits='flow' WHERE id=?", [(entry.id,) for entry in kept])
        for entry in kept:
            entry.waits = "flow"
        path = schedule[0]
        self.queued[path] = sorted(self.queued.get(path, []) + kept, key=lambda entry: entry.id)
        if dropped:
            name = self._name(path)
            self._tell(self._load(Path(path)), "skipped", "Missed runs skipped", (
                f"{name}: you chose to run only the latest {limit} of {len(missed)} missed runs; the other "
                f"{len(dropped)} are not run" if self.ask else
                f"{name}: {len(missed)} runs were due while the scheduler was off, more than {ASK_OVER}: the latest "
                f"{limit} run, oldest first, and the other {len(dropped)} are missed"))

    def _scheduled_flows(self) -> list[Path] | None:
        """The flows in scheduled.yaml; None if it cannot be read. A mistake in it (e.g. edited by hand) is told once,
        until it changes, and the service goes on: an entry that is not a flow file's path is left out, and the flows
        listed right run."""
        try:
            paths, left_out = self.home.scheduled_list()
            mistake = left_out and ("Mistake in the list of scheduled flows", (
                f"{self.home.root / 'scheduled.yaml'}: not a flow file's path, so left out: "
                + ", ".join("an empty item" if entry is None else repr(entry) for entry in left_out)
                + ". The other flows listed run."))
        except (OSError, ValueError) as e:
            paths, mistake = None, (LIST_UNREADABLE,
                                    f"{e}\n(no scheduled flow starts until it is put right)")
        if mistake and mistake != self._list_error:
            self.notify(*mistake)
        self._list_error = mistake or None
        return paths

    def _request(self, path: str, flow, as_of: dt.datetime, block: str | None, schedule: str, behind: bool = False,
                 on_time: bool = False, came: int | None = None):
        """A run of the flow's schedule for the moment `as_of`, which has come. While a run of the flow goes or waits
        its turn, its Overlap says what happens: skip, it is skipped (you are told); queue, it is saved and waits for
        them; allow, it goes on as if none did (`behind`: it waits for them whatever its Overlap, as a catch-up runs one
        at a time). Then, `on_time` (not missed), it starts now for its time unless the slots are all taken
        (max_concurrent_runs) by the runs going and the runs waiting their turn before it that could start now; saved,
        to claim its time as it begins (claim_time). Else (missed, or the slots are taken) it is saved and waits its
        turn: it starts in the order it came (at once if it can), and claims its place as it begins. A run waiting its
        turn counts as going. `came`: for a time due again, the row it had: it keeps its place, before the runs that
        came after it."""
        if busy := self._busy(path, came):
            policy = "queue" if behind else flow.overlap
            if policy == "skip":
                self._tell(flow, "skipped", "Run skipped", f"{flow.name}: {busy}")
                return
            if policy == "queue":
                self._put_in_line(self._save(path, [as_of], block, schedule, "flow", came)[0])
                return
        # (for a free slot: also one that waits only for a time of its flow due again, decided later in this check)
        ahead = [w for w in self._line() if (came is None or w.id < came) and self._may_start(w, deciding=True)]
        if on_time and not self._at_limit(len(ahead)):
            self._start(self._save(path, [as_of], block, schedule, "now", came)[0])
        else:
            self._put_in_line(self._save(path, [as_of], block, schedule, "slot", came)[0])

    def _at_limit(self, ahead: int = 0) -> bool:
        """As many runs are going, with `ahead` runs to start before the next, as max_concurrent_runs allows (none,
        while the settings have a mistake: _settings)."""
        settings = self._settings()
        limit = settings and settings["max_concurrent_runs"]
        return bool(limit) and len(self.runs) + ahead >= int(limit)

    def _settings(self) -> dict | None:
        """The settings; None while settings.yaml has a mistake. The service goes on without them meanwhile (Scheduled
        runs at once is not applied, the keeper checks no server), and you are told so once, until the file changes
        (from either thread: the keeper's reads them too)."""
        try:
            settings = self.home.settings()
            if (limit := settings["max_concurrent_runs"]) and not str(limit).isdigit():
                raise ValueError(f"settings.yaml: max_concurrent_runs (Scheduled runs at once) must be a whole number, "
                                 f"got {limit!r}")
            if not isinstance(settings["keeper"] or [], list):
                raise ValueError("settings.yaml: keeper must be a list of servers, each with its connection, command "
                                 "and home (Tools → Settings writes it so)")
        except (OSError, ValueError) as e:
            with self._settings_lock:
                try:
                    told = (self.home.root / "settings.yaml").read_bytes()
                except OSError:
                    told = b""
                if told != self._settings_told:
                    self._settings_told = told
                    self.messages.put(("Mistake in the settings", f"{e}. Until it is put right, Scheduled runs at once "
                                       "is not applied, and the keeper checks no server."))
            return None
        with self._settings_lock:
            self._settings_told = None
        return settings

    def _start(self, saved: _Waiting):
        # Its time has come: it loads, then begins at once, only if it can claim its row `saved` for this service, in
        # this epoch, as its process started now (--if-scheduled: see claim_time). It records its schedule in its run
        # history (--scheduled), and goes on if the service stops: only a request on its stdin stops it.
        # --start-at with its UTC offset: one moment, also when this PC's clock shows that time twice.
        path, as_of, block, schedule = saved.flow, saved.as_of, saved.block, saved.schedule
        started = dt.datetime.now(UTC)
        command = runner_command() + ["run", path, "--json-events", "--notify", "--scheduled", schedule,
                                      "--as-of", f"{as_of.astimezone():%Y-%m-%d %H:%M:%S}",
                                      "--start-at", as_of.astimezone().isoformat(timespec="seconds"),
                                      "--if-scheduled", self.id, str(saved.id), str(_epoch(self._db)),
                                      started.isoformat()]
        if block:
            command += ["--trigger", block]
        try:
            # errors="replace": a program the run starts (Java, a console tool) may write bytes that are not text in
            # this encoding straight to the output; reading must go on, or the run hangs once the pipe is full.
            proc = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                errors="replace",
                env={**os.environ, "TASKLOOM_HOME": str(self.home.root)},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:  # the system did not start it: you are told, and the service goes on (it does not run)
            self._forget([saved])
            name = Path(path).stem
            self._tell(self._load(Path(path)), "failure", f"{name} could not start",
                       f"{name}: the system could not start its run ({e.strerror or e})")
            return
        run = _Run(path, as_of, proc, block, schedule, waiting=saved, started=started)
        run.reader = threading.Thread(target=self._read, args=(run,), daemon=True)
        run.reader.start()
        self.runs.append(run)

    def _stop(self, run: _Run):
        """Asks the run to stop, as the editor's Stop does; _poll forces it if it is still going STOP_SECONDS later."""
        run.stopping = time.monotonic()
        try:
            if run.proc.stdin is None:  # (one from before the service restarted: on Windows, this ends it at once)
                self._cancelled_first(run)
                run.proc.terminate()
            else:
                run.proc.stdin.write(json.dumps({"cancel": True}) + "\n")
                run.proc.stdin.flush()
        except OSError:  # it has just ended
            pass

    def turn_off(self, choices: dict[str, str]):
        """You turned the service off (request_turn_off, which keeps it off): nothing new starts. For each flow in
        `choices`, "stop": its runs going stop (recorded as cancelled) and its waiting runs are dropped; "finish": its
        runs going finish, and its waiting runs still run. The runs going of other flows finish too; their waiting runs
        stay saved, to start when you turn it on again. Once nothing it lets finish is going or waiting, it is off;
        turned on again before then, it is on (see _take_requests)."""
        self.turning_off = dict(choices)
        stop = {path for path, choice in choices.items() if choice == "stop"}
        for run in self.runs:
            if run.flow in stop and run.stopping is None:
                self._stop(run)
        self._drop_waiting(lambda entry: entry.flow in stop)

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
        self._adopt()
        for run in [r for r in self.runs if r.proc.poll() is not None]:
            self.runs.remove(run)
            self._ended(run)
        for run in self.runs:  # asked to stop, and still going: forced to end
            if run.stopping is not None and time.monotonic() - run.stopping > STOP_SECONDS:
                self._cancelled_first(run)
                run.proc.kill()
        while not self.messages.empty():
            self.notify(*self.messages.get())
        if (self.turning_off is not None and not self.runs and not self._finishing()
                and not any(t.is_alive() for t in self._sending)):  # (its last emails sent)
            self.off = True

    def _take_requests(self):
        """Your answers about missed runs, and whether you turned the service off (scheduler.paused): then it turns
        off, with your choices for each flow's runs going (written in it: request_turn_off; kept until you turn it on,
        for the runs that claim as they begin: claim_time), or letting them all finish if it was marked off without them
        (e.g. while it started). Turned on again before the runs you let finish are done, it is on (they go on, and new
        times start)."""
        while self._answers:
            self._answer(*self._answers.popitem())
        if not paused(self.home):
            self.turning_off = None
            return
        choices = _turn_off_choices(self.home)
        if choices != self.turning_off:
            self.turn_off(choices)

    def _ended(self, run: _Run):
        """A run's process has ended: one that died without finishing is recorded as crashed (a failed run whose error
        says so; as cancelled if you stopped it), and you are told what did not succeed (a crash also by email, as the
        flow asks: see tell_crashed). A run that began has claimed its row (see claim_time). One that did not begin, as
        it was not to (see claim_time) or as it was stopped, is still saved: a run for its time leaves that time due
        again (a missed time, if it comes to that), one that waited its turn is back in its line; unless its schedule
        is off or changed since, or you chose Stop now for its flow: then it is dropped. One that failed before it
        could claim is dropped too: it does not run again."""
        if run.reader is not None:
            run.reader.join(5)  # it has read the last lines once the process has exited
        refused = run.run_id is None and run.proc.returncode == NOT_DUE
        if run.run_id is None and run.waiting is not None:
            entry = run.waiting
            if ((refused or run.stopping is not None) and (self.turning_off or {}).get(run.flow) != "stop"
                    and self._still_waiting(entry)):
                if entry.waits == "now":
                    self.again.setdefault((entry.flow, entry.block, entry.schedule), []).append(entry)
                else:
                    self._put_in_line(entry)
            else:
                self._forget([entry])
        if refused:  # it did not begin: nothing to tell
            return
        detail = (run.errors or run.output or ["the run process stopped unexpectedly" if run.as_of is None
                                               else f"exit code {run.proc.returncode}"])[-1]
        if run.status is None and run.run_id is not None:  # it crashed (or you stopped it, and it was forced to end)
            # Whoever records it tells you: this service, or the editor (see MainWindow._show_running_flows).
            if self._mark_crashed(run) and run.stopping is None:
                self._sent(tell_crashed(self.home, run.flow, self.notify, lambda *told: self.messages.put(told),
                                        detail))
            return
        if run.status == "success" or run.stopping is not None:
            return
        name = Path(run.flow).stem
        if run.status is None:  # refused before it began: it could not email about itself
            self._tell(self._load(Path(run.flow)), "failure", f"{name} failed", detail)
        else:
            self.notify(f"{name} {run.status}", detail)

    def _cancelled_first(self, run: _Run):
        """A run you stopped, about to be ended at once, is recorded as cancelled first: whoever then finds its process
        gone (this service, or the editor) does not take it for a crash."""
        if run.status is None and run.run_id is not None:
            self._mark_crashed(run)
            run.status = "cancelled"

    def _mark_crashed(self, run: _Run) -> bool:
        """Records the run, whose process has died (or is being forced to end), as crashed; as cancelled if you
        stopped it. False: its end was recorded already."""
        history = History(self.home.history_db)
        try:
            return history.mark_crashed(run.run_id, dt.datetime.now().isoformat(timespec="milliseconds"),
                                        cancelled=run.stopping is not None)
        finally:
            history.close()

    def _next_in_line(self):
        """Runs waiting their turn start in the order they came, each once it may (_may_start) and a slot is free. Each
        stays saved until it begins (it claims its place then: see claim_time). While the list of scheduled flows
        cannot be read, none does (they would not begin)."""
        if self._list_error and self._list_error[0] == LIST_UNREADABLE:
            return
        self._drop_gone()
        for entry in self._line():
            if self._may_start(entry):
                if self._at_limit():
                    return
                (self.queued[entry.flow] if entry.waits == "flow" else self.waiting).remove(entry)
                self._start(entry)

    def _may_start(self, entry: _Waiting, deciding: bool = False) -> bool:
        """Whether the run waiting its turn would begin now (see claim_time), given a free slot: while turning off,
        only one of a flow you let finish; not before a time of its flow due again that came before it (decided first,
        at the check: _check; `deciding`, the check still deciding them: as if they did not run); and while a run of
        its flow goes (also one you started yourself), only one waiting for a slot whose flow's Overlap is allow."""
        if self.turning_off is not None:
            if self.turning_off.get(entry.flow) != "finish":
                return False
        elif not deciding and any(w.flow == entry.flow and w.id < entry.id
                                  for line in self.again.values() for w in line):
            return False
        if entry.flow not in self.yours and not any(r.flow == entry.flow for r in self.runs):
            return True
        flow = self._load(Path(entry.flow))
        return entry.waits == "slot" and flow is not None and flow.overlap == "allow"

    def _finishing(self) -> bool:
        """While turning off: runs of the flows you let finish still wait their turn."""
        return any(self.turning_off.get(entry.flow) == "finish" for entry in self._line())

    def _still_waiting(self, entry: _Waiting) -> bool:
        """The run waiting its turn is still saved, and its schedule is on as it was (its flow listed, with that
        schedule), or the list of scheduled flows cannot be read (you are told): it keeps its place until it can be,
        and the check then decides (see tick)."""
        if self._db.execute("SELECT 1 FROM waiting_runs WHERE id=?", (entry.id,)).fetchone() is None:
            return False
        if (listed := self._scheduled_flows()) is None:
            return True
        flow = self._load(Path(entry.flow))
        return (flow is not None and entry.flow in map(str, listed)
                and (entry.block, entry.schedule) in {(t.block, t.text) for t in flow.triggers})

    def _tell(self, flow, event: str, title: str, message: str):
        """A desktop notification, plus an email if the flow's notify settings ask for this event (see tell)."""
        self._sent(tell(self.home, flow.notify if flow else None, event, title, message, self.notify,
                        lambda *told: self.messages.put(told)))

    def _sent(self, sending: threading.Thread):
        """An email being sent: the service turns off once its last emails are sent."""
        self._sending = [t for t in self._sending if t.is_alive()] + [sending]

    def _write_heartbeat(self):
        (self.home.root / "scheduler.heartbeat").write_text(str(int(time.time())))

    def wait_for_runs(self, timeout: float = 120):
        """Poll until every started and queued run has finished and every email is sent (used by tests)."""
        deadline = time.monotonic() + timeout
        while (self.runs or self._line() or any(t.is_alive() for t in self._sending)) and time.monotonic() < deadline:
            time.sleep(0.2)
            self._poll()
            self._next_in_line()
        self._poll()  # what the last emails told

    def seconds_to_next_check(self, now: dt.datetime) -> float:
        """How long the loop waits after its check at `now`: until the soonest due time, so its run starts then, and at
        most TICK_SECONDS (edited flows, finished and queued runs)."""
        now = now.astimezone(UTC)
        times = [t for path in self._scheduled_flows() or [] if (flow := self._load(path))
                 for i, trigger in enumerate(flow.triggers)
                 if (t := trigger.next_after(now, self._last(schedule_key(path, i, trigger)) if trigger.every else None))]
        seconds = (min(times) - now).total_seconds() if times else TICK_SECONDS
        return max(0.0, min(TICK_SECONDS, seconds))

    # --- keeping server schedulers alive ----------------------------------------------

    def check_servers(self):
        """For each server in the keeper setting: start its scheduler if it is not running, replacing a hung one. With
        a mistake in the settings, none: you are told (see _settings), and the keeper checks again next time."""
        settings = self._settings()
        for entry in (settings and settings["keeper"]) or []:
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
    """The keeper checks the servers every KEEPER_INTERVAL_SECONDS, until the service stops; whatever goes wrong in a
    check, you are told, and it checks again next time."""
    while not stop.is_set():
        try:
            scheduler.check_servers()
        except Exception as e:  # (never the end of the keeper)
            scheduler.messages.put(("Cannot check servers", f"the keeper's check failed, and is tried again in "
                                    f"{KEEPER_INTERVAL_SECONDS // 60} minutes: {e}"))
        stop.wait(KEEPER_INTERVAL_SECONDS)


def tell(home: Home, flow_notify, event: str, title: str, message: str, notify, later) -> threading.Thread:
    """You are told: notify(title, message), a desktop message, plus an email if the flow's notify settings
    (`flow_notify`; None: the settings' own) ask for this event. The email is sent on a thread of its own, so a mail
    server slow to answer holds nothing up; a problem sending it is told after: later(title, message), from that
    thread."""
    notify(title, message)

    def send():
        try:
            mail.send_notice(home, flow_notify, event, f"[Taskloom] {title}", Html(f"<p>{html.escape(message)}</p>"))
        except Exception as e:
            later("Cannot send email", str(e))

    sending = threading.Thread(target=send, daemon=True)
    sending.start()
    return sending


def tell_crashed(home: Home, flow_path: str, notify, later,
                 detail: str = "the run process stopped unexpectedly") -> threading.Thread:
    """You are told that a scheduled run of the flow crashed (its process has gone without finishing): "<flow>
    crashed", plus an email if the flow's notify settings ask for failures (see tell). The scheduler service and the
    editor both find such runs: whichever records it as crashed first (History.mark_crashed) tells, once."""
    try:
        flow_notify = load_flow(Path(flow_path)).notify
    except FlowError:  # (unreadable now: the settings' own)
        flow_notify = None
    return tell(home, flow_notify, "failure", f"{Path(flow_path).stem} crashed", detail, notify, later)


def paused(home: Home) -> bool:
    """You turned the scheduler service off: it does not start by itself until you turn it on."""
    return (home.root / "scheduler.paused").exists()


def set_paused(home: Home, value: bool):
    """Turns the service off, as Proceed does with no runs going (request_turn_off), or on again: then a turn-off asked
    for before is void, and a run whose program was loading while it was off does not begin (a new epoch: see
    claim_time)."""
    if value:
        request_turn_off(home, {})
    elif paused(home):
        with _saved_state(home) as db:
            _new_epoch(db)
        (home.root / "scheduler.paused").unlink(missing_ok=True)


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
    """Hold an exclusive lock for the life of this process, so only one scheduler runs per home. Taking it ends the term
    of the service before (it has gone, or is ending: it let go of the lock): a run it started does not begin from now
    on, also before this process's service is made the one on (Scheduler: see claim_time); one that began before is
    in the run history, for the next service (Scheduler._adopt)."""
    with _taking_turns(home):
        handle = _lock(home.root / "scheduler.lock")
    if handle is not None:
        db = history_connect(home.history_db)
        try:
            for table in STATE_TABLES:
                db.execute(table)
            db.execute("DELETE FROM service_on")
        finally:
            db.close()
    if handle is None:
        raise RuntimeError(f"a scheduler is already running for {home.root}")
    return handle


def service_running(home: Home) -> bool:
    """Whether a scheduler service runs for this Taskloom folder: it holds the folder's lock. Told by taking that lock
    for an instant, in turn (_taking_turns): one look never takes another's instant for a service."""
    with _taking_turns(home):
        handle = _lock(home.root / "scheduler.lock")
        if handle is not None:
            _unlock(handle)
    return handle is None


@contextlib.contextmanager
def _taking_turns(home: Home):
    """Whoever takes the folder's lock (a service as it starts, a look whether one runs) takes it in turn: meanwhile it
    holds scheduler.turn, waiting for it while another holds it (an instant)."""
    home.root.mkdir(parents=True, exist_ok=True)
    turn = _lock(home.root / "scheduler.turn", wait=True)
    try:
        yield
    finally:
        _unlock(turn)


def _lock(path: Path, wait: bool = False):
    """An exclusive lock on the file `path`, held until _unlock or the process ends; None if another process holds it
    (with `wait`, it waits until it can have it)."""
    handle = open(path, "a+")
    while True:
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)  # every process must lock the same byte
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError:
            if not wait:
                handle.close()
                return None
            time.sleep(0.01)


def _unlock(handle):
    """Lets go of a lock (_lock) at once: on Windows, closing its file alone may leave it locked a while."""
    if sys.platform == "win32":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()


def schedule_key(path, i: int, trigger) -> str:
    """The row of schedule_state that holds the state of the scheduled flow `path`'s schedule flow.triggers[i]:
    changed, or turned off and on again, a schedule is a new one."""
    return f"{path}#{trigger.block or i}#{trigger.text}"


def counted_from(home: Home, path, triggers: list) -> list[dt.datetime | None]:
    """For each of the scheduled flow `path`'s schedules, where the scheduler service counts it from if it repeats
    every N minutes or hours: one of its times, the newest that ran (or was handled otherwise, e.g. missed), on this
    PC's clock (as the editor shows times). None for the others, and while the service has not seen that schedule as
    it is now."""
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
    return [_moment(row[0]).astimezone().replace(tzinfo=None) if row else None for row in rows]


def claim_time(home: Home, path: Path, block: str | None, schedule: str, when: dt.datetime, service: str, row: int,
               epoch: int, started: dt.datetime, db) -> str | None:
    """What a run the scheduler service `service` (Scheduler.id) started in the epoch `epoch` at the moment `started`
    (with its UTC offset) claims as it begins, in the transaction `db` (the run's own, which records its start:
    History.start_run): its row `row` in waiting_runs, which that service saved for the flow `path`'s Schedule block
    `block` (None: its flow-level schedule), as that schedule was then (`schedule`, e.g. "cron 0 7 * * *"), and its
    time `when` (a local time, or with its UTC offset).
    None: it is its own (the row is gone), and the run begins. Else why not, and the row stays: the service starts it
    again in its turn, or, for a run for its time, that time is due again (a missed one, if it comes to that).

    It does not begin with no service on (none holds the folder's lock: it was ended, or crashed), if the service on is
    another one (it restarted since: the one on now sees to that time itself), or if you turned the service off since
    it was started, also if you turned it on again meanwhile (SCH-8); but while the service is off, a run that waited
    its turn for a flow you chose Let it finish for begins (SCH-4). Nor does one whose schedule no longer runs as it
    was (Active unticked, changed, the flow taken off scheduled.yaml: a changed schedule asks for its times itself), or
    whose row is gone (dropped, or another run began for it: a time runs once), or a run for its time that begins more
    than ON_TIME_GRACE after `started`, by this PC's clock: its program took that long to load (e.g. the PC slept
    meanwhile). Nor one while another run of its flow goes (scheduled, or started by you), unless the flow's Overlap is
    allow (SCH-11)."""
    said = f"{when.astimezone():%Y-%m-%d %H:%M}"
    if not service_running(home):  # (whoever takes the lock makes the service before not the one on: acquire_lock)
        return SERVICE_OFF
    if db.execute("SELECT id FROM service_on").fetchall() != [(service,)]:
        return f"the scheduler service has restarted since it started this run: the service on now sees to {said}"
    saved = db.execute("SELECT flow, waits FROM waiting_runs WHERE id=?", (row,)).fetchone()
    if saved is None:
        return f"its run for {said} is not to run any more (it was dropped, or another run began for it)"
    try:
        listed = [p for p in home.scheduled_flows() if real_path(p) == path]
        flow = load_flow(path) if listed else None
    except (OSError, ValueError, FlowError) as e:
        return f"{path.name} cannot be read: {e}"
    if flow is None or (block, schedule) not in {(t.block, t.text) for t in flow.triggers}:
        return f"{path.name} is not scheduled at {said} any more"
    if paused(home):
        if saved[1] == "now" or _turn_off_choices(home).get(saved[0]) != "finish":
            return SERVICE_OFF
    elif epoch != _epoch(db):
        return f"the scheduler service was turned off after it started this run: the service sees to {said} itself"
    if saved[1] == "now" and dt.datetime.now(UTC) - started > ON_TIME_GRACE:
        return (f"its program took more than 2 minutes to load (e.g. the computer was asleep): its time, {said}, "
                "counts as missed")
    if flow.overlap != "allow" and any(run_alive(pid, began) for pid, began in db.execute(
            "SELECT pid, began FROM runs WHERE status='running' AND flow_path=?", (str(path),))):
        return f"a run of {path.name} is still going"
    db.execute("DELETE FROM waiting_runs WHERE id=?", (row,))
    return None


def _epoch(db) -> int:
    """How many times you turned the scheduler service off or on again (see STATE_TABLES)."""
    row = db.execute("SELECT epoch FROM service_epoch").fetchone()
    return row[0] if row else 0


def _new_epoch(db):
    """You turned the service off or on again: the runs started before then do not begin (claim_time)."""
    epoch = _epoch(db) + 1
    db.execute("DELETE FROM service_epoch")
    db.execute("INSERT INTO service_epoch VALUES (?)", (epoch,))


@contextlib.contextmanager
def _saved_state(home: Home):
    """The service's state in the history database (STATE_TABLES), to change in one transaction from outside the
    service (the editor, a turn-off). It raises sqlite3.Error if the history database cannot be opened or written:
    then nothing is changed."""
    home.root.mkdir(parents=True, exist_ok=True)
    db = history_connect(home.history_db)
    try:
        with db:  # (commits at the end, or rolls back)
            db.execute("BEGIN IMMEDIATE")
            for table in STATE_TABLES:  # (none yet if no service has made them)
                db.execute(table)
            yield db
    finally:
        db.close()


def _turn_off_choices(home: Home) -> dict:
    """Your choices for each flow's runs going as you turned the service off (request_turn_off, in scheduler.paused):
    {flow: "finish" | "stop"}; empty if it was marked off without them (set_paused)."""
    try:
        choices = json.loads((home.root / "scheduler.paused").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return choices if isinstance(choices, dict) else {}


def _listed_spellings(home: Home) -> dict[str, str]:
    """Each scheduled flow's path as its runs record it (real_path), with its spelling in scheduled.yaml."""
    try:
        return {str(real_path(p)): str(p) for p in home.scheduled_flows()}
    except (OSError, ValueError):  # (cannot be read now: told by the service)
        return {}


def _running(home: Home) -> list[dict]:
    """The runs that the run history says are going (see History.running): the scheduler service's and yours."""
    if not home.history_db.exists():
        return []
    history = History(home.history_db)
    try:
        return history.running()
    finally:
        history.close()


def runs_going(home: Home) -> list[tuple[str, str]]:
    """The flows with runs going that the scheduler service started, as the run history has them (their processes
    still going): (flow file, as scheduled.yaml lists it; flow name), each flow once, in the order their runs started.
    For turning the service off flow by flow (request_turn_off)."""
    listed = _listed_spellings(home)
    return list(dict.fromkeys((listed.get(run["flow_path"], run["flow_path"]), run["flow"])
                              for run in _running(home) if run["scheduled"] and run_alive(run["pid"], run["began"])))


def forget_schedules(home: Home, path, triggers: list):
    """You saved the scheduled flow `path` with these schedules on (`triggers`, its flow.triggers; none: it is taken
    off): the state of its other schedules is forgotten, with their runs waiting their turn, so a schedule turned off
    and on again, or changed, starts afresh as a new one, also while the service is off (SCH-10). A new one on the
    clock (cron, a one-off time) starts now: its times after this save are due, also those that come while the service
    is off, which are then missed ones (SCH-9); an Every N one counts from when the service first sees it (SCH-7). With
    no schedules and no run history yet, there is nothing to forget: nothing is written. It raises sqlite3.Error if the
    history database cannot be opened or written: then nothing is changed."""
    if not triggers and not home.history_db.exists():
        return
    keys, on = {schedule_key(path, i, t): t for i, t in enumerate(triggers)}, {(t.block, t.text) for t in triggers}
    saved = dt.datetime.now(UTC).isoformat()
    with _saved_state(home) as db:
        stored = {key for (key,) in db.execute("SELECT trigger FROM schedule_state").fetchall()}
        db.executemany("DELETE FROM schedule_state WHERE trigger=?", [
            (key,) for key in stored if key.startswith(f"{path}#") and key not in keys])
        db.executemany("INSERT INTO schedule_state VALUES (?, ?)", [
            (key, saved) for key, t in keys.items() if key not in stored and not t.every])
        db.executemany("DELETE FROM waiting_runs WHERE id=?", [
            (row[0],) for row in db.execute("SELECT id, block, schedule FROM waiting_runs WHERE flow=?",
                                            (str(path),)).fetchall() if (row[1], row[2]) not in on])


def request_turn_off(home: Home, choices: dict[str, str]):
    """Turns the scheduler service off (Proceed); it stays off until you turn it on. `choices`: for each flow with runs
    going (runs_going), "finish" (let its runs finish; its waiting runs still run) or "stop" (Stop now); empty when
    none go. The choices are the mark itself (scheduler.paused), written whole and first: nothing new begins from then
    (claim_time), and the service, if one runs, applies them within a second and ends once what it lets finish is done
    (see Scheduler.turn_off). Then a new epoch begins, so a run whose program is loading does not begin, also if you
    turn the service on again before it has loaded; and Stop now is applied here, whether or not a service runs: the
    flow's waiting runs are dropped, and its runs going are stopped (_stop_runs_going)."""
    home.root.mkdir(parents=True, exist_ok=True)
    _write_whole(home.root / "scheduler.paused", json.dumps(choices))
    stop = {flow for flow, choice in choices.items() if choice == "stop"}
    with _saved_state(home) as db:
        _new_epoch(db)
        db.executemany("DELETE FROM waiting_runs WHERE flow=?", [(flow,) for flow in stop])
    _stop_runs_going(home, stop)


def _stop_runs_going(home: Home, flows: set[str]):
    """The runs going that the scheduler service started of these flows (as scheduled.yaml lists them) are stopped, and
    recorded as cancelled: a run cancels on SIGTERM, and on Windows this ends it at once, before it could record that
    itself. One whose process cannot be stopped (it has just ended) is not recorded so."""
    listed = _listed_spellings(home)
    history = History(home.history_db)
    try:
        for run in history.running():
            if (run["scheduled"] and listed.get(run["flow_path"], run["flow_path"]) in flows
                    and run_alive(run["pid"], run["began"])):
                try:
                    os.kill(run["pid"], signal.SIGTERM)
                except OSError:
                    continue
                history.mark_crashed(run["id"], dt.datetime.now().isoformat(timespec="milliseconds"), cancelled=True)
    finally:
        history.close()


def serve(home: Home, headless: bool) -> int:
    """The scheduler service, until you turn it off (from its first check on, also if you did while it started) or it
    is stopped."""
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
        failed = None
        while not stop.is_set() and not scheduler.off:
            wait, failed = _check_once(scheduler, failed)
            stop.wait(wait)
        # (a run that has not begun is not one of them: it begins only if this service is on)
        notify("Scheduler stopped", "turned off by you" if scheduler.off else
               f"{sum(r.run_id is not None for r in scheduler.runs)} run(s) still going continue on their own")
        return 0


def _check_once(scheduler: Scheduler, failed: str | None) -> tuple[float, str | None]:
    """A check now; then how long until the next one, and why it failed (None: it did not). A check that fails never
    ends the service (charter C10): you are told in plain words, once until why changes (`failed`: why the check before
    it failed), and it checks again."""
    now = dt.datetime.now()
    try:
        scheduler.tick(now)
        return scheduler.seconds_to_next_check(now), None
    except Exception as e:
        why = (f"{e.strerror}: {e.filename}" if isinstance(e, OSError) and e.strerror and e.filename
               else str(e) or type(e).__name__)
        if why != failed:
            scheduler.notify("A check failed", f"the scheduler service could not finish a check ({why}); it goes on "
                                               "checking")
        return TICK_SECONDS, why


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

    from .editor.dialogs import CatchUpDialog, ask_turn_off

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
    questions = []  # the catch-up questions shown (kept: Python would free one still open)

    def ask(flow_name, count, answer):  # (it returns at once: the service goes on while the question is open)
        for question in [q for q in questions if q.answer is answer]:  # asked again, with a new count: it replaces it
            questions.remove(question)
            question.hide()
        questions.append(CatchUpDialog(flow_name, count, answer))
        questions[-1].show()
        questions[-1].activateWindow()

    scheduler = Scheduler(home, lambda title, message: tray.showMessage(title, message), ask)
    menu = QMenu()
    open_editor = QAction("Open editor", menu)
    open_editor.triggered.connect(lambda: subprocess.Popen(windowed_command() + ["editor"],
                                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))

    def turn_off():  # the editor's question, applied as the editor does at the next check (check() then ends it)
        choices = ask_turn_off(runs_going(home))
        if choices is not None:  # (not scheduler.turn_off: the menu would keep the service and its tray icon alive)
            request_turn_off(home, choices)

    quit_action = QAction("Turn off the scheduler service", menu)
    quit_action.triggered.connect(turn_off)
    menu.addAction(open_editor)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.messageClicked.connect(open_editor.trigger)
    tray.show()
    threading.Thread(target=_keeper_loop, args=(scheduler, stop), daemon=True).start()
    timer = QTimer(singleShot=True, timerType=Qt.PreciseTimer)
    failed = None

    def check():
        nonlocal failed
        wait, failed = _check_once(scheduler, failed)
        if scheduler.off:  # turned off (Scheduler.turn_off), and what it let finish is done
            app.quit()
            return
        timer.start(math.ceil(wait * 1000))

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

