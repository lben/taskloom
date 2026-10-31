"""Tools > Settings > Theme: System (the default), Light or Dark. The choice is kept in the editor's own preferences
(editor.ini, never settings.yaml, which the scheduler reads) and used again at the next start, before the window
shows. Save in Settings applies it at once to the whole editor, with no restart and nothing left in the colours of the
theme before: the window and its panels, the canvas and its texts, the code boxes, the Documentation column, the Log
and its diagrams, grey headings and hints, the zoom bar's and the scheduler button's icons, tooltips and hover cards,
menus and the dialogs opened after. Dark has JetBrains-like colours, Light is light also when the system is dark, and
System follows the operating system, also when it switches while the editor is open, whatever style the application
starts with (Windows 10's cannot be dark). Each test names the point of the specification (1-5) it protects.

Driven through the real editor (the `win` fixture) and, for the next start, the real `taskloom editor` in a process
of its own. The operating system's light or dark mode is given as Qt reports it to the application:
QStyleHints.colorScheme(), and its colorSchemeChanged signal when it changes; and, as on a real dark system, a dark
standard palette from the style (QStyle.standardPalette(), Fusion's dark colours), since Qt's own code does not see a
Python patch of colorScheme() and the offscreen platform the tests run on ignores setColorScheme(). Readable: a WCAG contrast of at least 4.5:1 for text, and 3:1 for grey hints and
headings and for icons (WCAG's bar for large text and for graphics), measured on what the editor paints, where it
paints it (and, for every widget, on the colours it gives the widget)."""

import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QHelpEvent, QImage, QPalette, QStyleHints  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QLabel, QPushButton, QRadioButton, QStyle,  # noqa: E402
                               QStyleFactory, QToolButton, QToolTip, QWidget)

from taskloom.editor.canvas import CATEGORY_COLORS  # noqa: E402
from tests.test_code_editor import background, clean, code_box, color, duckdb, python  # noqa: E402
from tests.test_docs_help import dismiss, hide_native_tips, hover, row_named  # noqa: E402
from tests.test_inspect_mermaid import SMALL, contrast, entry, fence, images, near, show_log, unique  # noqa: E402
from tests.test_inspect_mermaid_theme import assert_drawn_on_the_logs_background  # noqa: E402
from tests.test_paths_editor_editing import (flow_file, labelled, menu, press, select_only,  # noqa: E402
                                             when_dialog_shows)

REPO = Path(__file__).resolve().parents[1]
THEMES = ["System", "Light", "Dark"]
# IntelliJ's dark theme: tool windows, editor and text areas, text, selection.
JETBRAINS = {"Window": "#2b2d30", "Base": "#1e1f22", "Text": "#dfe1e5", "Highlight": "#2e436e"}
ABOUT = 16  # "about": each of red, green and blue within this much
TEXT, HINT = 4.5, 3.0  # the contrast text needs, and grey hints, headings and icons
# The code boxes' keyword, string, comment and number colours (JetBrains-like), on a light and on a dark background.
CODE_LIGHT = {"keyword": "#0033b3", "string": "#067d17", "comment": "#808080", "number": "#1750eb"}
CODE_DARK = {"keyword": "#cf8e6d", "string": "#6aab73", "comment": "#7a7e85", "number": "#2aacb8"}
# Fusion's standard palette as Qt gives it on a real dark system (measured on macOS, Qt 6.12).
FUSION_DARK = {"Window": "#323232", "WindowText": "#f0f0f0", "Base": "#242424", "AlternateBase": "#2b2b2b",
               "Text": "#f0f0f0", "Button": "#323232", "ButtonText": "#f0f0f0"}


def dark_standard() -> QPalette:
    palette = QPalette()
    for role, value in FUSION_DARK.items():
        palette.setColor(getattr(QPalette, role), QColor(value))
    return palette


# --- the look the tests run with, and the operating system's light or dark mode ------------------------------------

