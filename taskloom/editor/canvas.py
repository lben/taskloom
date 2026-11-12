"""The canvas: blocks as nodes with ports, Loops and Sessions as boxes around their blocks, edges as curves, live run
status."""

from __future__ import annotations

import html
import math
from functools import cache
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap,
                           QTransform)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsTextItem, QGraphicsView, QHBoxLayout, QLabel, QLineEdit,
                               QSpinBox, QStyle, QStyleFactory, QToolButton)

from ..block import AFTER, DONE, all_ports
from ..blocks.logic import written_seconds
from ..flow import (API_REQUEST, ERROR_PORT, INDEX, ITEM, ITEMS, LINES, LOOP, RESULT, RESULTS, SESSION, BlockSpec,
                    block_config)
from .document import FlowDocument, edge_text

BLOCK_MIME = "application/x-taskloom-block"
TEXT_ITEM = "canvas.text"  # the Text item of the blocks tree: text on the canvas, not a block that runs
HANDLE = 10  # the corner you drag to resize a text
NODE_W, HEADER_H, ROW_H, FOOTER_H, PORT_R = 184, 28, 22, 22, 5
COUNTDOWN_H = 18  # the line under a Schedule block saying when it starts the flow next
# A Loop's box: as it is added; the least it can be resized to; the room it keeps around its blocks (left and right:
# for its ports, top: for its name, bottom: for its result line); where its item, index and result ports are, in from
# its sides; its shadow's offset; the corner you drag to resize it.
LOOP_W, LOOP_H, LOOP_MIN_W, LOOP_MIN_H = 520, 280, 260, 170
LOOP_SIDE, LOOP_TOP, LOOP_BOTTOM, INNER_X, SHADOW, GRIP = 70, 44, 30, 50, 6, 14
CATEGORY_COLORS = {
    "Start": "#1f8a4c", "Database": "#2c5d8f", "Data": "#2f7f7a", "Checks": "#4a5ba8", "Reports": "#9a6a1f",
    "Email": "#a24d6b", "Servers": "#4d6a35", "Files": "#6d7682", "Logic": "#7a4f9e", "Debug": "#b7791f",
}
USER_COLOR = "#b0563a"
# Each block's icon: white line drawings (24x24) from icons/: Tabler Icons (MIT, icons/LICENSE-tabler-icons.txt) and
# our own (duck, polar-bear, move-files, manual-start, inspect, if). A second drawing is a badge at its bottom right.
ICONS = Path(__file__).with_name("icons")
BLOCK_ICONS = {
    "trigger.manual": ("manual-start",), "trigger.schedule": ("calendar-time",),
    "logic.python": ("brand-python",), "logic.if": ("if",), "logic.wait": ("stopwatch",),
    "logic.ask_user": ("user-question",), LOOP: ("list", "badge-repeat"), "logic.inspect": ("inspect",),
    "logic.splitter": ("arrows-split",), "logic.merger": ("arrows-join",),
    "data.duckdb_sql": ("duck",), "data.polars": ("polar-bear",), "data.read_file": ("file-export",),
    "data.write_file": ("file-pencil",), "data.statistics": ("report-analytics",),
    "data.detect_anomalies": ("activity",),
    "checks.check_file": ("file-check",), "checks.check_table": ("list-check",), "db.run_query": ("database",),
    "files.copy": ("copy",), "files.move": ("move-files",), "files.delete": ("trash-x",),
    "servers.ssh_command": ("terminal-2",), "servers.start_process": ("satellite", "badge-player-play"),
    "servers.stop_process": ("satellite", "badge-player-stop"), "servers.health_check": ("stethoscope",),
    "servers.remote_extract": ("satellite", "badge-download"), SESSION: ("cookie",), API_REQUEST: ("api",),
    "reports.chart": ("chart-pie",), "reports.html_template": ("file-code",), "reports.excel": ("file-spreadsheet",),
    "email.send": ("mail-forward",),
}
USER_ICON = ("puzzle",)  # any block of the User Created group
TEXT_ICON = ("text-size",)  # Text, in the blocks tree
STATUS_COLORS = {"success": "#1f8a4c", "running": "#b7791f", "failed": "#c0392b",
                 "skipped": "#9aa6b2", "cancelled": "#9aa6b2", "retrying": "#b7791f"}
OFF = ("off", "off · not connected to a start block",  # (status, text under the block, its tooltip)
       "No start block leads to this block, so no run of the flow runs it. Connect it after a start block, or "
       "after a block that comes after one, to switch it on.")


def is_dark() -> bool:
    return QApplication.palette().window().color().lightness() < 128


@cache
def _drawing(name: str) -> QSvgRenderer:
    renderer = QSvgRenderer(str(ICONS / f"{name}.svg"))
    if not renderer.isValid():
        raise FileNotFoundError(f"The icon {ICONS / name}.svg is missing or not a drawing.")
    return renderer


