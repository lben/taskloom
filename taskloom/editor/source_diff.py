"""Commit History's source diffs (docs/promises/source-diff.yaml): two versions of a flow file side by side, the older
on the left, in a window of its own. Lines are compared with difflib: added, removed and changed lines are marked by
colour (the changed characters inside a changed line too), unchanged lines stay level (filler lines on the other side,
unnumbered), both sides scroll together, Next and Previous (F7, Shift+F7) jump between the changes, nothing can be
typed, Ctrl+F finds in the side it is pressed in and zoom changes both sides. A line above the sides says when the
versions differ in line ends or in the newline at the end, which the lines shown cannot."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from functools import cache

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPainter, QShortcut, QTextCursor
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QToolButton, QVBoxLayout,
                               QWidget)

from .canvas import is_dark
from .panels import CodeEditor, CodeHighlighter, FindBar, FindToggles
from .zoom import mark

# The contrast text needs (WCAG), and how much of the text's colour a word's darkest pixel is sure to get. Letters are
# drawn smoothed (their edges blended with the mark behind), and on Linux (FreeType, letters at fractional positions)
# no pixel of a word may get all of it: Ubuntu CI drew #83108f on #a9c0f3 at its darkest as #851895, 94% of it
# (4.50:1 for 4.74:1), and Qt's offscreen platform in Debian drew that word in DejaVu Sans Mono at 10 to 20 px at 95.6%
# or more. A ratio aimed above 4.5:1 is no margin for that: what a blend costs in contrast differs from one pair of
# colours to another. So text on a mark is shaded to read at 4.5:1 with only INK of its colour painted, under the
# worst seen.
READS = 4.5
INK = 0.9
# (light, dark), as in the approved mockup: a changed row, and the line along a change's top and bottom; the changed
# characters inside a changed row; a filler line's hatching. The text (the theme's) reads on each at 4.5:1 or more.
FILL = {"added": ("#d9f2dc", "#273d2d"), "removed": ("#fbe1e0", "#45282b"), "changed": ("#dde7fc", "#26344d")}
EDGE = {"added": ("#62b56f", "#4f8f5f"), "removed": ("#de6b68", "#b0565b"), "changed": ("#6d93e4", "#5779b8")}
CHARACTERS = ("#a9c0f3", "#3a5583")
HATCH = ("#d4d4d4", "#3a3c41")
# Characters inside a line (git ends a line at "\n" only) that a side would break it at, or show as a break: each shown
# as a character of its own. Qt's text document starts a new row at U+FDD0 and U+FDD1 (noncharacters, with no symbol
# of their own: shown as the replacement character).
SHOWN = str.maketrans({"\r": "␍", "\x0b": "␋", "\x0c": "␌", "\x1c": "␜", "\x1d": "␝", "\x1e": "␞",
                       "\x85": "␤", "\u2028": "↵", "\u2029": "¶", "\ufdd0": "\ufffd", "\ufdd1": "\ufffd"})


@dataclass
class Row:
    """A row both sides show: each side's line number (None: a filler line) and text, how the row is marked (equal,
    added, removed, changed) and each side's changed characters, (start, end) as Qt counts positions."""
    numbers: tuple
    texts: tuple
    kind: str
    spans: tuple = ((), ())


def _lines(data: bytes) -> tuple[list[str], list[str], list[str]]:
    """A version's lines as git counts them (each ended by "\\n" only) with their line breaks, each as shown (without
    its line break, the other characters a side would break it at shown as SHOWN gives them) and what is compared of
    each: the line with its line break, Windows' taken as Unix' (the note line says when they differ), so that a last
    line without one differs from the same line with one, as in `git diff`."""
    lines = re.findall(r"[^\n]*\n|[^\n]+", data.decode("utf-8", "backslashreplace"))
    keys = [line[:-2] + "\n" if line.endswith("\r\n") else line for line in lines]
    return lines, [key.removesuffix("\n").translate(SHOWN) for key in keys], keys


def _qt(text: str, i: int) -> int:
    """Where Qt puts text[i]: it counts a character outside the basic plane (e.g. an emoji) as two."""
    return len(text[:i].encode("utf-16-le")) // 2


def _changed_characters(old: str, new: str) -> tuple:
    found = ([], [])
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != "equal":
            for spans, text, start, end in ((found[0], old, a1, a2), (found[1], new, b1, b2)):
                if end > start:
                    spans.append((_qt(text, start), _qt(text, end)))
    return tuple(found[0]), tuple(found[1])


