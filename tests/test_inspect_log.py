"""Inspect is a good place to read data in the Log (specification inspect_log, phase 1): text tables line up, a value
that is a picture's path shows the picture, the message or a text value can be shown as Markdown, a log line carries
an optional stored `show` marker so runs reopened from history look the same, and a Mermaid fence that cannot be
drawn shows as text with a note (phase 2 draws the others: test_inspect_mermaid.py). Each test names the point of the
specification it protects."""

import html
import http.server
import inspect
import json
import os
import re
import sqlite3
import struct
import subprocess
import sys
import textwrap
import threading
import time
import zlib
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QUrl, Slot  # noqa: E402
from PySide6.QtGui import (QColor, QDesktopServices, QFontDatabase, QFontInfo, QImage, QImageReader,  # noqa: E402
                           QPalette, QTextBlockFormat, QTextCursor, QTextFormat)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel  # noqa: E402

from conftest import events  # noqa: E402
from taskloom.engine import preview  # noqa: E402
from taskloom.history import History  # noqa: E402

NO_SHOW = "(History.logs gives no show)"
GONE = "(the picture is no longer there)"
MERMAID_NOTE = "(could not draw this diagram: "  # how the note starts (phase 2: only a diagram that cannot be drawn)
UNDRAWABLE = "graph TD\n    start[Load --> report\n"  # a Mermaid diagram mmdr cannot draw (an unclosed label)
HELP = ("Draw the message (or, with no message, a text value) as Markdown in the Log: # headings, **bold**, *italic*, "
        "lists, | tables |, `code` and ``` code blocks (spaces kept). Mermaid diagrams (```mermaid code blocks) are "
        "drawn as pictures. As in Markdown, lines next to each other join into one paragraph: leave a blank line "
        "between paragraphs. Off: the text is shown exactly as it is, every space and line kept.")
DESCRIPTION = ("For checking your flow: put it between two blocks to see the data passing through (in the Log and in "
               "Data preview). Tables line up as in the hover tooltip, a path to a picture (.png, .jpg, .svg…) shows "
               "the picture, and the message can be shown as Markdown, with its Mermaid diagrams drawn. It passes the "
               "data on unchanged.")
SALES_CSV = "region,total\nNorth,120\nSouth,80\nEast,95\nWest,60\n"


# --- flows and the run history ------------------------------------------------------------------------------------

def write_flow(path: Path, blocks: dict, edges=()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": list(edges)}, sort_keys=False), encoding="utf-8")
    return path


def python(code: str, outputs=("output",), inputs=()) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code}}


def look(**config) -> dict:
    return {"type": "logic.inspect", **({"config": config} if config else {})}


def secret(value: str) -> dict:
    """An Ask User block for a password: in a run nobody can answer, its default, as a secret."""
    return {"type": "logic.ask_user", "config": {"prompt": "Key?", "kind": "password", "default": value}}


