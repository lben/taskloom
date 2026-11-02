"""Splitter and Merger. A Splitter's lines run at the same time (at most "At most at once" of them at a time) or one
after another, top to bottom on the canvas; lines may connect across; the Merger joins them: it waits for every line,
then passes on each line's output by name. A failed line stops no other line and fails the Merger; Run from here after
a fix runs that block, the rest of its line, the Merger and what follows, not the lines that succeeded. Validate refuses
a layout that could never finish, naming the connection. Through the real `taskloom` command (run, validate, check) on
flow files, its run history (history.db), and the offscreen editor. Each test names the point of the specification
(1-10) it protects.

The two settings are found by the labels the specification gives them ("Lines run": at the same time | one after
another; "At most at once"), the Merger's output as its one output that carries data: the tests do not depend on the
names the implementation gives them in the flow file. When blocks ran is read from the run history (when each block
started and finished), as the Run history shows it."""

import datetime as dt
import html
import json
import math
import os
import re
from functools import cache
from pathlib import Path
from xml.etree import ElementTree

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgGenerator  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QToolTip  # noqa: E402

from conftest import events  # noqa: E402
from taskloom.block import DONE, setting_label  # noqa: E402
from taskloom.flow import ERROR_PORT, BlockSpec, block_config  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_block_icons import built_in, canvas_icons, drawing, rows, unlike  # noqa: E402
from tests.test_editor import wait_done  # noqa: E402
from tests.test_inspect_mermaid import contrast  # noqa: E402
from tests.test_paths_editor_editing import clean, field, flow_file, select_only, settle  # noqa: E402
from tests.test_paths_editor_running import right_click  # noqa: E402
from tests.test_theme import TEXT, choose, most_common  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TICK = 0.016  # a clock tick on Windows: two times taken in a row may differ by this much
SAME_TIME, ONE_AFTER_ANOTHER = "at the same time", "one after another"
# Tabler Icons' outline arrows-split and arrows-join (MIT), the approved icons of the Splitter and the Merger.
ARROWS_SPLIT = ('<path d="M21 17h-8l-3.5 -5h-6.5"/><path d="M21 7h-8l-3.495 5"/><path d="M18 10l3 -3l-3 -3"/>'
                '<path d="M18 20l3 -3l-3 -3"/>')
ARROWS_JOIN = '<path d="M3 7h5l3.5 5h9.5"/><path d="M3 17h5l3.495 -5"/><path d="M18 15l3 -3l-3 -3"/>'


# --- the blocks, found by what the specification calls them ---------------------------------------------------------

@cache
def block_class(type_id: str):
    cls = discover(None).get(type_id)
    assert cls is not None, f"there is no {type_id} block"
    return cls


def setting(cls, label: str) -> str:
    """The key, in the flow file, of the setting Properties shows as `label`."""
    keys = {setting_label(key, f): key for key, f in cls.config.items()}
    assert label in keys, f"{cls.title} has no setting labelled {label!r} (its settings: {sorted(keys)})"
    return keys[label]


def normal(text) -> str:
    return " ".join(str(text).replace("_", " ").split()).lower()


def choice(f, shown: str):
    """The value of the choice setting `f` that Properties shows as `shown`."""
    options = getattr(f, "options", None)
    assert options, f"Lines run is not a choice: {f!r}"
    found = [o for o in options if normal(o) == shown]
    assert found, f"{shown!r} is not one of the choices of Lines run: {options}"
    return found[0]


def splitter(lines_run: str | None = None, at_most=None, x=0, y=0) -> dict:
    """A Splitter block; a setting left out (None) as it is when the block is added."""
    cls = block_class("logic.splitter")
    config = {}
    if lines_run is not None:
        key = setting(cls, "Lines run")
        config[key] = choice(cls.config[key], lines_run)
    if at_most is not None:
        config[setting(cls, "At most at once")] = at_most
    return {"type": "logic.splitter", "config": config, "ui": {"x": x, "y": y}}


def merger(x=0, y=0) -> dict:
    block_class("logic.merger")
    return {"type": "logic.merger", "ui": {"x": x, "y": y}}


def merged() -> str:
    """The Merger's output: its one output that carries data."""
    cls = block_class("logic.merger")
    outputs = [p for p in cls.ports(block_config(cls, BlockSpec(id="merge", type="logic.merger")))[1]
               if p not in (DONE, ERROR_PORT)]
    assert len(outputs) == 1, f"the Merger should have one output, passing on the lines' outputs: {outputs}"
    return outputs[0]


def python(code: str, inputs=("input",), outputs=("output",), x=0, y=0) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code},
            "ui": {"x": x, "y": y}}


def step(name: str, seconds: float = 0.0, x=400, y=0) -> dict:
    """A block of a line: it waits `seconds`, then passes on its name."""
    return python(f"import time\ntime.sleep({seconds})\noutput = {name!r}", x=x, y=y)


def counted(name: str, seconds: float = 0.0, x=400, y=0, fails_unless: str | None = None) -> dict:
    """A block of a line that notes each time it runs (in ran_<name>.txt), waits `seconds`, then passes on its name
    and what it got; with `fails_unless`, it fails until that file exists."""
    check = (f"import os\nif not os.path.exists({fails_unless!r}):\n    raise ValueError('the costs file is missing')\n"
             if fails_unless else "")
    return python(f"import time\nopen('ran_{name}.txt', 'a', encoding='utf-8').write('x')\n{check}"
                  f"time.sleep({seconds})\noutput = [{name!r}, input]", x=x, y=y)


def saver(filename: str, x=1600, y=0) -> dict:
    """A block that writes what it got (as JSON) to `filename`, and notes each time it runs (in ran_<its stem>.txt)."""
    return python(f"import json\nopen('ran_{Path(filename).stem}.txt', 'a', encoding='utf-8').write('x')\n"
                  f"with open({filename!r}, 'w', encoding='utf-8') as f:\n    json.dump(input, f)",
                  outputs=(), x=x, y=y)


MANUAL = {"type": "trigger.manual", "ui": {"x": -400, "y": 0}}
LOAD = python("output = 'today'", inputs=(), x=-200)  # what the Splitter gets, for each of its lines


def fed(splitter_id: str = "split") -> list[str]:
    """manual -> load -> the Splitter's value."""
    return ["manual.start -> load.after", f"load.output -> {splitter_id}.value"]


def lines_into(splitter_id: str, *firsts: str, port: str = "input") -> list[str]:
    return [f"{splitter_id}.lines -> {b}.{port}" for b in firsts]


def into_merger(merger_id: str, *lasts: str) -> list[str]:
    return [f"{b}.output -> {merger_id}.lines" for b in lasts]


