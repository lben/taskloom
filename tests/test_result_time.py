"""The tooltip of a block's result (the line under the block on the canvas) ends, at its bottom right, with when that
result was made: "results from 2026-10-31 14:03:22", the local time the block finished in the run the result belongs
to. Through the real editor (offscreen), its real runner processes and its run history. Each test names the point of
the specification it protects:
1. every block type, every end (succeeded, failed, skipped, cancelled): the line, with the time that block finished in
   the run its result belongs to (a result kept from an earlier run: that run's; a run picked in Run history: that
   run's); no such line while the block runs, nor on "off · not connected to a start block";
2. the rest of the tooltip as before; the line smaller and grey, readable in the light and the dark theme;
3. the same time whether the result arrived live or was loaded from the history (reopening the flow, Run history).
Not on the connections' (ports', edges') tooltips; the text under the block is unchanged."""

import datetime as dt
import html
import os
import re
import sqlite3
from contextlib import closing

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFontInfo, QHelpEvent, QPalette, QTextDocument, QTextFormat  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolTip  # noqa: E402

from taskloom.history import History  # noqa: E402
from tests.test_editor import wait_done, write_flow  # noqa: E402
from tests.test_inspect_mermaid import DARK, LIGHT, contrast  # noqa: E402
from tests.test_lines import click_row, menu_action, run_elsewhere, start_from, wait_runs  # noqa: E402

FORMAT = "%Y-%m-%d %H:%M:%S"
WHEN = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d"
# The tooltip's colours in each theme (Qt's own light ones; dark ones as a dark system theme gives them).
THEMES = {"light": {**LIGHT, "ToolTipBase": "#ffffdc", "ToolTipText": "#000000"},
          "dark": {**DARK, "ToolTipBase": "#2b2d30", "ToolTipText": "#dfe1e5"}}


# --- helpers ---------------------------------------------------------------------------

def now() -> str:
    return dt.datetime.now().strftime(FORMAT)


def finished_at(win, run_id: int) -> dict:
    """When each block that has finished in run `run_id` finished, as the run history has it, to the second (local
    time)."""
    with closing(sqlite3.connect(win.home.history_db)) as db:
        rows = db.execute("SELECT block, finished FROM blocks WHERE run_id=? AND finished IS NOT NULL",
                          (run_id,)).fetchall()
    return {block: dt.datetime.fromisoformat(finished).strftime(FORMAT) for block, finished in rows}


def later_second(qtbot, than: str):
    """Let the clock pass the second `than` (YYYY-MM-DD HH:MM:SS): what finishes next has a later time."""
    qtbot.waitUntil(lambda: now() > than, timeout=3_000)


def tip_of(tab, block) -> str:
    """The tooltip of the line under `block` on the canvas."""
    return tab.scene.nodes[block].footer.toolTip()


def lines_of(tip: str) -> tuple[QTextDocument, list]:
    """The tooltip as Qt reads its HTML: the document, and its lines that show something, top to bottom."""
    doc = QTextDocument()
    doc.setDefaultFont(QToolTip.font())
    doc.setHtml(tip)
    lines, block = [], doc.begin()
    while block.isValid():
        if block.text().strip():
            lines.append(block)
        block = block.next()
    return doc, lines


def ending(tip: str) -> tuple[str, bool]:
    """The tooltip's last line, and whether it is aligned to the right (so at the tooltip's bottom right)."""
    doc, lines = lines_of(tip)
    if not lines:
        return "", False
    return lines[-1].text(), bool(lines[-1].blockFormat().alignment() & Qt.AlignmentFlag.AlignRight)


def endings(tab, blocks) -> dict:
    return {block: ending(tip_of(tab, block)) for block in blocks}


def results_from(times: dict) -> dict:
    """What each block's tooltip ends with, given when its result was made: {block: time}."""
    return {block: (f"results from {when}", True) for block, when in times.items()}


def without_time(tip: str) -> bool:
    doc, _ = lines_of(tip)
    return "results from" not in doc.toPlainText() and not re.search(WHEN, doc.toPlainText())


