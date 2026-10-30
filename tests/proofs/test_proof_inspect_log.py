"""Proof: Inspect shows tables, pictures and Markdown in the Log. How proofs are written: README.md here.

The flow reads its table with a Run Query block from a real database (H2, through its Java driver), as the person
who asked does: the `jdbc_env` fixture needs Java."""

import inspect
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import yaml
from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFontInfo, QPalette, QPixmap, QTextCursor, QTextDocument, QTextFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from proofs.test_proof_retention_per_start import click_tick, drag_up, history_rows
from proofs.test_proof_run_output import click, hover, run_now, setting
from tests.test_inspect_log import (DARK, MERMAID_NOTE, OLD_SCHEMA, block_of, contrast, fixed_pitch_at, images,
                                    size_of, table_measurements)

ASKED = ("Inspect block: The query block result tooltip on hover displays the table well constructed (looks like a "
         "table) but when Inspect prints it spaces are not respected and it no longer looks like a table, the Inspect "
         "block should pride itself to be the best place to display text information, everything should be well "
         "formated, beautiful even (i.e: have markdown/mermaide support; I am most likely missing similar text "
         "beatify tech) If the input of the inspect is an image path then the log of the Inspect block (and "
         "therefore the global log as well) should show the image, this means the log output may require a re-work")
SQL = """\
select 'PS-' || (100 + x) as "publishing_system_id",
       cast(mod(x * 37, 23) + 5 as int) as "articles",
       cast(mod(x * 7919, 900) / 7.0 as double precision) as "share",
       cast(null as varchar(20)) as "note"
from system_range(1, 15)
"""
MESSAGE = """\
# Weekly sales check

Sales are **up** this week, see `sales.result`.

- articles: what each system published
- share: its part of the sales

What the flow does:

| Step | Blocks | How many |
|------|--------|---------:|
| Read | sales | 1 |
| Look | look_at_sales, look_at_chart | 2 |

Code keeps its spaces:

```
keep   these   spaces
PS-101    19
```

<b>not bold: written as it is</b>

```mermaid
graph LR
    sales --> chart
```
"""
BODY = "Sales are up this week, see sales.result."  # the paragraph of MESSAGE, drawn as Markdown
CELLS = [["Step", "Blocks", "How many"], ["Read", "sales", "1"], ["Look", "look_at_sales, look_at_chart", "2"]]
FLOW = {
    "blocks": {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 160}},
        "sales": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": SQL}, "ui": {"x": 260, "y": 160}},
        "look_at_sales": {"type": "logic.inspect", "ui": {"x": 540, "y": 0}},
        "chart": {"type": "reports.chart", "config": {"kind": "bar", "x": "publishing_system_id", "y": ["articles"],
                                                      "title": "Articles by publishing system"},
                  "ui": {"x": 540, "y": 160}},
        "look_at_chart": {"type": "logic.inspect", "ui": {"x": 820, "y": 80}},
        "summary": {"type": "logic.inspect", "config": {"message": MESSAGE}, "ui": {"x": 820, "y": 260}},
    },
    "edges": ["start_by_hand.start -> sales.after", "sales.result -> look_at_sales.value", "sales.result -> chart.table",
              "chart.image -> look_at_chart.value", "chart.image -> summary.value"],
}
INSPECTS = ["look_at_sales", "look_at_chart", "summary"]
LOG_HEIGHT = 520  # the Log made taller: a chart's picture (360 pixels high) and the lines around it
SEPARATORS = "│┆┬┴┼╪┌┐└┘╞╡"  # the characters between a text table's columns, on its rows and on its borders
SCALES = ["1", "1.25", "1.5"]  # Windows' display scales: 100%, 125%, 150%
GONE = "(the picture is no longer there)"
OLD_LINE = "A line from version 0.24, before this change"
# How the Log draws a table, measured in a program of its own (on Windows: on Qt's real Windows platform), with a
# picture of the table as that Log shows it.
MEASURE = textwrap.dedent(inspect.getsource(table_measurements)) + textwrap.dedent('''
    import json, sys
    from pathlib import Path
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QFontDatabase, QTextCursor
    from PySide6.QtWidgets import QApplication

    app = QApplication([sys.argv[0]])
    print("started", flush=True)
    from taskloom.editor.panels import LogPanel

    table = Path(sys.argv[1]).read_text(encoding="utf-8")
    log = LogPanel()
    log.resize(1100, 700)
    log.show()
    log.set_entries([{"ts": "2026-10-29T09:00:00.000", "block": "look_at_sales", "level": "INFO", "message": table}])
    app.processEvents()
    measured = table_measurements(log.text, table)
    if measured["found"]:  # the picture: from the Log's top left to the table's last line and last column
        end = QTextCursor(log.text.document())
        end.setPosition(log.text.document().toPlainText().find(table.splitlines()[-1]))
        right = max(x for layout in measured["layouts"] for x in layout)
        log.text.viewport().grab(QRect(0, 0, right + 16, log.text.cursorRect(end).bottom() + 8)).save(sys.argv[2])
    print(json.dumps({**measured, "fonts": bool(QFontDatabase.families()), "platform": app.platformName()}),
          flush=True)
''')


# --- what the Log shows -------------------------------------------------------------------------------------------

def plain(win) -> str:
    return win.log.text.document().toPlainText()


def lines(win, start: int = 0) -> list[str]:
    """The Log's lines (from the position `start` on), as it shows them."""
    return [line.rstrip() for line in plain(win)[start:].split("\n")]


def find(win, text: str, start: int = 0) -> int:
    """The position of `text` in the Log (the first from `start` on)."""
    found = plain(win).find(text, start)
    assert found >= 0, (text, plain(win)[start:][-3000:])
    return found


def run_entries(win, block: str, run_id: int) -> list[tuple]:
    """(message, show) of each Log entry of `block` in run #`run_id`."""
    found, current = [], False
    for e in win.log.entries:
        if e.get("header") and "started" in e.get("message", ""):
            current = f"run #{run_id} · " in e["message"]
        elif current and e.get("block") == block:
            found.append((e["message"], e.get("show")))
    return found


