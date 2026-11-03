"""Proofs: what a Loop does when an item fails, and what Validate says about Loops (stage 8 of the 2026-10-30
feedback). A failed item starts no new one, the items running finish, and the Loop fails listing the failed items;
after a fix, Run from here on the Loop runs only the items left; Stop cancels every item running at once. Validate
refuses a connection out of a Loop but through its result, and warns about a fixed file path inside a Loop and a Loop
inside a Loop. See README.md here."""

import datetime as dt
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QContextMenuEvent, QHelpEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_line_failures import (boxes_said, delete_connection, history, label, open_flow, press,
                                             settled, status_message)
from taskloom.editor.canvas import HEADER_H
from tests.test_lines import wait_runs
from tests.test_paths_editor_editing import field, flow_file, mouse_drag

ASKED = "for_each block is meant to run flows? Remind me how its design was decided"
FROM_HERE = "Run from this block on, with the same inputs"
NOT_OFFERED = ("Run only this block, with the same inputs", "Run only this block, with inputs I choose…",
               "Run from this block on, with the same inputs", "Run from this block on, with inputs I choose…")
REGIONS = ["north", "south", "east", "west", "central"]  # items 1 to 5
NUMBERS = {"north": "10 20 30", "south": "40 50", "west": "30 50", "central": "10 20"}  # east's comes later
EAST = "20 25"
RESULTS = "[60, 90, 45, 80, 30]"  # each region's total, in the regions' order
REPORTED = "north 60, south 90, east 45, west 80, central 30"
# load reads the numbers of its region; for the proof, it first waits (a minute at most) until the proof lets that
# region go on (a file in go/), so that each screenshot catches the moment it is about.
LOAD = """import time
from pathlib import Path
print('loading', item)
for _ in range(1200):  # (for the proof: until it lets this region go on)
    if Path('go', item).exists():
        break
    time.sleep(0.05)
numbers = Path('numbers', f'{item}.txt')
if not numbers.exists():
    raise FileNotFoundError(f'no numbers for {item}')
output = [int(n) for n in numbers.read_text(encoding='utf-8').split()]
print('loaded', output)"""
MISSING = ("FileNotFoundError: no numbers for east "  # (with the line of load's code it was raised on)
           f"(line {next(n for n, line in enumerate(LOAD.splitlines(), 1) if 'raise' in line)})")
LOOP_FAILED = f"1 of 5 items failed: item 3 (load failed: {MISSING}); 1 not run"
BLOCKS_WIDTH, INSPECTOR_WIDTH, LOG_HEIGHT = 180, 420, 330  # a wider canvas, and a taller Log


# --- the flows -------------------------------------------------------------------------------------------------------

def python(inputs: list[str], code: str, x: int, y: int, inside: str | None = None) -> dict:
    return {"type": "logic.python", "config": {"inputs": inputs, "code": code}, "ui": {"x": x, "y": y},
            **({"inside": inside} if inside else {})}


def loop(at_the_same_time: int | None, x: int, y: int, inside: str | None = None, **size) -> dict:
    return {"type": "logic.loop", "config": {} if at_the_same_time is None else {"at_the_same_time": at_the_same_time},
            "ui": {"x": x, "y": y, **size}, **({"inside": inside} if inside else {})}


START = {"type": "trigger.manual", "ui": {"x": 0, "y": 60}}


def regions_flow(folder: Path) -> Path:
    """regions gives five regions; the Loop each_region (2 at the same time) runs load (the region's numbers, from
    numbers/<region>.txt) and total (their sum) for each; report pairs each region with its total."""
    (folder / "numbers").mkdir()
    for region, numbers in NUMBERS.items():
        (folder / "numbers" / f"{region}.txt").write_text(numbers + "\n", encoding="utf-8")
    return flow_file(folder / "regions.yaml", {
        "start_by_hand": START, "regions": python([], f"output = {REGIONS!r}", 230, 60),
        "each_region": loop(2, 460, 0),
        "load": python(["item"], LOAD, 540, 60, inside="each_region"),
        "total": python(["input"], "output = sum(input)", 774, 60, inside="each_region"),
        "report": python(["regions", "totals"], "output = ', '.join(f'{r} {t}' for r, t in zip(regions, totals))",
                         1068, 60),
    }, ["start_by_hand.start -> regions.after", "regions.output -> each_region.items", "each_region.item -> load.item",
        "load.output -> total.input", "total.output -> each_region.result", "each_region.results -> report.totals",
        "regions.output -> report.regions"])


