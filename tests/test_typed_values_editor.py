"""Typed values for inputs (docs/promises/typed-values.yaml, agreed with the maintainer on 2026-11-24), work package 2:
the canvas, Properties and the menus. Rows TV-1, TV-2, TV-4, TV-5, TV-6 ("with inputs I choose…"), TV-7, TV-8 and
TV-9 (Validate and Run now in the editor), through the real editor window (the `win` fixture) with real input: a
double-click on the hole where the view shows it, right-click menus, keys, Undo and Redo, the zoom bar, Settings'
Theme. What is checked is what you see (texts, where things are on the screen, pixels) and the saved flow file; runs
are the editor's real `taskloom run`. The flow file's contract is at the top of tests/test_typed_values.py (WP1); the
editor stores a typed value as the text typed: {type: <type>, value: "<text>"}.

The UI contract (the approved mockup, notes stage-04-mockup/typed-value-light.png; the implementation follows it):

- The box for a typed value is a QWidget with objectName "typedValueBox", shown over the canvas (a child of the
  canvas or a window of its own, whose parents lead to the editor's window; never an item of the scene: it keeps its
  size at any zoom). It is shown, not exec'd: the editor goes on while it is open. It opens beside the hole: it does
  not cover the hole, its nearest edge is at most 24 px (plus the hole's radius on screen) from the hole's centre,
  and it lies wholly inside the visible canvas. It shows the text "<block>.<hole>" and the hint "Enter keeps it, Esc
  cancels" (each a QLabel). Its parts, each found by objectName:
    "typedValueType"  a QListWidget: the types text, int, float, bool, date, datetime, list (params.TYPES), in that
                      order, all shown; the current item is the type (a click on an item chooses it);
    "typedValueField" a QLineEdit: the value as typed; Enter (Return) in it keeps the value and closes the box; Esc
                      closes the box and changes nothing;
    "typedValueError" a QLabel: hidden while there is nothing to say; when Enter is pressed on a value that does not
                      fit its type, it shows why, naming the type as the list shows it, and the box stays open.
  Opened on a hole that has a value, the list and the field start from it.
- Right-click on an input hole: the menu that opens has the item "Set value…" (opens the box; enabled when the hole
  can take a typed value and has no line) and, while the hole has a value, "Clear value" (enabled). On a hole that
  offers no typed value (TV-5), "Set value…" is there but disabled, the menu shows tooltips, and that item's tooltip
  says why with these words (any case): a table input "table"; a hole that collects many lines "lines"; a run after
  hole "run after"; a hole that expects a dictionary "dictionary"; the Loop's result "inside". A double-click on such
  a hole, or on a hole with a line, opens no box.
- The value is drawn inside the block on the hole's row, right of the hole's name, ending at least 8 px before that
  row's output name; shortened with "…" when it does not fit, on one line; resting the mouse on it shows it whole.
- Properties shows, for a block with input holes, a QGroupBox with objectName "inputValues" and the title "Input
  values": a QFormLayout row per hole that can take a typed value, labelled with the hole's name. Its field holds a
  QLineEdit (the value as typed; empty: none) and a QComboBox (the type, the same 7 in the same order); Enter in the
  QLineEdit keeps the value with the type shown, choosing another type changes the value's type. A hole with a line
  shows a QLabel "line from <block>.<output>" instead.
"""

import ast
import math
import os
from collections import Counter

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QHelpEvent, QImage, QPainter, QTextDocument  # noqa: E402
from PySide6.QtGui import Qt as GuiQt  # noqa: E402 (mightBeRichText)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit,  # noqa: E402
                               QListWidget, QToolTip, QWidget)

from taskloom.blocks.logic import write_reusable_block  # noqa: E402
from taskloom.config import Home  # noqa: E402
from taskloom.editor import dialogs  # noqa: E402
from taskloom.editor.canvas import HEADER_H, NODE_W, PORT_R, ROW_H  # noqa: E402
from tests.test_parallel_lines import drawn_texts  # noqa: E402
from tests.test_paths_editor_editing import field, flow_file, menu, mouse_drag  # noqa: E402
from tests.test_paths_editor_running import gated, right_click  # noqa: E402
from tests.test_theme import as_found, choose  # noqa: E402,F401 (as_found: each test leaves the look as it found it)
from tests.test_typed_values import HAND_EDITS, as_recorded, python, recorded, recorder, typed  # noqa: E402

BOX, TYPE_LIST, VALUE_FIELD, ERROR_TEXT, INPUT_VALUES = (
    "typedValueBox", "typedValueType", "typedValueField", "typedValueError", "inputValues")
TYPES = ["text", "int", "float", "bool", "date", "datetime", "list"]
HINT = "Enter keeps it, Esc cancels"
SET, CLEAR = "Set value…", "Clear value"
ONLY_CHOOSE, FROM_CHOOSE = "Run only this block, with inputs I choose", "Run from this block on, with inputs I choose"
NEAR = 24  # px: how far the box may be from the hole
GAP = 8  # px: the least room between the value shown in a block and that row's output name
SCALE = 2  # pictures of a block's row are taken at twice the size


# --- the flows ---------------------------------------------------------------------------------------------------

def at(x: int, y: int) -> dict:
    return {"ui": {"x": x, "y": y}}


def filter_flow(folder, values=None, more=None, edges=()):
    """The mockup's flow: start → load, whose rows go to filter (a Python Code block with the holes rows and
    threshold, recording what arrives at them in got.jsonl). `values`: filter's typed values. Returns (flow, out)."""
    out = folder / "got.jsonl"
    blocks = {"start": {"type": "trigger.manual", **at(-300, 0)},
              "load": {**python([], "rows = [1, 2, 3]"), **at(0, 0)},
              "filter": {**recorder(out, ["rows", "threshold"], values), **at(300, 0)},
              **(more or {})}
    blocks["load"]["config"]["outputs"] = ["rows"]
    return flow_file(folder / "sales-filter.yaml", blocks,
                     ["start.start -> load.after", "load.rows -> filter.rows", *edges]), out


# Holes that take a typed value (block spec, hole), and holes that take none with the word their menu says why with.
OFFERED = {
    "Python Code input": ({"type": "logic.python", "config": {"inputs": ["x"], "code": "output = x"}}, "x"),
    "Inspect value": ({"type": "logic.inspect"}, "value"),
    "If value": ({"type": "logic.if", "config": {"condition": "True"}}, "value"),
    "Splitter value": ({"type": "logic.splitter"}, "value"),
    "Loop items": ({"type": "logic.loop"}, "items"),
    "Send Email subject": ({"type": "email.send"}, "subject"),
    "API Request body": ({"type": "servers.api_request"}, "body"),
}
NOT_OFFERED = {
    "Write File table": ({"type": "data.write_file"}, "table", "table"),
    "Statistics table": ({"type": "data.statistics"}, "table", "table"),
    "Send Email body": ({"type": "email.send"}, "body", "lines"),
    "Send Email attachments": ({"type": "email.send"}, "attachments", "lines"),
    "HTML Template data": ({"type": "reports.html_template"}, "data", "lines"),
    "Merger lines": ({"type": "logic.merger"}, "lines", "lines"),
    "run after": ({"type": "logic.python", "config": {"inputs": ["x"], "code": "output = x"}}, "after", "run after"),
    "Run Query params": ({"type": "db.run_query"}, "params", "dictionary"),
    "Statistics extra": ({"type": "data.statistics"}, "extra", "dictionary"),
    "Loop result": ({"type": "logic.loop"}, "result", "inside"),
}


