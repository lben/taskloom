"""Proofs (pull request #1): after a gap, a check just before a run's time keeps the on-time run; catch-up keeps
every missed time, also behind a run still going. How proofs are written: README.md here."""

import datetime as dt
import os

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QComboBox, QFileDialog, QLabel, QToolBar

from proofs.test_proof_scheduler_start import ONE, click_block, click_tab, clock, file_menu, reaches_zero
from taskloom import __version__
from taskloom.scheduler import EARLY_START, Scheduler, acquire_lock
from tests.test_scheduler_start_at import runs, write

ASKED = "a different agent found this ... Review it and verify if it is correct indeed, if so worth merging no?"
MINUTE = dt.timedelta(minutes=1)
MISFIRES = ("run_once", "skip", "catch_up")
REPORT = """\
params:
  when: {{type: datetime, expr: "now()"}}
blocks:
  every_minute:
    type: trigger.schedule
    config: {{cron: "* * * * *", misfire: {misfire}}}
    ui: {{x: 0, y: 0}}
  report:
    type: logic.python
    config:
      inputs: []
      code: |
        output = "the run for " + params["when"].strftime("%H:%M")
        print(output)
    ui: {{x: 260, y: 0}}
edges:
  - every_minute.start -> report.after
"""
LONG = """\
params:
  when: {type: datetime, expr: "now()"}
blocks:
  every_minute:
    type: trigger.schedule
    config: {cron: "* * * * *", misfire: catch_up}
    ui: {x: 0, y: 0}
  long_work:
    type: logic.python
    config:
      inputs: []
      code: |
        import time
        from pathlib import Path
        while not Path("go.txt").exists():  # a long piece of work: it ends when the file go.txt is there
            time.sleep(0.05)
        output = "the run for " + params["when"].strftime("%H:%M")
        print(output)
    ui: {x: 260, y: 0}
edges:
  - every_minute.start -> long_work.after
"""


class Service:
    """The scheduler service, run inside the proof so that the proof says when it checks: as `taskloom scheduler`
    does (scheduler.serve), it holds the lock and says which Taskloom it is (so the editor shows it on, and a run
    started early begins), then checks with the same scheduler. Its notifications are kept, with each check's time."""

    def __init__(self, home, qtbot):
        self.qtbot, self.notices, self.clock, self.next_check = qtbot, [], None, None
        self.lock = acquire_lock(home)
        (home.root / "scheduler.pid").write_text(str(os.getpid()))
        (home.root / "scheduler.version").write_text(f"{__version__} {os.getpid()}")
        self.scheduler = Scheduler(home, lambda title, message: self.notices.append(
            f"{self.clock:%H:%M:%S}  {title}: {message}"))

    def check(self, at: dt.datetime | None = None):
        """One check, now or at the chosen time `at`."""
        self.clock = at or dt.datetime.now()
        self.scheduler.tick(self.clock)
        self.next_check = self.clock + dt.timedelta(seconds=self.scheduler.seconds_to_next_check(self.clock))

    def run_until(self, done, until: dt.datetime):
        """Its loop, as serve() runs it: each check when the one before said, until done() or the clock says `until`."""
        while not done() and dt.datetime.now() < until:
            if dt.datetime.now() >= self.next_check:
                self.check()
            self.qtbot.wait(50)

    def wait_for_its_runs_to_end(self):
        self.qtbot.waitUntil(lambda: all(r.proc.poll() is not None for r in self.scheduler.runs), timeout=60_000)

    def stop(self):
        for run in self.scheduler.runs:
            if run.proc.poll() is None:
                run.proc.kill()
                run.proc.wait(10)
        self.lock.close()


@pytest.fixture
def service(win, qtbot):
    s = Service(win.home, qtbot)
    try:
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=10_000)
        yield s
    finally:
        s.stop()


def wait_till(qtbot, moment: dt.datetime):
    """Wait until the clock says `moment`, the editor going on as usual."""
    while (left := (moment - dt.datetime.now()).total_seconds()) > 0:
        qtbot.wait(max(1, int(min(left, 0.05) * 1000)))


