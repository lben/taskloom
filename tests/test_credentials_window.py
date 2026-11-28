"""Credentials on the PC (docs/promises/credentials.yaml): the window Tools > Credentials…, in the real editor
(offscreen), through what a person does: the menu, clicks, typing, pasting, right-click menus and buttons; and what it
stores: <Taskloom home>/credentials.yaml and the tests' stand-in credential store (tests/credential_store, never the
real one). Each test names the rows of the stage's combinations table it covers (B2, C1, J1, ...). Made-up values only.

What the tests take as given of the window (its contract; nothing else of its inside is used):
- Tools > "Credentials…" opens it, in the editor window it is chosen in (each editor window its own, as two Taskloom
  windows each have theirs): a top-level window titled "Credentials" (a QDialog, shown with exec() or show()).
- The cards: the QListWidget named "cards", an item a card (its text the card's name, in the list file's order);
  clicking one chooses it. The buttons "Add…" and "Rename…" ask the name with QInputDialog.getText; "Delete" takes the
  chosen card off at once, without a question.
- "Takes its values from": the QComboBox named "uses": "(none)" and the other cards; the window follows its current
  text.
- The chosen card's rows, top to bottom (rows of the card it takes its values from may show too, their key box
  disabled), and always one empty row last: each a QWidget named "credential-row" holding the QLineEdit "key", the
  QLineEdit "value", the eye (a button named "eye"; none on a pointer row) and the row's notes as QLabels: "Too short
  to hide in the Log: use 4 or more characters" (with "too short" in it), "no value stored".
- A key or a value is changed by clicking its box, Ctrl+A, typing (or pasting) and Tab. Typing a key in the empty row
  makes it a row, and a new empty row shows below it.
- A value box shows (its displayText) while its eye is shut the same run of dots for every value, whatever its length,
  no letter or digit, also while a value is typed; with its eye open, the value. A pointer row's value box shows the
  row it points at ("→ PROD.password").
- Right-click a row's key box: a menu with "Point at another row…" (it asks with QInputDialog.getItem, offering the
  rows of the other cards as CARD.key) and "Delete row" (at once, without a question).
- "Save" stores every change at once and closes the window; "Cancel" closes it and stores nothing.
- A change that is refused (a name, a circle, deleting what is used) or a Save that cannot be done is said in a message
  box (QMessageBox), and the window stays open. A circle's message says "circle" and names its cards; a refused
  deletion names who uses it; a refused name says why (a dot, a space).
- Renaming a card or a key that open flows use (Rename…, or its key box edited) shows a modal box naming each of those
  flows (as its tab does: its file name) and each block that uses it, with a button holding "only" (rename only) and
  one holding "rewrite" (also rewrite the references in the open flows). The rewrite is made when Save is pressed.
"""

import os
import subprocess
import sys
import time

import keyring.core
import pytest
import yaml
from keyring.errors import KeyringError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QInputDialog, QLabel,  # noqa: E402
                               QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QTextEdit, QWidget)
from shiboken6 import isValid  # noqa: E402
from taskloom_test_store import FileStore  # noqa: E402
from test_credentials_store import LONG, card_entries, entries, names, plant  # noqa: E402

from taskloom.config import Home  # noqa: E402
from taskloom.credentials import value as resolved  # noqa: E402

UAT_HOST, UAT_PASSWORD, PROD_PASSWORD = "uat-db.example.test", "made-up-pass-1", "made-up-pass-2"


# --- the window, as a person sees and uses it ----------------------------------------------------------------------

class Watch:
    """While a test runs: steps waiting for a Credentials window to open, the windows being used, the answers to give
    the next boxes, and the text of every other box the editor showed (closed at once)."""

    def __init__(self):
        self.openers, self.in_use, self.answers, self.said = [], [], [], []


WATCH: Watch | None = None


def plain(text: str) -> str:
    return text.replace("&", "")


def is_credentials(widget) -> bool:
    return any(found.window() is widget for found in widget.findChildren(QListWidget, "cards"))


def text_of(box) -> str:
    parts = [box.windowTitle(), *(label.text() for label in box.findChildren(QLabel))]
    parts += [edit.toPlainText() for kind in (QTextEdit, QPlainTextEdit) for edit in box.findChildren(kind)]
    parts += [items.item(i).text() for items in box.findChildren(QListWidget) for i in range(items.count())]
    return "\n".join(p for p in parts if p)


def _look():
    """Every 20 ms: a Credentials window waited for is handed to its steps; a box over it is answered as the test
    said, or else noted and closed (a box would wait forever for a click)."""
    watch = WATCH
    if watch is None:
        return
    if watch.openers:
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible() and widget not in watch.in_use and is_credentials(widget):
                watch.in_use.append(widget)
                steps = watch.openers.pop(0)
                QTimer.singleShot(0, lambda s=steps, w=widget: s(w))  # (not in here: this timer waits while it runs)
                break
    box = QApplication.activeModalWidget()
    if box is None or not box.isVisible() or is_credentials(box) or box.property("seen_by_test"):
        return
    box.setProperty("seen_by_test", True)
    text = text_of(box)
    if watch.answers:
        word, record = watch.answers.pop(0)
        record.append(text)
        button = next((b for b in box.findChildren(QAbstractButton)
                       if b.isVisible() and word.lower() in plain(b.text()).lower()), None)
        if button is not None:
            button.click()
            return
        watch.said.append(f"(no button holding {word!r}) {text}")
    else:
        watch.said.append(text)
    box.reject() if hasattr(box, "reject") else box.close()


@pytest.fixture(autouse=True)
def watch(qtbot, monkeypatch):
    global WATCH
    WATCH = Watch()
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: WATCH.said.append("\n".join(map(str, a[1:3]))) or QMessageBox.Ok))
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(_look)
    timer.start()
    yield WATCH
    timer.stop()
    left, WATCH = WATCH.said, None
    assert left == [], f"message boxes no step of the test expected: {left}"


def settle():
    for _ in range(3):
        QTest.qWait(10)


def wait_for(condition, seconds: float, what: str):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, what
        QTest.qWait(10)


def activate(window):
    if QApplication.activeWindow() is not window:
        window.activateWindow()
        deadline = time.monotonic() + 5
        while QApplication.activeWindow() is not window and time.monotonic() < deadline:
            QTest.qWait(10)


