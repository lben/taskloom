"""The Loop container (logic.loop), which replaces For Each (logic.for_each): a box drawn on the canvas around a set of
blocks, which run once for every item of a list, as a small flow of their own, one item at a time or several at the
same time ("At the same time", default 1: a sliding window); the Loop collects each item's result, in the list's order.
Through the real `taskloom` command (run, validate), its run history (history.db) and the offscreen editor. Each test
names the point of the specification (1-12) it protects.

What the tests take as given (the specification leaves it to the change, and the editor and the runner must agree on
it): the flow file records that a block is inside a Loop with the block key `inside: <the Loop's name>` (a Loop inside
another Loop has it too); the Loop's setting is found by the label the specification gives it ("At the same time"), its
ports by their names (items, after, results, done, error outside; item, index, result inside). On the canvas the Loop
is in the scene's nodes under its name, as any block, with its ports; its box is found as the canvas paints it (where
its grey background is), and its texts as Qt's SVG output records them. When items ran is read from what the blocks
inside write (the same clock, in the one run process)."""

import datetime as dt
import html
import json
import math
import os
import re
from collections import Counter
from functools import cache
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolTip  # noqa: E402

from conftest import events  # noqa: E402
from taskloom.block import setting_label  # noqa: E402
from taskloom.editor.canvas import FOOTER_H, HEADER_H, NODE_W, ROW_H, paint_icon  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_block_icons import (GROUP_COLORS, built_in, canvas_icons, has_icon, ink, pixels, rows,  # noqa: E402
                                     unlike)
from tests.test_editor import add_from_palette, wait_done  # noqa: E402
from tests.test_inspect_mermaid import contrast  # noqa: E402
from tests.test_parallel_lines import drawn_texts, entry, plain  # noqa: E402
from tests.test_paths_editor_editing import (clean, field, flow_file, menu, mouse_drag, saved,  # noqa: E402
                                             select_only, settle)
from tests.test_paths_editor_editing import taskloom as taskloom_cli  # noqa: E402
from tests.test_theme import TEXT, choose, most_common  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
LOOP = "logic.loop"
INSIDE = "inside"  # the block key with which the flow file records the Loop a block is inside
LOGIC = GROUP_COLORS["Logic"]
FOR_EACH_ICON = ("list", "badge-repeat")  # the For Each block's icon, the approved Loop icon


# --- the Loop and the blocks around it, as the flow file writes them ----------------------------------------------

@cache
def loop_class():
    cls = discover(None).get(LOOP)
    assert cls is not None, f"there is no {LOOP} block"
    return cls


def at_the_same_time() -> str:
    """The key, in the flow file, of the Loop's setting Properties shows as At the same time."""
    keys = {setting_label(key, f): key for key, f in loop_class().config.items()}
    assert "At the same time" in keys, f"the Loop has no setting At the same time (its settings: {sorted(keys)})"
    return keys["At the same time"]


def loop(same_time=None, x=0, y=0) -> dict:
    """A Loop; At the same time left out (None) as it is when the block is added."""
    config = {} if same_time is None else {at_the_same_time(): same_time}
    return {"type": LOOP, "config": config, "ui": {"x": x, "y": y}}


def inside(loop_id: str, spec: dict) -> dict:
    """The block `spec`, inside the Loop `loop_id`."""
    return {**spec, INSIDE: loop_id}


def python(code: str, inputs=("input",), outputs=("output",), x=0, y=0) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code},
            "ui": {"x": x, "y": y}}


def make(items, x=-450, y=0) -> dict:
    """A block that gives the list `items`."""
    return python(f"output = {items!r}", inputs=(), x=x, y=y)


def saver(filename: str, x=1400, y=0) -> dict:
    """A block that writes what it got (as JSON) to `filename`, and notes each time it runs (in ran_<its stem>.txt)."""
    return python(f"import json\nopen('ran_{Path(filename).stem}.txt', 'a', encoding='utf-8').write('x')\n"
                  f"with open({filename!r}, 'w', encoding='utf-8') as f:\n    json.dump(input, f)",
                  outputs=(), x=x, y=y)


MANUAL = {"type": "trigger.manual", "ui": {"x": -750, "y": 0}}


def per_item(folder: Path, items, code: str, same_time=None, inputs=("item", "index"), blocks=None, edges=()) -> Path:
    """manual -> make (the items) -> each (a Loop) -> report (writes the Loop's results to report.json); inside each:
    work (`code`, which gets the item and its index) -> each's result. `blocks`, `edges`: more of them."""
    return flow_file(folder / "flow.yaml", {
        "manual": MANUAL, "make": make(items), "each": loop(same_time),
        "work": inside("each", python(code, inputs, x=60, y=100)), "report": saver("report.json"), **(blocks or {}),
    }, ["manual.start -> make.after", "make.output -> each.items", *(f"each.{p} -> work.{p}" for p in inputs),
        "work.output -> each.result", "each.results -> report.input", *edges])


# A block inside that waits until go_<its item's index>.txt is there (60 s at most), saying so in the Log first.
GATED = ("import os, time\nprint(f'item {index} is waiting')\nfor _ in range(1200):\n"
         "    if os.path.exists(f'go_{index}.txt'):\n        break\n    time.sleep(0.05)\noutput = item")


def go(folder: Path, *indexes: int):
    for k in indexes:
        (folder / f"go_{k}.txt").write_text("", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def ran(folder: Path, name: str) -> int:
    """How many times the block (or the item) that notes itself in ran_<name>.txt ran."""
    path = folder / f"ran_{name}.txt"
    return len(path.read_text(encoding="utf-8")) if path.exists() else 0


def spans(folder: Path, n: int) -> dict[int, tuple[float, float]]:
    """{item index: (when it started, when it ended)}, as each item's block wrote them (span_<index>.json)."""
    return {k: tuple(read_json(folder / f"span_{k}.json")) for k in range(1, n + 1)}


# Writes when it started and ended (span_<index>.json), sleeping {seconds}[index] between.
TIMED = ("import json, time\nstart = time.perf_counter()\ntime.sleep({seconds}[index])\n"
         "open(f'span_{{index}}.json', 'w', encoding='utf-8').write(json.dumps([start, time.perf_counter()]))\n"
         "output = item")


# --- running and checking ------------------------------------------------------------------------------------------

def run(env, flow: Path, *args):
    """`taskloom run` (with --json-events): its result and the run's number. It ran (succeeded or failed)."""
    result = env.taskloom("run", flow, "--json-events", *args)
    assert result.returncode in (0, 1), result.stdout[-3000:] + result.stderr[-3000:]
    return result, env.last_run()[0]


def result_of(env, run_id: int, block: str) -> dict:
    [(status, error, summary)] = env.query("SELECT status, error, summary FROM blocks WHERE run_id=? AND block=?",
                                           run_id, block)
    return {"status": status, "error": error or "", "summary": summary or ""}


def validate(env, flow: Path) -> tuple[int, list[str], list[str]]:
    """`taskloom validate`: its exit code, its error lines and its warning lines."""
    checked = env.taskloom("validate", flow)
    lines = checked.stderr.splitlines()
    return (checked.returncode, [line for line in lines if line.startswith("error:")],
            [line for line in lines if line.startswith("warning:")])


def no_runs(env) -> bool:
    db = env.home / "history.db"
    return not db.exists() or not env.query("SELECT name FROM sqlite_master WHERE name='runs'") or \
        not env.query("SELECT id FROM runs")


# --- 3, 4: a run: once per item, in the flow's order, the results in the items' order ---------------------------------

def test_the_blocks_inside_run_once_per_item_as_a_flow_of_their_own_and_results_keep_the_items_order(env):
    """Spec 3 (item, index 1 to N, result) and 4 (the blocks inside run once per item, as a flow of their own in the
    flow's order: check, written first in the file, runs after load, which it takes from; three items at the same time
    finish in the opposite order; `results` is every item's result in the items' order, not the order they
    finished)."""
    load = python("import time\ntime.sleep({1: 0.9, 2: 0.5, 3: 0.1}[index])\n"
                  "open('finished.txt', 'a', encoding='utf-8').write(str(index))\n"
                  "output = {'item': item, 'index': index}", ("item", "index"), x=60, y=100)
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "make": make(["north", "south", "east"]), "each": loop(3),
        "check": inside("each", python("output = dict(input, checked=True)", x=60 + NODE_W + 160, y=100)),
        "load": inside("each", load), "report": saver("report.json"),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> load.item", "each.index -> load.index",
        "load.output -> check.input", "check.output -> each.result", "each.results -> report.input"])

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert (env.root / "finished.txt").read_text(encoding="utf-8") == "321"  # they finished last item first ...
    assert read_json(env.root / "report.json") == [  # ... and the results are in the items' order
        {"item": "north", "index": 1, "checked": True}, {"item": "south", "index": 2, "checked": True},
        {"item": "east", "index": 3, "checked": True}]
    assert result_of(env, run_id, "each")["status"] == "success"
    assert ran(env.root, "report") == 1
    assert env.last_run() == (run_id, "success")


