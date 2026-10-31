"""A new block where you write code (Run Query, DuckDB SQL, Polars Transform, HTML Template) starts with short comments
that teach how to use it, and a new If block's condition with the user's example line; the comments change nothing: only
comments is an empty setting, and once code is written below or between them the run ignores them. Inspect keeps an
empty message, and existing flows keep what they have. Through the offscreen editor (blocks added from the Blocks
list), the real `taskloom` command line, a real SMTP server and a real H2 database.

How the tests find a starter's examples: as in the Python Code block's starter, example code is written indented
under the text that explains it, e.g.

    -- Parameters: :name is a flow parameter's value, e.g.
    --   select * from input where day = :day

A comment line whose text (after its comment marker and one space) is indented more than the starter's least indented
text is example code; consecutive such lines are one example. Uncommented, each example must work in a real flow."""

import os
import re
import string
import textwrap
from html.parser import HTMLParser

import polars as pl
import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from taskloom.editor.canvas import BLOCK_MIME  # noqa: E402
from taskloom.editor.panels import CodeEdit  # noqa: E402

# The user's line, verbatim (spec 3).
IF_LINE = "# No need to delete this. Examples: value > 10, value is True, value == 'Option A'"
# Each example of that line, with a value it is true for and one it is false for.
IF_EXAMPLES = {"value > 10": (11, 10), "value is True": (True, 1), "value == 'Option A'": ("Option A", "Option B")}

# The blocks where you write code, by their title in the Blocks list: the setting that holds the code and the language
# of its comments.
CODE = {"DuckDB SQL": ("sql", "sql"), "Run Query": ("sql", "sql"), "Polars Transform": ("code", "python"),
        "HTML Template": ("template", "html"), "If": ("condition", "python")}
# Each language's comments: a line comment's marker, and the markers that open and close a comment of several lines.
COMMENTS = {"sql": ("--", "/*", "*/"), "python": ("#", None, None), "html": (None, "<!--", "-->")}

SOURCE_SQL = "select * from (values (1), (2), (3)) t(x)"  # three rows, column x
DAY = {"type": "date", "value": "2026-10-30"}
AMOUNT = {"type": "float", "value": 1234.5}
# What an uncommented example may fail with only because the table it is given is not the user's: a column it names is
# not there (DuckDB, Polars, H2), or, for Run Query, a table of the user's database.
MISSING_COLUMN = re.compile(r'(?i)referenced column "[^"]+" not found|column "[^"]+" (?:not found|does not exist)'
                            r'|unable to find column')
MISSING_TABLE = re.compile(r'(?i)table "[^"]+" not found')
SQL_TABLES = re.compile(r"(?i)\b(?:from|join)\s+([A-Za-z_]\w*)\b(?!\s*\()")  # the tables a query reads


# --- the editor ----------------------------------------------------------------------------------------------------

def settle():
    for _ in range(3):
        QApplication.processEvents()


def tree_item(win, title):
    return win.palette_panel.tree.findItems(title, Qt.MatchExactly | Qt.MatchRecursive)[0]


def added(tab, add) -> str:
    """The name of the one block `add()` adds to the flow."""
    before = set(tab.doc.blocks)
    add()
    settle()
    (block,) = set(tab.doc.blocks) - before
    return block


def double_click_in_tree(win, tab, title) -> str:
    """As the user does: double-click the block in the Blocks list."""
    return added(tab, lambda: win.palette_panel.tree.itemDoubleClicked.emit(tree_item(win, title), 0))


