"""Credentials on the PC (docs/promises/credentials.yaml): what the window Tools > Credentials… shows, in the real
editor (offscreen), as a person sees it: its title and labels and which of them are greyed, the help over the rows,
each row's key, value, where its value comes from and its eye, the placeholders and the dots, the boxes that can be
typed in and the one that has the focus, "Takes its values from", and where each box stands. The window is driven
as tests/test_credentials_window.py drives it (its helpers, and the contract in its docstring), with these of its
look besides: greyed text is drawn in the theme's placeholder colour; a row's QLabels say where its value comes from
and, for a short value, "Too short to hide in the Log: use 4 or more characters"; "Key" and "Value" are QLabels over
the rows; the rows are in the window's QScrollArea. Made-up values only."""

import os
import time

import keyring.core
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QCursor, QPalette  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QMessageBox, QPushButton, QScrollArea  # noqa: E402
from shiboken6 import isValid  # noqa: E402
from test_credentials_store import plant  # noqa: E402

import test_credentials_window as W  # noqa: E402
from taskloom.editor.canvas import STATUS_COLORS  # noqa: E402

watch = W.watch

DOTS = "●" * 12  # every value whose eye is shut (credentials.design.yaml: "the same 12 dots whatever its length")
SHORT_NOTE = "Too short to hide in the Log: use 4 or more characters"
HINT = "Right-click a row's key: Point at another row…, Delete row."
SHUT_TIP, OPEN_TIP = "Show this value (until you press it again or close the window)", "Hide this value"


def plain_help(card: str) -> str:
    return (f"Each value is kept in the operating system's credential store. Use one as secret:{card}.password, or "
            f"{{{{secret:{card}.password}}}} inside text.")


def taking_help(card: str, used: str) -> str:
    return f"{card} takes {used}'s rows (greyed); a row of its own with the same key wins."


# --- what the window shows, and where ------------------------------------------------------------------------------

def box(widget, window) -> QRect:
    """Where the widget stands in the window."""
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def label(window, text: str) -> QLabel:
    found = [lab for lab in window.findChildren(QLabel)
             if lab.text() == text and lab.window() is window and lab.isVisibleTo(window)]
    seen = [lab.text() for lab in window.findChildren(QLabel) if lab.isVisibleTo(window)]
    assert len(found) == 1, f"the window shows {len(found)} labels {text!r}: {seen}"
    return found[0]


def greyed(label_: QLabel) -> bool:
    """Drawn in the theme's placeholder colour, as greyed text is."""
    label_.ensurePolished()
    return label_.palette().color(QPalette.WindowText) == QApplication.palette().color(QPalette.PlaceholderText)


def rows_area(window) -> QScrollArea:
    found = window.findChildren(QScrollArea)
    assert len(found) == 1, f"the window has {len(found)} scroll areas"
    return found[0]


def help_label(window) -> QLabel:
    """The text at the top of the right side, over the rows."""
    left = box(rows_area(window), window).left()
    found = [lab for lab in window.findChildren(QLabel)
             if lab.window() is window and lab.isVisibleTo(window) and box(lab, window).left() >= left]
    return min(found, key=lambda lab: box(lab, window).top())


def where(row_) -> QLabel:
    """The row's label saying where its value comes from (its other label is the note on a short value)."""
    found = [lab for lab in row_.findChildren(QLabel) if lab.text() != SHORT_NOTE]
    assert len(found) == 1, [lab.text() for lab in row_.findChildren(QLabel)]
    return found[0]


def short_note(row_):
    found = [lab for lab in row_.findChildren(QLabel) if lab.text() == SHORT_NOTE and lab.isVisibleTo(row_)]
    return found[0] if found else None


def uses_box(window) -> QComboBox:
    return window.findChild(QComboBox, "uses")


def offered(window) -> list[str]:
    uses = uses_box(window)
    return [uses.itemText(i) for i in range(uses.count())]


def take_from(window, card: str):
    """"Takes its values from" set to `card` (or "(none)")."""
    uses = uses_box(window)
    assert uses.findText(card) >= 0, f"{card} is not offered: {offered(window)}"
    uses.setCurrentIndex(uses.findText(card))
    W.settle()


