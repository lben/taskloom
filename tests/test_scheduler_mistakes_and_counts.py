"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml): a hand edit that leaves
an entry in scheduled.yaml that is not a flow file's path (SCH-14), the operating system refusing to start a run's
process (SCH-14, charter C10), and the counts when more than 100 times of a Catch up schedule were missed and one more
is on time, or comes while you are asked (SCH-9).

The mistakes use the real service: `taskloom scheduler --headless`, and the tray service in this process. The counts
use the service's checks (Scheduler.tick) at chosen clock times, with each run's process replaced at the process
boundary by a stand-in that has finished by the next check (test_scheduler_new_promises.FakeRuns)."""

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from taskloom.config import Home
from taskloom.scheduler import Scheduler, acquire_lock
from tests.test_paths_scheduler import heartbeat
from tests.test_scheduler_new_promises import (EVERY_TEN, FIRST_SIGHT, HOURLY, FakeRuns, begins, drain, end_runs,
                                               flow_yaml, history, missed_times, one_at_a_time, pump, says, schedule,
                                               statuses, tray_service, utc, wait_until, write)
from tests.test_scheduler_start_at import stop_service

STAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", re.MULTILINE)  # how the headless service starts each message
# scheduled.yaml as a hand edit can leave it: an entry that is not a flow file's path ({listed}: a flow listed right)
MISTAKES = {
    "an empty item": "flows:\n  -\n  - {listed}\n",
    "a number item": "flows:\n  - 42\n  - {listed}\n",
    "flows: 5": "flows: 5\n",
}


@pytest.fixture
def fake_runs(monkeypatch) -> FakeRuns:
    """`taskloom run` processes replaced by stand-ins (see FakeRuns); a service made with them (FakeRuns.service)
    stops at the end."""
    runs = FakeRuns()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


def headless(env, log: Path, **extra) -> subprocess.Popen:
    """`taskloom scheduler --headless` for the Taskloom folder env.home, as on a server: everything it prints goes to
    `log`. `extra`: more environment variables."""
    with open(log, "w", encoding="utf-8") as out:
        return subprocess.Popen([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"],
                                env={**env.env, **extra}, cwd=env.root, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT)


def printed(log: Path) -> str:
    return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""


def told(log: Path) -> list[str]:
    """What the headless service has told you so far ("<title>: <message>", a message also over several lines), in
    order; not that it started or stopped."""
    messages = [part.strip() for part in STAMP.split(printed(log))[1:]]
    return [m for m in messages if not m.startswith(("Scheduler started", "Scheduler stopped"))]


def about_the_list(messages: list[str]) -> list[str]:
    """The messages about the list of scheduled flows (scheduled.yaml)."""
    return [m for m in messages if re.search(r"scheduled\.yaml|scheduled flows", m, re.IGNORECASE)]


def python_words(text: str) -> list[str]:
    """What in a message is Python's rather than plain words: a traceback, an exception's or a type's name."""
    return re.findall(r"Traceback|\b[A-Z]\w*Error\b|NoneType|PathLike|__\w+__|object is not iterable", text)


def checks_on(env, count: int = 3) -> bool:
    """Whether the service goes on checking: its heartbeat (the second of its latest check) changes `count` times."""
    beats = [heartbeat(env.home)]

    def changed() -> bool:
        beat = heartbeat(env.home)
        if beat is not None and beat != beats[-1]:
            beats.append(beat)
        return len(beats) > count

    return wait_until(changed, count + 10)


# --- SCH-14: an entry in scheduled.yaml that is not a flow file's path ---------------------------------------------

