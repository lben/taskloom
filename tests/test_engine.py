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
    # Waits of 0.3 s then 0.6 s. Each gap also includes overhead that a busy CI machine can stretch
    # by a second, so only the minimums are checked: they show the wait grows.
    assert gaps[0] >= 0.3 and gaps[1] >= 0.6 and gaps[1] - gaps[0] >= 0.2, gaps
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
    assert (env.root / "handled.txt").read_text() == "RuntimeError: database is down (line 1)"
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


def test_for_each_runs_a_flow_once_per_item(env):
    env.write("per_region.yaml", """
        params:
          region: {type: text, value: ""}
        blocks:
          save:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open(f'done_{params[\\"region\\"]}.txt', 'w').write('ok')"}
    """)
    flow = env.write("flow.yaml", """
        blocks:
          regions:
            type: logic.python
            config: {inputs: [], code: "output = ['EU', 'US']"}
          each:
            type: logic.for_each
            config: {flow: per_region.yaml, param: region}
        edges:
          - regions.output -> each.items
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert (env.root / "done_EU.txt").exists() and (env.root / "done_US.txt").exists()
    assert [s for (s,) in env.query("SELECT status FROM runs ORDER BY id")] == ["success"] * 3


def test_copy_or_move_onto_itself_is_refused_and_keeps_the_file(env):
    (env.root / "data").mkdir()
    (env.root / "data" / "x.csv").write_text("a,b\n1,2\n")
    for block in ("files.copy", "files.move"):
        flow = env.write("flow.yaml", f"""
            blocks:
              same:
                type: {block}
                config: {{source: "data/x.csv", destination: "data/"}}
        """)
        assert env.taskloom("run", flow).returncode == 1
        assert (env.root / "data" / "x.csv").read_text() == "a,b\n1,2\n"
        run_id, _ = env.last_run()
        assert "onto itself" in env.query("SELECT error FROM blocks WHERE run_id=?", run_id)[0][0]


@pytest.mark.skipif(sys.platform == "win32", reason="sets the local time zone with TZ")
def test_as_of_is_local_time_converted_to_the_flow_time_zone(env):
    env.env["TZ"] = "America/New_York"
    flow = env.write("flow.yaml", """
        timezone: Asia/Tokyo
        params:
          day: {type: date, expr: "today()"}
        blocks:
          note:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
    """)
    # 20:00 in New York is 09:00 the next morning in Tokyo.
    result = env.taskloom("run", flow, "--as-of", "2026-10-08 20:00")

    assert result.returncode == 0, result.stderr
    assert env.query("SELECT params FROM runs") == [('{"day": "2026-10-09"}',)]


def test_paths_that_meet_run_the_shared_block_once_either_way(env):
    flow = env.write("flow.yaml", """
        params:
          fresh: {type: bool, value: true}
        blocks:
          check:
            type: logic.if
            config: {condition: "fresh"}
          ask:
            type: logic.python
            config: {inputs: [input], code: "output = input"}
          install:
            type: files.copy
            config: {source: a.txt, destination: b.txt}
        edges:
          - check.true -> install.after
          - check.false -> ask.input
          - ask.output -> install.after
    """)
    (env.root / "a.txt").write_text("x")
    for fresh in ("true", "false"):
        (env.root / "b.txt").unlink(missing_ok=True)
        assert env.taskloom("run", flow, "--param", f"fresh={fresh}").returncode == 0
        assert (env.root / "b.txt").read_text() == "x"
        run_id, _ = env.last_run()
        assert env.block_status(run_id)["install"] == "success"


def test_a_failed_prerequisite_still_stops_a_block_whose_paths_meet(env):
    (env.root / "important.txt").write_text("keep me")
    flow = env.write("flow.yaml", """
        blocks:
          backup:
            type: logic.python
            config: {inputs: [], code: "raise RuntimeError('backup failed')"}
          check:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
          after_backup:
            type: logic.python
            config: {code: "output = input"}
          cleanup:
            type: files.delete
            config: {path: important.txt}
        edges:
          - backup.output -> after_backup.input
          - after_backup.output -> cleanup.after
          - check.output -> cleanup.after
    """)

    assert env.taskloom("run", flow).returncode == 1
    assert (env.root / "important.txt").read_text() == "keep me"
    run_id, _ = env.last_run()
    assert env.block_status(run_id)["cleanup"] == "skipped"


def test_error_handler_of_a_block_skipped_by_a_failure_does_not_open_a_path(env):
    (env.root / "important.txt").write_text("keep me")
    flow = env.write("flow.yaml", """
        blocks:
          backup:
            type: logic.python
            config: {inputs: [], code: "raise RuntimeError('backup failed')"}
          verify:
            type: logic.python
            config: {code: "output = input"}
          on_verify_error:
            type: logic.python
            config: {code: "output = input"}
          check:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
          cleanup:
            type: files.delete
            config: {path: important.txt}
        edges:
          - backup.output -> verify.input
          - verify.error -> on_verify_error.input
          - on_verify_error.output -> cleanup.after
          - check.output -> cleanup.after
    """)

    assert env.taskloom("run", flow).returncode == 1
    assert (env.root / "important.txt").read_text() == "keep me"
    run_id, _ = env.last_run()
    assert env.block_status(run_id)["cleanup"] == "skipped"


def test_a_failing_python_block_reports_its_name_inputs_code_and_variables(env):
    flow = env.write("flow.yaml", """
        blocks:
          totals:
            type: logic.python
            config: {inputs: [], code: "output = {'EU': 120, 'US': 0}"}
          ratio:
            type: logic.python
            config:
              inputs: [sales]
              code: |
                def share(total):
                    return total / sales["US"]
                result = share(sales["EU"])
        edges:
          - totals.output -> ratio.sales
    """)
    assert env.taskloom("run", flow).returncode == 1
    run_id, _ = env.last_run()
    [report] = [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND level='ERROR'", run_id)]
    assert report.startswith("ratio (Python Code) failed: ZeroDivisionError: division by zero (line 2)")
    assert 'sales ← totals.output: { "EU": 120, "US": 0 }' in report
    assert '❱ 2 │   return total / sales["US"]' in report  # the code around the failing line, marked
    assert "Variables in share():" in report and "total = 120" in report


def test_old_runs_are_forgotten_after_history_days(env):
    from taskloom.history import History

    env.write("home/settings.yaml", "history_days: 30\n")
    flow = env.write("flow.yaml", """
        blocks:
          b:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)
    assert env.taskloom("run", flow).returncode == 0
    env.query("UPDATE runs SET started='2020-01-01T00:00:00'")  # make it old
    history = History(env.home / "history.db")
    old_id = history.runs()[0]["id"]
    history.close()

    assert env.taskloom("run", flow).returncode == 0

    assert [r for (r,) in env.query("SELECT id FROM runs")] == [old_id + 1]
    assert env.query("SELECT count(*) FROM blocks WHERE run_id=?", old_id) == [(0,)]
    assert env.query("SELECT count(*) FROM logs WHERE run_id=?", old_id) == [(0,)]


