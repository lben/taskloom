"""Proof (pull request #1, redone): the keeper replaces a hung server scheduler, stopping only the process that holds
that Taskloom folder's lock. How proofs are written: README.md here."""

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QToolBar

from taskloom.config import Home
from taskloom.editor.safety import pid_alive
from taskloom.scheduler import acquire_lock
from tests.test_paths_scheduler import heartbeat, start_service, stop_service, wait_for

ASKED = "a different agent found this ... Review it and verify if it is correct indeed"
NOT_ON_WINDOWS = ("The keeper looks after Linux servers: what it runs there is written for the server's own shell, "
                  "/bin/sh, and finds the scheduler through /proc or lsof. The test SSH server of this proof runs its "
                  "commands on the computer the proof runs on, and Windows has none of these.")
RESTARTED = "Server scheduler restarted: appserver: the scheduler was hung: it was stopped and a new one was started"
TASKLOOM_THERE = """\
#!/bin/sh
# Taskloom on the test server: this same Taskloom.
exec {python} -m taskloom.cli "$@"
"""
OTHER_PROGRAM = """\
import signal, sys, time
from pathlib import Path


def asked_to_stop(*_):  # written down, then it ends
    Path(sys.argv[0] + ".asked").write_text("asked to stop", encoding="utf-8")
    sys.exit(0)


signal.signal(signal.SIGTERM, asked_to_stop)
Path(sys.argv[0] + ".ready").write_text("running", encoding="utf-8")
time.sleep(600)  # it runs until it is stopped, at most 10 minutes
"""


