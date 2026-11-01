"""Proof: on Windows, the editor's dialogs and Yes/No boxes open at once, without Windows' open (and close) animation.
How proofs are written: README.md here.

The other proofs draw the editor on Qt's offscreen platform, which has no real windows, so Windows animates nothing
there: this one starts the real editor in a process of its own on Qt's real Windows platform, as
tests/test_dialog_speed.py does, and listens to what it asks Windows."""

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

from taskloom.editor.safety import CRASH_LOG
from tests.test_dialog_speed import run_to_files

WINDOWS = sys.platform == "win32"
ASKED = ("Dialog boxes take some time to show up, it feels slow, why? It can likely be optimized — and later: even "
         "plain Yes/No boxes are slow, every time (on Windows 11).")
WITHIN_MS = 100  # a Yes/No box is on screen within this long of being asked for
BOXES = 3  # Yes/No boxes asked, one after the other ("every time")
TRANSITIONS_FORCEDISABLED = 3  # Windows' setting that turns a window's open and close animation off
FLOW = """\
blocks:
  start_by_hand: {type: trigger.manual, ui: {x: 0, y: 0}}
  load_sales: {type: logic.python, config: {inputs: [], code: "output = [120, 80, 45]"}, ui: {x: 260, y: 0}}
edges: ["start_by_hand.start -> load_sales.after"]
"""
NAMES = {"MainWindow": "the editor's window", "SettingsDialog": "Settings", "QMessageBox No/Yes": "Yes/No box",
         "QMenu": "the File menu", "QToolTip": "a tooltip"}

