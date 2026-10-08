"""End-to-end behaviour of the runner through the real `taskloom` command."""

import datetime as dt
import shutil
import signal
import sys
import time

import polars as pl
import pytest

from conftest import REPO, events


def test_example_flow_runs_end_to_end(env):
    shutil.copytree(REPO / "examples", env.root / "examples")
    flow = env.root / "examples" / "daily-sales-report.yaml"

    assert env.taskloom("validate", flow).returncode == 0
    result = env.taskloom("run", flow, "--param", "day=2026-10-02")

    assert result.returncode == 0, result.stderr
    out = pl.read_csv(env.root / "examples" / "out" / "sales_2026-10-02.csv")
    assert out.to_dicts() == [{"day_key": 20261002, "region": "EU", "stores": 3, "total": 753.73}]
    run_id, status = env.last_run()
    assert status == "success"
    assert env.block_status(run_id) == {"sales": "success", "totals": "success", "save": "success", "no_data": "skipped"}


FLAKY_BLOCK = """
    from taskloom import Block, fields, ports

    class Flaky(Block):
        type_id = "test.flaky"
        outputs = {"value": ports.Any()}
        config = {"fail_times": fields.Int(), "counter": fields.Path()}

        def run(self, ctx):
            from pathlib import Path
            counter = Path(self.config.counter)
            n = int(counter.read_text()) if counter.exists() else 0
            counter.write_text(str(n + 1))
            if n < self.config.fail_times:
                raise ConnectionError(f"failure {n + 1}")
            return {"value": n + 1}
"""


def test_flaky_block_succeeds_after_retries_with_exponential_backoff(env):
    env.write("home/blocks/flaky.py", FLAKY_BLOCK)
    flow = env.write("flow.yaml", """
        blocks:
          q:
            type: test.flaky
            config: {fail_times: 2, counter: counter.txt}
            retry: {policy: exponential, initial: 300ms, factor: 2, max_attempts: 3}
    """)

    result = env.taskloom("run", flow, "--json-events")

    assert result.returncode == 0, result.stderr
    starts = [dt.datetime.fromisoformat(e["ts"]) for e in events(result.stdout) if e["event"] == "block_started"]
    assert len(starts) == 3
    gaps = [(b - a).total_seconds() for a, b in zip(starts, starts[1:])]
    assert 0.3 <= gaps[0] < 0.6 and 0.6 <= gaps[1] < 0.9, gaps
    run_id, status = env.last_run()
    assert status == "success"
    assert env.query("SELECT attempts, summary FROM blocks WHERE run_id=?", run_id) == [(3, "value: 3")]


