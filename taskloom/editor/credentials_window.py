"""Tools > Credentials…: the cards listed in credentials.yaml and their rows of key and value, the values in the
credential store (see taskloom/credentials.py, whose rules this window follows). Nothing is stored until Save, which
stores every change at once; Cancel stores none. A value never shows unless its row's eye is open."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QByteArray, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from .. import credentials as C
from ..flow import renamed_credentials
from .canvas import STATUS_COLORS

SHORT = 4  # CR-6: a shorter value is not hidden in what a run tells (engine.forms_of)
DOTS = "●" * 12  # every value whose eye is shut shows so, whatever its length (CR-2)
NONE = "(none)"
# Tabler's eye and eye-off (MIT, as the editor's other icons).
EYE = ('<path d="M10 12a2 2 0 1 0 4 0a2 2 0 0 0 -4 0"/>'
       '<path d="M21 12c-2.4 4 -5.4 6 -9 6c-3.6 0 -6.6 -2 -9 -6c2.4 -4 5.4 -6 9 -6c3.6 0 6.6 2 9 6"/>')
EYE_OFF = ('<path d="M10.585 10.587a2 2 0 0 0 2.829 2.828"/>'
           '<path d="M16.681 16.673a8.717 8.717 0 0 1 -4.681 1.327c-3.6 0 -6.6 -2 -9 -6c1.272 -2.12 2.712 -3.678 '
           '4.32 -4.674m2.86 -1.146a9.055 9.055 0 0 1 1.82 -.18c3.6 0 6.6 2 9 6c-.666 1.11 -1.379 2.067 -2.138 2.87"/>'
           '<path d="M3 3l18 18"/>')


def _icon(paths: str, size: int = 18) -> QIcon:
    """The drawing in the theme's text colour."""
    colour = QApplication.palette().text().color().name()
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
           f'stroke="{colour}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>')
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QIcon(pixmap)


def _muted(label: QLabel) -> QLabel:
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


@dataclass(eq=False)
class Row:
    key: str
    pointer: str | None = None  # the row CARD.key it takes its value from
    value: str | None = None  # its own value (None: none stored)
    was: str | None = None  # its key when the window opened (None: added since)
    shown: bool = False  # its eye is open
    rewrite: bool = False  # renamed with "also rewrite the open flows"


@dataclass(eq=False)
class Card:
    name: str
    uses: str | None = None
    rows: list[Row] = field(default_factory=list)
    was: str | None = None
    rewrite: bool = False


class ValueBox(QLineEdit):
    """A row's value: DOTS while its eye is shut, the value while it is open. Typed into with the eye shut, it starts
    empty and hides what is typed; what is typed is the row's value when you leave the box."""

    def __init__(self, line: RowLine):
        super().__init__(objectName="value")
        self.line = line
        self.typing = self.typed = False
        self.textEdited.connect(lambda _: setattr(self, "typed", True))
        self.returnPressed.connect(self.commit)

    def show_value(self):
        row = self.line.row
        self.typing = self.typed = False
        self.setEchoMode(QLineEdit.Normal)
        if row is None:  # (the empty row: a key first)
            self.setText("")
            self.setPlaceholderText("value")
        elif row.pointer is not None:
            self.setReadOnly(True)
            self.setText(f"→ {row.pointer}")
            self.setToolTip(f"Takes the value of {row.pointer}. Right-click the key to change it.")
        else:
            self.setPlaceholderText("no value stored")
            self.setText(row.value or "" if row.shown else "" if row.value is None else DOTS)
            if self.hasFocus() and not row.shown:
                self._start_typing()
        self.setCursorPosition(0)

    def _start_typing(self):
        self.typing = True
        self.setEchoMode(QLineEdit.Password)
        self.setText("")

    def focusInEvent(self, event):
        super().focusInEvent(event)
        row = self.line.row
        if row is not None and row.pointer is None and not row.shown and not self.typing and self.isEnabled():
            self._start_typing()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if event.reason() != Qt.PopupFocusReason:  # (not for its own menu: Paste)
            self.commit()

    def commit(self):
        if self.typed and self.line.row is not None:
            self.line.dialog.value_typed(self.line, self.text())
        if self.line.row is not None:
            self.show_value()