def align(old: list[str], new: list[str], old_keys: list[str], new_keys: list[str]):
    """The rows both sides show, unchanged lines level, and the changes (each one's first row and the row after)."""
    rows, changes = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False).get_opcodes():
        if tag == "equal":
            rows += [Row((i + 1, j + 1), (old[i], new[j]), "equal") for i, j in zip(range(i1, i2), range(j1, j2))]
            continue
        kind = {"replace": "changed", "delete": "removed", "insert": "added"}[tag]
        start = len(rows)
        for k in range(max(i2 - i1, j2 - j1)):
            i, j = (i1 + k if i1 + k < i2 else None), (j1 + k if j1 + k < j2 else None)
            row = Row((None if i is None else i + 1, None if j is None else j + 1),
                      ("" if i is None else old[i], "" if j is None else new[j]), kind)
            if i is not None and j is not None:
                row.spans = _changed_characters(old[i], new[j])
            rows.append(row)
        changes.append((start, len(rows)))
    return rows, changes


def _line_ends(lines: list[str]) -> str | None:
    windows = sum(line.endswith("\r\n") for line in lines)
    unix = sum(line.endswith("\n") for line in lines) - windows
    if windows and unix:
        return "Windows (CRLF) and Unix (LF) mixed"
    return "Windows (CRLF)" if windows else "Unix (LF)" if unix else None


def note(old: list[str], new: list[str], rows: list[Row]) -> str:
    """What the lines shown cannot show: line ends that differ (also when both versions mix them, on other lines: an
    unchanged row's two lines then differ in their line ends only), or a newline at the end in one version only. `old`
    and `new`: each version's lines with their line breaks."""
    said = []
    ends = _line_ends(old), _line_ends(new)
    if None not in ends and ends[0] != ends[1]:
        said.append(f"The line ends differ: {ends[0]} on the left, {ends[1]} on the right.")
    elif any(row.kind == "equal" and old[row.numbers[0] - 1] != new[row.numbers[1] - 1] for row in rows):
        said.append("The line ends differ: both versions mix Windows (CRLF) and Unix (LF), on other lines.")
    if old and new and old[-1].endswith("\n") != new[-1].endswith("\n"):
        said.append("The newline at the end differs: the " + ("left" if old[-1].endswith("\n") else "right")
                    + " version ends with one, the other does not.")
    return " ".join(said)


def _luminance(colour: QColor) -> float:
    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(colour.redF()) + 0.7152 * channel(colour.greenF()) + 0.0722 * channel(colour.blueF())


def _contrast(a: QColor, b: QColor) -> float:
    """The WCAG contrast ratio of two colours (1 to 21)."""
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _darkest_pixel(text: QColor, behind: QColor) -> QColor:
    """What a word in `text` on `behind` is sure to show at its darkest: INK of its colour over `behind`'s."""
    return QColor.fromRgbF(*(INK * t + (1 - INK) * b for t, b in zip(text.getRgbF()[:3], behind.getRgbF()[:3])))


@cache
def _on_mark(colour: str, mark: str) -> QColor:
    """Text in `colour` on `mark`: in it where it reads there (READS, smoothed: INK), else in a shade of its hue just
    enough darker (on a mark lighter than it) or lighter (on one darker) to read there."""
    text, behind = QColor(colour), QColor(mark)
    hue, saturation, lightness, _ = text.getHslF()
    step = -0.01 if _luminance(behind) > _luminance(text) else 0.01
    while _contrast(_darkest_pixel(text, behind), behind) < READS and 0 < lightness < 1:
        lightness = min(1.0, max(0.0, lightness + step))
        text = QColor.fromHslF(hue, saturation, lightness)
    return text


class _OnMarks(CodeHighlighter):
    """A side's YAML in the code boxes' colours; on a change's mark (its row's, or its changed characters') each in a
    shade that reads there."""

    def __init__(self, document, rows: list[Row], side: int):
        self.rows, self.side = rows, side  # (first: it colours as it is made)
        super().__init__(document, "yaml")

    def highlightBlock(self, text):
        super().highlightBlock(text)
        n = self.currentBlock().blockNumber()
        if n >= len(self.rows) or self.rows[n].kind == "equal":
            return
        row, dark = self.rows[n], int(is_dark())
        for i in range(self.currentBlock().length() - 1):
            look = self.format(i)
            if look.foreground().style() == Qt.NoBrush:  # (the theme's text colour: it reads on every mark)
                continue
            mark = CHARACTERS[dark] if any(a <= i < b for a, b in row.spans[self.side]) else FILL[row.kind][dark]
            look.setForeground(_on_mark(look.foreground().color().name(), mark))
            self.setFormat(i, 1, look)


