"""Proof: closing the Documentation column gives the window, Blocks and Properties back the widths they had before it
opened, also in a window too narrow for the column beside them; a wide window, for comparison."""

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDockWidget, QFileDialog

from proofs.conftest import WINDOW, menu_item
from taskloom.editor.canvas import HEADER_H, NODE_W
from tests.test_docs_help import column_shown, docs_dock, wait_for, x_of
from tests.test_editor import write_flow
from tests.test_paths_editor_editing import settle

FLOW = """
    blocks:
      start_by_hand:
        type: trigger.manual
        ui: {x: 0, y: 0}
      archive_sales:
        type: files.copy
        config: {source: data/sales.csv, destination: archive/}
        ui: {x: 0, y: 170}
    edges:
      - start_by_hand.start -> archive_sales.after
"""
NAMES = {"window": "The window", "blocks": "Blocks", "canvas": "The canvas", "properties": "Properties",
         "column": "Documentation"}


def blocks_panel(win) -> QDockWidget:
    return next(d for d in win.findChildren(QDockWidget) if d.windowTitle() == "Blocks")


def widths(win) -> dict:
    """How wide the window and its panels are, in pixels (the column: 0 while it is closed)."""
    column = docs_dock(win)
    return {"window": win.width(), "blocks": blocks_panel(win).width(), "canvas": win.centralWidget().width(),
            "properties": win.inspector.width(), "column": column.width() if column.isVisible() else 0}


def table(before: dict, opened: dict, closed: dict) -> str:
    """The widths before the column opened, while it was open and once it closed, a row each."""
    def cell(n):
        return str(n) if n else "–"
    rows = [f"{'':<16}{'Before':>8}{'Column open':>14}{'Closed':>9}"]
    rows += [f"{NAMES[k]:<16}{cell(before[k]):>8}{cell(opened[k]):>14}{cell(closed[k]):>9}" for k in NAMES]
    return "\n".join(rows)


def close_up(win) -> QRect:
    """The top of Blocks and the canvas, in the window's pixels: where the close-up shows Blocks' width."""
    canvas = win.centralWidget()
    top_left = blocks_panel(win).mapTo(win, QPoint(0, 0))
    return QRect(top_left, QPoint(canvas.mapTo(win, QPoint(canvas.width(), 0)).x(), top_left.y() + 330))


def press_f1(win, qtbot, shown: bool):
    """F1 on the canvas, as you would; then the column is open (or closed) and the window laid out."""
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=3_000)
    win.current.view.setFocus()
    QTest.keyClick(win.current.view, Qt.Key_F1)
    wait_for(qtbot, lambda: column_shown(win) == shown, f"F1 did not {'open' if shown else 'close'} the column")
    settle()
    QTest.qWait(100)


def resize(win, qtbot, width: int):
    """The window `width` pixels wide, as dragging its edge makes it."""
    win.resize(width, WINDOW[1])
    qtbot.waitUntil(lambda: win.width() == width, timeout=3_000)
    settle()


def made_room(before: dict, opened: dict) -> str:
    """How Qt made room for the column, in words."""
    how = [f"{'Blocks' if k == 'blocks' else 'the window'} went from {before[k]} to {opened[k]} pixels"
           for k in ("blocks", "window") if opened[k] != before[k]]
    return ", and ".join(how) or "nothing changed"


def back(name: str, before: dict, opened: dict, closed: dict) -> str:
    """A check's words: `name` is back to its width, with its widths."""
    return (f"{NAMES[name]} is back to its width: {before[name]} pixels before, {opened[name]} with the column "
            f"open, {closed[name]} once it closed")


