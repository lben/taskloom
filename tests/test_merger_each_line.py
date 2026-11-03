"""The Merger's second mode, "each line as it finishes": instead of waiting for every line, the Merger passes on each
line's output as soon as that line has finished, and the blocks after it run once for that line (as a Loop runs its
blocks once per item), up to the end of the flow or up to a Merger set to all lines together further on, which waits
for every one of those runs. Up to At the same time runs at once (default 1, a sliding window), in the order the lines
finished. Through the real `taskloom` command (run, validate), its run history (history.db) and the offscreen editor.
Each test names the point of the specification (1-7) it protects.

What the tests take as given (the specification leaves it to the change): the Merger's two settings are found by the
labels the specification gives them ("Passes on": all lines together | each line as it finishes; "At the same time"),
its output as its one output that carries data; a line is named as the Merger names it today (the block connected into
its lines input: each line here is one block); the Log tag of a run is "[line <name>]", as the specification writes
it; the Merger's counter is the text "done/N · k running" the canvas draws on or around the Merger; the blocks after
it show how their runs ended as the blocks inside a Loop do (under the block, and hovering there). When runs happened
is read from what the blocks write (the same clock, in the one run process)."""

import datetime as dt
import os
import re
from collections import Counter
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QComboBox  # noqa: E402

from taskloom.block import DONE, setting_label  # noqa: E402
from taskloom.flow import ERROR_PORT, BlockSpec, block_config  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_editor import wait_done  # noqa: E402
from tests.test_loop_container import (context_menu, go, log_lines, no_runs, ran, read_json, result_of,  # noqa: E402
                                       run, run_in_editor, text_of, title, validate)
from tests.test_parallel_lines import (block_class, drawn_texts, entry, normal, plain, row_shown, saver,  # noqa: E402
                                       setting)
from tests.test_parallel_lines import spans as block_spans  # noqa: E402
from tests.test_paths_editor_editing import clean, field, flow_file, saved, select_only, settle  # noqa: E402
from tests.test_paths_editor_editing import taskloom as taskloom_cli  # noqa: E402
from tests.test_paths_editor_running import right_click  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MERGER = "logic.merger"
ALL, EACH = "all lines together", "each line as it finishes"
TICK = 0.016  # a clock tick on Windows: two times taken in a row may differ by this much
LINE_OF = {"sales": "load_sales", "costs": "load_costs", "stock": "load_stock"}  # what each line gives: its name


# --- the Merger, found by what the specification calls its settings -------------------------------------------------

def passes_on(shown: str) -> tuple[str, str]:
    """The key, in the flow file, of the Merger's setting Passes on, and its value Properties shows as `shown`."""
    cls = block_class(MERGER)
    key = setting(cls, "Passes on")
    options = getattr(cls.config[key], "options", None)
    assert options, f"Passes on is not a choice: {cls.config[key]!r}"
    found = [o for o in options if normal(o) == shown]
    assert found, f"{shown!r} is not one of the choices of Passes on: {options}"
    return key, found[0]


def merger(mode: str | None = None, same_time=None, x=900, y=0) -> dict:
    """A Merger; a setting left out (None) as it is when the block is added."""
    config = {}
    if mode is not None:
        key, value = passes_on(mode)
        config[key] = value
    if same_time is not None:
        config[setting(block_class(MERGER), "At the same time")] = same_time
    return {"type": MERGER, "config": config, "ui": {"x": x, "y": y}}


def each_line(same_time=None, x=900, y=0) -> dict:
    return merger(EACH, same_time, x=x, y=y)


def after(merger_id: str, spec: dict, dst: str, port: str = "input") -> str:
    """The connection from the Merger `spec` (its one output that carries data) into `dst`."""
    cls = block_class(MERGER)
    config = block_config(cls, BlockSpec(id=merger_id, type=MERGER, config=dict(spec.get("config") or {})))
    outputs = [p for p in cls.ports(config)[1] if p not in (DONE, ERROR_PORT)]
    assert len(outputs) == 1, f"the Merger should have one output, passing on what the lines give: {outputs}"
    return f"{merger_id}.{outputs[0]} -> {dst}.{port}"


# --- the blocks around it ---------------------------------------------------------------------------------------------

def python(code: str, inputs=("input",), outputs=("output",), x=0, y=0) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code},
            "ui": {"x": x, "y": y}}


def line(name: str, value: str, seconds: float = 0.0, y=0, x=300) -> dict:
    """A line of one block, `name`: it notes each time it runs (ran_<name>.txt), waits `seconds`, writes "<name> done"
    in the Log, then passes on `value`."""
    return python(f"import time\nopen('ran_{name}.txt', 'a', encoding='utf-8').write('x')\ntime.sleep({seconds})\n"
                  f"print('{name} done')\noutput = {value!r}", x=x, y=y)


def three(seconds=(0.0, 0.0, 0.0)) -> dict:
    """The lines load_sales (top), load_costs and load_stock (bottom), giving sales, costs and stock."""
    return {name: line(name, value, s, y=-300 + 300 * i)
            for i, ((value, name), s) in enumerate(zip(LINE_OF.items(), seconds))}


def keeper(name: str, x=1500, y=0) -> dict:
    """A block that, each time it runs, notes it (ran_<name>_<what it got>.txt) and writes what it got (as JSON) to
    <name>_<what it got>.json."""
    return python(f"import json, re\nkey = re.sub(r'[^\\w-]', '_', str(input))\n"
                  f"open(f'ran_{name}_{{key}}.txt', 'a', encoding='utf-8').write('x')\n"
                  f"with open(f'{name}_{{key}}.json', 'w', encoding='utf-8') as f:\n    json.dump(input, f)",
                  outputs=(), x=x, y=y)


def got(folder: Path, name: str) -> dict:
    """What the keeper `name` got, in each of its runs: {what it got, as in its file names: what it got}."""
    return {p.stem[len(name) + 1:]: read_json(p) for p in folder.glob(f"{name}_*.json")}


def runs_of(folder: Path, name: str) -> dict:
    """How many times the keeper `name` ran with each value: {what it got, as in its file names: times}."""
    return {p.stem[len(f"ran_{name}") + 1:]: len(p.read_text(encoding="utf-8"))
            for p in folder.glob(f"ran_{name}_*.txt")}


MANUAL = {"type": "trigger.manual", "ui": {"x": -700, "y": 0}}
LOAD = python("output = 'today'", inputs=(), x=-450)
SPLIT = {"type": "logic.splitter", "ui": {"x": -150, "y": 0}}


def front(lines: dict) -> tuple[dict, list]:
    """manual -> load -> split (its lines at the same time) -> each of `lines` -> merge's lines (merge itself is not
    in it)."""
    blocks = {"manual": MANUAL, "load": LOAD, "split": SPLIT, **lines}
    edges = ["manual.start -> load.after", "load.output -> split.value",
             *(f"split.lines -> {b}.input" for b in lines), *(f"{b}.output -> merge.lines" for b in lines)]
    return blocks, edges


