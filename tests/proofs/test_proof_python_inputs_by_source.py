"""Proof: several blocks connected into one input of a Python Code block arrive as a dictionary keyed by block
name. See README.md here."""

import os
import sys
from pathlib import Path

import pytest
import yaml
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QPushButton, QToolBar

from taskloom.editor.dialogs import AskDialog
from taskloom.editor.panels import CodeEdit

SALES_CSV = "region,product,amount\nNorth,Tea,120\nSouth,Coffee,80\nNorth,Cocoa,45\nEast,Tea,60\n"

REGION_CODE = """\
print("input is a", type(input).__name__)
print("keys:", list(input))
sales, region = input["load_sales"], input["ask_region"]
print('input["load_sales"] holds a', type(sales).__name__ + ":")
print(sales)
print('input["ask_region"] holds', repr(region))
output = sales[sales["region"] == region]"""

COUNT_CODE = """\
print("input is a", type(input).__name__, "of", len(input), "rows")
output = len(input)"""

SALES_FLOW = {  # in your Python environment, as a Python Code block you add in the editor
    "blocks": {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 110}},
        "load_sales": {"type": "data.read_file", "config": {"path": "sales.csv"}, "ui": {"x": 250, "y": 20}},
        "ask_region": {"type": "logic.ask_user",
                       "config": {"prompt": "Which region's sales?", "kind": "choice",
                                  "options": ["East", "North", "South", "West"]},
                       "ui": {"x": 250, "y": 200}},
        "region_sales": {"type": "logic.python",
                         "config": {"inputs": ["input"], "outputs": ["output"], "use_environment": True,
                                    "code": REGION_CODE},
                         "ui": {"x": 540, "y": 110}},
        "count_rows": {"type": "logic.python",
                       "config": {"inputs": ["input"], "outputs": ["output"], "use_environment": True,
                                  "code": COUNT_CODE},
                       "ui": {"x": 540, "y": -150}},
    },
    "edges": ["start.start -> load_sales.after", "start.start -> ask_region.after",
              "load_sales.table -> region_sales.input", "ask_region.answer -> region_sales.input",
              "load_sales.table -> count_rows.input"],
}

REPORT_CODE = """\
print("errors is a", type(errors).__name__)
print("keys:", list(errors))
for name, error in errors.items():
    print(f"{name} failed: {error['error']}")"""

ERRORS_FLOW = {  # in Taskloom's own Python: the file leaves Run in my Python environment out
    "blocks": {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 150}},
        "load_orders": {"type": "data.read_file", "config": {"path": "orders.csv"}, "ui": {"x": 250, "y": 0}},
        "compute_totals": {"type": "logic.python",
                           "config": {"inputs": [], "code": "raise RuntimeError('the totals do not add up')"},
                           "ui": {"x": 250, "y": 150}},
        "tidy_names": {"type": "logic.python", "config": {"inputs": [], "code": "output = 'names tidied'"},
                       "ui": {"x": 250, "y": 300}},
        "report_errors": {"type": "logic.python",
                          "config": {"inputs": ["errors"], "outputs": [], "code": REPORT_CODE},
                          "ui": {"x": 560, "y": 150}},
    },
    "edges": ["start.start -> load_orders.after", "start.start -> compute_totals.after",
              "start.start -> tidy_names.after", "load_orders.error -> report_errors.errors",
              "compute_totals.error -> report_errors.errors", "tidy_names.error -> report_errors.errors"],
}

AFTER_CHECK_CODE = """\
print("input is a", type(input).__name__)
print("keys:", list(input))
for name, value in input.items():
    print(f'input["{name}"] holds {value!r}')"""

CHECK_FLOW = {  # in Taskloom's own Python
    "blocks": {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 40}},
        "check": {"type": "logic.python",
                  "config": {"inputs": [], "outputs": ["result", "details"],
                             "code": "result = 'all rows add up'\ndetails = '4 rows, 305 in all'"},
                  "ui": {"x": 250, "y": 40}},
        "after_check": {"type": "logic.python",
                        "config": {"inputs": ["input"], "outputs": [], "code": AFTER_CHECK_CODE},
                        "ui": {"x": 540, "y": 40}},
    },
    "edges": ["start.start -> check.after", "check.result -> after_check.input",
              "check.details -> after_check.input"],
}


