"""Each block shows a small icon that identifies it: a white line drawing on its group colour, left of its name, in its
header on the canvas and next to its name in the Blocks list (where Text, which is not a block, shows "Tt"). Any block
of the User Created group shows a puzzle. The icons are drawn from SVGs, so they stay crisp at 150% and 200% screen
scaling, look the same in a light and a dark theme, and ship in the wheel and in the Windows build, with Tabler Icons'
MIT licence. Through the offscreen editor (the canvas as it paints a flow, the Blocks list as it paints its rows), a
wheel built with uv as in CI, and the Windows build's spec and build script. Each test names the point of the
specification (1-4, or Style) it protects.

How the tests see an icon: they read what the editor paints, pixel by pixel. An icon is drawn on its group colour, and
the group colours are unchanged (below). A pixel with that colour somewhere to its left, right, above and below it
(in the header, left of the name; in a row of the Blocks list) is inside the icon, and how far its colour is from the
group colour towards white is how much of the white drawing covers it: its ink, 0 (none) to 1 (all of it). Two icons
are compared by their ink, painted at 200%, pixel by pixel (in the header they are painted at the same place); an icon
is compared with a drawing (or with an icon painted at another size) by their shapes: the ink inside its bounding box,
resampled to a small square.
"""

import gc
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QPoint, QRectF, QSizeF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPixmap, QRegion  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from taskloom.editor.canvas import HEADER_H, NODE_W  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

# The group colours, unchanged by this change (a non-goal): the icons are drawn on them. (Checks came later, with its
# own colour.)
GROUP_COLORS = {"Start": "#1f8a4c", "Database": "#2c5d8f", "Data": "#2f7f7a", "Checks": "#4a5ba8",
                "Reports": "#9a6a1f", "Email": "#a24d6b", "Servers": "#4d6a35", "Files": "#6d7682", "Logic": "#7a4f9e",
                "Debug": "#b7791f"}
USER_CREATED = "#b0563a"
NOTES = "#9aa5b1"  # the Notes group of the Blocks list (Text)

# Our own drawings, as the user approved them (Style): viewBox 0 0 24 24, white strokes of width 2, round caps/joins.
OUR_DRAWINGS = {
    "data.duckdb_sql": '<circle cx="10" cy="9" r="5.5"/><path d="M15 10l5.5 1.2-5.5 2.3"/>'
                       '<circle cx="11" cy="7.8" r="0.6" fill="#fff"/><path d="M3 19c3-2 6-2 9 0s6 2 9 0"/>',
    "data.polars": '<circle cx="6.5" cy="7" r="2.3"/><circle cx="17.5" cy="7" r="2.3"/><circle cx="12" cy="13.5" r="7"/>'
                   '<circle cx="9.5" cy="12" r="0.6" fill="#fff"/><circle cx="14.5" cy="12" r="0.6" fill="#fff"/>'
                   '<ellipse cx="12" cy="16" rx="1.5" ry="1" fill="#fff"/>',
    "files.move": '<rect x="2" y="5" width="7" height="10" rx="1" stroke-dasharray="2 2"/>'
                  '<path d="M10.5 10h3.5m-1.5-2l2 2-2 2"/><rect x="16" y="5" width="7" height="10" rx="1"/>',
    "trigger.manual": '<circle cx="9" cy="9" r="6.5"/><path d="M7.6 6.4v5.2l4-2.6z"/>'
                      '<path d="M13.5 16l3 3m0-3l-3 3M19 15.5l1.5-1.5v7"/>',
    "logic.inspect": '<circle cx="10" cy="10" r="7"/><path d="M15.2 15.2L21 21"/>'
                     '<path d="M8.3 7H7v6h1.3M11.7 7H13v6h-1.3"/>',
}

NAMES = ("WWW", "iii")  # each block twice, under two names: where their headers differ is where the name starts
MIN_INK = 5  # an icon has at least this much ink at 100% (a line drawing of 12-18 px has several times more)
DIFFERENT = 8  # two icons differ in at least this many pixels (at 200%, by more than half their ink)