def chart_flow(folder: Path, **look_config) -> Path:
    """The user's case: Read file → Chart → Inspect (its value passed on to a block that writes it to passed.txt)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sales.csv").write_text(SALES_CSV, encoding="utf-8")
    return write_flow(folder / "report.yaml", {
        "manual": {"type": "trigger.manual"},
        "read": {"type": "data.read_file", "config": {"path": "sales.csv"}},
        "chart": {"type": "reports.chart", "config": {"x": "region", "y": ["total"]}},
        "look": look(**look_config),
        "after": python("open('passed.txt', 'w', encoding='utf-8').write(repr(v))", outputs=(), inputs=("v",)),
    }, ["manual.start -> read.after", "read.table -> chart.table", "chart.image -> look.value", "look.value -> after.v"])


def logged(env, run_id: int, block: str) -> list[tuple]:
    """(message, show) of each line `block` logged in run `run_id`, as the run history gives them back."""
    history = History(env.home / "history.db")
    try:
        return [(e["message"], e.get("show", NO_SHOW)) for e in history.logs(run_id) if e["block"] == block]
    finally:
        history.close()


def run_cli(env, flow, *args):
    result = env.taskloom("run", flow, *args)
    assert result.returncode == 0, result.stderr
    run_id, status = env.last_run()
    assert status == "success", env.block_status(run_id)
    return result, run_id


# --- 6-10: what Inspect logs, through the real CLI and run history ---------------------------------------------

def test_a_charts_picture_path_is_logged_as_a_picture_line_and_printed_as_its_path(env):
    """Spec 6b, 7, 9, 10 (the user's case, Chart → Inspect): one line whose message is the picture's absolute path,
    stored with show="image"; the terminal prints it as "HH:MM:SS [look] INFO: <path>"; the value passes on unchanged."""
    result, run_id = run_cli(env, chart_flow(env.root))

    [(message, show)] = logged(env, run_id, "look")
    picture = Path(message)
    assert show == "image", (message, show)
    assert picture.is_absolute() and picture.is_file() and picture.name == "chart.png", message
    assert re.search(rf"^\d\d:\d\d:\d\d \[look\] INFO: {re.escape(message)}$", result.stderr, re.M), result.stderr
    [(previews,)] = env.query("SELECT previews FROM blocks WHERE run_id=? AND block='chart'", run_id)
    image = json.loads(previews)["image"]  # the chart's value
    assert Path(image).resolve() == picture.resolve()
    assert (env.root / "passed.txt").read_text(encoding="utf-8") == repr(image)  # passed on unchanged


def test_json_events_carry_show_only_on_the_picture_line(env):
    """Spec 8, 10: with --json-events (how the editor runs a flow) the picture's log event has "show": "image"; every
    other event has no "show" key, as today."""
    flow = chart_flow(env.root)
    spec = yaml.safe_load(flow.read_text(encoding="utf-8"))
    spec["blocks"]["hello"] = python("print('hello from the run')", outputs=())
    spec["edges"].append("manual.start -> hello.after")
    flow.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    result, _ = run_cli(env, flow, "--json-events")

    found = events(result.stdout)
    [picture] = [e for e in found if e["event"] == "log" and e.get("block") == "look"]
    assert picture.get("show") == "image" and Path(picture["message"]).is_file(), picture
    others = [e for e in found if e is not picture]
    assert any(e["event"] == "log" and e["message"] == "hello from the run" for e in others), others
    assert [e for e in others if "show" in e] == []


PICTURE_NAMES = ["c.png", "d.JPG", "e.jpeg", "f.Gif", "g.bmp", "h.SVG", "i.webp"]


def test_a_relative_or_path_object_picture_path_is_logged_absolute_under_the_flow_folder(env):
    """Spec 6b and its edge paths: a str or a Path, with any of the picture suffixes in any case, that is a file when
    Inspect runs is a picture; a relative path is resolved against the flow's folder (not where the run was started
    from) and logged as an absolute path."""
    flow_dir = env.root / "flows"
    for name in PICTURE_NAMES:  # the run does not read the pictures: any bytes will do
        (flow_dir / "out").mkdir(parents=True, exist_ok=True)
        (flow_dir / "out" / name).write_bytes(b"not read by the run")
    code = "from pathlib import Path\n" + "".join(
        f"p{i} = Path('out', {name!r})\n" if i % 2 else f"p{i} = 'out/{name}'\n" for i, name in enumerate(PICTURE_NAMES))
    blocks = {"make": python(code, outputs=[f"p{i}" for i in range(len(PICTURE_NAMES))]),
              **{f"look{i}": look() for i in range(len(PICTURE_NAMES))}}
    write_flow(flow_dir / "pictures.yaml", blocks, [f"make.p{i} -> look{i}.value" for i in range(len(PICTURE_NAMES))])

    _, run_id = run_cli(env, Path("flows") / "pictures.yaml")  # started from the folder above the flow's

    for i, name in enumerate(PICTURE_NAMES):
        [(message, show)] = logged(env, run_id, f"look{i}")
        assert show == "image", (name, message, show)
        assert Path(message).is_absolute() and Path(message).resolve() == (flow_dir / "out" / name).resolve(), message


def test_values_that_are_not_pictures_are_shown_as_text_as_today(env):
    """Spec edge paths: a missing picture, a text file, a picture's name on two lines, a 5,000-character text ending
    .png (is_file may raise OSError: Python 3.12 does), a secret ending .png and several pictures from several connections are logged
    as today (preview of the value, no show) and the block succeeds."""
    flow_dir = env.root
    for name in ("notes.txt", "one.png", "two.png", "hidden.png"):
        (flow_dir / name).write_bytes(b"x")
    try:
        (flow_dir / "a\nb.png").write_bytes(b"x")  # so the name on two lines is a file (not possible on Windows)
    except OSError:
        pass
    long_text = "x" * 5000 + ".png"
    code = ("missing = 'missing.png'\nnotes = 'notes.txt'\nmultiline = 'a\\nb.png'\n"
            "long = 'x' * 5000 + '.png'\none = 'one.png'\ntwo = 'two.png'\n")
    flow = write_flow(flow_dir / "values.yaml", {
        "make": python(code, outputs=["missing", "notes", "multiline", "long", "one", "two"]),
        "key": secret("hidden.png"),
        **{f"look_{n}": look() for n in ("missing", "notes", "multiline", "long", "several", "secret")},
    }, ["make.missing -> look_missing.value", "make.notes -> look_notes.value",
        "make.multiline -> look_multiline.value", "make.long -> look_long.value", "make.one -> look_several.value",
        "make.two -> look_several.value", "key.answer -> look_secret.value"])

    _, run_id = run_cli(env, flow)

    assert logged(env, run_id, "look_missing") == [("missing.png", None)]
    assert logged(env, run_id, "look_notes") == [("notes.txt", None)]
    assert logged(env, run_id, "look_multiline") == [("a\nb.png", None)]
    assert logged(env, run_id, "look_long") == [(preview(long_text), None)]
    assert logged(env, run_id, "look_secret") == [("***", None)]
    [(several, show)] = logged(env, run_id, "look_several")
    assert show is None and several in (preview(["one.png", "two.png"]), preview(["two.png", "one.png"])), several
    assert set(env.block_status(run_id).values()) == {"success"}


def test_a_message_line_comes_before_the_picture_line(env):
    """Spec 6a, 6b and edge paths: with a message, the message line (show="markdown" only when Show as Markdown is
    on) and then the picture's line; Show as Markdown with no message and a picture value: the picture rule."""
    (env.root / "pic.png").write_bytes(b"x")
    pic = str((env.root / "pic.png").resolve())
    flow = write_flow(env.root / "said.yaml", {
        "make": python("output = 'pic.png'"),
        "said": look(message="Here is the picture"),
        "said_md": look(message="## The picture", markdown=True),
        "md_only": look(markdown=True),
    }, ["make.output -> said.value", "make.output -> said_md.value", "make.output -> md_only.value"])

    _, run_id = run_cli(env, flow)

    def resolved(rows):
        return [(str(Path(m).resolve()) if s == "image" else m, s) for m, s in rows]

    assert resolved(logged(env, run_id, "said")) == [("Here is the picture", None), (pic, "image")]
    assert resolved(logged(env, run_id, "said_md")) == [("## The picture", "markdown"), (pic, "image")]
    assert resolved(logged(env, run_id, "md_only")) == [(pic, "image")]


def test_show_as_markdown_marks_the_message_or_a_text_value_only(env):
    """Spec 4, 6a, 6c, 10: Show as Markdown (off by default) marks the filled-in message (secrets still ***), or with
    no message a text value, with show="markdown"; a Table, a dict, a number or a secret is never Markdown and
    shows as today; the terminal prints the Markdown source."""
    text = "**Sales** are up\n\n- North\n- South"
    flow = write_flow(env.root / "markdown.yaml", {
        "n": python("output = 3"),
        "text": python(f"output = {text!r}"),
        "sales": {"type": "data.duckdb_sql",
                  "config": {"inputs": [], "sql": "select 'North' as region, 120 as total union all select 'South', 80"}},
        "info": python("output = {'region': 'North', 'total': 120}"),
        "key": secret("s3cret-value"),
        "hi": look(message="## Hi {value}", markdown=True),
        "hi_plain": look(message="## Hi {value}"),
        "n_md": look(markdown=True),
        "text_md": look(markdown=True),
        "text_plain": look(),
        "table_md": look(markdown=True),
        "table_plain": look(),
        "info_md": look(markdown=True),
        "key_md": look(markdown=True),
        "key_message_md": look(message="## Key {value}", markdown=True),
    }, ["n.output -> hi.value", "n.output -> hi_plain.value", "n.output -> n_md.value", "text.output -> text_md.value",
        "text.output -> text_plain.value", "sales.result -> table_md.value", "sales.result -> table_plain.value",
        "info.output -> info_md.value", "key.answer -> key_md.value", "key.answer -> key_message_md.value"])

    result, run_id = run_cli(env, flow)

    assert logged(env, run_id, "hi") == [("## Hi 3", "markdown")]
    assert logged(env, run_id, "hi_plain") == [("## Hi 3", None)]
    assert logged(env, run_id, "n_md") == [("3", None)]
    assert logged(env, run_id, "text_md") == [(text, "markdown")]
    assert logged(env, run_id, "text_plain") == [(text, None)]
    [(table, show)] = logged(env, run_id, "table_md")
    assert show is None and table.startswith("2 rows × 2 columns") and "North" in table, (table, show)
    assert logged(env, run_id, "table_plain") == [(table, None)]  # the boxed table unchanged
    assert logged(env, run_id, "info_md") == [(preview({"region": "North", "total": 120}), None)]
    assert logged(env, run_id, "key_md") == [("***", None)]
    assert logged(env, run_id, "key_message_md") == [("## Key ***", "markdown")]
    assert re.search(r"^\d\d:\d\d:\d\d \[hi\] INFO: ## Hi 3$", result.stderr, re.M), result.stderr
    assert "s3cret-value" not in result.stderr