def one_block_flow(folder, spec: dict):
    """A flow of the one block `block` (no line comes into it)."""
    return flow_file(folder / "one.yaml", {"block": {**spec, **at(0, 0)}})


# --- looking and acting on the canvas --------------------------------------------------------------------------

def settle(rounds: int = 5):
    for _ in range(rounds):
        QApplication.processEvents()


def open_in(win, flow):
    """The flow opened in the editor, in a window as large as the mockup's."""
    win.resize(1400, 940)
    tab = win.open_flow(flow)
    settle()
    return tab


def ours(win, widget) -> bool:
    while widget is not None:
        if widget is win:
            return True
        widget = widget.parentWidget()
    return False


def open_box(win) -> QWidget | None:
    """The box for a typed value shown now, if one is."""
    return next((w for w in QApplication.allWidgets() if w.objectName() == BOX and w.isVisible() and ours(win, w)),
                None)


def part(box, kind, name):
    found = box.findChild(kind, name)
    assert found is not None, f"the box has no {kind.__name__} named {name!r}"
    return found


def the_box(qtbot, win, how: str) -> QWidget:
    try:
        qtbot.waitUntil(lambda: open_box(win) is not None, timeout=3_000)
    except qtbot.TimeoutError:
        raise AssertionError(f"no box for a typed value opened after {how}") from None
    return open_box(win)


def hole_point(tab, block, hole, centre=True) -> QPoint:
    """The hole on screen, in the canvas's viewport (scrolled to its middle first, as you would)."""
    settle()  # (an opened flow centres itself just after)
    port = tab.scene.nodes[block].ports[(hole, False)]
    if centre:
        tab.view.centerOn(port.scenePos())
        settle()
    point = tab.view.mapFromScene(port.scenePos())
    assert tab.view.viewport().rect().contains(point), (block, hole, point)
    return point


def put_hole_at(tab, block, hole, x: int, y: int):
    """Scroll the canvas so that the hole is at (x, y) of the visible canvas."""
    point = hole_point(tab, block, hole)
    across, down = tab.view.horizontalScrollBar(), tab.view.verticalScrollBar()
    across.setValue(across.value() + point.x() - x)
    down.setValue(down.value() + point.y() - y)
    settle()
    point = hole_point(tab, block, hole, centre=False)
    assert abs(point.x() - x) <= 1 and abs(point.y() - y) <= 1, (point, x, y)


def double_click_hole(qtbot, win, tab, block, hole, centre=True) -> QWidget | None:
    """Double-click the hole: the box that opens (None: none within a second)."""
    point = hole_point(tab, block, hole, centre)
    QTest.mouseDClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, point)
    try:
        qtbot.waitUntil(lambda: open_box(win) is not None, timeout=1_000)
    except qtbot.TimeoutError:
        return None
    return open_box(win)


def box_for(qtbot, win, tab, block, hole, centre=True) -> QWidget:
    box = double_click_hole(qtbot, win, tab, block, hole, centre)
    assert box is not None, f"no box for a typed value opened after a double-click on {block}.{hole}"
    return box


def hole_menu(qtbot, win, tab, block, hole, choose_item: str | None = None) -> tuple[dict, bool]:
    """Right-click the hole, as the mouse does: each item of the menu that opens, {text: (enabled, tooltip)}, and
    whether the menu shows tooltips; then click `choose_item` if it is there and enabled (else close the menu)."""
    return context_menu(qtbot, win, tab, hole_point(tab, block, hole), choose_item)


def context_menu(qtbot, win, tab, point: QPoint, choose_item: str | None = None) -> tuple[dict, bool]:
    """Right-click the canvas at `point` (of its viewport) through the window: see hole_menu."""
    found, shows_tips, tries, done = {}, [], [], []

    def act():  # (the menu waits for you inside the right-click)
        popup = QApplication.activePopupWidget()
        if popup is None and len(tries) < 250:
            tries.append(1)
            QTimer.singleShot(20, act)
            return
        if popup is not None:
            for action in popup.actions():
                if not action.isSeparator():
                    found[action.text().replace("&", "")] = (action.isEnabled(), action.toolTip())
            shows_tips.append(popup.toolTipsVisible())
            item = next((a for a in popup.actions() if choose_item is not None
                         and a.text().replace("&", "") == choose_item and a.isEnabled()), None)
            if item is not None:
                QTest.mouseClick(popup, Qt.LeftButton, Qt.NoModifier, popup.actionGeometry(item).center())
            if popup.isVisible():
                popup.close()
        done.append(1)

    for _ in range(2):  # (once more if no menu opened: a click can be lost while the window is still settling)
        tries.clear()
        done.clear()
        QTimer.singleShot(0, act)
        QTest.mouseClick(win.windowHandle(), Qt.RightButton, Qt.NoModifier, tab.view.viewport().mapTo(win, point))
        qtbot.waitUntil(lambda: bool(done), timeout=10_000)
        if found:
            break
    settle()
    return found, bool(shows_tips and shows_tips[0])


def offers(items: dict, text: str) -> bool:
    return items.get(text, (False, ""))[0]


def fill_in(box, type_: str | None = None, text: str | None = None):
    """In the box: click the type in its list, then replace the value's text with `text` (select all, type)."""
    if type_ is not None:
        types = part(box, QListWidget, TYPE_LIST)
        (item,) = types.findItems(type_, Qt.MatchExactly)
        QTest.mouseClick(types.viewport(), Qt.LeftButton, Qt.NoModifier, types.visualItemRect(item).center())
        chosen = types.currentItem()
        assert chosen is not None and chosen.text() == type_, f"clicking {type_} in the list did not choose it"
    if text is not None:
        value = part(box, QLineEdit, VALUE_FIELD)
        QTest.keyClick(value, Qt.Key_A, Qt.ControlModifier)
        if text:
            QTest.keyClicks(value, text)
        else:
            QTest.keyClick(value, Qt.Key_Delete)
    settle()


def key(win, box, which):
    QTest.keyClick(part(box, QLineEdit, VALUE_FIELD), which)
    settle()


def set_on_canvas(qtbot, win, tab, block, hole, type_, text):
    """Double-click the hole, choose the type, type the value, Enter: the box closes."""
    box = box_for(qtbot, win, tab, block, hole)
    fill_in(box, type_, text)
    key(win, box, Qt.Key_Return)
    assert open_box(win) is None, (f"the box stayed open after Enter on {text!r} as {type_}: "
                                   f"{part(box, QLabel, ERROR_TEXT).text()!r}")


