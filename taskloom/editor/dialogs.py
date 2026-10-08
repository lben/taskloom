"""Dialogs: run with parameters, overlapping runs, Ask User, connections, settings."""

from __future__ import annotations

import sys

import yaml

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)

from ..config import SETTINGS_DEFAULTS


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

    KINDS = ("jdbc", "ssh", "smtp")

    def __init__(self, home, parent=None):
        super().__init__(parent)
        self.home = home
        self.setWindowTitle("Connections")
        self.resize(780, 480)
        self.conns = home.connections()
        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        self.names = QListWidget()
        add, remove = QPushButton("Add"), QPushButton("Remove")
        left.addWidget(self.names)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        left.addLayout(row)
        layout.addLayout(left, 1)

        right = QVBoxLayout()
        self.form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItems(self.KINDS)
        self.edits = {key: QLineEdit() for key in
                      ("driver", "url", "jars", "host", "port", "security", "username", "key_file", "sender")}
        self.edits["driver"].setPlaceholderText("e.g. com.example.jdbc.Driver")
        self.edits["url"].setPlaceholderText("jdbc:…")
        self.edits["jars"].setPlaceholderText("driver .jar file(s), separated by ;")
        self.edits["port"].setPlaceholderText("22 for SSH; 25, 587 or 465 for email")
        self.edits["security"].setPlaceholderText("none, starttls or ssl")
        self.edits["sender"].setPlaceholderText("the From address, e.g. reports@example.com")
        self.edits["key_file"].setPlaceholderText("optional, e.g. ~/.ssh/id_ed25519 (or set a password)")
        browse = QToolButton(text="…")
        jar_row = QWidget()
        jar_layout = QHBoxLayout(jar_row)
        jar_layout.setContentsMargins(0, 0, 0, 0)
        jar_layout.addWidget(self.edits["jars"])
        jar_layout.addWidget(browse)
        self.form.addRow("Kind", self.kind)
        labels = {"driver": "Driver class", "url": "URL", "host": "Host", "port": "Port", "security": "Security",
                  "username": "User name", "key_file": "Key file", "sender": "Sender"}
        for key, edit in self.edits.items():
            self.form.addRow("Jar files" if key == "jars" else labels[key], jar_row if key == "jars" else edit)
        self.props_label = QLabel("Driver properties (user, timeouts, …)")
        self.form.addRow(self.props_label)
        right.addLayout(self.form)
        self.props = QTableWidget(0, 2)
        self.props.setHorizontalHeaderLabels(["Property", "Value"])
        self.props.horizontalHeader().setStretchLastSection(True)
        self.props.verticalHeader().hide()
        right.addWidget(self.props)
        prop_row = QHBoxLayout()
        self.add_prop, self.remove_prop = QPushButton("Add property"), QPushButton("Remove property")
        password = QPushButton("Set password…")
        for b in (self.add_prop, self.remove_prop, password):
            prop_row.addWidget(b)
        right.addLayout(prop_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        right.addWidget(buttons)
        layout.addLayout(right, 3)

        self.current = None
        self.names.addItems(list(self.conns))
        self.names.currentTextChanged.connect(self._select)
        self.kind.currentTextChanged.connect(self._show_kind)
        add.clicked.connect(self._add)
        remove.clicked.connect(self._remove)
        browse.clicked.connect(self._browse)
        self.add_prop.clicked.connect(lambda: self._add_prop("", ""))
        self.remove_prop.clicked.connect(lambda: self.props.removeRow(self.props.currentRow()))
        password.clicked.connect(self._set_password)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self._show_kind(self.kind.currentText())
        if self.conns:
            self.names.setCurrentRow(0)

    FIELDS = {"jdbc": ("driver", "url", "jars"), "ssh": ("host", "port", "username", "key_file"),
              "smtp": ("host", "port", "security", "username", "sender")}

    def _show_kind(self, kind):
        for key, edit in self.edits.items():
            widget = edit.parentWidget() if key == "jars" else edit
            self.form.setRowVisible(widget, key in self.FIELDS[kind])
        for w in (self.props, self.add_prop, self.remove_prop):
            w.setVisible(kind == "jdbc")
        self.form.setRowVisible(self.props_label, kind == "jdbc")

    def _add_prop(self, key, value):
        row = self.props.rowCount()
        self.props.insertRow(row)
        self.props.setItem(row, 0, QTableWidgetItem(str(key)))
        self.props.setItem(row, 1, QTableWidgetItem(str(value)))

    def _store_current(self):
        if self.current is None or self.current not in self.conns:
            return
        kind = self.kind.currentText()
        old = self.conns[self.current]
        conn = {"kind": kind}
        if kind == "jdbc":
            conn.update(driver=self.edits["driver"].text().strip(), url=self.edits["url"].text().strip(),
                        jars=[j.strip() for j in self.edits["jars"].text().split(";") if j.strip()])
            props = {}
            for r in range(self.props.rowCount()):
                key = self.props.item(r, 0) and self.props.item(r, 0).text().strip()
                if key:
                    props[key] = self.props.item(r, 1).text() if self.props.item(r, 1) else ""
            conn["properties"] = props
        else:
            for key in self.FIELDS[kind]:
                text = self.edits[key].text().strip()
                if text:
                    conn[key] = int(text) if key == "port" and text.isdigit() else text
            if old.get("kind") == kind and old.get("password"):
                conn["password"] = old["password"]
        self.conns[self.current] = conn

    def _select(self, name):
        self._store_current()
        self.current = name or None
        conn = self.conns.get(name, {})
        self.kind.setCurrentText(conn.get("kind", "jdbc"))
        jars = conn.get("jars") or []
        for key, edit in self.edits.items():
            edit.setText("; ".join([jars] if isinstance(jars, str) else jars) if key == "jars" else str(conn.get(key, "")))
        self.props.setRowCount(0)
        for key, value in (conn.get("properties") or {}).items():
            self._add_prop(key, value)

    def _add(self):
        name, ok = QInputDialog.getText(self, "New connection", "Name (letters, digits, _):")
        name = name.strip()
        if ok and name and name not in self.conns:
            self.conns[name] = {"kind": "jdbc", "properties": {}}
            self.names.addItem(name)
            self.names.setCurrentRow(self.names.count() - 1)

    def _remove(self):
        item = self.names.currentItem()
        if item is not None:
            self.conns.pop(item.text(), None)
            self.current = None
            self.names.takeItem(self.names.row(item))

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "JDBC driver jars", "", "Jar files (*.jar)")
        if paths:
            self.edits["jars"].setText("; ".join(paths))

    def _set_password(self):
        if self.current is None:
            return
        value, ok = QInputDialog.getText(self, "Password", f"Password for '{self.current}':", QLineEdit.Password)
        if not ok:
            return
        secret = f"{self.current}_password"
        where = self.home.set_secret(secret, value)
        self._store_current()
        if self.kind.currentText() in ("ssh", "smtp"):
            self.conns[self.current]["password"] = f"secret:{secret}"
        else:
            for r in range(self.props.rowCount()):
                if self.props.item(r, 0).text().strip() == "password":
                    self.props.item(r, 1).setText(f"secret:{secret}")
                    break
            else:
                self._add_prop("password", f"secret:{secret}")
        QMessageBox.information(self, "Password saved", f"Saved in {where}.")

    def _save(self):
        self._store_current()
        self.home.root.mkdir(parents=True, exist_ok=True)
        (self.home.root / "connections.yaml").write_text(yaml.safe_dump(self.conns, sort_keys=False), encoding="utf-8")
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
