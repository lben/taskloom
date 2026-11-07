"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml) when it stops, other
than by your turning it off, in the seconds before a time whose run it started early, or the PC sleeps across that time
(SCH-9); when the editor records a crashed scheduled run just before the service checks (SCH-13); when a run started
from a terminal is going as the PC's time zone changes (SCH-13); and what the service says when it stops with runs
going (SCH-4, SCH-16).

Each test uses the real service (`taskloom scheduler --headless`), real run processes and the real editor window where
the behaviour crosses processes. Only what cannot happen in a test is arranged: a schedule's next time a few seconds
away (a service before this one first saw it a minute earlier: next_time_in); the service being killed or stopped, as
by a crash, the editor's Restart the scheduler service? or a PC shutting down (signals to its process); the PC
sleeping, as its processes suspended (SIGSTOP) and resumed (SIGCONT); a service that is back more than 2 minutes after
a time, as the service's checks (Scheduler.tick) at that clock time, its runs replaced by stand-ins (FakeRuns); the
service's check falling between the editor's two steps, by the editor's own run history connection, which lets the
service check once the editor has recorded the crash and closed it; and the PC's time zone, as the TZ each process
starts with (or this process is set to)."""

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from taskloom.config import Home  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.scheduler import Scheduler, runs_listed  # noqa: E402
from tests.test_scheduler_mistakes_and_counts import STAMP, headless, printed, told  # noqa: E402
from tests.test_scheduler_new_promises import (HOURLY, FakeRuns, begins, drain, end_runs, flow_yaml,  # noqa: E402
                                               going, history, hold_until, one_at_a_time, pump, says, schedule,
                                               statuses, utc, wait_until, write)
from tests.test_scheduler_start_at import (checked_after, record_process_starts, run_processes,  # noqa: E402
                                           start_service, stop_service)
from tests.test_scheduler_turn_off_and_restart import (EVERY_MINUTE, MINUTE, block_errors, crashes_when,  # noqa: E402
                                                       next_time_in, said_going)
from tests.test_scheduler_while_off import EAST, FAILURE_MAILS, WEST, Told, crash_mails, utc_offset  # noqa: E402


# --- helpers ---------------------------------------------------------------------------------------------------------

def sleep_until(moment: dt.datetime):
    """Waits until this PC's clock shows `moment`."""
    time.sleep(max(0.0, moment.timestamp() - time.time()))


def service_pid(env) -> int:
    """The scheduler service's own process (on Windows, a venv's python.exe is a launcher: not that one)."""
    return int((env.home / "scheduler.pid").read_text(encoding="utf-8"))


def end_service(env, service: subprocess.Popen, killed: bool):
    """The service ends without being turned off: killed (SIGKILL), as by a crash, or asked to stop (SIGTERM), as the
    editor's Restart the scheduler service? and a PC shutting down ask it. (On Windows either ends it at once.)"""
    os.kill(service_pid(env), getattr(signal, "SIGKILL", signal.SIGTERM) if killed else signal.SIGTERM)
    service.wait(timeout=15)


def ran(home: Path, flow: str | None = None) -> list[tuple[str, dt.datetime | None]]:
    """(status, the time it ran for) of each run (of `flow`), in the order they started."""
    return [(r["status"], r["when"] and dt.datetime.fromisoformat(r["when"])) for r in history(home)
            if flow is None or r["flow"] == flow]


# --- SCH-9: the service stops just before a time whose run it started early ------------------------------------------

@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-6")
@pytest.mark.parametrize("misfire, stop",
                         [("run_once", "killed"), ("skip", "asked to stop"), ("run_once", "restarted")],
                         ids=["killed, Run once, back 3 s after its time",
                              "asked to stop (as a PC shutting down), Skip, back 3 s after its time",
                              "restarted by the editor, back before its time"])
