"""A safety net for a run that can no longer go on: while a run goes on, once no block is running and every block still
to run waits for something no running or startable block can bring (a line slot that is never freed, an input from a
block that cannot start), the run ends within about a second: each waiting block ends failed (or skipped) saying what
it waited for, the Log says "the run cannot go on: X waits for Y (...), ...", and the run fails (exit 1 from taskloom
run). A run that is only slow is never ended by it, and a run that is not stuck behaves as before. Through the real
`taskloom run` command (with --json-events, as the scheduler service and the editor run it) and its run history
(history.db). Each test names the point of the specification (1-4) it protects.

Validate refuses the layouts of lines known to never finish, so (spec 3) the stuck runs are reached by `taskloom run`
with that check's errors left out (taskloom.flow._lines_problems patched in the run's own process, its warnings kept):
the run itself goes as it would. Every run has a deadline: one still going after it waits forever (it is killed and the
test fails)."""

import datetime as dt
import json
import re
import subprocess
import sys
import threading
import time

import pytest

from conftest import events
from tests.test_loop_container import inside, loop, make
from tests.test_parallel_lines import (MANUAL, ONE_AFTER_ANOTHER, TICK, into_merger, lines_into, merger, merger_out,
                                       python, saver, spans, splitter, step)
from tests.test_paths_editor_editing import flow_file

DEADLINE = 30  # seconds: these runs take a few; one still going then waits forever
WITHIN = 2.5  # seconds: a stuck run ends "within about a second" of getting stuck (with room for a slow machine)
SLOW = 3.0  # seconds a slow block (or nobody answering) takes: well over the second after which a stuck run ends
ON_TIME = 1.0  # seconds: in a run that is not stuck, a block that waited starts this soon after what it waited for
# `taskloom run` with Validate's check of the lines' layouts giving no errors (its warnings kept), in the run's process.
UNCHECKED = ("import sys, taskloom.flow as f; check = f._lines_problems; "
             "f._lines_problems = lambda *a: ([], check(*a)[1]); from taskloom.cli import run_cli; run_cli(sys.argv[1:])")


# --- running, and reading the run history ------------------------------------------------------------------------------

# The same, also without the errors of Validate's check of the blocks a Merger runs once per line.
UNCHECKED_PER_LINE = UNCHECKED.replace(
    "from taskloom.cli", "per_line = f._per_line_problems; f._per_line_problems = lambda *a: ([], per_line(*a)[1]); "
                         "from taskloom.cli")


def command(flow, *args, unchecked: bool | str) -> list[str]:
    how = ["-c", unchecked if isinstance(unchecked, str) else UNCHECKED] if unchecked else ["-m", "taskloom.cli"]
    return [sys.executable, *how, "run", str(flow), "--json-events", *map(str, args)]


def text(output) -> str:
    return output.decode("utf-8", "replace") if isinstance(output, bytes) else output or ""


def run(env, flow, *args, unchecked: bool | str = True) -> subprocess.CompletedProcess:
    """`taskloom run --json-events`, which must end before DEADLINE (still going then, it is killed)."""
    try:
        return subprocess.run(command(flow, *args, unchecked=unchecked), env=env.env, cwd=env.root,
                              capture_output=True, text=True, timeout=DEADLINE)
    except subprocess.TimeoutExpired as e:
        told = [line for line in text(e.stdout).splitlines() if line.startswith("{")][-6:]
        pytest.fail(f"the run was still going after {DEADLINE} s: it waits forever. Its last events: {told} "
                    f"{text(e.stderr)[-1500:]}")


def results(env, run_id: int) -> dict:
    """{block: (its status, its error and summary)}, as the Run history shows each block's result."""
    return {b: (s, f"{e or ''} {m or ''}".strip()) for b, s, e, m in
            env.query("SELECT block, status, error, summary FROM blocks WHERE run_id=?", run_id)}


