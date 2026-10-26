"""The keeper replaces a hung scheduler on a server, safely: it stops the process that holds that Taskloom folder's
scheduler.lock there (found from the lock file itself: /proc on Linux, lsof where it exists), and only that process,
then starts a new scheduler. Through the keeper's check (what `taskloom scheduler` runs every 5 minutes) over the
real test SSH server, with the real Taskloom as the server's Taskloom command; the processes "on the server" are
real processes that hold the lock as a scheduler does."""

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import REPO
from taskloom import __version__
from taskloom.config import Home
from taskloom.editor.safety import pid_alive
from taskloom.scheduler import acquire_lock, running_scheduler_version
from tests.test_paths_scheduler import POSIX_SERVER, check_servers, heartbeat, keeper, service, wait_for

TASKLOOM_THERE = f"{shlex.quote(sys.executable)} -m taskloom.cli"  # the real Taskloom, as the server's command
RESTARTED = "Server scheduler restarted"

STAND_IN = """
import os, signal, sys, time
from pathlib import Path

when_asked, folder, said = sys.argv[1:4]
lock = None
if folder != "-":
    from taskloom.config import Home
    from taskloom.scheduler import acquire_lock

    lock = acquire_lock(Home(Path(folder)))  # held, as a scheduler service holds it while it runs
    Path(folder, "scheduler.pid").write_text(str(os.getpid()), encoding="utf-8")


def asked_to_stop(*_):
    with open(said, "a", encoding="utf-8") as f:
        f.write("asked to stop\\n")
    if when_asked == "ends":
        os._exit(0)
    if when_asked == "lets go of the lock":
        lock.close()  # and goes on: it no longer holds the lock


signal.signal(signal.SIGTERM, asked_to_stop)
with open(said, "a", encoding="utf-8") as f:
    f.write("running\\n")
while True:
    time.sleep(1)
"""


def on_the_server(env, name: str, when_asked: str, folder: Path | None, *command_line: str):
    """A process on the server that writes down when it is asked to stop (SIGTERM), then `when_asked`: "ends",
    "goes on" or "lets go of the lock". With `folder`, it holds that Taskloom folder's scheduler.lock, as a scheduler
    does, and wrote its scheduler.pid. `command_line`: what its command line shows after the program.
    Returns (the process, what it wrote)."""
    said = env.root / f"{name}.said"
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-c", STAND_IN, when_asked, str(folder or "-"), str(said), *command_line],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=proc.wait, daemon=True).start()  # reaped once it ends, as on a server
    if not wait_for(lambda: "running" in what_it_wrote(said) or proc.returncode is not None):
        proc.kill()
    assert "running" in what_it_wrote(said), f"{name} did not start"
    return proc, said


def what_it_wrote(said: Path) -> str:
    try:
        return said.read_text(encoding="utf-8")
    except OSError:
        return ""


def hung(remote: Path):
    """Its scheduler has not checked anything for 10 minutes (the keeper counts more than 2 as hung)."""
    (remote / "scheduler.heartbeat").write_text(str(int(time.time()) - 600), encoding="utf-8")


def told(notes) -> list[str]:
    """What the keeper told about the server: the titles."""
    return [title for title, message in notes if "appserver" in message]


def log(remote: Path) -> str:
    try:
        return (remote / "scheduler.log").read_text(encoding="utf-8")
    except OSError:
        return ""


def the_new_scheduler(remote: Path, old: list[int], checked: float) -> int:
    """The scheduler that runs for the Taskloom folder `remote` now: none of the `old` processes; it holds the
    folder's lock, wrote scheduler.pid and its heartbeat after `checked`."""
    pid = int((remote / "scheduler.pid").read_text(encoding="utf-8"))
    assert pid not in old and pid_alive(pid), (pid, old)
    assert running_scheduler_version(Home(remote)) == __version__  # (scheduler.pid names that scheduler itself)
    with pytest.raises(RuntimeError, match="already running"):
        acquire_lock(Home(remote)).close()
    assert wait_for(lambda: int(heartbeat(remote) or 0) >= checked - 1, timeout=5), heartbeat(remote)
    return pid


