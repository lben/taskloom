"""Typed values for inputs: what mutation testing of the change (docs/promises/typed-values.mutants.yaml) found no
test for, each checked as you meet it. Rows of docs/promises/typed-values.yaml, where a test is a row's behaviour.

As in tests/test_typed_values.py and tests/test_typed_values_editor.py, whose helpers these tests use: the real
`taskloom run` and `taskloom validate` on a temporary Taskloom folder, and the real editor window (the `win` fixture)
with real clicks, keys and menus. What a block draws is read from Qt's own record of the drawing (its SVG output: each
text with its font, each shape with its colours, where it is), and what a widget shows from its picture.
"""

import math
import os
import re
from xml.etree import ElementTree

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QPointF, QRect, QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgGenerator  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QLineEdit, QListWidget  # noqa: E402

from taskloom.editor.canvas import NODE_W, PORT_R  # noqa: E402
from tests.test_paths_editor_editing import field  # noqa: E402
from tests.test_theme import as_found, choose  # noqa: E402
from tests.test_typed_values import (as_recorded, flow_file, python, recorded, recorder,  # noqa: E402
                                     runs_recorded, said, typed)
from tests.test_typed_values_editor import (ERROR_TEXT, HINT, INPUT_VALUES, LONG, SET, TYPE_LIST,  # noqa: E402
                                            VALUE_FIELD, at, box_for, click_block, fill_in, filter_flow, hole_menu,
                                            hole_point, hover_text, key, one_block_flow, open_box, open_in, part,
                                            plain, put_hole_at, row_rect, saved, saved_values, settle, shown_value,
                                            type_into, value_row)

pytestmark = pytest.mark.usefixtures(as_found.__name__)  # each test leaves the look as it found it

SVG = "{http://www.w3.org/2000/svg}"
STYLE = ("fill", "fill-opacity", "stroke", "stroke-width", "font-family", "font-size", "font-style")


# --- what the canvas draws, as Qt records it -------------------------------------------------------------------------

def drawn_shapes(scene, rect: QRectF) -> list[dict]:
    """Every text, path and circle the scene draws in `rect`, as Qt's SVG output records it, in scene coordinates:
    its kind; its box (left, top, right, bottom: a path's or a circle's; a text's: where it starts on its baseline);
    a text's words; and how it is drawn (STYLE), as it or its group says."""
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    svg = QSvgGenerator()
    svg.setOutputDevice(buffer)
    svg.setSize(QSize(math.ceil(rect.width()), math.ceil(rect.height())))
    svg.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
    painter = QPainter(svg)
    scene.render(painter, QRectF(0, 0, rect.width(), rect.height()), rect)
    painter.end()
    buffer.close()
    found = []

    def point(m, x, y):
        return m[0] * x + m[2] * y + m[4] + rect.left(), m[1] * x + m[3] * y + m[5] + rect.top()

    def walk(element, m, style):
        transform = re.match(r"matrix\(([^)]*)\)", element.get("transform", ""))
        if transform:
            a, b, c, d, e, f = (float(v) for v in transform.group(1).split(","))
            m = (m[0] * a + m[2] * b, m[1] * a + m[3] * b, m[0] * c + m[2] * d, m[1] * c + m[3] * d,
                 m[0] * e + m[2] * f + m[4], m[1] * e + m[3] * f + m[5])
        style = {**style, **{k: element.get(k) for k in STYLE if element.get(k) is not None}}
        kind = element.tag.removeprefix(SVG)
        points = []
        if kind == "path":
            numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", element.get("d", ""))]
            points = [point(m, x, y) for x, y in zip(numbers[0::2], numbers[1::2])]
        elif kind == "circle":
            cx, cy, r = (float(element.get(k, "0")) for k in ("cx", "cy", "r"))
            points = [point(m, cx - r, cy - r), point(m, cx + r, cy + r)]
        elif kind == "text":
            points = [point(m, float(element.get("x", "0").split()[0]), float(element.get("y", "0").split()[0]))]
        if points:
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            found.append({"kind": kind, "box": (min(xs), min(ys), max(xs), max(ys)), **style,
                          "text": "".join(element.itertext()).strip() if kind == "text" else None})
        for child in element:
            walk(child, m, style)

    walk(ElementTree.fromstring(bytes(buffer.data())), (1, 0, 0, 1, 0, 0), {})
    return found


def advance(text: dict) -> float:
    """How wide a drawn text is, in its own font."""
    font = QFont(text["font-family"])
    font.setPointSizeF(float(text["font-size"]))
    font.setItalic(text.get("font-style") == "italic")
    return QFontMetricsF(font).horizontalAdvance(text["text"])