def finish_if_open(win):
    """Enter in the box, if it is still open."""
    box = open_box(win)
    if box is not None:
        key(win, box, Qt.Key_Return)


def plain(text: str) -> str:
    if GuiQt.mightBeRichText(text):
        doc = QTextDocument()
        doc.setHtml(text)
        text = doc.toPlainText()
    return text.replace("\u2028", "\n").replace("\u2029", "\n")


def box_texts(box) -> list[str]:
    return [plain(label.text()).strip() for label in box.findChildren(QLabel) if label.isVisible()]


def unchanged(win, tab) -> bool:
    """The flow's tab does not say it has unsaved changes."""
    return not win.tabs.tabText(win.tabs.indexOf(tab)).endswith("*")


# --- what the block shows --------------------------------------------------------------------------------------

def row_names(tab, block, hole) -> set[str]:
    """The hole's name and the output name on its row, as the block draws them."""
    node = tab.scene.nodes[block]
    row = node.inputs.index(hole)
    names = {"run after" if hole == "after" else hole}
    if row < len(node.outputs):
        names.add(node.outputs[row])
    return names


def row_rect(tab, block, hole) -> QRectF:
    node = tab.scene.nodes[block]
    y = node.ports[(hole, False)].scenePos().y()
    return QRectF(node.scenePos().x(), y - ROW_H / 2, NODE_W, ROW_H)


def shown_value(tab, block, hole) -> str | None:
    """What the block draws on the hole's row besides the hole's name and the output name (None: nothing)."""
    settle()
    rect = row_rect(tab, block, hole)
    names = row_names(tab, block, hole)
    texts = [t["text"] for t in drawn_texts(tab.scene, rect.adjusted(0, -ROW_H, 0, ROW_H))
             if rect.top() <= t["center"][1] <= rect.bottom() and rect.left() <= t["center"][0] <= rect.right()
             and t["text"] and t["text"] not in names]
    return " ".join(texts) if texts else None


def row_picture(tab, block, hole) -> QImage:
    """The block's row of the hole as drawn, at twice its size (nothing selected)."""
    tab.scene.clearSelection()
    settle()
    rect = row_rect(tab, block, hole)
    image = QImage(int(rect.width() * SCALE), int(rect.height() * SCALE), QImage.Format_ARGB32)
    image.fill(QColor("#808080"))
    painter = QPainter(image)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    tab.scene.render(painter, QRectF(0, 0, image.width(), image.height()), rect)
    painter.end()
    return image


def far(a: int, b: int) -> bool:
    ca, cb = QColor.fromRgb(a), QColor.fromRgb(b)
    return max(abs(ca.red() - cb.red()), abs(ca.green() - cb.green()), abs(ca.blue() - cb.blue())) > 40


def label_span(before: QImage, after: QImage) -> tuple[float, float] | None:
    """Where the block draws something on the row that it did not draw before (left, right, in the block's own
    coordinates), between its hole and its output hole; None: nowhere."""
    columns = [x for x in range(8 * SCALE, (NODE_W - 8) * SCALE)
               if any(before.pixel(x, y) != after.pixel(x, y) for y in range(after.height()))]
    return (columns[0] / SCALE, columns[-1] / SCALE) if columns else None


def check_label_fits(tab, block, hole, before: QImage) -> tuple[float, float]:
    """The value shown on the hole's row (compared with `before`, the row without it) is right of the hole's name and
    ends at least GAP px before the row's output name: it covers neither, nor any line (lines end at the holes).
    Returns where it is (left, right) in the block."""
    after = row_picture(tab, block, hole)
    background = Counter(before.pixel(x, y) for x in range(before.width()) for y in range(before.height()))
    background = background.most_common(1)[0][0]
    inked = [x for x in range(8 * SCALE, (NODE_W - 8) * SCALE)
             if any(far(before.pixel(x, y), background) for y in range(before.height()))]
    groups = []
    for x in inked:
        if groups and x - groups[-1][-1] <= 6 * SCALE:
            groups[-1].append(x)
        else:
            groups.append([x])
    assert groups, f"the hole's name {hole} is not drawn"
    name_right = groups[0][-1] / SCALE
    output_left = groups[-1][0] / SCALE if len(groups) > 1 and groups[-1][0] > NODE_W * SCALE / 2 else None
    span = label_span(before, after)
    assert span is not None, f"{block} shows nothing right of the hole's name {hole}"
    left, right = span
    assert left > name_right, f"the value shown at {block}.{hole} starts at {left}, over its name (to {name_right})"
    if output_left is not None:
        assert right <= output_left - GAP, (
            f"the value shown at {block}.{hole} ends at {right}: less than {GAP} px before the output name at "
            f"{output_left}")
    return left, right


def hover_text(qtbot, tab, scene_point: QPointF) -> str:
    """Rest the mouse on the canvas at `scene_point`: the text of the tooltip that shows."""
    tab.view.centerOn(scene_point)
    settle()
    viewport = tab.view.viewport()
    point = tab.view.mapFromScene(scene_point)
    QToolTip.hideText()
    QApplication.sendEvent(viewport, QHelpEvent(QHelpEvent.ToolTip, point, viewport.mapToGlobal(point)))

    def tip():
        return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)

    qtbot.waitUntil(lambda: tip() is not None, timeout=5_000)
    text = plain(tip().text())
    QToolTip.hideText()
    return text


def hover_label(qtbot, tab, block, hole, span) -> str:
    rect = row_rect(tab, block, hole)
    return hover_text(qtbot, tab, QPointF(rect.left() + (span[0] + span[1]) / 2, rect.center().y()))


def squeezed(text: str) -> str:
    return "".join(text.split())


# --- the box on the screen ---------------------------------------------------------------------------------------

def screen_rect(widget) -> QRect:
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def check_beside(tab, box, block, hole):
    """The box does not cover the hole, is at most NEAR px (plus the hole's radius) from its centre, and lies wholly
    in the visible canvas."""
    port = tab.scene.nodes[block].ports[(hole, False)]
    viewport = tab.view.viewport()
    centre = viewport.mapToGlobal(tab.view.mapFromScene(port.scenePos()))
    radius = math.ceil(PORT_R * tab.view.transform().m11())
    shown, canvas = screen_rect(box), screen_rect(viewport)
    percent = round(tab.view.transform().m11() * 100)
    where = f"{block}.{hole} at {centre.x()},{centre.y()} (zoom {percent}%): box {shown}"
    assert canvas.contains(shown), f"{where} is not wholly in the visible canvas {canvas}"
    assert not shown.intersects(QRect(centre.x() - radius, centre.y() - radius, 2 * radius + 1, 2 * radius + 1)), \
        f"{where} covers the hole"
    dx = max(shown.left() - centre.x(), 0, centre.x() - shown.right())
    dy = max(shown.top() - centre.y(), 0, centre.y() - shown.bottom())
    assert math.hypot(dx, dy) <= NEAR + radius, f"{where} is {math.hypot(dx, dy):.0f} px from the hole"