@pytest.mark.invariant("SCH-14")
@pytest.mark.parametrize("mistake", MISTAKES)
def test_the_server_service_with_an_entry_in_the_list_that_is_not_a_flow_tells_you_once_and_goes_on(env, mistake):
    """scheduled.yaml left by a hand edit with an entry that is not a flow file's path: an empty `-` item or a number
    (before a flow listed right), or `flows: 5`. `taskloom scheduler --headless` started then keeps running and
    checking, and tells you once, in plain words, what is wrong with the list; the flow listed right next to the
    mistake runs. Once the list is put right, the flows it lists run (a flow added then runs when the service sees
    it)."""
    paths = schedule(env.home, env.root, {"listed": flow_yaml(HOURLY), "added": flow_yaml(HOURLY)})  # (run when seen)
    write(env.home / "scheduled.yaml", MISTAKES[mistake].format(listed=json.dumps(str(paths["listed"]))))
    log = env.root / "service.log"
    service = headless(env, log)

    def ran(flow: str) -> bool:
        return statuses(env.home, flow) == ["success"] or service.poll() is not None

    try:
        wait_until(lambda: about_the_list(told(log)) or service.poll() is not None, 30)
        going_on = service.poll() is None and checks_on(env)  # (a few checks, each second: told once, not each time)
        if mistake != "flows: 5":
            wait_until(lambda: ran("listed"), 30)
        before = told(log), statuses(env.home, "listed")
        write(env.home / "scheduled.yaml", yaml.safe_dump({"flows": [str(paths["listed"]), str(paths["added"])]}))
        wait_until(lambda: ran("listed") and ran("added"), 30)
        still_going = service.poll() is None
    finally:
        stop_service(env, service)
        end_runs(env.home)

    assert going_on, f"the service stopped (or stopped checking) with {mistake} in scheduled.yaml:\n{printed(log)}"
    list_told = about_the_list(before[0])
    assert len(list_told) == 1, f"not told once what is wrong with scheduled.yaml:\n{printed(log)}"
    assert python_words(list_told[0]) == [], f"not in plain words: {list_told[0]}"
    if mistake != "flows: 5":
        assert before[1] == ["success"], f"the flow listed right next to the mistake did not run:\n{printed(log)}"
    assert still_going and (statuses(env.home, "listed"), statuses(env.home, "added")) == (["success"], ["success"]), \
        f"once the list was put right, the flows it lists did not run:\n{printed(log)}"


@pytest.mark.invariant("SCH-14")
def test_the_tray_service_started_with_an_empty_item_in_the_list_keeps_running_and_tells_you(qapp, env, monkeypatch,
                                                                                           fake_runs):
    """The tray service (as at login, or started by the editor) started while scheduled.yaml has an empty `-` item
    before a flow listed right: from its start it keeps running and checking, tells you once on its tray icon, in plain
    words, what is wrong with the list, and runs the flow listed right; once the list is put right, a flow added to it
    runs."""
    paths = schedule(env.home, env.root, {"listed": flow_yaml(HOURLY), "added": flow_yaml(HOURLY)})
    write(env.home / "scheduled.yaml", MISTAKES["an empty item"].format(listed=json.dumps(str(paths["listed"]))))
    seen = {}

    def drive(tray, result):
        pump(lambda: about_the_list(result["told"]) and fake_runs.of("listed"), 10)
        pump(lambda: False, 3.5)  # (a few more checks, each second: told once, not each time)
        seen["told"], seen["listed"] = list(result["told"]), len(fake_runs.of("listed"))
        write(env.home / "scheduled.yaml", yaml.safe_dump({"flows": [str(paths["listed"]), str(paths["added"])]}))
        seen["added"] = pump(lambda: fake_runs.of("added"), 10)
        seen["asked to end"] = result["quit"]

    try:
        tray_service(monkeypatch, env.home, drive)
    except Exception as e:
        pytest.fail(f"the tray service stopped at its start with an empty item in scheduled.yaml: "
                    f"{type(e).__name__}: {e}")

    list_told = about_the_list(seen["told"])
    assert len(list_told) == 1, f"not told once on the tray icon what is wrong with scheduled.yaml: {seen['told']}"
    assert python_words(list_told[0]) == [], f"not in plain words: {list_told[0]}"
    assert seen["listed"] == 1, f"the flow listed right next to the mistake did not run: {seen}"
    assert seen["added"] and seen["asked to end"] is None, f"once the list was put right, it did not go on: {seen}"


@pytest.mark.invariant("SCH-14")
@pytest.mark.parametrize("mistake", ["an empty item", "a number item"])
def test_a_flow_listed_next_to_the_mistake_still_begins_at_its_time_when_its_run_starts_early(env, mistake):
    """The service on (this test holds its lock), and scheduled.yaml with an entry that is not a flow file's path
    before a flow listed right (every 1 minute). The service's checks go on, and that flow's run for its next time,
    whose process the service starts a few seconds early so that it begins on time (it then checks the list before it
    begins), begins at its time and succeeds."""
    paths = schedule(env.home, env.root, {"listed": flow_yaml({"every": "1 minute"}, overlap="allow")})
    write(env.home / "scheduled.yaml", MISTAKES[mistake].format(listed=json.dumps(str(paths["listed"]))))
    told = []
    lock = acquire_lock(Home(env.home))  # the scheduler service is on: as `taskloom scheduler`, the lock first
    scheduler = Scheduler(Home(env.home), lambda title, message: told.append(f"{title}: {message}"))
    seen = dt.datetime.now().replace(microsecond=0) - dt.timedelta(seconds=57)
    try:
        scheduler.tick(seen)  # first seen then: its run for that time begins at once
        scheduler.tick(dt.datetime.now())  # its next time, a minute after, is 3 s away: that run's process starts now
        scheduler.wait_for_runs(timeout=60)
    except Exception as e:
        pytest.fail(f"the service's check stopped with {mistake} in scheduled.yaml: {type(e).__name__}: {e}")
    finally:
        lock.close()

    on_time = [r["status"] for r in history(env.home)
               if r["when"] and dt.datetime.fromisoformat(r["when"]) == seen + dt.timedelta(minutes=1)]
    assert on_time == ["success"], (f"the run for {seen + dt.timedelta(minutes=1):%H:%M:%S}, started early, did not "
                                    f"begin and succeed: {history(env.home)}; told: {told}")


