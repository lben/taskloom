"""Long output in the Log, Inspect and Data preview: the rows NC-5, NC-7, NC-8, NC-9 and NC-10 of
docs/promises/long-output.yaml (agreed 2026-11-14). NC-1, 2, 3, 4 and 6 are in tests/test_long_output_log.py; of
NC-2 the last test here adds the click on the block that is already the selected one.

"Long" means over 200 lines or over 20,000 characters. A long message in the Log shows its first 200 lines and a
"show all" that opens the whole of it in Data preview.

Everything goes through the real editor window (the `win` fixture: offscreen, a temporary Taskloom folder) and real
runs: Manual Start -> load_orders (a Python Code block that returns the value) -> inspect_orders (an Inspect block);
one test of NC-8 reads what `taskloom run` prints. A test reads only what a user can see: the Log's words, Data
preview's box, grid and label, the text under a block on the canvas, what hovering shows, and for NC-7 the bytes of
the run history's file.

Not here: one message of over 50 million characters kept whole while it is the newest (NC-10): measured on
2026-11-13, such a message takes about 25 seconds to reach the editor, too long for a test.
"""
import html
import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QTextCursor, QTextDocument  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SHOW_ALL = "show all"  # the words that end a long message's first 200 lines in the Log
TOKEN = "tok-ORDERS-7f3a9c2e51b8"  # the API token of NC-7 (kept in the temporary Taskloom folder's secrets.yaml)
SHORTENED = re.compile(r"… \(\d{1,3}(,\d{3})* characters\)$")  # how a shortened one-line label ends


# --- the values ---------------------------------------------------------------------------------------------------

# load_orders' code: an API's answer, a dict of COUNT orders. As JSON with an indent of 2 an order is 17 lines and
# about 330 characters.
ORDERS = '''
def order(i):
    return {"id": i, "customer": f"customer {i:06d}", "email": f"buyer{i}@example.com", "paid": i % 3 == 0,
            "total": round(i * 1.37, 2), "items": ["pen", "ink", "paper"],
            "address": {"street": f"{i} Main Street", "city": "Springfield", "zip": f"{i % 100000:05d}"}}
result = {"orders": [order(i) for i in range(1, COUNT + 1)]}
'''
# The same answer carrying your API token: on its second line and on its last line but one.
ORDERS_WITH_TOKEN = '''
def order(i):
    return {"id": i, "customer": f"customer {i:06d}", "email": f"buyer{i}@example.com", "paid": i % 3 == 0,
            "total": round(i * 1.37, 2), "items": ["pen", "ink", "paper"]}
token = ctx.home.get_secret("orders_token")
result = {"token": token, "orders": [order(i) for i in range(1, COUNT + 1)],
          "next": f"/orders?page=2&token={token}"}
'''
# A page of orders as a text of COUNT lines of 100 characters and a last line that says it ends: what load_orders
# logs in NC-10, four pages each run.
PAGE = '''
def page(batch, number):
    lines = [f"batch {batch} page {number} order {i:06d}: " + "pen, ink, paper; " * 4 + "end" for i in range(1, COUNT + 1)]
    return "\\n".join(lines) + f"\\nbatch {batch} page {number} ends here"
'''
LOG_PAGES = PAGE + '''
batch = open("batch.txt").read().strip()
for number in (1, 2, 3, 4):
    log(page(batch, number))
'''


def orders_code(count: int, code: str = ORDERS) -> str:
    return code.replace("COUNT", str(count))


def made_by(code: str, name: str = "result"):
    """What a Python Code block with this code gives, made here; a secret it reads is already ***, as it is shown."""
    stars = types.SimpleNamespace(home=types.SimpleNamespace(get_secret=lambda key: "***"))
    made = {"ctx": stars}
    exec(code, made)
    return made[name]


def whole_text(value) -> str:
    """The whole text of a value, as Inspect writes it and Data preview shows it: JSON with an indent of 2."""
    return json.dumps(value, indent=2, ensure_ascii=False)


# --- the flows ------------------------------------------------------------------------------------------------------

def python(code: str, inputs=(), outputs=("result",), **more) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code, **more}}