def test_rerun_one_block_with_the_inputs_of_an_earlier_run(env):
    flow = env.write("flow.yaml", """
        params:
          region: {type: text, value: EU}
        blocks:
          load:
            type: data.duckdb_sql
            config: {inputs: [], sql: "select :region as region, 5 as n"}
          settings:
            type: logic.python
            config: {inputs: [], code: "open('loads.txt', 'a').write('x'); output = {'factor': 2}"}
          compute:
            type: logic.python
            config:
              inputs: [table, settings]
              outputs: []
              code: |
                import os
                if not os.path.exists('fixed.txt'):
                    raise RuntimeError('bug')
                n = table.scan().collect()['n'][0]
                open('result.txt', 'w').write(f"{params['region']} {n * settings['factor']}")
        edges:
          - load.result -> compute.table
          - settings.output -> compute.settings
    """)
    assert env.taskloom("run", flow, "--param", "region=US").returncode == 1
    failed_run, _ = env.last_run()

    (env.root / "fixed.txt").write_text("the fix")
    result = env.taskloom("run", flow, "--only", "compute", "--inputs-from", failed_run)

    assert result.returncode == 0, result.stderr
    assert (env.root / "result.txt").read_text() == "US 10"  # same inputs and parameters as that run
    assert (env.root / "loads.txt").read_text() == "x"  # the other blocks did not run again
    run_id, status = env.last_run()
    assert status == "success" and env.block_status(run_id) == {"compute": "success"}

    # Retrying again, from that retry (what the editor does: it shows the latest run).
    (env.root / "result.txt").unlink()
    again = env.taskloom("run", flow, "--only", "compute", "--inputs-from", run_id)
    assert again.returncode == 0, again.stderr
    assert (env.root / "result.txt").read_text() == "US 10" and (env.root / "loads.txt").read_text() == "x"


def test_rerun_accepts_inputs_that_had_nothing_in_that_run(env):
    flow = env.write("flow.yaml", """
        blocks:
          check:
            type: logic.if
            config: {condition: "True"}
          boom:
            type: logic.python
            config: {inputs: [], code: "raise RuntimeError('down')"}
          fine:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
          handler:
            type: logic.python
            config:
              inputs: [value, errors]
              outputs: []
              code: "open('handled.txt', 'w').write(f'{value} {[e[\\"block\\"] for e in errors]}')"
        edges:
          - check.true -> handler.value
          - check.false -> handler.value
          - boom.error -> handler.errors
          - fine.error -> handler.errors
    """)
    assert env.taskloom("run", flow).returncode == 0
    run_id, _ = env.last_run()
    assert (env.root / "handled.txt").read_text() == "[None] ['boom']"
    (env.root / "handled.txt").unlink()

    result = env.taskloom("run", flow, "--only", "handler", "--inputs-from", run_id)

    assert result.returncode == 0, result.stderr
    assert (env.root / "handled.txt").read_text() == "[None] ['boom']"


