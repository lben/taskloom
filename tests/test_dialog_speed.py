"""Dialogs and message boxes appear at once on Windows (specification dialog_speed): every top-level window the editor
shows (its main window, the Run now question, the Run dialog, Ask User's question box, Settings, Connections, Yes/No
boxes) has the Windows open/close animation turned off before it is first shown, through
DwmSetWindowAttribute(hwnd, DWMWA_TRANSITIONS_FORCEDISABLED = 3, TRUE); menus and tooltips are left alone; a Yes/No box
still appears within 100 ms of being asked for; if the call fails (older Windows) every window still shows, with no
error box and no crash; elsewhere (the offscreen platform, macOS, Linux) nothing is called and everything shows as
before.

Each run is the real editor (`taskloom editor <flow>`) in its own process, on Qt's real Windows platform (on Windows)
or offscreen: it runs a flow from Run now (its question, its Run dialog, then Ask User's box from the real run
process), opens Settings and Connections from the Tools menu, asks three Yes/No boxes, opens the File menu and a
tooltip, then quits. Every DwmSetWindowAttribute call the editor makes, however it finds the function through ctypes,
is recorded with the window handle, the attribute, the value, and whether that window was already visible."""

import json
import os
import subprocess
import sys

import pytest

from taskloom.editor.safety import CRASH_LOG

WINDOWS = sys.platform == "win32"
on_windows = pytest.mark.skipif(not WINDOWS, reason="the Windows window-open animation exists only on Windows")
DWMWA_TRANSITIONS_FORCEDISABLED = 3
APPEARS_WITHIN_MS = 100  # the specification's bound for a Yes/No box
TICK_MS = 16  # one Windows clock tick of tolerance
QUESTIONS = 3  # Yes/No boxes asked, one after the other ("even plain Yes/No boxes are slow, every time")

# The windows the run shows (what the script calls them: the class; for a message box its buttons), each answered
# as a person would; all of them get the animation turned off on Windows.
WINDOWS_SHOWN = ["MainWindow", "QMessageBox Cancel/Run", "RunDialog", "AskDialog", "SettingsDialog",
                 "ConnectionsDialog", *["QMessageBox No/Yes"] * QUESTIONS]

FLOW = """\
params:
  day: {type: date, expr: "today()"}
blocks:
  start: {type: trigger.manual}
  check: {type: logic.ask_user, config: {prompt: "Go on with the nightly load?", kind: yes_no}}
edges: ["start.start -> check.after"]
"""

