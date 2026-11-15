"""Long output in the Log, Inspect and Data preview (docs/promises/long-output.yaml), rows NC-1, NC-2, NC-3, NC-4 and
NC-6: a long message (over 200 lines or over 20,000 characters) shows its first 200 lines and a "show all" in the Log,
"show all" opens the whole of it in Data preview, Data preview holds the whole text or JSON of a block's output, all of
it stays whole when the run is opened later, and a line over 2,000 characters is folded in Data preview.

Every case is a real run in the real editor: Manual Start -> load_orders (Python Code, makes the value) ->
inspect_orders (Inspect). The tests read what is on screen: the Log's text, Data preview's label and box, the find
bar's count, the clipboard. One case runs the flow from a terminal (`taskloom run`), which prints a long message
whole."""

import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeySequence, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit  # noqa: E402

SHOW_ALL = "· show all"  # how the line under a long message's first 200 lines ends
LAST_WORD = "closing-word"  # only on a text's last line, and in the 5 MB response's last value
FOLDED_WORD = "acrossfold"  # ten characters, placed across characters 1,995 to 2,005 of a long line


# --- the values: each is the code of the Python Code block load_orders, which sets `orders` ------------------------

def json_code(lines: int, characters: int | None = None) -> str:
    """`orders`: a dict whose JSON (as Inspect and Data preview write it, indent 2) has exactly `lines` lines, and
    exactly `characters` characters when given (every line made longer by the z that takes: none is a long line)."""
    code = ('orders = {"orders": [f"order {i:04d} for customer {i:04d}" + "z" * %d for i in range(1, %d)],'
            ' "last": "z" * %d + "end"}')
    count = lines - 5  # the orders, one a line; the other lines are {, "orders": [, ], "last": … and }
    spare = characters - len(said(made(code % (0, count + 1, 0)))) if characters else 0
    return code % (spare // count, count + 1, spare % count)


def text_code(lines: int) -> str:
    """`orders`: a text of `lines` lines; LAST_WORD is only on its last line."""
    return ('orders = "\\n".join([f"order {i:05d} for customer {i:05d}" for i in range(1, %d)]'
            ' + ["the last line says %s"])' % (lines, LAST_WORD))


def line_code(characters: int) -> str:
    """`orders`: a text of one line of `characters` characters, no two stretches of it alike."""
    return 'orders = "".join(f"order {i:05d} " for i in range(1, 5000))[:%d]' % characters


# An API response of about 5 MB: about 4.8 million characters in about 237,000 lines when written with indent 2.
API_RESPONSE = '''
def order(i):
    return {"id": i, "customer": f"customer {i:07d}", "email": f"buyer{i}@example.com", "paid": i % 3 == 0,
            "total": round(i * 1.37, 2), "items": ["paper", "pens", "ink"],
            "address": {"street": f"{i} Main Street", "city": "Springfield", "zip": f"{i % 100000:05d}"}}
orders = {"orders": [order(i) for i in range(13700)], "count": 13700, "last": "LAST_WORD"}
'''.replace("LAST_WORD", LAST_WORD)


def made(code: str, name: str = "orders"):
    """The value the block's code makes: the same code, run here."""
    scope = {}
    exec(code, scope)
    return scope[name]


def said(value) -> str:
    """A value as Inspect writes it in the Log and as Data preview shows it: a text as it is, a dict as JSON."""
    return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)


def more_lines(message: str) -> str:
    """The line the Log shows under a long message's first 200 lines, as the row words it:
    "… 236,565 more lines (4,830,112 characters in all) · show all"."""
    more = len(message.split("\n")) - 200
    return f"… {more:,} more lines ({len(message):,} characters in all) · show all"


# --- the flow and the run ------------------------------------------------------------------------------------------

def orders_flow(folder: Path, code: str, outputs=("orders",), **inspect) -> Path:
    """Manual Start -> load_orders (Python Code: `code`) -> one Inspect block per output (inspect_orders for
    `orders`), with the settings `inspect`."""
    blocks = {"manual": {"type": "trigger.manual"},
              "load_orders": {"type": "logic.python", "config": {"inputs": [], "outputs": list(outputs), "code": code}}}
    edges = ["manual.start -> load_orders.after"]
    for output in outputs:
        blocks[f"inspect_{output}"] = {"type": "logic.inspect", **({"config": inspect} if inspect else {})}
        edges.append(f"load_orders.{output} -> inspect_{output}.value")
    path = folder / "orders.yaml"
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": edges}, sort_keys=False), encoding="utf-8")
    return path


