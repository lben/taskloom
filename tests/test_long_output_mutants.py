"""Long output in the Log, Inspect and Data preview: what mutation testing of its code
(docs/promises/long-output.mutants.yaml) found no test for. Rows of docs/promises/long-output.yaml, where a test is a
row's behaviour.

As in tests/test_long_output_log.py, whose helpers these tests use: the real editor window and a real run of
Manual Start -> load_orders (Python Code) -> inspect_orders (Inspect); the tests read the Log's words and how they are
drawn, Data preview's label and box, what Find marks there and what Copy puts on the clipboard. The tests of a value's
text (a table's look, a secret, JSON, the one-line labels) run the same flow with `taskloom run` and read what it
prints and what the run history keeps.
"""
import os
import re

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests import test_find_bar as F  # noqa: E402
from tests import test_long_output_log as L  # noqa: E402
from tests import test_long_output_rest as R  # noqa: E402
from tests.test_rerun_carries_on import RUN_ONLY, choose  # noqa: E402

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
    """The stretches of Data preview's box that Find marks, top to bottom, each as its characters read (a line break
    of the box in it: "\\n")."""
    return [mark.cursor.selectedText().replace("\u2029", "\n") for mark in win.preview.text.extraSelections()]


def drawn(win, words: str) -> QTextCharFormat:
    """How the Log draws `words`, where it first has them: the look of their first letter."""
    document = win.log.text.document()
    found = document.find(words)
    assert not found.isNull(), f"the Log has no {words!r}: {L.log_text(win)[-300:]!r}"
    letter = QTextCursor(document)
    letter.setPosition(found.selectionStart() + 1)
    return letter.charFormat()


def header_shade(view, column: int) -> QColor:
    """The colour most of a column's header is painted in, in a table view."""
    header = view.horizontalHeader()
    return F.behind(header, QRect(header.sectionViewportPosition(column), 0, header.sectionSize(column),
                                  header.height()))


def inspected(env, block: str = "inspect_orders") -> str:
    """What the Inspect block wrote in the one run of `taskloom run`, as the run history keeps it for the Log."""
    [(message,)] = env.query("SELECT message FROM logs WHERE block=? AND level='INFO'", block)
    return message


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
    # mark2 is across the first fold; mark3 starts the line after the second, with nothing of the fold in its mark.
    assert marked(win) == ["mark0", "mark1", "mar\nk2", "mark3", "mark4", "mark5", "mark6"]
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


# --- how the Log draws a run's lines and show all ---------------------------------------------------------------------

def test_the_lines_that_start_and_end_a_run_are_bold_in_the_log_a_blocks_line_is_not_and_show_all_is_underlined(
        win, qtbot, tmp_path):
    L.run_in_editor(win, qtbot, L.orders_flow(tmp_path, L.json_code(lines=201)))
    L.the_message(win, qtbot)  # the run's last line is in the Log

    assert "run #1" in L.log_text(win).split("\n")[0]
    assert drawn(win, "run #1").fontWeight() >= 600, "the run's first line is not bold"
    assert drawn(win, "DONE").fontWeight() >= 600, "the run's last line is not bold"
    assert drawn(win, "[inspect_orders]").fontWeight() < 600, "a block's line is bold"
    assert drawn(win, "show all").fontUnderline(), "show all is not underlined, as a link is"
    assert not drawn(win, "more line").fontUnderline(), "the words before show all are underlined too"


@pytest.mark.invariant("NC-2")
def test_show_all_is_reached_with_the_tab_key_and_pressed_with_enter(win, qtbot, tmp_path):
    code = L.json_code(lines=201)
    L.run_in_editor(win, qtbot, L.orders_flow(tmp_path, code))
    L.the_message(win, qtbot)
    L.focus(win, qtbot, win.log.text)

    QTest.keyClick(win.log.text, Qt.Key_Tab)
    assert win.log.text.textCursor().selectedText() == "show all", "Tab in the Log did not go to show all"
    QTest.keyClick(win.log.text, Qt.Key_Return)

    qtbot.waitUntil(lambda: win.bottom_tabs.currentWidget() is win.preview, timeout=5_000)
    assert L.label(win).startswith("Log message of inspect_orders, ") and L.label(win).endswith(": 201 lines")
    L.assert_box_holds(win, qtbot, L.said(L.made(code)))


# --- Find in a table of Data preview ---------------------------------------------------------------------------------

