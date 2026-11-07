"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml): turning it off with
runs going (SCH-4), more than 100 missed Catch up times (SCH-9), daylight-saving clock changes (SCH-15), runs waiting
their turn surviving a stop (SCH-16), and the README explaining where the scheduler might surprise you (SCH-17).

Turning the service off, a crash and the server's log use the real service and real runs. Where a test counts
hundreds of runs or drives the clock across a clock change, the run processes are replaced at the process boundary:
subprocess.Popen, for `taskloom run` commands only, gives a stand-in that records the command and has finished by the
service's next check; one started early has claimed its time by then, as the real run does (FakeRuns)."""

import contextlib
import copy
import datetime as dt
import gc
import inspect
import io
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest
import shiboken6
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QPoint, QTimer  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QGroupBox, QLabel, QMessageBox, QPushButton,  # noqa: E402
                               QRadioButton, QSpinBox, QSystemTrayIcon)

from taskloom.config import Home  # noqa: E402
from taskloom.editor.safety import pid_alive  # noqa: E402
from taskloom.flow import real_path  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.scheduler import NOT_DUE, Scheduler, acquire_lock, claim_time, paused  # noqa: E402
from taskloom.scheduler import _unlock as unlock  # noqa: E402
from tests.test_editor import REAL_START_SCHEDULER  # noqa: E402
from tests.test_scheduler_start_at import start_at_clock, start_service, stop_service  # noqa: E402
from tests.test_service_off import stays_off_at_login  # noqa: E402

REAL_POPEN = subprocess.Popen
UTC = dt.timezone.utc
README = Path(__file__).resolve().parents[1] / "README.md"
TURN_OFF = "Turn off the scheduler service?"
CATCH_UP = "Catch up missed runs?"
ANSWERS = "answers a dialog"  # (the name of answer_when_shown's timers)
HOURLY = {"every": "60 minutes"}  # its first run is as soon as the service first sees it
NAMES = ("nightly_load", "weekly_report", "monthly_backup")  # (the flows of the SCH-4 tests)
WAIT_A_MINUTE = {"type": "logic.wait", "config": {"seconds": 60}}
A_FEW_SECONDS = dt.timedelta(seconds=10)  # how early a run's process may start, to be ready at its time
NO_TZ_ON_WINDOWS = pytest.mark.skipif(
    sys.platform == "win32", reason="the local time zone cannot be changed inside a running process on Windows")


# --- flows and history ----------------------------------------------------------------------------------------------

def write(path: Path, text: str) -> Path:
    """A file written as Taskloom reads it: UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def flow_yaml(*schedules: dict, work: dict | None = None, **top) -> str:
    """A flow as the editor saves it: Schedule blocks s1, s2, ... (their configs), each starting a block of its own
    (`work`; by default Python that does nothing); its as-of time is the parameter `when`. `top`: more top-level
    settings, e.g. overlap="queue"."""
    work = work or {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}}
    blocks, edges = {}, []
    for n, config in enumerate(schedules, 1):
        blocks[f"s{n}"] = {"type": "trigger.schedule", "config": copy.deepcopy(config)}
        blocks[f"work{n}"] = copy.deepcopy(work)
        edges.append(f"s{n}.start -> work{n}.after")
    return yaml.safe_dump({**top, "params": {"when": {"type": "datetime", "expr": "now()"}}, "blocks": blocks,
                           "edges": edges}, sort_keys=False)


def hold_until(name: str) -> dict:
    """Python that waits until the file <name>.go appears next to the flow."""
    code = f"import time\nfrom pathlib import Path\nwhile not Path({name + '.go'!r}).exists():\n    time.sleep(0.05)\n"
    return {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": code}}


def schedule(home: Path, folder: Path, flows: dict[str, str]) -> dict[str, Path]:
    """Save these flows ({name: text}) in `folder` and list them, in this order, in scheduled.yaml."""
    History(home / "history.db").close()  # (its tables exist before any run)
    paths = {name: write(folder / f"{name}.yaml", text).resolve() for name, text in flows.items()}
    for path in paths.values():
        Home(home).set_scheduled(path, True)
    return paths


def history(home: Path) -> list[dict]:
    """The runs in history, in the order they started."""
    with contextlib.closing(sqlite3.connect(home / "history.db", timeout=30)) as db:
        rows = db.execute("SELECT flow, status, started, finished, pid, params FROM runs ORDER BY id").fetchall()
    return [{"flow": flow, "status": status, "started": dt.datetime.fromisoformat(started),
             "finished": dt.datetime.fromisoformat(finished) if finished else None, "pid": pid,
             "when": json.loads(params).get("when")} for flow, status, started, finished, pid, params in rows]


def statuses(home: Path, flow: str) -> list[str]:
    return [r["status"] for r in history(home) if r["flow"] == flow]


def going(home: Path) -> set[str]:
    """The flows with a run going."""
    return {r["flow"] for r in history(home) if r["status"] == "running"}


def wait_until(condition, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def ended(pid: int) -> bool:
    """Whether the process has ended (one this process started is reaped first: an ended child is not left a
    zombie, which still looks alive)."""
    if sys.platform != "win32":
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, os.WNOHANG)
    return not pid_alive(pid)


def stop_the_service(home: Path):
    """Cleanup: the scheduler service of this Taskloom folder, whoever started it, is stopped."""
    try:
        pid = int((home / "scheduler.pid").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if pid != os.getpid() and not ended(pid):
        os.kill(pid, signal.SIGTERM)  # (on Windows this ends it at once)
        if not wait_until(lambda: ended(pid), 15) and sys.platform != "win32":
            os.kill(pid, signal.SIGKILL)


def end_runs(home: Path):
    """Cleanup: the run processes history says are still going are stopped, so that none outlives the test."""
    if not (home / "history.db").exists():
        return
    deadline = time.monotonic() + 20
    while True:
        alive = []
        for run in history(home):
            pid = run["pid"]
            if run["status"] != "running" or not pid or pid == os.getpid():
                continue
            if not ended(pid):
                alive.append(pid)
        if not alive:
            return
        force = time.monotonic() > deadline
        for pid in alive:
            with contextlib.suppress(OSError):
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM)
        if time.monotonic() > deadline + 5:
            return
        time.sleep(0.3)


# --- Qt: dialogs, and the tray service in this process -------------------------------------------------------------

def pump(until, seconds: float) -> bool:
    """Let Qt work (the service's checks, dialogs) until `until()` is true, at most `seconds`; whether it became so."""
    deadline = time.monotonic() + seconds
    while True:
        QApplication.processEvents()
        if until():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)


