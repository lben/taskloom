"""Proof: Ask User's "Don't answer" is a clean failure that goes out the error output. See README.md here."""

import json

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QLabel

from proofs.test_proof_handled_error import click_bottom_tab, hover, log_has, open_flow, press_run_now
from proofs.test_proof_run_output import click as click_block
from tests.test_ask_no_answer import python_internals
from tests.test_lines import shown, wait_runs
from tests.test_paths_editor_running import click, visible_ask

ASKED = ("Testing Ask User and choosing “Don't answer” gave in the Log “ERROR ask_user (Ask User) failed: "
         "RuntimeError: no answer was given”, followed by a Python traceback. You decided it should be a “clean "
         "failure that goes through the error hole”.")
FLOW = """\
blocks:
  start:
    type: trigger.manual
    ui: {x: 0, y: 60}
  ask_region:
    type: logic.ask_user
    config: {prompt: "Which region's sales?", kind: text}
    ui: {x: 260, y: 60}
  load_sales:
    type: logic.python
    config: {inputs: [region], code: "output = f'sales of the {region} region'"}
    ui: {x: 540, y: 60}
%s
edges:
  - start.start -> ask_region.after
  - ask_region.answer -> load_sales.region
%s
"""
HANDLER = """\
  show_problem:
    type: logic.inspect
    ui: {x: 540, y: 250}
"""
REPORT = "region-report.yaml"  # nothing is connected to ask_region's error output
HANDLED = "region-report-handled.yaml"  # show_problem is
BLOCKS = ["start", "ask_region", "load_sales"]
FAILED = "no answer was given"
NOBODY = "nobody can answer in this run; set a default or a secret for unattended runs"
KEYS = ['"block"', '"error"', '"traceback"', '"attempts"']  # the lines of what goes out the error output


def clean(text: str) -> bool:
    """`text` shows none of Python's internals: no RuntimeError (nor another error's name), no Traceback, no file
    paths, no line numbers."""
    return "RuntimeError" not in text and "Traceback" not in text and python_internals(text) == []


def button(box, text: str):
    (found,) = [b for b in box.findChild(QDialogButtonBox).buttons() if b.text() == text]
    return found


