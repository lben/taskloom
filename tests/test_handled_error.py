"""A failed block's failure is handled only when its error reaches a block that runs: at least one block connected
to its error output receives that error and runs. When every block its error goes to is skipped (for any reason),
the failure is unhandled and the run has failed, as if the error output were not connected: the same status, exit
code and notifications as any failed run. Which blocks run and which are skipped does not change. Through the real
`taskloom run`, the run history, the scheduler service, Keep runs and the editor."""

import datetime as dt
import os
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from taskloom.config import Home  # noqa: E402
from taskloom.scheduler import Scheduler  # noqa: E402
from tests.test_lines import shown, tab_icon_is, wait_runs  # noqa: E402

MANUAL = {"type": "trigger.manual"}
FAILS = "raise ValueError('the check failed')"


def python(code: str, inputs=(), outputs=()) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code}}


def write(path: Path, blocks: dict, edges: list) -> Path:
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": edges}, sort_keys=False), encoding="utf-8")
    return path


def run(env, flow: Path, *args):
    """`taskloom run FLOW ARGS`: (its result, the run's id, the run's status in the history)."""
    result = env.taskloom("run", flow, *args)
    run_id, status = env.last_run()
    return result, run_id, status


# The reported flow: the handler takes both check's result and its error, so check's failure skips it.
REPORTED_BLOCKS = {
    "go": MANUAL,
    "check": {"type": "logic.python", "config": {"outputs": ["result"], "code": FAILS}},
    "handler": {"type": "logic.python", "config": {"inputs": ["input"], "code": "print('handler got', input)"}},
}
REPORTED_EDGES = ["go.start -> check.after", "check.result -> handler.input", "check.error -> handler.input"]


def test_the_reported_flow_fails_when_the_failure_skips_its_only_error_handler(env):
    flow = write(env.root / "flow.yaml", REPORTED_BLOCKS, REPORTED_EDGES)

    result, run_id, status = run(env, flow)

    assert env.block_status(run_id) == {"go": "success", "check": "failed", "handler": "skipped"}  # as before
    assert (status, result.returncode) == ("failed", 1), result.stderr
    lines = result.stderr.splitlines()
    assert any("[check] failed" in line and "the check failed" in line for line in lines), result.stderr  # why
    assert any(line.endswith(f"run #{run_id} failed") for line in lines), result.stderr
    assert "handler got" not in result.stdout + result.stderr
    [(error,)] = env.query("SELECT error FROM blocks WHERE run_id=? AND block='check'", run_id)
    assert "the check failed" in error  # Run history says which block failed and why


def test_running_from_the_failed_block_again_fails_when_its_error_handler_is_skipped_again(env):
    flow = write(env.root / "flow.yaml", REPORTED_BLOCKS, REPORTED_EDGES)
    _, first, _ = run(env, flow)

    result, run_id, status = run(env, flow, "--from", "check", "--inputs-from", first)

    assert run_id != first
    assert env.block_status(run_id) == {"check": "failed", "handler": "skipped"}
    assert (status, result.returncode) == ("failed", 1), result.stderr


def test_running_from_a_later_block_fails_when_a_failure_whose_error_handler_was_skipped_stops_one_of_its_blocks(env):
    flow = write(env.root / "flow.yaml", {
        **REPORTED_BLOCKS,
        "prepare": python("value = 1", outputs=["value"]),
        "report": python("print('report got', value, result)", inputs=["value", "result"]),
    }, [*REPORTED_EDGES, "go.start -> prepare.after", "prepare.value -> report.value", "check.result -> report.result"])
    _, first, _ = run(env, flow)
    assert env.block_status(first)["handler"] == "skipped"  # nothing handled check's failure in that run

    result, run_id, status = run(env, flow, "--from", "prepare", "--inputs-from", first)

    assert env.block_status(run_id) == {"prepare": "success", "report": "skipped"}  # check's failure stops report
    assert (status, result.returncode) == ("failed", 1), result.stderr
    assert any(line.endswith(f"run #{run_id} failed") for line in result.stderr.splitlines()), result.stderr


def test_running_from_the_error_handler_succeeds_when_the_failure_it_handles_stops_a_block_after_it(env):
    flow = write(env.root / "flow.yaml", {
        "go": MANUAL,
        "check": python(FAILS, outputs=["result"]),
        "handler": python("note = str(problem)", inputs=["problem"], outputs=["note"]),
        "report": python("print('report got', note, result)", inputs=["note", "result"]),
    }, ["go.start -> check.after", "check.error -> handler.problem", "handler.note -> report.note",
        "check.result -> report.result"])
    _, first, _ = run(env, flow)

    result, run_id, status = run(env, flow, "--from", "handler", "--inputs-from", first)

    assert env.block_status(run_id) == {"handler": "success", "report": "skipped"}  # check's failure stops report
    assert (status, result.returncode) == ("success", 0), result.stderr


