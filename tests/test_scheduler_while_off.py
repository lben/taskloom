"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml) about times and runs
while it is off, asleep, starting or restarting: more missed Catch up times found before you answer the question about
them (SCH-9), a scheduled run whose process is gone told exactly once, by the editor or the service, whichever finds it
first (SCH-13), a time whose run was started early when you turned the service off just before it (SCH-9, SCH-8), a
turn-off asked for while the service starts (SCH-4), and a run going across a time zone change and a restart of the
service (SCH-11, SCH-16).

The counts use the service's checks (Scheduler.tick) at chosen clock times, with each run's process replaced at the
process boundary by a stand-in (test_scheduler_new_promises.FakeRuns). Where the behaviour crosses processes, the real
service, the real editor window and real run processes are used, and turning off is sent as the dialog's Proceed
sends it (request_turn_off). Only what cannot happen in a test is arranged: a run going when the PC restarted (its
process has gone since) as the run history and the service's list of runs going left it; the moment a service is
starting, held by a pause in its start until the test has turned it off; a schedule's next time a few seconds away (a
service before it first saw it a minute earlier: next_time_in); and the PC's time zone, as the TZ each process starts
with."""

import contextlib
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox  # noqa: E402

from taskloom.config import Home  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.scheduler import (RUNS_FILE, Scheduler, acquire_lock, paused, request_turn_off,  # noqa: E402
                                set_paused)
from tests.test_scheduler_mistakes_and_counts import (ASKED_AT, ON_TIME, catch_up_service, headless,  # noqa: E402
                                                      printed)
from tests.test_scheduler_new_promises import (HOURLY, FakeRun, FakeRuns, begins, drain, end_runs, ended,  # noqa: E402
                                               flow_yaml, going, history, hhmm, hold_until, missed_times,
                                               one_at_a_time, pump, says, schedule, statuses, utc, wait_until, write)
from tests.test_scheduler_start_at import (checked_after, record_process_starts, run_processes,  # noqa: E402
                                           start_service, stop_service, tray_messages)
from tests.test_scheduler_turn_off_and_restart import EVERY_MINUTE, block_errors, next_time_in, said_going  # noqa: E402


# --- stand-ins for run processes -------------------------------------------------------------------------------------

class Runs(FakeRuns):
    """FakeRuns, where the next run of a flow in `early` started early waits for its time until the test says it has
    come (at_its_time): its process waits meanwhile (its flow is held), then claims its time as `taskloom run
    --if-scheduled` does (FakeRun.reach_its_time). It begins (it says run_started, then run_finished, and goes on until
    its flow is let go), or it does not, e.g. as the service was turned off before its time: it prints why, records
    nothing, and ends with the exit code NOT_DUE."""

    def __init__(self):
        super().__init__()
        self.early: set[str] = set()

    def popen(self, args, *rest, **kwargs):
        run = super().popen(args, *rest, **kwargs)
        if isinstance(run, FakeRun) and run.claims and run.flow in self.early:
            self.early.discard(run.flow)
            self.holding.add(run.flow)
            run.waits = True
            run.beginning = [json.dumps(event) + "\n" for event in ({"event": "run_started", "run_id": 10 ** 6},
                                                                    {"event": "run_finished", "status": "success"})]
        return run


def at_its_time(runs: Runs, run: FakeRun) -> bool:
    """The run started early reaches its time: whether it began (it goes on until its flow is let go: here, it
    finishes); one that did not has ended."""
    began = run.reach_its_time()
    if not began:
        runs.holding.discard(run.flow)
    return began


@pytest.fixture
def fake_runs(monkeypatch):
    """`taskloom run` processes replaced by stand-ins (see Runs)."""
    runs = Runs()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


# --- SCH-9: more missed Catch up times found before you answer -------------------------------------------------------

EVERY_TIME = missed_times(439)  # every 10 minutes, 2026-01-05 00:10 to 2026-01-08 01:10
MISSED = [t for t in EVERY_TIME if t != ON_TIME]  # 438: all but 2026-01-06 01:10, which came while the service was on
WOKE = EVERY_TIME[-1] + dt.timedelta(minutes=5)  # 2026-01-08 01:15: two days after ON_TIME, every time 5+ min late


@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("restarts", [False, True],
                         ids=["the PC sleeps two days", "the service is back two days later"])
def test_times_missed_before_you_answer_are_asked_again_in_the_new_total_and_only_the_latest_100_of_all_run(
        env, fake_runs, restarts):
    """A Catch up schedule every 10 minutes missed 150 times (00:10 to 01:00 the next day); the check that finds them,
    3 s before 01:10, asks you, and 01:10 comes while the service is on and checking (it is not missed). Before you
    answer, the PC sleeps two days (the same service's next check comes then), or the service stops and is back two
    days later: 288 more times were missed (01:20 to 01:10 two days later). You are asked again, with the new total:
    438. Run only the latest 100: exactly the latest 100 of the 438 missed times run, oldest first, one at a time, and
    01:10 runs too, once (neither counted as missed nor dropped); you are told the other 338 were not run."""
    assert len(MISSED) == 438 and EVERY_TIME[150] == ON_TIME
    scheduler, told, asked = catch_up_service(env, someone_to_ask=True)
    drain(scheduler, ASKED_AT, fake_runs)
    drain(scheduler, ON_TIME, fake_runs)
    assert [(flow, count) for flow, count, _ in asked] == [("backlog", 150)], told
    if restarts:  # (the service before it is never checked again)
        scheduler = Scheduler(Home(env.home), lambda title, message: told.append(f"{title}: {message}"),
                              lambda flow, count, answer: asked.append((flow, count, answer)))
    drain(scheduler, WOKE, fake_runs)
    before_the_answer = begins(fake_runs.of("backlog"))

    assert [(flow, count) for flow, count, _ in asked] == [("backlog", 150), ("backlog", 438)], (
        f"two days later, with 438 times missed in all, you were asked {[count for _, count, _ in asked]}; "
        f"told: {told}")
    assert before_the_answer == [], f"runs began before you answered: {before_the_answer[:3]}..."
    asked[-1][2](100)  # Run only the latest 100
    drain(scheduler, WOKE, fake_runs)

    ran = begins(fake_runs.of("backlog"))
    on_time = utc([ON_TIME])[0]
    missed_ran = [t for t in ran if t != on_time]
    assert missed_ran == utc(MISSED[-100:]), (
        f"{len(missed_ran)} missed times ran, from {missed_ran[:1]}, not the latest 100 of the 438 oldest first, from "
        f"{utc(MISSED[-100:])[0]}; told: {told}")
    assert ran.count(on_time) == 1, f"01:10, which came while the service was on, ran {ran.count(on_time)} times"
    assert one_at_a_time(fake_runs.of("backlog")) == []
    about = [t for t in told if "backlog" in t]
    assert [t for t in about if says(t, 338)] and not [t for t in about if says(t, 50)], \
        f"not told that 338 were not run: {about}"


# --- SCH-13: a scheduled run whose process is gone, told exactly once -----------------------------------------------

YEARLY = {"cron": "0 2 1 1 *"}  # (its time never comes in a test)
FAILURE_MAILS = {"when": ["failure"], "to": ["team@example.com"]}
ADHOC = ("blocks:\n  manual:\n    type: trigger.manual\n  work:\n    type: logic.python\n"
         "    config: {inputs: [], outputs: [], code: pass}\nedges: [manual.start -> work.after]\n")


def gone_process() -> int:
    """The number of a process that has ended (as a run's has after the PC restarted)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=60)
    return proc.pid


def pc_restarted(home: Path, folder: Path):
    """Before the PC restarted, the scheduler service had started a run of nightly (its flow asks for failure emails)
    and the editor a run of adhoc (it has no schedule; started with its Manual block), both 10 minutes ago: the run
    history says both are running, as each run recorded itself (its process, the moment it began, and for nightly's
    that the service started it), and the service's list of runs going (scheduler.runs) lists nightly's, as they were
    left. The PC restarted: their processes are gone."""
    write(home / "settings.yaml", "smtp_connection: mailer\n")
    nightly = schedule(home, folder, {"nightly": flow_yaml(YEARLY, notify=FAILURE_MAILS)})["nightly"]
    adhoc = write(folder / "adhoc.yaml", ADHOC).resolve()
    began = dt.datetime.now() - dt.timedelta(minutes=10)
    started = began.isoformat(timespec="milliseconds")
    record = History(home / "history.db")
    try:
        runs = {"nightly": record.start_run("nightly", str(nightly), {}, started, started_by="s1", scheduled=True),
                "adhoc": record.start_run("adhoc", str(adhoc), {}, started, started_by="manual")}
        for name, block in (("nightly", "work1"), ("adhoc", "work")):
            record({"event": "block_started", "run_id": runs[name], "ts": started, "block": block, "attempt": 1})
    finally:
        record.close()
    gone = gone_process()
    with contextlib.closing(sqlite3.connect(home / "history.db", timeout=30)) as db:  # (as their own processes had it)
        db.executemany("UPDATE runs SET pid=?, began=? WHERE id=?",
                       [(gone, began.timestamp(), run) for run in runs.values()])
        db.commit()
    write(home / RUNS_FILE, json.dumps([{"flow": str(nightly), "name": "nightly", "run": runs["nightly"],
                                         "block": "s1", "schedule": "cron 0 2 1 1 *"}]))


class Told:
    """The message windows the editor shows that say "<flow> crashed", for each of `flows`: those shown through
    QMessageBox's functions (the editor fixture records them instead; they are taken from its record) and any other
    window (each is noted and closed once seen: none waits for a click). On macOS, Qt drops a message box's title:
    there, one whose text names the flow and a crash counts."""

    def __init__(self, win, flows: tuple[str, ...]):
        self.win, self.flows, self.shown = win, flows, []  # shown: (what it says, whether its title was dropped)
        self.timer = QTimer()
        self.timer.timeout.connect(self._look)
        self.timer.start(50)

    @staticmethod
    def _says(text: str, flow: str, untitled: bool) -> bool:
        return f"{flow} crashed" in text or (untitled and flow in text and "crash" in text.lower())

    def _look(self):
        for window in QApplication.topLevelWidgets():
            if window is self.win or not window.isVisible():
                continue
            text = " ".join([window.windowTitle(), *(label.text() for label in window.findChildren(QLabel))])
            untitled = sys.platform == "darwin" and isinstance(window, QMessageBox)
            if any(self._says(text, flow, untitled) for flow in self.flows):
                self.shown.append((text, untitled))
                window.reject() if isinstance(window, QDialog) else window.close()

    def _take_recorded(self):
        taken = [box for box in self.win.message_boxes
                 if any(self._says(" ".join(map(str, box[1])), flow, False) for flow in self.flows)]
        self.win.message_boxes[:] = [box for box in self.win.message_boxes if box not in taken]
        self.shown += [(" ".join(map(str, box[1])), False) for box in taken]

    def messages(self, flow: str) -> list[str]:
        self._take_recorded()
        return [text for text, untitled in self.shown if self._says(text, flow, untitled)]

    def stop(self):
        self.timer.stop()
        self._take_recorded()


def crash_mails(smtp, flow: str) -> list[tuple[str, str]]:
    """(subject, to) of the emails that say the flow crashed."""
    return [(str(m["Subject"]), str(m["To"])) for m in smtp.messages
            if f"{flow} crashed" in f"{m['Subject']} {m.get_body(('plain', 'html')).get_content()}"]


@pytest.mark.invariant("SCH-13")
@pytest.mark.parametrize("turned_off", [True, False], ids=[
    "you turned the service off, then open the editor", "the editor checks before a starting service takes its lock"])
def test_a_scheduled_run_whose_process_is_gone_is_recorded_crashed_and_told_exactly_once(env, smtp, win, turned_off):
    """A scheduled run of nightly was going when the PC restarted; its process is gone. You open the editor: with the
    service turned off by you, it stays off, and the editor records the run as crashed at once and tells you: a
    message window "nightly crashed", and an email to the team (the flow asks for failure emails); turning the service
    on later tells nothing more. With the service on, the editor's check comes before the service starting at the same
    time has taken its lock: the run is recorded as crashed, and you are told exactly once in all, by whichever found
    it first. A run the editor itself started (adhoc, not scheduled), whose process is gone too, is recorded as
    crashed, as today, and is not told."""
    home = env.home
    pc_restarted(home, env.root)
    if turned_off:
        set_paused(Home(home), True)
    told = Told(win, ("nightly", "adhoc"))
    service, at_once = None, None
    try:
        if turned_off:  # the editor's check comes every 2 s
            pump(lambda: told.messages("nightly") and crash_mails(smtp, "nightly"), 6)
            pump(lambda: False, 0.5)  # (a second message or email would have come by now)
            at_once = {"recorded": statuses(home, "nightly"), "told": told.messages("nightly"),
                       "emails": crash_mails(smtp, "nightly")}
            set_paused(Home(home), False)  # you turn it on (the editor's button turns it on so, then starts it)
        else:
            pump(lambda: False, 2.5)  # the editor's check (every 2 s) runs before any service holds the lock
        started = dt.datetime.now()
        service = start_service(env, tray=True)
        assert checked_after(env, started, until=started + dt.timedelta(seconds=30)), "the service did not check"
        pump(lambda: False, 2.5)  # (the service's checks, and the editor's, later)
        wait_until(lambda: crash_mails(smtp, "nightly"), 3)
        time.sleep(0.5)  # (a second email would have come by now)
        tray = [" ".join(message) for message in tray_messages(env)]
    finally:
        told.stop()
        if service is not None:
            stop_service(env, service)
        end_runs(home)

    if turned_off:
        assert at_once["recorded"] == ["failed"], f"the editor did not record the run as crashed: {at_once}"
        assert len(at_once["told"]) == 1, (f"the editor recorded nightly's run as crashed and told you "
                                           f"{len(at_once['told'])} times (a message window): {at_once['told']}")
        assert [to for _, to in at_once["emails"]] == ["team@example.com"], \
            f"not one email to the team saying nightly crashed: {at_once['emails']}"
    assert statuses(home, "nightly") == ["failed"] and [e for _, _, e in block_errors(home, "nightly")
                                                        if e and "crash" in e], \
        f"not recorded as crashed: {statuses(home, 'nightly')}, {block_errors(home, 'nightly')}"
    everywhere = told.messages("nightly") + [message for message in tray if "nightly crashed" in message]
    assert len(everywhere) == 1, f"told {len(everywhere)} times that nightly crashed, not once: {everywhere}"
    assert len(crash_mails(smtp, "nightly")) == 1, f"emails: {crash_mails(smtp, 'nightly')}"
    assert statuses(home, "adhoc") == ["failed"], "the run the editor started is not recorded as crashed, as today"
    adhoc_told = told.messages("adhoc") + [m for m in tray if "adhoc" in m] + crash_mails(smtp, "adhoc")
    assert adhoc_told == [], f"the run the editor started (not scheduled) was told: {adhoc_told}"


# --- SCH-9, SCH-8: a time whose run was started early, and the service turned off just before it ------------------

TODAY = dt.datetime(2026, 1, 5)


def clock(text: str) -> dt.datetime:
    """That time of day, today (HH:MM or HH:MM:SS)."""
    return dt.datetime.combine(TODAY, dt.time.fromisoformat(text))


def turned_off_before_its_time(env, runs: Runs, schedule_config: dict, began: bool) -> list[str]:
    """report runs on this schedule; the service first saw it at 06:59:30. At 06:59:56 its run for 07:00 is started
    early. You turn the service off (Proceed) at 06:59:57 if the run does not begin, else at 07:00:02, once it has
    begun (Let it finish); the service turns off once that run has ended. What the service told."""
    path = schedule(env.home, env.root, {"report": flow_yaml(schedule_config)})["report"]
    told = []
    service = Scheduler(Home(env.home), lambda title, message: told.append(f"{title}: {message}"))
    service.tick(clock("06:59:30"))
    runs.early.add("report")
    service.tick(clock("06:59:56"))
    (early,) = runs.of("report")
    assert begins([early]) == utc([clock("07:00")]), "the run for 07:00 was not started early"
    if not began:
        request_turn_off(Home(env.home), {})  # Proceed (no runs going: it only asked to confirm)
        service.tick(clock("06:59:58"))
    assert at_its_time(runs, early) == began, \
        f"at its time, the run for 07:00 {'did not begin' if began else 'began'}: {early.not_begun}"
    service.tick(clock("07:00:01"))
    if began:
        request_turn_off(Home(env.home), {str(path): "finish"})  # Proceed, Let it finish (the default)
        service.tick(clock("07:00:03"))
        runs.holding.discard("report")  # it finishes
    service.tick(clock("07:00:31"))
    assert service.off, "the service did not turn off"
    return told


@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-8")
@pytest.mark.parametrize("cron, misfire, on, ran, missed", [
    ("0 7 * * *", "run_once", "08:00", ["07:00"], None),
    ("0 7 * * *", "catch_up", "08:00", ["07:00"], None),
    ("0 7 * * *", "skip", "08:00", [], 1),
    ("* * * * *", "catch_up", "07:04:30", ["07:00", "07:01", "07:02", "07:03", "07:04"], None),
    ("* * * * *", "skip", "07:04:30", ["07:04"], 4),
], ids=["daily 07:00, Run once", "daily 07:00, Catch up", "daily 07:00, Skip", "every minute, Catch up",
        "every minute, Skip"])
def test_a_time_whose_run_did_not_begin_as_you_turned_the_service_off_just_before_is_missed_not_lost(
        env, fake_runs, cron, misfire, on, ran, missed):
    """report's run for 07:00 has been started early; you turn the service off (Proceed) at 06:59:57: the run does not
    begin, and the service turns off. You turn it on again later (`on`): 07:00 is a missed time like the others, under
    the schedule's Misfire: Run once runs it (a daily 07:00 turned on at 08:00 runs 07:00's run), Catch up includes
    it (oldest first, one at a time), Skip counts it in the number of missed runs you are told. No time runs twice."""
    told = turned_off_before_its_time(env, fake_runs, {"cron": cron, "misfire": misfire}, began=False)
    set_paused(Home(env.home), False)  # you turn it on: a new service starts
    before = len(fake_runs.started)
    service = Scheduler(Home(env.home), lambda title, message: told.append(f"{title}: {message}"))
    drain(service, clock(on), fake_runs)

    after = fake_runs.started[before:]
    began = begins(after)
    assert began == utc([clock(t) for t in ran]), (
        f"turned on at {on}, the runs that began are for {[hhmm(run) for run in after]}, not {ran}: 07:00, whose run "
        f"did not begin as you turned the service off just before it, is lost; told: {told}")
    assert len(set(began)) == len(began) and one_at_a_time(after) == []
    if missed:
        about = [t for t in told if "report" in t]
        stale = [t for t in about if says(t, missed - 1)] if missed > 1 else []
        assert [t for t in about if says(t, missed)] and not stale, \
            f"not told that {missed} runs were missed (07:00 among them): {about}"


@pytest.mark.invariant("SCH-6")
@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("misfire", ["run_once", "catch_up", "skip"])
def test_a_time_whose_run_began_before_you_turned_the_service_off_never_runs_again(env, fake_runs, misfire):
    """As above, but you turn the service off at 07:00:02, after report's run for 07:00 has begun (Let it finish): it
    finishes, and the service turns off. Turned on again at 08:00, 07:00 does not run again, and is not counted as
    missed."""
    told = turned_off_before_its_time(env, fake_runs, {"cron": "0 7 * * *", "misfire": misfire}, began=True)
    set_paused(Home(env.home), False)
    before = len(fake_runs.started)
    service = Scheduler(Home(env.home), lambda title, message: told.append(f"{title}: {message}"))
    drain(service, clock("08:00"), fake_runs)

    assert begins(fake_runs.started[before:]) == [], "07:00, whose run began and finished, ran again"
    assert not [t for t in told if "report" in t and ("missed" in t.lower() or "skipped" in t.lower())], told


@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-8")
def test_the_real_service_turned_off_just_before_a_time_runs_that_time_once_when_turned_on_again(env):
    """nightly runs every minute (Misfire Run once, the default), its next time a few seconds away, and the service
    (`taskloom scheduler --headless`) has started its run's process early. You turn the service off (Proceed) before
    that time: the run does not begin, and the service turns off. You turn it on again a few seconds after that time,
    before the next: that time was missed, so Run once runs it, once."""
    starts = record_process_starts(env)
    flows = schedule(env.home, env.root, {"nightly": flow_yaml(EVERY_MINUTE)})
    due = next_time_in(env.home, 10)
    services = [start_service(env, tray=False)]
    try:
        assert run_processes(starts, flows, due, until=due), f"no run process was started early for {due:%H:%M:%S}"
        request_turn_off(Home(env.home), {})  # Proceed (no runs going: it only asked to confirm)
        ahead = due.timestamp() - time.time()
        assert ahead > 0.5, f"turned off only {ahead:.2f} s before {due:%H:%M:%S}: too late to tell"
        assert wait_until(lambda: services[0].poll() is not None, 20), "the service did not turn off"
        not_begun = history(env.home)
        on_again = dt.datetime.now()
        services.append(start_service(env, tray=False))  # you turn it on again (as the editor's button starts it)
        wait_until(lambda: [r for r in history(env.home) if r["status"] != "running"], 15)
        time.sleep(2)  # (a second run for that time would have begun by now)
        runs = history(env.home)
    finally:
        for service in services:
            stop_service(env, service)
        end_runs(env.home)

    assert not_begun == [], f"the run started early began although you turned the service off: {not_begun}"
    ran = [(r["status"], r["when"] and dt.datetime.fromisoformat(r["when"])) for r in runs]
    assert ran == [("success", due)], (
        f"turned on again {(on_again - due).total_seconds():.1f} s after {due:%H:%M:%S}, the runs are "
        f"{ran}, not one run for {due:%H:%M:%S}: that time, whose run did not begin as you turned the service off "
        f"just before it, is lost")


# --- SCH-4: a turn-off asked for while the service starts -----------------------------------------------------------

# `taskloom scheduler --headless`, held once at the moment TEST_PAUSE_AT of its start until the file "go" appears in
# TEST_PAUSE_FOLDER (it writes "pausing" there once held): "lock", just after it took its lock; "scheduler", as it sets
# up its checks; "first check", just before its first check.
PAUSED_START = """
import os, time
from pathlib import Path

import taskloom.scheduler as scheduler
from taskloom.cli import run_cli

AT, FOLDER = os.environ["TEST_PAUSE_AT"], Path(os.environ["TEST_PAUSE_FOLDER"])
held = []


def pause(at):
    if at != AT or held:
        return
    held.append(at)
    (FOLDER / "pausing").write_text(at, encoding="utf-8")
    end = time.monotonic() + 60
    while not (FOLDER / "go").exists() and time.monotonic() < end:
        time.sleep(0.02)


acquire_lock, setup, tick = scheduler.acquire_lock, scheduler.Scheduler.__init__, scheduler.Scheduler.tick


def locked(home):
    handle = acquire_lock(home)
    pause("lock")
    return handle


def set_up(self, *args, **kwargs):
    pause("scheduler")
    setup(self, *args, **kwargs)


def check(self, *args, **kwargs):
    pause("first check")
    return tick(self, *args, **kwargs)


scheduler.acquire_lock, scheduler.Scheduler.__init__, scheduler.Scheduler.tick = locked, set_up, check
run_cli(["scheduler", "--headless"])
"""


def holds_lock(home: Path) -> bool:
    """A scheduler service holds the Taskloom folder's lock (so the editor takes it as running)."""
    try:
        acquire_lock(Home(home)).close()
        return False
    except RuntimeError:
        return True


@pytest.mark.invariant("SCH-4")
@pytest.mark.parametrize("moment", ["lock", "scheduler", "first check"], ids=[
    "just after it took its lock", "as it sets up its checks", "just before its first check"])
def test_a_turn_off_asked_for_while_the_service_starts_turns_it_off(env, moment):
    """The service is starting: it has taken its lock (so the editor takes it as running, and turning it off sends it
    the request) and has not checked yet. You turn it off then (Proceed; no runs going). It turns off as asked: it
    ends and stays off, and nothing new starts (soon, which runs as soon as the service sees it, does not run). It is
    never left marked off but still running."""
    schedule(env.home, env.root, {"soon": flow_yaml(HOURLY)})
    folder, log = env.root / "pause", env.root / "service.log"
    folder.mkdir()
    with open(log, "w", encoding="utf-8") as out:
        service = subprocess.Popen([sys.executable, "-c", PAUSED_START], cwd=env.root, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=subprocess.STDOUT,
                                   env={**env.env, "TEST_PAUSE_AT": moment, "TEST_PAUSE_FOLDER": str(folder)})
    try:
        wait_until(lambda: (folder / "pausing").exists() or service.poll() is not None, 30)
        starting = {"held there": (folder / "pausing").exists() and service.poll() is None,
                    "holds its lock": holds_lock(env.home),
                    "has checked": (env.home / "scheduler.heartbeat").exists()}
        request_turn_off(Home(env.home), {})  # Proceed
        write(folder / "go", "go")
        turned_off = wait_until(lambda: service.poll() is not None, 8)
        after = {"marked off": paused(Home(env.home)), "runs of soon": statuses(env.home, "soon")}
    finally:
        write(folder / "go", "go")
        stop_service(env, service)
        end_runs(env.home)

    assert starting == {"held there": True, "holds its lock": True, "has checked": False}, \
        f"the service was not held between its lock and its first check: {starting}\n{printed(log)}"
    assert turned_off, (f"turned off while it started ({moment}), the service goes on, "
                        f"{'marked off' if after['marked off'] else 'marked on'}; {after}\n{printed(log)}")
    assert after == {"marked off": True, "runs of soon": []}, after


# --- SCH-11, SCH-16: a run going across a time zone change and a restart of the service ----------------------------

WEST, EAST = "PST8", "JST-9"  # fixed UTC offsets as POSIX TZ values (8 h west, 9 h east of UTC), no daylight saving


def utc_offset(env, zone: str) -> int | None:
    """The UTC offset (seconds) a process started with the time zone `zone` (TZ) sees."""
    found = subprocess.run([sys.executable, "-c", "import datetime\n"
                            "print(int(datetime.datetime.now().astimezone().utcoffset().total_seconds()))"],
                           env={**env.env, "TZ": zone}, capture_output=True, text=True, timeout=60)
    return int(found.stdout) if found.stdout.strip() else None


@pytest.mark.invariant("SCH-11")
@pytest.mark.invariant("SCH-16")
def test_a_run_going_when_the_time_zone_changes_and_the_service_restarts_is_still_that_flows_run_going(env):
    """nightly's s1 run is going and its s2 run waits behind it (Overlap queue; saved). The PC's time zone changes from
    8 hours west of UTC to 9 hours east, and the service restarts (it stopped; the run goes on). The new service takes
    the live run as nightly's run going: it is not recorded or told as crashed, s2's run still waits for it, and
    starts once it has finished."""
    offsets = utc_offset(env, WEST), utc_offset(env, EAST)
    if offsets != (-8 * 3600, 9 * 3600):
        pytest.skip(f"a process here does not take its time zone from TZ (UTC offsets seen: {offsets})")
    schedule(env.home, env.root, {"nightly": flow_yaml(HOURLY, HOURLY, work=hold_until("nightly"), overlap="queue")})
    logs = [env.root / "service-west.log", env.root / "service-east.log"]
    service = headless(env, logs[0], TZ=WEST)
    try:
        assert wait_until(lambda: going(env.home) == {"nightly"}, 30), f"{history(env.home)}\n{printed(logs[0])}"
        assert wait_until(lambda: said_going(env.home), 10), "the service did not say which runs go"
        time.sleep(1.5)  # (a check later: s2's run waits its turn)
        stop_service(env, service)  # the service stops; the run goes on
        (live,) = history(env.home)
        assert not ended(live["pid"]), "the run ended with the service"
        restarted = dt.datetime.now()
        service = headless(env, logs[1], TZ=EAST)  # the time zone has changed
        assert checked_after(env, restarted, until=restarted + dt.timedelta(seconds=30)), \
            f"the service started again did not check:\n{printed(logs[1])}"
        time.sleep(2.5)  # (a few checks later)
        while_live = [r["status"] for r in history(env.home)]
        write(env.root / "nightly.go", "go")  # the live run finishes now
        wait_until(lambda: statuses(env.home, "nightly") == ["success", "success"], 30)
        runs = history(env.home)
    finally:
        stop_service(env, service)
        write(env.root / "nightly.go", "go")
        end_runs(env.home)

    crashes = [line for line in printed(logs[1]).splitlines() if "crash" in line.lower()]
    assert while_live == ["running"], (
        f"after the time zone changed and the service restarted, nightly's live run is recorded as "
        f"{while_live[:1]}, and {len(while_live) - 1} more of its runs started meanwhile: {while_live}; "
        f"told: {crashes}")
    assert crashes == [], f"told that the live run crashed: {crashes}"
    assert [r["status"] for r in runs] == ["success", "success"], runs
    # (each run recorded its times on its own clock: the first 8 h west of UTC, the second 9 h east)
    first_finished = runs[0]["finished"] + dt.timedelta(hours=8)
    second_started = runs[1]["started"] - dt.timedelta(hours=9)
    assert second_started >= first_finished, f"s2's run started before the live run finished: {runs}"