def test_a_time_whose_run_did_not_begin_as_the_service_stopped_just_before_it_runs_once_when_it_is_back(
        env, misfire, stop):
    """nightly runs every minute, its next time T a few seconds away, and the service has started T's run's process
    early. Before T the service stops, not turned off by you: it is killed 3 s before T (as by a crash), or asked to
    stop 3 s before T (as a PC shutting down asks it), and is back 3 s after T; or the editor restarts it (it asks it
    to stop, then starts a new one once it has ended) just after T's run's process started, and it is back before T.
    Back 3 s after T: T's run did not begin (no service was on at T), so T is a missed time, 3 s late, so on time: Run
    once runs it, and so does Skip. Back before T: T runs. Either way T runs exactly once."""
    starts = record_process_starts(env)
    flows = schedule(env.home, env.root, {"nightly": flow_yaml({**EVERY_MINUTE, "misfire": misfire})})
    due = next_time_in(env.home, 10)
    services = [start_service(env, tray=False)]
    not_begun = None
    try:
        assert run_processes(starts, flows, due, until=due - dt.timedelta(seconds=3)), \
            f"no run process was started early for {due:%H:%M:%S}"
        if stop == "restarted":
            end_service(env, services[0], killed=False)
            restarted = dt.datetime.now()
            services.append(start_service(env, tray=False))  # (as the editor's restart_scheduler starts it)
            assert checked_after(env, restarted, until=due), \
                f"restarted at {restarted:%H:%M:%S.%f}, the service did not check before {due:%H:%M:%S}: too late"
        else:
            sleep_until(due - dt.timedelta(seconds=3))
            end_service(env, services[0], killed=stop == "killed")
            ahead = due.timestamp() - time.time()
            assert ahead > 0.5, f"the service ended only {ahead:.2f} s before {due:%H:%M:%S}: too late to tell"
            sleep_until(due + dt.timedelta(seconds=3))
            not_begun = history(env.home)
            services.append(start_service(env, tray=False))  # it is back
        wait_until(lambda: [r for r in history(env.home) if r["status"] != "running"], 15)
        time.sleep(2)  # (a second run for that time would have begun by now)
        runs = ran(env.home)
    finally:
        for service in services:
            stop_service(env, service)
        end_runs(env.home)

    assert not_begun in (None, []), f"T's run began with no service on: {not_begun}"
    assert runs == [("success", due)], (
        f"the service {stop} before {due:%H:%M:%S} and back, the runs are {runs}, not one run for {due:%H:%M:%S}: "
        f"{'that time, whose run did not begin as the service stopped just before it, is lost' if not runs else ''}")


@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-6")
@pytest.mark.parametrize("misfire, ran_k, missed", [("skip", [2], 2), ("catch_up", [0, 1, 2], None)],
                         ids=["Skip", "Catch up"])
def test_a_time_whose_run_did_not_begin_as_the_service_was_killed_is_missed_when_it_is_back_over_2_minutes_later(
        env, misfire, ran_k, missed):
    """nightly runs every minute, its next time T a few seconds away, and the service has started T's run's process
    early; the service is killed 3 s before T, so that run does not begin. The service is back 2.5 minutes after T:
    T, T + 1 min and T + 2 min are due, and only T + 2 min is under 2 minutes late. Skip runs only T + 2 min and tells
    you 2 were missed (T among them); Catch up runs T, T + 1 min and T + 2 min, oldest first, one at a time. No time
    runs twice."""
    starts = record_process_starts(env)
    flows = schedule(env.home, env.root, {"nightly": flow_yaml({**EVERY_MINUTE, "misfire": misfire})})
    due = next_time_in(env.home, 10)
    service = start_service(env, tray=False)
    try:
        assert run_processes(starts, flows, due, until=due - dt.timedelta(seconds=3)), \
            f"no run process was started early for {due:%H:%M:%S}"
        sleep_until(due - dt.timedelta(seconds=3))
        end_service(env, service, killed=True)
        ahead = due.timestamp() - time.time()
        assert ahead > 0.5, f"killed only {ahead:.2f} s before {due:%H:%M:%S}: too late to tell"
        sleep_until(due + dt.timedelta(seconds=3))  # (T's run has reached its time with no service on)
        not_begun = history(env.home)
    finally:
        stop_service(env, service)
        end_runs(env.home)

    told_back, runs = [], FakeRuns()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subprocess, "Popen", runs.popen)
        back = Scheduler(Home(env.home), lambda title, message: told_back.append(f"{title}: {message}"))
        drain(back, due + dt.timedelta(seconds=150), runs)

    assert not_begun == [], f"T's run began with no service on: {not_begun}"
    expected = utc([due + k * MINUTE for k in ran_k])
    assert begins(runs.of("nightly")) == expected, (
        f"back 2.5 minutes after T ({utc([due])[0]}), the runs that began are for {begins(runs.of('nightly'))}, not "
        f"{expected}; told: {told_back}")
    assert one_at_a_time(runs.of("nightly")) == []
    if missed:
        about = [t for t in told_back if "nightly" in t]
        assert [t for t in about if says(t, missed)] and not [t for t in about if says(t, missed - 1)], (
            f"not told that {missed} runs were missed (T, whose run did not begin as the service was killed just "
            f"before it, among them): {about}")