def drop_on_canvas(win, tab, title) -> str:
    """As the user does: drag the block from the Blocks list onto the middle of the canvas."""
    settle()
    mime = QMimeData()
    mime.setData(BLOCK_MIME, tree_item(win, title).data(0, Qt.UserRole).encode())  # what the list's drag carries
    viewport = tab.view.viewport()
    at = viewport.rect().center()

    def drag_and_drop():
        enter = QDragEnterEvent(at, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(viewport, enter)
        assert enter.isAccepted()
        QApplication.sendEvent(viewport, QDropEvent(QPointF(at), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))

    return added(tab, drag_and_drop)


def clean(win):
    """Leave no unsaved changes: closing the window at the end of the test must not ask to save them."""
    for tab in win.tabs_list():
        tab.doc.dirty = False


def select(tab, *blocks):
    tab.scene.clearSelection()
    for block in blocks:
        tab.scene.nodes[block].setSelected(True)
    settle()


def code_box(win) -> CodeEdit:
    """The selected block's code box in Properties."""
    (box,) = win.properties.findChildren(CodeEdit)
    return box


def error_shown(win) -> str:
    """The red problem line at the top of Properties ('' when hidden)."""
    return win.properties.error.text() if not win.properties.error.isHidden() else ""


def edit_action(win, text):
    (edit,) = [a.menu() for a in win.menuBar().actions() if a.text().replace("&", "") == "Edit"]
    return next(a for a in edit.actions() if a.text() == text)


def save_as(win, tab, path, monkeypatch):
    """File > Save of a new flow, to `path`."""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    assert win.save(tab)


def new_block(win, title) -> dict:
    """The config a `title` block has as the editor adds it to a new flow (double-clicked in the Blocks list)."""
    tab = win.new_flow()
    try:
        return dict(tab.doc.blocks[double_click_in_tree(win, tab, title)].get("config") or {})
    finally:
        clean(win)


def starter_of(win, title) -> str:
    """The code a new `title` block starts with."""
    key, _ = CODE[title]
    config = new_block(win, title)
    assert isinstance(config.get(key), str) and config[key].strip(), f"a new {title} block starts empty: {config}"
    return config[key]


# --- comments and examples -----------------------------------------------------------------------------------------

def _after(text: str, marker: str) -> str:
    rest = text[len(marker):]
    return rest[1:] if rest.startswith(" ") else rest


def comment_lines(code: str, language: str) -> list[tuple[str, bool]]:
    """Each line of `code` as (the text of its comment, with one space after a comment marker taken off; whether a
    comment of several lines is still open after it). Fails if a line is not a comment or blank."""
    line_marker, opening, closing = COMMENTS[language]
    lines, inside = [], False
    for line in code.splitlines():
        text = line.rstrip()
        if not inside:
            stripped = text.strip()
            if not stripped:
                lines.append(("", False))
                continue
            if line_marker and stripped.startswith(line_marker):
                lines.append((_after(stripped, line_marker), False))
                continue
            assert opening and stripped.startswith(opening), f"not a {language} comment: {line!r} in\n{code}"
            text, inside = _after(stripped, opening), True
        if closing in text:
            text, rest = text.split(closing, 1)
            assert not rest.strip(), f"not a {language} comment: {rest!r} in {line!r}"
            inside = False
        lines.append((text.rstrip(), inside))
    assert not inside, f"a comment is not closed:\n{code}"
    return lines


def examples(code: str, language: str) -> list[str]:
    """The examples of a starter (see the module's docstring), uncommented."""
    texts = [text for text, _ in comment_lines(code, language)]

    def indent(text):
        return len(text) - len(text.lstrip())

    base = min(indent(t) for t in texts if t.strip())
    found, current = [], []
    for text in texts + [""]:
        if text.strip() and indent(text) > base:
            current.append(text)
        elif current:
            found.append(textwrap.dedent("\n".join(current)).strip())
            current = []
    assert found, f"no examples (indented under the text that explains them) in\n{code}"
    return found


def below(starter: str, code: str) -> str:
    """`code` written on the line after the starter's comments."""
    return starter.rstrip("\n") + "\n" + code


def between(starter: str, code: str, language: str) -> str:
    """`code` written between the starter's comments: on a line of its own at the line nearest the middle that is not
    inside a comment (after them if there is none)."""
    lines = starter.splitlines()
    ends = comment_lines(starter, language)
    free = [i for i in range(1, len(lines)) if not ends[i - 1][1]]
    at = min(free, key=lambda i: abs(i - len(lines) / 2)) if free else len(lines)
    return "\n".join(lines[:at] + [code] + lines[at:])


def params_for(example: str, language: str) -> dict:
    """A flow parameter for each one the example uses: a date, or a number where it gives a number format."""
    specs = {}
    if language == "sql":
        specs.update((name, "") for name in re.findall(r"(?<![:\w]):([A-Za-z_]\w*)", example))
    if language == "python":
        specs.update((name, "") for name in re.findall(r"""\bparams\[\s*["']([A-Za-z_]\w*)["']\s*\]""", example))
    if language in ("sql", "html"):
        try:
            for _, name, spec, _ in string.Formatter().parse(example):
                if name and (spec or name not in specs):
                    specs[name] = spec or ""
        except ValueError:
            pass  # (an unmatched brace: the run says so)
    return {name: AMOUNT if spec and not re.search(r"%[A-Za-z]", spec) else DAY for name, spec in specs.items()}


# --- flows ---------------------------------------------------------------------------------------------------------

def duckdb(sql: str, inputs=()) -> dict:
    return {"type": "data.duckdb_sql", "config": {"inputs": list(inputs), "sql": sql}}


def python(code: str) -> dict:
    """A Python Code block (in Taskloom's own Python) with no inputs, whose code sets output."""
    return {"type": "logic.python", "config": {"inputs": [], "code": code}}


def inspect() -> dict:
    return {"type": "logic.inspect"}


def write_file(env, name: str) -> dict:
    return {"type": "data.write_file", "config": {"path": str(env.root / "out" / f"{name}.csv")}}


def written(env, name: str) -> list[dict]:
    return pl.read_csv(env.root / "out" / f"{name}.csv").to_dicts()


def mail(subject: str) -> dict:
    return {"type": "email.send", "config": {"connection": "mailer", "to": ["team@example.com"], "subject": subject}}


def flow_file(env, blocks: dict, edges=(), params=None, name="flow.yaml"):
    path = env.root / name
    path.write_text(yaml.safe_dump({"params": params or {}, "blocks": blocks, "edges": list(edges)}, sort_keys=False),
                    encoding="utf-8")
    return path


def runs_recorded(env) -> int:
    return env.query("SELECT count(*) FROM runs")[0][0] if (env.home / "history.db").exists() else 0


def outcomes(env, result) -> dict:
    """{block: (status, error)} of the last run."""
    assert runs_recorded(env), f"the run was refused:\n{result.stderr}"
    run_id, _ = env.last_run()
    return {b: (s, e or "") for b, s, e in env.query("SELECT block, status, error FROM blocks WHERE run_id=?", run_id)}


def assert_examples_work(env, result, tried: dict, *allowed: re.Pattern):
    """Each example's block (tried: {block: example}) succeeded, or failed only with an error `allowed` matches."""
    ran = outcomes(env, result)
    wrong = {example: ran.get(block) for block, example in tried.items()
             if ran.get(block, ("",))[0] != "success"
             and not (ran[block][0] == "failed" and any(a.search(ran[block][1]) for a in allowed))}
    assert not wrong, "examples that do not work uncommented:\n" + "\n\n".join(
        f"{example}\n  -> {outcome}" for example, outcome in wrong.items())


class _Shown(HTMLParser):
    """The text an email's HTML shows (not its comments, styles or head)."""

    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        self.hidden += tag in ("head", "style", "script")
        self.parts.append(" " if tag in ("br", "p", "div", "table", "tr", "td") else "")

    def handle_endtag(self, tag):
        self.hidden -= tag in ("head", "style", "script")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def shown(msg) -> tuple[str, str]:
    """What an email shows: the text of its HTML, and its plain-text part (spaces and line breaks evened out)."""
    html = msg.get_body(preferencelist=("html",)).get_content()
    parser = _Shown()
    parser.feed(html)
    plain = msg.get_body(preferencelist=("plain",)).get_content()
    return " ".join("".join(parser.parts).split()), " ".join(plain.split())


# --- a new block starts with its starter ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", ["DuckDB SQL", "Run Query", "Polars Transform", "HTML Template"])
def test_a_new_code_block_starts_with_short_comments_in_its_language(win, title):
    """Spec 1: dropped from the Blocks list or double-clicked there, a new block starts with the same short comments
    (about 10-20 lines) in its language's own comment form (SQL --, Python #, HTML <!-- -->), shown in its code box."""
    key, language = CODE[title]
    tab = win.new_flow()
    try:
        listed = double_click_in_tree(win, tab, title)
        dropped = drop_on_canvas(win, tab, title)

        config = tab.doc.blocks[listed].get("config") or {}
        code = config.get(key)
        assert isinstance(code, str) and code.strip(), f"a new {title} block starts empty: {config}"
        comment_lines(code, language)  # only comments
        assert 8 <= len(code.strip().splitlines()) <= 24, code  # short: about 10-20 lines
        assert (tab.doc.blocks[dropped].get("config") or {}).get(key) == code
        select(tab, listed)
        assert code_box(win).toPlainText() == code  # its text, not a placeholder
    finally:
        clean(win)


# --- its examples work uncommented ---------------------------------------------------------------------------------

def test_the_duckdb_sql_starter_shows_input_tables_parameters_and_the_result_with_examples_that_work(env, win):
    """Spec 1: the DuckDB SQL starter shows, with examples, an input as a table name (the new block's input, `input`),
    a parameter, and what it gives out (its result); each example, uncommented in a block whose Inputs are the tables
    it reads, works (or fails only because the table given has none of the columns it names)."""
    starter = starter_of(win, "DuckDB SQL")
    found = examples(starter, "sql")
    assert any("input" in map(str.lower, SQL_TABLES.findall(e)) for e in found), found  # its input, as a table
    assert any(params_for(e, "sql") for e in found), found  # uses a flow parameter
    assert re.search(r"(?i)\bresult\b", starter), starter  # what it gives out
    blocks, edges, params, tried = {"source": duckdb(SOURCE_SQL)}, [], {}, {}
    for i, example in enumerate(found, 1):
        tables = list(dict.fromkeys(SQL_TABLES.findall(example))) or ["input"]
        blocks[f"example_{i}"] = duckdb(example, tables)
        edges += [f"source.result -> example_{i}.{table}" for table in tables]
        params.update(params_for(example, "sql"))
        tried[f"example_{i}"] = example

    result = env.taskloom("run", flow_file(env, blocks, edges, params))

    assert_examples_work(env, result, tried, MISSING_COLUMN)


def test_the_polars_transform_starter_shows_df_parameters_and_the_table_with_examples_that_work(env, win):
    """Spec 1: the Polars Transform starter shows, with examples, df (the table it gets), params, and what it gives out
    (its table); each example, uncommented, works on a table (or fails only because the table has none of the columns
    it names)."""
    starter = starter_of(win, "Polars Transform")
    found = examples(starter, "python")
    assert any(re.search(r"\bdf\b", e) for e in found), found
    assert any(params_for(e, "python") for e in found), found
    assert re.search(r"(?i)\btable\b", starter), starter
    blocks, edges, params, tried = {"source": duckdb(SOURCE_SQL)}, [], {}, {}
    for i, example in enumerate(found, 1):
        blocks[f"example_{i}"] = {"type": "data.polars", "config": {"code": example}}
        edges.append(f"source.result -> example_{i}.table")
        params.update(params_for(example, "python"))
        tried[f"example_{i}"] = example

    result = env.taskloom("run", flow_file(env, blocks, edges, params))

    assert_examples_work(env, result, tried, MISSING_COLUMN)


def test_the_html_template_starter_shows_its_variables_with_examples_that_work(env, win):
    """Spec 1: the HTML Template starter shows, with examples, the template's variables ({name}), and what it gives out
    (its html); each example, uncommented, renders (its variables given as flow parameters)."""
    starter = starter_of(win, "HTML Template")
    found = examples(starter, "html")
    assert any(params_for(e, "html") for e in found), found
    assert re.search(r"(?i)\bhtml\b", starter), starter
    blocks, params, tried = {}, {}, {}
    for i, example in enumerate(found, 1):
        blocks[f"example_{i}"] = {"type": "reports.html_template", "config": {"template": example}}
        params.update(params_for(example, "html"))
        tried[f"example_{i}"] = example

    result = env.taskloom("run", flow_file(env, blocks, params=params))

    assert_examples_work(env, result, tried)


def test_the_run_query_starter_shows_parameters_and_the_result_with_examples_that_work(jdbc_env, win):
    """Spec 1: the Run Query starter shows, with examples, how its params input and the flow parameters are used
    (:name) and what it gives out (its result); each example, uncommented, runs on a real database (or fails only
    because that database has none of the tables or columns it names)."""
    starter = starter_of(win, "Run Query")
    found = examples(starter, "sql")
    assert re.search(r"\bparams\b", starter), starter  # its input
    assert any(re.search(r"(?<![:\w]):[A-Za-z_]", e) for e in found), found
    assert re.search(r"(?i)\bresult\b", starter), starter
    blocks, params, tried = {}, {}, {}
    for i, example in enumerate(found, 1):
        blocks[f"example_{i}"] = {"type": "db.run_query", "config": {"connection": "warehouse", "sql": example}}
        params.update(params_for(example, "sql"))
        tried[f"example_{i}"] = example

    result = jdbc_env.taskloom("run", flow_file(jdbc_env, blocks, params=params))

    assert_examples_work(jdbc_env, result, tried, MISSING_TABLE, MISSING_COLUMN)


# --- only comments: the code is required ---------------------------------------------------------------------------

@pytest.mark.parametrize("title, label", [("DuckDB SQL", "SQL"), ("Run Query", "SQL"), ("Polars Transform", "Code"),
                                          ("If", "Condition")])
def test_a_block_with_only_its_starter_comments_says_its_code_is_required(env, win, monkeypatch, title, label):
    """Spec 2 and 3: a new block whose code is only its starter counts as empty: Properties, `taskloom validate` and
    `taskloom run` say "<label> is required", as for an empty setting, and nothing runs (no database or runtime
    error)."""
    key, _ = CODE[title]
    env.write("home/connections.yaml", """
        warehouse: {kind: jdbc, driver: org.h2.Driver, url: "jdbc:h2:mem:nowhere", jars: []}
    """)
    flow = env.root / "starter.yaml"
    tab = win.new_flow()
    try:
        block = double_click_in_tree(win, tab, title)
        config = tab.doc.blocks[block].get("config") or {}
        assert isinstance(config.get(key), str) and config[key].strip(), f"a new {title} block starts empty: {config}"
        if title == "Run Query":
            tab.doc.set_block_value(block, "connection", "warehouse")
        tab.doc.connect("start", "start", block, "after")
        select(tab, block)
        assert f"{label} is required" in error_shown(win)
        save_as(win, tab, flow, monkeypatch)
    finally:
        clean(win)

    checked = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow)

    for result in (checked, ran):
        assert result.returncode == 2, result.stdout + result.stderr
        assert f"{block}: {label} is required" in result.stderr
    assert runs_recorded(env) == 0


