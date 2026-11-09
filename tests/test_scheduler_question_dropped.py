"""A Catch up question that no longer applies (SCH-9, docs/promises/scheduler.yaml): more than 100 times of a Catch up
schedule were missed, and the service asked you about them; before you answered, their waiting runs were dropped (you
changed or turned off the schedule, or chose Stop now for its flow). By the no-surprises rule, the question's window
closes; an answer given in it is applied to nothing; asked again later, it is a new question in a new window, and only
an answer to that one applies.

The service's checks (Scheduler.tick) run at chosen clock times, with each run's process replaced at the process
boundary by a stand-in (test_scheduler_new_promises.FakeRuns); the question is asked through the service's `ask`, which
the tray service shows as a window. A schedule is saved as the editor saves it: the flow file, then forget_schedules.
The window is the tray service's own, in this process (test_scheduler_new_promises.tray_service)."""

import datetime as dt
import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from taskloom.config import Home  # noqa: E402
from taskloom.flow import load_flow  # noqa: E402
from taskloom.scheduler import forget_schedules  # noqa: E402
from tests.test_paths_scheduler import heartbeat  # noqa: E402
from tests.test_scheduler_new_promises import (CATCH_UP, EVERY_TEN, FIRST_SIGHT, FakeRuns, begins,  # noqa: E402
                                               check_after, clock_shows, drain, flow_yaml, one_at_a_time, pump, says,
                                               schedule, seen_then, tray_service, utc)


@pytest.fixture
def fake_runs(monkeypatch):
    """`taskloom run` processes replaced by stand-ins (FakeRuns)."""
    runs = FakeRuns()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


def save(home: Path, path: Path, *schedules: dict, moment: dt.datetime | None = None):
    """You save the flow with these schedules, as the editor saves it: the flow file, then forget_schedules, at this
    PC's clock (`moment`, if given)."""
    path.write_text(flow_yaml(*schedules), encoding="utf-8")
    if moment is None:
        forget_schedules(Home(home), path, load_flow(path).triggers)
        return
    with clock_shows(moment):
        forget_schedules(Home(home), path, load_flow(path).triggers)


ASKED = check_after(150)  # 2026-01-06 01:05: 150 times missed, you are asked
CHANGED = ASKED + dt.timedelta(minutes=1)  # you change the schedule to every 15 minutes and save
BACK = CHANGED + dt.timedelta(minutes=1)  # you change it back to every 10 minutes and save: it starts afresh
AGAIN = [dt.datetime(2026, 1, 6, 1, 10) + k * dt.timedelta(minutes=10) for k in range(120)]  # its times after BACK
ASKED_AGAIN = AGAIN[-1] + dt.timedelta(minutes=5)  # 2026-01-06 21:05: all 120 missed, you are asked again


