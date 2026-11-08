"""What the scheduler service must always do and does not do yet (docs/promises/scheduler.yaml), in the two cases TLA+
trace validation found (specs/formal/README.md, "Trace validation": the service's memory of saved runs goes stale):
a schedule saved changed just before its flow's next time, while its run for an earlier time waits its turn (SCH-11);
and Stop now, then the service turned on again before its next check (SCH-4).

The times use the service's checks (Scheduler.tick) at chosen clock times, the service made as `taskloom scheduler`
makes it (FakeRuns.service), with each run's process replaced at the process boundary by a stand-in that claims as the
real run does (test_scheduler_held_times.Runs). A changed schedule is saved as the editor saves it: the flow file,
then forget_schedules. Turning the service off is sent as the dialog's Proceed sends it (request_turn_off)."""

import datetime as dt
import subprocess

import pytest

from taskloom.config import Home
from taskloom.flow import load_flow
from taskloom.scheduler import forget_schedules, request_turn_off, set_paused
from tests.test_scheduler_held_times import Runs, check, clock, for_time
from tests.test_scheduler_new_promises import begins, clock_shows, flow_yaml, schedule, utc


@pytest.fixture
def fake_runs(monkeypatch) -> Runs:
    runs = Runs()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


@pytest.mark.invariant("SCH-11")
def test_a_time_after_a_changed_schedule_is_not_skipped_as_waiting_its_turn_when_nothing_waits(env, fake_runs):
    """Max concurrent runs 1. other runs at 06:05; nightly (Overlap skip) at 06:05 and 06:10. other's run goes on, so
    nightly's 06:05 waits for a free slot. At 06:09:59.5, after the service's check at 06:09:59, you save nightly with
    its schedule changed to 06:10 only: its waiting 06:05 run is dropped. other's run ends just after. At 06:10 no run
    of nightly is going or waiting, so its 06:10 runs, and you are not told it waits its turn."""
    (env.home / "settings.yaml").write_text("max_concurrent_runs: 1\n", encoding="utf-8")
    paths = schedule(env.home, env.root, {"other": flow_yaml({"cron": "5 6 * * *"}),
                                          "nightly": flow_yaml({"cron": "5,10 6 * * *"})})
    told_you = []
    fake_runs.holding.add("other")  # its run goes on until let go
    service = fake_runs.service(env.home, lambda title, message: told_you.append(f"{title}: {message}"))
    for moment in ("06:00:00", "06:05:00", "06:09:59"):
        check(service, fake_runs, clock(moment))
    assert begins(fake_runs.of("other")) == utc([clock("06:05:00")]), f"not arranged: {told_you}"
    assert fake_runs.of("nightly") == [] and env.query("SELECT waits FROM waiting_runs") == [("slot",)], (
        f"not arranged: nightly's 06:05 is not waiting for a slot; told: {told_you}")

    path = paths["nightly"]  # you save it, its schedule changed, as the editor saves it
    path.write_text(flow_yaml({"cron": "10 6 * * *"}), encoding="utf-8")
    with clock_shows(clock("06:09:59") + dt.timedelta(milliseconds=500)):
        forget_schedules(Home(env.home), path, load_flow(path).triggers)
    fake_runs.holding.discard("other")  # other's run ends before the next check
    check(service, fake_runs, clock("06:10:00"))

    waiting = [t for t in told_you if "still waiting its turn" in t]
    assert waiting == [], f"no run of nightly was going or waiting at 06:10, yet you were told: {waiting}"
    ran = begins(for_time(fake_runs.of("nightly"), clock("06:10:00")))
    assert ran == utc([clock("06:10:00")]), (
        f"nothing went at 06:10, yet nightly's run for 06:10 did not begin: {ran}; told: {told_you}")


@pytest.mark.invariant("SCH-4")
def test_stop_now_then_on_again_before_the_next_check_starts_no_process_for_a_dropped_waiting_run(env, fake_runs):
    """nightly (Overlap queue) runs at 06:05 and 06:10. Its 06:05 run goes on, so its 06:10 waits its turn behind it.
    At 06:10:00.5 you turn the service off with Stop now for nightly (Proceed), and turn it on again at once, before the
    service's next check. Stop now dropped the waiting 06:10 run: the service starts no process for it."""
    paths = schedule(env.home, env.root, {"nightly": flow_yaml({"cron": "5,10 6 * * *"}, overlap="queue")})
    told_you = []
    fake_runs.holding.add("nightly")  # its run goes on until let go
    service = fake_runs.service(env.home, lambda title, message: told_you.append(f"{title}: {message}"))
    for moment in ("06:00:00", "06:05:00", "06:10:00"):
        check(service, fake_runs, clock(moment))
    assert begins(fake_runs.of("nightly")) == utc([clock("06:05:00")]), f"not arranged: {told_you}"
    assert env.query("SELECT waits FROM waiting_runs") == [("flow",)], (
        f"not arranged: nightly's 06:10 is not waiting behind its run; told: {told_you}")

    request_turn_off(Home(env.home), {str(paths["nightly"]): "stop"})  # Proceed, with Stop now for nightly
    set_paused(Home(env.home), False)  # on again at once
    fake_runs.holding.discard("nightly")  # (Stop now stops its run going: it ends)
    for moment in ("06:10:01", "06:10:02", "06:10:03"):
        check(service, fake_runs, clock(moment))

    started = for_time(fake_runs.of("nightly"), clock("06:10:00"))
    assert started == [], (
        f"Stop now dropped nightly's waiting 06:10 run, yet the service started its process: "
        f"{[(r.start_at, r.not_begun) for r in started]}; told: {told_you}")
