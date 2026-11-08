"""Proof (pull request #1): catch-up keeps every missed time, also behind a run still going. How proofs are written:
README.md here. (Its proof "After a gap, a check just before a run's time keeps the on-time run", of a check that
started a run's program 5 seconds before its time, was withdrawn on 2026-11-07: no run's program is started before its
time now.)"""

import datetime as dt
import os

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QComboBox, QFileDialog, QLabel, QToolBar

from proofs.test_proof_scheduler_start import click_block, click_tab, file_menu
from taskloom import __version__
from taskloom.scheduler import Scheduler, acquire_lock
from tests.test_scheduler_start_at import runs, write

ASKED = "a different agent found this ... Review it and verify if it is correct indeed, if so worth merging no?"
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
    does (scheduler.serve), it holds the lock and says which Taskloom it is (so the editor shows it on, and a run it
    starts begins), then checks with the same scheduler. Its notifications are kept, with each check's time."""

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
