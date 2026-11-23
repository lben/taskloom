"""Source diffs in Commit History (docs/promises/source-diff.yaml, CD-1..7, agreed 2026-11-20): right-click a commit in
Commit History, "Show source diff against previous version" or "Show source diff against current version": a window on
top shows the flow file's text side by side, the older version on the left; added, removed and changed lines (and the
changed characters inside a changed line) marked by colour; unchanged lines level, filler lines unnumbered; both sides
scrolling together; Next and Previous (F7, Shift+F7) jumping between the changes; nothing typed; Find in the side
clicked in, Zoom on both sides; the title naming both versions; a note line when line ends or the newline at the end
differ; looking changes nothing. Each test names the row of the stage 3 combinations (D1..D16) it covers.

Driven through the real editor (the `win` fixture): the flow opened from its file, File → Commit History…, a card
right-clicked as you would and the menu's item clicked, keys typed where the focus is, the pointer put over a side,
the theme chosen in Settings. Git histories are made, and `git diff` read (the oracle of D10), with the real git
program; every repository and the Git config are the test's own (the autouse fixtures of test_commit_history). What
the window shows is read as you see it: each side's text, the numbers it paints left of its lines, and the colours it
paints its lines and characters in."""

# The window's contract (it does not exist yet: these are the names and looks the tests find its parts by).
# - Right-click a card in Commit History: a menu with PREVIOUS and CURRENT (below); on the first commit PREVIOUS is in
#   it, greyed out.
# - The window: a window of its own, not modal, objectName "source-diff", the active window once it opens; its title
#   names both versions (each commit's message and time as the cards show it, or "current" / "current (not saved)").
# - Its sides: QPlainTextEdit "source-diff-left" (the older version) and "source-diff-right". Each line of a side's
#   document is one row and the two sides' rows are level row by row; a filler line is an empty row. The numbered rows
#   are the file's lines as git counts them: ended by "\n" only (a Windows line end's "\r" not shown, no empty line
#   after the last newline; a lone "\r", "\x0b", "\x0c", "\x1c"-"\x1e", "\x85", U+2028, U+2029, U+FDD0 or U+FDD1
#   inside a line shown on its row as some character), numbered from 1 by a number painted inside the side, left of
#   its text (as CodeEditor paints them); a filler line has no number.
# - Marks are the colours painted behind the text: an unchanged row in the side's Base colour, an added row green, a
#   removed row red, a changed row blue (by hue, as in the approved mockup); inside a changed row the changed
#   characters, and only they, in a colour of their own.
# - Buttons "previous-change" and "next-change" (and F7, Shift+F7 anywhere in the window); they do not wrap round: at
#   the first change Previous is greyed out, at the last Next. As the window opens, the first change is the current
#   one, in view.
# - QLabel "change-count": "<n> of <m> changes". When the versions are the same, a label (e.g. it) says
#   "No differences".
# - QLabel "diff-note" above the sides, shown only when the versions differ in line ends, also when both mix them but
#   on other lines (it says "line ends", "Windows" and "Unix"), or in the newline at the end (it says "newline at the
#   end").
# - Ctrl+F in a side opens a find bar (as the code boxes' FindBar: count "1/3") searching that side only; Ctrl + / - / 0
#   with the pointer over either side zooms both alike (zoom panes of the editor window, as zoom.py has them).

import copy
import hashlib
import itertools
import os
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QTimer  # noqa: E402
from PySide6.QtGui import (QColor, QContextMenuEvent, QImage, QKeySequence, QMouseEvent, QPalette,  # noqa: E402
                           QTextCursor, QWheelEvent)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QMenu, QMessageBox, QPlainTextEdit  # noqa: E402

from tests.test_code_editor import drawn_texts  # noqa: E402
import tests.test_commit_history as commit_history  # noqa: E402
from tests.test_commit_history import dates, known_identity, local, make_history, open_history, settle  # noqa: E402

# Fixtures of Commit History's tests, used here by name (bound, not imported, so that pyflakes passes).
git_config, look_kept = commit_history.git_config, commit_history.look_kept
windows_closed = commit_history.windows_closed
from tests.test_find_bar import contrast, differs, open_find  # noqa: E402
from tests.test_paths_editor_editing import clean, menu, save_as  # noqa: E402
from tests.test_save_and_commit import GIT, git, needs_git, shown  # noqa: E402
from tests.test_theme import choose, not_in_theme  # noqa: E402
from tests.test_zoom_panes import pointer_over  # noqa: E402

PREVIOUS = "Show source diff against previous version"
CURRENT = "Show source diff against current version"
WINDOW, LEFT, RIGHT = "source-diff", "source-diff-left", "source-diff-right"
PREVIOUS_CHANGE, NEXT_CHANGE, COUNT, NOTE = "previous-change", "next-change", "change-count", "diff-note"
TEXT = 4.5  # the contrast text needs (WCAG)
pytestmark = needs_git


# --- flow files ------------------------------------------------------------------------------------------------------

HEAD = ["taskloom: 1", "name: {name}", "params: {{}}", "blocks:", "  manual:", "    type: trigger.manual",
        "    ui: {{x: -280, y: 0}}", "  load:", "    type: logic.python", "    config:", "      inputs: []",
        "      outputs: [output]", "      code: |"]
TAIL = ["    ui: {x: 0, y: 0}", "edges:", "- manual.start -> load.after"]
CODE_AT = len(HEAD)  # the file's line (from 0) that holds the code's first line


def code(n: int, word: str = "total", start: int = 0) -> list[str]:
    """`n` lines of Python, none the same."""
    return [f"{word}_{i} = read('region_{i}', day={i})" for i in range(start, start + n)]


def flow_lines(lines: list[str], name: str = "report", before=(), after=()) -> list[str]:
    """A flow file's lines: its Python block's code is `lines`; `before` and `after` are lines at its start and end."""
    return [*before, *(h.format(name=name) for h in HEAD), *(f"        {line}" for line in lines), *TAIL, *after]


def as_bytes(lines: list[str], newline: str = "\n", last: bool = True) -> bytes:
    """The bytes of a file of `lines`, each ended by `newline` (`last` False: not the last one)."""
    return (newline.join(lines) + (newline if last and lines else "")).encode("utf-8")


def flow_history(win, qtbot, folder: Path, versions: list[tuple[str, bytes]]):
    """A repository in `folder` whose commits, oldest first, give report.yaml these versions (message, bytes), the flow
    open in the editor and its Commit History showing: (the commits' ids and author times as shown, oldest first, the
    Commit History window, the flow's tab)."""
    when = dates(len(versions))
    ids = make_history(folder, [(m, {"report.yaml": data}, w) for (m, data), w in zip(versions, when)])
    tab = win.open_flow(folder / "report.yaml")
    assert tab is not None and win.message_boxes == []
    return ids, [local(w) for w in when], open_history(win, qtbot, "report.yaml"), tab


# --- right-clicking a commit and the window that opens ---------------------------------------------------------------

def right_click_card(history, qtbot, place: int, choose_item: str | None = None) -> tuple[list[dict], list[float]]:
    """Right-click the card at `place` among those shown in Commit History, as you would, then click its menu's item
    `choose_item` if it is there and enabled (the menu closed otherwise). What the menu listed ({"text", "enabled"}
    per item; nothing: no menu opened), and when the item was clicked (time.monotonic(); empty: it was not)."""
    view = history.listing
    index = view.model().index(history.rows()[place], 0)
    view.scrollTo(index)
    settle()
    viewport = view.viewport()
    at = view.visualRect(index).center()
    where = viewport.mapToGlobal(at)
    listed, clicked, done, deadline = [], [], [], time.monotonic() + 5

    def look():  # (a menu can wait for its click inside the right-click)
        if done:
            return
        popup = QApplication.activePopupWidget()
        if not isinstance(popup, QMenu):
            if time.monotonic() < deadline:
                QTimer.singleShot(20, look)
            else:
                done.append(True)
            return
        done.append(True)
        actions = [a for a in popup.actions() if a.text()]
        listed.extend({"text": shown(a.text()), "enabled": a.isEnabled()} for a in actions)
        chosen = next((a for a in actions if shown(a.text()) == choose_item and a.isEnabled()), None)
        if chosen is not None:
            clicked.append(time.monotonic())
            QTest.mouseClick(popup, Qt.LeftButton, Qt.NoModifier, popup.actionGeometry(chosen).center())
        try:
            if popup.isVisible():
                popup.close()
        except RuntimeError:  # (deleted once closed)
            pass

    for kind, buttons in ((QEvent.MouseButtonPress, Qt.RightButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
        QApplication.sendEvent(viewport, QMouseEvent(kind, QPointF(at), QPointF(where), Qt.RightButton, buttons,
                                                     Qt.NoModifier))
    QTimer.singleShot(0, look)
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, where))
    qtbot.waitUntil(lambda: bool(done), timeout=20_000)
    settle()
    return listed, clicked


def menu_of(history, qtbot, place: int) -> dict[str, bool]:
    """The items of the menu of the card at `place`, and whether each is enabled (it is closed again)."""
    listed, _ = right_click_card(history, qtbot, place)
    assert listed, "right-clicking a commit in Commit History opens no menu"
    return {item["text"]: item["enabled"] for item in listed}


def offered(items: dict[str, bool]) -> dict[str, bool | None]:
    """Whether the menu's two diff items are enabled (None: not in it)."""
    return {item: items.get(item) for item in (PREVIOUS, CURRENT)}


def diff_windows() -> list:
    return [w for w in QApplication.topLevelWidgets() if w.objectName() == WINDOW and w.isVisible()]


def open_diff(history, qtbot, place: int, item: str) -> "Diff":
    """Right-click the card at `place` and click `item`: the source diff window that shows."""
    before = set(diff_windows())
    listed, clicked = right_click_card(history, qtbot, place, item)
    assert listed, "right-clicking a commit in Commit History opens no menu"
    found = [i for i in listed if i["text"] == item]
    assert len(found) == 1, f"the menu has no {item!r} item: {listed}"
    assert found[0]["enabled"], f"{item!r} is greyed out"
    assert clicked, f"{item!r} was not clicked"
    qtbot.waitUntil(lambda: len(set(diff_windows()) - before) == 1, timeout=10_000)
    (window,) = set(diff_windows()) - before
    settle()
    return Diff(window, clicked[0])


class Diff:
    """What the source diff window shows."""

    def __init__(self, window, clicked: float):
        self.window, self.clicked = window, clicked

    @property
    def title(self) -> str:
        return self.window.windowTitle()

    def _part(self, kind, name: str):
        found = self.window.findChild(kind, name)
        assert found is not None and found.isVisible(), f"the source diff window shows no {kind.__name__} {name!r}"
        return found

    @property
    def left(self) -> QPlainTextEdit:
        return self._part(QPlainTextEdit, LEFT)

    @property
    def right(self) -> QPlainTextEdit:
        return self._part(QPlainTextEdit, RIGHT)

    def button(self, name: str) -> QAbstractButton:
        return self._part(QAbstractButton, name)

    def count(self) -> str:
        return " ".join(self._part(QLabel, COUNT).text().split())

    def note(self) -> str:
        """What the note line above the sides says ("": it does not show)."""
        found = self.window.findChild(QLabel, NOTE)
        return " ".join(found.text().split()) if found is not None and found.isVisible() else ""

    def says(self) -> list[str]:
        return [" ".join(label.text().split()) for label in self.window.findChildren(QLabel) if label.isVisible()]

    def texts(self) -> tuple[str, str]:
        return self.left.toPlainText(), self.right.toPlainText()

    def close(self):
        self.window.close()
        settle()