def pieces(lines) -> list[tuple[str, bool, tuple]]:
    """What the lines show, piece by piece, as Qt draws each: (text, bold, its font families)."""
    found = []
    for block in lines:
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.text().strip():
                fmt = fragment.charFormat()
                found.append((fragment.text(), fmt.font().bold(), tuple(fmt.fontFamilies() or ())))
            it += 1
    return found


def formats(block) -> list:
    """The formats of the pieces of text of a line."""
    found, it = [], block.begin()
    while not it.atEnd():
        if it.fragment().text().strip():
            found.append(it.fragment().charFormat())
        it += 1
    return found


def as_before(result: dict) -> str:
    """The tooltip of a block's result as the editor made it before this change, from the block's row in the history
    (the cases used here: succeeded with outputs, failed, skipped)."""
    if result["status"] == "success":
        return "".join(f"<b>{html.escape(port)}</b><pre>{html.escape(value, quote=False)}</pre>"
                       for port, value in result["previews"].items())
    if result["status"] == "failed":
        return f"<pre>{html.escape(result['error'], quote=False)}</pre>"
    assert result["status"] == "skipped" and result["summary"], result
    return html.escape(f"skipped: {result['summary']}")


def drawn_smaller(fmt) -> bool:
    """Text in format `fmt` is drawn smaller than the tooltip's own text (as Qt reads the HTML, not as laid out: the
    Windows runner's offscreen platform has no fonts)."""
    normal = QToolTip.font()
    if fmt.hasProperty(QTextFormat.FontPointSize):
        return fmt.fontPointSize() < (normal.pointSizeF() if normal.pointSizeF() > 0 else QFontInfo(normal).pointSizeF())
    if fmt.hasProperty(QTextFormat.FontPixelSize):
        return fmt.intProperty(QTextFormat.FontPixelSize) < (normal.pixelSize() if normal.pixelSize() > 0
                                                             else QFontInfo(normal).pixelSize())
    return fmt.intProperty(QTextFormat.FontSizeAdjustment) < 0


def drawn_colour(fmt, palette: QPalette) -> QColor:
    """The colour text in format `fmt` is drawn in on the tooltip's background (its own text colour if it has none)."""
    if fmt.foreground().style() == Qt.NoBrush:
        return palette.color(QPalette.ToolTipText)
    colour, base = fmt.foreground().color(), palette.color(QPalette.ToolTipBase)
    a = colour.alphaF()
    return QColor(*(round(a * c + (1 - a) * b) for c, b in zip(colour.getRgb()[:3], base.getRgb()[:3])))


def hovered_footer(qtbot, tab, block) -> str:
    """Rest the mouse on the line under `block` on the canvas, as you would: the text of the tooltip that shows."""
    QApplication.processEvents()  # an opened flow centres itself just after
    at = tab.scene.nodes[block].footer.sceneBoundingRect().center()
    tab.view.centerOn(at)  # scrolled into view: the canvas can be narrower than the flow (the Windows runner's)
    QApplication.processEvents()
    viewport = tab.view.viewport()
    pos = tab.view.mapFromScene(at)
    assert viewport.rect().contains(pos), pos
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, pos, viewport.mapToGlobal(pos)))

    def shown():
        return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)

    qtbot.waitUntil(lambda: shown() is not None, timeout=5_000)
    text = shown().text()
    QToolTip.hideText()
    return text


def pick_in_run_history(win, run_id: int):
    """Double-click run `run_id` in Run history, as you would: the canvas shows that run's results."""
    win.bottom_tabs.setCurrentWidget(win.history_panel)
    QApplication.processEvents()
    table = win.history_panel.table
    (row,) = [r for r in range(table.rowCount()) if table.item(r, 0).text() == str(run_id)]
    click_row(table, row, double=True)


