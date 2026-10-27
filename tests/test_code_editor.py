"""The Code boxes of Python and SQL in Properties (Python Code's code, Polars Transform's code, DuckDB SQL's sql, Run
Query's sql, and a python or sql Code field of a block of your own) feel like a code editor: Enter keeps how deep you
are (and goes one level deeper after a Python line ending with ':'), Tab and Shift+Tab indent with spaces, Backspace
goes back a level; keywords, strings, comments and numbers are coloured (readable in the light and the dark theme,
only on the screen); long lines do not wrap unless "Wrap lines" is ticked (remembered for every code box, also
after restarting the editor), with scroll bars as needed; line numbers on the left. Everything the box did before
keeps working. The other Code fields (expression, f-string, html) keep the plain box.

Driven through the real editor (the `win` fixture): flows opened from files, blocks selected on the canvas, keys
typed into the box, the flow saved to its file."""

import copy
import os
import re
from xml.etree import ElementTree

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette, QTextCursor  # noqa: E402
from PySide6.QtSvg import QSvgGenerator  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QPlainTextEdit  # noqa: E402

WRAP = "Wrap lines"
SVG = "{http://www.w3.org/2000/svg}"


# --- helpers ---------------------------------------------------------------------------------------------------

def settle():
    for _ in range(3):
        QApplication.processEvents()


def flow_file(path, blocks: dict):
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": []}, sort_keys=False), encoding="utf-8")
    return path


def open_blocks(win, path, blocks: dict):
    """Write a flow with these blocks to `path` and open it in the editor."""
    tab = win.open_flow(flow_file(path, blocks))
    settle()
    return tab


def select(tab, block):
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)
    settle()


def code_box(win) -> QPlainTextEdit:
    """The selected block's code box in Properties (the blocks used here have one)."""
    (box,) = [e for e in win.properties.widget().findChildren(QPlainTextEdit) if not e.isReadOnly()]
    return box


def wrap_boxes(win) -> list[QCheckBox]:
    return [b for b in win.properties.widget().findChildren(QCheckBox) if b.text() == WRAP]


def wrap_box(win) -> QCheckBox:
    boxes = wrap_boxes(win)
    assert len(boxes) == 1, f"{len(boxes)} {WRAP!r} tick boxes in Properties"
    return boxes[0]


def focus(win, qtbot, box):
    """As when you click into the box to type."""
    qtbot.waitExposed(win)
    win.activateWindow()
    box.setFocus()
    qtbot.waitUntil(box.hasFocus, timeout=5_000)


def leave(tab, qtbot, box):
    """As when you click on the canvas after typing: the box loses focus."""
    tab.view.setFocus()
    qtbot.waitUntil(lambda: not box.hasFocus(), timeout=5_000)
    settle()


def cursor_at(box, line: int, column: int | None = None):
    """Put the cursor on `line` (from 0), at `column` (None: the end of the line)."""
    block = box.document().findBlockByNumber(line)
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + (len(block.text()) if column is None else column))
    box.setTextCursor(cursor)


def select_text(box, start: tuple[int, int], end: tuple[int, int]):
    """Select from (line, column) to (line, column)."""
    doc = box.document()
    cursor = QTextCursor(doc)
    cursor.setPosition(doc.findBlockByNumber(start[0]).position() + start[1])
    cursor.setPosition(doc.findBlockByNumber(end[0]).position() + end[1], QTextCursor.KeepAnchor)
    box.setTextCursor(cursor)


def keys(box, *typed):
    """Type into the box: a str is typed as text, a Qt key is pressed, (key, modifier) with the modifier held."""
    for k in typed:
        if isinstance(k, str):
            QTest.keyClicks(box, k)
        elif isinstance(k, tuple):
            QTest.keyClick(box, *k)
        else:
            QTest.keyClick(box, k)
    settle()


