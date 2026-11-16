"""Zoom the pane under the pointer (docs/promises/zoom.yaml, ZM-1..ZM-6, agreed 2026-11-16): Ctrl with +, = or -
(main keys and number pad, and Ctrl+Shift+= where + needs Shift) changes the size of the pane under the mouse pointer,
wherever the keyboard focus is, Ctrl 0 puts it back to its normal size, and Ctrl with the mouse wheel over a text pane
does the same (ZM-1); over anything else the keys do nothing (ZM-2); each kind of pane keeps its size when the editor
is closed and opened again, the canvas its zoom per flow until the flow is closed (ZM-3); there is a smallest and a
largest size (ZM-4); zoom changes only the size (ZM-5); a code box built again has the remembered size (ZM-6).

The panes: the code boxes (Python, SQL, HTML), the Log, Data preview (its table and its text), Run history, the blocks
tree, the docs (Help > Documentation) and the canvas. Driven through the real editor window (the `win` fixture, with a
settings folder of its own): the window fits the screen (the offscreen one is 800 x 800, and the pointer can only be
where a screen is), the pointer is put over a pane with QCursor.setPos (the offscreen platform moves it there, and
QApplication.widgetAt finds the pane under it), the keyboard focus is somewhere else, and keys and wheel turns are sent
as the system sends them: a key to the widget with the focus, a wheel turn to the widget under the pointer. What is
measured is what shows: how high a text pane's lines are and how wide it lays out its text, the font a list or table
draws its items in, the canvas's scale. The Log and Data preview are filled through their panels, as after a run; Run
history lists runs recorded in the settings folder's history.db; Results of this block shows a real run's values."""

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QFontMetricsF, QImage, QTextCursor, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QLineEdit, QPlainTextEdit, QToolBar,  # noqa: E402
                               QToolTip, QTreeView)

from taskloom.config import Home  # noqa: E402
from taskloom.editor import dialogs  # noqa: E402
from taskloom.editor.window import MainWindow  # noqa: E402
from taskloom.history import History  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
from tests.test_code_editor import clean, code_box, drawn_texts, line_numbers, select, wrap_box  # noqa: E402
from tests.test_find_bar import behind, differs, open_find, plain, record_runs, run_in_editor, show_log  # noqa: E402
from tests.test_find_bar import where, write_flow  # noqa: E402

CTRL, PAD, SHIFT = Qt.ControlModifier, Qt.KeypadModifier, Qt.ShiftModifier
IN, EQUALS, OUT, NORMAL = (Qt.Key_Plus, CTRL), (Qt.Key_Equal, CTRL), (Qt.Key_Minus, CTRL), (Qt.Key_0, CTRL)
PAD_IN, PAD_OUT, PAD_NORMAL = (Qt.Key_Plus, CTRL | PAD), (Qt.Key_Minus, CTRL | PAD), (Qt.Key_0, CTRL | PAD)
SHIFT_IN = (Qt.Key_Plus, CTRL | SHIFT)  # Ctrl+Shift+= on a keyboard where + needs Shift

CODE_BOXES = {"Python code box": "python", "SQL code box": "sql", "HTML code box": "html"}
TEXT_PANES = [*CODE_BOXES, "Log", "Data preview text", "docs"]
LISTS = ["Data preview table", "Run history", "blocks tree"]
KINDS = [*TEXT_PANES, *LISTS, "canvas"]

LINES = 30
PYTHON = "\n".join(f"step_{i} = {i}  # the {i}th step of the report" for i in range(1, LINES + 1))
SQL = "\n".join(f"select region_{i}, sum(total) as total_{i} from sales group by 1" for i in range(1, LINES + 1))
HTML = "\n".join(f"<p>Region {i}: the totals of the day</p>" for i in range(1, LINES + 1))
BLOCKS = {
    "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
    "python": {"type": "logic.python", "config": {"inputs": [], "code": PYTHON}, "ui": {"x": 220, "y": 0}},
    "sql": {"type": "data.duckdb_sql", "config": {"sql": SQL}, "ui": {"x": 0, "y": 130}},
    "html": {"type": "reports.html_template", "config": {"template": HTML}, "ui": {"x": 220, "y": 130}},
    "read": {"type": "data.read_file", "config": {"path": "sales.csv"}, "ui": {"x": 440, "y": 0}},
    "again": {"type": "logic.python", "config": {"inputs": [], "code": "total = 1"}, "ui": {"x": 440, "y": 130}},
}
LOG = [f"line {i} of the nightly report" for i in range(40)]
PREVIEW_TEXT = "\n".join(f"row {i}: region {i % 4}, total {i * 10}" for i in range(60))
RUNS = 12


# --- the editor, the pointer, the keys and the wheel ---------------------------------------------------------------

def settle(rounds: int = 5):
    for _ in range(rounds):
        QApplication.processEvents()


def wait_until(condition, seconds: float = 15, what: str = "") -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, f"waited {seconds} s for {what or condition}"
        settle(1)


def menu(win, text: str) -> QAction:
    return next(a for a in win.findChildren(QAction) if a.text() == text)


def fit(win):
    """The window on the screen: the pointer can only be put where a screen is (offscreen: 800 x 800)."""
    win.setGeometry(QApplication.primaryScreen().availableGeometry())
    settle()


def make_room(win, kind: str):
    """Room on the screen for the pane: the canvas without the side panels (so that a small flow shows at 100%), the
    Documentation column without the Inspector (else it opens right of the screen)."""
    if kind == "canvas":
        win.blocks_dock.hide()
        win.inspector.hide()
    if kind == "docs":
        win.inspector.hide()
    settle()


def pointer_over(widget, name: str = ""):
    """Put the mouse pointer over `widget` (a scroll area: over the middle of what its viewport shows on the screen),
    and check it is the widget found under the pointer there."""
    name = name or type(widget).__name__
    area = widget.viewport() if isinstance(widget, QAbstractScrollArea) else widget
    shown = area.visibleRegion().boundingRect()
    rect = QRect(area.mapToGlobal(shown.topLeft()), shown.size()).intersected(QApplication.primaryScreen().geometry())
    assert not rect.isEmpty(), f"the {name} does not show on the screen"
    pointer_at(rect.center())
    under = QApplication.widgetAt(rect.center())
    assert under is not None and (under is widget or widget.isAncestorOf(under)), (
        f"the pointer is over {type(under).__name__ if under else None}, not over the {name}")


def pointer_at(at: QPoint):
    QCursor.setPos(at)
    settle()
    assert QCursor.pos() == at, f"the pointer is at {QCursor.pos()}, not at {at}"


def focus_on(widget):
    """The keyboard focus in `widget`, as when you click into it."""
    assert widget.isVisible(), f"{type(widget).__name__} does not show"
    widget.window().activateWindow()
    widget.setFocus()
    wait_until(widget.hasFocus, 5, f"the keyboard focus in {type(widget).__name__}")


def press(*keys):
    """Each (key, modifiers), as typed: sent to the widget with the keyboard focus (an open menu takes the keys)."""
    for key, modifiers in keys:
        to = QApplication.activePopupWidget() or QApplication.focusWidget()
        assert to is not None, "no widget has the keyboard focus"
        QTest.keyClick(to, key, modifiers)
        settle()