def row(tab, block, hole) -> dict:
    """What the block draws on the hole's row, in the block's own coordinates: the hole ("hole": its ring), its name
    ("name"), the output name on that row ("output", if any), and the value shown ("frame": its frame; "value": its
    text)."""
    node = tab.scene.nodes[block]
    settle()
    rect = row_rect(tab, block, hole)
    shapes = []
    for s in drawn_shapes(tab.scene, rect):
        left, top, right, bottom = s["box"]
        shapes.append({**s, "box": (left - node.x(), top - node.y(), right - node.x(), bottom - node.y())})
    top, bottom = rect.top() - node.y(), rect.bottom() - node.y()
    frames = [s for s in shapes if s["kind"] == "path" and s["box"][1] >= top - 1 and s["box"][3] <= bottom + 1
              and 8 < s["box"][0] < NODE_W]
    texts = [s for s in shapes if s["kind"] == "text" and top <= s["box"][1] <= bottom and s["text"]]
    found = {"hole": next((s for s in shapes if s["kind"] == "circle" and abs(s["box"][0] + s["box"][2]) < 1
                           and top <= (s["box"][1] + s["box"][3]) / 2 <= bottom), None),
             "name": next((t for t in texts if abs(t["box"][0] - 10) < 1), None)}
    assert len(frames) <= 1, frames
    if frames:
        found["frame"] = frames[0]
        found["value"] = next((t for t in texts if frames[0]["box"][0] <= t["box"][0] <= frames[0]["box"][2]), None)
        found["output"] = next((t for t in texts if t["box"][0] > frames[0]["box"][2]), None)
    return found


def width(shape: dict) -> float:
    return shape["box"][2] - shape["box"][0]


# --- what a widget shows ------------------------------------------------------------------------------------------

def picture(widget) -> QImage:
    """The widget as drawn, on white."""
    shown = widget.grab().toImage()
    image = QImage(shown.size(), QImage.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.drawImage(0, 0, shown)
    painter.end()
    return image


def colours(widget) -> list[QColor]:
    image = picture(widget)
    return [QColor(image.pixel(x, y)) for x in range(image.width()) for y in range(image.height())]


def darkest(widget) -> int:
    """The lightness (0: black, 255: white) of the darkest pixel the widget draws: the colour of its text."""
    return min(c.lightness() for c in colours(widget))


def reddest(widget) -> int:
    """How much redder than green and blue its reddest pixel is."""
    return max(c.red() - max(c.green(), c.blue()) for c in colours(widget))


def drawn_as_plain_text(label: QLabel) -> bool:
    """Whether the label draws its text as written (word-wrapped at its width), angle brackets and all: as a twin of
    it beside it (same parent, font, size and colour) that is told to."""
    twin = QLabel(label.parentWidget(), text=label.text(), wordWrap=True, textFormat=Qt.PlainText)
    twin.setStyleSheet(label.styleSheet())
    twin.setFont(label.font())
    twin.setFixedSize(label.size())
    try:
        return picture(twin) == picture(label)
    finally:
        twin.deleteLater()


# --- TV-3: a typed value the run cannot fill, a list written by hand --------------------------------------------------

SINGLE = "(text, a number, a date or time, yes/no) can be written into a setting"
CANNOT_FILL = {  # (the typed value, what the line into rows brings, the failed block's error)
    "a dictionary in a text": (typed("text", "{{rows}}"), "{'a': 1}",
                               f"the value typed for the input t: {{{{rows}}}}: the input rows got a dict, but only a "
                               f"single value {SINGLE}"),
    "a dictionary in a list's item": (typed("list", "a, {{rows}}"), "{'a': 1}",
                                      f"the value typed for the input t: {{{{rows}}}}: the input rows got a dict, but "
                                      f"only a single value {SINGLE}"),
    "a format the value cannot take": (typed("text", "{{rows:%Y}}"), "5",
                                       "the value typed for the input t: Invalid format specifier"),
}


@pytest.mark.invariant("TV-3")
@pytest.mark.parametrize("value, brought, error", list(CANNOT_FILL.values()), ids=list(CANNOT_FILL))
def test_a_typed_value_the_run_cannot_fill_fails_the_block_naming_its_input(env, value, brought, error):
    """use's typed value t names {{rows}}, an input that comes by a line from src. src brings a dictionary, which no
    text can hold, or the number 5, which the format %Y does not fit: `taskloom run` fails use (exit code 1), its
    error naming the input t and saying why; use never runs its code."""
    out = env.root / "got.jsonl"
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "src": python([], f"output = {brought}"),
        "use": recorder(out, ["rows", "t"], {"t": value})},
        ["start.start -> src.after", "src.output -> use.rows"])

    result = env.taskloom("run", flow)

    assert result.returncode == 1, said(result)
    run_id, _ = env.last_run()
    (failed,) = env.query("SELECT block, error FROM blocks WHERE run_id=? AND status='failed'", run_id)
    assert failed[0] == "use" and failed[1].startswith(error), failed
    assert recorded(out) == []


