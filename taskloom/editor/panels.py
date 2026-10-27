"""Side and bottom panels of the editor window."""

from __future__ import annotations

import datetime as dt
import html
import math
import re
from pathlib import Path

import polars as pl
import yaml
from PySide6.QtCore import (QAbstractTableModel, QObject, QDate, QMimeData, QModelIndex, QPointF, QRectF, QSize, Qt, QTime,
                            QTimer, Signal)
from PySide6.QtGui import QColor, QDrag, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit, QDateTimeEdit,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTableView,
                               QTableWidget, QTableWidgetItem, QTimeEdit, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import params as P
from ..block import IDENTIFIER, RESERVED_OUTPUTS, fields, setting_label
from ..blocks.logic import ReusableBlock
from ..calendars import load_calendar
from ..config import kept_runs
from ..expr import functions
from ..flow import BlockSpec, Retry, block_config, parse_duration, schedules_of
from ..scheduler import counted_from
from .canvas import BLOCK_MIME, CATEGORY_COLORS, NOTE_SIZES, NOTE_STYLES, STATUS_COLORS, TEXT_ITEM, USER_COLOR

# A fixed-width font, so a table's columns line up. "monospace" names no font on Windows or macOS, and it is also what
# <pre> (e.g. a table's rows in a tooltip) asks for on Qt's offscreen platform (as in the proofs): name real ones.
QFont.insertSubstitutions("monospace", ["Consolas", "Menlo", "DejaVu Sans Mono", "Courier New"])
MONO = QFont("monospace")  # not the system's fixed font: this module is loaded before the application has one


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
    """The hover text for a block: what it does in plain words (and what its inputs expect, if it says)."""
    text = cls.description or "No description yet: add description = \"...\" to this block's class."
    inputs = f"<br><br><b>Its inputs:</b> {html.escape(cls.inputs_help)}" if cls.inputs_help else ""
    return (f"<p style='white-space:normal'><b>{html.escape(cls.title or cls.type_id)}</b><br>{html.escape(text)}"
            f"{inputs}</p>")


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


NOTES_COLOR = "#9aa5b1"


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
        self.show_blocks(registry)
        self.search.textChanged.connect(self._filter)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

    def show_blocks(self, registry):
        """Fill the tree with the registry's blocks (again, e.g. after a reusable block was saved: the search
        still applies)."""
        self.tree.clear()
        groups: dict[str, list] = {}
        for type_id, cls in registry.blocks.items():
            source = registry.sources.get(type_id)
            category = cls.category if source == "built-in" else "User Created"
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
        # Not blocks: things you put on the canvas to explain the flow. They do not run.
        folder = QTreeWidgetItem(["Notes  (1)"])
        folder.setIcon(0, _square_icon(NOTES_COLOR, 12))
        folder.setFlags(Qt.ItemIsEnabled)
        font = folder.font(0)
        font.setBold(True)
        folder.setFont(0, font)
        self.tree.addTopLevelItem(folder)
        item = QTreeWidgetItem(["Text"])
        item.setIcon(0, _square_icon(NOTES_COLOR))
        item.setData(0, Qt.UserRole, TEXT_ITEM)
        item.setToolTip(0, "<b>Text</b><br>A title or remark on the canvas, to explain the flow. It does not run."
                           "<br>Double-click it on the canvas to write in it; Properties has its style and font.")
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        folder.addChild(item)
        folder.setExpanded(True)
        self._filter(self.search.text())

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

