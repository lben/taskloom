"""Proof: runs are kept for a time you choose (5 years by default), not only the last 20 (batch 1)."""

import datetime as dt

from PySide6.QtCore import QLocale, QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QFileDialog, QFormLayout, QLabel, QPushButton, QSpinBox, QToolBar, QWidget

from taskloom.history import connect
from tests.test_editor import write_flow

FLOW = """
    blocks:
      start_by_hand:
        type: trigger.manual
        ui: {x: 0, y: 0}
      daily_orders:
        type: logic.python
        config:
          inputs: []
          code: "output = pl.DataFrame({'region': ['North', 'South', 'East', 'West'], 'orders': [412, 380, 295, 331]})"
        ui: {x: 260, y: 0}
    edges:
      - start_by_hand.start -> daily_orders.after
"""
RUNS = 21  # one more than Taskloom used to keep
OLDEST = {1: 45, 2: 20}  # runs set back this many days, for the second part


def set_back(home, run_id: int, days: int):
    """Make a run one that started `days` earlier than it did (and ended as much earlier: its duration stays)."""
    db = connect(home.history_db)
    try:
        started, finished = db.execute("SELECT started, finished FROM runs WHERE id=?", (run_id,)).fetchone()
        earlier = [(dt.datetime.fromisoformat(t) - dt.timedelta(days=days)).isoformat(timespec="milliseconds")
                   for t in (started, finished)]
        db.execute("UPDATE runs SET started=?, finished=? WHERE id=?", (*earlier, run_id))
    finally:
        db.close()


def run_folders(home) -> list[int]:
    """The runs that still have their folder of files."""
    return sorted(int(p.name) for p in home.runs_dir.iterdir() if p.is_dir() and p.name.isdigit())


def disk_space(home) -> str:
    """What the files of the runs add up to, written as Settings writes a size."""
    total = sum(p.stat().st_size for p in home.runs_dir.rglob("*") if p.is_file())
    return QLocale.c().formattedDataSize(total, 1, QLocale.DataSizeTraditionalFormat)


def keep_runs_row(dialog) -> QWidget:
    """The row of the Settings form labelled “Keep runs for”: its number, its unit and the text after them."""
    form = dialog.findChild(QFormLayout)
    for row in range(form.rowCount()):
        label = form.itemAt(row, QFormLayout.LabelRole)
        if label and isinstance(label.widget(), QLabel) and label.widget().text() == "Keep runs for":
            return form.itemAt(row, QFormLayout.FieldRole).widget()
    raise AssertionError("Settings has no row “Keep runs for”")


def click_tab(win, panel):
    """Click the tab of a panel under the canvas (Log, Data preview, Run history)."""
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.bottom_tabs.indexOf(panel)).center())


def history_rows(win) -> list[list[str]]:
    table = win.history_panel.table
    return [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]


