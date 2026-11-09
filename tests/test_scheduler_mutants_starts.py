"""The scheduler service: starting a run's process and adopting runs going from before, checked where mutation
testing found the other tests let a planted bug through (docs/promises/scheduler.mutants.yaml). Each test keeps a
promise of docs/promises/scheduler.yaml or a behaviour the README states.

The service is the real Scheduler, made as `taskloom scheduler` makes it, checked at chosen clock times; its run
processes are the stand-ins of tests/test_scheduler_held_times.py (they claim their time as `taskloom run` does), or
real `taskloom run` processes where what the service reads from a run's process matters. A run that a service before
this one started is a real process, recorded in the run history as a run records itself as it begins."""

import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from taskloom.config import Home
from taskloom.flow import real_path
from taskloom.history import History
from taskloom.scheduler import STOP_SECONDS, request_turn_off
from tests.test_paths_scheduler import emails, service, write
from tests.test_paths_scheduler import flow as timed_flow
from tests.test_scheduler_held_times import YEARLY, Runs, about, check, clock, earlier_run_began
from tests.test_scheduler_new_promises import (HOURLY, REAL_POPEN, begins, flow_yaml, hhmm, schedule, statuses, utc,
                                               wait_until)
from tests.test_scheduler_promises import at, on

# (a program that ignores SIGTERM, on POSIX only)
SIGTERM_IGNORABLE = pytest.mark.skipif(sys.platform == "win32", reason=(
    "Windows has no SIGTERM a program can ignore: there it ends the program at once (TerminateProcess), so a "
    "run that does not stop when asked cannot happen this way"))


SLEEPS = "import time; time.sleep(60)"
IGNORES_SIGTERM = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\n" \
                  "time.sleep(60)"


@pytest.fixture
def fake_runs(monkeypatch) -> Runs:
    """Each `taskloom run` process the service starts is a stand-in (Runs, tests/test_scheduler_held_times.py)."""
    runs = Runs()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


