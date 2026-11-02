"""Proofs: what ends a Splitter's line, and what could never end. A failed line stops no other line and fails the
Merger, naming it; after a fix, Run from here runs only that line, the Merger and what follows; Stop cancels every line
at once. Validate refuses a layout that could never finish, and the layouts not supported yet, naming each. See
README.md here."""

import datetime as dt
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QToolBar

from proofs.conftest import choose, menu_item, right_click
from taskloom.editor.canvas import HEADER_H, NODE_W
from taskloom.history import History
from tests.test_lines import shown, wait_runs
from tests.test_paths_editor_editing import field, flow_file

# Taken as this file is imported, before the win fixture answers message boxes itself: Validate's box and a refused
# run's are then shown as in the app.
REAL_WARNING = QMessageBox.warning
ASKED = ("parallel runs is something we indeed need to support, probably the cleanest way is to create splitter and "
         "merger blocks and we set on the splitter if we want the split lines to run in parallel or not ... the 2nd "
         "line can connect with the 1st line ... Then the merger block can support \"join\" that waits for all the "
         "inputs to finish before proceeding ...")
FROM_HERE = "Run from this block on, with the same inputs"
SAME_TIME, ONE_AFTER_ANOTHER = "at the same time", "one after another"


# --- the flows -------------------------------------------------------------------------------------------------------

def python(code: str, inputs=("input",), x=0, y=0) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "code": code}, "ui": {"x": x, "y": y}}


def counted(name: str, code: str, seconds: float = 0.0, x=0, y=0) -> dict:
    """A Python Code block that counts its runs (a mark in runs-<name>.txt, in the flow's folder, for each) and says in
    the Log how many there have been, waits `seconds`, then runs `code`."""
    return python(f"import time\nwith open('runs-{name}.txt', 'a+', encoding='utf-8') as f:\n    f.write('x')\n"
                  f"    f.seek(0)\n    print('runs so far:', len(f.read()))\ndel f\ntime.sleep({seconds})\n{code}",
                  x=x, y=y)


def runs_of(folder: Path, name: str) -> int:
    """How many times the counted block `name` has run."""
    path = folder / f"runs-{name}.txt"
    return len(path.read_text(encoding="utf-8")) if path.exists() else 0


def start(x=0, y=0) -> dict:
    return {"type": "trigger.manual", "ui": {"x": x, "y": y}}


def splitter(lines_run: str = SAME_TIME, x=0, y=150) -> dict:
    return {"type": "logic.splitter", "config": {"lines_run": lines_run}, "ui": {"x": x, "y": y}}


def merger(x=460, y=150) -> dict:
    return {"type": "logic.merger", "ui": {"x": x, "y": y}}


def lines_from(splitter_id: str, *firsts: str) -> list[str]:
    return [f"{splitter_id}.lines -> {first}.input" for first in firsts]


def into(merger_id: str, *lasts: str) -> list[str]:
    return [f"{last}.output -> {merger_id}.lines" for last in lasts]


LOAD_COSTS = ("from pathlib import Path\nif not Path('costs.csv').exists():\n"
              "    raise FileNotFoundError('the costs file is missing')\n"
              "output = [int(n) for n in Path('costs.csv').read_text(encoding='utf-8').split()]")
MISSING = "FileNotFoundError: the costs file is missing"


def month_end(folder: Path) -> Path:
    """Three lines at the same time, top to bottom: load_sales; load_costs -> costs_total; load_stock; joined by merge,
    then report. load_costs fails until costs.csv is in the flow's folder."""
    return flow_file(folder / "month-end.yaml", {
        "start_by_hand": start(), "split": splitter(),
        "load_sales": counted("load_sales", "output = [120, 80, 45]", 1.5, x=230, y=0),
        "load_costs": counted("load_costs", LOAD_COSTS, x=230, y=150),
        "costs_total": counted("costs_total", "output = sum(input)", x=460, y=150),
        "load_stock": counted("load_stock", "output = 37", 1.5, x=230, y=300),
        "merge": merger(x=690), "report": counted("report", "output = sum(input['load_sales']) - input['costs_total']",
                                                  x=690, y=300),
    }, ["start_by_hand.start -> split.after", *lines_from("split", "load_sales", "load_costs", "load_stock"),
        "load_costs.output -> costs_total.input", *into("merge", "load_sales", "costs_total", "load_stock"),
        "merge.value -> report.input"])


LINE_1, LINE_2, LINE_3 = ["load_sales"], ["load_costs", "costs_total"], ["load_stock"]
MONTH_END = ["start_by_hand", "split", *LINE_1, *LINE_2, *LINE_3, "merge", "report"]


