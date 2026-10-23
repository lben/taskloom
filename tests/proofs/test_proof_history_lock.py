"""Proof: the editor carries on when a run is writing a new history file just as the editor checks it (found by the
tests on Windows). How proofs are written: README.md here."""

import datetime as dt
import sqlite3
import sys
import threading
import traceback
from contextlib import closing

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

from taskloom.history import SCHEMA, History
from tests.test_editor import write_flow

FLOW = """
    blocks:
      start:
        type: trigger.manual
        ui: {x: 0, y: 0}
      export_orders:
        type: logic.python
        config: {inputs: [], code: "output = 'orders exported'"}
        ui: {x: 260, y: 0}
    edges:
      - start.start -> export_orders.after
"""
HOLD = 3.0  # seconds the run holds the new history file: longer than the editor's 2 seconds between checks


def now() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")


def test_the_editor_carries_on_while_a_run_writes_a_new_history_file(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("The editor carries on while a run writes a new history file",
              "for each feature or fix you show me screenshots of the app running on the GitHub CI doing and "
              "accomplishing correctly the feature/fix")
    p.idea("When a run is creating the history file just as the editor looks at it, the editor waits its turn and "
           "carries on, instead of showing “Something went wrong”.")
    p.idea("You did not report this one: the tests found it on Windows. The history file is where every run is "
           "recorded, and the editor looks at it every 2 seconds, to show the flows running and their results. In "
           "a Taskloom folder where nothing has run yet, the first run creates that file. If the editor looked at "
           "it at that very moment, it got “database is locked”, which the app shows as a “Something went wrong” "
           "box.")
    p.idea("Now the editor waits for the run to finish writing, as it already does whenever a run is writing to the "
           "file, and then shows the run as usual. The file ends up in its usual mode: the one that lets runs, the "
           "scheduler and the editor use it at the same time.")
    p.why_simplest("The first one to open the file puts it in its usual mode, once. That needs the file to itself, and "
                   "when someone else is using it, the answer is “busy” at once: the usual waiting for your turn "
                   "does not apply to that step. That “busy” was the error.")
    p.why_simplest("The fix is one rule in the one place where runs, the scheduler and the editor all open the file: "
                   "if the file is busy just then, leave that step to the next one who opens it, and carry on. "
                   "No one waits for that step: the opening goes on as before (waiting its turn while the run "
                   "writes), and the next one to open the file puts it in its usual mode.")
    p.alternative("Keep trying until the file is free",
                  "More to it (tries, a pause between them, a limit), only to do what the next opening of the file "
                  "does anyway.")
    p.alternative("Let the editor ignore errors in its checks every 2 seconds",
                  "It would also hide real problems you should see, such as a damaged file. And runs and the "
                  "scheduler open the file the same way as the editor, so they could still meet the same error: "
                  "the fix covers them too.")
    p.alternative("Create the history file in its usual mode before any run",
                  "There is no moment sure to come first: the editor, the scheduler or a run from a terminal can "
                  "each be the first to use a new Taskloom folder, and two of them can start together.")
    p.fits("Taskloom already has a rule for this file: when someone else is using it, wait your turn (up to 30 "
           "seconds) instead of failing with “database is locked”. The step that puts the file in its usual mode was the "
           "one place that could still fail that way: now it is left for later instead, and the rest of the opening "
           "waits its turn as before.")
    p.fits("Nothing else changes: the same file, in the same mode in the end. Only who puts it in that mode can "
           "change: the next one to open it, when the first finds it busy.")
    flow = write_flow(tmp_path / "nightly-export.yaml", FLOW)
    history_file = win.home.history_db
    # As in the packaged app: an error the editor does not expect opens a "Something went wrong" box.
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, trace: win.show_error(
        "".join(traceback.format_exception(kind, error, trace))))
    screen = win.screen().geometry()  # the window down to the screen's bottom, as a full-screen one: the flows
    win.move(0, screen.bottom() + 1 - win.frameGeometry().height())  # running list opens upwards, over the window

    p.step("Open the flow nightly-export.yaml (File > Open…), in a Taskloom folder where nothing has run yet.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    tab = win.current
    p.shot(win, "The flow is open. ① No flows running: nothing has run in this Taskloom folder yet.",
           highlight=[win.running_flows], badges=True, zoom=[win.running_flows])
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    p.check("Nothing has run yet: there is no history file", not history_file.exists())

    p.step(f"Leave the editor open while a run creates the history file and writes to it, for {HOLD:.0f} seconds.")
    p.note("A real run cannot be timed to write at the very moment the editor looks at the file, so here the proof "
           f"plays the run: it creates the history file and holds it while it writes, for {HOLD:.0f} seconds. The "
           "editor looks at the file every 2 seconds, so it looks while the file is held. Then the proof records the "
           "run's start and its end itself, as a run does; no block runs, so the canvas shows no block results.")
    p.note("A message box would wait for a click, so the proof records each box the editor opens instead of showing "
           "it, and checks that none came up.")
    done_writing = threading.Event()
    looks = []  # each time the editor opens the history file: (the run had not finished writing, it had once open)
    editor_opens = win._history

    def editor_looks():
        during = not done_writing.is_set()
        history = editor_opens()
        looks.append((during, done_writing.is_set()))
        return history

    monkeypatch.setattr(win, "_history", editor_looks)
    run = sqlite3.connect(history_file, isolation_level=None, check_same_thread=False)
    run.execute("BEGIN IMMEDIATE")  # as a run creating the file: its tables, in a write not finished yet
    for statement in filter(str.strip, SCHEMA.split(";")):
        run.execute(statement)

    def finish_writing():
        done_writing.set()  # said first: the editor cannot get to the file before the COMMIT anyway
        run.execute("COMMIT")

    finishing = threading.Timer(HOLD, finish_writing)
    finishing.start()
    try:
        qtbot.waitUntil(lambda: done_writing.is_set() and bool(looks), timeout=30_000)
    finally:
        finishing.join()
        run.close()
    p.shot(win, "Once the run is done writing, the editor carries on: no “Something went wrong” box. ① No flows "
                "running: the run has not recorded itself yet.",
           highlight=[win.running_flows], badges=True, zoom=[win.running_flows])
    p.check("No “Something went wrong” box came up, nor any other", win.message_boxes == [])
    p.check("The editor looked at the history file while the run was writing it, and got to it once the run was "
            "done", looks[0] == (True, True))

    p.step("Wait for the run to record that it is running, then click “1 flow running”, at the bottom left.")
    with closing(History(history_file)) as history:  # as a run records itself
        run_id = history.start_run("nightly-export", str(flow.resolve()), {}, now(), "start")
    qtbot.waitUntil(lambda: win.running_flows.text() == "1 flow running", timeout=30_000)
    listed = []

    def look_at_the_list(menu):
        listed.extend(a.text() for a in menu.actions())
        p.shot(menu, f"① The run of nightly-export is listed, with since when it is going (run #{run_id}).",
               over=win, highlight=[menu.actions()[0]], badges=True, zoom=[menu, win.running_flows])

    p.while_open(lambda: QTest.mouseClick(win.running_flows, Qt.LeftButton), look_at_the_list)
    p.check(f"① The flows running list shows the run of nightly-export, run #{run_id}",
            len(listed) == 1 and listed[0].startswith("nightly-export  ·  running since ")
            and listed[0].endswith(f"(run #{run_id})"))

    p.step("Wait for the run to end.")
    with closing(History(history_file)) as history:  # as a run records its end
        history({"event": "run_finished", "run_id": run_id, "status": "success", "ts": now()})
    qtbot.waitUntil(lambda: f"DONE run #{run_id} success" in win.log.text.toPlainText(), timeout=30_000)
    p.shot(win, f"① The editor shows the run in the Log, under the canvas: run #{run_id} started, and ended with "
                "success.", highlight=[(win.log.text, f"run #{run_id} ")], badges=True,
           zoom=[(win.log.text, f"run #{run_id} ")])
    log = win.log.text.toPlainText()
    p.check(f"① The Log shows that run #{run_id} started and ended with success",
            f"run #{run_id} · " in log and f"DONE run #{run_id} success" in log)

    p.step("Click the Run history tab, under the canvas.")
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier,
                     bar.tabRect(win.bottom_tabs.indexOf(win.history_panel)).center())
    table = win.history_panel.table
    rows = [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]
    run_row = [table.item(0, 0), table.item(0, 4)]
    p.shot(win, f"① Run history shows the run: run {run_id}, started by start, ended with success.",
           highlight=[run_row], badges=True, zoom=run_row)
    p.check(f"① Run history lists run {run_id}, started by start, with success",
            [(r[0], r[2], r[3]) for r in rows] == [(str(run_id), "start", "success")])
    with closing(sqlite3.connect(history_file)) as db:
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    p.check("The history file is in its usual mode, the one that lets runs, the scheduler and the editor use it at "
            "the same time", mode == "wal")
    p.check("No “Something went wrong” box came up at any point", win.message_boxes == [])
