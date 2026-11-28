"""Credentials on the PC (docs/promises/credentials.yaml): what the actions of the window Tools > Credentials… do, in
the real editor (offscreen), as tests/test_credentials_window.py drives it (its helpers, its watch of the boxes, the
tests' stand-in credential store): the boxes Add…, Rename… and Point at another row… ask in (the real Qt boxes,
answered as a person answers them), the names they refuse and the messages saying why, the question about renaming
what open flows use and the references it rewrites, the right-click menu of a row's key, deleting, and Save: what it
stores in credentials.yaml and the store, and the messages when it cannot. Made-up values only.

What the tests take as given of the window, beside the contract in tests/test_credentials_window.py's docstring:
- Every box the window shows (a question, a refusal, a name or a row asked) is the Credentials window's own: it shows
  over it (its parent widget), not as a window of its own.
- Add… asks in a box titled "Add card": "Name of the new card (e.g. UAT, PROD):", empty; the card added is the one
  chosen. Rename… asks in "Rename card": "New name of the card PROD:", holding PROD. Point at another row… asks in
  "Point at another row": "DEFAULT.archive_password takes the value of:", offering the rows of the other cards in the
  list's order, not to be typed in, the row it points at (else the first) chosen.
- A refusal is a warning titled "Not changed" whose text is the reason as a sentence; a Save that cannot be done is
  one titled "Not saved".
- The question about renaming what open flows use is titled "Used by open flows", with the buttons "Rename only",
  "Also rewrite the open flows" and Cancel; its text names each open flow (its file name) and its blocks, in the
  flows' order, as "flow: block, block", one flow a line.
- A reference names the card before its first dot (CR-14: the dot separates card and key).
- The right-click menu of a row's key holds "Point at another row…" and "Delete row" first, then a separator, then
  the box's own items (Undo, …); on the empty row the first two cannot be chosen.
"""

import os
import sys
import time

import keyring.core
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QInputDialog, QLabel, QMessageBox, QPushButton  # noqa: E402
from taskloom_test_store import FileStore  # noqa: E402
from test_credentials_store import card_entries  # noqa: E402

import test_credentials_window as W  # noqa: E402
from taskloom.config import Home  # noqa: E402

# tests/test_credentials_window.py's fixtures, used here by name: its watch of the boxes (for every test) and the open
# flows that use the card UAT.
watch = W.watch
open_flows = W.open_flows

UAT_HOST, UAT_PASSWORD, PROD_PASSWORD = W.UAT_HOST, W.UAT_PASSWORD, W.PROD_PASSWORD
TWO = {"UAT": {"rows": {"host": None, "password": None}}, "PROD": {"rows": {"password": None}}}
TWO_VALUES = {"UAT.host": UAT_HOST, "UAT.password": UAT_PASSWORD, "PROD.password": PROD_PASSWORD}


# --- the boxes the window shows, as a person sees and answers them --------------------------------------------------

class Box(list):
    """What the next box showed, noted as the watch answers it (its _look appends the box's text here): its title,
    text, buttons and the window it shows over; for a box asking a name or a row, its question, what it holds and
    offers, and whether it can be typed in. `given` is typed (or chosen) in it before its button is pressed."""

    def __init__(self, given: str | None = None):
        super().__init__()
        self.given = given

    def append(self, text):
        box = QApplication.activeModalWidget()
        seen = {"title": box.windowTitle(), "over": box.parentWidget(),
                "buttons": sorted(W.plain(b.text()) for b in box.findChildren(QAbstractButton) if b.isVisible())}
        if isinstance(box, QInputDialog):
            seen |= {"question": box.labelText(), "holds": box.textValue(), "offers": box.comboBoxItems(),
                     "typed_in": box.isComboBoxEditable()}
            if self.given is not None:
                box.setTextValue(self.given)
        else:
            seen["text"] = box.text()
        super().append(seen)


def titled(title: str) -> str:
    """A message box's title as it shows: macOS shows none (Qt leaves it out there, as Apple's guidelines ask)."""
    return "" if sys.platform == "darwin" else title


def answer(word: str, given: str | None = None) -> Box:
    """The next box is answered with its button holding `word`, `given` typed or chosen in it first."""
    box = Box(given)
    W.WATCH.answers.append((word, box))
    return box


@pytest.fixture
def over(win, monkeypatch) -> list:
    """The window each warning shows over, in order; the warnings are recorded as the win fixture records them (so
    W.messages(win) gives their title and text)."""
    found, recorded = [], QMessageBox.warning
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda parent, *a, **k: found.append(parent) or recorded(parent, *a, **k)))
    return found


def refused(win, over, window, text: str):
    """The one message since last asked is the refusal `text`, over the window."""
    assert W.messages(win) == [f"Not changed\n{text}"]
    assert over == [window], "the refusal is not the Credentials window's"
    over.clear()


