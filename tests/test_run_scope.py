"""A run refuses to start only for problems in blocks that take part in it. A problem in a block that does not take
part in that run (an off block, or a block only another start block's line reaches) lets it go ahead, and its Log warns
about it; Validate, `taskloom validate` and `taskloom check` still list every problem (an off block's as a warning
that says it is off). Through the real `taskloom` command and its history, the scheduler and the editor."""

import datetime as dt
import os
import re

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from taskloom.config import Home  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.scheduler import Scheduler  # noqa: E402

# The user's flow: Manual Start -> a -> b, and Schedule -> c -> an If with no condition.
USER_FLOW = {
    "blocks": {
        "manual": {"type": "trigger.manual"},
        "a": {"type": "logic.python", "config": {"inputs": [], "code": "output = 21"}},
        "b": {"type": "logic.python",
              "config": {"inputs": ["input"], "outputs": [], "code": "open('b.txt', 'w').write(str(input * 2))"}},
        "nightly": {"type": "trigger.schedule", "config": {"cron": "0 2 * * *"}},
        "c": {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}},
        "branch": {"type": "logic.if", "config": {}},  # no condition
    },
    "edges": ["manual.start -> a.after", "a.output -> b.input", "nightly.start -> c.after", "c.output -> branch.value"],
}
NO_CONDITION = "branch: Condition is required"
# A block connected to nothing (off), with a required input that is not connected.
SPARE = {"type": "data.polars", "config": {"code": "df"}}
SPARE_PROBLEM = "required input 'table' is not connected"
OFF = "off · not connected to a start block"  # the canvas label

# The Log says the block does not take part in this run (in whatever words).
TAKES_NO_PART = re.compile(r"\b(?:does not|doesn't|do not|don't|not)\s+tak(?:e|es|ing)\s+part\b"
                           r"|\btak(?:e|es|ing)\s+no\s+part\b", re.I)
OFF_WORD = re.compile(r"\boff\b", re.I)


# --- helpers ---------------------------------------------------------------------------

class _Plain(yaml.SafeDumper):
    def ignore_aliases(self, data):  # a block used twice is written out twice, as you would
        return True


def write_flow(path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, Dumper=_Plain, sort_keys=False), encoding="utf-8")
    return path


def with_blocks(flow: dict, blocks: dict = (), edges=(), **top) -> dict:
    return {**top, "blocks": {**flow["blocks"], **dict(blocks)}, "edges": [*flow["edges"], *edges]}


def names(text: str, block: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(block)}(?!\w)", text) is not None


def runs_of(home) -> list[dict]:
    """The runs recorded in the history, oldest first."""
    history = History(home / "history.db")
    try:
        return list(reversed(history.runs()))
    finally:
        history.close()


def run_warnings(home, run_id: int) -> list[str]:
    """The WARNING lines of a run's Log, as its history keeps them ([block] message)."""
    history = History(home / "history.db")
    try:
        return [f"[{e['block']}] {e['message']}" if e["block"] else e["message"]
                for e in history.logs(run_id) if e["level"] == "WARNING"]
    finally:
        history.close()


def warns_about(lines, block: str, problem: str) -> bool:
    """One of the lines names `block` and `problem`, and says the block does not take part in this run."""
    return any(names(line, block) and problem in line and TAKES_NO_PART.search(line) for line in lines)


def log_lines(win, level: str) -> list[str]:
    """The editor Log's lines at `level` (HH:MM:SS [block] LEVEL message)."""
    return [line for line in win.log.text.toPlainText().splitlines() if re.search(rf"\b{level}\b", line)]


def wait_runs(qtbot, tab, n):
    qtbot.waitUntil(lambda: len(tab.runs) == n and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)


def no_message_boxes(win):
    boxes = list(win.message_boxes)
    win.message_boxes.clear()  # (said here, not again as the window closes)
    assert boxes == []


def menu_action(win, tab, block, starts_with):
    return next(a for a in win._block_menu_for(tab, block).actions() if a.text().startswith(starts_with))


# --- the user's flow, through the command line ------------------------------------------

def test_the_manual_line_runs_and_warns_while_the_schedule_line_is_refused(env):
    """Spec 6 (and 1, 2): Run (`taskloom run`, no --trigger) runs the Manual Start line, succeeds and warns about the
    If the Schedule's line reaches; a run started by the Schedule is refused with "Condition is required"."""
    flow = write_flow(env.root / "flow.yaml", USER_FLOW)

    manual = env.taskloom("run", flow)

    assert manual.returncode == 0, manual.stderr
    [run] = runs_of(env.home)
    assert run["status"] == "success"
    assert env.block_status(run["id"]) == {"manual": "success", "a": "success", "b": "success"}
    assert (env.root / "b.txt").read_text(encoding="utf-8") == "42"
    warnings = run_warnings(env.home, run["id"])
    assert warns_about(warnings, "branch", "Condition is required"), warnings
    assert any("WARNING" in line and "Condition is required" in line for line in manual.stderr.splitlines()), \
        manual.stderr

    scheduled = env.taskloom("run", flow, "--trigger", "nightly")

    assert scheduled.returncode == 2 and NO_CONDITION in scheduled.stderr, scheduled.stderr
    assert len(runs_of(env.home)) == 1  # refused before a run is recorded, as today


