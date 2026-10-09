"""Side and bottom panels of the editor window."""

from __future__ import annotations

import datetime as dt
import html
import math

import polars as pl
import yaml
from PySide6.QtCore import QAbstractTableModel, QDate, QMimeData, QModelIndex, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QTableView,
                               QTableWidget, QTableWidgetItem, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import params as P
from ..block import IDENTIFIER, fields
from ..calendars import load_calendar
from ..expr import functions
from ..flow import OVERLAP_POLICIES, BlockSpec, Retry, block_config, parse_duration
from ..schedule import MISFIRE_POLICIES, Trigger
from .canvas import BLOCK_MIME, CATEGORY_COLORS, STATUS_COLORS, USER_COLOR

MONO = QFontDatabase.systemFont(QFontDatabase.FixedFont)


def _square_icon(color: str, size: int = 14) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 3, 3)
    p.end()
    return QIcon(pix)


def status_icon(status: str | None, frame: int = 0, size: int = 14, clock: bool = False) -> QIcon:
    """Spinner (running), green dot (success), red ✕ (failed), hollow dot (never run); plus a clock if scheduled."""
    pix = QPixmap(size * 2 + 2 if clock else size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    c = size / 2
    if status == "running":
        p.setPen(QPen(QColor("#f1dcb3"), 2))
        p.drawEllipse(QPointF(c, c), c - 2, c - 2)
        p.setPen(QPen(QColor(STATUS_COLORS["running"]), 2))
        p.drawArc(QRectF(2, 2, size - 4, size - 4), -frame * 45 * 16, 100 * 16)
    elif status == "success":
        p.setBrush(QColor(STATUS_COLORS["success"]))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(c, c), 4.5, 4.5)
    elif status in ("failed", "cancelled"):
        p.setPen(QPen(QColor(STATUS_COLORS["failed"] if status == "failed" else "#9aa6b2"), 2.2))
        p.drawLine(QPointF(3, 3), QPointF(size - 3, size - 3))
        p.drawLine(QPointF(size - 3, 3), QPointF(3, size - 3))
    else:
        p.setPen(QPen(QColor("#9aa6b2"), 1.5))
        p.drawEllipse(QPointF(c, c), 4, 4)
    if clock:
        x = size + 2 + c
        p.setPen(QPen(QColor("#5f6d7c"), 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(x, c), c - 1.5, c - 1.5)
        p.drawLine(QPointF(x, c), QPointF(x, 3.5))
        p.drawLine(QPointF(x, c), QPointF(x + 2.5, c + 1.5))
    p.end()
    return QIcon(pix)


def scheduler_icon(running: bool, size: int = 20) -> QIcon:
    """A play (or stop) symbol with a small clock: the scheduler button."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor(STATUS_COLORS["success"] if running else "#5f6d7c")
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    if running:
        p.drawRoundedRect(QRectF(2, 2, 10, 10), 2, 2)  # stop
    else:
        p.drawPolygon([QPointF(3, 1.5), QPointF(13, 7), QPointF(3, 12.5)])  # play
    c = QPointF(size - 6, size - 6)
    p.setBrush(QApplication.palette().base())
    p.setPen(QPen(color, 1.6))
    p.drawEllipse(c, 5, 5)
    p.drawLine(c, c + QPointF(0, -3))
    p.drawLine(c, c + QPointF(2.2, 1.2))
    p.end()
    return QIcon(pix)


def block_tooltip(cls) -> str:
    """The hover text for a block: what it does in plain words."""
    text = cls.description or "No description yet: add description = \"...\" to this block's class."
    return f"<p style='white-space:normal'><b>{html.escape(cls.title or cls.type_id)}</b><br>{html.escape(text)}</p>"


# --- Blocks tree ------------------------------------------------------------------

class _BlockTree(QTreeWidget):
    def startDrag(self, actions):
        item = self.currentItem()
        type_id = item and item.data(0, Qt.UserRole)
        if not type_id:
            return
        mime = QMimeData()
        mime.setData(BLOCK_MIME, type_id.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon(0).pixmap(16, 16))
        drag.exec(Qt.CopyAction)


class Palette(QWidget):
    """Blocks as a tree of categories, like Godot's node list. Drag onto the canvas or double-click."""

    add_requested = Signal(str)

    def __init__(self, registry):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 0)
        title = QLabel("BLOCKS")
        title.setStyleSheet("font-weight: 600; color: palette(placeholder-text); letter-spacing: 1px;")
        self.search = QLineEdit(placeholderText="Search blocks…")
        self.search.setClearButtonEnabled(True)
        self.tree = _BlockTree()
        self.tree.setHeaderHidden(True)
        self.tree.setDragEnabled(True)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(True)
        layout.addWidget(title)
        layout.addWidget(self.search)
        layout.addWidget(self.tree)
        self._build(registry)
        self.search.textChanged.connect(self._filter)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

    def _build(self, registry):
        groups: dict[str, list] = {}
        for type_id, cls in registry.blocks.items():
            source = registry.sources.get(type_id)
            category = cls.category if source == "built-in" else "User blocks"
            groups.setdefault(category, []).append(cls)
        order = list(CATEGORY_COLORS) + sorted(set(groups) - set(CATEGORY_COLORS))
        for category in [c for c in order if c in groups]:
            color = CATEGORY_COLORS.get(category, USER_COLOR)
            folder = QTreeWidgetItem([f"{category}  ({len(groups[category])})"])
            folder.setIcon(0, _square_icon(color, 12))
            folder.setFlags(Qt.ItemIsEnabled)
            font = folder.font(0)
            font.setBold(True)
            folder.setFont(0, font)
            self.tree.addTopLevelItem(folder)
            for cls in sorted(groups[category], key=lambda c: c.title or c.type_id):
                item = QTreeWidgetItem([cls.title or cls.type_id])
                item.setIcon(0, _square_icon(color))
                item.setData(0, Qt.UserRole, cls.type_id)
                item.setToolTip(0, block_tooltip(cls))
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
                folder.addChild(item)
            folder.setExpanded(True)

    def _filter(self, text: str):
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            folder = self.tree.topLevelItem(i)
            any_visible = False
            for j in range(folder.childCount()):
                child = folder.child(j)
                match = not text or text in child.text(0).lower() or text in child.data(0, Qt.UserRole).lower()
                child.setHidden(not match)
                any_visible |= match
            folder.setHidden(not any_visible)
            folder.setExpanded(any_visible)

    def _on_double_click(self, item, column):
        type_id = item.data(0, Qt.UserRole)
        if type_id:
            self.add_requested.emit(type_id)


