"""What the scheduler service must always do (docs/promises/scheduler.yaml) where turning it off meets a run started
early (SCH-8, SCH-4), the editor's view of a service turning off (SCH-2, SCH-4), a Stop now that has to be forced
(SCH-4), and a service started again after a PC restart or with a run going (SCH-16, SCH-11, SCH-12, SCH-13).

Each test goes through the real service (`taskloom scheduler`, started from the editor's button or as a server's
`--headless` service), real run processes, and turning off as the dialog's Proceed sends it (request_turn_off) or
through the editor's button itself. Only what cannot happen in a test is arranged: where a schedule's next time must
come within seconds, a service before this one first saw it (and ran it) about a minute earlier (next_time_in); and a
PC restart, as the run going then ending at once (killed) while no service is on, and its process id, as its run
history has it, given to another program started since."""

import contextlib
import datetime as dt
import gc
import os
import signal
import sqlite3
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from taskloom.config import Home  # noqa: E402
from taskloom.scheduler import request_turn_off, runs_going  # noqa: E402
from tests.test_editor import REAL_START_SCHEDULER  # noqa: E402
from tests.test_paths_scheduler import start_service as start_headless  # noqa: E402
from tests.test_scheduler_new_promises import (HOURLY, TURN_OFF, FakeRuns, answer_when_shown, end_runs,  # noqa: E402
                                               ended, flow_yaml, going, history, hold_until, pump, schedule,
                                               statuses, stop_the_service, wait_until, write)
from tests.test_scheduler_start_at import (checked_after, record_process_starts, run_processes,  # noqa: E402
                                           start_service, stop_service, tray_messages)

MINUTE = dt.timedelta(minutes=1)
EVERY_MINUTE = {"every": "1 minute"}
NO_KEYRING = "keyring.backends.fail.Keyring"


# --- helpers ----------------------------------------------------------------------------------------------------

def next_time_in(home: Path, seconds: int) -> dt.datetime:
    """The scheduled flows' Every 1 minute schedules were first seen (and run) a minute minus `seconds` ago by a
    service before this one, so their next time comes `seconds` from now; that time (on this PC's clock, to the
    second). Only the schedules' state is kept: that service's runs were stand-ins (FakeRuns), and it is gone (it was
    made as `taskloom scheduler` makes one: FakeRuns.service, the folder's lock first, let go of as it stopped)."""
    seen = (dt.datetime.now() + dt.timedelta(seconds=seconds) - MINUTE).replace(microsecond=0)
    runs = FakeRuns()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subprocess, "Popen", runs.popen)
        runs.service(home).tick(seen)
    runs.end()  # (that service stops)
    gc.collect()  # (that service's hold on the history file goes with it)
    return seen + MINUTE