def wheel(turns: int, modifiers=CTRL, units: int = 120):
    """The mouse wheel turned `turns` times (more than 0: up, away from you) by `units` each with `modifiers` held,
    sent to the widget under the pointer, as the system sends it. 120 units are one notch of a mouse wheel, as Qt
    counts them; a trackpad, or a wheel that turns freely, sends turns of fewer units."""
    at = QCursor.pos()
    under = QApplication.widgetAt(at)
    assert under is not None, "nothing is under the pointer"
    for _ in range(abs(turns)):
        event = QWheelEvent(QPointF(under.mapFromGlobal(at)), QPointF(at), QPoint(0, 0),
                            QPoint(0, units if turns > 0 else -units), Qt.NoButton, modifiers, Qt.NoScrollPhase, False)
        QApplication.sendEvent(under, event)
        settle()


def bigger(after, before) -> bool:
    return all(a >= b for a, b in zip(after, before)) and any(a > b for a, b in zip(after, before))


# --- the panes and how big they show -------------------------------------------------------------------------------

@dataclass
class Pane:
    kind: str
    widget: object  # what the pointer is put over
    measure: Callable[[], tuple]

    def size(self) -> tuple:
        return self.measure()


def sample_at(view) -> int:
    """Where a text pane's first line of body text (not a heading) with 8 characters starts (its spaces left out)."""
    block = view.document().begin()
    while block.isValid():
        text = block.text()
        start = len(text) - len(text.lstrip())
        if block.blockFormat().headingLevel() == 0 and len(text.strip()) >= 8 and "￼" not in text[:start + 8]:
            return block.position() + start
        block = block.next()
    raise AssertionError(f"{type(view).__name__} shows no line of 8 characters")


def rect_at(view, position: int) -> QRect:
    cursor = QTextCursor(view.document())
    cursor.setPosition(position)
    return view.cursorRect(cursor)


def text_pane(kind: str, view) -> Pane:
    """A text pane's size: the height of its line, and the width it lays 8 characters of that line out in."""
    def measure():
        at = sample_at(view)
        first, last = rect_at(view, at), rect_at(view, at + 8)
        assert first.top() == last.top(), f"{kind}: the 8 characters measured are not on one line"
        return first.height(), last.left() - first.left()
    return Pane(kind, view, measure)


def first_item(view):
    model = view.model()
    assert model is not None and model.rowCount() > 0, f"{type(view).__name__} lists nothing"
    index = model.index(0, 0)
    if isinstance(view, QTreeView) and model.rowCount(index):
        index = model.index(0, 0, index)  # (the blocks tree: the first block of the first category)
    return index


def drawn_font(view, index) -> QFont:
    """The font `view` draws the item at `index` in: what the item sets of its own font over the view's."""
    own = index.data(Qt.FontRole)
    return own.resolve(view.font()) if own is not None else QFont(view.font())


def list_pane(kind: str, view) -> Pane:
    """A list's or table's size: the height and width of its first item's text in the font it draws it in."""
    def measure():
        index = first_item(view)
        metrics = QFontMetricsF(drawn_font(view, index))
        return metrics.height(), metrics.horizontalAdvance(str(index.data()))
    return Pane(kind, view, measure)


def canvas_pane(tab) -> Pane:
    return Pane("canvas", tab.view, lambda: (round(100 * tab.view.transform().m11(), 3),))


def zoom_flow(folder: Path) -> Path:
    path = folder / "zoom.yaml"
    if not path.exists():
        write_flow(path, BLOCKS)
    return path


def open_zoom_flow(win, folder: Path):
    tab = win.open_flow(zoom_flow(folder))
    settle()
    return tab


def sales_table(folder: Path) -> Path:
    import polars as pl

    path = folder / "sales.parquet"
    if not path.exists():
        pl.DataFrame({"region": [f"region {i % 4}" for i in range(40)],
                      "total": [i * 10 for i in range(40)]}).write_parquet(path)
    return path


def show_pane(win, folder: Path, kind: str) -> Pane:
    """The pane of this kind showing in the editor (with something in it), and how big it shows."""
    if kind in CODE_BOXES:
        select(open_zoom_flow(win, folder), CODE_BOXES[kind])
        return text_pane(kind, code_box(win))
    if kind == "Log":
        show_log(win, [("python", line) for line in LOG])  # (a block selected shows only its own lines)
        return text_pane(kind, win.log.text)
    if kind == "Data preview table":
        win.bottom_tabs.setCurrentWidget(win.preview)
        win.preview.show_outputs("sales", {"tables": {"table": str(sales_table(folder))}})
        settle()
        return list_pane(kind, win.preview.view)
    if kind == "Data preview text":
        win.bottom_tabs.setCurrentWidget(win.preview)
        win.preview.show_outputs("report", {"previews": {"text": PREVIEW_TEXT}})
        settle()
        return text_pane(kind, win.preview.text)
    if kind == "Run history":
        tab = open_zoom_flow(win, folder)
        history = History(win.home.history_db)
        try:
            recorded = len(history.runs(str(tab.doc.path)))
        finally:
            history.close()
        if not recorded:
            record_runs(win, str(tab.doc.path), [{"status": "failed" if i % 5 == 0 else "success"}
                                                  for i in range(RUNS)])
        table = win.history_panel.table
        wait_until(lambda: table.rowCount() == RUNS, what=f"Run history to list the {RUNS} runs")
        win.bottom_tabs.setCurrentWidget(win.history_panel)
        settle()
        return list_pane(kind, table)
    if kind == "blocks tree":
        return list_pane(kind, win.palette_panel.tree)
    if kind == "docs":
        if not win.docs.isVisible():
            menu(win, "Documentation").trigger()  # Help > Documentation
        settle()
        return text_pane(kind, win.docs.browser)
    if kind == "canvas":
        return canvas_pane(open_zoom_flow(win, folder))
    raise AssertionError(kind)


def elsewhere(win, kind: str):
    """Where the keyboard focus is while the pointer is over a pane of `kind`: in another pane."""
    return win.log.text if kind in ("blocks tree", "canvas") else win.palette_panel.tree


def ready(win, folder: Path, kind: str) -> Pane:
    """The pane showing, the focus elsewhere, the pointer over the pane."""
    fit(win)
    make_room(win, kind)
    pane = show_pane(win, folder, kind)
    if kind == "canvas":
        assert pane.size() == (100,), f"the small flow opens at {pane.size()[0]}%, not 100%"
    focus_on(elsewhere(win, kind))
    pointer_over(pane.widget, kind)
    return pane


DOCS_PAGE = "the-editor.html"  # the guide's page on the editor: long paragraphs, which the docs column wraps


def docs_page(win):
    """The docs (showing, the pointer over them) at the guide's page on the editor: their view."""
    win.docs.open_at(DOCS_PAGE)
    settle()
    view = win.docs.browser
    pointer_over(view, "docs")
    return view


def wrapped_paragraph(view, lines: int):
    """A paragraph in the middle of the page that the view wraps into at least `lines` lines."""
    view.document().documentLayout().blockBoundingRect(view.document().lastBlock())  # (the whole page laid out)
    height = view.document().size().height()
    block = view.document().begin()
    while block.isValid():
        layout = block.layout()
        if (block.blockFormat().headingLevel() == 0 and layout.lineCount() >= lines
                and 0.3 < layout.position().y() / height < 0.7):
            return block
        block = block.next()
    raise AssertionError(f"the middle of {view.source().toString()} has no paragraph wrapped into {lines} lines")