def near(a: int, b: int, by: int) -> bool:
    return abs(a - b) <= by


def lined_up(window):
    """Each row's key, value, where-label and eye on one line, every column lined up with the rows' and the
    titles' over them, the eye at the row's right end and a short value's note under its value."""
    rows_ = W.rows(window)
    key0, value0 = box(W.key_box(rows_[0]), window), box(W.value_box(rows_[0]), window)
    where0 = box(where(rows_[0]), window)
    title_key, title_value = box(label(window, "Key"), window), box(label(window, "Value"), window)
    said = f"titles {title_key} {title_value}, first row {key0} {value0}"
    assert title_key.center().y() == title_value.center().y(), said
    assert title_key.bottom() < key0.top(), said
    gaps = [box(b, window).top() - box(a, window).bottom() for a, b in zip(rows_, rows_[1:])]
    assert key0.top() - title_key.bottom() <= max(gaps) + 2, f"{said}, the rows {gaps} apart"
    assert near(title_key.left(), key0.left(), 6) and near(title_value.left(), value0.left(), 6), said
    for row_ in rows_:
        key, value, from_ = box(W.key_box(row_), window), box(W.value_box(row_), window), box(where(row_), window)
        line = box(row_, window)
        said = f"row {W.key_box(row_).text()!r}: key {key}, value {value}, where {from_}, row {line}"
        assert near(key.left(), key0.left(), 2) and near(key.width(), key0.width(), 2), said
        assert near(value.left(), value0.left(), 2) and near(value.right(), value0.right(), 4), said
        assert near(from_.left(), where0.left(), 2) and value.right() < from_.left(), said
        assert near(key.center().y(), value.center().y(), 1) and near(from_.center().y(), value.center().y(), 1), said
        eye = W.eye(row_)
        if eye is not None:
            at = box(eye, window)
            assert from_.right() < at.left() and near(at.center().y(), value.center().y(), 1), f"{said}, eye {at}"
            # (its column is at least 28 pixels wide: an eye drawn narrower, 24 on Windows and Linux, is at its left)
            assert near(at.right(), line.right(), 4), f"{said}, eye {at}"
        else:
            assert from_.right() <= line.right(), said
        note = short_note(row_)
        if note is not None:
            at = box(note, window)
            assert at.top() > value.bottom() and near(at.left(), value.left(), 2), f"{said}, note {at}"
            assert at.right() <= line.right(), f"{said}, note {at}"


def ink(icon) -> list[tuple[int, int, QColor]]:
    """The drawn pixels of the icon at the size it is drawn (18 px): (x, y, colour)."""
    image = icon.pixmap(QSize(18, 18)).toImage()
    return [(x, y, image.pixelColor(x, y)) for x in range(image.width()) for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 0]


def drawn_whole_in_the_text_colour(icon):
    drawn = ink(icon)
    assert len(drawn) > 20, f"the eye draws {len(drawn)} pixels"
    solid = {c.rgb() for _, _, c in drawn if c.alpha() == 255}
    assert solid == {QApplication.palette().text().color().rgb()}, [QColor(c).name() for c in solid]
    edge = [(x, y) for x, y, _ in drawn if x in (0, 17) or y in (0, 17)]
    assert edge == [], f"the drawing is cut at the icon's edge: {edge}"


def centre(icon) -> tuple[float, float]:
    drawn = ink(icon)
    weight = sum(c.alphaF() for _, _, c in drawn)
    return (sum((x + 0.5) * c.alphaF() for x, _, c in drawn) / weight,
            sum((y + 0.5) * c.alphaF() for _, y, c in drawn) / weight)