def deaf_to_stop(name: str) -> dict:
    """Python that starts a program, then spends a minute inside a call that holds Python's lock (as a call into a
    driver or library can): meanwhile the run cannot act on a request to stop, so a Stop now has to force it to end,
    and the program it started (which shares its output) is still there when it does. The program ends once the file
    <name>.go appears next to the flow (or after two minutes)."""
    program = (f"import time\nfrom pathlib import Path\nend = time.time() + 120\n"
               f"while not Path({name + '.go'!r}).exists() and time.time() < end:\n    time.sleep(0.05)\n")
    code = (f"import ctypes, subprocess, sys\nsubprocess.Popen([sys.executable, '-c', {program!r}])\n"
            f"if sys.platform == 'win32':\n    ctypes.PyDLL('kernel32').Sleep(60000)\n"
            f"else:\n    ctypes.PyDLL(None).sleep(60)\n")
    return {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": code}}


def crashes_when(name: str) -> dict:
    """Python that waits until the file <name>.crash appears next to the flow, then ends its run's process at once,
    as a crash does (nothing after it runs: the run cannot record or tell anything)."""
    code = (f"import os, time\nfrom pathlib import Path\nwhile not Path({name + '.crash'!r}).exists():\n"
            f"    time.sleep(0.05)\nos._exit(3)\n")
    return {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": code}}


def block_errors(home: Path, flow: str) -> list[tuple[str, str, str | None]]:
    """(block, status, error) of each block of the flow's runs."""
    with contextlib.closing(sqlite3.connect(home / "history.db", timeout=30)) as db:
        return db.execute("SELECT b.block, b.status, b.error FROM blocks b JOIN runs r ON r.id = b.run_id "
                          "WHERE r.flow = ? ORDER BY b.run_id", (flow,)).fetchall()


def checked_since_it_began(env, flow: str, seconds: float = 10) -> bool:
    """Whether the service has checked a second or more after `flow`'s latest run began, as its run history says (by
    then the service has taken in that the run began); waits for it at most `seconds`."""
    began = [r["started"] for r in history(env.home) if r["flow"] == flow]
    return bool(began) and checked_after(env, max(began) + dt.timedelta(seconds=1),
                                         until=dt.datetime.now() + dt.timedelta(seconds=seconds))


def on_screen(win, tab) -> tuple[str, str]:
    """What the editor shows of the service: its button's text, and the line under the flow's Schedule block s1."""
    return win.scheduler_action.text(), tab.scene.nodes["s1"].countdown


# --- SCH-8, SCH-4: a run started early, and turning the service off before its time ----------------------------------

@pytest.mark.invariant("SCH-8")
@pytest.mark.invariant("SCH-4")
@pytest.mark.parametrize("listed", [True, False], ids=["listed as going, Let it finish (the default)",
                                                       "not among the choices"])
def test_a_run_started_early_does_not_begin_once_you_turn_the_service_off_before_its_time(env, listed):
    """nightly is due in a few seconds; the service has started its run's process early, to be ready on time. Before
    that time you turn the service off and click Proceed: once with nightly listed as going and the dialog's default
    (Let it finish), once with nightly not among the choices. Its run does not begin, nothing is recorded for that
    time, and the service turns off."""
    starts = record_process_starts(env)
    flows = schedule(env.home, env.root, {"nightly": flow_yaml(EVERY_MINUTE)})
    due = next_time_in(env.home, 12)
    choices = {str(flows["nightly"]): "finish"} if listed else {}
    service = start_service(env, tray=False)
    try:
        found = run_processes(starts, flows, due, until=due)
        assert found, f"the service started no run process for {due:%H:%M:%S} before that time"
        request_turn_off(Home(env.home), choices)  # Proceed
        ahead = due.timestamp() - time.time()
        assert ahead > 0.5, f"turned off only {ahead:.2f} s before {due:%H:%M:%S}: too late to tell"
        deadline = due + dt.timedelta(seconds=12)
        while ((service.poll() is None or dt.datetime.now() < due + dt.timedelta(seconds=3))
               and dt.datetime.now() < deadline):
            time.sleep(0.1)
        runs, service_ended = history(env.home), service.poll() is not None
    finally:
        stop_service(env, service)
        end_runs(env.home)

    assert runs == [], (f"you turned the service off {ahead:.1f} s before {due:%H:%M:%S} (Proceed sent {choices}), "
                        f"and the run started early for that time began: "
                        f"{[(r['flow'], r['status'], r['when']) for r in runs]}")
    assert service_ended, f"the service did not turn off by {deadline:%H:%M:%S}"


@pytest.mark.invariant("SCH-4")
def test_a_run_started_early_is_not_a_run_going_so_turning_off_only_asks_to_confirm(win, tmp_path, monkeypatch):
    """nightly is due in a few seconds and the service has started its run's process early; it has not begun. It is
    not a run going: the flows with runs going do not list it, and the editor's button only asks you to confirm
    turning the service off (answered No: it stays on)."""
    home = win.home.root
    flows = schedule(home, tmp_path, {"nightly": flow_yaml(EVERY_MINUTE)})
    due = next_time_in(home, 12)
    folder = types.SimpleNamespace(root=tmp_path, home=home,
                                   env={**os.environ, "TASKLOOM_HOME": str(home), "PYTHON_KEYRING_BACKEND": NO_KEYRING})
    starts = record_process_starts(folder)
    asked = []  # the plain Yes/No question: answered No
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[1]) or QMessageBox.No))
    pump(lambda: False, 0.3)  # (the editor's own start-up checks run first)
    service = start_service(folder, tray=False)
    answer = {"timer": QTimer()}
    try:
        found = run_processes(starts, flows, due, until=due)
        assert found, f"the service started no run process for {due:%H:%M:%S} before that time"
        pump(lambda: False, 1.5)  # (a check later: the service has said which runs go)
        looked = time.time()
        listed = runs_going(Home(home))
        answer = answer_when_shown(TURN_OFF, ("nightly",), button="Cancel")
        win.toggle_scheduler()  # on: the button turns it off
        before = due.timestamp() - time.time()
        assert before > 0.2, f"looked only {before:.2f} s before {due:%H:%M:%S}: too late to tell"
    finally:
        stop_service(folder, service)
        end_runs(home)
        answer["timer"].stop()

    assert listed == [], (f"{due.timestamp() - looked:.1f} s before its time, the run started early is listed as a run "
                          f"going: {listed}")
    assert answer["seen"] is None and asked == [TURN_OFF], \
        f"turning off did not only ask to confirm: the dialog showed {answer['seen']}; asked {asked}"