def line_holding(view, position: int) -> tuple[int, str]:
    """The line the view shows the character at `position` on: where it starts, and its text."""
    block = view.document().findBlock(position)
    view.document().documentLayout().blockBoundingRect(block)  # (laid out)
    line = block.layout().lineForTextPosition(position - block.position())
    assert line.isValid(), f"no line shows the character at {position}"
    return block.position() + line.textStart(), block.text()[line.textStart():line.textStart() + line.textLength()]


def top_of(view) -> int:
    """Where the line at the top of the view starts."""
    return line_holding(view, view.cursorForPosition(QPoint(1, 1)).position())[0]


def scroll_line_to_top(view, position: int):
    """Scroll so that the line holding `position` is the top line in view."""
    bar = view.verticalScrollBar()
    bar.setValue(bar.value() + rect_at(view, position).top())
    settle()
    assert top_of(view) == line_holding(view, position)[0], (
        f"the top line is {line_holding(view, top_of(view))[1]!r}, not {line_holding(view, position)[1]!r}")


def started_again(qtbot, home: Path) -> MainWindow:
    """The editor opened again on the settings folder `home`."""
    again = MainWindow(Home(home))
    qtbot.addWidget(again)
    again.show()
    settle()
    return again


def closed(window):
    """The editor closed, as when you close its window (nothing left unsaved to ask about)."""
    clean(window)
    window.close()
    settle(10)
    assert not window.isVisible()


def shown_size(home: Path, folder: Path, kind: str) -> list:
    """In an editor opened on the settings folder `home`: how big the pane of `kind` shows."""
    window = MainWindow(Home(home))
    window.show()
    settle()
    fit(window)
    make_room(window, kind)
    size = show_pane(window, folder, kind).size()
    closed(window)
    return list(size)


NEW_EDITOR = """
import json, sys
from pathlib import Path
from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication([])
for name in ("information", "warning", "question"):  # (one would wait for a click: said, and answered No)
    setattr(QMessageBox, name,
            staticmethod(lambda *a, **k: print("message box:", a[1:3], file=sys.stderr) or QMessageBox.No))
from taskloom.editor.window import MainWindow
MainWindow.start_scheduler = lambda self, by_you=False: None  # (a real scheduler service would outlive the test)
from tests.test_zoom_panes import shown_size
print("SIZE", json.dumps(shown_size(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])))
"""