CHOSEN = {  # (use's typed value t, what is chosen for line_in, why the run is refused)
    "empty, in a text": (typed("text", "to {{line_in}}"), "",
                         "use: the value typed for the input t: {{line_in}}: nothing arrived at the input line_in"),
    "empty, in a list's item": (typed("list", "a, {{line_in}}"), "",
                                "use: the value typed for the input t: {{line_in}}: nothing arrived at the input "
                                "line_in"),
    "a table": (typed("text", "to {{line_in}}"), "table('rows.csv')",
                f"use: the value typed for the input t: {{{{line_in}}}}: the input line_in got a table, but only a "
                f"single value {SINGLE}"),
}


@pytest.mark.invariant("TV-3")
@pytest.mark.invariant("TV-6")
@pytest.mark.parametrize("value, chosen, why", list(CHOSEN.values()), ids=list(CHOSEN))
def test_run_only_with_an_input_chosen_that_its_typed_value_cannot_hold_is_refused_saying_why(env, value, chosen, why):
    """use's typed value t names {{line_in}}, an input a line from src comes into. `taskloom run --only use --input
    line_in=...`, as "with inputs I choose…" runs it, with line_in chosen empty or as a table read from a file:
    refused before it starts (exit code 2, no run), saying that nothing arrived at line_in, or that it got a table,
    which a text cannot hold."""
    out = env.root / "got.jsonl"
    (env.root / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "src": python([], "output = 'W3'"),
        "use": recorder(out, ["line_in", "t"], {"t": value})},
        ["start.start -> src.after", "src.output -> use.line_in"])

    result = env.taskloom("run", flow, "--only", "use", "--input", f"line_in={chosen}")

    assert result.returncode == 2, said(result)
    assert why in result.stderr, said(result)
    assert runs_recorded(env) == 0 and recorded(out) == []


@pytest.mark.invariant("TV-3")
def test_a_typed_list_written_by_hand_with_numbers_gives_the_block_numbers(env):
    """A typed list written by hand as a YAML list of a number, a decimal and a yes ([1, 2.5, true]): the block gets
    the list of the number 1, 2.5 and True, as written (an int arrives as a number)."""
    out = env.root / "got.jsonl"
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "use": recorder(out, ["x"], {"x": typed("list", [1, 2.5, True])})}, ["start.start -> use.after"])

    result = env.taskloom("run", flow)

    assert result.returncode == 0, said(result)
    assert recorded(out) == [as_recorded({"x": [1, 2.5, True]})]


# --- TV-9, TV-3: Validate names every typed value that cannot be ---------------------------------------------------

@pytest.mark.invariant("TV-9")
@pytest.mark.invariant("TV-3")
def test_validate_names_every_typed_value_that_cannot_be_and_each_list_item(env):
    """One block with six typed values written by hand, each wrong in its own way, in this order: on an input it does
    not have (gone), on run after (it takes none), with a line into the same input (lined), one that does not fit its
    type (bad: "1,5" as an int), a text naming nothing (named: {{nope}}), a list one of whose items names nothing
    (listed: a, {{none_here}}). `taskloom validate` names each of the six, not only the first."""
    flow = flow_file(env.root / "flow.yaml", {
        "src": python([], "output = 1"),
        "receiver": recorder(env.root / "got.jsonl", ["lined", "bad", "named", "listed"], {
            "gone": typed("text", "x"), "after": typed("text", "x"), "lined": typed("int", "1"),
            "bad": typed("int", "1,5"), "named": typed("text", "{{nope}}"),
            "listed": typed("list", "a, {{none_here}}")})},
        ["src.output -> receiver.lined"])

    validated = env.taskloom("validate", flow)

    errors = [line for line in validated.stderr.splitlines() if line.startswith("error: receiver: ")]
    assert validated.returncode == 2, said(validated)
    for hole, says in (("gone", "receiver has no such input"), ("after", "it takes no typed value"),
                       ("lined", "a line comes into lined too"), ("bad", "'1,5' as int"), ("named", "{{nope}}"),
                       ("listed", "{{none_here}}")):
        assert [line for line in errors if f"the value typed for the input {hole}" in line and says in line], \
            (hole, errors)


