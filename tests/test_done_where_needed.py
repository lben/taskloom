"""The `done` output only where a block can succeed without delivering any of its outputs: If, Python Code, reusable
blocks, a block whose ports declare no outputs, and a block class that sets has_done = True. On the others (Run Query,
Chart, ...) one of their outputs, which always arrives, is connected to the next block's `run after` instead. Through
the block API plugin authors use, the real `taskloom` command line and the offscreen editor."""

import os
import shutil

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextDocument  # noqa: E402

from conftest import REPO, events  # noqa: E402
from taskloom.block import all_ports  # noqa: E402
from taskloom.blocks.logic import write_reusable_block  # noqa: E402
from taskloom.flow import load_flow  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_editor import drag_port_to_port  # noqa: E402
from tests.test_paths_editor_editing import clean, error_shown, field, select_only, settle, type_in  # noqa: E402

# Spec §4, written out rather than computed, so a wrong declaration on a block is caught.
KEEP = ["logic.if", "logic.python"]
LOSE = ["data.duckdb_sql", "data.polars", "data.read_file", "data.write_file", "db.run_query", "files.copy",
        "files.move", "files.delete", "logic.wait", "logic.ask_user", "logic.for_each", "logic.inspect",
        "reports.html_table", "reports.chart", "reports.html_template", "reports.excel", "email.send",
        "servers.ssh_command", "servers.start_process", "servers.stop_process", "servers.health_check",
        "servers.remote_extract"]
START = ["trigger.manual", "trigger.schedule"]
RUN_AFTER_TIP = ("Run after: connect an output of another block (or a start block's 'start') here to make this block "
                 "wait for it. Its value is not used.")
DONE_TIP = ("Done: fires when this block has finished successfully, even if it produced none of its other outputs. "
            "Connect it to another block's 'run after'. No data goes out through here.")
WITHHELD = "held a password (or something that cannot be saved), so it was not kept"


def ports_of(cls) -> tuple[list, list]:
    """The input and output names of a block as the editor adds it: its settings' defaults, then its new_config."""
    config = {**{name: f.default for name, f in cls.config.items()}, **cls.new_config}
    inputs, outputs = all_ports(cls, config)
    return list(inputs), list(outputs)


def write_flow(path, blocks: dict, edges=()):
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": list(edges)}, sort_keys=False), encoding="utf-8")
    return path


def python(code: str, outputs=(), inputs=()) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code}}


def writes(name: str) -> dict:
    """A Python Code block that adds 'ran ' to the file `name` (in the folder the run starts in)."""
    return python(f"open({name!r}, 'a', encoding='utf-8').write('ran ')")


