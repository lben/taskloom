"""Each start block (Manual Start, Schedule) chooses how its runs are kept: 'Keep runs' in Properties is 'As in
Settings' (keep_runs_for; the default), 'All of them', 'The last N' or 'For a time' (a number and days, months or
years), with the tick box 'Always keep failed runs'. A run counts for the start blocks that started it (a re-run of
a block: for the start blocks whose lines hold that block) and is deleted, with its block results, logs and folder,
only when every one of them lets it go; what counts for no start block follows Settings. The choices are made in
the editor's Properties panel and saved from it, then the flows run with the real `taskloom run|validate`; what is
kept is read from the run history and the runs folder. And the README."""

import os
import re
import sqlite3
import time

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit,  # noqa: E402
                               QSpinBox, QWidget)

from conftest import REPO  # noqa: E402
from tests.test_paths_editor_editing import clean, field, select_only, settle, taskloom  # noqa: E402
from tests.test_retention_by_age import kept, runs_recorded, started_ago, write  # noqa: E402

OPTIONS = ["As in Settings", "All of them", "The last N", "For a time"]
UNITS = ["days", "months", "years"]
FAILED = "Always keep failed runs"
NOTE = "print('a line in the log'); output = 1"
YEAR = 365.25


def flow_yaml(starts: dict, code: str = NOTE) -> str:
    """A flow in which each start block ({name: "manual" for a Manual Start, else a Schedule's cron line})
    starts the Python block `note`."""
    blocks = {name: {"type": "trigger.manual"} if when == "manual" else
              {"type": "trigger.schedule", "config": {"cron": when}} for name, when in starts.items()}
    blocks["note"] = {"type": "logic.python", "config": {"inputs": [], "code": code}}
    return yaml.safe_dump({"blocks": blocks, "edges": [f"{name}.start -> note.after" for name in starts]},
                          sort_keys=False)


# --- Keep runs in Properties --------------------------------------------------------------------------------------

def words(widget) -> str:
    return (widget.title() if isinstance(widget, QGroupBox) else widget.text()).replace("&", "").rstrip(":").strip()


def on_row(body, label: str) -> list:
    """The widgets on the Properties rows labelled `label`."""
    found = []
    for form in body.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            name, value = form.itemAt(row, QFormLayout.LabelRole), form.itemAt(row, QFormLayout.FieldRole)
            if name and value and isinstance(name.widget(), QLabel) and words(name.widget()) == label and value.widget():
                found += [value.widget(), *value.widget().findChildren(QWidget)]
    return found


def keep_runs(win) -> dict:
    """What Properties shows of the selected start block's Keep runs (outside its When and Retry boxes), as lists
    of widgets: its "label", its "choice" (the four), the "number" and "unit" its choice shows, and the
    'Always keep failed runs' tick box ("failed")."""
    settle()
    body = win.properties.widget()
    boxes = [b for b in body.findChildren(QGroupBox) if b.title() in ("When", "Retry and timeout")]
    name = field(win, "Name")
    shown = [w for w in body.findChildren(QWidget) if w is not name and w.isVisibleTo(body) and w.isEnabled()
             and not any(b is w or b.isAncestorOf(w) for b in boxes)]
    combos = [w for w in shown if isinstance(w, QComboBox)]
    return {
        "label": [w for w in shown if isinstance(w, (QLabel, QGroupBox)) and words(w) == "Keep runs"],
        "choice": [c for c in combos if [c.itemText(i) for i in range(c.count())] == OPTIONS],
        "number": [w for w in shown if isinstance(w, QSpinBox)] or [w for w in shown if isinstance(w, QLineEdit)],
        "unit": [c for c in combos if all(c.findText(u) >= 0 for u in UNITS)],
        "failed": [b for b in shown if isinstance(b, QCheckBox) and (words(b) == FAILED or b in on_row(body, FAILED))],
    }


def one(win, part: str):
    found = keep_runs(win)[part]
    assert len(found) == 1, f"Properties of {win.properties.block_id!r}: expected one Keep runs {part}, got {found}"
    return found[0]