# Every kind of block that runs without a server, and every way a block ends but cancelled: succeeded (a start block,
# a database query, an Inspect, a report, a file written, an If), skipped (the If's path not taken, and after a
# failure) and failed; and a block connected to no start block (off).
EVERY_KIND = """
    blocks:
      manual:
        type: trigger.manual
      load:
        type: data.duckdb_sql
        config: {inputs: [], sql: "select 'north' as region, 3 as total"}
      look:
        type: logic.inspect
      page:
        type: reports.html_template
      save:
        type: data.write_file
        config: {path: out/totals.csv}
      check:
        type: logic.if
        config: {condition: "1 > 2"}
      never:
        type: logic.python
        config: {inputs: [value], outputs: [], code: "pass"}
      broken:
        type: logic.python
        config: {inputs: [], outputs: [], code: "raise ValueError('the report source is missing')"}
      after_broken:
        type: logic.python
        config: {inputs: [], outputs: [], code: "pass"}
      spare:
        type: logic.python
        config: {inputs: [], outputs: [], code: "pass"}
    edges:
      - manual.start -> load.after
      - load.result -> look.value
      - load.result -> page.data
      - load.result -> save.table
      - manual.start -> check.after
      - check.true -> never.value
      - manual.start -> broken.after
      - broken.done -> after_broken.after
"""
ENDS = {"manual": "success", "load": "success", "look": "success", "page": "success", "save": "success",
        "check": "success", "never": "skipped", "broken": "failed", "after_broken": "skipped"}


# --- 1 and 3: the time each block's result was made --------------------------------------

def test_after_run_now_each_blocks_result_tooltip_ends_with_when_it_finished_at_the_bottom_right(win, qtbot, tmp_path):
    """Spec 1 (the reported case) and 3: Run now, then rest the mouse on the line under a block; for every block that
    took part, whatever its type and however it ended, its tooltip ends with 'results from <the local time it
    finished in that run>' aligned to the right; choosing that run in Run history shows the same times."""
    tab = win.open_flow(write_flow(tmp_path / "kinds.yaml", EVERY_KIND))
    before = now()
    win.run_flow(tab)
    run_id = wait_runs(qtbot, tab, 1)
    after = now()
    assert {block: tab.scene.nodes[block].status for block in ENDS} == ENDS
    times = finished_at(win, run_id)
    assert set(times) == set(ENDS) and all(before <= t <= after for t in times.values()), (before, times, after)

    assert endings(tab, ENDS) == results_from(times)
    shown = hovered_footer(qtbot, tab, "load")  # the tooltip you see
    assert shown == tip_of(tab, "load") and ending(shown) == (f"results from {times['load']}", True), shown

    pick_in_run_history(win, run_id)  # the same run, now loaded from the history

    assert endings(tab, ENDS) == results_from(times)


def test_while_a_block_runs_and_on_a_block_that_is_off_there_is_no_time_and_a_finished_one_shows_its_own(
        win, qtbot, tmp_path):
    """Spec 1 (while running; off) and the non-goals: during a run, a block that has finished shows its time as it
    arrives, the block running shows none, nor does a block connected to no start block; the text under the blocks
    and the connections' tooltips carry no time."""
    tab = win.open_flow(write_flow(tmp_path / "going.yaml", """
        blocks:
          manual:
            type: trigger.manual
          load:
            type: logic.python
            config: {inputs: [], code: "output = 42"}
          hold:
            type: logic.python
            config:
              inputs: [value]
              outputs: []
              code: |
                import os, time
                while not os.path.exists('go.txt'):
                    time.sleep(0.05)
          spare:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - manual.start -> load.after
          - load.output -> hold.value
    """))
    assert tab.scene.nodes["spare"].status == "off" and without_time(tip_of(tab, "spare"))
    win.run_flow(tab)
    try:
        qtbot.waitUntil(lambda: tab.scene.nodes["hold"].status == "running", timeout=60_000)
        run_id = tab.runs[-1].run_id

        assert tab.scene.nodes["load"].status == "success"
        assert endings(tab, ["load"]) == results_from({"load": finished_at(win, run_id)["load"]})
        assert without_time(tip_of(tab, "hold")) and without_time(tip_of(tab, "spare")), (
            tip_of(tab, "hold"), tip_of(tab, "spare"))
    finally:
        (tmp_path / "go.txt").write_text("", encoding="utf-8")  # the run ends, whatever was found
        wait_done(qtbot, tab)
    assert tab.last["status"] == "success"
    times = finished_at(win, run_id)
    assert endings(tab, ["manual", "load", "hold"]) == results_from(times)
    assert tab.scene.nodes["spare"].status == "off" and without_time(tip_of(tab, "spare"))
    for block, node in tab.scene.nodes.items():  # the text under the block is unchanged
        assert "results from" not in node.status_text and not re.search(WHEN, node.status_text), (block, node.status_text)
    connections = [port.toolTip() for node in tab.scene.nodes.values() for port in node.ports.values()]
    connections += [edge.toolTip() for edge in tab.scene.edge_items]
    assert connections and not [tip for tip in connections if "results from" in tip], connections


