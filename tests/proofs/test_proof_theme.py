"""Proof: a Theme setting (System, Light, Dark) in Tools > Settings, applied at once to the whole editor, with
JetBrains-style dark colours. How proofs are written: README.md here."""

import json
import os
import subprocess
import sys

import yaml
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette, QPixmap, QStyleHints
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QStyle, QToolTip

from proofs.conftest import SCREEN
from proofs.test_proof_inspect_log import click_tab, hugged, picture_area
from proofs.test_proof_retention_per_start import drag_up
from proofs.test_proof_run_output import click, run_now
from taskloom.editor import theme
from taskloom.editor.canvas import CATEGORY_COLORS
from tests.test_code_editor import background, code_box, color
from tests.test_docs_help import dismiss, hover, row_named
from tests.test_inspect_mermaid import contrast, images, near, painted
from tests.test_paths_editor_editing import menu, press
from tests.test_theme import (ABOUT, CODE_DARK, CODE_LIGHT, HINT, JETBRAINS, TEXT, canvas_colour, dark_standard,
                              icons, in_place, is_dark, jetbrains_off, kept, links_and_text, most_common,
                              not_in_theme, note_contrast, offered, scheduler_button, strongest, theme_row,
                              whole_window_off)

ASKED = ("0.24.0 pdf says: 'The colours follow the editor's theme, light or dark, read as the canvas reads it, in "
         "colours like those of JetBrains' own light and dark themes' <-- I didn't know the editor already had dark "
         "theme but if it doesn't I would like it to be implemented across the board and available on the Settings "
         "screen. You decided: System, Light and Dark; a change applies at once, with no restart; Dark in colours "
         "like JetBrains' own.")
SALES_SQL = "select * from (values ('North', 120), ('South', 80), ('West', 45)) as sales(region, amount)"
CODE = """\
# This week's sales, by region
sales = {"North": 120, "South": 80, "West": 45}
total = 0
for region in sales:
    total += sales[region]
output = total
"""
WORDS = {"keyword": ("for region", "for"), "string": ('"North"',), "comment": ("# This week's",), "number": ("120",)}
REPORT = """\
## This week's sales

**{value}** sold in three regions.

```mermaid
flowchart LR
    load[Load the sales] --> add(Add them up)
    add --> show[Show the report]
```
"""  # filled in like a Python f-string: {value} is total_sales' number
FLOW = {
    "blocks": {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
        "load_sales": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": SALES_SQL}, "ui": {"x": 240, "y": 0}},
        "total_sales": {"type": "logic.python", "config": {"inputs": [], "code": CODE}, "ui": {"x": 0, "y": 190}},
        "report": {"type": "logic.inspect", "config": {"message": REPORT, "markdown": True},
                   "ui": {"x": 240, "y": 190}},
    },
    "edges": ["start_by_hand.start -> load_sales.after", "start_by_hand.start -> total_sales.after",
              "total_sales.output -> report.value"],
    "notes": {"note_1": {"text": "Weekly sales report", "x": 0, "y": -80, "style": "heading", "bold": True}},
}
BLOCKS = list(FLOW["blocks"])
CONNECTIONS = {"warehouse": {"kind": "jdbc", "driver": "org.h2.Driver", "url": "jdbc:h2:tcp://localhost/sales",
                             "jars": "drivers/h2.jar"}}
ROLES = {"Panels": QPalette.Window, "Text areas": QPalette.Base, "Text": QPalette.WindowText,
         "Selection": QPalette.Highlight, "Links": QPalette.Link, "Tooltips": QPalette.ToolTipBase}
