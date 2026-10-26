"""Proofs: scenarios that drive the real app the way you would and record what it showed, for the proof PDF.

Each proof tells one short story (tests/proofs/README.md says how to write one): what was asked, the idea, why
this design, the steps with their screenshots, and what was checked. A scenario is a normal test that asks for the
`proof` fixture:

    p = proof("Each start block runs its own line", "what the user asked, in their words")
    p.idea("...")                              # the design in plain words; the first is the summary on the cover
    p.why_simplest("...")                      # why it is the simplest way to do it
    p.alternative("another way", "why not")    # a way it could have been done instead, and why it lost
    p.fits("...")                              # how it fits the rest of Taskloom
    p.step("Press Run now")                    # what you do next
    p.shot(win, "what to look at ①", highlight=["total_sales"], badges=True, zoom=["read_sales", "total_sales"])
    p.while_open(open_it, look)                # look(it) while what open_it() opens waits (a dialog, a menu)
    p.text("The command printed", output)      # text a screenshot cannot show (CLI output, a Log excerpt)
    p.check("total_sales shows 245", ok)       # recorded, and asserted: a broken feature fails the proof
    p.note("why something is not shown")

The idea, why it is the simplest, at least one alternative and how it fits are required: a proof without them
fails. With PROOF_DIR set, each scenario writes PROOF_DIR/<scenario>/proof.json and its screenshots, and the run
writes PROOF_DIR/session.json; packaging/proof_pdf.py makes one PDF of them. Without PROOF_DIR nothing is written
(the scenarios still run and check)."""

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
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QContextMenuEvent, QFont, QFontDatabase, QPainter, QPen, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMenu, QPlainTextEdit, QTableWidgetItem, QWidget

from taskloom import __version__

