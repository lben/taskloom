"""Proof: a Splitter's lines run at the same time (with an optional limit) or one after another, top to bottom; the
Merger joins them, passing on each line's output by name (stage 7a of the 2026-10-30 feedback). How proofs are
written: README.md here."""

import contextlib
import datetime as dt
import json
import sqlite3

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

from proofs.test_proof_block_icons import list_row
from proofs.test_proof_canvas import drag_block, open_flow
from proofs.test_proof_docs_help import card_parts, card_says, click_block, in_window
from proofs.test_proof_python_reusable_block import item_rect
from proofs.test_proof_result_time import hover as hover_result
from proofs.test_proof_run_scope import log_lines, press
from proofs.test_proof_scheduler_start import click_empty
from proofs.test_proof_tab_plus import set_theme
from proofs.test_proof_run_output import setting
from proofs.test_proof_tab_stays import type_in
from taskloom.editor import theme
from tests.test_block_icons import GROUP_COLORS, drawing, rows, unlike_listed
from tests.test_docs_help import dismiss, hover as hover_setting
from tests.test_inspect_mermaid import contrast
from tests.test_parallel_lines import ARROWS_JOIN, ARROWS_SPLIT, numbers_on_connections, row_shown
from tests.test_paths_editor_editing import clean, field, flow_file
from tests.test_theme import TEXT, most_common

ASKED = ("parallel runs is something we indeed need to support, probably the cleanest way is to create splitter and "
         "merger blocks and we set on the splitter if we want the split lines to run in parallel or not ... the 2nd "
         "line can connect with the 1st line ... Then the merger block can support \"join\" that waits for all the "
         "inputs to finish before proceeding or can let the output of any line go out as soon as the line finishes.")
TICK = dt.timedelta(seconds=0.016)  # a clock tick on Windows: two times taken in a row may differ by this much
SECONDS = 1.5  # how long each line's first block works
BLOCKS_WIDTH = 180  # the Blocks list made narrower, for a wider canvas
ZOOM = 80  # the Splitter, its lines and the Merger fit the canvas at this zoom, with room above the lines


def python(inputs: list[str], code: str, x: int, y: int) -> dict:
    return {"type": "logic.python", "config": {"inputs": inputs, "code": code}, "ui": {"x": x, "y": y}}


def loads(what: str, output: str, x: int, y: int) -> dict:
    """A line's first block: it says in the Log what it loads, works for SECONDS, and gives `output`."""
    return python(["month"], f"import time\nprint('loading {what} of', month)\ntime.sleep({SECONDS})\n"
                             f"output = {output}", x, y)


SPLITTER, MERGER = "split_loads", "join_loads"
FIRSTS = ["load_sales", "load_costs", "load_stock"]  # top to bottom: each a line's first block
LINES = {1: ["load_sales"], 2: ["load_costs", "costs_total"], 3: ["load_stock"]}  # costs_total: in line 2 (see p.idea)
REPORT = ("output = f\"sales {loads['load_sales']}, margin {loads['costs_total']['margin']}, "
          "stock {loads['load_stock']}\"")
BLOCKS = {  # (report far enough right that the Splitter, the lines and the Merger are in the middle)
    "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 200}},
    "pick_month": python([], "output = '2026-10'", 205, 200),
    SPLITTER: {"type": "logic.splitter", "ui": {"x": 410, "y": 200}},
    "load_sales": loads("sales", "245", 660, 30),
    "load_costs": loads("costs", "[90, 60]", 660, 200),
    "costs_total": python(["costs", "sales"], "output = {'costs': sum(costs), 'margin': sales - sum(costs)}",
                          875, 200),
    "load_stock": loads("stock", "37", 660, 370),
    MERGER: {"type": "logic.merger", "ui": {"x": 1090, "y": 200}},
    "report": python(["loads"], REPORT, 1500, 200),
}
EDGES = ["start_by_hand.start -> pick_month.after", f"pick_month.output -> {SPLITTER}.value",
         *(f"{SPLITTER}.lines -> {b}.month" for b in FIRSTS),
         "load_costs.output -> costs_total.costs", "load_sales.output -> costs_total.sales",
         *(f"{b}.output -> {MERGER}.lines" for b in ("load_sales", "costs_total", "load_stock")),
         f"{MERGER}.value -> report.loads"]
