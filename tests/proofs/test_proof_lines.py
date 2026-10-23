"""Proof: each start block runs its own line (v0.18). The example every proof follows: see README.md here."""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QToolBar

from proofs.conftest import choose, menu_item, right_click
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
        config: {inputs: [], code: "raise RuntimeError('cannot reach the archive folder')"}
        ui: {x: 260, y: 200}
    edges:
      - start_by_hand.start -> read_sales.after
      - read_sales.output -> total_sales.input
      - every_night.start -> clean_up.after
"""
MANUAL_LINE = ["start_by_hand", "read_sales", "total_sales"]
SCHEDULE_LINE = ["every_night", "clean_up"]
LINES = [MANUAL_LINE, SCHEDULE_LINE]  # highlighted as ① and ②


def shows(tab, block) -> tuple:
    """What the canvas shows of a block: its status, the run its result is from, what its output held."""
    return (tab.scene.nodes[block].status, (tab.block_info.get(block) or {}).get("run_id"),
            tab.scene.values.get(block, {}).get("output", ""))


def test_each_start_block_runs_its_own_line(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Each start block runs its own line",
              "If one line succeeds or fails, the other lines don't get its outputs cleared out.")
    p.idea("Each start block runs its own line, and the other lines keep the results they show.")
    p.idea("A flow can have several start blocks. A start block and the blocks that follow it (with any block they "
           "take data from) form a line. When a start block starts a run, that run takes part only in its own line: "
           "it runs those blocks and no others.")
    p.idea("On the canvas, each block shows its latest result: the one from the last run it took part in. Every "
           "earlier result stays in the run history.")
    p.why_simplest("It all comes down to two plain rules: a run records only the blocks of its own line, and each "
                   "block shows its latest result. Nothing else is needed: no list of what to keep, no blocks marked "
                   "“not this time”.")
    p.why_simplest("When a run starts, the canvas clears just the blocks that run takes part in, and fills them in as "
                   "they finish. A block of another line is not part of the run, so nothing touches it: it simply "
                   "goes on showing what it showed.")
    p.alternative("One run for the whole flow, with the other lines' blocks marked “skipped”",
                  "Every run would then overwrite every block: the nightly clean-up would wipe out the sales total "
                  "you had just looked at, which is the very problem you described. And “skipped” would mean two "
                  "things: “something before it failed”, and “not its turn”.")
    p.alternative("A separate flow file for each line",
                  "It would work, but you would lose the one picture of work that belongs together, and the lines "
                  "could no longer share the flow's parameters. You asked for lines in one flow.")
    p.alternative("Remembering each line's last results on the side",
                  "That is a second copy of what the run history already holds, which would have to be kept in step "
                  "with it, and could fall out of step. The history already knows each block's latest result.")
    p.fits("Taskloom already worked out which blocks a run takes part in: it is the rule that switches off a block "
           "connected to no start block. A line is that same rule, applied to one start block, and the canvas marks "
           "“off · not connected to a start block” on exactly the blocks that are in no line.")
    p.fits("The canvas, Run history and “Results of this block…” all read the same records: each block's result in "
           "each run it took part in. That is why they always agree, and why a run you pick in Run history shows on "
           "the canvas just as it did when it ran.")
    p.fits("A Schedule block's line runs the same way whether the scheduler service starts it when its time comes, or "
           "you start it from the block's menu, as this proof does.")
    flow = write_flow(tmp_path / "two-lines.yaml", FLOW)

    p.step("Open the flow two-lines.yaml (File > Open…). It has two lines: ① the Manual Start block start_by_hand "
           "runs read_sales, then total_sales; ② the Schedule block every_night runs clean_up, which is set up to "
           "fail (“cannot reach the archive folder”), to show what a failing line does to the other one.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    p.shot(win, "The flow as it opens, with its two lines ① and ②. Nothing has run yet, so no block shows a result.",
           highlight=LINES, badges=True, zoom=LINES)

    p.step("Press ▶ Run now in the toolbar. It starts the Manual Start line ①.")
    run_now = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action)
    QTest.mouseClick(run_now, Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == 1 and not tab.running and tab.runs[0].status is not None,
                    timeout=60_000)
    first = tab.runs[0].run_id
    p.shot(win, "① The Manual Start line ran: its three blocks are green and show what they made; total_sales made "
                "245. ② The Schedule line did not run, so it still shows nothing.",
           highlight=LINES, badges=True, zoom=LINES)
    p.check("① The Manual Start line ran: its three blocks show success",
            all(shows(tab, b)[:2] == ("success", first) for b in MANUAL_LINE))
    p.check("total_sales shows 245 (120 + 80 + 45)", "245" in shows(tab, "total_sales")[2])
    p.check("② The Schedule line did not run: its blocks show nothing",
            all(shows(tab, b) == (None, None, "") for b in SCHEDULE_LINE))

    p.step("Right-click every_night and choose “Run the flow now, as if its time had come”. It starts the Schedule "
           "line ②.")
    p.note("No scheduler service runs during this proof, so the Schedule line is started from its block's menu, as "
           "you would to try it out. The scheduler service starts it the same way when its time comes.")

    def run_the_schedule_line(menu):
        p.shot(menu, "The menu of every_night, with the item to choose.", over=win,
               highlight=[menu_item(menu, "Run the flow now, as if its time had come")], zoom=[menu, "every_night"])
        choose(menu, "Run the flow now, as if its time had come")

    p.while_open(lambda: right_click(tab, "every_night"), run_the_schedule_line)
    qtbot.waitUntil(lambda: len(tab.runs) == 2 and not tab.running and tab.runs[1].status is not None,
                    timeout=60_000)
    second = tab.runs[1].run_id
    p.shot(win, "② The Schedule line ran, and failed: clean_up is red and says why. ① The Manual Start line still "
                "shows its results from the first run: nothing was cleared.",
           highlight=LINES, badges=True, zoom=LINES)
    p.check("② The Schedule line ran: every_night shows success and clean_up shows its failure, from the second run",
            shows(tab, "every_night")[:2] == ("success", second) and shows(tab, "clean_up")[:2] == ("failed", second))
    p.check("① The Manual Start line still shows its results from the first run",
            all(shows(tab, b)[:2] == ("success", first) for b in MANUAL_LINE))
    p.check("total_sales still shows 245", "245" in shows(tab, "total_sales")[2])

    p.step("Press ▶ Run now again. The Manual Start line ① runs a second time: it is the flow's third run.")
    QTest.mouseClick(run_now, Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == 3 and not tab.running and tab.runs[2].status is not None,
                    timeout=60_000)
    third = tab.runs[2].run_id
    run_3 = (win.log.text, f"run #{third} ")  # its lines in the Log: it started, it ended
    p.shot(win, "③ The Log, under the canvas, shows the third run, just after the second one failed: it started, and "
                "ended with success. ① The Manual Start line ran again and made the same results, so it looks just as "
                "before. ② clean_up still shows its failure: a line that succeeds does not clear another line's "
                "failure either.",
           highlight=[*LINES, run_3], badges=True, zoom=[(win.log.text, f"run #{second} failed"), run_3])
    log = win.log.text.toPlainText()
    p.check("③ The Log shows that the third run started and ended with success",
            f"run #{third} · " in log and f"DONE run #{third} success" in log)
    p.check("① The Manual Start line shows success, from the third run",
            all(shows(tab, b)[:2] == ("success", third) for b in MANUAL_LINE))
    p.check("② clean_up still shows its failure from the second run", shows(tab, "clean_up")[:2] == ("failed", second))

    p.step("Click the Run history tab, under the canvas.")
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier,
                     bar.tabRect(win.bottom_tabs.indexOf(win.history_panel)).center())
    table = win.history_panel.table
    runs = [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]
    p.shot(win, "Every run is kept, newest first. Started by says which start block started each: start_by_hand "
                "for runs 1 and 3 (line ①), every_night for run 2 (line ②).",
           highlight=[[table.item(r, 2) for r in range(table.rowCount())]],
           zoom=[table.item(0, 0), table.item(table.rowCount() - 1, 4)])
    p.check("Run history lists the three runs, newest first, each with the start block that started it",
            [(r[0], r[2], r[3]) for r in runs] == [(str(third), "start_by_hand", "success"),
                                                    (str(second), "every_night", "failed"),
                                                    (str(first), "start_by_hand", "success")])

    p.step("Right-click total_sales and choose “Results of this block…”.")
    rows = []

    def look_at_results(dialog):
        results = dialog.table
        rows.extend([results.item(r, c).text() for c in range(results.columnCount())]
                    for r in range(results.rowCount()))
        p.shot(dialog, "total_sales took part in runs 3 and 1, both started by start_by_hand, and made 245 each time. "
                       "Run 2, the Schedule line's, never touched it.",
               highlight=[[(results, 0), (results, 1)]])

    p.while_open(lambda: right_click(tab, "total_sales"),
                 lambda menu: p.while_open(lambda: choose(menu, "Results of this block…"), look_at_results))
    p.check("Its results list two runs, 3 and 1, both started by start_by_hand, both success with 245",
            [(r[0], r[2], r[3]) for r in rows] == [(str(third), "start_by_hand", "success"),
                                                    (str(first), "start_by_hand", "success")]
            and all("245" in r[4] for r in rows))
