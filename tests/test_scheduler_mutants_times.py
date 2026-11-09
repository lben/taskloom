"""The scheduler service: deciding which times are due and which runs start, checked where mutation testing found the
other tests let a planted bug through (docs/promises/scheduler.mutants.yaml). Each test keeps a promise of
docs/promises/scheduler.yaml or a behaviour the README states.

The times use the service's checks (Scheduler.tick) at chosen clock times, each service made as `taskloom scheduler`
makes it (the Taskloom folder's lock first: FakeRuns.service), with each run's process replaced at the process boundary
by a stand-in that claims its time at this PC's clock as the real run does (test_scheduler_held_times.Runs). A service
that stopped without warning (a crash, a PC restart) is one that checked no more, and whose runs' programs were still
loading then: none of them began (a_service_before). A run you started yourself is the record `taskloom run` makes in
the run history as it begins and ends, with this process standing in for its program."""

import datetime as dt
import gc
import subprocess
from pathlib import Path

import pytest

from taskloom.flow import load_flow, real_path
from taskloom.history import History
from tests.test_scheduler_held_times import Runs, about, check, clock, one_off
from tests.test_scheduler_new_promises import (EVERY_TEN, FIRST_SIGHT, HOURLY, FakeRun, FakeRuns, begins, check_after,
                                               clock_shows, drain, flow_yaml, missed_times, one_at_a_time, says,
                                               schedule, utc, write)
from tests.test_scheduler_start_at import start_at_clock

EVERY_MINUTE = {"cron": "* * * * *", "misfire": "catch_up"}


@pytest.fixture
def fake_runs(monkeypatch) -> Runs:
    runs = Runs()
    monkeypatch.setattr(subprocess, "Popen", runs.popen)
    yield runs
    runs.end()


def told(said: list):
    """What you are told (a desktop message, or the server's log), kept in `said`."""
    return lambda title, message: said.append(f"{title}: {message}")


def began(runs: list[FakeRun]) -> list[str]:
    """Of these stand-ins, those whose run began, in the order they started: their Schedule block and time (on this
    PC's clock)."""
    return [f"{r.block} {start_at_clock(r.start_at):%H:%M:%S}" for r in runs if r.reached and r.not_begun is None]


class StillLoading(FakeRuns):
    """Stand-ins whose programs are still loading as long as the service that started them lives: none claims."""

    def popen(self, args, *rest, **kwargs):
        run = super().popen(args, *rest, **kwargs)
        if isinstance(run, FakeRun):
            run.waits = True
            self.holding.add(run.flow)
        return run


def a_service_before(home: Path, *moments: dt.datetime):
    """A scheduler service before the next one, made as `taskloom scheduler` makes one, that checked at these moments
    (this PC's clock showing each) and then stopped without warning (a crash, a PC restart) while the programs of the
    runs it started were still loading: none of them began, and they are gone with it."""
    runs = StillLoading()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subprocess, "Popen", runs.popen)
        service = runs.service(home)
        for moment in moments:
            with clock_shows(moment):
                service.tick(moment)
    runs.end()
    del service
    gc.collect()  # (its hold on the history file goes with it)


def your_run_began(home: Path, path: Path) -> int:
    """You start a run of the flow yourself (the editor's Run, or `taskloom run`): it records itself in the run history
    as it begins, as that run does (this process stands in for its program). Its run id."""
    history = History(home / "history.db")
    try:
        return history.start_run(load_flow(path).name, str(real_path(path)), {}, dt.datetime.now().isoformat())
    finally:
        history.close()


def your_run_finished(home: Path, run_id: int):
    """Your run ends, recording its end in the run history as `taskloom run` does."""
    history = History(home / "history.db")
    try:
        history({"event": "run_finished", "run_id": run_id, "ts": dt.datetime.now().isoformat(), "status": "success"})
    finally:
        history.close()


# --- SCH-9: missed times, as Misfire says -----------------------------------------------------------------------------

