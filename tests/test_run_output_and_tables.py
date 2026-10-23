"""What runs show and produce: other programs' lines in the Log, table paths on a block's line, table previews with
their first and last rows, chart axis labels, and the save dialogs of the blocks that write files."""

import html
import json
import re
from pathlib import Path

import pytest
import yaml
from PySide6.QtGui import QTextFormat
from PySide6.QtWidgets import QFileDialog, QLineEdit, QToolButton

from taskloom.config import Home
from taskloom.editor.canvas import STATUS_COLORS
from taskloom.editor.dialogs import RunDialog
from taskloom.engine import run_flow
from taskloom.flow import load_flow
from taskloom.history import History
from taskloom.registry import discover

RED = STATUS_COLORS["failed"]  # how the Log shows an error


def write_blocks(path: Path, blocks: dict, edges=()) -> Path:
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": list(edges)}, sort_keys=False), encoding="utf-8")
    return path


def python(code: str, outputs=()) -> dict:
    return {"type": "logic.python", "config": {"inputs": [], "outputs": list(outputs), "code": code}}


def run_in_editor(win, qtbot, flow: Path):
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=90_000)
    return tab


def log_lines(win) -> list[str]:
    return win.log.text.toPlainText().splitlines()


def log_entry(win, text: str) -> dict:
    """The Log's entry whose message has `text`."""
    found = [e for e in win.log.entries if text in e.get("message", "")]
    assert len(found) == 1, (text, win.log.entries)
    return found[0]


def line_colors(win, text: str) -> set[str]:
    """The colors the Log shows the line with `text` in."""
    lines, current = [], []
    block = win.log.text.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            fmt = fragment.charFormat()
            color = fmt.foreground().color().name() if fmt.hasProperty(QTextFormat.ForegroundBrush) else None
            for i, part in enumerate(fragment.text().split("\u2028")):  # a <br> inside one paragraph
                if i:
                    lines.append(current)
                    current = []
                current.append((part.replace("\xa0", " "), color))
            it += 1
        lines.append(current)
        current = []
        block = block.next()
    [line] = [line for line in lines if text in "".join(part for part, _ in line)]
    return {color for _, color in line if color}


# --- 1. what another program prints during a run is plain output in the Log -----------------------------------

def test_a_run_query_blocks_driver_messages_are_plain_output_in_the_log(jdbc_env, win, qtbot, monkeypatch):
    """The user's case: the Java database driver prints a normal message (here the JVM's note about its options)
    while a Run Query block runs; the Log shows it as output, not as an error."""
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Xmx256m")  # the JVM says "Picked up JAVA_TOOL_OPTIONS: -Xmx256m"
    flow = write_blocks(jdbc_env.root / "query.yaml", {"q": {"type": "db.run_query", "config": {
        "connection": "warehouse", "sql": "select x as id, 'row-' || x as label from system_range(1, 3)"}}})

    tab = run_in_editor(win, qtbot, flow)

    assert tab.last["status"] == "success"
    entry = log_entry(win, "Picked up JAVA_TOOL_OPTIONS")
    assert entry["level"] == "OUTPUT", entry
    [line] = [line for line in log_lines(win) if "Picked up JAVA_TOOL_OPTIONS" in line]
    assert "ERROR" not in line, line
    assert RED not in line_colors(win, "Picked up JAVA_TOOL_OPTIONS")


def test_lines_a_library_prints_are_plain_output_in_order_with_the_runs_other_lines(win, qtbot, tmp_path):
    code = ("import sys\n"
            "print('first step')\n"
            "sys.stdout.write('library: cache warmed\\n'); sys.stdout.flush()\n"
            "print('second step')\n"
            "sys.stderr.write('driver: connection pool started\\n'); sys.stderr.flush()\n")
    flow = write_blocks(tmp_path / "chatty.yaml", {"work": python(code)})

    tab = run_in_editor(win, qtbot, flow)

    assert tab.last["status"] == "success"
    for text in ("library: cache warmed", "driver: connection pool started"):
        assert log_entry(win, text)["level"] == "OUTPUT"
        assert RED not in line_colors(win, text)
    text = win.log.text.toPlainText()
    assert text.index("first step") < text.index("library: cache warmed") < text.index("second step")


