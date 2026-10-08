"""Dialogs: run with parameters, overlapping runs, Ask User, connections, settings."""

from __future__ import annotations

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

    def __init__(self, home, parent=None):
        super().__init__(parent)
        self.home = home
        self.setWindowTitle("Connections")
        self.resize(760, 460)
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
        form = QFormLayout()
        self.kind = QLabel("jdbc")
        self.driver = QLineEdit(placeholderText="e.g. com.example.jdbc.Driver")
        self.url = QLineEdit(placeholderText="jdbc:…")
        self.jars = QLineEdit(placeholderText="driver .jar file(s), separated by ;")
        browse = QToolButton(text="…")
        jar_row = QHBoxLayout()
        jar_row.addWidget(self.jars)
        jar_row.addWidget(browse)
        form.addRow("Kind", self.kind)
        form.addRow("Driver class", self.driver)
        form.addRow("URL", self.url)
        form.addRow("Jar files", jar_row)
        right.addLayout(form)
        right.addWidget(QLabel("Driver properties (user, timeouts, …)"))
        self.props = QTableWidget(0, 2)
        self.props.setHorizontalHeaderLabels(["Property", "Value"])
        self.props.horizontalHeader().setStretchLastSection(True)
        self.props.verticalHeader().hide()
        right.addWidget(self.props)
        prop_row = QHBoxLayout()
        add_prop, remove_prop, password = QPushButton("Add property"), QPushButton("Remove property"), QPushButton("Set password…")
        for b in (add_prop, remove_prop, password):
            prop_row.addWidget(b)
        right.addLayout(prop_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        right.addWidget(buttons)
        layout.addLayout(right, 3)

        self.current = None
        self.names.addItems(list(self.conns))
        self.names.currentTextChanged.connect(self._select)
        add.clicked.connect(self._add)
        remove.clicked.connect(self._remove)
        browse.clicked.connect(self._browse)
        add_prop.clicked.connect(lambda: self._add_prop("", ""))
        remove_prop.clicked.connect(lambda: self.props.removeRow(self.props.currentRow()))
        password.clicked.connect(self._set_password)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        if self.conns:
            self.names.setCurrentRow(0)

    def _add_prop(self, key, value):
        row = self.props.rowCount()
        self.props.insertRow(row)
        self.props.setItem(row, 0, QTableWidgetItem(str(key)))
        self.props.setItem(row, 1, QTableWidgetItem(str(value)))

    def _store_current(self):
        if self.current is None or self.current not in self.conns:
            return
        conn = self.conns[self.current]
        conn.update(driver=self.driver.text().strip(), url=self.url.text().strip(),
                    jars=[j.strip() for j in self.jars.text().split(";") if j.strip()])
        props = {}
        for r in range(self.props.rowCount()):
            key = self.props.item(r, 0) and self.props.item(r, 0).text().strip()
            if key:
                props[key] = self.props.item(r, 1).text() if self.props.item(r, 1) else ""
        conn["properties"] = props

    def _select(self, name):
        self._store_current()
        self.current = name or None
        conn = self.conns.get(name, {})
        self.kind.setText(conn.get("kind", "jdbc"))
        self.driver.setText(conn.get("driver", ""))
        self.url.setText(conn.get("url", ""))
        jars = conn.get("jars") or []
        self.jars.setText("; ".join([jars] if isinstance(jars, str) else jars))
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
            self.jars.setText("; ".join(paths))

    def _set_password(self):
        if self.current is None:
            return
        value, ok = QInputDialog.getText(self, "Password", f"Password for '{self.current}':", QLineEdit.Password)
        if not ok:
            return
        secret = f"{self.current}_password"
        where = self.home.set_secret(secret, value)
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
        self.settings = home.settings()
        form = QFormLayout(self)
        self.calendar = QLineEdit(self.settings["calendar"] or "", placeholderText="weekends only, e.g. US or a calendars/<name>.yaml")
        self.keep_runs = QSpinBox(minimum=1, maximum=100000, value=int(self.settings["keep_runs"]))
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
        form.addRow("Java folder", java_row)
        form.addRow(QLabel(f"Settings file: {home.root / 'settings.yaml'}"))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Java folder (contains bin/java)", self.java_home.text())
        if path:
            self.java_home.setText(path)

    def _save(self):
        data = {"calendar": self.calendar.text().strip() or None, "keep_runs": self.keep_runs.value(),
                "java_home": self.java_home.text().strip() or None}
        data = {k: v for k, v in data.items() if v != SETTINGS_DEFAULTS[k]}
        self.home.root.mkdir(parents=True, exist_ok=True)
        (self.home.root / "settings.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self.accept()