def text(path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def blocks_of(env, run_id: int) -> dict:
    """{block: (status, summary)} of a run, from the history."""
    return {b: (s, m) for b, s, m in env.query("SELECT block, status, summary FROM blocks WHERE run_id=?", run_id)}


def runs_recorded(env) -> int:
    return env.query("SELECT count(*) FROM runs")[0][0] if (env.home / "history.db").exists() else 0


def plain(html_text: str) -> str:
    doc = QTextDocument()
    doc.setHtml(html_text)
    return doc.toPlainText()


MANUAL = {"type": "trigger.manual"}


# --- which blocks have done (§1, §2, §4) ------------------------------------------------------------------------

@pytest.mark.parametrize("type_id", KEEP + LOSE + START)
def test_a_built_in_block_has_done_only_when_it_can_succeed_without_its_outputs(tmp_path, type_id):
    """Spec §4 (T1), through taskloom.block.all_ports with the registry from discover(home.blocks_dir)."""
    cls = discover(tmp_path / "blocks").get(type_id)
    inputs, outputs = ports_of(cls)

    if type_id in START:
        assert "done" not in outputs and "after" not in inputs, (inputs, outputs)
        return
    assert "after" in inputs, inputs
    if type_id in KEEP:
        assert outputs[0] == "done", outputs
    else:
        assert "done" not in outputs, outputs
        assert outputs, outputs  # an output that always arrives, for the next block's run after


def test_on_the_canvas_only_if_and_python_code_of_the_built_in_blocks_show_done(win):
    """Spec §4 and §7 (T1): blocks that lose done show no done row; If and Python Code show it as their first output.
    The lists above name every built-in block."""
    builtin = sorted(t for t, source in win.registry.sources.items() if source == "built-in")
    assert builtin == sorted(KEEP + LOSE + START)
    tab = win.new_flow()
    added = {type_id: tab.doc.add_block(type_id, 0, 0) for type_id in KEEP + LOSE + START}
    settle()
    clean(win)  # nothing to save when the window closes

    for type_id, block in added.items():
        node = tab.scene.nodes[block]
        if type_id in START:
            assert "done" not in node.outputs and "after" not in node.inputs, (type_id, node.inputs, node.outputs)
        elif type_id in KEEP:
            assert node.outputs[0] == "done" and node.inputs[0] == "after", (type_id, node.outputs)
        else:
            assert "done" not in node.outputs and ("done", True) not in node.ports, (type_id, node.outputs)
            assert node.inputs[0] == "after", (type_id, node.inputs)


BLOCK_FILES = {
    "with outputs": ("""
from taskloom import Block, ports


class Load(Block):
    type_id = "test.load"
    description = "Loads a value."
    outputs = {"value": ports.Any()}

    def run(self, ctx):
        return {"value": 1}
""", "test.load", False),
    "without outputs": ("""
from taskloom import Block


class Notify(Block):
    type_id = "test.notify"
    description = "Tells someone; gives nothing back."
    outputs = {}

    def run(self, ctx):
        return {}
""", "test.notify", True),
    "with has_done": ("""
from taskloom import Block, ports


class Maybe(Block):
    type_id = "test.maybe"
    description = "Gives a value only sometimes."
    outputs = {"value": ports.Any()}
    has_done = True

    def run(self, ctx):
        return {}
""", "test.maybe", True),
}


@pytest.mark.parametrize("case", BLOCK_FILES)
def test_a_block_file_of_your_own_has_done_only_with_has_done_or_without_outputs(env, case):
    """Spec §1-§2 (T2): a hand-written block file in the blocks folder."""
    source, type_id, has_done = BLOCK_FILES[case]
    env.write(f"home/blocks/{type_id.replace('.', '_')}.py", source)
    registry = discover(env.home / "blocks")
    assert registry.warnings == [], registry.warnings

    inputs, outputs = ports_of(registry.get(type_id))

    assert "after" in inputs
    assert ("done" in outputs) is has_done, outputs
    if has_done:
        assert outputs[0] == "done"


PARTIAL_BLOCKS = """
from taskloom import Block, ports


class Partial(Block):
    type_id = "test.partial"
    description = "Gives its value but not its extra."
    outputs = {"value": ports.Any(), "extra": ports.Any()}

    def run(self, ctx):
        return {"value": 1}


class Maybe(Block):
    type_id = "test.maybe_none"
    description = "Gives nothing this time."
    outputs = {"value": ports.Any()}
    has_done = True

    def run(self, ctx):
        return None


class Silent(Block):
    type_id = "test.silent"
    description = "Has no outputs."
    outputs = {}

    def run(self, ctx):
        return {}
"""


def test_a_block_of_your_own_waits_for_what_arrives_and_says_when_nothing_reached_its_run_after(env):
    """Edge paths: a block without has_done that returns only some outputs: what waits for a missing one is skipped,
    saying “nothing reached its run-after”; its value still sets the order. has_done = True, and a block with no
    outputs, fire done (§1, §3)."""
    env.write("home/blocks/partial.py", PARTIAL_BLOCKS)
    flow = write_flow(env.root / "flow.yaml", {
        "start": MANUAL, "partial": {"type": "test.partial"}, "maybe": {"type": "test.maybe_none"},
        "silent": {"type": "test.silent"},
        "after_value": writes("after_value.txt"), "after_extra": writes("after_extra.txt"),
        "after_maybe": writes("after_maybe.txt"), "after_silent": writes("after_silent.txt"),
    }, ["start.start -> partial.after", "start.start -> maybe.after", "start.start -> silent.after",
        "partial.value -> after_value.after", "partial.extra -> after_extra.after",
        "maybe.done -> after_maybe.after", "silent.done -> after_silent.after"])

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    run_id, _ = env.last_run()
    blocks = blocks_of(env, run_id)
    assert blocks["after_extra"] == ("skipped", "nothing reached its run-after"), blocks
    assert not (env.root / "after_extra.txt").exists()
    for block in ("after_value", "after_maybe", "after_silent"):
        assert blocks[block][0] == "success", (block, blocks)
        assert text(env.root / f"{block}.txt") == "ran ", block


def test_blocks_that_keep_done_fire_it_even_when_they_produce_no_output(env):
    """Spec §2 (T3) and the edge path “Python Code with outputs [] or with code that leaves its outputs unset: done
    still fires”: a block saved with Create Reusable block keeps done first and it fires, like Python Code's."""
    write_reusable_block(env.home / "blocks", "Quiet step", "Does nothing to its input.", "anything",
                         {"inputs": ["value"], "outputs": ["result"], "use_environment": False, "code": "pass"})
    registry = discover(env.home / "blocks")
    assert ports_of(registry.get("user.quiet_step"))[1][0] == "done"
    flow = write_flow(env.root / "flow.yaml", {
        "start": MANUAL, "reusable": {"type": "user.quiet_step"},
        "no_outputs": python("pass"), "unset": python("pass", outputs=["result"]),
        "after_reusable": writes("after_reusable.txt"), "after_no_outputs": writes("after_no_outputs.txt"),
        "after_unset": writes("after_unset.txt"),
    }, ["start.start -> reusable.after", "start.start -> no_outputs.after", "start.start -> unset.after",
        "reusable.done -> after_reusable.after", "no_outputs.done -> after_no_outputs.after",
        "unset.done -> after_unset.after"])

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    run_id, _ = env.last_run()
    assert set(env.block_status(run_id).values()) == {"success"}, env.block_status(run_id)
    for block in ("after_reusable", "after_no_outputs", "after_unset"):
        assert text(env.root / f"{block}.txt") == "ran ", block


# --- running after a data output (§5) -----------------------------------------------------------------------------

def test_a_block_runs_after_any_output_connected_to_its_run_after_without_getting_its_value(env):
    """Spec §5 (T4): Read File.table and Chart.image into Python Code's run after: each runs after its source and
    has no `after` variable; with Chart.image and another block's done into one run after it waits for both; an If's
    output that did not fire skips with “nothing reached its run-after”; Wait's value with nothing connected and
    Delete Files' deleted with nothing matching still arrive."""
    shutil.copytree(REPO / "examples" / "data", env.root / "data")
    has_after = "open({!r}, 'w', encoding='utf-8').write(repr('after' in globals()))"
    flow = write_flow(env.root / "flow.yaml", {
        "start": MANUAL,
        "read": {"type": "data.read_file", "config": {"path": "data/sales.csv"}},
        "chart": {"type": "reports.chart", "config": {"kind": "bar", "x": "region", "y": ["amount"]}},
        "after_read": python(has_after.format("after_read.txt")),
        "after_chart": python(has_after.format("after_chart.txt")),
        "after_both": writes("after_both.txt"),
        "check": {"type": "logic.if", "config": {"condition": "False"}},
        "on_true": writes("on_true.txt"),
        "pause": {"type": "logic.wait", "config": {"seconds": 0}},
        "clear": {"type": "files.delete", "config": {"path": "nothing_here/*.tmp"}},
        "after_clear": writes("after_clear.txt"),
    }, ["start.start -> read.after", "read.table -> chart.table", "read.table -> after_read.after",
        "chart.image -> after_chart.after", "after_read.done -> after_both.after", "chart.image -> after_both.after",
        "start.start -> check.after", "check.true -> on_true.after",
        "start.start -> pause.after", "pause.value -> clear.after", "clear.deleted -> after_clear.after"])

    result = env.taskloom("run", flow, "--json-events")

    assert result.returncode == 0, result.stderr
    run_id, _ = env.last_run()
    blocks = blocks_of(env, run_id)
    assert blocks.pop("on_true") == ("skipped", "nothing reached its run-after")
    assert {b: s for b, (s, _) in blocks.items()} == dict.fromkeys(blocks, "success"), blocks
    assert text(env.root / "after_read.txt") == "False" and text(env.root / "after_chart.txt") == "False"
    assert not (env.root / "on_true.txt").exists()
    assert text(env.root / "after_both.txt") == text(env.root / "after_clear.txt") == "ran "
    seen = [(e["event"], e.get("block")) for e in events(result.stdout)]
    for before, after in (("read", "after_read"), ("chart", "after_chart"), ("after_read", "after_both"),
                          ("chart", "after_both"), ("pause", "clear"), ("clear", "after_clear")):
        assert seen.index(("block_finished", before)) < seen.index(("block_started", after)), (before, after)


def test_run_query_and_chart_have_no_done_and_their_output_is_dragged_to_run_after(win):
    """The user's case on the canvas (spec §5, §7): Run Query and Chart show no done; their result and image connect
    to a block's run after by dragging, both into the same run after."""
    tab = win.new_flow()
    query = tab.doc.add_block("db.run_query", 0, 0)
    chart = tab.doc.add_block("reports.chart", 0, 160)
    after = tab.doc.add_block("logic.python", 260, 80)
    settle()
    try:
        assert "done" not in tab.scene.nodes[query].outputs and "done" not in tab.scene.nodes[chart].outputs
        assert tab.scene.nodes[query].outputs[0] == "result" and tab.scene.nodes[chart].outputs[0] == "image"

        drag_port_to_port(tab, query, "result", after, "after")
        settle()
        drag_port_to_port(tab, chart, "image", after, "after")
        settle()

        assert tab.doc.edges() == [(query, "result", after, "after"), (chart, "image", after, "after")]
    finally:
        clean(win)


# --- a flow that still connects a removed done (§9) ---------------------------------------------------------------

STALE_DONE = "edge read.done -> next.after: 'read' has no output 'done'"


def stale_done_flow(path):
    return write_flow(path, {
        "start": MANUAL, "read": {"type": "data.read_file", "config": {"path": "data/sales.csv"}},
        "next": writes("next.txt"),
    }, ["start.start -> read.after", "read.done -> next.after"])


def test_a_flow_that_still_connects_a_removed_done_is_refused_by_validate_and_run_and_check_offers_to_remove_it(env):
    """Spec §9 (T5): no migration; the existing validation reports it with exit code 2, before any run is recorded;
    taskloom check offers its existing fix, removing the connection."""
    flow = stale_done_flow(env.root / "flow.yaml")

    validated = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow)
    checked = env.taskloom("check", flow)

    assert validated.returncode == 2 and f"error: {STALE_DONE}" in validated.stderr, validated.stderr
    assert ran.returncode == 2 and f"error: {STALE_DONE}" in ran.stderr, ran.stderr
    assert runs_recorded(env) == 0 and not (env.root / "next.txt").exists()
    assert "remove the connection read.done -> next.after" in checked.stdout, checked.stdout