def test_an_if_condition_of_only_comment_lines_says_condition_is_required(env):
    """Spec 3: a condition with only comment lines (and blank ones) is "Condition is required", the same message as
    an empty one; nothing runs."""
    flow = flow_file(env, {"check": {"type": "logic.if", "config": {"condition": "# first\n\n# second"}}})

    checked = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow)

    for result in (checked, ran):
        assert result.returncode == 2, result.stdout + result.stderr
        assert "check: Condition is required" in result.stderr
    assert runs_recorded(env) == 0


# --- code written below or between the comments: the comments are ignored ------------------------------------------

def test_duckdb_sql_runs_the_query_written_below_or_between_its_starter_comments(env, win):
    """Spec 2: with a query below or between the starter's comments, the block runs that query, in a flow with no
    parameters (whatever the comments say needs none)."""
    config = new_block(win, "DuckDB SQL")
    starter = config["sql"] if isinstance(config.get("sql"), str) else ""
    assert starter.strip(), f"a new DuckDB SQL block starts empty: {config}"
    query = "select count(*) as n, sum(x) as total from input"
    blocks, edges = {"source": duckdb(SOURCE_SQL)}, []
    for where, sql in {"below": below(starter, query), "between": between(starter, query, "sql")}.items():
        blocks[where] = {"type": "data.duckdb_sql", "config": {**config, "sql": sql}}
        blocks[f"save_{where}"] = write_file(env, where)
        edges += [f"source.result -> {where}.input", f"{where}.result -> save_{where}.table"]

    result = env.taskloom("run", flow_file(env, blocks, edges))

    assert result.returncode == 0, result.stderr
    assert written(env, "below") == written(env, "between") == [{"n": 3, "total": 6}]


