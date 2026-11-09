"""The scheduler service: the service's settings, its state in the history, and turning it off and on, checked where
mutation testing found the other tests let a planted bug through (docs/promises/scheduler.mutants.yaml). Each test
keeps a promise of docs/promises/scheduler.yaml or a behaviour the README states.

Each service is made as `taskloom scheduler` makes one (the Taskloom folder's lock first: FakeRuns.service), with its
runs' processes replaced at the process boundary by stand-ins that claim as `taskloom run` claims (FakeRuns). Turning
the service off and on is sent as the editor and the tray send it (request_turn_off, set_paused); runs going are
recorded in the run history as `taskloom run` records them (History.start_run)."""

import contextlib
import datetime as dt
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from taskloom import __version__
from taskloom.config import Home
from taskloom.flow import FORMAT_VERSION, FlowError, load_flow, real_path
from taskloom.history import History
from taskloom.scheduler import (_unlock, acquire_lock, forget_schedules, paused, request_turn_off, runs_going,
                                service_running, set_paused)
from tests.test_scheduler_at_its_time import clock
from tests.test_scheduler_new_promises import (HOURLY, NO_TZ_ON_WINDOWS, REAL_POPEN, FakeRuns, begins, clock_shows,
                                               drain, flow_yaml, schedule, statuses, utc, write)

UTC = dt.timezone.utc
SETTINGS_MISTAKE = "Mistake in the settings"
MEANWHILE = ". Until it is put right, Scheduled runs at once is not applied, and the keeper checks no server."


@pytest.fixture
def fake_runs(monkeypatch) -> FakeRuns:
    runs = FakeRuns()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