def test_the_editor_lists_a_removed_done_under_the_flow_has_problems_and_draws_no_line_for_it(win, tmp_path):
    """Spec §9 (T5): the editor's Run and Validate list it under “The flow has problems”; the canvas draws no line."""
    tab = win.open_flow(stale_done_flow(tmp_path / "stale.yaml"))
    settle()
    assert "read.done -> next.after" not in [e.text for e in tab.scene.edge_items]

    win.run_action.trigger()
    win.validate(tab)

    said = list(win.message_boxes)
    win.message_boxes.clear()
    assert [(kind, title) for kind, (title, _) in said] == [("warning", "The flow has problems")] * 2, said
    assert all(STALE_DONE in message for _, (_, message) in said), said
    assert not tab.runs


# --- re-runs with an output that was not kept (§6) ----------------------------------------------------------------

UNSAVABLE = python("import threading\nout = threading.Lock()", outputs=["out"])  # cannot be saved


def test_a_rerun_takes_an_output_that_was_not_kept_when_it_only_feeds_run_after(env):
    """Spec §6 (T6): run --only and run --from, from a run where src.out (a value that cannot be saved) went only into
    run after: next on its own, next and the block after it (which also waits for src.out), and that block on its
    own all run."""
    flow = write_flow(env.root / "flow.yaml", {
        "start": MANUAL, "src": UNSAVABLE, "next": writes("next.txt"), "last": writes("last.txt"),
    }, ["start.start -> src.after", "src.out -> next.after", "next.done -> last.after", "src.out -> last.after"])
    assert env.taskloom("run", flow).returncode == 0
    run_id, _ = env.last_run()

    only = env.taskloom("run", flow, "--only", "next", "--inputs-from", run_id)
    assert only.returncode == 0, only.stderr
    assert env.block_status(env.last_run()[0]) == {"next": "success"}
    from_next = env.taskloom("run", flow, "--from", "next", "--inputs-from", run_id)
    assert from_next.returncode == 0, from_next.stderr
    assert env.block_status(env.last_run()[0]) == {"next": "success", "last": "success"}
    only_last = env.taskloom("run", flow, "--only", "last", "--inputs-from", run_id)
    assert only_last.returncode == 0, only_last.stderr

    assert text(env.root / "next.txt") == "ran " * 3 and text(env.root / "last.txt") == "ran " * 3