def open_flow(win, monkeypatch, path: Path, flow: dict):
    """Write `flow` to `path` and open it (File > Open…), in a tab of its own."""
    path.write_text(yaml.safe_dump(flow, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    QTest.qWait(100)
    return win.current


def click(tab, block: str):
    """Click a block on the canvas, as you would: Properties shows it, the Log shows only its lines."""
    viewport = tab.view.viewport()
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier,
                     tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center()))
    QTest.qWait(100)


def press_run_now(win):
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)


def wait_for_the_run(qtbot, tab):
    qtbot.waitUntil(lambda: len(tab.runs) == 1 and not tab.running and tab.runs[0].status is not None,
                    timeout=180_000)
    QTest.qWait(300)  # what the run printed last reaches the Log
    return tab.runs[0]


def said(win, block: str) -> list[str]:
    """What `block` wrote to the Log in the run on screen, line by line."""
    return [line for e in win.log.entries if e.get("block") == block for line in e.get("message", "").splitlines()]


def status(tab, block: str):
    return tab.scene.nodes[block].status


def connected(tab, dst: str) -> list[str]:
    """The outputs connected into `dst`'s input, in the order of the connections: block.output."""
    return [f"{src}.{src_port}" for src, src_port, to, _ in tab.doc.edges() if to == dst]


def make_your_python_environment(env):
    """Your Python environment, with pandas and pyarrow: what Tools → Python environment makes, here from the
    command line. The package index is needed: on CI (which has it) a failure fails the proof."""
    utf8 = {"encoding": "utf-8", "errors": "replace"}  # what pip prints, as it is (not the Windows code page)
    made = env.taskloom("env", "create", "--python", sys.executable, timeout=300, **utf8)
    assert made.returncode == 0, made.stdout + made.stderr
    installed = env.taskloom("env", "pip", "install", "pandas", "pyarrow", timeout=900, **utf8)
    if installed.returncode != 0:
        if os.environ.get("CI"):
            raise AssertionError(installed.stdout + installed.stderr)
        pytest.skip(f"the package index is unreachable here: {installed.stdout[-500:]}")


