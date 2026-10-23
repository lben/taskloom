"""Runs are kept by age: `keep_runs_for` in settings.yaml (a number and a unit; 5 years by default) decides when a
run (its history record, block results, logs and its folder) is deleted. Through `taskloom run|validate`, the
run history, the runs folder, Tools > Settings in the editor and the README."""

import datetime as dt
import os
import re
import textwrap
import time

import pytest

from conftest import REPO
from tests.test_paths_editor_editing import menu, press, when_dialog_shows

QUICK = """
    blocks:
      note:
        type: logic.python
        config: {inputs: [], code: "print('a line in the log'); output = 1"}
"""
TOUCH = """
    blocks:
      touch:
        type: logic.python
        config: {inputs: [], outputs: [], code: "open('ran.txt', 'w').write('x')"}
"""


def write(env, text: str, name: str = "flow.yaml"):
    path = env.root / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def started_ago(env, run_id: int, days: float):
    """Make run `run_id` one that started (and finished) `days` ago: in the history and on disk."""
    when = dt.datetime.now() - dt.timedelta(days=days)
    env.query("UPDATE runs SET started=?, finished=CASE WHEN finished IS NULL THEN NULL ELSE ? END WHERE id=?",
              when.isoformat(timespec="milliseconds"), when.isoformat(timespec="milliseconds"), run_id)
    folder = env.home / "runs" / str(run_id)
    for path in sorted(folder.rglob("*"), reverse=True) + [folder]:
        os.utime(path, (when.timestamp(), when.timestamp()))


def kept(env) -> dict:
    """What is left of each run: its history record, block results, logs and folder."""
    return {"runs": [i for (i,) in env.query("SELECT id FROM runs ORDER BY id")],
            "blocks": [i for (i,) in env.query("SELECT DISTINCT run_id FROM blocks ORDER BY run_id")],
            "logs": [i for (i,) in env.query("SELECT DISTINCT run_id FROM logs ORDER BY run_id")],
            "folders": sorted(int(p.name) for p in (env.home / "runs").iterdir() if p.name.isdigit())}


