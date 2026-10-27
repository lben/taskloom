"""Proof: the done diamond only on the blocks whose outputs may not arrive (If, Python Code, reusable blocks); the
others, such as Run Query and Chart, connect an output to the next block's run after. See README.md here."""

import shutil

import yaml
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QToolBar

from conftest import REPO
from taskloom.editor.canvas import HEADER_H, NODE_W, PORT_R
from taskloom.editor.panels import CodeEdit
from tests.test_done_where_needed import DONE_TIP, RUN_AFTER_TIP, plain
from tests.test_editor import drag_port_to_port
from tests.test_lines import shown, wait_runs

# Taken as this file is imported, before the win fixture answers message boxes itself: Run now's box is then shown
# as in the app.
REAL_WARNING = QMessageBox.warning
ASKED = ("I don't think there is a need for a done diamond shaped output on some blocks I see like the Run Query or "
         "the Chart blocks, a \"next-step\" block can be connected from the result and just ignore the input no? Or "
         "is there a valid reason for it to exist? — Should I remove it from blocks that always produce an output, "
         "like Run Query and Chart? — yes")

LOOK = "which-blocks-have-done.yaml"
LOOK_FLOW = {  # on the left two blocks that lose done, on the right two that keep it, in the middle a next step
    "blocks": {
        "sales_query": {"type": "db.run_query", "config": {"sql": "SELECT region, amount FROM sales"},
                        "ui": {"x": 0, "y": 0}},
        "sales_chart": {"type": "reports.chart", "config": {"kind": "bar", "x": "region", "y": ["amount"]},
                        "ui": {"x": 0, "y": 170}},
        "next_step": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "print('next')"},
                      "ui": {"x": 240, "y": 85}},
        "tidy_up": {"type": "logic.python",
                    "config": {"inputs": ["input"], "outputs": ["output"], "code": "output = input"},
                    "ui": {"x": 480, "y": 0}},
        "any_rows": {"type": "logic.if", "config": {"condition": "len(value) > 0"}, "ui": {"x": 480, "y": 170}},
    },
    "edges": [],
}
LOSE, KEEP = ["sales_query", "sales_chart"], ["tidy_up", "any_rows"]  # highlighted as ①, ② and ③, ④

NEXT_CODE = """\
# read's table goes into this block's run after:
# it runs after read, and is not given the table.
if "after" in globals():
    print("next_step was given read's table")
else:
    print("read is done; next_step runs without its table")"""
RAN_AFTER = "read is done; next_step runs without its table"
IN_ORDER = "in-order.yaml"
OLD = "old-flow.yaml"  # in-order.yaml as it was written before this change: read's done into next_step's run after
STALE = "edge read.done -> next_step.after: 'read' has no output 'done'"


def in_order_flow(order_from: str) -> dict:
    """start, then read (Read File), then next_step (Python Code), which runs after read's output `order_from`."""
    return {
        "blocks": {
            "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
            "read": {"type": "data.read_file", "config": {"path": "data/sales.csv"}, "ui": {"x": 240, "y": 0}},
            "next_step": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": NEXT_CODE},
                          "ui": {"x": 480, "y": 0}},
        },
        "edges": ["start.start -> read.after", f"read.{order_from} -> next_step.after"],
    }


def open_flow(win, monkeypatch, path):
    """File > Open…, choosing `path` in the file dialog: the tab it opens in."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    QTest.qWait(100)
    return win.current


def press_run_now(win):
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)


def click_block(tab, block: str):
    """Click a block's title bar, as you would: Properties shows its settings."""
    at = tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, tab.view.mapFromScene(at))
    QApplication.processEvents()


def rows(win, tab, block: str, names: list[str], output: bool = True) -> QRect:
    """Where these rows of a block's outputs (or inputs) are in a screenshot of the window: their names and their
    circles or diamonds, so that the outline around them clears the rows next to them."""
    node = tab.scene.nodes[block]
    area = QRectF()
    for name in names:
        y = node.ports[(name, output)].pos().y()
        left = NODE_W / 2 if output else -PORT_R - 2
        area |= QRectF(left, y - 5, NODE_W / 2 + PORT_R + 2, 10)
    on_view = tab.view.mapFromScene(node.mapToScene(area)).boundingRect()
    return on_view.translated(tab.view.viewport().mapTo(win, QPoint(0, 0)))


