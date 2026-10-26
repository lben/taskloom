"""Proof: each start block chooses how long its runs are kept (v0.21): see README.md here."""

import yaml
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QGroupBox, QSpinBox,
                               QStyle, QStyleOptionButton, QToolBar)

from proofs.conftest import choose, right_click
from tests.test_editor import write_flow

FLOW = """
    blocks:
      by_hand:
        type: trigger.manual
        ui: {x: 0, y: 0}
      month_end_report:
        type: logic.python
        config: {inputs: [], code: "output = 'report made'"}
        ui: {x: 260, y: 0}
      every_minute:
        type: trigger.schedule
        config: {every: 1 minute}
        ui: {x: 0, y: 200}
      check_orders:
        type: logic.python
        config:
          inputs: []
          code: |
            from pathlib import Path
            if Path(r"DOWN").exists():
                raise RuntimeError("the order server did not answer")
            output = "the order server answered"
        ui: {x: 260, y: 200}
    edges:
      - by_hand.start -> month_end_report.after
      - every_minute.start -> check_orders.after
"""
MANUAL_LINE = ["by_hand", "month_end_report"]
SCHEDULE_LINE = ["every_minute", "check_orders"]
CHOICES = ["As in Settings", "All of them", "The last N", "For a time"]
FAILED = "Always keep failed runs"
# The runs, in the order they are started: M by_hand's line (Run now), S every_minute's (its menu); run 3 fails.
ORDER = "SMSSMSSM"
FAILS = 3


def file_menu(win, item: str):
    menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in menu.actions() if a.text() == item).trigger()


def click_block(win, block: str):
    tab = win.current
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier,
                     tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center()))
    QApplication.processEvents()
    assert win.properties.block_id == block, f"{block} is not the block Properties shows"


def click_tab(tabs, widget):
    bar = tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(tabs.indexOf(widget)).center())


def keep_box(win) -> QGroupBox:
    """The Keep runs box of the start block shown in Properties (made anew whenever the panel is), scrolled to."""
    QApplication.processEvents()
    box = next(b for b in win.properties.widget().findChildren(QGroupBox) if b.title() == "Keep runs")
    win.properties.ensureWidgetVisible(box)
    return box


def keep_list(win) -> QComboBox:
    return next(c for c in keep_box(win).findChildren(QComboBox) if c.itemText(0) == CHOICES[0])


def failed_box(win) -> QCheckBox:
    return next(c for c in keep_box(win).findChildren(QCheckBox) if c.text() == FAILED)


def items(combo: QComboBox) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def tick(box: QCheckBox) -> QRect:
    """Where a tick box's box is, in it (a click in the empty part of its row does nothing)."""
    option = QStyleOptionButton()
    box.initStyleOption(option)
    return box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box)


def click_tick(box: QCheckBox):
    QTest.mouseClick(box, Qt.LeftButton, Qt.NoModifier, tick(box).center())


def pick(popup, text: str):
    """Click `text` in the open list of a drop-down, as you would (not in the first instant after it opened:
    the drop-down takes no click then, so the click that opened it does not also choose)."""
    view = popup.findChild(QAbstractItemView)
    QTest.qWait(QApplication.doubleClickInterval() + 100)
    index = next(view.model().index(r, 0) for r in range(view.model().rowCount())
                 if view.model().index(r, 0).data() == text)
    at = view.visualRect(index).center()
    QTest.mouseMove(view.viewport(), at)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, at)


def open_list(combo: QComboBox):
    QTest.mouseClick(combo, Qt.LeftButton)


def drag_up(win, dock, by: int):
    """Drag the edge between the canvas and `dock`, the panels under it, up by `by` pixels, as you would."""
    at = QPoint(dock.geometry().center().x(), (win.centralWidget().geometry().bottom() + dock.geometry().top()) // 2)
    QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, at)
    for dy in range(10, by + 1, 10):
        QTest.mouseMove(win, at - QPoint(0, dy))
        QTest.qWait(20)  # the window moves the edge as the mouse goes
    QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, at - QPoint(0, by))


def all_shown(table) -> bool:
    """Every row of the table is in sight, none scrolled away or cut."""
    return all(table.viewport().rect().contains(table.visualItemRect(table.item(r, 0)))
               for r in range(table.rowCount()))


def history_rows(win) -> list[tuple[str, str, str]]:
    """Run history as it shows: (run, started by, status), newest first."""
    table = win.history_panel.table
    return [tuple(table.item(r, c).text() for c in (0, 2, 3)) for r in range(table.rowCount())]


def history_lines(table) -> list:
    """Each line of Run history, from Run to Duration (its Parameters column is empty here)."""
    return [[table.item(r, 0), table.item(r, 4)] for r in range(table.rowCount())]