def merger_out(merger_id: str, dst: str, port: str = "input") -> str:
    return f"{merger_id}.{merged()} -> {dst}.{port}"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def ran(folder: Path, name: str) -> int:
    """How many times the counted block `name` (or the saver of name.json) ran."""
    path = folder / f"ran_{name}.txt"
    return len(path.read_text(encoding="utf-8")) if path.exists() else 0


# --- running and reading the run history ----------------------------------------------------------------------------

def run(env, flow: Path, *args):
    """`taskloom run` (with --json-events): its result and the run's number. It ran (succeeded or failed)."""
    result = env.taskloom("run", flow, "--json-events", *args)
    assert result.returncode in (0, 1), result.stdout[-3000:] + result.stderr[-3000:]
    return result, env.last_run()[0]


def result_of(env, run_id: int, block: str) -> dict:
    [(status, error, summary, previews)] = env.query(
        "SELECT status, error, summary, previews FROM blocks WHERE run_id=? AND block=?", run_id, block)
    return {"status": status, "error": error or "", "summary": summary or "", "previews": json.loads(previews or "{}")}


def spans(env, run_id: int) -> dict:
    """{block: (when it started, when it finished)} for the blocks of the run that started."""
    return {b: (dt.datetime.fromisoformat(s), dt.datetime.fromisoformat(f)) for b, s, f in env.query(
        "SELECT block, started, finished FROM blocks WHERE run_id=? AND started IS NOT NULL AND finished IS NOT NULL",
        run_id)}


def together(a, b) -> float:
    """For how many seconds two blocks ran at the same time (negative: how long between them)."""
    return (min(a[1], b[1]) - max(a[0], b[0])).total_seconds()


def apart(times: dict, blocks, at_least: float) -> list:
    """The pairs of `blocks` that did not run together for at least `at_least` seconds."""
    blocks = list(blocks)
    return [(a, b, round(together(times[a], times[b]), 3)) for i, a in enumerate(blocks) for b in blocks[i + 1:]
            if together(times[a], times[b]) < at_least]


def sequence(stdout: str, blocks) -> list[tuple[str, str]]:
    """The block_started / block_finished events of `blocks`, in the order the run told them."""
    return [(e["event"], e["block"]) for e in events(stdout)
            if e["event"] in ("block_started", "block_finished") and e.get("block") in blocks]


# --- 1, 3, 5: lines at the same time, joined by the Merger ----------------------------------------------------------

def three_lines_flow(folder: Path, split: dict, seconds=(0.0, 0.0, 0.0), file_order=(0, 1, 2)) -> Path:
    """load -> split -> load_sales (top), load_costs, load_stock (bottom) -> merge -> report (report.json); the line
    blocks written into the file in `file_order` (indexes into top-to-bottom)."""
    top_to_bottom = ["load_sales", "load_costs", "load_stock"]
    names = [top_to_bottom[i] for i in file_order]
    line_blocks = {top_to_bottom[i]: step(top_to_bottom[i], seconds[i], y=-300 + 300 * i) for i in file_order}
    blocks = {"manual": MANUAL, "load": python("output = {'day': 'monday'}", inputs=(), x=-200),
              "split": split, **line_blocks, "merge": merger(x=800), "report": saver("report.json")}
    edges = ["manual.start -> load.after", "load.output -> split.value",
             *lines_into("split", *names), *into_merger("merge", *names), merger_out("merge", "report")]
    return flow_file(folder / "flow.yaml", blocks, edges)


def test_lines_run_at_the_same_time_by_default_and_the_merger_passes_on_each_lines_output_by_name(env):
    """Spec 1 (Lines run defaults to at the same time; At most at once empty: all lines at once), 3 (the lines run
    concurrently), 5 (the Merger waits for every line, then passes on {line name: its output})."""
    flow = three_lines_flow(env.root, splitter(x=0), seconds=(1.2, 1.2, 1.2))

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "report.json") == {"load_sales": "load_sales", "load_costs": "load_costs",
                                                   "load_stock": "load_stock"}
    times = spans(env, run_id)
    assert apart(times, ["load_sales", "load_costs", "load_stock"], at_least=0.6) == [], times
    assert times["merge"][0] >= max(times[b][1] for b in ("load_sales", "load_costs", "load_stock")) - \
        dt.timedelta(seconds=TICK), times  # it waited for every line


def test_a_lines_own_blocks_run_one_after_another_in_the_flows_order(env):
    """Spec 3: while the lines run concurrently, each line's own blocks run one after another, in the flow's order:
    the two blocks after branch, in the top line, do not run together, but each runs together with the other line."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(),
        "branch": step("branch", y=-300), "early": step("early", 0.6, x=700, y=-450),
        "late": step("late", 0.6, x=700, y=-150), "other": step("other", 2.0, y=300),
        "merge": merger(x=1100), "report": saver("report.json"),
    }, [*fed(), *lines_into("split", "branch", "other"), "branch.output -> early.input",
        "branch.output -> late.input", *into_merger("merge", "early", "late", "other"), merger_out("merge", "report")])

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert sequence(result.stdout, ["early", "late"]) == [("block_started", "early"), ("block_finished", "early"),
                                                          ("block_started", "late"), ("block_finished", "late")]
    t = spans(env, run_id)
    assert together(t["early"], t["other"]) >= 0.3 and together(t["late"], t["other"]) >= 0.3, t
    assert read_json(env.root / "report.json") == {"early": "early", "late": "late", "other": "other"}


@pytest.mark.parametrize("lines_run", [SAME_TIME, ONE_AFTER_ANOTHER])
def test_each_line_gets_its_own_copy_of_the_value_so_lines_cannot_change_each_others_data(env, lines_run):
    """Spec 1 (each line gets the value that arrived, its own copy) and 3 (each running block has its own copy of what
    it receives): two lines change the list they got; neither sees the other's change, in either mode."""
    def appends(name, y):
        return python(f"import time\ninput['seen'].append({name!r})\ntime.sleep(0.5)\noutput = input['seen']", y=y,
                      x=400)

    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": python("output = {'seen': ['load']}", inputs=(), x=-200),
        "split": splitter(lines_run), "first": appends("first", -200), "second": appends("second", 200),
        "merge": merger(x=800), "report": saver("report.json"),
    }, ["manual.start -> load.after", "load.output -> split.value", *lines_into("split", "first", "second"),
        *into_merger("merge", "first", "second"), merger_out("merge", "report")])

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "report.json") == {"first": ["load", "first"], "second": ["load", "second"]}