def test_rerun_refuses_inputs_that_held_a_password(env):
    env.write("home/secrets.yaml", "pw: hunter2SECRET\n").chmod(0o600)
    flow = env.write("flow.yaml", """
        blocks:
          ask:
            type: logic.ask_user
            config: {prompt: "Password?", kind: password, secret: pw}
          use:
            type: logic.python
            config: {inputs: [pw], outputs: [], code: "pass"}
        edges:
          - ask.answer -> use.pw
    """)
    assert env.taskloom("run", flow).returncode == 0
    run_id, _ = env.last_run()

    result = env.taskloom("run", flow, "--only", "use", "--inputs-from", run_id)

    assert result.returncode == 2 and "held a password" in result.stderr
    for path in (env.home / "runs").rglob("*"):
        if path.is_file():
            assert b"hunter2SECRET" not in path.read_bytes(), path


def test_retries_of_different_blocks_chain_and_survive_cleanup(env):
    env.write("home/settings.yaml", "keep_runs: 2\n")
    flow = env.write("flow.yaml", """
        blocks:
          load:
            type: data.duckdb_sql
            config: {inputs: [], sql: "select 5 as n"}
          a:
            type: logic.python
            config:
              inputs: [table]
              outputs: []
              code: |
                import os
                assert os.path.exists('fix_a.txt'), 'a is broken'
                open('a.txt', 'w').write(str(table.scan().collect()['n'][0]))
          other:
            type: logic.python
            config: {inputs: [], code: "output = 'x'"}
          b:
            type: logic.python
            config:
              inputs: [value]
              outputs: []
              code: |
                import os
                assert os.path.exists('fix_b.txt'), 'b is broken'
                open('b.txt', 'w').write(value)
        edges:
          - load.result -> a.table
          - other.output -> b.value
    """)
    assert env.taskloom("run", flow).returncode == 1
    run_id, _ = env.last_run()

    (env.root / "fix_a.txt").write_text("")
    assert env.taskloom("run", flow, "--only", "a", "--inputs-from", run_id).returncode == 0
    run_id, _ = env.last_run()
    (env.root / "fix_b.txt").write_text("")
    result = env.taskloom("run", flow, "--only", "b", "--inputs-from", run_id)  # from the retry of a

    assert result.returncode == 0, result.stderr
    assert (env.root / "b.txt").read_text() == "x"
    for _ in range(3):  # more retries than keep_runs: older run folders get deleted meanwhile
        run_id, _ = env.last_run()
        result = env.taskloom("run", flow, "--only", "a", "--inputs-from", run_id)
        assert result.returncode == 0, result.stderr
    assert (env.root / "a.txt").read_text() == "5"


def test_retrying_a_block_that_got_nothing_is_refused(env):
    flow = env.write("flow.yaml", """
        blocks:
          check:
            type: logic.if
            config: {condition: "False"}
          only_if_true:
            type: logic.python
            config: {inputs: [value], outputs: [], code: "pass"}
        edges:
          - check.true -> only_if_true.value
    """)
    assert env.taskloom("run", flow).returncode == 0
    run_id, _ = env.last_run()

    result = env.taskloom("run", flow, "--only", "only_if_true", "--inputs-from", run_id)

    assert result.returncode == 2 and "only_if_true did not run in run #" in result.stderr


def test_retrying_a_block_skipped_after_a_failure_is_refused(env):
    flow = env.write("flow.yaml", """
        blocks:
          fine:
            type: logic.python
            config: {code: "pass"}
          boom:
            type: logic.python
            config: {code: "raise ValueError('boom')"}
          later:
            type: logic.python
            config: {code: "open('later.txt', 'w').close()"}
          handler:
            type: logic.python
            config: {inputs: [e], code: "pass"}
        edges:
          - fine.done -> later.after
          - boom.done -> later.after
          - boom.error -> handler.e
    """)
    env.taskloom("run", flow)
    run_id, _ = env.last_run()
    assert env.block_status(run_id)["later"] == "skipped"

    result = env.taskloom("run", flow, "--only", "later", "--inputs-from", run_id)

    assert result.returncode == 2 and "later did not run in run #" in result.stderr
    assert not (env.root / "later.txt").exists()


def test_retrying_the_only_block_of_a_flow(env):
    flow = env.write("flow.yaml", """
        blocks:
          only1:
            type: logic.python
            config: {code: "open('ran.txt', 'a').write('x')"}
    """)
    assert env.taskloom("run", flow).returncode == 0
    run_id, _ = env.last_run()

    result = env.taskloom("run", flow, "--only", "only1", "--inputs-from", run_id)

    assert result.returncode == 0, result.stderr
    assert (env.root / "ran.txt").read_text() == "xx"
    assert env.query("SELECT count(*) FROM runs WHERE status = 'running'") == [(0,)]


def test_print_in_a_python_block_goes_to_its_log(env):
    flow = env.write("flow.yaml", """
        blocks:
          talk:
            type: logic.python
            config: {inputs: [], outputs: [], code: "print('rows:', 42, end='')\\nlog('also this')"}
    """)
    result = env.taskloom("run", flow, "--json-events")
    run_id, status = env.last_run()

    assert status == "success", result.stderr
    logs = [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block='talk'", run_id)]
    assert "rows: 42" in logs and "also this" in logs
    assert all(line.startswith("{") for line in result.stdout.splitlines())  # nothing mixed into the events