def two_waits(folder: Path) -> Path:
    """Two lines at the same time, each a Wait of 30 seconds, joined by merge."""
    wait = {"type": "logic.wait", "config": {"seconds": 30}}
    return flow_file(folder / "two-waits.yaml", {
        "start_by_hand": start(y=150), "split": splitter(x=230), "wait_north": {**wait, "ui": {"x": 460, "y": 50}},
        "wait_south": {**wait, "ui": {"x": 460, "y": 250}}, "merge": merger(x=690),
    }, ["start_by_hand.start -> split.after", "split.lines -> wait_north.value", "split.lines -> wait_south.value",
        "wait_north.value -> merge.lines", "wait_south.value -> merge.lines"])


NEVER = "load_stock.output -> load_sales.stock"  # from the bottom line (3) into the top line's first block


def never_finish(folder: Path) -> Path:
    """One after another, top to bottom: load_sales, load_costs, load_stock; load_sales also takes load_stock's
    output (NEVER)."""
    return flow_file(folder / "never-finish.yaml", {
        "start_by_hand": start(), "split": splitter(ONE_AFTER_ANOTHER, y=180),
        "load_sales": python("output = [sum([120, 80, 45]), stock]", ("input", "stock"), x=460, y=0),
        "load_costs": python("output = 70", x=230, y=180), "load_stock": python("output = 37", x=230, y=330),
        "merge": merger(x=690, y=180),
    }, ["start_by_hand.start -> split.after", *lines_from("split", "load_sales", "load_costs", "load_stock"), NEVER,
        *into("merge", "load_sales", "load_costs", "load_stock")])


def nested(folder: Path) -> Path:
    """split's line 1 is regions, a Splitter with lines north and south joined by regions_merge; line 2 is
    load_costs; merge joins them."""
    return flow_file(folder / "nested.yaml", {
        "start_by_hand": start(), "split": splitter(), "regions": splitter(x=230, y=50),
        "north": python("output = 120", x=460, y=-25), "south": python("output = 80", x=460, y=125),
        "regions_merge": merger(x=690, y=50), "load_costs": python("output = 70", x=230, y=275),
        "merge": merger(x=690, y=275),
    }, ["start_by_hand.start -> split.after", "split.lines -> regions.value", "split.lines -> load_costs.input",
        *lines_from("regions", "north", "south"), *into("regions_merge", "north", "south"),
        "regions_merge.value -> merge.lines", "load_costs.output -> merge.lines"])


ONE_WAY = "north_sales.output -> north_costs.sales"
OTHER_WAY = "south_costs.output -> south_sales.costs"


def side_by_side(folder: Path) -> Path:
    """Two Splitters side by side, sales (lines north_sales, south_sales) and costs (north_costs, south_costs), each
    with its Merger; north_sales feeds north_costs (ONE_WAY) and south_costs feeds south_sales (OTHER_WAY)."""
    return flow_file(folder / "side-by-side.yaml", {
        "start_by_hand": start(y=225), "sales": splitter(x=230, y=75), "costs": splitter(x=230, y=375),
        "north_sales": python("import time\ntime.sleep(1)\noutput = 120", x=460, y=0),
        "south_sales": python("output = 80", ("input", "costs"), x=460, y=150),
        "north_costs": python("output = [30, sales]", ("input", "sales"), x=690, y=300),
        "south_costs": python("output = 25", x=690, y=450),
        "sales_merge": merger(x=690, y=75), "costs_merge": merger(x=920, y=375),
    }, ["start_by_hand.start -> sales.after", "start_by_hand.start -> costs.after",
        *lines_from("sales", "north_sales", "south_sales"), *lines_from("costs", "north_costs", "south_costs"),
        ONE_WAY, OTHER_WAY, *into("sales_merge", "north_sales", "south_sales"),
        *into("costs_merge", "north_costs", "south_costs")])


# --- doing it as you would ---------------------------------------------------------------------------------------------

def open_flow(win, monkeypatch, path: Path):
    """File > Open…, choosing `path` in the file dialog: the tab it opens in."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    menu_item(file_menu, "Open…").trigger()
    QTest.qWait(100)
    return win.current


def press(win, action):
    """Press a button of the toolbar (▶ Run now, Stop, Validate)."""
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(action), Qt.LeftButton)


def click_bottom_tab(win, panel):
    """Click one of the tabs under the canvas (the Log, Data preview, Run history)."""
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.bottom_tabs.indexOf(panel)).center())
    QTest.qWait(100)


def click_block(tab, block: str):
    """Click a block's title bar: Properties shows it, and the Log only its lines."""
    at = tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, tab.view.mapFromScene(at))
    QTest.qWait(100)


def delete_connection(win, tab, qtbot, text: str):
    """Click a connection on the canvas (`text` as the flow file writes it), where nothing else is drawn over it, and
    press Delete."""
    (edge,) = [e for e in tab.scene.edge_items if e.text == text]
    clear = [at for t in sorted((i / 40 for i in range(2, 39)), key=lambda t: abs(t - 0.5))
             if tab.view.itemAt(at := tab.view.mapFromScene(edge.mapToScene(edge.path().pointAtPercent(t)))) is edge]
    assert clear, f"{text} is everywhere under something else"
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, clear[0])
    assert edge.isSelected(), f"clicking {text} did not select it"
    win.activateWindow()  # the keys go to the window you are in
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=5_000)
    tab.view.setFocus()
    QTest.keyClick(tab.view, Qt.Key_Delete)
    QTest.qWait(200)  # the canvas is drawn again