def test_at_most_at_once_runs_that_many_lines_and_starts_the_next_as_soon_as_one_ends_in_line_order(env):
    """Spec 3 (up to At most at once lines at a time; a new line starts as soon as one ends, in line order) and 2
    (line order is top to bottom on the canvas, not the order in the file): of three lines, at most 2 at once, the two
    top ones start; the bottom one starts when the first of them ends, while the other still runs."""
    flow = three_lines_flow(env.root, splitter(at_most=2), seconds=(0.6, 2.0, 0.6), file_order=(2, 1, 0))

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    t = spans(env, run_id)
    sales, costs, stock = t["load_sales"], t["load_costs"], t["load_stock"]
    assert together(sales, costs) >= 0.3, t  # the two top lines together
    assert stock[0] >= sales[1] - dt.timedelta(seconds=TICK), t  # the third waited for a line to end ...
    assert (costs[1] - stock[0]).total_seconds() >= 0.5, t  # ... the first to end, not both
    assert read_json(env.root / "report.json") == {"load_sales": "load_sales", "load_costs": "load_costs",
                                                   "load_stock": "load_stock"}


# --- 2, 3: one after another, top to bottom --------------------------------------------------------------------------

def test_one_after_another_runs_each_line_to_its_end_top_to_bottom_then_left_to_right(env):
    """Spec 2 (line order: top to bottom by where each line's first block is, then left to right) and 3 (one after
    another: line 1 runs to its end, then line 2, ...). The file lists the lines bottom first, and the second block of
    each line sits elsewhere on the canvas: only the first block's place counts."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(ONE_AFTER_ANOTHER),
        "x1": step("x1", 0.2, x=400, y=300), "x2": python("output = input", x=700, y=-900),
        "z1": step("z1", 0.2, x=700, y=-200), "z2": python("output = input", x=1000, y=300),
        "y1": step("y1", 0.2, x=400, y=-200), "y2": python("output = input", x=700, y=600),
        "merge": merger(x=1300), "report": saver("report.json"),
    }, [*lines_into("split", "x1", "z1", "y1"), *fed(),
        "x1.output -> x2.input", "z1.output -> z2.input", "y1.output -> y2.input",
        *into_merger("merge", "x2", "z2", "y2"), merger_out("merge", "report")])

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    order = ["y1", "y2", "z1", "z2", "x1", "x2"]
    assert sequence(result.stdout, order) == [(kind, b) for b in order for kind in ("block_started", "block_finished")]
    assert read_json(env.root / "report.json") == {"x2": "x1", "z2": "z1", "y2": "y1"}


# --- 4: the Splitter's Log line; the Log and the run history with lines at the same time -----------------------------

@pytest.mark.parametrize("lines_run, at_most, said", [
    (SAME_TIME, 2, "3 lines at the same time, at most 2 at once: load_sales, load_costs, load_stock"),
    (ONE_AFTER_ANOTHER, None, "3 lines one after another: 1 load_sales, 2 load_costs, 3 load_stock"),
], ids=["at_the_same_time", "one_after_another"])
def test_the_splitter_writes_one_log_line_naming_its_lines_in_order(env, lines_run, at_most, said):
    """Spec 4 (the Splitter writes one Log line when it starts its lines, as the specification words it) with the lines
    in line order (spec 2), not the file's."""
    flow = three_lines_flow(env.root, splitter(lines_run, at_most), file_order=(1, 2, 0))

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block='split'", run_id)] == [said]


def test_lines_at_the_same_time_keep_every_log_line_tagged_in_order_and_the_run_history_whole(env):
    """Spec 4: while lines run at the same time, each block's Log lines keep their [block] tag and arrive in the order
    they happen; the run history keeps every line, block result and status (nothing lost or mixed up): three lines each
    printing 200 lines at once, as the editor reads the run (JSON events) and as `taskloom run` prints it."""
    n, names = 200, ("north", "south", "east")
    printing = {name: python(f"import time\ntime.sleep(0.3)\nfor i in range({n}):\n    print({name!r}, i)\n"
                             f"time.sleep(0.3)\noutput = {name!r}", y=-300 + 300 * k) for k, name in enumerate(names)}
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(), **printing, "merge": merger(x=800),
        "report": saver("report.json"),
    }, [*fed(), *lines_into("split", *names), *into_merger("merge", *names),
        merger_out("merge", "report")])
    wanted = {name: [f"{name} {i}" for i in range(n)] for name in names}

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert apart(spans(env, run_id), names, at_least=0.2) == []  # they did print at the same time
    printed = [line for line in result.stdout.splitlines() if line.strip()]
    broken = []
    for line in printed:
        try:
            json.loads(line)
        except ValueError:
            broken.append(line)
    assert broken == [], f"lines of the run's output that are not one whole event each: {broken[:5]}"
    told = events(result.stdout)
    assert {name: [e["message"] for e in told if e["event"] == "log" and e.get("block") == name]
            for name in names} == wanted
    assert [e["ts"] for e in told] == sorted(e["ts"] for e in told), "events told out of the order they happened"
    logs = env.query("SELECT block, ts, message FROM logs WHERE run_id=? ORDER BY rowid", run_id)
    assert {name: [m for b, _, m in logs if b == name] for name in names} == wanted
    assert [ts for _, ts, _ in logs] == sorted(ts for _, ts, _ in logs), "the run history's Log is out of order"
    assert env.block_status(run_id) == {"manual": "success", "load": "success", "split": "success",
                                        "north": "success", "south": "success", "east": "success", "merge": "success",
                                        "report": "success"}
    assert env.last_run() == (run_id, "success")

    printed_log = env.taskloom("run", flow)  # as `taskloom run` prints the Log: "HH:MM:SS [block] INFO: message"
    assert printed_log.returncode == 0, printed_log.stderr[-3000:]
    lines = [line for line in printed_log.stderr.splitlines() if re.search(r"\b(north|south|east) \d+", line)]
    mixed = [line for line in lines if not re.fullmatch(r"\d\d:\d\d:\d\d \[(\w+)\] INFO: \1 \d+", line)]
    assert mixed == [], f"Log lines mixed up or without their own block's tag: {mixed[:5]}"
    assert {name: [line.split("INFO: ")[1] for line in lines if f"[{name}]" in line] for name in names} == wanted


# --- 3: connections across, a failed line, nested Splitters, a flow without a Splitter -------------------------------

CROSS = "load_stock.output -> combine.stock"  # from the bottom line (3) into the top one (1)


def across_flow(folder: Path, split: dict) -> Path:
    """Three lines; the top one's second block, combine, also uses the bottom line's output (CROSS)."""
    return flow_file(folder / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": split,
        "load_sales": step("sales", 0.5, y=-300), "load_costs": step("costs", 0.2, y=0),
        "load_stock": step("stock", 1.5, y=300),
        "combine": python("output = [sales, stock]", ("sales", "stock"), x=800, y=-300),
        "merge": merger(x=1200), "report": saver("report.json"),
    }, [*fed(), *lines_into("split", "load_sales", "load_costs", "load_stock"),
        "load_sales.output -> combine.sales", CROSS, *into_merger("merge", "combine", "load_costs", "load_stock"),
        merger_out("merge", "report")])


