"""Proof: every block shows its own icon, white on its group colour, on the canvas and in the Blocks list. How proofs
are written: README.md here."""

import os
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import numpy as np
import yaml
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPalette, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QToolTip, QWidget

from proofs.conftest import ZOOM_MARGIN, choose, right_click
from proofs.test_proof_python_new_block import drag_block, from_menu, open_flow
from proofs.test_proof_python_reusable_block import item_rect, pan, retype, tree_item
from taskloom.editor.canvas import HEADER_H, NODE_W, NodeItem
from tests.test_block_icons import (DIFFERENT, GROUP_COLORS, MIN_INK, NOTES, OUR_DRAWINGS, USER_CREATED, as_bitmaps,
                                    blur, carries_tabler_licence, differing, drawing, has_icon, ink, pixels, row, rows,
                                    unlike, unlike_listed)
from tests.test_inspect_mermaid import FAKE_PYINSTALLER, archive_files, fake_package, on_path, spec_build
from tests.test_paths_editor_editing import labelled, press

REPO = Path(__file__).resolve().parents[2]
ICONS = REPO / "taskloom" / "editor" / "icons"  # where the drawings are kept, with Tabler Icons' licence
ASKED = ("Lets add a small unique icon to identify each block while keeping their background group colors ... You "
         "will probably need to generate them in order to have a cohesive visual design, I am ok with you taking "
         "creative liberties that make my suggestions better. Remember this is only for the icons, the block names "
         "are still shown as before.")
LONG = "write_the_monthly_sales_report_for_every_region"
# The flow: one block of each kind that comes with Taskloom, in five parts of two columns, row by row.
PARTS = {
    "Start, Database, Debug and Email": [[("start_by_hand", "trigger.manual"), ("every_morning", "trigger.schedule")],
                                         [("load_sales", "db.run_query"), ("show_totals", "logic.inspect")],
                                         [("email_report", "email.send")]],
    "Data": [[("sum_by_region", "data.duckdb_sql"), ("clean_rows", "data.polars")],
             [("read_prices", "data.read_file"), (LONG, "data.write_file")]],
    "Logic": [[("add_tax", "logic.python"), ("big_order", "logic.if")],
              [("wait_a_minute", "logic.wait"), ("ask_region", "logic.ask_user")],
              [("split_regions", "logic.splitter"), ("join_regions", "logic.merger")]],  # (below: your own, later)
    "Servers": [[("run_script", "servers.ssh_command"), ("check_server", "servers.health_check")],
                [("start_server", "servers.start_process"), ("stop_server", "servers.stop_process")],
                [("fetch_logs", "servers.remote_extract"), ("call_orders", "servers.api_request")]],
    "Files and Reports": [[("copy_backup", "files.copy"), ("sales_chart", "reports.chart")],
                          [("move_old_files", "files.move"), ("report_page", "reports.html_template")],
                          [("delete_temp", "files.delete"), ("to_excel", "reports.excel")]],
}
CONFIG = {"add_tax": {"inputs": ["prices"], "code": "output = [round(p * 1.2, 2) for p in prices]"}}
COLUMN, ROW_GAP, PART_GAP = NODE_W + 32, 12, 56  # (on the canvas, at 100%)
ZOOM = 110  # each part fits the canvas at this zoom, under the zoom box
MINE, MINE_TITLE = "sales_tax", "Sales tax"  # the block of your own, made from add_tax
OWN = [("start_by_hand", "a play button in a circle, with ×1"), ("show_totals", "a magnifying glass, [ ] in its lens"),
       ("big_order", "“== ?”"), ("sum_by_region", "a duck"), ("clean_rows", "a polar bear"),
       ("move_old_files", "a dashed page, an arrow, a page")]
SATELLITES = [("start_server", "a satellite, with play"), ("stop_server", "a satellite, with stop"),
              ("fetch_logs", "a satellite, with download")]
LOOKS = {  # what each part's blocks show
    "Start, Database, Debug and Email": (
        "Start (green): start_by_hand, a Manual Start, shows a play button in a circle with ×1, Taskloom's own "
        "drawing; every_morning, a Schedule, a calendar with a clock. Database (blue): load_sales, a Run Query, a "
        "database. Debug (amber): show_totals, an Inspect, a magnifying glass with [ ] in its lens, Taskloom's own. "
        "Email (rose): email_report, a Send Email, an envelope with an arrow."),
    "Data": (
        "Data (teal): sum_by_region, a DuckDB SQL, shows a duck, and clean_rows, a Polars Transform, a polar bear: "
        "Taskloom's own drawings. read_prices, a Read File, shows a page with an arrow going out of it; ① the Write "
        "File, a page with a pencil. Its long name starts right of the icon and is cut short with … at the block's "
        "edge, as before."),
    "Logic": (
        "Logic (purple): add_tax, a Python Code, shows Python's logo in one colour; big_order, an If, “== ?”, "
        "Taskloom's own drawing; wait_a_minute, a Wait, a stopwatch; ask_region, an Ask User, a person with a "
        "question mark; split_regions, a Splitter, arrows that split; join_regions, a Merger, arrows that join."),
    "Servers": (
        "Servers (olive): run_script, an SSH Command, shows a terminal; check_server, a Health Check, a stethoscope. "
        "① start_server, stop_server and fetch_logs (Start Remote Process, Stop Remote Process, Remote Extract) share "
        "a satellite, told apart by the badge at its bottom right: play, stop, download. call_orders, an API "
        "Request, shows the letters API."),
    "Files and Reports": (
        "Files (grey): copy_backup, a Copy Files, shows two pages; move_old_files, a Move Files, a dashed page, an "
        "arrow and a page (the file leaves where it was), Taskloom's own; delete_temp, a Delete Files, a bin with an "
        "×. Reports (ochre): sales_chart, a Chart, a pie chart; report_page, an HTML Template, a page with < >; "
        "to_excel, a To Excel, a page with a table."),
}