# `env` before `win`: its Taskloom folder is the editor's (the same temporary one), and it is made first.
def test_several_blocks_into_one_python_input_arrive_keyed_by_their_names(proof, env, win, qtbot, monkeypatch):
    p = proof("Several blocks into one Python Code input arrive as a dictionary keyed by block name",
              "Since input can receive multiple sources the input variable should be a dictionary with the source "
              "block name as key and its output value (python block input value) as the value; this in order to "
              "identify values in the code")
    p.idea("When several blocks are connected into one input of a Python Code block, your code gets that input as "
           "a dictionary: each key is the name of a connected block, as on the canvas, and its value is what that "
           "block gave.")
    p.idea("So input[\"load_sales\"] is the sales table and input[\"ask_region\"] is the region, whichever order the "
           "connections were made in; the dictionary lists them in the order of the connections. With one block "
           "connected, the input is that block's value, as before.")
    p.idea("Only the blocks that gave something are in it. When the red error outputs of several blocks go to one "
           "handler, it holds the errors of the blocks that failed, keyed by their names. After an If, it holds the "
           "path that ran.")
    p.idea("When one block connects two of its outputs to the same input, each is keyed with the block's name and "
           "the output's, such as check.result and check.details, so neither is lost.")
    p.idea("Only the inputs of Python Code blocks, where you write the code, become dictionaries. Other blocks that "
           "take several connections into one input, such as Inspect or Send Email's body, still get a list, in "
           "the order of the connections.")
    p.why_simplest("Every block already has a name of its own on the canvas, so that name is the key. There is "
                   "nothing to set up: no new setting and no new kind of input. The connections you draw decide the "
                   "keys.")
    p.why_simplest("One rule hands every input what reaches it, so every run gives your code the same dictionary: "
                   "from the editor, from taskloom run, from the scheduler, and when you run the block again on its "
                   "own with the inputs of an earlier run (Run only this block, with the same inputs).")
    p.why_simplest("The keys come from how the blocks are connected, not from which of them delivered: the output's "
                   "name is added only when one block connects two of its outputs there, and then it is added whether "
                   "one or both of them arrive.")
    p.alternative("Keep the list, in the order of the connections",
                  "That is what it was. Your code had to know the order the connections were made in, which the "
                  "canvas does not show, and the positions moved whenever a block failed or an If took the other "
                  "path, since only what was delivered is in it. You asked to tell the values apart in the code.")
    p.alternative("One input for each connected block",
                  "A Python Code block can already have several inputs (its Inputs setting), each connected to one "
                  "block, and that still works for values that always arrive. But it cannot make one handler for the "
                  "errors of many blocks: a block runs only when each of its inputs gets something, so that handler "
                  "would run only when every block it covers had failed.")
    p.alternative("Always key by block and output, such as load_sales.table",
                  "You asked for the block's name as the key. Every key would carry an output name that is "
                  "usually not needed (the errors would be load_orders.error, compute_totals.error), for the rare "
                  "case of one block connecting two outputs to one input.")
    p.alternative("A dictionary for every block that takes several connections, Inspect and Send Email too",
                  "Those blocks are not code you write: they use what they get as a list (Send Email puts the bodies "
                  "one after another, Inspect passes on what it got). Changing them would change what flows "
                  "already do, and nothing in them needs to tell the sources apart by name.")
    p.fits("It is the input Taskloom already had for several connections: one that runs when any one of them "
           "delivers (one handler for many blocks' errors, two paths that meet after an If). Only what your code "
           "receives changed, from a list to a dictionary.")
    p.fits("A Python Code block you add in the editor starts with a few lines of comments that show this "
           "dictionary (input = {\"load_sales\": <a table>, \"ask_region\": \"North\"}), and the example flow "
           "shared-error-handler.yaml now names the steps that failed from its keys. In your Python environment a "
           "table in the dictionary is a pandas DataFrame, as a table on its own is.")
    p.fits("Code written for the old list needs changing: nothing converts it. A loop over the input now goes over "
           "the block names; input.values() goes over the values, input.items() over both.")

    assert win.home.root == env.home, "the editor and the command line use the same Taskloom folder"
    make_your_python_environment(env)
    (env.root / "sales.csv").write_text(SALES_CSV, encoding="utf-8")

    p.step("Open the flow sales-by-region.yaml (File > Open…) and click region_sales. Two blocks are connected into "
           "its one input: load_sales, which reads sales.csv into a table, and ask_region, which asks for a region.")
    p.note("region_sales and count_rows run in your Python environment, as a Python Code block you add in the "
           "editor does. That environment was made first, with pandas and pyarrow in it, as Tools → Python "
           "environment makes it; here from the command line (taskloom env create, then taskloom env pip install "
           "pandas pyarrow).")
    tab = open_flow(win, monkeypatch, env.root / "sales-by-region.yaml", SALES_FLOW)
    click(tab, "region_sales")
    p.check("load_sales and ask_region are connected into region_sales's input, and only load_sales into count_rows's",
            connected(tab, "region_sales") == ["load_sales.table", "ask_region.answer"]
            and connected(tab, "count_rows") == ["load_sales.table"])
    two = ["load_sales", "ask_region", "region_sales"]
    p.shot(win, "① load_sales and ask_region, both connected into region_sales's one input. ② count_rows, with only "
                "load_sales connected into its input.",
           highlight=[two, "count_rows"], badges=True, zoom=[*two, "count_rows"])
    code = next(c for c in win.properties.findChildren(CodeEdit) if c.isVisible())
    p.check("region_sales's code prints what its input is and its keys, then picks each value by its block's name",
            code.toPlainText() == REGION_CODE)
    lines = [(code, line) for line in REGION_CODE.splitlines()]
    p.shot(win, "region_sales's code, in Properties: it prints what its input is and its keys, then takes each "
                "value by its block's name, prints it, and keeps the sales of the region asked for.",
           highlight=[lines], zoom=[lines])

    p.step("Press ▶ Run now in the toolbar. ask_region asks which region's sales: choose North and press OK.")
    press_run_now(win)
    qtbot.waitUntil(lambda: any(d.isVisible() for d in win.findChildren(AskDialog)), timeout=120_000)
    ask = next(d for d in win.findChildren(AskDialog) if d.isVisible())
    QTest.keyClicks(ask.input, "North")
    p.check("ask_region asks which region's sales, and North is chosen",
            ask.windowTitle() == "ask_region asks" and ask.input.currentText() == "North")
    p.shot(ask, "ask_region's question, with North chosen.")
    QTest.mouseClick(next(b for b in ask.findChildren(QPushButton) if b.text() == "OK"), Qt.LeftButton)
    run = wait_for_the_run(qtbot, tab)
    p.check("The run ends with success", run.status == "success")

    p.step("Click region_sales. The Log, under the canvas, now shows only what it wrote.")
    click(tab, "region_sales")
    log, wrote = win.log.text, said(win, "region_sales")
    last_row = next((line for line in wrote if line.split()[1:] == ["East", "Tea", "60"]), "East")
    keys = [(log, "input is a dict"), (log, "keys: ['load_sales', 'ask_region']")]
    sales = [(log, 'input["load_sales"] holds a DataFrame:'), (log, last_row)]
    region = (log, 'input["ask_region"] holds')
    p.shot(win, "① Its input is a dictionary, and its keys are load_sales and ask_region: the names of the two "
                "blocks. ② input[\"load_sales\"] holds the sales table, as a pandas DataFrame. ③ "
                "input[\"ask_region\"] holds North, the answer given.",
           highlight=[keys, sales, region], badges=True, zoom=[*keys, *sales, region])
    p.check("① region_sales's input is a dictionary keyed load_sales, then ask_region",
            "input is a dict" in wrote and "keys: ['load_sales', 'ask_region']" in wrote)
    p.check("② input[\"load_sales\"] holds the four rows of sales.csv, as a pandas DataFrame",
            'input["load_sales"] holds a DataFrame:' in wrote
            and {("North", "Tea", "120"), ("South", "Coffee", "80"), ("North", "Cocoa", "45"),
                 ("East", "Tea", "60")} <= {tuple(line.split()[1:]) for line in wrote})
    p.check("③ input[\"ask_region\"] holds North", "input[\"ask_region\"] holds 'North'" in wrote)
    p.check("region_sales used them: it passed on the sales of North, 2 rows × 3 columns (its last line in the "
            "Log)",
            status(tab, "region_sales") == "success"
            and any(line.startswith("success · output: 2 rows × 3 columns") for line in wrote))

    p.step("Click count_rows. Only load_sales is connected into its input, so its input is load_sales's table "
           "itself, as before.")
    click(tab, "count_rows")
    table = (log, "input is a DataFrame of 4 rows")
    p.shot(win, "count_rows's input is the table itself, a DataFrame of 4 rows: not a dictionary.",
           highlight=[table], zoom=[win.log.showing, table, (log, f"DONE run #{run.run_id}")])
    p.check("count_rows's input is load_sales's table itself (a DataFrame of 4 rows), not a dictionary",
            "input is a DataFrame of 4 rows" in said(win, "count_rows") and status(tab, "count_rows") == "success")

    p.step("Open the flow one-error-handler.yaml, press ▶ Run now, and click report_errors when the run ends. The "
           "red error outputs of load_orders, compute_totals and tidy_names all go to report_errors's one input.")
    p.note("These blocks run in Taskloom's own Python (the flow file leaves Run in my Python environment out): the "
           "dictionary is the same there.")
    errors_tab = open_flow(win, monkeypatch, env.root / "one-error-handler.yaml", ERRORS_FLOW)
    press_run_now(win)
    run = wait_for_the_run(qtbot, errors_tab)
    click(errors_tab, "report_errors")
    failed = ["load_orders", "compute_totals"]
    p.shot(win, "① load_orders (there is no orders.csv) and compute_totals failed: they are red. ② tidy_names did "
                "not fail. ③ report_errors, which all three red error outputs go to, ran.",
           highlight=[failed, "tidy_names", "report_errors"], badges=True,
           zoom=[*failed, "tidy_names", "report_errors"])
    p.check("① load_orders and compute_totals failed, ② tidy_names succeeded, ③ report_errors ran",
            [status(errors_tab, b) for b in ("load_orders", "compute_totals", "tidy_names", "report_errors")]
            == ["failed", "failed", "success", "success"])
    p.check("The failures were handled, so the run ends with success", run.status == "success")
    keys = [(log, "errors is a dict"), (log, "keys: ['load_orders', 'compute_totals']")]
    errors = [(log, "load_orders failed:"), (log, "compute_totals failed:")]
    p.shot(win, "What report_errors wrote. ① Its input is a dictionary keyed by the two blocks that failed, "
                "load_orders and compute_totals; tidy_names, which did not fail, is not in it. ② Each holds its "
                "block's error.",
           highlight=[keys, errors], badges=True, zoom=[*keys, *errors])
    wrote = said(win, "report_errors")
    p.check("① report_errors's input holds the errors of load_orders and compute_totals, keyed by their names, and "
            "nothing of tidy_names",
            "errors is a dict" in wrote and "keys: ['load_orders', 'compute_totals']" in wrote
            and not any("tidy_names" in line for line in wrote))
    p.check("② Each holds its block's error",
            "load_orders failed: FileNotFoundError: file not found: orders.csv" in wrote
            and any(line.startswith("compute_totals failed: RuntimeError: the totals do not add up") for line in wrote))

    p.step("Open the flow check-two-outputs.yaml, press ▶ Run now, and click after_check when the run ends. check "
           "connects two of its outputs, result and details, to after_check's one input.")
    p.note("check's red error output, connected there too, would be keyed check.error, but it cannot arrive with "
           "check's other outputs: when check fails, a block connected to one of its other outputs does not run, "
           "as after any block that failed. To handle a block's error, connect its error output to a block that "
           "takes nothing else from it, as report_errors does.")
    check_tab = open_flow(win, monkeypatch, env.root / "check-two-outputs.yaml", CHECK_FLOW)
    press_run_now(win)
    wait_for_the_run(qtbot, check_tab)
    click(check_tab, "after_check")
    p.check("check's result and details outputs are both connected into after_check's input",
            connected(check_tab, "after_check") == ["check.result", "check.details"])
    p.shot(win, "check's two outputs, result and details, both connected into after_check's one input. Its red "
                "error output is not connected there (see the note above).",
           highlight=[["check", "after_check"]], zoom=["check", "after_check"])
    keys = [(log, "input is a dict"), (log, "keys: ['check.result', 'check.details']")]
    values = [(log, 'input["check.result"] holds'), (log, 'input["check.details"] holds')]
    p.shot(win, "① after_check's input is keyed check.result and check.details: the block's name and each output's, "
                "so both values are kept. ② Each holds what that output gave.",
           highlight=[keys, values], badges=True, zoom=[*keys, *values])
    wrote = said(win, "after_check")
    p.check("① after_check's input is keyed check.result and check.details",
            "input is a dict" in wrote and "keys: ['check.result', 'check.details']" in wrote)
    p.check("② Each holds that output's value",
            "input[\"check.result\"] holds 'all rows add up'" in wrote
            and "input[\"check.details\"] holds '4 rows, 305 in all'" in wrote)
