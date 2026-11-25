"""Typed values for inputs (docs/promises/typed-values.yaml, agreed with the maintainer on 2026-11-24), work package 1:
the engine, the flow file and Validate. Rows TV-1 (its conversions), TV-3, TV-6, TV-7 and TV-9, through the real
`taskloom run` (also --only, --from, --inputs-from) and `taskloom validate`, the engine's run_flow and Validate in this
process, the failure report the run history keeps, and the scheduler service starting a run as it does. The canvas,
Properties and the menus (WP2) are tested elsewhere.

The flow file's contract (the editor writes it; a hand edit may too):

    blocks:
      <block>:
        type: ...
        config: {...}
        inputs:                                      # the block's typed values, by the input hole each is for
          <hole>: {type: <one of params.TYPES>, value: <stored as a flow parameter's value is>}

A typed value stands in for one line into that hole: at a run its value is converted to its type as a flow parameter's
value is (params.coerce: "42" as int is the number 42, "a, b" as list is ["a", "b"]), {{name}} in a typed text (or in
each item of a typed list) is filled once like any field, and the block gets it exactly as if another block had sent
it. The value may be stored as the text typed (as the editor stores a flow parameter's value) or as a YAML value of
that type; both are read.

What the tests take as given (the promises leave it to the change):
- the failure report's "Inputs:" has one line for a typed hole, starting with the hole's name after the two spaces
  every such line starts with, and ending with ": " and the value in the short form the other lines show;
- Validate's problem with a typed value (TV-9, a {{name}} of BR-3 in it) is an `error:` line of `taskloom validate`
  naming the block and the hole; a run refused for it is refused before it begins (exit code 2, nothing recorded), with
  the same line; a {{name}} that cannot be filled may instead fail the block at the run, its error naming the hole;
- BR-4's warning for a typed text is a `warning:` line of `taskloom validate` naming the block and the hole and asking
  "did you mean {{name}}".
"""

import datetime as dt
import json
import os
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from taskloom import engine  # noqa: E402
from taskloom.blocks.logic import write_reusable_block  # noqa: E402
from taskloom.config import Home  # noqa: E402
from taskloom.flow import load_flow, validate  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from taskloom.scheduler import Scheduler  # noqa: E402
from tests.test_scheduler_promises import on  # noqa: E402

# Each type: (its value as the editor stores it, the text typed; as a YAML value of that type, written by hand; what
# the block gets).
TYPED = {
    "text": ("Hello, world", "Hello, world", "Hello, world"),
    "int": ("42", 42, 42),
    "float": ("2.5", 2.5, 2.5),
    "bool": ("yes", True, True),
    "date": ("2026-11-24", dt.date(2026, 11, 24), dt.date(2026, 11, 24)),
    "datetime": ("2026-11-24 07:30:00", dt.datetime(2026, 11, 24, 7, 30), dt.datetime(2026, 11, 24, 7, 30)),
    "list": ("a, b, c", ["a", "b", "c"], ["a", "b", "c"]),
}
STORED = {"as typed": 0, "as YAML values": 1}
T0 = dt.datetime(2026, 1, 5, 6, 30)  # the scheduler's clock


# --- helpers ---------------------------------------------------------------------------------------------------------

def flow_file(path: Path, blocks: dict, edges=(), **top) -> Path:
    path.write_text(yaml.safe_dump({**top, "blocks": blocks, "edges": list(edges)}, sort_keys=False),
                    encoding="utf-8")
    return path


def typed(type_: str, value) -> dict:
    """A typed value, as the flow file holds it under the block's `inputs`."""
    return {"type": type_, "value": value}