def choose_keep_runs(win, choice: str, number: int | None = None, unit: str | None = None, failed: bool = False):
    """Choose the selected start block's Keep runs in Properties."""
    one(win, "choice").setCurrentText(choice)
    assert one(win, "choice").currentText() == choice
    if number is not None:
        box = one(win, "number")
        if isinstance(box, QSpinBox):
            box.setValue(number)
        else:
            box.setText(str(number))
        box.editingFinished.emit()
    if unit is not None:
        one(win, "unit").setCurrentText(unit)
    if failed:
        one(win, "failed").setChecked(True)
    settle()


def keep_runs_shown(win) -> dict:
    """The selected start block's Keep runs as Properties shows it: the choice, its number and unit, the tick box."""
    choice = one(win, "choice").currentText()
    shown = {"choice": choice, "failed": one(win, "failed").isChecked()}
    if choice in ("The last N", "For a time"):
        number = one(win, "number")
        shown["number"] = number.value() if isinstance(number, QSpinBox) else int(number.text())
    if choice == "For a time":
        shown["unit"] = one(win, "unit").currentText()
    return shown


def choose_in_editor(win, path, keep: dict):
    """Open the flow, choose its start blocks' Keep runs in Properties ({block: (choice, number, unit, failed)}),
    save it and close it."""
    tab = win.open_flow(path)
    for block, choice in keep.items():
        select_only(tab, block)
        choose_keep_runs(win, *choice)
    assert win.save(tab)
    assert win.close_tab(tab)


def made_in_editor(win, path, text: str, keep: dict):
    path.write_text(text, encoding="utf-8")
    choose_in_editor(win, path, keep)
    return path


def run_ok(env, flow, *args):
    ran = env.taskloom("run", flow, *args)
    assert ran.returncode == 0, ran.stderr


def started_by(env) -> list[tuple]:
    return env.query("SELECT id, started_by FROM runs ORDER BY id")


# --- 1. Keep runs in Properties: As in Settings by default, the four choices, failed runs not kept apart ---------

def test_manual_start_and_schedule_show_keep_runs_as_in_settings_with_its_choices_and_failed_runs_unticked(win, tmp_path):
    path = tmp_path / "flow.yaml"
    path.write_text(flow_yaml({"manual": "manual", "nightly": "0 2 * * *"}), encoding="utf-8")
    tab = win.open_flow(path)

    for block in ("manual", "nightly"):
        select_only(tab, block)
        assert keep_runs(win)["label"], f"{block}: no 'Keep runs' in Properties"
        assert keep_runs_shown(win) == {"choice": "As in Settings", "failed": False}, block

        choose_keep_runs(win, "The last N")
        one(win, "number")  # a number to give
        choose_keep_runs(win, "For a time")
        one(win, "number")  # and its unit
        unit = one(win, "unit")
        assert [unit.itemText(i) for i in range(unit.count())] == UNITS, block
    clean(win)


def test_keep_runs_chosen_in_properties_is_saved_and_shown_again_when_the_flow_is_opened_again(win, tmp_path):
    chosen = {"manual": ("As in Settings", None, None, True), "manual_2": ("All of them",),
              "nightly": ("The last N", 3), "check": ("For a time", 18, "months", True)}
    path = made_in_editor(win, tmp_path / "flow.yaml", flow_yaml(
        {"manual": "manual", "manual_2": "manual", "nightly": "0 2 * * *", "check": "* * * * *"}), chosen)

    checked = taskloom(win, "validate", path)
    assert checked.returncode == 0, checked.stderr
    tab = win.open_flow(path)
    seen = {}
    for block in chosen:
        select_only(tab, block)
        seen[block] = keep_runs_shown(win)

    assert seen == {
        "manual": {"choice": "As in Settings", "failed": True},
        "manual_2": {"choice": "All of them", "failed": False},
        "nightly": {"choice": "The last N", "number": 3, "failed": False},
        "check": {"choice": "For a time", "number": 18, "unit": "months", "failed": True},
    }


# --- the user's case: an every-minute schedule keeps its last N runs; the manual runs follow Settings ------------

