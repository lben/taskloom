"""The scheduler service: a run's end, its crash or stop, and the saved runs it leaves, checked where mutation testing
found the other tests let a planted bug through (docs/promises/scheduler.mutants.yaml). Each test keeps a promise of
docs/promises/scheduler.yaml or a behaviour the README states.

The service is the real Scheduler, made as `taskloom scheduler` makes it and checked at chosen clock times; each run's
process is replaced at the process boundary by a stand-in that claims its time as the real run does (Run, in
tests/test_scheduler_held_times.py). Here a stand-in can also record itself in the run history as it begins, as the
real run does (Begins), and keep its output open after its process has ended."""

import contextlib
import datetime as dt
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import taskloom
from taskloom.config import Home
from taskloom.flow import load_flow, real_path
from taskloom.history import History
from taskloom.scheduler import forget_schedules, request_turn_off, set_paused, tell
from tests.test_scheduler_held_times import Run, Runs, check, clock, for_time, one_off
from tests.test_scheduler_new_promises import (EVERY_TEN, FIRST_SIGHT, REAL_POPEN, begins, check_after, clock_shows,
                                               flow_yaml, says, schedule, statuses, utc, write)

# (a program that ignores SIGTERM, on POSIX only)
SIGTERM_IGNORABLE = pytest.mark.skipif(sys.platform == "win32", reason=(
    "Windows has no SIGTERM a program can ignore: there it ends the program at once (TerminateProcess), so a "
    "run that does not stop when asked cannot happen this way"))


T = clock("07:00:00")
FAILURE_EMAILS = {"when": ["failure"], "to": ["team@example.com"]}


# --- stand-ins for run processes -------------------------------------------------------------------------------------

class Begins(Run):
    """Run (see there). One of a flow in Stand.recording records itself in the run history as it begins, as the real
    run does from its own process (History.start_run: going, with its schedule), says its run id (run_started) and goes
    on until its process ends; ended without finishing (killed), its end is not recorded: it crashed. Its process
    number is the stand-in's, which no process has: whoever else looks for its process by it (Stop now's own stop,
    the editor) finds none. One of a flow in Stand.output_kept keeps its output open once its process has ended (a
    program it started holds it). As the service next looks at one of a flow in Stand.as_looked_at, what is there for
    that flow happens (e.g. you click Proceed then, in the editor's process)."""

    def poll(self):
        if self.flow in self._runs.as_looked_at:
            self._runs.as_looked_at.pop(self.flow)()
        return super().poll()

    def reach_its_time(self) -> bool:
        recording = self.flow in self._runs.recording
        if recording:
            self.beginning = []  # (it says it began once it has recorded so: below)
        began = super().reach_its_time()
        if began and recording:
            self.stdout.say(self.record())
        return began

    def record(self) -> str:
        history = History(self.home / "history.db")
        try:
            run_id = history.start_run(self.flow, str(real_path(Path(self.args[self.args.index("run") + 1]))), {},
                                       dt.datetime.now().isoformat(timespec="seconds"),
                                       schedule=self.args[self.args.index("--scheduled") + 1])
        finally:
            history.close()
        with contextlib.closing(sqlite3.connect(self.home / "history.db")) as db, db:
            db.execute("UPDATE runs SET pid=? WHERE id=?", (self.pid, run_id))
        return json.dumps({"event": "run_started", "run_id": run_id}) + "\n"

    def kill(self):
        self.recorded_as_ended = statuses(self.home, self.flow)  # what the run history says as its process is ended
        super().kill()

    def end(self, code: int):
        if self.flow in self._runs.output_kept and self.returncode is None:
            self.returncode = code  # (its output stays open)
            return
        super().end(code)


class Stand(Runs):
    def __init__(self):
        super().__init__()
        self.recording: set[str] = set()
        self.output_kept: set[str] = set()
        self.as_looked_at: dict = {}  # {flow: what happens as the service next looks at a run of it (see Begins)}

    def popen(self, args, *rest, **kwargs):
        if not (isinstance(args, list) and "run" in map(str, args) and "--start-at" in map(str, args)):
            return REAL_POPEN(args, *rest, **kwargs)
        run = Begins([str(a) for a in args], self, Path((kwargs.get("env") or os.environ)["TASKLOOM_HOME"]))
        if run.flow in self.loading:
            self.loading.discard(run.flow)
            self.holding.add(run.flow)  # (its process goes on until it has loaded)
            run.waits = True
        self.started.append(run)
        return run


