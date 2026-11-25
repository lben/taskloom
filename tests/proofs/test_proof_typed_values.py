"""Proof: a value typed into an input on the canvas, in the box beside it (double-click the input, or right-click it →
Set value…). See README.md here."""

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QContextMenuEvent, QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QListWidget

from proofs.conftest import choose, menu_item
from proofs.test_proof_python_reusable_block import (click_block, connect, drag_block, in_window, open_flow, run_now,
                                                     shows, tooltip, tree_item, write_flow)
from tests.test_paths_editor_editing import menu as action
from tests.test_typed_values_editor import (box_for, box_texts, check_label_fits, fill_in, input_values, key, open_box,
                                            plain, row_picture, settle, shown_value, value_row)

ASKED = ("I want the value to be typed on the canvas view to the side of the input (probably a small dialog shows "
         "up)")
TYPES = ["text", "int", "float", "bool", "date", "datetime", "list"]
SALES = """
    blocks:
      start:
        type: trigger.manual
        ui: {x: 0, y: 0}
      load_sales:
        type: logic.python
        config: {inputs: [], outputs: [rows], code: "rows = [120, 80, 45, 300]"}
        ui: {x: 260, y: 0}
      load_limit:
        type: logic.python
        config: {inputs: [], outputs: [limit], code: "limit = 250"}
        ui: {x: 260, y: 180}
      filter:
        type: logic.python
        config:
          inputs: [rows, threshold]
          outputs: [big]
          code: |-
            kind = type(threshold).__name__
            print('threshold got', repr(threshold), 'of the type', kind)
            big = [amount for amount in rows if amount > threshold]
        ui: {x: 540, y: 0}
    edges:
      - start.start -> load_sales.after
      - start.start -> load_limit.after
      - load_sales.rows -> filter.rows
"""
WRONG, RIGHT = "1,5", "100"
BIG = "[120, 300]"  # the amounts above 100
LIMIT_LINE = ("load_limit", "limit", "filter", "threshold")
NO_TABLE = "A table input takes a table from a line: no typed value is a table."


def right_click_hole(tab, block: str, name: str):
    """Right-click an input's circle (opens its menu, which waits for a choice: see Proof.while_open)."""
    viewport = tab.view.viewport()
    at = tab.view.mapFromScene(tab.scene.nodes[block].ports[(name, False)].scenePos())
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, viewport.mapToGlobal(at)))


def hole(win, tab, block: str, name: str) -> QRect:
    """An input's circle, in the window's pixels."""
    centre = tab.view.mapFromScene(tab.scene.nodes[block].ports[(name, False)].scenePos())
    return in_window(win, tab.view.viewport(), QRect(centre - QPoint(7, 7), centre + QPoint(7, 7)))


def value(tab, block: str, name: str):
    """The block's typed value for the input, as the flow holds it (None: none)."""
    return (tab.doc.blocks[block].get("inputs") or {}).get(name)


