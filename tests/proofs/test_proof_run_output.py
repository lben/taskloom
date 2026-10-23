"""Proofs: what a run shows (batch 1), one story for each thing asked. A driver's own messages are plain output in
the Log and a block's Log line has its table's file path; hovering a table shows its first and last rows; charts
take axis labels; To Excel's Save dialog offers only Excel workbooks. See README.md here.

Each flow reads its table with a Run Query block from a real database (H2, through its Java driver), as the person
who asked does: the `jdbc_env` fixture needs Java."""

from pathlib import Path

import polars as pl
import yaml
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QHelpEvent, QImage, QPixmap, QTextCursor, QTextFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QComboBox, QDialogButtonBox, QFileDialog, QFormLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QToolBar, QToolButton)

from taskloom.editor.canvas import STATUS_COLORS
from taskloom.report import render_chart

SQL = """\
select 'PS-' || (100 + x) as "publishing_system_id",
       cast(mod(x * 37, 23) + 5 as int) as "articles",
       cast(mod(x * 7919, 900) + 300 as int) as "total_sales"
from system_range(1, 15)
"""
BLOCKS = {  # sales reads 15 rows; the flows of the stories connect chart or to_excel after it
    "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 120}},
    "sales": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": SQL}, "ui": {"x": 260, "y": 120}},
    "chart": {"type": "reports.chart", "config": {"kind": "bar", "x": "publishing_system_id", "y": ["total_sales"],
                                                  "title": "Sales by publishing system"}, "ui": {"x": 540, "y": 120}},
    "to_excel": {"type": "reports.excel", "ui": {"x": 540, "y": 120}},
}
COLUMNS = ["publishing_system_id", "articles", "total_sales"]
RED = STATUS_COLORS["failed"]  # how the Log shows an error
DRIVER = "Picked up JAVA_TOOL_OPTIONS"  # what Java prints as it starts, when that variable is set


def open_flow(win, monkeypatch, folder: Path, *after_sales: str):
    """Open sales-report.yaml (File > Open…): start_by_hand, sales, and the blocks `after_sales` fed by it."""
    flow = folder / "sales-report.yaml"
    flow.write_text(yaml.safe_dump({
        "blocks": {name: BLOCKS[name] for name in ("start_by_hand", "sales", *after_sales)},
        "edges": ["start_by_hand.start -> sales.after", *(f"sales.result -> {name}.table" for name in after_sales)],
    }, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    tab = win.current
    assert tab is not None and tab.doc.path == flow.resolve(), "the flow opens"
    return tab


def click(tab, block=None):
    """Click a block on the canvas, as you would; no block: an empty spot of the canvas."""
    view, viewport = tab.view, tab.view.viewport()
    if block is not None:
        at = view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center())
    else:
        at = viewport.rect().bottomRight() - QPoint(40, 40)
        assert view.itemAt(at) is None, "the spot to click is empty"
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, at)
    QTest.qWait(50)


def setting(win, label: str) -> tuple:
    """The name and the field of the selected block's setting `label`, as Properties shows them."""
    found = [(form.itemAt(row, QFormLayout.LabelRole).widget(), form.itemAt(row, QFormLayout.FieldRole).widget())
             for form in win.properties.widget().findChildren(QFormLayout) for row in range(form.rowCount())
             if (name := form.itemAt(row, QFormLayout.LabelRole)) and name.widget().text() == label]
    assert len(found) == 1, f"Properties shows {len(found)} settings named {label}"
    return found[0]


def type_into(edit: QLineEdit, text: str):
    """Click a field, type `text` over what it has and press Enter, as you would."""
    QTest.mouseClick(edit, Qt.LeftButton)
    edit.selectAll()
    QTest.keyClicks(edit, text)
    QTest.keyClick(edit, Qt.Key_Return)
    QTest.qWait(50)


def run_now(win, tab, qtbot, runs: int = 1):
    """Press ▶ Run now; wait for the flow's run number `runs` (of this proof) to end."""
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == runs and not tab.running and tab.runs[-1].status is not None,
                    timeout=120_000)
    QTest.qWait(200)  # what the run printed last reaches the Log
    return tab.runs[-1]


