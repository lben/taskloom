"""Proof: a long output is whole on screen. The Log shows a long message's first 200 lines and a “show all” that
opens all of it in Data preview; a message that is not long is whole in the Log; a very long line is folded in Data
preview; a one-line label says when it is shortened; a table in the Log stays a small look. See README.md here."""

import os
import re
import subprocess
import sys

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

from proofs.test_proof_failure_inputs import finished, in_the_log, open_flow, press_run_now, scroll_log_to
from proofs.test_proof_html_template_formatting import click_block
from proofs.test_proof_preview_long_error import click_tab, in_window
from proofs.test_proof_result_time import hover
from proofs.test_proof_retention_per_start import drag_up
from tests.test_long_output_log import (API_RESPONSE, FOLDED_WORD, LAST_WORD, box, copy_all, find, json_code, label,
                                        line_code, made, more_lines, press_show_all, said, the_message, visible)
from tests.test_long_output_rest import NOTE, ORDERS_TABLE, Heartbeat, arrives, as_seen, made_by

CHOSEN = ("The lighter choice: the Log shows a long message’s first 200 lines and a “show all” that opens the whole "
          "value in Data preview.")
ASKED = ("(A summary of what you reported, not your exact words.) The Log cut a long output off, also when an Inspect "
         "block wrote it, and the JSON of an API's response was cut. (Your exact words, when you chose how to show "
         f"it:) {CHOSEN}")
SMALL_LOOK = "keep the table a small look"  # the user's words about a table in the Log
# load_orders' code in the proof of the labels: a text of 500 lines of 125 characters, one order a line.
ORDER_LINES = ('result = "\\n".join(f"order {i:05d} for customer {i:05d}: pens, ink, paper, envelopes, stamps, '
               'folders, labels, tape, glue, staples, clips, pads, markers" for i in range(1, 501))')
BEFORE = ("Before, a text or a JSON was cut after its first 4,000 characters on its way to the Log, to Inspect and to "
          "Data preview, and nothing said so. The rest was nowhere on screen.")


def orders_flow(code: str, output: str) -> dict:
    """start -> load_orders (Python Code: `code`, which makes `output`) -> inspect_orders (Inspect)."""
    return {"blocks": {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
                       "load_orders": {"type": "logic.python", "ui": {"x": 260, "y": 0},
                                       "config": {"inputs": [], "outputs": [output], "code": code}},
                       "inspect_orders": {"type": "logic.inspect", "ui": {"x": 520, "y": 0}}},
            "edges": ["start.start -> load_orders.after", f"load_orders.{output} -> inspect_orders.value"]}


def log_text(win) -> str:
    return win.log.text.toPlainText()


def end_of_the_box(win, lines: int, width: int = 700) -> QRect:
    """The last `lines` lines of Data preview's box, in the window's pixels, `width` wide."""
    edit = win.preview.text
    end = QTextCursor(edit.document())
    end.movePosition(QTextCursor.End)
    rect = edit.cursorRect(end)
    end.movePosition(QTextCursor.PreviousBlock, QTextCursor.MoveAnchor, lines - 1)
    rect |= edit.cursorRect(end)
    rect = QRect(0, rect.top(), width, rect.height()) & edit.viewport().rect()
    assert not rect.isEmpty(), "the end of the box is out of view"
    return rect.translated(edit.viewport().mapTo(win, QPoint(0, 0)))


def the_tab(win, text: str) -> QRect:
    """The tab that reads `text` under the canvas, in the window's pixels (just inside it: tabs touch)."""
    bar = win.bottom_tabs.tabBar()
    rect = bar.tabRect(next(i for i in range(bar.count()) if bar.tabText(i) == text))
    return QRect(bar.mapTo(win, rect.topLeft()), rect.size()).adjusted(4, 4, -4, -3)