def menu_item(win, menu: str, text: str):
    for top in win.menuBar().actions():
        if top.menu() is not None and plain(top.text()) == menu:
            for action in top.menu().actions():
                if plain(action.text()) == text:
                    return action
    raise AssertionError(f"the editor's {menu} menu has no {text}")


def open_credentials(win, steps):
    """Tools > Credentials… in the editor window `win`, then steps(window) in the window that opens (it may wait for
    you inside the menu's call, as the other Tools windows do). A window the steps leave open is closed unsaved."""
    outcome = []

    def run(window):
        try:
            activate(window)
            steps(window)
            outcome.append(None)
        except BaseException as e:  # (raised again below, outside Qt)
            outcome.append(e)
        finally:
            WATCH.in_use.remove(window)
            if isValid(window) and window.isVisible():
                window.done(0) if hasattr(window, "done") else window.close()

    WATCH.openers.append(run)
    try:
        menu_item(win, "Tools", "Credentials…").trigger()
        wait_for(lambda: outcome, 120, "Tools > Credentials… opened no window with the list of cards")
    finally:
        if run in WATCH.openers:
            WATCH.openers.remove(run)
    if outcome[0] is not None:
        raise outcome[0]


def messages(win) -> list[str]:
    """The text of every message box shown since last asked (taken off the lists checked at the end of the test)."""
    found = [f"{title}\n{text}" for _, (title, text, *_) in win.message_boxes] + WATCH.said
    win.message_boxes.clear()
    WATCH.said.clear()
    return found


def answer_next_box(word: str) -> list[str]:
    """The next box the editor shows is answered with its button holding `word`; its text is added to the list."""
    record = []
    WATCH.answers.append((word, record))
    return record


def asked_names(monkeypatch, *given: str):
    """Add… and Rename… are answered with these names, in turn."""
    queue = list(given)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (queue.pop(0), True)))


def picked_row(monkeypatch, choice: str) -> list[str]:
    """Point at another row… is answered with `choice`; the rows it offered are added to the list."""
    offered = []

    def get_item(*a, **k):
        offered.extend(k.get("items", a[3] if len(a) > 3 else []))
        return choice, True

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(get_item))
    return offered


def card_list(window) -> QListWidget:
    return next(found for found in window.findChildren(QListWidget, "cards") if found.window() is window)


def card_names(window) -> list[str]:
    cards = card_list(window)
    return [cards.item(i).text() for i in range(cards.count()) if not cards.item(i).isHidden()]


def choose(window, card: str):
    cards = card_list(window)
    found = cards.findItems(card, Qt.MatchExactly)
    assert found, f"no card {card} in the list: {card_names(window)}"
    cards.scrollToItem(found[0])
    QTest.mouseClick(cards.viewport(), Qt.LeftButton, Qt.NoModifier, cards.visualItemRect(found[0]).center())
    settle()


def press(window, text: str):
    found = [b for b in window.findChildren(QAbstractButton)
             if plain(b.text()) == text and b.isVisibleTo(window) and b.window() is window]
    assert len(found) == 1, f"the window has {len(found)} buttons {text!r}"
    found[0].click()
    settle()


def rows(window) -> list[QWidget]:
    """The chosen card's rows as shown, top to bottom."""
    found = [r for r in window.findChildren(QWidget, "credential-row") if isValid(r) and r.isVisibleTo(window)]
    return sorted(found, key=lambda r: r.mapTo(window, QPoint(0, 0)).y())


def key_box(row_) -> QLineEdit:
    return row_.findChild(QLineEdit, "key")


def value_box(row_) -> QLineEdit:
    return row_.findChild(QLineEdit, "value")


def keys(window) -> list[str]:
    return [key_box(r).text() for r in rows(window)]


def row(window, key: str) -> QWidget:
    """The chosen card's own row (its key box enabled) whose key is `key`."""
    found = [r for r in rows(window) if key_box(r).text() == key and key_box(r).isEnabled()]
    assert found, f"no row {key!r}: {keys(window)}"
    return found[0]


def shown(row_) -> str:
    return value_box(row_).displayText()


def eye(row_):
    found = [b for b in row_.findChildren(QAbstractButton, "eye") if b.isVisibleTo(row_)]
    return found[0] if found else None


def notes(row_) -> str:
    """What the row says besides its key and value: its labels, and the value box's placeholder when it is empty."""
    said = [label.text() for label in row_.findChildren(QLabel) if label.isVisibleTo(row_)]
    if not value_box(row_).text():
        said.append(value_box(row_).placeholderText())
    return "\n".join(said).lower()


def dots(text: str) -> bool:
    """A value hidden: something shows, and no letter or digit of it."""
    return bool(text) and not any(c.isalnum() for c in text)


def type_into(edit: QLineEdit, text: str, paste: bool = False):
    """Click the box, select what it holds, type (or paste) `text` over it, and Tab out of it."""
    activate(edit.window())
    QTest.mouseClick(edit, Qt.LeftButton)
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    if paste:
        QApplication.clipboard().setText(text)
        QTest.keyClick(edit, Qt.Key_V, Qt.ControlModifier)
    elif text:
        QTest.keyClicks(edit, text)
    else:
        QTest.keyClick(edit, Qt.Key_Backspace)
    QTest.keyClick(edit, Qt.Key_Tab)
    settle()


def add_row(window, key: str, value: str | None = None, paste: bool = False):
    """Type `key` in the empty row at the bottom, then `value` in its value box."""
    last = rows(window)[-1]
    assert key_box(last).text() == "", f"the last row is not empty: {keys(window)}"
    type_into(key_box(last), key, paste=paste)
    if value is not None:
        type_into(value_box(row(window, key)), value, paste=paste)


