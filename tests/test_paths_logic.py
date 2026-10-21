"""The Logic blocks (Python Code, If, Wait, Ask User, For Each, Inspect) through their real paths:
`taskloom run` on real flow files with the run history checked, `taskloom env`, and the offscreen editor."""

import datetime as dt
import json
import signal
import sqlite3
import sys
import textwrap
import time
from pathlib import Path

import pytest

from conftest import Env
from test_editor import wait_done, write_flow  # (it also sets up the offscreen Qt platform)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QFileDialog, QLabel, QLineEdit

from taskloom.editor.dialogs import AskDialog, PythonEnvDialog, RunDialog
from taskloom.editor.safety import pid_alive


def write_utf8(env, relpath: str, text: str) -> Path:
    """env.write in UTF-8, as the editor saves flows and Taskloom reads them (env.write uses this computer's
    default encoding, which on Windows cannot hold ✓ or 東京)."""
    path = env.root / relpath
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def logs(env, run_id, block, level=None):
    sql = "SELECT message FROM logs WHERE run_id=? AND block=?" + (" AND level=?" if level else "") + " ORDER BY rowid"
    return [m for (m,) in env.query(sql, run_id, block, *([level] if level else []))]


def row(env, run_id, block) -> dict:
    found = env.query("SELECT status, error, summary, previews FROM blocks WHERE run_id=? AND block=?", run_id, block)
    assert found, f"{block} has no result in run #{run_id}"
    return dict(zip(("status", "error", "summary", "previews"), found[0]))


def stored_text(home) -> str:
    """Everything the run history holds, plus every file of the runs' folders."""
    with sqlite3.connect(home / "history.db") as db:
        text = " ".join(str(v) for table in ("runs", "blocks", "logs")
                        for r in db.execute(f"SELECT * FROM {table}") for v in r)
    for path in (home / "runs").rglob("*"):
        if path.is_file():
            text += path.read_bytes().decode(errors="replace")
    return text


def gone(pid: int, within: float = 5) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not pid_alive(pid):  # (os.kill(pid, 0) would send Ctrl+C on Windows)
            return True
        time.sleep(0.1)
    return False


# --- Python Code -----------------------------------------------------------------------------


def test_python_code_receives_its_inputs_and_params_and_sets_its_outputs(env):
    flow = env.write("flow.yaml", """
        params:
          region: {type: text, value: EU}
        blocks:
          numbers:
            type: logic.python
            config:
              inputs: []
              outputs: [values, factor]
              code: |
                values = [1, 2, 3]
                factor = 10
          scale:
            type: logic.python
            config:
              inputs: [values, factor]
              outputs: [scaled, label]
              code: |
                scaled = [v * factor for v in values]
                label = f"{params['region']}: {sum(scaled)}"
          save:
            type: logic.python
            config:
              inputs: [scaled, label]
              outputs: []
              code: "open('out.txt', 'w').write(repr((scaled, label)))"
          make:
            type: logic.python
            config: {code: "output = 'made with the default ports'"}
          use:
            type: logic.python
            config: {outputs: [], code: "open('default.txt', 'w').write(input)"}
        edges:
          - numbers.values -> scale.values
          - numbers.factor -> scale.factor
          - scale.scaled -> save.scaled
          - scale.label -> save.label
          - make.output -> use.input
    """)
    result = env.taskloom("run", flow, "--param", "region=US")

    assert result.returncode == 0, result.stderr
    assert (env.root / "out.txt").read_text() == repr(([10, 20, 30], "US: 60"))
    assert (env.root / "default.txt").read_text() == "made with the default ports"