def rect_at(edit, pos: int) -> QRect:
    cursor = QTextCursor(edit.document())
    cursor.setPosition(pos)
    return edit.cursorRect(cursor)


def char_at(edit, pos: int):
    """The format of the character at `pos`."""
    cursor = QTextCursor(edit.document())
    cursor.setPosition(pos + 1)
    return cursor.charFormat()


def area(win, start: int, end: int) -> QRect:
    """Where the Log shows its text from the position `start` to `end` (whole lines, a table's cells too), in a
    screenshot of the window: to outline it when that text is in the Log more than once."""
    edit = win.log.text
    doc = edit.document()
    rect, block = QRectF(), doc.findBlock(start)
    while block.isValid() and block.position() <= end:
        top = doc.documentLayout().blockBoundingRect(block).topLeft()
        for i in range(block.layout().lineCount()):
            line = block.layout().lineAt(i)
            first = block.position() + line.textStart()
            if first <= end and first + line.textLength() >= start:
                rect |= line.naturalTextRect().translated(top)
        block = block.next()
    scrolled = QPoint(edit.horizontalScrollBar().value(), edit.verticalScrollBar().value())
    return rect.toAlignedRect().adjusted(-3, 0, 3, 0).translated(edit.viewport().mapTo(win, QPoint(0, 0)) - scrolled)


def picture_area(win, pos: int, fmt) -> QRect:
    """Where the Log shows the picture at `pos`, in a screenshot of the window."""
    edit = win.log.text
    width, height = size_of(fmt)
    at = rect_at(edit, pos)
    return QRect(at.x(), at.bottom() + 1 - height, width, height).translated(edit.viewport().mapTo(win, QPoint(0, 0)))


def hugged(rect: QRect) -> QRect:
    """`rect`, less the room an outline leaves around it: outlined, it is hugged, as a line of text is, so the outline
    does not cover the lines next to it."""
    return rect.adjusted(5, 5, -5, -5)


def cut(rect: QRect, view: QRect) -> QRect:
    """`rect` (a long line, or a line and the picture under it) hugged, and cut where the close-up `view` ends: the
    line goes on past it, and the outline's badge is then in the close-up."""
    return hugged(rect & view)


def scroll_to(win, pos: int):
    """Scroll the Log so the line at `pos` is near its top, the line before it in sight too."""
    QTest.qWait(100)  # (the Log laid out as it is shown)
    bar = win.log.text.verticalScrollBar()
    bar.setValue(bar.value() + rect_at(win.log.text, pos).top() - 22)
    QTest.qWait(100)


def log_part(win, top: int, bottom: int) -> QRect:
    """The left part of the Log (a close-up's width) from `top` to `bottom`, in a screenshot of the window."""
    left = win.log.text.viewport().mapTo(win, QPoint(0, 0)).x()
    return QRect(left, top, 760, bottom - top)


def painted(win, pos: int, fmt) -> bool:
    """The picture at `pos` is painted: much of it is not the Log's background."""
    edit = win.log.text
    width, height = size_of(fmt)
    image = edit.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    at = rect_at(edit, pos)
    part = QRect(at.x(), at.bottom() + 1 - height, width, height).intersected(edit.viewport().rect())
    base = edit.palette().color(QPalette.Base).rgb()
    colors = [image.pixelColor(int(x * ratio), int(y * ratio)).rgb()
              for x in range(part.left(), part.right(), 4) for y in range(part.top(), part.bottom(), 4)]
    return len(colors) > 100 and sum(c != base for c in colors) > 0.05 * len(colors)


def unreadable(win) -> list:
    """The Log's text whose contrast with what is behind it is under 3:1 (none: all of it is readable)."""
    edit, found = win.log.text, []
    block = edit.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            fmt = fragment.charFormat()
            if fragment.text().strip() and not fmt.isImageFormat():
                color = (fmt.foreground().color() if fmt.hasProperty(QTextFormat.ForegroundBrush)
                         else edit.palette().color(QPalette.Text))
                behind = (fmt.background().color() if fmt.hasProperty(QTextFormat.BackgroundBrush)
                          else edit.palette().color(QPalette.Base))
                if contrast(color, behind) < 3:
                    found.append((fragment.text(), color.name(), behind.name()))
            it += 1
        block = block.next()
    return found


# --- where a table's columns are ----------------------------------------------------------------------------------

def separator_columns(win, rows: list[str]) -> list[QRect]:
    """Where the Log shows each column separator of the table `rows`, from its first line to its last, in a
    screenshot of the window (at the x of its first line)."""
    edit = win.log.text
    start = plain(win).find("\n".join(rows))
    top, bottom = rect_at(edit, start).top(), rect_at(edit, start + len("\n".join(rows))).bottom()
    to_window = edit.viewport().mapTo(win, QPoint(0, 0))
    return [QRect(rect_at(edit, start + i).x(), top, rect_at(edit, start + i + 1).x() - rect_at(edit, start + i).x(),
                  bottom - top).translated(to_window) for i, ch in enumerate(rows[0]) if ch in SEPARATORS]


def log_layouts(edit, rows: list[str]) -> list[tuple]:
    """The x (in pixels) of each column separator on each of `rows` in the Log: one tuple per distinct layout."""
    start, layouts = edit.document().toPlainText().find("\n".join(rows)), set()
    for row in rows:
        layouts.add(tuple(rect_at(edit, start + i).x() for i, ch in enumerate(row) if ch in SEPARATORS))
        start += len(row) + 1
    return sorted(layouts)


def tooltip_layouts(tip, rows: list[str]) -> list[tuple]:
    """The same for the tooltip `tip`: its text laid out in its font, as the tooltip lays it out."""
    doc = QTextDocument()
    doc.setDefaultFont(tip.font())
    doc.setHtml(tip.text())
    doc.size()  # (laid out)
    text, layouts = doc.toPlainText(), set()
    for row in rows:
        start = text.find(row)
        assert start >= 0, (row, text)
        block = doc.findBlock(start)
        layout, offset = block.layout(), start - block.position()
        layouts.add(tuple(round(layout.lineForTextPosition(offset + i).cursorToX(offset + i)[0])
                          for i, ch in enumerate(row) if ch in SEPARATORS))
    return sorted(layouts)