def paste_from_its_menu(window, edit):
    """Right-click the box and choose Paste in its menu."""
    point = edit.mapTo(window, edit.rect().center())
    seen, done = [], []

    def act(deadline):  # (a menu waits for you inside the right-click)
        menu = QApplication.activePopupWidget()
        if menu is None and time.monotonic() < deadline:
            QTimer.singleShot(20, lambda: act(deadline))
            return
        if menu is not None:
            seen.extend(W.plain(a.text()) for a in menu.actions() if a.text())
            item = next((a for a in menu.actions() if W.plain(a.text()).startswith("Paste") and a.isEnabled()), None)
            if item is not None:
                QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(item).center())
            if isValid(menu) and menu.isVisible():
                menu.close()
        done.append(1)

    QTimer.singleShot(0, lambda: act(time.monotonic() + 3))
    QCursor.setPos(window.mapToGlobal(point))
    QTest.mouseClick(window.windowHandle(), Qt.RightButton, Qt.NoModifier, point)
    W.wait_for(lambda: done, 60, "the right-click's menu was not closed")
    W.settle()
    assert any(s.startswith("Paste") for s in seen), f"the box's menu has no Paste: {seen or 'no menu'}"


# DEFAULT takes UAT's rows: UAT's host shows greyed in it, its own pin wins over UAT's, extra points at UAT.host.
TAKES = {"UAT": {"rows": {"host": None, "pin": None}},
         "DEFAULT": {"uses": "UAT", "rows": {"pin": None, "extra": "UAT.host"}}}
TAKES_VALUES = {"UAT.host": W.UAT_HOST, "UAT.pin": "k7q", "DEFAULT.pin": "z9"}


# --- the window, its labels and its help ---------------------------------------------------------------------------

@pytest.mark.invariant("CR-1")
def test_the_window_is_titled_credentials_over_its_editor_and_greys_its_help_hint_and_column_titles(win):
    """The window "Credentials" over the editor window it was chosen in; "Cards" and "Takes its values from:" in the
    text colour; the help (on several lines, inside the right side), the hint under the rows and the titles Key and
    Value greyed; the rows without a frame around them, taking the height between "Takes its values from" and the
    hint, which is over Save and Cancel at the bottom."""
    W.cards(win, TAKES, TAKES_VALUES)

    def steps(window):
        assert window.windowTitle() == "Credentials"
        assert window.windowHandle().transientParent() is win.windowHandle(), "the window is not over its editor"
        assert window.parentWidget() is win
        for text in ("Cards", "Takes its values from:"):
            assert not greyed(label(window, text)), f"{text!r} is greyed"
        help_ = label(window, plain_help("UAT"))
        assert help_ is help_label(window)
        for found in (help_, label(window, HINT), label(window, "Key"), label(window, "Value")):
            assert greyed(found), f"{found.text()!r} is not greyed"
        area = box(rows_area(window), window)
        at = box(help_, window)
        assert area.left() <= at.left() and at.right() <= area.right(), f"the help {at} is not over the rows {area}"
        assert at.height() >= 2 * help_.fontMetrics().lineSpacing(), "the help is not on several lines"
        assert rows_area(window).frameWidth() == 0, "the rows have a frame around them"
        uses, hint = box(uses_box(window), window), box(label(window, HINT), window)
        save = next(box(b, window) for b in window.findChildren(QPushButton) if W.plain(b.text()) == "Save")
        said = f"Takes its values from {uses}, the rows {area}, the hint {hint}, Save {save}"
        assert 0 <= area.top() - uses.bottom() <= 12 and 0 <= hint.top() - area.bottom() <= 12, said
        assert 0 <= save.top() - hint.bottom() <= 16 and window.height() - save.bottom() <= 16, said
        W.press(window, "Cancel")

    W.open_credentials(win, steps)


