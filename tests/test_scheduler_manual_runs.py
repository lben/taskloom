"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml, SCH-11) about runs you
started yourself: when a flow's scheduled time comes while a run of it that you started (from a terminal, or Run in the
editor) is going, Overlap decides as for a scheduled run going: Skip skips the time and tells you, Queue makes it wait
for your run, Allow starts it alongside. A run you started that has ended, or whose process has gone, no longer counts,
and neither does a run of another flow. Clicking Run yourself always starts at once, even while a scheduled run of the
flow is going: Overlap applies only when a scheduled time comes.

The runs you start are real `taskloom run` processes, going until a file appears next to the flow. The scheduled times
use the service's checks (Scheduler.tick) on a chosen clock just after this PC's (a run you started began before
them), the service made as `taskloom scheduler` makes it (the Taskloom folder's lock first: FakeRuns.service), with
each scheduled run's process stood in for at the process boundary (FakeRuns). Clicking Run is done in the real editor
window, while the real `taskloom scheduler --headless` has a scheduled run of the flow going."""

import contextlib
import datetime as dt
import os
import re
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_scheduler_mistakes_and_counts import headless, printed  # noqa: E402
from tests.test_scheduler_new_promises import (HOURLY, REAL_POPEN, FakeRuns, begins, drain, end_runs,  # noqa: E402
                                               hold_until, pump, schedule, utc, wait_until, write)
from tests.test_scheduler_start_at import stop_service  # noqa: E402

SECONDS = dt.timedelta(seconds=1)
YEARLY = {"cron": "0 2 1 1 *"}  # (its time never comes in a test)


@pytest.fixture
def fake_runs(monkeypatch) -> FakeRuns:
    """The scheduled runs' processes replaced by stand-ins (see FakeRuns); a run you start yourself is real. The
    service made last with them stops at the end."""
    runs = FakeRuns()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


def flow_text(schedule_config: dict, overlap: str, name: str) -> str:
    """A flow as the editor saves it, with no parameters (Run asks nothing): its Schedule block s1 (`schedule_config`)
    starts `work`, Python that goes on until the file <name>.go appears next to the flow; its Overlap is `overlap`."""
    return yaml.safe_dump({"overlap": overlap,
                           "blocks": {"s1": {"type": "trigger.schedule", "config": schedule_config},
                                      "work": hold_until(name)},
                           "edges": ["s1.start -> work.after"]}, sort_keys=False)


def runs_of(home: Path, flow: str) -> list[tuple[str, bool, int | None]]:
    """(status, whether the scheduler service started it, its process) of each run of `flow` in the run history, in
    the order they started."""
    with contextlib.closing(sqlite3.connect(home / "history.db", timeout=30)) as db:
        rows = db.execute("SELECT status, schedule IS NOT NULL, pid FROM runs WHERE flow=? ORDER BY id",
                          (flow,)).fetchall()
    return [(status, bool(scheduled), pid) for status, scheduled, pid in rows]


def started_by_hand(env, path: Path) -> subprocess.Popen:
    """`taskloom run FLOW` from a terminal; it has begun (it is recorded as running in the run history, not as a run
    the scheduler service started) when this returns."""
    flow = path.stem
    before = len(runs_of(env.home, flow))
    proc = REAL_POPEN([sys.executable, "-m", "taskloom.cli", "run", str(path)], env=env.env, cwd=env.root,
                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    began = wait_until(lambda: runs_of(env.home, flow)[before:] == [("running", False, proc.pid)]
                       or proc.poll() is not None, 60)
    assert began and proc.poll() is None, f"your run of {flow} did not begin: {runs_of(env.home, flow)}"
    return proc


def let_go(env, name: str, proc: subprocess.Popen):
    """Your run of `name` finishes: the file it waits for appears, and its process ends."""
    write(env.root / f"{name}.go", "go")
    proc.wait(timeout=60)


@pytest.fixture
def your_runs(env):
    """Cleanup: the runs you started end, and none outlives the test."""
    yield
    for name in ("nightly", "other"):
        write(env.root / f"{name}.go", "go")
    end_runs(env.home)


def about(told: list[str], flow: str) -> list[str]:
    return [t for t in told if flow in t]


def checks_around(service, runs: FakeRuns, due: dt.datetime):
    """The service checks as the time `due` comes: when it first sees the schedule, within its early start before
    `due`, at `due` and just after it."""
    assert dt.datetime.now() < due - 20 * SECONDS, "not arranged: the run you started took too long to begin"
    for moment in (due - 20 * SECONDS, due - 4 * SECONDS, due, due + SECONDS):
        drain(service, moment, runs)


def soon() -> dt.datetime:
    """A one-off time a minute after this PC's clock now (to the second): a run you start now has begun by the time
    the service's checks around it come (checks_around)."""
    return (dt.datetime.now() + dt.timedelta(minutes=1)).replace(microsecond=0)


def one_off(moment: dt.datetime) -> dict:
    return {"at": f"{moment:%Y-%m-%d %H:%M:%S}"}


# --- SCH-11: a run you started is going when the flow's scheduled time comes ----------------------------------------

@pytest.mark.invariant("SCH-11")
@pytest.mark.parametrize("overlap", ["skip", "queue", "allow"])
def test_a_scheduled_time_that_comes_while_your_run_of_the_flow_goes_follows_its_overlap(env, fake_runs, your_runs,
                                                                                        overlap):
    """You started nightly from a terminal (`taskloom run nightly.yaml`), and that run is going when nightly's
    scheduled one-off time T comes. Overlap Skip: no scheduled run starts, and you are told T was skipped as a run of
    nightly is going. Queue: T waits while your run goes, and starts once it has ended. Allow: T starts alongside your
    run."""
    due = soon()
    path = schedule(env.home, env.root, {"nightly": flow_text(one_off(due), overlap, "nightly")})["nightly"]
    mine = started_by_hand(env, path)
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    checks_around(service, fake_runs, due)
    while_yours_went = begins(fake_runs.of("nightly"))
    yours_going = mine.poll() is None
    if overlap == "queue":
        let_go(env, "nightly", mine)
        drain(service, due + 10 * SECONDS, fake_runs)

    assert yours_going, "not arranged: your run had ended before the scheduled time"
    if overlap == "allow":
        assert while_yours_went == utc([due]), (
            f"Overlap Allow: T ({due:%H:%M:%S}) did not start alongside your run: runs began for {while_yours_went}; "
            f"told: {told}")
        return
    assert while_yours_went == [], (
        f"Overlap {overlap}: a scheduled run of nightly began for {while_yours_went} while the run you started was "
        f"going; told: {told}")
    if overlap == "skip":
        skipped = [t for t in about(told, "nightly") if re.search(r"skip", t, re.IGNORECASE)
                   and re.search(r"\bgoing\b|\brunning\b", t, re.IGNORECASE)]
        assert skipped, f"not told that T was skipped as a run of nightly is going: {told}"
    else:
        assert begins(fake_runs.of("nightly")) == utc([due]), (
            f"Overlap Queue: once your run ended, T ({due:%H:%M:%S}) did not start: runs began for "
            f"{begins(fake_runs.of('nightly'))}; told: {told}")


# --- SCH-11: a run you started that has ended, or whose process has gone, and another flow's run ---------------------

@pytest.mark.invariant("SCH-11")
@pytest.mark.parametrize("how", ["it finished", "its process has gone"])
def test_a_run_you_started_that_is_over_no_longer_counts_for_overlap(env, fake_runs, your_runs, how):
    """You started nightly (Overlap skip) from a terminal, and that run is over before nightly's scheduled one-off time
    T: it finished, or its process has gone (killed: the run history still says it is running). T runs as usual, and
    nothing is skipped."""
    due = soon()
    path = schedule(env.home, env.root, {"nightly": flow_text(one_off(due), "skip", "nightly")})["nightly"]
    mine = started_by_hand(env, path)
    if how == "it finished":
        let_go(env, "nightly", mine)
        assert runs_of(env.home, "nightly") == [("success", False, mine.pid)], runs_of(env.home, "nightly")
    else:
        os.kill(mine.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        mine.wait(timeout=30)
        assert runs_of(env.home, "nightly") == [("running", False, mine.pid)], runs_of(env.home, "nightly")
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    checks_around(service, fake_runs, due)

    assert begins(fake_runs.of("nightly")) == utc([due]), (
        f"your run of nightly was over ({how}), yet T ({due:%H:%M:%S}) did not run: runs began for "
        f"{begins(fake_runs.of('nightly'))}; told: {told}")
    assert [t for t in about(told, "nightly") if re.search(r"skip", t, re.IGNORECASE)] == [], told


@pytest.mark.invariant("SCH-11")
def test_a_run_you_started_of_another_flow_does_not_count_for_this_flows_overlap(env, fake_runs, your_runs):
    """You started other from a terminal, and that run is going when nightly's (Overlap skip) scheduled one-off time T
    comes. T runs as usual, and nothing is skipped: Overlap counts runs of the same flow only."""
    due = soon()
    paths = schedule(env.home, env.root, {"nightly": flow_text(one_off(due), "skip", "nightly"),
                                          "other": flow_text(YEARLY, "skip", "other")})
    mine = started_by_hand(env, paths["other"])
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    checks_around(service, fake_runs, due)

    assert mine.poll() is None, "not arranged: your run of other had ended before nightly's time"
    assert begins(fake_runs.of("nightly")) == utc([due]), (
        f"while your run of other went, nightly's T ({due:%H:%M:%S}) did not run: runs began for "
        f"{begins(fake_runs.of('nightly'))}; told: {told}")
    assert [t for t in told if re.search(r"skip", t, re.IGNORECASE)] == [], told


# --- SCH-11: clicking Run yourself while a scheduled run of the flow is going ---------------------------------------

@pytest.mark.invariant("SCH-11")
@pytest.mark.parametrize("overlap", ["skip", "queue", "allow"])
def test_clicking_run_while_a_scheduled_run_of_the_flow_goes_starts_at_once_whatever_its_overlap(env, win, overlap):
    """The scheduler service (`taskloom scheduler --headless`) runs nightly every 60 minutes, so as it first sees it,
    and that scheduled run is going. You open nightly in the editor and click Run: your run starts at once, alongside
    the scheduled one, whatever nightly's Overlap: it applies only when a scheduled time comes."""
    path = schedule(env.home, env.root, {"nightly": flow_text(HOURLY, overlap, "nightly")})["nightly"]
    log = env.root / "service.log"
    service = headless(env, log)
    try:
        scheduled = wait_until(lambda: [r[:2] for r in runs_of(env.home, "nightly")] == [("running", True)], 60)
        assert scheduled, f"the scheduled run of nightly did not begin: {runs_of(env.home, 'nightly')}\n{printed(log)}"
        win.open_flow(path)
        win.run_action.trigger()  # ▶ Run now (its question answered: Run)
        yours = pump(lambda: [r for r in runs_of(env.home, "nightly") if r[:2] == ("running", False)], 30)
        runs = runs_of(env.home, "nightly")
    finally:
        write(env.root / "nightly.go", "go")
        pump(lambda: all(not run.running for run in win.current.runs) if win.current else True, 30)
        stop_service(env, service)
        end_runs(env.home)

    assert yours, (f"Overlap {overlap}: clicking Run while the scheduled run of nightly was going did not start your "
                   f"run: {runs}; the editor said: {win.statusBar().currentMessage()!r}")
    assert [r[:2] for r in runs] == [("running", True), ("running", False)], runs