SCRIPT = r'''
import ctypes, faulthandler, json, os, sys, time, traceback

# MODE "pass": the real DwmSetWindowAttribute; "fail": it fails (as on older Windows). QUESTIONS: Yes/No boxes to ask.
MODE, FLOW, QUESTIONS = sys.argv[1], sys.argv[2], int(sys.argv[3])
faulthandler.dump_traceback_later(75, exit=True)  # stuck: say where, and end
WINDOWS = sys.platform == "win32"
calls = []  # every DwmSetWindowAttribute call the editor makes

if WINDOWS:
    USER32 = ctypes.WinDLL("user32")
    USER32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    REAL = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
    REAL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    REAL.restype = ctypes.c_long
    E_INVALIDARG = 0x80070057 - (1 << 32)  # (an HRESULT, signed)

    def recorded(hwnd, attribute, pointer, size):
        handle = (hwnd or 0) & 0xFFFFFFFF  # (window handles are 32-bit values, also in 64-bit processes)
        readable = pointer is not None and pointer > 0xFFFF and size == 4  # (a BOOL passed by reference)
        calls.append({"hwnd": handle, "attribute": attribute, "size": size,
                      "value": ctypes.c_int.from_address(pointer).value if readable else None,
                      "visible": bool(USER32.IsWindowVisible(handle))})
        return E_INVALIDARG if MODE == "fail" else REAL(hwnd, attribute, pointer, size)

    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                  ctypes.c_uint32)(recorded)
    lookup = ctypes.CDLL.__getitem__

    def getitem(self, name):
        """However the editor finds DwmSetWindowAttribute (ctypes.windll.dwmapi, WinDLL, OleDLL), it gets a function
        that records the call (then makes it, or fails as on older Windows), with the restype and argtypes it sets."""
        if name != "DwmSetWindowAttribute":
            return lookup(self, name)
        function = self._FuncPtr(ctypes.cast(CALLBACK, ctypes.c_void_p).value)
        function.__name__ = name
        return function

    ctypes.CDLL.__getitem__ = getitem

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton, QToolTip, QWidget
from shiboken6 import isValid

from taskloom import cli
from taskloom.editor import window as editor

editor.MainWindow.start_scheduler = lambda self, by_you=False: None  # no scheduler service from a test


def kind(widget) -> str:
    return {Qt.Popup: "popup", Qt.ToolTip: "tooltip"}.get(widget.windowType(), "window")


def what(widget) -> str:
    """Its class; for a message box also its buttons as you read them (macOS shows no title on one)."""
    if isinstance(widget, QMessageBox):
        return "QMessageBox " + "/".join(sorted(b.text().replace("&", "") for b in widget.buttons()))
    return type(widget).__name__


def click(widget, text: str):
    next(b for b in widget.findChildren(QPushButton) if b.text().replace("&", "") == text).click()


ANSWERS = {  # each window as a person answers it
    "QMessageBox Cancel/Run": lambda w: click(w, "Run"),  # Run now's question
    "RunDialog": lambda w: w.accept(),  # Run, with the values computed for today
    "AskDialog": lambda w: click(w, "Yes"),
    "SettingsDialog": lambda w: w.reject(),
    "ConnectionsDialog": lambda w: w.reject(),
    "QMessageBox No/Yes": lambda w: click(w, "No"),
}


class Watcher(QObject):
    """Each top-level window the first time it is on screen (exposed, then drawn): what it is, its window handle, and
    how long after it was asked for; then it is answered as ANSWERS says. Any other dialog is noted and closed."""

    def __init__(self):
        super().__init__()
        self.shown, self.unexpected, self.notes, self.asked = [], [], [], None

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Expose and isinstance(obj, QWindow) and obj.isExposed():
            widget = QWidget.find(obj.winId())
            if widget is not None and widget.isWindow() and not widget.property("seen_by_test"):
                widget.setProperty("seen_by_test", True)
                QTimer.singleShot(0, lambda w=widget: self.on_screen(w))
        return False

    def on_screen(self, widget):
        if not isValid(widget):
            return
        now, hwnd = time.perf_counter(), int(widget.winId())
        self.shown.append({"what": what(widget), "kind": kind(widget), "hwnd": hwnd & 0xFFFFFFFF,
                           "ms": None if self.asked is None else (now - self.asked) * 1000,
                           "visible": bool(USER32.IsWindowVisible(hwnd)) if WINDOWS else None})
        self.asked = None
        widget.setProperty("seen_by_test", True)
        if what(widget) in ANSWERS:
            ANSWERS[what(widget)](widget)
        elif kind(widget) == "window" and not isinstance(widget, editor.MainWindow):
            text = widget.text() if isinstance(widget, QMessageBox) else ""
            self.unexpected.append(f"{what(widget)}: {text}")
            if isinstance(widget, QDialog):
                widget.reject()


watcher = Watcher()


def result(**more) -> str:
    return "RESULT " + json.dumps({"calls": calls, "shown": watcher.shown, "unexpected": watcher.unexpected,
                                   "notes": watcher.notes, **more})


def wait_until(check, what: str, seconds: float = 45):
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise TimeoutError(f"waited {seconds} s for {what}")
        QTest.qWait(10)


def menu(window, title: str):
    return next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == title)


def item(window, title: str, text: str):
    return next(a for a in menu(window, title).actions() if a.text() == text)


def shows(check, what: str):
    """A menu or tooltip that does not show is noted, not an error: only the test about them needs them."""
    try:
        wait_until(check, what, 10)
    except TimeoutError as e:
        watcher.notes.append(str(e))


def tooltips() -> list:
    """The tooltip's own window (not a menu's animation, which is a tooltip-type window too)."""
    return [w for w in QApplication.topLevelWidgets()
            if isinstance(w, QLabel) and w.windowType() == Qt.ToolTip and w.isVisible()]


def steps():
    try:
        window = next(w for w in QApplication.topLevelWidgets() if isinstance(w, editor.MainWindow))
        wait_until(lambda: window.windowHandle() is not None and window.windowHandle().isExposed(), "the editor")
        if not window.property("seen_by_test"):  # (on screen before the watcher was there)
            watcher.on_screen(window)
        QApplication.processEvents()
        tab = window.current
        item(window, "Run", "▶ Run now").trigger()  # its question, the Run dialog, then the run asks
        wait_until(lambda: any(e["what"] == "AskDialog" for e in watcher.shown) and not tab.running,
                   "the run to ask its question and end")
        item(window, "Tools", "Settings…").trigger()
        item(window, "Tools", "Connections…").trigger()
        for _ in range(QUESTIONS):
            watcher.asked = time.perf_counter()
            QMessageBox.question(window, "Continue?", "Go on with the nightly load?")
        files = menu(window, "File")
        files.popup(window.mapToGlobal(QPoint(40, 40)))
        shows(lambda: files.property("seen_by_test"), "the File menu")
        files.hide()
        QToolTip.showText(window.mapToGlobal(QPoint(60, 60)), "nightly", window)
        shows(lambda: any(w.property("seen_by_test") for w in tooltips()), "the tooltip")
        QToolTip.hideText()
        QApplication.processEvents()
        window.close()
    except Exception:
        print(result(error=traceback.format_exc()), flush=True)
        os._exit(4)


original_exec = QApplication.exec


def hooked_exec(*_):  # the editor's window is made and shown: watch, and go through the steps
    QApplication.instance().installEventFilter(watcher)
    QTimer.singleShot(0, steps)
    return original_exec()


QApplication.exec = hooked_exec
app = QApplication(sys.argv[:1])  # (the editor reuses it): Qt's platform has started
print("started", flush=True)
code = cli.main(["editor", FLOW])
print(result(code=code), flush=True)
if sys.platform == "win32":  # as taskloom's own entry point ends: os._exit with Qt's windows alive crashes there
    sys.exit(code)
os._exit(code)  # (the editor ships for Windows only; elsewhere Python's own shutdown after Qt can crash, on Linux)
'''