def the_line(win) -> QRect:
    """Data preview's line above its box, in the window's pixels: as wide as its words."""
    shown = win.preview.label
    return QRect(shown.mapTo(win, QPoint(0, 2)),
                 QSize(shown.fontMetrics().horizontalAdvance(label(win)) + 14, shown.height() - 4))


def lines_of_the_log(win, run: int, texts: list, width: int) -> QRect:
    """From the first to the last of the Log's lines that show `texts`, in the window's pixels: `width` wide."""
    rect = in_the_log(win, run, texts)
    return QRect(rect.left(), rect.top(), width, rect.height())


def marked(win, text: str) -> QRect:
    """Where Data preview's box shows `text` first, in the window's pixels."""
    edit = win.preview.text
    found = edit.document().find(text)
    assert not found.isNull(), f"the box does not show {text!r}"
    start, end = QTextCursor(found), QTextCursor(found)
    start.setPosition(found.selectionStart())
    end.setPosition(found.selectionEnd())
    rect = (edit.cursorRect(start) | edit.cursorRect(end)) & edit.viewport().rect()
    assert not rect.isEmpty(), f"{text!r} is out of view in the box"
    return rect.translated(edit.viewport().mapTo(win, QPoint(0, 0)))


def top_of(win, widget, height: int, width: int = 700) -> QRect:
    """The top of a widget, in the window's pixels: `width` wide and `height` high (for a close-up)."""
    return in_window(win, widget, width).adjusted(0, 0, 0, height - widget.height())