def test_polars_transform_runs_the_expression_written_below_or_between_its_starter_comments(env, win):
    """Spec 2: with an expression below or between the starter's comments, the block runs that expression, in a flow
    with no parameters."""
    config = new_block(win, "Polars Transform")
    starter = config["code"] if isinstance(config.get("code"), str) else ""
    assert starter.strip(), f"a new Polars Transform block starts empty: {config}"
    expression = 'df.with_columns((pl.col("x") * 10).alias("tenfold"))'
    blocks, edges = {"source": duckdb(SOURCE_SQL)}, []
    for where, code in {"below": below(starter, expression), "between": between(starter, expression, "python")}.items():
        blocks[where] = {"type": "data.polars", "config": {**config, "code": code}}
        blocks[f"save_{where}"] = write_file(env, where)
        edges += [f"source.result -> {where}.table", f"{where}.table -> save_{where}.table"]

    result = env.taskloom("run", flow_file(env, blocks, edges))

    assert result.returncode == 0, result.stderr
    assert written(env, "below") == written(env, "between") == [{"x": x, "tenfold": 10 * x} for x in (1, 2, 3)]


def test_run_query_runs_the_query_written_below_or_between_its_starter_comments(jdbc_env, win):
    """Spec 2: with a query below or between the starter's comments, the block sends it (comments and all) to a real
    database and gives its rows, in a flow with no parameters."""
    config = new_block(win, "Run Query")
    starter = config["sql"] if isinstance(config.get("sql"), str) else ""
    assert starter.strip(), f"a new Run Query block starts empty: {config}"
    query = "select 7 as answer"
    blocks, edges = {}, []
    for where, sql in {"below": below(starter, query), "between": between(starter, query, "sql")}.items():
        blocks[where] = {"type": "db.run_query", "config": {**config, "connection": "warehouse", "sql": sql}}
        blocks[f"save_{where}"] = write_file(jdbc_env, where)
        edges.append(f"{where}.result -> save_{where}.table")

    result = jdbc_env.taskloom("run", flow_file(jdbc_env, blocks, edges))

    assert result.returncode == 0, result.stderr
    assert written(jdbc_env, "below") == written(jdbc_env, "between") == [{"ANSWER": 7}]


