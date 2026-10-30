"""Side and bottom panels of the editor window."""

from __future__ import annotations

import datetime as dt
import html
import keyword
import math
import os
import re
from collections import OrderedDict
from functools import partial
from pathlib import Path

import polars as pl
import yaml
from markdown_it import MarkdownIt
from PySide6.QtCore import (QAbstractTableModel, QEvent, QObject, QDate, QMimeData, QModelIndex, QPoint, QPointF,
                            QRectF, QSize, QSizeF, Qt, QTime, QTimer, Signal)
from PySide6.QtGui import (QColor, QDrag, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QIcon, QImage,
                           QImageIOHandler, QImageReader, QPainter, QPalette, QPen, QPixmap, QSyntaxHighlighter,
                           QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextDocumentFragment,
                           QTextFormat, QTextFrameFormat, QTextImageFormat)
from PySide6.QtGui import Qt as GuiQt  # (Qt's functions for text, e.g. mightBeRichText)
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QAbstractSpinBox, QApplication, QCheckBox,
                               QComboBox, QDateEdit, QDateTimeEdit, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QSpinBox, QTableView, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QTimeEdit, QToolButton, QToolTip, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import params as P
from ..block import IDENTIFIER, RESERVED_OUTPUTS, fields, setting_label
from ..blocks.logic import ReusableBlock
from ..calendars import load_calendar
from ..config import kept_runs
from ..expr import functions
from ..flow import BlockSpec, Retry, block_config, parse_duration, schedules_of
from ..report import MAX_IMAGE_WIDTH
from ..scheduler import counted_from
from . import docs
from .canvas import (BLOCK_MIME, CATEGORY_COLORS, NOTE_SIZES, NOTE_STYLES, STATUS_COLORS, TEXT_ITEM, USER_COLOR,
                     is_dark)

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


def _write(box: QPlainTextEdit, before: str, inner: str, after: str):
    """Replace the box's selection by before + inner + after as one undo step, then select inner (empty: the cursor
    goes between)."""
    cursor = box.textCursor()
    cursor.beginEditBlock()
    cursor.insertText(before)
    start = cursor.position()
    cursor.insertText(inner)
    end = cursor.position()
    cursor.insertText(after)
    cursor.endEditBlock()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    box.setTextCursor(cursor)


def _selected(box: QPlainTextEdit) -> str:
    return box.textCursor().selectedText().replace("\u2029", "\n")  # (Qt separates the lines of a selection so)


def _wrap(box: QPlainTextEdit, tag: str):
    _write(box, f"<{tag}>", _selected(box), f"</{tag}>")


def _list(box: QPlainTextEdit, tag: str):
    """Each line the selection touches becomes an item of a <ul> or <ol>; nothing selected: one empty item."""
    cursor = box.textCursor()
    if not cursor.hasSelection():
        return _write(box, f"<{tag}>\n<li>", "", f"</li>\n</{tag}>")
    first, last = box.document().findBlock(cursor.selectionStart()), box.document().findBlock(cursor.selectionEnd())
    cursor.setPosition(first.position())
    cursor.setPosition(last.position() + last.length() - 1, QTextCursor.KeepAnchor)
    box.setTextCursor(cursor)
    _write(box, f"<{tag}>\n", "\n".join(f"<li>{line}</li>" for line in _selected(box).split("\n")), f"\n</{tag}>")


def _link(box: QPlainTextEdit):
    """Ask for the address; the selection (nothing selected: the address) becomes the link."""
    box.blockSignals(True)  # the question takes the focus: no saving yet, it would rebuild Properties and this box
    try:
        address, ok = QInputDialog.getText(box, "Link", "Link address:", text="https://")
    finally:
        box.blockSignals(False)
    if ok and address.strip():
        href = html.escape(address.strip())
        _write(box, f'<a href="{href}">', _selected(box) or href, "</a>")


def _new_line(box: QPlainTextEdit):
    cursor = box.textCursor()
    cursor.clearSelection()
    box.setTextCursor(cursor)
    _write(box, "<br>\n", "", "")


def _with_html_buttons(box: QPlainTextEdit) -> QWidget:
    """The box under text-labelled buttons that write Outlook-safe tags around its selection. ("Cursive" is italics:
    Outlook shows a font it does not have in Times New Roman.)"""
    widget = QWidget()
    # A click on a button keeps the focus in the box: leaving it would save, rebuilding Properties under the click.
    widget.setFocusPolicy(Qt.ClickFocus)
    widget.setFocusProxy(box)
    row = QHBoxLayout()
    for label, tip, act in (
            ("B", "Bold <b>", lambda: _wrap(box, "b")),
            ("I", "Italic (cursive) <i>", lambda: _wrap(box, "i")),
            ("U", "Underline <u>", lambda: _wrap(box, "u")),
            ("Heading", "Heading <h2>", lambda: _wrap(box, "h2")),
            ("• List", "Bulleted list <ul>: each selected line an item <li>", lambda: _list(box, "ul")),
            ("1. List", "Numbered list <ol>: each selected line an item <li>", lambda: _list(box, "ol")),
            ("Link…", "Link <a href>", lambda: _link(box)),
            ("New line", "New line <br> (Enter alone makes no new line in the email)", lambda: _new_line(box))):
        button = QToolButton(text=label)
        button.setToolTip(html.escape(tip))  # (shown as written, not read as HTML)
        font = button.font()
        font.setBold(label == "B")
        font.setItalic(label == "I")
        font.setUnderline(label == "U")
        button.setFont(font)
        button.clicked.connect(act)
        row.addWidget(button)
    row.addStretch()
    column = QVBoxLayout(widget)
    column.setContentsMargins(0, 0, 0, 0)
    column.addLayout(row)
    column.addWidget(box)
    return widget


