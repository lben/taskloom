"""Copy executed query: right-click a run in Run history and choose "Copy executed query" to copy the SQL a Run Query or
DuckDB SQL block sent in that run, with each value bound to it written in as an SQL literal, ready to paste into a SQL
tool and run (a secret only as <hidden: secret NAME>). Several query blocks: a submenu, in the order they ran; none,
or a run recorded before runs kept their queries: the item is disabled and says why. Through the real `taskloom run`
processes (as the editor, the scheduler service or a terminal start them), history.db and the editor (offscreen);
Run Query against a real H2 database server (needs Java)."""

import contextlib
import datetime as dt
import os
import sqlite3
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
import yaml  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QToolTip  # noqa: E402

from taskloom.flow import real_path  # noqa: E402

COPY = "Copy executed query"
NO_QUERY = "no query ran in this run"


# --- helpers ---------------------------------------------------------------------------------------------------

def write_flow(path: Path, flow: dict) -> Path:
    path.write_text(yaml.safe_dump(flow, sort_keys=False), encoding="utf-8")
    return path


def run_flow(env, flow: Path, succeeds: bool = True) -> int:
    """`taskloom run <flow>`, as the scheduler service or a terminal runs it; its run's number."""
    run = env.taskloom("run", flow)
    assert run.returncode == (0 if succeeds else 1), run.stdout + run.stderr
    return env.last_run()[0]


def plain(text: str) -> str:
    return text.replace("&", "")  # (a menu item's shortcut letter)


def row_of(win, run_id: int) -> int | None:
    """The row of run `run_id` in Run history (None: not listed)."""
    table = win.history_panel.table
    labels = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    column = labels.index("Run")
    rows = [r for r in range(table.rowCount()) if table.item(r, column) and table.item(r, column).text() == str(run_id)]
    return rows[0] if rows else None


def right_click_run(win, qtbot, run_id: int, choose: tuple[str, ...] = ()) -> list[list[dict]]:
    """Right-click run `run_id`'s row in Run history, as you would, then in each menu that opens click the item that
    starts with `choose[level]` (an item with a submenu opens it); the menus are closed when nothing more is to be
    chosen. What each menu that opened listed, item by item: {"text", "enabled", "submenu" (it has one), "tip" (its
    tooltip, when the menu shows tooltips)}."""
    win.bottom_tabs.setCurrentWidget(win.history_panel)
    qtbot.waitUntil(lambda: row_of(win, run_id) is not None, timeout=10_000)
    table = win.history_panel.table
    labels = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    item = table.item(row_of(win, run_id), labels.index("Run"))
    table.scrollToItem(item)
    QApplication.processEvents()
    viewport = table.viewport()
    at = table.visualItemRect(item).center()
    assert viewport.rect().contains(at), (at, viewport.rect())
    where = viewport.mapToGlobal(at)
    menus, handled, done, deadline = [], [], [], [time.monotonic() + 5]

    def close_menus():  # (never leave a menu waiting for a click)
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMenu) and widget.isVisible():
                widget.close()

    def look():  # (a menu can wait for its click inside the right-click)
        if done:
            return
        popup = QApplication.activePopupWidget()
        if not isinstance(popup, QMenu) or popup in handled:
            if time.monotonic() < deadline[0]:
                QTimer.singleShot(20, look)
                return
        else:
            handled.append(popup)
            actions = [a for a in popup.actions() if a.text()]
            menus.append([{"text": plain(a.text()), "enabled": a.isEnabled(), "submenu": a.menu() is not None,
                           "tip": plain(a.toolTip()) if popup.toolTipsVisible() else ""} for a in actions])
            level = len(menus) - 1
            wanted = choose[level] if level < len(choose) else None
            chosen = next((a for a in actions if wanted and plain(a.text()).startswith(wanted) and a.isEnabled()), None)
            if chosen is not None:
                QTest.mouseClick(popup, Qt.LeftButton, Qt.NoModifier, popup.actionGeometry(chosen).center())
                if chosen.menu() is not None:  # its submenu opens
                    deadline[0] = time.monotonic() + 5
                    QTimer.singleShot(20, look)
                    return
        done.append(True)
        close_menus()

    # A right-click: the button goes down on the row (which selects it), then the window asks for its menu.
    for kind, buttons in ((QEvent.MouseButtonPress, Qt.RightButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
        QApplication.sendEvent(viewport, QMouseEvent(kind, QPointF(at), QPointF(where), Qt.RightButton, buttons,
                                                     Qt.NoModifier))
    QTimer.singleShot(0, look)
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, where))
    qtbot.waitUntil(lambda: bool(done), timeout=20_000)
    QApplication.processEvents()
    return menus


