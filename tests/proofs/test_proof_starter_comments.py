"""Proof: a new block where you write code starts with short comments that teach it, and a new If block's Condition
with examples; the comments change nothing. See README.md here."""

import datetime as dt
import decimal
import json
import re
from pathlib import Path

import polars as pl
import pytest
import yaml
from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QLabel, QLineEdit, QMessageBox, QToolBar

from conftest import _h2_jar, _java_home
from proofs.test_proof_handled_error import click_bottom_tab, open_flow, press_run_now
from taskloom.blocks.logic import PythonCode
from taskloom.editor.canvas import HEADER_H, NODE_W
from taskloom.editor.panels import CodeEdit
from tests.test_editor import drag_port_to_port
from tests.test_paths_editor_editing import drag_over_and_drop, dragged_from_the_tree, settle
from tests.test_starter_comments import examples

# Taken as this file is imported, before the win fixture answers message boxes itself: Validate's box and the
# refusal's are then shown as in the app.
REAL_WARNING = QMessageBox.warning
ASKED = ("Just like with the Python Code block all the blocks where the user is expected to input some kind of code "
         "such as Run Query, DuckDB SQL, Polars Transform, others I am missing? it should be prefilled with some "
         "comments teaching the user how to make use of the block. … IF block: Present the IF text area with a "
         "message with examples like: # No need to delete this. Examples: value > 10, value is True, "
         "value == 'Option A'")
IF_LINE = "# No need to delete this. Examples: value > 10, value is True, value == 'Option A'"
DAY = "2026-10-30"
SALES = [("2026-10-29", "North", 120), ("2026-10-29", "South", -15), ("2026-10-30", "North", 80),
         ("2026-10-30", "East", 45), ("2026-10-30", "South", 60)]
SALES_SQL = ("select * from (values " + ", ".join(f"(date '{d}', '{r}', {a})" for d, r, a in SALES)
             + ") t(day, region, amount)")
MANAGERS = {"North": "Ana", "South": "Ben", "East": "Cleo"}
REGIONS_SQL = ("select * from (values " + ", ".join(f"('{r}', '{m}')" for r, m in MANAGERS.items())
               + ") t(region, manager)")
FLOW = {
    "params": {"day": {"type": "date", "value": DAY}},
    "blocks": {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
        "load_sales": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": SALES_SQL},
                       "ui": {"x": 220, "y": 0}},
        "count_orders": {"type": "logic.python", "config": {"inputs": [], "code": "output = 12"},
                         "ui": {"x": 220, "y": 390}},
        "over_10": {"type": "logic.inspect", "ui": {"x": 660, "y": 350}},
        "not_over_10": {"type": "logic.inspect", "ui": {"x": 660, "y": 480}},
    },
    "edges": ["start.start -> load_sales.after", "start.start -> count_orders.after"],
}
# The blocks this proof adds from the Blocks list, in order: the name each gets, where it lands on the canvas (its
# top-left corner), the setting that holds its code, and that code's comment form.
NEW = {
    "Run Query": ("run_query", QPointF(220, 550), "sql", "SQL comments, each line starting with --"),
    "DuckDB SQL": ("duckdb_sql", QPointF(440, 0), "sql", "SQL comments, each line starting with --"),
    "Polars Transform": ("polars", QPointF(440, 125), "code", "Python comments, each line starting with #"),
    "HTML Template": ("html_template", QPointF(440, 250), "template", "one HTML note, between <!-- and -->"),
    "If": ("if", QPointF(440, 390), "condition", "a Python comment, starting with #"),
}
CONNECTIONS = [("load_sales", "result", "duckdb_sql", "input"), ("load_sales", "result", "polars", "table"),
               ("load_sales", "result", "html_template", "data"), ("count_orders", "output", "if", "value"),
               ("if", "true", "over_10", "value"), ("if", "false", "not_over_10", "value"),
               ("start", "start", "run_query", "after")]
REQUIRED = ["run_query: SQL is required", "duckdb_sql: SQL is required", "polars: Code is required",
            "if: Condition is required"]
# What this proof writes under the comments, in the editor: one of each block's examples.
WRITTEN = {"run_query": "select region, count(*) as orders from orders group by region",
           "duckdb_sql": "select * from input where day = :day",
           "polars": 'df.with_columns((pl.col("amount") * 1.2).alias("gross"))',
           "if": "value > 10"}
BODY = "<b>Daily sales</b><br>Good morning, the figures are below.<br>{load_sales}"
NOTE = "This is a note"  # in the HTML Template's comments: never in the email
# The tables of the Run Query examples, in a database that runs inside the run itself (H2, in memory).
ORDERS = [(1, "North"), (2, "North"), (3, "South"), (4, "North"), (5, "East"), (6, "South")]
CUSTOMERS = [("Avery", "North"), ("Blake", "South"), ("Casey", "East")]
SHOP_SQL = f"""\
create table orders (id int, region varchar(20));
insert into orders values {", ".join(f"({i}, '{r}')" for i, r in ORDERS)};
create table sales (sale_date date, region varchar(20), amount decimal(10, 2));
insert into sales values {", ".join(f"(date '{d}', '{r}', {a})" for d, r, a in SALES if a > 0 and r != "South")};
create table customers (name varchar(20), region varchar(20));
insert into customers values {", ".join(f"('{n}', '{r}')" for n, r in CUSTOMERS)};
"""
SHOP_SALES = [s for s in SALES if s[2] > 0 and s[1] != "South"]
REGIONS = ["North", "East"]  # the examples' list parameter, regions
TOTAL = 1234.5  # and their number, total


