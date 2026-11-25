"""One brace rule, {{name}} (docs/promises/braces.yaml, rows BR-1..BR-10, agreed with the maintainer on 2026-11-16):
in every setting that is filled, {{name}} is a flow parameter or one of the block's inputs; single braces stay as
typed; {{{{ gives a literal {{; a value is put in once; Inspect's message holds a Python expression in {{ }}; Import
from cURL keeps the body as it was; every shipped example, starter text, help text and docs page uses {{name}}.

Each case of the stage's table of combinations (B1-B20) runs in each of the seven kinds of field the table names, as
the user types it there: a Run Query's SQL (an H2 database), an API Request's URL and its text body (a web server on
this computer), Write File's path, Send Email's message and an HTML Template (an SMTP server on this computer), and
Inspect's message (its line in the run history's Log). Through the real `taskloom validate` and `taskloom run` on flow
files (or, for the quicker cases, the run and Validate they call, in this process), the offscreen editor (Validate,
F7; Import from cURL; Properties), the block registry walked as the engine walks it (BR-5), and the shipped examples,
help texts and documentation pages (BR-8).

What the tests take as given (the promises leave it to the change):
- the warning of BR-4 is a `warning:` line of `taskloom validate`, and a WARNING line of the editor's Log after F7,
  that names the block and the setting as Properties labels it and says "did you mean {{name}}";
- an error of BR-3 is an `error:` line of `taskloom validate`; at the run, an `error:` line of a run refused before it
  began, or the failed block's error in the run history; either names the block and the setting (and the name);
- a single-brace text that cannot be a number (or a size) may be refused by a number setting, as typed: it is never
  filled in;
- Inspect's help tells what single braces are with the words "single brace";
- a single-brace placeholder in a text is {name} or {name:format} outside {{ }}: not ${NAME}, nor a YAML map such as
  {type: x}; Python code (Python Code, If, a Python code block of a docs page) is not looked at.
"""

import csv
import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QTextEdit, QWidget  # noqa: E402

from conftest import REPO  # noqa: E402
from taskloom import engine  # noqa: E402
from taskloom.block import AFTER, fields  # noqa: E402
from taskloom.config import Home  # noqa: E402
from taskloom.editor import docs  # noqa: E402
from taskloom.flow import load_flow, validate  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_api_request import PROXY_VARIABLES, Api  # noqa: E402
from tests.test_curl_import import Site, editing, fill_in, new_api, output, run_flow, setting_key  # noqa: E402
from tests.test_paths_editor_editing import clean, flow_file, select_only, settle  # noqa: E402
from tests.test_paths_reports_email import html_of  # noqa: E402

BLOCK = "braced"  # the block whose setting is under test, in every flow
V = "V7"  # the value of the flow parameter p
PARAMS = {"p": {"type": "text", "value": V}, "a": {"type": "text", "value": "A1"},
          "b": {"type": "text", "value": "B2"}, "q": {"type": "text", "value": "Q9"},
          "day": {"type": "date", "value": dt.date(2026, 11, 16)}}
KINDS = ("sql", "url", "body", "path", "email", "html", "inspect")
# The setting of each kind, as Properties labels it.
LABEL = {"sql": "SQL", "url": "URL", "body": "Content", "path": "Path", "email": "Message", "html": "Template",
         "inspect": "Message"}
# The kinds whose block has an input a single value can arrive at: (that input, the name a placeholder gives it).
INPUT = {"url": ("body", "body"), "email": ("subject", "subject"), "html": ("data", "fed"),
         "inspect": ("value", "value")}
OPEN, CLOSE = "@@[", "]@@"  # around the text where it is shown among other things (an email, the Log)
PATH_START = "out_"  # what a Write File path starts with, before the text under test
NOT_IN_A_WINDOWS_FILE_NAME = set(':"*?<>|')


# --- a setting of each kind, in a flow of its own -------------------------------------------------------------------

def api_request(url: str, **config) -> dict:
    return {"type": "servers.api_request", "config": {"url": url, "method": "POST", **config}}


def send_email(**config) -> dict:
    return {"type": "email.send", "config": {"connection": "mailer", "to": ["team@example.com"], "subject": "Report",
                                             **config}}


def between(text: str) -> str | None:
    """The text between OPEN and CLOSE, or None."""
    start, end = text.find(OPEN), text.find(CLOSE)
    return text[start + len(OPEN):end] if 0 <= start < end else None


