"""Proofs: a run that can no longer go on fails within about a second, saying which blocks wait for what, and a run
that is only slow is never ended; and three fixes seen in the 0.32.0 proof: the flow's tab after Stop, a Splitter
given no value, Run only this block on a Splitter. How proofs are written: README.md here."""

import datetime as dt
import sqlite3
import sys
from collections import Counter
from contextlib import closing

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

import taskloom.flow as flow_module
from proofs.conftest import _area, choose, menu_item, right_click
from proofs.test_proof_docs_help import in_window
from proofs.test_proof_line_failures import boxes_said, click_block, open_flow, press, settled
from proofs.test_proof_result_time import hover as hover_result
from proofs.test_proof_scheduler_start import click_empty
from proofs.test_proof_tab_plus import set_theme
from taskloom.editor import runs, theme
from taskloom.editor.canvas import STATUS_COLORS
from taskloom.editor.panels import status_icon
from tests.test_paths_editor_editing import flow_file
from tests.test_stuck_run import UNCHECKED

ASKED_NET = ("Agreed with you (2026-11-02), as part of the no-surprises rule: a run-time check that ends any stuck run "
             "with a clear message, so a layout Validate cannot judge never hangs silently (e.g. a scheduled run that "
             "never ends).")
ASKED_FIXES = ("From the 0.32.0 proof screenshots: after Stop the flow's tab showed a green success dot; a Splitter "
               "fed only by run after showed '✓ lines: None'; Run only this block on a Splitter logged '0 lines at the "
               "same time: ' with nothing after the colon.")
WITHIN = 2.5  # seconds: "within about a second" of getting stuck, with room for a slow machine (as the tests allow)
SLOW = 3  # seconds the Wait of the slow run waits
ON_TIME = 1.0  # seconds: a block that waited starts this soon after what it waited for
CANCELLED, SUCCESS = QColor(STATUS_COLORS["cancelled"]), QColor(STATUS_COLORS["success"])
FROM_SCHEDULE = "Run the flow now, as if its time had come"
ONLY = "Run only this block, with the same inputs"
RESULTS = "Results of this block…"
NOTHING = "its lines start with nothing: no value arrived"
BLOCKS_WIDTH, INSPECTOR_WIDTH, LOG_HEIGHT = 180, 360, 330  # a wider canvas, and a taller Log
LOG_PART = 760  # pixels: the left part of the Log a close-up shows (its lines here are wider than a close-up)


# --- the flows -------------------------------------------------------------------------------------------------------

def python(code: str, inputs=("input",), outputs=("output",), x=0, y=0) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code},
            "ui": {"x": x, "y": y}}


def manual(x=0, y=0) -> dict:
    return {"type": "trigger.manual", "ui": {"x": x, "y": y}}


def splitter(lines_run: str, x=0, y=0) -> dict:
    return {"type": "logic.splitter", "config": {"lines_run": lines_run}, "ui": {"x": x, "y": y}}


def merger(x=0, y=0) -> dict:
    return {"type": "logic.merger", "ui": {"x": x, "y": y}}


def wait(seconds: int, x=0, y=0) -> dict:
    return {"type": "logic.wait", "config": {"seconds": seconds}, "ui": {"x": x, "y": y}}


LINES_1 = ["load_sales", "load_costs", "load_stock"]  # top to bottom: lines 1, 2 and 3 of split
NEVER = "load_stock.output -> load_sales.stock"  # from line 3 into line 1's first block


def every_morning(folder):
    """The case of a scheduled run that would never end: every_morning starts pick_day, then split runs its lines one
    after another, top to bottom; load_sales, line 1, also takes what load_stock, line 3, makes (NEVER); merge joins
    them."""
    return flow_file(folder / "every-morning.yaml", {
        "every_morning": {"type": "trigger.schedule", "config": {"cron": "0 7 * * *"}, "ui": {"x": 0, "y": 150}},
        "pick_day": python("output = 'monday'", inputs=(), x=210, y=165),
        "split": splitter("one after another", x=420, y=165),
        "load_sales": python("output = ['sales of ' + input, stock]", ("input", "stock"), x=630, y=0),
        "load_costs": python("output = 'costs of ' + input", x=630, y=165),
        "load_stock": python("output = 'stock of ' + input", x=630, y=330),
        "merge": merger(x=840, y=165),
    }, ["every_morning.start -> pick_day.after", "pick_day.output -> split.value",
        *(f"split.lines -> {b}.input" for b in LINES_1), NEVER, *(f"{b}.output -> merge.lines" for b in LINES_1)])