def words(text: str) -> str:
    return text.replace("&", "").strip()


def row_of(dialog, radio, names) -> str | None:
    """The flow (one of `names`) whose row of the dialog holds this radio button: the one name shown in the smallest
    part of the dialog around it that shows only one, else the name shown on its line."""
    part = radio.parentWidget()
    while part is not None and part is not dialog:
        shown = [w.text() for w in part.findChildren(QLabel)]
        shown += [g.title() for g in [part, *part.findChildren(QGroupBox)] if isinstance(g, QGroupBox)]
        found = [n for n in names if n in " ".join(shown)]
        if len(found) == 1:
            return found[0]
        if found:
            break
        part = part.parentWidget()
    middle = radio.mapTo(dialog, radio.rect().center()).y()
    for label in dialog.findChildren(QLabel):
        top = label.mapTo(dialog, QPoint(0, 0)).y()
        found = [n for n in names if n in label.text()]
        if label.isVisible() and top <= middle <= top + label.height() and len(found) == 1:
            return found[0]
    return None


def answer_when_shown(title: str, names=(), choose=None, number=None, button=None, wait=0.0, observe=None,
                      seconds=8.0) -> dict:
    """Once a window titled `title` shows (with exec() or not) and has been shown for `wait` seconds: what it shows is
    kept in ["seen"] (and observe() in ["observed"]); then the radio buttons `choose` ({row: text}; a row is a flow of
    `names`, None for a choice in no flow's row) are clicked, the number box is set to `number`, and `button` is
    clicked. A window that cannot be answered so is closed, as is any dialog still open `seconds` later: a test never
    waits on a dialog forever. ["done"]: it has acted."""
    state = {"seen": None, "observed": None, "done": False, "closed": []}
    timer, shown_at = QTimer(), []
    deadline = time.monotonic() + seconds + wait

    def titled(w) -> bool:  # (on macOS, Qt gives a message box no title)
        return w.windowTitle() == title or (sys.platform == "darwin" and isinstance(w, QMessageBox)
                                            and not w.windowTitle())

    def look():
        window = next((w for w in QApplication.topLevelWidgets() if w.isVisible() and titled(w)), None)
        if window is None:
            if time.monotonic() > deadline:
                timer.stop()
                for w in QApplication.topLevelWidgets():
                    if w.isVisible() and isinstance(w, QDialog):
                        state["closed"].append(w.windowTitle())
                        w.reject()
                state["done"] = True
            return
        shown_at[:] = shown_at or [time.monotonic()]
        if time.monotonic() - shown_at[0] < wait:
            return
        timer.stop()
        rows = {}
        for radio in window.findChildren(QRadioButton):
            if radio.isVisible():
                rows.setdefault(row_of(window, radio, names), {})[words(radio.text())] = radio
        boxes = [b for b in window.findChildren(QSpinBox) if b.isVisible()]
        state["seen"] = {
            "text": " ".join(w.text() for w in window.findChildren(QLabel) if w.isVisible()),
            "buttons": [words(b.text()) for b in window.findChildren(QPushButton) if b.isVisible()],
            "choices": {row: sorted(radios) for row, radios in rows.items()},
            "checked": {row: [t for t, r in radios.items() if r.isChecked()] for row, radios in rows.items()},
            "numbers": [b.value() for b in boxes]}
        state["observed"] = observe() if observe else None
        picks = [rows.get(row, {}).get(text) for row, text in (choose or {}).items()]
        pushes = [b for b in window.findChildren(QPushButton) if b.isVisible() and words(b.text()) == button]
        if None in picks or len(pushes) != 1 or (number is not None and len(boxes) != 1):
            state["closed"].append(title)
            window.reject() if isinstance(window, QDialog) else window.close()
        else:
            for radio in picks:
                radio.click()
            if number is not None:
                boxes[0].setValue(number)
            pushes[0].click()
        state["done"] = True

    timer.setObjectName(ANSWERS)
    timer.timeout.connect(look)
    timer.start(50)
    state["timer"] = timer
    return state


def turn_off(tray):
    """Choose Turn off the scheduler service on the tray icon's menu."""
    (action,) = [a for a in tray.contextMenu().actions() if a.text() == "Turn off the scheduler service"]
    action.trigger()


def tray_service(monkeypatch, home: Path, drive) -> dict:
    """`taskloom scheduler` on a desktop (with its tray icon) for the Taskloom folder `home`, in this process:
    drive(tray, result) runs in place of the Qt event loop, from just after the service's first check, until it
    returns; the service has ended when this returns. result: "quit", time.time() when the service asked to end
    (QApplication.quit or exit; None: it did not); "told", its tray messages ("title: message")."""
    from taskloom import cli

    result, trays, show = {"quit": None, "told": []}, [], QSystemTrayIcon.show
    monkeypatch.setenv("TASKLOOM_HOME", str(home))
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))  # (none offscreen)
    monkeypatch.setattr(QSystemTrayIcon, "show", lambda self: (trays.append(self), show(self))[1])
    monkeypatch.setattr(QSystemTrayIcon, "showMessage",
                        lambda self, title, message, *rest: result["told"].append(f"{title}: {message}"))

    def asked_to_end(*_):
        result["quit"] = result["quit"] or time.time()

    monkeypatch.setattr(QCoreApplication, "quit", staticmethod(asked_to_end))
    monkeypatch.setattr(QCoreApplication, "exit", staticmethod(asked_to_end))

    def exec_():
        drive(trays[-1], result)
        return 0

    monkeypatch.setattr(QApplication, "exec", staticmethod(exec_))
    app = QApplication.instance()
    quits, timers = app.quitOnLastWindowClosed(), {id(o) for o in gc.get_objects() if isinstance(o, QTimer)}
    try:
        result["code"] = cli.main(["scheduler"])
    finally:
        app.setQuitOnLastWindowClosed(quits)
        # Its checks end with it (in a process of its own, the process ends), and nothing of it is left to find.
        for timer in [o for o in gc.get_objects() if isinstance(o, QTimer) and id(o) not in timers]:
            if shiboken6.isValid(timer) and timer.objectName() != ANSWERS:
                timer.stop()
                shiboken6.delete(timer)
        gc.collect()
    return result


# --- stand-ins for run processes -------------------------------------------------------------------------------------

FINISHED = json.dumps({"event": "run_finished", "status": "success"}) + "\n"


