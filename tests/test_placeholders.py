"""{name} placeholders in every setting you type a value into: a flow parameter, or the value arriving at one of the
block's own inputs (e.g. the Wait block's seconds `{value}`, or `{pause}` for a flow parameter pause); a number setting
takes the filled-in text as a number. What does not fit fails clearly, naming the block, the setting, the placeholder
and the reason; Validate (taskloom validate and check, F7 in the editor) reports a name that is neither a parameter
nor an input of the block, and one that is both, before any run.

Run for real: `taskloom run | validate | check` on flow files (with the run history), and the offscreen editor
(Properties, F7, the help shown on hover)."""

import json
import os
import re
import textwrap

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QHelpEvent, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import events  # noqa: E402
from tests.test_paths_editor_editing import error_shown, field, select_only, settle, type_in  # noqa: E402


# --- helpers -------------------------------------------------------------------------------------------------------

RUN_FAILED = 1  # taskloom run's exit code for a run that ran and failed (2: refused before it began)


def write(path, text: str):
    """A file (a flow, or a block file under home/blocks), written as Taskloom reads it: UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def runs_recorded(env) -> int:
    return env.query("SELECT count(*) FROM runs")[0][0] if (env.home / "history.db").exists() else 0


def error_lines(result) -> list[str]:
    return [line for line in result.stderr.splitlines() if line.startswith("error: ")]


def finished(result, block: str) -> dict:
    """The block_finished event of `block` in a run with --json-events."""
    found = [e for e in events(result.stdout) if e["event"] == "block_finished" and e["block"] == block]
    assert found, result.stdout + result.stderr
    return found[-1]


def failure_told(env, block: str) -> str:
    """The first line of what the last run's Log says about `block` failing, e.g. 'hold (Wait) failed: ...'."""
    run_id, _ = env.last_run()
    messages = [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block=? AND level='ERROR'",
                                        run_id, block)]
    assert messages, f"the Log of run #{run_id} says nothing about {block} failing"
    return messages[-1].splitlines()[0]


def told_before_or_as_it_failed(env, result, block: str) -> str:
    """What the user is told when `block` cannot fill its settings: the error lines of a flow refused before it ran
    (exit code 2, no run), or else the Log's line for the block, which failed."""
    if result.returncode == 2:
        assert runs_recorded(env) == 0, result.stderr
        return "\n".join(line for line in error_lines(result) if block in line)
    assert result.returncode == RUN_FAILED, result.stderr
    run_id, status = env.last_run()
    assert status == "failed" and env.block_status(run_id)[block] == "failed", env.block_status(run_id)
    return failure_told(env, block)


# --- the Wait block: {value} from its value input, {pause} from a flow parameter (the user's case) -------------------

WAIT_FROM_VALUE = """
    blocks:
      start: {type: trigger.manual}
      make: {type: logic.python, config: {inputs: [], code: "output = VALUE"}}
      hold: {type: logic.wait, config: {seconds: "{value}"}}
    edges:
      - start.start -> make.after
      - make.output -> hold.value
"""


@pytest.mark.parametrize("arriving", ["1.2", "' 1.2 '"], ids=["a number", "text with spaces around"])
def test_wait_waits_the_seconds_arriving_at_its_value_input_when_seconds_is_value(env, arriving):
    """Spec 2, 3 (the user's case): Wait's seconds `{value}` is the value arriving at its value input, read as a
    number (text too, surrounding spaces ignored)."""
    flow = write(env.root / "flow.yaml", WAIT_FROM_VALUE.replace("VALUE", arriving))

    result = env.taskloom("run", flow, "--json-events")

    assert result.returncode == 0, result.stderr
    hold = finished(result, "hold")
    assert hold["status"] == "success", hold
    assert 1.2 - 0.05 <= hold["duration"] < 1.2 + 3, hold  # it waited the 1.2 s that arrived


def test_wait_waits_the_seconds_of_a_flow_parameter_and_follows_an_override(env):
    """Spec 2, 3 (the user's case): Wait's seconds `{pause}` is the flow parameter pause, also when a run overrides
    it."""
    flow = write(env.root / "flow.yaml", """
        params:
          pause: {type: float, value: 1.2}
        blocks:
          start: {type: trigger.manual}
          hold: {type: logic.wait, config: {seconds: "{pause}"}}
        edges:
          - start.start -> hold.after
    """)

    as_set = env.taskloom("run", flow, "--json-events")
    overridden = env.taskloom("run", flow, "--json-events", "--param", "pause=0")

    assert as_set.returncode == 0 and overridden.returncode == 0, as_set.stderr + overridden.stderr
    assert 1.2 - 0.05 <= finished(as_set, "hold")["duration"] < 1.2 + 3, finished(as_set, "hold")
    assert finished(overridden, "hold")["duration"] < 1.0, finished(overridden, "hold")