@pytest.fixture(autouse=True)
def as_found(qapp, monkeypatch, tmp_path):
    """Each test leaves the application's look as it found it (its style, palette, style sheet, tooltip colours and
    colour scheme are the whole test session's). Save in Settings also writes, on Windows, the Startup script of
    "Start the scheduler service when I log in": in this test's folder, never the real one."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    style, sheet, palette, tips = qapp.style().name(), qapp.styleSheet(), QApplication.palette(), QToolTip.palette()
    yield
    QGuiApplication.styleHints().unsetColorScheme()
    if qapp.style().name() != style:
        QApplication.setStyle(style)
    if qapp.styleSheet() != sheet:
        qapp.setStyleSheet(sheet)
    QApplication.setPalette(palette)
    QToolTip.setPalette(tips)
    settle()


@pytest.fixture
def system(monkeypatch):
    """system("light" | "dark"): the operating system's light or dark mode as Qt reports it to the application (light
    at first), with the style's standard palette dark while it is dark; a switch while the editor is open is told as
    the system tells it (colorSchemeChanged)."""
    now = [Qt.ColorScheme.Light]
    monkeypatch.setattr(QStyleHints, "colorScheme", lambda self: now[0])
    standard = QStyle.standardPalette
    monkeypatch.setattr(QStyle, "standardPalette",
                        lambda self: dark_standard() if now[0] == Qt.ColorScheme.Dark else standard(self))

    def switch(mode: str):
        scheme = Qt.ColorScheme.Dark if mode == "dark" else Qt.ColorScheme.Light
        if scheme != now[0]:
            now[0] = scheme
            QGuiApplication.styleHints().colorSchemeChanged.emit(scheme)
        settle()

    return switch


# --- Tools > Settings > Theme --------------------------------------------------------------------------------------

def settle(rounds: int = 5):
    for _ in range(rounds):
        QApplication.processEvents()


def theme_row(dialog) -> QWidget:
    return labelled(dialog, "Theme")


def offered(row) -> tuple[list[str], str | None]:
    """The themes the Theme row offers, in order, and the one chosen."""
    if isinstance(row, QComboBox):
        return [row.itemText(i) for i in range(row.count())], row.currentText()
    buttons = row.findChildren(QRadioButton)
    names = [b.text().replace("&", "") for b in buttons]
    return names, next((n for n, b in zip(names, buttons) if b.isChecked()), None)


def pick(row, theme: str):
    if isinstance(row, QComboBox):
        row.setCurrentIndex(row.findText(theme))
    else:
        next(b for b in row.findChildren(QRadioButton) if b.text().replace("&", "") == theme).click()


def in_settings(win, do):
    """Tools > Settings…: do(dialog) while it is open (it presses Save or Cancel); what do gives back."""
    done = []

    def act(dialog):
        try:
            done.append(do(dialog))
        except Exception as e:  # (raised again below, in the test)
            done.append(e)

    when_dialog_shows(win, act)
    menu(win, "Settings…").trigger()
    settle()
    assert done, "Tools > Settings… showed no dialog"
    if isinstance(done[0], Exception):
        raise done[0]
    return done[0]


def choose(win, theme: str, button: str = "Save"):
    """Tools > Settings…, Theme: `theme`, then Save (or Cancel)."""
    in_settings(win, lambda dialog: (pick(theme_row(dialog), theme), press(dialog, button)))


def kept(win, theme: str):
    """Wait until editor.ini holds `theme` (QSettings writes the file from the event loop), for up to 5 s."""
    ini, deadline = win.home.root / "editor.ini", time.monotonic() + 5
    while not (ini.exists() and theme.lower() in ini.read_text(encoding="utf-8").lower()):
        held = ini.read_text(encoding="utf-8") if ini.exists() else None
        assert time.monotonic() < deadline, f"editor.ini does not hold the theme {theme}: {held!r}"
        settle()


def theme_shown(win) -> str | None:
    """The theme Tools > Settings… shows as chosen (closed with Cancel)."""
    return in_settings(win, lambda dialog: (offered(theme_row(dialog))[1], press(dialog, "Cancel"))[0])


# --- what is painted -----------------------------------------------------------------------------------------------

def most_common(image: QImage, step: int = 1) -> QColor:
    counts = Counter(image.pixel(x, y) for y in range(0, image.height(), step) for x in range(0, image.width(), step))
    return QColor.fromRgb(counts.most_common(1)[0][0])


def in_place(widget, rect: QRect | None = None) -> QImage:
    """`widget` (or `rect` of it) as its window paints it: over whatever is behind it."""
    settle()
    top = widget.window()
    rect = rect or widget.rect()
    return top.grab(QRect(widget.mapTo(top, rect.topLeft()), rect.size())).toImage()


def strongest(image: QImage, behind: QColor | None = None) -> float:
    """The contrast with the background (`behind`, else the colour most of `image` is) of the opaque colour most unlike
    it: the text, or the drawing, at its strongest."""
    behind = behind or most_common(image)
    found = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())]
    return max((contrast(c, behind) for c in found if c.alpha() == 255), default=1.0)


def is_dark(color: QColor) -> bool:
    return color.lightness() < 128


def describe(widget) -> str:
    text = getattr(widget, "text", None)
    text = text() if callable(text) else ""
    text = text if isinstance(text, str) else ""
    return " ".join(p for p in (type(widget).__name__, widget.objectName(), repr(text[:40]) if text else "") if p)


def colours_off(widget, dark: bool) -> list[str]:
    """What is wrong with `widget`'s colours for the dark (or light) theme: a background (of the window, of text
    areas and lists and their alternate rows, of buttons) of the other theme, or (when it is enabled) text that does
    not stand out from its background."""
    wrong, palette = [], widget.palette()
    for back, fore in ((QPalette.Window, QPalette.WindowText), (QPalette.Base, QPalette.Text),
                       (QPalette.AlternateBase, QPalette.Text), (QPalette.Button, QPalette.ButtonText)):
        b, f = palette.color(back), palette.color(fore)
        if is_dark(b) != dark:
            wrong.append(f"{describe(widget)}: {back.name} {b.name()}")
        elif widget.isEnabled() and contrast(f, b) < HINT:
            wrong.append(f"{describe(widget)}: {fore.name} {f.name()} on {b.name()}")
    return wrong


def not_in_theme(top: QWidget, dark: bool) -> list[str]:
    """The visible widgets of `top` (itself too) whose colours are not the theme's."""
    settle()
    return [w for widget in [top, *top.findChildren(QWidget)] if widget.isVisible() for w in colours_off(widget, dark)]