def log_entry(win, text: str, run: int) -> dict:
    """The Log's entry of run #`run` whose message has `text`."""
    entries, current = [], False
    for e in win.log.entries:
        if e.get("header") and "started" in e.get("message", ""):
            current = f"run #{run} · " in e["message"]
        if current and text in e.get("message", ""):
            entries.append(e)
    assert len(entries) == 1, (text, run, win.log.entries)
    return entries[0]


def colors(win, text: str) -> set:
    """The colors the Log draws the last line with `text` in."""
    lines = []
    block = win.log.text.document().begin()
    while block.isValid():
        line, it = [], block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            color = fmt.foreground().color().name() if fmt.hasProperty(QTextFormat.ForegroundBrush) else ""
            for i, part in enumerate(it.fragment().text().split("\u2028")):  # a line break inside one entry
                if i:
                    lines.append(line)
                    line = []
                line.append((part.replace("\xa0", " "), color))
            it += 1
        lines.append(line)
        block = block.next()
    return [{c for _, c in line if c} for line in lines if text in "".join(part for part, _ in line)][-1]


def hover(tab, item, qtbot):
    """Rest the mouse on a canvas item: the tooltip it shows."""
    viewport = tab.view.viewport()
    at = tab.view.mapFromScene(item.sceneBoundingRect().center())
    QApplication.sendEvent(viewport, QHelpEvent(QHelpEvent.ToolTip, at, viewport.mapToGlobal(at)))
    qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
    return tooltip()


def tooltip():
    return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)


def first_and_last_rows(shown: str) -> bool:
    """`shown` has sales' first 5 rows, a row of … and its last 5 rows, and not the 5 in between."""
    return (all(f"PS-{n}" in shown for n in [*range(101, 106), *range(111, 116)])
            and not any(f"PS-{n}" in shown for n in range(106, 111))
            and "…" in shown[shown.index("PS-105"):shown.index("PS-111")])


