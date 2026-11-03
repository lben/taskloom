"""Stop while the lines into a Merger set to each line as it finishes are still running, with no run after the Merger
going yet: the run ends at once, cancelled, the Merger with it (it does not wait forever for lines that were stopped).
Through `taskloom run --json-events --interactive`, the channel the editor's Stop goes through."""

import time

from conftest import events
from test_paths_engine import Running, write


def test_stop_while_the_lines_run_ends_the_run_cancelled_with_the_merger(env):
    flow = write(env, """
        blocks:
          manual: {type: trigger.manual}
          load: {type: logic.python, config: {inputs: [], code: "output = 'today'"}}
          split: {type: logic.splitter}
          load_sales: {type: logic.wait, config: {seconds: 60}}
          load_costs: {type: logic.wait, config: {seconds: 60}}
          merge: {type: logic.merger, config: {passes_on: each line as it finishes}}
          report: {type: logic.python, config: {outputs: [], code: "print(input)"}}
        edges:
          - manual.start -> load.after
          - load.output -> split.value
          - split.lines -> load_sales.value
          - split.lines -> load_costs.value
          - load_sales.value -> merge.lines
          - load_costs.value -> merge.lines
          - merge.value -> report.input
    """)
    with Running(env, flow, "--json-events", "--interactive") as run:
        run.wait_for("waiting 60 s")  # the lines run
        run.wait_for("each passed on as it finishes")  # the Merger has started: it waits for its lines
        stopped = time.monotonic()
        run.send({"cancel": True})  # what the editor's Stop sends

        assert run.finish() == 130
        assert time.monotonic() - stopped < 30  # not the 60 s the lines would wait, nor forever
    ended = {e["block"]: e["status"] for e in events("".join(run.out)) if e["event"] == "block_finished"}
    assert ended.get("merge") == "cancelled", ended
    assert [e["status"] for e in events("".join(run.out)) if e["event"] == "run_finished"] == ["cancelled"]
    assert env.last_run()[1] == "cancelled"  # (the run history)