def python(inputs=(), code="output = None", values=None) -> dict:
    """A Python Code block with the input holes `inputs` running `code`; `values`: its typed values, {hole: typed}."""
    spec = {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": ["output"], "code": code}}
    if values is not None:
        spec["inputs"] = values
    return spec


def recorder(out: Path, inputs: list, values=None, fail=False) -> dict:
    """A Python Code block with the input holes `inputs` that adds a line to the file `out` each time it runs, {hole:
    [the type of what arrived there, its repr]}; then passes on what its first hole got (`fail`: fails instead)."""
    shown = ", ".join(f"{name!r}: [type({name}).__name__, repr({name})]" for name in inputs)
    code = (f"import json\nwith open({str(out)!r}, 'a', encoding='utf-8') as f:\n"
            f"    f.write(json.dumps({{{shown}}}) + '\\n')\n"
            + ("raise ValueError('the totals do not match')\n" if fail else f"output = {inputs[0]}\n"))
    return python(inputs, code, values)


def as_recorded(values: dict) -> dict:
    return {name: [type(value).__name__, repr(value)] for name, value in values.items()}


def recorded(out: Path) -> list[dict]:
    """The lines a recorder added to `out`, one per run of it."""
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] if out.exists() else []


def held(value) -> tuple:
    """A value with its type: 42 is not 42.0, nor True; a date is not a datetime."""
    return type(value).__name__, value


def run_here(env, flow: Path, **kw) -> tuple[engine.RunResult, dict]:
    """`flow` run as `taskloom run` runs it, in this process: (its result, {block: its error} of each block that
    failed)."""
    home = Home(env.home)
    result = engine.run_flow(load_flow(flow), discover(home.blocks_dir), home, **kw)
    return result, dict(env.query("SELECT block, error FROM blocks WHERE run_id=? AND status='failed'",
                                  result.run_id))


def validated_here(env, flow: Path) -> tuple[list, list]:
    """(errors, warnings) of Validate on `flow`, as the editor's Validate and `taskloom validate` check it."""
    home = Home(env.home)
    return validate(load_flow(flow), discover(home.blocks_dir), home)


def said(result) -> str:
    return (result.stdout or "")[-3000:] + (result.stderr or "")[-3000:]


def runs_recorded(env) -> int:
    return env.query("SELECT COUNT(*) FROM runs")[0][0] if (env.home / "history.db").exists() else 0


def problems_told(env, result) -> list[str]:
    """What taskloom said was wrong: its error lines and, of a run that began and failed, each failed block's error
    (after the block's name)."""
    lines = [line for line in result.stderr.splitlines() if line.startswith("error: ")]
    if result.returncode == 1:
        run_id, _ = env.last_run()
        lines += [f"{block}: {error}" for block, error in
                  env.query("SELECT block, error FROM blocks WHERE run_id=? AND status='failed'", run_id)]
    return lines


def inputs_reported(env, run_id: int, block: str) -> list[str]:
    """The lines after "Inputs:" in the failure report `block` logged in run `run_id`, as the run history keeps it
    (what the Log shows); [] if it has no "Inputs:"."""
    history = History(env.home / "history.db")
    try:
        logs = history.logs(run_id)
    finally:
        history.close()
    reports = [log["message"] for log in logs if log["block"] == block and log["level"] == "ERROR"
               and log["message"].startswith(f"{block} (") and " failed: " in log["message"].splitlines()[0]]
    assert len(reports) == 1, logs
    lines = reports[0].splitlines()
    if "Inputs:" not in lines:
        return []
    found = []
    for line in lines[lines.index("Inputs:") + 1:]:
        if not line.startswith("  "):
            break
        found.append(line)
    return found


def gives_back(env) -> str:
    """A reusable block (Global: a file in the blocks folder), made from a Python Code block whose input x it gives
    back: its block type."""
    write_reusable_block(Home(env.home).blocks_dir, "Gives Back", "Gives back what arrives at x.", "x: anything",
                         {"inputs": ["x"], "outputs": ["output"], "use_environment": False, "code": "output = x"})
    return "user.gives_back"