def test_at_the_same_time_is_1_when_left_as_it_is_added_one_item_after_another_in_item_order(env):
    """Spec 3 (At the same time, default 1) and 4: left as it is when the Loop is added, the items run one at a time,
    in item order: each starts once the one before it has ended."""
    flow = per_item(env.root, ["a", "b", "c"], TIMED.format(seconds={1: 0.3, 2: 0.3, 3: 0.3}))

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    t = spans(env.root, 3)
    assert t[1][1] <= t[2][0] and t[2][1] <= t[3][0], t
    assert read_json(env.root / "report.json") == ["a", "b", "c"]


def test_at_the_same_time_runs_that_many_items_and_the_next_starts_as_soon_as_one_finishes_in_item_order(env):
    """Spec 3 and 4 (the user's decision: a sliding window): with At the same time 2, items 1 and 2 start; item 3 starts
    as soon as item 2 ends, while item 1 still runs (not rounds of 2), item 4 as soon as item 3 ends; never more than 2
    at once; the items start in item order."""
    flow = per_item(env.root, ["a", "b", "c", "d"], TIMED.format(seconds={1: 1.5, 2: 0.3, 3: 0.3, 4: 0.3}),
                    same_time=2)

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    t = spans(env.root, 4)
    assert sorted(t, key=lambda k: t[k][0]) == [1, 2, 3, 4], t  # they started in item order
    at_once = max(sum(1 for s, e in t.values() if s <= start < e) for start, _ in t.values())
    assert at_once == 2, t
    assert t[3][0] >= t[2][1] and t[3][0] - t[2][1] < 0.5, t  # item 3 started as soon as item 2 ended ...
    assert t[1][1] - t[3][0] >= 0.5, t  # ... while item 1 still ran
    assert t[4][0] >= t[3][1] and t[4][0] < t[1][1], t
    assert read_json(env.root / "report.json") == ["a", "b", "c", "d"]