def runs_recorded(env) -> int:
    if not (env.home / "history.db").exists() or not env.query(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"):
        return 0
    return env.query("SELECT count(*) FROM runs")[0][0]


# --- 2. newer runs are all kept, however many --------------------------------------------------------------------

def test_more_than_20_runs_are_all_kept_with_their_files(env):
    # The user: "Do not delete runs after a 20 count". No settings.yaml: the default, 5 years.
    flow = write(env, QUICK)
    for _ in range(21):
        ran = env.taskloom("run", flow)
        assert ran.returncode == 0, ran.stderr

    every = list(range(1, 22))
    assert kept(env) == {"runs": every, "blocks": every, "logs": every, "folders": every}


# --- 1 and 2. the default is 5 years; older runs go entirely, newer ones stay ----------------------------------

def test_by_default_a_run_older_than_5_years_is_deleted_and_a_newer_one_kept(env):
    flow = write(env, QUICK)
    for _ in range(2):
        assert env.taskloom("run", flow).returncode == 0
    started_ago(env, 1, 5 * 365.25 + 40)  # a little over 5 years ago
    started_ago(env, 2, 5 * 365.25 - 40)  # a little under

    ran = env.taskloom("run", flow)  # retention is applied as a run ends

    assert ran.returncode == 0, ran.stderr
    assert kept(env) == {"runs": [2, 3], "blocks": [2, 3], "logs": [2, 3], "folders": [2, 3]}


# --- 1. keep_runs_for is a number and a unit: days, months, years (singular too) --------------------------------

@pytest.mark.parametrize("setting, older, newer", [
    ("90 days", 95, 85),
    ("18 months", 570, 520),
    ("1 year", 380, 350),
    ("1 month", 35, 25),
])
def test_keep_runs_for_deletes_only_the_runs_that_started_longer_ago(env, setting, older, newer):
    env.write("home/settings.yaml", f"keep_runs_for: {setting}\n")
    flow = write(env, QUICK)
    for _ in range(2):
        ran = env.taskloom("run", flow)
        assert ran.returncode == 0, ran.stderr
    started_ago(env, 1, older)
    started_ago(env, 2, newer)

    ran = env.taskloom("run", flow)

    assert ran.returncode == 0, ran.stderr
    assert kept(env) == {"runs": [2, 3], "blocks": [2, 3], "logs": [2, 3], "folders": [2, 3]}


# --- 2. a run still going is never deleted ------------------------------------------------------------------------

def test_a_run_still_going_is_kept_however_old_while_an_old_finished_run_goes(env):
    quick = write(env, QUICK)
    slow = write(env, """
        blocks:
          first:
            type: logic.python
            config: {inputs: [], code: "output = pl.DataFrame({'n': [1, 2, 3]})"}
          pause:
            type: logic.wait
            config: {seconds: 120}
        edges:
          - first.done -> pause.after
    """, "slow.yaml")
    assert env.taskloom("run", quick).returncode == 0
    started_ago(env, 1, 10 * 365)
    going = env.popen("run", slow)
    try:
        deadline = time.monotonic() + 60
        while env.query("SELECT status FROM blocks WHERE run_id=2 AND block='pause'") != [("running",)]:
            assert going.poll() is None and time.monotonic() < deadline, "the slow run never got to its wait"
            time.sleep(0.05)
        started_ago(env, 2, 10 * 365)

        ran = env.taskloom("run", quick)

        assert ran.returncode == 0, ran.stderr
        assert env.query("SELECT id, status FROM runs ORDER BY id") == [(2, "running"), (3, "success")]
        assert kept(env)["folders"] == [2, 3]
        assert list((env.home / "runs" / "2" / "first").rglob("*.parquet"))  # its files, too
        assert env.query("SELECT block, status FROM blocks WHERE run_id=2 ORDER BY block") == [
            ("first", "success"), ("pause", "running")]
    finally:
        going.kill()
        going.communicate(timeout=30)


# --- 2. deleting an old run never breaks a newer run that reused its tables -------------------------------------

def test_a_rerun_from_a_deleted_old_run_still_has_its_tables(env):
    flow = write(env, """
        blocks:
          load:
            type: logic.python
            config: {inputs: [], code: "output = pl.DataFrame({'n': [1, 2, 3]})"}
          save:
            type: logic.python
            config: {inputs: [t], outputs: [], code: "open('rows.txt', 'a').write(str(len(t)))"}
        edges:
          - load.output -> save.t
    """)
    assert env.taskloom("run", flow).returncode == 0  # run 1
    ran = env.taskloom("run", flow, "--only", "save", "--inputs-from", 1)  # run 2 reuses run 1's table
    assert ran.returncode == 0, ran.stderr
    started_ago(env, 1, 6 * 365)
    ran = env.taskloom("run", flow)  # run 3: run 1 is older than 5 years now
    assert ran.returncode == 0, ran.stderr
    assert kept(env)["runs"] == [2, 3] and kept(env)["folders"] == [2, 3]

    ran = env.taskloom("run", flow, "--only", "save", "--inputs-from", 2)

    assert ran.returncode == 0, ran.stderr
    assert (env.root / "rows.txt").read_text(encoding="utf-8") == "3333"


# --- 3. a keep_runs_for that cannot be read is an error before a run starts --------------------------------------

@pytest.mark.parametrize("value", ["forever", "5 weeks", "-1 years", "5"])
def test_a_keep_runs_for_that_cannot_be_read_is_refused_naming_it_and_the_form(env, value):
    env.write("home/settings.yaml", f"keep_runs_for: {value}\n")
    flow = write(env, TOUCH)

    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert result.returncode == 2, (command, result.stdout, result.stderr)
        assert "keep_runs_for" in result.stderr and "Traceback" not in result.stderr, (command, result.stderr)
        said = result.stderr.replace(value, "")
        assert any(unit in said for unit in ("days", "months", "years")), (command, result.stderr)  # the form
    assert not (env.root / "ran.txt").exists() and runs_recorded(env) == 0


# --- 1. keep_runs and history_days are gone: the usual unknown keys error ---------------------------------------

def test_an_old_settings_file_with_keep_runs_and_history_days_is_refused_naming_them(env):
    env.write("home/settings.yaml", "keep_runs: 20\nhistory_days: 365\n")
    flow = write(env, TOUCH)

    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert result.returncode == 2, (command, result.stdout, result.stderr)
        assert "settings.yaml" in result.stderr and "unknown keys" in result.stderr, (command, result.stderr)
        assert re.search(r"\bkeep_runs\b", result.stderr) and "history_days" in result.stderr, (command, result.stderr)
    assert not (env.root / "ran.txt").exists() and runs_recorded(env) == 0


# --- 4. Tools > Settings: Keep runs for, as a number and a unit, and the disk space the runs take ---------------

def keep_runs_for_row(dialog) -> list:
    """The widgets on the Settings form row labelled 'Keep runs for'."""
    from PySide6.QtWidgets import QFormLayout, QLabel, QWidget

    def widgets(item):
        if item.widget() is not None:
            return [item.widget(), *item.widget().findChildren(QWidget)]
        layout = item.layout()
        return [w for i in range(layout.count()) for w in widgets(layout.itemAt(i))] if layout is not None else []

    for form in dialog.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            label, field = form.itemAt(row, QFormLayout.LabelRole), form.itemAt(row, QFormLayout.FieldRole)
            if (label and field and isinstance(label.widget(), QLabel)
                    and label.widget().text().replace("&", "").rstrip(":") == "Keep runs for"):
                return widgets(field)
    raise AssertionError("no row 'Keep runs for'")


def shown(dialog, seen: dict):
    """Note in `seen` what the 'Keep runs for' row shows, or why it cannot be read; return its number and unit."""
    from PySide6.QtWidgets import QComboBox, QLabel, QSpinBox

    try:
        row = keep_runs_for_row(dialog)
        [number] = [w for w in row if isinstance(w, QSpinBox)]
        [unit] = [w for w in row if isinstance(w, QComboBox)]
    except (AssertionError, ValueError) as e:  # (raised here, it would be lost in Qt's event loop)
        seen["problem"] = f"{type(e).__name__}: {e}"
        return None, None
    seen.update({"number": number.value(), "unit": unit.currentText(),
                 "units": sorted(unit.itemText(i) for i in range(unit.count())),
                 "next to it": " ".join(w.text() for w in row if isinstance(w, QLabel))})
    return number, unit


def settings_menu(win) -> list:
    """Tools > Settings; the message boxes it showed (a mistake in settings.yaml is told in one)."""
    from PySide6.QtWidgets import QApplication

    menu(win, "Settings…").trigger()
    QApplication.processEvents()
    said = [text for _, (_, text) in win.message_boxes]
    win.message_boxes.clear()
    return said


def test_settings_show_keep_runs_for_and_the_space_the_runs_take_and_save_it(win, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # Save on Windows writes a Startup script there
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "settings.yaml").write_text("keep_runs_for: 18 months\ncalendar: US\n", encoding="utf-8")
    for run_id in (1, 2):  # 1.24 MB in all: 1.2 MB, counted in 1000s or 1024s
        folder = win.home.runs_dir / str(run_id) / "load"
        folder.mkdir(parents=True)
        (folder / "output.parquet").write_bytes(b"\0" * 620_000)
    seen = {}

    def look_change_and_save(dialog):
        number, unit = shown(dialog, seen)
        if number is not None:
            number.setValue(90)
            unit.setCurrentText("days")
            press(dialog, "Save")

    when_dialog_shows(win, look_change_and_save)
    assert settings_menu(win) == []

    assert {k: seen.get(k) for k in ("number", "unit", "units")} == {
        "number": 18, "unit": "months", "units": ["days", "months", "years"]}, seen
    assert "1.2 MB" in seen["next to it"], seen
    settings = win.home.settings()
    assert settings["keep_runs_for"] == "90 days" and settings["calendar"] == "US"


def test_settings_show_5_years_when_settings_yaml_does_not_say(win):
    seen = {}
    when_dialog_shows(win, lambda dialog: (shown(dialog, seen), dialog.reject()))

    assert settings_menu(win) == []

    assert {k: seen.get(k) for k in ("number", "unit")} == {"number": 5, "unit": "years"}, seen


# --- 5. README ------------------------------------------------------------------------------------------------

def test_the_readme_describes_keep_runs_for_and_no_longer_keep_runs_or_history_days():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    [row] = [line for line in readme.splitlines() if line.startswith("| `settings.yaml` |")]

    assert "keep_runs_for" in row
    text = readme.replace(row, "")
    assert "keep_runs_for" in text and "5 years" in text
    assert not re.search(r"\bkeep_runs\b|\bhistory_days\b", readme)
