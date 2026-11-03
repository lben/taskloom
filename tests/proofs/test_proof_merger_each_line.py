"""Proofs: the Merger's "each line as it finishes": the blocks after it run once for each line, as soon as that line
has finished, in the order the lines finished, up to a Merger set to all lines together, which closes the runs; a
failed line and a failed run, running again from the Merger in the order its Runs again in says, and what Validate
says. How proofs are written: README.md here."""

import datetime as dt
import json
import re
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_canvas import open_flow
from proofs.test_proof_docs_help import card_parts, card_says, click_block
from proofs.test_proof_history_selection import row_of
from proofs.test_proof_line_failures import (boxes_said, choose_in_list, click_bottom_tab, delete_connection,
                                             status_message)
from proofs.test_proof_loop_failures import rest_on, scroll_log, under
from proofs.test_proof_result_time import hover as hover_result
from proofs.test_proof_run_output import setting
from proofs.test_proof_run_scope import press
from proofs.test_proof_scheduler_start import click_empty
from proofs.test_proof_tab_plus import set_theme
from taskloom.editor import theme
from taskloom.history import History
from tests.test_docs_help import dismiss, hover as hover_setting
from tests.test_inspect_mermaid import contrast
from tests.test_lines import click_row
from tests.test_loop_container import render
from tests.test_merger_each_line import merger_progress
from tests.test_parallel_lines import drawn_texts, row_shown
from tests.test_paths_editor_editing import clean, field, flow_file
from tests.test_theme import TEXT, most_common

ASKED = ("Then the merger block can support \"join\" that waits for all the inputs to finish before proceeding or can "
         "let the output of any line go out as soon as the line finishes.")
ORDER_ASKED = ("show the 2 possibilities so that the user can choose if he wants in the canvas order (default) or the "
               "order as the finished if needed")
EACH, ALL = "each line as it finishes", "all lines together"
LINE_ORDER, AS_FINISHED = "line order", "the order the lines finished"
FROM_HERE = "Run from this block on, with the same inputs"
NOT_OFFERED = ["Run only this block, with the same inputs", "Run only this block, with inputs I choose…",
               "Run from this block on, with the same inputs", "Run from this block on, with inputs I choose…"]
BLOCKS_WIDTH, INSPECTOR_WIDTH, LOG_HEIGHT = 180, 420, 330  # a wider canvas, and a taller Log
LOG_ZOOM = 760  # how much of a long Log line a close-up shows, from its start (in pixels)
MONTH = "2026-10"
SECONDS = {"sales": 1.5, "costs": 0.3, "stock": 0.8}  # how long each line's block loads
AMOUNTS = {"sales": 245, "costs": 150, "stock": 37}
REPORT_SECONDS = 0.8  # how long report works on each line
LINES = ["load_sales", "load_costs", "load_stock"]  # top to bottom on the canvas: line order
FINISHED = ["load_costs", "load_stock", "load_sales"]  # the order they finish in (SECONDS)
LAYOUTS = {"sales": "Sales of {month}: {amount}", "costs": "Costs of {month}: {amount}",
           "stock": "Stock at the end of {month}: {amount}"}
RESULTS = {f"load_{what}": f"{what} {amount}" for what, amount in AMOUNTS.items()}  # what gather passes on
SUMMARY = "sales 245, costs 150, stock 37"
TICK = dt.timedelta(seconds=0.016)  # a clock tick on Windows: two times taken in a row may differ by this much


# --- the flows --------------------------------------------------------------------------------------------------------

def python(inputs: list[str], code: str, x: int, y: int) -> dict:
    return {"type": "logic.python", "config": {"inputs": inputs, "code": code}, "ui": {"x": x, "y": y}}


def load(what: str) -> str:
    """A line's block: it says in the Log what it loads, works for SECONDS[what], and passes on what it loaded. The
    costs come from costs.csv, in the flow's folder: until it is there, load_costs fails."""
    amount = f"{AMOUNTS[what]}" if what != "costs" else "int(Path('costs.csv').read_text(encoding='utf-8'))"
    check = ("if not Path('costs.csv').exists():\n    raise FileNotFoundError('costs.csv has not arrived')\n"
             if what == "costs" else "")
    return (f"import time\nfrom pathlib import Path\nprint('loading {what} of', month)\n"
            f"time.sleep({SECONDS[what]})\n{check}"
            f"output = {{'what': '{what}', 'month': month, 'amount': {amount}}}")


# For each line: it works for REPORT_SECONDS, then fills the line's layout (layouts/<what>.txt; until it is there, it
# fails) and writes the report to reports/<what>.txt: one file per line, its name made from what the line gave.
REPORT = (f"import time\nfrom pathlib import Path\nprint('report of', input['what'])\ntime.sleep({REPORT_SECONDS})\n"
          "layout = Path('layouts') / f\"{input['what']}.txt\"\n"
          "if not layout.exists():\n    raise FileNotFoundError(f\"no layout for the {input['what']} report\")\n"
          "Path('reports').mkdir(exist_ok=True)\n"
          "Path(f\"reports/{input['what']}.txt\").write_text(layout.read_text(encoding='utf-8').format(**input),\n"
          "                                                encoding='utf-8')\n"
          "print(f\"wrote reports/{input['what']}.txt\")\n"
          "output = f\"{input['what']} {input['amount']}\"")
SUMMARIZE = "output = ', '.join(reports.values())"


def month_end_blocks(passes_on: str | None) -> tuple[dict, list[str]]:
    """start_by_hand -> pick_month -> split, whose three lines are load_sales, load_costs and load_stock -> merge
    (Passes on as given; None: as the flow file leaves it, all lines together) -> report -> gather (all lines
    together) -> summary."""
    merge = {"type": "logic.merger", "ui": {"x": 690, "y": 170}}
    if passes_on is not None:
        merge["config"] = {"passes_on": passes_on}
    return {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
        "pick_month": python([], f"output = {MONTH!r}", 0, 170),
        "split": {"type": "logic.splitter", "ui": {"x": 230, "y": 170}},
        "load_sales": python(["month"], load("sales"), 460, 0),
        "load_costs": python(["month"], load("costs"), 460, 170),
        "load_stock": python(["month"], load("stock"), 460, 340),
        "merge": merge,
        "report": python(["input"], REPORT, 920, 170),
        "gather": {"type": "logic.merger", "ui": {"x": 1150, "y": 170}},
        "summary": python(["reports"], SUMMARIZE, 1150, 340),
    }, ["start_by_hand.start -> pick_month.after", "pick_month.output -> split.value",
        *(f"split.lines -> {b}.month" for b in LINES), *(f"{b}.output -> merge.lines" for b in LINES),
        "merge.value -> report.input", "report.output -> gather.lines", "gather.value -> summary.reports"]


def month_end(folder: Path, passes_on: str | None) -> Path:
    return flow_file(folder / "month-end.yaml", *month_end_blocks(passes_on))


TAKES_A_RUN = "report.output -> summary.latest"  # summary runs once, after gather: refused


