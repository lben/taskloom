"""Name/value rows in Properties (docs/promises/rows.yaml, rows AP-1..AP-5, agreed with the maintainer on 2026-11-16):
a row is deleted by selecting it and pressing Delete, or by right-click and Delete row, and still by clearing its name;
a row's name and value are changed in place by double-clicking the cell, and a grey hint under the list says how to
change and delete rows; each change is one undo step and the flow file holds exactly what the list shows; a hidden
value (secret:) stays hidden while its row is edited; two rows with the same name in API Request's lists are both kept
and both sent, in the list's order.

Each case of the stage's table of combinations (A1-A13) runs in each of the seven lists where it applies: API Request's
Headers, Params, Form and Parts; Check Table's Column types and Allowed values; Statistics' Custom statistics. Through
the real editor window (the `win` fixture): a flow file opened, its block selected, its list clicked, double-clicked,
right-clicked and typed into with the mouse and keys, File > Save, Edit > Undo and Redo, Import from cURL; then the flow
file read back, and for AP-5 the flow run with the real `taskloom run` against a web server on this computer started by
the test (never the internet). The credential store is the tests' file one (the keyring is the fail one).

What the tests take as given (the promises leave it to the change):
- each list is a table (an item view) on its setting's row of Properties, its columns headed Name and Value (and its
  ticks); the line to add a row (name, the list's separator, value, then Enter) and the hint are text boxes and texts
  of Properties under the table, above the next setting; an empty list is its table with no rows;
- double-clicking a cell opens a text box in it (as Qt's item views do), where Ctrl+A selects its text;
- the right-click menu's item to delete the row under the pointer starts with "Delete row";
- the hint mentions double-clicking and Delete, in any words; grey: painted fainter than the setting's name;
- a header sent twice may arrive as two header lines or as one with its values joined by commas, as HTTP allows.
"""

import email
import email.policy
import html
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex, QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QAction, QCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFormLayout, QHeaderView, QLabel,  # noqa: E402
                               QLineEdit, QWidget)

from taskloom.blocks.servers import FORM_BODY, MULTIPART  # noqa: E402
from tests.test_api_request import PROXY_VARIABLES  # noqa: E402
from tests.test_curl_import import editing, fill_in, new_api  # noqa: E402
from tests.test_paths_editor_editing import flow_file, save_as, select_only, settle  # noqa: E402
from tests.test_theme import in_place, strongest  # noqa: E402

API, CHECK, STATS = "servers.api_request", "checks.check_table", "data.statistics"
BLOCK = "rows"  # the block whose list is under test, in every flow opened from a file
URL = "http://127.0.0.1:9/orders"  # (nothing is sent to it: only AP-5's flows run, against the test's own server)
DELETE_ROW = "Delete row"


class List(NamedTuple):
    label: str  # as Properties labels the setting
    block: str  # the block type that has it
    key: str  # its key in the flow file
    sep: str  # what is typed between a name and its value in the line that adds a row
    config: dict  # the block's other settings, so that Properties shows the list
    rows: tuple  # the three rows (name, value) the list starts with
    new: tuple  # a name and a value typed over a row's


LISTS = [
    List("Params", API, "params", "=", {"url": URL},
         (("page", "2"), ("region", "north"), ("limit", "100")), ("size", "50")),
    List("Headers", API, "headers", ": ", {"url": URL},
         (("Accept", "application/json"), ("X-Region", "north"), ("X-Trace", "nightly")), ("X-Team", "reports")),
    List("Form", API, "form", "=", {"url": URL, "method": "POST", "body": FORM_BODY},
         (("region", "north"), ("day", "2026-11-16"), ("top", "5")), ("size", "50")),
    List("Parts", API, "parts", "=", {"url": URL, "method": "POST", "body": MULTIPART},
         (("note", "nightly upload"), ("region", "north"), ("top", "5")), ("size", "50")),
    List("Column types", CHECK, "types_columns", ": ", {"types": True},
         (("amount", "float"), ("day", "date"), ("region", "text")), ("total", "int")),
    List("Allowed values", CHECK, "allowed_values", "=", {"values": True},
         (("amount", "0..1000"), ("region", "north, south"), ("day", "2026-01-01..")), ("total", "1..9")),
    List("Custom statistics", STATS, "custom", "=", {},
         (("p95", "quantile_cont(amount, 0.95)"), ("big_orders", "count_if(amount > 1000)"),
          ("north_share", "avg(case when region = 'north' then 1 else 0 end)")), ("biggest", "max(amount)")),
]
EVERY_LIST = pytest.mark.parametrize("lst", LISTS, ids=[lst.label for lst in LISTS])
API_LISTS = pytest.mark.parametrize("lst", [lst for lst in LISTS if lst.block == API],
                                    ids=[lst.label for lst in LISTS if lst.block == API])