# --- SCH-2, SCH-4: the editor while the service turns off, and turning it on again ----------------------------------

@pytest.mark.invariant("SCH-2")
@pytest.mark.invariant("SCH-4")
def test_the_editor_shows_the_service_off_while_a_run_you_let_finish_goes_and_on_again_it_stays_on(win, tmp_path,
                                                                                                   monkeypatch):
    """long_job's run is going when you turn the service off from the editor's button: Let it finish (the default),
    Proceed. While that run finishes, the editor shows the service off: its button says Scheduler service off, and
    long_job's Schedule block says scheduler service off instead of a countdown. You turn it on again before that
    run is done: the service stays on. The run goes on and finishes, the service does not end when it is done, and
    a flow scheduled now runs at its time."""
    home = win.home.root
    flows = schedule(home, tmp_path, {"long_job": flow_yaml(HOURLY, work=hold_until("long_job"))})
    tab = win.open_flow(flows["long_job"])
    pump(lambda: False, 0.3)  # (the editor's own start-up checks run first)
    monkeypatch.setattr(win, "start_scheduler", types.MethodType(REAL_START_SCHEDULER, win))  # the real service
    answers = []
    try:
        win.toggle_scheduler()  # off: the button turns it on
        assert pump(lambda: going(home) == {"long_job"}, 30), history(home)
        assert pump(lambda: runs_going(Home(home)), 10), "the service did not say which runs go"
        assert pump(lambda: on_screen(win, tab)[0] == "Scheduler service on", 5), on_screen(win, tab)
        service = int((home / "scheduler.pid").read_text(encoding="utf-8"))

        answers.append(proceed := answer_when_shown(TURN_OFF, ("long_job",), button="Proceed"))
        win.toggle_scheduler()  # on: the button turns it off
        pump(lambda: proceed["done"], 12)
        assert proceed["seen"] is not None and proceed["closed"] == [], (proceed["seen"], proceed["closed"])
        off = ("Scheduler service off", "scheduler service off")
        shown_off = pump(lambda: on_screen(win, tab) == off, 5)
        finishing = {"shown": on_screen(win, tab), "going": going(home), "service ended": ended(service)}
        assert finishing["going"] == {"long_job"} and not finishing["service ended"], finishing
        assert shown_off, (f"after Proceed, while the run you let finish goes, the editor shows {finishing['shown']} "
                           f"instead of {off}")

        answers.append(again := answer_when_shown(TURN_OFF, ("long_job",), button="Cancel"))
        win.toggle_scheduler()  # off: the button turns it on again, before long_job's run is done
        pump(lambda: on_screen(win, tab)[0] == "Scheduler service on", 5)
        on_again = {"turn-off dialog shown": again["seen"], "shown": on_screen(win, tab)}
        later = write(tmp_path / "next_job.yaml", flow_yaml(HOURLY)).resolve()
        Home(home).set_scheduled(later, True)  # its first time is as soon as the service sees it
        later_ran = pump(lambda: statuses(home, "next_job") == ["success"], 20)
        still_going = going(home)
        write(tmp_path / "long_job.go", "go")  # long_job's run can finish now
        finished = pump(lambda: statuses(home, "long_job") == ["success"], 20)
        pump(lambda: False, 3)  # (a service turning off would have ended by now)
        stayed_on = not ended(service)
    finally:
        write(tmp_path / "long_job.go", "go")
        stop_the_service(home)
        end_runs(home)
        for answer in answers:
            answer["timer"].stop()

    assert on_again["turn-off dialog shown"] is None and on_again["shown"][0] == "Scheduler service on", \
        f"the button did not turn the service on again: {on_again}"
    assert later_ran, f"a flow scheduled after you turned it on again did not run: {history(home)}"
    assert "long_job" in still_going and finished, f"long_job's run did not go on and finish: {history(home)}"
    assert stayed_on, "the service ended when the run you had let finish was done, although you turned it on again"


# --- SCH-4: a Stop now that has to be forced ------------------------------------------------------------------------