def test_an_html_template_sends_the_same_email_with_its_starter_comments_as_without_them(env, smtp, win):
    """Spec 2: the HTML Template's starter does not show in the email (its HTML or its plain text): with a template
    below or between the comments, the email shows what the template alone shows; with only the comments, what an
    empty template shows (every input in turn). A new block has no problem in Properties, and the flow validates."""
    tab = win.new_flow()
    try:
        block = double_click_in_tree(win, tab, "HTML Template")
        select(tab, block)
        assert error_shown(win) == ""
        config = tab.doc.blocks[block].get("config") or {}
    finally:
        clean(win)
    starter = config["template"] if isinstance(config.get("template"), str) else ""
    assert starter.strip(), f"a new HTML Template block starts empty: {config}"
    body = "<b>Hello {who}</b>, the totals are below.<br>Thank you"
    templates = {"plain": body, "below": below(starter, body), "between": between(starter, body, "html"),
                 "only": starter, "empty": None}
    blocks, edges = {"who": python("output = 'Bob'")}, []
    for name, template in templates.items():
        blocks[name] = {"type": "reports.html_template",
                        "config": {k: v for k, v in {**config, "template": template}.items() if v is not None}}
        blocks[f"mail_{name}"] = mail(name)
        edges += [f"who.output -> {name}.data", f"{name}.html -> mail_{name}.body"]
    flow = flow_file(env, blocks, edges)

    checked = env.taskloom("validate", flow)
    result = env.taskloom("run", flow)

    assert checked.returncode == 0, checked.stderr
    assert "Template" not in checked.stderr, checked.stderr  # no problem nor warning about the comments
    assert result.returncode == 0, result.stderr
    sent = {msg["Subject"]: shown(msg) for msg in smtp.messages}
    assert "Hello Bob, the totals are below. Thank you" in sent["plain"][0], sent["plain"]
    assert sent["below"] == sent["plain"], (sent["below"], sent["plain"])
    assert sent["between"] == sent["plain"], (sent["between"], sent["plain"])
    assert "Bob" in sent["empty"][0], sent["empty"]
    assert sent["only"] == sent["empty"], (sent["only"], sent["empty"])