def run_spans(folder: Path, names) -> dict[str, tuple[float, float]]:
    """{what a run got: (when it started, when it ended)}, as each run wrote them (span_<what it got>.json)."""
    return {n: tuple(read_json(folder / f"span_{n}.json")) for n in names}


# A block after the Merger that writes when its run started and ended (span_<what it got>.json), sleeping
# {seconds}[what it got] between.
TIMED = ("import json, time\nstart = time.perf_counter()\ntime.sleep({seconds}[input])\n"
         "open(f'span_{{input}}.json', 'w', encoding='utf-8').write(json.dumps([start, time.perf_counter()]))\n"
         "output = input")


def logs(env, run_id: int) -> list[tuple[str, str]]:
    """The run's Log lines in the run history, in order: (block, message)."""
    return [(b or "", m) for b, m in env.query("SELECT block, message FROM logs WHERE run_id=? ORDER BY rowid", run_id)]


# --- 2: each line as it finishes: the blocks after the Merger run once for that line ----------------------------------

def test_the_blocks_after_the_merger_run_for_a_line_as_soon_as_it_finishes_while_other_lines_still_run(env):
    """Spec 2, the user's words ("let the output of any line go out as soon as the line finishes"): load_costs waits
    (20 s at most) until report has run for load_sales' line; with the Merger set to each line as it finishes it has,
    while load_costs still ran. The blocks after the Merger (check, then report) run once per line, each run getting
    that line's output itself, not a dict of the lines."""
    waits = ("import os, time\nfor _ in range(400):\n    if os.path.exists('report_sales_.json'):\n        break\n"
             "    time.sleep(0.05)\n"
             "output = 'costs-after-sales' if os.path.exists('report_sales_.json') else 'costs-before-sales'")
    blocks, edges = front({"load_sales": line("load_sales", "sales", y=-300),
                           "load_costs": python(waits, x=300, y=0),
                           "load_stock": line("load_stock", "stock", 0.3, y=300)})
    merge = each_line()
    blocks |= {"merge": merge, "check": python("output = input + '!'", x=1200), "report": keeper("report")}
    flow = flow_file(env.root / "flow.yaml", blocks, [*edges, after("merge", merge, "check"),
                                                       "check.output -> report.input"])

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert got(env.root, "report") == {"sales_": "sales!", "costs-after-sales_": "costs-after-sales!",
                                       "stock_": "stock!"}
    assert runs_of(env.root, "report") == {"sales_": 1, "costs-after-sales_": 1, "stock_": 1}
    assert result_of(env, run_id, "merge")["status"] == "success"
    assert env.last_run() == (run_id, "success")


def test_at_the_same_time_is_1_when_left_out_the_runs_go_one_after_another_in_the_order_the_lines_finished(env):
    """Spec 1 (At the same time, default 1) and 2: left out, the runs after the Merger go one at a time, each starting
    once the one before it has ended, in the order the lines finished (the bottom line first here), not line order."""
    blocks, edges = front({"load_a": line("load_a", "a", 0.7, y=-300), "load_b": line("load_b", "b", 0.45, y=0),
                           "load_c": line("load_c", "c", 0.2, y=300)})
    merge = each_line()
    blocks |= {"merge": merge, "work": python(TIMED.format(seconds={"a": 0.4, "b": 0.4, "c": 0.4}), x=1200)}
    flow = flow_file(env.root / "flow.yaml", blocks, [*edges, after("merge", merge, "work")])

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    t = run_spans(env.root, "abc")
    started = sorted(t, key=lambda n: t[n][0])
    assert started == ["c", "b", "a"], t  # the order the lines finished
    assert all(t[later][0] >= t[earlier][1] - TICK for earlier, later in zip(started, started[1:])), t


def test_at_the_same_time_runs_that_many_and_the_next_starts_as_soon_as_one_ends_in_the_order_the_lines_finished(env):
    """Spec 2 (up to At the same time runs at once, the next starting as soon as one ends, in the order the lines
    finished; the Loop's sliding window): four lines finish bottom first; with At the same time 2, the runs for d and
    c start as their lines finish; b's waits until c's ends (not rounds of 2), while d's still runs; a's starts when
    b's ends; never more than 2 at once."""
    blocks, edges = front({"load_a": line("load_a", "a", 0.95, y=-450), "load_b": line("load_b", "b", 0.7, y=-150),
                           "load_c": line("load_c", "c", 0.45, y=150), "load_d": line("load_d", "d", 0.2, y=450)})
    merge = each_line(2)
    blocks |= {"merge": merge,
               "work": python(TIMED.format(seconds={"a": 0.3, "b": 0.3, "c": 0.6, "d": 2.0}), x=1200)}
    flow = flow_file(env.root / "flow.yaml", blocks, [*edges, after("merge", merge, "work")])

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    t = run_spans(env.root, "abcd")
    assert sorted(t, key=lambda n: t[n][0]) == ["d", "c", "b", "a"], t  # the order the lines finished
    at_once = max(sum(1 for s, e in t.values() if s <= start < e) for start, _ in t.values())
    assert at_once == 2, t
    assert t["b"][0] >= t["c"][1] - TICK and t["b"][0] - t["c"][1] < 0.5, t  # b's started as soon as c's ended ...
    assert t["d"][1] - t["b"][0] >= 0.5, t  # ... while d's still ran
    assert t["a"][0] >= t["b"][1] - TICK and t["a"][0] < t["d"][1], t


def test_a_merger_set_to_all_lines_together_further_on_waits_for_every_run_and_passes_on_each_by_line_name(env):
    """Spec 2: the runs go up to a Merger set to all lines together further on (here left as it is added: its
    default), which waits for every one of those runs and passes on {line name: what that run gave it}; the block
    after it runs once."""
    blocks, edges = front(three((0.0, 0.3, 0.6)))
    merge, gather = each_line(), merger(x=1500)
    blocks |= {"merge": merge,
               "check": python("open(f'ran_check_{input}.txt', 'a', encoding='utf-8').write('x')\n"
                               "output = input + '!'", x=1200),
               "gather": gather, "summary": saver("summary.json", x=1800)}
    flow = flow_file(env.root / "flow.yaml", blocks, [
        *edges, after("merge", merge, "check"), "check.output -> gather.lines", after("gather", gather, "summary")])

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "summary.json") == {"load_sales": "sales!", "load_costs": "costs!",
                                                    "load_stock": "stock!"}
    assert ran(env.root, "summary") == 1
    assert [ran(env.root, f"check_{v}") for v in LINE_OF] == [1, 1, 1]
    assert result_of(env, run_id, "gather")["status"] == "success"


