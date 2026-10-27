"""Run in my Python environment: every table a Python Code block receives is a pandas DataFrame, whatever else
happens to be installed there; without pandas (or pyarrow, which pandas needs to read Parquet) the block fails
before your code runs and says what to install and where. Through `taskloom run` with the run history checked."""

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import Env
from taskloom.pyenv import python_in


def _made(tmp_path_factory, name: str, *installs: list[str]) -> Path:
    """An environment made with `taskloom env create`, then `taskloom env pip ...` for each of `installs`."""
    maker = Env(tmp_path_factory.mktemp(name))
    made = maker.taskloom("env", "create", "--python", sys.executable, timeout=180)
    assert made.returncode == 0, made.stdout + made.stderr
    for args in installs:
        done = maker.taskloom("env", "pip", *args, timeout=900)
        if done.returncode != 0:
            if os.environ.get("CI"):  # CI has internet: never skipped there
                raise AssertionError(done.stdout + done.stderr)
            pytest.skip(f"the package index is unreachable here (pip {' '.join(args)} failed): {done.stdout[-500:]}")
    return maker.home / "python-env"


def _has(folder: Path, module: str) -> bool:
    return subprocess.run([str(python_in(folder)), "-I", "-c", f"import {module}"], capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def bare_env(tmp_path_factory):
    """Only what Python itself comes with: no pandas, no pyarrow."""
    folder = _made(tmp_path_factory, "bare")
    assert not _has(folder, "pandas") and not _has(folder, "pyarrow")
    return folder


@pytest.fixture(scope="module")
def polars_env():
    """The Python running these tests, as "an existing environment": it has Taskloom's polars and pyarrow but no
    pandas."""
    assert importlib.util.find_spec("polars") and importlib.util.find_spec("pyarrow")
    assert importlib.util.find_spec("pandas") is None, "this test needs a Python without pandas"
    return Path(sys.prefix)


@pytest.fixture(scope="module")
def pandas_only_env(tmp_path_factory):
    """pandas without pyarrow: pandas cannot read Parquet there."""
    folder = _made(tmp_path_factory, "pandas-only", ["install", "pandas"], ["uninstall", "-y", "pyarrow"])
    assert _has(folder, "pandas") and not _has(folder, "pyarrow")
    return folder


@pytest.fixture(scope="module")
def pandas_env(tmp_path_factory):
    """Made the way the block's message says: `taskloom env pip install pandas pyarrow`."""
    folder = _made(tmp_path_factory, "pandas", ["install", "pandas", "pyarrow"])
    assert _has(folder, "pandas") and _has(folder, "pyarrow")
    return folder


def use(env, folder: Path):
    """The python_env setting (Use an existing environment…). JSON is YAML: a Windows path's backslashes stay."""
    (env.home / "settings.yaml").write_text(f"python_env: {json.dumps(str(folder))}\n", encoding="utf-8")


def write_flow(env, text: str) -> Path:
    path = env.root / "flow.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def block(env, run_id, name) -> dict:
    found = env.query("SELECT status, error FROM blocks WHERE run_id=? AND block=?", run_id, name)
    assert found, f"{name} has no result in run #{run_id}"
    return dict(zip(("status", "error"), found[0]))


def logs(env, run_id, name) -> str:
    return "\n".join(m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block=?", run_id, name))


SALES = "select 'EU' as region, 10 as total union all select 'US', 20"


def assert_says_install_pandas_and_pyarrow(env, run_id, result):
    """The block failed, naming what to install (pandas and pyarrow) and where; your code never ran."""
    outside = block(env, run_id, "outside")
    error = outside["error"] or ""
    assert outside["status"] == "failed" and "pandas" in error and "pyarrow" in error, (
        outside, logs(env, run_id, "outside"))
    assert re.search(r"Tools (→|->) Python environment", error), error
    assert "taskloom env pip install pandas pyarrow" in error, error
    assert result.returncode == 1
    assert not (env.root / "ran.txt").exists(), "your code ran"


# The user's case: what a table is must not depend on what happens to be installed in the environment.
@pytest.mark.parametrize("environment", ["bare_env", "polars_env", "pandas_only_env"])
def test_without_pandas_or_pyarrow_a_table_input_fails_before_your_code_saying_what_to_install(env, request,
                                                                                              environment):
    use(env, request.getfixturevalue(environment))
    flow = write_flow(env, f"""
blocks:
  sales:
    type: data.duckdb_sql
    config: {{inputs: [], sql: "{SALES}"}}
  outside:
    type: logic.python
    config:
      inputs: [table]
      outputs: [rows]
      use_environment: true
      code: |
        open("ran.txt", "w").write("ran")
        log("table arrived as", type(table).__module__, type(table).__name__)
        rows = len(table)
edges:
  - sales.result -> outside.table
""")
    result = env.taskloom("run", flow)
    run_id, _ = env.last_run()

    assert_says_install_pandas_and_pyarrow(env, run_id, result)


def test_a_table_inside_a_dict_input_also_needs_pandas(env, bare_env):
    use(env, bare_env)
    flow = write_flow(env, f"""
blocks:
  sales:
    type: data.duckdb_sql
    config: {{inputs: [], sql: "{SALES}"}}
  group:
    type: logic.python
    config: {{inputs: [sales], outputs: [sources], code: "sources = {{'name': 'sales', 'tables': [sales]}}"}}
  outside:
    type: logic.python
    config:
      inputs: [sources]
      outputs: [rows]
      use_environment: true
      code: |
        open("ran.txt", "w").write("ran")
        log("table arrived as", type(sources["tables"][0]).__name__)
        rows = len(sources["tables"][0])
edges:
  - sales.result -> group.sales
  - group.sources -> outside.sources
""")
    result = env.taskloom("run", flow)
    run_id, _ = env.last_run()

    assert_says_install_pandas_and_pyarrow(env, run_id, result)


def test_a_block_with_no_table_input_runs_without_pandas(env, bare_env):
    use(env, bare_env)
    flow = write_flow(env, """
blocks:
  data:
    type: logic.python
    config: {inputs: [], outputs: [rows, names], code: "rows = {'EU': [10, 20], 'US': {'total': 30}}; names = ['EU', 'US']"}
  outside:
    type: logic.python
    config:
      inputs: [rows, names, unset]
      outputs: [total]
      use_environment: true
      code: |
        total = sum(rows["EU"]) + rows["US"]["total"] + len(names)
  save:
    type: logic.python
    config: {inputs: [total], outputs: [], code: "open('out.txt', 'w').write(repr(total))"}
edges:
  - data.rows -> outside.rows
  - data.names -> outside.names
  - outside.total -> save.total
""")
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", (result.stderr, block(env, run_id, "outside"))
    assert (env.root / "out.txt").read_text() == "62"


def test_with_pandas_a_table_arrives_as_a_pandas_dataframe_and_dataframes_you_set_come_back_as_tables(env,
                                                                                                    pandas_env):
    use(env, pandas_env)
    flow = write_flow(env, f"""
blocks:
  sales:
    type: data.duckdb_sql
    config: {{inputs: [], sql: "{SALES}"}}
  outside:
    type: logic.python
    config:
      inputs: [table]
      outputs: [doubled, arrow]
      use_environment: true
      code: |
        import pandas, pyarrow
        log("pandas DataFrame:", isinstance(table, pandas.DataFrame), "totals:", sorted(table["total"].tolist()))
        doubled = table.assign(total=table["total"] * 2)
        arrow = pyarrow.Table.from_pandas(table, preserve_index=False)
  save:
    type: data.write_file
    config: {{path: doubled.csv}}
  save_arrow:
    type: data.write_file
    config: {{path: arrow.csv}}
edges:
  - sales.result -> outside.table
  - outside.doubled -> save.table
  - outside.arrow -> save_arrow.table
""")
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", (result.stderr, block(env, run_id, "outside"))
    assert "pandas DataFrame: True totals: [10, 20]" in logs(env, run_id, "outside")
    lines = (env.root / "doubled.csv").read_text(encoding="utf-8").splitlines()
    assert sorted(lines) == ["EU,20", "US,40", "region,total"]
    lines = (env.root / "arrow.csv").read_text(encoding="utf-8").splitlines()
    assert sorted(lines) == ["EU,10", "US,20", "region,total"]


def test_a_polars_dataframe_you_set_comes_back_as_a_table(env, polars_env):
    use(env, polars_env)
    flow = write_flow(env, """
blocks:
  outside:
    type: logic.python
    config:
      inputs: []
      outputs: [frame]
      use_environment: true
      code: |
        import polars
        frame = polars.DataFrame({"region": ["EU", "US"], "total": [5, 7]})
  save:
    type: data.write_file
    config: {path: out.csv}
edges:
  - outside.frame -> save.table
""")
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", (result.stderr, block(env, run_id, "outside"))
    lines = (env.root / "out.csv").read_text(encoding="utf-8").splitlines()
    assert sorted(lines) == ["EU,5", "US,7", "region,total"]


def test_with_pandas_a_table_inside_a_dict_or_list_input_at_any_depth_is_a_pandas_dataframe(env, pandas_env):
    use(env, pandas_env)
    flow = write_flow(env, f"""
blocks:
  sales:
    type: data.duckdb_sql
    config: {{inputs: [], sql: "{SALES}"}}
  more:
    type: data.duckdb_sql
    config: {{inputs: [], sql: "select 'APAC' as region, 30 as total"}}
  group:
    type: logic.python
    config:
      inputs: [sales, more]
      outputs: [pair, sources]
      code: |
        pair = [sales, more]
        sources = {{"sales": sales, "by_kind": {{"more": [more, {{"again": sales}}]}}}}
  outside:
    type: logic.python
    config:
      inputs: [pair, sources]
      outputs: [found]
      use_environment: true
      code: |
        import pandas
        def seen(t):
            if isinstance(t, pandas.DataFrame):
                return ["pandas DataFrame", sorted(t["total"].tolist())]
            return [type(t).__name__, None]
        found = {{"list": [seen(t) for t in pair],
                 "dict": seen(sources["sales"]),
                 "list in dict in dict": seen(sources["by_kind"]["more"][0]),
                 "dict in list in dict in dict": seen(sources["by_kind"]["more"][1]["again"])}}
  save:
    type: logic.python
    config: {{inputs: [found], outputs: [], code: "import json; open('found.json', 'w', encoding='utf-8').write(json.dumps(found))"}}
edges:
  - sales.result -> group.sales
  - more.result -> group.more
  - group.pair -> outside.pair
  - group.sources -> outside.sources
  - outside.found -> save.found
""")
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", (result.stderr, block(env, run_id, "outside"))
    found = json.loads((env.root / "found.json").read_text(encoding="utf-8"))
    sales, more = ["pandas DataFrame", [10, 20]], ["pandas DataFrame", [30]]
    assert found == {"list": [sales, more], "dict": sales, "list in dict in dict": more,
                     "dict in list in dict in dict": sales}