def test_an_every_minute_schedule_keeping_the_last_3_deletes_its_older_runs_entirely_and_keeps_the_manual_run(env, win):
    flow = made_in_editor(win, env.root / "flow.yaml", flow_yaml({"manual": "manual", "check": "* * * * *"}),
                          {"check": ("The last N", 3)})

    for args in (["--trigger", "check"], ["--trigger", "check"], [], ["--trigger", "check"],
                 ["--trigger", "check"], ["--trigger", "check"]):
        run_ok(env, flow, *args)

    left = [3, 4, 5, 6]  # check's runs 1 and 2 go: their history record, block results, logs and folder
    assert kept(env) == {"runs": left, "blocks": left, "logs": left, "folders": left}
    assert started_by(env) == [(3, "manual"), (4, "check"), (5, "check"), (6, "check")]


# --- 1 and 2. For a time: each start block's own time, longer or shorter than Settings; As in Settings follows it --

def test_for_a_time_keeps_each_start_blocks_runs_for_its_own_time_and_as_in_settings_follows_keep_runs_for(env, win):
    env.write("home/settings.yaml", "keep_runs_for: 10 days\n")
    flow = made_in_editor(win, env.root / "flow.yaml", flow_yaml(
        {"manual": "manual", "daily": "0 6 * * *", "monthly": "0 6 1 * *", "yearly": "0 6 1 1 *"}),
        {"daily": ("For a time", 30, "days"), "monthly": ("For a time", 2, "months"),
         "yearly": ("For a time", 1, "years")})
    ages = {}  # run -> days ago
    for start, older, newer in (("manual", 20, 5), ("daily", 40, 20), ("monthly", 70, 50), ("yearly", 380, 350)):
        for days in (older, newer):
            run_ok(env, flow, *([] if start == "manual" else ["--trigger", start]))
            ages[env.last_run()[0]] = days
    for run_id, days in ages.items():
        started_ago(env, run_id, days)

    run_ok(env, flow)  # run 9

    assert kept(env) == {part: [2, 4, 6, 8, 9] for part in ("runs", "blocks", "logs", "folders")}


# --- 4 and 2. All of them is never cut by Settings; flows without start blocks and blocks on no line follow it --

def test_all_of_them_keeps_runs_older_than_settings_and_what_counts_for_no_start_block_follows_settings(env, win):
    kept_all = made_in_editor(win, env.root / "kept.yaml", f"""
blocks:
  manual: {{type: trigger.manual}}
  note: {{type: logic.python, config: {{inputs: [], code: "{NOTE}"}}}}
  spare: {{type: logic.python, config: {{inputs: [], code: "{NOTE}"}}}}
edges:
  - manual.start -> note.after
""", {"manual": ("All of them",)})
    plain = write(env, f"""
        blocks:
          note: {{type: logic.python, config: {{inputs: [], code: "{NOTE}"}}}}
    """, "plain.yaml")
    run_ok(env, kept_all)  # 1: counts for manual
    run_ok(env, kept_all, "--only", "spare")  # 2, 3: spare is on no start block's line
    run_ok(env, kept_all, "--only", "spare")
    run_ok(env, plain)  # 4, 5: a flow without start blocks
    run_ok(env, plain)
    for run_id, years in ((1, 10), (2, 10), (3, 1), (4, 10), (5, 1)):
        started_ago(env, run_id, years * YEAR)

    run_ok(env, kept_all)  # 6
    assert kept(env)["runs"] == [1, 3, 5, 6]

    run_ok(env, plain)  # 7: as a run of another flow ends, Settings still does not cut manual's runs
    every = [1, 3, 5, 6, 7]
    assert kept(env) == {"runs": every, "blocks": every, "logs": every, "folders": every}


# --- 2. a run started by two start blocks is deleted only when both let it go ------------------------------------

def test_a_run_started_by_two_start_blocks_is_deleted_only_when_both_let_it_go(env, win):
    flow = made_in_editor(win, env.root / "flow.yaml", flow_yaml({"manual": "manual", "manual_2": "manual"}),
                          {"manual": ("The last N", 1), "manual_2": ("For a time", 30, "days")})
    for _ in range(3):
        run_ok(env, flow)
    assert [by for _, by in started_by(env)] == ["manual, manual_2"] * 3
    started_ago(env, 1, 40)  # manual_2 lets it go too
    started_ago(env, 2, 10)  # manual_2 keeps it, though manual lets it go

    run_ok(env, flow)  # run 4

    assert kept(env) == {part: [2, 3, 4] for part in ("runs", "blocks", "logs", "folders")}