def test_taskloom_s_own_errors_stay_errors_in_the_log(win, qtbot, tmp_path, monkeypatch):
    """Protects: the run process dying, and a run Taskloom refuses, are still shown as errors."""
    crash = write_blocks(tmp_path / "crash.yaml", {"boom": python("import os; os._exit(3)")})
    run_in_editor(win, qtbot, crash)
    assert log_entry(win, "stopped unexpectedly (exit code 3)")["level"] == "ERROR"
    assert RED in line_colors(win, "stopped unexpectedly (exit code 3)")

    refused = tmp_path / "refused.yaml"
    refused.write_text(yaml.safe_dump({"params": {"n": {"type": "int", "value": 1}},
                                       "blocks": {"work": python("pass")}}), encoding="utf-8")

    def type_a_word_for_a_number(dialog):
        dialog.edits["n"].setText("many")
        return True

    monkeypatch.setattr(RunDialog, "exec", type_a_word_for_a_number)
    tab = win.open_flow(refused)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and any("many" in e.get("message", "")
                                                                 for e in win.log.entries), timeout=60_000)
    entry = log_entry(win, "many")
    assert entry["level"] == "ERROR", entry
    assert RED in line_colors(win, "many")


# --- 2. a block's line names the files of the tables it produced -----------------------------------------------

TWO_TABLES = ("left = pl.DataFrame({'id': [1, 2, 3], 'label': ['a', 'b', 'c']})\n"
              "right = pl.DataFrame({'id': [1, 2]})\n"
              "count = 3\n")


def test_taskloom_run_prints_the_full_path_of_each_table_on_the_blocks_line(env):
    flow = write_blocks(env.root / "tables.yaml", {"make": python(TWO_TABLES, ["left", "right", "count"])})

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    run_id, _ = env.last_run()
    [(stored,)] = env.query("SELECT tables FROM blocks WHERE run_id=? AND block='make'", run_id)
    tables = json.loads(stored)
    assert set(tables) == {"left", "right"} and all(Path(p).is_file() for p in tables.values())
    [line] = [line for line in result.stderr.splitlines() if "[make] success" in line]
    assert "left: 3 rows × 2 columns" in line and tables["left"] in line, line
    assert tables["right"] in line, line


def test_the_logs_line_of_a_block_names_the_file_of_its_table(win, qtbot, tmp_path):
    flow = write_blocks(tmp_path / "table.yaml", {"make": python(
        "output = pl.DataFrame({'id': [1, 2, 3], 'label': ['a', 'b', 'c']})", ["output"])})

    tab = run_in_editor(win, qtbot, flow)

    history = History(win.home.history_db)
    path = history.blocks(tab.last["id"])["make"]["tables"]["output"]
    history.close()
    assert Path(path).is_file()
    assert [line for line in log_lines(win) if "[make]" in line and path in line], log_lines(win)


def test_a_past_runs_log_names_the_file_of_a_blocks_table_after_what_the_block_printed(win, qtbot, tmp_path):
    """A run reopened from Run history (or started by the scheduler): its Log is rebuilt from the stored run."""
    flow = write_blocks(tmp_path / "table.yaml", {"make": python(
        "print('making the table')\noutput = pl.DataFrame({'id': [1, 2, 3], 'label': ['a', 'b', 'c']})", ["output"])})
    tab = run_in_editor(win, qtbot, flow)
    run_id = tab.last["id"]
    history = History(win.home.history_db)
    path = history.blocks(run_id)["make"]["tables"]["output"]
    history.close()

    win.show_history_run(run_id, tab)

    lines = log_lines(win)
    [table_line] = [i for i, line in enumerate(lines) if "[make]" in line and path in line]
    [printed] = [i for i, line in enumerate(lines) if "making the table" in line]
    assert printed < table_line, lines


# --- 3. hovering a table shows its first and last rows ---------------------------------------------------------

def frame(n: int, wide: bool = False) -> str:
    """Python code making a table of n rows whose labels are row-01, row-02, ...; wide: as many columns as a
    typical query result."""
    columns = {"id": f"list(range(1, {n} + 1))", "label": f"[f'row-{{i:02d}}' for i in range(1, {n} + 1)]"}
    if wide:
        columns.update({"amount": f"[i * 1.25 for i in range({n})]", "quantity": f"[i % 7 for i in range({n})]",
                        "region": f"['north'] * {n}", "channel": f"['online'] * {n}", "currency": f"['EUR'] * {n}",
                        "status": f"['shipped'] * {n}", "day": f"[dt.date(2026, 10, 1)] * {n}",
                        "store_code": f"['ST-0001'] * {n}", "category": f"['hardware'] * {n}",
                        "customer_name": f"['Example Customer'] * {n}"})
    return ("import datetime as dt\noutput = pl.DataFrame({" + ", ".join(f"{k!r}: {v}" for k, v in columns.items())
            + "})")


def shown_text(tooltip: str) -> str:
    """The value a tooltip shows (inside its <pre>)."""
    [value] = re.findall(r"<pre>(.*?)</pre>", tooltip, re.S)
    return html.unescape(value)