def test_a_long_message_shows_its_first_200_lines_in_the_log_and_show_all_opens_all_of_it(proof, win, qtbot, tmp_path,
                                                                                         monkeypatch):
    message = said(made(API_RESPONSE))  # what Inspect writes: the JSON, with an indent of 2
    lines = message.split("\n")
    more = more_lines(message)
    size = f"{len(message):,} characters in {len(lines):,} lines"

    p = proof("A long message: the Log shows its first 200 lines, and “show all” opens all of it in Data preview",
              ASKED)
    p.idea("A message of more than 200 lines, or more than 20,000 characters, is long. The Log shows a long message's "
           f"first 200 lines, then one line that says how much more there is: “{more}”.")
    p.idea("Click “show all” at the end of that line. Data preview comes to the front with the whole message, down to "
           "its last character, under a line that says which message it is: “Log message of inspect_orders”, the time "
           f"the Log shows for it, and “{len(lines):,} lines”. There you scroll it, find in it (Ctrl+F) and copy it.")
    p.idea(BEFORE + " An API's answer of 5 MB showed its first 4,000 characters, with Inspect too.")
    p.why_simplest("Data preview's box already showed a text, with Find and copy. “show all” puts the message in that "
                   "same box: no new window, nothing new to learn. The Log holds only the 200 lines, however big the "
                   "message is, so it scrolls and copies as fast as before.")
    p.why_simplest("The 5 MB go into the box 200,000 characters at a time, and the window answers your clicks and keys "
                   "between two pieces. So nothing freezes while a big answer arrives.")
    p.alternative("Show the whole message in the Log itself",
                  "This was the other choice you had. The Log is one box with the messages of every run you look at: "
                  "with 5 MB in it for each such message it gets slow to scroll and to copy from. You chose the "
                  "lighter way.")
    p.alternative("Open the whole message in a window of its own",
                  "One more window to place and to close. Data preview is already there under the canvas, with Find "
                  "and copy.")
    p.fits("Click a block, and Data preview shows that block's outputs again, as always. The block's own output is "
           "whole there too.")
    p.fits("A run you open later from Run history shows the same 200 lines, and “show all” still opens all of it. In "
           "a terminal, taskloom run prints a long message whole.")

    p.step("Open orders.yaml (File > Open…) and press ▶ Run now. load_orders gets an API's answer: 13,700 orders, "
           f"about 5 MB. inspect_orders, an Inspect block, writes it in the Log: as JSON, {size}. Scroll the Log up to "
           "where that message starts.")
    tab = open_flow(win, monkeypatch, tmp_path / "orders.yaml", orders_flow(API_RESPONSE, "orders"))
    with Heartbeat() as running:  # how long the window does not answer, as the NC-5 tests measure it
        press_run_now(win)
        run = finished(qtbot, tab, 1)
    shown = the_message(win, qtbot)
    scroll_log_to(win, run, "[inspect_orders] INFO")
    p.shot(win, "① inspect_orders ran. ② Its message in the Log starts with {, then the first order.",
           highlight=["inspect_orders", win.log.text], badges=True, zoom=[top_of(win, win.log.text, 140)])
    p.check(f"The run succeeded, and the message inspect_orders wrote has {size}",
            tab.runs[0].status == "success" and len(message) > 4_500_000 and len(lines) > 200_000)

    p.step("Scroll the Log down to its end.")
    bar = win.log.text.verticalScrollBar()
    bar.setValue(bar.maximum())
    QTest.qWait(100)
    p.shot(win, f"After the message's first 200 lines, the outlined line: “{more}”. Under it, the run's last line.",
           highlight=[(win.log.text, more)], zoom=[in_the_log(win, run, [lines[198].strip(), more, "DONE run"],
                                                             upto=more)])
    p.check("The Log shows the message's first 200 lines, as written",
            shown.lines[:-1] == lines[:200])
    p.check(f"Then the line “{more}”", shown.lines[-1].strip() == more)
    p.check("Nothing of the rest is in the Log: not customer 0000100, not the last line",
            "customer 0000100" not in log_text(win) and LAST_WORD not in log_text(win))

    p.step("Click “show all”, at the end of that line.")
    with Heartbeat() as opening:
        press_show_all(win, qtbot)
        arrived = arrives(qtbot, win, message, 20)
    preview = win.preview
    reads = f"Log message of inspect_orders, {shown.time}: {len(lines):,} lines"
    p.shot(win, f"① Data preview came to the front. ② Its line reads “{reads}”. Under it, the box holds the "
                "message, from its first line.",
           highlight=[the_tab(win, "Data preview"), the_line(win)], badges=True,
           zoom=[the_tab(win, "Data preview"), in_window(win, preview.label, 700), top_of(win, preview.text, 120)])
    p.check("① Data preview is at the front", win.bottom_tabs.currentWidget() is preview)
    p.check(f"② Its line reads “{reads}”", label(win) == reads)
    p.check(f"The box holds the whole message: all {len(message):,} characters, the same as load_orders made",
            arrived and box(win) == message)

    p.step("Drag the bar at the right of the box all the way down, to the end of the message.")
    bar = preview.text.verticalScrollBar()
    bar.setValue(bar.maximum())
    QTest.qWait(100)
    last = end_of_the_box(win, 3)
    p.shot(win, "The end of the message, in the box: “count”: 13700, “last”: “closing-word”, and the closing }. "
                "Nothing is cut.", highlight=[last], zoom=[end_of_the_box(win, 8)])
    end = QTextCursor(preview.text.document())
    end.movePosition(QTextCursor.End)
    p.check("The last line of the box is the closing }, and it is on screen",
            end.block().text() == "}" and preview.text.viewport().rect().intersects(preview.text.cursorRect(end)))
    p.check("The line before it is “last”: “closing-word”, the last value of the answer",
            box(win).endswith(f'"last": "{LAST_WORD}"\n}}'))

    p.note("How long the window took, measured during this proof on this computer. “Did not answer” is the longest "
           "time a click or a key would have waited.")
    p.check(f"While the flow ran ({running.took:.1f} seconds), the window never failed to answer for more than "
            f"1 second: {running.longest:.2f} seconds at most", running.longest <= 1)
    p.check(f"After “show all”, all of it was in Data preview after {opening.took:.1f} seconds (the limit is 15), and "
            f"the window never failed to answer for more than 1 second: {opening.longest:.2f} seconds at most",
            arrived and opening.took <= 15 and opening.longest <= 1)
    for each in win.tabs_list():  # nothing to save when the window closes
        each.doc.dirty = False


