"""The scheduler: runs scheduled flows, and keeps the scheduler on servers alive.

`taskloom scheduler` shows a tray icon with notifications on a desktop;
`taskloom scheduler --headless` is the same loop without a window, for servers.
Each run is a separate `taskloom run` process, like runs from the editor.
"""

from __future__ import annotations

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
from .flow import FlowError, load_flow
from .history import History
from .history import connect as history_connect
from .launch import runner_command, windowed_command
from .report import Html

TICK_SECONDS = 1  # it checks at least this often: a schedule saved (or changed) applies within a second
# A run's process starts this long before its time: loaded by then, it begins at its time.
EARLY_START = dt.timedelta(seconds=5)
NOT_DUE = 3  # the exit code of a run started early that did not begin (see why_not_due)
ON_TIME_GRACE = dt.timedelta(minutes=2)  # later than this, a run counts as missed
HEARTBEAT_STALE_SECONDS = 120
KEEPER_INTERVAL_SECONDS = 300
KEEPER_START_SECONDS = 30  # how long the keeper waits for a server scheduler it started to check
KEEPER_STOP_SECONDS = 5  # how long it waits for a hung one asked to stop, before it forces it
CATCH_UP_LIMIT = 100


@dataclass
class _Run:
    flow: str
    as_of: dt.datetime
    proc: subprocess.Popen
    run_id: int | None = None
    status: str | None = None
    errors: list = field(default_factory=list)  # what failed: "<block>: <error>"
    output: list = field(default_factory=list)  # its last lines that are not events (e.g. why it was refused)
    reader: threading.Thread | None = None


