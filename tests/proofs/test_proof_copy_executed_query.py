"""Proof: right-click a run in Run history > Copy executed query copies the SQL a query block sent in that run, with
its values written in, ready to paste into a SQL tool (stage 4 of the 2026-10-30 feedback). On a real H2 database
(needs Java), through JDBC. How proofs are written: README.md here."""

import subprocess
from pathlib import Path

import duckdb
import yaml
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from proofs.conftest import choose, menu_item
from proofs.test_proof_ask_user_box import answer_box, around, scroll_log_to
from proofs.test_proof_handled_error import click_bottom_tab, open_flow, press_run_now
from proofs.test_proof_run_output import click as click_block
from taskloom.editor.panels import CodeEdit
from tests.test_lines import wait_runs
from tests.test_paths_editor_running import click, visible_ask

ASKED = ("On the run history I should be able to right click a historical run and click on 'Copy executed query' "
         "which will put the query that was run at the time with the parameters replaced, the real/actual query "
         "that hit the query engine — Decided with you: copy the version with the values written in, so it can be "
         "pasted into a SQL tool such as DbVisualizer and run; a secret is shown as <hidden: secret NAME>.")
COPY = "Copy executed query"
NO_QUERY = "Copy executed query (no query ran in this run)"
# The proof's database, in the H2 server's memory: kept while the server runs, between the connections to it.
URL = "jdbc:h2:tcp://localhost:{port}/mem:shop;DB_CLOSE_DELAY=-1"
SALES = """\
create table sales (customer varchar(40), region varchar(20), amount decimal(10, 2), sale_day date);
insert into sales values
  ('O''Brien', 'north', 10.50, date '2026-01-16'),
  ('O''Brien', 'east', 7.00, date '2026-01-16'),
  ('O''Brien', 'south', 4.75, date '2026-01-16'),
  ('O''Brien', 'north', 3.20, date '2026-01-15'),
  ('Smith', 'north', 8.00, date '2026-01-16')"""
PARAMS = {"customer": {"type": "text", "value": "O'Brien"}, "day": {"type": "date", "value": "2026-01-16"},
          "regions": {"type": "list", "value": "north, east"}}
LOAD_SQL = """\
select customer, region, amount
from sales
where customer = :customer
  and sale_day = :day
  and region in (:regions)  -- which regions? those in :regions
order by region"""
LOAD_COPIED = """\
select customer, region, amount
from sales
where customer = 'O''Brien'
  and sale_day = DATE '2026-01-16'
  and region in ('north', 'east')  -- which regions? those in :regions
order by region"""
SUM_SQL = """\
select :customer as customer, :day as day, count(*) as sales, sum(amount) as total
from sales"""
SUM_COPIED = """\
select 'O''Brien' as customer, DATE '2026-01-16' as day, count(*) as sales, sum(amount) as total
from sales"""
API_KEY = "Blue-Heron-58-key"  # what you type in ask_key's box (made up for the proof)
KEY_SQL = "select :region as region, length(cast(:api_key as varchar)) as key_length"
KEY_COPIED = "select 'north' as region, length(cast(<hidden: secret api_key> as varchar)) as key_length"
RETURNS_SQL = """\
select customer, amount
from returns
where customer = :customer
  and return_day = :day"""
RETURNS_COPIED = """\
select customer, amount
from returns
where customer = 'O''Brien'
  and return_day = DATE '2026-01-16'"""
REFUSED = 'Table "RETURNS" not found'  # what H2 says