def whole_window_off(win, dark: bool) -> list[str]:
    """not_in_theme for the main window with each tab of the Inspector and of the Output shown in turn, and for its
    menus."""
    wrong = []
    for tabs in (win.right_tabs, win.bottom_tabs):
        current = tabs.currentIndex()
        for i in range(tabs.count()):
            tabs.setCurrentIndex(i)
            wrong += not_in_theme(win, dark)
        tabs.setCurrentIndex(current)
    for action in win.menuBar().actions():
        if action.menu() is not None:
            wrong += colours_off(action.menu(), dark)
    return sorted(set(wrong))


def dialog_off(win, dark: bool) -> list[str]:
    """not_in_theme for a dialog opened now (Tools > Settings…, closed with Cancel)."""
    return in_settings(win, lambda dialog: (not_in_theme(dialog, dark), press(dialog, "Cancel"))[0])


def canvas_colour(tab) -> QColor:
    """The colour of the canvas behind the blocks."""
    settle()
    return most_common(tab.view.viewport().grab().toImage(), step=4)


def jetbrains_off(win) -> list[str]:
    """How the window's colours differ from IntelliJ's dark theme: the tool windows, the Log (a text area), text and
    selection."""
    window, log = win.palette(), win.log.text.palette()
    shown = {"Window": window.color(QPalette.Window), "Base": log.color(QPalette.Base),
             "Text": window.color(QPalette.WindowText), "Highlight": window.color(QPalette.Highlight)}
    return [f"{role} {shown[role].name()}, not about {value}" for role, value in JETBRAINS.items()
            if not near(shown[role], QColor(value), ABOUT)]


def nightly(path: Path) -> Path:
    """A flow with a start block, a Python Code block, an unset Schedule and a text on the canvas."""
    return flow_file(path, {"manual": {"type": "trigger.manual"},
                            "load": python("import os  # nightly\nx = 'a' + str(42)\n"),
                            "nightly": {"type": "trigger.schedule"}},
                     ["manual.start -> load.after"],
                     notes={"note_1": {"text": "Nightly load", "x": 0, "y": 320}})


# --- 1: the choice, where it is kept, and the next start -----------------------------------------------------------

def test_settings_offers_system_light_and_dark_and_system_is_the_default(win):
    """Spec 1: Tools > Settings has a Theme row offering System, Light and Dark, in that order; a Taskloom that never
    chose shows System."""
    shown = in_settings(win, lambda dialog: (offered(theme_row(dialog)), press(dialog, "Cancel"))[0])
    assert shown == (THEMES, "System")


def test_the_theme_is_kept_in_the_editors_preferences_not_in_settings_yaml_and_cancel_keeps_it(win):
    """Spec 1, 2: Save keeps the theme in editor.ini (the editor's own preferences), never in settings.yaml: the
    settings file the scheduler reads stays readable to it (it refuses unknown keys) and holds no theme; Settings shows
    the theme chosen when it opens again; a theme chosen and then cancelled changes nothing."""
    ini, settings_file = win.home.root / "editor.ini", win.home.root / "settings.yaml"
    assert "dark" not in (ini.read_text(encoding="utf-8").lower() if ini.exists() else "")

    choose(win, "Dark")

    kept(win, "Dark")
    written = settings_file.read_text(encoding="utf-8") if settings_file.exists() else ""
    assert "theme" not in written.lower() and "dark" not in written.lower(), written
    assert "theme" not in win.home.settings()  # (it would raise on an unknown key)
    assert theme_shown(win) == "Dark"

    choose(win, "Light", button="Cancel")
    assert theme_shown(win) == "Dark"
    assert is_dark(win.palette().color(QPalette.Window)), "Cancel applied the theme chosen"