@pytest.fixture
def berlin():
    """The local time zone is Europe/Berlin: 2026-10-25 03:00 CEST becomes 02:00 CET (02:00-03:00 comes twice)."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


def record_run(home: Path, flow: str, path: Path, schedule: str | None, pid: int | None = None) -> int:
    """A run of the flow records itself in the run history as `taskloom run` does as it begins: going, with its process
    (`pid`; by default this one) and the moment it began; started by the scheduler service for `schedule`, or by you
    (None). Its id."""
    record = History(home / "history.db")
    try:
        run_id = record.start_run(flow, str(real_path(path)), {}, dt.datetime.now().isoformat(),
                                  started_by="s1" if schedule else "", schedule=schedule)
    finally:
        record.close()
    if pid is not None:
        with contextlib.closing(sqlite3.connect(home / "history.db", timeout=30)) as db:
            db.execute("UPDATE runs SET pid=?, began=? WHERE id=?", (pid, time.time(), run_id))
            db.commit()
    return run_id


# --- SCH-14: mistakes in the settings, the list of scheduled flows and a scheduled flow -----------------------------

@pytest.mark.invariant("SCH-14")
def test_a_mistake_in_the_settings_is_told_with_how_to_put_it_right_each_time_the_file_changes(env, fake_runs):
    """README: a mistake in settings.yaml (Scheduled runs at once not a whole number, a keeper setting that is not a
    list of servers) is told once, until the file changes, and meanwhile Scheduled runs at once is not applied and the
    keeper checks no server. The keeper checks (as its thread does) and the service checks, twice for each state of
    settings.yaml: first a folder of that name (it cannot be read), then a file with a stray word in it, then
    max_concurrent_runs: lots, then keeper: appserver. Each is told once, as "Mistake in the settings", with what is
    wrong and what is not applied meanwhile; the last two say which setting and how it is written right."""
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append((title, message)))
    settings = env.home / "settings.yaml"
    states = [lambda: settings.mkdir(), lambda: (settings.rmdir(), settings.write_bytes(b"XXXX")),
              lambda: settings.write_text("max_concurrent_runs: lots\n", encoding="utf-8"),
              lambda: settings.write_text("keeper: appserver\n", encoding="utf-8")]
    for n, change in enumerate(states):
        change()
        for second in range(2):
            service.check_servers()
            service.tick(clock(f"10:00:{2 * n + second:02}"))
        assert len(told) == n + 1, f"settings.yaml changed {n + 1} time(s), each to a mistake: told {told}"

    assert [title for title, _ in told] == [SETTINGS_MISTAKE] * 4, told
    assert all(message.endswith(MEANWHILE) for _, message in told), told
    lots, keeper = told[2][1], told[3][1]
    assert "max_concurrent_runs" in lots and "whole number" in lots and "'lots'" in lots, lots
    assert "settings.yaml: keeper must be a list of servers" in keeper, keeper
    assert "each with its connection, command and home (Tools → Settings writes it so)" in keeper, keeper


@pytest.mark.invariant("SCH-14")
def test_a_mistake_in_the_list_of_scheduled_flows_names_the_lists_file(env, fake_runs):
    """README: an entry in scheduled.yaml that is not a flow file's path (a number, after a hand edit) is left out, and
    you are told once: the message names the file to put right, by its path in the Taskloom folder."""
    write(env.home / "scheduled.yaml", "flows:\n  - 5\n")
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append((title, message)))
    service.tick(clock("10:00:00"))

    (title, message), = told
    assert title == "Mistake in the list of scheduled flows", told
    assert message.startswith(f"{Home(env.home).root / 'scheduled.yaml'}: "), message


@pytest.mark.invariant("SCH-14")
def test_a_flow_in_a_newer_format_says_a_newer_taskloom_may_have_saved_it_and_a_plain_mistake_says_only_why(
        env, fake_runs):
    """A scheduled flow saved in a newer flow format than this Taskloom reads: the service says it cannot be read, why,
    and that a newer Taskloom may have saved it (restart the scheduler from it). One with a plain mistake (a minute
    past 59 in its cron line) is told as its file's name and why, and nothing more."""
    paths = schedule(env.home, env.root, {"newer": flow_yaml(HOURLY, taskloom=FORMAT_VERSION + 1),
                                          "mistaken": flow_yaml({"cron": "61 * * * *"})})
    with pytest.raises(FlowError) as mistake:
        load_flow(paths["mistaken"])
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append((title, message)))
    service.tick(clock("10:00:00"))

    said = {message.split(":")[0]: message for title, message in told if title == "Scheduled flow cannot be read"}
    assert sorted(said) == ["mistaken.yaml", "newer.yaml"], told
    assert said["newer.yaml"].startswith("newer.yaml: unsupported flow format"), said
    assert said["newer.yaml"].endswith(f"(this scheduler is Taskloom {__version__}: if a newer Taskloom saved the "
                                       "flow, restart the scheduler from it)"), said
    assert said["mistaken.yaml"] == f"mistaken.yaml: {mistake.value.errors[0]}", said


@pytest.mark.invariant("SCH-14")
def test_a_scheduled_flow_that_was_missing_runs_once_it_is_back_whatever_its_files_date(env, fake_runs):
    """late is scheduled (every 60 minutes: it runs as soon as the service sees it), but its file is missing as the
    service checks: you are told it cannot be read, and the service goes on. Then the file is back, dated 1970-01-01
    00:00:01 UTC (as files unpacked from a reproducible archive are): at the next check its run starts."""
    path = schedule(env.home, env.root, {"late": flow_yaml(HOURLY)})["late"]
    text = path.read_text(encoding="utf-8")
    path.unlink()
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append((title, message)))
    drain(service, clock("10:00:00"), fake_runs)
    assert [title for title, _ in told] == ["Scheduled flow cannot be read"], told
    write(path, text)
    os.utime(path, (1, 1))
    drain(service, clock("10:00:01"), fake_runs)

    assert begins(fake_runs.of("late")) == utc([clock("10:00:01")]), told


# --- SCH-1: one service per Taskloom folder ------------------------------------------------------------------------