# --- Flows list -------------------------------------------------------------------

class FlowsPanel(QWidget):
    """Every open flow with its run status."""

    activated = Signal(object)  # the FlowTab
    run_requested = Signal(object)
    last_run_requested = Signal(object)
    schedule_toggled = Signal(object, bool)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        title = QLabel("FLOWS")
        title.setStyleSheet("font-weight: 600; color: palette(placeholder-text); letter-spacing: 1px;")
        self.list = QListWidget()
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.setIconSize(QSize(30, 14))
        layout.addWidget(title)
        layout.addWidget(self.list)
        self.items: dict[int, QListWidgetItem] = {}
        self._frame = 0
        self._timer = QTimer(self, interval=120)
        self._timer.timeout.connect(self._spin)
        self._timer.start()
        self.list.itemClicked.connect(lambda item: self.activated.emit(item.data(Qt.UserRole)))
        self.list.customContextMenuRequested.connect(self._menu)

    def add(self, tab):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, tab)
        self.list.addItem(item)
        self.items[id(tab)] = item
        self.update_tab(tab)

    def remove(self, tab):
        item = self.items.pop(id(tab), None)
        if item is not None:
            self.list.takeItem(self.list.row(item))

    def select(self, tab):
        item = self.items.get(id(tab))
        if item is not None:
            self.list.setCurrentItem(item)

    def update_tab(self, tab):
        item = self.items.get(id(tab))
        if item is None:
            return
        status, subtitle = tab.list_status()
        if tab.next_run:
            when = tab.next_run.strftime("%H:%M" if tab.next_run.date() == dt.date.today() else "%a %d %b %H:%M")
            subtitle += f" · next {when}"
            item.setToolTip(f"Scheduled; next run {tab.next_run:%Y-%m-%d %H:%M}")
        else:
            item.setToolTip("")
        item.setText(f"{tab.doc.data.get('name') or tab.doc.title}\n{subtitle}")
        item.setData(Qt.UserRole + 1, status)
        item.setData(Qt.UserRole + 2, tab.next_run is not None)
        item.setIcon(status_icon(status, self._frame, clock=tab.next_run is not None))
        item.setForeground(QColor(STATUS_COLORS["failed"]) if status == "failed" else self.list.palette().text().color())

    def _spin(self):
        self._frame = (self._frame + 1) % 8
        for item in self.items.values():
            if item.data(Qt.UserRole + 1) == "running":
                item.setIcon(status_icon("running", self._frame, clock=bool(item.data(Qt.UserRole + 2))))

    def _menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        tab = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction("Run now", lambda: self.run_requested.emit(tab))
        menu.addAction("Open", lambda: self.activated.emit(tab))
        menu.addAction("Open last run", lambda: self.last_run_requested.emit(tab))
        if tab.doc.path is not None:
            menu.addSeparator()
            if tab.scheduled:
                menu.addAction("Disable schedule", lambda: self.schedule_toggled.emit(tab, False))
            else:
                menu.addAction("Enable schedule", lambda: self.schedule_toggled.emit(tab, True))
        menu.exec(self.list.mapToGlobal(pos))