def measure_at_scales(folder: Path, table: str) -> list[dict]:
    """The table in a Log of its own, measured in a program of its own at each of Windows' display scales: on Windows
    on Qt's real Windows platform (as you see the editor), elsewhere offscreen."""
    folder.mkdir()
    (folder / "table.txt").write_text(table, encoding="utf-8")
    (folder / "measure.py").write_text(MEASURE, encoding="utf-8")
    platform = "windows" if sys.platform == "win32" else "offscreen"
    environment = {name: value for name, value in os.environ.items() if name != "QT_QPA_FONTDIR"}  # (the system's)
    found = []
    for scale in SCALES:
        picture = folder / f"log-{scale}.png"
        done = subprocess.run([sys.executable, str(folder / "measure.py"), str(folder / "table.txt"), str(picture)],
                              cwd=folder,
                              env={**environment, "QT_QPA_PLATFORM": platform, "QT_SCALE_FACTOR": scale},
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said, why = done.stdout.splitlines(), " ".join((done.stderr or done.stdout).split())[-400:]
        if "started" not in said:  # Qt's platform did not start
            found.append({"scale": scale, "error": why})
        elif done.returncode != 0:  # it started, then failed: not measured, and that fails the check
            found.append({"scale": scale, "found": False, "failed": why})
        else:
            found.append({"scale": scale, "picture": picture, **json.loads(said[-1])})
    return found


def said_at(m: dict) -> str:
    """What the measurement `m` at a display scale found, in a line."""
    if "error" in m:
        return f"could not start: {m['error']}"
    if "failed" in m:
        return f"started, then failed: {m['failed']}"
    if not m["found"]:
        return "the table is not in that Log"
    return (f"{m['font']}{'' if m['fonts'] else ' (Qt found no fonts)'}, missing characters: "
            f"{' '.join(m['missing']) or 'none'}; separators at x = {xs(m['layouts'])}")


def side_by_side(pictures: list[tuple[str, Path]]) -> QWidget:
    """A window that shows `pictures` (pictures another program took of itself) in a row, each under its name."""
    row = QWidget()
    layout = QHBoxLayout(row)
    for name, path in pictures:
        column = QVBoxLayout()
        column.addWidget(QLabel(name))
        picture = QLabel()
        picture.setPixmap(QPixmap(str(path)))
        column.addWidget(picture)
        column.addStretch()
        layout.addLayout(column)
    row.show()
    QTest.qWait(50)
    return row


def xs(layouts: list[tuple]) -> str:
    """Where the separators are: one list of x per layout."""
    return " / ".join(", ".join(map(str, layout)) for layout in layouts)


def widths(layouts: list[tuple]) -> str:
    """How wide the columns are between the separators, in pixels: one list per layout."""
    return " / ".join(", ".join(str(b - a) for a, b in zip(layout, layout[1:])) for layout in layouts)


# --- the run history ----------------------------------------------------------------------------------------------

def old_history(path: Path, flow: Path):
    """A run history file as Taskloom 0.24 left it (its log lines have no column for how the Log shows them), with
    one run of `flow` from then."""
    db = sqlite3.connect(path)
    try:
        db.executescript(OLD_SCHEMA)
        db.execute("UPDATE runs SET flow='sales-check', flow_path=?, started_by='start_by_hand', "
                   "counts_for='start_by_hand'", (str(flow),))
        db.execute("UPDATE logs SET block='look_at_sales', message=?", (OLD_LINE,))
        db.commit()
    finally:
        db.close()


def log_columns(path: Path) -> set:
    db = sqlite3.connect(path)
    try:
        return {row[1] for row in db.execute("PRAGMA table_info(logs)")}
    finally:
        db.close()


def stored(path: Path, run_id: int) -> list[tuple]:
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT message, show FROM logs WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
    finally:
        db.close()


# --- doing it, as you would ---------------------------------------------------------------------------------------

def double_click_run(win, run_id: int):
    """In Run history, double-click run #`run_id`: the editor shows that run."""
    click_tab(win, win.history_panel)
    table = win.history_panel.table
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == str(run_id))
    at = table.visualItemRect(table.item(row, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.qWait(100)


def click_tab(win, widget):
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.bottom_tabs.indexOf(widget)).center())
    QTest.qWait(50)


def show_all(win):
    QTest.mouseClick(win.log.clear_filter, Qt.LeftButton)
    QTest.qWait(50)


