"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml), in cases CI found: on
Windows, a run whose recorded process is not the one the service started, as when a venv's python.exe is a launcher
(SCH-12), and scheduled.yaml held open for a moment as the editor replaces it, which Windows does not let a reader
read (SCH-14, SCH-16; the file held open is a stand-in: its reads raise PermissionError, as on Windows); and a service
that holds the lock before it says which process and Taskloom it is (SCH-1).

The times use the service's checks (Scheduler.tick) at chosen clock times, the service made as `taskloom scheduler`
makes it (FakeRuns.service), with each run's process replaced at the process boundary by a stand-in that claims as the
real run does (test_scheduler_held_times.Runs)."""

import contextlib
import datetime as dt
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from taskloom import __version__, cli
from taskloom.config import Home
from taskloom.flow import load_flow, real_path
from taskloom.history import History
from taskloom.scheduler import LIST_UNREADABLE, _unlock, acquire_lock, running_scheduler_version, set_paused
from tests.test_scheduler_held_times import Runs, check, clock, for_time
from tests.test_scheduler_new_promises import (REAL_POPEN, FakeRun, begins, clock_shows, flow_yaml, run_command_line,
                                               schedule, utc)


@pytest.fixture
def fake_runs(monkeypatch) -> Runs:
    runs = Runs()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


def began_through_a_launcher(home: Path, run: FakeRun, child: subprocess.Popen) -> int:
    """The stand-in's run begins as `taskloom run` begins through a launcher: it claims its time and records itself in
    the run history in one transaction (History.start_run), with its own process `child`, not the stand-in's. It has
    not said its run's id yet. Its run's id."""
    args = run_command_line(run.args, home)
    flow = load_flow(real_path(Path(args.flow)))
    start_at = dt.datetime.fromisoformat(args.start_at)
    history = History(home / "history.db")
    try:
        with clock_shows(start_at), contextlib.redirect_stderr(io.StringIO()) as said:
            run_id = history.start_run(flow.name, str(flow.path), {}, start_at.isoformat(), started_by=args.trigger,
                                       schedule=args.scheduled, claim=cli._claim(Home(home), flow, args, start_at))
    except SystemExit:
        pytest.fail(f"not arranged: {flow.name}'s run did not begin: {said.getvalue()}")
    finally:
        history.close()
    with contextlib.closing(sqlite3.connect(home / "history.db", timeout=30)) as db:
        db.execute("UPDATE runs SET pid=?, began=? WHERE id=?", (child.pid, time.time(), run_id))
        db.commit()
    return run_id


@pytest.mark.invariant("SCH-12")
def test_a_run_whose_recorded_process_is_a_launchers_child_counts_once_for_max_concurrent_runs(env, fake_runs):
    """Max concurrent runs 2. nightly runs at 07:00 and other at 07:01. nightly's run begins through a launcher: its
    recorded process is not the one the service started. The service checks once before it reads that run's id, which
    it reads then. One run goes at 07:01, so other's run starts at its time."""
    (env.home / "settings.yaml").write_text("max_concurrent_runs: 2\n", encoding="utf-8")
    schedule(env.home, env.root, {"nightly": flow_yaml({"cron": "0 7 * * *"}),
                                  "other": flow_yaml({"cron": "1 7 * * *"})})
    told_you = []
    fake_runs.holding.add("nightly")  # its run goes on
    fake_runs.loading.add("nightly")  # its program loads until it begins (began_through_a_launcher)
    service = fake_runs.service(env.home, lambda title, message: told_you.append(f"{title}: {message}"))
    child = REAL_POPEN([sys.executable, "-c", "import time; time.sleep(120)"])  # (the launcher's child)
    try:
        for moment in ("06:59:50", "07:00:00"):
            check(service, fake_runs, clock(moment))
        (nightly,) = fake_runs.of("nightly")
        run_id = began_through_a_launcher(env.home, nightly, child)
        check(service, fake_runs, clock("07:00:01"))  # (before it reads what the run says)
        nightly.stdout.say(json.dumps({"event": "run_started", "run_id": run_id}) + "\n")
        check(service, fake_runs, clock("07:01:00"))
        started = for_time(fake_runs.of("other"), clock("07:01:00"))
    finally:
        child.kill()
        child.wait(timeout=15)

    assert started, (f"with only nightly's run going (Max concurrent runs 2), other's run did not start at 07:01: the "
                     f"service counts {len(service.runs)} runs going; told: {told_you}")


class ListInUse:
    """scheduled.yaml held open by another program, as Windows has it while the editor replaces it (os.replace): the
    next `times` reads of it raise PermissionError."""

    def __init__(self, monkeypatch):
        self.times = 0
        read = Path.read_text

        def read_text(path, *args, **kwargs):
            if path.name == "scheduled.yaml" and self.times > 0:
                self.times -= 1
                raise PermissionError(13, "The process cannot access the file because it is being used by another "
                                          "process", str(path))
            return read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)


@pytest.mark.invariant("SCH-14")
def test_the_list_held_open_for_a_moment_is_not_told_as_unreadable_and_the_flow_runs(env, fake_runs, monkeypatch):
    """nightly runs at 07:00. As the service checks at 07:00, scheduled.yaml is held open for a moment: its next 3
    reads fail, then it reads as usual. You are not told the list cannot be read, and nightly runs at 07:00."""
    schedule(env.home, env.root, {"nightly": flow_yaml({"cron": "0 7 * * *"})})
    in_use = ListInUse(monkeypatch)
    told_you = []
    service = fake_runs.service(env.home, lambda title, message: told_you.append(f"{title}: {message}"))
    check(service, fake_runs, clock("06:59:50"))
    in_use.times = 3
    check(service, fake_runs, clock("07:00:00"))

    unreadable = [t for t in told_you if LIST_UNREADABLE in t]
    assert unreadable == [], f"the list was held open for a moment only, yet you were told: {unreadable}"
    ran = begins(for_time(fake_runs.of("nightly"), clock("07:00:00")))
    assert ran == utc([clock("07:00:00")]), f"nightly did not run at 07:00: {ran}; told: {told_you}"