def test_driver_messages_are_plain_output_and_the_log_names_table_files(jdbc_env, proof, win, qtbot, monkeypatch,
                                                                         tmp_path):
    p = proof("Driver messages are plain output; table paths in the Log",
              "Run Query block: Normal JDBC messages shown as ERROR on the log despite not being actual errors … It "
              "would be nice if the log output of the block also included the Table repr so that I can copy the "
              "parquet file path from the log output")
    p.idea("What a database driver prints during a run shows in the Log as plain output, not as a red ERROR. And a "
           "block that makes a table now gets a line in the Log that ends with the full path of the table's file, to "
           "copy.")
    p.idea("A run is a separate program that tells the editor what happens in messages of its own. Anything else it "
           "prints comes from the libraries the blocks use: the Java driver, Java itself, a Python package. The Log "
           "shows it as it is, marked OUTPUT. Taskloom's own errors stay red: a block that fails, a run it refuses "
           "to start, a run whose program stops unexpectedly.")
    p.idea("A table a block makes is kept as a Parquet file in the run's folder. When a block that made a table "
           "finishes, the Log now shows a new line for it: its status and what it made, “success · result: 15 rows × "
           "3 columns”, then the full path of each table's file, to copy. A block that makes no table gets no such "
           "line. On the command line, the line taskloom run prints for such a block now ends with that path too.")
    p.why_simplest("The runner already says everything Taskloom means to say in its own messages, and the lines it "
                   "prints when it refuses a run start with “error:” or “warning:”. So one rule sorts the rest: those "
                   "lines are red, and anything else is what the libraries print. No list of drivers, or of the ways "
                   "they write.")
    p.why_simplest("A block's result already carried the paths of its tables' files: Data preview reads them from "
                   "there. The new Log line prints them after the block's status and what it made, the same words "
                   "taskloom run prints on the command line.")
    p.alternative("Recognising each driver's messages (INFO, WARN…) and colouring them",
                  "Every driver and library writes its own way: such a list would never be complete, and would "
                  "guess wrong. Whether a line is Taskloom's own is known for sure.")
    p.alternative("Hiding what the libraries print",
                  "A driver's real warning would be hidden with the rest.")
    p.alternative("Printing the table's rows in the Log",
                  "The Log would fill with rows, which hovering the table and Data preview already show. The path is "
                  "what you copy, and it opens the whole table.")
    p.fits("A library's output belongs to the run, not to one block: it shows when the Log shows all blocks, and, as "
           "before, it is not kept in the run history. The path line belongs to its block: selecting the block "
           "shows it with the block's other lines.")
    p.fits("taskloom run prints the same line on the command line, and a run reopened from Run history shows it too.")
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Xmx256m")  # (the runs the editor starts inherit it)

    p.step("Open the flow sales-report.yaml (File > Open…), and press ▶ Run now. Its Run Query block sales reads 15 "
           "rows of sales by publishing system from a database, through the database's Java (JDBC) driver.")
    p.note("Java prints “Picked up JAVA_TOOL_OPTIONS: …” as it starts when that setting is in the environment, as it "
           "is for this proof: a normal message, printed straight to the run's output, the way a driver prints its "
           "own messages.")
    tab = open_flow(win, monkeypatch, tmp_path)
    run = run_now(win, tab, qtbot)
    table = tab.block_info["sales"]["tables"]["result"]
    driver, table_line = (win.log.text, DRIVER), (win.log.text, table)
    p.shot(win, "In the Log: ① Java's message is plain text, marked OUTPUT, not a red ERROR. ② sales' line ends "
                "with the full path of its table's file (the next step shows it close up).",
           highlight=[driver, table_line], badges=True,
           zoom=[(win.log.text, f"run #{run.run_id} · "), driver, (win.log.text, "query returned 15 rows")])
    p.check("The run ended with success", run.status == "success")
    p.check("① Java's message shows as plain output (OUTPUT), not as an error, and is not red",
            log_entry(win, DRIVER, run.run_id)["level"] == "OUTPUT" and RED not in colors(win, DRIVER))
    line = log_entry(win, "success · result: 15 rows × 3 columns", run.run_id)
    p.check("② sales' line says success and the table's size, and ends with the full path of the table's file",
            line.get("block") == "sales" and line["message"].endswith(f" · {table}") and Path(table).is_absolute())

    p.step("Select the file path at the end of sales' line, and copy it (Ctrl+C).")
    path = win.log.text.document().find(table)
    at = QTextCursor(path)
    at.setPosition(path.selectionStart())
    QTest.mouseClick(win.log.text.viewport(), Qt.LeftButton, Qt.NoModifier, win.log.text.cursorRect(at).center())
    win.log.text.setTextCursor(path)  # (dragged over from that click to the path's end)
    QTest.keyClick(win.log.text, Qt.Key_C, Qt.ControlModifier)
    copied = QGuiApplication.clipboard().text()
    p.shot(win, "② The path, selected at the end of sales' line, after the table's size.", highlight=[table_line],
           badges=[2], zoom=[table_line])
    p.text("What was copied:", copied)
    copy = pl.read_parquet(copied) if Path(copied).is_file() else None
    p.check("② The copied path is the file of sales' table: its 15 rows of publishing_system_id, articles and "
            "total_sales", copy is not None and copy.height == 15 and copy.columns == COLUMNS)
    win.log.text.moveCursor(QTextCursor.End)  # (the selection goes)

    p.step("Then a real error: change sales' SQL to read a table that does not exist, and run again. Click sales, "
           "type the SQL, click an empty spot of the canvas and press ▶ Run now. The Log is scrolled up to where this "
           "second run starts.")
    click(tab, "sales")
    [sql] = win.properties.findChildren(QPlainTextEdit)
    win.activateWindow()  # (the SQL you type is kept as it loses the focus, which needs the window to be active)
    assert QTest.qWaitForWindowActive(win)
    QTest.mouseClick(sql.viewport(), Qt.LeftButton)
    sql.selectAll()
    QTest.keyClicks(sql, "select * from sales_2025")
    click(tab)
    p.check("sales' SQL is now select * from sales_2025",
            tab.doc.blocks["sales"]["config"]["sql"] == "select * from sales_2025")
    second = run_now(win, tab, qtbot, runs=2)
    start = win.log.text.document().find(f"run #{second.run_id} · ")
    start.clearSelection()
    win.log.text.setTextCursor(start)
    win.log.text.ensureCursorVisible()
    java = f"{log_entry(win, DRIVER, second.run_id)['ts'][11:19]} OUTPUT {DRIVER}"  # (the first run's is earlier)
    assert win.log.text.toPlainText().count(java) == 1, java
    driver_2, error = (win.log.text, java), (win.log.text, "sales (Run Query) failed")
    p.shot(win, "In the second run, ① Java's message is plain output again, and ② the database's error is a red "
                "ERROR: sales failed, as the table SALES_2025 is not found.",
           highlight=[driver_2, error], badges=True, zoom=[(win.log.text, f"run #{second.run_id} · "), driver_2, error])
    p.check("The second run failed", second.status == "failed")
    p.check("① Java's message is plain output in this run too, not red",
            log_entry(win, DRIVER, second.run_id)["level"] == "OUTPUT" and RED not in colors(win, java))
    p.check("② The database's error is an ERROR, in red",
            log_entry(win, "sales (Run Query) failed", second.run_id)["level"] == "ERROR"
            and RED in colors(win, "sales (Run Query) failed"))
    p.note("One case is less visible than before: if Taskloom's own runner broke before a run had begun, what it "
           "printed (a Python traceback) now shows as plain output too. No run is recorded then, so the flow's tab "
           "goes on showing how its last run ended: the Log is where you see it.")