# The editor (taskloom editor FLOW), in a process of its own: it opens Settings and cancels it, answers No to BOXES
# Yes/No boxes, opens the File menu, shows a tooltip, and quits. Each time the editor asks Windows to set a window's
# animation, that is recorded with Windows' answer; as each window is about to show (just after the editor's own
# watcher saw it), Windows is asked whether its animation is off; and each window's first moment on screen (exposed,
# then drawn) is noted, for a Yes/No box how long after it was asked for. The editor's window, and Settings and the
# first Yes/No box over it, are saved as pictures, as the editor drew them.
SCRIPT = r'''
import ctypes, faulthandler, json, os, sys, time, traceback
from pathlib import Path

FLOW, PICTURES, BOXES = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
faulthandler.dump_traceback_later(90, exit=True)  # stuck: say where, and end
WINDOWS = sys.platform == "win32"
TRANSITIONS_FORCEDISABLED = 3
requests = []  # what the editor asked Windows about a window, with Windows' answer

if WINDOWS:
    USER32 = ctypes.WinDLL("user32")
    USER32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    DWM = ctypes.WinDLL("dwmapi")
    SIGNATURE = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    SET, GET = DWM.DwmSetWindowAttribute, DWM.DwmGetWindowAttribute
    for function in (SET, GET):
        function.argtypes, function.restype = SIGNATURE, ctypes.c_long

    def asked_by_the_editor(hwnd, attribute, pointer, size):
        """The editor's request, passed on to Windows as it is, and recorded with Windows' answer."""
        value = ctypes.c_int.from_address(pointer).value if pointer and pointer > 0xFFFF and size == 4 else None
        visible = bool(USER32.IsWindowVisible(hwnd))
        answer = SET(hwnd, attribute, pointer, size)
        requests.append({"hwnd": (hwnd or 0) & 0xFFFFFFFF, "attribute": attribute, "value": value,
                         "visible": visible, "answer": answer})  # (window handles are 32-bit values)
        return answer

    RECORDING = ctypes.WINFUNCTYPE(ctypes.c_long, *SIGNATURE)(asked_by_the_editor)
    lookup = ctypes.CDLL.__getitem__

    def getitem(self, name):
        """However the editor finds DwmSetWindowAttribute (ctypes.windll.dwmapi, WinDLL), it gets the recording one."""
        if name != "DwmSetWindowAttribute":
            return lookup(self, name)
        function = self._FuncPtr(ctypes.cast(RECORDING, ctypes.c_void_p).value)
        function.__name__ = name
        return function

    ctypes.CDLL.__getitem__ = getitem
else:
    class Library:
        """No Windows library exists here: one the editor asked for, and each call to it, would be recorded."""

        def __init__(self, name, *args, **kwargs):
            self.name = name
            requests.append({"library": name})

        def __getattr__(self, function):
            def call(*args):
                requests.append({"library": self.name, "function": function})
                return 0
            return call

    ctypes.WinDLL = Library

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton, QToolTip, QWidget
from shiboken6 import isValid

from taskloom import cli
from taskloom.editor import window as editor

editor.MainWindow.start_scheduler = lambda self, by_you=False: None  # no scheduler service from a proof
PICTURE = {"SettingsDialog": "settings", "QMessageBox No/Yes": "yes-no"}


def what(widget) -> str:
    """Its class; for a message box also its buttons, as you read them; QToolTip for a tooltip's own window."""
    if isinstance(widget, QLabel) and widget.windowType() == Qt.ToolTip:
        return "QToolTip"
    if isinstance(widget, QMessageBox):
        return "QMessageBox " + "/".join(sorted(b.text().replace("&", "") for b in widget.buttons()))
    return type(widget).__name__


def kind(widget) -> str:
    return {Qt.Popup: "popup", Qt.ToolTip: "tooltip"}.get(widget.windowType(), "window")


def click(widget, text: str):
    next(b for b in widget.findChildren(QPushButton) if b.text().replace("&", "") == text).click()


def main_window():
    return next(w for w in QApplication.topLevelWidgets() if isinstance(w, editor.MainWindow))


def picture(widget, name: str) -> dict:
    """A picture of `widget` over the editor's window, where it is on the screen, as the editor drew them (Windows
    draws the title bars), and where `widget` is in it, in its pixels."""
    window = main_window()
    under = window.grab().toImage()
    ratio = under.devicePixelRatio()
    at = widget.mapToGlobal(QPoint(0, 0)) - window.mapToGlobal(QPoint(0, 0))
    area = QRect(QPoint(0, 0), window.size()).united(QRect(at, widget.size()))
    image = QImage(round(area.width() * ratio), round(area.height() * ratio), QImage.Format_ARGB32)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.white)
    painter = QPainter(image)
    painter.drawImage(-area.topLeft(), under)
    if widget is not window:
        painter.drawImage(at - area.topLeft(), widget.grab().toImage())
    painter.end()
    image.save(str(PICTURES / f"{name}.png"))
    where = QRect(at - area.topLeft(), widget.size())
    return {"file": f"{name}.png", "rect": [round(v * ratio) for v in (where.x(), where.y(), where.width(),
                                                                       where.height())]}


class Watcher(QObject):
    """Installed before the editor's own watcher, so it sees each window just after that one did."""

    def __init__(self):
        super().__init__()
        self.shown, self.unexpected, self.pictures, self.asked, self.counted = [], [], {}, None, 0

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow()
                and obj.property("entry") is None):
            self.about_to_show(obj)
        elif event.type() == QEvent.Expose and isinstance(obj, QWindow) and obj.isExposed():
            widget = QWidget.find(obj.winId())
            if widget is not None and widget.property("entry") is not None and not widget.property("on_screen"):
                widget.setProperty("on_screen", True)
                QTimer.singleShot(0, lambda w=widget: self.on_screen(w))
        return False

    def about_to_show(self, widget):
        """What the editor asked Windows about this window as it was about to show (window handles are reused, so
        only its requests since the last window showed), and what Windows says of it now."""
        hwnd = int(widget.winId())
        entry = {"what": what(widget), "kind": kind(widget), "ms": None,
                 "asked": [r for r in requests[self.counted:] if r.get("hwnd") == (hwnd & 0xFFFFFFFF)]}
        for r in entry["asked"]:
            r["for"] = len(self.shown)
        self.counted = len(requests)
        if WINDOWS:
            off = ctypes.c_int(-1)
            entry["windows_says"] = {"answer": GET(hwnd, TRANSITIONS_FORCEDISABLED, ctypes.byref(off), 4),
                                     "off": off.value, "visible": bool(USER32.IsWindowVisible(hwnd))}
        widget.setProperty("entry", len(self.shown))
        self.shown.append(entry)

    def on_screen(self, widget):
        if not isValid(widget):
            return
        now = time.perf_counter()
        entry = self.shown[widget.property("entry")]
        if isinstance(widget, QMessageBox) and self.asked is not None:
            entry["ms"], self.asked = (now - self.asked) * 1000, None
        name = PICTURE.get(entry["what"])
        if name and name not in self.pictures:
            self.pictures[name] = picture(widget, name)
        if entry["what"] == "SettingsDialog":
            widget.reject()  # Cancel
        elif entry["what"] == "QMessageBox No/Yes":
            click(widget, "No")
        elif entry["kind"] == "window" and not isinstance(widget, editor.MainWindow):
            self.unexpected.append(f"{entry['what']}: {widget.text() if isinstance(widget, QMessageBox) else ''}")
            if isinstance(widget, QDialog):
                widget.reject()


def result(**more) -> str:
    return "RESULT " + json.dumps({"requests": requests, "shown": watcher.shown, "unexpected": watcher.unexpected,
                                   "pictures": watcher.pictures, "platform": QApplication.platformName(), **more})


def wait_until(check, what: str, seconds: float = 45):
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise TimeoutError(f"waited {seconds} s for {what}")
        QTest.qWait(10)


def menu(window, title: str):
    return next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == title)


def steps():
    try:
        window = main_window()
        wait_until(lambda: window.property("on_screen"), "the editor's window")
        QTest.qWait(300)  # (laid out, the flow drawn)
        watcher.pictures["editor"] = picture(window, "editor")
        next(a for a in menu(window, "Tools").actions() if a.text() == "Settings…").trigger()
        for _ in range(BOXES):
            watcher.asked = time.perf_counter()
            QMessageBox.question(window, "Load the sales?", "Load this week's sales now?")
        files = menu(window, "File")
        files.popup(window.mapToGlobal(QPoint(40, 40)))
        wait_until(lambda: files.property("on_screen"), "the File menu", 10)
        files.hide()
        QToolTip.showText(window.mapToGlobal(QPoint(60, 60)), "Runs every night at 02:00", window)
        wait_until(lambda: any(what(w) == "QToolTip" and w.property("on_screen")
                               for w in QApplication.topLevelWidgets()), "the tooltip", 10)
        QToolTip.hideText()
        QApplication.processEvents()
        window.close()
    except Exception:
        print(result(error=traceback.format_exc()), flush=True)
        os._exit(4)


original_exec = QApplication.exec


def hooked_exec(*_):  # the editor's window is made and shown: go through the steps
    QTimer.singleShot(0, steps)
    return original_exec()


QApplication.exec = hooked_exec
app = QApplication(sys.argv[:1])  # (the editor uses it)
watcher = Watcher()
app.installEventFilter(watcher)  # before the editor installs its own, which then sees each event first
print("started", flush=True)
code = cli.main(["editor", FLOW])
print(result(code=code), flush=True)
if sys.platform == "win32":  # as taskloom's own entry point ends: os._exit with Qt's windows alive crashes there
    sys.exit(code)
os._exit(code)  # (the editor ships for Windows only; elsewhere Python's own shutdown after Qt can crash, on Linux)
'''