# --- what a side paints ----------------------------------------------------------------------------------------------

@dataclass
class Row:
    number: int | None  # the number painted on its left (None: none, a filler line)
    text: str
    colour: QColor  # what most of the row is painted in
    marked: tuple | None  # the characters (indexes into text) painted in another colour than the row (None: not read)


def painted(image, rect: QRect, step_x: int = 3, step_y: int = 2) -> QColor:
    """The colour most of `rect` (in the viewport's coordinates) of the viewport's `image` is painted in."""
    ratio = image.devicePixelRatio()
    r = QRect(int(rect.left() * ratio), int(rect.top() * ratio), max(1, int(rect.width() * ratio)),
              max(1, int(rect.height() * ratio))).intersected(image.rect())
    counts = Counter(image.pixel(x, y) for y in range(r.top(), r.bottom() + 1, step_y)
                     for x in range(r.left(), r.right() + 1, step_x))
    assert counts, f"nothing is painted in {rect}"
    return QColor.fromRgb(counts.most_common(1)[0][0])


def line_rect(side, block) -> QRect:
    """Where the side's viewport shows the row `block`, across its whole width."""
    at = side.cursorRect(QTextCursor(block))
    return QRect(0, at.top(), side.viewport().width(), at.height())


def wholly_in_view(side, row: int) -> bool:
    rect = line_rect(side, side.document().findBlockByNumber(row))
    return rect.top() >= 0 and rect.bottom() < side.viewport().height()


def top_row(side) -> int:
    return side.cursorForPosition(QPoint(1, 1)).block().blockNumber()


def painted_numbers(side) -> list[tuple[float, str]]:
    """The numbers the side paints left of its text: (where each one's baseline is in its viewport, the number)."""
    origin = side.viewport().mapTo(side, QPoint(0, 0))
    return [(y - origin.y(), text.strip()) for x, y, text in drawn_texts(side)
            if text.strip().isdigit() and x < origin.x()]


def marked_characters(side, block, image, row_colour: QColor) -> tuple:
    """The characters of the row `block` (indexes into its text) painted in another colour than the row."""
    found, units = [], 0
    for i, char in enumerate(block.text()):
        width = 2 if ord(char) > 0xFFFF else 1  # (Qt counts a character outside the basic plane as two)
        first, last = (QTextCursor(block) for _ in range(2))
        first.setPosition(block.position() + units)
        last.setPosition(block.position() + units + width)
        a, b = side.cursorRect(first), side.cursorRect(last)
        cell = QRect(a.left() + 1, a.top(), b.left() - a.left() - 2, a.height())
        if cell.width() > 0 and differs(painted(image, cell, 1, 1), row_colour, 6):
            found.append(i)
        units += width
    return tuple(found)


def shown_rows(side, characters: bool) -> dict[int, Row]:
    """The rows the side shows whole now, by their place (from 0)."""
    viewport = side.viewport()
    image = viewport.grab().toImage()
    numbers = painted_numbers(side)
    page = {}
    block = side.cursorForPosition(QPoint(1, 1)).block()
    while block.isValid():
        rect = line_rect(side, block)
        if rect.top() >= viewport.height():
            break
        if rect.top() >= 0 and rect.bottom() < viewport.height():
            colour = painted(image, rect)
            on_row = [n for y, n in numbers if rect.top() <= y <= rect.bottom() + 1]
            assert len(on_row) <= 1, f"row {block.blockNumber() + 1} has the numbers {on_row} painted on it"
            page[block.blockNumber()] = Row(int(on_row[0]) if on_row else None, block.text(), colour,
                                            marked_characters(side, block, image, colour) if characters else None)
        block = block.next()
    return page


def rows(side, characters: bool = False) -> list[Row]:
    """Every row of the side, top to bottom, as it shows it (scrolled to each in turn, then back)."""
    settle()
    bar, doc = side.verticalScrollBar(), side.document()
    kept, found = bar.value(), {}
    try:
        while len(found) < doc.blockCount():
            first = next(n for n in range(doc.blockCount()) if n not in found)
            bar.setValue(doc.findBlockByNumber(first).firstLineNumber())
            settle()
            page = shown_rows(side, characters)
            assert first in page, f"scrolled to its row {first + 1}, the side does not show it whole"
            found.update(page)
    finally:
        bar.setValue(kept)
        settle()
    return [found[n] for n in range(doc.blockCount())]


def kind(side, row: Row) -> str:
    """How the row is marked: "unchanged" (the side's background), "added" (green), "removed" (red), "changed"
    (blue)."""
    colour = row.colour
    if not differs(colour, side.palette().color(QPalette.Base), 6):
        return "unchanged"
    hue = colour.hslHue()
    if 75 <= hue <= 165:
        return "added"
    if 0 <= hue <= 25 or hue >= 330:
        return "removed"
    if 190 <= hue <= 260:
        return "changed"
    return f"another colour {colour.name()}"


def marks(side, side_rows: list[Row]) -> dict[int, str]:
    """The numbered rows marked, by their number: how each is marked."""
    return {r.number: kind(side, r) for r in side_rows if r.number is not None and kind(side, r) != "unchanged"}


def numbered(side_rows: list[Row]) -> list[str]:
    return [r.text for r in side_rows if r.number is not None]


def assert_shows(side_rows: list[Row], lines: list[str], which: str):
    """The side shows exactly `lines`, numbered from 1, and its filler lines empty and unnumbered."""
    assert numbered(side_rows) == lines, f"the {which} side does not show the lines of its version"
    assert [r.number for r in side_rows if r.number is not None] == list(range(1, len(lines) + 1)), (
        f"the {which} side's lines are not numbered 1 to {len(lines)}")
    assert all(r.text == "" for r in side_rows if r.number is None), f"a filler line of the {which} side has text"


def level(diff) -> int:
    """Each row the two sides both show whole is at the same height on the screen; how many were compared."""
    left, right = diff.left, diff.right
    compared = 0
    for n in range(min(top_row(left), top_row(right)), min(left.document().blockCount(),
                                                             right.document().blockCount())):
        if not (wholly_in_view(left, n) and wholly_in_view(right, n)):
            if compared:
                break
            continue
        tops = [side.viewport().mapTo(diff.window, line_rect(side, side.document().findBlockByNumber(n)).topLeft()).y()
                for side in (left, right)]
        assert tops[0] == tops[1], f"row {n + 1} is at {tops[0]} on the left and at {tops[1]} on the right"
        compared += 1
    return compared


def changes(diff, left_rows: list[Row], right_rows: list[Row]) -> list[tuple[int, int]]:
    """The changes, top to bottom: each one's first and last row (from 0)."""
    assert len(left_rows) == len(right_rows), "the two sides do not have as many rows"
    part = [kind(diff.left, a) != "unchanged" or kind(diff.right, b) != "unchanged" or a.number is None
            or b.number is None for a, b in zip(left_rows, right_rows)]
    found = []
    for n, inside in enumerate(part):
        if inside and (not found or found[-1][1] != n - 1):
            found.append((n, n))
        elif inside:
            found[-1] = (found[-1][0], n)
    return found


def strongest(side, rect: QRect, behind: QColor) -> float:
    """The contrast with `behind` of the colour most unlike it painted in `rect` of the side's viewport."""
    image = side.viewport().grab(rect).toImage()
    return max(contrast(image.pixelColor(x, y), behind) for y in range(image.height()) for x in range(image.width()))


def focus(diff, qtbot, side):
    """As when you click into `side`: the window is the active one and the keyboard focus is in it."""
    diff.window.raise_()
    diff.window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is diff.window, timeout=5_000)
    side.setFocus()
    qtbot.waitUntil(side.hasFocus, timeout=5_000)


def key(widget, k, modifiers=Qt.NoModifier):
    QTest.keyClick(QApplication.focusWidget() or widget, k, modifiers)
    settle()


def wheel(side, notches: int):
    """The mouse wheel turned over the side, `notches` towards you (scrolling down; less than 0: up)."""
    viewport = side.viewport()
    at = viewport.rect().center()
    for _ in range(abs(notches)):
        QApplication.sendEvent(viewport, QWheelEvent(QPointF(at), QPointF(viewport.mapToGlobal(at)), QPoint(0, 0),
                                                     QPoint(0, -120 if notches > 0 else 120), Qt.NoButton,
                                                     Qt.NoModifier, Qt.NoScrollPhase, False))
        settle()


def size(side) -> tuple[int, int]:
    """How big the side shows its text: the height of a line, and the width 20 characters of a line of code take."""
    block = side.document().findBlockByNumber(CODE_AT)
    first, last = QTextCursor(block), QTextCursor(block)
    last.setPosition(block.position() + 20)
    a, b = side.cursorRect(first), side.cursorRect(last)
    return a.height(), b.left() - a.left()


def grown(after: tuple, before: tuple) -> bool:
    return all(a >= b for a, b in zip(after, before)) and any(a > b for a, b in zip(after, before))


# --- CD-1, CD-4: the menu, the window, the first commit, Save as -----------------------------------------------------

@pytest.mark.invariant("CD-1")
@pytest.mark.invariant("CD-4")
def test_d1_both_items_on_a_commit_previous_greyed_on_the_first_also_after_save_as_and_the_window_opens_on_top(
        win, qtbot, tmp_path, monkeypatch, git_config):
    """D1: right-clicking any commit offers both diffs; on the first commit "against previous version" is greyed out
    (there, not hidden), "against current version" opens a window of its own on top (the active one, not modal), the
    commit on the left and the flow file as it is on the right. After Save as in the same repository the copy's
    Commit History holds the original's commits (another file name then): on the second of them both diffs work, on
    the first carried commit "against previous version" is greyed out."""
    known_identity(git_config)
    folder = tmp_path / "flows"
    versions = [flow_lines(code(6)), flow_lines(code(7)), flow_lines(code(8))]
    ids, _, history, tab = flow_history(win, qtbot, folder, [(f"Version {i + 1}", as_bytes(v))
                                                             for i, v in enumerate(versions)])

    assert offered(menu_of(history, qtbot, 0)) == {PREVIOUS: True, CURRENT: True}
    assert offered(menu_of(history, qtbot, 2)) == {PREVIOUS: False, CURRENT: True}
    diff = open_diff(history, qtbot, 2, CURRENT)

    assert diff.window.isWindow() and diff.window is not history.window and diff.window is not win
    assert not diff.window.isModal() and QApplication.activeModalWidget() is None
    qtbot.waitUntil(lambda: QApplication.activeWindow() is diff.window, timeout=5_000)
    assert_shows(rows(diff.left), versions[0], "left")
    assert_shows(rows(diff.right), versions[2], "right")
    diff.close()

    save_as(monkeypatch, folder / "copy.yaml")
    menu(win, "Save as…").trigger()
    settle()
    assert win.message_boxes == []
    saved = (folder / "copy.yaml").read_text(encoding="utf-8").splitlines()
    history = open_history(win, qtbot, "copy.yaml")
    assert history.ids(ids) == [ids[2], ids[1], ids[0]], "the copy's Commit History is not the original's commits"

    assert offered(menu_of(history, qtbot, 2)) == {PREVIOUS: False, CURRENT: True}
    diff = open_diff(history, qtbot, 1, PREVIOUS)
    assert_shows(rows(diff.left), versions[0], "left")
    assert_shows(rows(diff.right), versions[1], "right")
    diff.close()
    diff = open_diff(history, qtbot, 1, CURRENT)
    assert_shows(rows(diff.left), versions[1], "left")
    assert_shows(rows(diff.right), saved, "right")