@pytest.fixture
def runs(monkeypatch) -> Stand:
    stand_ins = Stand()
    monkeypatch.setattr(subprocess, "Popen", stand_ins.popen)
    yield stand_ins
    stand_ins.end()


def tick(service, runs: Stand, moment: dt.datetime):
    """One check of the service at `moment` (this PC's clock shows it meanwhile)."""
    runs.clock = moment
    with clock_shows(moment):
        service.tick(moment)


def at(service, runs: Stand, moment: dt.datetime):
    """The service, which first saw the schedules 10 s before `moment` (a schedule starts from when it is first seen),
    checks at `moment` (see check)."""
    check(service, runs, moment - dt.timedelta(seconds=10))
    check(service, runs, moment)


def until_off(service, runs: Stand, moment: dt.datetime, checks: int = 20):
    """The service checks at `moment` as `taskloom scheduler` does, until it is off; then it ends (it lets go of the
    Taskloom folder's lock), and a program it left loading loads with no service on."""
    for _ in range(checks):
        if service.off:
            break
        tick(service, runs, moment)
    runs.stop()
    for run in runs.started:
        run.poll()


def run_id_of(home: Path, flow: str) -> int:
    with contextlib.closing(sqlite3.connect(home / "history.db")) as db:
        [(run_id,)] = db.execute("SELECT id FROM runs WHERE flow=?", (flow,)).fetchall()
    return run_id


def crashed(told: list[str], flow: str) -> list[str]:
    return [t for t in told if t.startswith(f"{flow} crashed:")]


# --- a run's end: crashed --------------------------------------------------------------------------------------------

@pytest.mark.invariant("SCH-13")
def test_a_run_that_crashes_is_told_with_its_exit_code_and_an_email_that_cannot_be_sent_is_told_too(env, runs):
    """nightly's run begins at 07:00, then its process is killed (exit code -9) without finishing or printing why. It
    is recorded as crashed, and you are told "nightly crashed" with its exit code. Its flow asks for failure emails, and
    the mail connection the settings name is gone: you are told that the email could not be sent."""
    write(env.home / "settings.yaml", "smtp_connection: mailer\n")  # (connections.yaml has no such connection)
    schedule(env.home, env.root, {"nightly": flow_yaml(one_off(T), notify=FAILURE_EMAILS)})
    runs.recording.add("nightly")
    runs.holding.add("nightly")
    told = []
    service = runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    at(service, runs, T)
    [run] = runs.of("nightly")
    assert run.not_begun is None and statuses(env.home, "nightly") == ["running"], run.not_begun

    run.end(-9)
    check(service, runs, T + dt.timedelta(seconds=5))
    service.wait_for_runs(10)  # (the email being sent)

    assert statuses(env.home, "nightly") == ["failed"]
    [message] = crashed(told, "nightly") or [None]
    assert message and "exit code" in message and says(message, 9), f"not told the exit code: {told}"
    assert [t for t in told if t.startswith("Cannot send email:")], f"not told the email was not sent: {told}"


@pytest.mark.invariant("SCH-13")
def test_a_run_going_when_the_service_started_again_whose_program_has_gone_is_told_as_crashed(env, runs):
    """README (A run that crashes): its error says "crashed: the run process stopped unexpectedly", and you get a
    desktop message, also for a run that was going when the service started again. nightly's run began under a service
    that then stopped without warning; by the time a new service starts its program has gone (the PC restarted)."""
    schedule(env.home, env.root, {"nightly": flow_yaml(one_off(T))})
    runs.recording.add("nightly")
    runs.holding.add("nightly")
    at(runs.service(env.home), runs, T)
    [run] = runs.of("nightly")
    run.end(-9)  # (gone with the PC)

    told = []
    again = runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    check(again, runs, T + dt.timedelta(minutes=5))

    assert statuses(env.home, "nightly") == ["failed"]
    assert crashed(told, "nightly") == ["nightly crashed: the run process stopped unexpectedly"], told