# --- SCH-14, C10: the operating system refuses to start a run's process ---------------------------------------------

# Loaded by every Python process started with it on PYTHONPATH: the operating system refuses to start the process of
# a run of the flow file named in TEST_REFUSED_FLOW (as when the program cannot be started this moment).
REFUSE = """
import errno, os, subprocess

_execute_child = subprocess.Popen._execute_child


def _refusing(self, args, *rest):
    refused = os.environ.get("TEST_REFUSED_FLOW")
    if refused and isinstance(args, list) and "run" in args and args.index("run") + 1 < len(args) \\
            and os.path.basename(str(args[args.index("run") + 1])) == refused:
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(args[0]))
    return _execute_child(self, args, *rest)


subprocess.Popen._execute_child = _refusing
"""


@pytest.mark.invariant("SCH-14")
@pytest.mark.charter("C10")
def test_when_the_system_refuses_to_start_a_runs_process_the_service_tells_you_and_goes_on(env):
    """The operating system refuses to start the process of nightly_export's run. `taskloom scheduler --headless`
    keeps running and checking, tells you in plain words that nightly_export could not start, and a flow due later
    (added to the list then: it runs when the service sees it) still runs."""
    probe = write(env.root / "probe" / "sitecustomize.py", REFUSE).parent
    paths = schedule(env.home, env.root, {"nightly_export": flow_yaml(HOURLY), "later": flow_yaml(HOURLY)})
    Home(env.home).set_scheduled(paths["later"], False)  # (listed once nightly_export was refused)
    log = env.root / "service.log"
    service = headless(env, log, TEST_REFUSED_FLOW="nightly_export.yaml",
                       PYTHONPATH=os.pathsep.join(filter(None, [str(probe), env.env.get("PYTHONPATH")])))

    def refused() -> list[str]:  # (the test's folders, in a path it names, are no words of the message)
        messages = [m.replace(str(env.root.resolve()), "").replace(str(env.root), "") for m in told(log)]
        return [m for m in messages if "nightly_export" in m and re.search(r"start", m, re.IGNORECASE)]

    try:
        wait_until(lambda: refused() or service.poll() is not None, 30)
        going_on = service.poll() is None and checks_on(env)
        Home(env.home).set_scheduled(paths["later"], True)
        wait_until(lambda: statuses(env.home, "later") == ["success"] or service.poll() is not None, 30)
        still_going = service.poll() is None
    finally:
        stop_service(env, service)
        end_runs(env.home)

    assert going_on, f"the service stopped when nightly_export's run could not be started:\n{printed(log)}"
    assert refused(), f"not told that nightly_export could not start:\n{printed(log)}"
    assert python_words(refused()[0]) == [], f"not in plain words: {refused()[0]}"
    assert still_going and statuses(env.home, "later") == ["success"], \
        f"the flow due later did not run:\n{printed(log)}"
    assert statuses(env.home, "nightly_export") == []


# --- SCH-9: 150 missed Catch up times, with one more on time or coming while you are asked ---------------------------

ON_TIME = missed_times(151)[-1]  # 2026-01-06 01:10, the time after the 150 missed ones (01:00 the last of them)
ASKED_AT = ON_TIME - dt.timedelta(seconds=3)  # the check that finds them: ON_TIME is within its 5 s early start