def run_editor(folder: Path) -> dict:
    """The editor through SCRIPT, on Qt's Windows platform on Windows (elsewhere offscreen), with a new Taskloom
    folder: what it recorded, with its exit code, what it printed, its crash log and its pictures' folder."""
    pictures, home = folder / "pictures", folder / "home"
    pictures.mkdir(parents=True)
    (folder / "editor_steps.py").write_text(SCRIPT, encoding="utf-8")
    (folder / "weekly-sales.yaml").write_text(FLOW, encoding="utf-8")
    environment = {name: value for name, value in os.environ.items() if name != "QT_QPA_FONTDIR"}  # (the system's)
    environment.update(QT_QPA_PLATFORM="windows" if WINDOWS else "offscreen", TASKLOOM_HOME=str(home))
    done = run_to_files([sys.executable, str(folder / "editor_steps.py"), str(folder / "weekly-sales.yaml"),
                         str(pictures), str(BOXES)], folder, environment, 150)
    said = f"stdout:\n{done.stdout[-4000:]}\nstderr:\n{done.stderr[-4000:]}"
    assert "started" in done.stdout.splitlines(), f"Qt's platform did not start: {said}"
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, said
    crash = home / CRASH_LOG
    return {**json.loads(lines[-1][len("RESULT "):]), "returncode": done.returncode, "said": said,
            "crash_log": crash.read_text(encoding="utf-8", errors="replace") if crash.exists() else "",
            "folder": pictures}


