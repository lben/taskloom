"""Proof: pasted blocks and texts land where you are working: at the mouse pointer, at the spot right-clicked, or in
the middle of the canvas you see."""

import copy
from contextlib import contextmanager

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QContextMenuEvent, QCursor, QKeySequence, QPainter, QPen, QPolygonF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from proofs.conftest import choose, menu_item
from proofs.test_proof_canvas import click_block, click_text, in_the_middle, menu_of, open_flow, text_area
from tests.test_editor import write_flow
from tests.test_paste_at_cursor import layout, pasted, point_to, top_left

FLOW = """
    blocks:
      start_by_hand:
        type: trigger.manual
        ui: {x: 0, y: 0}
      load_sales:
        type: logic.python
        config: {inputs: [], code: "output = [120, 80, 45]"}
        ui: {x: 240, y: 0}
      sum_sales:
        type: logic.python
        config: {inputs: [input], code: "output = sum(input)"}
        ui: {x: 480, y: 0}
    edges:
      - start_by_hand.start -> load_sales.after
      - load_sales.output -> sum_sales.input
    notes:
      note_1: {text: "Sales report", x: 260, y: -110, style: heading, bold: true}
"""
TITLE = "Sales report"
COPIED = ["load_sales", "sum_sales"]  # copied with the title, note_1


class DrawnPointer(QWidget):
    """The mouse pointer, drawn where it is on `parent` (a screenshot does not show it): a black arrow, its tip on the
    point the pointer is at."""

    ARROW = [(0, 0), (0, 16), (4, 12.5), (7, 19), (9.5, 18), (6.5, 11.5), (11.5, 11.5)]  # the usual arrow, tip at 0, 0
    SCALE, EDGE = 1.5, 2  # its size; room for its white edge

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.resize(round(12 * self.SCALE) + 2 * self.EDGE, round(19 * self.SCALE) + 2 * self.EDGE)
        self.move(parent.mapFromGlobal(QCursor.pos()) - QPoint(self.EDGE, self.EDGE))
        self.show()
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("white"), 1.5))
        painter.setBrush(QColor("black"))
        painter.drawPolygon(QPolygonF([QPointF(self.EDGE + x * self.SCALE, self.EDGE + y * self.SCALE)
                                       for x, y in self.ARROW]))
        painter.end()


@contextmanager
def pointer_drawn(on: QWidget):
    """The pointer drawn where it is, on `on` (the window, or an open menu), for the screenshots taken meanwhile."""
    drawn = DrawnPointer(on)
    try:
        yield drawn
    finally:
        drawn.hide()
        drawn.deleteLater()


def settle():
    for _ in range(3):
        QApplication.processEvents()


def press(win, tab, keys):
    """Press a shortcut (Ctrl+C, Ctrl+V, Ctrl+Z; on a Mac, Cmd) while working on the canvas. The pointer stays put."""
    win.activateWindow()  # (a closed menu leaves no window active on the offscreen screen the proofs run on)
    assert QTest.qWaitForWindowActive(win, 5_000), "the window does not become active"
    tab.view.setFocus()
    QTest.keySequence(tab.view, QKeySequence(keys))
    settle()


def scroll_to(tab, x: int, y: int):
    """Scroll the canvas to these positions of its scroll bars, as dragging them does."""
    tab.view.horizontalScrollBar().setValue(x)
    tab.view.verticalScrollBar().setValue(y)
    settle()


def scrolled(tab) -> tuple[int, int]:
    return tab.view.horizontalScrollBar().value(), tab.view.verticalScrollBar().value()


def visible(tab) -> QRectF:
    """The part of the flow the canvas shows."""
    return tab.view.mapToScene(tab.view.viewport().rect()).boundingRect()


def everything(tab) -> QRectF:
    """The part of the flow its blocks and texts cover."""
    rect = QRectF()
    for item in [*tab.scene.nodes.values(), *tab.scene.note_items.values()]:
        rect |= item.sceneBoundingRect()
    return rect


