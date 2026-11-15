"""Running a block again: what mutation testing of the re-run code (docs/promises/reruns.mutants.yaml) found no test
for, in the functions it runs and, taken out by hand, in the changed lines of those it leaves out; and a closing
Merger run alone after a full run in which it ran. Rows of docs/promises/reruns.yaml, where a test is a row's
behaviour.

As in tests/test_rerun_carries_on.py, whose helpers these tests use: the real editor window and the entries of a
block's right-click menu, or the real command line; each run a real runner process.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from taskloom.editor.canvas import FOOTER_H  # noqa: E402
from tests.test_api_session import MANUAL, LoginSite, api, session  # noqa: E402
from tests.test_loop_container import per_item, ran, read_json, render  # noqa: E402
from tests.test_merger_each_line import LINE_OF, WHOLE  # noqa: E402
from tests.test_paths_editor_editing import flow_file  # noqa: E402
from tests.test_paths_editor_running import (eventually, running_run, start_elsewhere, stop_elsewhere,  # noqa: E402
                                             wait_without_the_editor)
from tests.test_rerun_carries_on import (RUN_ON, RUN_ONLY, choose, do, entry, full_run,  # noqa: E402
                                         load_orders_alone, open_from_history, orders_flow, python, run_now, under,
                                         write_flow)
from tests.test_rerun_old_and_kept import after_load_orders_ran_again, hover, lines_flow  # noqa: E402

CHAIN = """
    blocks:
      load:
        type: logic.python
        config: {inputs: [], code: "output = 'EU'"}
      clean:
        type: logic.python
        config: {code: "output = input.lower()"}
      save:
        type: logic.python
        config: {outputs: [], code: "open('saved.txt', 'a').write(input)"}
    edges:
      - load.output -> clean.input
      - clean.output -> save.input
