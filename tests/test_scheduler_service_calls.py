"""The scheduler service's functions that the editor and the tray icon call, called as they call them, in this process
and without Qt: which flows have runs going (the turn-off dialog lists them), turning the service off with Stop now or
Let it finish (SCH-4), starting at login (SCH-3), where an Every N schedule counts from (the countdown under a Schedule
block, SCH-7), and forgetting a saved schedule's state (SCH-10). Each takes a few seconds at most: mutation testing
runs them for each bug it plants in that code (pyproject.toml, [tool.mutmut])."""

import datetime as dt
import json
import subprocess
import sys
import time

import pytest
import yaml

from taskloom.config import Home
from taskloom.flow import load_flow, real_path
from taskloom.history import History
from taskloom.scheduler import (Scheduler, counted_from, forget_schedules, paused, request_turn_off,
                                running_scheduler_version, runs_going, service_running, set_start_at_login,
                                tell_crashed)
from tests.test_scheduler_at_its_time import clock
from tests.test_scheduler_new_promises import (EVERY_TEN, FIRST_SIGHT, HOURLY, FakeRuns, begins, check_after,
                                               clock_shows, drain, end_runs, flow_yaml, going, history, hhmm,
                                               hold_until, missed_times, schedule, seen_then, statuses, utc,
                                               wait_until, write)
from tests.test_scheduler_promises import on
from tests.test_scheduler_while_off import Runs, has_loaded

MINUTE = dt.timedelta(minutes=1)
# A flow whose schedules are its own `triggers:` (written by hand), not Schedule blocks.
FLOW_LEVEL = """\
overlap: queue
params: {when: {type: datetime, expr: "now()"}}
triggers:
%s
blocks:
  work: {type: logic.python, config: {inputs: [], outputs: [], code: "pass"}}
"""


@pytest.fixture
def fake_runs(monkeypatch) -> FakeRuns:
    """The scheduled runs' processes replaced by stand-ins that claim their time as the real run does (FakeRuns)."""
    runs = FakeRuns()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


