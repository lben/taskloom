"""Pasted blocks and texts land where you are working on the canvas, not next to where they were copied from: with the
pointer over the canvas (Ctrl+V), the top-left corner of the pasted group goes to the pointer; with the pointer
elsewhere (Paste from the Edit menu, the pointer over a panel), the group goes to the centre of the visible canvas;
Paste on the empty canvas's right-click menu puts its top-left corner at the spot clicked; pasting again at the same
place puts the new copies 40 px right and down of the previous ones. In the same flow or another flow's tab, at any
zoom. Through the real editor: the pointer is the real mouse cursor (the widget under it gets the move, as from the
mouse), Ctrl+V the real shortcut, the menus the real menus clicked with the mouse.

The group's top-left corner is the smallest x and the smallest y of the pasted blocks' and texts' positions (each
block's and text's own top-left corner, as the flow file stores it)."""

import copy
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QCursor, QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.test_paths_editor_editing import clean, flow_file, menu, settle  # noqa: E402
from tests.test_paths_editor_running import MANUAL  # noqa: E402

LOAD = {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}, "ui": {"x": 0, "y": 0}}
REPORT = {"type": "logic.python", "config": {"code": "pass"}, "ui": {"x": 300, "y": 40}}
NOTE = {"text": "Nightly load", "x": 120, "y": -160}
# The copied group (load, report and the text) has its top-left corner at (0, -160): load's left, the text's top.
# Where each copy must be from the pasted group's top-left corner, by what it is (its code, or its text).
LAYOUT = {"output = 1": (0, 160), "pass": (300, 200), "Nightly load": (120, 0)}
ORIGINALS = QRectF(-300, -300, 1000, 600)  # where the flow's blocks and text are


def report_flow(path):
    return flow_file(path, {"start": {**MANUAL, "ui": {"x": -250, "y": 0}}, "load": LOAD, "report": REPORT},
                     ["start.start -> load.after", "load.output -> report.input"], notes={"note_1": NOTE})


def copy_group(win, tab):
    """Select load, report and the text; Edit > Copy (copying is unchanged)."""
    QApplication.clipboard().setText("")
    settle()
    tab.scene.clearSelection()
    for block in ("load", "report"):
        tab.scene.nodes[block].setSelected(True)
    tab.scene.note_items["note_1"].setSelected(True)
    menu(win, "Copy").trigger()
    settle()
    assert "Nightly load" in QApplication.clipboard().text()


def visible(tab) -> QRectF:
    """The part of the flow the canvas shows."""
    return tab.view.mapToScene(tab.view.viewport().rect()).boundingRect()


def look_away(tab, zoom=100):
    """Zoom, and scroll the canvas to empty space far from the flow's blocks and text (none of them is on screen)."""
    settle()  # (an opened flow centres itself just after)
    tab.view.zoom_to(zoom)
    tab.view.centerOn(QPointF(3000, 2500))
    settle()
    assert round(tab.view.zoom_percent()) == zoom
    assert not visible(tab).intersects(ORIGINALS)


def spot(tab, fx: float, fy: float) -> QPoint:
    """A spot of the visible canvas, at these fractions of its width and height (whatever its size), not on the
    zoom control."""
    settle()
    viewport = tab.view.viewport()
    at = QPoint(round(viewport.width() * fx), round(viewport.height() * fy))
    assert viewport.rect().contains(at), (viewport.rect(), at)
    assert not tab.view.zoom_bar.geometry().contains(viewport.mapToParent(at))
    return at


def point_to(widget, at: QPoint):
    """Move the mouse pointer to `at` on `widget`."""
    QCursor.setPos(widget.mapToGlobal(at))
    settle()
    assert QCursor.pos() == widget.mapToGlobal(at)


def ctrl_v(win, tab):
    """Ctrl+V (on a Mac, Cmd+V) with the canvas focused; the pointer stays where it is."""
    win.activateWindow()
    tab.view.setFocus()
    QTest.keySequence(tab.view, QKeySequence(QKeySequence.Paste))
    settle()


def choose_from_edit_menu(win, qtbot, text: str):
    """Click Edit in the menu bar, move the pointer onto the item and click it, as with the mouse."""
    bar = win.menuBar()
    edit = next(a for a in bar.actions() if a.text() == "&Edit")
    title = bar.actionGeometry(edit).center()
    point_to(bar, title)
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, title)
    popup = edit.menu()
    qtbot.waitUntil(popup.isVisible, timeout=5_000)
    item = next(a for a in popup.actions() if a.text() == text)
    at = popup.actionGeometry(item).center()
    point_to(popup, at)
    QTest.mouseClick(popup, Qt.LeftButton, Qt.NoModifier, at)
    qtbot.waitUntil(lambda: not popup.isVisible(), timeout=5_000)
    settle()


