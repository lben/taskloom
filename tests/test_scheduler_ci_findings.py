"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml), in cases CI found on
Windows: a run whose recorded process is not the one the service started, as when a venv's python.exe is a launcher
(SCH-12).

The times use the service's checks (Scheduler.tick) at chosen clock times, the service made as `taskloom scheduler`
makes it (FakeRuns.service), with each run's process replaced at the process boundary by a stand-in that claims as the
real run does (test_scheduler_held_times.Runs)."""

import contextlib
import datetime as dt
import io
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from taskloom import cli
from taskloom.config import Home
from taskloom.flow import load_flow, real_path
from taskloom.history import History
from tests.test_scheduler_held_times import Runs, check, clock, for_time
from tests.test_scheduler_new_promises import REAL_POPEN, FakeRun, clock_shows, flow_yaml, run_command_line, schedule


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