def _tokens(comment: str, long: str, string: str, keywords, flags=0) -> re.Pattern:
    """What a line of code is made of, read from left to right; `long` starts a string or comment that may end on a
    later line. A word is read whole, so a name that starts like a keyword is not one."""
    return re.compile("|".join([f"(?P<comment>{comment})", f"(?P<long>{long})", f"(?P<string>{string})",
                                rf"(?P<keyword>\b(?:{'|'.join(keywords)})\b)",
                                r"(?P<number>\b\d[\d_]*(?:\.\d*)?(?:[eE][+-]?\d+)?)", r"(?P<word>\w+)"]), flags)


PYTHON_TOKENS = _tokens("#.*", "[rRbBfFuU]{0,2}(?:'''|\"\"\")",
                        r"""[rRbBfFuU]{0,2}(?:'(?:\\.|[^'\\])*'?|"(?:\\.|[^"\\])*"?)""", keyword.kwlist)
SQL_TOKENS = _tokens("--.*", r"/\*", "'(?:[^']|'')*'?", """select from where and or not in is null as on join left
    right inner outer full cross using group by order having limit offset distinct union all except intersect case when
    then else end between like ilike exists insert into values update set delete create replace table view drop alter
    with asc desc true false cast over partition qualify""".split(), re.IGNORECASE)
LONG_ENDS = ["'''", '"""', "*/"]  # how a string or comment over several lines ends (its index + 1: the line's state)
# Keyword, string, comment and number colours (as in JetBrains' light and dark themes), on a light and a dark background.
CODE_COLORS = {"keyword": ("#0033b3", "#cf8e6d"), "string": ("#067d17", "#6aab73"), "comment": ("#808080", "#7a7e85"),
               "number": ("#1750eb", "#2aacb8")}


class CodeHighlighter(QSyntaxHighlighter):
    """Colours a Python or SQL code box's keywords, strings, comments and numbers: only on the screen."""

    def __init__(self, document, language: str):
        super().__init__(document)
        self.tokens = PYTHON_TOKENS if language == "python" else SQL_TOKENS
        self.long = "string" if language == "python" else "comment"
        dark = is_dark()  # the theme now, as the box is made
        self.formats = {}
        for kind, (light, on_dark) in CODE_COLORS.items():
            self.formats[kind] = QTextCharFormat()
            self.formats[kind].setForeground(QColor(on_dark if dark else light))

    def highlightBlock(self, text):
        self.setCurrentBlockState(0)
        position = 0
        if self.previousBlockState() > 0:  # in a string or comment that started on an earlier line
            position = self._long(text, 0, 0, LONG_ENDS[self.previousBlockState() - 1])
        while (token := self.tokens.search(text, position)) is not None:
            if token.lastgroup == "long":
                start = token.group().lstrip("rRbBfFuU")
                position = self._long(text, token.start(), token.end(), "*/" if start == "/*" else start)
            else:
                if token.lastgroup != "word":
                    self.setFormat(token.start(), token.end() - token.start(), self.formats[token.lastgroup])
                position = token.end()

    def _long(self, text: str, start: int, after: int, end: str) -> int:
        """Colour from `start` to `end` (looked for from `after`), or to the end of the line if it ends on a later one;
        where to read on."""
        found = text.find(end, after)
        stop = len(text) if found < 0 else found + len(end)
        self.setFormat(start, stop - start, self.formats[self.long])
        if found < 0:
            self.setCurrentBlockState(LONG_ENDS.index(end) + 1)
        return stop


class _LineNumbers(QWidget):
    def __init__(self, editor: CodeEditor):
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event):
        self.editor.paint_line_numbers(self, event.rect())


