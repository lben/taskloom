"""Proofs: three canvas fixes. Texts are copied and pasted like blocks; a block added after a run runs on that run's
results; the zoom can be typed, with bolder + and - and the step box's arrows stacked."""

import copy

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QLineEdit, QStyle, QStyleFactory,
                               QStyleOptionButton, QStyleOptionSpinBox, QToolBar)

from proofs.conftest import choose, menu_item, right_click
from taskloom.editor.canvas import HEADER_H, NODE_W
from taskloom.editor.panels import CodeEdit
from tests.test_editor import write_flow

FLOW = """
    blocks:
      start_by_hand:
        type: trigger.manual
        ui: {x: 0, y: 0}
      read_sales:
        type: logic.python
        config: {inputs: [], code: "output = [120, 80, 45]"}
        ui: {x: 240, y: 0}
      total_sales:
        type: logic.python
        config: {inputs: [input], code: "output = sum(input)"}
        ui: {x: 480, y: 0}
    edges:
      - start_by_hand.start -> read_sales.after
      - read_sales.output -> total_sales.input
    notes:
      note_1: {text: "Daily sales report", x: 0, y: -110, style: heading, bold: true}
"""
TITLE = "Daily sales report"


def menu_of(win, title: str):
    """A menu of the menu bar (e.g. "&Edit")."""
    return next(a.menu() for a in win.menuBar().actions() if a.text() == title)


def open_flow(win, monkeypatch, path):
    """File > Open…, choosing `path` in the file dialog: the tab it opens in."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    menu_item(menu_of(win, "&File"), "Open…").trigger()
    QApplication.processEvents()
    return win.current


def from_edit_menu(p, win, item: str, look=None):
    """Open the Edit menu under its name in the menu bar and choose `item`; look(menu) first, to shoot it."""
    edit = menu_of(win, "&Edit")
    bar = win.menuBar()
    at = bar.mapToGlobal(bar.actionGeometry(edit.menuAction()).bottomLeft())

    def then(menu):
        if look is not None:
            look(menu)
        choose(menu, item)

    p.while_open(lambda: edit.exec(at), then)
    QApplication.processEvents()


def on_screen(tab, scene_point: QPointF) -> QPoint:
    return tab.view.mapFromScene(scene_point)


def click_block(tab, block: str, modifier=Qt.NoModifier):
    """Click a block's title bar, as you would (with Ctrl: add it to what is selected)."""
    at = tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, modifier, on_screen(tab, at))
    QApplication.processEvents()


def click_text(tab, note_id: str):
    """Click a text on the canvas, as you would."""
    item = tab.scene.note_items[note_id]
    at = item.mapToScene(QPointF(item.font().pointSizeF(), item.boundingRect().height() / 2))  # on its first word
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, on_screen(tab, at))
    QApplication.processEvents()


def drag_block(tab, block: str, to: QPointF):
    """Drag a block by its title bar so that its top-left corner is at `to`, on the canvas."""
    grab = QPointF(NODE_W / 2, HEADER_H / 2)
    a = on_screen(tab, tab.scene.nodes[block].mapToScene(grab))
    b = on_screen(tab, to + grab)
    viewport = tab.view.viewport()
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, a)
    for i in range(1, 6):
        QTest.mouseMove(viewport, a + (b - a) * i / 5)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, b)
    QApplication.processEvents()


def drag_port_to_port(tab, src: str, src_port: str, dst: str, dst_port: str):
    """Drag from an output (a block's right side) to an input (another's left side): a connection."""
    viewport = tab.view.viewport()
    a = on_screen(tab, tab.scene.nodes[src].ports[(src_port, True)].scenePos())
    b = on_screen(tab, tab.scene.nodes[dst].ports[(dst_port, False)].scenePos())
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(viewport, (a + b) / 2)
    QTest.mouseMove(viewport, b)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, b)
    QApplication.processEvents()