# --- the flow and the canvas ------------------------------------------------------------------------------------------

def write_every_block(path: Path, registry) -> tuple[dict, QPointF]:
    """The flow file: each part's blocks in two columns, three parts above two. Its parts (each a list of block
    names) and the free place in Logic, below split_regions."""
    def height(name, type_id):
        return NodeItem(name, {"type": type_id, "config": CONFIG.get(name, {})}, registry).height

    def part_height(grid):
        return sum(max(height(*b) for b in line) for line in grid) + ROW_GAP * (len(grid) - 1)

    blocks, parts, free = {}, {}, None
    for i, (title, grid) in enumerate(PARTS.items()):
        x0 = (i % 3) * (COLUMN + NODE_W + PART_GAP)
        y = 0 if i < 3 else max(part_height(PARTS[t]) for t in list(PARTS)[:3]) + PART_GAP
        parts[title] = []
        for line in grid:
            for column, (name, type_id) in enumerate(line):
                blocks[name] = {"type": type_id, **({"config": CONFIG[name]} if name in CONFIG else {}),
                                "ui": {"x": x0 + column * COLUMN, "y": y}}
                parts[title].append(name)
            y += max(height(*b) for b in line) + ROW_GAP
        if title == "Logic":
            free = QPointF(x0, y)
    path.write_text(yaml.safe_dump({"name": "every-block", "blocks": blocks, "edges": []}, sort_keys=False),
                    encoding="utf-8")
    return parts, free


def type_zoom(tab, percent: int):
    """Click the zoom box at the top right of the canvas, type a zoom and press Enter."""
    box = tab.view.zoom_bar.percent
    QTest.mouseClick(box, Qt.LeftButton)
    QTest.keyClicks(box, str(percent))
    QTest.keyClick(box, Qt.Key_Return)
    QApplication.processEvents()


def area(tab, blocks) -> QRectF:
    rect = QRectF()
    for b in blocks:
        rect |= tab.scene.nodes[b].sceneBoundingRect()
    return rect


def show_part(tab, blocks):
    """Drag the canvas with the middle mouse button until these blocks are in the middle of it, under the zoom box:
    clear of it, and of a close-up's margin around them when there is room."""
    view, rect = tab.view, area(tab, blocks)
    zoom = view.zoom_percent() / 100
    canvas, bar = view.viewport().height(), view.zoom_bar.geometry().bottom() - view.viewport().geometry().top()
    height = rect.height() * zoom
    top = bar + ZOOM_MARGIN + 2
    if top + height + ZOOM_MARGIN > canvas:  # (no room for both margins: the part stays clear of the zoom box)
        top = max(bar + 8, canvas - height - ZOOM_MARGIN)
    top = max(top, (canvas - height) / 2)
    pan(tab, QPointF(rect.center().x(), rect.top() + (canvas / 2 - top) / zoom))
    shown = view.mapFromScene(rect).boundingRect()
    assert view.viewport().rect().contains(shown) and shown.top() > bar, (
        f"the part does not fit the canvas at {view.zoom_percent():.0f}%: {shown} in {view.viewport().rect()}")


def deselect(tab, blocks):
    """Click the empty canvas just left of these blocks."""
    rect = area(tab, blocks)
    at = tab.view.mapFromScene(QPointF(rect.left() - PART_GAP / 2, rect.center().y()))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()