class Said:
    """What a stand-in run process prints, as the service reads it (line by line, on the thread that reads each run's
    output): the lines it has printed so far, then its end once the process has ended."""

    def __init__(self):
        self.lines, self.ended, self.waiting, self.read = [], False, False, False
        self.turn = threading.Condition()

    def __iter__(self):
        return self

    def __next__(self) -> str:
        with self.turn:
            while not self.lines and not self.ended:
                self.waiting = True  # (it has taken in every line before)
                self.turn.notify_all()
                self.turn.wait()
            self.waiting = False
            if self.lines:
                return self.lines.pop(0)
            self.read = True
            self.turn.notify_all()
        raise StopIteration

    def say(self, *lines: str):
        """It prints these lines; this returns once the service has taken in what they say."""
        with self.turn:
            self.lines += lines
            self.turn.notify_all()
            self.turn.wait_for(lambda: not self.lines and (self.waiting or self.read), timeout=30)

    def end(self):
        with self.turn:
            self.ended = True
            self.turn.notify_all()


@contextlib.contextmanager
def clock_shows(moment: dt.datetime):
    """Meanwhile this PC's clock, as the scheduler service's code reads it (datetime.now), shows `moment`: a stand-in
    run's clock is the test's clock."""
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.astimezone().replace(tzinfo=None)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("taskloom.scheduler.dt", types.SimpleNamespace(**{**vars(dt), "datetime": Clock}))
        yield


def claim_its_time(args: list[str], home: Path, clock: dt.datetime) -> str | None:
    """What `taskloom run FLOW --trigger BLOCK --start-at T --if-scheduled SCHEDULE SERVICE` (its command line: `args`)
    does when it reaches T, with this PC's clock showing `clock`: the claim of T the real run makes (claim_time),
    started before T as the scheduler service starts such a run. Whether a service is on is told by the Taskloom folder
    `home`, as the real run tells it. None: T is its own, and it begins; else why it does not begin."""
    def option(name: str) -> str | None:
        return args[args.index(name) + 1] if name in args else None

    # (started before its time: where claim_time is told so, it is)
    early = {"early": True} if "early" in inspect.signature(claim_time).parameters else {}
    with clock_shows(clock):
        return claim_time(Home(home), real_path(Path(option("run"))), option("--trigger"), option("--if-scheduled"),
                          dt.datetime.fromisoformat(option("--start-at")), args[args.index("--if-scheduled") + 2],
                          **early)


@contextlib.contextmanager
def service_on(home: Path):
    """The scheduler service is on: it holds the Taskloom folder's lock, as `taskloom scheduler` does. Here the
    service is the Scheduler a test checks with, in this process, which takes no lock: meanwhile it is taken for it
    (unless a service holds it already)."""
    try:
        lock = acquire_lock(Home(home))
    except RuntimeError:  # (a service holds it)
        lock = None
    try:
        yield
    finally:
        if lock is not None:
            unlock(lock)


class FakeRun:
    """A stand-in for one `taskloom run` process the scheduler starts: it records the command, and has finished by
    the service's next check (unless its flow is held). One started early, to claim its time (--if-scheduled), has
    reached that time by then too (unless it `waits` for the test to say so: reach_its_time): it claims it as the real
    run does (claim_its_time), with the service on (unless you turned it off), and begins only if it has it; else it
    prints why, records nothing and ends with the exit code NOT_DUE."""

    def __init__(self, args: list[str], runs: "FakeRuns", home: Path):
        self.args, self._runs, self.home = args, runs, home
        self.flow = Path(args[args.index("run") + 1]).stem
        self.block = args[args.index("--trigger") + 1] if "--trigger" in args else None
        self.start_at = args[args.index("--start-at") + 1]
        self.alongside = [r.flow for r in runs.started if r.returncode is None]  # the runs going as it started
        self.pid = 2 ** 30 + len(runs.started)  # (no process has it)
        self.returncode = None
        self.stdin = io.StringIO()
        self.claims = "--if-scheduled" in args
        self.waits = False  # (started early: it reaches its time only when the test says so)
        self.not_begun: str | None = None  # why it did not begin
        self.beginning = [FINISHED]  # what it prints once it has begun
        self.stdout = Said() if self.claims else io.StringIO(FINISHED)
        self.reached = not self.claims

    def reach_its_time(self) -> bool:
        """Started early, it reaches its time (its --start-at) and claims it; whether it began."""
        self.reached = True
        with service_on(self.home):
            self.not_begun = claim_its_time(self.args, self.home, instant(self.start_at)[0])
        if self.not_begun:
            self.stdout.say(f"not begun: {self.not_begun}\n")
            self.end(NOT_DUE)
            return False
        self.stdout.say(*self.beginning)
        return True

    def poll(self):
        if not self.reached and not self.waits:
            self.reach_its_time()
        if self.returncode is None and self.flow not in self._runs.holding:
            self.end(0)
        return self.returncode

    def end(self, code: int):
        if self.returncode is None:
            self.returncode = code
            if isinstance(self.stdout, Said):
                self.stdout.end()

    def wait(self, timeout=None):
        return self.poll()

    def kill(self):
        self.end(-9)

    terminate = kill

    def send_signal(self, sig):
        self.kill()


class FakeRuns:
    def __init__(self):
        self.started: list[FakeRun] = []  # in the order the scheduler started them
        self.holding: set[str] = set()  # flows whose runs keep going

    def popen(self, args, *rest, **kwargs):
        if not (isinstance(args, list) and "run" in map(str, args) and "--start-at" in map(str, args)):
            return REAL_POPEN(args, *rest, **kwargs)
        home = Path((kwargs.get("env") or os.environ)["TASKLOOM_HOME"])
        run = FakeRun([str(a) for a in args], self, home)
        self.started.append(run)
        return run

    def of(self, flow: str) -> list[FakeRun]:
        return [r for r in self.started if r.flow == flow]

    def end(self):
        """The stand-ins still going end (nothing reads them any more)."""
        for run in self.started:
            run.end(-9)


@pytest.fixture
def fake_runs(monkeypatch):
    runs = FakeRuns()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


def drain(scheduler: Scheduler, now: dt.datetime, runs: FakeRuns, checks: int = 500):
    """The service checks at `now` until nothing new starts for 3 checks in a row (stand-in runs finish by the next)."""
    idle = 0
    for _ in range(checks):
        before = len(runs.started)
        scheduler.tick(now)
        idle = idle + 1 if len(runs.started) == before else 0
        if idle == 3:
            return