def size_after_a_restart(home: Path, folder: Path, kind: str) -> tuple:
    """Taskloom started again (a new process) on the settings folder `home`: how big the pane of `kind` shows. Only
    what is in the folder carries over, as after quitting Taskloom or restarting the computer."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "TASKLOOM_HOME": str(home),
           "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
           "PYTHONPATH": os.pathsep.join([str(REPO), *filter(None, [os.environ.get("PYTHONPATH")])])}
    result = subprocess.run([sys.executable, "-c", NEW_EDITOR, str(home), str(folder), kind], cwd=REPO, env=env,
                            capture_output=True, text=True, timeout=180)
    said = [line for line in result.stdout.splitlines() if line.startswith("SIZE ")]
    assert result.returncode == 0 and said, f"the editor started again failed:\n{result.stderr[-3000:]}"
    assert "message box:" not in result.stderr, result.stderr[-3000:]
    return tuple(json.loads(said[0][len("SIZE "):]))


# --- ZM-1: the keys change the pane under the pointer, wherever the focus is ---------------------------------------

PAIRS = [("Log", "Python code box"), ("Python code box", "Log"), ("blocks tree", "canvas"),
         ("canvas", "blocks tree"), ("Run history", "Python code box")]


@pytest.mark.invariant("ZM-1")
@pytest.mark.parametrize("pointed, focused", PAIRS, ids=[f"{a} - focus in {b}" for a, b in PAIRS])
def test_ctrl_plus_zooms_the_pane_under_the_pointer_not_the_one_with_the_focus(win, qtbot, tmp_path, pointed, focused):
    """Z1: the pointer over pane A, the keyboard focus in pane B, Ctrl +: A grows, B keeps its size, its text and the
    focus (no "+" typed into a code box, nothing changed in the flow); Ctrl 0 puts A back, B still as it was."""
    fit(win)
    tab = open_zoom_flow(win, tmp_path)
    a = show_pane(win, tmp_path, pointed)
    b = show_pane(win, tmp_path, focused)
    try:
        a_normal, b_normal = a.size(), b.size()
        text = b.widget.toPlainText() if hasattr(b.widget, "toPlainText") else None
        focus_on(b.widget)
        pointer_over(a.widget, pointed)
        press(IN)
        assert bigger(a.size(), a_normal), f"{pointed} under the pointer: Ctrl + left it at {a.size()} ({a_normal})"
        assert b.size() == b_normal, f"{focused}, with the focus: Ctrl + changed it from {b_normal} to {b.size()}"
        if text is not None:
            assert b.widget.toPlainText() == text, f"Ctrl + changed the text of the {focused}"
        assert not tab.doc.dirty, "Ctrl + changed the flow"
        assert b.widget.hasFocus(), f"the focus left the {focused}"
        press(NORMAL)
        assert b.size() == b_normal, f"{focused}, with the focus: Ctrl 0 changed it from {b_normal} to {b.size()}"
        if pointed != "canvas":  # (here the flow opened zoomed out to fit: Ctrl 0 gives 100%, checked on its own)
            assert a.size() == a_normal, f"Ctrl 0 over the {pointed} gave {a.size()}, not its normal size {a_normal}"
    finally:
        clean(win)


@pytest.mark.invariant("ZM-1")
@pytest.mark.parametrize("kind", KINDS)
def test_ctrl_plus_and_ctrl_equals_grow_the_pane_ctrl_minus_shrinks_it_ctrl_0_makes_it_normal(win, qtbot, tmp_path,
                                                                                               kind):
    """Z2: over each kind of pane, Ctrl + makes it bigger, Ctrl = bigger again, Ctrl 0 normal, Ctrl - smaller, Ctrl 0
    normal again."""
    pane = ready(win, tmp_path, kind)
    try:
        normal = pane.size()
        press(IN)
        grown = pane.size()
        assert bigger(grown, normal), f"Ctrl + over the {kind} left it at {grown} (normal {normal})"
        press(EQUALS)
        assert bigger(pane.size(), grown), f"Ctrl = over the {kind} left it at {pane.size()} (after Ctrl +: {grown})"
        press(NORMAL)
        assert pane.size() == normal, f"Ctrl 0 over the {kind} gave {pane.size()}, not its normal size {normal}"
        press(OUT)
        assert bigger(normal, pane.size()), f"Ctrl - over the {kind} left it at {pane.size()} (normal {normal})"
        press(NORMAL)
        assert pane.size() == normal, f"Ctrl 0 over the {kind} gave {pane.size()}, not its normal size {normal}"
    finally:
        clean(win)


@pytest.mark.invariant("ZM-1")
@pytest.mark.parametrize("kind", KINDS)
def test_the_number_pad_keys_and_ctrl_shift_equals_do_as_the_main_keys(win, qtbot, tmp_path, kind):
    """Z3: number pad +, - and 0 with Ctrl, and Ctrl+Shift+= (a keyboard where + needs Shift), give the same sizes as
    Ctrl +, Ctrl - and Ctrl 0 on the main keys."""
    pane = ready(win, tmp_path, kind)
    try:
        normal = pane.size()
        press(IN)
        grown = pane.size()
        assert bigger(grown, normal), f"Ctrl + over the {kind} left it at {grown} (normal {normal})"
        press(NORMAL, OUT)
        shrunk = pane.size()
        assert bigger(normal, shrunk), f"Ctrl - over the {kind} left it at {shrunk} (normal {normal})"
        press(NORMAL, PAD_IN)
        assert pane.size() == grown, f"number pad + over the {kind}: {pane.size()}, Ctrl +: {grown}"
        press(PAD_NORMAL)
        assert pane.size() == normal, f"number pad 0 over the {kind}: {pane.size()}, normal: {normal}"
        press(PAD_OUT)
        assert pane.size() == shrunk, f"number pad - over the {kind}: {pane.size()}, Ctrl -: {shrunk}"
        press(NORMAL, SHIFT_IN)
        assert pane.size() == grown, f"Ctrl+Shift+= over the {kind}: {pane.size()}, Ctrl +: {grown}"
    finally:
        clean(win)


@pytest.mark.invariant("ZM-1")
@pytest.mark.invariant("ZM-3")
@pytest.mark.parametrize("kind", TEXT_PANES)
def test_ctrl_and_the_wheel_over_a_text_pane_do_as_the_keys_and_the_size_is_kept(win, qtbot, tmp_path, kind):
    """Z4: over a text pane, Ctrl and the wheel turned up one notch give the size Ctrl + gives, down one notch the size
    Ctrl - gives; a size the wheel gave is the one the pane has when the editor is opened again."""
    pane = ready(win, tmp_path, kind)
    try:
        normal = pane.size()
        press(IN)
        grown = pane.size()
        assert bigger(grown, normal), f"Ctrl + over the {kind} left it at {grown} (normal {normal})"
        press(NORMAL, OUT)
        shrunk = pane.size()
        press(NORMAL)
        wheel(1)
        assert pane.size() == grown, f"Ctrl + wheel up over the {kind}: {pane.size()}, Ctrl +: {grown}"
        press(NORMAL)
        wheel(-1)
        assert pane.size() == shrunk, f"Ctrl + wheel down over the {kind}: {pane.size()}, Ctrl -: {shrunk}"
        wheel(3)
        kept = pane.size()
        assert bigger(kept, grown)
    finally:
        closed(win)
    again = started_again(qtbot, win.home.root)
    try:
        fit(again)
        make_room(again, kind)
        assert show_pane(again, tmp_path, kind).size() == kept, f"the {kind} lost the size the wheel gave it"
    finally:
        closed(again)
    shown = size_after_a_restart(win.home.root, tmp_path, kind)
    assert shown == kept, f"Taskloom started again: the {kind} shows at {shown}, not at the size the wheel gave {kept}"


@pytest.mark.invariant("ZM-1")
def test_small_turns_of_a_trackpad_add_up_to_one_step_a_notch(win, qtbot, tmp_path):
    """ZM-1: a trackpad sends many turns smaller than a mouse wheel's notch (120 units). Over the Log, with Ctrl held,
    turns of 15 that add up to less than a notch change no size; the one that makes them a notch gives the size one
    Ctrl + gives; turns of 30 down that add up to a notch give the normal size again."""
    pane = ready(win, tmp_path, "Log")
    try:
        normal = pane.size()
        press(IN)
        grown = pane.size()
        assert bigger(grown, normal), f"Ctrl + over the Log left it at {grown} (normal {normal})"
        press(NORMAL)
        wheel(7, units=15)
        assert pane.size() == normal, (
            f"Ctrl and 7 turns of 15 units up (105, less than a notch) over the Log: {pane.size()}, not {normal}")
        wheel(1, units=15)
        assert pane.size() == grown, f"Ctrl and 8 turns of 15 units up (a notch): {pane.size()}, Ctrl +: {grown}"
        wheel(-4, units=30)
        assert pane.size() == normal, f"then 4 turns of 30 units down (a notch): {pane.size()}, not {normal}"
    finally:
        clean(win)


# --- ZM-2: over anything else the keys do nothing -----------------------------------------------------------------

PLACES = ["menu bar", "open menu", "toolbar", "splitter", "Properties field"]


@pytest.mark.invariant("ZM-2")
@pytest.mark.parametrize("place", PLACES)
def test_over_anything_else_the_keys_change_no_size(win, qtbot, tmp_path, place):
    """Z5: the pointer over the menu bar, an open menu, the toolbar, the splitter between the canvas and the Inspector,
    or a plain field of the Properties form (with the focus in it): Ctrl +, =, Shift+=, - and the number pad's change
    the size of no pane and of the field, and type nothing into the field."""
    fit(win)
    tab = open_zoom_flow(win, tmp_path)
    select(tab, "read")
    show_log(win, [("read", line) for line in LOG])
    (field,) = [f for f in win.properties.widget().findChildren(QLineEdit) if f.isVisible() and f.text() == "sales.csv"]
    panes = [text_pane("Log", win.log.text), list_pane("blocks tree", win.palette_panel.tree), canvas_pane(tab),
             Pane("Properties field", field, lambda: (QFontMetricsF(field.font()).height(), field.font().pointSizeF()))]
    opened = None
    try:
        before = {p.kind: p.size() for p in panes}
        focus_on(field if place == "Properties field" else win.log.text)
        if place == "menu bar":
            pointer_over(win.menuBar(), place)
        elif place == "open menu":
            opened = win.menuBar().actions()[0].menu()  # File
            opened.popup(win.menuBar().mapToGlobal(QPoint(10, win.menuBar().height())))
            settle()
            assert QApplication.activePopupWidget() is opened, "the menu did not open"
            pointer_over(opened, place)
        elif place == "toolbar":
            pointer_over(win.findChild(QToolBar, "run-toolbar"), place)
        elif place == "splitter":
            canvas, inspector = win.tabs, win.inspector
            left = canvas.mapToGlobal(QPoint(canvas.width(), canvas.height() // 2))
            right = inspector.mapToGlobal(QPoint(0, 0))
            assert right.x() - left.x() >= 2, "no room between the canvas and the Inspector"
            at = QPoint((left.x() + right.x()) // 2, left.y())
            pointer_at(at)
            under = QApplication.widgetAt(at)
            assert not any(p.widget is under or p.widget.isAncestorOf(under) for p in panes), (
                f"the pointer is over {type(under).__name__}, not over the splitter")
        else:
            pointer_over(field, place)
        for keys in ([IN], [EQUALS], [SHIFT_IN], [PAD_IN], [OUT], [PAD_OUT]):
            press(*keys)
            after = {p.kind: p.size() for p in panes}
            assert after == before, f"over the {place}, {keys} changed {[k for k in after if after[k] != before[k]]}"
        assert field.text() == "sales.csv" and not tab.doc.dirty
    finally:
        if opened is not None:
            opened.close()
        clean(win)


# --- ZM-4: a smallest and a largest size ---------------------------------------------------------------------------

def climb(pane, key) -> tuple:
    """Press `key` until the pane's size stays the same three presses running (at most 200 presses): that size."""
    sizes = [pane.size()]
    for _ in range(200):
        press(key)
        sizes.append(pane.size())
        if len(sizes) >= 4 and sizes[-1] == sizes[-2] == sizes[-3] == sizes[-4]:
            return sizes[-1]
    raise AssertionError(f"the {pane.kind} still changes after 200 presses of {key}: {sizes[-3:]}")