def finished(env, flow: str) -> list[dict]:
    return [r for r in runs(env) if r["flow"] == flow and r["status"] != "running"]


def misfire_row(win) -> list:
    """The Misfire label and its choice, in Properties (made anew whenever the panel is); [] until it is there."""
    labels = [w for w in win.properties.findChildren(QLabel) if w.text() == "Misfire" and w.isVisible()]
    choices = [w for w in win.properties.findChildren(QComboBox) if labels and w.isVisible()
               and w.toolTip() == labels[0].toolTip()]
    return labels[:1] + choices[:1]


def select_schedule(win, qtbot, tab):
    """Click every_minute: Properties shows it, and under it the countdown to its next time."""
    click_block(tab, "every_minute")
    qtbot.waitUntil(lambda: len(misfire_row(win)) == 2
                    and tab.scene.nodes["every_minute"].countdown.startswith("next run in"), timeout=5_000)


def switch_to(win, qtbot, tab):
    """Click the flow's tab: Properties shows its every_minute again."""
    click_tab(win.tabs, tab)
    misfire = tab.doc.blocks["every_minute"]["config"]["misfire"]
    qtbot.waitUntil(lambda: win.current is tab and [w.text() if isinstance(w, QLabel) else w.currentText()
                                                    for w in misfire_row(win)] == ["Misfire", misfire], timeout=5_000)


def history_rows(win) -> list[list[str]]:
    table = win.history_panel.table
    return [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]


def history_area(win, header=False) -> QRect:
    """Run history's rows (and its column names, with header), in the window's pixels, up to the end of their
    Parameters (that column stretches)."""
    table, area = win.history_panel.table, QRect()
    last = table.columnCount() - 1
    for r in range(table.rowCount()):
        for c in range(table.columnCount()):
            rect = table.visualItemRect(table.item(r, c))
            if c == last:
                rect.setWidth(table.fontMetrics().horizontalAdvance(table.item(r, c).text()) + 16)
            area |= rect
    area.translate(table.viewport().mapTo(win, QPoint(0, 0)))
    if header:
        names = table.horizontalHeader()
        area |= QRect(names.mapTo(win, QPoint(0, 0)), names.size()) & QRect(area.left(), 0, area.width(), win.height())
    return area


def history_outline(win) -> QRect:
    """What to outline of Run history's rows: their outline then falls on their edges, not over the column names."""
    return history_area(win).adjusted(0, 6, 0, -6)


def service_button(win):
    return win.findChild(QToolBar, "run-toolbar").widgetForAction(win.scheduler_action)


def editor_shows(env, tab) -> bool:
    """The editor has taken in the flow's latest finished run (it reads them every 2 seconds)."""
    done = finished(env, tab.doc.path.stem)
    return bool(done) and tab.last is not None and tab.last["id"] == done[-1]["id"]


