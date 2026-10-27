"""Proof: the countdown under a Schedule block moves on with the computer's clock, so it says 0 as the run begins, with
the real scheduler service running. How proofs are written: README.md here."""

import datetime as dt

from PySide6.QtWidgets import QFileDialog

from proofs.test_proof_scheduler_misfire import wait_till
from proofs.test_proof_scheduler_start import REPORT, click_block, click_empty, clock, countdown, file_menu
from tests.test_scheduler_start_at import runs, start_service, stop_service, wait_until_finished, write

ASKED = "the idea is that when the countdown gets to 0 the flow that the schedule block inititates gets run."
SECOND = dt.timedelta(seconds=1)


def shown_seconds(text: str) -> int:
    """The seconds a countdown such as "next run in 00:01:05" says."""
    hours, minutes, seconds = map(int, text.removeprefix("next run in ").split(":"))
    return (hours * 60 + minutes) * 60 + seconds


def test_the_countdown_moves_on_with_the_clock(proof, env, win, qtbot, monkeypatch):
    p = proof("The countdown moves on with the computer's clock", ASKED)
    p.idea("The countdown under a Schedule block now changes as the computer's clock reaches its next second, and "
           "always says the time left rounded up to a whole second: 00:00:01 in the last second before the run, and "
           "the run begins as it reaches 0.")
    p.idea("Before, the editor refreshed the countdowns once a second, but at whatever point in the second it had "
           "been opened, and each refresh counted from the start of its second. So between two refreshes a countdown "
           "could say up to 2 seconds more than was left, and still say 00:00:01 as the run began. The proof of an "
           "earlier change caught it on the Windows test computer: a countdown said 12 seconds when 10 were left.")
    p.why_simplest("Each refresh now sets the next one for just after the clock's next second (20 thousandths of a "
                   "second after it). The countdown is worked out as before; only when it is refreshed changes.")
    p.alternative("Refreshing ten times a second",
                  "The countdown would be off by at most a tenth of a second, but the editor would work out every "
                  "countdown ten times as often (for a schedule that runs every few minutes, reading Run history each "
                  "time), for nothing: the countdown shows whole seconds.")
    p.alternative("Counting from the exact time of each refresh, not from the start of its second",
                  "It removes one of the two seconds, not the other: between refreshes, the countdown would still "
                  "say up to a second too much.")
    p.fits("The scheduler service begins a run on the second its schedule gives (the change “Scheduled runs begin "
           "at their time”); the countdown now reaches 0 on that same second.")
    p.fits("It is one timer for the whole window: the countdowns under every Schedule block of the flow on screen, "
           "of every line, move on together.")

    due = (dt.datetime.now() + dt.timedelta(seconds=30)).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    path = write(env.root / "daily-report.yaml", REPORT.format(at=f"{due:%Y-%m-%d %H:%M}"))
    p.step(f"Open daily-report.yaml (File > Open…) and save it (File > Save). Its Schedule block, report_time, runs "
           f"it once, at {due:%H:%M} today.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu(win, "Open…")
    tab = win.current
    file_menu(win, "Save")
    p.check("The flow is scheduled", tab.scheduled)

    p.step("Turn the scheduler service on: here, from a command line, with taskloom scheduler --headless.")
    p.note("The Scheduler service button starts the same service, with an icon in the tray. It is started from a "
           "command line here so that the proof can stop it again at the end.")
    service = start_service(env, tray=False)
    try:
        click_block(tab, "report_time")
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on"
                        and countdown(tab, "report_time").startswith("next run in"), timeout=20_000)

        p.step("Look at the countdown early in a second of the clock, late in a second, and early in another.")
        for into in (150, 550, 150):  # (each in the second after the last screenshot: a slow one cannot overrun it)
            wait_till(qtbot, dt.datetime.now().replace(microsecond=0) + SECOND + dt.timedelta(milliseconds=into))
            text, now = countdown(tab, "report_time"), dt.datetime.now()
            left = (due - now).total_seconds()
            p.shot(win, f"At {clock(now)} on the computer's clock, {left:.2f} s before {due:%H:%M}:00, the countdown "
                        f"says “{text}”: the time left, rounded up.",
                   highlight=["report_time"], zoom=["report_time"])
            p.check(f"At {clock(now)} it says {shown_seconds(text)} s, with {left:.2f} s left",
                    left <= shown_seconds(text) < left + 1)

        p.step(f"Look at it in the last second before {due:%H:%M}:00.")
        wait_till(qtbot, due - SECOND + dt.timedelta(milliseconds=150))
        text, now = countdown(tab, "report_time"), dt.datetime.now()
        begun = [r for r in runs(env) if r["flow"] == "daily-report"]
        p.shot(win, f"At {clock(now)}, {(due - now).total_seconds():.2f} s before {due:%H:%M}:00: “{text}”. The run "
                    "has not begun yet.", highlight=["report_time"], zoom=["report_time"])
        p.check("In the last second it says 00:00:01, and no run has begun",
                text == "next run in 00:00:01" and begun == [])

        p.step(f"Click an empty part of the canvas, then wait until {due:%H:%M}:00 has passed.")
        click_empty(tab)  # the Log then shows every block's lines
        qtbot.waitUntil(lambda: any(r["flow"] == "daily-report" and r["status"] != "running" for r in runs(env)),
                        timeout=40_000)
        (run,) = [r for r in runs(env) if r["flow"] == "daily-report"]
        qtbot.waitUntil(lambda: f"run #{run['id']} success" in win.log.text.toPlainText(), timeout=10_000)
        log = win.log.text
        p.shot(win, f"As the countdown reached 0 the run began: the Log, under the canvas, says run #{run['id']} "
                    f"started at {due:%H:%M:%S}, and make_report wrote down when it began: "
                    f"{tab.scene.values['make_report']['output'].removeprefix('began at ')}.",
               zoom=[(log, f"run #{run['id']} "), (log, "[make_report]")])
        p.check(f"The run started at {due:%H:%M:%S}, within half a second",
                -0.02 <= (run["started"] - due).total_seconds() <= 0.5)
    finally:
        stop_service(env, service)
        wait_until_finished(env)