def shown_picture(recorded: dict, name: str, qtbot) -> tuple[QLabel, QRect]:
    """One of the editor's pictures, to shoot, and where the window it is of is in it."""
    saved = recorded["pictures"][name]
    label = QLabel()
    label.setPixmap(QPixmap(str(recorded["folder"] / saved["file"])))
    label.resize(label.pixmap().size())
    qtbot.addWidget(label)
    return label, QRect(*saved["rect"])


def name_of(entry: dict) -> str:
    return NAMES.get(entry["what"], entry["what"])


def windows_shown(recorded: dict, what: str | None = None) -> list[dict]:
    return [e for e in recorded["shown"] if e["kind"] == "window" and what in (None, e["what"])]


def popups_shown(recorded: dict) -> list[dict]:
    """The File menu and the tooltip (not the windows Qt may draw a menu's own fade or slide in)."""
    return [e for e in recorded["shown"] if e["what"] in ("QMenu", "QToolTip")]


def as_expected(recorded: dict) -> bool:
    """The editor's window, Settings and each Yes/No box showed and were answered, and no other window showed."""
    return (recorded["unexpected"] == [] and sorted(e["what"] for e in windows_shown(recorded))
            == sorted(["MainWindow", "SettingsDialog", *["QMessageBox No/Yes"] * BOXES]))


def quit_cleanly(recorded: dict) -> bool:
    """The File menu and the tooltip showed, and the editor quit with no error: none caught, none printed, none in
    its crash log."""
    return ({e["what"] for e in popups_shown(recorded)} == {"QMenu", "QToolTip"} and recorded.get("error") is None
            and recorded["returncode"] == 0 and "Traceback" not in recorded["said"]
            and "uncaught error" not in recorded["crash_log"])


def turned_off_before_shown(entry: dict) -> bool:
    """The editor asked Windows to turn this window's animation off while it was not yet visible, and Windows
    answered “done” (S_OK)."""
    return any(r["attribute"] == TRANSITIONS_FORCEDISABLED and r["value"] == 1 and not r["visible"]
               and r["answer"] == 0 for r in entry["asked"])


def windows_tells(entries: list[dict]) -> bool:
    """Windows answered the proof's question about each of these windows (rather than an error)."""
    return all(e["windows_says"]["answer"] == 0 for e in entries)


def check_turned_off(p, entries: list[dict], name: str):
    """The checks that `entries` (the windows called `name`) opened with the animation off."""
    p.check(f"Before {name} showed (Windows says it was not yet visible), the editor asked Windows to turn its "
            "animation off, and Windows answered “done”", entries and all(turned_off_before_shown(e) for e in entries))
    if entries and windows_tells(entries):
        p.check(f"Asked by the proof as {name} was about to show, Windows says its animation is off",
                all(e["windows_says"]["off"] == 1 and not e["windows_says"]["visible"] for e in entries))


def windows_says(entry: dict) -> str:
    says = entry["windows_says"]
    if says["answer"] != 0:
        return f"cannot say ({says['answer'] & 0xFFFFFFFF:08X})"
    return {1: "off", 0: "not off"}.get(says["off"], str(says["off"]))