"""


def test_a_block_refused_because_the_block_before_it_did_not_run_is_told_to_run_the_whole_flow_instead_once(env):
    flow = env.write("flow.yaml", CHAIN)
    assert env.taskloom("run", flow, "--only", "load").returncode == 0  # run #1, of only load: clean did not run in it

    result = env.taskloom("run", flow, "--only", "save", "--inputs-from", "1")

    assert result.returncode == 2 and "clean did not run in run #1" in result.stderr, result.stderr
    assert result.stderr.count("run the whole flow instead") == 1, result.stderr
    assert result.stderr.strip().endswith(": run the whole flow instead, or choose its inputs"), result.stderr
    assert not (env.root / "saved.txt").exists()


@pytest.mark.invariant("RR-5")
def test_a_closing_merger_rerun_alone_after_a_full_run_in_which_it_ran_gets_every_lines_result(win, qtbot, tmp_path):
    """Three lines into a Merger set to each line as it finishes, a report once per line, then gather (the Merger that
    closes the lines' runs) and summary. Every report succeeds in the full run, so gather ran in it. gather, then
    summary, each re-run alone, get all three lines' reports again, and no line's report runs again."""
    for line in ("costs", "stock"):
        (tmp_path / f"fixed_{line}.txt").write_text("")
    tab = win.open_flow(lines_flow(tmp_path))
    full = run_now(win, qtbot, tab)
    assert full.status == "success" and read_json(tmp_path / "summary.json") == WHOLE, full.log
    (tmp_path / "summary.json").unlink()

    gather = choose(win, qtbot, tab, "gather", RUN_ONLY)
    summary = choose(win, qtbot, tab, "summary", RUN_ONLY)

    assert gather.status == "success", gather.log
    assert summary.status == "success", summary.log
    assert read_json(tmp_path / "summary.json") == WHOLE and ran(tmp_path, "summary") == 2
    assert [ran(tmp_path, f"report_{value}") for value in LINE_OF] == [1, 1, 1]


@pytest.mark.invariant("RR-3")
def test_a_rerun_that_is_still_going_is_not_the_run_the_block_after_it_takes(win, qtbot, tmp_path):
    """load_orders -> clean; load_orders does not finish while hold.txt exists. After a full run (#1), load_orders is
    run alone from a terminal (#2, carrying on from #1) and is still going: clean's menu names run #1, whose results
    are whole, not run #2."""
    waiting = ("import os, time\nfor _ in range(1200):  # (60 s at most)\n    if not os.path.exists('hold.txt'):\n"
               "        break\n    time.sleep(0.05)\nresult = 'orders'")
    path = write_flow(tmp_path, {"load_orders": python(waiting, (), ("result",)),
                                 "clean": python("result = orders", ("orders",), ("result",))},
                      ["manual.start -> load_orders.after", "load_orders.result -> clean.orders"])
    tab = win.open_flow(path)
    first = run_now(win, qtbot, tab)
    assert first.status == "success", first.log
    (tmp_path / "hold.txt").write_text("")
    terminal = start_elsewhere(win, path, "--only", "load_orders", "--inputs-from", first.run)
    try:
        second = wait_without_the_editor(lambda: running_run(win, path))
        wait_without_the_editor(lambda: (win.home.runs_dir / str(second) / "load_orders").is_dir())

        named = entry(win, tab, "clean", RUN_ONLY).text()

        (tmp_path / "hold.txt").unlink()
        terminal.wait(60)
    finally:
        stop_elsewhere(terminal)
    assert second == first.run + 1 and named.startswith(f"{RUN_ONLY} (run #{first.run} · "), named


@pytest.mark.invariant("RR-3")
def test_a_newer_run_of_another_line_is_not_the_run_a_block_takes(win, qtbot, tmp_path):
    """Two lines, each with a start block of its own: manual -> load_orders -> clean, and nightly -> archive -> tidy.
    The first line runs (#1), then the second (#2), each from a terminal. clean's menu names run #1: run #2, the
    flow's newest, holds nothing of clean's line."""
    path = write_flow(tmp_path, {
        "load_orders": python("result = 'orders'", (), ("result",)),
        "clean": python("result = orders", ("orders",), ("result",)),
        "nightly": {"type": "trigger.manual"},
        "archive": python("result = 'files'", (), ("result",)),
        "tidy": python("result = files", ("files",), ("result",)),
    }, ["manual.start -> load_orders.after", "load_orders.result -> clean.orders",
        "nightly.start -> archive.after", "archive.result -> tidy.files"])
    tab = win.open_flow(path)
    for start, last in (("manual", "clean"), ("nightly", "tidy")):
        terminal = start_elsewhere(win, path, "--trigger", start)
        try:
            assert terminal.wait(60) == 0
        finally:
            stop_elsewhere(terminal)
        assert eventually(qtbot, lambda: under(tab, last) != "", timeout=20_000), last  # the editor has seen the run

    named = entry(win, tab, "clean", RUN_ONLY).text()

    assert (tab.result_run("clean"), tab.result_run("tidy")) == (1, 2)
    assert named.startswith(f"{RUN_ONLY} (run #1 · "), named


def test_before_a_flow_has_run_a_block_shows_nothing_under_it_and_its_same_inputs_entries_cannot_be_chosen(win,
                                                                                                         tmp_path):
    tab = win.open_flow(orders_flow(tmp_path))

    assert under(tab, "clean") == "" and hover(tab, "clean") == ""
    for words in (RUN_ONLY, RUN_ON):
        same_inputs = entry(win, tab, "clean", words)
        assert same_inputs.text() == words and not same_inputs.isEnabled()  # (no run to name)


# --- RR-7: what is old, while a run is still going and on a Loop --------------------------------------------------------

@pytest.mark.invariant("RR-7")
def test_while_a_loop_rerun_alone_is_still_going_the_block_after_it_is_old_once_an_item_is_done(win, qtbot,
                                                                                                tmp_path):
    """manual -> make (two items) -> each (a Loop, one item at a time) -> report. In the full run the Loop fails, so
    report is skipped (the first item fails, the second is not run). The work fixed, the Loop is re-run alone; its
    second item does not finish while hold.txt exists. While that item still runs, the first is done in the new run,
    and report's result is marked old already, not only once the run has ended; hovering it names work, the block
    that is done, not the Loop, which is still running; and Data preview's label, with report selected, says so."""
    work = ("import os, time\nif not os.path.exists('fixed.txt'):\n    raise ValueError(f'no data for {item}')\n"
            "if index == 2:\n    for _ in range(1200):  # (60 s at most)\n"
            "        if not os.path.exists('hold.txt'):\n            break\n        time.sleep(0.05)\n"
            "output = item.upper()")
    tab = win.open_flow(per_item(tmp_path, ["a", "b"], work))
    full = run_now(win, qtbot, tab)
    assert full.status == "failed" and under(tab, "report").startswith("skipped"), (full.log, under(tab, "report"))
    (tmp_path / "fixed.txt").write_text("")
    (tmp_path / "hold.txt").write_text("")
    tab.scene.nodes["report"].setSelected(True)
    QApplication.processEvents()
    assert win.preview.label.text() == "report: skipped, no output"
    seen = {}

    def the_loop_alone_until_its_first_item_is_done():
        entry(win, tab, "each", RUN_ONLY).trigger()
        # (the Loop's counter: 1 of its 2 items done, 1 running)
        qtbot.waitUntil(lambda: tab.scene.progress.get("each") == (1, 2, 1), timeout=60_000)
        seen["report"], seen["hover"] = under(tab, "report"), hover(tab, "report")
        seen["label"] = win.preview.label.text()
        (tmp_path / "hold.txt").unlink()

    alone = do(win, qtbot, tab, the_loop_alone_until_its_first_item_is_done)

    assert alone.status == "success", alone.log
    assert seen["report"].startswith("old · skipped"), seen
    assert seen["hover"].startswith(f"Old: made before work ran again (run #{alone.run})."), seen
    assert seen["label"] == "report: skipped, no output (old)", seen


def loop_colour(tab, loop: str) -> dict:
    """As strongest_colour of tests/test_rerun_old_and_kept.py, for a Loop's box: how far from grey its border (along
    its bottom side) and the text under its blocks are drawn (0: black, white or a grey; green or red: over 100)."""
    node = tab.scene.nodes[loop]
    tab.scene.clearSelection()
    QApplication.processEvents()
    box = node.rect
    parts = {"border": QRectF(box.left() + box.width() * 0.3, box.bottom() - 2, box.width() * 0.4, 4),
             "text": QRectF(box.left() + 10, box.bottom() - FOOTER_H, box.width() * 0.6, FOOTER_H - 4)}
    found = {}
    for part, rect in parts.items():
        image = render(tab.scene, node.mapRectToScene(rect), 2.0)
        pixels = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())]
        found[part] = max(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) for c in pixels)
    return found