class FlowTabsStatus(QObject):
    """Each open flow's last result (and, when scheduled, its next run) on its tab: an icon and a tooltip."""

    def __init__(self, tabs):
        super().__init__(tabs)
        self.tabs = tabs
        tabs.setIconSize(QSize(30, 14))
        self.scheduler_on = False  # the window says: scheduled flows run only while the scheduler service is on
        self._frame = 0
        self._timer = QTimer(self, interval=120)
        self._timer.timeout.connect(self._spin)
        self._timer.start()

    def update_tab(self, tab):
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        status, subtitle = tab.list_status()
        if tab.next_run:
            subtitle += (f" · next {tab.next_run:%Y-%m-%d %H:%M:%S}" if self.scheduler_on else
                         " · scheduler service off")
        if tab.scheduled and tab.doc.dirty:
            subtitle += "\nunsaved · the scheduler runs the saved version: save the flow to apply your changes"
        self.tabs.setTabToolTip(index, f"{tab.doc.path or 'not saved yet'}\n{subtitle}")
        self.tabs.setTabIcon(index, status_icon(status, self._frame, clock=tab.next_run is not None))
        self.tabs.tabBar().setTabTextColor(index, QColor(STATUS_COLORS["failed"]) if status == "failed"
                                           else self.tabs.palette().text().color())
        tab.shown_status = (status, tab.next_run is not None)

    def _spin(self):
        self._frame = (self._frame + 1) % 8
        for index in range(self.tabs.count()):
            status, clock = getattr(self.tabs.widget(index), "shown_status", (None, False))
            if status == "running":
                self.tabs.setTabIcon(index, status_icon("running", self._frame, clock=clock))


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
        self.doc = self.registry = self.block_id = self.note_id = None
        self._committing = False
        self._custom_when = None  # (flow, block) whose schedule you chose to write as a cron line
        self.refresh()

    def _finish_editing(self):
        # A field being edited saves when it loses focus: let it, while this still shows what it belongs
        # to (the next flow can have a block with the same name).
        focused = QApplication.focusWidget()
        if focused is not None and self.isAncestorOf(focused):
            focused.clearFocus()

    def show_block(self, doc, registry, block_id):
        shown = (self.doc, self.block_id)
        self._finish_editing()
        if (doc, block_id) == shown and self.block_id != block_id:
            block_id = self.block_id  # finishing renamed it: show it under its new name
        self.doc, self.registry, self.block_id, self.note_id = doc, registry, block_id, None
        self.refresh()

    def show_note(self, doc, note_id):
        """A text on the canvas: its text, style, emphasis and font."""
        self._finish_editing()
        self.doc, self.block_id, self.note_id = doc, None, note_id
        self.refresh()

    def on_doc_changed(self):
        if not self._committing:
            if self.doc is not None and self.block_id not in self.doc.blocks:
                self.block_id = None
            if self.doc is not None and self.note_id not in self.doc.notes:
                self.note_id = None
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
        old = self.widget()
        if old is not None:
            # Its fields may still report an edit (e.g. losing focus, which taking it out causes): it no
            # longer belongs to any block, and self.doc may already be another flow's.
            for child in old.findChildren(QWidget):
                child.blockSignals(True)
            self.takeWidget()
            old.deleteLater()  # not now: refresh may run inside a click on one of its buttons
        self.setWidget(body)
        if self.doc is not None and self.note_id is not None:
            self._note_form(layout)
            return
        if self.doc is None or self.block_id not in self.doc.blocks:  # (a name it no longer has: none)
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
        if issubclass(cls, ReusableBlock):  # it has no settings: what it does and what its inputs expect instead
            for heading, text in (("What it does", cls.description), ("What its inputs expect", cls.inputs_help)):
                if text:
                    layout.addWidget(QLabel(f"<b>{heading}</b>"))
                    layout.addWidget(QLabel(text, wordWrap=True, textFormat=Qt.PlainText))
        config = spec.get("config") or {}
        when = cls.type_id == "trigger.schedule"
        keeps = cls.type_id in ("trigger.manual", "trigger.schedule")
        if when:
            layout.addWidget(self._when_box(config))
        for key, f in cls.config.items():
            if when and key in ("cron", "every", "at") or keeps and key in ("keep", "keep_failed"):
                continue  # in the When box, or the Keep runs box
            widget = self._field_widget(key, f, config.get(key))
            label = QLabel(setting_label(key, f))
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
        if keeps:
            layout.addWidget(self._keep_box(config, cls.config))
        layout.addWidget(self._retry_box(spec))
        self._show_problems()

    # --- how a start block's runs are kept ---------------------------------------------------

    # Each choice, and what it writes in the flow file when you choose it (None: nothing, as in Settings).
    KEEP_CHOICES = {"As in Settings": None, "All of them": "all", "The last N": "last 100", "For a time": "90 days"}

    def _keep_box(self, config: dict, keep_fields: dict) -> QGroupBox:
        """Keep runs: as in Settings, all of them, the last N, or for a time (a number and days, months or
        years); and Always keep failed runs."""
        box = QGroupBox("Keep runs")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        try:
            kind, number = kept_runs(config["keep"]) if config.get("keep") is not None else ("settings", 0)
        except ValueError:  # written in the file by hand: the message above says so
            kind, number = "", 0
        choice = QComboBox()
        choice.addItems(list(self.KEEP_CHOICES))
        choice.setCurrentIndex({"settings": 0, "all": 1, "last": 2, "": -1}.get(kind, 3))
        choice.setToolTip(keep_fields["keep"].help)
        choice.currentTextChanged.connect(lambda text: (self._set("keep", self.KEEP_CHOICES.get(text)), self.refresh()))
        form.addRow(choice)
        if kind == "last":
            count = QSpinBox(minimum=1, maximum=1_000_000, value=number, keyboardTracking=False)
            count.valueChanged.connect(lambda n: self._set("keep", f"last {n}"))
            form.addRow("Runs", count)
        elif kind in ("days", "months", "years"):
            count = QSpinBox(minimum=1, maximum=100_000, value=number, keyboardTracking=False)
            unit = QComboBox()
            unit.addItems(["days", "months", "years"])
            unit.setCurrentText(kind)

            def commit():
                n, u = count.value(), unit.currentText()
                self._set("keep", f"{n} {u.removesuffix('s') if n == 1 else u}")

            count.valueChanged.connect(lambda _: commit())
            unit.currentTextChanged.connect(lambda _: commit())
            row = QWidget()
            parts = QHBoxLayout(row)
            parts.setContentsMargins(0, 0, 0, 0)
            parts.addWidget(count)
            parts.addWidget(unit)
            parts.addStretch()
            form.addRow("For", row)
        failed = QCheckBox(keep_fields["keep_failed"].label)
        failed.setChecked(bool(config.get("keep_failed")))
        failed.setToolTip(keep_fields["keep_failed"].help)
        failed.toggled.connect(lambda checked: self._set("keep_failed", checked or None))
        form.addRow(failed)
        return box

    # --- when a Schedule block runs ----------------------------------------------------------

    # Every few minutes or hours counts from when the scheduler service picks the schedule up (`every`); the
    # others are on the clock (cron lines).
    WHEN_KINDS = [("Every few minutes or hours", "every"), ("Every hour", "hourly"), ("Every day", "daily"),
                  ("Every weekday (Mon–Fri)", "weekdays"), ("Some days of the week", "weekly"),
                  ("Every month", "monthly"), ("Custom (a cron line)", "custom")]
    EVERY_CHOICES = ("1 minute", "2 minutes", "3 minutes", "4 minutes", "5 minutes", "6 minutes", "10 minutes",
                     "12 minutes", "15 minutes", "20 minutes", "30 minutes", "1 hour", "2 hours", "3 hours", "4 hours",
                     "6 hours", "8 hours", "12 hours")
    DEFAULT_PLANS = {"hourly": {"kind": "hourly", "minute": 0},
                     "daily": {"kind": "daily", "hour": 7, "minute": 0},
                     "weekdays": {"kind": "weekdays", "hour": 7, "minute": 0},
                     "weekly": {"kind": "weekly", "days": [1], "hour": 7, "minute": 0},
                     "monthly": {"kind": "monthly", "day": 1, "hour": 7, "minute": 0}}

    def _set_when(self, cron=None, every=None, at=None):
        """Repeating (cron or every) or one time (at): setting one clears the others, in one step."""
        if self.doc is None or self.block_id not in self.doc.blocks:
            return
        self._commit(lambda: self.doc.set_block_values(self.block_id, {"cron": cron, "every": every, "at": at}))

    def _when_box(self, config: dict) -> QGroupBox:
        """Repeating or one time; for repeating, how often (or a cron line); and the next runs."""
        box = QGroupBox("When")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        cron, every, at = (str(config.get(k) or "") for k in ("cron", "every", "at"))
        mode = QComboBox()
        mode.addItem("Repeating", "cron")
        mode.addItem("One time", "at")
        mode.setCurrentIndex(1 if at and not (cron or every) else 0)
        form.addRow("Runs", mode)
        preview = QLabel()
        preview.setWordWrap(True)
        preview.setTextInteractionFlags(Qt.TextSelectableByMouse)

        def show_preview(timing: dict):
            from ..schedule import Trigger, describe_cron
            timing = {k: v for k, v in timing.items() if v}
            try:
                trigger = Trigger({"schedule": timing})
            except ValueError as e:
                preview.setText("<span style='color: gray'>Not set yet: choose when it runs.</span>" if not timing
                                else f"<span style='color: {STATUS_COLORS['failed']}'>{html.escape(str(e))}</span>")
                return
            if trigger.every:  # not on the clock: its times are known once the scheduler service has seen it
                n = trigger.timing.text
                preview.setText(f"<b>Every {html.escape(n.removeprefix('1 '))}</b><br>From when the scheduler "
                                f"service picks it up (when you save the flow): its first run is then, and the next "
                                f"ones every {html.escape(n)} after it.")
                return
            cron_text = timing.get("cron")
            upcoming, t = [], dt.datetime.now()
            while len(upcoming) < 3 and (t := trigger.next_after(t)) is not None:
                upcoming.append(t)
            words = describe_cron(cron_text) if cron_text else f"Once, on {trigger.timing.when:%A %d %B %Y at %H:%M}"
            preview.setText(f"<b>{html.escape(words)}</b><br>" + ("Next: " + ", ".join(
                f"{t:%a %d %b %H:%M}" for t in upcoming) if upcoming else "This time has passed."))

        def switch(_):
            if mode.currentData() == "at" and not at:
                soon = (dt.datetime.now() + dt.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
                self._set_when(at=f"{soon:%Y-%m-%d %H:%M}")
            elif mode.currentData() == "cron" and not (cron or every):
                self._set_when()  # repeating, not chosen yet
            self.refresh()

        mode.activated.connect(switch)
        if mode.currentData() == "at":
            self._at_rows(form, at, show_preview)
        else:
            self._cron_rows(form, cron, every, show_preview)
        form.addRow(preview)
        show_preview({"at": at} if mode.currentData() == "at" else {"cron": cron, "every": every})
        return box

    def _at_rows(self, form, at: str, show_preview):
        edit = QDateTimeEdit(calendarPopup=True)
        edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        try:
            when = dt.datetime.fromisoformat(str(at))
        except ValueError:
            when = dt.datetime.now().replace(second=0, microsecond=0)
        edit.setDateTime(when)

        def commit():
            text = edit.dateTime().toPython().strftime("%Y-%m-%d %H:%M")
            self._set_when(at=text)
            show_preview({"at": text})

        edit.editingFinished.connect(commit)
        edit.calendarWidget().clicked.connect(lambda _: commit())
        form.addRow("On", edit)

    def _cron_rows(self, form, cron: str, every: str, show_preview):
        from ..schedule import DAY_WORDS, cron_plan, plan_cron

        plan = cron_plan(cron) if cron else None
        kind_box = QComboBox()
        if not (cron or every):
            kind_box.addItem("Choose…", "")
        for label, kind in self.WHEN_KINDS:
            kind_box.addItem(label, kind)
        custom = self._custom_when == (id(self.doc), self.block_id)
        current = "every" if every else "" if not cron else "custom" if custom or not plan else plan["kind"]
        kind_box.setCurrentIndex(kind_box.findData(current))
        form.addRow("How often", kind_box)

        def choose(_):
            kind = kind_box.currentData()
            self._custom_when = (id(self.doc), self.block_id) if kind == "custom" else None
            if kind == "custom":
                self._set_when(cron=cron or "0 7 * * MON-FRI")  # the line so far, to change as you like
            elif kind == "every":
                self._set_when(every="15 minutes")
            elif kind:
                self._set_when(cron=plan_cron(self.DEFAULT_PLANS[kind]))
            self.refresh()

        kind_box.activated.connect(choose)
        if not current:
            return
        if current == "every":
            box = QComboBox()
            box.addItems(self.EVERY_CHOICES)
            if box.findText(every) < 0:  # written in the file by hand
                box.addItem(every)
            box.setCurrentText(every)
            box.activated.connect(lambda _: (self._set_when(every=box.currentText()),
                                             show_preview({"every": box.currentText()})))
            form.addRow("Every", box)
            return
        if current == "custom":
            line = QLineEdit(cron)
            line.setToolTip("minute hour day month weekday, e.g. 0 7-17/2 * * MON-FRI = every two hours from 7:00 "
                            "to 17:00 on weekdays; * means every, */N every N, a-b a range, a,b a list")
            line.editingFinished.connect(lambda: (self._set_when(cron=line.text().strip() or None),
                                                  show_preview({"cron": line.text().strip()})))
            form.addRow("Cron line", line)
            hint = QLabel("minute · hour · day · month · weekday")
            hint.setStyleSheet("color: gray;")
            form.addRow("", hint)
            return
        plan = dict(plan)

        def commit():
            text = plan_cron(plan)
            self._set_when(cron=text)
            show_preview({"cron": text})

        if current == "hourly":
            minute = QSpinBox(minimum=0, maximum=59, value=plan["minute"])
            minute.editingFinished.connect(lambda: (plan.update(minute=minute.value()), commit()))
            form.addRow("At minute", minute)
            return
        if current == "weekly":
            days = QWidget()
            row = QHBoxLayout(days)
            row.setContentsMargins(0, 0, 0, 0)
            for d in (1, 2, 3, 4, 5, 6, 0):
                box = QCheckBox(DAY_WORDS[d])
                box.setChecked(d in plan["days"])

                def toggled(checked, d=d, box=box):
                    chosen = (set(plan["days"]) | {d}) if checked else (set(plan["days"]) - {d})
                    if not chosen:  # at least one day
                        box.setChecked(True)
                        return
                    plan.update(days=sorted(chosen))
                    commit()

                box.toggled.connect(toggled)
                row.addWidget(box)
            form.addRow("On", days)
        if current == "monthly":
            day = QSpinBox(minimum=1, maximum=31, value=plan["day"])
            day.setToolTip("Days 29 to 31 are skipped in months that do not have them")
            day.editingFinished.connect(lambda: (plan.update(day=day.value()), commit()))
            form.addRow("On day", day)
        time_edit = QTimeEdit(QTime(plan["hour"], plan["minute"]))
        time_edit.setDisplayFormat("HH:mm")
        time_edit.editingFinished.connect(lambda: (plan.update(hour=time_edit.time().hour(),
                                                               minute=time_edit.time().minute()), commit()))
        form.addRow("At", time_edit)

    def _rename(self, new):
        old = self.block_id
        self.block_id = new
        try:
            self.doc.rename_block(old, new)
        except ValueError:
            self.block_id = old
            raise

    def _set_names(self, key, f, value):
        """A list of input or output names: renaming or removing one updates its connections too."""
        if self.doc is None or self.block_id not in self.doc.blocks:
            return
        old = self.doc.blocks[self.block_id].get("config", {}).get(key)
        old, new = (old if old is not None else f.default or []), (value if value is not None else f.default or [])
        if old == new:
            return self._set(key, value)
        f.coerce(new)  # a bad name is reported before anything changes
        outputs = f.reserved == RESERVED_OUTPUTS
        self._commit(lambda: self.doc.set_port_names(self.block_id, key, value, list(old), list(new), outputs))

    def _set(self, key, value, section="config"):
        if self.doc is None or self.block_id not in self.doc.blocks:
            return  # the block was deleted while its field was being edited
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
            return self._connection_widget(key, f, value)
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
                value = convert(w.text())
                if value is not None:
                    f.coerce(value)  # a value it cannot take is told, naming this setting, before anything changes
                if isinstance(f, fields.Names):
                    self._set_names(key, f, value)
                else:
                    self._set(key, value)
            except (ValueError, yaml.YAMLError) as e:  # (YAML: a plugin field type's value, not finished)
                self.error.setText(f"{setting_label(key, f)}: {e}")
                self.error.show()

        w.editingFinished.connect(commit)
        if isinstance(f, fields.Path):
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            browse = QToolButton(text="…")
            browse.clicked.connect(lambda: self._browse(w, commit, f.files))
            row.addWidget(w)
            row.addWidget(browse)
            return box
        return w

    def _connection_widget(self, key, f, value):
        """A list of the connections of the right kind, with buttons to edit one or create one."""
        try:
            names = [n for n, c in self.home.connections().items() if c.get("kind") == f.kind]
        except ValueError:
            names = []
        combo = QComboBox()
        combo.addItem("This computer" if isinstance(f, fields.Server) else
                      "" if names else "(no connections yet: click New…)", "")
        for name in names:
            combo.addItem(name, name)
        if value and value not in names:
            combo.addItem(f"{value} (missing)", value)
        combo.setCurrentIndex(max(combo.findData(value or ""), 0))
        combo.activated.connect(lambda _: self._set(key, combo.currentData() or None))
        edit_button, new_button = QToolButton(text="Edit…"), QToolButton(text="New…")
        edit_button.setEnabled(bool(value))
        edit_button.clicked.connect(lambda: self._open_connections(key, select=combo.currentData()))
        new_button.clicked.connect(lambda: self._open_connections(key, new_kind=f.kind))
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(combo, 1)
        row.addWidget(edit_button)
        row.addWidget(new_button)
        return box

    def _open_connections(self, key, select=None, new_kind=None):
        from .dialogs import ConnectionsDialog, open_dialog

        dialog = open_dialog(self.window(), ConnectionsDialog, self.home, select=select, new_kind=new_kind)
        if dialog and new_kind and dialog.created in dialog.conns:
            self._set(key, dialog.created)  # use the connection just created
        self.refresh()

    def _browse(self, line_edit, commit, files):
        path, chosen = QFileDialog.getSaveFileName(self, "Choose a file", line_edit.text(), ";;".join(files),
                                                   options=QFileDialog.DontConfirmOverwrite)
        if path:
            extension = re.search(r"\(\*(\.\w+)\)", chosen)
            if extension and not Path(path).suffix:  # a name typed without one gets the chosen kind's
                path += extension.group(1)
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

    def _note_form(self, layout):
        note = self.doc.notes[self.note_id]
        layout.addWidget(QLabel("<b>Text</b><br><span style='color: gray'>on the canvas, for titles and remarks; "
                                "it does not run</span>"))
        self.error = QLabel()
        self.error.hide()
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        text = CodeEdit(str(note.get("text", "")))
        text.setFont(QApplication.font())
        text.editing_finished.connect(lambda: text.toPlainText().strip() and self._set_note("text", text.toPlainText()))
        form.addRow("Text", text)
        style = QComboBox()
        for name in ("title", "heading", "normal", "small"):
            style.addItem(name.capitalize(), name)
        style.setCurrentIndex(style.findData(note.get("style") or "normal"))
        # A style sets its size: a size you chose (or dragged) gives way to it.
        style.activated.connect(lambda _: (self._set_note_values(
            {"style": None if style.currentData() == "normal" else style.currentData(), "size": None}),
            self.refresh()))
        form.addRow("Style", style)
        style_size = float(NOTE_STYLES.get(note.get("style") or "normal", 11))
        current = float(note.get("size") or style_size)
        size = QDoubleSpinBox(minimum=NOTE_SIZES[0], maximum=NOTE_SIZES[1], decimals=1, suffix=" pt")
        size.setValue(current)
        size.setToolTip("Or drag the corner of the selected text on the canvas")
        # A change only (leaving the field is not one); the style's own size means no size of its own.
        size.editingFinished.connect(lambda: size.value() != current and self._set_note(
            "size", None if size.value() == style_size else size.value()))
        form.addRow("Size", size)
        for key in ("bold", "italic", "underline"):
            box = QCheckBox(key.capitalize())
            box.setChecked(bool(note.get(key)))
            box.toggled.connect(lambda checked, k=key: self._set_note(k, checked))
            form.addRow("", box)
        font = QComboBox()
        font.addItem("(the usual font)", "")
        for family in QFontDatabase.families():  # the fonts installed on this computer
            font.addItem(family, family)
        font.setCurrentIndex(max(font.findData(note.get("font") or ""), 0))
        font.activated.connect(lambda _: self._set_note("font", font.currentData() or None))
        form.addRow("Font", font)
        layout.addLayout(form)

    def _set_note(self, key, value):
        self._set_note_values({key: value})

    def _set_note_values(self, values: dict):
        if self.doc is None or self.note_id not in self.doc.notes:
            return
        self._commit(lambda: self.doc.set_note_values(self.note_id, values))

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
        # A name that is not allowed is kept, flagged (as the runner flags it), for you to put right. The name of
        # another parameter is refused: the one renamed keeps its name (the rows are the flow's params, in order).
        before, params, problems = list(self.doc.data["params"]), {}, []
        rows = list(self._rows())
        names = [name for name, *_ in rows]
        for r, (name, type_, kind, value) in enumerate(rows):
            if names.count(name) > 1 and r < len(before) and name != before[r]:
                problems.append(f"'{name}' is the name of another parameter")
                name = names[r] = before[r]
                self.table.blockSignals(True)
                self.table.item(r, 0).setText(name)
                self.table.blockSignals(False)
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
        values, notes = {}, [*problems, *(f"'{name}' is not a valid, unique name (letters, digits and _)"
                                          for name, *_ in self._rows() if not IDENTIFIER.match(name))]
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
    """Name, calendar and time zone of the flow, and what happens when scheduled runs overlap."""

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
        self.overlap = QComboBox()
        for value, label in (("skip", "skip the new run"), ("queue", "run it after the current one"),
                             ("allow", "run both at the same time")):
            self.overlap.addItem(label, value)
        self.form.addRow("Name", self.name)
        self.form.addRow("Description", self.description)
        self.form.addRow("Calendar", self.calendar)
        self.form.addRow("Time zone", self.timezone)
        self.form.addRow("If a scheduled run starts while the last one is still going", self.overlap)
        layout.addLayout(self.form)
        self.schedule = QLabel()
        self.schedule.setWordWrap(True)
        layout.addWidget(self.schedule)
        layout.addStretch()
        self.name.editingFinished.connect(lambda: self._set("name", self.name.text().strip()))
        self.description.editingFinished.connect(lambda: self._set("description", self.description.text().strip()))
        self.calendar.lineEdit().editingFinished.connect(lambda: self._set("calendar", self.calendar.currentText().strip()))
        self.calendar.activated.connect(lambda _: self._set("calendar", self.calendar.currentText().strip()))
        self.timezone.editingFinished.connect(lambda: self._set("timezone", self.timezone.text().strip()))
        self.overlap.activated.connect(lambda _: self._set("overlap", None if self.overlap.currentData() == "skip"
                                                           else self.overlap.currentData()))

    def _set(self, key, value):
        if self.doc is not None:
            self.doc.set_flow_value(key, value)

    def show_doc(self, doc):
        self.doc = doc
        self.refresh()

    def _show_schedule(self):
        triggers, problems = schedules_of(self.doc.data)
        now = dt.datetime.now()
        upcoming = [t for t in (tr.next_after(now, start) for tr, start in
                                zip(triggers, counted_from(self.home, self.doc.path, triggers))) if t]
        if problems:
            text = "<span style='color:%s'>%s</span>" % (STATUS_COLORS["failed"], html.escape("; ".join(problems)))
        elif upcoming:
            text = (f"Next scheduled run: <b>{min(upcoming):%a %Y-%m-%d %H:%M:%S}</b> (when the scheduler is on). "
                    "Change it in the Schedule block.")
        elif any(tr.every for tr in triggers):
            text = ("Its schedule starts when the scheduler service picks it up (when you save the flow): its first "
                    "run is then. Change it in the Schedule block.")
        else:
            text = ("This flow runs only when you press Run. To run it by itself at set times, add a "
                    "<b>Schedule</b> block (in Start) and connect it to the first block.")
        self.schedule.setText(text)

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
        self.overlap.setCurrentIndex(max(self.overlap.findData(self.doc.data.get("overlap") or "skip"), 0))
        self._show_schedule()


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
        if self._shown(entry):
            self.text.appendHtml(self._line(entry))

    def _shown(self, e: dict) -> bool:
        # A run's start and end lines stay visible with a block filter: they split the runs apart.
        return self.block is None or e.get("block") == self.block or e.get("header", False)

    def set_filter(self, block_id):
        self.block = block_id
        self.showing.setText(f"Showing <b>{html.escape(block_id)}</b>" if block_id else "All blocks")
        self.clear_filter.setVisible(block_id is not None)
        self._render()

    def _line(self, e: dict) -> str:
        level = e.get("level", "INFO")
        color = LEVEL_COLORS.get(level)
        block = f"[{html.escape(e['block'])}] " if e.get("block") else ""
        # Keep spaces as they are: reports such as Python tracebacks are laid out in columns.
        message = html.escape(e.get("message", "")).replace(" ", "&nbsp;").replace("\n", "<br>")
        level_html = f"<span style='color:{color}'>{level}</span>" if color else level
        line = f"<span style='color:gray'>{e.get('ts', '')[11:19]}</span> {block}{level_html} {message}"
        return f"<b>{line}</b>" if e.get("header") else line

    def _render(self):
        self.text.clear()
        self.text.appendHtml("<br>".join(self._line(e) for e in self.entries if self._shown(e)))


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
            if isinstance(value, pl.Series):  # a list cell
                value = value.to_list()
            return "" if value is None else str(value)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return f"{self.df.columns[section]}\n{self.df.dtypes[section]}"
        if role == Qt.DisplayRole:
            return str(section + 1)
        return None


class PreviewPanel(QWidget):
    """What the selected block produced in the run on screen: tables as a grid, anything else as text."""

    PREVIEW_ROWS = 200
    HINT = "Select a block to see what it produced in the last run (run the flow first)."

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 2, 6, 0)
        self.label = QLabel(self.HINT)
        self.ports = QComboBox()
        self.ports.hide()
        bar.addWidget(self.label)
        bar.addWidget(self.ports)
        bar.addStretch()
        layout.addLayout(bar)
        self.view = QTableView()
        self.view.setFont(MONO)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(MONO)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.view)
        layout.addWidget(self.text)
        self.text.hide()
        self.tables: dict[str, str] = {}
        self.previews: dict[str, str] = {}
        self.block_id = None
        self.ports.currentTextChanged.connect(self._load)

    def show_outputs(self, block_id, info: dict | None):
        info = info or {}
        self.block_id = block_id
        self.tables, self.previews = info.get("tables") or {}, info.get("previews") or {}
        names = list(self.tables) + [p for p in self.previews if p not in self.tables]
        self.ports.blockSignals(True)
        self.ports.clear()
        self.ports.addItems(names)
        self.ports.blockSignals(False)
        self.ports.setVisible(len(names) > 1)
        if not names:
            self.view.setModel(None)
            self.text.hide()
            self.view.show()
            status = info.get("status")
            self.label.setText(self.HINT if not block_id or not status else
                               f"{block_id}: {status}, no output" + (f" ({info['error']})" if info.get("error") else ""))
            return
        self._load(names[0])

    def _load(self, port):
        if not port:
            return
        if port not in self.tables:
            self.view.hide()
            self.text.show()
            self.text.setPlainText(self.previews[port])
            self.label.setText(f"{self.block_id}.{port}:")
            return
        self.text.hide()
        self.view.show()
        try:
            lazy = pl.scan_parquet(self.tables[port])
            total = lazy.select(pl.len()).collect().item()
            df = lazy.head(self.PREVIEW_ROWS).collect()
        except Exception as e:
            self.view.setModel(None)
            self.label.setText(f"Cannot preview: {e} (runs are cleaned up as Keep runs or keep_runs_for says)")
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
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Run", "Started", "Started by", "Status", "Duration", "Parameters"])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
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
            for col, text in enumerate([str(run["id"]), started.replace("T", " ")[:19], run["started_by"] or "",
                                        run["status"], duration, params]):
                item = QTableWidgetItem(text)
                if col == 3:
                    item.setIcon(status_icon(run["status"]))
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
