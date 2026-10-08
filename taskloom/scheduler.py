"""The scheduler: runs scheduled flows, and keeps the scheduler on servers alive.

`taskloom scheduler` shows a tray icon with notifications on a desktop;
`taskloom scheduler --headless` is the same loop without a window, for servers.
Each run is a separate `taskloom run` process, like runs from the editor.
"""

from __future__ import annotations

import datetime as dt
import html
import json
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

from . import mail, ssh
from .config import Home
from .flow import FlowError, load_flow
from .history import History
from .launch import runner_command
from .report import Html

TICK_SECONDS = 15
ON_TIME_GRACE = dt.timedelta(minutes=2)  # later than this, a run counts as missed
HEARTBEAT_STALE_SECONDS = 120
KEEPER_INTERVAL_SECONDS = 300
CATCH_UP_LIMIT = 100


@dataclass
class _Run:
    flow: str
    as_of: dt.datetime
    proc: subprocess.Popen
    run_id: int | None = None
    status: str | None = None
    errors: list = field(default_factory=list)
    reader: threading.Thread | None = None


class Scheduler:
    def __init__(self, home: Home, notify=None):
        self.home = home
        self.notify = notify or (lambda title, message: None)
        self.runs: list[_Run] = []
        self.queued: dict[str, list[dt.datetime]] = {}  # per flow, waiting for its previous run
        self.waiting: list[tuple[str, dt.datetime]] = []  # waiting for a free slot (max_concurrent_runs)
        self.messages: queue.Queue = queue.Queue()  # from the keeper thread
        self._flows: dict[str, tuple[float, object]] = {}
        home.root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(home.history_db, isolation_level=None)
        self._db.execute("CREATE TABLE IF NOT EXISTS schedule_state (trigger TEXT PRIMARY KEY, last TEXT NOT NULL)")

    # --- state ------------------------------------------------------------------

    def _last(self, key: str) -> dt.datetime | None:
        row = self._db.execute("SELECT last FROM schedule_state WHERE trigger=?", (key,)).fetchone()
        return dt.datetime.fromisoformat(row[0]) if row else None

    def _set_last(self, key: str, when: dt.datetime):
        self._db.execute("INSERT INTO schedule_state VALUES (?, ?) ON CONFLICT (trigger) DO UPDATE SET last=excluded.last",
                         (key, when.isoformat()))

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
            self.notify("Scheduled flow cannot be read", f"{path.name}: {e.errors[0]}")
        self._flows[str(path)] = (mtime, flow)
        return flow

    # --- the loop -----------------------------------------------------------------

    def tick(self, now: dt.datetime | None = None):
        now = now or dt.datetime.now()
        self._poll()
        while not self.messages.empty():
            self.notify(*self.messages.get())
        for path in self.home.scheduled_flows():
            flow = self._load(path)
            if flow is None:
                continue
            for i, trigger in enumerate(flow.triggers):
                key = f"{path}#{i}#{trigger.text}"
                last = self._last(key)
                if last is None:  # a new schedule starts from now; nothing is "missed"
                    self._set_last(key, now)
                    continue
                due = trigger.due(last, now, limit=CATCH_UP_LIMIT)
                if not due:
                    continue
                self._set_last(key, now)
                missed = [t for t in due if now - t > ON_TIME_GRACE]
                to_run = [t for t in due if now - t <= ON_TIME_GRACE]
                if missed and trigger.misfire == "run_once":
                    to_run.insert(0, missed[-1])
                elif missed and trigger.misfire == "catch_up":
                    to_run = missed + to_run
                elif missed:
                    self._tell(flow, "skipped", "Missed runs skipped",
                               f"{flow.name}: {len(missed)} run(s) were due while the scheduler was off")
                for n, as_of in enumerate(to_run):
                    self._request(str(path), flow, as_of, force_queue=n > 0)
        self._write_heartbeat()

    def _request(self, path: str, flow, as_of: dt.datetime, force_queue: bool = False):
        if any(r.flow == path for r in self.runs) or self.queued.get(path):
            policy = "queue" if force_queue else flow.overlap
            if policy == "skip":
                self._tell(flow, "skipped", "Run skipped", f"{flow.name}: the previous run is still going")
                return
            if policy == "queue":
                self.queued.setdefault(path, []).append(as_of)
                return
        self._start_or_wait(path, as_of)

    def _start_or_wait(self, path: str, as_of: dt.datetime):
        limit = self.home.settings()["max_concurrent_runs"]
        if limit and len(self.runs) >= int(limit):
            self.waiting.append((path, as_of))
        else:
            self._start(path, as_of)

    def _start(self, path: str, as_of: dt.datetime):
        command = runner_command() + ["run", path, "--json-events", "--notify",
                                      "--as-of", as_of.isoformat(sep=" ", timespec="minutes")]
        proc = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
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
                run.errors = (run.errors + [line.rstrip()])[-20:]
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
            if run.status != "success":
                name = Path(run.flow).stem
                detail = run.errors[-1] if run.errors else f"exit code {run.proc.returncode}"
                self.notify(f"{name} {run.status or 'failed'}", detail)
                if run.status is None:  # it crashed, so it could not email about itself
                    self._tell(self._load(Path(run.flow)), "failure", f"{name} crashed", detail, desktop=False)
            if self.queued.get(run.flow):
                self._start_or_wait(run.flow, self.queued[run.flow].pop(0))
        while self.waiting:
            limit = self.home.settings()["max_concurrent_runs"]
            if limit and len(self.runs) >= int(limit):
                break
            self._start(*self.waiting.pop(0))

    def _tell(self, flow, event: str, title: str, message: str, desktop: bool = True):
        """A desktop notification, plus an email if the flow's notify settings ask for this event."""
        if desktop:
            self.notify(title, message)
        try:
            mail.send_notice(self.home, flow.notify if flow else None, event, f"[Taskloom] {title}",
                             Html(f"<p>{html.escape(message)}</p>"))
        except Exception as e:
            self.notify("Cannot send email", str(e))

    def _write_heartbeat(self):
        (self.home.root / "scheduler.heartbeat").write_text(str(int(time.time())))

    def wait_for_runs(self, timeout: float = 120):
        """Tick until every started and queued run has finished (used by tests and on shutdown)."""
        deadline = time.monotonic() + timeout
        while (self.runs or any(self.queued.values()) or self.waiting) and time.monotonic() < deadline:
            time.sleep(0.2)
            self._poll()

    def next_runs(self) -> dict[str, dt.datetime]:
        """Next fire time per scheduled flow (for the editor)."""
        now, result = dt.datetime.now(), {}
        for path in self.home.scheduled_flows():
            flow = self._load(path)
            times = [t for t in (tr.next_after(now) for tr in (flow.triggers if flow else [])) if t]
            if times:
                result[str(path)] = min(times)
        return result

    # --- keeping server schedulers alive ----------------------------------------------

    def check_servers(self):
        """For each server in the keeper setting: start its scheduler if it is not running."""
        for entry in self.home.settings()["keeper"] or []:
            name = entry.get("connection")
            remote_home = entry.get("home", "~/.taskloom")
            try:
                client = ssh.connect(self.home.resolve_secrets(self.home.connections()[name]), self.home)
                try:
                    if self._remote_alive(client, remote_home):
                        continue
                    command = f"env TASKLOOM_HOME={ssh.remote_path(remote_home)} {entry['command']} scheduler --headless"
                    code, _, err = ssh.run(client, ssh.start_detached(
                        command, "~", f"{remote_home}/scheduler.log", f"{remote_home}/scheduler.pid"))
                    if code != 0:
                        raise RuntimeError(err.strip())
                    self.messages.put(("Server scheduler restarted", f"{name}: the scheduler was not running and was started"))
                finally:
                    client.close()
            except Exception as e:
                self.messages.put(("Cannot check server", f"{name}: {e}"))

    @staticmethod
    def _remote_alive(client, remote_home: str) -> bool:
        """Its process is running and its heartbeat is recent (a hung scheduler counts as dead)."""
        pid_file, heartbeat = (ssh.remote_path(f"{remote_home}/{name}") for name in ("scheduler.pid", "scheduler.heartbeat"))
        _, out, _ = ssh.run(client, f'kill -0 "$(cat {pid_file} 2>/dev/null)" 2>/dev/null && echo up || echo down; '
                                    f"cat {heartbeat} 2>/dev/null; echo; date +%s")
        lines = out.split()
        numbers = [int(x) for x in lines[1:] if x.isdigit()]
        return lines[:1] == ["up"] and len(numbers) == 2 and numbers[1] - numbers[0] < HEARTBEAT_STALE_SECONDS


