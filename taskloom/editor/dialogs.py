"""Dialogs: run with parameters, overlapping runs, Ask User, connections, settings."""

from __future__ import annotations

import html
import subprocess
import sys
from pathlib import Path

import yaml
from PySide6.QtCore import QProcess, QProcessEnvironment, Qt

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)

from ..block import IDENTIFIER
from ..config import SETTINGS_DEFAULTS
from ..launch import runner_command
from ..table import Table


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
        self.kind, self.value = kind, None
        layout = QVBoxLayout(self)
        label = QLabel(prompt)
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons = QDialogButtonBox()
        if kind == "yes_no":
            for text, value in (("Yes", True), ("No", False)):
                b = buttons.addButton(text, QDialogButtonBox.AcceptRole)
                b.clicked.connect(lambda _=False, v=value: self._answer(v))
        else:
            if kind == "choice":
                self.input = QComboBox()
                self.input.addItems([str(o) for o in options])
                if default is not None:
                    self.input.setCurrentText(str(default))
            else:
                self.input = QLineEdit("" if default is None or kind == "password" else str(default))
                if kind == "password":
                    self.input.setEchoMode(QLineEdit.Password)
            layout.addWidget(self.input)
            ok = buttons.addButton("OK", QDialogButtonBox.AcceptRole)
            ok.clicked.connect(lambda: self._answer(self.input.currentText() if kind == "choice" else self.input.text()))
        cancel = buttons.addButton("Don't answer", QDialogButtonBox.RejectRole)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _answer(self, value):
        self.value = value
        self.accept()