def assert_first_and_last_rows(text: str, n: int):
    shown = [i for i in range(1, n + 1) if f"row-{i:02d}" in text]
    assert shown == [1, 2, 3, 4, 5, n - 4, n - 3, n - 2, n - 1, n], text
    ellipsis_rows = [line for line in text.splitlines() if "…" in line and "row-" not in line]
    assert ellipsis_rows and text.index("row-05") < text.index(ellipsis_rows[0]) < text.index(f"row-{n - 4:02d}"), text
    assert f"{n} rows" in text, text


def test_hovering_a_long_tables_output_shows_its_first_rows_an_ellipsis_and_its_last_rows(win, qtbot, tmp_path):
    flow = write_blocks(tmp_path / "long.yaml", {"query": python(frame(40, wide=True), ["output"]),
                                                 "report": {"type": "logic.python", "config": {
                                                     "inputs": ["input"], "outputs": [], "code": "pass"}}},
                        ["query.output -> report.input"])

    tab = run_in_editor(win, qtbot, flow)

    assert tab.last["status"] == "success"
    query, report = tab.scene.nodes["query"], tab.scene.nodes["report"]
    assert_first_and_last_rows(shown_text(query.ports[("output", True)].toolTip()), 40)
    assert_first_and_last_rows(shown_text(report.ports[("input", False)].toolTip()), 40)
    assert_first_and_last_rows(shown_text(tab.scene.edge_items[0].toolTip()), 40)
    assert_first_and_last_rows(shown_text(query.footer.toolTip()), 40)  # hovering the block's result


def test_a_table_of_ten_rows_or_fewer_shows_every_row_once_and_eleven_rows_hide_the_middle_one(win, qtbot, tmp_path):
    flow = write_blocks(tmp_path / "sizes.yaml", {f"rows_{n}": python(frame(n), ["output"]) for n in (7, 10, 11)})

    tab = run_in_editor(win, qtbot, flow)

    assert tab.last["status"] == "success"

    def shown(n):
        return shown_text(tab.scene.nodes[f"rows_{n}"].ports[("output", True)].toolTip())

    for n in (7, 10):  # protects: every row, each once, nothing left out
        assert [shown(n).count(f"row-{i:02d}") for i in range(1, n + 1)] == [1] * n, shown(n)
        assert "…" not in shown(n), shown(n)
    assert_first_and_last_rows(shown(11), 11)


# --- 4. chart axis labels ---------------------------------------------------------------------------------------

SALES = python("output = pl.DataFrame({'store_id': ['A1', 'B2', 'C3'], 'total': [10.5, 20.0, 15.25], "
               "'stores': [3, 4, 2]})", ["output"])


def chart_axis_labels(env, monkeypatch, chart: dict) -> list[tuple[str, str]]:
    """Run a flow drawing `chart` from a small sales table; the (x, y) axis labels of the chart it drew."""
    import matplotlib.figure

    drawn, real_savefig = [], matplotlib.figure.Figure.savefig

    def savefig(figure, *args, **kwargs):
        drawn.append([(ax.get_xlabel(), ax.get_ylabel()) for ax in figure.axes])
        return real_savefig(figure, *args, **kwargs)

    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", savefig)
    flow = write_blocks(env.root / "chart.yaml", {"sales": SALES, "chart": {"type": "reports.chart", "config": chart}},
                        ["sales.output -> chart.table"])
    home = Home(env.home)
    result = run_flow(load_flow(flow), discover(home.blocks_dir), home)
    assert result.status == "success"
    assert Path(result.outputs[("chart", "image")]).is_file()
    [axes] = drawn
    return axes


@pytest.mark.parametrize("chart, labels", [
    ({"kind": "bar", "x": "store_id", "y": ["total"], "x_label": "Store ID",
      "y_label": "Total Sales"}, [("Store ID", "Total Sales")]),
    # horizontal bars: the categories (x) go up the side, the values (y) along the bottom
    ({"kind": "barh", "x": "store_id", "y": ["total"], "x_label": "Store ID",
      "y_label": "Total Sales"}, [("Total Sales", "Store ID")]),
    ({"kind": "line", "x": "store_id", "y": ["total", "stores"], "x_label": "Store ID",
      "y_label": "Amount"}, [("Store ID", "Amount")]),
    ({"kind": "bar", "x": "store_id", "y": ["total"], "x_label": "", "y_label": ""},
     [("store_id", "total")]),  # empty: the column names, as before
    ({"kind": "pie", "x": "store_id", "y": ["total"], "x_label": "Store ID",
      "y_label": "Total Sales"}, [("", "")]),  # a pie chart has no axes to label
], ids=["bar", "barh", "two-series", "empty", "pie"])
def test_a_charts_axes_are_labelled_with_its_x_and_y_labels(env, monkeypatch, chart, labels):
    assert chart_axis_labels(env, monkeypatch, chart) == labels


