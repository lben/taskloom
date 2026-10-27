"""Proof: right-click a Python Code block to save it as a reusable block of your own (Create Reusable block…)."""

import textwrap

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QHelpEvent, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QLabel, QToolBar

from proofs.conftest import choose, menu_item, right_click
from taskloom.editor.canvas import HEADER_H, NODE_W
from taskloom.editor.panels import CodeEdit

CODE = ("# tidy the names: trimmed, capitalised, sorted\n"
        "tidy = sorted(n.strip().title() for n in names)\n"
        "print(len(tidy), 'names tidied')\n"
        "output = tidy")
NAMES = """
    blocks:
      start_by_hand:
        type: trigger.manual
        ui: {x: 0, y: 0}
      load:
        type: logic.python
        config: {inputs: [], outputs: [names], code: "names = ['  ana ', 'josé', 'Bo']"}
        ui: {x: 260, y: 0}
      tidy:
        type: logic.python
        config:
          inputs: [names]
          code: |-
%s
        ui: {x: 520, y: 0}
    edges:
      - start_by_hand.start -> load.after
      - load.names -> tidy.names
""" % "\n".join(" " * 12 + line for line in CODE.split("\n"))
# Another flow, with the same three names: to compare what the new block makes with what tidy makes.
STARTERS = """
    blocks:
      start_by_hand:
        type: trigger.manual
        ui: {x: 0, y: 0}
      starters:
        type: logic.python
        config: {inputs: [], outputs: [names], code: "names = ['  ana ', 'josé', 'Bo']"}
        ui: {x: 260, y: 0}
    edges:
      - start_by_hand.start -> starters.after
"""
TIDIED = "['Ana', 'Bo', 'José']"  # what tidy's code makes of those names
NAME = "Tidy names"
WHAT_IT_DOES = "Tidies a list of names: takes away the spaces around each name, capitalises it, and sorts them."
INPUTS_EXPECT = "names: a list of names, as text. Spaces around a name are fine."
TAKEN = "Wait"  # a block that comes with Taskloom


def write_flow(path, text):
    """A flow file, in UTF-8 as Taskloom reads it (its names have an é)."""
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def open_flow(win, monkeypatch, path):
    """File > Open…, choosing `path` in the file dialog: the tab it opens in."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    menu_item(file_menu, "Open…").trigger()
    QApplication.processEvents()
    return win.current


def run_now(win, qtbot, tab):
    """Press ▶ Run now in the toolbar and wait for the run to end: the run."""
    count = len(tab.runs)
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == count + 1 and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)
    return tab.runs[-1]


def shows(tab, block) -> tuple:
    """What the canvas shows of a block: its status and the result under it."""
    node = tab.scene.nodes[block]
    return node.status, node.status_text


def in_window(win, widget, rect: QRect) -> QRect:
    """`rect`, in `widget`'s coordinates, in a screenshot of the window."""
    return rect.translated(widget.mapTo(win, QPoint(0, 0)))


def tree_item(win, title: str):
    """The blocks tree's item called `title` (a block, or a folder: "User Created  (1)"); None if there is none."""
    return next(iter(win.palette_panel.tree.findItems(title, Qt.MatchStartsWith | Qt.MatchRecursive)), None)


def item_rect(win, item) -> QRect:
    tree = win.palette_panel.tree
    return in_window(win, tree.viewport(), tree.visualItemRect(item))


def plain(html_text: str) -> str:
    doc = QTextDocument()
    doc.setHtml(html_text)
    return doc.toPlainText()


def tooltip():
    return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)


def click_block(tab, block: str):
    """Click a block's title bar, as you would."""
    at = tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, tab.view.mapFromScene(at))
    QApplication.processEvents()


def drag_block(tab, block: str, to: QPointF):
    """Drag a block by its title bar so that its top-left corner is at `to`, on the canvas."""
    grab = QPointF(NODE_W / 2, HEADER_H / 2)
    a = tab.view.mapFromScene(tab.scene.nodes[block].mapToScene(grab))
    b = tab.view.mapFromScene(to + grab)
    viewport = tab.view.viewport()
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, a)
    for i in range(1, 6):
        QTest.mouseMove(viewport, a + (b - a) * i / 5)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, b)
    QApplication.processEvents()