class Setting:
    """A setting of one kind in a flow of its own: `typed(text)` writes the flow with `text` typed into it (and around
    it what the block needs to run: e.g. Run Query's SQL is select '<text>' as v), `got()` reads what the setting gave
    when the block used it (None: it was not used; nothing was sent, written or shown)."""

    def __init__(self, request, env, kind: str):
        self.env, self.kind = env, kind
        if kind == "sql":
            request.getfixturevalue("jdbc_env")  # the connection warehouse, an H2 database
        if kind in ("email", "html"):
            self.smtp = request.getfixturevalue("smtp")  # the connection mailer
        if kind in ("url", "body"):
            self.api = Api()
            request.addfinalizer(self.api.close)
        # A request to this computer's web server goes straight to it, not through a proxy of this computer's.
        env.env = {k: v for k, v in env.env.items() if k.lower() not in PROXY_VARIABLES}

    def typed(self, text: str, params=PARAMS, arriving=None) -> Path:
        """The flow, with `text` typed into the setting; `arriving`: the value arriving at the block's input."""
        blocks, edges = {"start": {"type": "trigger.manual"}}, [f"start.start -> {BLOCK}.after"]
        if self.kind == "sql":
            blocks[BLOCK] = {"type": "db.run_query", "config": {"connection": "warehouse",
                                                                "sql": f"select '{text}' as v"}}
            blocks["save"] = {"type": "data.write_file", "config": {"path": "query.csv", "format": "csv"}}
            edges.append(f"{BLOCK}.result -> save.table")
        elif self.kind == "url":
            blocks[BLOCK] = api_request(f"{self.api.base}/t?v={text}")
        elif self.kind == "body":
            blocks[BLOCK] = api_request(f"{self.api.base}/t", body="text", content=text)
        elif self.kind == "path":
            blocks["rows"] = {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": "select 1 as x"}}
            blocks[BLOCK] = {"type": "data.write_file", "config": {"path": PATH_START + text, "format": "csv"}}
            edges += ["start.start -> rows.after", f"rows.result -> {BLOCK}.table"]
        elif self.kind == "email":
            blocks[BLOCK] = send_email(message=OPEN + text + CLOSE)
        elif self.kind == "html":
            blocks[BLOCK] = {"type": "reports.html_template", "config": {"template": OPEN + text + CLOSE}}
            blocks["mail"] = send_email()
            edges.append(f"{BLOCK}.html -> mail.body")
        else:
            blocks[BLOCK] = {"type": "logic.inspect", "config": {"message": OPEN + text + CLOSE}}
        if arriving is not None:  # from a block named as the placeholder names the input (the HTML Template's way)
            blocks["fed"] = {"type": "logic.python", "config": {"inputs": [], "code": f"output = {arriving!r}"}}
            edges += ["start.start -> fed.after", f"fed.output -> {BLOCK}.{INPUT[self.kind][0]}"]
        return flow_file(self.env.root / "flow.yaml", blocks, edges, params=params)

    def got(self) -> str | None:
        root = self.env.root
        if self.kind == "sql":
            if not (root / "query.csv").exists():
                return None
            with (root / "query.csv").open(newline="", encoding="utf-8") as f:
                return list(csv.reader(f))[1][0]
        if self.kind in ("url", "body"):
            sent = self.api.got("/t")
            if not sent:
                return None
            return dict(sent[0]["query"]).get("v") if self.kind == "url" else sent[0]["body"].decode("utf-8")
        if self.kind == "path":
            written = [os.path.relpath(p, root) for p in root.rglob("*")
                       if p.is_file() and os.path.relpath(p, root).startswith(PATH_START)]
            assert len(written) <= 1, f"more than one file written: {written}"
            return written[0][len(PATH_START):] if written else None
        if self.kind in ("email", "html"):
            if not self.smtp.messages:
                return None
            shown = between(html_of(self.smtp.messages[0]))
            return None if shown is None else html.unescape(shown)
        if not (root / "home" / "history.db").exists():
            return None
        shown = [between(m) for (m,) in self.env.query("SELECT message FROM logs WHERE block=?", BLOCK) if OPEN in m]
        return shown[0] if shown else None


@pytest.fixture
def setting(request, env) -> Setting:
    return Setting(request, env, request.param)


def said(result) -> str:
    return (result.stdout or "")[-3000:] + (result.stderr or "")[-3000:]


def listed(found: dict) -> str:
    """Each entry on a line of its own (an assertion would cut a long dict short)."""
    return "\n".join(f"{where}: {what!r}" for where, what in found.items())


def gave(setting: Setting, text: str, **kw) -> str:
    """What the setting gave with `text` typed into it, in a run that succeeded."""
    result = setting.env.taskloom("run", setting.typed(text, **kw))
    got = setting.got()
    assert result.returncode == 0 and got is not None, said(result)
    return got


def windows_file_name(setting: Setting, text: str):
    if setting.kind == "path" and sys.platform == "win32" and NOT_IN_A_WINDOWS_FILE_NAME & set(text):
        pytest.skip(f"{text!r} cannot be in a file name on Windows")


def problems_told(env, result) -> list[str]:
    """What taskloom said was wrong: its error lines and, of a run that began, each failed block's error."""
    lines = [line for line in result.stderr.splitlines() if line.startswith("error: ")]
    if result.returncode == 1:  # (a run that ran and failed; 2: refused before it began)
        run_id, _ = env.last_run()
        lines += [f"{block}: {error}" for block, error in
                  env.query("SELECT block, error FROM blocks WHERE run_id=? AND status='failed'", run_id)]
    return lines


def did_you_mean(result, kind: str, name: str) -> list[str]:
    """The warning lines of taskloom validate that ask, about the setting under test, did you mean {{name}}."""
    return [line for line in result.stderr.splitlines()
            if line.startswith("warning: ") and BLOCK in line and LABEL[kind] in line
            and f"did you mean {{{{{name}}}}}" in line.lower()]


def ran_here(env, flow: Path, **kw) -> tuple[engine.RunResult, dict]:
    """`flow` run as taskloom run runs it, in this process (quicker than starting taskloom; `kw`: only, given, as
    run_flow takes them): (its result, {block: its error} of each block that failed)."""
    result = engine.run_flow(load_flow(flow), discover(None), Home(env.home), **kw)
    return result, dict(env.query("SELECT block, error FROM blocks WHERE run_id=? AND status='failed'",
                                  result.run_id))


# --- B1-B20: each case in each kind of setting ----------------------------------------------------------------------

FILLED = {
    "B1": ("{{p}}", V),
    "B3": ("{{day:%d.%m.%Y}}", "16.11.2026"),
    "B7": ("{{ p }}", V),
    "B7-spaces-around-the-colon": ("{{ day : %d %m %Y }}", "16 11 2026"),
    "B7-space-after-the-colon": ("{{day: %Y}}", "2026"),
    "B8": ("{{a}}{{b}}", "A1B2"),
}


@pytest.mark.invariant("BR-1")
@pytest.mark.parametrize("setting", KINDS, indirect=True)
@pytest.mark.parametrize("typed, expected", list(FILLED.values()), ids=list(FILLED))
def test_double_braces_give_the_parameters_value_formatted_and_with_spaces_inside(setting, typed, expected):
    """B1, B3, B7, B8 (BR-1): {{p}} is p's value; {{day:%d.%m.%Y}} formats the date; {{ p }} is {{p}}, and spaces
    around the colon do not matter either: {{ day : %d %m %Y }} is {{day:%d %m %Y}} (the spaces inside the format
    kept), {{day: %Y}} is {{day:%Y}}; {{a}}{{b}} gives a's value then b's, adjacent."""
    assert gave(setting, typed) == expected


AROUND = {
    "B9": ('{"k": {{p}}}', '{"k": V7}'),
    "B10": ('{"a":{"b":{{p}}}}', '{"a":{"b":V7}}'),
    "B11": ("{{{p}}}", "{V7}"),
}


@pytest.mark.invariant("BR-1")
@pytest.mark.invariant("BR-2")
@pytest.mark.parametrize("setting", KINDS, indirect=True)
@pytest.mark.parametrize("typed, expected", list(AROUND.values()), ids=list(AROUND))
def test_a_placeholder_inside_single_braces_is_filled_and_the_single_braces_stay(setting, typed, expected):
    """B9, B10, B11 (BR-1, BR-2): JSON around a placeholder, ending }}} or }}}}, and {{{p}}}: the placeholder is
    filled, the single braces around it stay."""
    windows_file_name(setting, expected)
    assert gave(setting, typed) == expected


PLAIN = {
    "B16": "a }} b",
    "B18-json": '{"x": [1, {"y": 2}]}',
    "B18-css": "td {padding: 1px}",
    "B18-shell": "${HOME}",
    "B18-regex": r"\d{4}",
}


@pytest.mark.invariant("BR-2")
@pytest.mark.parametrize("setting", KINDS, indirect=True)
@pytest.mark.parametrize("typed", list(PLAIN.values()), ids=list(PLAIN))
def test_single_braces_and_closing_braces_alone_stay_character_for_character(setting, typed):
    """B16, B18 (BR-2): closing braces alone, JSON, CSS, ${HOME} and a regex \\d{4} are sent as typed."""
    windows_file_name(setting, typed)
    assert gave(setting, typed) == typed


LITERAL = {
    "B4": ("{{{{p}}", "{{p}}"),
    "B5-backslash": ("\\{{p}}", "\\" + V),
    "B5-windows-path": ("C:\\data\\{{p}}.csv", f"C:\\data\\{V}.csv"),
}


@pytest.mark.invariant("BR-9")
@pytest.mark.parametrize("setting", KINDS, indirect=True)
@pytest.mark.parametrize("typed, expected", list(LITERAL.values()), ids=list(LITERAL))
def test_four_opening_braces_give_two_and_a_backslash_is_plain_text(setting, typed, expected):
    """B4, B5 (BR-9): {{{{p}} gives the literal {{p}}; a backslash before {{p}} is plain text, so \\{{p}} gives \\V7
    and the Windows path C:\\data\\{{p}}.csv gives C:\\data\\V7.csv."""
    windows_file_name(setting, expected)
    assert gave(setting, typed) == expected


ONCE = {"B12": "{{q}}", "B13": "}}"}


@pytest.mark.invariant("BR-6")
@pytest.mark.parametrize("setting", KINDS, indirect=True)
@pytest.mark.parametrize("value", list(ONCE.values()), ids=list(ONCE))
def test_braces_inside_a_value_are_never_filled_again(setting, value):
    """B12, B13 (BR-6): p's value {{q}} (q being a parameter too) or }} is sent as it is."""
    assert gave(setting, "{{p}}", params={**PARAMS, "p": {"type": "text", "value": value}}) == value


@pytest.mark.invariant("BR-1")
@pytest.mark.parametrize("setting", list(INPUT), indirect=True)
def test_double_braces_give_the_value_arriving_at_the_blocks_input(setting):
    """B20 (BR-1): {{name}} where name is one of the block's inputs (not a parameter) is the value arriving there:
    API Request's body, Send Email's subject, an HTML Template's input named after its block, Inspect's value. (A Run
    Query takes flow parameters only, a Write File's input is a table, and the body arriving at an API Request is
    sent in place of its Content.)"""
    _, name = INPUT[setting.kind]
    assert gave(setting, f"{{{{{name}}}}}", arriving="W3") == "W3"


ERRORS = {"B6": ("{{nope}}", "nope"), "B14": ("{{p", None), "B15": ("{{}}", None)}


@pytest.mark.invariant("BR-3")
@pytest.mark.parametrize("setting", KINDS, indirect=True)
@pytest.mark.parametrize("typed, name", list(ERRORS.values()), ids=list(ERRORS))
def test_a_placeholder_naming_nothing_not_closed_or_empty_is_an_error_at_validate_and_at_the_run(setting, typed,
                                                                                                 name):
    """B6, B14, B15 (BR-3): {{nope}}, {{p with no closing }} and {{}} are an error of taskloom validate and of
    taskloom run, naming the block, the setting (and nope); nothing is sent, written or shown."""
    flow = setting.typed(typed)

    validated, ran = setting.env.taskloom("validate", flow), setting.env.taskloom("run", flow)

    def names_it(lines):
        return any(BLOCK in line and LABEL[setting.kind] in line and (name is None or name in line) for line in lines)

    assert validated.returncode == 2 and names_it(problems_told(setting.env, validated)), said(validated)
    assert ran.returncode != 0 and names_it(problems_told(setting.env, ran)), said(ran)
    assert setting.got() is None


@pytest.mark.invariant("BR-4")
@pytest.mark.invariant("BR-2")
@pytest.mark.parametrize("setting", KINDS, indirect=True)
def test_a_single_brace_parameter_name_is_sent_as_typed_and_validate_asks_did_you_mean(setting):
    """B2 (BR-4, BR-2): {p}, p being a flow parameter, gives one Validate warning naming the block and the setting,
    did you mean {{p}}?; the run sends {p} as typed."""
    validated = setting.env.taskloom("validate", setting.typed("{p}"))

    assert validated.returncode == 0, said(validated)
    assert len(did_you_mean(validated, setting.kind, "p")) == 1, validated.stderr
    assert gave(setting, "{p}") == "{p}"


@pytest.mark.invariant("BR-1")
@pytest.mark.invariant("BR-4")
@pytest.mark.parametrize("setting", KINDS, indirect=True)
def test_single_and_double_braces_in_one_setting_fill_one_and_warn_about_the_other(setting):
    """B17 (BR-1, BR-4): `{p} and {{p}}` gives `{p} and V7`, with one warning, for {p}."""
    validated = setting.env.taskloom("validate", setting.typed("{p} and {{p}}"))

    assert validated.returncode == 0, said(validated)
    assert len(did_you_mean(validated, setting.kind, "p")) == 1, validated.stderr
    assert gave(setting, "{p} and {{p}}") == "{p} and " + V


@pytest.mark.invariant("BR-2")
@pytest.mark.invariant("BR-4")
@pytest.mark.parametrize("setting", KINDS, indirect=True)
def test_a_single_brace_name_of_nothing_stays_with_no_warning(setting):
    """B19 (BR-2, BR-4): {x}, x being neither a parameter nor an input, is sent as typed and Validate says nothing
    about it."""
    validated = setting.env.taskloom("validate", setting.typed("{x}"))

    assert validated.returncode == 0, said(validated)
    assert [line for line in validated.stderr.splitlines() if "did you mean" in line.lower()] == [], validated.stderr
    assert gave(setting, "{x}") == "{x}"


# --- the editor's Validate (F7) shows the warning -------------------------------------------------------------------

@pytest.mark.invariant("BR-4")
def test_editors_validate_warns_did_you_mean_in_each_kind_of_setting(win, tmp_path):
    """B2 (BR-4) in the editor: F7 on a flow with {p} in each kind of setting finds no problem (no message box) and
    its Log has a WARNING for each, naming the block and the setting: did you mean {{p}}?"""
    (win.home.root / "connections.yaml").write_text(yaml.safe_dump({
        "warehouse": {"kind": "jdbc", "driver": "org.h2.Driver", "url": "jdbc:h2:mem:test", "jars": []},
        "mailer": {"kind": "smtp", "host": "127.0.0.1", "port": 25}}), encoding="utf-8")
    shown = {"sql": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": "select '{p}' as v"}},
             "url": api_request("http://127.0.0.1:9/t?v={p}"),
             "body": api_request("http://127.0.0.1:9/t", body="text", content="{p}"),
             "path": {"type": "data.write_file", "config": {"path": "out_{p}.csv"}},
             "email": send_email(message="{p}"),
             "html": {"type": "reports.html_template", "config": {"template": "{p}"}},
             "inspect": {"type": "logic.inspect", "config": {"message": "{p}"}}}
    blocks = {"start": {"type": "trigger.manual"},
              "rows": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": "select 1 as x"}},
              **{f"{BLOCK}_{kind}": spec for kind, spec in shown.items()}}
    edges = [f"start.start -> {block}.after" for block in blocks if block != "start"] + [
        f"rows.result -> {BLOCK}_path.table"]
    tab = win.open_flow(flow_file(tmp_path / "flow.yaml", blocks, edges, params=PARAMS))
    settle()

    win.validate_action.trigger()

    boxes = list(win.message_boxes)
    win.message_boxes.clear()
    assert boxes == [], boxes
    warnings = [line for line in win.log.text.toPlainText().splitlines() if re.search(r"\bWARNING\b", line)]
    unwarned = [kind for kind in shown if not any(f"{BLOCK}_{kind}" in line and LABEL[kind] in line
                                                  and "did you mean {{p}}" in line.lower() for line in warnings)]
    assert unwarned == [], warnings
    assert not tab.runs
    clean(win)


# --- BR-9: the Mermaid hexagon; BR-10: Inspect's expressions ---------------------------------------------------------

def inspect_flow(env, message: str, arriving=None, **config) -> Path:
    blocks = {"start": {"type": "trigger.manual"},
              BLOCK: {"type": "logic.inspect", "config": {"message": message, **config}}}
    edges = [f"start.start -> {BLOCK}.after"]
    if arriving is not None:
        blocks["fed"] = {"type": "logic.python", "config": {"inputs": [], "code": f"output = {arriving!r}"}}
        edges += ["start.start -> fed.after", f"fed.output -> {BLOCK}.value"]
    return flow_file(env.root / "flow.yaml", blocks, edges, params=PARAMS)


def logged(env) -> list[str]:
    return [m for (m,) in env.query("SELECT message FROM logs WHERE block=?", BLOCK)]


@pytest.mark.invariant("BR-9")
def test_a_mermaid_hexagon_in_inspects_markdown_is_written_with_four_opening_braces(env):
    """BR-9's own case: in Inspect's Markdown message, the Mermaid hexagon A{{text}} is written A{{{{text}}."""
    result = env.taskloom("run", inspect_flow(env, "```mermaid\nflowchart LR\n  A{{{{text}}\n```", markdown=True))

    assert result.returncode == 0, said(result)
    assert "```mermaid\nflowchart LR\n  A{{text}}\n```" in logged(env), logged(env)


@pytest.mark.invariant("BR-10")
def test_inspects_message_takes_a_python_expression_in_double_braces_and_single_braces_are_plain_text(env):
    """BR-10: {{value}}, {{len(value)}} and {{value["id"]}} work, {{p}} is the parameter's value as everywhere, and
    {"k": 1} stays as typed."""
    message = '{{value}} | {{len(value)}} | {{value["id"]}} | {{p}} | {"k": 1}'

    result = env.taskloom("run", inspect_flow(env, message, arriving={"id": 42, "n": 3}))

    assert result.returncode == 0, said(result)
    assert """{'id': 42, 'n': 3} | 2 | 42 | V7 | {"k": 1}""" in logged(env), logged(env)


STARTING_WITH_A_BRACE = {
    "dict": ('{{ {"a": 1}["a"] }}', "1"),
    "dict-comprehension": ("{{ {k: v for k, v in value.items() if v} }}", "{'id': 42}"),
}


@pytest.mark.invariant("BR-10")
@pytest.mark.parametrize("message, expected", list(STARTING_WITH_A_BRACE.values()), ids=list(STARTING_WITH_A_BRACE))
def test_inspects_expression_may_start_with_a_brace_of_its_own(env, message, expected):
    """BR-10: an expression in {{ }} that starts with { of its own (a dict, a dict comprehension), written with a space
    after {{, passes Validate and gives its value, like any other expression."""
    flow = inspect_flow(env, message, arriving={"id": 42, "n": 0})

    validated, result = env.taskloom("validate", flow), env.taskloom("run", flow)

    assert validated.returncode == 0, said(validated)
    assert result.returncode == 0, said(result)
    assert expected in logged(env), logged(env)


def single_braced(text: str) -> list[str]:
    """Every {...} of `text` outside {{ }}."""
    return re.findall(r"\{[^{}]*\}", re.sub(r"\{\{.*?\}\}", " ", text, flags=re.S))


@pytest.mark.invariant("BR-10")
def test_inspects_help_says_double_braces_hold_an_expression_and_single_braces_are_plain_text():
    """BR-10: Inspect's entry in Help > Documentation (its description, the message's help and examples) shows an
    expression in {{ }} and says what single braces are; each example of the message writes its expressions in
    {{ }}."""
    registry = discover(None)
    inspect = registry.get("logic.inspect")
    page = docs.pages(registry)[docs.category_page(inspect.category)]
    start = page.find('id="logic.inspect"')
    end = page.find("<h2", start)
    entry = html.unescape(re.sub(r"<[^>]+>", " ", page[start:end if end >= 0 else None]))

    assert "{{" in entry and re.search(r"single brace", entry, re.IGNORECASE), entry
    examples = inspect.config["message"].examples
    assert examples and [e for e in examples if "{{" not in e or single_braced(e)] == [], examples


AS_TYPED = {
    "triple double quotes": ('He said """hi""" to {{p}}', None, 'He said """hi""" to V7'),
    "triple single quotes": ("It's '''odd''' for {{p}}", None, "It's '''odd''' for V7"),
    "python's own functions": ("{{sorted(value)}} {{sum(value)}}", [3, 1, 2], "[1, 2, 3] 6"),
}


@pytest.mark.invariant("BR-10")
@pytest.mark.parametrize("message, arriving, expected", list(AS_TYPED.values()), ids=list(AS_TYPED))
def test_inspects_message_shows_triple_quotes_as_typed_and_its_expressions_may_use_pythons_own_functions(
        env, message, arriving, expected):
    """BR-10: the plain text of Inspect's message is shown as typed, three double quotes or three single quotes in it
    too, and an expression in {{ }} may use Python's own functions (sorted, sum), not only the expression functions:
    Validate lets the flow run and the Log shows the message filled in."""
    _, failed = ran_here(env, inspect_flow(env, message, arriving=arriving))

    assert failed == {}, failed
    assert expected in logged(env), logged(env)


# --- BR-5: every setting that is filled, as the engine walks the registry --------------------------------------------

ECHO = '''
import json

from taskloom import Block
from taskloom.registry import discover

BUILT_IN = discover(None)


def _run(self, ctx, **inputs):
    with open(f"{ctx.block_id}.json", "w", encoding="utf-8") as f:
        json.dump(vars(self.config), f)
    return {}


for _n, (_type, _keys) in enumerate(SETTINGS):
    globals()[f"Echo{_n}"] = type(f"Echo{_n}", (Block,), {
        "__module__": __name__, "type_id": f"test.echo{_n}", "title": f"Echo {_n}",
        "description": f"Writes the settings it got, as filled in, to <its name>.json: those of {_type}.",
        "config": {k: BUILT_IN.get(_type).config[k] for k in _keys}, "run": _run})
'''


def filled_settings() -> list[tuple[str, str, list[str]]]:
    """Every setting with the template flag of every built-in block, as the engine walks them: (block type, setting,
    the settings it is only for, with what they are only for in turn)."""
    found = []
    for type_id, cls in sorted(discover(None).blocks.items()):
        for key, f in cls.config.items():
            if f.template:
                needs, todo = [], list(f.only_if)
                while todo:
                    other = todo.pop(0)
                    if other not in needs:
                        needs.append(other)
                        todo += list(cls.config[other].only_if)
                found.append((type_id, key, needs))
    return found


def typed_as(f, text: str):
    """`text` as the flow file holds it in the setting `f`: in each item of a list; in the key and value of a row."""
    if isinstance(f, fields.List):
        return [text]
    if isinstance(f, fields.KeyValues):
        return [{"key": text, "value": text}]
    return text


def echo(env, settings, typed, first=0) -> dict:
    """Each setting of `settings` (from filled_settings) in a block of its own that writes what it got, filled in,
    `typed` (a function of the setting) typed into it and each setting it is only for set so that it is for it; run.
    {(block type, setting): what it got, None if it got nothing}."""
    registry = discover(None)
    (env.home / "blocks").mkdir(exist_ok=True)
    every = filled_settings()
    (env.home / "blocks" / "echo.py").write_text(
        f"SETTINGS = {[(t, [k, *needs]) for t, k, needs in every]!r}\n" + ECHO, encoding="utf-8")
    blocks, edges = {"start": {"type": "trigger.manual"}}, []
    for n, (type_id, key, needs) in enumerate(every):
        if (type_id, key) not in settings:
            continue
        cls = registry.get(type_id)
        config = {key: typed(cls.config[key])}
        for other in needs:
            wanted = (cls.config[key].only_if.get(other) or next(
                cls.config[k].only_if[other] for k in needs if other in cls.config[k].only_if))[0]
            if wanted is not None:  # (None: left out)
                config[other] = wanted
        blocks[f"e{n}"] = {"type": f"test.echo{n}", "config": config}
        edges.append(f"start.start -> e{n}.after")
    env.taskloom("run", flow_file(env.root / f"echo{first}.yaml", blocks, edges,
                                  params={"p": {"type": "int", "value": 7}}))
    got = {}
    for n, (type_id, key, _) in enumerate(every):
        if (type_id, key) in settings:
            written = env.root / f"e{n}.json"
            got[type_id, key] = json.loads(written.read_text(encoding="utf-8"))[key] if written.exists() else None
    return got


@pytest.mark.invariant("BR-5")
def test_every_setting_that_is_filled_gives_the_value_of_double_braces(env):
    """BR-5: {{p}} (p = 7) typed into every setting of every block that has the template flag (a block of its own
    holding that very setting) gives 7 as the setting reads it: the whole number 7, the number 7.0, or the text 7 (in
    each item of a list, in the key and the value of a row)."""
    registry = discover(None)
    every = {(t, k): registry.get(t).config[k] for t, k, _ in filled_settings()}

    got = echo(env, set(every), lambda f: typed_as(f, "{{p}}"))

    def expected(f):
        return 7 if isinstance(f, fields.Int) else 7.0 if isinstance(f, fields.Float) else typed_as(f, "7")

    wrong = {f"{t}.{k}": got[t, k] for (t, k), f in every.items() if got[t, k] != expected(f)}
    assert len(every) > 80 and wrong == {}, listed(wrong)


def takes_as_typed(f, value) -> bool:
    try:
        f.coerce(value)
        return True
    except ValueError:
        return False


@pytest.mark.invariant("BR-5")
@pytest.mark.invariant("BR-2")
def test_every_setting_that_is_filled_leaves_single_braces_as_typed(env):
    """BR-5, BR-2: {p} typed into every setting of every block that has the template flag is never filled in: a
    setting that takes text gets {p} as typed; a number setting (or one whose text is checked, such as a size) gets
    nothing, never 7."""
    registry = discover(None)
    every = {(t, k): registry.get(t).config[k] for t, k, _ in filled_settings()}
    as_text = {key for key, f in every.items()
               if not isinstance(f, (fields.Int, fields.Float)) and takes_as_typed(f, typed_as(f, "{p}"))}

    texts = echo(env, as_text, lambda f: typed_as(f, "{p}"))
    others = echo(env, set(every) - as_text, lambda f: typed_as(f, "{p}"), first=1)

    changed = {f"{t}.{k}": got for (t, k), got in texts.items() if got != typed_as(every[t, k], "{p}")}
    filled = {f"{t}.{k}": got for (t, k), got in others.items() if got is not None}
    assert len(as_text) > 60 and changed == {} and filled == {}, listed({**changed, **filled})


@pytest.mark.invariant("BR-5")
def test_python_code_is_not_filled(env):
    """BR-5: Python Code's code is run as written: {{p}} and {p} in it are not filled."""
    code = "with open('code.txt', 'w', encoding='utf-8') as f:\n    f.write('{{p}} {p}')"
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "code": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": code}}},
        ["start.start -> code.after"], params=PARAMS)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, said(result)
    assert (env.root / "code.txt").read_text(encoding="utf-8") == "{{p}} {p}"