def test_each_run_after_the_merger_gets_its_own_copy_of_what_it_receives(env):
    """Spec 2 (each run gets its own copy of what it receives): settings, before the Splitter, connects into report,
    after the Merger; each run adds its line's output to the list it got: no run sees another's change, nor does the
    block that keeps settings' value."""
    blocks, edges = front(three())
    merge, gather = each_line(), merger(x=1500)
    blocks |= {"settings": python("output = {'seen': []}", inputs=(), x=-450, y=500),
               "merge": merge,
               "report": python("base['seen'].append(value)\noutput = base['seen']", ("value", "base"), x=1200),
               "gather": gather, "summary": saver("summary.json", x=1800), "keep": saver("settings.json", y=500)}
    flow = flow_file(env.root / "flow.yaml", blocks, [
        *edges, "manual.start -> settings.after", after("merge", merge, "report", "value"),
        "settings.output -> report.base", "report.output -> gather.lines", after("gather", gather, "summary"),
        "settings.output -> keep.input", after("gather", gather, "keep", "after")])

    result, _ = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "summary.json") == {"load_sales": ["sales"], "load_costs": ["costs"],
                                                    "load_stock": ["stock"]}
    assert read_json(env.root / "settings.json") == {"seen": []}


def test_a_merger_set_to_all_lines_together_joins_as_before(env):
    """Spec 1 (all lines together: as today) and 7 (a flow whose Mergers join runs as in 0.33.0): set to all lines
    together, the Merger waits for every line, then passes on {line name: its output} once; the block after it runs
    once."""
    blocks, edges = front(three((0.6, 0.6, 0.6)))
    merge = merger(ALL)
    blocks |= {"merge": merge, "report": saver("report.json", x=1200)}
    flow = flow_file(env.root / "flow.yaml", blocks, [*edges, after("merge", merge, "report")])

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(env.root / "report.json") == {"load_sales": "sales", "load_costs": "costs", "load_stock": "stock"}
    assert ran(env.root, "report") == 1
    t = block_spans(env, run_id)
    assert t["merge"][0] >= max(t[b][1] for b in LINE_OF.values()) - dt.timedelta(seconds=TICK), t  # it waited


# --- 3: the Log and the run history ---------------------------------------------------------------------------------

def test_every_log_line_of_a_block_run_for_a_line_carries_its_block_and_line_and_none_is_lost(env):
    """Spec 3: three runs after the Merger at the same time (At the same time 3), each printing 100 lines at once:
    every Log line of the block run for a line carries its block and that line ("[report] [line load_costs] ..."), as
    `taskloom run` prints the Log; the run history keeps every run's lines, none lost or repeated."""
    n = 100
    blocks, edges = front(three())
    merge = each_line(3)
    blocks |= {"merge": merge,
               "report": python(f"import time\ntime.sleep(0.3)\nfor i in range({n}):\n"
                                f"    print(f'for {{input}} line {{i}}')", outputs=(), x=1200)}
    flow = flow_file(env.root / "flow.yaml", blocks, [*edges, after("merge", merge, "report")])
    wanted = {(v, i) for v in LINE_OF for i in range(n)}

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    kept = Counter((m.group(1), int(m.group(2))) for _, message in logs(env, run_id)
                   if (m := re.search(r"for (sales|costs|stock) line (\d+)\b", message)))
    assert set(kept) == wanted and set(kept.values()) == {1}, "the run history lost or repeated Log lines"

    printed = env.taskloom("run", flow)  # as `taskloom run` prints the Log

    assert printed.returncode == 0, printed.stderr[-3000:]
    found, mixed = set(), []
    for text in printed.stderr.splitlines():
        m = re.search(r"for (sales|costs|stock) line (\d+)\b", text)
        if m:
            found.add((m.group(1), int(m.group(2))))
            if "[report]" not in text or f"[line {LINE_OF[m.group(1)]}]" not in text:
                mixed.append(text)
    assert mixed == [], f"Log lines without their block's tag and their own line: {mixed[:5]}"
    assert found == wanted


def test_the_merger_writes_a_log_line_as_each_line_arrives(env):
    """Spec 3 (the Merger writes a Log line as each line arrives): for each line, the Merger's Log names it after that
    line has ended; for load_sales and load_costs, before the slow load_stock has ended."""
    blocks, edges = front(three((0.0, 0.4, 1.5)))
    merge = each_line()
    blocks |= {"merge": merge, "report": python("output = input", x=1200)}
    flow = flow_file(env.root / "flow.yaml", blocks, [*edges, after("merge", merge, "report")])

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    said = logs(env, run_id)
    ended = {name: next(i for i, (b, m) in enumerate(said) if b == name and m.endswith(f"{name} done"))
             for name in LINE_OF.values()}
    merger_said = [(i, m) for i, (b, m) in enumerate(said) if b == "merge"]
    for name in ("load_sales", "load_costs"):
        assert [m for i, m in merger_said if ended[name] < i < ended["load_stock"] and re.search(rf"\b{name}\b", m)], \
            (name, merger_said)
    assert [m for i, m in merger_said if i > ended["load_stock"] and re.search(r"\bload_stock\b", m)], merger_said


# --- 4: failures -----------------------------------------------------------------------------------------------------

def test_a_failed_line_has_no_run_a_failed_run_stops_no_other_and_the_merger_fails_listing_both(env):
    """Spec 4 (the specification's example): load_costs fails (no run after the Merger for it); report fails for
    load_stock's line, which ends first: that stops neither load_sales' line nor its run. Once every line has been
    dealt with, the Merger fails, its error listing both ("load_costs failed: ...; report failed for line load_stock:
    ..."), through its error output; the run fails."""
    blocks, edges = front({
        "load_sales": line("load_sales", "sales", 1.2, y=-300),
        "load_costs": python("import time\ntime.sleep(0.3)\nraise ValueError('no costs file today')", x=300),
        "load_stock": line("load_stock", "stock", 0.1, y=300)})
    merge = each_line()
    report = ("import json, time\nopen(f'ran_report_{input}.txt', 'a', encoding='utf-8').write('x')\n"
              "if input == 'stock':\n    raise ValueError('no stock report')\n"
              "open(f'report_{input}.json', 'w', encoding='utf-8').write(json.dumps([input, time.perf_counter()]))")
    handler = ("import json, time\n"
               "open('failure.json', 'w', encoding='utf-8').write(json.dumps([input, time.perf_counter()]))")
    blocks |= {"merge": merge, "report": python(report, outputs=(), x=1200),
               "on_failure": python(handler, outputs=(), x=1200, y=500)}
    flow = flow_file(env.root / "flow.yaml", blocks, [*edges, after("merge", merge, "report"),
                                                       "merge.error -> on_failure.input"])

    result, run_id = run(env, flow)

    assert result.returncode == 1
    assert env.last_run() == (run_id, "failed")
    assert {v: ran(env.root, f"report_{v}") for v in LINE_OF} == {"sales": 1, "costs": 0, "stock": 1}
    statuses = env.block_status(run_id)
    assert (statuses["load_sales"], statuses["load_costs"], statuses["load_stock"]) == ("success", "failed", "success")
    sales_report = read_json(env.root / "report_sales.json")
    error = result_of(env, run_id, "merge")
    assert error["status"] == "failed", error
    for words in ("load_costs failed", "no costs file today", "report failed for line load_stock", "no stock report"):
        assert words in error["error"], (words, error)
    failure, failed_at = read_json(env.root / "failure.json")
    assert failure["block"] == "merge" and "load_costs failed" in failure["error"] and \
        "report failed for line load_stock" in failure["error"], failure
    assert failed_at > sales_report[1]  # once every line had been dealt with