def test_a_message_that_is_not_long_is_whole_in_the_log(proof, win, qtbot, tmp_path, monkeypatch):
    code = json_code(lines=150, characters=9_000)
    message = said(made(code))
    lines = message.split("\n")

    p = proof("A message that is not long is whole in the Log", ASKED)
    p.idea("A message of up to 200 lines and up to 20,000 characters is not long: the Log shows all of it, as "
           "written. A JSON of 150 lines and 9,000 characters is there from its { to its closing }.")
    p.idea(BEFORE + " This JSON lost more than half of itself.")
    p.why_simplest("One rule decides, the same for every message: over 200 lines or over 20,000 characters, the Log "
                   "shows the start and “show all”; up to that, all of it. There is nothing to set.")
    p.alternative("Keep the cut at 4,000 characters, and add “show all” there",
                  "A small JSON or a short list, which is most of what you look at with Inspect, would need one more "
                  "click to be read. Up to 200 lines the Log shows it where you already are.")
    p.fits("It is the Log as you know it: the message's lines as written, to scroll, find in and copy.")

    p.step("Open orders.yaml (File > Open…) and press ▶ Run now. load_orders gives 145 orders; inspect_orders writes "
           "them in the Log: as JSON, 150 lines and 9,000 characters. Scroll the Log up to where that message starts.")
    tab = open_flow(win, monkeypatch, tmp_path / "orders.yaml", orders_flow(code, "orders"))
    press_run_now(win)
    run = finished(qtbot, tab, 1)
    shown = the_message(win, qtbot)
    scroll_log_to(win, run, "[inspect_orders] INFO")
    p.shot(win, "① inspect_orders ran. ② Its message in the Log starts with {, then the first orders.",
           highlight=["inspect_orders", win.log.text], badges=True, zoom=[top_of(win, win.log.text, 140)])
    p.check("The message inspect_orders wrote has 150 lines and 9,000 characters, and ends with }",
            tab.runs[0].status == "success" and (len(lines), len(message), message[-1]) == (150, 9_000, "}"))

    p.step("Scroll the Log down to its end.")
    bar = win.log.text.verticalScrollBar()
    bar.setValue(bar.maximum())
    QTest.qWait(100)
    p.shot(win, "The end of the message: its last orders, “last”, and the closing }. Then the run's last line. "
                "There is no “show all”: nothing was left out.",
           highlight=[lines_of_the_log(win, run, ['"last": "', "}"], 560).adjusted(3, 5, -3, -5)],  # hugs them
           zoom=[lines_of_the_log(win, run, ["order 0141 for customer 0141", "DONE run"], 600)])
    p.check("The Log shows all 150 lines of the message, as written, down to the closing }",
            shown.lines == lines and shown.lines[-1] == "}")
    p.check("The Log has no “show all”", "show all" not in log_text(win))
    for each in win.tabs_list():
        each.doc.dirty = False


