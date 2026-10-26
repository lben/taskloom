"""Proof: scheduled runs begin at their time (batch 1), with the real scheduler service running: see README.md here."""

import datetime as dt

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QFileDialog, QGroupBox, QLabel, QStyle, QStyleOptionButton, QToolBar

from tests.test_scheduler_start_at import (SLOW_IMPORT, record_process_starts, run_processes, runs, start_service,
                                           stop_service, wait_until, wait_until_finished, write)

LEAD = dt.timedelta(seconds=30)  # at least this long from opening the flows to their time: room to set everything up
ONE = dt.timedelta(seconds=1.5)  # a countdown shows whole seconds, and moves on once a second
REPORT = """\
blocks:
  report_time:
    type: trigger.schedule
    config: {{at: "{at}"}}
    ui: {{x: 0, y: 0}}
  make_report:
    type: logic.python
    config:
      inputs: []
      code: |
        import datetime
        output = "began at " + datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(output)
    ui: {{x: 260, y: 0}}
edges:
  - report_time.start -> make_report.after
"""
CLEAN_UP = """\
blocks:
  cleanup_time:
    type: trigger.schedule
    config: {{at: "{at}"}}
    ui: {{x: 0, y: 0}}
  clean_up:
    type: logic.python
    config: {{inputs: [], code: "output = 'cleaned up'"}}
    ui: {{x: 260, y: 0}}
edges:
  - cleanup_time.start -> clean_up.after
"""


def file_menu(win, item: str):
    menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in menu.actions() if a.text() == item).trigger()


def click_tab(tabs, widget):
    bar = tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(tabs.indexOf(widget)).center())


def click_block(tab, block: str):
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier,
                     tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center()))


def click_empty(tab):
    """Click an empty part of the canvas: nothing is selected then, and the Log shows every block's lines."""
    viewport = tab.view.viewport()
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, QPoint(20, viewport.height() - 20))


def active_box(win) -> QCheckBox:
    """The Active tick box of the Schedule block shown in Properties (made anew whenever the panel is)."""
    return next(c for c in win.properties.findChildren(QCheckBox) if c.toolTip().startswith("Untick it and this"))


def tick(box: QCheckBox) -> QRect:
    """Where a tick box's box is, in it (its row is wider, and a click in the empty part of the row does nothing)."""
    option = QStyleOptionButton()
    box.initStyleOption(option)
    return box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box)


def active_area(win) -> QRect:
    """The label Active and its tick box, in a screenshot of the window."""
    box = active_box(win)
    label = next(w for w in win.properties.findChildren(QLabel) if w.text() == "Active")
    return QRect(label.mapTo(win, QPoint(0, 0)), label.size()) | tick(box).translated(box.mapTo(win, QPoint(0, 0)))


def countdown(tab, block: str) -> str:
    return tab.scene.nodes[block].countdown


def reaches_zero(text: str, read_at: dt.datetime | None = None) -> dt.datetime:
    """When a countdown such as "next run in 00:01:05", read at `read_at` (default: now), reaches 0. Pass when it
    was read if a screenshot came between: a slow computer can take over a second to take one."""
    hours, minutes, seconds = map(int, text.removeprefix("next run in ").split(":"))
    return (read_at or dt.datetime.now()) + dt.timedelta(hours=hours, minutes=minutes, seconds=seconds)


def clock(moment: dt.datetime) -> str:
    return f"{moment:%H:%M:%S.%f}"[:-3]