def test_find_in_a_table_counts_no_cell_for_a_pattern_that_can_match_nothing_and_marks_a_found_header_not_a_cell(
        win, qtbot, tmp_path):
    code = 'orders = pl.DataFrame({"id": [1, 2, 3], "item": ["pen", "ink", "paper"], "amount": [3, 1, 2]})'
    tab = L.run_in_editor(win, qtbot, L.orders_flow(tmp_path, code))
    L.show_in_data_preview(win, tab)
    view = win.preview.view
    assert view.isVisible() and view.model().rowCount() == 3
    plain = header_shade(view, 1)
    assert not F.differs(header_shade(view, 2), plain), "the headers of item and amount look different before Find"

    assert L.find(win, qtbot, view, "(ink)*") == "1/1"  # the one cell that has ink, not every cell

    assert L.find(win, qtbot, view, "amount") == "1/1"
    QApplication.processEvents()
    assert F.differs(header_shade(view, 2), plain), "the header of amount, which Find found, is not marked"
    assert not F.differs(header_shade(view, 1), plain), "the header of item is marked too"
    assert not view.selectionModel().selectedIndexes(), "a cell is selected for a match in a header"


# --- a click on the block that is already selected -------------------------------------------------------------------

@pytest.mark.invariant("NC-2")
def test_a_click_on_the_selected_block_while_data_preview_shows_its_second_output_leaves_the_second_output_there(
        win, qtbot, tmp_path):
    code = 'orders = {"orders": [1, 2, 3]}\ncount = 3'
    tab = L.run_in_editor(win, qtbot, L.orders_flow(tmp_path, code, outputs=("orders", "count")))
    R.click_on_canvas(tab, "load_orders")
    win.bottom_tabs.setCurrentWidget(win.preview)
    assert L.label(win) == "load_orders.orders:" and win.preview.ports.isVisible()
    win.preview.ports.setCurrentText("count")  # as chosen in the box of outputs
    assert L.label(win) == "load_orders.count:" and L.box(win) == "3"

    R.click_on_canvas(tab, "load_orders")  # the block that is selected already
    qtbot.wait(200)

    assert L.label(win) == "load_orders.count:" and L.box(win) == "3", \
        f"after the click Data preview shows {L.label(win)!r}"
    assert win.preview.ports.currentText() == "count"


@pytest.mark.invariant("NC-2")
def test_a_click_on_the_selected_block_whose_result_is_old_brings_its_output_back_with_the_old_note(win, qtbot,
                                                                                                    tmp_path):
    tab = win.open_flow(R.orders_flow(tmp_path, R.LONG))
    qtbot.waitExposed(win)
    R.run(win, qtbot, tab)
    alone = choose(win, qtbot, tab, "load_orders", RUN_ONLY)  # inspect_orders' result is now old
    assert alone.status == "success", alone.log
    R.click_on_canvas(tab, "inspect_orders")
    assert R.label(win) == "inspect_orders.value (old):", R.label(win)
    R.press_show_all(win)  # its long message, from the first run
    qtbot.waitUntil(lambda: R.label(win).startswith("Log message of inspect_orders, "), timeout=10_000)

    R.click_on_canvas(tab, "inspect_orders")  # the block that is selected already

    try:
        qtbot.waitUntil(lambda: R.label(win).startswith("inspect_orders."), timeout=3_000)
    except qtbot.TimeoutError:
        pass
    assert R.label(win) == "inspect_orders.value (old):", f"after the click Data preview's label reads {R.label(win)!r}"


# --- what the editor reads from a run while it goes ------------------------------------------------------------------

def test_a_line_that_is_not_utf_8_from_a_library_is_in_the_log_with_a_mark_where_it_cannot_be_read(win, qtbot,
                                                                                                 tmp_path):
    # "café au lait" as Windows writes it in Western Europe: its é is one byte, which is not UTF-8.
    code = 'import os\nos.write(1, "café au lait\\n".encode("cp1252"))\norders = "done"'

    L.run_in_editor(win, qtbot, L.orders_flow(tmp_path, code))

    L.wait(qtbot, lambda: " OUTPUT caf\ufffd au lait" in L.log_text(win), lambda: L.log_text(win)[-600:])


# --- a value's text: a table's look, a secret, JSON ------------------------------------------------------------------

PASSWORD = "p4$$w0rd+(orders)"  # with punctuation that means something in a regular expression