def test_inspect_shows_tables_pictures_and_markdown_in_the_log(jdbc_env, proof, win, qtbot, monkeypatch, tmp_path):
    p = proof("Inspect shows tables, pictures and Markdown in the Log", ASKED)
    p.idea("Inspect is now a good place to read what passes through a flow. A table lines up in the Log exactly as "
           "in its hover tooltip, a value that is a picture's path shows the picture, and Inspect's message can be "
           "drawn as Markdown.")
    p.idea("The Log under the canvas was reworked into a rich text view. Each line of a run may now say how it is "
           "shown: as a picture (its message is the picture's path) or as Markdown. That mark is kept with the line "
           "in the run history, so a run you reopen from Run history, also one the scheduler ran, looks as it did "
           "live.")
    p.idea("A table shows as the same text table the hover tooltip shows, in the Log's fixed-width font, every space "
           "kept. This proof measures it on Windows, where you saw it break: where each column separator of each row "
           "is, in pixels.")
    p.idea("A picture: when Inspect's value is the path of a .png, .jpg, .jpeg, .gif, .bmp, .svg or .webp file that "
           "is there (a Chart's picture, for one), Inspect logs the picture's full path (after its message, if it has "
           "one), and the Log shows the picture under that line, scaled down to fit in 640 × 640 pixels, never up. "
           "It is read from its file when the Log draws it. If the file is gone, or is not a picture Qt can read, a "
           "grey line says so instead.")
    p.idea("Markdown: Inspect has a new setting, Show as Markdown, unticked at first. Ticked, its message (or, with "
           "no message, a text value) is drawn as GitHub Markdown: headings, bold and italic, lists, tables, quotes, "
           "and code in the fixed-width font with its spaces kept. HTML shows as written, a link opens nothing, and "
           "nothing is fetched from the internet. Unticked, the text shows exactly as it is, every space and line "
           "kept, as before.")
    p.idea("A Mermaid diagram (a ```mermaid block) in that Markdown is drawn as a picture where it is, offline, with "
           "mmdr, as you chose. A later change added that (Inspect draws Mermaid diagrams in the Log): this proof "
           "shows one drawn, briefly, and that change's own proof shows the details.")
    p.why_simplest("One small mark on a log line, drawn when the Log shows the line, does it all. The line itself "
                   "stays plain text (for a picture its path, for Markdown its text), so the terminal, the run "
                   "history and the headless Linux runner keep and print it as they print any line: only the "
                   "editor's Log draws it.")
    p.why_simplest("Everything this change draws with comes with Qt, which Taskloom already carries: its Markdown "
                   "reader, its rich text view and its picture readers. Nothing was added to the download for it; only "
                   "the later Mermaid change added mmdr, to draw the diagrams.")
    p.why_simplest("The table needed no change of its own: it is the very text the hover tooltip shows, in the same "
                   "fixed-width font. So no character of it was swapped for another; instead this proof measures, "
                   "on Windows, that the Log's font has every character of the table and that every column lines "
                   "up.")
    p.alternative("Showing every message as Markdown",
                  "Markdown joins lines that are next to each other into one paragraph and runs spaces together: "
                  "Python errors and text tables would lose their layout, the very thing you complained of. So "
                  "Markdown is a setting you tick, and everything else shows exactly as written.")
    p.alternative("Turning tables into grids (HTML tables) in the Log",
                  "They would look different from the hover tooltip you held up as the standard, and copying them "
                  "would no longer give the text table. The aligned text table is what you asked for.")
    p.alternative("A new run history file for the new mark",
                  "You would lose your past runs. The run history file gets the new column when it is opened, as it "
                  "got the column for each run's process before, so your past runs stay and still open.")
    p.alternative("Drawing Mermaid diagrams with a browser inside Taskloom, or with a web service",
                  "A browser engine (Qt WebEngine) adds about 130 MB to each download; a web service would send your "
                  "diagrams, and the data in them, out of your computer. The later change uses mmdr, a small program "
                  "(about 13 MB) that draws them offline, with no browser, as you chose.")
    p.fits("The Log works as before: selecting a block shows only its lines (its pictures and Markdown too), Show "
           "all shows everything, new lines keep the Log at the bottom unless you scrolled up, and copying gives "
           "plain text: a picture's line copies as its path, and the picture adds nothing.")
    p.fits("taskloom run in a terminal prints the same lines: the picture's path, the Markdown's text. A Chart's "
           "picture through Inspect is also part of the packaging check every release must pass, on Windows and on "
           "the Linux runner, and the Windows app is checked to carry Qt's reader for each kind of picture.")
    p.fits("Pictures stay cheap: only the file's header is read when its line is shown, the picture itself when it "
           "is drawn, and no file is held open, so Keep runs can still delete an old run's folder on Windows. One "
           "limit, said plainly: a file damaged after a good header shows Qt's small broken-picture mark, not a grey "
           "line.")
    flow = tmp_path / "sales-check.yaml"
    flow.write_text(yaml.safe_dump(FLOW, sort_keys=False), encoding="utf-8")
    flow = flow.resolve()
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Xmx256m")  # (the runs the editor starts inherit it)
    history = win.home.history_db
    edit = win.log.text

    p.step("Start with the run history of an earlier version: your Taskloom folder has the run history file of "
           "version 0.24, with one run of the flow sales-check.yaml from then.")
    assert not history.exists(), "the proof's Taskloom folder is new"
    old_history(history, flow)
    p.note("The proof writes that file as version 0.24 left it: its log lines have no column for how the Log shows "
           "them.")
    p.check("The run history file is from before this change: its log lines have no column for how they are shown",
            "show" not in log_columns(history))

    p.step("Open the flow sales-check.yaml (File > Open…). Its Run Query block sales reads 15 rows from a database, "
           "through the database's Java (JDBC) driver: a text, a whole-number, a decimal and an empty column.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow)
    p.shot(win, "① look_at_sales inspects sales' table; chart draws that table, and ② look_at_chart and ③ summary "
                "inspect its picture. All three are Inspect blocks.",
           highlight=INSPECTS, badges=True, zoom=["sales", *INSPECTS])

    # --- a table, as in its tooltip -----------------------------------------------------------------------------
    p.step("Press ▶ Run now, drag the top edge of the Log up to make it taller, and click look_at_sales. The Log "
           "then shows only look_at_sales' lines.")
    first = run_now(win, tab, qtbot)
    p.check("The run ended with success", first.status == "success")
    drag_up(win, win.output, LOG_HEIGHT - win.output.height())
    qtbot.waitUntil(lambda: win.output.height() >= LOG_HEIGHT - 30, timeout=5_000)
    click(tab, "look_at_sales")
    [table] = [m for m, _ in run_entries(win, "look_at_sales", first.run_id) if m.startswith("15 rows × 4 columns\n")]
    rows = table.split("\n")
    info = next(line for line in lines(win) if line.endswith(f"[look_at_sales] INFO {rows[0]}"))
    scroll_to(win, find(win, info))
    shown = [(edit, info), (edit, rows[-1])]
    p.shot(win, "① In the Log, under the canvas: look_at_sales' line, “15 rows × 4 columns”, then the table, its "
                "columns lined up.", highlight=[shown], badges=True, zoom=[shown])
    at = lines(win).index(info)
    p.check("The Log shows “15 rows × 4 columns”, then each row of the table as it was made, every space kept",
            rows[0] == "15 rows × 4 columns" and lines(win)[at + 1:at + len(rows)] == [r.rstrip() for r in rows[1:]])
    measured = table_measurements(edit, table)
    p.check("Each row of the table starts at the Log's left edge, as the line above it does",
            measured["lefts"] == [rect_at(edit, find(win, info)).x()])

    p.step("Rest the mouse on sales' result output, on its right side.")
    tip = hover(tab, tab.scene.nodes["sales"].ports[("result", True)], qtbot)
    tip_text = QTextDocument()
    tip_text.setHtml(tip.text())
    in_tip = tooltip_layouts(tip, rows[1:])
    p.text("Where the tooltip's column separators are on each line of the table, x in pixels (one line here: the same "
           "on every line)", xs(in_tip))
    p.shot(tip, "① The tooltip of sales' result shows the same table as the Log, with the same rows and columns, "
                "lined up.", over=win, highlight=[tip], badges=True, zoom=[tip])
    tip.hide()
    p.check("The tooltip shows the very same table as the Log, character for character",
            "\n".join(rows) in tip_text.toPlainText())
    p.check("In the tooltip, each column separator is at one x on every line", len(in_tip) == 1)

    p.step("Measure the table in the Log: the font it is drawn in, whether that font has every character of the "
           "table, and where each column separator is on each line.")
    in_log = log_layouts(edit, rows[1:])
    fixed = QFontInfo(edit.document().defaultFont()).fixedPitch()
    p.text("The table in the Log, measured", "\n".join([
        f"Its font: {measured['font']} ({'a' if fixed else 'not a'} fixed-width font)",
        f"Characters of the table that font does not have: {' '.join(measured['missing']) or 'none'}",
        f"Where the column separators are on each of its {len(rows) - 1} lines, x in pixels (one line here: the same "
        f"on every line): {xs(in_log)}",
        f"The columns' widths, in pixels: {widths(in_log)}; in the tooltip: {widths(in_tip)}"]))
    p.shot(win, "Each column separator of the table in the Log, outlined from the table's first line to its last: "
                "every outline is straight, as each separator is at the same place on every line.",
           highlight=separator_columns(win, rows[1:]), zoom=[shown])
    p.check(f"The Log's font, {measured['font']}, has every character of the table: no other font stands in for one",
            measured["missing"] == [])
    p.check("It is a fixed-width font", fixed and fixed_pitch_at(edit, find(win, rows[3]) + 2))
    p.check("Each column separator is at one x on every line: the columns line up",
            len(in_log) == 1 and len(in_log[0]) >= 5)

    where = ("on Qt's own Windows platform, as you see the editor" if sys.platform == "win32" else
             "offscreen (this run was not on Windows)")
    p.step(f"Show the same table in a Log of its own at 100%, 125% and 150%, Windows' display scales, {where}, and "
           "measure it the same way.")
    at_scales = measure_at_scales(tmp_path / "scales", table)
    p.text("The table in a Log of its own, at each display scale (x counted in pixels at 100%)",
           "\n".join(f"{float(m['scale']):.0%}: {said_at(m)}" for m in at_scales))
    started = [m for m in at_scales if "error" not in m]
    if len(started) < len(at_scales):
        # Offscreen always starts: only Qt's Windows platform may not, on a build machine with no screen.
        assert sys.platform == "win32", at_scales
        p.note("Qt's Windows platform could not start at some scales on this machine (what it said is above): those "
               "scales are not measured here.")
    shown_at = [(f"{float(m['scale']):.0%}", m["picture"]) for m in started if m.get("found") and m["picture"].is_file()]
    if shown_at:
        pictures = side_by_side(shown_at)
        p.shot(pictures, "The table in that Log, as it drew itself at each display scale: its columns line up at "
                         "each one.")
        pictures.close()
    if started:
        p.check("At each display scale measured, the font has every character and each column separator is at one x "
                "on every line", all(m["found"] and m["fonts"] and m["missing"] == [] and len(m["layouts"]) == 1
                                     for m in started))
    p.note("So nothing in the table's text had to change. The Log has drawn its text in a fixed-width font since "
           "version 0.21.0, and this proof found no character missing from it and no column out of line. If a table "
           "still does not line up for you, please send a screenshot and your version (taskloom --version prints "
           "it).")

    # --- a picture ----------------------------------------------------------------------------------------------
    p.step("Click look_at_chart.")
    click(tab, "look_at_chart")
    [(path, show)] = run_entries(win, "look_at_chart", first.run_id)
    path_line = next(line for line in lines(win) if line.endswith(f"[look_at_chart] INFO {path}"))
    scroll_to(win, find(win, path_line))
    found = images(edit)
    assert found, plain(win)
    line_at = area(win, find(win, path_line), find(win, path_line))
    view = log_part(win, line_at.top(), picture_area(win, *found[0]).bottom() + 4)
    p.shot(win, "① look_at_chart's line ends with the full path of chart's picture; ② the picture is under it, "
                "starting under [look_at_chart].",
           highlight=[cut(line_at, view), hugged(picture_area(win, *found[0]))], badges=True, zoom=view)
    p.check("① look_at_chart's line is its time, [look_at_chart], INFO and the full path of chart's picture",
            re.fullmatch(rf"\d\d:\d\d:\d\d \[look_at_chart\] INFO {re.escape(path)}", path_line) and show == "image"
            and Path(path).is_absolute() and Path(path).resolve() == Path(tab.block_info["chart"]["previews"]["image"])
            .resolve())
    p.check("② Under it is the picture, 640 × 360 pixels: the chart's 1280 × 720, scaled down to fit",
            [size_of(fmt) for _, fmt in found] == [(640, 360)] and found[0][0] > find(win, path_line))
    p.check("② It starts under [look_at_chart], where the block's name starts on the line above",
            abs(rect_at(edit, found[0][0]).x() - rect_at(edit, find(win, path_line) + path_line.index("[")).x()) <= 2)
    p.check("② It is drawn: the Log shows the chart, not an empty box", painted(win, *found[0]))

    p.step("Click Show all, above the Log: every block's lines, with the run's first and last lines.")
    show_all(win)
    everything = images(edit)
    scroll_to(win, find(win, path_line))
    p.shot(win, "With Show all, ① look_at_chart's picture is still there, among the lines of the other blocks.",
           highlight=[hugged(picture_area(win, *everything[0]))], badges=True)
    p.check("With Show all, the Log shows the chart's picture for look_at_chart and for summary, 640 × 360 each",
            [size_of(fmt) for _, fmt in everything] == [(640, 360), (640, 360)])
    p.check("And the run's first and last lines, and look_at_sales' table", f"run #{first.run_id} · " in plain(win)
            and f"DONE run #{first.run_id} success" in plain(win) and rows[3] in plain(win))

    p.step("Click summary. Show as Markdown is not ticked, so its message shows exactly as written, every space and "
           "line kept; then come the picture's path and the picture, as summary's value is the chart.")
    click(tab, "summary")
    message_line = next(line for line in lines(win) if line.endswith("[summary] INFO # Weekly sales check"))
    scroll_to(win, find(win, message_line))
    summary_path = (edit, f"[summary] INFO {path}")
    p.shot(win, "① summary's message as written: its # and ** are plain text, every line kept. ② Under it, the "
                "picture's path, with the picture further down.",
           highlight=[[(edit, message_line), (edit, "    sales --> chart")], summary_path],
           badges=True, zoom=[(edit, message_line), (edit, "| Look | look_at_sales")])
    p.check("summary logs its message, then the picture's path: two lines, in that order",
            run_entries(win, "summary", first.run_id) == [(MESSAGE, None), (path, "image")])
    p.check("The message shows each of its lines as written, spaces kept",
            all(line.rstrip() in lines(win) for line in MESSAGE.split("\n")[1:] if line.strip()))

    # --- Markdown -----------------------------------------------------------------------------------------------
    p.step("Tick Show as Markdown in summary's Properties, on the right.")
    label, box = setting(win, "Show as Markdown")
    click_tick(box)
    label, box = setting(win, "Show as Markdown")  # (Properties shows the block again, as it now is)
    p.shot(win, "① Show as Markdown, ticked, in summary's Properties.", highlight=[[label, box]], badges=True,
           zoom=[label, box])
    p.check("summary's Show as Markdown is ticked", box.isChecked()
            and tab.doc.blocks["summary"]["config"].get("markdown") is True)

    p.step("Press ▶ Run now again, and click summary. The Log shows its lines of both runs: scroll down to the "
           "second run's.")
    second = run_now(win, tab, qtbot, runs=2)
    p.check("The second run ended with success", second.status == "success")
    click(tab, "summary")
    run_2 = find(win, f"run #{second.run_id} · ")
    [(markdown, md_show), (path_2, path_2_show)] = run_entries(win, "summary", second.run_id)
    markdown_line = lines(win, run_2)[1]
    scroll_to(win, find(win, markdown_line, run_2))
    heading = block_of(edit, "Weekly sales check")
    cells = QTextCursor(block_of(edit, "Step")).currentTable()
    code = find(win, "keep   these   spaces", run_2)
    p.shot(win, "summary's message, drawn as Markdown under its line: ① a heading, bold, code and a list, ② a table "
                "with borders, ③ a code block in the fixed-width font, its spaces kept, and the HTML as written.",
           highlight=[hugged(area(win, heading.position(), find(win, "its part of the sales", run_2))),
                      hugged(area(win, cells.firstPosition(), cells.lastPosition())).adjusted(0, 0, 16, 0),
                      hugged(area(win, code, find(win, "PS-101    19", run_2))).adjusted(0, 0, 24, 0)],
           badges=True, zoom=area(win, find(win, markdown_line, run_2), find(win, "not bold: written", run_2)))
    p.check("summary logged its message marked as Markdown, then the picture's path marked as a picture",
            (markdown, md_show, path_2_show) == (MESSAGE, "markdown", "image") and Path(path_2).name == "chart.png")
    p.check("summary's line is its time, [summary] and INFO, with its message drawn under it",
            re.fullmatch(r"\d\d:\d\d:\d\d \[summary\] INFO", markdown_line)
            and lines(win, run_2)[2] == "Weekly sales check")
    p.check("① “Weekly sales check” is a heading, in bold",
            heading.blockFormat().headingLevel() == 1 and char_at(edit, heading.position()).fontWeight() >= 600)
    body = block_of(edit, BODY).position()
    p.check("① “up” is bold, and the two lines under it are a list",
            char_at(edit, body + BODY.index("up")).fontWeight() >= 600
            and all(block_of(edit, item).textList() is not None
                    for item in ("articles: what each system published", "share: its part of the sales")))
    p.check("② The table has 3 rows of 3 columns, with borders; its numbers are aligned right",
            cells is not None and (cells.rows(), cells.columns()) == (3, 3) and cells.format().border() > 0
            and [[cells.cellAt(r, c).firstCursorPosition().block().text() for c in range(3)] for r in range(3)] == CELLS
            and bool(cells.cellAt(1, 2).firstCursorPosition().block().blockFormat().alignment() & Qt.AlignRight))
    p.check("③ The code block keeps its spaces, in the fixed-width font; the text around it is not fixed-width",
            "keep   these   spaces" in lines(win, run_2) and fixed_pitch_at(edit, code)
            and not fixed_pitch_at(edit, body))
    p.check("The HTML <b>…</b> shows as written, not in bold", "<b>not bold: written as it is</b>" in lines(win, run_2))

    p.step("Scroll down the Log: the Mermaid block, drawn as its diagram, then the picture's path and the picture.")
    html_at = find(win, "not bold: written", run_2)
    scroll_to(win, html_at)
    path_2_line = next(line for line in lines(win, run_2) if line.endswith(f"[summary] INFO {path_2}"))
    path_2_at = find(win, path_2_line, run_2)
    [diagram] = [(pos, fmt) for pos, fmt in images(edit) if run_2 < pos < path_2_at]
    after = [(pos, fmt) for pos, fmt in images(edit) if pos > path_2_at]
    picture_2 = picture_area(win, *after[0])
    view = log_part(win, area(win, html_at, html_at).top(), picture_2.top() + 140)
    p.shot(win, "① The Mermaid block is drawn as its diagram, sales → chart, where it was in the Markdown; ② then "
                "the picture's path and the picture, as before.",
           highlight=[hugged(picture_area(win, *diagram)),
                      cut(area(win, path_2_at, path_2_at) | picture_2, view)],
           badges=True, zoom=view)
    p.note("This proof shows the diagram only briefly: the proof of the Mermaid change (Inspect draws Mermaid "
           "diagrams in the Log) shows in detail how diagrams are drawn.")
    later = plain(win)[run_2:]
    p.check("① The Mermaid block is drawn where it was, after the HTML line: a picture in place of its text, and no "
            "note that it could not be drawn",
            html_at < diagram[0] and "graph LR" not in later and "sales --> chart" not in later
            and MERMAID_NOTE not in later)
    p.check("① The diagram is painted: the Log shows it, not an empty box", painted(win, *diagram))
    p.check("② Then, after the Markdown, the picture's line and the picture, 640 × 360",
            [size_of(fmt) for _, fmt in after] == [(640, 360)] and after[0][0] > path_2_at)

    p.step("See the same Log in dark colours, as in dark mode.")
    p.note("The proof gives the editor the dark colours a dark mode gives it: the Log draws with the colours it is "
           "given.")
    original = QApplication.palette()
    dark = QPalette()
    for role, value in DARK.items():
        dark.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    try:
        QApplication.setPalette(dark)
        click(tab, "look_at_sales")  # (the Log is drawn again, in the new colours)
        click(tab, "summary")
        scroll_to(win, find(win, markdown_line, find(win, f"run #{second.run_id} · ")))
        p.shot(win.output, "In dark colours the Markdown's text is drawn light, as the rest of the Log's text is, and "
                           "the grey times stay readable; the Mermaid diagram is drawn for the dark background, and "
                           "the chart keeps its own white background.")
        p.check("In dark colours, every character in the Log stands out from what is behind it (a contrast of at "
                "least 3 to 1)", unreadable(win) == [])
    finally:
        QApplication.setPalette(original)
        QApplication.processEvents()

    # --- copying ------------------------------------------------------------------------------------------------
    p.step("Click look_at_chart; in the Log, select its picture's line and the picture with the mouse, and press "
           "Ctrl+C.")
    click(tab, "look_at_chart")
    win.activateWindow()
    scroll_to(win, find(win, path_line))
    [(pos, fmt), *_] = images(edit)
    begin, picture = rect_at(edit, find(win, path_line)), picture_area(win, pos, fmt)
    end = picture.translated(-edit.viewport().mapTo(win, QPoint(0, 0))).bottomRight() + QPoint(4, -4)
    QTest.mousePress(edit.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(begin.x() + 1, begin.center().y()))
    QTest.mouseMove(edit.viewport(), end)
    QTest.mouseRelease(edit.viewport(), Qt.LeftButton, Qt.NoModifier, end)
    QApplication.clipboard().setText("")
    QTest.keyClick(edit, Qt.Key_C, Qt.ControlModifier)
    copied = QApplication.clipboard().text()
    line_at = area(win, find(win, path_line), find(win, path_line))
    view = log_part(win, line_at.top(), picture.bottom() + 4)
    p.shot(win, "① The path's line and the picture under it, selected (in blue).",
           highlight=[cut(line_at | picture, view)], badges=True, zoom=view)
    p.text("What was copied", copied)
    p.check("The path's line copies as text, its spaces ordinary ones, and the picture adds nothing: no stray "
            "character, no empty line", copied.splitlines() == [path_line] and "\ufffc" not in copied
            and "\xa0" not in copied)
    edit.moveCursor(QTextCursor.End)  # (the selection goes)

    # --- the run history ----------------------------------------------------------------------------------------
    p.step("Click the Run history tab, under the canvas.")
    click_tab(win, win.history_panel)
    runs = history_rows(win)
    table_view = win.history_panel.table
    p.shot(win, "Run history lists ① the run from version 0.24 and ② the two new ones.",
           highlight=[(table_view, len(runs) - 1), [(table_view, 0), (table_view, 1)]], badges=True,
           zoom=[table_view.item(0, 0), table_view.item(len(runs) - 1, table_view.columnCount() - 1)])
    p.check("Run history lists the two new runs and the one from version 0.24, newest first",
            [r[0] for r in runs] == [str(second.run_id), str(first.run_id), "1"])

    p.step(f"Double-click run {second.run_id}, the Markdown run, and click summary: the run, reopened from the run "
           "history.")
    double_click_run(win, second.run_id)
    click(tab, "summary")
    p.check(f"The Log shows run {second.run_id} only", win.bottom_tabs.currentWidget() is win.log
            and f"run #{second.run_id} · " in plain(win) and f"run #{first.run_id} · " not in plain(win))
    p.check("summary's lines are as they were live: its message marked as Markdown, then the picture's path",
            run_entries(win, "summary", second.run_id) == [(markdown, "markdown"), (path_2, "image")])
    scroll_to(win, find(win, markdown_line))
    reopened = QTextCursor(block_of(edit, "Step")).currentTable()
    p.shot(win, f"Run {second.run_id}, reopened: summary's Markdown is drawn as it was live, ① its heading, ② its "
                "table, and the rest under them.",
           highlight=[(edit, "Weekly sales check"),
                      hugged(area(win, reopened.firstPosition(), reopened.lastPosition()))],
           badges=True, zoom=log_part(win, area(win, find(win, markdown_line), find(win, markdown_line)).top(),
                                      picture_area(win, *images(edit)[0]).bottom() + 4))
    p.check("They are drawn the same: the heading, the 3 by 3 table, the code block, the Mermaid diagram and the "
            "picture, 640 × 360",
            block_of(edit, "Weekly sales check").blockFormat().headingLevel() == 1 and reopened is not None
            and (reopened.rows(), reopened.columns()) == (3, 3) and "keep   these   spaces" in lines(win)
            and [size_of(fmt) for _, fmt in images(edit)] == [size_of(diagram[1]), (640, 360)]
            and "graph LR" not in plain(win))

    p.step("Double-click run 1, the run from version 0.24, and click look_at_sales.")
    double_click_run(win, 1)
    click(tab, "look_at_sales")
    p.shot(win, "① The old run's line shows as it always did.", highlight=[(edit, OLD_LINE)], badges=True,
           zoom=[(edit, "run #1 · "), (edit, OLD_LINE)])
    p.check("The old run opens, and its line shows as plain text", "run #1 · " in plain(win)
            and any(line.endswith(f"[look_at_sales] INFO {OLD_LINE}") for line in lines(win)) and images(edit) == [])
    p.check("The run history file now has the new column: the old line has nothing in it, the new picture lines say "
            "“image”", "show" in log_columns(history) and stored(history, 1) == [(OLD_LINE, None)]
            and (path_2, "image") in stored(history, second.run_id))

    # --- a picture that is gone ---------------------------------------------------------------------------------
    p.step(f"Reopen run {second.run_id} and click summary; delete the chart's picture of that run (as Keep runs does "
           "when it removes an old run's folder), then click Show all.")
    double_click_run(win, second.run_id)
    click(tab, "summary")
    edit.viewport().grab()  # (its picture painted, read from its file)
    try:
        os.remove(path_2)
        removed = True
    except OSError as e:
        removed = f"{type(e).__name__}: {e}"
    show_all(win)
    first_path = find(win, f"INFO {path_2}")
    scroll_to(win, first_path)
    notes = [find(win, GONE), find(win, GONE, find(win, GONE) + 1)]
    p.shot(win, "① Under each line with the picture's path, where the picture was, a grey line: “(the picture is no "
                "longer there)”.", highlight=[hugged(area(win, at, at + len(GONE))) for at in notes],
           badges=[1, 1],
           zoom=log_part(win, area(win, first_path, first_path).top(),
                         area(win, notes[1], notes[1]).bottom() + 4))
    p.check("The picture's file could be deleted although the Log had just drawn it: the Log keeps no file open",
            removed is True)
    p.check("With Show all, both of the run's lines with that path are followed by “(the picture is no longer "
            "there)”, and the chart is drawn nowhere: the only picture left is summary's Mermaid diagram",
            [size_of(fmt) for _, fmt in images(edit)] == [size_of(diagram[1])] and lines(win).count(GONE) == 2
            and all(lines(win)[i + 1] == GONE for i, line in enumerate(lines(win)) if line.endswith(path_2)))

    # --- how long it takes --------------------------------------------------------------------------------------
    p.step("Time the Log: give it 20,000 lines at once, as reopening a run with that many lines does, then click "
           "look_at_sales and Show all.")
    p.note("The proof hands the Log the 20,000 lines itself, as reopening such a run does: a run logging 20,000 lines "
           "would only make this proof slower.")
    many = [{"ts": f"2026-10-29T09:{i // 60 % 60:02}:{i % 60:02}.000", "block": "look_at_chart", "level": "INFO",
             "message": f"row {i}: value {i * 7}"} for i in range(20_000)]
    began = time.perf_counter()
    win.log.set_entries(many)
    QApplication.processEvents()
    drawn_in = time.perf_counter() - began
    began = time.perf_counter()
    click(tab, "look_at_sales")
    filtered_in = time.perf_counter() - began
    began = time.perf_counter()
    show_all(win)
    all_in = time.perf_counter() - began
    p.text("Measured on this machine", f"20,000 lines drawn in {drawn_in:.2f} s; clicking look_at_sales (whose lines "
                                       f"they are not) {filtered_in:.2f} s; Show all (all 20,000 again) {all_in:.2f} s")
    p.check("Each takes under 30 seconds: a wide bound, to catch only work that grows with the square of the lines",
            drawn_in < 30 and filtered_in < 30 and all_in < 30 and "row 19999: value 139993" in plain(win))

    # --- the terminal -------------------------------------------------------------------------------------------
    p.step("In a terminal, run the flow: taskloom run sales-check.yaml.")
    p.note("The proof reads what it prints in UTF-8, as a terminal shows it (Windows Terminal, for one).")
    done = subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow)], cwd=jdbc_env.root,
                          env={**jdbc_env.env, "PYTHONIOENCODING": "utf-8"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    printed, inspecting = [], False
    for line in done.stderr.splitlines():  # the Inspect blocks' lines, each with the lines of its message
        said = re.match(r"\d\d:\d\d:\d\d \[(\w+)\] ", line)
        if said:
            inspecting = said.group(1) in INSPECTS and " INFO: " in line
        if inspecting and not line.startswith("Picked up JAVA_TOOL_OPTIONS"):  # (Java's own message)
            printed.append(line)
    p.text("What it printed for the three Inspect blocks (its other lines left out)", "\n".join(printed))
    p.check("The run succeeded", done.returncode == 0)
    pictures = [line.split("[look_at_chart] INFO: ", 1)[1] for line in printed if "[look_at_chart] INFO: " in line]
    p.check("look_at_chart's line ends with the full path of the run's chart picture",
            len(pictures) == 1 and Path(pictures[0]).is_absolute() and Path(pictures[0]).is_file()
            and Path(pictures[0]).name == "chart.png")
    p.check("summary's lines are its Markdown's text, as written", any(
        re.fullmatch(r"\d\d:\d\d:\d\d \[summary\] INFO: # Weekly sales check", line) for line in printed)
        and all(line in printed for line in ("| Step | Blocks | How many |", "```mermaid", "keep   these   spaces")))