# A typed value of an input hole (docs/promises/typed-values.yaml, TV-3) is filled like any field: these blocks have the
# input holes of a built-in block that take one (the same port objects), and write what arrived at them.
ECHO_HOLES = '''
import json

from taskloom import Block
from taskloom.registry import discover

BUILT_IN = discover(None)


def _run(self, ctx, **inputs):
    with open(f"{ctx.block_id}.json", "w", encoding="utf-8") as f:
        json.dump(inputs, f, default=repr)
    return {}


for _n, (_type, _holes) in enumerate(HOLES):
    _cls = BUILT_IN.get(_type)
    _inputs = _cls.ports({k: f.default for k, f in _cls.config.items()})[0]
    globals()[f"Holes{_n}"] = type(f"Holes{_n}", (Block,), {
        "__module__": __name__, "type_id": f"test.holes{_n}", "title": f"Holes {_n}",
        "description": f"Writes what arrived at its inputs to <its name>.json: those of {_type}.",
        "inputs": {h: _inputs[h] for h in _holes}, "run": _run})
'''
# Plain holes that take no typed value (TV-5): a dictionary is expected there (no type is one), or the Loop's result,
# which is fed from inside the Loop.
NO_TYPED_VALUE = {("db.run_query", "params"), ("data.statistics", "extra"), ("logic.loop", "result")}