def test_hovering_a_table_shows_its_first_and_last_rows(jdbc_env, proof, win, qtbot, monkeypatch, tmp_path):
    p = proof("Table previews show the first and last rows",
              "Lets make the output result tooltip on hover display some head rows, an ellipsis (. . .), some tail "
              "rows instead of just the first rows")
    p.idea("Hovering a table shows its first rows, a row of …, and its last rows, instead of only its first rows.")
    p.idea("A table of 10 rows or fewer shows all of them. Over 10, it shows the first 5, a row of …, and the last "
           "5, under the table's size (here 15 rows × 3 columns).")
    p.why_simplest("Polars, the library that holds Taskloom's tables, already prints a long table this way: its first "
                   "rows, a row of …, its last rows. The preview takes just the table's first 6 and last 5 rows from "
                   "its file, and lets Polars lay out 10 of them: the first 5, the row of …, the last 5.")
    p.alternative("More rows, say the first 20 and the last 20",
                  "The tooltip would become a tall block over most of the canvas. Data preview, under the canvas, "
                  "shows up to 200 rows to scroll through.")
    p.alternative("Laying out the rows and columns ourselves",
                  "More code to keep, for what Polars already does well.")
    p.fits("The same preview shows wherever you hover a table: an output, an input, a connection, and the result "
           "under a block. As before, a password in the rows shows as ***, and Data preview still shows up to 200 "
           "rows.")

    p.step("Open the flow sales-report.yaml (File > Open…), and press ▶ Run now. sales reads 15 rows of sales by "
           "publishing system from a database; chart takes them.")
    tab = open_flow(win, monkeypatch, tmp_path, "chart")
    run = run_now(win, tab, qtbot)
    p.check("The run ended with success", run.status == "success")

    p.step("Rest the mouse on sales' result output, on its right side.")
    sales, chart = tab.scene.nodes["sales"], tab.scene.nodes["chart"]
    tip = hover(tab, sales.ports[("result", True)], qtbot)
    shown = tip.text()
    p.shot(tip, "Its size, 15 rows × 3 columns, then its first 5 rows (PS-101 to PS-105), a row of …, and its last 5 "
                "rows (PS-111 to PS-115).", over=win, highlight=[tip], zoom=[tip])
    tip.hide()
    p.check("It shows the first 5 rows, a row of …, and the last 5, and not the 5 in between",
            first_and_last_rows(shown))
    p.check("It says the table has 15 rows and 3 columns", "15 rows × 3 columns" in shown)
    p.check("The same rows show when hovering chart's table input, the connection, and the result under sales",
            all(first_and_last_rows(text) for text in (chart.ports[("table", False)].toolTip(),
                                                        tab.scene.edge_items[-1].toolTip(), sales.footer.toolTip())))