HAND_WRITTEN = ["# The nightly report, written by hand: Taskloom would not write this line", *flow_lines(code(6))]


@pytest.mark.invariant("CD-4")
def test_d2_a_commit_equal_to_the_current_version_says_no_differences(win, qtbot, tmp_path):
    """D2: the flow file as it is (written by hand: a comment Taskloom would not write) equals its last commit, and the
    tab holds no unsaved change: "against current version" on that commit says "No differences", nothing marked, both
    sides the same; on the commit before, it does not say so."""
    versions = [flow_lines(code(5)), HAND_WRITTEN]
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("Version 1", as_bytes(versions[0])),
                                                                     ("Version 2", as_bytes(versions[1]))])

    diff = open_diff(history, qtbot, 0, CURRENT)

    assert any("No differences" in said for said in diff.says()), diff.says()
    left, right = rows(diff.left), rows(diff.right)
    assert_shows(left, HAND_WRITTEN, "left")
    assert_shows(right, HAND_WRITTEN, "right")
    assert marks(diff.left, left) == {} and marks(diff.right, right) == {}
    assert diff.note() == ""
    diff.close()

    diff = open_diff(history, qtbot, 1, CURRENT)
    assert not any("No differences" in said for said in diff.says()), diff.says()


# --- CD-3: the title -------------------------------------------------------------------------------------------------

@pytest.mark.invariant("CD-3")
def test_d3_against_current_the_right_side_is_what_the_tab_holds_titled_current_or_current_not_saved(win, qtbot,
                                                                                                     tmp_path):
    """D3: with nothing unsaved the right side is the flow file as it is (its hand-written comment too) and the title
    says "current", not "not saved"; with an unsaved change in the tab the right side is what the tab holds (what
    File → Save then writes) and the title says "current (not saved)". The title names the commit too."""
    versions = [flow_lines(code(5)), HAND_WRITTEN]
    ids, when, history, tab = flow_history(win, qtbot, tmp_path / "flows", [("Load the numbers", as_bytes(versions[0])),
                                                                            ("Explain", as_bytes(versions[1]))])

    diff = open_diff(history, qtbot, 1, CURRENT)

    assert "current" in diff.title.lower() and "not saved" not in diff.title.lower(), diff.title
    assert "Load the numbers" in diff.title and when[0] in diff.title, diff.title
    assert_shows(rows(diff.left), versions[0], "left")
    assert_shows(rows(diff.right), HAND_WRITTEN, "right")
    diff.close()

    tab.doc.move_blocks({"manual": (-200, 40)})  # an unsaved change
    assert tab.doc.dirty
    diff = open_diff(history, qtbot, 1, CURRENT)

    assert "current (not saved)" in diff.title.lower(), diff.title
    assert "Load the numbers" in diff.title and when[0] in diff.title, diff.title
    shown_right = numbered(rows(diff.right))
    diff.close()
    menu(win, "Save").trigger()
    settle()
    assert not tab.doc.dirty and win.message_boxes == []
    assert shown_right == (tmp_path / "flows" / "report.yaml").read_text(encoding="utf-8").splitlines(), (
        "the right side is not what the tab holds")
    keys = [[line.split(":")[0] for line in lines if re.match(r"\w+:", line)] for lines in (HAND_WRITTEN, shown_right)]
    assert keys[1] == keys[0], f"the right side has the flow's keys in another order than its file: {keys}"


LONG = ("Explain why the nightly totals are read per region and not per store: " + "the stores report late, " * 8
        ).strip()


@pytest.mark.invariant("CD-3")
@pytest.mark.invariant("CD-1")
def test_d16_the_title_names_both_commits_message_and_time_also_a_long_message_and_while_a_search_filters(
        win, qtbot, tmp_path):
    """D16: "against previous version" on a commit: the title names it and the commit before it, each by its message
    and time as its card shows them, on one line, a long message too (its start at least); "against current version"
    names the commit and "current". With a search filtering the cards (the card under it is not the commit before),
    the previous version is still the commit before it in the history, on the left."""
    versions = [flow_lines(code(4)), flow_lines(code(5)), flow_lines(code(6))]
    messages = ["Load the numbers", f"{LONG}\n\nWhy: the stores send theirs after midnight.", "Tidy up"]
    _, when, history, _ = flow_history(win, qtbot, tmp_path / "flows", [(m, as_bytes(v))
                                                                        for m, v in zip(messages, versions)])

    diff = open_diff(history, qtbot, 0, PREVIOUS)

    title = diff.title
    assert "\n" not in title, title
    for part in ("Tidy up", when[2], LONG[:24], when[1]):
        assert part in title, f"the title does not name {part!r}: {title!r}"
    assert when[0] not in title, title
    diff.close()

    diff = open_diff(history, qtbot, 1, CURRENT)
    assert LONG[:24] in diff.title and when[1] in diff.title and "current" in diff.title.lower(), diff.title
    diff.close()

    history.type("Tidy|numbers")  # (in the messages of the last and the first commit only)
    assert len(history.cards()) == 2 and "Tidy up" in history.cards()[0] and "numbers" in history.cards()[1]
    diff = open_diff(history, qtbot, 0, PREVIOUS)
    assert when[1] in diff.title and when[0] not in diff.title, diff.title
    assert_shows(rows(diff.left), versions[1], "left")
    assert_shows(rows(diff.right), versions[2], "right")


# --- CD-2: marks, level rows, numbers, jumps, nothing typed, themes --------------------------------------------------

@pytest.mark.invariant("CD-2")
def test_d5_one_character_changed_marks_the_line_changed_and_only_that_character_inside_it(win, qtbot, tmp_path):
    """D5: one character of one line changed: that line is marked changed (blue) on both sides, and inside it only
    that character; no other line is marked."""
    old = flow_lines(code(20))
    new = list(old)
    new[CODE_AT + 7] = old[CODE_AT + 7].replace("region_7", "regiOn_7")
    at = old[CODE_AT + 7].index("region_7") + len("regi")
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(old)), ("Two", as_bytes(new))])

    diff = open_diff(history, qtbot, 0, PREVIOUS)

    left, right = rows(diff.left, characters=True), rows(diff.right, characters=True)
    assert_shows(left, old, "left")
    assert_shows(right, new, "right")
    line = CODE_AT + 8
    assert marks(diff.left, left) == {line: "changed"} and marks(diff.right, right) == {line: "changed"}
    (left_row,) = [r for r in left if r.number == line]
    (right_row,) = [r for r in right if r.number == line]
    assert left_row.marked == (at,), f"marked inside the line: {[left_row.text[i] for i in left_row.marked]}"
    assert right_row.marked == (at,), f"marked inside the line: {[right_row.text[i] for i in right_row.marked]}"


EDGES = {
    "lines added at the end": (flow_lines(code(12)), flow_lines(code(12), after=["# checked by Ana", "# and by Bo",
                                                                                 "# on Monday"])),
    "lines removed at the start": (flow_lines(code(12), before=["# made by hand", "# for the nightly report"]),
                                   flow_lines(code(12))),
    "an empty file against a full one": ([], flow_lines(code(12))),
}


@pytest.mark.invariant("CD-2")
@pytest.mark.parametrize("case", list(EDGES))
def test_d6_lines_added_at_the_end_removed_at_the_start_and_an_empty_file_against_a_full_one(win, qtbot, tmp_path,
                                                                                            case):
    """D6: only lines added at the end: they are marked added (green) on the right, with unnumbered filler lines level
    with them on the left; only lines removed at the start: marked removed (red) on the left, filler lines on the
    right; an empty file against a full one: every line of the full one added, the empty side only filler lines.
    Nothing else is marked."""
    old, new = EDGES[case]
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(old)), ("Two", as_bytes(new))])

    diff = open_diff(history, qtbot, 0, PREVIOUS)

    left, right = rows(diff.left), rows(diff.right)
    assert_shows(left, old, "left")
    assert_shows(right, new, "right")
    assert len(left) == len(right), "the two sides do not have as many rows"
    if case == "lines added at the end":
        assert marks(diff.left, left) == {}
        assert marks(diff.right, right) == {n: "added" for n in range(len(old) + 1, len(new) + 1)}
        assert [r.number for r in left[-3:]] == [None] * 3, "no filler lines level with the lines added"
    elif case == "lines removed at the start":
        assert marks(diff.left, left) == {1: "removed", 2: "removed"}
        assert marks(diff.right, right) == {}
        assert [r.number for r in right[:2]] == [None] * 2, "no filler lines level with the lines removed"
    else:
        assert marks(diff.right, right) == {n: "added" for n in range(1, len(new) + 1)}
        assert all(r.number is None for r in left), "the empty file's side numbers a line"


@pytest.mark.invariant("CD-2")
@pytest.mark.invariant("CD-7")
def test_d9_non_english_letters_and_an_emoji_before_a_change_mark_the_right_characters(win, qtbot, tmp_path):
    """D9: a line with letters of other languages (ü, ß, Cyrillic, Chinese) and an emoji before the one character
    changed, and a line whose changed character comes right after an emoji: in each, on both sides, the changed
    character and only it is marked (an emoji counts as one character, as you see it)."""
    words = 'label = "Grüße aus Zürich, Жар-птица 漢字 😀 total {}"'
    close = 'mark = "😀{}"'
    old = flow_lines([words.format("A"), *code(4), close.format("A")])
    new = flow_lines([words.format("B"), *code(4), close.format("B")])
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(old)), ("Two", as_bytes(new))])

    diff = open_diff(history, qtbot, 0, PREVIOUS)

    for side, side_rows, lines in ((diff.left, rows(diff.left, True), old), (diff.right, rows(diff.right, True), new)):
        assert numbered(side_rows) == lines
        changed = {CODE_AT + 1: "changed", CODE_AT + 6: "changed"}
        assert marks(side, side_rows) == changed
        for number in changed:
            (row,) = [r for r in side_rows if r.number == number]
            at = row.text.rindex('"') - 1  # the character changed
            assert at in row.marked, f"{row.text[at]!r} is not marked in {row.text!r}"
            wrong = [row.text[i] for i in row.marked if i != at and ord(row.text[i]) <= 0xFFFF]
            assert not wrong, f"{wrong} are marked too in {row.text!r}"