# `env` before `win`: its Taskloom folder is the editor's (the same temporary one), and it is made first.
def test_dont_answer_is_a_clean_failure_that_goes_out_the_error_output(proof, env, win, qtbot, monkeypatch):
    p = proof("Ask User's “Don't answer” is a clean failure that goes out the error output", ASKED)
    p.idea("Choosing “Don't answer” to an Ask User question fails the block with one short message: no answer was "
           "given. No RuntimeError, no Python traceback, no file paths: the Log, the block on the canvas and the run "
           "history all say just that.")
    p.idea("Otherwise it is a failure like any other. It goes out Ask User's red error output: a block connected "
           "there runs, gets the failure, and the run succeeds. With nothing connected there, the run fails, and the "
           "blocks that needed the answer are skipped. A retry setting on the block asks you again, as it retries "
           "any failure.")
    p.idea("Ask User's other failures that it expects are told the same way: an unattended run (taskloom run, or "
           "the scheduler service) where nobody can answer and no default or secret is set; an answer that is not "
           "one of its Options; kind choice without Options.")
    p.idea("Only the failures a block expects, like these, are told by their message alone. An unexpected error "
           "inside a block (a bug) still shows its traceback, in the Log and in what goes out the error output, so "
           "it can be found and fixed.")
    p.why_simplest("Ask User already stopped with a message that says it all: “no answer was given”. Now it marks "
                   "that failure as one it expects, where it stops. Taskloom tells such a failure by its message "
                   "alone, and every other error just as before, with its traceback.")
    p.why_simplest("Nothing else changes. The failure is recorded, retried and sent out the error output by the "
                   "same steps as any other failure: it already went out the error output before, with the "
                   "traceback in it. Now what goes out has the message alone, and an empty traceback.")
    p.alternative("Stop the run when you choose Don't answer, as the Stop button does",
                  "A stopped run runs no more blocks, so no block connected to the error output would run. You "
                  "asked for a failure that goes out the error output, and Stop is already there for stopping.")
    p.alternative("Give an empty answer, and let the run go on",
                  "The blocks that need the answer would run with nothing, and fail further on, or worse, work "
                  "with an empty region. Not answering is a failure of the question, and should say so where it "
                  "happened.")
    p.alternative("Leave out the traceback of every failure",
                  "A bug inside a block would then show only its one-line message, with nothing to tell where it "
                  "came from. The traceback is how a bug is found and fixed; only failures whose message says it "
                  "all leave it out.")
    p.alternative("Leave out the traceback of every failure of an Ask User block",
                  "A bug inside Ask User would lose its traceback too. Marking each expected failure where the block "
                  "stops for it tells exactly the failures that need no traceback, and no others.")
    p.fits("It ends as any failure does: the block is red on the canvas, with the message on its last line and in "
           "its tooltip; Run history and the flow's tab say the run failed when nothing handled it; taskloom run ends "
           "with exit code 1.")
    p.fits("What goes out the error output is what any failure sends there: the block that failed, the message, "
           "the traceback (empty here) and the number of attempts. A block made to handle other failures handles "
           "this one unchanged.")
    p.fits("taskloom run and the scheduler service, which starts scheduled runs with that same command, cannot ask "
           "anyone: Ask User then uses its Default or its Secret. With neither, it fails with the clean “nobody can "
           "answer” message, as the last step shows.")

    assert win.home.root == env.home, "the editor and the command line use the same Taskloom folder"
    env.env["PYTHONIOENCODING"] = "utf-8"  # what taskloom run prints, as a terminal shows it (on Windows too)
    (env.root / REPORT).write_text(FLOW % ("", ""), encoding="utf-8")
    (env.root / HANDLED).write_text(FLOW % (HANDLER, "  - ask_region.error -> show_problem.value"), encoding="utf-8")
    log = win.log.text

    # --- nothing connected to the error output: the run fails, cleanly --------------------------------------------
    p.step(f"Open the flow {REPORT} (File > Open…). start runs ask_region, an Ask User block of kind text that "
           "asks “Which region's sales?”. load_sales needs its answer. Nothing is connected to ask_region's red "
           "error output.")
    tab = open_flow(win, monkeypatch, env.root / REPORT)
    p.check("The flow opens: ask_region's answer goes into load_sales, and nothing takes its error output",
            tab is not None and [(s, o, to) for s, o, to, _ in tab.doc.edges() if s == "ask_region"]
            == [("ask_region", "answer", "load_sales")])
    p.shot(win, "① ask_region, which asks the question. ② load_sales, which needs its answer.",
           highlight=["ask_region", "load_sales"], badges=True, zoom=BLOCKS)

    p.step("Press ▶ Run now in the toolbar. ask_region's question box opens: click Don't answer.")
    press_run_now(win)
    box = visible_ask(qtbot, win)
    question = box.findChild(QLabel).text()
    p.shot(box, "The question box ask_region opens while the flow runs: its question, a box to type the answer in, "
                "and ① Don't answer, which you click.",
           over=win, highlight=[button(box, "Don't answer")], badges=True, zoom=[box])
    p.check("The question box asks “Which region's sales?”, with a Don't answer button",
            (box.windowTitle(), question) == ("ask_region asks", "Which region's sales?")
            and button(box, "Don't answer").isVisible())
    click(box, "Don't answer")
    first = wait_runs(qtbot, tab, 1)
    QTest.qWait(300)  # what the run printed last reaches the Log
    p.shot(win, "① ask_region failed: it is red, and its last line says why: no answer was given. ② load_sales was "
                "skipped: the answer it needs never came.",
           highlight=["ask_region", "load_sales"], badges=True, zoom=BLOCKS)
    p.check(f"① ask_region failed and ② load_sales was skipped, in run #{first}",
            shown(tab) == {"start": ("success", first), "ask_region": ("failed", first),
                           "load_sales": ("skipped", first)})
    p.check("On ask_region's last line, the canvas says “✕ no answer was given”",
            tab.scene.nodes["ask_region"].status_text == f"✕ {FAILED}")

    lines = [(log, f"ask_region (Ask User) failed: {FAILED}"), (log, f"run #{first} failed")]
    p.shot(win, "The outlined lines of the Log, under the canvas. First, ask_region failed: no answer was given. "
                "Below it, the input it got from start, as for any failure. No RuntimeError, no traceback, no file "
                f"paths. Last, run #{first} failed: nothing handled ask_region's failure.",
           highlight=lines, zoom=lines)
    p.check("The Log says “ask_region (Ask User) failed: no answer was given”",
            log_has(win, f"[ask_region] ERROR ask_region (Ask User) failed: {FAILED}"))
    p.check("The Log shows no RuntimeError, no Traceback, no file paths and no line numbers",
            clean(log.toPlainText()))
    p.check(f"The run failed: the Log says run #{first} failed",
            tab.runs[0].status == "failed" and log_has(win, f"run #{first} failed"))
    p.note("Before this change, the first line read “… failed: RuntimeError: no answer was given”, and a Python "
           "traceback followed it, with Taskloom's file paths and line numbers: what you reported. (This proof does "
           "not run the old version.) An unexpected error inside a block, a bug, still shows its traceback there.")

    p.step("Rest the mouse on ask_region's last line.")
    tip = hover(tab, tab.scene.nodes["ask_region"].footer, qtbot)
    said = tip.text()
    p.shot(tip, "Its tooltip gives the failure in full: no answer was given, and nothing more.", over=win,
           highlight=[tip], zoom=[tip, "ask_region"])
    tip.hide()
    p.check("The tooltip says “no answer was given”, and nothing more", said == f"<pre>{FAILED}</pre>")

    p.step("Click the Run history tab, under the canvas.")
    click_bottom_tab(win, win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 1, timeout=10_000)
    row = [table.item(0, 0), table.item(0, 4)]
    p.shot(win, f"Run history lists run #{first}, started by start, as failed.", highlight=[row], zoom=row)
    p.check(f"Run history lists run #{first}, started by start, as failed",
            [table.item(0, c).text() for c in (0, 2, 3)] == [str(first), "start", "failed"])

    p.step(f"Double-click run #{first}. The canvas and the Log show it as the run history keeps it.")
    at = table.visualItemRect(table.item(0, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # (QTest's is only the second click)
    qtbot.waitUntil(lambda: win.bottom_tabs.currentWidget() is win.log, timeout=10_000)
    QTest.qWait(200)
    status = win.statusBar()
    showing = QRect(status.mapTo(win, QPoint(0, 0)),
                    QSize(status.fontMetrics().horizontalAdvance(status.currentMessage()) + 16, status.height()))
    kept = (log, f"ask_region (Ask User) failed: {FAILED}")
    p.shot(win, f"① The status bar says the window shows run #{first}. ② The Log shows its messages as the run "
                "history kept them: ask_region failed: no answer was given, without a traceback there too. The "
                "canvas shows ask_region's failure from the run history as well.",
           highlight=[showing, kept], badges=True, zoom=[showing, kept, (log, f"run #{first} failed")])
    p.check(f"① The status bar says it shows run #{first}",
            status.currentMessage().startswith(f"Showing run #{first} "))
    p.check(f"The run history keeps ask_region's failure in run #{first} as “no answer was given”",
            (tab.block_info["ask_region"].get("run_id"), tab.block_info["ask_region"].get("error")) == (first, FAILED))
    p.check("② Its Log in the run history says ask_region failed: no answer was given, without RuntimeError, "
            "Traceback, file paths or line numbers",
            log_has(win, f"[ask_region] ERROR ask_region (Ask User) failed: {FAILED}") and clean(log.toPlainText()))

    # --- a block connected to the error output: it gets the failure, and the run succeeds --------------------------
    p.step(f"Open {HANDLED} (File > Open…). It is the same flow, with one more block: show_problem, an Inspect "
           "block, takes what comes out of ask_region's red error output.")
    handled = open_flow(win, monkeypatch, env.root / HANDLED)
    p.check("show_problem takes ask_region's error output",
            [(s, o) for s, o, to, _ in handled.doc.edges() if to == "show_problem"] == [("ask_region", "error")])
    p.shot(win, "① ask_region's red, dashed error output goes into ② show_problem. An Inspect block shows what "
                "reaches it, in the Log and in Data preview.",
           highlight=["ask_region", "show_problem"], badges=True, zoom=[*BLOCKS, "show_problem"])

    p.step("Press ▶ Run now, and click Don't answer in the question box again.")
    press_run_now(win)
    click(visible_ask(qtbot, win), "Don't answer")
    second = wait_runs(qtbot, handled, 1)
    QTest.qWait(300)
    p.shot(win, "① ask_region failed, as before: no answer was given. ② show_problem ran: the failure went out "
                "ask_region's error output and reached it. load_sales was skipped again: no answer came.",
           highlight=["ask_region", "show_problem"], badges=True, zoom=[*BLOCKS, "show_problem"])
    p.check(f"① ask_region failed, ② show_problem ran, and load_sales was skipped, in run #{second}",
            shown(handled) == {"start": ("success", second), "ask_region": ("failed", second),
                               "load_sales": ("skipped", second), "show_problem": ("success", second)})
    got = [(log, line) for line in ("[show_problem]", *KEYS, "}")]  # one outline around them all
    lines = [(log, f"ask_region (Ask User) failed: {FAILED}"), got, (log, f"run #{second} success")]
    p.shot(win, "The outlined lines of the Log. First, ask_region failed: no answer was given, as cleanly as before. "
                f"Then show_problem shows what it received. Last, run #{second} ended with success: ask_region's "
                "failure was handled.",
           highlight=lines, zoom=lines)
    p.check(f"Run #{second} ended with success: show_problem handled ask_region's failure",
            handled.runs[0].status == "success" and log_has(win, f"run #{second} success")
            and env.last_run() == (second, "success"))
    p.check("The Log shows no RuntimeError, no Traceback, no file paths and no line numbers",
            clean(log.toPlainText()))

    p.step("Click show_problem on the canvas, then the Data preview tab, under the canvas.")
    click_block(handled, "show_problem")
    click_bottom_tab(win, win.preview)
    text = win.preview.text
    received = json.loads(text.toPlainText())
    value = [(text, line) for line in ("{", *KEYS, "}")]
    p.shot(win, "Data preview shows what show_problem received from ask_region's error output: the block that "
                "failed, ask_region; the message, no answer was given; the traceback, empty, as there is none to "
                "tell; and the attempts, 1.",
           highlight=[value], zoom=[win.preview.label, *value])
    p.check("Data preview shows show_problem's value", win.preview.label.text() == "show_problem.value:")
    p.check("It names the block that failed: ask_region", received.get("block") == "ask_region")
    p.check("Its message is “no answer was given”", received.get("error") == FAILED)
    p.check("Its traceback is empty", received.get("traceback") == "")
    p.check("That is all it received, with the attempts: 1",
            received == {"block": "ask_region", "error": FAILED, "traceback": "", "attempts": 1})

    # --- an unattended run: nobody can answer ------------------------------------------------------------------
    p.step(f"In a terminal, in the folder of the flows, run the first flow: taskloom run {REPORT}. Nobody is there "
           "to answer, as in the runs the scheduler service starts, and ask_region has no Default and no Secret.")
    result = env.taskloom("run", REPORT, encoding="utf-8", errors="replace")
    printed = (result.stdout + result.stderr).strip()  # (a run's lines go to stderr)
    third, status = env.last_run()
    p.text(f"taskloom run {REPORT} printed", printed)
    p.check(f"It says ask_region failed: {NOBODY}", f"[ask_region] failed · {NOBODY}" in printed)
    p.check("It shows no RuntimeError, no Traceback, no file paths and no line numbers", clean(printed))
    p.check(f"load_sales was skipped, and the run failed: “run #{third} failed”, exit code 1, and failed in the run "
            "history",
            "[load_sales] skipped · something before it failed" in printed
            and printed.endswith(f"run #{third} failed") and (result.returncode, status) == (1, "failed"))
    p.note("This is shown as the command's output: it is what a terminal shows when you run it. With a Default or a "
           "Secret set on ask_region, the same run would use it, and go on.")