def test_stop_cancels_a_busy_block_and_its_tooltip_ends_with_when_it_was_cancelled(win, qtbot, tmp_path):
    """Spec 1 (any other end): a block cancelled by Stop shows when it ended in that run, as the start block before it
    shows when it finished."""
    tab = win.open_flow(write_flow(tmp_path / "busy.yaml", """
        blocks:
          manual:
            type: trigger.manual
          busy:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                import os, time
                while not os.path.exists('never.txt'):
                    time.sleep(0.05)
        edges:
          - manual.start -> busy.after
    """))
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.scene.nodes["busy"].status == "running", timeout=60_000)
    win.stop(tab)
    wait_done(qtbot, tab, timeout=30_000)
    assert tab.last["status"] == "cancelled" and tab.scene.nodes["busy"].status == "cancelled"

    assert endings(tab, ["manual", "busy"]) == results_from(finished_at(win, tab.last["id"]))


RERUN_FLOW = """
    blocks:
      manual:
        type: trigger.manual
      load:
        type: logic.python
        config: {inputs: [], code: "output = 21"}
      compute:
        type: logic.python
        config: {inputs: [input], outputs: [], code: "pass"}
      nightly:
        type: trigger.schedule
        config: {cron: "0 2 * * *"}
      job:
        type: logic.python
        config: {inputs: [], code: "output = 'nightly'"}
    edges:
      - manual.start -> load.after
      - load.output -> compute.input
      - nightly.start -> job.after
"""


def test_a_result_kept_from_an_earlier_run_shows_that_runs_time_and_a_run_picked_in_run_history_its_own(
        win, qtbot, tmp_path):
    """Spec 1 (kept results; Run history; a Schedule start) and 3: after Run now, the Schedule line run from its start
    block and Run only one block, each block shows the time it finished in the run its result comes from; choosing
    an older run in Run history shows that run's times, and choosing the newest shows the kept results' times again."""
    tab = win.open_flow(write_flow(tmp_path / "kept.yaml", RERUN_FLOW))
    win.run_flow(tab)
    first = wait_runs(qtbot, tab, 1)
    first_times = finished_at(win, first)
    later_second(qtbot, max(first_times.values()))
    start_from(win, tab, "nightly")  # Run the flow now, as if its time had come
    nightly = wait_runs(qtbot, tab, 2)
    nightly_times = finished_at(win, nightly)
    later_second(qtbot, max(nightly_times.values()))
    menu_action(win, tab, "compute", "Run only this block, with the same inputs").trigger()
    only = wait_runs(qtbot, tab, 3)
    only_times = finished_at(win, only)
    assert tab.runs[-1].status == "success" and set(only_times) == {"compute"}
    assert only_times["compute"] > first_times["compute"]
    kept = {"manual": first_times["manual"], "load": first_times["load"], "compute": only_times["compute"],
            "nightly": nightly_times["nightly"], "job": nightly_times["job"]}

    assert endings(tab, kept) == results_from(kept)

    pick_in_run_history(win, first)  # an older run, on purpose: only its results
    assert endings(tab, ["manual", "load", "compute"]) == results_from(first_times)
    assert not tip_of(tab, "nightly") and not tip_of(tab, "job")

    pick_in_run_history(win, only)  # the newest: each block's latest result again
    assert endings(tab, kept) == results_from(kept)


