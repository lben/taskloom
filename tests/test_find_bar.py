"""Ctrl+F (Cmd+F on macOS: QKeySequence.Find) opens a JetBrains-style find bar in the Log, Data preview, Run history and
every code box: at the bottom of the panel (under a code box), the focus in its search field, the text selected there
put in it; left to right the field, the count ("3/12", "0 results", nothing while the field is empty), an up arrow
(previous), a down arrow (next), a ".*" toggle (regular expressions, on when the editor starts) and an "Aa" toggle
(match case, off when the editor starts), each with a tooltip, shared by every find bar while the editor runs, and a
close button. Typing searches at once; every match is highlighted, the current one more strongly, and scrolled into
view; Enter or the down arrow goes to the next match, Shift+Enter or the up arrow to the previous one, wrapping round;
Esc or the close button closes it, removes the highlights and gives the focus back. An invalid regular expression is
marked red and says so, with no stale highlights; with regex off the text is searched as typed. The Log searches the
lines it shows (its block filter too) and new lines update the count; Data preview the cells and headers of the table
on screen; Run history every column; code boxes their code, never changing it. Light and dark both read well; the
README says it. Ctrl+F anywhere else does nothing new. Each test names the point of the specification (1-7) it
protects.

Driven through the real editor (the `win` fixture): flows run from it, runs recorded in history.db and listed by the
editor itself, blocks selected on the canvas, keys pressed where the focus is; and, for the next start, `taskloom
editor` in a process of its own. What is highlighted is read from what the editor paints."""

import json
import os
import re
import subprocess
import sys
import textwrap
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeySequence, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QLineEdit, QToolTip  # noqa: E402

from taskloom.editor import theme  # noqa: E402
from taskloom.history import History  # noqa: E402
from tests.test_code_editor import clean, code_box, open_blocks, select  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REGEX, CASE = ".*", "Aa"
COUNT = re.compile(r"\d+/\d+|0 results")
TEXT, MARK = 4.5, 3.0  # the contrast text needs (WCAG), and a mark that is not text (WCAG's bar for graphics)


# --- the editor ----------------------------------------------------------------------------------------------------

def settle(rounds: int = 5):
    for _ in range(rounds):
        QApplication.processEvents()


def focus(win, qtbot, widget):
    """As when you click into `widget`: the keyboard focus is in it."""
    assert widget.isVisible(), f"{type(widget).__name__} does not show"
    qtbot.waitExposed(win)
    win.activateWindow()
    widget.setFocus()
    qtbot.waitUntil(widget.hasFocus, timeout=5_000)


def press_find(widget):
    """Ctrl+F (Cmd+F on macOS), pressed where the focus is (`widget`)."""
    QTest.keySequence(widget, QKeySequence(QKeySequence.Find))  # (made once the application is)
    settle()


def shown_toggles(win) -> list[QAbstractButton]:
    """Every ".*" toggle showing in the editor: one per find bar open."""
    return [b for b in win.findChildren(QAbstractButton) if b.isVisible() and b.text().replace("&", "") == REGEX]


class Bar:
    """The find bar whose search field has the keyboard focus."""

    def __init__(self, field: QLineEdit):
        self.field = field
        widget = field.parentWidget()
        while widget is not None and not any(b.text().replace("&", "") == REGEX
                                             for b in widget.findChildren(QAbstractButton)):
            widget = widget.parentWidget()
        assert widget is not None, "the search field has no .* toggle next to it"
        self.widget = widget

    def shown(self) -> bool:
        try:
            return self.widget.isVisible()
        except RuntimeError:  # (closing it may delete it)
            return False

    def buttons(self) -> list[QAbstractButton]:
        """Its buttons showing, left to right."""
        shown = [b for b in self.widget.findChildren(QAbstractButton) if b.isVisible()]
        return sorted(shown, key=lambda b: b.mapTo(self.widget, QPoint(0, 0)).x())

    def _toggle(self, text: str) -> QAbstractButton:
        found = [b for b in self.buttons() if b.text().replace("&", "") == text]
        assert len(found) == 1, f"the find bar has {len(found)} {text!r} toggles"
        return found[0]

    @property
    def regex(self) -> QAbstractButton:
        return self._toggle(REGEX)

    @property
    def case(self) -> QAbstractButton:
        return self._toggle(CASE)

    def others(self) -> list[QAbstractButton]:
        """The arrows and the close button, left to right (not a clear button inside the field)."""
        return [b for b in self.buttons() if b.text().replace("&", "") not in (REGEX, CASE)
                and not self.field.isAncestorOf(b)]

    @property
    def up(self) -> QAbstractButton:
        return self.others()[0]

    @property
    def down(self) -> QAbstractButton:
        return self.others()[1]

    @property
    def close(self) -> QAbstractButton:
        return self.others()[-1]

    def count_label(self) -> QLabel | None:
        found = [label for label in self.widget.findChildren(QLabel) if label.isVisible()
                 and COUNT.fullmatch(re.sub(r"\s*/\s*", "/", " ".join(label.text().split())))]
        assert len(found) <= 1, [label.text() for label in found]
        return found[0] if found else None

    def count(self) -> str:
        """What its count shows: "3/12", "0 results", or "" (nothing)."""
        label = self.count_label()
        return re.sub(r"\s*/\s*", "/", " ".join(label.text().split())) if label else ""

    def current(self) -> int:
        """Which match is the current one (from 0), as the count says; and asserts how many there are."""
        shown = self.count()
        assert re.fullmatch(r"\d+/\d+", shown), f"the count shows {shown!r}"
        return int(shown.split("/")[0]) - 1

    def total(self) -> int:
        shown = self.count()
        assert re.fullmatch(r"\d+/\d+", shown), f"the count shows {shown!r}"
        return int(shown.split("/")[1])

    def type(self, text: str):
        """Type `text` over what the field holds."""
        self.field.selectAll()
        if text:
            QTest.keyClicks(self.field, text)
        else:
            QTest.keyClick(self.field, Qt.Key_Backspace)
        settle()

    def key(self, key, modifier=Qt.NoModifier):
        QTest.keyClick(self.field, key, modifier)
        settle()

    def click(self, button: QAbstractButton):
        settle()
        QTest.mouseClick(button, Qt.LeftButton)
        settle()

    def set_toggles(self, regex: bool, case: bool):
        for button, on in ((self.regex, regex), (self.case, case)):
            if button.isChecked() != on:
                self.click(button)