@pytest.mark.invariant("TV-3")
@pytest.mark.invariant("BR-4")
def test_validate_takes_a_parameter_and_a_lined_input_in_a_typed_text_and_warns_of_each_slip(env):
    """A typed text naming the flow parameter p and the input line_in that comes by a line ({{p}} and {{line_in}}) is
    no problem for `taskloom validate`; two typed texts each writing {p} with single braces get a "did you mean
    {{p}}" warning each."""
    flow = flow_file(env.root / "flow.yaml", {
        "src": python([], "output = 1"),
        "receiver": python(["line_in", "named", "x", "y"], "output = x", {
            "named": typed("text", "{{p}} and {{line_in}}"), "x": typed("text", "{p}"),
            "y": typed("text", "at {p}")})},
        ["src.output -> receiver.line_in"], params={"p": typed("text", "V7")})

    validated = env.taskloom("validate", flow)

    assert validated.returncode == 0 and "error" not in validated.stderr, said(validated)
    warned = [line for line in validated.stderr.splitlines()
              if line.startswith("warning: ") and "did you mean {{p}}" in line.lower()]
    assert sorted(hole for hole in ("named", "x", "y") for line in warned
                  if f"the value typed for the input {hole}" in line) == ["x", "y"], validated.stderr


# --- TV-5, TV-4: the menu says why, in its own words ------------------------------------------------------------------

REASONS = {  # (the block, the hole, the words of Set value…'s tooltip)
    "a table input": ({"type": "data.write_file"}, "table",
                      "A table input takes a table from a line: no typed value is a table."),
    "many lines": ({"type": "logic.merger"}, "lines",
                   "This input collects many lines into a list: connect lines to it."),
    "run after": ({"type": "logic.inspect"}, "after", "Run after only says when the block runs: no value goes in."),
    "a dictionary": ({"type": "db.run_query"}, "params",
                     "This input expects a dictionary, and no type of value is one: connect a line that brings one."),
    "the Loop's result": ({"type": "logic.loop"}, "result",
                          "The Loop's result comes from the blocks inside the Loop: connect the one that gives it."),
    "a plain input": ({"type": "logic.inspect"}, "value", "A value of its own for this input, as if a line brought it"),
}


@pytest.mark.invariant("TV-5")
@pytest.mark.invariant("TV-4")
def test_set_values_tooltip_says_why_in_the_words_of_its_reason(win, qtbot, tmp_path):
    """Right-click each hole: Set value…'s tooltip says, in these words, why a table input, a hole that collects many
    lines, run after, a hole that expects a dictionary and the Loop's result take no value, what a value is on a hole
    that takes one, and, on a hole a line comes into, to remove the line."""
    blocks = {f"b{i}": {**spec, **at(0, 400 * i)} for i, (spec, _, _) in enumerate(REASONS.values())}
    tab = open_in(win, flow_file(tmp_path / "why.yaml", {
        **blocks, "src": {**python([], "output = 1"), **at(-300, 0)}, "lined": {"type": "logic.inspect", **at(300, 0)}},
        ["src.output -> lined.value"]))
    cases = [(f"b{i}", hole, words) for i, (_, hole, words) in enumerate(REASONS.values())]
    cases.append(("lined", "value", "A line comes into it: remove the line to type a value instead."))

    for block, hole, words in cases:
        items, shows_tips = hole_menu(qtbot, win, tab, block, hole)
        assert SET in items and shows_tips, (block, hole, items)
        assert plain(items[SET][1]) == words, (block, hole, items[SET])


# --- TV-2: the value as the block draws it ----------------------------------------------------------------------------

@pytest.mark.invariant("TV-2")
def test_a_list_written_by_hand_shows_its_items_in_the_block_the_box_and_properties(win, qtbot, tmp_path):
    """A typed list written by hand as a YAML list ([a, b]): the block shows "a, b", the box opens with "a, b" to
    change, and Properties' Input values shows "a, b" — as the editor would have stored it typed."""
    tab = open_in(win, one_block_flow(tmp_path, python(["x"], "output = x", {"x": typed("list", ["a", "b"])})))

    assert shown_value(tab, "block", "x") == "a, b"
    box = box_for(qtbot, win, tab, "block", "x")
    assert part(box, QLineEdit, VALUE_FIELD).text() == "a, b"
    key(win, box, Qt.Key_Escape)
    click_block(tab, "block")
    edit, kinds = value_row(win, "x")
    assert (edit.text(), kinds.currentText()) == ("a, b", "list")