def cannot_go_on(env, run_id: int) -> tuple[list[str], dict]:
    """The run's Log lines saying "the run cannot go on: X waits for Y (...), Z waits for ...", and what they say each
    block waits for: {X: "Y (...)", Z: ...} (a block inside a Loop may carry its item's tag, X [item 1/1])."""
    lines = [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? ORDER BY rowid", run_id)
             if "the run cannot go on" in m]
    said = {}
    for line in lines:
        parts = line.split(" waits for ")
        for k, (before, after) in enumerate(zip(parts, parts[1:])):
            if k + 2 < len(parts):  # (up to the next block it names)
                after = re.sub(r"\w+(?: \[item \d+/\d+\])?\s*$", "", after)
            if named := re.search(r"(\w+)(?: \[item \d+/\d+\])?\s*$", before):
                said[named.group(1)] = after
    return lines, said


def when(told: list[dict], kind: str, block: str | None = None) -> dt.datetime:
    """When the run first told an event `kind` (of `block`)."""
    found = [e["ts"] for e in told if e["event"] == kind and (block is None or e.get("block") == block)]
    assert found, f"the run told no {kind} of {block}"
    return dt.datetime.fromisoformat(found[0])


def ends_saying_what_waits(env, done, since, ran, waiting: dict, rest=()) -> int:
    """Spec 1: a run that could no longer go on once the blocks `since` had finished ended within about a second of
    that, failed (exit 1); the blocks `ran` succeeded; each block of `waiting` ({block: what it waits for, or None})
    ended failed or skipped, its result saying it waited (for that), and the Log line names it: X waits for Y; each of
    `rest` ended failed or skipped (none is left running or unfinished). Returns the run's number."""
    told = events(done.stdout)
    run_id, status = env.last_run()
    assert (done.returncode, status) == (1, "failed"), done.stderr[-3000:] + done.stdout[-3000:]
    stuck = max(when(told, "block_finished", b) for b in since)
    took = (when(told, "run_finished") - stuck).total_seconds()
    assert took <= WITHIN, f"the run ended {took:.2f} s after it could no longer go on"
    ended = results(env, run_id)
    lines, said = cannot_go_on(env, run_id)
    assert lines, f"no Log line says the run cannot go on: {ended}"
    problems = [f"{b} ended {ended.get(b)}" for b in ran if ended.get(b, ("",))[0] != "success"]
    for block, what in waiting.items():
        status, result = ended.get(block, ("not ended", ""))
        if status not in ("failed", "skipped"):
            problems.append(f"{block} ended {status}: {result}")
        if "wait" not in result.lower() or (what is not None and what not in result):
            problems.append(f"{block}'s result does not say it waited for {what or 'something'}: {result!r}")
        if block not in said:
            problems.append(f"the Log does not say {block} waits for something: {lines}")
        elif what is not None and what not in said[block]:
            problems.append(f"the Log says {block} waits for {said[block]!r}, not for {what}: {lines}")
    problems += [f"{b} ended {ended.get(b)}" for b in rest if ended.get(b, ("not ended",))[0] not in ("failed", "skipped")]
    assert problems == [], problems
    return run_id


# --- 1: a run that can no longer go on fails at once, saying which blocks wait for what ------------------------------

def scheduled_flow(folder):
    """The user's case: a scheduled run (Schedule nightly) of a Splitter running its lines one after another, whose
    first line's load_sales also needs what the third line's load_stock makes: line 1 waits for line 3, which cannot
    start until line 1 has ended."""
    return flow_file(folder / "flow.yaml", {
        "nightly": {"type": "trigger.schedule", "config": {"cron": "0 7 * * *"}, "ui": {"x": -400, "y": 0}},
        "load": make(["monday"], x=-200), "split": splitter(ONE_AFTER_ANOTHER),
        "load_sales": python("output = [input, stock]", ("input", "stock"), x=400, y=-300),
        "load_costs": step("load_costs", y=0), "load_stock": step("load_stock", y=300),
        "merge": merger(x=800), "report": saver("report.json"),
    }, ["nightly.start -> load.after", "load.output -> split.value",
        *lines_into("split", "load_sales", "load_costs", "load_stock"), "load_stock.output -> load_sales.stock",
        *into_merger("merge", "load_sales", "load_costs", "load_stock"), merger_out("merge", "report")])