def typed_holes() -> list[tuple[str, list[str]]]:
    """Every input hole of every built-in block that takes a typed value (TV-5), as its block has it set up as added:
    (block type, its holes). Not a table input, a hole that collects many lines, run after, nor NO_TYPED_VALUE."""
    registry, found = discover(None), []
    for type_id, cls in sorted(registry.blocks.items()):
        if registry.sources[type_id] != "built-in" or cls.is_trigger:
            continue
        inputs = cls.ports({k: f.default for k, f in cls.config.items()})[0]
        holes = [h for h, port in inputs.items() if h != AFTER and port.kind != "table" and not port.many
                 and (type_id, h) not in NO_TYPED_VALUE]
        if holes:
            found.append((type_id, holes))
    return found


@pytest.mark.invariant("BR-5")
@pytest.mark.invariant("TV-3")
def test_a_typed_text_in_every_hole_that_takes_one_gives_the_value_of_double_braces(env):
    """BR-5 with TV-3: the typed text {{p}} (p = 7) in every input hole of every built-in block that takes a typed value
    (a block of its own with that block's holes) arrives filled in, as the text 7."""
    every = typed_holes()
    (env.home / "blocks").mkdir(exist_ok=True)
    (env.home / "blocks" / "holes.py").write_text(f"HOLES = {every!r}\n" + ECHO_HOLES, encoding="utf-8")
    blocks, edges = {"start": {"type": "trigger.manual"}}, []
    for n, (_, holes) in enumerate(every):
        blocks[f"h{n}"] = {"type": f"test.holes{n}", "inputs": {h: {"type": "text", "value": "{{p}}"} for h in holes}}
        edges.append(f"start.start -> h{n}.after")

    result = env.taskloom("run", flow_file(env.root / "holes.yaml", blocks, edges,
                                           params={"p": {"type": "int", "value": 7}}))

    got = {}
    for n, (type_id, holes) in enumerate(every):
        written = env.root / f"h{n}.json"
        arrived = json.loads(written.read_text(encoding="utf-8")) if written.exists() else {}
        got.update({f"{type_id}.{h}": arrived.get(h) for h in holes})
    wrong = {where: value for where, value in got.items() if value != "7"}
    assert len(got) >= 9 and wrong == {}, listed(wrong) + "\n" + said(result)


