"""Run history lists every finished run of the flow as its own row, newest first, whoever started it: also each run
the scheduler service starts while the editor shows the flow (after you double-clicked an older run to show it, and
while a run of yours is going), whatever spelling of the flow's path the editor opened it with. A row never turns
into another run. Through the real scheduler service, the `taskloom run` processes (as the service starts them),
history.db and the editor (offscreen)."""

import contextlib
import ctypes
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from PySide6.QtWidgets import QApplication

from tests.test_lines import click_row, shown, wait_runs
from tests.test_paths_editor_running import eventually
from tests.test_scheduler_start_at import stop_service

# --- helpers ---------------------------------------------------------------------------------------------------


def write_flow(path: Path, schedule: dict | None = None, wait: float = 0) -> Path:
    """A flow as the editor saves it: its Manual Start `manual` runs `check` (which waits `wait` seconds), its
    Schedule block `schedule` (once a year, unless `schedule` says otherwise) runs `load`."""
    blocks = {
        "manual": {"type": "trigger.manual"},
        "check": {"type": "logic.wait", "config": {"seconds": wait}},
        "schedule": {"type": "trigger.schedule", "config": schedule or {"cron": "0 2 1 1 *"}},
        "load": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": ["manual.start -> check.after",
                                                                 "schedule.start -> load.after"]}, sort_keys=False),
                    encoding="utf-8")
    return path


def run_cli(win, flow: Path, *args) -> int:
    """`taskloom run <flow> ...` as the scheduler service (`--trigger schedule`) or a terminal runs it; its run."""
    result = subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow), *args],
                            env={**os.environ, "TASKLOOM_HOME": str(win.home.root)}, capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stderr
    with contextlib.closing(sqlite3.connect(win.home.history_db)) as db:
        return db.execute("SELECT max(id) FROM runs").fetchone()[0]


def finished_runs(win, flow: Path) -> list[tuple[str, str, str]]:
    """The finished runs of the file `flow` in history.db, newest first, however its path was spelled:
    (run, started by, status)."""
    with contextlib.closing(sqlite3.connect(win.home.history_db)) as db:
        found = db.execute("SELECT id, flow_path, started_by, status FROM runs WHERE status != 'running' "
                           "ORDER BY id DESC").fetchall()
    return [(str(run_id), by, status) for run_id, path, by, status in found if os.path.samefile(path, flow)]


def rows(win) -> list[tuple[str, str, str]]:
    """Run history as the editor lists it, top to bottom: (run, started by, status) of each row."""
    table = win.history_panel.table
    labels = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    columns = [labels.index(name) for name in ("Run", "Started by", "Status")]
    return [tuple(table.item(r, c).text() for c in columns) for r in range(table.rowCount())]


def double_click(win, run_id: int):
    """Double-click the run's row in Run history: that run is shown."""
    win.bottom_tabs.setCurrentWidget(win.history_panel)
    table = win.history_panel.table
    (row,) = [r for r in range(table.rowCount()) if table.item(r, 0).text() == str(run_id)]
    table.scrollToItem(table.item(row, 0))
    QApplication.processEvents()
    click_row(table, row, double=True)


@contextlib.contextmanager
def scheduler_service(win, qtbot):
    """The real scheduler service (`taskloom scheduler --headless`) for the editor's Taskloom folder; turned off
    afterwards as the editor turns it off."""
    proc = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"],
                            env={**os.environ, "TASKLOOM_HOME": str(win.home.root)}, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        heartbeat = win.home.root / "scheduler.heartbeat"
        qtbot.waitUntil(lambda: heartbeat.exists() or proc.poll() is not None, timeout=30_000)
        assert proc.poll() is None, "the scheduler service did not start"
        yield proc
    finally:
        stop_service(SimpleNamespace(home=win.home.root), proc)  # (it reads home / scheduler.pid)


def short_name(path: Path) -> Path | None:
    """The 8.3 short spelling of `path` (Windows), or None if the drive gives none."""
    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer)):
        return None
    return Path(buffer.value) if buffer.value.lower() != str(path).lower() else None


def spelled(spelling: str, flow: Path, monkeypatch) -> Path:
    """Another spelling of the path of the file `flow` (<tmp>/Monthly Reports/nightly.yaml)."""
    folder = flow.parent
    if spelling == "relative":
        monkeypatch.chdir(folder.parent)
        return Path(folder.name) / ".." / folder.name / flow.name
    if spelling == "symlinked folder":
        link = folder.parent / "linked"
        try:
            link.symlink_to(folder, target_is_directory=True)
        except OSError as e:  # e.g. Windows without the right to create symbolic links
            pytest.skip(f"cannot create a symbolic link here: {e}")
        return link / flow.name
    if spelling == "other case":
        other = folder.parent / folder.name.upper() / (flow.stem.upper() + flow.suffix)
        if not other.exists():
            pytest.skip("this file system tells names apart by case: another case is another file")
        return other
    assert spelling == "short name"
    if sys.platform != "win32":
        pytest.skip("8.3 short names exist only on Windows")
    other = short_name(folder)
    if other is None:
        pytest.skip("this drive gives no 8.3 short names")
    return other / flow.name


