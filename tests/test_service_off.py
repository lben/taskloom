"""The scheduler service is on or off, and the editor, its tray icon, the CLI and the README say so plainly."""

import contextlib
import html
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QMessageBox, QSystemTrayIcon

from tests.test_editor import REAL_START_SCHEDULER, write_flow

# Their Schedule block's time (once a year) never comes during a test: a scheduler service running then never
# starts them.
QUICK = """
    blocks:
      yearly:
        type: trigger.schedule
        config: {cron: "0 2 1 1 *"}
      work:
        type: logic.python
        config: {inputs: [], outputs: [], code: "pass"}
    edges:
      - yearly.start -> work.after
"""
SLOW = """
    blocks:
      yearly:
        type: trigger.schedule
        config: {cron: "0 2 1 1 *"}
      work:
        type: logic.wait
        config: {seconds: 60}
    edges:
      - yearly.start -> work.after
"""
README = Path(__file__).resolve().parents[1] / "README.md"


def _env(root) -> dict:
    return {**os.environ, "TASKLOOM_HOME": str(root), "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}


@contextlib.contextmanager
def scheduler_service(root):
    """The real scheduler service for this Taskloom folder (started from a terminal), stopped afterwards."""
    proc = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"], env=_env(root),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def stays_off_at_login(root):
    """Starting at login (`taskloom scheduler --unless-paused`) says the service is off and does not start it."""
    result = subprocess.run([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless", "--unless-paused"],
                            env=_env(root), capture_output=True, text=True, timeout=30)  # it would keep running
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert "scheduler service is off" in out and "you turned it off" in out, out
    assert "paus" not in out.lower(), out


def plain(tip: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", tip)).split())


@pytest.mark.skipif(sys.platform == "win32", reason="stops the scheduler with SIGTERM")
def test_off_no_schedule_runs_and_on_shows_the_countdown(win, qtbot, tmp_path, monkeypatch):
    """The user's case: off, a Schedule block shows no next time; turned on, the countdown to the next run."""
    started, starts, read = [], [], []

    class Recorded(subprocess.Popen):  # the scheduler service the editor starts outlives it: stop it at the end
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            started.append(self)

    monkeypatch.setattr(subprocess, "Popen", Recorded)

    def start_scheduler(by_you=False):
        starts.append(by_you)
        REAL_START_SCHEDULER(win, by_you)

    monkeypatch.setattr(win, "start_scheduler", start_scheduler)  # the real one, recorded
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: read.extend(map(str, a[1:3])) or QMessageBox.Yes))
    tab = win.open_flow(write_flow(tmp_path / "nightly.yaml", QUICK))

    def under() -> str:  # the text under the Schedule block (the canvas redraws its blocks when the flow changes)
        return tab.scene.nodes["yearly"].countdown

    def tab_tip() -> str:
        return win.tabs.tabToolTip(win.tabs.indexOf(tab))

    try:
        win.save(tab)  # a scheduled flow saved: the service starts by itself
        qtbot.waitUntil(lambda: under().startswith("next run in "), timeout=30_000)
        qtbot.waitUntil(lambda: bool(re.search(r"next \d{4}-01-01 02:00:00", tab_tip())), timeout=10_000)
        read.append(win.scheduler_action.toolTip())

        win.scheduler_action.trigger()  # the Scheduler service button: turn it off (yes)
        qtbot.waitUntil(lambda: not under().startswith("next run in "), timeout=30_000)
        assert under() == "scheduler service off"  # no countdown, no next time
        tip = plain(tab.scene.nodes["yearly"].countdown_area.toolTip())
        assert "scheduler service is off" in tip and re.search(r"(not|n't) start", tip), tip
        assert "Scheduler service button (top right)" in tip and re.search(r"\bturns? it on\b", tip), tip

        def tab_says_off():
            assert "scheduler service off" in tab_tip() and not re.search(r"next \d{4}-", tab_tip()), tab_tip()
        qtbot.waitUntil(tab_says_off, timeout=10_000)

        tab.doc.move_blocks({"work": (10, 10)})  # changed, not saved yet: as before
        qtbot.waitUntil(lambda: under().endswith("save to schedule"), timeout=5_000)
        win.save(tab)  # saved again while off: it stays off
        read.append(win.statusBar().currentMessage())
        qtbot.waitUntil(lambda: under() == "scheduler service off", timeout=5_000)
        assert starts == [False] and not win.scheduler_running()
        stays_off_at_login(win.home.root)  # also at login

        win.scheduler_action.trigger()  # turned on by you: it runs, and counts down again
        qtbot.waitUntil(lambda: under().startswith("next run in "), timeout=30_000)
        qtbot.waitUntil(lambda: bool(re.search(r"next \d{4}-01-01 02:00:00", tab_tip())), timeout=10_000)
        assert starts == [False, True] and "scheduler service off" not in tab_tip()
    finally:
        for proc in started:  # (the scheduler service the editor started among them)
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    assert [t for t in read if "paus" in t.lower()] == []  # the button, its question, the status bar: on or off


def test_flows_running_show_their_next_run_only_while_the_service_is_on(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "slow.yaml", SLOW)
    win.home.set_scheduled(flow.resolve(), True)  # (as saving it in the editor does)
    elsewhere = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "run", str(flow)], env=_env(win.home.root),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def item() -> str:
        actions = win.running_flows.menu().actions()
        return actions[0].text() if len(actions) == 1 else ""

    try:
        qtbot.waitUntil(lambda: "running since" in item(), timeout=30_000)
        assert "scheduler service off" in item() and "next run" not in item(), item()  # no time while off

        with scheduler_service(win.home.root):
            def next_run_shown():
                assert re.search(r"next run \d{4}-01-01 02:00:00", item()) and "service off" not in item(), item()
            qtbot.waitUntil(next_run_shown, timeout=30_000)
    finally:
        elsewhere.kill()
        elsewhere.wait()