def test_a_scheduled_run_whose_lines_would_wait_forever_fails_at_once_saying_which_blocks_wait_for_what(env):
    """Spec 1 (the user's case, a scheduled run that would never end): once the Splitter has started its lines, no block
    runs and none can: load_sales waits for load_stock, an input from a block that cannot start; load_costs and
    load_stock wait for a line slot that is never freed. Within about a second the run ends: each of them failed or
    skipped saying what it waited for, the Log says the run cannot go on and which blocks wait for what, the Merger and
    the report after it do not run, and the run fails: exit 1."""
    flow = scheduled_flow(env.root)

    done = run(env, flow, "--trigger", "nightly")

    ends_saying_what_waits(env, done, since=["split"], ran=["nightly", "load", "split"],
                           waiting={"load_sales": "load_stock", "load_costs": None, "load_stock": None},
                           rest=["merge", "report"])
    assert not (env.root / "report.json").exists()


def test_two_splitters_whose_lines_would_wait_for_each_other_forever_fail_the_run_at_once(env):
    """Spec 1 with the lines of two Splitters side by side feeding each other both ways, each running its lines one
    after another: east's line 1 waits for west's line 2, west's line 1 for east's line 2, and neither line 2 can start
    until the line 1 before it has ended. Within about a second the run ends, failed, saying which blocks wait for
    what."""
    def needs_other(y):
        return python("output = [input, other]", ("input", "other"), x=400, y=y)

    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": make(["monday"], x=-200),
        "east": splitter(ONE_AFTER_ANOTHER, y=-300), "west": splitter(ONE_AFTER_ANOTHER, y=300),
        "east_1": needs_other(-400), "east_2": step("east_2", y=-200),
        "west_1": needs_other(200), "west_2": step("west_2", y=400),
        "merge_east": merger(x=800, y=-300), "merge_west": merger(x=800, y=300),
        "report": python("import json\nwith open('report.json', 'w', encoding='utf-8') as f:\n"
                         "    json.dump([east, west], f)", ("east", "west"), (), x=1200),
    }, ["manual.start -> load.after", "load.output -> east.value", "load.output -> west.value",
        *lines_into("east", "east_1", "east_2"), *lines_into("west", "west_1", "west_2"),
        "west_2.output -> east_1.other", "east_2.output -> west_1.other",
        *into_merger("merge_east", "east_1", "east_2"), *into_merger("merge_west", "west_1", "west_2"),
        merger_out("merge_east", "report", "east"), merger_out("merge_west", "report", "west")])

    done = run(env, flow)

    ends_saying_what_waits(env, done, since=["east", "west"], ran=["manual", "load", "east", "west"],
                           waiting={"east_1": "west_2", "west_1": "east_2", "east_2": None, "west_2": None},
                           rest=["merge_east", "merge_west", "report"])
    assert not (env.root / "report.json").exists()


