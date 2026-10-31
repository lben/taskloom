"""Proof: a run refuses to start only for problems in the blocks it runs; problems elsewhere are warnings. See
README.md here."""

from pathlib import Path

import yaml
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QToolBar

from proofs.conftest import choose, menu_item, right_click
from taskloom.history import History
from tests.test_lines import shown, wait_runs

# Taken as this file is imported, before the win fixture answers message boxes itself: Validate's box and the
# refusal's are then shown as in the app.
REAL_WARNING = QMessageBox.warning
ASKED = ("if I have an if block with no condition, when trying to run it it gives me 'ERROR if: Condition is "
         "required' which is correct, the problem is that if I isolate it which marks it as off (correct as well) and "
         "try to run the flow it still doesn't let me run it because of the same error ... off blocks should just be "
         "ignored altogether ... the manual start line should work without issues")
SALES = "pl.DataFrame({'region': ['north', 'south', 'east'], 'amount': [120, 80, 45]})"
FLOW = {
    "blocks": {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
        "load_sales": {"type": "logic.python", "config": {"inputs": [], "code": f"output = {SALES}"},
                       "ui": {"x": 260, "y": 0}},
        "total_sales": {"type": "data.polars", "config": {"code": "df.select(pl.col('amount').sum())"},
                        "ui": {"x": 520, "y": 0}},
        "by_region": {"type": "data.polars", "config": {"code": "df.group_by('region').agg(pl.col('amount').sum())"},
                      "ui": {"x": 520, "y": 130}},
        "every_night": {"type": "trigger.schedule", "config": {"cron": "0 2 * * *"}, "ui": {"x": 0, "y": 290}},
        "pick_region": {"type": "logic.python", "config": {"inputs": [], "code": "output = 'north'"},
                        "ui": {"x": 260, "y": 290}},
        "check_region": {"type": "logic.if", "config": {}, "ui": {"x": 520, "y": 290}},  # no Condition
    },
    "edges": ["start_by_hand.start -> load_sales.after", "load_sales.output -> total_sales.table",
              "load_sales.output -> by_region.table",
              "every_night.start -> pick_region.after", "pick_region.output -> check_region.value"],
}
MANUAL_LINE = ["start_by_hand", "load_sales", "total_sales"]
SCHEDULE_LINE = ["every_night", "pick_region", "check_region"]
EVERYTHING = [*MANUAL_LINE, "by_region", *SCHEDULE_LINE]
TO_BY_REGION = "load_sales.output -> by_region.table"
NO_CONDITION = "check_region: Condition is required"
NO_TABLE = "blocks.by_region: required input 'table' is not connected"
NOT_IN_RUN = "(not taking part in this run)"  # said after such a problem in a run's Log
OFF = "(off: not connected to a start block)"  # said after it by Validate and Check flows
OFF_LABEL = "off · not connected to a start block"  # under an off block, on the canvas
EVERY_NIGHT = "Run the flow now, as if its time had come"


def open_flow(win, monkeypatch, path: Path):
    """Open a flow file (File > Open…), in a tab of its own."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    QTest.qWait(100)
    return win.current


def press(win, action):
    """Press a button of the toolbar (▶ Run now, Validate)."""
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(action), Qt.LeftButton)


def click_bottom_tab(win, panel):
    """Click one of the tabs under the canvas (the Log, Run history…)."""
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.bottom_tabs.indexOf(panel)).center())
    QTest.qWait(100)


def delete_connection(win, tab, qtbot, text: str):
    """Click a connection on the canvas, in the middle of its line, and press Delete."""
    (edge,) = [e for e in tab.scene.edge_items if e.text == text]
    at = tab.view.mapFromScene(edge.mapToScene(edge.path().pointAtPercent(0.5)))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    assert edge.isSelected(), f"clicking {text} did not select it"
    win.activateWindow()  # the keys go to the window you are in
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=5_000)
    tab.view.setFocus()
    QTest.keyClick(tab.view, Qt.Key_Delete)
    QTest.qWait(200)  # the canvas is drawn again


def log_lines(win, level: str) -> list[str]:
    """The Log's lines at `level`, as it shows them (the time, the level, the message)."""
    return [line for line in win.log.text.toPlainText().splitlines() if f" {level} " in line]