def instant(text: str) -> tuple[dt.datetime, str | None]:
    """The moment a --start-at names, in UTC, and what is wrong with it: a local time without its UTC offset that the
    clock skips or shows twice names no single moment (it is read here as `taskloom run` reads it, fold=0)."""
    t = dt.datetime.fromisoformat(text)
    if t.tzinfo is not None:
        return t.astimezone(UTC), None
    first, second = (t.replace(fold=fold).timestamp() for fold in (0, 1))
    problem = None if first == second else f"--start-at {text} has no UTC offset, and the clock skips or repeats it"
    return dt.datetime.fromtimestamp(first, UTC), problem


def begins(runs: list[FakeRun]) -> list[str]:
    """When each run begins (its --start-at), as a moment in UTC; one started early that did not begin (it could not
    claim its time) is not among them."""
    return [f"{instant(r.start_at)[0]:%Y-%m-%d %H:%M:%S}" for r in runs if r.not_begun is None]


def utc(local_times: list[dt.datetime]) -> list[str]:
    return [f"{t.astimezone(UTC):%Y-%m-%d %H:%M:%S}" for t in local_times]


def hhmm(run: FakeRun) -> str:
    """The time a run begins (its --start-at) on this PC's clock, as HH:MM."""
    return f"{start_at_clock(run.start_at):%H:%M}"


def says(text: str, number: int) -> bool:
    """Whether the text says this number (not as part of a date or a time of day)."""
    return bool(re.search(rf"\b{number}\b", re.sub(r"\d{4}-\d\d-\d\d|\d\d?:\d\d(:\d\d)?", " ", text)))


# --- SCH-4: turning the service off ---------------------------------------------------------------------------------

@pytest.mark.invariant("SCH-4")
def test_the_editor_button_turns_the_service_off_flow_by_flow_and_others_keep_their_waiting_runs(win, tmp_path,
                                                                                                monkeypatch):
    """Two flows have a run going and one waiting its turn (overlap queue); a third has a run waiting for a free slot
    (max_concurrent_runs 2). Off from the editor's button: nightly_load Stop now, weekly_report Let it finish."""
    home = win.home.root
    write(home / "settings.yaml", "max_concurrent_runs: 2\n")
    schedule(home, tmp_path, {
        "nightly_load": flow_yaml(HOURLY, HOURLY, work=WAIT_A_MINUTE, overlap="queue"),
        "weekly_report": flow_yaml(HOURLY, HOURLY, work=hold_until("weekly_report"), overlap="queue"),
        "monthly_backup": flow_yaml(HOURLY)})
    asked = []  # a plain Yes/No question instead of the dialog: answered No
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[1:3]) or QMessageBox.No))
    pump(lambda: False, 0.3)  # (the editor's own start-up checks run first)
    monkeypatch.setattr(win, "start_scheduler", types.MethodType(REAL_START_SCHEDULER, win))  # the real service
    answer = {"timer": QTimer()}
    try:
        win.toggle_scheduler()  # off: the button turns it on
        assert pump(lambda: going(home) == {"nightly_load", "weekly_report"}, 30), history(home)
        pump(lambda: False, 1.5)  # (a check later: the others wait their turn)
        service = int((home / "scheduler.pid").read_text(encoding="utf-8"))

        answer = answer_when_shown(TURN_OFF, NAMES, choose={"nightly_load": "Stop now"}, button="Proceed")
        win.toggle_scheduler()  # on: the button turns it off
        pump(lambda: answer["done"], 12)
        seen = answer["seen"]
        assert seen is not None and seen["choices"] == {name: ["Let it finish", "Stop now"]
                                                        for name in ("nightly_load", "weekly_report")}, \
            f"no dialog with a row per flow with runs going; shown: {seen}; asked instead: {asked}"
        assert seen["checked"] == {"nightly_load": ["Let it finish"], "weekly_report": ["Let it finish"]}, seen
        assert {"Proceed", "Cancel"} <= set(seen["buttons"]) and answer["closed"] == [], (seen, answer["closed"])

        stopped = pump(lambda: statuses(home, "nightly_load") == ["cancelled"], 20)
        pump(lambda: False, 2)  # (time to start what it must not, or to end too soon)
        assert stopped, f"nightly_load's run was not stopped and recorded as cancelled: {history(home)}"
        assert not ended(service), "the service ended before the run it was told to let finish was done"
        assert sorted((r["flow"], r["status"]) for r in history(home)) == [
            ("nightly_load", "cancelled"), ("weekly_report", "running")], "after Proceed, something new started"

        write(tmp_path / "weekly_report.go", "go")  # weekly_report's runs can finish now
        off = pump(lambda: ended(service), 40)
        ended_at = time.time()
        runs = history(home)
        assert off, f"the service did not turn off once the runs let finish were done: {runs}"
        weekly = [r for r in runs if r["flow"] == "weekly_report"]
        assert [r["status"] for r in weekly] == ["success", "success"], f"Let it finish: its waiting run runs: {runs}"
        assert weekly[1]["started"] >= weekly[0]["finished"], weekly
        assert ended_at >= weekly[1]["finished"].timestamp(), weekly
        assert statuses(home, "nightly_load") == ["cancelled"], "Stop now: its waiting run is dropped"
        assert statuses(home, "monthly_backup") == [], "a flow not listed: its waiting run started while turning off"
        assert paused(Home(home))
        stays_off_at_login(home)

        win.toggle_scheduler()  # turned on again: the waiting run kept for monthly_backup starts
        assert pump(lambda: statuses(home, "monthly_backup") == ["success"], 30), history(home)
        pump(lambda: False, 2)
        assert sorted((r["flow"], r["status"]) for r in history(home)) == [
            ("monthly_backup", "success"), ("nightly_load", "cancelled"),
            ("weekly_report", "success"), ("weekly_report", "success")]
    finally:
        write(tmp_path / "weekly_report.go", "go")
        stop_the_service(home)
        end_runs(home)
        answer["timer"].stop()