def test_a_value_typed_into_an_input_beside_it_on_the_canvas(proof, env, win, qtbot, monkeypatch):
    p = proof("A value typed into an input, beside it on the canvas", ASKED)
    p.idea("An input with no line can hold a value of its own. Double-click the input's circle, or right-click it → "
           "Set value…: a small box opens beside it, with the value and its type (text, int, float, bool, date, "
           "datetime or list: the types of flow parameters). Enter keeps it, Esc leaves everything as it was, and a "
           "value that does not fit its type is not kept: the box says why.")
    p.idea("The block shows the value right of the input's name, and Properties shows the same value under Input "
           "values, where it can be changed too. At a run the block gets it as if a line brought it, with its type: "
           "an int arrives as a number.")
    p.idea("Connecting a line to the input replaces its value, in one step that one Undo takes back. An input that "
           "cannot hold a value (one that takes a table, collects many lines, or only says when the block runs) "
           "says why in its menu.")
    p.why_simplest("A typed value stands in for one line into the input: the block gets it exactly as it gets what a "
                   "line brings, so no block had to change to take one. Its types, and how its text is read, are "
                   "those of flow parameters, which Taskloom already had.")
    p.why_simplest("There is one value, kept with the block in the flow file, and the box and Properties change it "
                   "in the same way: the two cannot disagree.")
    p.why_simplest("A press on an input already starts a line, so the box opens on a double-click, or from the "
                   "input's right-click menu.")
    p.alternative("A block that gives a constant, connected to the input",
                  "One more block and one more line for each value, and the value is not seen at the input it is for. "
                  "You asked for it to be typed beside the input.")
    p.alternative("The value typed only in Properties",
                  "You asked for it on the canvas, beside the input. Properties shows the same value, for when the "
                  "block is selected.")
    p.alternative("A typed value kept when a line is connected, the line winning at a run",
                  "Two sources for one input would hide which one the block gets. Connecting a line removes the "
                  "value instead, and one Undo brings it back.")
    p.fits("The types and the way a value's text is read are those of flow parameters: “a, b” as a list is two "
           "items, “yes” as a bool is true. {{name}} in a typed text is filled once, as in any setting.")
    p.fits("Validate counts a typed value as a supplied input, a failure report's Inputs list it, and “with inputs I "
           "choose…” starts from it. Each copy of a reusable block keeps its own typed values, as it keeps its own "
           "lines.")
    flow = write_flow(env.root / "sales.yaml", SALES)

    # --- 1. the input -------------------------------------------------------------------------------------------------
    p.step("Open sales.yaml (File > Open…). load_sales gives four amounts; filter, a Python Code block, keeps those "
           "above threshold. Its threshold input has no line.")
    tab = open_flow(win, monkeypatch, flow)
    before = row_picture(tab, "filter", "threshold")  # (the row as drawn without a value: to compare)
    p.shot(win, "① filter: rows comes by a line from load_sales; its threshold input, the circle left of threshold, "
                "has none.", highlight=["filter"], badges=True, zoom=["load_sales", "filter"])

    # --- 2. the box ---------------------------------------------------------------------------------------------------
    p.step("Double-click the threshold input (its circle, on filter's left side).")
    box = box_for(qtbot, win, tab, "filter", "threshold", centre=False)
    types, field = box.findChild(QListWidget, "typedValueType"), box.findChild(QLineEdit, "typedValueField")
    p.shot(win, "A small box opens beside the input, left of it, naming it: filter.threshold. On its right, the "
                "type, one of the types of flow parameters; on its left, the value. Enter keeps it, Esc cancels.",
           highlight=[box], zoom=[box, "filter"])
    p.check("The box names filter.threshold and says Enter keeps it, Esc cancels",
            {"filter.threshold", "Enter keeps it, Esc cancels"} <= set(box_texts(box)))
    p.check(f"It offers the types {', '.join(TYPES)}", [types.item(i).text() for i in range(types.count())] == TYPES)
    p.check("It is beside the input, not over it",
            not in_window(win, box, box.rect()).intersects(hole(win, tab, "filter", "threshold")))

    # --- 3. a value that does not fit -------------------------------------------------------------------------------
    p.step(f"Click int, type {WRONG} and press Enter.")
    fill_in(box, "int", WRONG)
    key(win, box, Qt.Key_Return)
    error = box.findChild(QLabel, "typedValueError")
    p.shot(win, f"① {WRONG} is no int: the box says why, and stays open with what you typed, to correct it.",
           highlight=[error], badges=True, zoom=[box, "filter"])
    p.check(f"{WRONG} as an int is not kept: the box stays open and says why, naming the type",
            open_box(win) is box and error.isVisible() and "int" in error.text() and field.text() == WRONG)
    p.check("filter has no value yet", value(tab, "filter", "threshold") is None)

    # --- 4. Enter keeps it -------------------------------------------------------------------------------------------
    p.step(f"Type {RIGHT} instead and press Enter.")
    fill_in(box, None, RIGHT)
    key(win, box, Qt.Key_Return)
    try:
        span = check_label_fits(tab, "filter", "threshold", before)
    except AssertionError:
        span = None
    p.shot(win, f"The box closed, and ① filter shows {RIGHT} right of the input's name, threshold.",
           highlight=["filter"], badges=True, zoom=["filter"])
    p.check("Enter kept it: the box closed, and the flow holds the int 100",
            open_box(win) is None and value(tab, "filter", "threshold") == {"type": "int", "value": RIGHT})
    p.check(f"filter shows {RIGHT} right of threshold, clear of the output name",
            shown_value(tab, "filter", "threshold") == RIGHT and span is not None)

    # --- 5. Properties ------------------------------------------------------------------------------------------------
    p.step("Click filter.")
    click_block(tab, "filter")
    group = input_values(win)
    line = value_row(win, "rows")
    edit, kind = value_row(win, "threshold")
    p.shot(win, f"Properties show the same value under ① Input values: rows comes by a line from load_sales; "
                f"threshold is {RIGHT}, an int. A change here shows on the canvas too.",
           highlight=[group], badges=[1], zoom=[group])
    p.check("Input values: rows has a line from load_sales.rows; threshold holds 100, an int",
            plain(line.text()).strip() == "line from load_sales.rows"
            and (edit.text(), kind.currentText()) == (RIGHT, "int"))

    # --- 6. the run ---------------------------------------------------------------------------------------------------
    p.step("Press ▶ Run now.")
    run = run_now(win, qtbot, tab)
    said = "threshold got 100 of the type int"
    p.shot(win, f"① filter got 100 as a number: it kept the amounts above it, {BIG}. ② Its code printed the type of "
                f"what threshold got: int.", highlight=["filter", (win.log.text, said)], badges=True,
           zoom=["filter"])
    p.check("The run ended with success", run.status == "success")
    p.check(f"filter kept {BIG}", shows(tab, "filter") == ("success", f"✓ big: {BIG}"))
    p.check(f"filter's code got an int: the Log says “{said}”", said in win.log.text.toPlainText())

    # --- 7. right-click → Set value…, Esc ----------------------------------------------------------------------------
    p.step("Right-click the threshold input.")
    opened = {}

    def the_menu(menu):
        p.shot(menu, "Its menu: ① Set value… opens the same box; ② Clear value removes the value.", over=win,
               highlight=[menu_item(menu, "Set value…"), menu_item(menu, "Clear value")], badges=True,
               zoom=[menu, "filter"])
        p.step("Choose Set value….")
        choose(menu, "Set value…")

    p.while_open(lambda: right_click_hole(tab, "filter", "threshold"), the_menu)
    qtbot.waitUntil(lambda: open_box(win) is not None, timeout=3_000)
    box = open_box(win)
    opened["type"] = box.findChild(QListWidget, "typedValueType").currentItem().text()
    opened["value"] = box.findChild(QLineEdit, "typedValueField").text()
    p.shot(win, f"The same box, beside the input: it starts from the value, int and {RIGHT}.", highlight=[box],
           zoom=[box, "filter"])
    p.check("The box starts from the value: int, 100", opened == {"type": "int", "value": RIGHT})
    p.step("Type 7 and press Esc.")
    fill_in(box, None, "7")
    key(win, box, Qt.Key_Escape)
    p.check("Esc closed the box and changed nothing: the value is still 100",
            open_box(win) is None and value(tab, "filter", "threshold") == {"type": "int", "value": RIGHT}
            and shown_value(tab, "filter", "threshold") == RIGHT and not tab.doc.dirty)

    # --- 8. a line replaces the value; one Undo -----------------------------------------------------------------------
    p.step("Drag from load_limit's limit output to filter's threshold input.")
    connect(tab, *LIMIT_LINE)
    settle()  # (Properties are drawn again after the change)
    group = input_values(win)
    p.shot(win, "The line replaced the value: ① filter shows none at threshold, and ② Properties say threshold has a "
                "line from load_limit.limit.", highlight=["filter", group], badges=True, zoom=["load_limit", "filter"])
    line = value_row(win, "threshold")
    p.check("The line is there, and the value is gone, on the canvas and in Properties",
            LIMIT_LINE in tab.doc.edges() and value(tab, "filter", "threshold") is None
            and shown_value(tab, "filter", "threshold") is None
            and isinstance(line, QLabel) and plain(line.text()).strip() == "line from load_limit.limit")
    p.step("Choose Edit > Undo, once.")
    action(win, "Undo").trigger()
    settle()
    group = input_values(win)
    p.shot(win, f"One Undo: the line is gone, and {RIGHT} is back ① in filter and ② in Properties.",
           highlight=["filter", group], badges=True, zoom=["load_limit", "filter"])
    edit, kind = value_row(win, "threshold")
    p.check("One Undo took the line away and brought the value back, on the canvas and in Properties",
            LIMIT_LINE not in tab.doc.edges() and value(tab, "filter", "threshold") == {"type": "int", "value": RIGHT}
            and shown_value(tab, "filter", "threshold") == RIGHT
            and (edit.text(), kind.currentText()) == (RIGHT, "int"))

    # --- 9. an input that takes no value ---------------------------------------------------------------------------
    p.step("Add a Write File block: double-click Write File in the blocks tree, and drag the new block below filter. "
           "Right-click its table input.")
    tree = win.palette_panel.tree
    item = tree_item(win, "Write File")
    tree.scrollToItem(item)
    QApplication.processEvents()
    at = tree.visualItemRect(item).center()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # a double-click begins with a click
    QTest.mouseDClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()
    assert "write_file" in tab.doc.blocks
    drag_block(tab, "write_file", QPointF(540, 220))
    p.shot(win, "① The new block, write_file. Its table input, the circle left of table, takes a table.",
           highlight=["write_file"], badges=True, zoom=["write_file"])
    tips = {}

    def no_value(menu):
        set_value = menu_item(menu, "Set value…")
        tips["enabled"], tips["tip"] = set_value.isEnabled(), set_value.toolTip()
        p.shot(menu, "① Set value… is greyed out.", over=win, highlight=[set_value], badges=True,
               zoom=[menu, "write_file"])
        p.step("Rest the mouse on Set value….")
        at = menu.actionGeometry(set_value).center()
        QApplication.sendEvent(menu, QHelpEvent(QEvent.ToolTip, at, menu.mapToGlobal(at)))
        qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
        tips["shown"] = tooltip().text()
        p.shot(tooltip(), "It says why: a table input takes a table from a line, and no typed value is a table.")

    p.while_open(lambda: right_click_hole(tab, "write_file", "table"), no_value)
    p.check("Set value… cannot be chosen on Write File's table input, and its tooltip says why",
            not tips["enabled"] and tips["tip"] == NO_TABLE and tips["shown"] == NO_TABLE)
    p.note("The other inputs that take no value say why in the same way: those that collect many lines into a list "
           "(Send Email's body and attachments, HTML Template's data, Merger's lines), those that expect a dictionary "
           "(Run Query's params, Statistics' extra), the Loop's result, and run after.")
    tab.doc.dirty = False  # (write_file is not saved: closing the window at the end asks nothing)