@pytest.mark.invariant("CD-2")
def test_d12_typing_in_either_side_changes_nothing(win, qtbot, tmp_path):
    """D12: in either side, the cursor in the indentation of a line of code: letters, Enter, Tab, Shift+Tab,
    Backspace, Delete, paste, cut, Tab and Shift+Tab over lines selected, all selected then a letter, and Ctrl+Z
    change neither side's text, numbers or marks, nor the flow's tab or its file."""
    old = flow_lines(code(10))
    new = flow_lines(code(10)[:4] + ["extra = read('south', day=0)"] + code(10)[5:])
    _, _, history, tab = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(old)), ("Two", as_bytes(new))])
    flow = tmp_path / "flows" / "report.yaml"
    file_before, data_before = flow.read_bytes(), copy.deepcopy(tab.doc.data)
    QApplication.clipboard().setText("pasted\ntext")

    diff = open_diff(history, qtbot, 0, PREVIOUS)

    texts = diff.texts()
    typed = {
        "letters": lambda to: QTest.keyClicks(to, "xyz"),
        "Enter": lambda to: QTest.keyClick(to, Qt.Key_Return),
        "Tab": lambda to: QTest.keyClick(to, Qt.Key_Tab),
        "Shift+Tab": lambda to: QTest.keyClick(to, Qt.Key_Backtab, Qt.ShiftModifier),
        "Backspace": lambda to: QTest.keyClick(to, Qt.Key_Backspace),
        "Delete": lambda to: QTest.keyClick(to, Qt.Key_Delete),
        "paste": lambda to: QTest.keySequence(to, QKeySequence(QKeySequence.Paste)),
        "cut": lambda to: (QTest.keyClick(to, Qt.Key_End, Qt.ShiftModifier),
                           QTest.keySequence(to, QKeySequence(QKeySequence.Cut))),
        "Tab over lines": lambda to: (QTest.keyClick(to, Qt.Key_Down, Qt.ShiftModifier),
                                      QTest.keyClick(to, Qt.Key_Down, Qt.ShiftModifier),
                                      QTest.keyClick(to, Qt.Key_Tab)),
        "Shift+Tab over lines": lambda to: (QTest.keyClick(to, Qt.Key_Down, Qt.ShiftModifier),
                                            QTest.keyClick(to, Qt.Key_Backtab, Qt.ShiftModifier)),
        "a letter over everything": lambda to: (QTest.keySequence(to, QKeySequence(QKeySequence.SelectAll)),
                                                QTest.keyClicks(to, "q")),
        "Ctrl+Z": lambda to: (QTest.keySequence(to, QKeySequence(QKeySequence.Undo)),
                              QTest.keySequence(to, QKeySequence(QKeySequence.Undo))),
    }
    for side in (diff.left, diff.right):
        assert side.document().findBlockByNumber(CODE_AT + 2).text().startswith("        ")

        def put_cursor():  # (as you click into the indentation of a line of code)
            focus(diff, qtbot, side)
            block = side.document().findBlockByNumber(CODE_AT + 2)
            cursor = QTextCursor(block)
            cursor.setPosition(block.position() + 4)
            QTest.mouseClick(side.viewport(), Qt.LeftButton, Qt.NoModifier, side.cursorRect(cursor).center())
            settle()

        put_cursor()
        before = [(r.number, r.text, kind(side, r)) for r in rows(side)]
        for name, keys in typed.items():
            put_cursor()
            keys(QApplication.focusWidget() or side)
            settle()
            assert diff.texts() == texts, f"{name} in the {side.objectName()} side changed the text"
        put_cursor()
        assert [(r.number, r.text, kind(side, r)) for r in rows(side)] == before, (
            f"typing in the {side.objectName()} side changed its numbers or marks")
    assert flow.read_bytes() == file_before and tab.doc.data == data_before and not tab.doc.dirty


THREE_CHANGES_OLD = flow_lines(code(120))
THREE_CHANGES_NEW = flow_lines([*code(120)[:4], code(120)[4].replace("read(", "reed("), *code(120)[5:60],
                                *code(2, "added"), *code(120)[60:104], *code(120)[107:]])


@pytest.mark.invariant("CD-2")
def test_d13_f7_shift_f7_and_the_buttons_jump_between_changes_and_stop_at_either_end_sides_scroll_together_level(
        win, qtbot, tmp_path):
    """D13: three changes far apart (one line changed, two added, three removed). The window opens at the first,
    "1 of 3 changes", Previous greyed; F7 (focus in either side) and Next go to the next change, Shift+F7 and Previous
    to the one before, each scrolled into view on both sides, the count following; at the last change Next is greyed
    and F7 stays there, at the first Shift+F7 stays there. Every row is level with the other side's (filler lines
    opposite the lines added and removed, unnumbered; each side numbers its own lines 1, 2, 3...). The wheel over one
    side scrolls both."""
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(THREE_CHANGES_OLD)),
                                                                     ("Two", as_bytes(THREE_CHANGES_NEW))])

    diff = open_diff(history, qtbot, 0, PREVIOUS)

    assert diff.count() == "1 of 3 changes", diff.count()
    for side in (diff.left, diff.right):
        assert wholly_in_view(side, CODE_AT + 4), "the window does not open at the first change"
    left, right = rows(diff.left), rows(diff.right)
    assert_shows(left, THREE_CHANGES_OLD, "left")
    assert_shows(right, THREE_CHANGES_NEW, "right")
    found = changes(diff, left, right)
    assert len(found) == 3, f"the changes found by their marks: {found}"
    for a, b in zip(left, right):
        if a.number is None:
            assert kind(diff.right, b) != "unchanged", "a filler line on the left is not opposite a line added"
        if b.number is None:
            assert kind(diff.left, a) != "unchanged", "a filler line on the right is not opposite a line removed"
    previous, next_ = diff.button(PREVIOUS_CHANGE), diff.button(NEXT_CHANGE)
    assert not previous.isEnabled() and next_.isEnabled()
    assert level(diff) > 0

    def at_change(n: int):
        assert diff.count() == f"{n} of 3 changes", diff.count()
        for side in (diff.left, diff.right):
            assert wholly_in_view(side, found[n - 1][0]), f"change {n} is not in view on the {side.objectName()} side"
        assert level(diff) > 0
        assert previous.isEnabled() == (n > 1) and next_.isEnabled() == (n < 3)

    focus(diff, qtbot, diff.left)
    key(diff.left, Qt.Key_F7)
    at_change(2)
    key(diff.left, Qt.Key_F7)
    at_change(3)
    key(diff.left, Qt.Key_F7)
    at_change(3)
    key(diff.left, Qt.Key_F7, Qt.ShiftModifier)
    at_change(2)
    QTest.mouseClick(previous, Qt.LeftButton)
    settle()
    at_change(1)
    focus(diff, qtbot, diff.right)
    key(diff.right, Qt.Key_F7, Qt.ShiftModifier)
    at_change(1)
    QTest.mouseClick(next_, Qt.LeftButton)
    settle()
    at_change(2)
    focus(diff, qtbot, diff.right)
    key(diff.right, Qt.Key_F7)
    at_change(3)

    key(diff.right, Qt.Key_F7, Qt.ShiftModifier)
    top = top_row(diff.left)
    wheel(diff.left, 2)
    assert top_row(diff.left) > top, "the wheel over the left side does not scroll it"
    assert top_row(diff.right) == top_row(diff.left), "scrolling the left side does not scroll the right one"
    assert level(diff) > 0
    top = top_row(diff.right)
    wheel(diff.right, -1)
    assert top_row(diff.right) < top, "the wheel over the right side does not scroll it"
    assert top_row(diff.left) == top_row(diff.right), "scrolling the right side does not scroll the left one"
    assert level(diff) > 0


# (the changed characters in plain words: the colour of a string or a number on a mark is not the mark's)
ALL_KINDS_OLD = flow_lines([*code(6), "region = EU", *code(6, "sum"), "drop_me = True", *code(4, "avg")])
ALL_KINDS_NEW = flow_lines([*code(6), "region = EMEA", *code(6, "sum"), *code(4, "avg"), "added = True"])


@pytest.mark.invariant("CD-2")
@pytest.mark.parametrize("first, then", [("Dark", "Light"), ("Light", "Dark")])
def test_d15_added_removed_and_changed_colours_read_well_in_light_and_dark_and_follow_the_theme(look_kept, win, qtbot,
                                                                                               tmp_path, first, then):
    """D15: with a theme chosen in Settings, the window is in that theme; a changed line is blue, a removed one red,
    an added one green, each unlike the side's background, its text reading well on it (4.5:1), and the changed
    characters inside the changed line in a colour of their own on which the text reads well too. The other theme
    chosen while the window is open: the same in it."""
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(ALL_KINDS_OLD)),
                                                                     ("Two", as_bytes(ALL_KINDS_NEW))])
    choose(win, first)
    settle()

    diff = open_diff(history, qtbot, 0, PREVIOUS)

    for name in (first, then):
        if name == then:
            choose(win, then)
            settle()
        dark = name == "Dark"
        assert not (off := not_in_theme(diff.window, dark=dark)), f"{name}: " + "\n".join(off)
        expected = {diff.left: {CODE_AT + 7: "changed", CODE_AT + 14: "removed"},
                    diff.right: {CODE_AT + 7: "changed", CODE_AT + 18: "added"}}
        for side, wanted in expected.items():
            side_rows = rows(side, characters=True)
            assert marks(side, side_rows) == wanted, f"{name}: {side.objectName()}"
            for number in wanted:
                row = next(r for r in side_rows if r.number == number)
                place = next(i for i, r in enumerate(side_rows) if r.number == number)
                side.verticalScrollBar().setValue(side.document().findBlockByNumber(place).firstLineNumber())
                settle()
                block = side.document().findBlockByNumber(place)
                rect = line_rect(side, block)
                assert strongest(side, rect, row.colour) >= TEXT, (
                    f"{name}: the text of a {kind(side, row)} line does not read on {row.colour.name()}")
                if kind(side, row) == "changed":
                    assert row.marked, f"{name}: no character is marked inside the changed line"
                    cursor = QTextCursor(block)
                    cursor.setPosition(block.position() + row.marked[0])
                    start = side.cursorRect(cursor)
                    cursor.setPosition(block.position() + row.marked[-1] + 1)
                    span = QRect(start.left() + 1, rect.top(), side.cursorRect(cursor).left() - start.left() - 2,
                                 rect.height())
                    image = side.viewport().grab().toImage()
                    inner = painted(image, span, 1, 1)
                    assert differs(inner, row.colour, 6), f"{name}: the changed characters are not marked"
                    assert strongest(side, span, inner) >= TEXT, (
                        f"{name}: the changed characters do not read on {inner.name()}")


# The approved mockup's code colours (light, dark) of a YAML key and of a quoted string.
SYNTAX = {"key": ("#871094", "#cf8e6d"), "string": ("#067d17", "#6aab73")}
SYNTAX_OLD = flow_lines([*code(4), "kept: str = 'East'", *code(3, "sum"), "area: str = 'North'", *code(3, "avg"),
                         "region_id: int = 1", *code(3, "max"), "gone: str = 'West'", *code(3, "min")])
