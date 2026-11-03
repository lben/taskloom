"""Proof: the Loop, a box on the canvas whose blocks run once per item, up to At the same time items at once, with a
progress bar and [item i/N] Log lines; the results come back in the items' order (stage 8 of the 2026-10-30
feedback). How proofs are written: README.md here."""

import datetime as dt
import re

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

from proofs.test_proof_block_icons import list_row
from proofs.test_proof_canvas import drag_block, from_edit_menu, open_flow
from proofs.test_proof_docs_help import in_window
from proofs.test_proof_python_reusable_block import item_rect
from proofs.test_proof_result_time import hover as hover_result
from proofs.test_proof_run_output import setting, type_into
from proofs.test_proof_run_scope import press
from proofs.test_proof_scheduler_start import click_empty
from proofs.test_proof_tab_plus import set_theme
from taskloom.editor import theme
from taskloom.editor.canvas import HEADER_H, NODE_W
from taskloom.history import History
from tests.test_block_icons import GROUP_COLORS, has_icon, ink, pixels, rows, unlike, unlike_listed
from tests.test_inspect_mermaid import contrast
from tests.test_loop_container import (bar_area, beside, box, box_texts, drawn, for_each_drawing, how_it_looks,
                                       progress, render, slack)
from tests.test_paths_editor_editing import clean, drag_over_and_drop, dragged_from_the_tree, flow_file, settle
from tests.test_theme import most_common

ASKED = "for_each block is meant to run flows? Remind me how its design was decided"
LOGIC = GROUP_COLORS["Logic"]
BLOCKS_WIDTH, INSPECTOR_WIDTH, LOG_HEIGHT = 180, 420, 330  # a wider canvas, and a taller Log
LOOP = "each_region"  # the Loop of the flow that runs
INSIDE = ["load", "total"]
REGIONS = ["north", "south", "east", "west", "central"]  # items 1 to 5
SECONDS = {"north": 5.5, "south": 0.5, "east": 0.5, "west": 3.5, "central": 0.5}  # how long load takes for each
SALES = {"north": [120, 80], "south": [45, 30], "east": [60], "west": [25, 25, 50], "central": [90]}
TOTALS = {region: sum(sales) for region, sales in SALES.items()}
MONTH = "2026-10"
REPORTED = ", ".join(f"{region} {TOTALS[region]}" for region in REGIONS)
# The moment shot while it runs: 2 items done of 5 (south and east), 2 running (north and west); with SECONDS, from
# about 1 second into the Loop to 4.5.
MIDWAY = (2, 5, 2)
TICK = dt.timedelta(seconds=0.016)  # a clock tick on Windows: two times taken in a row may differ by this much
SOON = dt.timedelta(seconds=0.3)  # "at once": well under the half second the quickest item takes
MOVE = QPointF(-150, 80)  # how far the box is dragged, in the flow's points


def python(inputs: list[str], code: str, x: int, y: int) -> dict:
    return {"type": "logic.python", "config": {"inputs": inputs, "code": code}, "ui": {"x": x, "y": y}}


LOAD = (f"import time\nSECONDS = {SECONDS!r}\nSALES = {SALES!r}\nprint('loading', region, 'for', month)\n"
        "time.sleep(SECONDS[region])\nprint('loaded', region)\noutput = {'region': region, 'sales': SALES[region]}")
TOTAL = "output = {'region': input['region'], 'total': sum(input['sales'])}"
REPORT = "output = ', '.join(f\"{r['region']} {r['total']}\" for r in results)"


def blocks_to_try(folder):
    """load and total, connected, apart: the middle of the canvas is free for the Loop."""
    return flow_file(folder / "try-the-loop.yaml", {
        "load": python(["region", "month"], LOAD, -480, 60), "total": python(["input"], TOTAL, 380, 60),
    }, ["load.output -> total.input"])


def regions_flow(folder):
    """start_by_hand -> regions -> each_region (a Loop, as it comes: At the same time not set) -> report; inside
    each_region: load (the item, and the month from pick_month, outside the box) -> total -> each_region's result."""
    return flow_file(folder / "regions.yaml", {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 60}},
        "regions": python([], f"output = {REGIONS!r}", 230, 0),
        "pick_month": python([], f"output = {MONTH!r}", 230, 170),
        LOOP: {"type": "logic.loop", "config": {}, "ui": {"x": 460, "y": 0, "height": 220}},
        "load": {**python(["region", "month"], LOAD, 540, 60), "inside": LOOP},
        "total": {**python(["input"], TOTAL, 794, 60), "inside": LOOP},
        "report": python(["results"], REPORT, 1100, 60),
    }, ["start_by_hand.start -> regions.after", "regions.output -> each_region.items", "each_region.item -> load.region",
        "pick_month.output -> load.month", "load.output -> total.input", "total.output -> each_region.result",
        "each_region.results -> report.results"])


# --- on the canvas ------------------------------------------------------------------------------------------------