def run_to_files(command: list, folder, environment: dict, timeout: float) -> subprocess.CompletedProcess:
    """Run `command` with its output in files of `folder`, not pipes: a process the editor starts (a run) may hold a
    pipe open after the editor has ended. Fails, with what it printed, if it has not ended within `timeout` s."""
    out, err = folder / "stdout.txt", folder / "stderr.txt"
    with open(out, "w", encoding="utf-8") as stdout, open(err, "w", encoding="utf-8") as stderr:
        process = subprocess.Popen(command, env={**environment, "PYTHONIOENCODING": "utf-8"}, cwd=folder,
                                   stdout=stdout, stderr=stderr)
        try:
            process.wait(timeout=timeout)
            ended = True
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            ended = False
    printed = [path.read_text(encoding="utf-8", errors="replace") for path in (out, err)]
    assert ended, f"it had not ended after {timeout} s; stdout:\n{printed[0][-4000:]}\nstderr:\n{printed[1][-4000:]}"
    return subprocess.CompletedProcess(command, process.returncode, *printed)


def run_editor(folder, platform: str, mode: str = "pass") -> dict:
    """The editor through the steps above, on Qt's `platform`, DwmSetWindowAttribute working (mode "pass") or failing
    (mode "fail"): what it recorded. Skipped only if Qt's Windows platform cannot start here (no QApplication)."""
    folder.mkdir(parents=True, exist_ok=True)
    script, flow, home = folder / "editor_steps.py", folder / "nightly.yaml", folder / "home"
    script.write_text(SCRIPT, encoding="utf-8")
    flow.write_text(FLOW, encoding="utf-8")
    environment = {**os.environ, "QT_QPA_PLATFORM": platform, "TASKLOOM_HOME": str(home)}
    done = run_to_files([sys.executable, str(script), mode, str(flow), str(QUESTIONS)], folder, environment, 120)
    if platform == "windows" and "started" not in done.stdout.splitlines():
        pytest.skip(f"Qt's Windows platform could not start on this machine: {done.stderr[-800:]}")
    said = f"stdout:\n{done.stdout[-4000:]}\nstderr:\n{done.stderr[-4000:]}"
    assert done.returncode == 0, said  # no crash
    assert "Traceback" not in done.stderr, said
    [line] = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
    recorded = json.loads(line[len("RESULT "):])
    crash = home / CRASH_LOG
    recorded["crash_log"] = crash.read_text(encoding="utf-8", errors="replace") if crash.exists() else ""
    return recorded


def windows_shown(recorded) -> list[dict]:
    return [e for e in recorded["shown"] if e["kind"] == "window"]


def turned_off_before_shown(recorded, hwnd: int) -> bool:
    """DwmSetWindowAttribute(hwnd, DWMWA_TRANSITIONS_FORCEDISABLED, &TRUE, sizeof(BOOL)) was called while the window
    was not yet visible."""
    return any(c["hwnd"] == hwnd and c["attribute"] == DWMWA_TRANSITIONS_FORCEDISABLED and c["size"] == 4
               and c["value"] == 1 and not c["visible"] for c in recorded["calls"])


def assert_every_window_shows_normally(recorded):
    """Each window of the steps appeared (and was answered), nothing else did, and no error was caught."""
    assert recorded.get("error") is None, recorded["error"]
    assert recorded["unexpected"] == [], recorded["unexpected"]
    assert sorted(e["what"] for e in windows_shown(recorded)) == sorted(WINDOWS_SHOWN), recorded["shown"]
    assert "uncaught error" not in recorded["crash_log"], recorded["crash_log"]