def run_process(env, path, scheduled: bool) -> subprocess.Popen:
    """A real `taskloom run` of the flow, as the scheduler service starts one (it records the schedule it is for), or
    as you start one yourself from a terminal."""
    extra = ["--scheduled", "every 60 minutes", "--trigger", "s1"] if scheduled else []
    return subprocess.Popen([sys.executable, "-m", "taskloom.cli", "run", str(path), *extra], cwd=env.root,
                            env=env.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


@pytest.mark.invariant("SCH-4")
def test_proceeding_with_stop_now_while_no_service_runs_stops_that_flows_scheduled_runs_alone(env):
    """No scheduler service runs (it crashed while the turn-off dialog was open). nightly and weekly each have a run
    going that the service started, and a run of nightly you started yourself goes too. The dialog lists the flows
    with runs the service started: nightly and weekly, each once, in the order their runs started (not your run).
    You choose Stop now for nightly, Let it finish for weekly, and Proceed: nightly's scheduled run stops and is
    recorded as cancelled; weekly's run and yours go on, and finish. scheduled.yaml spells the flows' paths as they
    were saved (through ".."), not as their runs record them: the dialog and Stop now go by its spelling."""
    real = schedule(env.home, env.root, {"nightly": flow_yaml(HOURLY, work=hold_until("nightly")),
                                         "weekly": flow_yaml(HOURLY, work=hold_until("weekly"))})
    home = Home(env.home)
    (env.root / "saved").mkdir()
    paths = {name: env.root / "saved" / ".." / path.name for name, path in real.items()}  # (as listed)
    for name in paths:
        home.set_scheduled(real[name], False)
        home.set_scheduled(paths[name], True)
    procs = []
    try:
        procs.append(run_process(env, paths["nightly"], scheduled=True))
        assert wait_until(lambda: statuses(env.home, "nightly") == ["running"], 30), history(env.home)
        procs.append(run_process(env, paths["weekly"], scheduled=True))
        procs.append(run_process(env, paths["nightly"], scheduled=False))
        assert wait_until(lambda: len([r for r in history(env.home) if r["status"] == "running"]) == 3, 30), \
            f"not arranged: three runs going: {history(env.home)}"

        listed = runs_going(home)
        request_turn_off(home, {str(paths["nightly"]): "stop", str(paths["weekly"]): "finish"})  # Proceed
        stopped = wait_until(lambda: procs[0].poll() is not None, 15)
        others_going = procs[1].poll() is None and procs[2].poll() is None
        write(env.root / "weekly.go", "go")
        write(env.root / "nightly.go", "go")
        finished = wait_until(lambda: procs[1].poll() is not None and procs[2].poll() is not None, 30)
        recorded = [(r["flow"], r["status"]) for r in history(env.home)]
    finally:
        write(env.root / "weekly.go", "go")
        write(env.root / "nightly.go", "go")
        end_runs(env.home)
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(10)

    assert listed == [(str(paths["nightly"]), "nightly"), (str(paths["weekly"]), "weekly")], listed
    assert stopped and others_going, f"Stop now did not stop nightly's scheduled run alone: {recorded}"
    assert finished and sorted(recorded) == [("nightly", "cancelled"), ("nightly", "success"), ("weekly", "success")], \
        recorded
    assert paused(home), "Proceed did not turn the service off"


@pytest.mark.invariant("SCH-4")
def test_stop_now_and_let_it_finish_with_the_service_on_follow_each_flows_choice_then_it_turns_off(env):
    """The service runs nightly (two Schedule blocks) and weekly (three), each every 60 minutes with Overlap queue: at
    its first check each flow's first run goes and the others wait their turn behind it. You turn the service off:
    Stop now for nightly, Let it finish for weekly; Proceed. Nothing new starts; nightly's run going stops and is
    recorded as cancelled, and its waiting run is dropped (it never begins); weekly's run finishes and its waiting runs
    still run, one after another; the service turns off only once they are done. Nothing failed or was skipped, so
    nothing is told (README: it notifies you about failed or skipped runs)."""
    paths = schedule(env.home, env.root, {
        "nightly": flow_yaml(HOURLY, HOURLY, work=hold_until("nightly"), overlap="queue"),
        "weekly": flow_yaml(HOURLY, HOURLY, HOURLY, work=hold_until("weekly"), overlap="queue")})
    home = Home(env.home)
    told = []
    with on(env):
        service = Scheduler(home, lambda title, message: told.append((title, message)))
        try:
            service.tick()
            assert wait_until(lambda: (service.tick() or going(env.home)) == {"nightly", "weekly"}, 30), \
                f"not arranged: a run of each going: {history(env.home)}"
            request_turn_off(home, {str(paths["nightly"]): "stop", str(paths["weekly"]): "finish"})  # Proceed
            service.tick()
            stopped = wait_until(lambda: service.tick() or statuses(env.home, "nightly") == ["cancelled"], 20)
            weekly_going = statuses(env.home, "weekly") == ["running"] and not service.off
            write(env.root / "weekly.go", "go")
            write(env.root / "nightly.go", "go")
            off = wait_until(lambda: service.tick() or service.off, 30)  # (it checks until it is off, as it runs)
            done = wait_until(lambda: statuses(env.home, "weekly") == ["success"] * 3, 30)
        finally:
            write(env.root / "weekly.go", "go")
            write(env.root / "nightly.go", "go")
            end_runs(env.home)

    assert stopped and weekly_going, f"Stop now did not stop nightly's run alone: {history(env.home)}"
    assert off and done, f"weekly's waiting runs did not all run before the service turned off: {history(env.home)}"
    assert statuses(env.home, "nightly") == ["cancelled"], f"nightly's waiting run began: {history(env.home)}"
    assert told == [], told


@pytest.mark.invariant("SCH-7")
@pytest.mark.parametrize("text", [
    flow_yaml({"every": "5 minutes"}, {"cron": "0 7 * * *"}),
    FLOW_LEVEL % "  - schedule: {every: 5 minutes}\n  - schedule: {cron: '0 7 * * *'}",
], ids=["Schedule blocks", "the flow's own triggers"])
def test_an_every_n_schedule_is_counted_on_from_where_the_service_counts_it(env, fake_runs, text):
    """The countdown under an Every N Schedule block goes to its next run as the service runs it: the editor asks
    where the service counts each schedule from (counted_from) and counts on from there; so does Flows running for a
    flow-level schedule. Before any service has seen the flow there is none (it starts when the service picks it up),
    nor for a schedule on the clock. First seen at 10:00:14, every 5 minutes counts from 10:00:14, and once its
    10:05:14 run has run, from 10:05:14: the next run then is 10:10:14, as the service runs it and waits for it."""
    paths = schedule(env.home, env.root, {"report": text})
    home, triggers = Home(env.home), load_flow(paths["report"]).triggers
    asked = []
    asked.append(counted_from(home, paths["report"], triggers))
    service = fake_runs.service(env.home)
    service.tick(clock("10:00:14.250"))
    asked.append(counted_from(home, paths["report"], triggers))
    service.tick(clock("10:05:14.600"))
    asked.append(counted_from(home, paths["report"], triggers))
    for moment in ("10:05:15", "10:09:59", "10:10:14.100"):
        service.tick(clock(moment))

    assert asked == [[None, None], [clock("10:00:14"), None], [clock("10:05:14"), None]], asked
    assert counted_from(home, paths["report"], triggers[1:]) == [None]  # (a schedule on the clock alone: none)
    assert triggers[0].next_after(clock("10:05:15"), asked[-1][0]) == clock("10:10:14")
    assert service.seconds_to_next_check(clock("10:10:13.600")) == pytest.approx(0.4)
    assert begins(fake_runs.of("report")) == utc(
        [clock("10:00:14"), clock("10:05:14"), clock("10:10:14")])


@pytest.mark.invariant("SCH-10")
def test_a_schedule_turned_off_and_on_again_with_no_service_running_starts_afresh(env, fake_runs):
    """report (Overlap queue) runs every 5 minutes, first seen at 10:00:14: its 10:00:14 run goes on, and at 10:05:14
    its next run waits its turn behind it; then the service stops. While it is off you untick Active and save, then
    tick it again and save: each time the editor saves the file and forgets the state of the flow's schedules that
    are not on (forget_schedules). The service is back at 10:20:00: the schedule starts afresh, as a new one. Its run
    that waited does not begin, the times from before (10:10:14, 10:15:14) neither run nor count as missed, and every
    5 minutes counts from 10:20:00."""
    paths = schedule(env.home, env.root, {"report": flow_yaml({"every": "5 minutes"}, overlap="queue")})
    told = []
    service = fake_runs.service(env.home)
    fake_runs.holding.add("report")
    service.tick(clock("10:00:14"))
    service.tick(clock("10:05:14"))
    fake_runs.stop()  # (the service stops; its run going ends with it)
    fake_runs.holding.clear()
    for active in (False, True):
        write(paths["report"], flow_yaml({"every": "5 minutes", "active": active}, overlap="queue"))
        forget_schedules(Home(env.home), paths["report"], load_flow(paths["report"]).triggers)
    service = fake_runs.service(env.home, lambda title, message: told.append(f"{title}: {message}"))
    for moment in ("10:20:00", "10:20:01", "10:20:02", "10:25:00"):
        drain(service, clock(moment), fake_runs)

    assert [hhmm(run) for run in fake_runs.of("report")] == ["10:00", "10:20", "10:25"], \
        [r.start_at for r in fake_runs.started]
    assert not [t for t in told if "missed" in t.lower()], told


@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-10")
def test_a_clock_schedule_turned_on_with_no_service_running_has_its_times_after_the_save_as_missed_ones(
        env, fake_runs):
    """With the service off, at 10:00:30 you save report with a new schedule, every minute on the clock, Catch up: the
    editor forgets the state of its other schedules and starts the new one from the save (forget_schedules). The
    service is back at 10:03:10: 10:01, 10:02 and 10:03 came after the save, so each runs, oldest first (Catch up);
    nothing from before the save does."""
    paths = schedule(env.home, env.root, {"report": flow_yaml({"cron": "0 5 * * *"})})
    seen_then(env.home, clock("09:00:00"))  # (a service saw its old schedule, and stopped)
    write(paths["report"], flow_yaml({"cron": "* * * * *", "misfire": "catch_up"}))
    with clock_shows(clock("10:00:30")):
        forget_schedules(Home(env.home), paths["report"], load_flow(paths["report"]).triggers)
    service = fake_runs.service(env.home)
    for moment in ("10:03:10", "10:03:11", "10:03:12", "10:03:13"):
        drain(service, clock(moment), fake_runs)

    assert [hhmm(run) for run in fake_runs.of("report")] == ["10:01", "10:02", "10:03"], \
        [r.start_at for r in fake_runs.started]


@pytest.mark.invariant("SCH-13")
def test_a_crashed_scheduled_run_found_after_the_service_is_told_and_emailed_and_a_failed_email_is_told_too(env, smtp):
    """The editor finds a scheduled run whose process has gone and tells you as the service does (tell_crashed, called
    as the editor calls it): "<flow> crashed", the run process stopped unexpectedly, plus an email as the flow's
    notify asks. An email that cannot be sent (the mail connection is gone) is told after: Cannot send email."""
    write(env.home / "settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    asks = write(env.root / "asks.yaml", flow_yaml(HOURLY, notify={"when": ["failure"], "to": ["team@example.com"]}))
    told, later = [], []
    tell_crashed(Home(env.home), str(asks), lambda *said: told.append(said), lambda *said: later.append(said)).join(30)
    write(env.home / "settings.yaml", "notify_email: me@example.com\nsmtp_connection: gone\n")
    tell_crashed(Home(env.home), str(asks), lambda *said: told.append(said), lambda *said: later.append(said)).join(30)

    assert told == [("asks crashed", "the run process stopped unexpectedly")] * 2, told
    assert [(str(m["Subject"]), str(m["To"])) for m in smtp.messages] == [("[Taskloom] asks crashed",
                                                                          "team@example.com")]
    assert [title for title, _ in later] == ["Cannot send email"] and "gone" in later[0][1], later


@pytest.mark.invariant("SCH-5")
def test_the_service_wakes_at_the_next_time_of_an_every_n_schedule_not_up_to_a_second_after_it(env, fake_runs):
    """Between checks the service waits until the soonest due time, and at most a second (it then rereads changed
    flows): also for an Every N schedule, counted from where the service counts it. First seen at 10:00:14.000, every
    5 minutes is due next at 10:05:14: asked at 10:05:13.700 it waits 0.3 s, so the run starts at its time."""
    schedule(env.home, env.root, {"report": flow_yaml({"every": "5 minutes"})})
    service = fake_runs.service(env.home)
    service.tick(clock("10:00:14"))

    assert service.seconds_to_next_check(clock("10:05:13.700")) == pytest.approx(0.3)
    assert service.seconds_to_next_check(clock("10:05:10")) == pytest.approx(1)


@pytest.mark.invariant("SCH-14")
def test_an_email_the_service_cannot_send_about_skipped_runs_is_told_and_the_service_goes_on(env, fake_runs):
    """report (every minute, Misfire Skip) was first seen at 10:00:30; the service is back at 10:05:30. 10:05 is less
    than 2 minutes late and runs; the 4 times before it were missed: Missed runs skipped is told, and emailed as the
    settings ask, but the mail connection is gone: that is told too (Cannot send email), and the service goes on
    (its next time runs)."""
    write(env.home / "settings.yaml", "notify_email: me@example.com\nnotify_on: [skipped]\nsmtp_connection: gone\n")
    schedule(env.home, env.root, {"report": flow_yaml({"cron": "* * * * *", "misfire": "skip"})})
    seen_then(env.home, clock("10:00:30"))
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append((title, message)))
    service.tick(clock("10:05:30"))
    assert wait_until(lambda: service.tick(clock("10:05:31")) or len(told) >= 2, 30), told
    service.tick(clock("10:06:01"))

    assert [title for title, _ in told] == ["Missed runs skipped", "Cannot send email"], told
    assert "4 run(s)" in told[0][1] and "gone" in told[1][1], told
    assert [hhmm(run) for run in fake_runs.of("report")] == ["10:05", "10:06"]


@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("limit, ran, told_words", [
    (30, 30, ["Missed runs skipped", "you chose to run only the latest 30 of 150 missed runs",
              "other 120 are not run"]),
    (150, 150, None),
    ("server", 100, ["Missed runs skipped", "150 runs were due while the scheduler was off", "latest 100 run",
                     "other 50 are missed"]),
], ids=["Run only the latest 30", "Run only the latest 150 (all)", "a server: the latest 100"])
def test_missed_times_left_out_are_told_as_missed_runs_skipped_saying_how_many(env, fake_runs, limit, ran, told_words):
    """150 times of a Catch up schedule of the flow Night backlog (backlog.yaml) were missed. Asked about Night
    backlog's 150 missed runs, you choose Run only the latest N: the latest N run, oldest first, and a Missed runs
    skipped notification says, for Night backlog, that you chose them and how many others are not run; with N 150,
    all of them run and nothing is told. A server's service (no one to ask) runs the latest 100 and says how many of
    the 150 were missed."""
    schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN, name="Night backlog")})
    told, asked, names = [], [], []

    def ask(flow, count, answer):  # (the tray icon's question)
        names.append((flow, count))
        asked.append(answer)

    service = fake_runs.service(env.home, lambda title, message: told.append((title, message)),
                                ask=None if limit == "server" else ask)
    service.tick(FIRST_SIGHT)
    drain(service, check_after(150), fake_runs)
    if asked:
        asked[0](limit)
        drain(service, check_after(150) + MINUTE, fake_runs)

    assert begins(fake_runs.of("backlog")) == utc(missed_times(150)[-ran:])
    if told_words is None:
        assert told == [], told
    else:
        (title, message), = told
        assert [title] + [w for w in told_words[1:] if w in message] == told_words, told
        assert message.startswith("Night backlog: "), told
    assert names == ([] if limit == "server" else [("Night backlog", 150)]), names