@pytest.mark.invariant("SCH-4")
def test_the_tray_icons_turn_off_asks_flow_by_flow_cancel_keeps_it_on_proceed_follows_each_choice(qapp, env,
                                                                                                  monkeypatch):
    home = env.home
    schedule(home, env.root, {
        "nightly_load": flow_yaml(HOURLY, HOURLY, work=WAIT_A_MINUTE, overlap="queue"),
        "weekly_report": flow_yaml(HOURLY, HOURLY, work=hold_until("weekly_report"), overlap="queue")})
    seen, answers = {}, []

    def drive(tray, result):
        if not pump(lambda: going(home) == {"nightly_load", "weekly_report"}, 30):
            seen["runs did not start"] = history(home)
            return
        pump(lambda: False, 1.5)  # (a check later: their second runs wait their turn)
        answers.append(cancel := answer_when_shown(TURN_OFF, NAMES, button="Cancel"))
        turn_off(tray)
        pump(lambda: cancel["done"], 12)
        pump(lambda: result["quit"] is not None, 2)
        seen["cancel"] = cancel["seen"]
        seen["after cancel"] = {"asked to end": result["quit"] is not None, "off": paused(Home(home)),
                                "going": going(home)}
        if cancel["seen"] is None or result["quit"] is not None:
            return
        answers.append(proceed := answer_when_shown(TURN_OFF, NAMES, choose={"nightly_load": "Stop now"},
                                                    button="Proceed"))
        turn_off(tray)
        pump(lambda: proceed["done"], 12)
        seen["proceed"], seen["proceed closed"] = proceed["seen"], proceed["closed"]
        pump(lambda: statuses(home, "nightly_load") == ["cancelled"], 20)
        pump(lambda: False, 2)  # (time to start what it must not, or to end too soon)
        seen["while finishing"] = {"asked to end": result["quit"] is not None, "runs": history(home)}
        write(env.root / "weekly_report.go", "go")  # weekly_report's runs can finish now
        pump(lambda: result["quit"] is not None, 40)

    try:
        result = tray_service(monkeypatch, home, drive)
    finally:
        write(env.root / "weekly_report.go", "go")
        end_runs(home)
        for answer in answers:
            answer["timer"].stop()

    assert "runs did not start" not in seen, seen
    both = ("nightly_load", "weekly_report")
    rows = {n: ["Let it finish", "Stop now"] for n in both}
    assert seen["cancel"] is not None and seen["cancel"]["choices"] == rows, \
        f"Turn off on the tray icon showed no dialog with a row per flow with runs going: {seen}"
    assert seen["cancel"]["checked"] == {n: ["Let it finish"] for n in both}, seen["cancel"]
    assert {"Proceed", "Cancel"} <= set(seen["cancel"]["buttons"]), seen["cancel"]
    assert seen["after cancel"] == {"asked to end": False, "off": False, "going": set(both)}, "Cancel keeps it on"

    assert seen["proceed"] is not None and seen["proceed closed"] == [], seen
    finishing = seen["while finishing"]
    assert not finishing["asked to end"], "the service ended before the run it was told to let finish was done"
    assert sorted((r["flow"], r["status"]) for r in finishing["runs"]) == [
        ("nightly_load", "cancelled"), ("weekly_report", "running")], finishing["runs"]
    runs = history(home)
    weekly = [r for r in runs if r["flow"] == "weekly_report"]
    assert result["quit"] is not None, f"the service did not end once the runs let finish were done: {runs}"
    assert [r["status"] for r in weekly] == ["success", "success"], f"Let it finish: its waiting run runs: {runs}"
    assert result["quit"] >= weekly[1]["finished"].timestamp(), weekly
    assert statuses(home, "nightly_load") == ["cancelled"], "Stop now: its waiting run is dropped"
    assert paused(Home(home))
    stays_off_at_login(home)


@pytest.mark.invariant("SCH-4")
def test_the_tray_icons_turn_off_with_no_runs_going_asks_to_confirm(qapp, env, monkeypatch):
    schedule(env.home, env.root, {"yearly": flow_yaml({"cron": "0 2 1 1 *"})})  # (its time never comes in a test)
    seen, answers = {}, []

    def drive(tray, result):
        answers.append(no := answer_when_shown(TURN_OFF, button="No"))
        turn_off(tray)
        pump(lambda: no["done"], 12)
        pump(lambda: result["quit"] is not None, 1)
        seen["no"] = no["seen"]
        seen["after no"] = {"asked to end": result["quit"] is not None, "off": paused(Home(env.home))}
        if no["seen"] is None or result["quit"] is not None:
            return
        answers.append(yes := answer_when_shown(TURN_OFF, button="Yes"))
        turn_off(tray)
        pump(lambda: yes["done"] and result["quit"] is not None, 12)
        seen["yes"] = yes["seen"]

    try:
        result = tray_service(monkeypatch, env.home, drive)
    finally:
        for answer in answers:
            answer["timer"].stop()

    assert seen["no"] is not None, (f"Turn off on the tray icon did not ask to confirm (it turned the service off: "
                                    f"{seen['after no']})")
    assert seen["no"]["choices"] == {} and {"Yes", "No"} <= set(seen["no"]["buttons"]), seen["no"]
    assert seen["after no"] == {"asked to end": False, "off": False}, "No keeps it on"
    assert seen.get("yes") is not None and result["quit"] is not None, seen
    assert paused(Home(env.home))
    stays_off_at_login(env.home)


# --- SCH-9: more than 100 missed Catch up times ------------------------------------------------------------------

FIRST_SIGHT = dt.datetime(2026, 1, 5, 0, 0, 30)  # when the service first saw the "every 10 minutes" schedule
EVERY_TEN = {"cron": "*/10 * * * *", "misfire": "catch_up"}


def missed_times(count: int) -> list[dt.datetime]:
    """The first `count` times of EVERY_TEN after FIRST_SIGHT."""
    return [dt.datetime(2026, 1, 5, 0, 10) + k * dt.timedelta(minutes=10) for k in range(count)]


def check_after(count: int) -> dt.datetime:
    """A check 5 minutes after the last of those times: every one of them was missed (none is on time)."""
    return missed_times(count)[-1] + dt.timedelta(minutes=5)


def hourly_times(after: dt.datetime, until: dt.datetime, minute: int) -> list[dt.datetime]:
    t = after.replace(minute=minute, second=0, microsecond=0)
    t += dt.timedelta(hours=1) if t <= after else dt.timedelta(0)
    times = []
    while t <= until:
        times.append(t)
        t += dt.timedelta(hours=1)
    return times


def one_at_a_time(runs: list[FakeRun]) -> list[str]:
    """The runs that started while another run of their flow was going."""
    return [r.start_at for r in runs if r.flow in r.alongside]


@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("limit, ran, missed", [(None, 150, None), (30, 30, 120)],
                         ids=["Run all of them", "Run only the latest 30"])