def test_a_rerun_still_refuses_an_output_that_was_not_kept_when_it_also_feeds_a_data_input(env):
    """Spec §6 (T6, negative): the same output into run after and into a data input of a block in the re-run: refused
    with the existing message, by run --only that block and by run --from a block before it."""
    flow = write_flow(env.root / "flow.yaml", {
        "start": MANUAL, "src": UNSAVABLE, "next": writes("next.txt"),
        "last": python("open('last.txt', 'a', encoding='utf-8').write('ran ')", inputs=["value"]),
    }, ["start.start -> src.after", "src.out -> next.after", "next.done -> last.after", "src.out -> last.after",
        "src.out -> last.value"])
    assert env.taskloom("run", flow).returncode == 0
    run_id, _ = env.last_run()

    only = env.taskloom("run", flow, "--only", "last", "--inputs-from", run_id)
    from_next = env.taskloom("run", flow, "--from", "next", "--inputs-from", run_id)

    for refused in (only, from_next):
        assert refused.returncode == 2 and f"src.out {WITHHELD}" in refused.stderr, refused.stderr
    assert runs_recorded(env) == 1
    assert text(env.root / "next.txt") == text(env.root / "last.txt") == "ran "


def test_a_password_answer_connected_to_run_after_feeds_a_full_run_and_reruns(env):
    """Spec §6 and its edge path: an Ask User password answer into run after works in full runs and in re-runs (it
    used to go through ask.done)."""
    env.write("home/secrets.yaml", "pw: hunter2SECRET\n").chmod(0o600)
    flow = write_flow(env.root / "flow.yaml", {
        "start": MANUAL,
        "ask": {"type": "logic.ask_user", "config": {"prompt": "Password?", "kind": "password", "secret": "pw"}},
        "use": writes("use.txt"),
    }, ["start.start -> ask.after", "ask.answer -> use.after"])
    full = env.taskloom("run", flow)
    assert full.returncode == 0, full.stderr
    run_id, _ = env.last_run()

    only = env.taskloom("run", flow, "--only", "use", "--inputs-from", run_id)
    from_use = env.taskloom("run", flow, "--from", "use", "--inputs-from", run_id)

    assert only.returncode == 0, only.stderr
    assert from_use.returncode == 0, from_use.stderr
    assert text(env.root / "use.txt") == "ran " * 3


