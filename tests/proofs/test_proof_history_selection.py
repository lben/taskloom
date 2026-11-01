"""Proof: Run history keeps the run you selected, and where you scrolled to, as new runs come in (stage 4 of the
2026-10-30 feedback), with the real scheduler service running. How proofs are written: README.md here."""

import re

from PySide6.QtCore import QModelIndex, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QSpinBox

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_history_list import listed, recorded, rows, run_now
from proofs.test_proof_retention_per_start import click_block, drag_up, keep_box, keep_list
from proofs.test_proof_run_output import click as click_canvas
from proofs.test_proof_scheduler_start import click_tab, file_menu
from tests.test_lines import click_row, shown, wait_runs
from tests.test_paths_editor_running import eventually
from tests.test_scheduler_start_at import start_service, stop_service, wait_until_finished, write

ASKED = ("Not asked by you: added after v0.26.0's fix to Run history, which lists every run as it comes, "
         "scheduled runs included. Each time a run came in or ended, the list wrote all its rows again: the run you "
         "had selected lost its highlight, and the list went back to its top.")
FLOW = """\
blocks:
  by_hand:
    type: trigger.manual
    ui: {x: 0, y: 0}
  load_sales:
    type: logic.python
    config: {inputs: [], code: "output = [120, 80, 45]"}
    ui: {x: 260, y: 0}
  every_minute:
    type: trigger.schedule
    config: {every: 1 minute, keep: last 1}
    ui: {x: 0, y: 200}
  check_files:
    type: logic.wait
    config: {seconds: 5}
    ui: {x: 260, y: 200}
edges:
  - by_hand.start -> load_sales.after
  - every_minute.start -> check_files.after
"""
MANUAL_LINE = ["by_hand", "load_sales"]
SCHEDULE_LINE = ["every_minute", "check_files"]
COLUMNS = ["Run", "Started", "Started by", "Status", "Duration", "Parameters"]
RUNS = 20  # from a terminal before the flow is opened: more than Run history shows at once


def wheel(table, notches: int):
    """Turn the mouse wheel over Run history's list, `notches` notches: down, or up when negative."""
    viewport = table.viewport()
    at = QPointF(viewport.rect().center())
    QApplication.sendEvent(viewport, QWheelEvent(at, QPointF(viewport.mapToGlobal(at.toPoint())), QPoint(),
                                                 QPoint(0, -120 * notches), Qt.NoButton, Qt.NoModifier,
                                                 Qt.NoScrollPhase, False))
    QApplication.processEvents()


def row_of(table, run: str) -> int:
    (row,) = [r for r in range(table.rowCount()) if table.item(r, 0).text() == run]
    return row


def highlighted(win) -> list[str]:
    """The runs whose rows are highlighted (selected) in Run history, top to bottom; " (part of the row)" when not
    the whole row is."""
    table = win.history_panel.table
    found = sorted({index.row() for index in table.selectedIndexes()})
    return [table.item(r, 0).text() + ("" if table.selectionModel().isRowSelected(r, QModelIndex())
                                       else " (part of the row)") for r in found]


def in_view(table) -> dict[str, int]:
    """The runs whose rows show whole in Run history, top to bottom, each with how far down the list's view its row
    is, in pixels."""
    return {table.item(r, 0).text(): table.rowViewportPosition(r) for r in range(table.rowCount())
            if table.viewport().rect().contains(table.visualItemRect(table.item(r, 0)))}


def span(seen: dict[str, int]) -> str:
    return f"{next(iter(seen))} to {list(seen)[-1]}"


def view_rows(win, seen: dict[str, int]) -> list:
    """The rows of these runs, from Run to Duration, for one outline or a close-up."""
    table = win.history_panel.table
    return rows(win, [row_of(table, run) for run in seen])


def status_message(win) -> QRect:
    """The message at the left of the status bar, at the bottom of the window, in the window's pixels."""
    status = win.statusBar()
    return QRect(status.mapTo(win, QPoint(0, 0)),
                 QSize(status.fontMetrics().horizontalAdvance(status.currentMessage()) + 16, status.height()))