SLOW_LINES = ["split", "load_sales", "wait_for_stock", "merge"]


def slow_stock(folder):
    """Two lines at the same time: load_sales, line 1, also takes what wait_for_stock, line 2, a Wait of SLOW seconds,
    passes on; merge joins them."""
    return flow_file(folder / "slow-stock.yaml", {
        "start_by_hand": manual(x=0, y=110), "pick_day": python("output = 'monday'", inputs=(), x=210, y=110),
        "split": splitter("at the same time", x=420, y=110),
        "load_sales": python("output = ['sales of ' + input, stock]", ("input", "stock"), x=630, y=0),
        "wait_for_stock": wait(SLOW, x=630, y=210), "merge": merger(x=840, y=110),
    }, ["start_by_hand.start -> pick_day.after", "pick_day.output -> split.value", "split.lines -> load_sales.input",
        "split.lines -> wait_for_stock.value", "wait_for_stock.value -> load_sales.stock",
        "load_sales.output -> merge.lines", "wait_for_stock.value -> merge.lines"])


def long_wait(folder):
    """start_by_hand, then a Wait of a minute: running until stopped."""
    return flow_file(folder / "long-wait.yaml", {"start_by_hand": manual(x=0, y=0),
                                                 "wait_a_minute": wait(60, x=230, y=0)},
                     ["start_by_hand.start -> wait_a_minute.after"])


LINES_2 = ["load_sales", "load_costs"]


def lines_start(folder):
    """A Splitter connected only by run after (nothing at its value input): its two lines, joined by merge."""
    return flow_file(folder / "lines-start.yaml", {
        "start_by_hand": manual(x=0, y=110), "split": splitter("at the same time", x=230, y=110),
        "load_sales": python("output = 'sales'", x=460, y=30), "load_costs": python("output = 'costs'", x=460, y=190),
        "merge": merger(x=690, y=110),
    }, ["start_by_hand.start -> split.after", *(f"split.lines -> {b}.input" for b in LINES_2),
        *(f"{b}.output -> merge.lines" for b in LINES_2)])


# --- what the run history keeps --------------------------------------------------------------------------------------

def kept(win, run_id: int) -> tuple[dict, dict]:
    """The run (its status, when it started and finished) and each block's result in it (status, error, summary,
    started, finished), as the run history (history.db) keeps them."""
    with closing(sqlite3.connect(str(win.home.history_db))) as db:
        status, started, finished = db.execute("SELECT status, started, finished FROM runs WHERE id=?",
                                               (run_id,)).fetchone()
        blocks = {b: {"status": s, "error": e or "", "summary": m or "", "started": a, "finished": f}
                  for b, s, e, m, a, f in db.execute("SELECT block, status, error, summary, started, finished "
                                                     "FROM blocks WHERE run_id=?", (run_id,))}
    return {"status": status, "started": started, "finished": finished}, blocks


def when(text: str | None) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


def timetable(run: dict, blocks: dict) -> str:
    """Each block that finished, in the order it did, with how it ended and when, in seconds from the run's start."""
    zero = when(run["started"])
    rows = sorted((when(b["finished"]), name, b["status"]) for name, b in blocks.items() if b["finished"])
    return "\n".join([f"{(at - zero).total_seconds():5.2f} s  {name:15} {status}" for at, name, status in rows]
                     + [f"{(when(run['finished']) - zero).total_seconds():5.2f} s  the run ended:  {run['status']}"])


# --- what the editor shows -------------------------------------------------------------------------------------------

def make_room(win):
    """Drag the Blocks list's right edge and the panels' left edge in, and the Log's top edge up."""
    win.resizeDocks([win.blocks_dock, win.inspector], [BLOCKS_WIDTH, INSPECTOR_WIDTH], Qt.Horizontal)
    win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)
    QTest.qWait(100)


def statuses(tab, blocks) -> dict:
    return {b: tab.scene.nodes[b].status for b in blocks}


def in_log(win, text: str) -> tuple:
    """The Log's lines showing `text` (to outline), checked to be there."""
    assert text in win.log.text.toPlainText(), f"the Log does not show {text!r}"
    return win.log.text, text