def make_room(win):
    """Drag the Blocks list's right edge and the panels' left edge in, and the Log's top edge up."""
    win.resizeDocks([win.blocks_dock, win.inspector], [BLOCKS_WIDTH, INSPECTOR_WIDTH], Qt.Horizontal)
    win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)
    QTest.qWait(100)


def top_of(tab, loop: str) -> QPointF:
    """A spot on the Loop's top bar, where you click or grab the box (in the flow's points)."""
    r = tab.scene.nodes[loop].drawn_rect()
    return QPointF(r.center().x(), r.top() + HEADER_H / 2)


def click_at(tab, at: QPointF):
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, tab.view.mapFromScene(at))
    settle()


def drag_scene(tab, a: QPointF, b: QPointF):
    """Press the mouse at `a`, move it to `b` and let go (in the flow's points), as you would."""
    viewport, start, end = tab.view.viewport(), tab.view.mapFromScene(a), tab.view.mapFromScene(b)
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
    for i in range(1, 6):
        QTest.mouseMove(viewport, start + (end - start) * i / 5)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
    settle()


def on_canvas(win, tab, area: QRectF) -> QRect:
    """`area` of the canvas (in the flow's points), in the window's pixels."""
    return in_window(win, tab.view.viewport(), tab.view.mapFromScene(area).boundingRect())


def top_bar(win, tab, loop: str) -> QRect:
    """The Loop's top bar, with its name, its counter and its progress bar, in the window's pixels."""
    r = tab.scene.nodes[loop].drawn_rect()
    return on_canvas(win, tab, QRectF(r.left(), r.top(), r.width(), HEADER_H + 8))


def inside_of(tab, block: str):
    """The Loop the flow, as it would be saved, has the block inside (None: none)."""
    return tab.doc.blocks[block].get("inside")


def spot(tab, block: str) -> tuple[int, int]:
    """Where the flow, as it would be saved, has the block (its top-left corner, in the flow's points)."""
    ui = tab.doc.blocks[block]["ui"]
    return ui["x"], ui["y"]


def box_of(tab, loop: str) -> QRectF:
    """Where the canvas draws the Loop's box (in the flow's points)."""
    return tab.scene.nodes[loop].drawn_rect()


def holds(tab, loop: str, block: str) -> bool:
    """Whether the Loop's box, where the canvas draws it, holds the whole block."""
    return box_of(tab, loop).contains(drawn(tab, block))


def plain_box_problems(tab, loop: str) -> list[str]:
    """What is wrong with the look of the Loop's box before it runs (no counter yet): its background not grey or hardly
    unlike the canvas, no shadow darker than both beside it (measured as the test of this change measures them)."""
    rect, background = box(tab, loop)
    rgb, problems = background.getRgb()[:3], []
    inner = box_of(tab, loop).adjusted(1, 1, -1, -1)  # (inside its outline)
    if any(abs(a - b) > 2 for a, b in zip(rect.getRect(), inner.getRect())):
        problems.append(f"its grey fills {rect.getRect()}, not the whole box {inner.getRect()}")
    if max(rgb) - min(rgb) > 20:
        problems.append(f"its background {background.name()} is not grey")
    canvas = most_common(render(tab.scene, QRectF(round(rect.right()) + 60, round(rect.top()), 40,
                                                  round(rect.height()))))
    if abs(background.lightness() - canvas.lightness()) < 8:
        problems.append(f"its background {background.name()} hardly differs from the canvas {canvas.name()}")
    shadow = beside(tab, rect, loop)
    if shadow > background.lightness() - 8 or shadow > canvas.lightness() - 4:
        problems.append(f"no darker shadow beside it ({shadow:.0f})")
    return problems


def name_and_icon(tab, loop: str) -> tuple[bool, bool]:
    """Whether the box shows its name at its top; and whether the icon at its top left is the Loop's (as the test of
    this change tells it: less than 0.2 unlike the approved drawing, a list with a going-round arrow)."""
    rect, _ = box(tab, loop)
    named = any(loop in t["text"] and t["center"][1] < rect.top() + 40 for t in box_texts(tab, loop))
    top = tab.scene.nodes[loop].sceneBoundingRect().top()
    icon = ink(pixels(render(tab.scene, QRectF(rect.left() - 4, top, 60, rect.top() + 48 - top), 2.0)), LOGIC)
    return named, has_icon(icon, 2.0) and unlike(icon, for_each_drawing()) < 0.2


