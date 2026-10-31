"""A Python Code block's `output`, by default, is a dict of every variable its code created ({name: value}); when the
code sets `output = ...`, that is what goes out. Tables in it (a pandas DataFrame in your Python environment, a polars
DataFrame in Taskloom's own Python), at any depth, pass on as tables and are kept with the run; a variable that cannot
be passed on is left out, and the Log says so. In both modes, through `taskloom run` with the run history checked,
and in the offscreen editor (the block's result, Data preview, a new block's starter comment).

The blocks after the one under test write what they received as JSON, a table as {"table": its rows, sorted}: in
Taskloom's own Python a table arrives as a Table, in your environment as a pandas DataFrame."""

import html
import importlib.util
import json
import os
import re
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import Env  # noqa: E402
from taskloom.history import History  # noqa: E402

# --- the flows ------------------------------------------------------------------------------------------------

# Writes what `input` received to FILE, as JSON. Runs in Taskloom's own Python, where a table is a Table.
RECEIVE = '''\
import json

def seen(value):
    if isinstance(value, Table):
        return {"table": sorted(value.scan().collect().to_dicts(), key=lambda r: json.dumps(r, sort_keys=True))}
    if isinstance(value, dict):
        return {str(k): seen(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [seen(v) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return {"other": type(value).__module__ + "." + type(value).__name__}

with open(FILE, "w", encoding="utf-8") as _out:
    json.dump(seen(input), _out)
'''

# The same in your Python environment (with pandas), where a table is a pandas DataFrame.
RECEIVE_PANDAS = '''\
import json
import pandas

def seen(value):
    if isinstance(value, pandas.DataFrame):
        rows = json.loads(value.to_json(orient="records"))
        return {"table": sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))}
    if isinstance(value, dict):
        return {str(k): seen(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [seen(v) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return {"other": type(value).__module__ + "." + type(value).__name__}

with open(FILE, "w", encoding="utf-8") as _out:
    json.dump(seen(input), _out)
'''

SALES = ("select 'North' as region, 30 as total union all select 'South', 50 "
         "union all select 'North', 20")


def table(*rows: tuple) -> dict:
    """A table as the receiving blocks write it: {"table": its rows, sorted}."""
    return {"table": sorted(({"region": r, "total": t} for r, t in rows), key=lambda r: json.dumps(r, sort_keys=True))}


NORTH, SOUTH = table(("North", 30), ("North", 20)), table(("South", 50))
COMBINED = table(("North", 30), ("North", 20), ("South", 50))


def python(code: str, inputs=("input",), outputs=("output",), environment=False) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs),
                                               "use_environment": environment, "code": textwrap.dedent(code)}}


def receive(file: str, pandas=False) -> dict:
    """A block writing what its `input` got to `file` (in your environment with pandas: `pandas`). It has no
    outputs, so nothing of its own goes out."""
    code = (RECEIVE_PANDAS if pandas else RECEIVE).replace("FILE", repr(file))
    return python(code, outputs=(), environment=pandas)


