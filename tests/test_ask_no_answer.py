"""Ask User's expected failures are clean failures: choosing "Don't answer" in the editor, nobody to answer an
unattended run (no default, no secret), an answer that is not one of the Options, kind choice without Options. Each
says what went wrong in a short message, without Python internals (an exception's class name, a traceback, Taskloom's
files and line numbers), in the Log, on the canvas and in the run history. Otherwise it is an ordinary failure: it
goes out the block's error output (a block connected there runs and the failure is handled), the run fails when
nothing is connected, and the block's retry setting applies as to any failure. Unexpected errors (bugs) keep their
traceback. Through the real editor and the real `taskloom run`."""

import json
import os
import re
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from taskloom.editor.dialogs import AskDialog  # noqa: E402
from tests.test_lines import wait_runs  # noqa: E402
from tests.test_paths_editor_running import click, visible_ask  # noqa: E402

PYTHON_INTERNALS = {
    "a Python traceback": r"Traceback \(most recent call last\)",
    "an exception's class name": r"\b[A-Z]\w*(Error|Exception):",
    "a Python file and line": r'File "[^"]+", line \d+',
    "a Taskloom file": r"taskloom[\\/][\w\\/]*\.py",
    "a line number": r"\bline \d+",
}


def python_internals(text: str) -> list[str]:
    """Each kind of Python internals that `text` shows, with the text around it (none: [])."""
    found = []
    for what, pattern in PYTHON_INTERNALS.items():
        if match := re.search(pattern, text):
            found.append(f"{what}: …{text[max(match.start() - 60, 0):match.end() + 60]}…")
    return found