# The same blocks without the Splitter and the Merger: report takes the three outputs itself.
PLAIN_BLOCKS = {b: spec for b, spec in BLOCKS.items() if b not in (SPLITTER, MERGER, "report")}
PLAIN_BLOCKS["report"] = python(["loads"], REPORT, 1090, 200)
PLAIN_EDGES = ["start_by_hand.start -> pick_month.after", *(f"pick_month.output -> {b}.month" for b in FIRSTS),
               "load_costs.output -> costs_total.costs", "load_sales.output -> costs_total.sales",
               *(f"{b}.output -> report.loads" for b in ("load_sales", "costs_total", "load_stock"))]
PLAIN_ORDER = ["start_by_hand", "pick_month", "load_sales", "load_costs", "costs_total", "load_stock", "report"]
MERGED = {"load_sales": 245, "costs_total": {"costs": 150, "margin": 95}, "load_stock": 37}
REPORTED = "sales 245, margin 95, stock 37"


# --- what the run history keeps ---------------------------------------------------------------------------------------

def spans(win, run_id: int) -> dict[str, tuple[dt.datetime, dt.datetime]]:
    """When each block of run `run_id` started and finished, as the run history (history.db) keeps it."""
    with contextlib.closing(sqlite3.connect(str(win.home.history_db))) as db:
        found = db.execute("SELECT block, started, finished FROM blocks WHERE run_id=? AND started IS NOT NULL "
                           "AND finished IS NOT NULL", (run_id,)).fetchall()
    return {b: (dt.datetime.fromisoformat(s), dt.datetime.fromisoformat(f)) for b, s, f in found}


def line_spans(times: dict, lines: dict[int, list[str]]) -> dict[int, tuple[dt.datetime, dt.datetime]]:
    """Each line, from when its first block started to when its last one finished."""
    return {n: (min(times[b][0] for b in blocks), max(times[b][1] for b in blocks)) for n, blocks in lines.items()}


def most_at_once(spans_: list[tuple[dt.datetime, dt.datetime]]) -> int:
    """The most of these that ran at the same moment (for longer than a clock tick)."""
    inner = [(a + TICK, b - TICK) for a, b in spans_]
    return max(sum(a <= t < b for a, b in inner) for t, _ in inner)


def by_line(lines: dict[int, list[str]]) -> list[tuple[str, str]]:
    """The lines' blocks, each with the line it is in, said on the line's first block."""
    return [(f"line {n}" if i == 0 else "", b) for n, blocks in lines.items() for i, b in enumerate(blocks)]


def timetable(times: dict, rows_: list[tuple[str, str]]) -> str:
    """Each (label, block), with when the block started and ended, in seconds from when the first of them started."""
    zero = min(times[b][0] for _, b in rows_)
    return "\n".join(f"{label:8}{b:14}{(times[b][0] - zero).total_seconds():5.2f} s  to "
                     f"{(times[b][1] - zero).total_seconds():5.2f} s" for label, b in rows_)


# --- what the canvas shows ------------------------------------------------------------------------------------------

def number_areas(win, tab) -> list[QRect]:
    """Where the numbers on the connections leaving the Splitter are, in the window's pixels."""
    viewport = tab.view.viewport()
    return [in_window(win, viewport, tab.view.mapFromScene(e.number.sceneBoundingRect()).boundingRect())
            for e in tab.scene.edge_items if e.number is not None]


def numbered(tab) -> dict[int, str]:
    """The numbers drawn on the canvas near the Splitter, each with the block its connection goes to (a number drawn
    near the Splitter, on that connection)."""
    found = numbers_on_connections(tab, SPLITTER)
    return {n: labels[0]["on"] for n, labels in found.items()
            if len(labels) == 1 and labels[0]["away"] <= 20 and labels[0]["from_splitter"] < labels[0]["from_line"]}