def open_find(win, qtbot, widget) -> Bar:
    """Click into `widget`, press Ctrl+F: the find bar it opens, the focus in its search field."""
    focus(win, qtbot, widget)
    press_find(widget)
    field = QApplication.focusWidget()
    assert isinstance(field, QLineEdit), (f"Ctrl+F in {type(widget).__name__} gave the focus to "
                                          f"{type(field).__name__ if field else None}, not to a search field")
    bar = Bar(field)
    assert bar.shown(), "no find bar shows"
    return bar


def closed_and_back(bar: Bar, view):
    assert not bar.shown(), "the find bar still shows"
    assert view.hasFocus(), f"the focus is in {type(QApplication.focusWidget()).__name__}, not back in the panel"


def assert_under(bar: Bar, view, panel):
    """The bar is at the bottom of `panel`, under `view` (the panel's text or table)."""
    assert panel.isAncestorOf(bar.widget), "the find bar is not in the panel"
    top = bar.widget.mapTo(panel, QPoint(0, 0)).y()
    assert top >= view.mapTo(panel, QPoint(0, view.height())).y() - 1, "the find bar is not under the panel's view"
    assert top + bar.widget.height() <= panel.height() + 1, "the find bar is not inside the panel"


# --- what is painted -----------------------------------------------------------------------------------------------

def most_common(image) -> QColor:
    counts = Counter(image.pixel(x, y) for y in range(image.height()) for x in range(image.width()))
    return QColor.fromRgb(counts.most_common(1)[0][0])


def differs(a: QColor, b: QColor, by: int = 32) -> bool:
    """Visibly another colour: one of red, green and blue at least `by` (of 255) apart."""
    return max(abs(x - y) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3])) >= by


