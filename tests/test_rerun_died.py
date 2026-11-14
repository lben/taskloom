"""Running a block again after a re-run that ended before its own block finished: rows RR-2 and RR-6 of
docs/promises/reruns.yaml (agreed 2026-11-11).

A re-run starts from what the earlier run held, the earlier output of the block it runs included. When the re-run's
process dies while that block runs (or Stop comes before the block began), the block made nothing new: the block after
it, run again alone, must be refused in words that name the block to run again first, never run with the rows of
before.

As in tests/test_rerun_carries_on.py, whose helpers these tests use: the real editor window, the entries of a block's
right-click menu, each run a real runner process. A test reads only what a user can see: what a block received
(<block>_got.txt, one line per time it ran), the blocks that ran (ran.txt), the Log's words and the text under a block.
"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_rerun_carries_on import (LOAD_ORDERS, RUN_ON, RUN_ONLY, choose, do, entry, lines_of,  # noqa: E402
                                         noting, python, received, run_now, under, write_flow)

FLOW_RAN = ["load_orders", "clean", "report"]  # ran.txt after the full run


def slow_orders_flow(folder: Path) -> Path:
    """manual -> load_orders -> clean -> report, as the orders flow (clean and report note how many rows reached
    them); load_orders notes that it began, then does not go on while hold.txt exists (a slow query): a run can be
    ended while load_orders runs."""
    return write_flow(folder, {
        "load_orders": python(noting("load_orders", "'began'") + "import os, time\nwhile os.path.exists('hold.txt'):\n"
                              "    time.sleep(0.05)\n" + LOAD_ORDERS, (), ("result",)),
        "clean": python(noting("clean", "len(orders)") + "result = orders", ("orders",), ("result",)),
        "report": python(noting("report", "len(rows)") + "text = f'{len(rows)} orders'", ("rows",), ("text",)),
    }, ["manual.start -> load_orders.after", "load_orders.result -> clean.orders", "clean.result -> report.rows"])


def ten_rows_then_twelve(win, qtbot, folder: Path):
    """Full run #1: load_orders gives 10 rows, clean and report get them. Then its table has 12 rows. Gives the
    flow's tab."""
    (folder / "orders.txt").write_text("10")
    tab = win.open_flow(slow_orders_flow(folder))
    first = run_now(win, qtbot, tab)
    assert first.status == "success" and lines_of(folder / "ran.txt") == FLOW_RAN, first.log
    assert received(folder, "clean") == ["10"] and received(folder, "report") == ["10"]
    (folder / "orders.txt").write_text("12")
    return tab


def died_while_load_orders_ran(win, qtbot, folder: Path, words: str):
    """After ten_rows_then_twelve: load_orders is run again (`words`: alone, or from it on), run #2, and that run's
    process is killed while load_orders runs (e.g. the computer ran out of memory). Gives (the flow's tab, run #2)."""
    tab = ten_rows_then_twelve(win, qtbot, folder)
    (folder / "hold.txt").write_text("")  # its query is slow from now on

    def kill_while_load_orders_runs():
        entry(win, tab, "load_orders", words).trigger()
        qtbot.waitUntil(lambda: under(tab, "load_orders").startswith("running"), timeout=60_000)
        tab.runs[-1].proc.kill()

    died = do(win, qtbot, tab, kill_while_load_orders_runs)
    (folder / "hold.txt").unlink()
    # Run #2 failed and load_orders shows it; it began and made nothing, and no block after it began.
    assert died.status == "failed" and died.run is not None and under(tab, "load_orders").startswith("✕"), died.log
    assert lines_of(folder / "ran.txt") == FLOW_RAN + ["load_orders"]
    return tab, died


@pytest.mark.invariant("RR-2")
@pytest.mark.invariant("RR-6")
def test_after_a_rerun_of_a_block_alone_died_while_the_block_ran_the_block_after_it_is_refused_naming_that_block(
        win, qtbot, tmp_path):
    tab, _ = died_while_load_orders_ran(win, qtbot, tmp_path, RUN_ONLY)
    assert under(tab, "clean").startswith("old · "), under(tab, "clean")

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert received(tmp_path, "clean") == ["10"], clean.log  # it did not run: not with the 10 rows of run #1 again
    assert clean.status != "success" and "load_orders" in clean.said, clean.log  # the Log names the block to run first
    assert under(tab, "clean").startswith("old · "), under(tab, "clean")  # what it shows is still from before


@pytest.mark.invariant("RR-2")
@pytest.mark.invariant("RR-6")
def test_after_a_rerun_from_a_block_on_died_while_that_block_ran_the_blocks_after_it_are_each_refused_when_rerun_alone(
        win, qtbot, tmp_path):
    tab, _ = died_while_load_orders_ran(win, qtbot, tmp_path, RUN_ON)

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)
    report = choose(win, qtbot, tab, "report", RUN_ONLY)

    # Neither ran: not with the 10 rows of run #1 again, nor with what clean made of them.
    assert (received(tmp_path, "clean"), received(tmp_path, "report")) == (["10"], ["10"]), (clean.log, report.log)
    assert clean.status != "success" and "load_orders" in clean.said, clean.log  # the Log names the block to run first
    assert report.status != "success", report.log


@pytest.mark.invariant("RR-2")
@pytest.mark.parametrize("words", [RUN_ONLY, RUN_ON], ids=["it died run alone", "it died run from the block on"])
def test_after_a_rerun_that_died_the_block_rerun_alone_succeeds_and_the_block_after_it_gets_its_new_rows(
        win, qtbot, tmp_path, words):
    tab, _ = died_while_load_orders_ran(win, qtbot, tmp_path, words)

    again = choose(win, qtbot, tab, "load_orders", RUN_ONLY)  # its query is no longer slow
    assert again.status == "success" and "12 rows" in under(tab, "load_orders"), again.log
    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert received(tmp_path, "clean") == ["10", "12"], clean.log  # the 12 rows, not the 10 of run #1 again
    assert clean.status == "success", clean.log


@pytest.mark.invariant("RR-2")
def test_after_a_rerun_stopped_before_its_block_began_the_block_after_it_is_refused_naming_that_block(
        win, qtbot, tmp_path):
    tab = ten_rows_then_twelve(win, qtbot, tmp_path)

    def stop_at_once():
        entry(win, tab, "load_orders", RUN_ONLY).trigger()
        win.stop(tab)  # Stop reaches run #2's process as it starts: before it gets to load_orders

    stopped = do(win, qtbot, tab, stop_at_once)
    assert stopped.status == "cancelled" and stopped.run is not None, stopped.log
    assert lines_of(tmp_path / "ran.txt") == FLOW_RAN, "run #2 was to be stopped before load_orders began"

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert received(tmp_path, "clean") == ["10"], clean.log  # it did not run: not with the 10 rows of run #1 again
    assert clean.status != "success" and "load_orders" in clean.said, clean.log  # the Log names the block to run first