SYNTAX_NEW = flow_lines([*code(4), "kept: str = 'East'", *code(3, "sum"), "area: str = 'South'", *code(3, "avg"),
                         "branch_id: int = 1", *code(3, "max"), *code(3, "min"), "added: str = 'West'"])


def inks(side, line: str, token: str) -> dict[str, QColor]:
    """The characters of `token` in the side's row showing `line`, by the colour painted behind them (its name): the
    colour most unlike it painted in their cells (their text's colour)."""
    block = side.document().begin()
    while block.isValid() and block.text() != f"        {line}":  # (as flow_lines indents the code)
        block = block.next()
    assert block.isValid(), f"{side.objectName()} shows no line {line!r}"
    side.verticalScrollBar().setValue(block.firstLineNumber())
    settle()
    assert wholly_in_view(side, block.blockNumber()), f"{side.objectName()}: {line!r} is not in view"
    image = side.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    found = {}
    start = block.text().index(token)
    for i in range(start, start + len(token)):
        first, last = QTextCursor(block), QTextCursor(block)
        first.setPosition(block.position() + i)
        last.setPosition(block.position() + i + 1)
        a, b = side.cursorRect(first), side.cursorRect(last)
        cell = QRect(a.left() + 1, a.top() + 1, b.left() - a.left() - 2, a.height() - 3)  # (not a change's edge)
        behind = painted(image, cell, 1, 1)
        r = QRect(int(cell.left() * ratio), int(cell.top() * ratio), int(cell.width() * ratio),
                  int(cell.height() * ratio))
        ink = max((image.pixelColor(x, y) for y in range(r.top(), r.bottom() + 1)
                   for x in range(r.left(), r.right() + 1)), key=lambda c: contrast(c, behind))
        if behind.name() not in found or contrast(ink, behind) > contrast(found[behind.name()], behind):
            found[behind.name()] = ink
    return found


@pytest.mark.invariant("CD-2")
def test_d15_yaml_keys_and_strings_in_the_mockups_colours_reading_well_on_every_mark_and_following_the_theme(
        look_kept, win, qtbot, tmp_path):
    """D15: the flow's YAML in the approved mockup's code colours: a key and a quoted string on an unchanged line in
    them; on an added, a removed and a changed line, and on the changed characters inside it, in a colour of the same
    hue that reads well there (4.5:1). Light chosen in Settings; then Dark, while the window is open."""
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(SYNTAX_OLD)),
                                                                     ("Two", as_bytes(SYNTAX_NEW))])
    choose(win, "Light")
    settle()

    diff = open_diff(history, qtbot, 0, PREVIOUS)

    # (side, line, its token looked at, the token's kind, how many marks are behind it: 0, the row's, or the row's
    # and the changed characters')
    looked = [(diff.left, "kept: str = 'East'", "kept", "key", 0),
              (diff.right, "kept: str = 'East'", "'East'", "string", 0),
              (diff.left, "gone: str = 'West'", "gone", "key", 1),
              (diff.left, "gone: str = 'West'", "'West'", "string", 1),
              (diff.right, "added: str = 'West'", "added", "key", 1),
              (diff.right, "added: str = 'West'", "'West'", "string", 1),
              (diff.left, "area: str = 'North'", "area", "key", 1),
              (diff.left, "area: str = 'North'", "'North'", "string", 2),
              (diff.right, "area: str = 'South'", "'South'", "string", 2),
              (diff.left, "region_id: int = 1", "region_id", "key", 2),
              (diff.right, "branch_id: int = 1", "branch_id", "key", 2)]
    for name in ("Light", "Dark"):
        if name == "Dark":
            choose(win, name)
            settle()
        dark = name == "Dark"
        for side, line, token, syntax, behind in looked:
            seen = inks(side, line, token)
            base = side.palette().color(QPalette.Base).name()
            where = f"{name}: {token} in {line!r} ({side.objectName()})"
            if behind == 0:
                assert list(seen) == [base], f"{where} is painted on {list(seen)}, not on the side's {base}"
            else:
                assert len(seen) == behind and base not in seen, f"{where} is painted on {list(seen)}"
            hue = QColor(SYNTAX[syntax][dark]).hslHue()
            for colour, ink in seen.items():
                assert ink.hslHue() >= 0 and abs((ink.hslHue() - hue + 180) % 360 - 180) <= 15, (
                    f"{where}: {ink.name()} on {colour}, not the hue of the {syntax} colour {SYNTAX[syntax][dark]}")
                assert contrast(ink, QColor(colour)) >= TEXT, (
                    f"{where}: {ink.name()} on {colour} reads at {contrast(ink, QColor(colour)):.2f}:1")


# --- CD-5: looking changes nothing -----------------------------------------------------------------------------------

def git_status(folder: Path) -> str:
    """`git status`, without refreshing the index (so that asking changes nothing either)."""
    done = subprocess.run([GIT, "-C", str(folder), "status", "--porcelain", "--untracked-files=all", "--ignored"],
                          capture_output=True, timeout=60, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    assert done.returncode == 0, done.stderr
    return done.stdout.decode("utf-8", "replace")


def every_file(folder: Path) -> dict[str, tuple[str, int]]:
    """Each file under `folder` (its .git folder too): the hash of its bytes and the time it was last written."""
    return {p.relative_to(folder).as_posix(): (hashlib.sha1(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.mark.invariant("CD-5")
def test_d11_opening_both_diffs_changes_no_file_no_commit_and_no_git_setting(win, qtbot, tmp_path, git_config):
    """D11: opening "against previous version" and "against current version" (the tab with no unsaved change, then
    with one) changes nothing: not the flow file's bytes or time, not any file of the repository (its .git/config,
    index, refs and objects), not `git status`, not the user's Git config; the unsaved change stays unsaved."""
    folder = tmp_path / "flows"
    versions = [flow_lines(code(5)), flow_lines(code(6)), flow_lines(code(7))]
    ids, _, history, tab = flow_history(win, qtbot, folder, [(f"Version {i + 1}", as_bytes(v))
                                                             for i, v in enumerate(versions)])
    (folder / "notes.txt").write_bytes(b"not committed\n")
    config = git_config.read_bytes()
    status = git_status(folder)
    files = every_file(folder)

    diff = open_diff(history, qtbot, 0, PREVIOUS)
    assert_shows(rows(diff.right), versions[2], "right")
    diff.close()
    diff = open_diff(history, qtbot, 1, CURRENT)
    diff.close()
    tab.doc.move_blocks({"manual": (-200, 40)})  # an unsaved change
    data = copy.deepcopy(tab.doc.data)
    diff = open_diff(history, qtbot, 1, CURRENT)
    assert "not saved" in diff.title.lower(), diff.title
    diff.close()

    assert every_file(folder) == files, "opening the diffs changed files of the repository"
    assert git_status(folder) == status
    assert git(folder, "rev-parse", "HEAD").strip() == ids[-1]
    assert git_config.read_bytes() == config, "opening the diffs changed the user's Git config"
    assert tab.doc.dirty and tab.doc.data == data, "opening the diffs saved or changed the unsaved change"
    clean(win)


# --- CD-6: Find and Zoom ---------------------------------------------------------------------------------------------

@pytest.mark.invariant("CD-6")
def test_d14_ctrl_f_searches_the_side_clicked_in_and_zoom_changes_both_sides_keeping_them_level(win, qtbot, tmp_path):
    """D14: Ctrl+F in the left side finds "north" only in the left version (3 times), in the right side only in the
    right one (5 times). Ctrl + with the pointer over the left side (the focus in the right one) makes both sides'
    lines taller alike, Ctrl - over the right side makes both smaller alike, Ctrl 0 gives both their normal size;
    each time the rows stay level."""
    norths = [f"n_{i} = read('north', day={i})" for i in range(5)]
    old = flow_lines([*code(3), *norths[:3], *code(4, "sum")])
    new = flow_lines([*code(3), *norths[:4], *code(4, "sum"), norths[4]])
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(old)), ("Two", as_bytes(new))])

    diff = open_diff(history, qtbot, 0, PREVIOUS)

    texts = diff.texts()
    for side, total in ((diff.left, 3), (diff.right, 5)):
        diff.window.raise_()
        bar = open_find(diff.window, qtbot, side)
        bar.type("north")
        assert bar.total() == total, f"Ctrl+F in the {side.objectName()} side found {bar.total()}, not {total}"
        bar.key(Qt.Key_Escape)
    assert diff.texts() == texts

    diff.window.setGeometry(QApplication.primaryScreen().availableGeometry())  # (the pointer can only be on a screen)
    diff.window.raise_()
    settle()
    normal = size(diff.left)
    assert size(diff.right) == normal
    focus(diff, qtbot, diff.right)
    pointer_over(diff.left, "left side")
    key(diff.right, Qt.Key_Equal, Qt.ControlModifier)
    bigger = (size(diff.left), size(diff.right))
    assert grown(bigger[0], normal) and bigger[0] == bigger[1], f"Ctrl + made the sides {bigger}, from {normal}"
    assert level(diff) > 0
    focus(diff, qtbot, diff.left)
    pointer_over(diff.right, "right side")
    key(diff.left, Qt.Key_Minus, Qt.ControlModifier)
    key(diff.left, Qt.Key_Minus, Qt.ControlModifier)
    smaller = (size(diff.left), size(diff.right))
    assert grown(normal, smaller[0]) and smaller[0] == smaller[1], f"Ctrl - made the sides {smaller}, from {normal}"
    assert level(diff) > 0
    key(diff.left, Qt.Key_0, Qt.ControlModifier)
    assert (size(diff.left), size(diff.right)) == (normal, normal)
    assert level(diff) > 0


# --- CD-7: every difference and nothing else, line ends, speed --------------------------------------------------------

@pytest.mark.invariant("CD-7")
def test_d7_no_newline_at_the_end_in_one_version_the_note_line_says_so(win, qtbot, tmp_path):
    """D7: two versions both ending with a newline: no note line; the next version without one at the end: against
    the version before it, and the oldest against it as the current version, the note line says the newline at the
    end differs."""
    v1 = flow_lines(code(6))
    v2 = flow_lines(code(7))
    v3 = as_bytes(v2 + ["# no newline after this"], last=False)
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(v1)), ("Two", as_bytes(v2)),
                                                                     ("Three", v3)])

    diff = open_diff(history, qtbot, 1, PREVIOUS)
    assert diff.note() == "", f"a note line shows: {diff.note()!r}"
    diff.close()

    for place, item in ((0, PREVIOUS), (2, CURRENT)):
        diff = open_diff(history, qtbot, place, item)
        assert re.search(r"newline at the end", diff.note(), re.IGNORECASE), f"{item}: the note line: {diff.note()!r}"
        diff.close()


