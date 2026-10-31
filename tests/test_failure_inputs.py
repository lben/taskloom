"""A block that fails in a run of only that block (`taskloom run --only`, Run only this block) or of it and the blocks
after it (`--from`, Run from this block on) says in its failure report what each input received: every "Inputs:" line
shows the value that came (from the earlier run, or typed), in the same short form as in a full run, and
"(nothing arrived)" only for an input that received nothing. Through `taskloom run` and the editor, reading the
report the run history keeps (what the Log shows)."""

import os

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from taskloom.history import History  # noqa: E402
from tests.test_lines import menu_action, wait_runs  # noqa: E402

NOTHING = "(nothing arrived)"
TOKEN = "s3cr3t-token-value"


def python(code, inputs=(), outputs=("output",)) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code}}


def failing(*inputs) -> dict:
    return python("raise ValueError('the totals do not match')", inputs)


def write_flow(path, blocks, edges):
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": list(edges)}, sort_keys=False), encoding="utf-8")
    return path


def run(env, flow, *args) -> int:
    """Runs the flow (with `args`); the id of its run, which failed."""
    result = env.taskloom("run", flow, *args)
    run_id, status = env.last_run()
    assert status == "failed" and result.returncode == 1, result.stdout + result.stderr
    return run_id


def inputs_reported(home, run_id, block) -> list[str]:
    """The "Inputs:" lines of the failure report `block` logged in run `run_id`, as the run history of the Taskloom
    folder `home` keeps them."""
    history = History(home / "history.db")
    try:
        logs = history.logs(run_id)
    finally:
        history.close()
    reports = [log["message"] for log in logs if log["block"] == block and log["level"] == "ERROR"
               and log["message"].startswith(f"{block} (") and " failed: " in log["message"].splitlines()[0]]
    assert len(reports) == 1, logs
    lines = reports[0].splitlines()
    assert "Inputs:" in lines, reports[0]
    found = []
    for line in lines[lines.index("Inputs:") + 1:]:
        if not (line.startswith("  ") and " ← " in line):
            break
        found.append(line)
    return found


# The user's report: a Python Code block with several query results as inputs, run again from the earlier run.
QUERIES = {
    "manual": {"type": "trigger.manual"},
    "q1_query": python("import polars as pl\nresult = pl.DataFrame({'region': ['north', 'south'], 'amount': [10, 20]})",
                       outputs=("result",)),
    "q2_query": python("result = ['north', 'south']", outputs=("result",)),
    "q3_query": python("result = 42", outputs=("result",)),
    "q4_query": python("result = 'ready'", outputs=("result",)),
    "combine": failing("first", "second", "third", "fourth"),
    "report": python("print(input)", ("input",), ()),
}
QUERY_EDGES = [*(f"manual.start -> q{n}_query.after" for n in range(1, 5)),
               "q1_query.result -> combine.first", "q2_query.result -> combine.second",
               "q3_query.result -> combine.third", "q4_query.result -> combine.fourth",
               "combine.output -> report.input"]


@pytest.mark.parametrize("mode", ["--only", "--from"])
def test_a_rerun_of_the_failed_block_reports_the_inputs_it_got_from_the_earlier_run(env, mode):
    """Spec 1 (the user's report): each Inputs: line of the block the re-run starts at shows the value it received from
    the earlier run, as the full run showed it; also when the earlier run was itself such a re-run."""
    flow = write_flow(env.root / "flow.yaml", QUERIES, QUERY_EDGES)
    full = run(env, flow)
    in_full = inputs_reported(env.home, full, "combine")
    assert in_full[0].startswith("  first ← q1_query.result: 2 rows × 2 columns"), in_full
    assert in_full[2:] == ["  third ← q3_query.result: 42", "  fourth ← q4_query.result: ready"], in_full
    assert len(in_full) == 4 and not any(NOTHING in line for line in in_full), in_full

    again = run(env, flow, mode, "combine", "--inputs-from", full)
    once_more = run(env, flow, mode, "combine", "--inputs-from", again)  # the inputs kept from that re-run

    assert inputs_reported(env.home, again, "combine") == in_full
    assert inputs_reported(env.home, once_more, "combine") == in_full