def receiving(env, kind: str, block: str, value: dict) -> tuple[dict, list, tuple]:
    """A block of the kind `kind` named `block` with the typed value `value` at its input: (the blocks, the edges, the
    (block, output) where what it got comes out)."""
    if kind == "reusable block":
        return {block: {"type": gives_back(env), "inputs": {"x": value}}}, [], (block, "output")
    if kind == "If":  # (its condition is true: the value goes out of true)
        return ({block: {"type": "logic.if", "config": {"condition": "True"}, "inputs": {"value": value}}}, [],
                (block, "true"))
    if kind == "Inspect":  # (it passes the value on)
        return {block: {"type": "logic.inspect", "inputs": {"value": value}}}, [], (block, "value")
    # A Splitter: the first block of its line gives back what the line got.
    return ({block: {"type": "logic.splitter", "inputs": {"value": value}},
             f"{block}_line": python(["v"], "output = v")},
            [f"{block}.lines -> {block}_line.v"], (f"{block}_line", "output"))


# --- TV-1, TV-3: the block gets the value converted to its type (T1, T6) ---------------------------------------------

@pytest.mark.invariant("TV-1")
@pytest.mark.invariant("TV-3")
@pytest.mark.parametrize("stored", list(STORED))
def test_python_code_gets_each_typed_value_converted_to_its_type(env, stored):
    """T1 (TV-1, TV-3): a typed value of each type (text, int, float, bool, date, datetime, list) at a Python Code
    block's input, stored as the text typed or as a YAML value of its type, arrives converted, as a flow parameter's
    value is: an int as a number, a date as a date, a list as a list; a text with a comma stays one text."""
    blocks = {f"get_{t}": python(["x"], "output = x", {"x": typed(t, forms[STORED[stored]])})
              for t, forms in TYPED.items()}

    result, failed = run_here(env, flow_file(env.root / "flow.yaml", blocks))

    assert failed == {}, failed
    assert {t: held(result.outputs.get((f"get_{t}", "output"))) for t in TYPED} == {
        t: held(forms[2]) for t, forms in TYPED.items()}


@pytest.mark.invariant("TV-1")
@pytest.mark.invariant("TV-3")
@pytest.mark.parametrize("kind", ["reusable block", "If", "Splitter", "Inspect"])
def test_each_kind_of_block_gets_each_typed_value_converted_to_its_type(env, kind):
    """T1 (TV-1, TV-3): a typed value of each type at the input of a copy of a reusable block (Global), an If's value,
    a Splitter's value (each line gets it) and an Inspect's value arrives converted to its type, as a line would
    bring it."""
    blocks, edges, out = {}, [], {}
    for t, forms in TYPED.items():
        more, more_edges, out[t] = receiving(env, kind, f"get_{t}", typed(t, forms[0]))
        blocks.update(more)
        edges += more_edges

    result, failed = run_here(env, flow_file(env.root / "flow.yaml", blocks, edges))

    assert failed == {}, failed
    assert {t: held(result.outputs.get(out[t])) for t in TYPED} == {t: held(forms[2]) for t, forms in TYPED.items()}


@pytest.mark.invariant("TV-1")
@pytest.mark.invariant("TV-3")
def test_a_loop_runs_once_per_item_of_a_typed_list_at_its_items(env):
    """T1 (TV-1, TV-3): a Loop whose items is the typed list "a, b, c" runs its blocks once per item, a then b then
    c."""
    flow = flow_file(env.root / "flow.yaml", {
        "loop": {"type": "logic.loop", "inputs": {"items": typed("list", "a, b, c")}},
        "work": {**python(["item"], "output = item.upper()"), "inside": "loop"}},
        ["loop.item -> work.item", "work.output -> loop.result"])

    result, failed = run_here(env, flow)

    assert failed == {}, failed
    assert result.outputs.get(("loop", "results")) == ["A", "B", "C"]


LISTS = {"two items": ("a, b", ["a", "b"]), "one item": ("a", ["a"]), "no item": ("", [])}