# --- 2. a re-run of a block (only X / from X) counts for the start blocks whose lines hold X -----------------------

TWO_LINES = """
blocks:
  manual: {type: trigger.manual}
  check: {type: trigger.schedule, config: {cron: "* * * * *"}}
  load: {type: logic.python, config: {inputs: [], outputs: [], code: "print('load')"}}
  save: {type: logic.python, config: {inputs: [], outputs: [], code: "print('save')"}}
  probe: {type: logic.python, config: {inputs: [], outputs: [], code: "print('probe')"}}
  report: {type: logic.python, config: {inputs: [], outputs: [], code: "print('report')"}}
edges:
  - manual.start -> load.after
  - load.done -> save.after
  - save.done -> report.after
  - check.start -> probe.after
  - probe.done -> report.after
"""  # manual's line: load, save, report; check's line: probe, report


def test_a_rerun_of_a_block_counts_for_the_start_blocks_whose_lines_hold_it(env, win):
    flow = made_in_editor(win, env.root / "flow.yaml", TWO_LINES,
                          {"manual": ("All of them",), "check": ("The last N", 1)})
    run_ok(env, flow)  # 1: manual
    run_ok(env, flow, "--trigger", "check")  # 2: check
    run_ok(env, flow, "--only", "probe")  # 3: check's line
    run_ok(env, flow, "--only", "save")  # 4: manual's line
    run_ok(env, flow, "--from", "load", "--inputs-from", 1)  # 5: manual's line
    run_ok(env, flow, "--only", "report")  # 6: both lines
    for run_id in (1, 4, 5, 6):
        started_ago(env, run_id, 10 * YEAR)  # older than Settings keeps: only manual's All of them keeps them

    run_ok(env, flow, "--trigger", "check")  # 7

    # 2 and 3 count for check alone, which keeps its last run only; the re-runs of manual's blocks stay.
    assert kept(env) == {part: [1, 4, 5, 6, 7] for part in ("runs", "blocks", "logs", "folders")}


def test_a_rerun_of_a_block_in_two_lines_is_kept_while_either_start_block_keeps_it(env, win):
    flow = made_in_editor(win, env.root / "flow.yaml", TWO_LINES,
                          {"manual": ("The last N", 1), "check": ("The last N", 2)})
    run_ok(env, flow, "--only", "report")  # 1: counts for manual and check
    run_ok(env, flow)  # 2: manual's last run
    run_ok(env, flow, "--trigger", "check")  # 3
    assert kept(env)["runs"] == [1, 2, 3]  # manual lets 1 go, check keeps it (one of its last 2)

    run_ok(env, flow, "--trigger", "check")  # 4

    assert kept(env) == {part: [2, 3, 4] for part in ("runs", "blocks", "logs", "folders")}


# --- 1. Always keep failed runs ------------------------------------------------------------------------------------

def test_always_keep_failed_runs_keeps_a_failed_run_the_last_n_lets_go_and_unticked_it_goes(env, win):
    code = "import os\nprint('a line in the log')\nif os.path.exists('fail.txt'):\n    raise ValueError('asked to')"
    flow = made_in_editor(win, env.root / "flow.yaml", flow_yaml({"check": "* * * * *", "nightly": "0 2 * * *"}, code),
                          {"check": ("The last N", 1, None, True), "nightly": ("The last N", 1)})
    (env.root / "fail.txt").write_text("x", encoding="utf-8")
    for start in ("check", "nightly"):  # 1, 2: failed
        env.taskloom("run", flow, "--trigger", start)
        assert env.last_run()[1] == "failed"
    (env.root / "fail.txt").unlink()

    for start in ("check", "nightly", "check", "nightly"):  # 3 to 6
        run_ok(env, flow, "--trigger", start)

    assert env.query("SELECT id, started_by, status FROM runs ORDER BY id") == [
        (1, "check", "failed"), (5, "check", "success"), (6, "nightly", "success")]
    assert kept(env)["folders"] == [1, 5, 6]