def row_menu(window, row_, choose_: str) -> list[str]:
    """Right-click the row's key box: the menu's items; the one starting with `choose_` is clicked."""
    box = key_box(row_)
    point = box.mapTo(window, box.rect().center())
    seen, done = [], []

    def act(deadline):  # (a menu waits for you inside the right-click)
        menu = QApplication.activePopupWidget()
        if menu is None and time.monotonic() < deadline:
            QTimer.singleShot(20, lambda: act(deadline))
            return
        if menu is not None:
            seen.extend(plain(a.text()) for a in menu.actions() if a.text())
            item = next((a for a in menu.actions() if plain(a.text()).startswith(choose_) and a.isEnabled()), None)
            if item is not None:
                QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(item).center())
            if isValid(menu) and menu.isVisible():
                menu.close()
        done.append(1)

    activate(window)
    QTimer.singleShot(0, lambda: act(time.monotonic() + 3))
    QCursor.setPos(window.mapToGlobal(point))
    QTest.mouseClick(window.windowHandle(), Qt.RightButton, Qt.NoModifier, point)
    wait_for(lambda: done, 60, "the right-click's menu was not closed")
    settle()
    assert any(s.startswith(choose_) for s in seen), \
        f"right-clicking the row {box.text()!r} opens no menu with {choose_!r}: {seen or 'no menu'}"
    return seen


# --- what is stored ----------------------------------------------------------------------------------------------