@pytest.mark.parametrize("mode", ["--only", "--from"])
def test_typed_inputs_show_what_was_typed_one_left_empty_got_nothing_and_none_is_a_value(env, mode):
    """Spec 1 (typed values) and 3: a typed input shows the typed value; one left empty received nothing; one whose
    value from the earlier run is None did receive a value."""
    flow = write_flow(env.root / "flow.yaml", {
        "a": python("output = None"),
        "b": python("output = 'b'"),
        "c": python("output = 'c'"),
        "use": failing("x", "y", "z"),
    }, ["a.output -> use.x", "b.output -> use.y", "c.output -> use.z"])
    full = run(env, flow)
    assert inputs_reported(env.home, full, "use") == ["  x ← a.output: None", "  y ← b.output: b",
                                                      "  z ← c.output: c"]

    chosen = run(env, flow, mode, "use", "--inputs-from", full, "--input", "y='typed text'", "--input", "z=")

    assert inputs_reported(env.home, chosen, "use") == [
        "  x ← a.output: None", "  y ← b.output: typed text", f"  z ← c.output: {NOTHING}"]


def test_a_later_block_of_a_run_from_on_reports_the_values_of_the_blocks_before_the_start(env):
    """Spec 2: a block after the start fails; its inputs from blocks before the start (not run again, their values
    taken from the earlier run) and from the start block (run again) show what it received."""
    flow = write_flow(env.root / "flow.yaml", {
        "manual": {"type": "trigger.manual"},
        "load": python("output = 'loaded rows'"),
        "count": python("output = 3"),
        "prep": python("output = rows.upper()", ("rows",)),
        "final": failing("rows", "n", "prepared"),
    }, ["manual.start -> load.after", "manual.start -> count.after", "load.output -> prep.rows",
        "load.output -> final.rows", "count.output -> final.n", "prep.output -> final.prepared"])
    full = run(env, flow)
    in_full = inputs_reported(env.home, full, "final")
    assert in_full == ["  rows ← load.output: loaded rows", "  n ← count.output: 3",
                       "  prepared ← prep.output: LOADED ROWS"]

    again = run(env, flow, "--from", "prep", "--inputs-from", full)

    assert env.block_status(again)["prep"] == "success"
    assert inputs_reported(env.home, again, "final") == in_full


def test_several_connections_into_one_input_each_show_their_own_value(env):
    """Spec 4: a Python Code input with several connections receives a dict keyed by source (block.port when one
    block connects two outputs); each connection's line shows that connection's value, as in the full run."""
    flow = write_flow(env.root / "flow.yaml", {
        "load": python("output = 'loaded'"),
        "check": python("result = 'fine'\nextra = 7", outputs=("result", "extra")),
        "combine": failing("input"),
    }, ["load.output -> combine.input", "check.result -> combine.input", "check.extra -> combine.input"])
    full = run(env, flow)
    in_full = inputs_reported(env.home, full, "combine")
    assert in_full == ["  input ← load.output: loaded", "  input ← check.result: fine",
                       "  input ← check.extra: 7"]

    again = run(env, flow, "--from", "combine", "--inputs-from", full)

    assert inputs_reported(env.home, again, "combine") == in_full


def test_a_connection_that_delivered_nothing_says_so_and_the_one_that_delivered_shows_its_value(env):
    """Spec 3 and 4: after an If, only the path taken reached the input; the other connection received nothing."""
    flow = write_flow(env.root / "flow.yaml", {
        "rows": python("output = [1, 2, 3]"),
        "route": {"type": "logic.if", "config": {"condition": "len(value) > 5"}},
        "many": python("output = 'many'", ("input",)),
        "few": python("output = 'few'", ("input",)),
        "merge": failing("input"),
    }, ["rows.output -> route.value", "route.true -> many.input", "route.false -> few.input",
        "many.output -> merge.input", "few.output -> merge.input"])
    full = run(env, flow)
    in_full = inputs_reported(env.home, full, "merge")
    assert in_full == [f"  input ← many.output: {NOTHING}", "  input ← few.output: few"]

    again = run(env, flow, "--only", "merge", "--inputs-from", full)

    assert inputs_reported(env.home, again, "merge") == in_full