def test_which_taskloom_the_running_service_is_is_known_only_from_the_service_that_said_so(env):
    """The editor asks which Taskloom the service running is, to offer a restart with a newer one (README): the
    service says its version and process (scheduler.version) next to its process (scheduler.pid). A service of an
    older Taskloom says no version: the version left by the service before it is not taken for its own, so the
    restart is offered; with nothing said, none is known."""
    home = Home(env.home)
    asked = [running_scheduler_version(home)]
    write(env.home / "scheduler.pid", "4242\n")
    write(env.home / "scheduler.version", "0.37.0 4242\n")
    asked.append(running_scheduler_version(home))
    write(env.home / "scheduler.pid", "4343\n")  # (an older Taskloom's service, which says no version)
    asked.append(running_scheduler_version(home))

    assert asked == [None, "0.37.0", None], asked


@pytest.mark.invariant("SCH-1")
def test_whether_a_service_runs_is_told_by_its_lock_and_looking_takes_nothing_from_it(env):
    """Whether a scheduler service runs for this Taskloom folder (the editor's button, a run as it begins: on or off)
    is told by the folder's lock, which a running service holds: none before it starts, yes while it holds it, and
    looking does not take it from the service, nor keep it once the service has let go."""
    home = Home(env.home)
    asked = [service_running(home)]
    with on(env):
        asked += [service_running(home), service_running(home)]
    asked.append(service_running(home))
    with on(env):  # (a service can still start: looking kept nothing)
        asked.append(service_running(home))

    assert asked == [False, True, True, False, True], asked