def waits_flow(folder: Path) -> Path:
    """Three regions; the Loop each_region (2 at the same time) runs a Wait of 30 seconds for each."""
    return flow_file(folder / "waits.yaml", {
        "start_by_hand": START, "regions": python([], "output = ['north', 'south', 'east']", 230, 60),
        "each_region": loop(2, 460, 0),
        "pause": {"type": "logic.wait", "config": {"seconds": 30}, "ui": {"x": 540, "y": 60}, "inside": "each_region"},
    }, ["start_by_hand.start -> regions.after", "regions.output -> each_region.items",
        "each_region.item -> pause.value", "pause.value -> each_region.result"])


OUT_OF_THE_LOOP = "load.output -> report.totals"  # from inside each_region to outside it
THROUGH_RESULTS = "each_region.results -> report.totals"


def check_flow(folder: Path) -> Path:
    """Inside each_region: load, save_totals (a Write File to out/totals.csv) and the Loop each_month (inside it,
    check_month); report, outside, takes load's output (OUT_OF_THE_LOOP)."""
    return flow_file(folder / "check-loops.yaml", {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 150}},
        "regions": python([], "output = ['north', 'south', 'east']", 230, 150),
        "each_region": loop(None, 460, 0),
        "load": python(["item"], "output = pl.DataFrame({'month': [1, 2, 3], 'sales': [10, 20, 30]})", 540, 60,
                       inside="each_region"),
        "save_totals": {"type": "data.write_file", "config": {"path": "out/totals.csv"}, "ui": {"x": 784, "y": 60},
                        "inside": "each_region"},
        "each_month": loop(None, 540, 230, inside="each_region", width=360, height=170),
        "check_month": python(["item"], "output = item['sales'] > 0", 620, 290, inside="each_month"),
        "report": python(["totals"], "output = totals", 1098, 150),
    }, ["start_by_hand.start -> regions.after", "regions.output -> each_region.items", "each_region.item -> load.item",
        "load.output -> save_totals.table", "load.output -> each_month.items", "each_month.item -> check_month.item",
        "check_month.output -> each_month.result", "each_month.results -> each_region.result", OUT_OF_THE_LOOP])


def go(folder: Path, *regions: str):
    """Let load go on for these regions (see LOAD)."""
    (folder / "go").mkdir(exist_ok=True)
    for region in regions:
        (folder / "go" / region).write_text("", encoding="utf-8")


# --- doing it as you would ---------------------------------------------------------------------------------------------

def make_room(win, taller_log: bool = True):
    """Drag the Blocks list's right edge and the panels' left edge in (and the Log's top edge up)."""
    win.resizeDocks([win.blocks_dock, win.inspector], [BLOCKS_WIDTH, INSPECTOR_WIDTH], Qt.Horizontal)
    if taller_log:
        win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)
    QTest.qWait(100)


def loop_top(tab, loop_id: str) -> QPoint:
    """A spot on the Loop's top, on its name, in the canvas's pixels: where you click, grab or right-click the box."""
    box = tab.scene.nodes[loop_id].drawn_rect()
    return tab.view.mapFromScene(QPointF(box.left() + 90, box.top() + HEADER_H / 2))


def click_loop(win, tab, loop_id: str):
    """Click the Loop's top: Properties shows it, and the Log only its lines."""
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, loop_top(tab, loop_id))
    QTest.qWait(100)
    assert win.properties.block_id == loop_id, f"{loop_id} is not the block Properties shows"


def right_click_loop(tab, loop_id: str):
    """Right-click the Loop's top (opens its menu, which waits for a choice: see Proof.while_open)."""
    viewport, at = tab.view.viewport(), loop_top(tab, loop_id)
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, viewport.mapToGlobal(at)))


def tooltip():
    return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)


def rest_on(qtbot, menu, action):
    """Rest the mouse on an item of an open menu: its tooltip, once it shows."""
    at = menu.actionGeometry(action).center()
    QApplication.sendEvent(menu, QHelpEvent(QEvent.ToolTip, at, menu.mapToGlobal(at)))
    qtbot.waitUntil(lambda: tooltip() is not None and tooltip().text() == action.toolTip(), timeout=5_000)
    QTest.qWait(100)
    return tooltip()


def on_canvas(win, tab, area: QRectF) -> QRect:
    """`area` of the canvas (in the flow's points), in the window's pixels."""
    return tab.view.mapFromScene(area).boundingRect().translated(tab.view.viewport().mapTo(win, QPoint(0, 0)))


def counter(win, tab, loop_id: str) -> QRect:
    """Where the Loop's counter (done/N · k running) is, at the right of its top, in the window's pixels."""
    box = tab.scene.nodes[loop_id].drawn_rect()
    return on_canvas(win, tab, QRectF(box.right() - 150, box.top() + 3, 145, HEADER_H - 6))


def under(win, tab, block: str) -> QRect:
    """Where the line under a block (or at the bottom of a Loop's box), its result, is, in the window's pixels."""
    return on_canvas(win, tab, tab.scene.nodes[block].footer.sceneBoundingRect())