def listing(recorded: dict) -> str:
    """What the proof saw, window by window, as a table."""
    rows, boxes = [("Window", "The editor asked", "Windows answered", "Windows says", "On screen after")], 0
    for e in windows_shown(recorded) + popups_shown(recorded):
        name = name_of(e)
        if e["what"] == "QMessageBox No/Yes":
            boxes += 1
            name = f"{name} {boxes}"
        asked = [r for r in e["asked"] if r["attribute"] == TRANSITIONS_FORCEDISABLED]
        rows.append((name, "animation off" if asked else "nothing",
                     ", ".join("done" if r["answer"] == 0 else f"refused ({r['answer'] & 0xFFFFFFFF:08X})"
                               for r in asked) or "-",
                     windows_says(e), "" if e["ms"] is None else f"{e['ms']:.0f} ms"))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("   ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows)


def test_dialogs_and_yes_no_boxes_open_at_once_on_windows(proof, qtbot, tmp_path):
    p = proof("On Windows, dialogs and Yes/No boxes open at once", ASKED)
    p.idea("On Windows, the editor's windows, dialogs and Yes/No boxes now open at once: just before each one first "
           "shows, the editor tells Windows not to play its open and close animations for it. So they also close at "
           "once.")
    p.idea("Why they were slow: the editor was timed earlier on the Windows machines that build Taskloom. Qt, which "
           "draws the editor, has a Yes/No box on screen 15 to 50 ms after it is asked for: too quick to notice. So "
           "the wait you see is most likely Windows' own: on a Windows 11 desktop, Windows plays an animation for "
           "each new window, which fades in and grows into place, in about 150 to 250 ms, every time. Windows lets "
           "a program turn that animation off for its own windows, together with the one it plays as a window "
           "closes.")
    p.idea("The editor does it for every window it shows: its main window, Settings, Connections, the Run dialog, Ask "
           "User's question box, every Yes/No box. Menus and tooltips are left as they are, and so are your Windows "
           "settings: other programs open their windows as before.")
    p.idea("On macOS and Linux nothing changes: the editor makes no such call there.")
    p.why_simplest("Windows has a setting for exactly this, one for each window, which the program that owns the "
                   "window may set. The editor sets it in one place: a small watcher sees each of its windows as it "
                   "is about to show, and sets it there. No dialog had to change, and a dialog added later gets it "
                   "too, with nothing to remember.")
    p.why_simplest("Where Windows cannot do it, its answer is ignored: the window shows, with its animation, as "
                   "before. No error box, and nothing for you to set up.")
    p.alternative("Turning off Animation effects in Windows' settings",
                  "That setting (Settings > Accessibility > Visual effects) is for your whole computer: every "
                  "program's windows would stop animating, not just Taskloom's, and a program should not change it "
                  "for you. You can still turn it off yourself, if you want every window to open at once.")
    p.alternative("Making the editor's dialogs quicker to build",
                  "Building them ahead of time and keeping them would save almost nothing: Qt already has a box on "
                  "screen in 15 to 50 ms. And it would not touch the animation, which is the wait.")
    p.alternative("Setting it in each dialog, one by one",
                  "Every dialog and box would need the same line, also the boxes Qt makes itself (a Yes/No box is "
                  "one), and a new dialog could easily miss it. One watcher for all of them is less to write, and "
                  "cannot miss one.")
    p.fits("It is set up once, as the editor starts on Windows, before its main window first shows: so the main "
           "window opens at once too. It changes nothing in the windows themselves: a Yes/No box has the same "
           "buttons, keys and place; only Windows' open and close animations are gone.")
    p.fits("Like the Theme, it is about the editor's own windows only: Windows' own Open and Save windows are drawn "
           "by Windows, not by the editor, and open as Windows opens them.")

    if not WINDOWS:
        p.step("Start the editor (taskloom editor weekly-sales.yaml); open Tools > Settings… and press Cancel; "
               "answer No to three Yes/No boxes; open the File menu; show a tooltip; quit.")
        p.note(f"This run is on {'macOS' if sys.platform == 'darwin' else 'Linux'}, not Windows: there is no Windows "
               "animation here to turn off, so this run shows only that the editor asks Windows nothing. When the "
               "proof runs on Windows (as it does for each release), it shows the windows opening with the "
               "animation off.")
        p.note("The proof starts the editor in a process of its own, as taskloom editor does, with a new Taskloom "
               "folder and no scheduler service. There, any Windows library the editor asked for, and any call to "
               "it, would be recorded.")
        recorded = run_editor(tmp_path / "editor")
        p.text("What showed, in order", "\n".join(name_of(e) for e in windows_shown(recorded) + popups_shown(recorded)))
        p.check("The editor's window, Settings and the three Yes/No boxes showed and were answered as before, and "
                "nothing else", as_expected(recorded))
        p.check("The File menu and the tooltip showed, and the editor quit with no error", quit_cleanly(recorded))
        p.check("The editor asked for no Windows library and made no call to one", recorded["requests"] == [])
        return

    p.step("Start the editor: taskloom editor weekly-sales.yaml.")
    p.note("The proof starts the editor in a process of its own, as taskloom editor does, on Qt's real Windows "
           "platform, as you see it (the other proofs draw it offscreen, with no real windows, where Windows animates "
           "nothing), with a new Taskloom folder and no scheduler service. Each time the editor tells Windows to turn "
           "a window's animation off, the proof notes it, with Windows' answer; and as each window is about to show, "
           "the proof asks Windows itself whether that window's animation is off.")
    recorded = run_editor(tmp_path / "editor")
    if not windows_tells(windows_shown(recorded) + popups_shown(recorded)):
        p.note("Windows does not tell this setting back for every window: asked for it, it answered with an error "
               "code (“cannot say” in the table at the end). For those, Windows' own answer “done” to the editor, "
               "before the window showed, is what shows that its animation is off.")
    editor_window, _ = shown_picture(recorded, "editor", qtbot)
    p.shot(editor_window, "The editor, as it drew itself on Qt's Windows platform, with weekly-sales.yaml open. "
                          "(Windows draws the title bar: it is not in these pictures.)")
    p.check("The editor drew its windows on Qt's Windows platform", recorded["platform"] == "windows")
    check_turned_off(p, windows_shown(recorded, "MainWindow"), "its window")

    p.step("Choose Tools > Settings…, then press Cancel.")
    picture, settings = shown_picture(recorded, "settings", qtbot)
    p.shot(picture, "① Settings, over the editor: it opened with Windows' animation turned off.",
           highlight=[settings], badges=True)
    check_turned_off(p, windows_shown(recorded, "SettingsDialog"), "Settings")

    p.step("Ask a Yes/No box over the editor, three times, and answer No each time.")
    p.note("The proof asks the box itself, over the editor, as the editor asks each of its Yes/No questions (“Turn "
           "off the scheduler service?”, “Runs in progress”): it is the same kind of box, and the editor treats it "
           "the same way.")
    boxes = windows_shown(recorded, "QMessageBox No/Yes")
    times = ", ".join("not timed" if e["ms"] is None else f"{e['ms']:.0f} ms" for e in boxes)
    picture, box = shown_picture(recorded, "yes-no", qtbot)
    p.shot(picture, "① The first Yes/No box, over the editor: like the two after it, it opened with Windows' "
                    f"animation turned off. The three were on screen {times} after they were asked for.",
           highlight=[box], badges=True, zoom=box)
    check_turned_off(p, boxes, "each Yes/No box")
    p.check(f"Each Yes/No box was on screen within {WITHIN_MS} ms of being asked for: {times}",
            len(boxes) == BOXES and all(e["ms"] is not None and e["ms"] < WITHIN_MS for e in boxes))
    p.check("The editor showed its window, Settings and the three Yes/No boxes, each answered, and no other window",
            as_expected(recorded))

    p.step("Open the File menu, then show a tooltip.")
    p.text("What the proof saw, window by window: what the editor asked Windows before the window showed, and "
           "Windows' answer; what Windows says when the proof asks it, as the window is about to show; and for "
           "each Yes/No box, how long after it was asked for it was on screen", listing(recorded))
    popups = popups_shown(recorded)
    p.check("The editor asked Windows nothing about the File menu or the tooltip, nor about any window but those "
            "above, as each was about to show", popups and all(e["asked"] == [] for e in popups)
            and all("for" in r and recorded["shown"][r["for"]]["kind"] == "window" for r in recorded["requests"]))
    if popups and windows_tells(popups):
        p.check("Windows says their animation is not turned off: they show as before",
                all(e["windows_says"]["off"] == 0 for e in popups))
    p.check("The editor quit with no error: none caught, none printed, none in its crash log", quit_cleanly(recorded))
    p.note("What this proof cannot show is the animation itself. The pictures here are the editor's own: they show "
           "only what the editor drew. Windows plays the animation afterwards, on top of that; and on a build "
           "machine, where this proof usually runs, Windows may not play it at all. So the proof checks the two "
           "parts apart: the editor has each box on screen within 100 ms, and Windows was told, before each window "
           "showed, not to animate it.")
    p.note("The editor's own windows lose both of Windows' animations: they open at once, and close at once. Menus, "
           "tooltips and your Windows settings stay as they are, and so do other programs' windows. The animation is "
           "the best-supported explanation for the wait on Windows 11, but only your PC can confirm it: please say "
           "whether dialogs and Yes/No boxes now open at once for you.")