# --- 3. a lower choice applies to the earlier runs too, from the next time a run ends -----------------------------

def test_lowering_the_last_n_deletes_the_older_runs_the_next_time_a_run_ends(env, win):
    flow = made_in_editor(win, env.root / "flow.yaml", flow_yaml({"check": "* * * * *"}), {"check": ("The last N", 5)})
    for _ in range(4):
        run_ok(env, flow, "--trigger", "check")

    choose_in_editor(win, flow, {"check": ("The last N", 2)})
    assert kept(env)["runs"] == [1, 2, 3, 4]  # nothing goes until a run ends

    run_ok(env, flow, "--trigger", "check")  # 5
    assert kept(env) == {part: [4, 5] for part in ("runs", "blocks", "logs", "folders")}


# --- 3 and 4. a choice changed to keep more holds for the earlier runs, though another start block runs next -------

def test_a_choice_changed_to_keep_more_is_honoured_when_another_start_block_of_the_flow_runs_next(env, win):
    env.write("home/settings.yaml", "keep_runs_for: 10 days\n")
    flow = made_in_editor(win, env.root / "flow.yaml", flow_yaml({"manual": "manual", "check": "* * * * *"}), {})
    run_ok(env, flow)  # 1: manual, As in Settings
    run_ok(env, flow, "--trigger", "check")  # 2
    started_ago(env, 1, 20)  # older than Settings keeps

    choose_in_editor(win, flow, {"manual": ("All of them",)})
    run_ok(env, flow, "--trigger", "check")  # 3: check runs next, not manual

    assert started_by(env) == [(1, "manual"), (2, "check"), (3, "check")]
    assert kept(env) == {part: [1, 2, 3] for part in ("runs", "blocks", "logs", "folders")}


# --- 3. a run still going is never deleted; a newer run that reused an old run's tables keeps them ---------------

def block_status(env, run_id: int, block: str) -> list:
    try:
        return env.query("SELECT status FROM blocks WHERE run_id=? AND block=?", run_id, block)
    except sqlite3.OperationalError:  # the run has not made the history yet
        return []


def test_a_run_still_going_is_kept_though_the_last_n_lets_it_go(env, win):
    flow = made_in_editor(win, env.root / "flow.yaml", """
params:
  pause: {type: float, value: 0}
blocks:
  check: {type: trigger.schedule, config: {cron: "* * * * *"}}
  load: {type: logic.python, config: {inputs: [], code: "print('loaded'); output = pl.DataFrame({'n': [1, 2, 3]})"}}
  hold: {type: logic.python, config: {inputs: [t], outputs: [], code: "import time; time.sleep(params['pause'])"}}
edges:
  - check.start -> load.after
  - load.output -> hold.t
""", {"check": ("The last N", 1)})
    going = env.popen("run", flow, "--trigger", "check", "--param", "pause=120")
    try:
        deadline = time.monotonic() + 60
        while block_status(env, 1, "hold") != [("running",)]:
            assert going.poll() is None and time.monotonic() < deadline, "the slow run never got to its pause"
            time.sleep(0.05)

        run_ok(env, flow, "--trigger", "check")  # 2
        run_ok(env, flow, "--trigger", "check")  # 3

        assert env.query("SELECT id, status FROM runs ORDER BY id") == [(1, "running"), (3, "success")]
        assert kept(env)["folders"] == [1, 3]
        assert list((env.home / "runs" / "1" / "load").rglob("*.parquet"))  # its files, too
    finally:
        going.kill()
        going.communicate(timeout=30)


