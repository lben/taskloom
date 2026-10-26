"""A block whose timeout is up gets a short grace to end. One that finishes its work in the grace without having seen
the stop keeps its result (it is not redone: no email sent twice, no rows appended twice); one that saw the stop,
raised, or is still running after the grace fails as a timeout, as before. Stopping the whole run is unchanged.
Through `taskloom run`, its run history and a real SMTP server."""

import asyncio
import re
import signal
import sys
import textwrap
import time

import pytest

from conftest import REPO
from test_paths_engine import Running, logs, write

TIMED_OUT = "TimeoutError: timed out after 0.5 s"


def load_flow(env, code: str, timeout: str = "0.5s"):
    """A flow whose block `load` runs `code` (Python Code; it may set `output`) with this timeout and a second attempt
    allowed; `report` writes what `load` gives into report.txt."""
    flow = textwrap.dedent("""
        blocks:
          load:
            type: logic.python
            timeout: TIMEOUT
            retry: {policy: fixed, max_attempts: 2}
            config:
              inputs: []
              code: |
        CODE
          report:
            type: logic.python
            config: {inputs: [rows], outputs: [], code: "open('report.txt', 'w', encoding='utf-8').write(rows)"}
        edges:
          - load.output -> report.rows
    """)
    return write(env, flow.replace("TIMEOUT", timeout).replace("CODE", textwrap.indent(textwrap.dedent(code).strip(), " " * 8)))


def load_row(env, run_id: int) -> tuple:
    return env.query("SELECT status, attempts, error FROM blocks WHERE run_id=? AND block='load'", run_id)[0]


# --- 1. finished in the grace, never having seen the stop: it keeps its result --------------------------------------

def test_an_email_whose_server_answers_in_the_grace_is_sent_once_and_the_block_succeeds(env, smtp):
    original = smtp.handle_DATA

    async def slow_to_accept(server, session, envelope):  # the mail server takes 1.5 s to accept the message
        await asyncio.sleep(1.5)
        return await original(server, session, envelope)

    smtp.handle_DATA = slow_to_accept
    flow = write(env, """
        blocks:
          mail:
            type: email.send
            config: {connection: mailer, to: [team@example.com], subject: Nightly report, message: "All loads ran"}
            timeout: 0.5s
            retry: {policy: fixed, max_attempts: 2}
          check:
            type: logic.python
            config: {inputs: [sent], outputs: [], code: "open('sent.txt', 'w', encoding='utf-8').write(sent['subject'])"}
        edges:
          - mail.sent -> check.sent
    """)

    result = env.taskloom("run", flow)

    assert len(smtp.messages) == 1, f"the email was sent {len(smtp.messages)} times"
    assert smtp.messages[0]["Subject"] == "Nightly report"
    run_id, status = env.last_run()
    assert env.query("SELECT status, attempts, error FROM blocks WHERE run_id=? AND block='mail'", run_id) == [
        ("success", 1, None)]
    assert (env.root / "sent.txt").read_text(encoding="utf-8") == "Nightly report"  # its output went on
    assert (result.returncode, status) == (0, "success"), result.stderr


@pytest.mark.parametrize("code", [
    """
    import time
    time.sleep(1.5)  # busy past its 0.5 s timeout, never looking at ctx
    open('rows.txt', 'a', encoding='utf-8').write('row ')
    output = 'loaded'
    """,
    """
    import time
    ctx.check_cancelled()  # it looks only before the stop
    ctx.wait(0.05)
    if ctx.cancelled:
        raise RuntimeError('stopped')
    time.sleep(1.45)
    open('rows.txt', 'a', encoding='utf-8').write('row ')
    output = 'loaded'
    """,
], ids=["it never looks", "it looks only before the stop"])
def test_a_block_that_finishes_in_the_grace_without_seeing_the_stop_keeps_its_result(env, code):
    result = env.taskloom("run", load_flow(env, code))

    assert (env.root / "rows.txt").read_text(encoding="utf-8").split() == ["row"]  # appended once: not retried
    run_id, status = env.last_run()
    assert load_row(env, run_id) == ("success", 1, None)
    assert (env.root / "report.txt").read_text(encoding="utf-8") == "loaded"  # its output went on
    assert (result.returncode, status) == (0, "success"), result.stderr