@pytest.mark.invariant("NC-7")
def test_a_password_with_punctuation_in_a_tables_cell_shows_as_stars_with_the_rest_of_the_cell_as_it_is(env):
    """The cell has 68 characters with the password in it and 54 with *** in its place: the password is replaced
    first, so the cell is under the 60 characters at which a table's cells are cut, and shows whole."""
    secrets = env.home / "secrets.yaml"
    secrets.write_text(yaml.safe_dump({"orders_token": PASSWORD}), encoding="utf-8")
    secrets.chmod(0o600)
    link = "/orders?status=open&sort=newest&per_page=100&token="
    assert len(link + PASSWORD) == 68 and len(link + "***") == 54
    code = ('token = ctx.home.get_secret("orders_token")\n'
            'orders = pl.DataFrame({"id": [1, 2], "link": ["%s" + token, "/orders?page=3"]})' % link)

    done = env.taskloom("run", L.orders_flow(env.root, code))

    assert done.returncode == 0, done.stderr[-2000:]
    assert PASSWORD not in done.stdout + done.stderr, "the password is printed in the terminal"
    kept = [f.name for f in env.home.glob("history.db*") if PASSWORD.encode() in f.read_bytes()]
    assert not kept, f"the password is in the run history's file: {kept}"
    table = inspected(env).split("\n")
    assert table[0] == "2 rows × 2 columns" and len(table) == 9, table
    assert [[cell.strip() for cell in row.strip("│").split("┆")] for row in table[-3:-1]] == [
        ["1", link + "***"], ["2", "/orders?page=3"]], table


@pytest.mark.invariant("NC-9")
def test_a_table_of_12_rows_is_its_first_5_and_last_5_rows_also_where_polars_is_set_to_draw_20_rows(env):
    env.env["POLARS_FMT_MAX_ROWS"] = "20"  # as set by someone who uses Polars, for every program they start
    code = 'orders = pl.DataFrame({"id": list(range(1, 13)), "item": [f"pen {i:02d}" for i in range(1, 13)]})'

    done = env.taskloom("run", L.orders_flow(env.root, code))

    assert done.returncode == 0, done.stderr[-2000:]
    table = inspected(env)
    assert table.startswith("12 rows × 2 columns\n"), table
    assert re.findall(r"pen \d\d|…", table) == ["pen 01", "pen 02", "pen 03", "pen 04", "pen 05", "…", "…",
                                                "pen 08", "pen 09", "pen 10", "pen 11", "pen 12"], table


def test_a_table_of_21_columns_shows_its_first_10_and_last_10_with_an_ellipsis_between_and_one_of_20_shows_all(env):
    code = ('orders = pl.DataFrame({f"c{i:02d}": [i] for i in range(1, 22)})\n'
            'twenty = orders.drop("c21")')

    done = env.taskloom("run", L.orders_flow(env.root, code, outputs=("orders", "twenty")))

    assert done.returncode == 0, done.stderr[-2000:]
    names = [f"c{i:02d}" for i in range(1, 22)]
    wide, twenty = inspected(env).split("\n"), inspected(env, "inspect_twenty").split("\n")
    assert wide[0] == "1 rows × 21 columns" and twenty[0] == "1 rows × 20 columns"
    assert re.findall(r"c\d\d|…", wide[2]) == names[:10] + ["…"] + names[11:], wide[2]
    assert re.findall(r"c\d\d|…", twenty[2]) == names[:20], twenty[2]


def test_a_dict_with_a_date_and_an_accented_word_is_written_as_json_the_date_as_its_text_the_word_as_typed(env):
    code = 'import datetime\norders = {"city": "Zürich", "paid": datetime.date(2026, 3, 9)}'

    done = env.taskloom("run", L.orders_flow(env.root, code))

    assert done.returncode == 0, done.stderr[-2000:]
    assert inspected(env) == '{\n  "city": "Zürich",\n  "paid": "2026-03-09"\n}'


# --- the one-line labels, at their limits ----------------------------------------------------------------------------

@pytest.mark.invariant("NC-8")
def test_the_finished_line_shows_an_output_of_exactly_200_characters_whole_shortens_one_of_201_and_parts_them_with_a_semicolon(
        env):
    # As the finished line writes a text, in quotes, these have 200 and 201 characters.
    code = 'orders = "a" * 198\nlonger = "b" * 199'

    done = env.taskloom("run", L.orders_flow(env.root, code, outputs=("orders", "longer")))

    assert done.returncode == 0, done.stderr[-2000:]
    [line] = re.findall(r"^\d\d:\d\d:\d\d \[load_orders\] (success · .*)$", done.stderr, re.M)
    assert line == "success · orders: '" + "a" * 198 + "'; longer: '" + "b" * 199 + "… (199 characters)", line


@pytest.mark.invariant("NC-8")
def test_an_error_report_shows_a_variable_of_exactly_300_characters_whole_and_shortens_one_of_301(env):
    code = 'fits = "e" * 300\nlonger = "f" * 301\nraise ValueError("an order has no price")'

    done = env.taskloom("run", L.orders_flow(env.root, code))

    assert done.returncode == 1, done.stderr[-2000:]
    report = done.stderr.splitlines()
    assert "  fits = " + "e" * 300 in report, [line[:40] + " … " + line[-40:] for line in report if "fits" in line]
    assert "  longer = " + "f" * 300 + "… (301 characters)" in report, \
        [line[:40] + " … " + line[-40:] for line in report if "longer" in line]