class DiffSide(CodeEditor):
    """One side: the app's code box, read-only (no key changes its text), each line one row; changed rows coloured,
    filler lines hatched and unnumbered; the YAML in the code boxes' colours. Zoomed, it gives the other side its size
    too."""

    def __init__(self, rows: list[Row], side: int):
        super().__init__("", "yaml", wrap=False)
        self.highlighter.setDocument(None)
        self.highlighter = _OnMarks(self.document(), rows, side)
        self.setReadOnly(True)
        self.setPlainText("\n".join(row.texts[side] for row in rows))
        self.rows, self.side, self.other = rows, side, None
        self.setObjectName(("source-diff-left", "source-diff-right")[side])
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)  # (both sides as high: they scroll as far)
        mark(self, "source_diff")

    def keyPressEvent(self, event):
        QPlainTextEdit.keyPressEvent(self, event)  # (not the code box's: it indents and breaks lines)

    def changeEvent(self, event):
        super().changeEvent(event)
        other = getattr(self, "other", None)
        if event.type() == QEvent.FontChange and other is not None and other.font() != self.font():
            other.setProperty("zoom_normal", self.property("zoom_normal"))  # (Ctrl 0 over it: the same normal size)
            other.setFont(self.font())

    def in_view(self):
        """Each row in view: its place, its block and where the viewport shows it."""
        block, offset, height = self.firstVisibleBlock(), self.contentOffset(), self.viewport().height()
        while block.isValid() and block.blockNumber() < len(self.rows):
            rect = self.blockBoundingGeometry(block).translated(offset)
            if rect.top() > height:
                return
            yield block.blockNumber(), block, rect
            block = block.next()

    def paint_rows(self, painter: QPainter, width: float, dy: float = 0, strip: bool = False):
        """The rows in view of the changes, `dy` lower: each in its colour (a filler line hatched, unless in the
        `strip` between the sides) and a line along each change's top and bottom."""
        dark = int(is_dark())
        for n, _, rect in self.in_view():
            row = self.rows[n]
            if row.kind == "equal":
                continue
            box = QRectF(0, rect.top() + dy, width, rect.height())
            if row.numbers[self.side] is None and not strip:
                painter.setBrushOrigin(0, 0)
                painter.fillRect(box, QBrush(QColor(HATCH[dark]), Qt.BDiagPattern))
            else:
                painter.fillRect(box, QColor(FILL[row.kind][dark]))
            painter.setPen(QColor(EDGE[row.kind][dark]))
            if n == 0 or self.rows[n - 1].kind == "equal":
                painter.drawLine(QPointF(0, box.top()), QPointF(width, box.top()))
            if n + 1 == len(self.rows) or self.rows[n + 1].kind == "equal":
                painter.drawLine(QPointF(0, box.bottom() - 1), QPointF(width, box.bottom() - 1))

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        self.paint_rows(painter, self.viewport().width())
        colour = QColor(CHARACTERS[int(is_dark())])
        for n, block, rect in self.in_view():
            for start, end in self.rows[n].spans[self.side]:
                cursor = QTextCursor(block)
                cursor.setPosition(block.position() + start)
                left = self.cursorRect(cursor).left()
                cursor.setPosition(block.position() + end)
                painter.fillRect(QRectF(left, rect.top(), self.cursorRect(cursor).left() - left, rect.height()), colour)
        painter.end()
        super().paintEvent(event)

    def paint_line_numbers(self, widget: QWidget, rect):
        """The number of each line in view on its row (a filler line has none), on the row's colour."""
        painter = QPainter(widget)
        painter.fillRect(rect, self.palette().base())
        self.paint_rows(painter, widget.width())
        painter.setPen(self.palette().placeholderText().color())
        for n, _, box in self.in_view():
            number = self.rows[n].numbers[self.side]
            if number is not None:
                painter.drawText(QRectF(0, box.top(), widget.width() - 4, self.fontMetrics().height()), Qt.AlignRight,
                                 str(number))