@pytest.mark.parametrize("second_fails, handler, status, code", [
    (False, "skipped", "failed", 1),
    (True, "success", "success", 0),
], ids=["only the first fails: the handler is skipped", "both fail: the handler runs"])
def test_a_handler_with_one_input_per_covered_block_handles_the_failures_only_when_it_runs(
        env, second_fails, handler, status, code):
    flow = write(env.root / "flow.yaml", {
        "go": MANUAL,
        "check1": python(FAILS, outputs=["result"]),
        "check2": python("raise ValueError('the second check failed')" if second_fails else "result = 'fine'",
                         outputs=["result"]),
        "handler": python("print('handler got', a, b)", inputs=["a", "b"]),
    }, ["go.start -> check1.after", "go.start -> check2.after", "check1.error -> handler.a", "check2.error -> handler.b"])

    result, run_id, got = run(env, flow)

    assert env.block_status(run_id) == {"go": "success", "check1": "failed",
                                        "check2": "failed" if second_fails else "success", "handler": handler}
    assert (got, result.returncode) == (status, code), result.stderr


HANDLER_SKIPPED_BECAUSE = {
    # Its run-after comes from the path an If did not take.
    "a path not taken": ({
        "route": {"type": "logic.if", "config": {"condition": "False"}},
        "handler": python("print('handler got', problem)", inputs=["problem"]),
    }, ["go.start -> route.after", "route.true -> handler.after", "check.error -> handler.problem"]),
    # It also takes the data of another block that failed (a failure its own handler handles).
    "another block's failure": ({
        "load": python("raise ValueError('the load failed')", outputs=["rows"]),
        "load_handler": python("print('load handled')", inputs=["problem"]),
        "handler": python("print('handler got', problem, rows)", inputs=["problem", "rows"]),
    }, ["go.start -> load.after", "load.error -> load_handler.problem", "load.rows -> handler.rows",
        "check.error -> handler.problem"]),
}


@pytest.mark.parametrize("why", list(HANDLER_SKIPPED_BECAUSE))
def test_a_failure_whose_error_handler_is_skipped_for_another_reason_fails_the_run(env, why):
    blocks, edges = HANDLER_SKIPPED_BECAUSE[why]
    flow = write(env.root / "flow.yaml", {"go": MANUAL, "check": python(FAILS, outputs=["result"]), **blocks},
                 ["go.start -> check.after", *edges])

    result, run_id, status = run(env, flow)

    blocks_status = env.block_status(run_id)
    assert (blocks_status["check"], blocks_status["handler"]) == ("failed", "skipped"), blocks_status
    if "load_handler" in blocks_status:
        assert blocks_status["load_handler"] == "success"  # load's failure was handled; check's was not
    assert (status, result.returncode) == ("failed", 1), result.stderr


def test_a_failure_is_handled_when_one_of_the_blocks_its_error_goes_to_runs(env):
    flow = write(env.root / "flow.yaml", {
        "go": MANUAL,
        "check": python(FAILS, outputs=["result"]),
        "first": python("print('first got', input)", inputs=["input"]),
        "second": python("print('second got', problem)", inputs=["problem"]),
    }, ["go.start -> check.after", "check.result -> first.input", "check.error -> first.input",
        "check.error -> second.problem"])

    result, run_id, status = run(env, flow)

    assert env.block_status(run_id) == {"go": "success", "check": "failed", "first": "skipped", "second": "success"}
    assert (status, result.returncode) == ("success", 0), result.stderr


def test_an_error_handler_that_starts_and_fails_handles_the_failure_and_its_own_handler_handles_its_failure(env):
    flow = write(env.root / "flow.yaml", {
        "go": MANUAL,
        "check": python(FAILS, outputs=["result"]),
        "handler": python("raise RuntimeError('the handler failed too')", inputs=["problem"]),
        "last_resort": python("print('last resort got', problem)", inputs=["problem"]),
    }, ["go.start -> check.after", "check.error -> handler.problem", "handler.error -> last_resort.problem"])

    result, run_id, status = run(env, flow)

    assert env.block_status(run_id) == {"go": "success", "check": "failed", "handler": "failed",
                                        "last_resort": "success"}
    assert (status, result.returncode) == ("success", 0), result.stderr