def number_contrasts(tab) -> dict[int, float]:
    """Each number on the connections: the contrast of its colour with what is behind it, where the canvas paints it
    (as the test of this change measures it)."""
    found = {}
    for n, (label, *_) in numbers_on_connections(tab, SPLITTER).items():
        s = max(label["size"], 6.0)
        cx, cy = label["center"]
        image = QImage(round(8 * s), round(8 * s), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        tab.scene.render(painter, QRectF(0, 0, image.width(), image.height()), QRectF(cx - 2 * s, cy - 2 * s, 4 * s,
                                                                                       4 * s))
        painter.end()
        behind, ink, alpha = most_common(image), QColor(label["fill"]), label["opacity"]
        shown = QColor(*(round(alpha * f + (1 - alpha) * b) for f, b in zip(ink.getRgb()[:3], behind.getRgb()[:3])))
        found[n] = contrast(shown, behind)
    return found


def in_log(log, text: str) -> list:
    """The Log's lines that show `text`, to outline (none if it shows none: the check after the screenshot says so)."""
    return [(log, text)] if text in log.toPlainText() else []


def status_message_area(win, message: str) -> QRect:
    """Where the status bar, at the bottom of the window, shows `message`, in the window's pixels."""
    bar = win.statusBar()
    width = bar.fontMetrics().horizontalAdvance(message) + 16
    return in_window(win, bar, QRect(0, 0, width, bar.height()))


def statuses(tab, blocks) -> dict[str, str | None]:
    return {b: tab.scene.nodes[b].status for b in blocks}


def start_run(win, tab):
    """Press ▶ Run now: a run starts."""
    before = len(tab.runs)
    press(win, win.run_action)
    assert len(tab.runs) == before + 1, "Run now started no run"


def became(qtbot, condition) -> bool:
    """Whether `condition()` came true while the run went on (within 30 seconds: the runs here take a few)."""
    try:
        qtbot.waitUntil(condition, timeout=30_000)
    except qtbot.TimeoutError:
        return False
    return True


def run_ended(qtbot, tab) -> int:
    """Wait for the run to end: its number."""
    qtbot.waitUntil(lambda: not tab.running and tab.runs[-1].status is not None, timeout=60_000)
    QTest.qWait(300)  # what the run printed last reaches the Log
    return tab.runs[-1].run_id


def choose_lines_run(p, win, text: str):
    """Click Lines run, in Properties, to open its list, then click `text` in it."""
    combo = field(win, "Lines run")
    index = combo.findText(text)
    assert index >= 0, f"{text!r} is not in the list"

    def pick(_list):
        # A click just after the list opened is taken as the end of the click that opened it, not as a choice.
        QTest.qWait(QApplication.doubleClickInterval() + 100)
        view = combo.view()
        at = view.visualRect(view.model().index(index, 0)).center()
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, at)

    p.while_open(lambda: QTest.mouseClick(combo, Qt.LeftButton), pick)
    QApplication.processEvents()  # (Properties shows the block again: At most at once goes)


def zoom_canvas(tab, percent: int):
    """Click the canvas's zoom box (top right), type `percent` and press Enter."""
    box = tab.view.zoom_bar.percent
    QTest.mouseClick(box, Qt.LeftButton)
    QTest.keyClicks(box, str(percent))
    QTest.keyClick(box, Qt.Key_Return)
    QApplication.processEvents()


