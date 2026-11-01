"""Proof: a block's result tooltip ends with when that result was made, “results from” and the date and time (stage 3
of the 2026-10-30 feedback). How proofs are written: README.md here."""

import re
from collections import Counter

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QHelpEvent, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QToolBar, QToolTip

from proofs.conftest import choose, menu_item, right_click
from taskloom.history import History
from tests.test_editor import write_flow
from tests.test_inspect_mermaid import contrast
from tests.test_lines import click_row, wait_runs
from tests.test_paths_editor_editing import labelled, press
from tests.test_result_time import (as_before, drawn_colour, drawn_smaller, endings, finished_at, formats,
                                    later_second, lines_of, pieces, results_from)

ASKED = ("Lets add at the bottom right of the block->result at the bottom -> on mouse hover tooltip message, at the "
         "bottom right of that tooltip lets add the %Y-%m-%d date and hour:minute:seconds for all blocks. It should "
         "read something like: 'results from [date and time]'")
NAME = "daily-sales.yaml"
FLOW = """
    blocks:
      start:
        type: trigger.manual
        ui: {x: 0, y: 0}
      load_sales:
        type: logic.python
        config: {inputs: [], code: "output = {'North': 120, 'South': 80, 'East': 45}"}
        ui: {x: 240, y: 0}
      wait_a_moment:
        type: logic.wait
        config: {seconds: 8}
        ui: {x: 480, y: 0}
      total_sales:
        type: logic.python
        config: {inputs: [input], code: "output = f'{sum(input.values())} orders in all, from {len(input)} regions'"}
        ui: {x: 720, y: 0}
      check_archive:
        type: logic.python
        config: {inputs: [], outputs: [], code: "raise RuntimeError('cannot reach the archive folder')"}
        ui: {x: 240, y: 200}
      send_summary:
        type: logic.python
        config: {inputs: [], outputs: [], code: "pass"}
        ui: {x: 480, y: 200}
      old_notes:
        type: logic.python
        config: {inputs: [], outputs: [], code: "pass"}
        ui: {x: 240, y: 380}
    edges:
      - start.start -> load_sales.after
      - load_sales.output -> wait_a_moment.value
      - wait_a_moment.value -> total_sales.input
      - start.start -> check_archive.after
      - check_archive.done -> send_summary.after
"""
SALES_LINE = ["start", "load_sales", "wait_a_moment", "total_sales"]
ARCHIVE_LINE = ["check_archive", "send_summary"]
RAN = SALES_LINE + ARCHIVE_LINE  # the blocks a run of start takes part in; old_notes is in none
ENDED = {**dict.fromkeys(SALES_LINE, "success"), "check_archive": "failed", "send_summary": "skipped"}  # in each run
TOTAL = "245 orders in all, from 3 regions"
FAILED = "RuntimeError: cannot reach the archive folder (line 1)"
SKIPPED = "skipped: something before it failed"
INK = 48  # a pixel this much off the tooltip's background (in red, green or blue) is part of its text


# --- helpers -------------------------------------------------------------------------------------------------------

def tooltip():
    """The tooltip on the screen, if one shows."""
    return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)


def hover(qtbot, tab, block):
    """Rest the mouse on the line under `block` on the canvas, as you would: the tooltip once it shows that line's."""
    QApplication.processEvents()  # an opened flow centres itself just after
    footer = tab.scene.nodes[block].footer
    viewport = tab.view.viewport()
    at = tab.view.mapFromScene(footer.sceneBoundingRect().center())
    if not viewport.rect().contains(at):  # scrolled into view first (a canvas narrower than the flow)
        tab.view.centerOn(footer)
        QApplication.processEvents()
        at = tab.view.mapFromScene(footer.sceneBoundingRect().center())
    assert viewport.rect().contains(at), f"the line under {block} is out of view"
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, at, viewport.mapToGlobal(at)))
    qtbot.waitUntil(lambda: tooltip() is not None and tooltip().text() == footer.toolTip(), timeout=5_000)
    QTest.qWait(100)
    return tooltip()