PROOF_DIR = Path(os.environ["PROOF_DIR"]).resolve() if os.environ.get("PROOF_DIR") else None
WINDOW = (1600, 1000)  # the editor in the screenshots: room for the canvas, the panels and the Log
SCREEN = (1920, 1200)  # the offscreen screen it is on (Qt's default one is smaller than the window)
MARK = QColor("#e8590c")  # highlights and their badges (packaging/proof_pdf.py draws the badges in the text too)
ZOOM_MARGIN = 24  # around what a zoom shows, in screen pixels
STORY = {"idea": "the idea (p.idea)", "why_simplest": "why it is the simplest (p.why_simplest)",
         "alternatives": "another way it could have been done, and why not (p.alternative)",
         "fits": "how it fits the rest of Taskloom (p.fits)"}
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
        self.record = {"title": title, "asked": asked, "status": "passed", "error": "",
                       "started": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                       "version": __version__, "idea": [], "why_simplest": [], "alternatives": [], "fits": [],
                       "entries": [], "platform": f"{platform.platform()} · Python {platform.python_version()} · "
                                                 f"PySide6 {PySide6.__version__}"}
        self.folder = None
        if PROOF_DIR is not None:
            self.folder = PROOF_DIR / re.sub(r"[^\w.-]+", "_", f"{item.module.__name__.split('.')[-1]}.{item.name}")
            shutil.rmtree(self.folder, ignore_errors=True)  # what an earlier run of it left
            self.folder.mkdir(parents=True)

    def idea(self, text: str):
        self.record["idea"].append(text)

    def why_simplest(self, text: str):
        self.record["why_simplest"].append(text)

    def alternative(self, option: str, why_not: str):
        self.record["alternatives"].append({"option": option, "why_not": why_not})

    def fits(self, text: str):
        self.record["fits"].append(text)

    def missing(self) -> list[str]:
        """What the story does not say yet, of what it must."""
        return [what for part, what in STORY.items() if not self.record[part]]

    def step(self, text: str):
        self._add("step", text=text)

    def note(self, text: str):
        self._add("note", text=text)

    def text(self, caption: str, text: str):
        self._add("text", caption=caption, text=text)

    def check(self, description: str, condition):
        self._add("check", text=description, ok=bool(condition))
        assert condition, description

    def shot(self, widget, caption: str, over=None, highlight=(), badges=False, zoom=None):
        """A screenshot of `widget` as the app draws it, once what is pending has been drawn. `over`: the window
        it is shown over (e.g. a menu over the editor), drawn around it where it is on the screen.

        `highlight`: what to look at, each outlined: a block of the canvas (its name), a widget, a QAction (that
        item of the open menu it is in), a (table, row) pair (that row), a table's cell (its QTableWidgetItem), a
        (text box, text) pair (its lines that show that text, e.g. in the Log), a QRect (in the screenshot's
        pixels), or a list of these (one outline around them all). In a menu, a table or a text box, where items,
        rows and lines touch, the outline hugs what it outlines: it covers neither the header nor the next row.
        `badges`: True numbers the outlines 1, 2, 3…; a list gives each its number (e.g. [1, 2, 1]); a caption says
        ①, ②… for them. `zoom`: a part of the screenshot (said the same way) shown bigger next to it."""
        QTest.qWait(100)
        base = over if over is not None else widget
        image = base.grab().toImage()
        if over is not None:
            painter = QPainter(image)
            painter.drawImage(widget.mapToGlobal(QPoint(0, 0)) - over.mapToGlobal(QPoint(0, 0)),
                              widget.grab().toImage())
            painter.end()
        assert image.width() > 1 and image.height() > 1, f"empty screenshot: {caption}"
        number = sum(e["kind"] == "shot" for e in self.record["entries"]) + 1
        entry = {"file": f"{number:02}.png", "caption": caption}

        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        # An outline at the edge stays whole, clear of the frame the PDF draws around each picture.
        inside = QRectF(image.rect()).adjusted(5, 5, -5, -5)
        glow = QColor(MARK.red(), MARK.green(), MARK.blue(), 70)
        places = []  # of the badges
        for target in highlight:
            area, tight = _area(target, base)
            margin, radius = (1, 6) if tight else (6, 9)  # tight: on the border it shares with the next row
            outline = QRectF(area).adjusted(-margin, -margin, margin, margin) & inside
            for color, width in ((MARK, 3.5),) if tight else ((glow, 10), (MARK, 3.5)):
                painter.setPen(QPen(color, width))
                painter.drawRoundedRect(outline, radius, radius)
            # Its badge on its top right corner (text starts on the left); beside it when it hugs text.
            places.append(outline.topRight() + QPointF(10 if tight else 0, 0))
        numbers = range(1, len(places) + 1) if badges is True else badges or []
        assert len(numbers) in (0, len(places)), "a badge for each outline"
        for n, at in zip(numbers, places):
            _badge(painter, min(max(at.x(), 14), image.width() - 14), min(max(at.y(), 14), image.height() - 14), n)
        zoomed = None
        if zoom is not None:
            part = _area(zoom, base)[0].adjusted(-ZOOM_MARGIN, -ZOOM_MARGIN, ZOOM_MARGIN, ZOOM_MARGIN) & image.rect()
            assert not part.isEmpty(), f"the zoom is outside the screenshot: {caption}"
            zoomed = image.copy(part)
            entry["zoom"] = f"{number:02}-zoom.png"
            painter.setPen(QPen(QColor("#24292f"), 4, Qt.DashLine))  # where the zoom is from: seen when shown small
            painter.drawRect(QRectF(part).adjusted(1, 1, -1, -1))
        painter.end()

        if self.folder is not None:
            assert image.save(str(self.folder / entry["file"])), f"cannot save {entry['file']}"
            if zoomed is not None:
                assert zoomed.save(str(self.folder / entry["zoom"])), f"cannot save {entry['zoom']}"
        self._add("shot", **entry)

    def while_open(self, open_, then):
        """Call open_(), which opens a modal dialog or a menu and waits on it (e.g. a right-click on a block,
        choosing a menu item), and then(it) once it is open: to shoot it and act on it as you would. Closed after,
        if then(it) left it open. What open_() returns."""
        opened, errors = [], []

        def look():
            widget = QApplication.activePopupWidget() or QApplication.activeModalWidget()
            if widget is None or opened:
                return
            timer.stop()
            opened.append(widget)
            try:
                then(widget)
            except BaseException as e:  # raised again below
                errors.append(e)
            if widget.isVisible():  # or open_() would wait for it forever
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
        assert opened, "nothing opened"
        return result

    def _add(self, kind: str, **fields):
        self.record["entries"].append({"kind": kind, **fields})

    def finish(self):
        if self.folder is not None:
            (self.folder / "proof.json").write_text(json.dumps(self.record, indent=1, ensure_ascii=False),
                                                    encoding="utf-8")