def test_a_check_just_before_a_runs_time_keeps_the_on_time_run(proof, env, win, qtbot, monkeypatch, service):
    p = proof("After a gap, a check just before a run's time keeps the on-time run", ASKED)
    p.idea("When the computer wakes from sleep, or the scheduler service starts again, a few seconds before a "
           "scheduled run's time, that run is no longer lost: with run_once or skip it begins on time, and catch_up "
           "runs it after the missed ones. Before, the service started the run it had missed while the computer "
           "slept, and then refused the on-time one: “Run skipped: the previous run is still going”.")
    p.idea("Another agent found this case by checking a model of the scheduler (pull request #1). It was confirmed "
           "here: the new tests fail without the change, and pass with it.")
    p.idea("The service checks at least every 15 seconds. At each check it takes the times that came since its "
           "previous check, and the ones of the next 5 seconds: it starts a run's program 5 seconds early, so that "
           "the run begins at its time. After a gap, one check can find both: times missed while the computer "
           "slept, and the time about to come.")
    p.idea("The Misfire setting of the Schedule block now chooses among all of these together, as the README "
           "promises: run_once, “one run, for the most recent time”, which is the one about to come; skip, “only "
           "the on-time run”, with one notification that says how many were missed; catch_up, “one run for each "
           "time, oldest first, one after another”.")
    p.idea("Before, the setting chose only among the times already passed, and the time about to come was started "
           "on top of them. Its run found the missed run just started, and the flow's setting “If a scheduled run "
           "starts while the last one is still going” (skip the new run, unless you change it) refused it. So "
           "run_once ran the missed time instead of the most recent, skip could run a missed time instead of the "
           "on-time one, and catch_up lost its newest time.")
    p.why_simplest("The time about to come is simply one more time for the Misfire setting to choose from. The "
                   "three settings keep the rules the README gives; only the list they choose from is now complete. "
                   "Nothing new to set, and no extra wait.")
    p.why_simplest("The run chosen is started as before: one about to come is started early, to begin at its time; "
                   "a missed one begins at once. A catch-up of several times queues them, so they run one after "
                   "another.")
    p.alternative("Leave the time about to come for the next check",
                  "The next check comes up to 15 seconds later, after that time: the run would begin late, which is "
                  "what starting its program 5 seconds early is there to prevent.")
    p.alternative("Let the run about to come wait behind the missed one, instead of being refused",
                  "With run_once that is two runs where it promises one, and the on-time run would begin only once "
                  "the late one had ended.")
    p.alternative("Run the missed time and the on-time one side by side",
                  "Two runs of the same flow at the same time, which the setting “If a scheduled run starts while "
                  "the last one is still going” is there to decide. You chose skip for it, or kept that default.")
    p.fits("It is the same Misfire setting as in Properties, with the same three choices and the same 2-minute rule "
           "for what counts as on time. The tray service and the headless one (on servers) check the same way.")
    p.fits("The setting “If a scheduled run starts while the last one is still going” still decides when a run of "
           "the flow really is still going: a run that has not ended by the next time.")
    paths = {m: write(env.root / f"report-{m.replace('_', '-')}.yaml", REPORT.format(misfire=m)) for m in MISFIRES}
    names = {m: path.stem for m, path in paths.items()}

    p.step("Open report-run-once.yaml, report-skip.yaml and report-catch-up.yaml (File > Open…) and save each "
           "(File > Save). In each, the Schedule block every_minute runs the flow every minute, and report writes "
           "down which time the run is for. They differ only in every_minute's Misfire: run_once, skip and "
           "catch_up. Saved, they are scheduled.")
    tabs = {}
    for misfire, path in paths.items():
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, _path=path, **k: (str(_path), ""))
        file_menu(win, "Open…")
        tabs[misfire] = win.current
        file_menu(win, "Save")
        select_schedule(win, qtbot, tabs[misfire])
    switch_to(win, qtbot, tabs["run_once"])
    p.shot(win, "① The three flows, each in its tab. ② report-run-once's every_minute, with Misfire: run_once (the "
                "default). ③ The scheduler service is on.",
           highlight=[win.tabs.tabBar(), misfire_row(win), service_button(win)], badges=True,
           zoom=[misfire_row(win)])
    p.check("The three flows are scheduled, with Misfire run_once, skip and catch_up",
            all(tab.scheduled and tab.doc.blocks["every_minute"]["config"]["misfire"] == m for m, tab in tabs.items()))

    now = dt.datetime.now()
    if (now + EARLY_START + dt.timedelta(seconds=1)).minute != now.minute:  # it would start the next minute's run
        wait_till(qtbot, now.replace(second=0, microsecond=0) + MINUTE + dt.timedelta(seconds=1))
    service.check()
    t0 = service.clock
    missed = (t0 + EARLY_START).replace(second=0, microsecond=0) + MINUTE  # the first time after this check's
    on_time = missed + MINUTE
    wake = on_time - dt.timedelta(seconds=4)
    p.step(f"Let the scheduler service check at {t0:%H:%M:%S}. Then put the computer to sleep until "
           f"{wake:%H:%M:%S}, 4 seconds before {on_time:%H:%M}: the {missed:%H:%M} run is due while it sleeps.")
    p.note("A proof cannot put the computer to sleep. Here, the scheduler service's own checks run inside the "
           f"proof, at the real time, as the service makes them, and the proof holds them back from {t0:%H:%M:%S} "
           f"to {wake:%H:%M:%S}, as sleep does. Its notifications are listed as text at the end: a tray "
           "notification cannot be shot here.")
    p.check(f"At {t0:%H:%M:%S} nothing was due: the service started no run",
            not service.scheduler.runs and runs(env) == [])
    wait_till(qtbot, wake - dt.timedelta(seconds=3))
    click_tab(win.bottom_tabs, win.history_panel)
    just_before, read = tabs["run_once"].scene.nodes["every_minute"].countdown, dt.datetime.now()
    p.shot(win, f"Just before waking: {missed:%H:%M} has passed, and ② Run history is empty: nothing ran while the "
                f"computer slept. ① every_minute counts down to {on_time:%H:%M}: “{just_before}”.",
           highlight=["every_minute", win.history_panel.table], badges=True, zoom=["every_minute"])
    p.check(f"Nothing ran for {missed:%H:%M} while the computer slept",
            runs(env) == [] and win.history_panel.table.rowCount() == 0)
    p.check(f"The countdown aims at {on_time:%H:%M}:00", abs(reaches_zero(just_before, read) - on_time) <= ONE)
    wait_till(qtbot, wake)

    service.check()
    woke = service.clock
    p.step(f"Wake the computer at {wake:%H:%M:%S}: the scheduler service checks at once. Wait until "
           f"{on_time:%H:%M} has passed, then click the Run history tab.")
    p.check(f"It checked at {clock(woke)}, in the 5 seconds before {on_time:%H:%M}",
            on_time - EARLY_START < woke < on_time)
    expected = {"run_once": [on_time], "skip": [on_time], "catch_up": [missed, on_time]}
    service.run_until(lambda: all(len(finished(env, names[m])) >= len(times) for m, times in expected.items()),
                      until=on_time + dt.timedelta(seconds=50))  # (at 55 s, it would start the next minute's runs)
    done = {m: finished(env, names[m]) for m in MISFIRES}
    qtbot.waitUntil(lambda: all(editor_shows(env, tab) for tab in tabs.values()), timeout=20_000)
    click_tab(win.bottom_tabs, win.history_panel)
    once = done["run_once"][-1]
    p.shot(win, f"report-run-once (② Misfire: run_once): one run, for {on_time:%H:%M}, the most recent time. ③ It "
                f"began at {on_time:%H:%M:%S}, on time (Parameters: when, the time it is for), and ① report says "
                f"“the run for {on_time:%H:%M}”. The {missed:%H:%M} time, missed while the computer slept, does not "
                "run.",
           highlight=["report", misfire_row(win), history_outline(win)], badges=True,
           zoom=[history_area(win, header=True)])
    p.check(f"report-run-once ran once, for {on_time:%H:%M}", [r["when"] for r in done["run_once"]] == [on_time])
    p.check(f"That run began on time: {(once['started'] - on_time).total_seconds():.2f} s after {on_time:%H:%M:%S}",
            -0.02 <= (once["started"] - on_time).total_seconds() <= 0.5)
    p.check(f"Run history shows it: started {on_time:%H:%M:%S}, by every_minute, success, when={on_time:%H:%M}",
            [r[1:4] + r[5:] for r in history_rows(win)]
            == [[f"{on_time:%Y-%m-%d %H:%M:%S}", "every_minute", "success", f"when={on_time:%Y-%m-%d %H:%M:%S}"]])
    p.check(f"report says “the run for {on_time:%H:%M}”",
            f"the run for {on_time:%H:%M}" in tabs["run_once"].scene.values.get("report", {}).get("output", ""))

    p.step("Switch to report-skip.yaml.")
    switch_to(win, qtbot, tabs["skip"])
    click_tab(win.bottom_tabs, win.history_panel)
    skipped = done["skip"][-1]
    p.shot(win, f"report-skip (② Misfire: skip): ③ only the on-time run, for {on_time:%H:%M}, which began at "
                f"{on_time:%H:%M:%S}. ① report says “the run for {on_time:%H:%M}”. The {missed:%H:%M} time was "
                "missed, and is skipped: a notification says so (step 6).",
           highlight=["report", misfire_row(win), history_outline(win)], badges=True,
           zoom=[history_area(win, header=True)])
    p.check(f"report-skip ran once, for {on_time:%H:%M}, and began on time",
            [r["when"] for r in done["skip"]] == [on_time]
            and -0.02 <= (skipped["started"] - on_time).total_seconds() <= 0.5)
    p.check(f"Run history shows it: started {on_time:%H:%M:%S}, when={on_time:%H:%M}",
            [(r[1], r[5]) for r in history_rows(win)]
            == [(f"{on_time:%Y-%m-%d %H:%M:%S}", f"when={on_time:%Y-%m-%d %H:%M:%S}")])

    p.step("Switch to report-catch-up.yaml.")
    switch_to(win, qtbot, tabs["catch_up"])
    click_tab(win.bottom_tabs, win.history_panel)
    first, second = done["catch_up"][:2]
    p.shot(win, f"report-catch-up (② Misfire: catch_up): ③ one run for each time, oldest first, one after another. "
                f"The {missed:%H:%M} run, missed, began on waking, at {first['started']:%H:%M:%S}; the "
                f"{on_time:%H:%M} run waited until it had ended, and began at the service's next check, at "
                f"{second['started']:%H:%M:%S}. ① report shows the latest: “the run for {on_time:%H:%M}”.",
           highlight=["report", misfire_row(win), history_outline(win)], badges=True,
           zoom=[history_area(win, header=True)])
    p.check(f"report-catch-up ran twice: for {missed:%H:%M}, then for {on_time:%H:%M}",
            [r["when"] for r in done["catch_up"]] == [missed, on_time])
    p.check(f"The {on_time:%H:%M} run began only once the {missed:%H:%M} run had ended",
            second["started"] >= first["finished"])
    p.check("Run history shows both, newest first, each with its time",
            [r[5] for r in history_rows(win)] == [f"when={t:%Y-%m-%d %H:%M:%S}" for t in (on_time, missed)])

    p.step("Read what the scheduler service told: its notifications. And the times, to the millisecond.")
    p.text("The scheduler service's notifications, each with the time of the check that gave it",
           "\n".join(service.notices) or "(none)")
    p.check("No notification says “Run skipped”: no run was refused",
            not any("Run skipped" in notice for notice in service.notices))
    p.check(f"One notification, from the check on waking: report-skip missed 1 run ({missed:%H:%M})",
            service.notices == [f"{woke:%H:%M:%S}  Missed runs skipped: report-skip: 1 run(s) were due while the "
                                "scheduler was off"])
    p.text("The times, to the millisecond",
           "\n".join(f"{what:<44}{clock(when)}" for what, when in [
               ("The service checked", t0), ("The computer woke, and the service checked", woke),
               (f"report-run-once: its {on_time:%H:%M} run began", once["started"]),
               (f"report-skip: its {on_time:%H:%M} run began", skipped["started"]),
               (f"report-catch-up: its {missed:%H:%M} run began", first["started"]),
               (f"report-catch-up: its {missed:%H:%M} run ended", first["finished"]),
               (f"report-catch-up: its {on_time:%H:%M} run began", second["started"])]))


