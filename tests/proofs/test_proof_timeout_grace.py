"""Proof (found by pull request #1): a block that finishes just after its timeout, without having seen the stop, keeps
its result; one that saw the stop still times out and is retried. How proofs are written: README.md here."""

import re
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFileDialog, QGroupBox

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_scheduler_start import click_block, file_menu
from taskloom.editor.panels import CodeEdit
from tests.test_editor import write_flow

ASKED = ("Fix 4: a block that ignores its timeout but finishes within the 2-second grace after it. What should "
         "count? … Accept its result — if it finishes in the grace without having seen the stop signal, keep its "
         "result, so finished work is not redone")
FLOW = """
    blocks:
      start_append:
        type: trigger.manual
        ui: {x: 0, y: 0}
      append_sale:
        type: logic.python
        timeout: 1s
        retry: {policy: fixed, max_attempts: 2, delay: 1s}
        config:
          inputs: []
          code: |
            import time
            print(f"attempt {ctx.attempt}: appending a row")
            began = time.monotonic()
            time.sleep(2)  # slow work; it never looks at ctx
            with open("sales-log.csv", "a") as sales:
                sales.write("today,1250\\n")
            took = time.monotonic() - began
            with open("sales-log.csv") as sales:
                rows = len(sales.readlines())
            print(f"done after {took:.1f} s: {rows} row(s)")
            output = f"{rows} row(s)"
        ui: {x: 260, y: 0}
      start_load:
        type: trigger.manual
        ui: {x: 0, y: 200}
      load_chunks:
        type: logic.python
        timeout: 1s
        retry: {policy: fixed, max_attempts: 2, delay: 1s}
        config:
          inputs: []
          code: |
            import time
            print(f"attempt {ctx.attempt}: loading 30 chunks")
            began = time.monotonic()
            done = 0
            while done < 30 and not ctx.cancelled:
                time.sleep(0.1)  # one chunk
                done += 1
            took = time.monotonic() - began
            if done < 30:
                print(f"saw the stop after {took:.1f} s: "
                      f"stops with {done} of 30 chunks")
            output = f"{done} of 30 chunks loaded"
        ui: {x: 260, y: 200}
    edges:
      - start_append.start -> append_sale.after
      - start_load.start -> load_chunks.after
"""
LATE = ["start_append", "append_sale"]  # line ①: finishes in the grace, never looking
STOPS = ["start_load", "load_chunks"]  # line ②: looks, and stops early
TIMED_OUT = "TimeoutError: timed out after 1 s"
LOG_HEIGHT = 330  # the Log made taller, to show a whole run with its retry


def recorded(win, run_id: int, block: str) -> tuple:
    """What the run history holds of a block in a run: its status, how many attempts, its error."""
    with sqlite3.connect(win.home.history_db) as db:
        return db.execute("SELECT status, attempts, error FROM blocks WHERE run_id=? AND block=?",
                          (run_id, block)).fetchone()


def properties(win) -> tuple:
    """The code box and the Retry and timeout box that Properties shows for the selected block."""
    return (win.properties.findChildren(CodeEdit)[0],
            next(b for b in win.properties.findChildren(QGroupBox) if b.title() == "Retry and timeout"))


def run_line(p, win, qtbot, tab, line: list, runs: int, shoot_menu: bool = False) -> int:
    """Right-click the line's start block and choose Run the flow from here; wait for that run (the flow's `runs`th)
    to end."""
    start = line[0]

    def run_from_here(menu):
        if shoot_menu:
            p.shot(menu, f"The menu of {start}, with the item to choose.", over=win,
                   highlight=[menu_item(menu, "Run the flow from here")], zoom=[menu, *line])
        choose(menu, "Run the flow from here")

    p.while_open(lambda: right_click(tab, start), run_from_here)
    qtbot.waitUntil(lambda: len(tab.runs) == runs and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)
    return tab.runs[-1].run_id