# --- the editor's words (§7, §8) ----------------------------------------------------------------------------------

def test_naming_a_python_code_output_done_or_error_says_this_block_already_has_these(win):
    """Spec §8 (T7): done and error stay reserved output names of Python Code, refused as “this block already has
    these”."""
    tab = win.new_flow()
    block = tab.doc.add_block("logic.python", 0, 0)
    data = yaml.safe_dump(tab.doc.data)

    try:
        for name in ("done", "error"):
            select_only(tab, block)
            type_in(field(win, "Outputs"), name)
            assert "this block already has these" in error_shown(win), (name, error_shown(win))
            assert yaml.safe_dump(tab.doc.data) == data, name
    finally:
        clean(win)


def test_hovering_run_after_and_done_says_what_they_do(win):
    """Spec §7 (T8): the tooltip of run after on any block, and of done on If and Python Code."""
    tab = win.new_flow()
    blocks = [tab.doc.add_block(type_id, 0, 0) for type_id in ("db.run_query", "reports.chart", "logic.python",
                                                                 "logic.if")]
    settle()
    clean(win)  # nothing to save when the window closes

    for block in blocks:
        tip = plain(tab.scene.nodes[block].ports[("after", False)].toolTip())
        assert RUN_AFTER_TIP in tip, (block, tip)
    for block in blocks[2:]:
        tip = plain(tab.scene.nodes[block].ports[("done", True)].toolTip())
        assert DONE_TIP in tip, (block, tip)


# --- the examples (§10) -------------------------------------------------------------------------------------------

def test_the_deploy_runner_example_keeps_its_order_through_outputs_that_always_arrive():
    """Spec §10 (T9): upload after stop_old, extract after upload, start after extract, running after start, each
    through an output that always arrives. (That every example validates is
    test_paths_flow_format.py::test_each_example_is_valid_with_the_connections_its_comments_say_it_needs...)"""
    flow = load_flow(REPO / "examples" / "deploy-runner.yaml")
    order = {(e.src, e.src_port, e.dst) for e in flow.edges
             if e.dst_port == "after" and e.dst in ("upload", "extract", "start", "running")}

    assert order == {("stop_old", "stopped", "upload"), ("upload", "files", "extract"), ("extract", "folder", "start"),
                     ("start", "pid", "running")}