HEADERS = next(lst for lst in LISTS if lst.label == "Headers")


# --- the flow, the window and the list -------------------------------------------------------------------------------

def as_rows(pairs) -> list[dict]:
    """Rows as the flow file holds them: each ticked, so with no `on`."""
    return [{"key": k, "value": v} for k, v in pairs]


def activate(qtbot, win):
    """The editor is the window you work in (the keys you press go to it)."""
    win.activateWindow()
    win.raise_()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=5_000)


def open_list(qtbot, win, tmp_path, lst: List, rows=None):
    """A flow file with one block whose list `lst` has `rows` (its three, by default), opened in the editor with the
    block selected: (its tab, the file)."""
    config = {**lst.config, lst.key: as_rows(lst.rows if rows is None else rows)}
    path = flow_file(tmp_path / "rows.yaml", {BLOCK: {"type": lst.block, "config": config, "ui": {"x": 0, "y": 0}}})
    tab = win.open_flow(path)
    settle()
    select_only(tab, BLOCK)
    activate(qtbot, win)
    return tab, path


def setting(win, label: str) -> tuple[QLabel, QWidget]:
    """The name and the field of the setting `label`, on its row of Properties."""
    found = [(name.widget(), form.itemAt(r, QFormLayout.FieldRole).widget())
             for form in win.properties.widget().findChildren(QFormLayout) for r in range(form.rowCount())
             if (name := form.itemAt(r, QFormLayout.LabelRole)) and isinstance(name.widget(), QLabel)
             and name.widget().text() == label]
    assert len(found) == 1, f"Properties shows {len(found)} settings named {label!r}"
    return found[0]


def table(win, lst: List) -> QAbstractItemView:
    """The list's table, as Properties shows it now (it may be built again after each change)."""
    box = setting(win, lst.label)[1]
    views = [v for v in [box, *box.findChildren(QAbstractItemView)]
             if isinstance(v, QAbstractItemView) and not isinstance(v, QHeaderView) and v.isVisibleTo(win)]
    assert len(views) == 1, f"{lst.label}: {len(views)} tables on its row of Properties"
    return views[0]


def column(view, title: str) -> int:
    model = view.model()
    found = [c for c in range(model.columnCount())
             if str(model.headerData(c, Qt.Horizontal) or "").replace("&", "").strip() == title]
    assert len(found) == 1, f"the list has {len(found)} columns headed {title!r}"
    return found[0]


def shown(view) -> list[tuple[str, str]]:
    """The list's rows as it shows them: (name, value)."""
    model, n, v = view.model(), column(view, "Name"), column(view, "Value")
    return [(str(model.index(r, n).data() or ""), str(model.index(r, v).data() or "")) for r in range(model.rowCount())]


def cell(view, r: int, title: str) -> QPoint:
    """The middle of the cell of row `r` in the column `title`, in the table's viewport."""
    return view.visualRect(view.model().index(r, column(view, title))).center()


def under_the_list(win, view, kind) -> list:
    """The visible widgets of `kind` in Properties under the table and above the next setting's name."""
    body = win.properties.widget()
    bottom = view.mapTo(body, QPoint(0, view.height())).y()
    names = [item.widget() for form in body.findChildren(QFormLayout) for r in range(form.rowCount())
             if (item := form.itemAt(r, QFormLayout.LabelRole)) and item.widget() is not None]
    tops = [y for w in names if w.isVisibleTo(win) and (y := w.mapTo(body, QPoint(0, 0)).y()) >= bottom]
    next_top = min(tops, default=10 ** 9)
    return [w for w in body.findChildren(kind) if w.isVisibleTo(win) and w not in names and not view.isAncestorOf(w)
            and bottom <= w.mapTo(body, QPoint(0, 0)).y() < next_top]