@pytest.mark.invariant("SCH-13")
def test_a_crash_that_was_recorded_and_told_first_elsewhere_is_not_told_again(env, runs):
    """README: the service and the editor both look for runs whose program has gone; whichever finds one first records
    it and tells you, once. nightly's run began and its process is killed; the editor finds it first (it records it
    as crashed, and tells you itself); the service, checking next, does not tell you again."""
    schedule(env.home, env.root, {"nightly": flow_yaml(one_off(T))})
    runs.recording.add("nightly")
    runs.holding.add("nightly")
    told = []
    service = runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    at(service, runs, T)
    [run] = runs.of("nightly")
    run.end(-9)
    history = History(env.home / "history.db")  # (the editor, as MainWindow._show_running_flows does)
    try:
        assert history.mark_crashed(run_id_of(env.home, "nightly"), dt.datetime.now().isoformat())
    finally:
        history.close()

    check(service, runs, T + dt.timedelta(seconds=5))

    assert statuses(env.home, "nightly") == ["failed"]
    assert crashed(told, "nightly") == [], f"told again by the service: {told}"


# --- a run's end: stopped ------------------------------------------------------------------------------------------

@pytest.mark.invariant("SCH-4")
@SIGTERM_IGNORABLE
def test_a_run_that_does_not_stop_after_stop_now_is_recorded_as_cancelled_before_it_is_forced_to_end(env, runs,
                                                                                                     monkeypatch):
    """README (Turning the service off): Stop now: its runs going stop and are recorded as cancelled (a run that does
    not stop within a few seconds is forced to end, and is recorded as cancelled all the same). nightly's run began
    and ignores the request to stop, and Stop now's own stop cannot find its process: the service forces it to end,
    and it is recorded as cancelled before its process is ended, so whoever finds the process gone does not take it for
    a crash. You are not told it crashed."""
    monkeypatch.setattr("taskloom.scheduler.STOP_SECONDS", 0)  # ("a few seconds": none here)
    paths = schedule(env.home, env.root, {"nightly": flow_yaml(one_off(T))})
    runs.recording.add("nightly")
    runs.holding.add("nightly")  # (it does not stop when asked)
    told = []
    service = runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    at(service, runs, T)
    [run] = runs.of("nightly")

    request_turn_off(Home(env.home), {str(paths["nightly"]): "stop"})
    check(service, runs, T + dt.timedelta(seconds=5))

    assert getattr(run, "recorded_as_ended", None) == ["cancelled"], "not recorded as cancelled as it was forced to end"
    assert statuses(env.home, "nightly") == ["cancelled"]
    assert crashed(told, "nightly") == [], told


@pytest.mark.invariant("SCH-4")
def test_stop_now_clicked_as_a_check_begins_drops_the_time_that_check_then_starts_for_good(env, runs):
    """README (Turning the service off): for a flow you chose Stop now, its time is dropped: it neither runs later nor
    counts as missed; for the other flows its time is not lost: once the service is on again, it follows misfire.
    stopped's run for 06:59 is going (Overlap allow). At 07:00 you click Proceed with Stop now for stopped as the
    service's check has just looked for your choices (as it looks at stopped's run going): that check still starts the
    07:00 runs of first and of stopped. Neither begins (the service is off). Turned on again at 07:00:30, first's 07:00
    runs, once; stopped's never does."""
    paths = schedule(env.home, env.root, {"first": flow_yaml(one_off(T)), "stopped": flow_yaml(
        one_off(T - dt.timedelta(minutes=1)), one_off(T), overlap="allow")})
    runs.holding.add("stopped")
    told = []
    service = runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    at(service, runs, T - dt.timedelta(minutes=1))
    runs.as_looked_at["stopped"] = lambda: request_turn_off(Home(env.home), {str(paths["stopped"]): "stop"})
    tick(service, runs, T)
    assert [len(for_time(runs.of(flow), T)) for flow in ("first", "stopped")] == [1, 1], "both 07:00 runs started"
    tick(service, runs, T)  # (the service applies Stop now)
    runs.holding.discard("stopped")  # (its run going stops)
    until_off(service, runs, T + dt.timedelta(seconds=2))
    assert service.off and begins(for_time(runs.started, T)) == [], told

    set_paused(Home(env.home), False)
    check(runs.service(env.home, lambda title, message: told.append(f"{title}: {message}")), runs,
          T + dt.timedelta(seconds=30))

    assert begins(for_time(runs.of("first"), T)) == utc([T]), told
    assert begins(for_time(runs.of("stopped"), T)) == [], f"stopped's 07:00, dropped by Stop now, ran: {told}"