def copy_item(menus: list[list[dict]]) -> dict:
    """The Copy executed query item of the run's menu."""
    assert menus, "right-clicking the run opened no menu"
    found = [item for item in menus[0] if item["text"].startswith(COPY)]
    assert len(found) == 1, menus
    return found[0]


def copy_query(win, qtbot, run_id: int, *choose: str) -> str:
    """Right-click the run, click Copy executed query (and in its submenu `choose`): what is on the clipboard then."""
    QApplication.clipboard().setText("")
    menus = right_click_run(win, qtbot, run_id, (COPY, *choose))
    item = copy_item(menus)
    assert item["enabled"], menus
    if choose:
        assert item["submenu"] and len(menus) == 2, menus
    else:
        assert not item["submenu"] and len(menus) == 1, menus  # one query: the item copies it
    return QApplication.clipboard().text()


def told(win) -> str:
    """What the editor says now: its status bar message and the tooltip on screen (if any)."""
    return win.statusBar().currentMessage() + "\n" + (QToolTip.text() if QToolTip.isVisible() else "")


def assert_says_why_not(item: dict, menus):
    assert not item["enabled"], menus
    assert NO_QUERY in item["text"] or NO_QUERY in item["tip"], menus  # said where you see it


# --- one query block: the item copies its query ----------------------------------------------------------------

SUM_SQL = (
    "select :note as note, :region as region, :top as top, :ratio as ratio, :flag as flag, :day as day_v,"
    " :at as at_v,\n"
    "       'sales_{day:%Y%m%d}' as source, 'why?' as question, ':region' as literal, 1::varchar as cast_text"
    "  -- any :top? rows\n"
    "from (values ('north'), ('south'), ('east')) t(r)\n"
    "where r in (:regions) /* is :flag ? here */"
)
SUM_COPIED = (  # as sent ({day:...} filled in), each ? its value; ? and :name in quotes or comments stay
    "select 'really? yes' as note, 'O''Brien' as region, 5 as top, 0.25 as ratio, FALSE as flag,"
    " DATE '2026-01-16' as day_v, TIMESTAMP '2026-01-16 10:20:30' as at_v,\n"
    "       'sales_20260116' as source, 'why?' as question, ':region' as literal, 1::varchar as cast_text"
    "  -- any :top? rows\n"
    "from (values ('north'), ('south'), ('east')) t(r)\n"
    "where r in ('north', 'south') /* is :flag ? here */"
)