def add_line(win, lst: List) -> QLineEdit:
    lines = [e for e in under_the_list(win, table(win, lst), QLineEdit) if e.isEnabled() and not e.isReadOnly()]
    assert len(lines) == 1, f"{lst.label}: {len(lines)} lines to add a row under the list"
    return lines[0]


def plain(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).replace("&", "").split())


def action(win, menu: str, text: str) -> QAction:
    found = next(a.menu() for a in win.menuBar().actions() if a.text() == menu)
    return next(a for a in found.actions() if a.text() == text)


def save(win):
    """File > Save."""
    action(win, "&File", "Save").trigger()
    settle()


def undo(win):
    """Edit > Undo."""
    action(win, "&Edit", "Undo").trigger()
    settle()


def redo(win):
    """Edit > Redo."""
    action(win, "&Edit", "Redo").trigger()
    settle()


def saved_rows(path, lst: List, block: str = BLOCK) -> list:
    """The list's rows in the flow file ([]: none)."""
    config = yaml.safe_load(path.read_text(encoding="utf-8"))["blocks"][block].get("config") or {}
    return config.get(lst.key) or []


def holds(win, path, lst: List, expected, block: str = BLOCK):
    """The list shows `expected` (name, value) rows, and once saved the flow file holds exactly them."""
    assert shown(table(win, lst)) == list(expected), f"{lst.label} shows {shown(table(win, lst))}"
    save(win)
    assert saved_rows(path, lst, block) == as_rows(expected), f"the flow file holds {saved_rows(path, lst, block)}"


# --- the mouse and the keys -------------------------------------------------------------------------------------------

def click(view, at: QPoint, modifier=Qt.NoModifier):
    QTest.mouseClick(view.viewport(), Qt.LeftButton, modifier, at)
    settle()


def select_row(view, r: int):
    """Click the row's name, as you select a row."""
    click(view, cell(view, r, "Name"))
    assert any(i.row() == r for i in view.selectionModel().selectedIndexes()), f"clicking row {r} did not select it"


def press(view, key):
    """Press `key` where the keyboard focus is (in the list, after clicking it)."""
    focus = QApplication.focusWidget()
    assert focus is not None and (focus is view or view.isAncestorOf(focus)), \
        f"the keyboard focus is not in the list but in {focus}"
    QTest.keyClick(focus, key)
    settle()


def open_cell(view, r: int, title: str) -> QLineEdit:
    """Double-click the cell: the text box it opens, its text selected with Ctrl+A."""
    at = cell(view, r, title)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    settle()
    editor = QApplication.focusWidget()
    assert isinstance(editor, QLineEdit) and view.isAncestorOf(editor), \
        f"double-clicking the cell opened no text box: the focus is in {editor}"
    QTest.keyClick(editor, Qt.Key_A, Qt.ControlModifier)
    return editor


def type_over(view, r: int, title: str, text: str):
    """Double-click the cell, type `text` over what it holds and press Enter."""
    editor = open_cell(view, r, title)
    if text:
        QTest.keyClicks(editor, text)
    else:
        QTest.keyClick(editor, Qt.Key_Backspace)
    QTest.keyClick(editor, Qt.Key_Return)
    settle()


def right_click(qtbot, win, widget: QWidget, at: QPoint, choose: str = DELETE_ROW) -> list[str]:
    """Right-click `widget` at `at` (in its own coordinates) through the window, as the mouse does, Properties scrolled
    to it first: the items of the menu that opens ([]: none opened); the one starting with `choose` is clicked if it
    is there and enabled (otherwise the menu is closed)."""
    win.properties.ensureWidgetVisible(widget)
    settle()
    point = widget.mapTo(win, at)
    under = win.childAt(point)
    assert under is widget or widget.isAncestorOf(under), f"the pointer at {point} is over {under}, not {widget}"
    seen, done = [], []

    def act(deadline):  # (a menu waits for you inside the right-click)
        menu = QApplication.activePopupWidget()
        if menu is None and time.monotonic() < deadline:
            QTimer.singleShot(20, win, lambda: act(deadline))
            return
        if menu is not None:
            seen.extend(plain(a.text()) for a in menu.actions() if a.text())
            item = next((a for a in menu.actions() if plain(a.text()).startswith(choose) and a.isEnabled()), None)
            if item is not None:
                QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(item).center())
            if menu.isVisible():
                menu.close()
        done.append(1)

    QTimer.singleShot(0, win, lambda: act(time.monotonic() + 3))
    QCursor.setPos(win.mapToGlobal(point))  # (the pointer is there, as with a mouse: Qt's menu goes there)
    QTest.mouseClick(win.windowHandle(), Qt.RightButton, Qt.NoModifier, point)
    qtbot.waitUntil(lambda: bool(done), timeout=30_000)
    settle()
    return seen


