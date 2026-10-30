"""Proof: Mermaid diagrams in the Log follow the editor into dark mode, and back, at once. How proofs are written:
README.md here."""

import yaml
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from proofs.test_proof_inspect_log import find, hugged, log_part, picture_area, plain, rect_at
from proofs.test_proof_retention_per_start import drag_up
from proofs.test_proof_run_output import run_now
from tests.test_inspect_mermaid import DARK, LIGHT, contrast, images, near, size_of
from tests.test_inspect_mermaid_theme import counting_layouts, settled

ASKED = ("… the Inspect block should pride itself to be the best place to display text information, everything "
         "should be well formated, beautiful even (i.e: have markdown/mermaide support...)")
DIAGRAM = """\
flowchart LR
    orders[Tonight's orders] --> pack(Pack the boxes)
    pack --> labels[Print the labels]
    labels --> vans[Load the vans]
"""  # this proof's own diagram: no other proof draws it, so the editor has not kept a picture of it yet
HEADING = "Tonight's shipping"
REPORT = f"""\
## {HEADING}

**{{value}}** orders to ship tonight, from three warehouses.

| Warehouse | Orders | Van |
|-----------|--------|-----|
| North     | 17     | 1   |
| South     | 15     | 2   |
| West      | 10     | 3   |

```mermaid
{DIAGRAM}```

Before the vans leave:

- pack the boxes
- print the labels
- load the vans
- send the drivers their routes
- lock the warehouses

The vans leave at 8 pm.
"""  # filled in like a Python f-string: {value} is count_orders' number
FLOW = {
    "blocks": {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
        "count_orders": {"type": "logic.python", "config": {"inputs": [], "code": "output = 42"},
                         "ui": {"x": 260, "y": 0}},
        "report": {"type": "logic.inspect", "config": {"message": REPORT, "markdown": True}, "ui": {"x": 540, "y": 0}},
    },
    "edges": ["start_by_hand.start -> count_orders.after", "count_orders.output -> report.value"],
}
LOG_HEIGHT = 340  # the Log made taller: lines above the diagram and below it, all in view


def turn(colours: dict):
    """Give the editor the colours (the palette) a light or a dark system mode gives it, as the system does."""
    palette = QPalette()
    for role, value in colours.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    QApplication.setPalette(palette)
    QTest.qWait(100)


def the_diagram(win) -> tuple:
    """(position, format) of the one picture the Log shows."""
    [found] = images(win.log.text)
    return found


def looks(win, pos: int, fmt) -> tuple[bool, float]:
    """How the Log shows the diagram at `pos`, where it is scrolled to (all of it in view): whether its margin (2 to 4
    pixels in from its top-left corner) is the Log's background, and how much its lines and words stand out from that
    background (the highest contrast, 1 to 21)."""
    edit = win.log.text
    at = rect_at(edit, pos)
    width, height = (round(v) for v in size_of(fmt))
    part = QRect(at.left(), at.top(), width, height)
    assert edit.viewport().rect().contains(part), "the diagram is not all in view"
    image = edit.viewport().grab().toImage()
    ratio = image.devicePixelRatio()

    def colours(xs, ys):
        return [image.pixelColor(int(x * ratio), int(y * ratio)) for x in xs for y in ys]

    base = edit.palette().color(QPalette.Base)
    margin = colours(range(part.left() + 2, part.left() + 5), range(part.top() + 2, part.top() + 5))
    whole = colours(range(part.left(), part.right() + 1, 2), range(part.top(), part.bottom() + 1, 2))
    return all(near(c, base) for c in margin), max(contrast(c, base) for c in whole)


def log_background(win) -> QColor:
    return win.log.text.palette().color(QPalette.Base)