@pytest.fixture
def font_9pt(qapp):
    """The application's font at 9 pt (Windows' own size) during the test, then as it was."""
    before = QApplication.font()
    font = QFont(before)
    font.setPointSizeF(9)
    QApplication.setFont(font)
    yield
    QApplication.setFont(before)


@pytest.mark.invariant("TV-2")
def test_the_value_is_drawn_in_the_names_font_between_the_name_and_the_output(win, qtbot, tmp_path, font_9pt):
    """A block with the inputs n, b and c: its outputs done, output and error sit on the rows of run after, n and b;
    c's row has none. Values: 7 at n, a 500-character text at b and c, and (written by hand) at run after. Each value is
    written in the font and size of its hole's name, level with it, and starts just right of the name (run after's
    right of the words "run after"); a long one fits its frame with its "…", ends as far before the row's output name
    on the run after row as on b's (to within half a character: each font rounds the widths of the letters of done,
    in italics, and of error its own way), and on c's row (no output) as far from the block's right edge as the names
    are from its left edge."""
    tab = open_in(win, one_block_flow(tmp_path, recorder(tmp_path / "got.jsonl", ["n", "b", "c"], {
        "after": typed("text", LONG), "n": typed("int", "7"), "b": typed("text", LONG), "c": typed("text", LONG)})))
    rows = {hole: row(tab, "block", hole) for hole in ("after", "n", "b", "c")}

    for hole, drawn in rows.items():
        name, value, frame = drawn["name"], drawn.get("value"), drawn.get("frame")
        assert frame is not None and value is not None and name is not None, (hole, drawn)
        assert (value["font-family"], float(value["font-size"])) == (name["font-family"], float(name["font-size"])), \
            (hole, value, name)
        assert abs(value["box"][1] - name["box"][1]) <= 1, f"{hole}: the value's baseline {value} is not the name's"
        gap = frame["box"][0] - (name["box"][0] + advance(name))
        assert 0 < gap <= 10, f"{hole}: the value starts {gap:.1f} px right of the name {name['text']!r}"
        assert advance(value) <= width(frame) - 10 + 0.5, f"{hole}: {value['text']!r} does not fit its frame {frame}"
    assert rows["n"]["value"]["text"] == "7" and rows["c"]["value"]["text"].endswith("…")
    before_output = {hole: rows[hole]["output"]["box"][0] - rows[hole]["frame"]["box"][2] for hole in ("after", "b")}
    half_a_character = advance({**rows["b"]["value"], "text": "0"}) / 2  # (of the long value, in its font)
    assert abs(before_output["after"] - before_output["b"]) <= half_a_character + 0.5, (before_output, half_a_character)
    assert rows["c"].get("output") is None
    assert abs((NODE_W - rows["c"]["frame"]["box"][2]) - rows["c"]["name"]["box"][0]) <= 1, rows["c"]["frame"]


@pytest.mark.invariant("TV-2")
def test_the_value_is_framed_and_tinted_in_the_holes_colour(win, qtbot, tmp_path):
    """10 at filter's threshold: its frame is drawn in the colour of the threshold hole's ring, thinner than the ring,
    and filled with a tint of that colour (see-through), which the Dark theme makes stronger than the light one."""
    flow, _ = filter_flow(tmp_path, {"threshold": typed("int", "10")})
    tab = open_in(win, flow)

    def tint() -> float:
        drawn = row(tab, "filter", "threshold")
        frame, ring = drawn["frame"], drawn["hole"]
        assert frame["stroke"] == ring["stroke"] and frame["fill"] == ring["stroke"], (frame, ring)
        assert float(frame["stroke-width"]) < float(ring["stroke-width"]), (frame, ring)
        assert 0 < float(frame["fill-opacity"]) < 1, frame
        return float(frame["fill-opacity"])

    light = tint()
    choose(win, "Dark")
    settle()
    assert tint() > light