def deletes_by_menu(qtbot, win, view, r: int, title: str):
    """Right-click the row's cell `title` and choose Delete row."""
    seen = right_click(qtbot, win, view.viewport(), cell(view, r, title))
    assert any(item.startswith(DELETE_ROW) for item in seen), \
        f"right-clicking a row opens no menu with {DELETE_ROW!r}: {seen or 'no menu'}"


# --- AP-1: a row is deleted -------------------------------------------------------------------------------------------

@EVERY_LIST
@pytest.mark.invariant("AP-1")
@pytest.mark.invariant("AP-3")
def test_a_selected_row_is_deleted_by_the_delete_key_and_the_flow_file_holds_the_rest(qtbot, win, tmp_path, lst):
    """A1: click a row, press Delete: the row is gone, the others stay in their order, and the flow file holds them."""
    with editing(win):
        tab, path = open_list(qtbot, win, tmp_path, lst)
        view = table(win, lst)
        select_row(view, 1)
        press(view, Qt.Key_Delete)

        holds(win, path, lst, [lst.rows[0], lst.rows[2]])
        assert BLOCK in tab.doc.blocks, "Delete in the list deleted the block"


@EVERY_LIST
@pytest.mark.invariant("AP-1")
@pytest.mark.invariant("AP-3")
def test_right_click_and_delete_row_deletes_the_row_under_the_pointer(qtbot, win, tmp_path, lst):
    """A2: right-click a row (its value) and choose Delete row: that row is gone, the others stay, the file holds
    them."""
    with editing(win):
        _, path = open_list(qtbot, win, tmp_path, lst)
        deletes_by_menu(qtbot, win, table(win, lst), 1, "Value")

        holds(win, path, lst, [lst.rows[0], lst.rows[2]])


@EVERY_LIST
@pytest.mark.invariant("AP-1")
@pytest.mark.invariant("AP-3")
def test_deleting_the_only_row_leaves_an_empty_list_with_its_add_line_and_none_in_the_file(qtbot, win, tmp_path, lst):
    """A3: a list of one row; click it, press Delete: the list is empty, the line to add a row is still under it, and
    the flow file holds no row."""
    with editing(win):
        _, path = open_list(qtbot, win, tmp_path, lst, rows=lst.rows[:1])
        view = table(win, lst)
        select_row(view, 0)
        press(view, Qt.Key_Delete)

        holds(win, path, lst, [])
        assert add_line(win, lst).isVisible()


@EVERY_LIST
@pytest.mark.invariant("AP-1")
def test_the_delete_key_in_a_cell_being_edited_deletes_its_text_not_the_row(qtbot, win, tmp_path, lst):
    """A4: double-click a row's value, select its text, press Delete, then Enter: the value is emptied and the row is
    kept, with its name, in its place."""
    with editing(win):
        _, path = open_list(qtbot, win, tmp_path, lst)
        view = table(win, lst)
        editor = open_cell(view, 1, "Value")
        QTest.keyClick(editor, Qt.Key_Delete)
        assert editor.text() == "", f"Delete in the cell's text box left {editor.text()!r}"
        QTest.keyClick(editor, Qt.Key_Return)
        settle()

        holds(win, path, lst, [lst.rows[0], (lst.rows[1][0], ""), lst.rows[2]])


@EVERY_LIST
@pytest.mark.invariant("AP-1")
@pytest.mark.invariant("AP-3")
def test_deleting_the_row_being_edited_writes_nothing_back(qtbot, win, tmp_path, lst):
    """A5: double-click a row's value and type over it without pressing Enter, then right-click the same row (its name)
    and choose Delete row: the row is gone, the others are as they were, and what was typed is nowhere."""
    with editing(win):
        _, path = open_list(qtbot, win, tmp_path, lst)
        view = table(win, lst)
        typed = f"stale-{secrets.token_hex(4)}"
        QTest.keyClicks(open_cell(view, 1, "Value"), typed)
        settle()
        deletes_by_menu(qtbot, win, view, 1, "Name")

        holds(win, path, lst, [lst.rows[0], lst.rows[2]])
        assert typed not in path.read_text(encoding="utf-8")