# --- 1, 2: each run its own row, also the scheduler service's, also after an older run was shown ------------------

def test_each_run_the_scheduler_service_starts_is_its_own_new_row_also_after_you_showed_an_older_run(win, qtbot,
                                                                                                   tmp_path):
    """The user's report: the scheduler service on, the flow open in the editor, an older run shown (double-clicked
    in Run history). Each time the Schedule block starts the flow, a new row comes to the top of the list; the rows
    before stay as they were."""
    flow = write_flow(tmp_path / "nightly.yaml", schedule={"every": "1 minute"})
    tab = win.open_flow(flow)
    assert win.save(tab)  # saved with its Schedule block: listed for the scheduler service
    win.run_flow(tab)
    first = wait_runs(qtbot, tab, 1)
    win.run_flow(tab)
    second = wait_runs(qtbot, tab, 2)
    manual = [(str(second), "manual", "success"), (str(first), "manual", "success")]
    assert eventually(qtbot, lambda: rows(win) == manual), rows(win)
    double_click(win, first)  # an older run, shown on purpose
    assert shown(tab, ["check"]) == {"check": ("success", first)}

    with scheduler_service(win, qtbot):
        # The first right away (the service has just seen the schedule), the next a minute later.
        for n, timeout in ((1, 30_000), (2, 80_000)):
            def scheduled() -> list:
                return [r for r in finished_runs(win, flow) if r[1] == "schedule"]

            qtbot.waitUntil(lambda: len(scheduled()) == n, timeout=timeout)
            expected = scheduled() + manual
            assert finished_runs(win, flow) == expected
            assert [status for _, _, status in expected] == ["success"] * len(expected)

            assert eventually(qtbot, lambda: rows(win) == expected, timeout=10_000), \
                f"scheduled run {n}: Run history lists {rows(win)}, history.db has {expected}"


def test_a_scheduled_run_that_ends_while_your_run_is_going_is_listed_before_yours_ends(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "nightly.yaml", wait=30)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs[-1].run_id is not None, timeout=30_000)  # yours has begun
    scheduled = run_cli(win, flow, "--trigger", "schedule")  # as the scheduler service runs it

    listed = eventually(qtbot, lambda: [r for r in rows(win) if r[2] != "running"] ==
                        [(str(scheduled), "schedule", "success")], timeout=10_000)
    still_going, seen = tab.running, rows(win)
    win.stop(tab)
    qtbot.waitUntil(lambda: not tab.running, timeout=30_000)
    assert still_going, "your run ended before Run history was read: the check was not made while it was going"
    assert listed, f"while your run is going, Run history lists {seen}, not scheduled run {scheduled}"
    assert eventually(qtbot, lambda: rows(win) == finished_runs(win, flow)), (rows(win), finished_runs(win, flow))


def test_every_finished_run_is_its_own_row_newest_first_whoever_started_it(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "nightly.yaml")
    tab = win.open_flow(flow)
    win.run_flow(tab)
    first = wait_runs(qtbot, tab, 1)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 2)
    double_click(win, first)  # an older run, shown on purpose
    before = rows(win)

    # A terminal, the scheduler service (its Schedule block), Run only and Run from (as the editor's block menu).
    for args in ((), ("--trigger", "schedule"), ("--only", "load"), ("--from", "load")):
        run_cli(win, flow, *args)
        expected = finished_runs(win, flow)
        assert eventually(qtbot, lambda: rows(win) == expected), \
            f"after `taskloom run {' '.join(args)}`: Run history lists {rows(win)}, history.db has {expected}"

    assert [by for _, by, _ in rows(win)] == ["from load", "only load", "schedule", "manual", "manual", "manual"]
    assert rows(win)[-2:] == before  # the rows listed before are the same runs, as they were


# --- 3: whatever spelling of the path the flow was opened with --------------------------------------------------

@pytest.mark.parametrize("spelling", ["relative", "symlinked folder", "other case", "short name"])
def test_the_editor_lists_the_runs_of_the_file_whatever_spelling_of_its_path_it_was_opened_with(
        win, qtbot, tmp_path, monkeypatch, spelling):
    flow = write_flow(tmp_path / "Monthly Reports" / "nightly.yaml")
    namesake = write_flow(tmp_path / "Other Reports" / "nightly.yaml")  # another file, of the same name
    other = spelled(spelling, flow, monkeypatch)
    assert os.path.samefile(other, flow) and str(other) != str(flow)
    run_cli(win, namesake)  # not a run of this flow: never listed for it
    run_cli(win, flow, "--trigger", "schedule")  # as the scheduler service runs it, before the flow is opened

    win.open_flow(other)
    expected = finished_runs(win, flow)
    assert len(expected) == 1
    assert eventually(qtbot, lambda: rows(win) == expected), f"opened as {other}: lists {rows(win)}, not {expected}"

    run_cli(win, other)  # while it is open, from a terminal, with the spelling it was opened with
    expected = finished_runs(win, flow)
    assert len(expected) == 2
    assert eventually(qtbot, lambda: rows(win) == expected), f"opened as {other}: lists {rows(win)}, not {expected}"