class RowLine(QWidget):
    """One row of the chosen card (or of the card it takes its values from: `inherited`), or the empty row (row None):
    its key, its value, where it comes from, its eye, and its note."""

    def __init__(self, dialog: CredentialsDialog, card: Card, row: Row | None, inherited: bool, where: str):
        super().__init__(objectName="credential-row")
        self.dialog, self.card, self.row, self.stale = dialog, card, row, False
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        self.key = QLineEdit(row.key if row else "", objectName="key")
        self.key.setPlaceholderText("key")
        self.value = ValueBox(self)
        self.where = QLabel(where)
        if inherited:
            self.where = _muted(self.where)
            for edit in (self.key, self.value):
                edit.setEnabled(False)
                edit.setToolTip(f"{card.name}'s row: change it in {card.name}, or add a row {row.key} here to "
                                "replace it.")
        if row is None:
            self.value.setEnabled(False)
        self.eye = None
        if row is not None and row.pointer is None:
            self.eye = QToolButton(objectName="eye", checkable=True, autoRaise=True)
            self.eye.clicked.connect(lambda: dialog.toggle_eye(self))
        self.short = QLabel(f"Too short to hide in the Log: use {SHORT} or more characters")
        self.short.setStyleSheet(f"color: {STATUS_COLORS['running']};")
        self.short.setWordWrap(True)  # (so it does not narrow its row's key)
        grid.addWidget(self.key, 0, 0)
        grid.addWidget(self.value, 0, 1)
        grid.addWidget(self.where, 0, 2)
        if self.eye is not None:
            grid.addWidget(self.eye, 0, 3)
        grid.addWidget(self.short, 1, 1, 1, 3)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        grid.setColumnMinimumWidth(3, 28)
        if not inherited:
            self.key.editingFinished.connect(lambda: dialog.key_typed(self))
            self.key.setContextMenuPolicy(Qt.CustomContextMenu)
            self.key.customContextMenuRequested.connect(lambda at: dialog.row_menu(self, at))
        self.refresh()

    def refresh(self):
        row = self.row
        self.value.show_value()
        self.short.setVisible(row is not None and row.pointer is None and row.value is not None
                              and len(row.value) < SHORT)
        if self.eye is not None:
            self.eye.setChecked(row.shown)
            self.eye.setIcon(_icon(EYE if row.shown else EYE_OFF))
            self.eye.setToolTip("Hide this value" if row.shown else
                                "Show this value (until you press it again or close the window)")


