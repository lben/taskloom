"""Proof: when a Catch up schedule missed more than 100 times, the scheduler service asks first (SCH-9). How proofs are
written: README.md here."""

import datetime as dt

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QRadioButton, QSpinBox

from proofs.test_proof_scheduler_misfire import (Service, editor_shows, finished, history_area, history_outline,
                                                 misfire_row, service_button)
from proofs.test_proof_scheduler_start import click_block, click_tab, countdown, file_menu
from proofs.test_proof_scheduler_turn_off import status_message_area
from taskloom.editor.dialogs import CatchUpDialog
from taskloom.scheduler import set_paused
from tests.test_scheduler_new_promises import clock_shows
from tests.test_scheduler_start_at import runs, write

ASKED = ("for those cases show a dialog explaining the user about it and then asking them to proceed or to cap to a "
         "value they can modify in that dialog (100 by default)")
FLOW = """\
params:
  when: {type: datetime, expr: "now()"}
blocks:
  every_5_minutes:
    type: trigger.schedule
    config: {cron: "*/5 * * * *", misfire: catch_up}
    ui: {x: 0, y: 0}
  load_orders:
    type: logic.python
    config:
      inputs: []
      code: |
        output = "the orders of " + params["when"].strftime("%H:%M")
        print(output)
    ui: {x: 260, y: 0}
edges:
  - every_5_minutes.start -> load_orders.after
"""
LATEST = 4  # the number you type in the window (Run history shows 4 runs without scrolling)


@pytest.fixture
def questions():
    """The windows the scheduler service shows to ask you (closed at the end)."""
    shown = []
    yield shown
    for window in shown:
        window.hide()
        window.deleteLater()


@pytest.fixture
def services(win, qtbot):
    """start() starts the scheduler service (the proof's own: see Service), as `taskloom scheduler` starts; each is
    stopped at the end."""
    started = []
    yield lambda: started.append(Service(win.home, qtbot)) or started[-1]
    for service in started:
        service.stop()