def test_a_line_that_delivers_nothing_without_failing_has_no_run_and_the_mergers_log_says_so(env):
    """Spec 4: a line delivered nothing without failing (an If sent it nowhere): it has no run after the Merger, the
    Merger's Log says so, naming it; the other line's run goes on, and nothing failed."""
    blocks = {"manual": MANUAL, "load": LOAD, "split": SPLIT, "plain": python("output = input + '-p'", x=300, y=-200),
              "route": {"type": "logic.if", "config": {"condition": "value == 'never'"}, "ui": {"x": 300, "y": 200}}}
    merge = each_line()
    blocks |= {"merge": merge, "report": keeper("report", x=1200)}
    flow = flow_file(env.root / "flow.yaml", blocks, [
        "manual.start -> load.after", "load.output -> split.value", "split.lines -> plain.input",
        "split.lines -> route.value", "plain.output -> merge.lines", "route.true -> merge.lines",
        after("merge", merge, "report")])

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert got(env.root, "report") == {"today-p": "today-p"}
    assert result_of(env, run_id, "merge")["status"] == "success"
    assert [m for b, m in logs(env, run_id) if b == "merge" and re.search(r"\broute\b", m)], logs(env, run_id)


# --- 5: running again from the Merger ---------------------------------------------------------------------------------

def rerun_flow(folder: Path) -> Path:
    """The three lines (each noting its runs) -> merge (each line) -> report, which fails for load_costs' line until
    fixed.txt exists -> gather (all lines together) -> summary (summary.json)."""
    blocks, edges = front(three())
    merge, gather = each_line(), merger(x=1500)
    report = ("import os\nopen(f'ran_report_{input}.txt', 'a', encoding='utf-8').write('x')\n"
              "if input == 'costs' and not os.path.exists('fixed.txt'):\n"
              "    raise ValueError('the costs report is late')\n"
              "output = input.upper()")
    blocks |= {"merge": merge, "report": python(report, x=1200), "gather": gather,
               "summary": saver("summary.json", x=1800)}
    return flow_file(folder / "flow.yaml", blocks, [*edges, after("merge", merge, "report"),
                                                     "report.output -> gather.lines",
                                                     after("gather", gather, "summary")])


WHOLE = {"load_sales": "SALES", "load_costs": "COSTS", "load_stock": "STOCK"}


def test_run_from_the_merger_runs_again_only_the_runs_that_failed_keeping_the_others_and_not_the_lines(env):
    """Spec 5: after a run where report failed for load_costs' line, and a fix, `taskloom run --from merge
    --inputs-from N` runs report again only for that line; the lines are not run again (their kept outputs are used),
    the other runs keep their results: the Merger further on gets all three, and the block after it runs."""
    flow = rerun_flow(env.root)
    first, failed_run = run(env, flow)
    assert first.returncode == 1
    assert {v: ran(env.root, f"report_{v}") for v in LINE_OF} == {"sales": 1, "costs": 1, "stock": 1}
    (env.root / "fixed.txt").write_text("", encoding="utf-8")
    summaries = ran(env.root, "summary")

    second, from_run = run(env, flow, "--from", "merge", "--inputs-from", failed_run)

    assert second.returncode == 0, second.stderr[-3000:]
    assert {name: ran(env.root, name) for name in LINE_OF.values()} == {name: 1 for name in LINE_OF.values()}
    assert {v: ran(env.root, f"report_{v}") for v in LINE_OF} == {"sales": 1, "costs": 2, "stock": 1}
    assert read_json(env.root / "summary.json") == WHOLE
    assert ran(env.root, "summary") == summaries + 1
    assert result_of(env, from_run, "merge")["status"] == "success"


# --- 6: Validate ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("type_id, config, key", [
    ("data.write_file", {"path": "out/report.csv"}, "path"),
    ("files.copy", {"source": "in/sales.csv", "destination": "archive/"}, "destination"),
], ids=["write_file_path", "copy_files_destination"])
def test_validate_warns_about_a_file_path_a_block_run_once_per_line_writes_to_without_a_placeholder(
        env, type_id, config, key):
    """Spec 6 (as for a Loop): a block that runs once per line writing or copying to a file path with no {placeholder}
    (every line's run would write the same file) is warned about, naming the block and the setting; with a placeholder
    in it, it is not; a block after the Merger that closes the runs (it runs once) is not either. Nor is the Merger set
    to all lines together that closes the runs with its one connection: it joins every line's run."""
    label = setting_label(key, discover(None).get(type_id).config[key])
    feed = {"data.write_file": "table", "files.copy": "after"}[type_id]

    def flow_with(value):
        blocks, edges = front({"load_sales": line("load_sales", "sales", y=-200),
                               "load_costs": line("load_costs", "costs", y=200)})
        merge, gather = each_line(x=600), merger(x=1200, y=400)
        blocks |= {"merge": merge, "rows": python("output = pl.DataFrame({'region': [input]})", x=900),
                   "save": {"type": type_id, "config": {**config, key: value}, "ui": {"x": 1200, "y": 0}},
                   "gather": gather,
                   "lines_table": python("output = pl.DataFrame({'line': list(input)})", x=1500, y=400),
                   "final": {"type": type_id, "config": dict(config), "ui": {"x": 1800, "y": 400}}}
        return flow_file(env.root / "flow.yaml", blocks, [
            *edges, after("merge", merge, "rows"), f"rows.output -> save.{feed}", "rows.output -> gather.lines",
            after("gather", gather, "lines_table"), f"lines_table.output -> final.{feed}"],
            params={"day": {"type": "text", "value": "monday"}})

    code, errors, warnings = validate(env, flow_with(config[key]))

    assert code == 0, errors
    assert [w for w in warnings if re.search(r"\bsave\b", w) and label in w], warnings
    assert not [w for w in warnings if re.search(r"\bfinal\b", w) and label in w], warnings
    assert not [w for w in warnings if re.search(r"\bgather\b", w)], warnings

    code, errors, warnings = validate(env, flow_with(config[key].replace("/", "/{day}_", 1)))

    assert code == 0, errors
    assert not [w for w in warnings if re.search(r"\bsave\b", w) and label in w], warnings
    assert not [w for w in warnings if re.search(r"\bgather\b", w)], warnings


def inside(loop_id: str, spec: dict) -> dict:
    return {**spec, "inside": loop_id}


def in_a_loop(mode):
    """A Loop (per_item) over x and y; inside its box: split -> a, b -> merge (`mode`) -> work -> gather (all lines
    together) -> per_item's result. Run as it says, the Loop's results are each item's {line: work's result}."""
    merge, gather = merger(mode, x=450), merger(x=1050)
    blocks = {"manual": MANUAL, "make": python("output = ['x', 'y']", inputs=(), x=-450),
              "per_item": {"type": "logic.loop", "config": {}, "ui": {"x": -200, "y": -300}},
              "split": inside("per_item", {"type": "logic.splitter", "ui": {"x": -100, "y": 0}}),
              "a": inside("per_item", python("output = input + '-a'", x=150, y=-150)),
              "b": inside("per_item", python("output = input + '-b'", x=150, y=150)),
              "merge": inside("per_item", merge), "work": inside("per_item", python("output = input + '!'", x=750)),
              "gather": inside("per_item", gather), "report": saver("report.json", x=1800)}
    edges = ["manual.start -> make.after", "make.output -> per_item.items", "per_item.item -> split.value",
             "split.lines -> a.input", "split.lines -> b.input", "a.output -> merge.lines", "b.output -> merge.lines",
             after("merge", merge, "work"), "work.output -> gather.lines",
             after("gather", gather, "per_item", "result"),
             "per_item.results -> report.input"]

    def worked(folder: Path) -> bool:
        return read_json(folder / "report.json") == [{"a": "x-a!", "b": "x-b!"}, {"a": "y-a!", "b": "y-b!"}]

    return blocks, edges, "merge", worked