def test_an_unticked_schedule_block_is_not_active(win, qtbot, tmp_path):
    tab = win.open_flow(write_flow(tmp_path / "nightly.yaml", QUICK))
    tab.scene.nodes["yearly"].setSelected(True)

    label = next(w for w in win.properties.findChildren(QLabel) if w.text() == "Active")
    help_ = label.toolTip().lower()  # unticking it stops this schedule starting the flow; its other ones still do
    assert "paus" not in help_ and "start" in help_ and "other schedule" in help_, help_
    box = next(w for w in win.properties.findChildren(QCheckBox) if w.toolTip() == label.toolTip())
    box.setChecked(False)  # untick Active
    win.save(tab)

    qtbot.waitUntil(lambda: tab.scene.nodes["yearly"].countdown == "not active", timeout=5_000)
    tip = tab.scene.nodes["yearly"].countdown_area.toolTip()
    assert "paus" not in tip.lower(), tip


def test_turning_the_service_off_from_its_tray_icon_keeps_it_off(qapp, env, monkeypatch):
    import gc

    from taskloom import cli

    monkeypatch.setenv("TASKLOOM_HOME", str(env.home))
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))  # (none offscreen)
    shown = {}

    def exec_():  # the tray icon is up: read it, then choose its item that turns the service off
        (tray,) = [o for o in gc.get_objects() if isinstance(o, QSystemTrayIcon)]
        actions = tray.contextMenu().actions()
        shown["menu"], shown["tooltip"] = [a.text() for a in actions], tray.toolTip()
        for action in actions:
            if action.text() == "Turn off the scheduler service":
                action.trigger()
        return 0

    monkeypatch.setattr(QApplication, "exec", staticmethod(exec_))
    quits = qapp.quitOnLastWindowClosed()
    try:
        assert cli.main(["scheduler"]) == 0  # `taskloom scheduler` on a desktop: with its tray icon
    finally:
        qapp.setQuitOnLastWindowClosed(quits)

    assert "Turn off the scheduler service" in shown["menu"], shown
    assert [t for t in [*shown["menu"], shown["tooltip"]] if "paus" in t.lower()] == [], shown
    stays_off_at_login(env.home)  # it stays off until you turn it on


def test_an_older_service_that_cannot_be_restarted_is_not_called_paused(win, qtbot, monkeypatch):
    from taskloom.scheduler import acquire_lock

    warned = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))  # restart it: yes
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[1:3])))
    assert not (win.home.root / "scheduler.pid").exists()  # its PID file is gone: it cannot be stopped
    lock = acquire_lock(win.home)  # a scheduler service from an older Taskloom (it writes no version)
    try:
        qtbot.waitUntil(lambda: bool(warned), timeout=10_000)
    finally:
        lock.close()

    assert [t for box in warned for t in box if "paus" in str(t).lower()] == [], warned


def test_the_scheduler_command_help_says_off_not_paused(env):
    result = env.taskloom("scheduler", "--help")

    assert result.returncode == 0 and "--unless-paused" in result.stdout  # (the flag keeps its name)
    assert "paus" not in result.stdout.replace("--unless-paused", "").lower(), result.stdout


def test_the_readme_says_the_service_is_turned_on_or_off():
    text = README.read_text(encoding="utf-8").replace("--unless-paused", "")
    about_it = [p for p in text.split("\n\n") if "scheduler" in p.lower()]

    assert about_it and [p for p in about_it if "paus" in p.lower()] == []