@EVERY_LIST
@pytest.mark.invariant("AP-1")
def test_clearing_a_rows_name_still_deletes_the_row(qtbot, win, tmp_path, lst):
    """A6: double-click a row's name, clear it, press Enter: the row is gone, as before this change."""
    with editing(win):
        _, path = open_list(qtbot, win, tmp_path, lst)
        type_over(table(win, lst), 1, "Name", "")

        holds(win, path, lst, [lst.rows[0], lst.rows[2]])


@EVERY_LIST
@pytest.mark.invariant("AP-1")
def test_delete_with_no_row_selected_and_a_right_click_where_there_is_no_row_delete_nothing(qtbot, win, tmp_path,
                                                                                            lst):
    """A13: click a row and Ctrl+click it again (no row is selected), press Delete; right-click the list's heading,
    where there is no row (choosing Delete row if it is offered): no row is deleted, nor the block."""
    with editing(win):
        tab, path = open_list(qtbot, win, tmp_path, lst)
        view = table(win, lst)
        select_row(view, 1)
        click(view, cell(view, 1, "Name"), Qt.ControlModifier)
        assert not view.selectionModel().selectedIndexes(), "Ctrl+click did not leave the list with no row selected"
        press(view, Qt.Key_Delete)
        assert shown(table(win, lst)) == list(lst.rows), \
            f"Delete with no row selected changed the list: {shown(table(win, lst))}"

        view = table(win, lst)
        header = view.horizontalHeader()
        name_column = column(view, "Name")
        at = QPoint(header.sectionViewportPosition(name_column) + header.sectionSize(name_column) // 2,
                    header.height() // 2)
        right_click(qtbot, win, header, at)

        holds(win, path, lst, lst.rows)
        assert BLOCK in tab.doc.blocks, "the block was deleted"


# --- AP-2: changed in place, and the hint -----------------------------------------------------------------------------

@EVERY_LIST
@pytest.mark.invariant("AP-2")
@pytest.mark.invariant("AP-3")
def test_a_rows_name_and_value_are_changed_in_place_by_double_clicking_them(qtbot, win, tmp_path, lst):
    """A7: double-click a row's name, type a new one, Enter; the same for its value: the row is changed where it is,
    and the flow file holds it."""
    with editing(win):
        _, path = open_list(qtbot, win, tmp_path, lst)
        type_over(table(win, lst), 1, "Name", lst.new[0])
        holds(win, path, lst, [lst.rows[0], (lst.new[0], lst.rows[1][1]), lst.rows[2]])
        type_over(table(win, lst), 1, "Value", lst.new[1])

        holds(win, path, lst, [lst.rows[0], lst.new, lst.rows[2]])


@EVERY_LIST
@pytest.mark.invariant("AP-2")
def test_a_grey_hint_under_the_list_says_to_double_click_to_change_and_delete_to_delete(qtbot, win, tmp_path, lst):
    """A8: under the list, a text says how to change a row (double-click) and how to delete one (Delete), painted
    fainter than the setting's name."""
    with editing(win):
        open_list(qtbot, win, tmp_path, lst)
        view = table(win, lst)
        texts = under_the_list(win, view, QLabel)
        hints = [label for label in texts if re.search(r"double[\s-]?click", plain(label.text()), re.IGNORECASE)
                 and re.search(r"\bdelete\b", plain(label.text()), re.IGNORECASE)]
        assert len(hints) == 1, f"{lst.label}: no hint under the list naming double-click and Delete: " \
                                f"{[plain(label.text()) for label in texts]}"
        hint = hints[0]
        assert hint.isVisible()
        win.properties.ensureWidgetVisible(hint)  # (scrolled to, to be painted)
        faint = strongest(in_place(hint))
        name = setting(win, lst.label)[0]
        win.properties.ensureWidgetVisible(name)
        assert faint < strongest(in_place(name)), "the hint is not grey: it is painted as strong as the setting's name"


# --- AP-3: one undo step each -----------------------------------------------------------------------------------------

@EVERY_LIST
@pytest.mark.invariant("AP-3")
def test_each_edit_is_one_undo_step_and_the_file_holds_the_list_after_each_undo_and_redo(qtbot, win, tmp_path, lst):
    """A9 (edits): change a row's name, then another row's value; Undo, Undo, Redo, Redo: each takes back or does
    again one change, and after each the flow file, saved, holds what the list shows."""
    with editing(win):
        _, path = open_list(qtbot, win, tmp_path, lst)
        a, b, c = lst.rows
        renamed, revalued = (lst.new[0], a[1]), (b[0], lst.new[1])
        type_over(table(win, lst), 0, "Name", lst.new[0])
        type_over(table(win, lst), 1, "Value", lst.new[1])
        holds(win, path, lst, [renamed, revalued, c])

        undo(win)
        holds(win, path, lst, [renamed, b, c])
        undo(win)
        holds(win, path, lst, [a, b, c])
        redo(win)
        holds(win, path, lst, [renamed, b, c])
        redo(win)
        holds(win, path, lst, [renamed, revalued, c])


@EVERY_LIST
@pytest.mark.invariant("AP-3")
def test_each_delete_is_one_undo_step_and_the_file_holds_the_list_after_each_undo_and_redo(qtbot, win, tmp_path, lst):
    """A9 (deletes): delete the middle row, then the first, each with Delete; Undo, Undo, Redo, Redo: each takes back
    or does again one delete, the row back in its place, and after each the flow file holds what the list shows."""
    with editing(win):
        _, path = open_list(qtbot, win, tmp_path, lst)
        a, b, c = lst.rows
        view = table(win, lst)
        select_row(view, 1)
        press(view, Qt.Key_Delete)
        holds(win, path, lst, [a, c])
        view = table(win, lst)
        select_row(view, 0)
        press(view, Qt.Key_Delete)
        holds(win, path, lst, [c])

        undo(win)
        holds(win, path, lst, [a, c])
        undo(win)
        holds(win, path, lst, [a, b, c])
        redo(win)
        holds(win, path, lst, [a, c])
        redo(win)
        holds(win, path, lst, [c])


# --- A10: rows from Import from cURL ----------------------------------------------------------------------------------

def curl_for(lst: List) -> str:
    """The curl command that fills the list with its three rows."""
    if lst.label == "Params":
        return f"curl '{URL}?{urlencode(lst.rows)}'"
    option = {"Headers": "-H", "Form": "--data-urlencode", "Parts": "-F"}[lst.label]
    sep = ": " if lst.label == "Headers" else "="
    return f"curl {URL} " + " ".join(f"{option} '{k}{sep}{v}'" for k, v in lst.rows)


@API_LISTS
@pytest.mark.invariant("AP-1")
@pytest.mark.invariant("AP-2")
def test_rows_filled_in_from_curl_are_changed_and_deleted_like_any_row(qtbot, win, tmp_path, monkeypatch, lst):
    """A10: an API Request filled in by Import from cURL; double-click a row's value and change it, then click another
    row and press Delete: the list and the flow file (saved as) hold the change and not the deleted row."""
    with editing(win):
        path = tmp_path / "curl.yaml"
        tab, block = new_api(win)
        fill_in(win, curl_for(lst))
        activate(qtbot, win)
        assert shown(table(win, lst)) == list(lst.rows), f"Import from cURL filled {lst.label} with " \
                                                         f"{shown(table(win, lst))}"
        type_over(table(win, lst), 1, "Value", lst.new[1])
        assert shown(table(win, lst))[1] == (lst.rows[1][0], lst.new[1]), "the value filled in was not changed"
        view = table(win, lst)
        select_row(view, 0)
        press(view, Qt.Key_Delete)
        save_as(monkeypatch, path)
        action(win, "&File", "Save as…").trigger()
        settle()

        expected = [(lst.rows[1][0], lst.new[1]), lst.rows[2]]
        assert shown(table(win, lst)) == expected, f"{lst.label} shows {shown(table(win, lst))}"
        assert saved_rows(path, lst, block) == as_rows(expected)
        assert tab.doc.path == path.resolve()


# --- AP-4: a hidden value stays hidden --------------------------------------------------------------------------------

def everything_shown(win) -> str:
    """Every text the windows show: labels, text boxes, buttons, tooltips, titles, the status bar, and each cell of
    every list or table (its text, the text it is edited as, its tooltip)."""
    found = [win.statusBar().currentMessage()]
    widgets = [w for top in QApplication.topLevelWidgets() if top.isVisible()
               for w in [top, *top.findChildren(QWidget)]]
    for w in widgets:
        if not w.isVisible():
            continue
        for name in ("text", "toPlainText", "toolTip", "placeholderText", "windowTitle", "currentText", "title"):
            value = getattr(w, name, None)
            if callable(value):
                try:
                    found.append(str(value()))
                except TypeError:
                    pass
        if isinstance(w, QAbstractItemView) and w.model() is not None:
            model, todo = w.model(), [None]
            while todo:
                parent = todo.pop()
                parent_index = parent if parent is not None else QModelIndex()
                for r in range(model.rowCount(parent_index)):
                    for c in range(model.columnCount(parent_index)):
                        index = model.index(r, c, parent_index)
                        found += [str(model.data(index, role) or "") for role in (Qt.DisplayRole, Qt.EditRole,
                                                                                  Qt.ToolTipRole)]
                        if c == 0 and model.rowCount(index) > 0:
                            todo.append(index)
    return "\n".join(found)


@pytest.mark.invariant("AP-4")
def test_a_secret_header_value_stays_hidden_while_its_row_is_edited(qtbot, win, tmp_path):
    """A11: a Headers row whose value is secret:NAME, NAME a stored secret: shown, its value double-clicked and left
    with Enter, its name changed, the flow saved; at no point does any window show the secret's value, and the file
    keeps secret:NAME, never the value."""
    with editing(win):
        hidden = f"hidden-{secrets.token_hex(8)}"
        win.home.set_secret("orders_key", hidden)
        rows = (("Accept", "application/json"), ("X-Api-Key", "secret:orders_key"), ("X-Trace", "nightly"))
        _, path = open_list(qtbot, win, tmp_path, HEADERS, rows=rows)
        assert hidden not in everything_shown(win), "the secret's value is shown with its row"

        editor = open_cell(table(win, HEADERS), 1, "Value")
        assert hidden not in everything_shown(win) and hidden not in editor.text(), \
            "the secret's value is shown while its row's value is edited"
        QTest.keyClick(editor, Qt.Key_Return)
        settle()
        assert hidden not in everything_shown(win), "the secret's value is shown after its row's value was edited"

        editor = open_cell(table(win, HEADERS), 1, "Name")
        QTest.keyClicks(editor, "X-Key")
        assert hidden not in everything_shown(win), "the secret's value is shown while its row's name is edited"
        QTest.keyClick(editor, Qt.Key_Return)
        settle()

        holds(win, path, HEADERS, [rows[0], ("X-Key", "secret:orders_key"), rows[2]])
        assert hidden not in everything_shown(win)
        assert hidden not in path.read_text(encoding="utf-8")


# --- AP-5: two rows with the same name are kept and sent --------------------------------------------------------------

class Sent(NamedTuple):
    query: list  # the query's (name, value) pairs, in order
    headers: list  # the header lines (name, value), in order
    content_type: str
    body: bytes


class Recorder:
    """A web server on 127.0.0.1 for one test: notes each request (Sent) and answers 200 with the text ok."""

    def __init__(self):
        self.seen, recorder = [], self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                recorder._serve(self)

            do_POST = do_GET

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _serve(self, h):
        body = h.rfile.read(int(h.headers.get("Content-Length") or 0))
        self.seen.append(Sent(parse_qsl(urlsplit(h.path).query, keep_blank_values=True), list(h.headers.items()),
                              h.headers.get("Content-Type") or "", body))
        h.send_response(200)
        h.send_header("Content-Type", "text/plain")
        h.send_header("Content-Length", "2")
        h.end_headers()
        h.wfile.write(b"ok")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def run(win, path) -> subprocess.CompletedProcess:
    """The real `taskloom run` of the flow, on the editor's Taskloom folder, with no proxy of this computer's."""
    env = {k: v for k, v in os.environ.items() if k.lower() not in PROXY_VARIABLES}
    env.update(TASKLOOM_HOME=str(win.home.root), PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring")
    return subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(path)], env=env, cwd=path.parent,
                          capture_output=True, text=True, errors="replace", timeout=120)


