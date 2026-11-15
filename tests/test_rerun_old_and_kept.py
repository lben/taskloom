"""A re-run is seen by the blocks after it, the rows RR-7 and RR-5 of docs/promises/reruns.yaml (the other rows are in
tests/test_rerun_carries_on.py, whose flows and ways of driving the editor these tests use).

RR-7: a result made before an earlier block ran again is marked old. Read as a user sees it: the text under the block
on the canvas ("old · " before its result), what hovering that text says, how it is drawn (grey, with a grey border)
and Data preview's label with the block selected.

RR-5: what a re-run keeps from before also comes from the newest run: a Loop's items that already succeeded, the runs
of a Merger's lines (set to each line as it finishes), a Session's cookies (a web server on this computer, started by
the test; never the internet).
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from taskloom.editor.canvas import FOOTER_H, NODE_W  # noqa: E402
from tests.test_api_session import MANUAL, LoginSite, api, chain, session  # noqa: E402
from tests.test_loop_container import per_item, ran, read_json, render, saver  # noqa: E402
from tests.test_merger_each_line import LINE_OF, WHOLE, after, each_line, front, merger, three  # noqa: E402
from tests.test_merger_each_line import python as placed  # noqa: E402
from tests.test_paths_editor_editing import flow_file  # noqa: E402
from tests.test_rerun_carries_on import (RUN_ON, RUN_ONLY, choose, full_run, load_orders_alone,  # noqa: E402
                                         open_from_history, plain, received, run_now, under)

EVERY = ("manual", "load_orders", "clean", "report", "send", "archive")  # the orders flow's blocks, in its order
AFTER_LOAD_ORDERS = EVERY[2:]


# --- what a user sees of a block's result -------------------------------------------------------------------------------

def hover(tab, block: str) -> str:
    """What hovering the text under a block says."""
    return plain(tab.scene.nodes[block].footer.toolTip())


def preview_label(win, tab, block: str) -> str:
    """Data preview's label with `block` selected."""
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)
    QApplication.processEvents()
    label = win.preview.label.text()
    tab.scene.clearSelection()
    QApplication.processEvents()
    return label


def strongest_colour(tab, block: str) -> dict:
    """How far from grey the block's border (along its bottom side) and the text under it are drawn: for each, the
    largest difference between a pixel's red, green and blue (0: black, white or a grey; green or red: over 100)."""
    node = tab.scene.nodes[block]
    tab.scene.clearSelection()
    QApplication.processEvents()
    parts = {"border": QRectF(NODE_W * 0.3, node.height - 2, NODE_W * 0.4, 4),
             "text": QRectF(10, node.height - FOOTER_H, NODE_W - 20, FOOTER_H - 4)}
    found = {}
    for part, rect in parts.items():
        image = render(tab.scene, node.mapRectToScene(rect), 2.0)
        pixels = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())]
        found[part] = max(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) for c in pixels)
    return found


def old_blocks(tab) -> list:
    """The blocks whose result is marked old: under them, or when hovering that."""
    return [block for block in EVERY if under(tab, block).startswith("old") or hover(tab, block).startswith("Old")]


def after_load_orders_ran_again(win, qtbot, folder):
    """The rows' runs #5 and #6: a full run (10 rows; send fails, so archive is skipped), then load_orders changed
    (12 rows) and re-run alone. Gives (the tab, what was under each block after the full run, the full run, the
    re-run)."""
    tab, full = full_run(win, qtbot, folder, rows=10, mail_down=True)
    assert full.status == "failed" and under(tab, "send").startswith("✕"), full.log
    assert under(tab, "archive").startswith("skipped"), under(tab, "archive")
    as_it_was = {block: under(tab, block) for block in EVERY}
    alone = load_orders_alone(win, qtbot, tab, folder, rows=12)
    assert alone.status == "success" and "12 rows" in under(tab, "load_orders"), alone.log
    return tab, as_it_was, full, alone


# --- RR-7: a result made before an earlier block ran again is marked old ---------------------------------------------------

@pytest.mark.invariant("RR-7")
def test_every_block_after_a_block_rerun_alone_shows_old_before_its_result_also_a_failed_and_a_skipped_one(
        win, qtbot, tmp_path):
    tab, as_it_was, full, alone = after_load_orders_ran_again(win, qtbot, tmp_path)

    assert {block: under(tab, block) for block in AFTER_LOAD_ORDERS} == {
        block: "old · " + as_it_was[block] for block in AFTER_LOAD_ORDERS}
    assert under(tab, "send").startswith("old · ✕")  # a failed result keeps its ✕
    assert under(tab, "archive") == "old · skipped · something before it failed"
    assert not under(tab, "load_orders").startswith("old")  # the block that ran again
    assert under(tab, "manual") == as_it_was["manual"]  # a block before it