@pytest.mark.invariant("TV-1")
@pytest.mark.invariant("TV-3")
def test_a_typed_list_of_two_items_one_item_or_none(env):
    """T6 (TV-1, TV-3): the typed list "a, b" arrives as ["a", "b"], "a" as ["a"], and "" as the empty list."""
    blocks = {f"get_{n}": python(["x"], "output = x", {"x": typed("list", text)})
              for n, (text, _) in enumerate(LISTS.values())}

    result, failed = run_here(env, flow_file(env.root / "flow.yaml", blocks))

    assert failed == {}, failed
    assert {name: result.outputs.get((f"get_{n}", "output")) for n, name in enumerate(LISTS)} == {
        name: expected for name, (_, expected) in LISTS.items()}


# --- TV-7: an empty text is a value; no value is not (T4) -------------------------------------------------------------

@pytest.mark.invariant("TV-7")
def test_an_empty_typed_text_is_a_value_and_a_hole_without_one_gets_nothing(env):
    """T4 (TV-7): the typed text "" arrives as the empty text, not as nothing; a hole with no typed value and no line
    gets nothing (None), as before."""
    flow = flow_file(env.root / "flow.yaml", {
        "get": python(["empty", "unset"], "output = [empty, unset]", {"empty": typed("text", "")})})

    result, failed = run_here(env, flow)

    assert failed == {}, failed
    assert result.outputs.get(("get", "output")) == ["", None]


# --- TV-3: exactly as if another block had sent it, in every way of running (T9, T12, T13, T14) ---------------------

@pytest.mark.invariant("TV-3")
@pytest.mark.invariant("BR-1")
def test_a_setting_naming_an_input_with_a_typed_value_is_filled_with_it(env):
    """TV-3: a Wait whose Seconds is {{value}} and whose value input has the typed int 0 is filled with it as with a
    value a line brought (never "nothing is connected to the input value"): it waits 0 s and passes the number on."""
    flow = flow_file(env.root / "flow.yaml", {
        "pause": {"type": "logic.wait", "config": {"seconds": "{{value}}"}, "inputs": {"value": typed("int", "0")}}})

    result, failed = run_here(env, flow)

    assert failed == {}, failed
    assert held(result.outputs.get(("pause", "value"))) == held(0)


@pytest.mark.invariant("TV-3")
def test_a_block_inside_a_loop_gets_its_typed_value_for_every_item(env):
    """T14 (TV-3): a block inside a Loop with a typed value (the int 100) at one input and the item at another gets
    the typed value in the run of every item."""
    flow = flow_file(env.root / "flow.yaml", {
        "make": python([], "output = [1, 2, 3]"),
        "loop": {"type": "logic.loop"},
        "work": {**python(["item", "k"], "output = item + k", {"k": typed("int", "100")}), "inside": "loop"}},
        ["make.output -> loop.items", "loop.item -> work.item", "work.output -> loop.result"])

    result, failed = run_here(env, flow)

    assert failed == {}, failed
    assert result.outputs.get(("loop", "results")) == [101, 102, 103]


@pytest.mark.invariant("TV-3")
def test_blocks_on_a_splitters_lines_get_their_own_typed_values(env):
    """T14 (TV-3): on each line of a Splitter, a block with a typed value of its own (10, 20) at one input and the
    line's value (5) at another gets its typed value; the Merger joins 15 and 25."""
    flow = flow_file(env.root / "flow.yaml", {
        "make": python([], "output = 5"),
        "split": {"type": "logic.splitter"},
        "one": python(["v", "k"], "output = v + k", {"k": typed("int", "10")}),
        "two": python(["v", "k"], "output = v + k", {"k": typed("int", "20")}),
        "join": {"type": "logic.merger"}},
        ["make.output -> split.value", "split.lines -> one.v", "split.lines -> two.v", "one.output -> join.lines",
         "two.output -> join.lines"])

    result, failed = run_here(env, flow)

    assert failed == {}, failed
    assert result.outputs.get(("join", "value")) == {"one": 15, "two": 25}