def menu_of(window, row_) -> list[tuple[str, bool]]:
    """Right-click the row's key box: the menu's items top to bottom, each (its text, "-" for a separator; whether it
    can be chosen); the menu is closed with nothing chosen."""
    box = W.key_box(row_)
    point = box.mapTo(window, box.rect().center())
    seen, done = [], []

    def act(deadline):  # (a menu waits for you inside the right-click)
        menu = QApplication.activePopupWidget()
        if menu is None and time.monotonic() < deadline:
            QTimer.singleShot(20, lambda: act(deadline))
            return
        if menu is not None:
            seen.extend(("-" if a.isSeparator() else W.plain(a.text()), a.isEnabled()) for a in menu.actions())
            menu.close()
        done.append(1)

    W.activate(window)
    QTimer.singleShot(0, lambda: act(time.monotonic() + 3))
    QTest.mouseClick(window.windowHandle(), Qt.RightButton, Qt.NoModifier, point)
    W.wait_for(lambda: done, 60, "the right-click's menu was not closed")
    W.settle()
    return seen


def help_text(window) -> str:
    return "\n".join(label.text() for label in window.findChildren(QLabel) if label.isVisibleTo(window))


def chosen(window) -> str | None:
    item = W.card_list(window).currentItem()
    return item.text() if item is not None and item.isSelected() else None


def save_button(window) -> QPushButton:
    return next(b for b in window.findChildren(QPushButton) if W.plain(b.text()) == "Save" and b.isVisible())


def typing(edit, text: str):
    """Click the box, select what it holds and type `text` over it, without leaving it."""
    W.activate(edit.window())
    QTest.mouseClick(edit, Qt.LeftButton)
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, text)


class CountsWrites(FileStore):
    """The tests' store, noting the name of each entry written or removed."""

    def __init__(self):
        super().__init__()
        self.written = []

    def set_password(self, service, username, password):
        self.written.append(username)
        super().set_password(service, username, password)

    def delete_password(self, service, username):
        self.written.append(username)
        super().delete_password(service, username)


# --- Add… and Rename…: what they ask, the card added chosen, Cancel (CR-1) -----------------------------------------

@pytest.mark.invariant("CR-1")
def test_add_asks_the_name_over_the_window_and_the_card_added_is_chosen_for_its_rows(win, credential_store):
    """Add… asks "Name of the new card (e.g. UAT, PROD):" in a box "Add card" over the window, empty; the card given
    (STAGE) is chosen at once: the row typed next is STAGE's, and Save stores it there."""
    W.cards(win, TWO, TWO_VALUES)
    asked = answer("OK", "STAGE")

    def steps(window):
        W.choose(window, "UAT")
        W.press(window, "Add…")
        assert asked == [{"title": "Add card", "over": window, "buttons": ["Cancel", "OK"],
                          "question": "Name of the new card (e.g. UAT, PROD):", "holds": "", "offers": [],
                          "typed_in": False}]
        assert W.card_names(window) == ["UAT", "PROD", "STAGE"]
        assert chosen(window) == "STAGE"
        assert W.keys(window) == [""]
        W.add_row(window, "host", "stage-db.example.test")
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win) == [("UAT", None, [("host", None), ("password", None)]), ("PROD", None, [("password", None)]),
                               ("STAGE", None, [("host", None)])]
    assert card_entries(credential_store) == {**TWO_VALUES, "STAGE.host": "stage-db.example.test"}


@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-16")
def test_rename_asks_over_the_window_holding_the_name_and_cancel_or_the_same_name_changes_nothing(
        win, credential_store, open_flows):
    """Rename… on UAT, which open flows use, asks "New name of the card UAT:" in a box "Rename card" over the window,
    holding UAT. Cancel, and OK on the name as it was: nothing is said or asked (no question about the open flows),
    the card keeps its name, and Save changes nothing."""
    before = W.files(win, credential_store)
    cancelled, same = answer("Cancel", "TEST"), answer("OK")

    def steps(window):
        W.choose(window, "UAT")
        W.press(window, "Rename…")
        assert cancelled == [{"title": "Rename card", "over": window, "buttons": ["Cancel", "OK"],
                              "question": "New name of the card UAT:", "holds": "UAT", "offers": [],
                              "typed_in": False}]
        W.press(window, "Rename…")
        assert len(same) == 1 and same[0]["holds"] == "UAT"
        assert W.messages(win) == []
        assert W.card_names(window) == ["UAT", "UAT2"]
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.files(win, credential_store) == before
    assert not any(W.unsaved(win, tab) for tab in open_flows[1].values())


# --- names refused, and why (CR-14); a Save after a refusal (CR-1) -------------------------------------------------

