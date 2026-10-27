"""Several blocks connected into one input of a Python Code block: the input is a dict keyed by the source block's
name, with that block's output as the value, so your code can tell the values apart (one edge still gives the value).
Through `taskloom run` on real flow files, in Taskloom's own Python and in your Python environment."""

import json
import sys

import pytest
import yaml


def python(code, inputs=(), outputs=("output",), **config):
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code, **config}}


def saver(*names, **config):
    """A Python Code block that writes what each of its inputs got (its type and value) to got.json."""
    got = ", ".join(f"{name!r}: [type({name}).__name__, {name}]" for name in names)
    code = f"import json\nwith open('got.json', 'w', encoding='utf-8') as f:\n    json.dump({{{got}}}, f)\n"
    return python(code, names, (), **config)


def write_flow(env, blocks, edges):
    flow = env.root / "flow.yaml"
    flow.write_text(yaml.safe_dump({"blocks": blocks, "edges": list(edges)}, sort_keys=False), encoding="utf-8")
    return flow


def got_after(env, flow, *args) -> dict:
    """Runs the flow (with `args`) and returns {input: [type name, value]} as the saver block got them."""
    got = env.root / "got.json"
    got.unlink(missing_ok=True)
    result = env.taskloom("run", flow, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(got.read_text(encoding="utf-8"))  # keeps the dict's key order


@pytest.mark.parametrize("use_environment", [False, True], ids=["taskloom_python", "your_python_environment"])
def test_blocks_connected_into_one_input_arrive_as_a_dict_by_block_name_one_edge_as_the_value(env, use_environment):
    if use_environment:
        created = env.taskloom("env", "create", "--python", sys.executable, timeout=180)
        assert created.returncode == 0, created.stderr
    flow = write_flow(env, {
        "load": python("output = [1, 2]"),
        "report": python("output = 'ready'"),
        "count": python("output = 3"),
        "combine": saver("input", "single", use_environment=use_environment),
    }, ["report.output -> combine.input", "load.output -> combine.input", "count.output -> combine.single"])

    got = got_after(env, flow)

    assert got["input"] == ["dict", {"report": "ready", "load": [1, 2]}], got
    assert list(got["input"][1]) == ["report", "load"]  # in edge order
    assert got["single"] == ["int", 3]  # one edge: the value itself


def test_two_outputs_of_one_block_into_the_same_input_are_keyed_block_dot_port(env):
    flow = write_flow(env, {
        "check": python("result = 'fine'\nextra = 7", outputs=("result", "extra")),
        "other": python("output = 'other'"),
        "handler": saver("input"),
    }, ["check.result -> handler.input", "other.output -> handler.input", "check.extra -> handler.input"])

    got = got_after(env, flow)

    assert got["input"] == ["dict", {"check.result": "fine", "other": "other", "check.extra": 7}], got
    assert list(got["input"][1]) == ["check.result", "other", "check.extra"]


def test_a_block_with_its_result_and_its_error_into_the_input_is_keyed_block_dot_port_when_one_delivers(env):
    flow = write_flow(env, {
        "check": python("result = 'fine'", outputs=("result",)),
        "other": python("output = 'other'"),
        "handler": saver("input"),
    }, ["check.result -> handler.input", "check.error -> handler.input", "other.output -> handler.input"])

    got = got_after(env, flow)

    assert got["input"] == ["dict", {"check.result": "fine", "other": "other"}], got
    assert list(got["input"][1]) == ["check.result", "other"]


def test_after_an_if_only_the_path_that_ran_is_in_the_dict(env):
    flow = write_flow(env, {
        "rows": python("output = [1, 2, 3]"),
        "route": {"type": "logic.if", "config": {"condition": "len(value) > 5"}},
        "many": python("output = 'many'", inputs=("input",)),
        "few": python("output = 'few'", inputs=("input",)),
        "merge": saver("input"),
    }, ["rows.output -> route.value", "route.true -> many.input", "route.false -> few.input",
        "many.output -> merge.input", "few.output -> merge.input"])

    got = got_after(env, flow)

    assert got["input"] == ["dict", {"few": "few"}], got


ERROR_BLOCKS = {
    "load": {"type": "data.read_file", "config": {"path": "missing.csv"}},
    "compute": python("output = 1 / 0"),
    "fine": python("output = 1"),
    "handler": saver("errors"),
}
ERROR_EDGES = ["compute.error -> handler.errors", "fine.error -> handler.errors", "load.error -> handler.errors"]


def test_one_handler_gets_the_errors_of_the_blocks_that_failed_keyed_by_their_names(env):
    flow = write_flow(env, ERROR_BLOCKS, ERROR_EDGES)

    got = got_after(env, flow)  # the failures were handled: the run succeeds

    kind, errors = got["errors"]
    assert kind == "dict" and list(errors) == ["compute", "load"], got  # not "fine": it did not fail
    assert all(error["block"] == name for name, error in errors.items())
    run_id, _ = env.last_run()
    assert env.block_status(run_id) == {"load": "failed", "compute": "failed", "fine": "success", "handler": "success"}


def test_running_the_handler_again_on_its_own_gives_it_the_same_dict(env):
    flow = write_flow(env, ERROR_BLOCKS, ERROR_EDGES)
    first = got_after(env, flow)
    assert first["errors"][0] == "dict" and list(first["errors"][1]) == ["compute", "load"], first
    run_id, _ = env.last_run()

    again = got_after(env, flow, "--only", "handler", "--inputs-from", run_id)  # from what the blocks before it hold
    assert again == first and list(again["errors"][1]) == ["compute", "load"]

    rerun_id, _ = env.last_run()
    assert rerun_id != run_id
    once_more = got_after(env, flow, "--only", "handler", "--inputs-from", rerun_id)  # the inputs kept from that re-run
    assert once_more == first and list(once_more["errors"][1]) == ["compute", "load"]


def test_other_blocks_with_several_edges_into_one_input_still_get_a_list(env):
    flow = write_flow(env, {
        "first": python("output = 'one'"),
        "second": python("output = 'two'"),
        "look": {"type": "logic.inspect", "config": {}},
        "show": saver("input"),
    }, ["first.output -> look.value", "second.output -> look.value", "look.value -> show.input"])

    got = got_after(env, flow)  # Inspect passes on what it got

    assert got["input"] == ["list", ["one", "two"]], got