def test_a_very_long_line_is_folded_in_data_preview_and_copied_and_found_as_one_line(proof, win, qtbot, tmp_path,
                                                                                    monkeypatch):
    code = line_code(5_000) + f'\norders = orders[:1995] + "{FOLDED_WORD}" + orders[2005:]'
    text = made(code)

    p = proof("A very long line is folded in Data preview, and is still one line when you copy it or find in it",
              ASKED)
    p.idea("Data preview shows a line of more than 2,000 characters as lines of 2,000. A JSON that an API prints on "
           "one line, 5,000 characters here, shows as three lines: 2,000, 2,000 and 1,000.")
    p.idea("It is still one line. Copy gives it back as one line of 5,000 characters. Find finds a word that a fold "
           f"splits in two: “{FOLDED_WORD}” starts 5 characters before the first fold and ends 5 after it.")
    p.idea(BEFORE + " Of this line, the last 1,000 characters were missing.")
    p.why_simplest("The box draws a line whole each time it paints, and cannot scroll to the end of a very long one. "
                   "Lines of 2,000 are short enough to draw and to scroll, in the same box. The box remembers where "
                   "it folded, and Copy and Find leave those folds out.")
    p.alternative("Wrap the line at the box's width",
                  "The box would still hold it as one line and lay all of it out to draw any part of it. With "
                  "megabytes on one line, that is what holds the window.")
    p.alternative("Cut the line, and say how much is missing",
                  "The end of the answer would still not be on screen, which is what you reported.")
    p.fits("The Log does not fold. It shows the first 20,000 characters of a longer line, then “show all”, which opens "
           "all of it in Data preview, folded like this.")

    p.step("Open orders.yaml (File > Open…) and press ▶ Run now. load_orders gives a text of one line of 5,000 "
           "characters. Click load_orders, then the Data preview tab under the canvas.")
    tab = open_flow(win, monkeypatch, tmp_path / "orders.yaml", orders_flow(code, "orders"))
    press_run_now(win)
    finished(qtbot, tab, 1)
    click_block(tab, "load_orders")
    click_tab(win.bottom_tabs.tabBar(), "Data preview")
    preview = win.preview
    qtbot.waitUntil(lambda: len(visible(box(win))) >= 5_000, timeout=10_000)
    folded = visible(box(win)).split("\n")
    p.shot(win, "① load_orders is selected. ② Data preview shows its one line as three lines: the first two are "
                "2,000 characters long, the third has the last 1,000.",
           highlight=["load_orders", preview.text], badges=True, zoom=[top_of(win, preview, 110)])
    p.check("The text load_orders made is one line of 5,000 characters", len(text) == 5_000 and "\n" not in text)
    p.check("Data preview shows it as lines of 2,000, 2,000 and 1,000 characters",
            [len(each) for each in folded] == [2_000, 2_000, 1_000])
    p.check("Put end to end, they are the text, character for character", "".join(folded) == text)

    p.step("Click in the box, press Ctrl+A, then Ctrl+C.")
    copied, _ = copy_all(win, qtbot, preview.text)
    p.check("What was copied is one line of 5,000 characters: the text as load_orders made it",
            "\n" not in copied and len(copied) == 5_000 and copied == text)

    p.step(f"Press Ctrl+F and type {FOLDED_WORD}.")
    found = find(win, qtbot, preview.text, FOLDED_WORD)
    p.shot(win, f"The find bar, under the box, says {found}: the word is found, once. The box has scrolled to it: "
                f"its first 5 letters, “{FOLDED_WORD[:5]}”, are marked at the end of the first line. Its last 5 start "
                "the second line, at the far left.",
           highlight=[marked(win, FOLDED_WORD[:5]), preview.text_find_bar.count],
           zoom=[in_window(win, preview, -700)])
    p.check(f"“{FOLDED_WORD}” is in the text once, across characters 1,995 to 2,005: 5 before the fold, 5 after",
            text.count(FOLDED_WORD) == 1 and text[1995:2005] == FOLDED_WORD
            and folded[0].endswith(FOLDED_WORD[:5]) and folded[1].startswith(FOLDED_WORD[5:]))
    p.check("Find finds it: the find bar says 1/1", found == "1/1")
    for each in win.tabs_list():
        each.doc.dirty = False