def pan(tab, scene_point: QPointF):
    """Drag the canvas with the middle mouse button until `scene_point` is in the middle of it."""
    viewport = tab.view.viewport()
    a, b = tab.view.mapFromScene(scene_point), viewport.rect().center()
    QTest.mousePress(viewport, Qt.MiddleButton, Qt.NoModifier, a)
    for i in range(1, 6):
        QTest.mouseMove(viewport, a + (b - a) * i / 5)
    QTest.mouseRelease(viewport, Qt.MiddleButton, Qt.NoModifier, b)
    QApplication.processEvents()


def connect(tab, src: str, src_port: str, dst: str, dst_port: str):
    """Drag from an output (a block's right side) to an input (another's left side)."""
    viewport = tab.view.viewport()
    a = tab.view.mapFromScene(tab.scene.nodes[src].ports[(src_port, True)].scenePos())
    b = tab.view.mapFromScene(tab.scene.nodes[dst].ports[(dst_port, False)].scenePos())
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(viewport, (a + b) / 2)
    QTest.mouseMove(viewport, b)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, b)
    QApplication.processEvents()


def retype(box, text: str):
    """Select what a text box holds and type `text` over it, as you would."""
    box.setFocus()
    box.selectAll()
    QTest.keyClicks(box, text)


def test_a_python_code_block_saved_as_a_reusable_block(proof, env, win, qtbot, monkeypatch):
    p = proof("A Python Code block saved as a block of your own",
              "Python blocks should have a right click option added called: \"Create Reusable block\" which will "
              "open a window asking for the new User Block name (which will live under a new \"User Created\" "
              "category on the blocks tree), a help description for 1. what the block does overall and 2. what do "
              "the input(s) expect to receive")
    p.idea("Right-click a Python Code block and choose Create Reusable block…: it becomes a block of your own, "
           "listed at once under User Created in the blocks tree, to add to any flow.")
    p.idea("A window asks for its name (its title in the blocks tree), what it does, and what its inputs expect. "
           "Resting the mouse on it in the blocks tree shows both texts, and so do its Properties in a flow. A name "
           "another block already has, or one that would give the same file name, is refused: the window says why "
           "and stays open, to choose another.")
    p.idea("It is saved as one Python file in your blocks folder, in your Taskloom folder, named after it: Tidy names "
           "is tidy_names.py. The file holds the block's code, its inputs and outputs, and its Run in my Python "
           "environment choice. In a flow, the new block runs that code on what is connected to its inputs: in the "
           "editor, with taskloom run, and in the scheduler service. The block it was made from stays as it is.")
    p.why_simplest("Taskloom already had a place for blocks of your own: Python files in your blocks folder, which "
                   "the editor and taskloom run read (the scheduler service runs each flow with taskloom run). A "
                   "reusable block is one more file there, so it works wherever your flows run, with nothing new to "
                   "load it.")
    p.why_simplest("Inside, the new block is a Python Code block whose settings are fixed: the code, inputs and "
                   "outputs it was saved with. It runs exactly as the block it was made from, so there is no second "
                   "way of running code to keep in step with the first.")
    p.alternative("A list of reusable blocks kept in Taskloom's settings, or in a file of a new kind",
                  "The editor and taskloom run would each need new code to read it, and Taskloom would have two "
                  "ways of saying what a block is. The blocks folder is already read by both.")
    p.alternative("A new block that points to the block in its flow, instead of holding a copy of its code",
                  "It would change whenever that flow is edited, and break when the flow is moved or deleted, or on a "
                  "computer that does not have it. A file of its own stands alone.")
    p.alternative("Copying and pasting the block into each flow (which you could already do)",
                  "Each copy would be separate: no name in the blocks tree, no help, and a fix would have to be made "
                  "in every copy. A reusable block is one file: a change to it reaches every flow that uses it.")
    p.alternative("A Code box in the new block's Properties, to change it in each flow",
                  "Each flow would then have its own version, and it would no longer be one block that does one "
                  "thing. To change what it does, you edit its file (every flow that uses it gets the change), or "
                  "make a new one.")
    p.fits("The blocks tree already had a folder for the blocks in your blocks folder; it is now called User Created, "
           "as you asked, and the blocks you write by hand are there too.")
    p.fits("Every block in the list says what it does when you rest the mouse on it. A reusable block also says what "
           "its inputs expect, and its Properties show the same two texts, where other blocks show their settings.")
    p.fits("The window refuses a name the way Properties refuses a block name that is taken: it says why in red and "
           "lets you fix it.")
    names_file = write_flow(env.root / "names.yaml", NAMES)
    starters_file = write_flow(env.root / "new-starters.yaml", STARTERS)
    assert win.home.root == env.home  # the editor and the command line use the same Taskloom folder
    blocks_dir = win.home.blocks_dir

    p.step("Open the flow names.yaml (File > Open…) and press ▶ Run now. load gives a list of three names; tidy, a "
           "Python Code block, tidies them: no spaces around each name, capitalised, sorted.")
    tab = open_flow(win, monkeypatch, names_file)
    on_disk, data = names_file.read_bytes(), repr(tab.doc.data)
    run = run_now(win, qtbot, tab)
    p.shot(win, f"tidy made {TIDIED} of load's names. This is the block that will be saved as a block of your own.",
           highlight=["tidy"], zoom=["load", "tidy"])
    p.check(f"The run ended with success, and tidy made {TIDIED}",
            run.status == "success" and shows(tab, "tidy") == ("success", f"✓ output: {TIDIED}"))
    p.note("tidy runs in Taskloom's own Python (Run in my Python environment is unticked in its Properties), so this "
           "proof needs no Python environment of yours. The block made from it keeps that choice.")

    p.step("Right-click tidy.")
    seen = {}

    def fill_in(dialog):
        buttons = dialog.findChild(QDialogButtonBox)
        ok = buttons.button(QDialogButtonBox.Ok)
        retype(dialog.name, NAME)
        retype(dialog.what, WHAT_IT_DOES)
        retype(dialog.expects, INPUTS_EXPECT)
        p.shot(dialog, "① Its name, its title in the blocks tree; ② what it does; ③ what its inputs expect. The "
                       "window says it will be under User Created in the blocks tree.",
               highlight=[dialog.name, dialog.what, dialog.expects], badges=True)
        p.check("The window asks for the Name, What it does and What its inputs expect, and holds what was typed",
                (dialog.windowTitle(), dialog.name.text(), dialog.what.toPlainText(), dialog.expects.toPlainText())
                == ("Create Reusable block", NAME, WHAT_IT_DOES, INPUTS_EXPECT))

        p.step(f"To see a name already used refused, change Name to {TAKEN} and press OK. {TAKEN} is a block that "
               "comes with Taskloom.")
        retype(dialog.name, TAKEN)
        QTest.mouseClick(ok, Qt.LeftButton)
        QApplication.processEvents()
        p.shot(dialog, f"② It says why, in red: there is already a block called {TAKEN}. The window is still open, "
                       f"with ① the name to change and what you wrote below it.",
               highlight=[dialog.name, dialog.why], badges=True)
        p.check(f"{TAKEN} is refused: the window stays open and says there is already a block called {TAKEN}",
                dialog.isVisible() and dialog.why.isVisible()
                and dialog.why.text() == f"There is already a block called {TAKEN}: choose another name.")
        p.check("Nothing was saved: the blocks folder has no file",
                not blocks_dir.exists() or not list(blocks_dir.glob("*.py")))
        p.check("What it does and What its inputs expect still hold what you wrote",
                (dialog.what.toPlainText(), dialog.expects.toPlainText()) == (WHAT_IT_DOES, INPUTS_EXPECT))

        p.step(f"Change Name back to {NAME} and press OK.")
        retype(dialog.name, NAME)
        QTest.mouseClick(ok, Qt.LeftButton)
        seen["closed"] = not dialog.isVisible()

    def the_menu(menu):
        p.check("tidy's menu has Create Reusable block…",
                any(a.text() == "Create Reusable block…" and a.isEnabled() for a in menu.actions()))
        p.shot(menu, "Its menu: ① Create Reusable block… is the last item. Its … says it opens a window.", over=win,
               highlight=[menu_item(menu, "Create Reusable block…")], badges=True, zoom=[menu, "tidy"])
        p.step("Choose Create Reusable block…, and fill in the window that opens: Name, What it does, and What its "
               "inputs expect.")
        p.while_open(lambda: choose(menu, "Create Reusable block…"), fill_in)

    p.while_open(lambda: right_click(tab, "tidy"), the_menu)
    QApplication.processEvents()
    p.check("OK closed the window", seen.get("closed"))
    saved = blocks_dir / "tidy_names.py"
    folder, item = tree_item(win, "User Created"), tree_item(win, NAME)
    p.check(f"The blocks tree has User Created, with {NAME} in it",
            folder is not None and folder.text(0) == "User Created  (1)" and item is not None
            and item.parent() is folder and item.text(0) == NAME)
    tree = win.palette_panel.tree
    tree.scrollToItem(item)  # as you would, if the list is longer than the window
    QApplication.processEvents()
    p.shot(win, f"The blocks tree has a new folder, User Created, with {NAME} in it: at once, without starting the "
                f"editor again.", highlight=[[item_rect(win, folder), item_rect(win, item)]],
           zoom=[item_rect(win, tree_item(win, "Logic")), item_rect(win, item)])

    p.step(f"Rest the mouse on {NAME} in the blocks tree.")
    viewport = tree.viewport()
    at = tree.visualItemRect(item).center()
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, at, viewport.mapToGlobal(at)))
    qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
    tip = tooltip()
    shown = plain(tip.text())
    p.shot(tip, "It says what the block does, and what its inputs expect.", over=win, highlight=[tip],
           zoom=[tip, item_rect(win, item)])
    tip.hide()
    p.check("Its tooltip has its name, what it does, and what its inputs expect",
            shown.startswith(NAME) and WHAT_IT_DOES in shown and f"Its inputs: {INPUTS_EXPECT}" in shown)

    p.step("Open the file the status bar names: tidy_names.py, in the blocks folder of your Taskloom folder.")
    text = saved.read_text(encoding="utf-8")
    p.text("tidy_names.py, as it was saved", text)
    status = win.statusBar()
    p.shot(status, "The status bar, at the foot of the window, says where the block was saved: tidy_names.py, in the "
                   "blocks folder of the Taskloom folder (here a temporary one, for this proof).")
    p.check("The status bar says where it was saved",
            status.currentMessage() == f"{NAME} saved in {saved}: it is under User Created in the blocks tree")
    p.check("The blocks folder has one file, tidy_names.py", sorted(blocks_dir.glob("*.py")) == [saved])
    p.check("It has the block's name, what it does, what its inputs expect, and the category User Created",
            all(repr(s) in text for s in (NAME, WHAT_IT_DOES, INPUTS_EXPECT, "User Created")))
    p.check("Each line of tidy's code is on a line of its own, as written",
            all(f"            {line!r}," in text.split("\n") for line in CODE.split("\n")))
    p.note("Each line of the code is kept on a line of its own, as written, so the file is easy to read, and to "
           "edit.")

    p.step(f"Open another flow, new-starters.yaml (File > Open…), and add {NAME} from User Created: drag the canvas "
           f"to the left with the middle mouse button to make room, double-click {NAME}, drag the new block beside "
           f"starters, and drag from starters' names output to its names input.")
    other = open_flow(win, monkeypatch, starters_file)
    row = QRectF(0, 0, 520 + NODE_W, other.scene.nodes["starters"].sceneBoundingRect().height())
    pan(other, row.center())  # the room on the right of starters, where the new block goes
    item = tree_item(win, NAME)
    tree.scrollToItem(item)
    QApplication.processEvents()
    at = tree.visualItemRect(item).center()
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, at)  # a double-click begins with a click
    QTest.mouseDClick(viewport, Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()
    p.check(f"Double-clicking {NAME} adds a {NAME} block to the flow, named tidy_names",
            other.doc.blocks.get("tidy_names", {}).get("type") == "user.tidy_names")
    drag_block(other, "tidy_names", QPointF(520, 0))
    assert (other.scene.nodes["tidy_names"].pos(), other.scene.nodes["starters"].pos()) == (QPointF(520, 0),
                                                                                           QPointF(260, 0))
    connect(other, "starters", "names", "tidy_names", "names")
    p.shot(win, f"The new block, tidy_names, in new-starters.yaml, connected from starters. At its foot, {NAME}: the "
                f"name you gave it.", highlight=["tidy_names"], zoom=["starters", "tidy_names"])
    p.check("starters' names output is connected to its names input",
            ("starters", "names", "tidy_names", "names") in other.doc.edges())

    p.step("Click the new block, tidy_names, to see its Properties.")
    click_block(other, "tidy_names")
    body = win.properties.widget()
    labels = {w.text(): w for w in body.findChildren(QLabel) if w.isVisible()}
    what = [labels.get("<b>What it does</b>"), labels.get(WHAT_IT_DOES)]
    expects = [labels.get("<b>What its inputs expect</b>"), labels.get(INPUTS_EXPECT)]
    p.check("Its Properties show What it does and What its inputs expect, with what you wrote",
            None not in what + expects)
    p.shot(win, "In its Properties, ① what it does and ② what its inputs expect, as you wrote them. There is no Code "
                "box: it runs the code it was saved with.",
           highlight=[what, expects], badges=True, zoom=[*what, *expects])
    p.check("Its Properties have no Code box", body.findChildren(CodeEdit) == [])
    p.note("To change what it does, edit its file, tidy_names.py, or make a new block from a Python Code block: every "
           "flow that uses it then runs what the file says.")
    p.check("It has tidy's input and output: names in, output out",
            (other.scene.nodes["tidy_names"].inputs, other.scene.nodes["tidy_names"].outputs)
            == (tab.scene.nodes["tidy"].inputs, tab.scene.nodes["tidy"].outputs)
            and "names" in other.scene.nodes["tidy_names"].inputs)

    p.step("Press ▶ Run now. It saves the flow, then runs it.")
    run = run_now(win, qtbot, other)
    p.shot(win, f"tidy_names made {TIDIED} of starters' names: the same as tidy made in names.yaml (step 1).",
           highlight=["tidy_names"], zoom=["starters", "tidy_names"])
    p.check("The run ended with success", run.status == "success")
    p.check(f"tidy_names made {TIDIED}, as tidy did",
            shows(other, "tidy_names") == ("success", f"✓ output: {TIDIED}") == shows(tab, "tidy"))
    p.check("What its code printed is in the Log: 3 names tidied", "3 names tidied" in win.log.text.toPlainText())

    p.step("Go back to names.yaml (its tab, above the canvas) and click tidy.")
    bar = win.tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.tabs.indexOf(tab)).center())
    QApplication.processEvents()
    click_block(tab, "tidy")
    (code,) = win.properties.widget().findChildren(CodeEdit)
    lines = [(code, line) for line in CODE.split("\n")]  # each line of its code
    p.shot(win, "① tidy is as it was: a Python Code block, ② with its code in its Code box.",
           highlight=["tidy", lines], badges=True, zoom=["tidy", *lines])
    p.check("tidy still has its code", code.toPlainText() == CODE)
    p.check("names.yaml is unchanged, in the editor and on disk",
            repr(tab.doc.data) == data and names_file.read_bytes() == on_disk and not tab.doc.dirty)

    p.step("Run new-starters.yaml again, from the command line: taskloom run new-starters.yaml.")
    result = env.taskloom("run", starters_file)
    printed = (result.stdout + result.stderr).strip()  # (what happens in a run goes to stderr)
    p.text("taskloom run new-starters.yaml printed", printed)
    p.check("taskloom run ended with success",
            result.returncode == 0 and printed.splitlines()[-1].endswith(" success"))
    p.check(f"tidy_names made {TIDIED} there too", f"[tidy_names] success · output: {TIDIED}" in printed)
    p.note("taskloom run found the block in the same blocks folder. The scheduler service runs each flow with "
           "taskloom run, so a scheduled run finds it too. A flow that uses it on another computer needs "
           "tidy_names.py in that computer's blocks folder too: without it, Taskloom there does not know the block, "
           "and does not run the flow.")