@pytest.mark.invariant("CD-7")
def test_d8_windows_line_ends_in_one_version_the_note_line_says_so_and_lines_are_compared_by_their_text(win, qtbot,
                                                                                                        tmp_path):
    """D8: a version with Unix line ends, then one with Windows line ends and one line changed: the note line says the
    line ends differ (Windows and Unix); the sides show the lines without the line ends, and only the changed line is
    marked; the same against the current version."""
    old = flow_lines(code(8))
    new = flow_lines([*code(8)[:3], "changed = read('west', day=3)", *code(8)[4:]])
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(old)),
                                                                     ("Two", as_bytes(new, newline="\r\n"))])

    for place, item in ((0, PREVIOUS), (1, CURRENT)):
        diff = open_diff(history, qtbot, place, item)
        note = diff.note()
        assert re.search(r"line ends", note, re.IGNORECASE) and "Windows" in note and "Unix" in note, (
            f"{item}: the note line: {note!r}")
        left, right = rows(diff.left), rows(diff.right)
        assert_shows(left, old, "left")
        assert_shows(right, new, "right")
        assert marks(diff.left, left) == {CODE_AT + 4: "changed"}, item
        assert marks(diff.right, right) == {CODE_AT + 4: "changed"}, item
        diff.close()


def eleven_versions() -> list[list[str]]:
    """Eleven versions of a flow file's lines, oldest first, each changed from the one before in another way. No line
    is twice in a version and no line moves, so that any diff finding the fewest changes finds the same ones."""
    fresh = (f"fresh_{n} = read('extra', day={n})" for n in itertools.count())
    take = lambda n: [next(fresh) for _ in range(n)]  # noqa: E731
    c = code(40)
    files = [flow_lines(c)]

    def then(new_code, **kw):
        files.append(flow_lines(new_code, **kw))
        return new_code

    c = then([*c[:20], *take(1), *c[21:]])  # one line changed
    c = then([*take(3), *c])  # three lines added at the start of the code
    c = then(c[:-2])  # two removed at its end
    c = then([*c[:10], *take(1), *c[10:20], *c[21:30], *take(1), *c[31:]])  # added, removed and changed apart
    c = then([*c[:12], *take(2), *c[17:]])  # five lines replaced by two
    c = then([*c[:5], *take(6), *c[7:]])  # two lines replaced by six
    c = then([line if i % 4 else next(fresh) for i, line in enumerate(c)])  # every fourth line changed
    c = then(take(30), before=["# rewritten"])  # all the code replaced, a line added at the start of the file
    c = then([*c[:3], *take(2), *c[3:9], *take(1), *c[10:20], *take(2), *c[20:]])  # added next to a change
    then([*c[5:], *take(4)], name="nightly", after=["# the end"])  # removed, added, the name changed, a last line
    return files


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def git_changed(folder: Path, old: str, new: str) -> tuple[set[int], set[int]]:
    """The oracle: the lines (by number) of the older version `git diff` gives as removed, and those of the newer one
    it gives as added."""
    out = git(folder, "diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames", "--no-textconv", old, new, "--",
              "report.yaml")
    removed, added = set(), set()
    for hunk in HUNK.finditer(out):
        start, n = int(hunk[1]), int(hunk[2] if hunk[2] is not None else 1)
        removed.update(range(start, start + n))
        start, n = int(hunk[3]), int(hunk[4] if hunk[4] is not None else 1)
        added.update(range(start, start + n))
    return removed, added


@pytest.mark.invariant("CD-7")
def test_d10_ten_real_commit_pairs_mark_exactly_the_lines_git_diff_changes(win, qtbot, tmp_path):
    """D10: ten commits, each changing the flow file from the one before in another way (a line changed, lines added
    at the start, removed at the end, changes apart, blocks replaced by fewer and by more lines, every fourth line,
    everything, changes next to each other, the name): "against previous version" on each shows the two versions
    and marks on the left exactly the lines `git diff` gives as removed, on the right exactly those it gives as
    added: none more, none fewer."""
    folder = tmp_path / "flows"
    versions = eleven_versions()
    ids, _, history, _ = flow_history(win, qtbot, folder, [(f"Version {i + 1}", as_bytes(v))
                                                           for i, v in enumerate(versions)])
    assert history.ids(ids) == list(reversed(ids))

    wrong = []
    for place in range(10):
        new, old = len(ids) - 1 - place, len(ids) - 2 - place
        removed, added = git_changed(folder, ids[old], ids[new])
        assert removed or added
        diff = open_diff(history, qtbot, place, PREVIOUS)
        left, right = rows(diff.left), rows(diff.right)
        assert_shows(left, versions[old], "left")
        assert_shows(right, versions[new], "right")
        shown_removed, shown_added = set(marks(diff.left, left)), set(marks(diff.right, right))
        if shown_removed != removed or shown_added != added:
            wrong.append(f"version {old + 1} -> {new + 1}: on the left {sorted(shown_removed - removed)} marked more, "
                         f"{sorted(removed - shown_removed)} fewer; on the right {sorted(shown_added - added)} more, "
                         f"{sorted(added - shown_added)} fewer")
        diff.close()
    assert not wrong, "\n".join(wrong)


@pytest.mark.invariant("CD-7")
def test_d8_both_versions_mix_line_ends_on_other_lines_the_note_line_says_so(win, qtbot, tmp_path):
    """D8 (review P2): both versions mix Windows and Unix line ends, on other lines: on the left the first line ends
    in Windows' and the rest in Unix', on the right the last line in Windows' and the rest in Unix'; the text is the
    same. `git diff` gives those two lines as changed: against the previous version and as the oldest against the
    current one, the window does not say "No differences" and the note line says the line ends differ (Windows and
    Unix)."""
    lines = flow_lines(code(6))
    left = (lines[0] + "\r\n" + "\n".join(lines[1:]) + "\n").encode("utf-8")
    right = ("\n".join(lines[:-1]) + "\n" + lines[-1] + "\r\n").encode("utf-8")
    folder = tmp_path / "flows"
    ids, _, history, _ = flow_history(win, qtbot, folder, [("One", left), ("Two", right)])
    assert git_changed(folder, ids[0], ids[1]) == ({1, len(lines)}, {1, len(lines)})

    for place, item in ((0, PREVIOUS), (1, CURRENT)):
        diff = open_diff(history, qtbot, place, item)
        assert not any("No differences" in said for said in diff.says()), f"{item}: {diff.says()}"
        note = diff.note()
        assert note == "The line ends differ: both versions mix Windows (CRLF) and Unix (LF), on other lines.", (
            f"{item}: the versions differ in line ends (window: {diff.count()!r}), the note line: {note!r}")
        diff.close()


# What str.splitlines() ends a line at besides "\n" and "\r\n", and the two characters Qt's text document also starts
# a new row at (U+FDD0, U+FDD1); git ends a line at "\n" only.
BREAKS = "\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\ufdd0\ufdd1"


def broken_line(i: int, inside: str) -> str:
    """A comment line holding `inside` (e.g. one of BREAKS) in its middle."""
    return f"# {i}: one line to git{inside}# {i}: still that line"


def shows_line(text: str, line: str) -> bool:
    """A row's `text` shows the whole `line`: its parts around the BREAKS in it, in order, each of those shown as at
    least one character."""
    parts = re.split(f"[{BREAKS}]", line)
    return re.fullmatch(".+?".join(map(re.escape, parts)), text, re.DOTALL) is not None


@pytest.mark.invariant("CD-7")
def test_d10_a_lone_cr_and_the_other_breaks_inside_a_line_are_numbered_and_marked_as_git_diff_does(win, qtbot,
                                                                                                    tmp_path):
    """D10 (review C5): above the flow, lines holding inside them a lone carriage return, a vertical tab, a form feed,
    \\x1c, \\x1d, \\x1e, NEL, U+2028 or U+2029 (str.splitlines() breaks a line at each, git at none) or U+FDD0 or
    U+FDD1 (Qt's text document starts a new row at each), each followed by a plain line. The next version (the current
    one: only characters YAML reads) splits the carriage return's line in two at a "\\n", puts another of these
    characters in four of the others, removes the \\x1e and U+FDD0 ones, keeps four, adds one holding U+FDD0 after the
    U+FDD1 one and changes a line of code. Against the previous version and as the oldest against the current one,
    each side shows its version's lines as git counts them, numbered 1 to the last, each whole on its row; the two
    sides have as many rows and the plain lines both versions keep are level; and it marks on the left exactly the
    lines `git diff` gives as removed, on the right exactly those it gives as added."""
    def above(broken: list[list[str]]) -> list[str]:
        return [line for i, lines in enumerate(broken) for line in [*lines, f"# kept {i}"]]

    old_broken = [[broken_line(i, c)] for i, c in enumerate(BREAKS)]
    new_broken = [broken_line(0, "\n").split("\n"), [broken_line(1, "\r")], [broken_line(2, "\x85")],
                  [broken_line(3, "\u2028")], [broken_line(4, "\u2029")], [], *old_broken[6:9], [],
                  [*old_broken[10], broken_line(11, "\ufdd0")]]
    c = code(8)
    old = flow_lines(c, before=above(old_broken))
    new = flow_lines([*c[:4], "changed = read('west', day=4)", *c[5:]], before=above(new_broken))
    folder = tmp_path / "flows"
    ids, _, history, _ = flow_history(win, qtbot, folder, [("One", as_bytes(old)), ("Two", as_bytes(new))])
    removed, added = git_changed(folder, ids[0], ids[1])
    assert len(removed) == 8 and len(added) == 8, (removed, added)

    for place, item in ((0, PREVIOUS), (1, CURRENT)):
        diff = open_diff(history, qtbot, place, item)
        both = []
        for side, lines, changed, which in ((diff.left, old, removed, "left"), (diff.right, new, added, "right")):
            side_rows = rows(side)
            both.append(side_rows)
            numbers = [r.number for r in side_rows if r.number is not None]
            assert numbers == list(range(1, len(lines) + 1)), (
                f"{item}: the {which} side numbers {len(numbers)} lines, git counts {len(lines)} (it ends a line at "
                f'"\\n" only)')
            whole = [(r.number, r.text) for r in side_rows
                     if r.number is not None and not shows_line(r.text, lines[r.number - 1])]
            assert not whole, f"{item}: these rows of the {which} side do not show their line whole: {whole}"
            assert all(r.text == "" for r in side_rows if r.number is None), f"a {which} filler line has text"
            shown = set(marks(side, side_rows))
            assert shown == changed, (f"{item}: on the {which} side {sorted(shown - changed)} marked more, "
                                      f"{sorted(changed - shown)} fewer than `git diff` gives")
        kept = [{r.text: n for n, r in enumerate(side_rows) if r.text.startswith("# kept ")} for side_rows in both]
        assert len(both[0]) == len(both[1]) and kept[0] == kept[1], (
            f"{item}: the left side has {len(both[0])} rows, the right {len(both[1])}; the lines both versions keep "
            f"are on these rows (from 0): on the left {kept[0]}, on the right {kept[1]}")
        diff.close()


def two_thousand_lines(case: str) -> tuple[list[str], list[str]]:
    old = flow_lines(code(1984))
    assert len(old) == 2000
    if case == "many changes":
        lines = code(1984)
        new_code = []
        for i, line in enumerate(lines):
            if i % 97 == 50:
                continue  # removed
            new_code.append(line.replace("read(", "load(") if i % 10 == 3 else line)
            if i % 89 == 20:
                new_code.append(f"added_{i} = 0")
        return old, flow_lines(new_code)
    return old, [f"# line {i} of another file altogether" for i in range(2000)]