NEVER = "load_stock.output -> load_sales.stock"  # from the bottom line (3) into the top line's first block


def never_flow(folder: Path, split: dict) -> Path:
    """Three lines; the top one's first block, load_sales, also needs the bottom line's output (NEVER): line 1 cannot
    start before line 3 has made it."""
    return flow_file(folder / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": split,
        "load_sales": python("output = [input, stock]", ("input", "stock"), y=-300),
        "load_costs": step("costs", 0.2, y=0), "load_stock": step("stock", 0.2, y=300),
        "merge": merger(x=1200), "report": saver("report.json"),
    }, [*fed(), *lines_into("split", "load_sales", "load_costs", "load_stock"), NEVER,
        *into_merger("merge", "load_sales", "load_costs", "load_stock"), merger_out("merge", "report")])


def test_a_line_that_uses_another_lines_output_waits_until_it_has_arrived(env):
    """Spec 3 (a line may use outputs of another line: its block waits until those have arrived; lines connecting
    across are fine at the same time, and Validate does not refuse them, spec 7)."""
    flow = across_flow(env.root, splitter())
    checked = env.taskloom("validate", flow)
    assert checked.returncode == 0, checked.stderr

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    t = spans(env, run_id)
    assert together(t["load_sales"], t["load_stock"]) >= 0.25, t  # the lines ran at the same time ...
    assert t["combine"][0] >= t["load_stock"][1] - dt.timedelta(seconds=TICK), t  # ... combine waited for stock
    assert read_json(env.root / "report.json") == {"combine": ["sales", "stock"], "load_costs": "costs",
                                                   "load_stock": "stock"}


def costs_flow(folder: Path, split: dict) -> Path:
    """The user's case: three lines; load_costs fails until fixed.txt exists; load_costs -> costs_total in its line."""
    return flow_file(folder / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": split,
        "load_sales": counted("load_sales", 0.8, y=-300),
        "load_costs": counted("load_costs", y=0, fails_unless="fixed.txt"),
        "costs_total": counted("costs_total", x=700, y=0),
        "load_stock": counted("load_stock", 0.8, y=300),
        "merge": merger(x=1100), "report": saver("report.json"),
    }, [*fed(), *lines_into("split", "load_sales", "load_costs", "load_stock"),
        "load_costs.output -> costs_total.input", *into_merger("merge", "load_sales", "costs_total", "load_stock"),
        merger_out("merge", "report")])


@pytest.mark.parametrize("lines_run", [SAME_TIME, ONE_AFTER_ANOTHER])
def test_a_failed_line_stops_no_other_line_and_the_merger_fails_naming_it(env, lines_run):
    """Spec 3 (a failed line does not stop the others, in both modes: nothing is cancelled) and 5 (a line the Merger
    waits for failed: the Merger fails instead of being skipped, saying which line failed, and the error is in the
    Log; what follows it does not run)."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(lines_run),
        "load_sales": step("load_sales", 0.8, y=-300),
        "load_costs": python("raise ValueError('no costs file today')", y=0, x=400),
        "load_stock": step("load_stock", 0.8, y=300),
        "merge": merger(x=800), "report": saver("report.json"),
    }, [*fed(), *lines_into("split", "load_sales", "load_costs", "load_stock"),
        *into_merger("merge", "load_sales", "load_costs", "load_stock"), merger_out("merge", "report")])

    result, run_id = run(env, flow)

    assert result.returncode == 1
    assert env.block_status(run_id) == {"manual": "success", "load": "success", "split": "success",
                                        "load_sales": "success", "load_costs": "failed", "load_stock": "success",
                                        "merge": "failed", "report": "skipped"}
    t = spans(env, run_id)
    assert t["load_stock"][1] > t["load_costs"][1], t  # it went on after load_costs failed
    assert "load_costs failed" in result_of(env, run_id, "merge")["error"]
    errors = [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block='merge' AND level='ERROR'",
                                      run_id)]
    assert any("load_costs failed" in m for m in errors), errors
    assert not (env.root / "report.json").exists()
    assert env.last_run() == (run_id, "failed")


def test_the_mergers_error_output_handles_a_failed_line_as_any_blocks(env):
    """Spec 5: the Merger's error output can handle its failure (a failed line) as any block's: the block connected
    there runs and gets the error, which names the failed line."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(),
        "load_sales": step("load_sales", y=-200),
        "load_costs": python("raise ValueError('no costs file')", x=400, y=200),
        "merge": merger(x=800), "on_failure": saver("failure.json", y=300),
    }, [*fed(), *lines_into("split", "load_sales", "load_costs"),
        *into_merger("merge", "load_sales", "load_costs"), "merge.error -> on_failure.input"])

    _, run_id = run(env, flow)

    assert env.block_status(run_id)["merge"] == "failed"
    assert env.block_status(run_id)["on_failure"] == "success"
    failure = read_json(env.root / "failure.json")
    assert failure["block"] == "merge" and "load_costs failed" in failure["error"], failure


def test_a_line_that_delivers_nothing_without_failing_skips_the_merger_saying_which(env):
    """Spec 5: a line delivered nothing without failing (an If sent it nowhere): the Merger is skipped, as any block
    whose input got nothing, saying which line."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(),
        "plain": python("output = input", y=-200, x=400),
        "route": {"type": "logic.if", "config": {"condition": "value == 'never'"}, "ui": {"x": 400, "y": 200}},
        "merge": merger(x=800), "report": saver("report.json"),
    }, [*fed(), "split.lines -> plain.input",
        "split.lines -> route.value", "plain.output -> merge.lines", "route.true -> merge.lines",
        merger_out("merge", "report")])

    _, run_id = run(env, flow)

    merge = result_of(env, run_id, "merge")
    assert merge["status"] == "skipped" and "route" in merge["summary"], merge
    assert env.block_status(run_id)["plain"] == "success"
    assert not (env.root / "report.json").exists()


@pytest.mark.parametrize("lines_run", [SAME_TIME, ONE_AFTER_ANOTHER])
def test_validate_refuses_a_splitter_inside_a_line_naming_it(env, lines_run):
    """As agreed with the user, for this release (spec 3's nested Splitters come later): a Splitter inside another
    Splitter's line is refused by Validate before any run, in every mode, naming it; the run does not start."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "outer": splitter(lines_run),
        "inner": splitter(x=400, y=-300), "x": step("x", x=800, y=-500), "y": step("y", x=800, y=-100),
        "inner_merge": merger(x=1200, y=-300),
        "b": step("b", x=400, y=300),
        "outer_merge": merger(x=1600), "report": saver("report.json", x=2000),
    }, [*fed("outer"), "outer.lines -> inner.value", "outer.lines -> b.input",
        *lines_into("inner", "x", "y"), *into_merger("inner_merge", "x", "y"),
        f"inner_merge.{merged()} -> outer_merge.lines", "b.output -> outer_merge.lines",
        merger_out("outer_merge", "report")])

    checked = env.taskloom("validate", flow)

    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert [line for line in checked.stderr.splitlines()
            if line.startswith("error: inner:") and "inside line 1 of outer" in line], checked.stderr
    refused = env.taskloom("run", flow)
    assert refused.returncode == 2 and "error: inner:" in refused.stderr, refused.stderr
    assert no_runs(env)


