"""Run history keeps the run you selected (clicked) selected, and stays where you scrolled to, as new runs come in
(a scheduled start firing, a run from the editor or a terminal) and runs finish: the same run, by its number, stays
highlighted and holds the current cell; scrolled down, the rows in view stay the same runs; at the top, each new run
shows at the top. A selected run that Keep runs removes leaves nothing selected (never another run in its place).
Double-clicking a run still opens it; the columns and the order stay as they were. With Run history's find bar open
it works the same: its count and current match stay right (on the same run).
Through the real scheduler service, `taskloom run` processes, history.db and the editor (offscreen)."""

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication

from tests.test_find_bar import open_find
from tests.test_history_list_scheduled import (double_click, finished_runs, rows, run_cli, scheduler_service,
                                               write_flow)
from tests.test_lines import click_row, shown, wait_runs
from tests.test_paths_editor_running import eventually

COLUMNS = ["Run", "Started", "Started by", "Status", "Duration", "Parameters"]

# --- helpers ---------------------------------------------------------------------------------------------------


def show_history(win):
    """Look at Run history (the editor shows the Log as a new run's messages come)."""
    win.bottom_tabs.setCurrentWidget(win.history_panel)
    QApplication.processEvents()


def click_run(win, run_id) -> None:
    """Click the run's row in Run history, as you would to select it."""
    show_history(win)
    table = win.history_panel.table
    (row,) = [r for r in range(table.rowCount()) if table.item(r, 0).text() == str(run_id)]
    table.scrollToItem(table.item(row, 0))
    QApplication.processEvents()
    click_row(table, row)


def selected(win) -> list[str]:
    """The runs whose rows are highlighted in Run history, top to bottom ("(part of the row)": not the whole row)."""
    table = win.history_panel.table
    found = sorted({index.row() for index in table.selectedIndexes()})
    return [table.item(r, 0).text() + ("" if table.selectionModel().isRowSelected(r, QModelIndex())
                                       else " (part of the row)") for r in found]


def current(win) -> str | None:
    """The run whose row holds the current cell in Run history, or None."""
    table = win.history_panel.table
    return table.item(table.currentRow(), 0).text() if table.currentRow() >= 0 else None


def in_view(win) -> list[str]:
    """The runs whose rows show in Run history now, top to bottom (also a row only partly in view)."""
    show_history(win)
    table = win.history_panel.table
    first, last = table.rowAt(0), table.rowAt(table.viewport().height() - 1)
    return [table.item(r, 0).text() for r in range(first, (last if last >= 0 else table.rowCount() - 1) + 1)]


def listed_first(qtbot, win, run_id: int, status: str = "success") -> None:
    """Wait until Run history lists the run at the top, with that status."""
    assert eventually(qtbot, lambda: [(r[0], r[2]) for r in rows(win)[:1]] == [(str(run_id), status)],
                      timeout=10_000), f"run {run_id} ({status}) is not at the top of Run history: {rows(win)}"


# --- 1: the run you selected stays selected as new runs come in -------------------------------------------------

def test_the_run_you_selected_stays_selected_when_the_scheduler_service_starts_the_flow(win, qtbot, tmp_path):
    """The user's report: the flow open in the editor, a run selected in Run history (its row clicked), the
    scheduler service on. The Schedule block starts the flow: its run comes to the top of the list, and the run you
    had selected keeps its highlight and the current cell."""
    flow = write_flow(tmp_path / "nightly.yaml", schedule={"every": "1 minute"})
    tab = win.open_flow(flow)
    assert win.save(tab)  # saved with its Schedule block: listed for the scheduler service
    win.run_flow(tab)
    first = wait_runs(qtbot, tab, 1)
    win.run_flow(tab)
    second = wait_runs(qtbot, tab, 2)
    assert eventually(qtbot, lambda: [r[0] for r in rows(win)] == [str(second), str(first)]), rows(win)
    click_run(win, second)  # the newest run, at the top: the new one comes above it
    assert selected(win) == [str(second)] and current(win) == str(second)

    with scheduler_service(win, qtbot):
        qtbot.waitUntil(lambda: any(by == "schedule" for _, by, _ in finished_runs(win, flow)), timeout=30_000)
    (scheduled,) = [int(run) for run, by, _ in finished_runs(win, flow) if by == "schedule"]
    listed_first(qtbot, win, scheduled)

    assert selected(win) == [str(second)], f"after the scheduled run {scheduled}: selected {selected(win)}"
    assert current(win) == str(second)


