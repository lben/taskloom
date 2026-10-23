"""Proofs (batch 1): Run now asks which Manual Start to run; a + by the last tab opens a new flow; a scheduled flow
with unsaved changes says the scheduler runs its saved version; the flows running list names a flow by its file.
How proofs are written: README.md here."""

import contextlib
import os
import signal
import subprocess
import sys

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QToolBar, QToolTip

from taskloom.editor import dialogs
from taskloom.editor.canvas import HEADER_H, NODE_W
from taskloom.editor.safety import pid_alive
from taskloom.flow import load_flow
from tests.test_editor import add_from_palette, drag_port_to_port, write_flow
from tests.test_paths_editor_editing import field, select_only, type_in
from tests.test_paths_editor_running import REAL_CONFIRM_RUN_NOW

TWO_STARTS = """
    blocks:
      start:
        type: trigger.manual
        ui: {x: 0, y: 0}
      read_sales:
        type: logic.python
        config: {inputs: [], code: "output = [120, 80, 45]"}
        ui: {x: 260, y: 0}
      total_sales:
        type: logic.python
        config: {inputs: [input], code: "output = sum(input)"}
        ui: {x: 520, y: 0}
      rebuild_report:
        type: trigger.manual
        ui: {x: 0, y: 200}
      build_report:
        type: logic.python
        config: {inputs: [], code: "output = 'report built'"}
        ui: {x: 260, y: 200}
    edges:
      - start.start -> read_sales.after
      - read_sales.output -> total_sales.input
      - rebuild_report.start -> build_report.after
"""
FIRST_LINE = ["start", "read_sales", "total_sales"]
SECOND_LINE = ["rebuild_report", "build_report"]
LINES = [FIRST_LINE, SECOND_LINE]  # highlighted as ① and ②

NIGHTLY = """
    blocks:
      nightly:
        type: trigger.schedule
        config: {cron: "0 2 * * *"}
        ui: {x: 0, y: 0}
      export_orders:
        type: logic.python
        config: {inputs: [], code: "output = 'orders exported'"}
        ui: {x: 260, y: 0}
    edges:
      - nightly.start -> export_orders.after
"""
UNSAVED = "unsaved · the scheduler runs the saved version"


# --- helpers -------------------------------------------------------------------------------------------------------

def shows(tab, block) -> tuple:
    """What the canvas shows of a block: its status, the run its result is from, what its output held."""
    return (tab.scene.nodes[block].status, (tab.block_info.get(block) or {}).get("run_id"),
            tab.scene.values.get(block, {}).get("output", ""))


def file_menu(win, text: str):
    """An item of the File menu (to trigger it, as choosing it does)."""
    menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    return next(a for a in menu.actions() if a.text() == text)


def run_now_button(win):
    return win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action)


def tab_rect(win, index: int) -> QRect:
    """A tab of the flows' tabs, in the window's pixels."""
    bar = win.tabs.tabBar()
    rect = bar.tabRect(index)
    return QRect(bar.mapTo(win, rect.topLeft()), rect.size())


def plus_beside_last_tab(win) -> bool:
    """The + shows just right of the last tab, on the tabs' row, and takes the click there."""
    QApplication.processEvents()
    plus = win.tabs.tabBar().new_button
    last, place = tab_rect(win, win.tabs.count() - 1), QRect(plus.mapTo(win, QPoint(0, 0)), plus.size())
    hit = win.childAt(place.center())
    return (plus.isVisible() and 0 <= place.left() - last.right() <= 30
            and last.top() <= place.center().y() <= last.bottom() and (hit is plus or plus.isAncestorOf(hit)))


def drag_block(tab, block: str, dx: int, dy: int):
    """Drag a block by its title, as you would with the mouse."""
    viewport = tab.view.viewport()
    rect = tab.scene.nodes[block].sceneBoundingRect()
    a = tab.view.mapFromScene(rect.left() + 4 + NODE_W / 2, rect.top() + 4 + HEADER_H / 2)
    b = a + QPoint(dx, dy)
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(viewport, (a + b) / 2)
    QTest.mouseMove(viewport, b)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, b)


def under(tab, block) -> str:
    """The line under a Schedule block: when it starts the flow next."""
    return tab.scene.nodes[block].countdown


