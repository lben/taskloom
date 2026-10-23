"""Proofs: scenarios that drive the real app the way you would and record what it showed, for the proof PDF.

A scenario is a normal test that asks for the `proof` fixture:

    p = proof("Each start block runs its own line", "what the user asked, in their words")
    p.step("Press Run now")                    # what you do next
    p.shot(win, "what to look at")             # a screenshot the app draws of itself (a window, a dialog...)
    p.shot_while_open(open_it, "...", then)    # a modal dialog or a menu, while it is open
    p.text("The command printed", output)      # text a screenshot cannot show (CLI output, a Log excerpt)
    p.check("total_sales shows 245", ok)       # recorded, and asserted: a broken feature fails the proof
    p.note("why something is not shown")

With PROOF_DIR set, each scenario writes PROOF_DIR/<scenario>/proof.json and its screenshots, and the run writes
PROOF_DIR/session.json; packaging/proof_pdf.py makes one PDF of them. Without PROOF_DIR nothing is written (the
scenarios still run and check)."""

import datetime as dt
import json
import os
import platform
import re
import shutil
import sys
import tempfile
from pathlib import Path

import PySide6
import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent, QFont, QFontDatabase, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from taskloom import __version__

PROOF_DIR = Path(os.environ["PROOF_DIR"]).resolve() if os.environ.get("PROOF_DIR") else None
WINDOW = (1600, 1000)  # the editor in the screenshots: room for the canvas, the panels and the Log
SCREEN = (1920, 1200)  # the offscreen screen it is on (Qt's default one is smaller than the window)
_PROOF = pytest.StashKey()


@pytest.fixture(scope="session")
def qapp():
    """The application, on an offscreen screen bigger than the editor's window, with fonts on Windows too."""
    app = QApplication.instance()
    if app is None:
        if sys.platform == "win32":  # Qt's offscreen platform finds no fonts there unless pointed at them
            os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
        screen = Path(tempfile.mkdtemp(prefix="taskloom-proof-")) / "screen.json"
        screen.write_text(json.dumps({"screens": [{"name": "proof", "x": 0, "y": 0, "width": SCREEN[0],
                                                   "height": SCREEN[1], "logicalDpi": 96, "logicalBaseDpi": 96}]}))
        # Given relative to the current folder: Qt splits its platform options at ':', a Windows drive's too.
        # On the command line, not in QT_QPA_PLATFORM, so the runs the editor starts do not look for it.
        cwd = os.getcwd()
        os.chdir(screen.parent)
        try:
            app = QApplication([sys.argv[0], "-platform", f"offscreen:configfile={screen.name}"])
        finally:
            os.chdir(cwd)
            shutil.rmtree(screen.parent, ignore_errors=True)  # read once, when the application starts
        if sys.platform == "win32" and "Segoe UI" in QFontDatabase.families():
            app.setFont(QFont("Segoe UI", 9))  # what the editor uses on Windows
    assert QFontDatabase.families(), "Qt found no fonts: the screenshots would show no text"
    yield app


@pytest.fixture
def win(win, qtbot):
    """The editor (tests/conftest.py), big enough to show the canvas, the panels and the Log."""
    win.resize(*WINDOW)
    qtbot.wait(50)
    assert (win.width(), win.height()) == WINDOW, f"the editor is only {win.width()}x{win.height()}"
    return win


@pytest.fixture
def proof(request):
    """proof(title, asked) -> the record of this scenario (see this module's docstring)."""
    def start(title: str, asked: str) -> "Proof":
        assert _PROOF not in request.node.stash, "one proof per scenario"
        p = request.node.stash[_PROOF] = Proof(request.node, title, asked)
        return p
    return start