class Strip(QWidget):
    """Between the sides: each change's rows in its colour, joining its rows on the left to those on the right."""

    def __init__(self, side: DiffSide):
        super().__init__()
        self.side = side
        self.setFixedWidth(30)
        side.updateRequest.connect(lambda *_: self.update())  # (scrolled)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())
        viewport = self.side.viewport()
        top = self.mapFromGlobal(viewport.mapToGlobal(QPoint(0, 0))).y()
        painter.save()
        painter.setClipRect(0, top, self.width(), viewport.height())
        self.side.paint_rows(painter, self.width(), top, strip=True)
        painter.restore()
        painter.setPen(self.palette().mid().color())
        painter.drawLine(0, 0, 0, self.height())
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())


class SourceDiff(QWidget):
    """The window: `title`; `left` and `right` each a version (its name over its side, its bytes). Not modal."""

    def __init__(self, title: str, left: tuple[str, bytes], right: tuple[str, bytes], parent: QWidget):
        super().__init__(parent, Qt.Window)
        self.setObjectName("source-diff")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.resize(1200, 700)
        (old, old_lines, old_keys), (new, new_lines, new_keys) = _lines(left[1]), _lines(right[1])
        rows, self.changes = align(old_lines, new_lines, old_keys, new_keys)
        self.same = left[1] == right[1]
        self.at = -1  # the current change
        self.previous = QToolButton(text="Previous change (Shift+F7)", objectName="previous-change",
                                    arrowType=Qt.UpArrow, toolButtonStyle=Qt.ToolButtonTextBesideIcon)
        self.next = QToolButton(text="Next change (F7)", objectName="next-change", arrowType=Qt.DownArrow,
                                toolButtonStyle=Qt.ToolButtonTextBesideIcon)
        self.count = QLabel(objectName="change-count")
        bar = QHBoxLayout()
        for by, button, keys in ((-1, self.previous, "Shift+F7"), (1, self.next, "F7")):
            button.clicked.connect(lambda _=False, by=by: self.step(by))
            QShortcut(QKeySequence(keys), self, lambda by=by: self.step(by))
            bar.addWidget(button)
        bar.addSpacing(10)
        bar.addWidget(self.count)
        bar.addStretch()
        said = note(old, new, rows)
        self.note = QLabel(said, objectName="diff-note", wordWrap=True, textFormat=Qt.PlainText)
        self.left, self.right = DiffSide(rows, 0), DiffSide(rows, 1)
        self.left.other, self.right.other = self.right, self.left
        self.left.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # (the right side's scroll bar moves both)
        self.left.verticalScrollBar().valueChanged.connect(self.right.verticalScrollBar().setValue)
        self.right.verticalScrollBar().valueChanged.connect(self.left.verticalScrollBar().setValue)
        toggles = FindToggles(self)
        grid = QGridLayout()
        grid.setSpacing(0)
        for column, side, (name, _) in ((0, self.left, left), (2, self.right, right)):
            header = QLabel(name, textFormat=Qt.PlainText)
            header.setContentsMargins(4, 0, 0, 4)
            grid.addWidget(header, 0, column)
            grid.addWidget(side, 1, column)
            grid.addWidget(FindBar(side, toggles), 2, column)  # (Ctrl+F in the side: just under it)
            grid.setColumnStretch(column, 1)
        grid.addWidget(Strip(self.left), 1, 1)
        page = QVBoxLayout(self)
        page.addLayout(bar)
        page.addWidget(self.note)
        page.addLayout(grid, 1)
        if not said:
            self.note.hide()
        self._show_count()

    def showEvent(self, event):
        super().showEvent(event)
        if self.at < 0 and self.changes:  # as it opens: the first change
            self.go(0)

    def step(self, by: int):
        """The next (1) or previous (-1) change, if there is one."""
        if 0 <= self.at + by < len(self.changes):
            self.go(self.at + by)

    def go(self, at: int):
        """Change `at` the current one, in view on both sides (a third of the way down, where there is room)."""
        self.at = at
        rows = self.right.viewport().height() // max(1, self.right.fontMetrics().height())
        self.right.verticalScrollBar().setValue(max(0, self.changes[at][0] - rows // 3))
        self._show_count()

    def _show_count(self):
        self.count.setText(f"{self.at + 1} of {len(self.changes)} changes" if self.changes else
                           "No differences" if self.same else "No changed lines")
        self.previous.setEnabled(self.at > 0)
        self.next.setEnabled(self.at < len(self.changes) - 1)