@pytest.mark.invariant("SCH-9")
def test_a_catch_up_of_two_missed_times_runs_both_one_at_a_time(env, fake_runs):
    """tenly runs every 10 minutes (Catch up; Overlap skip, the default). The service first sees it at 00:00:30 and
    checks next at 00:25:00 (the PC slept): 00:10 and 00:20 were missed. Catch up runs every one of them, oldest first,
    one at a time; neither is skipped as the previous run still going or waiting its turn."""
    schedule(env.home, env.root, {"tenly": flow_yaml(EVERY_TEN)})
    said = []
    service = fake_runs.service(env.home, told(said))
    check(service, fake_runs, clock("00:00:30"))
    check(service, fake_runs, clock("00:25:00"))

    tenly = fake_runs.of("tenly")
    assert began(tenly) == ["s1 00:10:00", "s1 00:20:00"], f"not both missed times, oldest first; told: {said}"
    assert one_at_a_time(tenly) == []
    assert [m for m in about(said, "tenly") if "skip" in m.lower()] == []


@pytest.mark.invariant("SCH-9")
def test_101_catch_up_times_the_latest_exactly_2_minutes_late_ask_first(env, fake_runs):
    """backlog runs every 10 minutes (Catch up). 101 of its times came while the PC slept, the latest exactly 2 minutes
    before the service checks: only a time less than 2 minutes late is on time (README: missed times), so all 101 were
    missed, more than 100. You are asked first, about 101, and none of them runs before you answer."""
    schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN)})
    asked = []
    service = fake_runs.service(env.home, ask=lambda flow, count, answer: asked.append((flow, count)))
    check(service, fake_runs, FIRST_SIGHT)
    check(service, fake_runs, missed_times(101)[-1] + dt.timedelta(minutes=2))

    assert asked == [("backlog", 101)]
    assert fake_runs.of("backlog") == [], "missed runs started before the answer"


@pytest.mark.invariant("SCH-9")
def test_skip_tells_how_many_were_missed_and_emails_as_the_flow_asks(env, smtp, fake_runs):
    """The settings name a mail server only; tenly (every 10 minutes, Misfire Skip) asks for an email to
    team@example.com about skipped runs. The service first sees it at 00:00:30 and next checks at 00:35:00: 00:10,
    00:20 and 00:30 were missed. Skip runs none of them; a Missed runs skipped notification says 3 were missed, and so
    does an email to team@example.com (README: a flow's notify overrides the settings)."""
    write(env.home / "settings.yaml", "smtp_connection: mailer\n")
    schedule(env.home, env.root, {"tenly": flow_yaml({"cron": "*/10 * * * *", "misfire": "skip"},
                                                     notify={"when": ["skipped"], "to": ["team@example.com"]})})
    said = []
    service = fake_runs.service(env.home, told(said))
    check(service, fake_runs, clock("00:00:30"))
    check(service, fake_runs, clock("00:35:00"))
    service.wait_for_runs(10)  # (the email is sent)

    assert fake_runs.of("tenly") == []
    assert [m for m in about(said, "tenly") if "Missed runs skipped" in m and says(m, 3)], said
    mails = [(str(m["To"]), f"{m['Subject']} {m.get_body(('plain', 'html')).get_content()}") for m in smtp.messages]
    assert [to for to, text in mails if "Missed runs skipped" in text and says(text, 3)] == ["team@example.com"], mails


@pytest.mark.invariant("SCH-9")
def test_answering_about_a_schedules_missed_runs_keeps_the_flows_other_waiting_runs(env, fake_runs):
    """backlog has two Schedule blocks: s1 every 10 minutes and s2 at 09:05 and 10:05, both Catch up. The service first
    sees them at 00:00:30, and next checks at 16:55: s1 missed 101 times, so you are asked; s2's 09:05 runs (and goes
    on) and its 10:05 waits its turn behind it. You answer Run only the latest 1. Every run of s2 still runs (Catch up
    runs every one of them), and so does s1's latest, one at a time."""
    schedule(env.home, env.root, {"backlog": flow_yaml(EVERY_TEN, {"cron": "5 9,10 * * *", "misfire": "catch_up"})})
    asked, said = [], []
    service = fake_runs.service(env.home, told(said), ask=lambda flow, count, answer: asked.append((count, answer)))
    fake_runs.holding.add("backlog")  # (its runs go on until let go)
    check(service, fake_runs, FIRST_SIGHT)
    now = check_after(101)
    check(service, fake_runs, now)
    assert [count for count, _ in asked] == [101] and began(fake_runs.of("backlog")) == ["s2 09:05:00"], \
        f"not arranged: {said}"
    asked[0][1](1)  # Run only the latest 1
    check(service, fake_runs, now + dt.timedelta(seconds=1))  # (the answer applies)
    fake_runs.holding.discard("backlog")
    check(service, fake_runs, now + dt.timedelta(seconds=2))

    backlog = fake_runs.of("backlog")
    assert sorted(began(backlog)) == ["s1 16:50:00", "s2 09:05:00", "s2 10:05:00"], f"told: {said}"
    assert one_at_a_time(backlog) == []


