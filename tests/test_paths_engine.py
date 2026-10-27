"""The engine through its real paths: `taskloom run` and `taskloom validate` on real flow files, the run history,
the run folders and the channel the editor runs flows through (--interactive). Order and connections, error
outputs and handlers, retries, timeouts, cancellation, parameters and calendars, secrets, retention, re-runs,
run statuses and exit codes."""

import datetime as dt
import json
import signal
import sys
import textwrap
import threading
import time

import pytest

from conftest import REPO, events


def write(env, text: str, name: str = "flow.yaml"):
    """A file under the test folder (e.g. a flow, or home/calendars/x.yaml), written as Taskloom reads it: UTF-8."""
    path = env.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def logs(env, run_id: int, block: str, level: str | None = None) -> list[str]:
    sql = "SELECT message FROM logs WHERE run_id=? AND block=?" + (" AND level=?" if level else "") + " ORDER BY rowid"
    return [m for (m,) in env.query(sql, run_id, block, *([level] if level else []))]


def runs_recorded(env) -> int:
    return env.query("SELECT count(*) FROM runs")[0][0] if (env.home / "history.db").exists() else 0


def attempt_starts(stdout: str, block: str) -> list[dt.datetime]:
    return [dt.datetime.fromisoformat(e["ts"]) for e in events(stdout)
            if e["event"] == "block_started" and e["block"] == block]


class Running:
    """`taskloom run …` in the background. Its output is read by threads as it comes, so every wait on it has a
    deadline; the process is killed if it is still going when the test ends."""

    def __init__(self, env, *args):
        self.proc = env.popen("run", *args)
        self.out: list[str] = []
        self.err: list[str] = []
        for stream, lines in ((self.proc.stdout, self.out), (self.proc.stderr, self.err)):
            threading.Thread(target=self._read, args=(stream, lines), daemon=True).start()

    @staticmethod
    def _read(stream, lines):
        for line in stream:
            lines.append(line)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=30)

    def wait_for(self, text: str, timeout: float = 60):
        deadline = time.monotonic() + timeout
        while not any(text in line for line in list(self.out)):
            if time.monotonic() > deadline or self.proc.poll() is not None:
                time.sleep(0.5)  # the last lines of a process that just ended
                if any(text in line for line in list(self.out)):
                    return
                raise AssertionError(f"{text!r} never came: {''.join(self.out)[-2000:]} {''.join(self.err)[-2000:]}")
            time.sleep(0.02)

    def send(self, message: dict):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def finish(self, timeout: float = 30) -> int:
        code = self.proc.wait(timeout=timeout)
        time.sleep(0.2)  # let the readers take the last lines
        return code


# A user block (README: "Writing a block") that fails until the attempt it is told to succeed on.
ATTEMPTS_BLOCK = """
    from taskloom import Block, fields, ports


    class Attempts(Block):
        type_id = "test.attempts"
        description = "Fails until the attempt given, then gives the attempt number."
        outputs = {"value": ports.Any()}
        config = {"succeed_on": fields.Int(default=1)}

        def run(self, ctx):
            if ctx.attempt < self.config.succeed_on:
                raise ConnectionError(f"failed attempt {ctx.attempt}")
            return {"value": ctx.attempt}
"""


# --- order and connections ------------------------------------------------------------------------------------