NEXT_START = r"""
import json, os, sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QStyleHints
from PySide6.QtWidgets import QApplication, QMessageBox, QStyle

from taskloom import cli
from taskloom.editor import window

scheme = Qt.ColorScheme.Dark if sys.argv[1] == "dark" else Qt.ColorScheme.Light
QStyleHints.colorScheme = lambda self: scheme  # the system's light or dark mode, as Qt reports it


def dark_standard(self):  # the style's standard palette, dark as on a real dark system (sys.argv[2]: its colours)
    palette = QPalette()
    for role, value in json.loads(sys.argv[2]).items():
        palette.setColor(getattr(QPalette, role), QColor(value))
    return palette


if sys.argv[1] == "dark":
    QStyle.standardPalette = dark_standard
seen, boxes = {}, []
for name in ("information", "warning", "question", "critical"):  # (a message box would wait for a click)
    setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: boxes.append([_n, *map(str, a[1:3])])
                                            or QMessageBox.No))
show = window.MainWindow.show


def shown(self):
    show(self)
    if not seen:  # its colours as it first shows, before anything is painted (that waits for the event loop)
        own, log = self.palette(), self.log.text.palette()
        seen.update(Window=own.color(QPalette.Window).name(), WindowText=own.color(QPalette.WindowText).name(),
                    Base=log.color(QPalette.Base).name(), Text=log.color(QPalette.Text).name())


window.MainWindow.show = shown
window.MainWindow.start_scheduler = lambda self, by_you=False: None  # (no scheduler service from a test)
QApplication.exec = lambda self: 0  # started: it does not wait to be closed
code = cli.main(["editor"])
print(json.dumps({"seen": seen, "boxes": boxes, "code": code}), flush=True)
os._exit(0)
"""


@pytest.mark.parametrize("saved, system_mode, dark", [("Dark", "light", True), ("Light", "dark", False),
                                                      (None, "dark", True)],
                         ids=["dark-on-a-light-system", "light-on-a-dark-system", "system-default-on-a-dark-system"])
def test_the_next_start_shows_the_theme_from_its_first_paint(win, tmp_path, saved, system_mode, dark):
    """Spec 1, 3 (the user's case: Windows set to light, Dark chosen): `taskloom editor` started again on the same
    Taskloom folder has the theme chosen before (or System, the default, which follows the system) when its window
    first shows, so it never flashes in other colours: Dark (JetBrains-like) on a light system, Light on a dark one,
    and with nothing chosen the system's dark."""
    if saved:
        choose(win, saved)
        kept(win, saved)
    started = subprocess.run([sys.executable, "-c", NEXT_START, system_mode, json.dumps(FUSION_DARK)], cwd=tmp_path,
                             capture_output=True,
                             text=True, timeout=120,
                             env={**os.environ, "TASKLOOM_HOME": str(win.home.root), "QT_QPA_PLATFORM": "offscreen",
                                  "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"})
    lines = [line for line in started.stdout.splitlines() if line.startswith("{")]
    assert started.returncode == 0 and lines, started.stdout + started.stderr
    result = json.loads(lines[-1])
    assert result["boxes"] == [] and result["code"] == 0, result
    seen = {role: QColor(value) for role, value in result["seen"].items()}
    if dark:
        off = [f"{role} {seen[role].name()}, not about {JETBRAINS[key]}" for role, key in
               (("Window", "Window"), ("WindowText", "Text"), ("Base", "Base"), ("Text", "Text"))
               if not near(seen[role], QColor(JETBRAINS[key]), ABOUT)]
        assert off == [], f"as the window first showed: {off}"
    else:
        assert not is_dark(seen["Window"]) and not is_dark(seen["Base"]), result["seen"]
        assert is_dark(seen["WindowText"]) and is_dark(seen["Text"]), result["seen"]


# --- 2, 3: applied at once to everything open, with JetBrains-like dark colours ------------------------------------