def check_runs(folder: Path) -> Path:
    """month-end's blocks with merge set to each line as it finishes, and: rows (a one-row table of each line) ->
    save_rows, a Write File to out/rows.csv; summary also takes report's output (TAKES_A_RUN)."""
    blocks, edges = month_end_blocks(EACH)
    blocks["rows"] = python(["input"], "output = pl.DataFrame({'line': [input['what']], 'amount': [input['amount']]})",
                            920, 0)
    blocks["save_rows"] = {"type": "data.write_file", "config": {"path": "out/rows.csv"}, "ui": {"x": 1150, "y": 0}}
    blocks["summary"]["config"]["inputs"] = ["reports", "latest"]
    return flow_file(folder / "check-runs.yaml", blocks, [*edges, "merge.value -> rows.input",
                                                          "rows.output -> save_rows.table", TAKES_A_RUN])


def put_layouts(folder: Path, *whats: str):
    (folder / "layouts").mkdir(exist_ok=True)
    for what in whats:
        (folder / "layouts" / f"{what}.txt").write_text(LAYOUTS[what], encoding="utf-8")


def report_file(folder: Path, what: str) -> Path:
    return folder / "reports" / f"{what}.txt"


def written(folder: Path, what: str) -> bool:
    """Whether report wrote that line's report, with what that line gave."""
    path = report_file(folder, what)
    return path.exists() and path.read_text(encoding="utf-8") == LAYOUTS[what].format(month=MONTH,
                                                                                      amount=AMOUNTS[what])


# --- doing it as you would, and what the app shows ------------------------------------------------------------------

def make_room(win):
    """Drag the Blocks list's right edge and the panels' left edge in, and the Log's top edge up."""
    win.resizeDocks([win.blocks_dock, win.inspector], [BLOCKS_WIDTH, INSPECTOR_WIDTH], Qt.Horizontal)
    win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)
    QTest.qWait(100)


def reaches(qtbot, condition, seconds: float = 30) -> bool:
    """Whether `condition()` came true within `seconds`."""
    try:
        qtbot.waitUntil(condition, timeout=int(seconds * 1000))
    except qtbot.TimeoutError:
        return False
    return True


def run_ended(qtbot, tab, runs: int) -> int | None:
    """Wait for the tab's run number `runs` (of this proof, a refused one too) to end: its number in the run
    history (None: it was refused before it started)."""
    qtbot.waitUntil(lambda: len(tab.runs) == runs and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)
    QTest.qWait(300)  # what the run printed last reaches the Log
    return tab.runs[-1].run_id


def running_for(tab, block: str) -> set[str]:
    """The lines a block run once per line is running for now, as the canvas has them ([line load_costs], …)."""
    return set(tab.item_runs.get(block, set())) if tab.scene.nodes[block].status == "running" else set()


def counter_contrast(tab, block: str = "merge") -> float:
    """The contrast of the Merger's counter (done/N · k running) with what is behind it, where the canvas paints it
    (measured as the tests of the Loop measure its counter)."""
    texts = drawn_texts(tab.scene, tab.scene.nodes[block].sceneBoundingRect())
    t = next(t for t in texts if re.search(r"\d+/\d+ · \d+ running", t["text"]))
    s = max(t["size"], 6.0)
    cx, cy = t["center"]
    behind = most_common(render(tab.scene, QRectF(cx - 2 * s, cy - 2 * s, 4 * s, 4 * s), 2.0))
    ink, alpha = QColor(t["fill"]), t["opacity"]
    shown = QColor(*(round(alpha * f + (1 - alpha) * b) for f, b in zip(ink.getRgb()[:3], behind.getRgb()[:3])))
    return contrast(shown, behind)


def kept(win, read):
    """What read(history) reads of the run history the editor keeps."""
    history = History(win.home.history_db)
    try:
        return read(history)
    finally:
        history.close()


def ends(win, run_id: int) -> dict[str, dt.datetime]:
    """When each block ended in run `run_id`, as the run history keeps it."""
    return {b: dt.datetime.fromisoformat(r["finished"]) for b, r in kept(win, lambda h: h.blocks(run_id)).items()
            if r["finished"]}


def log_of(win, run_id: int) -> list[tuple[str, str, dt.datetime]]:
    """Run `run_id`'s Log lines as the run history keeps them: (block, message, when)."""
    return [(line["block"] or "", line["message"] or "", dt.datetime.fromisoformat(line["ts"]))
            for line in kept(win, lambda h: h.logs(run_id))]


def report_spans(lines) -> dict[str, tuple[dt.datetime, dt.datetime]]:
    """{line: (when report began its run for it, when that run ended: it wrote the report, or it failed)}, from
    report's Log lines."""
    found = {}
    for block, message, ts in lines:
        m = re.match(r"\[line (\w+)\] (report of|wrote|report \(Python Code\) failed)", message)
        if block == "report" and m:
            found.setdefault(m.group(1), {})["began" if m.group(2) == "report of" else "ended"] = ts
    return {line: (said["began"], said["ended"]) for line, said in found.items() if len(said) == 2}


def arrived(lines) -> list[str]:
    """The lines in the order the Merger's Log says they arrived."""
    return [m.split(" arrived")[0] for b, m, _ in lines if b == "merge" and " arrived: " in m]


def reported(lines) -> list[str]:
    """The lines in the order report began its runs for them."""
    return [m[len("[line "):].split("]")[0] for b, m, _ in lines if b == "report" and "] report of " in m]


def timetable(win, run_id: int) -> str:
    """When each line ended and when report ran for it, in seconds from the run's first line."""
    lines = log_of(win, run_id)
    zero, ended, spans = lines[0][2], ends(win, run_id), report_spans(lines)
    rows = [f"{b:12}{'ended at':>21} {(ended[b] - zero).total_seconds():5.2f} s" for b in LINES]
    rows += [f"{'report':12}{'for ' + line:>16}: {(a - zero).total_seconds():5.2f} s to "
             f"{(b - zero).total_seconds():5.2f} s" for line, (a, b) in sorted(spans.items(), key=lambda kv: kv[1][0])]
    return "\n".join(rows)


def card_of(qtbot, win, label: str):
    return hover_setting(qtbot, win, field(win, label), label, f"Merger › {label}")


def settings_rows(win, *labels: str) -> list:
    """The rows of Properties with these labels, each its name and its field."""
    return [list(setting(win, label)) for label in labels]


def value_of(tab, block: str, port: str):
    """What the canvas shows a block's output held: a list or a dict as such, a text as it is (None: nothing)."""
    shown = tab.scene.values.get(block, {}).get(port)
    try:
        return json.loads(shown) if shown is not None else None
    except ValueError:
        return shown


def log_lines_of(log, texts: list[str]) -> list:
    """The Log's lines that show each of `texts` (each shown once in it), to outline each on its own."""
    return [(log, text) for text in texts]