def test_an_item_of_a_loop_whose_lines_would_wait_forever_fails_the_loop_and_the_run_at_once(env):
    """Spec 1 inside a Loop: for its item, the blocks inside run the user's case (a Splitter running its lines one after
    another, line 1 needing what line 3 makes). The Loop is under way, but no block runs and none can: within about a
    second the item's waiting blocks end failed or skipped saying what they waited for, the Log says the run cannot go
    on, the Loop fails, the report after it does not run, and the run fails: exit 1."""
    def into(spec):
        return inside("each", spec)

    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "make": make(["monday"]), "each": loop(),
        "split": into(splitter(ONE_AFTER_ANOTHER, x=60, y=0)),
        "load_sales": into(python("output = [input, stock]", ("input", "stock"), x=360, y=-200)),
        "load_costs": into(step("load_costs", x=360, y=0)), "load_stock": into(step("load_stock", x=360, y=200)),
        "merge": into(merger(x=660)), "report": saver("report.json"),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> split.value",
        *lines_into("split", "load_sales", "load_costs", "load_stock"), "load_stock.output -> load_sales.stock",
        *into_merger("merge", "load_sales", "load_costs", "load_stock"), merger_out("merge", "each", "result"),
        "each.results -> report.input"])

    done = run(env, flow)

    run_id = ends_saying_what_waits(env, done, since=["split"], ran=["manual", "make"],
                                    waiting={"load_sales": "load_stock", "load_costs": None, "load_stock": None},
                                    rest=["merge", "report"])
    assert results(env, run_id)["each"][0] == "failed", results(env, run_id)
    assert not (env.root / "report.json").exists()


# --- 2, 4: a run that is only slow is never ended by it, and goes as before ------------------------------------------

def waiting_flow(folder, slow_id: str, slow: dict, port_in="input", port_out="output", more=None, edges=()):
    """Lines at the same time: the top line's load_sales also needs what the bottom line's first block, `slow_id`
    (`slow`, with the input and output `port_in` and `port_out`), gives; load_costs, in the middle, is quick."""
    return flow_file(folder / "flow.yaml", {
        "manual": MANUAL, "load": make(["monday"], x=-200), "split": splitter(),
        "load_sales": python("output = [input, other]", ("input", "other"), x=400, y=-300),
        "load_costs": step("load_costs", y=0), slow_id: slow, **(more or {}),
        "merge": merger(x=1200), "report": saver("report.json"),
    }, ["manual.start -> load.after", "load.output -> split.value", *lines_into("split", "load_sales", "load_costs"),
        f"split.lines -> {slow_id}.{port_in}", f"{slow_id}.{port_out} -> load_sales.other",
        *into_merger("merge", "load_sales", "load_costs"), f"{slow_id}.{port_out} -> merge.lines",
        merger_out("merge", "report"), *edges])


def went_on(env, done, waited_for: list[tuple[str, str]], slow: str) -> int:
    """Spec 2 and 4: the run was not ended: it succeeded (every block), its Log never says it cannot go on, `slow`
    really took its time, and each block of `waited_for` ((block, what it waited for)) started as soon as that had
    finished, as before. Returns the run's number."""
    run_id, status = env.last_run()
    assert (done.returncode, status) == (0, "success"), done.stderr[-3000:] + done.stdout[-3000:]
    assert set(env.block_status(run_id).values()) == {"success"}, env.block_status(run_id)
    assert cannot_go_on(env, run_id)[0] == []
    t = spans(env, run_id)
    assert (t[slow][1] - t[slow][0]).total_seconds() >= SLOW - 0.5, t  # (it was slow: the case did happen)
    for block, what in waited_for:
        waited = (t[block][0] - t[what][1]).total_seconds()
        assert -TICK <= waited <= ON_TIME, f"{block} started {waited:.2f} s after {what} finished: {t}"
    return run_id


@pytest.mark.parametrize("case", ["a_block_running", "a_wait_block", "a_retry_after_a_failed_attempt",
                                  "a_loop_running_its_item"])