def test_dark_then_light_apply_at_once_to_the_whole_window_its_panels_menus_and_later_dialogs(win, tmp_path):
    """Spec 2, 3 (the user's case: the system light, Dark chosen in Settings): at once, with no restart, the window
    has IntelliJ's dark colours (tool windows about #2B2D30, the Log about #1E1F22, text about #DFE1E5, selection about
    #2E436E); every panel (Blocks, Properties, Parameters, Flow, Log, Data preview, Run history, the Documentation
    column, the zoom bar) and every menu has a dark background with text that stands out; the canvas is dark and its
    block keeps its group colour; a dialog opened after is dark too. Light then brings it all back to light."""
    tab = win.open_flow(nightly(tmp_path / "nightly.yaml"))
    menu(win, "Documentation").trigger()  # the Documentation column open too
    try:
        select_only(tab, "load")
        tab.view.centerOn(tab.scene.nodes["load"])
        logic = QColor(CATEGORY_COLORS["Logic"])
        assert not (off := whole_window_off(win, dark=False)), "\n".join(off)  # (as today)

        choose(win, "Dark")

        assert jetbrains_off(win) == []
        assert not (off := whole_window_off(win, dark=True)), "\n".join(off)
        assert is_dark(canvas_colour(tab)), canvas_colour(tab).name()
        assert not (off := dialog_off(win, dark=True)), "a dialog opened after:\n" + "\n".join(off)
        node = tab.view.mapFromScene(tab.scene.nodes["load"].sceneBoundingRect()).boundingRect()
        shown = tab.view.viewport().grab(node.intersected(tab.view.viewport().rect())).toImage()
        assert any(near(shown.pixelColor(x, y), logic) for y in range(shown.height()) for x in range(shown.width())), \
            "the block lost its group colour"

        choose(win, "Light")

        assert not (off := whole_window_off(win, dark=False)), "\n".join(off)
        assert not is_dark(canvas_colour(tab)), canvas_colour(tab).name()
        assert not (off := dialog_off(win, dark=False)), "a dialog opened after:\n" + "\n".join(off)
    finally:
        clean(win)


def test_the_code_box_open_takes_the_themes_colours_and_later_boxes_too(win, tmp_path):
    """Spec 2, 3: a Python code box open in Properties when Dark is chosen shows at once its JetBrains-like dark
    colours (keywords, strings, comments, numbers) on a dark background; an SQL box opened after has them too; Light
    then gives the box open its light colours on a light background."""
    tab = win.open_flow(flow_file(tmp_path / "load.yaml", {
        "python": python("def area(r):  # half\n    return 'x', 42, size"),
        "sql": duckdb("select region, 'x', 42 -- all")}))
    try:
        settle()
        python_words = {"keyword": ("def area", "def"), "string": ("'x'",), "comment": ("# half",), "number": ("42",)}
        sql_words = {"keyword": ("select",), "string": ("'x'",), "comment": ("-- all",), "number": ("42",)}

        def shown(words) -> dict:
            box = code_box(win)
            settle()
            behind = background(box)
            found = {kind: color(box, *where) for kind, where in words.items()}
            assert all(contrast(QColor(c), behind) >= HINT for c in found.values()), (found, behind.name())
            return {"dark": is_dark(behind), **found}

        select_only(tab, "python")
        assert shown(python_words) == {"dark": False, **CODE_LIGHT}  # (as today)

        choose(win, "Dark")
        assert shown(python_words) == {"dark": True, **CODE_DARK}, "the code box open kept its colours"
        select_only(tab, "sql")
        assert shown(sql_words) == {"dark": True, **CODE_DARK}

        choose(win, "Light")
        assert shown(sql_words) == {"dark": False, **CODE_LIGHT}, "the code box open kept its colours"
    finally:
        clean(win)


def links_and_text(browser) -> list[tuple[str, str, float]]:
    """(link or text, its colour, its contrast with the column's background) for each piece of the page shown."""
    settle()
    behind = most_common(browser.viewport().grab().toImage(), step=2)
    found, block = [], browser.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            link = fmt.isAnchor() and bool(fmt.anchorHref())
            if fmt.foreground().style() != Qt.NoBrush:
                c = fmt.foreground().color()
            else:
                c = browser.palette().color(QPalette.Link if link else QPalette.Text)
            found.append(("link" if link else "text", c.name(), contrast(c, behind)))
            it += 1
        block = block.next()
    assert any(kind == "link" for kind, _, _ in found), "the page shows no links"
    return found


def unreadable(browser) -> set:
    return {(kind, c, round(k, 2)) for kind, c, k in links_and_text(browser) if k < TEXT}


def test_the_documentation_column_text_and_links_are_readable_in_the_theme_chosen(win):
    """Spec 2 (Documentation column links stayed dark blue): with Help > Documentation open, Dark makes the page shown
    readable at once, its text and its links (not left dark blue on the dark background), and a page opened after; Light
    then makes the page shown readable on the light background."""
    menu(win, "Documentation").trigger()
    browser = win.docs.browser
    assert unreadable(browser) == set()  # (as today)

    choose(win, "Dark")
    assert is_dark(most_common(browser.viewport().grab().toImage(), step=2))
    assert unreadable(browser) == set()
    first = next(href for href in (f.charFormat().anchorHref() for f in fragments(browser))
                 if href and not href.startswith(("http:", "https:", "#")))
    win.docs.open_at(first)
    assert unreadable(browser) == set()

    choose(win, "Light")
    assert not is_dark(most_common(browser.viewport().grab().toImage(), step=2))
    assert unreadable(browser) == set()