def bring_into_view(tab, blocks=(), notes=()):
    """Drag the canvas with the middle mouse button until these blocks and texts are in the middle of it."""
    rect = QRectF()
    for item in [tab.scene.nodes[b] for b in blocks] + [tab.scene.note_items[n] for n in notes]:
        rect |= item.sceneBoundingRect()
    viewport = tab.view.viewport()
    a, b = on_screen(tab, rect.center()), viewport.rect().center()
    QTest.mousePress(viewport, Qt.MiddleButton, Qt.NoModifier, a)
    for i in range(1, 6):
        QTest.mouseMove(viewport, a + (b - a) * i / 5)
    QTest.mouseRelease(viewport, Qt.MiddleButton, Qt.NoModifier, b)
    QApplication.processEvents()


def text_area(win, tab, note_id: str) -> QRect:
    """Where a text on the canvas is, in a screenshot of the window (to outline it)."""
    view = tab.view
    rect = view.mapFromScene(tab.scene.note_items[note_id].sceneBoundingRect()).boundingRect()
    return rect.translated(view.viewport().mapTo(win, QPoint(0, 0)))


def looks(note: dict) -> dict:
    """A text's words and look: all of it but where it is."""
    return {k: v for k, v in note.items() if k not in ("x", "y")}


def shows(tab, block) -> tuple:
    """What the canvas shows of a block: its status, the run its result is from, what its output held."""
    return (tab.scene.nodes[block].status, (tab.block_info.get(block) or {}).get("run_id"),
            tab.scene.values.get(block, {}).get("output", ""))


def wait_runs(qtbot, tab, n: int) -> int:
    """Wait until the tab's n-th run has finished: its number."""
    qtbot.waitUntil(lambda: len(tab.runs) == n and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)
    return tab.runs[-1].run_id


def width_on_screen(tab, block: str) -> float:
    return tab.view.mapFromScene(tab.scene.nodes[block].mapToScene(QPointF(NODE_W, 0))).x() - \
        tab.view.mapFromScene(tab.scene.nodes[block].mapToScene(QPointF(0, 0))).x()


def arrows(spin) -> tuple[QRect, QRect, QRect]:
    """Where a spin box draws its up arrow, its down arrow and its number, in the box."""
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    return tuple(spin.style().subControlRect(QStyle.CC_SpinBox, option, part, spin)
                 for part in (QStyle.SC_SpinBoxUp, QStyle.SC_SpinBoxDown, QStyle.SC_SpinBoxEditField))


def stacked(up: QRect, down: QRect, number: QRect) -> bool:
    """The up arrow just above the down arrow, in one column, right of the number."""
    return (not up.isEmpty() and not down.isEmpty() and up.bottom() <= down.top() + 1
            and abs(up.left() - down.left()) <= 1 and min(up.left(), down.left()) >= number.right() - 1)


# --- 1. texts are copied and pasted ------------------------------------------------------------------------------