def test_copy_executed_query_copies_the_sql_a_duckdb_block_sent_with_each_value_written_in(env, win, qtbot):
    flow = write_flow(env.root / "sales.yaml", {
        "params": {"note": {"type": "text", "value": "really? yes"}, "region": {"type": "text", "value": "O'Brien"},
                   "top": {"type": "int", "value": 5}, "ratio": {"type": "float", "value": 0.25},
                   "flag": {"type": "bool", "value": False}, "day": {"type": "date", "value": "2026-01-16"},
                   "at": {"type": "datetime", "value": "2026-01-16 10:20:30"},
                   "regions": {"type": "list", "value": "north, south"}},
        "blocks": {"manual": {"type": "trigger.manual"},
                   "sum_sales": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": SUM_SQL}}},
        "edges": ["manual.start -> sum_sales.after"]})
    run_id = run_flow(env, flow)
    win.open_flow(flow)

    copied = copy_query(win, qtbot, run_id)

    assert copied == SUM_COPIED
    said = told(win)
    assert "sum_sales" in said and f"#{run_id}" in said, said


# --- several query blocks: a submenu, in the order they ran ----------------------------------------------------

LOAD_SQL = (
    "select region, amount\n"
    "from (values ('O''Brien', 10.50, date '2026-01-16', timestamp '2026-01-16 10:20:30.123456', true, 1),\n"
    "             ('south', 3.25, date '2026-01-16', timestamp '2026-01-16 10:20:30', false, 2))"
    " t(region, amount, day_v, at_v, flag_v, id)\n"
    "where region = :region and amount >= :min_amount and day_v = :day and at_v = :since\n"
    "  and flag_v = :flag and id in (:ids) and :nothing is null  -- 'north'? not :region"
)
LOAD_COPIED = (
    "select region, amount\n"
    "from (values ('O''Brien', 10.50, date '2026-01-16', timestamp '2026-01-16 10:20:30.123456', true, 1),\n"
    "             ('south', 3.25, date '2026-01-16', timestamp '2026-01-16 10:20:30', false, 2))"
    " t(region, amount, day_v, at_v, flag_v, id)\n"
    "where region = 'O''Brien' and amount >= 10.50 and day_v = DATE '2026-01-16'"
    " and at_v = TIMESTAMP '2026-01-16 10:20:30.123456'\n"
    "  and flag_v = TRUE and id in (3, 1) and NULL is null  -- 'north'? not :region"
)
CHECK_SQL = "select count(*) as n from sales where amount > :ratio limit :top"
CHECK_COPIED = "select count(*) as n from sales where amount > 0.25 limit 5"


def test_several_query_blocks_are_listed_in_the_order_they_ran_and_each_copies_its_own_query(jdbc_env, win, qtbot):
    flow = write_flow(jdbc_env.root / "nightly.yaml", {
        "params": {"region": {"type": "text", "value": "O'Brien"}, "day": {"type": "date", "value": "2026-01-16"},
                   "flag": {"type": "bool", "value": True}, "ratio": {"type": "float", "value": 0.25},
                   "top": {"type": "int", "value": 5}},
        "blocks": {  # (listed in neither the order they run nor by name)
            "check_totals": {"type": "data.duckdb_sql", "config": {"inputs": ["sales"], "sql": CHECK_SQL}},
            "values": {"type": "logic.python", "config": {"inputs": [], "code": (
                "import datetime, decimal\n"
                "output = {'min_amount': decimal.Decimal('10.50'), 'nothing': None, 'ids': [3, 1],\n"
                "          'since': datetime.datetime(2026, 1, 16, 10, 20, 30, 123456)}")}},
            "load_sales": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": LOAD_SQL}},
            "manual": {"type": "trigger.manual"}},
        "edges": ["manual.start -> values.after", "values.output -> load_sales.params",
                  "load_sales.result -> check_totals.sales"]})
    run_id = run_flow(jdbc_env, flow)
    win.open_flow(flow)
    QApplication.clipboard().setText("")

    menus = right_click_run(win, qtbot, run_id, (COPY,))

    assert copy_item(menus)["submenu"], menus
    assert [item["text"] for item in menus[1]] == ["load_sales (Run Query)", "check_totals (DuckDB SQL)"], menus
    assert QApplication.clipboard().text() == ""  # opening the submenu copies nothing
    assert copy_query(win, qtbot, run_id, "load_sales (Run Query)") == LOAD_COPIED
    said = told(win)
    assert "load_sales" in said and f"#{run_id}" in said, said
    assert copy_query(win, qtbot, run_id, "check_totals (DuckDB SQL)") == CHECK_COPIED
    said = told(win)
    assert "check_totals" in said and f"#{run_id}" in said, said


# --- a query the database refused ------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["db.run_query", "data.duckdb_sql"])
def test_a_query_the_database_refused_still_copies_what_it_sent(env, win, qtbot, request, kind):
    config = {"connection": "warehouse"} if kind == "db.run_query" else {"inputs": []}
    if kind == "db.run_query":
        request.getfixturevalue("jdbc_env")  # (the H2 database as the connection `warehouse`)
    flow = write_flow(env.root / "load.yaml", {
        "params": {"region": {"type": "text", "value": "north"}, "day": {"type": "date", "value": "2026-01-16"}},
        "blocks": {"manual": {"type": "trigger.manual"},
                   "load_sales": {"type": kind, "config": {
                       **config, "sql": "select amount from no_such_table where region = :region and day_v = :day"}}},
        "edges": ["manual.start -> load_sales.after"]})
    run_id = run_flow(env, flow, succeeds=False)
    assert env.block_status(run_id)["load_sales"] == "failed"
    win.open_flow(flow)

    copied = copy_query(win, qtbot, run_id)

    assert copied == "select amount from no_such_table where region = 'north' and day_v = DATE '2026-01-16'"


# --- a secret --------------------------------------------------------------------------------------------------

TOKEN = "S3cr3t-T0ken-value"


def test_a_secret_is_copied_as_hidden_with_its_parameters_name_and_its_text_is_never_recorded(jdbc_env, win, qtbot):
    with (jdbc_env.home / "secrets.yaml").open("a", encoding="utf-8") as f:
        f.write(yaml.safe_dump({"report_token": TOKEN}))
    flow = write_flow(jdbc_env.root / "report.yaml", {
        "params": {"region": {"type": "text", "value": "north"}},
        "blocks": {
            "manual": {"type": "trigger.manual"},
            "ask_token": {"type": "logic.ask_user", "config": {
                "prompt": "Report token", "kind": "password", "secret": "report_token"}},
            "pack": {"type": "logic.python", "config": {"inputs": ["token"], "code": "output = {'token': token}"}},
            "load_sales": {"type": "db.run_query", "config": {
                "connection": "warehouse", "sql": "select :region as region, :token as token"}}},
        "edges": ["manual.start -> ask_token.after", "ask_token.answer -> pack.token",
                  "pack.output -> load_sales.params"]})
    run_id = run_flow(jdbc_env, flow)
    win.open_flow(flow)

    copied = copy_query(win, qtbot, run_id)

    assert copied == "select 'north' as region, <hidden: secret token> as token"
    for path in jdbc_env.home.glob("history.db*"):  # (with its write-ahead log)
        assert TOKEN.encode() not in path.read_bytes(), path


# --- no query to copy ------------------------------------------------------------------------------------------

def test_a_run_in_which_no_query_ran_shows_the_item_disabled_saying_why(env, win, qtbot):
    flow = write_flow(env.root / "report.yaml", {
        "blocks": {"manual": {"type": "trigger.manual"},
                   "report": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}}},
        "edges": ["manual.start -> report.after"]})
    run_id = run_flow(env, flow)
    win.open_flow(flow)
    QApplication.clipboard().setText("as it was")

    menus = right_click_run(win, qtbot, run_id, (COPY,))

    assert_says_why_not(copy_item(menus), menus)
    assert QApplication.clipboard().text() == "as it was"


