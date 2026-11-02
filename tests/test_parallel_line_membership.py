"""Which blocks a Splitter's line is made of, when lines run one after another or with At most at once lower than the
number of lines: the Merger and what follows it are after the lines, even a block that also takes a line's output
(spec 2), and a block a Splitter's connection goes to starts its own line, so the lines run in their numbered order
(spec 2, 3). Through the real `taskloom` command (validate, run) on flow files, with every run bounded by a deadline:
a layout Validate accepts must finish (spec 7)."""

import pytest

from conftest import events
from tests.test_parallel_lines import (LOAD, MANUAL, ONE_AFTER_ANOTHER, SAME_TIME, across_flow, fed, into_merger,
                                       lines_into, merged, merger, merger_out, python, read_json, result_of, run,
                                       saver, sequence, splitter, step)
from tests.test_paths_editor_editing import flow_file

DEADLINE = 60  # seconds: such a run takes a few; one that never ends is killed and the test fails


@pytest.mark.parametrize("lines_run, at_most", [(ONE_AFTER_ANOTHER, None), (SAME_TIME, 1)],
                         ids=["one_after_another", "at_the_same_time_at_most_1_of_2"])
def test_a_block_after_the_merger_that_also_takes_a_lines_output_runs_after_the_merger(env, lines_run, at_most):
    """Spec 2 (the Merger and what follows it are after the lines) and 7 (a layout Validate accepts can finish):
    report takes load_sales' output and the Merger's; it runs after the Merger, not in load_sales' line, and the run
    ends."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(lines_run, at_most),
        "load_sales": step("load_sales", y=-200), "load_costs": step("load_costs", y=200), "merge": merger(x=800),
        "report": python("import json\nwith open('report.json', 'w', encoding='utf-8') as f:\n"
                         "    json.dump([sales, joined], f)", ("sales", "joined"), outputs=(), x=1200),
    }, [*fed(), *lines_into("split", "load_sales", "load_costs"), *into_merger("merge", "load_sales", "load_costs"),
        "load_sales.output -> report.sales", merger_out("merge", "report", "joined")])
    checked = env.taskloom("validate", flow)
    assert checked.returncode == 0, checked.stderr

    result = env.taskloom("run", flow, "--json-events", timeout=DEADLINE)

    assert result.returncode == 0, result.stderr[-3000:]
    told = sequence(result.stdout, ["merge", "report"])
    assert told == [("block_started", "merge"), ("block_finished", "merge"), ("block_started", "report"),
                    ("block_finished", "report")], told
    assert read_json(env.root / "report.json") == ["load_sales", {"load_sales": "load_sales",
                                                                  "load_costs": "load_costs"}]


def test_one_after_another_runs_the_lines_in_their_numbered_order_when_a_later_line_starts_from_an_earlier_ones_output(
        env):
    """Spec 2 and 3 (one after another: line 1 to its end, then line 2, then line 3, as the Log line numbers them;
    a connection from an earlier line into a later one waits): line 3's first block, code, also takes line 1's output;
    it still runs in line 3, after line 2, and Validate accepts the layout."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(ONE_AFTER_ANOTHER),
        "wait_a": step("wait_a", 0.3, y=-200), "wait_b": step("wait_b", 0.3, y=0),
        "code": python("output = [input, a]", ("input", "a"), x=400, y=200),
        "merge": merger(x=800), "report": saver("report.json"),
    }, [*fed(), *lines_into("split", "wait_a", "wait_b", "code"), "wait_a.output -> code.a",
        *into_merger("merge", "wait_a", "wait_b", "code"), merger_out("merge", "report")])
    checked = env.taskloom("validate", flow)
    assert checked.returncode == 0, checked.stderr

    result = env.taskloom("run", flow, "--json-events", timeout=DEADLINE)

    assert result.returncode == 0, result.stderr[-3000:]
    said = [e["message"] for e in events(result.stdout)
            if e["event"] == "log" and e.get("block") == "split"]
    assert said == ["3 lines one after another: 1 wait_a, 2 wait_b, 3 code"], said
    order = ["wait_a", "wait_b", "code"]
    assert sequence(result.stdout, order) == [(kind, b) for b in order for kind in ("block_started", "block_finished")]
    assert read_json(env.root / "report.json") == {"wait_a": "wait_a", "wait_b": "wait_b", "code": ["today", "wait_a"]}