def popups_shown(recorded) -> list[dict]:
    """The File menu and the tooltip, as they showed (both must have)."""
    popups = [e for e in recorded["shown"] if e["kind"] in ("popup", "tooltip")]
    assert {e["kind"] for e in popups} == {"popup", "tooltip"}, (recorded["notes"], recorded["shown"])
    return popups


@pytest.fixture(scope="module")
def on_windows_platform(tmp_path_factory):
    """One run of the editor on Qt's real Windows platform, with the real DwmSetWindowAttribute."""
    return run_editor(tmp_path_factory.mktemp("windows"), "windows")


@on_windows
def test_on_windows_every_window_the_editor_shows_has_its_animation_turned_off_before_it_shows(on_windows_platform):
    """Spec 1 and 4 (the user's report: dialogs, even plain Yes/No boxes, are slow to show on Windows 11): on Qt's
    real Windows platform, the main window, the Run now question, the Run dialog, Ask User's question box (from a
    real run), Settings, Connections and each of three Yes/No boxes get DwmSetWindowAttribute(their window handle,
    DWMWA_TRANSITIONS_FORCEDISABLED = 3, TRUE) before they are first visible; each still shows normally."""
    recorded = on_windows_platform
    assert_every_window_shows_normally(recorded)

    missing = [e["what"] for e in windows_shown(recorded) if not turned_off_before_shown(recorded, e["hwnd"])]

    assert missing == [], (f"no DwmSetWindowAttribute(hwnd, {DWMWA_TRANSITIONS_FORCEDISABLED}, TRUE) before it "
                           f"showed: {missing}; calls made: {recorded['calls']}")


@on_windows
def test_on_windows_menus_and_tooltips_are_left_alone(on_windows_platform):
    """Spec 1, last sentence: menus, tooltips and popups are not top-level windows in this sense: the File menu and a
    tooltip shown in the editor get no DWMWA_TRANSITIONS_FORCEDISABLED call."""
    recorded = on_windows_platform
    popups = popups_shown(recorded)

    touched = [e for e in popups if any(c["hwnd"] == e["hwnd"] and c["attribute"] == DWMWA_TRANSITIONS_FORCEDISABLED
                                        for c in recorded["calls"])]

    assert touched == [], f"menus and tooltips are left alone: {touched}"


@on_windows
def test_on_windows_a_yes_no_box_appears_within_100_ms_of_being_asked_for(on_windows_platform):
    """Spec 4, the measurement: on Qt's real Windows platform, in the editor, each of three Yes/No boxes asked one
    after the other is on screen (exposed and drawn) within 100 ms (one 16 ms clock tick of tolerance) of being asked
    for."""
    boxes = [e for e in on_windows_platform["shown"] if e["what"] == "QMessageBox No/Yes"]
    assert len(boxes) == QUESTIONS and all(e["ms"] is not None for e in boxes), on_windows_platform["shown"]

    slow = [round(e["ms"], 1) for e in boxes if e["ms"] >= APPEARS_WITHIN_MS + TICK_MS]

    assert slow == [], f"Yes/No boxes shown after (ms): {[round(e['ms'], 1) for e in boxes]}"


@on_windows
def test_on_windows_when_the_call_fails_every_window_still_shows_without_an_error(tmp_path):
    """Spec 3: if DwmSetWindowAttribute fails (as on older Windows: it returns E_INVALIDARG, or raises OSError where
    the editor reads its result as an HRESULT), the editor still tried it for its windows, and every window still
    shows normally: no error box, no other dialog, no crash, no uncaught error (stderr, crash.log)."""
    recorded = run_editor(tmp_path, "windows", mode="fail")

    assert_every_window_shows_normally(recorded)
    tried = [e["what"] for e in windows_shown(recorded)
             if any(c["hwnd"] == e["hwnd"] and c["attribute"] == DWMWA_TRANSITIONS_FORCEDISABLED
                    for c in recorded["calls"])]
    assert tried, f"no DwmSetWindowAttribute call was made, so none could fail: {recorded['calls']}"


def test_elsewhere_nothing_is_called_and_every_window_still_shows(tmp_path):
    """Spec 2: on the offscreen platform (the tests' own, on Windows too) and on macOS and Linux, the editor makes no
    DwmSetWindowAttribute call, and every window (the main window, Run now's question, the Run dialog, Ask User's
    box, Settings, Connections, Yes/No boxes, a menu, a tooltip) shows and answers as before: no error, no crash."""
    recorded = run_editor(tmp_path, "offscreen")

    assert_every_window_shows_normally(recorded)
    popups_shown(recorded)
    assert recorded["calls"] == [], recorded["calls"]