@pytest.mark.invariant("SCH-4")
def test_a_stop_now_that_has_to_be_forced_is_recorded_as_cancelled_also_when_the_editor_checks_right_after(
        win, tmp_path, monkeypatch):
    """stubborn's run is inside a call that does not let it act on a request to stop, and a program it started
    shares its output. You turn the service off from the editor's button with Stop now for stubborn: its run is
    forced to end a few seconds later. The editor checks for runs whose process is gone right after that. The run is
    recorded as cancelled, never as failed or crashed."""
    home = win.home.root
    schedule(home, tmp_path, {"stubborn": flow_yaml(HOURLY, work=deaf_to_stop("stubborn"))})
    pump(lambda: False, 0.3)  # (the editor's own start-up checks run first)
    monkeypatch.setattr(win, "start_scheduler", types.MethodType(REAL_START_SCHEDULER, win))  # the real service
    answer = {"timer": QTimer()}
    try:
        win.toggle_scheduler()  # off: the button turns it on
        assert pump(lambda: going(home) == {"stubborn"}, 30), history(home)
        assert pump(lambda: runs_going(Home(home)), 10), "the service did not say which runs go"
        (run,) = history(home)
        service = int((home / "scheduler.pid").read_text(encoding="utf-8"))

        answer = answer_when_shown(TURN_OFF, ("stubborn",), choose={"stubborn": "Stop now"}, button="Proceed")
        win.toggle_scheduler()  # on: the button turns it off
        pump(lambda: answer["done"], 12)
        assert answer["seen"] is not None and answer["closed"] == [], (answer["seen"], answer["closed"])
        forced = pump(lambda: ended(run["pid"]), 30)
        win._refresh_statuses()  # the editor's check (every 2 s), right after the run's process was forced to end
        service_ended = pump(lambda: ended(service), 30)
    finally:
        write(tmp_path / "stubborn.go", "go")
        stop_the_service(home)
        end_runs(home)
        answer["timer"].stop()

    assert forced, f"stubborn's run was not forced to end: {history(home)}"
    assert statuses(home, "stubborn") == ["cancelled"], \
        f"a Stop now that had to be forced is recorded as {statuses(home, 'stubborn')}: {block_errors(home, 'stubborn')}"
    assert not [e for _, _, e in block_errors(home, "stubborn") if e and "crash" in e], block_errors(home, "stubborn")
    assert service_ended, "the service did not turn off"


# --- SCH-16, SCH-11, SCH-12, SCH-4: after a PC restart, a process id now another program's -------------------------

@pytest.mark.invariant("SCH-16")
@pytest.mark.invariant("SCH-11")
@pytest.mark.invariant("SCH-12")
@pytest.mark.invariant("SCH-4")
def test_after_a_pc_restart_a_run_whose_process_id_is_now_another_programs_is_not_a_run_going(env):
    """Before the PC restarted, nightly's run was going (Overlap skip, Max concurrent runs 1): the run history says
    running, and the service said so. The PC restarted: that run is gone, and its process id now belongs to an
    unrelated program started since. The service started again does not take it for nightly's run: nightly's next
    time runs (not skipped as still going), another flow's run is not held back for a free slot, it is not listed as
    a run going, and turning the service off with Stop now for nightly never ends that program. The old run is
    recorded as crashed, and you are told (a server's service tells in its log)."""
    write(env.home / "settings.yaml", "max_concurrent_runs: 1\n")
    began = (dt.datetime.now() + dt.timedelta(seconds=10)).replace(microsecond=0)  # the time of the run that was going
    due = began + dt.timedelta(seconds=16)  # nightly's next time
    times = [{"at": f"{t:%Y-%m-%d %H:%M:%S}"} for t in (began, due)]
    nightly = schedule(env.home, env.root, {"nightly": flow_yaml(*times, work=hold_until("nightly"),
                                                                 overlap="skip")})["nightly"]
    before = start_headless(env.home)  # the service before the PC restarted: it runs nightly at `began`
    try:
        assert wait_until(lambda: statuses(env.home, "nightly") == ["running"], 30), \
            f"nightly's run did not begin and go on: {history(env.home)}"
        assert checked_since_it_began(env, "nightly"), "the service did not check after nightly's run began"
    finally:
        stop_service(env, before)
    (old,) = history(env.home)
    os.kill(old["pid"], getattr(signal, "SIGKILL", signal.SIGTERM))  # the PC restarted: the run has gone
    assert wait_until(lambda: ended(old["pid"]), 15), "the run going did not end"
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])  # started after it began
    with contextlib.closing(sqlite3.connect(env.home / "history.db", timeout=30)) as db:
        db.execute("UPDATE runs SET pid=? WHERE flow='nightly'", (unrelated.pid,))
        db.commit()
    write(env.root / "nightly.go", "go")  # (its next run does not wait)
    schedule(env.home, env.root, {"other": flow_yaml(HOURLY)})  # its first time is as soon as the service sees it
    log = env.root / "service.log"
    with open(log, "w", encoding="utf-8") as out:
        service = start_headless(env.home, stdout=out)
    try:
        wait_until(lambda: statuses(env.home, "other") == ["success"]
                   and statuses(env.home, "nightly")[1:] == ["success"],
                   max(0.0, (due - dt.datetime.now()).total_seconds()) + 15)
        ran = {"nightly": statuses(env.home, "nightly")[1:], "other": statuses(env.home, "other")}
        time.sleep(1.5)  # (a check later: the service has said which runs go)
        listed = runs_going(Home(env.home))
        request_turn_off(Home(env.home), {str(nightly): "stop"})  # Proceed, with Stop now for nightly
        wait_until(lambda: service.poll() is not None or unrelated.poll() is not None, 15)
        time.sleep(0.5)
        program_alive = unrelated.poll() is None
        recorded = statuses(env.home, "nightly")[:1]
        told = [line for line in log.read_text(encoding="utf-8").splitlines()
                if "nightly" in line and "crash" in line.lower()]
        skipped = [line for line in log.read_text(encoding="utf-8").splitlines() if "skipped" in line.lower()]
    finally:
        stop_service(env, service)
        unrelated.kill()
        unrelated.wait(timeout=15)
        end_runs(env.home)

    assert ran["nightly"] == ["success"], \
        f"nightly's next time did not run, as if the program now on that process id were its run going: {skipped}"
    assert ran["other"] == ["success"], "another flow's run waited for the slot the old run seemed to hold"
    assert listed == [], f"listed as a run going: {listed}"
    assert program_alive, "turning the service off with Stop now for nightly ended the unrelated program"
    assert recorded == ["failed"] and [e for b, _, e in block_errors(env.home, "nightly")
                                       if b == "work1" and e and "crash" in e], \
        f"the old run is not recorded as crashed: {recorded}, {block_errors(env.home, 'nightly')}"
    assert told, f"you were not told the old run crashed:\n{log.read_text(encoding='utf-8')}"