def read(text: str) -> dict:
    """What a tooltip says, as Qt reads its HTML, in the tooltips' colours now: its lines that show something; whether
    the last is aligned right, and in smaller grey letters, dimmer than the rest (and their contrast on its
    background); the pieces of the lines above (text, bold, font)."""
    doc, lines = lines_of(text)  # (the lines are the document's: kept while read)
    tips = QToolTip.palette()
    base, ink = tips.color(QPalette.ToolTipBase), tips.color(QPalette.ToolTipText)
    last = formats(lines[-1]) if lines else []
    colours = [drawn_colour(fmt, tips) for fmt in last]
    return {"lines": [line.text() for line in lines],
            "right": bool(lines) and bool(lines[-1].blockFormat().alignment() & Qt.AlignmentFlag.AlignRight),
            "small_grey": bool(last) and all(drawn_smaller(fmt) and c.hsvSaturationF() <= 0.2
                                             and contrast(c, base) < contrast(ink, base)
                                             for fmt, c in zip(last, colours)),
            "contrast": min((contrast(c, base) for c in colours), default=0.0),
            "above": pieces(lines[:-1])}


def before_this_change(win, run_id: int, block: str) -> list:
    """The pieces of the tooltip the editor made, before this change, of `block`'s result in run `run_id` (from the
    run history), as `read` gives those of the lines above the time."""
    history = History(win.home.history_db)
    try:
        result = history.blocks(run_id)[block]
    finally:
        history.close()
    doc, lines = lines_of(as_before(result))
    return pieces(lines)


def drawn(win, tip) -> dict:
    """Where the tooltip draws its text, as found in what it draws, in the window's pixels: its "last" line and the
    lines "above" it; and its "background" colour."""
    image = tip.grab().toImage()
    ratio = image.devicePixelRatio()
    w, h = image.width(), image.height()
    back = Counter(image.pixel(x, y) for y in range(0, h, 2) for x in range(0, w, 2)).most_common(1)[0][0]
    back = image.pixelColor(*next((x, y) for y in range(h) for x in range(w) if image.pixel(x, y) == back))
    edge = round(2 * ratio)  # the tooltip's frame

    def ink(x, y):
        c = image.pixelColor(x, y)
        return max(abs(c.red() - back.red()), abs(c.green() - back.green()), abs(c.blue() - back.blue())) >= INK

    rows = {y: [x for x in range(edge, w - edge) if ink(x, y)] for y in range(edge, h - edge)}
    rows = {y: xs for y, xs in rows.items() if xs}
    assert rows, "the tooltip shows no text"
    ys = sorted(rows)
    top = ys[-1]
    while top - 1 in rows:  # up to the gap above the last line
        top -= 1

    def rect(lines) -> QRect:
        if not lines:
            return QRect()
        xs = [x for y in lines for x in rows[y]]
        found = QRect(QPoint(int(min(xs) / ratio), int(min(lines) / ratio)),
                      QPoint(int(max(xs) / ratio), int(max(lines) / ratio)))
        return found.translated(tip.mapToGlobal(QPoint(0, 0)) - win.mapToGlobal(QPoint(0, 0)))

    last = [y for y in ys if y >= top]
    strongest = max(contrast(image.pixelColor(x, y), back) for y in last for x in rows[y])
    return {"last": rect(last), "above": rect([y for y in ys if y < top]), "background": back,
            "strongest": strongest}


def at_bottom_right(seen: dict) -> bool:
    """The last line is drawn below the rest of the tooltip's text, and as far right as any of it."""
    last, above = seen["last"], seen["above"]
    return above.isNull() or (last.top() > above.bottom() and last.right() >= above.right() - 3)


def menu_action(win, menu_name: str, text: str):
    menu = next(a.menu() for a in win.menuBar().actions() if a.text() == menu_name)
    return next(a for a in menu.actions() if a.text() == text)


def click_tab(win, panel):
    """Click the tab of a panel under the canvas (Log, Data preview, Run history)."""
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.bottom_tabs.indexOf(panel)).center())


def lit(tip) -> str:
    """The time a tooltip ends with ("" if none)."""
    said = read(tip.text())["lines"]
    return said[-1].removeprefix("results from ") if said and said[-1].startswith("results from ") else ""


