"""Proof: Run history lists every run as its own row, scheduled runs included, as they come (stage 1 of the
2026-10-30 feedback), with the real scheduler service running. How proofs are written: README.md here."""

import contextlib
import datetime as dt
import os
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QToolBar

from proofs.test_proof_retention_per_start import all_shown, drag_up
from proofs.test_proof_scheduler_misfire import service_button
from proofs.test_proof_scheduler_start import click_block, click_tab, countdown, file_menu, reaches_zero
from tests.test_lines import click_row, shown, wait_runs
from tests.test_paths_editor_editing import field, type_in
from tests.test_paths_editor_running import eventually
from tests.test_scheduler_start_at import start_service, stop_service, wait_until_finished, write

ASKED = ("On the run list of the history tab I see only one entry for a scheduler block, that same item keeps "
         "refreshing itself but that isn't a historic presentation, each new run should create a new item on this "
         "list. — if a scheduled block executes multiple times it shows up only once here")
NOTE_TIME = """\
      code: |
        import datetime
        output = "ran at " + datetime.datetime.now().strftime("%H:%M:%S")
"""
FLOW = f"""\
blocks:
  by_hand:
    type: trigger.manual
    ui: {{x: 0, y: 0}}
  note_time:
    type: logic.python
    config:
      inputs: []
{NOTE_TIME}    ui: {{x: 260, y: 0}}
  check_files:
    type: logic.wait
    config: {{seconds: 0}}
    ui: {{x: 520, y: 0}}
  every_minute:
    type: trigger.schedule
    config: {{every: 1 minute}}
    ui: {{x: 0, y: 220}}
  load_sales:
    type: logic.python
    config:
      inputs: []
{NOTE_TIME}    ui: {{x: 260, y: 220}}
edges:
  - by_hand.start -> note_time.after
  - note_time.output -> check_files.value
  - every_minute.start -> load_sales.after
"""
MANUAL_LINE = ["by_hand", "note_time", "check_files"]
SCHEDULE_LINE = ["every_minute", "load_sales"]


def recorded(win, flow: Path) -> list[tuple[str, str, str]]:
    """Every run of the file `flow` in the run history (history.db), newest first, however its path was spelled:
    (run, started by, status)."""
    with contextlib.closing(sqlite3.connect(win.home.history_db)) as db:
        found = db.execute("SELECT id, flow_path, started_by, status FROM runs ORDER BY id DESC").fetchall()
    return [(str(run_id), by, status) for run_id, path, by, status in found if os.path.samefile(path, flow)]


def scheduled(win, flow: Path) -> list[str]:
    """The finished runs of `flow` that every_minute started (the scheduler service's), newest first."""
    return [run for run, by, status in recorded(win, flow) if by == "every_minute" and status != "running"]


def listed(win) -> list[tuple[str, str, str]]:
    """Run history as the editor lists it, top to bottom: (run, started by, status)."""
    table = win.history_panel.table
    return [tuple(table.item(r, c).text() for c in (0, 2, 3)) for r in range(table.rowCount())]


def whole_rows(win) -> list[list[str]]:
    """Run history as the editor lists it, every column."""
    table = win.history_panel.table
    return [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]


def lists_them_all(win, flow: Path) -> bool:
    return listed(win) == recorded(win, flow)


def rows(win, numbers) -> list:
    """These rows of Run history (those it has), from Run to Duration: one outline around them, or the part to show
    bigger. A row missing is shown missing, and the checks after the screenshot say so."""
    table = win.history_panel.table
    return [cell for r in numbers if r < table.rowCount() for cell in (table.item(r, 0), table.item(r, 4))]


def selected_runs(win) -> list[str]:
    """The runs whose rows are highlighted (selected) in Run history."""
    table = win.history_panel.table
    return sorted({table.item(item.row(), 0).text() for item in table.selectedItems()})


def started_at(win, run) -> str:
    with contextlib.closing(sqlite3.connect(win.home.history_db)) as db:
        (started,) = db.execute("SELECT started FROM runs WHERE id=?", (int(run),)).fetchone()
    return started[11:19]


def block_status(win, run, block) -> str | None:
    """How `block` ended in `run`, as the run history records it (None: it did not run)."""
    with contextlib.closing(sqlite3.connect(win.home.history_db)) as db:
        found = db.execute("SELECT status FROM blocks WHERE run_id=? AND block=?", (int(run), block)).fetchone()
    return found and found[0]