def test_a_flow_opened_after_a_run_elsewhere_shows_the_times_that_run_finished_its_blocks(win, qtbot, tmp_path):
    """Spec 3 (reopening the flow): a run made while the flow was closed (as the scheduler makes them) is shown when
    the flow opens, each block's tooltip ending with the time it finished in that run; Run history shows the same."""
    flow = write_flow(tmp_path / "elsewhere.yaml", """
        blocks:
          manual:
            type: trigger.manual
          load:
            type: logic.python
            config: {inputs: [], code: "output = {'region': 'north', 'total': 3}"}
          look:
            type: logic.inspect
        edges:
          - manual.start -> load.after
          - load.output -> look.value
    """)
    win.close_tab(win.open_flow(flow))  # you saw it: no runs yet
    run_id = run_elsewhere(win, flow)
    times = finished_at(win, run_id)
    assert set(times) == {"manual", "load", "look"}

    tab = win.open_flow(flow)

    assert {block: tab.scene.nodes[block].status for block in times} == dict.fromkeys(times, "success")
    assert endings(tab, times) == results_from(times)
    pick_in_run_history(win, run_id)
    assert endings(tab, times) == results_from(times)


# --- 2: the rest of the tooltip as before; the line smaller, grey, readable in light and dark ------------

@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_time_is_a_smaller_grey_line_readable_in_the_theme_and_the_rest_of_the_tooltip_is_as_before(
        win, qtbot, tmp_path, theme):
    """Spec 2: in the light and the dark theme (the application's and the tooltips' palette, as the system gives
    them), above the time a block's tooltip shows what it showed before (its outputs laid out as before, its error,
    why it was skipped), and the time line is drawn smaller than the rest, in a grey dimmer than the tooltip's text
    and readable on its background (a contrast of at least 3:1)."""
    palette = QPalette()
    for role, value in THEMES[theme].items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    original, original_tips = QApplication.palette(), QToolTip.palette()
    QApplication.setPalette(palette)
    QToolTip.setPalette(palette)
    QApplication.processEvents()
    try:
        tab = win.open_flow(write_flow(tmp_path / "styled.yaml", """
            blocks:
              manual:
                type: trigger.manual
              load:
                type: logic.python
                config: {inputs: [], code: "output = {'region': 'north', 'total': 3}"}
              broken:
                type: logic.python
                config: {inputs: [], outputs: [], code: "raise ValueError('the report source is missing')"}
              after_broken:
                type: logic.python
                config: {inputs: [], outputs: [], code: "pass"}
            edges:
              - manual.start -> load.after
              - manual.start -> broken.after
              - broken.done -> after_broken.after
        """))
        win.run_flow(tab)
        run_id = wait_runs(qtbot, tab, 1)
        times = finished_at(win, run_id)
        history = History(win.home.history_db)
        try:
            results = history.blocks(run_id)
        finally:
            history.close()
        assert {b: results[b]["status"] for b in ("load", "broken", "after_broken")} == {
            "load": "success", "broken": "failed", "after_broken": "skipped"}

        tips = QToolTip.palette()
        base, text = tips.color(QPalette.ToolTipBase), tips.color(QPalette.ToolTipText)
        assert (base.lightness() < 128) == (theme == "dark"), base.name()
        for block in ("load", "broken", "after_broken"):
            tip = tip_of(tab, block)
            doc, lines = lines_of(tip)
            *rest, last = lines
            assert (last.text(), bool(last.blockFormat().alignment() & Qt.AlignmentFlag.AlignRight)) == (
                f"results from {times[block]}", True), (block, tip)
            _, before = lines_of(as_before(results[block]))
            assert pieces(rest) == pieces(before), (block, tip)
            for fmt in formats(last):
                colour = drawn_colour(fmt, tips)
                assert drawn_smaller(fmt), (block, "the time is not smaller than the rest", tip)
                assert colour.hsvSaturationF() <= 0.2, (block, f"{colour.name()} is not grey", tip)
                assert contrast(colour, base) < contrast(text, base), (
                    block, f"{colour.name()} is not dimmer than the tooltip's text {text.name()}", tip)
                assert contrast(colour, base) >= 3, (
                    block, f"{colour.name()} on {base.name()}: contrast {contrast(colour, base):.2f}", tip)
    finally:
        QApplication.setPalette(original)
        QToolTip.setPalette(original_tips)
        QApplication.processEvents()
