"""Proof: every N minutes counts from when you switch the schedule on (v0.21), with the real scheduler service
running. How proofs are written: README.md here."""

import datetime as dt

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QGroupBox, QLabel, QToolTip

from proofs.test_proof_editor_runs import hover_line_under
from proofs.test_proof_scheduler_misfire import (finished, history_area, history_outline, history_rows, service_button,
                                                 wait_till)
from proofs.test_proof_scheduler_start import (ONE, click_block, click_empty, click_tab, clock, countdown, file_menu,
                                               reaches_zero)
from tests.test_interval_schedules import run_processes_of
from tests.test_paths_editor_editing import field
from tests.test_scheduler_start_at import (record_process_starts, start_service, stop_service, wait_until,
                                           wait_until_finished, write)

ASKED = ("I would have expected it to start at whatever time it is, the countdown to go across minutes if required, "
         "right now I am testing only 1 minute schedules but if I start one at say second 14 I expect it to do its "
         "first run at second 14 (I guess I can expect it to run 2 seconds later if python needs to load so it would "
         "be at second 16) then each minute at second 16 it will have to run again, I am not aiming to start "
         "anything at second 00 unless I just happened to have the luck to start it just at that second.")
MINUTE = dt.timedelta(minutes=1)
NOTE = """\
      code: |
        import datetime
        output = "began at " + datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(output)
"""
FLOW = f"""\
params:
  when: {{type: datetime, expr: "now()"}}
blocks:
  minute_test:
    type: trigger.schedule
    config: {{cron: "0 7 * * *"}}
    ui: {{x: 0, y: 0}}
  test_note:
    type: logic.python
    config:
      inputs: []
{NOTE}    ui: {{x: 260, y: 0}}
  on_the_clock:
    type: trigger.schedule
    config: {{cron: "* * * * *"}}
    ui: {{x: 0, y: 220}}
  clock_note:
    type: logic.python
    config:
      inputs: []
{NOTE}    ui: {{x: 260, y: 220}}
edges:
  - minute_test.start -> test_note.after
  - on_the_clock.start -> clock_note.after
"""
TEST_LINE, CLOCK_LINE = ["minute_test", "test_note"], ["on_the_clock", "clock_note"]


def when_box(win) -> QGroupBox:
    """The When box of the Schedule block shown in Properties."""
    return next(b for b in win.properties.widget().findChildren(QGroupBox) if b.title() == "When")


def when_preview(win) -> QLabel:
    """What the When box says under its choices: how often, and the next runs."""
    return next(label for label in when_box(win).findChildren(QLabel) if label.text().startswith("<b>"))


def choose_in_list(p, win, box, item: str, caption: str):
    """Open a choice's list, as a click on it does, shoot it with `item` outlined, and click `item`."""
    def look(popup):
        view, row = box.view(), box.findText(item)
        index = view.model().index(row, 0)
        view.scrollTo(index)  # in sight, as you would scroll to it
        p.shot(popup, caption, over=win, highlight=[(view, row)], zoom=[popup])
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, view.visualRect(index).center())

    p.while_open(box.showPopup, look)


def started_by(env, run: dict) -> str:
    return env.query("SELECT started_by FROM runs WHERE id=?", run["id"])[0][0]


def shown_in_log(win, run: dict) -> bool:
    return f"run #{run['id']} success" in win.log.text.toPlainText()