@pytest.mark.invariant("SCH-14")
def test_an_answer_about_missed_runs_of_a_flow_broken_meanwhile_is_applied_and_told_and_the_service_goes_on(
        env, fake_runs):
    """Asked about the 150 missed runs of Night backlog (backlog.yaml), you answer Run only the latest 30, but a hand
    edit has broken backlog.yaml meanwhile. The service goes on: it says the flow cannot be read, and that the other
    120 are not run (naming it by its file, backlog, as the flow cannot be read); none of its runs begins while it
    cannot be read."""
    paths = schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN, name="Night backlog")})
    told, asked = [], []
    service = fake_runs.service(env.home, lambda title, message: told.append((title, message)),
                                ask=lambda flow, count, answer: asked.append(answer))
    service.tick(FIRST_SIGHT)
    drain(service, check_after(150), fake_runs)
    write(paths["backlog"], "blocks: [")
    asked[0](30)
    drain(service, check_after(150) + MINUTE, fake_runs)

    assert [title for title, _ in told] == ["Scheduled flow cannot be read", "Missed runs skipped"], told
    assert told[1][1].startswith("backlog: you chose to run only the latest 30 of 150"), told
    assert begins(fake_runs.of("backlog")) == [], begins(fake_runs.of("backlog"))


@pytest.mark.invariant("SCH-9")
def test_two_flows_asked_about_their_missed_runs_each_get_the_answer_given_for_them(env, fake_runs):
    """backlog and archive each missed 150 times of a Catch up schedule: you are asked about each. You answer for
    archive Run only the latest 30, and for backlog Run all of them: archive runs its latest 30, backlog all 150."""
    schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN), "archive": flow_yaml(EVERY_TEN)})
    asked = {}
    service = fake_runs.service(env.home, ask=lambda flow, count, answer: asked.setdefault(flow, answer))
    service.tick(FIRST_SIGHT)
    drain(service, check_after(150), fake_runs)
    asked["archive"](30)
    asked["backlog"](None)
    drain(service, check_after(150) + MINUTE, fake_runs)

    assert sorted(asked) == ["archive", "backlog"], asked
    assert begins(fake_runs.of("archive")) == utc(missed_times(150)[-30:])
    assert begins(fake_runs.of("backlog")) == utc(missed_times(150))