def test_wait_fails_clearly_when_its_filled_in_seconds_is_not_a_number(env):
    """Spec 3, 4: "seconds: {value} gave 'abc', which is not a number": the Wait fails, naming itself, and nothing
    after it runs."""
    flow = write(env.root / "flow.yaml", """
        blocks:
          start: {type: trigger.manual}
          make: {type: logic.python, config: {inputs: [], code: "output = 'abc'"}}
          hold: {type: logic.wait, config: {seconds: "{value}"}}
          next: {type: logic.python, config: {inputs: [], code: "output = 1"}}
        edges:
          - start.start -> make.after
          - make.output -> hold.value
          - hold.value -> next.after
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == RUN_FAILED, result.stderr
    run_id, status = env.last_run()
    assert status == "failed"
    assert env.block_status(run_id)["hold"] == "failed"
    assert env.block_status(run_id).get("next") != "success"
    told = failure_told(env, "hold")
    assert told.startswith("hold "), told  # names the block
    assert "seconds" in told.lower() and "{value} gave 'abc', which is not a number" in told, told


NOT_CONNECTED = """
    blocks:
      start: {type: trigger.manual}
      hold: {type: logic.wait, config: {seconds: "{value}"}}
    edges:
      - start.start -> hold.after
"""


@pytest.mark.parametrize("case", ["not connected", "nothing arrived"])
def test_wait_fails_clearly_when_its_value_input_is_not_connected_or_nothing_arrived_there(env, case):
    """Spec 4: seconds `{value}` with nothing at the value input (not connected; or run on its own with the value
    left empty) fails, naming the block, the setting, the placeholder and why; it is never filled in silently."""
    if case == "not connected":
        flow = write(env.root / "flow.yaml", NOT_CONNECTED)
        result = env.taskloom("run", flow)
    else:  # run on its own (as Run this block does), with its value input left empty
        flow = write(env.root / "flow.yaml", WAIT_FROM_VALUE.replace("VALUE", "1"))
        result = env.taskloom("run", flow, "--only", "hold", "--input", "value=")

    told = told_before_or_as_it_failed(env, result, "hold")
    assert "hold" in told and "seconds" in told.lower() and "{value}" in told, told
    assert "connected" in told or "arrived" in told, told  # why: nothing is there to fill it with


# --- every kind of setting you type a value into, in any block ------------------------------------------------------

SETTINGS_BLOCK = """
    import json
    from taskloom import Block, fields, ports

    class Settings(Block):
        type_id = "test.settings"
        title = "Settings"
        description = "Writes the settings it got to <its name>.json."
        inputs = {"value": ports.Any(required=False), "region": ports.Any(required=False),
                  "when": ports.Any(required=False)}
        config = {
            "label": fields.Text(default=""),
            "count": fields.Int(default=1, minimum=1),
            "ratio": fields.Float(default=0.5),
            "items": fields.List(default=[]),
            "where": fields.Path(default="out.txt"),
            "code": fields.Code("python", default=""),
            "query": fields.Code("sql", default="select 1"),
        }

        def run(self, ctx, **inputs):
            with open(f"{ctx.block_id}.json", "w", encoding="utf-8") as f:
                json.dump(vars(self.config), f)
            return {}
"""


def settings_flow(env, code: str, settings: str, outputs=("value",), params: str = "{}"):
    """A flow: a Python Code block `make` (its `code` sets `outputs`), each output connected to the input of the same
    name of a test.settings block `report` with `settings`."""
    write(env.home / "blocks" / "settings.py", SETTINGS_BLOCK)
    edges = "".join(f"\n      - make.{name} -> report.{name}" for name in outputs)
    return write(env.root / "flow.yaml", f"""
        params: {params}
        blocks:
          start: {{type: trigger.manual}}
          make: {{type: logic.python, config: {{inputs: [], outputs: {json.dumps(list(outputs))}, code: {json.dumps(code)}}}}}
          report: {{type: test.settings, config: {{{settings}}}}}
        edges:
          - start.start -> make.after{edges}
    """.replace("\n      - make", "\n          - make"))


def test_text_path_whole_number_number_and_list_settings_take_parameters_and_inputs_and_code_stays_as_written(env):
    """Spec 1, 2, 3: in a block's text, path, whole number, number and list settings (each item), {name} is a flow
    parameter or the value arriving at the block's input `name`, with {name:format} and {{ }} as today; a number
    setting reads the filled-in text as its number (`{value}0`, spaces around ignored). Python code takes no
    placeholders; SQL takes flow parameters, as today."""
    flow = settings_flow(
        env,
        "import datetime\nvalue = 4\nregion = 'North'\nwhen = datetime.date(2026, 10, 2)",
        "label: '{region} {value:.1f} {when:%d.%m.%Y} {{literal}}', count: '{value}0', ratio: ' {value}.5 ', "
        "items: ['{region}', '{n:02d}', plain, '{{x}}'], where: 'out/{region}/{value}.txt', code: \"x = '{value}'\", "
        "query: 'select {n}'",
        outputs=("value", "region", "when"), params="{n: {type: int, value: 3}}")

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert json.loads((env.root / "report.json").read_text(encoding="utf-8")) == {
        "label": "North 4.0 02.10.2026 {literal}", "count": 40, "ratio": 4.5, "items": ["North", "03", "plain", "{x}"],
        "where": "out/North/4.txt", "code": "x = '{value}'", "query": "select 3"}


def test_sql_code_takes_flow_parameters_but_not_the_blocks_inputs(env):
    """Spec 1 (unchanged): SQL code takes flow parameters only; a placeholder naming one of the block's inputs is
    refused before any run, as today."""
    flow = settings_flow(env, "value = 4", "query: 'select {value}'")

    validated, ran = env.taskloom("validate", flow), env.taskloom("run", flow)

    assert validated.returncode == 2 and ran.returncode == 2, validated.stderr + ran.stderr
    assert any("report" in line and "Query" in line and "{value}" in line for line in error_lines(validated)), \
        validated.stderr
    assert runs_recorded(env) == 0 and not (env.root / "report.json").exists()


FAILS = {
    "a table arrives": ("value = pl.DataFrame({'a': [1, 2]})", "label: '{value}'", "Label", ["table"]),
    "below the minimum": ("value = 0", "count: '{value}'", "Count", [r"\b0\b", r"\b1\b"]),
    "not a whole number": ("value = 2.5", "count: '{value}'", "Count", [r"2\.5"]),
}


@pytest.mark.parametrize("code, settings, label, says", list(FAILS.values()), ids=list(FAILS))
def test_a_setting_that_cannot_take_what_arrived_fails_the_block_before_it_does_anything(env, code, settings, label,
                                                                                         says):
    """Spec 3, 4: a table cannot be written into a setting (only a single value); a whole number setting filled in
    keeps its own limits (its minimum; a whole number). The block fails before it does anything, and the Log names
    the block, the setting, the placeholder and why."""
    flow = settings_flow(env, code, settings)

    result = env.taskloom("run", flow)

    assert result.returncode == RUN_FAILED, result.stderr
    run_id, status = env.last_run()
    assert status == "failed" and env.block_status(run_id)["report"] == "failed", env.block_status(run_id)
    assert not (env.root / "report.json").exists()  # it did nothing
    told = failure_told(env, "report")
    assert told.startswith("report ") and label in told and "{value}" in told, told
    assert all(re.search(pattern, told.lower()) for pattern in says), told


# --- Validate: a name that is neither a parameter nor an input, or both, before any run -----------------------------

NEITHER = """
    params:
      pause: {type: float, value: 1}
    blocks:
      start: {type: trigger.manual}
      typo: {type: logic.wait, config: {seconds: "{pasue}"}}
      run_after: {type: logic.wait, config: {seconds: "{after}"}}
      ask: {type: logic.ask_user, config: {prompt: "Go on after {nope}?"}}
      pick: {type: logic.ask_user, config: {prompt: "Which?", kind: choice, options: ["{nope}", later]}}
    edges:
      - start.start -> typo.after
      - start.start -> run_after.after
      - start.start -> ask.after
      - start.start -> pick.after
"""
# Each block, the setting as Properties names it, and the placeholder that is neither.
NEITHER_SAID = [("typo", "Seconds", "{pasue}"), ("run_after", "Seconds", "{after}"), ("ask", "Prompt", "{nope}"),
                ("pick", "Options", "{nope}")]


def neither_said(lines: list[str]) -> list:
    """Of NEITHER_SAID, the problems no line tells as neither a flow parameter nor an input of the block."""
    return [said for said in NEITHER_SAID
            if not any(all(s in line for s in said) and "neither" in line and "parameter" in line and "input" in line
                       for line in lines)]


def test_a_placeholder_naming_neither_a_parameter_nor_an_input_of_the_block_is_refused_before_any_run(env):
    """Spec 2, 4, 5: a name that is neither a flow parameter nor a data input of its block (its run after is none)
    is reported by taskloom validate and taskloom check and refuses taskloom run, in a number, a text and a list
    setting, naming the block, the setting and the placeholder."""
    flow = write(env.root / "flow.yaml", NEITHER)

    validated, ran, checked = env.taskloom("validate", flow), env.taskloom("run", flow), env.taskloom("check", flow)

    assert validated.returncode == 2 and neither_said(error_lines(validated)) == [], validated.stderr
    assert ran.returncode == 2 and neither_said(error_lines(ran)) == [], ran.stderr
    assert runs_recorded(env) == 0
    assert checked.returncode == 2, checked.stdout
    assert neither_said([line for line in checked.stdout.splitlines() if "needs you" in line]) == [], checked.stdout


BOTH = """
    params:
      value: {type: float, value: 1}
    blocks:
      start: {type: trigger.manual}
      make: {type: logic.python, config: {inputs: [], code: "output = 2"}}
      hold: {type: logic.wait, config: {seconds: "{value}"}}
    edges:
      - start.start -> make.after
      - make.output -> hold.value
"""
BOTH_SAID = "{value} could be the parameter value or the input value: rename one"


def test_a_placeholder_naming_both_a_parameter_and_an_input_of_the_block_is_refused_before_any_run(env):
    """Spec 4, 5: "{value} could be the parameter value or the input value: rename one", from taskloom validate,
    taskloom check and taskloom run (which starts no run), naming the block and the setting."""
    flow = write(env.root / "flow.yaml", BOTH)

    validated, ran, checked = env.taskloom("validate", flow), env.taskloom("run", flow), env.taskloom("check", flow)

    for result in (validated, ran):
        assert result.returncode == 2, result.stderr
        assert any("hold" in line and "Seconds" in line and BOTH_SAID in line for line in error_lines(result)), \
            result.stderr
    assert runs_recorded(env) == 0
    assert checked.returncode == 2 and any("needs you" in line and "hold" in line and BOTH_SAID in line
                                           for line in checked.stdout.splitlines()), checked.stdout


def test_a_well_formed_placeholder_in_a_number_setting_is_no_problem_for_validate(env):
    """Spec 5: `{value}` (connected), `{value}0` and `{pause}` (a flow parameter) in number settings pass
    taskloom validate and taskloom check: they are only read when the block runs."""
    flow = write(env.root / "flow.yaml", """
        params:
          pause: {type: float, value: 1}
        blocks:
          start: {type: trigger.manual}
          make: {type: logic.python, config: {inputs: [], code: "output = 1"}}
          from_input: {type: logic.wait, config: {seconds: "{value}"}}
          tenfold: {type: logic.wait, config: {seconds: "{value}0"}}
          from_param: {type: logic.wait, config: {seconds: "{pause}"}}
        edges:
          - start.start -> make.after
          - make.output -> from_input.value
          - make.output -> tenfold.value
          - start.start -> from_param.after
    """)

    validated, checked = env.taskloom("validate", flow), env.taskloom("check", flow)

    assert validated.returncode == 0, validated.stderr
    assert checked.returncode == 0 and f"{flow}: OK" in checked.stdout, checked.stdout


# --- the editor: Properties, F7, the help on hover ------------------------------------------------------------------

EDITOR_FLOW = """
    params:
      rows: {type: int, value: 50}
    blocks:
      start: {type: trigger.manual}
      make: {type: logic.python, config: {inputs: [], code: "output = 1"}}
      hold: {type: logic.wait, config: {seconds: 1}}
      body: {type: reports.html_template}
    edges:
      - start.start -> make.after
      - make.output -> hold.value
      - start.start -> body.after
"""


def test_properties_takes_a_placeholder_in_a_number_setting_and_f7_finds_no_problem(win, tmp_path):
    """Spec 6, 5: typing {value} into Wait's Seconds, or {rows} into a whole number setting, shows no red mark and
    keeps what you typed; F7 then finds no problem. A number setting without placeholders is as today: a word in it is
    still told."""
    path = write(tmp_path / "flow.yaml", EDITOR_FLOW)
    tab = win.open_flow(path)
    settle()

    select_only(tab, "hold")
    type_in(field(win, "Seconds"), "{value}")
    assert error_shown(win) == ""
    assert tab.doc.blocks["hold"]["config"]["seconds"] == "{value}"
    type_in(field(win, "Seconds"), "soon")
    assert "Seconds" in error_shown(win)  # no placeholder: not a number, as today
    assert tab.doc.blocks["hold"]["config"]["seconds"] == "{value}"
    select_only(tab, "body")
    type_in(field(win, "Max table rows"), "{rows}")
    assert error_shown(win) == ""

    win.validate_action.trigger()  # F7: saves, then checks the flow

    assert win.message_boxes == []
    assert "no problems found" in win.statusBar().currentMessage()
    config = {b: (s.get("config") or {}) for b, s in yaml.safe_load(path.read_text(encoding="utf-8"))["blocks"].items()}
    assert config["hold"]["seconds"] == "{value}" and config["body"]["max_rows"] == "{rows}"


def test_f7_reports_a_placeholder_that_is_neither_a_parameter_nor_an_input_and_one_that_is_both(win, tmp_path):
    """Spec 5: the editor's Validate (F7) tells the same problems as taskloom validate, before any run."""
    path = write(tmp_path / "flow.yaml", """
        params:
          pause: {type: float, value: 1}
          value: {type: float, value: 1}
        blocks:
          start: {type: trigger.manual}
          make: {type: logic.python, config: {inputs: [], code: "output = 1"}}
          typo: {type: logic.wait, config: {seconds: "{pasue}"}}
          hold: {type: logic.wait, config: {seconds: "{value}"}}
        edges:
          - start.start -> make.after
          - start.start -> typo.after
          - make.output -> hold.value
    """)
    tab = win.open_flow(path)
    settle()

    win.validate_action.trigger()

    said = list(win.message_boxes)
    win.message_boxes.clear()
    assert [(kind, title) for kind, (title, _) in said] == [("warning", "The flow has problems")], said
    lines = said[0][1][1].splitlines()
    assert any("typo" in line and "Seconds" in line and "{pasue}" in line and "neither" in line for line in lines), \
        lines
    assert any("hold" in line and "Seconds" in line and BOTH_SAID in line for line in lines), lines
    assert not tab.runs


def help_on_hover(win, label: str) -> str:
    """The help card Properties shows when the mouse rests on the setting labelled `label`, as it reads."""
    widget = field(win, label)
    win.properties.ensureWidgetVisible(widget)
    QApplication.processEvents()
    at = widget.rect().center()
    QApplication.sendEvent(widget, QHelpEvent(QEvent.ToolTip, at, widget.mapToGlobal(at)))
    card = win.properties.card
    assert card.isVisible(), f"resting the mouse on {label} shows no help"
    shown = QTextDocument()
    shown.setHtml(card.text())
    card.dismiss()
    return " ".join(shown.toPlainText().split())


def says_placeholders(text: str) -> bool:
    return "placeholder" in text.lower() or "{name}" in text


def test_the_help_on_hover_of_number_and_text_settings_says_placeholders_can_be_used(win, tmp_path):
    """Spec 6: the help of a whole number, number, text and path setting says placeholders can be used (a choice
    takes none); Wait's Seconds says: a number, {value} for the number arriving at its value input, or {name} for a
    flow parameter."""
    path = write(tmp_path / "flow.yaml", """
        blocks:
          start: {type: trigger.manual}
          hold: {type: logic.wait, config: {seconds: 1}}
          body: {type: reports.html_template}
          launch: {type: servers.start_process, config: {command: "app"}}
          ask: {type: logic.ask_user, config: {prompt: "Go on?"}}
          save: {type: data.write_file, config: {path: out.csv}}
        edges:
          - start.start -> hold.after
    """)
    tab = win.open_flow(path)
    settle()
    shown = {}
    for block, label in (("hold", "Seconds"), ("body", "Max table rows"), ("launch", "Check after"),
                         ("ask", "Prompt"), ("save", "Path"), ("save", "Format")):
        select_only(tab, block)
        shown[block, label] = help_on_hover(win, label)

    wait = shown.pop(("hold", "Seconds"))
    assert "{value}" in wait and "value input" in wait and "{name}" in wait and "flow parameter" in wait, wait
    assert not says_placeholders(shown.pop(("save", "Format"))), "a choice takes no placeholders"
    assert [where for where, text in shown.items() if not says_placeholders(text)] == [], shown
