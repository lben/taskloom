"""Proof: Data preview on a block that failed with a very long error keeps the window as it is. See README.md here."""

import shutil

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from proofs.test_proof_failure_inputs import finished, open_flow, press_run_now, scroll_log_to
from proofs.test_proof_html_template_formatting import click_block

ASKED = ("Clicking Data preview on a flow or block whose Run Query failed made the canvas stop showing blocks, for "
         "that flow and others, and the editor slow, with nothing on the command line")
IDS = 486  # order numbers in the query
# What a database says when it refuses a long query: the whole statement, on one line. 3,000 characters.
ERROR = ("the database refused the query: SELECT id, item, qty, price FROM orders WHERE id IN ("
         + ", ".join(str(n) for n in range(1001, 1001 + IDS)) + ")")
LABEL = "load_orders: failed, no output"


def python(code: str, x: int, inputs=(), outputs=("output",)) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code},
            "ui": {"x": x, "y": 0}}


ORDERS = {
    "blocks": {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
               "load_orders": python(f"raise RuntimeError({ERROR!r})", 260, outputs=("result",)),
               "clean": python("result = orders", 520, inputs=("orders",), outputs=("result",))},
    "edges": ["start.start -> load_orders.after", "load_orders.result -> clean.orders"],
}
REPORT = {
    "blocks": {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
               "report": python("output = 'no orders yet'", 260),
               "send": python("print(text)", 520, inputs=("text",), outputs=())},
    "edges": ["start.start -> report.after", "report.output -> send.text"],
}
PRICES = {
    "blocks": {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
               "prices": {"type": "data.duckdb_sql", "ui": {"x": 260, "y": 0}, "config": {
                   "inputs": [], "sql": "select * from (values ('pen', 1.20), ('ink', 4.50)) as prices(item, price)"}}},
    "edges": ["start.start -> prices.after"],
}


def place(win, tab, block: str) -> QRect:
    """Where a block is drawn, in the window's pixels."""
    drawn = tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect()).boundingRect()
    return QRect(tab.view.viewport().mapTo(win, drawn.topLeft()), drawn.size())


def click_tab(bar, text: str):
    """Click the tab that reads `text` in a tab bar."""
    index = next(i for i in range(bar.count()) if bar.tabText(i) == text)
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(index).center())
    QTest.qWait(100)
    assert bar.currentIndex() == index


def in_window(win, widget, width: int | None = None) -> QRect:
    """A widget in the window's pixels: all of it, or its first `width` pixels from the left (its last ones, from the
    right, when `width` is negative)."""
    rect = widget.rect()
    if width is not None and abs(width) < rect.width():
        rect = QRect(0, 0, width, rect.height()) if width > 0 else QRect(rect.width() + width, 0, -width, rect.height())
    return QRect(widget.mapTo(win, rect.topLeft()), rect.size())


def line(win, label) -> QRect:
    """The line above the box, in the window's pixels: from the label's left, 300 pixels wide (its badge clears the
    tabs above it)."""
    return QRect(label.mapTo(win, QPoint(0, 0)), QSize(300, label.height()))