def test_chart_labels_are_set_in_properties_next_to_x_and_y(win):
    tab = win.new_flow()
    chart = tab.doc.add_block("reports.chart", 0, 0, {"x": "store_id", "y": ["total"], "title": "Sales",
                                                      "x_label": "Store ID", "y_label": "Total"})
    tab.scene.nodes[chart].setSelected(True)
    try:
        edits = win.properties.findChildren(QLineEdit)
        order = [e.text() for e in edits]
        assert "Store ID" in order and "Total" in order, order
        title = order.index("Sales")
        assert order.index("store_id") < order.index("Store ID") < title, order
        assert order.index("total") < order.index("Total") < title, order

        y_label = edits[order.index("Total")]
        y_label.setText("Total Sales")
        y_label.editingFinished.emit()

        assert tab.doc.blocks[chart]["config"]["y_label"] == "Total Sales"
        assert tab.doc.blocks[chart]["config"]["x_label"] == "Store ID"
    finally:
        tab.doc.dirty = False  # closing the editor asks nothing


# --- 5. save dialogs of the blocks that write files ------------------------------------------------------------

def browse(win, monkeypatch, block_type: str, choose) -> tuple[list, str]:
    """Click the browse button of the path field of a new `block_type` block; `choose(filters)` answers the save
    dialog with (path, selected filter). The filters the dialog offered, and the path the block has then."""
    tab = win.new_flow()
    block = tab.doc.add_block(block_type, 0, 0, {"path": "reports/old.xlsx"})
    tab.scene.nodes[block].setSelected(True)
    offered = []

    def get_save_file_name(*args, **kwargs):
        filters = kwargs.get("filter", args[3] if len(args) > 3 else "")
        offered.append(filters)
        return choose(filters)

    def exec_(dialog):
        offered.append(("a QFileDialog was opened instead of getSaveFileName", dialog.nameFilters()))
        return 0

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(get_save_file_name))
    monkeypatch.setattr(QFileDialog, "exec", exec_)
    try:
        [edit] = [e for e in win.properties.findChildren(QLineEdit) if e.text() == "reports/old.xlsx"]
        [button] = edit.parentWidget().findChildren(QToolButton)
        button.click()
        return offered, tab.doc.blocks[block]["config"].get("path")
    finally:
        tab.doc.dirty = False  # closing the editor asks nothing


@pytest.mark.parametrize("folder", ["reports", "first.last"])  # (a dot in a folder's name is not an extension)
def test_to_excels_save_dialog_offers_only_xlsx_and_adds_it_to_a_name_typed_without_it(win, monkeypatch, tmp_path,
                                                                                         folder):
    typed = tmp_path / folder / "sales"

    offered, path = browse(win, monkeypatch, "reports.excel", lambda filters: (str(typed), filters))

    assert offered == ["Excel workbook (*.xlsx)"]
    assert Path(path) == tmp_path / folder / "sales.xlsx"


@pytest.mark.parametrize("pattern, suffix", [("*.csv", ".csv"), ("*.xlsx", ".xlsx"), ("*.parquet", ".parquet")])
def test_write_files_save_dialog_offers_its_formats_and_adds_the_chosen_ones_extension(win, monkeypatch, tmp_path,
                                                                                       pattern, suffix):
    def choose(filters):
        return str(tmp_path / "sales"), next((f for f in filters.split(";;") if pattern in f), "")

    offered, path = browse(win, monkeypatch, "data.write_file", choose)

    [filters] = offered
    for each in ("*.csv", "*.xlsx", "*.parquet"):  # CSV, Excel and Parquet, each a choice of its own
        assert len([f for f in filters.split(";;") if each in f]) == 1, filters
    assert Path(path) == tmp_path / f"sales{suffix}"


@pytest.mark.parametrize("block_type, name, pattern", [("reports.excel", "sales.xlsx", "*.xlsx"),
                                                       ("data.write_file", "sales.csv", "*.csv")])
def test_a_name_typed_with_its_extension_is_kept_as_typed(win, monkeypatch, tmp_path, block_type, name, pattern):
    """Protects: the extension is not added a second time."""
    def choose(filters):
        return str(tmp_path / name), next((f for f in filters.split(";;") if pattern in f), "")

    _, path = browse(win, monkeypatch, block_type, choose)

    assert Path(path) == tmp_path / name


def test_cancelling_the_save_dialog_keeps_the_path(win, monkeypatch):
    """Protects: no extension is added to nothing; the path stays as it was."""
    _, path = browse(win, monkeypatch, "reports.excel", lambda filters: ("", ""))

    assert path == "reports/old.xlsx"