def connection(win, tab, text: str) -> QRect:
    """Where a connection (as the flow file writes it) runs on the canvas, in the window's pixels."""
    (edge,) = [e for e in tab.scene.edge_items if e.text == text]
    return on_canvas(win, tab, edge.sceneBoundingRect())


def drag_port(tab, src: str, src_port: str, dst: str, dst_port: str):
    """Drag from an output to an input on the canvas, as you would, to connect them."""
    a = tab.view.mapFromScene(tab.scene.nodes[src].ports[(src_port, True)].scenePos())
    b = tab.view.mapFromScene(tab.scene.nodes[dst].ports[(dst_port, False)].scenePos())
    assert tab.view.viewport().rect().contains(a) and tab.view.viewport().rect().contains(b), "a port is out of view"
    mouse_drag(tab, a, b)


def scroll_log(win, text: str | None):
    """Scroll the Log so that its first line showing `text` is at its top; None: to its end."""
    box = win.log.text
    scroll = box.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    if text is not None:
        found = box.document().find(text)
        assert not found.isNull(), f"no line of the Log shows {text!r}"
        found.clearSelection()
        box.setTextCursor(found)
        box.ensureCursorVisible()  # (from the end up to it: it comes to the top)
        scroll.setValue(scroll.value() - box.fontMetrics().lineSpacing())  # (and the line before it, above it)
    QTest.qWait(100)


def log_area(win, texts: list[str], after: int, inset: int = 0) -> QRect:
    """The lines of the Log that show any of `texts`, after its first `after` characters, in the window's pixels.
    `inset`: that much inside them, top and bottom (an outline drawn around them alone then goes between them and the
    lines next to them)."""
    box, area = win.log.text, QRect()
    for text in texts:
        found = box.document().find(text, after)
        assert not found.isNull(), f"no line of the Log after character {after} shows {text!r}"
        line = QTextCursor(found)
        line.movePosition(QTextCursor.StartOfLine)
        area |= box.cursorRect(line)
        line.movePosition(QTextCursor.EndOfLine)
        area |= box.cursorRect(line)
    return area.adjusted(-3, inset, 3, -inset).translated(box.viewport().mapTo(win, QPoint(0, 0)))


def run_log(win, run_id: int) -> list[str]:
    """The Log's lines from run #run_id's first line to its last."""
    lines = win.log.text.toPlainText().splitlines()
    first = next(i for i, line in enumerate(lines) if f"run #{run_id} · " in line)
    last = next(i for i, line in enumerate(lines)
                if i > first and re.search(rf"run #{run_id} (success|failed|cancelled)$", line))
    return lines[first:last + 1]


def at(lines: list[str], text: str) -> int:
    """The number of the first of `lines` that has `text` (-1: none)."""
    return next((i for i, line in enumerate(lines) if text in line), -1)


def end_runs(win, qtbot, folder: Path):
    """After the proof (also one that failed half way): no run left going, waiting for the proof."""
    go(folder, *REGIONS)
    for tab in win.tabs_list():
        if tab.running:
            win.stop(tab)
    qtbot.waitUntil(lambda: not any(tab.running for tab in win.tabs_list()), timeout=30_000)


def ended(tab, block: str) -> str:
    """What the canvas shows under a block: for one inside a Loop, how its items ended."""
    return tab.scene.nodes[block].status_text


# --- 1: a failed item, Run from here on the Loop, Stop ---------------------------------------------------------------