@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-14")
def test_a_card_name_refused_by_add_or_rename_says_why_over_the_window_and_a_save_after_it_stores(
        win, credential_store, over):
    """Add… given U.AT, uat or nothing, and Rename… of PROD given PR OD or Uat: each is refused at once by a warning
    "Not changed" over the window saying why, as a sentence; the cards stay as they were. A Save after the refusals
    is not refused: it stores the value typed after them and closes the window."""
    W.cards(win, TWO, TWO_VALUES)
    refusals = [
        ("Add…", "U.AT", "The card 'U.AT' holds a dot, which separates the card and the key: use letters, digits, _ "
                         "and -."),
        ("Add…", "uat", "There is a card UAT already: upper and lower case do not make another name."),
        ("Add…", "", "The card has no name."),
        ("Rename…", "PR OD", "The card 'PR OD' holds a space: use letters, digits, _ and -."),
        ("Rename…", "Uat", "There is a card UAT already: upper and lower case do not make another name.")]

    def steps(window):
        W.choose(window, "PROD")
        for button, name, why in refusals:
            answer("OK", name)
            W.press(window, button)
            refused(win, over, window, why)
            assert W.card_names(window) == ["UAT", "PROD"]
        W.type_into(W.value_box(W.row(window, "password")), "made-up-pass-3")
        W.press(window, "Save")
        assert not window.isVisible(), "a Save after a refusal was refused"

    W.open_credentials(win, steps)

    assert W.cards_of(win) == [("UAT", None, [("host", None), ("password", None)]), ("PROD", None, [("password", None)])]
    assert card_entries(credential_store) == {**TWO_VALUES, "PROD.password": "made-up-pass-3"}


@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-14")
def test_a_key_name_refused_says_why_over_the_window_and_its_box_shows_what_it_had_again(win, credential_store, over):
    """In UAT: the key password typed over as pass word, the key host rubbed out, and HOST typed in the empty row:
    each is refused at once by a warning "Not changed" over the window saying why, as a sentence; each key box shows
    what it had again (the empty row empty). Save then closes the window and changes nothing."""
    W.cards(win, TWO, TWO_VALUES)
    before = W.files(win, credential_store)

    def steps(window):
        W.choose(window, "UAT")
        W.type_into(W.key_box(W.row(window, "password")), "pass word")
        refused(win, over, window, "The key 'pass word' holds a space: use letters, digits, _ and -.")
        assert W.keys(window) == ["host", "password", ""]
        W.type_into(W.key_box(W.row(window, "host")), "")
        refused(win, over, window, "The key has no name.")
        assert W.keys(window) == ["host", "password", ""]
        W.type_into(W.key_box(W.rows(window)[-1]), "HOST")
        refused(win, over, window, "There is a key host already: upper and lower case do not make another name.")
        assert W.keys(window) == ["host", "password", ""]
        W.press(window, "Save")
        assert not window.isVisible(), "a Save after a refusal was refused"

    W.open_credentials(win, steps)

    assert W.files(win, credential_store) == before


@pytest.mark.invariant("CR-16")
def test_a_key_left_as_it_was_or_the_empty_row_left_empty_asks_and_says_nothing(win, credential_store, open_flows):
    """UAT.password, which open flows use, typed over with password again, and x typed then rubbed out in the empty
    row: no question about the open flows, no message, no new row; Save changes nothing."""
    before = W.files(win, credential_store)

    def steps(window):
        W.choose(window, "UAT")
        W.type_into(W.key_box(W.row(window, "password")), "password")
        last = W.key_box(W.rows(window)[-1])
        W.activate(window)
        QTest.mouseClick(last, Qt.LeftButton)
        QTest.keyClicks(last, "x")
        QTest.keyClick(last, Qt.Key_Backspace)
        QTest.keyClick(last, Qt.Key_Tab)
        W.settle()
        assert W.messages(win) == []
        assert W.keys(window) == ["host", "password", ""]
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.files(win, credential_store) == before
    assert not any(W.unsaved(win, tab) for tab in open_flows[1].values())


@pytest.mark.invariant("CR-1")
def test_after_a_key_is_typed_and_left_the_typing_goes_on_in_its_value_box(win, credential_store):
    """A key typed in UAT's empty row (port), and the key password renamed pass_2, each left with Tab: the typing goes
    on in that row's value box, and Save stores what was typed there under the new key."""
    W.cards(win, TWO, TWO_VALUES)

    def steps(window):
        W.choose(window, "UAT")
        for was, key, value in (("", "port", "15432"), ("password", "pass_2", "made-up-pass-4")):
            W.type_into(W.key_box(W.rows(window)[-1] if was == "" else W.row(window, was)), key)
            here = QApplication.focusWidget()
            assert here is W.value_box(W.row(window, key)), f"after the key {key}, the typing goes on in {here!r}"
            QTest.keyClicks(here, value)
            QTest.keyClick(here, Qt.Key_Tab)
            W.settle()
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win) == [("UAT", None, [("host", None), ("pass_2", None), ("port", None)]),
                               ("PROD", None, [("password", None)])]
    assert card_entries(credential_store) == {"UAT.host": UAT_HOST, "UAT.pass_2": "made-up-pass-4",
                                              "UAT.port": "15432", "PROD.password": PROD_PASSWORD}