def color_at(box, position: int) -> str:
    """The colour the character at `position` of the box's text is shown in."""
    block = box.document().findBlock(position)
    column = position - block.position()
    cursor = QTextCursor(block)
    cursor.setPosition(position + 1)  # (the format of the character before the cursor)
    brush = cursor.charFormat().foreground()
    color = brush.color() if brush.style() != Qt.NoBrush else None
    for shown in block.layout().formats():  # what a syntax highlighter shows over the text
        if shown.start <= column < shown.start + shown.length and shown.format.foreground().style() != Qt.NoBrush:
            color = shown.format.foreground().color()
    return (color or box.palette().text().color()).name()


def colors(box, context: str, word: str | None = None) -> set[str]:
    """The colours the characters of `word` (default: all of `context`) are shown in, where `context` (which
    occurs once in the box's text) has it; spaces are left out."""
    text = box.toPlainText()
    assert text.count(context) == 1, f"{context!r} is not once in the code"
    word = context if word is None else word
    start = text.index(context) + context.index(word)
    return {color_at(box, start + i) for i, char in enumerate(word) if not char.isspace()}


def color(box, context: str, word: str | None = None) -> str:
    shown = colors(box, context, word)
    assert len(shown) == 1, f"{word or context!r} is shown in several colours: {shown}"
    return shown.pop()


def drawn_texts(widget) -> list[tuple[float, float, str]]:
    """The texts `widget` and its children paint: (x, y of the baseline, text), in the widget's coordinates."""
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    svg = QSvgGenerator()
    svg.setOutputDevice(buffer)
    svg.setSize(widget.size())
    svg.setViewBox(widget.rect())
    widget.render(svg)
    buffer.close()
    texts = []
    for group in ElementTree.fromstring(bytes(buffer.data())).iter(SVG + "g"):
        matrix = re.match(r"matrix\(([^)]*)\)", group.get("transform", "matrix(1,0,0,1,0,0)"))
        dx, dy = (float(v) for v in matrix.group(1).split(",")[4:6])
        for text in group.findall(SVG + "text"):
            texts.append((float(text.get("x")) + dx, float(text.get("y")) + dy, text.text or ""))
    return texts


def line_numbers(box, starts: str) -> dict[str, list[str]]:
    """For each line of code painted that starts with `starts`: the numbers painted on its left, on its row."""
    around = box.parentWidget()  # (the numbers may be painted by the box or by a widget next to it)
    top = box.mapTo(around, QPoint(0, 0)).y()
    texts = [(x, y, t) for x, y, t in drawn_texts(around) if top <= y <= top + box.height()]
    half_line = box.fontMetrics().height() / 2
    return {t.strip(): [n.strip() for nx, ny, n in texts
                        if n.strip().isdigit() and nx < x and abs(ny - y) <= half_line]
            for x, y, t in texts if t.strip().startswith(starts)}


def visual_lines(box, line: int = 0) -> int:
    """How many lines on the screen `line` of the code takes."""
    return box.document().findBlockByNumber(line).layout().lineCount()