def write_flow(env, blocks: dict, edges=(), params=None) -> Path:
    flow = {**({"params": params} if params else {}), "blocks": blocks, "edges": list(edges)}
    path = env.root / "flow.yaml"
    path.write_text(yaml.safe_dump(flow, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def run(env, flow, *args):
    result = env.taskloom("run", flow, *args, timeout=300)
    run_id, status = env.last_run()
    return result, run_id, status


def logs(env, run_id, block) -> list[str]:
    return [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block=? ORDER BY rowid",
                                    run_id, block)]


def report(env, run_id) -> str:
    """Each block's result and Log in the run, for assertion messages."""
    rows = env.query("SELECT block, status, error, summary FROM blocks WHERE run_id=?", run_id)
    return "\n".join(f"{b}: {s} | {e or ''} | {m or ''} | log: {logs(env, run_id, b)}" for b, s, e, m in rows)


def received(env, run_id, file: str):
    path = env.root / file
    assert path.exists(), f"the block writing {file} did not run:\n{report(env, run_id)}"
    return json.loads(path.read_text(encoding="utf-8"))


def assert_succeeded(env, result, run_id, status):
    assert result.returncode == 0 and status == "success", result.stdout + result.stderr + "\n" + report(env, run_id)


# --- where the code runs -------------------------------------------------------------------------------------

def use(env, folder: Path):
    """The python_env setting (Use an existing environment…). JSON is YAML: a Windows path's backslashes stay."""
    (env.home / "settings.yaml").write_text(f"python_env: {json.dumps(str(folder))}\n", encoding="utf-8")


@pytest.fixture(scope="module")
def bare_env(tmp_path_factory):
    """Your Python environment made with `taskloom env create`, nothing installed in it."""
    maker = Env(tmp_path_factory.mktemp("bare"))
    made = maker.taskloom("env", "create", "--python", sys.executable, timeout=180)
    assert made.returncode == 0, made.stdout + made.stderr
    return maker.home / "python-env"


@pytest.fixture(scope="module")
def pandas_env(tmp_path_factory):
    """Your Python environment with pandas, made the way Taskloom says: `taskloom env pip install pandas pyarrow`.
    Taskloom's own Python (the one running these tests) has no pandas, as for the user."""
    assert importlib.util.find_spec("pandas") is None, "these tests need Taskloom's own Python without pandas"
    maker = Env(tmp_path_factory.mktemp("pandas"))
    made = maker.taskloom("env", "create", "--python", sys.executable, timeout=180)
    assert made.returncode == 0, made.stdout + made.stderr
    done = maker.taskloom("env", "pip", "install", "pandas", "pyarrow", timeout=900)
    if done.returncode != 0:
        if os.environ.get("CI"):  # CI has internet: never skipped there
            raise AssertionError(done.stdout + done.stderr)
        pytest.skip(f"the package index is unreachable here (pip install failed): {done.stdout[-500:]}")
    return maker.home / "python-env"


@pytest.fixture(params=[False, True], ids=["taskloom_python", "your_environment"])
def environment(request, env):
    """Whether the block under test runs in your Python environment (one with nothing installed) or in Taskloom's
    own Python."""
    if request.param:
        use(env, request.getfixturevalue("bare_env"))
    return request.param


# --- 1. without `output = ...`: a dict of the variables the code created -------------------------------------

DEFAULT_CODE = """\
import json
from os import path

def double(n):
    return n * 2

class Row:
    pass

_hidden = "not passed on"
rows = input
count = double(len(input))
under_limit = [n for n in input if n < limit]
settings = {"region": params["region"], "limit": limit}
log("made", count)
print("rows:", len(rows))
"""


def test_without_output_the_output_is_a_dict_of_the_variables_the_code_created(env, environment):
    """Spec 1: the code never sets `output`, so `output` is {name: value} of the variables it created at top level;
    not its inputs (`input`, `limit`), params, the helpers (log, print, ctx, pl, Table), the modules it imported,
    the function and class it defined, or names starting with _."""
    flow = write_flow(env, {
        "numbers": python("output = [3, 4, 12]", inputs=()),
        "limit": python("output = 10", inputs=()),
        "make": python(DEFAULT_CODE, inputs=("input", "limit"), environment=environment),
        "next": receive("received.json"),
    }, ["numbers.output -> make.input", "limit.output -> make.limit", "make.output -> next.input"],
        params={"region": {"type": "text", "value": "North"}})

    result, run_id, status = run(env, flow)

    assert_succeeded(env, result, run_id, status)
    assert received(env, run_id, "received.json") == {
        "rows": [3, 4, 12], "count": 6, "under_limit": [3, 4], "settings": {"region": "North", "limit": 10}}
    # Nothing the block was given is reported as left out either (e.g. ctx, which cannot be passed on).
    made = logs(env, run_id, "make")
    assert not [m for m in made if re.search(r"\b(ctx|params)\b", m)], made


def test_code_that_creates_no_variables_gives_an_empty_dict(env, environment):
    """Spec 1: a code that creates no variables gives {} (its input, connected to nothing, is not one of them);
    the block after it runs with it."""
    flow = write_flow(env, {
        "make": python('# only a comment, and a line in the Log\nprint("nothing to pass on")',
                       environment=environment),
        "next": receive("received.json"),
    }, ["make.output -> next.input"])

    result, run_id, status = run(env, flow)

    assert_succeeded(env, result, run_id, status)
    assert received(env, run_id, "received.json") == {}


# --- 2. `output = ...` overrides it; other outputs: not set = not produced -----------------------------------

@pytest.mark.parametrize("value, expected", [("42", 42), ("None", None)], ids=["number", "none"])
def test_output_the_code_sets_is_what_goes_out_not_the_dict(env, environment, value, expected):
    """Spec 2 (protects today's behaviour): the code sets `output`, so that value goes out, also when it is None,
    not a dict of its variables."""
    flow = write_flow(env, {
        "make": python(f"rows = [1, 2]\ncount = len(rows)\noutput = {value}", inputs=(), environment=environment),
        "next": receive("received.json"),
    }, ["make.output -> next.input"])

    result, run_id, status = run(env, flow)

    assert_succeeded(env, result, run_id, status)
    assert received(env, run_id, "received.json") == expected


def test_other_outputs_are_produced_only_when_the_code_sets_them(env, environment):
    """Spec 2: Outputs other than `output` keep today's rule (not set = not produced, what needs it is skipped),
    while `output` is the dict of the variables (those other outputs among them)."""
    flow = write_flow(env, {
        "make": python("north = [30, 20]\nregion = 'North'", inputs=(), outputs=("output", "north", "missing"),
                       environment=environment),
        "next": receive("received.json"),
        "next_north": receive("north.json"),
        "use_missing": python("open('missing.txt', 'w').write('ran')", inputs=("missing",), outputs=()),
    }, ["make.output -> next.input", "make.north -> next_north.input", "make.missing -> use_missing.missing"])

    result, run_id, status = run(env, flow)

    assert_succeeded(env, result, run_id, status)
    assert received(env, run_id, "received.json") == {"north": [30, 20], "region": "North"}
    assert received(env, run_id, "north.json") == [30, 20]
    assert env.block_status(run_id)["use_missing"] == "skipped", report(env, run_id)
    assert not (env.root / "missing.txt").exists()


def test_a_block_whose_outputs_do_not_include_output_has_no_default_output(env, environment):
    """Spec 1-2 (protects): the default dict is the `output` output's; a block whose Outputs leave `output` out
    runs as today, its other variables going nowhere (it does not fail on an output it does not have)."""
    flow = write_flow(env, {
        "make": python("rows = [1, 2]\nextra = 3", inputs=(), outputs=("rows",), environment=environment),
        "next": receive("received.json"),
    }, ["make.rows -> next.input"])

    result, run_id, status = run(env, flow)

    assert_succeeded(env, result, run_id, status)
    assert received(env, run_id, "received.json") == [1, 2]


# --- 3. tables anywhere in the output pass on as tables --------------------------------------------------------

POLARS_CODE = """\
north = pl.DataFrame({"region": ["North", "North"], "total": [30, 20]})
south = pl.DataFrame({"region": ["South"], "total": [50]})
combined = pl.concat([north, south])
percent_total = 50.0
by_region = {"north": {"rows": north}, "south": {"rows": south}}
parts = [north, [south]]
"""
SET_OUTPUT = "output = {'percent_total': percent_total, 'combined': combined}\n"


@pytest.mark.parametrize("how", ["default", "set"])
def test_in_taskloom_python_polars_tables_at_any_depth_of_the_output_pass_on_as_tables(env, how):
    """Spec 3: polars DataFrames in the output, in dicts and lists at any depth, reach the next block as Taskloom
    tables, as a DataFrame set as the output itself does; two tables under the same name in different dicts stay
    two tables. `default`: the dict of the variables; `set`: the user's `output = {...}` line."""
    flow = write_flow(env, {
        "make": python(POLARS_CODE + (SET_OUTPUT if how == "set" else ""), inputs=()),
        "next": receive("received.json"),
    }, ["make.output -> next.input"])

    result, run_id, status = run(env, flow)

    assert_succeeded(env, result, run_id, status)
    expected = {"percent_total": 50.0, "combined": COMBINED} if how == "set" else {
        "north": NORTH, "south": SOUTH, "combined": COMBINED, "percent_total": 50.0,
        "by_region": {"north": {"rows": NORTH}, "south": {"rows": SOUTH}}, "parts": [NORTH, [SOUTH]]}
    assert received(env, run_id, "received.json") == expected


# The user's code, in their environment with pandas; `set` ends with the user's line, `default` is without it.
USERS_CODE = """\
import pandas as pd
north = input[input["region"] == "North"]
south = input[input["region"] == "South"]
combined = pd.concat([north, south], ignore_index=True)
percent_total = round(north["total"].sum() / combined["total"].sum() * 100, 1)
by_region = {"north": {"rows": north}, "south": {"rows": south}}
parts = [north, [south]]
"""


@pytest.mark.parametrize("how", ["set", "default"])
def test_the_users_case_a_dict_holding_a_pandas_dataframe_reaches_the_next_blocks(env, pandas_env, how):
    """The user's report, spec 1 and 3: in your environment with pandas (Taskloom's own Python has none), the code
    ending with `output = {'percent_total': percent_total, 'combined': combined}` (combined a pandas DataFrame) no
    longer fails on pandas; without that line, every variable goes out. The next Python Code block gets the dict,
    tables inside dicts and lists at any depth arriving as tables: in your environment they are pandas DataFrames,
    in Taskloom's own Python they are Taskloom tables."""
    use(env, pandas_env)
    flow = write_flow(env, {
        "sales": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": SALES}},
        "report": python(USERS_CODE + (SET_OUTPUT if how == "set" else ""), environment=True),
        "outside": receive("outside.json", pandas=True),
        "inside": receive("inside.json"),
    }, ["sales.result -> report.input", "report.output -> outside.input", "report.output -> inside.input"])

    result, run_id, status = run(env, flow)

    assert_succeeded(env, result, run_id, status)
    expected = {"percent_total": 50.0, "combined": COMBINED} if how == "set" else {
        "north": NORTH, "south": SOUTH, "combined": COMBINED, "percent_total": 50.0,
        "by_region": {"north": {"rows": NORTH}, "south": {"rows": SOUTH}}, "parts": [NORTH, [SOUTH]]}
    assert received(env, run_id, "outside.json") == expected
    assert received(env, run_id, "inside.json") == expected


# --- 4. what cannot be passed on ------------------------------------------------------------------------------

UNPASSABLE_CODE = """\
import sqlite3
notes_file = open("notes.txt", encoding="utf-8")
lazy_numbers = (n for n in range(3))
warehouse_db = sqlite3.connect(":memory:")
kept = [1, 2]
"""


def test_a_variable_that_cannot_be_passed_on_is_left_out_and_one_log_line_says_which(env, environment):
    """Spec 4: an open file, a generator and a database connection are left out of the default dict, the rest goes
    out, and one line of the block's Log names the variables left out and why, by the kind of value each one is
    (no silent drop)."""
    (env.root / "notes.txt").write_text("a note\n", encoding="utf-8")
    flow = write_flow(env, {
        "make": python(UNPASSABLE_CODE, inputs=(), environment=environment),
        "next": receive("received.json"),
    }, ["make.output -> next.input"])

    result, run_id, status = run(env, flow)

    assert_succeeded(env, result, run_id, status)
    assert received(env, run_id, "received.json") == {"kept": [1, 2]}
    made = logs(env, run_id, "make")
    lines = [m for m in made if all(name in m for name in ("notes_file", "lazy_numbers", "warehouse_db"))]
    assert len(lines) == 1 and "\n" not in lines[0].strip(), made
    assert all(kind in lines[0] for kind in ("TextIOWrapper", "generator", "Connection")), lines[0]


def test_in_your_environment_a_pandas_value_taskloom_cannot_read_is_left_out_and_the_log_says_so(env, pandas_env):
    """Spec 4 and the user's report: in your environment, a variable holding a pandas value that is not a DataFrame
    (a Series) cannot be passed on to Taskloom's own Python, which has no pandas: it is left out of the default
    dict with a line in the Log naming `totals` and saying why (Taskloom's own Python has no pandas); the block
    does not fail on pandas."""
    use(env, pandas_env)
    flow = write_flow(env, {
        "make": python("""\
            import pandas as pd
            combined = pd.DataFrame({"region": ["North", "South"], "total": [30, 50]})
            totals = combined["total"]
            grand_total = int(totals.sum())
            """, inputs=(), environment=True),
        "next": receive("received.json"),
    }, ["make.output -> next.input"])

    result, run_id, status = run(env, flow)

    assert_succeeded(env, result, run_id, status)
    assert received(env, run_id, "received.json") == {"combined": table(("North", 30), ("South", 50)),
                                                      "grand_total": 80}
    made = logs(env, run_id, "make")
    lines = [m for m in made if re.search(r"\btotals\b", m)]
    assert len(lines) == 1, made
    [line] = lines
    assert "pandas" in line, line


def test_in_your_environment_output_set_to_something_that_cannot_be_passed_on_fails_the_block(env, bare_env):
    """Spec 4 (protects today's behaviour): the code sets `output` to a generator, which cannot be passed on: the
    block fails saying so (it is not silently dropped), and the block after it does not run."""
    use(env, bare_env)
    flow = write_flow(env, {
        "make": python("output = (n for n in range(3))", inputs=(), environment=True),
        "next": receive("received.json"),
    }, ["make.output -> next.input"])

    result, run_id, status = run(env, flow)

    assert result.returncode == 1 and status == "failed", report(env, run_id)
    [(block_status, error)] = env.query("SELECT status, error FROM blocks WHERE run_id=? AND block='make'", run_id)
    assert block_status == "failed" and "generator" in (error or ""), report(env, run_id)
    assert not (env.root / "received.json").exists()


# --- 5. kept with the run: Run only this block / Run from here ------------------------------------------------

KEPT_CODE = """\
north = {frame}({{"region": ["North", "North"], "total": [30, 20]}})
south = {frame}({{"region": ["South"], "total": [50]}})
percent_total = 37.5
by_region = {{"north": {{"rows": north}}, "south": {{"rows": south}}}}
"""


@pytest.mark.parametrize("where", ["taskloom_python", "your_environment_with_pandas"])
def test_the_dict_and_its_tables_are_kept_for_run_only_this_block_and_run_from_here(env, request, where):
    """Spec 5: the default dict, its tables (at any depth) as tables, is kept with the run, so running the next
    block on its own (Run only this block, --only) or from it (Run from here, --from) with that run's inputs gets
    it again."""
    environment = where != "taskloom_python"
    if environment:
        use(env, request.getfixturevalue("pandas_env"))
    code = ("import pandas as pd\n" + KEPT_CODE.format(frame="pd.DataFrame")) if environment else \
        KEPT_CODE.format(frame="pl.DataFrame")
    flow = write_flow(env, {
        "make": python(code, inputs=(), environment=environment),
        "next": receive("received.json"),
    }, ["make.output -> next.input"])
    expected = {"north": NORTH, "south": SOUTH, "percent_total": 37.5,
                "by_region": {"north": {"rows": NORTH}, "south": {"rows": SOUTH}}}

    result, first, status = run(env, flow)
    assert_succeeded(env, result, first, status)
    assert received(env, first, "received.json") == expected

    for how in ("--only", "--from"):
        (env.root / "received.json").unlink()
        result, run_id, status = run(env, flow, how, "next", "--inputs-from", first)
        assert run_id != first
        assert_succeeded(env, result, run_id, status)
        assert received(env, run_id, "received.json") == expected, how


# --- 6. the block's result and Data preview in the editor -----------------------------------------------------

def test_the_blocks_result_and_data_preview_show_the_dict_and_preview_its_tables_by_name(win, qtbot, tmp_path):
    """Spec 6: after a run, hovering the block's result shows the dict sensibly (each name, the number, each table's
    size; not a Table object's repr), the stored summary is not that repr either, and Data preview offers each table
    in the dict by its name in the dict, as a grid of its rows, next to the dict itself."""
    flow = tmp_path / "preview.yaml"
    flow.write_text(yaml.safe_dump({"blocks": {
        "start": {"type": "trigger.manual"},
        "make": python("""\
            north = pl.DataFrame({"region": ["North", "North"], "total": [30, 20]})
            combined = pl.concat([north, pl.DataFrame({"region": ["South"], "total": [50]})])
            percent_total = 50.0
            """, inputs=()),
    }, "edges": ["start.start -> make.after"]}, sort_keys=False), encoding="utf-8")
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=90_000)
    assert tab.last["status"] == "success", win.log.text.toPlainText()
    QApplication.processEvents()

    raw = ("Table(", "Path(", "__table__")
    tip = html.unescape(re.sub(r"<[^>]+>", " ", tab.scene.nodes["make"].footer.toolTip()))
    for shown in ("north", "combined", "percent_total", "50.0", "2 rows", "3 rows"):
        assert shown in tip, (shown, tip)
    assert not [r for r in raw if r in tip], tip
    history = History(win.home.history_db)
    try:
        summary = history.blocks(tab.last["id"])["make"]["summary"] or ""
    finally:
        history.close()
    assert "north" in summary and not [r for r in raw if r in summary], summary

    tab.scene.clearSelection()
    tab.scene.nodes["make"].setSelected(True)
    QApplication.processEvents()
    preview = win.preview
    names = [preview.ports.itemText(i) for i in range(preview.ports.count())]
    for name, rows in (("combined", 3), ("north", 2)):
        [item] = [n for n in names if name in n]  # e.g. output.combined
        preview.ports.setCurrentText(item)
        QApplication.processEvents()
        model = preview.view.model()
        assert not preview.view.isHidden() and model is not None, (item, preview.label.text())
        assert model.rowCount() == rows and f"{rows} rows" in preview.label.text(), (item, preview.label.text())
        headers = [model.headerData(c, Qt.Horizontal).split("\n")[0] for c in range(model.columnCount())]
        assert headers == ["region", "total"], headers
    assert "output" in names, names
    preview.ports.setCurrentText("output")
    QApplication.processEvents()
    text = preview.text.toPlainText()
    assert not preview.text.isHidden() and "percent_total" in text and "50.0" in text, text
    assert not [r for r in raw if r in text], text