def in_log(win, texts: list[str], after: str | None = None, last: bool = False, width: int = LOG_ZOOM,
           inset: int = 0) -> QRect:
    """The Log's lines that show `texts` (each the first such line after the last line showing `after`; `last`: the
    last line showing it), from their start and at most `width` pixels wide, in the window's pixels: to outline them
    together, or show them close up. `inset`: that much inside them, top and bottom (an outline drawn around them
    then goes between them and the lines next to them)."""
    box = win.log.text
    shown = box.toPlainText()
    start = shown.rfind(after) if after is not None else 0
    assert start >= 0, f"no line of the Log shows {after!r}"
    area = QRect()
    for text in texts:
        at = shown.rfind(text) if last else shown.find(text, start)
        assert at >= 0, f"no line of the Log shows {text!r}"
        line = QTextCursor(box.document())
        line.setPosition(at)
        line.movePosition(QTextCursor.StartOfLine)
        area |= box.cursorRect(line)
        line.movePosition(QTextCursor.EndOfLine)
        area |= box.cursorRect(line)
    area.setWidth(min(area.width(), width))
    return area.adjusted(-3, inset, 3, -inset).translated(box.viewport().mapTo(win, QPoint(0, 0)))


def merger_lines(lines) -> str:
    """What the Merger wrote in the Log in a run, as the run history keeps it."""
    return "\n".join(m for b, m, _ in lines if b == "merge")


def hide_tip(tab):
    QTest.mouseMove(tab.view.viewport(), tab.view.viewport().rect().topLeft())
    QToolTip.hideText()


def restore_look(win, app, as_found):
    """The Theme and the application's look as the proof found them; no run left going."""
    for run_ in [r for t in win.tabs_list() for r in t.runs if r.running]:  # (a proof that failed mid-run)
        run_.proc.kill()
        run_.proc.waitForFinished(5000)
    win.settings.setValue("theme", "System")
    theme.apply("System")
    if app.style().name() != as_found[0]:
        QApplication.setStyle(as_found[0])
    QApplication.setPalette(as_found[1])
    QToolTip.setPalette(as_found[2])
    QApplication.processEvents()
    clean(win)


# --- 1: each line as it finishes --------------------------------------------------------------------------------------