@pytest.mark.parametrize("lines_run, at_most", [(ONE_AFTER_ANOTHER, None), (SAME_TIME, 1)],
                         ids=["one_after_another", "at_the_same_time_at_most_1_of_2"])
def test_a_block_of_a_later_line_may_use_an_earlier_lines_output(env, lines_run, at_most):
    """The user's "the 2nd line can connect with the 1st line" (spec 3) and spec 7 (Validate refuses only a layout that
    could never finish): line 2 is load_costs -> costs_total, and costs_total also takes line 1's load_sales. Line 1
    has ended before line 2 starts, so Validate accepts it, and the run ends with costs_total run in line 2."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(lines_run, at_most),
        "load_sales": step("load_sales", 0.3, y=-200), "load_costs": step("load_costs", 0.3, y=200),
        "costs_total": python("output = [input, sales]", ("input", "sales"), x=800, y=200),
        "merge": merger(x=1200), "report": saver("report.json"),
    }, [*fed(), *lines_into("split", "load_sales", "load_costs"), "load_costs.output -> costs_total.input",
        "load_sales.output -> costs_total.sales", *into_merger("merge", "load_sales", "costs_total"),
        merger_out("merge", "report")])
    checked = env.taskloom("validate", flow)
    assert checked.returncode == 0, checked.stderr

    result = env.taskloom("run", flow, "--json-events", timeout=DEADLINE)

    assert result.returncode == 0, result.stderr[-3000:]
    order = ["load_sales", "load_costs", "costs_total"]
    assert sequence(result.stdout, order) == [(kind, b) for b in order for kind in ("block_started", "block_finished")]
    assert read_json(env.root / "report.json") == {"load_sales": "load_sales",
                                                   "costs_total": ["load_costs", "load_sales"]}


def test_one_after_another_a_block_after_several_lines_runs_in_the_last_of_them(env):
    """Spec 2, 3 and 7: combine, beside the top line, takes load_sales' output (line 1) and load_stock's (line 3); one
    after another it runs once line 3's load_stock has, so the layout can finish and Validate accepts it."""
    flow = across_flow(env.root, splitter(ONE_AFTER_ANOTHER))
    checked = env.taskloom("validate", flow)
    assert checked.returncode == 0, checked.stderr

    result = env.taskloom("run", flow, "--json-events", timeout=DEADLINE)

    assert result.returncode == 0, result.stderr[-3000:]
    order = ["load_sales", "load_costs", "load_stock", "combine"]
    assert sequence(result.stdout, order) == [(kind, b) for b in order for kind in ("block_started", "block_finished")]
    assert read_json(env.root / "report.json") == {"combine": ["sales", "stock"], "load_costs": "costs",
                                                   "load_stock": "stock"}


@pytest.mark.parametrize("lines_run, at_most", [(ONE_AFTER_ANOTHER, None), (SAME_TIME, 1)],
                         ids=["one_after_another", "at_the_same_time_at_most_1_of_2"])