OLD_SCHEMA = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flow TEXT NOT NULL, flow_path TEXT NOT NULL, params TEXT NOT NULL,
    status TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
    pid INTEGER, started_by TEXT NOT NULL DEFAULT '', counts_for TEXT NOT NULL DEFAULT ''
);
CREATE TABLE blocks (
    run_id INTEGER NOT NULL, block TEXT NOT NULL, status TEXT NOT NULL,
    started TEXT, finished TEXT, attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT, summary TEXT, tables TEXT, previews TEXT, PRIMARY KEY (run_id, block)
);
CREATE TABLE logs (
    run_id INTEGER NOT NULL, block TEXT, ts TEXT NOT NULL,
    level TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX logs_run ON logs (run_id, block);
CREATE TABLE keeps (
    flow_path TEXT NOT NULL, block TEXT NOT NULL, keep TEXT, keep_failed INTEGER NOT NULL,
    PRIMARY KEY (flow_path, block)
);
INSERT INTO runs (flow, flow_path, params, status, started, finished, pid, started_by, counts_for)
VALUES ('old', 'old.yaml', '{}', 'success', '2026-10-01T09:00:00.000', '2026-10-01T09:00:01.000', 1, 'manual', 'manual');
INSERT INTO logs VALUES (1, 'work', '2026-10-01T09:00:00.500', 'INFO', 'from before');
"""


def test_a_history_file_from_before_show_keeps_working(env):
    """The lead's correction to spec 9: an existing history.db without the logs' show column gets it when it is
    opened (as runs got pid, started_by and counts_for): runs still succeed and store show, and old lines read back
    with show None."""
    db = sqlite3.connect(env.home / "history.db")  # as Taskloom 0.24 left it
    db.executescript(OLD_SCHEMA)
    db.close()
    (env.root / "pic.png").write_bytes(b"x")
    flow = write_flow(env.root / "after.yaml", {"make": python("output = 'pic.png'"), "look": look()},
                      ["make.output -> look.value"])

    _, run_id = run_cli(env, flow)

    assert run_id == 2
    [(message, show)] = logged(env, run_id, "look")
    assert show == "image" and Path(message).resolve() == (env.root / "pic.png").resolve(), (message, show)
    history = History(env.home / "history.db")
    try:
        assert history.logs(1) == [{"block": "work", "ts": "2026-10-01T09:00:00.500", "level": "INFO",
                                    "message": "from before", "show": None}]
    finally:
        history.close()


# --- the Log in the editor: helpers -----------------------------------------------------------------------------

def entry(message: str, show=None, block="look", second=0) -> dict:
    """A log line as runs log them (and the history gives them back)."""
    e = {"ts": f"2026-10-28T09:00:{second:02d}.000", "block": block, "level": "INFO", "message": message}
    if show:
        e["show"] = show
    return e


def header(message: str, block="look", second=0) -> str:
    """How the Log writes an entry's line: HH:MM:SS [block] LEVEL message."""
    return f"09:00:{second:02d} [{block}] INFO {message}"


def show_log(win, entries):
    win.bottom_tabs.setCurrentWidget(win.log)
    win.log.set_entries(entries)
    QApplication.processEvents()


def plain(win) -> str:
    return win.log.text.document().toPlainText()


def cursor_at(edit, pos: int) -> QTextCursor:
    cursor = QTextCursor(edit.document())
    cursor.setPosition(pos)
    return cursor


def rect_at(edit, pos: int) -> QRect:
    """Where the character at `pos` starts on the screen (in the Log's viewport)."""
    return edit.cursorRect(cursor_at(edit, pos))


def line_start(edit, line: str) -> int:
    """The position of the Log's only line that is `line` (trailing spaces aside)."""
    text = edit.document().toPlainText()
    starts = [m.start() for m in re.finditer(rf"(?m)^{re.escape(line)}[ \t]*$", text)]
    assert len(starts) == 1, (line, text)
    return starts[0]


def bracket_x(edit, line: str) -> int:
    """The x of the "[" of `line` (where the block's name starts)."""
    return rect_at(edit, line_start(edit, line) + line.index("[")).x()


def images(edit) -> list[tuple]:
    """(position, QTextImageFormat) of each picture in the Log's document, in order."""
    found, block = [], edit.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.charFormat().isImageFormat():
                found += [(fragment.position() + i, fragment.charFormat().toImageFormat())
                          for i in range(fragment.length())]
            it += 1
        block = block.next()
    return found


def size_of(fmt) -> tuple[int, int]:
    return round(fmt.width()), round(fmt.height())


def shown_colors(edit, pos: int, fmt) -> list[QColor]:
    """The colours the Log shows in the middle of the picture at `pos`, scrolled into view and painted."""
    width, height = size_of(fmt)
    viewport = edit.viewport()
    rect = rect_at(edit, pos)
    edit.verticalScrollBar().setValue(edit.verticalScrollBar().value() + rect.top() + height // 2 - viewport.height() // 2)
    edit.horizontalScrollBar().setValue(edit.horizontalScrollBar().value() + rect.left() + width // 2
                                        - viewport.width() // 2)
    QApplication.processEvents()
    rect = rect_at(edit, pos)
    middle = QRect(rect.left() + width // 4, rect.top() + height // 4, width // 2, height // 2).intersected(viewport.rect())
    assert not middle.isEmpty(), (rect, viewport.rect())
    image = viewport.grab().toImage()
    ratio = image.devicePixelRatio()
    return [image.pixelColor(int(x * ratio), int(y * ratio))
            for x in range(middle.left(), middle.right() + 1, 2) for y in range(middle.top(), middle.bottom() + 1, 2)]


def mostly(colors, name: str) -> bool:
    """At least 90% of `colors` are `name` (red or blue; JPEG is a little off)."""
    def like(c):
        if name == "red":
            return c.red() > 200 and c.green() < 80 and c.blue() < 80
        return c.blue() > 200 and c.red() < 80 and c.green() < 80
    return sum(map(like, colors)) >= 0.9 * len(colors)


def picture(path: Path, width: int, height: int, color="red") -> Path:
    """A picture of one colour (a GIF: two frames, `color` then blue)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    kind = path.suffix.lower()
    if kind == ".svg":
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
                        f'<rect width="{width}" height="{height}" fill="{color}"/></svg>', encoding="utf-8")
    elif kind == ".gif":  # Qt cannot write GIFs
        from PIL import Image  # installed with matplotlib

        Image.new("RGB", (width, height), color).save(path, save_all=True, duration=100, loop=0,
                                                      append_images=[Image.new("RGB", (width, height), "blue")])
    else:
        image = QImage(width, height, QImage.Format_RGB32)
        image.fill(QColor(color))
        assert image.save(str(path)), path
    return path


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def huge_png(path: Path) -> Path:
    """A PNG whose header says 20000 × 20000 (its image data is not all there: only the header is read)."""
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 20000, 20000, 8, 6, 0, 0, 0))
                     + png_chunk(b"IDAT", zlib.compress(b"\x00" * 1000)) + png_chunk(b"IEND", b""))
    return path


def fonts_known() -> bool:
    return bool(QFontDatabase.families())  # none on Windows offscreen without QT_QPA_FONTDIR (the test job)


def fixed_pitch_at(edit, pos: int) -> bool:
    """Whether the character at `pos` is drawn in a fixed-width font (as this platform resolves it)."""
    fmt = cursor_at(edit, pos + 1).charFormat()
    return QFontInfo(fmt.font().resolve(edit.document().defaultFont())).fixedPitch()


def run_in_editor(win, qtbot, flow: Path):
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=90_000)
    assert tab.last["status"] == "success", (tab.last, plain(win))
    win.bottom_tabs.setCurrentWidget(win.log)
    QApplication.processEvents()
    return tab


def select(tab, block: str):
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)
    QApplication.processEvents()


def table_measurements(edit, table: str) -> dict:
    """How the text box `edit` draws `table` (Inspect's text of a Table, as preview() gives it): the font its rows are
    drawn in, the table's characters that font has no glyph for (QRawFont: no fallback fonts counted), and the
    distinct layouts of its rows: the x (in pixels) of each column separator on a row."""
    from PySide6.QtGui import QFontInfo, QRawFont, QTextCursor

    rows = table.splitlines()[1:]  # from its top border down (the first line is its size)
    doc = edit.document()
    start = doc.toPlainText().find("\n".join(rows))
    if start < 0:
        return {"found": False, "text": doc.toPlainText()[:3000]}

    def x(pos):
        cursor = QTextCursor(doc)
        cursor.setPosition(pos)
        return edit.cursorRect(cursor)

    cursor = QTextCursor(doc)
    cursor.setPosition(start + 1)
    font = cursor.charFormat().font().resolve(doc.defaultFont())
    raw = QRawFont.fromFont(font)
    layouts, lefts, pos = set(), set(), start
    for row in rows:
        layouts.add(tuple(x(pos + i).x() for i, ch in enumerate(row) if ch in "│┆┬┴┼╪┌┐└┘╞╡"))
        lefts.add(x(pos).x())
        pos += len(row) + 1
    return {"found": True, "font": QFontInfo(font).family(), "lefts": sorted(lefts),
            "missing": sorted({ch for ch in table if not ch.isspace() and not raw.supportsCharacter(ord(ch))}),
            "layouts": sorted(layouts)}


# --- 1, 3: a table in the Log lines up, as in the hover tooltip ------------------------------------------------

def table_csv(folder: Path) -> Path:
    """15 rows with a text, a whole-number, a decimal and an empty column."""
    rows = [f"PS-{100 + i},{i * 37 % 23 + 5},{i * 7919 % 900 / 7:.3f}," for i in range(1, 16)]
    path = folder / "articles.csv"
    path.write_text("publishing_system_id,articles,share,note\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_an_inspected_table_shows_in_the_log_as_its_tooltip_shows_it(win, qtbot, tmp_path):
    """Spec 3, 1b and 12 (protects them through the Log's rework): after a Read file → Inspect run, the Log has
    "HH:MM:SS [look] INFO 15 rows × 4 columns" and under it the same text table the hover tooltip shows, each row at
    the left edge, in a fixed-width font, its column separators at one x per column; copying gives ordinary spaces."""
    table_csv(tmp_path)
    flow = write_flow(tmp_path / "articles.yaml", {
        "manual": {"type": "trigger.manual"},
        "read": {"type": "data.read_file", "config": {"path": "articles.csv"}},
        "look": look(),
    }, ["manual.start -> read.after", "read.table -> look.value"])
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()

    tab = run_in_editor(win, qtbot, flow)

    history = History(win.home.history_db)
    table = history.blocks(tab.last["id"])["read"]["previews"]["table"]  # what hovering the table shows
    history.close()
    rows = table.splitlines()
    assert rows[0] == "15 rows × 4 columns" and "PS-115" in table, table
    edit = win.log.text
    lines = plain(win).split("\n")
    [at] = [i for i, line in enumerate(lines) if re.fullmatch(r"\d\d:\d\d:\d\d \[look\] INFO 15 rows × 4 columns", line)]
    assert lines[at + 1:at + len(rows)] == rows[1:], "\n".join(lines)
    measured = table_measurements(edit, table)
    assert measured["found"], measured
    assert measured["lefts"] == [rect_at(edit, line_start(edit, lines[at])).x()], measured  # at the left edge
    if fonts_known():
        start = plain(win).find("\n".join(rows[1:]))
        assert all(fixed_pitch_at(edit, start + i) for i in range(0, len("\n".join(rows[1:])), 7)
                   if plain(win)[start + i] != "\n"), measured["font"]
        assert len(measured["layouts"]) == 1, measured

    QApplication.clipboard().setText("")
    edit.setFocus()
    edit.selectAll()
    QTest.keyClick(edit, Qt.Key_C, Qt.ControlModifier)
    copied = QApplication.clipboard().text()
    assert "\xa0" not in copied
    assert "\n".join(rows[1:]) in "\n".join(copied.splitlines()), copied


MEASURE = textwrap.dedent(inspect.getsource(table_measurements)) + textwrap.dedent('''
    import json, sys
    from pathlib import Path
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    app = QApplication([sys.argv[0]])
    print("started", flush=True)
    import polars as pl
    from taskloom.editor.panels import LogPanel
    from taskloom.engine import preview
    from taskloom.table import Table

    frame = pl.DataFrame({"publishing_system_id": [f"PS-{100 + i}" for i in range(1, 16)],
                          "articles": [i * 37 % 23 + 5 for i in range(1, 16)],
                          "share": [i * 7919 % 900 / 7 for i in range(1, 16)],
                          "note": [None] * 15})
    table = preview(Table.from_frame(frame, Path("table.parquet").resolve()))
    log = LogPanel()
    log.resize(1100, 700)
    log.show()
    log.set_entries([{"ts": "2026-10-28T09:00:00.000", "block": "look", "level": "INFO", "message": table}])
    app.processEvents()
    print(json.dumps({**table_measurements(log.text, table), "fonts": bool(QFontDatabase.families())}), flush=True)
''')
NATIVE = "windows" if sys.platform == "win32" else "offscreen"  # elsewhere: this machine's fonts, offscreen
SETUPS = list(dict.fromkeys([("offscreen", "1"), (NATIVE, "1"), (NATIVE, "1.25"), (NATIVE, "1.5")]))


@pytest.mark.parametrize("platform, scale", SETUPS, ids=[f"{p}-{s}" for p, s in SETUPS])
def test_a_tables_characters_are_in_the_logs_font_and_its_columns_line_up(tmp_path, platform, scale):
    """Spec 1a, 1b, 1c (and 3): every character of Inspect's text of a 15-row table with text, number, decimal and
    empty columns is in the Log's own font (no fallback font), and each column separator has one x on every row.
    Offscreen with the system's fonts (on Windows: its Fonts folder, as the proofs do) and, on Windows, on Qt's real
    Windows platform at 100%, 125% and 150%; elsewhere offscreen at those scales, with this machine's fonts."""
    script = tmp_path / "measure.py"
    script.write_text(MEASURE, encoding="utf-8")
    environment = {**os.environ, "QT_QPA_PLATFORM": platform, "QT_SCALE_FACTOR": scale}
    if sys.platform == "win32" and platform == "offscreen":  # Qt's offscreen platform finds no fonts there otherwise
        environment.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

    done = subprocess.run([sys.executable, str(script)], env=environment, cwd=tmp_path, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)

    if platform == "windows" and "started" not in done.stdout.splitlines():
        pytest.skip(f"Qt's Windows platform could not start on this machine: {done.stderr[-800:]}")
    assert done.returncode == 0, done.stderr
    measured = json.loads(done.stdout.splitlines()[-1])
    if not measured["fonts"]:
        pytest.skip(f"Qt has no fonts on its {platform} platform here: no glyphs to check")
    assert measured["found"], measured
    assert measured["missing"] == [], f"{measured['font']} has no glyph for {measured['missing']}"
    assert len(measured["layouts"]) == 1, f"{measured['font']}: separators at {measured['layouts']}"


# --- 13, 17, 19: pictures ---------------------------------------------------------------------------------------

def test_a_charts_picture_shows_under_its_path_line_in_the_log_and_with_its_block(win, qtbot, tmp_path):
    """Spec 13, 17 (the user's case): after Read file → Chart → Inspect in the editor, the Log has the line
    "HH:MM:SS [look] INFO <absolute path>" and under it, starting under "[look]", the chart (1280×720 px) at
    640×360, drawn. Selecting another block hides it; selecting Inspect, or Show all, shows it again."""
    tab = run_in_editor(win, qtbot, chart_flow(tmp_path))
    edit = win.log.text
    [path] = [e["message"] for e in win.log.entries if e.get("block") == "look"]
    assert Path(path).is_absolute() and Path(path).is_file(), path
    [line] = [line for line in plain(win).split("\n") if line.endswith(f"[look] INFO {path}")]
    assert re.fullmatch(rf"\d\d:\d\d:\d\d \[look\] INFO {re.escape(path)}", line), line

    found = images(edit)
    assert [size_of(fmt) for _, fmt in found] == [(640, 360)], plain(win)
    [(pos, fmt)] = found
    assert pos > line_start(edit, line)
    assert abs(rect_at(edit, pos).x() - bracket_x(edit, line)) <= 2
    colors = shown_colors(edit, pos, fmt)
    base = edit.palette().color(QPalette.Base).rgb()
    assert sum(c.rgb() != base for c in colors) > 0.02 * len(colors), "the chart is not drawn"

    select(tab, "chart")
    assert images(edit) == [] and path not in plain(win)
    assert re.search(r"run #\d+ · .* started", plain(win)), plain(win)  # the run's lines stay
    select(tab, "look")
    assert [size_of(fmt) for _, fmt in images(edit)] == [(640, 360)]
    select(tab, "chart")
    win.log.clear_filter.click()  # Show all
    QApplication.processEvents()
    assert [size_of(fmt) for _, fmt in images(edit)] == [(640, 360)] and path in plain(win)


# (file name, its size, its size in the Log): scaled down, never up, to fit in 640 × 640, keeping proportions.
KINDS = [("wide.png", (1280, 720), (640, 360)), ("small.jpg", (64, 48), (64, 48)), ("tall.bmp", (300, 900), (213, 640)),
         ("moving.gif", (100, 50), (100, 50)), ("drawing.svg", (200, 100), (200, 100)),
         ("banner.webp", (2000, 500), (640, 160))]


def test_pictures_of_each_kind_show_scaled_down_never_up(win, tmp_path):
    """Spec 6b, 13: a PNG, JPEG, BMP, GIF (its first frame), SVG and WebP picture each show under their line, scaled
    down (never up) to fit in report.MAX_IMAGE_WIDTH × report.MAX_IMAGE_WIDTH (640 × 640), keeping proportions."""
    paths = [picture(tmp_path / name, *size) for name, size, _ in KINDS]
    show_log(win, [entry(str(p), "image", second=i) for i, p in enumerate(paths)])
    edit = win.log.text

    found = images(edit)

    sizes = [size_of(fmt) for _, fmt in found]
    assert len(sizes) == len(KINDS), plain(win)
    for size, (name, _, shown) in zip(sizes, KINDS):
        assert abs(size[0] - shown[0]) <= 1 and abs(size[1] - shown[1]) <= 1, (name, size, shown)
    for (pos, fmt), (name, _, _), path in zip(found, KINDS, paths):
        assert pos > line_start(edit, header(str(path), second=paths.index(path)))
        assert mostly(shown_colors(edit, pos, fmt), "red"), f"{name} is not drawn (or not its first frame)"


def test_a_picture_that_cannot_be_shown_gets_a_note_instead(win, tmp_path):
    """Spec 13 and edge paths: a missing file, a file whose header Qt cannot read (text saved as .png) and a picture
    over Qt's allocation limit (a 20000 × 20000 PNG header) show their path line and, indented under "[look]", a
    note instead of the picture; nothing hangs."""
    missing = tmp_path / "gone.png"
    corrupt = tmp_path / "notes.png"
    corrupt.write_text("this is text, not a picture", encoding="utf-8")
    huge = huge_png(tmp_path / "huge.png")
    reader = QImageReader(str(corrupt))
    reader.size()
    reason = reader.errorString()  # Qt's reason

    show_log(win, [entry(str(missing), "image"), entry(str(corrupt), "image", second=1),
                   entry(str(huge), "image", second=2)])
    edit = win.log.text

    assert images(edit) == []
    notes = [GONE, f"(cannot show this picture: {reason})",
             f"(cannot show this picture: over Qt's {QImageReader.allocationLimit()} MB picture limit)"]
    lines = [line.strip() for line in plain(win).split("\n")]
    for i, (path, note) in enumerate(zip((missing, corrupt, huge), notes)):
        line = header(str(path), second=i)
        assert note in lines and line in lines, plain(win)
        assert lines.index(note) == lines.index(line) + 1, plain(win)
        note_pos = plain(win).index(note)
        assert abs(rect_at(edit, note_pos).x() - bracket_x(edit, line)) <= 2


def test_a_picture_damaged_after_its_header_keeps_its_box_and_nothing_hangs(win, tmp_path):
    """Spec 13's stated limit: a PNG cut off inside its image data has a valid header, so its box is reserved at the
    shown size and painting it returns (Qt's broken-picture placeholder; no note, no hang, no crash)."""
    whole = picture(tmp_path / "whole.png", 1280, 720).read_bytes()
    cut = tmp_path / "cut.png"
    cut.write_bytes(whole[:len(whole) // 2])
    show_log(win, [entry(str(cut), "image"), entry("after", second=1)])
    edit = win.log.text

    assert [size_of(fmt) for _, fmt in images(edit)] == [(640, 360)], plain(win)
    assert not edit.viewport().grab().isNull()
    assert header(str(cut)) in plain(win) and header("after", second=1) in plain(win)
    assert "(cannot show this picture" not in plain(win) and GONE not in plain(win)


def test_a_shown_picture_can_be_deleted_and_the_next_redraw_says_it_is_gone(win, tmp_path):
    """Spec 13, 19 and edge paths (retention): once the Log has painted a picture, its file and folder can be
    deleted (also on Windows: the Log keeps no file open); the next redraw (Show all) shows the note."""
    folder = tmp_path / "runs" / "run-7"
    pic = picture(folder / "chart.png", 120, 80)
    show_log(win, [entry(str(pic), "image")])
    edit = win.log.text
    [(pos, fmt)] = images(edit)
    assert mostly(shown_colors(edit, pos, fmt), "red")

    os.remove(pic)
    folder.rmdir()
    win.log.set_filter("look")
    win.log.clear_filter.click()  # Show all
    QApplication.processEvents()

    assert images(edit) == []
    lines = [line.strip() for line in plain(win).split("\n")]
    assert GONE in lines, plain(win)
    assert lines.index(GONE) == lines.index(header(str(pic))) + 1, plain(win)


def test_an_overwritten_picture_shows_its_new_content(win, tmp_path):
    """Spec 13, 19 and edge paths: the picture is read from the file when the Log paints it, and a kept thumbnail is
    known by the file's time and size: a file overwritten later shows its new content, never a stale one."""
    pic = picture(tmp_path / "chart.png", 120, 80, "red")
    show_log(win, [entry(str(pic), "image")])
    edit = win.log.text
    [(pos, fmt)] = images(edit)
    assert mostly(shown_colors(edit, pos, fmt), "red")
    before = pic.stat().st_mtime_ns

    picture(pic, 120, 80, "blue")
    os.utime(pic, ns=(before + 2_000_000_000, before + 2_000_000_000))  # surely a later time, whatever the clock's tick

    assert mostly(shown_colors(edit, pos, fmt), "blue")


def test_copying_a_picture_entry_gives_its_path_line_and_nothing_for_the_picture(win, qtbot, tmp_path):
    """Spec 18: Ctrl+C of a Log with a picture copies its path line (a path with spaces, as ordinary spaces) and
    nothing for the picture: no object character, no blank line in its place."""
    pic = picture(tmp_path / "run folder" / "my chart.png", 64, 48)
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    show_log(win, [entry(str(pic), "image"), entry("done", second=1)])
    assert len(images(win.log.text)) == 1  # the picture is in the Log
    QApplication.clipboard().setText("")
    win.log.text.setFocus()
    win.log.text.selectAll()

    QTest.keyClick(win.log.text, Qt.Key_C, Qt.ControlModifier)

    copied = QApplication.clipboard().text()
    assert "\ufffc" not in copied and "\xa0" not in copied, repr(copied)
    assert copied.splitlines() == [header(str(pic)), header("done", second=1)], repr(copied)


COPY_THEN_QUIT = textwrap.dedent('''\
    import sys
    from pathlib import Path

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox

    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    app = QApplication(sys.argv)
    MainWindow.start_scheduler = lambda self, by_you=False: None
    QMessageBox.information = QMessageBox.warning = QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    w = MainWindow(Home(Path(sys.argv[1])))
    w.show()
    w.bottom_tabs.setCurrentWidget(w.log)
    w.log.set_entries([{"ts": "2026-10-15T09:00:00", "level": "INFO", "message": "copy me"}])
    w.log.text.setFocus()
    w.log.text.selectAll()
    QTest.keyClick(w.log.text, Qt.Key_C, Qt.ControlModifier)
    copied = QApplication.clipboard().text()
    print("copied: " + copied, flush=True)
    if "copy me" not in copied:
        sys.exit("nothing was copied from the Log")
    for tab in w.tabs_list():
        tab.doc.dirty = False
    QTimer.singleShot(0, w.close)
    QTimer.singleShot(50, app.quit)
    app.exec()
''')


def test_the_editor_quits_cleanly_after_copying_from_the_log(tmp_path):
    """Spec 18 (a reproduced crash): after Ctrl+C from the Log, closing the editor quits with exit code 0. A
    QMimeData made in Python for the copy was kept by the clipboard and freed twice on quit, crashing the editor."""
    script = tmp_path / "copy_then_quit.py"
    script.write_text(COPY_THEN_QUIT, encoding="utf-8")
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "TASKLOOM_HOME": str(tmp_path / "home"),
                   "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}

    done = subprocess.run([sys.executable, str(script), str(tmp_path / "home")], env=environment, cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)

    assert "copied: " in done.stdout and "copy me" in done.stdout, (done.stdout[-800:], done.stderr[-800:])
    assert done.returncode == 0, (done.returncode, done.stdout[-800:], done.stderr[-800:])


# --- 14, 15, 16: Markdown ---------------------------------------------------------------------------------------

MARKDOWN = textwrap.dedent("""\
    # Weekly sales

    Sales are **up** and *steady*, see `report.csv`.

    - North
    - South

    1. first
    2. second

    - [x] checked

    | region | total |
    |--------|------:|
    | North  |   120 |

    > a quote

    ```
    keep   these   spaces
    ```

    <b>x</b>
    """)


def block_of(edit, text: str):
    """The document block (paragraph) whose text is `text`."""
    block = edit.document().begin()
    while block.isValid():
        if block.text() == text:
            return block
        block = block.next()
    raise AssertionError(f"no paragraph {text!r} in {edit.document().toPlainText()!r}")


def char_format(edit, text: str, inside: str | None = None):
    """The format of the first character of `text` (in the paragraph `inside`, if given)."""
    pos = (block_of(edit, inside).position() + inside.index(text)) if inside else plain_index(edit, text)
    return cursor_at(edit, pos + 1).charFormat()


def plain_index(edit, text: str) -> int:
    pos = edit.document().toPlainText().find(text)
    assert pos >= 0, (text, edit.document().toPlainText())
    return pos


def test_a_markdown_entry_draws_headings_lists_tables_and_code_under_its_line(win):
    """Spec 14: a show="markdown" entry is the line "HH:MM:SS [look] INFO" and under it, starting under "[look]", the
    message drawn as GitHub Markdown: a bold heading, bold and italic, bullet, numbered and task lists, a quote, a
    table with borders and column alignment, code in a fixed-width font with its spaces kept while body text is
    not fixed-width, and raw HTML as it is written. No Mermaid note without a Mermaid fence."""
    show_log(win, [entry(MARKDOWN, "markdown"), entry("next", second=1)])
    edit = win.log.text
    text = plain(win)

    line = "09:00:00 [look] INFO"
    lines = [x.rstrip() for x in text.split("\n")]
    assert line in lines and lines[lines.index(line) + 1] == "Weekly sales", text
    heading = block_of(edit, "Weekly sales")
    assert heading.blockFormat().headingLevel() > 0
    assert char_format(edit, "Weekly sales").fontWeight() >= 600
    assert abs(rect_at(edit, heading.position()).x() - bracket_x(edit, line)) <= 2
    body = "Sales are up and steady, see report.csv."
    assert char_format(edit, "up", inside=body).fontWeight() >= 600
    assert char_format(edit, "steady", inside=body).fontItalic()
    for item in ("North", "South", "first", "second"):
        assert block_of(edit, item).textList() is not None, item
    assert block_of(edit, "checked").blockFormat().marker() == QTextBlockFormat.MarkerType.Checked
    assert block_of(edit, "a quote").blockFormat().intProperty(QTextFormat.BlockQuoteLevel) >= 1
    table = QTextCursor(block_of(edit, "region")).currentTable()
    assert table is not None and (table.rows(), table.columns()) == (2, 2)
    assert [[table.cellAt(r, c).firstCursorPosition().block().text() for c in range(2)] for r in range(2)] == \
        [["region", "total"], ["North", "120"]]
    assert table.format().border() > 0
    assert table.cellAt(1, 1).firstCursorPosition().block().blockFormat().alignment() & Qt.AlignRight
    assert "keep   these   spaces" in text.split("\n")
    assert "<b>x</b>" in text.split("\n")
    assert MERMAID_NOTE not in text
    if fonts_known():
        assert fixed_pitch_at(edit, plain_index(edit, "keep   these   spaces"))
        assert fixed_pitch_at(edit, plain_index(edit, "report.csv"))
        assert not fixed_pitch_at(edit, block_of(edit, body).position())


ENDINGS = {"heading": "# The end", "list": "- last item", "numbered": "1. last item", "table": "| a |\n|---|\n| 1 |",
           "quote": "> quoted", "code": "```\ncode\n```", "bold": "**all bold**"}


@pytest.mark.parametrize("ending", list(ENDINGS))
def test_the_line_after_a_markdown_entry_is_an_ordinary_log_line(win, ending):
    """Spec 14: whatever a Markdown entry ends with, the next entry is an ordinary Log line, like one before it: not
    a heading, list item, quote or table cell, at the left edge, in the same font."""
    show_log(win, [entry("before"), entry(f"Intro\n\n{ENDINGS[ending]}", "markdown", second=1),
                   entry("after", second=2)])
    edit = win.log.text
    assert MERMAID_NOTE not in plain(win)
    assert block_of(edit, "Intro") is not None  # drawn as Markdown

    before, after = line_start(edit, header("before")), line_start(edit, header("after", second=2))
    block = edit.document().findBlock(after)
    assert block.position() == after and block.text().startswith(header("after", second=2)), block.text()
    assert block.blockFormat().headingLevel() == 0 and block.textList() is None
    assert QTextCursor(block).currentTable() is None
    assert block.blockFormat().intProperty(QTextFormat.BlockQuoteLevel) == 0
    assert rect_at(edit, after).x() == rect_at(edit, before).x()
    fonts = []
    for start, line in ((before, header("before")), (after, header("after", second=2))):
        fmt = cursor_at(edit, start + len(line)).charFormat()  # the message's last character
        font = fmt.font().resolve(edit.document().defaultFont())
        fonts.append((QFontInfo(font).family(), font.pointSizeF(), font.bold(), font.italic()))
    assert fonts[1] == fonts[0]


def test_a_mermaid_fence_shows_as_text_with_a_note(win):
    """Spec 15, as phase 2 changes it: a ```mermaid fence in a Markdown entry that cannot be drawn shows as a code
    block (its spaces kept), and after it a grey italic line says why."""
    show_log(win, [entry(f"Steps:\n\n```mermaid\n{UNDRAWABLE}```\n", "markdown"), entry("next", second=1)])
    edit = win.log.text
    lines = plain(win).split("\n")

    assert "graph TD" in lines and "    start[Load --> report" in lines, lines
    [note_line] = [line for line in lines if line.startswith(MERMAID_NOTE)]
    assert lines.index(note_line) > lines.index("    start[Load --> report")
    assert lines.index(note_line) < lines.index(header("next", second=1))
    note = char_format(edit, note_line)
    assert note.fontItalic()
    assert note.hasProperty(QTextFormat.ForegroundBrush) and note.foreground().color().hsvSaturation() < 40
    if fonts_known():
        assert fixed_pitch_at(edit, plain_index(edit, "    start[Load --> report") + 4)


class _Opened(QObject):
    """Records the links the desktop would have been asked to open."""

    def __init__(self):
        super().__init__()
        self.urls = []

    @Slot(QUrl)
    def open(self, url):
        self.urls.append(url.toString())


def test_links_in_markdown_open_nothing_and_the_log_fetches_nothing(win, qtbot):
    """Spec 16 and edge paths: a Markdown link is shown as a link, but clicking it changes nothing and opens
    nothing; a picture or link naming an http:// address is never fetched."""
    requests = []

    class Server(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    opened = _Opened()
    for scheme in ("http", "https", "file"):
        QDesktopServices.setUrlHandler(scheme, opened, "open")
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        show_log(win, [entry(f"See [the report]({url}/report) and this chart:\n\n![chart]({url}/chart.png)\n",
                             "markdown"), entry("after", second=1)])
        edit = win.log.text
        link = plain_index(edit, "the report")
        fmt = cursor_at(edit, link + 1).charFormat()
        assert fmt.isAnchor() and fmt.anchorHref() == f"{url}/report", "the link is not shown as a link"
        before = plain(win)

        rect = rect_at(edit, link + 2)
        QTest.mouseClick(edit.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(rect.x(), rect.center().y()))
        edit.viewport().grab()  # paints the Markdown, its picture too
        qtbot.wait(500)  # time for a request, if one were made

        assert plain(win) == before
        assert opened.urls == [] and requests == []
    finally:
        for scheme in ("http", "https", "file"):
            QDesktopServices.unsetUrlHandler(scheme)
        server.shutdown()
        server.server_close()


LIGHT = {"Window": "#efefef", "WindowText": "#000000", "Base": "#ffffff", "AlternateBase": "#f7f7f7",
         "Text": "#000000", "Button": "#efefef", "ButtonText": "#000000", "Highlight": "#308cc6",
         "HighlightedText": "#ffffff", "PlaceholderText": "#7f7f7f", "Link": "#0000ff", "LinkVisited": "#ff00ff"}
DARK = {"Window": "#2b2d30", "WindowText": "#dfe1e5", "Base": "#1e1f22", "AlternateBase": "#26282b",
        "Text": "#dfe1e5", "Button": "#2b2d30", "ButtonText": "#dfe1e5", "Highlight": "#2f65ca",
        "HighlightedText": "#ffffff", "PlaceholderText": "#8c8c8c", "Link": "#589df6", "LinkVisited": "#b589ec"}


def contrast(a: QColor, b: QColor) -> float:
    """The WCAG contrast ratio of two colours (1 to 21)."""
    def luminance(c):
        def channel(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_markdown_and_notes_are_readable_in_the_light_and_the_dark_theme(win, tmp_path, theme):
    """Spec 14 and edge paths (dark mode): Markdown text follows the theme's text colour, and the notes are readable:
    every character in the Log has a contrast of at least 3:1 with the background behind it."""
    palette = QPalette()
    for role, value in (DARK if theme == "dark" else LIGHT).items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    original = QApplication.palette()
    QApplication.setPalette(palette)
    try:
        QApplication.processEvents()
        text = MARKDOWN + f"\n[a link](https://example.com)\n\n```mermaid\n{UNDRAWABLE}```\n"
        show_log(win, [entry(text, "markdown"),
                       entry(str(tmp_path / "gone.png"), "image", second=1)])
        edit = win.log.text
        assert block_of(edit, "Weekly sales").blockFormat().headingLevel() > 0  # drawn as Markdown
        assert MERMAID_NOTE in plain(win) and GONE in plain(win)
        unreadable, block = [], edit.document().begin()
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
                        unreadable.append((fragment.text(), color.name(), behind.name()))
                it += 1
            block = block.next()
        assert unreadable == []
    finally:
        QApplication.setPalette(original)
        QApplication.processEvents()


# --- 17: runs reopened from history, the block filter and following new lines ------------------------------------

def test_a_run_reopened_from_history_shows_the_same_markdown_and_picture(win, qtbot, tmp_path):
    """Spec 9, 17 and edge paths: a run that ran elsewhere (as a scheduled one does), reopened from Run history, shows
    the Inspect block's Markdown message, then its picture's path line, then the picture; selecting another block
    hides them, selecting the Inspect block or Show all shows them."""
    flow = chart_flow(tmp_path, message="# Sales chart\n\nTotals by **region**", markdown=True)
    done = subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow)], cwd=tmp_path, capture_output=True,
                          text=True, timeout=120, env={**os.environ, "TASKLOOM_HOME": str(win.home.root)})
    assert done.returncode == 0, done.stderr
    tab = win.open_flow(flow)
    win.bottom_tabs.setCurrentWidget(win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 1, timeout=10_000)
    at = table.visualItemRect(table.item(0, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # show that run
    QApplication.processEvents()
    edit = win.log.text

    def shown():
        assert block_of(edit, "Sales chart").blockFormat().headingLevel() > 0
        found = images(edit)
        assert [size_of(fmt) for _, fmt in found] == [(640, 360)], plain(win)
        [path] = [line for line in plain(win).split("\n") if line.endswith("chart.png")]
        assert block_of(edit, "Sales chart").position() < line_start(edit, path) < found[0][0]

    assert win.bottom_tabs.currentWidget() is win.log
    shown()
    select(tab, "chart")
    assert images(edit) == [] and "Sales chart" not in plain(win)
    select(tab, "look")
    shown()
    select(tab, "chart")
    win.log.clear_filter.click()  # Show all
    QApplication.processEvents()
    shown()


def test_the_log_follows_new_lines_only_when_it_was_at_the_bottom(win, qtbot, tmp_path):
    """Spec 17 (protects it through the Log's rework): new lines, pictures and Markdown keep the view at the bottom
    when it was there, and leave it where it is when you have scrolled up."""
    pic = picture(tmp_path / "chart.png", 64, 48)
    show_log(win, [entry(f"line {i}") for i in range(200)])
    bar = win.log.text.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0, timeout=5_000)
    bar.setValue(bar.maximum())

    for e in (entry(str(pic), "image", second=1), entry("# Done", "markdown", second=2), entry("last", second=3)):
        win.log.append(e)
    qtbot.waitUntil(lambda: bar.value() == bar.maximum(), timeout=5_000)
    last = plain_index(win.log.text, header("last", second=3))
    assert win.log.text.viewport().rect().contains(rect_at(win.log.text, last).center())

    bar.setValue(0)
    for e in (entry(str(pic), "image", second=4), entry("# More", "markdown", second=5), entry("later", second=6)):
        win.log.append(e)
    for _ in range(5):
        QApplication.processEvents()
    assert bar.value() == 0


def test_twenty_thousand_entries_are_drawn_in_bounded_time(win):
    """Spec 19 ("no worse in kind than today"): drawing 20,000 plain entries, and a block-filter switch, finish in a
    generous bound: this catches work that grows with the square of the entries, not speed."""
    entries = [entry(f"row {i}: value {i * 7}") for i in range(20_000)]
    start = time.perf_counter()
    show_log(win, entries)
    drawn = time.perf_counter() - start
    start = time.perf_counter()
    win.log.set_filter("other")
    win.log.set_filter(None)
    switched = time.perf_counter() - start

    assert header("row 19999: value 139993") in plain(win)
    assert drawn < 30 and switched < 30, (drawn, switched)


# --- 4, 5: the setting --------------------------------------------------------------------------------------------

def test_show_as_markdown_is_an_unticked_setting_of_the_inspect_block(win, tmp_path):
    """Spec 4, 5: an Inspect block's Properties have "Show as Markdown", unticked, with its help; ticking it sets
    markdown: true; the block's description (its hover text in Blocks) says what Inspect shows."""
    tab = win.open_flow(write_flow(tmp_path / "look.yaml", {"look": look()}))
    select(tab, "look")

    labels = [w for w in win.properties.findChildren(QLabel) if w.text() == "Show as Markdown"]
    assert len(labels) == 1, [w.text() for w in win.properties.findChildren(QLabel)]
    assert labels[0].toolTip() == HELP
    [box] = [b for b in win.properties.findChildren(QCheckBox) if b.toolTip() == HELP]
    assert not box.isChecked()
    box.click()
    try:
        assert tab.doc.blocks["look"]["config"]["markdown"] is True
    finally:
        tab.doc.dirty = False  # closing the window must not ask to save it
    tree = win.palette_panel.tree
    items = [tree.topLevelItem(i).child(j) for i in range(tree.topLevelItemCount())
             for j in range(tree.topLevelItem(i).childCount())]
    [item] = [i for i in items if i.data(0, Qt.UserRole) == "logic.inspect"]
    assert html.escape(DESCRIPTION) in item.toolTip(0)