def run_in_editor(win, qtbot, flow: Path):
    """Open the flow, press Run now, wait for the run to end, look at the Log."""
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: bool(tab.runs) and not tab.running and tab.last is not None, timeout=120_000)
    assert tab.last["status"] == "success", (tab.last, log_text(win)[-2000:])
    win.bottom_tabs.setCurrentWidget(win.log)
    QApplication.processEvents()
    return tab


def select(tab, block: str):
    """Click a block on the canvas."""
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)
    QApplication.processEvents()


def wait(qtbot, check, why, within: int = 5_000):
    """Wait until `check()` holds; if it never does, fail saying `why()`."""
    try:
        qtbot.waitUntil(check, timeout=within)
    except qtbot.TimeoutError:
        raise AssertionError(why()) from None


# --- the Log -------------------------------------------------------------------------------------------------------

ENTRY = re.compile(r"(\d\d:\d\d:\d\d) (?:\[(\w+)\] )?[A-Z]+(?: |$)")  # how a line of the Log starts: time [block] LEVEL
RUN_END = re.compile(r"\d\d:\d\d:\d\d DONE run #\d+ ")


@dataclass
class Message:
    time: str  # the HH:MM:SS its line starts with
    lines: list  # its lines as the Log shows them: the first is what follows the level, then the lines under it
    ends: list  # where each of those lines ends in the Log's document


def log_text(win) -> str:
    return win.log.text.toPlainText()


def messages(win, block: str) -> list[Message]:
    """The messages of `block` the Log shows, top to bottom (blank lines under a message's last line left out)."""
    found, at, mine = [], 0, False
    for line in log_text(win).split("\n"):
        start = ENTRY.match(line)
        if start:
            mine = start[2] == block
            if mine:
                found.append(Message(start[1], [line[start.end():]], [at + len(line)]))
        elif mine:
            found[-1].lines.append(line)
            found[-1].ends.append(at + len(line))
        at += len(line) + 1
    for message in found:
        while len(message.lines) > 1 and not message.lines[-1].strip():
            message.lines.pop()
            message.ends.pop()
    return found


def the_message(win, qtbot, block: str = "inspect_orders") -> Message:
    """The one message of `block` in the Log, once the Log shows the run down to its end line."""
    def last_line() -> str:
        return ([""] + [line for line in log_text(win).split("\n") if line.strip()])[-1]

    wait(qtbot, lambda: RUN_END.match(last_line()) is not None,
         lambda: f"the Log does not end with the run's end line but with {last_line()[-200:]!r}", within=30_000)
    found = messages(win, block)
    assert len(found) == 1, f"the Log shows {len(found)} messages of {block}"
    return found[0]


def assert_log_shows(shown: Message, expected: list):
    """The message's lines in the Log are exactly `expected` (the show all line: whatever spaces are around it)."""
    lines = list(shown.lines)
    if lines[-1].rstrip().endswith(SHOW_ALL):
        lines[-1] = lines[-1].strip()
    assert len(lines) == len(expected) and lines[-1] == expected[-1], (
        f"the Log shows {len(lines):,} lines of the message ({sum(map(len, lines)):,} characters), the last one "
        f"{lines[-1][-100:]!r}; it should show {len(expected):,} lines, the last one {expected[-1][-100:]!r}")
    assert lines == expected