def sent_pairs(lst: List, sent: Sent) -> list[tuple[str, str]]:
    """What the request carried of the list, as (name, value) pairs in the order sent."""
    if lst.label == "Params":
        return sent.query
    if lst.label == "Form":
        return parse_qsl(sent.body.decode("utf-8"), keep_blank_values=True)
    if lst.label == "Parts":
        message = email.message_from_bytes(b"Content-Type: " + sent.content_type.encode() + b"\r\n\r\n" + sent.body,
                                           policy=email.policy.HTTP)
        return [(p.get_param("name", header="content-disposition"), p.get_payload(decode=True).decode("utf-8"))
                for p in message.iter_parts()]
    return [(name.lower(), value.strip()) for name, line in sent.headers for value in line.split(",")]


@API_LISTS
@pytest.mark.invariant("AP-5")
def test_two_rows_with_the_same_name_are_both_kept_and_both_sent_in_the_lists_order(qtbot, win, tmp_path, lst):
    """A12: a list with a row and another; a second row with the first one's name added with the line under the list:
    the list shows the three, the flow file holds them, and the run sends both rows of that name, in the list's order
    (a header's values in their order; the others where they are in the list)."""
    with editing(win):
        is_headers = lst.label == "Headers"
        name, other = ("X-Tag", ("Accept", "text/plain")) if is_headers else ("tag", ("page", "2"))
        with Recorder() as server:
            lst_here = lst._replace(config={**lst.config, "url": f"{server.base}/echo"})
            _, path = open_list(qtbot, win, tmp_path, lst_here, rows=[(name, "one"), other])
            line = add_line(win, lst_here)
            QTest.mouseClick(line, Qt.LeftButton)
            QTest.keyClicks(line, f"{name}{lst.sep}two")
            QTest.keyClick(line, Qt.Key_Return)
            settle()
            rows = [(name, "one"), other, (name, "two")]
            holds(win, path, lst_here, rows)
            result = run(win, path)
            seen = list(server.seen)

        assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
        assert len(seen) == 1, f"{len(seen)} requests"
        pairs = sent_pairs(lst, seen[0])
        if is_headers:
            assert [v for n, v in pairs if n == name.lower()] == ["one", "two"], seen[0].headers
            assert [v for n, v in pairs if n == other[0].lower()] == [other[1]], seen[0].headers
        else:
            assert pairs == rows, pairs