def test_every_n_minutes_counts_from_when_you_switch_the_schedule_on(proof, env, win, qtbot, monkeypatch):
    p = proof("Every N minutes counts from when you switch the schedule on", ASKED)
    p.idea("In a Schedule block's When, Every few minutes or hours now counts from when you switch the schedule on, "
           "not from the clock's :00. Switched on at 10:00:14, every 1 minute runs at once, then at 10:01:14, "
           "10:02:14, and so on, to the second.")
    p.idea("Switched on is the moment the scheduler service first sees the schedule as it is now: you save it new, "
           "with a changed timing, or with Active ticked again while the service is on, or you turn the service on "
           "for the first time with it. Saving the flow again with the same timing does not move it. The service "
           "now checks every second (it used to check at least every 15 seconds), so it sees your save within a "
           "second, and the schedule counts from that second.")
    p.idea("The first run begins at once, at the check that sees your save, and cannot be prepared ahead: its "
           "program has to load then, so it may begin a second or two after the save (your second 16). The later "
           "runs begin on the second itself, at second 14, not 16: the service starts each one's program 5 "
           "seconds early, as for every scheduled run, so it has loaded by its time.")
    p.idea("Under the block, the countdown counts to the next run as the service will run it, across the minute "
           "when it has to; its tooltip lists the next times, with seconds. Plans on the clock stay on the clock: "
           "every hour at :MM, every day, weekday, chosen days or month at a time, and a cron line. The cron line "
           "* * * * *, which the When builder wrote for “every minute” before, still runs at :00.")
    p.why_simplest("The scheduler service already remembers, for each schedule, the newest time it has handled: "
                   "that is how it tells which times were missed while it was off. For an every-N schedule, that "
                   "remembered time is also where it counts from. The first time the service sees the schedule, it "
                   "takes that second as the schedule's first time and runs it; each later time is N after the one "
                   "before. Nothing new is stored, by the service or in the flow file.")
    p.why_simplest("The service remembers each schedule as it is written: its flow, its block and its timing. So a "
                   "schedule whose timing you change, or that you switch off and on again, is a new one to it, and "
                   "counts again from then, with no extra rule. The countdown reads the same remembered time, so it "
                   "counts to the run the service will start.")
    p.alternative("A cron line, as Every few minutes wrote before",
                  "A cron line counts whole minutes on the clock, and has no seconds: it can say “at minute 14 of "
                  "each hour”, never “each minute at second 14”, and */5 always means :00, :05, :10.")
    p.alternative("Counting from when the first run began (your second 16)",
                  "That moment depends on how long the run's program takes to load: on the computer, on what else "
                  "it is doing, on the flow. And the service needs the times at once: for the countdown, and to start "
                  "each later run's program 5 seconds before its time. The second it saw the schedule is known at "
                  "once, and is when you switched it on.")
    p.alternative("Writing the starting time into the flow file when you save",
                  "The editor would change the file at every save, and the time would be when you saved, even if "
                  "the scheduler service was off then (or for a copy of the flow put on a server): everything since "
                  "would count as missed. The service already remembers each schedule's times.")
    p.fits("It is a choice of the same When builder: Every few minutes or hours (1 to 30 minutes, 1 to 12 hours) "
           "takes the place of Every few minutes, which wrote a cron line. The other choices are as they were. In "
           "the flow file it is every: 1 minute.")
    p.fits("A flow saved before with “every minute” keeps its cron line * * * * *: it shows as Custom (a cron line) "
           "and runs at :00, as on_the_clock does here. Choose Every few minutes or hours to count from when you "
           "switch it on.")
    p.fits("Misfire works as for every schedule: turning the scheduler service off and on again does not move the "
           "times; the ones that came while it was off were missed, and Misfire says what becomes of them.")
    p.fits("A check rereads only the flow files that changed since the one before, so checking every second "
           "costs little.")
    path = write(env.root / "minute-test.yaml", FLOW)
    starts = record_process_starts(env)  # when the service starts each run's program (not on screen)

    p.step("Turn the scheduler service on: here, from a command line, with taskloom scheduler --headless. The "
           "Scheduler service button starts the same service, with an icon in the tray; from a command line, the "
           "proof can stop it again at the end.")
    service = start_service(env, tray=False)
    try:
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=30_000)
        p.shot(win, "The Scheduler service button, top right, says Scheduler service on: the editor sees the "
                    "service the command line started.",
               highlight=[service_button(win)], zoom=[service_button(win)])
        # From here to the save takes a few seconds; the save then comes well after one :00 and well before the
        # next, so that the two lines' runs do not meet.
        now = dt.datetime.now()
        if not 8 <= now.second <= 30:
            wait_till(qtbot, now.replace(second=8, microsecond=0) + (MINUTE if now.second > 8 else dt.timedelta()))

        p.step("Open minute-test.yaml (File > Open…) and save it (File > Save). It has two lines: ① the Schedule "
               "block minute_test, set to every day at 07:00 for now, starts test_note; ② the Schedule block "
               "on_the_clock starts clock_note. Each note writes down when it began.")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
        file_menu(win, "Open…")
        tab = win.current
        file_menu(win, "Save")
        qtbot.waitUntil(lambda: all(countdown(tab, b).startswith("next run in") for b in ("minute_test",
                                                                                          "on_the_clock")),
                        timeout=10_000)
        whole_minute = dt.datetime.now().replace(second=0, microsecond=0) + MINUTE
        clock_left, clock_read = countdown(tab, "on_the_clock"), dt.datetime.now()
        p.shot(win, f"Both lines are scheduled, and each counts to its next run: ① minute_test to 07:00; ② "
                    f"on_the_clock, “{clock_left}”, to {whole_minute:%H:%M:%S}, the next whole minute.",
               highlight=[TEST_LINE, CLOCK_LINE], badges=True, zoom=[TEST_LINE, CLOCK_LINE])
        p.check("The flow is scheduled", tab.scheduled and path.resolve() in win.home.scheduled_flows())
        p.check(f"② on_the_clock counts to the next whole minute, {whole_minute:%H:%M:%S}",
                abs(reaches_zero(clock_left, clock_read) - whole_minute) <= ONE)

        p.step("Select on_the_clock: it is on the clock.")
        click_block(tab, "on_the_clock")
        qtbot.waitUntil(lambda: field(win, "How often").currentText() == "Custom (a cron line)", timeout=5_000)
        preview = when_preview(win)
        on_clock_times = [whole_minute + k * MINUTE for k in range(3)]
        p.shot(win, "on_the_clock's When: How often is Custom (a cron line), * * * * *, the line the When builder "
                    "wrote for “every minute” before this change. Its next runs are on the clock, at "
                    f"{', '.join(f'{t:%H:%M}' for t in on_clock_times)}: a cron line has no seconds.",
               highlight=[when_box(win), "on_the_clock"], zoom=[when_box(win)])
        p.check("on_the_clock shows as Custom (a cron line), * * * * *, with its next runs on whole minutes",
                field(win, "Cron line").text() == "* * * * *"
                and "Next: " + ", ".join(f"{t:%a %d %b %H:%M}" for t in on_clock_times) in preview.text())

        p.step("Select minute_test, then in Properties, under When, open How often and choose Every few minutes or "
               "hours.")
        click_block(tab, "minute_test")
        qtbot.waitUntil(lambda: field(win, "How often").currentText() == "Every day", timeout=5_000)
        choose_in_list(p, win, field(win, "How often"), "Every few minutes or hours",
                       "How often: Every few minutes or hours (outlined) counts from when you switch the schedule "
                       "on. The others are on the clock.")

        def config() -> dict:
            return tab.doc.blocks["minute_test"].get("config") or {}

        qtbot.waitUntil(lambda: config().get("every") == "15 minutes", timeout=5_000)

        p.step("Open Every and choose 1 minute.")
        choose_in_list(p, win, field(win, "Every"), "1 minute",
                       "Every: 1 minute (outlined), up to 30 minutes, then 1 to 12 hours.")
        qtbot.waitUntil(lambda: config().get("every") == "1 minute"
                        and field(win, "Every").currentText() == "1 minute", timeout=5_000)
        box = when_box(win)
        p.shot(win, "When: every minute, “from when the scheduler service picks it up (when you save the flow): "
                    "its first run is then, and the next ones every 1 minute after it”.",
               highlight=[box], zoom=[box])
        p.check("minute_test is set to every 1 minute, with no cron line",
                config().get("every") == "1 minute" and "cron" not in config())

        now = dt.datetime.now()
        save_at = max(now.replace(microsecond=50_000) + dt.timedelta(seconds=1), now.replace(second=12,
                                                                                            microsecond=50_000))
        assert save_at <= whole_minute - dt.timedelta(seconds=15), \
            f"too slow: the save, at {save_at:%H:%M:%S}, would come near {whole_minute:%H:%M:%S}"
        p.step(f"Click an empty part of the canvas, then save the flow (File > Save) at {save_at:%H:%M:%S}, a "
               "second well away from :00. The scheduler service sees the save at its next check, within a second, "
               "and the schedule counts from the second of that check: the second you saved in, or the next one.")
        click_empty(tab)  # the Log then shows every block's lines
        wait_till(qtbot, save_at)
        pressed = dt.datetime.now()
        file_menu(win, "Save")
        qtbot.waitUntil(lambda: any(shown_in_log(win, r) for r in finished(env, "minute-test")), timeout=30_000)
        (first,) = finished(env, "minute-test")
        switched_on, log = first["when"], win.log.text
        first_note = tab.scene.values["test_note"]["output"]
        after_save = (first["started"] - pressed).total_seconds()
        first_program = dict((at, started) for started, at in run_processes_of(starts, path))[switched_on]
        to_program = first_program - pressed.timestamp()
        which = ("the second you saved in" if switched_on == pressed.replace(microsecond=0)
                 else "the second after you saved")
        p.shot(win, f"The service saw the save at its check in second {switched_on:%S}, {which}: the schedule "
                    f"counts from {switched_on:%H:%M:%S} (Run history, step 10, shows it). The first run began right "
                    f"then, {after_save:.1f} s after you saved (your “second 16”): {to_program:.1f} s until that "
                    "check started its program, then the time the program took to load, as it could not be "
                    f"prepared ahead (step 11 has the times). The Log, under the canvas: run #{first['id']} started "
                    f"at {first['started']:%H:%M:%S}, and test_note {first_note}.",
               zoom=[(log, f"run #{first['id']} "), (log, first_note)])
        p.check(f"The service saw the save at its next check: the schedule counts from {switched_on:%H:%M:%S}, "
                "the second you saved in or the next one, not from :00",
                switched_on - pressed.replace(microsecond=0) in (dt.timedelta(0), dt.timedelta(seconds=1))
                and switched_on.second != 0)
        # (10 s: a slow computer may take a few seconds to load the program; the next :00 is at least 15 s away)
        p.check(f"The first run is for {switched_on:%H:%M:%S}, started by minute_test, and began right then, "
                f"{after_save:.1f} s after the save",
                started_by(env, first) == "minute_test" and first["status"] == "success"
                and switched_on <= first["started"] <= pressed + dt.timedelta(seconds=10))

        next_run = switched_on + MINUTE
        p.step("Hover the countdown under minute_test.")
        qtbot.waitUntil(lambda: countdown(tab, "minute_test").startswith("next run in"), timeout=5_000)
        left, read = countdown(tab, "minute_test"), dt.datetime.now()
        tip = hover_line_under(tab, "minute_test")
        try:
            tip_text = QToolTip.text()
            p.shot(tip, f"The countdown under minute_test, “{left}”, counts to {next_run:%H:%M:%S}, one minute "
                        f"after it was switched on, across the minute. Its tooltip lists the next runs, each at "
                        f"second {switched_on:%S}.", over=win, highlight=[tip], zoom=[tip, "minute_test"])
            p.check(f"The countdown counts to {next_run:%H:%M:%S}", abs(reaches_zero(left, read) - next_run) <= ONE)
            p.check(f"Its tooltip lists the next five runs, {next_run:%H:%M:%S} to "
                    f"{switched_on + 5 * MINUTE:%H:%M:%S}, each at second {switched_on:%S}",
                    tip is not None and all(f"{switched_on + k * MINUTE:%H:%M:%S}" in tip_text for k in range(1, 6)))
        finally:
            QToolTip.hideText()

        on_clock = whole_minute
        p.step(f"Wait for {on_clock:%H:%M:%S}, the next whole minute.")

        def clock_run() -> list:
            return [r for r in finished(env, "minute-test") if r["when"] == on_clock]

        qtbot.waitUntil(lambda: any(shown_in_log(win, r) for r in clock_run()),
                        timeout=int(((on_clock - dt.datetime.now()).total_seconds() + 30) * 1000))
        (by_clock,) = clock_run()
        test_left, test_read = countdown(tab, "minute_test"), dt.datetime.now()
        p.shot(win, f"② on_the_clock's run began at {by_clock['started']:%H:%M:%S}, on the clock: clock_note "
                    f"{tab.scene.values['clock_note']['output']}. ① minute_test goes on counting across the minute: "
                    f"“{test_left}”, to {next_run:%H:%M:%S}.",
               highlight=[TEST_LINE, CLOCK_LINE], badges=True, zoom=[TEST_LINE, CLOCK_LINE])
        p.check(f"② on_the_clock's run began at {on_clock:%H:%M:%S}, within half a second",
                started_by(env, by_clock) == "on_the_clock" and by_clock["status"] == "success"
                and -0.02 <= (by_clock["started"] - on_clock).total_seconds() <= 0.5)
        p.check(f"① minute_test still counts to {next_run:%H:%M:%S}",
                abs(reaches_zero(test_left, test_read) - next_run) <= ONE)

        p.step(f"Wait until {next_run:%H:%M:%S}, one minute after the schedule was switched on.")

        def second_run() -> list:
            return [r for r in finished(env, "minute-test") if r["when"] == next_run]

        qtbot.waitUntil(lambda: any(shown_in_log(win, r) for r in second_run()),
                        timeout=int(((next_run - dt.datetime.now()).total_seconds() + 30) * 1000))
        (second,) = second_run()
        second_note = tab.scene.values["test_note"]["output"]
        p.shot(win, f"The next run began on that second, one minute later: run #{second['id']} started at "
                    f"{second['started']:%H:%M:%S}, and test_note {second_note}. Its program was started 5 seconds "
                    "early, so it had loaded by then (step 11).",
               zoom=[(log, f"run #{second['id']} "), (log, second_note)])
        p.check(f"Run #{second['id']} is for {next_run:%H:%M:%S}, started by minute_test, and began within half a "
                "second of it", started_by(env, second) == "minute_test" and second["status"] == "success"
                and -0.02 <= (second["started"] - next_run).total_seconds() <= 0.5)

        p.step("Click the Run history tab, under the canvas.")
        click_tab(win.bottom_tabs, win.history_panel)
        rows = history_rows(win)
        p.shot(win, f"Newest first. Started by minute_test: run #{first['id']} for {switched_on:%H:%M:%S} (when, "
                    f"under Parameters), begun at {first['started']:%H:%M:%S}, and run #{second['id']} for and at "
                    f"{next_run:%H:%M:%S}. Between them, run #{by_clock['id']}, by on_the_clock, at "
                    f"{on_clock:%H:%M:%S}.",
               highlight=[history_outline(win)], zoom=[history_area(win, header=True)])
        p.check("Run history lists the three runs, each with when it began, its start block and its time",
                [[r[0], r[1], r[2], r[3], r[5]] for r in rows] == [
                    [str(run["id"]), f"{run['started']:%Y-%m-%d %H:%M:%S}", by, "success",
                     f"when={run['when']:%Y-%m-%d %H:%M:%S}"]
                    for run, by in ((second, "minute_test"), (by_clock, "on_the_clock"), (first, "minute_test"))])

        p.step("Read the times, to the millisecond: when you saved, when the service saw it, and when each run's "
               "program started and the run began.")
        p.note("When the service started each run's program is not on screen: the editor shows a run once it "
               "begins. The proof writes it down as the service starts the program.")
        programs = dict((at, started) for started, at in run_processes_of(starts, path))
        p.text("The times, to the millisecond",
               "\n".join(f"{what:<50}{clock(when)}{also}" for what, when, also in [
                   ("You saved", pressed, ""),
                   ("The schedule counts from: the second it was seen", switched_on, ""),
                   (f"run #{first['id']} (minute_test): its program started",
                    dt.datetime.fromtimestamp(programs[switched_on]),
                    f"   {programs[switched_on] - pressed.timestamp():.1f} s after you saved"),
                   (f"run #{first['id']} (minute_test): began", first["started"], ""),
                   (f"run #{by_clock['id']} (on_the_clock): its program started",
                    dt.datetime.fromtimestamp(programs[on_clock]),
                    f"   {on_clock.timestamp() - programs[on_clock]:.1f} s before {on_clock:%H:%M:%S}"),
                   (f"run #{by_clock['id']} (on_the_clock): began", by_clock["started"], ""),
                   (f"run #{second['id']} (minute_test): its program started",
                    dt.datetime.fromtimestamp(programs[next_run]),
                    f"   {next_run.timestamp() - programs[next_run]:.1f} s before {next_run:%H:%M:%S}"),
                   (f"run #{second['id']} (minute_test): began", second["started"], "")]))
        p.check(f"Run #{first['id']}'s program started only once you had saved: it could not be prepared ahead",
                programs[switched_on] >= pressed.timestamp())
        p.check(f"Run #{second['id']}'s program started at least a second before {next_run:%H:%M:%S}, so the run "
                "began on that second", next_run.timestamp() - programs[next_run] >= 1.0)
    finally:
        stop_service(env, service)
        waiting = [at for _, at in run_processes_of(starts, path)]  # a program started early ends at its time
        if waiting:
            wait_until(max(waiting) + dt.timedelta(seconds=3))
        wait_until_finished(env)