def end(remote: Path, *procs: subprocess.Popen, also: int | None = None):
    """End what this test started on the server: its processes, the process `also`, and the scheduler the keeper
    started there."""
    for proc in procs:
        if proc.returncode is None:
            proc.kill()
    if also and also != os.getpid() and pid_alive(also):
        os.kill(also, signal.SIGKILL)  # (stopped: it would not end when asked)
    try:
        pid = int((remote / "scheduler.pid").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pid = None
    if pid and pid != os.getpid() and pid not in [p.pid for p in procs] and pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
        if not wait_for(lambda: not pid_alive(pid), timeout=15):
            os.kill(pid, signal.SIGKILL)
    for proc in procs:
        wait_for(lambda: proc.returncode is not None, timeout=15)


@POSIX_SERVER
def test_the_keeper_replaces_the_scheduler_it_keeps_on_a_server_once_it_hangs(env, server):
    # Required behaviour 1, the reported case: the server's scheduler (one the keeper started) hangs: it runs, checks
    # nothing, and does not end when asked; it still holds the folder's lock, so no new scheduler could start there
    # and the keeper said it could not be started, every 5 minutes. The keeper stops it and starts a new one.
    remote = server.home_path / ".taskloom"
    keeper(env, command=TASKLOOM_THERE)
    scheduler, notes = service(env, {})
    stuck = None
    try:
        check_servers(scheduler)  # none runs there yet: the keeper starts one
        assert told(notes) == [RESTARTED], (notes, log(remote))
        assert running_scheduler_version(Home(remote)) == __version__  # (scheduler.pid names that scheduler itself)
        stuck = int((remote / "scheduler.pid").read_text(encoding="utf-8"))
        os.kill(stuck, signal.SIGSTOP)  # it hangs: it checks nothing more, and a request to stop does nothing
        hung(remote)
        notes.clear()

        checked = time.time()
        check_servers(scheduler)

        assert told(notes) == [RESTARTED], (notes, log(remote))
        the_new_scheduler(remote, [stuck], checked)
        assert wait_for(lambda: not pid_alive(stuck), timeout=10), "the hung scheduler still runs"
    finally:
        end(remote, also=stuck)


@POSIX_SERVER
@pytest.mark.parametrize("when_asked", ["ends", "goes on", "lets go of the lock"])
def test_the_keeper_asks_the_hung_scheduler_to_stop_and_forces_it_only_while_it_still_holds_the_lock(
        env, server, when_asked):
    # Required behaviour 1: "It first asks it to stop (SIGTERM), waits a few seconds, and forcibly stops it only if
    # that same process is still holding the lock"; then it starts a new scheduler and says so once that one runs.
    remote = server.home_path / ".taskloom"
    stuck, said = on_the_server(env, "hung", when_asked, remote, "scheduler", "--headless")
    hung(remote)
    keeper(env, command=TASKLOOM_THERE)
    scheduler, notes = service(env, {})
    try:
        checked = time.time()
        check_servers(scheduler)

        assert told(notes) == [RESTARTED], (notes, log(remote))
        the_new_scheduler(remote, [stuck.pid], checked)
        assert "asked to stop" in what_it_wrote(said)  # asked first
        if when_asked == "lets go of the lock":
            assert stuck.returncode is None, "it was forced to stop, though it no longer held the lock"
        else:
            assert wait_for(lambda: stuck.returncode is not None, timeout=10), "it still runs"
            # It ended when asked (exit code 0), or, as it did not, it was forced (SIGKILL).
            assert stuck.returncode == (0 if when_asked == "ends" else -signal.SIGKILL), stuck.returncode
    finally:
        end(remote, stuck)


@POSIX_SERVER
@pytest.mark.parametrize("bystander", ["a program with 'scheduler' in its command line",
                                       "the scheduler of another Taskloom folder"])
def test_the_keeper_signals_no_other_process_when_it_replaces_a_hung_scheduler(env, server, bystander):
    # Required behaviour 2: no other process is ever signalled: not one whose command line merely contains
    # 'scheduler', not a scheduler of another Taskloom folder on the same server (whose lock file has the same name).
    remote = server.home_path / ".taskloom"
    stuck, _ = on_the_server(env, "hung", "ends", remote, "scheduler", "--headless")
    hung(remote)
    if bystander == "the scheduler of another Taskloom folder":
        other, other_said = on_the_server(env, "other", "ends", server.home_path / "second" / ".taskloom",
                                          "scheduler", "--headless")
    else:
        other, other_said = on_the_server(env, "other", "ends", None, "airflow", "scheduler")
    keeper(env, command=TASKLOOM_THERE)
    scheduler, notes = service(env, {})
    try:
        checked = time.time()
        check_servers(scheduler)

        assert told(notes) == [RESTARTED], (notes, log(remote))
        the_new_scheduler(remote, [stuck.pid, other.pid], checked)
        assert other.returncode is None and "asked to stop" not in what_it_wrote(other_said), \
            f"{bystander} was signalled"
    finally:
        end(remote, stuck, other)


@POSIX_SERVER
def test_after_a_reboot_the_process_that_got_the_pid_in_scheduler_pid_is_left_alone(env, server):
    # Required behaviour 2: "not one that got the pid in scheduler.pid after a reboot": after a reboot no process
    # holds the lock; scheduler.pid names a process that now is another program (with 'scheduler' in its command
    # line), and the heartbeat is from before the reboot. The keeper starts a new scheduler and signals nothing.
    remote = server.home_path / ".taskloom"
    remote.mkdir()
    other, other_said = on_the_server(env, "other", "ends", None, "airflow", "scheduler")
    (remote / "scheduler.pid").write_text(str(other.pid), encoding="utf-8")
    hung(remote)
    keeper(env, command=TASKLOOM_THERE)
    scheduler, notes = service(env, {})
    try:
        checked = time.time()
        check_servers(scheduler)

        assert told(notes) == [RESTARTED], (notes, log(remote))
        the_new_scheduler(remote, [other.pid], checked)
        assert other.returncode is None and "asked to stop" not in what_it_wrote(other_said), \
            "the process that got the pid in scheduler.pid was signalled"
    finally:
        end(remote, other)


def path_without(program: str, shadows: Path) -> str:
    """PATH, but without `program`: each folder of PATH that has it is replaced by a copy (of links) without it."""
    folders = []
    for n, folder in enumerate(os.environ["PATH"].split(os.pathsep)):
        if not (Path(folder) / program).exists():
            folders.append(folder)
            continue
        shadow = shadows / str(n)
        shadow.mkdir(parents=True)
        for entry in Path(folder).iterdir():
            if entry.name != program:
                (shadow / entry.name).symlink_to(entry)
        folders.append(str(shadow))
    return os.pathsep.join(folders)


@POSIX_SERVER
@pytest.mark.skipif(Path("/proc/self/fd").is_dir(),
                    reason="this system has /proc, which the keeper reads; the test server cannot hide it")
def test_where_the_keeper_cannot_tell_which_process_holds_the_lock_it_stops_nothing_and_says_why(
        env, server, monkeypatch):
    # Required behaviour 3: "Where the keeper cannot tell which process holds the lock (no /proc and no lsof), it
    # stops nothing and says the scheduler could not be restarted and why." Here: no /proc, and no lsof on the PATH
    # of the server's commands.
    monkeypatch.setenv("PATH", path_without("lsof", env.root / "no-lsof"))  # (the test server's commands get it)
    remote = server.home_path / ".taskloom"
    stuck, said = on_the_server(env, "hung", "ends", remote, "scheduler", "--headless")
    hung(remote)
    keeper(env, command=TASKLOOM_THERE)
    scheduler, notes = service(env, {})
    try:
        check_servers(scheduler)

        about = [f"{title}: {message}" for title, message in notes if "appserver" in message]
        assert about and RESTARTED not in told(notes), notes
        assert stuck.returncode is None and "asked to stop" not in what_it_wrote(said), "it was signalled"
        assert [m for m in about if "lsof" in m], about  # why: what the server lacks to tell
    finally:
        end(remote, stuck)


def test_the_readme_says_the_keeper_replaces_a_hung_scheduler():
    # Required behaviour 4: README's keeper paragraph says it replaces a hung scheduler.
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    about_keeper = [p for p in readme.split("\n\n") if "keeper" in p and not p.startswith("|")]  # (not the table)

    assert [p for p in about_keeper if "hung" in p.lower()], about_keeper