def press_show_all(win, qtbot, block: str = "inspect_orders") -> Message:
    """Press "show all" under the long message of `block` in the Log, with the mouse. Gives that message as the Log
    showed it."""
    win.bottom_tabs.setCurrentWidget(win.log)
    QApplication.processEvents()
    shown = the_message(win, qtbot, block)
    assert shown.lines[-1].rstrip().endswith(SHOW_ALL), (
        f'the Log shows no "show all" under the message of {block}: the last of its {len(shown.lines):,} lines '
        f"there is {shown.lines[-1][-100:]!r}")
    edit = win.log.text
    words = edit.document().find("show all", shown.ends[-1] - len(shown.lines[-1]))
    assert not words.isNull() and words.selectionEnd() <= shown.ends[-1], 'the words "show all" are not in the Log'
    middle = QTextCursor(edit.document())
    middle.setPosition(words.selectionStart() + 4)
    for _ in range(2):  # scroll it into the middle of the Log, as with the scroll bars
        rect = edit.cursorRect(middle)
        for bar, off in ((edit.verticalScrollBar(), rect.center().y() - edit.viewport().height() // 2),
                         (edit.horizontalScrollBar(), rect.center().x() - edit.viewport().width() // 2)):
            bar.setValue(bar.value() + off)
        QApplication.processEvents()
    at = edit.cursorRect(middle).center()
    assert edit.viewport().rect().contains(at), '"show all" could not be scrolled into view'
    QTest.mouseClick(edit.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()
    return shown


# --- Data preview, Find, copy --------------------------------------------------------------------------------------

def label(win) -> str:
    """Data preview's label as it reads."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", win.preview.label.text()))).strip()


def box(win) -> str:
    """What Data preview's text box holds ("" while it does not show)."""
    return win.preview.text.toPlainText() if win.preview.text.isVisible() else ""


def assert_box_holds(win, qtbot, whole: str, within: int = 10_000):
    """Data preview's box comes to hold exactly `whole`, to its last character."""
    def why() -> str:
        got = box(win)
        return (f"Data preview's box holds {len(got):,} characters ending {got[-40:]!r}; the whole text has "
                f"{len(whole):,} characters and ends {whole[-40:]!r} (the label reads {label(win)!r})")

    wait(qtbot, lambda: win.preview.text.document().characterCount() > len(whole) and box(win) == whole, why, within)


def assert_show_all_opened(win, qtbot, shown: Message, block: str, message: str, within: int = 10_000):
    """After "show all": Data preview is at the front, its label names the Log message with the time the Log shows for
    it and its number of lines, and the box holds the whole message."""
    wait(qtbot, lambda: win.bottom_tabs.currentWidget() is win.preview,
         lambda: 'pressing "show all" did not bring Data preview to the front')
    lines = len(message.split("\n"))
    assert label(win) == f"Log message of {block}, {shown.time}: {lines:,} lines"
    assert_box_holds(win, qtbot, message, within)


def visible(text: str) -> str:
    """`text` as a reader sees it: without the characters that are never drawn (zero-width marks)."""
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def focus(win, qtbot, view):
    """As when you click into `view`: the keyboard focus is in it."""
    assert view.isVisible(), f"{type(view).__name__} does not show"
    qtbot.waitExposed(win)
    win.activateWindow()
    view.setFocus()
    qtbot.waitUntil(view.hasFocus, timeout=5_000)


def copy_all(win, qtbot, view) -> tuple[str, float]:
    """Click into `view`, select all and copy (Ctrl+A, Ctrl+C): what the clipboard then holds, and the seconds the two
    keys took."""
    focus(win, qtbot, view)
    QApplication.clipboard().setText("")
    began = time.monotonic()
    QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)
    copied = QApplication.clipboard().text()
    return copied, time.monotonic() - began


def find(win, qtbot, view, word: str) -> str:
    """Click into `view`, press Ctrl+F and type `word`: what the find bar's count then shows ("1/1", "0 results")."""
    focus(win, qtbot, view)
    QTest.keySequence(view, QKeySequence(QKeySequence.Find))
    QApplication.processEvents()
    field = QApplication.focusWidget()
    assert isinstance(field, QLineEdit), "Ctrl+F opened no find bar"
    field.selectAll()
    QTest.keyClicks(field, word)

    def count() -> str:
        texts = [re.sub(r"\s*/\s*", "/", " ".join(shown.text().split()))
                 for shown in field.parentWidget().findChildren(QLabel) if shown.isVisible()]
        return next((text for text in texts if re.fullmatch(r"\d+/\d+|0 results", text)), "")

    try:
        qtbot.waitUntil(lambda: re.fullmatch(r"[1-9]\d*/\d+", count()) is not None, timeout=5_000)
    except qtbot.TimeoutError:
        pass
    return count()


def show_in_data_preview(win, tab, block: str = "load_orders"):
    """Click the block, then the Data preview tab."""
    select(tab, block)
    win.bottom_tabs.setCurrentWidget(win.preview)
    QApplication.processEvents()


# --- NC-1: a long message in the Log shows its first 200 lines and a "show all" ------------------------------------

@pytest.mark.invariant("NC-1")
def test_a_json_of_150_lines_and_9000_characters_is_whole_in_the_log_to_its_closing_brace(win, qtbot, tmp_path):
    code = json_code(lines=150, characters=9_000)
    message = said(made(code))
    assert (len(message.split("\n")), len(message), message[-1]) == (150, 9_000, "}")

    run_in_editor(win, qtbot, orders_flow(tmp_path, code))

    assert_log_shows(the_message(win, qtbot), message.split("\n"))
    assert "show all" not in log_text(win)


@pytest.mark.invariant("NC-1")
def test_a_message_of_exactly_200_lines_and_exactly_20000_characters_is_not_long_and_is_whole_in_the_log(win, qtbot,
                                                                                                         tmp_path):
    code = json_code(lines=200, characters=20_000)
    message = said(made(code))
    assert (len(message.split("\n")), len(message)) == (200, 20_000)

    run_in_editor(win, qtbot, orders_flow(tmp_path, code))

    assert_log_shows(the_message(win, qtbot), message.split("\n"))
    assert "show all" not in log_text(win)


@pytest.mark.invariant("NC-1")
def test_one_line_of_exactly_20000_characters_is_not_long_and_is_whole_in_the_log(win, qtbot, tmp_path):
    code = line_code(20_000)
    message = made(code)
    assert len(message) == 20_000 and "\n" not in message

    run_in_editor(win, qtbot, orders_flow(tmp_path, code))

    shown = visible("".join(the_message(win, qtbot).lines))  # (however the Log lays one long line out)
    assert len(shown) == 20_000, f"the Log shows {len(shown):,} of the line's 20,000 characters"
    assert shown == message
    assert "show all" not in log_text(win)


@pytest.mark.invariant("NC-1")
def test_a_message_of_201_lines_is_long_the_log_shows_its_first_200_lines_then_show_all(win, qtbot, tmp_path):
    code = json_code(lines=201)
    message = said(made(code))
    lines = message.split("\n")
    assert len(lines) == 201 and len(message) < 20_000

    run_in_editor(win, qtbot, orders_flow(tmp_path, code))

    shown = the_message(win, qtbot)
    under = shown.lines[-1].strip()  # (how it says "1 more line" is not agreed)
    assert under.endswith(f"({len(message):,} characters in all) {SHOW_ALL}"), (
        f"under the message the Log shows no line that ends \"({len(message):,} characters in all) {SHOW_ALL}\" but "
        f"{under[-100:]!r}")
    assert shown.lines[:-1] == lines[:200]


@pytest.mark.invariant("NC-1")
@pytest.mark.parametrize("code, inspect", [(json_code(lines=250), {}), (text_code(lines=250), {}),
                                           (text_code(lines=250), {"message": "{value}"})],
                         ids=["a dict", "a text", "a text written by the Inspect block's message"])
def test_a_long_message_shows_its_first_200_lines_then_how_many_more_there_are_and_show_all(win, qtbot, tmp_path, code,
                                                                                            inspect):
    message = said(made(code))
    lines = message.split("\n")
    assert len(lines) == 250 and len("\n".join(lines[:200])) < 20_000

    run_in_editor(win, qtbot, orders_flow(tmp_path, code, **inspect))

    # Its first 200 lines, the line that says how much more there is, and nothing of the rest.
    assert more_lines(message) == f"… 50 more lines ({len(message):,} characters in all) · show all"
    assert_log_shows(the_message(win, qtbot), lines[:200] + [more_lines(message)])
    assert lines[200] not in log_text(win)


@pytest.mark.invariant("NC-1")
@pytest.mark.invariant("NC-2")
def test_inspect_on_a_5_mb_api_response_shows_its_first_200_lines_and_show_all_opens_all_of_it(win, qtbot, tmp_path):
    """The rows' own example: "… 236,565 more lines (4,830,112 characters in all) · show all" in the Log, then
    "Log message of inspect_orders, 09:02:41: 236,765 lines" over the whole of it, with this response's numbers."""
    message = said(made(API_RESPONSE))
    lines = message.split("\n")
    assert 4_500_000 < len(message) < 5_500_000 and len(lines) > 200_000 and message[-1] == "}"

    run_in_editor(win, qtbot, orders_flow(tmp_path, API_RESPONSE))

    assert_log_shows(the_message(win, qtbot), lines[:200] + [more_lines(message)])
    assert "customer 0000100" not in log_text(win) and LAST_WORD not in log_text(win)  # nothing of the rest
    shown = press_show_all(win, qtbot)
    assert_show_all_opened(win, qtbot, shown, "inspect_orders", message, within=30_000)
    assert box(win).endswith(f'"last": "{LAST_WORD}"\n}}')


@pytest.mark.invariant("NC-1")
@pytest.mark.parametrize("characters", [25_000, 20_001], ids=["25,000 characters", "20,001 characters"])
def test_a_message_of_one_very_long_line_shows_its_first_20000_characters_then_show_all(win, qtbot, tmp_path,
                                                                                        characters):
    code = line_code(characters)
    message = made(code)
    assert len(message) == characters and "\n" not in message

    run_in_editor(win, qtbot, orders_flow(tmp_path, code))

    shown = the_message(win, qtbot)
    under = shown.lines[-1].strip()
    assert under.endswith(SHOW_ALL) and f"{characters:,} characters in all" in under, (
        f'under the line the Log shows no line that ends "{SHOW_ALL}" and says {characters:,} characters in all, but '
        f"{under[-100:]!r}")
    first = visible("".join(shown.lines[:-1]))  # (however the Log lays one long line out)
    assert len(first) == 20_000, f"the Log shows {len(first):,} characters of the line, not its first 20,000"
    assert first == message[:20_000]


@pytest.mark.invariant("NC-1")
def test_a_long_message_drawn_as_markdown_shows_its_first_200_lines_drawn_then_show_all(win, qtbot, tmp_path):
    code = 'orders = "\\n".join(f"- order {i:04d} for customer {i:04d}" for i in range(1, 251))'
    items = [line[2:] for line in made(code).split("\n")]  # a list of 250 items, one a line

    run_in_editor(win, qtbot, orders_flow(tmp_path, code, markdown=True))

    drawn = [line.strip() for line in the_message(win, qtbot).lines if line.strip()]
    orders = [line for line in drawn if "order" in line]
    assert orders == items[:200], (
        f"the Log draws {len(orders)} items of the list, the last one {orders[-1] if orders else ''!r}; it should "
        f"draw its first 200 lines, each as a list item (without its dash), the last one {items[199]!r}")
    assert drawn[-1].endswith(SHOW_ALL) and drawn.index(items[199]) == len(drawn) - 2, (
        f'under the 200 items the Log shows no line that ends "{SHOW_ALL}" but {drawn[-1][-100:]!r}')


@pytest.mark.invariant("NC-1")
def test_taskloom_run_in_a_terminal_prints_the_whole_of_a_long_inspect_message_to_its_last_character(env):
    code = json_code(lines=250)
    message = said(made(code))

    result = env.taskloom("run", orders_flow(env.root, code))

    assert result.returncode == 0, result.stderr
    printed = result.stdout + result.stderr  # (the terminal shows both)
    assert message in printed, (
        f"the terminal printed {len(printed):,} characters ending {printed[-120:]!r}: the message's "
        f"{len(message):,} characters are not all there")


# --- NC-2: "show all" opens the whole message in Data preview ------------------------------------------------------

@pytest.mark.invariant("NC-2")
def test_show_all_brings_data_preview_to_the_front_with_the_whole_message_and_a_selected_block_gets_it_back(
        win, qtbot, tmp_path):
    code = json_code(lines=250)
    message = said(made(code))
    tab = run_in_editor(win, qtbot, orders_flow(tmp_path, code))

    shown = press_show_all(win, qtbot)

    # Data preview at the front, "Log message of inspect_orders, HH:MM:SS: 250 lines", the whole message in the box.
    assert_show_all_opened(win, qtbot, shown, "inspect_orders", message)
    assert box(win)[-1] == "}"
    select(tab, "load_orders")
    assert label(win).startswith("load_orders.orders") and "Log message" not in label(win), label(win)
    assert_box_holds(win, qtbot, message)  # that block's output again (the same JSON: the label says whose it is)


@pytest.mark.invariant("NC-2")
def test_with_two_long_messages_in_the_log_each_show_all_opens_its_own(win, qtbot, tmp_path):
    returns_code = text_code(lines=300).replace("orders = ", "returns = ").replace("order ", "return ")
    code = json_code(lines=250) + "\n" + returns_code
    orders, returns = said(made(code, "orders")), made(code, "returns")
    assert len(returns.split("\n")) == 300 and "return 00001" in returns and "return 00001" not in orders
    tab = run_in_editor(win, qtbot, orders_flow(tmp_path, code, outputs=("orders", "returns")))

    shown = press_show_all(win, qtbot, "inspect_returns")
    assert_show_all_opened(win, qtbot, shown, "inspect_returns", returns)

    shown = press_show_all(win, qtbot, "inspect_orders")
    assert_show_all_opened(win, qtbot, shown, "inspect_orders", orders)

    shown = press_show_all(win, qtbot, "inspect_returns")
    assert_show_all_opened(win, qtbot, shown, "inspect_returns", returns)

    select(tab, "load_orders")  # the block's own outputs again: the one its label names
    output = re.match(r"load_orders\.(orders|returns)\b", label(win))
    assert output, f"with load_orders selected, Data preview's label reads {label(win)!r}"
    assert_box_holds(win, qtbot, {"orders": orders, "returns": returns}[output[1]])


# --- NC-3: Data preview shows the whole text or JSON of a block's output -------------------------------------------

@pytest.mark.invariant("NC-3")
@pytest.mark.parametrize("code", [json_code(lines=250), text_code(lines=250)], ids=["a dict", "a text"])
def test_data_preview_holds_the_whole_output_of_the_block_that_made_it_to_its_last_character(win, qtbot, tmp_path,
                                                                                            code):
    whole = said(made(code))
    assert len(whole) > 7_000 and whole.endswith(("}", LAST_WORD))
    tab = run_in_editor(win, qtbot, orders_flow(tmp_path, code))

    show_in_data_preview(win, tab, "load_orders")

    assert label(win).startswith("load_orders.orders"), label(win)
    assert_box_holds(win, qtbot, whole)


@pytest.mark.invariant("NC-3")
def test_selecting_the_block_that_made_a_5_mb_response_shows_all_of_it_down_to_the_closing_brace(win, qtbot, tmp_path):
    whole = said(made(API_RESPONSE))
    tab = run_in_editor(win, qtbot, orders_flow(tmp_path, API_RESPONSE))

    show_in_data_preview(win, tab, "load_orders")

    assert_box_holds(win, qtbot, whole, within=30_000)
    edit = win.preview.text  # scroll to the end: the closing } is there
    edit.verticalScrollBar().setValue(edit.verticalScrollBar().maximum())
    QApplication.processEvents()
    end = QTextCursor(edit.document())
    end.movePosition(QTextCursor.End)
    assert end.block().text() == "}" and edit.viewport().rect().intersects(edit.cursorRect(end))


# --- NC-4: it stays whole afterwards -------------------------------------------------------------------------------

@pytest.mark.invariant("NC-4")
def test_a_run_opened_later_from_run_history_shows_the_same_200_lines_and_show_all_still_opens_all_of_it(win, qtbot,
                                                                                                         tmp_path):
    """The run ran elsewhere (as a scheduled one does): this editor has it from the run history only."""
    code = json_code(lines=250)
    message = said(made(code))
    lines = message.split("\n")
    flow = orders_flow(tmp_path, code)
    done = subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow)], cwd=tmp_path, capture_output=True,
                          text=True, timeout=120, env={**os.environ, "TASKLOOM_HOME": str(win.home.root)})
    assert done.returncode == 0, done.stderr
    tab = win.open_flow(flow)
    win.bottom_tabs.setCurrentWidget(win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 1, timeout=10_000)
    at = table.visualItemRect(table.item(0, 0)).center()

    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # open that run
    QApplication.processEvents()

    assert_log_shows(the_message(win, qtbot), lines[:200] + [more_lines(message)])
    shown = press_show_all(win, qtbot)
    assert_show_all_opened(win, qtbot, shown, "inspect_orders", message)
    show_in_data_preview(win, tab, "load_orders")  # and the block that made it: all of it
    assert label(win).startswith("load_orders.orders"), label(win)
    assert_box_holds(win, qtbot, message)


