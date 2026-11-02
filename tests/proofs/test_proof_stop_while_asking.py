"""Proof: Ask User's question box no longer keeps the editor waiting: Stop can be clicked while a question waits,
and closes it. See README.md here."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolBar

from proofs.test_proof_ask_user_box import answer_box, around, lines_with, write_flow
from proofs.test_proof_handled_error import log_has, open_flow, press_run_now
from taskloom.editor.runs import RunProcess
from tests.test_lines import shown, wait_runs
from tests.test_paths_editor_running import click, visible_ask

ASKED = ("Asked whether the question box should stop blocking the Stop button: the user can use the stop button and "
         "that also closes the ask user dialog, I think that makes sense, let me know if it doesn't")
FLOW = "region-report.yaml"
QUESTION = "Which region's sales?"
STOPPED = "question closed without an answer: the run was stopped"


def click_in_window(win, widget, at: QPoint | None = None):
    """A left click on `widget` (at `at` in it; else its middle) as the system delivers it: to the editor's window,
    where the mouse is. A window kept waiting by a dialog does not get it."""
    at = widget.rect().center() if at is None else at
    QTest.mouseClick(win.windowHandle(), Qt.LeftButton, Qt.NoModifier, widget.mapTo(win, at))


def test_stop_can_be_clicked_while_a_question_waits_and_closes_it(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Stop can be clicked while Ask User's question waits, and closes it", ASKED)
    p.idea("Ask User's question box no longer keeps the editor waiting. It is a window of its own, above the "
           "editor's window, and opens with the cursor in it, ready for your answer. While it waits, you can use the "
           "rest of the editor too, and click Stop.")
    p.idea("Click Stop while a question waits: the run stops, its question's box closes by itself, and the Log says "
           "why: “question closed without an answer: the run was stopped”. Nothing typed into the box is used.")
    p.idea("The box itself is unchanged: OK and Enter give your answer, and Don't answer makes the block fail, as "
           "before.")
    p.why_simplest("Everything Stop needs was already there. Stop asks the run to stop. A run that stops while its "
                   "question waits writes why in its Log and tells the editor, which closes that box without sending "
                   "anything: the way the box already closed when the block's Timeout ran out.")
    p.why_simplest("Only the box was in the way: it was opened as a dialog that keeps the editor's window waiting "
                   "until it is answered, so no click reached Stop. The one change is how the box is shown: as a "
                   "window of its own, with the keyboard in it.")
    p.alternative("Leave it as it was: Don't answer, in the box, already ends the wait",
                  "Don't answer makes the block fail, and a failure can go on, through the block's error output: it "
                  "does not stop the run. Stop does, and you asked for the Stop button.")
    p.alternative("Keep the box blocking the editor, and add a “Stop the run” button to it",
                  "A second Stop, in every question box, next to Don't answer: two buttons that end the wait in "
                  "different ways, to tell apart. The Stop button is already there, always in the same place.")
    p.alternative("Ask the question inside the editor's window, in a bar above the canvas",
                  "A new place for questions, to build for every kind of answer (text, number, choice, yes/no, "
                  "password) and for several runs asking at once. The box already does all that.")
    p.fits("Stop is the same Stop as for any run: it stops the runs of the flow on screen, and the runs you queued "
           "to start after them do not start.")
    p.fits("The box closes as it does when the block's Timeout runs out: by itself, with the reason in the Log, as a "
           "line of the block's, kept with the run in Run history.")
    p.fits("Questions are answered from any tab: a run's question box waits above the editor whichever flow is on "
           "screen.")

    sent = []  # what the editor sends to its runs (answers, Stop), as it sends it
    real_send = RunProcess._send
    monkeypatch.setattr(RunProcess, "_send", lambda run, message: (sent.append(message), real_send(run, message)))
    log = win.log.text
    flow = write_flow(tmp_path, FLOW, "ask_region", {"prompt": QUESTION, "kind": "text"})
    toolbar = win.findChild(QToolBar, "run-toolbar")
    stop = toolbar.widgetForAction(win.stop_action)
    buttons = [toolbar.widgetForAction(win.validate_action), toolbar.widgetForAction(win.run_action)]  # around Stop

    p.step(f"Open the flow {FLOW} (File > Open…). start, a Manual Start block, runs ask_region, an Ask User block of "
           "kind text that asks “Which region's sales?”. show_answer, an Inspect block, shows the answer it gets.")
    tab = open_flow(win, monkeypatch, flow)
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    p.shot(win, "① start runs ② ask_region, which asks the question; ③ show_answer shows the answer.",
           highlight=["start", "ask_region", "show_answer"], badges=True, zoom=["start", "show_answer"])

    p.step("Press ▶ Run now in the toolbar. ask_region's question box opens, with the cursor in it.")
    press_run_now(win)
    box = visible_ask(qtbot, win)
    edit = answer_box(box)
    QApplication.processEvents()  # laid out and focused, as you see it
    p.shot(box, "① The question box opens above the editor, with the cursor in its empty text box, ready for your "
                "answer.",
           over=win, highlight=[around(edit, win)], badges=True, zoom=[box])
    p.check("The box is the active window, with the cursor in its text box: what you type goes into it",
            QApplication.activeWindow() is box and box.focusWidget() is edit and edit.hasFocus())
    p.check("Nothing keeps the editor's window waiting: not the box, and no other window",
            not box.isModal() and QApplication.activeModalWidget() is None)

    p.step("Type South in the box, without clicking OK.")
    QTest.keyClicks(edit, "South")
    p.shot(box, "① Stop, in the editor's toolbar, and ② the question box, with South typed in it: both on screen. "
                "The close-up is the box.",
           over=win, highlight=[stop, around(box, win)], badges=True, zoom=[box])
    p.check("South is in the box, and the box still waits", edit.text() == "South" and box.isVisible())

    p.step("Click Stop, in the toolbar, with the mouse, while the box is open.")
    p.shot(box, "① The Stop you click, in the toolbar: it can be clicked while the box waits. The close-up is the "
                "toolbar.",
           over=win, highlight=[stop], badges=True, zoom=buttons)
    p.check("Stop can be clicked: it is not greyed out", stop.isEnabled() and win.stop_action.isEnabled())
    before = len(sent)
    click_in_window(win, stop)
    qtbot.waitUntil(lambda: not box.isVisible(), timeout=20_000)
    stopped_run = wait_runs(qtbot, tab, 1)
    QTest.qWait(300)  # what the run printed last reaches the Log
    why = [(log, STOPPED), (log, f"run #{stopped_run} cancelled")]
    p.shot(win, "The box closed by itself. The outlined lines of the Log say why: the question was closed without an "
                f"answer, because the run was stopped. Then run #{stopped_run} was cancelled.",
           highlight=[why], zoom=why)
    p.check("The click reached Stop: the editor told the run to stop, and sent it nothing else",
            sent[before:] == [{"cancel": True}])
    p.check("The box closed by itself", not box.isVisible())
    p.check("The Log says “question closed without an answer: the run was stopped”",
            log_has(win, f"[ask_region] WARNING {STOPPED}"))
    p.check(f"Run #{stopped_run} was cancelled, and South was not used: no answer was sent, and show_answer did not "
            "get it",
            tab.runs[0].status == "cancelled" and not any("value" in m for m in sent)
            and lines_with(win, "[show_answer] INFO") == 0)
    p.note("Stop stops the runs of the flow on screen, as before: if you switch to another flow's tab while a "
           "question waits, Stop there does not stop the run that is asking. Click its flow's tab first.")
    p.note("Stop's key, Shift+F5, belongs to the editor's window: pressed while the cursor is in the box, it does not "
           "reach Stop. Click Stop.")

    qtbot.waitUntil(lambda: win.statusBar().currentMessage() == "", timeout=10_000)  # its “Stopping…” is gone

    p.step("Press ▶ Run now again. While its question waits, click the Run history tab, under the canvas.")
    press_run_now(win)
    box = visible_ask(qtbot, win)
    bar = win.bottom_tabs.tabBar()
    history = win.history_panel.table
    click_in_window(win, bar, bar.tabRect(win.bottom_tabs.indexOf(win.history_panel)).center())
    # Both runs: the editor's own refresh, every 2 seconds, adds the new one, which stays as it is while it asks.
    qtbot.waitUntil(lambda: history.rowCount() == 2, timeout=10_000)
    rows = [[history.item(r, c).text() for c in range(history.columnCount())] for r in range(history.rowCount())]
    ids = [r[0] for r in rows]
    assert str(stopped_run) in ids, f"Run history does not list run #{stopped_run}: {rows}"
    row = ids.index(str(stopped_run))
    new = rows[1 - row]
    p.shot(box, f"① Run history, under the canvas: run #{stopped_run} was cancelled. Above it, the new run, "
                f"#{new[0]}, is running, while ② its question still waits above the editor. The close-up is Run "
                "history.",
           over=win, highlight=[[history.item(row, 0), history.item(row, 4)], around(box, win)], badges=True,
           zoom=[history.item(0, 0), history.item(1, 4)])  # both runs, from Run to Duration
    p.check("The click switched the panel under the canvas to Run history",
            win.bottom_tabs.currentWidget() is win.history_panel)
    p.check(f"Run history lists run #{stopped_run}, cancelled", rows[row][3] == "cancelled")
    p.check(f"Above it, the new run #{new[0]} is listed as running: its question waits",
            row == 1 and new[3] == "running")
    p.check("The question still waits: its box is open", box.isVisible())
    p.note("Closing the flow's tab while its question waits leaves the run going, as closing a tab during any run "
           "does, and the box stays: its Don't answer still ends the wait.")

    p.step("Click the Log tab, under the canvas, then click in the box, type East, and click OK.")
    click_in_window(win, bar, bar.tabRect(win.bottom_tabs.indexOf(win.log)).center())
    edit = answer_box(box)
    QTest.mouseClick(edit, Qt.LeftButton)
    QTest.keyClicks(edit, "East")
    click(box, "OK")
    answered_run = wait_runs(qtbot, tab, 2)
    QTest.qWait(300)
    east = (log, "[show_answer] INFO East")
    p.shot(win, f"The outlined line of the Log: show_answer got East, the answer typed after Run history was looked "
                f"at. Run #{answered_run} succeeded.",
           highlight=[east], zoom=[(log, f"run #{answered_run} ")])
    p.check("The box closed once answered", not box.isVisible())
    p.check(f"Run #{answered_run} succeeded: East reached show_answer",
            tab.runs[1].status == "success" and log_has(win, "[show_answer] INFO East")
            and shown(tab) == {b: ("success", answered_run) for b in tab.doc.blocks})