@pytest.mark.invariant("CD-7")
@pytest.mark.parametrize("case", ["many changes", "the whole file replaced"])
def test_d4_a_flow_of_2000_lines_opens_within_2_seconds(win, qtbot, tmp_path, case):
    """D4: a flow file of 2,000 lines with many changes (every tenth line changed, some lines removed, some added), or
    replaced by another one altogether: from the click on "against previous version" to the window showing both
    versions takes at most 2 seconds."""
    old, new = two_thousand_lines(case)
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(old)), ("Two", as_bytes(new))])

    diff = open_diff(history, qtbot, 0, PREVIOUS)
    qtbot.waitUntil(lambda: all(side.document().blockCount() >= 2000 for side in (diff.left, diff.right)),
                    timeout=30_000)
    diff.window.grab()  # (painted)
    took = time.monotonic() - diff.clicked

    assert [line for line in diff.left.toPlainText().split("\n") if line] == old
    assert [line for line in diff.right.toPlainText().split("\n") if line] == new
    assert took <= 2.0, f"the window took {took:.2f} s to show the diff"


# --- what mutation testing found the tests above let through (docs/promises/source-diff.mutants.yaml) ---------------

def header(diff, side) -> QLabel:
    """The line over `side` naming its version: the label shown just above it."""
    top = side.mapTo(diff.window, QPoint(0, 0))
    found = [label for label in diff.window.findChildren(QLabel) if label.isVisible() and not label.objectName()
             and abs(label.mapTo(diff.window, QPoint(0, 0)).x() - top.x()) <= 1]
    assert len(found) == 1, f"over the {side.objectName()} side: {[label.text() for label in found]}"
    return found[0]


def in_window(diff, widget) -> QRect:
    return QRect(widget.mapTo(diff.window, QPoint(0, 0)), widget.size())


def strip_of(diff):
    """The strip between the two sides."""
    left, right = in_window(diff, diff.left), in_window(diff, diff.right)
    strip = diff.window.childAt(QPoint((left.right() + right.left()) // 2, left.center().y()))
    assert strip is not None and strip not in (diff.left, diff.right), "nothing is between the two sides"
    return strip


@pytest.mark.invariant("CD-1")
@pytest.mark.invariant("CD-2")
@pytest.mark.invariant("CD-3")
def test_d16_the_title_and_the_line_over_each_side_name_each_version_exactly_and_the_parts_are_side_by_side(
        win, qtbot, tmp_path):
    """D16: the title is "report.yaml:  "<message>", <time>  ↔  "<message>", <time>" ("current" or "current (not
    saved)" for the tab's version), a message of 60 characters whole, of 61 its first 59 and "…"; over each side its
    version: "<message>"  ·  <time>  ·  <its short id> (7 characters, as git gives it), "Current" or "Current (not
    saved)", in plain text (a message holding <b> shows it). The buttons say "Previous change (Shift+F7)" and "Next
    change (F7)" beside an up and a down arrow. Each line is over its side; the left side, the strip between and the
    right side are side by side, level."""
    messages = ["Load <b>the</b> numbers", "Read the nightly totals per region, not per store, as agreed",
                "Read the nightly totals per region, not per store, as agreed!"]
    assert [len(m) for m in messages[1:]] == [60, 61]
    versions = [flow_lines(code(4)), flow_lines(code(5)), flow_lines(code(6))]
    ids, when, history, tab = flow_history(win, qtbot, tmp_path / "flows", [(m, as_bytes(v))
                                                                            for m, v in zip(messages, versions)])
    cut = messages[2][:59] + "…"

    diff = open_diff(history, qtbot, 0, PREVIOUS)
    assert diff.title == f'report.yaml:  "{messages[1]}", {when[1]}  ↔  "{cut}", {when[2]}'
    assert header(diff, diff.left).text() == f'"{messages[1]}"  ·  {when[1]}  ·  {ids[1][:7]}'
    assert header(diff, diff.right).text() == f'"{cut}"  ·  {when[2]}  ·  {ids[2][:7]}'
    for name, text, arrow in ((PREVIOUS_CHANGE, "Previous change (Shift+F7)", Qt.UpArrow),
                              (NEXT_CHANGE, "Next change (F7)", Qt.DownArrow)):
        button = diff.button(name)
        assert button.text() == text and button.arrowType() == arrow, (button.text(), button.arrowType())
        assert text in [t.strip() for _, _, t in drawn_texts(button)], f"{name} does not show {text!r}"
    diff.close()

    diff = open_diff(history, qtbot, 2, CURRENT)
    assert diff.title == f'report.yaml:  "{messages[0]}", {when[0]}  ↔  current'
    left_header = header(diff, diff.left)
    assert left_header.text() == f'"{messages[0]}"  ·  {when[0]}  ·  {ids[0][:7]}'
    assert "<b>the</b>" in "".join(t for _, _, t in drawn_texts(left_header)), "the message's <b> is not shown"
    assert header(diff, diff.right).text() == "Current"
    left, right, strip = in_window(diff, diff.left), in_window(diff, diff.right), in_window(diff, strip_of(diff))
    for side, rect in ((diff.left, left), (diff.right, right)):
        assert in_window(diff, header(diff, side)).bottom() < rect.top(), f"{side.objectName()}'s line is not over it"
    assert left.right() < strip.left() and strip.right() < right.left(), (left, strip, right)
    assert left.top() == strip.top() == right.top() and left.height() == strip.height() == right.height(), (
        left, strip, right)
    diff.close()

    tab.doc.move_blocks({"manual": (-200, 40)})  # an unsaved change
    diff = open_diff(history, qtbot, 2, CURRENT)
    assert diff.title == f'report.yaml:  "{messages[0]}", {when[0]}  ↔  current (not saved)'
    assert header(diff, diff.right).text() == "Current (not saved)"
    clean(win)


NOTE_LINES = flow_lines(code(6))
MIXED = (NOTE_LINES[0] + "\r\n" + "\n".join(NOTE_LINES[1:]) + "\n").encode("utf-8")
# Oldest first: (its bytes, what the note line says against the version before it, the change count there).
NOTES = [(as_bytes(NOTE_LINES), None, None),
         (as_bytes(NOTE_LINES, "\r\n"), "The line ends differ: Unix (LF) on the left, Windows (CRLF) on the right.",
          "No changed lines"),
         (as_bytes(NOTE_LINES, "\r\n", last=False),
          "The newline at the end differs: the left version ends with one, the other does not.", "1 of 1 changes"),
         (as_bytes(NOTE_LINES), "The line ends differ: Windows (CRLF) on the left, Unix (LF) on the right. The newline "
          "at the end differs: the right version ends with one, the other does not.", "1 of 1 changes"),
         (MIXED, "The line ends differ: Unix (LF) on the left, Windows (CRLF) and Unix (LF) mixed on the right.",
          "No changed lines"),
         (b"", "", "1 of 1 changes"),
         (as_bytes(NOTE_LINES, "\r\n"), "", "1 of 1 changes")]


@pytest.mark.invariant("CD-7")
@pytest.mark.invariant("CD-4")
def test_d7_d8_the_note_line_says_which_line_ends_and_which_newline_at_the_end_differ_and_an_empty_or_gone_file(
        win, qtbot, tmp_path, monkeypatch):
    """D7, D8: against the version before, the note line says in words how the line ends differ (Unix, Windows, or
    both mixed, on which side) and which version ends with a newline, both when both differ; versions that differ in
    line ends only say "No changed lines"; an empty file has no line ends: against it, or it against another, the note
    says nothing. In a window too narrow for the note on one line, the note wraps. The flow's file emptied: "against
    current version" on the empty commit says "No differences", Previous and Next greyed. The file gone: a message
    over Commit History says it cannot be read, and no window opens."""
    folder = tmp_path / "flows"
    _, _, history, _ = flow_history(win, qtbot, folder, [(f"Version {i + 1}", data)
                                                         for i, (data, _, _) in enumerate(NOTES)])
    for n in range(1, len(NOTES)):
        diff = open_diff(history, qtbot, len(NOTES) - 1 - n, PREVIOUS)
        assert (diff.note(), diff.count()) == NOTES[n][1:], f"version {n} -> {n + 1}"
        if n == 3:
            diff.window.resize(700, diff.window.height())
            settle()
            note = diff.window.findChild(QLabel, NOTE)
            assert diff.window.width() == 700, "the window cannot be narrower than the note on one line"
            # (taller than the note on one line: two lines are not twice one line's rounded height on every OS)
            assert note.height() > note.heightForWidth(10 ** 6), "the note does not wrap in a narrow window"
        diff.close()

    (folder / "report.yaml").write_bytes(b"")
    diff = open_diff(history, qtbot, 1, CURRENT)
    assert diff.count() == "No differences" and diff.note() == ""
    assert not diff.button(PREVIOUS_CHANGE).isEnabled() and not diff.button(NEXT_CHANGE).isEnabled()
    diff.close()

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a) or QMessageBox.Ok))
    (folder / "report.yaml").unlink()
    listed, clicked = right_click_card(history, qtbot, 0, CURRENT)
    assert clicked and diff_windows() == []
    assert len(warned) == 1 and warned[0][0] is history.window, warned
    assert warned[0][1] == "Cannot show the source diff", warned
    assert warned[0][2].startswith("report.yaml cannot be read: "), warned
    (folder / "report.yaml").write_bytes(NOTES[-1][0])


def colour_at(image, x: int, y: int) -> QColor:
    ratio = image.devicePixelRatio()
    return image.pixelColor(int(x * ratio), int(y * ratio))


def row_top(side, n: int) -> int:
    """Where the side's viewport shows the top of its row `n` (from 0)."""
    return line_rect(side, side.document().findBlockByNumber(n)).top()


def change_painted(side, image, xs, first: int, last: int, fill: QColor, dy: int = 0, filler: bool = False) -> list:
    """What is wrong with how the change of rows `first` to `last` (from 0) of `side` is painted in `image` (its
    viewport's, or the strip's: `dy` lower), at the columns `xs`: along its top and its bottom, straight across, a line
    in the change's colour; between its rows no such line, and (unless they are the side's filler lines) its rows in
    `fill`."""
    wanted = kind(side, Row(None, "", fill, None))
    wrong = []
    for x in xs:
        edges = [row_top(side, first), row_top(side, last + 1) - 1]
        inner = [y for n in range(first + 1, last + 1) for y in (row_top(side, n) - 1, row_top(side, n))]
        for y in edges + inner:
            c = colour_at(image, x, y + dy)
            line = differs(c, fill, 6) and kind(side, Row(None, "", c, None)) == wanted
            if line != (y in edges) or (y in inner and not filler and differs(c, fill, 6)):
                wrong.append(f"{'edge' if y in edges else 'inside'} at x {x}, y {y}: {c.name()} (fill {fill.name()})")
    return wrong