# --- what the run says when a {{name}} cannot be filled in; SQL code; a header's rows ---------------------------------

SINGLE_ONLY = "but only a single value (text, a number, a date or time, yes/no) can be written into a setting"
# What arrives at Wait's value input (None: nothing is connected there; "": nothing arrived, as Run this block with
# the value left empty), as typed for Run this block, and why its seconds {{value}} cannot be filled in.
CANNOT_FILL = {
    "not connected": (None, "nothing is connected to the input value"),
    "nothing arrived": ("", "nothing arrived at the input value"),
    "a table": ("pl.DataFrame({'a': [1]})", f"the input value got a table, {SINGLE_ONLY}"),
    "None": ("None", f"the input value got None, {SINGLE_ONLY}"),
    "a dict": ("{'a': 1}", f"the input value got a dict, {SINGLE_ONLY}"),
}


@pytest.mark.invariant("BR-3")
@pytest.mark.parametrize("typed, why", list(CANNOT_FILL.values()), ids=list(CANNOT_FILL))
def test_a_placeholder_that_cannot_be_filled_fails_its_block_saying_why(env, typed, why):
    """BR-3 at the run: Wait's seconds {{value}} with nothing connected to its value input, nothing arriving there, or
    a table, None or a dict arriving: the block fails, never filled in, and its error names the setting, the
    placeholder and why, in these words."""
    blocks = {"start": {"type": "trigger.manual"}, BLOCK: {"type": "logic.wait", "config": {"seconds": "{{value}}"}}}
    edges = [f"start.start -> {BLOCK}.after"]
    if typed is not None:
        blocks["make"] = {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}}
        edges += ["start.start -> make.after", f"make.output -> {BLOCK}.value"]
    flow = flow_file(env.root / "flow.yaml", blocks, edges, params=PARAMS)

    _, failed = ran_here(env, flow) if typed is None else ran_here(env, flow, only=BLOCK, given={"value": typed})

    assert failed == {BLOCK: f"Seconds: {{{{value}}}}: {why}"}, failed