@pytest.mark.invariant("TV-2")
def test_hovering_the_value_names_its_type_and_line_breaks_show_as_spaces(win, qtbot, tmp_path):
    """Resting the mouse on 10 at filter's threshold shows "threshold: int" above the value; a text with a line break
    written by hand ("ab", "cd" on the next line) is shown in its block on one line, "ab cd"."""
    flow, _ = filter_flow(tmp_path, {"threshold": typed("int", "10")},
                          {"other": {**python(["x"], "output = x", {"x": typed("text", "ab\ncd")}), **at(300, 250)}})
    tab = open_in(win, flow)
    left, top, right, bottom = row(tab, "filter", "threshold")["frame"]["box"]
    node = tab.scene.nodes["filter"]

    tip = hover_text(qtbot, tab, node.mapToScene(QPointF((left + right) / 2, (top + bottom) / 2)))

    assert tip.splitlines()[0].strip() == "threshold: int", tip
    assert shown_value(tab, "other", "x") == "ab cd"


# --- TV-1: the box ----------------------------------------------------------------------------------------------------

@pytest.mark.invariant("TV-1")
def test_a_new_value_starts_as_text_on_a_box_that_hides_the_canvas(win, qtbot, tmp_path):
    """A double-click on filter's threshold (no value yet): the box opens with text chosen as the type, drawn on a
    background of its own over the canvas (the canvas does not show through it); abc, Enter: kept as the text abc."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    hole_point(tab, "filter", "threshold")  # (scrolled to the middle, as a double-click on it does)
    canvas = tab.view.viewport().grab().toImage()

    box = box_for(qtbot, win, tab, "filter", "threshold", centre=False)

    assert part(box, QListWidget, TYPE_LIST).currentItem().text() == "text"
    corner = box.geometry().bottomLeft() + QPoint(3, -3)  # (inside the box, clear of its frame and its parts)
    now = tab.view.viewport().grab().toImage()
    assert now.pixel(corner) != canvas.pixel(corner), "the canvas shows through the box"
    fill_in(box, None, "abc")
    key(win, box, Qt.Key_Return)
    assert open_box(win) is None
    assert saved_values(win, tab, "filter") == {"threshold": typed("text", "abc")}


@pytest.mark.invariant("TV-1")
def test_the_type_list_shows_every_type_whole(win, qtbot, tmp_path):
    """The box's list of types shows each of the seven types whole: every row inside the list, as wide as its
    name."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)

    types = part(box_for(qtbot, win, tab, "filter", "threshold"), QListWidget, TYPE_LIST)

    shown = types.viewport().rect()
    for i in range(types.count()):
        item = types.item(i)
        place = types.visualItemRect(item)
        assert shown.contains(place), f"{item.text()} is cut off: {place} of {shown}"
        assert types.fontMetrics().horizontalAdvance(item.text()) + 4 <= place.width(), (item.text(), place)
    key(win, open_box(win), Qt.Key_Escape)