@pytest.mark.invariant("SCH-4")
def test_let_it_finish_runs_the_flows_waiting_run_before_the_service_turns_off(env, runs):
    """SCH-4: Let it finish: its runs going finish, and its runs waiting their turn still run; the service turns off
    when the last run you let finish is done. nightly (Overlap queue) has two schedules at 07:00: s1's run goes, s2's
    waits behind it. You turn the service off with Let it finish for nightly; then s1's run ends. s2's run still begins,
    before the service is off (then it ends)."""
    paths = schedule(env.home, env.root, {"nightly": flow_yaml(one_off(T), one_off(T), overlap="queue")})
    runs.holding.add("nightly")
    service = runs.service(env.home)
    at(service, runs, T)
    assert len(runs.of("nightly")) == 1

    request_turn_off(Home(env.home), {str(paths["nightly"]): "finish"})
    runs.holding.discard("nightly")
    until_off(service, runs, T + dt.timedelta(seconds=5))

    assert service.off
    assert begins(runs.of("nightly")) == utc([T, T]), "s2's run, let finish, did not begin before the service was off"


@pytest.mark.invariant("SCH-4")
def test_the_service_turns_off_at_the_check_that_finds_the_last_run_let_finish_did_not_begin(env, runs):
    """SCH-4: the service turns off when the last run you let finish is done; README: a waiting run whose schedule you
    change meanwhile is dropped. nightly (Overlap queue) has two schedules at 07:00: s1's run goes, s2's waits behind
    it. You turn the service off with Let it finish for nightly; s1's run ends and s2's starts, and while its program
    loads you save nightly with s2's schedule changed, so it does not begin: the check that finds it ended turns the
    service off."""
    paths = schedule(env.home, env.root, {"nightly": flow_yaml(one_off(T), one_off(T), overlap="queue")})
    runs.holding.add("nightly")
    service = runs.service(env.home)
    at(service, runs, T)
    request_turn_off(Home(env.home), {str(paths["nightly"]): "finish"})
    runs.holding.discard("nightly")
    runs.loading.add("nightly")
    tick(service, runs, T + dt.timedelta(seconds=1))
    [_, s2] = runs.of("nightly")

    write(paths["nightly"], flow_yaml(one_off(T), one_off(T + dt.timedelta(hours=1)), overlap="queue"))
    forget_schedules(Home(env.home), paths["nightly"], load_flow(paths["nightly"]).triggers)  # (the editor's save)
    assert not s2.reach_its_time(), "s2's run began after its schedule changed"
    runs.holding.discard("nightly")
    tick(service, runs, T + dt.timedelta(seconds=2))

    assert service.off, "the service did not turn off once the last run let finish was done"


# --- a run that has not begun ----------------------------------------------------------------------------------------

def test_a_program_loading_for_exactly_2_minutes_is_not_stopped_and_its_run_begins(env, runs):
    """README: a run does not begin if its program has loaded more than 2 minutes after it started; a program the
    service finds still loading more than 2 minutes after it started is asked to stop. nightly's run for 07:00 (Misfire
    Skip) loads in exactly 2 minutes, by this PC's clock: the service checking then does not stop it, its run begins,
    for 07:00, and nothing is said to be skipped or missed."""
    schedule(env.home, env.root, {"nightly": flow_yaml(one_off(T, misfire="skip"))})
    runs.loading.add("nightly")
    told = []
    service = runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    at(service, runs, T)
    [run] = runs.of("nightly")
    check(service, runs, T + dt.timedelta(minutes=2))  # (still loading)

    assert run.reach_its_time(), f"its run did not begin: {run.not_begun}"
    runs.holding.discard("nightly")
    check(service, runs, T + dt.timedelta(minutes=2, seconds=1))

    assert begins(runs.of("nightly")) == utc([T]) and len(runs.of("nightly")) == 1
    assert [t for t in told if "nightly" in t] == [], told