LOG_HEIGHT = 300  # the Log made taller: report's lines and its diagram, all in view
NEXT_START = r"""
import json, os, sys, time, traceback
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

# On the proofs' offscreen screen (screen.json, in the folder it runs in), in the font the proofs show the editor in.
app = QApplication(["taskloom", "-platform", "offscreen:configfile=screen.json"])
if sys.platform == "win32" and "Segoe UI" in QFontDatabase.families():
    app.setFont(QFont("Segoe UI", 9))
elif sys.platform == "linux" and "DejaVu Sans" in QFontDatabase.families():
    app.setFont(QFont("DejaVu Sans", 9))
app.setStyle("Windows")  # in Qt's classic Windows look, as the proof's editor (the offscreen screen's own is Fusion)
look = app.style().name()

from taskloom import cli
from taskloom.editor import window

boxes, seen, editors = [], {}, []
for name in ("information", "warning", "question", "critical"):  # (a message box would wait for a click)
    setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: boxes.append([_n, *map(str, a[1:3])])
                                            or QMessageBox.No))
window.MainWindow.start_scheduler = lambda self, by_you=False: None  # no scheduler service from a proof
show = window.MainWindow.show


def shown(self):
    show(self)
    if not seen:  # its colours as it first shows, before anything is painted
        own, log = self.palette(), self.log.text.palette()
        seen.update(Window=own.color(QPalette.Window).name(), WindowText=own.color(QPalette.WindowText).name(),
                    Base=log.color(QPalette.Base).name(), Text=log.color(QPalette.Text).name(),
                    style=QApplication.style().name())
        editors.append(self)


window.MainWindow.show = shown


def shoot(app):  # instead of waiting for you to close it: its picture once it has settled, then it ends
    editor = editors[0]
    deadline = time.monotonic() + 30
    while editor.current is None and time.monotonic() < deadline:
        QTest.qWait(50)
    QTest.qWait(1500)
    editor.grab().save(sys.argv[1])

    def place(widget):
        at = widget.mapTo(editor, QPoint(0, 0))
        return [at.x(), at.y(), widget.width(), widget.height()]

    tab = editor.current
    seen["flows"] = [t.doc.title for t in editor.tabs_list()]
    seen["parts"] = {"blocks": place(editor.palette_panel), "canvas": place(tab.view) if tab else None,
                     "inspector": place(editor.right_tabs), "output": place(editor.bottom_tabs)}
    return 0


QApplication.exec = shoot
try:
    code = cli.main(["editor"])
except BaseException:  # (the editor's crash log would take it: said here instead)
    traceback.print_exc(file=sys.stdout)
    sys.stdout.flush()
    os._exit(2)
print(json.dumps({"seen": seen, "boxes": boxes, "code": code, "look": look}), flush=True)
os._exit(0)
"""


def settle():
    for _ in range(5):
        QApplication.processEvents()


def open_flow(win, monkeypatch, flow):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    menu(win, "Open…").trigger()
    return win.current


def in_settings(p, win, then):
    """Tools > Settings…: then(dialog) while it is open (it closes it with Save or Cancel)."""
    p.while_open(lambda: menu(win, "Settings…").trigger(), then)
    settle()


def theme_list(row) -> tuple:
    """Click the Theme row: its list opens. The list, and where each choice is in it."""
    row.showPopup()
    QTest.qWait(QApplication.doubleClickInterval() + 100)  # (a click just after it opens is not a choice)
    view = row.view()
    return view, {row.itemText(i): view.visualRect(view.model().index(i, 0)) for i in range(row.count())}


def click_choice(row, view, places: dict, name: str):
    """Click `name` in the Theme row's open list."""
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, places[name].center())
    QTest.qWait(50)
    assert row.currentText() == name, f"{name} was not chosen: {row.currentText()}"


def choose_theme(p, win, name: str):
    """Tools > Settings…, Theme: `name`, Save."""
    def then(dialog):
        row = theme_row(dialog)
        click_choice(row, *theme_list(row), name)
        press(dialog, "Save")
    in_settings(p, win, then)


def in_dialog(widget, rect: QRect, dialog) -> QRect:
    """`rect` of `widget` (another window, e.g. an open list) in a screenshot of `dialog`."""
    return QRect(widget.mapToGlobal(rect.topLeft()) - dialog.mapToGlobal(QPoint(0, 0)), rect.size())


def in_window(win, widget, rect: QRect | None = None) -> QRect:
    """`rect` of `widget` (all of it by default) in a screenshot of the window."""
    rect = rect or widget.rect()
    return QRect(widget.mapTo(win, rect.topLeft()), rect.size())


def code_colours(win) -> dict:
    """The code box's colour for each kind of word, and its background."""
    box = code_box(win)
    settle()
    return {"background": background(box).name(), **{kind: color(box, *where) for kind, where in WORDS.items()}}


def code_lines(win) -> QRect:
    """The code box's lines, in a screenshot of the window."""
    box = code_box(win)
    return in_window(win, box.viewport(), QRect(0, 0, 520, box.fontMetrics().lineSpacing() * 7 + 12))