def _area(target, base: QWidget) -> tuple[QRect, bool]:
    """Where `target` (see Proof.shot) is in a screenshot of `base`, and whether it touches others like it (a menu's
    item, a table's row or cell, a text box's lines): its outline then hugs it."""
    if isinstance(target, QRect):
        return target, False
    if isinstance(target, list):
        assert target, "an empty list to highlight"
        area, tight = QRect(), False
        for each in target:
            more = _area(each, base)
            area, tight = area | more[0], tight or more[1]
        return area, tight
    tight = not isinstance(target, (str, QWidget))
    if isinstance(target, str):
        tab = getattr(base, "current", None)
        assert tab is not None, f"{target!r}: blocks are found on the editor window's canvas"
        widget = tab.view.viewport()
        rect = tab.view.mapFromScene(tab.scene.nodes[target].sceneBoundingRect()).boundingRect()
    elif isinstance(target, QAction):
        widget = next((m for m in target.associatedObjects() if isinstance(m, QMenu) and m.isVisible()), None)
        assert widget is not None, f"{target.text()!r} is in no open menu"
        rect = widget.actionGeometry(target)
    elif isinstance(target, QTableWidgetItem):
        widget, rect = target.tableWidget().viewport(), target.tableWidget().visualItemRect(target)
    elif isinstance(target, tuple) and isinstance(target[0], QPlainTextEdit):
        box, text = target
        widget, rect = box.viewport(), QRect()
        found = box.document().find(text)
        while not found.isNull():  # each line it is on, from its start to its end
            line = QTextCursor(found)
            line.movePosition(QTextCursor.StartOfLine)
            rect |= box.cursorRect(line)
            line.movePosition(QTextCursor.EndOfLine)
            rect |= box.cursorRect(line)
            found = box.document().find(text, found)
        assert not rect.isEmpty(), f"no line shows {text!r}"
        rect.adjust(-3, 0, 3, 0)  # lines touch only above and below: room on the sides, so it clears the text
    elif isinstance(target, tuple):
        view, row = target
        assert isinstance(view, QAbstractItemView), f"{target!r}: a (table, row) or (text box, text) pair"
        model, widget = view.model(), view.viewport()
        rect = QRect()
        for column in range(model.columnCount()):
            rect |= view.visualRect(model.index(row, column))
    else:
        widget, rect = target, target.rect()
    return rect.translated(widget.mapToGlobal(QPoint(0, 0)) - base.mapToGlobal(QPoint(0, 0))), tight


def _badge(painter: QPainter, x: float, y: float, number: int):
    """A numbered badge centred on (x, y), as packaging/proof_pdf.py draws it in the text."""
    painter.setPen(QPen(QColor("white"), 2))
    painter.setBrush(MARK)
    painter.drawEllipse(QRectF(x - 12, y - 12, 24, 24))
    font = QFont(painter.font())
    font.setPixelSize(14)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(QRectF(x - 12, y - 12, 24, 24), Qt.AlignCenter, str(number))
    painter.setBrush(Qt.NoBrush)


def menu_item(menu, starts_with: str) -> QAction:
    """The item of an open menu whose text starts with `starts_with` (to choose it, or to highlight it)."""
    return next(a for a in menu.actions() if a.text().startswith(starts_with))


def choose(menu, starts_with: str):
    """Click the item of an open menu whose text starts with `starts_with`, as you would."""
    action = menu_item(menu, starts_with)
    assert action.isEnabled(), f"{action.text()!r} is greyed out"
    QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(action).center())


def right_click(tab, block: str):
    """Right-click a block on the canvas (opens its menu, which waits for a choice: see Proof.while_open)."""
    viewport = tab.view.viewport()
    at = tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center())
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, viewport.mapToGlobal(at)))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    result = yield
    p = item.stash.get(_PROOF, None)
    if p is not None and (missing := p.missing()):  # the story is part of the proof
        p.record["status"] = "failed"
        p.record["error"] = f"The proof does not say {', nor '.join(missing)}: see tests/proofs/README.md"
        raise AssertionError(p.record["error"])
    return result


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