@pytest.mark.invariant("NC-4")
@pytest.mark.parametrize("opened_by", ["selecting the block that made it", "show all in the Log"])
def test_in_data_preview_select_all_and_copy_gives_the_whole_text_and_find_finds_a_word_on_its_last_line(
        win, qtbot, tmp_path, opened_by):
    code = text_code(lines=250)
    whole = made(code)
    assert whole.count(LAST_WORD) == 1 and LAST_WORD in whole.split("\n")[-1]
    tab = run_in_editor(win, qtbot, orders_flow(tmp_path, code))
    if opened_by == "show all in the Log":
        press_show_all(win, qtbot)
    else:
        show_in_data_preview(win, tab, "load_orders")
    assert_box_holds(win, qtbot, whole)

    copied, _ = copy_all(win, qtbot, win.preview.text)
    found = find(win, qtbot, win.preview.text, LAST_WORD)

    assert len(copied) == len(whole), f"{len(copied):,} of the text's {len(whole):,} characters were copied"
    assert copied == whole
    assert found == "1/1", f"Find shows {found!r} for a word that is on the last line"


@pytest.mark.invariant("NC-4")
def test_select_all_and_copy_in_the_log_with_a_long_message_takes_under_2_seconds_and_gives_what_the_log_shows(
        win, qtbot, tmp_path):
    """A message of 8,000 lines, written by the Inspect block's own message."""
    code = 'orders = "\\n".join(f"order {i:05d} for customer {i:05d}" for i in range(1, 8001))'
    run_in_editor(win, qtbot, orders_flow(tmp_path, code, message="{value}"))
    assert the_message(win, qtbot).lines[0] == "order 00001 for customer 00001"

    copied, took = copy_all(win, qtbot, win.log.text)

    assert took < 2, f"select all and copy in the Log took {took:.1f} seconds"
    assert "order 00001 for customer 00001" in copied
    assert visible(copied).splitlines() == visible(log_text(win)).splitlines()