def choose_in_list(p, combo, text: str):
    """Open a drop-down and click `text` in its list, as you would (not in the first instant after it opened: it
    takes no click then)."""
    def pick(popup):
        view = combo.view()
        QTest.qWait(QApplication.doubleClickInterval() + 100)
        row = next(r for r in range(combo.count()) if combo.itemText(r) == text)
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier,
                         view.visualRect(view.model().index(row, 0)).center())

    p.while_open(combo.showPopup, pick)
    QTest.qWait(100)


def label(win, text: str) -> QLabel:
    """The label of a row of Properties."""
    return next(w for w in win.properties.findChildren(QLabel) if w.text() == text and w.isVisible())


def box_text(box) -> QLabel:
    """The text of a message box, to outline it."""
    return next(w for w in box.findChildren(QLabel) if w.text() == box.text())


def boxes_said(p, monkeypatch, act, caption: str) -> list[str]:
    """Do `act` (which shows a message box), shoot the box as the app shows it, with `caption`, and close it: what it
    said."""
    said = []

    def look(box):
        said.append(box.text())
        p.shot(box, caption, highlight=[box_text(box)])
        box.accept()

    with monkeypatch.context() as real:  # the box as the app shows it
        real.setattr(QMessageBox, "warning", REAL_WARNING)
        p.while_open(act, look)
    QTest.qWait(100)
    return said


def history(win, read):
    """What read(history) reads of the run history the editor keeps."""
    kept = History(win.home.history_db)
    try:
        return read(kept)
    finally:
        kept.close()


def times(win, run_id: int) -> dict:
    """{block: (when it started, when it finished)} in run `run_id`, as its history keeps them."""
    with closing(sqlite3.connect(win.home.history_db)) as db:
        rows = db.execute("SELECT block, started, finished FROM blocks WHERE run_id=? AND started IS NOT NULL AND "
                          "finished IS NOT NULL", (run_id,)).fetchall()
    return {b: (dt.datetime.fromisoformat(s), dt.datetime.fromisoformat(f)) for b, s, f in rows}


def output_of(tab, block: str):
    """What the canvas shows a block's output held (None: it shows none)."""
    try:
        return json.loads(tab.scene.values.get(block, {}).get("output", ""))
    except ValueError:
        return None


def statuses(tab, blocks) -> dict:
    return {b: tab.scene.nodes[b].status for b in blocks}


def status_message(win) -> tuple[str, QRect]:
    """What the status bar, at the bottom of the window, says; and where, in the window's pixels: to outline it."""
    bar = win.statusBar()
    message = bar.currentMessage()
    return message, QRect(bar.mapTo(win, bar.rect().topLeft()),
                          QSize(bar.fontMetrics().horizontalAdvance(message) + 16, bar.height()))


def settled(win, qtbot):
    """Once a run has ended: the window says no flow is running (and the Log has its last lines)."""
    qtbot.waitUntil(lambda: win.running_flows.text() == "No flows running", timeout=10_000)
    QTest.qWait(200)


# --- 1: a failed line, Run from here, Stop -----------------------------------------------------------------------------