@pytest.mark.parametrize("with_splitter", [False, True], ids=["no_splitter", "a_splitter_left_at_one_line"])
def test_without_a_splitter_or_with_one_left_at_one_line_blocks_run_one_at_a_time_in_flow_order(env, with_splitter):
    """Spec 3: a flow with no Splitter (or a Splitter left at one line) runs exactly as before: one block at a time,
    in the flow's order, also where it branches, with the same results. (Without a Splitter it holds today too: it
    protects the flows the user has.)"""
    blocks = {"manual": MANUAL, "load": LOAD, **({"fan_out": splitter(x=-100)} if with_splitter else {}),
              "work": python("output = input", x=0),
              "left": python("import time\ntime.sleep(0.4)\noutput = 'left'", inputs=("value",), x=300, y=-200),
              "right": python("import time\ntime.sleep(0.4)\noutput = 'right'", inputs=("value",), x=300, y=200),
              "join": python("output = [left, right]", ("left", "right"), x=600), "report": saver("report.json")}
    edges = ["manual.start -> load.after", "work.output -> left.value", "work.output -> right.value",
             "left.output -> join.left", "right.output -> join.right", "join.output -> report.input"]
    edges += (["load.output -> fan_out.value", "fan_out.lines -> work.input"] if with_splitter
              else ["load.output -> work.input"])
    flow = flow_file(env.root / "flow.yaml", blocks, edges)

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    order = ["load", *(["fan_out"] if with_splitter else []), "work", "left", "right", "join", "report"]
    assert sequence(result.stdout, order) == [(kind, b) for b in order for kind in ("block_started", "block_finished")]
    assert read_json(env.root / "report.json") == ["left", "right"]


# --- 6: Run from here after a failed line; Run only this block on the Merger -----------------------------------------

def test_run_from_the_failed_block_reruns_its_line_the_merger_and_what_follows_not_the_lines_that_succeeded(env):
    """Spec 6 (the user's case): after a run where load_costs failed, and a fix, Run from here on it runs it, the rest
    of its line, the Merger and what follows; the lines that succeeded are not run again: their kept outputs reach the
    Merger. Then Run only this block on the Merger gets the lines' kept outputs."""
    flow = costs_flow(env.root, splitter())
    first, failed_run = run(env, flow)
    assert first.returncode == 1
    assert env.block_status(failed_run) == {"manual": "success", "load": "success", "split": "success",
                                            "load_sales": "success", "load_costs": "failed", "costs_total": "skipped",
                                            "load_stock": "success", "merge": "failed", "report": "skipped"}
    (env.root / "fixed.txt").write_text("", encoding="utf-8")

    second, from_run = run(env, flow, "--from", "load_costs", "--inputs-from", failed_run)

    assert second.returncode == 0, second.stderr[-3000:]
    assert {name: ran(env.root, name) for name in ("load_sales", "load_costs", "costs_total", "load_stock")} == \
        {"load_sales": 1, "load_costs": 2, "costs_total": 1, "load_stock": 1}
    whole = {"load_sales": ["load_sales", "today"], "costs_total": ["costs_total", ["load_costs", "today"]],
             "load_stock": ["load_stock", "today"]}
    assert read_json(env.root / "report.json") == whole
    assert ran(env.root, "report") == 1

    third, only_run = run(env, flow, "--only", "merge", "--inputs-from", from_run)

    assert third.returncode == 0, third.stderr[-3000:]
    merge = result_of(env, only_run, "merge")
    assert merge["status"] == "success" and json.loads(merge["previews"][merged()]) == whole, merge
    assert ran(env.root, "report") == 1 and ran(env.root, "load_sales") == 1


# --- 7: Validate refuses a layout that could never finish -----------------------------------------------------------

def no_runs(env) -> bool:
    db = env.home / "history.db"
    return not db.exists() or not env.query("SELECT name FROM sqlite_master WHERE name='runs'") or \
        not env.query("SELECT id FROM runs")


@pytest.mark.parametrize("lines_run, at_most", [(ONE_AFTER_ANOTHER, None), (SAME_TIME, 2)],
                         ids=["one_after_another", "at_the_same_time_at_most_2_of_3"])
def test_validate_refuses_a_connection_from_a_later_line_into_an_earlier_one_naming_it(env, lines_run, at_most):
    """Spec 7: in one-after-another mode, and at the same time with an At most at once lower than the number of
    lines, a connection from a later line into an earlier line could never finish: taskloom validate and check refuse
    it before any run, naming the connection, and taskloom run refuses to start."""
    flow = never_flow(env.root, splitter(lines_run, at_most))

    checked = env.taskloom("validate", flow)
    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert [line for line in checked.stderr.splitlines() if line.startswith("error:") and NEVER in line], \
        checked.stderr
    found = env.taskloom("check", flow)
    assert found.returncode == 2, found.stdout + found.stderr
    assert [line for line in found.stdout.splitlines() if "needs you" in line and NEVER in line], found.stdout
    refused = env.taskloom("run", flow)
    assert refused.returncode == 2 and NEVER in refused.stderr, refused.stderr
    assert no_runs(env)
    assert not (env.root / "report.json").exists()


@pytest.mark.parametrize("at_most", [None, 3], ids=["all_lines_at_once", "at_most_as_many_as_the_lines"])
def test_validate_accepts_lines_connecting_across_when_all_of_them_run_at_once(env, at_most):
    """Spec 7: at the same time with no limit, or a limit not lower than the number of lines, the same connection
    across is fine: validate finds no problem."""
    flow = across_flow(env.root, splitter(SAME_TIME, at_most))

    checked = env.taskloom("validate", flow)

    assert checked.returncode == 0, checked.stderr
    assert CROSS not in checked.stderr