@pytest.mark.invariant("RR-7")
def test_hovering_an_old_result_says_which_block_ran_again_in_which_run_and_how_to_bring_it_up_to_date(
        win, qtbot, tmp_path):
    tab, as_it_was, full, alone = after_load_orders_ran_again(win, qtbot, tmp_path)
    says = (f"Old: made before load_orders ran again (run #{alone.run}). Right-click → Run only this block to bring "
            "it up to date.")

    for block in AFTER_LOAD_ORDERS:
        assert hover(tab, block).startswith(says), (block, hover(tab, block))
    for block in ("manual", "load_orders"):
        assert not hover(tab, block).startswith("Old"), (block, hover(tab, block))


@pytest.mark.invariant("RR-7")
def test_data_previews_label_says_old_for_a_table_a_text_and_a_failed_result(win, qtbot, tmp_path):
    tab, as_it_was, full, alone = after_load_orders_ran_again(win, qtbot, tmp_path)

    assert preview_label(win, tab, "clean") == "clean.result (old): 10 rows × 3 columns"  # a table
    assert preview_label(win, tab, "report") == "report.text (old):"  # a text
    assert preview_label(win, tab, "send").startswith("send: failed, no output (old)"), preview_label(win, tab, "send")
    assert preview_label(win, tab, "load_orders") == "load_orders.result: 12 rows × 3 columns"  # the one that ran again


@pytest.mark.invariant("RR-7")
def test_an_old_result_is_drawn_in_grey_with_a_grey_border_also_a_failed_one(win, qtbot, tmp_path):
    tab, as_it_was, full, alone = after_load_orders_ran_again(win, qtbot, tmp_path)

    fresh = strongest_colour(tab, "load_orders")
    assert fresh["border"] > 80 and fresh["text"] > 80, fresh  # (green: this way of looking tells a colour from grey)
    for block in ("clean", "send"):  # one that succeeded, one that failed (a result not old: green, red)
        drawn = strongest_colour(tab, block)
        assert drawn["border"] < 50 and drawn["text"] < 50, (block, drawn)


@pytest.mark.invariant("RR-7")
def test_after_the_next_block_is_rerun_alone_it_is_no_longer_old_and_the_blocks_after_it_still_are(win, qtbot, tmp_path):
    tab, as_it_was, full, alone = after_load_orders_ran_again(win, qtbot, tmp_path)

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert clean.status == "success" and received(tmp_path, "clean") == ["10", "12"], clean.log
    assert under(tab, "clean").startswith("✓") and "12 rows" in under(tab, "clean"), under(tab, "clean")
    assert not hover(tab, "clean").startswith("Old"), hover(tab, "clean")
    assert preview_label(win, tab, "clean") == "clean.result: 12 rows × 3 columns"
    for block in ("report", "send", "archive"):
        assert under(tab, block) == "old · " + as_it_was[block], block
        assert hover(tab, block).startswith("Old: made before "), (block, hover(tab, block))
    assert preview_label(win, tab, "report") == "report.text (old):"


@pytest.mark.invariant("RR-7")
@pytest.mark.invariant("RR-8")
def test_after_run_from_this_block_on_nothing_it_ran_is_old(win, qtbot, tmp_path):
    tab, as_it_was, full, alone = after_load_orders_ran_again(win, qtbot, tmp_path)
    (tmp_path / "mail_down.txt").unlink()  # the mail server is back
    assert old_blocks(tab) == list(AFTER_LOAD_ORDERS)

    on = choose(win, qtbot, tab, "clean", RUN_ON)

    assert on.status == "success" and received(tmp_path, "send") == ["10 orders", "12 orders"], on.log
    assert old_blocks(tab) == []
    assert preview_label(win, tab, "report") == "report.text:"