def paint_icon(painter: QPainter, rect: QRectF, icon: tuple[str, ...], color: QColor):
    """A block's icon filling the square `rect`: a rounded square of its group colour with the white drawing on it,
    drawn at the painter's resolution (sharp on a scaled screen). A badge is drawn at half size at its bottom right, on
    a circle of the group colour (its file has lines twice as wide, so they come out as wide as the drawing's)."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(rect, rect.width() / 5.5, rect.width() / 5.5)
    pad = rect.width() / 11
    box = rect.adjusted(pad, pad, -pad, -pad)  # the drawing's 24x24
    u = box.width() / 24
    for i, name in enumerate(icon):
        if i:  # the badge
            centre = box.topLeft() + QPointF(18 * u, 18 * u)
            painter.drawEllipse(centre, 6.5 * u, 6.5 * u)
            box = QRectF(centre.x() - 6 * u, centre.y() - 6 * u, 12 * u, 12 * u)
        _drawing(name).render(painter, box)
    painter.restore()


def block_ports(registry, spec: dict) -> tuple[dict, dict]:
    """Ports for a block as configured; falls back to defaults while its config is incomplete."""
    cls = registry.get(spec.get("type"))
    if cls is None:
        return {}, {}
    config = spec.get("config") or {}
    try:
        return all_ports(cls, block_config(cls, BlockSpec(id="x", type=cls.type_id, config=dict(config))))
    except Exception:
        pass
    try:
        return all_ports(cls, {**{k: f.default for k, f in cls.config.items()}, **config})
    except Exception:
        return cls.inputs, cls.outputs


class PortItem(QGraphicsItem):
    def __init__(self, node: "NodeItem", name: str, is_output: bool, y: float):
        super().__init__(node)
        self.node, self.name, self.is_output = node, name, is_output
        self.setPos(NODE_W if is_output else 0, y)
        self.setAcceptHoverEvents(True)
        self._hover = False

    def boundingRect(self):
        r = PORT_R + 3
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter, option, widget=None):
        color = QColor("#c0392b") if self.name == ERROR_PORT else QColor("#8a9bb0")
        painter.setPen(QPen(color, 2))
        painter.setBrush(color if self._hover else QApplication.palette().base())
        r = PORT_R + (1 if self._hover else 0)
        if (self.name == AFTER and not self.is_output) or (self.name == DONE and self.is_output):
            # a diamond: it sets the order, it carries no data
            painter.drawPolygon([QPointF(0, -r - 1), QPointF(r + 1, 0), QPointF(0, r + 1), QPointF(-r - 1, 0)])
        else:
            painter.drawEllipse(QPointF(0, 0), r, r)

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()

    def anchor(self) -> QPointF:
        return self.scenePos()


class NodeItem(QGraphicsItem):
    has_ports = True  # (a Session's box has none: nothing is connected to it)

    def __init__(self, block_id: str, spec: dict, registry):
        super().__init__()
        self.block_id = block_id
        cls = registry.get(spec.get("type"))
        self.known = cls is not None
        self.type_title = (cls.title or cls.type_id) if cls else f"unknown: {spec.get('type')}"
        if cls is not None:
            from .panels import block_tooltip
            self.setToolTip(block_tooltip(cls))
        source = registry.sources.get(spec.get("type"), "")
        self.color = QColor(CATEGORY_COLORS.get(cls.category, USER_COLOR) if cls and source == "built-in" else USER_COLOR)
        # (an unknown type shows its colour with no icon: nothing is known about it)
        self.icon = () if cls is None else BLOCK_ICONS[cls.type_id] if source == "built-in" else USER_ICON
        inputs, outputs = block_ports(registry, spec) if self.has_ports else ({}, {})
        self.input_ports = inputs
        # `after` is the first row, drawn differently (it only sets when the block runs);
        # start blocks have neither it nor an error output.
        self.inputs = ([AFTER] if AFTER in inputs else []) + [n for n in inputs if n != AFTER]
        self.outputs = ([DONE] if DONE in outputs else []) + [n for n in outputs if n != DONE] + \
            ([] if (cls is not None and cls.is_trigger) or not self.has_ports else [ERROR_PORT])
        rows = max(len(self.inputs), len(self.outputs), 1)
        # A Schedule block has a line of its own for when it starts the flow next, a Wait block for how long it waits
        # (while it waits: the time left), above its result.
        self.counts_down = spec.get("type") in ("trigger.schedule", "logic.wait")
        self.height = HEADER_H + 6 + rows * ROW_H + (COUNTDOWN_H if self.counts_down else 0) + FOOTER_H
        self.status, self.status_text, self.old = None, "", False  # (see set_status)
        # (done, of, running) in the run on screen: a Loop's items, a Merger's lines (set to each line as it finishes)
        self.progress: tuple[int, int, int] | None = None
        self.countdown = ""
        seconds = (spec.get("config") or {}).get("seconds")
        self.resting = ("…" if spec.get("type") != "logic.wait" else "needs seconds" if seconds is None
                        else f"waits {written_seconds(seconds)} s")  # the line without a countdown
        self.ports: dict[tuple[str, bool], PortItem] = {}
        for i, name in enumerate(self.inputs):
            self.ports[(name, False)] = PortItem(self, name, False, HEADER_H + 6 + i * ROW_H + ROW_H / 2)
        for i, name in enumerate(self.outputs):
            self.ports[(name, True)] = PortItem(self, name, True, HEADER_H + 6 + i * ROW_H + ROW_H / 2)
        # Over the message at the bottom: hovering it shows all of the message (it is cut to fit).
        self.footer = QGraphicsRectItem(0, self.height - FOOTER_H, NODE_W, FOOTER_H, self)
        self.footer.setPen(Qt.NoPen)
        self.footer.setAcceptedMouseButtons(Qt.NoButton)
        self.countdown_area = QGraphicsRectItem(0, self.height - FOOTER_H - COUNTDOWN_H, NODE_W, COUNTDOWN_H, self)
        self.countdown_area.setPen(Qt.NoPen)
        self.countdown_area.setAcceptedMouseButtons(Qt.NoButton)
        self.countdown_area.setVisible(self.counts_down)
        ui = spec.get("ui") or {}
        self.setPos(ui.get("x", 0), ui.get("y", 0))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(1)

    def boundingRect(self):
        return QRectF(-4, -4, NODE_W + 8, self.height + 8)

    def drawn_rect(self) -> QRectF:
        """Where it is drawn on the canvas (without the room for its selection's outline)."""
        return QRectF(self.pos(), QSizeF(NODE_W, self.height))

    def set_status(self, status: str | None, text: str = "", tip: str = "", old: bool = False):
        """The run's result under the block: `text` (short), and `tip` (HTML) when hovering it. `old`: it was made
        before a block before it ran again, so it is drawn in grey, its border too."""
        self.status, self.status_text, self.old = status, text, old
        self.footer.setToolTip(tip if status else "")
        self.update()

    def set_countdown(self, text: str, tip: str = ""):
        self.countdown = text
        self.countdown_area.setToolTip(tip)
        self.update()

    def set_progress(self, progress: tuple[int, int, int] | None):
        self.progress = progress
        self.update()

    def paint(self, painter: QPainter, option, widget=None):
        pal = QApplication.palette()
        rect = QRectF(0, 0, NODE_W, self.height)
        muted = pal.placeholderText().color()
        border = muted if self.old else QColor(STATUS_COLORS.get(self.status, pal.mid().color().name()))
        pen = QPen(border, 2 if self.status in ("running", "success", "failed") else 1.2)
        if self.status in ("skipped", "cancelled"):
            pen.setStyle(Qt.DashLine)
        painter.setOpacity(0.6 if self.status in ("skipped", "cancelled") else 1.0)
        painter.setPen(pen)
        painter.setBrush(pal.base())
        painter.drawRoundedRect(rect, 7, 7)
        if self.isSelected():
            painter.setPen(QPen(pal.highlight().color(), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 9, 9)
        # header
        painter.setPen(QPen(pal.mid().color(), 1))
        painter.drawLine(QPointF(1, HEADER_H), QPointF(NODE_W - 1, HEADER_H))
        paint_icon(painter, QRectF(4, 3, 22, 22), self.icon, self.color)
        font = QFont(painter.font())
        font.setBold(True)
        font.setPointSizeF(max(font.pointSizeF(), 9) * 0.95)
        painter.setFont(font)
        painter.setPen(pal.text().color())
        title = painter.fontMetrics().elidedText(self.block_id, Qt.ElideRight, NODE_W - 40)
        painter.drawText(QRectF(32, 0, NODE_W - 38, HEADER_H), Qt.AlignVCenter, title)
        # ports
        font.setBold(False)
        font.setPointSizeF(font.pointSizeF() * 0.92)
        painter.setFont(font)
        for (name, is_output), port in self.ports.items():
            y = port.pos().y()
            painter.setPen(QColor("#c0392b") if name == ERROR_PORT else muted)
            box = QRectF(10, y - ROW_H / 2, NODE_W - 20, ROW_H)
            label = name
            if (name == AFTER and not is_output) or (name == DONE and is_output):
                font.setItalic(True)
                painter.setFont(font)
                label = "run after" if name == AFTER else "done"
            painter.drawText(box, Qt.AlignVCenter | (Qt.AlignRight if is_output else Qt.AlignLeft), label)
            if font.italic():
                font.setItalic(False)
                painter.setFont(font)
        if self.counts_down:  # when it starts the flow next; how long it waits, or the time left
            painter.setPen(pal.text().color())
            line = QRectF(10, self.height - FOOTER_H - COUNTDOWN_H, NODE_W - 20, COUNTDOWN_H)
            painter.drawText(line, Qt.AlignVCenter, painter.fontMetrics().elidedText(
                "⏱ " + (self.countdown or self.resting), Qt.ElideRight, int(line.width())))
        # footer: the run status (running its lines: its counter), else the type
        text = self.status_text if self.status else self.type_title
        if self.status == "running" and self.progress is not None:
            text = counter_text(self.progress)
        painter.setPen(QColor(STATUS_COLORS[self.status]) if self.status in STATUS_COLORS and self.status != "skipped"
                       and not self.old else muted)
        footer = QRectF(10, self.height - FOOTER_H, NODE_W - 20, FOOTER_H - 4)
        painter.drawText(footer, Qt.AlignVCenter, painter.fontMetrics().elidedText(text, Qt.ElideRight, int(footer.width())))
        painter.setOpacity(1.0)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            self.scene().update_edges(self.block_id)
        return super().itemChange(change, value)


def counter_text(progress: tuple[int, int, int]) -> str:
    """A Loop's or a Merger's counter while it runs: done/N · k running (none running: done/N done)."""
    done, of, running = progress
    return f"{done}/{of} · {running} running" if running else f"{done}/{of} done"


def loop_colors() -> tuple[QColor, QColor]:
    """A Loop box's grey and its shadow: a step lighter than the canvas in a dark theme, darker in a light one, so it
    stands out from it either way; the shadow darker than both."""
    canvas = canvas_color()
    return (canvas.lighter(150), QColor(0, 0, 0, 150)) if is_dark() else (canvas.darker(108), QColor(0, 0, 0, 70))


LOOP_PORT_HELP = {
    ITEMS: "The list whose items it runs the blocks inside it for (a table: one item per row, as a dict of its "
           "columns).",
    ITEM: "Inside the loop: the item of the run of its blocks. Connect it to the blocks inside.",
    INDEX: "Inside the loop: the item's position, 1 to N. Connect it to the blocks inside.",
    RESULT: "Inside the loop: what each item gives back. Connect the block inside that gives it here.",
    RESULTS: "Every item's result, in the items' order (not the order they finished), once every item has run.",
}


class LoopItem(NodeItem):
    """A Loop on the canvas: a grey box with a darker shadow around the blocks inside it (fit), its icon and name at
    its top, while it runs its counter (done/N · k running) and progress bar, after it its result at its bottom. Its
    own ports are on its sides: items and run after on the left, results, done and error on the right; item and index
    (outputs) and result (an input), for the blocks inside, are inside it. Grab it by its top to move it, with its
    blocks; drag its bottom-right corner to resize it, never smaller than its blocks."""

    def __init__(self, block_id: str, spec: dict, registry):
        super().__init__(block_id, spec, registry)
        ui = spec.get("ui") or {}
        self.stored = QRectF(0, 0, _size(ui.get("width"), LOOP_W), _size(ui.get("height"), LOOP_H))  # its own size
        self.needed: QRectF | None = None  # what its blocks need of it
        self._resizing = None
        self._was = self.pos()
        self.setAcceptHoverEvents(True)
        self.setZValue(-1)
        self.fit([])

    def fit(self, inside: list[QRectF]):
        """Its box: its own size, grown to hold the blocks inside it (`inside`: where they are drawn on the canvas)
        with room around them; its ports and result line on it."""
        self.prepareGeometryChange()
        self.needed = None
        for r in inside:
            room = r.translated(-self.pos()).adjusted(-LOOP_SIDE, -LOOP_TOP, LOOP_SIDE, LOOP_BOTTOM)
            self.needed = room if self.needed is None else self.needed.united(room)
        self.rect = self.stored if self.needed is None else self.stored.united(self.needed)
        self._place()

    def _place(self):
        r = self.rect

        def row(i: int) -> float:
            return r.top() + HEADER_H + 10 + i * ROW_H + ROW_H / 2

        left = [n for n in self.inputs if n != RESULT] + [n for n in self.outputs if n in (ITEM, INDEX)]
        right = [n for n in self.outputs if n not in (ITEM, INDEX)] + [n for n in self.inputs if n == RESULT]
        for i, name in enumerate(left):
            inner = name in (ITEM, INDEX)
            self.ports[(name, inner)].setPos(r.left() + (INNER_X if inner else 0), row(i))
        for i, name in enumerate(right):
            inner = name == RESULT
            self.ports[(name, not inner)].setPos(r.right() - (INNER_X if inner else 0), row(i))
        self.footer.setRect(r.left(), r.bottom() - FOOTER_H, r.width() - GRIP, FOOTER_H)
        self.countdown_area.setRect(QRectF())

    def drawn_rect(self) -> QRectF:
        return self.rect.translated(self.pos())

    def boundingRect(self):
        return self.rect.adjusted(-4, -4, SHADOW + 4, SHADOW + 4)

    def _header(self) -> QRectF:
        return QRectF(self.rect.left(), self.rect.top(), self.rect.width(), HEADER_H + 8)

    def _grip(self) -> QRectF:
        return QRectF(self.rect.right() - GRIP, self.rect.bottom() - GRIP, GRIP, GRIP)

    def shape(self) -> QPainterPath:
        """Its top (to grab, move or right-click it) and its corner (to resize it): elsewhere in it, the canvas's
        (selecting blocks inside it by dragging round them)."""
        path = QPainterPath()
        path.addRect(self._header())
        path.addRect(self._grip())
        return path

    def paint(self, painter: QPainter, option, widget=None):
        pal = QApplication.palette()
        r, (fill, shade) = self.rect, loop_colors()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(shade)
        painter.drawRoundedRect(r.translated(SHADOW, SHADOW), 9, 9)
        muted = pal.placeholderText().color()
        pen = QPen(muted if self.old else QColor(STATUS_COLORS.get(self.status, pal.mid().color().name())),
                   2 if self.status in ("running", "success", "failed") else 1.2)
        if self.status in ("skipped", "cancelled"):
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(r, 9, 9)
        if self.isSelected():
            painter.setPen(QPen(pal.highlight().color(), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(r.adjusted(-3, -3, 3, 3), 11, 11)
        paint_icon(painter, QRectF(r.left() + 6, r.top() + 3, 22, 22), self.icon, self.color)
        font = QFont(painter.font())
        font.setBold(True)
        font.setPointSizeF(max(font.pointSizeF(), 9) * 0.95)
        painter.setFont(font)
        painter.setPen(pal.text().color())
        counter = counter_text(self.progress) if self.progress is not None else ""
        counter_w = painter.fontMetrics().horizontalAdvance(counter) if counter else 0
        name = QRectF(r.left() + 34, r.top(), max(r.width() - 48 - counter_w, 20), HEADER_H)
        painter.drawText(name, Qt.AlignVCenter, painter.fontMetrics().elidedText(
            self.block_id, Qt.ElideRight, int(name.width())))
        if counter:
            painter.drawText(QRectF(r.right() - 10 - counter_w, r.top(), counter_w, HEADER_H),
                             Qt.AlignVCenter | Qt.AlignRight, counter)
            bar = QRectF(r.left() + 10, r.top() + HEADER_H + 1, r.width() - 20, 4)
            painter.setPen(Qt.NoPen)
            painter.setBrush(shade)  # (its empty part: darker than the box in either theme)
            painter.drawRoundedRect(bar, 2, 2)
            done, of, _ = self.progress
            if of:
                painter.setBrush(QColor(STATUS_COLORS.get(self.status if self.status != "skipped" else "running",
                                                          STATUS_COLORS["running"])))
                painter.drawRoundedRect(QRectF(bar.topLeft(), QSizeF(bar.width() * done / of, bar.height())), 2, 2)
        font.setBold(False)
        font.setPointSizeF(font.pointSizeF() * 0.92)
        for (port_name, is_output), port in self.ports.items():
            inner = port_name in (ITEM, INDEX, RESULT)
            painter.setPen(QColor("#c0392b") if port_name == ERROR_PORT else muted)
            font.setItalic(port_name in (AFTER, DONE) and not inner)
            painter.setFont(font)
            label = {AFTER: "run after"}.get(port_name, port_name) if not is_output else port_name
            x, y = port.pos().x(), port.pos().y()
            if inner and is_output:  # item, index: their names left of them
                box, align = QRectF(r.left() + 6, y - ROW_H / 2, INNER_X - 14, ROW_H), Qt.AlignRight
            elif inner:  # result: right of it
                box, align = QRectF(x + 8, y - ROW_H / 2, INNER_X - 10, ROW_H), Qt.AlignLeft
            elif is_output:
                box, align = QRectF(x - NODE_W, y - ROW_H / 2, NODE_W - 10, ROW_H), Qt.AlignRight
            else:
                box, align = QRectF(x + 10, y - ROW_H / 2, NODE_W - 20, ROW_H), Qt.AlignLeft
            painter.drawText(box, Qt.AlignVCenter | align, label)
        font.setItalic(False)
        painter.setFont(font)
        if self.status not in (None, "running"):  # (while it runs, the counter says it)
            painter.setPen(QColor(STATUS_COLORS[self.status]) if self.status in STATUS_COLORS
                           and self.status != "skipped" and not self.old else muted)
            footer = QRectF(r.left() + 10, r.bottom() - FOOTER_H, r.width() - 20 - GRIP, FOOTER_H - 4)
            painter.drawText(footer, Qt.AlignVCenter, painter.fontMetrics().elidedText(
                self.status_text, Qt.ElideRight, int(footer.width())))
        painter.setPen(QPen(muted, 1.2))  # the corner to resize it
        for k in (4, 8):
            painter.drawLine(QPointF(r.right() - 3 - k, r.bottom() - 3), QPointF(r.right() - 3, r.bottom() - 3 - k))

    def hoverMoveEvent(self, event):
        self.setCursor(Qt.SizeFDiagCursor if self._grip().contains(event.pos()) else Qt.ArrowCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._grip().contains(event.pos()):
            self._resizing = (event.scenePos(), QRectF(self.rect))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            self._resize_to(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resize_to(event.scenePos())
            self._resizing = None
            event.accept()
            # Its size from where the flow file has it (its top-left there stays put: the canvas sees no move).
            self.scene().doc.set_box(self.block_id, self.rect.right(), self.rect.bottom())
            return
        super().mouseReleaseEvent(event)

    def _resize_to(self, at: QPointF):
        """Its bottom-right corner where the corner dragged from `pressed` is now: never smaller than its blocks need,
        nor than the least a box can be."""
        pressed, start = self._resizing
        right = max(start.right() + at.x() - pressed.x(), start.left() + LOOP_MIN_W)
        bottom = max(start.bottom() + at.y() - pressed.y(), start.top() + LOOP_MIN_H)
        if self.needed is not None:
            right, bottom = max(right, self.needed.right()), max(bottom, self.needed.bottom())
        self.prepareGeometryChange()
        self.rect = QRectF(start.topLeft(), QPointF(right, bottom))
        self._place()
        self.update()

    def itemChange(self, change, value):
        """Moving it moves the blocks inside it (those not selected: those move by themselves)."""
        if change == QGraphicsItem.ItemPositionChange:
            self._was = self.pos()
        elif change == QGraphicsItem.ItemPositionHasChanged and self.scene() is not None:
            moved = self.pos() - self._was
            for node in self.scene().inside(self.block_id):
                if not node.isSelected():
                    node.setPos(node.pos() + moved)
        return super().itemChange(change, value)


class SessionItem(LoopItem):
    """A Session on the canvas: a box drawn as a Loop's, around the blocks inside it, its icon and name at its top; it
    has no ports, counter nor progress bar (it does not run: the API Request blocks inside it share its cookies)."""

    has_ports = False


def _size(value, default: float) -> float:
    """A Loop's or a Session's width or height as its flow file has it (not a number there: as it is added)."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else default


def _curve(a: QPointF, b: QPointF) -> QPainterPath:
    path = QPainterPath(a)
    dx = max(abs(b.x() - a.x()) / 2, 40)
    path.cubicTo(QPointF(a.x() + dx, a.y()), QPointF(b.x() - dx, b.y()), b)
    return path


def canvas_color() -> QColor:
    """The colour of the canvas behind the blocks."""
    window = QApplication.palette().window().color()
    return window.lighter(103) if not is_dark() else window.darker(110)


class LineNumber(QGraphicsItem):
    """The number of a line, on its connection from a Splitter that runs its lines one after another: in the text
    colour, on a round patch of the canvas's colour (the connection does not run through it)."""

    R = 9

    def __init__(self, edge: "EdgeItem", number: int):
        super().__init__(edge)
        self.number = str(number)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self):
        return QRectF(-self.R - 1, -self.R - 1, 2 * self.R + 2, 2 * self.R + 2)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#8a9bb0"), 1.5))
        painter.setBrush(canvas_color())
        painter.drawEllipse(QPointF(0, 0), self.R, self.R)
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QApplication.palette().text().color())
        painter.drawText(QRectF(-self.R, -self.R, 2 * self.R, 2 * self.R), Qt.AlignCenter, self.number)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, src_port: PortItem, dst_port: PortItem, number: int | None = None):
        super().__init__()
        self.src_port, self.dst_port = src_port, dst_port
        self.text = edge_text(src_port.node.block_id, src_port.name, dst_port.node.block_id, dst_port.name)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setZValue(0)
        self.number = None if number is None else LineNumber(self, number)
        self.update_path()

    def update_path(self):
        self.setPath(_curve(self.src_port.anchor(), self.dst_port.anchor()))
        if self.number is not None:  # near the Splitter, where the connections have parted
            path = self.path()
            self.number.setPos(path.pointAtPercent(path.percentAtLength(min(160, 0.4 * path.length()))))

    def shape(self):
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        return stroker.createStroke(self.path())

    def paint(self, painter, option, widget=None):
        color = QApplication.palette().highlight().color() if self.isSelected() else QColor("#8a9bb0")
        pen = QPen(color, 2.5 if self.isSelected() else 2)
        if self.src_port.name == ERROR_PORT:
            pen.setColor(QColor("#c0392b") if not self.isSelected() else color)
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self.path())


def value_html(text: str) -> str:
    """A value as shown when hovering (cut when long): escaped, to go inside <pre>."""
    return html.escape(text if len(text) <= 4000 else text[:4000] + "…", quote=False)  # e.g. a wide table's rows


def _short(text: str, n: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


NOTE_STYLES = {"title": 22, "heading": 16, "normal": 11, "small": 9}  # point sizes


def note_font(note: dict) -> QFont:
    """The font of a text on the canvas: its font (any installed one; else the usual), style and emphasis."""
    font = QFont(note["font"]) if note.get("font") else QFont(QApplication.font())
    font.setPointSizeF(float(note.get("size") or NOTE_STYLES.get(note.get("style") or "normal", NOTE_STYLES["normal"])))
    font.setBold(bool(note.get("bold")) or note.get("style") == "title")
    font.setItalic(bool(note.get("italic")))
    font.setUnderline(bool(note.get("underline")))
    return font


NOTE_SIZES = (6.0, 300.0)  # points: the smallest and largest text


class NoteItem(QGraphicsTextItem):
    """Text on the canvas (a title, a remark). Double-click to write in it, drag to move it. When it is
    selected: drag its corner to make the text bigger or smaller, its right edge to change where its
    lines wrap. Properties has its style, size and font."""

    def __init__(self, note_id: str, note: dict):
        super().__init__(str(note.get("text", "")))
        self.note_id = note_id
        self.setFont(note_font(note))
        self.setDefaultTextColor(QApplication.palette().text().color())
        if note.get("width"):
            self.setTextWidth(float(note["width"]))  # its lines wrap at this width
        self.setPos(note.get("x", 0), note.get("y", 0))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(0)
        self.setToolTip("Text: double-click to write in it. Selected: drag its corner to make it bigger or "
                        "smaller, its right edge to change where its lines wrap.")
        self._resizing = None  # "scale" (the corner) or "wrap" (the right edge) while dragging one

    @property
    def editing(self) -> bool:
        return bool(self.textInteractionFlags() & Qt.TextEditable)

    def _text_rect(self) -> QRectF:
        return super().boundingRect()

    def boundingRect(self) -> QRectF:
        # Room for the grips, just outside the text (so they never overlap, however short the text).
        return self._text_rect().adjusted(0, 0, HANDLE, HANDLE)

    def _grips_shown(self) -> bool:
        return self.isSelected() and not self.editing

    def shape(self) -> QPainterPath:
        """The text, and its grips while they are shown (else the space next to it is the canvas's)."""
        path = QPainterPath()
        path.addRect(self._text_rect())
        if self._grips_shown():
            path.addRect(self._handle())
            path.addRect(self._edge())
        return path

    def contains(self, point) -> bool:
        return self.shape().contains(point)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedChange:
            self.prepareGeometryChange()  # its grips (and what they let you grab) come and go
        return super().itemChange(change, value)

    def _handle(self) -> QRectF:
        """Past the bottom-right corner: makes the text bigger or smaller."""
        r = self._text_rect()
        return QRectF(r.right(), r.bottom(), HANDLE, HANDLE)

    def _edge(self) -> QRectF:
        """Right of the text, halfway down: where its lines wrap."""
        r = self._text_rect()
        return QRectF(r.right(), r.center().y() - HANDLE / 2, HANDLE, HANDLE)

    def _grip_at(self, pos) -> str | None:
        if not self._grips_shown():
            return None
        return "scale" if self._handle().contains(pos) else "wrap" if self._edge().contains(pos) else None

    def paint(self, painter: QPainter, option, widget=None):
        selected = self.isSelected()
        option.state &= ~QStyle.State_Selected  # its own outline, below
        option.state &= ~QStyle.State_HasFocus
        if self.defaultTextColor() != QApplication.palette().text().color():  # the theme changed since
            self.setDefaultTextColor(QApplication.palette().text().color())
        super().paint(painter, option, widget)
        if selected or self.editing:
            color = QApplication.palette().highlight().color()
            painter.setPen(QPen(color, 1.5, Qt.SolidLine if self.editing else Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._text_rect().adjusted(1, 1, -1, -1))
            if not self.editing:
                painter.setBrush(color)
                painter.drawRect(self._handle())
                painter.drawRect(self._edge())

    # --- writing in it on the canvas ---------------------------------------------------

    def mouseDoubleClickEvent(self, event):
        if not self.editing:
            self.start_editing()
        super().mouseDoubleClickEvent(event)

    def start_editing(self):
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus(Qt.MouseFocusReason)
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.clearFocus()  # done writing
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if self.editing:
            # Later: saving rebuilds the canvas, which must not delete this item while Qt is still using it.
            self.stop_editing(now=False)

    def stop_editing(self, now: bool):
        """Done writing: what you wrote goes into the flow (now, or just after the event being handled)."""
        if not self.editing:
            return
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self.update()
        scene = self.scene()
        if scene is not None:
            scene.write_note_text(self.note_id, self.toPlainText(), now)

    # --- resizing ------------------------------------------------------------------------

    def hoverMoveEvent(self, event):
        grip = self._grip_at(event.pos())
        self.setCursor({"scale": Qt.SizeFDiagCursor, "wrap": Qt.SizeHorCursor}.get(grip, Qt.ArrowCursor))
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        grip = self._grip_at(event.pos()) if event.button() == Qt.LeftButton else None
        if grip:
            self._resizing = grip
            rect = self._text_rect()
            self._start = (event.pos(), rect.width(), rect.height(), self.font().pointSizeF(), self.textWidth())
            event.accept()
            return
        super().mousePressEvent(event)

    def _resize_to(self, pos):
        # From where the drag began: a click without moving changes nothing.
        pressed, width, height, size, wrap = self._start
        dx, dy = pos.x() - pressed.x(), pos.y() - pressed.y()
        if self._resizing == "wrap":
            if dx:
                self.setTextWidth(max(40.0, width + dx))
            return
        # The corner scales the text (and where it wraps, so its lines stay the same), following the
        # direction you dragged most: right or down for bigger, left or up for smaller.
        scale = max((width + dx) / width if abs(dx) / width >= abs(dy) / height else (height + dy) / height, 0.01)
        scale = min(max(scale, NOTE_SIZES[0] / size), NOTE_SIZES[1] / size)
        font = self.font()
        font.setPointSizeF(size * scale)
        self.setFont(font)
        if wrap > 0:
            self.setTextWidth(wrap * scale)

    def mouseMoveEvent(self, event):
        if self._resizing:
            self._resize_to(event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resize_to(event.pos())
            event.accept()
            values = {"width": round(self.textWidth())} if self.textWidth() > 0 else {}
            if self._resizing == "scale" and self.font().pointSizeF() != self._start[3]:
                font = self.font()
                font.setPointSizeF(round(font.pointSizeF(), 1))  # as it is saved
                self.setFont(font)
                values["size"] = font.pointSizeF()
            self._resizing = None
            self.scene().doc.set_note_values(self.note_id, values)
            return
        super().mouseReleaseEvent(event)


class FlowScene(QGraphicsScene):
    # Each with whether it was chosen: False when the canvas is only drawn again after a change to the flow.
    block_selected = Signal(object, bool)  # block id or None
    note_selected = Signal(str, bool)  # a text on the canvas, when it alone is selected
    block_menu = Signal(str, object)  # block id, screen position (right-click on a block)
    canvas_menu = Signal(object, object)  # scene position, screen position (right-click on empty canvas)

    def __init__(self, doc: FlowDocument, registry):
        super().__init__()
        self.doc, self.registry = doc, registry
        self.nodes: dict[str, NodeItem] = {}
        self.note_items: dict[str, NoteItem] = {}
        self.countdowns: dict[str, tuple[str, str]] = {}  # Schedule or Wait block -> (text, tip)
        self.edge_items: list[EdgeItem] = []
        self.statuses: dict[str, tuple[str, str, str]] = {}  # block -> (status, text, tip)
        # block -> (the block before it that ran again, in which run): its result on the canvas was made before that
        self.old: dict[str, tuple[str, int]] = {}
        # A Loop's items, a Merger's lines (set to each line as it finishes): (done, of, running), in the run on screen
        self.progress: dict[str, tuple[int, int, int]] = {}
        self.off: set[str] = set()  # blocks connected to no start block (in a flow with start blocks)
        self.values: dict[str, dict[str, str]] = {}  # block -> port -> text, from the run on screen
        self._drag_from: PortItem | None = None
        self._drag_line: QGraphicsPathItem | None = None
        self._rebuilding = False
        # Rebuilding deletes every item; never do that while Qt is still delivering a mouse event
        # to one of them (it would use a deleted item and crash). Such rebuilds wait until after.
        self._in_mouse_event = False
        self._in_selection = False  # telling others what is selected (they may change the flow meanwhile)
        self._rebuild_pending = False
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self.selectionChanged.connect(self._on_selection)
        doc.changed.connect(self.rebuild)
        self.rebuild()

    # --- building from the document ---------------------------------------------

    def rebuild(self):
        if self._in_mouse_event or self._in_selection:
            if not self._rebuild_pending:
                self._rebuild_pending = True
                QTimer.singleShot(0, self, self._deferred_rebuild)
            return
        renamed, self.doc.renamed = self.doc.renamed, {}
        selected = {renamed.get(b, b) for b in self.selected_block_ids()}
        selected_notes = set(self.selected_note_ids())
        # Text being written on the canvas when something else changes the flow: kept, written just after.
        writing = [(i.note_id, i.toPlainText()) for i in self.note_items.values() if i.editing]
        for item in self.note_items.values():
            item.setTextInteractionFlags(Qt.NoTextInteraction)  # (no writing on its way out)
        for note_id, text in writing:
            self.write_note_text(note_id, text, now=False)
        self._rebuilding = True
        self.clear()
        self.nodes, self.edge_items, self.note_items = {}, [], {}
        lines = self.doc.lines(self.registry)
        self.off = ({b for b, spec in self.doc.blocks.items() if spec.get("type") != SESSION}  # (a Session runs in none)
                    - set().union(*lines.values()) if lines else set())
        for note_id, note in self.doc.notes.items():
            item = NoteItem(note_id, note)
            self.addItem(item)
            self.note_items[note_id] = item
            item.setSelected(note_id in selected_notes)
        for block_id, spec in self.doc.blocks.items():
            node = {LOOP: LoopItem, SESSION: SessionItem}.get(spec.get("type"), NodeItem)(block_id, spec, self.registry)
            self.addItem(node)
            self.nodes[block_id] = node
            node.set_status(*self._shown_status(block_id))
            if block_id in self.countdowns:
                node.set_countdown(*self.countdowns[block_id])
            if block_id in self.progress:
                node.set_progress(self.progress[block_id])
            node.setSelected(block_id in selected)
        # Each Loop's box round its blocks, the innermost first (a Loop inside it is one of them), drawn over the boxes
        # around it.
        loops = [b for b, node in self.nodes.items() if isinstance(node, LoopItem)]
        for loop in sorted(loops, key=lambda b: len(self.doc.containers(b)), reverse=True):
            self.nodes[loop].fit([node.drawn_rect() for node in self.inside(loop)])
            self.nodes[loop].setZValue(-1 + 0.1 * min(len(self.doc.containers(loop)), 9))
        numbered = self.doc.numbered_lines(self.registry)
        for src, sp, dst, dp in self.doc.edges():
            a = self.nodes.get(src) and self.nodes[src].ports.get((sp, True))
            b = self.nodes.get(dst) and self.nodes[dst].ports.get((dp, False))
            if a and b:
                edge = EdgeItem(a, b, numbered.get(src, {}).get(dst) if sp == LINES else None)
                self.addItem(edge)
                self.edge_items.append(edge)
        self._rebuilding = False
        self.refresh_tooltips()
        self._on_selection(chosen=False)

    # --- values from the run on screen, shown when hovering ----------------------------

    def set_values(self, block_id: str, values: dict):
        """What a block's outputs produced in the run on screen: {port: readable text}."""
        self.values[block_id] = dict(values)
        self.refresh_tooltips()

    def clear_values(self):
        self.values = {}
        self.refresh_tooltips()

    def _value(self, block_id, port):
        text = self.values.get(block_id, {}).get(port)
        return None if text is None else value_html(text)

    def refresh_tooltips(self):
        for node in self.nodes.values():
            for (name, is_output), port in node.ports.items():
                port.setToolTip(self._port_tooltip(node, name, is_output))
        for edge in self.edge_items:
            value = self._value(edge.src_port.node.block_id, edge.src_port.name)
            edge.setToolTip(f"<b>{html.escape(edge.text)}</b>" + (f"<pre>{value}</pre>" if value is not None else ""))

    def _port_tooltip(self, node, name, is_output) -> str:
        head = f"<b>{html.escape(node.block_id)}.{html.escape(name)}</b>"
        if isinstance(node, LoopItem) and name in LOOP_PORT_HELP:
            head += f"<br>{html.escape(LOOP_PORT_HELP[name])}"
        if name == AFTER and not is_output:
            head += ("<br>Run after: connect an output of another block (or a start block's 'start') here to make "
                     "this block wait for it. Its value is not used.")
        elif name == DONE and is_output:
            head += ("<br>Done: fires when this block has finished successfully, even if it produced none of its other "
                     "outputs. Connect it to another block's 'run after'. No data goes out through here.")
        if is_output:
            value = self._value(node.block_id, name)
            return head + (f"<pre>{value}</pre>" if value is not None else "<br><i>No value in the run on screen.</i>")
        sources = [(s, sp) for s, sp, d, dp in self.doc.edges() if d == node.block_id and dp == name]
        if not sources:
            return head + "<br><i>Not connected.</i>"
        parts = []
        for src, sp in sources:
            value = self._value(src, sp)
            parts.append(f"from <b>{html.escape(src)}.{html.escape(sp)}</b>"
                         + (f"<pre>{value}</pre>" if value is not None else ": <i>nothing arrived in the run on screen</i>"))
        return head + "<br>" + "<br>".join(parts)

    def inside(self, loop: str) -> list[NodeItem]:
        """The blocks (and Loops, Sessions) directly inside a Loop or a Session."""
        return [self.nodes[b] for b, spec in self.doc.blocks.items() if spec.get("inside") == loop and b in self.nodes]

    def loop_at(self, at: QPointF, leaving=()) -> str | None:
        """The innermost Loop or Session whose box holds the point `at` (none of `leaving`)."""
        found = [(item.rect.width() * item.rect.height(), b) for b, item in self.nodes.items()
                 if isinstance(item, LoopItem) and b not in leaving and item.drawn_rect().contains(at)]
        return min(found)[1] if found else None

    def set_progress(self, block_id: str, done: int, of: int, running: int):
        """A Loop's items, or a Merger's lines (set to each line as it finishes), in the run on screen: how many are
        done, of how many, and how many run now."""
        self.progress[block_id] = (done, of, running)
        if block_id in self.nodes:
            self.nodes[block_id].set_progress(self.progress[block_id])

    def update_edges(self, block_id: str):
        for edge in self.edge_items:
            if block_id in (edge.src_port.node.block_id, edge.dst_port.node.block_id):
                edge.update_path()

    def set_status(self, block_id: str, status: str | None, text: str = "", tip: str = ""):
        """A block's result on the canvas: `text` under it (short), `tip` (HTML) when hovering that."""
        if status is None:
            self.statuses.pop(block_id, None)
            self.old.pop(block_id, None)
            if self.progress.pop(block_id, None) is not None and block_id in self.nodes:
                self.nodes[block_id].set_progress(None)
        else:
            self.statuses[block_id] = (status, text, tip or html.escape(text))
        if block_id in self.nodes:
            self.nodes[block_id].set_status(*self._shown_status(block_id))

    def set_old(self, old: dict[str, tuple[str, int]]):
        """The results on the canvas made before a block before them ran again: {block: (that block, its run)}.
        Each is marked old; the others no longer are."""
        changed = {b for b in old.keys() | self.old.keys() if old.get(b) != self.old.get(b)}
        self.old = dict(old)
        for block_id in changed & self.nodes.keys():
            self.nodes[block_id].set_status(*self._shown_status(block_id))

    def _shown_status(self, block_id: str) -> tuple[str | None, str, str, bool]:
        """Under a block: its result on the canvas, and whether it is old (then it says so, and hovering it why);
        without one, that it is off if no start block leads to it (not a result: it goes away once the block is
        connected to a line)."""
        if block_id not in self.statuses:
            return (*(OFF if block_id in self.off else (None, "", "")), False)
        status, text, tip = self.statuses[block_id]
        if block_id not in self.old:
            return status, text, tip, False
        by, run_id = self.old[block_id]
        return status, "old · " + text, (f"<p><b>Old:</b> made before {html.escape(by)} ran again (run #{run_id}). "
                                         "Right-click → Run only this block to bring it up to date.</p>" + tip), True

    def show_result(self, block_id: str, info: dict):
        """A block's result in a run (an event of the run, or its row in the history), made at info["finished"]: under
        it, and on its outputs when hovering them. A block inside a Loop, over its items (info["items"]; or after a
        Merger set to each line as it finishes, over its lines' runs): how they ended (✓ 4 ✕ 1) under it; hovering
        it, what the last item (or line) to end gave, saying which it was."""
        items = info.get("items")
        status = items["status"] if items else info.get("status")
        values = dict(info.get("previews") or {})
        if status == "success":
            values["done"] = "finished successfully"
        elif status == "failed" and info.get("error"):
            values["error"] = info["error"]
        self.set_values(block_id, values)
        attempts = info.get("attempts") or 0
        retried = f" · retried {attempts - 1}×" if attempts > 1 else ""
        if status == "success":
            text = "✓ " + _short(info.get("summary") or "done", 48) + retried
            # Hovering it shows each output as the connections do (e.g. JSON laid out).
            tip = "".join(f"<b>{html.escape(port)}</b><pre>{value_html(value)}</pre>"
                          for port, value in (info.get("previews") or {}).items())
            tip = (tip or html.escape(info.get("summary") or "done")) + html.escape(retried)
        elif status == "failed":
            text = "✕ " + _short(info.get("error") or "failed", 48)
            tip = f"<pre>{value_html(info.get('error') or 'failed')}</pre>"
        elif status == "skipped" and info.get("summary"):
            text, tip = f"skipped · {info['summary']}", html.escape(f"skipped: {info['summary']}")
        else:
            text = tip = status
        if items:
            text = items["ended"]
            run_of = "line" if items["last"].startswith("[line ") else "item"  # (its tag: [line load], [item 2/5])
            tip = (f"<p><b>{html.escape(items['ended'])}</b> · the last {run_of} to end: "
                   f"{html.escape(items['last'])}, {html.escape(status)}</p>{tip}")
        if info.get("finished"):  # (not known if the run process died mid-block)
            tip += ('<p align="right" style="color:gray; font-size:small">results from '
                    f'{info["finished"].replace("T", " ")[:19]}</p>')
        self.set_status(block_id, info.get("status"), text, tip)

    def set_countdown(self, block_id: str, text: str, tip: str = ""):
        """Under a Schedule block: when it starts the flow next; under a Wait block: the time left while it waits ("":
        how long it waits)."""
        self.countdowns[block_id] = (text, tip)
        if block_id in self.nodes and (self.nodes[block_id].countdown, self.nodes[block_id].countdown_area.toolTip()) != (text, tip):
            self.nodes[block_id].set_countdown(text, tip)

    def clear_statuses(self):
        for block_id in list(self.statuses) + list(self.progress):
            self.set_status(block_id, None)
        self.clear_values()

    # --- selection ----------------------------------------------------------------

    def selected_block_ids(self) -> list[str]:
        return [i.block_id for i in self.selectedItems() if isinstance(i, NodeItem)]

    def write_note_text(self, note_id: str, text: str, now: bool):
        """Put text written on the canvas into the flow (nothing if it is empty or unchanged)."""
        def write():
            if note_id in self.doc.notes and text.strip() and text != self.doc.notes[note_id].get("text"):
                self.doc.set_note_value(note_id, "text", text)
        if now:
            write()
        else:
            QTimer.singleShot(0, self, write)

    def commit_editing(self):
        """Before saving, running or closing: what is being written on the canvas goes into the flow."""
        for item in list(self.note_items.values()):
            if item.editing:
                item.stop_editing(now=True)
                return  # (writing it rebuilt the canvas)

    def add_note_at(self, at: QPointF) -> str:
        """Put a text on the canvas and select it (to write it in Properties, or double-click it)."""
        note_id = self.doc.add_note(at.x(), at.y())
        if note_id in self.note_items:  # (a rebuild during a mouse event happens just after)
            self.clearSelection()
            self.note_items[note_id].setSelected(True)
        else:
            QTimer.singleShot(0, self, lambda: note_id in self.note_items and (
                self.clearSelection(), self.note_items[note_id].setSelected(True)))
        return note_id

    def selected_note_ids(self) -> list[str]:
        return [i.note_id for i in self.selectedItems() if isinstance(i, NoteItem)]

    def _on_selection(self, chosen=True):
        if self._rebuilding:
            return
        ids, notes = self.selected_block_ids(), self.selected_note_ids()
        # Showing the selection saves the field being edited, which can change the flow: its rebuild
        # waits until Qt is done with this selection change (it would delete the items in it).
        self._in_selection = True
        try:
            self.block_selected.emit(ids[0] if len(ids) == 1 else None, chosen)
            if not ids and len(notes) == 1:
                self.note_selected.emit(notes[0], chosen)
        finally:
            self._in_selection = False

    def delete_selected(self):
        blocks, notes = self.selected_block_ids(), self.selected_note_ids()
        edges = [i.text for i in self.selectedItems() if isinstance(i, EdgeItem)]
        if blocks or edges or notes:
            self.doc.remove(blocks, edges, notes)

    # --- connecting ports and moving nodes ---------------------------------------

    def _port_at(self, pos: QPointF) -> PortItem | None:
        for item in self.items(pos):
            if isinstance(item, PortItem):
                return item
        return None

    def _deferred_rebuild(self):
        self._rebuild_pending = False
        self.rebuild()

    def _mouse(handler):  # noqa: N805 - a decorator used inside the class body
        def wrapped(self, event):
            self._in_mouse_event = True
            try:
                handler(self, event)
            finally:
                self._in_mouse_event = False
        return wrapped

    @_mouse
    def mousePressEvent(self, event):
        port = self._port_at(event.scenePos()) if event.button() == Qt.LeftButton else None
        if port is not None:
            self._drag_from = port
            self._drag_line = QGraphicsPathItem()
            self._drag_line.setPen(QPen(QApplication.palette().highlight().color(), 2, Qt.DashLine))
            self._drag_line.setZValue(2)
            self.addItem(self._drag_line)
            event.accept()
            return
        super().mousePressEvent(event)

    @_mouse
    def mouseMoveEvent(self, event):
        if self._drag_from is not None:
            a, b = self._drag_from.anchor(), event.scenePos()
            self._drag_line.setPath(_curve(a, b) if self._drag_from.is_output else _curve(b, a))
            event.accept()
            return
        super().mouseMoveEvent(event)

    @_mouse
    def mouseReleaseEvent(self, event):
        if self._drag_from is not None:
            start, target = self._drag_from, self._port_at(event.scenePos())
            self.removeItem(self._drag_line)
            self._drag_from = self._drag_line = None
            if target is not None and target.is_output != start.is_output and target.node is not start.node:
                out, inp = (start, target) if start.is_output else (target, start)
                port = inp.node.input_ports.get(inp.name)
                many = getattr(port, "many", False) or getattr(port, "any_of", False)
                self.doc.connect(out.node.block_id, out.name, inp.node.block_id, inp.name, many)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        moved = {}
        for block_id, node in self.nodes.items():
            ui = self.doc.blocks.get(block_id, {}).get("ui") or {}
            if (round(node.pos().x()), round(node.pos().y())) != (ui.get("x", 0), ui.get("y", 0)):
                moved[block_id] = (node.pos().x(), node.pos().y())
        if moved:
            # A block dragged (not moved with its Loop) is inside the innermost Loop its middle is in, or in none.
            inside = {}
            for block_id in moved:
                node = self.nodes[block_id]
                if node.isSelected():
                    loop = self.loop_at(node.drawn_rect().center(), {block_id, *self.doc.within(block_id)})
                    if loop != self.doc.blocks[block_id].get("inside"):
                        inside[block_id] = loop
            self.doc.move_blocks(moved, inside)
        moved_notes = {}
        for note_id, item in self.note_items.items():
            note = self.doc.notes.get(note_id, {})
            if (round(item.pos().x()), round(item.pos().y())) != (note.get("x", 0), note.get("y", 0)):
                moved_notes[note_id] = (item.pos().x(), item.pos().y())
        if moved_notes:
            self.doc.move_notes(moved_notes)

    def contextMenuEvent(self, event):
        for item in self.items(event.scenePos()):
            node = item if isinstance(item, NodeItem) else item.parentItem()
            if isinstance(node, NodeItem):
                self.block_menu.emit(node.block_id, event.screenPos())
                event.accept()
                return
        if not self.items(event.scenePos()):
            self.canvas_menu.emit(event.scenePos(), event.screenPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRectF):
        pal = QApplication.palette()
        painter.fillRect(rect, canvas_color())
        painter.setPen(QPen(pal.mid().color(), 1.4))
        step = 20
        left, top = int(rect.left()) - int(rect.left()) % step, int(rect.top()) - int(rect.top()) % step
        points = [QPointF(x, y) for x in range(left, int(rect.right()), step) for y in range(top, int(rect.bottom()), step)]
        if len(points) < 40000:
            painter.drawPoints(points)


class ZoomField(QLineEdit):
    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if not self.isModified():  # it shows the zoom (setText), nothing typed yet: what is typed replaces it,
            self.selectAll()       # also on a click after an earlier Enter (the field still has the focus)


class ZoomBar(QFrame):
    """Floats over the top-right corner of a canvas: a magnifier, the zoom (type one there), + and − by the step
    between them."""

    def __init__(self, view: "FlowView"):
        super().__init__(view)
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        self.magnifier = QLabel()
        self._draw_magnifier()
        self.percent = ZoomField(alignment=Qt.AlignCenter, toolTip="The zoom: click it, type one (20-300%), Enter")
        self.percent.setFixedWidth(self.percent.fontMetrics().horizontalAdvance("300%") + 16)  # + and − stay put
        self.percent.returnPressed.connect(lambda: self._typed(view))
        self.percent.editingFinished.connect(view._show_zoom)  # Enter, or left without it: the zoom it is at
        self.plus = QToolButton(text="+", autoRaise=True)
        self.minus = QToolButton(text="\N{MINUS SIGN}", autoRaise=True)
        for button in (self.plus, self.minus):  # easy to see
            font = button.font()
            font.setBold(True)
            font.setPointSizeF(font.pointSizeF() * 1.5)
            button.setFont(font)
        self.step = QSpinBox(minimum=1, maximum=100, value=5, suffix="%", toolTip="How much + and − zoom")
        # Its arrows one above the other on its right, as in a number field, whatever the application's style
        # (Windows 11's puts them side by side).
        fusion = QStyleFactory.create("Fusion")
        fusion.setParent(self)  # (it lasts as long as the bar)
        self.step.setStyle(fusion)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 2, 2)
        layout.setSpacing(2)
        for widget in (self.magnifier, self.percent, self.plus, self.step, self.minus):
            layout.addWidget(widget)
        self.plus.clicked.connect(lambda: view.zoom_to(view.zoom_percent() + self.step.value()))
        self.minus.clicked.connect(lambda: view.zoom_to(view.zoom_percent() - self.step.value()))
        self.adjustSize()

    def _draw_magnifier(self):
        """In the palette's text colour: drawn again when the colours change (e.g. the theme)."""
        magnifier = QPixmap(14, 14)
        magnifier.fill(Qt.transparent)
        p = QPainter(magnifier)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(self.palette().windowText().color(), 1.6))
        p.drawEllipse(QRectF(1.5, 1.5, 8, 8))
        p.drawLine(QPointF(8.5, 8.5), QPointF(12.5, 12.5))
        p.end()
        self.magnifier.setPixmap(magnifier)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.PaletteChange:
            self._draw_magnifier()

    def _typed(self, view: "FlowView"):
        """A zoom typed into the percentage (150 or 150%): zoom there (text that is not a number changes nothing)."""
        try:
            percent = float(self.percent.text().strip().removesuffix("%"))
        except ValueError:
            return
        if math.isfinite(percent):
            view.zoom_to(percent)


class FlowView(QGraphicsView):
    def __init__(self, scene: FlowScene):
        super().__init__(scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setAcceptDrops(True)
        self._pan_from = None
        self.zoom_bar = ZoomBar(self)  # a child of the view, not the scene: not saved, not scrolled with the flow
        self._show_zoom()
        self.center_on_flow()

    def resizeEvent(self, event):  # also when only the visible canvas (the viewport) changes size
        super().resizeEvent(event)
        canvas = self.viewport().geometry()
        self.zoom_bar.move(canvas.right() - 8 - self.zoom_bar.width(), canvas.top() + 8)

    def zoom_percent(self) -> float:
        return 100 * self.transform().m11()

    def zoom_to(self, percent: float):
        """Zoom to that percentage (kept to 20-300%, like the mouse wheel) around the middle of the visible canvas."""
        zoom = min(max(percent, 20), 300) / 100
        # Qt keeps the middle as an exact scene point, so clicks in a row do not drift by the whole-pixel scrolling.
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.setTransform(QTransform.fromScale(zoom, zoom))
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)  # the mouse wheel zooms around the mouse
        self._show_zoom()

    def _show_zoom(self):
        self.zoom_bar.percent.setText(f"{round(self.zoom_percent())}%")

    def center_on_flow(self):
        """Show the whole flow: centred, zoomed out (not below half size) if it does not fit."""
        nodes = list(self.scene().nodes.values())
        if not nodes:
            self.centerOn(QPointF(400, 200))
            return
        rect = nodes[0].sceneBoundingRect()
        for node in nodes:
            rect = rect.united(node.sceneBoundingRect())
        rect = rect.adjusted(-30, -30, 30, 30)
        view = self.viewport().rect()
        scale = min(1.0, view.width() / rect.width(), view.height() / rect.height())
        self.zoom_to(100 * max(scale, 0.5))
        self.centerOn(rect.center())

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        zoom = self.transform().m11() * factor
        if 0.2 <= zoom <= 3:
            self.scale(factor, factor)
            self._show_zoom()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._pan_from = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self._pan_from is not None:
            self._pan_from = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(BLOCK_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(BLOCK_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event):
        type_id = bytes(event.mimeData().data(BLOCK_MIME)).decode()
        self.add_item(type_id, self.mapToScene(event.position().toPoint()), dropped=True)
        event.acceptProposedAction()

    def add_item(self, type_id: str, at: QPointF, dropped: bool = False):
        """What was dropped from the blocks tree (or double-clicked there): a block, or (the Text item) text on the
        canvas. A block dropped in a Loop's box is inside it."""
        if type_id == TEXT_ITEM:
            self.scene().add_note_at(at)
        else:
            cls = self.scene().registry.get(type_id)
            width = LOOP_W if type_id in (LOOP, SESSION) else NODE_W
            self.scene().doc.add_block(type_id, at.x() - width / 2, at.y() - HEADER_H / 2,
                                       cls.new_config if cls else None,
                                       inside=self.scene().loop_at(at) if dropped else None)

    def paste_corner(self, blocks: dict, notes: dict, at: QPointF | None = None) -> tuple[QPointF, bool]:
        """Where pasted blocks and texts (as copied) put their top-left corner: at `at` (the spot right-clicked), else
        at the mouse pointer when it is over the canvas, else where the group is in the middle of the visible canvas;
        then 40 px right and down while a copy would be exactly where a block or text already is (pasted again at the
        same place), a pasted text under a block or a pasted block wholly over a text (texts are drawn behind
        blocks): none is hidden. Returns the corner, and whether it was moved for a text (it may then be anywhere)."""
        scene, canvas = self.scene(), self.viewport()
        nodes = [NodeItem(i, spec, scene.registry) for i, spec in blocks.items()]
        texts = [NoteItem(i, note) for i, note in notes.items()]
        corner = QPointF(min(item.x() for item in nodes + texts), min(item.y() for item in nodes + texts))
        pointer = QCursor.pos()
        if at is None and QApplication.widgetAt(pointer) is canvas:  # (not on a menu, e.g. Edit > Paste, or a panel)
            at = self.mapToScene(canvas.mapFromGlobal(pointer))
        elif at is None:
            group = QRectF()
            for item in nodes + texts:
                group = group.united(item.sceneBoundingRect())
            at = self.mapToScene(canvas.rect().center()) - (group.center() - corner)
        at = QPointF(round(at.x()), round(at.y()))  # (positions are stored as whole numbers)
        there_nodes, there_texts = list(scene.nodes.values()), list(scene.note_items.values())
        taken = {(item.x(), item.y()) for item in there_nodes + there_texts}

        def repeated(shift: QPointF) -> bool:
            return any((item.x() + shift.x(), item.y() + shift.y()) in taken for item in nodes + texts)

        def covered(shift: QPointF) -> bool:
            return (any(b.sceneBoundingRect().intersects(t.sceneBoundingRect().translated(shift))
                        for t in texts for b in there_nodes) or
                    any(b.sceneBoundingRect().translated(shift).contains(t.sceneBoundingRect())
                        for b in nodes for t in there_texts))

        for_text = False
        while repeated(at - corner) or covered(at - corner):
            for_text = for_text or covered(at - corner)
            at += QPointF(40, 40)
        return at, for_text

    def add_at_center(self, type_id: str):
        center = self.mapToScene(self.viewport().rect().center())
        n = len(self.scene().doc.blocks) + len(self.scene().doc.notes)
        self.add_item(type_id, QPointF(center.x() + (n % 5) * 24, center.y() - 40 + HEADER_H / 2 + (n % 5) * 24))