@pytest.mark.invariant("CR-16")
def test_cancel_in_the_question_about_open_flows_keeps_the_key_and_a_save_after_it_changes_nothing(
        win, credential_store, open_flows):
    """UAT.password, which open flows use, typed over as pass_word: Cancel in the question about the open flows; the
    key box shows password again, and Save closes the window and changes nothing, in the list or the flows."""
    before = W.files(win, credential_store)
    asked = answer("Cancel")

    def steps(window):
        W.choose(window, "UAT")
        W.type_into(W.key_box(W.row(window, "password")), "pass_word")
        assert len(asked) == 1 and asked[0]["title"] == titled("Used by open flows")
        assert W.keys(window) == ["host", "password", ""]
        W.press(window, "Save")
        assert not window.isVisible(), "a Save after the question was cancelled was refused"

    W.open_credentials(win, steps)

    assert W.files(win, credential_store) == before
    assert not any(W.unsaved(win, tab) for tab in open_flows[1].values())


# --- Save: what it stores, and the messages when it cannot (CR-1, CR-13) --------------------------------------------

@pytest.mark.invariant("CR-1")
def test_a_value_typed_and_saved_without_leaving_its_box_is_stored(win, credential_store):
    """A new UAT.password typed, and Save pressed while the typing is still in the box: it is stored."""
    W.cards(win, TWO, TWO_VALUES)

    def steps(window):
        W.choose(window, "UAT")
        typing(W.value_box(W.row(window, "password")), "made-up-pass-5")
        W.press(window, "Save")
        assert not window.isVisible()

    W.open_credentials(win, steps)

    assert card_entries(credential_store) == {**TWO_VALUES, "UAT.password": "made-up-pass-5"}


@pytest.mark.invariant("CR-1")
def test_a_save_after_the_credentials_were_saved_elsewhere_says_so_over_the_window_and_stores_nothing_of_it(
        win, credential_store, over):
    """While the window is open, taskloom secret stores a new row PROD.token; then Save of a new UAT.password: a
    warning "Not saved" over the window says the credentials were saved elsewhere and what to do; the window stays
    open; the store keeps PROD.token and the old UAT.password."""
    W.cards(win, TWO, TWO_VALUES)

    def steps(window):
        W.choose(window, "UAT")
        W.type_into(W.value_box(W.row(window, "password")), "made-up-pass-6")
        Home(win.home.root).set_secret("PROD.token", "made-up-token-3")
        W.press(window, "Save")
        assert W.messages(win) == [
            "Not saved\nThe credentials were saved elsewhere since this window opened (in another Credentials window, "
            "or with taskloom secret). Nothing of this window is saved, so nothing saved there is lost: Cancel, open "
            "Credentials again and make your changes there."]
        assert over == [window], "the message is not the Credentials window's"
        assert window.isVisible()

    W.open_credentials(win, steps)

    assert card_entries(credential_store) == {**TWO_VALUES, "PROD.token": "made-up-token-3"}


@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-13")
def test_a_save_the_store_cannot_take_says_why_over_the_window_and_that_nothing_is_saved(
        win, monkeypatch, credential_store, over):
    """The store locked while the window is open, then Save of a new UAT.password: a warning "Not saved" over the
    window says the credential store cannot be used and why, then that nothing is saved; the window stays open."""
    W.cards(win, TWO, TWO_VALUES)
    before = W.files(win, credential_store)

    def steps(window):
        W.choose(window, "UAT")
        W.type_into(W.value_box(W.row(window, "password")), "made-up-pass-8")
        monkeypatch.setenv("TEST_CREDENTIAL_STORE_MODE", "unavailable")
        W.press(window, "Save")
        said = W.messages(win)
        assert len(said) == 1 and said[0].startswith("Not saved\nThe credential store cannot be used: ") \
            and said[0].endswith("\n\nNothing is saved: the list and every value are as they were."), said
        assert over == [window], "the message is not the Credentials window's"
        assert window.isVisible()
        monkeypatch.delenv("TEST_CREDENTIAL_STORE_MODE")

    W.open_credentials(win, steps)

    assert W.files(win, credential_store) == before


@pytest.mark.invariant("CR-1")
def test_save_writes_to_the_store_only_the_value_changed(win, monkeypatch, credential_store):
    """Only UAT.password changed: Save writes that one entry of the store and leaves every other untouched (each
    write is an access the operating system's store may ask about)."""
    W.cards(win, TWO, TWO_VALUES)
    counting = CountsWrites()

    def steps(window):
        W.choose(window, "UAT")
        W.type_into(W.value_box(W.row(window, "password")), "made-up-pass-7")
        monkeypatch.setattr(keyring.core, "_keyring_backend", counting)
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert counting.written == ["UAT.password"]
    assert card_entries(credential_store) == {**TWO_VALUES, "UAT.password": "made-up-pass-7"}