def runs_of(fake_runs, flow: str) -> list[tuple[str, str]]:
    """The schedule each run of the flow that began was for, and its time (HH:MM:SS on this PC's clock)."""
    return [(run.args[run.args.index("--scheduled") + 1], run.start_at[11:19]) for run in fake_runs.of(flow)
            if run.reached and run.not_begun is None]


@pytest.mark.invariant("SCH-10")
@pytest.mark.invariant("SCH-7")
def test_a_flow_saved_forgets_only_its_schedules_that_are_off_and_keeps_the_others_and_other_flows_as_they_were(
        env, fake_runs):
    """report has two flow-level schedules, every 5 and every 7 minutes (Overlap queue); daily_load runs every 3
    minutes. The service first saw them at 10:00:14: report's every-5 run goes on, and its every-7 run, then its
    10:05:14 run, wait their turn behind it; then the service stops. With no service running you take report's every 7
    minutes off and save: the editor forgets the state of the schedules that are off (forget_schedules). Back at
    10:06:00, every 5 minutes keeps its rhythm and its waiting run (10:05:14 begins, then 10:10:14), every 7 minutes'
    waiting run is dropped, and daily_load keeps its rhythm (10:06:14, 10:09:14)."""
    both = FLOW_LEVEL % "  - schedule: {every: 5 minutes}\n  - schedule: {every: 7 minutes}"
    paths = schedule(env.home, env.root, {"report": both,
                                          "daily_load": flow_yaml({"every": "3 minutes"})})
    service = fake_runs.service(env.home)
    fake_runs.holding.add("report")
    service.tick(clock("10:00:14"))
    service.tick(clock("10:05:14"))
    fake_runs.stop()
    fake_runs.holding.clear()
    write(paths["report"], FLOW_LEVEL % "  - schedule: {every: 5 minutes}")
    forget_schedules(Home(env.home), paths["report"], load_flow(paths["report"]).triggers)
    service = fake_runs.service(env.home)
    for moment in ("10:06:00", "10:06:01", "10:06:02", "10:06:14", "10:09:14", "10:10:14"):
        drain(service, clock(moment), fake_runs)

    assert runs_of(fake_runs, "report") == [("every 5 minutes", "10:00:14"), ("every 5 minutes", "10:05:14"),
                                            ("every 5 minutes", "10:10:14")], runs_of(fake_runs, "report")
    assert [time for _, time in runs_of(fake_runs, "daily_load")] == [
        "10:00:14", "10:03:14", "10:06:14", "10:09:14"], runs_of(fake_runs, "daily_load")