def test_an_output_the_code_does_not_set_is_not_produced_and_what_needs_it_is_skipped(env):
    flow = env.write("flow.yaml", """
        blocks:
          pick:
            type: logic.python
            config: {inputs: [], outputs: [found, missing], code: "found = 'yes'"}
          use_found:
            type: logic.python
            config: {inputs: [found], outputs: [], code: "open('found.txt', 'w').write(found)"}
          use_missing:
            type: logic.python
            config: {inputs: [missing], outputs: [], code: "open('missing.txt', 'w').write('ran')"}
          later:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('later.txt', 'w').write('ran')"}
        edges:
          - pick.found -> use_found.found
          - pick.missing -> use_missing.missing
          - use_missing.done -> later.after
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr
    assert (env.root / "found.txt").read_text() == "yes"
    assert not (env.root / "missing.txt").exists() and not (env.root / "later.txt").exists()
    assert row(env, run_id, "use_missing")["status"] == "skipped"
    assert "missing" in row(env, run_id, "use_missing")["summary"]  # a skipped block says why
    assert row(env, run_id, "later")["status"] == "skipped" and row(env, run_id, "later")["summary"]
    assert "missing" not in row(env, run_id, "pick")["summary"]


def test_print_goes_to_the_log_but_print_into_a_file_goes_into_the_file(env):
    flow = write_utf8(env, "flow.yaml", """
        blocks:
          talk:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                import sys
                print("rows", 42, sep="=")
                with open("printed.txt", "w") as f:
                    print("into the file", file=f)
                print("to stderr", file=sys.stderr)
                print("Zürich ✓ 東京")
    """)
    result = env.taskloom("run", flow, "--json-events")
    run_id, status = env.last_run()

    assert status == "success", result.stderr
    said = logs(env, run_id, "talk")
    assert "rows=42" in said and "to stderr" in said and "Zürich ✓ 東京" in said
    assert "into the file" not in " ".join(said)
    assert (env.root / "printed.txt").read_text() == "into the file\n"
    assert all(line.startswith("{") for line in result.stdout.splitlines())  # nothing mixed into the events


def test_an_exception_in_the_code_names_its_type_message_line_and_variables(env):
    flow = env.write("flow.yaml", """
        blocks:
          check:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                regions = ["EU", "US"]
                wanted = "APAC"
                if wanted not in regions:
                    raise ValueError(f"no region {wanted}")
    """)
    result = env.taskloom("run", flow, "--json-events")
    run_id, status = env.last_run()

    assert result.returncode == 1 and status == "failed"
    assert row(env, run_id, "check")["error"] == "ValueError: no region APAC (line 4)"
    [report] = logs(env, run_id, "check", "ERROR")
    assert "❱ 4" in report and 'raise ValueError(f"no region {wanted}")' in report  # the failing line, marked
    assert "wanted = APAC" in report  # the values of your variables
    assert "ctx =" not in report and "print =" not in report  # not Taskloom's helpers
    assert result.stderr == ""  # a failure in the code, not a crash of Taskloom


def test_a_syntax_error_in_the_code_says_which_line(env):
    flow = env.write("flow.yaml", """
        blocks:
          broken:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                total = 1
                if total > 0
                    output = total
    """)
    result = env.taskloom("run", flow, "--json-events")
    run_id, status = env.last_run()

    assert result.returncode == 1 and status == "failed"
    error = row(env, run_id, "broken")["error"]
    assert error.startswith("SyntaxError") and "line 2" in error, error
    assert result.stderr == ""


def test_the_errors_of_several_blocks_reach_one_handler_that_runs_once(env):
    flow = env.write("flow.yaml", """
        blocks:
          first:
            type: logic.python
            config: {inputs: [], outputs: [], code: "raise RuntimeError('first broke')"}
          second:
            type: logic.python
            config: {inputs: [], outputs: [], code: "1 / 0"}
          fine:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
          handler:
            type: logic.python
            config:
              inputs: [errors]
              outputs: []
              code: |
                import json
                with open("errors.json", "a") as f:
                    f.write(json.dumps([[e["block"], e["error"]] for e in errors]) + "\\n")
        edges:
          - first.error -> handler.errors
          - second.error -> handler.errors
          - fine.error -> handler.errors
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr  # the failures were handled
    [line] = (env.root / "errors.json").read_text().splitlines()  # it ran once
    got = json.loads(line)
    assert [block for block, _ in got] == ["first", "second"]
    assert got[0][1].startswith("RuntimeError: first broke") and got[1][1].startswith("ZeroDivisionError")


def test_a_dataframe_the_code_sets_reaches_the_next_block_as_a_table(env):
    flow = env.write("flow.yaml", """
        blocks:
          make:
            type: logic.python
            config: {inputs: [], outputs: [table], code: "table = pl.DataFrame({'region': ['EU', 'US'], 'total': [10, 20]})"}
          save:
            type: data.write_file
            config: {path: out.csv}
        edges:
          - make.table -> save.table
    """)
    result = env.taskloom("run", flow)
    run_id, _ = env.last_run()

    assert result.returncode == 0, result.stderr
    assert (env.root / "out.csv").read_text() == "region,total\nEU,10\nUS,20\n"
    assert row(env, run_id, "make")["summary"] == "table: 2 rows × 2 columns"


def test_unusual_characters_large_values_and_values_that_cannot_be_saved_pass_on_intact(env):
    flow = write_utf8(env, "flow.yaml", """
        blocks:
          make:
            type: logic.python
            config:
              inputs: []
              outputs: [text, many, lazy]
              code: |
                text = 'Zürich ✓ 東京 "quoted" \\'single\\' {braces} \\\\back'
                many = list(range(200_000))
                lazy = (n * 2 for n in range(3))  # a generator: cannot be kept in the run's folder
          save:
            type: logic.python
            config:
              inputs: [text, many, lazy]
              outputs: []
              code: |
                with open("out.txt", "w", encoding="utf-8") as f:
                    f.write(f"{text}|{len(many)}|{sum(many)}|{list(lazy)}")
        edges:
          - make.text -> save.text
          - make.many -> save.many
          - make.lazy -> save.lazy
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr
    expected_text = 'Zürich ✓ 東京 "quoted" \'single\' {braces} \\back'
    assert (env.root / "out.txt").read_text(encoding="utf-8") == f"{expected_text}|200000|{sum(range(200_000))}|[0, 2, 4]"
    assert "Zürich ✓ 東京" in row(env, run_id, "make")["summary"]


@pytest.mark.parametrize("config, problem", [
    ("{inputs: [after], code: 'pass'}", "after: every block already has these; choose other names"),
    ("{outputs: [done], code: 'pass'}", "done: every block already has these; choose other names"),
    ("{outputs: [error], code: 'pass'}", "error: every block already has these; choose other names"),
    ("{inputs: ['my input'], code: 'pass'}", "expected a list of names (letters, digits, _)"),
    ("{inputs: [a, a], code: 'pass'}", "duplicate names"),
    ("{inputs: []}", "config.code is required"),
    ("{code: 'pass', use_environment: 'yes'}", "config.use_environment: expected true or false"),
], ids=["input-after", "output-done", "output-error", "space", "duplicate", "no-code", "use-environment-text"])
def test_python_code_config_that_cannot_work_is_refused_before_running(env, config, problem):
    flow = env.write("flow.yaml", f"""
        blocks:
          code:
            type: logic.python
            config: {config}
    """)
    checked = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow)

    assert checked.returncode == 2 and problem in checked.stderr, checked.stderr
    assert ran.returncode == 2 and problem in ran.stderr
    assert not (env.home / "history.db").exists() or env.query("SELECT count(*) FROM runs") == [(0,)]


def test_runaway_code_is_stopped_by_the_blocks_timeout(env):
    flow = env.write("flow.yaml", """
        blocks:
          loop:
            type: logic.python
            timeout: 1s
            config:
              inputs: []
              outputs: []
              code: |
                while True:
                    pass
    """)
    started = time.monotonic()
    result = env.taskloom("run", flow, timeout=60)

    assert result.returncode == 1
    assert time.monotonic() - started < 20
    run_id, status = env.last_run()
    assert status == "failed" and row(env, run_id, "loop")["error"] == "TimeoutError: timed out after 1 s"


SYS_EXIT_FLOW = """
    blocks:
      stop:
        type: logic.python
        config:
          inputs: []
          outputs: []
          use_environment: {use_environment}
          code: |
            import sys
            nothing_to_do = True
            if nothing_to_do:
                {exit_call}
            open('after_exit.txt', 'w').write('ran')
      handler:
        type: logic.python
        config: {{inputs: [e], outputs: [], code: "open('handled.txt', 'w').write(e['error'])"}}
      later:
        type: logic.python
        config: {{inputs: [], outputs: [], code: "open('later.txt', 'w').write('ran')"}}
    edges:
      - stop.error -> handler.e
      - stop.done -> later.after
"""
SYS_EXIT_SUCCEEDS = ["sys.exit()", "sys.exit(0)"]


def run_sys_exit(env, use_environment, exit_call):
    flow = env.write("flow.yaml", SYS_EXIT_FLOW.format(use_environment=use_environment, exit_call=exit_call))
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()
    assert not (env.root / "after_exit.txt").exists()  # the code ended at sys.exit()
    return result, run_id, status


def check_sys_exit_succeeds(env, result, run_id, status):
    """README: `sys.exit()` ends the code there and the block succeeds."""
    assert row(env, run_id, "stop")["status"] == "success", row(env, run_id, "stop")
    assert result.returncode == 0 and status == "success", result.stderr
    assert (env.root / "later.txt").exists() and not (env.root / "handled.txt").exists()


def check_sys_exit_with_a_value_fails(env, result, run_id, status):
    """README: given a value other than 0 (e.g. `sys.exit("no data")`) the block fails."""
    stop = row(env, run_id, "stop")
    assert stop["status"] == "failed" and "no data" in stop["error"], stop
    assert "no data" in (env.root / "handled.txt").read_text()  # its error handler got the message
    assert row(env, run_id, "later")["status"] == "skipped" and not (env.root / "later.txt").exists()
    assert result.returncode == {"success": 0, "failed": 1}[status], (result.returncode, status)


@pytest.mark.parametrize("exit_call", SYS_EXIT_SUCCEEDS)
def test_sys_exit_in_the_code_ends_the_block_not_the_whole_run(env, exit_call):
    check_sys_exit_succeeds(env, *run_sys_exit(env, "false", exit_call))


def test_sys_exit_with_a_message_fails_the_block_with_that_message(env):
    check_sys_exit_with_a_value_fails(env, *run_sys_exit(env, "false", 'sys.exit("no data")'))


# --- Python Code in your own Python environment ----------------------------------------------


@pytest.fixture(scope="module")
def python_env(tmp_path_factory):
    """A Python environment made once with `taskloom env create`, for the tests below to use through
    the python_env setting (Use an existing environment…)."""
    maker = Env(tmp_path_factory.mktemp("pyenv"))
    made = maker.taskloom("env", "create", "--python", sys.executable, timeout=180)
    assert made.returncode == 0, made.stderr
    assert made.stdout.startswith(f"created {maker.home / 'python-env'}")
    info = maker.taskloom("env", "info")
    assert info.returncode == 0 and info.stdout.strip() == f"{maker.home / 'python-env'}: ready"
    return maker.home / "python-env"


def use(env, python_env):
    env.write("home/settings.yaml", f"python_env: {python_env}\n")


def test_env_commands_without_an_environment_say_what_to_do(env):
    info = env.taskloom("env", "info")
    assert info.returncode == 0 and info.stdout.strip() == f"{env.home / 'python-env'}: not created yet"

    pip = env.taskloom("env", "pip", "list")
    assert pip.returncode == 1 and "create it first (taskloom env create)" in pip.stderr
    assert "Traceback" not in pip.stderr

    env.write("home/settings.yaml", f"python: {env.root / 'no-such-python'}\n")
    made = env.taskloom("env", "create")
    assert made.returncode == 1 and made.stderr.startswith("error:") and "no-such-python" in made.stderr
    assert "Traceback" not in made.stderr
    assert env.taskloom("env", "info").stdout.strip().endswith("not created yet")

    elsewhere = env.root / "my-env"
    env.write("home/settings.yaml", f"python_env: {elsewhere}\n")
    assert env.taskloom("env", "info").stdout.strip() == f"{elsewhere}: not created yet"


def venv_python(folder: Path) -> Path:
    return folder / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def made_by(folder: Path) -> str:
    """The command that made the environment in `folder`, as Python's venv records it in pyvenv.cfg."""
    for line in (folder / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(" = ")
        if key == "command":
            return value
    raise AssertionError(f"{folder} does not say what made it")


def test_env_create_without_python_uses_the_python_setting(env, python_env):
    chosen = venv_python(python_env)  # a Python other than the one Taskloom runs on
    env.write("home/settings.yaml", f"python: {chosen}\n")
    made = env.taskloom("env", "create", timeout=180)

    assert made.returncode == 0, made.stderr
    assert made.stdout.startswith(f"created {env.home / 'python-env'} (Python 3")
    assert made_by(env.home / "python-env").startswith(f"{chosen} -m venv ")
    assert env.taskloom("env", "info").stdout.strip() == f"{env.home / 'python-env'}: ready"


def test_env_create_with_no_python_set_uses_a_python_found_here(env):
    made = env.taskloom("env", "create", timeout=180)

    assert made.returncode == 0, made.stderr
    assert made.stdout.startswith(f"created {env.home / 'python-env'} (Python 3")
    assert made_by(env.home / "python-env").startswith(f"{sys.executable} -m venv ")  # (from source: Taskloom's own)
    assert env.taskloom("env", "info").stdout.strip() == f"{env.home / 'python-env'}: ready"


def test_the_python_environment_screen_with_its_python_field_empty_creates_one_from_a_python_found_here(win, qtbot):
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "settings.yaml").write_text(f"python: {win.home.root / 'no-such-python'}\n")
    dialog = PythonEnvDialog(win.home, win)
    assert dialog.python.text().endswith("no-such-python")
    dialog.python.clear()  # empty: find one on this computer
    dialog.create_button.click()
    qtbot.waitUntil(lambda: dialog.proc is None, timeout=180_000)  # it creates it, then lists its packages

    created = win.home.root / "python-env"
    assert "failed" not in dialog.output.toPlainText(), dialog.output.toPlainText()
    assert made_by(created).startswith(f"{sys.executable} -m venv ")
    assert "In use:" in dialog.status.text() and str(created) in dialog.status.text()
    assert dialog.install_button.isEnabled()


def test_using_a_folder_that_is_not_a_python_environment_warns_and_keeps_the_one_in_use(
        win, qtbot, tmp_path, monkeypatch, python_env):
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "settings.yaml").write_text(f"python_env: {python_env}\n")
    folder = tmp_path / "just-a-folder"
    folder.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(folder)))
    dialog = PythonEnvDialog(win.home, win)
    qtbot.waitUntil(lambda: dialog.proc is None, timeout=120_000)  # it lists the packages first
    dialog.existing_button.click()

    [(kind, (title, text))] = win.message_boxes
    win.message_boxes.clear()
    assert (kind, title) == ("warning", "Not a Python environment") and str(folder) in text, text
    assert win.home.settings()["python_env"] == str(python_env)
    assert str(python_env) in dialog.status.text() and str(folder) not in dialog.status.text()
    assert f"using {folder}" not in dialog.output.toPlainText()