def spot(tab, fx: float, fy: float) -> QPoint:
    """A spot of the visible canvas, at these fractions of its width and height."""
    viewport = tab.view.viewport()
    return QPoint(round(viewport.width() * fx), round(viewport.height() * fy))


def pointer(tab) -> QPoint:
    """Where the mouse pointer is, on the canvas."""
    return tab.view.viewport().mapFromGlobal(QCursor.pos())


def on_screen(tab, corner) -> QPoint:
    """Where a point of the flow (x, y) is drawn, on the canvas."""
    return tab.view.mapFromScene(QPointF(*corner))


def within_a_pixel(a: QPoint, b: QPoint) -> bool:
    return abs(a.x() - b.x()) <= 1 and abs(a.y() - b.y()) <= 1


def ring(win, widget, at: QPoint) -> QRect:
    """A small square around a spot of `widget`, in a screenshot of the window (to outline the spot)."""
    centre = widget.mapTo(win, at)
    return QRect(centre.x() - 8, centre.y() - 8, 16, 16)


def outline(win, tab, blocks, notes) -> list:
    """What to outline for these blocks and texts: one outline around them all."""
    return [*blocks, *(text_area(win, tab, n) for n in notes)]


def copies_of(tab, blocks) -> dict:
    """Which of these pasted blocks is the copy of load_sales, and which of sum_sales (by their code)."""
    code = {"load_sales": "output = [120, 80, 45]", "sum_sales": "output = sum(input)"}
    return {name: next(b for b in blocks if tab.doc.blocks[b]["config"]["code"] == code[name]) for name in COPIED}


def added_connections(tab, blocks) -> set:
    return {e for e in tab.doc.edges() if e[0] in blocks or e[2] in blocks}


def selected(tab, blocks, notes) -> bool:
    """These blocks and texts, and only they, are selected."""
    return (sorted(tab.scene.selected_block_ids()) == sorted(blocks)
            and sorted(tab.scene.selected_note_ids()) == sorted(notes))


def open_edit_menu(win, qtbot):
    """Point at Edit in the menu bar and click it: its menu, open (the menu bar's menus do not wait for a choice)."""
    bar, edit = win.menuBar(), menu_of(win, "&Edit")
    title = bar.actionGeometry(edit.menuAction()).center()
    point_to(bar, title)
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, title)
    qtbot.waitUntil(edit.isVisible, timeout=5_000)
    return edit


def right_click_at(canvas, at: QPoint):
    """Right-click the canvas at `at`, the pointer there (opens its menu, which waits for a choice: see
    Proof.while_open)."""
    point_to(canvas, at)
    QApplication.sendEvent(canvas, QContextMenuEvent(QContextMenuEvent.Mouse, at, canvas.mapToGlobal(at)))


def click_item(menu, qtbot, text: str):
    """Click the item of an open menu that the pointer is on."""
    QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(menu_item(menu, text)).center())
    qtbot.waitUntil(lambda: not menu.isVisible(), timeout=5_000)
    settle()