def zoom(tab, percent: int):
    """Type the zoom in the zoom bar, Enter."""
    percent_field = tab.view.zoom_bar.percent
    QTest.mouseClick(percent_field, Qt.LeftButton)
    QTest.keyClick(percent_field, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(percent_field, str(percent))
    QTest.keyClick(percent_field, Qt.Key_Return)
    settle()
    assert round(tab.view.transform().m11() * 100) == percent


# --- the file, Properties and runs ---------------------------------------------------------------------------------

def saved(win, tab) -> dict:
    """File > Save (the flow on screen), then the flow file as saved."""
    assert win.current is tab
    menu(win, "Save").trigger()
    settle()
    return yaml.safe_load(tab.doc.path.read_text(encoding="utf-8"))


def saved_values(win, tab, block) -> dict:
    """The block's typed values in the saved flow file."""
    return saved(win, tab)["blocks"][block].get("inputs") or {}


def click_block(tab, block):
    """Click the block's title: it alone is selected, and Properties shows it."""
    node = tab.scene.nodes[block]
    title = node.mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))
    tab.view.centerOn(title)
    settle()
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, tab.view.mapFromScene(title))
    settle()
    assert tab.scene.selected_block_ids() == [block]


def input_values(win) -> QGroupBox:
    found = win.properties.widget().findChild(QGroupBox, INPUT_VALUES)
    assert found is not None and found.isVisible(), "Properties shows no Input values box"
    assert found.title() == "Input values"
    return found


def value_row(win, hole):
    """The Input values row of the hole: (its value field, its type list), or the QLabel of a hole with a line."""
    for form in input_values(win).findChildren(QFormLayout):
        for row in range(form.rowCount()):
            name, value = form.itemAt(row, QFormLayout.LabelRole), form.itemAt(row, QFormLayout.FieldRole)
            if name and value and isinstance(name.widget(), QLabel) and plain(name.widget().text()).strip() == hole:
                widget = value.widget()
                if isinstance(widget, QLabel):
                    return widget
                edits, combos = widget.findChildren(QLineEdit), widget.findChildren(QComboBox)
                assert len(edits) == 1 and len(combos) == 1, f"the row {hole}: {edits}, {combos}"
                return edits[0], combos[0]
    raise AssertionError(f"no row {hole} in Input values")


def type_into(edit: QLineEdit, text: str):
    """Select all, type, Enter."""
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, text)
    QTest.keyClick(edit, Qt.Key_Return)
    settle()


def no_boxes(win) -> list:
    """The message boxes shown (then forgotten: the test says what was wrong, not the fixture)."""
    shown = list(win.message_boxes)
    win.message_boxes.clear()
    return shown


def run_ended(win, qtbot, tab, runs: int):
    """Wait until the editor's run after the `runs` runs of the tab has ended (or a box said the flow has problems):
    it ended well, else what the box or the Log said."""
    qtbot.waitUntil(lambda: bool(win.message_boxes) or (len(tab.runs) > runs and not tab.running
                                                        and tab.runs[-1].status is not None), timeout=60_000)
    assert no_boxes(win) == []
    assert tab.runs[-1].status == "success", win.log.text.toPlainText()[-2000:]


def run_now(win, qtbot, tab):
    """Run > Run now, until the run has ended well."""
    runs = len(tab.runs)
    menu(win, "▶ Run now").trigger()
    run_ended(win, qtbot, tab, runs)


@pytest.fixture
def gives_back_win(tmp_path, request):
    """The editor, started with the reusable block Gives Back (Global: a file in the blocks folder, as Create Reusable
    block… writes it), whose input x it gives back at its output."""
    write_reusable_block(Home(tmp_path / "home").blocks_dir, "Gives Back", "Gives back what arrives at x.",
                         "x: anything", {"inputs": ["x"], "outputs": ["output"], "use_environment": False,
                                         "code": "output = x"})
    return request.getfixturevalue("win")


# --- TV-1: double-click or Set value…; Enter keeps it, Esc leaves everything as it was --------------------------------

@pytest.mark.invariant("TV-1")
def test_double_clicking_a_hole_opens_the_box_beside_it_and_enter_keeps_the_typed_value(win, qtbot, tmp_path):
    """TV-1: a double-click on filter's threshold hole opens the box beside it, naming filter.threshold, with the types
    of flow parameters and the hint; choose int, type 10, Enter: the box closes, the block shows 10, the saved file
    holds {type: int, value: "10"} and the run gives the block the number 10."""
    flow, out = filter_flow(tmp_path)
    tab = open_in(win, flow)

    box = box_for(qtbot, win, tab, "filter", "threshold")

    assert {"filter.threshold", HINT} <= set(box_texts(box)), box_texts(box)
    types = part(box, QListWidget, TYPE_LIST)
    assert [types.item(i).text() for i in range(types.count())] == TYPES
    error = part(box, QLabel, ERROR_TEXT)
    assert not error.isVisible() or not error.text().strip()
    check_beside(tab, box, "filter", "threshold")
    fill_in(box, "int", "10")
    key(win, box, Qt.Key_Return)

    assert open_box(win) is None
    assert shown_value(tab, "filter", "threshold") == "10"
    assert saved_values(win, tab, "filter") == {"threshold": typed("int", "10")}
    run_now(win, qtbot, tab)
    assert recorded(out)[-1] == as_recorded({"rows": [1, 2, 3], "threshold": 10})