def right_click_and_choose(tab, at: QPoint, starts_with: str) -> list[str]:
    """Right-click the canvas at `at` (the pointer there first); in the menu that opens, move the pointer onto the
    first enabled item starting with `starts_with` and click it (none: the menu is closed). Returns the menu's items
    ([]: no menu opened)."""
    viewport = tab.view.viewport()
    point_to(viewport, at)
    opened, finished, tries = [], [], []

    def look():  # (the menu waits for you inside the right-click)
        if finished:
            return
        popup = QApplication.activePopupWidget()
        if popup is None:
            tries.append(1)
            if len(tries) < 250:  # up to 5 s: a busy machine can take a while to open it
                QTimer.singleShot(20, look)
            return
        opened.extend(a.text() for a in popup.actions() if a.text())
        item = next((a for a in popup.actions() if a.text().startswith(starts_with) and a.isEnabled()), None)
        if item is not None:
            spot_on_menu = popup.actionGeometry(item).center()
            point_to(popup, spot_on_menu)  # the pointer leaves the spot clicked, onto the menu
            QTest.mouseClick(popup, Qt.LeftButton, Qt.NoModifier, spot_on_menu)
        if popup.isVisible():
            popup.close()

    QTimer.singleShot(0, look)
    # What the window sends the canvas when you right-click it.
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, viewport.mapToGlobal(at)))
    finished.append(1)
    settle()
    popup = QApplication.activePopupWidget()  # (never leave a menu open)
    if popup is not None:
        opened.extend(a.text() for a in popup.actions() if a.text())
        popup.close()
    return opened


def pasted(tab, before: dict) -> tuple[list[str], list[str]]:
    """The blocks and texts a paste added: those not in `before` (the flow's data before it)."""
    return ([b for b in tab.doc.blocks if b not in before["blocks"]],
            [n for n in tab.doc.notes if n not in (before.get("notes") or {})])


def top_left(tab, blocks, notes) -> tuple[int, int]:
    xs = [tab.doc.blocks[b]["ui"]["x"] for b in blocks] + [tab.doc.notes[n]["x"] for n in notes]
    ys = [tab.doc.blocks[b]["ui"]["y"] for b in blocks] + [tab.doc.notes[n]["y"] for n in notes]
    return min(xs), min(ys)


def layout(tab, blocks, notes) -> dict:
    """Where each pasted copy is from the group's top-left corner, by its code (a block) or its text."""
    x0, y0 = top_left(tab, blocks, notes)
    where = {tab.doc.blocks[b]["config"]["code"]: (tab.doc.blocks[b]["ui"]["x"] - x0, tab.doc.blocks[b]["ui"]["y"] - y0)
             for b in blocks}
    where.update({tab.doc.notes[n]["text"]: (tab.doc.notes[n]["x"] - x0, tab.doc.notes[n]["y"] - y0) for n in notes})
    return where


def near(tab, corner: tuple[int, int], at: QPointF) -> bool:
    """`corner` is at the flow point `at`, within a pixel of the screen (the pointer is on a pixel; positions are
    stored as whole numbers)."""
    pixel = 1 / tab.view.transform().m11()
    return abs(corner[0] - at.x()) <= pixel + 1 and abs(corner[1] - at.y()) <= pixel + 1


def group_rect(tab, blocks, notes) -> QRectF:
    """The part of the canvas the pasted copies cover."""
    items = [tab.scene.nodes[b] for b in blocks] + [tab.scene.note_items[n] for n in notes]
    rect = items[0].sceneBoundingRect()
    for item in items[1:]:
        rect = rect.united(item.sceneBoundingRect())
    return rect


def assert_selected_and_shown(tab, blocks, notes):
    """The copies, and only they, are selected (to move them), and they are drawn in the visible canvas."""
    assert sorted(tab.scene.selected_block_ids()) == sorted(blocks)
    assert sorted(tab.scene.selected_note_ids()) == sorted(notes)
    assert all(tab.scene.nodes[b].isVisible() for b in blocks)
    assert all(tab.scene.note_items[n].isVisible() for n in notes)
    assert visible(tab).intersects(group_rect(tab, blocks, notes)), (visible(tab), group_rect(tab, blocks, notes))