def on_the_server(server_home: Path, *command: str) -> subprocess.Popen:
    """A process on the test server (it is this computer): HOME is the server's home, as for its SSH commands, and
    its Taskloom folder is the server's ~/.taskloom. It is reaped once it ends, as on a server, so that once stopped
    it is gone from the list of processes."""
    there = {"HOME": str(server_home), "TASKLOOM_HOME": str(server_home / ".taskloom")}
    proc = subprocess.Popen(list(command), cwd=server_home, env={**os.environ, **there},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc


def other_program(server_home: Path, name: str, *args: str) -> tuple[subprocess.Popen, Path]:
    """Another program on the server, ~/bin/<name>: it writes down if it is ever asked to stop (the file it returns)."""
    script = server_home / "bin" / name
    script.parent.mkdir(exist_ok=True)
    script.write_text(OTHER_PROGRAM, encoding="utf-8")
    proc = on_the_server(server_home, sys.executable, str(script), *args)
    ready = Path(f"{script}.ready")
    assert wait_for(lambda: ready.exists() or proc.returncode is not None, timeout=30), f"{name} did not start"
    assert ready.exists(), f"{name} did not start"
    return proc, Path(f"{script}.asked")


def ps(pids: list[int]) -> dict[int, tuple[str, str]]:
    """What ps says of these processes: {pid: (state, command line)}; a process that is not running is not in it."""
    out = subprocess.run(["ps", "-o", "pid=,stat=,command=", "-p", ",".join(map(str, pids))],
                         capture_output=True, text=True, timeout=30).stdout
    found = {}
    for line in out.splitlines():
        pid, stat, command = (line.split(None, 2) + ["", ""])[:3]
        found[int(pid)] = (stat, command)
    return found


def state(stat: str) -> str:
    return "frozen" if stat.startswith("T") else "ended" if stat.startswith("Z") else "running"


def holders(lock: Path) -> list[int]:
    """The processes that have this file open, as this computer tells: /proc, else lsof (the proof's own look)."""
    found = set()
    if Path("/proc/self/fd").is_dir():
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                fds = os.listdir(f"/proc/{pid}/fd")
            except OSError:  # gone, or not ours
                continue
            for fd in fds:
                try:
                    if os.path.samefile(f"/proc/{pid}/fd/{fd}", lock):
                        found.add(int(pid))
                except OSError:
                    pass
    else:
        out = subprocess.run(["lsof", "-t", str(lock)], capture_output=True, text=True, timeout=30).stdout
        found = {int(pid) for pid in out.split()}
    return sorted(found)


def lock_is_held(remote: Path) -> bool:
    """A scheduler holds the folder's lock: a new one could not start there."""
    try:
        acquire_lock(Home(remote)).close()
        return False
    except RuntimeError:
        return True


def the_server(server_home: Path, remote: Path, who: dict[int, str]) -> str:
    """What the server shows: what ps shows of its processes (those in `who`, {pid: what it is}), summarised; then
    the scheduler's files in its Taskloom folder."""
    listed = ps(list(who))
    rows = [f"{'PID':<8}{'State':<10}Command line"]
    for pid in who:
        stat, command = listed.get(pid, ("", ""))
        for python in {sys.executable, os.path.realpath(sys.executable)}:
            command = command.replace(python, "python")
        command = command.replace(str(server_home), "~")
        rows.append(f"{pid:<8}{state(stat) if pid in listed else 'gone':<10}{command or '(not running any more)'}")
    pid = int((remote / "scheduler.pid").read_text(encoding="utf-8"))
    beat = int(time.time()) - int(heartbeat(remote) or 0)
    held = holders(remote / "scheduler.lock")
    rows += ["", "In ~/.taskloom:",
             f"{'scheduler.pid':<21}{pid}  ({who.get(pid, 'not one of the processes above')})",
             f"{'scheduler.heartbeat':<21}its last check: " + (f"{beat // 60} minutes ago" if beat >= 120
                                                                else f"{beat} second{'s' * (beat != 1)} ago"),
             f"{'scheduler.lock':<21}held by " + (", ".join(f"{p} ({who.get(p, 'another process')})" for p in held)
                                                   or "no process")]
    return "\n".join(rows)


def taskloom_scheduler(pid: int) -> bool:
    """That process is a Taskloom scheduler (its command line says so)."""
    return "taskloom.cli scheduler" in ps([pid]).get(pid, ("", ""))[1]


def while_the_editor_runs(condition, timeout: float) -> bool:
    """Wait until `condition()` (at most `timeout` seconds), while the editor goes on: it keeps its window up to
    date."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        QTest.qWait(100)
    return True


@pytest.mark.linux_proof(NOT_ON_WINDOWS)
def test_the_keeper_replaces_a_hung_server_scheduler_and_stops_no_other_process(proof, env, server, win):
    p = proof("The keeper replaces a hung server scheduler, and stops no other process", ASKED)
    p.idea("When the scheduler on one of your servers hangs, the keeper on your PC now replaces it: it stops that "
           "scheduler, and no other process, starts a new one, and tells you “the scheduler was hung: it was stopped "
           "and a new one was started”.")
    p.idea("Pull request #1 found the case: a hung scheduler still holds its Taskloom folder's lock, so each new "
           "scheduler the keeper started there stopped at once, every 5 minutes, and no scheduled flow ran on that "
           "server. Its fix could stop programs that have nothing to do with Taskloom, so it was redone.")
    p.idea("To tell which process is the hung scheduler, the keeper goes by the lock itself. A scheduler opens its "
           "folder's lock file, scheduler.lock, as it starts, and holds it for as long as it runs. The keeper asks "
           "the server which processes have that very file open: through /proc on Linux, or with lsof where there "
           "is no /proc. It never goes by a process's name, nor by the number in scheduler.pid.")
    p.idea("It asks that process to stop, waits up to 5 seconds, and forces it only if it still holds the lock. Then "
           "it starts the new scheduler, and tells you once that one has checked.")
    p.idea("On a server where it cannot tell which process holds the lock, one with neither /proc nor lsof, it stops "
           "nothing. If a hung scheduler holds the lock there, the new one cannot start, and the keeper says so, and "
           "why: the server has neither /proc nor lsof.")
    p.why_simplest("The lock already says which process is that folder's scheduler: it is how Taskloom makes sure "
                   "only one scheduler runs per folder. Asking who holds it needs nothing new on the server: no "
                   "extra file, no extra program, nothing for you to set.")
    p.why_simplest("Asking first, and forcing only if needed, lets a scheduler that is merely slow end by itself; a "
                   "process that let go of the lock meanwhile is not forced.")
    p.why_simplest("Everything else is as it was: the keeper's check every 5 minutes, what counts as hung (no check "
                   "for 2 minutes), how the new scheduler is started, and the wait for it to check.")
    p.alternative("Stop the process whose number is in scheduler.pid, if its command line says “scheduler” (pull "
                  "request #1's fix)",
                  "That file does not always name the hung scheduler. Each failed start writes its own number "
                  "there, and that number is free again once the start has failed; after a reboot, the file still "
                  "holds the number from before. Such a number can go to any program, even one with “scheduler” in "
                  "its name: the keeper would stop that program, and leave a hung scheduler holding the lock. It also "
                  "read the command line from /proc, so on a server without /proc it stopped nothing.")
    p.alternative("Stop every process whose command line says “scheduler”",
                  "It would stop other programs with that word in their command line, like one started as "
                  "“mail-digest scheduler”, and the schedulers of your other Taskloom folders on the same server.")
    p.alternative("Delete the lock file, so that the new scheduler can start",
                  "Two schedulers would then run for the same folder: if the hung one came back to life, it would "
                  "run your flows a second time. The lock is there to prevent exactly that.")
    p.fits("The keeper is the part of the scheduler service that keeps your servers' schedulers running: the "
           "servers in Tools > Settings, under “Servers whose scheduler this computer keeps running”. It checks them "
           "when the service starts and every 5 minutes. What it does when a scheduler is not running at all is "
           "unchanged; only a hung one is now stopped first.")
    p.fits("It goes by the same lock the editor uses to tell whether the scheduler service is on (the Scheduler "
           "service button). Each Taskloom folder has its own lock file, so the scheduler of another Taskloom folder "
           "on the same server is never taken for this one.")
    p.fits("Its message is the one it gave when it started a scheduler that was not running, “Server scheduler "
           "restarted”, now saying that the scheduler was hung. Like every message of the service, it shows as a "
           "notification of the tray icon, or is printed by the headless service.")
    if sys.platform == "win32":
        pytest.skip(f"Not shown on Windows. {NOT_ON_WINDOWS} CI shows this proof on Linux.")

    server_home = server.home_path
    remote = server_home / ".taskloom"  # the server's Taskloom folder
    taskloom_there = server_home / "taskloom" / "taskloom"
    taskloom_there.parent.mkdir()
    taskloom_there.write_text(TASKLOOM_THERE.format(python=shlex.quote(sys.executable)), encoding="utf-8")
    taskloom_there.chmod(0o755)
    hung = service = None
    others = []
    try:
        p.step("Choose Settings… in the Tools menu, press Add server, type appserver as its SSH connection, and press "
               "Save. The Taskloom command on the server and the folder there keep what Add server fills in: "
               "~/taskloom/taskloom and ~/.taskloom.")
        p.note("The server is a test SSH server on this computer, set up in Tools > Connections as appserver. Its home "
               "is a temporary folder, and it runs commands in /bin/sh, as a Linux server does. Its "
               "~/taskloom/taskloom starts this same Taskloom.")
        settings = next(a for a in next(a.menu() for a in win.menuBar().actions() if a.text() == "&Tools").actions()
                        if a.text() == "Settings…")
        typed = []

        def set_the_keeper(dialog):
            QTest.mouseClick(next(b for b in dialog.findChildren(QPushButton) if b.text() == "Add server"),
                             Qt.LeftButton)
            table, row = dialog.keeper, dialog.keeper.rowCount() - 1
            table.item(row, 0).setText("appserver")
            typed.extend(table.item(row, column).text() for column in range(3))
            table.resizeColumnsToContents()  # as you would widen them, to read them whole
            heading = next(w for w in dialog.findChildren(QLabel) if w.text().startswith("Servers whose scheduler"))
            p.shot(dialog, "① This computer keeps appserver's scheduler running: the Taskloom command there is "
                           "~/taskloom/taskloom, for its Taskloom folder ~/.taskloom. (The table's columns are "
                           "widened, to show them whole.)",
                   highlight=[[heading, table]], badges=True)
            next(b for b in dialog.findChildren(QPushButton) if b.text().replace("&", "") == "Save").click()

        p.while_open(settings.trigger, set_the_keeper)
        p.check("① Settings lists appserver, with ~/taskloom/taskloom and ~/.taskloom",
                typed == ["appserver", "~/taskloom/taskloom", "~/.taskloom"])
        p.check("Saved: the keeper looks after appserver",
                Home(env.home).settings()["keeper"] == [{"connection": "appserver", "command": "~/taskloom/taskloom",
                                                         "home": "~/.taskloom"}])

        p.step("On the server, its scheduler runs, then hangs: it holds ~/.taskloom's lock, but checks nothing any "
               "more. Two other programs run there too: mail-digest, started as “mail-digest scheduler”, and "
               "report-scheduler.")
        remote.mkdir()
        hung = on_the_server(server_home, str(taskloom_there), "scheduler", "--headless")
        ran = wait_for(lambda: heartbeat(remote) is not None and (remote / "scheduler.pid").exists()
                               and (remote / "scheduler.pid").read_text(encoding="utf-8") == str(hung.pid), timeout=60)
        p.check("The server's scheduler ran: it checked, and it holds ~/.taskloom's lock",
                ran and lock_is_held(remote) and holders(remote / "scheduler.lock") == [hung.pid])
        os.kill(hung.pid, signal.SIGSTOP)  # it hangs: it checks nothing more, and cannot answer a request to stop
        assert wait_for(lambda: state(ps([hung.pid]).get(hung.pid, ("", ""))[0]) == "frozen", timeout=10)
        (remote / "scheduler.heartbeat").write_text(str(int(time.time()) - 600), encoding="utf-8")
        mail_digest, mail_digest_asked = other_program(server_home, "mail-digest", "scheduler")
        report_scheduler, report_scheduler_asked = other_program(server_home, "report-scheduler")
        others = [mail_digest, report_scheduler]
        (remote / "scheduler.pid").write_text(str(report_scheduler.pid), encoding="utf-8")
        p.note("The proof cannot wait for a real hang, so it freezes the scheduler, as a debugger does: it checks "
               "nothing, and cannot answer a request to stop. It sets the last check to 10 minutes ago (the keeper "
               "counts more than 2 minutes as hung), and writes report-scheduler's number in scheduler.pid, as when "
               "a number left there by a failed start, or from before a reboot, goes to another program.")
        who = {hung.pid: "the hung scheduler", mail_digest.pid: "mail-digest", report_scheduler.pid: "report-scheduler"}
        p.text("On the server: what ps shows of its processes, summarised, and the scheduler's files in ~/.taskloom",
               the_server(server_home, remote, who))
        p.check("Now it is frozen, its last check was 10 minutes ago, and it still holds the lock: no new scheduler "
                "could start there",
                state(ps([hung.pid])[hung.pid][0]) == "frozen" and int(heartbeat(remote)) <= time.time() - 590
                and lock_is_held(remote) and holders(remote / "scheduler.lock") == [hung.pid])
        p.check("mail-digest and report-scheduler run, and scheduler.pid holds report-scheduler's number",
                [state(s) for s, _ in ps([mail_digest.pid, report_scheduler.pid]).values()] == ["running"] * 2
                and (remote / "scheduler.pid").read_text(encoding="utf-8") == str(report_scheduler.pid))
        button = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.scheduler_action)
        off = while_the_editor_runs(lambda: win.scheduler_action.text() == "Scheduler service off", timeout=10)
        p.shot(button, "The editor, top right: Scheduler service off. This computer's scheduler service, and its "
                       "keeper, are not started yet: nothing looks after appserver, so its scheduler stays hung, as "
                       "listed above.")
        p.check("The editor shows the scheduler service off", off)

        p.step("Start the scheduler service on this computer (taskloom scheduler --headless, as from a terminal). Its "
               "keeper checks appserver at once, as it does each time the service starts, then every 5 minutes.")
        p.note("The keeper has no window: it runs inside the scheduler service. On your PC the service tells you what "
               "the keeper did in a notification of its tray icon; started from a terminal, as here, it prints the "
               "same messages. So the next step shows text: what the service printed, and what the server shows.")
        printed = env.root / "service-printed.txt"
        started = time.time()
        with open(printed, "w", encoding="utf-8") as out:
            service = start_service(env.home, stdout=out)

        def said() -> list[str]:  # each line: "<date> <time> <title>: <message>"
            return [line.split(" ", 2)[-1] for line in printed.read_text(encoding="utf-8").splitlines()]

        told = while_the_editor_runs(lambda: any("appserver" in line for line in said()), timeout=90)
        on = while_the_editor_runs(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=10)
        p.shot(button, "The editor, top right: Scheduler service on. The service runs on this computer, and its "
                       "keeper with it.")
        stop_service(env.home, service)
        p.check("The editor shows the scheduler service on", on)

        p.step("Read what the service printed, and look at the server again.")
        p.text("What the service printed (its Taskloom folder on this computer shortened to …/home)",
               printed.read_text(encoding="utf-8").replace(str(env.home), "…/home").strip())
        p.check("The service said: “Server scheduler restarted: appserver: the scheduler was hung: it was stopped and "
                "a new one was started”", told and [line for line in said() if "appserver" in line] == [RESTARTED])
        new = int((remote / "scheduler.pid").read_text(encoding="utf-8"))
        who[new] = "the new scheduler"
        p.text("On the server: what ps shows of its processes, summarised, and the scheduler's files in ~/.taskloom",
               the_server(server_home, remote, who))
        p.check("The hung scheduler is gone: frozen, it could not answer the request to stop, so it was forced",
                wait_for(lambda: hung.returncode is not None, timeout=10) and hung.returncode == -signal.SIGKILL)
        p.check("A new scheduler runs for ~/.taskloom: it wrote scheduler.pid, holds the lock, and has checked",
                new not in (hung.pid, mail_digest.pid, report_scheduler.pid) and pid_alive(new)
                and taskloom_scheduler(new) and holders(remote / "scheduler.lock") == [new]
                and int(heartbeat(remote) or 0) >= started - 1)
        p.check("mail-digest still runs, and was never asked to stop",
                mail_digest.returncode is None and pid_alive(mail_digest.pid) and not mail_digest_asked.exists())
        p.check("report-scheduler, whose number is in scheduler.pid, still runs, and was never asked to stop",
                report_scheduler.returncode is None and pid_alive(report_scheduler.pid)
                and not report_scheduler_asked.exists())
        off = while_the_editor_runs(lambda: win.scheduler_action.text() == "Scheduler service off", timeout=10)
        p.shot(button, "The editor, top right: Scheduler service off again. The proof stopped the service once its "
                       "keeper had replaced the hung scheduler: that is the last line it printed, “Scheduler "
                       "stopped”. On your PC it keeps running, and its keeper checks appserver every 5 minutes.")
        p.check("The service was stopped at the end: the editor shows it off, and its last line says “Scheduler "
                "stopped”", off and said()[-1].startswith("Scheduler stopped:"))
        p.note(("Here the keeper found the hung scheduler through /proc." if Path("/proc/self/fd").is_dir() else
                "Here the keeper found the hung scheduler with lsof, as this computer has no /proc.")
               + " Not shown: a server with neither /proc nor lsof. There the keeper cannot tell which process holds "
                 "the lock, so it stops nothing; it tells you the new scheduler could not be started, and why: "
                 "“appserver has neither /proc nor lsof”.")
    finally:
        if service is not None:
            stop_service(env.home, service)
        for proc in [hung, *others]:
            if proc is not None and proc.returncode is None:
                proc.kill()  # (a frozen process ends this way too)
        try:  # the scheduler the keeper started on the server, if it did
            new = int((remote / "scheduler.pid").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            new = None
        if new and new != os.getpid() and taskloom_scheduler(new):
            os.kill(new, signal.SIGTERM)
            if not wait_for(lambda: not pid_alive(new), timeout=15):
                os.kill(new, signal.SIGKILL)
        for proc in [hung, *others]:
            if proc is not None:
                wait_for(lambda: proc.returncode is not None, timeout=15)
