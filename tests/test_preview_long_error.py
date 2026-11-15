"""Data preview on a block that failed, or whose table cannot be read: the label stays short and the whole error is in
the scrolling box below it, so the window keeps its size and the blocks stay where they were
(docs/promises/editor-fixes.yaml, rows DP-1 to DP-3).

Driven through the real editor window (the `win` fixture): real flows, run for real."""

import shutil

import pytest
import yaml
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from test_editor import wait_done

# One line of 3,000 characters with no space in it (a path, an address), one of words (a statement), and 80 lines.
UNBROKEN = ("orders/2026/" * 250)[:3000]
WORDS = ("no customer for this order; " * 108)[:3000]
LINES = "\n".join(f"line {n}: the order has no customer" for n in range(1, 81))
MARKUP = "expected <b>amount</b> & <i>currency</i> in <orders>, found &amp; instead"
LONG_ERRORS = {"3,000 characters on one line, no spaces": UNBROKEN, "3,000 characters on one line, words": WORDS,
               "80 lines": LINES}


def settle(rounds: int = 5):
    for _ in range(rounds):
        QApplication.processEvents()


def write_flow(path, blocks: dict, edges=()):
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": list(edges)}, sort_keys=False), encoding="utf-8")
    return path


def failing_flow(path, error: str):
    """Manual Start `manual`, then the Python Code block `load_orders`, which fails with `error`."""
    return write_flow(path, {"manual": {"type": "trigger.manual"},
                             "load_orders": {"type": "logic.python", "config": {
                                 "inputs": [], "outputs": [], "code": f"raise ValueError({error!r})"}}},
                      ["manual.start -> load_orders.after"])


def report_flow(path):
    """Another flow, not run: Manual Start `manual`, then the Python Code block `report`."""
    return write_flow(path, {"manual": {"type": "trigger.manual"},
                             "report": {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}}},
                      ["manual.start -> report.after"])


def place(win, tab, block: str) -> QRect:
    """Where the block is drawn, in the window's coordinates; it must be wholly on the tab's canvas."""
    view = tab.view
    drawn = view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect()).boundingRect()
    assert view.viewport().rect().contains(drawn), (
        f"{block} is not wholly on the canvas: it is drawn at {drawn}, the canvas is {view.viewport().rect()}")
    return QRect(view.viewport().mapTo(win, drawn.topLeft()), drawn.size())


def show(win, tab):
    win.tabs.setCurrentWidget(tab)
    settle()


def select(win, tab, block: str):
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)
    settle()


def open_data_preview(win):
    win.bottom_tabs.setCurrentWidget(win.preview)
    settle()


def failed_with(win, qtbot, tmp_path, error: str):
    """The editor with two flows open, on its Log tab: `orders`, whose load_orders has just failed with `error`, and
    `report`. Gives (orders' tab, report's tab, the window's size, where load_orders is, where report is), before
    load_orders is selected."""
    other = win.open_flow(report_flow(tmp_path / "report.yaml"))
    tab = win.open_flow(failing_flow(tmp_path / "orders.yaml", error))
    win.run_flow(tab)
    wait_done(qtbot, tab)
    assert tab.scene.nodes["load_orders"].status == "failed"
    win.bottom_tabs.setCurrentWidget(win.log)
    show(win, other)
    there = place(win, other, "report")
    show(win, tab)
    return tab, other, win.size(), place(win, tab, "load_orders"), there


# --- DP-1: the window and the blocks stay as they were ----------------------------------------------------------------

@pytest.mark.invariant("DP-1")
@pytest.mark.parametrize("error", LONG_ERRORS.values(), ids=LONG_ERRORS.keys())
def test_opening_data_preview_on_a_failed_block_keeps_the_windows_size_and_every_tabs_blocks_in_place(
        win, qtbot, tmp_path, error):
    tab, other, size, here, there = failed_with(win, qtbot, tmp_path, error)

    select(win, tab, "load_orders")
    open_data_preview(win)

    assert win.preview.isVisible()
    assert (win.width(), win.height()) == (size.width(), size.height()), "the window changed its size"
    assert place(win, tab, "load_orders") == here, "load_orders moved"
    show(win, other)  # the other flow's tab
    assert (win.width(), win.height()) == (size.width(), size.height()), "the window changed its size"
    assert place(win, other, "report") == there, "report, in the other flow's tab, moved"


# --- DP-2: a short label, the whole error in the box ------------------------------------------------------------------

@pytest.mark.invariant("DP-2")
@pytest.mark.parametrize("error", [*LONG_ERRORS.values(), MARKUP, "it broke"],
                         ids=[*LONG_ERRORS.keys(), "markup", "a short error"])