@pytest.mark.invariant("SCH-1")
def test_starting_a_second_service_while_one_runs_changes_nothing_for_the_runs_of_the_one_on(env, fake_runs):
    """A service runs (it holds the Taskloom folder's lock); `taskloom scheduler` started again meanwhile does nothing:
    it says one is already running. The service on goes on as before: nightly's run (every 60 minutes: as soon as the
    service sees it) starts and begins at its time."""
    schedule(env.home, env.root, {"nightly": flow_yaml(HOURLY)})
    service = fake_runs.service(env.home)
    with pytest.raises(RuntimeError, match="already running"):
        acquire_lock(Home(env.home))
    for moment in ("10:00:00", "10:00:01", "10:00:02"):
        with clock_shows(clock(moment)):
            service.tick(clock(moment))

    runs = fake_runs.of("nightly")
    assert [(run.reached, run.not_begun) for run in runs] == [(True, None)], [run.not_begun for run in runs]
    assert begins(runs) == utc([clock("10:00:00")])


@pytest.mark.invariant("SCH-1")
@pytest.mark.parametrize("first", ["a service starts", "a look whether one runs"])
def test_the_service_holds_the_folders_lock_by_the_name_the_keeper_looks_for(env, first):
    """README: the keeper replaces a hung scheduler on a server by stopping the process that holds that Taskloom
    folder's `scheduler.lock` (found through /proc or lsof, on a disk that tells upper and lower case apart). In a new
    Taskloom folder, a service starts, or first something looks whether one runs (as the editor does) and then one
    starts: the folder's lock is the file scheduler.lock, by that name, and the service holds it."""
    home = Home(env.home)
    if first == "a look whether one runs":
        assert not service_running(home)
    lock = acquire_lock(home)
    try:
        assert "scheduler.lock" in os.listdir(env.home), os.listdir(env.home)
        assert service_running(home)
    finally:
        _unlock(lock)


# --- SCH-2: turned off, it stays off; only you turn it on ------------------------------------------------------------

@pytest.mark.invariant("SCH-2")
def test_proceed_marks_the_folder_off_by_the_name_every_start_looks_for(env):
    """Proceed (no runs going) marks the Taskloom folder off: the file scheduler.paused, by that name, which every start
    of the service looks for (`taskloom scheduler --unless-paused` at login, the editor), also on a disk that tells
    upper and lower case apart. The service is off until you turn it on."""
    request_turn_off(Home(env.home), {})

    assert "scheduler.paused" in os.listdir(env.home), os.listdir(env.home)
    assert paused(Home(env.home))


@pytest.mark.invariant("SCH-2")
def test_turning_the_service_on_from_two_places_at_once_turns_it_on_without_a_failure(env):
    """The service is off. You turn it on in the editor just as `taskloom scheduler`, started by you in a terminal,
    turns it on too, while another process is writing the run history (both wait for it, after each has seen the
    service off). Neither fails, and the service is on."""
    History(env.home / "history.db").close()
    home = Home(env.home)
    request_turn_off(home, {})
    failures = []

    def turn_on():
        try:
            set_paused(home, False)
        except Exception as e:  # (what the editor or `taskloom scheduler` would show you)
            failures.append(e)

    writing = sqlite3.connect(env.home / "history.db", timeout=30, isolation_level=None)
    try:
        writing.execute("BEGIN IMMEDIATE")
        both = [threading.Thread(target=turn_on) for _ in range(2)]
        for place in both:
            place.start()
        time.sleep(0.5)  # (each has seen the service off, and waits for the history)
        writing.execute("COMMIT")
        for place in both:
            place.join(30)
    finally:
        writing.close()

    assert failures == [], failures
    assert not paused(home)


# --- SCH-4: turning the service off with runs going ----------------------------------------------------------------