def test_a_charts_axes_show_the_labels_you_give(jdbc_env, proof, win, qtbot, monkeypatch, tmp_path):
    p = proof("Chart axes take your labels",
              "Chart block: ... add Optional labels so if a column is called something like publishing_system_id "
              "it can be shown on the chart as 'Publishing System ID'")
    p.idea("The Chart block has two new settings, X label and Y label: the names its axes show.")
    p.idea("They sit under X and Y in Properties. Left empty, the axes are named after the columns, as before. With "
           "horizontal bars the names follow their axes (the categories go up the side); a pie chart has no axes "
           "to name.")
    p.why_simplest("The chart already named its axes, after the columns. Now it uses your name for an axis when you "
                   "give one, and the column's when you do not: two optional settings, and nothing else to learn.")
    p.alternative("Turning column names into words by themselves (publishing_system_id → Publishing System Id)",
                  "It cannot know that ID is written in capitals, or which name you want; and every chart's axes "
                  "would change without asking.")
    p.alternative("Renaming the columns first, e.g. in the SQL",
                  "That works today, but it renames the column for every block after it (an Excel sheet, an email "
                  "table), and the SQL gets longer for a chart's sake.")
    p.fits("X label and Y label are settings like any other: kept in the flow file, shown in Properties, named the "
           "way Properties names every setting. The chart is still a picture for an email or a report.")

    p.step("Open the flow sales-report.yaml (File > Open…), click chart, and type the names its axes are to show: "
           "Publishing System ID in X label, and Total Sales in Y label.")
    tab = open_flow(win, monkeypatch, tmp_path, "chart")
    click(tab, "chart")
    type_into(setting(win, "X label")[1], "Publishing System ID")
    type_into(setting(win, "Y label")[1], "Total Sales")
    x, x_label, y_label = setting(win, "X"), setting(win, "X label"), setting(win, "Y label")
    p.shot(win, "In Properties, ① X label is just under X (the column publishing_system_id), and ② Y label just "
                "under Y (the column total_sales).", highlight=[list(x_label), list(y_label)], badges=True,
           zoom=[*x, *y_label])
    config = tab.doc.blocks["chart"]["config"]
    p.check("chart's X label is Publishing System ID, and its Y label Total Sales",
            (config.get("x_label"), config.get("y_label")) == ("Publishing System ID", "Total Sales"))

    p.step("Press ▶ Run now, and open the picture chart drew: its file is chart's result.")
    p.note("The editor does not show pictures: the Chart block's result is a picture file, for an email or a "
           "report. Here it is, opened from that file.")
    run = run_now(win, tab, qtbot)
    p.check("The run ended with success", run.status == "success")
    image = tab.block_info["chart"]["previews"]["image"]
    picture = QLabel(pixmap=QPixmap(image))
    picture.resize(picture.sizeHint())
    p.shot(picture, "The x axis says Publishing System ID, and the y axis Total Sales: the names you typed, not the "
                    "column names publishing_system_id and total_sales.")
    data = pl.read_parquet(tab.block_info["sales"]["tables"]["result"])

    def drawn(x_label: str, y_label: str) -> QImage:
        out = render_chart(data, tmp_path / f"chart-{len(x_label)}.png", "bar", "publishing_system_id",
                           ["total_sales"], "Sales by publishing system", 640, 360, x_label, y_label)
        return QImage(str(out))

    p.check("The picture is the chart drawn with the labels Publishing System ID and Total Sales, not with the "
            "column names", QImage(image) == drawn("Publishing System ID", "Total Sales")
            and QImage(image) != drawn("", ""))