# --- If ------------------------------------------------------------------------------------------------------------

def test_a_new_if_block_starts_with_the_users_example_line(win):
    """Spec 3: dropped from the Blocks list or double-clicked there, a new If block's condition starts with the user's
    line, "# No need to delete this. Examples: ...", shown in its condition box; it is only comments."""
    tab = win.new_flow()
    try:
        listed = double_click_in_tree(win, tab, "If")
        dropped = drop_on_canvas(win, tab, "If")

        condition = (tab.doc.blocks[listed].get("config") or {}).get("condition")
        assert isinstance(condition, str) and condition.splitlines()[:1] == [IF_LINE], f"a new If block: {condition!r}"
        comment_lines(condition, "python")  # only comments
        assert (tab.doc.blocks[dropped].get("config") or {}).get("condition") == condition
        select(tab, listed)
        assert code_box(win).toPlainText() == condition
    finally:
        clean(win)


def if_flow(cases: list[tuple[str, object]]) -> tuple[dict, list]:
    """For each (condition, value): a block giving the value, an If with the condition, and an Inspect on each path,
    case_N, if_N, yes_N (true) and no_N (false)."""
    blocks, edges = {}, []
    for i, (condition, value) in enumerate(cases, 1):
        blocks |= {f"case_{i}": python(f"output = {value!r}"),
                   f"if_{i}": {"type": "logic.if", "config": {"condition": condition}},
                   f"yes_{i}": inspect(), f"no_{i}": inspect()}
        edges += [f"case_{i}.output -> if_{i}.value", f"if_{i}.true -> yes_{i}.value", f"if_{i}.false -> no_{i}.value"]
    return blocks, edges