def test_texts_are_copied_and_pasted_like_blocks(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Texts are copied and pasted, like blocks",
              "I cannot copy/paste Text blocks, copy/pasting should be available for all blocks")
    p.idea("Texts on the canvas can now be copied and pasted like blocks: on their own, or together with blocks, in "
           "the same flow or into another one.")
    p.idea("Select a text (or texts and blocks together) and choose Edit > Copy, or press Ctrl+C. Edit > Paste, or "
           "Ctrl+V, adds a copy of each, a little to the right of and below the original: the same words in the same "
           "look (style, size, font, bold, italic, underline, where its lines wrap).")
    p.idea("The copies are selected, so you can drag them straight to where you want them, and one Undo takes back "
           "the whole paste.")
    p.why_simplest("Copy already put the selected blocks, with the connections between them, on the clipboard; Paste "
                   "read them back and added copies of them. Texts are now one more part of what Copy puts there, and "
                   "Paste adds their copies in the same step as the blocks'.")
    p.why_simplest("So there is still one Copy and one Paste, for whatever you selected: nothing new to learn, and "
                   "nothing that works for blocks but not for texts.")
    p.alternative("A separate Copy text command",
                  "Two commands to choose between, and a title could not be copied together with the blocks it "
                  "describes. You asked for copy and paste to work for everything on the canvas.")
    p.alternative("Duplicate: a copy made in place, without the clipboard",
                  "It could not take a text into another flow, as copy and paste does through the clipboard. And "
                  "Taskloom has no Duplicate for blocks, so texts would work differently from blocks.")
    p.alternative("Copying only a text's words, as plain text",
                  "Pasting would bring the words back but not their look: the style, size and font would have to be "
                  "set again on every copy.")
    p.fits("Copy and Paste stay where they were, in the Edit menu, with the same shortcuts. They act on the canvas: "
           "in the Log or in a text field, Ctrl+C and Ctrl+V still copy and paste text there.")
    p.fits("A pasted text lands where a pasted block does, a little to the right and below, and texts and blocks "
           "pasted together keep their places next to each other. Like pasted blocks, the copies are selected, and "
           "one Undo removes the whole paste.")
    p.fits("Each copy is a new text: pasting into a flow that already has texts never replaces one of them.")
    flow = write_flow(tmp_path / "daily-sales.yaml", FLOW)
    QApplication.clipboard().setText("")

    p.step(f"Open the flow daily-sales.yaml (File > Open…). It has a title, the text “{TITLE}”, above its three "
           "blocks.")
    tab = open_flow(win, monkeypatch, flow)
    original = dict(tab.doc.notes["note_1"])
    p.check("The flow opens with one text", tab is not None and list(tab.doc.notes) == ["note_1"])
    p.shot(win, f"① The text “{TITLE}”, above the flow's blocks.", highlight=[text_area(win, tab, "note_1")],
           badges=True, zoom=[text_area(win, tab, "note_1"), "total_sales"])

    p.step("Click the text to select it, then choose Edit > Copy.")
    click_text(tab, "note_1")
    p.check("The text is selected", tab.scene.selected_note_ids() == ["note_1"])

    def copy_it(menu):
        p.shot(menu, "① Edit > Copy, with ② the text selected: a dashed frame around it, with its two handles.",
               over=win,
               highlight=[menu_item(menu, "Copy"), text_area(win, tab, "note_1")], badges=True,
               zoom=[menu, text_area(win, tab, "note_1")])

    from_edit_menu(p, win, "Copy", copy_it)

    p.step("Choose Edit > Paste.")
    from_edit_menu(p, win, "Paste")
    pasted = [n for n in tab.doc.notes if n != "note_1"]
    p.shot(win, "① The copy, a little to the right of and below the original. The copy is the one selected (its "
                "dashed frame), ready to be dragged where you want it; the original is as it was.",
           highlight=[text_area(win, tab, n) for n in pasted], badges=True,
           zoom=[text_area(win, tab, n) for n in pasted + ["note_1"]] + ["read_sales"])
    p.check("Paste added one text", len(pasted) == 1)
    the_copy = tab.doc.notes[pasted[0]]
    p.check(f"The copy has the same words, “{TITLE}”, in the same look: heading style, bold",
            looks(the_copy) == looks(original) and tab.scene.note_items[pasted[0]].toPlainText() == TITLE
            and tab.scene.note_items[pasted[0]].font().bold())
    p.check("The copy is a little to the right of and below the original",
            the_copy["x"] > original["x"] and the_copy["y"] > original["y"])
    p.check("① The copy is selected, the original is not", tab.scene.selected_note_ids() == pasted)
    p.check("The original is unchanged", tab.doc.notes["note_1"] == original)

    p.step("Click the original text, then hold Ctrl and click read_sales and total_sales: the three are selected "
           "together. Choose Edit > Copy.")
    click_text(tab, "note_1")
    click_block(tab, "read_sales", Qt.ControlModifier)
    click_block(tab, "total_sales", Qt.ControlModifier)
    p.check("The text and the two blocks are selected together",
            tab.scene.selected_note_ids() == ["note_1"]
            and sorted(tab.scene.selected_block_ids()) == ["read_sales", "total_sales"])
    from_edit_menu(p, win, "Copy")
    source = copy.deepcopy(tab.doc.data)
    p.shot(win, "① The text and ② the two blocks, selected together, are copied.",
           highlight=[text_area(win, tab, "note_1"), ["read_sales", "total_sales"]], badges=True,
           zoom=[text_area(win, tab, "note_1"), "read_sales", "total_sales"])

    p.step("Press + next to the tabs to start a new flow, and choose Edit > Paste there. Paste puts the copies where "
           "the originals were in the first flow, a little to the right and below: here that is past the right edge "
           "of the canvas, so drag the canvas with the middle mouse button to bring them into view.")
    QTest.mouseClick(win.tabs.tabBar().new_button, Qt.LeftButton)
    QApplication.processEvents()
    target = win.current
    p.check("A new flow opens, in its own tab", target is not tab and list(target.doc.blocks) == ["start"])
    from_edit_menu(p, win, "Paste")
    bring_into_view(target, ["read_sales", "total_sales"], list(target.doc.notes))
    canvas = target.view.viewport().rect()
    p.check("Dragging the canvas brought the pasted text and blocks into view",
            all(canvas.contains(target.view.mapFromScene(item.sceneBoundingRect()).boundingRect())
                for item in [target.scene.nodes["read_sales"], target.scene.nodes["total_sales"],
                             *target.scene.note_items.values()]))
    p.check("The new flow now has the text and the two blocks, with the connection between them",
            [n["text"] for n in target.doc.notes.values()] == [TITLE]
            and set(target.doc.blocks) == {"start", "read_sales", "total_sales"}
            and ("read_sales", "output", "total_sales", "input") in target.doc.edges())
    (note,) = target.doc.notes
    p.check("The text has the same words and look there", looks(target.doc.notes[note]) == looks(original))
    p.check("Text and blocks keep their places next to each other: both moved by the same amount",
            (target.doc.notes[note]["x"] - original["x"], target.doc.notes[note]["y"] - original["y"])
            == (target.doc.blocks["read_sales"]["ui"]["x"] - 240, target.doc.blocks["read_sales"]["ui"]["y"]))
    p.check("What was pasted is selected", target.scene.selected_note_ids() == [note]
            and sorted(target.scene.selected_block_ids()) == ["read_sales", "total_sales"])
    p.check("The flow they came from is unchanged", tab.doc.data == source)
    p.shot(win, "In the new flow (its tab is “untitled”): ① the text and ② the two blocks, with their connection, "
                "pasted and selected. The blocks say “off · not connected to a start block” until you connect them to "
                "this flow's start block, as any block does. The flow they came from is unchanged, in its own tab.",
           highlight=[text_area(win, target, note), ["read_sales", "total_sales"]], badges=True,
           zoom=[text_area(win, target, note), "read_sales", "total_sales"])
    for each in win.tabs_list():  # nothing to save when the window closes
        each.doc.dirty = False