@pytest.mark.invariant("SCH-9")
@pytest.mark.invariant("SCH-11")
def test_with_overlap_allow_a_time_missed_while_its_catch_up_goes_waits_for_it(env, fake_runs):
    """tenly runs every 10 minutes (Catch up, Overlap allow), each run going on a long time. The service first sees it
    at 00:00:30 and next checks at 00:35:00: 00:10, 00:20 and 00:30 were missed, and catch up one at a time. Then the
    PC sleeps until 00:45:00: 00:40 was missed too, while the catch-up goes. It waits for it: the missed runs of a
    Catch up schedule always go one at a time, even with Overlap allow (README: Overlaps)."""
    schedule(env.home, env.root, {"tenly": flow_yaml(EVERY_TEN, overlap="allow")})
    said = []
    service = fake_runs.service(env.home, told(said))
    fake_runs.holding.add("tenly")
    for moment in ("00:00:30", "00:35:00", "00:45:00"):
        check(service, fake_runs, clock(moment))
    fake_runs.holding.discard("tenly")  # (each run ends)
    check(service, fake_runs, clock("00:45:30"))

    tenly = fake_runs.of("tenly")
    assert began(tenly) == ["s1 00:10:00", "s1 00:20:00", "s1 00:30:00", "s1 00:40:00"], f"told: {said}"
    assert one_at_a_time(tenly) == [], "a missed run of a Catch up schedule started alongside its catch-up"


@pytest.mark.invariant("SCH-11")
def test_with_overlap_allow_a_catch_up_time_goes_alongside_a_run_of_another_schedule_of_its_flow(env, fake_runs):
    """twice (Overlap allow) has two Schedule blocks: s1 every 60 minutes (its first run is as the service first sees
    it, 00:00:30, and goes on) and s2 every 10 minutes, Catch up. s2's 00:10 comes while s1's run goes: with Overlap
    allow a second run goes at once (SCH-11); only a schedule's own catch-up goes one at a time."""
    schedule(env.home, env.root, {"twice": flow_yaml(HOURLY, EVERY_TEN, overlap="allow")})
    said = []
    service = fake_runs.service(env.home, told(said))
    fake_runs.holding.add("twice")
    check(service, fake_runs, clock("00:00:30"))
    check(service, fake_runs, clock("00:10:00"))

    twice = fake_runs.of("twice")
    assert began(twice) == ["s1 00:00:30", "s2 00:10:00"], f"told: {said}"
    assert twice[1].alongside == ["twice"], "s2's run did not start alongside s1's"


# --- SCH-16, SCH-12, SCH-11: a time whose run had not begun keeps its place -------------------------------------------

@pytest.mark.invariant("SCH-16")
@pytest.mark.invariant("SCH-9")
@pytest.mark.parametrize("lives, misfire, ran", [
    (["07:00:30"], "run_once", ["s1 07:00:00", "s2 06:58:00", "s2 06:59:00", "s2 07:00:00"]),
    (["07:02:30"], "run_once",
     ["s1 07:00:00", "s2 06:58:00", "s2 06:59:00", "s2 07:00:00", "s2 07:01:00", "s2 07:02:00"]),
    (["07:00:30", "07:00:50"], "run_once", ["s1 07:00:00", "s2 06:58:00", "s2 06:59:00", "s2 07:00:00"]),
    (["07:00:30", "07:02:30"], "skip", ["s2 06:58:00", "s2 06:59:00", "s2 07:00:00", "s2 07:01:00", "s2 07:02:00"])],
    ids=["back within 2 minutes", "back after 2 minutes", "stopped twice", "stopped twice, then back late with Skip"])