def test_a_one_line_label_says_when_it_is_shortened_and_a_table_in_the_log_stays_a_small_look(proof, win, qtbot,
                                                                                             tmp_path, monkeypatch):
    whole = made_by(ORDER_LINES)  # the text load_orders makes
    ending = f"… ({len(whole):,} characters)"

    p = proof("A one-line label says when it is shortened, and a table in the Log stays a small look",
              f"{ASKED} (And about a table in the Log:) {SMALL_LOOK}")
    p.idea("Some places show a value on one line: the line a block writes in the Log when it finishes, and the line "
           f"under it on the canvas. A long value is shortened there, and now says so: the line ends with “{ending}”, "
           "the size of the whole value.")
    p.idea("Rest the mouse on the line under the block: the hover shows the value's first 4,000 characters, then “…”. "
           "All of it is in Data preview.")
    p.idea("A table that Inspect writes in the Log stays a small look at the table, as you asked: its count of rows "
           "and columns, its first 5 and last 5 rows, and cells shortened with … at 60 characters. The whole table, "
           "with whole cells, is in Data preview's grid.")
    p.idea("Before, a shortened label was cut without a word: you could not tell a value that ended there from one "
           "that went on for megabytes.")
    p.why_simplest("A label keeps to one line so that the Log and the canvas stay easy to read. What it did not say "
                   "was whether there is more, and how much: the count of characters says both, in the same line.")
    p.alternative("Put “show all” on these labels too",
                  "All of the value is already one click away: click the block, and Data preview shows it. A link in "
                  "every finished line would crowd the Log.")
    p.alternative("Show a table whole in the Log, like a text",
                  "You asked to keep it a small look. A table has a grid made for it, in Data preview, with every row, "
                  "Find and sorting by its columns' names in view.")
    p.fits("The same ending is on each input and variable of an error report, and on the finished line that "
           "taskloom run prints in a terminal.")

    p.step("Open orders.yaml (File > Open…) and press ▶ Run now. load_orders gives a text of 500 orders, one a line: "
           f"{len(whole):,} characters. Rest the mouse on the line under load_orders, on the canvas.")
    flow = tmp_path / "orders.yaml"
    tab = open_flow(win, monkeypatch, flow, orders_flow(ORDER_LINES, "result"))
    press_run_now(win)
    finished(qtbot, tab, 1)
    under = tab.scene.nodes["load_orders"].status_text
    tip = hover(qtbot, tab, "load_orders")
    says = as_seen(tip.text())
    place = QRect(tip.mapToGlobal(QPoint(0, 0)) - win.mapToGlobal(QPoint(0, 0)), tip.size())
    p.shot(tip, "The hover: the output's name, result, then the value from its first character. It stops after "
                "4,000 characters, in the middle of order 00032, with “…”. Its last line, in small letters, says when "
                "the result was made.",
           over=win, highlight=[tip], zoom=[QRect(place.left(), place.bottom() - 110, min(700, place.width()), 110)])
    tip.hide()
    after = says[len("result\n") + 4000:]  # what the hover shows after the value's first 4,000 characters
    p.text("The last lines of the hover", "\n".join(says.split("\n")[-4:]))
    p.check("The whole hover is on screen, inside the window", win.rect().contains(place))
    p.check("The hover shows the name result, then the value's first 4,000 characters",
            says.startswith("result\n" + whole[:4000]))
    p.check("Then “…”, and nothing more of the value", after.startswith("…") and whole[4000:4020] not in after)

    p.step("In a terminal, run the same flow: taskloom run orders.yaml. Look at the line it prints when load_orders "
           "finishes.")
    done = subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow)], cwd=tmp_path, capture_output=True,
                          text=True, timeout=120, env={**os.environ, "TASKLOOM_HOME": str(win.home.root)})
    printed = dict(re.findall(r"^\d\d:\d\d:\d\d \[(\w+)\] (success · .*)$", done.stderr, re.M))
    p.text("The line the terminal printed for load_orders", printed.get("load_orders", done.stderr[-2000:]))
    p.check(f"It is one line: the value's first characters, then “{ending}”",
            done.returncode == 0 and printed["load_orders"].startswith("success · result: ")
            and printed["load_orders"].endswith(ending))
    p.check("The line under load_orders on the canvas is the same, after its ✓",
            under == "✓ " + printed["load_orders"].removeprefix("success · "))
    p.note("On the canvas a block is too narrow for that line: it shows its start. The ending with the count is in "
           "what the terminal prints, and in an error report's inputs and variables.")

    p.step("Open orders_table.yaml and press ▶ Run now. Here load_orders gives a table of 12 orders; the note of the "
           "second one is 100 characters long. inspect_orders writes the table in the Log. Drag the edge between the "
           "canvas and the Log up, to see the whole of it.")
    tab = open_flow(win, monkeypatch, tmp_path / "orders_table.yaml", orders_flow(ORDERS_TABLE, "result"))
    press_run_now(win)
    run = finished(qtbot, tab, 1)
    drag_up(win, win.output, 280)
    scroll_log_to(win, run, "[inspect_orders] INFO 12 rows × 3 columns")
    table = log_text(win)[log_text(win).index("[inspect_orders] INFO 12 rows × 3 columns"):]
    look = in_the_log(win, run, ["[inspect_orders] INFO 12 rows × 3 columns", "pen 12"], upto=NOTE[:60] + "…")
    p.shot(win, "The table in the Log: “12 rows × 3 columns”, then pen 01 to pen 05, a row of …, and pen 08 to "
                "pen 12. The long note of pen 02 ends with … after 60 characters.",
           highlight=[look], zoom=[look])
    p.check("The Log says “12 rows × 3 columns”, then shows the first 5 and the last 5 rows: not pen 06 and pen 07",
            [f"pen {i:02d}" for i in range(1, 13) if f"pen {i:02d}" in table]
            == ["pen 01", "pen 02", "pen 03", "pen 04", "pen 05", "pen 08", "pen 09", "pen 10", "pen 11", "pen 12"])
    p.check("The note of 100 characters is shortened with … after its first 60",
            len(NOTE) == 100 and NOTE[:60] + "…" in table and NOTE[:61] not in table)

    p.step("Click inspect_orders, then the Data preview tab.")
    click_block(tab, "inspect_orders")
    click_tab(win.bottom_tabs.tabBar(), "Data preview")
    preview = win.preview
    model = preview.view.model()
    p.shot(win, "① The line says “inspect_orders.value: 12 rows × 3 columns”. ② The grid has every row, pen 06 and "
                "pen 07 too. The note of pen 02 is longer than its column is wide.",
           highlight=[the_line(win), [(preview.view, 5), (preview.view, 6)]], badges=True,
           zoom=[in_window(win, preview, 700)])
    p.check("① The line reads “inspect_orders.value: 12 rows × 3 columns”",
            preview.view.isVisible() and preview.label.text() == "inspect_orders.value: 12 rows × 3 columns")
    p.check("② The grid has the 12 rows, pen 01 to pen 12",
            [model.data(model.index(row, 1)) for row in range(model.rowCount())]
            == [f"pen {i:02d}" for i in range(1, 13)])

    p.step("Double-click the right edge of the note column's header: the column becomes as wide as its longest cell.")
    header = preview.view.horizontalHeader()
    QTest.mouseDClick(header.viewport(), Qt.LeftButton, Qt.NoModifier,
                      QPoint(header.sectionViewportPosition(2) + header.sectionSize(2) - 1, header.height() // 2))
    QTest.qWait(100)
    cell = preview.view.visualRect(model.index(1, 2))
    note = QRect(preview.view.viewport().mapTo(win, cell.topLeft()), cell.size())
    p.shot(win, "The note of pen 02, whole in its cell: its 100 characters, down to “leave it at the sid”, where it "
                "ends.", highlight=[note], zoom=[note.adjusted(0, -40, 0, 30)])
    p.check("The note of pen 02 is whole in its cell: all 100 characters",
            model.data(model.index(1, 2)) == NOTE and model.headerData(2, Qt.Horizontal).startswith("note"))
    p.check("The note column is wide enough to show them on one line, and the cell is on screen",
            header.sectionSize(2) >= preview.view.fontMetrics().horizontalAdvance(NOTE)
            and preview.view.viewport().rect().contains(cell))
    for each in win.tabs_list():
        each.doc.dirty = False