def test_mermaid_diagrams_follow_the_editor_into_dark_mode_and_back(proof, win, qtbot, monkeypatch, tmp_path):
    p = proof("Mermaid diagrams follow the editor into dark mode, and back, at once", ASKED)
    p.idea("When the editor turns dark or light, as it does when you turn your computer's dark mode on or off, the "
           "Mermaid diagrams already in the Log are drawn again at once, on the Log's new background and in "
           "Mermaid's matching colours. You click nothing.")
    p.idea("Before, a diagram kept the background it was first drawn on until the Log was drawn again, for instance "
           "when you clicked a block: in dark mode, a white box in a dark Log. The proof of Mermaid diagrams found it: "
           "it had to click a block to see the dark colours.")
    p.idea("The rest of the Log stays as it was: the same lines, scrolled to the same place (at the bottom, it goes on "
           "following new lines), still showing only the block you clicked, if you clicked one.")
    p.why_simplest("The Log already knows how to draw itself again from its lines: it does it each time you click a "
                   "block or Show all. When its colours change, and only if it shows a diagram, it now does the same, "
                   "then scrolls back to where it was, up and down and sideways.")
    p.why_simplest("The editor already keeps each diagram it has drawn, by its text and the background it was drawn on "
                   "(the last 32), so that it is not drawn again each time the Log repaints. So a diagram is drawn "
                   "once for the dark background and once for the light one: switching back draws nothing new, it "
                   "shows the picture kept from before.")
    p.alternative("A diagram on a see-through background, to fit both",
                  "A diagram's lines and words are dark on a light background and light on a dark one (Mermaid's "
                  "dark colours). A see-through picture is still drawn in one of the two: in a dark Log, the light "
                  "diagram's dark lines and arrows would hardly show.")
    p.alternative("Each diagram taking the Log's background each time it is painted, the Log not drawn again",
                  "The Log makes room for each diagram at its size, and mmdr draws a flowchart a few pixels bigger in "
                  "its dark colours than in its light ones: the dark picture would be squeezed into the light one's "
                  "room, or the Log would have to be laid out again anyway.")
    p.alternative("Leaving it as it was: the new colours at the next click",
                  "Until then, a white box in a dark Log (or a dark one in a light Log). You asked for the Log to be "
                  "well formatted, beautiful even.")
    p.fits("It is what a click on a block or Show all already does: the Log drawn again from its lines, with the "
           "diagrams kept. So after you switch, the Log shows what a click would show, where you had scrolled to.")
    p.fits("Taskloom has no light or dark setting of its own: it takes the colours your system gives it (the canvas, "
           "for one, takes its background from them each time it is painted). Your computer's dark mode stays the "
           "one switch, and the Log's diagrams now follow it too.")
    p.fits("Only a Log that shows a diagram is drawn again: a Log of plain lines is left as it is.")
    flow = tmp_path / "tonight.yaml"
    flow.write_text(yaml.safe_dump(FLOW, sort_keys=False), encoding="utf-8")
    edit, bar = win.log.text, win.log.text.verticalScrollBar()
    laid_out = counting_layouts(monkeypatch)  # the text of each diagram mmdr lays out, from now on

    def drawn() -> int:
        return sum("Load the vans" in text for text in laid_out)

    def look(caption: str) -> tuple[bool, float, tuple[int, int]]:
        """Shoot the window, the diagram ① and report's heading ② outlined, with a close-up of the Log as it is
        scrolled; how the diagram is drawn (see looks), and where the heading and the diagram are in the Log's view
        (their tops)."""
        pos, fmt = the_diagram(win)
        view_top = edit.viewport().mapTo(win, QPoint(0, 0)).y()
        p.shot(win, caption, highlight=[hugged(picture_area(win, pos, fmt)), (edit, HEADING)], badges=True,
               zoom=log_part(win, view_top, view_top + edit.viewport().height()))
        return (*looks(win, pos, fmt), (rect_at(edit, find(win, HEADING)).top(), rect_at(edit, pos).top()))

    original = QApplication.palette()
    try:
        p.step("With your computer in light mode, open the flow tonight.yaml (File > Open…), press ▶ Run now, and "
               "drag the top edge of the Log up to make it taller. count_orders makes a number, 42; report, an "
               "Inspect block with Show as Markdown ticked, shows a message with a Mermaid flowchart.")
        turn(LIGHT)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
        file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
        next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
        tab = win.current
        p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
        run = run_now(win, tab, qtbot)
        drag_up(win, win.output, LOG_HEIGHT - win.output.height())
        qtbot.waitUntil(lambda: win.output.height() >= LOG_HEIGHT - 30, timeout=5_000)
        settled(edit)
        line = [*tab.scene.nodes]
        p.shot(win, "① The run ended with success: its three blocks are green. ② The Log, made taller, shows the end "
                    "of the run: report's message drawn as Markdown, its flowchart drawn as a picture, then the run's "
                    "last line.",
               highlight=[line, edit], badges=True)
        p.check("① The run ended with success: its three blocks show success",
                run.status == "success" and all(tab.scene.nodes[b].status == "success" for b in line))
        p.check("② The Log shows report's flowchart as a picture, not as its text",
                len(images(edit)) == 1 and "flowchart LR" not in plain(win) and "Load the vans" not in plain(win))
        p.note("To count the drawings, the proof notes each time mmdr lays out a diagram, from before the run on.")
        p.check("mmdr drew the flowchart once, on the light background", drawn() == 1)

        p.step("Scroll the Log up, until report's heading is the first line it shows.")
        bar.setValue(bar.value() + rect_at(edit, find(win, HEADING)).top())
        settled(edit)
        on_light, standing_out, places = look(
            "In light mode, ① report's flowchart is drawn on the Log's white background, in Mermaid's light colours: "
            "dark lines and words. ② The Log is scrolled up to report's heading; the rest of report's message, and "
            "the run's last line, are further down.")
        p.check("The Log's background is light", log_background(win).lightness() >= 128)
        p.check("① The flowchart is drawn on it: its margin is the Log's light background", on_light)
        p.check("Its lines and words stand out from it (a contrast of at least 3 to 1)", standing_out >= 3)
        scrolled, shown = bar.value(), plain(win)
        p.check(f"② The Log is scrolled to report's heading: {scrolled} pixels down, short of its bottom "
                f"({bar.maximum()})", 0 < scrolled < bar.maximum() and places[0] == 0)

        p.step("Turn your computer's dark mode on, and click nothing.")
        p.note("The proof turns the editor dark the way your computer's dark mode reaches it: it gives the editor the "
               "dark colours (its palette) that a dark mode gives it. It does not turn on the real dark mode setting "
               "of the computer it runs on, which would change the whole computer, not just this editor.")
        turn(DARK)
        settled(edit)
        on_dark, standing_out, now = look(
            "Dark mode is on, and nothing was clicked: the editor is dark, and ① the same flowchart, where it was in "
            "the Log, is now drawn on the Log's dark background, in Mermaid's dark colours: light lines and words. "
            "The Log shows the same lines, ② still scrolled to report's heading.")
        p.check("The Log's background is now dark", log_background(win).lightness() < 128)
        p.check("① The flowchart is drawn on it: its margin is the Log's dark background, not the white it was first "
                "drawn on", on_dark)
        p.check("Its lines and words stand out from it (a contrast of at least 3 to 1)", standing_out >= 3)
        p.check("The Log shows the same text as before", plain(win) == shown)
        p.check(f"② It is still scrolled {scrolled} pixels down: report's heading is still the first line it shows, "
                "and the flowchart is at the same height", bar.value() == scrolled and now == places)
        p.check("mmdr drew the flowchart once more, on the dark background: twice in all", drawn() == 2)

        p.step("Turn dark mode off again, and click nothing.")
        turn(LIGHT)
        settled(edit)
        on_light, standing_out, now = look(
            "Light mode again, and nothing was clicked: ① the flowchart is drawn on the Log's white background again, "
            "as it was first drawn, and ② the Log is still scrolled to report's heading.")
        p.check("The Log's background is light again", log_background(win).lightness() >= 128)
        p.check("① The flowchart is drawn on it: its margin is the Log's light background", on_light)
        p.check("Its lines and words stand out from it (a contrast of at least 3 to 1)", standing_out >= 3)
        p.check("The Log shows the same text as before", plain(win) == shown)
        p.check(f"② It is still scrolled {scrolled} pixels down: report's heading is still the first line it shows, "
                "and the flowchart is at the same height", bar.value() == scrolled and now == places)
        p.check("Nothing new was drawn: mmdr drew the flowchart twice in all, once for each background; the light "
                "picture shown now is the one kept from the run", drawn() == 2)
    finally:
        QApplication.setPalette(original)
        QApplication.processEvents()