def test_validate_warns_about_a_splitter_with_one_line_and_a_merger_with_one_connection(env):
    """Spec 7: a Splitter with fewer than two lines and a Merger with fewer than two connections are warned about,
    not refused."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "fan_out": splitter(), "only_line": step("only_line"), "gather": merger(x=800),
        "report": saver("report.json"),
    }, [*fed("fan_out"), "fan_out.lines -> only_line.input", "only_line.output -> gather.lines",
        merger_out("gather", "report")])

    checked = env.taskloom("validate", flow)

    assert checked.returncode == 0, checked.stderr
    warnings = [line for line in checked.stderr.splitlines() if line.startswith("warning:")]
    assert any("fan_out" in line for line in warnings), warnings
    assert any("gather" in line for line in warnings), warnings


@pytest.mark.parametrize("at_most", [0, "two"])
def test_at_most_at_once_takes_a_whole_number_of_one_or_more(env, at_most):
    """Spec 1: At most at once is a whole number, 1 or more: anything else is refused, naming the setting."""
    flow = three_lines_flow(env.root, splitter(SAME_TIME, at_most))

    checked = env.taskloom("validate", flow)

    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert [line for line in checked.stderr.splitlines() if "split" in line and "At most at once" in line], \
        checked.stderr


@pytest.mark.parametrize("into_the_merger, passed_on", [
    (["left.output", "pair.north", "pair.south"], {"left": "left", "pair.north": "n", "pair.south": "s"}),
    (["left.output"], {"left": "left"}),
], ids=["a_block_connecting_two_outputs", "only_one_line_connected"])
def test_the_merger_keys_each_line_by_its_block_and_block_output_like_python_code(env, into_the_merger, passed_on):
    """Spec 5: the Merger passes on {line name: that line's output} keyed like Python Code's inputs from several
    blocks: the block's name, or block.output when a block connects several of its outputs; also when only one line is
    connected."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(),
        "left": step("left", y=-200),
        "pair": python("north = 'n'\nsouth = 's'", outputs=("north", "south"), x=400, y=200),
        "merge": merger(x=800), "report": saver("report.json"),
    }, [*fed(), "split.lines -> left.input", "split.lines -> pair.input",
        *[f"{source} -> merge.lines" for source in into_the_merger], merger_out("merge", "report")])

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "report.json") == passed_on


# --- 8: a block's timeout ends only that block ---------------------------------------------------------------------

def test_a_blocks_timeout_ends_only_that_block_the_other_line_goes_on(env):
    """Spec 8: a block's timeout ends only that block: the line running at the same time goes on to its end."""
    flow = flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(),
        "slow": {"type": "logic.wait", "config": {"seconds": 30}, "timeout": "1s", "ui": {"x": 400, "y": -200}},
        "steady": step("steady", 2.5, y=200),
        "merge": merger(x=800),
    }, [*fed(), "split.lines -> slow.value", "split.lines -> steady.input",
        "slow.value -> merge.lines", "steady.output -> merge.lines"])

    _, run_id = run(env, flow)

    slow, steady = result_of(env, run_id, "slow"), result_of(env, run_id, "steady")
    assert slow["status"] == "failed" and "timed out" in slow["error"], slow
    assert steady["status"] == "success", steady
    t = spans(env, run_id)
    assert together(t["slow"], t["steady"]) >= 0.5, t  # the two lines ran at the same time ...
    assert (t["steady"][1] - t["slow"][1]).total_seconds() >= 0.5, t  # ... and steady went on after the timeout
    assert (t["slow"][1] - t["slow"][0]).total_seconds() < 10, t


# --- 9: the editor ---------------------------------------------------------------------------------------------------

def test_splitter_and_merger_are_in_the_blocks_list_under_logic_with_the_split_and_join_arrows(win, tmp_path):
    """Spec 1, 5 and 9: both blocks are in the Blocks list under Logic, as Splitter and Merger; on the canvas the
    Splitter shows the split arrows and the Merger the join arrows (Tabler Icons' arrows-split and arrows-join): of
    every built-in block's icon, theirs is the most like that drawing."""
    logic = [item.text(0) for item in rows(win.palette_panel).get("Logic", [])]
    assert "Splitter" in logic and "Merger" in logic, logic
    icons = canvas_icons(win, tmp_path, built_in(win.registry))
    for type_id, body in (("logic.splitter", ARROWS_SPLIT), ("logic.merger", ARROWS_JOIN)):
        reference = drawing(body)
        closest = min(icons, key=lambda t: unlike(icons[t], reference))
        assert closest == type_id, (f"the {type_id} drawing is most like the icon of {closest} "
                                    f"({unlike(icons[closest], reference):.3f} against "
                                    f"{unlike(icons[type_id], reference):.3f})")


def row_shown(win, label: str) -> bool:
    try:
        widget = field(win, label)
    except AssertionError:
        return False
    return not widget.isHidden()


def test_at_most_at_once_is_shown_only_when_lines_run_at_the_same_time(win, tmp_path):
    """Spec 1: Lines run offers at the same time and one after another (at the same time when the block is added);
    At most at once is shown only for at the same time."""
    path = flow_file(tmp_path / "flow.yaml", {
        "manual": MANUAL, "split": splitter(), "a": step("a", y=-200), "b": step("b", y=200),
    }, ["manual.start -> split.after", *lines_into("split", "a", "b")])
    tab = win.open_flow(path)
    try:
        settle()
        select_only(tab, "split")

        lines_run = field(win, "Lines run")
        assert isinstance(lines_run, QComboBox), lines_run
        offered = [normal(lines_run.itemText(i)) for i in range(lines_run.count())]
        assert offered == [SAME_TIME, ONE_AFTER_ANOTHER] and normal(lines_run.currentText()) == SAME_TIME, offered
        assert row_shown(win, "At most at once")

        lines_run.setCurrentIndex(offered.index(ONE_AFTER_ANOTHER))
        settle()

        assert not row_shown(win, "At most at once")
    finally:
        clean(win)


SVG = "{http://www.w3.org/2000/svg}"
NUMBER = re.compile(r"\(?#?([1-9])[.)]?")