def hover_line_under(tab, block):
    """Hover the line under a Schedule block, as you would with the mouse: its tooltip, once it shows."""
    node, viewport = tab.scene.nodes[block], tab.view.viewport()
    at = tab.view.mapFromScene(node.countdown_area.sceneBoundingRect().center())
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, at, viewport.mapToGlobal(at)))
    QApplication.processEvents()
    return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)


@contextlib.contextmanager
def scheduler_service(home):
    """The real scheduler service for this Taskloom folder, started as from a terminal; stopped at the end as the
    editor turns it off: the process in scheduler.pid (on Windows a venv's python.exe only launches that one)."""
    proc = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"],
                            env={**os.environ, "TASKLOOM_HOME": str(home.root)},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            try:
                pid = int((home.root / "scheduler.pid").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pid = None
            if pid and pid != os.getpid() and pid_alive(pid):
                os.kill(pid, signal.SIGTERM)
            else:
                proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)


# --- 1. Run now asks which Manual Start ----------------------------------------------------------------------------

def test_run_now_asks_which_manual_start_and_runs_only_its_line(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Run now asks which Manual Start to run",
              "A second manual start block made the other/initial manual start block run when that wasn't the "
              "intended behavior, if I start a manual block only the blocks that depend on that block should run")
    p.idea("With several Manual Start blocks, Run now asks which one to run, and runs only the blocks that follow "
           "it.")
    p.idea("The question has a button for each Manual Start, and All of them. The one you click runs its own line, "
           "the blocks connected after it, and nothing else, just as right-clicking it and choosing “Run the flow "
           "from here” does. All of them runs from every Manual Start at once, as Run now did before.")
    p.idea("Next time in the same flow, the one you chose last is offered first. The question shows even if you "
           "ticked “Don't ask me again”, because which one to run is a real choice. A flow with one Manual Start is "
           "asked as before.")
    p.why_simplest("Two things already existed: Run now's question (“Run now?”), and running a flow from one start "
                   "block, which is what “Run the flow from here” does. The question only gains a button for each "
                   "Manual Start, and Run now starts the run from the one you click. No new way of running a flow was "
                   "needed.")
    p.why_simplest("The choice shows only when there is one to make: a flow with one Manual Start looks just as "
                   "before.")
    p.alternative("Keep running all of them, and right-click a start to run only its line",
                  "That is what Run now did, and what surprised you: Run now (F5) ran every line. Its question "
                  "mentioned the right-click only in passing, and did not show at all once “Don't ask me again” was "
                  "ticked.")
    p.alternative("Run now runs only the first Manual Start",
                  "Which one is first cannot be seen on the canvas, and running the other one would still need the "
                  "right-click. A rule you cannot see is the problem you hit.")
    p.alternative("Run the Manual Start selected on the canvas",
                  "What runs would depend on what you clicked last: easy to get wrong without noticing. The question "
                  "shows the choice each time, and needs nothing selected.")
    p.alternative("A list beside Run now in the toolbar, to choose the start",
                  "It would always take room, also in the many flows with one Manual Start, where there is nothing "
                  "to choose.")
    p.fits("It is the rule of “Each start block runs its own line”: a run started from one Manual Start takes part "
           "only in that start's line, and the other lines keep the results they show. Run history's Started by "
           "names the block you chose.")
    p.fits("The choice is remembered for each flow, the way the editor remembers which of a flow's runs you have "
           "seen.")
    monkeypatch.setattr(dialogs, "confirm_run_now", REAL_CONFIRM_RUN_NOW)  # the real question, answered as you would
    flow = write_flow(tmp_path / "two-starts.yaml", TWO_STARTS)

    p.step("Open the flow two-starts.yaml (File > Open…). It has two Manual Start blocks: start, the one every new "
           "flow comes with, runs line ① (read_sales, then total_sales); rebuild_report, added later, runs line ② "
           "(build_report).")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu(win, "Open…").trigger()
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    p.shot(win, "Line ① starts at start, line ② at rebuild_report. Nothing has run yet, so no block shows a result.",
           highlight=LINES, badges=True, zoom=LINES)

    p.step("Press ▶ Run now in the toolbar. Taskloom asks which Manual Start to run.")
    asked = []

    def look_at_the_question(box, then: str):
        buttons = {b.text().replace("&", ""): b for b in box.buttons()}
        asked.append((sorted(buttons), box.defaultButton().text().replace("&", "")))
        if then == "rebuild_report":
            p.shot(box, "A button for each Manual Start, start and rebuild_report, and All of them; or Cancel. "
                        "Nothing was chosen in this flow before, so All of them is offered first (Enter chooses it).",
                   highlight=[[buttons["start"], buttons["rebuild_report"], buttons["All of them"]]])
            p.step("Click rebuild_report. Only its line ② runs.")
        else:
            p.shot(box, "① rebuild_report, chosen last time in this flow, is now offered first (Enter chooses it).",
                   highlight=[buttons["rebuild_report"]], badges=True)
        QTest.mouseClick(buttons[then], Qt.LeftButton)

    p.while_open(lambda: QTest.mouseClick(run_now_button(win), Qt.LeftButton),
                 lambda box: look_at_the_question(box, "rebuild_report"))
    qtbot.waitUntil(lambda: len(tab.runs) == 1 and not tab.running and tab.runs[0].status is not None,
                    timeout=60_000)
    run = tab.runs[0].run_id
    p.shot(win, "② rebuild_report's line ran: its blocks are green, and build_report says “report built”. ① start's "
                "line did not run: its blocks show nothing.",
           highlight=LINES, badges=True, zoom=LINES)
    p.check("Run now asked which Manual Start: start, rebuild_report or All of them (or Cancel), with All of them "
            "first", asked[0] == (sorted(["start", "rebuild_report", "All of them", "Cancel"]), "All of them"))
    p.check("② rebuild_report's line ran: both its blocks show success, from that run",
            all(shows(tab, b)[:2] == ("success", run) for b in SECOND_LINE)
            and "report built" in shows(tab, "build_report")[2])
    p.check("① start's line did not run: its three blocks show nothing",
            all(shows(tab, b) == (None, None, "") for b in FIRST_LINE))

    p.step("Click the Run history tab, under the canvas.")
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier,
                     bar.tabRect(win.bottom_tabs.indexOf(win.history_panel)).center())
    table = win.history_panel.table
    rows = [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]
    p.shot(win, "① The run is listed as started by rebuild_report alone.", highlight=[table.item(0, 2)], badges=True,
           zoom=[table.item(0, 0), table.item(0, 4)])
    p.check("① Run history: one run, started by rebuild_report, success",
            [(r[0], r[2], r[3]) for r in rows] == [(str(run), "rebuild_report", "success")])

    p.step("Press ▶ Run now again, and click Cancel.")
    p.while_open(lambda: QTest.mouseClick(run_now_button(win), Qt.LeftButton),
                 lambda box: look_at_the_question(box, "Cancel"))
    p.check("① This time rebuild_report is offered first", asked[1][1] == "rebuild_report")
    p.check("Cancel runs nothing: still one run", len(tab.runs) == 1)


