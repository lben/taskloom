"""Running a block again after a full run that ended before reaching it, and after Keep runs let a re-run in between
go: rows RR-1, RR-2, RR-3 and RR-8 of docs/promises/reruns.yaml (agreed 2026-11-11).

As in tests/test_rerun_carries_on.py, whose helpers these tests use: the real editor window, the entries of a block's
right-click menu, each run a real runner process. A test reads only what a user can see: what a block received
(<block>_got.txt, one line per time it ran), the Log's words, the menu's words, Run history and the text under a block.
"""
import os
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_paths_editor_running import (block_running, eventually, running_run, start_elsewhere,  # noqa: E402
                                             stop_elsewhere, wait_without_the_editor)
from tests.test_rerun_carries_on import (LOAD_ORDERS, RUN_ONLY, choose, do, entry, history_rows,  # noqa: E402
                                         load_orders_alone, noting, orders_flow, python, received, run_now, under,
                                         write_flow)

# --- a full run that ended after load_orders finished and before clean began ------------------------------------------

STOPPED = "you pressed Stop"
DIED = "its process died"
DIED_ELSEWHERE = "started from a terminal, its process died"  # (`taskloom run FLOW`, as the scheduler starts one)


def held_flow(folder: Path) -> Path:
    """manual -> load_orders -> clean -> report, as the orders flow (clean and report note how many rows reached them);
    and hold, after load_orders is done. clean does not depend on hold, but blocks run one at a time in the file's
    order, so hold runs before clean, and it does not finish while hold.txt exists: a full run can be ended there."""
    return write_flow(folder, {
        "load_orders": python(LOAD_ORDERS, (), ("result",)),
        "hold": python("import os, time\nwhile os.path.exists('hold.txt'):\n    time.sleep(0.05)\n", (), ()),
        "clean": python(noting("clean", "len(orders)") + "result = orders", ("orders",), ("result",)),
        "report": python(noting("report", "len(rows)") + "text = f'{len(rows)} orders'", ("rows",), ("text",)),
    }, ["manual.start -> load_orders.after", "load_orders.done -> hold.after", "load_orders.result -> clean.orders",
        "clean.result -> report.rows"])


def ended_before_clean(win, qtbot, folder: Path, how: str):
    """Full run #1: load_orders gives 10 rows, clean and report get them. Then its table has 12 rows, and full run #2
    ends (`how`) while hold runs: load_orders has finished in it, clean has not begun. Gives (the flow's tab, the
    number of run #2)."""
    (folder / "orders.txt").write_text("10")
    path = held_flow(folder)
    tab = win.open_flow(path)
    first = run_now(win, qtbot, tab)
    assert first.status == "success", first.log
    assert received(folder, "clean") == ["10"] and received(folder, "report") == ["10"]
    (folder / "orders.txt").write_text("12")
    (folder / "hold.txt").write_text("")  # from now on hold does not finish
    if how == DIED_ELSEWHERE:
        terminal = start_elsewhere(win, path)
        try:
            second = wait_without_the_editor(lambda: running_run(win, path))
            wait_without_the_editor(lambda: block_running(win, second, "hold"))
            terminal.kill()  # e.g. the computer restarted
            terminal.wait(30)
        finally:
            stop_elsewhere(terminal)
    else:
        def end_while_hold_runs():
            win.run_flow(tab)
            qtbot.waitUntil(lambda: under(tab, "hold").startswith("running"), timeout=60_000)
            if how == STOPPED:
                win.stop(tab)
            else:
                tab.runs[-1].proc.kill()

        second = do(win, qtbot, tab, end_while_hold_runs).run
    (folder / "hold.txt").unlink()
    # The editor shows what run #2 made (one started elsewhere: once it has looked at the history again).
    assert eventually(qtbot, lambda: "12 rows" in under(tab, "load_orders"), timeout=20_000), under(tab, "load_orders")
    assert second is not None and received(folder, "clean") == ["10"]  # clean did not begin in run #2
    return tab, second


@pytest.mark.invariant("RR-2")
@pytest.mark.parametrize("how", [STOPPED, DIED, DIED_ELSEWHERE])
def test_after_a_full_run_that_ended_before_a_block_that_block_rerun_alone_gets_what_the_run_made_before_it(
        win, qtbot, tmp_path, how):
    tab, second = ended_before_clean(win, qtbot, tmp_path, how)

    named = entry(win, tab, "clean", RUN_ONLY).text()
    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert received(tmp_path, "clean") == ["10", "12"], clean.log  # the 12 rows of run #2, not the 10 of run #1 again
    assert clean.status == "success", clean.log
    assert named.startswith(f"{RUN_ONLY} (run #{second} · "), named


