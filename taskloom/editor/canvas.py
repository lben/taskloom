"""The canvas: blocks as nodes with ports, edges as curves, live run status."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsPathItem, QGraphicsScene,
                               QGraphicsView)

from ..flow import ERROR_PORT, BlockSpec, block_config
from .document import FlowDocument, edge_text

BLOCK_MIME = "application/x-taskloom-block"
NODE_W, HEADER_H, ROW_H, FOOTER_H, PORT_R = 184, 28, 22, 22, 5
CATEGORY_COLORS = {
    "Triggers": "#6b5ca5", "Database": "#2c5d8f", "Data": "#2f7f7a", "Reports": "#9a6a1f",
    "Email": "#a24d6b", "Servers": "#4d6a35", "Files": "#6d7682", "Logic": "#7a4f9e",
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
        return cls.ports(block_config(cls, BlockSpec(id="x", type=cls.type_id, config=dict(config))))
    except Exception:
        pass
    try:
        return cls.ports({**{k: f.default for k, f in cls.config.items()}, **config})
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
        source = registry.sources.get(spec.get("type"), "")
        self.color = QColor(CATEGORY_COLORS.get(cls.category, USER_COLOR) if cls and source == "built-in" else USER_COLOR)
        inputs, outputs = block_ports(registry, spec)
        self.input_ports = inputs
        self.inputs, self.outputs = list(inputs), list(outputs) + [ERROR_PORT]
        rows = max(len(self.inputs), len(self.outputs), 1)
        self.height = HEADER_H + 6 + rows * ROW_H + FOOTER_H
        self.status, self.status_text = None, ""
        self.ports: dict[tuple[str, bool], PortItem] = {}
        for i, name in enumerate(self.inputs):
            self.ports[(name, False)] = PortItem(self, name, False, HEADER_H + 6 + i * ROW_H + ROW_H / 2)
        for i, name in enumerate(self.outputs):
            self.ports[(name, True)] = PortItem(self, name, True, HEADER_H + 6 + i * ROW_H + ROW_H / 2)
        ui = spec.get("ui") or {}
        self.setPos(ui.get("x", 0), ui.get("y", 0))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(1)

    def boundingRect(self):
        return QRectF(-4, -4, NODE_W + 8, self.height + 8)

    def set_status(self, status: str | None, text: str = ""):
        self.status, self.status_text = status, text
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
            painter.drawText(box, Qt.AlignVCenter | (Qt.AlignRight if is_output else Qt.AlignLeft), name)
        # footer: type, or the run status
        text = self.status_text if self.status else self.type_title
        painter.setPen(QColor(STATUS_COLORS[self.status]) if self.status in STATUS_COLORS and self.status != "skipped" else muted)
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


class FlowScene(QGraphicsScene):
    block_selected = Signal(object)  # block id or None

    def __init__(self, doc: FlowDocument, registry):
        super().__init__()
        self.doc, self.registry = doc, registry
        self.nodes: dict[str, NodeItem] = {}
        self.edge_items: list[EdgeItem] = []
        self.statuses: dict[str, tuple[str, str]] = {}
        self._drag_from: PortItem | None = None
        self._drag_line: QGraphicsPathItem | None = None
        self._rebuilding = False
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self.selectionChanged.connect(self._on_selection)
        doc.changed.connect(self.rebuild)
        self.rebuild()

    # --- building from the document ---------------------------------------------

    def rebuild(self):
        selected = set(self.selected_block_ids())
        self._rebuilding = True
        self.clear()
        self.nodes, self.edge_items = {}, []
        for block_id, spec in self.doc.blocks.items():
            node = NodeItem(block_id, spec, self.registry)
            self.addItem(node)
            self.nodes[block_id] = node
            if block_id in self.statuses:
                node.set_status(*self.statuses[block_id])
            node.setSelected(block_id in selected)
        for src, sp, dst, dp in self.doc.edges():
            a = self.nodes.get(src) and self.nodes[src].ports.get((sp, True))
            b = self.nodes.get(dst) and self.nodes[dst].ports.get((dp, False))
            if a and b:
                edge = EdgeItem(a, b)
                self.addItem(edge)
                self.edge_items.append(edge)
        self._rebuilding = False
        self._on_selection()

    def update_edges(self, block_id: str):
        for edge in self.edge_items:
            if block_id in (edge.src_port.node.block_id, edge.dst_port.node.block_id):
                edge.update_path()

    def set_status(self, block_id: str, status: str | None, text: str = ""):
        if status is None:
            self.statuses.pop(block_id, None)
        else:
            self.statuses[block_id] = (status, text)
        if block_id in self.nodes:
            self.nodes[block_id].set_status(status, text)

    def clear_statuses(self):
        for block_id in list(self.statuses):
            self.set_status(block_id, None)

    # --- selection ----------------------------------------------------------------

    def selected_block_ids(self) -> list[str]:
        return [i.block_id for i in self.selectedItems() if isinstance(i, NodeItem)]

    def _on_selection(self):
        if self._rebuilding:
            return
        ids = self.selected_block_ids()
        self.block_selected.emit(ids[0] if len(ids) == 1 else None)

    def delete_selected(self):
        blocks = self.selected_block_ids()
        edges = [i.text for i in self.selectedItems() if isinstance(i, EdgeItem)]
        if blocks or edges:
            self.doc.remove(blocks, edges)

    # --- connecting ports and moving nodes ---------------------------------------

    def _port_at(self, pos: QPointF) -> PortItem | None:
        for item in self.items(pos):
            if isinstance(item, PortItem):
                return item
        return None

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

    def mouseMoveEvent(self, event):
        if self._drag_from is not None:
            a, b = self._drag_from.anchor(), event.scenePos()
            self._drag_line.setPath(_curve(a, b) if self._drag_from.is_output else _curve(b, a))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_from is not None:
            start, target = self._drag_from, self._port_at(event.scenePos())
            self.removeItem(self._drag_line)
            self._drag_from = self._drag_line = None
            if target is not None and target.is_output != start.is_output and target.node is not start.node:
                out, inp = (start, target) if start.is_output else (target, start)
                many = getattr(inp.node.input_ports.get(inp.name), "many", False)
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
        nodes = self.scene().nodes.values()
        if nodes:
            rect = nodes.__iter__().__next__().sceneBoundingRect()
            for node in nodes:
                rect = rect.united(node.sceneBoundingRect())
            self.centerOn(rect.center())
        else:
            self.centerOn(QPointF(400, 200))

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
        pos = self.mapToScene(event.position().toPoint())
        self.scene().doc.add_block(type_id, pos.x() - NODE_W / 2, pos.y() - HEADER_H / 2)
        event.acceptProposedAction()

    def add_at_center(self, type_id: str):
        center = self.mapToScene(self.viewport().rect().center())
        n = len(self.scene().doc.blocks)
        self.scene().doc.add_block(type_id, center.x() - NODE_W / 2 + (n % 5) * 24, center.y() - 40 + (n % 5) * 24)