# --- 2. A + just right of the last tab -------------------------------------------------------------------------------

def test_a_plus_right_of_the_last_tab_opens_a_new_flow(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("A + just right of the last tab opens a new flow",
              "Add a plus sign to the right of the most recent tab in order to create a new tab")
    p.idea("A + just right of the last tab opens a new flow in a new tab, as File > New flow does.")
    p.idea("It follows the last tab as you open, close and move flows. When there are more tabs than fit and they "
           "scroll, it waits at the right end of the tabs' row, so it never covers a tab.")
    p.idea("The new flow starts as every new flow does: with a Manual Start block, start, and not saved until you "
           "save it (or run it).")
    p.why_simplest("It is one small button beside the tabs that does what File > New flow does: no new kind of flow, "
                   "nothing new to learn.")
    p.alternative("A + tab after the last tab",
                  "It would be a tab you could select, move or close, and everything that goes through the open "
                  "flows (saving them, reopening them next time, their statuses) would have to skip it.")
    p.alternative("A button at the right end of the tabs' row, the usual place for one",
                  "It would sit at the far right, away from the tabs, not right of the most recent tab where you "
                  "asked for it. The + goes there only when the tabs do not all fit.")
    p.alternative("Double-click the empty space beside the tabs", "Nothing would show that it is there.")
    p.fits("The + does what File > New flow (Ctrl+N) does, so the new flow is the same whichever you use.")
    p.fits("A flow opened with the + is like any other new flow: Save, or Run now, asks where to save it, and the "
           "flow takes that file's name.")
    flow = write_flow(tmp_path / "two-starts.yaml", TWO_STARTS)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu(win, "Open…").trigger()
    first = win.current

    p.step("Open a flow (here two-starts.yaml) and look above the canvas: the + is just right of its tab, the last "
           "one.")
    plus = win.tabs.tabBar().new_button
    last = tab_rect(win, win.tabs.count() - 1)
    p.shot(win, "① The last tab, two-starts.yaml, and ② the + just right of it.", highlight=[last, plus],
           badges=True, zoom=[last, plus])
    p.check("② The + is just right of the last tab, on the tabs' row", plus_beside_last_tab(win))

    p.step("Click the +. A new flow opens in a new tab.")
    QTest.mouseClick(plus, Qt.LeftButton)
    new = win.current
    last = tab_rect(win, win.tabs.count() - 1)
    p.shot(win, "① The new flow's tab, “untitled (unsaved)”, and ② the + again just right of it. ③ The new flow "
                "starts with a Manual Start block, start.",
           highlight=[last, plus, "start"], badges=True, zoom=[last, plus])
    p.check("① A new tab opens, with a new flow: not saved yet, with one block, the Manual Start start",
            win.tabs.count() == 2 and new is not first and new.doc.path is None
            and win.tabs.tabText(win.tabs.indexOf(new)) == "untitled (unsaved)"
            and {b: spec["type"] for b, spec in new.doc.blocks.items()} == {"start": "trigger.manual"})
    p.check("② The + is again just right of the last tab, the new one", plus_beside_last_tab(win))


# --- 3. Unsaved changes to a scheduled flow --------------------------------------------------------------------------

def test_a_scheduled_flow_with_unsaved_changes_says_the_scheduler_runs_the_saved_version(proof, win, qtbot, tmp_path,
                                                                                      monkeypatch):
    p = proof("A scheduled flow with unsaved changes says the scheduler runs the saved version",
              "I did deactivate a schedule start block but its flow kept running despite being deactivated, aren't "
              "flows supposed to be autosaved anyway?")
    p.idea("While a scheduled flow has unsaved changes, its Schedule blocks say that the scheduler runs the saved "
           "version. Once you save, it runs what you see.")
    p.idea("Under each Schedule block of the flow, the line that tells when it runs next says “unsaved · the "
           "scheduler runs the saved version” (cut to the block's width: hover it to read all of it). The flow's "
           "tab tooltip says it too.")
    p.idea("Flows are not saved by themselves: you chose to keep Save, so that half-finished edits never run "
           "unattended. The scheduler service runs the flow file as you last saved it, so a Schedule block unticked "
           "on the screen but not saved yet still runs.")
    p.why_simplest("The editor already knows both things: that the flow is scheduled, and that it has unsaved "
                   "changes. When both are true, it says so where you look at the schedule. What runs, and when, "
                   "does not change: it is now said.")
    p.alternative("Save by itself (autosave)",
                  "You chose to keep Save. With autosave, a change you are halfway through (a schedule half edited, "
                  "a block half set up) would be what the scheduler runs that night, with nobody there. Your work is "
                  "not lost meanwhile: the editor copies unsaved changes to a recovery folder as you work. Only the "
                  "flow file, which the scheduler reads, waits for your Save.")
    p.alternative("Save the Active box at once, and the rest on Save",
                  "Some changes would save themselves and others would not: harder to know what the file holds than "
                  "one rule and the * on the tab.")
    p.alternative("Ask whether to save each time you change a schedule",
                  "A question for every change, and it would not cover the other changes (a block's settings, its "
                  "connections) that the scheduler also runs as saved.")
    p.fits("Run now still saves a flow before it runs it, so a run you start from the editor always runs what you "
           "see. Only the scheduler, which runs when you are not there, waits for your Save.")
    p.fits("Within about 15 seconds of your Save, the scheduler service reads the saved file. It starts each run a "
           "few seconds before its time, and such a run does not begin if, by then, you have saved the schedule "
           "off.")
    p.fits("It is the line that already said when the next run comes, or that the scheduler service is off: now it "
           "also says when the next run would use the saved version.")
    flow = write_flow(tmp_path / "nightly-export.yaml", NIGHTLY)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    service = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.scheduler_action)

    p.step("Open nightly-export.yaml: a scheduled flow, whose Schedule block nightly starts it every night at 02:00. "
           "The scheduler service is on.")
    p.note("For this proof the real scheduler service is started (as from a terminal), and stopped at the end.")
    with scheduler_service(win.home):
        file_menu(win, "Open…").trigger()
        tab = win.current
        file_menu(win, "Save").trigger()  # saved with an active Schedule block: scheduled (as it was yesterday)
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=60_000)
        qtbot.waitUntil(lambda: under(tab, "nightly").startswith("next run in"), timeout=10_000)
        p.shot(win, "① Under nightly, the time left until its next run, at 02:00. ② The scheduler service is on.",
               highlight=["nightly", service], badges=True, zoom=["nightly"])
        p.check("The flow is scheduled, and ② the scheduler service is on",
                flow.resolve() in win.home.scheduled_flows() and win.scheduler_running())
        p.check("① Under nightly: “next run in …”", under(tab, "nightly").startswith("next run in"))

        p.step("Select nightly and untick Active in Properties. Do not save.")
        select_only(tab, "nightly")
        field(win, "Active").click()
        qtbot.waitUntil(lambda: under(tab, "nightly") == UNSAVED, timeout=5_000)
        active, index = field(win, "Active"), win.tabs.indexOf(tab)  # (Properties shows the block again)
        active_label = next(w for w in win.properties.findChildren(QLabel) if w.text() == "Active")
        p.shot(win, "① Under nightly: “unsaved · the scheduler runs the saved version”, cut to the block's width "
                    "(hover it to read all of it: next step). ② Active is unticked. ③ The * on the tab: not saved.",
               highlight=["nightly", [active_label, active], tab_rect(win, index)], badges=True,
               zoom=[tab_rect(win, index), "nightly"])
        p.check("① The line under nightly says “unsaved · the scheduler runs the saved version”",
                under(tab, "nightly") == UNSAVED and not active.isChecked())
        p.check("③ The tab has its *, and its tooltip (hover the tab) says it too",
                win.tabs.tabText(index) == "nightly-export.yaml *" and UNSAVED in win.tabs.tabToolTip(index))
        p.check("It is true: the saved file still has the schedule on, and the flow is still on the scheduler "
                "service's list, so the service would still run it at 02:00",
                [t.block for t in load_flow(flow).triggers] == ["nightly"]
                and flow.resolve() in win.home.scheduled_flows())

        p.step("Hover the line under nightly.")
        tip = hover_line_under(tab, "nightly")
        try:
            p.check("Its tooltip says all of it", tip is not None and QToolTip.text() == (
                "This flow has unsaved changes: the scheduler service runs the saved version of it. Save the flow to "
                "apply your changes."))
            p.shot(tip, "The tooltip says it in full: the scheduler service runs the saved version of the flow; save "
                        "it to apply your changes.", over=win, highlight=[tip], zoom=[tip, "nightly"])
        finally:
            QToolTip.hideText()

        p.step("Save the flow (File > Save).")
        file_menu(win, "Save").trigger()
        qtbot.waitUntil(lambda: under(tab, "nightly") == "not active", timeout=5_000)
        p.shot(win, "① Saved: nightly says “not active”, as any schedule with Active unticked does. ② No * on the "
                    "tab: what you see is what the scheduler runs.",
               highlight=["nightly", tab_rect(win, index)], badges=True, zoom=[tab_rect(win, index), "nightly"])
        p.check("① The line under nightly says “not active”; ② the tab has no * and its tooltip no longer says unsaved",
                under(tab, "nightly") == "not active" and win.tabs.tabText(index) == "nightly-export.yaml"
                and UNSAVED not in win.tabs.tabToolTip(index))
        p.check("The flow is off the scheduler service's list, and its file has the schedule off: it does not run at "
                "02:00 any more", flow.resolve() not in win.home.scheduled_flows() and not load_flow(flow).triggers)