def test_each_item_gets_its_own_copy_of_what_comes_from_outside_once_those_blocks_are_done(env):
    """Spec 4 and 8: a block outside (settings, slow and written last in the file) connects into a block inside: the
    Loop waits until it is done; each item gets its own copy of its value: an item changing it changes it neither for
    the next item nor outside the Loop."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "make": make(["a", "b", "c"]), "each": loop(),
        "mark": inside("each", python("base['seen'].append(item)\noutput = base['seen']", ("item", "base"),
                                      x=60, y=100)),
        "report": saver("report.json"), "keep": saver("settings.json", y=400),
        "settings": python("import time\ntime.sleep(0.5)\noutput = {'seen': []}", inputs=(), x=-450, y=400),
    }, ["manual.start -> make.after", "make.output -> each.items", "manual.start -> settings.after",
        "each.item -> mark.item", "settings.output -> mark.base", "mark.output -> each.result",
        "each.results -> report.input", "settings.output -> keep.input", "each.done -> keep.after"])

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "report.json") == [["a"], ["b"], ["c"]]
    assert read_json(env.root / "settings.json") == {"seen": []}
    assert env.block_status(run_id)["settings"] == "success"


def test_a_table_gives_one_item_per_row_as_a_dict_of_its_columns(env):
    """Spec 3: a table arriving at `items` gives one item per row, as a dict of its columns."""
    table = python("output = pl.DataFrame({'region': ['north', 'south'], 'sales': [10, 20]})", inputs=(), x=-450)
    flow = per_item(env.root, [], "output = item", inputs=("item",), blocks={"make": table})

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "report.json") == [{"region": "north", "sales": 10}, {"region": "south", "sales": 20}]


def test_an_empty_list_runs_nothing_inside_and_gives_an_empty_list(env):
    """Spec 4: an empty list runs nothing inside the Loop and its results are []: what follows it runs."""
    flow = per_item(env.root, [], "open('ran_work.txt', 'a', encoding='utf-8').write('x')\noutput = item")

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert ran(env.root, "work") == 0
    assert read_json(env.root / "report.json") == []
    assert result_of(env, run_id, "each")["status"] == "success"


def test_items_that_are_not_a_list_or_a_table_fail_the_loop_saying_so(env):
    """Spec 3 (items is a list, or a table) with the no-surprises rule: a text arriving at `items` is not run letter
    by letter: the Loop fails, saying it needs a list; nothing inside runs and what follows it is skipped."""
    flow = per_item(env.root, "north", "open('ran_work.txt', 'a', encoding='utf-8').write('x')\noutput = item")

    result, run_id = run(env, flow)

    assert result.returncode == 1
    each = result_of(env, run_id, "each")
    assert each["status"] == "failed" and "list" in each["error"], each
    assert ran(env.root, "work") == 0
    assert env.block_status(run_id)["report"] == "skipped"


# --- 5: the Log and the run history --------------------------------------------------------------------------------

def test_the_loop_writes_one_log_line_as_it_starts_and_one_as_it_ends(env):
    """Spec 5: the Loop writes one Log line when it starts, worded as the specification gives it ("5 items, 2 at the
    same time"), and one when it ends."""
    flow = per_item(env.root, ["a", "b", "c", "d", "e"], "output = item", same_time=2)

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    said = [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block='each' ORDER BY rowid", run_id)]
    assert len(said) == 2 and said[0] == "5 items, 2 at the same time", said


def test_every_log_line_of_a_block_inside_carries_its_block_and_item_and_none_is_lost_or_mixed_up(env):
    """Spec 5: four items at the same time, each printing 150 lines at once: every Log line of the block inside carries
    its block tag and its own item ("[work] ... [item 2/4]"), as `taskloom run` prints the Log; the run's events (as
    the editor reads them) are whole; the run history keeps every line, none lost."""
    n = 150
    code = f"import time\ntime.sleep(0.3)\nfor i in range({n}):\n    print(f'item {{index}} line {{i}}')\noutput = item"
    flow = per_item(env.root, ["a", "b", "c", "d"], code, same_time=4)
    wanted = {(k, i) for k in range(1, 5) for i in range(n)}

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    broken = []
    for line in (line for line in result.stdout.splitlines() if line.strip()):
        try:
            json.loads(line)
        except ValueError:
            broken.append(line)
    assert broken == [], f"lines of the run's output that are not one whole event each: {broken[:5]}"
    assert events(result.stdout)[-1]["event"] == "run_finished"
    kept = Counter((int(m.group(1)), int(m.group(2))) for (message,) in env.query(
        "SELECT message FROM logs WHERE run_id=?", run_id) if (m := re.search(r"item (\d) line (\d+)\b", message)))
    assert set(kept) == wanted and set(kept.values()) == {1}, "the run history lost or repeated Log lines"

    printed = env.taskloom("run", flow)  # as `taskloom run` prints the Log

    assert printed.returncode == 0, printed.stderr[-3000:]
    found, mixed = set(), []
    for line in printed.stderr.splitlines():
        m = re.search(r"item (\d) line (\d+)\b", line)
        if m:
            found.add((int(m.group(1)), int(m.group(2))))
            if "[work]" not in line or f"[item {m.group(1)}/4]" not in line:
                mixed.append(line)
    assert mixed == [], f"Log lines without their block's tag and their own item: {mixed[:5]}"
    assert found == wanted


# --- 6: failures -----------------------------------------------------------------------------------------------------

def test_a_failed_item_starts_no_new_item_lets_the_running_ones_finish_and_fails_the_loop_listing_them(env):
    """Spec 6 (the specification's example): five items, four at the same time; items 2 and 4 fail. No new item starts
    (item 5 does not run), the items running (1 and 3) finish, then the Loop fails, its error listing the failed items
    with their errors and the one not run; it leaves through its error output, and the block after its results is
    skipped, as after any failure."""
    code = ("import time\nopen(f'started_{index}.txt', 'w', encoding='utf-8').close()\n"
            "if index in (2, 4):\n    time.sleep(0.3)\n    raise ValueError(f'no data for {item}')\n"
            "time.sleep(1.5)\nopen(f'ended_{index}.txt', 'w', encoding='utf-8').close()\noutput = item")
    flow = per_item(env.root, ["a", "b", "c", "d", "e"], code, same_time=4,
                    blocks={"on_failure": saver("failure.json", y=400)}, edges=["each.error -> on_failure.input"])

    _, run_id = run(env, flow)

    assert [k for k in range(1, 6) if (env.root / f"started_{k}.txt").exists()] == [1, 2, 3, 4]
    assert [k for k in range(1, 6) if (env.root / f"ended_{k}.txt").exists()] == [1, 3]
    each = result_of(env, run_id, "each")
    assert each["status"] == "failed", each
    error = each["error"]
    assert re.search(r"\b2 of 5\b", error) and re.search(r"\b1 not run\b", error), error
    assert "item 2" in error and "item 4" in error and "work" in error, error
    assert "no data for b" in error and "no data for d" in error, error
    assert env.block_status(run_id)["report"] == "skipped"
    assert not (env.root / "report.json").exists()
    failure = read_json(env.root / "failure.json")
    assert failure["block"] == "each" and "item 2" in failure["error"], failure


def test_a_failure_handled_inside_the_loop_does_not_fail_its_item(env):
    """Spec 6: an item fails when one of its blocks fails and that failure is not handled inside the loop: here load
    fails for item 2, and its error output goes to a block inside: the item, and the Loop, succeed."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "make": make(["a", "b", "c"]), "each": loop(),
        "load": inside("each", python("if item == 'b':\n    raise ValueError('no data for b')\noutput = item",
                                      ("item",), x=60, y=100)),
        "note": inside("each", python("print('handled:', input['error'])", outputs=(), x=60 + NODE_W + 160, y=100)),
        "echo": inside("each", python("output = item.upper()", ("item",), x=60, y=300)),
        "report": saver("report.json"),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> load.item", "load.error -> note.input",
        "each.item -> echo.item", "echo.output -> each.result", "each.results -> report.input"])

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert result_of(env, run_id, "each")["status"] == "success"
    assert read_json(env.root / "report.json") == ["A", "B", "C"]


# --- 7: running again from the Loop ---------------------------------------------------------------------------------

def test_run_from_the_loop_runs_again_only_the_items_that_failed_or_did_not_run_then_what_follows(env):
    """Spec 7: after a run where items 2 and 4 failed and item 5 did not run, and a fix, `taskloom run --from each
    --inputs-from N` runs again only items 2, 4 and 5; items 1 and 3 keep their results from that run and are not run
    again; then what follows the Loop runs, with every item's result in the items' order."""
    code = ("import os, time\nopen(f'ran_{index}.txt', 'a', encoding='utf-8').write('x')\n"
            "if index in (2, 4) and not os.path.exists('fixed.txt'):\n"
            "    time.sleep(0.3)\n    raise ValueError(f'no data for {item}')\n"
            "time.sleep(1.5 if index in (1, 3) else 0)\noutput = item.upper()")
    flow = per_item(env.root, ["a", "b", "c", "d", "e"], code, same_time=4)
    first, failed_run = run(env, flow)
    assert first.returncode == 1
    assert {k: ran(env.root, str(k)) for k in range(1, 6)} == {1: 1, 2: 1, 3: 1, 4: 1, 5: 0}
    (env.root / "fixed.txt").write_text("", encoding="utf-8")

    second, from_run = run(env, flow, "--from", "each", "--inputs-from", failed_run)

    assert second.returncode == 0, second.stderr[-3000:]
    assert {k: ran(env.root, str(k)) for k in range(1, 6)} == {1: 1, 2: 2, 3: 1, 4: 2, 5: 1}
    assert read_json(env.root / "report.json") == ["A", "B", "C", "D", "E"]
    assert ran(env.root, "report") == 1
    assert result_of(env, from_run, "each")["status"] == "success"


def test_run_only_the_loop_runs_again_only_the_items_that_failed_or_did_not_run_with_the_outside_value(env):
    """Spec 7 and 8 with the no-surprises rule: settings, outside the Loop, connects into work, inside it; after a run
    where item 2 failed (so item 3 did not run) and a fix, `taskloom run --only each --inputs-from N` runs items 2 and
    3 only, each with the value settings gave in that run (never none); item 1 keeps its result; neither settings nor
    the block after the Loop runs."""
    work = python("import json, os\nopen(f'ran_{index}.txt', 'a', encoding='utf-8').write('x')\n"
                  "if index == 2 and not os.path.exists('fixed.txt'):\n    raise ValueError('no data for b')\n"
                  "output = f\"{item}-{base['tag']}\"\n"
                  "open(f'out_{index}.json', 'w', encoding='utf-8').write(json.dumps(output))",
                  ("item", "index", "base"), x=60, y=100)
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "make": make(["a", "b", "c"]), "each": loop(), "work": inside("each", work),
        "settings": python("open('ran_settings.txt', 'a', encoding='utf-8').write('x')\noutput = {'tag': 'x'}",
                           inputs=(), x=-450, y=400),
        "report": saver("report.json"),
    }, ["manual.start -> make.after", "make.output -> each.items", "manual.start -> settings.after",
        "each.item -> work.item", "each.index -> work.index", "settings.output -> work.base",
        "work.output -> each.result", "each.results -> report.input"])
    first, failed_run = run(env, flow)
    assert first.returncode == 1
    assert {k: ran(env.root, str(k)) for k in (1, 2, 3)} == {1: 1, 2: 1, 3: 0}
    (env.root / "fixed.txt").write_text("", encoding="utf-8")

    second, only_run = run(env, flow, "--only", "each", "--inputs-from", failed_run)

    assert second.returncode == 0, second.stderr[-3000:]
    assert result_of(env, only_run, "each")["status"] == "success"
    assert {k: ran(env.root, str(k)) for k in (1, 2, 3)} == {1: 1, 2: 2, 3: 1}
    assert read_json(env.root / "out_2.json") == "b-x" and read_json(env.root / "out_3.json") == "c-x"
    assert ran(env.root, "settings") == 1
    assert ran(env.root, "report") == 0


# --- 8, 9: connections and Validate ---------------------------------------------------------------------------------

OUT_OF_THE_LOOP = "work.output -> tail.input"  # from a block inside the Loop to one outside


def out_of_the_loop_flow(folder: Path) -> Path:
    return per_item(folder, ["a", "b"], "output = item", blocks={"tail": saver("tail.json", y=400)},
                    edges=[OUT_OF_THE_LOOP])


def test_validate_refuses_a_connection_from_inside_the_loop_to_outside_but_through_result_naming_it(env):
    """Spec 8 and 9: a block inside connects out of the Loop only through `result`: taskloom validate refuses any
    other connection from inside to outside, naming it and saying to use the loop's result; taskloom run refuses to
    start."""
    flow = out_of_the_loop_flow(env.root)

    code, errors, _ = validate(env, flow)

    assert code == 2, errors
    assert [e for e in errors if OUT_OF_THE_LOOP in e and "result" in e.replace(OUT_OF_THE_LOOP, "")], errors
    refused = env.taskloom("run", flow)
    assert refused.returncode == 2 and OUT_OF_THE_LOOP in refused.stderr, refused.stderr
    assert no_runs(env)


def test_a_loop_inside_a_loop_runs_its_own_items_per_item_of_the_outer_one_and_validate_warns_about_it(env):
    """Spec 8 (a Loop inside a Loop is allowed: its own items per item of the outer one) and 9 (Validate warns that its
    blocks run items x items times, without refusing the flow)."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "make": make([[1, 2, 3], [4, 5, 6]]), "each": loop(),
        "inner": inside("each", loop(x=60, y=100)),
        "square": inside("inner", python("open('ran_square.txt', 'a', encoding='utf-8').write('x')\n"
                                         "output = item * item", ("item",), x=120, y=200)),
        "report": saver("report.json"),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> inner.items",
        "inner.item -> square.item", "square.output -> inner.result", "inner.results -> each.result",
        "each.results -> report.input"])

    code, errors, warnings = validate(env, flow)
    assert code == 0, errors
    assert [w for w in warnings if "inner" in w and "loop" in w.lower()], warnings

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "report.json") == [[1, 4, 9], [16, 25, 36]]
    assert ran(env.root, "square") == 6


def test_a_splitter_inside_a_loop_works_as_anywhere(env):
    """Spec 4 and 8: a Splitter inside a Loop: each item runs both its lines, and the Merger inside joins them into the
    item's result."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "make": make(["x", "y"]), "each": loop(),
        "split": inside("each", {"type": "logic.splitter", "ui": {"x": 60, "y": 200}}),
        "a": inside("each", python("output = input + '-a'", x=360, y=100)),
        "b": inside("each", python("output = input + '-b'", x=360, y=300)),
        "merge": inside("each", {"type": "logic.merger", "ui": {"x": 660, "y": 200}}),
        "report": saver("report.json"),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> split.value",
        "split.lines -> a.input", "split.lines -> b.input", "a.output -> merge.lines", "b.output -> merge.lines",
        "merge.value -> each.result", "each.results -> report.input"])

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "report.json") == [{"a": "x-a", "b": "x-b"}, {"a": "y-a", "b": "y-b"}]


def test_a_loop_inside_a_splitters_line_works_as_anywhere(env):
    """Spec 8: a Loop as a Splitter's line: it runs its items while the other line runs, and the Merger joins its
    results with the other line's output."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "make": make(["x", "y"]), "split": {"type": "logic.splitter", "ui": {"x": -200, "y": 0}},
        "each": loop(y=-300), "work": inside("each", python("output = item.upper()", ("item",), x=60, y=-200)),
        "other": python("output = len(input)", x=0, y=400),
        "merge": {"type": "logic.merger", "ui": {"x": 900, "y": 0}}, "report": saver("report.json"),
    }, ["manual.start -> make.after", "make.output -> split.value", "split.lines -> each.items",
        "split.lines -> other.input", "each.item -> work.item", "work.output -> each.result",
        "each.results -> merge.lines", "other.output -> merge.lines", "merge.value -> report.input"])

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "report.json") == {"each": ["X", "Y"], "other": 2}