@pytest.mark.invariant("RR-7")
def test_an_old_loop_is_drawn_in_grey_with_a_grey_border(win, qtbot, tmp_path):
    """manual -> make (two items) -> each (a Loop) -> report. After a full run the Loop's border and the text under it
    are green; make re-run alone, the Loop's result is old and both are grey."""
    tab = win.open_flow(per_item(tmp_path, ["a", "b"], "output = item.upper()"))
    assert run_now(win, qtbot, tab).status == "success"
    fresh = loop_colour(tab, "each")
    assert choose(win, qtbot, tab, "make", RUN_ONLY).status == "success"

    old = loop_colour(tab, "each")

    assert under(tab, "each").startswith("old · "), under(tab, "each")
    assert fresh["border"] > 80 and fresh["text"] > 80, fresh  # (green: this way of looking tells a colour from grey)
    assert old["border"] < 50 and old["text"] < 50, old


@pytest.mark.invariant("RR-7")
def test_data_previews_label_of_the_block_selected_says_old_as_soon_as_the_block_before_it_has_run_again(win, qtbot,
                                                                                                      tmp_path):
    """clean stays selected. load_orders re-run alone: Data preview's label says clean's 10 rows are old, without
    selecting clean again. clean re-run alone: the label shows its 12 rows, no longer old."""
    tab, full = full_run(win, qtbot, tmp_path, rows=10)
    assert full.status == "success", full.log
    tab.scene.nodes["clean"].setSelected(True)
    QApplication.processEvents()
    label = win.preview.label.text
    assert label() == "clean.result: 10 rows × 3 columns"

    assert load_orders_alone(win, qtbot, tab, tmp_path, rows=12).status == "success"
    while_old = label()
    assert choose(win, qtbot, tab, "clean", RUN_ONLY).status == "success"

    assert while_old == "clean.result (old): 10 rows × 3 columns"
    assert label() == "clean.result: 12 rows × 3 columns"