def test_data_preview_of_a_long_error_keeps_the_window_as_it_is(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Data preview of a failed block keeps the window and the blocks where they are", ASKED)
    p.idea("Data preview now shows a failed block's error in a box that scrolls, under a short line that says which "
           "block failed. The window keeps its size and the blocks stay where they were, however long the error is.")
    p.idea("Before, the error was written on that one line above the box, whole. A database's error often repeats "
           "the whole query, on one line: thousands of characters. A window cannot be narrower than a line of text "
           "it shows, so the window grew many times wider than the screen. The canvas grew with it, and its blocks "
           "ended up far outside what you could see, in that flow's tab and in every other.")
    p.idea("Now that line only reads “load_orders: failed, no output”. The whole error is in the box below it, every "
           "character as written, to scroll, select and copy. The same goes for a table that cannot be shown: the "
           "line reads “Cannot preview” and the reason is in the box.")
    p.why_simplest("The box was already there: Data preview uses it for a text result. It scrolls, sideways and "
                   "down, so it never asks for more room than it has. Putting the error in it needed nothing new.")
    p.alternative("Keep the error on the line and cut it short with “…”",
                  "You would no longer see the end of the error, which is often where the cause is, and you could "
                  "not copy it whole.")
    p.alternative("Keep the error on the line and let the line wrap",
                  "A long error would then push the box and the table down, or out of the panel. A box that scrolls "
                  "keeps the panel's height too.")
    p.fits("The Log still shows the failure report as before. Data preview shows the same error text, and the "
           "line above it reads as it does for a block with no output: “block: status, no output”.")
    p.fits("A text result is shown in that same box, with Find (Ctrl+F) and copy. An error now gets the same.")

    p.step("Open report.yaml and orders.yaml (File > Open…). In orders.yaml press ▶ Run now. load_orders fails: the "
           "database refuses its query, and the error repeats the query, 3,000 characters on one line.")
    other = open_flow(win, monkeypatch, tmp_path / "report.yaml", REPORT)
    tab = open_flow(win, monkeypatch, tmp_path / "orders.yaml", ORDERS)
    press_run_now(win)
    run = finished(qtbot, tab, 1)
    click_tab(win.bottom_tabs.tabBar(), "Log")
    win.tabs.setCurrentWidget(other)
    QTest.qWait(100)
    there = place(win, other, "report")
    win.tabs.setCurrentWidget(tab)
    QTest.qWait(100)
    size, here = win.size(), place(win, tab, "load_orders")
    scroll_log_to(win, run, "the database refused the query")
    p.shot(win, "① load_orders failed. ② The Log shows its failure report: the error is one very long line. The "
                "window is 1600 × 1000.",
           highlight=["load_orders", win.log.text], badges=True,
           zoom=["start", "load_orders", "clean"])
    p.check("load_orders failed, with an error of 3,000 characters on one line",
            tab.scene.nodes["load_orders"].status == "failed" and len(ERROR) == 3000 and "\n" not in ERROR
            and ERROR in win.log.text.toPlainText())

    p.step("Click load_orders, then click the Data preview tab, under the canvas.")
    click_block(tab, "load_orders")
    click_tab(win.bottom_tabs.tabBar(), "Data preview")
    preview = win.preview
    p.shot(win, "① The line is short: “load_orders: failed, no output”. ② The error is in the box under it, on its "
                "one line, with a bar to scroll it sideways. The window is still 1600 × 1000, and ③ the blocks are where they were.",
           highlight=[line(win, preview.label), preview.text, ["start", "load_orders", "clean"]], badges=True,
           zoom=[in_window(win, preview.label, 700), in_window(win, preview.text, 700)])
    p.note("Before this change the window became as wide as the error's one line here, many times the screen's "
           "width: the canvas showed no blocks any more, in this tab and in report.yaml's.")
    p.check("The window has the size it had: 1600 × 1000",
            (win.width(), win.height()) == (size.width(), size.height()) == (1600, 1000))
    p.check("load_orders is where it was on the canvas", place(win, tab, "load_orders") == here)
    p.check("① The line reads “load_orders: failed, no output”", preview.label.text() == LABEL)
    p.check("② The box is shown and holds every character of the error, as written",
            preview.text.isVisible() and ERROR in preview.text.toPlainText())

    p.step("Drag the bar under the box all the way to the right, to the end of the error.")
    bar = preview.text.horizontalScrollBar()
    bar.setValue(bar.maximum())
    QTest.qWait(100)
    p.shot(win, "The end of the error, in the box: the query's last numbers and the line of the code it failed on. "
                "Nothing is cut.",
           highlight=[preview.text], zoom=[in_window(win, preview.text, -700)])
    p.check("The box scrolls sideways: the error is longer than what it shows at once", bar.maximum() > 0)

    p.step("Press Ctrl+A and Ctrl+C in the box: the whole error is copied.")
    preview.text.setFocus()
    QTest.keyClick(preview.text, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(preview.text, Qt.Key_C, Qt.ControlModifier)
    p.check("What was copied holds the whole error", ERROR in QApplication.clipboard().text())

    p.step("Click the tab of the other flow, report.yaml.")
    click_tab(win.tabs.tabBar(), win.tabs.tabText(win.tabs.indexOf(other)))
    p.shot(win, "report.yaml's blocks are where they were too: start, report and send, at the middle of the canvas.",
           highlight=[["start", "report", "send"]])
    p.note("Data preview still shows load_orders here: it keeps the block you clicked last until you click "
           "another one.")
    p.check("The window is still 1600 × 1000", (win.width(), win.height()) == (1600, 1000))
    p.check("report is where it was on its canvas", place(win, other, "report") == there)
    for each in win.tabs_list():  # nothing to save when the window closes
        each.doc.dirty = False


def test_a_table_that_cannot_be_shown_says_why_in_the_box(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("“Cannot preview” says why in the box, and the window keeps its size", ASKED)
    p.idea("When Data preview cannot show a block's table, the line above the box reads “Cannot preview”, and the "
           "reason is in the box under it: which file could not be read, and why that happens.")
    p.idea("Before, the reason was on that line, after “Cannot preview:”. It holds the file's whole path and an "
           "explanation, on one line, so the window grew wider with it, as it did for a failed block's long error.")
    p.why_simplest("It is the same change as for a failed block: the line stays short, the long text goes into the "
                   "box that already wraps and scrolls.")
    p.alternative("Shorten the reason so it fits the line",
                  "The path of the file and the setting that decides how long a run's files are kept are what you "
                  "need to act on it. They would be the first words to go.")
    p.fits("A failed block and a table that cannot be read now look alike in Data preview: a short line, and the "
           "details in the box.")

    p.step("Open prices.yaml and press ▶ Run now. prices, a Run SQL on tables block, makes a table of two rows.")
    tab = open_flow(win, monkeypatch, tmp_path / "prices.yaml", PRICES)
    press_run_now(win)
    run = finished(qtbot, tab, 1)
    click_block(tab, "prices")
    click_tab(win.bottom_tabs.tabBar(), "Data preview")
    preview = win.preview
    p.shot(win, "① prices succeeded, and Data preview shows ② its table: 2 rows × 2 columns.",
           highlight=["prices", line(win, preview.label)], badges=True, zoom=[in_window(win, preview, 700)])
    p.check("Data preview shows the table of prices: 2 rows × 2 columns",
            preview.label.text().startswith("prices.") and "2 rows × 2 columns" in preview.label.text()
            and preview.view.isVisible())

    p.step("Time passes: Taskloom cleans up that run's files, as Keep runs for says. Click start, then prices again.")
    folder = win.home.runs_dir / str(run)
    tables = list(folder.rglob("*.parquet"))
    shutil.rmtree(folder)
    click_tab(win.bottom_tabs.tabBar(), "Log")
    click_block(tab, "start")
    size, here = win.size(), place(win, tab, "prices")
    click_block(tab, "prices")
    click_tab(win.bottom_tabs.tabBar(), "Data preview")
    reason = preview.text.toPlainText()
    p.shot(win, "① The line reads “Cannot preview”. ② The box says why: the file of the table is gone, the run's "
                "files were cleaned up. The window is still 1600 × 1000.",
           highlight=[line(win, preview.label), preview.text], badges=True,
           zoom=[in_window(win, preview.label, 700), in_window(win, preview.text, 700)])
    p.note("The proof deletes the run's folder itself, which is what the clean-up does once a run is older than Keep "
           "runs for allows.")
    p.text("The reason, as the box holds it", reason)
    p.check("① The line reads “Cannot preview”, and nothing more", preview.label.text() == "Cannot preview")
    p.check("② The box is shown, names the file that could not be read and says the run's files were cleaned up",
            preview.text.isVisible() and len(tables) == 1 and tables[0].name in reason and "cleaned up" in reason)
    p.check("The window is still 1600 × 1000, and prices is where it was",
            (win.width(), win.height()) == (size.width(), size.height()) == (1600, 1000)
            and place(win, tab, "prices") == here)
    for each in win.tabs_list():
        each.doc.dirty = False