def copies_connection(tab, blocks) -> tuple[set, tuple]:
    """The connections the paste added (from or into a copy), and the one between the copies of load and report."""
    by_code = {tab.doc.blocks[b]["config"]["code"]: b for b in blocks}
    added = {e for e in tab.doc.edges() if e[0] in blocks or e[2] in blocks}
    return added, (by_code["output = 1"], "output", by_code["pass"], "input")


def assert_centred(tab, blocks, notes):
    """The pasted group is at the centre of the visible canvas: that centre is on the copies' part of the canvas."""
    centre = tab.view.mapToScene(tab.view.viewport().rect().center())
    margin = 1 / tab.view.transform().m11() + 1
    rect = group_rect(tab, blocks, notes)
    assert rect.adjusted(-margin, -margin, margin, margin).contains(centre), (rect, centre)


# --- 1. Ctrl+V with the pointer over the canvas -----------------------------------------------------------------

def test_ctrl_v_with_the_pointer_over_the_canvas_puts_the_copies_top_left_corner_at_the_pointer(win, qtbot, tmp_path):
    """Spec 1, the user's scenario (and 5): two connected blocks and a text copied, the canvas scrolled to empty space
    far from them, the pointer there, Ctrl+V: the copies keep their layout, the group's top-left corner is at the
    pointer, they are selected and shown, the connection between them is kept, the originals are unchanged, and one
    Undo removes the whole paste."""
    qtbot.waitExposed(win)
    tab = win.open_flow(report_flow(tmp_path / "report.yaml"))
    copy_group(win, tab)
    original = copy.deepcopy(tab.doc.data)
    look_away(tab)
    at = spot(tab, 0.3, 0.6)
    point_to(tab.view.viewport(), at)

    ctrl_v(win, tab)

    blocks, notes = pasted(tab, original)
    assert len(blocks) == 2 and len(notes) == 1, (tab.doc.blocks, tab.doc.notes)
    assert layout(tab, blocks, notes) == LAYOUT  # as they were to each other
    corner = top_left(tab, blocks, notes)
    assert near(tab, corner, tab.view.mapToScene(at)), (corner, tab.view.mapToScene(at))  # at the pointer
    assert_selected_and_shown(tab, blocks, notes)
    added, between = copies_connection(tab, blocks)
    assert added == {between}  # the copies' own connection, not the one from start
    assert {b: tab.doc.blocks[b] for b in original["blocks"]} == original["blocks"]
    assert tab.doc.notes["note_1"] == NOTE and set(tab.doc.data["edges"]) >= set(original["edges"])

    menu(win, "Undo").trigger()  # one step takes back the whole paste
    settle()
    assert tab.doc.data == original
    assert set(tab.scene.nodes) == set(original["blocks"]) and set(tab.scene.note_items) == {"note_1"}
    clean(win)


# --- 2. the pointer not over the canvas: the centre of the visible canvas ---------------------------------------

def test_paste_chosen_from_the_edit_menu_puts_the_copies_at_the_centre_of_the_visible_canvas(win, qtbot, tmp_path):
    """Spec 2 (and 4): Edit > Paste clicked with the mouse (the pointer on the menu, not on the canvas): the copies,
    in their layout, are at the centre of the visible canvas, selected; Edit > Paste again puts the new copies 40 px
    right and down of the previous ones; one Undo removes only that paste."""
    qtbot.waitExposed(win)
    tab = win.open_flow(report_flow(tmp_path / "report.yaml"))
    copy_group(win, tab)
    original = copy.deepcopy(tab.doc.data)
    look_away(tab)

    choose_from_edit_menu(win, qtbot, "Paste")

    blocks, notes = pasted(tab, original)
    assert len(blocks) == 2 and len(notes) == 1, (tab.doc.blocks, tab.doc.notes)
    assert layout(tab, blocks, notes) == LAYOUT
    assert_centred(tab, blocks, notes)
    assert_selected_and_shown(tab, blocks, notes)
    first = top_left(tab, blocks, notes)
    after_first = copy.deepcopy(tab.doc.data)

    choose_from_edit_menu(win, qtbot, "Paste")  # again, the canvas not moved

    blocks, notes = pasted(tab, after_first)
    assert len(blocks) == 2 and len(notes) == 1, (tab.doc.blocks, tab.doc.notes)
    assert layout(tab, blocks, notes) == LAYOUT
    assert top_left(tab, blocks, notes) == (first[0] + 40, first[1] + 40)  # not hidden under the first copies
    assert_selected_and_shown(tab, blocks, notes)
    menu(win, "Undo").trigger()
    settle()
    assert tab.doc.data == after_first
    clean(win)