def left_part(win, *targets) -> QRect:
    """The left part, LOG_PART pixels wide, of the Log's lines `targets` show (see in_log), in the window's pixels."""
    area = QRect()
    for target in targets:
        area |= _area(target, win)[0]
    return QRect(area.left(), area.top(), LOG_PART, area.height())


def tab_area(win, tab) -> QRect:
    """Where the flow's tab is, in the window's pixels."""
    bar = win.tabs.tabBar()
    return in_window(win, bar, bar.tabRect(win.tabs.indexOf(tab)))


def tab_icon(win, tab):
    return win.tabs.tabIcon(win.tabs.indexOf(tab)).pixmap(win.tabs.iconSize()).toImage()


def tab_shows(win, tab, status: str) -> bool:
    """The flow's tab shows the icon of `status`, as the editor draws it (a flow with no schedule: no clock beside)."""
    return tab_icon(win, tab) == status_icon(status).pixmap(win.tabs.iconSize()).toImage()


def icon_colour(win, tab) -> QColor:
    """The colour the tab's icon is drawn in: the most common of its solid pixels."""
    image = tab_icon(win, tab)
    found = Counter(image.pixelColor(x, y).rgb() for x in range(image.width()) for y in range(image.height())
                    if image.pixelColor(x, y).alpha() == 255)
    return QColor.fromRgb(found.most_common(1)[0][0]) if found else QColor()


def run_ended(qtbot, win, tab, n: int) -> int:
    """Wait for the tab's n-th run to end (within 30 seconds: none here takes longer): its number."""
    qtbot.waitUntil(lambda: len(tab.runs) == n and not tab.running and tab.runs[-1].status is not None,
                    timeout=30_000)
    settled(win, qtbot)
    return tab.runs[-1].run_id


def no_tooltip(tab):
    QTest.mouseMove(tab.view.viewport(), tab.view.viewport().rect().topLeft())
    QToolTip.hideText()
    QApplication.processEvents()


# --- 1: the safety net -----------------------------------------------------------------------------------------------