def test_a_failed_item_starts_no_new_one_and_run_from_the_loop_runs_only_the_items_left(proof, win, qtbot, tmp_path,
                                                                                         monkeypatch, request):
    p = proof("A failed item starts no new one; after a fix, Run from here on the Loop runs only the items left",
              ASKED)
    p.idea("As you decided: when an item of a Loop fails, no new item starts. The items already running go on to their "
           "end, then the Loop fails, listing the failed items, why each failed, and how many did not run: “1 of 5 "
           "items failed: item 3 (load failed: …); 1 not run”. The blocks after the Loop do not run, as after any "
           "failure.")
    p.idea("Once you have fixed what failed, right-click the Loop and choose Run from this block on, with the same "
           "inputs. It runs again only the items that failed or did not run; the items that succeeded keep the "
           "results they gave, so the Loop's results come out complete, in the items' order, and the blocks after it "
           "run.")
    p.idea("On a block inside a Loop, Run only this block and Run from this block on are not offered in this release: "
           "they are greyed out, and say to right-click the Loop instead. Stop ends every item running at once: each "
           "is cancelled, and so is the Loop.")
    p.why_simplest("Each item is a small run of the blocks inside the Loop, and a failure in it works as anywhere: "
                   "the blocks after the failed one, in that item, are skipped. The Loop adds a single rule: once an "
                   "item has failed, it starts no new one. It does not stop the items running: as you decided, they "
                   "finish, so no work half done is thrown away.")
    p.why_simplest("To run again, the Loop keeps each item's result with the run. Run from this block on is the "
                   "command Taskloom already had; on a Loop it runs again only the items that did not succeed, and "
                   "takes the others' results from the run you carry on from (an item is kept when it is the same "
                   "item, at the same place in the list, as in that run). Nothing new to learn.")
    p.alternative("Cancel the items running as soon as one fails",
                  "Their work, half done, would be thrown away and run again after the fix. As you decided: no new "
                  "item starts, and the running ones finish.")
    p.alternative("Go on with every item, and fail at the end",
                  "A problem shared by every item (a server down, a missing folder) would make each of them fail, one "
                  "after another, before you hear of it. Stopping at the first failure ends the run soon; the items "
                  "left are listed as not run, and run after the fix.")
    p.alternative("Let the Loop pass on the results it has, with gaps for the failed items",
                  "The blocks after it would work on part of the data, and the run could look fine: a surprise. A "
                  "Loop that fails, naming the items, stops that before it happens.")
    p.alternative("Run every item again after the fix",
                  "The items that succeeded would be done twice: files written twice, emails sent twice. As you "
                  "decided, running again keeps the items that succeeded.")
    p.alternative("Offer Run from here on the block inside that failed",
                  "That block runs once for each item: run on its own, it would have to be told which item, and what "
                  "to do with the rest of that item's blocks. Not in this release: its menu says to right-click the "
                  "Loop, which runs every item left.")
    p.fits("A failed block inside a Loop looks and behaves as anywhere else: red, its error in the Log, its error "
           "output there to handle the failure. The Loop's own error output handles the Loop's failure, as any "
           "block's does.")
    p.fits("Each Log line of a block inside carries its item, [item 3/5], so the lines of items running at the same "
           "time can be told apart. Under each block inside, the canvas shows how its items ended: ✓ 3 ✕ 1.")
    p.fits("Run from this block on, with the same inputs, is the same menu item as on any block: the blocks before the "
           "Loop do not run again, and what they gave in the failed run is used. Stop is the same Stop as for any "
           "run.")
    folder = tmp_path / "flows"
    folder.mkdir()
    flow = regions_flow(folder)
    request.addfinalizer(lambda: end_runs(win, qtbot, folder))
    log = win.log.text

    p.step("Open the flow regions.yaml (File > Open…). regions gives five regions; for each, the Loop each_region runs "
           "load (it reads the region's numbers) and total (their sum); report, after it, pairs each region with "
           "its total.")
    p.note("For this proof, the Blocks list and the panels on the right are made narrower and the Log taller (their "
           "edges dragged), so that the canvas and the Log show more.")
    make_room(win)
    tab = open_flow(win, monkeypatch, flow)
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())

    p.step("Click each_region, at its top: Properties shows its setting At the same time: 2.")
    click_loop(win, tab, "each_region")
    same_time = field(win, "At the same time")
    row = [label(win, "At the same time"), same_time]
    p.shot(win, "① The Loop each_region, a grey box around load and total, the blocks it runs once for each region. "
                "② At the same time is 2: two regions at once. The numbers folder has north, south, west and "
                "central: east's numbers are missing, so its load will fail.",
           highlight=["each_region", row], badges=True, zoom=["each_region", row])
    p.check("① load and total are inside each_region; ② At the same time shows 2",
            all(tab.doc.blocks[b].get("inside") == "each_region" for b in ("load", "total"))
            and same_time.text() == "2")

    p.step("Click Show all, above the Log, and press ▶ Run now in the toolbar. north and south (items 1 and 2) run at "
           "once; as they end, east and west (items 3 and 4) start, and east fails.")
    p.note("To catch each moment on a screenshot, load waits, for each region, until the proof lets it go on (a file "
           "it puts in the flow's folder): north and south first, then east, then west. You would not do this in a "
           "flow of your own; it changes nothing else.")
    QTest.mouseClick(win.log.clear_filter, Qt.LeftButton)
    press(win, win.run_action)
    box = tab.scene.nodes["each_region"]
    qtbot.waitUntil(lambda: box.progress == (0, 5, 2), timeout=60_000)
    go(folder, "north", "south")
    qtbot.waitUntil(lambda: box.progress == (2, 5, 2), timeout=30_000)
    go(folder, "east")
    qtbot.waitUntil(lambda: box.progress == (3, 5, 1), timeout=30_000)
    QTest.qWait(1500)  # (time enough for a new item to start, if one would)
    p.shot(win, "east has failed, and west is still running: ① the Loop's counter says 3/5 · 1 running (north, south "
                "and east are done with); ② load shows as running, for west. No new item started: central (item 5) "
                "will not run.",
           highlight=[counter(win, tab, "each_region"), "load"], badges=True, zoom=["each_region"])
    p.check("① The Loop's counter shows 3 of 5 done, 1 running; ② load is running",
            box.progress == (3, 5, 1) and tab.scene.nodes["load"].status == "running")

    p.step("Scroll the Log up to where east and west start.")
    scroll_log(win, "[item 3/5] loading east")
    failed = "[load] ERROR [item 3/5] load (Python Code) failed: FileNotFoundError: no numbers for east"
    said = [(log, "[item 3/5] loading east"), (log, "[item 4/5] loading west"), (log, failed)]
    p.shot(win, "The Log at that moment: ① east (item 3) and west (item 4) started, then east failed: no numbers for "
                "east. Under it, its error in full. No line from central (item 5): it did not start.",
           highlight=[said], badges=True, zoom=said)
    text = log.toPlainText()
    p.check("① east and west started, then east failed; the Log has no line from item 5",
            0 <= at(text.splitlines(), said[0][1]) < at(text.splitlines(), failed)
            and 0 <= at(text.splitlines(), said[1][1]) < at(text.splitlines(), failed) and "[item 5/5]" not in text)

    p.step("Wait for west to end (the proof lets it go on): the run ends. Scroll the Log down to its end.")
    go(folder, "west")
    first = wait_runs(qtbot, tab, 1)
    settled(win, qtbot)
    scroll_log(win, None)
    kept = history(win, lambda h: h.blocks(first))
    p.shot(win, "① The Loop failed: red, its error at its bottom; its counter says 4/5 done (central did not run). "
                "② Under load, ✓ 3 ✕ 1: three regions loaded, east failed. ③ Under total, ✓ 3 skipped 1: east's "
                "total was skipped, as after any failure. ④ report, after the Loop, did not run.",
           highlight=["each_region", "load", "total", "report"], badges=True,
           zoom=["each_region", "report"])
    p.check(f"Run #{first} failed", tab.runs[0].status == "failed")
    p.check(f"① The run history has the Loop failed with “{LOOP_FAILED}”",
            kept["each_region"]["status"] == "failed" and kept["each_region"]["error"] == LOOP_FAILED)
    p.check("② load shows ✓ 3 ✕ 1, and ③ total ✓ 3 skipped 1, as the run history keeps them",
            ended(tab, "load") == "✓ 3 ✕ 1" and ended(tab, "total") == "✓ 3 skipped 1"
            and kept["load"]["items"]["ended"] == "✓ 3 ✕ 1" and kept["total"]["items"]["ended"] == "✓ 3 skipped 1")
    p.check("④ report was skipped",
            tab.scene.nodes["report"].status == "skipped" and kept["report"]["status"] == "skipped")

    lines = run_log(win, first)
    said = [(log, "[item 4/5] loaded [30, 50]"), (log, "[each_region] ERROR each_region (Loop) failed: "),
            (log, f"run #{first} failed")]
    p.shot(win, f"The end of the Log: ① west went on after east's failure, and loaded its numbers. ② Then the Loop "
                f"failed, listing the failed item, why it failed, and the one not run; and run #{first} failed.",
           highlight=[said[0], said[1:]], badges=True, zoom=said)
    p.check(f"① west loaded its numbers after east's failure; ② then the Loop failed with “{LOOP_FAILED}”",
            0 <= at(lines, failed) < at(lines, said[0][1])
            < at(lines, f"[each_region] ERROR each_region (Loop) failed: {LOOP_FAILED}"))
    p.check("No line of the run is from item 5", not [line for line in lines if "[item 5/5]" in line])
    p.note("west's lines come after east's failure: an item that had already started when another failed runs to its "
           "end, so its own lines can come after the failure in the Log. Each line's [item i/5] says which item it "
           "is from.")
    p.note("Had load's error output been connected to a block inside the Loop that handles the failure, east's item "
           "would not have failed, and neither would the Loop. Leave it unconnected, as here, when a failure should "
           "fail the item.")

    # --- a block inside: not offered yet ------------------------------------------------------------------------
    p.step("Right-click load, the block that failed, and rest the mouse on “Run from this block on, with the same "
           "inputs”.")
    entries = []

    def look_inside(menu):
        entries.extend(menu_item(menu, text) for text in NOT_OFFERED)
        p.shot(menu, "The menu of load, inside the Loop: ① Run only this block and Run from this block on are greyed "
                     "out.", over=win, highlight=[entries], badges=True, zoom=[menu, "load"])
        tip = rest_on(qtbot, menu, menu_item(menu, FROM_HERE))
        entries.append(tip.text())
        # (shot by itself: one line wider than the window, it is shown from the screen's left edge, beyond the window)
        p.shot(tip, "Its tooltip, in full, says why, and what to do instead: right-click the loop each_region and "
                    "choose Run from this block on; it runs again the items that failed or did not run.")

    p.while_open(lambda: right_click(tab, "load"), look_inside)
    why = ("load is inside the loop each_region: running a block inside a loop on its own is not offered yet. "
           "Right-click the loop each_region and choose Run from this block on: it runs again the items that failed "
           "or did not run.")
    p.check("① The four are greyed out, each saying “load is inside the loop each_region: … Right-click the loop "
            "each_region and choose Run from this block on …”",
            all(not a.isEnabled() and a.toolTip() == why for a in entries[:4]) and entries[4] == why)
    p.check("Nothing ran", len(tab.runs) == 1)

    # --- the fix, and Run from here on the Loop ----------------------------------------------------------------
    p.step(f"Put east's numbers in the numbers folder: east.txt, with {EAST}.")
    (folder / "numbers" / "east.txt").write_text(EAST + "\n", encoding="utf-8")
    go(folder, *REGIONS)  # (from now on, load does not wait)

    p.step("Right-click each_region, at its top, and choose “Run from this block on, with the same inputs”.")

    def run_from_the_loop(menu):
        p.shot(menu, f"The menu of each_region: ① the item to choose. The same inputs are those of run #{first}, the "
                     "run the Loop shows.", over=win, highlight=[menu_item(menu, FROM_HERE)], badges=True,
               zoom=[menu])
        choose(menu, FROM_HERE)

    p.while_open(lambda: right_click_loop(tab, "each_region"), run_from_the_loop)
    second = wait_runs(qtbot, tab, 2)
    settled(win, qtbot)
    kept = history(win, lambda h: h.blocks(second))
    p.shot(win, f"Run #{second} succeeded. ① The Loop is green, and gives every region's total, in the regions' "
                f"order: results {RESULTS} (60, 90 and 80 kept from run #{first}, 45 and 30 made now). ② load and "
                "total show ✓ 2: the two regions that ran this time. ③ report ran.",
           highlight=[under(win, tab, "each_region"), ["load", "total"], "report"], badges=True,
           zoom=["each_region", "report"])
    p.check(f"Run #{second} succeeded", tab.runs[1].status == "success")
    p.check(f"① The Loop shows success, from run #{second}, with results {RESULTS}, as the run history keeps it",
            box.status == "success" and tab.block_info["each_region"]["run_id"] == second
            and ended(tab, "each_region") == f"✓ results: {RESULTS}"
            and kept["each_region"]["summary"] == f"results: {RESULTS}")
    p.check(f"② load and total show ✓ 2, from run #{second}",
            all(tab.scene.nodes[b].status == "success" and tab.block_info[b]["run_id"] == second
                and ended(tab, b) == "✓ 2" for b in ("load", "total")))
    p.check(f"③ report succeeded, and made “{REPORTED}”",
            kept["report"]["status"] == "success" and tab.scene.values.get("report", {}).get("output") == REPORTED)
    p.check(f"The history of run #{second} holds the Loop, the blocks inside it and report, and no other block",
            set(kept) == {"each_region", "load", "total", "report"})

    lines = run_log(win, second)
    keeps = f"5 items, 2 at the same time; items 1, 2 and 4 keep their results from run #{first}, where they succeeded"
    ran = ["[item 3/5] loading east", "[item 5/5] loading central", "[item 3/5] loaded [20, 25]",
           "[item 5/5] loaded [10, 20]"]
    said = [(log, f"run #{second} · "), (log, f"[each_region] INFO {keeps}"), (log, f"run #{second} success")]
    p.shot(win, f"The Log of run #{second}: ① the Loop says items 1, 2 and 4 keep their results from run #{first}, "
                "where they succeeded; under it, only east (item 3) and central (item 5) ran: no line from items 1, 2 "
                "or 4.",
           highlight=[[said[1], log_area(win, ran, log.toPlainText().index(f"run #{second} · "))]], badges=True,
           zoom=said)
    p.check(f"① The Log says “{keeps}”", at(lines, f"[each_region] INFO {keeps}") >= 0)
    p.check("① Only items 3 and 5 ran: the Log has their lines, and none from items 1, 2 or 4",
            all(at(lines, line) >= 0 for line in ran)
            and not [line for line in lines if any(f"[item {k}/5]" in line for k in (1, 2, 4))])

    # --- Stop --------------------------------------------------------------------------------------------------
    p.step("Open the flow waits.yaml (File > Open…) and press ▶ Run now. Its Loop runs a Wait of 30 seconds for each "
           "of three regions, two at the same time.")
    stop_tab = open_flow(win, monkeypatch, waits_flow(folder))
    press(win, win.run_action)
    box = stop_tab.scene.nodes["each_region"]
    qtbot.waitUntil(lambda: box.progress == (0, 3, 2) and stop_tab.scene.nodes["pause"].status == "running",
                    timeout=30_000)
    QTest.qWait(1500)  # their countdowns are going
    p.shot(win, "Two items wait at once: ① the counter says 0/3 · 2 running; ② pause is running.",
           highlight=[counter(win, stop_tab, "each_region"), "pause"], badges=True, zoom=list(stop_tab.doc.blocks))
    p.check("① 0 of 3 done, 2 running; ② pause is running",
            box.progress == (0, 3, 2) and stop_tab.scene.nodes["pause"].status == "running")

    p.step("Click Stop in the toolbar.")
    stopped = dt.datetime.now()
    press(win, win.stop_action)
    third = wait_runs(qtbot, stop_tab, 1)
    settled(win, qtbot)
    kept = history(win, lambda h: (h.blocks(third), h.run(third)))
    p.shot(win, "Both items ended at once: ① under pause, cancelled 2. ② The Loop is cancelled (its outline dashed).",
           highlight=[under(win, stop_tab, "pause"), "each_region"], badges=True, zoom=list(stop_tab.doc.blocks))
    p.check("① Both items' pause were cancelled: “cancelled 2”, on the canvas and in the run history",
            ended(stop_tab, "pause") == "cancelled 2" and kept[0]["pause"]["items"]["ended"] == "cancelled 2")
    p.check("② The Loop, and the run, ended cancelled",
            box.status == "cancelled" and kept[0]["each_region"]["status"] == "cancelled"
            and stop_tab.runs[0].status == "cancelled")
    p.check("The run ended within 5 seconds of Stop, long before the 30 seconds",
            (dt.datetime.fromisoformat(kept[1]["finished"]) - stopped).total_seconds() < 5)

    lines = run_log(win, third)
    said = [(log, "[item 1/3] waiting 30 s"), (log, "[item 2/3] waiting 30 s"), (log, f"run #{third} cancelled")]
    waiting, cancelled = log_area(win, [said[0][1], said[1][1]], 0, 5), log_area(win, [said[2][1]], 0, 5)
    cancelled.setRight(waiting.right() + 40)  # (its number clear of the lines above)
    p.shot(win, f"The Log of run #{third}: ① items 1 and 2 began their 30 seconds; ② at Stop, the run was cancelled. "
                "No line from item 3: it never started.",
           highlight=[waiting, cancelled], badges=True, zoom=[*said, cancelled])
    p.check("① Items 1 and 2 began to wait; ② the run was cancelled; no line from item 3",
            at(lines, said[0][1]) >= 0 and at(lines, said[1][1]) >= 0 and at(lines, f"ERROR run #{third} cancelled") >= 0
            and not [line for line in lines if "[item 3/3]" in line])


