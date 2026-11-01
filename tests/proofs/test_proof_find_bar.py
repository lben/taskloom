"""Proof: Ctrl+F finds text in the Log, Data preview, Run history and every code box, as JetBrains' editors do. How
proofs are written: README.md here."""

import datetime as dt
import re

import yaml
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QHelpEvent, QKeySequence, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QToolTip

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_inspect_log import click_tab
from proofs.test_proof_retention_per_start import drag_up
from proofs.test_proof_run_output import click, run_now
from proofs.test_proof_tab_plus import set_theme
from taskloom.editor import theme
from tests.test_code_editor import code_box
from tests.test_find_bar import (MARK, TEXT, Bar, cell_matches, check_cells, check_text, contrast, in_view, is_red,
                                 most_common, red_pixels, says_invalid, text_matches, text_on, unhighlighted, where)

ASKED = ("Implement the ability to do cntrl+f when the focus is on the Log window and it should work as the JetBrains "
         "products where it tells me how many found, an up and a down arrow to the right that lets me navigate over "
         "the matches (highlighting them) and it also should have support for regex searches that need to be enabled "
         "by clicking a '.*' button to the right of the search bar that shows up at the bottom of the Log window; "
         "there should also be support for a 'Aa' button that enables/disables Case Sensitive search. This search "
         "should work for Data Preview and Run history as well. This search should also work across every block "
         "that houses text like Python Code, Run Query, DuckDB SQL, Polars Transform, others I am missing? blocks. "
         "Regex search is enabled by default and Case sensitive search is disabled by default.")
LATE, NO_PAYMENT = (4, 13, 18, 31, 36), (9, 27)  # the orders paid late, and those not paid yet ("Late: …")
CHECK = f"""\
# This week's 40 orders: a line for each, saying which were paid late
regions = ["North", "South", "East", "West"]
late = {{{", ".join(map(str, LATE))}}}
no_payment = {{{", ".join(map(str, NO_PAYMENT))}}}
for i in range(1, 41):
    if i in no_payment:
        note = "Late: no payment yet"
    elif i in late:
        note = "paid late"
    else:
        note = "paid on time"
    print(f"row {{i}}: {{regions[i % 4]}}, {{i * 37 % 500 / 10}} k, {{note}}")
"""
SQL = f"""\
-- This week's 40 orders, with how each was paid
select range as "row",
       ['North', 'South', 'East', 'West'][range % 4 + 1] as region,
       range * 37 % 500 / 10 as amount_k,
       case when range in {NO_PAYMENT} then 'Late: no payment yet'
            when range in {LATE} then 'paid late'
            else 'paid on time' end as payment,
       case when range in {LATE} then 3 else 0 end as days_late
from range(1, 41)
"""
FLOW = {
    "blocks": {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
        "load_sales": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": SQL}, "ui": {"x": 260, "y": -60}},
        "check_rows": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": CHECK},
                       "ui": {"x": 260, "y": 60}},
        "every_night": {"type": "trigger.schedule", "config": {"cron": "0 2 * * *"}, "ui": {"x": 0, "y": 175}},
        "archive_old": {"type": "logic.python",
                        "config": {"inputs": [], "code": "raise RuntimeError('cannot reach the archive folder')"},
                        "ui": {"x": 260, "y": 175}},
    },
    "edges": ["start_by_hand.start -> load_sales.after", "start_by_hand.start -> check_rows.after",
              "every_night.start -> archive_old.after"],
}
SCHEDULE_RUN = "Run the flow now, as if its time had come"
LOG_HEIGHT = 400  # the Log made taller: some 20 of its lines in view


def settle():
    for _ in range(5):
        QApplication.processEvents()


def click_in(widget, at: QPoint):
    """Click `widget` at `at`, as you would: the keyboard focus goes there. (Its window is made the active one first,
    as a click on it makes it: offscreen, a menu that was open may have left no window active.)"""
    widget.window().activateWindow()
    settle()
    QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, at)
    settle()


def press_ctrl_f() -> Bar | None:
    """Press Ctrl+F (Cmd+F on macOS) where the keyboard focus is: the find bar it opened, whose search field then has
    the focus (tests/test_find_bar.py); None when no search field has it."""
    focused = QApplication.focusWidget()
    if focused is None:
        return None
    QTest.keySequence(focused, QKeySequence(QKeySequence.Find))
    settle()
    field = QApplication.focusWidget()
    return Bar(field) if isinstance(field, QLineEdit) else None