@pytest.mark.invariant("ZM-4")
@pytest.mark.parametrize("kind", KINDS)
def test_there_is_a_smallest_and_a_largest_size_and_at_either_end_nothing_more_happens(win, qtbot, tmp_path, kind):
    """Z6: Ctrl + makes the pane bigger up to a largest size, where Ctrl + (and Ctrl with the wheel turned up, over a
    text pane) does nothing more; Ctrl - smaller down to a smallest size, still showing its text, where Ctrl - (and
    the wheel turned down) does nothing more; from there Ctrl + makes it bigger again."""
    pane = ready(win, tmp_path, kind)
    try:
        normal = pane.size()
        largest = climb(pane, IN)
        assert bigger(largest, normal), f"Ctrl + over the {kind} never made it bigger than {normal}"
        if kind in TEXT_PANES:
            wheel(3)
            assert pane.size() == largest, f"Ctrl + wheel over the {kind} went past the largest size {largest}"
        smallest = climb(pane, OUT)
        assert bigger(normal, smallest), f"Ctrl - over the {kind} never made it smaller than {normal}"
        assert all(value > 0 for value in smallest), f"the {kind} at its smallest shows nothing: {smallest}"
        if kind in TEXT_PANES:
            wheel(-3)
            assert pane.size() == smallest, f"Ctrl + wheel over the {kind} went past the smallest size {smallest}"
        press(IN)
        assert bigger(pane.size(), smallest), f"at its smallest, Ctrl + over the {kind} does nothing"
    finally:
        clean(win)


@pytest.mark.invariant("ZM-4")
@pytest.mark.parametrize("end", ["largest", "smallest"])
def test_at_either_end_the_keys_and_the_wheel_do_not_scroll_the_docs(win, qtbot, tmp_path, end):
    """ZM-4: the docs at their largest size (or smallest), scrolled to the middle of a long paragraph, its top line half
    shown: Ctrl + (or Ctrl -) and Ctrl with the wheel turned up (or down) one notch over them change nothing, neither
    the size nor where the docs are scrolled to."""
    pane = ready(win, tmp_path, "docs")
    try:
        view = docs_page(win)
        key, notch, name = (IN, 1, "Ctrl +") if end == "largest" else (OUT, -1, "Ctrl -")
        at_end = climb(pane, key)
        paragraph = wrapped_paragraph(view, 3)
        line = paragraph.layout().lineAt(1)
        scroll_line_to_top(view, paragraph.position() + line.textStart())
        bar = view.verticalScrollBar()
        bar.setValue(bar.value() + round(line.height() / 2))
        settle()
        scrolled = bar.value()
        for how in (name, "Ctrl and the wheel"):
            if how == name:
                press(key)
            else:
                wheel(notch)
            assert pane.size() == at_end, f"at the {end} size, the {how} changed the docs' size to {pane.size()}"
            assert bar.value() == scrolled, (
                f"at the {end} size, the {how} scrolled the docs from {scrolled} to {bar.value()}")
    finally:
        clean(win)


@pytest.mark.invariant("ZM-1")
@pytest.mark.parametrize("kind", KINDS)
def test_after_many_steps_ctrl_0_gives_exactly_the_normal_size(win, qtbot, tmp_path, kind):
    """Z7: after many steps in and out (main keys, =, the number pad), Ctrl 0 gives exactly the normal size; after
    steps down, number pad 0 too."""
    pane = ready(win, tmp_path, kind)
    try:
        normal = pane.size()
        press(IN, IN, EQUALS, OUT, IN, PAD_IN, OUT, IN)
        assert bigger(pane.size(), normal), f"the steps left the {kind} at {pane.size()} (normal {normal})"
        press(NORMAL)
        assert pane.size() == normal, f"Ctrl 0 over the {kind} gave {pane.size()}, not its normal size {normal}"
        press(OUT, OUT, PAD_OUT)
        assert bigger(normal, pane.size()), f"Ctrl - left the {kind} at {pane.size()} (normal {normal})"
        press(PAD_NORMAL)
        assert pane.size() == normal, f"number pad 0 over the {kind} gave {pane.size()}, not {normal}"
    finally:
        clean(win)


# --- ZM-3: each kind keeps its size when the editor is opened again; the canvas does not ---------------------------

@pytest.mark.invariant("ZM-3")
@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_of_pane_has_its_size_when_the_editor_is_opened_again_the_canvas_does_not(win, qtbot, tmp_path,
                                                                                             kind):
    """Z8: a pane zoomed in, the editor closed and opened again on the same settings folder: a pane of that kind shows
    at the zoomed size; also when Taskloom is started again (a new process: only what the folder holds carries over).
    The canvas shows the flow at its normal zoom again."""
    pane = ready(win, tmp_path, kind)
    normal = pane.size()
    try:
        press(IN, IN)
        zoomed = pane.size()
        assert bigger(zoomed, normal), f"Ctrl + over the {kind} left it at {zoomed} (normal {normal})"
    finally:
        closed(win)
    expected, which = (normal, "its normal zoom") if kind == "canvas" else (zoomed, "the size it had")
    again = started_again(qtbot, win.home.root)
    try:
        fit(again)
        make_room(again, kind)
        shown = show_pane(again, tmp_path, kind).size()
        assert shown == expected, f"the editor opened again: the {kind} shows at {shown}, not at {which} {expected}"
    finally:
        closed(again)
    shown = size_after_a_restart(win.home.root, tmp_path, kind)
    assert shown == expected, f"Taskloom started again: the {kind} shows at {shown}, not at {which} {expected}"


# --- ZM-6: a code box built again has the remembered size ----------------------------------------------------------

@pytest.mark.invariant("ZM-6")
def test_a_code_box_built_again_for_another_block_or_another_theme_has_the_remembered_size(win, qtbot, tmp_path):
    """Z9: the Python code box zoomed in; another Python block selected, then the SQL and the HTML block, then the
    first again: each code box shows at that size; after Tools > Settings > Theme Dark, and selecting again, too."""
    from tests.test_theme import choose

    style, sheet, palette, tips = QApplication.style().name(), QApplication.instance().styleSheet(), \
        QApplication.palette(), QToolTip.palette()
    pane = ready(win, tmp_path, "Python code box")
    tab = win.current
    try:
        normal = pane.size()
        press(IN, IN)
        zoomed = pane.size()
        assert bigger(zoomed, normal), f"Ctrl + over the code box left it at {zoomed} (normal {normal})"
        for block in ("again", "sql", "html", "python"):
            select(tab, block)
            shown = text_pane(block, code_box(win)).size()
            assert shown == zoomed, f"the code box of {block}, built again, shows at {shown}, not at {zoomed}"
        choose(win, "Dark")
        settle()
        shown = text_pane("python", code_box(win)).size()
        assert shown == zoomed, f"after the theme changed, the code box shows at {shown}, not at {zoomed}"
        for block in ("sql", "python"):
            select(tab, block)
            shown = text_pane(block, code_box(win)).size()
            assert shown == zoomed, f"in the new theme, the code box of {block} shows at {shown}, not at {zoomed}"
    finally:
        clean(win)
        if QApplication.style().name() != style:
            QApplication.setStyle(style)
        if QApplication.instance().styleSheet() != sheet:
            QApplication.instance().setStyleSheet(sheet)
        QApplication.setPalette(palette)
        QToolTip.setPalette(tips)
        settle()


# --- ZM-5: zoom changes only the size -----------------------------------------------------------------------------

FIND_LINES = 120
NEEDLES = [i for i in range(42, FIND_LINES, 4)]  # one line in four from line 42: some always just out of view
TOP, SELECTED = 50, 51