def contrast(a: QColor, b: QColor) -> float:
    """The WCAG contrast ratio of two colours (1 to 21)."""
    def luminance(c):
        def channel(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def background(box) -> QColor:
    """The colour behind the code, where nothing is written (the bottom right corner of a box with short code)."""
    image = box.viewport().grab().toImage()
    return image.pixelColor(image.width() - 3, image.height() - 3)


def clean(*windows):
    """Leave no unsaved changes (closing the windows must not ask to save them), also none still being typed."""
    focused = QApplication.focusWidget()
    if focused is not None:
        focused.clearFocus()  # (a code box saves what was typed in it when it loses focus)
        settle()
    for window in windows:
        for tab in window.tabs_list():
            tab.doc.dirty = False


def python(code: str) -> dict:
    return {"type": "logic.python", "config": {"inputs": [], "code": code}}


def duckdb(sql: str) -> dict:
    return {"type": "data.duckdb_sql", "config": {"sql": sql}}


LONG_LINE = "total = " + " + ".join(f"value_{i}" for i in range(80))  # much wider than any Properties panel
LONG_SQL = "select " + ", ".join(f"column_{i}" for i in range(80)) + " from sales"


# --- every python and sql Code box is a code editor; the others keep the plain box -----------------------------

IN_SCOPE = {  # block, its code field, its config, a line of code and the keyword in it
    "Python Code": ("logic.python", "code", {"inputs": []}, "    return value", "return"),
    "Polars Transform": ("data.polars", "code", {}, "    return value", "return"),
    "DuckDB SQL": ("data.duckdb_sql", "sql", {}, "    select value", "select"),
    "Run Query": ("db.run_query", "sql", {}, "    select value", "select"),
}


@pytest.mark.parametrize("title", list(IN_SCOPE))
def test_every_python_and_sql_code_box_is_a_code_editor(win, qtbot, tmp_path, title):
    """Spec scope (every Code field whose language is python or sql) with 1-4: its box has the Wrap lines tick box
    (unticked), colours its keyword, numbers its lines, keeps the indentation on Enter and indents with spaces on
    Tab."""
    type_id, key, config, line, keyword = IN_SCOPE[title]
    tab = open_blocks(win, tmp_path / "scope.yaml", {"work": {"type": type_id, "config": {**config, key: line}}})
    try:
        select(tab, "work")
        box = code_box(win)
        assert box.toPlainText() == line

        wrap = wrap_boxes(win)
        assert len(wrap) == 1 and wrap[0].isVisible() and not wrap[0].isChecked(), f"no {WRAP!r} box for {title}"
        assert color(box, line, keyword) != color(box, line, "value"), f"{title}: its keyword is not coloured"
        assert list(line_numbers(box, keyword).values()) == [["1"]], f"{title}: no line number 1 left of the line"

        focus(win, qtbot, box)
        cursor_at(box, 0)
        keys(box, Qt.Key_Return, Qt.Key_Tab)
        assert box.toPlainText() == line + "\n" + " " * 8  # the same 4 spaces, then Tab to the next multiple of 4
    finally:
        clean(win)


OTHER_CODE = {"If": ("logic.if", "condition"), "Inspect": ("logic.inspect", "message"),
              "HTML Template": ("reports.html_template", "template")}


@pytest.mark.parametrize("title", list(OTHER_CODE))
def test_the_other_code_fields_keep_the_plain_box(win, tmp_path, title):
    """Spec scope: the expression, f-string and html Code fields keep today's plain box: no Wrap lines tick box, no
    colours, long lines wrap."""
    type_id, key = OTHER_CODE[title]
    text = "if value and not done for 'x' # 42 " + " ".join(f"word_{i}" for i in range(80))
    tab = open_blocks(win, tmp_path / "plain.yaml", {"other": {"type": type_id, "config": {key: text}}})
    try:
        select(tab, "other")
        box = code_box(win)

        assert box.toPlainText() == text
        assert wrap_boxes(win) == []
        assert len(colors(box, text)) == 1, f"{title}: its text is coloured"
        assert visual_lines(box) > 1, f"{title}: its long line does not wrap"
    finally:
        clean(win)


# --- 1. indentation -------------------------------------------------------------------------------------------

def test_python_typed_with_enter_and_backspace_is_indented_as_shown_and_saved_exactly(win, qtbot, tmp_path):
    """Spec 1 (Enter keeps the indentation, one more level after a line ending with ':', Backspace goes back a level)
    with 2 and 5 (the colours are only display: the code is saved, as typed, when the box loses focus): the user's
    case of writing a few lines of Python."""
    path = tmp_path / "area.yaml"
    tab = open_blocks(win, path, {"area": python("import math")})
    try:
        select(tab, "area")
        box = code_box(win)
        focus(win, qtbot, box)
        cursor_at(box, 0)

        keys(box, Qt.Key_Return, "def area(r):", Qt.Key_Return, "if r < 0:", Qt.Key_Return, "raise ValueError(r)",
             Qt.Key_Return, Qt.Key_Backspace, "return math.pi * r ** 2", Qt.Key_Return, Qt.Key_Backspace,
             "output = area(2)")
        expected = ("import math\n"
                    "def area(r):\n"
                    "    if r < 0:\n"
                    "        raise ValueError(r)\n"
                    "    return math.pi * r ** 2\n"
                    "output = area(2)")
        assert box.toPlainText() == expected
        leave(tab, qtbot, box)

        assert tab.doc.blocks["area"]["config"]["code"] == expected
        assert win.save(tab)
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["blocks"]["area"]["config"]["code"] == expected
    finally:
        clean(win)


def test_enter_starts_the_new_line_with_the_same_spaces_or_tabs_as_typed(win, qtbot, tmp_path):
    """Spec 1: Enter starts the new line with the same leading whitespace as the line it was pressed on (spaces or
    tabs, exactly as typed), in Python and in SQL, also on a line that is not the last."""
    tab = open_blocks(win, tmp_path / "enter.yaml", {"python": python("x = 1"), "sql": duckdb("select 1")})
    try:
        for block, text, line, expected in [
                ("python", "    x = 1", 0, "    x = 1\n    "),
                ("python", "\t\tx = 1", 0, "\t\tx = 1\n\t\t"),
                ("python", " \t x = 1", 0, " \t x = 1\n \t "),
                ("python", "    a = 1\nb = 2", 0, "    a = 1\n    \nb = 2"),
                ("sql", "select a,\n       b", 1, "select a,\n       b\n       "),
                ("sql", "\tfrom sales", 0, "\tfrom sales\n\t")]:
            select(tab, block)
            box = code_box(win)
            focus(win, qtbot, box)
            box.setPlainText(text)
            cursor_at(box, line)
            keys(box, Qt.Key_Return)
            assert box.toPlainText() == expected, f"Enter after {text.splitlines()[line]!r} in {block}"
    finally:
        clean(win)


def test_after_a_python_line_ending_with_a_colon_enter_goes_one_level_deeper(win, qtbot, tmp_path):
    """Spec 1: in Python, after a line ending with ':' (ignoring trailing spaces and a # comment) the new line gets
    one more level (4 spaces); a line that does not end with ':' keeps its level."""
    tab = open_blocks(win, tmp_path / "colon.yaml", {"python": python("x = 1")})
    try:
        select(tab, "python")
        box = code_box(win)
        focus(win, qtbot, box)
        for line, indent in [("for row in rows:", 4),
                             ("    if ok:   # check it", 8),
                             ("if line.startswith('#'):", 4),  # (a '#' in a string is not a comment)
                             ("    # the steps:", 4),  # (only a comment: no code ends with ':')
                             ("total = {'a': 1}", 0)]:
            box.setPlainText(line)
            cursor_at(box, 0)
            keys(box, Qt.Key_Return)
            assert box.toPlainText() == line + "\n" + " " * indent, f"Enter after {line!r}"
    finally:
        clean(win)


def test_tab_inserts_spaces_up_to_the_next_multiple_of_four(win, qtbot, tmp_path):
    """Spec 1: Tab inserts spaces up to the next multiple of 4 columns; no tab character is typed, in Python or SQL."""
    tab = open_blocks(win, tmp_path / "tab.yaml", {"python": python("x = 1"), "sql": duckdb("select 1")})
    try:
        for block, text, expected in [("python", "", "    "), ("python", "ab", "ab  "), ("python", "abcd", "abcd    "),
                                      ("python", "x = 1", "x = 1   "), ("sql", "select", "select  ")]:
            select(tab, block)
            box = code_box(win)
            focus(win, qtbot, box)
            box.setPlainText(text)
            cursor_at(box, 0)
            keys(box, Qt.Key_Tab)
            assert box.toPlainText() == expected, f"Tab after {text!r} in {block}"
    finally:
        clean(win)


def test_tab_and_shift_tab_indent_and_unindent_every_selected_line(win, qtbot, tmp_path):
    """Spec 1: with several lines selected, Tab indents them all by 4 spaces and Shift+Tab removes up to 4 leading
    spaces from each."""
    tab = open_blocks(win, tmp_path / "block.yaml", {"python": python("a\n  b\nc")})
    try:
        select(tab, "python")
        box = code_box(win)
        focus(win, qtbot, box)

        select_text(box, (0, 1), (2, 1))  # from the end of the first line into the last one
        keys(box, Qt.Key_Tab)
        assert box.toPlainText() == "    a\n      b\n    c"

        select_text(box, (0, 2), (2, 5))
        keys(box, (Qt.Key_Backtab, Qt.ShiftModifier))  # (what Shift+Tab sends)
        assert box.toPlainText() == "a\n  b\nc"

        select_text(box, (0, 0), (2, 1))
        keys(box, (Qt.Key_Backtab, Qt.ShiftModifier))
        assert box.toPlainText() == "a\nb\nc"  # only the 2 spaces there were; the others had none
    finally:
        clean(win)


def test_backspace_after_indentation_spaces_goes_back_to_the_previous_multiple_of_four(win, qtbot, tmp_path):
    """Spec 1: Backspace right after indentation spaces removes back to the previous multiple of 4 columns; after
    other spaces it removes one character."""
    tab = open_blocks(win, tmp_path / "back.yaml", {"python": python("x = 1")})
    try:
        select(tab, "python")
        box = code_box(win)
        focus(win, qtbot, box)
        for text, column, expected in [(" " * 8, 8, " " * 4), (" " * 6, 6, " " * 4), ("  ", 2, ""),
                                       ("    x = 1", 4, "x = 1"), ("x = 1   ", 8, "x = 1  ")]:
            box.setPlainText(text)
            cursor_at(box, 0, column)
            keys(box, Qt.Key_Backspace)
            assert box.toPlainText() == expected, f"Backspace at column {column} of {text!r}"
    finally:
        clean(win)


# --- 2. colours -----------------------------------------------------------------------------------------------

PYTHON_SAMPLE = '''import math
def area(rows, formatted=None):
    """Adds the rows.
    for each one, if any
    """
    notes = 'if'  # for each row
    for row in rows:
        label = f"total {len(rows)}"
    mark = "#x" + 'single'
    return 42 + 3.5 if rows else notes
'''

SQL_SAMPLE = """-- daily totals
SELECT region, amount AS orders_count, 'north' AS place
from sales /* skip the select
where it is old */ WHERE amount > 2.5
  And region >= 10 -- and not before
Group By region
"""


def test_python_keywords_strings_comments_and_numbers_each_have_their_own_colour(win, tmp_path):
    """Spec 2: Python keywords, strings (single, double, triple-quoted over several lines, f-strings), comments and
    numbers each in their own colour; names (also those starting like a keyword) and keywords inside strings or
    comments are not coloured as keywords."""
    tab = open_blocks(win, tmp_path / "colours.yaml", {"python": python(PYTHON_SAMPLE)})
    try:
        select(tab, "python")
        box = code_box(win)

        keyword = color(box, "def area", "def")
        string = color(box, "'if'")
        comment = color(box, "# for each row")
        number = color(box, "42")
        plain = color(box, "    label", "label")
        assert len({keyword, string, comment, number, plain}) == 5, (keyword, string, comment, number, plain)

        for context, word in [("import math", "import"), ("    return", "return"), ("    for row", "for"),
                              ("row in rows", "in"), ("3.5 if", "if"), ("else notes", "else")]:
            assert color(box, context, word) == keyword, word
        for context, word in [("for each one, if any", None), ('"""Adds', None), ('"#x"', None), ("'single'", None),
                              ('f"total {', '"total ')]:
            assert color(box, context, word) == string, context
        assert color(box, "3.5") == number
        for context in ("formatted", "    notes", "else notes"):
            assert keyword not in colors(box, context, context.split()[-1]), f"{context!r} coloured as a keyword"
        assert color(box, "notes = 'if'", "notes") == plain  # back to code after the triple-quoted string
        assert color(box, "mark") == plain  # after "#x": it was a string, not a comment
    finally:
        clean(win)


def test_sql_keywords_in_any_case_strings_comments_and_numbers_each_have_their_own_colour(win, tmp_path):
    """Spec 2: SQL keywords (case-insensitive), strings, comments (-- and /* */, also over several lines) and numbers
    each in their own colour; keywords inside comments are coloured as comments."""
    tab = open_blocks(win, tmp_path / "sql.yaml", {"sql": duckdb(SQL_SAMPLE)})
    try:
        select(tab, "sql")
        box = code_box(win)

        keyword = color(box, "SELECT")
        string = color(box, "'north'")
        comment = color(box, "-- daily totals")
        number = color(box, "2.5")
        plain = color(box, "from sales", "sales")
        assert len({keyword, string, comment, number, plain}) == 5, (keyword, string, comment, number, plain)

        for context, word in [("from sales", "from"), ("*/ WHERE", "WHERE"), ("  And", "And"), ("Group By", "Group"),
                              ("Group By", "By"), ("amount AS", "AS")]:
            assert color(box, context, word) == keyword, word
        for context in ("/* skip the select", "where it is old */", "-- and not before"):
            assert color(box, context) == comment, context
        assert color(box, "10") == number
        assert keyword not in colors(box, "orders_count"), "orders_count coloured as a keyword"
    finally:
        clean(win)


LIGHT = {"Window": "#efefef", "WindowText": "#000000", "Base": "#ffffff", "AlternateBase": "#f7f7f7",
         "Text": "#000000", "Button": "#efefef", "ButtonText": "#000000", "Highlight": "#308cc6",
         "HighlightedText": "#ffffff", "PlaceholderText": "#7f7f7f"}
DARK = {"Window": "#2b2d30", "WindowText": "#dfe1e5", "Base": "#1e1f22", "AlternateBase": "#26282b",
        "Text": "#dfe1e5", "Button": "#2b2d30", "ButtonText": "#dfe1e5", "Highlight": "#2f65ca",
        "HighlightedText": "#ffffff", "PlaceholderText": "#8c8c8c"}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_colours_are_readable_in_the_light_and_the_dark_theme(win, tmp_path, theme):
    """Spec 2: in the light and the dark theme (the application's palette, as the system gives it and as the canvas
    reads it) keywords, strings, comments, numbers and names each have their own colour, readable: a contrast of
    at least 3:1 with the background of the code."""
    palette = QPalette()
    for role, value in (DARK if theme == "dark" else LIGHT).items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    original = QApplication.palette()
    QApplication.setPalette(palette)
    try:
        settle()
        tab = open_blocks(win, tmp_path / "theme.yaml", {
            "python": python("def area(r):  # half\n    return 'x', 42, size"),
            "sql": duckdb("select region, 'x', 42 -- all")})
        for block, shown in [("python", {"keyword": ("def area", "def"), "string": ("'x'",), "comment": ("# half",),
                                         "number": ("42",), "name": ("size",)}),
                             ("sql", {"keyword": ("select",), "string": ("'x'",), "comment": ("-- all",),
                                      "number": ("42",), "name": ("region",)})]:
            select(tab, block)
            box = code_box(win)
            settle()
            behind = background(box)
            found = {what: QColor(color(box, *where)) for what, where in shown.items()}
            assert len({c.name() for c in found.values()}) == len(found), f"{block}: not each in its own colour"
            for what, c in found.items():
                assert contrast(c, behind) >= 3, (f"{block} {what}s in {c.name()} on {behind.name()}: "
                                                  f"contrast {contrast(c, behind):.2f}")
    finally:
        clean(win)
        QApplication.setPalette(original)
        settle()


# --- 3. wrapping and scroll bars ------------------------------------------------------------------------------

def test_long_lines_do_not_wrap_and_scroll_bars_appear_as_needed(win, qtbot, tmp_path):
    """Spec 3: wrapping is off by default (Wrap lines unticked): a long line stays one line with a horizontal scroll
    bar; long code gets a vertical one; short code needs neither."""
    tab = open_blocks(win, tmp_path / "wide.yaml", {
        "short": python("output = 1"), "wide": python(LONG_LINE),
        "long": python("\n".join(f"step_{i} = {i}" for i in range(200)))})
    try:
        select(tab, "wide")
        box = code_box(win)
        settle()
        assert visual_lines(box) == 1, "the long line wraps"
        qtbot.waitUntil(lambda: box.horizontalScrollBar().isVisible(), timeout=3_000)
        assert not wrap_box(win).isChecked()

        select(tab, "short")
        box = code_box(win)
        assert not box.horizontalScrollBar().isVisible() and not box.verticalScrollBar().isVisible()

        select(tab, "long")
        box = code_box(win)
        qtbot.waitUntil(lambda: box.verticalScrollBar().isVisible(), timeout=3_000)
    finally:
        clean(win)


def test_wrap_lines_is_for_every_code_box_remembered_after_a_restart_and_changes_nothing_in_the_flow(
        win, qtbot, tmp_path):
    """Spec 3: the Wrap lines tick box turns wrapping on and off; the choice is for all code boxes (Python and SQL),
    remembered after restarting the editor (with your Taskloom files: a Taskloom that never chose starts unwrapped)
    and changes nothing in the flow."""
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    path = flow_file(tmp_path / "wrap.yaml", {"python": python(LONG_LINE), "sql": duckdb(LONG_SQL)})
    written = path.read_bytes()
    tab = win.open_flow(path)
    settle()
    before = copy.deepcopy(tab.doc.data)
    windows = [win]

    def started_again(home) -> tuple:
        """The editor started again on `home`, with the flow open: its window and the flow's tab."""
        again = MainWindow(Home(home))
        qtbot.addWidget(again)
        again.show()
        windows.append(again)
        settle()  # (its first start's check of the open flows: none yet)
        opened = again.open_flow(path)
        settle()
        return again, opened

    def wraps(window, opened, block) -> bool:
        select(opened, block)
        box = code_box(window)
        shown = wrap_box(window).isChecked()
        if shown:
            qtbot.waitUntil(lambda: visual_lines(box) > 1 and not box.horizontalScrollBar().isVisible(), timeout=3_000)
        else:
            qtbot.waitUntil(lambda: box.horizontalScrollBar().isVisible(), timeout=3_000)
            assert visual_lines(box) == 1
        return shown

    try:
        assert not wraps(win, tab, "python")
        wrap_box(win).click()
        assert wraps(win, tab, "python")
        assert wraps(win, tab, "sql")  # every code box
        assert tab.doc.data == before and not tab.doc.dirty  # nothing changed in the flow

        other = tmp_path / "another home"
        assert not wraps(*started_again(other), "python")  # a Taskloom that never chose

        again, opened = started_again(win.home.root)
        assert wraps(again, opened, "python")  # remembered
        wrap_box(again).click()
        assert not wraps(again, opened, "sql")
        assert not wraps(*started_again(win.home.root), "python")  # unticked, also remembered

        assert tab.doc.data == before and not tab.doc.dirty and not opened.doc.dirty
        assert path.read_bytes() == written
    finally:
        clean(*windows)


# --- 4. line numbers ------------------------------------------------------------------------------------------

def test_line_numbers_are_shown_on_the_left_of_each_line_also_after_scrolling(win, qtbot, tmp_path):
    """Spec 4: line numbers, from 1 as in error messages, in a margin on the left of the code, each on the row of
    its line, also for the lines scrolled to."""
    lines = [f"# step {k}" for k in range(1, 61)]
    tab = open_blocks(win, tmp_path / "numbers.yaml", {"python": python("\n".join(lines))})
    try:
        select(tab, "python")
        box = code_box(win)

        shown = line_numbers(box, "# step")
        assert "# step 1" in shown and "# step 60" not in shown, sorted(shown)
        assert shown == {line: [line.split()[-1]] for line in shown}, f"the numbers left of the lines: {shown}"

        box.verticalScrollBar().setValue(box.verticalScrollBar().maximum())
        settle()
        shown = line_numbers(box, "# step")
        assert "# step 60" in shown and "# step 1" not in shown, sorted(shown)
        assert shown == {line: [line.split()[-1]] for line in shown}, f"the numbers left of the lines: {shown}"
    finally:
        clean(win)


# --- 5. what works today keeps working ------------------------------------------------------------------------

def test_leaving_the_box_saves_the_code_as_one_undo_step(win, qtbot, tmp_path):
    """Spec 5: the code is saved to the block when the box loses focus, and that edit is one undo step."""
    tab = open_blocks(win, tmp_path / "undo.yaml", {"python": python("output = 1")})
    try:
        select(tab, "python")
        box = code_box(win)
        focus(win, qtbot, box)
        cursor_at(box, 0)
        keys(box, " + 1", Qt.Key_Return, "print(output)")
        typed = box.toPlainText()
        assert typed == "output = 1 + 1\nprint(output)"
        leave(tab, qtbot, box)

        assert tab.doc.blocks["python"]["config"]["code"] == typed and tab.doc.dirty
        undo, redo = (next(a for a in win.findChildren(QAction) if a.text() == text) for text in ("Undo", "Redo"))
        undo.trigger()
        settle()
        assert tab.doc.blocks["python"]["config"]["code"] == "output = 1"
        assert code_box(win).toPlainText() == "output = 1"
        redo.trigger()
        settle()
        assert tab.doc.blocks["python"]["config"]["code"] == typed
        assert code_box(win).toPlainText() == typed
    finally:
        clean(win)


def test_copy_paste_and_undo_keys_work_inside_the_box(win, qtbot, tmp_path):
    """Spec 5: Copy, Paste and Undo inside the code box act on its text (not on the flow's blocks)."""
    tab = open_blocks(win, tmp_path / "keys.yaml", {"python": python("alpha")})
    try:
        select(tab, "python")
        blocks = copy.deepcopy(tab.doc.blocks)
        box = code_box(win)
        focus(win, qtbot, box)
        QApplication.clipboard().setText("something else")

        QTest.keySequence(box, QKeySequence(QKeySequence.SelectAll))
        QTest.keySequence(box, QKeySequence(QKeySequence.Copy))
        assert QApplication.clipboard().text() == "alpha"
        cursor_at(box, 0)
        QTest.keySequence(box, QKeySequence(QKeySequence.Paste))
        settle()
        assert box.toPlainText() == "alphaalpha"
        QTest.keySequence(box, QKeySequence(QKeySequence.Undo))
        settle()
        assert box.toPlainText() == "alpha"
        assert tab.doc.blocks == blocks  # no block pasted, nothing undone in the flow
    finally:
        clean(win)


def test_a_new_python_code_block_shows_its_starter_snippet_coloured_as_comments(win, tmp_path):
    """Spec 5 (the starter snippet of a new Python Code block shows in the box) with 2 (comments are coloured, also
    the quotes and keywords written in them)."""
    tab = win.new_flow()
    try:
        tree = win.palette_panel.tree
        tree.itemDoubleClicked.emit(tree.findItems("Python Code", Qt.MatchExactly | Qt.MatchRecursive)[0], 0)
        settle()
        (block,) = [b for b in tab.doc.blocks if tab.doc.blocks[b]["type"] == "logic.python"]
        select(tab, block)
        box = code_box(win)
        snippet = tab.doc.blocks[block]["config"]["code"]

        assert box.toPlainText() == snippet
        comment = color(box, snippet.splitlines()[0])
        assert comment != box.palette().text().color().name(), "the comments are not coloured"
        assert colors(box, snippet) == {comment}, "parts of the comments are coloured as code"
    finally:
        clean(win)


LOOKUP_BLOCK = '''
from taskloom import Block, fields, ports

class Lookup(Block):
    type_id = "my.lookup"
    title = "Lookup"
    outputs = {"table": ports.Any()}
    config = {"sql": fields.Code("sql", default="select 1 as one")}

    def run(self, ctx):
        return {"table": None}
'''


def test_the_default_of_a_code_field_of_your_own_block_shows_as_its_placeholder(win, qtbot):
    """Spec 5 (the placeholder/default text) and scope (a Code field of language sql in a block of your own): its
    default shows greyed in the empty box, which is a code editor."""
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    blocks = win.home.root / "blocks"
    blocks.mkdir(parents=True, exist_ok=True)
    (blocks / "lookup.py").write_text(LOOKUP_BLOCK, encoding="utf-8")
    mine = MainWindow(Home(win.home.root))  # it finds your blocks when it starts
    qtbot.addWidget(mine)
    mine.show()
    tab = mine.new_flow()
    try:
        block = tab.doc.add_block("my.lookup", 0, 0)
        settle()
        select(tab, block)
        box = code_box(mine)

        assert box.toPlainText() == "" and box.placeholderText() == "select 1 as one"
        assert len(wrap_boxes(mine)) == 1, f"the sql box of your own block has no {WRAP!r} tick box"
    finally:
        clean(mine)