def test_the_blocks_after_the_merger_run_once_per_line_as_soon_as_each_line_finishes(
        proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("The Merger's each line as it finishes: the blocks after it run once per line, as each line finishes",
              ASKED)
    p.idea("The Merger has a second way to pass its lines on. Its setting Passes on says which: all lines together "
           "(as the block comes, and as before: it waits for every line, then passes on {line name: that line's "
           "output} once) or each line as it finishes: as soon as a line has finished, the Merger passes that line's "
           "output on, the value itself, and the blocks after it run once for that line, while the other lines "
           "still run.")
    p.idea("As agreed with you, the blocks after it run once per line the way a Loop runs the blocks inside it once "
           "per item. At the same time (1 as it comes) says how many of those runs go at once; as soon as one ends, "
           "the next starts, in the order the lines finished. Each run gets its own copy of what it receives. While "
           "it runs, the Merger shows “done/3 · 1 running” under it; each Log line of a block run for a line says "
           "which line, as [line load_costs]; the Merger writes a line as each line arrives (“load_costs arrived: "
           "its run starts”). Afterwards each block it runs says how its runs ended (✓ 3), and resting the mouse on "
           "it shows the last run's result, naming its line.")
    p.idea("The runs go up to the end of the flow, or up to a Merger set to all lines together further on. That "
           "Merger closes them: it waits for every one of those runs and passes on {line name: what that run gave "
           "it}, once, to the blocks after it. Here gather closes the runs of report, and summary gets each line's "
           "report under the line's name.")
    p.why_simplest("Nothing new to learn: one choice on the Merger you already use, and the runs after it behave as a "
                   "Loop's items do: the same At the same time, the same sliding window, the same counter, the same "
                   "kind of tags in the Log, the same ✓ 3 under each block. Each line's run is a small run of the "
                   "blocks after the Merger, by the rules a flow's blocks always follow: in the flow's order, a "
                   "failure stops the blocks after it in that run, an error output is the way out.")
    p.why_simplest("Where the runs end is drawn, not set: they are the blocks after the Merger, up to the end of the "
                   "flow or up to a Merger set to all lines together, which joins the runs as it joins lines. No end "
                   "marker, no list of blocks to keep up to date.")
    p.why_simplest("It all happens in the one run: one Log, one row in Run history, one Stop for everything.")
    p.alternative("A block of its own (“as each finishes”) next to the Merger",
                  "Two blocks would then sit where the lines come together, doing nearly the same thing. You asked for "
                  "the Merger to do either: one block with a choice keeps it in one place, and switching from one way "
                  "to the other is one click.")
    p.alternative("Copy the blocks that follow into each line",
                  "Each line would then hold its own report block, and every change to it would have to be made "
                  "three times. The lines would not share one place where their results come together.")
    p.alternative("Start the runs in line order, top to bottom",
                  "A slow line at the top would hold up every run after it, which is what you wanted to avoid: “as "
                  "soon as the line finishes”. In the order the lines finish, a line's run never waits for a slower "
                  "line.")
    p.alternative("Each line's run as a run of its own in Run history",
                  "The Log and Run history would be split up, Stop would have to find every one of them, and nothing "
                  "could wait for all of them to close the runs. The Loop already runs part of a flow once per item "
                  "inside one run; the Merger does the same, per line.")
    p.fits("It is the Loop's way of running blocks again and again, used for lines: At the same time has the same "
           "name and meaning, the Merger's counter reads like the Loop's (done/N · k running), the Log tags runs as "
           "[line load_costs] where a Loop tags them [item 2/5], and the blocks show ✓ 3 as the blocks inside a Loop "
           "do.")
    p.fits("The Splitter is unchanged: its lines run at the same time or one after another, as it says. The Merger "
           "set to all lines together joins as before, and a flow file that does not say how a Merger passes its "
           "lines on keeps it joining them.")
    p.fits("In Properties, At the same time and Runs again in show only when Passes on is each line as it finishes; "
           "the hover help of each row says what it does, as for every setting.")

    folder = tmp_path / "flows"
    folder.mkdir()
    (folder / "costs.csv").write_text(f"{AMOUNTS['costs']}\n", encoding="utf-8")
    put_layouts(folder, *AMOUNTS)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings writes Windows' Startup script there)
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    log = win.log.text
    try:
        # --- 1. The setting -------------------------------------------------------------------------------------------
        p.step("Open the flow month-end.yaml (File > Open…). start_by_hand runs pick_month, which gives the month to "
               "the Splitter split. Its three lines, top to bottom: load_sales, load_costs and load_stock. The Merger "
               "merge takes them to report, which writes each line's report; the Merger gather then passes them on to "
               "summary.")
        p.note(f"load_sales works for about {SECONDS['sales']:g} seconds, load_costs for {SECONDS['costs']:g} and "
               f"load_stock for {SECONDS['stock']:g}: the lines finish in another order than top to bottom. report "
               f"works for {REPORT_SECONDS:g} seconds on each line, then writes reports/sales.txt, reports/costs.txt or "
               "reports/stock.txt, named after what the line gave it. For this proof, the Blocks list and the panels "
               "on the right are made narrower and the Log taller (their edges dragged).")
        make_room(win)
        tab = open_flow(win, monkeypatch, month_end(folder, None))
        p.check("The flow opens", tab is not None and tab.doc.path.name == "month-end.yaml")
        p.shot(win, "The flow: ① the Splitter split and its three lines; ② the Merger merge; ③ report, after it; ④ the "
                    "Merger gather and summary.",
               highlight=[["split", *LINES], "merge", "report", ["gather", "summary"]], badges=True,
               zoom=list(tab.doc.blocks))

        p.step("Click merge, and look at Properties.")
        click_block(win, "merge")
        rows = settings_rows(win, "Passes on")
        p.shot(win, f"① Passes on says {ALL}, as the flow file leaves it: the Merger joins its lines, as before. No At "
                    "the same time is shown.",
               highlight=rows, badges=True, zoom=[*rows[0], "merge"])
        p.check(f"Passes on shows {ALL}, and Properties shows no At the same time and no Runs again in",
                field(win, "Passes on").currentText() == ALL and not row_shown(win, "At the same time")
                and not row_shown(win, "Runs again in"))

        p.step("Rest the mouse on Passes on.")
        card = card_of(qtbot, win, "Passes on")
        label, what, values, _ = card_says(card)
        parts = card_parts(win, card)
        p.shot(card, f"The card of Passes on: ① {ALL} waits for every line, then passes on {{line name: that line's "
                     f"output}} once; {EACH} passes on each line's output as soon as that line has finished, and the "
                     "blocks after it run once for that line. ② Its two choices.",
               over=win, highlight=parts[:2], badges=True, zoom=[card, *parts[:2]])
        p.check("① The card tells both ways",
                label == "Passes on" and f"{ALL}: it waits for every line" in what
                and f"{EACH}: it passes on each line's output as soon as that line has finished, and the blocks after "
                    "it run once for that line" in what)
        p.check(f"② It offers {ALL} and {EACH}", ALL in values and EACH in values)
        dismiss(qtbot, win)

        p.step(f"Click Passes on and choose {EACH}.")
        choose_in_list(p, field(win, "Passes on"), EACH)
        rows = settings_rows(win, "Passes on", "At the same time", "Runs again in")
        p.shot(win, f"① Passes on: {EACH}. ② At the same time shows, with 1: one run after the Merger at a time. ③ Runs "
                    "again in shows too, with line order; it is for running again from the Merger (the next proof).",
               highlight=rows, badges=True, zoom=[*rows[0], *rows[-1]])
        same_time = field(win, "At the same time")
        p.check(f"① Passes on says {EACH}, and the flow keeps it",
                field(win, "Passes on").currentText() == EACH
                and tab.doc.blocks["merge"].get("config", {}).get("passes_on") == EACH)
        p.check("② At the same time shows 1, as it comes",
                row_shown(win, "At the same time") and (same_time.text() or same_time.placeholderText()) == "1")
        p.check(f"③ Runs again in shows {LINE_ORDER}, as it comes",
                row_shown(win, "Runs again in") and field(win, "Runs again in").currentText() == LINE_ORDER)

        # --- 2. A run -------------------------------------------------------------------------------------------------
        p.step("Click an empty part of the canvas, and press ▶ Run now.")
        click_empty(tab)
        press(win, win.run_action)
        p.check("The run comes to the moment shown below: report runs for load_costs' line while load_sales still runs",
                reaches(qtbot, lambda: running_for(tab, "report") == {"[line load_costs]"}
                        and tab.scene.nodes["load_sales"].status == "running"
                        and merger_progress(tab, 3) == (0, 1)))
        p.shot(win, "While it runs: ① load_costs finished first, so ② the Merger passed its output on, and ③ report "
                    "runs for load_costs' line, while ④ load_sales still runs. Under the Merger: 0/3 · 1 running.",
               highlight=["load_costs", under(win, tab, "merge"), "report", "load_sales"], badges=True,
               zoom=["split", *LINES, "merge", "report"])
        p.check("Once the picture was taken, report still ran for load_costs' line only, load_sales still ran, and the "
                "Merger still said 0/3 · 1 running",
                running_for(tab, "report") == {"[line load_costs]"} and tab.scene.nodes["load_sales"].status == "running"
                and merger_progress(tab, 3) == (0, 1))
        first = run_ended(qtbot, tab, 1)
        lines = log_of(win, first)
        ended = ends(win, first)
        spans = report_spans(lines)
        p.check(f"Run #{first} succeeded", tab.runs[-1].status == "success")
        p.check("The run history agrees: report began its run for load_costs' line once load_costs had ended, and "
                "ended it before load_sales ended",
                "load_costs" in spans and ended["load_costs"] - TICK <= spans["load_costs"][0]
                and spans["load_costs"][1] < ended["load_sales"])

        p.shot(win, f"After the run: ① report says ✓ 3: its three runs succeeded. ② gather closed the runs and ③ summary "
                    f"made “{SUMMARY}”.",
               highlight=["report", "gather", "summary"], badges=True, zoom=["merge", "report", "gather", "summary"])
        p.check("① report shows ✓ 3", tab.scene.nodes["report"].status_text == "✓ 3")
        p.check(f"③ summary made “{SUMMARY}”", value_of(tab, "summary", "output") == SUMMARY)
        p.check("report wrote one file per line, each with that line's report: "
                + ", ".join(f"reports/{what}.txt" for what in AMOUNTS), all(written(folder, w) for w in AMOUNTS))

        said = [f"[merge] INFO {b} arrived: its run " for b in FINISHED]
        tagged = [f"[report] INFO [line {b}] {what}" for b in FINISHED for what in ("report of", "wrote")]
        scroll_log(win, f"run #{first} · ")
        p.shot(win, "The Log of the run. In the outlined lines, the Merger says as each line arrives: load_costs first "
                    "(its run starts), then load_stock and load_sales (their runs wait for their turn); and each of "
                    "report's lines says which line it is for: load_costs', then load_stock's, then load_sales'.",
               highlight=log_lines_of(log, said + tagged),
               zoom=[in_log(win, [f"run #{first} · ", *said, *tagged, f"run #{first} success"])])
        finished = sorted(LINES, key=lambda b: ended[b])
        p.check(f"The lines finished in another order than top to bottom: {', '.join(finished)}", finished == FINISHED)
        p.check("The Merger says “load_costs arrived: its run starts”, “load_stock arrived: its run waits for its "
                "turn”, “load_sales arrived: its run waits for its turn”, in that order",
                [m for b, m, _ in lines if b == "merge" and " arrived: " in m]
                == ["load_costs arrived: its run starts", "load_stock arrived: its run waits for its turn",
                    "load_sales arrived: its run waits for its turn"])
        p.check(f"The lines arrived, and report ran for them, in the order they finished: "
                f"{', '.join(FINISHED)}", arrived(lines) == reported(lines) == finished)
        p.check("Each of report's Log lines says the line it is for",
                all(re.match(r"\[line load_\w+\] ", m) for b, m, _ in lines if b == "report"))

        p.step("Rest the mouse on the line under report: its result.")
        tip = hover_result(qtbot, tab, "report")
        said_there = tip.text()
        p.shot(tip, "✓ 3, and the last run to end: [line load_sales], with what report gave it: “sales 245”.",
               over=win, zoom=[tip, "report"])
        p.check("The card says ✓ 3, and that the last line to end was [line load_sales], success",
                "✓ 3" in said_there and "the last line to end: [line load_sales], success" in said_there)
        p.check("It shows that run's result, “sales 245”", "sales 245" in said_there)
        hide_tip(tab)

        p.step("Rest the mouse on the line under gather: its result.")
        tip = hover_result(qtbot, tab, "gather")
        p.shot(tip, "gather passed on each line's report under the line's name: load_sales, load_costs, load_stock.",
               over=win, zoom=[tip, "gather"])
        p.check(f"gather passed on {{line name: report's result}}: {json.dumps(RESULTS)}",
                value_of(tab, "gather", "value") == RESULTS)
        hide_tip(tab)

        # --- 3. Dark --------------------------------------------------------------------------------------------------
        p.step("Choose Tools > Settings…, set Theme to Dark and press Save. Then press ▶ Run now again.")
        set_theme(p, win, "Dark", shoot=False)
        QApplication.processEvents()
        p.check("The editor is dark", QApplication.palette().window().color().lightness() < 128)
        click_empty(tab)
        press(win, win.run_action)
        p.check("The second run comes to the same moment: 0/3 · 1 running",
                reaches(qtbot, lambda: len(tab.runs) == 2 and running_for(tab, "report") == {"[line load_costs]"}
                        and merger_progress(tab, 3) == (0, 1)))
        ratio = counter_contrast(tab)
        p.shot(win, "In Dark, while it runs: ① the Merger's counter, 0/3 · 1 running, reads well on the dark block.",
               highlight=[under(win, tab, "merge")], badges=True, zoom=["merge", "report"])
        p.check(f"① The counter reads well: a contrast of {ratio:.2f} to 1 with what is behind it ({TEXT} or more)",
                ratio >= TEXT)
        second = run_ended(qtbot, tab, 2)
        p.check(f"Run #{second} succeeded, with the same summary",
                tab.runs[-1].status == "success" and value_of(tab, "summary", "output") == SUMMARY)

        p.step("Set Theme back to System (Tools > Settings…) and press Save.")
        set_theme(p, win, "System", shoot=False)
        p.shot(win, f"The editor is light again (System: as this computer is), showing run #{second}.",
               zoom=["merge", "report", "gather", "summary"])
        p.check("The Theme is System again, and the editor light",
                theme.chosen(win.settings) == "System" and QApplication.palette().window().color().lightness() >= 128)

        p.step(f"Last, look at when each line ended in run #{first}, and when report ran for each, as the run history "
               "keeps them.")
        p.text(f"Run #{first}, from the run history: when each line ended, and when report ran for each (in seconds "
               "from the run's first line)", timetable(win, first))
        p.check("report began each run once the line had ended, in the order the lines ended",
                all(ended[b] - TICK <= spans[b][0] for b in FINISHED)
                and sorted(spans, key=lambda b: spans[b][0]) == FINISHED)
        p.check("report's runs went one at a time (At the same time 1), each once the one before had ended",
                len(spans) == 3 and all(spans[b][0] >= spans[a][1] - TICK for a, b in zip(FINISHED, FINISHED[1:])))
        p.check("report ran for load_costs' line and for load_stock's while load_sales was still loading: the run for "
                "load_stock began before load_sales ended",
                spans["load_costs"][1] < ended["load_sales"] and spans["load_stock"][0] < ended["load_sales"])
    finally:
        restore_look(win, app, as_found)


# --- 2: a failed line and failed runs; running again from the Merger; Validate --------------------------------------

def test_failed_runs_run_again_from_the_merger_in_the_order_you_choose_and_validate_checks_the_runs(
        proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("The Merger's each line as it finishes: a failed line or run, running again from the Merger, and "
              "Validate", f"{ASKED} … {ORDER_ASKED[0].upper()}{ORDER_ASKED[1:]}.")
    p.idea("When a line fails, it has no run after the Merger; when a run fails, it stops no other. Once every line "
           "has been dealt with, the Merger fails, listing each failure (“load_costs failed: …; report failed for "
           "line load_sales: …”), and what follows it does not run, as after any failure.")
    p.idea("Once you have fixed what failed, right-click the Merger and choose Run from this block on, with the same "
           "inputs: it runs again only the runs that failed or did not run. The lines do not run again (what they "
           "gave in that run is used), and the runs that succeeded keep what they gave, so the Merger that closes the "
           "runs gets every line's result.")
    p.idea("You asked to “show the 2 possibilities” for the order of the runs done again: the Merger's setting Runs "
           "again in says which. Line order, as it comes: top to bottom, where the lines reach the Merger on the "
           "canvas. Or the order the lines finished in the run it carries on from. A run from the start always goes "
           "in the order the lines finish.")
    p.idea("A line that failed gave nothing to carry on with: the Merger cannot run again from that run, and the Log "
           "says so (run the whole flow instead). On a block run once per line, Run only this block and Run from this "
           "block on are not offered in this release: they are greyed out, and say to right-click the Merger.")
    p.idea("Validate (F7) refuses, before any run, a connection into a block after the Merger that closes the runs "
           "from one of the runs: that block runs once, after all of them, and what each run has reaches it only "
           "through that Merger. It warns about a file path that a block run once per line writes to without a "
           "{placeholder}: each line's run would write over the one before.")
    p.why_simplest("It is the rule a Loop follows for its items, with lines for items: the runs that succeeded keep "
                   "what they gave with the run, and Run from this block on, on the Merger, runs again only the others. "
                   "Nothing to choose by hand.")
    p.why_simplest("Everything that went wrong is said in one place, where the lines come together, as the Merger set "
                   "to all lines together does it for lines: you see at once which line or run to fix, and why.")
    p.why_simplest("Runs again in is one setting with two choices. Line order, the default, is what you see on the "
                   "canvas, so a run done again never depends on how fast the lines were the time before, unless you "
                   "choose that.")
    p.alternative("Stop the other runs as soon as one fails",
                  "Work already half done would be thrown away, and you would have to run it again after the fix. As "
                  "with the lines, where a failed line stops no other line, nothing is cancelled: the other runs go on "
                  "to their end and keep what they made. A Loop is different: once one of its items fails, it starts "
                  "no new one (the items already running still end).")
    p.alternative("Start no new run once one fails, as a Loop does",
                  "The runs follow the rule of the lines they come from: a failed line stops no other line, and a run "
                  "that fails stops no other line's run. Otherwise a line that finished later would get no run only "
                  "for being slower, and there would be more to run again after the fix. The price, seen in the first "
                  "run below: when the runs fail for a reason they share (there, a missing layout), each one still "
                  "runs and fails, and the Log says so for each.")
    p.alternative("Run every line's run again after a fix",
                  "The runs that succeeded would run again for nothing, and write their files again. Keeping them is "
                  "what Run from this block on already does with a Loop's items.")
    p.alternative("One order only for the runs done again",
                  "You asked to choose. Line order can be read off the canvas; the order the lines finished keeps the "
                  "order of the run before, when that is what matters, so each is offered, line order first.")
    p.alternative("Let a run start, and find a wrong connection only when it gets there",
                  "Part of the flow would have run (files written) before the mistake showed. Validate refuses it "
                  "before anything runs, naming the connection, as it does a connection out of a Loop's box.")
    p.fits("The Merger fails like any block: red, its error in the Log and on the canvas, its error output there to "
           "handle it. The canvas, the Log and Run history read the same records: a run you pick in Run history shows "
           "on the canvas as it ran, and Run from this block on carries on from the run the Merger shows.")
    p.fits("Run from this block on is the one every block has; on the Merger, as on a Loop, the runs that succeeded "
           "in the run it carries on from keep their results.")
    p.fits("Validate (F7) and taskloom validate in a terminal say the same, in the same words, and every run checks "
           "it before it starts. A warning does not stop a run: it is written in the Log.")

    folder = tmp_path / "flows"
    folder.mkdir()
    put_layouts(folder, "costs")
    log = win.log.text
    try:
        # --- 1. A failed line and failed runs -----------------------------------------------------------------------
        p.step("Open the flow month-end.yaml (File > Open…): the flow of the proof before, with merge set to each line "
               "as it finishes. In its folder, costs.csv has not arrived yet, and layouts/ has only the costs layout: "
               "the sales and stock reports have none yet.")
        p.note(f"So ① load_costs fails at once, and ② report fails for the sales and stock lines: it works for "
               f"{REPORT_SECONDS:g} seconds, then finds no layout. The lines finish as before: load_costs, load_stock, "
               "then load_sales.")
        make_room(win)
        tab = open_flow(win, monkeypatch, month_end(folder, EACH))
        p.check("The flow opens, with merge set to each line as it finishes",
                tab is not None and tab.doc.blocks["merge"]["config"]["passes_on"] == EACH)

        p.step("Press ▶ Run now.")
        press(win, win.run_action)
        first = run_ended(qtbot, tab, 1)
        lines, ended = log_of(win, first), ends(win, first)
        spans = report_spans(lines)
        p.shot(win, "① load_costs failed: its line has no run. ② report ran for load_stock's line and failed (no "
                    "layout), then ran for load_sales' line all the same, and failed too: ✕ 2. ③ The Merger failed, so "
                    "④ gather and summary did not run.",
               highlight=["load_costs", "report", "merge", ["gather", "summary"]], badges=True,
               zoom=[*LINES, "merge", "report", "gather", "summary"])
        p.check(f"Run #{first} failed", tab.runs[-1].status == "failed")
        p.check("① load_costs failed; load_sales and load_stock succeeded",
                {b: tab.scene.nodes[b].status for b in LINES}
                == {"load_sales": "success", "load_costs": "failed", "load_stock": "success"})
        p.check("② report shows ✕ 2: it ran for load_stock's line and for load_sales', and failed for both",
                tab.scene.nodes["report"].status_text == "✕ 2" and set(spans) == {"load_stock", "load_sales"})
        p.check("② A failed run stops no other: report began its run for load_sales' line after its run for "
                "load_stock's had failed", spans["load_sales"][0] >= spans["load_stock"][1] - TICK)
        p.check("③ The Merger failed; ④ gather and summary were skipped",
                {b: tab.scene.nodes[b].status for b in ("merge", "gather", "summary")}
                == {"merge": "failed", "gather": "skipped", "summary": "skipped"})

        p.step("Click merge, the Merger. The Log, under the canvas, then shows only its lines.")
        click_block(win, "merge")
        error = kept(win, lambda h: h.blocks(first))["merge"]["error"] or ""
        said = ["[merge] INFO load_costs has no run: load_costs failed", "[merge] INFO load_stock arrived: its run starts",
                "[merge] INFO load_sales arrived: its run", "[merge] ERROR merge (Merger) failed: load_costs failed"]
        p.shot(win, "The outlined lines: as each line was dealt with, the Merger said so (load_costs has no run: it "
                    "failed; load_stock and load_sales arrived); once all three had been dealt with, it failed, listing "
                    "each failure. That last line goes on past the close-up: the whole error is above.",
               highlight=[in_log(win, said, inset=4)],
               zoom=[in_log(win, [f"run #{first} · ", *said, "lines ← load_stock.output"])])
        p.text("The Merger's error, in full (as the run history keeps it; one failure on each line here)",
               error.replace("; ", ";\n"))
        listed = error.split("; ")
        p.check("The Merger's error lists load_costs' failure and report's for each of the two lines: "
                "“load_costs failed: FileNotFoundError: costs.csv has not arrived …”, “report failed for line "
                "load_sales: FileNotFoundError: no layout for the sales report …”, and the same for load_stock",
                len(listed) == 3 and listed[0].startswith("load_costs failed: FileNotFoundError: costs.csv has not "
                                                          "arrived")
                and listed[1].startswith("report failed for line load_sales: FileNotFoundError: no layout for the "
                                         "sales report")
                and listed[2].startswith("report failed for line load_stock: FileNotFoundError: no layout for the "
                                         "stock report"))
        QTest.mouseClick(win.log.clear_filter, Qt.LeftButton)

        p.step("Put costs.csv in the flow's folder, then right-click merge and choose “Run from this block on, with the "
               "same inputs”.")
        (folder / "costs.csv").write_text(f"{AMOUNTS['costs']}\n", encoding="utf-8")
        p.while_open(lambda: right_click(tab, "merge"), lambda menu: choose(menu, FROM_HERE))
        refused = run_ended(qtbot, tab, 2)
        why = ("error: merge would not run: load_costs, before it, failed: run the whole flow instead, or choose its "
               "inputs")
        p.shot(win, f"The Log: ① “{why}”. load_costs failed in run #{first}: it gave the Merger nothing to carry on "
                    "with, and the Merger does not run lines again.",
               highlight=[(log, why)], badges=True, zoom=[(log, f"run #{first} failed"), (log, why)])
        p.check(f"The Log says “{why}”, and no run was started", why in log.toPlainText() and refused is None
                and [r["id"] for r in kept(win, lambda h: h.runs())] == [first])

        p.step("As the Log says, run the whole flow: press ▶ Run now.")
        press(win, win.run_action)
        second = run_ended(qtbot, tab, 3)
        lines, ended = log_of(win, second), ends(win, second)
        error = kept(win, lambda h: h.blocks(second))["merge"]["error"] or ""
        p.shot(win, f"Run #{second}: ① load_costs succeeded, and so did report's run for its line; report's runs for "
                    "the sales and stock lines failed again (no layouts yet): ✓ 1 ✕ 2. ② The Merger failed, listing "
                    "them.",
               highlight=[["load_costs", "report"], "merge"], badges=True,
               zoom=[*LINES, "merge", "report", "gather", "summary"])
        p.check(f"Run #{second} failed: all three lines succeeded; report shows ✓ 1 ✕ 2",
                tab.runs[-1].status == "failed"
                and all(tab.scene.nodes[b].status == "success" for b in LINES)
                and tab.scene.nodes["report"].status_text == "✓ 1 ✕ 2")
        p.check("① report wrote reports/costs.txt", written(folder, "costs"))
        p.check("② The Merger lists report's failure for load_sales' line and for load_stock's, and nothing else",
                [why.split(": ")[0] for why in error.split("; ")]
                == ["report failed for line load_sales", "report failed for line load_stock"])
        finished_then = sorted(LINES, key=lambda b: ended[b])
        p.check(f"In run #{second} the lines finished in the order {', '.join(finished_then)}",
                finished_then == FINISHED)

        p.step("Right-click report.")
        entries = []

        def look_at_report(menu):
            entries.extend(menu_item(menu, text) for text in NOT_OFFERED)
            p.shot(menu, "The menu of report, which runs once per line: ① Run only this block and Run from this block on "
                         "are greyed out.", over=win, highlight=[entries], badges=True, zoom=[menu, "report"])
            tip = rest_on(qtbot, menu, menu_item(menu, FROM_HERE))
            entries.append(tip.text())
            p.shot(tip, "Resting the mouse on one, its tooltip says why, and what to do instead: right-click the Merger "
                        "merge and choose Run from this block on.")

        p.while_open(lambda: right_click(tab, "report"), look_at_report)
        why_not = ("report runs once per line of the Merger merge (each line as it finishes): running it on its own is "
                   "not offered yet. Right-click the Merger merge and choose Run from this block on: it runs again the "
                   "lines' runs that failed or did not run.")
        p.check("① The four are greyed out, each saying “report runs once per line of the Merger merge … Right-click "
                "the Merger merge and choose Run from this block on …”",
                all(not a.isEnabled() and a.toolTip() == why_not for a in entries[:4]) and entries[4] == why_not)

        # --- 2. Running again from the Merger: line order -------------------------------------------------------------
        p.step("Put the sales and stock layouts in layouts/, then right-click merge and choose “Run from this block on, "
               "with the same inputs”. Its Runs again in is line order, as it comes.")
        put_layouts(folder, "sales", "stock")
        costs_written = report_file(folder, "costs").stat().st_mtime
        p.check(f"merge's Runs again in is {LINE_ORDER}",
                "runs_again_in" not in tab.doc.blocks["merge"]["config"])

        def run_from_the_merger(menu):
            p.shot(menu, f"The menu of merge: ① the item to choose; the same inputs are those of run #{second}, the run "
                         "the Merger shows.", over=win, highlight=[menu_item(menu, FROM_HERE)], badges=True,
                   zoom=[menu, "merge"])
            choose(menu, FROM_HERE)

        started = time.time()
        p.while_open(lambda: right_click(tab, "merge"), run_from_the_merger)
        third = run_ended(qtbot, tab, 4)
        lines = log_of(win, third)
        p.shot(win, f"Run #{third} succeeded: ① report says ✓ 2: it ran again for the two lines whose runs had failed; "
                    f"② gather got every line's report and summary made “{SUMMARY}”. The lines did not run again: they "
                    f"show their results from run #{second}.",
               highlight=["report", ["gather", "summary"]], badges=True,
               zoom=[*LINES, "merge", "report", "gather", "summary"])
        p.check(f"Run #{third} succeeded; report shows ✓ 2", tab.runs[-1].status == "success"
                and tab.scene.nodes["report"].status_text == "✓ 2")
        p.check(f"② gather passed on every line's report, load_costs' kept from run #{second}, and summary made "
                f"“{SUMMARY}”", value_of(tab, "gather", "value") == RESULTS
                and value_of(tab, "summary", "output") == SUMMARY)
        p.check(f"The lines did not run again: run #{third} holds the Merger, report, gather and summary, and no other "
                "block", set(kept(win, lambda h: h.blocks(third))) == {"merge", "report", "gather", "summary"})
        p.check("Each run's file: reports/costs.txt was not written again; reports/sales.txt and reports/stock.txt "
                "were written in this run",
                report_file(folder, "costs").stat().st_mtime == costs_written
                and all(written(folder, w) and report_file(folder, w).stat().st_mtime >= started - 1
                        for w in ("sales", "stock")))

        keeps = f"kept from run #{second}, where they succeeded: the runs for load_costs"
        said = ["[merge] INFO load_sales arrived: its run starts", "[merge] INFO load_stock arrived: its run waits",
                "[report] INFO [line load_sales] report of", "[report] INFO [line load_stock] wrote"]
        scroll_log(win, None)
        here = f"run #{third} · "
        p.shot(win, f"The Log of run #{third}. In the outlined lines, the two runs left go in line order, top to bottom: "
                    "load_sales arrived first and its run started, load_stock's waited for its turn; then report ran "
                    "for load_sales' line, then for load_stock's. No line is for load_costs: its run is kept from run "
                    f"#{second}, as the Merger says above.",
               highlight=[in_log(win, said, after=here, inset=4)],
               zoom=[in_log(win, [here, *said, f"run #{third} success"], after=here)])
        p.text(f"What the Merger wrote in the Log in run #{third} (its first lines are longer than the Log is wide)",
               merger_lines(lines))
        p.check(f"The Merger says “… {keeps}”", any(b == "merge" and m.endswith(keeps) for b, m, _ in lines))
        p.check("The runs went in line order: load_sales, then load_stock; none for load_costs",
                arrived(lines) == reported(lines) == ["load_sales", "load_stock"])

        # --- 3. Running again from the Merger: the order the lines finished -------------------------------------------
        p.step(f"Click the Run history tab, under the canvas, and double-click run #{second}: the canvas shows it "
               "again.")
        click_bottom_tab(win, win.history_panel)
        table = win.history_panel.table
        click_row(table, row_of(table, str(second)))
        p.shot(win, f"① Run #{second}, started by start_by_hand, failed: the one to double-click. Run #{third} above it "
                    "started from merge.",
               highlight=[(table, row_of(table, str(second)))], badges=True,
               zoom=[table.item(0, 0), table.item(table.rowCount() - 1, 4)])
        p.check(f"Run history lists run #{third}, from merge, success, above run #{second}, from start_by_hand, failed",
                [(table.item(r, 0).text(), table.item(r, 2).text(), table.item(r, 3).text()) for r in range(2)]
                == [(str(third), "from merge", "success"), (str(second), "start_by_hand", "failed")])
        click_row(table, row_of(table, str(second)), double=True)
        QTest.qWait(200)
        p.check(f"The canvas shows run #{second} again: merge's result is from it, and failed; report shows ✓ 1 ✕ 2",
                (tab.block_info.get("merge") or {}).get("run_id") == second
                and tab.scene.nodes["merge"].status == "failed"
                and tab.scene.nodes["report"].status_text == "✓ 1 ✕ 2")
        click_bottom_tab(win, win.log)

        p.step("Click merge, and rest the mouse on Runs again in, in Properties.")
        click_block(win, "merge")
        card = card_of(qtbot, win, "Runs again in")
        label, what, values, _ = card_says(card)
        parts = card_parts(win, card)
        p.shot(card, "The card of Runs again in: ① running again from the Merger, its runs go in line order (top to "
                     "bottom, as on the canvas) or in the order the lines finished in the run it carries on from. ② "
                     "Its two choices.", over=win, highlight=parts[:2], badges=True, zoom=[card, *parts[:2]])
        p.check("① The card tells both orders", label == "Runs again in"
                and "line order (top to bottom, as on the canvas)" in what
                and "the order the lines finished in the run it carries on from" in what)
        p.check(f"② It offers {LINE_ORDER} and {AS_FINISHED}", LINE_ORDER in values and AS_FINISHED in values)
        dismiss(qtbot, win)

        p.step(f"Choose {AS_FINISHED} in Runs again in. Then click an empty part of the canvas, right-click merge and "
               f"choose “Run from this block on, with the same inputs”: those of run #{second} again.")
        choose_in_list(p, field(win, "Runs again in"), AS_FINISHED)
        rows = settings_rows(win, "Runs again in")
        p.shot(win, f"① Runs again in: {AS_FINISHED}.", highlight=rows, badges=True, zoom=[*rows[0], "merge"])
        p.check(f"① Runs again in says {AS_FINISHED}, and the flow keeps it",
                field(win, "Runs again in").currentText() == AS_FINISHED
                and tab.doc.blocks["merge"]["config"].get("runs_again_in") == AS_FINISHED)
        costs_written = report_file(folder, "costs").stat().st_mtime
        click_empty(tab)  # (the Log shows every block's lines again)
        menu_said = []

        def from_the_same_run(menu):
            menu_said.append(menu_item(menu, FROM_HERE).text())
            choose(menu, FROM_HERE)

        p.while_open(lambda: right_click(tab, "merge"), from_the_same_run)
        fourth = run_ended(qtbot, tab, 5)
        lines = log_of(win, fourth)
        said = ["[merge] INFO load_stock arrived: its run starts", "[merge] INFO load_sales arrived: its run waits",
                "[report] INFO [line load_stock] report of", "[report] INFO [line load_sales] wrote"]
        scroll_log(win, None)
        here = f"run #{fourth} · "
        p.shot(win, f"The Log of run #{fourth}: it carries on from run #{second} again, and keeps the run for load_costs. "
                    f"In the outlined lines, the runs left go in the order the lines finished in run #{second}: "
                    "load_stock first (its run starts), load_sales after (its run waits for its turn); then report ran "
                    "for load_stock's line, then for load_sales'.",
               highlight=[in_log(win, said, after=here, inset=4)],
               zoom=[in_log(win, [here, *said, f"run #{fourth} success"], after=here)])
        p.text(f"What the Merger wrote in the Log in run #{fourth}", merger_lines(lines))
        p.check(f"The menu offered the inputs of run #{second}", f"(run #{second} · " in menu_said[0])
        p.check(f"Run #{fourth} succeeded, carrying on from run #{second}: the Merger keeps the run for load_costs",
                tab.runs[-1].status == "success" and any(b == "merge" and m.endswith(keeps) for b, m, _ in lines))
        p.check(f"The runs went in the order the lines finished in run #{second}: load_stock, then load_sales "
                "(top to bottom would be load_sales first)",
                arrived(lines) == reported(lines) == [b for b in finished_then if b != "load_costs"]
                == ["load_stock", "load_sales"])
        p.check("Each run's file: reports/costs.txt was not written again",
                report_file(folder, "costs").stat().st_mtime == costs_written)

        # --- 4. Validate --------------------------------------------------------------------------------------------
        p.step("Open the flow check-runs.yaml (File > Open…): month-end's blocks, with two more and one more "
               "connection.")
        tab = open_flow(win, monkeypatch, check_runs(folder))
        refused_edge = (f"edge {TAKES_A_RUN}: summary runs once, after gather, which closes the runs merge makes once "
                        "per line, but takes from report, which has one line's run: what each run has reaches it only "
                        "through gather (connect its output instead)")
        warned = ("save_rows: Path: every line of merge writes to out/rows.csv (it has no {placeholder}, so each line's "
                  "run writes over what the one before wrote)")
        p.shot(win, "① rows makes a one-row table of each line, and save_rows (a Write File) writes it to "
                    "out/rows.csv: one path for every line. ② report's output also goes to summary's latest input.",
               highlight=[["rows", "save_rows"], ["report", "summary"]], badges=True, zoom=list(tab.doc.blocks))
        p.check("The flow opens", tab.doc.path.name == "check-runs.yaml")

        p.step("Press Validate in the toolbar (F7).")
        said = boxes_said(p, monkeypatch, lambda: press(win, win.validate_action),
                          f"The flow has problems: the connection {TAKES_A_RUN} goes into summary, which runs once, "
                          "after gather has closed the runs; what each run has reaches it only through gather.")
        p.check(f"Validate refuses the connection, naming it, and nothing else: “{refused_edge}”",
                said == [refused_edge])
        QTest.qWait(100)
        p.shot(win, "The Log's outlined lines: first the warning about save_rows' path, then the refused connection. "
                    "Both go on past the close-up: the checks give each in full.",
               highlight=log_lines_of(log, [warned, refused_edge]), zoom=[in_log(win, [warned, refused_edge])])
        p.check(f"The Log warns: “{warned}”", warned in log.toPlainText())

        p.step(f"Click the connection {TAKES_A_RUN} on the canvas, press Delete, and press Validate (F7) again.")
        delete_connection(win, tab, qtbot, TAKES_A_RUN)
        p.check("The connection is gone", TAKES_A_RUN not in tab.doc.data["edges"])
        press(win, win.validate_action)
        QTest.qWait(200)
        message, showing = status_message(win)
        p.shot(win, f"No box this time: ① the status bar says “{message}”; ② the Log warns about save_rows' path again: "
                    "a warning does not stop a run.",
               highlight=[showing, in_log(win, [warned], last=True, inset=5)], badges=True,
               zoom=[in_log(win, [warned], last=True), showing])
        p.check("Validate finds no problem: no box, and the status bar says so",
                win.message_boxes == [] and message.endswith("no problems found"))
        p.check("② The warning about save_rows' path is in the Log again",
                log.toPlainText().count(warned) == 2)
        p.note("Validate also refuses a block where the runs of two Mergers set to each line as it finishes meet (it "
               "cannot run once per line of both), and a block run once per line that takes from the end of one of "
               "the Merger's lines (take that line's output from the Merger instead). A Splitter inside another "
               "Splitter's line is still refused.")
    finally:  # no run left going
        for run_ in [r for t in win.tabs_list() for r in t.runs if r.running]:
            run_.proc.kill()
            run_.proc.waitForFinished(5000)
        clean(win)
