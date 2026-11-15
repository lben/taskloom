"""Dialogs: run with parameters, overlapping runs, Ask User, connections, settings, turning the scheduler service off
and its missed runs."""

from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path

import yaml
from PySide6.QtCore import QLocale, QProcess, QProcessEnvironment, Qt, QTimer

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ..block import IDENTIFIER
from ..blocks.logic import as_number, as_yes_no
from ..blocks.servers import AUTH_NEEDS, AUTHS, BASIC, BEARER, DIGEST, NO_AUTH
from ..config import SETTINGS_DEFAULTS, keep_runs_for
from ..launch import ARGS_VARIABLE, runner_command
from ..scheduler import ASK_OVER
from ..table import Table
from .canvas import STATUS_COLORS
from .panels import MONO
from .theme import THEMES


def answered(dialog: QDialog) -> int:
    """Show `dialog` until it is answered (its exec() result); the caller reads its answers at once, then it is
    deleted. Left to its window, Python would free it at some moment of the editor's exit, which can crash it."""
    result = dialog.exec()
    _delete_when_done(dialog)
    return result


def _delete_when_done(dialog: QDialog):
    """Delete the dialog (when control is back in the event loop: after its answers are read), once no program of its
    own is running (a connection's test, creating the Python environment): it would be stopped with it. A program
    that ends may start the next one (the list of packages): looked at again then."""
    running = next((p for p in dialog.findChildren(QProcess) if p.state() != QProcess.NotRunning), None)
    if running is None:
        dialog.deleteLater()
    else:
        running.finished.connect(lambda *_: QTimer.singleShot(0, dialog, lambda: _delete_when_done(dialog)))


def open_dialog(parent, cls, home, **kw) -> QDialog | None:
    """Open a dialog of your files (settings, connections); the dialog if you closed it with OK or Save. A mistake
    in one of the files, e.g. made by hand, is named instead: the dialog would write over what the file holds."""
    try:
        dialog = cls(home, parent, **kw)
    except ValueError as e:
        QMessageBox.warning(parent, "Mistake in a settings file",
                            f"{e}\n\nIt is in {home.root}: put it right, then try again.")
        return None
    return dialog if answered(dialog) else None