@pytest.mark.invariant("SCH-16")
def test_a_time_whose_program_was_loading_when_the_service_restarted_runs_once_under_the_new_service(env, runs):
    """README: if the service restarts before a run has begun (it was ended or crashed, and started again), the run the
    earlier service started does not begin: the service now on sees to that time itself. nightly's run for 07:00 is
    still loading when the service stops without warning; a new one starts 20 s later and runs 07:00, once."""
    schedule(env.home, env.root, {"nightly": flow_yaml(one_off(T))})
    runs.loading.add("nightly")
    at(runs.service(env.home), runs, T)
    [loading] = runs.of("nightly")

    check(runs.service(env.home), runs, T + dt.timedelta(seconds=20))
    assert not loading.reach_its_time(), "the program the earlier service started began"
    runs.holding.discard("nightly")

    assert begins(runs.of("nightly")) == utc([T]), [r.not_begun for r in runs.of("nightly")]


# --- the saved runs a run or a service leaves ------------------------------------------------------------------------

@pytest.mark.invariant("SCH-10")
def test_waiting_runs_of_flows_taken_off_while_the_service_was_stopped_do_not_run_once_they_are_back(env, runs):
    """README: a waiting run whose schedule you turn off or change meanwhile is dropped: it would not run as it was; a
    schedule turned back on starts afresh. At 01:05 line (Overlap queue) has two schedules due: s1's run is still
    loading and s2's waits behind it; backlog (Catch up) has missed 150 times, and you have not answered yet. The
    service stops without warning, both flows are taken off the list, and a new service checks; then both are listed
    again and the service starts again: none of those runs starts, and you are not asked about backlog again."""
    paths = schedule(env.home, env.root, {"line": flow_yaml({"cron": "5 1 * * *"}, {"cron": "5 1 * * *"},
                                                            overlap="queue"),
                                          "backlog": flow_yaml(EVERY_TEN)})
    asked = []

    def ask(flow, count, answer):
        asked.append((flow, count))

    runs.loading.add("line")
    before = runs.service(env.home, ask=ask)
    check(before, runs, FIRST_SIGHT)
    check(before, runs, check_after(150))
    assert len(runs.of("line")) == 1 and asked == [("backlog", 150)], asked

    for path in paths.values():
        Home(env.home).set_scheduled(path, False)
    check(runs.service(env.home, ask=ask), runs, check_after(150) + dt.timedelta(seconds=20))
    for path in paths.values():
        Home(env.home).set_scheduled(path, True)
    check(runs.service(env.home, ask=ask), runs, check_after(150) + dt.timedelta(seconds=40))

    assert len(runs.of("line")) == 1, f"line's runs from before it was taken off started: {begins(runs.of('line'))}"
    assert asked == [("backlog", 150)], asked


@pytest.mark.invariant("SCH-14")
def test_a_waiting_run_starts_only_once_the_list_of_scheduled_flows_can_be_read_again(env, runs):
    """README: if scheduled.yaml cannot be read at all, you are told, and no scheduled flow starts until it is put
    right; the runs going go on. nightly (Overlap queue) has two schedules at 07:00: s1's run goes, s2's waits behind
    it. A hand edit breaks the list, and s1's run ends: s2's does not start; once the list is put right, it runs."""
    schedule(env.home, env.root, {"nightly": flow_yaml(one_off(T), one_off(T), overlap="queue")})
    listed = (env.home / "scheduled.yaml").read_text(encoding="utf-8")
    runs.holding.add("nightly")
    service = runs.service(env.home)
    at(service, runs, T)

    write(env.home / "scheduled.yaml", "flows: 5\n")
    runs.holding.discard("nightly")
    check(service, runs, T + dt.timedelta(seconds=10))
    assert len(runs.of("nightly")) == 1, "a run started while the list of scheduled flows could not be read"

    write(env.home / "scheduled.yaml", listed)
    check(service, runs, T + dt.timedelta(seconds=20))
    assert begins(runs.of("nightly")) == utc([T, T]) and len(runs.of("nightly")) == 2