def text_contrast(widget) -> float:
    """How much a widget's text stands out from its background, in the colours it is given."""
    palette = widget.palette()
    return contrast(palette.color(QPalette.Text), palette.color(QPalette.Base))


def blocks_heading(win) -> QLabel:
    return next(w for w in win.palette_panel.findChildren(QLabel) if w.text() == "BLOCKS")


def keeps_its_colour(tab, block: str, colour: str) -> bool:
    """The block, as the canvas paints it, shows its group's colour (behind its icon)."""
    node = tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect()).boundingRect()
    shown = tab.view.viewport().grab(node.intersected(tab.view.viewport().rect())).toImage()
    return any(near(shown.pixelColor(x, y), QColor(colour)) for y in range(shown.height()) for x in range(shown.width()))


def diagram_shown(win) -> tuple[bool, float]:
    """Whether the Log's diagram is drawn on the Log's background (its margin is that colour), and how much its lines
    and words stand out from it (the highest contrast, 1 to 21)."""
    edit = win.log.text
    [(pos, fmt)] = images(edit)
    base = edit.palette().color(QPalette.Base)
    margin = painted(edit, pos, fmt, corner=True)
    return all(near(c, base) for c in margin), max(contrast(c, base) for c in painted(edit, pos, fmt))


def the_look(win, tab) -> dict:
    """What the editor looks like: Qt's look (its style), the application's colours and the tooltips', and how the
    canvas, the code box, the Log, the Documentation column and the text on the canvas are painted."""
    palette, tips = QApplication.palette(), QToolTip.palette()
    return {"style": QApplication.style().name(),
            "colours": {f"{group.name} {role.name}": palette.color(group, role).name()
                        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled) for role in QPalette.ColorRole
                        if role not in (QPalette.NColorRoles, QPalette.NoRole)},
            "tooltips": {role.name: tips.color(role).name() for role in (QPalette.ToolTipBase, QPalette.ToolTipText)},
            "canvas": canvas_colour(tab).name(), "code": code_colours(win),
            "log": most_common(win.log.text.viewport().grab().toImage(), step=2).name(),
            "documentation": sorted({c for _, c, _ in links_and_text(win.docs.browser)}),
            "text on the canvas": tab.scene.note_items["note_1"].defaultTextColor().name()}


def main_colours(win) -> dict:
    """The main colours the window shows (the text areas: the Log's)."""
    window = win.palette()
    found = {label: window.color(role).name() for label, role in ROLES.items()}
    found["Text areas"] = win.log.text.palette().color(QPalette.Base).name()
    found["Tooltips"] = QToolTip.palette().color(QPalette.ToolTipBase).name()
    return found


def colours_table(columns: dict[str, dict]) -> str:
    """The main colours, one row each, one column per moment."""
    rows = [f"{'':<12}" + "".join(f"{name:<14}" for name in columns)]
    for label in ROLES:
        rows.append(f"{label:<12}" + "".join(f"{shown[label].upper():<14}" for shown in columns.values()))
    return "\n".join(rows)