@pytest.mark.invariant("TV-1")
def test_set_value_in_the_holes_menu_opens_the_same_box(win, qtbot, tmp_path):
    """TV-1: right-click the hole, choose Set value…: the same box opens beside it; float 2.5, Enter: kept."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)

    items, _ = hole_menu(qtbot, win, tab, "filter", "threshold", SET)

    assert offers(items, SET), items
    box = the_box(qtbot, win, "Set value… in the menu of filter.threshold")
    assert {"filter.threshold", HINT} <= set(box_texts(box)), box_texts(box)
    check_beside(tab, box, "filter", "threshold")
    fill_in(box, "float", "2.5")
    key(win, box, Qt.Key_Return)
    assert open_box(win) is None
    assert shown_value(tab, "filter", "threshold") == "2.5"
    assert saved_values(win, tab, "filter") == {"threshold": typed("float", "2.5")}


@pytest.mark.invariant("TV-1")
def test_esc_closes_the_box_and_leaves_everything_as_it_was(win, qtbot, tmp_path):
    """TV-1: on a hole with the value 10 (int), the box starts from it; change it to the float 2.5 and press Esc: the
    box closes, the block still shows 10, the flow has no unsaved change and the file is as it was. On a hole without
    a value, a value typed and Esc: still no value."""
    flow, _ = filter_flow(tmp_path, {"threshold": typed("int", "10")}, {"other": {**python(["x"]), **at(300, 250)}})
    before = flow.read_bytes()
    tab = open_in(win, flow)

    box = box_for(qtbot, win, tab, "filter", "threshold")
    assert part(box, QListWidget, TYPE_LIST).currentItem().text() == "int"
    assert part(box, QLineEdit, VALUE_FIELD).text() == "10"
    fill_in(box, "float", "2.5")
    key(win, box, Qt.Key_Escape)

    assert open_box(win) is None
    assert shown_value(tab, "filter", "threshold") == "10"
    assert unchanged(win, tab)

    box = box_for(qtbot, win, tab, "other", "x")
    fill_in(box, "int", "7")
    key(win, box, Qt.Key_Escape)

    assert open_box(win) is None
    assert shown_value(tab, "other", "x") is None
    assert unchanged(win, tab)
    assert flow.read_bytes() == before
    assert saved_values(win, tab, "filter") == {"threshold": typed("int", "10")}
    assert saved_values(win, tab, "other") == {}


@pytest.mark.invariant("TV-1")
@pytest.mark.parametrize("type_, text", [("int", "1,5"), ("float", "1,5"), ("date", "2026-02-30")])
def test_a_value_that_does_not_fit_its_type_is_not_kept_and_the_box_says_why(win, qtbot, tmp_path, type_, text):
    """T5 (TV-1): "1,5" as an int or a float, 2026-02-30 as a date: Enter keeps nothing, the box stays open and says
    why, naming the type; the flow has no change. A value that fits, then Enter, is kept."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)

    box = box_for(qtbot, win, tab, "filter", "threshold")
    fill_in(box, type_, text)
    key(win, box, Qt.Key_Return)

    box = open_box(win)
    assert box is not None, f"{text!r} as {type_}: the box closed"
    error = part(box, QLabel, ERROR_TEXT)
    assert error.isVisible() and type_ in plain(error.text()), plain(error.text())
    assert part(box, QLineEdit, VALUE_FIELD).text() == text  # (to correct it)
    assert unchanged(win, tab) and shown_value(tab, "filter", "threshold") is None

    fill_in(box, "text")
    key(win, box, Qt.Key_Return)
    assert open_box(win) is None
    assert saved_values(win, tab, "filter") == {"threshold": typed("text", text)}


# --- TV-2: shown in the block, shortened, whole on hover; one value with Properties ----------------------------------

@pytest.mark.invariant("TV-2")
def test_the_value_shows_in_the_block_right_of_the_holes_name_and_whole_on_hover(win, qtbot, tmp_path):
    """TV-2 (T7, light theme): 10 at filter's threshold is drawn on its row, right of the name threshold and at least
    8 px before the output name error; resting the mouse on it shows 10."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    before = row_picture(tab, "filter", "threshold")

    set_on_canvas(qtbot, win, tab, "filter", "threshold", "int", "10")

    assert shown_value(tab, "filter", "threshold") == "10"
    span = check_label_fits(tab, "filter", "threshold", before)
    assert "10" in hover_label(qtbot, tab, "filter", "threshold", span)
    assert saved_values(win, tab, "filter") == {"threshold": typed("int", "10")}


LONG = "0123456789" * 50  # 500 characters
LINES = "first line\n" + "second line, " * 12 + "\nthird line"


@pytest.mark.invariant("TV-2")
def test_a_long_value_and_one_with_line_breaks_are_shortened_in_the_block_and_whole_on_hover_and_at_the_run(
        win, qtbot, tmp_path):
    """T3 (TV-2, TV-3): a 500-character value typed in the box, and a value with line breaks (in the file as the
    editor stores it): each block shows it on one line, shortened with "…", fitting its row; resting the mouse on it
    shows it whole; the file holds it whole and the run gives the block all of it."""
    out = tmp_path / "got.jsonl"
    flow = flow_file(tmp_path / "long.yaml", {
        "start": {"type": "trigger.manual", **at(-300, 0)},
        "long": {**recorder(out, ["x"]), **at(0, 0)},
        "lines": {**recorder(out, ["x"], {"x": typed("text", LINES)}), **at(0, 200)},
        "plain": {**recorder(out, ["x"]), **at(0, 400)}},  # (as the others look without a value)
        ["start.start -> long.after", "start.start -> lines.after"])
    tab = open_in(win, flow)
    before = row_picture(tab, "plain", "x")

    set_on_canvas(qtbot, win, tab, "long", "x", "text", LONG)

    for block, value in (("long", LONG), ("lines", LINES)):
        shown = shown_value(tab, block, "x")
        assert shown is not None and shown.endswith("…") and "\n" not in shown, (block, shown)
        assert squeezed(value).startswith(squeezed(shown[:-1])[:8]), (block, shown)
        span = check_label_fits(tab, block, "x", before)
        assert squeezed(value) in squeezed(hover_label(qtbot, tab, block, "x", span)), block
    assert saved_values(win, tab, "long") == {"x": typed("text", LONG)}
    run_now(win, qtbot, tab)
    assert sorted(map(str, recorded(out))) == sorted(map(str, [as_recorded({"x": LONG}), as_recorded({"x": LINES})]))


@pytest.mark.invariant("TV-2")
def test_properties_input_values_box_and_the_canvas_show_one_value(win, qtbot, tmp_path):
    """T21 (TV-2): Properties of filter has an Input values box: rows says it gets a line from load.rows; threshold
    has a value field and a type. Set 10 (int) on the canvas: Properties shows it at once. Type 25 there, Enter: the
    block shows 25; choose float there: the file holds the float 25 and the box on the canvas starts from it."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    click_block(tab, "filter")

    line = value_row(win, "rows")
    assert isinstance(line, QLabel) and plain(line.text()).strip() == "line from load.rows", line
    edit, kinds = value_row(win, "threshold")
    assert edit.text() == "" and [kinds.itemText(i) for i in range(kinds.count())] == TYPES

    set_on_canvas(qtbot, win, tab, "filter", "threshold", "int", "10")
    edit, kinds = value_row(win, "threshold")
    assert (edit.text(), kinds.currentText()) == ("10", "int")

    type_into(edit, "25")
    assert shown_value(tab, "filter", "threshold") == "25"
    _, kinds = value_row(win, "threshold")
    kinds.setFocus()
    QTest.keyClicks(kinds, "float")
    settle()
    assert saved_values(win, tab, "filter") == {"threshold": typed("float", "25")}
    box = box_for(qtbot, win, tab, "filter", "threshold")
    assert (part(box, QListWidget, TYPE_LIST).currentItem().text(), part(box, QLineEdit, VALUE_FIELD).text()) == \
        ("float", "25")
    key(win, box, Qt.Key_Escape)


# --- T7: the box beside the hole and the value in the block, wherever the hole is -------------------------------------