# --- the question about renaming what open flows use, and the references rewritten (CR-16) -------------------------

QUESTION_END = ("\n\nRename only: they keep the old name, and Validate names it. Also rewrite: on Save each of them names "
                "{new} instead, as an unsaved change Undo takes back. Flows that are not open are not changed.")
QUESTION_BUTTONS = ["Also rewrite the open flows", "Cancel", "Rename only"]


@pytest.mark.invariant("CR-16")
def test_renaming_a_card_open_flows_use_asks_naming_each_flow_and_its_blocks_and_rename_only_leaves_them(
        win, credential_store, open_flows):
    """UAT renamed TEST: a question "Used by open flows" over the window names the card, then each open flow using it
    and its blocks using it, one flow a line ("flow: block, block", in the flows' and blocks' order), and says what
    each answer does; its buttons are Rename only, Also rewrite the open flows and Cancel. Rename only, then Save: the
    card is TEST with its values, and no flow has changed."""
    paths, tabs = open_flows
    on_disk = {name: path.read_bytes() for name, path in paths.items()}
    answer("OK", "TEST")
    asked = answer("only")

    def steps(window):
        W.choose(window, "UAT")
        W.press(window, "Rename…")
        assert asked == [{"title": titled("Used by open flows"), "over": window, "buttons": QUESTION_BUTTONS,
                          "text": "The card UAT is used by flows open in this window (flow: blocks):\n\n"
                                  "orders_flow.yaml: call_api, bearer_call, show_note, run_code\n"
                                  "billing_flow.yaml: billing_note" + QUESTION_END.format(new="TEST")}]
        assert W.card_names(window) == ["TEST", "UAT2"]
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert not any(W.unsaved(win, tab) for tab in tabs.values())
    assert {name: path.read_bytes() for name, path in paths.items()} == on_disk
    assert card_entries(credential_store) == {"TEST.host": UAT_HOST, "TEST.password": UAT_PASSWORD,
                                              "UAT2.token": "made-up-token-2"}


@pytest.mark.invariant("CR-16")
def test_renaming_a_key_open_flows_use_and_rewriting_names_the_new_key_in_exactly_the_references_to_it(
        win, credential_store, open_flows):
    """The key UAT.password renamed pass_word: the question names UAT.password and only the flow and blocks using that
    row (not billing_flow, which uses UAT.host); Also rewrite, then Save: orders_flow names UAT.pass_word in each
    reference to UAT.password and nothing else changes (UAT.host, UAT2.token, billing_flow)."""
    paths, tabs = open_flows
    asked = answer("rewrite")

    def steps(window):
        W.choose(window, "UAT")
        W.type_into(W.key_box(W.row(window, "password")), "pass_word")
        assert asked == [{"title": titled("Used by open flows"), "over": window, "buttons": QUESTION_BUTTONS,
                          "text": "UAT.password is used by flows open in this window (flow: blocks):\n\n"
                                  "orders_flow.yaml: call_api, bearer_call, show_note, run_code"
                                  + QUESTION_END.format(new="UAT.pass_word")}]
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.unsaved(win, tabs["orders"]) and not W.unsaved(win, tabs["billing"])
    assert W.references_in(W.save_tab(win, tabs["orders"])) == {
        "call_api": ["secret:UAT.pass_word"], "bearer_call": "UAT.pass_word",
        "show_note": "UAT report {{secret:UAT.pass_word}} and {{secret:UAT2.token}}",
        "run_code": 'p = secret("UAT.pass_word")\nh = secret("UAT.host")'}
    assert W.save_tab(win, tabs["billing"])["billing_note"]["config"]["message"] == "{{secret:UAT.host}}"
    assert card_entries(credential_store) == {"UAT.host": UAT_HOST, "UAT.pass_word": UAT_PASSWORD,
                                              "UAT2.token": "made-up-token-2"}


@pytest.mark.invariant("CR-14")
@pytest.mark.invariant("CR-16")
def test_a_reference_with_a_second_dot_names_the_card_before_the_first_and_follows_its_renaming(
        win, credential_store, tmp_path):
    """An open flow's note holds {{secret:UAT.pass.word}} (a key with a dot, which no key can be): the card it names is
    UAT, the name before the first dot, as for a run (CR-14). UAT renamed TEST with Also rewrite: the question names
    the flow and its note, and after Save the note names TEST.pass.word."""
    W.cards(win, {"UAT": {"rows": {"password": None}}}, {"UAT.password": UAT_PASSWORD})
    tab = win.open_flow(W.flow_file(tmp_path / "odd_flow.yaml", {"odd_note": W.note("{{secret:UAT.pass.word}}")}))
    answer("OK", "TEST")
    asked = answer("rewrite")

    def steps(window):
        W.choose(window, "UAT")
        W.press(window, "Rename…")
        assert len(asked) == 1 and "\n\nodd_flow.yaml: odd_note\n\n" in asked[0]["text"], asked
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.save_tab(win, tab)["odd_note"]["config"]["message"] == "{{secret:TEST.pass.word}}"