@pytest.mark.invariant("SCH-14")
def test_a_mistake_in_the_list_of_scheduled_flows_is_told_once_naming_each_entry_left_out(env, fake_runs):
    """scheduled.yaml, edited by hand, has an empty item and a number next to a flow listed right: the service tells
    you once (Mistake in the list of scheduled flows) which entries it leaves out, each as you would read it (an
    empty item, 5), and runs the flow listed right; told again only once the list changes."""
    paths = schedule(env.home, env.root, {"listed": flow_yaml({"every": "1 minute"})})
    write(env.home / "scheduled.yaml", f"flows:\n  -\n  - 5\n  - {json.dumps(str(paths['listed']))}\n")
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append((title, message)))
    for moment in ("10:00:14", "10:00:15", "10:01:14"):
        drain(service, clock(moment), fake_runs)

    (title, message), = told
    assert title == "Mistake in the list of scheduled flows", told
    assert "left out: an empty item, 5. The other flows listed run." in message, message
    assert [hhmm(run) for run in fake_runs.of("listed")] == ["10:00", "10:01"]


@pytest.mark.invariant("SCH-14")
def test_a_scheduled_flow_with_a_setting_this_taskloom_does_not_know_says_a_newer_taskloom_may_have_saved_it(
        env, fake_runs):
    """A scheduled flow whose block has a setting this Taskloom does not know (a newer Taskloom saved it): the
    service says the flow cannot be read, why, and that a newer Taskloom may have saved it (restart the scheduler from
    it); one with a plain mistake (a minute past 59 in its cron line) is told without that hint."""
    newer = yaml.safe_load(flow_yaml(HOURLY))
    newer["blocks"]["work1"]["retry_on"] = ["timeout"]
    schedule(env.home, env.root, {"newer": yaml.safe_dump(newer), "mistaken": flow_yaml({"cron": "61 * * * *"})})
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append((title, message)))
    service.tick(clock("10:00:00"))

    said = {message.split(":")[0]: message for title, message in told if title == "Scheduled flow cannot be read"}
    assert sorted(said) == ["mistaken.yaml", "newer.yaml"], told
    assert "unknown key 'retry_on'" in said["newer.yaml"] and "restart the scheduler from it" in said["newer.yaml"]
    assert "restart the scheduler" not in said["mistaken.yaml"], said


def test_each_check_leaves_the_moment_the_keeper_on_another_pc_reads_to_tell_the_service_is_not_hung(env, fake_runs):
    """The keeper on your PC tells a server's scheduler service is alive, not hung, by its heartbeat (README: it
    replaces a hung one): the moment of its latest check, in whole seconds since 1970 as the server's `date +%s`
    gives them, in scheduler.heartbeat in its Taskloom folder. Each check writes it anew."""
    schedule(env.home, env.root, {"report": flow_yaml(HOURLY)})
    service = fake_runs.service(env.home)
    service.tick(clock("10:00:00"))
    first = (env.home / "scheduler.heartbeat").read_text(encoding="utf-8")
    (env.home / "scheduler.heartbeat").write_text("0", encoding="utf-8")
    service.tick(clock("10:00:01"))
    second = (env.home / "scheduler.heartbeat").read_text(encoding="utf-8")

    assert first.isdigit() and abs(int(first) - time.time()) < 10, first
    assert second.isdigit() and abs(int(second) - time.time()) < 10, second


@pytest.fixture
def loading_runs(monkeypatch) -> Runs:
    """Stand-ins for the scheduled runs' processes where the next run of a flow in .loading is still loading until
    the test says it has loaded (has_loaded), then claims its time as the real run does."""
    runs = Runs()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