# --- SCH-8: runs started early for the same time, and no service on at that time -------------------------------------

SHARED = [f"flow_{letter}" for letter in "abcdefghijklmnop"]  # (no digits: a flow's name in a message is never a count)
TRIES = 3  # (whether two runs reach their time at the very same instant is chance: each try is another chance)


def killed_before_a_shared_time(env, folder: Path) -> tuple[dt.datetime, list[tuple[str, str]]]:
    """In a Taskloom folder of its own (in `folder`): the SHARED flows run every minute, all due at the same time T a
    few seconds away; the service starts each one's run process early and is killed 3 s before T. T, and the runs
    that began (flow, status) by 3 s after T."""
    place = types.SimpleNamespace(root=folder, home=folder / "home")
    place.home.mkdir(parents=True)
    place.env = {**env.env, "TASKLOOM_HOME": str(place.home)}
    starts = record_process_starts(place)
    flows = schedule(place.home, place.root, {name: flow_yaml(EVERY_MINUTE) for name in SHARED})
    due = next_time_in(place.home, 10)
    service = start_service(place, tray=False)
    try:
        early = run_processes(starts, flows, due, until=due - dt.timedelta(seconds=3))
        assert set(early) == set(SHARED), f"not every run for {due:%H:%M:%S} was started early: {sorted(early)}"
        sleep_until(due - dt.timedelta(seconds=3))
        end_service(place, service, killed=True)
        ahead = due.timestamp() - time.time()
        assert ahead > 0.5, f"killed only {ahead:.2f} s before {due:%H:%M:%S}: too late to tell"
        sleep_until(due + dt.timedelta(seconds=3))  # (the runs have reached T with no service on)
        return due, [(r["flow"], r["status"]) for r in history(place.home)]
    finally:
        stop_service(place, service)
        end_runs(place.home)


@pytest.mark.invariant("SCH-8")
def test_runs_started_early_for_the_same_time_none_begins_when_the_service_was_killed_just_before_it(env):
    """Sixteen flows run every minute, all due at the same time T a few seconds away, and the service has started each
    one's run process early. The service is killed 3 s before T (as by a crash), so no service is on at T: the sixteen
    runs reach T together, and none of them begins, and nothing is recorded for any of them. Tried up to three times,
    each in a Taskloom folder of its own."""
    for attempt in range(1, TRIES + 1):
        due, began = killed_before_a_shared_time(env, env.root / f"try-{attempt}")
        if began:
            break

    assert began == [], (
        f"with no service on at {due:%H:%M:%S} (it was killed 3 s before; try {attempt} of {TRIES}), {len(began)} of "
        f"the {len(SHARED)} runs started early for that time began: {began}")