def paths_taken(env, result, count: int) -> list[bool]:
    """For each case of if_flow: whether its If took the true path (and only it)."""
    ran = outcomes(env, result)
    taken = []
    for i in range(1, count + 1):
        statuses = (ran[f"yes_{i}"][0], ran[f"no_{i}"][0])
        assert statuses in (("success", "skipped"), ("skipped", "success")), (i, ran[f"if_{i}"], statuses)
        taken.append(statuses[0] == "success")
    return taken


def test_the_examples_in_the_if_line_work_as_written(env, win):
    """Spec 3 (and 1: every example works uncommented): each example of the new If block's line, as the condition,
    is true for a value it describes and false for another."""
    condition = starter_of(win, "If")
    first = condition.splitlines()[0]
    assert first == IF_LINE, first
    found = [e.strip() for e in first.split("Examples:", 1)[1].split(",")]
    assert found == list(IF_EXAMPLES), found
    cases = [(example, value) for example in found for value in IF_EXAMPLES[example]]  # true, then false

    result = env.taskloom("run", flow_file(env, *if_flow(cases)))

    assert paths_taken(env, result, len(cases)) == [True, False] * len(found)


def test_a_condition_written_under_the_if_line_decides_the_path(env, win):
    """Spec 3: the If line's comment is ignored when the condition is evaluated: with an expression below it (also with
    another comment line after it, or with a # inside a quoted text), the If goes the way that expression says."""
    starter = starter_of(win, "If")
    conditions = [below(starter, "value > 10"), below(starter, "value > 10\n# checked on every run"),
                  below(starter, "value == 'Option #1'")]
    cases = [(conditions[0], 42), (conditions[0], 5), (conditions[1], 42), (conditions[1], 5),
             (conditions[2], "Option #1"), (conditions[2], "Option 1")]

    result = env.taskloom("run", flow_file(env, *if_flow(cases)))

    assert paths_taken(env, result, len(cases)) == [True, False, True, False, True, False]


# --- Inspect and existing flows ------------------------------------------------------------------------------------

def test_a_new_inspect_block_keeps_an_empty_message(win):
    """Spec 4: a new Inspect block gets no starter: its message is empty (empty shows the whole value)."""
    tab = win.new_flow()
    try:
        listed = double_click_in_tree(win, tab, "Inspect")
        dropped = drop_on_canvas(win, tab, "Inspect")

        for block in (listed, dropped):
            assert not (tab.doc.blocks[block].get("config") or {}).get("message"), tab.doc.blocks[block]
        select(tab, listed)
        assert code_box(win).toPlainText() == ""
    finally:
        clean(win)