def find_lines(wrapped: bool) -> list[str]:
    lines = []
    for i in range(FIND_LINES):
        line = f"needle_{i} = {i}" if i in NEEDLES else f"value_{i} = {i}"
        if wrapped and i % 7 == 3:
            line += "  # " + " ".join(f"word_{k}" for k in range(40))  # wraps in the box
        lines.append(line)
    return lines


def find_ready(win, tmp_path, kind: str):
    """The pane of `kind` showing FIND_LINES lines (the code box with Wrap lines ticked and long lines that wrap): its
    view, and the lines."""
    fit(win)
    if kind == "Python code box":
        lines = find_lines(wrapped=True)
        tab = win.open_flow(write_flow(tmp_path / "find.yaml", {"python": {
            "type": "logic.python", "config": {"inputs": [], "code": "\n".join(lines)}}}))
        settle()
        select(tab, "python")
        if not wrap_box(win).isChecked():
            wrap_box(win).click()
            settle()
        return code_box(win), lines
    lines = find_lines(wrapped=False)
    if kind == "Log":
        show_log(win, lines)
        return win.log.text, lines
    win.bottom_tabs.setCurrentWidget(win.preview)
    win.preview.show_outputs("report", {"previews": {"text": "\n".join(lines)}})
    settle()
    return win.preview.text, lines


def line_of(view, number: int, lines: list[str]):
    """The block of the view that shows line `number`."""
    block = view.document().begin()
    while block.isValid():
        if block.text().endswith(lines[number]):
            return block
        block = block.next()
    raise AssertionError(f"line {number} is not shown")


def scroll_to_top(view, block):
    """Scroll so that `block` is the top line in view."""
    bar = view.verticalScrollBar()
    if isinstance(view, QPlainTextEdit):
        bar.setValue(block.firstLineNumber())
    else:
        bar.setValue(int(view.document().documentLayout().blockBoundingRect(block).top()))
    settle()


def top_line(view) -> str:
    return view.cursorForPosition(QPoint(1, 1)).block().text()


def wholly_in_view(view, rect: QRect) -> bool:
    return view.viewport().rect().contains(rect)


def matches_in_view(view) -> list[tuple[int, int]]:
    text = view.document().toPlainText()
    return [(m.start(), len(m.group())) for m in re.finditer("needle", text)
            if wholly_in_view(view, where(view, m.start(), len(m.group())))]


def unmarked(view) -> list[tuple[int, int]]:
    """The matches wholly in view not painted highlighted."""
    settle()
    back = plain(view)
    return [m for m in matches_in_view(view) if not differs(behind(view, where(view, *m)), back)]


def selection(view) -> tuple:
    cursor = view.textCursor()
    return cursor.selectionStart(), cursor.selectionEnd(), cursor.selectedText()


@pytest.mark.invariant("ZM-5")
@pytest.mark.parametrize("how", ["keys", "wheel"])
@pytest.mark.parametrize("kind", ["Python code box", "Log", "Data preview text"])
def test_zoom_keeps_the_text_the_selection_the_top_line_and_the_find_highlights(win, qtbot, tmp_path, kind, how):
    """Z10: the find bar open on "needle" (focus in its field), a selection, line 50 at the top (in the code box with
    Wrap lines on): zoomed in (Ctrl + twice, or Ctrl and the wheel up two notches, the pointer over the pane), then
    out below the normal size, the text, the selection and the top line stay as they were, the find bar counts the
    same, and every match in view is highlighted, also those that came into view."""
    view, lines = find_ready(win, tmp_path, kind)
    try:
        pane = text_pane(kind, view)
        bar = open_find(win, qtbot, view)
        bar.type("needle")
        count = bar.count()
        assert count.endswith(f"/{len(NEEDLES)}"), f"the find bar counts {count!r}"
        start = line_of(view, SELECTED, lines).position()
        cursor = QTextCursor(view.document())
        cursor.setPosition(start)
        cursor.setPosition(start + 8, QTextCursor.KeepAnchor)
        view.setTextCursor(cursor)
        scroll_to_top(view, line_of(view, TOP, lines))
        text, selected, top = view.document().toPlainText(), selection(view), top_line(view)
        assert top.endswith(lines[TOP]), f"line {TOP} is not at the top: {top!r}"
        assert unmarked(view) == [], "the matches in view are not highlighted before zooming"
        normal, at_first = pane.size(), set(matches_in_view(view))
        assert bar.field.hasFocus()
        pointer_over(view, kind)
        for step, wheel_by, keys in (("in", 2, (IN, IN)), ("out", -5, (OUT,) * 5)):
            if how == "keys":
                press(*keys)
            else:
                wheel(wheel_by)
            size = pane.size()
            assert (bigger(size, normal) if step == "in" else bigger(normal, size)), (
                f"zoomed {step} with the {how} over the {kind}: {size} (normal {normal})")
            assert view.document().toPlainText() == text, f"zoomed {step}: the text changed"
            assert selection(view) == selected, f"zoomed {step}: the selection changed"
            assert top_line(view) == top, f"zoomed {step}: the top line is {top_line(view)!r}, not {top!r}"
            assert bar.count() == count, f"zoomed {step}: the find bar counts {bar.count()}, not {count}"
            assert unmarked(view) == [], f"zoomed {step}: matches in view not highlighted: {unmarked(view)}"
        assert set(matches_in_view(view)) - at_first, "zoomed out, no match came into view: nothing new was checked"
    finally:
        clean(win)


@pytest.mark.invariant("ZM-5")
def test_zoom_keeps_the_top_line_of_a_wrapped_paragraph_at_the_top(win, qtbot, tmp_path):
    """ZM-5: the docs scrolled so that the third line of a long paragraph, which the column wraps, is the top line.
    Each Ctrl + (twice) and Ctrl - (then below the normal size) over the docs keeps the line that was at the top at the
    top: the text it started with is on the top line, wrapped again at the new size."""
    pane = ready(win, tmp_path, "docs")
    try:
        view = docs_page(win)
        paragraph = wrapped_paragraph(view, 6)
        scroll_line_to_top(view, paragraph.position() + paragraph.layout().lineAt(2).textStart())
        for name, key in [("Ctrl +", IN)] * 2 + [("Ctrl -", OUT)] * 5:
            size, (start, text) = pane.size(), line_holding(view, top_of(view))
            press(key)
            assert pane.size() != size, f"{name} over the docs left them at {size}"
            assert top_of(view) == line_holding(view, start)[0], (
                f"{name} over the docs: the top line is {line_holding(view, top_of(view))[1]!r}; the line that was at "
                f"the top, {text!r}, now starts on {line_holding(view, start)[1]!r}")
    finally:
        clean(win)