# --- SCH-9: the PC sleeps across a time whose run was started early --------------------------------------------------

# A sitecustomize module for the processes started with env.env: each `taskloom run` process they start (with
# --start-at) is written down in the folder TEST_RUN_PIDS, as a file named by its process id holding its command line.
RUN_PIDS = """
import json, os, subprocess

if os.environ.get("TEST_RUN_PIDS"):
    class Recorded(subprocess.Popen):
        def __init__(self, args, *rest, **kwargs):
            super().__init__(args, *rest, **kwargs)
            if isinstance(args, list) and "--start-at" in map(str, args):
                path = os.path.join(os.environ["TEST_RUN_PIDS"], str(self.pid))
                with open(path + ".part", "w", encoding="utf-8") as f:
                    json.dump([str(a) for a in args], f)
                os.replace(path + ".part", path)  # (never read half-written)

    subprocess.Popen = Recorded
"""


def record_run_pids(env) -> Path:
    """The folder where the run processes the service starts are written down (see RUN_PIDS)."""
    probe, folder = env.root / "pids-probe", env.root / "run-pids"
    write(probe / "sitecustomize.py", RUN_PIDS)
    folder.mkdir()
    env.env.update(PYTHONPATH=os.pathsep.join(filter(None, [str(probe), env.env.get("PYTHONPATH")])),
                   TEST_RUN_PIDS=str(folder))
    return folder


def early_runs(folder: Path, flows: dict[str, Path], due: dt.datetime, until: dt.datetime) -> dict[str, int]:
    """The process id of each flow's run for the time `due`; waits for them until the clock says `until`."""
    found = {}
    while len(found) < len(flows) and dt.datetime.now() < until:
        for record in folder.iterdir():
            if not record.name.isdigit():
                continue
            args = json.loads(record.read_text(encoding="utf-8"))
            for name, path in flows.items():
                if (Path(args[args.index("run") + 1]) == path
                        and dt.datetime.fromisoformat(args[args.index("--as-of") + 1]) == due):
                    found.setdefault(name, int(record.name))
        time.sleep(0.05)
    return found


def has_ended(pid: int) -> bool:
    """Whether the process has ended: gone, or ended and not yet reaped by its parent (suspended here)."""
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return not state or state.startswith("Z")


# name: (Misfire, which wakes first: its run started early, or the service (its run wakes once the service checked))
SLEEPERS = {"audit": ("skip", "run"), "billing": ("skip", "service"),
            "cleanup": ("run_once", "run"), "digest": ("run_once", "service")}


@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-11")
@pytest.mark.skipif(sys.platform == "win32",
                    reason="the PC sleeping is its processes suspended and resumed, which a test can do only on POSIX "
                           "(SIGSTOP, SIGCONT)")