@pytest.mark.invariant("TV-1")
def test_a_value_that_does_not_fit_is_explained_in_red_whole_and_as_typed(win, qtbot, tmp_path):
    """<b>1,5</b> and more, as an int: the box says why under the value, in red, as wide as the value field and
    wrapped over as many lines as it takes (all of it shown), with the value as typed (its <b> too, not made bold);
    the hint "Enter keeps it, Esc cancels" is lighter than the box's other words; the type list stays at the top of
    the box, level with the block.hole name."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    box = box_for(qtbot, win, tab, "filter", "threshold")
    fill_in(box, "int", "<b>1,5</b> and 2,5 and 3,5")

    key(win, box, Qt.Key_Return)

    box = open_box(win)
    assert box is not None
    error, value = part(box, QLabel, ERROR_TEXT), part(box, QLineEdit, VALUE_FIELD)
    assert error.isVisible() and "'<b>1,5</b> and 2,5 and 3,5'" in error.text(), error.text()
    assert error.width() >= value.width(), (error.geometry(), value.geometry())
    assert drawn_as_plain_text(error), "the reason is not drawn as written"
    assert reddest(error) > 100, "the reason is not written in red"
    name = next(label for label in box.findChildren(QLabel) if plain(label.text()) == "filter.threshold")
    hint = next(label for label in box.findChildren(QLabel) if label.text() == HINT)
    assert darkest(hint) >= darkest(name) + 40, (darkest(hint), darkest(name))
    types = part(box, QListWidget, TYPE_LIST)
    assert abs(types.geometry().top() - name.geometry().top()) <= 1, (types.geometry(), name.geometry())
    key(win, box, Qt.Key_Escape)


def canvas_of(win, tab, wide: int):
    """The window made as wide as leaves the canvas `wide` px (the panels beside it keep their width)."""
    win.resize(win.width() - (tab.view.viewport().width() - wide), win.height())
    settle()
    assert abs(tab.view.viewport().width() - wide) <= 2, tab.view.viewport().size()


def hole_on_screen(tab, block, hole) -> tuple[QPoint, int]:
    port = tab.scene.nodes[block].ports[(hole, False)]
    return tab.view.mapFromScene(port.scenePos()), math.ceil(PORT_R * tab.view.transform().m11())


# (where the hole is put on the canvas, given its size and the box's; where the box must then be)
WHERE = {
    "left, level with it": ("wide", lambda c, b: (c.width() - 20, c.height() // 2), "left"),
    "left, its bottom at the canvas's bottom": ("wide", lambda c, b: (c.width() - 20, c.height() - 12), "left"),
    "below, centred on it": ("narrow", lambda c, b: (c.width() // 2, 40), "below"),
    "below, at the canvas's right edge": ("narrow", lambda c, b: (c.width() - 100, 40), "below"),
    "above, centred on it": ("narrow", lambda c, b: (c.width() // 2, c.height() - 20), "above"),
}


@pytest.mark.invariant("TV-1")
@pytest.mark.parametrize("canvas, put, side", list(WHERE.values()), ids=list(WHERE))
def test_the_box_opens_where_the_canvas_has_room(win, qtbot, tmp_path, canvas, put, side):
    """On a canvas with room for two boxes side by side, the box opens left of a hole near the canvas's right edge,
    level with it (as near level as the canvas's bottom lets it). On a canvas too narrow for it beside the hole (100 px
    wider than the box), it opens below the hole, centred on it, or at the canvas's right edge when the hole is near
    it, and above it when there is no room below. It never covers the hole and lies wholly in the canvas."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    box = box_for(qtbot, win, tab, "filter", "threshold")
    size = box.size()
    key(win, box, Qt.Key_Escape)
    canvas_of(win, tab, 2 * size.width() + 40 if canvas == "wide" else size.width() + 100)
    shown = tab.view.viewport().rect()
    put_hole_at(tab, "filter", "threshold", *put(shown.size(), size))
    hole, radius = hole_on_screen(tab, "filter", "threshold")

    box = box_for(qtbot, win, tab, "filter", "threshold", centre=False)

    placed = box.geometry()
    assert shown.contains(placed), (placed, shown)
    assert not placed.intersects(QRect(hole.x() - radius, hole.y() - radius, 2 * radius + 1, 2 * radius + 1)), \
        (placed, hole)
    if side == "left":
        assert placed.right() < hole.x() - radius, (placed, hole)
        level = min(hole.y() - (placed.height() - 1) / 2, shown.bottom() - placed.height() + 1)
        assert abs(placed.top() - level) <= 1, (placed, hole, shown)
    else:
        assert (placed.top() > hole.y() + radius) if side == "below" else (placed.bottom() < hole.y() - radius), \
            (placed, hole)
        across = min(hole.x() - (placed.width() - 1) / 2, shown.right() - placed.width() + 1)
        assert abs(placed.left() - across) <= 1, (placed, hole, shown)
    key(win, box, Qt.Key_Escape)


@pytest.mark.invariant("TV-1")
def test_a_box_taller_than_the_canvas_stays_left_of_the_hole(win, qtbot, tmp_path):
    """The window as short as it goes (the canvas about 150 px high), the canvas with room for two boxes side by side,
    and the hole near its right edge: the box opens left of it. A value that does not fit, with a long reason (many
    lines), makes the box taller than the canvas: it stays left of the hole, beside it, not covering it."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    box = box_for(qtbot, win, tab, "filter", "threshold")
    canvas_of(win, tab, 2 * box.width() + 40)
    key(win, box, Qt.Key_Escape)
    win.resize(win.width(), 300)  # (it keeps its least height)
    settle()
    shown = tab.view.viewport().rect()
    put_hole_at(tab, "filter", "threshold", shown.width() - 20, shown.height() // 2)
    hole, radius = hole_on_screen(tab, "filter", "threshold")
    box = box_for(qtbot, win, tab, "filter", "threshold", centre=False)
    fill_in(box, "int", " ".join(["1,5"] * 60))

    key(win, box, Qt.Key_Return)

    box = open_box(win)
    assert box is not None and part(box, QLabel, ERROR_TEXT).isVisible()
    placed = box.geometry()
    assert placed.height() > shown.height(), (placed, shown)
    assert hole.x() - radius - 30 <= placed.right() < hole.x() - radius, (placed, hole)
    key(win, box, Qt.Key_Escape)


@pytest.mark.invariant("TV-1")
def test_keys_the_box_has_no_use_for_reach_the_canvas(win, qtbot, tmp_path):
    """With the box open, Page Down (which the value field has no use for) scrolls the canvas, as without the box;
    the box stays open, and Esc then closes it."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    box = box_for(qtbot, win, tab, "filter", "threshold")
    scroll = tab.view.verticalScrollBar()
    before = scroll.value()

    QTest.keyClick(part(box, QLineEdit, VALUE_FIELD), Qt.Key_PageDown)
    settle()

    assert scroll.value() > before
    assert open_box(win) is box
    key(win, box, Qt.Key_Escape)
    assert open_box(win) is None