@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-16")
def test_renaming_a_card_or_row_added_in_the_window_asks_nothing_though_flows_are_open(
        win, credential_store, open_flows):
    """With flows open that use UAT: a card STAGE added and renamed STAGE2, and a row port added to UAT and renamed
    port_2, are renamed with no question and no message (no open flow can use what was not there when the window
    opened), and Save stores them under their new names."""
    answer("OK", "STAGE")
    answer("OK", "STAGE2")

    def steps(window):
        W.press(window, "Add…")
        W.add_row(window, "token", "made-up-token-4")
        W.press(window, "Rename…")
        W.choose(window, "UAT")
        W.add_row(window, "port", "15432")
        W.type_into(W.key_box(W.row(window, "port")), "port_2")
        assert W.messages(win) == []
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win) == [("UAT", None, [("host", None), ("password", None), ("port_2", None)]),
                               ("UAT2", None, [("token", None)]), ("STAGE2", None, [("token", None)])]
    assert card_entries(credential_store) == {"UAT.host": UAT_HOST, "UAT.password": UAT_PASSWORD,
                                              "UAT.port_2": "15432", "UAT2.token": "made-up-token-2",
                                              "STAGE2.token": "made-up-token-4"}


# --- renaming follows into the rows that point at what was renamed, and only those (CR-3) --------------------------

POINTING = {"PROD": {"rows": {"password": None, "host": None}}, "UAT": {"rows": {"password": None}},
            "DEFAULT": {"rows": {"a": "PROD.password", "b": "PROD.host", "c": "UAT.password"}}}
POINTING_VALUES = {"PROD.password": PROD_PASSWORD, "PROD.host": "prod-db.example.test", "UAT.password": UAT_PASSWORD}


@pytest.mark.invariant("CR-3")
def test_renaming_a_card_then_a_key_follows_into_the_rows_pointing_at_them_and_no_other(win, credential_store):
    """DEFAULT's rows point at PROD.password, PROD.host and UAT.password. PROD renamed LIVE: the two pointing at PROD
    follow (LIVE.password, LIVE.host); then LIVE's key password renamed secret_key: only the one pointing at it
    follows (LIVE.secret_key). Save stores the pointers so, and each name gives the value it gave before."""
    W.cards(win, POINTING, POINTING_VALUES)
    answer("OK", "LIVE")

    def steps(window):
        W.choose(window, "PROD")
        W.press(window, "Rename…")
        W.choose(window, "DEFAULT")
        assert [W.shown(W.row(window, key)) for key in "abc"] == ["→ LIVE.password", "→ LIVE.host", "→ UAT.password"]
        W.choose(window, "LIVE")
        W.type_into(W.key_box(W.row(window, "password")), "secret_key")
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win) == [("LIVE", None, [("secret_key", None), ("host", None)]),
                               ("UAT", None, [("password", None)]),
                               ("DEFAULT", None, [("a", "LIVE.secret_key"), ("b", "LIVE.host"), ("c", "UAT.password")])]
    assert [W.value_of(win, f"DEFAULT.{key}") for key in "abc"] == [PROD_PASSWORD, "prod-db.example.test",
                                                                     UAT_PASSWORD]


# --- deleting (CR-1, CR-3, CR-12) ----------------------------------------------------------------------------------

USERS = {"UAT": {"rows": {"host": None, "password": None}}, "PROD": {"rows": {"password": None}},
         "DEFAULT": {"uses": "UAT", "rows": {"archive_password": "PROD.password"}},
         "STAGE": {"rows": {"db": "UAT.host", "archive": "PROD.password"}}}