def test_more_than_100_missed_ask_once_and_wait_for_the_answer_while_other_flows_run(env, fake_runs, limit, ran,
                                                                                     missed):
    schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN), "other": flow_yaml({"cron": "* * * * *"})})
    told, asked = [], []
    scheduler = Scheduler(Home(env.home), lambda title, message: told.append(f"{title}: {message}"),
                          ask=lambda flow, count, answer: asked.append((flow, count, answer)))
    scheduler.tick(FIRST_SIGHT)
    now = check_after(150)  # 150 times missed, each over 2 minutes ago
    drain(scheduler, now, fake_runs)
    drain(scheduler, now + dt.timedelta(minutes=1), fake_runs)  # not answered yet

    assert [(flow, count) for flow, count, _ in asked] == [("backlog", 150)], told
    assert fake_runs.of("backlog") == [], "missed runs started before the answer"
    assert begins(fake_runs.of("other")) == utc([now, now + dt.timedelta(minutes=1)]), "other flows run as usual"

    asked[0][2](limit)
    drain(scheduler, now + dt.timedelta(minutes=1), fake_runs)

    backlog = fake_runs.of("backlog")
    assert begins(backlog) == utc(missed_times(150)[-ran:]), told  # oldest first
    assert one_at_a_time(backlog) == []
    assert len(asked) == 1
    if missed:
        assert [t for t in told if "backlog" in t and says(t, missed)], f"not told that {missed} were missed: {told}"


@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("count", [100, 101])
def test_a_catch_up_asks_only_when_more_than_100_were_missed(env, fake_runs, count):
    schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN)})
    asked = []
    scheduler = Scheduler(Home(env.home), ask=lambda flow, missed, answer: asked.append((flow, missed)))
    scheduler.tick(FIRST_SIGHT)
    drain(scheduler, check_after(count), fake_runs)

    backlog = fake_runs.of("backlog")
    if count > 100:
        assert asked == [("backlog", count)] and backlog == [], begins(backlog)
    else:
        assert asked == [], "asked about 100 missed times or fewer"
        assert begins(backlog) == utc(missed_times(count)) and one_at_a_time(backlog) == []


@pytest.mark.invariant("SCH-9")
def test_with_no_screen_more_than_100_missed_run_the_latest_100_and_tell_how_many_were_missed(env, smtp, fake_runs):
    """A server's service (no `ask`): the latest 100 run at once, oldest first; the log (notify) and, as the flow asks
    for notices of skipped runs, an email say that 50 were missed."""
    write(env.home / "settings.yaml", "smtp_connection: mailer\n")
    schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN, notify={"when": ["skipped"],
                                                                           "to": ["team@example.com"]})})
    told = []
    scheduler = Scheduler(Home(env.home), lambda title, message: told.append(f"{title}: {message}"))
    scheduler.tick(FIRST_SIGHT)
    drain(scheduler, check_after(150), fake_runs)
    wait_until(lambda: smtp.messages, 10)
    time.sleep(0.5)  # (a second email would have come by now)

    backlog = fake_runs.of("backlog")
    assert begins(backlog) == utc(missed_times(150)[-100:]), "not the latest 100, oldest first"
    assert one_at_a_time(backlog) == []
    assert [t for t in told if "backlog" in t and says(t, 50)], f"the log does not say 50 were missed: {told}"
    mails = [(str(m["To"]), f"{m['Subject']} {m.get_body(('plain', 'html')).get_content()}") for m in smtp.messages]
    assert len(mails) == 1 and mails[0][0] == "team@example.com" and says(mails[0][1], 50), mails