def test_code_in_your_environment_gets_inputs_and_params_prints_to_the_log_and_sets_outputs(env, python_env):
    use(env, python_env)
    flow = env.write("flow.yaml", """
        params:
          day: {type: date, value: "2026-10-02"}
        blocks:
          data:
            type: logic.python
            config: {inputs: [], outputs: [rows], code: "rows = [{'region': 'EU', 'total': 10}, {'region': 'US', 'total': 20}]"}
          outside:
            type: logic.python
            config:
              inputs: [rows]
              outputs: [total, label, never]
              use_environment: true
              code: |
                import sys
                print("rows:", len(rows))
                total = sum(r["total"] for r in rows)
                label = f"{params['day']:%Y-%m-%d} in {sys.prefix}"
          save:
            type: logic.python
            config: {inputs: [total, label], outputs: [], code: "open('out.txt', 'w').write(repr(total) + '|' + label)"}
          use_never:
            type: logic.python
            config: {inputs: [never], outputs: [], code: "open('never.txt', 'w').write('ran')"}
        edges:
          - data.rows -> outside.rows
          - outside.total -> save.total
          - outside.label -> save.label
          - outside.never -> use_never.never
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr
    total, label = (env.root / "out.txt").read_text().split("|")
    assert total == "30" and label == f"2026-10-02 in {python_env}"
    assert "rows: 2" in logs(env, run_id, "outside")
    assert not (env.root / "never.txt").exists() and row(env, run_id, "use_never")["status"] == "skipped"


def test_an_exception_in_your_environment_names_its_type_and_line(env, python_env):
    use(env, python_env)
    flow = env.write("flow.yaml", """
        blocks:
          outside:
            type: logic.python
            config:
              inputs: []
              outputs: []
              use_environment: true
              code: |
                values = [1, 2]
                share = values[0] / (values[1] - 2)
    """)
    result = env.taskloom("run", flow, "--json-events")
    run_id, status = env.last_run()

    assert result.returncode == 1 and status == "failed"
    error = row(env, run_id, "outside")["error"]
    assert "ZeroDivisionError" in error and "line 2" in error, error
    [report] = logs(env, run_id, "outside", "ERROR")
    assert "share = values[0] / (values[1] - 2)" in report  # the code at the failing line
    assert result.stderr == ""


def test_an_exception_in_your_environment_is_reported_as_itself_not_as_a_missing_module(env, python_env):
    use(env, python_env)  # an environment with only what Python itself comes with
    flow = env.write("flow.yaml", """
        blocks:
          outside:
            type: logic.python
            config:
              inputs: []
              outputs: []
              use_environment: true
              code: "raise ValueError('no region APAC')"
    """)
    env.taskloom("run", flow)
    run_id, _ = env.last_run()

    error = row(env, run_id, "outside")["error"]
    assert error.rstrip().splitlines()[-1] == "ValueError: no region APAC", error  # it ends with what went wrong
    assert "No module named" not in error, error


def test_a_failure_in_your_environment_shows_the_values_of_your_variables(env, python_env):
    use(env, python_env)
    flow = env.write("flow.yaml", """
        blocks:
          outside:
            type: logic.python
            config:
              inputs: []
              outputs: []
              use_environment: true
              code: |
                wanted = "APAC"
                raise ValueError("no region " + wanted)
    """)
    env.taskloom("run", flow)
    run_id, _ = env.last_run()

    [report] = logs(env, run_id, "outside", "ERROR")
    assert "wanted = APAC" in report or "wanted = 'APAC'" in report, report


def test_a_failure_in_your_environment_never_shows_a_secret_in_its_variables(env, python_env):
    use(env, python_env)
    env.write("home/secrets.yaml", "pw: hunter2SECRET\n").chmod(0o600)
    flow = env.write("flow.yaml", """
        blocks:
          ask:
            type: logic.ask_user
            config: {prompt: "Password?", kind: password, secret: pw}
          outside:
            type: logic.python
            config:
              inputs: [pw]
              outputs: []
              use_environment: true
              code: |
                url = 'https://example.com/api?' + 'a' * 263 + '&token=' + pw  # the 300-character cut falls inside the password
                raise ValueError('refused')
        edges:
          - ask.answer -> outside.pw
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert status == "failed"
    [report] = logs(env, run_id, "outside", "ERROR")
    assert "Variables" in report and "url = 'https://example.com/api?" in report, report
    assert "hunte" not in result.stdout + result.stderr
    assert "hunte" not in stored_text(env.home)


@pytest.mark.parametrize("exit_call", SYS_EXIT_SUCCEEDS)
def test_sys_exit_in_code_in_your_environment_ends_the_block_clearly(env, python_env, exit_call):
    use(env, python_env)
    check_sys_exit_succeeds(env, *run_sys_exit(env, "true", exit_call))


def test_sys_exit_with_a_message_in_your_environment_fails_the_block_with_that_message(env, python_env):
    use(env, python_env)
    check_sys_exit_with_a_value_fails(env, *run_sys_exit(env, "true", 'sys.exit("no data")'))


def test_code_in_your_environment_is_stopped_by_the_blocks_timeout(env, python_env):
    use(env, python_env)
    flow = env.write("flow.yaml", """
        blocks:
          slow:
            type: logic.python
            timeout: 1s
            config:
              inputs: []
              outputs: []
              use_environment: true
              code: |
                import os, time
                with open("pid.txt", "w") as f:
                    f.write(str(os.getpid()))
                time.sleep(60)
    """)
    started = time.monotonic()
    result = env.taskloom("run", flow, timeout=60)

    assert result.returncode == 1 and time.monotonic() - started < 20
    run_id, _ = env.last_run()
    assert row(env, run_id, "slow")["error"] == "TimeoutError: timed out after 1 s"
    assert gone(int((env.root / "pid.txt").read_text()))  # its Python was stopped, not left running


def test_in_an_environment_with_a_dataframe_library_tables_arrive_and_leave_as_dataframes(env):
    # The Python running these tests has Taskloom's own polars and pyarrow: use it as "an existing environment".
    use(env, Path(sys.prefix))
    flow = env.write("flow.yaml", """
        blocks:
          sales:
            type: data.duckdb_sql
            config: {inputs: [], sql: "select 'EU' as region, 10 as total union all select 'US', 20"}
          outside:
            type: logic.python
            config:
              inputs: [table]
              outputs: [doubled]
              use_environment: true
              code: |
                print("library:", type(table).__module__.split(".")[0], len(table))
                doubled = type(table)({"region": list(table["region"]), "total": [v * 2 for v in table["total"]]})
          save:
            type: data.write_file
            config: {path: out.csv}
        edges:
          - sales.result -> outside.table
          - outside.doubled -> save.table
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", (result.stderr, row(env, run_id, "outside")["error"])
    said = logs(env, run_id, "outside")
    assert "library: polars 2" in said or "library: pandas 2" in said, said  # a DataFrame, not a file path
    assert sorted((env.root / "out.csv").read_text().splitlines()) == ["EU,20", "US,40", "region,total"]


def test_a_pip_command_that_fails_exits_with_a_failure_and_shows_pips_message(env, python_env):
    use(env, python_env)
    result = env.taskloom("env", "pip", "install", "--no-index", "no-such-package", timeout=180)

    assert result.returncode != 0, result.stdout
    assert "no-such-package" in result.stdout  # pip's own message reaches whoever ran it
    assert "Traceback" not in result.stdout + result.stderr


def test_the_python_environment_screen_says_failed_when_pip_fails(win, qtbot, python_env):
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "settings.yaml").write_text(f"python_env: {python_env}\n")
    dialog = PythonEnvDialog(win.home, win)
    qtbot.waitUntil(lambda: dialog.proc is None, timeout=120_000)  # it lists the packages first
    dialog.packages.setText("--no-index no-such-package")
    dialog.install_button.click()
    qtbot.waitUntil(lambda: dialog.proc is None, timeout=180_000)

    after = dialog.output.toPlainText().split("$ taskloom env pip install")[-1].splitlines()
    assert any(line.startswith("failed (exit code") for line in after), after
    assert "done" not in after


# --- If --------------------------------------------------------------------------------------


@pytest.mark.parametrize("value, condition, taken", [
    ("['EU']", "len(value) > 0", "true"),
    ("[]", "len(value) > 0", "false"),
    ("0", "value == 0", "true"),
    ("None", "value is None", "true"),
], ids=["non-empty", "empty", "zero", "none"])
def test_if_sends_its_value_down_one_path_and_skips_the_other(env, value, condition, taken):
    flow = env.write("flow.yaml", f"""
        blocks:
          make:
            type: logic.python
            config: {{inputs: [], code: "output = {value}"}}
          check:
            type: logic.if
            config: {{condition: "{condition}"}}
          on_true:
            type: logic.python
            config: {{inputs: [v], outputs: [], code: "open('true.txt', 'w').write(repr(v))"}}
          on_false:
            type: logic.python
            config: {{inputs: [v], outputs: [], code: "open('false.txt', 'w').write(repr(v))"}}
          after_false:
            type: logic.python
            config: {{inputs: [], outputs: [], code: "open('after_false.txt', 'w').write('ran')"}}
        edges:
          - make.output -> check.value
          - check.true -> on_true.v
          - check.false -> on_false.v
          - on_false.done -> after_false.after
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr
    other = "false" if taken == "true" else "true"
    assert (env.root / f"{taken}.txt").read_text() == value  # the value, unchanged
    assert not (env.root / f"{other}.txt").exists()
    assert row(env, run_id, f"on_{other}")["status"] == "skipped" and row(env, run_id, f"on_{other}")["summary"]
    assert (env.root / "after_false.txt").exists() == (taken == "false")  # what follows the other path is skipped too
    if taken == "true":
        assert row(env, run_id, "after_false")["status"] == "skipped"


@pytest.mark.parametrize("where, taken, rows", [
    ("true", "true", ["EU,10", "US,20"]),
    ("false", "false", []),
], ids=["rows", "no-rows"])
def test_if_on_a_query_result_goes_by_its_rows_and_passes_the_table_on(env, where, taken, rows):
    flow = env.write("flow.yaml", f"""
        blocks:
          sales:
            type: data.duckdb_sql
            config: {{inputs: [], sql: "select * from (select 'EU' as region, 10 as total union all select 'US', 20) where {where}"}}
          check:
            type: logic.if
            config: {{condition: "len(value) > 0"}}
          on_true:
            type: data.write_file
            config: {{path: true.csv}}
          on_false:
            type: data.write_file
            config: {{path: false.csv}}
        edges:
          - sales.result -> check.value
          - check.true -> on_true.table
          - check.false -> on_false.table
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", (result.stderr, row(env, run_id, "check")["error"])
    other = "false" if taken == "true" else "true"
    header, *written = (env.root / f"{taken}.csv").read_text().splitlines()
    assert header == "region,total" and sorted(written) == rows  # the table itself, unchanged
    assert not (env.root / f"{other}.csv").exists() and row(env, run_id, f"on_{other}")["status"] == "skipped"


def test_if_conditions_use_parameters_and_calendar_functions(env):
    flow = env.write("flow.yaml", """
        params:
          region: {type: text, value: EU}
        blocks:
          workday:
            type: logic.if
            config: {condition: "is_business_day(today()) and region == 'EU'"}
          on_workday:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('ran.txt', 'w').write('workday')"}
          on_rest:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('ran.txt', 'w').write('rest')"}
        edges:
          - workday.true -> on_workday.after
          - workday.false -> on_rest.after
    """)
    for as_of, region, expected in (("2026-10-19 09:00", "EU", "workday"),  # a Monday
                                    ("2026-10-17 09:00", "EU", "rest"),  # a Saturday
                                    ("2026-10-19 09:00", "US", "rest")):
        result = env.taskloom("run", flow, "--as-of", as_of, "--param", f"region={region}")
        assert result.returncode == 0, result.stderr
        assert (env.root / "ran.txt").read_text() == expected, (as_of, region)


@pytest.mark.parametrize("condition, problem", [
    ("value > limit", "unknown name 'limit'"),
    ("value >", "invalid expression"),
    ("value.startswith('E')", "unsupported syntax"),
    ("check_region(value)", "unknown function 'check_region'"),
], ids=["unknown-name", "syntax", "attribute", "unknown-function"])
def test_a_condition_that_cannot_be_worked_out_fails_the_if_clearly(env, condition, problem):
    flow = env.write("flow.yaml", f"""
        blocks:
          make:
            type: logic.python
            config: {{inputs: [], code: "output = 'EU'"}}
          check:
            type: logic.if
            config: {{condition: "{condition}"}}
          on_true:
            type: logic.python
            config: {{inputs: [v], outputs: [], code: "pass"}}
          on_false:
            type: logic.python
            config: {{inputs: [v], outputs: [], code: "pass"}}
        edges:
          - make.output -> check.value
          - check.true -> on_true.v
          - check.false -> on_false.v
    """)
    result = env.taskloom("run", flow, "--json-events")
    run_id, status = env.last_run()

    assert result.returncode == 1 and status == "failed" and result.stderr == ""
    assert problem in row(env, run_id, "check")["error"]
    assert env.block_status(run_id)["on_true"] == env.block_status(run_id)["on_false"] == "skipped"


# --- Wait ------------------------------------------------------------------------------------


def test_wait_pauses_then_passes_its_input_on_unchanged(env):
    flow = env.write("flow.yaml", """
        blocks:
          make:
            type: logic.python
            config: {inputs: [], code: "output = {'n': 1, 'items': [1, 2]}"}
          pause:
            type: logic.wait
            config: {seconds: 1.5}
          save:
            type: logic.python
            config: {inputs: [v], outputs: [], code: "open('out.txt', 'w').write(repr(v))"}
          idle:
            type: logic.wait
            config: {seconds: "0.2"}
          last:
            type: logic.python
            config: {inputs: [v], outputs: [], code: "open('idle.txt', 'w').write(repr(v))"}
        edges:
          - make.output -> pause.value
          - pause.value -> save.v
          - idle.value -> last.v
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr
    assert (env.root / "out.txt").read_text() == "{'n': 1, 'items': [1, 2]}"
    assert (env.root / "idle.txt").read_text() == "None"  # nothing connected: nothing to pass on
    [(started, finished)] = env.query("SELECT started, finished FROM blocks WHERE run_id=? AND block='pause'", run_id)
    # (less a clock tick: the history stamps the wall clock, the wait counts on another clock; Windows ticks every ~16 ms)
    assert (dt.datetime.fromisoformat(finished) - dt.datetime.fromisoformat(started)).total_seconds() >= 1.5 - 0.02


# --- Config of every logic block that cannot work --------------------------------------------


@pytest.mark.parametrize("block, problem", [
    ("{type: logic.if}", "config.condition is required"),
    ("{type: logic.wait}", "config.seconds is required"),
    ("{type: logic.wait, config: {seconds: soon}}", "config.seconds"),
    ("{type: logic.ask_user}", "config.prompt is required"),
    ("{type: logic.ask_user, config: {prompt: 'Go?', kind: number}}",
     "config.kind: expected one of ['text', 'password', 'yes_no', 'choice']"),
    ("{type: logic.ask_user, config: {prompt: 'Go?', kind: choice, options: EU}}", "config.options: expected a list"),
    ("{type: logic.ask_user, config: {prompt: 'Region for {country}?'}}", "unknown parameter {country}"),
    ("{type: logic.for_each, config: {param: region}}", "config.flow is required"),
    ("{type: logic.for_each, config: {flow: inner.yaml}}", "config.param is required"),
    ("{type: logic.for_each, config: {flow: inner.yaml, param: region, stop_on_failure: maybe}}",
     "config.stop_on_failure: expected true or false"),
], ids=["if-no-condition", "wait-no-seconds", "wait-not-a-number", "ask-no-prompt", "ask-unknown-kind",
        "ask-options-not-a-list", "ask-unknown-parameter", "each-no-flow", "each-no-param", "each-stop-not-bool"])
def test_logic_block_config_that_cannot_work_is_refused_by_validate(env, block, problem):
    flow = env.write("flow.yaml", f"""
        blocks:
          it: {block}
    """)
    checked = env.taskloom("validate", flow)

    assert checked.returncode == 2 and problem in checked.stderr, checked.stderr
    assert "Traceback" not in checked.stderr


# --- Ask User, when nobody can be asked -------------------------------------------------------


def ask_flow(env, config, extra=""):
    return write_utf8(env, "flow.yaml", f"""
        {extra}
        blocks:
          ask:
            type: logic.ask_user
            config: {config}
          save:
            type: logic.python
            config: {{inputs: [answer], outputs: [], code: "open('answer.txt', 'w', encoding='utf-8').write(repr(answer))"}}
        edges:
          - ask.answer -> save.answer
    """)


@pytest.mark.parametrize("config, expected", [
    ("{prompt: 'Go on?', kind: yes_no, default: 'no'}", "False"),
    ("{prompt: 'Go on?', kind: yes_no, default: 'yes'}", "True"),
    ("{prompt: 'Go on?', kind: yes_no, default: true}", "True"),
    ("{prompt: 'Region?', kind: choice, options: [EU, US], default: US}", "'US'"),
    ("{prompt: 'Region?', kind: text, default: 'São Paulo, 東京'}", "'São Paulo, 東京'"),
], ids=["yes-no-no", "yes-no-yes", "yes-no-bool", "choice", "text"])
def test_ask_user_uses_its_default_answer_in_runs_nobody_can_answer(env, config, expected):
    result = env.taskloom("run", ask_flow(env, config))

    assert result.returncode == 0, result.stderr
    assert (env.root / "answer.txt").read_text(encoding="utf-8") == expected


def test_ask_users_prompt_and_default_fill_in_parameters(env):
    flow = ask_flow(env, "{prompt: 'Region for {region}?', default: '{region}-north'}",
                    extra="params:\n          region: {type: text, value: EU}")
    result = env.taskloom("run", flow, "--param", "region=US")

    assert result.returncode == 0, result.stderr
    assert (env.root / "answer.txt").read_text() == "'US-north'"


@pytest.mark.parametrize("config, problem", [
    ("{prompt: 'Region?', kind: choice, options: [EU, US], default: APAC}", "'APAC' is not one of ['EU', 'US']"),
    ("{prompt: 'Region?', kind: choice, default: EU}", "kind 'choice' needs options"),
    ("{prompt: 'Token?', kind: password, secret: not_stored}", "secret 'not_stored' is not set (run: taskloom secret set not_stored)"),
], ids=["choice-default-not-an-option", "choice-without-options", "secret-not-stored"])
def test_ask_user_without_a_usable_answer_fails_clearly_and_at_once(env, config, problem):
    started = time.monotonic()
    result = env.taskloom("run", ask_flow(env, config), "--json-events")
    run_id, status = env.last_run()

    assert result.returncode == 1 and status == "failed" and result.stderr == ""
    assert time.monotonic() - started < 30
    assert problem in row(env, run_id, "ask")["error"]
    assert row(env, run_id, "save")["status"] == "skipped"


def test_ask_users_secret_is_used_unattended_and_never_shows(env):
    env.write("home/secrets.yaml", "api_token: tok-Zx81-unseen\n").chmod(0o600)
    flow = env.write("flow.yaml", """
        blocks:
          ask:
            type: logic.ask_user
            config: {prompt: "API token?", secret: api_token}
          show:
            type: logic.inspect
            config: {message: "token is {value}"}
          use:
            type: logic.python
            config:
              inputs: [token]
              outputs: [header]
              code: |
                assert token == "tok-Zx81-unseen"
                header = "Bearer " + token
                print("using", header)
        edges:
          - ask.answer -> show.value
          - ask.answer -> use.token
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr
    assert "token is ***" in logs(env, run_id, "show")
    assert "tok-Zx81" not in result.stdout + result.stderr
    assert "tok-Zx81" not in stored_text(env.home)


# Passwords whose text repr() and JSON write differently (a \ or " escaped, ä as \u00e4); each starts and ends with
# three letters and digits found nowhere else, so any form of the password in a text shows by them.
TRICKY_PASSWORDS = pytest.mark.parametrize("password", ['Zq81\\Vx"Ny4', 'Rm5ä\\Tq"Hy8', "Kj4äöPw9x"],
                                           ids=["backslash-and-quote", "accent-backslash-and-quote", "accents"])


def ask_for_password(env, password, blocks, to, edges=""):
    """A flow whose Ask User gives the stored secret `pw` (holding `password`) to the input `to`."""
    env.write("home/secrets.yaml", f"pw: {json.dumps(password)}\n").chmod(0o600)
    return env.write("flow.yaml", f"""
        blocks:
          ask:
            type: logic.ask_user
            config: {{prompt: "Password?", kind: password, secret: pw}}
{blocks}
        edges:
          - ask.answer -> {to}
{edges}
    """)


def where_the_password_shows(env, result, password) -> list[str]:
    """Each place any part of the password shows: the terminal, the run history, the runs' folders."""
    found = []
    for place, text in (("terminal", result.stdout + result.stderr), ("stored", stored_text(env.home))):
        for piece in (password[:3], password[-3:]):
            if (at := text.find(piece)) >= 0:
                found.append(f"{place}: …{text[max(at - 50, 0):at + 30]}…")
    return found


@TRICKY_PASSWORDS
def test_a_password_with_escaped_characters_never_shows_in_summaries_prints_or_inspect(env, password):
    flow = ask_for_password(env, password, """
          build:
            type: logic.python
            config:
              inputs: [pw]
              outputs: [login, parts]
              code: |
                open('got.txt', 'w', encoding='utf-8').write(pw)
                login = 'user:' + pw
                parts = ['token=' + pw]
                print('parts:', parts)
          message:
            type: logic.inspect
            config: {message: "got {[value]}"}
          whole:
            type: logic.inspect""", "build.pw", """
          - build.login -> message.value
          - build.parts -> whole.value""")
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr
    assert (env.root / "got.txt").read_text(encoding="utf-8") == password  # the code got the password itself
    assert len(logs(env, run_id, "message")) == len(logs(env, run_id, "whole")) == 1
    assert where_the_password_shows(env, result, password) == []


@TRICKY_PASSWORDS
def test_a_password_with_escaped_characters_never_shows_in_a_failing_blocks_variables(env, password):
    flow = ask_for_password(env, password, """
          use:
            type: logic.python
            config:
              inputs: [pw]
              outputs: []
              code: |
                open('got.txt', 'w', encoding='utf-8').write(pw)
                parts = ['token=' + pw]
                login = {'user': 'admin', 'password': 'token=' + pw}
                raise ValueError('refused')""", "use.pw")
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 1 and status == "failed"
    assert (env.root / "got.txt").read_text(encoding="utf-8") == password
    [report] = logs(env, run_id, "use", "ERROR")
    assert "Variables" in report and "parts = " in report and "login = " in report, report
    assert where_the_password_shows(env, result, password) == []


@TRICKY_PASSWORDS
def test_a_password_with_escaped_characters_never_shows_in_what_your_environment_prints_or_produces(env, python_env,
                                                                                                     password):
    use(env, python_env)
    flow = ask_for_password(env, password, """
          build:
            type: logic.python
            config:
              inputs: [pw]
              outputs: [login, parts]
              use_environment: true
              code: |
                open('got.txt', 'w', encoding='utf-8').write(pw)
                login = 'user:' + pw
                parts = ['token=' + pw]
                print('parts:', parts)""", "build.pw")
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr
    assert (env.root / "got.txt").read_text(encoding="utf-8") == password
    assert where_the_password_shows(env, result, password) == []


@TRICKY_PASSWORDS
def test_a_password_with_escaped_characters_never_shows_in_a_failure_in_your_environment(env, python_env, password):
    use(env, python_env)
    flow = ask_for_password(env, password, """
          use:
            type: logic.python
            config:
              inputs: [pw]
              outputs: []
              use_environment: true
              code: |
                open('got.txt', 'w', encoding='utf-8').write(pw)
                parts = ['token=' + pw]
                login = {'user': 'admin', 'password': 'token=' + pw}
                raise ValueError('refused')""", "use.pw")
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 1 and status == "failed"
    assert (env.root / "got.txt").read_text(encoding="utf-8") == password
    [report] = logs(env, run_id, "use", "ERROR")
    assert "Variables" in report and "parts = " in report and "login = " in report, report
    assert where_the_password_shows(env, result, password) == []


# --- Ask User, in the editor ------------------------------------------------------------------


def ask_in_editor(win, qtbot, tmp_path, config, answer, save="repr(answer)", extra=""):
    flow = write_flow(tmp_path / "ask.yaml", f"""
        {extra}
        blocks:
          ask:
            type: logic.ask_user
            config: {config}
          save:
            type: logic.python
            config: {{inputs: [answer], outputs: [], code: "open('answer.txt', 'w', encoding='utf-8').write({save})"}}
        edges:
          - ask.answer -> save.answer
    """)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: win.findChild(AskDialog) is not None, timeout=30_000)
    answer(win.findChild(AskDialog))
    wait_done(qtbot, tab)
    return tab


def click(dialog, text):
    button = next(b for b in dialog.findChild(QDialogButtonBox).buttons() if b.text() == text)
    QTest.mouseClick(button, Qt.LeftButton)


def test_ask_user_yes_no_in_the_editor_answers_with_its_buttons(win, qtbot, tmp_path):
    def answer(dialog):
        texts = [b.text() for b in dialog.findChild(QDialogButtonBox).buttons()]
        assert "Yes" in texts and "No" in texts and "OK" not in texts
        click(dialog, "No")

    tab = ask_in_editor(win, qtbot, tmp_path, "{prompt: 'Restart the app?', kind: yes_no, default: 'yes'}", answer)

    assert tab.last["status"] == "success"
    assert (tmp_path / "answer.txt").read_text() == "False"  # what you clicked, not the default


def test_ask_users_question_in_the_editor_is_its_prompt_filled_in_with_the_parameters_of_the_run(
        win, qtbot, tmp_path, monkeypatch):
    def run_for_sao_paulo(dialog):  # the Run window: you type the region
        dialog.edits["region"].setText("São Paulo")
        return True

    def answer(dialog):
        shown = [label.text() for label in dialog.findChildren(QLabel)]
        assert "Restart the app in São Paulo?" in shown, shown
        assert dialog.input.text() == "São Paulo-north"
        click(dialog, "OK")

    monkeypatch.setattr(RunDialog, "exec", run_for_sao_paulo)
    ask_in_editor(win, qtbot, tmp_path, "{prompt: 'Restart the app in {region}?', default: '{region}-north'}", answer,
                  extra="params:\n          region: {type: text, value: EU}")

    assert (tmp_path / "answer.txt").read_text(encoding="utf-8") == "'São Paulo-north'"


def test_ask_user_text_in_the_editor_starts_from_its_default(win, qtbot, tmp_path):
    def answer(dialog):
        assert dialog.input.text() == "EU"
        click(dialog, "OK")

    ask_in_editor(win, qtbot, tmp_path, "{prompt: 'Region?', default: EU}", answer)

    assert (tmp_path / "answer.txt").read_text() == "'EU'"


def test_ask_user_choice_in_the_editor_offers_its_options_with_the_default_chosen(win, qtbot, tmp_path):
    def answer(dialog):
        assert [dialog.input.itemText(i) for i in range(dialog.input.count())] == ["EU", "US", "APAC"]
        assert dialog.input.currentText() == "US"
        dialog.input.setCurrentText("APAC")
        click(dialog, "OK")

    ask_in_editor(win, qtbot, tmp_path, "{prompt: 'Region?', kind: choice, options: [EU, US, APAC], default: US}", answer)

    assert (tmp_path / "answer.txt").read_text() == "'APAC'"


def test_a_password_typed_in_the_editor_is_hidden_used_and_never_stored(win, qtbot, tmp_path):
    def answer(dialog):
        assert dialog.input.echoMode() == QLineEdit.Password
        assert dialog.input.text() == ""  # its default is never shown
        dialog.input.setText("Typed-Secret-9")
        click(dialog, "OK")

    tab = ask_in_editor(win, qtbot, tmp_path, "{prompt: 'Password?', kind: password, default: other-pass}", answer,
                        save="str(answer == 'Typed-Secret-9')")

    assert tab.last["status"] == "success"
    assert (tmp_path / "answer.txt").read_text() == "True"
    assert "Typed-Secret-9" not in stored_text(tmp_path / "home")


def test_not_answering_in_the_editor_fails_the_block_and_skips_what_needs_the_answer(win, qtbot, tmp_path):
    tab = ask_in_editor(win, qtbot, tmp_path, "{prompt: 'Region?', default: EU}", lambda d: click(d, "Don't answer"))

    assert tab.last["status"] == "failed"
    assert not (tmp_path / "answer.txt").exists()
    with sqlite3.connect(tmp_path / "home" / "history.db") as db:
        found = dict((b, (s, e)) for b, s, e in db.execute("SELECT block, status, error FROM blocks"))
    assert found["ask"] == ("failed", "RuntimeError: no answer was given")
    assert found["save"][0] == "skipped"


# --- Python Code, in the editor ---------------------------------------------------------------


def test_the_python_code_properties_offer_run_in_my_python_environment(win):
    tab = win.new_flow()
    block = tab.doc.add_block("logic.python", 0, 0)
    tab.scene.nodes[block].setSelected(True)

    labels = [label.text() for label in win.properties.findChildren(QLabel)]
    tab.doc.dirty = False

    assert "Run in my Python environment" in labels, labels  # the README: "Tick Run in my Python environment"


def test_a_name_no_input_can_have_is_refused_in_the_properties(win):
    tab = win.new_flow()
    block = tab.doc.add_block("logic.python", 0, 0)
    tab.scene.nodes[block].setSelected(True)
    field = next(e for e in win.properties.findChildren(QLineEdit) if e.placeholderText() == "input")
    field.setText("after")
    field.editingFinished.emit()
    shown, error, inputs = (not win.properties.error.isHidden(), win.properties.error.text(),
                            tab.doc.blocks[block].get("config", {}).get("inputs"))
    tab.doc.dirty = False

    assert shown and "every block already has these; choose other names" in error, error
    assert inputs in (None, ["input"])  # nothing changed


# --- For Each --------------------------------------------------------------------------------


INNER = """
    params:
      region: {type: text, value: ""}
      day: {type: date, expr: "today()"}
    blocks:
      work:
        type: logic.python
        config:
          inputs: []
          outputs: []
          code: |
            if params["region"] == "bad":
                raise RuntimeError("bad region")
            open(f"done_{params['region']}.txt", "w").write(str(params["day"]))
"""


def each_flow(env, items, stop_on_failure=False, flow="per_item.yaml", param="region"):
    env.write("flows/per_item.yaml", INNER)
    return env.write("flows/outer.yaml", f"""
        blocks:
          regions:
            type: logic.python
            config: {{inputs: [], code: "output = {items}"}}
          each:
            type: logic.for_each
            config: {{flow: {flow}, param: {param}, stop_on_failure: {str(stop_on_failure).lower()}}}
          save:
            type: logic.python
            config: {{inputs: [results], outputs: [], code: "import json; open('results.json', 'w').write(json.dumps(results))"}}
        edges:
          - regions.output -> each.items
          - each.results -> save.results
    """)


def test_for_each_runs_the_flow_once_per_item_at_the_same_time_and_lists_the_results(env):
    # Run from another folder: the flow's path is relative to the flow file.
    result = env.taskloom("run", each_flow(env, "['EU', 'US']"), "--as-of", "2026-10-02 08:00")

    assert result.returncode == 0, result.stderr
    folder = env.root / "flows"
    assert json.loads((folder / "results.json").read_text()) == [
        {"item": "EU", "run_id": 2, "status": "success"}, {"item": "US", "run_id": 3, "status": "success"}]
    # Each run's parameters are computed for the outer run's time, not the time it happened to start.
    assert (folder / "done_EU.txt").read_text() == (folder / "done_US.txt").read_text() == "2026-10-02"
    assert env.query("SELECT id, status FROM runs ORDER BY id") == [(1, "success"), (2, "success"), (3, "success")]
    assert [json.loads(p)["region"] for (p,) in env.query("SELECT params FROM runs WHERE id > 1 ORDER BY id")] == ["EU", "US"]
    said = logs(env, 1, "each")
    assert "region=EU: run #2 success" in said and "region=US: run #3 success" in said


def test_for_each_with_no_items_runs_nothing_and_succeeds(env):
    result = env.taskloom("run", each_flow(env, "[]"))

    assert result.returncode == 0, result.stderr
    assert json.loads((env.root / "flows" / "results.json").read_text()) == []
    assert env.query("SELECT count(*) FROM runs") == [(1,)]


def test_for_each_over_a_flow_with_a_manual_start_runs_its_start_line_and_leaves_unconnected_blocks_off(env):
    env.write("flows/started.yaml", """
        params:
          region: {type: text, value: ""}
        blocks:
          start:
            type: trigger.manual
          work:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                open(f"done_{params['region']}.txt", "w").write("ran")
          switched_off:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                open(f"off_{params['region']}.txt", "w").write("ran")
        edges:
          - start.start -> work.after
    """)
    result = env.taskloom("run", each_flow(env, "['EU', 'US']", flow="started.yaml"))

    assert result.returncode == 0, result.stderr
    folder = env.root / "flows"
    assert json.loads((folder / "results.json").read_text()) == [
        {"item": "EU", "run_id": 2, "status": "success"}, {"item": "US", "run_id": 3, "status": "success"}]
    assert sorted(p.name for p in folder.glob("*.txt")) == ["done_EU.txt", "done_US.txt"]
    for run_id in (2, 3):
        blocks = env.block_status(run_id)
        assert blocks.get("work") == "success" and blocks.get("switched_off") != "success", blocks


@pytest.mark.parametrize("stop_on_failure, runs, done", [
    (False, [("bad", "failed"), ("US", "success")], {"EU", "US"}),
    (True, [("bad", "failed")], {"EU"}),
], ids=["goes-on", "stops"])
def test_for_each_after_a_failed_item_goes_on_unless_told_to_stop(env, stop_on_failure, runs, done):
    result = env.taskloom("run", each_flow(env, "['EU', 'bad', 'US']", stop_on_failure=stop_on_failure))

    assert result.returncode == 1
    inner = env.query("SELECT id, status FROM runs WHERE id > 1 ORDER BY id")
    assert [s for _, s in inner] == ["success"] + [s for _, s in runs]
    assert {p.stem.removeprefix("done_") for p in (env.root / "flows").glob("done_*.txt")} == done
    error = row(env, 1, "each")["error"]
    assert f"1 of {len(inner)} run(s) did not succeed: bad (run #3)" in error
    assert row(env, 1, "save")["status"] == "skipped"


@pytest.mark.parametrize("items, flow, param, problem", [
    ("'EU,US'", "per_item.yaml", "region", "input 'items' must be a list, got str"),
    ("['EU']", "per_item.yaml", "country", "per_item.yaml has no parameter 'country'"),
    ("['EU']", "missing.yaml", "region", "missing.yaml"),
    ("['EU']", "invalid.yaml", "region", "unknown block type 'logic.nothing'"),
], ids=["not-a-list", "unknown-param", "missing-flow", "invalid-flow"])
def test_for_each_that_cannot_run_its_flow_fails_clearly(env, items, flow, param, problem):
    env.write("flows/invalid.yaml", """
        params:
          region: {type: text, value: ""}
        blocks:
          x: {type: logic.nothing}
    """)
    result = env.taskloom("run", each_flow(env, items, flow=flow, param=param), "--json-events")

    assert result.returncode == 1 and result.stderr == ""
    assert problem in row(env, 1, "each")["error"]
    assert env.query("SELECT count(*) FROM runs") == [(1,)]


def inner_wait_running(env) -> bool:
    try:
        return env.query("SELECT count(*) FROM blocks WHERE run_id=2 AND block='pause' AND status='running'") == [(1,)]
    except sqlite3.OperationalError:  # the run history is not there yet
        return False


@pytest.mark.skipif(sys.platform == "win32", reason="sends SIGINT")
def test_ctrl_c_during_for_each_cancels_the_item_running_and_starts_no_more(env):
    env.write("flows/slow.yaml", """
        params:
          region: {type: text, value: ""}
        blocks:
          pause:
            type: logic.wait
            config: {seconds: 60}
    """)
    flow = env.write("flows/outer.yaml", """
        blocks:
          regions:
            type: logic.python
            config: {inputs: [], code: "output = ['EU', 'US']"}
          each:
            type: logic.for_each
            config: {flow: slow.yaml, param: region}
        edges:
          - regions.output -> each.items
    """)
    proc = env.popen("run", flow, "--json-events")
    try:
        deadline = time.monotonic() + 30
        while not inner_wait_running(env) and time.monotonic() < deadline:
            time.sleep(0.1)
        started = time.monotonic()
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=30) == 130
        assert time.monotonic() - started < 10  # not the 60 s the item would take
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert env.query("SELECT id, status FROM runs ORDER BY id") == [(1, "cancelled"), (2, "cancelled")]


# --- Inspect ---------------------------------------------------------------------------------


def inspect_flow(env, message_config, value="{'healthy': True, 'problems': []}", extra=""):
    return env.write("flow.yaml", f"""
        {extra}
        blocks:
          make:
            type: logic.python
            config: {{inputs: [], code: "output = {value}"}}
          look:
            type: logic.inspect
            {message_config}
          save:
            type: logic.python
            config: {{inputs: [v], outputs: [], code: "open('out.txt', 'w').write(repr(v))"}}
        edges:
          - make.output -> look.value
          - look.value -> save.v
    """)


def test_inspect_without_a_message_shows_the_whole_value_and_passes_it_on(env):
    result = env.taskloom("run", inspect_flow(env, ""))
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr
    [shown] = logs(env, run_id, "look")
    assert "healthy" in shown and "true" in shown.lower() and "problems" in shown, shown  # every part of the value
    assert (env.root / "out.txt").read_text() == "{'healthy': True, 'problems': []}"
    assert '"healthy": true' in json.loads(row(env, run_id, "look")["previews"])["value"]  # Data preview


def test_inspect_message_is_filled_in_like_an_f_string_with_params_and_functions(env):
    flow = inspect_flow(env, "config: {message: \"Healthy: {value['healthy']} {value['problems']} on {params['day']} "
                             "({today():%A}), {{literal}}\"}",
                        extra="params:\n          day: {type: date, expr: today()}")
    result = env.taskloom("run", flow, "--as-of", "2026-10-19 09:00")
    run_id, _ = env.last_run()

    assert result.returncode == 0, result.stderr
    assert logs(env, run_id, "look") == ["Healthy: True [] on 2026-10-19 (Monday), {literal}"]
    assert (env.root / "out.txt").read_text() == "{'healthy': True, 'problems': []}"


def test_inspect_message_with_quotes_in_its_text(env):
    flow = inspect_flow(env, "config:\n              message: |-\n                Status \"{value['healthy']}\", it's fine")
    result = env.taskloom("run", flow)
    run_id, _ = env.last_run()

    assert result.returncode == 0, row(env, run_id, "look")["error"]
    assert logs(env, run_id, "look") == ["Status \"True\", it's fine"]


def test_inspect_message_that_cannot_be_filled_in_fails_clearly(env):
    result = env.taskloom("run", inspect_flow(env, "config: {message: \"{value['missing']}\"}"), "--json-events")
    run_id, status = env.last_run()

    assert result.returncode == 1 and status == "failed" and result.stderr == ""
    assert row(env, run_id, "look")["error"] == "ValueError: the message could not be filled in: KeyError: 'missing'"
    assert row(env, run_id, "save")["status"] == "skipped"


def test_inspect_shows_tables_and_values_from_several_blocks(env):
    flow = env.write("flow.yaml", """
        blocks:
          sales:
            type: data.duckdb_sql
            config: {inputs: [], sql: "select 'EU' as region, 10 as total union all select 'US', 20"}
          table_look:
            type: logic.inspect
          a:
            type: logic.python
            config: {inputs: [], code: "output = 'from a'"}
          b:
            type: logic.python
            config: {inputs: [], code: "output = 'from b'"}
          both:
            type: logic.inspect
            config: {message: "got {value}"}
          save:
            type: data.write_file
            config: {path: out.csv}
        edges:
          - sales.result -> table_look.value
          - table_look.value -> save.table
          - a.output -> both.value
          - b.output -> both.value
    """)
    result = env.taskloom("run", flow)
    run_id, status = env.last_run()

    assert result.returncode == 0 and status == "success", result.stderr
    [shown] = logs(env, run_id, "table_look")
    assert shown.startswith("2 rows × 2 columns") and "EU" in shown and "US" in shown
    assert sorted((env.root / "out.csv").read_text().splitlines()) == ["EU,10", "US,20", "region,total"]
    assert logs(env, run_id, "both") == ["got ['from a', 'from b']"]