def test_a_block_that_finishes_in_the_grace_without_seeing_the_stop_keeps_its_result(proof, win, qtbot, tmp_path,
                                                                                     monkeypatch):
    p = proof("A block that finishes just after its timeout keeps its result", ASKED)
    p.idea("A block that finishes its work just after its timeout, in the 2-second grace, without having looked "
           "whether it was told to stop, now keeps its result: it succeeds, once, and is not retried, so its work is "
           "not done twice. A block that did look, saw the stop and ended early still fails as a timeout, and is "
           "retried, as before.")
    p.idea("When a block's Timeout is up, Taskloom tells it to stop and gives it 2 seconds to end. A block that waits "
           "or loops can look whether it was told to stop, and end early. A block that never looks, such as a few "
           "lines of Python Code that do one long thing, or a Send Email whose mail server answers late, goes on with "
           "its work and may finish it in those 2 seconds.")
    p.idea("Before, such an attempt was always counted as timed out: its result was thrown away and, with retries, "
           "its work was done again (a second email, a row appended twice), and if every attempt took as long the "
           "block failed in the end anyway.")
    p.idea("Another agent found this (pull request #1). Its fix was not taken as it was: it kept the result of every "
           "block that ended in the grace, also of a block that saw the stop and ended early on purpose with only "
           "part of its work, as load_chunks does here, and recorded that as a success.")
    p.why_simplest("A block can find out that it was told to stop only by looking, and Taskloom now notes when it "
                   "looks and the answer is yes. At the end of the grace one rule decides: the attempt keeps its "
                   "result if the block finished, without an error, and never saw the stop. Otherwise it is a "
                   "timeout, as before: it saw the stop, it ended with an error, or it was still going.")
    p.why_simplest("Nothing new to set, and nothing to change in your blocks: the same Timeout, the same Retry, the "
                   "same 2-second grace.")
    p.alternative("Keep the result of every block that finishes in the grace (pull request #1's fix)",
                  "A block that saw the stop and ended early on purpose may return part of its work: it would be "
                  "recorded as a success, and the blocks after it would go on with part of the data. Whether it saw "
                  "the stop is what tells the two apart.")
    p.alternative("Keep counting it as a timeout, and retry it, as before",
                  "Its work was finished, and the retry did it again: an email sent twice, rows appended twice. You "
                  "chose to accept its result.")
    p.alternative("Count it as a timeout, but do not retry it",
                  "Its work would not be redone, but the block would show a failure though its work was done, and "
                  "the blocks after it would not run: you would have to run them by hand, with nothing to fix.")
    p.fits("It is the same Timeout and Retry as before, in Properties → Retry and timeout, and the same 2-second "
           "grace a block gets when you press Stop. Stopping a run is unchanged: a block that finishes in the grace "
           "after you press Stop is still cancelled, as you asked it to stop.")
    p.fits("A block looks the same way for a timeout and for Stop: in Python Code with ctx.cancelled, as load_chunks "
           "does here. Taskloom's own blocks that wait or loop look on their own (Wait, Run Query as it reads its "
           "rows, SSH Command, Copy Files), so when they end early at the stop, it is a timeout. Python Code with “Run "
           "in my Python environment” ticked runs apart from Taskloom, which stops it as soon as its Timeout is up: "
           "a timeout, as before.")
    p.fits("A block that keeps its result looks like any success: green on the canvas, success in the Log and in Run "
           "history, and its output goes on to the blocks after it.")
    flow = write_flow(tmp_path / "timeout-grace.yaml", FLOW)
    monkeypatch.chdir(tmp_path)  # the runs the editor starts work in this folder: sales-log.csv is written here
    sales_log = tmp_path / "sales-log.csv"

    p.step("Open the flow timeout-grace.yaml (File > Open…). It has two lines, each a Manual Start and one Python Code "
           "block with a 1-second Timeout and 2 attempts: ① append_sale appends a row to sales-log.csv, a slow piece "
           "of work that takes 2 seconds and never looks whether it was told to stop; ② load_chunks loads 30 chunks, "
           "0.1 s each, and looks between chunks.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu(win, "Open…")
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    p.shot(win, "The two lines ① and ②. Nothing has run yet, so no block shows a result.",
           highlight=[LATE, STOPS], badges=True, zoom=[LATE, STOPS])

    p.step("Click append_sale. Properties, on the right, shows its code and its Retry and timeout.")
    click_block(tab, "append_sale")
    code, retry_box = properties(win)
    p.shot(win, "① append_sale's code sleeps 2 seconds, its slow work, then appends a row to sales-log.csv; nowhere "
                "does it look whether it was told to stop. ② Its Retry and timeout: fixed, 2 attempts, 1 s apart, "
                "Timeout 1s.",
           highlight=[(code, "time.sleep(2)"), retry_box], badges=True, zoom=[code, retry_box])
    spec = tab.doc.blocks["append_sale"]
    p.check("② append_sale: Timeout 1s, Retry fixed, 2 attempts, 1 s apart",
            spec["timeout"] == "1s" and spec["retry"] == {"policy": "fixed", "max_attempts": 2, "delay": "1s"})

    p.step("Right-click start_append and choose “Run the flow from here”. It runs line ①: append_sale.")
    first = run_line(p, win, qtbot, tab, LATE, 1, shoot_menu=True)
    node = tab.scene.nodes["append_sale"]
    p.shot(win, "① append_sale is green: it succeeded, and its output says sales-log.csv has “1 row(s)”. There is no "
                "“retried”: it ran once.",
           highlight=[LATE], badges=True, zoom=[LATE])
    rows = sales_log.read_text(encoding="utf-8")
    p.text("sales-log.csv after the run, as the proof reads it: one row", rows)
    p.check("① append_sale shows success, with its output “1 row(s)”, and not “retried”",
            node.status == "success" and "'1 row(s)'" in node.status_text and "retried" not in node.status_text)
    p.check("sales-log.csv holds one row: the work was not redone", len(rows.splitlines()) == 1)
    p.check("Run history records append_sale as success after 1 attempt",
            recorded(win, first, "append_sale") == ("success", 1, None))

    log = win.log.text.toPlainText()
    lines = [(win.log.text, "[append_sale] INFO "), (win.log.text, f"DONE run #{first} ")]
    took = [float(t) for t in re.findall(r"\[append_sale\] INFO done after ([\d.]+) s", log)]
    p.shot(win, "The Log, under the canvas: append_sale's attempt 1 began, and was done after about 2 seconds: past "
                "its 1-second Timeout, in the 2-second grace. The run ended with success. No attempt failed, and "
                "there was no attempt 2.",
           highlight=[lines], zoom=[lines])
    p.check("The Log shows one attempt, attempt 1, done past the 1 s Timeout, in the 2-second grace",
            log.count(": appending a row") == 1 and "attempt 1: appending a row" in log
            and len(took) == 1 and 1.0 < took[0] < 3.0)
    p.check("No attempt failed, and the run ended with success", "failed" not in log and "attempt 2" not in log
            and f"DONE run #{first} success" in log)

    p.step("Click load_chunks. Properties shows its code and the same Retry and timeout.")
    click_block(tab, "load_chunks")
    code, retry_box = properties(win)
    p.shot(win, "① Before each chunk, load_chunks looks whether it was told to stop (ctx.cancelled); if it was, it "
                "stops, and returns what it has done so far. ② The same Retry and timeout: 2 attempts, 1 s apart, "
                "Timeout 1s.",
           highlight=[(code, "ctx.cancelled"), retry_box], badges=True, zoom=[code, retry_box])

    p.step("Right-click start_load and choose “Run the flow from here”. It runs line ②: load_chunks.")
    second = run_line(p, win, qtbot, tab, STOPS, 2)
    node = tab.scene.nodes["load_chunks"]
    p.shot(win, f"② load_chunks is red: “{TIMED_OUT}”. ① append_sale still shows its success.",
           highlight=[LATE, STOPS], badges=True, zoom=[LATE, STOPS])
    p.check(f"② load_chunks shows its failure: “{TIMED_OUT}”", node.status == "failed" and TIMED_OUT in node.status_text)
    p.check("① append_sale still shows its success, from the first run",
            tab.scene.nodes["append_sale"].status == "success"
            and (tab.block_info.get("append_sale") or {}).get("run_id") == first)
    p.check("Run history records load_chunks as failed after 2 attempts, timed out after 1 s",
            recorded(win, second, "load_chunks") == ("failed", 2, TIMED_OUT))

    p.step("Drag the top edge of the Log up, to make it taller, and scroll it to where the second run starts.")
    win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)
    qtbot.waitUntil(lambda: win.output.height() >= LOG_HEIGHT - 10, timeout=5_000)
    box = win.log.text
    box.moveCursor(QTextCursor.End)
    start = box.document().find(f"run #{second} · ")
    start.clearSelection()
    box.setTextCursor(start)  # scrolled up to it: the run's first line at the top
    box.ensureCursorVisible()
    log = box.toPlainText()
    run_2 = log[log.index(f"run #{second} · "):]
    stopped = [int(n) for n in re.findall(r"stops with (\d+) of 30 chunks", run_2)]
    first_try = [(box, "attempt 1: loading"), (box, "attempt 1/2 failed")]
    retry = [(box, "waiting 1 s before attempt 2/2"), (box, "(Python Code) failed")]
    p.shot(win, "The second run in the Log. ① Attempt 1 of load_chunks saw the stop just after its 1-second Timeout, "
                "stopped with part of its chunks, and failed: timed out after 1 s. ② Taskloom waited 1 s and tried again: attempt 2 "
                "did the same, and load_chunks failed: timed out after 1 s.",
           highlight=[first_try, retry], badges=True, zoom=first_try + retry)
    p.check(f"① Attempt 1 saw the stop, stopped early with part of its work, and failed: “attempt 1/2 failed: "
            f"{TIMED_OUT}”",
            "attempt 1: loading 30 chunks" in run_2 and f"attempt 1/2 failed: {TIMED_OUT}" in run_2
            and run_2.index("saw the stop") < run_2.index("attempt 1/2 failed") and stopped and stopped[0] < 30)
    p.check("② It was retried, as before: “waiting 1 s before attempt 2/2”; attempt 2 saw the stop too, stopped "
            f"early, and the block failed: “{TIMED_OUT}”",
            "waiting 1 s before attempt 2/2" in run_2 and "attempt 2: loading 30 chunks" in run_2
            and len(stopped) == 2 and stopped[1] < 30 and f"load_chunks (Python Code) failed: {TIMED_OUT}" in run_2)