def test_run_only_and_run_from_a_block_of_the_manual_line_go_ahead(env):
    """Spec 1, 2, 6: `--only` and `--from` on blocks of the Manual Start line go ahead (and warn about the If);
    `--from` a block whose blocks after it include the If is refused."""
    with_condition = with_blocks(USER_FLOW, {"branch": {"type": "logic.if", "config": {"condition": "True"}}})
    flow = write_flow(env.root / "flow.yaml", with_condition)
    first = env.taskloom("run", flow)
    assert first.returncode == 0, first.stderr
    earlier = runs_of(env.home)[-1]["id"]
    write_flow(flow, USER_FLOW)  # the If loses its condition

    for args, ran in ((["--only", "b"], {"b"}), (["--from", "a"], {"a", "b"})):
        result = env.taskloom("run", flow, *args, "--inputs-from", earlier)

        assert result.returncode == 0, (args, result.stderr)
        run = runs_of(env.home)[-1]
        assert run["status"] == "success" and set(env.block_status(run["id"])) == ran
        warnings = run_warnings(env.home, run["id"])
        assert warns_about(warnings, "branch", "Condition is required"), (args, warnings)

    refused = env.taskloom("run", flow, "--from", "c")  # c and the If after it take part

    assert refused.returncode == 2 and NO_CONDITION in refused.stderr, refused.stderr
    assert len(runs_of(env.home)) == 3


# --- every kind of block problem, in an off block ------------------------------------------

REPORT = {"type": "logic.python",
          "config": {"inputs": [], "outputs": [], "code": "open('report.txt', 'w').write('done')"}}
MANUAL_REPORT = {"blocks": {"manual": {"type": "trigger.manual"}, "report": REPORT},
                 "edges": ["manual.start -> report.after"]}
LOOP = {"type": "logic.python", "config": {"inputs": ["x"], "code": "output = x"}}
SSH_CONNECTION = "files:\n  kind: ssh\n  host: localhost\n"


@pytest.mark.parametrize("blocks, edges, home_files, block, problem", [
    ({"spare": {"type": "logic.if"}}, [], {}, "spare", "Condition is required"),
    ({"spare": {"type": "logic.wait", "config": {"seconds": [1, 2]}}}, [], {}, "spare", "Seconds: expected a number"),
    ({"spare": {"type": "reports.html_template", "config": {"template": "{nothing}"}}}, [], {}, "spare",
     "Template: {nothing} is not an input or a flow parameter"),
    ({"spare": {"type": "logic.ask_user", "config": {"prompt": "Which {region}?"}}}, [], {}, "spare",
     "Prompt: unknown parameter {region}"),
    ({"spare": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": "select :day"}}}, [], {}, "spare",
     "SQL: unknown bind parameter :day"),
    ({"spare": {"type": "db.run_query", "config": {"connection": "nowhere", "sql": "select 1"}}}, [], {}, "spare",
     "no connection named 'nowhere'"),
    ({"spare": {"type": "db.run_query", "config": {"connection": "files", "sql": "select 1"}}}, [],
     {"connections.yaml": SSH_CONNECTION}, "spare", "connection 'files' is ssh, expected jdbc"),
    ({"spare": {"type": "logic.nothing_like_this"}}, [], {}, "spare", "unknown block type 'logic.nothing_like_this'"),
    ({"spare": SPARE}, [], {}, "spare", SPARE_PROBLEM),
    ({"loop_a": LOOP, "loop_b": LOOP}, ["loop_a.output -> loop_b.x", "loop_b.output -> loop_a.x"], {}, "loop_a",
     "cycle through: loop_a, loop_b"),
], ids=["empty required setting", "setting of the wrong kind", "block type's own check", "unknown parameter",
        "unknown SQL bind name", "unknown connection", "connection of the wrong kind", "unknown block type",
        "required input not connected", "loop among off blocks"])
def test_a_problem_in_an_off_block_does_not_stop_the_run(env, blocks, edges, home_files, block, problem):
    """Spec 2, 3 (and 7 for the unconnected required input): the run goes ahead and its Log warns, naming the
    block, the problem, and that the block does not take part in this run."""
    for name, text in home_files.items():
        (env.home / name).write_text(text, encoding="utf-8")
    flow = write_flow(env.root / "flow.yaml", with_blocks(MANUAL_REPORT, blocks, edges))

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    [run] = runs_of(env.home)
    assert run["status"] == "success" and (env.root / "report.txt").read_text(encoding="utf-8") == "done"
    warnings = run_warnings(env.home, run["id"])
    assert warns_about(warnings, block, problem), warnings
    assert any("WARNING" in line and problem in line for line in result.stderr.splitlines()), result.stderr


@pytest.mark.parametrize("blocks, edges, params, settings, message", [
    ({"spare": {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}}}, ["spare.output -> ghost.input"],
     None, None, "edge spare.output -> ghost.input: unknown block"),
    ({"a": LOOP, "b": LOOP}, ["manual.start -> a.after", "a.output -> b.x", "b.output -> a.x"], None, None,
     "the flow has a cycle through: a, b"),
    ({}, [], {"n": {"type": "int", "expr": "1 +"}}, None, "parameter 'n': invalid expression '1 +'"),
    ({}, [], None, "keep_runs_for: soon\n", "keep_runs_for must be"),
], ids=["connection from an off block to a missing block", "loop through blocks taking part", "parameter problem",
        "keep_runs_for setting"])