# --- Properties -------------------------------------------------------------------

class CodeEdit(QPlainTextEdit):
    editing_finished = Signal()

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setFont(MONO)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.setMinimumHeight(110)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.editing_finished.emit()


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


class PropertiesPanel(QScrollArea):
    """Properties of the selected block, generated from its config fields."""

    def __init__(self, home):
        super().__init__()
        self.home = home
        self.setWidgetResizable(True)
        self.doc = self.registry = self.block_id = None
        self._committing = False
        self.refresh()

    def show_block(self, doc, registry, block_id):
        self.doc, self.registry, self.block_id = doc, registry, block_id
        self.refresh()

    def on_doc_changed(self):
        if not self._committing:
            if self.doc is not None and self.block_id not in self.doc.blocks:
                self.block_id = None
            self.refresh()

    def _commit(self, fn):
        self._committing = True
        try:
            fn()
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()
            return
        finally:
            self._committing = False
        self._show_problems()

    def refresh(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setAlignment(Qt.AlignTop)
        self.setWidget(body)
        if self.doc is None or self.block_id is None:
            hint = QLabel("Select a block to see its properties.\n\nDrag blocks from the tree on the left, "
                          "or double-click them. Connect an output (right side) to an input (left side) by dragging.")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: palette(placeholder-text);")
            layout.addWidget(hint)
            return
        spec = self.doc.blocks[self.block_id]
        cls = self.registry.get(spec.get("type"))
        head = QLabel(f"<b>{html.escape(cls.title if cls else spec.get('type'))}</b>"
                      f"<br><span style='color: gray'>{html.escape(spec.get('type'))}</span>")
        layout.addWidget(head)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {STATUS_COLORS['failed']};")
        self.error.hide()
        layout.addWidget(self.error)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        name = QLineEdit(self.block_id)
        name.editingFinished.connect(lambda: self._commit(lambda: self._rename(name.text().strip())))
        form.addRow("Name", name)
        layout.addLayout(form)
        if cls is None:
            layout.addWidget(QLabel("This block type is not installed."))
            return
        config = spec.get("config") or {}
        for key, f in cls.config.items():
            widget = self._field_widget(key, f, config.get(key))
            label = QLabel(key.replace("_", " ").capitalize())
            if f.help:
                label.setToolTip(f.help)
                widget.setToolTip(f.help)
            if isinstance(f, fields.Code):
                layout.addWidget(label)
                layout.addWidget(widget)
            else:
                row = QFormLayout()
                row.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
                row.addRow(label, widget)
                layout.addLayout(row)
        layout.addWidget(self._retry_box(spec))
        self._show_problems()

    def _rename(self, new):
        old = self.block_id
        self.block_id = new
        try:
            self.doc.rename_block(old, new)
        except ValueError:
            self.block_id = old
            raise

    def _set(self, key, value, section="config"):
        self._commit(lambda: self.doc.set_block_value(self.block_id, key, value, section))

    def _field_widget(self, key, f, value):
        default = f.default
        if isinstance(f, fields.Choice):
            w = QComboBox()
            w.addItems(f.options)
            w.setCurrentText(str(value if value is not None else default or f.options[0]))
            w.currentTextChanged.connect(lambda t: self._set(key, None if t == default else t))
            return w
        if isinstance(f, fields.Bool):
            w = QCheckBox()
            w.setChecked(bool(value if value is not None else default))
            w.toggled.connect(lambda checked: self._set(key, None if checked == default else checked))
            return w
        if isinstance(f, fields.Connection):
            w = QComboBox(editable=True)
            try:
                names = [n for n, c in self.home.connections().items() if c.get("kind") == f.kind]
            except ValueError:
                names = []
            w.addItems(names)
            w.setCurrentText(_to_text(value))
            w.lineEdit().editingFinished.connect(lambda: self._set(key, w.currentText().strip() or None))
            w.activated.connect(lambda _: self._set(key, w.currentText().strip() or None))
            return w
        if isinstance(f, fields.Code):
            w = CodeEdit(_to_text(value))
            w.setPlaceholderText(_to_text(default))
            w.editing_finished.connect(lambda: self._set(key, w.toPlainText() or None))
            return w
        w = QLineEdit(_to_text(value))
        w.setPlaceholderText(_to_text(default))
        if isinstance(f, (fields.Names, fields.List)):
            w.setPlaceholderText(_to_text(default) or "comma, separated")
            convert = lambda t: None if not t.strip() else [x.strip() for x in t.split(",") if x.strip()]  # noqa: E731
        elif isinstance(f, fields.Int):
            convert = lambda t: int(t) if t.strip() else None  # noqa: E731
        elif isinstance(f, fields.Float):
            convert = lambda t: float(t) if t.strip() else None  # noqa: E731
        elif isinstance(f, fields.Text):
            convert = lambda t: t if t != "" else None  # noqa: E731
        else:  # a field type from a plugin: edit its value as YAML
            w.setText("" if value is None else yaml.safe_dump(value, default_flow_style=True).strip())
            convert = lambda t: yaml.safe_load(t) if t.strip() else None  # noqa: E731

        def commit():
            try:
                self._set(key, convert(w.text()))
            except ValueError as e:
                self.error.setText(f"{key}: {e}")
                self.error.show()

        w.editingFinished.connect(commit)
        if isinstance(f, fields.Path):
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            browse = QToolButton(text="…")
            browse.clicked.connect(lambda: self._browse(w, commit))
            row.addWidget(w)
            row.addWidget(browse)
            return box
        return w

    def _browse(self, line_edit, commit):
        path, _ = QFileDialog.getSaveFileName(self, "Choose a file", line_edit.text(),
                                              options=QFileDialog.DontConfirmOverwrite)
        if path:
            line_edit.setText(path)
            commit()

    def _retry_box(self, spec):
        retry = dict(spec.get("retry") or {})
        box = QGroupBox("Retry and timeout")
        form = QFormLayout(box)
        policy = QComboBox()
        policy.addItems(["none", "fixed", "exponential"])
        policy.setCurrentText(retry.get("policy", "none"))
        form.addRow("Policy", policy)
        shown = {"fixed": ["max_attempts", "delay"],
                 "exponential": ["max_attempts", "initial", "factor", "max_delay", "jitter"]}.get(retry.get("policy", "none"), [])
        placeholders = {"max_attempts": "3", "delay": "e.g. 30s", "initial": "e.g. 30s", "factor": "2",
                        "max_delay": "e.g. 10m", "jitter": "0 to 1"}
        edits = {}
        for key in shown:
            edits[key] = QLineEdit(_to_text(retry.get(key)), placeholderText=placeholders[key])
            form.addRow(key.replace("_", " ").capitalize(), edits[key])
        timeout = QLineEdit(_to_text(spec.get("timeout")), placeholderText="none, e.g. 20m")
        form.addRow("Timeout", timeout)

        def commit_retry():
            data = {"policy": policy.currentText()}
            for key, edit in edits.items():
                text = edit.text().strip()
                if text:
                    data[key] = float(text) if key in ("factor", "jitter") else int(text) if key == "max_attempts" else text
            Retry.parse(data)  # raises ValueError with a readable message
            self._set("retry", None if data == {"policy": "none"} else data, section=None)

        def commit_policy():
            self._commit(lambda: self.doc.set_block_value(
                self.block_id, "retry", None if policy.currentText() == "none" else {"policy": policy.currentText()}, None))
            self.refresh()

        def commit_timeout():
            text = timeout.text().strip()
            if text:
                parse_duration(text)
            self._set("timeout", text or None, section=None)

        policy.currentTextChanged.connect(lambda _: commit_policy())
        for edit in edits.values():
            edit.editingFinished.connect(lambda: self._commit(commit_retry))
        timeout.editingFinished.connect(lambda: self._commit(commit_timeout))
        return box

    def _show_problems(self):
        spec = self.doc.blocks.get(self.block_id)
        cls = spec and self.registry.get(spec.get("type"))
        if not cls:
            return
        try:
            block_config(cls, BlockSpec(id=self.block_id, type=cls.type_id, config=dict(spec.get("config") or {})))
            self.error.hide()
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()


# --- Parameters -------------------------------------------------------------------

class ParametersPanel(QWidget):
    """The flow's parameters with a live preview of their values on a chosen day."""

    COLUMNS = ["Name", "Type", "Kind", "Value", "Preview"]

    def __init__(self, home):
        super().__init__()
        self.home, self.doc, self._committing = home, None, False
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Preview as of"))
        self.as_of = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.as_of.setDisplayFormat("ddd yyyy-MM-dd")
        top.addWidget(self.as_of)
        top.addStretch()
        add, remove = QPushButton("Add"), QPushButton("Remove")
        top.addWidget(add)
        top.addWidget(remove)
        layout.addLayout(top)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        header = self.table.horizontalHeader()
        for col in (0, 1, 2, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self.note)
        add.clicked.connect(self._add)
        remove.clicked.connect(self._remove)
        self.as_of.dateChanged.connect(lambda _: self._preview())
        self.table.itemChanged.connect(lambda _: self._commit())

    def show_doc(self, doc):
        self.doc = doc
        self.refresh()

    def on_doc_changed(self):
        if not self._committing:
            self.refresh()

    def refresh(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for name, spec in (self.doc.data.get("params") or {}).items() if self.doc else []:
            kind = "expression" if "expr" in spec else "value"
            self._insert(name, spec.get("type", "text"), kind, _to_text(spec.get("expr", spec.get("value"))))
        self.table.blockSignals(False)
        self._preview()

    def _insert(self, name, type_, kind, value):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(name))
        types = QComboBox()
        types.addItems(P.TYPES)
        types.setCurrentText(type_)
        kinds = QComboBox()
        kinds.addItems(["value", "expression"])
        kinds.setCurrentText(kind)
        self.table.setCellWidget(row, 1, types)
        self.table.setCellWidget(row, 2, kinds)
        item = QTableWidgetItem(value)
        item.setFont(MONO)
        item.setToolTip(value)
        self.table.setItem(row, 3, item)
        preview = QTableWidgetItem("")
        preview.setFlags(Qt.ItemIsEnabled)
        preview.setFont(MONO)
        self.table.setItem(row, 4, preview)
        types.currentTextChanged.connect(lambda _: self._commit())
        kinds.currentTextChanged.connect(lambda _: self._commit())

    def _add(self):
        names = {self.table.item(r, 0).text() for r in range(self.table.rowCount())}
        n = 1
        while f"param{n}" in names:
            n += 1
        self.table.blockSignals(True)
        self._insert(f"param{n}", "text", "value", "")
        self.table.blockSignals(False)
        self._commit()

    def _remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._commit()

    def _rows(self):
        for r in range(self.table.rowCount()):
            yield (self.table.item(r, 0).text().strip(), self.table.cellWidget(r, 1).currentText(),
                   self.table.cellWidget(r, 2).currentText(), self.table.item(r, 3).text())

    def _commit(self):
        if self.doc is None:
            return
        params, problems = {}, []
        for name, type_, kind, value in self._rows():
            if not IDENTIFIER.match(name) or name in params:
                problems.append(f"'{name}' is not a valid, unique name")
                continue
            params[name] = {"type": type_, ("expr" if kind == "expression" else "value"): value}
        self._committing = True
        try:
            self.doc.set_params(params)
        finally:
            self._committing = False
        self._preview(problems)

    def _preview(self, problems=()):
        if self.doc is None:
            return
        day = self.as_of.date().toPython()
        now = dt.datetime.combine(day, dt.datetime.now().time())
        cal_name = self.doc.data.get("calendar") or self._setting("calendar")
        values, notes = {}, list(problems)
        try:
            calendar = load_calendar(cal_name, self.home.calendars_dir)
            funcs = functions(now, calendar, lambda n: load_calendar(n, self.home.calendars_dir))
        except Exception as e:
            funcs, notes = {}, notes + [f"calendar: {e}"]
        self.table.blockSignals(True)
        for r, (name, type_, kind, value) in enumerate(self._rows()):
            cell = self.table.item(r, 4)
            try:
                raw = P.evaluate(value, {**funcs, **values}) if kind == "expression" else value
                values[name] = P.coerce(type_, raw)
                text = _to_text(values[name])
                if isinstance(values[name], dt.date):
                    text += values[name].strftime(" (%a)")
                cell.setText(text)
                cell.setForeground(QColor(STATUS_COLORS["success"]))
            except Exception as e:
                cell.setText(str(e))
                cell.setForeground(QColor(STATUS_COLORS["failed"]))
        self.table.blockSignals(False)
        notes.append(f"Calendar: {cal_name or 'weekends only'}. Business-day functions skip its weekends and holidays.")
        self.note.setText("\n".join(notes))

    def _setting(self, key):
        try:
            return self.home.settings()[key]
        except ValueError:
            return None


# --- Flow settings ----------------------------------------------------------------

class FlowSettingsPanel(QWidget):
    """Name, calendar and time zone of the flow, and when the scheduler runs it."""

    schedule_toggled = Signal(bool)

    def __init__(self, home):
        super().__init__()
        self.home, self.doc = home, None
        layout = QVBoxLayout(self)
        self.form = QFormLayout()
        self.name = QLineEdit()
        self.description = QLineEdit()
        self.calendar = QComboBox(editable=True)
        self.calendar.lineEdit().setPlaceholderText("default from settings")
        self.timezone = QLineEdit(placeholderText="machine local, e.g. Europe/Madrid")
        self.form.addRow("Name", self.name)
        self.form.addRow("Description", self.description)
        self.form.addRow("Calendar", self.calendar)
        self.form.addRow("Time zone", self.timezone)
        layout.addLayout(self.form)

        box = QGroupBox("Schedule")
        schedule = QVBoxLayout(box)
        self.scheduled = QCheckBox("Run on schedule (the scheduler must be running)")
        schedule.addWidget(self.scheduled)
        self.triggers = QTableWidget(0, 3)
        self.triggers.setHorizontalHeaderLabels(["When", "Cron or date and time", "If missed"])
        header = self.triggers.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.triggers.verticalHeader().hide()
        self.triggers.setMinimumHeight(110)
        schedule.addWidget(self.triggers)
        buttons = QHBoxLayout()
        add, remove = QPushButton("Add"), QPushButton("Remove")
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        schedule.addLayout(buttons)
        overlap_row = QFormLayout()
        self.overlap = QComboBox()
        self.overlap.addItems(OVERLAP_POLICIES)
        overlap_row.addRow("If the previous run is still going", self.overlap)
        schedule.addLayout(overlap_row)
        self.next_label = QLabel()
        self.next_label.setWordWrap(True)
        schedule.addWidget(self.next_label)
        hint = QLabel("Cron: minute hour day month weekday, e.g. <code>0 7 * * MON-FRI</code> (07:00 on weekdays), "
                      "<code>*/5 * * * *</code> (every 5 minutes). Once: <code>2026-10-08 15:30</code>. "
                      "If missed: what to do with runs due while the computer was off.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        schedule.addWidget(hint)
        layout.addWidget(box)
        layout.addStretch()

        self.name.editingFinished.connect(lambda: self._set("name", self.name.text().strip()))
        self.description.editingFinished.connect(lambda: self._set("description", self.description.text().strip()))
        self.calendar.lineEdit().editingFinished.connect(lambda: self._set("calendar", self.calendar.currentText().strip()))
        self.calendar.activated.connect(lambda _: self._set("calendar", self.calendar.currentText().strip()))
        self.timezone.editingFinished.connect(lambda: self._set("timezone", self.timezone.text().strip()))
        self.overlap.activated.connect(lambda _: self._set("overlap", None if self.overlap.currentText() == "skip" else self.overlap.currentText()))
        self.scheduled.clicked.connect(lambda: self.schedule_toggled.emit(self.scheduled.isChecked()))
        add.clicked.connect(lambda: (self._add_trigger("cron", "0 7 * * MON-FRI", "run_once"), self._commit_triggers()))
        remove.clicked.connect(lambda: (self.triggers.removeRow(self.triggers.currentRow()), self._commit_triggers()))
        self.triggers.itemChanged.connect(lambda _: self._commit_triggers())

    def _set(self, key, value):
        if self.doc is not None:
            self.doc.set_flow_value(key, value)

    def show_doc(self, doc):
        self.doc = doc
        self.refresh()

    def _add_trigger(self, kind, value, misfire):
        self.triggers.blockSignals(True)
        r = self.triggers.rowCount()
        self.triggers.insertRow(r)
        kinds = QComboBox()
        kinds.addItems(["cron", "once"])
        kinds.setCurrentText(kind)
        misfires = QComboBox()
        misfires.addItems(MISFIRE_POLICIES)
        misfires.setCurrentText(misfire)
        self.triggers.setCellWidget(r, 0, kinds)
        self.triggers.setItem(r, 1, QTableWidgetItem(value))
        self.triggers.setCellWidget(r, 2, misfires)
        kinds.activated.connect(lambda _: self._commit_triggers())
        misfires.activated.connect(lambda _: self._commit_triggers())
        self.triggers.blockSignals(False)

    def _commit_triggers(self):
        triggers = []
        for r in range(self.triggers.rowCount()):
            kind = self.triggers.cellWidget(r, 0).currentText()
            value = self.triggers.item(r, 1).text().strip() if self.triggers.item(r, 1) else ""
            spec = {"cron" if kind == "cron" else "at": value}
            misfire = self.triggers.cellWidget(r, 2).currentText()
            if misfire != "run_once":
                spec["misfire"] = misfire
            triggers.append({"schedule": spec})
        self._set("triggers", triggers or None)

    def _show_next(self):
        problems, upcoming = [], []
        now = dt.datetime.now()
        for i, data in enumerate(self.doc.data.get("triggers") or []):
            try:
                t = Trigger(data).next_after(now)
                if t:
                    upcoming.append(t)
            except ValueError as e:
                problems.append(f"Schedule {i + 1}: {e}")
        text = "Next run: " + min(upcoming).strftime("%a %Y-%m-%d %H:%M") if upcoming else "No upcoming runs."
        if problems:
            text = "<span style='color:%s'>%s</span>" % (STATUS_COLORS["failed"], html.escape("; ".join(problems)))
        self.next_label.setText(text)

    def refresh(self):
        if self.doc is None:
            return
        self.name.setText(self.doc.data.get("name") or "")
        self.description.setText(self.doc.data.get("description") or "")
        self.calendar.clear()
        names = [""] + sorted(p.stem for p in self.home.calendars_dir.glob("*.yaml")) + ["US", "GB", "NYSE"]
        self.calendar.addItems(list(dict.fromkeys(names)))
        self.calendar.setCurrentText(self.doc.data.get("calendar") or "")
        self.timezone.setText(self.doc.data.get("timezone") or "")
        self.overlap.setCurrentText(self.doc.data.get("overlap") or "skip")
        self.scheduled.setEnabled(self.doc.path is not None)
        self.scheduled.setToolTip("" if self.doc.path else "Save the flow first")
        try:
            self.scheduled.setChecked(self.doc.path in self.home.scheduled_flows())
        except ValueError:
            self.scheduled.setChecked(False)
        self.triggers.setRowCount(0)
        for data in self.doc.data.get("triggers") or []:
            spec = (data or {}).get("schedule") or {}
            kind = "cron" if "cron" in spec else "once"
            self._add_trigger(kind, str(spec.get("cron", spec.get("at", ""))), spec.get("misfire", "run_once"))
        self._show_next()


# --- Log ----------------------------------------------------------------------------

LEVEL_COLORS = {"ERROR": STATUS_COLORS["failed"], "WARNING": STATUS_COLORS["running"],
                "RETRY": STATUS_COLORS["running"], "DONE": STATUS_COLORS["success"]}


class LogPanel(QWidget):
    filter_cleared = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 2, 6, 0)
        self.showing = QLabel("All blocks")
        self.clear_filter = QToolButton(text="Show all")
        bar.addWidget(self.showing)
        bar.addWidget(self.clear_filter)
        bar.addStretch()
        layout.addLayout(bar)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(MONO)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.text)
        self.entries, self.block = [], None
        self.clear_filter.clicked.connect(lambda: (self.set_filter(None), self.filter_cleared.emit()))
        self.clear_filter.hide()

    def set_entries(self, entries: list[dict]):
        self.entries = entries
        self._render()

    def append(self, entry: dict):
        self.entries.append(entry)
        if self.block is None or entry.get("block") == self.block:
            self.text.appendHtml(self._line(entry))

    def set_filter(self, block_id):
        self.block = block_id
        self.showing.setText(f"Showing <b>{html.escape(block_id)}</b>" if block_id else "All blocks")
        self.clear_filter.setVisible(block_id is not None)
        self._render()

    def _line(self, e: dict) -> str:
        level = e.get("level", "INFO")
        color = LEVEL_COLORS.get(level)
        block = f"[{html.escape(e['block'])}] " if e.get("block") else ""
        message = html.escape(e.get("message", "")).replace("\n", "<br>")
        level_html = f"<span style='color:{color}'>{level}</span>" if color else level
        return f"<span style='color:gray'>{e.get('ts', '')[11:19]}</span> {block}{level_html} {message}"

    def _render(self):
        self.text.clear()
        self.text.appendHtml("<br>".join(self._line(e) for e in self.entries
                                          if self.block is None or e.get("block") == self.block))