def marked(check, *args) -> int:
    """A check of Taskloom's tests (tests/test_find_bar.py) on what a view paints: the current match is in view, it and
    every other match in view are highlighted, the current one in a colour of its own. How many other matches were in
    view with it; -1 when that does not hold."""
    try:
        return check(*args)
    except AssertionError:
        return -1


def tip_on(qtbot, widget, at: QPoint | None = None):
    """Rest the mouse on `widget` (at its middle, or `at`): the tooltip it shows."""
    QToolTip.hideText()
    at = at if at is not None else widget.rect().center()
    QTest.mouseMove(widget, at)
    QApplication.sendEvent(widget, QHelpEvent(QEvent.ToolTip, at, widget.mapToGlobal(at)))
    tips = []

    def shown():
        tips[:] = [w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()]
        return bool(tips)

    qtbot.waitUntil(shown, timeout=5_000)
    return tips[0]


def in_window(win, widget, rect: QRect | None = None) -> QRect:
    """`rect` of `widget` (all of it by default) in a screenshot of the window."""
    rect = rect if rect is not None else widget.rect()
    return rect.translated(widget.mapTo(win, QPoint(0, 0)))


def lines_of(win, view, first: int, last: int, width: int = 640) -> QRect:
    """The text view's lines from the one with the position `first` to the one with `last`, their first `width`
    pixels, in a screenshot of the window."""
    a, b = QTextCursor(view.document()), QTextCursor(view.document())
    a.setPosition(first)
    b.setPosition(last)
    top, bottom = view.cursorRect(a).top(), view.cursorRect(b).bottom()
    return in_window(win, view.viewport(), QRect(0, top, width, bottom - top + 1))


def typed(win, bar: Bar, width: int = 280) -> QRect:
    """The left end of a find bar's field, where what you type shows, in a screenshot of the window."""
    return in_window(win, bar.field, QRect(0, 0, width, bar.field.height()))


def bar_end(win, bar: Bar) -> QRect:
    """The right end of a find bar: the end of its field, the count, the arrows, the two toggles and the close
    button, in a screenshot of the window."""
    end = in_window(win, bar.widget)
    end.setLeft(in_window(win, bar.widget.count).left() - 120)
    return end


def under(win, bar: Bar, view) -> bool:
    """The find bar shows just under `view`."""
    top, bottom = in_window(win, bar.widget).top(), in_window(win, view).bottom()
    return bar.shown() and bottom <= top <= bottom + 24


def left_to_right(bar: Bar) -> bool:
    """Its parts, left to right: the field, the count, the up and down arrows, .*, Aa and the close button."""
    parts = [bar.field, bar.widget.count, bar.up, bar.down, bar.regex, bar.case, bar.close]
    lefts = [part.mapTo(bar.widget, QPoint(0, 0)).x() for part in parts]
    return lefts == sorted(lefts) and len(set(lefts)) == len(lefts)


def red_contrast(widget) -> float:
    """How much the red the widget paints stands out from its background (what most of it is painted in)."""
    image = widget.grab().toImage()
    back = most_common(image)
    reds = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())
            if is_red(image.pixelColor(x, y))]
    return max((contrast(c, back) for c in reds), default=1.0)


def selected_rows(table) -> list[int]:
    return sorted(i.row() for i in table.selectionModel().selectedRows())