@pytest.mark.invariant("TV-3")
@pytest.mark.invariant("TV-6")
@pytest.mark.parametrize("mode", ["--only", "--from"])
def test_run_only_this_block_and_run_from_this_block_on_use_the_typed_values(env, mode):
    """T9 (TV-3, TV-6): after a full run, Run only this block (--only a --inputs-from that run) gives a its typed value
    again (the int 7); Run from this block on (--from a) also gives the block after it its own (a date) and what a
    passes on."""
    out_a, out_b = env.root / "a.jsonl", env.root / "b.jsonl"
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "a": recorder(out_a, ["x"], {"x": typed("int", "7")}),
        "b": recorder(out_b, ["from_a", "y"], {"y": typed("date", "2026-11-24")})},
        ["start.start -> a.after", "a.output -> b.from_a"])
    full = env.taskloom("run", flow)
    assert full.returncode == 0, said(full)
    run_id, _ = env.last_run()

    again = env.taskloom("run", flow, mode, "a", "--inputs-from", run_id)

    assert again.returncode == 0, said(again)
    assert recorded(out_a) == [as_recorded({"x": 7})] * 2
    assert recorded(out_b) == [as_recorded({"from_a": 7, "y": dt.date(2026, 11, 24)})] * (2 if mode == "--from" else 1)


@pytest.mark.invariant("TV-6")
@pytest.mark.parametrize("mode", ["--only", "--from"])
def test_with_the_same_inputs_gives_todays_typed_value_and_what_lines_brought_in_that_run(env, mode):
    """T10 (TV-6): the typed value 10 changed to 50, "with the same inputs" (mode use --inputs-from the full run) gives
    use 50, while its line brings what it brought in that run ("first", though the block before it would now give
    "second"); changed to 70, the same again from that re-run (whose inputs it kept) gives 70, the line still
    "first"."""
    out, seed = env.root / "use.jsonl", env.root / "seed.txt"

    def flow(n: str) -> Path:
        return flow_file(env.root / "flow.yaml", {
            "start": {"type": "trigger.manual"},
            "src": python([], "output = open('seed.txt', encoding='utf-8').read()"),
            "use": recorder(out, ["n", "line_in"], {"n": typed("int", n)})},
            ["start.start -> src.after", "src.output -> use.line_in"])

    seed.write_text("first", encoding="utf-8")
    full = env.taskloom("run", flow("10"))
    assert full.returncode == 0, said(full)
    full_id, _ = env.last_run()
    seed.write_text("second", encoding="utf-8")

    again = env.taskloom("run", flow("50"), mode, "use", "--inputs-from", full_id)
    assert again.returncode == 0, said(again)
    again_id, _ = env.last_run()
    once_more = env.taskloom("run", flow("70"), mode, "use", "--inputs-from", again_id)

    assert once_more.returncode == 0, said(once_more)
    assert recorded(out) == [as_recorded({"n": n, "line_in": "first"}) for n in (10, 50, 70)]


@pytest.mark.invariant("TV-3")
def test_a_block_with_only_typed_values_and_no_line_from_a_start_block_does_not_run(env):
    """T12 (TV-3): typed values do not change which blocks run: in a flow with a Start block, a block with a typed value
    and no line leading to it from the Start block does not run; the block the Start block leads to does."""
    out_on, out_off = env.root / "on.jsonl", env.root / "off.jsonl"
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "on": recorder(out_on, ["x"], {"x": typed("int", "1")}),
        "off": recorder(out_off, ["x"], {"x": typed("int", "2")})},
        ["start.start -> on.after"])

    result = env.taskloom("run", flow)

    assert result.returncode == 0, said(result)
    run_id, _ = env.last_run()
    assert recorded(out_on) == [as_recorded({"x": 1})]
    assert recorded(out_off) == [] and env.block_status(run_id).get("off") != "success"