# --- Data preview -------------------------------------------------------------------

class FrameModel(QAbstractTableModel):
    def __init__(self, df: pl.DataFrame):
        super().__init__()
        self.df = df

    def rowCount(self, parent=QModelIndex()):
        return self.df.height

    def columnCount(self, parent=QModelIndex()):
        return self.df.width

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            value = self.df[index.row(), index.column()]
            return "" if value is None else str(value)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return f"{self.df.columns[section]}\n{self.df.dtypes[section]}"
        if role == Qt.DisplayRole:
            return str(section + 1)
        return None


class PreviewPanel(QWidget):
    PREVIEW_ROWS = 200

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 2, 6, 0)
        self.label = QLabel("Select a block that produced a table in the last run.")
        self.ports = QComboBox()
        self.ports.hide()
        bar.addWidget(self.label)
        bar.addWidget(self.ports)
        bar.addStretch()
        layout.addLayout(bar)
        self.view = QTableView()
        self.view.setFont(MONO)
        layout.addWidget(self.view)
        self.tables: dict[str, str] = {}
        self.ports.currentTextChanged.connect(self._load)

    def show_tables(self, block_id, tables: dict):
        self.tables = tables or {}
        self.ports.blockSignals(True)
        self.ports.clear()
        self.ports.addItems(list(self.tables))
        self.ports.blockSignals(False)
        self.ports.setVisible(len(self.tables) > 1)
        if not self.tables:
            self.view.setModel(None)
            self.label.setText(f"{block_id}: no table output in this run." if block_id else
                               "Select a block that produced a table in the last run.")
            return
        self.block_id = block_id
        self._load(next(iter(self.tables)))

    def _load(self, port):
        if not port:
            return
        try:
            lazy = pl.scan_parquet(self.tables[port])
            total = lazy.select(pl.len()).collect().item()
            df = lazy.head(self.PREVIEW_ROWS).collect()
        except Exception as e:
            self.view.setModel(None)
            self.label.setText(f"Cannot preview: {e} (old run folders are cleaned up after keep_runs runs)")
            return
        self.view.setModel(FrameModel(df))
        self.label.setText(f"{self.block_id}.{port}: {total:,} rows × {df.width} columns"
                           + (f" (showing first {self.PREVIEW_ROWS})" if total > self.PREVIEW_ROWS else ""))


