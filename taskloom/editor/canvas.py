"""The canvas: blocks as nodes with ports, edges as curves, live run status."""

from __future__ import annotations

import html

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsTextItem, QGraphicsView, QStyle)

from ..block import AFTER, DONE, all_ports
from ..flow import ERROR_PORT, BlockSpec, block_config
from .document import FlowDocument, edge_text

BLOCK_MIME = "application/x-taskloom-block"
TEXT_ITEM = "canvas.text"  # the Text item of the blocks tree: text on the canvas, not a block that runs
HANDLE = 10  # the corner you drag to resize a text
NODE_W, HEADER_H, ROW_H, FOOTER_H, PORT_R = 184, 28, 22, 22, 5
COUNTDOWN_H = 18  # the line under a Schedule block saying when it starts the flow next
CATEGORY_COLORS = {
    "Start": "#1f8a4c", "Database": "#2c5d8f", "Data": "#2f7f7a", "Reports": "#9a6a1f",
    "Email": "#a24d6b", "Servers": "#4d6a35", "Files": "#6d7682", "Logic": "#7a4f9e", "Debug": "#b7791f",
}
USER_COLOR = "#b0563a"
STATUS_COLORS = {"success": "#1f8a4c", "running": "#b7791f", "failed": "#c0392b",
                 "skipped": "#9aa6b2", "cancelled": "#9aa6b2", "retrying": "#b7791f"}


def is_dark() -> bool:
    return QApplication.palette().window().color().lightness() < 128


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
        inputs, outputs = block_ports(registry, spec)
        self.input_ports = inputs
        # `after` is the first row, drawn differently (it only sets when the block runs);
        # start blocks have neither it nor an error output.
        self.inputs = ([AFTER] if AFTER in inputs else []) + [n for n in inputs if n != AFTER]
        self.outputs = ([DONE] if DONE in outputs else []) + [n for n in outputs if n != DONE] + \
            ([] if cls is not None and cls.is_trigger else [ERROR_PORT])
        rows = max(len(self.inputs), len(self.outputs), 1)
        # A Schedule block has a line of its own for when it starts the flow next, above its result.
        self.counts_down = spec.get("type") == "trigger.schedule"
        self.height = HEADER_H + 6 + rows * ROW_H + (COUNTDOWN_H if self.counts_down else 0) + FOOTER_H
        self.status, self.status_text = None, ""
        self.countdown = ""
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

    def set_status(self, status: str | None, text: str = "", tip: str = ""):
        """The run's result under the block: `text` (short), and `tip` (HTML) when hovering it."""
        self.status, self.status_text = status, text
        self.footer.setToolTip(tip if status else "")
        self.update()

    def set_countdown(self, text: str, tip: str = ""):
        self.countdown = text
        self.countdown_area.setToolTip(tip)
        self.update()

    def paint(self, painter: QPainter, option, widget=None):
        pal = QApplication.palette()
        rect = QRectF(0, 0, NODE_W, self.height)
        border = QColor(STATUS_COLORS.get(self.status, pal.mid().color().name()))
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
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.color)
        painter.drawRoundedRect(QRectF(8, 7, 14, 14), 3, 3)
        font = QFont(painter.font())
        font.setBold(True)
        font.setPointSizeF(max(font.pointSizeF(), 9) * 0.95)
        painter.setFont(font)
        painter.setPen(pal.text().color())
        title = painter.fontMetrics().elidedText(self.block_id, Qt.ElideRight, NODE_W - 36)
        painter.drawText(QRectF(28, 0, NODE_W - 34, HEADER_H), Qt.AlignVCenter, title)
        # ports
        font.setBold(False)
        font.setPointSizeF(font.pointSizeF() * 0.92)
        painter.setFont(font)
        muted = pal.placeholderText().color()
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
        if self.counts_down:  # when it starts the flow next
            painter.setPen(pal.text().color())
            line = QRectF(10, self.height - FOOTER_H - COUNTDOWN_H, NODE_W - 20, COUNTDOWN_H)
            painter.drawText(line, Qt.AlignVCenter, painter.fontMetrics().elidedText(
                "⏱ " + (self.countdown or "…"), Qt.ElideRight, int(line.width())))
        # footer: the run status, else the type
        text = self.status_text if self.status else self.type_title
        painter.setPen(QColor(STATUS_COLORS[self.status]) if self.status in STATUS_COLORS and self.status != "skipped"
                       else muted)
        footer = QRectF(10, self.height - FOOTER_H, NODE_W - 20, FOOTER_H - 4)
        painter.drawText(footer, Qt.AlignVCenter, painter.fontMetrics().elidedText(text, Qt.ElideRight, int(footer.width())))
        painter.setOpacity(1.0)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            self.scene().update_edges(self.block_id)
        return super().itemChange(change, value)


def _curve(a: QPointF, b: QPointF) -> QPainterPath:
    path = QPainterPath(a)
    dx = max(abs(b.x() - a.x()) / 2, 40)
    path.cubicTo(QPointF(a.x() + dx, a.y()), QPointF(b.x() - dx, b.y()), b)
    return path