def fragments(browser):
    block = browser.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            yield it.fragment()
            it += 1
        block = block.next()


def note_contrast(tab, note_id: str) -> float:
    """How much the text on the canvas `note_id` stands out from the canvas: what it paints (the canvas painted with
    and without it), at its strongest. (Found by its id: a change to the flow makes the canvas's items again.)"""
    note, view = tab.scene.note_items[note_id], tab.view
    view.centerOn(note)
    settle()
    rect = view.mapFromScene(note.sceneBoundingRect()).boundingRect().intersected(view.viewport().rect())
    shown = view.viewport().grab(rect).toImage()
    note.setVisible(False)
    settle()
    behind = view.viewport().grab(rect).toImage()
    note.setVisible(True)
    settle()
    canvas = most_common(behind)
    drawn = [shown.pixelColor(x, y) for y in range(shown.height()) for x in range(shown.width())
             if shown.pixel(x, y) != behind.pixel(x, y)]
    assert drawn, "the text paints nothing"
    return max(contrast(c, canvas) for c in drawn)


def test_text_on_the_canvas_is_readable_in_the_theme_chosen(win, tmp_path):
    """Spec 2 (canvas notes kept the text colour they were made with): a text on the canvas made in the light theme
    is light on the dark canvas as soon as Dark is chosen, a text added after too, and both are dark on the light canvas
    once Light is chosen."""
    tab = win.open_flow(nightly(tmp_path / "nightly.yaml"))
    try:
        settle()
        tab.scene.clearSelection()
        assert note_contrast(tab, "note_1") >= TEXT  # (as today)

        choose(win, "Dark")
        assert note_contrast(tab, "note_1") >= TEXT, "the text kept the colour it was made with"
        win.add_text(tab)  # ("Text")
        settle()
        tab.scene.clearSelection()
        (later,) = set(tab.scene.note_items) - {"note_1"}
        assert note_contrast(tab, later) >= TEXT

        choose(win, "Light")
        assert note_contrast(tab, "note_1") >= TEXT and note_contrast(tab, later) >= TEXT
    finally:
        clean(win)