def test_a_time_whose_run_had_not_begun_as_the_service_stopped_keeps_its_place_when_it_is_back(env, fake_runs, lives,
                                                                                              misfire, ran):
    """nightly (Overlap skip) has two Schedule blocks: s1 at 07:00:00 (Run once, or Skip) and s2 every minute (Catch
    up). A service sees them at 06:57:30 and next checks at 07:00:00: it starts s1's run for 07:00, and s2's 06:58 to
    07:00 wait their turn behind it (a catch-up goes one at a time). It stops without warning before s1's run has
    begun; the next service is back at `lives` (each stopping so before the last). The service on sees to s1's 07:00
    itself, as Misfire says (README: if the service restarts before a run has begun), in its place: before s2's runs,
    which came after it, so it is neither skipped as the previous run still waiting its turn nor run after them (each
    starts once, in the order they came: SCH-16). With Skip, back more than 2 minutes after 07:00, it was missed: it
    does not run, and you are told."""
    schedule(env.home, env.root, {"nightly": flow_yaml(one_off(clock("07:00:00"), misfire), EVERY_MINUTE)})
    a_service_before(env.home, clock("06:57:30"), clock("07:00:00"))
    for moment in lives[:-1]:
        a_service_before(env.home, clock(moment))
    said = []
    service = fake_runs.service(env.home, told(said))
    check(service, fake_runs, clock(lives[-1]))

    nightly = fake_runs.of("nightly")
    assert began(nightly) == ran, f"told: {said}"
    assert one_at_a_time(nightly) == []
    skipped = [m for m in about(said, "nightly") if "skip" in m.lower()]
    if misfire == "skip":
        assert len(skipped) == 1 and "Missed runs skipped" in skipped[0] and says(skipped[0], 1), said
    else:
        assert skipped == []


@pytest.mark.invariant("SCH-11")
@pytest.mark.invariant("SCH-16")
def test_a_time_due_again_while_your_run_of_its_flow_goes_waits_in_its_place_with_overlap_queue(env, fake_runs):
    """nightly (Overlap queue) has two Schedule blocks, each at 07:00:00. A service starts s1's run then, and s2's
    waits its turn behind it; the service stops without warning before s1's run has begun. You start a run of nightly
    yourself. The service is back at 07:00:30: s1's 07:00 waits until your run has finished (README: with queue it
    waits until your run has finished, then runs once), in its place before s2's, which came after it. Your run ends:
    s1's runs, then s2's."""
    paths = schedule(env.home, env.root, {"nightly": flow_yaml(one_off(clock("07:00:00")), one_off(clock("07:00:00")),
                                                               overlap="queue")})
    a_service_before(env.home, clock("06:59:30"), clock("07:00:00"))
    yours = your_run_began(env.home, paths["nightly"])
    said = []
    service = fake_runs.service(env.home, told(said))
    check(service, fake_runs, clock("07:00:30"))
    assert fake_runs.of("nightly") == [], f"a scheduled run of nightly started while yours went: {said}"
    your_run_finished(env.home, yours)
    check(service, fake_runs, clock("07:00:40"))

    nightly = fake_runs.of("nightly")
    assert began(nightly) == ["s1 07:00:00", "s2 07:00:00"], f"told: {said}"
    assert one_at_a_time(nightly) == []