def test_failure_goes_down_the_error_port_and_skips_dependents(env):
    flow = env.write("flow.yaml", """
        blocks:
          boom:
            type: logic.python
            config: {inputs: [], code: "raise RuntimeError('database is down')"}
          after_boom:
            type: logic.python
            config: {code: "output = input"}
          handler:
            type: logic.python
            config:
              inputs: [err]
              code: "open('handled.txt', 'w').write(err['error'])"
          independent:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
        edges:
          - boom.output -> after_boom.input
          - boom.error -> handler.err
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert (env.root / "handled.txt").read_text() == "RuntimeError: database is down"
    run_id, status = env.last_run()
    assert status == "success"
    assert env.block_status(run_id) == {
        "boom": "failed", "after_boom": "skipped", "handler": "success", "independent": "success"}


def test_unhandled_failure_fails_the_run(env):
    flow = env.write("flow.yaml", """
        blocks:
          boom:
            type: logic.python
            config: {inputs: [], code: "raise RuntimeError('database is down')"}
          after_boom:
            type: logic.python
            config: {code: "output = input"}
        edges:
          - boom.output -> after_boom.input
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 1
    run_id, status = env.last_run()
    assert status == "failed"
    assert env.block_status(run_id) == {"boom": "failed", "after_boom": "skipped"}


def test_user_block_overrides_builtin_and_broken_user_files_are_reported(env):
    env.write("home/blocks/my_wait.py", """
        from taskloom import Block, fields, ports

        class InstantWait(Block):
            type_id = "logic.wait"
            inputs = {"value": ports.Any(required=False)}
            outputs = {"value": ports.Any()}
            config = {"seconds": fields.Float()}

            def run(self, ctx, value=None):
                return {"value": "from user block"}
    """)
    env.write("home/blocks/broken.py", "this is not python")
    flow = env.write("flow.yaml", """
        blocks:
          wait:
            type: logic.wait
            config: {seconds: 60}
          save:
            type: logic.python
            config: {outputs: [], code: "open('out.txt', 'w').write(input)"}
        edges:
          - wait.value -> save.input
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert (env.root / "out.txt").read_text() == "from user block"
    assert "could not load user block file" in result.stderr and "broken.py" in result.stderr


def test_block_timeout_fails_the_attempt(env):
    flow = env.write("flow.yaml", """
        blocks:
          slow:
            type: logic.wait
            config: {seconds: 30}
            timeout: 500ms
    """)

    started = time.monotonic()
    result = env.taskloom("run", flow)

    assert result.returncode == 1
    assert time.monotonic() - started < 15
    run_id, _ = env.last_run()
    assert env.query("SELECT status, error FROM blocks WHERE run_id=?", run_id) == [
        ("failed", "TimeoutError: timed out after 0.5 s")]


@pytest.mark.skipif(sys.platform == "win32", reason="sends SIGINT")
def test_ctrl_c_cancels_the_run(env):
    flow = env.write("flow.yaml", """
        blocks:
          slow:
            type: logic.wait
            config: {seconds: 60}
          after:
            type: logic.python
            config: {code: "output = input"}
        edges:
          - slow.value -> after.input
    """)
    proc = env.popen("run", flow, "--json-events")
    for line in proc.stdout:
        if '"block_started"' in line:
            break
    started = time.monotonic()
    proc.send_signal(signal.SIGINT)

    assert proc.wait(timeout=15) == 130
    assert time.monotonic() - started < 5
    run_id, status = env.last_run()
    assert status == "cancelled"
    assert env.block_status(run_id) == {"slow": "cancelled", "after": "cancelled"}


def test_validate_reports_all_problems(env):
    flow = env.write("flow.yaml", """
        blocks:
          a:
            type: logic.python
            config: {code: "output = input"}
          b:
            type: logic.python
            config: {code: "output = input"}
          save:
            type: data.write_file
            config: {path: "out_{missing}.csv"}
        edges:
          - a.output -> b.input
          - b.output -> a.input
          - b.nope -> save.table
    """)

    result = env.taskloom("validate", flow)

    assert result.returncode == 2
    assert "unknown parameter {missing}" in result.stderr
    assert "'b' has no output 'nope'" in result.stderr
    assert env.taskloom("run", flow).returncode == 2


def test_date_parameters_bind_to_sql_as_date_and_integer_with_overrides(env):
    flow = env.write("flow.yaml", """
        params:
          day: {type: date, expr: "today() - bdays(1)"}
          day_key: {type: int, expr: "int(format(day, '%Y%m%d'))"}
        blocks:
          q:
            type: data.duckdb_sql
            config:
              inputs: []
              sql: "select :day as day, :day_key as day_key, :day_key + 1 as next_key"
          save:
            type: data.write_file
            config: {path: "out/{day:%Y%m%d}.parquet"}
        edges:
          - q.result -> save.table
    """)

    result = env.taskloom("run", flow, "--param", "day=2026-01-16")

    assert result.returncode == 0, result.stderr
    df = pl.read_parquet(env.root / "out" / "20260116.parquet")
    assert df.schema["day"] == pl.Date and df.schema["day_key"].is_integer()
    assert df.to_dicts() == [{"day": dt.date(2026, 1, 16), "day_key": 20260116, "next_key": 20260117}]


def test_cleaning_old_run_folders_never_touches_a_run_in_progress(env):
    env.write("home/settings.yaml", "keep_runs: 1\n")
    long_flow = env.write("long.yaml", """
        blocks:
          src:
            type: logic.python
            config: {inputs: [], code: "output = pl.DataFrame({'x': [1, 2, 3]})"}
          pause:
            type: logic.wait
            config: {seconds: 3}
          total:
            type: data.duckdb_sql
            config: {inputs: [t], sql: "select sum(x) as s from t"}
        edges:
          - src.output -> pause.value
          - pause.value -> total.t
    """)
    short_flow = env.write("short.yaml", """
        blocks:
          src:
            type: logic.python
            config: {inputs: [], code: "output = pl.DataFrame({'x': [1]})"}
    """)
    long_run = env.popen("run", long_flow, "--json-events")
    for line in long_run.stdout:
        if '"pause"' in line:
            break
    for _ in range(2):
        assert env.taskloom("run", short_flow).returncode == 0

    assert long_run.wait(timeout=30) == 0, long_run.stderr.read()
    assert env.query("SELECT status FROM runs WHERE id=1") == [("success",)]


def test_computed_previous_business_day_uses_the_flow_calendar(env):
    def previous_weekday(day):
        day -= dt.timedelta(days=1)
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)
        return day

    holiday = previous_weekday(dt.date.today())
    expected = previous_weekday(holiday)
    env.write("home/calendars/work.yaml", f"add: [{holiday}]\n")
    flow = env.write("flow.yaml", """
        calendar: work
        params:
          day: {type: date, expr: "today() - bdays(1)"}
          day_key: {type: int, expr: "int(format(day, '%Y%m%d'))"}
        blocks:
          q:
            type: data.duckdb_sql
            config: {inputs: [], sql: "select :day as day, :day_key as day_key"}
          save:
            type: data.write_file
            config: {path: out.parquet}
        edges:
          - q.result -> save.table
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert pl.read_parquet(env.root / "out.parquet").to_dicts() == [
        {"day": expected, "day_key": int(expected.strftime("%Y%m%d"))}]


def test_ask_user_without_anyone_to_ask_uses_the_default_or_fails_at_once(env):
    flow = env.write("flow.yaml", """
        blocks:
          ask:
            type: logic.ask_user
            config: {prompt: "Region?", default: EU}
          save:
            type: logic.python
            config: {outputs: [], code: "open('answer.txt', 'w').write(input)"}
        edges:
          - ask.answer -> save.input
    """)
    assert env.taskloom("run", flow).returncode == 0
    assert (env.root / "answer.txt").read_text() == "EU"

    no_default = env.write("no_default.yaml", """
        blocks:
          ask:
            type: logic.ask_user
            config: {prompt: "Region?"}
    """)
    started = time.monotonic()
    assert env.taskloom("run", no_default).returncode == 1
    assert time.monotonic() - started < 15
    run_id, _ = env.last_run()
    assert "set a default or a secret" in env.query("SELECT error FROM blocks WHERE run_id=?", run_id)[0][0]