# --- 2. a block added after a run runs on its results ----------------------------------------------------------

def test_a_block_added_after_a_run_runs_on_that_runs_results(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("A block added after a run can use that run's results",
              "If I add a new block after a flow ran and I want to make that block to take advantage of the results "
              "of the already run flow it doesn't let me ... I should be able to add a new block and make use of the "
              "existing results of the latest run")
    p.idea("A block you add after a run can now run on that run's results, without running the flow again.")
    p.idea("Each block shows the result of the last run it took part in, and its menu's “Run only this block, with "
           "the same inputs” runs it again on what reached it in that run. A block added since has no result of its "
           "own, so that item used to be greyed out. Now, when a block shows no result, the item uses the latest run "
           "among the results that the blocks connected into it show, and says which run that is.")
    p.idea("The new block runs on what those blocks hold in that run. They are not run again: a slow query before it "
           "is not run a second time.")
    p.why_simplest("Running one block on an earlier run's results was already there, and Taskloom could already work "
                   "out what a block gets from what the blocks before it hold in a run, also for a block that took no "
                   "part in that run. Only the menu stopped you: it looked at the block's own result alone. Now, "
                   "when there is none, it looks at the blocks connected into it.")
    p.why_simplest("“Run only this block, with inputs I choose…” already filled in its values that way, from the "
                   "latest run among the blocks connected into it. The “with the same inputs” items now use that "
                   "same rule: one rule for the whole menu.")
    p.alternative("Run the whole flow again",
                  "That is what you had to do before: it runs every block again, a slow query too, and anything the "
                  "flow sends is sent again, just to try one new block.")
    p.alternative("Always the flow's very latest run",
                  "The latest run may be another line's, one that never touched the blocks connected into the new "
                  "block: they would have nothing to give it. Taking the run from those blocks means the new block "
                  "gets exactly what you see on them.")
    p.alternative("Ask which run to use, each time",
                  "One more question every time, when almost always you want the results you see. To use an older "
                  "run, pick it in Run history: the canvas then shows that run, and the menu takes the inputs from "
                  "it.")
    p.fits("The run the menu names comes from what the canvas shows: the latest of the runs whose results the blocks "
           "connected into the new block show. So what you see on them is what it gets.")
    p.fits("Once it has run, the new block shows its own result, like any block, and from then on its menu uses that "
           "result, as for every other block. Run history lists the run as “only” that block, as for any block run "
           "on its own.")
    p.fits("“Run from this block on, with the same inputs” follows the same rule: the new block and the blocks after "
           "it run, the blocks before it do not.")
    flow = write_flow(tmp_path / "daily-sales.yaml", FLOW)

    p.step("Open the flow daily-sales.yaml and press ▶ Run now. (File > Open…, then ▶ Run now in the toolbar.)")
    tab = open_flow(win, monkeypatch, flow)
    run_now = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action)
    QTest.mouseClick(run_now, Qt.LeftButton)
    first = wait_runs(qtbot, tab, 1)
    line = ["start_by_hand", "read_sales", "total_sales"]
    p.shot(win, f"① Run #{first} ran the flow: its blocks are green and show what they made. read_sales made "
                "[120, 80, 45], total_sales 245.",
           highlight=[line], badges=True, zoom=[line])
    p.check(f"Run #{first} succeeded: the three blocks show success",
            tab.runs[0].status == "success" and all(shows(tab, b)[:2] == ("success", first) for b in line))

    p.step("Add a new block, biggest_sale, fed by read_sales. Double-click Python Code in the Blocks list and drag "
           "the new block under total_sales. In Properties, name it biggest_sale, write its code, "
           "output = max(input), and untick Run in my Python environment (there is none here, so it runs in "
           "Taskloom's own Python). Then drag from read_sales's output to its input.")
    tree = win.palette_panel.tree
    tree.itemDoubleClicked.emit(tree.findItems("Python Code", Qt.MatchExactly | Qt.MatchRecursive)[0], 0)
    QApplication.processEvents()
    (added,) = set(tab.doc.blocks) - set(line)
    drag_block(tab, added, QPointF(480, 190))
    name = next(f for f in win.properties.findChildren(QLineEdit) if f.text() == added)
    name.setFocus()
    name.selectAll()
    QTest.keyClicks(name, "biggest_sale")
    QTest.keyClick(name, Qt.Key_Return)
    QApplication.processEvents()
    code = win.properties.findChild(CodeEdit)
    code.setFocus()
    code.selectAll()
    QTest.keyClicks(code, "output = max(input)")
    code.editing_finished.emit()  # as when you click elsewhere
    QApplication.processEvents()
    box = next(c for c in win.properties.findChildren(QCheckBox) if "own Python environment" in c.toolTip())
    option = QStyleOptionButton()
    box.initStyleOption(option)
    QTest.mouseClick(box, Qt.LeftButton, Qt.NoModifier,
                     box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box).center())
    QApplication.processEvents()
    drag_port_to_port(tab, "read_sales", "output", "biggest_sale", "input")
    p.check("biggest_sale is in the flow, connected from read_sales's output, and runs in Taskloom's own Python",
            ("read_sales", "output", "biggest_sale", "input") in tab.doc.edges()
            and tab.doc.blocks["biggest_sale"]["config"].get("code") == "output = max(input)"
            and not tab.doc.blocks["biggest_sale"]["config"].get("use_environment"))
    p.check("biggest_sale shows no result: it was added after the run", shows(tab, "biggest_sale") == (None, None, ""))
    p.shot(win, f"① biggest_sale, new, shows no result of its own. ② read_sales, connected into it, shows its result "
                f"from run #{first}.",
           highlight=["biggest_sale", "read_sales"], badges=True, zoom=["read_sales", "total_sales", "biggest_sale"])

    p.step("Right-click biggest_sale.")
    items = {}

    def look_at_menu(menu):
        items.update((a.text(), a.isEnabled()) for a in menu.actions() if a.text())
        p.shot(menu, f"Its menu: ① “Run only this block, with the same inputs” can be chosen, and names run #{first}, "
                     "the run whose result read_sales shows.", over=win,
               highlight=[menu_item(menu, "Run only this block, with the same inputs")], badges=True,
               zoom=[menu, "biggest_sale"])

    p.while_open(lambda: right_click(tab, "biggest_sale"), look_at_menu)
    same = [t for t in items if t.startswith("Run only this block, with the same inputs")]
    onwards = [t for t in items if t.startswith("Run from this block on, with the same inputs")]
    p.check(f"“Run only this block, with the same inputs” can be chosen, and names run #{first}",
            len(same) == 1 and items[same[0]] and f"(run #{first} · " in same[0])
    p.check(f"“Run from this block on, with the same inputs” can be chosen too, and names run #{first}",
            len(onwards) == 1 and items[onwards[0]] and f"(run #{first} · " in onwards[0])

    p.step("Choose “Run only this block, with the same inputs”.")
    p.while_open(lambda: right_click(tab, "biggest_sale"),
                 lambda menu: choose(menu, "Run only this block, with the same inputs"))
    second = wait_runs(qtbot, tab, 2)
    p.shot(win, f"① biggest_sale ran, in run #{second}, on read_sales's result from run #{first}: it is green and "
                f"made 120, the biggest sale. ② read_sales and total_sales did not run again: they still show their "
                f"results from run #{first}.",
           highlight=["biggest_sale", ["read_sales", "total_sales"]], badges=True,
           zoom=["read_sales", "total_sales", "biggest_sale"])
    p.check(f"biggest_sale shows success, from run #{second}, and made 120",
            tab.runs[-1].status == "success" and shows(tab, "biggest_sale") == ("success", second, "120"))
    p.check(f"read_sales and total_sales still show their results from run #{first}",
            shows(tab, "read_sales")[:2] == ("success", first) and shows(tab, "total_sales")[:2] == ("success", first))
    p.check("The new block was saved before it ran", not tab.doc.dirty)
    alone = (win.log.text, f"running only biggest_sale, with the inputs it got in run #{first}")
    p.shot(win, f"③ The Log, under the canvas: run #{second} started, ran only biggest_sale, with the inputs it got "
                f"in run #{first}, and ended with success. (The Log shows the lines of the selected block, "
                "biggest_sale, and each run's first and last lines.)",
           highlight=[alone], badges=[3],
           zoom=[win.log.showing, win.log.clear_filter, (win.log.text, f"run #{first} "),
                 (win.log.text, f"run #{second} ")])
    p.check(f"③ The Log says run #{second} ran only biggest_sale, with the inputs it got in run #{first}",
            alone[1] in win.log.text.toPlainText())

    p.step("Click the Run history tab, under the canvas.")
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.bottom_tabs.indexOf(win.history_panel)).center())
    table = win.history_panel.table
    runs = [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]
    p.shot(win, f"Started by: run #{second} ran only biggest_sale; run #{first}, the whole flow, was started by "
                "start_by_hand.",
           highlight=[[table.item(r, 2) for r in range(table.rowCount())]],
           zoom=[table.item(0, 0), table.item(table.rowCount() - 1, 4)])
    p.check(f"Run history: run #{second}, started by “only biggest_sale”, succeeded; run #{first} was started by "
            "start_by_hand",
            [(r[0], r[2], r[3]) for r in runs] == [(str(second), "only biggest_sale", "success"),
                                                    (str(first), "start_by_hand", "success")])