# --- 7. a new Python Code block's starter comment ------------------------------------------------------------

STARTER = """\
# Inputs are available as variables (default: `input`).
#
# ┌──────────────┐
# │  Load Sales  │─────► Python
# └──────────────┘
#
#   input = sales table
#
# ┌──────────────┐
# │  Load Sales  │──┐
# └──────────────┘  │
#                   ├──► Python
# ┌──────────────┐  │
# │  Ask Region  │──┘
# └──────────────┘
#
#   input = {
#       "load_sales": <table>,
#       "ask_region": "North"
#   }
#
#   sales = input["load_sales"]
#   region = input["ask_region"]
#
# Tables are pandas DataFrames:
#   north = sales[sales["region"] == region]
#
# Parameters:
#   day = params["day"]
#
# Log output:
#   print("Found", len(north), "rows")
#
# What goes out:
#   By default, every variable you create, as a dict:
#     output = {"sales": ..., "region": ..., "north": ...}
#   To send something else, set output yourself:
#     output = north"""


def test_a_new_python_blocks_starter_comment_says_what_comes_in_and_what_goes_out(win):
    """Spec 7: a Python Code block added from the Blocks tree starts with exactly this comment (its box drawings
    aligned), which says that every variable goes out as a dict by default and how to send something else."""
    tab = win.new_flow()
    try:
        before = set(tab.doc.blocks)
        [item] = win.palette_panel.tree.findItems("Python Code", Qt.MatchExactly | Qt.MatchRecursive)
        win.palette_panel.tree.itemDoubleClicked.emit(item, 0)
        QApplication.processEvents()
        [block] = set(tab.doc.blocks) - before
        code = (tab.doc.blocks[block].get("config") or {}).get("code") or ""
    finally:
        tab.doc.dirty = False  # closing the window asks nothing
    assert code.rstrip("\n") == STARTER, code