@pytest.mark.invariant("TV-1")
@pytest.mark.parametrize("percent", [20, 100, 300])
def test_the_box_opens_beside_the_hole_inside_the_canvas_at_any_zoom_and_at_the_canvas_edge(win, qtbot, tmp_path,
                                                                                             percent):
    """T7 (TV-1): at 20 %, 100 % and 300 %, with the hole in the middle of the canvas, at its top-left corner and at its
    bottom-right corner: the box opens beside the hole, wholly in the visible canvas."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    zoom(tab, percent)
    size = tab.view.viewport().size()

    for where in ("middle", (12, 12), (size.width() - 12, size.height() - 12)):
        if where != "middle":
            put_hole_at(tab, "filter", "threshold", *where)
        box = box_for(qtbot, win, tab, "filter", "threshold", centre=where == "middle")
        check_beside(tab, box, "filter", "threshold")
        key(win, box, Qt.Key_Escape)
        assert open_box(win) is None


@pytest.mark.invariant("TV-1")
@pytest.mark.invariant("TV-2")
def test_in_a_loop_the_box_opens_beside_the_hole_and_the_value_fits_the_blocks_row(win, qtbot, tmp_path):
    """T7 (TV-1, TV-2): a block inside a Loop's box (its hole k) and the Loop's own items hole: the box opens beside
    each; the value at k is drawn in its row, right of k and before the output name error."""
    out = tmp_path / "got.jsonl"
    flow = flow_file(tmp_path / "loop.yaml", {
        "start": {"type": "trigger.manual", **at(-400, 0)},
        "each": {"type": "logic.loop", **at(0, 0)},
        "work": {**recorder(out, ["item", "k"]), "inside": "each", **at(120, 80)}},
        ["start.start -> each.after", "each.item -> work.item", "work.output -> each.result"])
    tab = open_in(win, flow)
    before = row_picture(tab, "work", "k")

    for block, hole, type_, text in (("work", "k", "int", "100"), ("each", "items", "list", "a, b, c")):
        box = box_for(qtbot, win, tab, block, hole)
        check_beside(tab, box, block, hole)
        fill_in(box, type_, text)
        key(win, box, Qt.Key_Return)
        assert open_box(win) is None

    assert shown_value(tab, "work", "k") == "100"
    check_label_fits(tab, "work", "k", before)
    assert saved_values(win, tab, "each") == {"items": typed("list", "a, b, c")}


@pytest.mark.invariant("TV-1")
@pytest.mark.invariant("TV-2")
def test_in_the_dark_theme_the_box_opens_beside_the_hole_and_the_value_fits_the_blocks_row(win, qtbot, tmp_path):
    """T7 (TV-1, TV-2): in the Dark theme (Tools > Settings > Theme), the box opens beside the hole and the value shown
    is right of the hole's name and before the row's output name."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    choose(win, "Dark")
    settle()
    before = row_picture(tab, "filter", "threshold")

    box = box_for(qtbot, win, tab, "filter", "threshold")
    check_beside(tab, box, "filter", "threshold")
    fill_in(box, "int", "10")
    key(win, box, Qt.Key_Return)

    assert shown_value(tab, "filter", "threshold") == "10"
    check_label_fits(tab, "filter", "threshold", before)
    assert saved_values(win, tab, "filter") == {"threshold": typed("int", "10")}


# --- T8: the box open while the canvas is drawn again ---------------------------------------------------------------

REBUILDS = ["undo", "tab switch", "theme switch", "run ending"]


@pytest.mark.invariant("TV-1")
@pytest.mark.parametrize("rebuild", REBUILDS)
def test_the_box_open_while_the_canvas_is_drawn_again_leaves_the_value_as_before_or_as_kept(win, qtbot, tmp_path,
                                                                                             rebuild):
    """T8 (TV-1): with the box open on filter.threshold and 77 (int) typed in it, the canvas is drawn again: Undo (of a
    block moved before), a switch to another flow's tab, a switch to the Dark theme, or a run ending. Nothing fails;
    Enter in the box if it is still open (on the other tab, too); then the value is as before (none) or as kept (77),
    the block shows just that, and the other flow has no change."""
    gate = tmp_path / "go"
    flow, _ = filter_flow(tmp_path, more={"gate": {**gated(str(gate)), **at(0, 250)}},
                          edges=["start.start -> gate.after"])
    tab = open_in(win, flow)
    other = None
    if rebuild == "undo":
        load = tab.scene.nodes["load"]
        title = tab.view.mapFromScene(load.mapToScene(QPointF(NODE_W / 2, HEADER_H / 2)))
        mouse_drag(tab, title, title + QPoint(60, 0))
        assert saved(win, tab)["blocks"]["load"]["ui"]["x"] != 0
    elif rebuild == "tab switch":
        other = open_in(win, flow_file(tmp_path / "other.yaml", {"solo": {**python(["threshold"]), **at(0, 0)}}))
        bar = win.tabs.tabBar()
        QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.tabs.indexOf(tab)).center())
        settle()
        assert win.current is tab
    elif rebuild == "run ending":
        menu(win, "▶ Run now").trigger()
        qtbot.waitUntil(lambda: bool(win.message_boxes) or tab.scene.nodes["gate"].status == "running",
                        timeout=60_000)
        assert no_boxes(win) == []

    try:
        box = box_for(qtbot, win, tab, "filter", "threshold")
        fill_in(box, "int", "77")
        if rebuild == "undo":
            menu(win, "Undo").trigger()
        elif rebuild == "tab switch":
            bar = win.tabs.tabBar()
            QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.tabs.indexOf(other)).center())
            settle()
            finish_if_open(win)  # (while the other flow is on screen)
            QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.tabs.indexOf(tab)).center())
        elif rebuild == "theme switch":
            choose(win, "Dark")
    finally:
        if rebuild == "run ending":  # (also when the test fails before: the run is not left going)
            gate.write_text("go")
            qtbot.waitUntil(lambda: not tab.running, timeout=60_000)
    settle()
    finish_if_open(win)

    assert win.current is tab
    kept = saved_values(win, tab, "filter").get("threshold")
    assert kept in (None, typed("int", "77")), kept
    assert shown_value(tab, "filter", "threshold") == (None if kept is None else "77")
    if other is not None:
        assert unchanged(win, other) and shown_value(other, "solo", "threshold") is None


# --- TV-4: a line replaces the value, in one step; a hole with a line offers no value ---------------------------------

@pytest.mark.invariant("TV-4")
@pytest.mark.parametrize("block, hole", [("filter", "threshold"), ("check", "value")],
                         ids=["Python Code input (takes several lines)", "If value"])
