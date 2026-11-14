"""Long output in the Log, Inspect and Data preview: what mutation testing of its code
(docs/promises/long-output.mutants.yaml) found no test for. Rows of docs/promises/long-output.yaml, where a test is a
row's behaviour.

As in tests/test_long_output_log.py, whose helpers these tests use: the real editor window and a real run of
Manual Start -> load_orders (Python Code) -> inspect_orders (Inspect); the tests read the Log's words, Data preview's
label and box, what Find marks there and what Copy puts on the clipboard.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests import test_long_output_log as L  # noqa: E402

# load_orders' code: a text of three lines. The first has an emoji (Qt counts it as two characters) and ends as a
# Windows line does; the second has 7,000 characters, so Data preview folds it at 2,000, 4,000 and 6,000, with mark2
# across the first fold, mark3 just after the second and mark4 just before the third; mark6 ends the text.
MARKED = ('line = list("." * 7000)\n'
          'for word, at in (("mark1", 100), ("mark2", 1997), ("mark3", 4000), ("mark4", 5995), ("mark5", 6500)):\n'
          '    line[at:at + 5] = word\n'
          'orders = "first \\U0001F600 mark0\\r\\n" + "".join(line) + "\\nlast mark6"')
# load_orders' code: it makes a small value, and a library it uses writes one line of 25,000 characters.
LIBRARY_LINE = ('import sys\n'
                'sys.stdout.write("".join(f"row {i:05d} " for i in range(1, 5000))[:25000] + "\\n")\n'
                'sys.stdout.flush()\n'
                'orders = "done"')


def under_the_message(win, qtbot) -> str:
    """The line the Log shows under the long message of inspect_orders."""
    return L.the_message(win, qtbot).lines[-1].strip()


def press_the_only_show_all(win):
    """Press the one "show all" the Log shows, with the mouse."""
    edit = win.log.text
    assert L.log_text(win).count("show all") == 1, L.log_text(win)[-300:]
    words = edit.document().find("show all")
    middle = QTextCursor(edit.document())
    middle.setPosition(words.selectionStart() + 4)
    for _ in range(2):  # scroll it into the middle of the Log, as with the scroll bars
        rect = edit.cursorRect(middle)
        for bar, off in ((edit.verticalScrollBar(), rect.center().y() - edit.viewport().height() // 2),
                         (edit.horizontalScrollBar(), rect.center().x() - edit.viewport().width() // 2)):
            bar.setValue(bar.value() + off)
        QApplication.processEvents()
    QTest.mouseClick(edit.viewport(), Qt.LeftButton, Qt.NoModifier, edit.cursorRect(middle).center())
    QApplication.processEvents()


def marked(win) -> list[str]:
    """The stretches of Data preview's box that Find marks, top to bottom, each as its characters read."""
    return [mark.cursor.selectedText().replace("\u2029", "") for mark in win.preview.text.extraSelections()]


def test_a_message_of_201_lines_says_1_more_line_and_the_log_still_starts_with_the_runs_first_line(win, qtbot,
                                                                                                    tmp_path):
    code = L.json_code(lines=201)
    message = L.said(L.made(code))

    L.run_in_editor(win, qtbot, L.orders_flow(tmp_path, code))

    assert under_the_message(win, qtbot) == f"… 1 more line ({len(message):,} characters in all) · show all"
    first = L.log_text(win).split("\n")[0]
    assert "run #1" in first and first.endswith("started"), f"the Log starts with {first!r}"


def test_a_message_cut_inside_its_first_line_says_how_many_characters_it_has_and_counts_no_lines(win, qtbot,
                                                                                                  tmp_path):
    code = L.line_code(25_000) + ' + "\\nthe second line\\nthe last line"'
    message = L.made(code)
    assert len(message.split("\n")) == 3 and len(message.split("\n")[0]) == 25_000

    L.run_in_editor(win, qtbot, L.orders_flow(tmp_path, code))

    assert under_the_message(win, qtbot) == f"… ({len(message):,} characters in all) · show all"


@pytest.mark.invariant("NC-6")
def test_find_in_data_preview_marks_each_word_itself_after_an_emoji_and_around_the_folds_of_a_long_line(win, qtbot,
                                                                                                         tmp_path):
    tab = L.run_in_editor(win, qtbot, L.orders_flow(tmp_path, MARKED))
    L.show_in_data_preview(win, tab)
    whole = L.made(MARKED).replace("\r\n", "\n")
    qtbot.waitUntil(lambda: L.box(win).replace("\n", "") == whole.replace("\n", ""), timeout=10_000)
    assert [len(line) for line in L.box(win).split("\n")] == [13, 2000, 2000, 2000, 1000, 10]

    assert L.find(win, qtbot, win.preview.text, r"mark\d") == "1/7"
    assert marked(win) == ["mark0", "mark1", "mark2", "mark3", "mark4", "mark5", "mark6"]
    # A pattern that can also match nothing counts only the word it finds.
    assert L.find(win, qtbot, win.preview.text, "(mark5)*") == "1/1"
    assert marked(win) == ["mark5"]


@pytest.mark.invariant("NC-6")
def test_a_part_of_a_folded_line_copied_from_data_preview_is_that_part_as_it_was_and_typing_changes_nothing(
        win, qtbot, tmp_path):
    tab = L.run_in_editor(win, qtbot, L.orders_flow(tmp_path, MARKED))
    L.show_in_data_preview(win, tab)
    whole = L.made(MARKED).replace("\r\n", "\n")
    box = win.preview.text
    qtbot.waitUntil(lambda: L.box(win).replace("\n", "") == whole.replace("\n", ""), timeout=10_000)
    L.focus(win, qtbot, box)
    cursor = QTextCursor(box.document())
    cursor.setPosition(box.document().find("mark3").selectionStart())
    cursor.setPosition(box.document().find("mark5").selectionEnd(), QTextCursor.KeepAnchor)
    box.setTextCursor(cursor)  # as dragged with the mouse: from mark3, past the third fold, to the end of mark5

    QApplication.clipboard().setText("")
    QTest.keyClick(box, Qt.Key_C, Qt.ControlModifier)

    copied = QApplication.clipboard().text()
    assert copied == whole[whole.index("mark3"):whole.index("mark5") + 5], (len(copied), copied[:40], copied[-40:])
    shown = L.box(win)
    QTest.keyClicks(box, "x")
    assert L.box(win) == shown, "typing in Data preview's box changed its text"


@pytest.mark.invariant("NC-2")
def test_show_all_under_a_line_a_library_wrote_names_no_block_says_1_line_and_shows_it_folded(win, qtbot, tmp_path):
    line = "".join(f"row {i:05d} " for i in range(1, 5000))[:25_000]

    L.run_in_editor(win, qtbot, L.orders_flow(tmp_path, LIBRARY_LINE))
    qtbot.waitUntil(lambda: "show all" in L.log_text(win), timeout=10_000)
    shown = next(text for text in L.log_text(win).split("\n") if " OUTPUT " in text)
    press_the_only_show_all(win)

    qtbot.waitUntil(lambda: win.bottom_tabs.currentWidget() is win.preview, timeout=5_000)
    assert L.label(win) == f"Log message, {shown[:8]}: 1 line"
    qtbot.waitUntil(lambda: L.box(win).replace("\n", "") == line, timeout=10_000)
    assert [len(part) for part in L.box(win).split("\n")] == [2000] * 12 + [1000]