def test_a_shared_error_handler_handles_the_failure_that_reached_it_when_a_covered_block_was_stopped_by_it(env):
    flow = write(env.root / "flow.yaml", {
        "go": MANUAL,
        "load": python("raise ValueError('the load failed')", outputs=["rows"]),
        "compute": python("total = len(rows)", inputs=["rows"], outputs=["total"]),
        "handler": python("print('handler got', list(errors))", inputs=["errors"]),
    }, ["go.start -> load.after", "load.rows -> compute.rows", "load.error -> handler.errors",
        "compute.error -> handler.errors"])

    result, run_id, status = run(env, flow)

    assert env.block_status(run_id) == {"go": "success", "load": "failed", "compute": "skipped", "handler": "success"}
    assert (status, result.returncode) == ("success", 0), result.stderr


@pytest.mark.parametrize("handler_inputs, error_edges, handler, status, code", [
    (["errors"], ["check.error -> handler.errors", "load.error -> handler.errors"], "success", "success", 0),
    (["a", "b"], ["check.error -> handler.a", "load.error -> handler.b"], "skipped", "failed", 1),
], ids=["one input for both lines: it runs", "one input per line: it is skipped"])
def test_each_lines_failure_into_a_handler_shared_by_two_lines_is_handled_only_when_the_handler_runs(
        env, handler_inputs, error_edges, handler, status, code):
    flow = write(env.root / "flow.yaml", {
        "go": MANUAL,
        "nightly": {"type": "trigger.schedule", "config": {"cron": "0 2 * * *"}},
        "check": python(FAILS, outputs=["result"]),
        "load": python("raise ValueError('the load failed')", outputs=["rows"]),
        "handler": python(f"print('handler got', {', '.join(handler_inputs)})", inputs=handler_inputs),
    }, ["go.start -> check.after", "nightly.start -> load.after", *error_edges])

    for args, line in [((), {"go": "success", "check": "failed"}),  # Run now: the Manual Start's line
                       (("--trigger", "nightly"), {"nightly": "success", "load": "failed"})]:  # as scheduled
        result, run_id, got = run(env, flow, *args)

        assert env.block_status(run_id) == {**line, "handler": handler}, args
        assert (got, result.returncode) == (status, code), (args, result.stderr)


def test_a_scheduled_run_whose_failure_skips_its_error_handler_is_told_and_emailed_as_failed(env, smtp):
    (env.home / "settings.yaml").write_text("notify_email: me@example.com\nsmtp_connection: mailer\n",
                                            encoding="utf-8")
    flow = write(env.root / "flow.yaml", {
        "nightly": {"type": "trigger.schedule", "config": {"cron": "* * * * *"}},
        **{k: v for k, v in REPORTED_BLOCKS.items() if k != "go"},
    }, ["nightly.start -> check.after", *REPORTED_EDGES[1:]])
    home = Home(env.home)
    home.set_scheduled(flow, True)
    notes = []
    scheduler = Scheduler(home, lambda title, message: notes.append((title, message)))
    t0 = dt.datetime(2026, 1, 5, 6, 30)
    try:
        scheduler.tick(t0)
        scheduler.tick(t0 + dt.timedelta(minutes=1))  # its time: the run starts
        scheduler.wait_for_runs()
    finally:
        for started in scheduler.runs:  # (none, unless it never finished)
            started.proc.kill()
            started.proc.wait(timeout=15)

    run_id, status = env.last_run()
    assert env.block_status(run_id) == {"nightly": "success", "check": "failed", "handler": "skipped"}
    assert status == "failed"
    (title, message), = notes  # the desktop notification of a failed run
    assert "failed" in title and "the check failed" in message, notes
    (email,) = smtp.messages  # notify_on is failure by default
    assert "failed" in email["Subject"], email["Subject"]


def test_always_keep_failed_runs_keeps_the_runs_whose_failure_skipped_its_error_handler(env):
    flow = write(env.root / "flow.yaml", {
        **REPORTED_BLOCKS, "go": {"type": "trigger.manual", "config": {"keep": "last 1", "keep_failed": True}},
    }, REPORTED_EDGES)

    for _ in range(2):
        env.taskloom("run", flow)

    assert env.query("SELECT id, status FROM runs ORDER BY id") == [(1, "failed"), (2, "failed")]


def test_the_editor_shows_the_run_failed_on_its_tab_in_run_history_and_why_in_the_log(win, qtbot, tmp_path):
    flow = write(tmp_path / "flow.yaml", REPORTED_BLOCKS, REPORTED_EDGES)
    tab = win.open_flow(flow)

    win.run_flow(tab)
    run_id = wait_runs(qtbot, tab, 1)

    assert shown(tab) == {"go": ("success", run_id), "check": ("failed", run_id), "handler": ("skipped", run_id)}
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 1, timeout=10_000)
    assert (table.item(0, 0).text(), table.item(0, 3).text()) == (str(run_id), "failed")  # Run history
    assert tab_icon_is(win, tab, "failed")  # the tab
    assert "the check failed" in win.log.text.toPlainText()  # the Log says why