@pytest.mark.invariant("CR-1")
def test_the_help_says_how_to_begin_with_no_card_how_to_name_a_value_and_which_rows_a_card_takes(win):
    """No card: the help says to add one, there is no row and nothing to choose a card to take from. With cards, the
    first is chosen as the window opens and its rows shown; the help names the chosen card in secret:CARD.password and
    {{secret:CARD.password}}, or says which card's rows it takes."""
    def nothing_yet(window):
        assert help_label(window).text() == "Add a card (e.g. UAT or PROD), then its rows: a key and its value."
        assert W.card_names(window) == [] and W.rows(window) == []
        assert not uses_box(window).isEnabled() and offered(window) == []
        W.press(window, "Cancel")

    W.open_credentials(win, nothing_yet)

    W.cards(win, {"UAT": {"rows": {"host": None}}, "DEFAULT": {"uses": "UAT", "rows": {}}}, {"UAT.host": W.UAT_HOST})

    def two(window):
        chosen = W.card_list(window).currentItem()
        assert chosen is not None and chosen.text() == "UAT" and chosen.isSelected()
        assert W.keys(window) == ["host", ""]
        assert help_label(window).text() == plain_help("UAT")
        assert uses_box(window).isEnabled() and uses_box(window).currentText() == "(none)"
        W.choose(window, "DEFAULT")
        assert help_label(window).text() == taking_help("DEFAULT", "UAT")
        W.press(window, "Cancel")

    W.open_credentials(win, two)


# --- the rows ------------------------------------------------------------------------------------------------------

@pytest.mark.invariant("CR-3")
def test_a_card_that_takes_from_another_shows_those_rows_greyed_first_then_its_own_then_the_empty_row(win):
    """DEFAULT takes UAT's rows: UAT's host first, greyed "from UAT", its boxes not to be typed in and saying where to
    change it; DEFAULT's own pin, "own, wins over UAT"; its own extra; the empty row last. Made to take from (none),
    it shows only its own rows, and Save stores that."""
    W.cards(win, TAKES, TAKES_VALUES)

    def steps(window):
        W.choose(window, "DEFAULT")
        assert offered(window) == ["(none)", "UAT"]
        assert uses_box(window).currentText() == "UAT" and uses_box(window).isEnabled()
        rows_ = W.rows(window)
        assert W.keys(window) == ["host", "pin", "extra", ""]
        assert [where(r).text() for r in rows_] == ["from UAT", "<b>own</b>, wins over UAT", "", ""]
        assert greyed(where(rows_[0])) and not greyed(where(rows_[1]))
        host = rows_[0]
        tip = "UAT's row: change it in UAT, or add a row host here to replace it."
        for edit in (W.key_box(host), W.value_box(host)):
            assert not edit.isEnabled() and edit.toolTip() == tip
        assert [W.key_box(r).isEnabled() for r in rows_] == [False, True, True, True]
        assert [W.value_box(r).isEnabled() for r in rows_] == [False, True, True, False]
        assert [W.shown(r) for r in rows_] == [DOTS, DOTS, "→ UAT.host", ""]

        take_from(window, "(none)")
        assert W.keys(window) == ["pin", "extra", ""]
        assert [where(r).text() for r in W.rows(window)] == ["", "", ""]
        assert help_label(window).text() == plain_help("DEFAULT")
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.messages(win) == []
    assert W.cards_of(win)[1] == ("DEFAULT", None, [("pin", None), ("extra", "UAT.host")])


@pytest.mark.invariant("CR-2")
@pytest.mark.invariant("CR-15")
def test_every_box_says_what_goes_in_it_and_a_pointer_row_shows_where_it_points_and_takes_no_typing(win):
    """A stored value: 12 dots; a listed key with no stored value: "no value stored"; every key box: "key"; the empty
    row: "value", not to be typed in before its key; a pointer row: "→ PROD.host", no eye, a tooltip saying how to
    change it, and typing in it changes nothing."""
    W.cards(win, {"UAT": {"rows": {"host": None, "token": None, "db": "PROD.host"}}, "PROD": {"rows": {"host": None}}},
            {"UAT.host": W.UAT_HOST, "PROD.host": "prod-db.example.test"})

    def steps(window):
        assert W.keys(window) == ["host", "token", "db", ""]
        host, token, db, empty = W.rows(window)
        assert [W.key_box(r).placeholderText() for r in (host, token, db, empty)] == ["key"] * 4
        assert W.shown(host) == DOTS
        assert W.shown(token) == "" and W.value_box(token).placeholderText() == "no value stored"
        assert W.shown(empty) == "" and W.value_box(empty).placeholderText() == "value"
        assert W.key_box(empty).isEnabled() and not W.value_box(empty).isEnabled()
        assert W.shown(db) == "→ PROD.host" and W.eye(db) is None
        assert W.value_box(db).toolTip() == "Takes the value of PROD.host. Right-click the key to change it."
        pointer = W.value_box(db)
        W.activate(window)
        QTest.mouseClick(pointer, Qt.LeftButton)
        QTest.keyClicks(pointer, "x")
        assert pointer.displayText() == "→ PROD.host"
        W.press(window, "Cancel")

    W.open_credentials(win, steps)