def test_problems_that_can_affect_the_run_still_refuse_it(env, blocks, edges, params, settings, message):
    """Spec 4: these stay errors whatever block they are in: the run is refused, with the same message as today."""
    if settings:
        (env.home / "settings.yaml").write_text(settings, encoding="utf-8")
    data = with_blocks(MANUAL_REPORT, blocks, edges, **({"params": params} if params else {}))
    flow = write_flow(env.root / "flow.yaml", data)

    result = env.taskloom("run", flow)

    assert result.returncode == 2 and message in result.stderr, result.stderr
    assert runs_of(env.home) == [] and not (env.root / "report.txt").exists()


# --- the scheduler -----------------------------------------------------------------------

def test_a_scheduled_run_goes_ahead_when_another_schedules_line_has_a_problem(env):
    """Spec 1, 2: the scheduler's run of one Schedule's line goes ahead, and warns about the If only another
    Schedule's line reaches."""
    flow = write_flow(env.root / "flow.yaml", {
        "blocks": {
            "hourly": {"type": "trigger.schedule", "config": {"cron": "0 * * * *"}},
            "report": REPORT,
            "weekly": {"type": "trigger.schedule", "config": {"cron": "0 3 * * SUN"}},
            "branch": {"type": "logic.if", "config": {}},  # no condition
        },
        "edges": ["hourly.start -> report.after", "weekly.start -> branch.after"],
    })
    home = Home(env.home)
    home.set_scheduled(flow, True)
    notes = []
    scheduler = Scheduler(home, lambda title, message: notes.append((title, message)))
    monday = dt.datetime(2026, 1, 5, 6, 30)
    scheduler.tick(monday)  # first sight of the schedules
    scheduler.tick(monday + dt.timedelta(hours=1))  # hourly's 07:00 is due; weekly's Sunday 03:00 is not

    scheduler.wait_for_runs()

    runs = runs_of(env.home)
    assert [(r["status"], r["started_by"]) for r in runs] == [("success", "hourly")], notes
    assert (env.root / "report.txt").read_text(encoding="utf-8") == "done"
    warnings = run_warnings(env.home, runs[0]["id"])
    assert warns_about(warnings, "branch", "Condition is required"), warnings


# --- Validate and check still list every problem ---------------------------------------------

def test_validate_lists_every_problem_an_off_blocks_as_a_warning(env):
    """Spec 5: `taskloom validate` gives the If the Schedule's line reaches as an error, as today, and the off block's
    problem as a warning that says it is off; with only that warning, the flow is OK."""
    flow = write_flow(env.root / "flow.yaml", with_blocks(USER_FLOW, {"spare": SPARE}))

    result = env.taskloom("validate", flow)

    lines = result.stderr.splitlines()
    assert result.returncode == 2 and f"error: {NO_CONDITION}" in lines, result.stderr
    assert any(line.startswith("warning: ") and names(line, "spare") and SPARE_PROBLEM in line and OFF_WORD.search(line)
               for line in lines), result.stderr
    assert not any(line.startswith("error: ") and SPARE_PROBLEM in line for line in lines), result.stderr

    only_off = write_flow(env.root / "off.yaml", with_blocks(MANUAL_REPORT, {"spare": SPARE}))
    result = env.taskloom("validate", only_off)

    assert result.returncode == 0, result.stderr
    assert any(line.startswith("warning: ") and names(line, "spare") and SPARE_PROBLEM in line and OFF_WORD.search(line)
               for line in result.stderr.splitlines()), result.stderr