class CredentialsDialog(QDialog):
    """`flows`: (name, flow file data, block types) of each flow open in the editor, for renaming what they use
    (CR-16). Raises CredentialError (or OSError) if the list or the store cannot be read: the caller says it."""

    def __init__(self, home, parent=None, flows=()):
        super().__init__(parent)
        self.root, self.flows = home.root, list(flows)
        self.version = C.version(self.root)  # (a later Save of another window, or process, refuses this one's)
        self.cards: list[Card] = []
        self.opened: dict[str, str] = {}  # CARD.key: its value as stored when the window opened
        for name, listed in C.cards(self.root).items():
            card = Card(name, listed["uses"], was=name)
            for key, pointer in listed["rows"].items():
                value = None if pointer is not None else C.stored(self.root, f"{name}.{key}")
                if value is not None:
                    self.opened[f"{name}.{key}"] = value
                card.rows.append(Row(key, pointer, value, was=key))
            self.cards.append(card)
        self.card: Card | None = None
        self.lines: list[RowLine] = []
        self._filling = self._busy = False
        self.refused = False  # (a refusal or a renaming called off just now: _refused_now)

        self.setWindowTitle("Credentials")
        self.resize(760, 460)
        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addWidget(QLabel("Cards"))
        self.list = QListWidget(objectName="cards")
        left.addWidget(self.list)
        buttons = QHBoxLayout()
        for text, slot in (("Add…", self._add), ("Rename…", self._rename), ("Delete", self._delete)):
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        left.addLayout(buttons)
        layout.addLayout(left, 2)

        right = QVBoxLayout()
        self.help = _muted(QLabel())
        self.help.setWordWrap(True)
        right.addWidget(self.help)
        source = QHBoxLayout()
        source.addWidget(QLabel("Takes its values from:"))
        self.uses = QComboBox(objectName="uses")
        source.addWidget(self.uses)
        source.addStretch()
        right.addLayout(source)
        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)
        self.rows_layout.setSpacing(4)
        area = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        area.setWidget(self.rows_host)
        right.addWidget(area, 1)
        right.addWidget(_muted(QLabel("Right-click a row's key: Point at another row…, Delete row.")))
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        right.addWidget(box)
        layout.addLayout(right, 5)

        self.list.currentRowChanged.connect(self._choose)
        self.uses.currentTextChanged.connect(self._uses_chosen)
        self.list.addItems([card.name for card in self.cards])
        if self.cards:
            self.list.setCurrentRow(0)
        else:
            self._choose(-1)

    # --- what the window holds, as credentials.py reads it --------------------------------------------------------

    def listed(self) -> dict:
        return {c.name: {"uses": c.uses, "rows": {r.key: r.pointer for r in c.rows}} for c in self.cards}

    def _find(self, name: str | None) -> Card | None:
        return next((c for c in self.cards if name and c.name.casefold() == name.casefold()), None)

    def new_reference(self, block: str, reference: str) -> str | None:
        """After Save: what a flow's `reference` (CARD.key as the window opened) names now, if it was renamed with
        "also rewrite the open flows"; else None."""
        card, _, key = reference.partition(".")
        here = next((c for c in self.cards if c.was and c.was.casefold() == card.casefold()), None)
        row = here and next((r for r in here.rows if r.was and r.was.casefold() == key.casefold()), None)
        new = f"{here.name if here and here.rewrite else card}.{row.key if row and row.rewrite else key}"
        return new if new != reference else None

    def _refuse(self, text: str):
        QMessageBox.warning(self, "Not changed", text)
        self._refused_now()

    def _refused_now(self):
        """A refusal, or a renaming called off: a Save asked for at this moment saves nothing and leaves the window
        open, also when it is what made it (a click on Save takes the focus from the box being typed in, before the
        click); a Save after it does."""
        self.refused = True
        QTimer.singleShot(0, self, lambda: setattr(self, "refused", False))

    def _name_problem(self, name: str, what: str, others: list[str]) -> str | None:
        try:
            C.check_name(name, what)
        except C.CredentialError as e:
            return f"{str(e)[:1].upper()}{str(e)[1:]}."
        same = next((o for o in others if o.casefold() == name.casefold()), None)
        return None if same is None else (f"There is a {what} {same} already: upper and lower case do not make another "
                                          "name.")

    def _circle_made(self, before: str | None) -> bool:
        """Whether the change just made made a circle (said, if so)."""
        found = C.circle(self.listed())
        if found and not before:
            self._refuse(f"{found}: a card or row would take its value from itself in the end.")
        return bool(found and not before)

    # --- the cards --------------------------------------------------------------------------------------------------

    def _choose(self, index: int):
        self.card = self.cards[index] if 0 <= index < len(self.cards) else None
        self._fill_uses()
        self._show_rows()

    def _fill_uses(self):
        self._filling = True
        self.uses.clear()
        card = self.card
        self.uses.setEnabled(card is not None)
        if card is not None:
            self.uses.addItems([NONE, *[c.name for c in self.cards if c is not card]])
            used = self._find(card.uses)
            self.uses.setCurrentText(used.name if used else card.uses or NONE)
        self._filling = False

    def _uses_chosen(self, text: str):
        if self._filling or self.card is None:
            return
        before, old = C.circle(self.listed()), self.card.uses
        self.card.uses = None if text == NONE else text
        if self._circle_made(before):
            self.card.uses = old
            self._fill_uses()
            return
        self._show_rows()

    def _add(self):
        name, ok = QInputDialog.getText(self, "Add card", "Name of the new card (e.g. UAT, PROD):")
        if not ok:
            return
        problem = self._name_problem(name, "card", [c.name for c in self.cards])
        if problem:
            self._refuse(problem)
            return
        self.cards.append(Card(name))
        self.list.addItem(name)
        self.list.setCurrentRow(len(self.cards) - 1)

    def _rename(self):
        card = self.card
        if card is None:
            return
        name, ok = QInputDialog.getText(self, "Rename card", f"New name of the card {card.name}:", text=card.name)
        if not ok or name == card.name:
            return
        problem = self._name_problem(name, "card", [c.name for c in self.cards if c is not card])
        if problem:
            self._refuse(problem)
            return
        rewrite = self._ask_open_flows(card, None, name)
        if rewrite is None:
            return
        old, card.name, card.rewrite = card.name, name, rewrite
        for c in self.cards:  # (what takes from it or points at it follows)
            if c.uses and c.uses.casefold() == old.casefold():
                c.uses = name
            for r in c.rows:
                if r.pointer and C.split(r.pointer)[0].casefold() == old.casefold():
                    r.pointer = f"{name}.{C.split(r.pointer)[1]}"
        self.list.item(self.cards.index(card)).setText(name)
        self._fill_uses()
        self._show_rows()

    def _delete(self):
        card = self.card
        if card is None:
            return
        found = C.users(self.listed(), card.name)
        if found:
            self._refuse(f"{card.name} is not deleted, it is used: {'; '.join(found)}.")
            return
        index = self.cards.index(card)
        del self.cards[index]
        self.list.takeItem(index)
        self._choose(self.list.currentRow())  # (the row the list told during the take was one of the list before it)

    # --- the rows ---------------------------------------------------------------------------------------------------

    def _show_rows(self, focus: tuple[Row, str] | None = None):
        """The chosen card's rows, with those of the card it takes its values from that it has none for, and the
        empty row last; `focus`: (row, "key" or "value") to type in."""
        for line in self.lines:
            line.stale = True
            line.hide()
            line.deleteLater()
        self.lines = []
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        card = self.card
        if card is None:
            self.help.setText("Add a card (e.g. UAT or PROD), then its rows: a key and its value.")
            return
        used = self._find(card.uses)
        own = {r.key.casefold(): r for r in card.rows}
        shown = []  # (card, row, inherited, where)
        if used is not None and used is not card:
            for r in used.rows:
                mine = own.pop(r.key.casefold(), None)
                shown.append((card, mine, False, f"<b>own</b>, wins over {used.name}") if mine else
                             (used, r, True, f"from {used.name}"))
        shown += [(card, r, False, "") for r in card.rows if r.key.casefold() in own]
        shown.append((card, None, False, ""))
        self.help.setText(f"{card.name} takes {used.name}'s rows (greyed); a row of its own with the same key wins."
                          if used else "Each value is kept in the operating system's credential store. Use one as "
                          f"secret:{card.name}.password, or {{{{secret:{card.name}.password}}}} inside text.")
        header = QWidget()
        grid = QGridLayout(header)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.addWidget(_muted(QLabel("Key")), 0, 0)
        grid.addWidget(_muted(QLabel("Value")), 0, 1)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        self.rows_layout.addWidget(header)
        self.lines = [RowLine(self, *one) for one in shown]
        width = max(line.where.sizeHint().width() for line in self.lines)
        for line in self.lines:
            line.where.setFixedWidth(width)
            self.rows_layout.addWidget(line)
        spacer = QWidget()
        spacer.setFixedWidth(width)
        grid.addWidget(spacer, 0, 2)
        grid.setColumnMinimumWidth(3, 28)
        self.rows_layout.addStretch(1)
        if focus is not None:
            line = next((line for line in self.lines if line.row is focus[0]), None)
            if line is not None:
                (line.key if focus[1] == "key" else line.value).setFocus()

    def _later(self, focus: tuple[Row, str] | None = None):
        """The rows shown again once the box typed in has let go (it is one of them)."""
        QTimer.singleShot(0, self, lambda: self._show_rows(focus))

    def key_typed(self, line: RowLine):
        if line.stale or self._busy or self.card is None:
            return
        card, row, new = line.card, line.row, line.key.text()
        if row is None and not new or row is not None and new == row.key:
            return
        problem = self._name_problem(new, "key", [r.key for r in card.rows if r is not row])
        if problem:
            line.key.setText(row.key if row else "")
            self._refuse(problem)
            return
        if row is None:  # the empty row: a row now, and an empty row below it
            row = Row(new)
            card.rows.append(row)
            self._later((row, "value"))
            return
        self._busy = True
        try:
            rewrite = self._ask_open_flows(card, row, f"{card.name}.{new}")
        finally:
            self._busy = False
        if rewrite is None:
            line.key.setText(row.key)
            self._refused_now()
            return
        old, row.key, row.rewrite = row.key, new, rewrite
        for c in self.cards:  # (the rows pointing at it follow)
            for r in c.rows:
                if r.pointer:
                    to_card, to_key = C.split(r.pointer)
                    if to_card.casefold() == card.name.casefold() and to_key.casefold() == old.casefold():
                        r.pointer = f"{to_card}.{new}"
        self._later((row, "value"))

    def value_typed(self, line: RowLine, value: str):
        line.row.value = value
        line.refresh()

    def toggle_eye(self, line: RowLine):
        if line.value.typed:  # (what is being typed is kept)
            self.value_typed(line, line.value.text())
        line.row.shown = not line.row.shown
        line.refresh()

    def row_menu(self, line: RowLine, at):
        menu = line.key.createStandardContextMenu()
        first = menu.actions()[0] if menu.actions() else None
        point, delete = QAction("Point at another row…", menu), QAction("Delete row", menu)
        for action in (point, delete):
            action.setEnabled(line.row is not None and not line.stale)
            menu.insertAction(first, action)
        menu.insertSeparator(first)
        chosen = menu.exec(line.key.mapToGlobal(at))
        menu.deleteLater()
        if chosen is point:
            self._point(line)
        elif chosen is delete:
            self._delete_row(line)

    def _point(self, line: RowLine):
        card, row = line.card, line.row
        rows = [f"{c.name}.{r.key}" for c in self.cards if c is not card for r in c.rows]
        if not rows:
            self._refuse("There is no row of another card to point at: add one first.")
            return
        chosen, ok = QInputDialog.getItem(self, "Point at another row", f"{card.name}.{row.key} takes the value of:",
                                          rows, rows.index(row.pointer) if row.pointer in rows else 0, False)
        if not ok:
            return
        before, old = C.circle(self.listed()), row.pointer
        row.pointer = chosen
        if self._circle_made(before):
            row.pointer = old
            return
        row.value, row.shown = None, False  # (it holds no value of its own: Save removes the one it had)
        self._show_rows()

    def _delete_row(self, line: RowLine):
        card, row = line.card, line.row
        found = C.users(self.listed(), card.name, row.key)
        if found:
            self._refuse(f"{card.name}.{row.key} is not deleted, it is used: {'; '.join(found)}.")
            return
        card.rows.remove(row)
        self._show_rows()

    # --- renaming what open flows use (CR-16) -----------------------------------------------------------------------

    def _ask_open_flows(self, card: Card, row: Row | None, new: str) -> bool | None:
        """Renaming `card` (or its `row`) to `new`: if open flows use it as it was when the window opened, asks
        whether to rename only (False) or also rewrite them on Save (True); None: not renamed. False if none does."""
        if card.was is None or row is not None and row.was is None:
            return False
        used = []
        for name, data, registry in self.flows:
            blocks = []

            def seen(block, reference, blocks=blocks):
                to_card, _, to_key = reference.partition(".")
                if to_card.casefold() == card.was.casefold() and block not in blocks \
                        and (row is None or to_key.casefold() == row.was.casefold()):
                    blocks.append(block)

            renamed_credentials(data, registry, seen)
            if blocks:
                used.append(f"{name}: {', '.join(blocks)}")
        if not used:
            return False
        what = f"The card {card.name}" if row is None else f"{card.name}.{row.key}"
        box = QMessageBox(QMessageBox.Warning, "Used by open flows",
                          f"{what} is used by flows open in this window (flow: blocks):\n\n" + "\n".join(used) +
                          f"\n\nRename only: they keep the old name, and Validate names it. Also rewrite: on Save "
                          f"each of them names {new} instead, as an unsaved change Undo takes back. Flows that are "
                          "not open are not changed.", parent=self)
        only = box.addButton("Rename only", QMessageBox.AcceptRole)
        rewrite = box.addButton("Also rewrite the open flows", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        box.deleteLater()
        return True if clicked is rewrite else False if clicked is only else None

    # --- Save --------------------------------------------------------------------------------------------------------

    def _save(self):
        focused = self.focusWidget()
        if isinstance(focused, QLineEdit):  # (what is being typed goes in first)
            focused.clearFocus()
        if self.refused:  # (it was refused, or its renaming called off: nothing saved, the window open)
            return
        if C.version(self.root) != self.version:
            QMessageBox.warning(self, "Not saved", "The credentials were saved elsewhere since this window opened (in "
                                "another Credentials window, or with taskloom secret). Nothing of this window is "
                                "saved, so nothing saved there is lost: Cancel, open Credentials again and make your "
                                "changes there.")
            return
        final = {f"{c.name}.{r.key}": r.value for c in self.cards for r in c.rows
                 if r.pointer is None and r.value is not None}
        values = {name: None for name in self.opened if name not in final}  # (first: a store may ignore case)
        values |= {name: value for name, value in final.items() if self.opened.get(name) != value}
        try:
            C.save(self.root, self.listed(), values)
        except Exception as e:  # (the store or the file: their messages never hold a value)
            QMessageBox.warning(self, "Not saved", f"{e}\n\nNothing is saved: the list and every value are as they "
                                "were.")
            return
        self.accept()