def test_closing_the_documentation_column_gives_the_panels_back_their_widths(proof, win, qtbot, tmp_path,
                                                                             monkeypatch):
    p = proof("Closing the Documentation column gives the panels back their widths, also in a narrow window",
              "Ideally it could be open within the app itself so that I can read/remember what any section does "
              "without losing focus of what I am doing so that I can have the canvas, properties, etc visible and "
              "the documentation open maybe to the far-rigtht as a column so that the work and the documentation all "
              "of it is on sight")
    p.idea("Close the Documentation column, and the window, Blocks and Properties are as wide as they were before "
           "you opened it, also in a window too narrow for the column beside them.")
    p.idea("This was found on the Windows test computer: in a narrow window, closing the column left the Blocks "
           "panel narrower than before.")
    p.idea("When the column opens next to Properties, it first notes how wide the window, Blocks and Properties are. "
           "In a wide window, the column takes its room from the canvas. In a narrow one, the canvas has too little "
           "to spare, so Qt, the toolkit the editor is built with, makes room the way it does for any panel: it "
           "narrows the canvas as far as it goes, then Blocks, then widens the window. When you close the column, "
           "Blocks and Properties get their widths back, and a window Qt widened narrows back to its width.")
    p.idea("If you widen the window yourself while the column is open, closing the column leaves the window as wide "
           "as you made it.")
    p.why_simplest("Three widths, noted as the column is asked to open and given back when it closes: that is all it "
                   "keeps. They are noted before Qt lays the window out with the column, so they are the widths you "
                   "had, not the ones Qt made to fit it.")
    p.why_simplest("The window is narrowed back only when Qt widened it: when it is wider than before, but no wider "
                   "than it must be with the column open. Wider than that, you widened it yourself, and it keeps "
                   "the width you gave it.")
    p.alternative("Noting the widths once the column shows, as before this fix",
                  "By then Qt has already made room for it: Blocks is narrower and the window wider, and those are "
                  "the widths it would note. Only Properties, which Qt had not touched, got its width back: the "
                  "fault this change fixes.")
    p.alternative("Saving the whole window layout before the column opens, and putting it back on closing",
                  "Qt can save and restore it, but that would also undo what you changed while reading: a Log you "
                  "made taller, a panel you moved. And the window's own width is not part of that layout: it would "
                  "still have to be put back on its own.")
    p.alternative("Giving the column only the room the canvas can spare",
                  "Then nothing else would change, but in a narrow window the canvas has next to nothing to spare, "
                  "and the column would open too narrow to read. Qt's own way gives it a width you can read.")
    p.fits("Opening the column is as it was: when the canvas has room, the column takes its room from the canvas, and "
           "Properties keeps its width; when it has not, Qt makes room as it does for any panel. Only closing "
           "changed: it now puts back everything opening took.")
    p.fits("F1, Help > Documentation and Learn more › on a card all open the same column, so closing it gives the "
           "widths back whichever of them opened it.")
    flow = write_flow(tmp_path / "archive.yaml", FLOW)

    # --- a narrow window -------------------------------------------------------------------------------------
    p.step("Open archive.yaml, click archive_sales, and make the window narrow, as on a small screen. Open it with "
           "File > Open…; archive_sales is its Copy Files block, so Properties shows its settings.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    menu_item(file_menu, "Open…").trigger()
    settle()
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    at = tab.view.mapFromScene(tab.scene.nodes["archive_sales"].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2)))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    settle()
    p.check("Properties shows archive_sales", win.properties.block_id == "archive_sales")
    blocks, canvas, properties, column = blocks_panel(win), win.centralWidget(), win.inspector, docs_dock(win)
    panels = [blocks, canvas, properties]
    # Narrower than the window must be with the column open, whatever widths the system's fonts give the panels.
    narrowest, column_narrowest = win.minimumWidth(), column.minimumSizeHint().width()
    narrow = narrowest + column_narrowest // 2
    p.note(f"The proof makes the window {narrow} pixels wide, as dragging its edge would: "
           f"{narrow - narrowest} pixels wider than the narrowest it can be ({narrowest} pixels), less than the "
           f"{column_narrowest} the column needs at its narrowest. So the column has no room beside the panels, and "
           "narrowing Blocks alone cannot make enough: the window has to widen too, whatever widths the system's "
           "fonts give the panels.")
    resize(win, qtbot, narrow)
    before = widths(win)
    p.shot(win, f"① Blocks, ② the canvas and ③ Properties, side by side in the window {narrow} pixels wide: "
                f"{before['blocks']}, {before['canvas']} and {before['properties']} pixels wide. The close-up shows "
                "the top of Blocks and the canvas.",
           highlight=panels, badges=True, zoom=close_up(win))
    p.check(f"The window is {narrow} pixels wide, with ① Blocks ({before['blocks']} px), ② the canvas "
            f"({before['canvas']} px) and ③ Properties ({before['properties']} px) side by side, the column closed",
            before["window"] == narrow and all(w.isVisible() for w in panels) and not column.isVisible()
            and x_of(blocks) < x_of(canvas) < x_of(properties))

    p.step("Press F1 to open the Documentation column. It opens at the far right.")
    press_f1(win, qtbot, shown=True)
    opened = widths(win)
    p.shot(win, f"④ The column is open at the far right, {opened['column']} pixels wide. ② The canvas had too little "
                f"room to spare for it, so Qt made room: {made_room(before, opened)}. In the close-up, ① Blocks is "
                f"{opened['blocks']} pixels wide (it was {before['blocks']}).",
           highlight=[*panels, column], badges=True, zoom=close_up(win))
    p.check("④ The column is open, docked right of ③ Properties, at the window's right edge",
            column.isVisible() and not column.isFloating() and x_of(column) > x_of(properties)
            and win.width() - column.geometry().right() < 20)
    p.check(f"There was no room beside the panels, and Qt made some, from ① Blocks and by widening the window: "
            f"{made_room(before, opened)}",
            opened["window"] > before["window"] and opened["blocks"] < before["blocks"])

    p.step("Press F1 again to close the column.")
    press_f1(win, qtbot, shown=False)
    closed = widths(win)
    p.note("Before this fix, the column noted only how wide Properties was, and only once Qt had already made room "
           "for it. So closing it in a narrow window gave Properties its width back, but left Blocks narrower, and "
           "the window as wide as Qt had made it. On the Windows test computer, Blocks went from 151 to 90 pixels.")
    p.shot(win, f"The window is {closed['window']} pixels wide again, and ① Blocks, ② the canvas and ③ Properties "
                f"have their widths back: {closed['blocks']}, {closed['canvas']} and {closed['properties']} pixels, "
                "as in step 1. The close-up shows ① Blocks as wide as in step 1.",
           highlight=panels, badges=True, zoom=close_up(win))
    p.check("The column is closed", not column.isVisible())
    p.check(back("window", before, opened, closed), closed["window"] == before["window"])
    p.check("① " + back("blocks", before, opened, closed), closed["blocks"] == before["blocks"])
    p.check("② " + back("canvas", before, opened, closed), closed["canvas"] == before["canvas"])
    p.check("③ " + back("properties", before, opened, closed), closed["properties"] == before["properties"])

    # --- a wide window, for comparison ---------------------------------------------------------------------------
    p.step(f"Make the window {WINDOW[0]} pixels wide again, and press F1.")
    resize(win, qtbot, WINDOW[0])
    wide = widths(win)
    press_f1(win, qtbot, shown=True)
    wide_opened = widths(win)
    p.shot(win, f"In the wide window, the canvas has room: ④ the column opens beside the panels, "
                f"{wide_opened['column']} pixels wide, and only ② the canvas is narrower (from {wide['canvas']} to "
                f"{wide_opened['canvas']} pixels). The window, ① Blocks and ③ Properties keep their widths.",
           highlight=[*panels, column], badges=True)
    p.check("④ The column is open, docked right of ③ Properties, at the window's right edge",
            column.isVisible() and not column.isFloating() and x_of(column) > x_of(properties)
            and win.width() - column.geometry().right() < 20)
    p.check(f"It took its room from ② the canvas alone ({wide['canvas']} → {wide_opened['canvas']} px): the window "
            f"({wide['window']} px), ① Blocks ({wide['blocks']} px) and ③ Properties ({wide['properties']} px) kept "
            "their widths",
            wide_opened["canvas"] < wide["canvas"]
            and all(wide_opened[k] == wide[k] for k in ("window", "blocks", "properties")))

    p.step("Press F1 again to close the column.")
    press_f1(win, qtbot, shown=False)
    wide_closed = widths(win)
    p.shot(win, f"The column is closed: ② the canvas is {wide_closed['canvas']} pixels wide again, and the window, "
                "① Blocks and ③ Properties are as they were.",
           highlight=panels, badges=True)
    p.check("The column is closed", not column.isVisible())
    p.check(f"The window, ① Blocks, ② the canvas and ③ Properties are as wide as before the column opened: "
            f"{wide['window']}, {wide['blocks']}, {wide['canvas']} and {wide['properties']} pixels",
            wide_closed == wide)

    p.step("Compare the widths in the two windows.")
    p.text(f"In the window {narrow} pixels wide (steps 1 to 3), in pixels: before you pressed F1, with the column "
           "open, and once you closed it", table(before, opened, closed))
    p.text(f"In the window {WINDOW[0]} pixels wide (steps 4 and 5)", table(wide, wide_opened, wide_closed))