@pytest.mark.parametrize("type_id, config, key", [
    ("data.write_file", {"path": "out/report.csv"}, "path"),
    ("files.copy", {"source": "in/sales.csv", "destination": "archive/"}, "destination"),
], ids=["write_file_path", "copy_files_destination"])
def test_validate_warns_about_a_file_path_inside_the_loop_without_a_placeholder(env, type_id, config, key):
    """Spec 9: a block inside writing or copying to a file path with no {placeholder} (every item would write the same
    file) is warned about, naming the block and the setting; with a placeholder in it, it is not."""
    cls = discover(None).get(type_id)
    label = setting_label(key, cls.config[key])
    feeds = {"data.write_file": ("rows", "table"), "files.copy": ("rows", "after")}[type_id]

    def flow_with(value):
        writer = {"type": type_id, "config": {**config, key: value}, "ui": {"x": 60 + NODE_W + 160, "y": 100}}
        return flow_file(env.root / "flow.yaml", {
            "manual": MANUAL, "make": make(["north", "south"]), "each": loop(),
            "rows": inside("each", python("output = pl.DataFrame({'region': [item]})", ("item",), x=60, y=100)),
            "save": inside("each", writer),
            "echo": inside("each", python("output = item", ("item",), x=60, y=300)),
        }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> rows.item",
            f"rows.output -> save.{feeds[1]}", "each.item -> echo.item", "echo.output -> each.result"],
            params={"day": {"type": "text", "value": "monday"}})

    code, errors, warnings = validate(env, flow_with(config[key]))

    assert code == 0, errors
    assert [w for w in warnings if "save" in w and label in w], warnings

    code, errors, warnings = validate(env, flow_with(config[key].replace("/", "/{day}_", 1)))

    assert code == 0, errors
    assert not [w for w in warnings if "save" in w and label in w], warnings


def test_validate_warns_when_results_is_connected_but_result_is_not(env):
    """Spec 9: the Loop's results connected, but nothing into its result: Validate warns, naming the Loop."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "make": make(["a"]), "each": loop(),
        "work": inside("each", python("output = item", ("item",), x=60, y=100)), "report": saver("report.json"),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> work.item",
        "each.results -> report.input"])

    code, errors, warnings = validate(env, flow)

    assert code == 0, errors
    assert [w for w in warnings if "each" in w and "result" in w], warnings


def test_validate_checks_the_blocks_inside_like_any_others(env):
    """Spec 9: Validate checks the blocks inside a Loop like any others: a Wait inside without its seconds is refused,
    naming it and the setting (and the Loop itself is a block it knows)."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "make": make(["a"]), "each": loop(),
        "pause": inside("each", {"type": "logic.wait", "ui": {"x": 60, "y": 100}}),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> pause.value",
        "pause.value -> each.result"])

    code, errors, _ = validate(env, flow)

    assert code == 2
    assert [e for e in errors if e.startswith("error: pause:") and "Seconds" in e], errors
    assert not [e for e in errors if "unknown" in e], errors


@pytest.mark.parametrize("same_time", [0, "two"])
def test_at_the_same_time_takes_a_whole_number_of_one_or_more(env, same_time):
    """Spec 3: At the same time is a whole number, 1 or more: anything else is refused, naming the setting."""
    flow = per_item(env.root, ["a", "b"], "output = item", same_time=same_time)

    code, errors, _ = validate(env, flow)

    assert code == 2
    assert [e for e in errors if "each" in e and "At the same time" in e], errors


# --- 10, 11: For Each is gone; the README ---------------------------------------------------------------------------

def test_for_each_is_gone_and_a_flow_still_using_it_fails_to_load_as_any_unknown_block_type(env):
    """Spec 10: logic.for_each is no block any more: a flow file still using it is refused with the usual message for
    an unknown block type, by taskloom validate and taskloom run."""
    assert discover(None).get("logic.for_each") is None
    env.write("per_region.yaml", "params:\n  region: {type: text, value: north}\nblocks: {}\n")
    flow = flow_file(env.root / "flow.yaml", {
        "make": make(["north", "south"]),
        "repeat": {"type": "logic.for_each", "config": {"flow": "per_region.yaml", "param": "region"}},
    }, ["make.output -> repeat.items"])

    code, errors, _ = validate(env, flow)

    assert code == 2
    assert [e for e in errors if "unknown block type 'logic.for_each'" in e], errors
    refused = env.taskloom("run", flow)
    assert refused.returncode == 2 and "unknown block type 'logic.for_each'" in refused.stderr, refused.stderr


def test_readme_has_a_section_on_the_loop_container_with_an_example_and_the_docs_no_longer_tell_of_for_each():
    """Spec 10 and 11: README has a section on the Loop container (replacing For Each's), with an example, telling what
    is inside, the items, item / index / result, At the same time, the results' order, failures and running again;
    neither README nor the docs folder tell of For Each any more."""
    for path in [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]:
        text = path.read_text(encoding="utf-8")
        assert "For Each" not in text and "for_each" not in text, f"{path.name} still tells of For Each"
    lines = (REPO / "README.md").read_text(encoding="utf-8").splitlines()
    fenced = [line.startswith("```") for line in lines]
    in_code = [sum(fenced[:i]) % 2 == 1 for i in range(len(lines))]  # (a # line in an example is no heading)
    heads = [(i, len(m.group(1))) for i, line in enumerate(lines) if not in_code[i] and (m := re.match(r"(#+) ", line))]
    found = [(i, level) for i, level in heads if "Loop" in lines[i]]
    assert found, "README has no section whose heading names the Loop"
    start, level = found[0]
    end = next((i for i, lv in heads if i > start and lv <= level), len(lines))
    section = "\n".join(lines[start:end])
    assert "```" in section or "example" in section.lower(), "README's Loop section has no example"
    said = plain(section)
    for words in ("inside", "items", "item", "index", "result", "at the same time", "order", "fail", "run from"):
        assert words in said, f"README's Loop section does not tell about {words!r}"


# --- the editor: what the canvas paints -------------------------------------------------------------------------------

def render(scene, rect: QRectF, scale: float = 1.0) -> QImage:
    """`rect` of the scene (background and items) as the canvas paints it, at `scale`."""
    image = QImage(max(1, round(rect.width() * scale)), max(1, round(rect.height() * scale)),
                   QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    scene.render(painter, QRectF(0, 0, image.width(), image.height()), rect, Qt.IgnoreAspectRatio)
    painter.end()
    return image


def box(tab, loop_id: str) -> tuple[QRectF, QColor]:
    """The Loop's box as the canvas paints it (scene coordinates): where its background colour is, the colour most of
    it, away from the other blocks, is painted in; and that colour."""
    settle()
    around = tab.scene.nodes[loop_id].sceneBoundingRect().adjusted(-8, -8, 8, 8)
    area = QRectF(math.floor(around.left()), math.floor(around.top()), math.ceil(around.width()),
                  math.ceil(around.height()))
    rgb = pixels(render(tab.scene, area))
    free = np.ones(rgb.shape[:2], bool)
    for block_id, node in tab.scene.nodes.items():
        if block_id != loop_id:
            r = node.sceneBoundingRect().translated(-area.left(), -area.top())
            free[max(0, math.floor(r.top())):max(0, math.ceil(r.bottom())),
                 max(0, math.floor(r.left())):max(0, math.ceil(r.right()))] = False
    packed = (rgb[..., 0].astype(np.int64) << 16) | (rgb[..., 1].astype(np.int64) << 8) | rgb[..., 2].astype(np.int64)
    colours, counts = np.unique(packed[free], return_counts=True)
    background = QColor.fromRgb(int(colours[counts.argmax()]))
    same = np.abs(rgb - background.getRgb()[:3]).max(axis=2) <= 3
    # The box is the part of that colour around the middle (a shadow fading out can pass through it, further out).
    ys, xs = np.nonzero(same & free)
    middle = np.argmin((ys - rgb.shape[0] / 2) ** 2 + (xs - rgb.shape[1] / 2) ** 2)
    ys, xs = np.nonzero(_reached(same, (ys[middle], xs[middle])))
    rect = QRectF(area.left() + xs.min(), area.top() + ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
    return rect, background


def _reached(mask: np.ndarray, start: tuple[int, int]) -> np.ndarray:
    """The pixels of `mask` connected to `start` (along rows and columns)."""
    region = np.zeros_like(mask)
    region[start] = True
    while True:
        before = int(region.sum())
        for flip in (False, True):  # whole runs along the rows, then along the columns
            m, r = (mask.T, region.T) if flip else (mask, region)
            starts = m & ~np.concatenate([np.zeros((m.shape[0], 1), bool), m[:, :-1]], axis=1)
            runs = np.cumsum(starts.ravel()).reshape(m.shape) * m
            touched = np.unique(runs[r & m])
            grown = np.isin(runs, touched[touched > 0])
            region = grown.T if flip else grown
        if int(region.sum()) == before:
            return region


def box_texts(tab, loop_id: str) -> list[dict]:
    """The texts the canvas draws on and around the Loop's box (not those of the blocks inside it)."""
    settle()
    others = [node.sceneBoundingRect() for b, node in tab.scene.nodes.items() if b != loop_id]
    return [t for t in drawn_texts(tab.scene, tab.scene.nodes[loop_id].sceneBoundingRect())
            if not any(r.contains(QPointF(*t["center"])) for r in others)]


def progress(tab, n: int, loop_id: str = "each") -> tuple[int, int] | None:
    """(done, running) as the Loop's box shows it, "done/N · k running"; None while it shows no such counter."""
    shown = " ".join(t["text"] for t in box_texts(tab, loop_id))
    m = re.search(rf"(\d+)\s*/\s*{n}\s*·\s*(\d+)\s+running", shown)
    return (int(m.group(1)), int(m.group(2))) if m else None


def bar_area(tab, loop_id: str = "each") -> tuple[QRectF, QColor]:
    """Where the Loop's progress bar is drawn: the top of its box down to 10 px above its highest port (scene
    coordinates); and the box's background."""
    rect, background = box(tab, loop_id)
    top = rect.top() + 4
    highest = min(p.scenePos().y() for p in tab.scene.nodes[loop_id].ports.values())
    return QRectF(rect.left(), top, rect.width(), math.floor(highest - 10 - top)), background


def header(rect: QRectF) -> QPointF:
    """A spot at the top of the Loop's box, where its name is: to grab, move or right-click the box."""
    return QPointF(rect.center().x(), rect.top() + 10)


def title(tab, block_id: str) -> QPointF:
    return tab.scene.nodes[block_id].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))