@pytest.mark.parametrize("mode", ["--only", "--from"])
@pytest.mark.parametrize("block", ["check", "stop"])
def test_paths_after_an_if_meeting_in_an_input_that_takes_any_one_show_which_came(env, mode, block):
    """Spec 3 and 4 for an input that takes any one of its connections and is not keyed by source (Inspect's value,
    a run-after): after an If, the path taken shows its value and the other says nothing arrived, as in the full
    run."""
    flow = write_flow(env.root / "flow.yaml", {
        "rows": python("output = [1, 2, 3]"),
        "route": {"type": "logic.if", "config": {"condition": "len(value) > 5"}},
        "many": python("output = 'many'", ("input",)),
        "few": python("output = 'few'", ("input",)),
        "check": {"type": "logic.inspect", "config": {"message": "{value['missing']}"}},
        "stop": failing(),
    }, ["rows.output -> route.value", "route.true -> many.input", "route.false -> few.input",
        "many.output -> check.value", "few.output -> check.value",
        "many.output -> stop.after", "few.output -> stop.after"])
    full = run(env, flow)
    in_full = inputs_reported(env.home, full, block)
    port = "value" if block == "check" else "after"
    assert in_full == [f"  {port} ← many.output: {NOTHING}", f"  {port} ← few.output: few"], in_full

    again = run(env, flow, mode, block, "--inputs-from", full)

    assert inputs_reported(env.home, again, block) == in_full


def test_a_password_in_an_input_of_a_rerun_block_stays_masked_in_its_report(env):
    """Spec 5: the value shown for an input holding a password the block reads shows *** in its place, and the
    password appears nowhere in the run's log or output."""
    env.write("home/secrets.yaml", f"report_token: {TOKEN}\n").chmod(0o600)
    flow = write_flow(env.root / "flow.yaml", {
        "load": python(f"output = 'the token is {TOKEN}'"),
        "check": python("token = ctx.home.get_secret('report_token')\nraise ValueError('the token was refused')",
                        ("text",), ()),
    }, ["load.output -> check.text"])
    full = run(env, flow)
    assert inputs_reported(env.home, full, "check") == ["  text ← load.output: the token is ***"]

    result = env.taskloom("run", flow, "--only", "check", "--inputs-from", full)
    again, status = env.last_run()

    assert status == "failed" and again != full, result.stdout + result.stderr
    assert inputs_reported(env.home, again, "check") == ["  text ← load.output: the token is ***"]
    messages = [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=?", again)]
    assert not any(TOKEN in m for m in messages) and TOKEN not in result.stdout + result.stderr


@pytest.mark.parametrize("choice", ["Run only this block, with the same inputs",
                                    "Run from this block on, with the same inputs"])
def test_in_the_editor_a_rerun_with_the_same_inputs_reports_the_inputs_it_got(win, qtbot, tmp_path, choice):
    """Spec 1, the user's steps: the flow runs and its Python Code block fails; right-click it, run it again with the
    same inputs; it fails again and its report shows what each input received, as the first run's did."""
    flow = write_flow(tmp_path / "queries.yaml", QUERIES, QUERY_EDGES)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    full = wait_runs(qtbot, tab, 1)
    in_full = inputs_reported(win.home.root, full, "combine")
    assert len(in_full) == 4 and not any(NOTHING in line for line in in_full), in_full

    menu_action(win, tab, "combine", choice).trigger()
    again = wait_runs(qtbot, tab, 2)

    assert tab.runs[-1].status == "failed" and again != full
    assert inputs_reported(win.home.root, again, "combine") == in_full
    assert NOTHING not in win.log.text.toPlainText(), win.log.text.toPlainText()