@pytest.mark.invariant("ZM-5")
@pytest.mark.parametrize("kind", ["Python code box", "SQL code box"])
def test_a_zoomed_code_box_shows_each_line_number_whole_on_its_line(win, qtbot, tmp_path, kind):
    """Z11: a code box of 120 lines scrolled to its end (numbers of three digits), zoomed in three steps: each line in
    view has its own number on its row, and every number shows whole, inside the margin left of the code."""
    fit(win)
    language = CODE_BOXES[kind]
    code = "\n".join(f"-- step {k}" if language == "sql" else f"# step {k}" for k in range(1, 121))
    block = {"type": "logic.python", "config": {"inputs": [], "code": code}} if language == "python" else \
        {"type": "data.duckdb_sql", "config": {"sql": code}}
    tab = win.open_flow(write_flow(tmp_path / "numbers.yaml", {"work": block}))
    settle()
    try:
        select(tab, "work")
        box = code_box(win)
        pane = text_pane(kind, box)
        box.verticalScrollBar().setValue(box.verticalScrollBar().maximum())
        settle()
        normal = pane.size()
        focus_on(win.palette_panel.tree)
        pointer_over(box, kind)
        press(IN, IN, IN)
        assert bigger(pane.size(), normal), f"Ctrl + over the {kind} left it at {pane.size()} (normal {normal})"
        box.verticalScrollBar().setValue(box.verticalScrollBar().maximum())
        settle()
        starts = "-- step" if language == "sql" else "# step"
        shown = line_numbers(box, starts)
        assert f"{starts} 120" in shown, f"the last line is not in view: {sorted(shown)}"
        assert shown and shown == {line: [line.split()[-1]] for line in shown}, f"numbers left of the lines: {shown}"
        around = box.parentWidget()
        left = box.mapTo(around, QPoint(box.contentsRect().left(), 0)).x()
        top = box.mapTo(around, QPoint(0, 0)).y()
        numbers = [(x, n) for x, y, n in drawn_texts(around) if n.strip().isdigit() and top <= y <= top + box.height()]
        cut = [(n, round(x, 1)) for x, n in numbers if x < left - 0.5]
        assert numbers and not cut, f"numbers cut off on the left of the margin (it starts at {left}): {cut}"
    finally:
        clean(win)


@pytest.mark.invariant("ZM-5")
@pytest.mark.parametrize("kind", list(CODE_BOXES))
def test_a_tab_in_a_zoomed_code_box_is_as_wide_as_four_spaces(win, qtbot, tmp_path, kind):
    """Z11: in a code box, a line starting with a tab lines up with one starting with four spaces, also after zooming
    in three steps (tab stops follow the font)."""
    fit(win)
    language = CODE_BOXES[kind]
    code = "\tvalue = 1\n    value = 1\n" + "\n".join(f"line_{i} = {i}" for i in range(10))
    config = {"python": ("logic.python", {"inputs": [], "code": code}), "sql": ("data.duckdb_sql", {"sql": code}),
              "html": ("reports.html_template", {"template": code})}[language]
    tab = win.open_flow(write_flow(tmp_path / "tabs.yaml", {"work": {"type": config[0], "config": config[1]}}))
    settle()
    try:
        select(tab, "work")
        box = code_box(win)
        pane = text_pane(kind, box)

        def offsets() -> tuple[int, int, float]:
            text = box.toPlainText()
            tabbed, spaced = rect_at(box, text.index("\tvalue") + 1), rect_at(box, text.index("    value") + 4)
            line = box.document().findBlockByNumber(2)
            char = (rect_at(box, line.position() + 8).left() - rect_at(box, line.position()).left()) / 8
            return tabbed.left(), spaced.left(), char

        tabbed, spaced, char = offsets()
        assert abs(tabbed - spaced) <= max(1.5, char / 4), f"unzoomed, a tab ends at {tabbed}, 4 spaces at {spaced}"
        normal = pane.size()
        focus_on(win.palette_panel.tree)
        pointer_over(box, kind)
        press(IN, IN, IN)
        assert bigger(pane.size(), normal), f"Ctrl + over the {kind} left it at {pane.size()} (normal {normal})"
        tabbed, spaced, char = offsets()
        assert abs(tabbed - spaced) <= max(1.5, char / 4), (
            f"zoomed in, a tab ends at x {tabbed} and four spaces at x {spaced} (a character is {char:.1f} wide)")
    finally:
        clean(win)


LONG = "\n".join(f"row {i}" for i in range(250))  # over the Log's 200 lines: a note "… more lines" under it


@pytest.mark.invariant("ZM-5")
@pytest.mark.parametrize("how", ["keys", "wheel"])
def test_the_logs_lines_under_a_message_stay_under_its_block_column_when_zoomed(win, qtbot, tmp_path, how):
    """The Log writes what is under a line (here the note under a long message: "… more lines … show all") under its
    [block] column: zoomed in (Ctrl + twice, or Ctrl and the wheel), it still starts where the [block] column starts,
    on the lines written before the zoom and on a line written after it."""
    fit(win)
    entries = [{"ts": "2026-10-31T09:00:00.000", "block": "report", "level": "INFO", "message": "short line"},
               {"ts": "2026-10-31T09:00:01.000", "block": "report", "level": "INFO", "message": LONG}]
    win.bottom_tabs.setCurrentWidget(win.log)
    win.log.set_entries(entries)
    settle()
    view = win.log.text
    pane = text_pane("Log", view)

    def columns() -> list[tuple[int, int]]:
        """(where [report] starts, where the note under it starts) for each long message in the Log."""
        text, found = view.document().toPlainText(), []
        for m in re.finditer(r"\[report\] INFO row 0", text):
            note = text.index("…", m.start())
            found.append((rect_at(view, m.start()).left(), rect_at(view, note).left()))
        return found

    try:
        assert all(a == b for a, b in columns()) and columns(), f"before zooming: {columns()}"
        normal = pane.size()
        focus_on(win.palette_panel.tree)
        pointer_over(view, "Log")
        if how == "keys":
            press(IN, IN)
        else:
            wheel(2)
        assert bigger(pane.size(), normal), f"zooming with the {how} over the Log left it at {pane.size()} ({normal})"
        assert all(a == b for a, b in columns()), f"zoomed: [report] starts at x, the note at x: {columns()}"
        win.log.append({"ts": "2026-10-31T09:00:02.000", "block": "report", "level": "INFO", "message": LONG})
        settle()
        assert len(columns()) == 2 and all(a == b for a, b in columns()), (
            f"zoomed, a message written after: [report] starts at x, the note at x: {columns()}")
    finally:
        clean(win)


@pytest.mark.invariant("ZM-5")
@pytest.mark.parametrize("kind", LISTS)
def test_the_rows_of_a_zoomed_list_or_table_are_tall_enough_for_their_text(win, qtbot, tmp_path, kind):
    """Zoomed in four steps, each item of a list or table in view is at least as tall as its text in the font it is
    drawn in: the text shows whole, not cut."""
    pane = ready(win, tmp_path, kind)
    try:
        normal = pane.size()
        press(IN, IN, IN, IN)
        assert bigger(pane.size(), normal), f"Ctrl + over the {kind} left it at {pane.size()} (normal {normal})"
        view = pane.widget
        index = first_item(view)
        rect = view.visualRect(index)
        needed = QFontMetricsF(drawn_font(view, index)).height()
        assert rect.height() >= needed, f"the {kind}'s first item is {rect.height()} px high, its text {needed:.1f}"
    finally:
        clean(win)


def picture(folder: Path) -> Path:
    image = QImage(160, 90, QImage.Format_RGB32)
    image.fill(QColor("#3a7bd5"))
    path = folder / "chart.png"
    assert image.save(str(path))
    return path