# `env` before `win`: its Taskloom folder is the editor's (the same temporary one), and it is made first.
def test_a_blocks_result_tooltip_ends_with_when_that_result_was_made(proof, env, win, qtbot, monkeypatch):
    p = proof("A block's result tooltip ends with when that result was made", ASKED)
    p.idea("Rest the mouse on the line under a block, and the tooltip of its result now ends, at its bottom right, "
           "with when that result was made: “results from 2026-10-31 19:00:17”, in small grey letters. Every block "
           "has it, whatever its kind and however it ended: succeeded, failed, skipped or stopped with Stop.")
    p.idea("The time is when the block finished, in the run its result comes from, to the second, in your "
           "computer's time. A block that keeps its result from an earlier run shows that run's time; a run you "
           "choose in Run history, or one shown when you open the flow, shows the times its blocks finished in it, "
           "the same as when those results arrived.")
    p.idea("Above it, the tooltip is as before: the block's outputs, its error, or why it was skipped. A block that "
           "is still running, or that is off (no start block leads to it), has no result yet, and no such line.")
    p.why_simplest("Each run already records, in the run history, the moment each block finished: the run sends it "
                   "to the editor with the block's result, and writes the very same moment into the run history. "
                   "The tooltip shows that moment. Nothing new is recorded or kept.")
    p.why_simplest("So there is one rule, wherever a result comes from: its tooltip ends with the time it was made. "
                   "Results that arrive as a run goes, results kept from an earlier run, a run chosen in Run history "
                   "and a flow you open all follow it, with no case of their own.")
    p.why_simplest("The line is added after the rest of the tooltip, which is made just as before.")
    p.alternative("The time the run started, as Run history and “Results of this block…” show it",
                  "A run can take minutes, its blocks finishing one after another, and a block run again on its own "
                  "is in a run of its own. When the block finished is when its result was made, which is what the "
                  "line says.")
    p.alternative("The time the editor received the result",
                  "A run made while the flow was closed (by the scheduler service, at night), or one chosen in Run "
                  "history, would show when you looked at it, not when its results were made.")
    p.alternative("The time on the canvas, in the line under the block",
                  "That line is short and already holds the result (✓ and what it made, ✕ and the error). A date "
                  "and time under every block would crowd the canvas. You asked for it in the tooltip, at its "
                  "bottom right.")
    p.alternative("How long ago (“5 minutes ago”)",
                  "You asked for the date and the time. A “how long ago” is out of date as soon as it shows.")
    p.fits("It goes with “each block shows its latest result”: the time is that result's own, so a line of blocks "
           "whose results were kept says when they were made, not when another line ran.")
    p.fits("The date and time are written as Run history and “Results of this block…” write them (year-month-day, "
           "hours:minutes:seconds), so they are easy to match with a run there.")
    p.fits("The line is a middle grey on every theme (Tools > Settings > Theme): it can be read on the light tooltips "
           "and on the dark ones.")
    monkeypatch.setenv("APPDATA", str(env.root / "AppData"))  # Settings' Save writes a script there on Windows
    assert win.home.root == env.home, "the editor and the command line use the same Taskloom folder"
    env.env["PYTHONIOENCODING"] = "utf-8"  # what taskloom run prints, as a terminal shows it (on Windows too)
    flow = write_flow(env.root / NAME, FLOW)
    run_now = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))

    def shoot_tip(tip, block: str, caption: str):
        """The window with the tooltip of `block` over it, its last line outlined; close up, it and the block."""
        seen = drawn(win, tip)
        line = seen["last"].adjusted(-4, -3, 4, 3)  # (the outline's glow clear of the letters)
        p.shot(tip, caption, over=win, highlight=[line], zoom=[tip, block])
        return seen

    # --- the flow, and a block that is off -------------------------------------------------------------------------
    p.step(f"Open the flow {NAME} (File > Open…). In line ①, start runs load_sales, which counts the orders of three "
           "regions; wait_a_moment, a Wait block, waits 8 seconds; then total_sales adds them up. In line ②, start "
           "also runs check_archive, set up to fail, and send_summary after it. ③ old_notes is connected to no start "
           "block.")
    menu_action(win, "&File", "Open…").trigger()
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    p.shot(win, "Line ① and line ② start at start; ③ old_notes is off: no start block leads to it. Nothing has run "
                "yet.", highlight=[SALES_LINE, ARCHIVE_LINE, "old_notes"], badges=True,
           zoom=[SALES_LINE, ARCHIVE_LINE, "old_notes"])

    p.step("Rest the mouse on the last line of old_notes, “off · not connected to a start block”.")
    tip = hover(qtbot, tab, "old_notes")
    said = read(tip.text())["lines"]
    p.shot(tip, "Its tooltip says why it is off, and nothing more: no “results from”, as old_notes has no result.",
           over=win, highlight=[tip], zoom=[tip, "old_notes"])
    tip.hide()
    p.check("old_notes is off, and its tooltip has no “results from” line, nor any date or time",
            tab.scene.nodes["old_notes"].status == "off" and said and said[0].startswith("No start block leads")
            and lit(tip) == "" and not any("results from" in line for line in said))

    # --- run 1: while a block runs, then each way a block ends --------------------------------------------------
    p.step("Press ▶ Run now in the toolbar, and while wait_a_moment is still running, rest the mouse on its last "
           "line, “running”.")
    QTest.mouseClick(run_now, Qt.LeftButton)
    qtbot.waitUntil(lambda: tab.scene.nodes["wait_a_moment"].status == "running", timeout=60_000)
    tip = hover(qtbot, tab, "wait_a_moment")
    said = read(tip.text())["lines"]
    p.shot(tip, "wait_a_moment is running: its tooltip says just that, “running”, with no “results from” line. Its "
                "result is not made yet.", over=win, highlight=[tip], zoom=[tip, "wait_a_moment"])
    tip.hide()
    p.check("wait_a_moment was still running when its tooltip was shot", tab.scene.nodes["wait_a_moment"].status
            == "running" and tab.running)
    p.check("Its tooltip says “running”, and has no “results from” line", said == ["running"])
    p.note("A block still running when its run process stops unexpectedly (say the computer shuts down in the middle "
           "of a run) shows that it failed, “the run process stopped unexpectedly”, with no “results from” line: "
           "the run history has no time it finished, and the editor does not invent one. (Not shown here.)")

    p.step("Wait for the run to end.")
    first = wait_runs(qtbot, tab, 1)
    times = finished_at(win, first)
    live = endings(tab, RAN)  # each tooltip's last line, as the results arrived from the run
    p.shot(win, f"Run #{first} ended. ① start, load_sales, wait_a_moment and total_sales succeeded: green. ② "
                "check_archive failed (red), so send_summary, after it, was skipped (grey). ③ old_notes is still "
                "off.", highlight=[SALES_LINE, ARCHIVE_LINE, "old_notes"], badges=True,
           zoom=[SALES_LINE, ARCHIVE_LINE, "old_notes"])
    p.check("① The four blocks of line ① succeeded; ② check_archive failed and send_summary was skipped",
            {b: tab.scene.nodes[b].status for b in RAN} == ENDED)
    p.check(f"The tooltip of each of these six blocks ends, aligned right, with “results from” and the time that "
            f"block finished in run #{first}, as the run history has it", live == results_from(times))
    p.check("③ old_notes, off, still has no such line", tab.scene.nodes["old_notes"].status == "off"
            and "results from" not in tab.scene.nodes["old_notes"].footer.toolTip())

    for block, how, above, caption in (
            ("total_sales", "which succeeded", ["output", TOTAL],
             f"total_sales' tooltip shows its output, as before: “output”, then {TOTAL}. Under it, outlined, at its "
             "bottom right, in small grey letters: results from {when}."),
            ("check_archive", "which failed", [FAILED],
             f"check_archive's tooltip shows its error, as before: {FAILED}. Under it, outlined, at its bottom "
             "right: results from {when}."),
            ("send_summary", "which was skipped", [SKIPPED],
             "send_summary's tooltip says why it was skipped, as before: something before it failed. Under it, "
             "outlined, at its bottom right: results from {when}.")):
        p.step(f"Rest the mouse on the last line of {block}, {how}.")
        tip = hover(qtbot, tab, block)
        said, when = read(tip.text()), times[block]
        seen = shoot_tip(tip, block, caption.format(when=when))
        tip.hide()
        p.check(f"Above the time, the tooltip is as before this change: {' / '.join(above)}",
                said["lines"][:-1] == above and said["above"] == before_this_change(win, first, block))
        p.check(f"It ends with “results from {when}”: when {block} finished in run #{first}, as the run history "
                "has it", said["lines"][-1] == f"results from {when}")
        p.check("That line is aligned right, and drawn at the bottom right: below the rest, as far right as any of "
                "it", said["right"] and at_bottom_right(seen))
        p.check("It is in smaller letters than the rest, in grey, dimmer than the rest", said["small_grey"])
    p.note("To tell that the rest of each tooltip is unchanged, the proof lays out the tooltip as the editor made it "
           "before this change, from the same result in the run history, and compares the two, line by line, bold "
           "included. (This proof does not run the old version.)")

    # --- run 2: total_sales again, the blocks before it keep their results -----------------------------------
    p.step("Right-click total_sales and choose “Run from this block on, with the same inputs”. Only total_sales runs "
           "again, with what wait_a_moment gave it in run #1; the blocks before it are not run again.")
    later_second(qtbot, max(times.values()))  # so that its new result is from a later second than run #1's

    def run_from_total_sales(menu):
        item = "Run from this block on, with the same inputs"
        p.shot(menu, "The menu of total_sales, with the item to choose.", over=win,
               highlight=[menu_item(menu, item)], zoom=[menu, "total_sales"])
        choose(menu, item)

    p.while_open(lambda: right_click(tab, "total_sales"), run_from_total_sales)
    second = wait_runs(qtbot, tab, 2)
    again = finished_at(win, second)
    p.check(f"Run #{second} ran total_sales alone, and it succeeded",
            set(again) == {"total_sales"} and tab.runs[-1].status == "success")

    p.step("Rest the mouse on the last line of load_sales, then on that of total_sales.")
    tip = hover(qtbot, tab, "load_sales")
    kept = lit(tip)
    shoot_tip(tip, "load_sales", f"load_sales did not run again: it keeps its result from run #{first}, and its "
                                 f"tooltip still ends with results from {kept}, when it made it: its own time, "
                                 "earlier than total_sales' in that run, which came after wait_a_moment's 8 seconds.")
    tip.hide()
    p.check(f"load_sales' tooltip ends with “results from {times['load_sales']}”, its time in run #{first}",
            kept == times["load_sales"])
    tip = hover(qtbot, tab, "total_sales")
    new = lit(tip)
    shoot_tip(tip, "total_sales", f"total_sales ran again: its tooltip now ends with results from {new}, when it "
                                  f"finished in run #{second}, later than in run #{first} ({times['total_sales']}).")
    tip.hide()
    p.check(f"total_sales' tooltip ends with “results from {again['total_sales']}”, its time in run #{second}, "
            f"later than in run #{first}", new == again["total_sales"] > times["total_sales"])
    p.check(f"The other blocks before it, and line ②, all keep their results and times from run #{first}",
            endings(tab, [b for b in RAN if b != "total_sales"])
            == results_from({b: t for b, t in times.items() if b != "total_sales"}))

    # --- an older run, chosen in Run history ----------------------------------------------------------------------
    p.step(f"Click the Run history tab, under the canvas, and double-click run #{first}, the older run.")
    click_tab(win, win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 2, timeout=10_000)
    (row,) = [r for r in range(table.rowCount()) if table.item(r, 0).text() == str(first)]
    p.shot(win, f"Run history: run #{second}, from total_sales on, and ① run #{first}, started by start, the one "
                "double-clicked.", highlight=[[table.item(row, 0), table.item(row, 4)]], badges=True,
           zoom=[table.item(0, 0), table.item(1, 4)])
    p.check(f"Run history lists run #{second}, started from total_sales, and run #{first}, started by start",
            [[table.item(r, c).text() for c in (0, 2)] for r in range(2)]
            == [[str(second), "from total_sales"], [str(first), "start"]])
    click_row(table, row, double=True)
    qtbot.waitUntil(lambda: win.statusBar().currentMessage().startswith(f"Showing run #{first} "), timeout=10_000)

    p.step("Rest the mouse on the last line of total_sales.")
    tip = hover(qtbot, tab, "total_sales")
    shown = lit(tip)
    shoot_tip(tip, "total_sales", f"The canvas shows run #{first}, read from the run history: total_sales ends "
                                  f"with results from {shown} again, its time in run #{first}, the same as when that "
                                  "result arrived.")
    tip.hide()
    p.check(f"total_sales' tooltip ends with “results from {times['total_sales']}”, its time in run #{first}",
            shown == times["total_sales"])
    p.check(f"Every block of run #{first} ends its tooltip with the very line it had when its result arrived",
            endings(tab, RAN) == live)

    # --- a run made while the flow is closed, and the flow opened again --------------------------------------------
    p.step(f"Close the flow (File > Close flow), then run it from a terminal, in its folder: taskloom run {NAME}.")
    p.note("A flow opens showing the runs you have not seen yet: one you have seen opens as you left it, without "
           "results, as before this change. So the run to see when it opens is one made while it was closed, as the "
           "scheduler service makes them: here from a terminal, with the command the service runs flows with.")
    menu_action(win, "&File", "Close flow").trigger()
    p.check("The flow is closed", all(t.doc.path != flow.resolve() for t in win.tabs_list()))
    result = env.taskloom("run", NAME, encoding="utf-8", errors="replace")
    printed = (result.stdout + result.stderr).strip()  # (a run's lines go to stderr)
    third, _ = env.last_run()
    made = finished_at(win, third)
    ends = [line for line in printed.splitlines()
            if re.match(r"\d\d:\d\d:\d\d (run #|\[\w+\] (success|failed|skipped))", line)]
    p.text(f"taskloom run {NAME} printed, as the run started, as each block finished, and as the run ended (its "
           "other lines, such as when each block started and check_archive's traceback, are left out here)",
           "\n".join(ends))
    p.check(f"Run #{third} ran the six blocks; as each finished, the command printed the time, and the run history "
            "has the same times", set(made) == set(RAN)
            and all(f"{made[b][11:]} [{b}] {ENDED[b]}" in printed for b in RAN))

    p.step("Open the flow again (File > Open…).")
    menu_action(win, "&File", "Open…").trigger()
    tab = win.current
    qtbot.waitUntil(lambda: tab.scene.nodes["total_sales"].status == "success", timeout=10_000)
    p.shot(win, f"The flow opens showing run #{third}, which you have not seen yet: the same results as in run "
                f"#{first}, made by the run in the terminal.", highlight=[SALES_LINE, ARCHIVE_LINE],
           zoom=[SALES_LINE, ARCHIVE_LINE])
    p.check(f"Each block of run #{third} shows its result from that run",
            {b: (tab.block_info.get(b) or {}).get("run_id") for b in RAN} == dict.fromkeys(RAN, third))
    p.check(f"Every block of run #{third} ends its tooltip with the time it finished in that run",
            endings(tab, RAN) == results_from(made))

    p.step("Rest the mouse on the last line of total_sales.")
    tip = hover(qtbot, tab, "total_sales")
    shown = lit(tip)
    shoot_tip(tip, "total_sales", f"total_sales ends with results from {shown}: the date, and the time taskloom run "
                                  "printed as total_sales finished.")
    tip.hide()
    p.check(f"total_sales' tooltip ends with “results from {made['total_sales']}”: the date, and the time the "
            "command printed for it", shown == made["total_sales"])

    # --- dark -------------------------------------------------------------------------------------------------------
    p.step("Choose Settings… in the Tools menu, set Theme to Dark, and press Save.")
    try:
        def to_dark(dialog):
            row = labelled(dialog, "Theme")
            row.setCurrentIndex(row.findText("Dark"))
            p.shot(dialog, "① Theme: Dark, about to be saved.", highlight=[row], badges=True)
            press(dialog, "Save")

        p.while_open(menu_action(win, "&Tools", "Settings…").trigger, to_dark)
        QTest.qWait(200)
        p.check("The editor is dark now, its tooltips too",
                win.palette().color(QPalette.Window).lightness() < 128
                and QToolTip.palette().color(QPalette.ToolTipBase).lightness() < 128)

        p.step("Rest the mouse on the last line of total_sales again.")
        tip = hover(qtbot, tab, "total_sales")
        said = read(tip.text())
        seen = shoot_tip(tip, "total_sales", "In the dark theme, the tooltip is dark, and results from "
                                             f"{made['total_sales']} is a middle grey on it, which can be read.")
        tip.hide()
        p.check("The tooltip is drawn dark", seen["background"].lightness() < 128)
        p.check(f"It still ends, at its bottom right, with “results from {made['total_sales']}”",
                said["lines"][-1] == f"results from {made['total_sales']}" and said["right"] and at_bottom_right(seen))
        p.check(f"Its grey stands out from the dark background by {said['contrast']:.1f} to 1 (as drawn, at its "
                f"strongest, {seen['strongest']:.1f} to 1): at least 3 to 1, the least Taskloom's themes give their "
                "grey hints", said["contrast"] >= 3 and seen["strongest"] >= 3)
        p.note("To be exact: 3 to 1 is what WCAG, the web's guide to readable text, asks of large text; of small text "
               "it asks 4.5 to 1. The line is a hint, dimmer than the rest of the tooltip on purpose, as Taskloom's "
               "other grey hints are. The proof sets the Theme back to System at its end, as it found it.")
    finally:
        win.settings.setValue("theme", "System")  # as Settings' Save keeps it
        win.apply_theme()
        QApplication.processEvents()