class Scheduler:
    def __init__(self, home: Home, notify=None):
        self.home = home
        self.notify = notify or (lambda title, message: None)
        self.runs: list[_Run] = []
        self.queued: dict[str, list[dt.datetime]] = {}  # per flow, waiting for its previous run
        self.waiting: list[tuple[str, dt.datetime]] = []  # waiting for a free slot (max_concurrent_runs)
        self.messages: queue.Queue = queue.Queue()  # from the keeper thread and the emails being sent
        self._flows: dict[str, tuple[float, object]] = {}
        self._sending: list[threading.Thread] = []  # emails being sent
        self._list_error: str | None = None  # why scheduled.yaml cannot be read (told once)
        home.root.mkdir(parents=True, exist_ok=True)
        self._db = history_connect(home.history_db)
        self._db.execute("CREATE TABLE IF NOT EXISTS schedule_state (trigger TEXT PRIMARY KEY, last TEXT NOT NULL)")

    # --- state ------------------------------------------------------------------

    def _last(self, key: str) -> dt.datetime | None:
        row = self._db.execute("SELECT last FROM schedule_state WHERE trigger=?", (key,)).fetchone()
        return dt.datetime.fromisoformat(row[0]) if row else None

    def _set_last(self, key: str, when: dt.datetime):
        self._db.execute("INSERT INTO schedule_state VALUES (?, ?) ON CONFLICT (trigger) DO UPDATE SET last=excluded.last",
                         (key, when.isoformat()))

    def _busy(self, path: str) -> bool:
        """A run of this flow is going, queued, or waiting for a free slot."""
        return (any(r.flow == path for r in self.runs) or bool(self.queued.get(path))
                or any(waiting == path for waiting, _ in self.waiting))

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

    # --- the loop -----------------------------------------------------------------

    def tick(self, now: dt.datetime | None = None):
        now = now or dt.datetime.now()
        self._poll()
        paths = self._scheduled_flows()
        on, unreadable = set(), []
        for path in paths or []:
            flow = self._load(path)
            if flow is None:  # (its schedules keep their state until it can be read again)
                unreadable.append(f"{path}#")
                continue
            for i, trigger in enumerate(flow.triggers):
                key = schedule_key(path, i, trigger)
                on.add(key)
                last = self._last(key)
                if last is None:  # a new schedule starts from now; nothing is "missed"
                    # Every N minutes or hours: its first run is now (to the second), the next ones N after it.
                    last = now.replace(microsecond=0) - trigger.every if trigger.every else now
                    self._set_last(key, last)
                due = trigger.due(last, now + EARLY_START)  # since the previous check, and the ones soon
                if not due:
                    continue
                # Times in the early window are the newest in this check (the run that should begin on time).
                # Misfire applies to them together with the times already due. Asking for every soon time
                # as well as the newest past time dropped the on-time run: the past one had just started,
                # and overlap skip then refused the soon one.
                past, soon = [t for t in due if t <= now], [t for t in due if t > now]
                if trigger.misfire == "run_once":
                    chosen = (past + soon)[-1:]  # one run, for the most recent time
                    to_run = [t for t in chosen if t <= now]
                    early = [t for t in chosen if t > now]
                elif trigger.misfire == "catch_up":
                    room = CATCH_UP_LIMIT
                    to_run = past[:room]
                    early = soon[: max(0, room - len(to_run))]
                else:  # skip: only the on-time run. The one we start early is that run; older times were missed.
                    if soon:
                        to_run, early, missed = [], soon[:1], len(past)
                    elif past and now - past[-1] <= ON_TIME_GRACE:
                        to_run, early, missed = past[-1:], [], len(past) - 1
                    else:
                        to_run, early, missed = [], [], len(past)
                    if missed:
                        self._tell(flow, "skipped", "Missed runs skipped",
                                   f"{flow.name}: {missed} run(s) were due while the scheduler was off")
                planned = to_run + early
                # The newest time handled: nothing comes between it and now + EARLY_START, and an every-N
                # schedule counts on from it (one of its times).
                self._set_last(key, due[-1])
                # A catch-up of more than one time runs one after another, including behind a run that is
                # already going. force_queue on every time but the first used to skip the oldest and, when
                # the soon time was requested on its own, skip the on-time run too.
                path_s = str(path)
                busy = self._busy(path_s)
                serial = trigger.misfire == "catch_up" and len(planned) > 1
                for n, as_of in enumerate(planned):
                    self._request(path_s, flow, (as_of, trigger.block),
                                  force_queue=serial and (n > 0 or busy))
        if paths is not None:
            # A schedule that is off (its Schedule block's Active unticked, its flow not in scheduled.yaml) is
            # forgotten: on again, it starts from then as a new one does, and the times while it was off neither
            # run nor count as missed.
            stored = {row[0] for row in self._db.execute("SELECT trigger FROM schedule_state")}
            off = [key for key in stored - on if not key.startswith(tuple(unreadable))]
            self._db.executemany("DELETE FROM schedule_state WHERE trigger=?", [(key,) for key in off])
        self._write_heartbeat()

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

    def _request(self, path: str, flow, when: tuple, force_queue: bool = False):
        """`when` is (due time, Schedule block or None). A run waiting for a free slot counts as going."""
        if self._busy(path):
            policy = "queue" if force_queue else flow.overlap
            if policy == "skip":
                self._tell(flow, "skipped", "Run skipped", f"{flow.name}: the previous run is still going")
                return
            if policy == "queue":
                self.queued.setdefault(path, []).append(when)
                return
        self._start_or_wait(path, when)

    def _start_or_wait(self, path: str, when: tuple):
        if self._at_limit():
            self.waiting.append((path, when))
        else:
            self._start(path, when)

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

    def _start(self, path: str, when: tuple):
        as_of, block = when
        # Started early, it loads, then waits for its time (a time that has passed: it begins at once).
        command = runner_command() + ["run", path, "--json-events", "--notify",
                                      "--as-of", as_of.isoformat(sep=" ", timespec="seconds"),
                                      "--start-at", as_of.isoformat(timespec="seconds"), "--if-scheduled"]
        if block:
            command += ["--trigger", block]
        # errors="replace": a program the run starts (Java, a console tool) may write bytes that are not text in
        # this encoding straight to the output; reading must go on, or the run hangs once the pipe is full.
        proc = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            errors="replace",
            env={**os.environ, "TASKLOOM_HOME": str(self.home.root)},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        run = _Run(path, as_of, proc)
        run.reader = threading.Thread(target=self._read, args=(run,), daemon=True)
        run.reader.start()
        self.runs.append(run)

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
        for run in [r for r in self.runs if r.proc.poll() is not None]:
            self.runs.remove(run)
            run.reader.join(5)  # it has read the last lines once the process has exited
            if run.status is None and run.run_id is not None:
                history = History(self.home.history_db)
                history.mark_crashed(run.run_id, dt.datetime.now().isoformat(timespec="milliseconds"))
                history.close()
            # (one that did not begin, as its schedule or the service was turned off before its time: nothing to tell)
            if run.status != "success" and not (run.run_id is None and run.proc.returncode == NOT_DUE):
                name = Path(run.flow).stem
                detail = (run.errors or run.output or [f"exit code {run.proc.returncode}"])[-1]
                self.notify(f"{name} {run.status or 'failed'}", detail)
                if run.status is None:  # it crashed, so it could not email about itself
                    self._tell(self._load(Path(run.flow)), "failure", f"{name} crashed", detail, desktop=False)
            if self.queued.get(run.flow):
                self._start_or_wait(run.flow, self.queued[run.flow].pop(0))
        while self.waiting and not self._at_limit():
            self._start(*self.waiting.pop(0))
        while not self.messages.empty():
            self.notify(*self.messages.get())

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
    every N minutes or hours: one of its times, the newest the service ran (or started early). None for the others,
    and while the service has not seen that schedule as it is now."""
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
    return [dt.datetime.fromisoformat(row[0]) if row else None for row in rows]


def why_not_due(home: Home, path: Path, block: str | None, when: dt.datetime) -> str | None:
    """Why a run the scheduler service started early, for the time `when` of the flow `path`'s Schedule block
    `block` (None: its flow-level schedule), does not begin at that time: the service was turned off, or that
    schedule no longer runs then (Active unticked, changed, the flow taken off scheduled.yaml). None: it begins."""
    try:
        acquire_lock(home).close()  # nobody holds it: no scheduler service runs
        return "the scheduler service is off"
    except RuntimeError:
        pass
    try:
        listed = [p for p in home.scheduled_flows() if p.resolve() == path]
        triggers = load_flow(path).triggers if listed else []
    except (OSError, ValueError, FlowError) as e:
        return f"{path.name} cannot be read: {e}"
    starts = counted_from(home, listed[0], triggers) if listed else []
    if any(t.block == block and t.next_after(when - dt.timedelta(seconds=1), start) == when
           for t, start in zip(triggers, starts)):
        return None
    return f"{path.name} is not scheduled at {when:%Y-%m-%d %H:%M} any more"


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
        while not stop.is_set():
            now = dt.datetime.now()
            scheduler.tick(now)
            stop.wait(scheduler.seconds_to_next_check(now))
        notify("Scheduler stopped", f"{len(scheduler.runs)} run(s) still going continue on their own")
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