def test_blocks_run_after_the_blocks_they_need_whatever_their_order_in_the_file(env):
    flow = write(env, """
        blocks:
          report:
            type: logic.python
            config: {inputs: [total], outputs: [], code: "open('order.txt', 'a').write('report ')"}
          total:
            type: logic.python
            config: {inputs: [rows], code: "open('order.txt', 'a').write('total '); output = sum(rows)"}
          first:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('order.txt', 'a').write('first ')"}
          load:
            type: logic.python
            config: {inputs: [], code: "open('order.txt', 'a').write('load '); output = [1, 2, 3]"}
          last:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('order.txt', 'a').write('last ')"}
        edges:
          - total.output -> report.total
          - load.output -> total.rows
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    order = (env.root / "order.txt").read_text().split()
    assert sorted(order) == ["first", "last", "load", "report", "total"]  # each ran once
    assert order.index("load") < order.index("total") < order.index("report")


def test_several_connections_into_one_input_arrive_as_a_list_in_the_order_of_the_connections(env):
    flow = write(env, """
        blocks:
          a:
            type: logic.python
            config: {inputs: [], code: "output = 'a'"}
          b:
            type: logic.python
            config: {inputs: [], code: "output = 'b'"}
          c:
            type: logic.python
            config: {inputs: [], code: "output = 'c'"}
          join:
            type: logic.python
            config: {inputs: [items], outputs: [], code: "open('joined.txt', 'w').write(','.join(items))"}
        edges:
          - b.output -> join.items
          - c.output -> join.items
          - a.output -> join.items
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert (env.root / "joined.txt").read_text() == "b,c,a"


@pytest.mark.parametrize("fail, ran, skipped", [(True, "on_failure", "on_success"), (False, "on_success", "on_failure")])
def test_a_block_connected_to_an_error_output_runs_only_when_that_block_fails(env, fail, ran, skipped):
    flow = write(env, """
        params:
          fail: {type: bool, value: false}
        blocks:
          work:
            type: logic.python
            config: {inputs: [], code: "assert not params['fail'], 'broken'\\noutput = 1"}
          on_failure:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('on_failure.txt', 'w').write('x')"}
          on_success:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('on_success.txt', 'w').write('x')"}
        edges:
          - work.error -> on_failure.after
          - work.done -> on_success.after
    """)

    result = env.taskloom("run", flow, "--param", f"fail={fail}")

    assert result.returncode == 0, result.stderr  # a failure something handles does not fail the run
    run_id, status = env.last_run()
    assert status == "success"
    assert env.block_status(run_id) == {"work": "failed" if fail else "success", ran: "success", skipped: "skipped"}
    assert (env.root / f"{ran}.txt").exists() and not (env.root / f"{skipped}.txt").exists()


# --- error outputs, retries and timeouts ------------------------------------------------------------------------

def test_an_error_handler_runs_once_after_the_last_attempt_and_gets_the_block_its_error_and_the_attempts(env):
    flow = write(env, """
        blocks:
          fetch:
            type: logic.python
            config: {inputs: [], code: "raise ConnectionError('server busy')"}
            retry: {policy: fixed, delay: 0, max_attempts: 3}
          handler:
            type: logic.python
            config:
              inputs: [problem]
              outputs: []
              code: |
                import json
                with open('problems.jsonl', 'a') as f:
                    f.write(json.dumps({k: problem[k] for k in ('block', 'error', 'attempts')}) + '\\n')
        edges:
          - fetch.error -> handler.problem
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    lines = (env.root / "problems.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == [
        {"block": "fetch", "error": "ConnectionError: server busy (line 1)", "attempts": 3}]
    run_id, status = env.last_run()
    assert status == "success"
    assert env.query("SELECT block, status, attempts FROM blocks WHERE run_id=? ORDER BY block", run_id) == [
        ("fetch", "failed", 3), ("handler", "success", 1)]
    assert len(logs(env, run_id, "fetch", "RETRY")) == 2  # each retry is logged


def test_an_error_handler_that_fails_itself_fails_the_run(env):
    flow = write(env, """
        blocks:
          fetch:
            type: logic.python
            config: {inputs: [], code: "raise ConnectionError('server busy')"}
          handler:
            type: logic.python
            config: {inputs: [problem], outputs: [], code: "raise RuntimeError('the handler is broken too')"}
        edges:
          - fetch.error -> handler.problem
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 1
    run_id, status = env.last_run()
    assert status == "failed"
    assert env.block_status(run_id) == {"fetch": "failed", "handler": "failed"}


def test_a_fixed_retry_waits_its_delay_before_each_new_attempt_and_logs_each_failure(env):
    env.write("home/blocks/attempts.py", ATTEMPTS_BLOCK)
    flow = write(env, """
        blocks:
          fetch:
            type: test.attempts
            config: {succeed_on: 3}
            retry: {policy: fixed, delay: 300ms, max_attempts: 3}
    """)

    result = env.taskloom("run", flow, "--json-events")

    assert result.returncode == 0, result.stderr
    starts = attempt_starts(result.stdout, "fetch")
    gaps = [(b - a).total_seconds() for a, b in zip(starts, starts[1:])]
    assert len(starts) == 3 and all(gap >= 0.3 - 0.02 for gap in gaps), gaps  # a clock tick of tolerance
    run_id, _ = env.last_run()
    assert env.query("SELECT status, attempts, summary FROM blocks WHERE run_id=?", run_id) == [("success", 3, "value: 3")]
    assert len(logs(env, run_id, "fetch", "ERROR")) == 2 and len(logs(env, run_id, "fetch", "RETRY")) == 2


def test_an_exponential_retry_never_waits_longer_than_its_max_delay(env):
    env.write("home/blocks/attempts.py", ATTEMPTS_BLOCK)
    flow = write(env, """
        blocks:
          fetch:
            type: test.attempts
            config: {succeed_on: 3}
            retry: {policy: exponential, initial: 200ms, factor: 100, max_delay: 300ms, max_attempts: 3}
    """)

    result = env.taskloom("run", flow, "--json-events")

    assert result.returncode == 0, result.stderr
    starts = attempt_starts(result.stdout, "fetch")
    gaps = [(b - a).total_seconds() for a, b in zip(starts, starts[1:])]
    # 0.2 s, then 0.2 * 100 = 20 s held to max_delay: 0.3 s.
    assert len(gaps) == 2 and gaps[0] >= 0.2 - 0.02 and 0.3 - 0.02 <= gaps[1] < 10, gaps


@pytest.mark.parametrize("retry, attempts", [
    (None, 1),
    ("{policy: fixed, max_attempts: 2}", 2),
    ("{policy: exponential, initial: 10ms, jitter: 0.5}", 3),  # 3 attempts unless max_attempts says otherwise
], ids=["no retry", "fixed", "exponential, default attempts"])
def test_a_block_that_keeps_failing_is_tried_max_attempts_times_and_fails_with_its_last_error(env, retry, attempts):
    env.write("home/blocks/attempts.py", ATTEMPTS_BLOCK)
    flow = write(env, f"""
        blocks:
          fetch:
            type: test.attempts
            config: {{succeed_on: 99}}
            {f"retry: {retry}" if retry else ""}
          after:
            type: logic.python
            config: {{outputs: [], code: "open('after.txt', 'w').write('x')"}}
        edges:
          - fetch.value -> after.input
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 1
    run_id, status = env.last_run()
    assert status == "failed"
    assert env.query("SELECT block, status, attempts, error FROM blocks WHERE run_id=? ORDER BY block", run_id) == [
        ("after", "skipped", 0, None), ("fetch", "failed", attempts, f"ConnectionError: failed attempt {attempts}")]
    assert not (env.root / "after.txt").exists()


@pytest.mark.parametrize("timeout", ["300ms", "0.3s", "0.3"])
def test_a_timeout_stops_each_attempt_and_the_error_says_so(env, timeout):
    flow = write(env, f"""
        blocks:
          slow:
            type: logic.wait
            config: {{seconds: 30}}
            timeout: {timeout}
            retry: {{policy: fixed, max_attempts: 2}}
          quick:
            type: logic.wait
            config: {{seconds: 0.1}}
            timeout: 1m
          handler:
            type: logic.python
            config:
              inputs: [problem]
              outputs: []
              code: "open('problem.txt', 'w').write(f\\"{{problem['error']}} ({{problem['attempts']}} attempts)\\")"
        edges:
          - slow.error -> handler.problem
    """)

    result = env.taskloom("run", flow, "--json-events")

    assert result.returncode == 0, result.stderr
    stamps = [dt.datetime.fromisoformat(e["ts"]) for e in events(result.stdout)]
    assert (stamps[-1] - stamps[0]).total_seconds() < 15  # not the 2 x 30 s the block would wait
    assert (env.root / "problem.txt").read_text() == "TimeoutError: timed out after 0.3 s (2 attempts)"
    run_id, _ = env.last_run()
    assert env.block_status(run_id) == {"slow": "failed", "quick": "success", "handler": "success"}


# --- cancellation -----------------------------------------------------------------------------------------------

def test_stop_during_the_wait_before_a_retry_cancels_the_run_at_once(env):
    flow = write(env, """
        blocks:
          fetch:
            type: logic.python
            config: {inputs: [], code: "raise ConnectionError('server busy')"}
            retry: {policy: fixed, delay: 60s, max_attempts: 3}
          save:
            type: logic.python
            config: {outputs: [], code: "open('saved.txt', 'w').write('x')"}
        edges:
          - fetch.output -> save.input
    """)
    with Running(env, flow, "--json-events", "--interactive") as run:  # how the editor runs a flow
        run.wait_for('"RETRY"')
        stopped = time.monotonic()
        run.send({"cancel": True})  # what the editor's Stop sends

        assert run.finish() == 130
        assert time.monotonic() - stopped < 30  # not the 60 s wait
    run_id, status = env.last_run()
    assert status == "cancelled"
    assert env.block_status(run_id) == {"fetch": "cancelled", "save": "cancelled"}
    assert not (env.root / "saved.txt").exists()


def test_a_run_whose_editor_goes_away_is_cancelled(env):
    flow = write(env, """
        blocks:
          slow:
            type: logic.wait
            config: {seconds: 60}
    """)
    with Running(env, flow, "--json-events", "--interactive") as run:
        run.wait_for('"block_started"')
        run.proc.stdin.close()  # the editor's end of the channel closes

        assert run.finish() == 130
    run_id, status = env.last_run()
    assert status == "cancelled" and env.block_status(run_id) == {"slow": "cancelled"}


@pytest.mark.skipif(sys.platform == "win32", reason="sends SIGINT")
def test_pressing_ctrl_c_twice_forces_the_run_to_stop_and_leaves_it_recorded_as_cancelled(env):
    flow = write(env, """
        blocks:
          busy:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                import time
                for _ in range(600):
                    time.sleep(0.1)  # busy, and not checking whether the run was stopped
    """)
    with Running(env, flow, "--json-events") as run:
        run.wait_for('"block_started"')
        run.proc.send_signal(signal.SIGINT)
        time.sleep(0.3)
        run.proc.send_signal(signal.SIGINT)  # "cancelling… (press Ctrl+C again to force)"
        run.finish(timeout=15)  # not the 60 s the block would keep going
    run_id, status = env.last_run()
    problems = []
    if status != "cancelled":
        problems.append(f"the run is recorded {status}")
    if env.block_status(run_id) != {"busy": "cancelled"}:
        problems.append(f"its block is recorded {env.block_status(run_id)}")
    if "Traceback" in "".join(run.err):
        problems.append("a traceback was printed: " + "".join(run.err)[-300:])
    assert problems == []


# --- parameters and calendars -----------------------------------------------------------------------------------

WRITE_PARAMS = """
    blocks:
      show:
        type: logic.python
        config:
          inputs: []
          outputs: []
          code: |
            import json
            with open('got.json', 'w', encoding='utf-8') as f:
                json.dump({k: [type(v).__name__, str(v)] for k, v in params.items()}, f, ensure_ascii=False)
"""


def with_params(params: str) -> str:
    """A flow with these parameters whose one block writes each parameter's type and value into got.json."""
    return "params:\n" + textwrap.indent(textwrap.dedent(params).strip("\n"), "  ") + "\n" + textwrap.dedent(WRITE_PARAMS)


@pytest.mark.parametrize("params, args", [
    ("""
          t: {type: text, value: hello}
          n: {type: int, value: 42}
          f: {type: float, value: 2.5}
          b: {type: bool, value: true}
          d: {type: date, value: 2026-01-16}
          ts: {type: datetime, value: 2026-01-16 08:30:00}
          l: {type: list, value: [EU, US]}""", []),
    ("""
          t: {type: text, value: 'hello'}
          n: {type: int, value: '42'}
          f: {type: float, value: '2.5'}
          b: {type: bool, value: 'true'}
          d: {type: date, value: '2026-01-16'}
          ts: {type: datetime, value: '2026-01-16 08:30'}
          l: {type: list, value: 'EU, US'}""", []),
    ("""
          t: {type: text, value: x}
          n: {type: int, value: 0}
          f: {type: float, value: 0}
          b: {type: bool, value: false}
          d: {type: date, value: 2000-01-01}
          ts: {type: datetime, value: '2000-01-01 00:00'}
          l: {type: list, value: []}""",
     ["t=hello", "n=42", "f=2.5", "b=yes", "d=2026-01-16", "ts=2026-01-16 08:30", "l=EU, US"]),
], ids=["values", "values as text, as the editor saves them", "--param"])
def test_every_parameter_type_reaches_the_blocks_as_that_type_and_is_saved_with_the_run(env, params, args):
    flow = write(env, with_params(params))

    result = env.taskloom("run", flow, *[a for arg in args for a in ("--param", arg)])

    assert result.returncode == 0, result.stderr
    assert json.loads((env.root / "got.json").read_text(encoding="utf-8")) == {
        "t": ["str", "hello"], "n": ["int", "42"], "f": ["float", "2.5"], "b": ["bool", "True"],
        "d": ["date", "2026-01-16"], "ts": ["datetime", "2026-01-16 08:30:00"], "l": ["list", "['EU', 'US']"]}
    assert json.loads(env.query("SELECT params FROM runs")[0][0]) == {
        "t": "hello", "n": 42, "f": 2.5, "b": True, "d": "2026-01-16", "ts": "2026-01-16 08:30:00", "l": ["EU", "US"]}


def test_text_and_list_parameters_with_unusual_characters_reach_the_blocks_intact(env):
    flow = write(env, with_params("""
        note: {type: text, value: ''}
        places: {type: list, value: []}
    """))
    note = "Zürich «quoted» 'single' \"double\" a=b {braces} 🚀"

    result = env.taskloom("run", flow, "--param", f"note={note}", "--param", "places=São Paulo, 東京 ,a=b")

    assert result.returncode == 0, result.stderr
    assert json.loads((env.root / "got.json").read_text(encoding="utf-8")) == {
        "note": ["str", note], "places": ["list", "['São Paulo', '東京', 'a=b']"]}
    assert json.loads(env.query("SELECT params FROM runs")[0][0]) == {"note": note, "places": ["São Paulo", "東京", "a=b"]}


def test_a_parameter_left_empty_in_the_flow_file_runs_empty_as_the_parameters_tab_shows_it(env, win):
    flow = write(env, with_params("""
        note: {type: text, value:}
        tags: {type: list, value:}
    """))
    win.open_flow(flow)
    table = win.parameters.table
    assert {table.item(r, 0).text(): (table.item(r, 3).text(), table.item(r, 4).text())
            for r in range(table.rowCount())} == {"note": ("", ""), "tags": ("", "")}  # value and preview: empty

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert json.loads((env.root / "got.json").read_text(encoding="utf-8")) == {"note": ["str", ""], "tags": ["list", "[]"]}


@pytest.mark.parametrize("args, problem", [
    (["--param", "n=abc"], "parameter 'n'"),
    (["--param", "f=ten"], "parameter 'f'"),
    (["--param", "flag=maybe"], "parameter 'flag'"),
    (["--param", "day=2026-02-30"], "parameter 'day'"),
    (["--param", "when=noon"], "parameter 'when'"),
    (["--param", "nope=1"], "unknown parameter(s): nope"),
    (["--param", "day"], "name=value"),
    (["--as-of", "tomorrow"], "tomorrow"),
])
def test_a_wrong_parameter_value_or_as_of_is_refused_and_nothing_runs(env, args, problem):
    flow = write(env, """
        params:
          n: {type: int, value: 1}
          f: {type: float, value: 1.5}
          flag: {type: bool, value: true}
          day: {type: date, expr: "today()"}
          when: {type: datetime, expr: "now()"}
        blocks:
          a:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('ran.txt', 'w').write('x')"}
    """)

    result = env.taskloom("run", flow, *args)

    assert result.returncode == 2 and problem in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert runs_recorded(env) == 0 and not (env.root / "ran.txt").exists()


def test_parameter_expressions_use_earlier_parameters_and_every_date_function_as_of_the_time_given(env):
    flow = write(env, """
        params:
          day: {type: date, expr: "today()"}
          at: {type: datetime, expr: "now()"}
          plus_days: {type: date, expr: "today() + days(3)"}
          minus_week: {type: date, expr: "today() - weeks(1)"}
          next_month: {type: date, expr: "today() + months(1)"}
          last_year: {type: date, expr: "today() - months(12)"}
          month_start: {type: date, expr: "start_of_month(today())"}
          month_end: {type: date, expr: "end_of_month(next_month)"}
          week_start: {type: date, expr: "start_of_week(today())"}
          prev_bday: {type: date, expr: "today() - bdays(1)"}
          next_bday: {type: date, expr: "next_business_day(today())"}
          before_monday: {type: date, expr: "previous_business_day(parse_date('2026-02-02'))"}
          open_today: {type: bool, expr: "is_business_day(today())"}
          between: {type: int, expr: "business_days_between(parse_date('2026-01-26'), parse_date('2026-02-02'))"}
          between_back: {type: int, expr: "business_days_between(parse_date('2026-02-02'), parse_date('2026-01-26'))"}
          key: {type: int, expr: "int(format(prev_bday, '%Y%m%d'))"}
          parsed: {type: date, expr: "parse_date('31/01/2026', '%d/%m/%Y')"}
          arithmetic: {type: int, expr: "max(1, min(5, abs(-3))) + len('ab') + round(2.6)"}
          text: {type: text, expr: "str(int('7') * 2) + 'x'"}
          ratio: {type: float, expr: "float('1.5') * 2"}
          which: {type: text, expr: "'weekend' if not open_today else 'weekday'"}
          pick: {type: int, expr: "[10, 20, 30][1] + {'a': 1}['a']"}
          regions: {type: list, expr: "['EU', 'US'] if open_today else ['ALL']"}
        blocks:
          a:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
    """)

    result = env.taskloom("run", flow, "--as-of", "2026-01-31 18:45")  # a Saturday

    assert result.returncode == 0, result.stderr
    assert json.loads(env.query("SELECT params FROM runs")[0][0]) == {
        "day": "2026-01-31", "at": "2026-01-31 18:45:00", "plus_days": "2026-02-03", "minus_week": "2026-01-24",
        "next_month": "2026-02-28", "last_year": "2025-01-31", "month_start": "2026-01-01", "month_end": "2026-02-28",
        "week_start": "2026-01-26", "prev_bday": "2026-01-30", "next_bday": "2026-02-02", "before_monday": "2026-01-30",
        "open_today": False, "between": 5, "between_back": -5, "key": 20260130, "parsed": "2026-01-31",
        "arithmetic": 8, "text": "14x", "ratio": 3.0, "which": "weekend", "pick": 21, "regions": ["ALL"]}


@pytest.mark.parametrize("expr, problem", [
    ("yesterday()", "unknown function 'yesterday'"),
    ("later + days(1)", "unknown name 'later'"),  # a parameter declared after it
    ("today() -", "invalid expression"),
    ("today().year", "unsupported syntax"),
    ("today() + 1", "unsupported operand"),
    ("today() - days(1 / 0)", "division by zero"),
    ("today() - bdays(1, cal='Atlantis')", "unknown holiday calendar 'Atlantis'"),
    ("today() - bdays(1, cal='US-XX')", "unknown holiday calendar 'US-XX'"),  # a region the country does not have
    ("'not a date'", "not a date"),
])
def test_a_parameter_expression_that_cannot_be_worked_out_is_refused_naming_the_parameter(env, expr, problem):
    flow = write(env, f"""
        params:
          day: {{type: date, expr: {json.dumps(expr)}}}
          later: {{type: date, value: 2026-01-01}}
        blocks:
          a:
            type: logic.python
            config: {{inputs: [], outputs: [], code: "open('ran.txt', 'w').write('x')"}}
    """)

    checked = env.taskloom("validate", flow)
    result = env.taskloom("run", flow)

    for output in (checked, result):
        assert output.returncode == 2, output.stderr
        assert "parameter 'day'" in output.stderr and problem in output.stderr, output.stderr
        assert "Traceback" not in output.stderr
    assert runs_recorded(env) == 0 and not (env.root / "ran.txt").exists()


def test_as_of_fixes_now_for_the_parameters_the_conditions_and_the_start_block(env):
    flow = write(env, """
        params:
          day: {type: date, expr: "today()"}
          at: {type: datetime, expr: "now()"}
        blocks:
          start:
            type: trigger.manual
          check:
            type: logic.if
            config: {condition: "today() == parse_date('2026-02-28') and format(now(), '%H:%M') == '23:59'"}
          on_the_day:
            type: logic.python
            config: {outputs: [], code: "open('started.txt', 'w').write(str(input))"}
        edges:
          - start.start -> check.value
          - check.true -> on_the_day.input
    """)

    result = env.taskloom("run", flow, "--as-of", "2026-02-28 23:59")

    assert result.returncode == 0, result.stderr
    assert (env.root / "started.txt").read_text() == "2026-02-28 23:59:00"
    assert json.loads(env.query("SELECT params FROM runs")[0][0]) == {"day": "2026-02-28", "at": "2026-02-28 23:59:00"}


def far_time_zone() -> str:
    """A time zone 12 hours or more from this machine's: a run that ignored it would be that far off."""
    local_hours = dt.datetime.now().astimezone().utcoffset().total_seconds() / 3600
    return "Pacific/Kiritimati" if local_hours < 1 else "Pacific/Pago_Pago"  # UTC+14, UTC-11


ZONED_FLOW = """
    timezone: ZONE
    params:
      item: {type: text, value: ''}
      day: {type: date, expr: "today()"}
      at: {type: datetime, expr: "now()"}
    blocks:
      start:
        type: trigger.manual
      show:
        type: logic.python
        config: {outputs: [], code: "open('started.txt', 'w', encoding='utf-8').write(str(input))"}
    edges:
      - start.start -> show.input
"""


def test_now_and_today_are_in_the_flows_time_zone(env):
    from zoneinfo import ZoneInfo

    zone = far_time_zone()
    flow = write(env, ZONED_FLOW.replace("ZONE", zone))
    before = dt.datetime.now(ZoneInfo(zone)).replace(tzinfo=None)

    result = env.taskloom("run", flow)

    after = dt.datetime.now(ZoneInfo(zone)).replace(tzinfo=None)
    assert result.returncode == 0, result.stderr
    params = json.loads(env.query("SELECT params FROM runs")[0][0])
    at = dt.datetime.fromisoformat(params["at"])
    started = dt.datetime.fromisoformat((env.root / "started.txt").read_text(encoding="utf-8"))
    tolerance = dt.timedelta(minutes=1)
    assert before - tolerance <= at <= after + tolerance, (zone, before, at)
    assert params["day"] == at.date().isoformat() and started == at  # one time for the whole run


def test_a_flow_that_for_each_runs_in_the_same_time_zone_gets_the_time_of_the_outer_run(env):
    from zoneinfo import ZoneInfo

    zone = far_time_zone()
    write(env, ZONED_FLOW.replace("ZONE", zone), "each.yaml")
    flow = write(env, f"""
        timezone: {zone}
        params:
          at: {{type: datetime, expr: "now()"}}
        blocks:
          items:
            type: logic.python
            config: {{inputs: [], code: "output = ['one']"}}
          each:
            type: logic.for_each
            config: {{flow: each.yaml, param: item}}
        edges:
          - items.output -> each.items
    """)
    before = dt.datetime.now(ZoneInfo(zone)).replace(tzinfo=None)

    result = env.taskloom("run", flow)

    after = dt.datetime.now(ZoneInfo(zone)).replace(tzinfo=None)
    assert result.returncode == 0, result.stderr
    outer, inner = [json.loads(p) for (p,) in env.query("SELECT params FROM runs ORDER BY id")]
    tolerance = dt.timedelta(minutes=1)
    for at in (outer["at"], inner["at"]):
        assert before - tolerance <= dt.datetime.fromisoformat(at) <= after + tolerance, (zone, before, outer, inner)
    # Both flows are in the same time zone, and the flow it runs is worked out for the time of the outer run.
    assert inner["at"] == outer["at"] and inner["day"] == outer["at"][:10]


@pytest.mark.parametrize("regions, args", [
    ("{type: list, value: [EU]}", ["--param", "regions="]),
    ("""{type: list, expr: "['EU'] if is_business_day(today()) else []"}""", ["--as-of", "2026-01-31 09:00"]),
], ids=["--param regions=", "an expression that gives none"])
def test_an_empty_list_bound_into_sql_fails_the_block_saying_so_and_its_error_handler_gets_that(env, regions, args):
    flow = write(env, """
        params:
          regions: REGIONS
        blocks:
          sales:
            type: logic.python
            config: {inputs: [], code: "output = pl.DataFrame({'region': ['EU', 'US'], 'amount': [1, 2]})"}
          pick:
            type: data.duckdb_sql
            config: {inputs: [sales], sql: "select * from sales where region in (:regions)"}
          handler:
            type: logic.python
            config:
              inputs: [problem]
              outputs: []
              code: "open('problem.txt', 'w', encoding='utf-8').write(problem['error'])"
        edges:
          - sales.output -> pick.sales
          - pick.error -> handler.problem
    """.replace("REGIONS", regions))

    result = env.taskloom("run", flow, *args)

    assert result.returncode == 0, result.stderr
    run_id, _ = env.last_run()
    assert env.block_status(run_id) == {"sales": "success", "pick": "failed", "handler": "success"}
    assert "bind parameter :regions is an empty list" in (env.root / "problem.txt").read_text(encoding="utf-8")


def test_an_empty_list_bound_into_a_database_query_fails_saying_so(jdbc_env):
    env = jdbc_env
    flow = write(env, """
        params:
          regions: {type: list, value: [EU]}
        blocks:
          q:
            type: db.run_query
            config:
              connection: warehouse
              sql: "select region from (values ('EU'), ('US')) as t(region) where region in (:regions)"
    """)
    assert env.taskloom("run", flow).returncode == 0  # the query itself works

    result = env.taskloom("run", flow, "--param", "regions=")

    assert result.returncode == 1
    run_id, status = env.last_run()
    [(error,)] = env.query("SELECT error FROM blocks WHERE run_id=? AND block='q'", run_id)
    assert status == "failed" and "bind parameter :regions is an empty list" in error, error


CALENDAR_PARAMS = """
    params:
      prev: {type: date, expr: "today() - bdays(1)"}
      prev_us: {type: date, expr: "today() - bdays(1, cal='US')"}
      prev_nyse: {type: date, expr: "today() - bdays(1, cal='NYSE')"}
      two_back_fourday: {type: date, expr: "today() - bdays(2, cal='fourday')"}
      mlk_day_open: {type: bool, expr: "is_business_day(parse_date('2026-01-19'))"}
      mlk_day_open_us: {type: bool, expr: "is_business_day(parse_date('2026-01-19'), cal='US')"}
    blocks:
      a:
        type: logic.python
        config: {inputs: [], outputs: [], code: "pass"}
"""


@pytest.mark.parametrize("flow_calendar, prev", [("", "2026-01-19"), ("calendar: US\n", "2026-01-16")],
                         ids=["the settings' calendar", "the flow's own calendar"])
def test_business_days_follow_the_settings_calendar_the_flows_and_the_one_an_expression_names(env, flow_calendar, prev):
    write(env, "calendar: office\n", "home/settings.yaml")
    write(env, "base: US\nremove: [2026-01-19]\n", "home/calendars/office.yaml")  # the office works on MLK Day
    write(env, "weekend: [fri, sat, sun]\nadd: [2026-01-15]\n", "home/calendars/fourday.yaml")
    flow = write(env, flow_calendar + textwrap.dedent(CALENDAR_PARAMS))

    result = env.taskloom("run", flow, "--as-of", "2026-01-20 09:00")  # the Tuesday after MLK Day (a US holiday)

    assert result.returncode == 0, result.stderr
    assert json.loads(env.query("SELECT params FROM runs")[0][0]) == {
        "prev": prev, "prev_us": "2026-01-16", "prev_nyse": "2026-01-16", "two_back_fourday": "2026-01-14",
        "mlk_day_open": not flow_calendar, "mlk_day_open_us": False}


def test_a_calendar_of_a_region_within_a_country_counts_that_regions_holidays(env):
    write(env, "base: US-NY\n", "home/calendars/office.yaml")
    flow = write(env, """
        calendar: US-NY
        params:
          prev: {type: date, expr: "today() - bdays(1)"}
          prev_office: {type: date, expr: "today() - bdays(1, cal='office')"}
          prev_us: {type: date, expr: "today() - bdays(1, cal='US')"}
          lincoln_open: {type: bool, expr: "is_business_day(parse_date('2026-02-12'))"}
        blocks:
          a:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
    """)

    result = env.taskloom("run", flow, "--as-of", "2026-02-13 09:00")  # the day after Lincoln's Birthday (New York)

    assert result.returncode == 0, result.stderr
    assert json.loads(env.query("SELECT params FROM runs")[0][0]) == {
        "prev": "2026-02-11", "prev_office": "2026-02-11", "prev_us": "2026-02-12", "lincoln_open": False}


def test_a_calendar_without_holidays_this_year_is_a_warning_and_an_unknown_one_an_error(env):
    write(env, "add: [2020-01-01]\n", "home/calendars/old.yaml")
    flow = write(env, "calendar: old\n" + textwrap.dedent(CALENDAR_PARAMS).replace("fourday", "old"))

    result = env.taskloom("validate", flow)

    assert result.returncode == 0, result.stderr
    assert f"warning: calendar 'old' lists no holidays for {dt.date.today().year}" in result.stderr

    flow = write(env, "calendar: Atlantis\n" + textwrap.dedent(CALENDAR_PARAMS).replace("fourday", "old"))
    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert result.returncode == 2 and "unknown holiday calendar 'Atlantis'" in result.stderr, result.stderr
    assert runs_recorded(env) == 0


@pytest.mark.parametrize("calendar", ["weekend: [sat, funday]\n", "weekend: sat\n", "add: [2026-13-01]\n"],
                         ids=["unknown weekend day", "weekend not a list", "date that does not exist"])
def test_a_mistake_in_a_calendar_file_is_reported_naming_the_calendar(env, calendar):
    write(env, calendar, "home/calendars/work.yaml")
    flow = write(env, "calendar: work\n" + textwrap.dedent(CALENDAR_PARAMS).replace("fourday", "work"))

    result = env.taskloom("validate", flow)

    assert result.returncode == 2
    assert "work" in result.stderr, result.stderr  # which calendar (file) has the mistake


# --- secrets ----------------------------------------------------------------------------------------------------

LOGIN_BLOCK = """
    from taskloom import Block, fields, ports


    class Login(Block):
        type_id = "test.login"
        description = "Logs in with a connection's password."
        outputs = {"login": ports.Any(), "password": ports.Any()}
        config = {"connection": fields.Connection(kind="api")}

        def run(self, ctx):
            password = ctx.connection(self.config.connection)["password"]
            ctx.log(f"logging in with {password}")
            return {"login": f"user:{password}", "password": password}
"""
PASSWORD = "Qv8Lantern7x4Z"


def where_the_password_shows(places: dict) -> list[str]:
    """Each place ({name: text}) where the start or the end of PASSWORD shows, with the text around it."""
    found = []
    for place, text in places.items():
        for piece in (PASSWORD[:6], PASSWORD[-6:]):
            if (at := text.find(piece)) >= 0:
                found.append(f"{place}: …{text[max(at - 40, 0):at + 30]}…")
    return found


def test_a_connection_password_never_reaches_the_terminal_the_history_or_the_run_folders(env):
    env.write("home/blocks/login.py", LOGIN_BLOCK)
    env.write("home/connections.yaml", "vault:\n  kind: api\n  password: 'secret:vault_pw'\n")
    env.write("home/secrets.yaml", f"vault_pw: {PASSWORD}\n").chmod(0o600)
    flow = write(env, """
        blocks:
          login:
            type: test.login
            config: {connection: vault}
          look:
            type: logic.inspect
          use:
            type: logic.python
            config:
              inputs: [login, password]
              outputs: []
              code: |
                open('got.txt', 'w').write(password)
                print('using', login, [password])
                raise RuntimeError('refused for ' + login)
            retry: {policy: fixed, max_attempts: 2}
          handler:
            type: logic.python
            config:
              inputs: [problem]
              outputs: []
              code: "open('handled.txt', 'w', encoding='utf-8').write(problem['error'] + problem['traceback'])"
        edges:
          - login.login -> look.value
          - login.login -> use.login
          - login.password -> use.password
          - use.error -> handler.problem
    """)

    terminal = ""
    for args in ([], ["--json-events"]):
        result = env.taskloom("run", flow, *args)
        assert result.returncode == 0, result.stderr
        terminal += result.stdout + result.stderr
    run_id, _ = env.last_run()
    retry = env.taskloom("run", flow, "--only", "use", "--inputs-from", run_id)

    assert (env.root / "got.txt").read_text() == PASSWORD  # the code had the password itself
    assert env.block_status(run_id) == {"login": "success", "look": "success", "use": "failed", "handler": "success"}
    assert "refused for user:" in (env.root / "handled.txt").read_text(encoding="utf-8")
    assert retry.returncode == 2 and "held a password" in retry.stderr  # never saved, so a re-run cannot have it
    places = {"the terminal": terminal + retry.stdout + retry.stderr,
              "the error handler's input": (env.root / "handled.txt").read_text(encoding="utf-8")}
    for path in env.home.rglob("*"):
        if path.is_file() and path.name != "secrets.yaml":  # the history, the run folders
            places[str(path.relative_to(env.home))] = path.read_bytes().decode("latin-1")
    assert where_the_password_shows(places) == []


def test_a_password_in_a_table_never_shows_in_its_preview_even_where_a_long_cell_is_cut_short(env):
    env.write("home/blocks/login.py", LOGIN_BLOCK)
    env.write("home/connections.yaml", "vault:\n  kind: api\n  password: 'secret:vault_pw'\n")
    env.write("home/secrets.yaml", f"vault_pw: {PASSWORD}\n").chmod(0o600)
    flow = write(env, """
        blocks:
          login:
            type: test.login
            config: {connection: vault}
          servers:
            type: logic.python
            config:
              inputs: [pw]
              code: "output = pl.DataFrame({'url': ['jdbc:example://db.example.com/sales;user=me;pwd=' + pw + ';ssl=true']})"
          look:
            type: logic.inspect
        edges:
          - login.password -> servers.pw
          - servers.output -> look.value
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    run_id, _ = env.last_run()
    shown = {"the terminal": result.stdout + result.stderr,
             "the data preview": " ".join(p for (p,) in env.query("SELECT previews FROM blocks WHERE run_id=?", run_id)),
             "the log": " ".join(logs(env, run_id, "look"))}
    # Data preview and the Log show a table's first rows, each cell cut short after 60 characters.
    assert where_the_password_shows(shown) == []


# --- run folders, retention and settings ------------------------------------------------------------------------

def test_runs_older_than_keep_runs_for_are_deleted_and_a_rerun_from_one_says_so(env):
    flow = write(env, """
        blocks:
          load:
            type: logic.python
            config: {inputs: [], code: "output = pl.DataFrame({'n': [1, 2, 3]})"}
          save:
            type: logic.python
            config: {inputs: [t], outputs: [], code: "open('rows.txt', 'a').write(str(len(t)))"}
        edges:
          - load.output -> save.t
    """)
    for _ in range(3):
        assert env.taskloom("run", flow).returncode == 0
    env.query("UPDATE runs SET started='2020-01-01T00:00:00' WHERE id < 3")  # older than keep_runs_for (5 years)
    assert env.taskloom("run", flow).returncode == 0  # deletes runs 1 and 2 as it ends

    assert sorted(p.name for p in (env.home / "runs").iterdir()) == ["3", "4"]
    assert env.query("SELECT id, status FROM runs ORDER BY id") == [(3, "success"), (4, "success")]

    result = env.taskloom("run", flow, "--only", "save", "--inputs-from", 1)
    assert result.returncode == 2 and "keep_runs_for" in result.stderr, result.stderr
    result = env.taskloom("run", flow, "--only", "save", "--inputs-from", 4)
    assert result.returncode == 0, result.stderr
    assert (env.root / "rows.txt").read_text() == "33333"


@pytest.mark.parametrize("setting", ["keep_runs_for: lots"])
def test_a_retention_setting_that_is_not_a_number_is_reported_naming_it(env, setting):
    env.write("home/settings.yaml", setting + "\n")
    flow = write(env, """
        blocks:
          a:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
    """)

    result = env.taskloom("run", flow)

    name = setting.split(":")[0]
    recorded = [s for (s,) in env.query("SELECT status FROM runs")] if (env.home / "history.db").exists() else []
    problems = []
    if name not in result.stderr:
        problems.append(f"the error does not say which setting: {result.stderr.strip()!r}")
    if recorded and {"success": 0, "failed": 1, "cancelled": 130}[recorded[-1]] != result.returncode:
        problems.append(f"the run is recorded {recorded[-1]!r} but taskloom exited with {result.returncode}")
    assert problems == []


# --- re-runs ----------------------------------------------------------------------------------------------------

RERUN_FLOW = """
    params:
      region: {type: text, value: EU}
    blocks:
      load:
        type: logic.python
        config: {inputs: [], code: "output = params['region']"}
      save:
        type: logic.python
        config: {outputs: [], code: "open('saved.txt', 'a').write(input)"}
    edges:
      - load.output -> save.input
"""


@pytest.mark.parametrize("args, problem", [
    (["--only", "nope"], "no block named 'nope'"),
    (["--from", "nope", "--inputs-from", "1"], "no block named 'nope'"),
    (["--only", "save", "--from", "load"], "use one of them"),
    (["--input", "input='x'"], "--only"),
    (["--only", "save", "--input", "input"], "NAME=VALUE"),
    (["--only", "save", "--inputs-from", "1", "--input", "nope=1"], "no input named 'nope'"),
    (["--only", "save", "--inputs-from", "99"], "no run #99"),
    (["--only", "save", "--inputs-from", "99", "--param", "region=US"], "no run #99"),
], ids=["unknown block", "from an unknown block", "only and from", "input without only", "input without a value",
        "unknown input", "run that does not exist", "run that does not exist, with a parameter"])
def test_a_rerun_that_cannot_work_is_refused_before_anything_runs(env, args, problem):
    flow = write(env, RERUN_FLOW)
    assert env.taskloom("run", flow).returncode == 0  # run #1

    result = env.taskloom("run", flow, *args)

    assert result.returncode == 2 and problem in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert runs_recorded(env) == 1 and (env.root / "saved.txt").read_text() == "EU"


@pytest.mark.parametrize("how", ["--only", "--from"])
def test_a_rerun_with_the_inputs_of_a_run_of_another_flow_is_refused(env, how):
    other = write(env, RERUN_FLOW.replace("output = params['region']", "output = 'OTHER FLOW'")
                  .replace("saved.txt", "other_saved.txt"), "other.yaml")
    flow = write(env, RERUN_FLOW)
    assert env.taskloom("run", other).returncode == 0
    other_run, _ = env.last_run()

    result = env.taskloom("run", flow, how, "save", "--inputs-from", other_run)

    saved = (env.root / "saved.txt").read_text() if (env.root / "saved.txt").exists() else None
    assert (result.returncode, saved) == (2, None), result.stderr  # not this flow's run: its data is not this flow's


WRITE_FILE_FLOW = """
    blocks:
      start:
        type: trigger.manual
      load:
        type: logic.python
        config: {inputs: [], code: "output = pl.DataFrame({'n': [1, 2]})"}
      save:
        type: data.write_file
        config: {path: out.csv}
    edges:
      - start.start -> load.after
      - load.output -> save.table
"""


@pytest.mark.parametrize("inputs_from", [True, False], ids=["with a run's inputs", "with none"])
def test_running_one_block_with_a_required_input_left_empty_is_refused_before_anything_runs(env, inputs_from):
    flow = write(env, WRITE_FILE_FLOW)
    assert env.taskloom("run", flow).returncode == 0
    (env.root / "out.csv").unlink()

    result = env.taskloom("run", flow, "--only", "save", *(["--inputs-from", "1"] if inputs_from else []),
                          "--input", "table=")

    assert result.returncode == 2 and "save.table needs a value" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert runs_recorded(env) == 1 and not (env.root / "out.csv").exists()


def test_the_editor_refuses_to_run_one_block_with_a_required_input_cleared(env, win, qtbot, monkeypatch):
    from taskloom.editor import dialogs
    from tests.test_lines import menu_action, wait_runs

    flow = write(env, WRITE_FILE_FLOW)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    (env.root / "out.csv").unlink()
    shown = {}

    def clear_table(dialog):
        shown.update({name: field.text() for name, field in dialog.fields.items()})
        dialog.fields["table"].setText("")  # an empty field means no value
        return True
    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", clear_table)

    menu_action(win, tab, "save", "Run only this block, with inputs I choose").trigger()
    wait_runs(qtbot, tab, 2)

    assert shown["table"].startswith("table(")  # it was filled in with what reached it
    assert "save.table needs a value" in win.log.text.toPlainText()
    assert runs_recorded(env) == 1 and not (env.root / "out.csv").exists()


def test_running_on_from_a_block_is_refused_when_a_block_after_it_needs_a_password_from_before_it(env):
    env.write("home/blocks/login.py", LOGIN_BLOCK)
    env.write("home/connections.yaml", "vault:\n  kind: api\n  password: 'secret:vault_pw'\n")
    env.write("home/secrets.yaml", f"vault_pw: {PASSWORD}\n").chmod(0o600)
    flow = write(env, """
        blocks:
          login:
            type: test.login
            config: {connection: vault}
          load:
            type: logic.python
            config: {inputs: [], code: "output = 'rows'"}
          step:
            type: logic.python
            config: {inputs: [data], code: "output = data + '!'"}
          send:
            type: logic.python
            config:
              inputs: [data, password]
              outputs: []
              code: "open('sent.txt', 'a').write(f'{data} with a password of {len(password)} ')"
        edges:
          - load.output -> step.data
          - step.output -> send.data
          - login.password -> send.password
    """)
    assert env.taskloom("run", flow).returncode == 0

    result = env.taskloom("run", flow, "--from", "step", "--inputs-from", "1")

    assert result.returncode == 2, result.stderr
    assert "login.password held a password" in result.stderr and "run the whole flow" in result.stderr, result.stderr
    assert runs_recorded(env) == 1
    assert (env.root / "sent.txt").read_text() == "rows! with a password of 14 "  # only the full run sent it


# --- flow files, validation, run statuses and exit codes --------------------------------------------------------

@pytest.mark.parametrize("content", [None, b"", b"- a\n- b\n", b"blocks: [unclosed\n", b"name: caf\xe9\nblocks: {}\n"],
                         ids=["missing", "empty", "a list", "not YAML", "not UTF-8"])
def test_a_flow_file_that_cannot_be_read_is_an_error_not_a_crash(env, content):
    flow = env.root / "flow.yaml"
    if content is not None:
        flow.write_bytes(content)

    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert result.returncode == 2 and result.stderr.startswith("error: "), result.stderr
        assert "Traceback" not in result.stderr
    assert runs_recorded(env) == 0


WAIT_A = "blocks:\n  a: {type: logic.wait, config: {seconds: 0}"
WAIT_AB = WAIT_A + "}\n  b: {type: logic.wait, config: {seconds: 0}}\n"
WAIT_ABC = WAIT_AB + "  c: {type: logic.wait, config: {seconds: 0}}\n"
INVALID_FLOWS = {
    "unknown top-level key": ("steps: []\nblocks: {}\n", "unknown top-level key 'steps'"),
    "newer flow format": ("taskloom: 2\nblocks: {}\n", "unsupported flow format"),
    "block id with a dash": ("blocks:\n  my-block: {type: logic.wait, config: {seconds: 0}}\n",
                             "blocks.my-block: not a valid block id"),
    "block named error": ("blocks:\n  error: {type: logic.wait, config: {seconds: 0}}\n",
                          "blocks.error: not a valid block id"),
    "block without a type": ("blocks:\n  a: {config: {seconds: 0}}\n", "blocks.a: needs a 'type'"),
    "unknown block key": (WAIT_A + ", retries: 3}\n", "blocks.a: unknown key 'retries'"),
    "unknown block type": ("blocks:\n  a: {type: logic.sleep}\n", "unknown block type 'logic.sleep'"),
    "unknown setting": ("blocks:\n  a: {type: logic.wait, config: {secs: 1}}\n", "unknown setting(s) ['secs']"),
    "setting of the wrong kind": ("blocks:\n  a: {type: logic.wait, config: {seconds: soon}}\n", "a: Seconds:"),
    "missing setting": ("blocks:\n  a: {type: logic.wait}\n", "a: Seconds is required"),
    "block from a newer version": (WAIT_A + ", version: 99}\n", "saved with version 99"),
    "connection that cannot be read": (WAIT_AB + "edges:\n  - a.value => b.value\n", "cannot read 'a.value => b.value'"),
    "connection to an unknown block": (WAIT_AB + "edges:\n  - a.value -> z.value\n", "unknown block"),
    "connection into an unknown input": (WAIT_AB + "edges:\n  - a.value -> b.val\n", "'b' has no input 'val'"),
    "input connected twice": (WAIT_ABC + "edges:\n  - a.value -> c.value\n  - b.value -> c.value\n",
                              "input 'c.value' is already connected"),
    "required input not connected": ("blocks:\n  each: {type: logic.for_each, config: {flow: x.yaml, param: p}}\n",
                                     "required input 'items' is not connected"),
    "cycle": (WAIT_AB + "edges:\n  - a.value -> b.value\n  - b.value -> a.after\n", "cycle through: a, b"),
    "parameter name": ("params:\n  2day: {type: date, value: 2026-01-01}\nblocks: {}\n", "params.2day: not a valid name"),
    "parameter without a type": ("params:\n  day: {value: 2026-01-01}\nblocks: {}\n", "params.day: needs a type"),
    "parameter of an unknown type": ("params:\n  day: {type: string, value: x}\nblocks: {}\n", "params.day: needs a type"),
    "parameter with a value and an expr": ("params:\n  day: {type: date, value: 2026-01-01, expr: today()}\nblocks: {}\n",
                                           "exactly one of 'value' or 'expr'"),
    "parameter value of the wrong type": ("params:\n  n: {type: int, value: many}\nblocks: {}\n", "parameter 'n'"),
    "time zone": ("timezone: Mars/Olympus\nblocks: {}\n", "Mars/Olympus"),
    "retry policy": (WAIT_A + ", retry: {policy: linear}}\n", "retry policy must be none, fixed or exponential"),
    "retry setting of another policy": (WAIT_A + ", retry: {policy: none, max_attempts: 3}}\n",
                                        "unknown retry setting(s) for policy none"),
    "no attempts": (WAIT_A + ", retry: {policy: fixed, max_attempts: 0}}\n", "max_attempts >= 1"),
    "shrinking backoff": (WAIT_A + ", retry: {policy: exponential, factor: 0.5}}\n", "factor >= 1"),
    "jitter above 1": (WAIT_A + ", retry: {policy: exponential, jitter: 1.5}}\n", "jitter between 0 and 1"),
    "retry delay": (WAIT_A + ", retry: {policy: fixed, delay: soon}}\n", "invalid duration 'soon'"),
    "timeout": (WAIT_A + ", timeout: 10 min}\n", "invalid duration '10 min'"),
}


@pytest.mark.parametrize("text, problem", list(INVALID_FLOWS.values()), ids=list(INVALID_FLOWS))
def test_validate_and_run_refuse_a_flow_with_a_mistake_saying_what_it_is(env, text, problem):
    flow = write(env, text)

    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert result.returncode == 2 and problem in result.stderr, result.stderr
        assert "Traceback" not in result.stderr
    assert runs_recorded(env) == 0


def test_a_flow_without_blocks_is_valid_and_its_run_succeeds(env):
    flow = write(env, "blocks: {}\n")

    assert env.taskloom("validate", flow).returncode == 0
    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert env.last_run()[1] == "success"


def test_a_long_chain_of_blocks_runs_through(env):
    blocks = {"b0": {"type": "logic.python", "config": {"inputs": [], "code": "output = 0"}}}
    edges = []
    for i in range(1, 50):
        blocks[f"b{i}"] = {"type": "logic.python", "config": {"code": "output = input + 1"}}
        edges.append(f"b{i - 1}.output -> b{i}.input")
    blocks["save"] = {"type": "logic.python", "config": {"outputs": [], "code": "open('n.txt', 'w').write(str(input))"}}
    edges.append("b49.output -> save.input")
    flow = write(env, json.dumps({"blocks": blocks, "edges": edges}))  # JSON is YAML

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert (env.root / "n.txt").read_text() == "49"


def test_validate_of_a_flow_whose_name_the_windows_console_cannot_show_says_it_is_ok(env):
    # Windows: printed into a pipe or a file, Python writes in the ANSI code page (cp1252 on most).
    env.env["PYTHONIOENCODING"] = "cp1252"
    flow = write(env, """
        name: Ventas → informe 📈
        blocks:
          a:
            type: logic.wait
            config: {seconds: 0}
    """)

    result = env.taskloom("validate", flow)

    assert result.returncode == 0, result.stderr
    assert "OK (1 blocks)" in result.stdout


# --- user blocks ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("config, greeting", [("{greeting: Hola}", "Hola, Ana!"), ("{}", "Hello, Ana!")],
                         ids=["its setting", "its default"])
def test_the_block_from_the_readme_runs_with_its_setting_and_logs(env, config, greeting):
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    code = readme.split("# ~/.taskloom/blocks/greet.py\n", 1)[1].split("```", 1)[0]
    write(env, code, "home/blocks/greet.py")
    flow = write(env, f"""
        params:
          who: {{type: text, value: Ana}}
        blocks:
          person:
            type: logic.python
            config: {{inputs: [], code: "output = params['who']"}}
          greet:
            type: my.greet
            config: {config}
          save:
            type: logic.python
            config: {{inputs: [text], outputs: [], code: "open('greeting.txt', 'w').write(text)"}}
        edges:
          - person.output -> greet.name
          - greet.text -> save.text
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert (env.root / "greeting.txt").read_text() == greeting
    run_id, _ = env.last_run()
    assert logs(env, run_id, "greet") == ["greeting Ana"]


ODD_BLOCK = """
    from taskloom import Block, fields, ports


    class Odd(Block):
        type_id = "test.odd"
        description = "Returns what its setting says."
        outputs = {"value": ports.Any(), "table": ports.Table()}
        config = {"returns": fields.Code("python")}

        def run(self, ctx):
            return eval(self.config.returns)
"""


@pytest.mark.parametrize("returns, problem", [
    ("[1, 2]", "TypeError: run() must return a dict of outputs, got list"),
    ("{'nope': 1}", "ValueError: run() returned unknown output 'nope'"),
    ("{'table': [1, 2]}", "TypeError: output 'table' must be a table, got list"),
])
def test_a_block_that_returns_something_other_than_its_outputs_fails_saying_what(env, returns, problem):
    env.write("home/blocks/odd.py", ODD_BLOCK)
    flow = write(env, f"""
        blocks:
          odd:
            type: test.odd
            config: {{returns: {json.dumps(returns)}}}
          after:
            type: logic.python
            config: {{inputs: [], outputs: [], code: "open('after.txt', 'w').write('x')"}}
        edges:
          - odd.value -> after.after
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 1
    run_id, status = env.last_run()
    [(error,)] = env.query("SELECT error FROM blocks WHERE run_id=? AND block='odd'", run_id)
    assert status == "failed" and error.startswith(problem), error
    assert env.block_status(run_id)["after"] == "skipped" and not (env.root / "after.txt").exists()


# --- the editor's preview of parameters -------------------------------------------------------------------------

def test_the_parameters_tab_previews_what_a_run_on_that_day_gets(env, win):
    from PySide6.QtCore import QDate

    write(env, "add: [2026-01-16]\n", "home/calendars/work.yaml")  # that Friday is a day off
    flow = write(env, """
        calendar: work
        params:
          day: {type: date, expr: "today() - bdays(1)"}
          day_key: {type: int, expr: "int(format(day, '%Y%m%d'))"}
        blocks:
          start:
            type: trigger.manual
          a:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - start.start -> a.after
    """)
    win.open_flow(flow)
    win.parameters.as_of.setDate(QDate(2026, 1, 19))  # a Monday
    table = win.parameters.table
    preview = {table.item(r, 0).text(): table.item(r, 4).text() for r in range(table.rowCount())}

    result = env.taskloom("run", flow, "--as-of", "2026-01-19 07:00")

    assert result.returncode == 0, result.stderr
    assert preview["day"].startswith("2026-01-15") and preview["day_key"] == "20260115"  # (the day's name follows)
    assert json.loads(env.query("SELECT params FROM runs")[0][0]) == {"day": "2026-01-15", "day_key": 20260115}