def lines_drawn(tab) -> list[str]:
    """The connections the canvas draws, as their text (e.g. read.table -> next_step.after)."""
    return [edge.text for edge in tab.scene.edge_items]


def hover(tab, item, qtbot):
    """Rest the mouse on a canvas item (a block's circle or diamond): the tooltip it shows."""
    viewport = tab.view.viewport()
    at = tab.view.mapFromScene(item.sceneBoundingRect().center())
    QApplication.sendEvent(viewport, QHelpEvent(QHelpEvent.ToolTip, at, viewport.mapToGlobal(at)))
    qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
    return tooltip()


def tooltip():
    return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)


def terminal(env, *args):
    """`taskloom ARGS` in a terminal, in the folder of the flows: (what it printed, its exit code)."""
    result = env.taskloom(*args, encoding="utf-8", errors="replace")
    return (result.stdout + result.stderr).strip(), result.returncode


def runs_recorded(env) -> int:
    return env.query("SELECT count(*) FROM runs")[0][0]


# `env` before `win`: its Taskloom folder is the editor's (the same temporary one), and it is made first.
def test_the_done_diamond_only_where_a_blocks_output_may_not_arrive(proof, env, win, qtbot, monkeypatch):
    p = proof("The done diamond only where a block's output may not arrive", ASKED)
    p.idea("Run Query, Chart and every other block whose output always arrives no longer have a done diamond. To "
           "run a next step after one of them, connect its output (Run Query's result, Chart's image) to the next "
           "block's run after diamond: the next block waits for it and does not use its value.")
    p.idea("The done diamond stays where it has a job: on If, on Python Code and the blocks you saved from a Python "
           "Code block (Create Reusable block), and on a block that has no outputs at all. If gives true or false, "
           "never both, and Python Code may set none of its outputs: done fires whenever they succeed, so a next "
           "step can come after them whatever they gave.")
    p.idea("22 blocks lose it. Among them are Write File and Send Email: they too always give their output (the "
           "file, and sent), so that is what goes into the next block's run after.")
    p.why_simplest("Run after already took any output, so running a block after Run Query or Chart needed nothing "
                   "new. What changed is which blocks show done: a block has it when it says it may succeed without "
                   "giving its outputs (only If and Python Code say so, and the blocks saved from Python Code inherit "
                   "it), or when it has no outputs to connect. Start blocks have neither done nor run after, as "
                   "before.")
    p.why_simplest("One rule decides both where the canvas draws done and when a run fires it, so the two can never "
                   "disagree: a done you can connect always fires when its block succeeds.")
    p.alternative("Keep done on every block, as before",
                  "On Run Query, done and result arrive at the same moment, every time: two connectors for one "
                  "event, and a choice between them that makes no difference. That is the clutter you saw.")
    p.alternative("Mark each output that always arrives, block by block",
                  "Taskloom could then work out where done is needed from the outputs. But each output of the 22 "
                  "blocks would carry a new mark, and describing an output would gain a new setting for everyone "
                  "who writes blocks, to reach the answer one setting on two blocks gives: a block either may finish "
                  "without its outputs, or it may not.")
    p.alternative("Take done away from every block",
                  "A Python Code block with no outputs would leave nothing to connect, and one whose code may set "
                  "none of its outputs could not always be waited for. An If would need both true and false "
                  "connected to say “after the If, whichever way it went”.")
    p.fits("Run after takes any output, as it did: a start block's start, an If's true, a table, a chart. The next "
           "block waits for what is connected to it, and does not use its value: a Python Code block gets no "
           "variable for it.")
    p.fits("Flows already written that connect a done that is gone stop, with the problem named: taskloom validate "
           "and taskloom run end with exit code 2 before any run starts, Run now in the editor lists it under “The "
           "flow has problems”, and a scheduled run fails the same way. Nothing rewires it for you: Tools > Check "
           "flows (also offered the first time this version starts) and taskloom check offer only to remove that "
           "connection, after a backup. Connect the block's output to the run after instead.")
    p.fits("Re-running one block, or from one block on, with an earlier run's inputs also works when what reached "
           "its run after was a password (as Ask User's answer can be): it was not kept, but its value is not used "
           "there. The deploy-runner example that comes with Taskloom now orders its steps through stopped, files, "
           "folder and pid.")

    assert win.home.root == env.home, "the editor and the command line use the same Taskloom folder"
    env.env["PYTHONIOENCODING"] = "utf-8"  # what taskloom prints, as a terminal shows it (on Windows too)
    shutil.copytree(REPO / "examples" / "data", env.root / "data")  # sales.csv, for Read File
    for name, data in ((LOOK, LOOK_FLOW), (IN_ORDER, in_order_flow("table")), (OLD, in_order_flow("done"))):
        (env.root / name).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    # --- which blocks have done -------------------------------------------------------------------------------------
    p.step(f"Open the flow {LOOK} (File > Open…). On the left, a Run Query block and a Chart block; on the right, a "
           "Python Code block and an If; in the middle, next_step, a Python Code block to run after them.")
    tab = open_flow(win, monkeypatch, env.root / LOOK)
    nodes = tab.scene.nodes  # (built again each time the flow changes)
    p.check("The flow opens", tab is not None and tab.doc.path == (env.root / LOOK).resolve())
    outputs = [rows(win, tab, block, nodes[block].outputs) for block in LOSE + KEEP]
    p.shot(win, "The outputs, on the right side of each block. ① sales_query (Run Query) gives result and error, and "
                "② sales_chart (Chart) gives image and error: no done diamond. ③ tidy_up (Python Code) and ④ any_rows "
                "(If) still have done, first, drawn as a diamond.",
           highlight=outputs, badges=True, zoom=list(LOOK_FLOW["blocks"]))
    p.check("① sales_query (Run Query) has no done: its outputs are result and error",
            nodes["sales_query"].outputs == ["result", "error"] and ("done", True) not in nodes["sales_query"].ports)
    p.check("② sales_chart (Chart) has no done: its outputs are image and error",
            nodes["sales_chart"].outputs == ["image", "error"] and ("done", True) not in nodes["sales_chart"].ports)
    p.check("③ tidy_up (Python Code) has done first, then output and error",
            nodes["tidy_up"].outputs == ["done", "output", "error"])
    p.check("④ any_rows (If) has done first, then true, false and error",
            nodes["any_rows"].outputs == ["done", "true", "false", "error"])
    p.check("Each of them, and next_step, still has its run after diamond, first among its inputs",
            all(node.inputs[0] == "after" for node in nodes.values()))

    # --- an output into the next step's run after -------------------------------------------------------------------
    p.step("Drag from sales_query's result to next_step's run after, then from sales_chart's image to the same run "
           "after.")
    drag_port_to_port(tab, "sales_query", "result", "next_step", "after")
    QApplication.processEvents()
    drag_port_to_port(tab, "sales_chart", "image", "next_step", "after")
    QApplication.processEvents()
    p.shot(win, "Two lines go into ③ next_step's run after diamond: from ① sales_query's result and from ② "
                "sales_chart's image. next_step will run after both, and use neither.",
           highlight=[rows(win, tab, "sales_query", ["result"]), rows(win, tab, "sales_chart", ["image"]),
                      rows(win, tab, "next_step", ["after"], output=False)],
           badges=True, zoom=["sales_query", "sales_chart", "next_step"])
    p.check("The flow has the two connections, into next_step's run after",
            tab.doc.edges() == [("sales_query", "result", "next_step", "after"),
                                ("sales_chart", "image", "next_step", "after")])
    p.check("The canvas draws both", lines_drawn(tab) == ["sales_query.result -> next_step.after",
                                                         "sales_chart.image -> next_step.after"])
    p.note("To run a block after Write File or Send Email, connect its file, or its sent, the same way.")

    # --- what run after and done say --------------------------------------------------------------------------------
    p.step("Rest the mouse on next_step's run after diamond.")
    tip = hover(tab, tab.scene.nodes["next_step"].ports[("after", False)], qtbot)
    said = plain(tip.text())
    p.shot(tip, "It says what run after does: connect an output of another block (or a start block's start) to make "
                "this block wait for it; its value is not used. Below, what is connected: sales_query's result and "
                "sales_chart's image (nothing arrived yet: the flow has not run).",
           over=win, highlight=[tip], zoom=[tip, "next_step"])
    tip.hide()
    p.check("The tooltip says: “" + RUN_AFTER_TIP + "”", RUN_AFTER_TIP in said)
    p.check("It lists what is connected there: sales_query.result and sales_chart.image",
            "from sales_query.result" in said and "from sales_chart.image" in said)

    p.step("Rest the mouse on tidy_up's done diamond.")
    tip = hover(tab, tab.scene.nodes["tidy_up"].ports[("done", True)], qtbot)
    said = plain(tip.text())
    p.shot(tip, "Why Python Code keeps done: it fires when the block has finished successfully, even if it produced "
                "none of its other outputs.", over=win, highlight=[tip], zoom=[tip, "tidy_up"])
    tip.hide()
    p.check("The tooltip says: “" + DONE_TIP + "”", DONE_TIP in said)

    # --- a run: the next step runs after Read File's table, without it ----------------------------------------------
    p.step(f"Open {IN_ORDER} (File > Open…) and click next_step. read, a Read File block, reads data/sales.csv; its "
           "table goes into next_step's run after.")
    run_tab = open_flow(win, monkeypatch, env.root / IN_ORDER)
    click_block(run_tab, "next_step")
    code = next(c for c in win.properties.findChildren(CodeEdit) if c.isVisible())
    p.check("The flow opens: read's table is connected to next_step's run after",
            run_tab.doc.edges() == [("start", "start", "read", "after"), ("read", "table", "next_step", "after")])
    connection = [rows(win, run_tab, "read", ["table"]), rows(win, run_tab, "next_step", ["after"], output=False)]
    p.shot(win, "The line from ① read's table to ② next_step's run after: next_step runs after read.",
           highlight=connection, badges=True, zoom=["start", "read", "next_step"])
    code_lines = [(code, "# read's table"), (code, 'print("read is done')]
    p.shot(win, "next_step's code, in Properties: it prints whether it was given read's table, which would be a "
                "variable named after.", highlight=[code_lines], zoom=code_lines)
    p.check("Properties shows next_step's code", code.toPlainText() == NEXT_CODE)

    p.step("Press ▶ Run now in the toolbar, then Show all, above the Log, to see the lines of every block.")
    press_run_now(win)
    first = wait_runs(qtbot, run_tab, 1)
    QTest.qWait(300)  # what the run printed last reaches the Log
    QTest.mouseClick(win.log.clear_filter, Qt.LeftButton)  # (the Log showed only next_step's, the block clicked)
    QTest.qWait(100)
    p.shot(win, "The run: start, read and next_step are green.", highlight=[["start", "read", "next_step"]],
           zoom=["start", "read", "next_step"])
    p.check(f"start, read and next_step succeeded, in run #{first}",
            shown(run_tab) == {"start": ("success", first), "read": ("success", first),
                               "next_step": ("success", first)})
    log = win.log.text
    read_line, next_line = "[read] INFO read data/sales.csv", f"[next_step] INFO {RAN_AFTER}"
    order = [(log, read_line), (log, next_line)]
    p.shot(win, "The Log, under the canvas: ① read read the file, then ② next_step ran and printed that it was not "
                "given read's table.", highlight=order, badges=True, zoom=order)
    text = log.toPlainText()
    p.check(f"① The Log says “{read_line}”, ② then “{next_line}”",
            read_line in text and next_line in text and text.index(read_line) < text.index(next_line))
    (read_done, next_started), = env.query(
        "SELECT r.finished, n.started FROM blocks r JOIN blocks n ON n.run_id = r.run_id "
        "WHERE r.run_id = ? AND r.block = 'read' AND n.block = 'next_step'", first)
    p.check("The run's record agrees: next_step started only once read had finished", read_done <= next_started)

    # --- a flow written before this change --------------------------------------------------------------------------
    p.step(f"In a terminal, in the folder of the flows, check {OLD}: taskloom validate {OLD}. It is {IN_ORDER} as it "
           "was written before this change: read's done goes into next_step's run after.")
    before = runs_recorded(env)
    printed, exit_code = terminal(env, "validate", OLD)
    p.text(f"taskloom validate {OLD} printed this, and ended with exit code {exit_code}", printed)
    p.check("taskloom validate names the connection and the block: “error: " + STALE + "”",
            printed == f"error: {STALE}")
    p.check("It ends with exit code 2", exit_code == 2)

    p.step(f"Run it there: taskloom run {OLD}.")
    printed, exit_code = terminal(env, "run", OLD)
    p.text(f"taskloom run {OLD} printed this, and ended with exit code {exit_code}", printed)
    p.check("taskloom run stops with the same error, and exit code 2", printed == f"error: {STALE}" and exit_code == 2)
    p.check("No run was recorded: the flow did not start", runs_recorded(env) == before)
    p.note("The scheduler service starts a scheduled run with this same command, so a scheduled flow like this one "
           "fails the same way, and says why.")

    p.step(f"Open {OLD} in the editor (File > Open…) and press ▶ Run now.")
    old_tab = open_flow(win, monkeypatch, env.root / OLD)
    said, titles = [], []

    def warning(parent, title, *rest):  # the real box; its title is in its title bar, which is not drawn here
        titles.append(title)
        return REAL_WARNING(parent, title, *rest)

    def look_at_problems(box):
        said.append(box.text())
        message = next(label for label in box.findChildren(QLabel) if label.text() == box.text())
        p.shot(box, "Run now stops and says why, naming the connection and the block: read has no output done.",
               highlight=[message])
        box.accept()

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "warning", staticmethod(warning))
        p.while_open(lambda: press_run_now(win), look_at_problems)
    QTest.qWait(100)
    p.note("These screenshots draw no window title bar, so the box's title, “The flow has problems”, is not in "
           "the picture: the check below reads it from what Taskloom gives the box.")
    p.check("Run now's box, titled “The flow has problems”, says it in the same words",
            titles == ["The flow has problems"] and said == [STALE])
    p.check("Nothing ran", not old_tab.runs and runs_recorded(env) == before)
    p.shot(win, "On the canvas, no line goes from read to next_step: there is no done on read to draw it from.",
           highlight=[["read", "next_step"]], zoom=["read", "next_step"])
    p.check("The canvas draws only the line from start to read", lines_drawn(old_tab) == ["start.start -> read.after"])
    p.check("The Log keeps the error too", STALE in win.log.text.toPlainText())

    p.step(f"Ask what can be fixed: taskloom check {OLD}.")
    printed, exit_code = terminal(env, "check", OLD)
    p.text(f"taskloom check {OLD} printed this, and ended with exit code {exit_code}", printed)
    p.check("It offers only to remove the connection read.done -> next_step.after, not to move it",
            "can fix: remove the connection read.done -> next_step.after: read has no output 'done'" in printed
            and printed.count("can fix:") == 1)
    p.note("Removing that connection (taskloom check --fix, or Tools > Check flows, after a backup) also removes the "
           f"order: then connect read's table to next_step's run after, as {IN_ORDER} does in steps 5 and 6.")
    tab.doc.dirty = False  # the lines of step 2 were drawn only to be shown: closing the window must not ask