def drawn_texts(scene, rect: QRectF) -> list[dict]:
    """Every text the scene draws in `rect`, as Qt's SVG output records it: its text, its centre (about: from where it
    starts on its baseline and its font size) in scene coordinates, its size, and its colour (with its opacity)."""
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    svg = QSvgGenerator()
    svg.setOutputDevice(buffer)
    svg.setSize(QSize(math.ceil(rect.width()), math.ceil(rect.height())))
    svg.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
    painter = QPainter(svg)
    scene.render(painter, QRectF(0, 0, rect.width(), rect.height()), rect)
    painter.end()
    buffer.close()
    found = []

    def walk(element, m):
        transform = re.match(r"matrix\(([^)]*)\)", element.get("transform", ""))
        if transform:
            a, b, c, d, e, f = (float(v) for v in transform.group(1).split(","))
            m = (m[0] * a + m[2] * b, m[1] * a + m[3] * b, m[0] * c + m[2] * d, m[1] * c + m[3] * d,
                 m[0] * e + m[2] * f + m[4], m[1] * e + m[3] * f + m[5])
        if element.tag == SVG + "text":
            x, y = float(element.get("x", "0").split()[0]), float(element.get("y", "0").split()[0])
            size = float(element.get("font-size", "9"))
            x, y = x + 0.3 * size, y - 0.35 * size
            found.append({"text": "".join(element.itertext()).strip(), "size": size,
                          "center": (m[0] * x + m[2] * y + m[4] + rect.left(), m[1] * x + m[3] * y + m[5] + rect.top()),
                          "fill": element.get("fill", "#000000"), "opacity": float(element.get("fill-opacity", "1"))})
        for child in element:
            walk(child, m)

    walk(ElementTree.fromstring(bytes(buffer.data())), (1, 0, 0, 1, 0, 0))
    return found


def numbers_on_connections(tab, splitter_id: str) -> dict[int, list[dict]]:
    """The numbers drawn on the canvas: for each, where it is drawn, with the connection leaving the Splitter it is
    nearest (`on`: the block that connection goes to), how far from it, and how far from its two ends."""
    settle()
    connections = {}
    for edge in tab.scene.edge_items:
        if edge.text.startswith(f"{splitter_id}.lines -> "):
            path = edge.path()
            points = [edge.mapToScene(path.pointAtPercent(i / 200)) for i in range(201)]
            connections[edge.text.split(" -> ")[1].split(".")[0]] = [(p.x(), p.y()) for p in points]
    assert connections, f"no connection leaves {splitter_id} on the canvas"
    found: dict[int, list[dict]] = {}
    for text in drawn_texts(tab.scene, tab.scene.itemsBoundingRect().adjusted(-60, -60, 60, 60)):
        number = NUMBER.fullmatch(text["text"])
        if number is None:
            continue
        away = {to: min(math.dist(text["center"], p) for p in points) for to, points in connections.items()}
        on = min(away, key=away.get)
        found.setdefault(int(number.group(1)), []).append({
            **text, "on": on, "away": away[on], "others": min([d for to, d in away.items() if to != on], default=1e9),
            "from_splitter": math.dist(text["center"], connections[on][0]),
            "from_line": math.dist(text["center"], connections[on][-1])})
    return found


def assert_numbered(tab, expected: dict[int, str]):
    found = numbers_on_connections(tab, "split")
    assert sorted(found) == sorted(expected) and all(len(v) == 1 for v in found.values()), \
        f"the numbers drawn on the connections leaving the Splitter: {found}"
    for number, line in expected.items():
        label = found[number][0]
        assert label["on"] == line and label["away"] <= 20 and label["away"] < label["others"], \
            f"{number} should be on the connection to {line}: {label}"
        assert label["from_splitter"] < label["from_line"], f"{number} should be near the Splitter: {label}"


def numbered_flow(folder: Path) -> Path:
    """A Splitter set to one after another, its lines listed bottom first in the file."""
    return flow_file(folder / "flow.yaml", {
        "manual": MANUAL, "split": splitter(ONE_AFTER_ANOTHER, x=0, y=0),
        "load_stock": step("load_stock", x=500, y=450), "load_sales": step("load_sales", x=500, y=-450),
        "load_costs": step("load_costs", x=500, y=0), "merge": merger(x=1000),
    }, ["manual.start -> split.after", *lines_into("split", "load_stock", "load_sales", "load_costs"),
        *into_merger("merge", "load_stock", "load_sales", "load_costs")])


def test_one_after_another_numbers_the_connections_leaving_the_splitter_top_to_bottom_and_they_follow_moves(
        win, tmp_path):
    """Spec 2 and 9: in one-after-another mode the canvas shows each connection leaving the Splitter numbered 1, 2, 3
    in line order (top to bottom), on the connection, near the Splitter; moved blocks renumber them."""
    tab = win.open_flow(numbered_flow(tmp_path))
    try:
        settle()

        assert_numbered(tab, {1: "load_sales", 2: "load_costs", 3: "load_stock"})

        tab.doc.move_blocks({"load_stock": (500, -900)})  # as a drag of the block ends: now the top line
        settle()

        assert_numbered(tab, {1: "load_stock", 2: "load_sales", 3: "load_costs"})
    finally:
        clean(win)