def test_ctrl_v_with_the_pointer_over_a_panel_puts_the_copies_at_the_centre_of_the_visible_canvas(win, qtbot,
                                                                                                  tmp_path):
    """Spec 2: the canvas focused but the pointer over a panel (the blocks list), Ctrl+V: the copies, in their layout,
    are at the centre of the visible canvas, selected and shown."""
    qtbot.waitExposed(win)
    tab = win.open_flow(report_flow(tmp_path / "report.yaml"))
    copy_group(win, tab)
    original = copy.deepcopy(tab.doc.data)
    look_away(tab)
    panel = win.palette_panel
    assert panel.isVisible()
    point_to(panel, panel.rect().center())
    viewport = tab.view.viewport()
    assert not viewport.rect().contains(viewport.mapFromGlobal(QCursor.pos()))  # not over the canvas

    ctrl_v(win, tab)

    blocks, notes = pasted(tab, original)
    assert len(blocks) == 2 and len(notes) == 1, (tab.doc.blocks, tab.doc.notes)
    assert layout(tab, blocks, notes) == LAYOUT
    assert_centred(tab, blocks, notes)
    assert_selected_and_shown(tab, blocks, notes)
    clean(win)


# --- 3. Paste on the empty canvas's right-click menu -----------------------------------------------------------

def test_paste_on_the_empty_canvas_right_click_menu_puts_the_copies_top_left_corner_at_the_spot_clicked(win, qtbot,
                                                                                                         tmp_path):
    """Spec 3: right-click an empty spot of the canvas: its menu has Paste; choosing it (the pointer then on the menu,
    not on the spot) puts the group's top-left corner at the spot clicked, in its layout, selected, the connection
    kept; one Undo removes it."""
    qtbot.waitExposed(win)
    tab = win.open_flow(report_flow(tmp_path / "report.yaml"))
    copy_group(win, tab)
    original = copy.deepcopy(tab.doc.data)
    look_away(tab)
    at = spot(tab, 0.3, 0.5)
    clicked = tab.view.mapToScene(at)

    items = right_click_and_choose(tab, at, "Paste")

    assert any(i.startswith("Paste") for i in items), items
    blocks, notes = pasted(tab, original)
    assert len(blocks) == 2 and len(notes) == 1, (tab.doc.blocks, tab.doc.notes)
    assert layout(tab, blocks, notes) == LAYOUT
    corner = top_left(tab, blocks, notes)
    assert near(tab, corner, clicked), (corner, clicked)  # the spot clicked, not where the pointer went after
    assert_selected_and_shown(tab, blocks, notes)
    added, between = copies_connection(tab, blocks)
    assert added == {between}
    menu(win, "Undo").trigger()
    settle()
    assert tab.doc.data == original
    clean(win)


# --- 4. pasting again at the same place ------------------------------------------------------------------------

def test_ctrl_v_again_at_the_same_place_puts_each_new_copy_40_px_right_and_down_of_the_previous_one(win, qtbot,
                                                                                                   tmp_path):
    """Spec 4 (and 1): Ctrl+V three times, the pointer not moved: the first copies at the pointer, each next one
    40 px right and down of the previous paste (never exactly under another); the pointer moved, Ctrl+V: the copies'
    top-left corner is at the pointer again."""
    qtbot.waitExposed(win)
    tab = win.open_flow(report_flow(tmp_path / "report.yaml"))
    copy_group(win, tab)
    look_away(tab, zoom=100)
    at = spot(tab, 0.3, 0.5)
    point_to(tab.view.viewport(), at)
    corners = []

    for _ in range(3):
        before = copy.deepcopy(tab.doc.data)
        ctrl_v(win, tab)
        blocks, notes = pasted(tab, before)
        assert len(blocks) == 2 and len(notes) == 1, (tab.doc.blocks, tab.doc.notes)
        assert layout(tab, blocks, notes) == LAYOUT
        assert sorted(tab.scene.selected_block_ids()) == sorted(blocks)  # the latest copies
        corners.append(top_left(tab, blocks, notes))

    assert near(tab, corners[0], tab.view.mapToScene(at)), (corners, tab.view.mapToScene(at))
    assert corners[1] == (corners[0][0] + 40, corners[0][1] + 40), corners
    assert corners[2] == (corners[1][0] + 40, corners[1][1] + 40), corners
    elsewhere = spot(tab, 0.7, 0.3)
    point_to(tab.view.viewport(), elsewhere)
    before = copy.deepcopy(tab.doc.data)

    ctrl_v(win, tab)

    blocks, notes = pasted(tab, before)
    corner = top_left(tab, blocks, notes)
    assert near(tab, corner, tab.view.mapToScene(elsewhere)), (corner, tab.view.mapToScene(elsewhere))
    clean(win)