def test_a_rerun_from_a_run_the_last_n_deleted_still_has_its_tables(env, win):
    flow = made_in_editor(win, env.root / "flow.yaml", """
blocks:
  check: {type: trigger.schedule, config: {cron: "* * * * *"}}
  load: {type: logic.python, config: {inputs: [], code: "output = pl.DataFrame({'n': [1, 2, 3]})"}}
  save: {type: logic.python, config: {inputs: [t], outputs: [], code: "open('rows.txt', 'a').write(str(len(t)))"}}
edges:
  - check.start -> load.after
  - load.output -> save.t
""", {"check": ("The last N", 2)})
    run_ok(env, flow, "--trigger", "check")  # 1
    run_ok(env, flow, "--only", "save", "--inputs-from", 1)  # 2 reuses run 1's table
    run_ok(env, flow, "--trigger", "check")  # 3: run 1 is no longer one of the last 2
    assert kept(env)["runs"] == [2, 3] and kept(env)["folders"] == [2, 3]

    run_ok(env, flow, "--only", "save", "--inputs-from", 2)

    assert (env.root / "rows.txt").read_text(encoding="utf-8") == "3333"


# --- 4. one flow's choices never delete another flow's runs ---------------------------------------------------------

def test_one_flows_last_n_never_deletes_the_runs_of_another_flows_start_block_of_the_same_name(env, win):
    cut = made_in_editor(win, env.root / "cut.yaml", flow_yaml({"check": "* * * * *"}), {"check": ("The last N", 1)})
    other = made_in_editor(win, env.root / "other.yaml", flow_yaml({"check": "* * * * *"}),
                           {"check": ("The last N", 2)})

    for flow in (cut, other, cut, other, cut):  # cut: 1, 3, 5; other: 2, 4
        run_ok(env, flow, "--trigger", "check")

    assert kept(env) == {part: [2, 4, 5] for part in ("runs", "blocks", "logs", "folders")}


# --- 5. a choice that cannot be read is refused, naming the block and Keep runs -------------------------------------

def swapped(value, pattern: str, new):
    """`value` (a saved flow) with each number or word matching `pattern` replaced by `new` (not in the blocks'
    places on the canvas)."""
    if isinstance(value, dict):
        return {k: v if k == "ui" else swapped(v, pattern, new) for k, v in value.items()}
    if isinstance(value, list):
        return [swapped(v, pattern, new) for v in value]
    if isinstance(value, int) and not isinstance(value, bool):
        return new if re.fullmatch(pattern, str(value)) else value
    if isinstance(value, str):
        return re.sub(rf"(?<![\w.-]){pattern}(?!\w)", str(new), value)
    return value


@pytest.mark.parametrize("block, keep, pattern, new", [
    ("by_hand", ("The last N", 37), "37", 0),
    ("nightly", ("For a time", 37, "months"), "37", -37),
    ("nightly", ("For a time", 37, "months"), "months?", "fortnights"),
], ids=["last 0", "a negative number", "an unknown unit"])
def test_a_keep_runs_that_cannot_be_read_is_refused_by_validate_and_run_naming_the_block_and_setting(
        env, win, block, keep, pattern, new):
    flow = made_in_editor(win, env.root / "flow.yaml", flow_yaml(
        {"by_hand": "manual", "nightly": "0 2 * * *"}, "open('ran.txt', 'w').write('x')"), {block: keep})
    saved = yaml.safe_load(flow.read_text(encoding="utf-8"))
    broken = swapped(saved, pattern, new)
    assert broken != saved, f"the saved flow does not hold {keep}: {saved}"
    flow.write_text(yaml.safe_dump(broken, sort_keys=False, allow_unicode=True), encoding="utf-8")

    for command in (["validate", flow], ["run", flow, *([] if block == "by_hand" else ["--trigger", block])]):
        result = env.taskloom(*command)
        assert result.returncode == 2, (command, result.stdout, result.stderr)
        assert block in result.stderr and "Keep runs" in result.stderr, (command, result.stderr)
        assert "Traceback" not in result.stderr, (command, result.stderr)
    assert not (env.root / "ran.txt").exists() and runs_recorded(env) == 0


# --- 6. README ------------------------------------------------------------------------------------------------------

def test_the_readme_describes_keep_runs_of_a_start_block_and_how_it_goes_with_settings():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    about = " ".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", readme) if re.search(r"Keep runs\b(?! for)", p))

    assert about, "the README does not mention Keep runs"
    for text in [*OPTIONS, FAILED, "keep_runs_for"]:
        assert text in about, text