class CodeEditor(CodeEdit):
    """A Python or SQL code box: coloured, with line numbers on the left, long lines not wrapped unless chosen, and
    Enter, Tab, Shift+Tab and Backspace that keep and change the indentation as code editors do."""

    def __init__(self, text: str, language: str, wrap: bool):
        super().__init__(text)
        self.language = language
        self.set_wrap(wrap)
        self.highlighter = CodeHighlighter(self.document(), language)
        self.numbers = _LineNumbers(self)
        self.blockCountChanged.connect(self._fit_numbers)
        self.updateRequest.connect(lambda *_: self.numbers.update())  # (scrolled or changed: all of it)
        self._fit_numbers()

    def set_wrap(self, wrap: bool):
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth if wrap else QPlainTextEdit.NoWrap)

    def _fit_numbers(self):
        width = 8 + self.fontMetrics().horizontalAdvance("9") * len(str(self.blockCount()))
        self.setViewportMargins(width, 0, 0, 0)
        rect = self.contentsRect()
        self.numbers.setGeometry(rect.left(), rect.top(), width, rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_numbers()

    def paint_line_numbers(self, widget: QWidget, rect):
        """Each shown line's number (from 1, as in error messages), on its row."""
        painter = QPainter(widget)
        painter.fillRect(rect, self.palette().base())
        painter.setPen(self.palette().placeholderText().color())
        block = self.firstVisibleBlock()
        while block.isValid():
            top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
            if top > rect.bottom():
                break
            painter.drawText(QRectF(0, top, widget.width() - 4, self.fontMetrics().height()), Qt.AlignRight,
                             str(block.blockNumber() + 1))
            block = block.next()

    def keyPressEvent(self, event):
        cursor = self.textCursor()
        plain = not event.modifiers() & ~Qt.KeypadModifier
        doc = self.document()
        first, last = doc.findBlock(cursor.selectionStart()), doc.findBlock(cursor.selectionEnd())
        before = first.text()[:cursor.selectionStart() - first.position()]  # the line up to what is typed over
        if last != first and cursor.selectionEnd() == last.position():
            last = last.previous()  # (a selection of whole lines ends at the start of the next one)
        lines = [first]
        while lines[-1] != last:
            lines.append(lines[-1].next())
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and plain:
            indent = before[:len(before) - len(before.lstrip())]
            if self.language == "python" and _without_comment(before).rstrip().endswith(":"):
                indent += "    "
            cursor.insertText("\n" + indent)
        elif event.key() == Qt.Key_Tab and plain and len(lines) == 1:
            cursor.insertText(" " * (4 - len(before.expandtabs(4)) % 4))  # (a tab shows up to a multiple of 4)
        elif event.key() == Qt.Key_Tab and plain:
            self._indent(lines, 4)
        elif event.key() == Qt.Key_Backtab:
            self._indent(lines, -4)
        elif event.key() == Qt.Key_Backspace and plain and not cursor.hasSelection() and before and not before.strip(" "):
            cursor.setPosition(cursor.position() - (len(before) - 1) % 4 - 1, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()  # back to the previous multiple of 4
        else:
            return super().keyPressEvent(event)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def _indent(self, lines, by: int):
        """Add `by` spaces at the start of each line, or (negative) remove up to that many."""
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()  # one step for Undo
        for line in lines:
            cursor.setPosition(line.position())
            if by > 0:
                cursor.insertText(" " * by)
            else:
                spaces = len(line.text()) - len(line.text().lstrip(" "))
                cursor.setPosition(line.position() + min(-by, spaces), QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
        cursor.endEditBlock()


def _without_comment(line: str) -> str:
    """A line of Python without its # comment (a '#' in a string starts none)."""
    for token in PYTHON_TOKENS.finditer(line):
        if token.lastgroup == "comment":
            return line[:token.start()]
    return line


def _as_written(tip: str) -> str:
    """A tooltip that shows `tip` as written: Qt shows a text that looks like HTML (e.g. one saying <br>) as HTML."""
    return html.escape(tip, quote=False) if GuiQt.mightBeRichText(tip) else tip


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


class HelpCard(QLabel):
    """What resting the mouse on a row of Properties shows, in place of a tooltip: the row's label in bold, what it is
    for, its examples (or its choices, or the connections it takes) and Learn more ›. One card for all the rows; it
    stays while the mouse is on the row or on the card, so the link can be clicked, and never takes the focus."""

    learn_more = Signal(str)  # the documentation page and anchor of the row

    def __init__(self, parent):
        super().__init__(parent, Qt.ToolTip)  # a window of its own that is never activated (as a tooltip)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setTextFormat(Qt.RichText)
        self.setWordWrap(True)
        self.setMargin(6)
        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        self.setBackgroundRole(QPalette.ToolTipBase)
        self.setForegroundRole(QPalette.ToolTipText)
        self.setAutoFillBackground(True)
        self.linkActivated.connect(self._learn_more)
        self.row, self._on_card = None, False
        self._leaving = QTimer(self, singleShot=True, interval=300)  # time to move the mouse onto the card
        self._leaving.timeout.connect(self._left)
        QGuiApplication.instance().applicationStateChanged.connect(self._application_state)

    def show_for(self, row, widget: QWidget):
        """Show `row`'s card just below `widget` (above it if there is no room), on its screen."""
        if row is not self.row:
            self.row = row
            self.setText(row.text)
            natural = QTextDocument()  # its lines unwrapped
            natural.setDefaultFont(self.font())
            natural.setHtml(row.text)
            width = min(360, math.ceil(natural.idealWidth()) + 2 * (self.margin() + self.frameWidth()))
            self.resize(width, self.heightForWidth(width))
        self._leaving.stop()
        self.setPalette(QToolTip.palette())  # a tooltip's colours, as they are now (light or dark mode)
        screen = widget.screen().availableGeometry()
        at = widget.mapToGlobal(QPoint(0, 0))
        y = at.y() + widget.height() + 2
        if y + self.height() > screen.bottom() + 1:
            y = at.y() - self.height() - 2
        self.move(max(screen.left(), min(at.x(), screen.right() + 1 - self.width())),
                  max(screen.top(), min(y, screen.bottom() + 1 - self.height())))
        self.show()
        self.raise_()
        QApplication.instance().installEventFilter(self)  # for Esc, wherever the focus is

    def entered(self, row):
        if row is self.row:
            self._leaving.stop()

    def left(self, row):
        if row is self.row and self.isVisible():
            self._leaving.start()

    def dismiss(self):
        QApplication.instance().removeEventFilter(self)
        self._leaving.stop()
        self.row, self._on_card = None, False
        self.hide()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.dismiss()
        return False

    def enterEvent(self, event):
        self._on_card = True
        self._leaving.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._on_card = False
        if self.isVisible():
            self._leaving.start()
        super().leaveEvent(event)

    def _left(self):
        if not self._on_card and not (self.row is not None and self.row.under_mouse()):
            self.dismiss()

    def _learn_more(self, href: str):
        self.dismiss()
        self.learn_more.emit(href)

    def _application_state(self, state):
        if state != Qt.ApplicationActive:  # e.g. Alt+Tab to another program
            self.dismiss()


class RowHelp(QObject):
    """A row of Properties: resting the mouse on its label or one of its inputs shows its card, not a tooltip; their
    tooltip is what the row is for. Pressing a key or a mouse button in them hides the card."""

    def __init__(self, card: HelpCard, widgets: list, label: str, what: str, values: str, href: str):
        super().__init__(widgets[0])  # it goes with the row
        self.card, self.widgets = card, widgets
        self.text = (f"<b>{html.escape(label)}</b><br>{html.escape(what)}<br>{values}<br>"
                     f'<a href="{html.escape(href)}">Learn more ›</a>')
        for widget in widgets:
            widget.setToolTip(_as_written(what))
            # The mouse is on a code box's text area, or on a spin box's text.
            inner = ([widget.viewport()] if isinstance(widget, QAbstractScrollArea) else
                     widget.findChildren(QLineEdit) if isinstance(widget, QAbstractSpinBox) else [])
            for part in (widget, *inner):
                part.installEventFilter(self)

    def under_mouse(self) -> bool:
        return any(widget.underMouse() for widget in self.widgets)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.ToolTip:
            self.card.show_for(self, watched if watched in self.widgets else watched.parentWidget())
            return True
        if kind == QEvent.Enter:
            self.card.entered(self)
        elif kind == QEvent.Leave:
            self.card.left(self)
        elif kind in (QEvent.KeyPress, QEvent.MouseButtonPress):
            self.card.dismiss()
        return False


def _inputs(widget: QWidget) -> list:
    """The inputs of a setting's widget: itself, or the boxes in it (not its buttons, nor a code box's Wrap lines)."""
    if isinstance(widget, (QLineEdit, QComboBox, QAbstractSpinBox, QCheckBox, QPlainTextEdit)):
        return [widget]
    return [w for w in widget.children() if isinstance(w, (QLineEdit, QComboBox, QPlainTextEdit))]


class PropertiesPanel(QScrollArea):
    """Properties of the selected block, generated from its config fields."""

    def __init__(self, home, settings):
        super().__init__()
        self.home = home
        self.settings = settings  # (the editor's preferences)
        self.setWidgetResizable(True)
        self.doc = self.registry = self.block_id = self.note_id = None
        self._committing = False
        self._custom_when = None  # (flow, block) whose schedule you chose to write as a cron line
        self.card = HelpCard(self)
        self.refresh()

    def _help(self, label, inputs: list, title: str, what: str, values: str, href: str):
        RowHelp(self.card, ([label] if label is not None else []) + inputs, title, what, values, href)

    def _row_help(self, row: str, label, *inputs):
        """The card of a row that is not a setting (docs.ROWS)."""
        self._help(label, list(inputs), *docs.row_card(row), docs.row_href(row))

    def _field_help(self, cls, key: str, label, *inputs):
        f = cls.config[key]
        self._help(label, list(inputs), setting_label(key, f), *docs.field_card(f), docs.field_href(cls, key))

    def _add_row(self, form: QFormLayout, row: str, widget: QWidget, *inputs):
        """Add the row `row` of docs.ROWS to `form`, with its label and card (on `inputs`, or `widget` itself)."""
        form.addRow(docs.ROWS[row][0], widget)
        self._row_help(row, form.labelForField(widget), *(inputs or [widget]))

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
        self.card.dismiss()  # its row is going
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
        self._add_row(form, "name", name)
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
            if isinstance(f, fields.SourceNames):
                layout.addWidget(self._source_names_box(cls, key, f, config.get(key)))
                continue
            widget = self._field_widget(key, f, config.get(key))
            label = QLabel(setting_label(key, f))
            widget.setToolTip(_as_written(docs.field_card(f)[0]))  # (also its buttons': …, Edit…, New…)
            self._field_help(cls, key, label, *_inputs(widget))
            if isinstance(f, fields.Code):
                layout.addWidget(label)
                layout.addWidget(widget)
            else:
                row = QFormLayout()
                row.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
                row.addRow(label, widget)
                layout.addLayout(row)
        if keeps:
            layout.addWidget(self._keep_box(config, cls))
        layout.addWidget(self._retry_box(spec))
        self._show_problems()

    # --- how a start block's runs are kept ---------------------------------------------------

    # Each choice, and what it writes in the flow file when you choose it (None: nothing, as in Settings).
    KEEP_CHOICES = {"As in Settings": None, "All of them": "all", "The last N": "last 100", "For a time": "90 days"}

    def _keep_box(self, config: dict, cls) -> QGroupBox:
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
        choice.currentTextChanged.connect(lambda text: (self._set("keep", self.KEEP_CHOICES.get(text)), self.refresh()))
        form.addRow(choice)
        self._field_help(cls, "keep", None, choice)
        if kind == "last":
            count = QSpinBox(minimum=1, maximum=1_000_000, value=number, keyboardTracking=False)
            count.valueChanged.connect(lambda n: self._set("keep", f"last {n}"))
            self._add_row(form, "keep.runs", count)
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
            self._add_row(form, "keep.for", row, count, unit)
        failed = QCheckBox(cls.config["keep_failed"].label)
        failed.setChecked(bool(config.get("keep_failed")))
        failed.toggled.connect(lambda checked: self._set("keep_failed", checked or None))
        form.addRow(failed)
        self._field_help(cls, "keep_failed", None, failed)
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
        self._add_row(form, "when.runs", mode)
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
        self._add_row(form, "when.at", edit)

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
        self._add_row(form, "when.how_often", kind_box)

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
            self._add_row(form, "when.every", box)
            return
        if current == "custom":
            line = QLineEdit(cron)
            line.editingFinished.connect(lambda: (self._set_when(cron=line.text().strip() or None),
                                                  show_preview({"cron": line.text().strip()})))
            self._add_row(form, "when.cron", line)
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
            self._add_row(form, "when.minute", minute)
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
            self._add_row(form, "when.days", days, *days.findChildren(QCheckBox))
        if current == "monthly":
            day = QSpinBox(minimum=1, maximum=31, value=plan["day"])
            day.editingFinished.connect(lambda: (plan.update(day=day.value()), commit()))
            self._add_row(form, "when.day", day)
        time_edit = QTimeEdit(QTime(plan["hour"], plan["minute"]))
        time_edit.setDisplayFormat("HH:mm")
        time_edit.editingFinished.connect(lambda: (plan.update(hour=time_edit.time().hour(),
                                                               minute=time_edit.time().minute()), commit()))
        self._add_row(form, "when.time", time_edit)

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

    def _sources(self) -> dict[str, list[str]]:
        """{input: ["block.port", ...] connected into it, in edge order} of the block shown."""
        sources = {}
        for src, src_port, dst, dst_port in self.doc.edges():
            if dst == self.block_id:
                sources.setdefault(dst_port, []).append(f"{src}.{src_port}")
        return sources

    def _source_names_box(self, cls, key, f, value) -> QGroupBox:
        """A row for each output connected into the input f.port: where it comes from and its name, to change (left
        empty: its default again), each with the setting's card; while nothing is connected there, how to."""
        box = QGroupBox(setting_label(key, f))
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        sources = self._sources().get(f.port, [])
        names = f.in_effect(value if isinstance(value, dict) else {}, sources, self.doc.data["params"])
        for source in sources:
            edit = QLineEdit(str(names[source]))  # (a name written wrong by hand: Properties says so)
            edit.editingFinished.connect(lambda source=source, edit=edit: self._set_source_name(key, f, source, edit))
            label = QLabel(f"from {source}")
            form.addRow(label, edit)
            self._field_help(cls, key, label, edit)
        note = QLabel("Write {name} in the Template where an input should go; flow parameters work the same way. An "
                      "empty Template shows every input in turn." if sources else
                      "Nothing is connected yet. Connect blocks (tables, charts, text…) to this block's data input; "
                      "each one is listed here with a name, at first its block's name.", wordWrap=True,
                      textFormat=Qt.PlainText)
        note.setStyleSheet("color: palette(placeholder-text);")
        form.addRow(note)
        return box

    def _set_source_name(self, key, f, source, edit):
        """The name typed in the Inputs row of `source`; left empty: its default again (as if it had none)."""
        if self.doc is None or self.block_id not in self.doc.blocks:
            return
        sources, params = self._sources().get(f.port, []), self.doc.data["params"]
        if source not in sources:
            return  # its line was removed while the row was being edited
        stored = (self.doc.blocks[self.block_id].get("config") or {}).get(key)
        # Every name shown is stored with it (a file written by hand may store none), so no other one changes.
        names = f.in_effect(stored if isinstance(stored, dict) else {}, sources, params)
        others = {s: n for s, n in names.items() if s != source}
        name = edit.text().strip() or f.in_effect(others, sources, params)[source]
        if name == names[source]:
            edit.setText(name)
            return  # (left as it was: nothing to save)
        names = {**names, source: name}
        try:
            f.coerce(names)  # a name it cannot take is told before anything changes
        except ValueError as e:
            self.error.setText(f"{setting_label(key, f)}: {e}")
            self.error.show()
            return
        edit.setText(name)
        self._set(key, names)

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
            code = f.language in ("python", "sql")
            wrap = self.settings.value("wrap_code", False, type=bool)  # (as chosen in any code box)
            w = CodeEditor(_to_text(value), f.language, wrap) if code else CodeEdit(_to_text(value))
            w.setPlaceholderText(_to_text(default))
            w.setToolTip(_as_written(f.help))  # (the box's own: it may be under buttons)
            w.editing_finished.connect(lambda: self._set(key, w.toPlainText() or None))
            if f.language == "html":
                return _with_html_buttons(w)
            if not code:
                return w
            wrap_box = QCheckBox("Wrap lines", checked=wrap)
            self._row_help("wrap", None, wrap_box)
            wrap_box.toggled.connect(lambda on: (self.settings.setValue("wrap_code", on), w.set_wrap(on)))
            box = QWidget()
            column = QVBoxLayout(box)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(w)
            column.addWidget(wrap_box)
            return box
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
        self._add_row(form, "retry.policy", policy)
        shown = {"fixed": ["max_attempts", "delay"],
                 "exponential": ["max_attempts", "initial", "factor", "max_delay", "jitter"]}.get(retry.get("policy", "none"), [])
        placeholders = {"max_attempts": "3", "delay": "e.g. 30s", "initial": "e.g. 30s", "factor": "2",
                        "max_delay": "e.g. 10m", "jitter": "0 to 1"}
        edits = {}
        for key in shown:
            edits[key] = QLineEdit(_to_text(retry.get(key)), placeholderText=placeholders[key])
            self._add_row(form, f"retry.{key}", edits[key])
        timeout = QLineEdit(_to_text(spec.get("timeout")), placeholderText="none, e.g. 20m")
        self._add_row(form, "timeout", timeout)

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
        self._add_row(form, "text.text", text)
        style = QComboBox()
        for name in ("title", "heading", "normal", "small"):
            style.addItem(name.capitalize(), name)
        style.setCurrentIndex(style.findData(note.get("style") or "normal"))
        # A style sets its size: a size you chose (or dragged) gives way to it.
        style.activated.connect(lambda _: (self._set_note_values(
            {"style": None if style.currentData() == "normal" else style.currentData(), "size": None}),
            self.refresh()))
        self._add_row(form, "text.style", style)
        style_size = float(NOTE_STYLES.get(note.get("style") or "normal", 11))
        current = float(note.get("size") or style_size)
        size = QDoubleSpinBox(minimum=NOTE_SIZES[0], maximum=NOTE_SIZES[1], decimals=1, suffix=" pt")
        size.setValue(current)
        # A change only (leaving the field is not one); the style's own size means no size of its own.
        size.editingFinished.connect(lambda: size.value() != current and self._set_note(
            "size", None if size.value() == style_size else size.value()))
        self._add_row(form, "text.size", size)
        for key in ("bold", "italic", "underline"):
            box = QCheckBox(key.capitalize())
            box.setChecked(bool(note.get(key)))
            box.toggled.connect(lambda checked, k=key: self._set_note(k, checked))
            form.addRow("", box)
            self._row_help(f"text.{key}", None, box)
        font = QComboBox()
        font.addItem("(the usual font)", "")
        for family in QFontDatabase.families():  # the fonts installed on this computer
            font.addItem(family, family)
        font.setCurrentIndex(max(font.findData(note.get("font") or ""), 0))
        font.activated.connect(lambda _: self._set_note("font", font.currentData() or None))
        self._add_row(form, "text.font", font)
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
            config = block_config(cls, BlockSpec(id=self.block_id, type=cls.type_id,
                                                 config=dict(spec.get("config") or {})))
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()
            return
        problems = cls.problems(config, self._sources(), set(self.doc.data["params"]))
        self.error.setText("\n".join(problems))
        self.error.setVisible(bool(problems))


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
GREY = "gray"  # the time and the notes: readable on a light and on a dark background
MARKDOWN = QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML
FENCES = MarkdownIt("commonmark", {"html": False})  # finds the ```mermaid code blocks Qt's Markdown would show
DARK_DIAGRAM = '\n%%{init: {"theme": "dark"}}%%\n'  # at the end: Mermaid's front matter (---) must come first
PICTURE = "taskloom-picture:"  # the Log's names for its pictures: never a file's or a web address
_DIAGRAMS = OrderedDict()  # (source, background, pixel ratio) -> its picture, or why it could not be drawn


def mermaid_picture(source: str, background: QColor, ratio: float) -> QPixmap | str:
    """The Mermaid diagram `source` drawn by mmdr, offline, on `background` (in Mermaid's dark theme on a dark one), at
    its own size scaled down to fit report.MAX_IMAGE_WIDTH square (never up), sharp at `ratio` pixels per point; or
    the first line of why it could not be drawn. The last 32 are kept, so each is drawn once, not on each repaint."""
    key = (source, background.name(), ratio)
    if key not in _DIAGRAMS:
        try:
            import mmdr  # here, not at the top: only the editor draws diagrams (the runner has no mmdr)

            diagram = mmdr.render(source + DARK_DIAGRAM if background.lightness() < 128 else source)
            image = QImage.fromData(diagram.png(background=background.name()))  # at its own size
            scale = min(1, MAX_IMAGE_WIDTH / max(image.width(), image.height())) * ratio
            if scale != 1:
                image = QImage.fromData(diagram.png(image.width() * scale, image.height() * scale, background.name()))
            image.setDevicePixelRatio(ratio)
            _DIAGRAMS[key] = QPixmap.fromImage(image)
        except BaseException as e:  # mmdr's ValueError for a malformed diagram, anything else, even a panic of its
            # Rust code (a BaseException): the diagram shows as text, the editor goes on
            _DIAGRAMS[key] = (str(e).strip() or type(e).__name__).splitlines()[0]
        if len(_DIAGRAMS) > 32:
            _DIAGRAMS.popitem(last=False)
    _DIAGRAMS.move_to_end(key)
    return _DIAGRAMS[key]


def _mermaid_pieces(text: str) -> list[tuple[str, str | None]]:
    """Markdown `text` cut at its ```mermaid code blocks (those outside lists and quotes): (Markdown, None) and (the
    code block as written, its diagram's source), in order. Cut from the text: Qt's Markdown runs two code blocks
    one right after the other together."""
    text = re.sub(r"\r\n?", "\n", text)  # as markdown-it counts lines
    lines, pieces, start = text.split("\n"), [], 0
    for token in FENCES.parse(text):
        if token.type == "fence" and token.level == 0 and token.info.split()[:1] == ["mermaid"]:
            first, end = token.map
            pieces += [("\n".join(lines[start:first]), None), ("\n".join(lines[first:end]), token.content)]
            start = end
    return pieces + [("\n".join(lines[start:]), None)]


def _picture_size(path: str) -> QSizeF | str:
    """The size the Log shows the picture at `path` in (scaled down to fit report.MAX_IMAGE_WIDTH square, never up),
    from the file's header only, or the note it shows instead."""
    try:
        there = Path(path).is_file()
    except OSError:
        there = False
    if not there:
        return "(the picture is no longer there)"
    reader = QImageReader(path)
    size = reader.size()
    if size.isEmpty():
        return f"(cannot show this picture: {reader.errorString()})"
    limit = QImageReader.allocationLimit()  # MB; 0: none
    if limit and size.width() * size.height() * 4 > limit * 1024 * 1024:
        return f"(cannot show this picture: over Qt's {limit} MB picture limit)"
    if _turned(reader):
        size.transpose()
    return QSizeF(size) * min(1, MAX_IMAGE_WIDTH / max(size.width(), size.height()))


def _turned(reader: QImageReader) -> bool:
    """The picture is stored turned a quarter (e.g. a photo taken upright): it is drawn the other way round."""
    return bool(reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90)


class _LogDocument(QTextDocument):
    """The Log's text. Its pictures are read from their files each time they are painted, so they show each file as
    it is now and no file stays open (it can be deleted, also on Windows); the last 32 painted are kept, by file
    time and size; its Mermaid diagrams are drawn again only once mermaid_picture no longer keeps them. It loads
    nothing else: no other file, nothing from the network."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.pictures = {}  # name -> (path, shown size), or what draws a Mermaid diagram at a pixel ratio
        self.thumbnails = OrderedDict()  # (path, mtime_ns, size, pixel ratio) -> QPixmap

    def loadResource(self, kind, name):
        # Not the base class's, which would keep each picture in the document for good.
        picture = self.pictures.get(name.toString()) if kind == QTextDocument.ImageResource else None
        if picture is None:
            return None
        ratio = self.view.devicePixelRatioF()
        if callable(picture):  # a Mermaid diagram
            drawn = picture(ratio)
            return drawn if isinstance(drawn, QPixmap) else None
        path, shown = picture
        try:
            stat = os.stat(path)
        except OSError:
            return None  # gone since: Qt's broken-picture mark, until the next redraw says so
        key = (path, stat.st_mtime_ns, stat.st_size, ratio)
        if key not in self.thumbnails:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            scaled = (shown * ratio).toSize()
            reader.setScaledSize(scaled.transposed() if _turned(reader) else scaled)
            image = reader.read()
            if image.isNull():  # e.g. damaged after its header: Qt's broken-picture mark
                return None
            image.setDevicePixelRatio(ratio)  # sharp on a high-DPI screen
            self.thumbnails[key] = QPixmap.fromImage(image)
            if len(self.thumbnails) > 32:
                self.thumbnails.popitem(last=False)
        self.thumbnails.move_to_end(key)
        return self.thumbnails[key]


class _LogView(QTextBrowser):
    """The Log's text box: read only, in the fixed-width font, lines never wrapped, links shown but never opened."""

    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setLineWrapMode(QTextBrowser.NoWrap)
        self.setDocument(_LogDocument(self))
        self.document().setUndoRedoEnabled(False)
        self.setFont(MONO)

    def createMimeDataFromSelection(self):
        # Plain text only; a picture adds nothing (its path is the line above it). The object must come from Qt: the
        # clipboard keeps it, and one made in Python is freed twice when the program quits (a crash on exit). Qt
        # fills it from the selection when it is first read, so read its text before replacing everything with it.
        mime = super().createMimeDataFromSelection()
        text = mime.text()
        mime.clear()
        mime.setText(re.sub("\ufffc\n?", "", text))
        return mime


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
        self.text = _LogView()
        layout.addWidget(self.text)
        self.entries, self.block = [], None
        # New lines keep the view at the bottom when it is there, and leave it where you scrolled up to.
        self._follow = True
        scroll = self.text.verticalScrollBar()
        scroll.valueChanged.connect(lambda value: setattr(self, "_follow", value >= scroll.maximum()))
        scroll.rangeChanged.connect(lambda _, maximum: self._follow and scroll.setValue(maximum))
        self.clear_filter.clicked.connect(lambda: (self.set_filter(None), self.filter_cleared.emit()))
        self.clear_filter.hide()

    def set_entries(self, entries: list[dict]):
        self.entries = entries
        self._render()

    def append(self, entry: dict):
        self.entries.append(entry)
        if self._shown(entry):
            self._write([entry])

    def _shown(self, e: dict) -> bool:
        # A run's start and end lines stay visible with a block filter: they split the runs apart.
        return self.block is None or e.get("block") == self.block or e.get("header", False)

    def set_filter(self, block_id):
        self.block = block_id
        self.showing.setText(f"Showing <b>{html.escape(block_id)}</b>" if block_id else "All blocks")
        self.clear_filter.setVisible(block_id is not None)
        self._render()

    def _render(self):
        self.text.clear()
        self.text.document().pictures.clear()
        self._follow = True  # drawn again: it shows the last lines
        self._write([e for e in self.entries if self._shown(e)])

    def _write(self, entries: list[dict]):
        cursor = QTextCursor(self.text.document())
        cursor.beginEditBlock()
        for e in entries:
            self._entry(cursor, e)
        cursor.endEditBlock()

    def _entry(self, cursor: QTextCursor, e: dict):
        """An entry's line, HH:MM:SS [block] LEVEL message, at the end of the Log; for a picture (show "image") the
        picture under it, for Markdown (show "markdown") the message drawn under it instead."""
        show, message, level = e.get("show"), e.get("message", ""), e.get("level", "INFO")
        # One paragraph (its lines split by line separators), its spaces kept: reports such as Python tracebacks are
        # laid out in columns.
        said = "" if show == "markdown" or not message else " " + "\u2028".join(message.splitlines())
        self._paragraph(cursor, QTextBlockFormat())
        plain = QTextCharFormat()
        if e.get("header"):
            plain.setFontWeight(QFont.Bold)
        for text, color in ((e.get("ts", "")[11:19], GREY), (f" [{e['block']}] " if e.get("block") else " ", None),
                            (level, LEVEL_COLORS.get(level)), (said, None)):
            fmt = QTextCharFormat(plain)
            if color:
                fmt.setForeground(QColor(color))
            cursor.insertText(text, fmt)
        if show == "image":
            self._picture(cursor, message)
        elif show == "markdown":
            self._markdown(cursor, message)

    def _paragraph(self, cursor: QTextCursor, fmt: QTextBlockFormat):
        """Start a paragraph at the end of the Log: in the empty one there (the first, or the one after Markdown)."""
        cursor.movePosition(QTextCursor.End)
        _start(cursor, fmt)

    def _indent(self) -> float:
        """Under the [block] column: the width of "HH:MM:SS "."""
        return QFontMetricsF(self.text.document().defaultFont()).horizontalAdvance("00:00:00 ")

    def _under(self) -> QTextBlockFormat:
        fmt = QTextBlockFormat()
        fmt.setLeftMargin(self._indent())
        return fmt

    def _note(self, cursor: QTextCursor, text: str):
        self._paragraph(cursor, self._under())
        _grey(cursor, text)

    def _picture(self, cursor: QTextCursor, path: str):
        shown = _picture_size(path)
        if isinstance(shown, str):
            self._note(cursor, shown)
            return
        self._paragraph(cursor, self._under())
        self._image(cursor, (path, shown), shown)

    def _image(self, cursor: QTextCursor, picture, shown: QSizeF):
        """A picture at the cursor, at the size `shown`: a file's (path, shown), or what draws a diagram."""
        pictures = self.text.document().pictures
        fmt = QTextImageFormat()
        fmt.setName(f"{PICTURE}{len(pictures)}")
        fmt.setWidth(shown.width())
        fmt.setHeight(shown.height())
        pictures[fmt.name()] = picture
        cursor.insertImage(fmt)

    def _markdown(self, cursor: QTextCursor, text: str):
        """`text` drawn as Markdown, in a frame under the [block] column (the next entry's line is outside it), each
        ```mermaid code block drawn as its diagram where it is, or, if it cannot be, shown as written and followed by
        a grey line saying why."""
        frame_format = QTextFrameFormat()
        frame_format.setLeftMargin(self._indent())
        frame = cursor.insertFrame(frame_format)
        background, ratio = self.text.palette().color(QPalette.Base), self.text.devicePixelRatioF()
        for written, diagram in _mermaid_pieces(text):
            drawn = None if diagram is None else mermaid_picture(diagram, background, ratio)
            if isinstance(drawn, QPixmap):
                _start(cursor, QTextBlockFormat())
                self._image(cursor, partial(mermaid_picture, diagram, background), drawn.deviceIndependentSize())
                continue
            if written.strip():
                source = QTextDocument()
                source.setMarkdown(written, MARKDOWN)
                # The paragraph it goes into keeps its own format: give it the first paragraph's (e.g. a heading's).
                first = source.firstBlock().blockFormat()
                first.clearProperty(QTextFormat.ObjectIndex)
                _start(cursor, first)
                cursor.insertFragment(QTextDocumentFragment(source))
            if drawn:
                _start(cursor, QTextBlockFormat())
                _grey(cursor, f"(could not draw this diagram: {drawn})")
        # Its text in the application's font, its code in the Log's fixed-width one.
        parts, block = [], cursor.document().findBlock(frame.firstPosition())
        while block.isValid() and block.position() <= frame.lastPosition():
            code = block.blockFormat().hasProperty(QTextFormat.BlockCodeLanguage)
            it = block.begin()
            while not it.atEnd():
                part = it.fragment()
                parts.append((part.position(), part.length(), code or part.charFormat().fontFixedPitch()))
                it += 1
            block = block.next()
        for start, length, code in parts:
            fmt = QTextCharFormat()
            fmt.setFontFamilies((MONO if code else QApplication.font()).families())
            part = QTextCursor(cursor.document())
            part.setPosition(start)
            part.setPosition(start + length, QTextCursor.KeepAnchor)
            part.mergeCharFormat(fmt)


def _start(cursor: QTextCursor, fmt: QTextBlockFormat):
    """Start a paragraph at the cursor: in the empty one it is in, if it is."""
    if cursor.block().length() > 1:
        cursor.insertBlock(fmt, QTextCharFormat())
    else:
        cursor.setBlockFormat(fmt)


def _grey(cursor: QTextCursor, text: str):
    """A note's text: grey and italic."""
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(GREY))
    fmt.setFontItalic(True)
    cursor.insertText(text, fmt)


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