# Two user blocks: one of their own type, one that replaces the built-in Wait (a user block can replace a built-in).
GREET = '''
from taskloom import Block, ports

class Greet(Block):
    type_id = "my.greet"
    title = "Greet"
    description = "Says hello to whoever you name."
    inputs = {"name": ports.Any()}
    outputs = {"text": ports.Any()}

    def run(self, ctx, name):
        return {"text": f"Hello, {name}!"}
'''
SLOW_WAIT = '''
from taskloom import Block, ports

class SlowWait(Block):
    type_id = "logic.wait"
    title = "Slow Wait"
    description = "Passes its input on, as the built-in Wait does, but this one is yours."
    category = "Logic"
    inputs = {"value": ports.Any(required=False)}
    outputs = {"value": ports.Any()}

    def run(self, ctx, value=None):
        return {"value": value}
'''

TABLER_LICENCE = ("Kuna", "Permission is hereby granted, free of charge",  # Tabler Icons' MIT licence (its author)
                  'THE SOFTWARE IS PROVIDED "AS IS"')


# --- reading what the editor paints ------------------------------------------------------------------------------

def pixels(image: QImage) -> np.ndarray:
    """An image's colours: height x width x (red, green, blue)."""
    image = image.convertToFormat(QImage.Format_RGBA8888)
    w, h = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), np.uint8).reshape(h, image.bytesPerLine())
    return rows[:, :w * 4].reshape(h, w, 4)[:, :, :3].astype(float)


def ink(rgb: np.ndarray, color: str) -> np.ndarray:
    """How much of a white drawing on `color` covers each pixel (0 where there is none, or no `color` around it)."""
    g = np.array(QColor(color).getRgb()[:3], float)
    on = np.abs(rgb - g).max(axis=2) <= 10

    def seen(mask, axis):  # the colour is somewhere before this pixel along that axis
        return np.logical_or.accumulate(mask, axis=axis)

    inside = seen(on, 1) & seen(on[:, ::-1], 1)[:, ::-1] & seen(on, 0) & seen(on[::-1], 0)[::-1]
    white = (rgb - g) @ (255 - g) / ((255 - g) @ (255 - g))
    return np.where(inside, np.clip(white, 0, 1), 0)


def to_image(a: np.ndarray) -> QImage:
    data = np.ascontiguousarray(np.clip(a * 255, 0, 255).astype(np.uint8))
    return QImage(data.tobytes(), data.shape[1], data.shape[0], data.shape[1], QImage.Format_Grayscale8).copy()


def resized(a: np.ndarray, w: int, h: int) -> np.ndarray:
    return pixels(to_image(a).scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))[:, :, 0] / 255


def shape(a: np.ndarray, n: int = 20) -> np.ndarray:
    """The ink inside its bounding box, resampled to n x n: the same drawing at any size gives about the same."""
    ys, xs = np.nonzero(a > 0.3)
    return resized(a[ys.min():ys.max() + 1, xs.min():xs.max() + 1], n, n)


def unlike(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(shape(a) - shape(b)).mean())


def differing(a: np.ndarray, b: np.ndarray) -> int:
    """In how many pixels two icons painted at the same place differ by more than half their ink."""
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    return int((np.abs(a[:h, :w] - b[:h, :w]) > 0.5).sum())


def blur(a: np.ndarray) -> float:
    """Pixels the drawing only partly covers, for each unit of its ink: higher when its lines are blurred."""
    return float(((a > 0.2) & (a < 0.8)).sum() / a.sum())