def in_another_each_line_section(mode):
    """split -> p, q -> outer_merge (`mode`) -> split2 -> r, s -> inner_merge (`mode`) -> tail. Run as it says, tail
    runs once per line of inner_merge for each line of outer_merge: four times."""
    outer, inner = merger(mode, x=500), merger(mode, x=1400)
    blocks = {"manual": MANUAL, "load": LOAD, "split": SPLIT,
              "p": python("output = 'p'", x=200, y=-150), "q": python("output = 'q'", x=200, y=150),
              "outer_merge": outer, "split2": {"type": "logic.splitter", "ui": {"x": 800, "y": 0}},
              "r": python("output = input + '-r'", x=1100, y=-150), "s": python("output = input + '-s'", x=1100, y=150),
              "inner_merge": inner, "tail": keeper("tail", x=1700)}
    edges = ["manual.start -> load.after", "load.output -> split.value", "split.lines -> p.input",
             "split.lines -> q.input", "p.output -> outer_merge.lines", "q.output -> outer_merge.lines",
             after("outer_merge", outer, "split2", "value"), "split2.lines -> r.input", "split2.lines -> s.input",
             "r.output -> inner_merge.lines", "s.output -> inner_merge.lines", after("inner_merge", inner, "tail")]

    def worked(folder: Path) -> bool:
        return got(folder, "tail") == {"p-r": "p-r", "p-s": "p-s", "q-r": "q-r", "q-s": "q-s"}

    return blocks, edges, "inner_merge", worked


@pytest.mark.parametrize("build", [in_a_loop, in_another_each_line_section], ids=["in_a_loop", "in_an_each_line_run"])
def test_an_each_line_merger_in_a_loop_or_another_each_line_section_is_refused_naming_it_or_works(env, build):
    """Spec 6 with the no-surprises rule: a Merger set to each line as it finishes inside a Loop's box, or inside the
    runs of another one, is either refused by Validate before any run, naming it (and `taskloom run` does not start),
    or it works: the run does what the flow says. (The same flow with Mergers that join is fine.)"""
    blocks, edges, _, _ = build(None)
    code, errors, _ = validate(env, flow_file(env.root / "joined.yaml", blocks, edges))
    assert code == 0, errors
    blocks, edges, merger_id, worked = build(EACH)
    flow = flow_file(env.root / "flow.yaml", blocks, edges)

    code, errors, _ = validate(env, flow)

    if code == 2:
        assert [e for e in errors if re.search(rf"\b{merger_id}\b", e)], errors
        refused = env.taskloom("run", flow)
        assert refused.returncode == 2 and merger_id in refused.stderr, refused.stderr
        assert no_runs(env)
    else:
        assert code == 0, errors
        result, _ = run(env, flow)
        assert result.returncode == 0, result.stderr[-3000:]
        assert worked(env.root)


@pytest.mark.parametrize("same_time", [0, "two"])
def test_the_mergers_at_the_same_time_takes_a_whole_number_of_one_or_more(env, same_time):
    """Spec 1: the Merger's At the same time is a whole number, 1 or more: anything else is refused, naming the
    Merger and the setting."""
    blocks, edges = front(three())
    merge = each_line()
    merge["config"][setting(block_class(MERGER), "At the same time")] = same_time
    blocks |= {"merge": merge, "report": keeper("report", x=1200)}
    flow = flow_file(env.root / "flow.yaml", blocks, [*edges, after("merge", each_line(), "report")])

    code, errors, _ = validate(env, flow)

    assert code == 2, errors
    assert [e for e in errors if re.search(r"\bmerge\b", e) and "At the same time" in e], errors


# --- 1: the documentation ---------------------------------------------------------------------------------------------

def test_the_documentation_and_readme_say_what_each_mode_of_the_merger_does():
    """Spec 1 (its help and the documentation say what each mode does): the Merger's entry in Help > Documentation
    (its description and settings' help) tells of all lines together (it waits) and each line as it finishes (the
    blocks after it run once per line) and At the same time; README's part on the Merger tells of both modes and At
    the same time."""
    from taskloom.editor import docs

    said = entry(docs.pages(discover(None))[docs.category_page("Logic")], MERGER)
    for words in (ALL, EACH, "at the same time", "wait", "once"):
        assert words in said, f"the Merger's entry does not tell about {words!r}: {said}"
    lines = (REPO / "README.md").read_text(encoding="utf-8").splitlines()
    fenced = [text.startswith("```") for text in lines]
    in_code = [sum(fenced[:i]) % 2 == 1 for i in range(len(lines))]  # (a # line in an example is no heading)
    heads = [(i, len(m.group(1))) for i, text in enumerate(lines) if not in_code[i] and (m := re.match(r"(#+) ", text))]
    parts = []
    for start, level in [(i, lv) for i, lv in heads if "Merger" in lines[i]]:
        end = next((i for i, lv in heads if i > start and lv <= level), len(lines))
        parts.append("\n".join(lines[start:end]))
    assert parts, "README has no section whose heading names the Merger"
    section = plain("\n".join(parts))
    for words in (ALL, EACH, "at the same time"):
        assert words in section, f"README's part on the Merger does not tell about {words!r}"


# --- the editor -------------------------------------------------------------------------------------------------------

def two_lines_flow(folder: Path, merge: dict) -> Path:
    """load_sales and load_costs -> merge -> report (a keeper: report_<what it got>.json for each run)."""
    blocks, edges = front({"load_sales": line("load_sales", "sales", y=-200),
                           "load_costs": line("load_costs", "costs", 0.3, y=200)})
    blocks |= {"merge": merge, "report": keeper("report", x=1300)}
    return flow_file(folder / "flow.yaml", blocks, [*edges, after("merge", each_line(), "report")])