@pytest.mark.invariant("CR-3")
@pytest.mark.invariant("CR-12")
def test_deleting_what_others_use_is_refused_naming_each_user_and_a_row_only_rows_point_at_can_go(
        win, credential_store, over):
    """Delete of UAT (DEFAULT takes its values from it, STAGE.db points at UAT.host) and Delete row of PROD.password
    (two rows point at it): each refused by a warning "Not changed" over the window naming every user, joined by
    "; ". UAT.password, which no row points at, is deleted though DEFAULT takes UAT's values: Save removes its value."""
    W.cards(win, USERS, TWO_VALUES)

    def steps(window):
        W.choose(window, "UAT")
        W.press(window, "Delete")
        refused(win, over, window, "UAT is not deleted, it is used: DEFAULT takes its values from UAT; STAGE.db points "
                                   "at UAT.host.")
        W.choose(window, "PROD")
        W.row_menu(window, W.row(window, "password"), "Delete row")
        refused(win, over, window, "PROD.password is not deleted, it is used: DEFAULT.archive_password points at "
                                   "PROD.password; STAGE.archive points at PROD.password.")
        W.choose(window, "UAT")
        W.row_menu(window, W.row(window, "password"), "Delete row")
        assert W.messages(win) == []
        assert W.keys(window) == ["host", ""]
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win)[0] == ("UAT", None, [("host", None)])
    assert card_entries(credential_store) == {"UAT.host": UAT_HOST, "PROD.password": PROD_PASSWORD}


THREE = {"UAT": {"rows": {"host": None}}, "PROD": {"rows": {"password": None}}, "STAGE": {"rows": {"token": None}}}
THREE_VALUES = {"UAT.host": UAT_HOST, "PROD.password": PROD_PASSWORD, "STAGE.token": "made-up-token-5"}
KEYS_OF = {"UAT": ["host", ""], "PROD": ["password", ""], "STAGE": ["token", ""]}


def deleted(window, card: str, left: list[str]):
    """Delete pressed on `card`: the cards `left`, one of them chosen in the list, and the rows shown are its own with
    the empty row last (CR-1)."""
    W.choose(window, card)
    W.press(window, "Delete")
    assert W.card_names(window) == left
    assert chosen(window) in left, f"after {card} was deleted no card is chosen"
    assert W.keys(window) == KEYS_OF[chosen(window)], \
        f"after {card} was deleted, {chosen(window)} is chosen in the list but the rows shown are {W.keys(window)}"


@pytest.mark.invariant("CR-1")
@pytest.mark.parametrize("card, left", [("UAT", ["PROD", "STAGE"]), ("PROD", ["UAT", "STAGE"])])
def test_after_a_card_above_others_is_deleted_the_rows_shown_are_those_of_the_card_chosen(win, card, left):
    """UAT, PROD and STAGE, none used: the first or the middle card deleted; a card is chosen in the list, and the
    rows shown are its own (not another card's, not none)."""
    W.cards(win, THREE, THREE_VALUES)
    W.open_credentials(win, lambda window: deleted(window, card, left))


@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-12")
def test_after_the_last_card_is_deleted_the_rows_shown_are_the_chosen_cards_and_none_when_no_card_is_left(
        win, credential_store):
    """UAT, PROD and STAGE, none used: STAGE deleted (the last in the list), then PROD (the last then), then UAT:
    after each the rows shown are those of the card chosen in the list; with no card left, no row shows and the
    window says to add a card. Save leaves no card and no value."""
    W.cards(win, THREE, THREE_VALUES)

    def steps(window):
        deleted(window, "STAGE", ["UAT", "PROD"])
        deleted(window, "PROD", ["UAT"])
        W.press(window, "Delete")
        assert W.card_names(window) == [] and W.rows(window) == []
        assert "Add a card (e.g. UAT or PROD), then its rows: a key and its value." in help_text(window)
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win) == [] and card_entries(credential_store) == {}


# --- Point at another row… (CR-3) ---------------------------------------------------------------------------------

POINTS = {"UAT": {"rows": {"host": None, "password": None}}, "PROD": {"rows": {"password": None}},
          "DEFAULT": {"rows": {"archive_password": "PROD.password", "token": None}}}


@pytest.mark.invariant("CR-3")
def test_point_at_another_row_offers_the_other_cards_rows_holding_where_it_points_and_cancel_changes_nothing(
        win, credential_store):
    """Point at another row… on DEFAULT.archive_password asks "DEFAULT.archive_password takes the value of:" in a box
    "Point at another row" over the window, offering UAT.host, UAT.password and PROD.password (not to be typed in),
    holding PROD.password, where it points: Cancel changes nothing. On DEFAULT.token, which holds a value, it holds
    the first row offered; UAT.password chosen: the row shows → UAT.password, and Save stores the pointer and removes
    the value it had."""
    W.cards(win, POINTS, {**TWO_VALUES, "DEFAULT.token": "made-up-token-6"})
    offered = ["UAT.host", "UAT.password", "PROD.password"]
    cancelled, picked = answer("Cancel", "UAT.host"), answer("OK", "UAT.password")

    def steps(window):
        W.choose(window, "DEFAULT")
        W.row_menu(window, W.row(window, "archive_password"), "Point at another row…")
        assert cancelled == [{"title": "Point at another row", "over": window, "buttons": ["Cancel", "OK"],
                              "question": "DEFAULT.archive_password takes the value of:", "holds": "PROD.password",
                              "offers": offered, "typed_in": False}]
        assert W.shown(W.row(window, "archive_password")) == "→ PROD.password"
        W.row_menu(window, W.row(window, "token"), "Point at another row…")
        assert len(picked) == 1 and picked[0]["holds"] == "UAT.host" and picked[0]["offers"] == offered
        assert W.shown(W.row(window, "token")) == "→ UAT.password"
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win)[2] == ("DEFAULT", None, [("archive_password", "PROD.password"), ("token", "UAT.password")])
    assert card_entries(credential_store) == TWO_VALUES
    assert W.value_of(win, "DEFAULT.token") == UAT_PASSWORD