def orders_flow(folder: Path, code: str, more: dict | None = None, edges=()) -> Path:
    """manual -> load_orders (Python Code: `code`) -> inspect_orders (Inspect), then the blocks and connections of
    `more` and `edges`."""
    path = folder / "orders.yaml"
    blocks = {"manual": {"type": "trigger.manual"}, "load_orders": python(code),
              "inspect_orders": {"type": "logic.inspect"}, **(more or {})}
    edges = ["manual.start -> load_orders.after", "load_orders.result -> inspect_orders.value", *edges]
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": edges}, sort_keys=False), encoding="utf-8")
    return path


# --- the editor -------------------------------------------------------------------------------------------------------

def log_text(win) -> str:
    return win.log.text.toPlainText()


def log_lines(win) -> list[str]:
    return [line.strip() for line in log_text(win).splitlines()]


def box(win) -> str:
    """The text in Data preview's box."""
    return win.preview.text.toPlainText()


def label(win) -> str:
    """Data preview's label as it reads."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", win.preview.label.text()))).strip()


def run(win, qtbot, tab, ends="success") -> int:
    """Run now, and wait for the run to end and for the editor to have shown it. Gives the run's number."""
    count = len(tab.runs)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: len(tab.runs) > count and not tab.running and tab.runs[-1].status is not None,
                    timeout=300_000)
    qtbot.wait(300)  # the editor's own follow-up: the history, the canvas
    assert tab.runs[-1].status == ends, (tab.runs[-1].status, log_text(win)[-2000:])
    return tab.runs[-1].run_id


def select(tab, block: str):
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)


def click_on_canvas(tab, block: str):
    """Left-click the middle of `block` on the canvas itself, with the mouse."""
    tab.view.ensureVisible(tab.scene.nodes[block])
    at = tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center())
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()


def under(tab, block: str) -> str:
    """The text under a block on the canvas: its finished line."""
    return tab.scene.nodes[block].status_text


def as_seen(tip: str) -> str:
    """A hover's text as it is shown (its HTML laid out)."""
    document = QTextDocument()
    document.setHtml(tip)
    return document.toPlainText()


def hover(tab, block: str) -> str:
    """What hovering the result under a block shows."""
    return as_seen(tab.scene.nodes[block].footer.toolTip())