def drawn(tab, block_id: str) -> QRectF:
    """Where a block is drawn (its bounding rect has room for the selection's outline)."""
    return tab.scene.nodes[block_id].sceneBoundingRect().adjusted(4, 4, -4, -4)


def drag(tab, a: QPointF, b: QPointF):
    """Press at the scene point `a`, move to `b` and let go, as the mouse does, both in view (zoomed out if need be)."""
    view = tab.view
    span = QRectF(a, b).normalized().adjusted(-60, -60, 60, 60)
    shown = view.viewport().rect()
    view.zoom_to(100 * min(1.0, shown.width() / span.width(), shown.height() / span.height()))
    view.centerOn(span.center())
    settle()
    mouse_drag(tab, view.mapFromScene(a), view.mapFromScene(b))


def slack(tab) -> float:
    """How far off (in points of the flow) a drag can land at the canvas's zoom: a screen pixel, and one more."""
    return 2 / (tab.view.zoom_percent() / 100) + 1


def context_menu(qtbot, win, tab, at: QPointF, choose: str | None = None) -> list[tuple[str, bool, str]]:
    """Right-click the canvas at the scene point `at` (scrolled into view, as you would); note each item of the menu
    that opens (its text, whether it can be chosen, its tooltip) and click the one starting with `choose` if it can be
    chosen, else close the menu."""
    settle()
    tab.view.centerOn(at)
    settle()
    point = tab.view.mapFromScene(at)
    assert tab.view.viewport().rect().contains(point), point
    seen, tries, done = [], [], []

    def act():  # (the menu waits for you inside the right-click)
        popup = QApplication.activePopupWidget()
        if popup is None and len(tries) < 250:
            tries.append(1)
            QTimer.singleShot(20, act)
            return
        if popup is not None:
            seen.extend((a.text(), a.isEnabled(), a.toolTip()) for a in popup.actions() if not a.isSeparator())
            item = next((a for a in popup.actions()
                         if choose is not None and a.text().startswith(choose) and a.isEnabled()), None)
            if item is not None:
                QTest.mouseClick(popup, Qt.LeftButton, Qt.NoModifier, popup.actionGeometry(item).center())
            if popup.isVisible():
                popup.close()
        done.append(1)

    for _ in range(2):  # (once more if no menu opened: a click can be lost while the window is still settling)
        tries.clear()
        done.clear()
        QTimer.singleShot(0, act)
        QTest.mouseClick(win.windowHandle(), Qt.RightButton, Qt.NoModifier, tab.view.viewport().mapTo(win, point))
        qtbot.waitUntil(lambda: bool(done), timeout=10_000)
        if seen:
            break
    return seen


def run_in_editor(win, tab):
    """Run now (F5): a run starts (the flow is not refused)."""
    win.run_flow(tab)
    assert tab.runs, f"the run did not start: {win.message_boxes}"


def wait_running(qtbot, tab, block: str):
    """Wait until the block shows as running (at most a minute; at once if the run ends first)."""
    qtbot.waitUntil(lambda: tab.scene.nodes[block].status == "running" or not tab.running, timeout=60_000)
    assert tab.scene.nodes[block].status == "running", f"the run ended before {block} ran: {tab.last}"