def test_a_theme_setting_light_dark_or_as_the_system_applied_at_once(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("A Theme setting: System, Light or Dark, applied at once to the whole editor", ASKED)
    p.idea("Tools > Settings now starts with a Theme row: System, Light or Dark. System, the default, is light or "
           "dark as your computer is, and follows it when you switch your computer while the editor is open. Light "
           "and Dark stay as you chose them, whatever your computer is.")
    p.idea("Save shows it at once in the whole editor: the window and each of its panels, the canvas and its texts, "
           "the code boxes, the Documentation column, the Log and its diagrams, the menus, tooltips and hover cards, "
           "and the dialogs you open after. Nothing restarts: the flow, its results and what you selected stay.")
    p.idea("Dark has colours like those of JetBrains' dark theme (as in IntelliJ IDEA and PyCharm): grey panels "
           "(#2B2D30), darker text areas (#1E1F22), light grey text (#DFE1E5), and the code colours the code boxes "
           "already had on a dark background. Your choice is kept for the next start, which shows it from the "
           "first moment.")
    p.idea("Before, the editor had no theme of its own: it took its colours from your computer, as far as Qt follows "
           "it (Windows 10's own look cannot be dark), and when they changed while it was open, a few parts kept the "
           "colours they were first drawn in.")
    p.idea("Two things stay as your computer has them: the window's frame (its title bar) and the computer's own "
           "Open and Save windows, which the operating system draws, not the editor.")
    p.why_simplest("Qt, which draws the editor, gives its windows one set of colours: for panels, text areas, text, "
                   "buttons, selection, links and tooltips. A theme is just that set, given to the whole application "
                   "at once: every window, dialog, menu and tooltip takes it, also those opened later. Taskloom's own "
                   "drawings, the canvas and its blocks, already read it each time they are painted.")
    p.why_simplest("Only what was drawn once, in the colours of that moment, is drawn again when they change: the code "
                   "boxes' colours, the Documentation's links, the Log and its diagrams, the grey headings and hints "
                   "(such as BLOCKS), the zoom bar's magnifier, "
                   "the scheduler button's icon, the flow tabs' clocks and the texts on the canvas.")
    p.why_simplest("Dark is shown in Qt's Fusion look, which is the same on every system and draws everything in the "
                   "theme's colours: some systems' own looks, Windows 10's among them, draw their buttons and tabs in "
                   "the system's colours and cannot be dark. Light, on a light system, is Qt's own look, exactly as "
                   "before.")
    p.why_simplest("The choice is kept in editor.ini, the editor's own preferences, which already remember the flows "
                   "you had open and Wrap lines, and it is read before any of the window is made: the window never "
                   "shows in other colours first.")
    p.alternative("Keeping the theme in settings.yaml, with the other Settings",
                  "settings.yaml holds what runs use (the calendar, how long runs are kept, …): the scheduler service "
                  "and the runs read it, and refuse a setting they do not know. The theme is only the editor's look, "
                  "which nothing that runs needs.")
    p.alternative("A style sheet (Qt's CSS) for the whole editor",
                  "It would have to name each kind of widget in each of its states, and Taskloom's own drawings, which "
                  "read Qt's set of colours, would not see it: two places to keep in step, instead of one.")
    p.alternative("Applying it at the next start",
                  "Everything would be made in the new colours, with nothing to draw again. But you decided that a "
                  "change applies at once, with no restart.")
    p.alternative("Following your computer only, as before",
                  "There would be no choice on the Settings screen, which you asked for, and on Windows 10 the editor "
                  "could never be dark.")
    p.fits("The code boxes, the canvas and the Log's diagrams already chose their light or dark colours by the "
           "window's background (the 0.24.0 proof's “read as the canvas reads it”): the theme now sets that "
           "background, and they follow it with nothing of their own.")
    p.fits("The Log keeps the last 32 Mermaid diagrams it has drawn, each by the background it was drawn on: after "
           "the first switch, switching back draws nothing new.")
    p.fits("Nothing that runs is touched: the scheduler service and the runs have no window, and settings.yaml stays "
           "as it was.")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings writes Windows' Startup script there)
    found = {"style": QApplication.style().name(), "palette": QApplication.palette(), "tips": QToolTip.palette()}
    system = []  # the operating system's light or dark mode, once the proof turns it (step 14)
    flow = tmp_path / "weekly-sales.yaml"
    flow.write_text(yaml.safe_dump(FLOW, sort_keys=False), encoding="utf-8")
    (win.home.root / "connections.yaml").write_text(yaml.safe_dump(CONNECTIONS), encoding="utf-8")
    try:
        QApplication.setStyle("Windows")  # (put back in finally)
        settle()
        p.step("Open the Documentation column (Help > Documentation), drag the top edge of the Log up a little, open "
               "the flow weekly-sales.yaml (File > Open…) and press ▶ Run now.")
        p.note("On this proof's offscreen screen, Qt's own look is already Fusion, the look Dark is shown in, so a "
               "switch to it could not be seen. The proof first puts the editor in Qt's classic Windows look, which Qt "
               "has on every system, to stand for a system's own look such as Windows 10's: the light screenshots show "
               "it. Taskloom's tests also check that Dark is dark in each look Qt offers where they run (Windows 10's "
               "and 11's on Windows).")
        menu(win, "Documentation").trigger()
        drag_up(win, win.output, LOG_HEIGHT - win.output.height())
        qtbot.waitUntil(lambda: win.output.height() >= LOG_HEIGHT - 30, timeout=5_000)
        tab = open_flow(win, monkeypatch, flow)
        p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
        run = run_now(win, tab, qtbot)
        p.check("The run ends with success", run.status == "success")

        p.step("Click total_sales.")
        click(tab, "total_sales")
        click_tab(win, win.log)
        light = main_colours(win)
        before = the_look(win, tab)
        p.shot(win, "The editor as it is, light, in Qt's Windows look. The flow has blocks of four groups (Start, "
                    "Data, Logic, Debug) and a text on the canvas; ① total_sales is selected, and ② Properties shows "
                    "its code; the Documentation column is open, on the right.",
               highlight=["total_sales", code_box(win)], badges=True)

        p.step("Choose Tools > Settings…")

        def to_dark(dialog):
            row = theme_row(dialog)
            choices = offered(row)
            label = next(w for w in dialog.findChildren(QLabel) if w.text() == "Theme")
            top_left = label.mapTo(dialog, QPoint(0, 0))
            p.shot(dialog, "① Theme is Settings' first row, and System is chosen: nobody chose a theme yet.",
                   highlight=[[label, row]], badges=True,
                   zoom=[QRect(top_left.x(), top_left.y(), 560, 3 * row.height())])
            p.check("Settings has a Theme row offering System, Light and Dark, with System chosen",
                    choices == (["System", "Light", "Dark"], "System"))
            p.step("Click the Theme row, choose Dark and press Save.")
            view, places = theme_list(row)
            listed = in_dialog(view.window(), view.window().rect(), dialog)
            p.shot(view.window(), "① Its list: System (light or dark as your computer is), Light and Dark.",
                   over=dialog, badges=True, highlight=[listed],
                   zoom=[QRect(top_left.x(), top_left.y(), 560, listed.bottom() - top_left.y())])
            click_choice(row, view, places, "Dark")
            press(dialog, "Save")

        in_settings(p, win, to_dark)
        kept(win, "Dark")
        dark = main_colours(win)
        p.shot(win, "At once, the whole window is dark: ① the Blocks list, ② the canvas, ③ Properties, ④ the "
                    "Documentation column and ⑤ the Log. The flow is still open, total_sales still selected, and the "
                    "blocks show their results, each in its group's colour: nothing restarted.",
               highlight=[win.palette_panel, tab.view, win.right_tabs, win.docs.browser, win.bottom_tabs],
               badges=True, zoom=BLOCKS)
        p.note("The screenshots show what the editor draws, without the window's frame (its title bar), which the "
               "operating system draws: on your computer the frame stays as your system has it.")
        p.check("It is the same window, with the same flow open and its blocks still showing their results: no restart",
                win.isVisible() and win.current is tab and all(tab.scene.nodes[b].status == "success" for b in BLOCKS))
        p.check("The main colours are like JetBrains' dark theme: panels about #2B2D30, text areas (the Log) about "
                "#1E1F22, text about #DFE1E5, selection about #2E436E", jetbrains_off(win) == [])
        p.check("Every part of the window, in each tab of the Inspector and of the Output, and every menu, has a dark "
                "background with text that stands out from it", whole_window_off(win, dark=True) == [])
        p.check("② The canvas is dark, and its blocks keep their groups' colours (Start, Data, Logic, Debug)",
                is_dark(canvas_colour(tab))
                and all(keeps_its_colour(tab, block, CATEGORY_COLORS[group]) for block, group in
                        (("start_by_hand", "Start"), ("load_sales", "Data"), ("total_sales", "Logic"),
                         ("report", "Debug"))))
        p.check(f"Dark is shown in Qt's Fusion look, instead of the {before['style'].capitalize()} look the editor "
                "was in", before["style"].lower() == "windows" and QApplication.style().name().lower() == "fusion")

        p.step("Look at the code box, in Properties.")
        shown = code_colours(win)
        p.shot(win, "① total_sales' code, in JetBrains-like colours on the dark text area: keywords (for, in) "
                    "orange, strings green, comments grey, numbers teal.", highlight=[code_box(win)], badges=True,
               zoom=[code_lines(win)])
        p.check("The code box is dark, and shows keywords, strings, comments and numbers in the dark colours",
                is_dark(QColor(shown["background"])) and {k: shown[k] for k in WORDS} == CODE_DARK)
        p.check("Keywords, strings and numbers stand out from it at least 4.5 to 1, comments (grey, quieter) at least "
                "3 to 1", all(contrast(QColor(shown[k]), QColor(shown["background"])) >= TEXT
                             for k in ("keyword", "string", "number"))
                and contrast(QColor(shown["comment"]), QColor(shown["background"])) >= HINT)

        p.step("Look at the Documentation column.")
        browser = win.docs.browser
        pieces = links_and_text(browser)
        p.shot(win, "① The Documentation column: its text light grey on the dark page, its links light blue.",
               highlight=[browser], badges=True,
               zoom=[in_window(win, browser.viewport(), QRect(0, 0, browser.viewport().width(), 420))])
        p.check("The Documentation column's page is dark", is_dark(most_common(browser.viewport().grab().toImage(), 2)))
        p.check("Each of its words and links stands out from it at least 4.5 to 1 (none left dark blue)",
                all(k >= TEXT for _, _, k in pieces) and any(kind == "link" for kind, _, _ in pieces))

        p.step("Look at the Blocks list, the canvas and the toolbar.")
        heading, bar, button = blocks_heading(win), tab.view.zoom_bar, scheduler_button(win)
        note = tab.scene.note_items["note_1"]
        note_area = in_window(win, tab.view.viewport(), tab.view.mapFromScene(note.sceneBoundingRect()).boundingRect())
        p.shot(win, "① BLOCKS, the Blocks list's grey heading, and the list with each block's icon. ② The text on the "
                    "canvas, light on the dark canvas. ③ The zoom bar: its magnifier is drawn light.",
               highlight=[[heading, win.palette_panel.tree], note_area, bar], badges=True,
               zoom=[heading, note_area, bar, in_window(win, win.palette_panel.tree, QRect(0, 0, 200, 130))])
        p.shot(win, "④ The scheduler button, at the right of the toolbar: its icon a lighter grey than in light, to "
                    "stand out from the dark toolbar.", highlight=[button], badges=[4], zoom=[button])
        stand_out = icons(win, tab)
        h, v = tab.view.horizontalScrollBar().value(), tab.view.verticalScrollBar().value()
        on_canvas = note_contrast(tab, "note_1")  # (it scrolls the canvas to the text: scrolled back after)
        tab.view.horizontalScrollBar().setValue(h)
        tab.view.verticalScrollBar().setValue(v)
        p.check("① BLOCKS, a grey heading meant to be quieter, stands out at least 3 to 1",
                strongest(in_place(heading)) >= HINT)
        p.check("② The text on the canvas stands out from the canvas at least 4.5 to 1", on_canvas >= TEXT)
        p.check("③ The magnifier and ④ the scheduler button's icon stand out at least 3 to 1 from what they are on",
                round(stand_out["magnifier"], 2) >= HINT and round(stand_out["scheduler"], 2) >= HINT)

        p.step("Click report: the Log shows its lines, with its Mermaid diagram.")
        click(tab, "report")
        edit = win.log.text
        edit.verticalScrollBar().setValue(0)
        QTest.qWait(100)
        [(pos, fmt)] = images(edit)
        p.shot(win, "① report's Mermaid diagram, drawn again on the Log's dark background in Mermaid's dark colours, "
                    "under report's heading and line.", highlight=[hugged(picture_area(win, pos, fmt))], badges=True,
               zoom=[in_window(win, edit.viewport(), QRect(0, 0, 700, edit.viewport().height()))])
        on_log, diagram = diagram_shown(win)
        p.check("The Log is dark, and its text stands out at least 4.5 to 1",
                is_dark(edit.palette().color(QPalette.Base)) and text_contrast(edit) >= TEXT)
        p.check("① The diagram is drawn on the Log's dark background, its lines and words standing out at least "
                "3 to 1", on_log and diagram >= HINT)

        p.step("Click load_sales, then the Data preview tab, under the canvas.")
        click(tab, "load_sales")
        click_tab(win, win.preview)
        table = win.preview.view
        rows = table.horizontalHeader().height() + 3 * table.rowHeight(0)
        p.shot(win, "① Data preview shows load_sales' table, on a dark background.",
               highlight=[in_window(win, table, QRect(0, 0, 420, rows))], badges=True,
               zoom=[in_window(win, win.preview.label), in_window(win, table, QRect(0, 0, 560, rows + 8))])
        p.check("Data preview shows load_sales' three rows on a dark background, its text standing out at least 4.5 "
                "to 1", table.model().rowCount() == 3 and is_dark(most_common(in_place(table.viewport())))
                and text_contrast(table) >= TEXT)

        p.step("Click the Run history tab.")
        click_tab(win, win.history_panel)
        runs = win.history_panel.table
        rows = runs.horizontalHeader().height() + runs.rowHeight(0)
        p.shot(win, "① Run history lists the run, on a dark background.",
               highlight=[in_window(win, runs, QRect(0, 0, 560, rows))], badges=True,
               zoom=[in_window(win, runs, QRect(0, -26, 700, rows + 34))])
        p.check("Run history lists the run on a dark background, its text standing out at least 4.5 to 1",
                runs.rowCount() == 1 and is_dark(most_common(in_place(runs.viewport())))
                and text_contrast(runs) >= TEXT)
        click_tab(win, win.log)

        p.step("Click total_sales and rest the mouse on Inputs, in Properties.")
        click(tab, "total_sales")
        inputs = row_named(win, "Inputs", "", win.registry.get("logic.python"))
        card = hover(qtbot, win, inputs.inputs[0], "Inputs", "Python Code › Inputs")
        image = card.grab().toImage()
        p.shot(card, "① Its hover card is dark too, with light text and a light blue Learn more ›.", over=win,
               highlight=[card], badges=True, zoom=[card, inputs.label_widget])
        p.check("The hover card is dark, its text standing out at least 4.5 to 1",
                is_dark(most_common(image)) and strongest(image) >= TEXT)
        dismiss(qtbot, win)

        p.step("Choose Tools > Connections…")

        def connections(dialog):
            off = not_in_theme(dialog, dark=True)
            p.shot(dialog, "A dialog opened after the switch is dark too: its list, fields, table and buttons.")
            p.check("Every part of the Connections dialog has a dark background with text that stands out", off == [])
            press(dialog, "Cancel")

        p.while_open(lambda: menu(win, "Connections…").trigger(), connections)

        p.step("Choose Light in Tools > Settings and press Save.")
        choose_theme(p, win, "Light")
        kept(win, "Light")
        after = the_look(win, tab)
        p.shot(win, "Everything is light again, at once, as before Dark: ① total_sales' code in its light colours.",
               highlight=[code_box(win)], badges=True, zoom=[code_lines(win)])
        p.text("The main colours: before, in Dark, and in Light again",
               colours_table({"Before": light, "Dark": dark, "Light again": main_colours(win)}))
        p.check("Every part of the window, and every menu, is light again", whole_window_off(win, dark=False) == [])
        p.check(f"Light is Qt's own look again, the {before['style'].capitalize()} look the editor was in before "
                "Dark", after["style"] == before["style"] != "fusion")
        p.check("It looks exactly as before Dark: the same colours for every part of every window and for tooltips, "
                "and the canvas, the code box, the Log, the Documentation column and the text on the canvas painted "
                "in the same colours", after == before)
        p.check("① The code box shows its light colours again", {k: after["code"][k] for k in WORDS} == CODE_LIGHT)

        p.step("Choose System in Tools > Settings and press Save, then turn your computer's dark mode on.")
        choose_theme(p, win, "System")
        p.note("The proof runs offscreen, with no real light or dark mode to switch: it tells the editor that the "
               "system turned dark the way Qt tells it on a real computer (the system's colour scheme is now dark, and "
               "the style's standard colours are dark), as Taskloom's tests do.")
        system.append(Qt.ColorScheme.Light)
        monkeypatch.setattr(QStyleHints, "colorScheme", lambda self: system[0])
        standard = QStyle.standardPalette
        monkeypatch.setattr(QStyle, "standardPalette",
                            lambda self: dark_standard() if system[0] == Qt.ColorScheme.Dark else standard(self))

        def turn(scheme):
            system[0] = scheme
            QGuiApplication.styleHints().colorSchemeChanged.emit(scheme)
            settle()
            QTest.qWait(100)

        turn(Qt.ColorScheme.Dark)
        p.shot(win, "With System chosen, the editor turned dark as soon as the computer did, with nothing clicked.",
               zoom=BLOCKS)
        p.check("The editor is dark, in the JetBrains-like colours, every part of it",
                jetbrains_off(win) == [] and whole_window_off(win, dark=True) == [])

        p.step("Turn your computer's dark mode off again.")
        turn(Qt.ColorScheme.Light)
        p.shot(win, "Light again, with the computer.", zoom=BLOCKS)
        p.check("The editor is light again, every part of it", whole_window_off(win, dark=False) == [])

        p.step("Choose Dark in Tools > Settings, press Save, and close the editor.")
        choose_theme(p, win, "Dark")
        kept(win, "Dark")
        ini = (win.home.root / "editor.ini").read_text(encoding="utf-8")
        settings_file = win.home.root / "settings.yaml"
        written = settings_file.read_text(encoding="utf-8") if settings_file.exists() else ""
        win.close()
        settle()
        p.text("editor.ini, the editor's own preferences in your Taskloom folder: its line with the theme",
               "\n".join(line for line in ini.splitlines() if line.startswith("theme=")))
        p.text("settings.yaml, which the scheduler service and the runs read: only the settings changed from their "
               "defaults (none here)", written)
        p.check("editor.ini holds Dark", "theme=Dark" in ini.splitlines())
        p.check("settings.yaml holds no theme", "theme" not in written.lower() and "dark" not in written.lower())

        p.step("Start the editor again (taskloom editor).")
        p.note("The proof starts the editor in a process of its own, as taskloom editor does, on the same Taskloom "
               "folder, in Qt's Windows look as above and with no scheduler service; once it has settled, it saves a "
               "picture of its window and ends.")
        folder = tmp_path / "next-start"
        folder.mkdir()
        (folder / "screen.json").write_text(json.dumps({"screens": [{
            "name": "proof", "x": 0, "y": 0, "width": SCREEN[0], "height": SCREEN[1], "logicalDpi": 96,
            "logicalBaseDpi": 96}]}), encoding="utf-8")
        picture = folder / "editor.png"
        started = subprocess.run([sys.executable, "-c", NEXT_START, str(picture)], cwd=folder, capture_output=True,
                                 text=True, errors="replace", timeout=180,
                                 env={**os.environ, "TASKLOOM_HOME": str(win.home.root),
                                      "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"})
        lines = [line for line in started.stdout.splitlines() if line.startswith("{")]
        assert started.returncode == 0 and lines and picture.exists(), started.stdout + started.stderr
        result = json.loads(lines[-1])
        seen = result["seen"]
        again = QLabel()
        again.setPixmap(QPixmap(str(picture)))
        again.resize(again.pixmap().size())
        qtbot.addWidget(again)
        canvas = QRect(*seen["parts"]["canvas"])
        p.shot(again, "The new editor, as it started: dark at once, with weekly-sales.yaml open again. (A flow you "
                      "open starts clean: its blocks show no results until it runs.) ① The Blocks list, ② the canvas, "
                      "③ Properties and ④ the Log.",
               highlight=[QRect(*seen["parts"][k]) for k in ("blocks", "canvas", "inspector", "output")],
               badges=True, zoom=[QRect(canvas.x(), canvas.y(), canvas.width(), min(canvas.height(), 400))])
        first = {k: QColor(seen[k]) for k in ("Window", "WindowText", "Base", "Text")}
        p.check("It started with no message", result["boxes"] == [] and result["code"] == 0)
        p.check("It reopened weekly-sales.yaml", seen.get("flows") == ["weekly-sales.yaml"])
        p.check("It was started in Qt's Windows look; as its window first showed, before anything was painted, it had "
                "Dark's colours, in Qt's Fusion look: panels about #2B2D30, text areas about #1E1F22, text about "
                "#DFE1E5", result["look"].lower() == "windows" and seen["style"].lower() == "fusion"
                and near(first["Window"], QColor(JETBRAINS["Window"]), ABOUT)
                and near(first["Base"], QColor(JETBRAINS["Base"]), ABOUT)
                and near(first["WindowText"], QColor(JETBRAINS["Text"]), ABOUT)
                and near(first["Text"], QColor(JETBRAINS["Text"]), ABOUT))
    finally:  # Taskloom and the application's look as the proof found them: System, Qt's own colours
        if system:
            system[0] = Qt.ColorScheme.Light
        win.settings.setValue("theme", "System")
        win.settings.sync()
        theme.apply("System")
        if QApplication.style().name() != found["style"]:
            QApplication.setStyle(found["style"])
        QApplication.setPalette(found["palette"])
        QToolTip.setPalette(found["tips"])
        settle()