@pytest.mark.invariant("RR-7")
@pytest.mark.invariant("RR-10")
def test_nothing_is_old_after_a_full_run(win, qtbot, tmp_path):
    tab, as_it_was, full, alone = after_load_orders_ran_again(win, qtbot, tmp_path)

    again = run_now(win, qtbot, tab)

    assert again.status == "failed" and received(tmp_path, "send") == ["10 orders", "12 orders"], again.log
    assert old_blocks(tab) == []
    assert preview_label(win, tab, "clean") == "clean.result: 12 rows × 3 columns"
    assert preview_label(win, tab, "report") == "report.text:"


@pytest.mark.invariant("RR-7")
@pytest.mark.invariant("RR-9")
def test_nothing_is_old_while_a_run_opened_from_run_history_is_shown(win, qtbot, tmp_path):
    tab, as_it_was, full, alone = after_load_orders_ran_again(win, qtbot, tmp_path)

    open_from_history(win, full.run)

    assert {block: under(tab, block) for block in EVERY} == as_it_was
    assert old_blocks(tab) == []
    assert preview_label(win, tab, "clean") == "clean.result: 10 rows × 3 columns"


# --- RR-5: what a re-run keeps from before also comes from the newest run --------------------------------------------------

@pytest.mark.invariant("RR-5")
def test_a_loop_rerun_alone_keeps_the_items_of_the_newest_run_and_the_block_after_it_gets_every_result(win, qtbot,
                                                                                                    tmp_path):
    """A Loop over 5 items (all at the same time); items 2 and 4 fail in the full run. Item 2 fixed, the Loop is
    re-run alone: it runs items 2 and 4 only, and item 4 fails again. Item 4 fixed, the Loop is re-run alone again: it
    runs item 4 only (item 2 is kept from the re-run before, the others from the full run). The block after the Loop,
    re-run alone, gets all five results."""
    work = ("import os, time\nopen(f'ran_{index}.txt', 'a', encoding='utf-8').write('x')\n"
            "if index in (2, 4) and not os.path.exists(f'fixed_{index}.txt'):\n"
            "    time.sleep(0.5)\n    raise ValueError(f'no data for {item}')\n"
            "output = item.upper()")
    tab = win.open_flow(per_item(tmp_path, ["a", "b", "c", "d", "e"], work, same_time=5))
    times_ran = lambda: [ran(tmp_path, str(k)) for k in range(1, 6)]  # noqa: E731
    full = run_now(win, qtbot, tab)
    assert full.status == "failed" and times_ran() == [1, 1, 1, 1, 1], full.log
    (tmp_path / "fixed_2.txt").write_text("")
    first = choose(win, qtbot, tab, "each", RUN_ONLY)
    assert first.status == "failed" and times_ran() == [1, 2, 1, 2, 1], first.log
    (tmp_path / "fixed_4.txt").write_text("")

    second = choose(win, qtbot, tab, "each", RUN_ONLY)
    report = choose(win, qtbot, tab, "report", RUN_ONLY)

    assert second.status == "success" and times_ran() == [1, 2, 1, 3, 1], second.log
    assert report.status == "success", report.log
    assert read_json(tmp_path / "report.json") == ["A", "B", "C", "D", "E"] and ran(tmp_path, "report") == 1


def lines_flow(folder):
    """load_sales, load_costs and load_stock (three lines) -> merge (each line as it finishes) -> report, once per
    line (for costs and for stock it fails until fixed_costs.txt, fixed_stock.txt exists) -> gather (all lines
    together) -> summary (it writes what it got to summary.json)."""
    blocks, edges = front(three())
    merge, gather = each_line(), merger(x=1500)
    report = ("import os\nopen(f'ran_report_{input}.txt', 'a', encoding='utf-8').write('x')\n"
              "if input in ('costs', 'stock') and not os.path.exists(f'fixed_{input}.txt'):\n"
              "    raise ValueError(f'the {input} report is late')\n"
              "output = input.upper()")
    blocks |= {"merge": merge, "report": placed(report, x=1200), "gather": gather,
               "summary": saver("summary.json", x=1800)}
    return flow_file(folder / "flow.yaml", blocks, [*edges, after("merge", merge, "report"),
                                                     "report.output -> gather.lines", after("gather", gather, "summary")])