class Proof:
    def __init__(self, item, title: str, asked: str):
        self.record = {"title": title, "asked": asked, "test": item.nodeid, "status": "passed", "error": "",
                       "started": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                       "version": __version__, "entries": [],
                       "platform": f"{platform.platform()} · Python {platform.python_version()} · "
                                   f"PySide6 {PySide6.__version__}"}
        self.folder = None
        if PROOF_DIR is not None:
            self.folder = PROOF_DIR / re.sub(r"[^\w.-]+", "_", f"{item.module.__name__.split('.')[-1]}.{item.name}")
            shutil.rmtree(self.folder, ignore_errors=True)  # what an earlier run of it left
            self.folder.mkdir(parents=True)

    def step(self, text: str):
        self._add("step", text=text)

    def note(self, text: str):
        self._add("note", text=text)

    def text(self, caption: str, text: str):
        self._add("text", caption=caption, text=text)

    def check(self, description: str, condition):
        self._add("check", text=description, ok=bool(condition))
        assert condition, description

    def shot(self, widget, caption: str, over=None):
        """A screenshot of `widget` as the app draws it, once what is pending has been drawn. `over`: the window
        it is shown over (e.g. a menu over the editor), drawn around it where it is on the screen."""
        QTest.qWait(100)
        image = widget.grab().toImage()
        window = widget.windowTitle() if widget.isWindow() else ""
        if over is not None:
            base = over.grab().toImage()
            painter = QPainter(base)
            painter.drawImage(widget.mapToGlobal(QPoint(0, 0)) - over.mapToGlobal(QPoint(0, 0)), image)
            painter.end()
            image, window = base, over.windowTitle()
        assert image.width() > 1 and image.height() > 1, f"empty screenshot: {caption}"
        name = f"{sum(e['kind'] == 'shot' for e in self.record['entries']) + 1:02}.png"
        if self.folder is not None:
            assert image.save(str(self.folder / name)), f"cannot save {name}"
        self._add("shot", file=name, caption=caption, window=window)

    def shot_while_open(self, open_, caption: str, then=None, over=None):
        """Screenshot what open_() opens and waits on (a dialog's or a menu's exec()): once it is open, it is
        shot, then then(it) acts on it as you would (by default it is closed). What open_() returns."""
        opened, errors = [], []

        def look():
            widget = QApplication.activePopupWidget() or QApplication.activeModalWidget()
            if widget is None or opened:
                return
            timer.stop()
            opened.append(widget)
            try:
                self.shot(widget, caption, over)
                if then:
                    then(widget)
                else:
                    widget.close()
            except BaseException as e:  # raised again below; closed, or open_() would wait for it forever
                errors.append(e)
                widget.close()

        timer = QTimer(interval=50)
        timer.timeout.connect(look)
        timer.start()
        try:
            result = open_()
            for _ in range(200):  # it opened without waiting, or not yet
                if opened:
                    break
                QTest.qWait(50)
        finally:
            timer.stop()
        if errors:
            raise errors[0]
        assert opened, f"nothing opened: {caption}"
        return result

    def _add(self, kind: str, **fields):
        self.record["entries"].append({"kind": kind, **fields})

    def finish(self):
        if self.folder is not None:
            (self.folder / "proof.json").write_text(json.dumps(self.record, indent=1, ensure_ascii=False),
                                                    encoding="utf-8")


def choose(menu, starts_with: str):
    """Click the item of an open menu whose text starts with `starts_with`, as you would."""
    action = next(a for a in menu.actions() if a.text().startswith(starts_with))
    assert action.isEnabled(), f"{action.text()!r} is greyed out"
    QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(action).center())


def right_click(tab, block: str):
    """Right-click a block on the canvas (opens its menu, which waits for a choice: see Proof.shot_while_open)."""
    viewport = tab.view.viewport()
    at = tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center())
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, viewport.mapToGlobal(at)))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    p = item.stash.get(_PROOF, None)
    if p is not None:
        if report.failed and not p.record["error"]:
            call.excinfo.traceback = call.excinfo.traceback.cut(path=item.path)  # from the scenario on
            p.record["error"] = str(call.excinfo.getrepr(style="short", chain=False))
            p.record["status"] = "failed"
        elif report.skipped and p.record["status"] == "passed":
            p.record["status"], p.record["error"] = "skipped", str(call.excinfo.value) if call.excinfo else ""
        if report.when == "teardown":
            p.finish()
    return report


def pytest_sessionfinish(session, exitstatus):
    if PROOF_DIR is not None:  # so the PDF can say the run failed, also when a scenario recorded nothing
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        (PROOF_DIR / "session.json").write_text(json.dumps({"exitstatus": int(exitstatus)}))