def test_to_excels_save_dialog_offers_only_xlsx_and_adds_it(jdbc_env, proof, win, qtbot, monkeypatch, tmp_path):
    p = proof("To Excel's Save dialog offers only .xlsx",
              "To Excel: The 'Save as type' should be defaulted only to approved extensions (likely only to xlsx) so "
              "that I just write a name without an extension and it will create the file with the right extension")
    p.idea("The … button next to To Excel's Path opens a Save dialog that offers only Excel workbook (*.xlsx). A "
           "name typed without an extension gets .xlsx.")
    p.idea("A name you type with an extension is kept as you typed it. Write File's Save dialog offers CSV, Excel "
           "and Parquet, and adds the extension of the one you choose.")
    p.why_simplest("Each file setting now says which kinds of file it takes. The … button hands that list to the "
                   "Save dialog and, when the name you chose has no extension, adds the one of the kind chosen in the "
                   "dialog. Settings that take any file keep a dialog that shows every file.")
    p.alternative("Adding .xlsx when the block writes the file",
                  "Properties would show one name and the run would write another. Settling the name when you choose "
                  "it keeps them the same.")
    p.alternative("Refusing a name without an extension",
                  "One more message to read and fix, for something Taskloom can do for you.")
    p.fits("Write File's Save dialog works the same way, with CSV file, Excel workbook and Parquet file. The Path is "
           "still a setting you can also type in Properties.")

    p.step("Open the flow sales-report.yaml (File > Open…), click to_excel, then the … button next to its Path, and "
           "in the Save dialog, go to the folder reports and open the list Files of type.")
    p.note("No screen is used during this proof, so Qt draws its own Save dialog, where the list of kinds of file is "
           "called Files of type. On your Windows desktop the usual Windows Save dialog opens, with the same list "
           "under Save as type.")
    reports = tmp_path / "reports"
    reports.mkdir()
    tab = open_flow(win, monkeypatch, tmp_path, "to_excel")
    click(tab, "to_excel")
    [browse] = setting(win, "Path")[1].findChildren(QToolButton)

    def save_as(dialog):
        assert isinstance(dialog, QFileDialog), dialog
        types = dialog.findChild(QComboBox, "fileTypeCombo")
        dialog.setDirectory(str(reports))
        QTest.qWait(300)
        types.showPopup()
        listed = types.view().window()
        qtbot.waitUntil(listed.isVisible, timeout=5_000)
        p.shot(listed, "① The list Files of type, open: the only kind of file it offers is Excel workbook (*.xlsx), "
                       "with nothing above or below it.", over=dialog, highlight=[listed], badges=True)
        p.check("① The Save dialog offers one kind of file: Excel workbook (*.xlsx)",
                [types.itemText(i) for i in range(types.count())] == ["Excel workbook (*.xlsx)"])
        types.hidePopup()
        qtbot.waitUntil(lambda: not listed.isVisible(), timeout=5_000)
        p.step("Close the list, and type the name sales_by_system, with no extension.")
        name = dialog.findChild(QLineEdit, "fileNameEdit")
        QTest.keyClicks(name, "sales_by_system")
        p.shot(dialog, "The name, typed with no extension: sales_by_system.", highlight=[name])
        p.step("Press Save.")
        QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save), Qt.LeftButton)

    p.while_open(lambda: QTest.mouseClick(browse, Qt.LeftButton), save_as)
    path = setting(win, "Path")
    saved = tab.doc.blocks["to_excel"]["config"].get("path")
    p.shot(win, "to_excel's Path is now the folder reports and the name you typed, with .xlsx added: "
                "…/reports/sales_by_system.xlsx.", highlight=[list(path)], zoom=[*path, setting(win, "Sheets")[1]])
    p.check("The Path is …/reports/sales_by_system.xlsx: .xlsx was added to the name",
            saved is not None and Path(saved).resolve() == (reports / "sales_by_system.xlsx").resolve())

    p.step("Click an empty spot of the canvas, and press ▶ Run now.")
    click(tab)
    run = run_now(win, tab, qtbot)
    wrote = (win.log.text, "[to_excel]")
    p.shot(win, "to_excel's line in the Log: it wrote the 15 rows to …/reports/sales_by_system.xlsx.",
           highlight=[wrote], zoom=[wrote])
    workbook = reports / "sales_by_system.xlsx"
    p.check("The run ended with success", run.status == "success")
    p.check("reports/sales_by_system.xlsx is an Excel workbook, and its sheet holds the 15 rows",
            workbook.is_file() and pl.read_excel(workbook).columns == COLUMNS and pl.read_excel(workbook).height == 15)