# --- TV-2, TV-4, TV-5, TV-7, TV-8: Properties' Input values -----------------------------------------------------------

@pytest.mark.invariant("TV-7")
@pytest.mark.invariant("TV-2")
def test_emptying_a_value_in_properties_removes_it_and_an_empty_text_stays_one(win, qtbot, tmp_path):
    """use has 10 (int) at a and an empty text at b. In Properties' Input values, a's field emptied, Enter: a has no
    value (in the file and in the block). b's field (empty: the empty text), Enter: b still holds the empty text."""
    tab = open_in(win, one_block_flow(tmp_path, recorder(tmp_path / "got.jsonl", ["a", "b"], {
        "a": typed("int", "10"), "b": typed("text", "")})))
    click_block(tab, "block")
    edit, _ = value_row(win, "a")

    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(edit, Qt.Key_Delete)
    QTest.keyClick(edit, Qt.Key_Return)
    settle()

    assert saved_values(win, tab, "block") == {"b": typed("text", "")}
    assert shown_value(tab, "block", "a") is None
    edit, _ = value_row(win, "b")
    QTest.keyClick(edit, Qt.Key_Return)
    settle()
    assert saved_values(win, tab, "block") == {"b": typed("text", "")}
    assert row(tab, "block", "b").get("frame") is not None, "the block no longer shows that b has a value"


@pytest.mark.invariant("TV-8")
def test_renaming_an_input_of_a_block_without_typed_values(win, qtbot, tmp_path):
    """filter has no typed value. Its Inputs in Properties "rows, threshold" → "orders, threshold": the line from
    load comes into orders, and the block still has no typed value."""
    flow, _ = filter_flow(tmp_path)
    tab = open_in(win, flow)
    click_block(tab, "filter")

    type_into(field(win, "Inputs"), "orders, threshold")

    data = saved(win, tab)
    assert "load.rows -> filter.orders" in data["edges"], data["edges"]
    assert "inputs" not in data["blocks"]["filter"], data["blocks"]["filter"]


@pytest.mark.invariant("TV-2")
@pytest.mark.invariant("TV-4")
@pytest.mark.invariant("TV-5")
def test_properties_input_values_rows_say_what_each_hole_has(win, qtbot, tmp_path):
    """Properties' Input values: filter's rows, with lines from load and from more, says "lines from load.rows,
    more.output", lighter than the input's name; threshold, without a value, says in its empty field "no value: type
    one, or connect a line". Send Email has a row for its subject, though its body and attachments (which take none)
    come first. A Merger, none of whose holes take a value, has no Input values box."""
    flow, _ = filter_flow(tmp_path, more={
        "more": {**python([], "output = 4"), **at(0, 250)},
        "mail": {"type": "email.send", **at(600, 0)},
        "merge": {"type": "logic.merger", **at(600, 300)}}, edges=["more.output -> filter.rows"])
    tab = open_in(win, flow)

    click_block(tab, "filter")
    line = value_row(win, "rows")
    assert isinstance(line, QLabel) and plain(line.text()).strip() == "lines from load.rows, more.output", line.text()
    name = next(label for label in input_values_labels(win) if plain(label.text()).strip() == "rows")
    assert darkest(line) >= darkest(name) + 40, (darkest(line), darkest(name))
    edit, _ = value_row(win, "threshold")
    assert edit.text() == "" and edit.placeholderText() == "no value: type one, or connect a line"

    click_block(tab, "mail")
    edit, kinds = value_row(win, "subject")
    assert (edit.text(), kinds.currentText()) == ("", "text")

    click_block(tab, "merge")
    shown = win.properties.widget().findChild(QGroupBox, INPUT_VALUES)
    assert shown is None or not shown.isVisible(), "a Merger shows an Input values box"


def input_values_labels(win) -> list[QLabel]:
    return win.properties.widget().findChild(QGroupBox, INPUT_VALUES).findChildren(QLabel)

