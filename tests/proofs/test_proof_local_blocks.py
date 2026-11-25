"""Proof: a Python Code block made a Local reusable block, copied with Copy and Paste, each copy given its own typed
value; then promoted to a global reusable block, and the flow run on the command line. See README.md here."""

import yaml
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QDialogButtonBox, QLabel, QMessageBox, QRadioButton

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_python_reusable_block import (click_block, connect, item_rect, open_flow, retype, run_now, shows,
                                                     tree_item, write_flow)
from taskloom.editor.panels import CodeEdit
from tests.test_paths_editor_editing import field
from tests.test_paths_editor_editing import menu as action
from tests.test_typed_values_editor import box_for, fill_in, key, open_box, shown_value

REAL_QUESTION = QMessageBox.question  # (the win fixture answers questions itself: here you see them)
ASKED = ("Create reusable block offers Global (saved in the tree on the left) or Local (only this flow, not in the "
         "tree; copy and paste make more; right-click \"Promote to global reusable block\")")
CODE = "total = amount * (100 - percent) / 100"
ORDER = """
    blocks:
      start:
        type: trigger.manual
        ui: {x: 0, y: 0}
      load_order:
        type: logic.python
        config: {inputs: [], outputs: [amount], code: "amount = 200"}
        ui: {x: 260, y: 0}
      discount:
        type: logic.python
        config:
          inputs: [amount, percent]
          outputs: [total]
          code: "%s"
        ui: {x: 520, y: 0}
    edges:
      - start.start -> load_order.after
      - load_order.amount -> discount.amount
""" % CODE
NAME = "Apply discount"
WHAT_IT_DOES = "Takes a percentage off an amount."
INPUTS_EXPECT = "amount: a number. percent: how much to take off, in percent (a whole number)."
LOCAL_TYPE, GLOBAL_TYPE = "local.apply_discount", "user.apply_discount"
PROMOTE = "Promote to global reusable block…"
UNDO_WORDS = "Undo cannot remove the global block's file"
VALUES = {"discount": "10", "discount_2": "25"}  # each copy's percent
TOTALS = {"discount": "180.0", "discount_2": "150.0"}  # 200 less 10 %, less 25 %


def right_click_canvas(tab, scene_point: QPointF):
    """Right-click an empty spot of the canvas (opens its menu, which waits for a choice: see Proof.while_open)."""
    viewport = tab.view.viewport()
    at = tab.view.mapFromScene(scene_point)
    assert viewport.rect().contains(at) and not tab.scene.items(scene_point), "not an empty spot of the canvas"
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, viewport.mapToGlobal(at)))


def type_value(qtbot, win, tab, block: str, value: str):
    """Double-click the block's percent input, choose int, type the value (Enter is pressed by the caller)."""
    box = box_for(qtbot, win, tab, block, "percent", centre=False)
    fill_in(box, "int", value)
    return box