def test_scheduled_runs_begin_at_their_time(proof, env, win, qtbot, monkeypatch):
    p = proof("Scheduled runs begin at their time",
              "Schedule block countdown still aiming to the PC clock, actual execution 2 seconds later")
    p.idea("A scheduled run now begins on the second its schedule says: when the countdown under the Schedule block "
           "reaches 0, the Log and Run history say the run started at that same second.")
    p.idea("The runs were late because each run is its own program, and the scheduler service started that program "
           "only when the time came. The program then took about 2 seconds to load before the run began, and that "
           "is the time the Log showed.")
    p.idea("Now the service starts the run's program 5 seconds early and tells it the time. The program loads, "
           "checks the flow, then waits. At the time, it checks that the schedule still has that time and that the "
           "service is still on, and begins. If you switched the schedule off in the meantime, or turned the service "
           "off, the run does not begin and nothing is recorded.")
    p.why_simplest("The service gives the run's program its time to begin, and the program does the rest. It already "
                   "loaded and checked the flow before it recorded the run; now it waits for the time in between, on "
                   "the computer's clock (the clock the countdown reads), and checks then that its schedule still has "
                   "that time.")
    p.why_simplest("Why 5 seconds: more than twice the 2 seconds loading took for you, and still short, because the "
                   "service decides whether a run may start (is the flow's previous run still going?) when it starts "
                   "the program.")
    p.alternative("Making the run's program load faster",
                  "It would only make the delay shorter. How long loading takes depends on the computer, on what "
                  "else it is doing and on the blocks the flow uses, so the run would still begin late, by an amount "
                  "that changes.")
    p.alternative("Recording the run as started at its time, whatever the clock says",
                  "The Log would look right, but the blocks would still begin 2 seconds late. That hides the delay "
                  "instead of removing it.")
    p.alternative("Running the flows inside the scheduler service, already loaded",
                  "Every run would share one program: a run that crashed or used up the memory would take the service "
                  "and the other runs down with it. Each run in its own program is how Taskloom runs everything, also "
                  "from the editor and from a terminal.")
    p.fits("A scheduled run is still the same program as a run from the editor or from a terminal (taskloom run): "
           "the service starts it earlier and gives it its time.")
    p.fits("The ways to stop a schedule still work up to the last second: untick Active, change the time, delete the "
           "block, or turn the scheduler service off. The waiting program checks them at the time.")
    p.fits("The setting “If a scheduled run starts while the last one is still going” now applies when the program "
           "starts, 5 seconds early. With “skip the new run”, the usual setting, a previous run that ends in the last "
           "5 seconds before the next time gets that next run skipped: the price of starting early.")

    write(env.home / "blocks" / "slow_import.py", SLOW_IMPORT)
    due = (dt.datetime.now() + LEAD).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    report_file = write(env.root / "daily-report.yaml", REPORT.format(at=f"{due:%Y-%m-%d %H:%M}"))
    clean_file = write(env.root / "clean-up.yaml", CLEAN_UP.format(at=f"{due:%Y-%m-%d %H:%M}"))
    starts = record_process_starts(env)  # when the service starts each run's program (not on screen)

    p.step(f"Open daily-report.yaml and clean-up.yaml (File > Open…) and save each (File > Save). Each has a Schedule "
           f"block set to run once, at {due:%H:%M} today. Saved, they are scheduled.")
    p.note("A one-time schedule, at a whole minute as the Properties panel sets it. You have since asked that “every "
           "few minutes” count from when you switch the schedule on, not from the start of the hour: that comes in "
           "the next batch. This change is the same for every kind of schedule: a run begins at the time its "
           "schedule gives.")
    tabs = []
    for path in (report_file, clean_file):
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, _path=path, **k: (str(_path), ""))
        file_menu(win, "Open…")
        tabs.append(win.current)
        file_menu(win, "Save")
    report, clean = tabs
    p.check("Both flows are scheduled", report.scheduled and clean.scheduled)

    p.step("Turn the scheduler service on: here, from a command line, with taskloom scheduler --headless.")
    p.note("The Scheduler service button starts the same service, with an icon in the tray. It is started from a "
           "command line here so that the proof can stop it again at the end.")
    service = start_service(env, tray=False)
    try:
        click_tab(win.tabs, report)
        click_block(report, "report_time")
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on"
                        and countdown(report, "report_time").startswith("next run in"), timeout=20_000)
        button = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.scheduler_action)
        when_box = next(b for b in win.properties.findChildren(QGroupBox) if b.title() == "When")
        p.shot(win, f"① report_time counts down to {due:%H:%M}:00: “{countdown(report, 'report_time')}”. "
                    f"② Properties: it runs once, at {due:%H:%M} today. ③ The scheduler service is on.",
               highlight=["report_time", when_box, button], badges=True, zoom=[when_box, button])
        p.check(f"The countdown aims at {due:%H:%M}:00",
                abs(reaches_zero(countdown(report, "report_time")) - due) <= ONE)

        p.step("In the last 5 seconds before that time, switch off clean-up's schedule: in clean-up.yaml, select "
               "cleanup_time, untick Active and save.")
        early = run_processes(starts, {"report": report.doc.path, "clean": clean.doc.path}, due, until=due)
        click_tab(win.tabs, clean)
        click_block(clean, "cleanup_time")
        box = active_box(win)
        QTest.mouseClick(box, Qt.LeftButton, Qt.NoModifier, tick(box).center())
        file_menu(win, "Save")
        switched_off = dt.datetime.now()
        qtbot.waitUntil(lambda: active_box(win).isVisible(), timeout=5_000)  # Properties, laid out again
        p.shot(win, "cleanup_time is switched off and saved: ① it says “not active”, ② Active is unticked. The "
                    "service had already started its run's program, to begin at the time.",
               highlight=["cleanup_time", active_area(win)], badges=True, zoom=["cleanup_time"])
        p.check("The service had already started the program of both runs when clean-up was switched off",
                early.keys() == {"report", "clean"} and max(early.values()) < switched_off.timestamp())
        p.check("clean-up was switched off before its time: it says “not active” and is no longer scheduled",
                switched_off < due - dt.timedelta(seconds=0.5) and not clean.scheduled
                and countdown(clean, "cleanup_time") == "not active")

        p.step("Go back to daily-report.yaml and watch the countdown.")
        click_tab(win.tabs, report)
        click_empty(report)
        qtbot.waitUntil(lambda: countdown(report, "report_time") in ("next run in 00:00:02", "next run in 00:00:01"),
                        timeout=10_000)
        just_before, looked = countdown(report, "report_time"), dt.datetime.now()
        begun = [r for r in runs(env) if r["flow"] == "daily-report"]
        p.shot(win, f"Just before {due:%H:%M}:00: ① “{just_before}”. ② Nothing has begun yet: no flow is running. "
                    "Yet the service has already started this run's program (the times are listed at step 6): it is "
                    "loaded, and waits for its time.",
               highlight=["report_time", win.running_flows], badges=True, zoom=["report_time"])
        p.check(f"Just before its time the countdown said {just_before[-8:]}, and no run had begun",
                begun == [] and looked < due)
        ahead = {name: due.timestamp() - started for name, started in early.items()}
        p.check("The service started each run's program at least a second before its time",
                all(seconds >= 1.0 for seconds in ahead.values()))

        p.step(f"Wait until {due:%H:%M}:00 has passed.")
        p.note("To show the case you hit, the Taskloom folder here has a block file that takes a second to load, so "
               "the run's program is slow to get ready, as the packaged Taskloom was on your computer.")
        qtbot.waitUntil(lambda: any(r["flow"] == "daily-report" and r["status"] != "running" for r in runs(env)),
                        timeout=40_000)
        (run,) = [r for r in runs(env) if r["flow"] == "daily-report"]
        run_id = run["id"]
        qtbot.waitUntil(lambda: f"run #{run_id} success" in win.log.text.toPlainText(), timeout=10_000)
        began = dt.datetime.fromisoformat(env.query("SELECT started FROM blocks WHERE run_id=? AND block=?",
                                                    run_id, "make_report")[0][0])
        log = win.log.text
        p.shot(win, f"The run began at {due:%H:%M}:00. The Log, under the canvas, says it started at {due:%H:%M:%S}, "
                    f"the second the countdown reached 0, and make_report wrote down when it began: "
                    f"{report.scene.values['make_report']['output'].removeprefix('began at ')}.",
               zoom=[(log, f"run #{run_id} "), (log, "[make_report]")])
        p.check(f"The Log says run #{run_id} started at {due:%H:%M:%S}",
                f"{due:%H:%M:%S} INFO run #{run_id} · {due:%Y-%m-%d %H:%M:%S} started" in log.toPlainText())
        p.check("The run was recorded as started within half a second after its time",
                -0.02 <= (run["started"] - due).total_seconds() <= 0.5)
        p.check("make_report began within a second after the time", 0 <= (began - due).total_seconds() <= 1.0)

        p.step("Click the Run history tab, under the canvas.")
        click_tab(win.bottom_tabs, win.history_panel)
        table = win.history_panel.table
        row = [table.item(0, c).text() for c in range(table.columnCount())]
        p.shot(win, f"Run #{run_id} started at {due:%Y-%m-%d %H:%M:%S} (outlined), started by report_time, and "
                    "succeeded.",
               highlight=[table.item(0, 1)], zoom=[table.item(0, 0), table.item(0, 4)])
        p.check(f"Run history: one run, started at {due:%H:%M:%S} by report_time, success",
                table.rowCount() == 1 and row[1:4] == [f"{due:%Y-%m-%d %H:%M:%S}", "report_time", "success"])
        p.text("The times, to the millisecond (when the service started the programs is not on screen: the editor "
               "shows a run only once it begins)",
               "\n".join(f"{what:<34}{clock(when)}{note}" for what, when, note in [
                   ("Due time", due, ""),
                   ("daily-report: its program started", dt.datetime.fromtimestamp(early["report"]),
                    f"   {ahead['report']:.1f} s before"),
                   ("clean-up: its program started", dt.datetime.fromtimestamp(early["clean"]),
                    f"   {ahead['clean']:.1f} s before"),
                   ("clean-up switched off and saved", switched_off, ""),
                   (f"daily-report: run #{run_id} started", run["started"], ""),
                   ("daily-report: make_report began", began, "")]))

        p.step("Switch to clean-up.yaml.")
        click_tab(win.tabs, clean)
        wait_until(due + dt.timedelta(seconds=2))  # its program has checked at the time, and ended
        p.shot(win, "clean-up did not run: ① its blocks show no result, and ② its Run history is empty. Its program, "
                    "already waiting, saw at the time that the schedule was off, and ended without beginning a run.",
               highlight=[["cleanup_time", "clean_up"], win.history_panel.table], badges=True)
        p.check("clean-up has no run", [r for r in runs(env) if r["flow"] == "clean-up"] == []
                and win.history_panel.table.rowCount() == 0)
    finally:
        stop_service(env, service)
        if any(starts.iterdir()):  # a run's program started early ends by itself at its time
            wait_until(due + dt.timedelta(seconds=3))
        wait_until_finished(env)