def test_an_inspect_message_starting_with_a_hash_is_shown_as_written(env):
    """Spec 4 and 5: an Inspect message has no comments: one that is a Markdown heading (# ...) is shown as written,
    not taken for an empty message (which would show the whole value)."""
    blocks = {"report": python("output = 'all rows loaded'"),
              "show": {"type": "logic.inspect", "config": {"message": "# Daily report", "markdown": True}}}

    result = env.taskloom("run", flow_file(env, blocks, ["report.output -> show.value"]))

    assert result.returncode == 0, result.stderr
    logged = [m for (m,) in env.query("SELECT message FROM logs WHERE block='show'")]
    assert any("# Daily report" in m for m in logged), logged
    assert not any("all rows loaded" in m for m in logged), logged


EXISTING = {  # a flow file's code blocks, as written by hand: with their code, and without it
    "load": duckdb("select 1 as x"),
    "load_empty": {"type": "data.duckdb_sql", "config": {"inputs": []}},
    "change": {"type": "data.polars", "config": {"code": "df"}},
    "change_empty": {"type": "data.polars"},
    "query": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": "select 1 as x"}},
    "query_empty": {"type": "db.run_query", "config": {"connection": "warehouse"}},
    "body": {"type": "reports.html_template", "config": {"template": "<b>Hi</b>"}},
    "body_empty": {"type": "reports.html_template"},
    "check": {"type": "logic.if", "config": {"condition": "value > 1"}},
    "check_empty": {"type": "logic.if"},
    "show": {"type": "logic.inspect"},
}


def existing_flow(env):
    blocks = {name: {**spec, "ui": {"x": 220 * (i % 4), "y": 140 * (i // 4)}} for i, (name, spec) in
              enumerate(EXISTING.items())}
    return flow_file(env, blocks, name="existing.yaml")


def test_opening_and_saving_an_existing_flow_keeps_its_code_settings_as_they_are(env, win):
    """Spec 5: only new blocks get starters: an existing flow's code blocks, opened, shown in Properties and saved,
    keep their code, and those without code stay without it."""
    flow = existing_flow(env)
    tab = win.open_flow(flow)
    try:
        for block in EXISTING:
            select(tab, block)
        assert win.save(tab)
    finally:
        clean(win)

    saved = yaml.safe_load(flow.read_text(encoding="utf-8"))["blocks"]
    kept = {block: saved[block].get("config") or {} for block in EXISTING}
    assert kept == {block: spec.get("config") or {} for block, spec in EXISTING.items()}


def test_copied_and_pasted_code_blocks_keep_their_code_as_it_is(env, win):
    """Spec 5: copy and paste of existing blocks is not adding new ones: the copies of blocks without code have
    none."""
    empty = ["load_empty", "change_empty", "query_empty", "body_empty", "check_empty", "show"]
    tab = win.open_flow(existing_flow(env))
    try:
        QApplication.clipboard().setText("")
        select(tab, *empty)
        before = set(tab.doc.blocks)

        edit_action(win, "Copy").trigger()
        edit_action(win, "Paste").trigger()
        settle()

        copies = [tab.doc.blocks[block] for block in set(tab.doc.blocks) - before]
        pasted = {spec["type"]: spec.get("config") or {} for spec in copies}
        assert pasted == {EXISTING[b]["type"]: EXISTING[b].get("config") or {} for b in empty}
    finally:
        clean(win)


def test_an_existing_templates_outlook_conditional_comments_still_reach_the_email(env, smtp):
    """Spec 5: an existing flow's HTML Template keeps its own HTML comments, such as the conditional comments that lay
    an email out in Outlook: they reach the email as written."""
    opening = '<!--[if mso]><table role="presentation" width="600"><tr><td><![endif]-->'
    closing = "<!--[if mso]></td></tr></table><![endif]-->"
    blocks = {"who": python("output = 'Bob'"),
              "body": {"type": "reports.html_template", "config": {"template": f"{opening}Hello {{who}}{closing}"}},
              "send": mail("report")}

    result = env.taskloom("run", flow_file(env, blocks, ["who.output -> body.data", "body.html -> send.body"]))

    assert result.returncode == 0, result.stderr
    html = smtp.messages[0].get_body(preferencelist=("html",)).get_content()
    assert f"{opening}Hello Bob{closing}" in html, html