def test_a_failed_line_stops_no_other_line_and_run_from_here_reruns_only_it(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("A failed line stops no other line; after a fix, Run from here runs only it", ASKED)
    p.idea("When a line of a Splitter fails, the other lines go on to their end: nothing is cancelled. The Merger then "
           "fails, saying which line failed and why (“load_costs failed: …”), in the Log too, and the blocks after it "
           "do not run. The lines that succeeded keep what they made.")
    p.idea("Once you have fixed what failed, right-click the failed block and choose Run from this block on, with the "
           "same inputs. It runs that block, the rest of its line, the Merger and what follows. The lines that "
           "succeeded do not run again: what they made in the failed run reaches the Merger.")
    p.idea("Stop ends every line at once: each running block is cancelled, and so is the run.")
    p.why_simplest("A line is the blocks after one of the Splitter's connections. Its blocks run one after another, "
                   "as in any flow, while the other lines run beside it. A failure already stopped only the blocks "
                   "after the failed one: in a line, those are the rest of that line. So the other lines go on by the "
                   "very rule that was already there; nothing new decides it.")
    p.why_simplest("The Merger waits for every line it joins. When a line has delivered nothing because a block in it "
                   "failed, the Merger fails with that block's error, named, where any other block would just be "
                   "skipped: you see at once which line to fix, and why.")
    p.why_simplest("Run from this block on is the one Taskloom already had: it runs a block and the blocks after it, and "
                   "takes everything else from the run you carry on from. The lines that succeeded are not after "
                   "load_costs, so they do not run again; what they made is kept with that run.")
    p.alternative("Cancel the other lines as soon as one fails",
                  "Work already half done in them would be thrown away, and you would have to run them again after "
                  "the fix. As agreed with you, nothing is cancelled silently: the other lines finish and keep what "
                  "they made.")
    p.alternative("Let the Merger pass on the lines that succeeded, and leave out the one that failed",
                  "The blocks after it would then run on part of the data, and the run could look as if all were well. "
                  "A Merger that fails, naming the line, stops that before it happens.")
    p.alternative("A separate “retry the failed lines” command",
                  "A second way to run part of a flow again, with its own rules to learn. Run from this block on "
                  "already does it, from any block, with or without a Splitter.")
    p.fits("A failed block in a line looks and behaves as anywhere else: red, its error in the Log, its error output "
           "there to handle the failure. The Merger's own error output handles the Merger's failure the same way.")
    p.fits("The canvas, the Log and Run history read the same records: each block's result in each run it took part "
           "in. After Run from here, the lines that did not run again go on showing their results from the failed "
           "run, and Run history says what started each run.")
    p.fits("Stop is the same Stop as for any run. A block's Timeout still ends only that block.")
    folder = tmp_path / "flows"
    folder.mkdir()
    flow = month_end(folder)
    log = win.log.text

    p.step("Open the flow month-end.yaml (File > Open…). Its Splitter, split, starts three lines, top to bottom: "
           "① load_sales; ② load_costs, then costs_total; ③ load_stock. The Merger, merge, joins them, and report "
           "works out sales minus costs from what it passes on.")
    tab = open_flow(win, monkeypatch, flow)
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    p.shot(win, "The three lines ①, ② and ③, between split and merge. load_costs reads costs.csv, which is not in the "
                "flow's folder yet: it will fail.",
           highlight=[LINE_1, LINE_2, LINE_3], badges=True, zoom=MONTH_END)

    p.step("Press ▶ Run now in the toolbar. The three lines run at the same time, and load_costs fails at once.")
    press(win, win.run_action)
    first = wait_runs(qtbot, tab, 1)
    settled(win, qtbot)
    p.shot(win, "① load_sales and ③ load_stock went on to their end: green, each with its output. ② load_costs failed "
                "(red), so costs_total, after it, was skipped. ④ The Merger failed, saying “load_costs failed: …”, "
                "and report did not run.",
           highlight=[LINE_1, LINE_2, LINE_3, "merge"], badges=True, zoom=MONTH_END)
    ran = times(win, first)
    p.check("① load_sales and ③ load_stock succeeded; ② load_costs failed and costs_total was skipped",
            statuses(tab, [*LINE_1, *LINE_2, *LINE_3])
            == {"load_sales": "success", "load_costs": "failed", "costs_total": "skipped", "load_stock": "success"})
    p.check("① and ③ ran at the same time, and went on after load_costs had failed: both ended after it, and no "
            "block was cancelled",
            (min(ran["load_sales"][1], ran["load_stock"][1]) - max(ran["load_sales"][0], ran["load_stock"][0]))
            .total_seconds() > 1
            and all(ran[b][1] > ran["load_costs"][1] for b in ("load_sales", "load_stock"))
            and "cancelled" not in statuses(tab, MONTH_END).values())
    merge_error = history(win, lambda h: h.blocks(first))["merge"]["error"] or ""
    p.check(f"④ The Merger failed with “load_costs failed: {MISSING} …”, and report did not run",
            statuses(tab, ["merge", "report"]) == {"merge": "failed", "report": "skipped"}
            and merge_error.startswith(f"load_costs failed: {MISSING}"))
    p.check(f"Run #{first} failed", tab.runs[0].status == "failed")
    p.note("Had load_costs's error output been connected to a block that handles the failure, the Merger would not "
           "fail: it would be skipped, as any block after a handled failure is, and the run would count as a success. "
           "Leave the error output unconnected, as here, when the Merger should fail.")

    p.step("Click merge, the Merger, on the canvas. The Log, under the canvas, then shows only its lines.")
    click_block(tab, "merge")
    failed = (log, f"[merge] ERROR merge (Merger) failed: load_costs failed: {MISSING}")
    got = [(log, "lines ← load_sales.output"), (log, "lines ← costs_total.output"), (log, "lines ← load_stock.output")]
    p.shot(win, "① The Merger's error names the line that failed and why. ② What it got from each line: load_sales' "
                "[120, 80, 45] and load_stock's 37 arrived; from costs_total, in the failed line, nothing arrived.",
           highlight=[failed, got], badges=True, zoom=[failed, *got])
    text = log.toPlainText()
    p.check(f"① The Log shows the Merger's error: “load_costs failed: {MISSING}”", failed[1] in text)
    p.check("② It lists what arrived: [120, 80, 45] from load_sales, 37 from load_stock, nothing from costs_total",
            "lines ← load_sales.output: [ 120, 80, 45 ]" in text and "lines ← load_stock.output: 37" in text
            and "lines ← costs_total.output: (nothing arrived)" in text)

    p.step("Click Show all, above the Log, and put the missing file, costs.csv (30, 25 and 15), in the flow's folder.")
    QTest.mouseClick(win.log.clear_filter, Qt.LeftButton)
    (folder / "costs.csv").write_text("30\n25\n15\n", encoding="utf-8")

    p.step("Right-click load_costs and choose “Run from this block on, with the same inputs”.")

    def run_from_here(menu):
        p.shot(menu, f"The menu of load_costs, with the item to choose: the same inputs are those of run #{first}, "
                     "the one load_costs shows.", over=win, highlight=[menu_item(menu, FROM_HERE)],
               zoom=[menu, "load_costs"])
        choose(menu, FROM_HERE)

    p.while_open(lambda: right_click(tab, "load_costs"), run_from_here)
    second = wait_runs(qtbot, tab, 2)
    settled(win, qtbot)
    p.shot(win, f"All green. ② load_costs, costs_total, ④ the Merger and report ran in run #{second}; report made 175 "
                f"(245 of sales minus 70 of costs). ① load_sales and ③ load_stock did not run again: they show their "
                f"results from run #{first}.",
           highlight=[LINE_1, LINE_2, LINE_3, ["merge", "report"]], badges=True, zoom=MONTH_END)
    p.check(f"Run #{second} succeeded", tab.runs[1].status == "success")
    p.check(f"② load_costs, costs_total, ④ the Merger and report show success from run #{second}",
            shown(tab, [*LINE_2, "merge", "report"]) == {b: ("success", second) for b in [*LINE_2, "merge", "report"]})
    p.check(f"① load_sales and ③ load_stock still show their results from run #{first}",
            shown(tab, [*LINE_1, *LINE_3]) == {b: ("success", first) for b in [*LINE_1, *LINE_3]})
    p.check(f"The history of run #{second} holds load_costs, costs_total, the Merger and report, and no other block",
            set(history(win, lambda h: h.blocks(second))) == {*LINE_2, "merge", "report"})
    counts = {b: runs_of(folder, b) for b in (*LINE_1, *LINE_2, *LINE_3, "report")}
    p.check("Each block's own count of its runs: load_sales 1, load_costs 2, costs_total 1, load_stock 1, report 1",
            counts == {"load_sales": 1, "load_costs": 2, "costs_total": 1, "load_stock": 1, "report": 1})
    p.check("report made 175: 245 of sales (from run #1) minus 70 of costs", output_of(tab, "report") == 175)

    rerun = [(log, f"run #{second} · "), (log, "running from load_costs on"), (log, "[load_costs] INFO runs so far: 2"),
             (log, "[costs_total] INFO runs so far: 1"), (log, "[report] INFO runs so far: 1"),
             (log, f"run #{second} success")]
    p.shot(win, f"The Log of run #{second}: it runs from load_costs on, with the inputs load_costs got in run "
                f"#{first}. In the outlined lines load_costs says it has now run twice, costs_total and report once. "
                "No line from load_sales or load_stock: they did not run.",
           highlight=[rerun[2:5]], zoom=rerun)
    text = log.toPlainText().split(f"run #{second} · ")[-1]
    p.check(f"The Log says run #{second} runs from load_costs on, with the inputs it got in run #{first}",
            f"running from load_costs on (it and the blocks after it), with the inputs it got in run #{first}" in text)
    p.check(f"In run #{second}, load_costs says “runs so far: 2”, costs_total and report “runs so far: 1”; "
            "load_sales and load_stock say nothing",
            all(f"[{b}] INFO runs so far: {n}" in text for b, n in (("load_costs", 2), ("costs_total", 1),
                                                                    ("report", 1)))
            and "[load_sales]" not in text and "[load_stock]" not in text)

    p.step("Click merge, then the Data preview tab, under the canvas.")
    click_block(tab, "merge")
    click_bottom_tab(win, win.preview)
    preview = win.preview
    lines = [(preview.text, line.strip()) for line in preview.text.toPlainText().splitlines() if line.strip()]
    p.shot(win, f"What the Merger passed on in run #{second}: each line's output, by its last block's name: load_sales' "
                f"[120, 80, 45] and load_stock's 37, kept from run #{first}, and costs_total's 70, made now.",
           highlight=[[preview.label, *lines]], zoom=[preview.label, *lines])
    try:
        merged = json.loads(preview.text.toPlainText())
    except ValueError:
        merged = None
    p.check("The Merger passed on {load_sales: [120, 80, 45], costs_total: 70, load_stock: 37}",
            preview.label.text() == "merge.value:"
            and merged == {"load_sales": [120, 80, 45], "costs_total": 70, "load_stock": 37})

    p.step("Click the Run history tab, under the canvas.")
    click_bottom_tab(win, win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 2, timeout=10_000)
    rows = [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]
    p.shot(win, f"Run #{second}, started from load_costs, succeeded; run #{first}, started by start_by_hand, failed.",
           highlight=[[table.item(0, 2), table.item(0, 3)]], zoom=[table.item(0, 0), table.item(1, 4)])
    p.check(f"Run history: run #{second}, from load_costs, success; run #{first}, start_by_hand, failed",
            [(r[0], r[2], r[3]) for r in rows] == [(str(second), "from load_costs", "success"),
                                                    (str(first), "start_by_hand", "failed")])
    click_bottom_tab(win, win.log)

    # --- Stop -----------------------------------------------------------------------------------------------------
    p.step("Open the flow two-waits.yaml (File > Open…) and press ▶ Run now. Its Splitter starts two lines at the same "
           "time, each a Wait block of 30 seconds.")
    stop_tab = open_flow(win, monkeypatch, two_waits(folder))
    nodes = stop_tab.scene.nodes
    press(win, win.run_action)
    qtbot.waitUntil(lambda: nodes["wait_north"].status == "running" and nodes["wait_south"].status == "running",
                    timeout=30_000)
    QTest.qWait(1500)  # their countdowns are going
    p.shot(win, "Both lines run at once: ① wait_north and ② wait_south are running, each counting down its 30 "
                "seconds.",
           highlight=["wait_north", "wait_south"], badges=True, zoom=list(nodes))
    p.check("① wait_north and ② wait_south are both running",
            statuses(stop_tab, ["wait_north", "wait_south"]) == {"wait_north": "running", "wait_south": "running"})

    p.step("Click Stop in the toolbar.")
    stopped = dt.datetime.now()
    press(win, win.stop_action)
    third = wait_runs(qtbot, stop_tab, 1)
    QTest.qWait(300)
    p.shot(win, "Both lines ended at once: ① wait_north and ② wait_south are cancelled, and so is ③ the Merger, "
                "which was waiting for them.",
           highlight=["wait_north", "wait_south", "merge"], badges=True, zoom=list(nodes))
    ended = times(win, third)
    p.check("① wait_north, ② wait_south and ③ the Merger are cancelled",
            statuses(stop_tab, ["wait_north", "wait_south", "merge"])
            == dict.fromkeys(("wait_north", "wait_south", "merge"), "cancelled"))
    p.check("① and ② ended within 5 seconds of Stop, long before their 30 seconds, and within a second and a half of "
            "each other",
            all((ended[b][1] - stopped).total_seconds() < 5 for b in ("wait_north", "wait_south"))
            and abs((ended["wait_north"][1] - ended["wait_south"][1]).total_seconds()) < 1.5)
    stopped_run = [(log, f"run #{third} · "), (log, "[split] INFO 2 lines at the same time"),
                   (log, "[wait_north] INFO waiting 30 s"), (log, "[wait_south] INFO waiting 30 s"),
                   (log, f"run #{third} cancelled")]
    p.shot(win, f"The Log of run #{third}: split started its two lines at the same time, both began their 30 "
                "seconds; at Stop, the run was cancelled.",
           highlight=[stopped_run[-1]], zoom=stopped_run)
    p.check(f"Run #{third} was cancelled, and the Log says so", stop_tab.runs[0].status == "cancelled"
            and f"ERROR run #{third} cancelled" in log.toPlainText())


# --- 2: Validate refuses a layout that could never finish ------------------------------------------------------------

def test_validate_refuses_a_layout_that_could_never_finish_naming_the_connection(proof, win, qtbot, tmp_path,
                                                                                monkeypatch):
    p = proof("Validate refuses a layout that could never finish, naming the connection", ASKED)
    p.idea("A line may use what another line makes: its block waits until that has arrived. When a Splitter runs its "
           "lines one after another, line 1 runs to its end before line 2 starts, and so on. So a connection from a "
           "later line into the block an earlier line starts with could never be served: line 1 would wait for line "
           "3, which cannot start until line 1 has ended.")
    p.idea("As agreed with you, Validate (F7) now refuses such a layout before any run, naming the connection and "
           "the two lines, and ▶ Run now refuses to start with the same message. With the lines at the same time and "
           "no At most at once, every line starts at once: the same connection is then fine, and the run ends.")
    p.idea("As you chose for this release, two layouts are not supported yet, and Validate refuses each, naming it: "
           "a Splitter inside another Splitter's line, and two Splitters side by side whose lines feed each other "
           "both ways. One way only is fine. (A block that takes from the lines of two Splitters side by side is "
           "refused too.)")
    p.why_simplest("Validate already checks a flow before every run. This is one more look at the same drawing: which "
                   "line each block runs in, and how its Splitter runs its lines. A block that several lines lead to "
                   "runs in the last of them, so it only waits for lines that start before its own: they are running "
                   "or done. The block a line starts with cannot move: it is where its line begins. So the connection "
                   "that could wait forever is one from a later line into an earlier line's first block, when the "
                   "lines do not all start at once, and that is the one Validate refuses.")
    p.why_simplest("The message is what you need to fix it: the connection as the flow file writes it, the line "
                   "numbers the canvas shows, why it would wait forever, and the two ways out.")
    p.alternative("Let the run start, and end it if it waits too long",
                  "Part of the flow would already have run (a file written, an email sent) before the run got stuck, "
                  "and you would learn of it only once the wait ran out: the next morning, for a run the scheduler "
                  "started at night. Validate says so before anything runs, and points at the connection.")
    p.alternative("Run the lines in another order by itself, so the line that makes the data goes first",
                  "You chose the order: top to bottom, as you see the lines. A run that quietly changed it would "
                  "surprise you, and the numbers on the canvas would no longer say what happens.")
    p.alternative("Allow no connection between lines at all",
                  "You asked for the 2nd line to connect with the 1st. Only the connections that could wait forever "
                  "are refused; the others work, in every mode.")
    p.alternative("Support a Splitter inside another Splitter's line now",
                  "Lines inside lines make it much harder to say which line runs a block and what waits for what. "
                  "Rather than a layout that might hang, it is refused before it runs, with where to put the Splitter "
                  "instead: after the outer Splitter's Merger.")
    p.fits("Validate (F7), Tools > Check flows, and taskloom validate and taskloom check in a terminal all list it; "
           "every run checks for it before it starts, the scheduler's too, as for any problem in the blocks it runs.")
    p.fits("The line numbers in the message are the ones the canvas draws on the Splitter's connections when it runs "
           "its lines one after another: 1, 2, 3, top to bottom by where the block each line starts with sits.")
    p.fits("The connection is named as the flow file writes it, as Validate names other connection problems.")
    folder = tmp_path / "flows"
    folder.mkdir()

    p.step("Open the flow never-finish.yaml (File > Open…). Its Splitter, split, runs its three lines one after "
           "another: load_sales, load_costs, then load_stock.")
    tab = open_flow(win, monkeypatch, never_finish(folder))
    p.check("The flow opens", tab is not None and tab.doc.path.name == "never-finish.yaml")
    p.shot(win, "The canvas numbers the connections leaving split 1, 2, 3: the order the lines run in, top to bottom "
                "by where the block each starts with sits. ① load_stock's output, at the end of line 3, goes up into "
                "load_sales, which line 1 starts with.",
           highlight=["load_stock", "load_sales"], badges=[1, 1], zoom=list(tab.doc.blocks))
    numbers = {e.text.split(" -> ")[1].split(".")[0]: e.number.number for e in tab.scene.edge_items
               if e.text.startswith("split.lines -> ") and e.number is not None}
    p.check("The connections leaving split are numbered 1 (load_sales), 2 (load_costs), 3 (load_stock)",
            numbers == {"load_sales": "1", "load_costs": "2", "load_stock": "3"})
    refused = (f"edge {NEVER}: from line 3 of split into line 1, which would wait for it forever: split runs its "
               "lines one after another (connect a line only into a later one, or run all its lines at once)")

    p.step("Press Validate in the toolbar (F7).")
    said = boxes_said(p, monkeypatch, lambda: press(win, win.validate_action),
                      "The flow has problems: the connection load_stock.output -> load_sales.stock goes from line 3 "
                      "of split into line 1, which would wait for it forever, as split runs its lines one after "
                      "another. Then the two ways out.")
    p.check(f"Validate names the connection, and only it: “{refused}”", said == [refused])

    p.step("Press ▶ Run now in the toolbar.")
    said = boxes_said(p, monkeypatch, lambda: press(win, win.run_action),
                      "The run is refused before it starts, with the same message.")
    p.check("▶ Run now is refused with the same message", said == [refused])
    p.check("No run was started or recorded", tab.runs == [] and history(win, lambda h: h.runs()) == [])

    p.step("Click split, and in Properties choose “at the same time” for Lines run, leaving At most at once empty.")
    click_block(tab, "split")
    choose_in_list(p, field(win, "Lines run"), SAME_TIME)
    lines_run, at_most = field(win, "Lines run"), field(win, "At most at once")
    rows = [[label(win, "Lines run"), lines_run], [label(win, "At most at once"), at_most]]
    p.shot(win, "① Lines run is now at the same time, and ② At most at once is empty: every line starts at once.",
           highlight=rows, badges=True, zoom=rows)
    p.check("① Lines run says at the same time; ② At most at once is empty",
            lines_run.currentText() == SAME_TIME and at_most.text() == ""
            and (tab.doc.blocks["split"].get("config") or {}).get("lines_run", SAME_TIME) == SAME_TIME)
    p.check("split's connections carry no numbers any more", all(e.number is None for e in tab.scene.edge_items))

    p.step("Press Validate (F7) again.")
    press(win, win.validate_action)
    QTest.qWait(200)
    message, showing = status_message(win)
    p.shot(win, f"No box this time: the status bar, at the bottom, says “{message}”.", highlight=[showing],
           zoom=[showing])
    p.check("Validate finds no problem: no box, and the status bar says so",
            win.message_boxes == [] and message.endswith("no problems found"))

    p.step("Press ▶ Run now.")
    press(win, win.run_action)
    run = wait_runs(qtbot, tab, 1)
    settled(win, qtbot)
    p.shot(win, f"Run #{run} ran to its end, all green: ① load_sales waited for ② load_stock's output, and made "
                "[245, 37]. split's connections carry no numbers now: its lines start together.",
           highlight=["load_sales", "load_stock"], badges=True, zoom=list(tab.doc.blocks))
    ran = times(win, run)
    p.check(f"Run #{run} succeeded", tab.runs[0].status == "success"
            and set(statuses(tab, tab.doc.blocks).values()) == {"success"})
    p.check("① load_sales started once ② load_stock had finished, and made [245, 37]",
            ran["load_sales"][0] >= ran["load_stock"][1]
            and output_of(tab, "load_sales") == [245, 37])
    p.note("With an At most at once lower than the number of lines, Validate refuses every connection from a later "
           "line into the block an earlier line starts with, even when that layout might finish (with at most 2 of "
           "3 lines at once, lines 1 and 2 start together, so line 1 could wait for line 2). Raise the limit, or "
           "connect the lines the other way.")

    # --- not supported yet --------------------------------------------------------------------------------------
    p.step("Open the flow nested.yaml (File > Open…). Line 1 of split is regions, another Splitter, with its own "
           "lines north and south and its own Merger.")
    tab = open_flow(win, monkeypatch, nested(folder))
    inner = ["regions", "north", "south", "regions_merge"]
    p.shot(win, "① regions, a Splitter inside line 1 of split.", highlight=[inner], badges=True,
           zoom=list(tab.doc.blocks))

    p.step("Press Validate (F7).")
    said = boxes_said(p, monkeypatch, lambda: press(win, win.validate_action),
                      "The flow has problems: regions is a Splitter inside line 1 of split, which is not supported "
                      "yet; put it after split's Merger.")
    p.check("Validate says “regions: a Splitter inside line 1 of split: a Splitter inside another Splitter's line is "
            "not supported yet (put it after split's Merger)”, and nothing else",
            said == ["regions: a Splitter inside line 1 of split: a Splitter inside another Splitter's line is not "
                     "supported yet (put it after split's Merger)"])

    p.step("Open the flow side-by-side.yaml (File > Open…). Two Splitters side by side, sales and costs, each with "
           "two lines and its Merger.")
    tab = open_flow(win, monkeypatch, side_by_side(folder))
    p.shot(win, "The lines feed each other both ways: ① north_sales' output goes into north_costs, which a line of "
                "costs starts with; ② south_costs' output goes into south_sales, which a line of sales starts with.",
           highlight=["north_sales", "north_costs", "south_costs", "south_sales"], badges=[1, 1, 2, 2],
           zoom=list(tab.doc.blocks))

    p.step("Press Validate (F7).")
    said = boxes_said(p, monkeypatch, lambda: press(win, win.validate_action),
                      "The flow has problems: the lines of the two Splitters feed each other both ways, through the two "
                      "connections it names, one each way, so they could wait for each other forever.")
    p.check(f"Validate refuses it, naming a connection each way: {ONE_WAY} and {OTHER_WAY}",
            len(said) == 1 and "\n" not in said[0] and "feed each other both ways" in said[0]
            and f"edge {ONE_WAY}" in said[0] and f"edge {OTHER_WAY}" in said[0])

    p.step("Click the connection from south_costs to south_sales, and press Delete.")
    delete_connection(win, tab, qtbot, OTHER_WAY)
    p.shot(win, "One way only is left: ① north_sales' output into north_costs.",
           highlight=["north_sales", "north_costs"], badges=[1, 1], zoom=list(tab.doc.blocks))
    p.check("The connection is gone; the other way is still there",
            OTHER_WAY not in tab.doc.data["edges"] and ONE_WAY in tab.doc.data["edges"])

    p.step("Press Validate (F7) again.")
    press(win, win.validate_action)
    QTest.qWait(200)
    message, showing = status_message(win)
    p.shot(win, f"No box: the status bar says “{message}”.", highlight=[showing], zoom=[showing])
    p.check("Validate finds no problem", win.message_boxes == [] and message.endswith("no problems found"))

    p.step("Press ▶ Run now.")
    press(win, win.run_action)
    run = wait_runs(qtbot, tab, 1)
    settled(win, qtbot)
    p.shot(win, f"Run #{run} ran to its end, all green: ① north_costs waited for ② north_sales' 120, and made "
                "[30, 120].",
           highlight=["north_costs", "north_sales"], badges=True, zoom=list(tab.doc.blocks))
    ran = times(win, run)
    p.check(f"Run #{run} succeeded, every block green", tab.runs[0].status == "success"
            and set(statuses(tab, tab.doc.blocks).values()) == {"success"})
    p.check("① north_costs started once ② north_sales had finished, and made [30, 120]",
            ran["north_costs"][0] >= ran["north_sales"][1] and output_of(tab, "north_costs") == [30, 120])