@pytest.mark.invariant("BR-1")
def test_a_header_that_cannot_be_filled_in_its_format_fails_the_block_naming_headers(env):
    """BR-1: {{name:format}} formats the value; a format the value cannot take ({{p:d}}, p being a text) in a row of
    an API Request's Headers fails the block before it sends anything, naming the setting and why."""
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        BLOCK: api_request("http://127.0.0.1:9/t", headers=[{"key": "X-Count", "value": "{{p:d}}"}])},
        [f"start.start -> {BLOCK}.after"], params=PARAMS)

    _, failed = ran_here(env, flow)

    assert failed == {BLOCK: "Headers: Unknown format code 'd' for object of type 'str'"}, failed


@pytest.mark.invariant("BR-5")
def test_header_rows_are_filled_in_their_key_and_value_and_a_row_not_ticked_is_not_sent(env, monkeypatch):
    """BR-5: an API Request's Headers are filled in each row's key and value ({{p}} gives V7), and a row that is not
    ticked (on: false) is not sent, whatever it holds."""
    for name in [k for k in os.environ if k.lower() in PROXY_VARIABLES]:
        monkeypatch.delenv(name)  # (a request to this computer's web server goes straight to it)
    api = Api()
    try:
        flow = flow_file(env.root / "flow.yaml", {
            "start": {"type": "trigger.manual"},
            BLOCK: api_request(f"{api.base}/t", headers=[{"key": "X-{{p}}", "value": "{{p}}"},
                                                         {"key": "X-Off", "value": "{{p}}", "on": False}])},
            [f"start.start -> {BLOCK}.after"], params=PARAMS)

        _, failed = ran_here(env, flow)
        sent = api.got("/t")
    finally:
        api.close()

    assert failed == {} and len(sent) == 1, failed
    headers = sent[0]["headers"]
    assert headers.get("x-v7") == V and "x-off" not in headers, headers


@pytest.mark.invariant("BR-1")
def test_sql_code_fills_the_parameter_even_where_an_input_of_the_block_has_its_name(env):
    """BR-1 in SQL code, which takes flow parameters only: DuckDB SQL's {{n}} is the parameter n (2), though a table
    arrives at its input n too, which the SQL reads by that name."""
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "rows": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": "select 1 as x"}},
        BLOCK: {"type": "data.duckdb_sql", "config": {"inputs": ["n"], "sql": "select {{n}} as v, x from n"}}},
        ["start.start -> rows.after", f"rows.result -> {BLOCK}.n"], params={"n": {"type": "int", "value": 2}})

    result, failed = ran_here(env, flow)

    assert failed == {}, failed
    assert result.outputs[BLOCK, "result"].scan().collect().to_dicts() == [{"v": 2, "x": 1}]


@pytest.mark.invariant("BR-9")
def test_four_opening_braces_before_a_placeholder_give_two_and_the_placeholder_after_them_is_filled(env):
    """BR-9 with a placeholder after the literal: DuckDB SQL's '{{{{p}} is {{p}}' gives {{p}} is V7, the text before
    the placeholder kept whole, its {{ too."""
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        BLOCK: {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": "select '{{{{p}} is {{p}}' as v"}}},
        [f"start.start -> {BLOCK}.after"], params=PARAMS)

    result, failed = ran_here(env, flow)

    assert failed == {}, failed
    assert result.outputs[BLOCK, "result"].scan().collect().to_dicts() == [{"v": "{{p}} is " + V}]