def test_a_run_that_can_no_longer_go_on_fails_at_once_saying_what_waits_and_a_slow_one_goes_on(
        proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("A run that can no longer go on fails at once, saying which blocks wait for what; a slow run goes on",
              ASKED_NET)
    p.idea("A run that can no longer go on now ends within about a second, failed, and says why. The Log names each "
           "block that waits and what it waits for: “the run cannot go on: load_sales waits for load_stock (line 3 of "
           "split), …”. Each of those blocks ends failed, saying what it waited for, and the blocks after them end as "
           "after any failure.")
    p.idea("A run can no longer go on when nothing in it is at work, and every block still to run waits for something "
           "nothing can bring any more: a line's turn that never comes, or the output of a block that cannot start. A "
           "block that is running is at work, however long it takes: a slow query, a Wait, a question waiting for "
           "your answer, a block waiting to try again. So a run that is only slow is never ended.")
    p.idea("Validate (F7) still refuses, before anything runs, the layouts known never to finish, as since 0.32.0. "
           "This is the net under it: should a layout Validate cannot judge get stuck, the run does not hang "
           "silently, as a scheduled run that never ends would. It fails, and says where.")
    p.why_simplest("It watches one thing: whether any part of the run is at work. Each part of a run that waits (a "
                   "block for the blocks before it, a line for its turn, a Loop for its items, the run for its lines) "
                   "says so while it waits. Once nothing has been at work for half a second, and nothing went back "
                   "to work meanwhile, nothing ever can: every part waits for another. A waiting part looks again at "
                   "least ten times a second, so half a second is well over what it needs to notice that what it "
                   "waited for has come.")
    p.why_simplest("There is no time limit to choose. A run is ended for being stuck, never for being slow, and it "
                   "tells the two apart by what is at work, not by a clock. It then ends the way a failure does: the "
                   "waiting blocks fail, saying what they waited for, what follows them fails or is skipped as after "
                   "any failure, and the run counts as failed, with every block's result in Run history.")
    p.alternative("A time limit on every run (end it after an hour, say)",
                  "Which limit? A month-end run may take hours, and a stuck run would hang for the whole limit before "
                  "you heard of it. Being slow and being stuck are different: telling them apart by what is at work, "
                  "a slow run is never ended and a stuck one ends at once.")
    p.alternative("Only Validate, before the run",
                  "Validate refuses the layouts known never to finish, and still does. But a check before the run can "
                  "only judge the layouts it knows; the net catches whatever gets stuck, as it happens.")
    p.alternative("Leave the run waiting, with a warning in the Log",
                  "The run would still never end, and never show as failed; at night nobody reads the Log. As agreed "
                  "(no surprises: when something is wrong, fail, and let you decide), it fails, and shows in red like "
                  "any failure.")
    p.fits("Before anything runs, Validate (F7), Tools > Check flows, taskloom validate and every run's own check "
           "refuse the same layouts as before. A run that is not stuck goes exactly as before: the net only watches.")
    p.fits("The waiting blocks end as failed blocks always do: red on the canvas, their error in the Log and in Run "
           "history. A Merger fails, naming the failed line, and the blocks after it are skipped. Each item of a "
           "Loop, and each line's run of a Merger set to each line as it finishes, is watched as part of the same "
           "run: a Loop whose item gets stuck fails.")
    p.fits("The net is part of the run itself, so it works the same whether the scheduler service started the run, "
           "the editor did, or taskloom run in a terminal.")
    folder = tmp_path / "flows"
    folder.mkdir()
    log = win.log.text

    # --- a run that would wait forever ---------------------------------------------------------------------------
    p.step("Open the flow every-morning.yaml (File > Open…). Its Schedule block every_morning starts pick_day, which "
           "gives the day to the Splitter split. split runs its three lines one after another, top to bottom: "
           "load_sales, load_costs, load_stock; merge joins them. load_sales also takes what load_stock makes: line 1 "
           "would wait for line 3, which cannot start until line 1 has ended.")
    p.note("For this proof the Blocks list and the panels on the right are made narrower, and the Log taller (their "
           "edges dragged), so that the canvas and the Log show more.")
    make_room(win)
    tab = open_flow(win, monkeypatch, every_morning(folder))
    p.check("The flow opens", tab is not None and tab.doc.path.name == "every-morning.yaml")
    p.shot(win, "① load_stock's output, at the end of line 3, goes up into ② load_sales, the block line 1 starts "
                "with. The numbers 1, 2, 3 on split's connections are the order its lines run in.",
           highlight=["load_stock", "load_sales"], badges=True, zoom=["split", *LINES_1, "merge"])

    p.step("Press Validate in the toolbar (F7).")
    said = boxes_said(p, monkeypatch, lambda: press(win, win.validate_action),
                      "Validate refuses this layout before anything runs: line 1 would wait for line 3 forever. So "
                      "would every run's own check, the scheduler's too.")
    p.check("Validate refuses the connection load_stock.output -> load_sales.stock, from line 3 into line 1",
            len(said) == 1 and said[0].startswith(f"edge {NEVER}: from line 3 of split into line 1"))
    p.note("No layout Validate accepts is known to get stuck: the net is there for those it cannot judge. So, to show "
           "the net at work, this proof turns off that one check of Validate (the check of the lines' layouts) for "
           "the next step only: in the editor and in the run it starts, both this proof's own processes, as the "
           "tests of this change do. Nothing else changes: the run goes as it would.")

    p.step("Right-click every_morning and choose “Run the flow now, as if its time had come”: the run its schedule "
           "starts.")
    check = flow_module._lines_problems
    with monkeypatch.context() as unchecked:  # Validate's check of the lines' layouts: no errors (its warnings kept)
        unchecked.setattr(flow_module, "_lines_problems", lambda *a: ([], check(*a)[1]))
        unchecked.setattr(runs, "runner_command", lambda: [sys.executable, "-c", UNCHECKED])
        p.while_open(lambda: right_click(tab, "every_morning"), lambda menu: choose(menu, FROM_SCHEDULE))
        first = run_ended(qtbot, win, tab, 1)
    run, ended = kept(win, first)
    p.shot(win, "The run ended, failed. ① split started its lines, then nothing could move: ② load_sales, load_costs "
                "and load_stock failed (red), each saying what it waited for; ③ merge failed, as a Merger does when "
                "a line failed.",
           highlight=["split", LINES_1, "merge"], badges=True, zoom=["split", *LINES_1, "merge"])
    moved = max(when(b["finished"]) for b in ended.values() if b["status"] == "success")
    took = (when(run["finished"]) - moved).total_seconds()
    p.text("When each block finished, and the run, as the run history keeps it (in seconds from the run's start)",
           timetable(run, ended))
    p.check(f"Run #{first} failed", run["status"] == "failed" and tab.runs[-1].status == "failed")
    p.check("① every_morning, pick_day and split succeeded", all(ended[b]["status"] == "success"
                                                                 for b in ("every_morning", "pick_day", "split")))
    p.check("② load_sales, load_costs and load_stock each ended failed, and none of them ran its code: none made an "
            "output", statuses(tab, LINES_1) == dict.fromkeys(LINES_1, "failed")
            and all(ended[b]["status"] == "failed" for b in LINES_1)
            and not any(tab.scene.values.get(b, {}).get("output") for b in LINES_1))
    p.check("③ merge failed; every block has a result, none was left running",
            ended["merge"]["status"] == "failed" and sorted(ended) == sorted(tab.doc.blocks)
            and "running" not in {b["status"] for b in ended.values()})
    p.check(f"From the last block that finished, split, to the end of the run: {took:.2f} s (within about a second; "
            f"at most {WITHIN:g} s allowed, for a slow machine)",
            0 <= took <= WITHIN and moved == when(ended["split"]["finished"]))

    p.step("Look at the Log, under the canvas.")
    stuck = "the run cannot go on: load_sales waits for load_stock (line 3 of split)"
    waited = {"load_sales": "it waits for load_stock (line 3 of split)",
              "load_costs": "it waits for its turn (line 2 of split, which runs its lines one after another)",
              "load_stock": "it waits for its turn (line 3 of split, which runs its lines one after another)"}
    errors = [in_log(win, f"[{b}] ERROR {b} (Python Code) failed: the run cannot go on: {w}")
              for b, w in waited.items()]
    ended_line = in_log(win, f"run #{first} failed")
    p.shot(win, "The outlined lines, top to bottom. First, the run cannot go on, and which blocks wait for what: "
                "load_sales for load_stock, in line 3; load_costs and load_stock for their turn, which never comes. "
                "Then each of the three failed, its error saying what it waited for, and under it what it got: "
                "load_sales got nothing from load_stock. Last, the run failed. The lines go on to the right of the "
                "close-up: they are written out in full above.",
           highlight=[in_log(win, stuck), errors, ended_line],
           zoom=[left_part(win, in_log(win, "[split] INFO 3 lines one after another"), ended_line)])
    text = log.toPlainText()
    said = next(line for line in text.splitlines() if stuck in line)
    p.text("The Log's lines in full (they are wider than the Log)",
           "\n".join(line for line in text.splitlines() if "the run cannot go on" in line and "[merge]" not in line))
    p.check(f"The Log says “{stuck}, load_costs waits for its turn (line 2 of split, …), load_stock waits for its "
            "turn (line 3 of split, …)”",
            said.endswith(f"{stuck}, load_costs waits for its turn (line 2 of split, which runs its lines one after "
                          "another), load_stock waits for its turn (line 3 of split, which runs its lines one after "
                          "another)"))
    p.check("Each waiting block's result says what it waited for, in the run history too: "
            + "; ".join(f"{b}: “{w}”" for b, w in waited.items()),
            all(ended[b]["error"] == f"the run cannot go on: {w}" for b, w in waited.items()))
    under = text.split("[load_sales] ERROR")[-1].split("[load_costs]")[0]  # (load_sales' error, and what it got)
    p.check("Under load_sales' error, what it got: “stock ← load_stock.output: (nothing arrived)”",
            "stock ← load_stock.output: (nothing arrived)" in under)
    p.check(f"The Log says run #{first} failed", f"ERROR run #{first} failed" in text)

    p.step("Rest the mouse on the line under load_sales.")
    click_empty(tab)
    tip = hover_result(qtbot, tab, "load_sales")
    p.shot(tip, "Its result, in full: it failed because the run could not go on, as it waited for load_stock, in "
                "line 3 of split.", over=win, zoom=[tip, "load_sales"])
    p.check("The card says “the run cannot go on: it waits for load_stock (line 3 of split)”",
            "the run cannot go on: it waits for load_stock (line 3 of split)" in tip.text())
    p.note("About these messages: one case is said less exactly. When the blocks that are stuck are among those a "
           "Merger set to each line as it finishes runs once per line, the message can name the blocks after that "
           "Merger as waiting for it, rather than the blocks inside its runs. The run still ends within about a "
           "second, failed.")
    no_tooltip(tab)

    # --- a run that is only slow ---------------------------------------------------------------------------------
    p.step(f"Open slow-stock.yaml (File > Open…) and press ▶ Run now. Its Splitter runs its two lines at the same "
           f"time: line 1's load_sales also takes what line 2's wait_for_stock, a Wait of {SLOW} seconds, passes "
           "on.")
    p.note("Validate's check is on again from here on. This layout is fine: its lines start together, so line 2 is "
           "under way while line 1 waits for it.")
    slow = open_flow(win, monkeypatch, slow_stock(folder))
    click_empty(slow)
    press(win, win.run_action)
    nodes = slow.scene.nodes
    qtbot.waitUntil(lambda: nodes["wait_for_stock"].status == "running" and "left" in nodes["wait_for_stock"].countdown,
                    timeout=30_000)
    QTest.qWait(500)  # (into its wait)
    seen = statuses(slow, ["wait_for_stock", "load_sales"])
    p.shot(win, f"During the run: for {SLOW} seconds nothing is at work but ① wait_for_stock, counting down, while ② "
                "load_sales waits for it. The run is only slow: it goes on.",
           highlight=["wait_for_stock", "load_sales"], badges=True, zoom=SLOW_LINES)
    p.check("① wait_for_stock is running, and ② load_sales has not started: it waits",
            seen == {"wait_for_stock": "running", "load_sales": None})
    second = run_ended(qtbot, win, slow, 1)
    run, ended = kept(win, second)
    p.shot(win, "The run went on to its end, all green: ② load_sales started as soon as ① wait_for_stock had passed "
                "the day on, and merge followed: the run succeeded.",
           highlight=["wait_for_stock", "load_sales"], badges=True, zoom=SLOW_LINES)
    p.text("When each block finished, and the run, as the run history keeps it (in seconds from the run's start)",
           timetable(run, ended))
    waited = (when(ended["wait_for_stock"]["finished"]) - when(ended["wait_for_stock"]["started"])).total_seconds()
    after = (when(ended["load_sales"]["started"]) - when(ended["wait_for_stock"]["finished"])).total_seconds()
    p.check(f"Run #{second} succeeded, every block with it", run["status"] == "success"
            and {b["status"] for b in ended.values()} == {"success"} and len(ended) == len(slow.doc.blocks))
    p.check(f"① wait_for_stock really waited: {waited:.2f} s, while load_sales waited for it", waited >= SLOW - 0.5)
    p.check(f"② load_sales started {after:.2f} s after wait_for_stock ended (as soon as it could)",
            -0.02 <= after <= ON_TIME)
    p.check("The Log never says this run cannot go on",
            "cannot go on" not in log.toPlainText().split(f"run #{second} · ")[-1])


# --- 2: three fixes -------------------------------------------------------------------------------------------------

def test_after_stop_the_tab_shows_cancelled_a_splitter_given_nothing_says_so_and_run_only_says_no_line_runs(
        proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Three fixes: the tab after Stop, a Splitter given no value, Run only this block on a Splitter",
              ASKED_FIXES)
    p.idea("After Stop, the flow's tab shows the run as cancelled: a grey ✕, the grey the canvas and Run history use "
           "for a cancelled run, not the green dot of success. A failed line still comes first: with several start "
           "blocks, the tab is red when a line's last run failed, else grey when one was stopped, else green.")
    p.idea(f"A Splitter connected only by run after (nothing at its value input) says under it “{NOTHING}”, not "
           "“lines: None”, as if None were a value. Its lines run as before, each getting nothing.")
    p.idea("Run only this block on a Splitter runs the Splitter alone, and its Log line says so: “no line runs: only "
           "split runs”, not “0 lines at the same time: ” with nothing after the colon.")
    p.why_simplest("Each fix changes only the words or the colour that were wrong. The tab already had a grey ✕ for a "
                   "cancelled run; it now picks it when a line's last run was stopped, as it picked the red ✕ for a "
                   "failed one. The Splitter's line under it and its Log line keep their places, with plain words for "
                   "the case of nothing. No new setting, nothing to learn.")
    p.alternative("Show a stopped run in red on the tab, as failed",
                  "You stopped it: nothing went wrong. Red would send you looking for an error that is not there. The "
                  "canvas and Run history already say cancelled, in grey, and the tab now says the same.")
    p.alternative("Show nothing under a Splitter that got no value",
                  "The line under a block is where you look for what it did. Saying plainly that its lines started "
                  "with nothing tells you its value input is not connected, which may be a mistake to fix.")
    p.alternative("Leave out the Splitter's Log line when no line runs",
                  "A Splitter always says how its lines run. Saying that none does, and why (only it runs), shows "
                  "that Run only this block did what you asked, and leaves nothing to wonder about.")
    p.fits("The tab shows each start block's line by its last run, as before; its tooltip lists them "
           "(start_by_hand: cancelled …). Failed, then cancelled, then success: the most serious comes first.")
    p.fits("The Splitter's words are kept with its result in the run history, so Results of this block… says the "
           "same as the canvas. A Splitter that gets a value shows it, as before.")
    folder = tmp_path / "flows"
    folder.mkdir()
    log = win.log.text
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    try:
        p.step("Choose Tools > Settings…, set Theme to Light and press Save.")
        set_theme(p, win, "Light", shoot=False)
        p.check("The editor is light", theme.chosen(win.settings) == "Light"
                and QApplication.palette().window().color().lightness() >= 128)

        # --- the tab after Stop ---------------------------------------------------------------------------------
        p.step("Open long-wait.yaml (File > Open…) and press ▶ Run now. start_by_hand starts wait_a_minute, a Wait of "
               "60 seconds.")
        p.note("For this proof the Blocks list and the panels on the right are made narrower, and the Log taller "
               "(their edges dragged), so that the canvas and the Log show more.")
        make_room(win)
        tab = open_flow(win, monkeypatch, long_wait(folder))
        press(win, win.run_action)
        qtbot.waitUntil(lambda: tab.scene.nodes["wait_a_minute"].status == "running", timeout=30_000)
        QTest.qWait(500)
        p.shot(win, "Before Stop: ① the flow's tab shows its run going, a turning circle left of its name (the ✕ at "
                    "its right end closes the tab); ② wait_a_minute is running.",
               highlight=[tab_area(win, tab), "wait_a_minute"], badges=True, zoom=[tab_area(win, tab)])
        p.check("① The tab shows the run going", tab.shown_status == ("running", False))

        p.step("Click Stop in the toolbar.")
        press(win, win.stop_action)
        first = run_ended(qtbot, win, tab, 1)
        colour = icon_colour(win, tab)
        tip = win.tabs.tabToolTip(win.tabs.indexOf(tab)).splitlines()
        p.shot(win, "After Stop: ① the flow's tab shows a grey ✕ left of its name: cancelled, not the green dot of "
                    "success; ② wait_a_minute is cancelled, in the same grey.",
               highlight=[tab_area(win, tab), "wait_a_minute"], badges=True, zoom=[tab_area(win, tab)])
        p.check(f"Run #{first} was cancelled, and so was wait_a_minute", tab.runs[-1].status == "cancelled"
                and tab.scene.nodes["wait_a_minute"].status == "cancelled")
        p.check("① The tab shows the icon of a cancelled run, not that of success",
                tab_shows(win, tab, "cancelled") and not tab_shows(win, tab, "success"))
        p.check(f"① Its ✕ is drawn in {colour.name().upper()}: the grey the canvas draws a cancelled block's outline "
                f"and line in ({CANCELLED.name().upper()}), not the green of success ({SUCCESS.name().upper()})",
                colour == CANCELLED)
        p.check("Its tooltip says start_by_hand: cancelled", any(line.startswith("start_by_hand: cancelled")
                                                                 for line in tip))

        # --- a Splitter given no value ------------------------------------------------------------------------------
        p.step("Open lines-start.yaml (File > Open…) and press ▶ Run now. Its Splitter, split, is connected only by "
               "run after: nothing reaches its value input. Its two lines, load_sales and load_costs, are joined by "
               "merge.")
        lines = open_flow(win, monkeypatch, lines_start(folder))
        click_empty(lines)
        press(win, win.run_action)
        second = run_ended(qtbot, win, lines, 1)
        node = lines.scene.nodes["split"]
        p.shot(win, "① Under split: “✓ its lines start with nothing: …”, cut short to fit the block, and no “lines: "
                    "None”. ② Its lines ran, and merge joined them: the run succeeded.",
               highlight=["split", [*LINES_2, "merge"]], badges=True,
               zoom=["start_by_hand", "split", *LINES_2, "merge"])
        _, ended = kept(win, second)
        p.check(f"② Run #{second} succeeded, every block with it", lines.runs[-1].status == "success"
                and {b["status"] for b in ended.values()} == {"success"} and len(ended) == len(lines.doc.blocks))
        p.check(f"① The line under split is “✓ {NOTHING}”, shown as far as it fits: no “None”",
                node.status_text == f"✓ {NOTHING}" and "None" not in node.status_text)

        p.step("Right-click split and choose “Results of this block…”.")
        rows = []

        def look_at_results(dialog):
            table = dialog.table
            rows.extend([table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount()))
            p.shot(dialog, f"split's result in run #{second}, in full: ① “{NOTHING}”.",
                   highlight=[table.item(0, 4)], badges=True)

        p.while_open(lambda: right_click(lines, "split"),
                     lambda menu: p.while_open(lambda: choose(menu, RESULTS), look_at_results))
        p.check(f"① Results of this block… says, for run #{second}: success, “{NOTHING}”",
                [(r[0], r[3], r[4]) for r in rows] == [(str(second), "success", NOTHING)])
        p.check("The run history keeps the same words", ended["split"]["summary"] == NOTHING)
        p.note("What a Splitter connected only by run after passes on to its lines is still nothing: Python's None, "
               "which is what each line's first block gets, as before. Where its values are shown (the lower part of "
               "this window, and the card when you rest the mouse on the line under split or on its lines output), "
               "that value shows as None.")

        tabs = [tab_area(win, tab), tab_area(win, lines)]
        p.shot(win, "The two flows' tabs side by side: ① long-wait.yaml, stopped: the grey ✕; ② lines-start.yaml, "
                    "which succeeded: the green dot.",
               highlight=tabs, badges=True, zoom=tabs)
        p.check("① long-wait.yaml's tab shows cancelled; ② lines-start.yaml's shows success, in green",
                tab_shows(win, tab, "cancelled") and tab_shows(win, lines, "success")
                and icon_colour(win, lines) == SUCCESS)

        # --- Run only this block on a Splitter ------------------------------------------------------------------------
        p.step("Right-click split and choose “Run only this block, with the same inputs”.")

        def run_only(menu):
            p.shot(menu, "split's menu, with the item to choose.", over=win, highlight=[menu_item(menu, ONLY)],
                   zoom=[menu, "split"])
            choose(menu, ONLY)

        p.while_open(lambda: right_click(lines, "split"), run_only)
        third = run_ended(qtbot, win, lines, 2)

        p.step("Click split on the canvas: the Log shows only its lines, with each run's start and end.")
        click_block(lines, "split")
        said = "[split] INFO no line runs: only split runs"
        shown = [in_log(win, f"run #{third} · "), in_log(win, "[split] INFO running only split"), in_log(win, said),
                 in_log(win, f"run #{third} success")]
        p.shot(win, f"The outlined line: in run #{third}, split says “no line runs: only split runs”. load_sales and "
                    f"load_costs did not run: they still show their results from run #{second}.",
               highlight=[in_log(win, said)], zoom=shown)
        _, ended = kept(win, third)
        mine = log.toPlainText().split(f"run #{third} · ")[-1]
        p.check(f"Run #{third} ran split alone, and succeeded", lines.runs[-1].status == "success"
                and set(ended) == {"split"} and ended["split"]["status"] == "success")
        p.check("The Log says “no line runs: only split runs”", said in mine)
        p.check("No Log line of split ends with a colon, nor says “0 lines”",
                not any(line.rstrip().endswith(":") or "0 lines" in line for line in mine.splitlines()
                        if "[split]" in line))
        p.check(f"load_sales and load_costs still show their results from run #{second}",
                all(lines.scene.nodes[b].status == "success" and lines.result_run(b) == second for b in LINES_2))
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