@pytest.mark.invariant("AP-5")
def test_the_note_under_connection_shows_a_repeated_headers_values_as_they_are_sent(qtbot, win, tmp_path):
    """A12, as Properties says it: with an API connection chosen whose headers X-Tag and Cookie the block's own
    replace, and the block's Headers holding two rows of each name, the note under Connection shows each replacing
    header with the value that is sent: its rows' values in the list's order, joined by ", " (by "; " for Cookie), as
    Copy as cURL shows them (-H 'X-Tag: one, two', -H 'Cookie: a=1; b=2')."""
    with editing(win):
        (win.home.root / "connections.yaml").write_text(yaml.safe_dump({"shop": {
            "kind": "api", "base_url": "http://127.0.0.1:9/api",
            "headers": as_rows([("X-Tag", "zero"), ("Cookie", "theme=dark")])}}), encoding="utf-8")
        rows = [("X-Tag", "one"), ("Cookie", "a=1"), ("Accept", "text/plain"), ("X-Tag", "two"), ("Cookie", "b=2")]
        open_list(qtbot, win, tmp_path, HEADERS._replace(config={**HEADERS.config, "connection": "shop"}), rows=rows)
        notes = [w.text() for w in win.properties.widget().findChildren(QLabel)
                 if w.isVisibleTo(win) and w.text().startswith("From the connection shop")]
        assert len(notes) == 1, f"Properties shows {len(notes)} notes from the connection shop"
        lines = notes[0].splitlines()
        assert "Header X-Tag: zero (replaced by this block's X-Tag: one, two)" in lines, lines
        assert "Header Cookie: theme=dark (replaced by this block's Cookie: a=1; b=2)" in lines, lines