def test_run_history_keeps_the_run_you_selected_as_new_runs_come_in(proof, env, win, qtbot, monkeypatch):
    p = proof("Run history keeps the run you selected, and where you scrolled to, as new runs come in", ASKED)
    p.idea("As new runs come in, the run you clicked in Run history stays highlighted, and the list stays where you "
           "scrolled to: the same runs in view, in the same places.")
    p.idea("It does not matter who starts the new runs (the scheduler service, you in the editor, a terminal), nor "
           "whether they are just starting or have ended: the run you selected is found again by its number, "
           "wherever its row now is.")
    p.idea("When the list is at its very top, it stays at the top: each new run shows there, as before, and the run "
           "you selected moves down one row with the others, still selected.")
    p.idea("A run that Keep runs removes is no longer in the list. When it was the one you selected, nothing is "
           "selected: the highlight never goes to another run.")
    p.idea("One run is selected at a time: Ctrl+click and Shift+click no longer select several. What the list shows "
           "has not changed: the same columns, every run newest first.")
    p.why_simplest("A run's number never changes, and no two runs share one; its row changes each time a new run "
                   "comes in above it. So before the list writes its rows again, it notes two run numbers: the run "
                   "you selected, and the run at the top of what you see (and how much of it is cut off). After, it "
                   "selects that run again, and scrolls so that the same run is at the top again. That is all.")
    p.why_simplest("When the list is at its very top, there is no run to hold at the top: the list stays at the top, "
                   "so a new run shows there. Selecting the run again never scrolls the list to it: if you scrolled "
                   "it out of view, the list stays where you are.")
    p.why_simplest("One run, because one is all that Run history acts on: double-clicking shows one run, and Copy "
                   "executed query copies one run's query.")
    p.alternative("Remembering the selected row, by its place in the list",
                  "Each new run comes in at the top and moves every row down one, so the highlight would land on the "
                  "run that was just above yours: the wrong one. "
                  "And when Keep runs removed the run you selected, the run that took its row would be selected in "
                  "its place.")
    p.alternative("Remembering how far down the list was scrolled",
                  "New runs come in above what you see, so the same distance from the top would show other, newer "
                  "runs: the list would seem to jump one row for each new run. The run at the top of the view is "
                  "what you see, so that is what the list holds on to.")
    p.alternative("Adding only the new rows, and changing the rows of the runs that ended, instead of writing the "
                  "list again",
                  "The list would then change in three ways (new runs, runs that ended, runs Keep runs removed), each "
                  "to be kept in step with the run history. The list already wrote every row again from the run "
                  "history; it still does, and noting two run numbers around that is all that was added.")
    p.fits("Run history still reads the flow's runs at the check the editor makes every 2 seconds, and writes its "
           "rows again only when the runs have changed (a run started, ended or was removed), as since the Run "
           "history fix. Its columns and its order are the same.")
    p.fits("Double-clicking a run still shows it on the canvas (step 7), and the canvas stays on it as new runs come "
           "in. Run history's find bar (Ctrl+F) keeps its current match on the same run too, and its count right.")
    p.fits("Keep runs works as before: each start block keeps its runs as its Properties say. The list shows what "
           "is kept.")
    flow = write(env.root / "orders.yaml", FLOW)

    p.step(f"Run the flow orders.yaml {RUNS} times from a terminal (taskloom run orders.yaml), then open it (File > "
           "Open…) and save it (File > Save). It has two lines: by_hand, a Manual Start, runs load_sales; "
           "every_minute, a Schedule block set to every 1 minute, runs check_files, which waits 5 seconds. Saved, "
           "the flow is scheduled.")
    for _ in range(RUNS):
        done = env.taskloom("run", "orders.yaml")
        assert done.returncode == 0, done.stdout + done.stderr
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu(win, "Open…")
    tab = win.current
    file_menu(win, "Save")
    p.check("The flow is open and scheduled", tab.doc.path == flow.resolve() and tab.scheduled)

    p.step("Click the Run history tab, under the canvas, scroll down two notches with the mouse wheel, and click the "
           "run in the middle of what you see. (The edge above Run history is dragged up a little first, to see more "
           "rows.)")
    click_tab(win.bottom_tabs, win.history_panel)
    drag_up(win, win.output, 100)
    table, bar = win.history_panel.table, win.history_panel.table.verticalScrollBar()
    assert eventually(qtbot, lambda: len(listed(win)) == RUNS), listed(win)
    assert bar.maximum() > 0, "Run history shows every run at once: nothing to scroll"
    wheel(table, 2)
    shows = in_view(table)
    choice = list(shows)[len(shows) // 2]
    click_row(table, row_of(table, choice))
    before = in_view(table)
    p.shot(win, f"Scrolled down, runs {span(before)} in view: ① run {choice}, the one you clicked, is highlighted.",
           highlight=[rows(win, [row_of(table, choice)])], badges=True, zoom=view_rows(win, before))
    p.check(f"① Run {choice}, and only it, is selected: its whole row is highlighted", highlighted(win) == [choice])
    p.check(f"The list is scrolled down: run {RUNS}, the newest, is above what you see",
            bar.value() > 0 and str(RUNS) not in before)

    p.step("Turn the scheduler service on: here, from a command line, with taskloom scheduler --headless. It sees "
           "every_minute's schedule and starts its first run at once, which takes 5 seconds. Watch Run history while "
           "it goes. When it has ended, the Log, under the canvas, comes to the front with its messages: click the "
           "Run history tab again.")
    p.note("The Scheduler service button starts the same service, with an icon in the tray. It is started from a "
           "command line here so that the proof can stop it again once its run has ended.")
    service = start_service(env, tray=False)
    try:
        qtbot.waitUntil(lambda: any(by == "every_minute" for _, by, _ in recorded(win, flow)), timeout=60_000)
        (scheduled,) = [run for run, by, _ in recorded(win, flow) if by == "every_minute"]
        came = eventually(qtbot, lambda: listed(win)[:1] == [(scheduled, "every_minute", "running")], timeout=10_000)
        going = highlighted(win), in_view(table)
        qtbot.waitUntil(lambda: recorded(win, flow)[0] == (scheduled, "every_minute", "success"), timeout=30_000)
    finally:
        stop_service(env, service)
        wait_until_finished(env)
    qtbot.waitUntil(lambda: f"run #{scheduled} success" in win.log.text.toPlainText(), timeout=10_000)
    click_tab(win.bottom_tabs, win.history_panel)
    ended = eventually(qtbot, lambda: listed(win) == recorded(win, flow))
    after = in_view(table)
    p.shot(win, f"Run {scheduled} has come and ended, above what you see. ① Run {choice} is still highlighted, and "
                f"the list has not moved: runs {span(after)} in view, as before.",
           highlight=[rows(win, [row_of(table, choice)])], badges=True, zoom=view_rows(win, after))
    p.check(f"Run {scheduled}, the scheduler service's, came at the top of the list, started by every_minute: first "
            "running, then ended with success",
            came and ended and listed(win)[0] == (scheduled, "every_minute", "success"))
    p.check(f"While run {scheduled} was running, run {choice} stayed selected, and the list did not move",
            going == ([choice], before))
    p.check(f"① Now it has ended, run {choice} is still the one run selected", highlighted(win) == [choice])
    p.check(f"The list did not move: the same runs, {span(before)}, are in view, each in the same place",
            after == before)
    p.check("Run history lists every run of the flow, newest first, in the same columns: " + ", ".join(COLUMNS),
            listed(win) == recorded(win, flow)
            and [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())] == COLUMNS)

    p.step(f"Scroll back up to the top and click run {scheduled}, the newest. Then press ▶ Run now in the toolbar: "
           "by_hand's line runs. When it has ended, click the Run history tab again.")
    wheel(table, -10)
    click_row(table, 0)
    newest = highlighted(win), bar.value()
    run_now(win)
    yours = str(wait_runs(qtbot, tab, 1))
    click_tab(win.bottom_tabs, win.history_panel)
    eventually(qtbot, lambda: listed(win) == recorded(win, flow))
    p.shot(win, f"① Your run {yours} shows at the top, as before. ② Run {scheduled}, the one you selected, is right "
                "under it, still highlighted.",
           highlight=[rows(win, [0]), rows(win, [1])], badges=True, zoom=rows(win, range(4)))
    p.check(f"Run {scheduled}, at the very top, was the one selected before your run", newest == ([scheduled], 0))
    p.check(f"① Your run {yours} is the new row at the top, and the list is still at its very top",
            listed(win)[0] == (yours, "by_hand", "success") and bar.value() == 0)
    p.check(f"② Run {scheduled} is still the one run selected, now right under it",
            highlighted(win) == [scheduled] and row_of(table, scheduled) == 1)

    p.step("Click every_minute on the canvas. Properties, on the right, shows its Keep runs: it keeps only its last "
           "run.")
    click_block(win, "every_minute")
    box = keep_box(win)
    p.shot(win, "① every_minute keeps only its last run (The last N, Runs 1): when a new run of it ends, the one "
                f"before it goes. ② In Run history, under the canvas, run {scheduled} is still highlighted.",
           highlight=[box, rows(win, [1])], badges=True, zoom=[box])
    p.check("① every_minute keeps the last 1 run",
            (keep_list(win).currentText(), box.findChild(QSpinBox).value()) == ("The last N", 1))
    p.check(f"② Run {scheduled}, every_minute's, is still the one run selected in Run history",
            highlighted(win) == [scheduled])

    p.step("Right-click every_minute and choose “Run the flow now, as if its time had come”. When its run has ended, "
           "click the Run history tab again.")

    def run_the_schedule_line(menu):
        p.shot(menu, "The menu of every_minute, with the item to choose.", over=win,
               highlight=[menu_item(menu, "Run the flow now, as if its time had come")], zoom=[menu, "every_minute"])
        choose(menu, "Run the flow now, as if its time had come")

    p.while_open(lambda: right_click(tab, "every_minute"), run_the_schedule_line)
    last = str(wait_runs(qtbot, tab, 2))
    click_tab(win.bottom_tabs, win.history_panel)
    gone = eventually(qtbot, lambda: listed(win) == recorded(win, flow) and listed(win)[0][0] == last
                      and scheduled not in [run for run, _, _ in listed(win)], timeout=10_000)
    p.shot(win, f"① Run {last}, every_minute's new run, is at the top. Keep runs removed run {scheduled}, the one you "
                f"had selected: ② run {yours} is now in the row run {scheduled} had, and is not highlighted. No row "
                "is.",
           highlight=[rows(win, [0]), rows(win, [1])], badges=True, zoom=rows(win, range(4)))
    p.check(f"① Run {last} is the new row at the top, started by every_minute",
            listed(win)[0] == (last, "every_minute", "success"))
    p.check(f"Keep runs removed run {scheduled}: it is no longer in the run history, nor in Run history", gone)
    p.check(f"② Nothing is selected: no row is highlighted, not run {yours}, now in the row run {scheduled} had, nor "
            "any other", highlighted(win) == [] and table.item(1, 0).text() == yours)

    older = listed(win)[2][0]
    p.step(f"Click an empty spot of the canvas, then double-click run {older}, an older run of by_hand's, to show "
           "it on the canvas. (With no block selected on the canvas, the Log shows every block's lines.)")
    click_canvas(tab)
    click_row(table, 2, double=True)
    qtbot.waitUntil(lambda: not tab.following_latest and win.bottom_tabs.currentWidget() is win.log, timeout=5_000)
    log_lines = (win.log.text, f"run #{older} ")
    p.shot(win, f"The canvas shows run {older}: ① by_hand's line, with its results from that run; ② every_minute's "
                f"line shows nothing, it took no part in run {older}. Run {older} made the same results as by_hand's "
                "newer run: the next picture shows which run this is.",
           highlight=[MANUAL_LINE, SCHEDULE_LINE], badges=True, zoom=[MANUAL_LINE, SCHEDULE_LINE])
    p.shot(win, f"③ The Log, which came to the front, shows run {older}'s messages, from “run #{older} … started” to "
                f"“run #{older} success”, and only them. ④ The status bar, at the bottom of the window, says it shows "
                f"run #{older}.",
           highlight=[log_lines, status_message(win)], badges=[3, 4],
           zoom=[win.log.showing, log_lines, status_message(win)])
    p.check(f"① by_hand's line shows its results from run {older}",
            all(shown(tab, MANUAL_LINE)[b] == ("success", int(older)) for b in MANUAL_LINE))
    p.check(f"② every_minute's line shows nothing: it took no part in run {older}",
            all(shown(tab, SCHEDULE_LINE)[b] == (None, None) for b in SCHEDULE_LINE))
    p.check(f"③ The Log shows run {older}'s messages, from its start to its end, and no other run's",
            re.findall(r"run #(\d+) ", win.log.text.toPlainText()) == [older, older]
            and win.log.showing.text() == "All blocks")
    p.check(f"④ The status bar says the window shows run #{older}",
            win.statusBar().currentMessage().startswith(f"Showing run #{older} "))
    p.check(f"In Run history, run {older}, the one you double-clicked, is the one run selected",
            highlighted(win) == [older])