@pytest.mark.invariant("SCH-16")
@pytest.mark.invariant("SCH-11")
def test_a_time_exactly_2_minutes_late_waits_its_turn_and_still_runs_once_the_service_is_back(env, fake_runs):
    """nightly (Run once, Overlap skip) runs at 07:01 and 07:04. A service sees it at 06:59:30 and, the PC having
    slept, next checks at 07:03:00, exactly 2 minutes after 07:01: not less than 2 minutes late, 07:01 was missed, so
    its run waits its turn (it starts at once). The service stops without warning before that run has begun, and the
    next one is back at 07:04:10. The run waiting its turn starts, once (README: Waiting runs are saved), and 07:04,
    which came while it waited, follows Overlap skip: it is skipped, and you are told the previous run is still
    waiting its turn."""
    schedule(env.home, env.root, {"nightly": flow_yaml({"cron": "1,4 7 * * *"})})
    a_service_before(env.home, clock("06:59:30"), clock("07:03:00"))
    said = []
    service = fake_runs.service(env.home, told(said))
    check(service, fake_runs, clock("07:04:10"))

    assert began(fake_runs.of("nightly")) == ["s1 07:01:00"], f"told: {said}"
    assert [m for m in about(said, "nightly") if "skipped" in m and "waiting its turn" in m], said


@pytest.mark.invariant("SCH-12")
def test_a_time_due_again_starts_before_a_new_time_when_one_run_at_a_time_is_allowed(env, fake_runs):
    """Scheduled runs at once 1. later (listed first) runs at 07:00:30, earlier at 07:00:00. A service started
    earlier's run for 07:00 and stopped without warning before it had begun; the next one is back at 07:00:30. It sees
    to earlier's 07:00 itself, and it came first: earlier's run starts, and later's waits until it has ended (SCH-12:
    the rest wait and start in the order they came)."""
    schedule(env.home, env.root, {"later": flow_yaml(one_off(clock("07:00:30"))),
                                  "earlier": flow_yaml(one_off(clock("07:00:00")))})
    a_service_before(env.home, clock("06:59:00"), clock("07:00:00"))
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    said = []
    service = fake_runs.service(env.home, told(said))
    check(service, fake_runs, clock("07:00:30"))

    assert [(r.flow, r.reached and r.not_begun is None) for r in fake_runs.started] == [
        ("earlier", True), ("later", True)], f"told: {said}"
    assert one_at_a_time(fake_runs.started) == [] and fake_runs.started[1].alongside == []


@pytest.mark.invariant("SCH-12")
@pytest.mark.invariant("SCH-16")
def test_a_run_waiting_its_turn_behind_a_time_due_again_still_starts_before_runs_that_came_after_it(env, fake_runs):
    """Scheduled runs at once 1. nightly (Overlap queue) has two Schedule blocks at 07:00:00; report (listed first)
    runs at 07:00:10. A service started nightly's s1 run at 07:00 (s2's waits behind it) and report's at 07:00:10, and
    stopped without warning before either had begun. Back at 07:00:30, the service sees to both times itself, and the
    runs start in the order they came: nightly's s1, nightly's s2, then report's, each once the one before has ended.
    nightly's s2, which waits only for nightly's time due again, comes before report's (SCH-12)."""
    schedule(env.home, env.root, {"report": flow_yaml(one_off(clock("07:00:10"))),
                                  "nightly": flow_yaml(one_off(clock("07:00:00")), one_off(clock("07:00:00")),
                                                       overlap="queue")})
    a_service_before(env.home, clock("06:59:30"), clock("07:00:00"), clock("07:00:10"))
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    said = []
    service = fake_runs.service(env.home, told(said))
    check(service, fake_runs, clock("07:00:30"))

    assert [(r.flow, r.block) for r in fake_runs.started] == [("nightly", "s1"), ("nightly", "s2"), ("report", "s1")], \
        f"told: {said}"
    assert all(r.reached and r.not_begun is None for r in fake_runs.started), f"told: {said}"
    assert one_at_a_time(fake_runs.started) == [] and [r.alongside for r in fake_runs.started] == [[], [], []]