@pytest.mark.invariant("RR-5")
def test_a_merger_of_each_line_rerun_alone_keeps_the_lines_runs_that_succeeded_in_the_newest_run(win, qtbot, tmp_path):
    """Three lines into a Merger set to each line as it finishes; the report after it fails for costs and for stock in
    the full run. Costs fixed, the Merger is re-run alone: report runs for costs and stock only, and fails again for
    stock. Stock fixed, the Merger is re-run alone again: report runs for stock only (costs' run is kept from the
    re-run before, sales' from the full run). Run from this block on, on the Merger, then runs report for no line
    again, and the blocks after get all three lines' reports."""
    tab = win.open_flow(lines_flow(tmp_path))
    times_ran = lambda: [ran(tmp_path, f"report_{value}") for value in LINE_OF]  # noqa: E731 (sales, costs, stock)
    full = run_now(win, qtbot, tab)
    assert full.status == "failed" and times_ran() == [1, 1, 1], full.log
    (tmp_path / "fixed_costs.txt").write_text("")
    first = choose(win, qtbot, tab, "merge", RUN_ONLY)
    assert first.status == "failed" and times_ran() == [1, 2, 2], first.log
    (tmp_path / "fixed_stock.txt").write_text("")

    second = choose(win, qtbot, tab, "merge", RUN_ONLY)
    on = choose(win, qtbot, tab, "merge", RUN_ON)

    assert second.status == "success" and on.status == "success", (second.log, on.log)
    assert times_ran() == [1, 2, 3]
    assert read_json(tmp_path / "summary.json") == WHOLE and ran(tmp_path, "summary") == 1
    assert [ran(tmp_path, line) for line in LINE_OF.values()] == [1, 1, 1]  # the lines themselves ran once


@pytest.mark.invariant("RR-5")
@pytest.mark.invariant("RR-1")
def test_after_a_merger_of_each_line_is_rerun_alone_the_blocks_after_its_runs_rerun_alone_get_every_lines_result(
        win, qtbot, tmp_path):
    """As the Loop's case, for a Merger set to each line as it finishes: the report after it fails for costs in the full
    run, so the blocks after the lines' runs are skipped. Costs fixed, the Merger is re-run alone (report runs for costs
    only). gather, then summary, each re-run alone, get all three lines' reports."""
    (tmp_path / "fixed_stock.txt").write_text("")  # only costs' report fails
    tab = win.open_flow(lines_flow(tmp_path))
    times_ran = lambda: [ran(tmp_path, f"report_{value}") for value in LINE_OF]  # noqa: E731 (sales, costs, stock)
    full = run_now(win, qtbot, tab)
    assert full.status == "failed" and under(tab, "summary").startswith("skipped"), full.log
    (tmp_path / "fixed_costs.txt").write_text("")
    merge = choose(win, qtbot, tab, "merge", RUN_ONLY)
    assert merge.status == "success" and times_ran() == [1, 2, 1], merge.log

    gather = choose(win, qtbot, tab, "gather", RUN_ONLY)
    summary = choose(win, qtbot, tab, "summary", RUN_ONLY)

    assert gather.status == "success", gather.log
    assert summary.status == "success", summary.log
    assert read_json(tmp_path / "summary.json") == WHOLE and ran(tmp_path, "summary") == 1
    assert times_ran() == [1, 2, 1]


@pytest.mark.invariant("RR-5")
def test_a_request_in_a_session_rerun_alone_sends_the_cookie_the_request_before_it_got_when_it_was_rerun_alone(
        win, qtbot, tmp_path):
    """login -> data -> more inside a Session; each login gives a new session cookie. After a full run, login is
    re-run alone (a new cookie). data, then more, each re-run alone, send that new cookie, not the full run's."""
    with LoginSite() as site:
        tab = win.open_flow(flow_file(tmp_path / "flow.yaml", {
            "manual": MANUAL, "sess": session(0, 0),
            **{name: api(site.url(f"/{name}"), k) for k, name in enumerate(("login", "data", "more"))},
        }, ["manual.start -> login.after", *chain("login", "data", "more")]))
        full = run_now(win, qtbot, tab)
        assert full.status == "success", full.log
        assert site.seen == [("/login", {}), ("/data", {"sessionid": site.issued[0]}),
                             ("/more", {"sessionid": site.issued[0]})]
        login = choose(win, qtbot, tab, "login", RUN_ONLY)
        assert login.status == "success" and len(site.issued) == 2, login.log

        data = choose(win, qtbot, tab, "data", RUN_ONLY)
        more = choose(win, qtbot, tab, "more", RUN_ONLY)

    assert data.status == "success" and more.status == "success", (data.log, more.log)
    assert site.seen[4:] == [("/data", {"sessionid": site.issued[1]}), ("/more", {"sessionid": site.issued[1]})]
    assert len(site.issued) == 2  # nothing logged in again