def test_a_run_started_early_whose_time_the_pc_slept_across_does_not_begin_and_its_time_is_missed(env):
    """Four flows run every minute (Overlap skip, the default), their next time T a few seconds away, and the service
    has started T's runs' processes early. The PC sleeps 3 s before T (the service and those processes are suspended)
    and wakes 130 s after T: T and T + 1 min are more than 2 minutes late, T + 2 min is on time. As the PC wakes,
    audit's and cleanup's runs wake before the service checks; billing's and digest's only after it has checked. T's
    runs do not begin, and nothing late runs. audit and billing (Skip) run T + 2 min, which is not refused as "the
    previous run is still going", and you are told 2 were missed (T and T + 1 min); cleanup and digest (Run once) run
    only the latest, T + 2 min."""
    folder = record_run_pids(env)
    flows = schedule(env.home, env.root, {name: flow_yaml({**EVERY_MINUTE, "misfire": misfire})
                                          for name, (misfire, _) in SLEEPERS.items()})
    due = next_time_in(env.home, 10)
    log = env.root / "service.log"
    service = headless(env, log)
    suspended = []
    try:
        runs = early_runs(folder, flows, due, until=due - dt.timedelta(seconds=3))
        assert set(runs) == set(SLEEPERS), f"the runs for {due:%H:%M:%S} were not all started early: {runs}"
        sleep_until(due - dt.timedelta(seconds=3))
        suspended = [service_pid(env), *runs.values()]  # the PC sleeps
        for pid in suspended:
            os.kill(pid, signal.SIGSTOP)
        ahead = due.timestamp() - time.time()
        assert ahead > 1, f"the PC slept only {ahead:.2f} s before {due:%H:%M:%S}: too late to tell"
        sleep_until(due + dt.timedelta(seconds=130))
        first, last = ([runs[name] for name, (_, wakes) in SLEEPERS.items() if wakes == which]
                       for which in ("run", "service"))
        for pid in first:  # the PC wakes: these runs before the service
            os.kill(pid, signal.SIGCONT)
        assert wait_until(lambda: all(has_ended(pid) for pid in first), 15), "the runs that woke first did not end"
        woke = dt.datetime.now()
        os.kill(suspended[0], signal.SIGCONT)
        suspended = last
        assert checked_after(env, woke, until=woke + dt.timedelta(seconds=10)), "the service did not check on waking"
        for pid in last:  # the others once it has checked
            os.kill(pid, signal.SIGCONT)
        suspended = []
        on_time = due + 2 * MINUTE
        wait_until(lambda: all((("success", on_time) in ran(env.home, name)) for name in SLEEPERS), 20)
        time.sleep(2)  # (a run for T beginning late would show by now)
        runs_now = {name: ran(env.home, name) for name in SLEEPERS}
    finally:
        for pid in suspended:
            try:
                os.kill(pid, signal.SIGCONT)
            except OSError:
                pass
        stop_service(env, service)
        end_runs(env.home)

    messages = told(log)
    assert runs_now == {name: [("success", on_time)] for name in SLEEPERS}, (
        f"slept from 3 s before {due:%H:%M:%S} to 130 s after it, the runs are {runs_now}, not only "
        f"{on_time:%H:%M:%S}'s for each flow; told: {messages}")
    refused = [m for m in messages if "still going" in m]
    assert refused == [], f"the on-time run was refused as the previous run still going: {refused}"
    for name in ("audit", "billing"):
        about = [m for m in messages if name in m]
        assert [m for m in about if says(m, 2)] and not [m for m in about if says(m, 1)], \
            f"{name}: not told that 2 runs were missed (T and T + 1 min): {about}"


# --- SCH-13: the editor records a crash just before the service checks ----------------------------------------------