# --- 4. The flows running list names a flow by its file -----------------------------------------------------------

def test_the_flows_running_list_names_a_new_flow_by_its_file(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("The flows running list names each flow by its file",
              "'Flows running' button at the bottom left corner shows a new (but already saved with a name) flow as "
              "'untitled'")
    p.idea("The flows running list, at the bottom left, names each flow by its file's name, not untitled.")
    p.idea("A run of weekly-report.yaml is listed as weekly-report, whoever started it: you, the scheduler or a "
           "terminal. And a new flow takes its file's name when you first save it, unless you named it yourself "
           "under Flow, so Run now's question says “Run weekly-report now?” too.")
    p.why_simplest("Every run records the flow file it ran, whoever started it: the editor, the scheduler or a "
                   "terminal. So the list always has the file's name to show, as the tab does.")
    p.alternative("Only name new flows after their file when first saved",
                  "That fixes new flows, but flows already saved as untitled, and the runs the scheduler or a "
                  "terminal starts from them, would still be listed as untitled. (New flows are now named after "
                  "their file as well.)")
    p.alternative("Ask for a name when you create a flow",
                  "One more question every time you start something, and the name could still differ from the "
                  "file's.")
    p.alternative("Show the flow's whole path",
                  "Too long for a small list at the bottom of the window; the file's name is what the tab shows.")
    p.fits("The list shows the runs going anywhere (from the editor, on schedule or from a terminal), and choosing "
           "one opens its flow: it now names them as the tabs and File > Open do.")
    p.fits("A name you gave the flow under Flow is kept when you save it, or save it as another file.")
    monkeypatch.setattr(dialogs, "confirm_run_now", REAL_CONFIRM_RUN_NOW)  # the real question, answered as you would
    screen = win.screen().geometry()  # the window down to the screen's bottom, as a full-screen one: the list
    win.move(0, screen.bottom() + 1 - win.frameGeometry().height())  # opens upwards, over the window
    report = tmp_path / "weekly-report.yaml"

    p.step("Choose File > New flow, and add a Wait block after start, set to 30 seconds. Double-click Wait in the "
           "Blocks list, drag it right of start, connect start to it, and type 30 in its Seconds. The run will take "
           "30 seconds: time to look at the list.")
    file_menu(win, "New flow").trigger()
    tab = win.current
    add_from_palette(win, "Wait")
    (wait,) = [b for b in tab.doc.blocks if b != "start"]
    drag_block(tab, wait, 260, 0)
    drag_port_to_port(tab, "start", "start", wait, "after")
    select_only(tab, wait)
    type_in(field(win, "Seconds"), "30")
    p.shot(win, "① start, connected to the Wait block; ② its Seconds: 30.0.",
           highlight=[["start", wait], field(win, "Seconds")], badges=True, zoom=["start", wait])
    p.check("① start is connected to the Wait block, ② which waits 30 seconds",
            ("start", "start", wait, "after") in tab.doc.edges() and tab.doc.blocks[wait]["config"] == {"seconds": 30})

    p.step("Press ▶ Run now: save the new flow as weekly-report.yaml, then click Run.")
    p.note("Where to save is asked in the system's own file window, which this proof cannot show: it is answered "
           "weekly-report.yaml for you.")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(report), "")))
    question = []

    def run_it(box):
        text = box.findChild(QLabel, "qt_msgbox_label")
        question.append(text.text())
        p.shot(box, "① Saved as weekly-report.yaml, the flow took its file's name: Run now asks “Run weekly-report "
                    "now?”, not untitled.", highlight=[text], badges=True)
        QTest.mouseClick(box.button(QMessageBox.Yes), Qt.LeftButton)  # Run

    p.while_open(lambda: QTest.mouseClick(run_now_button(win), Qt.LeftButton), run_it)
    p.check("The new flow is saved as weekly-report.yaml, and its tab says so",
            tab.doc.path == report.resolve() and win.tabs.tabText(win.tabs.indexOf(tab)) == "weekly-report.yaml")
    p.check("① Saved the first time, the flow took its file's name: Run now asked “Run weekly-report now?”, and the "
            "file names it weekly-report",
            "Run weekly-report now?" in question[0] and load_flow(report).name == "weekly-report")

    p.step("While it runs, click “1 flow running”, at the bottom left of the window.")
    try:
        qtbot.waitUntil(lambda: win.running_flows.text() == "1 flow running", timeout=30_000)
        listed = []

        def look_at_the_list(menu):
            listed.extend((a.text(), tab.running) for a in menu.actions())
            p.shot(menu, "① The flows running: weekly-report, by its file's name, and since when its run is going. "
                         "No longer untitled.", over=win,
                   highlight=[menu.actions()[0]], badges=True, zoom=[menu, win.running_flows])

        p.while_open(lambda: QTest.mouseClick(win.running_flows, Qt.LeftButton), look_at_the_list)
        p.check("① The list shows the run going, named weekly-report (its file's name), not untitled",
                len(listed) == 1 and listed[0][0].startswith("weekly-report  ·  running since ")
                and "untitled" not in listed[0][0] and listed[0][1])
    finally:  # (no need to wait for the rest of the 30 seconds)
        win.stop(tab)
        qtbot.waitUntil(lambda: not tab.running, timeout=30_000)