# --- Run history --------------------------------------------------------------------

class HistoryPanel(QWidget):
    open_run = Signal(int)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Run", "Started", "Status", "Duration", "Parameters"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hint = QLabel("Double-click a run to show it on the canvas.")
        hint.setStyleSheet("color: palette(placeholder-text); padding: 2px 6px;")
        layout.addWidget(hint)
        layout.addWidget(self.table)
        self.table.cellDoubleClicked.connect(lambda row, _: self.open_run.emit(int(self.table.item(row, 0).text())))

    def show_runs(self, runs: list[dict]):
        self.table.setRowCount(0)
        for run in runs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            started = run["started"] or ""
            duration = ""
            if run["finished"] and run["started"]:
                seconds = (dt.datetime.fromisoformat(run["finished"]) - dt.datetime.fromisoformat(run["started"])).total_seconds()
                duration = f"{seconds:.1f} s" if seconds < 120 else f"{math.floor(seconds / 60)} min {seconds % 60:.0f} s"
            params = " ".join(f"{k}={v}" for k, v in yaml.safe_load(run["params"] or "{}").items())
            for col, text in enumerate([str(run["id"]), started.replace("T", " ")[:19], run["status"], duration, params]):
                item = QTableWidgetItem(text)
                if col == 2:
                    item.setIcon(status_icon(run["status"]))
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