def test_a_line_waiting_for_another_line_that_is_slow_is_not_ended(env, case):
    """Spec 2 (a run that is only slow is never ended: a block running long, a Wait block, a block waiting to retry
    after a failed attempt, a Loop whose item runs long) and 4 (it goes as before): for SLOW seconds nothing runs but
    the bottom line's load_stock, and load_sales, in the top line, waits for it; the run succeeds, load_sales starting
    as soon as load_stock has finished."""
    port_in, port_out, more, edges = "input", "output", None, ()
    if case == "a_block_running":
        slow = step("load_stock", SLOW, y=300)
    elif case == "a_wait_block":
        slow, port_in, port_out = {"type": "logic.wait", "config": {"seconds": SLOW}, "ui": {"x": 400, "y": 300}}, \
            "value", "value"
    elif case == "a_retry_after_a_failed_attempt":
        slow = {**python("if ctx.attempt == 1:\n    raise ConnectionError('the stock server is busy')\n"
                         "output = 'load_stock'", x=400, y=300),
                "retry": {"policy": "fixed", "delay": f"{SLOW:g}s", "max_attempts": 2}}
    else:
        slow, port_in, port_out = loop(y=300), "items", "results"
        more = {"stock_item": inside("load_stock", step("stock_item", SLOW, x=460, y=400))}
        edges = ["load_stock.item -> stock_item.input", "stock_item.output -> load_stock.result"]
    flow = waiting_flow(env.root, "load_stock", slow, port_in, port_out, more, edges)

    done = run(env, flow, unchecked=False)

    went_on(env, done, [("load_sales", "load_stock")], slow="load_stock")