# --- NC-6: a very long line is folded in Data preview --------------------------------------------------------------

@pytest.mark.invariant("NC-6")
def test_a_line_of_5000_characters_is_shown_as_lines_of_2000_copied_back_as_one_line_and_found_across_a_fold(
        win, qtbot, tmp_path):
    code = line_code(5_000) + f'\norders = orders[:1995] + "{FOLDED_WORD}" + orders[2005:]'
    line = made(code)
    assert len(line) == 5_000 and "\n" not in line and line[1995:2005] == FOLDED_WORD and line.count(FOLDED_WORD) == 1
    tab = run_in_editor(win, qtbot, orders_flow(tmp_path, code))

    show_in_data_preview(win, tab, "load_orders")

    def lines() -> list:
        return visible(box(win)).split("\n")

    wait(qtbot, lambda: [len(shown) for shown in lines()] == [2_000, 2_000, 1_000],
         lambda: f"Data preview shows the line of 5,000 characters as lines of {[len(shown) for shown in lines()]} "
                 f"characters, not of 2,000, 2,000 and 1,000")
    assert "".join(lines()) == line
    copied, _ = copy_all(win, qtbot, win.preview.text)
    assert "\n" not in copied and len(copied) == 5_000, (
        f"copied: {len(copied):,} characters in {len(copied.splitlines())} lines, not the one line of 5,000")
    assert copied == line
    found = find(win, qtbot, win.preview.text, FOLDED_WORD)
    assert found == "1/1", f"Find shows {found!r} for a word across characters 1,995 to 2,005"


@pytest.mark.invariant("NC-6")
def test_lines_of_up_to_2000_characters_are_shown_in_data_preview_as_they_are(win, qtbot, tmp_path):
    code = (line_code(4_499) + '\norders = orders[:2000] + "\\n" + orders[2000:3999] + "\\n" + orders[3999:]')
    text = made(code)
    assert [len(line) for line in text.split("\n")] == [2_000, 1_999, 500]
    tab = run_in_editor(win, qtbot, orders_flow(tmp_path, code))

    show_in_data_preview(win, tab, "load_orders")

    assert_box_holds(win, qtbot, text)
    assert [len(line) for line in box(win).split("\n")] == [2_000, 1_999, 500]