def test_passes_on_is_all_lines_together_by_default_and_at_the_same_time_shows_only_for_each_line(win, tmp_path):
    """Spec 1, in Properties: a Merger from a flow file without the setting shows Passes on as all lines together, of
    the two choices, and no At the same time; chosen each line as it finishes, At the same time shows 1; saved, the
    flow file says so and the flow runs the block after the Merger once per line (the real command)."""
    path = two_lines_flow(tmp_path, {"type": MERGER, "ui": {"x": 900, "y": 0}})
    tab = win.open_flow(path)
    try:
        settle()
        select_only(tab, "merge")

        mode = field(win, "Passes on")
        assert isinstance(mode, QComboBox), mode
        offered = [normal(mode.itemText(i)) for i in range(mode.count())]
        assert sorted(offered) == sorted([ALL, EACH]) and normal(mode.currentText()) == ALL, offered
        assert not row_shown(win, "At the same time")

        mode.setCurrentIndex(offered.index(EACH))
        settle()

        assert row_shown(win, "At the same time")
        same_time = field(win, "At the same time")
        assert (same_time.text() or getattr(same_time, "placeholderText", str)()) == "1"
        assert win.save(tab)
        key, value = passes_on(EACH)
        assert (saved(path)["blocks"]["merge"].get("config") or {}).get(key) == value, saved(path)["blocks"]["merge"]
        done = taskloom_cli(win, "run", path, cwd=tmp_path)
        assert done.returncode == 0, done.stderr[-3000:]
        assert got(tmp_path, "report") == {"sales": "sales", "costs": "costs"}
    finally:
        clean(win)


def merger_progress(tab, n: int, merger_id: str = "merge") -> tuple[int, int] | None:
    """(done, running) as the canvas shows it on or around the Merger, "done/N · k running"; None while it shows
    none."""
    settle()
    around = tab.scene.nodes[merger_id].sceneBoundingRect().adjusted(-30, -40, 30, 40)
    shown = " ".join(t["text"] for t in drawn_texts(tab.scene, around))
    m = re.search(rf"(\d+)\s*/\s*{n}\s*·\s*(\d+)\s+running", shown)
    return (int(m.group(1)), int(m.group(2))) if m else None


# A block that waits until go_<what it got>.txt is there (60 s at most), then passes it on.
GATED = ("import os, time\nfor _ in range(1200):\n    if os.path.exists(f'go_{input}.txt'):\n        break\n"
         "    time.sleep(0.05)\noutput = input")


def test_while_it_runs_the_merger_shows_done_of_n_and_how_many_runs_go(win, qtbot, tmp_path):
    """Spec 3 (the Merger shows "done/N · k running" like the Loop's) and 2 (At the same time 2): two lines arrive at
    once and both their runs go (0/3 · 2 running); one ends (1/3 · 1 running); the third line arrives and its run
    starts (1/3 · 2 running); then the run ends well."""
    stock = python("import os, time\nfor _ in range(1200):\n    if os.path.exists('go_stock_line.txt'):\n"
                   "        break\n    time.sleep(0.05)\noutput = 'stock'", x=300, y=300)
    blocks, edges = front({"load_sales": line("load_sales", "sales", y=-300),
                           "load_costs": line("load_costs", "costs", y=0), "load_stock": stock})
    merge = each_line(2, x=900)
    blocks |= {"merge": merge, "report": python(GATED, x=1500)}
    tab = win.open_flow(flow_file(tmp_path / "flow.yaml", blocks, [*edges, after("merge", merge, "report")]))
    try:
        run_in_editor(win, tab)
        qtbot.waitUntil(lambda: merger_progress(tab, 3) == (0, 2), timeout=30_000)

        go(tmp_path, "sales")

        qtbot.waitUntil(lambda: merger_progress(tab, 3) == (1, 1), timeout=20_000)

        go(tmp_path, "stock_line")

        qtbot.waitUntil(lambda: merger_progress(tab, 3) == (1, 2), timeout=20_000)

        go(tmp_path, "costs", "stock")
        wait_done(qtbot, tab)
    finally:
        go(tmp_path, "sales", "costs", "stock", "stock_line")
        if tab.running:  # (a step above failed: a run still waiting for a gate is stopped)
            win.stop(tab)
        wait_done(qtbot, tab, timeout=20_000)
        clean(win)
    assert tab.last["status"] == "success"


def test_the_block_after_the_merger_shows_how_its_runs_ended_and_the_last_ones_result_with_its_line(
        win, qtbot, tmp_path):
    """Spec 3 and 4, in the editor: report runs once per line and fails for load_stock's, the last line to end. After
    the run it shows how its runs ended ("✓ 2 ✕ 1"), and hovering it shows the last one's result, saying which line;
    the Merger failed, naming that run; shown again from the run history it is the same, and the Log has each run's
    lines, each with its block and its own line."""
    report = ("print(f'report gets {input}')\nif input == 'stock':\n    raise ValueError('no report today')\n"
              "output = input.upper()")
    blocks, edges = front(three((0.0, 0.4, 0.8)))
    merge = each_line()
    blocks |= {"merge": merge, "report": python(report, x=1300)}
    tab = win.open_flow(flow_file(tmp_path / "flow.yaml", blocks, [*edges, after("merge", merge, "report")]))
    try:
        run_in_editor(win, tab)
        wait_done(qtbot, tab)
        assert tab.last["status"] == "failed"
        run_id = tab.runs[-1].run_id

        def check_shown():
            node = tab.scene.nodes["report"]
            ended = node.status_text
            assert re.search(r"✓\s*2", ended) and re.search(r"[✗✕]\s*1", ended), ended
            tip = text_of(node.footer.toolTip())
            assert "load_stock" in tip and "no report today" in tip, tip
            assert tab.scene.nodes["merge"].status == "failed"
            for value, name in LINE_OF.items():
                said = [text for text in log_lines(win) if f"report gets {value}" in text]
                assert len(said) == 1 and "[report]" in said[0] and f"[line {name}]" in said[0], (value, said)

        check_shown()
        history = History(win.home.history_db)
        try:
            merge_result = history.blocks(run_id)["merge"]
        finally:
            history.close()
        assert merge_result["status"] == "failed" and "report failed for line load_stock" in merge_result["error"], \
            merge_result

        win.show_history_run(run_id, tab)
        settle()

        check_shown()
    finally:
        clean(win)


def test_right_click_the_merger_run_from_this_block_on_runs_again_only_the_runs_that_failed(win, qtbot, tmp_path):
    """Spec 5, the user's path: after a run where report failed for load_costs' line, and a fix, right-click the
    Merger > Run from this block on, with the same inputs runs report again only for that line; the lines are not run
    again; the Merger further on gets every line's result."""
    tab = win.open_flow(rerun_flow(tmp_path))
    try:
        run_in_editor(win, tab)
        wait_done(qtbot, tab)
        assert tab.last["status"] == "failed"
        (tmp_path / "fixed.txt").write_text("", encoding="utf-8")
        failed = tab.runs[-1]

        seen = right_click(qtbot, win, tab, "merge", "title", "Run from this block on, with the same inputs")

        assert any(t.startswith("Run from this block on, with the same inputs") for t in seen), seen
        qtbot.waitUntil(lambda: tab.runs[-1] is not failed and not tab.running and tab.runs[-1].status is not None,
                        timeout=60_000)
        assert tab.runs[-1].status == "success"
        assert {name: ran(tmp_path, name) for name in LINE_OF.values()} == {name: 1 for name in LINE_OF.values()}
        assert {v: ran(tmp_path, f"report_{v}") for v in LINE_OF} == {"sales": 1, "costs": 2, "stock": 1}
        assert read_json(tmp_path / "summary.json") == WHOLE
    finally:
        clean(win)