@pytest.mark.invariant("RR-7")
def test_after_an_old_block_is_deleted_the_blocks_left_are_still_marked_and_unmarked(win, qtbot, tmp_path):
    """After load_orders ran again, clean, report, send and archive are old. archive is deleted from the canvas.
    clean re-run alone is no longer old, and report still is."""
    tab, *_ = after_load_orders_ran_again(win, qtbot, tmp_path)
    tab.scene.nodes["archive"].setSelected(True)
    tab.scene.delete_selected()
    QApplication.processEvents()
    assert "archive" not in tab.scene.nodes

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert clean.status == "success", clean.log
    assert "12 rows" in under(tab, "clean") and not under(tab, "clean").startswith("old"), under(tab, "clean")
    assert under(tab, "report").startswith("old · "), under(tab, "report")


@pytest.mark.invariant("RR-7")
def test_back_on_the_newest_run_from_run_history_what_was_old_is_marked_old_again(win, qtbot, tmp_path):
    """After load_orders ran again (run #2), the full run (#1) is opened from Run history: nothing is old. Then run #2
    is opened there, the newest: the canvas shows each block's latest result again, and clean's is old again."""
    tab, _, full, alone = after_load_orders_ran_again(win, qtbot, tmp_path)
    open_from_history(win, full.run)
    assert not under(tab, "clean").startswith("old"), under(tab, "clean")

    open_from_history(win, alone.run)

    assert "12 rows" in under(tab, "load_orders"), under(tab, "load_orders")
    assert under(tab, "clean").startswith("old · "), under(tab, "clean")
    assert under(tab, "send").startswith("old · ✕"), under(tab, "send")
    assert hover(tab, "clean").startswith(f"Old: made before load_orders ran again (run #{alone.run}).")


# --- RR-5: a Session's cookies go to its own requests only ---------------------------------------------------------------

@pytest.mark.invariant("RR-5")
def test_a_login_rerun_alone_passes_its_cookie_on_to_the_requests_of_its_own_session_only(env):
    """login and data are inside one Session, prefs, after login, inside another. After a full run, login is run alone
    (a new session cookie). data, run alone from that run, sends the new cookie, and its Log says where the cookies
    come from. prefs, run alone from that run, sends no session cookie, as in the full run: it is not in login's
    Session."""
    with LoginSite() as site:
        flow = flow_file(env.root / "flow.yaml", {
            "manual": MANUAL, "sess": session(0, 0), "other": session(900, 0),
            "login": api(site.url("/login"), 0), "data": api(site.url("/data"), 1),
            "prefs": api(site.url("/prefs"), 3, within="other"),
        }, ["manual.start -> login.after", "login.status -> data.after", "login.status -> prefs.after"])
        runs = [env.taskloom("run", flow), env.taskloom("run", flow, "--only", "login", "--inputs-from", "1"),
                env.taskloom("run", flow, "--only", "data", "--inputs-from", "2"),
                env.taskloom("run", flow, "--only", "prefs", "--inputs-from", "2")]

    assert [run.returncode for run in runs] == [0, 0, 0, 0], [run.stderr for run in runs]
    assert site.seen[-2] == ("/data", {"sessionid": site.issued[1]}) and len(site.issued) == 2, site.seen
    assert ("session sess's cookies come from run #2, as they were after the newest run of the blocks before data: "
            "sessionid") in runs[2].stderr, runs[2].stderr
    assert all("sessionid" not in cookies for path, cookies in site.seen if path == "/prefs"), site.seen