class ConnectionsDialog(QDialog):
    """Edit connections.yaml. Passwords go to the secret store, never into the file."""

    KINDS = {"jdbc": "Database (JDBC)", "ssh": "Server (SSH)", "smtp": "Email server (SMTP)"}
    FIELDS = {"jdbc": ("driver", "url", "jars", "username"), "ssh": ("host", "port", "username", "key_file"),
              "smtp": ("host", "port", "security", "username", "sender")}
    HELP = {
        "jdbc": "A database you query with Run Query. Ask your database team for the driver .jar file, "
                "the driver class and the JDBC URL. Extra settings (timeouts, …) go in the table below.",
        "ssh": "A server you reach with SSH: for the Servers blocks, and file locations written name:path. "
               "Use your login and password, or a key file instead of a password.",
        "smtp": "The mail server Send Email and notifications use. Inside a company this is often a relay "
                "on port 25 that needs no login; ask your IT team.",
    }
    LABELS = {"driver": "Driver class", "url": "URL", "jars": "Driver .jar file(s)", "host": "Host", "port": "Port",
              "security": "Security", "username": "User name", "key_file": "Key file", "sender": "Send as"}

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
                        "sender": "the From address, e.g. reports@example.com", "host": "e.g. server01.example.com"}
        for key, text in placeholders.items():
            self.edits[key].setPlaceholderText(text)
        self.jar_row = QWidget()
        jar_layout = QHBoxLayout(self.jar_row)
        jar_layout.setContentsMargins(0, 0, 0, 0)
        browse = QToolButton(text="…")
        jar_layout.addWidget(self.edits["jars"])
        jar_layout.addWidget(browse)
        for key, edit in self.edits.items():
            self.form.addRow(self.LABELS[key], self.jar_row if key == "jars" else edit)
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
        self.editor_widgets = [self.kind, *self.edits.values(), self.password, self.props, self.add_prop,
                               self.remove_prop, self.test_button, browse]

        self.names.addItems(list(self.conns))
        self.names.currentTextChanged.connect(self._select)
        self.kind.currentIndexChanged.connect(lambda _: self._show_kind())
        add.clicked.connect(lambda: self._add(None))
        rename.clicked.connect(self._rename)
        remove.clicked.connect(self._remove)
        browse.clicked.connect(self._browse)
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
        self.help.setText(self.HELP[kind] if enabled else "Click New… to add a connection.")
        for key, edit in self.edits.items():
            self.form.setRowVisible(self.jar_row if key == "jars" else edit, enabled and key in self.FIELDS[kind])
        self.edits["port"].setPlaceholderText({"ssh": "22", "smtp": "25 (or 587 with starttls, 465 with ssl)"}.get(kind, ""))
        for w in (self.props, self.props_label, self.add_prop, self.remove_prop):
            w.setVisible(enabled and kind == "jdbc")
        self.form.setRowVisible(self.password, enabled)
        self.form.setRowVisible(self.password_status, enabled)
        self._show_password_status()

    def _password_ref(self, conn: dict):
        return (conn.get("properties") or {}).get("password") if conn.get("kind") == "jdbc" else conn.get("password")

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
        conn = {"kind": kind}
        for key in self.FIELDS[kind]:
            text = self.edits[key].text().strip()
            if key == "jars":
                conn["jars"] = [j.strip() for j in text.split(";") if j.strip()]
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
        self.kind.blockSignals(True)
        self.kind.setCurrentIndex(max(self.kind.findData(conn.get("kind", "jdbc")), 0))
        self.kind.blockSignals(False)
        props = dict(conn.get("properties") or {})
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

    def _write(self):
        """Save connections.yaml and any new passwords (to the secret store)."""
        self._store_current()
        for name, value in self.passwords.items():
            if name not in self.conns:
                continue
            secret = f"{name}_password"
            self.home.set_secret(secret, value)
            conn = self.conns[name]
            if conn["kind"] == "jdbc":
                conn.setdefault("properties", {})["password"] = f"secret:{secret}"
            else:
                conn["password"] = f"secret:{secret}"
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
        proc.setProcessEnvironment(env)

        def done(code, _status):
            out = bytes(proc.readAllStandardOutput()).decode(errors="replace").strip()
            err = bytes(proc.readAllStandardError()).decode(errors="replace").strip().removeprefix("error: ")
            ok = code == 0
            self.test_result.setText(f"<span style='color:{'#1f8a4c' if ok else '#c0392b'}'>"
                                     f"{'✓ ' + html.escape(out) if ok else '✕ ' + html.escape(err[-400:])}</span>")
            self.test_button.setEnabled(True)

        proc.finished.connect(done)
        program, *args = runner_command()
        proc.start(program, args + ["connection", "test", self.current])

    def _save(self):
        self._write()
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, home, parent=None):
        super().__init__(parent)
        self.home = home
        self.setWindowTitle("Settings")
        self.resize(640, 420)
        self.settings = home.settings()
        form = QFormLayout(self)
        self.calendar = QLineEdit(self.settings["calendar"] or "", placeholderText="weekends only, e.g. US or a calendars/<name>.yaml")
        self.keep_runs = QSpinBox(minimum=1, maximum=100000, value=int(self.settings["keep_runs"]))
        self.max_runs = QSpinBox(minimum=0, maximum=1000, value=int(self.settings["max_concurrent_runs"] or 0),
                                 specialValueText="unlimited")
        self.java_home = QLineEdit(self.settings["java_home"] or "", placeholderText="uses JAVA_HOME if empty")
        browse = QToolButton(text="…")
        browse.clicked.connect(self._browse)
        java_row = QWidget()
        row = QHBoxLayout(java_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.java_home)
        row.addWidget(browse)
        form.addRow("Default calendar", self.calendar)
        form.addRow("Keep files of last runs", self.keep_runs)
        form.addRow("Scheduled runs at once", self.max_runs)
        form.addRow("Java folder", java_row)
        self.at_login = QCheckBox("Start the scheduler when I log in")
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
        data = {**self.settings, "calendar": self.calendar.text().strip() or None, "keep_runs": self.keep_runs.value(),
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
                         "backups folder of your Taskloom folder first." if fixable else "Nothing to fix."))
        text.setWordWrap(True)
        layout.addWidget(text)
        self.details = QPlainTextEdit(readOnly=True)
        lines = []
        for r in reports:
            lines.append(f"{r.path}: " + ("OK" if r.ok else ""))
            lines += [f"    can fix: {t}" for t in r.fixes] + [f"    needs you: {t}" for t in r.problems]
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
        self.resize(560, 0)
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
        from ..pyenv import env_dir

        python = self._env_python()
        ready = python.exists()
        if ready:
            version = subprocess.run([str(python), "--version"], capture_output=True, text=True,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
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
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(lambda: self._add_output(bytes(self.proc.readAllStandardOutput())))
        self.proc.finished.connect(self._finished)
        program, *rest = runner_command()
        self.proc.start(program, rest + args)
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