def test_more_than_100_missed_asks_first_and_runs_only_the_latest_you_choose(proof, env, win, qtbot, monkeypatch,
                                                                            questions, services):
    p = proof("More than 100 missed: the scheduler service asks how many to catch up", ASKED)
    p.idea("When a schedule with Misfire catch_up missed more than 100 of its times while the scheduler service was "
           "off or the computer asleep, the service asks first. A window, Catch up missed runs?, says how many were "
           "missed and offers Run all of them, or Run only the latest N, with N 100 unless you change it.")
    p.idea("With only the latest N, those N run, oldest first, one at a time. The older ones are not run, and a "
           "notification tells you how many. Until you answer, that schedule's runs wait; other flows run as usual.")
    p.why_simplest("Up to 100 missed times, you are not asked: they all run. Above that you are asked once, in one "
                   "small window with one number to change. The runs you choose wait in the same line as every "
                   "catch-up, so they run one at a time, oldest first.")
    p.alternative("Cap a catch-up at 100 without asking, as before",
                  "You said Catch up should mean all of them (“why cap at 100? I'd rather have it mean ALL of them”); "
                  "only you can say whether that many runs are still worth it.")
    p.alternative("Ask in the editor",
                  "The service runs your flows also while the editor is closed: it asks in a window of its own, so the "
                  "question comes when the service finds the missed times.")
    p.fits("You are told how many were not run with the same Missed runs skipped notification that Misfire skip gives, "
           "plus an email if the flow's notifications include skipped runs.")
    p.fits("On a server, with no screen to ask on, the latest 100 run, and the service's log says how many were "
           "missed.")
    day = dt.date.today() - dt.timedelta(days=1)

    def at(text: str) -> dt.datetime:
        return dt.datetime.combine(day, dt.time.fromisoformat(text))

    path = write(env.root / "orders.yaml", FLOW)
    set_paused(win.home, True)  # (turned off earlier, as the button's Yes does with no runs going)

    def ask(flow_name, count, answer):  # as the scheduler service asks on a desktop (scheduler._serve_tray)
        questions.append(CatchUpDialog(flow_name, count, answer))
        questions[-1].show()
        questions[-1].activateWindow()

    on = []  # the service the button starts

    def turn_on(by_you=False):  # the button: as the editor turns it on, with the proof's service as the program
        if by_you:
            set_paused(win.home, False)
        on.append(services())
        on[-1].scheduler.ask = ask

    monkeypatch.setattr(win, "start_scheduler", turn_on)

    p.step(f"With the scheduler service off, open orders.yaml (File > Open…) and save it (File > Save), at 08:00 "
           f"yesterday ({day:%Y-%m-%d}). Its Schedule block every_5_minutes runs it every 5 minutes, with Misfire: "
           "catch_up. Its block load_orders is quick: it says which time its run is for. Then click every_5_minutes.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu(win, "Open…")
    tab = win.current
    with clock_shows(at("08:00:00")):  # (saved as the service's clock has it)
        file_menu(win, "Save")
    said = status_message_area(win)
    message = win.statusBar().currentMessage()
    click_block(tab, "every_5_minutes")
    qtbot.waitUntil(lambda: len(misfire_row(win)) == 2
                    and countdown(tab, "every_5_minutes") == "scheduler service off", timeout=5_000)
    p.shot(win, "① Misfire: catch_up. ② Under every_5_minutes: scheduler service off. ③ The editor says the flow is "
                "scheduled, but the service is off.", highlight=[misfire_row(win), "every_5_minutes", said],
           badges=True, zoom=[misfire_row(win)])
    p.check("The flow is scheduled, every 5 minutes, with Misfire catch_up",
            tab.scheduled and tab.doc.blocks["every_5_minutes"]["config"] == {"cron": "*/5 * * * *",
                                                                             "misfire": "catch_up"})
    p.check("The editor says the scheduler service is off", message.startswith("This flow is scheduled, but the "
                                                                              "scheduler service is off"))

    p.step("Leave it off until 18:03 yesterday, then click Scheduler service off, at the top right, to turn it on. "
           "At its first check, 18:03:00, it finds the times it missed, and a window opens: Catch up missed runs?")
    p.note("Here the scheduler service's own checks run inside the proof, at times it chooses, yesterday: a proof "
           "cannot wait 10 hours. The button starts the proof's service in place of the service's program. Its window "
           "is the one the service shows on your desktop, also with the editor closed, shown the same way. The runs "
           "it starts are real. Run history's Started is when the proof ran them; when, under Parameters, is the "
           "time each run is for.")
    QTest.mouseClick(service_button(win), Qt.LeftButton)
    qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=10_000)
    (service,) = on
    service.check(at("18:03:00"))
    (window,) = questions
    texts = " ".join(label.text() for label in window.findChildren(QLabel))
    choice = {r.text(): r for r in window.findChildren(QRadioButton)}
    number, = window.findChildren(QSpinBox)
    proceed = next(b for b in window.findChildren(QPushButton) if b.text() == "Proceed")
    intro = window.findChildren(QLabel)[0]
    p.shot(window, "① orders missed 120 of its times. ② Run all of them, chosen unless you change it, or ③ Run only "
                   "the latest 100, a number you can change. ④ Proceed.",
           highlight=[intro, choice["Run all of them"], [choice["Run only the latest"], number], proceed], badges=True)
    p.check("The window says that orders missed 120 of its times: every 5 minutes from 08:05 to 18:00",
            window.isVisible() and window.windowTitle() == "Catch up missed runs?"
            and texts.startswith("orders missed 120 of its scheduled times"))
    p.check("It offers Run all of them (chosen) or Run only the latest, with 100 in its box",
            choice["Run all of them"].isChecked() and not choice["Run only the latest"].isChecked()
            and number.value() == 100)
    service.check(at("18:03:05"))
    p.check("While the window is open, none of the missed runs starts, also at the service's next check, 18:03:05",
            runs(env) == [] and window.isVisible())

    p.step(f"Click Run only the latest, type {LATEST} in its box, and click Proceed.")
    QTest.mouseClick(choice["Run only the latest"], Qt.LeftButton)
    QTest.mouseClick(number.lineEdit(), Qt.LeftButton)
    QTest.keyClick(number, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(number, str(LATEST))
    p.shot(window, f"① Run only the latest, ② {LATEST}. ③ Proceed.",
           highlight=[choice["Run only the latest"], number, proceed], badges=True)
    p.check(f"Run only the latest is chosen, with {LATEST}",
            choice["Run only the latest"].isChecked() and number.value() == LATEST)
    QTest.mouseClick(proceed, Qt.LeftButton)
    p.check("Proceed closed the window", not window.isVisible())

    checks = [at("18:03:00") + dt.timedelta(seconds=10 * k) for k in range(1, LATEST + 2)]
    p.step(f"Let the scheduler service check at {checks[0]:%H:%M:%S}, {checks[1]:%H:%M:%S} and so on to "
           f"{checks[-1]:%H:%M:%S}, each once the run before has ended. Then click the Run history tab.")
    for moment in checks:
        service.wait_for_its_runs_to_end()
        service.check(moment)
    qtbot.waitUntil(lambda: len(finished(env, "orders")) == LATEST and editor_shows(env, tab), timeout=20_000)
    click_tab(win.bottom_tabs, win.history_panel)
    qtbot.waitUntil(lambda: win.history_panel.table.rowCount() == LATEST, timeout=10_000)
    done = finished(env, "orders")
    latest = [f"{at('18:00:00') - dt.timedelta(minutes=5 * k):%H:%M}" for k in reversed(range(LATEST))]
    in_words = ", ".join(latest[:-1]) + " and " + latest[-1]
    p.shot(win, f"① The latest {LATEST} times ran, one for each: when {in_words} (newest at the top). Each began "
                "once the one before had ended. ② load_orders shows the latest: “the orders of 18:00”.",
           highlight=[history_outline(win), "load_orders"], badges=True, zoom=[history_area(win, header=True)])
    p.check(f"Exactly the latest {LATEST} ran, oldest first: {in_words}, all success",
            [(f"{r['when']:%H:%M}", r["status"]) for r in runs(env)] == [(t, "success") for t in latest])
    p.check("One at a time: each began once the one before had ended",
            all(b["started"] >= a["finished"] for a, b in zip(done, done[1:])))

    p.step("Read what the scheduler service told: its notifications.")
    p.text("The scheduler service's notifications, each with the time of the check that gave it",
           "\n".join(service.notices) or "(none)")
    p.check(f"One notification: the other {120 - LATEST} missed runs are not run",
            service.notices == [f"{checks[0]:%H:%M:%S}  Missed runs skipped: orders: you chose to run only the "
                                f"latest {LATEST} of 120 missed runs; the other {120 - LATEST} are not run"])