# --- 2: Validate ---------------------------------------------------------------------------------------------------

def test_validate_refuses_a_connection_out_of_a_loop_and_warns_about_a_fixed_path_and_a_loop_in_a_loop(
        proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("What Validate says about Loops", ASKED)
    p.idea("Validate (F7) checks the Loops too, before anything runs. A connection from a block inside a Loop to a "
           "block outside it is refused, naming the connection: a block inside connects out of the Loop only through "
           "the Loop's result, and the blocks outside take the Loop's results.")
    p.idea("Three things are warned about, in the Log, without refusing the flow: a block inside a Loop that writes a "
           "file whose path has no {placeholder} (every item writes the same file, each over the one before); a Loop "
           "inside another Loop (its blocks run items × items times); and a Loop whose results is connected while "
           "nothing inside it is connected to its result (every item gives back nothing). This proof shows the first "
           "two.")
    p.why_simplest("Each item gives back one thing, at the Loop's result; the Loop collects them, in the items' order, "
                   "at its results. A connection from inside straight out would leave it open which item's value the "
                   "block outside gets, and when, while items may still be running. So there is one way out, and the "
                   "message says what to connect instead.")
    p.why_simplest("The two warnings shown are about what the canvas does not show: a file path looks fine on one "
                   "block, but it is written once per item; a box inside a box looks small, but its blocks run once "
                   "per item of both. They are warnings, not refusals: both can be what you want.")
    p.alternative("Let a connection go out of the Loop, with the last item's value",
                  "Which item is last depends on which ends last when several run at the same time: a surprise. "
                  "Through result, every item's value comes out, in order.")
    p.alternative("Refuse a fixed file path inside a Loop, and a Loop inside a Loop",
                  "A file written over by each item can be what you want (only the last one matters), and so can a "
                  "small Loop inside another. Validate says it in the Log, and the flow runs.")
    p.alternative("Add the item to the file's name by itself",
                  "A file name you did not write would surprise you. Validate says it, and you choose.")
    p.fits("Validate (F7) and taskloom validate in a terminal list the same problems, and every run checks for them "
           "before it starts: ▶ Run now refuses a flow Validate refuses.")
    p.fits("As for any other problem, an error shows in a box and in the Log, a warning in the Log only. The "
           "connection is named as the flow file writes it, as Validate names other connection problems.")
    folder = tmp_path / "flows"
    folder.mkdir()
    log = win.log.text

    p.step("Open the flow check-loops.yaml (File > Open…). Inside the Loop each_region: load, save_totals (a Write "
           "File) and another Loop, each_month. report, outside, takes load's output.")
    p.note("For this proof, the Blocks list and the panels on the right are made narrower (their edges dragged), so "
           "that the canvas is wider.")
    make_room(win, taller_log=False)
    tab = open_flow(win, monkeypatch, check_flow(folder))
    blocks = list(tab.doc.blocks)
    p.check("The flow opens", tab is not None and tab.doc.path.name == "check-loops.yaml")
    p.shot(win, "① load's output goes out of each_region, straight into report. ② save_totals writes out/totals.csv, "
                "for every region. ③ each_month, a Loop inside each_region.",
           highlight=["load", "report", "save_totals", "each_month"], badges=[1, 1, 2, 3], zoom=blocks)
    refused = (f"edge {OUT_OF_THE_LOOP}: from inside the loop each_region to outside it: a block inside a loop connects "
               "out of it only through the loop's result (connect it to each_region.result, and take "
               "each_region.results outside the loop)")
    same_file = ("save_totals: Path: every item of the loop each_region writes to out/totals.csv (it has no "
                 "{placeholder}, so each item writes over what the one before wrote)")
    nested = ("each_month: a loop inside the loop each_region: its blocks run for every item of each_month once per "
              "item of each_region (items × items times)")

    p.step("Press Validate in the toolbar (F7).")
    said = boxes_said(p, monkeypatch, lambda: press(win, win.validate_action),
                      f"The flow has problems: the connection {OUT_OF_THE_LOOP} goes from inside the loop each_region "
                      "to outside it; connect it to each_region.result, and take each_region.results outside the loop.")
    p.check(f"Validate refuses the connection, naming it, and only it: “{refused}”", said == [refused])
    warned = [(log, same_file), (log, nested)]
    p.shot(win, "The Log, under the canvas, has the same error, and before it ① two warnings: every region writes "
                "out/totals.csv over the one before, and each_month's blocks run items × items times.",
           highlight=[warned], badges=True, zoom=[*warned, (log, refused)])
    text = log.toPlainText()
    p.check(f"① The Log warns: “{same_file}”", f"WARNING {same_file}" in text)
    p.check(f"① The Log warns: “{nested}”", f"WARNING {nested}" in text)
    p.check("No run was started", tab.runs == [])

    p.step("Click the connection from load to report and press Delete. Then drag from each_region's results, on the "
           "right of its box, to report's totals.")
    delete_connection(win, tab, qtbot, OUT_OF_THE_LOOP)
    drag_port(tab, "each_region", "results", "report", "totals")
    p.shot(win, "① report now takes each_region's results: every region's result, in order.",
           highlight=[connection(win, tab, THROUGH_RESULTS)], badges=True, zoom=blocks)
    p.check("report takes each_region.results; the connection from load is gone",
            THROUGH_RESULTS in tab.doc.data["edges"] and OUT_OF_THE_LOOP not in tab.doc.data["edges"])

    p.step("Press Validate (F7) again.")
    before = len(log.toPlainText())
    press(win, win.validate_action)
    QTest.qWait(200)
    message, showing = status_message(win)
    new = log.toPlainText()[before:].splitlines()
    p.shot(win, f"No box this time: the status bar, at the bottom of the window, says “{message}”.",
           highlight=[showing], zoom=[showing])
    p.check("Validate refuses nothing: no box, and the status bar says no problems found",
            win.message_boxes == [] and message.endswith("no problems found"))
    again = log_area(win, [same_file, nested], before, 5)
    p.shot(win, "The two warnings are in the Log again (outlined), under the first Validate's lines: the flow can "
                "run, and they say what to look at.",
           highlight=[again], zoom=[(log, refused), again])
    p.check("The Log has the two warnings again, and no error",
            at(new, f"WARNING {same_file}") >= 0 and at(new, f"WARNING {nested}") >= 0
            and not [line for line in new if " ERROR " in line])