def the_bar(tab, loop: str) -> tuple[float, QColor, QColor]:
    """The Loop's progress bar as the canvas paints it: how much of it is filled (0 to 1), the colour of its filled
    part (near its left end) and of its empty part (near its right end)."""
    area, background = bar_area(tab, loop)
    rgb = pixels(render(tab.scene, area))
    off = np.abs(rgb - background.getRgb()[:3]).max(axis=2) > 24
    row = int(off.sum(axis=1).argmax())  # the bar: the row most unlike the box
    track = rgb[row][off[row]]
    filled, empty = track[len(track) // 10], track[-len(track) // 10]
    share = float((np.abs(track - filled).max(axis=1) <= 24).mean())
    return share, QColor(*map(int, filled)), QColor(*map(int, empty))


def shown_progress(tab, loop: str = LOOP):
    """(done, of, running) as the Loop's box has it in the run on screen."""
    return tab.scene.nodes[loop].progress


def reaches(qtbot, condition, seconds: float = 30) -> bool:
    """Whether `condition()` came true within `seconds`."""
    try:
        qtbot.waitUntil(condition, timeout=int(seconds * 1000))
    except qtbot.TimeoutError:
        return False
    return True


def run_ended(qtbot, tab) -> int:
    qtbot.waitUntil(lambda: not tab.running and tab.runs[-1].status is not None, timeout=60_000)
    QTest.qWait(300)  # what the run printed last reaches the Log
    return tab.runs[-1].run_id


# --- the Log and the run history ------------------------------------------------------------------------------------

def loaded_order(log) -> list[str]:
    """The regions in the order the Log says load was done with them."""
    return re.findall(r"\] loaded (\w+)", log.toPlainText())


def item_spans(win, run_id: int) -> dict[int, tuple[dt.datetime, dt.datetime]]:
    """{item number: when load began it and when it was done with it}, from that item's Log lines as the run history
    keeps them."""
    kept = History(win.home.history_db)
    try:
        lines = kept.logs(run_id)
    finally:
        kept.close()
    found = {}
    for line in lines:
        m = re.match(r"\[item (\d)/5\] (loading|loaded) ", line["message"] or "")
        if m and line["block"] == "load":
            found.setdefault(int(m.group(1)), {})[m.group(2)] = dt.datetime.fromisoformat(line["ts"])
    return {k: (said["loading"], said["loaded"]) for k, said in found.items() if len(said) == 2}


def most_at_once(spans: list[tuple[dt.datetime, dt.datetime]]) -> int:
    """The most of these that were going on at the same moment (for longer than a clock tick)."""
    inner = [(a + TICK, b - TICK) for a, b in spans]
    return max((sum(a <= t < b for a, b in inner) for t, _ in inner), default=0)


def timetable(spans: dict[int, tuple[dt.datetime, dt.datetime]]) -> str:
    zero = min(a for a, _ in spans.values())
    return "\n".join(f"[item {k}/5] {REGIONS[k - 1]:8} {(a - zero).total_seconds():5.2f} s  to "
                     f"{(b - zero).total_seconds():5.2f} s" for k, (a, b) in sorted(spans.items()))


def total_row(win, run_id: int) -> dict:
    """What the run history keeps of total in that run."""
    kept = History(win.home.history_db)
    try:
        return kept.blocks(run_id)["total"]
    finally:
        kept.close()


def test_the_blocks_inside_a_loop_run_once_per_item_several_at_the_same_time(
        proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("The Loop: a box on the canvas whose blocks run once per item", ASKED)
    p.idea("For Each, which ran another flow file once per item, is gone. In its place is the Loop: a box you draw "
           "on the canvas around a few blocks. Those blocks run once for every item of a list, up to At the same time "
           "items at once, and the Loop gives back every item's result in the list's order.")
    p.idea("As you decided: a loop drawn on the canvas replaces For Each's other flow file. It is a box around a few "
           "blocks, showing its name, a progress bar and a done/N counter, grey with a darker shadow, and it must read "
           "well in Dark. Blocks outside it may connect into it. Its setting is called At the same time, 1 as it comes, "
           "and it is a sliding window: as soon as any item ends, the next starts. The results keep the list's order, "
           "each Log line is tagged with its item, and the progress reads “done/N · k running”. When an item fails, no "
           "new item starts, the running ones finish, then the Loop fails and lists the failed items; running it again "
           "keeps the items that succeeded.")
    p.idea("Add the Loop from the Blocks list (under Logic), and drag blocks into its box; drag one out and it is no "
           "longer inside. The box grows to hold its blocks, moving it moves them, and deleting it leaves them on the "
           "canvas. It is grey with a darker shadow, with its icon and name at its top.")
    p.idea("The list arrives at its items, on its left side. Inside the box, item gives each run of the blocks its "
           "item, and index its place in the list (1 to N); what you connect to result, inside on the right, is what "
           "that item gives back. results, outside on the right, is every item's result in the items' order, not the "
           "order they finished in. Blocks outside the box may connect into blocks inside it: each item gets its own "
           "copy of what they give.")
    p.idea("At the same time says how many items run at once; it is 1 as the Loop comes. As soon as one item ends, the "
           "next starts. While it runs, the box shows a progress bar and “done/N · k running”, a block inside shows "
           "running while any item is in it, and each of its Log lines says its item, as [item 2/5]. The Loop writes a "
           "line as it starts (“5 items, 2 at the same time”) and one as it ends. Afterwards each block inside says "
           "how its items ended (✓ 5), and resting the mouse on it shows the result of the last item to end, naming "
           "it.")
    p.idea("If an item fails (a block inside fails, and no error output inside the box handles it), no new item "
           "starts, the items running finish, and the Loop fails, listing the failed items. Once you have fixed the "
           "cause, right-click the Loop and choose Run from this block on: it runs again only the items that failed "
           "or did not run, the others keeping their results. This proof shows a run in which every item succeeds.")
    p.why_simplest("The blocks inside are ordinary blocks, on the same canvas, with their settings, ports, Log and "
                   "results as anywhere else. The Loop only says which blocks it runs and how many items at once. Each "
                   "item runs them as a small flow of its own, by the rules a flow's blocks always follow: in the "
                   "flow's order, a failure stops the blocks after it, an error output is the way out.")
    p.why_simplest("The flow file says one thing more about a block in a box: which Loop it is inside. The box's place "
                   "and size are kept like any block's place.")
    p.why_simplest("At the same time is a sliding window: a slow item holds up only its own place, not the others. "
                   "Each result goes back to its item's place in the list, so their order never depends on which item "
                   "was quicker.")
    p.alternative("Keep For Each, running another flow file once per item",
                  "What runs per item was out of sight, in another file, and each item became a run of its own, with "
                  "its own Log and its own row in Run history. You chose to replace it: the box shows on one canvas "
                  "what runs per item, and the whole Loop is one run.")
    p.alternative("A Loop start block and a Loop end block, with the blocks to repeat between them",
                  "What is repeated would be whatever lies between the two, worked out from the connections: nothing "
                  "on the canvas would show it at a glance, and one connection more could pull a block in. A box "
                  "shows exactly what is inside, which is what you agreed on.")
    p.alternative("A connection back to an earlier block, to go round again",
                  "The flow would have a circle in it. Taskloom works out the order to run blocks in from connections "
                  "that never lead back (Validate refuses a circle), and when such a circle stopped would be hidden "
                  "in its blocks' code. A Loop goes round a known number of times: once per item.")
    p.alternative("Several items in one round: start N, wait for all of them, then the next N",
                  "One slow item would hold up its whole round. You chose the sliding window: as soon as any item "
                  "ends, the next starts. Rounds of several items together are not in this release.")
    p.alternative("The results in the order the items finished",
                  "The block after the Loop would get them in a different order from one run to the next, depending "
                  "on which item was quicker. In the items' order, the first result is always the first item's.")
    p.fits("It runs like a Splitter's lines: each item gets its own copy of what it receives, so no item can change "
           "another's data; Stop cancels every running item at once; Validate (F7) refuses, before any run, a "
           "connection from a block inside the box to one outside it other than through result, and names it.")
    p.fits("Run from this block on, on the Loop, is the one Taskloom already had; the Loop adds only that the items "
           "that succeeded in the run you carry on from keep their results from it.")
    p.fits("Its icon is the one you approved for it, a list with a going-round arrow, white on the Logic purple, as "
           "For Each had. Its grey is worked out from the canvas's own colour, a step lighter in Dark and a step "
           "darker in Light, so the box stands out in either Theme.")
    p.fits("It is in the Blocks list, in Help > Documentation and in the hover help like any block. Properties shows "
           "its one setting, At the same time; a Loop has no Retry or Timeout of its own (set them on the blocks "
           "inside it).")

    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings writes Windows' Startup script there)
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    log = win.log.text
    try:
        # --- 1. In the Blocks list ------------------------------------------------------------------------------------
        p.step("Look at the Blocks list, on the left, under Logic.")
        tree = win.palette_panel.tree
        logic = rows(win.palette_panel)["Logic"]
        folder = logic[0].parent()
        tree.scrollToItem(logic[-1])
        tree.scrollToItem(folder)
        QApplication.processEvents()
        loop_row = next((item for item in logic if item.text(0) == "Loop"), None)
        p.check("The Blocks list has Loop under Logic", loop_row is not None)
        p.shot(win, "Under Logic: ① Loop, with its icon, a list with a going-round arrow. For Each is no longer there.",
               highlight=[list_row(win, loop_row)], badges=True,
               zoom=[item_rect(win, folder), item_rect(win, logic[-1])])
        listed = [item.text(0) for items in rows(win.palette_panel).values() for item in items]
        p.check("For Each is in none of the Blocks list's groups", "For Each" not in listed)
        like = {item.text(0): unlike_listed(item, LOGIC, {"loop": for_each_drawing()})["loop"] for item in logic}
        p.check("① Of the Logic blocks' icons in the list, Loop's is the one most like the drawing you approved for it",
                min(like, key=like.get) == "Loop")

        # --- 2. The box -------------------------------------------------------------------------------------------
        p.step("Open the flow try-the-loop.yaml (File > Open…). It has two Python Code blocks, load and total, load "
               "connected to total, with room between them.")
        p.note("For this proof, the Blocks list and the panels on the right are made narrower and the Log taller (their "
               "edges dragged), so that the canvas is wider and the Log shows a whole run.")
        make_room(win)
        tab = open_flow(win, monkeypatch, blocks_to_try(tmp_path))
        settle()
        p.check("The flow opens", tab is not None and set(tab.doc.blocks) == set(INSIDE))

        p.step("Drag Loop from the Blocks list and drop it between load and total.")
        dropped = drag_over_and_drop(tab, dragged_from_the_tree(loop_row), tab.view.mapFromScene(QPointF(0, 30)))
        loops = [b for b, spec in tab.doc.blocks.items() if spec["type"] == "logic.loop"]
        p.check("The canvas takes it: one Loop, named loop", dropped and loops == ["loop"])
        p.shot(win, "The new Loop: a grey box with a darker shadow, its icon and its name, loop, at its top. Its "
                    "counter and progress bar appear when it runs.", zoom=["loop"])
        named, its_icon = name_and_icon(tab, "loop")
        own_size = box_of(tab, "loop").size()  # as it comes
        p.check("Its name is at its top, and at its top left is the Loop's icon, as in the Blocks list",
                named and its_icon)

        p.step("Drag load into the box, then total, beside it.")
        r = tab.scene.nodes["loop"].drawn_rect()
        before = {b: spot(tab, b) for b in INSIDE}
        drag_block(tab, "load", QPointF(r.left() + 90, r.top() + 50))
        drag_block(tab, "total", QPointF(r.left() + 90 + NODE_W + 70, r.top() + 50))
        settle()
        p.shot(win, "① load and total are inside the box, and the box grew to hold them both. Their connection is "
                    "kept.", highlight=["loop"], badges=True, zoom=["loop"])
        p.check("① load and total are inside the Loop: the flow, saved, would say so",
                [inside_of(tab, b) for b in INSIDE] == ["loop", "loop"])
        p.check("① The box, as the canvas paints it, holds both of them whole, and grew wider to do so",
                all(holds(tab, "loop", b) for b in INSIDE) and box_of(tab, "loop").width() > own_size.width() + 20)
        p.check("① Their connection is kept", "load.output -> total.input" in tab.doc.data["edges"])
        problems = plain_box_problems(tab, "loop")  # (now that no connection crosses its empty part)
        p.check("① The box is grey, unlike the canvas, with a shadow darker than both beside it"
                + (f" (not: {'; '.join(problems)})" if problems else ""), problems == [])
        dropped_at = {b: spot(tab, b) for b in INSIDE}

        p.step("Choose Edit > Undo, then Edit > Redo.")
        from_edit_menu(p, win, "Undo")
        settle()
        p.shot(win, "After Undo: ① total is back where it was, outside the box; ② the box is the size it came in "
                    "again, with load still inside.", highlight=["total", "loop"], badges=True, zoom=["loop", "total"])
        p.check("① After Undo, total is no longer inside the Loop and is back where it was; ② load is still inside",
                inside_of(tab, "total") is None and spot(tab, "total") == before["total"]
                and not holds(tab, "loop", "total") and inside_of(tab, "load") == "loop")
        p.check("② The box is the size it came in again", box_of(tab, "loop").size() == own_size)
        from_edit_menu(p, win, "Redo")
        settle()
        p.check("After Redo, total is inside the box again, where you dropped it",
                inside_of(tab, "total") == "loop" and spot(tab, "total") == dropped_at["total"]
                and holds(tab, "loop", "total"))

        p.step(f"Drag the box by its top bar {-MOVE.x():g} points to the left and {MOVE.y():g} down.")
        start = tab.scene.nodes["loop"].drawn_rect()
        area = on_canvas(win, tab, start.united(start.translated(MOVE)).adjusted(-16, -16, 24, 24))  # both places
        p.shot(win, "Before: the box, with load and total inside it.", zoom=[area])
        box_before = box_of(tab, "loop")
        drag_scene(tab, top_of(tab, "loop"), top_of(tab, "loop") + MOVE)
        box_after = box_of(tab, "loop")
        moved = (round(box_after.left() - box_before.left()), round(box_after.top() - box_before.top()))
        shifted = {b: (spot(tab, b)[0] - dropped_at[b][0], spot(tab, b)[1] - dropped_at[b][1]) for b in INSIDE}
        p.shot(win, "After, in the same close-up: ① the box moved, and load and total moved with it, still inside.",
               highlight=["loop"], badges=True, zoom=[area])
        p.check(f"① The box moved by {moved[0]}, {moved[1]} points (about {MOVE.x():g}, {MOVE.y():g}), and kept its size",
                abs(moved[0] - MOVE.x()) <= slack(tab) and abs(moved[1] - MOVE.y()) <= slack(tab)
                and box_after.size() == box_before.size())
        p.check("① load and total moved by the same distance, and are still inside it: "
                + ", ".join(f"{b} {dx}, {dy}" for b, (dx, dy) in shifted.items()),
                all(abs(dx - moved[0]) <= 2 and abs(dy - moved[1]) <= 2 for dx, dy in shifted.values())
                and all(inside_of(tab, b) == "loop" and holds(tab, "loop", b) for b in INSIDE))

        p.step("Drag total out of the box, to its right.")
        p.note("load, inside the box, is still connected to total, now outside it: the canvas keeps the connection as "
               "you drew it. A block inside a Loop may connect out of it only through the Loop's result, so Validate "
               "(F7) would refuse this one before any run, naming it.")
        r = tab.scene.nodes["loop"].drawn_rect()
        drag_block(tab, "total", QPointF(r.right() + 60, r.top() + 50))
        p.shot(win, "① total is out of the box, no longer inside the Loop, and the box is the size it came in again; "
                    "② load stays inside.", highlight=["total", "load"], badges=True, zoom=["loop", "total"])
        p.check("① total is no longer inside the Loop, nor in its box, which is the size it came in again; ② load "
                "still is inside", inside_of(tab, "total") is None and not holds(tab, "loop", "total")
                and box_of(tab, "loop").size() == own_size and inside_of(tab, "load") == "loop"
                and holds(tab, "loop", "load"))

        p.step("Click the box's top bar and choose Edit > Delete.")
        click_at(tab, top_of(tab, "loop"))
        p.check("Clicking its top bar selects the Loop", tab.scene.selected_block_ids() == ["loop"])
        from_edit_menu(p, win, "Delete")
        settle()
        p.shot(win, "The Loop is gone; ① load and total are still on the canvas, with their connection.",
               highlight=[INSIDE], badges=True, zoom=INSIDE)
        p.check("① The Loop is deleted; load and total are still there, inside no Loop, still connected",
                "loop" not in tab.doc.blocks and set(INSIDE) <= set(tab.scene.nodes)
                and all(inside_of(tab, b) is None for b in INSIDE)
                and "load.output -> total.input" in tab.doc.data["edges"])

        p.step("Choose Edit > Undo.")
        from_edit_menu(p, win, "Undo")
        settle()
        p.shot(win, "① One Undo brings the box back, with load inside it as before.", highlight=["loop"], badges=True,
               zoom=["loop", "total"])
        p.check("① The Loop is back, with load inside it and total outside, as before Delete",
                "loop" in tab.scene.nodes and inside_of(tab, "load") == "loop" and holds(tab, "loop", "load")
                and inside_of(tab, "total") is None)
        clean(win)

        # --- 3. A run -------------------------------------------------------------------------------------------------
        p.step("Open the flow regions.yaml (File > Open…). Its Manual Start, start_by_hand, starts regions, which gives "
               "five regions to the Loop each_region. Inside it, load takes the region and the month (from pick_month, "
               "outside the box), and total adds up the region's sales into the Loop's result. report, after the Loop, "
               "writes one line from the results.")
        p.note(f"load waits as if it were loading the region's sales: {SECONDS['north']:g} seconds for north, "
               f"{SECONDS['west']:g} for west, half a second for the others. So the items end in another order than "
               "the list's.")
        tab = open_flow(win, monkeypatch, regions_flow(tmp_path))
        settle()
        p.check("The flow opens", tab is not None and LOOP in tab.scene.nodes)
        p.shot(win, "The flow, left to right: the Manual Start start_by_hand, connected to regions (pick_month is under "
                    f"it); the Loop {LOOP}, with load and total inside, and the connection from pick_month going into "
                    "the box, to load; report, after the Loop.",
               zoom=["start_by_hand", "regions", "pick_month", LOOP, "report"])

        p.step(f"Click {LOOP}'s top bar, and type 2 in its At the same time, in Properties.")
        click_at(tab, top_of(tab, LOOP))
        label, same_time = setting(win, "At the same time")
        as_it_came = same_time.text() or same_time.placeholderText()
        type_into(same_time, "2")
        label, same_time = setting(win, "At the same time")
        p.check("At the same time showed 1, as the Loop comes", as_it_came == "1")
        p.shot(win, "① In Properties, the Loop's one setting, At the same time: 2.",
               highlight=[[label, same_time]], badges=True, zoom=[label, same_time])
        p.check("① At the same time says 2, and the Loop keeps it",
                same_time.text() == "2" and tab.doc.blocks[LOOP]["config"].get("at_the_same_time") == 2)
        p.note("Several items in one round (chunks) are not in this release: At the same time is a sliding window.")

        p.step("Click an empty part of the canvas, and press ▶ Run now.")
        click_empty(tab)
        press(win, win.run_action)
        p.check("The run comes to the moment shown below: 2 of 5 items done, 2 running",
                reaches(qtbot, lambda: shown_progress(tab) == MIDWAY))
        painted = progress(tab, 5, LOOP)
        filled, _, _ = the_bar(tab, LOOP)
        problems = how_it_looks(tab, LOOP)
        running = tab.scene.nodes["load"].status, tab.item_runs.get("load")
        p.shot(win, "While it runs: ① the box's counter says 2/5 · 2 running, and its progress bar is two fifths "
                    "full; ② load shows running: north and west are in it now.",
               highlight=[top_bar(win, tab, LOOP), "load"], badges=True, zoom=[LOOP])
        p.check("① The box painted “2/5 · 2 running”, and still had it once the picture was taken",
                painted == (2, 2) and shown_progress(tab) == MIDWAY)
        p.check(f"① Its progress bar is {filled:.0%} full: 2 of 5 is 40%", abs(filled - 0.4) <= 0.06)
        p.check("① The box is grey, unlike the canvas, with a darker shadow beside it, and its name and counter read "
                "well: a contrast of at least 4.5 to 1 with what is behind them"
                + (f" (not: {'; '.join(problems)})" if problems else ""), problems == [])
        p.check("② load shows as running, for items 1 and 4 (north and west)",
                running == ("running", {"[item 1/5]", "[item 4/5]"}))
        lines = [(log, "5 items, 2 at the same time"), [(log, f"[item {k}/5] ") for k in range(1, 5)]]
        p.shot(win, "The Log at that moment: the Loop's first line, “5 items, 2 at the same time”, outlined on its own; "
                    "under it, outlined together, load's lines: each says its item, and the month it got from "
                    "pick_month. North (item 1) is still loading; south and east (items 2 and 3) have ended, and west "
                    "(item 4) has begun.",
               highlight=lines, zoom=[(log, f"run #{tab.runs[-1].run_id} · "), lines[0], *lines[1]])
        text = log.toPlainText()
        p.check("The Log was taken at the same moment: still 2 of 5 done, 2 running", shown_progress(tab) == MIDWAY)
        p.check("The Log says “5 items, 2 at the same time”", "[each_region] INFO 5 items, 2 at the same time" in text)
        p.check(f"Each of load's lines says its item; each “loading” line says “for {MONTH}”",
                all(re.search(r"\[load\] INFO \[item \d/5\] (loaded \w+$|loading \w+ for " + MONTH + "$)", line)
                    for line in text.splitlines() if "[load]" in line))

        first = run_ended(qtbot, tab)
        p.check(f"Run #{first} succeeded", tab.runs[-1].status == "success")
        reported = tab.scene.values.get("report", {}).get("output", "")
        p.shot(win, f"After the run: ① the box says 5/5 done, its bar full; ② load and total each say ✓ 5: their five "
                    f"items succeeded; ③ report made “{REPORTED}”, in the list's order.",
               highlight=[LOOP, INSIDE, "report"], badges=True, zoom=[LOOP, "report"])
        p.check("① The box shows all 5 items done", shown_progress(tab) == (5, 5, 0)
                and re.search(r"5\s*/\s*5 done", " ".join(t["text"] for t in box_texts(tab, LOOP))))
        p.check("② load and total each show ✓ 5", [tab.scene.nodes[b].status_text for b in INSIDE] == ["✓ 5", "✓ 5"])
        p.check(f"③ report made “{REPORTED}”: the results in the list's order", REPORTED in reported)

        ended = loaded_order(log)
        ends = [(log, f"loaded {region}") for region in ended]
        order = [f"{region} (item {REGIONS.index(region) + 1})" for region in ended]
        p.shot(win, "The Log after the run: the outlined lines, top to bottom, show the order in which load was done "
                    f"with the regions: {', '.join(order[:-1])} and, last, {order[-1]}. The line after them is the "
                    "Loop's last, “5 items done”.",
               highlight=ends,
               zoom=[(log, f"run #{first} · "), (log, f" for {MONTH}"), *ends, (log, f"run #{first} success")])
        p.check(f"The items ended in another order than the list's ({', '.join(ended)}), yet report has their "
                "results in the list's order", sorted(ended) == sorted(REGIONS) and ended != REGIONS
                and REPORTED in reported)
        p.check("The Log says “5 items done”", "[each_region] INFO 5 items done" in log.toPlainText())

        last = ended[-1]
        number = REGIONS.index(last) + 1
        p.step("Rest the mouse on the line under total, inside the box: its result.")
        p.note("Run from here and Run only this block are not offered on a block inside a Loop in this release: in "
               "the menu of total, their entries are greyed out and say to right-click the Loop instead.")
        tip = hover_result(qtbot, tab, "total")
        said = tip.text()
        p.shot(tip, f"✓ 5, and the last item to end, item {number} of 5 ({last}), with what total gave it: “region”: "
                    f"“{last}”, “total”: {TOTALS[last]}.", over=win, zoom=[tip, "total"])
        p.check(f"The card says ✓ 5, and that the last item to end was [item {number}/5], {last}: the last to end in "
                "the Log", "✓ 5" in said and f"the last item to end: [item {number}/5], success" in said)
        p.check(f"It shows that item's result: {last}, {TOTALS[last]}",
                re.search(rf"&quot;region&quot;: &quot;{last}&quot;|\"region\": \"{last}\"", said)
                and f"{TOTALS[last]}" in said)
        QTest.mouseMove(tab.view.viewport(), tab.view.viewport().rect().topLeft())
        QToolTip.hideText()

        # --- 4. Dark ----------------------------------------------------------------------------------------------
        p.step("Choose Tools > Settings…, set Theme to Dark and press Save. Then press ▶ Run now again.")
        set_theme(p, win, "Dark", shoot=False)
        QApplication.processEvents()
        p.check("The editor is dark", QApplication.palette().window().color().lightness() < 128)
        click_empty(tab)
        press(win, win.run_action)
        p.check("The second run comes to the same moment: 2 of 5 items done, 2 running",
                reaches(qtbot, lambda: len(tab.runs) == 2 and shown_progress(tab) == MIDWAY))
        filled, filled_colour, empty_colour = the_bar(tab, LOOP)
        problems = how_it_looks(tab, LOOP)
        p.shot(win, "In Dark, while it runs: the box is a lighter grey than the dark canvas, with a darker shadow; its "
                    "name, its counter (2/5 · 2 running) and its progress bar read well.", zoom=[LOOP])
        p.check("The box still had 2 of 5 done, 2 running, once the picture was taken", shown_progress(tab) == MIDWAY)
        p.check("The box is grey, unlike the canvas, with a darker shadow beside it, and its name and counter read "
                "well: a contrast of at least 4.5 to 1 with what is behind them"
                + (f" (not: {'; '.join(problems)})" if problems else ""), problems == [])
        ratio = contrast(filled_colour, empty_colour)
        p.check(f"The bar's filled part stands out from its empty part: {ratio:.1f} to 1 (3 to 1 or more, the bar for "
                f"the parts of a picture); it is {filled:.0%} full", ratio >= 3 and abs(filled - 0.4) <= 0.06)
        second = run_ended(qtbot, tab)
        p.check(f"Run #{second} succeeded, with the same results in the same order",
                tab.runs[-1].status == "success" and REPORTED in tab.scene.values.get("report", {}).get("output", ""))

        p.step("Set Theme back to System (Tools > Settings…) and press Save.")
        set_theme(p, win, "System", shoot=False)
        p.shot(win, f"The editor is light again (System: as this computer is); the box shows that run #{second} "
                    "ended: 5/5 done.", zoom=[LOOP])
        p.check("The Theme is System again, and the editor light",
                theme.chosen(win.settings) == "System" and QApplication.palette().window().color().lightness() >= 128)

        p.step(f"Last, look at when load began and ended each item in run #{first}, as the run history keeps each "
               "item's Log lines.")
        spans = item_spans(win, first)
        most = most_at_once(list(spans.values()))
        p.text(f"Run #{first}, from the run history: when load began and ended each item (in seconds, from when the "
               "first began)", timetable(spans))
        p.check(f"The run history has each of the 5 items' Log lines, and never more than 2 items at once (at most "
                f"{most} together)", sorted(spans) == [1, 2, 3, 4, 5] and most == 2)
        p.check("Items 1 and 2 began together; then, as soon as an item ended, the next in the list began, while north "
                "(item 1) was still loading: item 3 as item 2 ended, item 4 as item 3 ended, item 5 as item 4 ended",
                len(spans) == 5 and abs(spans[2][0] - spans[1][0]) < SOON
                and all(-TICK <= spans[k][0] - spans[k - 1][1] < SOON and spans[k][0] < spans[1][1] for k in (3, 4, 5)))
        p.check(f"The run history keeps one row for total over its items: ✓ 5, and the last item to end, item "
                f"{number} ({last})", total_row(win, first).get("items")
                == {"ended": "✓ 5", "last": f"[item {number}/5]", "status": "success"})
        p.note("Run history keeps one row for each block inside a Loop, with how its items ended and the last of them, "
               "as the canvas shows it. Each item's own result is kept with the run (Run from this block on, on the "
               "Loop, uses them), and every item's Log lines are in the run history, as above.")
    finally:  # the Theme and the application's look as the proof found them; no run left going
        for run_ in [r for t in win.tabs_list() for r in t.runs if r.running]:  # (a proof that failed mid-run)
            run_.proc.kill()
            run_.proc.waitForFinished(5000)
        win.settings.setValue("theme", "System")
        theme.apply("System")
        if app.style().name() != as_found[0]:
            QApplication.setStyle(as_found[0])
        QApplication.setPalette(as_found[1])
        QToolTip.setPalette(as_found[2])
        QApplication.processEvents()
        clean(win)