@pytest.mark.invariant("CR-2")
def test_each_rows_eye_is_a_flat_toggle_drawn_whole_in_the_text_colour_that_says_what_it_does(win):
    """Shut: not pressed, "Show this value (until you press it again or close the window)", the eye-off drawing.
    Pressed: pressed in, "Hide this value", another drawing (the open eye, centred), the value shown; a row with no
    stored value shows nothing with its eye open. Each drawing whole, in the theme's text colour."""
    W.cards(win, {"UAT": {"rows": {"host": None, "token": None}}}, {"UAT.host": W.UAT_HOST})

    def steps(window):
        eye = W.eye(W.row(window, "host"))
        assert eye.autoRaise() and eye.isCheckable() and not eye.isChecked()
        assert eye.toolTip() == SHUT_TIP
        shut = eye.icon()
        drawn_whole_in_the_text_colour(shut)
        eye.click()
        W.settle()
        eye = W.eye(W.row(window, "host"))
        assert W.shown(W.row(window, "host")) == W.UAT_HOST
        assert eye.isChecked() and eye.toolTip() == OPEN_TIP
        drawn_whole_in_the_text_colour(eye.icon())
        assert eye.icon().pixmap(QSize(18, 18)).toImage() != shut.pixmap(QSize(18, 18)).toImage()
        x, y = centre(eye.icon())
        assert near(round(x * 4), 36, 1) and near(round(y * 4), 36, 1), f"the open eye is drawn off centre: {x}, {y}"
        token = W.row(window, "token")
        W.eye(token).click()
        W.settle()
        assert W.shown(W.row(window, "token")) == ""
        eye.click()
        W.settle()
        eye = W.eye(W.row(window, "host"))
        assert W.shown(W.row(window, "host")) == DOTS and not eye.isChecked() and eye.toolTip() == SHUT_TIP
        W.press(window, "Cancel")

    W.open_credentials(win, steps)


@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-6")
def test_the_columns_line_up_under_key_and_value_one_line_a_row_and_grow_with_the_window(win):
    """In a card of its own and in one taking another's rows: each row's key, value, where its value comes from and
    eye on one line, lined up under Key and Value, the eye at the right end, a short value's note (in the colour of a
    running block) under its value. A wider window gives the cards 2 parts of the width it adds and the rows 5; a
    row gives its key 2 parts of what it adds and its value 3."""
    W.cards(win, TAKES, TAKES_VALUES)

    def steps(window):
        lined_up(window)
        note = short_note(W.row(window, "pin"))
        assert note is not None and short_note(W.row(window, "host")) is None
        note.ensurePolished()
        assert note.palette().color(QPalette.WindowText).rgb() == QColor(STATUS_COLORS["running"]).rgb()
        W.choose(window, "DEFAULT")
        lined_up(window)
        widths = []
        for width in (1100, 1400):
            window.resize(width, 460)
            W.settle()
            lined_up(window)
            first = W.rows(window)[0]
            widths.append([W.card_list(window).width(), rows_area(window).width(), W.key_box(first).width(),
                           W.value_box(first).width()])
        cards_, area, key, value = (b - a for a, b in zip(*widths))
        assert cards_ > 0 and near(round(area / cards_ * 10), 25, 2), f"cards +{cards_}, rows +{area}"
        assert key > 0 and near(round(value / key * 10), 15, 1), f"key +{key}, value +{value}"
        W.press(window, "Cancel")

    W.open_credentials(win, steps)


# --- typing ---------------------------------------------------------------------------------------------------------

TYPING = {"UAT": {"rows": {"host": None, "pin": None, "db": "PROD.host"}}, "PROD": {"rows": {"host": None}}}
TYPING_VALUES = {"UAT.host": W.UAT_HOST, "UAT.pin": "uat-pin-4471", "PROD.host": "prod-db.example.test"}