@pytest.mark.invariant("SCH-8")
@pytest.mark.parametrize("meanwhile", ["taken off the list", "its schedule changed", "Let it finish"])
def test_a_run_still_loading_does_not_begin_once_its_flow_is_taken_off_its_schedule_changed_or_the_service_off(
        env, loading_runs, meanwhile):
    """At 07:00 the service starts report's run for 07:00, and its program is still loading when you take report off
    the scheduled flows, change its time to 08:00 and save, or turn the service off (choosing Let it finish for
    report: that is for its runs going and waiting their turn, not one still loading). Once loaded, it does not
    begin, and nothing is recorded for it."""
    paths = schedule(env.home, env.root, {"report": flow_yaml({"cron": "0 7 * * *"})})
    service = loading_runs.service(env.home)
    service.tick(clock("06:59:00"))
    loading_runs.loading.add("report")
    service.tick(clock("07:00:00"))
    (run,) = loading_runs.of("report")
    if meanwhile == "taken off the list":
        Home(env.home).set_scheduled(paths["report"], False)
    elif meanwhile == "its schedule changed":
        write(paths["report"], flow_yaml({"cron": "0 8 * * *"}))
    else:
        request_turn_off(Home(env.home), {str(paths["report"]): "finish"})

    assert not has_loaded(loading_runs, run), f"it began though {meanwhile}"
    assert history(env.home) == []


@pytest.mark.invariant("SCH-11")
def test_a_time_skipped_for_overlap_says_why_in_plain_words(env, smtp, fake_runs):
    """Overlap skip: a time that comes while a run of the flow goes, or waits its turn, is skipped, and you are told
    (Run skipped) why, in plain words, plus an email if the flow's notify asks for skipped runs. going's 10:00 run
    still goes at 10:01; waiting's 10:00 run waits for a free slot (Scheduled runs at once: 1, taken by going) at
    10:01; yours has a run you started yourself going."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\nsmtp_connection: mailer\n")
    paths = schedule(env.home, env.root, {
        "going": flow_yaml({"cron": "* * * * *"}, overlap="skip",
                           notify={"when": ["skipped"], "to": ["team@example.com"]}),
        "waiting": flow_yaml({"cron": "* * * * *"}, overlap="skip"),
        "yours": flow_yaml({"cron": "0 7 * * *"}, {"cron": "1 10 * * *"}, overlap="skip")})
    told = []
    service = fake_runs.service(env.home, lambda title, message: told.append((title, message)))
    fake_runs.holding.update({"going", "waiting"})
    service.tick(clock("09:59:30"))
    service.tick(clock("10:00:00"))
    history_db = History(env.home / "history.db")  # (your run of yours, started from a terminal: this process)
    try:
        history_db.start_run("yours", str(real_path(paths["yours"])), {}, dt.datetime.now().isoformat())
        service.tick(clock("10:01:00"))
    finally:
        history_db.close()

    assert sorted(told) == [("Run skipped", "going: the previous run is still going"),
                            ("Run skipped", "waiting: the previous run is still waiting its turn"),
                            ("Run skipped", "yours: a run of it that you started is still going")], told
    assert wait_until(lambda: smtp.messages, 10)
    assert [(str(m["Subject"]), str(m["To"])) for m in smtp.messages] == [("[Taskloom] Run skipped",
                                                                          "team@example.com")]


@pytest.mark.invariant("SCH-13")
def test_a_scheduled_run_that_fails_before_it_begins_is_told_and_emailed_as_its_flow_asks(env, smtp, loading_runs):
    """report's run for 07:00 fails as its program loads, before it begins (it says why, e.g. a connection that is
    gone): it could not email about itself, so the service tells you (report failed, with what it said), and emails
    whom report's notify names for failures."""
    write(env.home / "settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    schedule(env.home, env.root, {"report": flow_yaml({"cron": "0 7 * * *"},
                                                     notify={"when": ["failure"], "to": ["team@example.com"]})})
    told = []
    service = loading_runs.service(env.home, lambda title, message: told.append((title, message)))
    service.tick(clock("06:59:00"))
    loading_runs.loading.add("report")
    service.tick(clock("07:00:00"))
    (run,) = loading_runs.of("report")
    run.stdout.say("no connection named 'warehouse' in connections.yaml\n")
    run.end(1)
    assert wait_until(lambda: service.tick(clock("07:00:01")) or smtp.messages, 30), told

    assert told == [("report failed", "no connection named 'warehouse' in connections.yaml")], told
    assert [(str(m["Subject"]), str(m["To"])) for m in smtp.messages] == [("[Taskloom] report failed",
                                                                          "team@example.com")]