def test_ctrl_f_finds_text_in_the_log_data_preview_run_history_and_code_boxes(proof, win, qtbot, tmp_path,
                                                                                monkeypatch):
    p = proof("Ctrl+F finds text in the Log, Data preview, Run history and every code box", ASKED)
    p.idea("Ctrl+F (Cmd+F on macOS) now opens a find bar, as in JetBrains' editors, where the cursor is: in the Log, "
           "Data preview, Run history, or a code box in Properties. The bar shows at the bottom of that panel, or "
           "just under the code box, with the cursor in its search field.")
    p.idea("Left to right it has the search field; the count, such as 3/7 (the third of 7 matches; “0 results” when "
           "nothing matches); an up and a down arrow, for the previous and the next match (also Shift+Enter and "
           "Enter), going round from the last to the first and back; .* for regular expressions, on; Aa for match "
           "case, off; and × to close it (also Esc), which puts the cursor back in the panel or box.")
    p.idea("It finds as you type (text you had selected, on one line, is already in the field): every match is "
           "highlighted, the current one in a colour of its own, and the panel scrolls to it; in Data preview its "
           "cell is also selected, in Run history its run's row. A pattern that is not a valid regular expression "
           "turns the field red, the count says so, and nothing is highlighted.")
    p.idea("Each place finds in what it shows. The Log, in its lines (every block's, or the selected block's), and "
           "lines a run prints while the bar is open are found as they come. Data preview, in the cells and column "
           "headers of the table it shows (its first 200 rows), or in its text. Run history, in every column of its "
           "list. A code box, in its code, which finding never changes.")
    p.idea("The two toggles are shared by all the find bars while the editor runs: Aa turned on in the Log's bar is on "
           "in Data preview's too. They are not saved: each time the editor starts, .* is on and Aa off again.")
    p.idea("What it does not do: there is no replace, it finds in one panel or one box at a time (not in all at once), "
           "and there is no find on the canvas: Ctrl+F there does nothing.")
    p.why_simplest("It is one find bar, the same everywhere: each place with text to search (the Log, Data preview's "
                   "table and its text, Run history and each code box) has its own bar under it, which searches what "
                   "that place shows. The search uses Python's regular expressions, as a Python Code block does: a "
                   "pattern is written the same way, and ^ and $ are the start and end of a line.")
    p.why_simplest("The highlights are painted over the text, as a selection is, not written into it: a code box's "
                   "code does not change, there is nothing to undo, and the flow is not changed. Only the lines in "
                   "view are painted, so a Log of thousands of lines stays quick as new lines come. In a table, each "
                   "cell found is painted on its highlight as the table draws it.")
    p.why_simplest("The .* and Aa of every bar are the same two switches: turned in one bar, they are turned in all, "
                   "so the bars always agree; as the editor starts, they are set as you asked.")
    p.alternative("Qt's own find, which text boxes have: the next match from the cursor, one at a time",
                  "It gives no count and does not highlight every match, and tables have no find of that kind: Data "
                  "preview and Run history would have needed a second way of finding, with other rules.")
    p.alternative("One search box for the whole window, for instance in the toolbar",
                  "It would be far from the text it searches, and would have to guess which panel you meant. You asked "
                  "for a bar at the bottom of the Log, as JetBrains' editors have one under the text they search.")
    p.alternative("Remembering .* and Aa for the next start, as Wrap lines is remembered",
                  "Then a start would begin with whatever was last chosen, not with regular expressions on and match "
                  "case off, as you asked.")
    p.alternative("Highlighting the matches by colouring the text itself, as a word processor does",
                  "In a code box that would change the code's text, and Undo would take the colours back; in a long "
                  "Log, each new line would mean colouring every match again.")
    p.fits("Ctrl+F works only where the cursor is, as Ctrl+C and Ctrl+V copy blocks only on the canvas and text "
           "elsewhere: on the canvas, or in an ordinary field, it does nothing new.")
    p.fits("The highlights follow the Theme setting (Tools > Settings): on light, yellow for the matches and blue for "
           "the current one, as in JetBrains' light theme; on dark, dark green and dark blue, as in its dark theme. "
           "They are chosen by the window's background, as the code boxes choose their colours, and painted again "
           "when the theme changes.")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings writes Windows' Startup script there)
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    flow = tmp_path / "weekly-check.yaml"
    flow.write_text(yaml.safe_dump(FLOW, sort_keys=False), encoding="utf-8")
    try:
        qtbot.waitExposed(win)
        win.activateWindow()

        # --- the Log -------------------------------------------------------------------------------------------
        p.step("Drag the Log's top edge up a little, open weekly-check.yaml (File > Open…), and press ▶ Run now.")
        drag_up(win, win.output, LOG_HEIGHT - win.output.height())
        qtbot.waitUntil(lambda: win.output.height() >= LOG_HEIGHT - 30, timeout=5_000)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
        menu_item(next(a.menu() for a in win.menuBar().actions() if a.text() == "&File"), "Open…").trigger()
        tab = win.current
        p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
        first = run_now(win, tab, qtbot)
        view = win.log.text
        log = view.document().toPlainText()
        p.check("The run ends with success, and check_rows printed its 40 lines in the Log",
                first.status == "success" and len(re.findall(r"row \d+: ", log)) == 40)

        p.step("Click in the Log, on its last line, and press Ctrl+F.")
        click_in(view.viewport(), QPoint(60, view.viewport().height() - 8))
        bar = press_ctrl_f()
        p.check("Ctrl+F opens a find bar, with the cursor in its search field",
                bar is not None and bar.field.hasFocus())
        p.shot(win, "① The find bar, at the bottom of the Log, with the cursor in its search field, which runs the "
                    "width of the Log. The close-up shows its right end: the end of the field; the count, empty until "
                    "you type; the up and down arrows; .*, pressed in: on; Aa, off; and × to close it.",
               highlight=[bar.widget], badges=True, zoom=[bar_end(win, bar)])
        p.check("① It is at the bottom of the Log, under its lines", under(win, bar, view))
        p.check("Left to right: the field, the count, the up arrow, the down arrow, .*, Aa and ×", left_to_right(bar))
        p.check(".* (regular expressions) is on, and Aa (match case) off",
                bar.regex.isChecked() and not bar.case.isChecked())

        p.step("Type late.")
        bar.type("late")
        matches = text_matches(view, "late")
        p.shot(win, f"It finds as you type: ① the count reads {bar.count()}, the first of 7 matches.",
               highlight=[bar.widget.count], badges=True, zoom=[bar_end(win, bar)])
        p.check(f"The count reads 1/7: the Log has late 7 times, in any case ({len(matches)} times)",
                bar.count() == "1/7" and len(matches) == 7)
        p.shot(win, "In the Log, ① the current match, row 4's late, is on blue, and the Log scrolled to it; ② the other "
                    "matches in view are on yellow: row 9's Late, row 13's late, row 18's late.",
               highlight=[(view, "row 4: "), (view, "row 9: ")], badges=True,
               zoom=[lines_of(win, view, matches[0][0], matches[3][0]), typed(win, bar)])
        p.check("① The current match is in view, highlighted in a colour of its own; ② every other match in view is "
                "highlighted too", marked(check_text, view, matches, 0) >= 1)

        p.step("Press Enter, click the down arrow, then press Shift+Enter and click the up arrow.")
        counts = []
        for press in (lambda: bar.key(Qt.Key_Return), lambda: bar.click(bar.down),
                      lambda: bar.key(Qt.Key_Return, Qt.ShiftModifier), lambda: bar.click(bar.up)):
            press()
            counts.append((bar.count(), marked(check_text, view, matches, bar.current()) >= 0))
        p.check(f"Enter goes to the next match, 2/7, the down arrow to the one after, 3/7; Shift+Enter back to 2/7, "
                f"the up arrow to 1/7 ({', '.join(c for c, _ in counts)}), each shown and highlighted as the current one",
                [c for c, _ in counts] == ["2/7", "3/7", "2/7", "1/7"] and all(ok for _, ok in counts))

        p.step("Press Shift+Enter once more, then Enter.")
        bar.key(Qt.Key_Return, Qt.ShiftModifier)
        p.shot(win, f"After Shift+Enter: from the first match it went round to the last. ① The count reads "
                    f"{bar.count()}.", highlight=[bar.widget.count], badges=True, zoom=[bar_end(win, bar)])
        p.shot(win, "The Log scrolled down to the last match: ① row 36's late, the current one, is on blue; ② row "
                    "31's late is on yellow, as is row 27's Late above it: the other matches in view.",
               highlight=[(view, "row 36: "), (view, "row 31: ")], badges=True,
               zoom=[lines_of(win, view, matches[4][0], matches[6][0]), typed(win, bar)])
        p.check("Shift+Enter goes round from the first match to the last: 7/7; ① it is in view, highlighted in a "
                "colour of its own, ② the others in view highlighted too",
                bar.count() == "7/7" and marked(check_text, view, matches, 6) >= 1)
        bar.key(Qt.Key_Return)
        p.check("Then Enter goes round from the last match to the first: 1/7, shown as the current one",
                bar.count() == "1/7" and marked(check_text, view, matches, 0) >= 0)

        p.step(r"Type row \d+ over late.")
        bar.type(r"row \d+")
        rows = text_matches(view, r"row \d+")
        p.shot(win, f"With .* on, row \\d+ is a regular expression: row, a space and a number. The count reads "
                    f"{bar.count()}. Each row and its number is highlighted, whatever the number's length: ① row 8, "
                    "② row 12.", highlight=[(view, "row 8: "), (view, "row 12: ")],
               badges=True, zoom=[lines_of(win, view, rows[5][0], rows[13][0]), typed(win, bar)])
        p.check(r"row \d+ finds each of the 40 rows: 1/40", bar.count() == "1/40" and len(rows) == 40)
        p.check("① ② Each is highlighted with its whole number (row 8, row 12, …)",
                marked(check_text, view, rows, 0) >= 1)

        p.step("Type late again, and click Aa.")
        bar.type("late")
        bar.click(bar.case)
        lower, upper = text_matches(view, "late", 0), text_matches(view, "Late", 0)
        p.shot(win, f"Match case is on, and the count reads {bar.count()}: ① the two Late: lines are no longer "
                    "found; ② the paid late lines still are.",
               highlight=[(view, "row 9: "), (view, "row 13: ")], badges=True,
               zoom=[lines_of(win, view, lower[0][0], lower[2][0]), typed(win, bar)])
        p.check("Aa is on, and the count changes from 7 matches to 5: only late as typed, 1/5",
                bar.case.isChecked() and bar.count() == "1/5" and len(lower) == 5)
        p.check("① The Late: lines are no longer highlighted; ② the paid late ones are",
                any(in_view(view, where(view, *m)) for m in upper) and unhighlighted(view, upper) == []
                and marked(check_text, view, lower, 0) >= 1)

        p.step(r"Type row \d+ again, and click .* off.")
        bar.type(r"row \d+")
        bar.click(bar.regex)
        p.shot(win, f"With .* off, row \\d+ is searched as typed, backslash and + included: the count reads "
                    f"{bar.count()}, as no line of the Log has that text. ① .* is off (raised); Aa is still on.",
               highlight=[bar.regex], badges=True, zoom=[bar_end(win, bar)])
        p.check(r".* is off, and row \d+ is searched as typed: 0 results (the Log has no such text)",
                not bar.regex.isChecked() and bar.count() == "0 results" and r"row \d+" not in log)

        p.step("Rest the mouse on .*, then on Aa.")
        p.note("Here the window stands against the screen's right edge, as a full-screen one does: a tooltip that "
               "would run off the screen opens to the left of the mouse, so all of it is in the picture.")
        was = win.pos()
        win.move(win.screen().geometry().right() + 1 - win.frameGeometry().width(), was.y())
        settle()
        for button, says in ((bar.regex, "regular expressions"), (bar.case, "match case")):
            tip = tip_on(qtbot, button)
            p.shot(tip, f"The tooltip of {button.text()}, outlined, just under it: “{tip.text()}”.", over=win,
                   highlight=[tip], zoom=[tip, bar_end(win, bar)])
            p.check(f"{button.text()}'s tooltip says what it does: {says}, on and off",
                    says in tip.text().lower() and "on" in tip.text() and "off" in tip.text())
            p.check(f"All of {button.text()}'s tooltip is in the picture: it lies inside the window",
                    QRect(win.mapToGlobal(QPoint(0, 0)), win.size()).contains(tip.geometry()))
            QToolTip.hideText()
        win.move(was)
        settle()

        p.step("Click .* on again, type late, then ( after it.")
        bar.click(bar.regex)
        bar.type("late")
        lit = unhighlighted(view, lower)  # highlighted, with late valid (Aa is on)
        valid_red = red_pixels(bar.field)
        QTest.keyClicks(bar.field, "(")
        settle()
        said = bar.widget.count.text()
        p.shot(win, f"late( is not a valid regular expression: its bracket is never closed. ① The field's text turns "
                    f"red, the count says {said} (next step), and nothing in the Log is highlighted any more.",
               highlight=[typed(win, bar, 60)], badges=True,
               zoom=[lines_of(win, view, lower[0][0], lower[2][0]), typed(win, bar)])
        p.check("Before the bracket, late was found and highlighted", len(lit) >= 2)
        p.check("① The field's text turns red", red_pixels(bar.field) - valid_red >= 10)
        p.check(f"The bar says the pattern is not valid: “{said}”", says_invalid(bar) and said == "Invalid regex")
        p.check("No highlight stays in the Log", unhighlighted(view, matches) == [])

        p.step("Rest the mouse on the search field.")
        field = bar.field
        tip = tip_on(qtbot, field, QPoint(max(10, field.width() - 640), field.height() // 2))  # (all of it in view)
        p.shot(tip, f"① The count, in red: {said}. ② The field's tooltip says why: “{tip.text()}”.", over=win,
               highlight=[bar.widget.count, tip], badges=True, zoom=[tip, bar_end(win, bar)])
        p.check("① The count is red", red_pixels(bar.widget.count) >= 10)
        p.check("② The field's tooltip says why the pattern is not valid",
                tip.text().lower().startswith("not a valid regular expression: missing )"))
        QToolTip.hideText()

        p.step("Press Backspace, then Esc.")
        bar.key(Qt.Key_Backspace)
        again, red_again = bar.count(), red_pixels(bar.field) - valid_red
        bar.key(Qt.Key_Escape)
        p.check(f"Backspace: late is valid again, the red goes, and its matches are found again ({again}: Aa is still "
                "on)", again == "1/5" and red_again < 10 and not says_invalid(bar))
        p.shot(win, "Esc closed the bar: its highlights went, and the cursor is back in the Log.",
               zoom=[lines_of(win, view, matches[0][0], matches[3][0])])
        p.check("Esc closes the bar, its highlights go, and the cursor is back in the Log",
                not bar.shown() and unhighlighted(view, matches) == [] and view.hasFocus())

        # --- Data preview --------------------------------------------------------------------------------------
        p.step("Click load_sales on the canvas, then the Data preview tab, then the table's first cell, and press "
               "Ctrl+F.")
        click(tab, "load_sales")
        click_tab(win, win.preview)
        table = win.preview.view
        model = table.model()
        p.check("Data preview shows load_sales' table: its 40 rows", model is not None and model.rowCount() == 40)
        click_in(table.viewport(), table.visualRect(model.index(0, 0)).center())
        bar = press_ctrl_f()
        p.check("Ctrl+F opens the find bar under the table, with the cursor in its field",
                bar is not None and bar.field.hasFocus() and under(win, bar, table))
        p.check("Aa is still on, as you left it in the Log's bar: the toggles are shared by every find bar",
                bar.case.isChecked() and bar.regex.isChecked())

        p.step("Click Aa off, and type late.")
        bar.click(bar.case)
        bar.type("late")
        cells = cell_matches(table, "late")
        heads = table.horizontalHeader()
        days_late = next(c for c in range(model.columnCount())
                         if model.headerData(c, Qt.Horizontal).split("\n")[0] == "days_late")
        head = in_window(win, heads.viewport(), QRect(heads.sectionViewportPosition(days_late), 0,
                                                      heads.sectionSize(days_late), heads.height()))
        current = table.currentIndex()
        top_rows = in_window(win, table.viewport(), table.visualRect(model.index(9, 0)))  # down to row 10
        p.shot(win, f"The count reads {bar.count()}: ① the column header days_late, and 7 cells of payment. ② The "
                    "current match, row 4's paid late, is selected; the other cells found are on yellow.",
               highlight=[head, in_window(win, table.viewport(), table.visualRect(current))], badges=True,
               zoom=[head, top_rows])
        p.check(f"8 matches: the header days_late and 7 cells ({len(cells)} cells)",
                bar.total() == 8 and len(cells) == 7)
        p.check("② The current match is row 4's payment, 2/8: selected, in view and highlighted; the other cells "
                "found in view are highlighted too",
                bar.count() == "2/8" and (current.row(), current.column()) == (3, 3)
                and marked(check_cells, table, cells, 0) >= 1)

        p.step("Press Shift+Enter.")
        bar.key(Qt.Key_Return, Qt.ShiftModifier)
        p.shot(win, f"The count reads {bar.count()}: the current match is now ① the column header days_late, on blue; "
                    "no cell is selected.", highlight=[head], badges=True, zoom=[head, top_rows])
        p.check("The previous match is the header days_late: 1/8, and no cell is selected",
                bar.count() == "1/8" and not table.selectionModel().hasSelection())
        bar.key(Qt.Key_Escape)

        # --- Run history ---------------------------------------------------------------------------------------
        p.step(f"Right-click every_night, choose “{SCHEDULE_RUN}”, then press ▶ Run now.")
        p.note("archive_old, the block every_night starts, is set up to fail (“cannot reach the archive folder”), so "
               "that Run history has a failed run to find. No scheduler service runs during this proof: the "
               "Schedule line is started from its block's menu, as you would to try it out.")
        runs = win.history_panel.table
        qtbot.waitUntil(lambda: runs.rowCount() == 1, timeout=15_000)
        started = runs.item(0, 1).text()  # run 1's, to the second: the runs after start in a later second
        qtbot.waitUntil(lambda: dt.datetime.now().isoformat(" ", "seconds") > started, timeout=5_000)
        p.while_open(lambda: right_click(tab, "every_night"), lambda menu: choose(menu, SCHEDULE_RUN))
        qtbot.waitUntil(lambda: len(tab.runs) == 2 and not tab.running and tab.runs[1].status is not None,
                        timeout=120_000)
        third = run_now(win, tab, qtbot, runs=3)
        p.check("The second run fails, the third succeeds",
                tab.runs[1].status == "failed" and third.status == "success")

        p.step("Click the Run history tab, then its first row, press Ctrl+F and type failed.")
        click_tab(win, win.history_panel)
        qtbot.waitUntil(lambda: runs.rowCount() == 3, timeout=15_000)
        click_in(runs.viewport(), runs.visualItemRect(runs.item(0, 0)).center())
        bar = press_ctrl_f()
        p.check("Ctrl+F opens the find bar under the list, with the cursor in its field",
                bar is not None and bar.field.hasFocus() and under(win, bar, runs))
        bar.type("failed")
        failed = next(r for r in range(runs.rowCount()) if runs.item(r, 3).text() == "failed")
        listed = in_window(win, runs, QRect(0, 0, runs.columnViewportPosition(4) + runs.columnWidth(4) + 40,
                                            runs.horizontalHeader().height() + 3 * runs.rowHeight(0) + 4))
        p.shot(win, f"The count reads {bar.count()}: ① the failed run, run {runs.item(failed, 0).text()}, is "
                    "selected.", highlight=[(runs, failed)], badges=True, zoom=[listed, typed(win, bar)])
        p.check("1/1: the failed run's row is selected, whole",
                bar.count() == "1/1" and selected_rows(runs) == [failed]
                and all(runs.item(failed, c).isSelected() for c in range(runs.columnCount())))

        p.step("Type the time run 1 started, as Started shows it.")
        run_1 = next(r for r in range(runs.rowCount()) if runs.item(r, 0).text() == str(first.run_id))
        time = runs.item(run_1, 1).text().split(" ")[1]
        bar.type(time)
        p.shot(win, f"{time} is when run 1 started: the count reads {bar.count()}, and ① run 1's row is selected.",
               highlight=[(runs, run_1)], badges=True, zoom=[listed, typed(win, bar)])
        p.check(f"1/1: run 1's row is selected (it started at {time})",
                bar.count() == "1/1" and selected_rows(runs) == [run_1])
        bar.key(Qt.Key_Escape)

        # --- the code boxes ------------------------------------------------------------------------------------
        p.step("Click check_rows, click in its code box (in Properties), press Ctrl+F, type late and press Enter "
               "twice.")
        click(tab, "check_rows")
        box = code_box(win)
        code, undo = box.toPlainText(), box.document().isUndoAvailable()
        click_in(box.viewport(), QPoint(20, 10))
        bar = press_ctrl_f()
        p.check("Ctrl+F opens the find bar just under the code box, with the cursor in its field",
                bar is not None and bar.field.hasFocus() and under(win, bar, box))
        bar.type("late")
        found = text_matches(box, "late")
        bar.key(Qt.Key_Return)
        bar.key(Qt.Key_Return)
        k = bar.current()
        p.shot(win, f"The count reads {bar.count()}. ① The matches in view are highlighted: the late on the third "
                    "line on yellow, and the current one, the third, in \"Late: no payment yet\", on blue (the first "
                    "is at the end of the first line, out of view to the right; the other two are further down).",
               highlight=[box], badges=True,
               zoom=[in_window(win, box), in_window(win, bar.widget)])
        p.check(f"It finds late {len(found)} times in the code, and Enter goes from match to match, 3/{len(found)}",
                bar.total() == len(found) >= 4 and k == 2 and marked(check_text, box, found, k) >= 1)
        p.check("The code is the same: nothing was changed, nothing to undo",
                box.toPlainText() == code == CHECK and box.document().isUndoAvailable() == undo)

        p.step("Press Esc.")
        bar.key(Qt.Key_Escape)
        p.shot(win, "① The bar closed, the highlights went, and the cursor is back in the code box, where the current "
                    "match was.", highlight=[box], badges=True, zoom=[in_window(win, box)])
        p.check("Esc closes the bar, its highlights go, and the cursor is back in the code box",
                not bar.shown() and unhighlighted(box, found) == [] and box.hasFocus())
        p.check("The code and the flow are unchanged", box.toPlainText() == CHECK
                and tab.doc.blocks["check_rows"]["config"]["code"] == CHECK and not tab.doc.dirty)

        p.step("Click load_sales, click in its SQL box, press Ctrl+F and type late.")
        click(tab, "load_sales")
        box = code_box(win)
        click_in(box.viewport(), QPoint(20, 10))
        bar = press_ctrl_f()
        p.check("Ctrl+F opens the find bar just under the SQL box, with the cursor in its field",
                bar is not None and bar.field.hasFocus() and under(win, bar, box))
        bar.type("late")
        p.shot(win, f"① DuckDB SQL's SQL box has the same bar, just under it: the count reads {bar.count()}.",
               highlight=[box], badges=True, zoom=[in_window(win, box), in_window(win, bar.widget)])
        p.check("It finds late in the SQL, which stays the same",
                bar.total() == len(text_matches(box, "late")) == 3 and box.toPlainText() == SQL and not tab.doc.dirty)
        p.note("Every code box in Properties has this bar: Python Code's Code, Run Query's and DuckDB SQL's SQL, "
               "Polars Transform's Code, HTML Template's Template, If's Condition, Inspect's Message, and any code "
               "setting a block of your own has. Taskloom's tests open each of these and find in it.")
        bar.key(Qt.Key_Escape)

        # --- Dark ----------------------------------------------------------------------------------------------
        p.step("Choose Tools > Settings…, set Theme to Dark and press Save.")
        set_theme(p, win, "Dark", shoot=False)
        p.step("Click check_rows, click in the Log, press Ctrl+F and type late.")
        click(tab, "check_rows")
        click_tab(win, win.log)
        click_in(view.viewport(), QPoint(60, view.viewport().height() - 8))
        bar = press_ctrl_f()
        p.check("The editor is dark, and Ctrl+F opens the Log's find bar",
                QApplication.palette().window().color().lightness() < 128 and bar is not None)
        bar.type("late")
        matches = text_matches(view, "late")
        k = bar.current()
        p.shot(win, f"In Dark, the Log shows check_rows' lines of runs 1 and 3, so the count reads {bar.count()}. The "
                    "current match, row 4's late at the top, is on dark blue, the others on dark green. The text reads "
                    "well on each.",
               zoom=[lines_of(win, view, matches[k][0], matches[k + 3][0]), typed(win, bar)])
        shown = [where(view, *m) for m in matches if in_view(view, where(view, *m))]
        p.check("Every match in view is highlighted, the current one in a colour of its own",
                marked(check_text, view, matches, k) >= 1)
        p.check(f"The text reads on every highlight: a contrast of at least 4.5 to 1 ({len(shown)} in view)",
                len(shown) >= 2 and all(text_on(view, rect) >= TEXT for rect in shown))

        p.step("Type ( after it.")
        QTest.keyClicks(bar.field, "(")
        settle()
        on_field, on_count = red_contrast(bar.field), red_contrast(bar.widget.count)
        p.shot(win, "① late( in red on the dark field. No highlight stays.", highlight=[typed(win, bar, 60)],
               badges=True, zoom=[lines_of(win, view, matches[k][0], matches[k + 3][0]), typed(win, bar)])
        p.shot(win, f"① The count says {bar.widget.count.text()}, in the same red, on the dark bar.",
               highlight=[bar.widget.count], badges=True, zoom=[bar_end(win, bar)])
        p.check(f"The red stands out from the dark field and bar: a contrast of {on_field:.1f} and {on_count:.1f} to "
                "1, at least 3 to 1", on_field >= MARK and on_count >= MARK and says_invalid(bar))
        p.check("No highlight stays in the Log", unhighlighted(view, matches) == [])
        bar.key(Qt.Key_Escape)

        p.step("Set Theme back to System (Tools > Settings…), and press Save.")
        set_theme(p, win, "System", shoot=False)
        p.shot(win, "The editor has its light colours again.")
        p.check("The Theme is System again, and the editor light",
                theme.chosen(win.settings) == "System" and QApplication.palette().window().color().lightness() >= 128)
        p.note("Not shown here: that .* is on and Aa off again when the editor starts next. Taskloom's tests turn them "
               "over, start the editor again in a program of its own, and check it.")
    finally:  # the Theme and the application's look as the proof found them
        win.settings.setValue("theme", "System")
        theme.apply("System")
        if app.style().name() != as_found[0]:
            QApplication.setStyle(as_found[0])
        QApplication.setPalette(as_found[1])
        QToolTip.setPalette(as_found[2])
        QApplication.processEvents()