@pytest.mark.invariant("SCH-9")
def test_an_answer_to_a_question_whose_missed_runs_were_dropped_is_applied_to_nothing(env, fake_runs):
    """A Catch up schedule every 10 minutes missed 150 times, and you are asked about them. Before you answer, you
    change its time and save the flow: its missed runs are dropped, so that question no longer applies. A minute later
    you change it back and save: it starts afresh, as a new schedule does. It then misses 120 times, and you are asked
    again: a new question. In the first question's window you answer Run only the latest 5: that is applied to
    nothing (no run begins; the 120 missed runs still wait for the new question's answer). You answer the new
    question with Run only the latest 3: the latest 3 run, oldest first, one at a time, and you are told the other
    117 are not run."""
    path = schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN)})["backlog"]
    told, asked = [], []
    scheduler = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"),
                                  ask=lambda flow, count, answer: asked.append((flow, count, answer)))
    scheduler.tick(FIRST_SIGHT)
    drain(scheduler, ASKED, fake_runs)
    assert [(flow, count) for flow, count, _ in asked] == [("backlog", 150)], f"not arranged: {told}"

    save(env.home, path, {"cron": "*/15 * * * *", "misfire": "catch_up"}, moment=CHANGED)
    drain(scheduler, CHANGED, fake_runs)
    save(env.home, path, EVERY_TEN, moment=BACK)
    drain(scheduler, BACK, fake_runs)
    drain(scheduler, ASKED_AGAIN, fake_runs)
    assert [(flow, count) for flow, count, _ in asked] == [("backlog", 150), ("backlog", 120)], (
        f"not arranged: you were not asked again about the 120 times missed after the change; told: {told}")
    assert fake_runs.of("backlog") == [], f"not arranged: runs of backlog began before any answer; told: {told}"

    asked[0][2](5)  # Run only the latest 5, in the window of the question that no longer applies
    drain(scheduler, ASKED_AGAIN, fake_runs)
    ran = begins(fake_runs.of("backlog"))
    assert ran == [], (
        f"the answer given to the question whose missed runs were dropped was applied to the new question: "
        f"{len(ran)} of its 120 missed runs began ({ran[:1]} to {ran[-1:]}); told: {told}")

    asked[1][2](3)  # Run only the latest 3, in the new question's window
    drain(scheduler, ASKED_AGAIN, fake_runs)
    backlog = fake_runs.of("backlog")
    assert begins(backlog) == utc(AGAIN[-3:]), (
        f"the new question's answer (the latest 3) did not apply as usual: {begins(backlog)}; told: {told}")
    assert one_at_a_time(backlog) == []
    assert [t for t in told if "backlog" in t and says(t, 117)], f"not told that 117 were not run: {told}"


def catch_up_windows() -> list:
    """The Catch up missed runs? windows you see."""
    return [w for w in QApplication.topLevelWidgets() if w.isVisible() and w.windowTitle() == CATCH_UP]


@pytest.mark.invariant("SCH-9")
def test_the_tray_closes_the_window_of_a_catch_up_question_whose_missed_runs_were_dropped(qapp, env, monkeypatch,
                                                                                            fake_runs):
    """The tray service, off for 120 hours: an hourly Catch up schedule missed 120 times, and a Catch up missed runs?
    window asks about them. Before you answer, you change the schedule's time and save the flow: its missed runs are
    dropped, so the question no longer applies. Within the service's next checks, its window has closed."""
    now = dt.datetime.now()
    minute = (now.minute + 30) % 60  # its times are at least 25 minutes from now: none comes during the test
    path = schedule(env.home, env.root, {"backlog": flow_yaml({"cron": f"{minute} * * * *",
                                                                "misfire": "catch_up"})})["backlog"]
    seen_then(env.home, now - dt.timedelta(hours=120))  # the service first saw it then, and has been off since
    seen = {}

    def drive(tray, result):
        if not pump(lambda: catch_up_windows(), 15):
            return
        seen["asked"] = " ".join(label.text() for w in catch_up_windows() for label in w.findChildren(QLabel))
        before = heartbeat(env.home)
        # (its new times are at least 40 minutes from now: none comes during the test)
        save(env.home, path, {"cron": f"{(minute + 15) % 60} * * * *", "misfire": "catch_up"})
        beats = {before}

        def closed_after_checks() -> bool:
            beats.add(heartbeat(env.home))
            return len(beats - {None}) >= 3 and not catch_up_windows()

        pump(closed_after_checks, 6)
        seen["checks"] = len(beats - {None, before})  # (each a check that began after the save)
        seen["open"] = [label.text() for w in catch_up_windows() for label in w.findChildren(QLabel)]
        for window in catch_up_windows():  # (nothing of this test is left open; only Proceed closes it)
            window.hide()

    tray_service(monkeypatch, env.home, drive)

    assert "asked" in seen and "backlog" in seen["asked"], f"not arranged: no {CATCH_UP!r} window about backlog"
    assert seen["checks"] >= 2, f"not arranged: the service checked {seen['checks']} times after the save"
    assert seen["open"] == [], (
        f"its missed runs were dropped, yet the window of that question is still open after "
        f"{seen['checks']} checks: {seen['open'][:1]}")