@pytest.mark.invariant("ZM-5")
def test_pictures_in_the_log_keep_their_size_when_the_log_is_zoomed(win, qtbot, tmp_path):
    """ZM-5: a picture in the Log (as Inspect shows a chart) keeps the size it shows at when the Log is zoomed in and
    out; its text zooms."""
    fit(win)
    path = picture(tmp_path)
    win.bottom_tabs.setCurrentWidget(win.log)
    win.log.set_entries([
        {"ts": "2026-10-31T09:00:00.000", "block": "look", "level": "INFO", "message": "the chart of the day"},
        {"ts": "2026-10-31T09:00:01.000", "block": "look", "level": "INFO", "message": str(path), "show": "image"}])
    settle()
    view = win.log.text
    pane = text_pane("Log", view)

    def shown() -> tuple[int, int]:
        at = view.document().toPlainText().index("￼")
        return rect_at(view, at + 1).left() - rect_at(view, at).left(), rect_at(view, at).height()

    try:
        normal, size = pane.size(), shown()
        assert size[0] > 100, f"the picture shows {size[0]} px wide"
        focus_on(win.palette_panel.tree)
        pointer_over(view, "Log")
        press(IN, IN)
        assert bigger(pane.size(), normal), f"Ctrl + over the Log left it at {pane.size()} (normal {normal})"
        assert shown() == size, f"zoomed in, the picture shows at {shown()}, not {size}"
        press(OUT, OUT, OUT, OUT)
        assert bigger(normal, pane.size()), f"Ctrl - over the Log left it at {pane.size()} (normal {normal})"
        assert shown() == size, f"zoomed out, the picture shows at {shown()}, not {size}"
    finally:
        clean(win)


def paragraphs(page: str) -> list[str]:
    """The page's paragraphs and list items, as text."""
    parts = re.split(r"</?(?:p|li|h\d|tr|div|blockquote)\b[^>]*>", page)
    return [" ".join(re.sub(r"<[^>]*>", " ", part).split()) for part in parts if part.strip()]


@pytest.mark.invariant("ZM-5")
def test_the_docs_say_that_pictures_and_mermaid_drawings_in_the_log_keep_their_size(win):
    """ZM-5: Help > Documentation says, where it tells about zooming, that pictures and Mermaid drawings in the Log keep
    their size."""
    import html

    found = [text for page in win.docs.browser.pages().values() for text in map(html.unescape, paragraphs(page))
             if re.search(r"zoom", text, re.I) and re.search(r"picture|image", text, re.I)
             and re.search(r"Mermaid", text) and re.search(r"\bLog\b", text)
             and re.search(r"keep|stay|same size|not (?:zoom|grow|change)", text, re.I)]
    assert found, "no paragraph of the docs says that the Log's pictures and Mermaid drawings keep their size on zoom"


# --- ZM-1: a box in a modal dialog --------------------------------------------------------------------------------

@pytest.mark.invariant("ZM-1")
def test_a_text_box_of_a_modal_dialog_zooms_under_the_pointer(win, qtbot, tmp_path):
    """Z12: in Results of this block (a modal dialog), the pointer over the box of the run's values and the focus in
    its list of runs: Ctrl + makes the box bigger, Ctrl 0 normal."""
    fit(win)
    flow = write_flow(tmp_path / "values.yaml", {
        "manual": {"type": "trigger.manual"},
        "report": {"type": "logic.python", "config": {"inputs": [], "outputs": ["output"],
                                                      "code": "output = 'the totals of each region'"}}},
        ["manual.start -> report.after"])
    tab = run_in_editor(win, qtbot, flow)
    tries, done = [], []

    def act():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            if len(tries) < 250:  # (about 5 s)
                tries.append(1)
                QTimer.singleShot(20, win, act)
            return
        try:
            assert isinstance(dialog, dialogs.BlockResultsDialog), type(dialog).__name__
            pane = text_pane("values box", dialog.values)
            focus_on(dialog.table)
            normal = pane.size()
            pointer_over(dialog.values, "values box")
            press(IN)
            grown = pane.size()
            press(NORMAL)
            done.append((normal, grown, pane.size(), dialog.table.hasFocus()))
        except Exception as e:  # (raised again below, in the test)
            done.append(e)
        finally:
            dialog.reject()

    QTimer.singleShot(0, win, act)
    (results,) = [a for a in win._block_menu_for(tab, "report").actions() if a.text() == "Results of this block…"]
    results.trigger()  # shows the dialog and waits until it is answered
    settle()
    assert done, "Results of this block showed no modal dialog"
    if isinstance(done[0], Exception):
        raise done[0]
    normal, grown, back, focus_kept = done[0]
    assert bigger(grown, normal), f"Ctrl + over the values box left it at {grown} (normal {normal})"
    assert back == normal, f"Ctrl 0 over the values box gave {back}, not {normal}"
    assert focus_kept, "the focus left the list of runs"


# --- the canvas: the wheel as before; Ctrl + per flow, until the flow is closed --------------------------------------

def second_flow(folder: Path) -> Path:
    return write_flow(folder / "other.yaml", {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}}})


@pytest.mark.invariant("ZM-1")
def test_the_plain_wheel_over_the_canvas_zooms_it_as_before(win, qtbot, tmp_path):
    """Z13: the wheel with no key held over the canvas zooms it as before: up one notch 1.15 times, down one notch back,
    down again 1/1.15 of the normal zoom."""
    pane = ready(win, tmp_path, "canvas")
    try:
        wheel(1, Qt.NoModifier)
        assert pane.size() == (round(115, 3),), pane.size()
        wheel(-1, Qt.NoModifier)
        assert pane.size() == (100,), pane.size()
        wheel(-1, Qt.NoModifier)
        assert pane.size() == (round(100 / 1.15, 3),), pane.size()
    finally:
        clean(win)


@pytest.mark.invariant("ZM-1")
@pytest.mark.invariant("ZM-3")
def test_ctrl_plus_over_the_canvas_zooms_that_flow_only_until_it_is_closed(win, qtbot, tmp_path):
    """Z13: Ctrl + over the canvas of one flow zooms it; another flow's canvas keeps its zoom; back on the first, its
    zoom is as it was; closed and opened again, the flow shows at its normal zoom, also after the editor is opened
    again."""
    pane = ready(win, tmp_path, "canvas")
    first = win.current
    try:
        press(IN, IN)
        zoomed = pane.size()
        assert bigger(zoomed, (100,)), f"Ctrl + over the canvas left it at {zoomed}"
        other = win.open_flow(second_flow(tmp_path))
        settle()
        assert canvas_pane(other).size() == (100,), "the other flow opened zoomed"
        pointer_over(other.view, "other canvas")
        press(OUT)
        other_zoom = canvas_pane(other).size()
        assert bigger((100,), other_zoom), f"Ctrl - over the other canvas left it at {other_zoom}"
        win.tabs.setCurrentWidget(first)
        settle()
        assert canvas_pane(first).size() == zoomed, f"back on the first flow: {canvas_pane(first).size()}, not {zoomed}"
        win.tabs.setCurrentWidget(other)
        settle()
        assert canvas_pane(other).size() == other_zoom
        clean(win)
        assert win.close_tab(first)
        settle()
        reopened = open_zoom_flow(win, tmp_path)
        assert canvas_pane(reopened).size() == (100,), f"closed and opened again: {canvas_pane(reopened).size()}"
    finally:
        closed(win)
    again = started_again(qtbot, win.home.root)
    try:
        fit(again)
        make_room(again, "canvas")
        for path in (zoom_flow(tmp_path), second_flow(tmp_path)):
            tab = again.open_flow(path)
            settle()
            shown = canvas_pane(tab).size()
            assert shown == (100,), f"{path.name} in the editor opened again: {shown}"
    finally:
        closed(again)
