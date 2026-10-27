"""Proof: a new Python Code block runs in your Python environment and starts with an example of its inputs; a block
that is not new (in a flow file, or copied and pasted) keeps the settings it has."""

import sys

import yaml
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QFileDialog, QLabel, QToolBar

from proofs.conftest import choose, menu_item
from taskloom.blocks.logic import PythonCode
from taskloom.editor.canvas import HEADER_H, NODE_W
from taskloom.editor.panels import CodeEdit
from taskloom.pyenv import env_dir
from tests.test_python_new_block import ENV_BOX, environment_box

TRY_IT = """\
name: try-it
blocks:
  start:
    type: trigger.manual
    ui: {x: 0, y: 0}
  show:
    type: logic.inspect
    ui: {x: 640, y: 0}
edges: []
"""
# Written as a flow file was before this change: it does not say where its Python Code block runs.
OLDER = """\
name: older
blocks:
  start:
    type: trigger.manual
    ui: {x: 0, y: 0}
  where_it_ran:
    type: logic.python
    config:
      inputs: []
      code: |-
        import sys
        print("This ran in", sys.prefix)
        output = "done"
    ui: {x: 320, y: 0}
edges:
  - start.start -> where_it_ran.after
"""


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def menu_of(win, title: str):
    """A menu of the menu bar (e.g. "&File")."""
    return next(a.menu() for a in win.menuBar().actions() if a.text() == title)


def open_flow(win, monkeypatch, path):
    """File > Open…, choosing `path` in the file dialog: the tab it opens in."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    menu_item(menu_of(win, "&File"), "Open…").trigger()
    QTest.qWait(50)
    return win.current


def from_menu(p, win, title: str, item: str, then_in=None):
    """Open a menu of the menu bar under its name and choose `item`; then_in(dialog): what to do in the window that
    item opens, if it opens one."""
    menu = menu_of(win, title)
    bar = win.menuBar()
    at = bar.mapToGlobal(bar.actionGeometry(menu.menuAction()).bottomLeft())

    def then(opened):
        if then_in is None:
            choose(opened, item)
        else:
            p.while_open(lambda: choose(opened, item), then_in)

    p.while_open(lambda: menu.exec(at), then)
    QTest.qWait(50)


def on_screen(tab, scene_point: QPointF):
    return tab.view.mapFromScene(scene_point)


def click_block(tab, block: str):
    """Click a block's title bar, as you would."""
    at = tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, on_screen(tab, at))
    QTest.qWait(50)


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
    QTest.qWait(50)


def drag_port_to_port(tab, src: str, src_port: str, dst: str, dst_port: str):
    """Drag from an output (a block's right side) to an input (another's left side): a connection."""
    viewport = tab.view.viewport()
    a = on_screen(tab, tab.scene.nodes[src].ports[(src_port, True)].scenePos())
    b = on_screen(tab, tab.scene.nodes[dst].ports[(dst_port, False)].scenePos())
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(viewport, (a + b) / 2)
    QTest.mouseMove(viewport, b)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, b)
    QTest.qWait(50)


def run_now(win, qtbot, tab, n: int):
    """Press ▶ Run now in the toolbar and wait until the tab's n-th run has finished: that run."""
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == n and not tab.running and tab.runs[-1].status is not None,
                    timeout=120_000)
    return tab.runs[-1]


def environment_setting(win):
    """The selected block's "Run in my Python environment" in Properties: its name and its box."""
    label = next(w for w in win.properties.findChildren(QLabel) if w.text() == ENV_BOX)
    return [label, environment_box(win)]


def block_lines(text: str, block: str) -> str:
    """The lines of a flow file that are the block `block`: its name and what is indented under it."""
    lines = text.splitlines()
    first = lines.index(f"  {block}:")
    under = range(first + 1, len(lines))
    last = next((i for i in under if lines[i] and not lines[i].startswith("   ")), len(lines))
    return "\n".join(lines[first:last])


def status(tab, block) -> str | None:
    return tab.scene.nodes[block].status