@pytest.mark.parametrize("theme", ["Light", "Dark"])
def test_the_numbers_on_the_connections_read_well_in_light_and_dark(win, tmp_path, monkeypatch, theme):
    """Spec 9: the numbers on the connections read well in the Light and the Dark theme (Tools > Settings > Theme): a
    contrast of at least 4.5:1 (WCAG's bar for text) with what is behind them, measured where the canvas paints them."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings may write a Startup script there)
    app = QApplication.instance()
    look = app.style().name(), app.styleSheet(), QApplication.palette(), QToolTip.palette()
    tab = win.open_flow(numbered_flow(tmp_path))
    try:
        choose(win, theme)
        settle()
        found = numbers_on_connections(tab, "split")
        assert sorted(found) == [1, 2, 3], found
        poor = {}
        for number, (label, *_) in found.items():
            s = max(label["size"], 6.0)
            cx, cy = label["center"]
            image = QImage(round(8 * s), round(8 * s), QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            tab.scene.render(painter, QRectF(0, 0, image.width(), image.height()), QRectF(cx - 2 * s, cy - 2 * s,
                                                                                          4 * s, 4 * s))
            painter.end()
            behind, ink, alpha = most_common(image), QColor(label["fill"]), label["opacity"]
            shown = QColor(*(round(alpha * f + (1 - alpha) * b) for f, b in
                             zip(ink.getRgb()[:3], behind.getRgb()[:3])))
            if contrast(shown, behind) < TEXT:
                poor[number] = (shown.name(), behind.name(), round(contrast(shown, behind), 2))
        assert poor == {}, f"numbers that do not stand out from what is behind them in {theme}: {poor}"
    finally:
        QGuiApplication.styleHints().unsetColorScheme()
        if app.style().name() != look[0]:
            QApplication.setStyle(look[0])
        if app.styleSheet() != look[1]:
            app.setStyleSheet(look[1])
        QApplication.setPalette(look[2])
        QToolTip.setPalette(look[3])
        settle()
        clean(win)


def test_the_canvas_shows_lines_running_at_once_and_stop_cancels_every_running_line(win, qtbot, tmp_path):
    """Spec 9 (several blocks show as running at once on the canvas while lines run at the same time) and 8 (Stop
    cancels every running line at once: each running block ends as cancelled, as for any run)."""
    wait = {"type": "logic.wait", "config": {"seconds": 60}}
    path = flow_file(tmp_path / "flow.yaml", {
        "manual": MANUAL, "load": LOAD, "split": splitter(),
        "wait_a": {**wait, "ui": {"x": 400, "y": -200}}, "wait_b": {**wait, "ui": {"x": 400, "y": 200}},
        "merge": merger(x=800),
    }, [*fed(), "split.lines -> wait_a.value", "split.lines -> wait_b.value",
        "wait_a.value -> merge.lines", "wait_b.value -> merge.lines"])
    tab = win.open_flow(path)
    nodes = tab.scene.nodes

    win.run_flow(tab)
    qtbot.waitUntil(lambda: nodes["wait_a"].status == "running" and nodes["wait_b"].status == "running",
                    timeout=30_000)
    stopped = dt.datetime.now()
    win.stop(tab)
    wait_done(qtbot, tab, timeout=20_000)

    assert tab.last["status"] == "cancelled"
    assert (nodes["wait_a"].status, nodes["wait_b"].status, nodes["merge"].status) == ("cancelled",) * 3
    history = History(win.home.history_db)
    try:
        results = history.blocks(tab.runs[-1].run_id)
    finally:
        history.close()
    ended = [dt.datetime.fromisoformat(results[b]["finished"]) for b in ("wait_a", "wait_b")]
    assert all((t - stopped).total_seconds() < 5 for t in ended), (stopped, ended)
    assert abs((ended[0] - ended[1]).total_seconds()) < 1.5, ended


def test_after_a_failed_line_right_click_run_from_here_reruns_only_that_line_and_what_follows(win, qtbot, tmp_path):
    """Spec 6, the user's case in the editor: a run where load_costs failed (the Merger fails, the lines that
    succeeded keep their results); after a fix, right-click load_costs > Run from this block on runs it, the rest of
    its line, the Merger and what follows, not the other lines."""
    tab = win.open_flow(costs_flow(tmp_path, splitter()))
    nodes = tab.scene.nodes
    win.run_flow(tab)
    wait_done(qtbot, tab)
    assert {b: nodes[b].status for b in ("load_sales", "load_costs", "costs_total", "load_stock", "merge", "report")} \
        == {"load_sales": "success", "load_costs": "failed", "costs_total": "skipped", "load_stock": "success",
            "merge": "failed", "report": "skipped"}
    (tmp_path / "fixed.txt").write_text("", encoding="utf-8")
    failed = tab.runs[-1]

    seen = right_click(qtbot, win, tab, "load_costs", "title", "Run from this block on, with the same inputs")
    assert any(t.startswith("Run from this block on, with the same inputs") for t in seen), seen
    qtbot.waitUntil(lambda: tab.runs[-1] is not failed and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)

    assert tab.runs[-1].status == "success"
    assert all(nodes[b].status == "success" for b in ("load_sales", "load_costs", "costs_total", "load_stock",
                                                      "merge", "report")), {b: n.status for b, n in nodes.items()}
    assert {name: ran(tmp_path, name) for name in ("load_sales", "load_costs", "costs_total", "load_stock")} == \
        {"load_sales": 1, "load_costs": 2, "costs_total": 1, "load_stock": 1}
    assert set(read_json(tmp_path / "report.json")) == {"load_sales", "costs_total", "load_stock"}


def test_f7_names_the_connection_that_could_never_finish(win, tmp_path):
    """Spec 7: the editor's Validate (F7) refuses, before any run, the connection from a later line into an earlier
    one in one-after-another mode, naming it."""
    tab = win.open_flow(never_flow(tmp_path, splitter(ONE_AFTER_ANOTHER)))
    settle()

    win.validate_action.trigger()

    said = list(win.message_boxes)
    win.message_boxes.clear()
    assert [(kind, title) for kind, (title, _) in said] == [("warning", "The flow has problems")], said
    assert NEVER in said[0][1][1], said
    assert not tab.runs


def plain(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split()).lower()


def entry(page: str, type_id: str) -> str:
    """A block's entry on a reference page of the Documentation (from its heading to the next block's), as text."""
    start = page.find(f'<h2 id="{type_id}"')
    assert start >= 0, f"no entry for {type_id}"
    end = page.find("<h2", start + 1)
    return plain(page[start:end if end >= 0 else None])


def test_the_documentation_and_the_hover_help_say_what_a_line_is_and_how_each_mode_behaves(win):
    """Spec 1, 5 and 9: both blocks are in Help > Documentation (Contents and the Logic page) and in the hover help;
    the Splitter's entry tells about lines, its two modes, its limit and the Merger; the Merger's that it waits for the
    lines."""
    from taskloom.editor import docs

    pages = docs.pages(win.registry)
    for type_id in ("logic.splitter", "logic.merger"):
        assert f'#{type_id}"' in pages["index.html"], type_id
    page = pages[docs.category_page("Logic")]
    split = entry(page, "logic.splitter")
    for words in ("line", SAME_TIME, ONE_AFTER_ANOTHER, "at most at once", "merger"):
        assert words in split, f"the Splitter's entry does not tell about {words!r}: {split}"
    merge = entry(page, "logic.merger")
    for words in ("line", "wait"):
        assert words in merge, f"the Merger's entry does not tell about {words!r}: {merge}"
    tree = win.palette_panel.tree
    for title in ("Splitter", "Merger"):
        items = tree.findItems(title, Qt.MatchExactly | Qt.MatchRecursive)
        assert items, f"no {title} in the Blocks list"
        assert "line" in plain(items[0].toolTip(0)), items[0].toolTip(0)


def test_readme_has_a_section_on_splitter_and_merger():
    """Spec 10: README has a short section on Splitter and Merger: the two modes, the limit, lines connecting across,
    what a failed line does, Run from here after one, the Validate check, and that "as each finishes" comes next."""
    lines = (REPO / "README.md").read_text(encoding="utf-8").splitlines()
    heads = [(i, len(m.group(1))) for i, line in enumerate(lines) if (m := re.match(r"(#+) ", line))]
    found = [(i, level) for i, level in heads if "Splitter" in lines[i]]
    assert found, "README has no section whose heading names the Splitter"
    start, level = found[0]
    end = next((i for i, lv in heads if i > start and lv <= level), len(lines))
    section = plain("\n".join(lines[start:end]))
    for words in ("merger", SAME_TIME, ONE_AFTER_ANOTHER, "at most at once", "across", "fail", "run from",
                  "validate", "as each finishes", "next"):
        assert words in section, f"README's Splitter and Merger section does not tell about {words!r}"