@pytest.mark.invariant("TV-3")
def test_the_command_line_and_the_scheduler_give_a_block_the_same_typed_values(env):
    """T13 (TV-3): a scheduled flow whose block has a typed value of each type: run from the command line, and started
    by the scheduler service at its time (as `taskloom scheduler` starts it: its own `taskloom run` process), the
    block gets the same values, each converted to its type."""
    out = env.root / "got.jsonl"
    flow = flow_file(env.root / "flow.yaml", {
        "every_minute": {"type": "trigger.schedule", "config": {"cron": "* * * * *"}},
        "get": recorder(out, [f"x_{t}" for t in TYPED], {f"x_{t}": typed(t, forms[0]) for t, forms in TYPED.items()})},
        ["every_minute.start -> get.after"])
    by_hand = env.taskloom("run", flow)
    assert by_hand.returncode == 0, said(by_hand)

    told = []
    with on(env):
        home = Home(env.home)
        home.set_scheduled(flow, True)
        scheduler = Scheduler(home, lambda title, message: told.append((title, message)))
        scheduler.tick(T0)  # first sight of the schedule
        scheduler.tick(T0 + dt.timedelta(minutes=1))  # due now: it starts
        scheduler.wait_for_runs()

    expected = as_recorded({f"x_{t}": forms[2] for t, forms in TYPED.items()})
    assert recorded(out) == [expected, expected], told


# --- TV-6: Validate counts a typed value as supplied; the failure report lists it (T16) ------------------------------

@pytest.mark.invariant("TV-6")
def test_validate_counts_a_typed_value_at_a_loops_items_as_supplied(env):
    """T16 (TV-6): a Loop whose items has a typed list and no line passes Validate (the editor's and `taskloom
    validate`): no "required input 'items' is not connected"."""
    flow = flow_file(env.root / "flow.yaml", {
        "loop": {"type": "logic.loop", "inputs": {"items": typed("list", "a, b")}},
        "work": {**python(["item"], "output = item"), "inside": "loop"}},
        ["loop.item -> work.item", "work.output -> loop.result"])

    errors, warnings = validated_here(env, flow)
    by_command = env.taskloom("validate", flow)

    assert errors == [] and [w for w in warnings if "items" in w] == [], (errors, warnings)
    assert by_command.returncode == 0 and "not connected" not in by_command.stderr, said(by_command)


@pytest.mark.invariant("TV-6")
def test_validate_counts_a_typed_value_at_an_api_requests_body_as_it_counts_a_line(env):
    """T16 (TV-6): an API Request takes its body from its body input when that is supplied, so Validate refuses a Body
    setting of its own (text) with a line into body; with a typed value there instead of the line, it refuses it too,
    naming the block and Body."""
    def request(edges=(), **more) -> Path:
        return flow_file(env.root / "flow.yaml", {
            "src": python([], "output = 'sent'"),
            "req": {"type": "servers.api_request", "config": {"url": "http://127.0.0.1:9/t", "method": "POST",
                                                              "body": "text", "content": "own"}, **more}}, edges)

    by_line, _ = validated_here(env, request(["src.output -> req.body"]))
    by_value, _ = validated_here(env, request(inputs={"body": typed("text", "typed")}))

    assert [e for e in by_line if e.startswith("req: Body:")], by_line  # (as before)
    assert [e for e in by_value if e.startswith("req: Body:")], by_value


@pytest.mark.invariant("TV-6")
@pytest.mark.parametrize("mode", ["a full run", "--only", "--from"])
def test_a_failure_reports_inputs_list_the_typed_value(env, mode):
    """T16 (TV-6): a block that fails, with the typed int 4217 at one input and a line into another, says in its failure
    report's "Inputs:" what each got: the line's value as before and the typed value, in a full run, Run only this
    block and Run from this block on."""
    out = env.root / "use.jsonl"
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "src": python([], "output = 'from src'"),
        "use": recorder(out, ["line_in", "typed_in"], {"typed_in": typed("int", "4217")}, fail=True)},
        ["start.start -> src.after", "src.output -> use.line_in"])
    result = env.taskloom("run", flow)
    assert result.returncode == 1, said(result)
    if mode != "a full run":
        full_id, _ = env.last_run()
        result = env.taskloom("run", flow, mode, "use", "--inputs-from", full_id)
        assert result.returncode == 1, said(result)
    run_id, _ = env.last_run()

    lines = inputs_reported(env, run_id, "use")

    assert "  line_in ← src.output: from src" in lines, lines
    assert len([line for line in lines if line.startswith("  typed_in") and line.endswith(": 4217")]) == 1, lines
    assert recorded(out)[-1] == as_recorded({"line_in": "from src", "typed_in": 4217})