def test_the_label_names_the_failed_block_and_the_box_below_holds_every_character_of_the_error(
        win, qtbot, tmp_path, error):
    tab = failed_with(win, qtbot, tmp_path, error)[0]

    select(win, tab, "load_orders")
    open_data_preview(win)

    assert win.preview.label.text() == "load_orders: failed, no output"
    box = win.preview.text
    assert box.isVisible(), "the box under the label is not shown"
    assert error in box.toPlainText(), f"the box does not hold the error as written: {box.toPlainText()[:300]!r}"


@pytest.mark.invariant("DP-2")
def test_the_whole_error_can_be_selected_and_copied_from_the_box(win, qtbot, tmp_path):
    tab = failed_with(win, qtbot, tmp_path, LINES + "\n" + MARKUP)[0]
    select(win, tab, "load_orders")
    open_data_preview(win)
    QApplication.clipboard().clear()

    win.preview.text.selectAll()  # Ctrl+A, Ctrl+C in the box
    win.preview.text.copy()

    assert LINES + "\n" + MARKUP in QApplication.clipboard().text()


@pytest.mark.invariant("DP-2")
def test_the_box_holds_the_error_only_while_the_failed_block_is_selected(win, qtbot, tmp_path):
    """Another block selected after the failed one shows its own: a table as a grid, a value as its text, and nothing
    of the error where the block ran and has no output."""
    python = lambda code, outputs: {"type": "logic.python",  # noqa: E731
                                    "config": {"inputs": [], "outputs": outputs, "code": code}}
    flow = write_flow(tmp_path / "orders.yaml", {
        "manual": {"type": "trigger.manual"},
        "clean": python("rows = pl.DataFrame({'n': [1, 2, 3]})\nnote = 'three orders'", ["rows", "note"]),
        "send": python("pass", []),
        "load_orders": python(f"raise ValueError({UNBROKEN!r})", [])},
        ["manual.start -> clean.after", "manual.start -> send.after", "manual.start -> load_orders.after"])
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_done(qtbot, tab)
    assert {b: tab.scene.nodes[b].status for b in ("clean", "send", "load_orders")} == {
        "clean": "success", "send": "success", "load_orders": "failed"}
    open_data_preview(win)
    preview = win.preview

    select(win, tab, "load_orders")
    assert preview.label.text() == "load_orders: failed, no output"
    assert preview.text.isVisible() and UNBROKEN in preview.text.toPlainText()

    select(win, tab, "clean")
    assert preview.view.isVisible() and not preview.text.isVisible()
    assert preview.view.model().rowCount() == 3 and preview.label.text().startswith("clean.rows: 3 rows")
    preview.ports.setCurrentText("note")
    assert preview.text.isVisible() and "three orders" in preview.text.toPlainText()
    assert UNBROKEN not in preview.text.toPlainText()

    select(win, tab, "load_orders")
    select(win, tab, "send")
    assert preview.label.text() == "send: success, no output"
    assert not preview.text.isVisible() or UNBROKEN not in preview.text.toPlainText()


# --- DP-3: a preview that cannot be read ------------------------------------------------------------------------------

@pytest.mark.invariant("DP-3")
def test_a_table_whose_file_is_gone_reads_cannot_preview_with_the_reason_in_the_box_and_the_window_keeps_its_size(
        win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "orders.yaml", {
        "manual": {"type": "trigger.manual"},
        "load_orders": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": "select 1 as amount"}}},
        ["manual.start -> load_orders.after"])
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_done(qtbot, tab)
    win.bottom_tabs.setCurrentWidget(win.log)
    settle()
    size, here = win.size(), place(win, tab, "load_orders")
    run_folder = win.home.runs_dir / str(tab.runs[-1].run_id)
    tables = list(run_folder.rglob("*.parquet"))
    assert len(tables) == 1, f"the run kept {len(tables)} tables in {run_folder}"
    shutil.rmtree(run_folder)  # the run's files were cleaned up

    select(win, tab, "load_orders")
    open_data_preview(win)

    assert win.preview.label.text() == "Cannot preview"
    box = win.preview.text
    assert box.isVisible(), "the box under the label is not shown"
    reason = box.toPlainText()
    assert tables[0].name in reason, reason  # what could not be read
    assert "cleaned up" in reason and "keep_runs_for" in reason, reason  # why a run's files go
    assert (win.width(), win.height()) == (size.width(), size.height()), "the window changed its size"
    assert place(win, tab, "load_orders") == here, "load_orders moved"