def test_a_line_block_fed_by_a_block_outside_the_lines_added_last_runs(env, lines_run, at_most):
    """Spec 7 (a layout Validate accepts finishes) and 3 (blocks outside the lines run as before): save_sales, in line
    1, also takes folder's output; folder is outside the lines and was added last, so it comes after the Merger in the
    file. The Merger waits for line 2, which waits for line 1, which waits for folder: folder runs meanwhile, and the
    run ends."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(lines_run, at_most),
        "load_sales": step("load_sales", 0.3, y=-200), "load_costs": step("load_costs", 0.3, y=200),
        "merge": merger(x=1200), "report": saver("report.json"),
        "folder": python("output = 'out'", (), x=400, y=-400),
        "save_sales": python("import json\nwith open('sales.json', 'w', encoding='utf-8') as f:\n"
                             "    json.dump([input, folder], f)", ("input", "folder"), outputs=(), x=800, y=-200),
    }, [*fed(), *lines_into("split", "load_sales", "load_costs"), *into_merger("merge", "load_sales", "load_costs"),
        merger_out("merge", "report"), "manual.start -> folder.after", "load_sales.output -> save_sales.input",
        "folder.output -> save_sales.folder"])
    checked = env.taskloom("validate", flow)
    assert checked.returncode == 0, checked.stderr

    result = env.taskloom("run", flow, "--json-events", timeout=DEADLINE)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "sales.json") == ["load_sales", "out"]
    assert read_json(env.root / "report.json") == {"load_sales": "load_sales", "load_costs": "load_costs"}


def test_a_failure_before_the_splitter_skips_the_merger(env):
    """Spec 5: the Merger fails when a line it waits for failed; a failure before the Splitter is no line's: the
    Merger is skipped, as any block after a failure, and its error output does not run."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": python("raise ValueError('no data today')", inputs=(), x=-200),
        "split": splitter(), "load_sales": step("load_sales", y=-200), "load_costs": step("load_costs", y=200),
        "merge": merger(x=800), "report": saver("report.json"), "on_failure": saver("handled.json", y=400),
    }, [*fed(), *lines_into("split", "load_sales", "load_costs"), *into_merger("merge", "load_sales", "load_costs"),
        merger_out("merge", "report"), "merge.error -> on_failure.input"])

    result, run_id = run(env, flow)

    assert result.returncode == 1, result.stderr[-3000:]
    merge = result_of(env, run_id, "merge")
    assert (merge["status"], merge["summary"]) == ("skipped", "something before it failed"), merge
    assert not (env.root / "handled.json").exists()


def side_by_side(folder, lines_run, both_ways: bool):
    """Two Splitters side by side, sales (lines a1, a2) and costs (lines b1, b2), each joined by its Merger; a2's
    output also goes to b1, and (both_ways) b2's to a1."""
    return flow_file(folder / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "sales": splitter(lines_run, y=-300), "costs": splitter(lines_run, y=300),
        "a1": python("output = 'a1'", ("input", "b2"), y=-400), "a2": step("a2", 0.3, y=-200),
        "b1": python("output = ['b1', a2]", ("input", "a2"), y=200), "b2": step("b2", y=400),
        "sales_merge": merger(x=800, y=-300), "costs_merge": merger(x=800, y=300),
        "report": python("import json\nwith open('report.json', 'w', encoding='utf-8') as f:\n"
                         "    json.dump([sales, costs], f)", ("sales", "costs"), outputs=(), x=1200),
    }, [*fed("sales"), "load.output -> costs.value", *lines_into("sales", "a1", "a2"), *lines_into("costs", "b1", "b2"),
        *into_merger("sales_merge", "a1", "a2"), *into_merger("costs_merge", "b1", "b2"), "a2.output -> b1.a2",
        *(["b2.output -> a1.b2"] if both_ways else []),
        f"sales_merge.{merged()} -> report.sales", f"costs_merge.{merged()} -> report.costs"])


@pytest.mark.parametrize("lines_run", [SAME_TIME, ONE_AFTER_ANOTHER])
def test_validate_refuses_the_lines_of_two_splitters_side_by_side_feeding_each_other_both_ways(env, lines_run):
    """Spec 7 and the no-surprises rule, as agreed with the user: the lines of two Splitters side by side that feed
    each other both ways could wait for each other forever; Validate refuses it in every mode, naming a connection
    each way, and the run does not start."""
    flow = side_by_side(env.root, lines_run, both_ways=True)

    checked = env.taskloom("validate", flow)

    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert [line for line in checked.stderr.splitlines() if line.startswith("error:")
            and "a2.output -> b1.a2" in line and "b2.output -> a1.b2" in line], checked.stderr
    refused = env.taskloom("run", flow, timeout=DEADLINE)
    assert refused.returncode == 2, refused.stderr
    assert not (env.root / "report.json").exists()