def text_of(tip: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", tip)).split())


def log_lines(win) -> list[str]:
    return win.log.text.toPlainText().splitlines()


def for_each_drawing() -> np.ndarray:
    """The For Each block's icon (the approved Loop icon: a list with a going-round arrow), as blocks paint theirs."""
    image = QImage(96, 96, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    painter = QPainter(image)
    paint_icon(painter, QRectF(0, 0, 96, 96), FOR_EACH_ICON, QColor(LOGIC))
    painter.end()
    return ink(pixels(image), LOGIC)


def two_blocks_inside(folder: Path) -> Path:
    """make -> each (a Loop) -> report; inside each: load (gets the item) -> check -> each's result."""
    return flow_file(folder / "flow.yaml", {
        "manual": MANUAL, "make": make(["north", "south"]), "each": loop(),
        "load": inside("each", python("output = item.upper()", ("item",), x=60, y=100)),
        "check": inside("each", python("output = input + '!'", x=60 + NODE_W + 160, y=100)),
        "report": saver("report.json"),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> load.item",
        "load.output -> check.input", "check.output -> each.result", "each.results -> report.input"])


# --- 1: added from the Blocks list; a grey box that reads well in Light and Dark --------------------------------------

def test_the_loop_is_added_from_the_blocks_list_under_logic_as_a_box_with_its_name_and_the_for_each_icon(win, tmp_path):
    """Spec 1 and 10: the Blocks list has Loop under Logic, and no For Each; added from it like a block, it appears as
    a box that can hold a block, its name at the top, the For Each icon (a list with a going-round arrow) at its top
    left: of every built-in block's icon, the most like that drawing; At the same time shows 1."""
    logic = [item.text(0) for item in rows(win.palette_panel).get("Logic", [])]
    assert "Loop" in logic and "For Each" not in logic, logic
    tab = win.new_flow()
    try:
        add_from_palette(win, "Loop")
        settle()
        [loop_id] = [b for b, spec in tab.doc.blocks.items() if spec["type"] == LOOP]
        rect, _ = box(tab, loop_id)
        assert rect.width() > NODE_W and rect.height() > HEADER_H + 6 + ROW_H + FOOTER_H, rect  # a block fits in it
        assert [t for t in box_texts(tab, loop_id) if loop_id in t["text"] and t["center"][1] < rect.top() + 40], \
            box_texts(tab, loop_id)
        top = tab.scene.nodes[loop_id].sceneBoundingRect().top()
        icon = ink(pixels(render(tab.scene, QRectF(rect.left() - 4, top, 60, rect.top() + 48 - top), 2.0)), LOGIC)
        assert has_icon(icon, 2.0), "no icon on the Logic colour at the top left of the Loop's box"
        select_only(tab, loop_id)
        same_time = field(win, "At the same time")
        assert (same_time.text() or getattr(same_time, "placeholderText", str)()) == "1"
        reference = for_each_drawing()
        others = canvas_icons(win, tmp_path, {t: c for t, c in built_in(win.registry).items() if t != LOOP})
        likeness = {t: unlike(a, reference) for t, a in {**others, LOOP: icon}.items()}
        closest = min(likeness, key=likeness.get)
        assert closest == LOOP and likeness[LOOP] < 0.2, (closest, likeness[closest], likeness[LOOP])
    finally:
        clean(win)


def beside(tab, rect: QRectF, loop_id: str) -> float:
    """The darkest lightness just right of and just below the Loop's box (where its shadow is), on rows and columns
    of the canvas without its dots, away from the Loop's ports."""
    left, top = math.floor(rect.left()), math.floor(rect.top())
    area = QRectF(left, top, math.ceil(rect.width()) + 14, math.ceil(rect.height()) + 14)
    rgb = pixels(render(tab.scene, area))
    light = (rgb.max(axis=2) + rgb.min(axis=2)) / 2
    ports = [p.scenePos() for p in tab.scene.nodes[loop_id].ports.values()]
    right, bottom = round(rect.right()) - left, round(rect.bottom()) - top
    found = []
    for row in range(20, bottom - 10):
        y = top + row
        if 6 <= y % 20 <= 14 and all(abs(y - p.y()) > 12 for p in ports):
            found += list(light[row, right + 2:right + 13])
    for col in range(20, right - 10):
        x = left + col
        if 6 <= x % 20 <= 14 and all(abs(x - p.x()) > 12 for p in ports):
            found += list(light[bottom + 2:bottom + 13, col])
    assert found, "nothing to measure beside the box"
    return float(min(found))


def how_it_looks(tab, loop_id: str) -> list[str]:
    """What does not read well on the Loop's box: its background not grey or hardly unlike the canvas, no shadow darker
    than it, its name or its counter in a colour that does not stand out from what is behind it."""
    rect, background = box(tab, loop_id)
    problems = []
    rgb = background.getRgb()[:3]
    if max(rgb) - min(rgb) > 20:
        problems.append(f"its background {background.name()} is not grey")
    canvas = most_common(render(tab.scene, QRectF(round(rect.right()) + 60, round(rect.top()), 40,
                                                  round(rect.height()))))
    if abs(background.lightness() - canvas.lightness()) < 8:
        problems.append(f"its background {background.name()} hardly differs from the canvas {canvas.name()}")
    shadow = beside(tab, rect, loop_id)
    if shadow > background.lightness() - 8 or shadow > canvas.lightness() - 4:
        problems.append(f"no darker shadow beside it (darkest lightness {shadow:.0f}; its background "
                        f"{background.lightness()}, the canvas {canvas.lightness()})")
    texts = box_texts(tab, loop_id)
    names = [t for t in texts if loop_id in t["text"]]
    counters = [t for t in texts if re.search(r"\d+\s*/\s*\d+", t["text"]) or "running" in t["text"]]
    if not names or not counters:
        problems.append(f"its name or its counter is not on it: {[t['text'] for t in texts]}")
    for t in names + counters:
        s = max(t["size"], 6.0)
        cx, cy = t["center"]
        behind = most_common(render(tab.scene, QRectF(cx - 2 * s, cy - 2 * s, 4 * s, 4 * s), 2.0))
        ink_colour, alpha = QColor(t["fill"]), t["opacity"]
        shown = QColor(*(round(alpha * f + (1 - alpha) * b) for f, b in zip(ink_colour.getRgb()[:3],
                                                                           behind.getRgb()[:3])))
        if contrast(shown, behind) < TEXT:
            problems.append(f"{t['text']!r}: {shown.name()} on {behind.name()} ({contrast(shown, behind):.2f}:1)")
    return problems


def test_the_box_is_grey_with_a_darker_shadow_and_its_name_and_counter_read_well_in_light_and_dark(
        win, qtbot, tmp_path, monkeypatch):
    """Spec 1 (the user's request: a grey background with a darker shadow, reading well in dark mode): in the Light and
    the Dark theme (Tools > Settings > Theme), while it runs, the box is grey, unlike the canvas, with a shadow darker
    than both beside it; its name and its counter stand out from what is behind them (4.5:1, WCAG's bar for text)."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings may write a Startup script there)
    app = QApplication.instance()
    look = app.style().name(), app.styleSheet(), QApplication.palette(), QToolTip.palette()
    path = flow_file(tmp_path / "flow.yaml", {
        "manual": MANUAL, "make": make(["a", "b"]), "each": loop(),
        "work": inside("each", python(GATED, ("item", "index"), x=60, y=100)),
        "tail": inside("each", python("output = input", x=60 + NODE_W + 260, y=100)),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> work.item", "each.index -> work.index",
        "work.output -> tail.input", "tail.output -> each.result"])
    tab = win.open_flow(path)
    found = {}
    try:
        run_in_editor(win, tab)
        wait_running(qtbot, tab, "work")
        qtbot.waitUntil(lambda: progress(tab, 2) == (0, 1), timeout=20_000)
        for theme in ("Light", "Dark"):
            choose(win, theme)
            settle()
            found[theme] = how_it_looks(tab, "each")
    finally:
        go(tmp_path, 1, 2)
        wait_done(qtbot, tab)
        QGuiApplication.styleHints().unsetColorScheme()
        if app.style().name() != look[0]:
            QApplication.setStyle(look[0])
        if app.styleSheet() != look[1]:
            app.setStyleSheet(look[1])
        QApplication.setPalette(look[2])
        QToolTip.setPalette(look[3])
        settle()
        clean(win)
    assert {theme: problems for theme, problems in found.items() if problems} == {}


# --- 3: its ports -----------------------------------------------------------------------------------------------------

def test_its_ports_are_items_run_after_results_done_and_error_outside_the_box_and_item_index_result_inside(
        win, tmp_path):
    """Spec 3: outside the box, the input items and the usual run after (left), the outputs results, done and error
    (right); inside the box, the outputs item and index and the input result."""
    tab = win.open_flow(two_blocks_inside(tmp_path))
    try:
        settle()
        ports = tab.scene.nodes["each"].ports
        assert set(ports) == {("items", False), ("after", False), ("result", False), ("results", True),
                              ("done", True), ("error", True), ("item", True), ("index", True)}, sorted(ports)
        rect, _ = box(tab, "each")
        within = rect.adjusted(4, 4, -4, -4)
        at = {key: port.scenePos() for key, port in ports.items()}
        assert all(within.contains(at[key]) for key in (("item", True), ("index", True), ("result", False))), (rect, at)
        assert all(at[key].x() <= rect.left() + 4 for key in (("items", False), ("after", False))), (rect, at)
        assert all(at[key].x() >= rect.right() - 4 for key in (("results", True), ("done", True), ("error", True))), \
            (rect, at)
    finally:
        clean(win)


def test_blocks_inside_connect_to_item_index_and_result_by_dragging_like_any_port_and_the_flow_runs(win, tmp_path):
    """Spec 3: dragged on the canvas, the Loop's item and index connect to a block inside, and its output to the Loop's
    result, like any ports; saved, the flow runs (the real command) with every item's result."""
    path = flow_file(tmp_path / "flow.yaml", {
        "manual": MANUAL, "make": make(["north", "south"]), "each": loop(),
        "work": inside("each", python("output = f'{index}:{item}'", ("item", "index"), x=60, y=100)),
        "report": saver("report.json"),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.results -> report.input"])
    tab = win.open_flow(path)
    try:
        settle()
        assert {("item", True), ("index", True), ("result", False)} <= set(tab.scene.nodes["each"].ports), \
            sorted(tab.scene.nodes["each"].ports)
        for src, sp, dst, dp in (("each", "item", "work", "item"), ("each", "index", "work", "index"),
                                 ("work", "output", "each", "result")):
            drag(tab, tab.scene.nodes[src].ports[(sp, True)].scenePos(),
                 tab.scene.nodes[dst].ports[(dp, False)].scenePos())
        assert {"each.item -> work.item", "each.index -> work.index", "work.output -> each.result"} <= \
            set(tab.doc.data["edges"]), tab.doc.data["edges"]
        assert win.save(tab)
        done = taskloom_cli(win, "run", path, cwd=tmp_path)
        assert done.returncode == 0, done.stderr[-3000:]
        assert read_json(tmp_path / "report.json") == ["1:north", "2:south"]
    finally:
        clean(win)


# --- 2: inside or not; moving, resizing, deleting, copying; undo -----------------------------------------------------

def test_a_block_dragged_into_the_box_is_inside_the_loop_and_dragged_out_is_not_with_undo_and_redo(win, tmp_path):
    """Spec 2: a block dragged into the box is inside the Loop (the flow file says so; the box holds it); dragged out,
    it is not; Undo and Redo step through both."""
    path = flow_file(tmp_path / "flow.yaml", {"each": loop(), "load": python("output = 1", inputs=(), x=-NODE_W - 120)})
    tab = win.open_flow(path)
    try:
        settle()
        rect, _ = box(tab, "each")
        start = dict(tab.doc.blocks["load"]["ui"])
        drag(tab, title(tab, "load"), title(tab, "load") + (rect.center() - drawn(tab, "load").center()))

        assert tab.doc.blocks["load"].get(INSIDE) == "each", tab.doc.blocks["load"]
        assert box(tab, "each")[0].contains(drawn(tab, "load"))
        assert win.save(tab)
        assert saved(path)["blocks"]["load"].get(INSIDE) == "each"
        spot = dict(tab.doc.blocks["load"]["ui"])

        held = box(tab, "each")[0]
        drag(tab, title(tab, "load"), QPointF(title(tab, "load").x(), held.bottom() + 200))

        assert not tab.doc.blocks["load"].get(INSIDE), tab.doc.blocks["load"]
        menu(win, "Undo").trigger()
        settle()
        assert tab.doc.blocks["load"].get(INSIDE) == "each" and tab.doc.blocks["load"]["ui"] == spot
        menu(win, "Redo").trigger()
        settle()
        assert not tab.doc.blocks["load"].get(INSIDE)
        menu(win, "Undo").trigger()
        menu(win, "Undo").trigger()
        settle()
        assert not tab.doc.blocks["load"].get(INSIDE) and tab.doc.blocks["load"]["ui"] == start
    finally:
        clean(win)


def test_moving_the_box_moves_its_blocks_and_it_is_resized_from_its_corner_never_smaller_than_them(win, tmp_path):
    """Spec 2: dragging the box moves its blocks with it (one Undo puts all back); dragging its bottom-right corner
    resizes it, never smaller than the blocks inside it; Undo and Redo step through that; saved, it keeps its size."""
    path = two_blocks_inside(tmp_path)
    tab = win.open_flow(path)
    try:
        settle()
        before, _ = box(tab, "each")
        spots = {b: (tab.doc.blocks[b]["ui"]["x"], tab.doc.blocks[b]["ui"]["y"]) for b in ("load", "check")}

        drag(tab, header(before), header(before) + QPointF(120, 80))

        moved, _ = box(tab, "each")
        dx, dy = moved.left() - before.left(), moved.top() - before.top()
        assert abs(dx - 120) <= slack(tab) and abs(dy - 80) <= slack(tab), (before, moved)
        assert abs(moved.width() - before.width()) <= 1 and abs(moved.height() - before.height()) <= 1
        for b, (x, y) in spots.items():
            ui = tab.doc.blocks[b]["ui"]
            assert abs(ui["x"] - x - dx) <= 1 and abs(ui["y"] - y - dy) <= 1, (b, ui, (dx, dy))
        menu(win, "Undo").trigger()
        settle()
        assert {b: (tab.doc.blocks[b]["ui"]["x"], tab.doc.blocks[b]["ui"]["y"]) for b in spots} == spots
        back, _ = box(tab, "each")
        assert abs(back.left() - before.left()) <= 1 and abs(back.top() - before.top()) <= 1, (before, back)

        corner = QPointF(before.right() - 5, before.bottom() - 5)
        drag(tab, corner, corner + QPointF(150, 100))

        grown, _ = box(tab, "each")
        assert abs(grown.width() - before.width() - 150) <= slack(tab), (before, grown)
        assert abs(grown.height() - before.height() - 100) <= slack(tab), (before, grown)
        assert abs(grown.left() - before.left()) <= 1 and abs(grown.top() - before.top()) <= 1, (before, grown)
        assert {b: (tab.doc.blocks[b]["ui"]["x"], tab.doc.blocks[b]["ui"]["y"]) for b in spots} == spots

        drag(tab, QPointF(grown.right() - 5, grown.bottom() - 5), QPointF(grown.left() + 30, grown.top() + 30))

        small, _ = box(tab, "each")
        assert small.width() < grown.width() and small.height() < grown.height(), (grown, small)
        assert all(small.contains(drawn(tab, b)) for b in spots), (small, [drawn(tab, b) for b in spots])
        menu(win, "Undo").trigger()
        settle()
        undone, _ = box(tab, "each")
        assert abs(undone.width() - grown.width()) <= 1 and abs(undone.height() - grown.height()) <= 1
        menu(win, "Redo").trigger()
        settle()
        redone, _ = box(tab, "each")
        assert abs(redone.width() - small.width()) <= 1 and abs(redone.height() - small.height()) <= 1
        menu(win, "Undo").trigger()  # (larger than its blocks need: a size of its own)

        assert win.save(tab)
        assert win.close_tab(tab)
        tab = win.open_flow(path)
        settle()
        reopened, _ = box(tab, "each")
        assert abs(reopened.width() - grown.width()) <= 1 and abs(reopened.height() - grown.height()) <= 1
    finally:
        clean(win)


def test_deleting_the_loop_keeps_its_blocks_on_the_canvas_no_longer_inside_and_one_undo_brings_it_back(win, tmp_path):
    """Spec 2: deleting the Loop keeps its blocks (and the connection between them) on the canvas, no longer inside
    any Loop; its own connections go with it; one Undo step brings it all back."""
    tab = win.open_flow(two_blocks_inside(tmp_path))
    try:
        settle()
        original = json.dumps(tab.doc.data, sort_keys=True)
        select_only(tab, "each")

        menu(win, "Delete").trigger()
        settle()

        assert set(tab.doc.blocks) == {"manual", "make", "load", "check", "report"}
        assert not tab.doc.blocks["load"].get(INSIDE) and not tab.doc.blocks["check"].get(INSIDE)
        assert tab.doc.data["edges"] == ["manual.start -> make.after", "load.output -> check.input"]
        assert {"load", "check"} <= set(tab.scene.nodes)
        menu(win, "Undo").trigger()
        settle()
        assert json.dumps(tab.doc.data, sort_keys=True) == original
        assert "each" in tab.scene.nodes
    finally:
        clean(win)


def test_copy_and_paste_of_the_loop_copies_the_blocks_inside_it(win, tmp_path):
    """Spec 2: copying the Loop and pasting it adds a new Loop with copies of the blocks inside it, inside it, with
    the connections between them; the original is left as it was; one Undo takes the copy away."""
    tab = win.open_flow(two_blocks_inside(tmp_path))
    try:
        settle()
        original = json.dumps(tab.doc.data, sort_keys=True)
        before = json.loads(json.dumps(tab.doc.blocks))
        select_only(tab, "each")

        menu(win, "Copy").trigger()
        menu(win, "Paste").trigger()
        settle()

        new = {b: spec for b, spec in tab.doc.blocks.items() if b not in before}
        assert len(new) == 3, sorted(new)  # the Loop and the two blocks inside it
        [copy] = [b for b, spec in new.items() if spec["type"] == LOOP]
        by_code = {spec["config"]["code"]: b for b, spec in new.items() if spec["type"] == "logic.python"}
        load, check = by_code[before["load"]["config"]["code"]], by_code[before["check"]["config"]["code"]]
        assert new[load].get(INSIDE) == copy and new[check].get(INSIDE) == copy, new
        assert {f"{copy}.item -> {load}.item", f"{load}.output -> {check}.input", f"{check}.output -> {copy}.result"} \
            <= set(tab.doc.data["edges"]), tab.doc.data["edges"]
        assert all(tab.doc.blocks[b] == spec for b, spec in before.items())
        menu(win, "Undo").trigger()
        settle()
        assert json.dumps(tab.doc.data, sort_keys=True) == original
    finally:
        clean(win)


# --- 5: while it runs, and after --------------------------------------------------------------------------------------

def test_while_it_runs_the_box_shows_done_of_n_and_how_many_run_and_its_blocks_show_running(win, qtbot, tmp_path):
    """Spec 1 and 5: while the Loop runs (2 of 4 items at the same time), its box shows "done/N · k running" and a
    progress bar whose filled part grows with done/N; the block inside shows as running while any item runs it; each
    of its Log lines carries its block and its item."""
    path = per_item(tmp_path, ["a", "b", "c", "d"], GATED, same_time=2)
    tab = win.open_flow(path)
    filled = []
    try:
        run_in_editor(win, tab)
        wait_running(qtbot, tab, "work")
        qtbot.waitUntil(lambda: progress(tab, 4) == (0, 2), timeout=20_000)
        area, background = bar_area(tab)
        rgb = pixels(render(tab.scene, area))
        off = np.abs(rgb - background.getRgb()[:3]).max(axis=2) > 24
        row = int(off.sum(axis=1).argmax())  # the bar's track: the row most unlike the box
        track = off[row]
        assert track.sum() >= 0.6 * area.width(), (int(track.sum()), area)
        empty = np.array(Counter(map(tuple, rgb[row][track])).most_common(1)[0][0])

        def share() -> float:  # of the track, filled (unlike its empty colour)
            painted = pixels(render(tab.scene, area))[row][track]
            return float((np.abs(painted - empty).max(axis=1) > 24).mean())

        filled.append(share())

        go(tmp_path, 1)

        qtbot.waitUntil(lambda: progress(tab, 4) == (1, 2), timeout=20_000)
        assert tab.scene.nodes["work"].status == "running"  # items 2 and 3 run it
        filled.append(share())

        go(tmp_path, 2)

        qtbot.waitUntil(lambda: progress(tab, 4) == (2, 2), timeout=20_000)
        filled.append(share())

        go(tmp_path, 3, 4)
        wait_done(qtbot, tab)
    finally:
        go(tmp_path, 1, 2, 3, 4)
        wait_done(qtbot, tab)
        clean(win)
    assert all(abs(f - done / 4) <= 0.06 for f, done in zip(filled, (0, 1, 2))), filled
    assert filled[0] < filled[1] < filled[2], filled
    assert tab.last["status"] == "success"
    assert read_json(tmp_path / "report.json") == ["a", "b", "c", "d"]
    for k in range(1, 5):
        said = [line for line in log_lines(win) if f"item {k} is waiting" in line]
        assert said and all("[work]" in line and f"[item {k}/4]" in line for line in said), (k, said)


def test_every_live_log_line_of_a_block_inside_carries_its_own_item_also_the_line_naming_its_table(
        win, qtbot, tmp_path):
    """Spec 5: every live Log line of a block inside, including the line naming the table it gives, carries its block
    and its own item, with items running at the same time (4 items, 2 at once, item k giving a table of k rows)."""
    path = flow_file(tmp_path / "flow.yaml", {
        "manual": MANUAL, "make": make(["a", "b", "c", "d"]), "each": loop(2),
        "work": inside("each", python("import time\ntime.sleep(0.3)\noutput = pl.DataFrame({'n': list(range(index))})",
                                      ("item", "index"), x=60, y=100)),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> work.item", "each.index -> work.index"])
    tab = win.open_flow(path)
    try:
        run_in_editor(win, tab)
        wait_done(qtbot, tab)
    finally:
        clean(win)
    assert tab.last["status"] == "success"
    lines = [line for line in log_lines(win) if "[work]" in line]
    assert [line for line in lines if re.search(r"(\d+) rows", line)], lines  # a line names its table
    for line in lines:
        m = re.search(r"\[item ([1-4])/4\]", line)
        assert m, f"a Log line of work without its item: {line}"
        assert all(int(n) == int(m.group(1)) for n in re.findall(r"(\d+) rows", line)), line


def test_after_the_run_each_block_inside_shows_how_its_items_ended_and_the_run_history_keeps_every_item(
        win, qtbot, tmp_path):
    """Spec 5: after the run (5 items, 2 at the same time, the last fails), the block inside shows how its items ended
    ("✓ 4 ✗ 1"), and hovering it shows the last item's result with which item it was; shown again from the run
    history, it is the same, and the Log has each item's lines, each with its own item."""
    code = ("import time\nprint(f'item {index} says hello')\nif index == 5:\n    time.sleep(0.5)\n"
            "    raise ValueError('no data for e')\noutput = item.upper()")
    path = per_item(tmp_path, ["a", "b", "c", "d", "e"], code, same_time=2)
    tab = win.open_flow(path)
    try:
        run_in_editor(win, tab)
        wait_done(qtbot, tab)
        run_id = tab.runs[-1].run_id

        def check_shown():
            node = tab.scene.nodes["work"]
            ended = node.status_text
            assert re.search(r"✓\s*4", ended) and re.search(r"[✗✕]\s*1", ended), ended
            tip = text_of(node.footer.toolTip())
            assert "item 5" in tip and "no data for e" in tip, tip

        check_shown()
        history = History(win.home.history_db)
        try:
            each = history.blocks(run_id)["each"]
        finally:
            history.close()
        assert each["status"] == "failed" and "item 5" in each["error"], each

        win.show_history_run(run_id, tab)
        settle()

        check_shown()
        for k in range(1, 6):
            said = [line for line in log_lines(win) if f"item {k} says hello" in line]
            assert len(said) == 1 and "[work]" in said[0] and f"[item {k}/5]" in said[0], (k, said)
    finally:
        clean(win)


# --- 6: Stop ----------------------------------------------------------------------------------------------------------

def test_stop_cancels_every_running_item_at_once_and_the_loop_ends_cancelled(win, qtbot, tmp_path):
    """Spec 6: Stop cancels every item running at once (here two Waits of a minute); the Loop ends cancelled."""
    path = flow_file(tmp_path / "flow.yaml", {
        "manual": MANUAL, "make": make([1, 2, 3]), "each": loop(2),
        "pause": inside("each", {"type": "logic.wait", "config": {"seconds": 60}, "ui": {"x": 60, "y": 100}}),
    }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> pause.value",
        "pause.value -> each.result"])
    tab = win.open_flow(path)
    try:
        run_in_editor(win, tab)
        wait_running(qtbot, tab, "pause")
        qtbot.waitUntil(lambda: progress(tab, 3) == (0, 2), timeout=20_000)
        stopped = dt.datetime.now()

        win.stop(tab)
        wait_done(qtbot, tab, timeout=20_000)

        assert tab.last["status"] == "cancelled"
        assert tab.scene.nodes["each"].status == "cancelled"
        history = History(win.home.history_db)
        try:
            run_id = tab.runs[-1].run_id
            each, ended = history.blocks(run_id)["each"], history.run(run_id)["finished"]
        finally:
            history.close()
        assert each["status"] == "cancelled", each
        assert (dt.datetime.fromisoformat(ended) - stopped).total_seconds() < 5, (stopped, ended)
    finally:
        clean(win)


# --- 7: running again, in the editor ----------------------------------------------------------------------------------

def test_right_click_the_loop_run_from_this_block_on_runs_again_only_the_items_that_failed_or_did_not_run(
        win, qtbot, tmp_path):
    """Spec 7, the user's path: after a run where item 2 failed (so item 3 did not run) and a fix, right-click the
    Loop > Run from this block on, with the same inputs runs items 2 and 3 only; item 1 keeps its result; what follows
    the Loop gets every item's result."""
    code = ("import os\nopen(f'ran_{index}.txt', 'a', encoding='utf-8').write('x')\n"
            "if index == 2 and not os.path.exists('fixed.txt'):\n    raise ValueError('no data for b')\n"
            "output = item.upper()")
    tab = win.open_flow(per_item(tmp_path, ["a", "b", "c"], code))
    try:
        run_in_editor(win, tab)
        wait_done(qtbot, tab)
        assert tab.last["status"] == "failed"
        assert {k: ran(tmp_path, str(k)) for k in (1, 2, 3)} == {1: 1, 2: 1, 3: 0}
        (tmp_path / "fixed.txt").write_text("", encoding="utf-8")
        failed = tab.runs[-1]

        seen = context_menu(qtbot, win, tab, header(box(tab, "each")[0]),
                            "Run from this block on, with the same inputs")

        assert any(text.startswith("Run from this block on, with the same inputs") and can for text, can, _ in seen), \
            seen
        qtbot.waitUntil(lambda: tab.runs[-1] is not failed and not tab.running and tab.runs[-1].status is not None,
                        timeout=60_000)
        assert tab.runs[-1].status == "success"
        assert {k: ran(tmp_path, str(k)) for k in (1, 2, 3)} == {1: 1, 2: 2, 3: 1}
        assert read_json(tmp_path / "report.json") == ["A", "B", "C"]
    finally:
        clean(win)


def test_run_from_here_and_run_only_are_not_offered_on_a_block_inside_a_loop_and_say_to_right_click_the_loop(
        win, qtbot, tmp_path):
    """Spec 7: on a block inside a Loop, Run only this block and Run from this block on are not offered in this
    release (also after a run, when they are for other blocks): their menu entries cannot be chosen and say why (right-
    click the loop)."""
    tab = win.open_flow(per_item(tmp_path, ["a", "b"], "output = item"))
    try:
        run_in_editor(win, tab)
        wait_done(qtbot, tab)
        runs = len(tab.runs)

        seen = context_menu(qtbot, win, tab, title(tab, "work"))

        offered = [(text, can, tip) for text, can, tip in seen
                   if text.startswith(("Run only this block", "Run from this block on"))]
        assert any(t.startswith("Run only this block") for t, _, _ in offered), seen
        assert any(t.startswith("Run from this block on") for t, _, _ in offered), seen
        assert not [t for t, can, _ in offered if can], offered
        assert all("loop" in f"{t} {tip}".lower() for t, _, tip in offered), offered
        assert len(tab.runs) == runs
    finally:
        clean(win)


# --- 8, 9: F7 ---------------------------------------------------------------------------------------------------------

def test_f7_refuses_a_connection_from_inside_the_loop_to_outside_naming_it(win, tmp_path):
    """Spec 8 and 9: the editor's Validate (F7) refuses, before any run, a connection from a block inside the Loop to
    one outside (but through result), naming it."""
    tab = win.open_flow(out_of_the_loop_flow(tmp_path))
    try:
        settle()

        win.validate_action.trigger()

        said = list(win.message_boxes)
        win.message_boxes.clear()
        assert [(kind, title) for kind, (title, _) in said] == [("warning", "The flow has problems")], said
        assert OUT_OF_THE_LOOP in said[0][1][1], said
        assert not tab.runs
    finally:
        clean(win)


# --- 11: Help > Documentation and the hover help ----------------------------------------------------------------------

def test_the_documentation_and_the_hover_help_describe_the_loop_and_no_longer_for_each(win):
    """Spec 10 and 11: Help > Documentation has the Loop (in Contents and on the Logic page), telling what is inside,
    the items, item / index / result, At the same time, the results' order, failures and running again; no page tells
    of For Each; the Loop's hover help in the Blocks list tells of its items."""
    from taskloom.editor import docs

    pages = docs.pages(win.registry)
    assert f'#{LOOP}"' in pages["index.html"]
    said = entry(pages[docs.category_page("Logic")], LOOP)
    for words in ("inside", "items", "item", "index", "result", "at the same time", "order", "fail", "run from"):
        assert words in said, f"the Loop's entry does not tell about {words!r}: {said}"
    assert [name for name, page in pages.items() if "For Each" in page or "for_each" in page] == []
    items = win.palette_panel.tree.findItems("Loop", Qt.MatchExactly | Qt.MatchRecursive)
    assert items, "no Loop in the Blocks list"
    assert "item" in plain(items[0].toolTip(0)), items[0].toolTip(0)