def test_connecting_a_line_removes_the_value_in_one_step_that_one_undo_brings_back(win, qtbot, tmp_path, block, hole):
    """T2 (TV-4): type 10 at the hole; connect load's rows to it: the value is gone; Undo: the line is gone and the
    value back; Undo: the value gone; Redo: the value back. Each step as the saved file and the block show it."""
    flow, _ = filter_flow(tmp_path, more={"check": {"type": "logic.if", "config": {"condition": "True"},
                                                    **at(300, 250)}})
    tab = open_in(win, flow)
    line = f"load.rows -> {block}.{hole}"

    def state():
        data = saved(win, tab)
        return (line in data["edges"], (data["blocks"][block].get("inputs") or {}).get(hole),
                shown_value(tab, block, hole))

    set_on_canvas(qtbot, win, tab, block, hole, "int", "10")
    assert state() == (False, typed("int", "10"), "10")
    out, into = tab.scene.nodes["load"].ports[("rows", True)], tab.scene.nodes[block].ports[(hole, False)]
    tab.view.centerOn((out.scenePos() + into.scenePos()) / 2)
    settle()
    mouse_drag(tab, tab.view.mapFromScene(out.scenePos()), tab.view.mapFromScene(into.scenePos()))
    assert state() == (True, None, None)
    menu(win, "Undo").trigger()
    assert state() == (False, typed("int", "10"), "10")
    menu(win, "Undo").trigger()
    assert state() == (False, None, None)
    menu(win, "Redo").trigger()
    assert state() == (False, typed("int", "10"), "10")


@pytest.mark.invariant("TV-4")
@pytest.mark.parametrize("lined, free", [(("filter", "rows"), ("filter", "threshold")),
                                         (("check", "value"), ("other_check", "value"))],
                         ids=["Python Code input", "If value"])
def test_a_hole_with_a_line_offers_no_set_value(win, qtbot, tmp_path, lined, free):
    """T19 (TV-4): a hole a line comes into: its menu has no Set value… to choose, and a double-click opens no box;
    the same hole of a block without that line (or the block's other hole) offers it."""
    check = {"type": "logic.if", "config": {"condition": "True"}}
    flow, _ = filter_flow(tmp_path, more={"check": {**check, **at(300, 250)}, "other_check": {**check, **at(300, 450)}},
                          edges=["load.rows -> check.value"])
    tab = open_in(win, flow)

    lined_items, _ = hole_menu(qtbot, win, tab, *lined)
    free_items, _ = hole_menu(qtbot, win, tab, *free)

    assert offers(free_items, SET), free_items
    assert lined_items and not offers(lined_items, SET), lined_items
    assert double_click_hole(qtbot, win, tab, *lined) is None


# --- TV-5: the holes that offer no typed value say why ---------------------------------------------------------------

@pytest.mark.invariant("TV-5")
@pytest.mark.parametrize("spec, hole, why", list(NOT_OFFERED.values()), ids=list(NOT_OFFERED))
def test_a_hole_that_takes_no_typed_value_offers_none_and_its_menu_says_why(win, qtbot, tmp_path, spec, hole, why):
    """T18 (TV-5): table inputs, holes that collect many lines, run after, holes that expect a dictionary and the
    Loop's result: the menu's Set value… cannot be chosen and its tooltip (shown) says why; a double-click opens no
    box."""
    tab = open_in(win, one_block_flow(tmp_path, spec))

    items, shows_tips = hole_menu(qtbot, win, tab, "block", hole)

    assert SET in items, f"the menu of {hole} does not say why it offers no value: {items}"
    enabled, tip = items[SET]
    assert not enabled and shows_tips and why in plain(tip).lower(), (enabled, shows_tips, tip)
    assert double_click_hole(qtbot, win, tab, "block", hole) is None


@pytest.mark.invariant("TV-5")
@pytest.mark.invariant("TV-1")
@pytest.mark.parametrize("spec, hole", list(OFFERED.values()), ids=list(OFFERED))
def test_a_plain_hole_offers_set_value_and_a_double_click_opens_the_box(win, qtbot, tmp_path, spec, hole):
    """T18 (TV-5, TV-1): Python Code's and Inspect's inputs (which also accept several lines), the If's, Splitter's and
    Loop's items, Send Email's subject, API Request's body: Set value… opens the box, and so does a double-click."""
    tab = open_in(win, one_block_flow(tmp_path, spec))

    items, _ = hole_menu(qtbot, win, tab, "block", hole, SET)

    assert offers(items, SET), items
    box = the_box(qtbot, win, f"Set value… in the menu of {hole}")
    assert f"block.{hole}" in box_texts(box)
    key(win, box, Qt.Key_Escape)
    box = box_for(qtbot, win, tab, "block", hole)
    key(win, box, Qt.Key_Escape)


@pytest.mark.invariant("TV-5")
def test_a_reusable_blocks_input_offers_set_value(gives_back_win, qtbot, tmp_path):
    """T18 (TV-5): the input x of a copy of a reusable block (Global) offers Set value…, which opens the box."""
    win = gives_back_win
    tab = open_in(win, one_block_flow(tmp_path, {"type": "user.gives_back"}))

    items, _ = hole_menu(qtbot, win, tab, "block", "x", SET)

    assert offers(items, SET), items
    key(win, the_box(qtbot, win, "Set value… in the menu of x"), Qt.Key_Escape)


# --- TV-6: "with inputs I choose…" starts from the typed value --------------------------------------------------------

NOTE = "line one\nline two"


def as_python(text: str):
    """What a field of the "with inputs I choose…" window gives the block: its text as Python."""
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return f"<not a Python value: {text!r}>"


def held(value) -> tuple:
    return type(value).__name__, value


@pytest.mark.invariant("TV-6")
@pytest.mark.parametrize("item", [ONLY_CHOOSE, FROM_CHOOSE], ids=["run only", "run from"])
@pytest.mark.parametrize("earlier", ["no run yet", "after a run that got 10"])
def test_with_inputs_i_choose_starts_from_the_typed_values(win, qtbot, tmp_path, monkeypatch, item, earlier):
    """T11 (TV-6): use has the typed values x (int) and note (a text with a line break). Its "with inputs I choose…"
    window shows them as Python you can edit: x as the number, note with its line break; after a run that gave x 10
    and a change of x to 50 on the canvas, x shows 50 (today's value). Run as shown: the block gets them."""
    out = tmp_path / "got.jsonl"
    flow = flow_file(tmp_path / "choose.yaml", {
        "start": {"type": "trigger.manual", **at(-300, 0)},
        "use": {**recorder(out, ["x", "note"], {"x": typed("int", "10"), "note": typed("text", NOTE)}), **at(0, 0)}},
        ["start.start -> use.after"])
    tab = open_in(win, flow)
    x = 10
    if earlier != "no run yet":
        run_now(win, qtbot, tab)
        assert recorded(out)[-1] == as_recorded({"x": 10, "note": NOTE})
        set_on_canvas(qtbot, win, tab, "use", "x", "int", "50")
        x = 50
    seen = {}

    def exec_(dialog):  # the window: note what it shows, press Run
        seen.update({name: f.text() for name, f in dialog.fields.items()})
        return True

    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", exec_)
    runs = len(tab.runs)
    right_click(qtbot, win, tab, "use", "title", item)
    run_ended(win, qtbot, tab, runs)

    assert {name: held(as_python(text)) for name, text in seen.items()} == {"x": held(x), "note": held(NOTE)}, seen
    assert recorded(out)[-1] == as_recorded({"x": x, "note": NOTE})


# --- TV-7: Clear value; an empty text is a value ---------------------------------------------------------------------