@pytest.mark.invariant("SCH-4")
def test_the_turn_off_question_lists_only_the_flows_whose_scheduled_runs_are_going(env):
    """The Turn off question lists each flow with runs going that the service started (runs_going, as the editor and
    the tray ask it): nightly's scheduled run is going. Not a run you started yourself (manual's, going), nor a
    scheduled run whose process has gone (gone's: it crashed, and is not recorded so yet)."""
    paths = schedule(env.home, env.root, {name: flow_yaml(HOURLY) for name in ("nightly", "manual", "gone")})
    record_run(env.home, "nightly", paths["nightly"], "every 60 minutes")
    record_run(env.home, "manual", paths["manual"], None)
    record_run(env.home, "gone", paths["gone"], "every 60 minutes", pid=2 ** 30)  # (no process has it)

    assert runs_going(Home(env.home)) == [(str(paths["nightly"]), "nightly")]


@pytest.mark.invariant("SCH-4")
def test_stop_now_stops_every_flows_runs_also_after_one_whose_process_has_just_ended(env, monkeypatch):
    """No service runs as you click Proceed with Stop now for first and second, each with a scheduled run going. As it
    is stopped, first's process has just ended on its own (the system says there is no such process). second's run is
    stopped all the same, and recorded as cancelled."""
    paths = schedule(env.home, env.root, {name: flow_yaml(HOURLY) for name in ("first", "second")})
    processes = {name: REAL_POPEN([sys.executable, "-c", "import time; time.sleep(60)"]) for name in paths}
    try:
        for name, path in paths.items():
            record_run(env.home, name, path, "every 60 minutes", pid=processes[name].pid)
        real_kill = os.kill

        def kill(pid, sig):
            if pid == processes["first"].pid and sig == signal.SIGTERM:  # it ends just before it is told to stop
                processes["first"].kill()
                processes["first"].wait(15)
                raise ProcessLookupError(3, "No such process")
            real_kill(pid, sig)

        monkeypatch.setattr(os, "kill", kill)
        request_turn_off(Home(env.home), {str(path): "stop" for path in paths.values()})
        monkeypatch.undo()
        assert statuses(env.home, "second") == ["cancelled"]
        processes["second"].wait(15)  # (told to stop, it ends)
    finally:
        for process in processes.values():
            process.kill()
            process.wait(15)


# --- SCH-10: a schedule turned on starts afresh from its save --------------------------------------------------------

@NO_TZ_ON_WINDOWS
@pytest.mark.invariant("SCH-10")
@pytest.mark.invariant("SCH-15")
def test_a_clock_schedule_saved_in_the_hour_the_clock_shows_twice_starts_from_that_save(env, fake_runs, berlin):
    """In Berlin, 2026-10-25 02:00-03:00 comes twice (CEST, then CET). With the service off, at 02:40 CET (the second
    time) you save report with a new schedule, every quarter hour: it starts from that save. The service is on at
    02:41 CET: 02:45 CEST, which came before the save, neither runs nor counts as missed (SCH-10), nor do the quarter
    hours shown a second time (SCH-15). Its next run is 03:00 CET."""
    path = schedule(env.home, env.root, {"report": flow_yaml({"cron": "0 5 * * *"})})["report"]
    write(path, flow_yaml({"cron": "*/15 * * * *"}))
    with clock_shows(dt.datetime(2026, 10, 25, 1, 40, tzinfo=UTC)):  # 02:40 CET
        forget_schedules(Home(env.home), path, load_flow(path).triggers)
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    for minute in (41, 50):
        drain(service, dt.datetime(2026, 10, 25, 1, minute, tzinfo=UTC), fake_runs)
    assert fake_runs.of("report") == [] and told == [], (told, [r.start_at for r in fake_runs.started])
    drain(service, dt.datetime(2026, 10, 25, 2, 0, tzinfo=UTC), fake_runs)  # 03:00 CET

    assert begins(fake_runs.of("report")) == ["2026-10-25 02:00:00"], told