class EdgeItem(QGraphicsPathItem):
    def __init__(self, src_port: PortItem, dst_port: PortItem):
        super().__init__()
        self.src_port, self.dst_port = src_port, dst_port
        self.text = edge_text(src_port.node.block_id, src_port.name, dst_port.node.block_id, dst_port.name)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setZValue(0)
        self.update_path()

    def update_path(self):
        self.setPath(_curve(self.src_port.anchor(), self.dst_port.anchor()))

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
    return html.escape(text if len(text) <= 1500 else text[:1500] + "…", quote=False)


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
    block_selected = Signal(object)  # block id or None
    note_selected = Signal(str)  # a text on the canvas, when it alone is selected
    block_menu = Signal(str, object)  # block id, screen position (right-click on a block)
    canvas_menu = Signal(object, object)  # scene position, screen position (right-click on empty canvas)

    def __init__(self, doc: FlowDocument, registry):
        super().__init__()
        self.doc, self.registry = doc, registry
        self.nodes: dict[str, NodeItem] = {}
        self.note_items: dict[str, NoteItem] = {}
        self.countdowns: dict[str, tuple[str, str]] = {}  # Schedule block -> (text, tip)
        self.edge_items: list[EdgeItem] = []
        self.statuses: dict[str, tuple[str, str, str]] = {}  # block -> (status, text, tip)
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
        for note_id, note in self.doc.notes.items():
            item = NoteItem(note_id, note)
            self.addItem(item)
            self.note_items[note_id] = item
            item.setSelected(note_id in selected_notes)
        for block_id, spec in self.doc.blocks.items():
            node = NodeItem(block_id, spec, self.registry)
            self.addItem(node)
            self.nodes[block_id] = node
            if block_id in self.statuses:
                node.set_status(*self.statuses[block_id])
            if block_id in self.countdowns:
                node.set_countdown(*self.countdowns[block_id])
            node.setSelected(block_id in selected)
        for src, sp, dst, dp in self.doc.edges():
            a = self.nodes.get(src) and self.nodes[src].ports.get((sp, True))
            b = self.nodes.get(dst) and self.nodes[dst].ports.get((dp, False))
            if a and b:
                edge = EdgeItem(a, b)
                self.addItem(edge)
                self.edge_items.append(edge)
        self._rebuilding = False
        self.refresh_tooltips()
        self._on_selection()

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
        if name == AFTER and not is_output:
            head += ("<br>Run after: connect another block's 'done' (or a start block) here to make this block "
                     "wait for it. No data comes in through here.")
        elif name == DONE and is_output:
            head += ("<br>Done: fires when this block has finished successfully. Connect it to another block's "
                     "'run after'. No data goes out through here.")
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

    def update_edges(self, block_id: str):
        for edge in self.edge_items:
            if block_id in (edge.src_port.node.block_id, edge.dst_port.node.block_id):
                edge.update_path()

    def set_status(self, block_id: str, status: str | None, text: str = "", tip: str = ""):
        """A block's result on the canvas: `text` under it (short), `tip` (HTML) when hovering that."""
        if status is None:
            self.statuses.pop(block_id, None)
        else:
            self.statuses[block_id] = (status, text, tip or html.escape(text))
        if block_id in self.nodes:
            self.nodes[block_id].set_status(status, text, tip or html.escape(text))

    def set_countdown(self, block_id: str, text: str, tip: str = ""):
        """Under a Schedule block: when it starts the flow next."""
        self.countdowns[block_id] = (text, tip)
        if block_id in self.nodes and (self.nodes[block_id].countdown, self.nodes[block_id].countdown_area.toolTip()) != (text, tip):
            self.nodes[block_id].set_countdown(text, tip)

    def clear_statuses(self):
        for block_id in list(self.statuses):
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

    def _on_selection(self):
        if self._rebuilding:
            return
        ids, notes = self.selected_block_ids(), self.selected_note_ids()
        # Showing the selection saves the field being edited, which can change the flow: its rebuild
        # waits until Qt is done with this selection change (it would delete the items in it).
        self._in_selection = True
        try:
            self.block_selected.emit(ids[0] if len(ids) == 1 else None)
            if not ids and len(notes) == 1:
                self.note_selected.emit(notes[0])
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
            self.doc.move_blocks(moved)
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
        painter.fillRect(rect, pal.window().color().lighter(103) if not is_dark() else pal.window().color().darker(110))
        painter.setPen(QPen(pal.mid().color(), 1.4))
        step = 20
        left, top = int(rect.left()) - int(rect.left()) % step, int(rect.top()) - int(rect.top()) % step
        points = [QPointF(x, y) for x in range(left, int(rect.right()), step) for y in range(top, int(rect.bottom()), step)]
        if len(points) < 40000:
            painter.drawPoints(points)


class FlowView(QGraphicsView):
    def __init__(self, scene: FlowScene):
        super().__init__(scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setAcceptDrops(True)
        self._pan_from = None
        self.center_on_flow()

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
        self.resetTransform()
        self.scale(max(scale, 0.5), max(scale, 0.5))
        self.centerOn(rect.center())

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        zoom = self.transform().m11() * factor
        if 0.2 <= zoom <= 3:
            self.scale(factor, factor)

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
        self.add_item(type_id, self.mapToScene(event.position().toPoint()))
        event.acceptProposedAction()

    def add_item(self, type_id: str, at: QPointF):
        """What was dropped from the blocks tree: a block, or (the Text item) text on the canvas."""
        if type_id == TEXT_ITEM:
            self.scene().add_note_at(at)
        else:
            self.scene().doc.add_block(type_id, at.x() - NODE_W / 2, at.y() - HEADER_H / 2)

    def add_at_center(self, type_id: str):
        center = self.mapToScene(self.viewport().rect().center())
        n = len(self.scene().doc.blocks) + len(self.scene().doc.notes)
        self.add_item(type_id, QPointF(center.x() + (n % 5) * 24, center.y() - 40 + HEADER_H / 2 + (n % 5) * 24))