def test_run_from_here_and_run_only_are_not_offered_on_a_block_run_once_per_line_and_say_to_right_click_the_merger(
        win, qtbot, tmp_path):
    """Spec 5: on a block that runs once per line, Run only this block and Run from this block on are not offered in
    this release (also after a run): their entries cannot be chosen and say why (right-click the Merger). On the block
    after the Merger that closes the runs (it runs once), Run from this block on is offered as on any block."""
    blocks, edges = front({"load_sales": line("load_sales", "sales", y=-200),
                           "load_costs": line("load_costs", "costs", y=200)})
    merge, gather = each_line(), merger(x=1500)
    blocks |= {"merge": merge, "report": python("output = input", x=1200), "gather": gather,
               "summary": saver("summary.json", x=1800)}
    tab = win.open_flow(flow_file(tmp_path / "flow.yaml", blocks, [
        *edges, after("merge", merge, "report"), "report.output -> gather.lines", after("gather", gather, "summary")]))
    try:
        run_in_editor(win, tab)
        wait_done(qtbot, tab)
        assert tab.last["status"] == "success"
        runs = len(tab.runs)

        seen = context_menu(qtbot, win, tab, title(tab, "report"))

        offered = [(text, can, tip) for text, can, tip in seen
                   if text.startswith(("Run only this block", "Run from this block on"))]
        assert any(t.startswith("Run only this block") for t, _, _ in offered), seen
        assert any(t.startswith("Run from this block on") for t, _, _ in offered), seen
        assert not [t for t, can, _ in offered if can], offered
        assert all("merger" in f"{t} {tip}".lower() for t, _, tip in offered), offered

        seen = context_menu(qtbot, win, tab, title(tab, "summary"))

        assert [t for t, can, _ in seen if t.startswith("Run from this block on, with the same inputs") and can], seen
        assert len(tab.runs) == runs
    finally:
        clean(win)


# A block after the Merger closing the runs (gather) that also takes what a line's run has: it runs once, after them all.
CLOSED = """blocks:
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
"""
TAKES_FROM_A_RUN = {"the_merger": "merge.value -> summary.extra", "a_block_run_once_per_line": "report.output -> summary.extra"}


@pytest.mark.parametrize("edge", TAKES_FROM_A_RUN.values(), ids=TAKES_FROM_A_RUN.keys())
def test_a_block_after_the_merger_closing_the_runs_may_not_take_from_one_of_the_runs(env, edge):
    """Spec 2 and 6, and the no-surprises rule: summary runs once, after gather has every line's run; what one run has
    (merge's value for a line, or report's) reaches it only through gather. Validate refuses the connection, naming it,
    and the run does not start (it would otherwise wait forever)."""
    flow = env.root / "flow.yaml"
    flow.write_text(CLOSED + f"- {edge}\n", encoding="utf-8")

    checked = env.taskloom("validate", flow)

    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert [line for line in checked.stderr.splitlines() if line.startswith("error:") and edge in line], checked.stderr
    refused = env.taskloom("run", flow, timeout=60)
    assert refused.returncode == 2 and edge in refused.stderr, refused.stderr


def test_an_each_line_merger_inside_a_loop_whose_closing_merger_gives_the_loops_result_works(env):
    """Spec 2 and 6 (the README: it works inside a Loop's box): inside the Loop, merge (each line as it finishes) ->
    work -> gather (all lines together) -> the Loop's result. work runs once per line, then gather closes the runs and
    gives the item's result: Validate accepts it and the run gives each item's {line: work's result}."""
    blocks, edges, _, worked = in_a_loop(EACH)
    flow = flow_file(env.root / "flow.yaml", blocks, edges)

    code, errors, _ = validate(env, flow)

    assert code == 0, errors
    result, _ = run(env, flow)
    assert result.returncode == 0, result.stderr[-3000:]
    assert worked(env.root)


@pytest.mark.parametrize("handled", [False, True], ids=["unhandled", "handled_by_its_error_output"])
def test_a_failed_line_with_a_loop_in_the_runs_and_a_merger_closing_them_fails_the_merger_cleanly(env, handled):
    """Spec 4 with a Loop among the blocks run once per line: load_costs fails; merge (each line as it finishes) fails
    once every line has been dealt with, gather (closing the runs) and what follows it are skipped, and the run fails
    (load_costs' own failure is handled by no block: as with a Merger that joins), its error output's handler running
    when connected. No block is left without a result and nothing breaks inside Taskloom."""
    blocks, edges = front({"load_sales": line("load_sales", "sales", y=-200),
                           "load_costs": python("raise ValueError('no costs file today')", y=0, x=300),
                           "load_stock": line("load_stock", "stock", y=200)})
    merge, gather = each_line(), merger(x=2100)
    blocks |= {"merge": merge, "twice": python("output = [input, input]", x=1200),
               "per_item": {"type": "logic.loop", "config": {}, "ui": {"x": 1400, "y": -200}},
               "upper": inside("per_item", python("output = input.upper()", x=1500)),
               "gather": gather, "final": keeper("final", x=2400)}
    edges += [after("merge", merge, "twice"), "twice.output -> per_item.items", "per_item.item -> upper.input",
              "upper.output -> per_item.result", "per_item.results -> gather.lines", after("gather", gather, "final")]
    if handled:
        blocks["on_failure"] = python("open('handled.txt', 'w').write(error['error'])", inputs=("error",), outputs=(),
                                      x=1200, y=400)
        edges.append("merge.error -> on_failure.error")
    flow = flow_file(env.root / "flow.yaml", blocks, edges)
    assert validate(env, flow)[0] == 0

    result, run_id = run(env, flow)

    assert "Traceback" not in result.stderr, result.stderr[-3000:]
    assert result.returncode == 1, result.stderr[-3000:]
    assert result_of(env, run_id, "merge")["status"] == "failed"
    assert "load_costs failed" in result_of(env, run_id, "merge")["error"]
    assert [result_of(env, run_id, b)["status"] for b in ("gather", "final")] == ["skipped", "skipped"]
    assert (env.root / "handled.txt").exists() == handled


def test_validate_refuses_a_block_run_once_per_line_by_two_mergers_naming_it_and_both(env):
    """Spec 2 and 6 with the no-surprises rule: w takes what two Mergers set to each line as it finishes pass on, each
    for its own Splitter's lines: it cannot run once per line of both. Validate refuses it, naming w and both Mergers,
    and the run does not start."""
    m1, m2 = each_line(x=600, y=-200), each_line(x=600, y=200)
    blocks = {"manual": MANUAL, "load": LOAD,
              "s1": {"type": "logic.splitter", "ui": {"x": 0, "y": -200}},
              "s2": {"type": "logic.splitter", "ui": {"x": 0, "y": 200}},
              "a": python("output = 'a'", x=300, y=-300), "b": python("output = 'b'", x=300, y=-100),
              "c": python("output = 'c'", x=300, y=100), "d": python("output = 'd'", x=300, y=300),
              "m1": m1, "m2": m2, "w": python("output = x + y", ("x", "y"), x=900)}
    edges = ["manual.start -> load.after", "load.output -> s1.value", "load.output -> s2.value",
             "s1.lines -> a.input", "s1.lines -> b.input", "s2.lines -> c.input", "s2.lines -> d.input",
             "a.output -> m1.lines", "b.output -> m1.lines", "c.output -> m2.lines", "d.output -> m2.lines",
             after("m1", m1, "w", "x"), after("m2", m2, "w", "y")]
    flow = flow_file(env.root / "flow.yaml", blocks, edges)

    code, errors, _ = validate(env, flow)

    assert code == 2, errors
    assert [e for e in errors if re.search(r"\bw\b", e) and "m1" in e and "m2" in e], errors
    refused = env.taskloom("run", flow)
    assert refused.returncode == 2, refused.stderr
    assert no_runs(env)