# --- SCH-13: a crash of a run going when the service restarted -------------------------------------------------------

@pytest.mark.invariant("SCH-13")
def test_a_run_going_when_the_service_restarted_that_then_crashes_is_told_on_the_desktop_and_by_email(env, smtp):
    """fragile's run is going, and its flow asks for failure emails (to the team). The service stops and starts
    again (as when the editor restarts it); the run goes on, and the new service takes it as a run going. Then the
    run crashes: it is recorded as crashed, and you are told: a desktop message "fragile crashed", and an email to
    the team."""
    write(env.home / "settings.yaml", "smtp_connection: mailer\n")
    schedule(env.home, env.root, {"fragile": flow_yaml(HOURLY, work=crashes_when("fragile"),
                                                       notify={"when": ["failure"], "to": ["team@example.com"]})})
    service = start_service(env, tray=True)
    try:
        assert wait_until(lambda: going(env.home) == {"fragile"}, 30), history(env.home)
        assert checked_since_it_began(env, "fragile"), "the service did not check after the run began"
        (run,) = history(env.home)
        stop_service(env, service)  # the service stops; the run goes on
        assert not ended(run["pid"]), "the run ended with the service"
        restarted = dt.datetime.now()
        service = start_service(env, tray=True)
        assert checked_after(env, restarted, until=restarted + dt.timedelta(seconds=30)), \
            "the service started again did not check"
        time.sleep(1.5)  # (a check later)
        write(env.root / "fragile.crash", "now")  # the run crashes
        wait_until(lambda: statuses(env.home, "fragile") != ["running"], 15)
        wait_until(lambda: [m for m in tray_messages(env) if m[0] == "fragile crashed"] and smtp.messages, 10)
        time.sleep(0.5)  # (a second email would have come by now)
    finally:
        stop_service(env, service)
        write(env.root / "fragile.crash", "now")
        end_runs(env.home)

    assert statuses(env.home, "fragile") == ["failed"] and [e for _, _, e in block_errors(env.home, "fragile")
                                                            if e and "crash" in e], \
        f"not recorded as crashed: {statuses(env.home, 'fragile')}, {block_errors(env.home, 'fragile')}"
    assert [m for m in tray_messages(env) if m[0] == "fragile crashed"], \
        f"no desktop message 'fragile crashed'; the tray icon showed {tray_messages(env)}"
    assert [(str(m["Subject"]), str(m["To"])) for m in smtp.messages] == [("[Taskloom] fragile crashed",
                                                                          "team@example.com")]
