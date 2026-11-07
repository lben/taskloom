"""The scheduler service's leak check: the real service runs 10,000 simulated minutes, and what it holds must stay flat.

    python packaging/leak_check.py [--minutes 10000] [--out leak.json]

The service (taskloom.scheduler.Scheduler) checks its flows on a simulated clock, twice a minute, and starts real
child processes for its runs. Each run is a small stand-in program that says it succeeded, so the check measures the
service, not the run. Three flows cover its paths: every minute; every 5 minutes, waiting its turn for the only slot
(max_concurrent_runs 1) and so saved and removed in the run history; daily at 02:00 with Catch up. Every 1,000
minutes the PC "sleeps" for 3 hours, so missed times are skipped, told and caught up.

After a warm-up of 1,000 minutes, at every 1,000 minutes: the Python memory the service holds (tracemalloc), its
threads, its open files and its child processes still alive or not reaped. It passes when the threads, files and
child processes stay what they were after the warm-up, and over the second half of the minutes the memory grew by
less than 100 bytes per run started: a leak of 100 bytes per run fails. (Only the second half, because tracemalloc
cannot see a table Python made before the check began, such as its table of interned strings: the first time Python
rebuilds it, the new one counts as growth once, about 2 MiB, and never again.) Linux and macOS (it counts open
files and child processes as those show them). Exit 1 when it fails.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gc
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from taskloom import scheduler as service  # noqa: E402
from taskloom.config import Home  # noqa: E402
from taskloom.history import History  # noqa: E402

WARM_UP = 1000
EVERY = 1000
SLEEP = dt.timedelta(hours=3)
BYTES_PER_RUN = 100
STAND_IN = "import sys; sys.stdout.write('{\"event\": \"run_finished\", \"status\": \"success\"}\\n')"
FLOWS = {
    "every_minute": {"cron": "* * * * *"},
    "every_5_minutes": {"every": "5 minutes"},
    "nightly": {"cron": "0 2 * * *", "misfire": "catch_up"},
}


def flow_text(schedule: dict, **top) -> str:
    return yaml.safe_dump({**top, "blocks": {
        "start": {"type": "trigger.schedule", "config": schedule},
        "work": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}}},
        "edges": ["start.start -> work.after"]}, sort_keys=False)


def open_files() -> int:
    return len(os.listdir("/proc/self/fd" if Path("/proc/self/fd").exists() else "/dev/fd"))


def child_processes() -> int:
    """This process's children, also ended ones not yet reaped (zombies)."""
    listed = subprocess.run(["ps", "-A", "-o", "ppid="], capture_output=True, text=True, timeout=30).stdout.split()
    return sum(int(ppid) == os.getpid() for ppid in listed) - 1  # (minus ps itself)


def measure(minute: int, runs: int) -> dict:
    gc.collect()
    return {"minute": minute, "runs started": runs, "memory_bytes": tracemalloc.get_traced_memory()[0],
            "threads": threading.active_count(), "open_files": open_files(), "child_processes": child_processes()}


def settle(scheduler: service.Scheduler, at: dt.datetime):
    """Lets every run of this minute start and end: the service checks again until nothing is going or waiting."""
    while scheduler.runs or scheduler.waiting or any(scheduler.queued.values()):
        for run in list(scheduler.runs):
            run.proc.wait(timeout=60)
        scheduler.tick(at)