def catch_up_service(env, runs: FakeRuns, someone_to_ask: bool) -> tuple[Scheduler, list, list]:
    """The service's checks over a Catch up schedule every 10 minutes (the flow `backlog`), first seen at FIRST_SIGHT,
    its runs stood in for by `runs` (made as `taskloom scheduler` makes it: FakeRuns.service): (the service, what it
    told, what it asked: (flow, count, answer))."""
    schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN)})
    told, asked = [], []
    scheduler = runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"),
                             (lambda flow, count, answer: asked.append((flow, count, answer))) if someone_to_ask
                             else None)
    scheduler.tick(FIRST_SIGHT)
    return scheduler, told, asked


@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("someone_to_ask", [True, False], ids=["asked: Run only the latest 100", "a server: no one"])
def test_150_missed_and_one_on_time_the_latest_100_missed_run_and_the_on_time_one_too(env, fake_runs, someone_to_ask):
    """A Catch up schedule missed 150 times (00:10 to 01:00 the next day, every 10 minutes); at the check that finds
    them, 3 s before 01:10, 01:10 is on time (its run's process starts then, to begin at 01:10). The question says 150
    were missed; Run only the latest 100. The latest 100 missed times run, oldest first, one at a time, and 01:10 runs
    too, after them (it is not missed); you are told 50 were not run. With no one to ask (a server): the same runs
    without a question, and its log says 150 were missed and 50 not run."""
    scheduler, told, asked = catch_up_service(env, fake_runs, someone_to_ask)
    drain(scheduler, ASKED_AT, fake_runs)
    if someone_to_ask:
        assert [(flow, count) for flow, count, _ in asked] == [("backlog", 150)], told
        asked[0][2](100)  # Run only the latest 100
        drain(scheduler, ASKED_AT, fake_runs)
    drain(scheduler, ON_TIME, fake_runs)

    ran = begins(fake_runs.of("backlog"))
    on_time = utc([ON_TIME])[0]
    missed_ran = [t for t in ran if t != on_time]
    assert missed_ran == utc(missed_times(150)[-100:]), (
        f"{len(missed_ran)} missed times ran, from {missed_ran[:1]}, not the latest 100 of the 150 oldest first, from "
        f"{utc(missed_times(150)[-100:])[0]}; told: {told}")
    assert ran.count(on_time) == 1 and ran[-1] == on_time, f"the on-time {on_time} did not run once, after them: {told}"
    assert one_at_a_time(fake_runs.of("backlog")) == []
    about = [t for t in told if "backlog" in t]
    assert [t for t in about if says(t, 50)] and not [t for t in about if says(t, 51)], \
        f"not told that 50 were not run: {about}"
    if not someone_to_ask:
        assert [t for t in about if says(t, 150) and says(t, 50)], f"the log does not say 150 missed, 50 not: {about}"
    assert len(asked) == (1 if someone_to_ask else 0)


@pytest.mark.invariant("SCH-9")
def test_times_that_come_while_you_are_asked_are_not_missed_and_run_after_the_chosen_missed_runs(env, fake_runs):
    """As above: 150 missed, 01:10 on time at the check 3 s before it, which asks. The question stays open while 01:20
    and 01:30 come (checks a few seconds before and at each). Then Run only the latest 100: the latest 100 missed times
    run, oldest first, then 01:10, 01:20 and 01:30, one at a time; you are told 50 were not run (the times that came
    while you were asked are neither counted as missed nor dropped)."""
    scheduler, told, asked = catch_up_service(env, fake_runs, someone_to_ask=True)
    later = [ON_TIME + dt.timedelta(minutes=10), ON_TIME + dt.timedelta(minutes=20)]
    for moment in [ASKED_AT, ON_TIME] + [m for t in later for m in (t - dt.timedelta(seconds=3), t)]:
        drain(scheduler, moment, fake_runs)
    assert [(flow, count) for flow, count, _ in asked] == [("backlog", 150)], told

    asked[0][2](100)  # Run only the latest 100
    drain(scheduler, later[-1] + dt.timedelta(seconds=5), fake_runs)

    ran = begins(fake_runs.of("backlog"))
    came = utc([ON_TIME] + later)
    assert [t for t in ran if t in came] == came, f"of the times that came while asked, these ran: {ran[100:]}; {told}"
    assert ran == utc(missed_times(150)[-100:]) + came, (
        f"not the latest 100 missed times, oldest first, then {came}: {len(ran) - len(came)} missed times ran, from "
        f"{ran[:1]}; told: {told}")
    assert one_at_a_time(fake_runs.of("backlog")) == []
    about = [t for t in told if "backlog" in t]
    assert [t for t in about if says(t, 50)] and not [t for t in about if says(t, 53)], \
        f"not told that 50 were not run: {about}"
    assert len(asked) == 1