def test_runs_are_kept_for_a_time_you_choose(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Runs are kept for a time you choose (5 years by default), not only the last 20",
              "Do not delete runs after a 20 count, lets remove only old runs that are older than (configurable, "
              "default 5 years)")
    p.idea("Taskloom keeps every run for a time you choose, 5 years unless you say otherwise, however many runs "
           "there are. Only the runs older than that are deleted.")
    p.idea("Tools > Settings has a line for it, Keep runs for: a number, and days, months or years. Next to it is "
           "the disk space your runs take now, so you can see what keeping them costs.")
    p.idea("Each time a run ends, Taskloom deletes the runs that started longer ago than that: all of each one, its "
           "line in Run history, its messages and the files it made. Every newer run stays. A run still going is "
           "never deleted, however long ago it started.")
    p.idea("This is one setting for all of Taskloom, and it is what this proof shows. Choosing it for each start "
           "block (keep all runs, the last few, for a time, or always keep the failed ones) comes in the next batch, "
           "as you asked after this one was built.")
    p.why_simplest("One rule, by age, in one place: as a run ends, it deletes the finished runs that started before "
                   "the cut-off. There is no clean-up job to keep running or to schedule: runs end wherever runs "
                   "happen (the editor, the scheduler service, the command line), and the cleaning happens there too.")
    p.why_simplest("It replaces the two rules there were before, keep the files of the newest 20 runs and keep the "
                   "record of each run for 365 days, with this one. A run's record and its files now go together: "
                   "Run history no longer lists runs whose tables were deleted long ago.")
    p.alternative("Keep a count, only a bigger one (say the newest 1,000 runs)",
                  "A count deletes by how busy Taskloom is, not by how old a run is: the count was for all your "
                  "flows together, so a flow that runs every 5 minutes would push a monthly flow's runs out within "
                  "days. You asked for an age.")
    p.alternative("A separate clean-up, for example once a day in the scheduler service",
                  "It would clean only while the service runs, so on a computer where you run flows only from the "
                  "editor or the command line nothing would ever be deleted. It is one more thing that can stop "
                  "without you noticing. Cleaning as each run ends needs nothing extra.")
    p.alternative("Delete the files by age, and keep the records for ever (or for another time)",
                  "Two settings to understand, and a Run history that fills with runs whose tables are gone: the "
                  "mismatch the old pair of rules had.")
    p.fits("Keep runs for sits in Tools > Settings with the other settings for all of Taskloom, and is saved in the "
           "same settings file. While you keep the default, nothing is written there: 5 years applies.")
    p.fits("Run history, “Results of this block…”, the canvas and the Data preview read the same records and files: "
           "a deleted run is gone from all of them at once, and a kept one shows everywhere, tables included.")
    p.fits("Running a block again on an earlier run's results gives the new run its own copy of those tables: "
           "deleting the earlier run never breaks it.")
    p.fits("A value typed by hand in the settings file that cannot be read (say “forever”) is refused before a run "
           "starts, like any other mistake there.")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # Settings' Save writes a script there on Windows
    flow = write_flow(tmp_path / "daily-orders.yaml", FLOW)
    home = win.home

    p.step("Open the flow daily-orders.yaml (File > Open…). Its Manual Start runs daily_orders, which makes a small "
           "table of orders by region; each run keeps that table in its files.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())

    p.step(f"Press ▶ Run now {RUNS} times, one run more than Taskloom used to keep. Then click the Run history tab, "
           "under the canvas.")
    run_now = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action)
    for n in range(1, RUNS + 1):
        if n == RUNS:
            for run_id, days in OLDEST.items():
                set_back(home, run_id, days)
        QTest.mouseClick(run_now, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(tab.runs) == n and not tab.running and tab.runs[-1].status is not None,
                        timeout=60_000)
    p.check(f"All {RUNS} runs succeeded", [r.status for r in tab.runs] == ["success"] * RUNS)
    p.note(f"Taskloom keeps runs by when they started, and a proof cannot wait 45 days. So before the last of the "
           f"{RUNS} runs, runs 1 and 2 were made to have started 45 and 20 days earlier, by changing their start "
           "time in the run history (how long they took stays the same). Nothing else is done behind the app's back.")
    click_tab(win, win.history_panel)
    table = win.history_panel.table
    rows = history_rows(win)
    last = table.rowCount() - 1
    p.shot(win, f"① Run {RUNS}, the newest, is at the top of Run history, and the older runs follow below it.",
           highlight=[[table.item(0, 0), table.item(0, 4)]], badges=True, zoom=[table.item(0, 0), table.item(4, 4)])

    p.step("Scroll to the bottom of Run history.")
    table.scrollToBottom()
    p.shot(win, "At the bottom, the two oldest runs: ① run 2 started 20 days ago, and run 1 45 days ago. With Keep "
                "runs for at its default, 5 years, both stay, with their files.",
           highlight=[[table.item(last - 1, 0), table.item(last, 4)]], badges=True,
           zoom=[table.item(last - 4, 0), table.item(last, 4)])
    p.check(f"Run history lists all {RUNS} runs, from {RUNS} down to 1",
            [r[0] for r in rows] == [str(i) for i in range(RUNS, 0, -1)])
    ago = {int(r[0]): (dt.datetime.now() - dt.datetime.fromisoformat(r[1])).days for r in rows}
    p.check("① Run 1 started 45 days ago, and run 2 20 days ago", (ago[1], ago[2]) == (45, 20))
    p.check(f"Each of the {RUNS} runs still has its files, run 1 too (before, only the newest 20 runs kept theirs)",
            run_folders(home) == list(range(1, RUNS + 1)))

    p.step("Double-click run 1. Then click daily_orders on the canvas, and the Data preview tab.")
    run_1 = table.visualItemRect(table.item(last, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, run_1)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, run_1)  # (QTest's is only the second click)
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier,
                     tab.view.mapFromScene(tab.scene.nodes["daily_orders"].sceneBoundingRect().center()))
    click_tab(win, win.preview)
    preview, status = win.preview, win.statusBar()
    message = QRect(status.mapTo(win, QPoint(0, 0)),
                    QSize(status.fontMetrics().horizontalAdvance(status.currentMessage()) + 16, status.height()))
    table_shown = [preview.label, *((preview.view, r) for r in range(4))]
    p.shot(win, "① The status bar says the canvas shows run 1, from 45 days ago. ② The Data preview shows the "
                "table daily_orders made in that run: it is read from run 1's own files, which are still there.",
           highlight=[message, table_shown], badges=True, zoom=[message, *table_shown])
    p.check("① The canvas shows run 1, and the status bar says so",
            tab.result_run("daily_orders") == 1 and status.currentMessage().startswith("Showing run #1 "))
    model = preview.view.model()
    cells = [[str(model.index(r, c).data()) for c in range(model.columnCount())] for r in range(model.rowCount())] \
        if model is not None else []
    p.check("② The Data preview shows run 1's table: 4 regions, North with 412 orders",
            len(cells) == 4 and cells[0] == ["North", "412"])

    p.step("Choose Settings… in the Tools menu.")
    settings = next(a for a in next(a.menu() for a in win.menuBar().actions() if a.text() == "&Tools").actions()
                    if a.text() == "Settings…")
    space = disk_space(home)

    def look_set_and_save(dialog):
        row = keep_runs_row(dialog)
        number, unit = row.findChild(QSpinBox), row.findChild(QComboBox)
        said = " ".join(w.text() for w in row.findChildren(QLabel))
        p.note("Keep runs for, here, is for all of Taskloom. Choosing it for each start block comes in the next "
               "batch.")
        p.shot(dialog, f"① Keep runs for: 5 years, the default. Next to it, the disk space the {RUNS} runs take "
                       f"now: {space}.", highlight=[row], badges=True)
        p.check("① Settings showed Keep runs for 5 years", (number.value(), unit.currentText()) == (5, "years"))
        p.check(f"① And the disk space the runs take: {space}, what their files add up to",
                said == f"the runs take {space} now")
        number.setValue(30)
        unit.setCurrentText("days")
        p.step("Set Keep runs for to 30 days, and press Save.")
        p.shot(dialog, "① Keep runs for: 30 days, about to be saved.", highlight=[row], badges=True)
        next(b for b in dialog.findChildren(QPushButton) if b.text().replace("&", "") == "Save").click()

    p.while_open(settings.trigger, look_set_and_save)
    p.check("Saved: Taskloom now keeps runs for 30 days", home.settings()["keep_runs_for"] == "30 days")

    p.step("Press ▶ Run now once more, for run 22. Then click the Run history tab, and scroll to its bottom.")
    QTest.mouseClick(run_now, Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == RUNS + 1 and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)
    click_tab(win, win.history_panel)
    table.scrollToBottom()
    qtbot.waitUntil(lambda: not status.currentMessage(), timeout=10_000)  # “Showing run #1” goes after 5 s
    rows = history_rows(win)
    last = table.rowCount() - 1
    p.shot(win, "As run 22 ended, Taskloom deleted run 1, which started 45 days ago: ① run 2, from 20 days ago, is "
                "now the oldest, and is kept with every run after it.",
           highlight=[[table.item(last, 0), table.item(last, 4)]], badges=True,
           zoom=[table.item(last - 4, 0), table.item(last, 4)])
    p.check("Run history lists runs 22 down to 2: run 1 is gone, every newer run stays",
            [r[0] for r in rows] == [str(i) for i in range(RUNS + 1, 1, -1)])
    p.check("Run 1's files are deleted with it; runs 2 to 22 keep theirs",
            run_folders(home) == list(range(2, RUNS + 2)))