@pytest.mark.invariant("BR-1")
@pytest.mark.invariant("BR-4")
def test_a_parameter_whose_name_starts_with_a_capital_letter_is_filled_and_warned_about_in_single_braces(env):
    """BR-1, BR-4 with a flow parameter named Size (a capital letter first, as a parameter's name may start): in DuckDB
    SQL, Validate finds no problem with {{Size}} and asks about {Size}: did you mean {{Size}}?; the run gives Size's
    value for {{Size}} and {Size} as typed."""
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        BLOCK: {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": "select {{Size}} as v, '{Size}' as w"}}},
        [f"start.start -> {BLOCK}.after"], params={"Size": {"type": "int", "value": 3}})

    errors, warnings = validate(load_flow(flow), discover(None), Home(env.home))

    assert errors == [], errors
    assert [w for w in warnings if "did you mean" in w] == [
        f"{BLOCK}: SQL: {{Size}} is plain text: did you mean {{{{Size}}}}?"], warnings
    result, failed = ran_here(env, flow)
    assert failed == {}, failed
    assert result.outputs[BLOCK, "result"].scan().collect().to_dicts() == [{"v": 3, "w": "{Size}"}]


@pytest.mark.invariant("BR-3")
def test_a_loops_at_the_same_time_naming_its_result_input_fails_the_loop_naming_the_setting_and_the_placeholder(env):
    """BR-3 at the run: a Loop's At the same time {{result}} names its input result, which only its items give back, so
    it cannot be filled in as the Loop starts: the Loop fails, before any item runs, and its error names the setting
    and {{result}}."""
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "make": {"type": "logic.python", "config": {"inputs": [], "code": "output = [1, 2]"}},
        BLOCK: {"type": "logic.loop", "config": {"at_the_same_time": "{{result}}"}},
        "work": {"type": "logic.python", "config": {"inputs": ["item"], "code": "output = item"}, "inside": BLOCK}},
        ["start.start -> make.after", f"make.output -> {BLOCK}.items", f"{BLOCK}.item -> work.item",
         f"work.output -> {BLOCK}.result"], params=PARAMS)

    _, failed = ran_here(env, flow)

    assert list(failed) == [BLOCK], failed
    assert failed[BLOCK].startswith("At the same time: ") and "{{result}}" in failed[BLOCK], failed


@pytest.mark.invariant("BR-3")
def test_an_item_of_a_list_setting_that_cannot_be_filled_fails_its_block_naming_the_setting(env):
    """BR-3 at the run, in a setting that is a list: Ask User's Options {{value}}, later, with nothing connected to its
    value input, fails the block before it asks anything, its error naming the setting (Options), the placeholder and
    why."""
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        BLOCK: {"type": "logic.ask_user", "config": {"prompt": "Which?", "kind": "choice",
                                                     "options": ["{{value}}", "later"]}}},
        [f"start.start -> {BLOCK}.after"], params=PARAMS)

    _, failed = ran_here(env, flow)

    assert failed == {BLOCK: "Options: {{value}}: nothing is connected to the input value"}, failed


# --- HTML Template: what Validate and a run on its own say about its inputs' names ----------------------------------

@pytest.mark.invariant("BR-1")
def test_an_html_templates_inputs_named_like_a_parameter_are_each_named_in_validates_error(env):
    """BR-1: {{day}} names a flow parameter or an input, so two inputs both named day, day being a parameter too, is an
    error of Validate that names both of them."""
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "a": {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}},
        "b": {"type": "logic.python", "config": {"inputs": [], "code": "output = 2"}},
        BLOCK: {"type": "reports.html_template", "config": {"names": {"a.output": "day", "b.output": "day"},
                                                            "template": "{{day}}"}}},
        ["start.start -> a.after", "start.start -> b.after", f"a.output -> {BLOCK}.data", f"b.output -> {BLOCK}.data"],
        params=PARAMS)

    errors, _ = validate(load_flow(flow), discover(None), Home(env.home))

    assert (f"{BLOCK}: Inputs: day (from a.output, b.output) is also a flow parameter; give the input another name"
            in errors), errors


@pytest.mark.invariant("BR-3")
def test_an_html_template_run_on_its_own_with_an_input_it_has_no_name_for_fails_naming_the_placeholder(env):
    """BR-3 at the run: an HTML Template whose {{w}} names the block connected to it, run on its own with what arrives
    at data typed as coming from another block, fails with the words Validate would use: {{w}} is not an input."""
    flow = flow_file(env.root / "flow.yaml", {
        "w": {"type": "logic.python", "config": {"inputs": [], "code": "output = 'hi'"}},
        BLOCK: {"type": "reports.html_template", "config": {"template": "Hi {{w}}"}}},
        [f"w.output -> {BLOCK}.data"], params=PARAMS)

    _, failed = ran_here(env, flow, only=BLOCK, given={"data": "{'other.output': 'there'}"})

    assert failed == {BLOCK: "ValueError: Template: {{w}} is not an input or a flow parameter (inputs: other; write "
                             "{{{{ for a literal {{)"}, failed


# --- BR-7: Import from cURL keeps the body ---------------------------------------------------------------------------

CURL = "curl -X POST {base}/orders -H 'Content-Type: application/json' -d '{body}'"


def imported_and_run(win, tmp_path, body: str, command: str = CURL) -> tuple[str, bytes]:
    """A curl command (`command`, {base} the web server's address) sending `body` as JSON, imported into a new API
    Request and run: (the Content the flow file holds, the body the web server got)."""
    path = tmp_path / "imported.yaml"
    with editing(win), Site() as site:
        tab, api = new_api(win)
        fill_in(win, command.replace("{base}", site.base).replace("{body}", body))
        result = run_flow(win, tab, path, api)
        assert result.returncode == 0, output(result)
        sent = site.only().body
    content = yaml.safe_load(path.read_text(encoding="utf-8"))["blocks"][api]["config"][setting_key(win, "Content")]
    return content, sent


@pytest.mark.invariant("BR-7")
def test_import_from_curl_keeps_a_json_body_as_it_was_and_the_run_sends_it_byte_for_byte(win, tmp_path):
    """BR-7: a JSON body with nested braces (ending }}}}) and its own spacing is kept as it was in the curl command,
    and the run sends exactly those bytes."""
    body = '{"item":"widget",  "count": 2, "tags": {"a": {"b": [1, {"c": null}]}}}'

    content, sent = imported_and_run(win, tmp_path, body)

    assert sent == body.encode("utf-8")
    assert content == body


@pytest.mark.invariant("BR-7")
@pytest.mark.invariant("BR-9")
def test_import_from_curl_writes_double_braces_in_the_body_with_four_so_the_run_still_sends_them(win, tmp_path):
    """BR-7, BR-9: a curl body holding {{x}} is imported as {{{{x}}, so that the run still sends {{x}}."""
    body = '{"note": "{{x}} stays", "n": {"m": 1}}'

    content, sent = imported_and_run(win, tmp_path, body)

    assert sent == body.encode("utf-8")
    assert content == body.replace("{{", "{{{{")


