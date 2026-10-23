"""Proof: each start block runs its own line (v0.18)."""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QToolBar

from proofs.conftest import choose, right_click
from tests.test_editor import write_flow

FLOW = """
    blocks:
      start_by_hand:
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
      every_night:
        type: trigger.schedule
        config: {cron: "0 2 * * *"}
        ui: {x: 0, y: 200}
      clean_up:
        type: logic.python
        config: {inputs: [], code: "output = 'old files removed'"}
        ui: {x: 260, y: 200}
    edges:
      - start_by_hand.start -> read_sales.after
      - read_sales.output -> total_sales.input
      - every_night.start -> clean_up.after
"""
MANUAL_LINE = ["start_by_hand", "read_sales", "total_sales"]
SCHEDULE_LINE = ["every_night", "clean_up"]


def shows(tab, block) -> tuple:
    """What the canvas shows of a block: its status, the run its result is from, what its output held."""
    return (tab.scene.nodes[block].status, (tab.block_info.get(block) or {}).get("run_id"),
            tab.scene.values.get(block, {}).get("output", ""))


def test_each_start_block_runs_its_own_line(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Each start block runs its own line",
              "If one line succeeds or fails, the other lines don't get its outputs cleared out.")
    flow = write_flow(tmp_path / "two-lines.yaml", FLOW)

    p.step("Open the flow two-lines.yaml (File > Open…). It has two lines: the Manual Start block start_by_hand "
           "runs read_sales then total_sales; the Schedule block every_night runs clean_up.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    tab = win.current
    p.check("The flow is open", tab is not None and tab.doc.path == flow.resolve())
    p.shot(win, "The flow as opened: no block shows a result yet.")

    p.step("Press ▶ Run now in the toolbar.")
    run_now = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action)
    QTest.mouseClick(run_now, Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == 1 and not tab.running and tab.runs[0].status is not None,
                    timeout=60_000)
    first = tab.runs[0].run_id
    p.shot(win, "After Run now: start_by_hand, read_sales and total_sales show their results (in green). "
                "every_night and clean_up show nothing: they are the other line, which did not run.")
    p.check("The Manual Start line ran: its three blocks show success",
            all(shows(tab, b)[:2] == ("success", first) for b in MANUAL_LINE))
    p.check("total_sales shows its output: 245 (120 + 80 + 45)", "245" in shows(tab, "total_sales")[2])
    p.check("The Schedule line did not run: its blocks show nothing",
            all(shows(tab, b) == (None, None, "") for b in SCHEDULE_LINE))

    p.step("Right-click the Schedule block every_night and choose “Run the flow now, as if its time had come”.")
    p.note("No scheduler service runs in this test, so the Schedule line is started from its block's menu, as you "
           "would to try it out; the scheduler service starts it the same way when its time comes.")
    p.shot_while_open(lambda: right_click(tab, "every_night"),
                      "The menu of every_night: “Run the flow now, as if its time had come”.",
                      then=lambda menu: choose(menu, "Run the flow now, as if its time had come"), over=win)
    qtbot.waitUntil(lambda: len(tab.runs) == 2 and not tab.running and tab.runs[1].status is not None,
                    timeout=60_000)
    second = tab.runs[1].run_id
    p.shot(win, "After the Schedule line ran: every_night and clean_up show their results from this second run, and "
                "start_by_hand, read_sales and total_sales still show theirs from the first run: not cleared.")
    p.check("The Schedule line ran: its blocks show success, from the second run",
            all(shows(tab, b)[:2] == ("success", second) for b in SCHEDULE_LINE))
    p.check("The Manual Start line still shows its results from the first run",
            all(shows(tab, b)[:2] == ("success", first) for b in MANUAL_LINE))
    p.check("total_sales still shows its output: 245", "245" in shows(tab, "total_sales")[2])

    p.step("Right-click total_sales and choose “Results of this block…”.")
    rows = []

    def look_at_results(dialog):
        table = dialog.table
        rows.extend([table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount()))
        dialog.close()

    p.shot_while_open(
        lambda: right_click(tab, "total_sales"), "The menu of total_sales.",
        then=lambda menu: p.shot_while_open(
            lambda: choose(menu, "Results of this block…"),
            "The results of total_sales: it took part in one run, the first, started by start_by_hand; the "
            "Schedule line's run did not touch it.", then=look_at_results),
        over=win)
    p.check(f"Its results list one run: run {first}, started by start_by_hand, success, 245",
            len(rows) == 1 and rows[0][0] == str(first) and rows[0][2:4] == ["start_by_hand", "success"]
            and "245" in rows[0][4])