def test_check_lists_every_problem_and_says_which_block_is_off(env):
    """Spec 5: `taskloom check` (the upgrader) lists the If the Schedule's line reaches as needing you, as today,
    and lists the off block's problem saying the block is off."""
    flow = write_flow(env.root / "flow.yaml", with_blocks(USER_FLOW, {"spare": SPARE}))

    result = env.taskloom("check", flow)

    lines = result.stdout.splitlines()
    assert result.returncode == 2 and f"  needs you: {NO_CONDITION}" in lines, result.stdout + result.stderr
    assert any(names(line, "spare") and SPARE_PROBLEM in line and OFF_WORD.search(line) for line in lines), \
        result.stdout


# --- the editor ------------------------------------------------------------------------------

def test_editor_run_run_only_and_run_from_go_ahead_in_the_users_flow(win, qtbot, tmp_path):
    """Spec 1, 2, 6: in the editor, Run (the Manual Start line) goes ahead, with a WARNING in the Log about the If
    and no ERROR about it; Run only this block and Run from this block on, on its blocks, go ahead too."""
    flow = write_flow(tmp_path / "flow.yaml", USER_FLOW)
    tab = win.open_flow(flow)

    win.run_flow(tab)  # Run now

    no_message_boxes(win)
    wait_runs(qtbot, tab, 1)
    assert tab.runs[-1].status == "success"
    assert {b: tab.scene.nodes[b].status for b in ("a", "b")} == {"a": "success", "b": "success"}
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "42"
    warnings = log_lines(win, "WARNING")
    assert warns_about(warnings, "branch", "Condition is required"), warnings
    assert not [line for line in log_lines(win, "ERROR") if "Condition is required" in line]

    menu_action(win, tab, "b", "Run only this block, with the same inputs").trigger()
    no_message_boxes(win)
    wait_runs(qtbot, tab, 2)
    assert tab.runs[-1].status == "success"

    menu_action(win, tab, "a", "Run from this block on, with the same inputs").trigger()
    no_message_boxes(win)
    wait_runs(qtbot, tab, 3)
    assert tab.runs[-1].status == "success"


def test_disconnecting_a_block_with_a_required_input_switches_it_off_and_the_flow_runs(win, qtbot, tmp_path):
    """Spec 7 (the user's journey): disconnect the block on the canvas, it is off, and Run goes ahead with a
    WARNING about its unconnected required input."""
    from PySide6.QtWidgets import QApplication

    flow = write_flow(tmp_path / "flow.yaml", {
        "blocks": {
            "manual": {"type": "trigger.manual"},
            "load": {"type": "logic.python", "config": {"inputs": [], "code": "output = pl.DataFrame({'n': [1, 2]})"}},
            "shape": SPARE,
        },
        "edges": ["manual.start -> load.after", "load.output -> shape.table"],
    })
    tab = win.open_flow(flow)
    tab.scene.clearSelection()
    (edge,) = [e for e in tab.scene.edge_items if e.text == "load.output -> shape.table"]
    edge.setSelected(True)
    tab.scene.delete_selected()  # Delete
    QApplication.processEvents()
    assert tab.scene.nodes["shape"].status_text == OFF

    win.run_flow(tab)  # Run now (it saves the flow first)

    no_message_boxes(win)
    wait_runs(qtbot, tab, 1)
    assert tab.runs[-1].status == "success" and tab.scene.nodes["load"].status == "success"
    warnings = log_lines(win, "WARNING")
    assert warns_about(warnings, "shape", SPARE_PROBLEM), warnings


def test_editor_validate_lists_every_problem_an_off_blocks_as_a_warning(win, tmp_path):
    """Spec 5: Validate (F7) shows the If the Schedule's line reaches as an error, as today, and the off block's
    problem as a WARNING that says it is off."""
    flow = write_flow(tmp_path / "flow.yaml", with_blocks(USER_FLOW, {"spare": SPARE}))
    tab = win.open_flow(flow)
    assert win.current is tab

    win.validate_action.trigger()  # F7

    boxes = list(win.message_boxes)
    win.message_boxes.clear()
    assert len(boxes) == 1 and NO_CONDITION in boxes[0][1][1], boxes  # "The flow has problems", as today
    assert [line for line in log_lines(win, "ERROR") if NO_CONDITION in line]
    warnings = log_lines(win, "WARNING")
    assert any(names(line, "spare") and SPARE_PROBLEM in line and OFF_WORD.search(line) for line in warnings), warnings
    assert not [line for line in log_lines(win, "ERROR") if SPARE_PROBLEM in line]