@pytest.mark.invariant("BR-7")
def test_import_from_curl_keeps_spaces_and_a_tab_at_the_ends_of_the_bodys_lines(win, tmp_path):
    """BR-7: spaces and a tab at the end of lines inside the quoted body are kept, and the run sends them; spaces or a
    tab copied after a line-ending backslash of the command (outside the quotes) still continue the command."""
    body = '{"item": "widget",  \n\t"count": 2,\t\n\t"tags": []}'
    command = "curl -X POST {base}/orders \\  \n  -H 'Content-Type: application/json' \\\t\n  -d '{body}'"

    content, sent = imported_and_run(win, tmp_path, body, command)

    assert sent == body.encode("utf-8")
    assert content == body


# --- BR-8: every shipped example, starter text, help text and docs page ---------------------------------------------

def single_brace_placeholders(text: str) -> list[str]:
    """The {name} and {name:format} of `text` outside {{ }} (not ${NAME}, nor a YAML map such as {type: x})."""
    outside = re.sub(r"\{\{.*?\}\}", " ", text, flags=re.S)
    return re.findall(r"(?<![$])\{[A-Za-z_]\w*(?::[^{}\s][^{}]*)?\}", outside)


def python(f) -> bool:
    """A setting holding Python code (Python Code's code, an If's condition), which is not filled."""
    return isinstance(f, fields.Code) and f.language in ("python", "expression")


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


EXAMPLES = sorted((REPO / "examples").glob("*.yaml"))


@pytest.mark.invariant("BR-8")
@pytest.mark.parametrize("example", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_each_example_flow_writes_placeholders_with_double_braces_and_validates_without_a_did_you_mean(env, example):
    """BR-8: no setting of a shipped example flow has a single-brace {name} naming a flow parameter or an input of its
    block; taskloom validate (with the connections it names) finds no problem and gives no did you mean warning."""
    registry = discover(None)
    flow = yaml.safe_load(example.read_text(encoding="utf-8"))
    connections, single = {}, []
    for block, spec in flow["blocks"].items():
        cls = registry.get(spec["type"])
        config = spec.get("config") or {}
        coming = {e.split("->")[0].strip().split(".")[0] for e in flow.get("edges", [])
                  if e.split("->")[1].strip().split(".")[0] == block}
        names = set(flow.get("params") or {}) | set(cls.inputs) | coming | set((config.get("names") or {}).values())
        for key, value in config.items():
            f = cls.config.get(key)
            if isinstance(f, fields.Connection) and value:
                connections[value] = {"kind": f.kind}
            if f is not None and not python(f):
                single += [(block, key, found) for text in strings(value) for found in single_brace_placeholders(text)
                           if re.match(r"\{(\w+)", found).group(1) in names]
    env.write("home/connections.yaml", yaml.safe_dump(connections))

    validated = env.taskloom("validate", example)

    assert single == []
    assert validated.returncode == 0, said(validated)
    assert [line for line in validated.stderr.splitlines() if "did you mean" in line.lower()] == [], validated.stderr


def help_texts() -> dict[str, str]:
    """Every block's description, and each setting's help, examples and starter text (but Python code's)."""
    texts = {}
    for type_id, cls in sorted(discover(None).blocks.items()):
        texts[f"{type_id}: description"] = cls.description
        for key, f in cls.config.items():
            if python(f):
                continue
            texts[f"{type_id}.{key}: help"] = f.help
            texts.update((f"{type_id}.{key}: example {i + 1}", str(e)) for i, e in enumerate(f.examples))
        for key, value in (getattr(cls, "new_config", None) or {}).items():
            if not python(cls.config.get(key)):
                texts.update((f"{type_id}.{key}: starter text", text) for text in strings(value))
    return texts


@pytest.mark.invariant("BR-8")
@pytest.mark.invariant("RD-2")
def test_every_help_text_starter_text_and_documentation_page_writes_placeholders_with_double_braces():
    """BR-8: no single-brace {name} or {name:format} in the blocks' help, examples and starter texts, in the pages of
    Help > Documentation (the guide and the reference, as the website shows them too) or in README.md and
    docs/guide.md; Python code in them aside."""
    texts = help_texts()
    for name, page in docs.pages(discover(None)).items():
        page = re.sub(r'(?s)<code class="language-python">.*?</code>', " ", page)
        texts[f"Help > Documentation, {name}"] = html.unescape(re.sub(r"<[^>]+>", " ", page))
    for name in ("README.md", "docs/guide.md"):
        texts[name] = re.sub(r"(?s)```python.*?```", " ", (REPO / name).read_text(encoding="utf-8"))

    found = {where: single for where, text in texts.items() if (single := single_brace_placeholders(text or ""))}

    assert found == {}, listed(found)


@pytest.mark.invariant("BR-8")
def test_properties_shows_no_single_brace_placeholder_in_the_help_of_any_block(win, tmp_path):
    """BR-8 in the editor: what Properties shows for each built-in block (its notes, the hints in empty boxes, the
    tooltips), an HTML Template with an input connected and an API Request with a connection chosen among them, has
    no single-brace {name}."""
    (win.home.root / "connections.yaml").write_text(
        yaml.safe_dump({"api": {"kind": "api", "base_url": "http://127.0.0.1:9"}}), encoding="utf-8")
    types = sorted(t for t, source in win.registry.sources.items() if source == "built-in")
    blocks = {f"b{n}": {"type": t, "ui": {"x": 300 * (n % 6), "y": 200 * (n // 6)}} for n, t in enumerate(types)}
    template = next(b for b, spec in blocks.items() if spec["type"] == "reports.html_template")
    request = next(b for b, spec in blocks.items() if spec["type"] == "servers.api_request")
    blocks[request]["config"] = {"connection": "api"}
    blocks["src"] = {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}, "ui": {"x": 0, "y": -300}}
    tab = win.open_flow(flow_file(tmp_path / "flow.yaml", blocks, [f"src.output -> {template}.data"]))
    settle()

    found = {}
    for block, spec in blocks.items():
        select_only(tab, block)
        body = win.properties.widget()
        shown = [w.text() for w in body.findChildren(QLabel) if w.isVisibleTo(win)]
        shown += [w.placeholderText() for kind in (QLineEdit, QPlainTextEdit, QTextEdit)
                  for w in body.findChildren(kind) if w.isVisibleTo(win)]
        shown += [w.toolTip() for w in body.findChildren(QWidget) if w.isVisibleTo(win)]
        single = [s for text in shown for s in single_brace_placeholders(html.unescape(re.sub(r"<[^>]+>", " ", text)))]
        if single:
            found[spec["type"]] = single
    clean(win)

    assert found == {}, listed(found)