@pytest.mark.parametrize("lines_run", [SAME_TIME, ONE_AFTER_ANOTHER])
def test_the_lines_of_two_splitters_side_by_side_may_feed_each_other_one_way(env, lines_run):
    """Spec 3 (a line may use what another line makes; its block waits for it): b1, costs' line 1, takes a2's output,
    sales' line 2; Validate accepts it, b1 runs once a2 has, and the run ends."""
    flow = side_by_side(env.root, lines_run, both_ways=False)
    checked = env.taskloom("validate", flow)
    assert checked.returncode == 0, checked.stderr

    result = env.taskloom("run", flow, "--json-events", timeout=DEADLINE)

    assert result.returncode == 0, result.stderr[-3000:]
    told = sequence(result.stdout, ["a2", "b1"])
    assert told.index(("block_started", "b1")) > told.index(("block_finished", "a2")), told
    assert read_json(env.root / "report.json") == [{"a1": "a1", "a2": "a2"}, {"b1": ["b1", "a2"], "b2": "b2"}]


def test_validate_refuses_a_block_that_takes_from_the_lines_of_two_splitters_side_by_side(env):
    """As agreed with the user: b1x, after costs' b1, also takes sales' a2; it is in the lines of both Splitters, so
    which line runs it is not clear: Validate refuses it, naming the block and a connection from each."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "sales": splitter(y=-300), "costs": splitter(y=300),
        "a1": step("a1", y=-400), "a2": step("a2", y=-200), "b1": step("b1", y=200),
        "b1x": python("output = input", ("input", "a2"), x=700, y=200), "b2": step("b2", y=400),
        "sales_merge": merger(x=1000, y=-300), "costs_merge": merger(x=1000, y=300),
    }, [*fed("sales"), "load.output -> costs.value", *lines_into("sales", "a1", "a2"), *lines_into("costs", "b1", "b2"),
        "b1.output -> b1x.input", "a2.output -> b1x.a2", *into_merger("sales_merge", "a1", "a2"),
        *into_merger("costs_merge", "b1x", "b2")])

    checked = env.taskloom("validate", flow)

    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert [line for line in checked.stderr.splitlines() if line.startswith("error: b1x takes from the lines of")
            and "a2.output -> b1x.a2" in line and "b1.output -> b1x.input" in line], checked.stderr


def test_validate_refuses_the_lines_of_three_splitters_feeding_each_other_in_a_circle(env):
    """Spec 7 and the no-surprises rule: x's lines feed y's, y's feed z's and z's feed x's: they could wait for each
    other forever; Validate refuses it, naming the three connections."""
    blocks, edges = {"manual": MANUAL, "load": LOAD}, ["manual.start -> load.after"]
    circle = {"x": "y", "y": "z", "z": "x"}
    for n, (s, to) in enumerate(circle.items()):
        blocks |= {s: splitter(ONE_AFTER_ANOTHER, y=n * 600), f"{s}1": python("output = 1", ("input", "other"),
                   y=n * 600 - 100), f"{s}2": step(f"{s}2", y=n * 600 + 100), f"{s}_merge": merger(x=900, y=n * 600)}
        edges += [f"load.output -> {s}.value", *lines_into(s, f"{s}1", f"{s}2"),
                  *into_merger(f"{s}_merge", f"{s}1", f"{s}2"), f"{s}2.output -> {to}1.other"]
    flow = flow_file(env.root / "flow.yaml", blocks, edges)

    checked = env.taskloom("validate", flow)

    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert [line for line in checked.stderr.splitlines() if line.startswith("error: the lines of")
            and all(f"{s}2.output -> {to}1.other" in line for s, to in circle.items())], checked.stderr