def run_now(win):
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)


def seconds_left(tab) -> float:
    """What the countdown under every_minute says is left, in seconds."""
    text = countdown(tab, "every_minute")
    return (reaches_zero(text) - dt.datetime.now()).total_seconds() if text.startswith("next run in") else 999


def test_run_history_lists_every_run_scheduled_ones_too_as_they_come(proof, env, win, qtbot, monkeypatch):
    p = proof("Run history lists every run, scheduled ones too, as they come", ASKED)
    p.idea("Run history now lists every run of the flow as its own row, newest first, each with its own run number: "
           "the runs you start and each run the scheduler service starts, as they come. A new run never takes an "
           "older run's row: the rows already there stay as they were.")
    p.idea("The editor reads the flow's runs again every 2 seconds, whatever the canvas shows: also after you "
           "double-clicked an older run to look at it, and while a run you started from the editor is going. That "
           "was the cause on your Windows PC: the list was read again only while the canvas followed the newest run "
           "and no run of yours was going (and when a run of yours ended, or you switched to the flow's tab). Once "
           "you had double-clicked a run, or while a run of yours went on, the runs the scheduler service started "
           "did not come into the list.")
    p.idea("And a flow's runs are found however its path was spelled when it was opened or run, also with other "
           "capitals: it is the same file, with the same runs. Windows already gave a file's path one spelling. On a "
           "Mac a path keeps the capitals it was typed with, and a flow opened that way listed none of its runs.")
    p.why_simplest("Run history shows what the run history records: one row for each run, newest first, whoever "
                   "started it (Started by says who). That did not change. What changed is when the editor reads "
                   "it: at the check it already makes every 2 seconds, of the flows' schedules, of the scheduler "
                   "service and of the flows running, for the flow you are looking at.")
    p.why_simplest("When the runs it reads are the ones already listed, the list is left as it is: the row you "
                   "clicked stays highlighted, and the list stays where you scrolled to. It is written again only "
                   "when the runs have changed (one has started, ended or been deleted); the row you clicked is then "
                   "no longer highlighted, but the canvas stays on the run you double-clicked (step 5).")
    p.why_simplest("Each flow file has one spelling. When the editor opens or saves a flow, and when a run starts "
                   "(the editor, the scheduler service and a terminal all start runs with taskloom run), the path is "
                   "turned into the way the file is written on disk: whole, without links or “..”, each name with "
                   "the capitals it has on disk. The run history records each run under that spelling, and the "
                   "editor asks for the flow's runs with the same one.")
    p.alternative("The scheduler service telling the editor of each run it starts",
                  "A second way for news to travel, between two programs that need not run together (the service "
                  "also runs at login, with or without the editor), and runs started from a terminal would still go "
                  "untold. Every run is already recorded in the run history, whoever starts it, and the editor "
                  "already reads it every 2 seconds.")
    p.alternative("Watching the run history's file for changes",
                  "A run writes to it many times (each block, each message), so the editor would be woken all the "
                  "time, and how files are watched differs from one system to another. Reading the flow's runs "
                  "every 2 seconds is one small question to the run history.")
    p.alternative("Comparing paths without regard to capitals",
                  "On Linux, capitals make another file: nightly-sales.yaml and NIGHTLY-SALES.YAML can be two "
                  "different flows there. Asking the system how the file itself is spelled is right on every "
                  "system.")
    p.fits("The canvas works as before: each block shows its latest result, unless you double-clicked an older run. "
           "Then the canvas stays on that run while the list goes on taking in the new runs, until you double-click "
           "the newest run or start one.")
    p.fits("While the canvas follows the newest results, each new run's messages come into the Log as before, and "
           "the Log comes to the front with them (step 3).")
    p.fits("The rows are what Run history always showed: Run, Started, Started by (the start block, or “only …” and "
           "“from …” for Run only and Run from), Status, Duration and Parameters. A run from a terminal comes in "
           "the same way (step 8).")
    flow = write(env.root / "Sales Reports" / "nightly-sales.yaml", FLOW)

    p.step("Open nightly-sales.yaml (File > Open…) and save it (File > Save). It has two lines: by_hand, a Manual "
           "Start, runs note_time and check_files; every_minute, a Schedule block set to every 1 minute, runs "
           "load_sales. note_time and load_sales each write down when they ran. Saved, the flow is scheduled.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu(win, "Open…")
    tab = win.current
    file_menu(win, "Save")
    p.check("The flow is open and scheduled", tab.doc.path == flow.resolve() and tab.scheduled)

    p.step("Press ▶ Run now, and again once it has run. Then click the Run history tab, under the canvas, and drag "
           "the edge above it up a little, to see more rows.")
    run_now(win)
    first = str(wait_runs(qtbot, tab, 1))
    first_time = tab.scene.values["note_time"]["output"]
    # The second run in a later second than the first, so that note_time tells the two apart on the canvas (step 5).
    qtbot.waitUntil(lambda: dt.datetime.now().strftime("ran at %H:%M:%S") != first_time, timeout=5_000)
    run_now(win)
    second = str(wait_runs(qtbot, tab, 2))
    second_time = tab.scene.values["note_time"]["output"]
    click_tab(win.bottom_tabs, win.history_panel)
    drag_up(win, win.output, 100)
    table = win.history_panel.table
    mine = [(second, "by_hand", "success"), (first, "by_hand", "success")]
    eventually(qtbot, lambda: listed(win) == mine)
    before = whole_rows(win)
    p.shot(win, f"Before the scheduler service runs anything: your two runs, {second} and {first}, newest first, "
                "both started by by_hand.",
           highlight=[rows(win, [0, 1])], zoom=rows(win, [0, 1]))
    p.check(f"Run history lists your two runs, {second} then {first}, started by by_hand", listed(win) == mine)
    p.check(f"Your two runs wrote down different times: note_time {first_time} in run {first}, {second_time} in run "
            f"{second}",
            first_time.startswith("ran at ") and second_time.startswith("ran at ") and first_time != second_time)

    p.step("Turn the scheduler service on: here, from a command line, with taskloom scheduler --headless. It sees "
           "every_minute's schedule and starts its first run at once, then one every minute. When that run has "
           "come, the Log, under the canvas, comes to the front with its messages: click the Run history tab again.")
    p.note("The Scheduler service button starts the same service, with an icon in the tray. It is started from a "
           "command line here so that the proof can stop it again at the end.")
    service = start_service(env, tray=False)
    try:
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=30_000)
        qtbot.waitUntil(lambda: len(scheduled(win, flow)) == 1, timeout=60_000)
        (third,) = scheduled(win, flow)
        qtbot.waitUntil(lambda: f"run #{third} success" in win.log.text.toPlainText(), timeout=10_000)
        log_to_front = win.bottom_tabs.currentWidget() is win.log
        click_tab(win.bottom_tabs, win.history_panel)
        eventually(qtbot, lambda: lists_them_all(win, flow))
        # The countdown under every_minute catches up with the service a moment after the toolbar does.
        qtbot.waitUntil(lambda: countdown(tab, "every_minute").startswith("next run in"), timeout=5_000)
        p.shot(win, f"① The scheduler service's first run, {third}, is a new row at the top, started by "
                    f"every_minute at {started_at(win, third)}. ② Your runs {second} and {first} stay as they were, "
                    "under it. ③ The scheduler service is on.",
               highlight=[rows(win, [0]), rows(win, [1, 2]), service_button(win)], badges=True,
               zoom=rows(win, [0, 1, 2]))
        p.check("The Log came to the front with the scheduled run's messages", log_to_front)
        p.check("Every run of the flow in the run history is a row of Run history, newest first, each with its own "
                f"number: {', '.join(r[0] for r in recorded(win, flow))}",
                lists_them_all(win, flow) and len(listed(win)) == 3)
        p.check(f"① Run {third}, the scheduler service's, is the new row at the top, started by every_minute",
                listed(win)[0] == (third, "every_minute", "success"))
        p.check(f"② Your runs {second} and {first} are the rows under it, as they were",
                whole_rows(win)[1:] == before)

        p.step(f"Double-click run {first}, an older run, to show it on the canvas.")
        click_row(table, next(r for r in range(table.rowCount()) if table.item(r, 0).text() == first), double=True)
        qtbot.waitUntil(lambda: not tab.following_latest, timeout=5_000)
        p.shot(win, f"① The canvas shows run {first}: note_time {first_time}, as in that run. ② every_minute's line "
                    f"shows nothing: it took no part in run {first}. The Log, under the canvas, shows run {first}'s "
                    "messages.",
               highlight=[MANUAL_LINE, SCHEDULE_LINE], badges=True, zoom=[MANUAL_LINE, SCHEDULE_LINE])
        p.check(f"① The canvas shows run {first}'s results: by_hand's line from run {first}, every_minute's line "
                "empty",
                tab.scene.values["note_time"]["output"] == first_time
                and all(shown(tab, MANUAL_LINE)[b] == ("success", int(first)) for b in MANUAL_LINE)
                and all(shown(tab, SCHEDULE_LINE)[b] == (None, None) for b in SCHEDULE_LINE))

        p.step("Click the Run history tab again, and wait for the schedule's next run, a minute after its first.")
        click_tab(win.bottom_tabs, win.history_panel)
        highlighted = selected_runs(win)
        qtbot.waitUntil(lambda: len(scheduled(win, flow)) == 2, timeout=90_000)
        fourth = scheduled(win, flow)[0]
        eventually(qtbot, lambda: lists_them_all(win, flow))
        p.shot(win, f"① The schedule's next run, {fourth}, started by every_minute at {started_at(win, fourth)}, is "
                    "a new row at the top; the rows under it are as they were.",
               highlight=[rows(win, [0])], badges=True, zoom=rows(win, range(4)))
        p.note(f"Run {first}'s row was highlighted until run {fourth} came; it no longer is. When a run comes, the "
               "list is written anew, and the highlight of the row you clicked does not stay. The canvas is what "
               "keeps showing the run you double-clicked.")
        p.check("Every run of the flow in the run history is a row, newest first: "
                f"{', '.join(r[0] for r in recorded(win, flow))}",
                lists_them_all(win, flow) and len(listed(win)) == 4)
        p.check(f"① Run {fourth} is the new row at the top, started by every_minute",
                listed(win)[0] == (fourth, "every_minute", "success"))
        p.check(f"Run {first}'s row was highlighted after the double-click, and is no longer once run {fourth} came",
                highlighted == [first] and selected_runs(win) == [])
        p.shot(win, f"② The canvas still shows run {first}, the one you double-clicked: note_time says "
                    f"“{first_time}”, as in run {first} (in run {second} it said “{second_time}”). ③ load_sales "
                    f"shows no result, though run {fourth} has just run it: every_minute's line took no part in run "
                    f"{first}.",
               highlight=[MANUAL_LINE, SCHEDULE_LINE], badges=[2, 3], zoom=[MANUAL_LINE, SCHEDULE_LINE])
        p.check(f"② The canvas still shows run {first}, the run you double-clicked",
                not tab.following_latest and tab.scene.values["note_time"]["output"] == first_time
                and all(shown(tab, MANUAL_LINE)[b] == ("success", int(first)) for b in MANUAL_LINE))
        p.check(f"③ load_sales ran in run {fourth}, and the canvas shows no result for every_minute's line",
                block_status(win, fourth, "load_sales") == "success"
                and all(shown(tab, SCHEDULE_LINE)[b] == (None, None) for b in SCHEDULE_LINE))

        p.step("Select check_files, type 30 in its Seconds and save the flow (File > Save): your next run will "
               "take 30 seconds. When the countdown under every_minute is down to about 15 seconds, press ▶ Run "
               "now, then click the Run history tab.")
        click_block(tab, "check_files")
        type_in(field(win, "Seconds"), "30")
        file_menu(win, "Save")
        qtbot.waitUntil(lambda: seconds_left(tab) <= 15, timeout=70_000)
        left = countdown(tab, "every_minute")
        run_now(win)
        qtbot.waitUntil(lambda: len(tab.runs) == 3 and tab.runs[-1].run_id is not None, timeout=30_000)
        fifth = str(tab.runs[-1].run_id)
        click_tab(win.bottom_tabs, win.history_panel)
        qtbot.waitUntil(lambda: len(scheduled(win, flow)) == 3, timeout=60_000)
        sixth = scheduled(win, flow)[0]
        came = eventually(qtbot, lambda: listed(win)[:2] == [(sixth, "every_minute", "success"),
                                                             (fifth, "by_hand", "running")], timeout=10_000)
        going, seen = tab.running, listed(win)
        p.shot(win, f"You pressed ▶ Run now with “{left}” to go. ① The schedule's run {sixth} came while yours was "
                    f"going: a new row at the top. ② Your run {fifth} is still going.",
               highlight=[rows(win, [0]), rows(win, [1])], badges=True, zoom=rows(win, range(6)))
        p.check(f"① While your run {fifth} is going, the schedule's run {sixth} is listed, at the top",
                came and going and seen[0] == (sixth, "every_minute", "success"))
        p.check(f"② Your run {fifth} is listed under it, running", seen[1] == (fifth, "by_hand", "running"))
        qtbot.waitUntil(lambda: not tab.running, timeout=60_000)
        eventually(qtbot, lambda: lists_them_all(win, flow))
        p.check("Once your run has ended, every run in the run history is a row: "
                f"{', '.join(r[0] for r in recorded(win, flow))}",
                lists_them_all(win, flow) and len(listed(win)) == 6)
    finally:
        stop_service(env, service)
        wait_until_finished(env)

    p.step("Close the flow (File > Close flow), then open it again (File > Open…) with its name typed in capitals: "
           "SALES REPORTS/NIGHTLY-SALES.YAML.")
    p.note("The scheduler service is turned off before this step, so that no new run comes while the lists are "
           "compared.")
    p.note("On Windows a flow opened with other capitals already listed its runs before this change: Windows gives a "
           "file's path the capitals it has on disk. It mattered on a Mac, where a path keeps the capitals it was "
           "typed with: there the flow opened this way listed none of its runs.")
    last = whole_rows(win)
    file_menu(win, "Close flow")
    other = flow.parent.parent / flow.parent.name.upper() / flow.name.upper()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(other), ""))
    file_menu(win, "Open…")
    tab = win.current
    click_tab(win.bottom_tabs, win.history_panel)
    eventually(qtbot, lambda: lists_them_all(win, flow))
    assert all_shown(table), "Run history does not show all its runs: drag its edge higher"
    # The editor sees the service has stopped at its next check, every 2 seconds.
    qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service off", timeout=10_000)
    qtbot.waitUntil(lambda: countdown(tab, "every_minute") == "scheduler service off", timeout=5_000)
    tab_bar = win.tabs.tabBar()
    its_tab = tab_bar.tabRect(win.tabs.indexOf(tab)).translated(tab_bar.mapTo(win, tab_bar.rect().topLeft()))
    p.shot(win, "Opened as SALES REPORTS/NIGHTLY-SALES.YAML, the flow is the same file: ① its tab says "
                "nightly-sales.yaml, as the file is written on disk.",
           highlight=[its_tab], badges=True, zoom=its_tab)
    p.check("① Opened with other capitals, the flow is the same file: its tab says nightly-sales.yaml",
            os.path.samefile(other, flow) and tab.doc.path == flow.resolve()
            and win.tabs.tabText(win.tabs.indexOf(tab)) == "nightly-sales.yaml")
    p.shot(win, "② Run history lists the same six runs, row for row.",
           highlight=[rows(win, range(6))], badges=[2], zoom=rows(win, range(6)))
    p.check("② Run history lists the same six runs as before, row for row",
            whole_rows(win) == last and lists_them_all(win, flow))

    p.step("From a terminal in the folder that holds Sales Reports, run load_sales on its own, with the flow's path "
           "typed yet another way: taskloom run \"SALES REPORTS/NIGHTLY-SALES.YAML\" --only load_sales. When the "
           "Log comes to the front with its messages, click the Run history tab again.")
    done = env.taskloom("run", Path(flow.parent.name.upper()) / flow.name.upper(), "--only", "load_sales")
    assert done.returncode == 0, done.stdout + done.stderr
    seventh = recorded(win, flow)[0][0]
    qtbot.waitUntil(lambda: f"run #{seventh} success" in win.log.text.toPlainText(), timeout=10_000)
    click_tab(win.bottom_tabs, win.history_panel)
    eventually(qtbot, lambda: lists_them_all(win, flow))
    p.text("The terminal printed", (done.stdout + done.stderr).strip())
    assert all_shown(table), "Run history does not show all its runs: drag its edge higher"
    p.shot(win, f"① Run {seventh}, from the terminal, is a new row at the top: Started by says “only load_sales”. "
                "② The runs before it are as they were.",
           highlight=[rows(win, [0]), rows(win, range(1, 7))], badges=True, zoom=rows(win, range(7)))
    p.check(f"① Run {seventh}, started from the terminal with a path typed another way, is the new row at the top",
            listed(win)[0] == (seventh, "only load_sales", "success") and lists_them_all(win, flow))
    p.check("② The six runs before it are the rows under it, as they were", whole_rows(win)[1:] == last)
