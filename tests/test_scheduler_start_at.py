"""A scheduled run begins at its due time (README: Schedules): `taskloom run --start-at` loads everything first and
then waits for that moment before it records and begins the run, and the scheduler service (tray and headless)
starts each run's process a few seconds early, so even a run whose process takes long to load is recorded as
started at its time. Everything else about scheduling stays as it is."""

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

from taskloom.config import Home
from taskloom.editor.safety import pid_alive
from taskloom.history import History
from taskloom.scheduler import set_paused

TOLERANCE = 0.02  # clock ticks (~16 ms on Windows); start times are recorded to the millisecond
EARLY = 10  # seconds between the service's first check and the run's due time: room to start its process early
# Like the packaged Taskloom on Windows, where the run's process took ~2 s to load before it recorded the run.
SLOW_IMPORT = "import time\n\ntime.sleep(1.0)  # a block file whose libraries take long to import\n"
NOTE_FLOW = "blocks:\n  note:\n    type: logic.python\n    config: {inputs: [], outputs: [], code: pass}\n"


# --- helpers ---------------------------------------------------------------------------------------------------

def write(path: Path, text: str) -> Path:
    """A file written as Taskloom reads it: UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def every_minute(wait: float = 0, active: bool = True) -> str:
    """A flow its Schedule block starts every minute (as the editor saves it); its as-of time is the parameter
    `when`; it waits `wait` seconds."""
    return yaml.safe_dump({
        "params": {"when": {"type": "datetime", "expr": "now()"}},
        "blocks": {"every_minute": {"type": "trigger.schedule", "config": {"cron": "* * * * *", "active": active}},
                   "pause": {"type": "logic.wait", "config": {"seconds": wait}},
                   "note": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}}},
        "edges": ["every_minute.start -> pause.after", "pause.done -> note.after"],
    }, sort_keys=False)


def scheduled(env, names, **flow) -> dict[str, Path]:
    """Per-minute flows named `names`, saved and listed in scheduled.yaml."""
    History(env.home / "history.db").close()  # its tables exist before any run
    home = Home(env.home)
    paths = {name: write(env.root / f"{name}.yaml", every_minute(**flow)) for name in names}
    for path in paths.values():
        home.set_scheduled(path, True)
    return paths


def runs(env) -> list[dict]:
    """The runs in history, oldest first; "when" is the as-of time (what now() was), if the flow has it."""
    found = []
    for i, flow, params, status, started, finished in env.query(
            "SELECT id, flow, params, status, started, finished FROM runs ORDER BY id"):
        when = json.loads(params).get("when")
        found.append({"id": i, "flow": flow, "status": status, "started": dt.datetime.fromisoformat(started),
                      "when": dt.datetime.fromisoformat(when) if when else None,
                      "finished": dt.datetime.fromisoformat(finished) if finished else None})
    return found


def first_block_start(env, run_id: int) -> dt.datetime:
    return dt.datetime.fromisoformat(env.query("SELECT min(started) FROM blocks WHERE run_id=?", run_id)[0][0])


def seconds_after(moment: dt.datetime, when: dt.datetime) -> float:
    return (when - moment).total_seconds()


def wait_until(moment: dt.datetime):
    while (left := (moment - dt.datetime.now()).total_seconds()) > 0:
        time.sleep(min(left, 0.1))


def run_for(env, flow: str, due: dt.datetime, until: dt.datetime) -> dict | None:
    """The finished run of `flow` for the time `due`; waits for it until the clock says `until`."""
    while True:
        done = [r for r in runs(env) if r["flow"] == flow and r["when"] == due and r["status"] != "running"]
        if done or dt.datetime.now() > until:
            return done[0] if done else None
        time.sleep(0.1)


def wait_until_finished(env, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while any(r["status"] == "running" for r in runs(env)) and time.monotonic() < deadline:
        time.sleep(0.2)


TRAY_SERVICE = """
import json, os

from PySide6.QtWidgets import QSystemTrayIcon

from taskloom.cli import run_cli


