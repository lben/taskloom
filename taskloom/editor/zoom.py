"""Zoom the pane under the mouse pointer (docs/promises/zoom.yaml): Ctrl with +, = or - (main keys and number pad) and
Ctrl 0, and Ctrl with the mouse wheel over a text pane, wherever the keyboard focus is. A pane is a widget marked with
`mark`. Each kind of pane's size, in steps of one point from its normal font, is kept in editor.ini and given to a pane
of that kind when it is first shown. The canvas zooms as its zoom bar's + and − do, per flow, and is not kept."""

import math

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QStyle, QTableView, QTextEdit

SMALLEST, LARGEST = 0.5, 3  # times the normal size (the canvas: 20% to 300%)
KEYS = {Qt.Key_Plus: 1, Qt.Key_Equal: 1, Qt.Key_Minus: -1, Qt.Key_0: 0}  # (Ctrl = is Ctrl + without Shift)


def mark(widget, kind: str):
    """`widget` is a pane: "canvas", or the name its kind's size is kept under."""
    widget.setProperty("zoom", kind)


def _ctrl(event) -> bool:
    """Ctrl (Cmd on macOS) held, alone or with Shift; a number pad key says so too."""
    return event.modifiers() & ~(Qt.ShiftModifier | Qt.KeypadModifier) == Qt.ControlModifier


def _pane_under_pointer():
    widget = QApplication.widgetAt(QCursor.pos())
    while widget is not None:
        if widget.property("zoom") is not None:
            return widget
        if widget.isWindow():  # (a menu that opened over a pane is not the pane)
            return None
        widget = widget.parentWidget()
    return None


class Zoom(QObject):
    """The editor window's: an event filter on the whole application, for the panes of that window and its dialogs."""

    def __init__(self, window, settings):
        super().__init__(window)
        self.window, self.settings = window, settings
        self._turned = 0  # the wheel's turns not yet a notch (120 units): a trackpad sends smaller ones
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Show:  # a pane shown, not zoomed yet: its kind's size (in its window by then, not yet when
            # it is polished)
            if (watched.isWidgetType() and watched.property("zoom") not in (None, "canvas")
                    and watched.property("zoom_normal") is None and self._mine(watched) and self._step(watched)):
                self._show(watched, self._step(watched))
            return False
        if kind == QEvent.KeyPress and _ctrl(event) and event.key() in KEYS:
            by = KEYS[event.key()]
        elif not (kind == QEvent.Wheel and _ctrl(event) and event.angleDelta().y()):
            return False
        pane = _pane_under_pointer()
        if pane is None or (kind == QEvent.Wheel and not isinstance(pane, (QTextEdit, QPlainTextEdit))):
            return kind == QEvent.KeyPress  # over anything else the keys do nothing (nothing typed); the wheel scrolls
        if not self._mine(pane):
            return False  # (another editor window's)
        if kind == QEvent.Wheel:  # a step a notch; smaller turns add up to one
            self._turned += event.angleDelta().y()
            by = int(self._turned / 120)
            self._turned -= by * 120
            if not by:
                event.accept()
                return True
        if pane.property("zoom") == "canvas":
            pane.zoom_to(100 if by == 0 else pane.zoom_percent() + by * pane.zoom_bar.step.value())
        else:
            step = self._show(pane, 0 if by == 0 else self._step(pane) + by)
            self.settings.setValue(f"zoom/{pane.property('zoom')}", step)
        event.accept()
        return True

    def _mine(self, widget) -> bool:
        while widget is not None and widget is not self.window:
            widget = widget.parentWidget()
        return widget is not None

    def _step(self, pane) -> int:
        return self.settings.value(f"zoom/{pane.property('zoom')}", 0, type=int)

    def _show(self, pane, step: int) -> int:
        """Show the pane `step` points bigger than its normal font (kept between the smallest and the largest size):
        that step. A text pane keeps its top line (of a paragraph it wraps, that line, not the paragraph's first), or
        its last if it is scrolled to its end (the Log follows it). Already at that size, nothing happens."""
        normal = pane.property("zoom_normal")
        if normal is None:
            normal = QFont(pane.font())
            pane.setProperty("zoom_normal", normal)
        size = normal.pointSizeF()
        step = min(max(step, math.ceil(size * (SMALLEST - 1))), math.floor(size * (LARGEST - 1)))
        font = QFont(normal)
        if step:
            font.setPointSizeF(size + step)
        if font == pane.font():  # (at the smallest or the largest size: not even a scroll)
            return step
        bar = pane.verticalScrollBar() if isinstance(pane, QTextEdit) else None  # (a QPlainTextEdit keeps its own)
        if bar is not None:
            end, scrolled = bar.value() == bar.maximum(), bar.maximum() > 0
            top = pane.cursorForPosition(QPoint(0, 1))  # its top line's start (at 0 it can be the line just above)
        pane.setFont(font)
        if isinstance(pane, QTableView):  # rows as high as their text, as Qt makes them for the font they start in
            rows = pane.verticalHeader()
            rows.setDefaultSectionSize(max(rows.minimumSectionSize(), pane.style().pixelMetric(
                QStyle.PM_HeaderDefaultSectionSizeVertical, None, rows)))
        if bar is not None and scrolled:
            bar.setValue(bar.maximum() if end else bar.value() + pane.cursorRect(top).top())  # (laid out again)
        return step