def test_the_run_you_selected_stays_selected_as_runs_start_and_finish_and_the_list_stays_as_it_was(win, qtbot,
                                                                                                   tmp_path):
    """A run from the editor (listed as running, then finished) and, while it is going, a run as the scheduler
    service starts it: the run you selected (the oldest, at the bottom) stays selected and holds the current cell
    at each step. Run history keeps its columns and lists every run newest first; double-clicking a run opens it."""
    flow = write_flow(tmp_path / "nightly.yaml", wait=6)
    first = run_cli(win, flow, "--trigger", "schedule")
    run_cli(win, flow, "--trigger", "schedule")
    tab = win.open_flow(flow)
    assert eventually(qtbot, lambda: rows(win) == finished_runs(win, flow)), rows(win)
    click_run(win, first)
    assert selected(win) == [str(first)] and current(win) == str(first)

    win.run_flow(tab)  # Manual Start: waits 6 s
    try:
        qtbot.waitUntil(lambda: tab.runs[-1].run_id is not None, timeout=30_000)
        yours = tab.runs[-1].run_id
        listed_first(qtbot, win, yours, "running")
        assert (selected(win), current(win)) == ([str(first)], str(first)), f"your run {yours} began"

        scheduled = run_cli(win, flow, "--trigger", "schedule")  # while yours is going
        listed_first(qtbot, win, scheduled)
        assert (selected(win), current(win)) == ([str(first)], str(first)), f"scheduled run {scheduled} came"
    finally:
        wait_runs(qtbot, tab, 1)  # (it ends by itself)
    assert eventually(qtbot, lambda: rows(win) == finished_runs(win, flow), timeout=10_000), rows(win)
    assert (selected(win), current(win)) == ([str(first)], str(first)), f"your run {yours} finished"

    table = win.history_panel.table
    assert [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())] == COLUMNS
    double_click(win, first)
    assert shown(tab, ["load"]) == {"load": ("success", first)}