def drawing(body: str, size: int = 96) -> np.ndarray:
    """The ink of one of our drawings (Style), as the SVG draws it, white on black."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" '
           f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("black"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixels(image)[:, :, 0] / 255


def header(tab, block_id: str, scale: float) -> np.ndarray:
    """The header of a block on the canvas, as the canvas paints it on a screen scaled to `scale` (2: 200%)."""
    QApplication.processEvents()
    node = tab.scene.nodes[block_id]
    image = QImage(round(NODE_W * scale), round(HEADER_H * scale), QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(scale)
    image.fill(QApplication.palette().color(QPalette.Base))
    painter = QPainter(image)
    painter.setRenderHints(tab.view.renderHints())
    tab.scene.render(painter, QRectF(0, 0, NODE_W, HEADER_H), QRectF(node.scenePos(), QSizeF(NODE_W, HEADER_H)))
    painter.end()
    return pixels(image)


def name_start(tab, k: int, scale: float) -> int:
    """Where (in pixels at `scale`) the name starts in the header of the blocks of type number k."""
    one, other = (header(tab, f"{name}{k}", scale) for name in NAMES)
    middle = slice(round(HEADER_H * scale / 4), round(HEADER_H * scale * 3 / 4))  # (not the block's rounded corners)
    differ = np.flatnonzero(np.abs(one[middle] - other[middle]).max(axis=(0, 2)) > 60)
    assert differ.size, f"the header of {NAMES[0]}{k} does not show its name"
    return int(differ[0])


def header_icon(tab, k: int, color: str, scale: float = 2.0) -> np.ndarray:
    """The ink on `color` left of the name, in the header of the first block of type number k."""
    cut = name_start(tab, k, scale) - round(2 * scale)
    return ink(header(tab, f"{NAMES[0]}{k}", scale)[:, :max(cut, 0)], color)


def has_icon(a: np.ndarray, scale: float) -> bool:
    return a.sum() / scale ** 2 >= MIN_INK


def open_blocks(window, folder: Path, types: list[str]):
    """A flow with each of these block types twice (under the names NAMES + its number), opened in `window`."""
    blocks = {f"{name}{k}": {"type": t, "ui": {"x": -4500 + 1000 * (k % 8) + 450 * j, "y": -4000 + 900 * (k // 8)}}
              for k, t in enumerate(types) for j, name in enumerate(NAMES)}
    path = folder / "icons.yaml"
    path.write_text(yaml.safe_dump({"name": "icons", "blocks": blocks, "edges": []}), encoding="utf-8")
    tab = window.open_flow(path)
    tab.scene.clearSelection()
    QApplication.processEvents()
    return tab


def built_in(registry) -> dict[str, str]:
    """Every built-in block type, with its group colour."""
    return {t: GROUP_COLORS[cls.category] for t, cls in registry.blocks.items()
            if registry.sources[t] == "built-in"}


def canvas_icons(window, folder: Path, colors: dict[str, str], scale: float = 2.0) -> dict[str, np.ndarray]:
    tab = open_blocks(window, folder, list(colors))
    return {t: header_icon(tab, k, colors[t], scale) for k, t in enumerate(colors)}


def rows(palette) -> dict[str, list]:
    """The Blocks list: each group's name (without its count) with its rows."""
    tree = palette.tree
    return {tree.topLevelItem(i).text(0).split("  (")[0]:
            [tree.topLevelItem(i).child(j) for j in range(tree.topLevelItem(i).childCount())]
            for i in range(tree.topLevelItemCount())}