def shown(self, title, message, *rest):  # a tray message: written down
    with open(os.environ["TRAY_MESSAGES"], "a", encoding="utf-8") as f:
        f.write(json.dumps([title, message]) + "\\n")


QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)  # the offscreen platform has no tray
QSystemTrayIcon.showMessage = shown
run_cli(["scheduler"])
"""


def start_service(env, tray: bool) -> subprocess.Popen:
    """`taskloom scheduler` (the tray service, as at login or started by the editor; its tray messages are written
    to tray-messages.jsonl) or `taskloom scheduler --headless`, for the Taskloom folder env.home."""
    if tray:
        command = [sys.executable, "-c", TRAY_SERVICE]
        extra = {"QT_QPA_PLATFORM": "offscreen", "TRAY_MESSAGES": str(env.root / "tray-messages.jsonl")}
    else:
        command, extra = [sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"], {}
    return subprocess.Popen(command, env={**env.env, **extra}, cwd=env.root, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tray_messages(env) -> list:
    path = env.root / "tray-messages.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def stop_service(env, proc: subprocess.Popen):
    """Stop it as the editor turns it off: SIGTERM to the process in scheduler.pid (on Windows that ends it; a
    venv's python.exe there is a launcher, so the scheduler's own process is the one in the file)."""
    if proc.poll() is None:
        try:
            pid = int((env.home / "scheduler.pid").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pid = None
        if pid and pid != os.getpid() and pid_alive(pid):
            os.kill(pid, signal.SIGTERM)
        else:
            proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)


def due_time(env, service: subprocess.Popen) -> dt.datetime:
    """The first per-minute time at least EARLY seconds after the service's first check."""
    heartbeat = env.home / "scheduler.heartbeat"
    deadline = time.monotonic() + 60
    while not heartbeat.exists() and service.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert heartbeat.exists(), "the scheduler service did not check"
    first_check = dt.datetime.fromtimestamp(heartbeat.stat().st_mtime)
    return (first_check + dt.timedelta(seconds=EARLY)).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)


def checked_after(env, moment: dt.datetime, until: dt.datetime) -> bool:
    """Whether the service has checked (written its heartbeat) after `moment`; waits for it until `until`."""
    heartbeat = env.home / "scheduler.heartbeat"
    while dt.datetime.now() < until:
        try:
            if dt.datetime.fromtimestamp(heartbeat.stat().st_mtime) > moment:
                return True
        except OSError:  # being written this instant
            pass
        time.sleep(0.1)
    return False


PROBE = """
import itertools, json, os, subprocess, time

if os.environ.get("TEST_PROCESS_STARTS"):
    _count = itertools.count()

    class RecordedPopen(subprocess.Popen):
        def __init__(self, args, *rest, **kwargs):
            super().__init__(args, *rest, **kwargs)
            record = {"time": time.time(), "args": [str(a) for a in args] if isinstance(args, list) else [str(args)]}
            path = os.path.join(os.environ["TEST_PROCESS_STARTS"], f"{os.getpid()}-{next(_count)}")
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(record, f)
            os.replace(path + ".tmp", path)  # never read half-written

    subprocess.Popen = RecordedPopen
"""


def record_process_starts(env) -> Path:
    """A folder where the processes started with env.env record each process they start (by a sitecustomize
    module): its command line, and time.time() once it has started."""
    probe, starts = env.root / "probe", env.root / "starts"
    write(probe / "sitecustomize.py", PROBE)
    starts.mkdir()
    env.env.update(PYTHONPATH=os.pathsep.join(filter(None, [str(probe), env.env.get("PYTHONPATH")])),
                   TEST_PROCESS_STARTS=str(starts))
    return starts


def run_processes(starts: Path, flows: dict[str, Path], due: dt.datetime, until: dt.datetime) -> dict[str, float]:
    """When the service started the process of each flow's run for `due` (time.time()); waits for them until the
    clock says `until`."""
    found = {}
    while len(found) < len(flows) and dt.datetime.now() < until:
        for record in sorted(starts.glob("*-*[0-9]")):
            started = json.loads(record.read_text(encoding="utf-8"))
            args = started["args"]
            if "run" not in args or "--as-of" not in args:
                continue
            flow = Path(args[args.index("run") + 1])
            for name, path in flows.items():
                if flow == path and dt.datetime.fromisoformat(args[args.index("--as-of") + 1]) == due:
                    found.setdefault(name, started["time"])
        time.sleep(0.05)
    return found