# --- what holds the service up, and what it leaves for the keeper --------------------------------------------------

@pytest.mark.invariant("SCH-5")
def test_a_run_whose_output_stays_open_after_its_program_ended_holds_the_service_up_at_most_5_seconds(env, runs):
    """SCH-5: a run begins at most 5 seconds after its time while the service is on. nightly's program ends, but a
    program it started keeps its output open: the service's check that finds it ended waits at most 5 seconds for the
    run's last lines, never until that other program ends."""
    schedule(env.home, env.root, {"nightly": flow_yaml(one_off(T))})
    runs.holding.add("nightly")
    runs.output_kept.add("nightly")
    service = runs.service(env.home)
    at(service, runs, T)
    [run] = runs.of("nightly")
    runs.holding.discard("nightly")

    checking = threading.Thread(target=tick, args=(service, runs, T + dt.timedelta(seconds=1)), daemon=True)
    try:
        checking.start()
        checking.join(5.9)
        assert not checking.is_alive(), "the check that found the run ended was held up for more than 5 seconds"
    finally:
        run.stdout.end()  # (the other program ends)
        checking.join(10)


def test_each_check_leaves_its_heartbeat_under_the_name_the_keeper_reads(env, runs):
    """README (keeper): your scheduler starts a server's scheduler again, or replaces it once it has hung (stopped
    checking). It tells a check by the file scheduler.heartbeat in the server's Taskloom folder, read by that name (on
    a server, file names are case-sensitive): each check writes it, with the time of the check (seconds since 1970)."""
    service = runs.service(env.home)
    before = int(time.time())
    tick(service, runs, T)

    assert "scheduler.heartbeat" in os.listdir(env.home)
    assert before <= int((env.home / "scheduler.heartbeat").read_text()) <= time.time()


TOLD_THEN_ENDS = """\
import sys
from pathlib import Path

from taskloom.config import Home
from taskloom.scheduler import tell

tell(Home(Path(sys.argv[1])), {"when": ["failure"], "to": ["team@example.com"]}, "failure", "nightly failed", "boom",
     lambda title, message: print(f"{title}: {message}", flush=True), lambda *told: None)
"""


def test_a_mail_server_that_does_not_answer_holds_up_neither_the_message_nor_the_program_ending(env):
    """README (A mail server that does not answer): emails are sent on their own, so a mail server that does not answer
    holds nothing up. nightly asks for failure emails, through a mail server that takes the connection and never
    answers: you are told on the desktop at once, and a program that told you so ends at once (it does not wait for
    that server)."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    write(env.home / "settings.yaml", "smtp_connection: mailer\n")
    write(env.home / "connections.yaml", f"mailer:\n  kind: smtp\n  host: 127.0.0.1\n  port: {server.getsockname()[1]}"
                                         "\n  sender: taskloom@example.com\n")
    root = str(Path(taskloom.__file__).resolve().parents[1])  # (the Taskloom these tests import)
    try:
        told, started = [], time.monotonic()
        tell(Home(env.home), FAILURE_EMAILS, "failure", "nightly failed", "boom",
             lambda title, message: told.append(f"{title}: {message}"), lambda *_: None)
        assert told == ["nightly failed: boom"] and time.monotonic() - started < 2

        ended = subprocess.run([sys.executable, "-c", TOLD_THEN_ENDS, str(env.home)], cwd=root, capture_output=True,
                               text=True, timeout=20, env={**env.env, "PYTHONPATH": root})
        assert ended.returncode == 0 and ended.stdout == "nightly failed: boom\n", ended.stderr
    finally:
        server.close()