def of_day(rows):
    return {r for r in rows if r[0] == DAY}


# Each example the comments hold, what it should give in the run that uses it as code, and a check of that: on the
# rows of its table (dates as text, numbers as numbers), or on its html.
DUCKDB = {
    "select * from input": ("all five sales", lambda rows: set(rows) == set(SALES)),
    "select s.*, r.manager from sales s join regions r using (region)":
        ("the five sales, each with its region's manager",
         lambda rows: set(rows) == {(d, r, a, MANAGERS[r]) for d, r, a in SALES}),
    "select * from input where day = :day":
        (f"the three sales of {DAY} (day)", lambda rows: len(rows) == 3 and set(rows) == of_day(SALES)),
    "select * from input where region in (:regions)":
        ("the three sales of North and East (regions)",
         lambda rows: len(rows) == 3 and set(rows) == {s for s in SALES if s[1] in REGIONS}),
}
POLARS = {
    'df.filter(pl.col("amount") > 0)': ("the four sales with an amount over 0",
                                        lambda rows: set(rows) == {s for s in SALES if s[2] > 0}),
    'df.with_columns((pl.col("amount") * 1.2).alias("gross"))':
        ("the five sales, each with gross, its amount times 1.2",
         lambda rows: {r[:3] + (round(r[3], 6),) for r in rows} == {s + (round(s[2] * 1.2, 6),) for s in SALES}),
    'df.filter(pl.col("day") == params["day"])':
        (f"the three sales of {DAY} (day)", lambda rows: len(rows) == 3 and set(rows) == of_day(SALES)),
}
RUN_QUERY = {
    "select region, count(*) as orders from orders group by region":
        ("the orders of each region: East 1, North 3, South 2", lambda rows: set(rows) == {
            (r, sum(1 for _, o in ORDERS if o == r)) for r in MANAGERS}),
    "select * from sales where sale_date = :day":
        (f"the database's two sales of {DAY} (day)", lambda rows: len(rows) == 2 and set(rows) == of_day(SHOP_SALES)),
    "select * from customers where region in (:regions)":
        ("the customers of North and East (regions): Avery and Casey",
         lambda rows: set(rows) == {c for c in CUSTOMERS if c[1] in REGIONS}),
}
HTML = {
    "<b>Daily sales</b><br>Good morning, the figures are below.":
        ("that same text", lambda html: html == "<b>Daily sales</b><br>Good morning, the figures are below."),
    "Hi team, please see the chart below<br>{chart}":
        ("the text, then the chart's picture (an <img> inside the email)",
         lambda html: re.fullmatch(r'Hi team, please see the chart below<br><img src="cid:[^"]+"[^>]*>', html)),
    "Total {total:,.2f} on {day:%d %B %Y}":
        ("Total 1,234.50 on 30 October 2026", lambda html: html == "Total 1,234.50 on 30 October 2026"),
}
IF_CASES = {"value > 10": (12, 10), "value is True": (True, 1), "value == 'Option A'": ("Option A", "Option B")}


def is_sql_comments(code: str) -> bool:
    return bool(code.strip()) and all(line.startswith("--") for line in code.splitlines())


def is_python_comments(code: str) -> bool:
    return bool(code.strip()) and all(line.startswith("#") for line in code.splitlines())


def is_one_html_note(code: str) -> bool:
    return code.startswith("<!--") and code.endswith("-->") and code.count("<!--") == 1 and code.count("-->") == 1


COMMENTS = {"Run Query": is_sql_comments, "DuckDB SQL": is_sql_comments, "Polars Transform": is_python_comments,
            "HTML Template": is_one_html_note, "If": is_python_comments}


def drop(win, tab, title: str, corner: QPointF) -> str:
    """Drag a block from the Blocks list onto the canvas, its top-left corner at `corner`: its name."""
    tree = win.palette_panel.tree
    item = tree.findItems(title, Qt.MatchExactly | Qt.MatchRecursive)[0]
    tree.scrollToItem(item)
    before = set(tab.doc.blocks)
    at = tab.view.mapFromScene(corner + QPointF(NODE_W / 2, HEADER_H / 2))  # (its title bar's middle lands there)
    assert drag_over_and_drop(tab, dragged_from_the_tree(item), at), f"the canvas did not take {title}"
    (block,) = set(tab.doc.blocks) - before
    return block


def click(tab, block: str):
    """Click a block's title bar, as you would: Properties shows it."""
    at = tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, tab.view.mapFromScene(at))
    QTest.qWait(100)


def code_box(win) -> CodeEdit:
    """The selected block's code box, in Properties."""
    return next(c for c in win.properties.findChildren(CodeEdit) if c.isVisible())