@pytest.mark.invariant("SCH-13")
def test_a_crash_the_editor_records_just_before_the_service_checks_is_told_exactly_once(env, smtp, win, monkeypatch):
    """nightly's scheduled run is going, the service has listed it as going (scheduler.runs), and its flow asks for
    failure emails (to the team). Its process crashes. The editor's check finds it first and records it as crashed;
    right then, before the editor decides whether to tell you, the service checks (it finds the run recorded already,
    and rewrites its list of runs going without it); then the editor goes on. You are told exactly once all the same:
    one message "nightly crashed" (from the editor or the service) and one email to the team.

    The order is forced through the real code: the editor's run history connection (MainWindow._history) is one whose
    close, once the editor has recorded a crash through it, first lets the service (a Scheduler in this process,
    starting a real run process) check."""
    write(env.home / "settings.yaml", "smtp_connection: mailer\n")
    schedule(env.home, env.root, {"nightly": flow_yaml(HOURLY, work=crashes_when("nightly"), notify=FAILURE_MAILS)})
    by_service = []
    service = Scheduler(Home(env.home), lambda title, message: by_service.append(f"{title}: {message}"))
    order = []

    class EditorHistory(History):
        marked = False

        def mark_crashed(self, *args, **kwargs):
            marked = super().mark_crashed(*args, **kwargs)
            self.marked = self.marked or marked
            return marked

        def close(self):
            super().close()
            if self.marked and not order:
                order.append("the editor recorded it")
                service.tick()
                order.append("the service checked")

    told_in_editor = Told(win, ("nightly",))
    try:
        service.tick()  # its first time is now
        assert wait_until(lambda: service.runs and service.runs[0].run_id is not None, 30), history(env.home)
        service.tick()  # it lists the run as going
        (run,) = service.runs
        assert run.run_id in runs_listed(Home(env.home)), "the service did not list the run as going"
        write(env.root / "nightly.crash", "now")  # the run crashes
        assert wait_until(lambda: run.proc.poll() is not None, 15), "the run did not crash"
        monkeypatch.setattr(win, "_history", lambda: EditorHistory(env.home / "history.db"))
        win._refresh_statuses()  # the editor's check (every 2 s)
        for _ in range(3):  # the later checks of the service, and of the editor
            service.tick()
        service.wait_for_runs(10)
        pump(lambda: told_in_editor.messages("nightly") or crash_mails(smtp, "nightly"), 3)
        pump(lambda: False, 2.5)  # (a second message or email would have come by now)
        service.wait_for_runs(10)
    finally:
        told_in_editor.stop()
        write(env.root / "nightly.crash", "now")
        end_runs(env.home)

    assert order == ["the editor recorded it", "the service checked"], f"the order was not forced: {order}"
    assert statuses(env.home, "nightly") == ["failed"] and [e for _, _, e in block_errors(env.home, "nightly")
                                                            if e and "crash" in e], \
        f"not recorded as crashed: {statuses(env.home, 'nightly')}, {block_errors(env.home, 'nightly')}"
    everywhere = told_in_editor.messages("nightly") + [m for m in by_service if "nightly crashed" in m]
    assert len(everywhere) == 1, f"told {len(everywhere)} times that nightly crashed, not once: {everywhere}"
    assert [to for _, to in crash_mails(smtp, "nightly")] == ["team@example.com"], \
        f"not one email to the team saying nightly crashed: {crash_mails(smtp, 'nightly')}"


# --- SCH-13: a run from a terminal, going as the time zone changes --------------------------------------------------

TERMINAL_FLOW = yaml.safe_dump({"blocks": {"manual": {"type": "trigger.manual"}, "work": hold_until("terminal")},
                                "edges": ["manual.start -> work.after"]}, sort_keys=False)


@pytest.fixture
def terminal_run(env):
    """`taskloom run terminal.yaml` from a terminal while the PC's time zone is 8 hours west of UTC; it goes until
    the file terminal.go appears."""
    offsets = utc_offset(env, WEST), utc_offset(env, EAST)
    if offsets != (-8 * 3600, 9 * 3600) or not hasattr(time, "tzset"):
        pytest.skip(f"the time zone cannot be set per process here (TZ; UTC offsets seen: {offsets})")
    path = write(env.root / "terminal.yaml", TERMINAL_FLOW)
    History(env.home / "history.db").close()
    proc = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "run", str(path)], env={**env.env, "TZ": WEST},
                            cwd=env.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        assert wait_until(lambda: going(env.home) == {"terminal"}, 30), history(env.home)
        yield proc
    finally:
        write(env.root / "terminal.go", "go")
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
        end_runs(env.home)