@pytest.mark.invariant("SCH-9")
def test_the_server_service_runs_the_latest_100_at_once_and_its_log_says_how_many_were_missed(env):
    """`taskloom scheduler --headless`, off for 120 hours: an hourly Catch up schedule missed 120 times."""
    now = dt.datetime.now()
    minute = (now.minute + 30) % 60  # its times are at least 25 minutes from now: none comes during the test
    schedule(env.home, env.root, {"backlog": flow_yaml({"cron": f"{minute} * * * *", "misfire": "catch_up"})})
    seen_at = now - dt.timedelta(hours=120)
    Scheduler(Home(env.home)).tick(seen_at)  # the service first saw the schedule then, and has been off since
    times = hourly_times(seen_at, now, minute)
    missed = len(times) - 100
    log = env.root / "scheduler.log"
    with open(log, "w", encoding="utf-8") as out:
        service = REAL_POPEN([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"], env=env.env,
                             cwd=env.root, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
    try:
        wait_until(lambda: history(env.home), 30)
        wait_until(lambda: says(log.read_text(encoding="utf-8"), missed), 3)
        runs, text = history(env.home), log.read_text(encoding="utf-8")
    finally:
        stop_service(env, service)
        end_runs(env.home)

    assert [line for line in text.splitlines() if "backlog" in line and says(line, missed)], \
        f"the server's log does not say {missed} runs of backlog were missed:\n{text}"
    assert runs, f"no run began:\n{text}"
    assert f"{instant(runs[0]['when'])[0]:%Y-%m-%d %H:%M}" == utc([times[-100]])[0][:16], \
        f"the first run is for {runs[0]['when']}, not the oldest of the latest 100 ({times[-100]})"


@pytest.mark.invariant("SCH-9")
def test_the_tray_service_asks_in_a_dialog_and_runs_only_the_latest_n_chosen(qapp, env, monkeypatch, fake_runs):
    """The tray service, off for 120 hours: an hourly Catch up schedule missed 120 times. Its dialog shows while the
    service goes on (another flow runs); Run only the latest 3."""
    now = dt.datetime.now()
    minute = (now.minute + 30) % 60  # its times are at least 25 minutes from now: none comes during the test
    schedule(env.home, env.root, {"backlog": flow_yaml({"cron": f"{minute} * * * *", "misfire": "catch_up"})})
    seen_at = now - dt.timedelta(hours=120)
    Scheduler(Home(env.home)).tick(seen_at)  # the service first saw the schedule then, and has been off since
    schedule(env.home, env.root, {"other": flow_yaml(HOURLY)})  # it runs as soon as the service sees it
    times = hourly_times(seen_at, now, minute)
    # (it may show during the service's first check, before the drive: answered from then on)
    answer = answer_when_shown(CATCH_UP, choose={None: "Run only the latest"}, number=3, button="Proceed", wait=2.5,
                               observe=lambda: {r.flow for r in fake_runs.started}, seconds=10)

    def drive(tray, result):
        pump(lambda: answer["done"], 15)
        if answer["seen"] is not None:
            pump(lambda: len(fake_runs.of("backlog")) >= 3, 15)
            pump(lambda: False, 2.5)  # (a fourth would have started by now)

    try:
        result = tray_service(monkeypatch, env.home, drive)
    finally:
        answer["timer"].stop()

    seen = answer["seen"]
    assert seen is not None, f"no {CATCH_UP!r} dialog; runs of backlog started: {len(fake_runs.of('backlog'))}"
    assert "backlog" in seen["text"] and says(seen["text"], len(times)), seen["text"]
    assert {"Run all of them", "Run only the latest"} <= set(seen["choices"].get(None, [])), seen
    assert seen["numbers"] == [100] and "Proceed" in seen["buttons"], seen
    assert answer["observed"] == {"other"}, "while it was open: other flows run, the missed runs wait"
    backlog = fake_runs.of("backlog")
    assert begins(backlog) == utc(times[-3:]) and one_at_a_time(backlog) == [], begins(backlog)
    assert [t for t in result["told"] if "backlog" in t and says(t, len(times) - 3)], result["told"]


# --- SCH-15: daylight-saving clock changes ----------------------------------------------------------------------

@pytest.fixture
def berlin():
    """The local time zone is Europe/Berlin: 2026-03-29 02:00 CET becomes 03:00 CEST (02:00-03:00 is skipped), and
    2026-10-25 03:00 CEST becomes 02:00 CET (02:00-03:00 comes twice)."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


def run_through(scheduler: Scheduler, runs: FakeRuns, start: dt.datetime, end: dt.datetime, every: int) -> list:
    """The service checks from `start` to `end` (UTC) every `every` seconds of real time, given the local clock's
    time: naive, with fold=1 in the second pass of a repeated hour (PEP 495). Each run started, with the moment (UTC)
    of the check that started it."""
    started, moment = [], start
    while moment <= end:
        before = len(runs.started)
        scheduler.tick(dt.datetime.fromtimestamp(moment.timestamp()))
        started += [(run, moment) for run in runs.started[before:]]
        moment += dt.timedelta(seconds=every)
    return started


def off_time(started: list, every: int) -> list[str]:
    """Runs whose process started more than a few seconds before their time, or not at the first check at or after
    it (late, or in a burst)."""
    return [f"{run.start_at} started at {moment:%H:%M:%S} UTC" for run, moment in started
            if not -dt.timedelta(seconds=every) < instant(run.start_at)[0] - moment <= A_FEW_SECONDS]


def missed_said(told: list[str]) -> list[str]:
    return [t for t in told if re.search(r"missed|was off", t, re.IGNORECASE)]


def service_with(env, config: dict) -> tuple[Scheduler, list[str]]:
    schedule(env.home, env.root, {"clock": flow_yaml(config)})
    told = []
    return Scheduler(Home(env.home), lambda title, message: told.append(f"{title}: {message}")), told


@NO_TZ_ON_WINDOWS
@pytest.mark.invariant("SCH-15")
@pytest.mark.parametrize("start, end", [
    (dt.datetime(2026, 3, 29, 0, 41, 10, tzinfo=UTC), dt.datetime(2026, 3, 29, 1, 20, tzinfo=UTC)),
    (dt.datetime(2026, 10, 24, 23, 41, 10, tzinfo=UTC), dt.datetime(2026, 10, 25, 2, 20, tzinfo=UTC)),
], ids=["spring forward, 01:41 CET to 03:20 CEST", "fall back, 01:41 CEST to 03:20 CET"])
def test_every_5_minutes_keeps_its_rhythm_in_real_time_through_a_clock_change(env, berlin, fake_runs, start, end):
    scheduler, told = service_with(env, {"every": "5 minutes"})  # first seen at `start`
    started = run_through(scheduler, fake_runs, start, end, every=10)

    expected = []
    while (t := start + len(expected) * dt.timedelta(minutes=5)) <= end:
        expected.append(t)
    assert begins([run for run, _ in started]) == utc(expected), f"--start-at: {[r.start_at for r, _ in started]}"
    assert [p for run, _ in started if (p := instant(run.start_at)[1])] == []
    assert off_time(started, every=10) == []
    assert missed_said(told) == []


@NO_TZ_ON_WINDOWS
@pytest.mark.invariant("SCH-15")
@pytest.mark.parametrize("misfire", ["skip", "run_once", "catch_up"])
def test_a_time_the_clock_skips_runs_once_right_after_the_jump(env, berlin, fake_runs, misfire):
    scheduler, told = service_with(env, {"cron": "30 2 * * *", "misfire": misfire})  # 02:30 does not exist that day
    jump = dt.datetime(2026, 3, 29, 1, 0, tzinfo=UTC)  # 02:00 CET becomes 03:00 CEST
    started = run_through(scheduler, fake_runs, jump - dt.timedelta(minutes=10), jump + dt.timedelta(minutes=40), 5)

    assert len(started) == 1, f"{[run.start_at for run, _ in started]}; told: {told}"
    (run, moment), = started
    when, problem = instant(run.start_at)
    assert jump <= when <= jump + dt.timedelta(seconds=5), \
        f"it begins at {when:%H:%M:%S} UTC (--start-at {run.start_at}), not right after the jump at 01:00:00 UTC"
    assert problem is None and off_time(started, every=5) == []
    assert missed_said(told) == []


@NO_TZ_ON_WINDOWS
@pytest.mark.invariant("SCH-15")
@pytest.mark.parametrize("misfire", ["skip", "run_once", "catch_up"])
def test_a_time_the_clock_shows_twice_runs_once_the_first_time(env, berlin, fake_runs, misfire):
    scheduler, told = service_with(env, {"cron": "30 2 * * *", "misfire": misfire})
    started = run_through(scheduler, fake_runs, dt.datetime(2026, 10, 24, 23, 50, tzinfo=UTC),
                          dt.datetime(2026, 10, 25, 2, 10, tzinfo=UTC), every=10)  # 01:50 CEST to 03:10 CET

    assert [instant(run.start_at)[0] for run, _ in started] == [dt.datetime(2026, 10, 25, 0, 30, tzinfo=UTC)], \
        f"not once, at 02:30 CEST (00:30 UTC): {[run.start_at for run, _ in started]}"
    assert [p for run, _ in started if (p := instant(run.start_at)[1])] == []
    assert off_time(started, every=10) == []
    assert missed_said(told) == []


@NO_TZ_ON_WINDOWS
@pytest.mark.invariant("SCH-15")
def test_each_quarter_hour_of_the_repeated_hour_runs_once(env, berlin, fake_runs):
    scheduler, told = service_with(env, {"cron": "*/15 * * * *"})
    started = run_through(scheduler, fake_runs, dt.datetime(2026, 10, 24, 23, 50, tzinfo=UTC),
                          dt.datetime(2026, 10, 25, 2, 20, tzinfo=UTC), every=10)  # 01:50 CEST to 03:20 CET

    moments = [instant(run.start_at)[0] for run, _ in started]
    clock = [f"{dt.datetime.fromtimestamp(m.timestamp()):%H:%M}" for m in moments]
    assert clock == ["02:00", "02:15", "02:30", "02:45", "03:00", "03:15"], [run.start_at for run, _ in started]
    assert moments == sorted(set(moments))
    assert [p for run, _ in started if (p := instant(run.start_at)[1])] == []
    assert off_time(started, every=10) == []
    assert missed_said(told) == []


# --- SCH-16: runs waiting their turn are saved -------------------------------------------------------------------

@pytest.mark.invariant("SCH-16")
def test_runs_waiting_their_turn_start_once_each_in_order_after_the_service_stopped_without_warning(env, fake_runs):
    """At 10:05: backlog catches up 10:01 to 10:05 (10:01 going, the rest waiting); queued's s1 is going and its s2
    waits behind it (overlap queue); slot waits for a free slot (max_concurrent_runs 2). The service then stops
    without warning (a crash, a PC restart: its runs are gone too), and a new one starts on the same folder."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 2\n")
    at_10_05 = {"cron": "5 10 * * *"}
    schedule(env.home, env.root, {
        "backlog": flow_yaml({"cron": "* * * * *", "misfire": "catch_up"}),
        "queued": flow_yaml(at_10_05, at_10_05, overlap="queue"),
        "slot": flow_yaml({"cron": "* * * * *"})})
    fake_runs.holding |= {"backlog", "queued"}
    before = Scheduler(Home(env.home))
    before.tick(dt.datetime(2026, 1, 5, 10, 0))
    before.tick(dt.datetime(2026, 1, 5, 10, 5))
    first = [(r.flow, r.block, hhmm(r)) for r in fake_runs.started]
    assert first == [("backlog", "s1", "10:01"), ("queued", "s1", "10:05")], first

    fake_runs.holding.clear()  # (the runs going are gone with it)
    for run in fake_runs.started:
        run.poll()
    after = Scheduler(Home(env.home))  # the service started again (`before` is never checked again)
    drain(after, dt.datetime(2026, 1, 5, 10, 5, 20), fake_runs)

    again = fake_runs.started[len(first):]
    assert sorted((r.flow, r.block, hhmm(r)) for r in again) == [
        ("backlog", "s1", "10:02"), ("backlog", "s1", "10:03"), ("backlog", "s1", "10:04"),
        ("backlog", "s1", "10:05"), ("queued", "s2", "10:05"), ("slot", "s1", "10:05")], \
        f"the runs waiting their turn did not start, each once, once the service was back: {begins(again)}"
    backlog = [r for r in again if r.flow == "backlog"]
    assert [hhmm(r) for r in backlog] == ["10:02", "10:03", "10:04", "10:05"]  # in order
    assert one_at_a_time(backlog) == []


@pytest.mark.invariant("SCH-16")
def test_runs_waiting_their_turn_start_after_the_service_process_is_killed(env):
    """The real service, max_concurrent_runs 1: first_in_line's s1 run goes, its s2 run waits behind it (overlap
    queue), next_in_line's run waits for the slot. The service is killed (as by a crash), then started again."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    schedule(env.home, env.root, {
        "first_in_line": flow_yaml(HOURLY, HOURLY, work=hold_until("first_in_line"), overlap="queue"),
        "next_in_line": flow_yaml(HOURLY)})
    service = start_service(env, tray=False)
    try:
        assert wait_until(lambda: going(env.home) == {"first_in_line"}, 30), history(env.home)
        time.sleep(1.5)  # (a check later)
        pid = int((env.home / "scheduler.pid").read_text(encoding="utf-8"))
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))  # killed (on Windows SIGTERM ends it at once)
        service.wait(timeout=15)
        write(env.root / "first_in_line.go", "go")  # the run that was going finishes on its own
        assert wait_until(lambda: statuses(env.home, "first_in_line") == ["success"], 30), history(env.home)
        assert statuses(env.home, "next_in_line") == []

        service = start_service(env, tray=False)
        wait_until(lambda: (statuses(env.home, "first_in_line"), statuses(env.home, "next_in_line")) == (
            ["success", "success"], ["success"]), 30)
        time.sleep(2)  # (a run started twice would show by now)
        runs = history(env.home)
    finally:
        stop_service(env, service)
        write(env.root / "first_in_line.go", "go")
        end_runs(env.home)

    assert sorted((r["flow"], r["status"]) for r in runs) == [
        ("first_in_line", "success"), ("first_in_line", "success"), ("next_in_line", "success")], \
        f"the runs waiting their turn did not start, each once, after the service was back: {runs}"
    restarted = runs[1:]
    assert restarted[1]["started"] >= restarted[0]["finished"], f"two runs at once with max_concurrent_runs 1: {runs}"


# --- SCH-17: the README --------------------------------------------------------------------------------------------

def schedules_prose() -> list[str]:
    """The paragraphs of the README's Schedules section, in plain words (its examples left out), lower case."""
    text = README.read_text(encoding="utf-8")
    section = text[text.index("## Schedules"):]
    section = re.sub(r"```.*?```", "", section[:section.index("\n## ", 1)], flags=re.DOTALL)
    return [" ".join(p.split()).lower() for p in section.split("\n\n") if p.strip()]


@pytest.mark.invariant("SCH-17")
@pytest.mark.parametrize("case, patterns", [
    ("missed times and the 100 limit, also on servers", [r"missed", r"\b100\b", r"server"]),
    ("overlaps", [r"overlap", r"\bqueue", r"\bskip", r"\ballow"]),
    ("turning the service off", [r"turn", r"let it finish", r"stop now"]),
    ("saved waiting runs", [r"wait", r"saved", r"restart|crash"]),
    ("clock changes", [r"daylight|summer time", r"clock"]),
], ids=["missed and 100", "overlaps", "turning off", "saved runs", "clock changes"])
def test_the_readme_explains_where_the_scheduler_might_surprise_you(case, patterns):
    explained = [p for p in schedules_prose() if all(re.search(pattern, p) for pattern in patterns)]

    assert explained, f"no paragraph of the README's Schedules section explains {case} (looked for {patterns})"