def contrast(a: QColor, b: QColor) -> float:
    """The WCAG contrast ratio of two colours (1 to 21)."""
    def luminance(c):
        def channel(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def area(view, rect: QRect):
    """What the view's viewport paints in `rect` (its edges left out), where the viewport shows it."""
    return view.viewport().grab(rect.adjusted(1, 1, -1, -1).intersected(view.viewport().rect())).toImage()


def behind(view, rect: QRect) -> QColor:
    """The colour behind the text in `rect` of the view: what most of it is painted in."""
    return most_common(area(view, rect))


def text_on(view, rect: QRect) -> float:
    """The contrast of the text in `rect` with what is behind it, at the text's strongest."""
    image, back = area(view, rect), behind(view, rect)
    return max(contrast(image.pixelColor(x, y), back) for y in range(image.height()) for x in range(image.width()))


def plain(view) -> QColor:
    """The view's own background: what most of its viewport is painted in."""
    return most_common(view.viewport().grab().toImage())


def in_view(view, rect: QRect) -> bool:
    return view.viewport().rect().intersects(rect)


def where(view, start: int, length: int) -> QRect:
    """Where a text view (the Log, a code box) shows the characters start..start+length (on one line)."""
    first, last = QTextCursor(view.document()), QTextCursor(view.document())
    first.setPosition(start)
    last.setPosition(start + length)
    a, b = view.cursorRect(first), view.cursorRect(last)
    return QRect(QPoint(a.left(), a.top()), QPoint(b.left() - 1, a.bottom()))


def text_matches(view, pattern: str, flags=re.IGNORECASE) -> list[tuple[int, int]]:
    """(start, length) of each match of `pattern` in the text the view shows, in order."""
    return [(m.start(), len(m.group())) for m in re.finditer(pattern, view.document().toPlainText(), flags)]


def check_text(view, matches, current: int) -> int:
    """The current match is scrolled into view; every match in view is highlighted, the current one otherwise than the
    others. How many other matches were in view with it."""
    settle()
    back, rects = plain(view), [where(view, start, length) for start, length in matches]
    assert in_view(view, rects[current]), f"the current match ({current + 1}) is not scrolled into view"
    shades = {i: behind(view, r) for i, r in enumerate(rects) if in_view(view, r)}
    for i, shade in shades.items():
        assert differs(shade, back), f"match {i + 1} is not highlighted ({shade.name()} on {back.name()})"
    others = [i for i in shades if i != current]
    for i in others:
        assert differs(shades[i], shades[current], 24), (
            f"the current match is highlighted as the others ({shades[current].name()}, {shades[i].name()})")
    return len(others)


def unhighlighted(view, matches, but: int | None = None) -> list[int]:
    """The matches (from 1; `but` left out) still highlighted: painted otherwise than the view's background."""
    settle()
    back = plain(view)
    return [i + 1 for i, (start, length) in enumerate(matches) if i != but
            and in_view(view, where(view, start, length)) and differs(behind(view, where(view, start, length)), back)]


def cell_matches(view, pattern: str) -> list:
    """The cells (model indexes) of the table view whose text matches `pattern`, row by row."""
    model = view.model()
    return [model.index(r, c) for r in range(model.rowCount()) for c in range(model.columnCount())
            if re.search(pattern, str(model.data(model.index(r, c)) or ""), re.IGNORECASE)]


def check_cells(view, matches, current: int) -> int:
    """The current match's cell is selected and scrolled to; every match in view is highlighted (otherwise than a cell
    of its column that does not match), the current one otherwise than the others. How many other matches were in view
    with it."""
    settle()
    index = matches[current]
    assert view.selectionModel().isSelected(index), f"the current match's cell (row {index.row() + 1}) is not selected"
    assert in_view(view, view.visualRect(index)), f"the current match's cell (row {index.row() + 1}) is not in view"
    model, seen = view.model(), 0
    for i, other in enumerate(matches):
        if i == current or not in_view(view, view.visualRect(other)):
            continue
        plain_cells = [model.index(r, other.column()) for r in range(model.rowCount())]
        plain_cells = [c for c in plain_cells if c not in matches and not view.selectionModel().isSelected(c)
                       and in_view(view, view.visualRect(c))]
        if not plain_cells:
            continue
        shade, cell = behind(view, view.visualRect(other)), behind(view, view.visualRect(plain_cells[0]))
        assert differs(shade, cell), f"the match in row {other.row() + 1} is not highlighted ({shade.name()})"
        assert differs(shade, behind(view, view.visualRect(index)), 24), "the current match looks as the others"
        seen += 1
    return seen


def is_red(c: QColor) -> bool:
    hue = c.hsvHue()
    return c.alpha() == 255 and (0 <= hue <= 15 or hue >= 345) and c.hsvSaturation() >= 30 and c.value() >= 60


def red_pixels(widget) -> int:
    image = widget.grab().toImage()
    return sum(is_red(image.pixelColor(x, y)) for y in range(image.height()) for x in range(image.width()))


def says_invalid(bar: Bar) -> bool:
    """The field's tooltip, or a short message on the bar, says the pattern is not valid."""
    texts = [bar.field.toolTip(), bar.widget.toolTip()]
    texts += [label.text() for label in bar.widget.findChildren(QLabel) if label.isVisible()]
    return any(re.search(r"invalid|not (a )?valid", t, re.IGNORECASE) for t in texts)


# --- flows, the Log and Run history --------------------------------------------------------------------------------

def write_flow(path: Path, blocks: dict, edges=()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": list(edges)}, sort_keys=False), encoding="utf-8")
    return path


def python_flow(path: Path, code: str) -> Path:
    """Manual Start `manual` runs the Python Code block `report`."""
    return write_flow(path, {"manual": {"type": "trigger.manual"},
                             "report": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": code}}},
                      ["manual.start -> report.after"])


def run_in_editor(win, qtbot, flow: Path):
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: bool(tab.runs) and not tab.running and tab.last is not None, timeout=90_000)
    assert tab.last["status"] == "success", tab.last
    return tab


def show_log(win, lines):
    """The Log showing these lines: each a message, or (block, message)."""
    entries = []
    for i, line in enumerate(lines):
        block, message = line if isinstance(line, tuple) else ("report", line)
        entries.append({"ts": f"2026-10-31T09:{i // 60 % 60:02d}:{i % 60:02d}.000", "block": block, "level": "INFO",
                        "message": message})
    win.bottom_tabs.setCurrentWidget(win.log)
    win.log.set_entries(entries)
    settle()


def haystack(size: int, needles: dict[int, str]) -> list[str]:
    """`size` lines of hay, line i being needles[i] where it says."""
    return [needles.get(i, f"line {i} hay") for i in range(size)]


START = datetime(2026, 10, 1, 0, 0, 0)


def started(run_id: int) -> datetime:
    return START + timedelta(hours=run_id - 1)


def record_runs(win, flow_path: str, runs: list[dict]):
    """Finished runs of the flow file, in history.db as runs record them (run i started i - 1 hours after START)."""
    history = History(win.home.history_db)
    try:
        for run in runs:
            begun = started(len(history.runs()) + 1)
            run_id = history.start_run("nightly", flow_path, run.get("params", {}), begun.isoformat(),
                                       started_by=run.get("by", "manual"))
            history({"event": "run_finished", "run_id": run_id, "status": run.get("status", "success"),
                     "ts": (begun + timedelta(seconds=run.get("seconds", 1.0))).isoformat()})
    finally:
        history.close()


def listed_runs(win, qtbot, tmp_path, runs: list[dict]):
    """A flow open in the editor, with these runs in its Run history (listed by the editor from history.db)."""
    tab = win.open_flow(write_flow(tmp_path / "nightly.yaml", {"manual": {"type": "trigger.manual"},
                                                                "check": {"type": "logic.wait",
                                                                          "config": {"seconds": 0}}},
                                   ["manual.start -> check.after"]))
    record_runs(win, str(tab.doc.path), runs)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == len(runs), timeout=15_000)
    win.bottom_tabs.setCurrentWidget(win.history_panel)  # (new runs show their messages in the Log)
    settle()
    return tab, table


# --- 1, 2: Ctrl+F in the Log: the user's case ----------------------------------------------------------------------

def test_the_users_case_ctrl_f_in_the_log_after_a_run_counts_highlights_and_steps_through_the_matches(win, qtbot,
                                                                                                       tmp_path):
    """Spec 1, 2, 3, 5 (the user's case): after a run, with the focus in the Log, Ctrl+F opens the find bar at the
    bottom of the Log with the focus in its field, regex on and match case off. Typing "needle" counts the matches at
    once ("k/3"), highlights every one, the current one more strongly, scrolled into view; Enter and the down arrow go
    to the next, Shift+Enter and the up arrow to the previous one, wrapping round at the ends."""
    code = "for i in range(150):\n    print('line', i, 'needle' if i % 50 == 25 else 'hay')"
    run_in_editor(win, qtbot, python_flow(tmp_path / "report.yaml", code))
    win.bottom_tabs.setCurrentWidget(win.log)
    settle()
    view = win.log.text

    bar = open_find(win, qtbot, view)
    assert_under(bar, view, win.log)
    assert bar.field.text() == "" and bar.count() == ""
    assert bar.regex.isChecked() and not bar.case.isChecked()

    bar.type("NEEDLE")  # match case is off
    matches = text_matches(view, "needle")
    assert len(matches) == 3, view.document().toPlainText()
    assert bar.total() == 3
    k = bar.current()
    check_text(view, matches, k)
    for press in (lambda: bar.key(Qt.Key_Return), lambda: bar.click(bar.down), lambda: bar.key(Qt.Key_Return)):
        press()
        k = (k + 1) % 3
        assert bar.current() == k, f"next went to {bar.count()}, not {k + 1}/3"
        check_text(view, matches, k)
    for press in (lambda: bar.key(Qt.Key_Return, Qt.ShiftModifier), lambda: bar.click(bar.up),
                  lambda: bar.key(Qt.Key_Return, Qt.ShiftModifier), lambda: bar.click(bar.up)):
        press()
        k = (k - 1) % 3
        assert bar.current() == k, f"previous went to {bar.count()}, not {k + 1}/3"
        check_text(view, matches, k)


def test_the_bar_shows_its_parts_left_to_right_regex_on_match_case_off_and_says_what_each_toggle_does(win, qtbot):
    """Spec 2: left to right the search field, the count, an up arrow, a down arrow, ".*" (on when the editor starts),
    "Aa" (off), a close button; each toggle's tooltip says what it does. The count shows nothing while the field is
    empty (also once emptied again, its highlights gone) and "0 results" when nothing matches."""
    show_log(win, haystack(40, {5: "line 5 needle", 6: "line 6 needle"}))
    view = win.log.text
    bar = open_find(win, qtbot, view)

    assert bar.regex.isCheckable() and bar.regex.isChecked(), "regular expressions are not on"
    assert bar.case.isCheckable() and not bar.case.isChecked(), "match case is not off"
    assert re.search(r"regular expression|regex", bar.regex.toolTip(), re.IGNORECASE), bar.regex.toolTip()
    assert re.search(r"case", bar.case.toolTip(), re.IGNORECASE), bar.case.toolTip()
    assert bar.count() == ""

    bar.type("needle")
    assert bar.total() == 2
    assert len(bar.others()) == 3, "not just an up arrow, a down arrow and a close button besides the toggles"
    parts = [bar.field, bar.count_label(), bar.up, bar.down, bar.regex, bar.case, bar.close]
    lefts = [part.mapTo(bar.widget, QPoint(0, 0)).x() for part in parts]
    assert lefts == sorted(lefts) and len(set(lefts)) == len(lefts), f"not left to right: {lefts}"

    bar.type("nothing like this")
    assert bar.count() == "0 results"
    bar.type("needle")
    k = bar.current()
    bar.type("")
    assert bar.count() == ""
    assert unhighlighted(view, text_matches(view, "needle"), but=k) == [], "highlights stay with the field empty"


def test_text_selected_in_the_log_or_a_code_box_is_put_in_the_search_field(win, qtbot, tmp_path):
    """Spec 1: text selected where Ctrl+F is pressed is put in the search field, and searched at once."""
    show_log(win, haystack(20, {3: "line 3 needle", 9: "line 9 needle"}))
    view = win.log.text
    start, length = text_matches(view, "needle")[1]
    cursor = view.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(start + length, QTextCursor.KeepAnchor)
    view.setTextCursor(cursor)
    bar = open_find(win, qtbot, view)
    assert bar.field.text() == "needle"
    assert bar.total() == 2
    bar.key(Qt.Key_Escape)

    tab = open_blocks(win, tmp_path / "code.yaml", {"load": {"type": "logic.python", "config": {
        "inputs": [], "code": "total = 1\nsubtotal = total + 2\nprint(total)"}}})
    try:
        select(tab, "load")
        box = code_box(win)
        cursor = box.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(len("total"), QTextCursor.KeepAnchor)
        box.setTextCursor(cursor)
        bar = open_find(win, qtbot, box)
        assert bar.field.text() == "total"
        assert bar.total() == 4
    finally:
        clean(win)


def test_ctrl_f_anywhere_else_opens_no_find_bar(win, qtbot, tmp_path):
    """Spec 1 and the non-goals (no find on the canvas): Ctrl+F on the canvas or in a field of Properties opens no find
    bar and leaves the focus, the field and the flow as they were."""
    tab = open_blocks(win, tmp_path / "other.yaml", {"load": {"type": "logic.python", "config": {
        "inputs": ["sales"], "code": "output = sales"}}})
    try:
        focus(win, qtbot, tab.view)
        press_find(tab.view)
        assert shown_toggles(win) == [], "Ctrl+F on the canvas opened a find bar"
        assert tab.view.hasFocus()

        select(tab, "load")
        (field,) = [f for f in win.properties.widget().findChildren(QLineEdit) if f.isVisible() and f.text() == "sales"]
        focus(win, qtbot, field)
        press_find(field)
        assert shown_toggles(win) == [], "Ctrl+F in a field of Properties opened a find bar"
        assert field.hasFocus() and field.text() == "sales"
        assert not tab.doc.dirty
    finally:
        clean(win)


# --- 3, 4: closing, invalid patterns, regex off, match case --------------------------------------------------------

def test_esc_and_the_close_button_close_the_bar_remove_the_highlights_and_give_the_focus_back(win, qtbot):
    """Spec 3: Esc closes the bar, removes the highlights and gives the focus back to the Log; so does the close
    button. (The current match may stay selected, as in JetBrains' editors.)"""
    show_log(win, haystack(12, {2: "line 2 needle", 3: "line 3 needle", 4: "line 4 needle"}))
    view = win.log.text
    matches = text_matches(view, "needle")

    bar = open_find(win, qtbot, view)
    bar.type("needle")
    k = bar.current()
    assert check_text(view, matches, k) == 2
    bar.key(Qt.Key_Escape)
    closed_and_back(bar, view)
    assert unhighlighted(view, matches, but=k) == [], "Esc left highlights"

    bar = open_find(win, qtbot, view)
    bar.type("needle")
    k = bar.current()
    check_text(view, matches, k)
    bar.click(bar.close)
    closed_and_back(bar, view)
    assert unhighlighted(view, matches, but=k) == [], "the close button left highlights"


def test_an_invalid_regular_expression_is_marked_red_and_said_with_no_crash_and_no_stale_highlights(win, qtbot):
    """Spec 4: "needle(" is not a valid regular expression: the field is marked red and the bar (or the field's
    tooltip) says the pattern is not valid; nothing stays highlighted and the count shows no match; Enter does nothing
    harmful. Back to a valid pattern, the mark goes and the matches come back. With regex off, "(" is a plain
    character."""
    show_log(win, haystack(12, {2: "line 2 needle", 3: "call needle(3)", 8: "line 8 needle"}))
    view, before = win.log.text, win.log.text.document().toPlainText()
    matches = text_matches(view, "needle")
    bar = open_find(win, qtbot, view)
    bar.type("needle")
    assert bar.total() == 3
    valid_red = red_pixels(bar.field)

    QTest.keyClicks(bar.field, "(")
    settle()
    assert bar.field.text() == "needle("
    assert red_pixels(bar.field) - valid_red >= 10, "the field is not marked red"
    assert says_invalid(bar), "nothing says the pattern is not valid"
    assert not re.fullmatch(r"\d+/\d+", bar.count()), f"the count still shows {bar.count()}"
    assert unhighlighted(view, matches) == [], "highlights stay with an invalid pattern"
    bar.key(Qt.Key_Return)
    bar.key(Qt.Key_Return, Qt.ShiftModifier)
    assert bar.shown() and unhighlighted(view, matches) == []
    assert view.document().toPlainText() == before

    bar.key(Qt.Key_Backspace)
    assert bar.total() == 3
    assert red_pixels(bar.field) - valid_red < 10, "the red mark stays with a valid pattern"
    assert not says_invalid(bar)
    check_text(view, matches, bar.current())

    bar.click(bar.regex)
    assert not bar.regex.isChecked()
    bar.type("needle(")
    assert bar.count() == "1/1", "with regex off, ( is not searched as typed"
    assert red_pixels(bar.field) - valid_red < 10 and not says_invalid(bar)
    check_text(view, text_matches(view, re.escape("needle(")), 0)


def test_a_repeat_count_too_large_for_python_is_said_to_be_not_valid_like_any_bad_pattern(win, qtbot):
    """Spec 4: "a{4294967296}" (Python refuses the count with an OverflowError, not a pattern error) is said to be not
    valid, as "(" is, with no crash and no count."""
    show_log(win, haystack(5, {2: "line 2 needle"}))
    bar = open_find(win, qtbot, win.log.text)
    bar.type("a{4294967296}")
    assert says_invalid(bar), "nothing says the pattern is not valid"
    assert not re.fullmatch(r"\d+/\d+", bar.count()), f"the count shows {bar.count()}"


def test_with_regex_off_the_text_is_searched_as_typed(win, qtbot):
    """Spec 2, 4: ".*" on, "3.5" and "(net)" are regular expressions; clicked off, they are searched as typed; the count
    follows at once."""
    show_log(win, ["total (net) = 3.5", "total net = 345", "the end"])
    bar = open_find(win, qtbot, win.log.text)
    bar.type("3.5")
    assert bar.total() == 2
    bar.type("(net)")
    assert bar.total() == 2
    bar.click(bar.regex)
    assert not bar.regex.isChecked()
    assert bar.count() == "1/1", "turning regular expressions off did not search again"
    bar.type("3.5")
    assert bar.count() == "1/1"
    bar.type(".*")
    assert bar.count() == "0 results"
    bar.click(bar.regex)
    assert bar.regex.isChecked() and bar.total() >= 1


def test_match_case_off_finds_every_case_and_on_only_the_case_typed(win, qtbot):
    """Spec 2: "Aa" off (as the editor starts), "error" finds Error, error and ERROR; clicked on, only error."""
    show_log(win, ["Error in load", "error in report", "ERROR in check", "all good"])
    bar = open_find(win, qtbot, win.log.text)
    bar.type("error")
    assert bar.total() == 3
    bar.click(bar.case)
    assert bar.case.isChecked()
    assert bar.count() == "1/1", "turning match case on did not search again"
    check_text(win.log.text, text_matches(win.log.text, "error", 0), 0)
    bar.click(bar.case)
    assert bar.total() == 3


def test_the_toggles_are_shared_by_every_find_bar_and_kept_while_the_editor_runs(win, qtbot, tmp_path):
    """Spec 2: turned over in the Log's find bar, ".*" and "Aa" are so in a code box's and in Run history's, and again
    in the Log's when it opens again; the search follows them."""
    tab = open_blocks(win, tmp_path / "toggles.yaml", {"load": {"type": "logic.python", "config": {
        "inputs": [], "code": "x = 'a.c'\ny = 'abc'\nz = 'A.C'"}}})
    show_log(win, ["a.c one", "abc two", "A.C three"])  # (after opening the flow, which shows its own Log)
    bar = None
    try:
        bar = open_find(win, qtbot, win.log.text)
        bar.type("a.c")
        assert bar.total() == 3
        bar.set_toggles(regex=False, case=True)
        assert bar.count() == "1/1"
        bar.key(Qt.Key_Escape)

        select(tab, "load")
        box = code_box(win)
        bar = open_find(win, qtbot, box)
        assert not bar.regex.isChecked() and bar.case.isChecked(), "the code box's find bar has other toggles"
        bar.type("a.c")
        assert bar.count() == "1/1"
        bar.key(Qt.Key_Escape)

        win.bottom_tabs.setCurrentWidget(win.history_panel)
        bar = open_find(win, qtbot, win.history_panel.table)
        assert not bar.regex.isChecked() and bar.case.isChecked(), "Run history's find bar has other toggles"
        bar.key(Qt.Key_Escape)

        tab.scene.clearSelection()  # (the Log shows every block's lines again)
        win.bottom_tabs.setCurrentWidget(win.log)
        settle()
        bar = open_find(win, qtbot, win.log.text)
        assert not bar.regex.isChecked() and bar.case.isChecked(), "the Log's find bar did not keep its toggles"
        bar.type("a.c")
        assert bar.count() == "1/1"
    finally:
        if bar is not None and bar.shown():
            bar.set_toggles(regex=True, case=False)  # (as the editor starts)
        clean(win)


NEXT_START = r"""
import json, os, sys, time
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QLineEdit, QMessageBox

from taskloom import cli
from taskloom.editor import window

boxes = []
for name in ("information", "warning", "question", "critical"):  # (a message box would wait for a click)
    setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: boxes.append(_n) or QMessageBox.No))
window.MainWindow.start_scheduler = lambda self, by_you=False: None  # (no scheduler service from a test)


def settle(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()


def opened(app):
    (win,) = [w for w in app.topLevelWidgets() if isinstance(w, window.MainWindow)]
    win.activateWindow()
    win.bottom_tabs.setCurrentWidget(win.log)
    win.log.text.setFocus()
    deadline = time.monotonic() + 10
    while not win.log.text.hasFocus() and time.monotonic() < deadline:
        settle(0.05)
    QTest.keySequence(win.log.text, QKeySequence(QKeySequence.Find))
    settle(0.2)
    field = app.focusWidget()
    toggles = {b.text().replace("&", ""): b.isChecked() for b in win.findChildren(QAbstractButton)
               if b.isVisible() and b.text().replace("&", "") in (".*", "Aa")}
    print(json.dumps({"field": isinstance(field, QLineEdit), "toggles": toggles, "boxes": boxes}), flush=True)
    return 0


QApplication.exec = opened  # started: look at it instead of waiting to be closed
code = cli.main(["editor"])
os._exit(code)
"""


def test_the_next_editor_start_has_regex_on_and_match_case_off_again(win, qtbot, tmp_path):
    """Spec 2 (".*" ON and "Aa" OFF when the editor starts; kept only while it runs): after the editor turned them over,
    `taskloom editor` started again on the same Taskloom folder has regex on and match case off."""
    show_log(win, ["needle"])
    bar = open_find(win, qtbot, win.log.text)
    bar.set_toggles(regex=False, case=True)
    bar.key(Qt.Key_Escape)
    win.settings.sync()
    settle()

    started_editor = subprocess.run(
        [sys.executable, "-c", NEXT_START], cwd=tmp_path, capture_output=True, text=True, timeout=120,
        env={**os.environ, "TASKLOOM_HOME": str(win.home.root), "QT_QPA_PLATFORM": "offscreen",
             "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"})
    lines = [line for line in started_editor.stdout.splitlines() if line.startswith("{")]
    assert started_editor.returncode == 0 and lines, started_editor.stdout + started_editor.stderr
    result = json.loads(lines[-1])
    assert result["boxes"] == [], result
    assert result["field"], "Ctrl+F in the Log of the editor started again opened no find bar"
    assert result["toggles"] == {REGEX: True, CASE: False}, result["toggles"]


# --- 5: what each panel searches -----------------------------------------------------------------------------------

def test_the_log_finds_in_the_lines_its_block_filter_shows(win, qtbot):
    """Spec 5: with the Log showing one block's lines, the search counts only those; Show all shows every line, and the
    count follows."""
    show_log(win, [("load", "load needle 1"), ("report", "report needle 1"), ("load", "load needle 2"),
                   ("report", "report needle 2"), ("report", "report needle 3")])
    win.log.set_filter("load")
    settle()
    view = win.log.text
    bar = open_find(win, qtbot, view)
    bar.type("needle")
    assert bar.total() == 2, "the search is not of the lines the Log shows"
    check_text(view, text_matches(view, "needle"), bar.current())

    win.log.clear_filter.click()
    settle()
    assert bar.total() == 5, "the count did not follow Show all"
    check_text(view, text_matches(view, "needle"), bar.current())


def test_new_log_lines_arriving_while_the_bar_is_open_update_the_count(win, qtbot, tmp_path):
    """Spec 5: during a run, with the Log's find bar open on "needle", a new line with a needle updates the count from
    1 to 2 matches."""
    gate = tmp_path / "go.txt"
    code = textwrap.dedent(f"""\
        import pathlib, time
        print("needle one")
        gate = pathlib.Path({str(gate)!r})
        deadline = time.monotonic() + 60
        while not gate.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        print("needle two")
    """)
    tab = win.open_flow(python_flow(tmp_path / "live.yaml", code))
    try:
        win.run_flow(tab)
        win.bottom_tabs.setCurrentWidget(win.log)
        view = win.log.text
        qtbot.waitUntil(lambda: "needle one" in view.document().toPlainText(), timeout=60_000)
        bar = open_find(win, qtbot, view)
        bar.type("needle")
        assert bar.count() == "1/1"

        gate.write_text("go", encoding="utf-8")
        qtbot.waitUntil(lambda: "needle two" in view.document().toPlainText(), timeout=60_000)
        qtbot.waitUntil(lambda: re.fullmatch(r"[12]/2", bar.count()) is not None, timeout=5_000)
        qtbot.waitUntil(lambda: not tab.running, timeout=60_000)
        check_text(view, text_matches(view, "needle"), bar.current())
    finally:
        gate.write_text("go", encoding="utf-8")
        deadline = time.monotonic() + 60
        while tab.running and time.monotonic() < deadline:  # (closing the editor with a run going would ask first)
            qtbot.wait(50)


def test_data_preview_finds_in_the_cells_and_headers_of_the_table_on_screen_and_selects_the_current_cell(win, qtbot,
                                                                                                         tmp_path):
    """Spec 1, 3, 5: in Data preview (300 rows, the first 200 on screen), Ctrl+F opens the bar under the table; "needle"
    is in 4 cells on screen (a fifth is in row 251, not on screen); each match in turn is selected, scrolled to and
    highlighted, the others too; a column header is searched as well; Esc gives the focus back to the table."""
    sql = ("select range as id, case when range in (7, 8, 120, 190, 250) then 'needle' else 'hay' end as word, "
           "'plain' as amount_total from range(300)")
    tab = run_in_editor(win, qtbot, write_flow(tmp_path / "words.yaml", {
        "start": {"type": "trigger.manual"},
        "words": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": sql}}}, ["start.start -> words.after"]))
    select(tab, "words")
    win.bottom_tabs.setCurrentWidget(win.preview)
    settle()
    view = win.preview.view
    assert view.model() is not None and view.model().rowCount() == 200, win.preview.label.text()
    matches = cell_matches(view, "needle")
    assert [m.row() for m in matches] == [7, 8, 120, 190]

    bar = open_find(win, qtbot, view)
    assert_under(bar, view, win.preview)
    bar.type("needle")
    assert bar.total() == 4, "not the cells of the table on screen"
    k, seen = bar.current(), 0
    seen += check_cells(view, matches, k)
    for _ in range(5):
        bar.key(Qt.Key_Return)
        k = (k + 1) % 4
        assert bar.current() == k
        seen += check_cells(view, matches, k)
    bar.click(bar.up)
    k = (k - 1) % 4
    assert bar.current() == k
    seen += check_cells(view, matches, k)
    assert seen > 0, "never two matches in view at once: the others' highlight was not seen"

    bar.type("amount")
    assert bar.count() == "1/1", "the column headers are not searched"
    bar.key(Qt.Key_Escape)
    closed_and_back(bar, view)


def test_run_history_finds_in_every_column_and_selects_the_current_run(win, qtbot, tmp_path):
    """Spec 1, 3, 5: Run history's find bar (under the list) searches every column of the 40 runs listed: Run,
    Started, Started by, Status, Duration, Parameters; each match in turn is selected, scrolled to and highlighted;
    the close button gives the focus back to the list."""
    runs = []
    for run_id in range(1, 41):
        runs.append({"params": {"region": "South" if run_id == 33 else "North"},
                     "status": "failed" if run_id in (5, 22, 37) else "success",
                     "by": "nightly" if run_id in (30, 31) else "manual",
                     "seconds": 125 if run_id == 12 else 1.0})
    _, table = listed_runs(win, qtbot, tmp_path, runs)
    bar = open_find(win, qtbot, table)
    assert_under(bar, table, win.history_panel)

    seen = 0
    for term, expected, column in [("failed", 3, "Status"), ("nightly", 2, "Started by"), ("South", 1, "Parameters"),
                                   ("2 min 5 s", 1, "Duration"), (started(32).isoformat(" ")[:16], 1, "Started"),
                                   ("40", 1, "Run")]:
        matches = cell_matches(table, term)
        assert len(matches) == expected, (term, [table.item(m.row(), m.column()).text() for m in matches])
        assert {table.horizontalHeaderItem(m.column()).text() for m in matches} == {column}
        bar.type(term)
        assert bar.total() == expected, f"{term!r} ({column}): {bar.count()}"
        k = bar.current()
        seen += check_cells(table, matches, k)
        for _ in range(expected + 1):
            bar.key(Qt.Key_Return)
            k = (k + 1) % expected
            assert bar.current() == k
            seen += check_cells(table, matches, k)
    assert seen > 0, "never two matches in view at once: the others' highlight was not seen"

    bar.click(bar.close)
    closed_and_back(bar, table)


CODE_BOXES = {  # block, its Code setting, the rest of its config
    "Python Code": ("logic.python", "code", {"inputs": []}),
    "Run Query": ("db.run_query", "sql", {}),
    "DuckDB SQL": ("data.duckdb_sql", "sql", {}),
    "Polars Transform": ("data.polars", "code", {}),
    "HTML Template": ("reports.html_template", "template", {}),
    "If": ("logic.if", "condition", {}),
    "Inspect": ("logic.inspect", "message", {}),
}
CODE = "\n".join({2: "x = 'needle'", 30: "needle + needle", 57: "y = needle"}.get(i, f"line_{i} = {i}")
                 for i in range(60))


@pytest.mark.parametrize("title", list(CODE_BOXES))
def test_every_code_box_finds_under_itself_without_changing_the_code(win, qtbot, tmp_path, title):
    """Spec 1, 3, 5: in every code box (each Code setting of a block), Ctrl+F opens the find bar just under the box;
    "needle" is found 4 times, each in turn scrolled into view and highlighted, the others too; the code, its Undo and
    the flow stay unchanged; Esc gives the focus back to the box."""
    type_id, key, config = CODE_BOXES[title]
    tab = open_blocks(win, tmp_path / "code.yaml", {"work": {"type": type_id, "config": {**config, key: CODE}}})
    try:
        select(tab, "work")
        box = code_box(win)
        assert box.toPlainText() == CODE
        undo = box.document().isUndoAvailable()

        bar = open_find(win, qtbot, box)
        assert win.properties.isAncestorOf(bar.widget), "the code box's find bar is not in Properties"
        top, bottom = bar.widget.mapTo(win, QPoint(0, 0)).y(), box.mapTo(win, QPoint(0, box.height())).y()
        assert bottom - 1 <= top <= bottom + 20, f"the find bar is not just under the box ({top}, box ends at {bottom})"
        left, box_left = bar.widget.mapTo(win, QPoint(0, 0)).x(), box.mapTo(win, QPoint(0, 0)).x()
        assert left < box_left + box.width() and left + bar.widget.width() > box_left

        bar.type("needle")
        matches = text_matches(box, "needle")
        assert bar.total() == 4
        k, seen = bar.current(), 0
        seen += check_text(box, matches, k)
        for _ in range(5):
            bar.key(Qt.Key_Return)
            k = (k + 1) % 4
            assert bar.current() == k
            seen += check_text(box, matches, k)
        bar.key(Qt.Key_Return, Qt.ShiftModifier)
        k = (k - 1) % 4
        assert bar.current() == k
        seen += check_text(box, matches, k)
        assert seen > 0, "never two matches in view at once: the others' highlight was not seen"

        assert box.toPlainText() == CODE and box.document().isUndoAvailable() == undo, "the search changed the code"
        bar.key(Qt.Key_Escape)
        closed_and_back(bar, box)
        assert box.toPlainText() == CODE
        assert tab.doc.blocks["work"]["config"][key] == CODE and not tab.doc.dirty
    finally:
        clean(win)


def type_at_end(box, text: str):
    cursor = box.textCursor()
    cursor.movePosition(QTextCursor.End)
    box.setTextCursor(cursor)
    QTest.keyClicks(box, text)
    settle()


@pytest.mark.parametrize("title", list(CODE_BOXES))
def test_a_code_box_typed_in_before_and_while_its_find_bar_is_open_keeps_the_bar_and_the_focus(win, qtbot, tmp_path,
                                                                                               title):
    """Spec 1, 3, 5 (the common path: edit the code, then search): in every code box, typed in and then Ctrl+F, the
    find bar opens just under the box with the focus in its field and the text typed kept; typed in again with the bar
    open and clicked back into the field, the bar still shows with the focus in its field, the new match is found,
    Enter and Shift+Enter step through the matches; the edit is saved and the search did not change it."""
    type_id, key, config = CODE_BOXES[title]
    tab = open_blocks(win, tmp_path / "code.yaml", {"work": {"type": type_id, "config": {**config, key: "a = 1"}}})
    try:
        select(tab, "work")
        box = code_box(win)
        focus(win, qtbot, box)
        type_at_end(box, " needle")

        bar = open_find(win, qtbot, box)
        box = code_box(win)
        assert win.properties.isAncestorOf(bar.widget), "the code box's find bar is not in Properties"
        top, bottom = bar.widget.mapTo(win, QPoint(0, 0)).y(), box.mapTo(win, QPoint(0, box.height())).y()
        assert bottom - 1 <= top <= bottom + 20, f"the find bar is not just under the box ({top}, box ends at {bottom})"
        left, box_left = bar.widget.mapTo(win, QPoint(0, 0)).x(), box.mapTo(win, QPoint(0, 0)).x()
        assert left < box_left + box.width() and left + bar.widget.width() > box_left
        assert box.toPlainText() == "a = 1 needle", "the text just typed is not kept"

        bar.type("needle")
        assert bar.count() == "1/1"

        focus(win, qtbot, box)
        type_at_end(box, " needle")
        bar.click(bar.field)
        assert bar.shown(), "the find bar went away when clicked back into after typing in the box"
        assert bar.field.hasFocus(), f"the focus is in {type(QApplication.focusWidget()).__name__}, not the field"
        assert bar.total() == 2, "the needle typed with the bar open is not found"
        k = bar.current()
        bar.key(Qt.Key_Return)
        assert bar.current() == (k + 1) % 2
        bar.key(Qt.Key_Return, Qt.ShiftModifier)
        assert bar.current() == k

        assert code_box(win).toPlainText() == "a = 1 needle needle"
        assert tab.doc.blocks["work"]["config"][key] == "a = 1 needle needle", "the edit is not saved"
    finally:
        clean(win)


def test_enter_in_a_code_box_still_starts_a_new_line_while_its_find_bar_is_open(win, qtbot, tmp_path):
    """Spec 1 (no other shortcut is taken over): with the code box's find bar open, Enter typed back in the box starts
    a new line as before (as deep as the one before), and does not go to the next match."""
    tab = open_blocks(win, tmp_path / "enter.yaml", {"load": {"type": "logic.python", "config": {
        "inputs": [], "code": "if ready:\n    total = 1"}}})
    try:
        select(tab, "load")
        box = code_box(win)
        bar = open_find(win, qtbot, box)
        bar.type("total")
        assert bar.count() == "1/1"
        focus(win, qtbot, box)
        cursor = box.textCursor()
        cursor.movePosition(QTextCursor.End)
        box.setTextCursor(cursor)
        QTest.keyClick(box, Qt.Key_Return)
        settle()
        assert box.toPlainText() == "if ready:\n    total = 1\n    "
    finally:
        clean(win)


# --- 6: light and dark ---------------------------------------------------------------------------------------------

@pytest.fixture
def look_kept(qapp):
    """The application's look (its style, palette, style sheet and tooltip colours are the whole test session's) put
    back as it was."""
    style, sheet, palette, tips = qapp.style().name(), qapp.styleSheet(), QApplication.palette(), QToolTip.palette()
    yield
    if qapp.style().name() != style:
        QApplication.setStyle(style)
    if qapp.styleSheet() != sheet:
        qapp.setStyleSheet(sheet)
    QApplication.setPalette(palette)
    QToolTip.setPalette(tips)
    settle()


def assert_red_mark_reads_well(field):
    image = field.grab().toImage()
    pixels = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())]
    back = most_common(image)
    if is_red(back):  # the field's background is red: its text reads on it
        assert max(contrast(c, back) for c in pixels) >= TEXT, f"the text does not read on the red {back.name()}"
    else:
        best = max((contrast(c, back) for c in pixels if is_red(c)), default=1.0)
        assert best >= MARK, f"the red mark does not stand out from {back.name()} ({best:.2f})"


@pytest.mark.parametrize("name", ["Light", "Dark"])
def test_highlights_and_the_invalid_mark_read_well_in_light_and_dark(win, qtbot, tmp_path, look_kept, name):
    """Spec 6: in the theme chosen (light or dark), the Log's and Run history's highlights stand out from the background,
    the current one from the others, the text reads on them (4.5:1), and the red invalid mark stands out (3:1)."""
    theme.apply(name)
    settle()
    show_log(win, haystack(10, {3: "line 3 needle", 4: "line 4 needle"}))
    view = win.log.text
    matches = text_matches(view, "needle")
    bar = open_find(win, qtbot, view)
    bar.type("needle")
    assert check_text(view, matches, bar.current()) == 1
    for start, length in matches:
        shade, rect = behind(view, where(view, start, length)), where(view, start, length)
        assert differs(shade, plain(view)), f"{name}: a highlight {shade.name()} on {plain(view).name()}"
        assert text_on(view, rect) >= TEXT, f"{name}: the text does not read on the highlight {shade.name()}"
    QTest.keyClicks(bar.field, "(")
    settle()
    assert red_pixels(bar.field) >= 10, f"{name}: the field is not marked red"
    assert_red_mark_reads_well(bar.field)
    bar.key(Qt.Key_Escape)

    _, table = listed_runs(win, qtbot, tmp_path, [{"params": {"region": "South"}}, {"params": {"region": "South"}},
                                                  {"params": {"region": "North"}}])
    bar = open_find(win, qtbot, table)
    bar.type("South")
    matches = cell_matches(table, "South")
    assert check_cells(table, matches, bar.current()) == 1
    for index in matches:
        if not table.selectionModel().isSelected(index):
            assert text_on(table, table.visualRect(index)) >= TEXT, f"{name}: the text does not read on the highlight"


# --- 7: the README -------------------------------------------------------------------------------------------------

def test_the_readme_says_ctrl_f_finds_in_the_log_data_preview_run_history_and_code_boxes_with_its_two_toggles():
    """Spec 7: where the README describes the Log, Data preview, Run history and the code boxes, it says Ctrl+F finds
    text there, with its two toggles (regular expressions, match case)."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    items = [" ".join(item.split()) for item in re.split(r"\n\s*\n|\n- ", readme)]
    found = [item for item in items if "Ctrl+F" in item]
    assert found, "the README says nothing about Ctrl+F"
    said = " ".join(found)
    for place in ("Log", "Data preview", "Run history", "code box"):
        assert place in said, f"the README's Ctrl+F does not say it works in the {place}"
    assert any((".*" in item or "regular expression" in item.lower()) and ("Aa" in item or "case" in item.lower())
               for item in found), "the README's Ctrl+F does not name its two toggles"