def test_scrolled_down_the_rows_in_view_stay_and_at_the_top_each_new_run_shows_at_the_top(win, qtbot, tmp_path):
    """More runs than the list shows at once. Scrolled half way down, a run selected there: a new run comes, and
    the same runs stay in view, the selected one still selected. Back at the top, the newest run selected: a new
    run comes and shows at the top, the one you selected right under it, still selected. And at the top with the
    run you selected out of view (the oldest, at the bottom): each new run shows at the top, the list does not
    jump to the selected run, which stays selected; so too when the Log shows as the run comes."""
    flow = write_flow(tmp_path / "nightly.yaml")
    win.open_flow(flow)
    show_history(win)
    table = win.history_panel.table
    fit = table.viewport().height() // table.verticalHeader().defaultSectionSize() + 1  # rows in view at once
    for _ in range(fit + 6):
        last = run_cli(win, flow)
    listed_first(qtbot, win, last)
    assert len(rows(win)) == fit + 6
    show_history(win)
    bar = table.verticalScrollBar()
    assert bar.maximum() > 0, "the list shows every run at once: nothing to scroll"

    bar.setValue(bar.maximum() // 2)  # scrolled half way down
    QApplication.processEvents()
    choice = in_view(win)[len(in_view(win)) // 2]
    click_run(win, choice)
    before = in_view(win)
    assert before[0] != rows(win)[0][0] and selected(win) == [choice]
    new = run_cli(win, flow, "--trigger", "schedule")
    listed_first(qtbot, win, new)
    assert in_view(win) == before, f"scrolled down, run {new} came: the list shows {in_view(win)}, not {before}"
    assert (selected(win), current(win)) == ([choice], choice)

    bar.setValue(0)  # back at the top
    QApplication.processEvents()
    click_run(win, new)  # the newest run, at the top
    newer = run_cli(win, flow, "--trigger", "schedule")
    listed_first(qtbot, win, newer)
    assert in_view(win)[0] == str(newer), f"at the top, run {newer} came: the list shows {in_view(win)}"
    assert (selected(win), current(win)) == ([str(new)], str(new))

    oldest = rows(win)[-1][0]
    click_run(win, oldest)  # (scrolled to it at the bottom to click it)
    bar.setValue(0)  # back at the top: the run you selected is out of view
    QApplication.processEvents()
    assert oldest not in in_view(win)
    for showing in (win.history_panel, win.log):  # Run history showing as the run comes; then the Log
        win.bottom_tabs.setCurrentWidget(showing)
        newest = run_cli(win, flow, "--trigger", "schedule")
        listed_first(qtbot, win, newest)
        assert in_view(win)[0] == str(newest), (f"at the top, run {oldest} selected out of view, run {newest} came "
                                                f"({type(showing).__name__} showing): the list shows {in_view(win)}")
        assert (selected(win), current(win)) == ([oldest], oldest)


# --- 2: a selected run that Keep runs removes leaves nothing selected --------------------------------------------

def test_when_keep_runs_removes_the_selected_run_no_other_run_is_selected_in_its_place(win, qtbot, tmp_path):
    """Manual Start keeps the last 3 runs. The run you selected stays selected while it is kept; when a new run
    makes Keep runs remove it, nothing is selected (not the run now in its row, nor any other)."""
    flow = tmp_path / "nightly.yaml"
    flow.write_text("blocks:\n"
                    "  manual: {type: trigger.manual, config: {keep: last 3}}\n"
                    "  load: {type: logic.python, config: {inputs: [], outputs: [], code: pass}}\n"
                    "edges: ['manual.start -> load.after']\n", encoding="utf-8")
    runs = [run_cli(win, flow) for _ in range(3)]
    win.open_flow(flow)
    assert eventually(qtbot, lambda: [r[0] for r in rows(win)] == [str(r) for r in reversed(runs)]), rows(win)
    click_run(win, runs[1])
    assert selected(win) == [str(runs[1])]

    runs.append(run_cli(win, flow))  # the oldest goes; the one you selected is kept
    assert eventually(qtbot, lambda: [r[0] for r in rows(win)] == [str(r) for r in reversed(runs[1:])],
                      timeout=10_000), rows(win)
    assert (selected(win), current(win)) == ([str(runs[1])], str(runs[1]))

    runs.append(run_cli(win, flow))  # now the one you selected goes
    assert eventually(qtbot, lambda: [r[0] for r in rows(win)] == [str(r) for r in reversed(runs[2:])],
                      timeout=10_000), rows(win)
    assert selected(win) == [], f"run {runs[1]} is gone: selected {selected(win)}"


# --- 4: with Run history's find bar open, its count and current match stay on the same run -----------------------

def test_with_the_find_bar_open_its_count_and_current_match_stay_on_the_same_run(win, qtbot, tmp_path):
    """Eight runs, each a success. Ctrl+F in Run history, "success" typed (one match per run), Enter to the 4th match:
    4/8, the 5th run's, which going to it selects. A new run comes at the top: 5/9, the same run's cell still the
    current match and its run still selected; Enter goes on to the next older run. Then another run clicked: as a
    new run comes, the count moves down with the current match and the run you clicked stays selected."""
    flow = write_flow(tmp_path / "nightly.yaml")
    runs = [run_cli(win, flow) for _ in range(8)]
    win.open_flow(flow)
    listed_first(qtbot, win, runs[-1])
    show_history(win)
    bar = open_find(win, qtbot, win.history_panel.table)
    bar.type("success")
    assert bar.count() == "1/8"
    for _ in range(3):
        bar.key(Qt.Key_Return)
    assert bar.count() == "4/8"
    assert (selected(win), current(win)) == ([str(runs[4])], str(runs[4]))

    new = run_cli(win, flow, "--trigger", "schedule")
    listed_first(qtbot, win, new)
    show_history(win)  # (the editor shows the Log as a new run comes; the bar's count shows with Run history)
    assert bar.count() == "5/9", f"run {new} came above the current match (run {runs[4]})"
    assert (selected(win), current(win)) == ([str(runs[4])], str(runs[4]))
    bar.key(Qt.Key_Return)
    assert (bar.count(), selected(win)) == ("6/9", [str(runs[3])])

    click_run(win, runs[1])
    assert selected(win) == [str(runs[1])]
    at = bar.current()
    newer = run_cli(win, flow, "--trigger", "schedule")
    listed_first(qtbot, win, newer)
    show_history(win)
    assert bar.count() == f"{at + 2}/10", f"run {newer} came above the current match ({at + 1}/9)"
    assert (selected(win), current(win)) == ([str(runs[1])], str(runs[1]))