@pytest.mark.invariant("RR-8")
@pytest.mark.parametrize("how", [STOPPED, DIED_ELSEWHERE])
def test_after_a_full_run_that_ended_before_a_block_the_block_after_it_is_refused_until_that_block_is_rerun(
        win, qtbot, tmp_path, how):
    tab, _ = ended_before_clean(win, qtbot, tmp_path, how)

    report = choose(win, qtbot, tab, "report", RUN_ONLY)  # without re-running clean, which run #2 did not reach

    assert received(tmp_path, "report") == ["10"], report.log  # it did not run: not with what clean made of the 10 rows
    assert report.status != "success" and "clean" in report.said, report.log  # the Log names the block to re-run first

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)
    again = choose(win, qtbot, tab, "report", RUN_ONLY)

    assert (clean.status, again.status) == ("success", "success"), (clean.log, again.log)
    assert received(tmp_path, "clean") == ["10", "12"] and received(tmp_path, "report") == ["10", "12"]


# --- Keep runs lets a re-run in between go ----------------------------------------------------------------------------

def keep_runs(path: Path, keep: str, keep_failed: bool) -> None:
    """The start block's Keep runs and Always keep failed runs, as Properties writes them in the flow file."""
    flow = yaml.safe_load(path.read_text(encoding="utf-8"))
    flow["blocks"]["manual"]["config"] = {"keep": keep, "keep_failed": keep_failed}
    path.write_text(yaml.safe_dump(flow, sort_keys=False), encoding="utf-8")


def fixed_then_clean_alone(win, qtbot, folder: Path):
    """The start block keeps the newest run and every failed one. Run #1: load_orders fails (there is no orders.txt).
    Its table is put right and it is re-run alone (#2, 12 rows), then clean alone (#3, which carries on from #2): as
    #3 ends, Keep runs lets #2 go. Gives (the flow's tab, run #1, run #3)."""
    path = orders_flow(folder)
    keep_runs(path, "last 1", keep_failed=True)
    tab = win.open_flow(path)
    full = run_now(win, qtbot, tab)
    fixed = load_orders_alone(win, qtbot, tab, folder, rows=12)
    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)
    assert (full.status, fixed.status, clean.status) == ("failed", "success", "success"), clean.log
    assert received(folder, "clean") == ["12"] and received(folder, "report") == []
    assert set(history_rows(win)) == {full.run, clean.run}, history_rows(win)  # run #2 is gone
    return tab, full, clean


@pytest.mark.invariant("RR-1")
@pytest.mark.invariant("RR-3")
@pytest.mark.parametrize("block, got", [("report", ["12"]), ("clean", ["12", "12"])],
                         ids=["report alone", "clean alone again"])
def test_with_the_rerun_in_between_let_go_by_keep_runs_the_newest_rerun_is_named_and_used(win, qtbot, tmp_path, block,
                                                                                         got):
    tab, _, clean = fixed_then_clean_alone(win, qtbot, tmp_path)

    named = entry(win, tab, block, RUN_ONLY).text()
    again = choose(win, qtbot, tab, block, RUN_ONLY)

    assert named.startswith(f"{RUN_ONLY} (run #{clean.run} · "), named
    assert again.status == "success", again.log  # not "would not run: load_orders, before it, failed" (run #1)
    assert received(tmp_path, block) == got  # report: what clean made in run #3; clean again: load_orders' 12 rows


@pytest.mark.invariant("RR-3")
def test_with_the_run_of_a_blocks_own_result_let_go_by_keep_runs_the_rerun_that_carried_on_from_it_is_named_and_used(
        win, qtbot, tmp_path):
    tab, full, _ = fixed_then_clean_alone(win, qtbot, tmp_path)
    report = choose(win, qtbot, tab, "report", RUN_ONLY)  # run #4 carries on from #3; as it ends, Keep runs lets #3 go
    assert report.status == "success" and received(tmp_path, "report") == ["12"], report.log
    assert set(history_rows(win)) == {full.run, report.run}, history_rows(win)  # run #3, of clean's result, is gone

    named = entry(win, tab, "clean", RUN_ONLY).text()
    again = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert received(tmp_path, "clean") == ["12", "12"], again.log  # load_orders' 12 rows, which run #4 still holds
    assert again.status == "success", again.log
    assert named.startswith(f"{RUN_ONLY} (run #{report.run} · "), named