def painted(tab, block: str, scale: float) -> QImage:
    """A block's header as the canvas paints it on a screen scaled to `scale` (2: 200%)."""
    QApplication.processEvents()
    node = tab.scene.nodes[block]
    image = QImage(round(NODE_W * scale), round(HEADER_H * scale), QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(scale)
    image.fill(QApplication.palette().color(QPalette.Base))
    painter = QPainter(image)
    painter.setRenderHints(tab.view.renderHints())
    tab.scene.render(painter, QRectF(0, 0, NODE_W, HEADER_H), QRectF(node.scenePos(), QSizeF(NODE_W, HEADER_H)))
    painter.end()
    return image


def icon(tab, block: str, color: str, scale: float = 2.0) -> np.ndarray:
    """How much of the white drawing covers each pixel of the header, on `color` (0 elsewhere): its icon."""
    return ink(pixels(painted(tab, block, scale)), color)


def name_columns(rgb: np.ndarray, color: str, edge: int, side: int) -> np.ndarray:
    """The columns of a header's name: its pixels nearer the text's colour than the background and the group colour,
    `edge` pixels in from the top and bottom, `side` from the left and right (the block's border)."""
    palette = QApplication.palette()
    to = [np.abs(rgb - np.array(QColor(c).getRgb()[:3], float)).sum(axis=2)
          for c in (palette.color(QPalette.Text), palette.color(QPalette.Base), QColor(color))]
    near = (to[0] < to[1]) & (to[0] < to[2])
    near[:edge], near[-edge:], near[:, :side], near[:, -side:] = False, False, False, False
    return np.nonzero(near)[1]


def left_of_its_name(tab, block: str, color: str, scale: float = 2.0) -> bool:
    """The header shows a white icon on `color`, and right of it the name, which stays inside the block."""
    rgb = pixels(painted(tab, block, scale))
    a = ink(rgb, color)
    if not has_icon(a, scale) or a.max() < 0.9:  # (no white)
        return False
    xs = name_columns(rgb, color, round(3 * scale), round(2 * scale))
    return xs.size > 20 * scale and xs.min() > np.nonzero(a > 0.3)[1].max() and xs.max() < (NODE_W - 3) * scale


def group_color(registry, block_type: str) -> str:
    """The colour a block's icon is drawn on: its group's, or User Created's for a block of your own."""
    if registry.sources.get(block_type) != "built-in":
        return USER_CREATED
    return GROUP_COLORS[registry.blocks[block_type].category]


def folder_color(folder) -> str:
    """The colour of a group of the Blocks list."""
    group = folder.text(0).split("  (")[0]
    return NOTES if group == "Notes" else USER_CREATED if group == "User Created" else GROUP_COLORS[group]


def square(image: QImage, color: str, pad: int) -> QRect:
    """Where the square of `color` is in `image` (in its pixels), and `pad` pixels around it."""
    ys, xs = np.nonzero(np.abs(pixels(image) - np.array(QColor(color).getRgb()[:3], float)).max(axis=2) <= 10)
    return QRect(int(xs.min()) - pad, int(ys.min()) - pad, int(np.ptp(xs)) + 1 + 2 * pad,
                 int(np.ptp(ys)) + 1 + 2 * pad)


def text_drawing(text: str, size: int = 96) -> np.ndarray:
    """`text`, written white on black in the editor's font, bold: to compare an icon with."""
    image = QImage(size * 2, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("black"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    font = QFont(QApplication.font())
    font.setPixelSize(size * 5 // 8)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(QRectF(0, 0, size * 2, size), Qt.AlignCenter, text)
    painter.end()
    return pixels(image)[:, :, 0] / 255


def list_row(win, item) -> QRect:
    """A row of the Blocks list, in a screenshot of the window, so that its outline hugs it (rows touch)."""
    return item_rect(win, item).adjusted(-2, 5, 2, -5)


def sheet(cells, columns: int, footer: str) -> tuple[QWidget, list[list[QWidget]]]:
    """A window of pictures, each with its text under it, `columns` to a row: each picture with its text."""
    window = QWidget()
    window.setAutoFillBackground(True)
    font = QFont(QApplication.font())
    font.setPointSizeF(font.pointSizeF() * 1.4)  # (the window is shown smaller in the PDF)
    window.setFont(font)
    grid = QGridLayout(window)
    grid.setContentsMargins(20, 20, 20, 20)
    grid.setHorizontalSpacing(32)
    grid.setVerticalSpacing(6)
    shown = []
    for i, (pixmap, text) in enumerate(cells):
        picture, label = QLabel(), QLabel(text)
        picture.setPixmap(pixmap)
        label.setWordWrap(True)
        label.setFixedWidth(pixmap.width())
        grid.addWidget(picture, (i // columns) * 2, i % columns, Qt.AlignLeft | Qt.AlignBottom)
        grid.addWidget(label, (i // columns) * 2 + 1, i % columns, Qt.AlignLeft | Qt.AlignTop)
        grid.setRowMinimumHeight((i // columns) * 2 + 1, label.heightForWidth(pixmap.width()) + 14)
        shown.append([picture, label])
    grid.addWidget(QLabel(footer), grid.rowCount(), 0, 1, columns)
    window.show()
    QTest.qWait(50)
    return window, shown


# --- the story --------------------------------------------------------------------------------------------------------

def test_every_block_shows_its_own_icon(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Every block shows its own icon", ASKED)
    p.idea("Each block now shows a small white icon left of its name, on its group's colour: on the canvas, in the "
           "block's header, and in the Blocks list, next to its name. Each of the 29 blocks that come with Taskloom "
           "has its own; a block of your own (User Created) shows a puzzle piece, and Text, in the Blocks list, "
           "shows Tt.")
    p.idea("The icon takes the place of the small coloured square that was left of each name. On the canvas the "
           "square is a little larger now (22 pixels instead of 14; 18 in the Blocks list) and has the block's drawing "
           "on it in white, so each block keeps its group's colour where it was. The names are shown as before, 4 "
           "pixels further right; a long name is still cut short with … at the block's edge.")
    p.idea("The icons are line drawings in one style. Most are from Tabler Icons, a free set under the MIT licence; "
           "six are Taskloom's own, drawn in the same style: the duck of DuckDB SQL, the polar bear of Polars "
           "Transform, the pages of Move Files, Manual Start's play button with ×1, Inspect's magnifying glass with "
           "[ ] in its lens, and If's “== ?”. The three Remote blocks (Start Remote Process, Stop Remote Process, "
           "Remote Extract) share a satellite, told apart by a small badge: play, stop, download. These are the "
           "icons you approved on 30 October.")
    p.idea("They are drawings, not pictures: Taskloom draws them at the size and sharpness of your screen, so they "
           "stay crisp at 150% and 200%, and they are the same white drawings in the Light theme and the Dark one.")
    p.why_simplest("One list says which drawing each kind of block has, and one piece of the editor draws a block's "
                   "icon from it. The canvas and the Blocks list both use that piece, so they always show the same "
                   "icon. Any block of your own gets the puzzle piece, with nothing to set up.")
    p.why_simplest("A badge is the same drawing step used twice: a second drawing, half the size, on a circle of the "
                   "group colour at the icon's bottom right. That is how the three Remote blocks share one satellite, "
                   "and the Loop shows a list with a going-round arrow.")
    p.why_simplest("The drawings are small files that come with Taskloom, kept in its folder with Tabler Icons' "
                   "licence. The editor reads them from there: nothing is fetched from the internet, so it shows "
                   "them the same with no connection.")
    p.alternative("Pictures (PNG files) of each icon",
                  "A picture is made for one size. On a screen set to 150% or 200% it is enlarged and looks blurred "
                  "(step 12 shows the difference), unless each icon is made again for each size. A drawing is drawn "
                  "sharp at any size, from one file.")
    p.alternative("An icon font, as many websites use",
                  "A font has to be loaded before it can be used, and Taskloom's own drawings (the duck, the polar "
                  "bear, …) would have to be made into a font too, with badges as letters drawn over letters. Drawing "
                  "files need neither.")
    p.alternative("Coloured icons, or the products' own logos (DuckDB's duck, Polars' bear)",
                  "Their colours would compete with the group colours, which you asked to keep. White on the group "
                  "colour keeps the groups easy to tell apart, and all the icons in one style. The one logo used, "
                  "Python's (your choice), is a one-colour line drawing from Tabler Icons, white like the rest.")
    p.alternative("Fetching the icons from Tabler's website when the editor needs them",
                  "The editor would need the internet to show its blocks, and would show none behind a firewall or "
                  "offline. The files come with Taskloom.")
    p.fits("The group colours are the ones the blocks already had: the icon is drawn on them, so the canvas and the "
           "Blocks list keep one colour for each group, as before.")
    p.fits("The new Theme setting (Tools > Settings > Theme) changes the editor's colours, not the group colours: the "
           "icons are the same white drawings on the same colours in Light and in Dark.")
    p.fits("Blocks of your own, written by hand or made with Create Reusable block…, show the puzzle piece: they "
           "are under User Created in the Blocks list, in its colour, and Taskloom cannot know what they do.")
    p.fits("The drawings ship inside Taskloom, in the wheel and in the Windows build, with Tabler Icons' licence "
           "(README's License section names it too). Splitter and Merger, which came later, show Tabler's "
           "arrows-split and arrows-join. The Loop is a box around blocks, not one: its own proof shows its icon.")
    registry = win.registry
    # (but the Loop and the Session: boxes drawn around blocks, which their own proofs show)
    built_in = sorted(t for t, source in registry.sources.items()
                      if source == "built-in" and t not in ("logic.loop", "servers.api_session"))
    flow = tmp_path / "every-block.yaml"
    parts, free = write_every_block(flow, registry)
    titles = list(parts)

    # --- one block of each kind ---------------------------------------------------------------------------------------
    p.step("Open the flow every-block.yaml (File > Open…). It has one block of each of the 27 kinds that come with "
           "Taskloom but the Loop and the Session, in five parts. They are not connected: the flow is only to look "
           "at them.")
    tab = open_flow(win, monkeypatch, flow)
    tab.scene.clearSelection()
    blocks = [b for part in parts.values() for b in part]
    kind = {b: tab.doc.blocks[b]["type"] for b in blocks}
    colors = {b: group_color(registry, kind[b]) for b in blocks}
    p.check("The flow opens, with one block of each of the 27 kinds that come with Taskloom but the Loop and the "
            "Session", tab.doc.path == flow.resolve() and len(built_in) == 27 and sorted(kind.values()) == built_in)
    canvas = tab.view.viewport().rect()
    p.check("The canvas shows the whole flow: all 27 blocks are in view", all(
        canvas.contains(tab.view.mapFromScene(tab.scene.nodes[b].sceneBoundingRect()).boundingRect()) for b in blocks))
    p.shot(win, "The canvas zooms out to show the whole flow: ① Start, Database, Debug and Email; ② Data; ③ Logic; "
                "④ Servers; ⑤ Files and Reports. Each block shows an icon left of its name, on its group's colour; "
                "the Blocks list, on the left, shows them too. The next steps zoom in on each part.",
           highlight=[parts[t] for t in titles], badges=True)
    icons = {b: icon(tab, b, colors[b]) for b in blocks}  # as the canvas paints them at 200%, to compare
    missing = [b for b in blocks if not left_of_its_name(tab, b, colors[b])]
    p.check("Each of the 27 blocks shows a white icon on its group's colour, left of its name, and the name stays "
            "inside the block" + (f" (not: {', '.join(missing)})" if missing else ""), missing == [])
    alike = [f"{a} and {b}" for i, a in enumerate(blocks) for b in blocks[i + 1:]
             if differing(icons[a], icons[b]) < DIFFERENT]
    p.check("No two of the 27 show the same icon" + (f" (alike: {'; '.join(alike)})" if alike else ""), alike == [])

    # --- each part, closer --------------------------------------------------------------------------------------------
    for title in titles:
        if title == titles[0]:
            p.step(f"Zoom in on the first part: type {ZOOM} in the zoom box at the top right of the canvas and press "
                   f"Enter, then drag the canvas with the middle mouse button to {title}.")
            type_zoom(tab, ZOOM)
            p.check(f"The canvas is at {ZOOM}%", round(tab.view.zoom_percent()) == ZOOM)
        elif title == "Data":
            p.step(f"Drag the canvas to the {title} part. Its Write File block has a long name, {LONG}.")
        else:
            p.step(f"Drag the canvas to the {title} part.")
        show_part(tab, parts[title])
        if title == "Data":
            header = tab.view.mapFromScene(QRectF(tab.scene.nodes[LONG].scenePos(), QSizeF(NODE_W, HEADER_H)))
            mark = [header.boundingRect().translated(tab.view.viewport().mapTo(win, QPoint(0, 0)))]
        elif title == "Servers":
            mark = [[b for b, _ in SATELLITES]]
        else:
            mark = []
        p.shot(win, LOOKS[title], highlight=mark, badges=bool(mark), zoom=parts[title])
        if title == "Data":
            p.check(f"① {LONG} shows its icon on the Data colour, and right of it its name, which stays inside the "
                    "block", left_of_its_name(tab, LONG, GROUP_COLORS["Data"]))
        if title == "Servers":
            ys, xs = np.nonzero(icons["start_server"] > 0)
            corner = np.zeros(icons["start_server"].shape, bool)  # the icon's bottom right: where the badge is
            corner[int(ys.min() + 0.45 * np.ptp(ys)):, int(xs.min() + 0.45 * np.ptp(xs)):] = True
            pairs = [(a, b) for i, (a, _) in enumerate(SATELLITES) for b, _ in SATELLITES[i + 1:]]
            apart = [np.abs(icons[a] - icons[b]) > 0.5 for a, b in pairs]
            p.check("① The three show the same satellite: outside its bottom right corner, their icons are the same "
                    "to the pixel", all((d & ~corner).sum() <= 2 for d in apart))
            p.check("① Each has its own badge there: in that corner, each two of them differ",
                    all((d & corner).sum() >= DIFFERENT for d in apart))

    # --- Taskloom's own drawings, at 300% -----------------------------------------------------------------------------
    p.step("Look closer at the drawings Taskloom made, and at the satellite. Type 300 in the zoom box and press "
           "Enter, then drag the canvas to each of those blocks in turn.")
    type_zoom(tab, 300)
    p.check("The canvas is at 300%", round(tab.view.zoom_percent()) == 300)
    cells = []
    for block, what in OWN + SATELLITES:
        node = tab.scene.nodes[block]
        pan(tab, node.mapToScene(QPointF(NODE_W / 2, HEADER_H / 2)))
        seen = tab.view.mapFromScene(QRectF(node.scenePos(), QSizeF(NODE_W, HEADER_H))).boundingRect()
        image = tab.view.viewport().grab(seen.adjusted(-4, -4, 4, 4)).toImage()
        name_end = int(name_columns(pixels(image), colors[block], 12, 12).max())
        cells.append((QPixmap.fromImage(image.copy(0, 0, min(name_end + 24, image.width()), image.height())),
                      f"{registry.blocks[kind[block]].title}: {what}"))
    window, shown = sheet(cells, 3, "Each picture is the left of a block's header, as the canvas shows it at 300%.")
    try:
        p.shot(window, "① Taskloom's own drawings, in the style of Tabler's: Manual Start, Inspect, If, DuckDB SQL, "
                       "Polars Transform and Move Files. ② Tabler's satellite, with a badge for each of the three "
                       "Remote blocks.", highlight=[shown[:6], shown[6:]], badges=True)
    finally:
        window.close()
        window.deleteLater()
    p.note("At 300% the canvas shows about one block at a time, so the proof puts the nine headers it showed side by "
           "side in one picture.")
    by_kind = {kind[b]: a for b, a in icons.items()}
    unlike_own = [t for t, body in OUR_DRAWINGS.items()
                  if min(by_kind, key=lambda k: unlike(by_kind[k], drawing(body))) != t]
    p.check("① Of the 27 icons, the one most like each drawing you approved (the duck, the polar bear, the pages "
            "with an arrow, the play button with ×1, the magnifying glass with [ ]) is that block's"
            + (f" (not: {unlike_own})" if unlike_own else ""), unlike_own == [])
    written = text_drawing("== ?")
    p.check("① Of the 27, If's icon is the one most like “== ?” written out",
            min(by_kind, key=lambda k: unlike(by_kind[k], written)) == "logic.if")

    # --- a block of your own ------------------------------------------------------------------------------------------
    p.step(f"Make a block of your own from add_tax, a Python Code block. Type {ZOOM} in the zoom box, press Enter and "
           f"drag the canvas back to the Logic part; right-click add_tax, choose Create Reusable block…, name it "
           f"{MINE_TITLE} and press OK. Then double-click {MINE_TITLE}, now under User Created in the Blocks list, "
           f"drag the new block, {MINE}, to the free place below split_regions, and save the flow (File > Save).")
    type_zoom(tab, ZOOM)
    show_part(tab, parts["Logic"])

    def name_it(dialog):
        retype(dialog.name, MINE_TITLE)
        retype(dialog.what, "Adds 20% tax to each price.")
        press(dialog, "OK")

    p.while_open(lambda: right_click(tab, "add_tax"),
                 lambda menu: p.while_open(lambda: choose(menu, "Create Reusable block…"), name_it))
    QApplication.processEvents()
    item = tree_item(win, MINE_TITLE)
    p.check(f"{MINE_TITLE} is listed under User Created in the Blocks list",
            item is not None and item.parent() is not None and item.parent().text(0).startswith("User Created"))
    tree = win.palette_panel.tree
    tree.scrollToItem(item)
    QApplication.processEvents()
    at = tree.visualItemRect(item).center()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # a double-click: Qt's test one is only
    QTest.mouseDClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # its second click
    QApplication.processEvents()
    p.check(f"Double-clicking it adds a block of your own, {MINE}, to the flow",
            tab.doc.blocks.get(MINE, {}).get("type") == f"user.{MINE}")
    drag_block(tab, MINE, free)
    logic = parts["Logic"] + [MINE]
    deselect(tab, logic)
    from_menu(p, win, "&File", "Save")
    p.check("The flow is saved, with its new block",
            not tab.doc.dirty and MINE in yaml.safe_load(flow.read_text(encoding="utf-8"))["blocks"])
    show_part(tab, logic)
    p.shot(win, f"① {MINE}, the block of your own, shows a puzzle piece on the User Created colour; add_tax, the "
                f"Python Code block it was made from, keeps Python's logo. ② In the Blocks list, {MINE_TITLE} shows "
                "the same puzzle piece, under User Created.",
           highlight=[MINE, list_row(win, item)], badges=True, zoom=logic)
    colors[MINE] = USER_CREATED
    icons[MINE] = icon(tab, MINE, USER_CREATED)
    p.check(f"① {MINE} shows a white icon on the User Created colour, left of its name",
            left_of_its_name(tab, MINE, USER_CREATED))
    like = [b for b in blocks if differing(icons[MINE], icons[b]) < DIFFERENT]
    p.check("① It is none of the 27 icons of the blocks in the flow" + (f" (like: {like})" if like else ""),
            like == [])

    # --- the Blocks list ----------------------------------------------------------------------------------------------
    p.step("Look at the Blocks list, on the left, from its top.")
    tree.scrollToTop()
    QApplication.processEvents()
    groups = rows(win.palette_panel)
    folders = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    duckdb = next(c for c in groups["Data"] if c.text(0) == "DuckDB SQL")
    p.shot(win, "① Each group keeps its plain square of colour, as before. ② Each block shows its icon on a small "
                "square of its group's colour: the icon its header shows on the canvas, here DuckDB SQL's duck.",
           highlight=[list_row(win, folders[0]), list_row(win, duckdb)], badges=True,
           zoom=[item_rect(win, folders[0]), item_rect(win, groups["Email"][-1])])
    title_to_block = {registry.blocks[kind[b]].title: b for b in blocks} | {MINE_TITLE: MINE}
    listed, plain, wrong = {}, [], []  # each row's icon, as the Blocks list paints it at 200%, to compare
    for folder in folders:
        color = folder_color(folder)
        painted_row = row(win.palette_panel, folder)
        if (np.abs(painted_row - QColor(color).getRgb()[:3]).max(axis=2) <= 10).sum() < 50 or \
                ink(painted_row, color).sum() > MIN_INK:
            plain.append(folder.text(0))
        for child in [folder.child(j) for j in range(folder.childCount())]:
            listed[child.text(0)] = ink(row(win.palette_panel, child), color)
            if child.text(0) in title_to_block and title_to_block[child.text(0)] != MINE:
                distance = unlike_listed(child, color, icons)
                if min(distance, key=distance.get) != title_to_block[child.text(0)]:
                    wrong.append(child.text(0))
    p.check(f"① Each of the {len(folders)} groups shows a plain square of its colour, with no drawing on it"
            + (f" (not: {plain})" if plain else ""), plain == [])
    p.check("② Each of the 27 blocks in the flow shows, in the list, the icon it shows on the canvas"
            + (f" (not: {wrong})" if wrong else ""), len(listed) == 31 and wrong == [])

    p.step("Scroll the Blocks list down to its end.")
    tree.scrollToBottom()
    QApplication.processEvents()
    text = groups["Notes"][0]
    first = next(f for f in folders if tree.viewport().rect().contains(tree.visualItemRect(f)))
    p.shot(win, f"The rest of the groups, then ① {MINE_TITLE}, under User Created, with the puzzle piece, and ② Text, "
                "under Notes: not a block but words you put on the canvas, with Tt.",
           highlight=[list_row(win, item), list_row(win, text)], badges=True,
           zoom=[item_rect(win, first), item_rect(win, text)])
    distance = unlike_listed(item, USER_CREATED, icons)
    p.check(f"① {MINE_TITLE} shows the puzzle piece: the icon {MINE} shows on the canvas",
            min(distance, key=distance.get) == MINE)
    distance = unlike_listed(text, NOTES, icons)
    p.check("② Text shows Tt, an icon of its own on the Notes colour, like none of the blocks' icons",
            has_icon(listed["Text"], 2.0) and min(distance.values()) > 0.05)

    # --- Dark ---------------------------------------------------------------------------------------------------------
    p.step("Choose Tools > Settings…, set Theme to Dark and press Save.")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save writes Windows' Startup script there: not yours)
    app = QApplication.instance()
    style, style_sheet, palette, tips = app.style().name(), app.styleSheet(), QApplication.palette(), QToolTip.palette()
    try:
        from_menu(p, win, "&Tools", "Settings…",
                  then_in=lambda dialog: (labelled(dialog, "Theme").setCurrentText("Dark"), press(dialog, "Save")))
        QTest.qWait(100)
        tree.scrollToItem(item)
        QApplication.processEvents()
        p.shot(win, f"The editor in the Dark theme. On the canvas, the icons of the Logic part and of {MINE} are the "
                    "same white drawings on the same colours as in Light (step 8), and so are the Blocks list's.",
               zoom=logic)
        p.check("The editor is dark", win.palette().color(QPalette.Window).lightness() < 128
                and tab.view.palette().color(QPalette.Base).lightness() < 128)
        changed = [b for b in icons if differing(icons[b], icon(tab, b, colors[b])) > 2]
        p.check("On the canvas, each of the 29 icons is the same white drawing on the same colour as in Light"
                + (f" (not: {changed})" if changed else ""), changed == [])
        changed = [c.text(0) for f in folders for c in [f.child(j) for j in range(f.childCount())]
                   if differing(listed[c.text(0)], ink(row(win.palette_panel, c), folder_color(f))) > 2]
        p.check("In the Blocks list, each of the 31 icons, Text's too, is the same as in Light"
                + (f" (not: {changed})" if changed else ""), changed == [])
    finally:  # Theme back to System, and the application's look as it was, for what comes next
        win.settings.setValue("theme", "System")
        win.apply_theme()
        if app.style().name() != style:
            QApplication.setStyle(style)
        if app.styleSheet() != style_sheet:
            app.setStyleSheet(style_sheet)
        QApplication.setPalette(palette)
        QToolTip.setPalette(tips)
        QTest.qWait(50)
    p.note("The proof then sets Theme back to System, as it was.")

    # --- crisp on a scaled screen -------------------------------------------------------------------------------------
    p.step("Look at three icons as the canvas draws them on a screen set to 200%, next to the same icons drawn at "
           "100% and then enlarged, as a picture made for 100% is on such a screen.")
    cells = [None] * 6
    for i, block in enumerate(["start_by_hand", "sum_by_region", "fetch_logs"]):
        fine, small = painted(tab, block, 2.0), painted(tab, block, 1.0)
        fine.setDevicePixelRatio(1)
        small.setDevicePixelRatio(1)
        part = square(fine, colors[block], 2)  # (clear of the block's rounded corner)
        part = QRect(part.x() // 2 * 2, part.y() // 2 * 2, (part.width() + 1) // 2 * 2, (part.height() + 1) // 2 * 2)
        enlarged = small.copy(QRect(part.x() // 2, part.y() // 2, part.width() // 2, part.height() // 2)).scaled(
            part.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        title = registry.blocks[kind[block]].title
        for at, (image, how) in enumerate([(fine.copy(part), "drawn for 200%"), (enlarged, "drawn at 100%, enlarged")]):
            bigger = image.scaled(image.width() * 4, image.height() * 4, Qt.IgnoreAspectRatio, Qt.FastTransformation)
            cells[at * 3 + i] = (QPixmap.fromImage(bigger), f"{title}, {how}")
    window, shown = sheet(cells, 3, "Each picture is shown 4 times bigger, pixel for pixel.")
    try:
        p.shot(window, "① Drawn for a 200% screen, the lines are sharp: the ×1, the duck's eye and the download badge "
                       "are clear. ② Drawn at 100% and then enlarged, the same icons are blurred.",
               highlight=[shown[:3], shown[3:]], badges=True)
    finally:
        window.close()
        window.deleteLater()
    p.note("The proof's screen is not scaled, so it has the canvas draw each header as it does on a screen set to "
           "200%: into a picture with two pixels for each one of the screen.")
    blurred = [b for b in icons if not blur(icon(tab, b, colors[b], 2.0))
               < 0.85 * as_bitmaps(icon(tab, b, colors[b], 1.0), icon(tab, b, colors[b], 2.0), 2.0)]
    p.check("① On the canvas, each of the 28 icons is sharper drawn for 200% than drawn at 100% and enlarged"
            + (f" (not: {blurred})" if blurred else ""), blurred == [])
    blurred = []
    for folder in folders:
        for child in [folder.child(j) for j in range(folder.childCount())]:
            small = ink(row(win.palette_panel, child, 1.0), folder_color(folder))
            fine = ink(row(win.palette_panel, child, 2.0), folder_color(folder))
            if not blur(fine) < 0.85 * as_bitmaps(small, fine, 2.0):
                blurred.append(child.text(0))
    p.check("In the Blocks list, each of the 31 icons, Text's too, is sharper drawn for 200% than drawn at 100% and "
            "enlarged" + (f" (not: {blurred})" if blurred else ""), blurred == [])

    # --- in the wheel and the Windows build ---------------------------------------------------------------------------
    p.step("Look inside the wheel (what pip installs) and the Windows build, as CI makes them.")
    drawings = sorted(f.name for f in ICONS.glob("*.svg"))
    licence = "LICENSE-tabler-icons.txt"
    wheel, in_wheel = build_wheel(tmp_path / "wheel")
    shipped = sorted(Path(n).name for n in in_wheel if n.startswith("taskloom/editor/icons/"))
    recipe = [(Path(src), Path(dest)) for src, dest in spec_build(monkeypatch, headless=False)["datas"]]
    archive, in_build = build_windows(tmp_path / "windows")
    p.text("What the wheel and the Windows build hold of the icons",
           f"{wheel}, in taskloom/editor/icons:\n"
           + textwrap.fill(", ".join(shipped), 110, initial_indent="  ", subsequent_indent="  ", break_on_hyphens=False)
           + "\n\n"
           "The Windows build's recipe (packaging/taskloom.spec) gives PyInstaller to copy:\n"
           + "\n".join(f"  the folder {src.relative_to(REPO).as_posix()}, as {dest.as_posix()}"
                       for src, dest in recipe if src == ICONS) + "\n\n"
           f"{archive}, in taskloom/licenses/tabler-icons:\n  LICENSE")
    p.check(f"The wheel has every drawing ({len(drawings)}) and Tabler Icons' MIT licence, next to them",
            shipped == sorted(drawings + [licence])
            and carries_tabler_licence({licence: in_wheel[f"taskloom/editor/icons/{licence}"]}))
    p.check("The Windows build's recipe gives PyInstaller the icons folder, drawings and licence, as "
            "taskloom/editor/icons", (ICONS, Path("taskloom", "editor", "icons")) in recipe)
    p.check("The Windows build's licenses folder has Tabler Icons' MIT licence",
            bool(carries_tabler_licence({"LICENSE": in_build.get("taskloom/licenses/tabler-icons/LICENSE", b"")})))
    p.note("PyInstaller takes minutes to make the Windows build's programs, so the proof runs the build script with "
           "PyInstaller left out, and reads what its recipe gives PyInstaller to copy. CI makes the whole Windows "
           "build in its own job, and tries it out.")


def build_wheel(folder: Path) -> tuple[str, dict[str, bytes]]:
    """The wheel built with uv, as CI has it, from a copy of what it is made of: its name and its files."""
    uv = shutil.which("uv")
    assert uv, "uv is not installed: it builds the wheel, as in CI"
    source = folder / "source"
    shutil.copytree(REPO / "taskloom", source / "taskloom", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy(REPO / name, source / name)
    done = subprocess.run([uv, "build", "--wheel", "--out-dir", str(folder / "out"), str(source)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    [wheel] = (folder / "out").glob("*.whl")
    with zipfile.ZipFile(wheel) as z:
        return wheel.name, {n: z.read(n) for n in z.namelist() if not n.endswith("/")}


def build_windows(folder: Path) -> tuple[str, dict[str, bytes]]:
    """packaging/build.py, run as CI runs it on a copy of the project, with PyInstaller's (minutes long) making of
    the programs left out: its archive's name and files."""
    repo = folder / "repo"
    for name in ("taskloom", "packaging", "examples"):
        shutil.copytree(REPO / name, repo / name, ignore=shutil.ignore_patterns("__pycache__", "out", "dist", "build"))
    for name in ("README.md", "LICENSE"):
        shutil.copy(REPO / name, repo / name)
    fake = fake_package(folder / "fake", "PyInstaller", "")
    (fake / "PyInstaller" / "__main__.py").write_text(FAKE_PYINSTALLER, encoding="utf-8")
    done = subprocess.run([sys.executable, str(repo / "packaging" / "build.py")], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300,
                          env={**os.environ, "PYTHONPATH": on_path(fake)})
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    [archive] = [a for a in (repo / "dist").iterdir() if a.name.endswith((".zip", ".tar.gz"))]
    return archive.name, archive_files(archive)