def test_a_python_code_block_reused_in_its_flow_with_another_value_in_each_copy(proof, env, win, qtbot, monkeypatch):
    p = proof("One Python Code block reused in its flow, each copy with its own value", ASKED)
    p.idea("Right-click a Python Code block → Create reusable block… asks where the new block is kept: Global, a file "
           "in your blocks folder, listed in the blocks tree for every flow (as before), or Local, only this flow. "
           "Local keeps its definition (its inputs, outputs and code) in the flow file, and the block itself becomes "
           "its first copy: its name, place, lines and typed values stay.")
    p.idea("Copy and Paste make more copies. Every copy runs the same code, and each has its own lines and typed "
           "values, so two copies can differ in one value. Properties of any copy show the shared inputs, outputs and "
           "code under “Shared by N blocks: an edit changes all N”.")
    p.idea("Right-click a copy → Promote to global reusable block… asks first (Undo cannot remove the file it writes), "
           "then saves the block in your blocks folder and lists it in the blocks tree; every copy in the flow uses "
           "it, and the flow file no longer holds the definition.")
    p.why_simplest("A Local block is a block of your own whose definition is in the flow file instead of a file of "
                   "its own: the same code, inputs and outputs, run the same way. The editor, taskloom run and the "
                   "scheduler service already read the flow file, so a flow with Local blocks needs no other file to "
                   "run.")
    p.why_simplest("The block itself becoming the first copy keeps its lines and values where they are, and leaves no "
                   "second block with the same code to drift apart from the copies.")
    p.alternative("A copy placed beside the block, the block itself left as it is (your first idea)",
                  "The flow would keep two blocks with the same code: a fix made in the copies would miss the "
                  "original, and its lines would have to be moved by hand. You chose the block itself becoming the "
                  "first copy.")
    p.alternative("Pasted Python Code blocks, each with its own code (which you could already do)",
                  "A fix would have to be made in every copy. With one definition shared by all copies, one edit "
                  "changes them all, and Properties says so before you make it.")
    p.alternative("Promote without asking, Undo removing the file it wrote",
                  "Undo changes flows, not the files in your blocks folder, which other flows may use as soon as the "
                  "file is there. So it asks first, and says that Undo cannot remove the file.")
    p.fits("It is Create reusable block…'s window, with one more choice: Global, chosen as it opens, does what it did "
           "before. A Local block runs as a block of your own does: the code, inputs and outputs it was made with.")
    p.fits("The values typed into an input on the canvas (double-click it) belong to each block: each copy keeps its "
           "own, as it keeps its own lines. Copy and Paste are the Edit menu's, as for any block, and the definition "
           "goes with the copied block, also into another flow's tab.")
    p.fits("Promote writes the same file that Create reusable block… writes for a Global block, under User Created in "
           "the blocks tree.")
    flow = write_flow(env.root / "order-prices.yaml", ORDER)
    assert win.home.root == env.home  # the editor and the command line use the same Taskloom folder

    # --- 1. the block ------------------------------------------------------------------------------------------------
    p.step("Open order-prices.yaml (File > Open…) and click discount. load_order gives an amount, 200; discount, a "
           "Python Code block, takes a percentage off it. Its percent input has no line.")
    tab = open_flow(win, monkeypatch, flow)
    click_block(tab, "discount")
    code = next(c for c in win.properties.widget().findChildren(CodeEdit) if c.isVisible())
    p.shot(win, f"① discount, a Python Code block: amount comes by a line from load_order; percent has none. ② Its "
                f"code, in Properties: {CODE}.", highlight=["discount", (code, CODE)], badges=True,
           zoom=["load_order", "discount"])
    p.check("discount is a Python Code block with that code", tab.doc.blocks["discount"]["type"] == "logic.python"
            and code.toPlainText() == CODE)

    # --- 2. Create reusable block…, Local --------------------------------------------------------------------------
    p.step("Right-click discount.")
    lines_before = list(tab.doc.data["edges"])
    seen = {}

    def fill_in_window(dialog):
        global_, local = (dialog.findChild(QRadioButton, n) for n in ("reusable_global", "reusable_local"))
        p.check("The window asks Global or Local; Global is chosen as it opens",
                global_ is not None and local is not None and global_.isChecked() and not local.isChecked())
        QTest.mouseClick(local, Qt.LeftButton, Qt.NoModifier, local.rect().center())
        retype(dialog.name, NAME)
        retype(dialog.what, WHAT_IT_DOES)
        retype(dialog.expects, INPUTS_EXPECT)
        about = next(w for w in dialog.findChildren(QLabel) if w.text().startswith("Only this flow"))
        p.shot(dialog, "① Local: only this flow, kept in the flow file, not in the blocks tree. ② Its name, what it "
                       "does and what its inputs expect, as for a Global block.",
               highlight=[[local, about], [dialog.name, dialog.what, dialog.expects]], badges=True)
        p.check("Local is chosen", local.isChecked() and not global_.isChecked())
        p.step("Press OK.")
        QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok), Qt.LeftButton)
        seen["closed"] = not dialog.isVisible()

    def the_menu(menu):
        p.shot(menu, "Its menu: ① Create reusable block….", over=win,
               highlight=[menu_item(menu, "Create reusable block…")], badges=True, zoom=[menu, "discount"])
        p.step("Choose Create reusable block…, click Local, and fill in the window: Name, What it does, What its "
               "inputs expect.")
        p.while_open(lambda: choose(menu, "Create reusable block…"), fill_in_window)

    p.while_open(lambda: right_click(tab, "discount"), the_menu)
    QApplication.processEvents()
    p.check("OK closed the window", seen.get("closed"))
    p.shot(win, f"① discount is now the first copy of the Local block {NAME}: the same name, place and line, and at "
                f"its foot, {NAME}. ② The blocks tree has no {NAME}: a Local block is kept in this flow. ③ The status "
                f"bar says so.", highlight=["discount", win.palette_panel.tree, win.statusBar()], badges=True,
           zoom=["load_order", "discount"])
    p.check("The status bar says discount is the first copy, kept in this flow's file",
            win.statusBar().currentMessage() == f"discount is the first copy of the Local block {NAME}, kept in this "
                                                "flow's file: Copy and Paste it to make more")
    p.check(f"discount is a copy of {NAME} ({LOCAL_TYPE}), with no code of its own, and its line is kept",
            tab.doc.blocks["discount"]["type"] == LOCAL_TYPE and not tab.doc.blocks["discount"].get("config")
            and tab.doc.data["edges"] == lines_before)
    p.check(f"The flow holds the definition of {NAME}: its inputs, outputs and code",
            {k: tab.doc.data["local_blocks"]["apply_discount"][k] for k in ("title", "inputs", "outputs", "code")}
            == {"title": NAME, "inputs": ["amount", "percent"], "outputs": ["total"], "code": CODE})
    p.check(f"{NAME} is not in the blocks tree, and the blocks folder has no file",
            tree_item(win, NAME) is None and not list(win.home.blocks_dir.glob("*.py")))

    # --- 3. a second copy --------------------------------------------------------------------------------------------
    p.step("Click discount and choose Edit > Copy. Right-click the empty canvas below discount and choose Paste. Then "
           "drag from load_order's amount output to the new block's amount input.")
    click_block(tab, "discount")
    action(win, "Copy").trigger()
    p.while_open(lambda: right_click_canvas(tab, QPointF(520, 200)), lambda menu: choose(menu, "Paste"))
    QApplication.processEvents()
    p.check("Paste made discount_2, another copy of the same Local block",
            tab.doc.blocks.get("discount_2", {}).get("type") == LOCAL_TYPE)
    connect(tab, "load_order", "amount", "discount_2", "amount")
    p.shot(win, f"Two copies of {NAME}: ① discount and ② discount_2, each with a line from load_order.",
           highlight=["discount", "discount_2"], badges=True, zoom=["load_order", "discount", "discount_2"])
    p.check("load_order's amount goes to both copies",
            {("load_order", "amount", c, "amount") for c in VALUES} <= set(tab.doc.edges()))

    # --- 4. Properties of a copy -------------------------------------------------------------------------------------
    p.step("Click discount_2.")
    click_block(tab, "discount_2")
    body = win.properties.widget()
    note = body.findChild(QLabel, "shared_note")
    code = body.findChild(CodeEdit, "local_code")
    inputs, outputs = field(win, "Inputs"), field(win, "Outputs")
    names = [w for w in body.findChildren(QLabel) if w.isVisible() and w.text() in ("Inputs", "Outputs")]
    p.shot(win, "Its Properties: ① its inputs and outputs, and under ② “Shared by 2 blocks: an edit changes all 2”, "
                "③ its code.", highlight=[[*names, inputs, outputs], note, (code, CODE)], badges=True,
           zoom=[*names, inputs, outputs, note, (code, CODE)])
    p.check("Properties of discount_2 say “Shared by 2 blocks: an edit changes all 2”",
            note is not None and note.text() == "Shared by 2 blocks: an edit changes all 2")
    p.check("They show the shared inputs, outputs and code",
            (inputs.text(), outputs.text(), code.toPlainText()) == ("amount, percent", "total", CODE))

    # --- 5. a value of its own for each copy ------------------------------------------------------------------------
    p.step("Double-click discount's percent input (its circle, on the left). In the box beside it, choose int, type 10 "
           "and press Enter.")
    box = type_value(qtbot, win, tab, "discount", VALUES["discount"])
    p.shot(win, "The box opens beside discount's percent input, left of it, naming it: discount.percent. In it, the "
                "type, int, and the value, 10.", highlight=[box], zoom=[box, "discount"])
    key(win, box, Qt.Key_Return)
    p.check("Enter kept it: the box closed", open_box(win) is None)
    p.step("Do the same on discount_2's percent input, with 25.")
    box = type_value(qtbot, win, tab, "discount_2", VALUES["discount_2"])
    key(win, box, Qt.Key_Return)
    p.shot(win, "Each copy shows its own value right of percent: ① 10 in discount, ② 25 in discount_2.",
           highlight=list(VALUES), badges=True, zoom=list(VALUES))
    p.check("discount shows 10 and discount_2 shows 25, at percent",
            all(shown_value(tab, c, "percent") == v for c, v in VALUES.items()))

    # --- 6. the run --------------------------------------------------------------------------------------------------
    p.step("Press ▶ Run now. It saves the flow, then runs it.")
    run = run_now(win, qtbot, tab)
    p.shot(win, f"The same code, two results: ① discount took 10 % off 200: {TOTALS['discount']}; ② discount_2 took "
                f"25 % off: {TOTALS['discount_2']}.", highlight=list(VALUES), badges=True,
           zoom=["load_order", *VALUES])
    p.check("The run ended with success", run.status == "success")
    p.check(f"discount gave {TOTALS['discount']} and discount_2 {TOTALS['discount_2']}",
            all(shows(tab, c) == ("success", f"✓ total: {TOTALS[c]}") for c in VALUES))
    text = flow.read_text(encoding="utf-8")
    p.text("order-prices.yaml, as Run now saved it", text)
    saved = yaml.safe_load(text)
    p.check("The flow file holds the definition once, and two copies of it, each with its own percent and no code",
            list(saved["local_blocks"]) == ["apply_discount"]
            and all(saved["blocks"][c]["type"] == LOCAL_TYPE and "config" not in saved["blocks"][c]
                    and saved["blocks"][c]["inputs"] == {"percent": {"type": "int", "value": v}}
                    for c, v in VALUES.items()))
    p.note("Run now runs the flow with taskloom run, as the scheduler service does: the flow file is all it needs.")

    # --- 7. Promote to global reusable block -------------------------------------------------------------------------
    p.step(f"Right-click discount and choose {PROMOTE}")
    asked = []

    def say_yes(question):
        asked.append(question.text())
        words = next(w for w in question.findChildren(QLabel) if UNDO_WORDS in w.text())
        p.shot(question, "It asks first, and ① says where the file goes (your blocks folder: here a temporary one, "
                         "for this proof) and that Undo cannot remove the global block's file: Undo makes the copies "
                         "Local again, and the file stays.", highlight=[words], badges=True)
        p.step("Click Yes.")
        QTest.mouseClick(question.button(QMessageBox.Yes), Qt.LeftButton)

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "question", REAL_QUESTION)
        p.while_open(lambda: right_click(tab, "discount"),
                     lambda menu: p.while_open(lambda: choose(menu, PROMOTE), say_yes))
    QApplication.processEvents()
    p.check("It asked first, saying Undo cannot remove the global block's file",
            len(asked) == 1 and UNDO_WORDS in asked[0])
    folder, item = tree_item(win, "User Created"), tree_item(win, NAME)
    tree = win.palette_panel.tree
    if item is not None:
        tree.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        QApplication.processEvents()
    mine = [item_rect(win, folder), item_rect(win, item)] if item else []
    p.shot(win, f"① {NAME} is in the blocks tree, under User Created. ② Both copies use it, each with its own value.",
           highlight=[mine, list(VALUES)] if item else list(VALUES), badges=bool(item), zoom=mine or None)
    p.check(f"The blocks tree has {NAME} under User Created", item is not None and item.parent() is folder)
    p.check("Its file is in the blocks folder: apply_discount.py",
            [f.name for f in win.home.blocks_dir.glob("*.py")] == ["apply_discount.py"])
    p.check(f"Both copies are {NAME} blocks from the blocks tree ({GLOBAL_TYPE}), with their own values",
            all(tab.doc.blocks[c]["type"] == GLOBAL_TYPE
                and tab.doc.blocks[c]["inputs"] == {"percent": {"type": "int", "value": v}}
                for c, v in VALUES.items()))
    p.check("The flow no longer holds the definition", not tab.doc.data.get("local_blocks"))

    # --- 8. the command line -----------------------------------------------------------------------------------------
    p.step("Save the flow (File > Save) and run it from the command line: taskloom run order-prices.yaml.")
    action(win, "Save").trigger()
    QApplication.processEvents()
    p.check("The flow is saved", not tab.doc.dirty and GLOBAL_TYPE in flow.read_text(encoding="utf-8"))
    result = env.taskloom("run", flow)
    printed = (result.stdout + result.stderr).strip()  # (what happens in a run goes to stderr)
    p.text("taskloom run order-prices.yaml printed", printed)
    p.check("taskloom run ended with success", result.returncode == 0 and printed.splitlines()[-1].endswith(" success"))
    p.check(f"The two copies gave {TOTALS['discount']} and {TOTALS['discount_2']}, as in the editor",
            all(f"[{c}] success · total: {t}" in printed for c, t in TOTALS.items()))