def query_flow(path: Path, params: dict, blocks: dict, edges: list[str]) -> Path:
    """A flow file: the Manual Start by_hand, then `blocks` in a row, left to right (a block with its own "ui" is
    where that puts it)."""
    laid_out = {"by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}}}
    for i, (name, spec) in enumerate(blocks.items(), 1):
        laid_out[name] = {"ui": {"x": 240 * i, "y": 0}, **spec}
    flow = {"params": params, "blocks": laid_out, "edges": edges}
    path.write_text(yaml.safe_dump(flow, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def h2_shell(h2, sql: str) -> str:
    """H2's own SQL shell (the Shell tool in the H2 jar: a SQL tool outside Taskloom) running `sql` on the proof's
    database: what it printed."""
    done = subprocess.run([str(Path(h2["java_home"]) / "bin" / "java"), "-cp", str(h2["jar"]), "org.h2.tools.Shell",
                           "-url", URL.format(port=h2["port"]), "-user", "sa", "-password", "", "-sql", sql],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    printed = (done.stdout + done.stderr).strip()
    assert done.returncode == 0 and not printed.startswith("Error"), printed
    return printed


def shell_rows(printed: str) -> list[list[str]]:
    """The rows H2's shell printed: under its header, above its "(2 rows, 5 ms)"."""
    return [[cell.strip() for cell in line.split("|")] for line in printed.splitlines()[1:]
            if not line.startswith("(")]


def lined_up(header: list[str], rows: list[list[str]]) -> str:
    """Rows as H2's shell prints them: in columns, separated by |."""
    widths = [max(len(r[c]) for r in [header, *rows]) for c in range(len(header))]
    return "\n".join(" | ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip() for r in [header, *rows])


def preview(win) -> tuple[list[str], list[list[str]]]:
    """What the Data preview shows: its columns' names, and its rows as text."""
    model = win.preview.view.model()
    names = [model.headerData(c, Qt.Horizontal).split("\n")[0] for c in range(model.columnCount())]
    return names, [[model.data(model.index(r, c)) for c in range(model.columnCount())]
                   for r in range(model.rowCount())]


def preview_area(win) -> QRect:
    """The Data preview's columns and rows, with the line above them (not the empty space beside and under them),
    in the window's pixels."""
    view = win.preview.view
    model = view.model()
    cells = QRect()
    for r in range(model.rowCount()):
        for c in range(model.columnCount()):
            cells |= view.visualRect(model.index(r, c))
    cells.translate(view.viewport().mapTo(win, QPoint(0, 0)))
    top_left = win.preview.label.mapTo(win, QPoint(0, 0))
    return QRect(top_left, cells.bottomRight()) | QRect(top_left, win.preview.label.size())


def status_area(win) -> QRect:
    """The message in the status bar, in the window's pixels."""
    bar = win.statusBar()
    return QRect(bar.mapTo(win, QPoint(0, 0)),
                 QSize(bar.fontMetrics().horizontalAdvance(bar.currentMessage()) + 24, bar.height()))


def log_lines(win, first: str, last: str) -> QRect:
    """The lines of the Log from the one showing `first` to the one showing `last`, in the window's pixels."""
    box = win.log.text
    top = box.cursorRect(box.document().find(first)).top()
    bottom = box.cursorRect(box.document().find(last)).bottom()
    return QRect(QPoint(0, top), QPoint(box.viewport().width() - 1, bottom)).translated(
        box.viewport().mapTo(win, QPoint(0, 0))).adjusted(0, -2, 0, 2)


def run_row(win, run_id: int) -> int:
    table = win.history_panel.table
    return next(r for r in range(table.rowCount()) if table.item(r, 0).text() == str(run_id))


def run_cells(win, run_id: int) -> list:
    """Run `run_id`'s row in Run history, from Run to Status (to show it bigger)."""
    table = win.history_panel.table
    return [table.item(run_row(win, run_id), 0), table.item(run_row(win, run_id), 3)]


def right_click_run(win, run_id: int):
    """Right-click run `run_id` in Run history, as you would: the button goes down on its row, which selects it, then
    its menu opens (it waits for a choice: see Proof.while_open)."""
    table = win.history_panel.table
    viewport = table.viewport()
    at = table.visualItemRect(table.item(run_row(win, run_id), 0)).center()
    where = viewport.mapToGlobal(at)
    for kind, buttons in ((QEvent.MouseButtonPress, Qt.RightButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
        QApplication.sendEvent(viewport, QMouseEvent(kind, QPointF(at), QPointF(where), Qt.RightButton, buttons,
                                                     Qt.NoModifier))
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, where))


def open_submenu(qtbot, menu):
    """Click Copy executed query in the run's menu: the list of the run's queries opens beside it. The list."""
    item = menu_item(menu, COPY)
    QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(item).center())
    qtbot.waitUntil(lambda: item.menu() is not None and item.menu().isVisible(), timeout=5_000)
    return item.menu()


class WithMenu:
    """The window with an open menu drawn over it where it is on the screen: a submenu is shot over the two."""

    def __init__(self, win, menu):
        self.win, self.menu, self.current = win, menu, win.current

    def grab(self):
        picture = self.win.grab()
        painter = QPainter(picture)
        painter.drawPixmap(self.menu.mapToGlobal(QPoint(0, 0)) - self.win.mapToGlobal(QPoint(0, 0)), self.menu.grab())
        painter.end()
        return picture

    def mapToGlobal(self, point):
        return self.win.mapToGlobal(point)


def right_click_canvas(tab, at: QPoint):
    """Right-click an empty spot of the canvas (its menu waits for a choice)."""
    viewport = tab.view.viewport()
    assert tab.view.itemAt(at) is None, "the spot is empty"
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, viewport.mapToGlobal(at)))


def run_now(p, win, caption: str | None = None):
    """Press ▶ Run now, then Run in the window that shows the values of the flow's parameters (shot first, with
    `caption`)."""
    def run_it(dialog):
        if caption:
            p.shot(dialog, caption)
        click(dialog, "Run")

    p.while_open(lambda: press_run_now(win), run_it)


def activate(qtbot, win):
    """The window is the one you type in. On the screen it is, once you click in it; here the menus and windows
    that were open over it leave none active."""
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=5_000)


def clipboard() -> str:
    return QApplication.clipboard().text()


def status(win) -> str:
    return win.statusBar().currentMessage()


# `jdbc_env` before `win`: its Taskloom folder is the editor's (the same temporary one), and it is made first.
def test_copy_executed_query_copies_the_sql_a_run_sent_with_its_values_written_in(proof, jdbc_env, h2_server, win,
                                                                                    qtbot, monkeypatch):
    p = proof("Run history: Copy executed query", ASKED)
    p.idea("Right-click a run in Run history and choose Copy executed query: the clipboard then holds the SQL a Run "
           "Query or DuckDB SQL block sent in that run, with each value written in where it went, ready to paste "
           "into a SQL tool such as DbVisualizer and run.")
    p.idea("Each value is written as SQL: a text in single quotes, with a quote in it doubled (O'Brien is "
           "'O''Brien'), a number as it is, TRUE or FALSE, NULL, a date as DATE '2026-01-16', a date and time as "
           "TIMESTAMP '…', a list item by item, separated by commas. The rest is the SQL exactly as the block sent "
           "it, its comments too. A secret is never written in: you get <hidden: secret NAME>, NAME the name the "
           "query gives it.")
    p.idea("With one query in the run, the item copies it. With several, it opens a list of the blocks that sent "
           "them, by name and type, in the order they ran: choose one. A run in which no query ran shows the item "
           "greyed out, saying so. The status bar says what was copied.")
    p.idea("Nothing changes in how a query runs: Taskloom still sends the values to the database separately from "
           "the SQL (bound parameters), as before. The copy writes them in only so that you can read the query, or "
           "run it by hand.")
    p.why_simplest("Each query block keeps one thing in the run history, just before its query goes to the "
                   "database: the SQL as it sends it (each :name a ?, a list one ? per item) and each value it binds, "
                   "already written as SQL. Run Query and DuckDB SQL keep it at the same moment, in the same way. "
                   "Copying then only puts each value in the place of its ?, in order.")
    p.why_simplest("Because it is kept as the query is sent, nothing has to be worked out again later: not from the "
                   "flow (which you may have changed since), not from the parameters. And a query the database "
                   "refuses is kept too: it was kept before the database answered.")
    p.alternative("Rebuild the query when you copy it, from the flow file and the run's parameters",
                  "The flow may have changed since that run, and a value that came from an earlier block (Run "
                  "Query's params input) is not among the run's parameters: it would not be the query that hit the "
                  "database, which is what you asked for.")
    p.alternative("Send the query with the values written in, instead of separately",
                  "Then the copy would be exactly what was sent, but every query Taskloom runs would change, to show "
                  "one. A value sent separately can never be taken for SQL, whatever it holds: a quote, as in "
                  "O'Brien, or a semicolon.")
    p.alternative("Copy the SQL with its ?s, and the values in a list under it",
                  "That is literally what the database got, but a SQL tool cannot run it as pasted. Decided with "
                  "you: the values written in.")
    p.alternative("Write each query in the Log",
                  "Every run would fill the Log with long queries, burying its messages, and you would still have to "
                  "find the query and select it by hand.")
    p.fits("Run history now has a right-click menu, like the blocks' own; its one item is Copy executed query. It "
           "reads the same run history as the canvas and Results of this block…. Runs started anywhere keep their "
           "queries: Run now, the scheduler service and a terminal all start the same kind of run, which writes that "
           "history.")
    p.fits("The ?s are found by the same reading of SQL that finds the :name parameters: a ? or a :name in quotes or "
           "in a comment is not a parameter, and is left as it is.")
    p.fits("A secret stays out of the run history, as everywhere in Taskloom (the Log shows it as ***): only its "
           "name is kept, so that the copy says which value to fill in.")

    # The proof's database: the table sales, in H2, made with H2's own SQL shell; the connection warehouse uses it.
    jdbc_env.write("home/connections.yaml", f"""
        warehouse:
          kind: jdbc
          driver: org.h2.Driver
          url: "{URL.format(port=h2_server['port'])}"
          jars: ["{h2_server['jar'].as_posix()}"]
          properties: {{user: sa, password: "secret:warehouse_password"}}
    """)
    h2_shell(h2_server, SALES)
    # daily_total runs after load_sales (it reads its rows), though it comes first by name and in the file.
    sales = query_flow(jdbc_env.root / "customer_sales.yaml", PARAMS, {
        "daily_total": {"type": "data.duckdb_sql", "config": {"inputs": ["sales"], "sql": SUM_SQL},
                        "ui": {"x": 480, "y": 0}},
        "load_sales": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": LOAD_SQL},
                       "ui": {"x": 240, "y": 0}}},
        ["by_hand.start -> load_sales.after", "load_sales.result -> daily_total.sales"])

    # --- a run with two queries -------------------------------------------------------------------------------------
    p.step("Open the flow customer_sales.yaml (File > Open…), and click Parameters, on the right. load_sales, a Run "
           "Query block, loads a customer's sales of a day in some regions from the database (H2, through JDBC); "
           "daily_total, a DuckDB SQL block, adds them up: it runs after load_sales, whose rows it reads, though its "
           "name comes first and the flow file lists it first.")
    p.text("The table sales in the database", h2_shell(h2_server, "select * from sales order by customer, sale_day, "
                                                                  "region"))
    tab = open_flow(win, monkeypatch, sales)
    win.right_tabs.setCurrentWidget(win.parameters)
    params = win.parameters.table
    values = [params.item(r, 3) for r in range(params.rowCount())]
    p.shot(win, "① The two query blocks. ② Their parameters: customer, a text with a quote in it, O'Brien; day, a "
                "date; regions, a list of two.",
           highlight=[["load_sales", "daily_total"], [params.item(0, 0), values[-1]]], badges=True,
           zoom=[params.item(0, 0), values[-1]])
    in_file = [b for b in yaml.safe_load(sales.read_text(encoding="utf-8"))["blocks"] if b != "by_hand"]
    p.check("The flow file lists daily_total, then load_sales: as their names sort, not as they run",
            in_file == sorted(in_file) == ["daily_total", "load_sales"])
    p.check("The flow's parameters are customer O'Brien, day 2026-01-16 and regions north, east",
            [(params.item(r, 0).text(), values[r].text()) for r in range(params.rowCount())]
            == [("customer", "O'Brien"), ("day", "2026-01-16"), ("regions", "north, east")])

    p.step("Press ▶ Run now in the toolbar, then Run in the window that shows the parameters' values.")
    run_now(p, win, "The values the run sends with its queries: O'Brien, 2026-01-16, and the list of north and east.")
    run = wait_runs(qtbot, tab, 1)
    p.shot(win, "① Both blocks ran: load_sales gave 2 rows, daily_total 1.", highlight=[["load_sales", "daily_total"]],
           badges=True, zoom=["by_hand", "daily_total"])
    p.check("The run succeeded: load_sales and daily_total show success",
            tab.runs[0].status == "success"
            and [tab.scene.nodes[b].status for b in ("load_sales", "daily_total")] == ["success", "success"])

    # --- the list of the run's queries ------------------------------------------------------------------------------
    p.step(f"Click the Run history tab, under the canvas, right-click run {run}, and click {COPY}.")
    click_bottom_tab(win, win.history_panel)
    QApplication.clipboard().setText("")
    listed = []

    def look_at_the_list(menu):
        sub = open_submenu(qtbot, menu)
        listed.extend(a.text() for a in sub.actions())
        p.shot(sub, f"① {COPY} opens ② the list of the run's queries: each block that sent one, by name and type, in "
                    "the order they ran.", over=WithMenu(win, menu),
               highlight=[menu_item(menu, COPY), [menu_item(sub, "load_sales"), menu_item(sub, "daily_total")]],
               badges=True, zoom=[menu, sub, *run_cells(win, run)])
        p.check("Opening the list copied nothing yet", clipboard() == "")
        choose(sub, "load_sales (Run Query)")

    p.while_open(lambda: right_click_run(win, run), look_at_the_list)
    p.check("The list shows load_sales (Run Query), then daily_total (DuckDB SQL): the order they ran in, not by "
            "name nor as in the flow file",
            listed == ["load_sales (Run Query)", "daily_total (DuckDB SQL)"])

    p.step("Choose load_sales (Run Query).")
    copied = clipboard()
    p.shot(win, f"① The status bar says what was copied: the query of load_sales, in run #{run}.",
           highlight=[status_area(win)], badges=True, zoom=[status_area(win), *run_cells(win, run)])
    p.check(f"The status bar says “Copied the query of load_sales, run #{run}”",
            status(win) == f"Copied the query of load_sales, run #{run}")

    # --- what it copied ---------------------------------------------------------------------------------------------
    p.step("To see what you copied, right-click an empty spot under the blocks and choose Add text here; then click in "
           "the new text's Text box, in Properties, press Ctrl+A, then Ctrl+V, and click an empty spot of the canvas.")
    spot = tab.view.mapFromScene(QPointF(0, 160))
    p.while_open(lambda: right_click_canvas(tab, spot), lambda menu: choose(menu, "Add text here"))
    (note,) = tab.doc.notes
    editor = win.properties.findChild(CodeEdit)
    activate(qtbot, win)
    QTest.mouseClick(editor.viewport(), Qt.LeftButton)
    QTest.keyClick(editor, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(editor, Qt.Key_V, Qt.ControlModifier)
    click_block(tab)
    pasted = tab.scene.note_items[note]
    tab.doc.dirty = False  # (the text is not saved: closing the window at the end asks nothing)
    text_area = tab.view.mapFromScene(pasted.sceneBoundingRect()).boundingRect().translated(
        tab.view.viewport().mapTo(win, QPoint(0, 0)))
    p.shot(win, "① What you copied: the query load_sales sent, each value written in where its parameter was. The "
                "text in quotes, its quote doubled: 'O''Brien'. The date as DATE '2026-01-16'. The list's two items, "
                "separated by a comma. The ? and the :regions in the comment are left as they are: they are not "
                "parameters.", highlight=[text_area], badges=True, zoom=[text_area])
    p.check("The clipboard holds exactly what the close-up shows: the SQL load_sales sent, its values written in",
            copied == LOAD_COPIED)
    p.check("The text on the canvas shows it, as pasted", pasted.toPlainText() == LOAD_COPIED)

    # --- it runs as pasted ------------------------------------------------------------------------------------------
    p.step("Paste it into a SQL tool and run it: here, H2's own SQL shell, on the same database. Then click "
           "load_sales and the Data preview tab, to see the rows it gave in the run.")
    printed = h2_shell(h2_server, copied)
    p.note("A SQL tool is outside Taskloom: the proof hands what you copied, as it is, to H2's own SQL shell (the "
           "Shell tool in the H2 driver's jar), as you would paste it into DbVisualizer.")
    p.text("H2's SQL shell printed", printed)
    click_block(tab, "load_sales")
    click_bottom_tab(win, win.preview)
    gave = preview(win)
    p.shot(win, "① load_sales' rows from the run, in the Data preview: the very rows H2's shell gave for what you "
                "copied. O'Brien's sales of 2026-01-16 in east and north.",
           highlight=[preview_area(win)], badges=True, zoom=[preview_area(win)])
    p.check("H2's shell runs it as pasted, and gives the very rows load_sales gave in the run",
            shell_rows(printed) == gave[1] and [row[:2] for row in gave[1]] == [["O'Brien", "east"],
                                                                                ["O'Brien", "north"]])
    p.note("The values are written as standard SQL, as H2, DuckDB and many other databases read them. A database "
           "that spells one of them its own way (a date, TRUE or FALSE) needs that value retyped.")

    p.step(f"Click the Run history tab, right-click run {run} again, click {COPY}, and choose daily_total "
           "(DuckDB SQL).")
    click_bottom_tab(win, win.history_panel)

    def choose_daily_total(menu):
        sub = open_submenu(qtbot, menu)
        p.shot(sub, "① The DuckDB SQL block's query, chosen in the list.", over=WithMenu(win, menu),
               highlight=[menu_item(sub, "daily_total")], badges=True, zoom=[menu, sub, *run_cells(win, run)])
        choose(sub, "daily_total (DuckDB SQL)")

    p.while_open(lambda: right_click_run(win, run), choose_daily_total)
    summed = clipboard()
    p.text("What you copied", summed)
    p.check("It is the SQL daily_total sent, its values written in, as shown above", summed == SUM_COPIED)
    p.check(f"The status bar says “Copied the query of daily_total, run #{run}”",
            status(win) == f"Copied the query of daily_total, run #{run}")

    p.step(f"Run it in DuckDB, outside Taskloom, with sales the rows load_sales gave in run {run} (in your SQL tool, "
           "sales is your own table). Then click daily_total and the Data preview tab.")
    with duckdb.connect() as con:
        rows = Path(tab.block_info["load_sales"]["tables"]["result"]).as_posix()
        con.execute(f"create view sales as select * from read_parquet('{rows}')")
        answer = con.sql(summed)
        ran = answer.columns, [[str(value) for value in row] for row in answer.fetchall()]
    p.text("DuckDB gave", lined_up(*ran))
    click_block(tab, "daily_total")
    click_bottom_tab(win, win.preview)
    summed_up = preview(win)
    p.shot(win, "① What daily_total gave in the run, in the Data preview: the row DuckDB gave for what you copied.",
           highlight=[preview_area(win)], badges=True, zoom=[preview_area(win)])
    p.check("DuckDB runs it as pasted, and gives the very row daily_total gave in the run: O'Brien, 2026-01-16, "
            "2 sales, 17.50", list(ran) == list(summed_up) and summed_up[1] == [["O'Brien", "2026-01-16", "2", "17.50"]])

    # --- a secret ---------------------------------------------------------------------------------------------------
    key_flow = query_flow(jdbc_env.root / "check_key.yaml", {"region": {"type": "text", "value": "north"}}, {
        "ask_key": {"type": "logic.ask_user", "config": {"prompt": "API key for the sales service",
                                                         "kind": "password"}},
        "as_params": {"type": "logic.python", "config": {"inputs": ["key"], "code": "output = {'api_key': key}"}},
        "check_key": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": KEY_SQL}}},
        ["by_hand.start -> ask_key.after", "ask_key.answer -> as_params.key", "as_params.output -> check_key.params"])
    p.step("Open check_key.yaml, press ▶ Run now and Run, type the API key in the box ask_key shows, and click OK. "
           "ask_key asks for a password; check_key, a Run Query block, gets it as its parameter api_key, and asks the "
           "database only how long it is.")
    key_tab = open_flow(win, monkeypatch, key_flow)
    run_now(p, win)
    box = visible_ask(qtbot, win)
    edit = answer_box(box)
    QTest.mouseClick(edit, Qt.LeftButton)
    QTest.keyClicks(edit, API_KEY)
    p.shot(box, f"① The key, masked as you type it ({API_KEY}, made up for this proof).", highlight=[around(edit, box)],
           badges=True)
    click(box, "OK")
    key_run = wait_runs(qtbot, key_tab, 1)
    click_block(key_tab, "check_key")
    click_bottom_tab(win, win.preview)
    key_length = preview(win)[1]
    p.shot(win, f"① check_key's result: the database got the key itself, {len(API_KEY)} characters long.",
           highlight=[preview_area(win)], badges=True, zoom=[preview_area(win)])
    p.check(f"The run succeeded, and the database got the key itself: check_key gave its length, {len(API_KEY)}",
            key_tab.runs[0].status == "success" and key_length == [["north", str(len(API_KEY))]])

    p.step(f"Click the Run history tab, right-click run {key_run} and choose {COPY}: check_key is the run's only query, "
           "so the item copies it.")
    click_bottom_tab(win, win.history_panel)

    def copy_the_only_one(menu):
        p.shot(menu, f"With one query in the run, ① {COPY} copies it.", over=win, highlight=[menu_item(menu, COPY)],
               badges=True, zoom=[menu, *run_cells(win, key_run)])
        choose(menu, COPY)

    p.while_open(lambda: right_click_run(win, key_run), copy_the_only_one)
    secret = clipboard()
    p.text("What you copied", secret)
    p.check("The key is copied as <hidden: secret api_key>, api_key the name the query gives it, as shown above",
            secret == KEY_COPIED)
    p.check("The key's text is nowhere in what was copied", API_KEY not in secret)
    p.check("Nor anywhere in the run history", all(API_KEY.encode() not in f.read_bytes()
                                                   for f in jdbc_env.home.glob("history.db*")))

    # --- a query the database refused -------------------------------------------------------------------------------
    returns = query_flow(jdbc_env.root / "load_returns.yaml", PARAMS, {
        "load_returns": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": RETURNS_SQL}}},
        ["by_hand.start -> load_returns.after"])
    p.step("Open load_returns.yaml, press ▶ Run now and Run. load_returns asks for the table returns, which the "
           "database does not have.")
    returns_tab = open_flow(win, monkeypatch, returns)
    run_now(p, win)
    refused = wait_runs(qtbot, returns_tab, 1)
    click_bottom_tab(win, win.log)
    scroll_log_to(win, REFUSED)
    said = log_lines(win, REFUSED, "and return_day = ?")
    p.shot(win, f"① load_returns failed. ② The Log says why: {REFUSED}. It shows the SQL the database got, too: a ? "
                "where each value goes, the values sent apart from it.",
           highlight=["load_returns", said], badges=True, zoom=[said])
    p.check("The run failed, with load_returns, which the database refused",
            returns_tab.runs[0].status == "failed" and returns_tab.scene.nodes["load_returns"].status == "failed"
            and REFUSED in win.log.text.toPlainText())

    p.step(f"Click the Run history tab, right-click run {refused} and choose {COPY}.")
    click_bottom_tab(win, win.history_panel)

    def copy_the_refused_one(menu):
        p.shot(menu, f"① The run failed; ② {COPY} is there all the same.", over=win,
               highlight=[win.history_panel.table.item(run_row(win, refused), 3), menu_item(menu, COPY)], badges=True,
               zoom=[menu, *run_cells(win, refused)])
        choose(menu, COPY)

    p.while_open(lambda: right_click_run(win, refused), copy_the_refused_one)
    p.text("What you copied", clipboard())
    p.check("It copies what load_returns sent, though the database refused it, as shown above",
            clipboard() == RETURNS_COPIED)

    # --- no query ---------------------------------------------------------------------------------------------------
    tidy = query_flow(jdbc_env.root / "tidy_up.yaml", {}, {
        "tidy_up": {"type": "logic.python", "config": {"inputs": [], "code": "removed = 3"}}},
        ["by_hand.start -> tidy_up.after"])
    p.step("Open tidy_up.yaml, which has no query block, press ▶ Run now, then right-click its run in Run history.")
    tidy_tab = open_flow(win, monkeypatch, tidy)
    press_run_now(win)
    tidy_run = wait_runs(qtbot, tidy_tab, 1)
    click_bottom_tab(win, win.history_panel)
    before, item = clipboard(), []

    def look_at_greyed(menu):
        item.append(menu_item(menu, COPY))
        p.shot(menu, f"① {COPY} is greyed out, and says no query ran in this run.", over=win, highlight=[item[0]],
               badges=True, zoom=[menu, *run_cells(win, tidy_run)])

    p.while_open(lambda: right_click_run(win, tidy_run), look_at_greyed)
    p.check(f"The item is greyed out and says “{NO_QUERY}”", not item[0].isEnabled() and item[0].text() == NO_QUERY)
    p.check("Nothing was copied: the clipboard is as it was", clipboard() == before)
    p.note("A run recorded before this version kept no query either: its item is greyed out in the same way, and "
           "says the same.")
