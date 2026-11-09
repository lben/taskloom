"""Proof: turning the scheduler service off with runs going asks you, flow by flow (SCH-4). How proofs are written:
README.md here."""

import datetime as dt
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QFileDialog, QGroupBox, QRadioButton

from proofs.test_proof_scheduler_misfire import (Service, editor_shows, history_area, history_outline, history_rows,
                                                 service_button)
from proofs.test_proof_scheduler_start import click_tab, clock, countdown, file_menu
from taskloom.scheduler import paused, service_running
from tests.test_scheduler_new_promises import clock_shows
from tests.test_scheduler_start_at import runs, write

ASKED = ("instead it should present me a dialog box showing me which schedules are running and giving me the option "
         "to keep until they finish or to be closed when I click on \"Proceed\"")
STOPPED, FINISHED = "import_orders", "build_report"  # the flow you choose Stop now for, and the one you let finish
FLOW = """\
params:
  when: {{type: datetime, expr: "now()"}}
overlap: queue
blocks:
  every_minute:
    type: trigger.schedule
    config: {{cron: "* * * * *"}}
    ui: {{x: 0, y: 0}}
  long_work:
    type: logic.python
    config:
      inputs: []
      code: |
        import time
        from pathlib import Path
        while not Path("{name}.go").exists():  # a long piece of work: it ends when the file {name}.go is there
            time.sleep(0.05)
        output = "the run for " + params["when"].strftime("%H:%M")
        print(output)
    ui: {{x: 260, y: 0}}
edges:
  - every_minute.start -> long_work.after
"""


@pytest.fixture
def service(win, qtbot):
    s = Service(win.home, qtbot)
    try:
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=10_000)
        yield s
    finally:
        s.stop()


def check_and_end_when_off(service, moment: dt.datetime) -> bool:
    """One check at `moment`; then, if it is off (you turned it off, and what you let finish is done), the service
    ends as `taskloom scheduler` does: it lets go of its lock. Whether it ended."""
    service.check(moment)
    if service.scheduler.off:
        service.lock.close()
    return service.scheduler.off


def ran(env, flow: str) -> list[tuple[str, str]]:
    """The flow's runs, oldest first: (the time each is for, its status)."""
    return [(f"{r['when']:%H:%M}", r["status"]) for r in runs(env) if r["flow"] == flow]


def waiting(env) -> list[tuple[str, str]]:
    """The runs waiting their turn, as the service saved them: (flow, the time each is for)."""
    return [(Path(path).stem, f"{dt.datetime.fromisoformat(due).astimezone():%H:%M}")
            for path, due in env.query("SELECT flow, due FROM waiting_runs ORDER BY id")]


def status_message_area(win) -> QRect:
    """The message the status bar shows, in the window's pixels."""
    bar = win.statusBar()
    width = bar.fontMetrics().horizontalAdvance(bar.currentMessage()) + 16
    return QRect(bar.mapTo(win, QPoint(0, 0)), QSize(width, bar.height()))


def dialog_parts(dialog):
    """The turn-off window's row for each flow, the choice `text` in a flow's row, and its buttons."""
    rows = {g.title(): g for g in dialog.findChildren(QGroupBox)}
    buttons = dialog.findChild(QDialogButtonBox)

    def choice(flow: str, text: str) -> QRadioButton:
        return next(r for r in rows[flow].findChildren(QRadioButton) if r.text() == text)

    return rows, choice, {b.text().replace("&", ""): b for b in buttons.buttons()}


def taken_in(env, tab) -> bool:
    """The editor has taken in the flow's runs: the latest that ended, and the one going, if one is."""
    going = [r["id"] for r in runs(env) if r["flow"] == tab.doc.path.stem and r["status"] == "running"]
    return editor_shows(env, tab) and set(going) == tab.going