def assert_began_at(env, run: dict, due: dt.datetime):
    """The run is recorded as started (what the Log's 'started' line says), and began, at its time."""
    late = seconds_after(due, run["started"])
    assert -TOLERANCE <= late <= 0.5 + TOLERANCE, f"{run['flow']} recorded as started {late:+.2f} s after {due}"
    begun = seconds_after(due, first_block_start(env, run["id"]))
    assert -TOLERANCE <= begun <= 1.0, f"the first block of {run['flow']} started {begun:+.2f} s after {due}"


# --- 2: taskloom run --start-at ------------------------------------------------------------------------------------

def test_run_start_at_loads_first_then_records_and_begins_the_run_at_that_moment(env):
    write(env.home / "blocks" / "slow_import.py", SLOW_IMPORT)  # loading takes over a second
    flow = write(env.root / "report.yaml", NOTE_FLOW)
    History(env.home / "history.db").close()
    moment = (dt.datetime.now() + dt.timedelta(seconds=8)).replace(microsecond=0)
    proc = env.popen("run", flow, "--start-at", moment.isoformat(timespec="seconds"))
    try:
        while dt.datetime.now() < moment - dt.timedelta(seconds=0.25):
            assert proc.poll() is None, f"it did not wait until {moment}: exit code {proc.returncode}, {proc.stderr.read()}"
            assert runs(env) == [], "the run was recorded before its moment"
            time.sleep(0.05)
        _, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=15)

    assert proc.returncode == 0, err
    (run,) = runs(env)
    assert run["status"] == "success"
    late = seconds_after(moment, run["started"])
    assert -TOLERANCE <= late <= 0.5 + TOLERANCE, f"recorded as started {late:+.2f} s after {moment}"
    begun = seconds_after(moment, first_block_start(env, run["id"]))
    assert begun >= -TOLERANCE, f"its first block started {-begun:.2f} s before {moment}"


def test_run_start_at_a_moment_that_has_passed_begins_at_once(env):
    flow = write(env.root / "report.yaml", NOTE_FLOW)
    launched = dt.datetime.now()

    result = env.taskloom("run", flow, "--start-at", (launched - dt.timedelta(hours=1)).isoformat(timespec="seconds"),
                          timeout=60)

    assert result.returncode == 0, result.stderr
    (run,) = runs(env)
    assert run["status"] == "success"
    began = seconds_after(launched, run["started"])  # when it began, not the moment that had passed
    assert -TOLERANCE <= began < 30, f"recorded as started {began:+.2f} s after it was launched"


def test_run_start_at_refuses_a_flow_with_a_mistake_once_loaded_not_at_that_moment(env):
    # "loads everything it needs (imports, the flow, its checks) and then waits until that moment"
    flow = write(env.root / "report.yaml", "blocks:\n  load:\n    type: nope.missing\n    config: {}\n")
    History(env.home / "history.db").close()
    launched = time.monotonic()

    result = env.taskloom("run", flow, "--start-at",
                          (dt.datetime.now() + dt.timedelta(seconds=30)).isoformat(timespec="seconds"), timeout=90)

    assert result.returncode == 2 and "nope.missing" in result.stderr, result.stderr
    assert time.monotonic() - launched < 20, "refused only at its --start-at moment"
    assert runs(env) == []


# --- 1 and 3: the scheduler service, in real time -------------------------------------------------------------------

