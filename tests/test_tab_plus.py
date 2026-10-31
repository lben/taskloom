"""The + button that opens a new flow tab is larger, easy to see and to hit (the user's words: "Editor: The plus sign
to create a new tab needs to be larger"). Spec 1: its + is at least about twice today's height (about 1.5 times the
tab text's capital letters or more), bold enough to read, and the button is at least as tall as a tab and at least
about 24 px wide. Spec 2: it follows the palette's text colour, also when the colours change with the editor open,
and it is crisp at 150% and 200%. Spec 3: clicking it opens a new flow, and its tooltip says so. Where it sits (right
of the last tab, never over a tab, at the right end when the tabs scroll) is protected by test_editor_run_choices.py.

The + is measured on the button as the real editor draws it, in its own process (offscreen, at the scale under test,
with the system's fonts: on Windows from its Fonts folder, as Qt's offscreen platform finds none there otherwise)."""

import inspect
import json
import os
import statistics
import subprocess
import sys
import textwrap
from collections import Counter

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# Colours to change to with the editor open (as when the system's dark mode is turned on, then off): each with a text
# colour of its own, so the + shows it follows the palette's text colour (not a fixed black or white).
DARK = {"Window": "#2b2d30", "Base": "#1e1f22", "Button": "#2b2d30",
        "WindowText": "#f2d58c", "Text": "#f2d58c", "ButtonText": "#f2d58c"}
LIGHT = {"Window": "#f4f4f4", "Base": "#ffffff", "Button": "#f4f4f4",
         "WindowText": "#1a3a8a", "Text": "#1a3a8a", "ButtonText": "#1a3a8a"}


def new_flow_button(window):
    """The button by the flows' tabs that opens a new flow, as its tooltip says (not the canvas's zoom +, nor a
    button in a panel)."""
    from PySide6.QtWidgets import QAbstractButton, QDockWidget
    pages, docks = window.tabs_list(), window.findChildren(QDockWidget)
    found = [b for b in window.findChildren(QAbstractButton)
             if b.isVisible() and "new flow" in b.toolTip().lower()
             and not any(p.isAncestorOf(b) for p in pages) and not any(d.isAncestorOf(b) for d in docks)]
    assert len(found) == 1, f"no single button by the tabs whose tooltip says it opens a new flow: {found}"
    return found[0]


SHOOT = textwrap.dedent(inspect.getsource(new_flow_button)) + textwrap.dedent('''
    import json, os, sys
    from pathlib import Path
    from PySide6.QtGui import QColor, QFontDatabase, QFontMetricsF, QPalette
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication([sys.argv[0]])
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    MainWindow.start_scheduler = lambda self, by_you=False: None  # no real scheduler service
    for name in ("information", "warning", "question"):  # a message box would wait for a click
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.No))
    out, palettes = Path(sys.argv[1]), json.loads(sys.argv[2])
    window = MainWindow(Home(out / "home"))
    window.show()
    window.new_flow()  # as the editor opens with no flow to restore
    shots = {}

    def shoot(name):
        app.processEvents()
        picture = new_flow_button(window).grab()
        picture.save(str(out / f"{name}.png"))
        shots[name] = {"ratio": picture.devicePixelRatio(),
                       "cap": QFontMetricsF(window.tabs.tabBar().font()).capHeight()}  # the tab text's capitals

    shoot("as it opens")
    for name, colours in palettes.items():  # the colours changed with the editor open
        palette = QPalette()
        for role, value in colours.items():
            palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
        QApplication.setPalette(palette)
        shoot(name)
    print(json.dumps({"fonts": bool(QFontDatabase.families()), "shots": shots}), flush=True)
    os._exit(0)  # done: the window is left as it is (no closing it, no teardown)
''')