def test_turning_the_service_off_with_runs_going_asks_flow_by_flow(proof, env, win, qtbot, monkeypatch, service):
    p = proof("Turning the scheduler service off with runs going: you choose for each flow", ASKED)
    p.idea("When you turn the scheduler service off while scheduled runs are going, a window lists each flow with "
           "runs going. For each one you choose Let it finish (the default) or Stop now, then click Proceed. Cancel "
           "keeps the service on, and changes nothing.")
    p.idea("Let it finish: its runs going finish, and its runs waiting their turn still run. Stop now: its runs going "
           "stop and are recorded as cancelled, and its waiting runs are dropped. Once you click Proceed, nothing new "
           "starts, and the service turns off when the last run you let finish is done.")
    p.why_simplest("One window, with one row per flow and two choices in each. Your choices are written where the "
                   "service already looks to see whether you turned it off, so it applies them at its next check "
                   "(it checks every second), also if you close the editor meanwhile.")
    p.alternative("A plain Yes or No, as before (“Runs already going continue”)",
                  "You could not see which flows had runs going, nor stop one that should not go on.")
    p.alternative("One choice for all the flows: stop them all, or let them all finish",
                  "You asked to choose which to keep until they finish: a report nearly done is worth waiting for, "
                  "while an import started at the wrong time is not.")
    p.fits("The tray icon's Turn off the scheduler service shows the same window. A run stopped here is recorded as "
           "cancelled in Run history, as a run you stop with the editor's Stop is.")
    p.fits("Flows not in the list keep their waiting runs: they are saved, and start when you turn the service on "
           "again. With no runs going, you are only asked to confirm.")
    day = dt.date.today() - dt.timedelta(days=1)

    def at(text: str) -> dt.datetime:
        return dt.datetime.combine(day, dt.time.fromisoformat(text))

    paths = {name: write(env.root / f"{name}.yaml", FLOW.format(name=name)) for name in (STOPPED, FINISHED)}
    tabs = {}

    def show_history(flow: str, count: int, quiet=False):
        """Once the editor has taken in the flow's latest run (it looks every 2 seconds, and shows a new run in the
        Log), click the flow's tab and the Run history tab, and wait for its `count` runs there. `quiet`: and for the
        status bar to show No flows running or 1 flow running again (a message hides it a few seconds)."""
        qtbot.waitUntil(lambda: taken_in(env, tabs[flow]), timeout=20_000)
        click_tab(win.tabs, tabs[flow])
        click_tab(win.bottom_tabs, win.history_panel)
        latest = [r for r in runs(env) if r["flow"] == flow][-1]
        qtbot.waitUntil(lambda: len(history_rows(win)) == count and history_rows(win)[0][3] == latest["status"]
                        and (not quiet or not win.statusBar().currentMessage()), timeout=20_000)
        assert win.bottom_tabs.currentWidget() is win.history_panel

    p.step(f"Open {STOPPED}.yaml and {FINISHED}.yaml (File > Open…) and save each (File > Save). Each runs every "
           "minute. Its long_work stands for a long piece of work: it goes on until a file named after its flow, "
           f"{STOPPED}.go or {FINISHED}.go, is next to it; the proof creates that file when the run is to end. Each "
           "flow's “If a scheduled run starts while the last one is still going” is “run it after the current one”.")
    for name, path in paths.items():
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, _path=path, **k: (str(_path), ""))
        file_menu(win, "Open…")
        tabs[name] = win.current
        with clock_shows(at("06:30:00")):  # (saved as the service's clock has it: as its checks below begin)
            file_menu(win, "Save")
    p.check("Both flows are scheduled, each with “run it after the current one”",
            all(tab.scheduled and tab.doc.data.get("overlap") == "queue" for tab in tabs.values()))

    p.step(f"Let the scheduler service check at 06:31:00 and at 06:32:00 yesterday ({day:%Y-%m-%d}). At 06:31:00 "
           "each flow's 06:31 run starts, and keeps going. At 06:32:00 each flow's 06:32 time waits its turn behind "
           "it.")
    p.note("Here the scheduler service's own checks run inside the proof, at times it chooses, yesterday morning: a "
           "proof cannot wait for minutes between them. The runs it starts are real. The flows are saved at 06:30:00 "
           "yesterday for the service, so their first time is 06:31. Run history's Started is when the proof ran "
           "them; when, under Parameters, is the time each run is for.")
    service.check(at("06:31:00"))
    qtbot.waitUntil(lambda: win.running_flows.text() == "2 flows running", timeout=60_000)
    service.check(at("06:32:00"))
    p.shot(win, "① 2 flows running: the 06:31 run of each. ② The scheduler service is on: you click this button to "
                "turn it off.", highlight=[win.running_flows, service_button(win)], badges=True,
           zoom=[win.running_flows])
    p.check("Each flow's 06:31 run is going", ran(env, STOPPED) == ran(env, FINISHED) == [("06:31", "running")])
    p.check("Each flow's 06:32 run waits its turn", waiting(env) == [(STOPPED, "06:32"), (FINISHED, "06:32")])
    p.note("The editor shows a run once it has begun, not while it waits its turn, as the 06:32 runs do here.")

    p.step("Click Scheduler service on, at the top right. A window, Turn off the scheduler service?, opens: click "
           "Cancel.")
    seen = {}

    def look_and_cancel(dialog):
        rows, choice, buttons = dialog_parts(dialog)
        seen.update(title=dialog.windowTitle(), flows=sorted(rows),
                    chosen={flow: [r.text() for r in g.findChildren(QRadioButton) if r.isChecked()]
                            for flow, g in rows.items()},
                    choices={flow: [r.text() for r in g.findChildren(QRadioButton)] for flow, g in rows.items()},
                    buttons=sorted(buttons))
        p.shot(dialog, "① The flows with runs going, each with ② Let it finish chosen (the default) or Stop now. "
                       "③ Cancel.", highlight=[list(rows.values()), [choice(STOPPED, "Let it finish"),
                                                                     choice(FINISHED, "Let it finish")],
                                               buttons["Cancel"]], badges=True)
        QTest.mouseClick(buttons["Cancel"], Qt.LeftButton)

    p.while_open(lambda: QTest.mouseClick(service_button(win), Qt.LeftButton), look_and_cancel)
    p.check("The window is Turn off the scheduler service?, with a row for each flow with runs going, and Proceed "
            "and Cancel", seen.get("title") == "Turn off the scheduler service?"
            and seen.get("flows") == sorted([STOPPED, FINISHED]) and seen.get("buttons") == ["Cancel", "Proceed"])
    p.check("Each row offers Let it finish and Stop now, with Let it finish chosen",
            seen["choices"] == {f: ["Let it finish", "Stop now"] for f in (STOPPED, FINISHED)}
            and seen["chosen"] == {f: ["Let it finish"] for f in (STOPPED, FINISHED)})
    qtbot.wait(2500)  # (the editor looks again every 2 seconds)
    p.shot(win, "After Cancel: ① the scheduler service is still on, and ② both runs still go.",
           highlight=[service_button(win), win.running_flows], badges=True, zoom=[win.running_flows])
    p.check("Cancel kept it on: not turned off, both runs going, both waiting runs saved",
            win.scheduler_action.text() == "Scheduler service on" and not paused(win.home)
            and service_running(win.home) and ran(env, STOPPED) == ran(env, FINISHED) == [("06:31", "running")]
            and waiting(env) == [(STOPPED, "06:32"), (FINISHED, "06:32")])

    p.step(f"Click Scheduler service on again. In the window, click Stop now under {STOPPED}, leave Let it finish "
           f"under {FINISHED}, and click Proceed.")

    def choose_and_proceed(dialog):
        rows, choice, buttons = dialog_parts(dialog)
        QTest.mouseClick(choice(STOPPED, "Stop now"), Qt.LeftButton)
        p.shot(dialog, f"① Stop now, for {STOPPED}. ② Let it finish, for {FINISHED}. ③ Proceed.",
               highlight=[choice(STOPPED, "Stop now"), choice(FINISHED, "Let it finish"), buttons["Proceed"]],
               badges=True)
        seen["stop"] = choice(STOPPED, "Stop now").isChecked() and choice(FINISHED, "Let it finish").isChecked()
        QTest.mouseClick(buttons["Proceed"], Qt.LeftButton)

    p.while_open(lambda: QTest.mouseClick(service_button(win), Qt.LeftButton), choose_and_proceed)
    p.check(f"Stop now is chosen for {STOPPED}, Let it finish for {FINISHED}", seen["stop"])
    message = win.statusBar().currentMessage()
    qtbot.waitUntil(lambda: countdown(win.current, "every_minute") == "scheduler service off", timeout=5_000)
    said = status_message_area(win)
    p.shot(win, "① The editor says the service is turning off, and ends once the runs you let finish are done. "
                "② The button says off from now on, and ③ under every_minute: nothing new starts.",
           highlight=[said, service_button(win), "every_minute"], badges=True, zoom=[said])
    p.check("The editor says “Scheduler service turning off: it ends once the runs you let finish are done”",
            message == "Scheduler service turning off: it ends once the runs you let finish are done")

    p.step(f"Let the scheduler service check at 06:33:00, a new time for both flows. Then click the {STOPPED} tab, "
           "and the Run history tab, under the canvas.")
    service.check(at("06:33:00"))
    qtbot.waitUntil(lambda: all(r.proc.poll() is not None for r in service.scheduler.runs
                                if r.flow == str(paths[STOPPED])), timeout=30_000)
    show_history(STOPPED, 1)
    p.shot(win, f"① {STOPPED}'s 06:31 run stopped: cancelled. Its 06:32 run, which waited its turn, never ran, nor "
                "did its 06:33 time.", highlight=[history_outline(win)], badges=True,
           zoom=[history_area(win, header=True)])
    p.check(f"{STOPPED}'s 06:31 run was stopped and recorded as cancelled",
            ran(env, STOPPED) == [("06:31", "cancelled")])
    p.check(f"{STOPPED}'s waiting run was dropped; {FINISHED}'s still waits",
            waiting(env) == [(FINISHED, "06:32")])
    p.check("Nothing new started at 06:33", ran(env, FINISHED) == [("06:31", "running")]
            and [r for r in runs(env) if r["when"] == at("06:33:00")] == [])

    p.step(f"Let {FINISHED}'s 06:31 run end: create {FINISHED}.go, and delete it once the run has ended. Let the "
           f"scheduler service check at 06:33:15 and at 06:33:20. Then click the {FINISHED} tab, and the Run "
           "history tab.")
    go = env.root / f"{FINISHED}.go"
    go.write_text("go")
    service.wait_for_its_runs_to_end()
    go.unlink()
    check_and_end_when_off(service, at("06:33:15"))
    qtbot.waitUntil(lambda: ran(env, FINISHED) == [("06:31", "success"), ("06:32", "running")], timeout=60_000)
    still_on = not check_and_end_when_off(service, at("06:33:20"))
    show_history(FINISHED, 2, quiet=True)
    table = win.history_panel.table
    p.shot(win, f"① {FINISHED}'s 06:31 run finished. ② Its 06:32 run, which waited its turn, is running now: you let "
                "the flow finish. ③ 1 flow running.", highlight=[(table, 1), (table, 0), win.running_flows],
           badges=True, zoom=[history_area(win, header=True)])
    p.check(f"{FINISHED}'s 06:31 run finished, and then its 06:32 run began",
            ran(env, FINISHED) == [("06:31", "success"), ("06:32", "running")])
    p.check("While it goes, the service is still on: it has not turned off", still_on and service_running(win.home))
    p.note("The button says the service is off from Proceed on, also while the runs you let finish go: no new time "
           "starts.")

    p.step(f"Let the 06:32 run end: create {FINISHED}.go again. Let the scheduler service check at 06:33:30. Then "
           "click the Run history tab.")
    go.write_text("go")
    service.wait_for_its_runs_to_end()
    off = check_and_end_when_off(service, at("06:33:30"))
    ended = dt.datetime.now()
    show_history(FINISHED, 2, quiet=True)
    p.shot(win, f"① Both of {FINISHED}'s runs finished. ② No flows running. The scheduler service has turned off.",
           highlight=[history_outline(win), win.running_flows], badges=True, zoom=[history_area(win, header=True)])
    last = [r for r in runs(env) if r["flow"] == FINISHED][-1]
    p.text("When the last run you let finish ended, and when the scheduler service turned off",
           f"{FINISHED}'s 06:32 run finished    {clock(last['finished'])}\n"
           f"the scheduler service turned off  {clock(ended)}")
    p.note("The scheduler service's tray icon is not on these screenshots: offscreen, there is no tray. So the proof "
           "shows when the service let go of the Taskloom folder, which a running service holds.")
    p.check("Once that run was done, the service turned off", off and not service_running(win.home)
            and ended >= last["finished"] and win.running_flows.text() == "No flows running")
    p.check(f"All that ran: {STOPPED}'s 06:31 run, cancelled, and {FINISHED}'s 06:31 and 06:32 runs",
            ran(env, STOPPED) == [("06:31", "cancelled")]
            and ran(env, FINISHED) == [("06:31", "success"), ("06:32", "success")] and waiting(env) == [])