def test_pasted_blocks_and_texts_land_where_you_are_working(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Pasted blocks and texts land where you are working",
              "When pasting a block it should be pasted where I am currently focusing on, not on the original "
              "coordinates of the block")
    p.idea("Pasted blocks and texts now land where you are working: at the mouse pointer, at the spot you "
           "right-clicked, or in the middle of the canvas you see. They no longer land next to where they were "
           "copied from, which could be far out of view.")
    p.idea("Press Ctrl+V with the mouse pointer on the canvas, and the copies' top-left corner goes to the pointer. "
           "Right-click an empty spot of the canvas and choose Paste, new in that menu, and it goes to the spot you "
           "clicked. Choose Edit > Paste in the menu bar (or press Ctrl+V with the pointer off the canvas), and the "
           "copies go in the middle of the canvas you see.")
    p.idea("The copies keep their layout and the connections between them, and they are selected, so you can drag "
           "them on at once. Paste again without moving, and the new copies go 40 pixels right and down of the last "
           "ones, so none is hidden exactly under another. One Undo takes back the whole paste.")
    p.idea("A paste moves right and down from where you pointed only to keep something from being hidden: an exact "
           "repeat, a pasted text that would be behind a block (texts are drawn behind blocks), or a pasted block "
           "that would wholly cover a text. If moving off a block or a text takes the copies out of view, the canvas "
           "scrolls to show them. "
           "There is no snapping to a grid, and copying is unchanged.")
    p.why_simplest("Each paste picks one point: where the copies' top-left corner goes. The copies keep their layout "
                   "around it. That corner is the leftmost left edge and the highest top edge among the copied blocks "
                   "and texts, the corner each block and text is placed by in the flow file.")
    p.why_simplest("The point is where you are working, in this order: the spot you right-clicked; else the pointer, "
                   "when it is on the canvas; else the middle of the canvas you see. Every way to paste falls in one "
                   "of the three, and each is the place you are looking at when you paste.")
    p.why_simplest("The copies move only when something would be hidden, and then always the same way: 40 pixels "
                   "right and down at a time, until nothing is. So they land where you pointed, or as near it as "
                   "they can while staying in sight.")
    p.alternative("Keep the old place: 40 pixels right and down of the originals",
                  "That is what you had. After scrolling to another part of the flow, or in another flow's tab, the "
                  "copies landed where the originals were, out of view, and you had to go and find them.")
    p.alternative("Always at the pointer, also for Edit > Paste",
                  "To choose Edit > Paste, the pointer is on the menu, not on the canvas: the copies would land "
                  "wherever the menu happened to be, possibly out of view. While you choose from the menu bar, you "
                  "are looking at the canvas as a whole, so its middle is the place.")
    p.alternative("Snap the copies to a grid, or search for the nearest free space",
                  "Either would move the copies away from where you pointed, a search possibly far away, so they "
                  "would not be where you looked. Nothing on Taskloom's canvas snaps to a grid (the dots are only its "
                  "background), so pasted blocks would behave unlike dragged ones.")
    p.alternative("Never move a paste, even when something would be hidden",
                  "A second Ctrl+V would then put the copies exactly on top of the first ones, as if nothing had "
                  "happened. And a text pasted onto a block would be behind it, unreadable.")
    p.fits("Copy is unchanged: it still puts the selected blocks, the connections between them, and the selected "
           "texts on the clipboard, so copying into another flow works as before. Only where Paste puts the copies "
           "changed.")
    p.fits("The empty canvas's right-click menu already had “Add text here”, which puts a text at the spot clicked. "
           "Paste is next to it and uses the same spot.")
    p.fits("Ctrl+V pastes blocks only while you work on the canvas, as before: in the Log or a text field it pastes "
           "text there.")
    p.fits("The 40 pixels are the step the old paste took from the originals. A paste is one change to the flow, "
           "like any other: one Undo takes it all back.")
    p.fits("The layout is in the flow's own pixels, whatever the zoom: at 50% the copies are drawn half as big, as "
           "everything is, and laid out just as the originals.")
    flow = write_flow(tmp_path / "sales-report.yaml", FLOW)
    QApplication.clipboard().setText("")

    # --- 1. copy two connected blocks and the text ------------------------------------------------------------
    p.step(f"Open the flow sales-report.yaml (File > Open…). start_by_hand starts load_sales, whose output goes into "
           f"sum_sales; the text “{TITLE}” is their title.")
    tab = open_flow(win, monkeypatch, flow)
    QTest.qWait(50)  # (an opened flow centres itself just after)
    home = scrolled(tab)
    p.check("The flow opens, with its three blocks and its title",
            list(tab.doc.blocks) == ["start_by_hand", *COPIED]
            and list(tab.doc.notes) == ["note_1"])
    p.shot(win, f"① The title “{TITLE}” above ② load_sales and sum_sales, connected; start_by_hand starts them.",
           highlight=[text_area(win, tab, "note_1"), COPIED], badges=True,
           zoom=[text_area(win, tab, "note_1"), "start_by_hand", *COPIED])

    p.step("Click the title, hold Ctrl and click load_sales and sum_sales, then press Ctrl+C.")
    click_text(tab, "note_1")
    for block in COPIED:
        click_block(tab, block, Qt.ControlModifier)
    p.check("The title and the two blocks are selected together", selected(tab, COPIED, ["note_1"]))
    press(win, tab, QKeySequence.Copy)
    original = {"blocks": copy.deepcopy(tab.doc.blocks), "notes": copy.deepcopy(tab.doc.notes),
                "layout": layout(tab, COPIED, ["note_1"])}
    clipboard = QApplication.clipboard().text()
    p.check("Ctrl+C copied them, as before: the clipboard holds the two blocks and the title",
            all(f"{name}:" in clipboard for name in COPIED) and TITLE in clipboard)
    p.shot(win, "① The title and the two blocks, selected (the blocks' frames are coloured, the title has a dashed "
                "frame), are copied.",
           highlight=[outline(win, tab, COPIED, ["note_1"])], badges=True, zoom=[outline(win, tab, COPIED, ["note_1"])])

    # --- 2. Ctrl+V at the pointer ------------------------------------------------------------------------------
    p.step("Scroll the canvas right and down to an empty part, far from the flow, point at an empty spot and press "
           "Ctrl+V.")
    p.note("Screenshots do not show the mouse pointer, so the proof draws it where it is: a black arrow, its tip on "
           "the point it points at. The proof scrolls by setting the canvas's scroll bars, as dragging them does.")
    scroll_to(tab, home[0] + 1400, home[1] + 900)
    p.check("The canvas shows an empty part: none of the flow's blocks and texts is in view",
            not visible(tab).intersects(everything(tab)))
    point_to(tab.view.viewport(), spot(tab, 0.2, 0.25))
    before = {"blocks": dict(tab.doc.blocks), "notes": dict(tab.doc.notes)}
    press(win, tab, QKeySequence.Paste)
    first_blocks, first_notes = pasted(tab, before)
    copy_of = copies_of(tab, first_blocks)
    with pointer_drawn(win) as drawn:
        p.shot(win, "① The copies of the title and of the two blocks, with their connection, selected. Their top-left "
                    f"corner is at the pointer's tip: as far left as {copy_of['load_sales']}, as high as the title.",
               highlight=[outline(win, tab, first_blocks, first_notes)], badges=True,
               zoom=[outline(win, tab, first_blocks, first_notes), drawn])
    p.check("Ctrl+V added copies of the two blocks and of the title: "
            f"{', '.join(first_blocks)} and a new “{TITLE}”",
            len(first_blocks) == 2 and len(first_notes) == 1
            and tab.doc.notes[first_notes[0]]["text"] == TITLE)
    first = top_left(tab, first_blocks, first_notes)
    p.check("① The copies' top-left corner is at the pointer, within a pixel",
            within_a_pixel(on_screen(tab, first), pointer(tab)))
    p.check("The copies keep their layout: each is where its original is, from the group's top-left corner",
            layout(tab, first_blocks, first_notes) == original["layout"])
    p.check(f"{copy_of['load_sales']}'s output goes into {copy_of['sum_sales']}'s input, as between the originals; "
            "nothing connects the copies to start_by_hand",
            added_connections(tab, first_blocks) == {(copy_of["load_sales"], "output", copy_of["sum_sales"], "input")})
    p.check("The copies, and only they, are selected", selected(tab, first_blocks, first_notes))
    p.check("The originals are unchanged",
            all(tab.doc.blocks[b] == spec for b, spec in original["blocks"].items())
            and tab.doc.notes["note_1"] == original["notes"]["note_1"])

    # --- 3. Ctrl+V again --------------------------------------------------------------------------------------
    p.step("Press Ctrl+V again, without moving the pointer.")
    before = {"blocks": dict(tab.doc.blocks), "notes": dict(tab.doc.notes)}
    press(win, tab, QKeySequence.Paste)
    second_blocks, second_notes = pasted(tab, before)
    with pointer_drawn(win) as drawn:
        p.shot(win, "② The new copies are 40 pixels right and down of ① the first ones (their titles are outlined): "
                    "none hides exactly under another. The new copies are the ones selected.",
               highlight=[text_area(win, tab, first_notes[0]), text_area(win, tab, second_notes[0])], badges=True,
               zoom=[outline(win, tab, first_blocks + second_blocks, first_notes + second_notes), drawn])
    second = top_left(tab, second_blocks, second_notes)
    p.check("Ctrl+V added copies of the two blocks and of the title again, in their layout",
            len(second_blocks) == 2 and len(second_notes) == 1
            and layout(tab, second_blocks, second_notes) == original["layout"])
    p.check(f"② Their top-left corner is 40 pixels right and 40 down of ①'s: at {second}, after {first}",
            second == (first[0] + 40, first[1] + 40))
    places = [(b["ui"]["x"], b["ui"]["y"]) for b in tab.doc.blocks.values()] + \
        [(n["x"], n["y"]) for n in tab.doc.notes.values()]
    p.check("No block or text is exactly where another one is", len(set(places)) == len(places))
    p.check("② The new copies are the ones selected", selected(tab, second_blocks, second_notes))

    # --- 4. Edit > Paste: the middle of the canvas you see --------------------------------------------------------
    p.step("Scroll further down, to another empty part, and choose Edit > Paste in the menu bar.")
    scroll_to(tab, home[0] + 1400, home[1] + 1700)
    p.check("The canvas shows an empty part again: no block or text, not even the copies, is in view",
            not visible(tab).intersects(everything(tab)))
    before = {"blocks": dict(tab.doc.blocks), "notes": dict(tab.doc.notes)}
    edit = open_edit_menu(win, qtbot)
    point_to(edit, edit.actionGeometry(menu_item(edit, "Paste")).center())
    p.check("The pointer is on the Edit menu's Paste, not on the canvas",
            QApplication.widgetAt(QCursor.pos()) is edit)
    with pointer_drawn(edit) as drawn:
        p.shot(edit, "① Paste, in the Edit menu, with the pointer on it.", over=win,
               highlight=[menu_item(edit, "Paste")], badges=True, zoom=[edit])
    click_item(edit, qtbot, "Paste")
    middle_blocks, middle_notes = pasted(tab, before)
    canvas = tab.view.viewport()
    under = QApplication.widgetAt(QCursor.pos())
    with pointer_drawn(win):
        p.shot(win, "① The copies, in the middle of the canvas you see: ② marks its middle. They are selected. The "
                    "pointer is still where Paste was in the menu, over the Blocks list.",
               highlight=[outline(win, tab, middle_blocks, middle_notes), ring(win, canvas, canvas.rect().center())],
               badges=True, zoom=[outline(win, tab, middle_blocks, middle_notes)])
    p.check("Edit > Paste added copies of the two blocks and of the title, in their layout",
            len(middle_blocks) == 2 and len(middle_notes) == 1
            and layout(tab, middle_blocks, middle_notes) == original["layout"])
    p.check("① They are in the middle of the canvas you see: the middle of the copies is ② the canvas's middle, "
            "within 2 pixels", in_the_middle(tab, middle_blocks, middle_notes))
    p.check("They are selected", selected(tab, middle_blocks, middle_notes))
    p.check("The pointer, where Paste was, is over the Blocks list, not on the canvas",
            under is not None and win.palette_panel.isAncestorOf(under))

    # --- 4 (cont.). the case the rule is for: a text that would be behind a block ---------------------------------
    p.step("Scroll back to the flow, so that its blocks are in the middle of the canvas, click the title alone and "
           "press Ctrl+C.")
    scroll_to(tab, *home)
    click_text(tab, "note_1")
    p.check("The title alone is selected", selected(tab, [], ["note_1"]))
    press(win, tab, QKeySequence.Copy)
    middle = tab.view.mapToScene(canvas.rect().center())
    title = tab.scene.note_items["note_1"].sceneBoundingRect()
    # Where the copy's top-left corner goes for its middle to be the canvas's middle, before anything moves it.
    in_middle = QPointF(round(middle.x() - title.width() / 2), round(middle.y() - title.height() / 2))
    behind = [b for b, node in tab.scene.nodes.items()
              if node.sceneBoundingRect().intersects(title.translated(in_middle - title.topLeft()))]
    p.shot(win, "② The middle of the canvas is on ① load_sales: a copy of the title put in the middle would be behind "
                "it, since texts are drawn behind blocks.",
           highlight=["load_sales", ring(win, canvas, canvas.rect().center())], badges=True,
           zoom=[text_area(win, tab, "note_1"), "start_by_hand", *COPIED])
    p.check(f"A copy of the title in the middle of the canvas would be behind {' and '.join(behind)}",
            "load_sales" in behind)

    p.step("Choose Edit > Paste in the menu bar.")
    before = {"blocks": dict(tab.doc.blocks), "notes": dict(tab.doc.notes)}
    edit = open_edit_menu(win, qtbot)
    point_to(edit, edit.actionGeometry(menu_item(edit, "Paste")).center())
    click_item(edit, qtbot, "Paste")
    blocks, (clear_note,) = pasted(tab, before)
    moved = QPointF(tab.doc.notes[clear_note]["x"], tab.doc.notes[clear_note]["y"]) - in_middle
    steps = round(moved.x() / 40)
    copy_rect = tab.scene.note_items[clear_note].sceneBoundingRect()
    p.shot(win, f"① The copy of the title went right and down from the middle, {steps} times 40 pixels, until no block "
                "covers it: it is readable, below the blocks. It is the one selected.",
           highlight=[text_area(win, tab, clear_note)], badges=True,
           zoom=[text_area(win, tab, clear_note), text_area(win, tab, "note_1"), *COPIED])
    p.check("Edit > Paste added one copy of the title, and no block", blocks == []
            and tab.doc.notes[clear_note]["text"] == TITLE)
    p.check(f"It went right and down from the middle in steps of 40 pixels ({steps} of them), as far as needed",
            steps >= 1 and moved == QPointF(40 * steps, 40 * steps)
            and any(node.sceneBoundingRect().intersects(copy_rect.translated(-40, -40))
                    for node in tab.scene.nodes.values()))
    p.check("① No block covers it: it is clear of every block",
            not any(node.sceneBoundingRect().intersects(copy_rect) for node in tab.scene.nodes.values()))
    p.check("It is in view, and selected",
            canvas.rect().contains(tab.view.mapFromScene(copy_rect).boundingRect())
            and selected(tab, [], [clear_note]))

    # --- 5. right-click an empty spot > Paste --------------------------------------------------------------------
    p.step("Right-click an empty spot of the canvas, under start_by_hand, and choose Paste.")
    clicked = on_screen(tab, (0, 240))  # under start_by_hand, left of the copy just pasted
    assert canvas.rect().contains(clicked) and not tab.scene.items(tab.view.mapToScene(clicked)), "not an empty spot"
    before = {"blocks": dict(tab.doc.blocks), "notes": dict(tab.doc.notes)}
    items = []

    def paste_here(menu):
        items.extend(a.text() for a in menu.actions() if a.text())
        point_to(menu, menu.actionGeometry(menu_item(menu, "Paste")).center())
        with pointer_drawn(menu):
            p.shot(menu, "The canvas's menu opens where you right-clicked: ① Paste, under “Add text here”, with the "
                         "pointer on it.", over=win, highlight=[menu_item(menu, "Paste")], badges=True,
                   zoom=[menu])
        choose(menu, "Paste")

    p.while_open(lambda: right_click_at(canvas, clicked), paste_here)
    settle()
    _, (spot_note,) = pasted(tab, before)
    corner = top_left(tab, [], [spot_note])
    with pointer_drawn(win):
        p.shot(win, "② The copy of the title has its top-left corner at ① the spot you right-clicked, not where the "
                    "pointer went to choose Paste.",
               highlight=[ring(win, canvas, clicked), text_area(win, tab, spot_note)], badges=True,
               zoom=[ring(win, canvas, clicked), text_area(win, tab, spot_note), "start_by_hand"])
    p.check("The empty canvas's menu has Paste, next to “Add text here”", "Paste" in items and "Add text here" in items)
    p.check("② The copy's top-left corner is at ① the spot right-clicked, within a pixel",
            within_a_pixel(on_screen(tab, corner), clicked))
    p.check("When you chose Paste, the pointer was on the menu, away from that spot", (pointer(tab) - clicked).manhattanLength() > 20)

    # --- 6. another flow's tab, at 50% --------------------------------------------------------------------------
    p.step("Copy the title and the two blocks again (click the title, Ctrl+click load_sales and sum_sales, Ctrl+C), "
           "press + next to the tabs for a new flow, and set its zoom to 50%: click the percentage, type 50, press "
           "Enter.")
    click_text(tab, "note_1")
    for block in COPIED:
        click_block(tab, block, Qt.ControlModifier)
    p.check("The title and the two blocks are selected together again", selected(tab, COPIED, ["note_1"]))
    press(win, tab, QKeySequence.Copy)
    plus = win.tabs.tabBar().new_button
    point_to(plus, plus.rect().center())
    QTest.mouseClick(plus, Qt.LeftButton)
    QTest.qWait(50)  # (a new flow centres itself just after)
    other = win.current
    bar = other.view.zoom_bar
    QTest.mouseClick(bar.percent, Qt.LeftButton)
    QTest.keyClicks(bar.percent, "50")
    QTest.keyClick(bar.percent, Qt.Key_Return)
    settle()
    tab_bar = win.tabs.tabBar()
    new_tab = tab_bar.tabRect(win.tabs.indexOf(other)).translated(tab_bar.mapTo(win, QPoint(0, 0)))
    p.shot(win, "① The new flow, “untitled”, in its own tab; ② its zoom: 50%.",
           highlight=[new_tab, bar], badges=True, zoom=[new_tab, bar])
    p.check("A new flow opens in its own tab, with its start block, at 50%",
            other is not tab and list(other.doc.blocks) == ["start"] and round(other.view.zoom_percent()) == 50
            and bar.percent.text() == "50%")

    p.step("Point at an empty spot of the new flow's canvas and press Ctrl+V.")
    point_to(other.view.viewport(), spot(other, 0.6, 0.2))
    before = {"blocks": dict(other.doc.blocks), "notes": dict(other.doc.notes), "edges": list(other.doc.data["edges"])}
    press(win, other, QKeySequence.Paste)
    other_blocks, other_notes = pasted(other, before)
    with pointer_drawn(win) as drawn:
        p.shot(win, "In the new flow, at 50%: ① the copies of the title and the two blocks, connected and selected, "
                    "their top-left corner at the pointer's tip.",
               highlight=[outline(win, other, other_blocks, other_notes)], badges=True,
               zoom=[outline(win, other, other_blocks, other_notes), drawn, "start"])
    p.check("Ctrl+V added copies of the two blocks and of the title", len(other_blocks) == 2 and len(other_notes) == 1)
    p.check("① Their top-left corner is at the pointer, within a pixel, at 50% too",
            within_a_pixel(on_screen(other, top_left(other, other_blocks, other_notes)), pointer(other)))
    p.check("They keep their layout, in the flow's own pixels", layout(other, other_blocks, other_notes)
            == original["layout"])
    other_copy = copies_of(other, other_blocks)
    p.check("They are connected as the originals, and selected",
            added_connections(other, other_blocks)
            == {(other_copy["load_sales"], "output", other_copy["sum_sales"], "input")}
            and selected(other, other_blocks, other_notes))

    # --- 7. one Undo removes the whole paste ------------------------------------------------------------------
    p.step("Press Ctrl+Z (Edit > Undo) once.")
    press(win, other, QKeySequence.Undo)
    p.shot(win, "One Undo took back the whole paste: the two blocks, their connection and the title are gone. The new "
                "flow has only its start block again.",
           highlight=["start"], zoom=["start"])
    p.check("The two copied blocks, their connection and the copied title are all gone, after one Undo",
            dict(other.doc.blocks) == before["blocks"] and dict(other.doc.notes) == before["notes"]
            and list(other.doc.data["edges"]) == before["edges"]
            and set(other.scene.nodes) == {"start"} and not other.scene.note_items)
    for each in win.tabs_list():  # nothing to save when the window closes
        each.doc.dirty = False