class RunDialog(QDialog):
    """Shows parameter values computed for today; change any to re-run a past day."""

    def __init__(self, flow_name: str, specs: dict, computed: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Run {flow_name}")
        self.computed = {k: str(v) for k, v in computed.items()}
        layout = QVBoxLayout(self)
        note = QLabel("Values are computed for today. Change any of them to run for another day; "
                      "parameters computed from a changed one follow it.")
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        self.edits: dict[str, QLineEdit] = {}
        for row, name in enumerate(specs):
            edit = QLineEdit(self.computed.get(name, ""))
            reset = QToolButton(text="Reset")
            reset.clicked.connect(lambda _=False, e=edit, n=name: e.setText(self.computed.get(n, "")))
            grid.addWidget(QLabel(name), row, 0)
            grid.addWidget(edit, row, 1)
            grid.addWidget(reset, row, 2)
            self.edits[name] = edit
        layout.addLayout(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("Run", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def overrides(self) -> dict:
        return {n: e.text() for n, e in self.edits.items() if e.text() != self.computed.get(n, "")}


class OverlapDialog(QDialog):
    SKIP, QUEUE, ALONGSIDE = "skip", "queue", "allow"

    def __init__(self, flow_name: str, detail: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Previous run still going")
        self.choice = self.SKIP
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{flow_name}</b> is still running. {detail}"))
        self.remember = QCheckBox("Remember my choice for this flow")
        layout.addWidget(self.remember)
        row = QHBoxLayout()
        row.addStretch()
        for label, choice in (("Skip", self.SKIP), ("Queue after it", self.QUEUE), ("Run alongside", self.ALONGSIDE)):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, c=choice: self._choose(c))
            row.addWidget(button)
        layout.addLayout(row)

    def _choose(self, choice):
        self.choice = choice
        self.accept()


class AskDialog(QDialog):
    """A question from an Ask User block during a manual run."""

    def __init__(self, block: str, prompt: str, kind: str, options: list, default, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{block} asks")
        self.block, self.kind, self.default, self.value = block, kind, default, None
        layout = QVBoxLayout(self)
        label = QLabel(prompt)
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons = QDialogButtonBox()
        if kind == "yes_no":
            yes = default is None or as_yes_no(default)
            for text, value in (("Yes", True), ("No", False)):
                b = buttons.addButton(text, QDialogButtonBox.AcceptRole)
                b.setDefault(value == yes)  # Enter presses the Default's button (Yes without one)
                b.clicked.connect(lambda _=False, v=value: self._answer(v))
        else:
            if kind == "choice":
                self.input = QComboBox()
                self.input.addItems([str(o) for o in options])
                if default is not None:
                    self.input.setCurrentText(str(default))
            else:
                self.input = QLineEdit()
                if kind == "password":
                    self.input.setEchoMode(QLineEdit.Password)
            layout.addWidget(self.input)
            if kind in ("text", "number") and default is not None:  # (Ask User takes an empty answer as the Default)
                self.input.setPlaceholderText(str(default))  # grey, in the empty box: what you type replaces it
                note = QLabel(f"Leave it empty to use the default: {default}", textFormat=Qt.PlainText, wordWrap=True)
                note.setStyleSheet("color: palette(placeholder-text);")
                layout.addWidget(note)
            if kind == "number":
                self.why = QLabel("Type a number, e.g. 12 or 2.5")  # when OK is pressed without one
                self.why.setStyleSheet(f"color: {STATUS_COLORS['failed']};")
                self.why.hide()
                layout.addWidget(self.why)
            ok = buttons.addButton("OK", QDialogButtonBox.AcceptRole)
            ok.setDefault(True)  # Enter answers
            ok.clicked.connect(lambda: self._answer(self.input.currentText() if kind == "choice" else self.input.text()))
        cancel = buttons.addButton("Don't answer", QDialogButtonBox.RejectRole)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _answer(self, value):
        if self.kind == "number" and (value != "" or self.default is None):  # (empty with a Default: the Default)
            try:
                value = as_number(value)  # (an int when whole, else a float)
            except ValueError:
                self.why.show()
                self.adjustSize()  # (room for it: the note above stays whole)
                return  # only a number is taken: the question stays
        self.value = value
        self.accept()


class ConnectionsDialog(QDialog):
    """Edit connections.yaml. Passwords go to the secret store, never into the file."""

    KINDS = {"jdbc": "Database (JDBC)", "ssh": "Server (SSH)", "smtp": "Email server (SMTP)", "api": "Web API (HTTP)"}
    FIELDS = {"jdbc": ("driver", "url", "jars", "username"), "ssh": ("host", "port", "username", "key_file"),
              "smtp": ("host", "port", "security", "username", "sender"),
              "api": ("base_url", "username", "key_name", "ca_file")}
    HELP = {
        "jdbc": "A database you query with Run Query. Ask your database team for the driver .jar file and the "
                "JDBC URL; choosing the jar fills in the driver class. Extra settings (timeouts, …) go in the "
                "table below.",
        "ssh": "A server you reach with SSH: for the Servers blocks, and for files on a server (choose it where a "
               "path is, in Copy, Move or Delete Files). Use your login and password, or a key file instead of a "
               "password.",
        "smtp": "The mail server Send Email and notifications use. Inside a company this is often a relay "
                "on port 25 that needs no login; ask your IT team.",
        "api": "A web API that API Request blocks call: its base URL (their URL may be relative to it, e.g. "
               "/orders/7), its auth, the headers every request sends (a block's own header of the same name wins) "
               "and its certificate settings.",
    }
    LABELS = {"jars": "Driver .jar file(s)", "driver": "Driver class", "url": "URL", "host": "Host", "port": "Port",
              "security": "Security", "username": "User name", "key_file": "Key file", "sender": "Send as",
              "base_url": "Base URL", "key_name": "Key name", "ca_file": "CA certificate file"}
    # An API connection's secret: what it is, by its Auth.
    SECRET_LABELS = {BASIC: "Password", DIGEST: "Password", BEARER: "Token"}

    def __init__(self, home, parent=None, select: str | None = None, new_kind: str | None = None):
        super().__init__(parent)
        self.home = home
        self.setWindowTitle("Connections")
        self.resize(820, 560)
        self.conns = home.connections()
        self.passwords: dict[str, str] = {}  # typed but not saved yet
        self.created: str | None = None
        self.current: str | None = None

        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        self.names = QListWidget()
        add, rename, remove = QPushButton("New…"), QPushButton("Rename…"), QPushButton("Remove")
        left.addWidget(self.names)
        row = QHBoxLayout()
        for b in (add, rename, remove):
            row.addWidget(b)
        left.addLayout(row)
        layout.addLayout(left, 2)

        right = QVBoxLayout()
        self.form = QFormLayout()
        self.kind = QComboBox()
        for key, label in self.KINDS.items():
            self.kind.addItem(label, key)
        self.form.addRow("Kind", self.kind)
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color: palette(placeholder-text);")
        self.form.addRow(self.help)
        self.edits = {key: QLineEdit() for key in self.LABELS}
        placeholders = {"driver": "e.g. com.example.jdbc.Driver", "url": "jdbc:…",
                        "jars": "separate several files with ;", "port": "SSH: 22 · email: 25, 587 or 465",
                        "security": "none, starttls or ssl", "key_file": "optional, e.g. ~/.ssh/id_ed25519",
                        "sender": "the From address, e.g. reports@example.com", "host": "e.g. server01.example.com",
                        "base_url": "e.g. https://api.example.com/v1", "key_name": "the header or query parameter, "
                        "e.g. X-Api-Key", "ca_file": "optional: a PEM file of an authority to trust"}
        for key, text in placeholders.items():
            self.edits[key].setPlaceholderText(text)
        self.jar_row = QWidget()
        jar_layout = QHBoxLayout(self.jar_row)
        jar_layout.setContentsMargins(0, 0, 0, 0)
        browse = QToolButton(text="…")
        jar_layout.addWidget(self.edits["jars"])
        jar_layout.addWidget(browse)
        self.jar_note = QLabel()  # the typed paths that do not exist
        self.jar_note.setStyleSheet(f"color: {STATUS_COLORS['failed']};")
        self.driver_note = QLabel("No driver class found in the jar (an older driver): type it in Driver class; "
                                  "the driver's documentation names it.")
        self.driver_note.setStyleSheet("color: palette(placeholder-text);")
        self.borrowed_note = QLabel()  # the driver class is only in other connections' jars
        self.borrowed_note.setStyleSheet(f"color: {STATUS_COLORS['running']};")
        for note in (self.jar_note, self.driver_note, self.borrowed_note):
            note.setWordWrap(True)
        self.auth = QComboBox()  # an API connection's
        self.auth.addItems(list(AUTHS))
        self.dont_verify = QCheckBox("Don't verify certificates")
        for key, edit in self.edits.items():
            self.form.addRow(self.LABELS[key], self.jar_row if key == "jars" else edit)
            if key == "jars":
                self.form.addRow("", self.jar_note)
            if key == "driver":
                self.form.addRow("", self.driver_note)
                self.form.addRow("", self.borrowed_note)
            if key == "base_url":
                self.form.addRow("Auth", self.auth)
            if key == "ca_file":
                self.form.addRow("", self.dont_verify)
        self.password = QLineEdit(echoMode=QLineEdit.Password)
        self.password_status = QLabel()
        self.form.addRow("Password", self.password)
        self.form.addRow("", self.password_status)
        right.addLayout(self.form)
        self.props_label = QLabel("Extra driver settings (name and value)")
        right.addWidget(self.props_label)
        self.props = QTableWidget(0, 2)
        self.props.setHorizontalHeaderLabels(["Setting", "Value"])
        self.props.horizontalHeader().setStretchLastSection(True)
        self.props.verticalHeader().hide()
        right.addWidget(self.props)
        prop_row = QHBoxLayout()
        self.add_prop, self.remove_prop = QPushButton("Add setting"), QPushButton("Remove setting")
        prop_row.addWidget(self.add_prop)
        prop_row.addWidget(self.remove_prop)
        prop_row.addStretch()
        right.addLayout(prop_row)
        test_row = QHBoxLayout()
        self.test_button = QPushButton("Save and test connection")
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        right.addLayout(test_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        right.addWidget(buttons)
        layout.addLayout(right, 5)
        self.editor_widgets = [self.kind, *self.edits.values(), self.auth, self.dont_verify, self.password, self.props,
                               self.add_prop, self.remove_prop, self.test_button, browse]

        self.names.addItems(list(self.conns))
        self.names.currentTextChanged.connect(self._select)
        self.kind.currentIndexChanged.connect(lambda _: self._show_kind())
        self.auth.currentIndexChanged.connect(lambda _: self._show_kind())
        add.clicked.connect(lambda: self._add(None))
        rename.clicked.connect(self._rename)
        remove.clicked.connect(self._remove)
        browse.clicked.connect(self._browse)
        self.edits["jars"].editingFinished.connect(self._jars_typed)
        self.edits["driver"].editingFinished.connect(self._show_borrowed)
        self.add_prop.clicked.connect(lambda: self._add_prop("", ""))
        self.remove_prop.clicked.connect(lambda: self.props.removeRow(self.props.currentRow()))
        self.test_button.clicked.connect(self._test)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        if new_kind:
            self._add(new_kind)
        elif select in self.conns:
            self.names.setCurrentRow(list(self.conns).index(select))
        elif self.conns:
            self.names.setCurrentRow(0)
        self._show_kind()

    # --- showing a connection ---------------------------------------------------------

    def _kind(self) -> str:
        return self.kind.currentData()

    def _show_kind(self):
        kind = self._kind()
        enabled = self.current is not None
        for w in self.editor_widgets:
            w.setEnabled(enabled)
        text = self.HELP.get(kind, f"A connection of a kind for blocks of your own ({kind}): its settings are kept "
                                   "as they are in connections.yaml; change them there.")
        self.help.setText(text if enabled else "Click New… to add a connection.")
        auth = self.auth.currentText() if kind == "api" else None
        for key, edit in self.edits.items():  # (an API connection's user name and key name: for its Auth)
            self.form.setRowVisible(self.jar_row if key == "jars" else edit, enabled and key in self.FIELDS.get(kind, ())
                                    and (auth is None or auth in AUTH_NEEDS.get(key, AUTHS)))
        self.form.setRowVisible(self.auth, enabled and kind == "api")
        self.form.setRowVisible(self.dont_verify, enabled and kind == "api")
        self.edits["port"].setPlaceholderText({"ssh": "22", "smtp": "25 (or 587 with starttls, 465 with ssl)"}.get(kind, ""))
        self.form.setRowVisible(self.jar_note, False)  # until you type jars that do not exist
        self.form.setRowVisible(self.driver_note, False)  # until you give jars that name no driver class
        for w in (self.props, self.props_label, self.add_prop, self.remove_prop):
            w.setVisible(enabled and kind in ("jdbc", "api"))
        api = kind == "api"  # (its headers in the table of a database's driver settings)
        self.props_label.setText("Headers every request sends (name and value)" if api else
                                 "Extra driver settings (name and value)")
        self.props.setHorizontalHeaderLabels(["Header" if api else "Setting", "Value"])
        self.add_prop.setText("Add header" if api else "Add setting")
        self.remove_prop.setText("Remove header" if api else "Remove setting")
        self.form.labelForField(self.password).setText(self.SECRET_LABELS.get(auth, "Key" if auth else "Password"))
        self.form.setRowVisible(self.password, enabled and auth != NO_AUTH)
        self.form.setRowVisible(self.password_status, enabled and auth != NO_AUTH)
        self._show_password_status()
        self._show_borrowed()

    def _jars(self) -> list[str]:
        return [j.strip() for j in self.edits["jars"].text().split(";") if j.strip()]

    def _show_borrowed(self):
        """Warn when the driver class is not in this connection's jars but in another's (see borrowed_driver)."""
        from ..jdbc import borrowed_driver

        text = ""
        if self.current is not None and self._kind() == "jdbc":
            conn = {"kind": "jdbc", "driver": self.edits["driver"].text().strip(), "jars": self._jars()}
            text = borrowed_driver(self.current, {**self.conns, self.current: conn})
        self.borrowed_note.setText(text)
        self.form.setRowVisible(self.borrowed_note, bool(text))

    def _password_ref(self, conn: dict):
        return ((conn.get("properties") or {}).get("password") if conn.get("kind") == "jdbc" else
                conn.get("secret") if conn.get("kind") == "api" else conn.get("password"))

    def _show_password_status(self):
        if self.current is None:
            return
        if self.current in self.passwords:
            text = "A new password will be saved when you click Save."
        else:
            ref = self._password_ref(self.conns.get(self.current, {}))
            saved = False
            if isinstance(ref, str) and ref.startswith("secret:"):
                try:
                    self.home.get_secret(ref[len("secret:"):])
                    saved = True
                except (KeyError, PermissionError):
                    pass
            text = ("A password is saved. Type here only to change it." if saved else
                    "No password saved. Type it here; it is stored securely, never in the connections file.")
        self.password_status.setText(f"<span style='color:gray'>{text}</span>")

    def _add_prop(self, key, value):
        row = self.props.rowCount()
        self.props.insertRow(row)
        self.props.setItem(row, 0, QTableWidgetItem(str(key)))
        self.props.setItem(row, 1, QTableWidgetItem(str(value)))

    def _store_current(self):
        """Copy the form into self.conns (nothing is written to disk until Save)."""
        if self.current is None or self.current not in self.conns:
            return
        kind, old = self._kind(), self.conns[self.current]
        conn = {"kind": kind} if kind in self.FIELDS else {**old, "kind": kind}  # a kind of your blocks: as written
        for key in self.FIELDS.get(kind, ()):
            text = self.edits[key].text().strip()
            if key == "jars":
                conn["jars"] = self._jars()
            elif key == "username" and kind == "jdbc":
                continue  # a JDBC user name is a driver setting
            elif text:
                conn[key] = int(text) if key == "port" and text.isdigit() else text
        if kind == "jdbc":
            props = {}
            if self.edits["username"].text().strip():
                props["user"] = self.edits["username"].text().strip()
            for r in range(self.props.rowCount()):
                key = self.props.item(r, 0) and self.props.item(r, 0).text().strip()
                if key and key not in ("user", "password"):
                    props[key] = self.props.item(r, 1).text() if self.props.item(r, 1) else ""
            if self._password_ref(old):
                props["password"] = self._password_ref(old)
            conn["properties"] = props
        elif kind == "api":  # its user name and key name only for its Auth (_show_kind); no secret without one
            for key in [k for k in ("username", "key_name") if self.auth.currentText() not in AUTH_NEEDS[k]]:
                conn.pop(key, None)
            if self.auth.currentText() != NO_AUTH:
                conn["auth"] = self.auth.currentText()
                if self._password_ref(old):
                    conn["secret"] = self._password_ref(old)
            elif self.password.text():
                self.password.clear()
            if self.dont_verify.isChecked():
                conn["dont_verify"] = True
            rows = [{"key": self.props.item(r, 0).text().strip(),
                     "value": self.props.item(r, 1).text() if self.props.item(r, 1) else ""}
                    for r in range(self.props.rowCount()) if self.props.item(r, 0) and self.props.item(r, 0).text().strip()]
            rows += [r for r in old.get("headers") or [] if isinstance(r, dict) and r.get("on") is False]  # (kept)
            if rows:
                conn["headers"] = rows
        elif self._password_ref(old) and old.get("kind") != "jdbc":
            conn["password"] = self._password_ref(old)
        if self.password.text():
            self.passwords[self.current] = self.password.text()
            self.password.clear()
        self.conns[self.current] = conn

    def _select(self, name):
        self._store_current()
        self.current = name or None
        conn = self.conns.get(name, {})
        kind = conn.get("kind", "jdbc")
        self.kind.blockSignals(True)
        if self.kind.findData(kind) < 0:  # a kind for blocks of your own (fields.Connection("ftp"))
            self.kind.addItem(str(kind), kind)
        self.kind.setCurrentIndex(self.kind.findData(kind))
        self.kind.blockSignals(False)
        props = dict(conn.get("properties") or {})
        if kind == "api":  # its headers in the same table (those not ticked, on: false, are kept as they are)
            props = {r.get("key"): r.get("value") for r in conn.get("headers") or []
                     if isinstance(r, dict) and r.get("on") is not False}
        self.auth.blockSignals(True)
        self.auth.setCurrentText(conn.get("auth") or NO_AUTH)
        self.auth.blockSignals(False)
        self.dont_verify.setChecked(conn.get("dont_verify") is True)
        jars = conn.get("jars") or []
        for key, edit in self.edits.items():
            if key == "jars":
                edit.setText("; ".join([jars] if isinstance(jars, str) else jars))
            elif key == "username" and conn.get("kind") == "jdbc":
                edit.setText(str(props.get("user", "")))
            else:
                edit.setText(str(conn.get(key, "")))
        self.password.clear()
        self.props.setRowCount(0)
        for key, value in props.items():
            if key not in ("user", "password"):
                self._add_prop(key, value)
        self.test_result.clear()
        self._show_kind()

    # --- buttons ----------------------------------------------------------------------

    def _ask_name(self, title, text="") -> str | None:
        name, ok = QInputDialog.getText(self, title, "Name (letters, digits and _, e.g. warehouse or appserver):", text=text)
        name = name.strip()
        if not ok or not name:
            return None
        if not IDENTIFIER.match(name):
            QMessageBox.warning(self, title, f"'{name}' can only contain letters, digits and _.")
            return None
        if name in self.conns:
            QMessageBox.warning(self, title, f"There is already a connection named '{name}'.")
            return None
        return name

    def _add(self, kind):
        name = self._ask_name("New connection")
        if name is None:
            return
        self._store_current()
        self.conns[name] = {"kind": kind or "jdbc"}
        self.created = name
        self.names.addItem(name)
        self.names.setCurrentRow(self.names.count() - 1)

    def _rename(self):
        if self.current is None:
            return
        old = self.current
        new = self._ask_name("Rename connection", old)
        if new is None:
            return
        self._store_current()
        self.conns = {(new if k == old else k): v for k, v in self.conns.items()}
        if old in self.passwords:
            self.passwords[new] = self.passwords.pop(old)
        self.current = new
        self.names.currentItem().setText(new)
        QMessageBox.information(self, "Renamed", f"Flows that use '{old}' need to be changed to '{new}'.")

    def _remove(self):
        item = self.names.currentItem()
        if item is not None:
            self.conns.pop(item.text(), None)
            self.passwords.pop(item.text(), None)
            self.current = None
            self.names.takeItem(self.names.row(item))
            self._show_kind()

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "JDBC driver jars", "", "Jar files (*.jar)")
        if paths:
            self.edits["jars"].setText("; ".join(paths))
            self._use_jars()

    def _jars_typed(self):
        """Typed or pasted jars are used once you leave the field or press Enter (not on each key)."""
        if self.edits["jars"].isModified():  # (since … or showing the connection set it)
            self.edits["jars"].setModified(False)
            self._use_jars()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.focusWidget() is self.edits["jars"]:
            return  # Enter there only fills in the Driver class (_jars_typed); it does not Save
        super().keyPressEvent(event)

    def _use_jars(self):
        """Say which of the jars do not exist; fill in the Driver class from the others."""
        jars = self._jars()
        missing = [j for j in jars if not Path(j).expanduser().is_file()]
        self.jar_note.setText(f"Not found (check the path): {'; '.join(missing)}")
        self.form.setRowVisible(self.jar_note, bool(missing))
        self._fill_driver([j for j in jars if j not in missing])
        self._show_borrowed()

    def _fill_driver(self, jars):
        """Driver class from the jars (JDBC 4 drivers name it); one you typed is replaced only if you say so."""
        from ..jdbc import driver_classes

        found, typed = driver_classes(jars), self.edits["driver"].text().strip()
        self.form.setRowVisible(self.driver_note, bool(jars) and not found)
        if not found or typed in found:
            return
        if typed and QMessageBox.question(
                self, "Driver class", f"The jar names the driver class {' or '.join(found)}, not {typed}, the one "
                                      "you typed.\n\nUse the one the jar names?") != QMessageBox.Yes:
            return
        chosen, ok = found[0], True
        if len(found) > 1:
            chosen, ok = QInputDialog.getItem(self, "Driver class", "The jar names several driver classes; choose "
                                                                    "the one to use:", found, 0, False)
        if ok:
            self.edits["driver"].setText(chosen)

    def _write(self):
        """Save connections.yaml and any new passwords (to the secret store)."""
        self._store_current()
        for name, value in self.passwords.items():
            if name not in self.conns:
                continue
            conn = self.conns[name]
            # (an API connection's: named for what it is, e.g. shop_token)
            secret = f"{name}_{self.SECRET_LABELS.get(conn.get('auth'), 'key').lower()}" if conn["kind"] == "api" \
                else f"{name}_password"
            self.home.set_secret(secret, value)
            if conn["kind"] == "jdbc":
                conn.setdefault("properties", {})["password"] = f"secret:{secret}"
            else:
                conn["secret" if conn["kind"] == "api" else "password"] = f"secret:{secret}"
        self.passwords.clear()
        self.home.root.mkdir(parents=True, exist_ok=True)
        (self.home.root / "connections.yaml").write_text(yaml.safe_dump(self.conns, sort_keys=False), encoding="utf-8")
        self._show_password_status()

    def _test(self):
        if self.current is None:
            return
        self._write()
        self.test_result.setText("Testing…")
        self.test_button.setEnabled(False)
        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("TASKLOOM_HOME", str(self.home.root))
        # Started as the editor starts runs: its output is then in UTF-8, as read below.
        env.insert(ARGS_VARIABLE, json.dumps(["connection", "test", self.current]))
        proc.setProcessEnvironment(env)

        def done(code, _status):
            out = bytes(proc.readAllStandardOutput()).decode(errors="replace").strip()
            err = bytes(proc.readAllStandardError()).decode(errors="replace").strip()
            # The reason is on the "error: " line; a driver's Java stack trace can follow it.
            err = next((line.removeprefix("error: ")[:400] for line in err.splitlines() if line.startswith("error: ")),
                       err[-400:])
            ok = code == 0
            said = "✓ " + html.escape(out).replace("\n", "<br>") if ok else "✕ " + html.escape(err)  # (a warning: its line)
            self.test_result.setText(f"<span style='color:{'#1f8a4c' if ok else '#c0392b'}'>{said}</span>")
            self.test_button.setEnabled(True)

        proc.finished.connect(done)
        program, *args = runner_command()
        proc.start(program, args)

    def _save(self):
        self._write()
        self.accept()


def _folder_size(folder) -> int:
    """The bytes the files in `folder` hold, added up as a file manager does. (scandir: on Windows, without
    opening each file, so years of runs add up quickly.)"""
    total = 0
    try:
        entries = list(os.scandir(folder))
    except OSError:  # none yet, or deleted meanwhile, e.g. as a run ends
        return 0
    for entry in entries:
        try:
            total += _folder_size(entry.path) if entry.is_dir(follow_symlinks=False) else entry.stat().st_size
        except OSError:
            pass
    return total


class SettingsDialog(QDialog):
    def __init__(self, home, parent=None, theme: str = "System"):
        super().__init__(parent)
        self.home = home
        self.setWindowTitle("Settings")
        self.resize(640, 420)
        self.settings = home.settings()
        number, unit = keep_runs_for(self.settings)
        try:
            max_runs = int(self.settings["max_concurrent_runs"] or 0)
        except (TypeError, ValueError):
            raise ValueError("settings.yaml: max_concurrent_runs must be a whole number, "
                             f"got {self.settings['max_concurrent_runs']!r}") from None
        form = QFormLayout(self)
        self.theme = QComboBox(toolTip="The editor's colours. System: light or dark as your system is. Kept in "
                                       "editor.ini, the editor's own preferences")
        self.theme.addItems(THEMES)
        self.theme.setCurrentText(theme)
        form.addRow("Theme", self.theme)
        self.calendar = QLineEdit(self.settings["calendar"] or "", placeholderText="weekends only, e.g. US or a calendars/<name>.yaml")
        self.keep_runs_for = QSpinBox(minimum=1, maximum=100000, value=number)
        self.keep_runs_unit = QComboBox()
        self.keep_runs_unit.addItems(["days", "months", "years"])
        self.keep_runs_unit.setCurrentText(unit)
        size = QLocale.c().formattedDataSize(_folder_size(home.runs_dir), 1, QLocale.DataSizeTraditionalFormat)
        keep_row = QWidget()
        keep = QHBoxLayout(keep_row)
        keep.setContentsMargins(0, 0, 0, 0)
        for widget in (self.keep_runs_for, self.keep_runs_unit, QLabel(f"the runs take {size} now")):
            keep.addWidget(widget)
        keep.addStretch()
        self.max_runs = QSpinBox(minimum=0, maximum=1000, value=max_runs, specialValueText="unlimited")
        self.java_home = QLineEdit(self.settings["java_home"] or "", placeholderText="uses JAVA_HOME if empty")
        browse = QToolButton(text="…")
        browse.clicked.connect(self._browse)
        java_row = QWidget()
        row = QHBoxLayout(java_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.java_home)
        row.addWidget(browse)
        form.addRow("Default calendar", self.calendar)
        form.addRow("Keep runs for", keep_row)
        form.addRow("Scheduled runs at once", self.max_runs)
        form.addRow("Java folder", java_row)
        self.at_login = QCheckBox("Start the scheduler service when I log in")
        if sys.platform == "win32":
            from ..scheduler import startup_file
            self.at_login.setChecked(startup_file().exists())
        else:
            self.at_login.setEnabled(False)
            self.at_login.setToolTip("Windows only; on servers the keeper starts the scheduler")
        form.addRow("Scheduler", self.at_login)
        self.notify_email = QLineEdit(", ".join(self.settings["notify_email"]) if isinstance(self.settings["notify_email"], list)
                                      else self.settings["notify_email"] or "", placeholderText="you@example.com")
        self.smtp = QComboBox(editable=True)
        try:
            self.smtp.addItems([""] + [n for n, c in home.connections().items() if c.get("kind") == "smtp"])
        except ValueError:
            pass
        self.smtp.setCurrentText(self.settings["smtp_connection"] or "")
        events = QWidget()
        events_row = QHBoxLayout(events)
        events_row.setContentsMargins(0, 0, 0, 0)
        self.events = {}
        for event, label in (("failure", "a run fails"), ("success", "a run succeeds"), ("skipped", "runs are skipped")):
            self.events[event] = QCheckBox(label)
            self.events[event].setChecked(event in (self.settings["notify_on"] or []))
            events_row.addWidget(self.events[event])
        form.addRow("Email notifications to", self.notify_email)
        form.addRow("Send them with", self.smtp)
        form.addRow("Email me when", events)
        form.addRow(QLabel("Notifications are for scheduled runs; a flow can override them with notify: in its file."))
        form.addRow(QLabel("Servers whose scheduler this computer keeps running (checked every 5 minutes):"))
        self.keeper = QTableWidget(0, 3)
        self.keeper.setHorizontalHeaderLabels(["SSH connection", "Taskloom command on the server", "Taskloom folder there"])
        self.keeper.horizontalHeader().setStretchLastSection(True)
        self.keeper.verticalHeader().hide()
        for entry in self.settings["keeper"] or []:
            self._add_keeper(entry.get("connection", ""), entry.get("command", ""), entry.get("home", "~/.taskloom"))
        form.addRow(self.keeper)
        keeper_buttons = QHBoxLayout()
        add, remove = QPushButton("Add server"), QPushButton("Remove server")
        add.clicked.connect(lambda: self._add_keeper("", "~/taskloom/taskloom", "~/.taskloom"))
        remove.clicked.connect(lambda: self.keeper.removeRow(self.keeper.currentRow()))
        keeper_buttons.addWidget(add)
        keeper_buttons.addWidget(remove)
        keeper_buttons.addStretch()
        form.addRow(keeper_buttons)
        form.addRow(QLabel(f"Settings file: {home.root / 'settings.yaml'}"))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _add_keeper(self, connection, command, folder):
        r = self.keeper.rowCount()
        self.keeper.insertRow(r)
        for col, text in enumerate((connection, command, folder)):
            self.keeper.setItem(r, col, QTableWidgetItem(text))

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Java folder (contains bin/java)", self.java_home.text())
        if path:
            self.java_home.setText(path)

    def _save(self):
        keeper = []
        for r in range(self.keeper.rowCount()):
            values = [(self.keeper.item(r, c).text().strip() if self.keeper.item(r, c) else "") for c in range(3)]
            if values[0] and values[1]:
                keeper.append({"connection": values[0], "command": values[1], "home": values[2] or "~/.taskloom"})
        number, unit = self.keep_runs_for.value(), self.keep_runs_unit.currentText()
        data = {**self.settings, "calendar": self.calendar.text().strip() or None,
                "keep_runs_for": f"{number} {unit.removesuffix('s') if number == 1 else unit}",
                "max_concurrent_runs": self.max_runs.value() or None,
                "java_home": self.java_home.text().strip() or None, "keeper": keeper,
                "notify_email": self.notify_email.text().strip() or None,
                "smtp_connection": self.smtp.currentText().strip() or None,
                "notify_on": [e for e, box in self.events.items() if box.isChecked()]}
        data = {k: v for k, v in data.items() if v != SETTINGS_DEFAULTS[k]}
        self.home.root.mkdir(parents=True, exist_ok=True)
        (self.home.root / "settings.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        if self.at_login.isEnabled():
            from ..scheduler import set_start_at_login
            set_start_at_login(self.home, self.at_login.isChecked())
        self.accept()


def offer_manual_start(parent, flow_name: str) -> bool:
    """A flow without a start block: add a Manual Start (connected to its first blocks) to run it?"""
    return QMessageBox.question(
        parent, "No start block",
        f"{flow_name} has no start block. A flow starts from a Manual Start (when you run it) or a Schedule (at "
        "the times you set).\n\nAdd a Manual Start connected to its first blocks, and run it?",
        QMessageBox.Yes | QMessageBox.Cancel) == QMessageBox.Yes


def confirm_run_now(parent, flow_name: str, what: str, starts=(), chosen: str | None = None) -> tuple[bool | str, bool]:
    """Ask before Run now: (what to run, and do not ask again). What to run: False (nothing: cancelled), True (run
    it), or, given `starts` (its Manual Starts) to choose from, the one chosen (True: All of them). `chosen`, the
    choice made last time, is offered first; else All of them."""
    box = QMessageBox(QMessageBox.Question, "Run now?", f"<b>Run {html.escape(flow_name)} now?</b><br><br>"
                      f"{html.escape(what)}", QMessageBox.Cancel if starts else QMessageBox.Yes | QMessageBox.Cancel,
                      parent)
    if starts:  # one button for each, and All of them
        choices = {box.addButton(start, QMessageBox.AcceptRole): start for start in starts}
        everything = box.addButton("All of them", QMessageBox.AcceptRole)
        box.setDefaultButton(next((b for b, start in choices.items() if start == chosen), everything))
        answered(box)
        clicked = box.clickedButton()
        return (True if clicked is everything else choices.get(clicked, False)), False
    box.button(QMessageBox.Yes).setText("Run")
    never = QCheckBox("Don't ask me again")
    box.setCheckBox(never)
    go = answered(box) == QMessageBox.Yes
    return go, go and never.isChecked()


TURN_OFF = "Turn off the scheduler service?"


def ask_turn_off(going: list[tuple[str, str]], parent=None) -> dict[str, str] | None:
    """Turn off the scheduler service? Asked by the editor's button and the tray icon's Turn off alike. `going`: the
    flows with runs going, (flow file, name) as runs_going gives them. The answer, as Scheduler.turn_off takes it: for
    each of them "finish" (Let it finish) or "stop" (Stop now); {} once you confirm, with none going. None: you keep it
    on."""
    if not going:
        answer = QMessageBox.question(parent, TURN_OFF, "No scheduled flow runs until you turn it on again (it does not "
                                      "start by itself meanwhile).")
        return {} if answer == QMessageBox.Yes else None
    dialog = TurnOffDialog(going, parent)
    return dialog.choices() if answered(dialog) else None


class TurnOffDialog(QDialog):
    """A row for each flow with runs going: Let it finish (the default) or Stop now; Proceed or Cancel."""

    def __init__(self, going: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(TURN_OFF)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "These flows have runs going. Choose for each one:<br>"
            "<b>Let it finish</b>: its runs going finish, and its runs waiting their turn still run.<br>"
            "<b>Stop now</b>: its runs going stop (they are recorded as cancelled), and its waiting runs are dropped."
            "<br><br>Once you click Proceed, nothing new starts, and the service turns off when the last run you let "
            "finish is done. Other flows keep their waiting runs: they start when you turn the service on again.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.stop: dict[str, QRadioButton] = {}
        for path, name in going:
            row = QGroupBox(name.replace("&", "&&"), toolTip=path)  # (its radio buttons go together)
            line = QHBoxLayout(row)
            line.addWidget(QRadioButton("Let it finish", checked=True))
            line.addWidget(stop := QRadioButton("Stop now"))
            line.addStretch()
            layout.addWidget(row)
            self.stop[path] = stop
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("Proceed", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choices(self) -> dict[str, str]:
        return {path: "stop" if stop.isChecked() else "finish" for path, stop in self.stop.items()}


class CatchUpDialog(QDialog):
    """More than ASK_OVER times of a flow's Catch up schedule were missed: Run all of them, or Run only the latest N.
    Shown without holding anything up (the scheduler service goes on while it is open); Proceed calls answer(None)
    (all) or answer(N). Only Proceed closes it: until you answer, those runs wait."""

    def __init__(self, flow_name: str, count: int, answer, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Catch up missed runs?")
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"{flow_name} missed {count} of its scheduled times while the scheduler service was off or the computer "
            "was asleep. Its schedule's Misfire is catch_up: it runs each missed time, oldest first, one at a time. "
            "They wait for your answer, and so do its times that come meanwhile (those are not missed: they run "
            "after them); other flows run as usual.", textFormat=Qt.PlainText, wordWrap=True))
        # (answer kept here and called by accept: in a slot's lambda, it would keep the service and this window
        # alive through Qt, after the service has ended)
        self.answer = answer
        everything = QRadioButton("Run all of them", checked=True)
        self.latest = QRadioButton("Run only the latest")
        self.number = QSpinBox(enabled=False)
        self.number.setRange(1, count)
        self.number.setValue(ASK_OVER)
        self.latest.toggled.connect(self.number.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self.latest)
        row.addWidget(self.number)
        row.addStretch()
        layout.addWidget(everything)
        layout.addLayout(row)
        layout.addWidget(QLabel("The older ones are not run: they count as missed, and you are told how many.",
                                wordWrap=True))
        proceed = QPushButton("Proceed", default=True)
        proceed.clicked.connect(self.accept)
        layout.addWidget(proceed, alignment=Qt.AlignRight)

    def accept(self):  # Proceed
        self.answer(self.number.value() if self.latest.isChecked() else None)
        super().accept()

    def reject(self):  # (Escape, closing the window): only Proceed answers
        pass


class CheckFlowsDialog(QDialog):
    """What this version can fix in your flows, and what needs you; Back up and fix does the first."""

    def __init__(self, reports: list, parent=None, intro: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Check flows")
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        fixable = [r for r in reports if r.fixes]
        text = QLabel((intro + " " if intro else "")
                      + (f"{len(fixable)} flow(s) can be fixed. <b>Back up and fix</b> copies each into the "
                         "backups folder of your Taskloom folder first." if fixable
                         else "Nothing it can fix by itself: the lines marked 'needs you' are for you to change."
                         if any(r.problems for r in reports) else "Nothing to fix."))
        text.setWordWrap(True)
        layout.addWidget(text)
        self.details = QPlainTextEdit(readOnly=True)
        lines = []
        for r in reports:
            lines.append(f"{r.path}: " + ("OK" if r.ok else ""))
            lines += ([f"    can fix: {t}" for t in r.fixes] + [f"    needs you: {t}" for t in r.problems]
                      + [f"    warning: {t}" for t in r.warnings])
        self.details.setPlainText("\n".join(lines) or "No flows to check: open some, or schedule them.")
        layout.addWidget(self.details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        if fixable:
            buttons.addButton("Back up and fix", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def input_text(value) -> str | None:
    """A value as Python you can edit, or None when it is too big or cannot be written that way."""
    if isinstance(value, Table):
        return f"table({str(value.path)!r})"
    text = repr(value)
    return None if len(text) > 5000 or "\n" in text or text.startswith("<") else text


class ChooseInputsDialog(QDialog):
    """Values for a block's inputs, to run just that block with them (e.g. to test it)."""

    def __init__(self, block_id: str, names: list[str], values: dict, missing: dict, run, parent=None,
                 typed: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Run {block_id} with inputs you choose")
        self.values, self.typed = values, typed or {}
        layout = QVBoxLayout(self)
        intro = QLabel("Write each value as Python: <code>42</code>, <code>'text'</code>, <code>[1, 2]</code>, "
                       "<code>{'a': 1}</code>, <code>None</code>; a table: <code>table('data.csv')</code> or "
                       "<code>pl.DataFrame({'a': [1, 2]})</code>. Leave a field empty for no value."
                       + (f" Filled in: what reached the block in {run}." if values else "")
                       + (" What you typed last time is shown as you typed it and worked out again when you "
                          "press Run (so <code>table('data.csv')</code> reads the file again)." if self.typed else ""))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.fields: dict[str, QLineEdit] = {}
        for name in names:
            field = QLineEdit()
            if name in self.typed:
                field.setText(self.typed[name])
            elif name in values:
                text = input_text(values[name])
                if text is None:
                    field.setPlaceholderText(f"empty: the {type(values[name]).__name__} it got in {run}")
                else:
                    field.setText(text)
            elif name in missing:
                field.setToolTip(missing[name])
                field.setPlaceholderText("empty: no value")
            self.fields[name] = field
            form.addRow(name, field)
        if not names:
            form.addRow(QLabel("This block has no inputs: it just runs."))
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Run")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        # As tall as its contents need at this width: the least height Qt gives it is for its introduction on one line.
        self.resize(560, self.heightForWidth(560))

    def given(self) -> dict:
        """{input: Python text} for each input you set, or typed last time (worked out again); the other
        unchanged ones keep what they got in that run."""
        given = {}
        for name, field in self.fields.items():
            text = field.text()
            if name not in self.values or name in self.typed:
                given[name] = text  # empty: no value
            elif (shown := input_text(self.values[name])) is None:
                if text.strip():
                    given[name] = text
            elif text != shown:
                given[name] = text
        return given


class ReusableBlockDialog(QDialog):
    """Create Reusable block: the new block's name (its title in the blocks tree), what it does and what its inputs
    expect. OK takes a name only when `problem(name)` has nothing against it; else the window says why."""

    def __init__(self, problem, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Reusable block")
        self.resize(520, 0)
        self.problem = problem
        layout = QVBoxLayout(self)
        intro = QLabel("Saves this block as a block of your own, under <b>User Created</b> in the blocks tree, to use "
                       "in any flow: it runs this block's code on what is connected to its inputs.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.name, self.what, self.expects = QLineEdit(), QPlainTextEdit(), QPlainTextEdit()
        form.addRow("Name", self.name)
        form.addRow("What it does", self.what)
        form.addRow("What its inputs expect", self.expects)
        layout.addLayout(form)
        self.why = QLabel()  # why the name cannot be used
        self.why.setWordWrap(True)
        self.why.setStyleSheet(f"color: {STATUS_COLORS['failed']};")
        self.why.hide()
        layout.addWidget(self.why)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _ok(self):
        why = self.problem(self.name.text().strip())
        self.why.setText(why)
        self.why.setVisible(bool(why))
        if not why:
            self.accept()


class CommitDialog(QDialog):
    """File → Save & Commit…: where the flow file is committed (`target`, a git.Target; `folder`: the flow's), the
    commit message, and the name and email to commit with. Its Save & Commit button needs all three; else the window
    says what is missing."""

    def __init__(self, file_name: str, target, folder: Path, name: str, email: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save & Commit")
        self.resize(560, 0)
        layout = QVBoxLayout(self)
        root = html.escape(str(target.root))
        where = (f"a new Git repository in <b>{root}</b> (there is none yet)" if target.new else
                 f"the Git repository in <b>{root}</b>")
        intro = QLabel(f"Saves <b>{html.escape(file_name)}</b> and commits it, and only it, to {where}, on branch "
                       f"<b>{html.escape(target.branch)}</b>."
                       + (" The flow's folder is inside that repository." if target.root != folder else ""))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.name, self.email, self.message = QLineEdit(name), QLineEdit(email), QPlainTextEdit()
        form.addRow("Name", self.name)
        form.addRow("Email", self.email)
        form.addRow("Message", self.message)
        layout.addLayout(form)
        self.why = QLabel()  # what is missing
        self.why.setWordWrap(True)
        self.why.setStyleSheet(f"color: {STATUS_COLORS['failed']};")
        self.why.hide()
        layout.addWidget(self.why)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("Save && Commit", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.message.setFocus()

    def _ok(self):
        named = self.name.text().strip() and self.email.text().strip()
        why = ("Write what changed." if not self.message.toPlainText().strip() else
               "" if named else "Give your name and email for the commit.")
        self.why.setText(why)
        self.why.setVisible(bool(why))
        if not why:
            self.accept()


class BlockResultsDialog(QDialog):
    """A block's results in the runs it took part in, newest first: when each run started, what started it,
    how the block ended; the selected run's values below. Show this run (or a double-click) chooses run_id."""

    def __init__(self, block_id: str, results: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Results of {block_id}")
        self.resize(820, 560)
        self.results, self.run_id = results, None
        layout = QVBoxLayout(self)
        intro = QLabel(f"{block_id} in each run it took part in, newest first. Select one to see its values; "
                       "<b>Show this run</b> (or a double-click) puts that run on the canvas." if results
                       else f"{block_id} has not taken part in a run of this flow yet.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(len(results), 5)
        self.table.setHorizontalHeaderLabels(["Run", "Started", "Started by", "Status", "Result"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        for row, r in enumerate(results):
            for col, text in enumerate([str(r["run_id"]), (r["started"] or "").replace("T", " ")[:19],
                                        r["started_by"] or "", r["status"], r["error"] or r["summary"] or ""]):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table, 2)
        self.values = QPlainTextEdit(readOnly=True)  # what the selected run's outputs held
        self.values.setFont(MONO)  # e.g. a table's rows, in columns
        self.values.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.values, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        show = buttons.addButton("Show this run", QDialogButtonBox.AcceptRole)
        show.setEnabled(bool(results))
        buttons.accepted.connect(lambda: self._choose(self.table.currentRow()))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.currentCellChanged.connect(lambda row, *_: self._show_values(row))
        self.table.cellDoubleClicked.connect(lambda row, _: self._choose(row))
        if results:
            self.table.setCurrentCell(0, 0)

    def _show_values(self, row: int):
        if 0 <= row < len(self.results):
            previews = self.results[row]["previews"]
            self.values.setPlainText("\n\n".join(f"{port}:\n{text}" for port, text in previews.items())
                                     or "No output values in this run.")

    def _choose(self, row: int):
        if 0 <= row < len(self.results):
            self.run_id = self.results[row]["run_id"]
            self.accept()


class PythonEnvDialog(QDialog):
    """Your Python environment: which one is used, its packages, and installing more with pip."""

    def __init__(self, home, parent=None):
        super().__init__(parent)
        self.home = home
        self.setWindowTitle("Python environment")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        intro = QLabel("Python Code blocks with <b>Run in my Python environment</b> ticked run here, so they can use "
                       "packages that Taskloom does not include. Install them with pip below; your pip settings "
                       "(e.g. a company package mirror) apply.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        create_row = QHBoxLayout()
        self.python = QLineEdit(home.settings().get("python") or "",
                                placeholderText="Python to create a new one from (empty: find one on this computer)")
        browse, self.create_button = QToolButton(text="…"), QPushButton("Create environment")
        self.existing_button = QPushButton("Use an existing environment…")
        create_row.addWidget(self.python, 1)
        create_row.addWidget(browse)
        create_row.addWidget(self.create_button)
        create_row.addWidget(self.existing_button)
        layout.addLayout(create_row)
        install_row = QHBoxLayout()
        self.packages = QLineEdit(placeholderText="packages, e.g. requests openpyxl==3.1.2")
        self.install_button = QPushButton("Install")
        install_row.addWidget(self.packages, 1)
        install_row.addWidget(self.install_button)
        layout.addLayout(install_row)
        self.output = QPlainTextEdit(readOnly=True)
        layout.addWidget(self.output, 1)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        browse.clicked.connect(self._browse)
        self.create_button.clicked.connect(self._create)
        self.existing_button.clicked.connect(self._use_existing)
        self.install_button.clicked.connect(
            lambda: self._run(["env", "pip", "install", *self.packages.text().split()], then=self._list))
        self.packages.returnPressed.connect(self.install_button.click)
        self.proc, self._partial, self._then = None, b"", None
        self._show_status()
        if self._env_python().exists():
            self._list()  # show what it has straight away

    def _env_python(self):
        from ..pyenv import env_python
        return env_python(self.home)

    def _show_status(self):
        from ..pyenv import env_dir, python_version

        python = self._env_python()
        ready = python.exists()
        if ready:
            version = python_version(python)
            self.status.setText(f"<b>In use:</b> {html.escape(str(env_dir(self.home)))} ({html.escape(version)})")
        else:
            self.status.setText(f"<b>Not created yet:</b> {html.escape(str(env_dir(self.home)))}. Create one, or use "
                                "an environment you already have.")
        for w in (self.packages, self.install_button):
            w.setEnabled(ready and self.proc is None)
        for w in (self.create_button, self.existing_button):
            w.setEnabled(self.proc is None)
        self.create_button.setText("Create again" if ready else "Create environment")

    def _save_setting(self, key, value):
        settings = self.home.settings()
        if value != settings.get(key):
            data = {k: v for k, v in {**settings, key: value}.items() if v != SETTINGS_DEFAULTS[k]}
            self.home.root.mkdir(parents=True, exist_ok=True)
            (self.home.root / "settings.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Python program", self.python.text())
        if path:
            self.python.setText(path)

    def _use_existing(self):
        from ..pyenv import python_in

        folder = QFileDialog.getExistingDirectory(self, "The environment's folder (it contains bin/ or Scripts/)")
        if not folder:
            return
        if not python_in(Path(folder)).exists():
            QMessageBox.warning(self, "Not a Python environment", f"There is no {python_in(Path(folder))}.")
            return
        self._save_setting("python_env", folder)
        self.output.appendPlainText(f"using {folder}")
        self._show_status()
        self._list()

    def _create(self):
        self._save_setting("python", self.python.text().strip() or None)
        self._save_setting("python_env", None)  # create the default one in the Taskloom folder
        self._run(["env", "create"] + (["--python", self.python.text().strip()] if self.python.text().strip() else []),
                  then=self._list)

    def _list(self):
        self._run(["env", "pip", "list"])

    def _add_output(self, data: bytes):
        """Show whole lines only (output arrives in pieces), with Windows line ends counted once."""
        *lines, self._partial = (self._partial + data).replace(b"\r\n", b"\n").split(b"\n")
        for line in lines:
            self.output.appendPlainText(line.decode(errors="replace").rstrip())

    def _run(self, args, then=None):
        """Run `taskloom ARGS`, showing its output; `then` runs after it succeeds (e.g. list the packages)."""
        if self.proc is not None:
            return
        self._then = then
        self.output.appendPlainText(f"$ taskloom {' '.join(args)}")
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("TASKLOOM_HOME", str(self.home.root))
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert(ARGS_VARIABLE, json.dumps(args))  # as the editor starts runs: output in UTF-8
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(lambda: self._add_output(bytes(self.proc.readAllStandardOutput())))
        self.proc.finished.connect(self._finished)
        program, *rest = runner_command()
        self.proc.start(program, rest)
        self._show_status()

    def _finished(self, code, _status):
        self._add_output(bytes(self.proc.readAllStandardOutput()))
        if self._partial:  # a last line without a line end
            self._add_output(b"\n")
        self.output.appendPlainText("done" if code == 0 else f"failed (exit code {code})")
        self.proc = None
        self._show_status()
        then, self._then = self._then, None
        if code == 0 and then is not None:
            then()