@pytest.mark.invariant("CR-2")
def test_a_value_box_empties_to_be_typed_in_shows_its_dots_again_when_left_and_with_its_eye_open_edits_in_place(
        win, credential_store):
    """Clicked with its eye shut, a value box is empty, and left without typing it shows its dots again; what is
    typed (nothing selected first) is the whole new value. With the eye open, a click keeps the value to be edited;
    a pointer row keeps "→ PROD.host"."""
    W.cards(win, TYPING, TYPING_VALUES)

    def steps(window):
        W.activate(window)
        host = W.value_box(W.row(window, "host"))
        QTest.mouseClick(host, Qt.LeftButton)
        assert host.displayText() == ""
        QTest.keyClick(host, Qt.Key_Tab)
        W.settle()
        assert W.shown(W.row(window, "host")) == DOTS

        pin = W.value_box(W.row(window, "pin"))
        QTest.mouseClick(pin, Qt.LeftButton)
        QTest.keyClicks(pin, "new-pin-77")
        QTest.keyClick(pin, Qt.Key_Tab)
        W.settle()
        W.eye(W.row(window, "pin")).click()
        W.settle()
        assert W.shown(W.row(window, "pin")) == "new-pin-77"
        QTest.mouseClick(pin, Qt.LeftButton)
        assert pin.displayText() == "new-pin-77"
        QTest.keyClick(pin, Qt.Key_End)
        QTest.keyClicks(pin, "-b")
        QTest.keyClick(pin, Qt.Key_Tab)
        W.settle()
        assert W.shown(W.row(window, "pin")) == "new-pin-77-b"

        db = W.value_box(W.row(window, "db"))
        QTest.mouseClick(db, Qt.LeftButton)
        assert db.displayText() == "→ PROD.host"
        QTest.keyClick(db, Qt.Key_Tab)
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.value_of(win, "UAT.pin") == "new-pin-77-b"
    assert W.value_of(win, "UAT.host") == W.UAT_HOST


@pytest.mark.invariant("CR-2")
def test_the_eye_pressed_while_typing_shows_what_is_typed_and_paste_from_the_boxs_menu_adds_to_what_is_typed(
        win, credential_store):
    """Typing in a value box, the eye pressed: what is typed shows; pressed again: dots, the value typed kept.
    Typed, then pasted with the box's own right-click menu: both make the value."""
    W.cards(win, TYPING, TYPING_VALUES)

    def steps(window):
        W.activate(window)
        pin = W.value_box(W.row(window, "pin"))
        QTest.mouseClick(pin, Qt.LeftButton)
        QTest.keyClicks(pin, "typed-pin-5")
        QTest.mouseClick(W.eye(W.row(window, "pin")), Qt.LeftButton)
        W.settle()
        assert W.shown(W.row(window, "pin")) == "typed-pin-5"
        QTest.mouseClick(W.eye(W.row(window, "pin")), Qt.LeftButton)
        W.settle()
        assert W.shown(W.row(window, "pin")) == DOTS

        host = W.value_box(W.row(window, "host"))
        QTest.mouseClick(host, Qt.LeftButton)
        QTest.keyClicks(host, "uat-db")
        QApplication.clipboard().setText("-2.example.test")
        paste_from_its_menu(window, host)
        QTest.keyClick(host, Qt.Key_Tab)
        W.settle()
        W.eye(W.row(window, "host")).click()
        W.settle()
        assert W.shown(W.row(window, "host")) == "uat-db-2.example.test"
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.value_of(win, "UAT.pin") == "typed-pin-5"
    assert W.value_of(win, "UAT.host") == "uat-db-2.example.test"