# --- TV-9: a hand-edited typed value that cannot be (T17) -------------------------------------------------------------

HAND_EDITS = {  # (the hole the message names, the typed values, the edges)
    "a value and a line on one hole": ("typed_in", {"typed_in": typed("int", "3")},
                                       ["src.output -> receiver.typed_in"]),
    "an int that does not fit": ("typed_in", {"typed_in": typed("int", "1,5")}, []),
    "a date that does not exist": ("typed_in", {"typed_in": typed("date", "2026-02-30")}, []),
    "a value on a hole the block does not have": ("no_such_hole", {"no_such_hole": typed("text", "a")}, []),
}


@pytest.mark.invariant("TV-9")
@pytest.mark.parametrize("hole, values, edges", list(HAND_EDITS.values()), ids=list(HAND_EDITS))
def test_a_hand_edited_typed_value_that_cannot_be_is_named_by_validate_and_the_run_refuses_with_its_words(
        env, hole, values, edges):
    """T17 (TV-9): a flow file edited by hand with a typed value and a line on the same hole, a value that does not fit
    its type ("1,5" as an int, 2026-02-30 as a date), or a value on a hole the block does not have: `taskloom validate`
    names the block and the hole, and `taskloom run` refuses to start with the same message; the block never runs."""
    out = env.root / "got.jsonl"
    flow = flow_file(env.root / "flow.yaml", {
        "src": python([], "output = 1"),
        "receiver": recorder(out, ["typed_in"], values)}, edges)

    validated, ran = env.taskloom("validate", flow), env.taskloom("run", flow)

    def naming(result) -> list[str]:
        return [line for line in result.stderr.splitlines()
                if line.startswith("error: ") and "receiver" in line and hole in line]

    assert validated.returncode == 2 and naming(validated), said(validated)
    assert ran.returncode == 2 and naming(ran) == naming(validated), said(ran)
    assert runs_recorded(env) == 0 and recorded(out) == []


# --- TV-3 with BR-1..9: {{name}} in a typed text or list is filled once, like any field (T20) ------------------------

V = "V7"  # the flow parameter p
BRACE_PARAMS = {"p": typed("text", V), "q": typed("text", "Q9"), "day": typed("date", dt.date(2026, 11, 16)),
                "braced": typed("text", "{{q}}"), "comma": typed("text", "x, y")}
FILLED_IN = {  # (the promise, the type, the typed value, what the block gets)
    "a parameter": ("BR-1", "text", "out_{{day}}.csv", "out_2026-11-16.csv"),
    "a parameter formatted with spaces inside": ("BR-1", "text", "{{ day : %d.%m.%Y }}", "16.11.2026"),
    "an input that comes by a line": ("BR-1", "text", "out_{{line_in}}.csv", "out_W3.csv"),
    "single braces stay": ("BR-2", "text", '{"k": {{p}}} ${HOME} \\d{4}', '{"k": V7} ${HOME} \\d{4}'),
    "a single-brace name stays": ("BR-4", "text", "{day}", "{day}"),
    "a value is put in once": ("BR-6", "text", "{{braced}}", "{{q}}"),
    "four opening braces give two": ("BR-9", "text", "{{{{p}}", "{{p}}"),
    "a backslash is plain text": ("BR-9", "text", "C:\\data\\{{p}}.csv", f"C:\\data\\{V}.csv"),
    "list items filled one by one": ("BR-1", "list", "{{comma}}, b", ["x, y", "b"]),
}