@contextlib.contextmanager
def process(code: str = SLEEPS):
    """A real process (the program of a run that a service before this one started), ended at the end of this."""
    proc = REAL_POPEN([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        if "ready" in code:
            assert proc.stdout.readline().strip() == "ready"
        yield proc
    finally:
        proc.kill()
        proc.wait()
        proc.stdout.close()


def recorded_as_begun(home: Path, run, path: Path) -> int:
    """The stand-in's run begins as `taskloom run` begins one: it records itself in the run history (running, its
    process, the moment it began, its Schedule block and schedule); it has not said its run's id yet. Its id."""
    record = History(home / "history.db")
    try:
        run_id = record.start_run(run.flow, str(real_path(path)), {}, "2026-01-05T07:00:00",
                                  started_by=run.block or "", schedule=run.args[run.args.index("--scheduled") + 1])
    finally:
        record.close()
    with contextlib.closing(sqlite3.connect(home / "history.db", timeout=30)) as db:
        db.execute("UPDATE runs SET pid=?, began=? WHERE id=?", (run.pid, time.time(), run_id))
        db.commit()
    return run_id


def finished(home: Path, flow: str):
    """The flow's run going records its end, as a run that finishes does."""
    with contextlib.closing(sqlite3.connect(home / "history.db", timeout=30)) as db:
        db.execute("UPDATE runs SET status='success', finished='2026-01-05T08:01:00' WHERE flow=? AND "
                   "status='running'", (flow,))
        db.commit()


# --- starting a run's process ----------------------------------------------------------------------------------------

def test_a_run_whose_program_cannot_be_started_is_told_and_emailed_once_and_the_service_goes_on(env, smtp,
                                                                                                monkeypatch):
    """README (Notifications; the scheduler service notifies you about failed runs): nightly's time comes (06:31 each
    day) and the system cannot start its run's program (Taskloom's program is no longer where it was, e.g. removed
    while the service runs). You are told once, "nightly could not start", with why in the system's own words; the
    flow's notify asks for failure emails to team@example.com, so one email goes there. The service goes on: later
    checks do not try that run again."""
    write(env.home / "settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    gone = env.root / "removed" / "taskloom"
    try:
        REAL_POPEN([str(gone)])
    except OSError as e:
        why = e.strerror
    else:
        pytest.fail(f"{gone} started")

    def popen(args, *rest, **kwargs):  # (a `taskloom run` command: its program is the one removed)
        if isinstance(args, list) and "run" in map(str, args):
            args = [str(gone)] + args[list(map(str, args)).index("run"):]
        return REAL_POPEN(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    with on(env):
        scheduler, notes = service(env, {"nightly.yaml": timed_flow(
            "31 6 * * *", notify={"when": ["failure"], "to": ["team@example.com"]})})
        for moment in ("06:30:00", "06:31:00", "06:31:01", "06:31:30"):
            scheduler.tick(at(moment))
        scheduler.wait_for_runs()

    assert [title for title, _ in notes] == ["nightly could not start"], notes
    message = notes[0][1]
    assert message.startswith("nightly") and message.endswith(f"({why})"), \
        f"not told why in the system's words ({why}): {message!r}"
    assert emails(smtp) == [("[Taskloom] nightly could not start", "team@example.com")], emails(smtp)
    assert statuses(env.home, "nightly") == []


@pytest.mark.invariant("SCH-13")
def test_a_crashed_run_is_told_with_what_it_wrote_last_also_on_its_error_output(env):
    """A real scheduled run whose program writes why it fails on its error output, as Java does, then dies: you are
    told "crash crashed", and the message says what it wrote."""
    why = "java.lang.OutOfMemoryError: Java heap space"
    with on(env):
        scheduler, notes = service(env, {"crash.yaml": timed_flow(code=f"import os\nos.write(2, b'{why}\\n')\n"
                                                                       "os._exit(1)")})
        scheduler.tick(at("06:30:00"))
        scheduler.tick(at("06:31:00"))
        scheduler.wait_for_runs()

    assert [title for title, _ in notes] == ["crash crashed"] and why in notes[0][1], notes


@pytest.mark.invariant("SCH-13")
def test_a_run_of_this_service_is_recorded_crashed_and_told_once_only_after_its_process_died(env, fake_runs):
    """nightly's run (every 60 minutes: it runs as soon as the service sees it) begins: it records itself in the run
    history, with its process, and only then says its run's id. A check between the two finds it going and the
    service's own (known by its process): nothing is told, and it is still recorded as going. Then its process dies
    without finishing: at the next check it is recorded as crashed, and you are told once: nightly crashed."""
    path = schedule(env.home, env.root, {"nightly": flow_yaml(HOURLY)})["nightly"]
    fake_runs.loading.add("nightly")  # (its program loads until the test says it has)
    told = []
    scheduler = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    check(scheduler, fake_runs, clock("07:00:00"))
    (run,) = fake_runs.of("nightly")
    run.beginning = []  # (what it says as it begins comes below)
    assert run.reach_its_time(), f"nightly's run did not begin: {run.not_begun}"
    run_id = recorded_as_begun(env.home, run, path)
    check(scheduler, fake_runs, clock("07:00:01"))
    assert statuses(env.home, "nightly") == ["running"] and about(told, "nightly") == [], (
        f"nightly's run, going and not yet saying its id: recorded {statuses(env.home, 'nightly')}; told: {told}")

    run.stdout.say(json.dumps({"event": "run_started", "run_id": run_id}) + "\n")
    check(scheduler, fake_runs, clock("07:00:02"))
    run.end(-9)  # (e.g. the system ended it)
    check(scheduler, fake_runs, clock("07:00:03"))

    assert statuses(env.home, "nightly") == ["failed"], statuses(env.home, "nightly")
    assert [m.split(":")[0] for m in about(told, "nightly")] == ["nightly crashed"], told


@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("going", ["started by this service", "started by a service before this one"])
def test_a_missed_catch_up_time_with_overlap_allow_waits_for_the_run_of_its_schedule_going(env, fake_runs, going):
    """nightly runs at :00 every hour (Catch up, Overlap allow); its 07:00 run goes on. At 08:05 (the PC slept) its
    08:00 time was missed: catch-up runs go one at a time, so it waits for the 07:00 run, also one that a service
    before this one started (this one knows it from the run history), and starts once that run has ended, with no
    other run of nightly going."""
    path = schedule(env.home, env.root, {"nightly": flow_yaml({"cron": "0 * * * *", "misfire": "catch_up"},
                                                              overlap="allow")})["nightly"]
    fake_runs.holding.add("nightly")
    scheduler = fake_runs.service(env.home)
    for moment in ("06:59:59", "07:00:00"):
        check(scheduler, fake_runs, clock(moment))
    (first,) = fake_runs.of("nightly")
    with process() as its_process:
        if going == "started by a service before this one":
            earlier_run_began(env.home, path, its_process.pid)  # (its program, as it records itself)
            scheduler = fake_runs.service(env.home)  # (that service stopped; this one started)
        check(scheduler, fake_runs, clock("08:05:00"))
        assert begins(fake_runs.of("nightly")) == utc([clock("07:00:00")]), (
            f"with nightly's 07:00 run going ({going}), runs began for {begins(fake_runs.of('nightly'))}")

        first.end(0)  # the 07:00 run ends
        finished(env.home, "nightly")
    check(scheduler, fake_runs, clock("08:05:10"))

    assert begins(fake_runs.of("nightly")) == utc([clock("07:00:00"), clock("08:00:00")]), \
        begins(fake_runs.of("nightly"))
    assert fake_runs.of("nightly")[-1].alongside == [], "the 08:00 run went alongside the 07:00 one"


# --- runs going from before ------------------------------------------------------------------------------------------

@pytest.mark.invariant("SCH-11")
@pytest.mark.invariant("SCH-12")
def test_a_run_from_a_service_before_this_one_counts_once_for_its_flow_however_its_path_is_spelled(env, fake_runs):
    """Max concurrent runs 2. nightly (every minute, Overlap skip) is listed in scheduled.yaml under another spelling
    of its path (flows/../nightly.yaml, as a hand edit may write it), and its runs record its one real path. A run of
    it that a service before this one started is going. This service checks a few times, then 07:01 comes: nightly's
    07:01 is skipped, as its run is going, and you are told; third's 07:01 starts, as the run from before counts once:
    2 runs go."""
    (env.home / "settings.yaml").write_text("max_concurrent_runs: 2\n", encoding="utf-8")
    paths = schedule(env.home, env.root, {"nightly": flow_yaml({"cron": "* * * * *"}),
                                          "third": flow_yaml({"cron": "1 7 * * *"})})
    (env.root / "flows").mkdir()
    Home(env.home).set_scheduled(paths["nightly"], False)
    Home(env.home).set_scheduled(Path(f"{env.root / 'flows'}{os.sep}..{os.sep}nightly.yaml"), True)
    told = []
    with process() as its_process:
        earlier_run_began(env.home, paths["nightly"], its_process.pid)
        scheduler = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
        for moment in ("07:00:50", "07:00:56", "07:01:00"):
            check(scheduler, fake_runs, clock(moment))
        started = [(r.flow, hhmm(r)) for r in fake_runs.started]

    assert started == [("third", "07:01")], f"with nightly's run from before going, the service started {started}"
    assert [m for m in about(told, "nightly") if "skip" in m.lower()], f"not told nightly's 07:01 was skipped: {told}"


@pytest.mark.invariant("SCH-13")
def test_a_run_from_before_of_a_flow_taken_off_the_schedule_is_recorded_crashed_and_told_once(env, fake_runs):
    """README (A run that crashes: also for a run that was going when the service started again): nightly's run, which
    a service before this one started, is going; you have taken nightly off the schedule since. This service finds it
    in the run history. Its process then dies without finishing: at the next check the run is recorded as crashed,
    and you are told once: nightly crashed."""
    path = schedule(env.home, env.root, {"nightly": flow_yaml(YEARLY)})["nightly"]
    Home(env.home).set_scheduled(path, False)
    told = []
    with process() as its_process:
        earlier_run_began(env.home, path, its_process.pid)
        scheduler = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
        check(scheduler, fake_runs, clock("07:00:30"))
        assert statuses(env.home, "nightly") == ["running"] and told == [], told
    check(scheduler, fake_runs, clock("07:00:31"))

    assert statuses(env.home, "nightly") == ["failed"], statuses(env.home, "nightly")
    assert [m.split(":")[0] for m in about(told, "nightly")] == ["nightly crashed"], told


@pytest.mark.invariant("SCH-4")
@pytest.mark.parametrize("its_program", [
    pytest.param("does not stop when asked", marks=SIGTERM_IGNORABLE),
    "ends just as it is asked to stop"])
def test_stop_now_ends_a_run_from_a_service_before_this_one_and_then_the_service_turns_off(env, fake_runs,
                                                                                           monkeypatch, its_program):
    """nightly's run, which a service before this one started, is going; its program does not stop on SIGTERM. You
    turn the service off with Stop now for nightly (Proceed). The run is recorded as cancelled; its program is forced
    to end STOP_SECONDS later, by the service's clock; or it ends at the very moment the service asks it to stop. Either
    way the service then turns off, and nothing is told as failed or crashed."""
    path = schedule(env.home, env.root, {"nightly": flow_yaml(YEARLY)})["nightly"]
    passed = [0.0]  # (seconds the service's clock is ahead)
    monkeypatch.setattr("taskloom.scheduler.time", types.SimpleNamespace(
        **{**vars(time), "monotonic": lambda: time.monotonic() + passed[0]}))
    told = []
    with process(IGNORES_SIGTERM) as its_process:
        earlier_run_began(env.home, path, its_process.pid)
        scheduler = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
        check(scheduler, fake_runs, clock("07:00:30"))
        request_turn_off(Home(env.home), {str(path): "stop"})
        if its_program == "ends just as it is asked to stop":
            kill = os.kill

            def ends_first(pid, sig):
                if pid == its_process.pid and sig != 0 and its_process.returncode is None:
                    kill(pid, signal.SIGKILL)
                    its_process.wait()
                return kill(pid, sig)

            monkeypatch.setattr(os, "kill", ends_first)
        check(scheduler, fake_runs, clock("07:00:31"))
        if its_program == "does not stop when asked":
            assert its_process.poll() is None, "its program, which ignores SIGTERM, ended before it was forced"
            passed[0] = STOP_SECONDS + 1
            check(scheduler, fake_runs, clock("07:00:38"))
        assert wait_until(lambda: its_process.poll() is not None, 5), "the run's program was not ended"
    check(scheduler, fake_runs, clock("07:00:40"))

    assert statuses(env.home, "nightly") == ["cancelled"], statuses(env.home, "nightly")
    assert scheduler.off, "the service did not turn off once nightly's run had ended"
    assert told == [], told