@pytest.mark.invariant("CR-1")
def test_a_key_typed_in_the_empty_row_leaves_the_typing_in_the_new_rows_value_box(win, credential_store):
    """A key typed in the empty row and Tab: the new row's value box has the focus, empty, so what is typed next is
    its value."""
    W.cards(win, {"UAT": {"rows": {"host": None}}}, {"UAT.host": W.UAT_HOST})

    def steps(window):
        W.type_into(W.key_box(W.rows(window)[-1]), "port")
        assert W.keys(window) == ["host", "port", ""]
        new = W.value_box(W.row(window, "port"))
        assert QApplication.focusWidget() is new and new.displayText() == ""
        QTest.keyClicks(QApplication.focusWidget(), "5432")
        QTest.keyClick(QApplication.focusWidget(), Qt.Key_Tab)
        W.settle()
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.value_of(win, "UAT.port") == "5432"


@pytest.mark.invariant("CR-1")
def test_a_key_typed_in_the_empty_row_then_another_card_clicked_shows_that_card_and_keeps_the_new_row(
        win, credential_store):
    """A key typed in the empty row and, without Tab, a click on another card: the window shows that card's rows
    (no error), and the card typed in has its new row when chosen again, and after Save."""
    W.cards(win, {"UAT": {"rows": {"host": None}}, "PROD": {"rows": {"user": None}}}, {"UAT.host": W.UAT_HOST})

    def steps(window):
        W.activate(window)
        last = W.key_box(W.rows(window)[-1])
        QTest.mouseClick(last, Qt.LeftButton)
        QTest.keyClicks(last, "port")
        W.choose(window, "PROD")
        W.settle()
        assert W.card_list(window).currentItem().text() == "PROD" and W.keys(window) == ["user", ""]
        W.choose(window, "UAT")
        assert W.keys(window) == ["host", "port", ""]
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win) == [("UAT", None, [("host", None), ("port", None)]), ("PROD", None, [("user", None)])]


# --- what a card takes its values from ----------------------------------------------------------------------------

@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-14")
def test_the_card_taken_from_shows_as_listed_and_a_save_without_changes_leaves_the_list_and_the_store_as_they_were(
        win, credential_store):
    """The list file writes DEFAULT's uat in lower case: "Takes its values from" shows UAT, as the card is listed,
    and UAT's rows show. A value left in the store under a row that points elsewhere stays. Save with no change
    leaves the list file and the store as they were."""
    W.cards(win, {"UAT": {"rows": {"host": None}}, "DEFAULT": {"uses": "uat", "rows": {"extra": "UAT.host"}}},
            {"UAT.host": W.UAT_HOST})
    plant(credential_store, {"DEFAULT.extra": "left-over-value-3"})
    before = W.files(win, credential_store)

    def steps(window):
        W.choose(window, "DEFAULT")
        assert offered(window) == ["(none)", "UAT"] and uses_box(window).currentText() == "UAT"
        assert W.keys(window) == ["host", "extra", ""]
        W.press(window, "Save")
        assert not window.isVisible()

    W.open_credentials(win, steps)

    assert W.files(win, credential_store) == before


@pytest.mark.invariant("CR-1")
def test_a_save_that_changes_one_value_writes_only_that_one(win, monkeypatch, credential_store):
    """One value changed of two stored: Save writes only it, so a store that takes no second write (locked after
    the first) saves it, with no message."""
    W.cards(win, {"UAT": {"rows": {"host": None, "password": None}}},
            {"UAT.host": W.UAT_HOST, "UAT.password": W.UAT_PASSWORD})

    def steps(window):
        W.type_into(W.value_box(W.row(window, "password")), "new-pass-31")
        monkeypatch.setattr(keyring.core, "_keyring_backend", W.RefusesSecondWrite())
        W.press(window, "Save")
        assert W.messages(win) == [] and not window.isVisible()

    W.open_credentials(win, steps)

    assert W.value_of(win, "UAT.password") == "new-pass-31"
    assert W.value_of(win, "UAT.host") == W.UAT_HOST