# --- 3. the zoom control ---------------------------------------------------------------------------------------

def test_the_zoom_can_be_typed_and_its_buttons_are_clearer(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Type the zoom; bolder + and −, stacked arrows",
              "Zoom: Allow to write the zoom value, not just the step value; the up and down arrows should be "
              "stacked one on top of the other; make the + and - bigger or bolder")
    p.idea("The zoom percentage in the corner of the canvas is now a box you can type in; + and − are bold and "
           "bigger; the step box's arrows are stacked, one above the other.")
    p.idea("Click the percentage: what it shows is selected, so what you type replaces it. Type 150 (or 150%) and "
           "press Enter: the canvas zooms to 150%, around the middle of what you see. A zoom under 20% or over 300% "
           "is set to the nearest of them; text that is not a number leaves the zoom as it was.")
    p.idea("+ and − are bold and half as big again as the usual text. The step box between them has its up arrow "
           "above its down arrow, on its right, also on Windows 11, whose usual look puts them side by side.")
    p.why_simplest("The percentage was already shown there: it became a box you can type in, in the same place. "
                   "What you type goes to the very zoom that + and − use, so it has the same limits and zooms around "
                   "the same middle point, and the wheel, + and − go on from it.")
    p.why_simplest("For the arrows, the step box alone is drawn in a plain standard look that is built into the "
                   "toolkit Taskloom's windows are made with, and that look always stacks them; the rest of Taskloom "
                   "keeps your system's look. For + and −, the same buttons get a bold font, half as big again.")
    p.alternative("A Zoom to… window, or a list of set zooms (50%, 100%, 150%…)",
                  "Another window or list to open, and a list offers only its own values. Typing where the zoom is "
                  "already shown takes one click.")
    p.alternative("Drawing the step box's arrows ourselves",
                  "More to draw and to make clickable, for what that standard look already does.")
    p.alternative("That standard look for the whole window",
                  "The arrows would be stacked, but every window of Taskloom would stop looking like the rest of "
                  "your system, when only the step box needed the change.")
    p.fits("A typed zoom has the mouse wheel's limits, 20% to 300%, and the box follows every zoom: typed, by + and "
           "−, or with the wheel.")
    p.fits("Like + and −, a typed zoom keeps the middle of what you see in the middle, so the part of the flow you "
           "were looking at stays in front of you.")
    p.fits("Leaving the box without pressing Enter puts back the zoom the canvas is at.")
    flow = write_flow(tmp_path / "daily-sales.yaml", FLOW)

    p.step("Open the flow daily-sales.yaml (File > Open…). The zoom control is in the top-right corner of the "
           "canvas.")
    tab = open_flow(win, monkeypatch, flow)
    view, bar = tab.view, tab.view.zoom_bar
    QApplication.processEvents()
    before, width_before = view.zoom_percent(), width_on_screen(tab, "read_sales")
    middle = view.mapToScene(view.viewport().rect().center())  # the point of the flow in the middle
    p.shot(win, f"① The zoom control: a magnifier, the zoom ({bar.percent.text()}), +, the step that + and − zoom "
                f"by ({bar.step.text()}), and −.",
           highlight=[bar], badges=True, zoom=[bar])
    p.check(f"The canvas is at {round(before)}%, and the box says so", bar.percent.text() == f"{round(before)}%")

    p.step("Click the percentage and type 150.")
    QTest.mouseClick(bar.percent, Qt.LeftButton)
    p.check("Clicking it selects what it shows, so what you type replaces it",
            bar.percent.selectedText() == bar.percent.text())
    QTest.keyClicks(bar.percent, "150")
    p.shot(win, f"① The box shows 150, typed. The canvas is still at {round(before)}%: it zooms when you press "
                "Enter.", highlight=[bar], badges=True, zoom=[bar])
    p.check("The box shows 150", bar.percent.text() == "150")
    p.check(f"The canvas has not zoomed yet: it is still at {round(before)}%", view.zoom_percent() == before)

    p.step("Press Enter.")
    QTest.keyClick(bar.percent, Qt.Key_Return)
    QApplication.processEvents()
    after = width_on_screen(tab, "read_sales")
    now_middle = view.mapFromScene(middle) - view.viewport().rect().center()  # where it is now, from the middle
    p.shot(win, "The canvas is at 150%: ② read_sales and the other blocks are drawn half as big again as at 100%. "
                "① The box says 150%.",
           highlight=[bar, "read_sales"], badges=True, zoom=[bar])
    p.check("The canvas zoomed to 150%, and the box says “150%”",
            round(view.zoom_percent(), 6) == 150 and bar.percent.text() == "150%")
    p.check(f"read_sales is drawn 1.5 times as wide as at 100% ({NODE_W * 1.5:.0f} pixels instead of {NODE_W})",
            abs(after - NODE_W * 1.5) <= 1 and abs(width_before - NODE_W * before / 100) <= 1)
    p.check("It zoomed around the middle of what you see: the same point of the flow is still in the middle",
            abs(now_middle.x()) <= 2 and abs(now_middle.y()) <= 2)

    p.step("Click the step box's up arrow five times, then click +.")
    up, down, number = arrows(bar.step)
    for _ in range(5):
        QTest.mouseClick(bar.step, Qt.LeftButton, Qt.NoModifier, up.center())
    QApplication.processEvents()
    step = bar.step.value()
    QTest.mouseClick(bar.plus, Qt.LeftButton)
    QApplication.processEvents()
    p.shot(win, "Up close: + and − are bold and bigger than the text around them. The step box's arrows are stacked "
                "on its right, ▲ above ▼; five clicks on ▲ made the step 10%, and + then zoomed from 150% to 160%.",
           highlight=[bar], zoom=[bar])
    usual = QApplication.font(bar.plus).pointSizeF()
    p.check(f"+ and − are bold, and bigger than the usual text: {bar.plus.font().pointSizeF():g} points against "
            f"{usual:g}", all(b.font().bold() and b.font().pointSizeF() > usual for b in (bar.plus, bar.minus)))
    p.check("The step box's arrows are stacked on its right: ▲ just above ▼, in one column, right of the number",
            stacked(up, down, number))
    p.check("Five clicks on ▲ made the step 10%", step == 10 and bar.step.text() == "10%")
    p.check("+ then zoomed from 150% to 160%", round(view.zoom_percent(), 6) == 160 and bar.percent.text() == "160%")

    # Windows 11's look puts a number box's arrows side by side: the step box keeps its own look, whatever the
    # window's. Checked with that look where it exists (on Windows); the screenshots keep this system's look.
    if "windows11" in [k.lower() for k in QStyleFactory.keys()]:
        original, kept = QApplication.style().name(), [QStyleFactory.create("windows11")]
        try:
            QApplication.setStyle(kept[0])
            QApplication.processEvents()
            in_windows_11 = arrows(bar.step)
        finally:
            kept.append(QStyleFactory.create(original))
            QApplication.setStyle(kept[-1])
            QApplication.processEvents()
        p.check("With Windows 11's look for the window, which puts a number box's arrows side by side, the step "
                "box's arrows are still stacked", stacked(*in_windows_11))
    else:
        p.note("Windows 11's look, which puts a number box's arrows side by side, does not exist on this system, so "
               "the step box was not also checked with it here. On Windows, this proof checks that too.")