def hatched(side, image, n: int) -> list[str]:
    """What is wrong with the filler line `n` (from 0) of `side` as hatched in `image`: inside it (its edges aside)
    only the side's Base colour and the mockup's hatching colour, both."""
    base = side.palette().color(QPalette.Base)
    hatch = QColor(HATCH_COLOURS[int(base.lightness() < 128)])
    seen = {colour_at(image, x, y).name() for y in range(row_top(side, n) + 2, row_top(side, n + 1) - 2)
            for x in range(side.viewport().width())}
    other = [c for c in seen if differs(QColor(c), base, 6) and differs(QColor(c), hatch, 6)]
    found = [any(not differs(QColor(c), want, 6) for c in seen) for want in (base, hatch)]
    return [] if found == [True, True] and not other else [f"row {n + 1}: {sorted(seen)}"]


HATCH_COLOURS = ("#d4d4d4", "#3a3c41")  # the approved mockup's (light, dark)
PAINT_OLD = flow_lines(code(40), before=["# made by hand", "# for the nightly report"])
PAINT_NEW = flow_lines([*code(40)[:8], *(line.replace("read(", "load(") for line in code(40)[8:11]), *code(40)[11:21],
                        *code(2, "added"), *code(40)[21:]])
# The changes' rows (from 0, first and last): 2 removed at the start, 3 changed, 2 added.
PAINT_CHANGES = [(0, 1), (CODE_AT + 10, CODE_AT + 12), (CODE_AT + 23, CODE_AT + 24)]


@pytest.mark.invariant("CD-2")
def test_d13_d15_each_change_edged_top_and_bottom_filler_lines_hatched_the_strip_joining_level_numbers_right_aligned(
        win, qtbot, tmp_path):
    """D13, D15: two lines removed at the start, three changed, two added. Each change has a line in its colour along
    its top and its bottom, straight across its rows, none between them; a numbered row is in the change's fill to
    its left edge, a filler line hatched in the mockup's grey. The strip between the sides is painted, row by row and
    level with them, in each change's fill (filler lines too), edged as on the sides, with a straight line down each
    of its sides (on a screen at 200 % too); it follows when the sides scroll. Line numbers are right-aligned inside
    their margin. The window opens at the first change (the top of the file); hidden and shown again it stays where
    it was scrolled; Next puts the next change a third of the way down."""
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", as_bytes(PAINT_OLD)),
                                                                     ("Two", as_bytes(PAINT_NEW))])
    diff = open_diff(history, qtbot, 0, PREVIOUS)
    left, right, strip = diff.left, diff.right, strip_of(diff)
    assert top_row(left) == 0 and top_row(right) == 0, "the window does not open at the top, its first change"
    numbers = [(x, text.strip()) for x, _, text in drawn_texts(left) if text.strip().isdigit()
               and x < left.viewport().mapTo(left, QPoint(0, 0)).x()]
    ends = {round(x + left.fontMetrics().horizontalAdvance(n)) for x, n in numbers}
    assert {len(n) for _, n in numbers} == {1, 2} and max(ends) - min(ends) <= 1, f"numbers not right-aligned: {ends}"
    assert max(ends) <= left.viewport().mapTo(left, QPoint(0, 0)).x(), f"numbers drawn past their margin: {ends}"

    def look(changes) -> list[str]:
        wrong = []
        strip_image = strip.grab().toImage()
        dy = left.viewport().mapTo(diff.window, QPoint(0, 0)).y() - strip.mapTo(diff.window, QPoint(0, 0)).y()
        for first, last in changes:
            numbered = left if left.document().findBlockByNumber(first).text() and not right.document(
            ).findBlockByNumber(first).text() else right
            image = {s: s.viewport().grab().toImage() for s in (left, right)}
            fill = painted(image[numbered], QRect(0, row_top(numbered, first) + 2, numbered.viewport().width(), 4))
            for side in (left, right):
                filler = side is not numbered and not side.document().findBlockByNumber(first).text()
                width = side.viewport().width()
                wrong += [f"{side.objectName()}: {w}" for w in change_painted(
                    side, image[side], [0, width - 40, width - 1], first, last, fill, filler=filler)]
                if filler:
                    wrong += [f"{side.objectName()}: {w}" for n in range(first, last + 1)
                              for w in hatched(side, image[side], n)]
            wrong += [f"strip: {w}" for w in change_painted(left, strip_image, [1, strip.width() // 2,
                                                                                strip.width() - 2], first, last,
                                                            fill, dy=dy)]
        mid = strip.palette().mid().color()
        scaled = QImage(strip.width() * 2, strip.height() * 2, QImage.Format_ARGB32_Premultiplied)
        scaled.setDevicePixelRatio(2)
        strip.render(scaled)  # (as on a screen at 200 %, where a side line that leans by a pixel shows)
        wrong += [f"strip's side line at x {x}, y {y} ({image.devicePixelRatio():g}x)"
                  for image in (strip_image, scaled) for x in (0, strip.width() - 1)
                  for y in (0, strip.height() // 2, strip.height() - 1) if differs(colour_at(image, x, y), mid, 6)]
        return wrong

    assert not (wrong := look(PAINT_CHANGES[:2])), "\n".join(wrong)
    wheel(left, 2)
    scrolled = top_row(left)
    assert scrolled > 0
    diff.window.hide()
    settle()
    diff.window.show()
    settle()
    assert top_row(left) == scrolled, "shown again, the window jumps back to the first change"

    QTest.mouseClick(diff.button(NEXT_CHANGE), Qt.LeftButton)
    settle()
    assert diff.count() == "2 of 3 changes"
    height = right.viewport().height()
    at = row_top(right, PAINT_CHANGES[1][0])
    assert abs(at - height / 3) <= 1.5 * line_rect(right, right.document().begin()).height(), (
        f"the change is {at} px down of {height}, not a third of the way down")
    assert not (wrong := look(PAINT_CHANGES[1:])), "\n".join(wrong)
    shot = QApplication.primaryScreen().grabWindow(diff.window.winId()).toImage()
    fresh, place = strip.grab().toImage(), strip.mapTo(diff.window, QPoint(0, 0))
    stale = [y for y in range(strip.height()) if shot.pixelColor(place.x() + strip.width() // 2, place.y() + y)
             != fresh.pixelColor(strip.width() // 2, y)]
    assert not stale, f"the strip on screen is not what it paints now that the sides scrolled (rows {stale[:5]}...)"


# A comment of 296 characters: after its first ones, only letters each found more than 3 times in it (difflib's
# autojunk would take them as junk, and the line as changed from there on).
COMMENT = ("the stores send their totals late so the totals in the dash are read later than the rest and the last "
           "stores that send theirs later still are added to the totals in the last one that then ends and the totals "
           "are sent on as one total to the head and noted there as the totals read")
RULE = "print('-' * 40)"  # a line found many times in the flow (also more than 1% of its lines: autojunk's "popular")
SECTIONS = [line for k in range(5) for line in [*code(40, f"part{k}"), RULE, RULE, RULE]]
LINES_OLD = flow_lines(["# Note 1 " + COMMENT, "region = 'north'", "x = 1  # ==========", *SECTIONS])
LINES_NEW = flow_lines(["# Note 2 " + COMMENT, "region = 'far north'", "x = 2  # ==========", *SECTIONS[:40],
                        "x_added = 1", *SECTIONS[40:43], "y_added = 2", *SECTIONS[43:]], before=["# Zürich"])


def marked_cells(side, block, image, row_colour: QColor, indexes) -> list[int]:
    """Which of the characters `indexes` of the row `block` are painted in another colour than the row."""
    found = []
    for i in indexes:
        first, last = QTextCursor(block), QTextCursor(block)
        first.setPosition(block.position() + i)
        last.setPosition(block.position() + i + 1)
        a, b = side.cursorRect(first), side.cursorRect(last)
        if differs(painted(image, QRect(a.left() + 1, a.top(), b.left() - a.left() - 2, a.height()), 1, 1),
                   row_colour, 6):
            found.append(i)
    return found


@pytest.mark.invariant("CD-2")
@pytest.mark.invariant("CD-7")
def test_d9_d10_long_lines_repeated_lines_a_byte_not_utf8_and_characters_added_inside_a_line_are_marked_as_changed(
        win, qtbot, tmp_path):
    """D9, D10: the older version starts with a line holding a byte that is not UTF-8 (Zürich in Latin-1): it shows,
    the byte as \\xfc. A comment of 296 characters with one character changed near its start: that character alone
    is marked, and the line stays one row, the side scrolling sideways. "far " added inside a line: marked on the
    right, nothing on the left. In a flow of 236 lines with a line repeated 15 times, a line added each side of three
    such lines: only those two lines are marked, as git diff gives them. On a changed row ending in a comment, the
    comment's last character is in the same shade as the rest."""
    old_bytes = b"# Z\xfcrich\n" + as_bytes(LINES_OLD)
    _, _, history, _ = flow_history(win, qtbot, tmp_path / "flows", [("One", old_bytes),
                                                                     ("Two", as_bytes(LINES_NEW))])
    diff = open_diff(history, qtbot, 0, PREVIOUS)

    left, right = rows(diff.left), rows(diff.right)
    assert left[0].text == "# Z\\xfcrich" and left[0].number == 1, left[0]
    at = CODE_AT + 1  # (the row of the first line of code, from 0, under the line of Zürich)
    assert marks(diff.left, left) == {n: "changed" for n in (1, at + 1, at + 2, at + 3)}
    added = [LINES_NEW.index(f"        {line}") + 1 for line in ("x_added = 1", "y_added = 2")]
    assert marks(diff.right, right) == {**{n: "changed" for n in (1, at + 1, at + 2, at + 3)},
                                        **{n: "added" for n in added}}

    for side, far in ((diff.left, []), (diff.right, [18, 19, 20, 21])):
        side.verticalScrollBar().setValue(0)
        settle()
        image = side.viewport().grab().toImage()
        rects = {n: line_rect(side, side.document().findBlockByNumber(n)) for n in (at, at + 1, at + 2)}
        colours = {n: painted(image, QRect(0, r.top() + 2, side.viewport().width(), 4)) for n, r in rects.items()}
        long_row = side.document().findBlockByNumber(at)
        assert long_row.layout().lineCount() == 1 and side.horizontalScrollBar().maximum() > 0, "the long line wraps"
        assert marked_cells(side, long_row, image, colours[at], range(10, 30)) == [15], side.objectName()
        north = side.document().findBlockByNumber(at + 1)
        assert marked_cells(side, north, image, colours[at + 1], range(len(north.text()))) == far, side.objectName()
        rule = side.document().findBlockByNumber(at + 2)
        signs = [i for i in range(rule.text().index("#"), len(rule.text())) if rule.text()[i] == "="]
        inks = []
        for i in signs:
            first, last = QTextCursor(rule), QTextCursor(rule)
            first.setPosition(rule.position() + i)
            last.setPosition(rule.position() + i + 1)
            a, b = side.cursorRect(first), side.cursorRect(last)
            inks.append(strongest(side, QRect(a.left() + 1, a.top() + 1, b.left() - a.left() - 2, a.height() - 3),
                                  colours[at + 2]))
        assert inks[-1] >= 0.9 * max(inks[:-1]), f"{side.objectName()}: the comment's signs read at {inks}"