@pytest.fixture
def moved_east():
    """The PC's time zone is now 9 hours east of UTC, for this process (the editor, started after the change)."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = EAST
    time.tzset()
    try:
        if dt.datetime.now().astimezone().utcoffset() != dt.timedelta(hours=9):
            pytest.skip("this process does not take its time zone from TZ")
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


@pytest.mark.invariant("SCH-13")
def test_a_run_from_a_terminal_going_as_the_time_zone_moves_east_is_not_recorded_as_crashed_by_the_editor(
        env, terminal_run, moved_east, win):
    """A run started from a terminal (not by the service, so not in its list of runs going) is going; the PC's time
    zone then changes from 8 hours west of UTC to 9 hours east, and the editor is started. The editor's check does not
    record the live run as crashed: it shows it running, and the run finishes as a success."""
    win._refresh_statuses()  # the editor's check (every 2 s)
    after_check = statuses(env.home, "terminal")
    shown = win.running_flows.text()
    write(env.root / "terminal.go", "go")  # the run finishes now
    wait_until(lambda: statuses(env.home, "terminal") != ["running"], 30)

    assert after_check == ["running"], (
        f"the editor started after the time zone moved east recorded the live run as {after_check} (shows "
        f"{shown!r}): {block_errors(env.home, 'terminal')}")
    assert shown == "1 flow running", shown
    assert statuses(env.home, "terminal") == ["success"], history(env.home)


# --- SCH-4, SCH-16: what the service says when it stops with runs going ----------------------------------------------

# `taskloom scheduler --headless`, stopped as Ctrl+C in its console stops it (SIGINT, which its handler takes as a
# request to stop) at its first check after the file TEST_STOP_FILE appears.
STOPPED_ON_REQUEST = """
import os, signal
from pathlib import Path

import taskloom.scheduler as scheduler
from taskloom.cli import run_cli

check = scheduler.Scheduler.tick


def tick(self, *args, **kwargs):
    result = check(self, *args, **kwargs)
    if Path(os.environ["TEST_STOP_FILE"]).exists():
        signal.raise_signal(signal.SIGINT)
    return result


scheduler.Scheduler.tick = tick
run_cli(["scheduler", "--headless"])
"""


def stopped_message(log: Path) -> str:
    """What the headless service said as it stopped ("Scheduler stopped: ..."); empty if it did not."""
    messages = [part.strip() for part in STAMP.split(printed(log))[1:]]
    return next((m for m in messages if m.startswith("Scheduler stopped")), "")


@pytest.mark.invariant("SCH-4")
@pytest.mark.invariant("SCH-16")
def test_when_the_service_stops_with_runs_going_it_counts_only_the_runs_that_have_begun(env):
    """going's run has begun and goes on (until a file appears); soon's run, due a few seconds later, has been started
    early and has not begun. The service is stopped then, before soon's time (Ctrl+C in its console; on a server, as
    kill or the system stops it). It says 1 run is still going and continues on its own, not 2: soon's run, which will
    not begin, is not a run going."""
    starts = record_process_starts(env)
    soon = schedule(env.home, env.root, {"soon": flow_yaml(EVERY_MINUTE)})
    due = next_time_in(env.home, 12)
    schedule(env.home, env.root, {"going": flow_yaml(HOURLY, work=hold_until("going"))})  # it runs as soon as seen
    log, stop = env.root / "service.log", env.root / "stop"
    with open(log, "w", encoding="utf-8") as out:
        service = subprocess.Popen([sys.executable, "-c", STOPPED_ON_REQUEST], cwd=env.root,
                                   stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                   env={**env.env, "TEST_STOP_FILE": str(stop)})
    try:
        assert wait_until(lambda: going(env.home) == {"going"} and said_going(env.home), 20), \
            f"going's run did not begin: {history(env.home)}\n{printed(log)}"
        assert run_processes(starts, soon, due, until=due - dt.timedelta(seconds=3)), \
            f"soon's run for {due:%H:%M:%S} was not started early"
        write(stop, "stop")
        assert wait_until(lambda: service.poll() is not None, 5), "the service did not stop"
        ahead = due.timestamp() - time.time()
        assert ahead > 0.5, f"the service stopped only {ahead:.2f} s before {due:%H:%M:%S}: too late to tell"
        message = stopped_message(log)
        live = going(env.home)
    finally:
        write(env.root / "going.go", "go")
        stop_service(env, service)
        end_runs(env.home)

    assert live == {"going"}, f"going's run did not go on after the service stopped: {live}"
    assert says(message, 1) and not says(message, 2), (
        f"with 1 run going and 1 started early that will not begin, the service said as it stopped: {message!r}\n"
        f"{printed(log)}")