@pytest.mark.invariant("SCH-4")
def test_stop_now_stops_a_run_the_service_before_a_restart_started_and_the_service_then_turns_off(env):
    """nightly's run was started by the service before it restarted (it crashed, and is on again): the service on
    knows it from the run history. You turn the service off with Stop now for nightly: that run stops and is recorded
    as cancelled, and the service turns off."""
    paths = schedule(env.home, env.root, {"nightly": flow_yaml(HOURLY, work=hold_until("nightly"))})
    home = Home(env.home)
    try:
        with on(env):
            before = Scheduler(home)  # (the service before: its run goes on after it is gone)
            before.tick()
            assert wait_until(lambda: going(env.home) == {"nightly"}, 30), history(env.home)

        def reaped():  # (the service before is gone: the system reaps its run's process as it ends; here, this test)
            return [run.proc.poll() for run in before.runs]

        with on(env):
            service = Scheduler(home)
            service.tick()
            request_turn_off(home, {str(paths["nightly"]): "stop"})  # Proceed
            off = wait_until(lambda: reaped() and (service.tick() or service.off), 30)
        stopped = wait_until(lambda: statuses(env.home, "nightly") == ["cancelled"], 15)
    finally:
        write(env.root / "nightly.go", "go")
        end_runs(env.home)

    assert off and stopped, f"Stop now did not stop the run from before the restart: {history(env.home)}"


# A scheduled run's process that ends at once when asked to stop, as on Windows (there SIGTERM ends a process before
# it could record anything): here it ignores the request, and records nothing itself, so what the history says of it
# is what Stop now recorded.
ENDS_WITHOUT_RECORDING = """
import signal, sys, time
from pathlib import Path
from taskloom.history import History
signal.signal(signal.SIGTERM, signal.SIG_IGN)
history = History(Path(sys.argv[1]))
history.start_run("nightly", sys.argv[2], {}, "2026-01-05T10:00:00", schedule="every 60 minutes")
history.close()
print("began", flush=True)
time.sleep(60)
"""


@pytest.mark.invariant("SCH-4")
def test_stop_now_records_a_stopped_scheduled_run_as_cancelled_also_where_it_cannot_record_it_itself(env):
    """With no service running, you choose Stop now for nightly, whose scheduled run goes: its process is told to stop
    and the run is recorded as cancelled at once, when it is stopped, also where its process ends before it could
    record that itself (as on Windows)."""
    paths = schedule(env.home, env.root, {"nightly": flow_yaml(HOURLY)})
    proc = subprocess.Popen([sys.executable, "-c", ENDS_WITHOUT_RECORDING, str(env.home / "history.db"),
                             str(real_path(paths["nightly"]))], env=env.env, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "began"
        request_turn_off(Home(env.home), {str(paths["nightly"]): "stop"})
        recorded = statuses(env.home, "nightly")
    finally:
        proc.kill()
        proc.wait(10)

    assert recorded == ["cancelled"], recorded
    (run,) = history(env.home)
    assert run["finished"] is not None and abs(run["finished"] - dt.datetime.now()) < dt.timedelta(minutes=1), run


@pytest.mark.invariant("SCH-4")
def test_stop_now_stops_the_scheduled_run_of_a_flow_taken_off_the_scheduled_flows_while_it_goes(env):
    """nightly's scheduled run goes, and meanwhile you take nightly off the scheduled flows. With no service running
    you turn the service off: the dialog still lists nightly (by its file), and Stop now stops its run and records it
    as cancelled."""
    paths = schedule(env.home, env.root, {"nightly": flow_yaml(HOURLY, work=hold_until("nightly"))})
    home = Home(env.home)
    proc = run_process(env, paths["nightly"], scheduled=True)
    try:
        assert wait_until(lambda: statuses(env.home, "nightly") == ["running"], 30), history(env.home)
        home.set_scheduled(paths["nightly"], False)
        listed = runs_going(home)
        request_turn_off(home, {path: "stop" for path, _ in listed})  # Proceed
        stopped = wait_until(lambda: proc.poll() is not None, 15)
    finally:
        write(env.root / "nightly.go", "go")
        end_runs(env.home)
        if proc.poll() is None:
            proc.kill()
        proc.wait(10)

    assert listed == [(str(real_path(paths["nightly"])), "nightly")], listed
    assert stopped and statuses(env.home, "nightly") == ["cancelled"], history(env.home)


def test_starting_at_login_anywhere_but_windows_says_it_is_for_windows_only(env, monkeypatch):
    """`taskloom scheduler --at-login on` (and Tools → Settings) away from Windows: refused, saying so in plain
    words."""
    monkeypatch.delenv("APPDATA", raising=False)
    with pytest.raises(RuntimeError) as refused:
        set_start_at_login(Home(env.home), True)

    assert str(refused.value) == "starting the scheduler at login is supported on Windows only"