def start_schedule_line(p, tab):
    """Right-click every_minute and choose “Run the flow now, as if its time had come”."""
    p.while_open(lambda: right_click(tab, "every_minute"),
                 lambda menu: choose(menu, "Run the flow now, as if its time had come"))


def run_folders(home) -> list[int]:
    return sorted(int(p.name) for p in home.runs_dir.iterdir() if p.is_dir() and p.name.isdigit())


def test_each_start_block_chooses_how_long_its_runs_are_kept(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Each start block chooses how long its runs are kept",
              "probably we need to make the retention policy a parameter per start block so that for any start "
              "block I can choose a retention policy where I can choose to keep all runs, a number, after some "
              "age, maybe another useful policy")
    p.idea("Each start block, Manual Start or Schedule, now says how long the runs it starts are kept. In its "
           "Properties, Keep runs offers: As in Settings (the default: the time set in Tools > Settings, 5 years "
           "unless you changed it), All of them, The last N (a number of runs), or For a time (a number of days, "
           "months or years).")
    p.idea("The other useful choice is a tick box under it, Always keep failed runs: a failed run is kept whatever "
           "Keep runs says. The last N are the N newest runs, failed or not; with the tick, a failed run stays once "
           "it is no longer one of them.")
    p.idea("A run counts for the start block that started it, as Run history's Started by says. Running one block "
           "again (Run only this block, Run from this block on) counts for the start blocks whose lines hold that "
           "block. A run is deleted only when every start block it counts for lets it go: if one of them still "
           "keeps it, it stays.")
    p.why_simplest("It is the same clean-up as before, at the same moment: as each run ends, wherever it ran (the "
                   "editor, the scheduler service, the command line), Taskloom deletes the runs to let go. Only the "
                   "rule changed: instead of one time for every run, each run follows the choice of the start blocks "
                   "it counts for.")
    p.why_simplest("The choice is saved in the flow file, with the start block, like its When and Active. As each run "
                   "starts, Taskloom notes the start blocks it counts for, and how each of the flow's start blocks "
                   "keeps its runs right then. The latest choices decide for all of a start block's runs, so a change "
                   "applies to its earlier runs too, from when the flow's next run ends.")
    p.alternative("One choice for the whole flow",
                  "A flow can have several lines with very different rhythms: a check every minute next to a report "
                  "you run by hand once a month. Keeping the flow's last 100 runs would push the reports out within "
                  "two hours. Each start block is one line, with one rhythm, so that is where the choice belongs.")
    p.alternative("A fifth choice, “Only the failed runs”",
                  "It would replace the other choices instead of going with them. As a tick box, it adds to any of "
                  "them: keep the last 100 runs, and every failure, however old, to go back to.")
    p.alternative("Reading each flow file when cleaning up",
                  "As any run ends, the clean-up goes through the runs of all your flows: it would have to open every "
                  "flow file that has runs, each time. And the runs of a flow file you moved, renamed or deleted "
                  "would have no choice left to follow. Noted as each run starts, the choices stay with the runs.")
    p.alternative("Deleting a run as soon as one of its start blocks lets it go",
                  "Deleting cannot be undone. When two start blocks share a run (a block run again that is on both "
                  "lines, or Run now starting two Manual Starts), the one that keeps it longer wins.")
    p.fits("Settings' Keep runs for stays the default: every start block begins at As in Settings, so each flow "
           "keeps its runs exactly as before until you choose otherwise. Settings also still decides for the runs "
           "that count for no start block: those of a flow without one, or of a block run again that is on no line.")
    p.fits("A deleted run goes as it did: all of it at once, its line in Run history, its messages and its files. "
           "A run still going is never deleted, nor counted among the last N, and a block run again on an older "
           "run's tables keeps its own copy of them.")
    p.fits("Each flow's start blocks count on their own: a start block with the same name in another flow never "
           "deletes this flow's runs.")
    down = tmp_path / "order-server-down"
    flow = write_flow(tmp_path / "orders.yaml", FLOW.replace("DOWN", down.as_posix()))
    home = win.home

    p.step("Open the flow orders.yaml (File > Open…). It has two lines: ① the Manual Start by_hand runs "
           "month_end_report, a report you make by hand now and then; ② the Schedule every_minute runs "
           "check_orders, a check of the order server, every minute.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu(win, "Open…")
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    qtbot.waitUntil(lambda: tab.scene.nodes["every_minute"].countdown != "", timeout=10_000)  # what it says under it
    p.shot(win, "The flow, with its two lines ① and ②.", highlight=[MANUAL_LINE, SCHEDULE_LINE], badges=True,
           zoom=[MANUAL_LINE, SCHEDULE_LINE])

    p.step("Click by_hand. Properties, on the right, shows its settings.")
    click_block(win, "by_hand")
    box = keep_box(win)
    p.shot(win, "① by_hand has a new box, Keep runs: As in Settings, and Always keep failed runs unticked. That is "
                "where every start block starts: its runs are kept as Tools > Settings says, as before.",
           highlight=[box], badges=True, zoom=[box])
    p.check("① by_hand's Keep runs says As in Settings, with Always keep failed runs unticked",
            keep_list(win).currentText() == "As in Settings" and not failed_box(win).isChecked())

    p.step("Click the Keep runs list.")
    offered = []

    def look_at_choices(popup):
        offered.extend(items(keep_list(win)))
        p.shot(popup, "The four choices: As in Settings, All of them, The last N, For a time.", over=win,
               highlight=[popup], zoom=[popup, keep_box(win)])
        pick(popup, "For a time")

    p.while_open(lambda: open_list(keep_list(win)), look_at_choices)
    p.check("The list offers As in Settings, All of them, The last N and For a time", offered == CHOICES)

    p.step("Choose For a time, to see it, and click its list of units.")
    QApplication.processEvents()
    count = keep_box(win).findChild(QSpinBox)
    unit = next(c for c in keep_box(win).findChildren(QComboBox) if c is not keep_list(win))
    units = items(unit)

    def look_at_units(popup):
        p.shot(popup, "For a time keeps the runs for a number of days, months or years, here 90 days to begin "
                      "with. It is by_hand's own time: it can be longer or shorter than Settings' one.", over=win,
               highlight=[keep_box(win)], zoom=[popup, keep_box(win)])

    p.while_open(lambda: open_list(unit), look_at_units)
    p.check("For a time shows a number, 90, and a unit: days, months or years",
            count.value() == 90 and unit.currentText() == "days" and units == ["days", "months", "years"])

    p.step("Choose All of them in the Keep runs list instead.")
    p.note("A proof that lasts a minute has no old runs: it cannot show a time letting runs go, nor All of them "
           "keeping a run older than Settings' time. Taskloom's own tests check both, with runs made to look older.")
    p.while_open(lambda: open_list(keep_list(win)), lambda popup: pick(popup, "All of them"))
    box = keep_box(win)
    p.shot(win, "① by_hand keeps all of its runs: none is ever deleted, however old, whatever Settings says.",
           highlight=[box], badges=True, zoom=[box])
    p.check("① by_hand's Keep runs says All of them", keep_list(win).currentText() == "All of them")

    p.step("Click every_minute. In Keep runs, choose The last N, type 2 in Runs and press Enter, and tick Always "
           "keep failed runs.")
    click_block(win, "every_minute")
    p.while_open(lambda: open_list(keep_list(win)), lambda popup: pick(popup, "The last N"))
    runs_box = keep_box(win).findChild(QSpinBox)
    runs_box.selectAll()
    QTest.keyClicks(runs_box, "2")
    QTest.keyClick(runs_box, Qt.Key_Return)
    click_tick(failed_box(win))
    box = keep_box(win)
    when = next(b for b in win.properties.widget().findChildren(QGroupBox) if b.title() == "When")
    p.shot(win, "every_minute, a Schedule block (① its When: every minute), has the same Keep runs box: ② it keeps "
                "its last 2 runs, and always its failed runs.",
           highlight=[when, box], badges=True, zoom=[when, box])
    p.check("② every_minute keeps the last 2 runs, and always its failed runs",
            (keep_list(win).currentText(), keep_box(win).findChild(QSpinBox).value(), failed_box(win).isChecked())
            == ("The last N", 2, True))

    p.step("Save the flow (File > Save).")
    file_menu(win, "Save")
    saved = yaml.safe_load(flow.read_text(encoding="utf-8"))["blocks"]
    p.check("Saved: by_hand keeps all of its runs, every_minute its last 2 and its failed ones",
            (saved["by_hand"].get("config") or {}).get("keep") == "all"
            and saved["every_minute"]["config"].get("keep") == "last 2"
            and saved["every_minute"]["config"].get("keep_failed") is True)

    p.step(f"Start the two lines {len(ORDER)} times, in this order: "
           + ", ".join("①" if line == "M" else "②" for line in ORDER) + f". These are runs 1 to {len(ORDER)}: line "
           "① is started with ▶ Run now in the toolbar, line ② by right-clicking every_minute and choosing “Run the "
           "flow now, as if its time had come”.")
    p.note("No scheduler service runs during this proof, so line ② is started from its block's menu, as you would "
           "to try it out. A run the scheduler service starts at its time is kept in the same way.")
    p.note(f"There is no order server here: check_orders fails while a file named order-server-down is in the "
           f"flow's folder. The proof puts it there before run {FAILS} and takes it away after, so that run "
           f"{FAILS} fails as a real check would while its server is down.")
    run_now = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action)
    for n, line in enumerate(ORDER, 1):
        if n == FAILS:
            down.write_text("down", encoding="utf-8")
        if line == "M":
            QTest.mouseClick(run_now, Qt.LeftButton)
        else:
            start_schedule_line(p, tab)
        qtbot.waitUntil(lambda: len(tab.runs) == n and not tab.running and tab.runs[-1].status is not None,
                        timeout=60_000)
        if n == FAILS:
            down.unlink()
            p.shot(win, f"Run {FAILS}, of line ②, failed: check_orders is red, and its error under it begins "
                        "“RuntimeError: the order”.", highlight=[SCHEDULE_LINE],
                   zoom=[MANUAL_LINE, SCHEDULE_LINE])
            p.check(f"Run {FAILS} failed: check_orders shows its failure",
                    tab.runs[-1].status == "failed" and tab.scene.nodes["check_orders"].status == "failed")
    ran = [(r.run_id, r.status) for r in tab.runs]
    p.check(f"The {len(ORDER)} runs ran, numbered 1 to {len(ORDER)}; run {FAILS} failed, the others succeeded",
            ran == [(n, "failed" if n == FAILS else "success") for n in range(1, len(ORDER) + 1)])

    p.step("Click the Run history tab, under the canvas, and drag the edge above it up a little, to see more runs.")
    click_tab(win.bottom_tabs, win.history_panel)
    drag_up(win, win.output, 120)
    table = win.history_panel.table
    rows = history_rows(win)
    assert all_shown(table), "Run history does not show all its runs: drag its edge higher"
    p.shot(win, "Each line kept its runs its own way. ① by_hand kept all 3 of its runs: 8, 5 and 2. "
                "② every_minute kept its last 2 runs, 7 and 6, and run 3, which failed. Its runs 1 and 4 succeeded "
                "and are no longer among its last 2: they are deleted. Had by_hand also kept only its last 2, its "
                "run 2 would be gone too.",
           highlight=history_lines(table), badges=[1 if by == "by_hand" else 2 for _, by, _ in rows],
           zoom=[table.item(0, 0), table.item(table.rowCount() - 1, 4)])
    p.check("① by_hand's runs 8, 5 and 2 are all kept",
            [r for r in rows if r[1] == "by_hand"] == [(str(n), "by_hand", "success") for n in (8, 5, 2)])
    p.check("② every_minute kept its last 2 runs, 7 and 6, and its failed run 3; its runs 1 and 4 are deleted",
            [r for r in rows if r[1] == "every_minute"]
            == [("7", "every_minute", "success"), ("6", "every_minute", "success"), ("3", "every_minute", "failed")])
    p.check("The files of runs 1 and 4 are deleted with them; the kept runs keep theirs",
            run_folders(home) == [2, 3, 5, 6, 7, 8])

    p.step("Untick every_minute's Always keep failed runs.")
    click_tick(failed_box(win))
    box = keep_box(win)
    p.shot(win, "① Always keep failed runs is unticked. Nothing is deleted yet: a change counts from when the flow's "
                "next run ends, so Run history, below, still lists the same six runs.",
           highlight=[box], badges=True, zoom=[box])
    p.check("① Always keep failed runs is unticked", not failed_box(win).isChecked())
    p.check("Nothing is deleted yet: Run history still lists runs 8, 7, 6, 5, 3 and 2",
            [r[0] for r in history_rows(win)] == ["8", "7", "6", "5", "3", "2"]
            and run_folders(home) == [2, 3, 5, 6, 7, 8])

    p.step(f"Start line ② once more, from every_minute's menu: run {len(ORDER) + 1}. Then click Run history again "
           "(a run brings the Log to the front).")
    start_schedule_line(p, tab)
    qtbot.waitUntil(lambda: len(tab.runs) == len(ORDER) + 1 and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)
    click_tab(win.bottom_tabs, win.history_panel)
    rows = history_rows(win)
    assert all_shown(table), "Run history does not show all its runs"
    p.shot(win, f"As run {len(ORDER) + 1} ended, Taskloom deleted run 6, no longer one of every_minute's last 2, "
                "and run 3, the failed one: failed runs are no longer always kept. ② every_minute keeps its last 2 "
                f"runs, {len(ORDER) + 1} and 7. ① by_hand's runs 8, 5 and 2 are still there.",
           highlight=history_lines(table), badges=[1 if by == "by_hand" else 2 for _, by, _ in rows],
           zoom=[table.item(0, 0), table.item(table.rowCount() - 1, 4)])
    p.check(f"Run history: every_minute's runs {len(ORDER) + 1} and 7, and by_hand's 8, 5 and 2; runs 6 and 3 "
            "are deleted, with their files",
            [(r[0], r[1]) for r in rows] == [(str(len(ORDER) + 1), "every_minute"), ("8", "by_hand"),
                                             ("7", "every_minute"), ("5", "by_hand"), ("2", "by_hand")]
            and run_folders(home) == [2, 5, 7, 8, len(ORDER) + 1])