def row(palette, item, scale: float = 2.0) -> np.ndarray:
    """A row of the Blocks list, as the list paints it on a screen scaled to `scale`."""
    tree = palette.tree
    tree.scrollToItem(item)
    QApplication.processEvents()
    rect = tree.visualItemRect(item)
    assert rect.isValid(), item.text(0)
    image = QImage(round(rect.width() * scale), round(rect.height() * scale), QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(scale)
    image.fill(tree.viewport().palette().color(QPalette.Base))
    tree.viewport().render(image, QPoint(0, 0), QRegion(rect))
    return pixels(image)


def unlike_listed(item, color: str, icons: dict[str, np.ndarray]) -> dict[str, float]:
    """How unlike each of `icons` (header icons at 200%) the icon of a row of the Blocks list is: that icon painted
    (by itself) at 200% as large as a header's icon may be (12 to 28 px), at the size it is most like each."""
    shapes = []
    for size in range(12, 29):
        image = QImage(2 * size, 2 * size, QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(2)
        image.fill(QApplication.palette().color(QPalette.Base))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        item.icon(0).paint(painter, 0, 0, size, size)
        painter.end()
        a = ink(pixels(image), color)
        if (a > 0.3).any():
            shapes.append(shape(a))
    assert shapes, f"{item.text(0)}: its icon has no white drawing on {color}"
    return {key: min(float(np.abs(s - shape(a)).mean()) for s in shapes) for key, a in icons.items()}


def theme(name: str):
    """The palette a light or a dark system theme gives the application."""
    from tests.test_inspect_mermaid import DARK, LIGHT

    palette = QPalette()
    for role, value in (DARK if name == "dark" else LIGHT).items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    QApplication.setPalette(palette)
    QApplication.processEvents()


def without_icons() -> dict:
    """Run in a separate Python whose `taskloom` is another one (an unpacked wheel, the Windows build's files), in a
    folder of its own: which built-in blocks show no icon on the canvas or in the Blocks list, and whether Text has its
    icon there."""
    import taskloom
    from taskloom.editor.document import FlowDocument
    from taskloom.editor.panels import Palette
    from taskloom.editor.window import FlowTab
    from taskloom.registry import discover

    app = QApplication.instance() or QApplication([])
    registry = discover(None)
    colors = built_in(registry)

    class Window:  # opens a flow file in a tab of its own, as the editor does
        def open_flow(self, path):
            self.tab = FlowTab(FlowDocument.load(path), registry)
            return self.tab

    window = Window()
    canvas = canvas_icons(window, Path.cwd(), colors, scale=1.0)
    palette = Palette(registry)
    palette.show()
    groups = rows(palette)
    titles = {registry.blocks[t].title: t for t in colors}
    listed = {item.text(0): has_icon(ink(row(palette, item, 1.0), colors[titles[item.text(0)]]), 1.0)
              for items in groups.values() for item in items if item.text(0) in titles}
    assert len(listed) == len(colors), sorted(listed)
    app.processEvents()
    return {"taskloom": taskloom.__file__,
            "canvas": sorted(t for t, a in canvas.items() if not has_icon(a, 1.0)),
            "list": sorted(title for title, shown in listed.items() if not shown),
            "text": bool(has_icon(ink(row(palette, groups["Notes"][0], 1.0), NOTES), 1.0))}


CHECK = """
import importlib.util, json, sys
sys.path.append(sys.argv[2])  # (after the taskloom under test: for the tests' own helpers)
spec = importlib.util.spec_from_file_location("block_icons_check", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("RESULT " + json.dumps(module.without_icons()))
"""


def check_from(folder: Path, tmp_path: Path) -> dict:
    """without_icons, run with `folder` first on the module path, from a folder where nothing else is, and with the
    web out of reach (through a proxy where nothing listens): the icons are never fetched at run time."""
    away = tmp_path / "elsewhere"
    away.mkdir(exist_ok=True)
    done = subprocess.run([sys.executable, "-c", CHECK, __file__, str(REPO)], cwd=away,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                          env={**os.environ, "PYTHONPATH": str(folder), "QT_QPA_PLATFORM": "offscreen",
                               "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
                               "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"})
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    [line] = [x for x in done.stdout.splitlines() if x.startswith("RESULT ")]
    result = json.loads(line.removeprefix("RESULT "))
    assert Path(result["taskloom"]).resolve().is_relative_to(folder.resolve()), result["taskloom"]
    return result


def carries_tabler_licence(files: dict[str, bytes]) -> list[str]:
    """The files (by name) whose text is Tabler Icons' MIT licence."""
    return [name for name, data in files.items()
            if all(m in " ".join(data.decode("utf-8", "replace").split()) for m in TABLER_LICENCE)]


# --- 1: every built-in block has its own icon, left of its name ---------------------------------------------------

def test_every_built_in_block_shows_an_icon_left_of_its_name_on_its_group_colour(win, tmp_path):
    """Spec 1 (the user's request; "a test lists any without one"): on the canvas, the header of every built-in block
    type shows a white line drawing on its group colour, left of its name, about 16-18 px (here: 10 to 24 px at
    100%), and the name is still shown, right of it."""
    colors = built_in(win.registry)
    tab = open_blocks(win, tmp_path, list(colors))
    without, wrong = [], []
    for k, (type_id, color) in enumerate(colors.items()):
        a = header_icon(tab, k, color, scale=1.0)
        if not has_icon(a, 1.0):
            without.append(type_id)
            continue
        ys, xs = np.nonzero(a > 0.3)
        size = max(np.ptp(ys), np.ptp(xs)) + 1
        if not 10 <= size <= 24:
            wrong.append(f"{type_id}: {size} px")
    assert without == [], f"these built-in blocks show no icon left of their name: {without}"
    assert wrong == [], f"these icons are not a small icon of about 16-18 px: {wrong}"


def test_no_two_built_in_blocks_share_an_icon(win, tmp_path):
    """Spec 1: no two built-in block types show the same icon (also the three Servers blocks drawn as a satellite,
    which differ by their badge, and the file icons, which differ by what is on the page)."""
    icons = canvas_icons(win, tmp_path, built_in(win.registry))
    without = [t for t, a in icons.items() if not has_icon(a, 2.0)]
    assert without == [], f"these built-in blocks show no icon: {without}"
    types = list(icons)
    same = [(a, b, differing(icons[a], icons[b])) for i, a in enumerate(types) for b in types[i + 1:]
            if differing(icons[a], icons[b]) < DIFFERENT]
    assert same == [], f"these block types show the same icon (pixels that differ): {same}"


def test_our_own_drawings_are_the_icons_of_their_blocks(win, tmp_path):
    """Style (the approved set, "use exactly these designs"): the duck, the polar bear, the pages with an arrow, the
    play sign with x1 and the magnifying glass with [ ] are the icons of DuckDB SQL, Polars Transform, Move Files,
    Manual Start and Inspect: of every built-in block's icon, that block's is the most like its drawing."""
    icons = canvas_icons(win, tmp_path, built_in(win.registry))
    without = [t for t, a in icons.items() if not has_icon(a, 2.0)]
    assert without == [], f"these built-in blocks show no icon: {without}"
    for type_id, body in OUR_DRAWINGS.items():
        reference = drawing(body)
        closest = min(icons, key=lambda t: unlike(icons[t], reference))
        assert closest == type_id, (f"the drawing for {type_id} is most like the icon of {closest} "
                                    f"({unlike(icons[closest], reference):.3f} against "
                                    f"{unlike(icons[type_id], reference):.3f})")


def test_a_long_name_is_still_shown_right_of_the_icon_and_fits_in_the_header(win, tmp_path):
    """Spec 1: the name is still shown as before, right of the icon (it may move right to make room); a long name
    never runs over the icon nor out of the block: it fits or is cut short, as today."""
    long_name = "nightly_report_for_every_region_and_every_month_of_the_year"
    blocks = {"load": {"type": "data.read_file", "ui": {"x": 0, "y": 0}},
              long_name: {"type": "data.read_file", "ui": {"x": 0, "y": 400}}}
    path = tmp_path / "names.yaml"
    path.write_text(yaml.safe_dump({"name": "names", "blocks": blocks, "edges": []}), encoding="utf-8")
    tab = win.open_flow(path)
    tab.scene.clearSelection()
    color = GROUP_COLORS["Data"]
    text = QApplication.palette().color(QPalette.Text).name()
    base = QApplication.palette().color(QPalette.Base).name()
    for block_id in ("load", long_name):
        rgb = header(tab, block_id, 1.0)
        a = ink(rgb, color)
        assert has_icon(a, 1.0), f"{block_id} shows no icon"
        icon_right = int(np.nonzero(a > 0.3)[1].max())
        # the name: pixels nearer the text colour than the background and the group colour, inside the header's border
        to = {c: np.abs(rgb - QColor(c).getRgb()[:3]).sum(axis=2) for c in (text, base, color)}
        near_text = (to[text] < to[base]) & (to[text] < to[color])
        near_text[:3], near_text[-3:], near_text[:, :2], near_text[:, -2:] = False, False, False, False
        ys, xs = np.nonzero(near_text)
        assert xs.size > 20, f"{block_id}: no name in its header"
        assert xs.min() > icon_right, f"{block_id}: its name starts at x={xs.min()}, over the icon (to x={icon_right})"
        assert xs.max() < NODE_W - 3, f"{block_id}: its name runs out of the block (to x={xs.max()})"


# --- 2: User Created blocks show the puzzle ------------------------------------------------------------------------

def test_any_user_created_block_shows_the_puzzle_icon(win, qtbot, tmp_path):
    """Spec 2: two blocks of your own (one of its own type, one that replaces the built-in Wait, as a user block may)
    both show the same icon, the puzzle, on the User Created colour, on the canvas and next to their names under
    User Created in the Blocks list; it is none of the built-in blocks' icons (not Wait's stopwatch either)."""
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    built_ins = canvas_icons(win, tmp_path, built_in(win.registry))
    blocks = win.home.root / "blocks"
    blocks.mkdir(parents=True, exist_ok=True)
    (blocks / "greet.py").write_text(GREET, encoding="utf-8")
    (blocks / "slow_wait.py").write_text(SLOW_WAIT, encoding="utf-8")
    mine = MainWindow(Home(win.home.root))  # it finds your blocks when it starts
    qtbot.addWidget(mine)
    mine.show()
    (tmp_path / "mine").mkdir()
    greet, slow_wait = canvas_icons(mine, tmp_path / "mine", {"my.greet": USER_CREATED,
                                                              "logic.wait": USER_CREATED}).values()
    assert has_icon(greet, 2.0) and has_icon(slow_wait, 2.0), "a block of your own shows no icon on its colour"
    assert differing(greet, slow_wait) < DIFFERENT, "two blocks of your own show different icons"
    like = {t: differing(greet, a) for t, a in built_ins.items() if differing(greet, a) < DIFFERENT}
    assert like == {}, f"a block of your own shows the icon of {sorted(like)}"

    user_rows = rows(mine.palette_panel)["User Created"]
    assert sorted(item.text(0) for item in user_rows) == ["Greet", "Slow Wait"]
    assert all(has_icon(ink(row(mine.palette_panel, item), USER_CREATED), 2.0) for item in user_rows), (
        "a block of your own shows no icon on its colour in the Blocks list")
    for item in user_rows:
        distance = unlike_listed(item, USER_CREATED, {"the puzzle": greet, **built_ins})
        closest = min(distance, key=distance.get)
        assert closest == "the puzzle", f"in the Blocks list, {item.text(0)} shows the icon of {closest}"


# --- 3: the Blocks list ----------------------------------------------------------------------------------------------

def test_the_blocks_list_shows_each_blocks_icon_on_a_square_of_its_group_colour(win, tmp_path):
    """Spec 3: in the Blocks list, each block's row shows the same icon as its header on the canvas, white on a small
    square of its group colour; each group's row keeps its plain colour square; Text shows an icon of its own ("Tt")
    on the Notes colour."""
    colors = built_in(win.registry)
    canvas = canvas_icons(win, tmp_path, colors)
    titles = {win.registry.blocks[t].title: t for t in colors}
    tree = win.palette_panel.tree
    groups = rows(win.palette_panel)
    without, mismatched, plain = [], [], []
    for i in range(tree.topLevelItemCount()):
        folder = tree.topLevelItem(i)
        group = folder.text(0).split("  (")[0]
        color = NOTES if group == "Notes" else GROUP_COLORS[group]
        painted = row(win.palette_panel, folder)
        if (np.abs(painted - QColor(color).getRgb()[:3]).max(axis=2) <= 10).sum() < 50 or \
                ink(painted, color).sum() > MIN_INK:
            plain.append(group)
    assert plain == [], f"these groups no longer show a plain square of their colour: {plain}"
    for group, items in groups.items():
        if group == "Notes":
            continue
        for item in items:
            type_id = titles[item.text(0)]
            if not has_icon(ink(row(win.palette_panel, item), colors[type_id]), 2.0):
                without.append(item.text(0))
                continue
            distance = unlike_listed(item, colors[type_id], canvas)
            closest = min(distance, key=distance.get)
            if closest != type_id:
                mismatched.append(f"{item.text(0)} shows the icon of {closest}")
    assert without == [], f"these blocks show no icon on their group colour in the Blocks list: {without}"
    assert mismatched == [], mismatched
    [text] = groups["Notes"]
    assert text.text(0) == "Text"
    assert has_icon(ink(row(win.palette_panel, text), NOTES), 2.0), "Text shows no icon on the Notes colour"
    distance = unlike_listed(text, NOTES, canvas)
    closest = min(distance, key=distance.get)
    assert distance[closest] > 0.05, f"Text shows the icon of {closest}"


# --- 4: crisp when the screen is scaled, the same in light and dark ---------------------------------------------------

def as_bitmaps(small: np.ndarray, scaled: np.ndarray, scale: float) -> float:
    """How blurred the 100% icon would be at `scale`, enlarged as a bitmap is: smoothly, or (at 200%) pixel by pixel
    (as blurred as at 100%); the least of those."""
    enlarged = blur(resized(small, scaled.shape[1], scaled.shape[0]))
    return min(enlarged, blur(small)) if scale == 2 else enlarged


@pytest.mark.parametrize("scale", [1.5, 2.0], ids=["150%", "200%"])
def test_the_icons_are_crisp_when_the_screen_is_scaled(win, tmp_path, scale):
    """Spec 4: drawn from the SVGs, the icons are painted at the screen's resolution: at 150% and 200% their lines
    are sharper than the icon painted at 100% and then enlarged as a bitmap is, on the canvas and in the Blocks
    list."""
    colors = built_in(win.registry)
    tab = open_blocks(win, tmp_path, list(colors))
    blurred = []
    for k, (type_id, color) in enumerate(colors.items()):
        small, scaled = header_icon(tab, k, color, 1.0), header_icon(tab, k, color, scale)
        assert has_icon(small, 1.0) and has_icon(scaled, scale), f"{type_id} shows no icon"
        if not blur(scaled) < 0.85 * as_bitmaps(small, scaled, scale):
            blurred.append(f"{type_id} (canvas): {blur(scaled):.2f}, as a bitmap {as_bitmaps(small, scaled, scale):.2f}")
    titles = {win.registry.blocks[t].title: t for t in colors}
    for group, items in rows(win.palette_panel).items():
        for item in items:
            color = NOTES if group == "Notes" else colors[titles[item.text(0)]]
            small, scaled = ink(row(win.palette_panel, item, 1.0), color), ink(row(win.palette_panel, item, scale), color)
            assert has_icon(small, 1.0) and has_icon(scaled, scale), f"{item.text(0)} shows no icon in the list"
            if not blur(scaled) < 0.85 * as_bitmaps(small, scaled, scale):
                blurred.append(f"{item.text(0)} (list): {blur(scaled):.2f}, "
                               f"as a bitmap {as_bitmaps(small, scaled, scale):.2f}")
    assert blurred == [], f"at {scale:.0%} these icons are as blurred as an enlarged bitmap: {blurred}"


def test_the_icons_look_the_same_in_light_and_dark(win, tmp_path):
    """Spec 4: with a dark system theme the icons are the same white drawings on the same group colours as with a
    light one, on the canvas and in the Blocks list."""
    colors = built_in(win.registry)
    original = QApplication.palette()
    try:
        theme("light")
        tab = open_blocks(win, tmp_path, list(colors))
        light = {t: header_icon(tab, k, c) for k, (t, c) in enumerate(colors.items())}
        text = rows(win.palette_panel)["Notes"][0]
        light_text = ink(row(win.palette_panel, text), NOTES)
        theme("dark")
        dark = {t: header_icon(tab, k, c) for k, (t, c) in enumerate(colors.items())}
        dark_text = ink(row(win.palette_panel, text), NOTES)
    finally:
        QApplication.setPalette(original)
        QApplication.processEvents()
    assert [t for t, a in light.items() if not has_icon(a, 2.0) or a.max() < 0.9] == [], "no white icon in light"
    changed = [t for t in colors if not has_icon(dark[t], 2.0) or differing(light[t], dark[t]) > 2]
    assert changed == [], f"in the dark theme these icons are not the same white drawing on their colour: {changed}"
    assert has_icon(light_text, 2.0) and has_icon(dark_text, 2.0) and differing(light_text, dark_text) <= 2, (
        "Text's icon in the Blocks list is not the same in light and dark")


# --- 4, Style: in the wheel and the Windows build, with Tabler Icons' licence --------------------------------------

def test_the_wheel_carries_the_icons_and_tabler_icons_licence(tmp_path):
    """Spec 4, Style: the wheel (built with uv, as CI has it) has the icons, never fetched at run time: Taskloom
    installed from it alone shows every built-in block's icon on the canvas and in the Blocks list, and Text's; and it
    carries Tabler Icons' MIT licence text."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed (it builds the wheel, as in CI)")
    source = tmp_path / "source"
    shutil.copytree(REPO, source, ignore=shutil.ignore_patterns(".git", ".venv", "dist", "build", "tests",
                                                               "__pycache__", ".pytest_cache"))
    done = subprocess.run([uv, "build", "--wheel", "--out-dir", str(tmp_path / "out"), str(source)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    [wheel] = (tmp_path / "out").glob("*.whl")
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as z:
        z.extractall(installed)
        files = {n: z.read(n) for n in z.namelist() if not n.endswith("/")}

    result = check_from(installed, tmp_path)

    assert result["canvas"] == [], f"installed from the wheel, these blocks show no icon: {result['canvas']}"
    assert result["list"] == [], f"installed from the wheel, the Blocks list shows no icon for {result['list']}"
    assert result["text"], "installed from the wheel, Text shows no icon in the Blocks list"
    assert carries_tabler_licence(files), "the wheel has no copy of Tabler Icons' MIT licence"


def test_the_windows_build_carries_the_icons_and_tabler_icons_licence(monkeypatch, tmp_path):
    """Spec 4, Style: the Windows build (packaging/taskloom.spec, its datas) has the icons: Taskloom's modules with
    the data files the spec hands PyInstaller, at the places it puts them, show every built-in block's icon on the
    canvas and in the Blocks list, and Text's; the build (the spec's data files, or what packaging/build.py adds to
    its folder) carries Tabler Icons' MIT licence text."""
    from tests.test_inspect_mermaid import FAKE_PYINSTALLER, archive_files, fake_package, on_path, spec_build

    frozen = tmp_path / "frozen"
    for module in (REPO / "taskloom").rglob("*.py"):  # in the build, from its archive of modules
        target = frozen / module.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(module, target)
    data = {}
    for src, dest in spec_build(monkeypatch, headless=False)["datas"]:
        if not Path(dest).parts or Path(dest).parts[0] != "taskloom":
            continue  # (other packages' data: holidays)
        for match in map(Path, sorted(map(str, Path(src).parent.glob(Path(src).name)))):
            files = [match] if match.is_file() else [f for f in match.rglob("*") if f.is_file()]
            for f in files:
                to = frozen / dest / (f.name if match.is_file() else f.relative_to(match))
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, to)
                data[str(to.relative_to(frozen))] = f.read_bytes()

    result = check_from(frozen, tmp_path)

    assert result["canvas"] == [], f"in the Windows build, these blocks show no icon: {result['canvas']}"
    assert result["list"] == [], f"in the Windows build, the Blocks list shows no icon for {result['list']}"
    assert result["text"], "in the Windows build, Text shows no icon in the Blocks list"

    repo = tmp_path / "repo"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", ".venv", "dist", "build", "tests", "__pycache__",
                                                             ".pytest_cache"))
    fake = fake_package(tmp_path / "fake", "PyInstaller", "")
    (fake / "PyInstaller" / "__main__.py").write_text(FAKE_PYINSTALLER, encoding="utf-8")
    done = subprocess.run([sys.executable, str(repo / "packaging" / "build.py")], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300,
                          env={**os.environ, "PYTHONPATH": on_path(fake)})
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    [archive] = [p for p in (repo / "dist").iterdir() if p.name.endswith((".zip", ".tar.gz"))]
    assert carries_tabler_licence({**data, **archive_files(archive)}), (
        "the Windows build has no copy of Tabler Icons' MIT licence")