@pytest.mark.invariant("SCH-16")
def test_a_waiting_run_refused_while_the_list_cannot_be_read_is_kept_and_runs_later(env, fake_runs, monkeypatch):
    """nightly (Overlap queue) runs at 07:00 and 07:01; its 07:01 waits behind its 07:00 run. That run ends, and the
    service starts the waiting run. While its program loads, you turn the service off and on again, so its claim is
    refused. The service's next check finds it ended while scheduled.yaml cannot be read. The waiting run is kept:
    once the list reads again, it starts again and runs."""
    schedule(env.home, env.root, {"nightly": flow_yaml({"cron": "0,1 7 * * *"}, overlap="queue")})
    in_use = ListInUse(monkeypatch)
    told_you = []
    fake_runs.holding.add("nightly")  # its run goes on until let go
    service = fake_runs.service(env.home, lambda title, message: told_you.append(f"{title}: {message}"))
    for moment in ("06:59:50", "07:00:00", "07:01:00"):
        check(service, fake_runs, clock(moment))
    assert begins(fake_runs.of("nightly")) == utc([clock("07:00:00")]), f"not arranged: {told_you}"
    assert env.query("SELECT waits FROM waiting_runs") == [("flow",)], f"not arranged: 07:01 does not wait: {told_you}"
    fake_runs.loading.add("nightly")  # the waiting run's program loads until it has loaded (reach_its_time)
    fake_runs.holding.discard("nightly")  # the 07:00 run ends
    check(service, fake_runs, clock("07:01:10"))
    (waiting,) = for_time(fake_runs.of("nightly"), clock("07:01:00"))
    set_paused(Home(env.home), True)  # you turn the service off (no runs going: you only confirm)
    set_paused(Home(env.home), False)  # and on again at once
    assert not waiting.reach_its_time(), f"not arranged: the waiting run began after off and on: {told_you}"

    in_use.times = 10 ** 6  # the list cannot be read during the next check
    check(service, fake_runs, clock("07:01:12"))
    in_use.times = 0
    for moment in ("07:01:13", "07:01:14"):
        check(service, fake_runs, clock(moment))

    ran = begins(for_time(fake_runs.of("nightly"), clock("07:01:00")))
    assert ran == utc([clock("07:01:00")]), (
        f"nightly's waiting 07:01 run, whose process ended while the list could not be read, did not run once the list "
        f"read again: {ran}; told: {told_you}")


@pytest.mark.invariant("SCH-1")
def test_a_service_holding_the_lock_says_which_process_and_taskloom_it_is(env):
    """A service takes the Taskloom folder's lock first, as `taskloom scheduler` does (acquire_lock). As soon as it
    holds it, whoever sees a service running sees which process it is (scheduler.pid) and which Taskloom (its
    version): the editor can stop or restart it, and does not take it for an older one."""
    home = Home(env.home)
    lock = acquire_lock(home)
    try:
        pid_file = env.home / "scheduler.pid"
        said = pid_file.read_text(encoding="utf-8").strip() if pid_file.exists() else None
        version = running_scheduler_version(home)
    finally:
        _unlock(lock)

    assert said == str(os.getpid()), f"the service holds the lock, but scheduler.pid says {said!r}, not its process"
    assert version == __version__, f"the service holds the lock, but its Taskloom version reads as {version!r}"


@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("limit", [None, 3], ids=["no limit", "Max concurrent runs 3"])
def test_an_on_time_run_beside_a_catch_up_whose_runs_wait_for_one_another_is_not_held_for_a_free_slot(
        env, fake_runs, limit):
    """alpha (every 10 minutes, Catch up) has 06:40, 06:50 and 07:00 to catch up, one at a time; beta (07:00, Misfire
    Skip) comes on time at the same check, with slots free (no limit, or 3 with none going): alpha's later runs wait
    for its first, so they do not take a slot, and beta starts for its time. Its program is still loading when you
    turn the service off and on, so its claim is refused: its time is then decided by Misfire Skip, and it does not run
    3 minutes late as a run that waited its turn would (SCH-9)."""
    if limit:
        (env.home / "settings.yaml").write_text(f"max_concurrent_runs: {limit}\n", encoding="utf-8")
    schedule(env.home, env.root, {"alpha": flow_yaml({"cron": "*/10 * * * *", "misfire": "catch_up"}),
                                  "beta": flow_yaml({"cron": "0 7 * * *", "misfire": "skip"})})
    told = []
    fake_runs.holding.add("alpha")
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    check(service, fake_runs, clock("06:30:00"))
    fake_runs.loading.add("beta")
    check(service, fake_runs, clock("07:00:05"))
    (beta,) = fake_runs.of("beta")
    set_paused(Home(env.home), True)
    set_paused(Home(env.home), False)
    assert not beta.reach_its_time(), f"not arranged: beta's 07:00 run began though the service was turned off: {told}"
    check(service, fake_runs, clock("07:03:00"))
    ran = begins(for_time(fake_runs.of("beta"), clock("07:00:00")))
    assert ran == [], f"beta (Misfire Skip) began for 07:00 at 07:03, 3 minutes late (limit {limit}): {ran}; {told}"