def check(minutes: int) -> dict:
    started = 0

    def stand_in():  # (the service asks for the command once per run it starts)
        nonlocal started
        started += 1
        return [sys.executable, "-S", "-c", STAND_IN]

    service.runner_command = stand_in
    told = {"count": 0}

    def notify(title, message):
        told["count"] += 1

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        home = Home(root / "home")
        home.root.mkdir(parents=True)
        (home.root / "settings.yaml").write_text("max_concurrent_runs: 1\n", encoding="utf-8")
        History(home.history_db).close()
        for name, schedule in FLOWS.items():
            path = root / f"{name}.yaml"
            path.write_text(flow_text(schedule, overlap="queue"), encoding="utf-8")
            home.set_scheduled(path.resolve(), True)

        tracemalloc.start()
        scheduler = service.Scheduler(home, notify)
        now = dt.datetime(2026, 3, 2, 0, 0)
        checkpoints, snapshots, began = [], None, time.monotonic()
        for minute in range(1, minutes + 1):
            now += dt.timedelta(minutes=1)
            if minute % EVERY == EVERY // 2:
                now += SLEEP  # the PC sleeps: the service is not checking
            scheduler.tick(now)
            settle(scheduler, now + dt.timedelta(seconds=30))
            if minute >= WARM_UP and minute % EVERY == 0:
                checkpoints.append(measure(minute, started))
                snapshots = (snapshots or [tracemalloc.take_snapshot()])[:1] + [tracemalloc.take_snapshot()]
        seconds = time.monotonic() - began
        tracemalloc.stop()
    # Where the memory grew most after the warm-up: the lines of code that allocated it.
    grew = [f"{stat.traceback[0].filename.split('taskloom')[-1]}:{stat.traceback[0].lineno} "
            f"{stat.size_diff / 1024:+,.0f} KiB in {stat.count_diff:+,} blocks"
            for stat in snapshots[-1].compare_to(snapshots[0], "lineno")[:8] if stat.size_diff > 0]

    first, middle, last = checkpoints[0], checkpoints[len(checkpoints) // 2], checkpoints[-1]
    growth = last["memory_bytes"] - middle["memory_bytes"]
    limit = BYTES_PER_RUN * (last["runs started"] - middle["runs started"])
    flat = {key: all(c[key] == first[key] for c in checkpoints) for key in ("threads", "open_files", "child_processes")}
    passed = growth < limit and all(flat.values())
    summary = (f"{minutes:,} simulated minutes, {started:,} runs: memory {growth / 1024:+,.0f} KiB from minute "
               f"{middle['minute']:,} to {last['minute']:,} (limit +{limit / 1024:,.0f} KiB, {BYTES_PER_RUN} bytes a run);"
               f" threads {last['threads']}, open files {last['open_files']}, child processes {last['child_processes']}"
               + ("" if all(flat.values()) else f"; not flat: {', '.join(k for k, v in flat.items() if not v)}"))
    return {"passed": passed, "summary": summary, "seconds": round(seconds), "told": told["count"], "grew": grew,
            "numbers": {"simulated minutes": f"{minutes:,}", "runs started (real child processes)": f"{started:,}",
                        f"memory growth, minute {middle['minute']:,} to {last['minute']:,}":
                            f"{growth / 1024:+,.0f} KiB (limit +{limit / 1024:,.0f} KiB)",
                        "memory at each checkpoint (KiB)": " ".join(f"{c['memory_bytes'] / 1024:,.0f}"
                                                                    for c in checkpoints),
                        "threads at each checkpoint": " ".join(str(c["threads"]) for c in checkpoints),
                        "open files at each checkpoint": " ".join(str(c["open_files"]) for c in checkpoints),
                        "child processes at each checkpoint": " ".join(str(c["child_processes"]) for c in checkpoints),
                        "messages told (missed times)": f"{told['count']:,}"},
            "checkpoints": checkpoints}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=int, default=10000)
    p.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    if a.minutes < WARM_UP + EVERY:
        p.error(f"--minutes must be at least {WARM_UP + EVERY}")
    result = check(a.minutes)
    if a.out:
        a.out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    for c in result["checkpoints"]:
        print(f"minute {c['minute']:>6,}: runs {c['runs started']:>6,}, memory {c['memory_bytes'] / 1024:>8,.0f} KiB, "
              f"threads {c['threads']}, open files {c['open_files']}, child processes {c['child_processes']}")
    if not result["passed"]:
        print("Where the memory grew most after the warm-up:", *result["grew"], sep="\n  ")
    print(("PASSED: " if result["passed"] else "FAILED: ") + result["summary"] + f" ({result['seconds']} s)")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