# A history file as Taskloom wrote it before runs kept the queries they sent.
HISTORY_BEFORE = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flow TEXT NOT NULL, flow_path TEXT NOT NULL, params TEXT NOT NULL,
    status TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
    pid INTEGER, started_by TEXT NOT NULL DEFAULT '', counts_for TEXT NOT NULL DEFAULT ''
);
CREATE TABLE blocks (
    run_id INTEGER NOT NULL, block TEXT NOT NULL, status TEXT NOT NULL,
    started TEXT, finished TEXT, attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT, summary TEXT, tables TEXT, previews TEXT, PRIMARY KEY (run_id, block)
);
CREATE TABLE logs (
    run_id INTEGER NOT NULL, block TEXT, ts TEXT NOT NULL,
    level TEXT NOT NULL, message TEXT NOT NULL, show TEXT
);
CREATE INDEX logs_run ON logs (run_id, block);
CREATE TABLE keeps (
    flow_path TEXT NOT NULL, block TEXT NOT NULL, keep TEXT, keep_failed INTEGER NOT NULL,
    PRIMARY KEY (flow_path, block)
);
"""


def test_a_run_recorded_before_runs_kept_their_queries_says_why_and_runs_after_it_copy_theirs(env, win, qtbot):
    flow = write_flow(env.root / "sales.yaml", {
        "params": {"top": {"type": "int", "value": 5}},
        "blocks": {"manual": {"type": "trigger.manual"},
                   "sum_sales": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": "select :top as top"}}},
        "edges": ["manual.start -> sum_sales.after"]})
    yesterday = dt.datetime.now() - dt.timedelta(days=1)
    started, finished = (t.isoformat(timespec="milliseconds") for t in (yesterday, yesterday + dt.timedelta(seconds=1)))
    with contextlib.closing(sqlite3.connect(env.home / "history.db")) as db:
        db.executescript(HISTORY_BEFORE)
        db.execute("INSERT INTO runs (flow, flow_path, params, status, started, finished, started_by, counts_for) "
                   "VALUES ('sales', ?, '{\"top\": 5}', 'success', ?, ?, 'manual', 'manual')",
                   (str(real_path(flow)), started, finished))
        for block in ("manual", "sum_sales"):
            db.execute("INSERT INTO blocks (run_id, block, status, started, finished, attempts, tables, previews) "
                       "VALUES (1, ?, 'success', ?, ?, 1, '{}', '{}')", (block, started, finished))
        db.commit()
    newer = run_flow(env, flow)  # with this history file
    assert newer == 2
    win.open_flow(flow)

    older = right_click_run(win, qtbot, 1, (COPY,))
    copied = copy_query(win, qtbot, newer)

    assert_says_why_not(copy_item(older), older)
    assert copied == "select 5 as top"