def test_tabler_icons_licence_is_in_the_repository_and_named_with_the_third_party_licences():
    """Style: Tabler Icons' MIT licence text is in the repository, and README's License section, where the project
    lists the third-party licences, names Tabler Icons and its MIT licence."""
    found = []
    for folder, dirs, files in os.walk(REPO):  # the repository's own files (not .git, .venv, builds, the tests)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("dist", "build", "site", "tests", "__pycache__")]
        found += [f for f in files if Path(f).suffix not in (".py", ".svg")
                  and carries_tabler_licence({f: (Path(folder) / f).read_bytes()})]
    assert found, "Tabler Icons' MIT licence text is not in the repository (next to the icons, packaging/licenses)"
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = " ".join(readme.split("\n## License", 1)[1].split("\n## ", 1)[0].split())
    assert re.search(r"Tabler Icons.{0,200}MIT|MIT.{0,200}Tabler Icons", section, re.I), section


def test_a_changed_copy_of_a_blocks_list_icon_still_paints(win):
    """A copy of a Blocks list icon that is then changed (Qt makes its own copy of the drawing for it) paints, and the
    editor does not crash: Qt keeps using that copy after Python would have let it go."""
    item = rows(win.palette_panel)["Logic"][0]
    for _ in range(20):
        changed = item.icon(0)
        changed.addPixmap(QPixmap(4, 4))
        gc.collect()
        assert not changed.pixmap(16, 16).isNull()
    assert not item.icon(0).pixmap(16, 16).isNull()