def shoot_the_plus(tmp_path, scale: str, palettes: dict | None = None) -> dict:
    """{name: (picture of the + button in device pixels, its device pixel ratio, the tab text's cap height in px)}:
    as the real editor opens at `scale` ("as it opens"), then after each change to `palettes` (in order)."""
    script = tmp_path / "shoot.py"
    script.write_text(SHOOT, encoding="utf-8")
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_SCALE_FACTOR": scale,
                   "TASKLOOM_HOME": str(tmp_path / "home")}
    if sys.platform == "win32":  # Qt's offscreen platform finds no fonts there otherwise
        environment.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    done = subprocess.run([sys.executable, str(script), str(tmp_path), json.dumps(palettes or {})], env=environment,
                          cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    assert done.returncode == 0, done.stderr[-3000:]
    measured = json.loads(done.stdout.splitlines()[-1])
    if not measured["fonts"]:
        pytest.skip("Qt has no fonts on its offscreen platform here: no tab text to compare the + with")
    return {name: (QImage(str(tmp_path / f"{name}.png")), shot["ratio"], shot["cap"])
            for name, shot in measured["shots"].items()}


def the_plus(picture: QImage, ratio: float) -> dict:
    """The + drawn on the button's picture: its ink is the colour farthest from the background (the colour most of
    the picture has); each pixel's cover is how far it goes from the background toward the ink (0 to 1). Sizes in
    the editor's px (device pixels / ratio): its height and width, the thickness of its upright and of its crossbar
    (the ink across each, away from where they cross and from their ends), and `soft`: across those strokes, the
    device pixels only partly covered (a crisp stroke has at most one at each of its two edges, a stretched picture
    of a smaller one more)."""
    w, h = picture.width(), picture.height()
    colours = [[picture.pixelColor(x, y) for x in range(w)] for y in range(h)]
    background = QColor.fromRgba(Counter(c.rgba() for row in colours for c in row).most_common(1)[0][0])
    back = (background.red(), background.green(), background.blue())

    def away(c):
        return [c.red() - back[0], c.green() - back[1], c.blue() - back[2]]

    ink = max((c for row in colours for c in row), key=lambda c: sum(d * d for d in away(c)))
    toward = away(ink)
    length = sum(d * d for d in toward)
    assert length > 0, "nothing is drawn on the + button"
    cover = [[min(1.0, max(0.0, sum(a * b for a, b in zip(away(c), toward)) / length)) for c in row]
             for row in colours]
    rows = [sum(cover[y]) for y in range(h)]
    cols = [sum(cover[y][x] for y in range(h)) for x in range(w)]
    inked_rows, inked_cols = [y for y in range(h) if rows[y] >= 0.5], [x for x in range(w) if cols[x] >= 0.5]
    top, bottom, left, right = inked_rows[0], inked_rows[-1], inked_cols[0], inked_cols[-1]
    crossbar = [y for y in range(top, bottom + 1) if rows[y] >= (right - left + 1) / 2]
    upright = [x for x in range(left, right + 1) if cols[x] >= (bottom - top + 1) / 2]
    assert crossbar and upright, "what the button shows is not a +"

    def middle(span):  # the middle half of an arm
        span = list(span)
        return span[len(span) // 4:len(span) - len(span) // 4]

    upright_rows = middle(range(top, crossbar[0])) + middle(range(crossbar[-1] + 1, bottom + 1))
    crossbar_cols = middle(range(left, upright[0])) + middle(range(upright[-1] + 1, right + 1))
    assert upright_rows and crossbar_cols, "what the button shows is not a + (no arms around where it crosses)"
    soft = max(statistics.median(sum(0.1 < cover[y][x] < 0.9 for x in range(w)) for y in upright_rows),
               statistics.median(sum(0.1 < cover[y][x] < 0.9 for y in range(h)) for x in crossbar_cols))
    return {"height": (bottom - top + 1) / ratio, "width": (right - left + 1) / ratio,
            "upright": statistics.median(rows[y] for y in upright_rows) / ratio,
            "crossbar": statistics.median(cols[x] for x in crossbar_cols) / ratio,
            "soft": soft, "margin": min(left, top, w - 1 - right, h - 1 - bottom), "ink": ink,
            "background": background}


# --- spec 1 and 2: a large, bold +, crisp at 150% and 200% ---------------------------------------------------------

@pytest.mark.parametrize("scale", ["1", "1.5", "2"], ids=["100%", "150%", "200%"])
def test_the_plus_is_large_bold_and_crisp_at_100_150_and_200_percent(tmp_path, scale):
    """Spec 1: as the editor opens, its + is at least about 1.5 times as tall as the tab text's capital letters
    (about twice today's +, which is less than them), whole on its button, and its strokes are bold enough to read
    (about 2 px or more; today's are about 1 px). Spec 2: at 150% and 200% it is drawn at the screen's resolution,
    crisp (each edge of a stroke at most one partly covered pixel), not a smaller picture stretched."""
    [(picture, ratio, cap)] = shoot_the_plus(tmp_path, scale).values()
    plus = the_plus(picture, ratio)
    shown = {k: round(v, 2) if isinstance(v, float) else v for k, v in plus.items() if k not in ("ink", "background")}

    assert plus["height"] >= 1.4 * cap, f"the + is {plus['height']:.1f} px tall, the tab's capitals {cap:.1f}: {shown}"
    assert plus["margin"] >= 1, f"the + is cut off at the button's edge: {shown}"
    assert 0.6 <= plus["width"] / plus["height"] <= 1.6, f"not a + (as wide as it is tall): {shown}"
    assert min(plus["upright"], plus["crossbar"]) >= 1.9, f"the +'s strokes are thin: {shown}"
    assert plus["soft"] <= 2, f"the + is blurred at {float(scale):.0%} (a stretched picture): {shown}"


# --- spec 2: the palette's text colour, also when the colours change with the editor open --------------------------

def test_the_plus_follows_the_palettes_text_colour_when_the_colours_change_with_the_editor_open(tmp_path):
    """Spec 2 (protects it: today's text + follows the palette; a + drawn once as a picture would not): with the
    editor open at 150%, the colours change to dark ones, then back to light ones, each with a text colour of its
    own; each time the + is drawn in the new text colour, on the new background."""
    shots = shoot_the_plus(tmp_path, "1.5", {"dark": DARK, "light": LIGHT})

    for name, colours in (("dark", DARK), ("light", LIGHT)):
        picture, ratio, _ = shots[name]
        plus = the_plus(picture, ratio)
        ink, text = plus["ink"], QColor(colours["ButtonText"])
        assert max(abs(ink.red() - text.red()), abs(ink.green() - text.green()), abs(ink.blue() - text.blue())) <= 40, (
            f"after the change to {name} colours the + is {ink.name()} on {plus['background'].name()}, "
            f"the palette's text {text.name()} on {colours['Button']}")


# --- spec 1 and 3: a tab-tall button, at least 24 px wide, a click anywhere on it opens a new flow -----------------

def test_a_click_anywhere_on_the_tab_tall_plus_opens_a_new_flow(win, qtbot):
    """Spec 1 (its clickable area) and 3: as the editor opens, the + button is at least as tall as a tab and at least
    24 px wide, its tooltip says it opens a new flow, nothing covers its corners, and a click near each corner opens
    a new flow tab (as File > New flow: unsaved, with a Manual Start)."""
    win.new_flow()  # as the editor opens with no flow to restore
    QApplication.processEvents()
    bar, button = win.tabs.tabBar(), new_flow_button(win)
    tab = bar.tabRect(bar.count() - 1)
    assert "new flow" in button.toolTip().lower(), button.toolTip()
    assert button.height() >= tab.height(), f"the + button is {button.height()} px tall, a tab {tab.height()} px"
    assert button.width() >= 24, f"the + button is {button.width()} px wide"

    w, h = button.width(), button.height()
    for corner in (QPoint(1, 1), QPoint(w - 2, 1), QPoint(1, h - 2), QPoint(w - 2, h - 2)):
        QApplication.processEvents()  # (it moved right of the tab opened last)
        button = new_flow_button(win)
        hit = win.childAt(button.mapTo(win, corner))
        assert hit is button or button.isAncestorOf(hit), f"at the + button's corner {corner} is {hit!r}"
        count = win.tabs.count()
        QTest.mouseClick(button, Qt.LeftButton, Qt.NoModifier, corner)
        assert win.tabs.count() == count + 1, f"a click at the + button's corner {corner} opened no new flow"
        assert win.current.doc.path is None
        assert [spec["type"] for spec in win.current.doc.blocks.values()] == ["trigger.manual"]