# --- 2. saw the stop, raised, or still running after the grace: a timeout, as before ---------------------------------

@pytest.mark.parametrize("code", [
    """
    import time
    done = 0
    while done < 30 and not ctx.cancelled:
        time.sleep(0.1)
        done += 1
    output = str(done) + ' of 30 loaded'  # it saw the stop and returns early, with part of its work
    """,
    """
    ctx.wait(30)
    output = 'loaded'
    """,
    """
    try:
        ctx.wait(30)
    except Exception:
        pass  # the stop ended its wait
    output = 'part loaded'
    """,
    """
    import time
    while True:
        if ctx.cancelled:
            raise ConnectionError('the connection closed as it stopped')
        time.sleep(0.05)
    """,
    """
    import time
    time.sleep(1.5)  # never looking at ctx
    raise ValueError('a row did not fit')
    """,
    """
    import time
    time.sleep(4)  # past the grace
    output = 'loaded'
    """,
], ids=["it sees the stop and returns early", "its wait raises at the stop", "it returns after the stop ends its wait",
        "it raises its own error at the stop", "it raises in the grace", "it is still running after the grace"])
def test_a_block_that_saw_the_stop_raised_or_outlived_the_grace_fails_as_a_timeout_and_is_retried(env, code):
    result = env.taskloom("run", load_flow(env, code))

    run_id, status = env.last_run()
    assert load_row(env, run_id) == ("failed", 2, TIMED_OUT)
    assert TIMED_OUT in logs(env, run_id, "load", "ERROR")[0]  # the first attempt failed as a timeout too
    assert env.block_status(run_id)["report"] == "skipped" and not (env.root / "report.txt").exists()
    assert (result.returncode, status) == (1, "failed"), result.stderr


# --- 3. stopping the whole run is unchanged ---------------------------------------------------------------------------

@pytest.mark.parametrize("how", [
    "stop",
    pytest.param("ctrl+c", marks=pytest.mark.skipif(sys.platform == "win32", reason="sends SIGINT")),
])
def test_stopping_the_run_while_a_block_finishes_in_the_grace_still_cancels_it(env, how):
    flow = load_flow(env, """
        import time
        time.sleep(1.5)  # busy, never looking at ctx
        open('finished.txt', 'w', encoding='utf-8').write(repr(time.time()))
        output = 'loaded'
    """, timeout="1m")
    args = ("--json-events", "--interactive") if how == "stop" else ("--json-events",)
    with Running(env, flow, *args) as run:
        run.wait_for('"block_started"')
        stopped = time.time()
        if how == "stop":
            run.send({"cancel": True})  # what the editor's Stop sends
        else:
            run.proc.send_signal(signal.SIGINT)

        assert run.finish() == 130
    finished = env.root / "finished.txt"
    assert finished.exists() and float(finished.read_text(encoding="utf-8")) > stopped, \
        "the block did not finish its work after the stop, in the grace"
    run_id, status = env.last_run()
    assert status == "cancelled"
    assert env.block_status(run_id) == {"load": "cancelled", "report": "cancelled"}
    assert not (env.root / "report.txt").exists()


# --- 4. the design notes say so ---------------------------------------------------------------------------------------

def test_the_design_notes_say_a_block_that_finishes_in_the_grace_keeps_its_result():
    design = (REPO / "docs" / "DESIGN.md").read_text(encoding="utf-8")
    grace = " ".join(" ".join(item.split()) for item in re.split(r"\n\s*- ", design) if "grace" in item)

    assert re.search(r"\b(finish|return|complet)", grace, re.I), grace
    assert re.search(r"\b(result|succeed|success|keep)", grace, re.I), grace