def _keeper_loop(scheduler: Scheduler, stop: threading.Event):
    while not stop.is_set():
        scheduler.check_servers()
        stop.wait(KEEPER_INTERVAL_SECONDS)


def acquire_lock(home: Home):
    """Hold an exclusive lock for the life of this process, so only one scheduler runs per home."""
    home.root.mkdir(parents=True, exist_ok=True)
    handle = open(home.root / "scheduler.lock", "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError(f"a scheduler is already running for {home.root}") from None
    return handle


def serve(home: Home, headless: bool) -> int:
    with acquire_lock(home):  # held until the scheduler stops
        # The keeper checks this PID, however the scheduler was started.
        (home.root / "scheduler.pid").write_text(str(os.getpid()))
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
            scheduler.tick()
            stop.wait(TICK_SECONDS - time.time() % TICK_SECONDS)
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
    from PySide6.QtCore import QTimer
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
    tray.setToolTip("Taskloom scheduler")
    scheduler = Scheduler(home, lambda title, message: tray.showMessage(title, message))
    menu = QMenu()
    open_editor = QAction("Open editor", menu)
    open_editor.triggered.connect(lambda: subprocess.Popen(runner_command() + ["editor"],
                                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    quit_action = QAction("Stop scheduler", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(open_editor)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.messageClicked.connect(open_editor.trigger)
    tray.show()
    threading.Thread(target=_keeper_loop, args=(scheduler, stop), daemon=True).start()
    timer = QTimer(interval=TICK_SECONDS * 1000)
    timer.timeout.connect(scheduler.tick)
    timer.start()
    scheduler.tick()
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
    program, *args = runner_command()
    pythonw = Path(program).with_name("pythonw.exe")
    if pythonw.exists():
        program = str(pythonw)
    command = " ".join(f'"{a}"' if " " in a else a for a in [program, *args, "scheduler"])
    return f'@echo off\nset "TASKLOOM_HOME={home.root}"\nstart "" {command}\n'


def set_start_at_login(home: Home, enabled: bool) -> Path:
    path = startup_file()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(startup_script(home), encoding="utf-8", newline="\r\n")  # Windows line ends
    elif path.exists():
        path.unlink()
    return path