def grey_texts(win, tab) -> dict[str, float]:
    """How much each grey heading and hint stands out, where it is shown: the Blocks heading, Properties' hint with
    nothing selected, a block's type under its name, an unset Schedule's "Not set yet", a canvas text's line under
    "Text", and Run history's hint."""
    def label(where, starts) -> QLabel:
        settle()
        found = [w for w in where.findChildren(QLabel) if w.isVisible() and starts in w.text()]
        assert found, f"no {starts!r} shown"
        return found[0]

    def second_line(widget) -> QImage:
        r = widget.rect()
        return in_place(widget, QRect(0, r.height() // 2, r.width(), r.height() - r.height() // 2))

    shown = {"BLOCKS": strongest(in_place(label(win.palette_panel, "BLOCKS")))}
    win.right_tabs.setCurrentWidget(win.properties)
    tab.scene.clearSelection()
    shown["Select a block"] = strongest(in_place(label(win.properties, "Select a block")))
    select_only(tab, "load")
    shown["logic.python"] = strongest(second_line(label(win.properties, "logic.python")))
    select_only(tab, "nightly")
    shown["Not set yet"] = strongest(in_place(label(win.properties, "Not set yet")))
    tab.scene.clearSelection()
    tab.scene.note_items["note_1"].setSelected(True)
    shown["on the canvas"] = strongest(second_line(label(win.properties, "on the canvas, for titles")))
    tab.scene.clearSelection()
    win.bottom_tabs.setCurrentWidget(win.history_panel)
    shown["Double-click a run"] = strongest(in_place(label(win.history_panel, "Double-click a run")))
    win.bottom_tabs.setCurrentWidget(win.log)
    return shown


def test_grey_headings_and_hints_stay_readable_in_the_theme_chosen(win, tmp_path):
    """Spec 2, 3 (small grey headings such as BLOCKS nearly disappeared; a few hints use plain grey): every grey
    heading and hint stands out at least 3:1 from what is behind it, in the light theme, at once in Dark, and in Light
    again."""
    tab = win.open_flow(nightly(tmp_path / "nightly.yaml"))
    try:
        settle()
        faint = {name: round(k, 2) for name, k in grey_texts(win, tab).items() if k < HINT}
        assert faint == {}, "light, as today"

        choose(win, "Dark")
        faint = {name: round(k, 2) for name, k in grey_texts(win, tab).items() if k < HINT}
        assert faint == {}, "dark"

        choose(win, "Light")
        faint = {name: round(k, 2) for name, k in grey_texts(win, tab).items() if k < HINT}
        assert faint == {}, "light again"
    finally:
        clean(win)


def scheduler_button(win) -> QToolButton:
    (button,) = [b for b in win.findChildren(QToolButton) if b.defaultAction() is win.scheduler_action]
    return button


def icons(win, tab) -> dict:
    """The zoom bar's magnifier (left of its percentage) and the scheduler button's icon: how much each stands out from
    the bar it is on, and the icon's colours."""
    bar = tab.view.zoom_bar
    inside = bar.contentsRect()  # (not its frame)
    magnifier = in_place(bar, QRect(inside.left(), inside.top(), bar.percent.x() - inside.left(), inside.height()))
    behind = most_common(in_place(scheduler_button(win)))
    icon = win.scheduler_action.icon().pixmap(QSize(20, 20)).toImage()
    return {"magnifier": strongest(magnifier), "scheduler": strongest(icon, behind),
            "colours": [icon.pixelColor(x, y) for y in range(icon.height()) for x in range(icon.width())
                        if icon.pixelColor(x, y).alpha() == 255]}


def test_the_zoom_bar_and_scheduler_button_icons_are_drawn_in_the_theme_chosen(win, tmp_path):
    """Spec 2 (the zoom bar's magnifier and the scheduler button's icon were drawn once): after Dark, the magnifier and
    the scheduler icon stand out at least 3:1 from the bar they are on and the icon has nothing left in the light
    theme's background colour; after Light again, the same on the light bars with nothing of the dark one's."""
    tab = win.open_flow(nightly(tmp_path / "nightly.yaml"))
    try:
        settle()
        before = icons(win, tab)
        assert before["magnifier"] >= HINT and before["scheduler"] >= HINT  # (as today)
        light_base = QApplication.palette().color(QPalette.Base)

        choose(win, "Dark")
        after = icons(win, tab)
        assert round(after["magnifier"], 2) >= HINT, "the magnifier is drawn in the light theme's colours"
        assert round(after["scheduler"], 2) >= HINT, "the scheduler icon does not stand out"
        assert not [c.name() for c in after["colours"] if near(c, light_base)], "the icon keeps the light background"
        dark_base = QApplication.palette().color(QPalette.Base)

        choose(win, "Light")
        again = icons(win, tab)
        assert again["magnifier"] >= HINT and again["scheduler"] >= HINT
        assert not [c.name() for c in again["colours"] if near(c, dark_base)], "the icon keeps the dark background"
    finally:
        clean(win)


def tip_shown(qtbot, widget) -> QWidget:
    """Rest the mouse on `widget`: the tooltip it shows."""
    hide_native_tips()
    at = widget.rect().center()
    QApplication.sendEvent(widget, QHelpEvent(QEvent.ToolTip, at, widget.mapToGlobal(at)))
    qtbot.waitUntil(lambda: any(w.inherits("QTipLabel") and w.isVisible() for w in QApplication.topLevelWidgets()),
                    timeout=5_000)
    return next(w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible())


def tips(win, qtbot, tab) -> dict[str, tuple[bool, float]]:
    """A tooltip (the scheduler button's) and a hover card (Copy Files' Source in Properties): whether each is dark,
    and how much its text stands out."""
    tip = tip_shown(qtbot, scheduler_button(win)).grab().toImage()
    hide_native_tips()
    select_only(tab, "copy")
    card = hover(qtbot, win, row_named(win, "Source", "", win.registry.get("files.copy")).inputs[0], "Source",
                 "Copy Files › Source").grab().toImage()
    dismiss(qtbot, win)
    return {"tooltip": (is_dark(most_common(tip)), round(strongest(tip), 2)),
            "card": (is_dark(most_common(card)), round(strongest(card), 2))}


def test_tooltips_and_hover_cards_take_the_theme_chosen(win, qtbot, tmp_path):
    """Spec 2: a tooltip and a Properties hover card shown after Dark is chosen are dark with readable text; after
    Light, light with readable text."""
    tab = win.open_flow(flow_file(tmp_path / "copy.yaml", {"copy": {"type": "files.copy"}}))
    try:
        settle()
        assert all(not dark and k >= TEXT for dark, k in tips(win, qtbot, tab).values())  # (as today)

        choose(win, "Dark")
        shown = tips(win, qtbot, tab)
        assert all(dark and k >= TEXT for dark, k in shown.values()), shown

        choose(win, "Light")
        shown = tips(win, qtbot, tab)
        assert all(not dark and k >= TEXT for dark, k in shown.values()), shown
    finally:
        clean(win)


def test_the_logs_diagrams_are_drawn_again_on_the_theme_chosen(win):
    """Spec 2: a Mermaid diagram already in the Log is drawn again on the Log's dark background as soon as Dark is
    chosen, and on its light one when Light is."""
    show_log(win, [entry("Plan:\n\n" + fence(unique(SMALL)), "markdown")])
    edit = win.log.text

    choose(win, "Dark")
    [(pos, fmt)] = images(edit)
    assert_drawn_on_the_logs_background(edit, pos, fmt, "dark")

    choose(win, "Light")
    [(pos, fmt)] = images(edit)
    assert_drawn_on_the_logs_background(edit, pos, fmt, "light")


def test_the_logs_text_and_links_are_readable_in_the_theme_chosen(win):
    """Spec 2, 3: a link already in the Log (its colour is fixed when the Markdown is read) is readable on the Log's
    dark background as soon as Dark is chosen, and on its light one when Light is; so is the Log's other text (its
    grey line part is a hint)."""
    show_log(win, [entry("Report ready, see [the docs](https://example.com/x)", "markdown")])
    for theme, dark in (("Dark", True), ("Light", False)):
        choose(win, theme)
        assert is_dark(most_common(win.log.text.viewport().grab().toImage(), step=2)) == dark, theme
        shown = links_and_text(win.log.text)
        faint = {(kind, c, round(k, 2)) for kind, c, k in shown if k < (TEXT if kind == "link" else HINT)}
        assert faint == set(), f"{theme}: {faint}"


# --- 3: System follows the operating system, Light and Dark do not -------------------------------------------------

def test_system_follows_the_operating_system_also_when_it_switches_while_the_editor_is_open(win, system):
    """Spec 3: with System (the default) the editor is dark, with IntelliJ's colours, as soon as the operating system
    switches to dark while it is open, and light again when the system switches back."""
    system("dark")
    assert jetbrains_off(win) == []
    assert not (off := whole_window_off(win, dark=True)), "\n".join(off)

    system("light")
    assert not (off := whole_window_off(win, dark=False)), "\n".join(off)


def test_light_and_dark_stay_whatever_the_operating_system_and_system_follows_it_again(win, system):
    """Spec 3 (Light: when the system is dark, everything is light): on a dark system Light makes everything light, and
    stays light; Dark stays dark when the system switches to light; System then follows the system again."""
    system("dark")
    choose(win, "Light")
    assert not (off := whole_window_off(win, dark=False)), "\n".join(off)

    choose(win, "Dark")
    system("light")
    assert jetbrains_off(win) == []

    system("dark")
    choose(win, "Light")
    system("light")
    system("dark")
    assert not (off := whole_window_off(win, dark=False)), "Light followed the system:\n" + "\n".join(off)

    choose(win, "System")
    assert not (off := whole_window_off(win, dark=True)), "\n".join(off)
    system("light")
    assert not (off := whole_window_off(win, dark=False)), "\n".join(off)


# --- 4: whatever style the application starts with -----------------------------------------------------------------

@pytest.mark.parametrize("style", ["windowsvista", "windows11", "Windows", "Fusion", "macOS"])
def test_dark_is_dark_whatever_style_the_application_starts_with(win, style):
    """Spec 4 (Windows 10's style, windowsvista, cannot be dark): with the application in a style Qt offers on this
    system (Windows 10's and 11's on Windows), Dark gives dark tabs, text fields, buttons and choice boxes with readable
    text, in the window and in a dialog opened after."""
    if style.lower() not in [key.lower() for key in QStyleFactory.keys()]:
        pytest.skip(f"Qt offers no {style} style on this system")
    QApplication.setStyle(style)
    settle()

    choose(win, "Dark")

    def dark_controls(dialog) -> dict:
        add = next(b for b in dialog.findChildren(QPushButton) if b.text().replace("&", "") == "Add server")
        shown = {"button": add.grab().toImage(), "choice": theme_row(dialog).grab().toImage()}
        press(dialog, "Cancel")
        return shown

    shown = {"tabs": win.bottom_tabs.tabBar().grab().toImage(), "search": win.palette_panel.search.grab().toImage(),
             **in_settings(win, dark_controls)}
    light = {name: most_common(image).name() for name, image in shown.items() if not is_dark(most_common(image))}
    assert light == {}, f"{style}: not dark"
    assert strongest(shown["button"]) >= TEXT, f"{style}: the button's text"


# --- 5: README -----------------------------------------------------------------------------------------------------

def test_readme_tells_about_the_theme_setting():
    """Spec 5: README, where it describes Settings, tells about the Theme setting and its System, Light and Dark."""
    paragraphs = (REPO / "README.md").read_text(encoding="utf-8").split("\n\n")
    words = ("settings", "theme", "system", "light", "dark")
    told = [p for p in paragraphs if all(word in p.lower() for word in words)]
    assert told, "README does not tell about Settings' Theme (System, Light, Dark)"