def test_the_runs_go_in_the_order_the_lines_finished_also_when_the_merger_starts_after_some_have(env):
    """Spec 2 (in the order the lines finished): send, run once per line, also needs recipients, a slow block outside
    the lines (added last), so the Merger starts only once every line has ended: c, then b, then a. Its runs (At the
    same time 1) and its "arrived" Log lines still go c, b, a."""
    merge, close = each_line(), merger(x=1500)
    blocks = {"manual": MANUAL, "load": LOAD, "split": SPLIT,
              "a": line("a", "A", 0.9, y=-200), "b": line("b", "B", 0.5, y=0), "c": line("c", "C", 0.1, y=200),
              "merge": merge,
              "send": python("open('sent.txt', 'a', encoding='utf-8').write(input)\noutput = input",
                             ("input", "to"), x=1200),
              "close": close,
              "recipients": python("import time\ntime.sleep(1.5)\noutput = 'all'", inputs=(), x=900, y=400)}
    edges = ["manual.start -> load.after", "manual.start -> recipients.after", "load.output -> split.value",
             *(f"split.lines -> {b}.input" for b in "abc"), *(f"{b}.output -> merge.lines" for b in "abc"),
             after("merge", merge, "send"), "recipients.output -> send.to", "send.output -> close.lines"]
    flow = flow_file(env.root / "flow.yaml", blocks, edges)

    result, run_id = run(env, flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert (env.root / "sent.txt").read_text(encoding="utf-8") == "CBA"
    arrived = [m.split(" arrived")[0] for b, m in logs(env, run_id) if b == "merge" and " arrived" in m]
    assert arrived == ["c", "b", "a"], arrived


def test_validate_refuses_two_mergers_whose_runs_meet_at_a_loop_naming_them(env):
    """Spec 2 and 6: m2's runs reach the Loop per_item through its items; m1's reach a block inside it: the Loop would
    run once per line of both. Validate refuses it, naming the Loop (where they meet) and both Mergers."""
    m1, m2 = each_line(x=600, y=-200), each_line(x=600, y=200)
    blocks = {"manual": MANUAL, "load": LOAD,
              "s1": {"type": "logic.splitter", "ui": {"x": 0, "y": -200}},
              "s2": {"type": "logic.splitter", "ui": {"x": 0, "y": 200}},
              "a": python("output = 'a'", x=300, y=-300), "b": python("output = 'b'", x=300, y=-100),
              "c": python("output = 'c'", x=300, y=100), "d": python("output = 'd'", x=300, y=300),
              "m1": m1, "m2": m2, "twice": python("output = [input + '1', input + '2']", x=900, y=200),
              "per_item": {"type": "logic.loop", "config": {}, "ui": {"x": 1100, "y": 0}},
              "inner": inside("per_item", python("output = f'{item}/{x}'", ("item", "x"), x=1200)),
              "report": python("print(input)", outputs=(), x=1600)}
    edges = ["manual.start -> load.after", "load.output -> s1.value", "load.output -> s2.value",
             "s1.lines -> a.input", "s1.lines -> b.input", "s2.lines -> c.input", "s2.lines -> d.input",
             "a.output -> m1.lines", "b.output -> m1.lines", "c.output -> m2.lines", "d.output -> m2.lines",
             after("m2", m2, "twice"), "twice.output -> per_item.items", "per_item.item -> inner.item",
             after("m1", m1, "inner", "x"), "inner.output -> per_item.result", "per_item.results -> report.input"]
    flow = flow_file(env.root / "flow.yaml", blocks, edges)

    code, errors, _ = validate(env, flow)

    assert code == 2, errors
    assert [e for e in errors if re.search(r"\bper_item\b", e) and "m1" in e and "m2" in e], errors


RUNS_AGAIN = {"line_order": ("line order", "ABC"), "the_order_they_finished": ("the order the lines finished", "CBA")}


@pytest.mark.parametrize("how", ["--from", "--only"])
@pytest.mark.parametrize("order", RUNS_AGAIN.values(), ids=RUNS_AGAIN.keys())
def test_running_again_from_the_merger_goes_in_line_order_or_in_the_order_the_lines_finished_as_set(env, order, how):
    """The user's choice (2026-11-03): running again from the Merger (or only it), the runs it does again go in line
    order, top to bottom as on the canvas (the default), or, as its setting Runs again in says, in the order the lines
    finished in the run it carries on from. Here a (top) finished last and c (bottom) first; the flow file lists them
    in neither order."""
    shown, expected = order
    merge = each_line()
    cls = block_class(MERGER)
    key = setting(cls, "Runs again in")
    options = getattr(cls.config[key], "options", None)
    assert options and shown in [normal(o) for o in options], options
    if shown != "line order":
        merge["config"][key] = next(o for o in options if normal(o) == shown)
    else:
        assert normal(cls.config[key].default) == "line order"
    blocks = {"manual": MANUAL, "load": LOAD, "split": SPLIT,
              "c": line("c", "C", 0.1, y=200), "a": line("a", "A", 0.9, y=-200), "b": line("b", "B", 0.5, y=0),
              "merge": merge,
              "send": python("open('sent.txt', 'a', encoding='utf-8').write(input)\nimport os\n"
                             "if not os.path.exists('fixed.txt'): raise ValueError('not yet')\noutput = input", x=1200)}
    edges = ["manual.start -> load.after", "load.output -> split.value",
             *(f"split.lines -> {b}.input" for b in "cab"), *(f"{b}.output -> merge.lines" for b in "cab"),
             after("merge", merge, "send")]
    flow = flow_file(env.root / "flow.yaml", blocks, edges)
    first, first_id = run(env, flow)
    assert first.returncode == 1 and (env.root / "sent.txt").read_text(encoding="utf-8") == "CBA", first.stderr[-2000:]
    (env.root / "sent.txt").unlink()
    (env.root / "fixed.txt").write_text("ok", encoding="utf-8")

    again, again_id = run(env, flow, how, "merge", "--inputs-from", str(first_id))

    assert again.returncode == 0, again.stderr[-3000:]
    assert (env.root / "sent.txt").read_text(encoding="utf-8") == expected
    arrived = [m.split(" arrived")[0] for b, m in logs(env, again_id) if b == "merge" and " arrived" in m]
    assert "".join(arrived).upper() == expected, arrived