def test_a_line_waiting_for_an_answer_asked_in_another_line_is_not_ended(env):
    """Spec 2 (Ask User waiting for an answer is only slow) and 4: run from the editor's channel (--interactive), the
    bottom line asks a question nobody answers for SLOW seconds while load_sales, in the top line, waits for the
    answer; then it is answered: the run succeeds, load_sales starting as soon as the answer came."""
    flow = waiting_flow(env.root, "ask_region", {"type": "logic.ask_user", "config": {"prompt": "Which region?"},
                                                 "ui": {"x": 400, "y": 300}}, "value", "answer")
    proc = subprocess.Popen(command(flow, "--interactive", unchecked=False), env=env.env, cwd=env.root,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = [], []

    def read(stream, lines):
        for line in stream:
            lines.append(line)

    readers = [threading.Thread(target=read, args=pair, daemon=True) for pair in ((proc.stdout, out), (proc.stderr, err))]
    for reader in readers:
        reader.start()
    try:
        deadline = time.monotonic() + DEADLINE
        while not (asked := [e for e in events("".join(out)) if e["event"] == "ask"]):
            assert proc.poll() is None and time.monotonic() < deadline, \
                f"no question was asked: {''.join(out)[-2000:]} {''.join(err)[-2000:]}"
            time.sleep(0.05)
        time.sleep(SLOW)  # nobody answers for a while
        proc.stdin.write(json.dumps({"ask_id": asked[0]["ask_id"], "value": "north"}) + "\n")
        proc.stdin.flush()
        try:
            code = proc.wait(timeout=DEADLINE)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the run was still going {DEADLINE} s after the answer: {''.join(out)[-2000:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
    for reader in readers:
        reader.join(timeout=10)

    went_on(env, subprocess.CompletedProcess(proc.args, code, "".join(out), "".join(err)),
            [("load_sales", "ask_region")], slow="ask_region")


def test_lines_waiting_for_their_turn_behind_a_slow_line_one_after_another_are_not_ended(env):
    """Spec 2 (a line waiting for another line that is running) and 4: one after another, line 1's load_sales runs for
    SLOW seconds while lines 2 and 3 wait for their turn; the run succeeds, each line starting as soon as the one
    before it has ended."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": make(["monday"], x=-200), "split": splitter(ONE_AFTER_ANOTHER),
        "load_sales": step("load_sales", SLOW, y=-300), "load_costs": step("load_costs", y=0),
        "load_stock": step("load_stock", y=300), "merge": merger(x=800), "report": saver("report.json"),
    }, ["manual.start -> load.after", "load.output -> split.value",
        *lines_into("split", "load_sales", "load_costs", "load_stock"),
        *into_merger("merge", "load_sales", "load_costs", "load_stock"), merger_out("merge", "report")])

    done = run(env, flow, unchecked=False)

    went_on(env, done, [("load_costs", "load_sales"), ("load_stock", "load_costs")], slow="load_sales")


def test_a_line_waiting_for_a_line_that_starts_once_a_slow_line_ends_is_not_ended(env):
    """Spec 2 (only what no running or startable block can bring makes a run stuck) and 4: at most 2 of its 3 lines at
    once, line 1's load_sales also needs what line 3's load_stock makes (a layout Validate refuses, left unchecked
    here); line 3 can start once line 2's load_costs, running for SLOW seconds, ends. So the run is only slow: it
    succeeds, line 3 starting as soon as line 2 has ended, and load_sales as soon as load_stock has finished."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": make(["monday"], x=-200), "split": splitter(at_most=2),
        "load_sales": python("output = [input, stock]", ("input", "stock"), x=400, y=-300),
        "load_costs": step("load_costs", SLOW, y=0), "load_stock": step("load_stock", y=300),
        "merge": merger(x=800), "report": saver("report.json"),
    }, ["manual.start -> load.after", "load.output -> split.value",
        *lines_into("split", "load_sales", "load_costs", "load_stock"), "load_stock.output -> load_sales.stock",
        *into_merger("merge", "load_sales", "load_costs", "load_stock"), merger_out("merge", "report")])

    done = run(env, flow)

    went_on(env, done, [("load_stock", "load_costs"), ("load_sales", "load_stock")], slow="load_costs")


def test_a_run_stuck_outside_the_lines_fails_at_once_too(env):
    """Spec 1 ("nothing waits forever", whatever the layout): summary, after gather (which closes the runs merge makes
    once per line), also takes merge's value. With Validate's refusal of that left out, the blocks outside the lines
    wait for each other once the lines have ended; within about a second the run ends, failed, its Log saying which
    blocks wait for what, and no block is left running."""
    flow = env.root / "flow.yaml"
    flow.write_text("""blocks:
  manual: {type: trigger.manual}
  split: {type: logic.splitter}
  load_sales: {type: logic.python, config: {inputs: [input], code: "output = 'sales'"}}
  load_costs: {type: logic.python, config: {inputs: [input], code: "output = 'costs'"}}
  merge: {type: logic.merger, config: {passes_on: each line as it finishes}}
  report: {type: logic.python, config: {inputs: [input], code: "output = input.upper()"}}
  gather: {type: logic.merger}
  summary: {type: logic.python, config: {inputs: [input, extra], outputs: [], code: "print('summary', input, extra)"}}
edges:
- manual.start -> split.after
- split.lines -> load_sales.input
- split.lines -> load_costs.input
- load_sales.output -> merge.lines
- load_costs.output -> merge.lines
- merge.value -> report.input
- report.output -> gather.lines
- gather.value -> summary.input
- merge.value -> summary.extra
""", encoding="utf-8")

    done = run(env, flow, unchecked=UNCHECKED_PER_LINE)

    told = events(done.stdout)
    run_id, status = env.last_run()
    assert (done.returncode, status) == (1, "failed"), done.stderr[-3000:] + done.stdout[-3000:]
    lines, _ = cannot_go_on(env, run_id)
    assert lines, results(env, run_id)
    stuck = max(when(told, "block_finished", b) for b in ("load_sales", "load_costs"))
    assert (when(told, "run_finished") - stuck).total_seconds() <= WITHIN + 1.5, told[-5:]
    started = [e["block"] for e in told if e["event"] == "block_started"]
    finished = [e["block"] for e in told if e["event"] == "block_finished"]
    assert [b for b in set(started) if started.count(b) > finished.count(b)] == [], told
    ended = results(env, run_id)
    assert sorted(ended) == sorted(["manual", "split", "load_sales", "load_costs", "merge", "report", "gather",
                                    "summary"]), ended  # every block has a result, none is left without one