def test_a_new_python_block_runs_in_your_environment_and_starts_with_an_example(proof, win, qtbot, tmp_path,
                                                                               monkeypatch):
    p = proof("A new Python Code block runs in your Python environment, and starts with an example",
              "Python blocks should have “Run in my Python environment” enabled by default. ... Should come with a "
              "commented out small python snippet with comments and examples that show the input(s) format (the "
              "dictionary).")
    p.idea("A Python Code block you add from the Blocks list now starts with Run in my Python environment ticked, "
           "and with a short example as its code: comments that show what your code receives and how it passes a "
           "value on.")
    p.idea("The example shows each input as a variable named as in Inputs (input, unless you rename it); several "
           "blocks connected into it as a dictionary keyed by their names, e.g. input[\"load_sales\"]; a table as a "
           "pandas DataFrame (in your environment); the flow's parameters; print() writing to the Log; and setting "
           "output. It is only comments: as it is, the block runs, does nothing, and passes nothing on.")
    p.idea("Both are saved in the flow file, like any setting you choose. A Python Code block that is not new keeps "
           "the settings it has: one in a flow file you open, or one you copy and paste. A flow file that does not "
           "say where its block runs keeps running it in Taskloom's own Python, as before, so no flow you already "
           "have changes when you upgrade.")
    p.why_simplest("A new block takes its first settings from one place: the Python Code block itself says what a "
                   "block added from the Blocks list starts with. Double-clicking it in the list and dragging it onto "
                   "the canvas both use that. Everything else takes a block as it is: opening a file, pasting, undo "
                   "and redo.")
    p.why_simplest("The setting itself is unchanged: a block runs in your environment when its flow file says so "
                   "(use_environment: true), and in Taskloom's own Python when the file leaves it out, as before. A "
                   "new block simply has it written in, as when you tick the box yourself.")
    p.why_simplest("The example is plain comments in the Code box: it is saved with the block, you read it where you "
                   "write your code, and you keep or delete it as you like. Being only comments, it cannot fail.")
    p.alternative("Tick it for every Python Code block, also those already in flow files",
                  "Every flow you already have would move to your environment the day you upgrade. On a computer "
                  "with no environment it would fail, and code that uses the helpers only Taskloom's own Python "
                  "gives it would break. With only new blocks changing, nothing you already have does.")
    p.alternative("Make ticked the default, and write the setting, unticked, into your flow files when you upgrade",
                  "It would change files you never touched, also the flows the scheduler runs. And a flow file "
                  "written by hand or by a script that leaves the setting out would then need an environment to "
                  "run. Only the editor knows that a block is new, so the editor is where it is ticked.")
    p.alternative("Show the example as grey hint text in an empty Code box",
                  "Hint text disappears as soon as you type, cannot be copied from, and is not in the flow file. You "
                  "asked for a commented-out snippet in the code, to read and copy from while you write.")
    p.fits("The box and the line in the flow file are the ones Python Code blocks already had: ticking the box by "
           "hand writes the same line. Your environment is the one Tools > Python environment creates (as "
           "taskloom env create does), and a block with the box ticked runs there, in its own process.")
    p.fits("On a computer with no Python environment yet, a new block needs one first, or its box unticked. Run "
           "without one, it fails, and its message says to create one under Tools > Python environment. This "
           "proof shows that message, then creates the environment.")
    p.fits("The example says what really arrives: several blocks connected into one input give a dictionary keyed by "
           "their names, and in your environment every table is a pandas DataFrame. It uses only what your code has "
           "wherever it runs (its inputs, params, print(), output), none of the extras of Taskloom's own Python.")
    p.fits("Copy and paste works as for every block: the copy has the settings of the block it was copied from.")
    try_it = write(tmp_path / "try-it.yaml", TRY_IT)
    older = write(tmp_path / "older.yaml", OLDER)

    # --- a new block ---------------------------------------------------------------------------------------------
    p.step("Open try-it.yaml (File > Open…) and double-click Python Code in the Blocks list, on the left. "
           "try-it.yaml has a Manual Start block, start, and an Inspect block, show, which shows whatever reaches it.")
    tab = open_flow(win, monkeypatch, try_it)
    tree = win.palette_panel.tree
    item = tree.findItems("Python Code", Qt.MatchExactly | Qt.MatchRecursive)[0]
    tree.scrollToItem(item)
    at = tree.visualItemRect(item).center()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # a double-click: Qt's test one is only
    QTest.mouseDClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # its second click
    QTest.qWait(50)
    p.check("A Python Code block, python, is added to the flow",
            set(tab.doc.blocks) == {"start", "show", "python"}
            and tab.doc.blocks["python"]["type"] == "logic.python")
    drag_block(tab, "python", QPointF(320, 0))
    click_block(tab, "python")
    setting, code = environment_setting(win), win.properties.findChild(CodeEdit)
    p.shot(win, "① The new block, python (dragged between start and show), selected. In Properties: ② Run in my "
                "Python environment is ticked, and ③ the Code box holds the example, only comments: what each input "
                "is, the dictionary that several connected blocks give, a table as a pandas DataFrame, params, print() "
                "and output.",
           highlight=["python", setting, code], badges=True, zoom=[setting, code])
    p.check("② Run in my Python environment is ticked", environment_box(win).isChecked())
    example = code.toPlainText()
    p.check("③ The Code box holds the example, and it is only comments",
            example == PythonCode.new_config["code"]
            and all(line.lstrip().startswith("#") for line in example.splitlines()))
    p.check("The example shows a dictionary of several blocks keyed by their names, then input[\"load_sales\"]",
            '{"load_sales": ' in example and 'input["load_sales"]' in example)

    p.step("Connect it: drag from start's output to python's run-after input, then from python's output to show's "
           "input.")
    drag_port_to_port(tab, "start", "start", "python", "after")
    drag_port_to_port(tab, "python", "output", "show", "value")
    line = ["start", "python", "show"]
    p.shot(win, "① start now runs python, and python's output goes into show.", highlight=[line], badges=True,
           zoom=[line])
    p.check("① start runs python, and python's output goes to show",
            sorted(tab.doc.edges()) == [("python", "output", "show", "value"), ("start", "start", "python", "after")])

    p.step("Save the flow (File > Save).")
    from_menu(p, win, "&File", "Save")
    saved = try_it.read_text(encoding="utf-8")
    lines = block_lines(saved, "python")
    p.note("Taskloom has no window that shows a flow file's text, so the block's lines are shown here as the file "
           "has them, read from it once saved.")
    p.text("The new block, python, in try-it.yaml. The file keeps the code as one quoted text, with a blank line "
           "between its lines (that is how it writes text of several lines); it reads back as the same lines.", lines)
    flows = win.tabs.tabBar()
    p.shot(flows, "① The flow's tab, above the canvas, says try-it.yaml, without the * it had before: the flow is "
                  "saved.", highlight=[flows.tabRect(win.tabs.indexOf(tab))], badges=True)
    kept = (yaml.safe_load(saved)["blocks"]["python"] or {}).get("config") or {}
    p.check("① try-it.yaml is saved", not tab.doc.dirty and win.tabs.tabText(win.tabs.indexOf(tab)) == "try-it.yaml")
    p.check("The flow file says the block runs in your Python environment: use_environment: true",
            kept.get("use_environment") is True and "use_environment: true" in lines)
    p.check("The flow file has the example as the block's code, as the Code box shows it",
            kept.get("code") == example)

    # --- run as it is: first with no environment, then in one --------------------------------------------------
    p.step("Press ▶ Run now. There is no Python environment on this computer yet.")
    first = run_now(win, qtbot, tab, 1)
    win.log.text.verticalScrollBar().setValue(0)  # scrolled up to the run's first lines
    said = (win.log.text, "failed: FileNotFoundError: this block runs in your Python environment, but there is none "
                          "yet: create it under Tools → Python environment")
    p.shot(win, "① python failed: it runs in your Python environment, and there is none yet. ② Its message, first in "
                "the Log (which shows python's lines, as python is selected), says where to create one: under Tools → "
                "Python environment. show did not run.",
           highlight=["python", said], badges=True, zoom=[said])
    p.check("① The run failed at python, and show did not run",
            first.status == "failed" and status(tab, "python") == "failed" and status(tab, "show") == "skipped")
    p.check("② Its message says there is no Python environment yet, and to create one under Tools → Python "
            "environment", said[1] in win.log.text.toPlainText())

    p.step("Open Tools > Python environment…, type the Python to make it from, and press Create environment.")
    p.note("On your computer you can leave the Python box empty: Taskloom finds one. Here it is given the Python this "
           "proof itself runs with, so it does not depend on which other Pythons the computer has. It is created in "
           "this proof's own Taskloom folder.")
    made = []

    def create_environment(dialog):
        dialog.python.clear()
        QTest.keyClicks(dialog.python, sys.executable)
        QTest.mouseClick(dialog.create_button, Qt.LeftButton)
        output = dialog.output.toPlainText
        qtbot.waitUntil(lambda: dialog.proc is None
                        and ("failed (exit code" in output() or output().count("done") >= 2),
                        timeout=300_000)  # it is made, then its packages are listed
        made.append((dialog.status.text(), output()))
        p.shot(dialog, "① It says In use: the environment, in this Taskloom folder, with its Python version. ② "
                       "Below, what it ran: taskloom env create, then the list of its packages.",
               highlight=[dialog.status, (dialog.output, "$ taskloom env create")], badges=True)
        QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Close), Qt.LeftButton)

    from_menu(p, win, "&Tools", "Python environment…", then_in=create_environment)
    (shown, ran), = made
    p.check("① The environment is in use, in this proof's Taskloom folder",
            shown.startswith("<b>In use:</b>") and str(env_dir(win.home)) in shown)
    p.check("② It was made with taskloom env create, and it worked", "$ taskloom env create" in ran
            and "failed" not in ran)

    p.step("Press ▶ Run now again, with the block's code as it is.")
    second = run_now(win, qtbot, tab, 2)
    p.shot(win, "① python ran in your environment, which it could not do in step 4, and succeeded: it is green. ② "
                "show was skipped: nothing reached its input.",
           highlight=["python", "show"], badges=True, zoom=["python", "show"])
    p.check("① python succeeded", status(tab, "python") == "success")
    p.check("② show was skipped: “nothing reached its 'value' input”",
            tab.scene.statuses.get("show", (None, ""))[:2]
            == ("skipped", "skipped · nothing reached its 'value' input"))
    ended = f"DONE run #{second.run_id} success"
    qtbot.waitUntil(lambda: ended in win.log.text.toPlainText(), timeout=30_000)
    ran_ok = (win.log.text, f"run #{second.run_id} ")
    p.shot(win, f"③ The Log, under the canvas: run #{second.run_id} started, and ended with success.",
           highlight=[ran_ok], badges=[3], zoom=[ran_ok])
    p.check(f"③ The Log says run #{second.run_id} started and ended with success",
            f"run #{second.run_id} · " in win.log.text.toPlainText() and ended in win.log.text.toPlainText()
            and second.status == "success")

    p.step("Click the Data preview tab, under the canvas. python is still selected.")
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.bottom_tabs.indexOf(win.preview)).center())
    p.shot(win, "① Data preview says what python produced in the run: nothing. It succeeded, with no output.",
           highlight=[win.preview.label], badges=True, zoom=[win.preview.label, bar])
    p.check("① Data preview says “python: success, no output”",
            win.preview.label.text() == "python: success, no output")

    # --- what is not new keeps its settings ----------------------------------------------------------------------
    p.step("Open older.yaml, a flow file that does not say where its Python Code block runs, and click that block, "
           "where_it_ran.")
    p.note("older.yaml is written as flow files were before this change: its block says nothing about where it runs, "
           "like the Python Code blocks in your flow files, unless you ticked their box.")
    tab = open_flow(win, monkeypatch, older)
    click_block(tab, "where_it_ran")
    setting, code = environment_setting(win), win.properties.findChild(CodeEdit)
    p.shot(win, "① Run in my Python environment is not ticked: the file does not say so. ② Its code is its own: it "
                "prints which Python it runs in.",
           highlight=[setting, code], badges=True, zoom=[setting, code])
    p.check("① Run in my Python environment is not ticked", not environment_box(win).isChecked())
    p.check("② Its code is the one in the file", code.toPlainText().startswith("import sys"))

    p.step("Press ▶ Run now.")
    third = run_now(win, qtbot, tab, 1)
    where = f"This ran in {sys.prefix}"
    printed = (win.log.text, where)
    p.shot(win, "① where_it_ran printed where it ran: in the folder of the Python Taskloom itself runs with, not in "
                "your environment, python-env, though there is one now. This flow runs as it did before the upgrade.",
           highlight=[printed], badges=True, zoom=[printed])
    p.check(f"Run #{third.run_id} succeeded", third.status == "success" and status(tab, "where_it_ran") == "success")
    p.check("① It ran in Taskloom's own Python, not in your environment",
            where in win.log.text.toPlainText() and str(env_dir(win.home)) not in win.log.text.toPlainText())

    p.step("Copy where_it_ran and paste it (Edit > Copy, then Edit > Paste), and drag the copy under it.")
    from_menu(p, win, "&Edit", "Copy")
    from_menu(p, win, "&Edit", "Paste")
    (pasted,) = set(tab.doc.blocks) - {"start", "where_it_ran"}
    drag_block(tab, pasted, QPointF(320, 170))
    click_block(tab, pasted)
    setting, code = environment_setting(win), win.properties.findChild(CodeEdit)
    p.shot(win, f"① The copy, {pasted}, is not connected yet, so it is off. In Properties, it has the settings of "
                "where_it_ran: ② Run in my Python environment is not ticked, and ③ its code is where_it_ran's, not the "
                "example. It is a copy, not a new block.",
           highlight=[pasted, setting, code], badges=True, zoom=[setting, code])
    p.check(f"② {pasted}'s Run in my Python environment is not ticked", not environment_box(win).isChecked())
    p.check(f"③ {pasted} has where_it_ran's settings as they are: its code, and nothing about where it runs",
            tab.doc.blocks[pasted]["config"] == yaml.safe_load(OLDER)["blocks"]["where_it_ran"]["config"]
            and "use_environment" not in tab.doc.blocks[pasted]["config"])
    for each in win.tabs_list():  # the copy was pasted only to be shown: closing the window must not ask to save it
        each.doc.dirty = False