@pytest.mark.invariant("CR-3")
def test_point_at_another_row_with_no_row_of_another_card_is_refused_saying_to_add_one(win, credential_store, over):
    """ONLY.host, and the card EMPTY with no row: Point at another row… on ONLY.host is refused by a warning "Not
    changed" over the window saying there is no row of another card to point at."""
    W.cards(win, {"ONLY": {"rows": {"host": None}}, "EMPTY": {"rows": {}}}, {"ONLY.host": UAT_HOST})
    before = W.files(win, credential_store)

    def steps(window):
        W.choose(window, "ONLY")
        W.row_menu(window, W.row(window, "host"), "Point at another row…")
        refused(win, over, window, "There is no row of another card to point at: add one first.")
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.files(win, credential_store) == before


@pytest.mark.invariant("CR-3")
def test_pointing_a_row_into_a_circle_is_refused_naming_it_and_the_row_keeps_where_it_pointed(
        win, credential_store, over):
    """UAT.alias points at DEFAULT.archive_password, which points at PROD.password: pointing DEFAULT.archive_password
    at UAT.alias is refused by a warning "Not changed" over the window naming the circle; the row still points at
    PROD.password, also after Save."""
    W.cards(win, {"UAT": {"rows": {"host": None, "alias": "DEFAULT.archive_password"}},
                  "PROD": {"rows": {"password": None}}, "DEFAULT": {"rows": {"archive_password": "PROD.password"}}},
            {"UAT.host": UAT_HOST, "PROD.password": PROD_PASSWORD})
    answer("OK", "UAT.alias")

    def steps(window):
        W.choose(window, "DEFAULT")
        W.row_menu(window, W.row(window, "archive_password"), "Point at another row…")
        refused(win, over, window, "Circle in credentials: UAT.alias -> DEFAULT.archive_password -> UAT.alias: a card "
                                   "or row would take its value from itself in the end.")
        assert W.shown(W.row(window, "archive_password")) == "→ PROD.password"
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win)[2] == ("DEFAULT", None, [("archive_password", "PROD.password")])
    assert W.value_of(win, "UAT.alias") == PROD_PASSWORD


@pytest.mark.invariant("CR-3")
def test_with_a_circle_already_stored_a_row_can_still_be_pointed_elsewhere(win, credential_store):
    """X takes its values from Y and Y from X (a circle in the stored list); pointing DEFAULT.archive at UAT.password,
    which makes no circle, is not refused, and Save stores it."""
    W.cards(win, {"X": {"uses": "Y", "rows": {}}, "Y": {"uses": "X", "rows": {}}, "UAT": {"rows": {"password": None}},
                  "DEFAULT": {"rows": {"archive": None}}},
            {"UAT.password": UAT_PASSWORD, "DEFAULT.archive": "made-up-archive-1"})
    answer("OK", "UAT.password")

    def steps(window):
        W.choose(window, "DEFAULT")
        W.row_menu(window, W.row(window, "archive"), "Point at another row…")
        assert W.messages(win) == []
        assert W.shown(W.row(window, "archive")) == "→ UAT.password"
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win)[3] == ("DEFAULT", None, [("archive", "UAT.password")])
    assert card_entries(credential_store) == {"UAT.password": UAT_PASSWORD}


# --- the right-click menu of a row's key (CR-1, CR-3) -------------------------------------------------------------

@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-3")
def test_a_rows_menu_starts_with_point_and_delete_then_the_boxs_own_items_and_the_empty_rows_cannot_be_chosen(win):
    """Right-click UAT.host's key: Point at another row… and Delete row, then a separator, then the key box's own
    items from Undo on; on the empty row's key the first two are there but cannot be chosen."""
    W.cards(win, TWO, TWO_VALUES)
    seen = []

    def steps(window):
        W.choose(window, "UAT")
        seen.append(menu_of(window, W.row(window, "host")))
        seen.append(menu_of(window, W.rows(window)[-1]))

    W.open_credentials(win, steps)

    for items, can in zip(seen, (True, False)):
        assert [text for text, _ in items[:3]] == ["Point at another row…", "Delete row", "-"], items
        assert items[3][0].startswith("Undo"), items
        assert [enabled for _, enabled in items[:2]] == [can, can], items