@pytest.mark.invariant("TV-3")
@pytest.mark.parametrize("type_, value, expected", [
    pytest.param(t, value, expected, id=name, marks=pytest.mark.invariant(row))
    for name, (row, t, value, expected) in FILLED_IN.items()])
def test_double_braces_in_a_typed_text_or_list_are_filled_once_like_any_field(env, type_, value, expected):
    """T20 (TV-3, BR-1, BR-2, BR-4, BR-6, BR-9): {{day}} in a typed text is the flow parameter day, formatted as
    {{day:format}} says, spaces inside the braces aside; {{line_in}} is what the line into the block's input line_in
    brought; single braces, ${HOME}, a regex and a single-brace {day} stay as typed; a parameter whose value holds
    {{q}} gives {{q}}, not filled again; {{{{ gives {{; a backslash is plain text; in a typed list each item is filled
    on its own, so a value with a comma stays one item."""
    flow = flow_file(env.root / "flow.yaml", {
        "src": python([], "output = 'W3'"),
        "receiver": python(["typed_in", "line_in"], "output = typed_in", {"typed_in": typed(type_, value)})},
        ["src.output -> receiver.line_in"], params=BRACE_PARAMS)

    result, failed = run_here(env, flow)

    assert failed == {}, failed
    assert result.outputs.get(("receiver", "output")) == expected


NOT_FILLED = {  # (the typed text, the name the message names, more typed values)
    "a name of nothing": ("{{nope}}", "nope", {}),
    "not closed": ("{{p", None, {}),
    "another typed value": ("{{other_typed}}", "other_typed", {"other_typed": typed("text", "abc")}),
}


@pytest.mark.invariant("TV-3")
@pytest.mark.invariant("BR-3")
@pytest.mark.parametrize("value, name, more", list(NOT_FILLED.values()), ids=list(NOT_FILLED))
def test_a_typed_text_naming_nothing_or_another_typed_value_is_an_error_at_validate_and_at_the_run(
        env, value, name, more):
    """T20 (TV-3, BR-3): {{nope}} (no parameter or input of that name), a {{ not closed, and {{other_typed}} naming
    another typed value of the block (which comes by no line) in a typed text: an error of `taskloom validate` and of
    `taskloom run`, naming the block, the hole (and the name); the block never runs."""
    out = env.root / "got.jsonl"
    flow = flow_file(env.root / "flow.yaml", {
        "receiver": recorder(out, ["typed_in", "other_typed"], {"typed_in": typed("text", value), **more})},
        params=BRACE_PARAMS)

    validated, ran = env.taskloom("validate", flow), env.taskloom("run", flow)

    def names_it(lines) -> bool:
        return any("receiver" in line and "typed_in" in line and (name is None or name in line) for line in lines)

    assert validated.returncode == 2 and names_it(problems_told(env, validated)), said(validated)
    assert ran.returncode != 0 and names_it(problems_told(env, ran)), said(ran)
    assert recorded(out) == []


@pytest.mark.invariant("TV-3")
@pytest.mark.invariant("BR-4")
def test_a_single_brace_parameter_name_in_a_typed_text_gives_validates_did_you_mean_warning(env):
    """T20 (TV-3, BR-4): {day} in a typed text, day being a flow parameter, gives one Validate warning naming the block
    and the hole: did you mean {{day}}? (The run gives {day} as typed: see the test above.)"""
    flow = flow_file(env.root / "flow.yaml", {
        "receiver": python(["typed_in"], "output = typed_in", {"typed_in": typed("text", "{day}")})},
        params=BRACE_PARAMS)

    validated = env.taskloom("validate", flow)

    assert validated.returncode == 0, said(validated)
    assert len([line for line in validated.stderr.splitlines()
                if line.startswith("warning: ") and "receiver" in line and "typed_in" in line
                and "did you mean {{day}}" in line.lower()]) == 1, validated.stderr