def test_a_splitters_lines_run_at_the_same_time_or_one_after_another_and_the_merger_joins_them(
        proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Splitter and Merger: lines that run at the same time, or one after another", ASKED)
    p.idea("Two new blocks, under Logic: a Splitter starts lines that run at the same time (with a limit if you want "
           "one) or one after another, top to bottom; a Merger waits for all of them and passes on each line's output "
           "by name.")
    p.idea("Each connection leaving the Splitter's lines output starts one line, and each line gets its own copy of "
           "the value that arrived at the Splitter. A line is the block that connection goes to and the blocks after "
           "it, up to the Merger. A line may use what another line makes, as you asked (“the 2nd line can connect "
           "with the 1st line”): its block waits until that has arrived.")
    p.idea("The Splitter's Lines run says how: at the same time (as the block comes), or one after another. At the "
           "same time, At most at once can limit how many lines run together, to spare a database or the computer: "
           "the next line starts as soon as one ends. One after another runs line 1 to its end, then line 2, and so "
           "on. Line order is top to bottom on the canvas (by where each line's first block is, then left to "
           "right); one after another, the canvas writes 1, 2, 3 on the connections leaving the Splitter, and the "
           "numbers follow when you move the blocks.")
    p.idea("Inside a line, its blocks run one at a time, in the flow's order, as always (a block waiting for what "
           "another line makes lets the next one that need not wait go first). The blocks outside the lines (before "
           "the Splitter, from the Merger on, and any on a branch of their own) run one at a time among themselves, "
           "in the flow's order. A block on a branch of its own, one that does not wait for the lines, can run while "
           "the lines do. A flow without a Splitter runs exactly as it did. A block that several lines lead to runs "
           "in the last of them in line order: here costs_total takes from line 1 and from line 2, so it runs in "
           "line 2.")
    p.idea("The Merger joins: it waits until every line connected to it has delivered, then sends on one value, "
           "{line name: that line's output}, named as Python Code names what it receives from several blocks. Its "
           "other mode, which you also asked for, “as each finishes” (the blocks after it run once for each line, "
           "as soon as that line ends), comes in the next release.")
    p.idea("The Log says once how the lines run (“3 lines at the same time: load_sales, load_costs, load_stock”). "
           "Validate (F7) refuses, before any run, a layout that could never finish, and names the connection: for "
           "example, one after another, a connection from line 3 into line 1's first block, which would wait for a "
           "line that cannot start before line 1 ends.")
    p.why_simplest("Two blocks, and two settings on the Splitter; nothing new for you to draw: the lines are the "
                   "connections you already draw out of the Splitter, and where they join is the Merger. You see on the canvas which blocks "
                   "run side by side and where they come together.")
    p.why_simplest("The lines run inside the one run: one Log, one entry in Run history, one ▶ Run now and one Stop. "
                   "Each line runs its blocks the way a whole flow always ran them, one after another, and the blocks "
                   "outside the lines run so among themselves; a block on a branch of its own, one that does not "
                   "wait for the lines, can run while they do. Each block gets its own copy of what it receives, so "
                   "no line can change another line's data.")
    p.why_simplest("Line order comes from where the blocks are, top to bottom, so there is nothing to number by hand "
                   "and keep up to date: move a block and the order follows. The Merger's value is named like Python "
                   "Code's inputs from several blocks, so the block after it reads the lines' outputs by name.")
    p.alternative("A setting on every block, or every branch of a flow running at the same time",
                  "It would change how the flows you have run: their blocks run one at a time today, and some of "
                  "them load a database. And nothing would say which blocks belong together or where they meet "
                  "again. With a Splitter you choose where lines run side by side, and a flow without one runs as it "
                  "did; in a flow with one, a block on a branch of its own, one that does not wait for the lines, "
                  "can also run while they do.")
    p.alternative("Each line its own run, started by the Splitter",
                  "Each line would have its own Log and its own row in Run history, Stop would have to find every "
                  "one of them, and the Merger would have to wait across runs. In one run, all of that stays as it "
                  "is.")
    p.alternative("A number on each connection that you set yourself",
                  "Numbers you set must be kept in step when you add, remove or move a line, and two lines could get "
                  "the same one. Top to bottom is what you already see, and the canvas writes the numbers for you.")
    p.alternative("The Merger sending on a list of the lines' outputs, in line order",
                  "The block after it would read each output by its place in the list, and moving a line would give "
                  "it another line's output. By name, as Python Code does it, moving a line changes nothing.")
    p.fits("The Splitter and the Merger are Logic blocks like the others, in the Blocks list and in the "
           "Documentation, with a card on each setting; their icons are Tabler's arrows-split and arrows-join, in "
           "the style of the others.")
    p.fits("While lines run at the same time, the canvas shows each running block as running, each Log line keeps "
           "its block's name, and the run history keeps each block's result with when it started and ended, as with "
           "one block at a time.")
    p.fits("A failed line stops no other line: the Merger then fails, naming the failed block, and once you have "
           "fixed it, Run from this block on runs that block, the rest of its line, the Merger and what follows "
           "it, without running again the lines that succeeded. Stop cancels every running line at once. Validate, "
           "Tools > Check flows, taskloom validate and taskloom check refuse the same layouts, in the same words.")
    p.fits("The numbers on the connections are drawn in the theme's text colour, on a small patch of the canvas's "
           "colour, so they read well in Light and in Dark.")

    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings writes Windows' Startup script there)
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    flow = flow_file(tmp_path / "month-end.yaml", BLOCKS, EDGES)
    plain = flow_file(tmp_path / "month-end-plain.yaml", PLAIN_BLOCKS, PLAIN_EDGES)
    log = win.log.text
    try:
        # --- 1. The two blocks --------------------------------------------------------------------------------------
        p.step("Look at the Blocks list, on the left, under Logic.")
        tree = win.palette_panel.tree
        logic = rows(win.palette_panel)["Logic"]
        folder = logic[0].parent()
        tree.scrollToItem(logic[-1])
        tree.scrollToItem(folder)
        QApplication.processEvents()
        splitter_row, merger_row = (next(item for item in logic if item.text(0) == title)
                                    for title in ("Splitter", "Merger"))
        p.shot(win, "Under Logic: ① Splitter, with arrows that split, and ② Merger, with arrows that join.",
               highlight=[list_row(win, splitter_row), list_row(win, merger_row)], badges=True,
               zoom=[item_rect(win, folder), item_rect(win, logic[-1])])
        p.check("① and ② The Blocks list has Splitter and Merger under Logic",
                {"Splitter", "Merger"} <= {item.text(0) for item in logic})
        color = GROUP_COLORS["Logic"]
        like = {item.text(0): unlike_listed(item, color, {"split": drawing(ARROWS_SPLIT),
                                                          "join": drawing(ARROWS_JOIN)}) for item in logic}
        p.check("① Of the Logic blocks' icons, Splitter's is the one most like Tabler's arrows-split drawing, and ② "
                "Merger's the one most like arrows-join",
                min(like, key=lambda t: like[t]["split"]) == "Splitter"
                and min(like, key=lambda t: like[t]["join"]) == "Merger")

        p.step(f"Open the flow month-end.yaml (File > Open…). start_by_hand runs pick_month, which gives the month to "
               f"the Splitter {SPLITTER}. Its three lines, top to bottom: load_sales; load_costs, then costs_total, "
               "which also takes load_sales' output (a line using another line's output); load_stock. Each load_ "
               f"block works for about {SECONDS:g} seconds. The Merger {MERGER} joins the lines, and report writes "
               "one line from what it receives.")
        p.note("For this proof, the Blocks list is made narrower (its right edge dragged to the left), so that the "
               "canvas is wider.")
        win.resizeDocks([win.blocks_dock], [BLOCKS_WIDTH], Qt.Horizontal)
        tab = open_flow(win, monkeypatch, flow)
        p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
        p.shot(win, f"The whole flow. ① The Splitter {SPLITTER}; ② its three lines; ③ the Merger {MERGER}, then "
                    "report.",
               highlight=[SPLITTER, [*FIRSTS, "costs_total"], [MERGER, "report"]], badges=True)

        p.step(f"Type {ZOOM} in the canvas's zoom box, at its top right, and press Enter: the Splitter, its lines and "
               "the Merger fill the canvas.")
        zoom_canvas(tab, ZOOM)
        p.check(f"The canvas shows the flow at {ZOOM}%", round(tab.view.zoom_percent()) == ZOOM)

        p.step(f"Click {SPLITTER}, and rest the mouse on its Lines run setting, in Properties.")
        click_block(win, SPLITTER)
        lines_run = field(win, "Lines run")
        card = hover_setting(qtbot, win, lines_run, "Lines run", "Splitter › Lines run")
        label, what, values, _ = card_says(card)
        parts = card_parts(win, card)
        p.shot(card, "The card of Lines run: ① at the same time, the lines run together; one after another, line 1 "
                     "runs to its end, then line 2, top to bottom as on the canvas. ② Its two choices.",
               over=win, highlight=parts[:2], badges=True, zoom=[card, *parts[:2]])
        p.check("① The card of Lines run tells both ways: at the same time, and one after another, top to bottom as "
                "on the canvas",
                label == "Lines run" and "at the same time: the lines run together" in what
                and "one after another: line 1 runs to its end, then line 2" in what and "top to bottom" in what)
        p.check("② It offers at the same time and one after another", "at the same time" in values
                and "one after another" in values)
        dismiss(qtbot, win)

        # --- 2. At the same time ----------------------------------------------------------------------------------
        p.step("Click an empty part of the canvas, and press ▶ Run now. Lines run is at the same time, as the block "
               "comes.")
        p.check("Lines run says at the same time", lines_run.currentText() == "at the same time")
        click_empty(tab)
        start_run(win, tab)
        at_once = became(qtbot, lambda: all(s == "running" for s in statuses(tab, FIRSTS).values()))
        seen = statuses(tab, FIRSTS)
        p.shot(win, "During the run: ① load_sales, load_costs and load_stock are all running at once, each with the "
                    "amber outline and “running” under it.",
               highlight=[FIRSTS], badges=True, zoom=[SPLITTER, *FIRSTS])
        p.check("① load_sales, load_costs and load_stock were running at the same moment, on the canvas",
                at_once and all(s == "running" for s in seen.values()))
        first = run_ended(qtbot, tab)
        p.check("The run succeeded", tab.runs[-1].status == "success")
        times = spans(win, first)
        together = (min(times[b][1] for b in FIRSTS) - max(times[b][0] for b in FIRSTS)).total_seconds()
        p.check(f"The run history agrees: the three ran together for {together:.2f} s",
                together >= SECONDS - 0.5)
        p.check("costs_total, in line 2, waited for load_sales, of line 1, before it started",
                times["costs_total"][0] >= times["load_sales"][1] - TICK)

        said = f"3 lines at the same time: {', '.join(FIRSTS)}"
        p.shot(win, f"After the run, ① the Log's line from {SPLITTER}: “{said}”. Each line's block wrote its own line "
                    "under its own name.",
               highlight=in_log(log, said), badges=True,
               zoom=[(log, f"run #{first} · "), *in_log(log, said), (log, f"DONE run #{first} ")])
        p.check(f"① The Log says once “{said}”", sum(said in line for line in log_lines(win, "INFO")) == 1)

        p.step(f"Rest the mouse on the line under {MERGER}: its result.")
        tip = hover_result(qtbot, tab, MERGER)
        value = json.loads(tab.scene.values[MERGER]["value"])
        p.shot(tip, "The Merger's result: one value with each line's output under its name: load_sales, costs_total "
                    "(the last block of line 2) and load_stock.", over=win, zoom=[tip, MERGER])
        p.check("The Merger's result has the keys load_sales, costs_total and load_stock",
                sorted(value) == sorted(MERGED))
        p.check(f"Each holds that line's output: {json.dumps(MERGED)}", value == MERGED)
        p.check(f"report, after the Merger (out of view, right of it), made “{REPORTED}” from it",
                REPORTED in tab.scene.values.get("report", {}).get("output", ""))
        QTest.mouseMove(tab.view.viewport(), tab.view.viewport().rect().topLeft())
        QToolTip.hideText()

        # --- 3. At most 2 at once ---------------------------------------------------------------------------------
        p.step(f"Click {SPLITTER} and type 2 in its At most at once, then click an empty part of the canvas and press "
               "▶ Run now.")
        p.note("At most at once takes a whole number, not a {placeholder}: Validate checks the lines with it before "
               "the run.")
        click_block(win, SPLITTER)
        type_in(field(win, "At most at once"), "2")
        shown = [*setting(win, "Lines run"), *setting(win, "At most at once")]
        p.shot(win, "① In Properties, Lines run: at the same time, and At most at once: 2.",
               highlight=[shown], badges=True, zoom=[SPLITTER, *shown])
        p.check("① At most at once says 2, and the Splitter keeps it",
                shown[3].text() == "2" and tab.doc.blocks[SPLITTER]["config"].get("at_most") == 2)
        click_empty(tab)
        start_run(win, tab)
        two = became(qtbot, lambda: statuses(tab, ["load_sales", "load_costs"]) == {"load_sales": "running",
                                                                                  "load_costs": "running"})
        seen = statuses(tab, FIRSTS)
        p.shot(win, "During the run: ① lines 1 and 2 run; ② line 3 waits for one of them to end.",
               highlight=[["load_sales", "load_costs"], "load_stock"], badges=True, zoom=[SPLITTER, *FIRSTS])
        p.check("① load_sales and load_costs were running while ② load_stock had not started",
                two and seen["load_sales"] == seen["load_costs"] == "running" and seen["load_stock"] != "running")
        second = run_ended(qtbot, tab)
        said = f"3 lines at the same time, at most 2 at once: {', '.join(FIRSTS)}"
        p.shot(win, f"① The Log: “{said}”.", highlight=in_log(log, said), badges=True,
               zoom=[(log, f"run #{second} · "), *in_log(log, said), (log, f"DONE run #{second} ")])
        p.check(f"① The Log says once “{said}”", sum(said in line for line in log_lines(win, "INFO")) == 1)
        times = spans(win, second)
        p.text("When the lines' blocks ran, as the run history keeps it (in seconds, from when the first began)",
               timetable(times, by_line(LINES)))
        most = most_at_once(list(line_spans(times, LINES).values()))
        p.check(f"The run history shows never more than two lines at once (at most {most} together)", most == 2)
        p.check("Line 3 started only once line 1 had ended",
                times["load_stock"][0] >= times["load_sales"][1] - TICK)
        p.check("The run succeeded, with the same result", tab.runs[-1].status == "success"
                and json.loads(tab.scene.values[MERGER]["value"]) == MERGED)

        # --- 4. One after another -----------------------------------------------------------------------------------
        p.step(f"Click {SPLITTER} and choose one after another in its Lines run.")
        click_block(win, SPLITTER)
        choose_lines_run(p, win, "one after another")
        p.check("Lines run says one after another, and At most at once is no longer shown",
                field(win, "Lines run").currentText() == "one after another" and not row_shown(win, "At most at once"))
        click_empty(tab)
        p.shot(win, "The outlined numbers: on the canvas, the connections leaving the Splitter are numbered top to "
                    "bottom, 1 to load_sales, 2 to load_costs, 3 to load_stock.",
               highlight=number_areas(win, tab), zoom=[SPLITTER, *FIRSTS])
        p.check("The connections are numbered 1 load_sales, 2 load_costs, 3 load_stock, each near the Splitter",
                numbered(tab) == {1: "load_sales", 2: "load_costs", 3: "load_stock"})

        p.step("Press ▶ Run now.")
        start_run(win, tab)
        third = run_ended(qtbot, tab)
        said = "3 lines one after another: 1 load_sales, 2 load_costs, 3 load_stock"
        p.shot(win, f"① The Log: “{said}”. Below it, load_sales, load_costs and load_stock begin one after another, "
                    "about 1.5 seconds apart.",
               highlight=in_log(log, said), badges=True,
               zoom=[(log, f"run #{third} · "), *in_log(log, said), (log, f"DONE run #{third} ")])
        p.check(f"① The Log says once “{said}”", sum(said in line for line in log_lines(win, "INFO")) == 1)
        times = spans(win, third)
        p.text("When the lines' blocks ran, as the run history keeps it (in seconds, from when the first began)",
               timetable(times, by_line(LINES)))
        each = line_spans(times, LINES)
        p.check("Line 1 ended before line 2 started, and line 2 before line 3",
                each[1][1] <= each[2][0] + TICK and each[2][1] <= each[3][0] + TICK)
        p.check("costs_total, which uses line 1's output, ran in line 2: after load_costs, before line 3",
                times["load_costs"][1] - TICK <= times["costs_total"][0]
                and times["costs_total"][1] <= times["load_stock"][0] + TICK)
        p.check("The run succeeded, with the same result", tab.runs[-1].status == "success"
                and json.loads(tab.scene.values[MERGER]["value"]) == MERGED)

        p.step("Press Validate in the toolbar (F7).")
        press(win, win.validate_action)
        QApplication.processEvents()
        message = win.statusBar().currentMessage()
        said_there = status_message_area(win, message)
        p.shot(win, f"① Validate finds no problem: “{message}”, at the bottom of the window.",
               highlight=[said_there], badges=True, zoom=[said_there])
        p.check("① Validate finds no problem: no box came up, and the status bar says so",
                win.message_boxes == [] and message == f"{tab.doc.title}: no problems found")
        p.note("Validate refuses a layout that could never finish. One after another (or with At most at once lower "
               "than the number of lines), that is a connection from a later line into an earlier one; here "
               "costs_total, in line 2, takes from line 1, which has ended by then. A Splitter inside another "
               "Splitter's line is not supported yet: Validate refuses it. The lines of two Splitters side by side "
               "may feed each other one way only.")

        p.step("Drag load_stock up, above load_sales.")
        top = tab.scene.nodes["load_sales"].pos()
        drag_block(tab, "load_stock", QPointF(top.x(), top.y() - 140))
        QApplication.processEvents()
        p.check("load_stock is now above load_sales",
                tab.scene.nodes["load_stock"].pos().y() < tab.scene.nodes["load_sales"].pos().y())
        p.shot(win, "The outlined numbers follow: 1 to load_stock, now at the top, 2 to load_sales, 3 to load_costs.",
               highlight=number_areas(win, tab), zoom=[SPLITTER, *FIRSTS])
        p.check("The connections are numbered 1 load_stock, 2 load_sales, 3 load_costs",
                numbered(tab) == {1: "load_stock", 2: "load_sales", 3: "load_costs"})

        # --- 5. Dark --------------------------------------------------------------------------------------------------
        p.step("Choose Tools > Settings…, set Theme to Dark and press Save.")
        set_theme(p, win, "Dark", shoot=False)
        QApplication.processEvents()
        ratios = number_contrasts(tab)
        p.shot(win, "In Dark, the outlined numbers are light on the dark canvas: 1, 2 and 3 read well.",
               highlight=number_areas(win, tab), zoom=[SPLITTER, *FIRSTS])
        p.check("The editor is dark", QApplication.palette().window().color().lightness() < 128)
        p.check(f"Each number reads well: a contrast of at least {TEXT} to 1 with what is behind it ("
                + ", ".join(f"{n}: {r:.1f}" for n, r in sorted(ratios.items())) + ")",
                sorted(ratios) == [1, 2, 3] and min(ratios.values()) >= TEXT)

        p.step("Set Theme back to System (Tools > Settings…) and press Save.")
        set_theme(p, win, "System", shoot=False)
        p.shot(win, "The editor is light again (System: as this computer is), and the outlined numbers read as "
                    "before.",
               highlight=number_areas(win, tab), zoom=[SPLITTER, *FIRSTS])
        p.check("The Theme is System again, and the editor light",
                theme.chosen(win.settings) == "System" and QApplication.palette().window().color().lightness() >= 128)

        # --- 6. Without a Splitter ------------------------------------------------------------------------------------
        p.step("Open month-end-plain.yaml (File > Open…): the same blocks without the Splitter and the Merger; report "
               "takes the three outputs itself. Press ▶ Run now.")
        tab2 = open_flow(win, monkeypatch, plain)
        click_empty(tab2)
        start_run(win, tab2)
        fourth = run_ended(qtbot, tab2)
        times = spans(win, fourth)
        loading = [(log, f"loading {what} of") for what in ("sales", "costs", "stock")]
        p.shot(win, "Without a Splitter, the flow runs as before, one block at a time: ① in the Log, load_sales, "
                    "load_costs and load_stock each begin when the one before has ended, about 1.5 seconds apart.",
               highlight=[loading], badges=True,
               zoom=[(log, f"run #{fourth} · "), *loading, (log, f"DONE run #{fourth} ")])
        p.check(f"One at a time, in the flow's order ({', '.join(PLAIN_ORDER)}): each block started once the one "
                "before it had ended",
                sorted(times) == sorted(PLAIN_ORDER)
                and all(times[a][1] <= times[b][0] + TICK for a, b in zip(PLAIN_ORDER, PLAIN_ORDER[1:])))

        p.step("Rest the mouse on the line under report: its result.")
        tip = hover_result(qtbot, tab2, "report")
        p.shot(tip, f"report's result: “{REPORTED}”, the same as with the Merger: Python Code names what it "
                    "receives from several blocks as the Merger does.", over=win, zoom=[tip, "report"])
        p.check(f"The card shows report's result “{REPORTED}”", REPORTED in tip.text())
        p.check(f"report made “{REPORTED}”", REPORTED in tab2.scene.values.get("report", {}).get("output", ""))
        QTest.mouseMove(tab2.view.viewport(), tab2.view.viewport().rect().topLeft())
        QToolTip.hideText()
    finally:  # the Theme and the application's look as the proof found them; no run left going
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
        clean(win)  # (load_stock was moved after the last save: closing the window must not ask to save it)
