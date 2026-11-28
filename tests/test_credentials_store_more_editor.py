"""Stage 5 (credentials): what the editor's windows offer and refuse from the cards listed, beyond
tests/test_credentials_window.py and tests/test_credentials_editor.py: every row the Connections window offers for a
password, in order, and a circle refused when it is made also when a row's name is none. Promises:
docs/promises/credentials.yaml (CR-3, CR-4)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QComboBox  # noqa: E402

import test_credentials_window as W  # noqa: E402
from test_credentials_editor import in_connections  # noqa: E402

watch = W.watch  # (autouse: the Credentials window is handed to its steps; a box no step expected fails the test)


@pytest.mark.invariant("CR-4")
def test_the_connections_window_offers_each_cards_rows_then_the_rows_it_takes_from_the_card_it_uses(
        win, monkeypatch, credential_store):
    """UAT and PROD with rows; DEFAULT takes from UAT and has a row pointing at PROD.token; MID takes from DEFAULT and
    has no row: offered are each card's own rows, then what it takes (MID.archive through DEFAULT, after MID.token,
    which no card it takes from has), in the list's order, after "Type a new value"."""
    W.cards(win, {"UAT": {"rows": {"host": None, "password": None}},
                  "PROD": {"rows": {"password": None, "token": None}},
                  "DEFAULT": {"uses": "UAT", "rows": {"archive": "PROD.token"}},
                  "MID": {"uses": "DEFAULT", "rows": {}}}, {})
    offered = []

    def steps(dialog):
        picker = dialog.findChild(QComboBox, "credential")
        offered.extend(picker.itemText(i) for i in range(picker.count()))

    in_connections(win, monkeypatch, steps)

    assert offered == ["Type a new value", "UAT.host", "UAT.password", "PROD.password", "PROD.token",
                       "DEFAULT.archive", "DEFAULT.host", "DEFAULT.password", "MID.host", "MID.password", "MID.archive"]


@pytest.mark.invariant("CR-3")
def test_a_circle_of_two_cards_whose_one_row_is_named_none_is_refused_when_it_is_made(win, credential_store):
    """A and B each have one row, named none: A made to take its values from B is taken; B then made to take from A
    is refused at once with the message naming the circle."""
    W.cards(win, {"A": {"rows": {"none": None}}, "B": {"rows": {"none": None}}}, {})
    said = {}

    def steps(window):
        for card, uses in (("A", "B"), ("B", "A")):
            W.choose(window, card)
            box = window.findChild(QComboBox, "uses")
            box.setCurrentIndex(box.findText(uses))
            W.settle()
            said[f"{card} uses {uses}"] = W.messages(win)

    W.open_credentials(win, steps)

    assert said == {"A uses B": [], "B uses A": [
        "Not changed\nCircle in credentials: A -> B -> A: a card or row would take its value from itself in the end."]}