def cards(win, listed: dict, values: dict):
    """credentials.yaml written as `listed` ({CARD: {"uses": ..., "rows": {key: None or "CARD.key"}}}), then each of
    `values` ({"CARD.key": value}) stored as taskloom secret set stores it."""
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "credentials.yaml").write_text(
        yaml.safe_dump({"cards": listed}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    for reference, value in values.items():
        Home(win.home.root).set_secret(reference, value)


def cards_of(win) -> list[tuple]:
    """The list file's cards, in order, each as (name, the card it takes from or None, [(key, None or pointer)])."""
    path = win.home.root / "credentials.yaml"
    listed = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("cards") or {} if path.exists() else {}
    return [(name, (card or {}).get("uses"), list(((card or {}).get("rows") or {}).items()))
            for name, card in listed.items()]


def files(win, store) -> tuple:
    """What the list file says and every entry of the store: to show nothing was stored (a Save that changed nothing
    may write the list again in its own layout)."""
    return cards_of(win), entries(store)


def value_of(win, reference: str) -> str:
    """The value a run gets for `reference`: the row's own, a pointer followed, or the card it takes from."""
    return resolved(win.home.root, reference)


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class RefusesSecondWrite(FileStore):
    """The tests' store, refusing the second value written to it, as a store that is locked midway through a Save."""

    def __init__(self):
        super().__init__()
        self.writes = 0

    def set_password(self, service, username, password):
        self.writes += 1
        if self.writes == 2:
            raise KeyringError("the credential store was locked (made-up)")
        super().set_password(service, username, password)


# --- cards and rows: add, rename, delete; Save and Cancel (CR-1) ----------------------------------------------------

@pytest.mark.invariant("CR-1")
def test_the_window_shows_the_cards_left_the_chosen_cards_rows_right_and_one_empty_row_last(win, monkeypatch):
    """C6: Tools > Credentials… lists the cards in the list file's order; choosing one shows its rows and one empty row
    below them; typing a key in the empty row makes it a row with a new empty row below; a card just added shows one
    empty row."""
    cards(win, {"UAT": {"rows": {"host": None, "password": None}}, "PROD": {"rows": {"password": None}}},
          {"UAT.host": UAT_HOST, "UAT.password": UAT_PASSWORD, "PROD.password": PROD_PASSWORD})
    asked_names(monkeypatch, "STAGE")

    def steps(window):
        assert card_names(window) == ["UAT", "PROD"]
        choose(window, "PROD")
        assert keys(window) == ["password", ""]
        choose(window, "UAT")
        assert keys(window) == ["host", "password", ""]
        cards_ = card_list(window)
        assert cards_.mapTo(window, QPoint(cards_.width(), 0)).x() <= rows(window)[0].mapTo(window, QPoint(0, 0)).x()
        add_row(window, "port")
        assert keys(window) == ["host", "password", "port", ""]
        add_row(window, "region")
        assert keys(window) == ["host", "password", "port", "region", ""]
        press(window, "Add…")
        assert card_names(window) == ["UAT", "PROD", "STAGE"]
        choose(window, "STAGE")
        assert keys(window) == [""]
        press(window, "Cancel")

    open_credentials(win, steps)


@pytest.mark.invariant("CR-1")
@pytest.mark.parametrize("button", ["Save", "Cancel"])
def test_save_stores_every_change_at_once_and_cancel_stores_none(win, monkeypatch, credential_store, button):
    """C5: cards and rows added, a key renamed, a value changed, a row and a card deleted: nothing is stored until a
    button is pressed; Save then stores all of it (a renamed key keeps its value; what was deleted is gone from the
    store too), Cancel none of it."""
    cards(win, {"UAT": {"rows": {"host": None, "password": None, "token": None}}, "OLD": {"rows": {"user": None}}},
          {"UAT.host": UAT_HOST, "UAT.password": UAT_PASSWORD, "UAT.token": "made-up-token-1",
           "OLD.user": "made-up-user-1"})
    before = files(win, credential_store)
    asked_names(monkeypatch, "PROD")

    def steps(window):
        choose(window, "UAT")
        type_into(key_box(row(window, "host")), "hostname")
        type_into(value_box(row(window, "password")), "made-up-pass-9")
        row_menu(window, row(window, "token"), "Delete row")
        assert keys(window) == ["hostname", "password", ""]
        choose(window, "OLD")
        press(window, "Delete")
        assert "OLD" not in card_names(window)
        press(window, "Add…")
        choose(window, "PROD")
        add_row(window, "host", "prod-db.example.test")
        add_row(window, "password", PROD_PASSWORD)
        assert files(win, credential_store) == before, "a change was stored before Save"
        press(window, button)
        assert not window.isVisible(), f"{button} did not close the window"

    open_credentials(win, steps)

    if button == "Cancel":
        assert files(win, credential_store) == before
        return
    assert cards_of(win) == [("UAT", None, [("hostname", None), ("password", None)]),
                             ("PROD", None, [("host", None), ("password", None)])]
    assert card_entries(credential_store) == {"UAT.hostname": UAT_HOST, "UAT.password": "made-up-pass-9",
                                              "PROD.host": "prod-db.example.test", "PROD.password": PROD_PASSWORD}


# --- values hidden; the eye (CR-2) -------------------------------------------------------------------------------

EYE_CARDS = {"UAT": {"rows": {"host": None, "username": None, "password": None, "pin": None}}}
EYE_VALUES = {"UAT.host": UAT_HOST, "UAT.username": "svc_uat_7f3", "UAT.password": UAT_PASSWORD, "UAT.pin": "k7q"}


@pytest.mark.invariant("CR-2")
def test_every_value_shows_the_same_dots_and_the_eye_shows_only_its_row_until_pressed_again(win):
    """C1: values of 19, 11, 14 and 3 characters show the same dots; the eye of one row shows that value, and it stays
    shown while another row's value is typed, which shows no letter of it; pressed again: dots."""
    cards(win, EYE_CARDS, EYE_VALUES)

    def steps(window):
        choose(window, "UAT")
        hidden = [shown(row(window, key)) for key in ("host", "username", "password", "pin")]
        assert dots(hidden[0]) and hidden == [hidden[0]] * 4, f"the values show so: {hidden}"
        password = row(window, "password")
        eye(password).click()
        settle()
        assert shown(row(window, "password")) == UAT_PASSWORD
        assert [shown(row(window, k)) for k in ("host", "username", "pin")] == [hidden[0]] * 3

        username = value_box(row(window, "username"))
        activate(window)
        QTest.mouseClick(username, Qt.LeftButton)
        QTest.keyClick(username, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(username, "svc-new-9z")
        assert shown(row(window, "password")) == UAT_PASSWORD, "the open eye closed while another row was typed in"
        assert dots(username.displayText()), f"a value being typed shows: {username.displayText()!r}"
        QTest.keyClick(username, Qt.Key_Tab)
        settle()
        assert shown(row(window, "password")) == UAT_PASSWORD
        assert dots(shown(row(window, "username"))), shown(row(window, "username"))

        eye(row(window, "password")).click()
        settle()
        assert shown(row(window, "password")) == hidden[0]
        press(window, "Cancel")

    open_credentials(win, steps)


@pytest.mark.invariant("CR-2")
def test_a_value_shown_by_its_eye_is_dots_again_when_the_window_is_opened_again(win):
    """C2: the eye opened, the window closed and opened again: the value shows as dots."""
    cards(win, EYE_CARDS, EYE_VALUES)

    def show_and_close(window):
        choose(window, "UAT")
        eye(row(window, "password")).click()
        settle()
        assert shown(row(window, "password")) == UAT_PASSWORD
        press(window, "Cancel")

    def look_again(window):
        choose(window, "UAT")
        assert dots(shown(row(window, "password"))), shown(row(window, "password"))
        assert shown(row(window, "password")) == shown(row(window, "host"))
        press(window, "Cancel")

    open_credentials(win, show_and_close)
    open_credentials(win, look_again)


# --- a card that takes from another, pointers, circles, deleting and renaming what is used (CR-3) ------------------

USED = {"UAT": {"rows": {"host": None, "password": None}}, "PROD": {"rows": {"password": None}},
        "DEFAULT": {"uses": "UAT", "rows": {"archive_password": "PROD.password"}}}
USED_VALUES = {"UAT.host": UAT_HOST, "UAT.password": UAT_PASSWORD, "PROD.password": PROD_PASSWORD}


@pytest.mark.invariant("CR-3")
def test_a_card_takes_its_values_from_another_chosen_in_the_window_and_its_own_row_wins(win):
    """B1: DEFAULT, with no rows, is made to take its values from UAT and given a row username of its own: after Save
    DEFAULT.username is its own value, DEFAULT.host and DEFAULT.password are UAT's."""
    cards(win, {"UAT": {"rows": {"host": None, "username": None, "password": None}}, "DEFAULT": {"rows": {}}},
          {"UAT.host": UAT_HOST, "UAT.username": "svc_uat_7f3", "UAT.password": UAT_PASSWORD})

    def steps(window):
        choose(window, "DEFAULT")
        uses = window.findChild(QComboBox, "uses")
        uses.setCurrentIndex(uses.findText("UAT"))
        settle()
        add_row(window, "username", "svc-default-own")
        press(window, "Save")

    open_credentials(win, steps)

    assert cards_of(win)[1] == ("DEFAULT", "UAT", [("username", None)])
    assert value_of(win, "DEFAULT.username") == "svc-default-own"
    assert value_of(win, "DEFAULT.host") == UAT_HOST
    assert value_of(win, "DEFAULT.password") == UAT_PASSWORD


@pytest.mark.invariant("CR-3")
def test_a_row_made_to_point_at_another_row_shows_where_has_no_eye_and_holds_no_value(win, monkeypatch,
                                                                                        credential_store):
    """A row of DEFAULT made by right-click > Point at another row… shows the row it points at and has no eye; after
    Save the list holds the pointer, the store no value of the row, and the row's value is PROD.password's."""
    cards(win, {"UAT": {"rows": {"password": None}}, "PROD": {"rows": {"password": None}}, "DEFAULT": {"rows": {}}},
          {"UAT.password": UAT_PASSWORD, "PROD.password": PROD_PASSWORD})
    offered = picked_row(monkeypatch, "PROD.password")

    def steps(window):
        choose(window, "DEFAULT")
        add_row(window, "archive_password")
        row_menu(window, row(window, "archive_password"), "Point at another row…")
        assert {"PROD.password", "UAT.password"} <= set(offered), offered
        pointer = row(window, "archive_password")
        assert "PROD.password" in shown(pointer), shown(pointer)
        assert eye(pointer) is None, "a pointer row has an eye"
        press(window, "Save")

    open_credentials(win, steps)

    assert cards_of(win)[2] == ("DEFAULT", None, [("archive_password", "PROD.password")])
    assert value_of(win, "DEFAULT.archive_password") == PROD_PASSWORD
    assert not any(name.upper().startswith("DEFAULT.") for name in card_entries(credential_store))


@pytest.mark.invariant("CR-3")
def test_a_circle_is_refused_when_it_is_made_with_a_message_naming_it(win, monkeypatch, credential_store):
    """A5: UAT made to take its values from DEFAULT, which takes from UAT; PROD.password made to point at
    DEFAULT.archive_password, which points at PROD.password: each is refused at once with a message saying circle and
    naming both cards; Save then stores nothing of either."""
    cards(win, USED, USED_VALUES)
    before = files(win, credential_store)
    offered = picked_row(monkeypatch, "DEFAULT.archive_password")

    def steps(window):
        choose(window, "UAT")
        uses = window.findChild(QComboBox, "uses")
        uses.setCurrentIndex(uses.findText("DEFAULT"))
        settle()
        said = "\n".join(messages(win))
        assert "circle" in said.lower() and "UAT" in said and "DEFAULT" in said, said or "no message"

        choose(window, "PROD")
        row_menu(window, row(window, "password"), "Point at another row…")
        assert "DEFAULT.archive_password" in offered, offered
        said = "\n".join(messages(win))
        assert "circle" in said.lower() and "PROD" in said and "DEFAULT" in said, said or "no message"
        press(window, "Save")

    open_credentials(win, steps)

    assert files(win, credential_store) == before


@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-3")
def test_deleting_a_card_or_row_that_another_uses_is_refused_naming_who(win, credential_store):
    """B2: deleting UAT (DEFAULT takes its values from it) and the row PROD.password (DEFAULT.archive_password points
    at it): each refused with a message naming the user; both stay, and Save changes nothing."""
    cards(win, USED, USED_VALUES)
    before = files(win, credential_store)

    def steps(window):
        choose(window, "UAT")
        press(window, "Delete")
        said = "\n".join(messages(win))
        assert "DEFAULT" in said, said or "no message"
        assert "UAT" in card_names(window)

        choose(window, "PROD")
        row_menu(window, row(window, "password"), "Delete row")
        said = "\n".join(messages(win))
        assert names(said, "DEFAULT", "archive_password"), said or "no message"
        assert keys(window) == ["password", ""]
        press(window, "Save")

    open_credentials(win, steps)

    assert files(win, credential_store) == before


@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-3")
def test_renaming_a_card_or_key_follows_into_cards_that_take_from_it_and_rows_that_point_at_it(win, monkeypatch,
                                                                                            credential_store):
    """B3: UAT renamed TEST (Rename…) and PROD's key password renamed secret_key (its key box): after Save DEFAULT
    takes its values from TEST and its row points at PROD.secret_key; every value is under its new name only."""
    cards(win, USED, USED_VALUES)
    asked_names(monkeypatch, "TEST")

    def steps(window):
        choose(window, "UAT")
        press(window, "Rename…")
        assert card_names(window) == ["TEST", "PROD", "DEFAULT"]
        choose(window, "PROD")
        type_into(key_box(row(window, "password")), "secret_key")
        press(window, "Save")

    open_credentials(win, steps)

    assert cards_of(win) == [("TEST", None, [("host", None), ("password", None)]),
                             ("PROD", None, [("secret_key", None)]),
                             ("DEFAULT", "TEST", [("archive_password", "PROD.secret_key")])]
    assert card_entries(credential_store) == {"TEST.host": UAT_HOST, "TEST.password": UAT_PASSWORD,
                                              "PROD.secret_key": PROD_PASSWORD}
    assert value_of(win, "DEFAULT.archive_password") == PROD_PASSWORD
    assert value_of(win, "DEFAULT.host") == UAT_HOST


# --- a short value (CR-6) ------------------------------------------------------------------------------------------

@pytest.mark.invariant("CR-6")
def test_a_value_shorter_than_4_characters_or_empty_has_a_note_on_its_row(win):
    """A8, A9, A10: stored values of 3 characters and of none have the note on their row, one of 4 characters and a
    longer one do not; typed values get it and lose it as they are made shorter or longer."""
    cards(win, {"UAT": {"rows": {"host": None, "pin": None, "blank": None, "code": None}}},
          {"UAT.host": UAT_HOST, "UAT.pin": "k7q", "UAT.blank": "", "UAT.code": "k7q9"})

    def steps(window):
        choose(window, "UAT")
        assert "too short" in notes(row(window, "pin"))
        assert "too short" in notes(row(window, "blank"))
        assert "too short" not in notes(row(window, "code"))
        assert "too short" not in notes(row(window, "host"))
        add_row(window, "otp", "x1")
        assert "too short" in notes(row(window, "otp"))
        type_into(value_box(row(window, "code")), "abc")
        assert "too short" in notes(row(window, "code"))
        type_into(value_box(row(window, "pin")), "k7q9z")
        assert "too short" not in notes(row(window, "pin"))
        press(window, "Cancel")

    open_credentials(win, steps)


# --- names (CR-14) -------------------------------------------------------------------------------------------------

@pytest.mark.invariant("CR-14")
def test_a_card_or_key_named_as_one_that_exists_in_other_case_is_refused(win, monkeypatch, credential_store):
    """B4: with UAT there, Add… uat and Rename… PROD to Uat are refused with a message; a key PASSWORD beside password
    is refused when typed or at the latest on Save: the list keeps one UAT with one row password, its value as it
    was."""
    cards(win, {"UAT": {"rows": {"password": None}}}, {"UAT.password": UAT_PASSWORD})
    asked_names(monkeypatch, "uat", "PROD", "Uat")

    def steps(window):
        press(window, "Add…")
        assert messages(win), "a second card uat was not refused"
        assert card_names(window) == ["UAT"]
        press(window, "Add…")
        choose(window, "PROD")
        press(window, "Rename…")
        assert messages(win), "renaming PROD to Uat was not refused"
        assert card_names(window) == ["UAT", "PROD"]

        choose(window, "UAT")
        type_into(key_box(rows(window)[-1]), "PASSWORD")
        if not messages(win):  # (not refused as it was typed: then on Save)
            type_into(value_box(row(window, "PASSWORD")), "made-up-pass-9")
            press(window, "Save")
            assert messages(win), "a second key PASSWORD beside password was not refused"

    open_credentials(win, steps)

    assert [(name, rows_) for name, _, rows_ in cards_of(win) if name.casefold() == "uat"] == \
        [("UAT", [("password", None)])]
    assert value_of(win, "UAT.password") == UAT_PASSWORD


@pytest.mark.invariant("CR-14")
@pytest.mark.parametrize("what", ["card", "key"])
@pytest.mark.parametrize("name, why", [("U.AT", "dot"), ("U AT", "space")], ids=["dot", "space"])
def test_a_name_with_a_dot_or_a_space_is_refused_with_a_message_saying_why(win, monkeypatch, credential_store, what,
                                                                           name, why):
    """B4: a card added or a key typed with a dot or a space is refused (when given, or at the latest on Save) with a
    message saying so; nothing of it is stored."""
    cards(win, {"UAT": {"rows": {"password": None}}}, {"UAT.password": UAT_PASSWORD})
    before = files(win, credential_store)
    asked_names(monkeypatch, name)

    def steps(window):
        if what == "card":
            press(window, "Add…")
            said = messages(win)
            assert card_names(window) == ["UAT"]
        else:
            choose(window, "UAT")
            type_into(key_box(rows(window)[-1]), name)
            said = messages(win)
            if not said:  # (not refused as it was typed: then on Save)
                type_into(value_box(row(window, name)), "made-up-value-1")
                press(window, "Save")
                said = messages(win)
        assert any(why in text.lower() for text in said), said or "no message"

    open_credentials(win, steps)

    assert files(win, credential_store) == before


@pytest.mark.invariant("CR-14")
def test_names_of_one_character_100_characters_and_other_alphabets_are_taken(win, monkeypatch, credential_store):
    """B4: cards X, a 100-character name and Zürich_Ключ-1, and a key пароль, are made and stored as given."""
    long_name = "C" * 100
    asked_names(monkeypatch, "X", long_name, "Zürich_Ключ-1")

    def steps(window):
        for _ in range(3):
            press(window, "Add…")
        choose(window, "X")
        add_row(window, "пароль", "made-up-value-2", paste=True)
        press(window, "Save")

    open_credentials(win, steps)

    assert messages(win) == []
    assert [name for name, _, _ in cards_of(win)] == ["X", long_name, "Zürich_Ключ-1"]
    assert value_of(win, "X.пароль") == "made-up-value-2"


# --- the list and the store disagree (CR-15) -----------------------------------------------------------------------

@pytest.mark.invariant("CR-15")
def test_a_listed_key_with_no_stored_value_shows_no_value_stored_on_its_row(win):
    """D1: UAT lists host and password, the store holds only host: the window opens with no error, the row password
    says "no value stored", the row host does not."""
    cards(win, {"UAT": {"rows": {"host": None, "password": None}}}, {"UAT.host": UAT_HOST})

    def steps(window):
        choose(window, "UAT")
        assert "no value stored" in notes(row(window, "password"))
        assert "no value stored" not in notes(row(window, "host"))
        press(window, "Cancel")

    open_credentials(win, steps)
    assert messages(win) == []


@pytest.mark.invariant("CR-15")
def test_a_stored_value_not_listed_is_replaced_when_its_row_is_added_and_saved(win, credential_store):
    """D2: the store holds UAT.token, the list does not: the window shows no such row; the row token added with a new
    value and saved: the store holds the new value, and a run gets it."""
    cards(win, {"UAT": {"rows": {"host": None}}}, {"UAT.host": UAT_HOST})
    plant(credential_store, {"UAT.token": "old-unlisted-token-1"})

    def steps(window):
        choose(window, "UAT")
        assert keys(window) == ["host", ""]
        add_row(window, "token", "new-listed-token-2")
        press(window, "Save")

    open_credentials(win, steps)

    assert cards_of(win) == [("UAT", None, [("host", None), ("token", None)])]
    assert card_entries(credential_store)["UAT.token"] == "new-listed-token-2"
    assert value_of(win, "UAT.token") == "new-listed-token-2"


# --- two windows; a store that refuses (CR-1, CR-13) ----------------------------------------------------------------

@pytest.mark.invariant("CR-1")
def test_when_two_windows_save_the_later_save_is_refused_with_a_message_and_nothing_is_lost(win, qtbot,
                                                                                           credential_store):
    """C3: two editor windows each open Credentials; the second saves a new UAT.host, then the first, which changed
    UAT.password, presses Save: refused with a message, the first window stays open, the store keeps the second's
    host and the password as it was."""
    from taskloom.editor.window import MainWindow

    cards(win, {"UAT": {"rows": {"host": None, "password": None}}}, {"UAT.host": UAT_HOST, "UAT.password": UAT_PASSWORD})
    other = MainWindow(Home(win.home.root))
    qtbot.addWidget(other)
    other.show()

    def in_second(second):
        choose(second, "UAT")
        type_into(value_box(row(second, "host")), "second-window-host-2")
        press(second, "Save")
        assert not second.isVisible()

    def in_first(first):
        choose(first, "UAT")
        type_into(value_box(row(first, "password")), "first-window-pass-1")
        open_credentials(other, in_second)
        press(first, "Save")
        assert messages(win), "the later Save was not refused with a message"
        assert first.isVisible(), "the window whose Save was refused closed (what was typed in it is lost)"

    open_credentials(win, in_first)

    assert card_entries(credential_store) == {"UAT.host": "second-window-host-2", "UAT.password": UAT_PASSWORD}


@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-13")
def test_a_store_that_cannot_be_used_on_save_is_said_and_nothing_is_stored(win, monkeypatch, credential_store):
    """C4: the store becomes unusable (locked) while the window is open; Save says the credential store cannot be used,
    the window stays open, and neither the list nor the store has changed."""
    cards(win, {"UAT": {"rows": {"host": None, "password": None}}}, {"UAT.host": UAT_HOST, "UAT.password": UAT_PASSWORD})
    before = files(win, credential_store)
    asked_names(monkeypatch, "PROD")

    def steps(window):
        choose(window, "UAT")
        type_into(value_box(row(window, "password")), "made-up-pass-9")
        press(window, "Add…")
        choose(window, "PROD")
        add_row(window, "host", "prod-db.example.test")
        monkeypatch.setenv("TEST_CREDENTIAL_STORE_MODE", "unavailable")
        press(window, "Save")
        said = "\n".join(messages(win))
        assert "credential store" in said.lower(), said or "no message"
        assert window.isVisible()

    open_credentials(win, steps)

    assert files(win, credential_store) == before


@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-13")
def test_a_store_that_refuses_midway_through_a_save_leaves_the_list_and_every_value_as_before(win, monkeypatch,
                                                                                             credential_store):
    """C4: three values changed and a card added; the store refuses the second value written: a message, the window
    stays open, the list is as it was and every value already written is put back."""
    cards(win, {"UAT": {"rows": {"host": None, "password": None, "token": None}}},
          {"UAT.host": UAT_HOST, "UAT.password": UAT_PASSWORD, "UAT.token": "made-up-token-1"})
    before = files(win, credential_store)
    asked_names(monkeypatch, "PROD")

    def steps(window):
        choose(window, "UAT")
        for key, new in (("host", "new-host-1"), ("password", "new-pass-1"), ("token", "new-token-1")):
            type_into(value_box(row(window, key)), new)
        press(window, "Add…")
        choose(window, "PROD")
        add_row(window, "host", "prod-db.example.test")
        monkeypatch.setattr(keyring.core, "_keyring_backend", RefusesSecondWrite())
        press(window, "Save")
        assert messages(win), "a Save the store refused said nothing"
        assert window.isVisible()

    open_credentials(win, steps)

    assert files(win, credential_store) == before


# --- deleting removes from the store; long values (CR-12, CR-17) -----------------------------------------------------

@pytest.mark.invariant("CR-12")
@pytest.mark.invariant("CR-17")
def test_deleting_a_row_or_a_card_in_the_window_removes_its_values_from_the_store(win, credential_store):
    """A row whose value is 5,000 characters (in parts) and a whole card deleted in the window and saved: every entry
    of theirs is gone from the store; the other row keeps its value."""
    cards(win, {"UAT": {"rows": {"host": None, "password": None}}, "PROD": {"rows": {"password": None}}},
          {"UAT.host": UAT_HOST, "PROD.password": PROD_PASSWORD})
    Home(win.home.root).set_secret("UAT.password", LONG)
    assert len(card_entries(credential_store)) > 3, "the 5,000 characters are not stored in parts"

    def steps(window):
        choose(window, "UAT")
        row_menu(window, row(window, "password"), "Delete row")
        choose(window, "PROD")
        press(window, "Delete")
        press(window, "Save")

    open_credentials(win, steps)

    assert cards_of(win) == [("UAT", None, [("host", None)])]
    assert card_entries(credential_store) == {"UAT.host": UAT_HOST}


@pytest.mark.invariant("CR-17")
def test_a_long_value_pasted_in_the_window_is_stored_whole_in_a_store_like_windows_and_shown_whole(win, monkeypatch,
                                                                                                   credential_store):
    """A13 in a store like Windows' (names in any case one entry, an entry over 1,280 UTF-16 units refused): 5,000
    characters pasted as a value and saved: no message, no entry over the limit, read back whole; opened again, its eye
    shows it whole."""
    monkeypatch.setenv("TEST_CREDENTIAL_STORE_MODE", "case-insensitive,size-limit")
    cards(win, {"UAT": {"rows": {"host": None}}}, {"UAT.host": UAT_HOST})

    def paste(window):
        choose(window, "UAT")
        add_row(window, "certificate", LONG, paste=True)
        press(window, "Save")

    def look(window):
        choose(window, "UAT")
        eye(row(window, "certificate")).click()
        settle()
        assert shown(row(window, "certificate")) == LONG
        press(window, "Cancel")

    open_credentials(win, paste)

    assert messages(win) == []
    assert value_of(win, "UAT.certificate") == LONG
    assert all(utf16_units(part) <= 1280 for part in card_entries(credential_store).values())
    open_credentials(win, look)


# --- renaming what open flows use (CR-16) -------------------------------------------------------------------------

def api(url: str, **config) -> dict:
    return {"type": "servers.api_request", "config": {"url": url, **config}}


def note(message: str) -> dict:
    return {"type": "logic.inspect", "config": {"message": message}}


def python(code: str) -> dict:
    return {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": code, "use_environment": False}}


ORDERS = {  # every kind of reference to UAT.password; UAT2.token and the word UAT are no reference to it
    "call_api": api("https://orders.example.test/a", headers=[{"key": "X-Pass", "value": "secret:UAT.password"}]),
    "bearer_call": api("https://orders.example.test/b", auth="Bearer token", secret="UAT.password"),
    "show_note": note("UAT report {{secret:UAT.password}} and {{secret:UAT2.token}}"),
    "run_code": python('p = secret("UAT.password")\nh = secret("UAT.host")'),
}
USING_PASSWORD = list(ORDERS)


def flow_file(path, blocks: dict):
    """The flow `blocks`, each started by its Manual Start."""
    flow = {"blocks": {"manual": {"type": "trigger.manual"}, **blocks},
            "edges": [f"manual.start -> {name}.after" for name in blocks]}
    path.write_text(yaml.safe_dump(flow, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def open_flows(win, tmp_path):
    """UAT, UAT2 and their values; open in the editor: orders_flow (ORDERS), billing_flow (UAT.host only),
    unrelated_flow (no reference); nightly_flow (UAT.password) only on disk."""
    cards(win, {"UAT": {"rows": {"host": None, "password": None}}, "UAT2": {"rows": {"token": None}}},
          {"UAT.host": UAT_HOST, "UAT.password": UAT_PASSWORD, "UAT2.token": "made-up-token-2"})
    folder = tmp_path / "flows"
    folder.mkdir()
    paths = {"orders": flow_file(folder / "orders_flow.yaml", ORDERS),
             "billing": flow_file(folder / "billing_flow.yaml", {"billing_note": note("{{secret:UAT.host}}")}),
             "unrelated": flow_file(folder / "unrelated_flow.yaml", {"plain_note": note("UAT is only a word here")}),
             "nightly": flow_file(folder / "nightly_flow.yaml", {"nightly_note": note("{{secret:UAT.password}}")})}
    tabs = {name: win.open_flow(paths[name]) for name in ("orders", "billing", "unrelated")}
    assert all(tabs.values())
    return paths, tabs


def unsaved(win, tab) -> bool:
    return win.tabs.tabText(win.tabs.indexOf(tab)).endswith("*")


def save_tab(win, tab) -> dict:
    """File > Save with the flow's tab chosen: the blocks of the file written."""
    win.tabs.setCurrentWidget(tab)
    menu_item(win, "File", "Save").trigger()
    settle()
    return yaml.safe_load(tab.doc.path.read_text(encoding="utf-8"))["blocks"]


def references_in(blocks: dict) -> dict:
    """Each ORDERS block's reference, as its setting holds it."""
    return {"call_api": [h["value"] for h in blocks["call_api"]["config"]["headers"]],
            "bearer_call": blocks["bearer_call"]["config"]["secret"],
            "show_note": blocks["show_note"]["config"]["message"],
            "run_code": blocks["run_code"]["config"]["code"]}


def validate(win, path) -> subprocess.CompletedProcess:
    """`taskloom validate` of the flow file, as a terminal starts it, on the editor's Taskloom folder."""
    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root)}  # (with the test's credential store)
    return subprocess.run([sys.executable, "-m", "taskloom.cli", "validate", str(path)], env=env, cwd=path.parent,
                          capture_output=True, text=True, errors="replace", timeout=120)


def error_lines(checked: subprocess.CompletedProcess) -> list[str]:
    return [line for line in (checked.stdout + checked.stderr).splitlines() if line.startswith("error:")]


def lists_users(box: str, flows: list[str], blocks: list[str], not_listed: list[str]):
    for name in flows + blocks:
        assert name in box, f"the warning does not name {name}:\n{box}"
    for name in not_listed:
        assert name not in box, f"the warning names {name}, which does not use it:\n{box}"


@pytest.mark.invariant("CR-16")
def test_renaming_a_card_open_flows_use_and_rewriting_changes_them_on_save_each_unsaved_and_undoable(
        win, monkeypatch, open_flows):
    """J1: UAT renamed TEST, "also rewrite" chosen in the warning, which names the open flows and blocks using UAT
    (not the one that does not). Nothing changes until Save; then each open flow using UAT shows an unsaved change and
    holds TEST in every reference to UAT (the word UAT and UAT2.token as they were); Undo in it takes it back. The
    flow not open is unchanged, and Validate names its reference to UAT."""
    paths, tabs = open_flows
    on_disk = {name: path.read_bytes() for name, path in paths.items()}
    asked_names(monkeypatch, "TEST")
    box = answer_next_box("rewrite")

    def steps(window):
        choose(window, "UAT")
        press(window, "Rename…")
        wait_for(lambda: box, 10, "renaming a card open flows use showed no warning")
        lists_users(box[0], ["orders_flow.yaml", "billing_flow.yaml"], USING_PASSWORD + ["billing_note"],
                    ["unrelated_flow.yaml", "plain_note"])
        assert not any(unsaved(win, tab) for tab in tabs.values()), "the flows changed before Save"
        press(window, "Save")

    open_credentials(win, steps)

    assert unsaved(win, tabs["orders"]) and unsaved(win, tabs["billing"])
    assert not unsaved(win, tabs["unrelated"])
    assert save_tab(win, tabs["billing"])["billing_note"]["config"]["message"] == "{{secret:TEST.host}}"
    assert references_in(save_tab(win, tabs["orders"])) == {
        "call_api": ["secret:TEST.password"], "bearer_call": "TEST.password",
        "show_note": "UAT report {{secret:TEST.password}} and {{secret:UAT2.token}}",
        "run_code": 'p = secret("TEST.password")\nh = secret("TEST.host")'}
    win.tabs.setCurrentWidget(tabs["orders"])
    menu_item(win, "Edit", "Undo").trigger()
    assert references_in(save_tab(win, tabs["orders"])) == references_in(ORDERS)

    assert paths["nightly"].read_bytes() == on_disk["nightly"]
    assert paths["unrelated"].read_bytes() == on_disk["unrelated"]
    checked = validate(win, paths["nightly"])
    assert checked.returncode != 0 and any("nightly_note" in line and "UAT" in line for line in error_lines(checked)), \
        checked.stdout + checked.stderr
    assert [name for name, _, _ in cards_of(win)] == ["TEST", "UAT2"]
    assert value_of(win, "TEST.password") == UAT_PASSWORD


@pytest.mark.invariant("CR-16")
def test_renaming_a_key_open_flows_use_only_leaves_them_and_validate_names_the_old_references(win, open_flows):
    """J2: UAT's key password renamed pass_word, "rename only" chosen in the warning, which names orders_flow and its
    blocks (not billing_flow, which uses UAT.host only). After Save no flow has changed and Validate names each old
    reference with its block."""
    paths, tabs = open_flows
    on_disk = paths["orders"].read_bytes()
    box = answer_next_box("only")

    def steps(window):
        choose(window, "UAT")
        type_into(key_box(row(window, "password")), "pass_word")
        wait_for(lambda: box, 10, "renaming a key open flows use showed no warning")
        lists_users(box[0], ["orders_flow.yaml"], USING_PASSWORD,
                    ["billing_flow.yaml", "billing_note", "unrelated_flow.yaml"])
        press(window, "Save")

    open_credentials(win, steps)

    assert not any(unsaved(win, tab) for tab in tabs.values())
    assert paths["orders"].read_bytes() == on_disk
    assert cards_of(win)[0] == ("UAT", None, [("host", None), ("pass_word", None)])
    assert value_of(win, "UAT.pass_word") == UAT_PASSWORD
    checked = validate(win, paths["orders"])
    errors = error_lines(checked)
    for block in USING_PASSWORD:
        assert any(f"{block}:" in line and names(line, "UAT", "password") for line in errors), \
            f"Validate does not name {block}'s UAT.password:\n{checked.stdout + checked.stderr}"


@pytest.mark.invariant("CR-16")
def test_choosing_to_rewrite_the_open_flows_then_cancel_changes_nothing(win, open_flows, credential_store):
    """J3: UAT's key password renamed pass_word, "also rewrite" chosen, then Cancel: no flow changed, the list and the
    store as they were."""
    paths, tabs = open_flows
    before = files(win, credential_store)
    box = answer_next_box("rewrite")

    def steps(window):
        choose(window, "UAT")
        type_into(key_box(row(window, "password")), "pass_word")
        wait_for(lambda: box, 10, "renaming a key open flows use showed no warning")
        press(window, "Cancel")

    open_credentials(win, steps)

    assert not any(unsaved(win, tab) for tab in tabs.values())
    assert references_in(save_tab(win, tabs["orders"])) == references_in(ORDERS)
    assert files(win, credential_store) == before