def write_below(win, code: str):
    """In the code box, press Ctrl+End (to its end), Enter twice and type `code` (under a blank line); then leave the
    box (as when you click elsewhere), which saves it."""
    box = code_box(win)
    box.setFocus()
    QTest.keyClick(box, Qt.Key_End, Qt.ControlModifier)
    QTest.keyClick(box, Qt.Key_Return)
    QTest.keyClick(box, Qt.Key_Return)
    QTest.keyClicks(box, code)
    box.editing_finished.emit()
    QTest.qWait(100)


def last_line(win, box: CodeEdit) -> QRect:
    """Where the box shows the text of its last line, in the window (to outline it)."""
    line = box.document().lastBlock()
    shown = box.blockBoundingGeometry(line).translated(box.contentOffset()).toRect()
    shown.setWidth(round(box.document().documentMargin()) + box.fontMetrics().horizontalAdvance(line.text()) + 4)
    return shown.translated(box.viewport().mapTo(win, QPoint(0, 0)))


def top_of(win, widget, height: int) -> QRect:
    """The top `height` pixels of a widget, in the window (to zoom on)."""
    return QRect(widget.mapTo(win, QPoint(0, 0)), QSize(widget.width(), height))


def press(win, action):
    """Press a button of the toolbar (Validate, ▶ Run now)."""
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(action), Qt.LeftButton)


def box_text(box) -> QLabel:
    """The text of a message box, to outline it."""
    return next(label for label in box.findChildren(QLabel) if label.text() == box.text())


def edit_menu(win, item: str):
    menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Edit")
    next(a for a in menu.actions() if a.text() == item).trigger()
    QTest.qWait(100)


def run_now(p, win, qtbot, tab, n: int, shoot_dialog: bool = False):
    """Press ▶ Run now, press Run in the box that shows the flow's parameters, and wait for the tab's n-th run (and
    for the status bar to say it has ended)."""
    def look(dialog):
        if shoot_dialog:
            p.shot(dialog, f"The box shows the flow's parameter: day is {DAY}. Press Run.",
                   highlight=[dialog.findChild(QLineEdit)])
        QTest.mouseClick(next(b for b in dialog.findChild(QDialogButtonBox).buttons() if b.text() == "Run"),
                         Qt.LeftButton)

    p.while_open(lambda: press_run_now(win), look)
    qtbot.waitUntil(lambda: len(tab.runs) == n and not tab.running and tab.runs[-1].status is not None,
                    timeout=120_000)
    qtbot.waitUntil(lambda: win.running_flows.text() == "No flows running", timeout=10_000)
    return tab.runs[-1]


def preview_rows_shown(win) -> list:
    """The rows Data preview shows of a table, to outline them."""
    view = win.preview.view
    return [[(view, row) for row in range(view.model().rowCount())]]


def preview_table(win) -> list:
    """What Data preview shows of a table, to zoom on: its first line, its header and its rows."""
    view = win.preview.view
    return [win.preview.label, (view, 0), (view, view.model().rowCount() - 1)]


def preview_text(win, lines: int = 3) -> QRect:
    """What Data preview shows of a text, to zoom on: its first line, and the text's first lines."""
    text = win.preview.text
    height = text.fontMetrics().lineSpacing() * lines + 8
    top_left = win.preview.label.mapTo(win, QPoint(0, 0))
    bottom = text.viewport().mapTo(win, QPoint(0, height)).y()
    return QRect(top_left, QPoint(top_left.x() + 740, bottom))


def preview_rows(win) -> list[tuple]:
    """The rows Data preview shows, as text."""
    model = win.preview.view.model()
    return [tuple(model.index(r, c).data() for c in range(model.columnCount())) for r in range(model.rowCount())]


def preview_columns(win) -> list[str]:
    """The columns Data preview shows (each header says its name, then its type)."""
    model = win.preview.view.model()
    return [model.headerData(c, Qt.Horizontal).split("\n")[0] for c in range(model.columnCount())]


def plain(value):
    """A table's value as the checks above write it: a date as text, a number as a number."""
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def outcomes(env, run_id: int) -> dict:
    """{block: (status, the rows of its table, its other outputs {output: as shown})} of a run, as the run history
    keeps them."""
    found = {}
    for block, status, tables, previews in env.query(
            "SELECT block, status, tables, previews FROM blocks WHERE run_id=?", run_id):
        paths = list(json.loads(tables or "{}").values())
        rows = [tuple(map(plain, row)) for row in pl.read_parquet(paths[0]).rows()] if paths else None
        found[block] = (status, rows, json.loads(previews or "{}"))
    return found


PARAMS = {"day": {"type": "date", "value": DAY}, "regions": {"type": "list", "value": ", ".join(REGIONS)},
          "total": {"type": "float", "value": TOTAL}}