def press_show_all(win):
    """Click, with the mouse, the last "show all" in the Log: the newest long message's."""
    win.bottom_tabs.setCurrentWidget(win.log)
    QApplication.processEvents()
    edit = win.log.text
    at = edit.toPlainText().rfind(SHOW_ALL)
    assert at >= 0, f'the Log has no "{SHOW_ALL}" to press; it ends: {log_text(win)[-300:]!r}'
    cursor = QTextCursor(edit.document())
    cursor.setPosition(at + len(SHOW_ALL) // 2)
    edit.setTextCursor(cursor)  # scroll there
    edit.ensureCursorVisible()
    QApplication.processEvents()
    spot = edit.cursorRect(cursor).center()
    assert edit.viewport().rect().contains(spot), (spot, edit.viewport().rect())
    QTest.mouseClick(edit.viewport(), Qt.LeftButton, Qt.NoModifier, spot)


def arrives(qtbot, win, whole: str, seconds: float) -> bool:
    """Wait, the window's own event loop turning, until Data preview's box holds `whole` (at most `seconds`)."""
    edit = win.preview.text
    try:  # (the count is read first: reading 5 MB of text on every turn would itself keep the window busy)
        qtbot.waitUntil(lambda: edit.document().characterCount() > len(whole) and edit.toPlainText() == whole,
                        timeout=int(seconds * 1000))
    except qtbot.TimeoutError:
        return False
    return True


def missing(shown: str, whole: str) -> str:
    """"" when `shown` is `whole` to its last character; else what differs, in a few words (never the texts)."""
    if shown == whole:
        return ""
    same = next((i for i, (a, b) in enumerate(zip(shown, whole)) if a != b), min(len(shown), len(whole)))
    return (f"{len(shown):,} characters shown of {len(whole):,}; the same up to character {same:,}; "
            f"shown there {shown[same:same + 40]!r}, expected {whole[same:same + 40]!r}; "
            f"it ends {shown[-40:]!r}, expected {whole[-40:]!r}")


class Heartbeat:
    """How long the window does not answer: a timer due every 20 ms in the window's thread, where a click or a key
    waits its turn too, and the gaps between its calls. `longest` is the longest gap, `took` the whole time."""

    def __init__(self):
        self.beats, self.longest, self.took = [], 0.0, 0.0
        self.timer = QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(20)
        self.timer.timeout.connect(lambda: self.beats.append(time.perf_counter()))

    def __enter__(self):
        self.beats = [time.perf_counter()]
        self.timer.start()
        return self

    def __exit__(self, *error):
        self.timer.stop()
        self.beats.append(time.perf_counter())
        self.took = self.beats[-1] - self.beats[0]
        self.longest = max(b - a for a, b in zip(self.beats, self.beats[1:]))


# --- NC-5: the editor stays responsive ----------------------------------------------------------------------------

FIVE_MB = orders_code(14_000)  # as JSON with an indent of 2: about 4.8 million characters in about 238,000 lines


def run_5_mb(win, qtbot, folder: Path):
    """The 5 MB JSON's flow, opened and run to its end with no block selected. Gives (the flow's tab, the whole text
    of the value, the heartbeat while it ran)."""
    whole = whole_text(made_by(FIVE_MB))
    assert 4_700_000 < len(whole) < 4_900_000, len(whole)
    tab = win.open_flow(orders_flow(folder, FIVE_MB))
    qtbot.waitExposed(win)
    with Heartbeat() as running:
        run(win, qtbot, tab)
    print(f"NC-5 measured: a JSON of {len(whole):,} characters in {whole.count(chr(10)) + 1:,} lines. While the flow "
          f"ran to its end ({running.took:.2f} s), the longest time the window did not answer: {running.longest:.3f} s")
    return tab, whole, running


def measured(what: str, beat: Heartbeat, arrived: bool) -> str:
    """What a step measured, in words (printed for the proof, and said when a limit is passed)."""
    return (f"{what}: {'all of it was' if arrived else 'not all of it was'} in Data preview after {beat.took:.2f} s; "
            f"the longest time the window did not answer: {beat.longest:.3f} s")


@pytest.mark.invariant("NC-5")
def test_with_a_5_mb_json_the_window_always_answers_within_a_second_and_show_all_brings_all_of_it_within_15_seconds(
        win, qtbot, tmp_path):
    tab, whole, running = run_5_mb(win, qtbot, tmp_path)
    assert running.longest <= 1, (f"while the flow ran, the window did not answer for {running.longest:.3f} s "
                                  f"(the run took {running.took:.2f} s)")

    with Heartbeat() as opening:
        press_show_all(win)
        arrived = arrives(qtbot, win, whole, 20)
    said = measured("NC-5 measured: after show all was pressed", opening, arrived)
    print(said)

    assert arrived, f"{said}. Data preview's box: {missing(box(win), whole)}"
    assert opening.longest <= 1, said
    assert opening.took <= 15, said


@pytest.mark.invariant("NC-5")
def test_selecting_the_block_that_made_a_5_mb_json_or_running_again_with_it_selected_never_holds_the_window_a_second(
        win, qtbot, tmp_path):
    tab, whole, running = run_5_mb(win, qtbot, tmp_path)

    with Heartbeat() as selecting:
        select(tab, "load_orders")  # the block that made the value
        arrived = arrives(qtbot, win, whole, 20)
    said = measured("NC-5 measured: after load_orders was selected", selecting, arrived)
    print(said)
    assert arrived, f"{said}. Data preview's box: {missing(box(win), whole)}"
    assert selecting.longest <= 1, said

    with Heartbeat() as again:  # still selected: Data preview shows its new output as the run makes it
        run(win, qtbot, tab)
        arrived = arrives(qtbot, win, whole, 20)
    said = measured("NC-5 measured: after the flow ran again with load_orders selected", again, arrived)
    print(said)
    assert arrived, f"{said}. Data preview's box: {missing(box(win), whole)}"
    assert again.longest <= 1, said


# --- NC-7: secrets stay hidden in the whole text ---------------------------------------------------------------------

@pytest.mark.invariant("NC-7")
def test_an_api_token_in_a_long_response_shows_as_stars_in_the_log_data_preview_the_hover_and_the_run_history_file(
        win, qtbot, tmp_path):
    code = orders_code(1_500, ORDERS_WITH_TOKEN)
    whole = whole_text(made_by(code))  # with *** where the token is: on its second line and its last line but one
    assert 300_000 < len(whole) < 900_000 and whole.count("***") == 2 and whole.endswith('token=***"\n}'), len(whole)
    win.home.root.mkdir(parents=True, exist_ok=True)
    secrets = win.home.root / "secrets.yaml"
    secrets.write_text(yaml.safe_dump({"orders_token": TOKEN}), encoding="utf-8")
    secrets.chmod(0o600)
    tab = win.open_flow(orders_flow(tmp_path, code))
    qtbot.waitExposed(win)

    run(win, qtbot, tab)

    kept = [f.name for f in win.home.root.glob("history.db*") if TOKEN.encode() in f.read_bytes()]
    assert not kept, f"the token is in the run history's file: {kept}"
    hovers = [as_seen(tip) for node in tab.scene.nodes.values()
              for tip in (node.footer.toolTip(), *(port.toolTip() for port in node.ports.values()))]
    hovers += [as_seen(edge.toolTip()) for edge in tab.scene.edge_items]  # the blocks, their inputs and outputs, the lines
    assert not any(TOKEN in text for text in hovers), "the token shows when hovering the canvas"
    assert '"token": "***"' in hover(tab, "load_orders"), hover(tab, "load_orders")[:300]
    assert TOKEN not in log_text(win), "the token is in the Log"
    assert '"token": "***",' in log_lines(win), log_text(win)[:600]

    press_show_all(win)
    assert arrives(qtbot, win, whole, 30), f"after show all, Data preview's box: {missing(box(win), whole)}"
    for block in ("load_orders", "inspect_orders"):
        select(tab, block)
        assert arrives(qtbot, win, whole, 30), f"with {block} selected, Data preview's box: {missing(box(win), whole)}"
        assert TOKEN not in log_text(win), f"the token is in the Log with {block} selected"


# --- NC-8: one-line labels stay one line, and say when they are shortened -------------------------------------------------

LONG = orders_code(100)  # as JSON with an indent of 2: about 33,000 characters in 1,700 lines
COUNT_ORDERS = ({"count_orders": python("result = {'orders': len(orders['orders'])}", ("orders",))},
                ["load_orders.result -> count_orders.orders"])  # a block whose output fits: {'orders': 100}


def badly_shortened(line: str, before: str, value) -> str:
    """"" when `line` is one line: `before`, how the value starts (as the row writes it: {'orders': [{'id': 1, ), then
    "… (N characters)", N being the length of the value's whole text; else what is wrong with it."""
    start, ending = before + repr(value)[:22], f"… ({len(whole_text(value)):,} characters)"
    if "\n" in line or not line.startswith(start) or not line.endswith(ending):
        return f"{line!r} is not one line that starts {start!r} and ends {ending!r}"
    shown = line[len(before):-len(ending)]
    return "" if repr(value).startswith(shown) else f"{shown!r} is not how the value starts"


@pytest.mark.invariant("NC-8")
def test_the_finished_line_of_a_long_output_ends_with_how_many_characters_it_has_and_one_that_fits_is_shown_whole(
        win, qtbot, tmp_path):
    tab = win.open_flow(orders_flow(tmp_path, LONG, *COUNT_ORDERS))

    run(win, qtbot, tab)

    assert under(tab, "count_orders") == "✓ result: {'orders': 100}"  # it fits: whole, with no ending
    wrong = badly_shortened(under(tab, "load_orders"), "✓ result: ", made_by(LONG))
    assert not wrong, wrong


@pytest.mark.invariant("NC-8")
def test_the_finished_line_taskloom_run_prints_for_a_long_output_ends_with_how_many_characters_it_has(env):
    done = env.taskloom("run", orders_flow(env.root, LONG, *COUNT_ORDERS))

    assert done.returncode == 0, done.stderr[-2000:]
    printed = dict(re.findall(r"^\d\d:\d\d:\d\d \[(\w+)\] (success · .*)$", done.stderr, re.M))
    assert printed.get("count_orders") == "success · result: {'orders': 100}", printed  # it fits: whole
    wrong = badly_shortened(printed["load_orders"], "success · result: ", made_by(LONG))
    assert not wrong, wrong


# load_orders' code: a text of COUNT characters, an order a line (30 characters with its line break).
ORDER_TEXT = 'result = "\\n".join(f"order {i:06d}: pen, ink, paper" for i in range(1, 1001))[:COUNT]'
CARRIED = "load_orders.result -> inspect_orders.value"  # the connection that carries load_orders' result


def hovered_result(tab) -> dict[str, str]:
    """load_orders' result as hovering shows it, by where the mouse rests: on the line under the block, on the block's
    output (the dot on its right) and on the connection that carries the value to inspect_orders. Each hover names
    what it shows on its first line; the value is what follows it (under the block, up to the hover's last line, which
    says when the result was made)."""
    lines = [as_seen(edge.toolTip()) for edge in tab.scene.edge_items]
    shown = {"the block": ("result", re.sub(r"\nresults from [-\d]+ [:\d]+$", "", hover(tab, "load_orders"))),
             "the block's output": ("load_orders.result",
                                    as_seen(tab.scene.nodes["load_orders"].ports["result", True].toolTip())),
             "the connection": (CARRIED, next((text for text in lines if text.startswith(CARRIED)), ""))}
    for where, (name, text) in shown.items():
        assert text.startswith(name + "\n"), f"the hover over {where} does not start with {name!r}: {text[:80]!r}"
    return {where: text[len(name) + 1:] for where, (name, text) in shown.items()}


@pytest.mark.invariant("NC-8")
@pytest.mark.parametrize("code", [LONG, orders_code(4001, ORDER_TEXT)],
                         ids=["a JSON of 33,000 characters", "a text of 4,001 characters"])
def test_hovering_an_output_of_over_4000_characters_shows_its_first_4000_then_how_many_characters_it_has(
        win, qtbot, tmp_path, code):
    value = made_by(code)
    whole = value if isinstance(value, str) else whole_text(value)  # (a text is its own whole text)
    assert len(whole) > 4000, len(whole)
    ending = f"… ({len(whole):,} characters)"
    tab = win.open_flow(orders_flow(tmp_path, code))

    run(win, qtbot, tab)

    wrong = {where: f"{missing(shown[:4000], whole[:4000]) or 'the first 4,000'}, then {shown[4000:4060]!r}"
             for where, shown in hovered_result(tab).items() if shown != whole[:4000] + ending}
    assert not wrong, (f"hovering does not show the first 4,000 of the value's {len(whole):,} characters and then "
                       f"{ending!r}; it shows: {wrong}")


@pytest.mark.invariant("NC-8")
@pytest.mark.parametrize("characters", [4000, 1000], ids=["exactly 4,000 characters", "1,000 characters"])
def test_hovering_an_output_of_4000_characters_or_fewer_shows_it_whole_with_no_ending(win, qtbot, tmp_path,
                                                                                      characters):
    code = orders_code(characters, ORDER_TEXT)
    whole = made_by(code)
    assert len(whole) == characters, len(whole)
    tab = win.open_flow(orders_flow(tmp_path, code))

    run(win, qtbot, tab)

    wrong = {where: missing(shown, whole) for where, shown in hovered_result(tab).items() if shown != whole}
    assert not wrong, f"hovering does not show the value of {characters:,} characters whole, and nothing after: {wrong}"


@pytest.mark.invariant("NC-8")
@pytest.mark.parametrize("environment", [False, True], ids=["in Taskloom's own Python", "in my Python environment"])
def test_an_error_report_says_how_many_characters_each_shortened_input_and_variable_has(win, qtbot, tmp_path,
                                                                                         environment):
    """check_orders gets load_orders' long value as `orders`, makes seen_ids (a set of 9,000 numbers: one line of about
    49,000 characters) and checked (3), then fails. Its report in the Log names its input and its variables, one line
    each."""
    if environment:  # "Run in my Python environment": the environment of the Taskloom folder, made from this Python
        made = subprocess.run([sys.executable, "-m", "taskloom.cli", "env", "create", "--python", sys.executable],
                              capture_output=True, text=True, timeout=300,
                              env={**os.environ, "TASKLOOM_HOME": str(win.home.root)})
        assert made.returncode == 0, made.stdout + made.stderr
    value = made_by(LONG)
    seen_ids = set(range(9000))
    tab = win.open_flow(orders_flow(tmp_path, LONG, {
        "check_orders": python("seen_ids = set(range(9000))\nchecked = 3\nraise ValueError('an order has no price')",
                               ("orders",), (), use_environment=environment),
    }, ["load_orders.result -> check_orders.orders"]))

    run(win, qtbot, tab, ends="failed")

    lines = log_lines(win)
    report = "\n".join(line[:400] for line in lines[-45:])

    def line_of(start: str) -> str:
        """The report's line that starts so (a report that says its variables twice says the same line twice)."""
        found = {line for line in lines if line.startswith(start)}
        assert len(found) == 1, f"{len(found)} lines start {start!r} in the report:\n{report}"
        return found.pop()

    given, ending = line_of("orders ← load_orders.result: "), f"… ({len(whole_text(value)):,} characters)"
    assert given.endswith(ending), f"the input's line {given!r} does not end {ending!r}"
    shown = "".join(given[len("orders ← load_orders.result: "):-len(ending)].split())  # as JSON or as Python writes it
    assert len(shown) >= 20 and any("".join(text.split()).startswith(shown) for text in (whole_text(value), repr(value))), \
        f"{shown!r} is not how the value starts"

    assert "checked = 3" in lines, report  # it fits: whole, with no ending
    ids, ending = line_of("seen_ids = "), f"… ({len(repr(seen_ids)):,} characters)"  # (a set has one text: one line)
    assert ids.startswith("seen_ids = {0, 1, 2, 3, ") and ids.endswith(ending), \
        f"the variable's line {ids!r} does not end {ending!r}"
    assert repr(seen_ids).startswith(ids[len("seen_ids = "):-len(ending)]), ids
    orders = line_of("orders = ")
    assert SHORTENED.search(orders), f"the variable's line {orders!r} does not end … (N characters)"


# --- NC-9: a table in the Log stays a small look at the table ---------------------------------------------------------------

NOTE = ("leave it at the side door, " * 4)[:100]  # a cell of 100 characters
ORDERS_TABLE = f'''
notes = ["ok"] * 12
notes[1] = {NOTE!r}
result = pl.DataFrame({{"id": list(range(1, 13)), "item": [f"pen {{i:02d}}" for i in range(1, 13)], "note": notes}})
'''


@pytest.mark.invariant("NC-9")
def test_a_table_in_the_log_is_its_count_its_first_5_and_last_5_rows_with_cells_cut_at_60_and_the_grid_has_it_whole(
        win, qtbot, tmp_path):
    assert len(NOTE) == 100
    tab = win.open_flow(orders_flow(tmp_path, ORDERS_TABLE))

    run(win, qtbot, tab)

    text = log_text(win)
    assert re.search(r"^\d\d:\d\d:\d\d \[inspect_orders\] INFO 12 rows × 3 columns$", text, re.M), text
    table = text[text.index("12 rows × 3 columns"):]
    assert [f"pen {i:02d}" for i in range(1, 13) if f"pen {i:02d}" in table] == [
        "pen 01", "pen 02", "pen 03", "pen 04", "pen 05", "pen 08", "pen 09", "pen 10", "pen 11", "pen 12"], table
    assert NOTE[:60] + "…" in table and NOTE[:61] not in table, table

    select(tab, "inspect_orders")
    win.bottom_tabs.setCurrentWidget(win.preview)
    QApplication.processEvents()
    model = win.preview.view.model()
    assert win.preview.view.isVisible() and win.preview.label.text() == "inspect_orders.value: 12 rows × 3 columns"
    assert model.rowCount() == 12 and [model.headerData(c, Qt.Horizontal) for c in range(3)][2].startswith("note")
    assert [model.data(model.index(row, 1)) for row in range(12)] == [f"pen {i:02d}" for i in range(1, 13)]
    assert model.data(model.index(1, 2)) == NOTE  # the whole cell


# --- NC-10: the Log keeps the latest 20,000 messages and at most 50 million characters ---------------------------------------

PAGE_LINES = 50_000  # a page: 50,000 lines of 100 characters, about 5 million characters


def open_from_history(win, qtbot, run_id: int):
    """Double-click a run in Run history."""
    win.bottom_tabs.setCurrentWidget(win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: any(table.item(row, 0).text() == str(run_id) for row in range(table.rowCount())),
                    timeout=10_000)
    row = next(row for row in range(table.rowCount()) if table.item(row, 0).text() == str(run_id))
    at = table.visualItemRect(table.item(row, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()


@pytest.mark.invariant("NC-10")
def test_past_50_million_characters_the_oldest_runs_messages_leave_the_log_the_newest_is_whole_and_run_history_has_them_all(
        win, qtbot, tmp_path):
    """Three runs; in each, note_batch logs "batch N received" and load_orders logs four pages of about 5 million
    characters: about 20 million a run, 60 million in all. The last two runs together are 40 million."""
    page = made_by(orders_code(PAGE_LINES, PAGE), "page")
    assert 5_000_000 < len(page(1, 1)) < 5_100_000
    started = time.perf_counter()
    tab = win.open_flow(orders_flow(tmp_path, orders_code(PAGE_LINES, LOG_PAGES), {
        "note_batch": python("log('batch ' + open('batch.txt').read().strip() + ' received')", (), ()),
    }, ["manual.start -> note_batch.after", "note_batch.done -> load_orders.after"]))
    qtbot.waitExposed(win)
    # note_batch is selected while the runs go: the Log shows each run's first and last lines and note_batch's (and
    # has none of the 60 million characters to write).
    select(tab, "note_batch")
    runs = []
    for batch in (1, 2, 3):
        (tmp_path / "batch.txt").write_text(str(batch))
        runs.append(run(win, qtbot, tab))
        print(f"NC-10 measured: batch {batch} (run #{runs[-1]}) had run after {time.perf_counter() - started:.1f} s")

    noted = [line.split(" INFO ")[-1] for line in log_lines(win) if line.endswith(" received")]
    assert noted == ["batch 2 received", "batch 3 received"], f"with note_batch selected the Log has: {noted}"

    win.log.clear_filter.click()  # Show all: every block's messages again
    QApplication.processEvents()
    text = log_text(win)
    first = {(batch, number): f"batch {batch} page {number} order 000001: " in text  # each page's first line
             for batch in (1, 2, 3) for number in (1, 2, 3, 4)}
    assert not first[1, 1] and not first[1, 2], f"after Show all the Log still has the oldest run's first pages: {first}"
    assert all(first[batch, number] for batch in (2, 3) for number in (1, 2, 3, 4)), \
        f"after Show all the Log lacks pages of the last two runs (40 million characters): {first}"

    press_show_all(win)  # the newest message: batch 3's page 4
    assert arrives(qtbot, win, page(3, 4), 60), f"the newest message, after its show all: {missing(box(win), page(3, 4))}"

    open_from_history(win, qtbot, runs[0])  # the oldest run: its messages are in Run history
    qtbot.waitUntil(lambda: "batch 1 received" in log_text(win), timeout=60_000)
    text = log_text(win)
    assert all(f"batch 1 page {number} order 000001: " in text for number in (1, 2, 3, 4)), text[:2000]
    press_show_all(win)  # its last page
    assert arrives(qtbot, win, page(1, 4), 60), f"run #{runs[0]}'s last page, after its show all: " \
                                                f"{missing(box(win), page(1, 4))}"
    print(f"NC-10 measured: the whole test took {time.perf_counter() - started:.1f} s")


MESSAGES = 20_050  # one run's messages: 50 more than the 20,000 the Log keeps
# load_orders' code: it prints one short line for each order of the batch that batch.txt names ("<batch> <orders>").
PACK_ORDERS = '''
batch, count = open("batch.txt").read().split()
for i in range(1, int(count) + 1):
    print(f"batch {batch} order {i:05d} packed")
result = int(count)
'''


def packed(win, batch: int) -> list[int]:
    """The orders of `batch` that the Log says were packed: their numbers, in the Log's order."""
    return [int(line[1]) for line in re.finditer(rf"batch {batch} order (\d{{5}}) packed$", log_text(win), re.M)]


def in_words(orders: list[int]) -> str:
    return f"{len(orders):,} of its messages, order {orders[0]} to order {orders[-1]}" if orders else "none of them"


@pytest.mark.invariant("NC-10")
def test_a_run_of_20050_messages_is_whole_in_the_log_when_it_ends_and_from_run_history_its_oldest_leave_when_the_next_run_starts(
        win, qtbot, tmp_path):
    """The 20,000 messages are a limit on earlier runs' messages, applied when a new run starts: the run that has
    just ended is whole in the Log, and so is a run opened from Run history. No block is selected: the Log shows
    every block's messages."""
    every = list(range(1, MESSAGES + 1))
    started = time.perf_counter()
    tab = win.open_flow(orders_flow(tmp_path, PACK_ORDERS))
    qtbot.waitExposed(win)

    (tmp_path / "batch.txt").write_text(f"1 {MESSAGES}")
    long_run = run(win, qtbot, tab)
    when_it_ended = packed(win, 1)
    print(f"NC-10 measured: a run of {MESSAGES:,} messages had run after {time.perf_counter() - started:.1f} s")

    (tmp_path / "batch.txt").write_text("2 3")  # the next run: 3 messages
    run(win, qtbot, tab)
    earlier, newest = packed(win, 1), packed(win, 2)

    open_from_history(win, qtbot, long_run)
    last = f"batch 1 order {MESSAGES:05d} packed"
    qtbot.waitUntil(lambda: "batch 2 order" not in log_text(win) and last in log_text(win), timeout=60_000)
    from_history = packed(win, 1)
    print(f"NC-10 measured: the whole test took {time.perf_counter() - started:.1f} s")

    # As before: once the next run has started, the earlier run's oldest messages have left and its latest are there
    # (20,000 with the few other lines of the two runs: started, finished, inspect_orders' own).
    assert newest == [1, 2, 3], f"the next run's messages in the Log: {newest}"
    assert earlier == every[-len(earlier):] and 19_990 <= len(earlier) <= 20_000, \
        f"after the next run the Log has, of run #{long_run}: {in_words(earlier)}"
    assert when_it_ended == every and from_history == every, (
        f"run #{long_run} logged {MESSAGES:,} messages, order 1 to order {MESSAGES}. When it ended the Log had "
        f"{in_words(when_it_ended)}; opened from Run history the Log had {in_words(from_history)}")


# --- NC-2: a click on the block that is already selected brings Data preview back to its outputs ---------------------

# load_orders also logs a long message of its own: 300 lines that are not its output.
LONG_AND_CHECKED = LONG + '''
log("\\n".join(f"order {i:04d} checked" for i in range(1, 301)))
'''


@pytest.mark.invariant("NC-2")
@pytest.mark.parametrize("block", ["inspect_orders", "load_orders"])
def test_a_click_on_the_block_that_is_already_selected_brings_data_preview_back_from_its_log_message_to_its_output(
        win, qtbot, tmp_path, block):
    """inspect_orders' long message is the text of its output (the label says which of the two the box holds);
    load_orders' long message is another text (the box changes too)."""
    output = whole_text(made_by(LONG))
    tab = win.open_flow(orders_flow(tmp_path, LONG_AND_CHECKED))
    qtbot.waitExposed(win)
    run(win, qtbot, tab)
    click_on_canvas(tab, block)  # selected: the Log shows its messages, Data preview its output
    assert label(win).startswith(f"{block}."), label(win)
    press_show_all(win)  # its long message
    qtbot.waitUntil(lambda: label(win).startswith(f"Log message of {block}, "), timeout=10_000)
    if block == "load_orders":
        assert box(win).endswith("order 0300 checked") and box(win) != output

    click_on_canvas(tab, block)  # the block that is selected already

    try:
        qtbot.waitUntil(lambda: label(win).startswith(f"{block}."), timeout=3_000)
    except qtbot.TimeoutError:
        pass
    assert label(win).startswith(f"{block}."), \
        f"after a click on {block}, already the selected block, Data preview's label reads {label(win)!r}"
    assert arrives(qtbot, win, output, 10), f"Data preview's box after that click: {missing(box(win), output)}"