# --- 5. in another flow's tab, at any zoom -----------------------------------------------------------------------

def test_ctrl_v_in_another_flows_tab_at_any_zoom_puts_the_copies_top_left_corner_at_the_pointer(win, qtbot, tmp_path):
    """Spec 5 (and 1): copied in one flow, Ctrl+V in a new flow's tab zoomed to 150% and then to 50%, the pointer on
    its canvas: the copies keep their layout (in the flow, whatever the zoom), the group's top-left corner is at the
    pointer, they are selected and shown, their connection kept; one Undo removes each paste; the flow they came from
    is unchanged."""
    qtbot.waitExposed(win)
    source = win.open_flow(report_flow(tmp_path / "source.yaml"))
    copy_group(win, source)
    source_data = copy.deepcopy(source.doc.data)
    target = win.new_flow()
    settle()
    assert win.current is target

    for zoom, (fx, fy) in ((150, (0.3, 0.6)), (50, (0.6, 0.4))):
        look_away(target, zoom)
        before = copy.deepcopy(target.doc.data)
        at = spot(target, fx, fy)
        point_to(target.view.viewport(), at)

        ctrl_v(win, target)

        blocks, notes = pasted(target, before)
        assert len(blocks) == 2 and len(notes) == 1, (zoom, target.doc.blocks, target.doc.notes)
        assert layout(target, blocks, notes) == LAYOUT, zoom
        corner = top_left(target, blocks, notes)
        assert near(target, corner, target.view.mapToScene(at)), (zoom, corner, target.view.mapToScene(at))
        assert_selected_and_shown(target, blocks, notes)
        added, between = copies_connection(target, blocks)
        assert added == {between}, zoom
        menu(win, "Undo").trigger()
        settle()
        assert target.doc.data == before, zoom

    assert source.doc.data == source_data and not source.doc.dirty
    clean(win)


# --- 6. a paste never puts a text under a block (texts are drawn behind blocks) ----------------------------------

@pytest.mark.parametrize("zoom", [100, 200, 300])
@pytest.mark.parametrize("copied, onto", [("note_1", "load"), ("load", "note_1")])
def test_edit_menu_paste_onto_a_block_or_a_text_puts_no_text_under_a_block(win, qtbot, tmp_path, copied, onto, zoom):
    """Spec 5 (at any zoom, the copies are visible) and no surprises: the canvas showing `onto` in its middle, `copied`
    (the text, or the block load) copied, Edit > Paste: the copy is selected and wholly in view (moved off a block, it
    may have left the view: the canvas follows it), no pasted text is under a block (not even partly: it would be
    unreadable) and no pasted block is wholly over a text, where it would be hidden."""
    qtbot.waitExposed(win)
    tab = win.open_flow(report_flow(tmp_path / "report.yaml"))
    settle()  # (an opened flow centres itself just after)
    tab.scene.clearSelection()
    (tab.scene.note_items.get(copied) or tab.scene.nodes[copied]).setSelected(True)
    menu(win, "Copy").trigger()
    settle()
    original = copy.deepcopy(tab.doc.data)
    tab.view.zoom_to(zoom)
    tab.view.centerOn((tab.scene.note_items.get(onto) or tab.scene.nodes[onto]).sceneBoundingRect().center())
    settle()

    choose_from_edit_menu(win, qtbot, "Paste")

    blocks, notes = pasted(tab, original)
    assert len(blocks) + len(notes) == 1, (tab.doc.blocks, tab.doc.notes)
    assert_selected_and_shown(tab, blocks, notes)
    assert visible(tab).contains(group_rect(tab, blocks, notes)), (visible(tab), group_rect(tab, blocks, notes))
    for text in [tab.scene.note_items[n] for n in notes]:
        assert not any(block.sceneBoundingRect().intersects(text.sceneBoundingRect())
                       for block in tab.scene.nodes.values()), text.note_id
    for block in [tab.scene.nodes[b] for b in blocks]:
        assert not any(block.sceneBoundingRect().contains(text.sceneBoundingRect())
                       for text in tab.scene.note_items.values()), block.block_id
    clean(win)