def examples_flows(starters: dict) -> dict:
    """For each kind of block, a flow with a block for each example in its comments, written as its code: {kind:
    (file name, flow, {block: example})}; for If, a block for each value tried, with (condition, value, whether the
    condition is true for it)."""
    load_sales = {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": SALES_SQL}}
    flows = {}

    def flow(kind, name, blocks, edges, tried, places):
        """`places`: where each block is on the canvas, {block: (column, row)}."""
        for block, (column, row) in places.items():
            blocks[block] = {**blocks[block], "ui": {"x": round(250 * column), "y": round(140 * row)}}
        flows[kind] = (name, {"name": name.removesuffix(".yaml"), "params": PARAMS, "blocks": blocks,
                              "edges": edges}, tried)

    blocks = {"load_sales": load_sales,
              "load_regions": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": REGIONS_SQL}}}
    edges, tried = [], {}
    for i, sql in enumerate(examples(starters["DuckDB SQL"], "sql"), 1):
        tables = ["sales", "regions"] if " join " in sql else ["input"]  # (Inputs as the comments say)
        blocks[f"duckdb_{i}"] = {"type": "data.duckdb_sql", "config": {"inputs": tables, "sql": sql}}
        edges += [f"load_{'regions' if t == 'regions' else 'sales'}.result -> duckdb_{i}.{t}" for t in tables]
        tried[f"duckdb_{i}"] = sql
    flow("DuckDB SQL", "duckdb-examples.yaml", blocks, edges, tried,
         {"load_sales": (0.5, 0), "load_regions": (2.5, 0), **{b: (i, 1.3) for i, b in enumerate(tried)}})

    blocks, edges, tried = {"load_sales": load_sales}, [], {}
    for i, code in enumerate(examples(starters["Polars Transform"], "python"), 1):
        blocks[f"polars_{i}"] = {"type": "data.polars", "config": {"code": code}}
        edges.append(f"load_sales.result -> polars_{i}.table")
        tried[f"polars_{i}"] = code
    flow("Polars Transform", "polars-examples.yaml", blocks, edges, tried,
         {"load_sales": (1, 0), **{b: (i, 1.3) for i, b in enumerate(tried)}})

    blocks, tried = {}, {}
    for i, sql in enumerate(examples(starters["Run Query"], "sql"), 1):
        blocks[f"query_{i}"] = {"type": "db.run_query", "config": {"connection": "shop", "sql": sql}}
        tried[f"query_{i}"] = sql
    flow("Run Query", "run-query-examples.yaml", blocks, [], tried, {b: (i, 0) for i, b in enumerate(tried)})

    blocks = {"load_sales": load_sales,
              "chart": {"type": "reports.chart", "config": {"kind": "bar", "x": "region", "y": ["amount"]}},
              "only_note": {"type": "reports.html_template", "config": {"template": starters["HTML Template"]}},
              "empty": {"type": "reports.html_template"}}
    edges = ["load_sales.result -> chart.table", "load_sales.result -> only_note.data",
             "load_sales.result -> empty.data"]
    tried = {}
    for i, template in enumerate(examples(starters["HTML Template"], "html"), 1):
        blocks[f"html_{i}"] = {"type": "reports.html_template", "config": {"template": template}}
        if "{chart}" in template:
            edges.append(f"chart.image -> html_{i}.data")
        tried[f"html_{i}"] = template
    flow("HTML Template", "html-template-examples.yaml", blocks, edges, tried,
         {"load_sales": (2, 0), "chart": (1, 0), **{b: (i, 1.3) for i, b in enumerate([*tried, "only_note", "empty"])}})

    blocks, edges, tried, places, n = {}, [], {}, {}, 0
    for row, condition in enumerate(if_examples(starters["If"])):
        for column, (value, true) in enumerate(zip(IF_CASES.get(condition, ()), (True, False))):
            n += 1
            blocks |= {f"value_{n}": {"type": "logic.python", "config": {"inputs": [], "code": f"output = {value!r}"}},
                       f"if_{n}": {"type": "logic.if", "config": {"condition": condition}}}
            edges.append(f"value_{n}.output -> if_{n}.value")
            tried[f"if_{n}"] = (condition, value, true)
            places |= {f"value_{n}": (2 * column, 1.2 * row), f"if_{n}": (2 * column + 1, 1.2 * row)}
    flow("If", "if-examples.yaml", blocks, edges, tried, places)
    return flows


def run_examples(env, name: str, shown) -> tuple[str, int, str, dict]:
    """taskloom run `name`, as in a terminal: what it printed for the blocks `shown` and its last line (file paths
    left out), its exit code, how the run ended, and what each block did."""
    result = env.taskloom("run", name, encoding="utf-8", errors="replace")
    run_id, status = env.last_run()
    lines = (result.stdout + result.stderr).strip().splitlines()
    printed = [re.sub(r" · [^·]*\.parquet$", "", line) for line in lines
               if any(f"[{block}] " in line for block in shown) and re.search(r"\] (success|failed) ·", line)]
    return "\n".join(printed + lines[-1:]), result.returncode, status, outcomes(env, run_id)


def if_examples(line: str) -> list[str]:
    """The examples of If's line: after Examples:, comma-separated."""
    return [e.strip() for e in line.split("Examples:", 1)[1].split(",")]


# `env` before `win`: its Taskloom folder is the editor's (the same temporary one), and it is made first.
def test_new_code_blocks_start_with_comments_that_teach_them(proof, env, win, qtbot, monkeypatch):
    p = proof("New code blocks start with comments that teach them; If starts with examples", ASKED)
    p.idea("A block you add from the Blocks list where you write code now starts with a few lines of comments that "
           "teach it, as Python Code already did: Run Query, DuckDB SQL, Polars Transform and HTML Template. Each is "
           "in its language's own comment form (SQL --, Python #, an HTML note <!-- -->), and shows, with examples "
           "you can copy, what the block gets, how to use the flow's parameters, and what goes out. A new If "
           "block's Condition starts with your line: " + IF_LINE + ".")
    p.idea("The comments change nothing. Left as they are, the code is only comments, and that counts as empty: Run "
           "Query and DuckDB SQL say “SQL is required”, Polars Transform “Code is required” and If “Condition is "
           "required”, in red in Properties, in Validate and in a run that is refused, as for an empty box. Nothing "
           "runs, so there is no database or Python error. An HTML Template with only its note shows every input in "
           "turn, as an empty Template does.")
    p.idea("Write your code under the comments (or between them) and it runs as written, the comments aside. DuckDB "
           "SQL, Polars Transform and If skip them. Run Query sends them to the database with the query, and "
           "databases skip comments. HTML Template leaves its notes out of the email, and a {name} written inside a "
           "note shows nothing. Two kinds of comment are kept as written, because they do something in an email: "
           "Outlook's conditional comments (<!--[if mso]> … <![endif]-->) and <style> blocks.")
    p.idea("Only new blocks get the comments: a block already in a flow file keeps its code, and so does a block you "
           "copy and paste. Inspect gets none: its Message stays empty, as an empty Message shows the whole value "
           "(and a # there is a Markdown heading, not a comment).")
    p.why_simplest("Each block says, in one place, what a new one starts with: the place Python Code's example "
                   "already comes from. Dragging a block from the Blocks list and double-clicking it there both use "
                   "it; opening a file, pasting, undo and redo take a block as it is. So the comments are plain code "
                   "in the box: saved in the flow file, there to copy from while you write, and yours to delete.")
    p.why_simplest("One rule makes them harmless: required code that is only comments and blank lines counts as "
                   "empty. An empty setting already had its message, so it is the same message, given before "
                   "anything runs. Python Code is the exception, as before: its code is a script that runs as it is, "
                   "comments and all.")
    p.why_simplest("Each language already skips its own comments: DuckDB, Python (for Polars Transform and If) and "
                   "databases. Only HTML Template has to leave its notes out, because a note's {name} examples "
                   "would otherwise be taken for real ones.")
    p.alternative("Grey hint text in an empty box",
                  "It disappears as soon as you type, cannot be copied from, and is not in the flow file. You asked "
                  "for the box to be prefilled with comments, as Python Code's is.")
    p.alternative("Let a block whose code is only comments run, and do nothing",
                  "Run Query would send only comments to the database, DuckDB SQL would give no table, and Polars "
                  "Transform and If would have no expression: each would fail with its own error, once running. “SQL "
                  "is required” says what is missing, before anything runs.")
    p.alternative("Take the comments out of Run Query's SQL before sending it",
                  "Databases already skip comments. Sent as written, what runs is exactly what you see in the box.")
    p.alternative("Fill in the comments in blocks already in flow files too",
                  "It would change flows you never touched, also those the scheduler runs. Only the editor knows that "
                  "a block is new, so the editor is where the comments are written.")
    p.fits("It is how Python Code's example already works: the same place says what a new block starts with, and "
           "only a block added from the Blocks list gets it.")
    p.fits("“… is required” is the message an empty setting always gave: in red at the top of Properties, in "
           "Validate's box and the Log, in a refused ▶ Run now, and from taskloom validate and taskloom run.")
    p.fits("The If line uses what If already understands: value is what reaches its value input, and the flow's "
           "parameters and Taskloom's functions (such as is_business_day(today())) work as before.")
    p.fits("Every example in the comments is used as code in a real run at the end of this proof, so none of them "
           "teaches something that does not work.")
    try:
        see_it_working(p, env, win, qtbot, monkeypatch)
    finally:
        for each in win.tabs_list():
            each.doc.dirty = False  # closing the window must not ask to save them, also after a check that failed


def see_it_working(p, env, win, qtbot, monkeypatch):
    """The steps of the proof above."""
    java_home = _java_home()
    if not java_home:
        pytest.skip("Java is not installed: Run Query needs it")
    assert win.home.root == env.home, "the editor and the command line use the same Taskloom folder"
    env.env["PYTHONIOENCODING"] = "utf-8"  # what taskloom run prints, as a terminal shows it (on Windows too)
    shop = env.write("shop.sql", SHOP_SQL)
    env.write("home/settings.yaml", f"java_home: '{Path(java_home).as_posix()}'\n")
    env.write("home/connections.yaml", yaml.safe_dump({"shop": {
        "kind": "jdbc", "driver": "org.h2.Driver", "jars": [_h2_jar().as_posix()],
        "url": f"jdbc:h2:mem:;INIT=RUNSCRIPT FROM '{shop.as_posix()}'"}}))
    # Properties wide enough to show the comments' lines whole, and Data preview tall enough for five rows.
    win.resizeDocks([win.inspector], [660], Qt.Horizontal)
    win.resizeDocks([win.output], [280], Qt.Vertical)
    QTest.qWait(100)

    # --- new blocks start with their comments ----------------------------------------------------------------------
    p.step("Open the flow sales-report.yaml (File > Open…).")
    path = env.root / "sales-report.yaml"
    path.write_text(yaml.safe_dump(FLOW, sort_keys=False), encoding="utf-8")
    tab = open_flow(win, monkeypatch, path)
    p.note("Properties is made wider than at first, so the comments' lines show whole, and the panel under the "
           "canvas taller, for Data preview's rows: as you would by dragging their edges.")
    p.shot(win, "The flow: start runs load_sales, a DuckDB SQL block already in the file (five sales, of 29 and 30 "
                "October), and count_orders, which gives 12. over_10 and not_over_10 are there to show an If's two "
                "paths. The flow has a parameter, day.",
           highlight=[list(FLOW["blocks"])], zoom=list(FLOW["blocks"]))
    click(tab, "load_sales")
    p.check("load_sales, already in the flow file, keeps its own query: no comments were added to it",
            code_box(win).toPlainText() == SALES_SQL)

    starters = {}
    for title, (name, corner, key, form) in NEW.items():
        p.step(f"Drag {title} from the Blocks list onto the canvas, and click it.")
        block = drop(win, tab, title, corner)
        click(tab, block)
        box = code_box(win)
        code = starters[title] = box.toPlainText()
        p.shot(win, f"① The new {title} block, {block}. ② Its code box, in Properties, starts with {form}.",
               highlight=[block, box], badges=True, zoom=[box])
        p.check(f"① A new {title} block is added, named {name}", block == name)
        p.check(f"② Its code box shows the block's code, which is only {form}",
                tab.doc.blocks[block]["config"].get(key) == code and COMMENTS[title](code))
        if title == "If":
            p.check("It is your line: " + IF_LINE, code == IF_LINE)

    p.step("Drag Inspect and Python Code onto the canvas too, and click each. Then take them out again: Edit > Undo, "
           "twice.")
    inspect = drop(win, tab, "Inspect", QPointF(0, 330))
    python = drop(win, tab, "Python Code", QPointF(0, 170))
    click(tab, inspect)
    p.check("A new Inspect block's Message is empty, as before",
            not tab.doc.blocks[inspect]["config"].get("message") and code_box(win).toPlainText() == "")
    click(tab, python)
    box = code_box(win)
    p.shot(win, f"① The new Python Code block, {python}: ② its Code starts with its own example, as before (its first "
                f"lines here). The new Inspect block beside it, {inspect}, clicked just before, has an empty Message.",
           highlight=[python, box], badges=True, zoom=top_of(win, box, 330))
    p.check("② A new Python Code block starts with its own example, as before",
            box.toPlainText() == tab.doc.blocks[python]["config"].get("code") == PythonCode.new_config["code"])
    edit_menu(win, "Undo")
    edit_menu(win, "Undo")
    p.check("After Edit > Undo, twice, both are gone again", python not in tab.doc.blocks and inspect not in tab.doc.blocks)

    # --- as they are: their code is required -----------------------------------------------------------------------
    p.step("Connect the new blocks: load_sales' result to duckdb_sql, polars and html_template; count_orders' output "
           "to if, and if's true and false to over_10 and not_over_10; start to run_query. Click run_query and, in "
           "Properties, choose the connection shop.")
    for edge in CONNECTIONS:
        drag_port_to_port(tab, *edge)
        settle()
    click(tab, "run_query")
    combo = next(c for c in win.properties.findChildren(QComboBox) if c.findData("shop") >= 0)
    combo.setCurrentIndex(combo.findData("shop"))
    combo.activated.emit(combo.currentIndex())  # as a click on it does
    settle()
    combo = next(c for c in win.properties.findChildren(QComboBox) if c.findData("shop") >= 0)  # (Properties redrawn)
    p.note("shop is a database that runs inside the run itself: H2, in memory, with three small tables (orders, sales "
           "and customers), set up in this proof's own Taskloom folder. No database server is involved.")
    p.shot(win, "① Every new block is connected. ② run_query's Connection is shop. ③ The red line at the top of "
                "Properties says what run_query still needs: SQL is required.",
           highlight=[list(tab.doc.blocks), combo, win.properties.error], badges=True,
           zoom=[win.properties.error, combo])
    p.check("① Every new block is connected as said", set(CONNECTIONS) <= set(tab.doc.edges()))
    p.check("② run_query's Connection is shop", tab.doc.blocks["run_query"]["config"].get("connection") == "shop")
    p.check("③ Properties says, in red: SQL is required",
            not win.properties.error.isHidden() and win.properties.error.text() == "SQL is required")

    p.step("Press Validate in the toolbar (F7).")
    said = []

    def look_at_the_problems(box):
        said.append(box.text())
        p.shot(box, "The flow has problems: the four new blocks whose code is still only their comments, each saying "
                    "its code is required, as for an empty box. html_template is not among them.",
               highlight=[box_text(box)])
        box.accept()

    with monkeypatch.context() as real:  # the box as the app shows it
        real.setattr(QMessageBox, "warning", REAL_WARNING)
        p.while_open(lambda: press(win, win.validate_action), look_at_the_problems)
    QTest.qWait(100)
    p.check("Validate lists four problems, each “… is required”: " + "; ".join(REQUIRED),
            said == ["\n".join(REQUIRED)])

    p.step("Press ▶ Run now.")
    refused = []

    def look_at_the_refusal(box):
        refused.append(box.text())
        box.accept()

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "warning", REAL_WARNING)
        p.while_open(lambda: press_run_now(win), look_at_the_refusal)
    QTest.qWait(100)
    p.check("▶ Run now is refused with the same box, the same four problems", refused == said)
    log = win.log.text
    errors = [(log, f"ERROR {problem}") for problem in REQUIRED]
    p.shot(win, "The Log, under the canvas, keeps the problems: four ERROR lines from Validate, then the same four "
                "from ▶ Run now, which refused to start. No run started, so nothing reached the database and no code "
                "ran.", highlight=[errors], zoom=[errors])
    p.check("The Log has each problem twice, as an ERROR, and no other ERROR",
            [line.split(" ERROR ", 1)[1] for line in log.toPlainText().splitlines() if " ERROR " in line]
            == REQUIRED * 2)
    p.check("No run started: Run history is empty, and nothing was recorded",
            not tab.runs and (not (env.home / "history.db").exists() or env.query("SELECT count(*) FROM runs") == [(0,)]))

    # --- code under the comments ------------------------------------------------------------------------------------
    for block, code in WRITTEN.items():
        p.step(f"Click {block}, click in its code box, press Ctrl+End, then Enter twice, and write “{code}”.")
        click(tab, block)
        write_below(win, code)
        box = code_box(win)
        title = next(t for t, (name, *_) in NEW.items() if name == block)
        p.shot(win, f"{block}'s code box: its comments, and under them, outlined, what you wrote. The red line at the "
                    "top of Properties is gone.", highlight=[last_line(win, box)], zoom=[box])
        p.check(f"{block}'s code is its comments, a blank line and “{code}”",
                tab.doc.blocks[block]["config"].get(NEW[title][2]) == starters[title] + "\n\n" + code)
        p.check("Properties shows no problem", win.properties.error.isHidden())

    p.step("Press ▶ Run now, and Run in the box that shows the flow's parameter.")
    first = run_now(p, win, qtbot, tab, 1, shoot_dialog=True)
    p.shot(win, "Every block ran but not_over_10, which is skipped: count_orders gave 12, and ① if took its true path, "
                "to ② over_10, which shows 12.",
           highlight=["if", "over_10"], badges=True, zoom=["count_orders", "if", "over_10", "not_over_10"])
    p.check(f"Run #{first.run_id} succeeded", first.status == "success")
    p.check("① if took its true path for 12: ② over_10 ran and not_over_10 was skipped",
            tab.scene.nodes["over_10"].status == "success" and tab.scene.nodes["not_over_10"].status == "skipped")

    p.step("Click the Data preview tab, under the canvas, and click duckdb_sql.")
    click_bottom_tab(win, win.preview)
    click(tab, "duckdb_sql")
    rows = preview_rows(win)
    p.shot(win, f"Data preview: duckdb_sql gave the three sales of {DAY}, the day its parameter day says.",
           highlight=preview_rows_shown(win), zoom=preview_table(win))
    p.check(f"duckdb_sql gave the three sales of {DAY}",
            win.preview.label.text() == "duckdb_sql.result: 3 rows × 3 columns"
            and sorted(rows) == sorted((d, r, str(a)) for d, r, a in SALES if d == DAY))

    p.step("Click polars.")
    click(tab, "polars")
    rows = preview_rows(win)
    p.shot(win, "Data preview: polars gave load_sales' five sales, with a new column, gross: the amount times 1.2.",
           highlight=preview_rows_shown(win), zoom=preview_table(win))
    p.check("polars gave the five sales, with gross, the amount times 1.2",
            preview_columns(win) == ["day", "region", "amount", "gross"] and len(rows) == 5
            and all(float(r[3]) == round(int(r[2]) * 1.2, 6) for r in rows))

    p.step("Click run_query.")
    click(tab, "run_query")
    p.shot(win, "Data preview: run_query gave the number of orders of each region, from the database shop.",
           highlight=preview_rows_shown(win), zoom=preview_table(win))
    p.check("run_query gave the orders of each region: East 1, North 3, South 2",
            sorted(preview_rows(win)) == [("East", "1"), ("North", "3"), ("South", "2")])
    p.note("Run Query sends everything in its box to the database, the comments too. Databases skip comments, so the "
           "query runs as written: H2 did here.")

    p.step("Click html_template, whose Template is still only its note.")
    click(tab, "html_template")
    shown = win.preview.text.toPlainText()
    p.shot(win, "Data preview: html_template's html, the email body it made, is load_sales' table, as an empty "
                "Template gives. The note is not in it.",
           highlight=[(win.preview.text, "<table cellpadding")], zoom=preview_text(win))
    p.check("html_template's html is a table of load_sales' rows, without the note",
            shown.startswith("<table") and all(r in shown for r in MANAGERS) and NOTE not in shown
            and "<!--" not in shown)

    p.step(f"Click in html_template's Template, press Ctrl+End, then Enter twice, and write “{BODY}”. Press ▶ Run "
           "now again, and click the Data preview tab.")
    write_below(win, BODY)
    second = run_now(p, win, qtbot, tab, 2)
    click_bottom_tab(win, win.preview)  # (a run shows the Log)
    shown = win.preview.text.toPlainText()
    p.shot(win, "Data preview: html_template's html is your body, with load_sales' table where {load_sales} is. The "
                "note is not in it: only the two line breaks you typed after it are (an email shows none).",
           highlight=[(win.preview.text, "<b>Daily sales</b>")], zoom=preview_text(win, 4))
    p.check(f"Run #{second.run_id} succeeded", second.status == "success")
    p.check("The html is your body, the table in place of {load_sales}, and the note is not in it",
            shown.startswith("\n\n<b>Daily sales</b><br>Good morning, the figures are below.<br><table")
            and shown.endswith("</table>") and NOTE not in shown and "<!--" not in shown)

    # --- every example works ----------------------------------------------------------------------------------------
    flows = examples_flows(starters)
    for kind, expected in (("DuckDB SQL", DUCKDB), ("Polars Transform", POLARS), ("Run Query", RUN_QUERY),
                           ("HTML Template", HTML), ("If", None)):
        name, flow, tried = flows[kind]
        an = "an" if kind in ("If", "HTML Template") else "a"
        p.step(f"Open {name} in the editor (File > Open…): a flow with {an} {kind} block for "
               + ("each example in its line, with a value it is true for and one it is false for." if kind == "If"
                  else "each example in its comments, written as its code.")
               + f" Then, in a terminal, in the folder of the flows, run taskloom run {name}.")
        if kind == "DuckDB SQL":
            p.note("Each of these flows takes the examples from the comments that the new blocks showed above, as they "
                   f"are. Their parameters are day ({DAY}), regions ({', '.join(REGIONS)}) and total ({TOTAL}), and "
                   "load_sales gives the five sales of the flow above. Here the join example's block has Inputs sales "
                   "and regions, as its comment says, with load_regions giving each region's manager.")
        elif kind == "Run Query":
            p.note("Run Query's examples run on the database shop, with its tables orders, sales and customers.")
        elif kind == "HTML Template":
            p.note("The chart example's block gets the picture of chart, a Chart block, into its data input, where it "
                   "is named chart. only_note's Template is the comments as a new block has them, and empty has no "
                   "Template: both get load_sales' table.")
        shown = list(tried) + (["only_note", "empty"] if kind == "HTML Template" else [])
        (env.root / name).write_text(yaml.safe_dump(flow, sort_keys=False), encoding="utf-8")
        opened = open_flow(win, monkeypatch, env.root / name)
        printed, exit_code, status, ran = run_examples(env, name, shown)
        p.text(f"taskloom run {name} printed this for each example's block, and its last line (file paths left out)",
               printed)
        qtbot.waitUntil(lambda: all(opened.scene.nodes[b].status is not None for b in shown), timeout=20_000)
        p.shot(win, f"{name} in the editor shows the run from the terminal once it ends: each example's block (outlined) "
                    + ("succeeded, and says which path it took: true or false, with the value." if kind == "If" else
                       "succeeded, and says what it gave."),
               highlight=shown if kind == "If" else [shown], zoom=list(flow["blocks"]) if kind == "If" else shown)
        p.check(f"taskloom run {name} ended with success, and exit code {exit_code}",
                exit_code == 0 and status == "success")
        p.check(f"The editor shows each example's block of {name} as succeeded, in that run",
                all(opened.scene.nodes[b].status == "success" for b in shown))
        if kind == "If":
            p.check("If's line holds three examples, each tried with a value it is true for and one it is false for",
                    if_examples(starters["If"]) == list(IF_CASES))
            for block, (condition, value, true) in tried.items():
                p.check(f"If · {condition}, for {value!r} → the {'true' if true else 'false'} path",
                        ran[block][0] == "success" and list(ran[block][2]) == ["true" if true else "false"])
            continue
        p.check(f"The {kind} comments hold {len(expected)} examples, each tried here",
                list(tried.values()) == list(expected))
        for block, example in tried.items():
            what, ok = expected.get(example, ("?", lambda _: False))
            status_, rows, previews = ran[block]
            p.check(f"{kind} · {example} → {what}",
                    status_ == "success" and ok(previews.get("html") if kind == "HTML Template" else rows))
        if kind == "HTML Template":
            p.check("only_note gives the same html as empty: load_sales' table, every input in turn",
                    ran["only_note"][2]["html"] == ran["empty"][2]["html"]
                    and ran["only_note"][2]["html"].startswith("<table"))