def history(win, read):
    """What read(history) reads of the run history the editor keeps."""
    kept = History(win.home.history_db)
    try:
        return read(kept)
    finally:
        kept.close()


def box_text(box) -> QLabel:
    """The text of a message box, to outline it."""
    return next(label for label in box.findChildren(QLabel) if label.text() == box.text())


def test_a_run_refuses_only_for_problems_in_the_blocks_it_runs(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("A run refuses to start only for problems in the blocks it runs", ASKED)
    p.idea("A run now refuses to start only for a problem in a block it runs. A problem in any other block, an off "
           "block or a block only another start block's line reaches, no longer stops it: the run goes ahead, and "
           "its Log warns about the problem, saying that block is not taking part in this run.")
    p.idea("Which blocks a run runs did not change. ▶ Run now runs the Manual Start's line: the blocks after it, with "
           "the blocks whose data they need. A Schedule block starts its own line, when its time comes or from its "
           "menu. Run only this block and Run from this block on run just those blocks. Each run is now checked for "
           "the blocks it runs, and for nothing else.")
    p.idea("Validate (F7) still checks the whole flow, every line, so that you hear of each problem before a run "
           "meets it. A problem in a block that some start block's line runs is an error, as before. A problem only "
           "an off block has is a warning that says the block is off. Tools > Check flows lists it the same way.")
    p.idea("Some problems still stop every run, whatever block they are about, because they can change the run "
           "itself: a connection to a block, an input or an output that does not exist; a loop through a block that "
           "runs; a problem in the flow's parameters, calendar or time zone; a wrong value in Settings.")
    p.why_simplest("The check that decides whether a run may start was already there. It is now told which blocks "
                   "the run takes part in, and it sorts what it finds: a problem in one of those blocks is an error "
                   "that stops the run, as before; a problem in any other block is a warning the run goes ahead "
                   "with. The messages are the same ones, and there is nothing new to set.")
    p.why_simplest("The blocks a run is checked for are worked out by the same rule that decides which blocks it runs, "
                   "so the check and the run cannot disagree: what is checked is exactly what runs.")
    p.alternative("Say nothing at all about off blocks (“ignored altogether”)",
                  "The run does ignore them: it neither runs them nor stops for them. But an off block is often one "
                  "you have put aside for a while. If Validate said nothing about it, you would only learn of its "
                  "problem when you connect it again. So Validate and Check flows still list it, as a warning, never "
                  "as an error.")
    p.alternative("Go easy on off blocks only, and keep checking every line on each run",
                  "That mends the off block, but not your second point: with an If missing its Condition in the "
                  "Schedule line, the Manual Start line still could not run. Checking each run for its own blocks "
                  "covers both with one rule.")
    p.alternative("Turn every problem into a warning, and let a block fail when it runs",
                  "A block that cannot work would then start a run and fail halfway, after the blocks before it had "
                  "done their part (written a file, sent an email). For a block that runs, refusing before anything "
                  "happens is safer: nothing runs until it is fixed.")
    p.fits("It builds on the rule that switches a block off (connect it to no start block) and on start blocks' "
           "lines: the canvas marks “off · not connected to a start block” on exactly the blocks Validate warns are "
           "off.")
    p.fits("▶ Run now, a Schedule block's “Run the flow now, as if its time had come”, the scheduler service, "
           "taskloom run in a terminal, Run only this block and Run from this block on all check this way, each for "
           "the blocks it runs. taskloom validate and taskloom check list the problems as Validate and Check flows "
           "do.")
    p.fits("A run's warnings are lines of its Log like any other, kept with the run in its history.")

    path = tmp_path / "sales.yaml"
    path.write_text(yaml.safe_dump(FLOW, sort_keys=False), encoding="utf-8")
    log = win.log.text

    p.step("Open the flow sales.yaml (File > Open…). Like yours, it has a Manual Start line and a Schedule line, and "
           "the Schedule line has an If with no Condition.")
    tab = open_flow(win, monkeypatch, path)
    p.check("The flow opens", tab is not None and tab.doc.path == path.resolve())
    p.shot(win, "① The Manual Start line: start_by_hand runs load_sales, whose table goes to total_sales and to "
                "by_region. ② The Schedule line: every_night runs pick_region, then check_region, an If with no "
                "Condition.",
           highlight=[[*MANUAL_LINE, "by_region"], SCHEDULE_LINE], badges=True, zoom=EVERYTHING)

    p.step("Isolate by_region: click the connection from load_sales to by_region, and press Delete. by_region is "
           "then connected to nothing, so it is off, and its table input, which it needs, is not connected.")
    delete_connection(win, tab, qtbot, TO_BY_REGION)
    p.check("The connection is gone", TO_BY_REGION not in tab.doc.data["edges"])
    p.check("① by_region is off: the canvas says “off · not connected to a start block” under it",
            tab.scene.nodes["by_region"].status_text == OFF_LABEL)
    p.shot(win, "① by_region is off: “off · not connected to a start block”, under it.",
           highlight=["by_region"], badges=True, zoom=[*MANUAL_LINE, "by_region"])
    p.note("by_region stands for any block you isolate. An If with no Condition isolated this way is off too, and "
           "its missing Condition is then a warning, just like by_region's input.")

    p.step("Press ▶ Run now in the toolbar. It starts the Manual Start line ①.")
    press(win, win.run_action)
    p.check("▶ Run now does not refuse the run: no box comes up", win.message_boxes == [])
    first = wait_runs(qtbot, tab, 1)
    QTest.qWait(300)  # what the run printed last reaches the Log
    p.shot(win, "① The run went ahead: start_by_hand, load_sales and total_sales are green, and total_sales made "
                "its table (“✓ table: 1 rows × 1 columns”). ② by_region, off, did not run. ③ The Schedule line did not run: its blocks show nothing.",
           highlight=[MANUAL_LINE, "by_region", SCHEDULE_LINE], badges=True, zoom=EVERYTHING)
    p.check(f"① The run went ahead and succeeded: run #{first}", tab.runs[0].status == "success")
    p.check("① start_by_hand, load_sales and total_sales show success, from that run",
            shown(tab, MANUAL_LINE) == {b: ("success", first) for b in MANUAL_LINE})
    p.check("total_sales made 245 (120 + 80 + 45)", "245" in tab.scene.values.get("total_sales", {}).get("table", ""))
    p.check("② by_region is still off, with no result; ③ the Schedule line's blocks show nothing",
            shown(tab, ["by_region"]) == {"by_region": ("off", None)}
            and shown(tab, SCHEDULE_LINE) == {b: (None, None) for b in SCHEDULE_LINE})

    warned = [(log, NO_CONDITION), (log, NO_TABLE)]
    p.shot(win, "The outlined lines of the Log, under the canvas. It warns that check_region has no Condition and that "
                "by_region's table input is not connected, each “not taking part in this run”: first as ▶ Run now "
                f"checks the run, then again in the run's own lines, after “run #{first} … started”. The last line: "
                "the run ended with success.",
           highlight=[warned, (log, f"run #{first} success")], zoom=[*warned, (log, f"run #{first} success")])
    warnings = log_lines(win, "WARNING")
    p.check("The Log warns that check_region has no Condition, and is not taking part in this run",
            any(f"{NO_CONDITION} {NOT_IN_RUN}" in line for line in warnings))
    p.check("The Log warns that by_region's table input is not connected, and it is not taking part in this run",
            any(f"{NO_TABLE} {NOT_IN_RUN}" in line for line in warnings))
    p.check("The Log has no ERROR line", log_lines(win, "ERROR") == [])
    kept = history(win, lambda h: [e["message"] for e in h.logs(first) if e["level"] == "WARNING"])
    p.check(f"The history of run #{first} keeps both warnings",
            sorted(kept) == sorted([f"{NO_CONDITION} {NOT_IN_RUN}", f"{NO_TABLE} {NOT_IN_RUN}"]))

    p.step("Click the Run history tab, under the canvas.")
    click_bottom_tab(win, win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 1, timeout=10_000)
    p.shot(win, f"Run history lists run #{first}, started by start_by_hand, as success.",
           highlight=[table.item(0, 3)], zoom=[table.item(0, 0), table.item(0, 4)])
    p.check(f"Run history lists run #{first}, started by start_by_hand, as success",
            [table.item(0, c).text() for c in (0, 2, 3)] == [str(first), "start_by_hand", "success"])
    p.check(f"The history of run #{first} holds the Manual Start line's blocks and no other: nothing of the Schedule "
            "line ran, nor by_region", set(history(win, lambda h: h.blocks(first))) == set(MANUAL_LINE))

    p.step("Press Validate in the toolbar (F7). It checks the whole flow: both lines, and by_region.")
    said = []

    def look_at_the_problems(box):
        said.append(box.text())
        p.shot(box, "The flow has problems: check_region: Condition is required. It is the only one here: by_region's "
                    "is a warning, in the Log.", highlight=[box_text(box)])
        box.accept()

    with monkeypatch.context() as real:  # the box as the app shows it
        real.setattr(QMessageBox, "warning", REAL_WARNING)
        p.while_open(lambda: press(win, win.validate_action), look_at_the_problems)
    QTest.qWait(100)
    p.check("Validate's box names check_region's problem, and only it", said == [NO_CONDITION])
    listed = [(log, f"{NO_TABLE} {OFF}"), (log, f"ERROR {NO_CONDITION}")]
    p.shot(win, "The outlined lines of the Log keep what Validate found: a WARNING for by_region, which says it is "
                "off: not connected to a start block; and an ERROR for check_region, which the Schedule line runs.",
           highlight=[listed], zoom=listed)
    errors, warnings = log_lines(win, "ERROR"), log_lines(win, "WARNING")
    p.check("The Log lists by_region's problem as a WARNING that says it is off",
            any(line.endswith(f"{NO_TABLE} {OFF}") for line in warnings))
    p.check("The Log lists check_region's problem as an ERROR",
            any(line.endswith(f"ERROR {NO_CONDITION}") for line in errors))
    p.check("by_region's problem is not an ERROR", not any(NO_TABLE in line for line in errors))

    p.step(f"Right-click every_night and choose “{EVERY_NIGHT}”. It starts the Schedule line ②, which check_region "
           "is in.")
    said.clear()

    def look_at_the_menu(menu):
        p.shot(menu, "The menu of every_night, with the item to choose.", over=win,
               highlight=[menu_item(menu, EVERY_NIGHT)], zoom=[menu, "every_night"])
        p.while_open(lambda: choose(menu, EVERY_NIGHT), look_at_the_refusal)

    def look_at_the_refusal(box):
        said.append(box.text())
        p.shot(box, "The run is refused, and the box says why: check_region: Condition is required. check_region "
                    "is in the line every_night starts.", highlight=[box_text(box)])
        box.accept()

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "warning", REAL_WARNING)
        p.while_open(lambda: right_click(tab, "every_night"), look_at_the_menu)
    QTest.qWait(100)
    p.check("The box says “check_region: Condition is required”, and only that", said == [NO_CONDITION])
    p.check(f"No run was recorded: the history still holds only run #{first}",
            [r["id"] for r in history(win, lambda h: h.runs())] == [first] and len(tab.runs) == 1)
    p.note("The scheduler service checks the same way when every_night's time comes: that run is refused too, for "
           "the same reason, while ▶ Run now still runs the Manual Start line.")

    p.step("Choose Check flows… in the Tools menu. It checks your open and scheduled flows, as Taskloom does by "
           "itself the first time a new version starts.")
    details = []

    def look_at_the_check(dialog):
        box = dialog.details
        details.extend(line.strip() for line in box.toPlainText().splitlines())
        p.shot(dialog, "① check_region's problem needs you. ② by_region's is a warning, which says it is off: not "
                       "connected to a start block.",
               highlight=[(box, f"needs you: {NO_CONDITION}"), (box, f"warning: {NO_TABLE}")], badges=True)
        dialog.reject()  # Close

    tools = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Tools")
    p.while_open(lambda: menu_item(tools, "Check flows").trigger(), look_at_the_check)
    p.check("① check_region's problem is listed as needing you, and nothing else is",
            [line for line in details if line.startswith("needs you:")] == [f"needs you: {NO_CONDITION}"])
    p.check("② by_region's problem is listed as a warning that says it is off",
            [line for line in details if line.startswith("warning:")] == [f"warning: {NO_TABLE} {OFF}"])