@pytest.mark.invariant("TV-7")
def test_an_empty_text_is_a_value_and_clear_value_leaves_none(win, qtbot, tmp_path):
    """T4 (TV-7): an empty text typed at use's hole x is a value: the block shows it holds one, the file has it, the
    menu offers Clear value and the run gives the block "". Clear value: the file has none, the block shows none, the
    menu no longer offers Clear value, and the run gives the block nothing (None)."""
    out = tmp_path / "got.jsonl"
    flow = flow_file(tmp_path / "empty.yaml", {
        "start": {"type": "trigger.manual", **at(-300, 0)},
        "use": {**recorder(out, ["x"]), **at(0, 0)}}, ["start.start -> use.after"])
    tab = open_in(win, flow)
    before = row_picture(tab, "use", "x")

    set_on_canvas(qtbot, win, tab, "use", "x", "text", "")

    assert label_span(before, row_picture(tab, "use", "x")) is not None, "the block does not show that x has a value"
    assert saved_values(win, tab, "use") == {"x": typed("text", "")}
    items, _ = hole_menu(qtbot, win, tab, "use", "x")
    assert offers(items, CLEAR), items
    run_now(win, qtbot, tab)
    assert recorded(out)[-1] == as_recorded({"x": ""})

    items, _ = hole_menu(qtbot, win, tab, "use", "x", CLEAR)

    assert offers(items, CLEAR)
    assert saved_values(win, tab, "use") == {}
    assert label_span(before, row_picture(tab, "use", "x")) is None
    assert not offers(hole_menu(qtbot, win, tab, "use", "x")[0], CLEAR)
    run_now(win, qtbot, tab)
    assert recorded(out)[-1] == as_recorded({"x": None})


# --- TV-8: renaming, removing, copy and paste, copies of a reusable block ---------------------------------------------

@pytest.mark.invariant("TV-8")
def test_renaming_a_python_input_keeps_its_value_and_removing_it_removes_the_value(win, qtbot, tmp_path):
    """T15 (TV-8): filter's threshold has 10. Its Inputs in Properties "rows, threshold" → "rows, limit" (the same
    count: renamed by position): the value is at limit, in the file and in the block. Inputs "rows": the value is
    gone."""
    flow, _ = filter_flow(tmp_path, {"threshold": typed("int", "10")})
    tab = open_in(win, flow)
    click_block(tab, "filter")

    type_into(field(win, "Inputs"), "rows, limit")

    assert saved_values(win, tab, "filter") == {"limit": typed("int", "10")}
    assert shown_value(tab, "filter", "limit") == "10"

    click_block(tab, "filter")
    type_into(field(win, "Inputs"), "rows")

    assert saved_values(win, tab, "filter") == {}
    assert shown_value(tab, "filter", "rows") is None


@pytest.mark.invariant("TV-8")
def test_copy_and_paste_keep_the_typed_values(win, qtbot, tmp_path):
    """T15 (TV-8): filter (threshold 10) selected, Edit > Copy, Edit > Paste: the pasted block has threshold 10 too,
    in the file and on the canvas; the original keeps its own."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    set_on_canvas(qtbot, win, tab, "filter", "threshold", "int", "10")
    click_block(tab, "filter")
    blocks = set(tab.scene.nodes)

    menu(win, "Copy").trigger()
    empty = QPointF(800, 0)  # (right of the flow: nothing there)
    tab.view.centerOn(empty)
    settle()
    items, _ = context_menu(qtbot, win, tab, tab.view.mapFromScene(empty), "Paste")
    assert offers(items, "Paste"), items

    (pasted,) = set(tab.scene.nodes) - blocks
    assert shown_value(tab, pasted, "threshold") == "10"
    data = saved(win, tab)
    assert {b: data["blocks"][b].get("inputs") for b in ("filter", pasted)} == {
        "filter": {"threshold": typed("int", "10")}, pasted: {"threshold": typed("int", "10")}}


@pytest.mark.invariant("TV-8")
def test_each_copy_of_a_reusable_block_has_its_own_typed_values(gives_back_win, qtbot, tmp_path):
    """T15 (TV-8): two copies of the reusable block Gives Back: 10 typed at one's x, 50 at the other's. Each shows
    and keeps its own; the run gives each its own (got records them)."""
    win = gives_back_win
    out = tmp_path / "got.jsonl"
    flow = flow_file(tmp_path / "copies.yaml", {
        "start": {"type": "trigger.manual", **at(-300, 0)},
        "one": {"type": "user.gives_back", **at(0, 0)},
        "two": {"type": "user.gives_back", **at(0, 200)},
        "got": {**recorder(out, ["a", "b"]), **at(300, 100)}},
        ["start.start -> one.after", "start.start -> two.after", "one.output -> got.a", "two.output -> got.b"])
    tab = open_in(win, flow)

    set_on_canvas(qtbot, win, tab, "one", "x", "int", "10")
    set_on_canvas(qtbot, win, tab, "two", "x", "int", "50")

    assert (shown_value(tab, "one", "x"), shown_value(tab, "two", "x")) == ("10", "50")
    data = saved(win, tab)
    assert {b: data["blocks"][b].get("inputs") for b in ("one", "two")} == {
        "one": {"x": typed("int", "10")}, "two": {"x": typed("int", "50")}}
    run_now(win, qtbot, tab)
    assert recorded(out)[-1] == as_recorded({"a": 10, "b": 50})


# --- TV-9: Validate and Run now in the editor on a hand-edited file --------------------------------------------------

@pytest.mark.invariant("TV-9")
@pytest.mark.parametrize("hole, values, edges", list(HAND_EDITS.values()), ids=list(HAND_EDITS))
def test_validate_and_run_now_in_the_editor_name_the_block_and_hole_of_a_hand_edited_value(win, qtbot, tmp_path,
                                                                                           hole, values, edges):
    """T17 (TV-9, the editor): a flow file edited by hand with a typed value and a line on one hole, a value that does
    not fit its type, or a value on a hole the block does not have opens in the editor; Validate says the flow has
    problems, naming the block and the hole; Run now says the same and starts no run."""
    out = tmp_path / "got.jsonl"
    flow = flow_file(tmp_path / "hand.yaml", {
        "start": {"type": "trigger.manual", **at(-300, 0)},
        "src": {**python([], "output = 1"), **at(0, 0)},
        "receiver": {**recorder(out, ["typed_in"], values), **at(300, 0)}},
        ["start.start -> src.after", "start.start -> receiver.after", *edges])
    tab = open_in(win, flow)

    def naming() -> list[str]:
        (kind, (title, text)), = win.message_boxes
        win.message_boxes.clear()
        assert (kind, title) == ("warning", "The flow has problems"), (kind, title, text)
        return [line for line in text.splitlines() if "receiver" in line and hole in line]

    menu(win, "Validate").trigger()
    settle()
    validated = naming()
    menu(win, "▶ Run now").trigger()
    settle()
    refused = naming()

    assert validated and refused == validated, (validated, refused)
    assert not tab.runs and recorded(out) == []