@pytest.mark.invariant("SCH-12")
@pytest.mark.invariant("SCH-16")
def test_a_flow_with_a_mistake_holds_up_only_its_own_runs_that_came_after_its_time_due_again(env, fake_runs):
    """Scheduled runs at once 1. broken (Overlap queue) has two Schedule blocks at 07:00:00, and other one at 07:00:00
    too. A service starts broken's s1 run then; broken's s2 waits behind it, and other's for a free slot; the service
    stops without warning before s1's run has begun. Meanwhile broken's file gets a mistake. Back at 07:00:30: other's
    run starts at once, as the slot is free (broken's mistake holds up no other flow), and broken's s2 does not start
    before broken's s1, its time due again that came before it (each starts once, in the order they came). Once broken
    is put right, its s1 runs, then its s2."""
    flow = flow_yaml(one_off(clock("07:00:00")), one_off(clock("07:00:00")), overlap="queue")
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    paths = schedule(env.home, env.root, {"broken": flow, "other": flow_yaml(one_off(clock("07:00:00")))})
    a_service_before(env.home, clock("06:59:30"), clock("07:00:00"))
    write(paths["broken"], "blocks: [unclosed\n")  # (a mistake: it cannot be read)
    said = []
    service = fake_runs.service(env.home, told(said))
    check(service, fake_runs, clock("07:00:30"))
    started_then = [(r.flow, r.block) for r in fake_runs.started]
    write(paths["broken"], flow)  # put right
    check(service, fake_runs, clock("07:01:00"))

    assert started_then == [("other", "s1")], f"told: {said}"
    assert [(r.flow, r.block) for r in fake_runs.started] == [("other", "s1"), ("broken", "s1"), ("broken", "s2")], \
        f"told: {said}"
    assert all(r.reached and r.not_begun is None for r in fake_runs.started), f"told: {said}"


# --- claim_time: a run for its time, its program slow to load ---------------------------------------------------------

def test_a_run_whose_program_takes_exactly_2_minutes_to_load_still_begins_for_its_time(env, fake_runs):
    """nightly's one-off time 07:00:00 comes and the service starts its run; its program takes exactly 2 minutes to
    load (the PC slept meanwhile). It begins, for 07:00: only a run whose program loaded more than 2 minutes after it
    started does not begin, its time then a missed one (README: Scheduled runs)."""
    schedule(env.home, env.root, {"nightly": flow_yaml(one_off(clock("07:00:00")))})
    said = []
    service = fake_runs.service(env.home, told(said))
    check(service, fake_runs, clock("06:59:30"))
    fake_runs.loading.add("nightly")
    check(service, fake_runs, clock("07:00:00"))  # its program starts loading
    (program,) = fake_runs.of("nightly")
    assert not program.reached, f"not arranged: {said}"
    fake_runs.clock = clock("07:02:00")
    assert program.reach_its_time(), f"it did not begin: {program.not_begun}"
    fake_runs.holding.discard("nightly")
    check(service, fake_runs, clock("07:02:01"))

    assert began(fake_runs.of("nightly")) == ["s1 07:00:00"], f"told: {said}"


@pytest.mark.invariant("SCH-11")
@pytest.mark.parametrize("late", [0, 1], ids=["checked at its second", "checked a second after it"])
def test_a_catch_up_schedule_with_allow_starts_a_time_that_comes_alongside_its_run_going(env, fake_runs, late):
    """tenly (every 10 minutes, Catch up, Overlap allow): each run goes on longer than 10 minutes. Its 00:20 time
    comes while its 00:10 run goes, and the service checks at 00:20 or a second after it: that time is not missed, so
    it starts at once beside the run going, as Overlap allow says (README: allow runs both at the same time; only the
    missed times of a Catch up schedule go one at a time)."""
    schedule(env.home, env.root, {"tenly": flow_yaml({"cron": "*/10 * * * *", "misfire": "catch_up"},
                                                     overlap="allow")})
    service = fake_runs.service(env.home)
    fake_runs.holding.add("tenly")  # (each run goes on longer than 10 minutes)
    drain(service, dt.datetime(2026, 1, 5, 0, 0, 30), fake_runs)
    drain(service, dt.datetime(2026, 1, 5, 0, 10, 0), fake_runs)
    drain(service, dt.datetime(2026, 1, 5, 0, 20, late), fake_runs)
    assert begins(fake_runs.of("tenly")) == utc([dt.datetime(2026, 1, 5, 0, 10), dt.datetime(2026, 1, 5, 0, 20)]), \
        f"tenly's 00:20 time, checked {late} s after it while its 00:10 run went, did not start beside it"