def test_the_tray_service_begins_a_run_at_its_time_and_not_one_whose_schedule_went_off_before_it(env):
    """1, the reported case: the countdown reached 0 at hh:mm:00 and the Log said 'run #N started' at hh:mm:02, as
    the run's process loaded before it recorded the run. 3: a schedule made inactive or taken off scheduled.yaml
    before its time does not run: its run's process, started early, is stopped before it begins (nothing is
    recorded, nothing is told)."""
    write(env.home / "blocks" / "slow_import.py", SLOW_IMPORT)
    starts = record_process_starts(env)
    flows = scheduled(env, ["nightly", "unticked", "unlisted"])
    service = start_service(env, tray=True)
    try:
        due = due_time(env, service)
        changed = {name: flows[name] for name in ("unticked", "unlisted")}
        found = run_processes(starts, changed, due, until=due + dt.timedelta(seconds=16))
        assert found.keys() == changed.keys(), f"no run process started for {due} of {set(changed) - set(found)}"
        ahead = {name: round(due.timestamp() - t, 2) for name, t in found.items()}
        assert all(seconds >= 1.0 for seconds in ahead.values()), f"started only this many seconds before {due}: {ahead}"
        write(flows["unticked"], every_minute(active=False))  # its Schedule block's Active unticked, and saved
        Home(env.home).set_scheduled(flows["unlisted"], False)  # taken off scheduled.yaml
        assert time.time() < due.timestamp() - 0.5, "changed too late to tell"
        run = run_for(env, "nightly", due, until=due + dt.timedelta(seconds=30))
        # (by then a run that began at its time is recorded, and what ended at that time has been noticed)
        assert checked_after(env, due + dt.timedelta(seconds=1.5), until=due + dt.timedelta(seconds=17)), \
            f"the service did not check in the 15 s after {due}"
        ran = [(r["flow"], r["status"]) for r in runs(env) if r["flow"] != "nightly" and r["when"] >= due]
    finally:
        stop_service(env, service)
        wait_until_finished(env)

    assert run is not None and run["status"] == "success", f"no successful run of nightly for {due}: {run}"
    assert_began_at(env, run, due)
    assert ran == [], f"runs of schedules that went off, recorded at or after {due}: {ran}"
    assert tray_messages(env) == []


def test_the_headless_service_begins_a_run_at_its_time_and_its_early_process_counts_against_the_limit(env):
    """1: also `taskloom scheduler --headless`. 3: max_concurrent_runs, "a run counts against the limit from when its
    process starts": of two runs due at once with a limit of 1, the second starts once the first has finished."""
    write(env.home / "blocks" / "slow_import.py", SLOW_IMPORT)
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    scheduled(env, ["load", "report"], wait=2)
    service = start_service(env, tray=False)
    try:
        due = due_time(env, service)
        done = [run_for(env, name, due, until=due + dt.timedelta(seconds=45)) for name in ("load", "report")]
    finally:
        stop_service(env, service)
        wait_until_finished(env)

    assert None not in done, f"not both ran for {due}: {done}"
    assert {r["status"] for r in done} == {"success"}
    first, second = sorted(done, key=lambda r: r["started"])
    assert_began_at(env, first, due)
    assert first["finished"] <= second["started"], f"two runs at once with max_concurrent_runs: 1: {done}"


def test_turning_the_service_off_before_a_runs_time_means_that_run_does_not_happen(env):
    """3, README: turn the scheduler service off (as the editor does) "then no scheduled flow runs"; a run whose
    process it started early has not begun yet, so it does not happen."""
    starts = record_process_starts(env)
    flows = scheduled(env, ["nightly"])
    service = start_service(env, tray=True)
    try:
        due = due_time(env, service)
        found = run_processes(starts, flows, due, until=due + dt.timedelta(seconds=16))
        assert found, f"no run process started for {due}"
        ahead = round(due.timestamp() - found["nightly"], 2)
        assert ahead >= 1.0, f"started only {ahead} s before {due}"
        set_paused(Home(env.home), True)  # as the editor turns it off
        turned_off = time.time()
        stop_service(env, service)
        assert turned_off < due.timestamp() - 0.5, "turned off too late to tell"
        wait_until(due + dt.timedelta(seconds=4))
        ran = [(r["flow"], r["status"]) for r in runs(env) if r["when"] >= due]
    finally:
        stop_service(env, service)
        wait_until_finished(env)

    assert ran == [], f"runs recorded at or after {due} with the service off: {ran}"