def write_flow(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def history(home, run_id) -> tuple[dict, str]:
    """The run's blocks in the run history ({block: (status, error, attempts)}) and its Log there."""
    with sqlite3.connect(home / "history.db") as db:
        blocks = {b: (s, e, a) for b, s, e, a in
                  db.execute("SELECT block, status, error, attempts FROM blocks WHERE run_id=?", (run_id,))}
        logs = "\n".join(m for (m,) in db.execute("SELECT message FROM logs WHERE run_id=?", (run_id,)))
    return blocks, logs


# The user's report: a Manual Start, then Ask User of kind text; in the editor you choose "Don't answer".
REPORTED = """
blocks:
  start:
    type: trigger.manual
  ask_user:
    type: logic.ask_user
    config: {prompt: "Which region?", kind: text}
edges:
  - start.start -> ask_user.after
"""


def dont_answer(win, qtbot, tab) -> int:
    """Run the flow from the editor, choose "Don't answer" to its question; the run's id once it has finished."""
    win.run_flow(tab)
    click(visible_ask(qtbot, win), "Don't answer")
    return wait_runs(qtbot, tab, len(tab.runs))


# --- "Don't answer" in the editor ----------------------------------------------------------------------------


def test_dont_answer_fails_ask_user_with_a_clean_message_in_the_log_on_the_canvas_and_in_the_history(
        win, qtbot, tmp_path):
    """Spec 1 (the user's report exactly) and spec 2 (nothing connected to its error output: the run fails)."""
    tab = win.open_flow(write_flow(tmp_path / "region.yaml", REPORTED))

    run_id = dont_answer(win, qtbot, tab)

    assert tab.runs[-1].status == "failed"  # nothing handles the failure: the run fails, as today
    log = win.log.text.toPlainText()
    assert "ask_user" in log and "no answer" in log.lower(), log
    assert python_internals(log) == [], log
    node = tab.scene.nodes["ask_user"]
    on_canvas = "\n".join([node.status_text, node.footer.toolTip(), str(tab.block_info["ask_user"].get("error"))])
    assert node.status == "failed" and "no answer" in node.status_text.lower(), on_canvas
    assert python_internals(on_canvas) == [], on_canvas
    blocks, logs = history(win.home.root, run_id)
    status, error, _ = blocks["ask_user"]
    assert status == "failed" and "no answer" in error.lower(), error
    assert python_internals(error) == [], error
    assert "no answer" in logs.lower() and python_internals(logs) == [], logs


def test_dont_answer_goes_out_the_error_output_to_the_block_connected_there(win, qtbot, tmp_path):
    """Spec 2: the block connected to the error output runs, the failure counts as handled, and the error's value
    says which block failed and the message, with no Python internals in its traceback part."""
    tab = win.open_flow(write_flow(tmp_path / "handled.yaml", """
blocks:
  start:
    type: trigger.manual
  ask_user:
    type: logic.ask_user
    config: {prompt: "Which region?", kind: text}
  handler:
    type: logic.python
    config:
      inputs: [problem]
      outputs: []
      code: |
        import json
        open('problem.json', 'w', encoding='utf-8').write(json.dumps(problem, default=str))
edges:
  - start.start -> ask_user.after
  - ask_user.error -> handler.problem
"""))

    run_id = dont_answer(win, qtbot, tab)

    blocks, _ = history(win.home.root, run_id)
    assert {b: s for b, (s, _, _) in blocks.items()} == {"start": "success", "ask_user": "failed",
                                                          "handler": "success"}
    assert tab.runs[-1].status == "success"  # handled, as for any block
    problem = json.loads((tmp_path / "problem.json").read_text(encoding="utf-8"))
    assert problem["block"] == "ask_user" and "no answer" in problem["error"].lower(), problem
    assert python_internals(problem["error"]) == [], problem
    assert python_internals(str(problem.get("traceback") or "")) == [], problem


def test_dont_answer_with_a_retry_setting_asks_again_and_each_attempt_fails_cleanly(win, qtbot, tmp_path):
    """Spec 4 (the retry setting applies as to any failure: asked again, two attempts) and spec 1 (the Log of
    each failed attempt says no answer was given without Python internals)."""
    tab = win.open_flow(write_flow(tmp_path / "retried.yaml", REPORTED.replace(
        "kind: text}", "kind: text}\n    retry: {policy: fixed, delay: 100ms, max_attempts: 2}")))
    win.run_flow(tab)

    first = visible_ask(qtbot, win)
    click(first, "Don't answer")
    qtbot.waitUntil(lambda: any(d.isVisible() and d is not first for d in win.findChildren(AskDialog)),
                    timeout=60_000)  # asked again
    click(visible_ask(qtbot, win), "Don't answer")
    run_id = wait_runs(qtbot, tab, 1)

    blocks, logs = history(win.home.root, run_id)
    status, error, attempts = blocks["ask_user"]
    assert (status, attempts) == ("failed", 2), blocks
    assert tab.runs[-1].status == "failed"
    log = win.log.text.toPlainText()
    assert log.lower().count("no answer") >= 2, log  # each attempt says why it failed
    assert python_internals(log) == [], log
    assert python_internals(error) == [] and python_internals(logs) == [], logs


# --- unattended runs and answers that cannot be used -----------------------------------------------------------


@pytest.mark.parametrize("config, says", [
    ("{prompt: 'Region?'}", "nobody can answer"),
    ("{prompt: 'Region?', kind: choice, options: [EU, US], default: APAC}", "not one of"),
    ("{prompt: 'Region?', kind: choice, default: EU}", "needs options"),
], ids=["nobody-can-answer", "answer-not-an-option", "choice-without-options"])
def test_ask_users_other_expected_failures_are_clean_in_the_output_and_the_history(env, config, says):
    """Spec 3: nobody can answer an unattended run with no default and no secret; an answer that is not one of the
    Options; kind choice without Options. Each fails the run (nothing handles it) with a clean message."""
    flow = env.write("flow.yaml", f"""
        blocks:
          ask:
            type: logic.ask_user
            config: {config}
          save:
            type: logic.python
            config: {{inputs: [answer], outputs: [], code: "print(answer)"}}
        edges:
          - ask.answer -> save.answer
    """)

    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert (result.returncode, status) == (1, "failed"), result.stderr
    blocks, logs = history(env.home, run_id)
    assert blocks["ask"][0] == "failed" and blocks["save"][0] == "skipped", blocks
    error = blocks["ask"][1]
    assert says in error, error
    assert python_internals(error) == [], error
    assert says in logs and python_internals(logs) == [], logs
    printed = result.stdout + result.stderr
    assert says in printed and python_internals(printed) == [], printed


BUGGY_BLOCK = """
    from taskloom import Block, ports

    def _cached(key):
        raise {error}("the cache is in a bad state")

    class Buggy(Block):
        type_id = "test.buggy"
        outputs = {{"value": ports.Any()}}

        def run(self, ctx):
            return {{"value": _cached("regions")}}
"""


@pytest.mark.parametrize("error", ["RuntimeError", "ValueError"])
def test_an_unexpected_error_keeps_its_traceback(env, error):
    """Spec 3: unexpected errors (bugs) keep their traceback, in the Log and in the error output's value, even when
    they are of the same kinds Ask User raised for its expected failures."""
    env.write("home/blocks/buggy.py", BUGGY_BLOCK.format(error=error))
    flow = env.write("flow.yaml", """
        blocks:
          load:
            type: test.buggy
          handler:
            type: logic.python
            config:
              inputs: [problem]
              outputs: []
              code: |
                import json
                open('problem.json', 'w', encoding='utf-8').write(json.dumps(problem, default=str))
        edges:
          - load.error -> handler.problem
    """)

    result = env.taskloom("run", flow)
    run_id, _ = env.last_run()

    assert result.returncode == 0, result.stderr  # handled
    _, logs = history(env.home, run_id)
    assert "Traceback (most recent call last)" in logs and "buggy.py" in logs, logs
    assert "Traceback (most recent call last)" in result.stderr, result.stderr
    problem = json.loads((env.root / "problem.json").read_text(encoding="utf-8"))
    assert problem["block"] == "load" and "the cache is in a bad state" in problem["error"], problem
    assert "Traceback (most recent call last)" in problem["traceback"] and "_cached" in problem["traceback"], problem