@pytest.mark.invariant("CR-3")
def test_a_refused_circle_leaves_the_card_taking_from_the_card_it_took_from(win, credential_store):
    """UAT takes from PROD, DEV from UAT: UAT made to take from DEV is refused (a circle), and "Takes its values from"
    shows PROD again, with PROD's rows; Save keeps PROD."""
    W.cards(win, {"UAT": {"uses": "PROD", "rows": {}}, "PROD": {"rows": {"host": None}}, "DEV": {"uses": "UAT", "rows": {}}},
            {"PROD.host": "prod-db.example.test"})

    def steps(window):
        assert uses_box(window).currentText() == "PROD"
        take_from(window, "DEV")
        said = W.messages(win)
        assert len(said) == 1 and "circle" in said[0].lower(), said
        assert uses_box(window).currentText() == "PROD"
        assert W.keys(window) == ["host", ""] and help_label(window).text() == taking_help("UAT", "PROD")
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win)[0] == ("UAT", "PROD", [])


@pytest.mark.invariant("CR-3")
def test_a_circle_found_in_the_list_file_does_not_stop_choosing_what_another_card_takes_from_or_undoing_it(
        win, credential_store):
    """The list file has UAT and PROD taking from each other: DEV can be made to take from UAT, and UAT from (none),
    with no message; Save stores both."""
    W.cards(win, {"UAT": {"uses": "PROD", "rows": {"host": None}}, "PROD": {"uses": "UAT", "rows": {"host": None}},
                  "DEV": {"rows": {}}}, {"UAT.host": W.UAT_HOST, "PROD.host": "prod-db.example.test"})

    def steps(window):
        W.choose(window, "DEV")
        take_from(window, "UAT")
        assert W.messages(win) == [] and uses_box(window).currentText() == "UAT"
        assert W.keys(window) == ["host", ""]
        W.choose(window, "UAT")
        take_from(window, "(none)")
        assert W.messages(win) == []
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert [(name, uses) for name, uses, _ in W.cards_of(win)] == [("UAT", None), ("PROD", "UAT"), ("DEV", "UAT")]


# --- the buttons, and a list that cannot be read --------------------------------------------------------------------

@pytest.mark.invariant("CR-1")
@pytest.mark.parametrize("button", ["Add…", "Rename…", "Delete"])
def test_enter_presses_save_also_while_add_rename_or_delete_has_the_focus(win, credential_store, button):
    """Tabbed to Add…, Rename… or Delete, Enter saves and closes the window, as anywhere in it; it adds, renames
    or deletes nothing."""
    W.cards(win, {"UAT": {"rows": {"host": None}}, "SPARE": {"rows": {}}}, {"UAT.host": W.UAT_HOST})

    def steps(window):
        W.choose(window, "SPARE")
        found = [b for b in window.findChildren(QPushButton) if W.plain(b.text()) == button and b.window() is window]
        assert len(found) == 1
        found[0].setFocus(Qt.TabFocusReason)
        assert QApplication.focusWidget() is found[0]
        QTest.keyClick(found[0], Qt.Key_Return)
        W.settle()
        assert not window.isVisible(), f"Enter on {button} did not save"

    W.open_credentials(win, steps)

    assert [name for name, _, _ in W.cards_of(win)] == ["UAT", "SPARE"]


@pytest.mark.invariant("CR-15")
def test_a_list_that_cannot_be_read_is_said_over_the_editor_naming_its_folder_and_no_window_opens(win, monkeypatch):
    """credentials.yaml damaged: Tools > Credentials… shows one warning "Credentials" over the editor window, saying
    what is wrong with the file, naming it, and the folder it is in; no Credentials window opens."""
    win.home.root.mkdir(parents=True, exist_ok=True)
    path = win.home.root / "credentials.yaml"
    path.write_bytes(b"cards: {UAT: [\n")
    said, opened = [], []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: said.append(a) or QMessageBox.Ok))
    closes = lambda window: (opened.append(window), window.done(0))  # noqa: E731
    W.WATCH.openers.append(closes)

    W.menu_item(win, "Tools", "Credentials…").trigger()
    W.settle()

    assert opened == []
    W.WATCH.openers.remove(closes)
    assert len(said) == 1 and len(said[0]) == 3, said
    parent, title, text = said[0]
    assert parent is win and title == "Credentials"
    message, _, rest = text.partition("\n\n")
    assert str(path) in message, text
    assert rest == f"It is in {win.home.root}: put it right, then try again.", text