def test_catch_up_keeps_every_missed_time_behind_a_run_still_going(proof, env, win, qtbot, monkeypatch, service):
    p = proof("Catch-up keeps every missed time, also behind a run still going", ASKED)
    p.idea("With Misfire catch_up, when the computer wakes and several of a flow's times came while it slept, each "
           "of them now runs, oldest first, one after another, also when a run of the flow is still going: they "
           "wait behind it. Before, the oldest was refused (“Run skipped: the previous run is still going”), and "
           "only the later ones waited.")
    p.idea("Another agent found this case by checking a model of the scheduler (pull request #1). It was confirmed "
           "here: the new test fails without the change, and passes with it.")
    p.idea("A single new time that comes while a run is still going is not a catch-up: it still follows the flow's "
           "setting “If a scheduled run starts while the last one is still going”. By default that is “skip the new "
           "run”, so that a flow that takes longer than its interval does not pile up runs.")
    p.why_simplest("catch_up already ran several times one after another: every time but the first waited for the "
                   "one before it. Now the first waits too, when a run of the flow is still going. That is the "
                   "whole change: no new setting, and nothing changes for a single time.")
    p.alternative("Let every catch_up time wait behind a run still going, a single one too",
                  "Then catch_up would overrule “If a scheduled run starts while the last one is still going” at "
                  "every check, not only after a gap: a flow that runs longer than its interval would pile up runs "
                  "without end, which “skip the new run” is there to prevent.")
    p.alternative("Start the oldest time at once, beside the run still going",
                  "Two runs of the same flow at the same time, where catch_up promises “one after another”.")
    p.fits("The times wait in the same queue that “run it after the current one” uses: a run waiting there begins "
           "once the one before it has ended, at the service's next check (at most 15 seconds later).")
    p.fits("It is the README's promise for catch_up: “one run for each time, oldest first, one after another (at "
           "most 100)”.")
    day = dt.date.today() - dt.timedelta(days=1)

    def at(text: str) -> dt.datetime:
        return dt.datetime.combine(day, dt.time.fromisoformat(text))

    path = write(env.root / "catch-up.yaml", LONG)
    go = env.root / "go.txt"

    def whens() -> list[str]:
        return [f"{r['when']:%H:%M}" for r in runs(env)]

    p.step("Open catch-up.yaml (File > Open…) and save it (File > Save). Its Schedule block every_minute runs it "
           "every minute, with Misfire: catch_up. long_work stands for a long piece of work: it goes on until the "
           "file go.txt is next to the flow, and the proof creates that file when the run is to end.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu(win, "Open…")
    tab = win.current
    file_menu(win, "Save")
    select_schedule(win, qtbot, tab)
    p.shot(win, "① every_minute, with Misfire: catch_up. ② long_work, the long piece of work.",
           highlight=[misfire_row(win), "long_work"], badges=True, zoom=[misfire_row(win)])
    p.check("The flow is scheduled, with Misfire catch_up",
            tab.scheduled and tab.doc.blocks["every_minute"]["config"]["misfire"] == "catch_up")

    p.step(f"Let the scheduler service check at 06:30:00 and at 06:31:00 yesterday ({day:%Y-%m-%d}). At 06:31:00 "
           "it starts the 06:31 run, which keeps going.")
    p.note("Here the scheduler service's own checks run inside the proof, at times it chooses, yesterday morning: a "
           "proof cannot put the computer to sleep, nor wait for hours. The runs it starts are real, and the editor "
           "shows them as it shows every scheduled run. So Run history's Started is when the proof ran them; when, "
           "under Parameters, is the time each run is for. The countdown under every_minute reads the computer's "
           "clock.")
    service.check(at("06:30:00"))
    service.check(at("06:31:00"))
    qtbot.waitUntil(lambda: win.running_flows.text() == "1 flow running", timeout=20_000)
    p.shot(win, "① The 06:31 run is going: 1 flow running.", highlight=[win.running_flows], badges=True,
           zoom=[win.running_flows])
    p.check("The 06:31 run is going",
            [(f"{r['when']:%H:%M}", r["status"]) for r in runs(env)] == [("06:31", "running")])

    p.step("Put the computer to sleep until 06:33:00, then let the 06:31 run end: create go.txt. On waking, the "
           "scheduler service checks: 06:32 and 06:33 came while it slept, and the 06:31 run is still going. Once "
           "that run has ended, the service's next checks, at 06:33:15 and 06:33:30, start the 06:32 run, then the "
           "06:33 run. Then click the Run history tab.")
    service.check(at("06:33:00"))
    p.note("The editor shows a run once it has begun, not while it waits in the service's queue, as the 06:32 and "
           "06:33 runs do here until the 06:31 run has ended.")
    p.check("On waking, no run was refused, and the 06:31 run was still the only one",
            service.notices == [] and whens() == ["06:31"])
    go.write_text("go")
    for next_check in ("06:33:15", "06:33:30"):
        service.wait_for_its_runs_to_end()
        service.check(at(next_check))
    service.wait_for_its_runs_to_end()
    qtbot.waitUntil(lambda: editor_shows(env, tab) and len(finished(env, "catch-up")) == 3, timeout=20_000)
    click_tab(win.bottom_tabs, win.history_panel)
    done = finished(env, "catch-up")
    p.shot(win, "① Three runs, one for each time, oldest first: when 06:31, 06:32 and 06:33 (newest at the top). "
                "Each began once the one before had ended. ② long_work shows the latest: “the run for 06:33”.",
           highlight=[history_outline(win), "long_work"], badges=True, zoom=[history_area(win, header=True)])
    p.check("Three runs, for 06:31, 06:32 and 06:33, in that order, all success",
            [(f"{r['when']:%H:%M}", r["status"]) for r in done] == [("06:31", "success"), ("06:32", "success"),
                                                                     ("06:33", "success")])
    p.check("One after another: each began once the one before had ended",
            all(b["started"] >= a["finished"] for a, b in zip(done, done[1:])))
    p.check("Run history lists them, newest first, each with its time",
            [r[5] for r in history_rows(win)]
            == [f"when={at(t):%Y-%m-%d %H:%M:%S}" for t in ("06:33", "06:32", "06:31")])

    p.step("Click the Flow tab, on the right.")
    click_tab(win.right_tabs, win.flow_settings)
    overlap = win.flow_settings.overlap
    overlap_row = [win.flow_settings.form.labelForField(overlap), overlap]
    p.shot(win, "① This flow's setting “If a scheduled run starts while the last one is still going”: skip the new "
                "run, the default.", highlight=[overlap_row], badges=True, zoom=[overlap_row])
    p.check("The flow's setting is “skip the new run”", overlap.currentText() == "skip the new run")

    p.step("Let a single new time come while a run is going: delete go.txt, let the scheduler service check at "
           "06:34:00 and at 06:35:00, then create go.txt again. At 06:34:00 the 06:34 run starts, and keeps going; "
           "06:35 comes while it is going.")
    go.unlink()
    service.check(at("06:34:00"))
    qtbot.waitUntil(lambda: whens() == ["06:31", "06:32", "06:33", "06:34"], timeout=20_000)
    service.check(at("06:35:00"))
    go.write_text("go")
    service.wait_for_its_runs_to_end()
    qtbot.waitUntil(lambda: editor_shows(env, tab) and len(finished(env, "catch-up")) == 4, timeout=20_000)
    click_tab(win.bottom_tabs, win.history_panel)
    p.shot(win, "① The 06:34 run ran. 06:35, a single new time that came while it was going, did not: it was "
                "skipped, as the flow's setting says.",
           highlight=[history_outline(win)], badges=True, zoom=[history_area(win, header=True)])
    p.check("06:34 ran, and 06:35 did not", whens() == ["06:31", "06:32", "06:33", "06:34"])

    p.step("Read what the scheduler service told: its notifications.")
    p.text("The scheduler service's notifications, each with the time of the check that gave it",
           "\n".join(service.notices) or "(none)")
    p.check("One notification, at 06:35:00: “Run skipped”, for the single new time only",
            service.notices == ["06:35:00  Run skipped: catch-up: the previous run is still going"])
