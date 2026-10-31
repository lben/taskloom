"""Proof: a Python Code block passes on every variable it creates, as a dict, unless your code sets output; tables in
that dict, also from your own Python environment with pandas, reach the next blocks as tables. See README.md here.

The proof makes a small Python environment with pandas and pyarrow in its own Taskloom folder, with taskloom env
pip install, as yours would be: it needs the internet (on CI it always has it)."""

import importlib.util
import os
import re
import sys
import textwrap

import pytest
import yaml
from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_handled_error import click_bottom_tab
from proofs.test_proof_python_env_tables_pandas import DATAFRAME, has, said, status
from proofs.test_proof_python_new_block import click_block, environment_setting, from_menu, open_flow
from proofs.test_proof_run_output import run_now
from taskloom.blocks.logic import PythonCode
from taskloom.editor.panels import CodeEdit
from tests.test_docs_help import click_link, page_and_anchor

SALES = ("select 'North' as region, 120 as total union all select 'South', 80 "
         "union all select 'North', 60 union all select 'South', 40")
USERS_LINE = "output = {'percent_total': percent_total, 'combined': combined}"
FLOW = f"""
    blocks:
      start_by_hand:
        type: trigger.manual
        ui: {{x: 0, y: 0}}
      load_sales:
        type: data.duckdb_sql
        config: {{inputs: [], sql: "{SALES}"}}
        ui: {{x: 270, y: 0}}
      sales_report:
        type: logic.python
        config:
          inputs: [input]
          use_environment: true
          code: |-
            import pandas as pd

            def share(part, whole):
                return round(100 * part / whole, 1)

            north = input[input.region == "North"]
            south = input[input.region == "South"]
            combined = pd.concat([north, south], ignore_index=True)
            percent_total = share(north.total.sum(), combined.total.sum())
            {USERS_LINE}
        ui: {{x: 540, y: 0}}
      show_report:
        type: logic.python
        config:
          inputs: [input]
          outputs: []
          use_environment: true
          code: |-
            print("input is", type(input))
            if isinstance(input, dict):
                for name, value in input.items():
                    if hasattr(value, "columns"):  # a table
                        value = f"{{type(value)}}, {{len(value)}} rows"
                    else:  # with its type: str for text
                        value = f"{{value}} ({{type(value).__name__}})"
                    print(f"  {{name}}: {{value}}")
            else:
                print(f"  {{len(input)}} rows")
        ui: {{x: 810, y: 0}}
    edges:
      - start_by_hand.start -> load_sales.after
      - load_sales.result -> sales_report.input
      - sales_report.output -> show_report.input
"""
BLOCKS = ["start_by_hand", "load_sales", "sales_report", "show_report"]
FILE_LINES = ['report_file = open("report.txt", "w", encoding="utf-8")',
              'report_file.write(f"North: {percent_total}% of the total")']
NUMBER = r"60\.0 \((float|float64)\)"  # show_report prints percent_total as its value, then its type: a number
OVERRIDE = "output = combined"
LEFT_OUT = "left out of output"  # how the Log's warning line starts
BOX = "┌┐└┘│├─►"  # the characters of the starter's drawings
DOWN, UP = "┌┐├│", "└┘├│"  # those with a stroke going down, and up


def code_box(win) -> CodeEdit:
    """The selected block's Code box, in Properties."""
    return win.properties.findChild(CodeEdit)


def to_the_end(win, qtbot, box: CodeEdit):
    """Click on the Code box's last line and press End, as you would: the cursor is at the end of the code."""
    cursor = QTextCursor(box.document())
    cursor.movePosition(QTextCursor.End)
    win.activateWindow()
    QTest.mouseClick(box.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(8, box.cursorRect(cursor).center().y()))
    qtbot.waitUntil(box.hasFocus, timeout=5_000)
    QTest.keyClick(box, Qt.Key_End)
    assert box.textCursor().atEnd(), "the cursor is not at the end of the code"


def delete_last_line(win, qtbot):
    """Select the Code box's last line, with the line break before it, and press Backspace: the line is gone."""
    box = code_box(win)
    to_the_end(win, qtbot, box)
    cursor = box.textCursor()
    cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
    cursor.movePosition(QTextCursor.PreviousCharacter, QTextCursor.KeepAnchor)  # (the line break before it)
    box.setTextCursor(cursor)  # selected as you would by dragging over it
    QTest.keyClick(box, Qt.Key_Backspace)


def add_lines(win, qtbot, *lines: str):
    """Type each of `lines` on a line of its own after the Code box's last line."""
    box = code_box(win)
    to_the_end(win, qtbot, box)
    for line in lines:
        QTest.keyClick(box, Qt.Key_Return)
        QTest.keyClicks(box, line)


def settled(win, qtbot, run):
    """`run`, once the window has seen it end: the bottom left says no flow is running."""
    qtbot.waitUntil(lambda: win.running_flows.text() == "No flows running", timeout=10_000)
    return run


def printed(win, run_id: int) -> list[str]:
    """What show_report printed in run #`run_id`: its lines in the Log."""
    return said(win, "show_report", run_id)


def in_log(win, run_id: int, lines: list[str]) -> QRect:
    """The Log's lines of run #`run_id` that show `lines` (the first of each after that run's first line), in the
    window's pixels, for an outline on their edges. The Log keeps every run's lines, so the same text can be on
    several of them."""
    box = win.log.text
    doc = box.document()
    found = doc.find(f"run #{run_id} · ")
    assert not found.isNull(), f"the Log has no run #{run_id}"
    rect = QRect()
    for text in lines:
        found = doc.find(text, found)
        assert not found.isNull(), f"run #{run_id} has no line {text!r} in the Log"
        line = QTextCursor(found)
        line.movePosition(QTextCursor.StartOfLine)
        rect |= box.cursorRect(line)
        line.movePosition(QTextCursor.EndOfLine)
        rect |= box.cursorRect(line)
    return rect.adjusted(-3, 5, 10, -5).translated(box.viewport().mapTo(win, QPoint(0, 0)))


def warnings_of(win, block: str, run_id: int) -> list[str]:
    """The WARNING lines of `block` in run #`run_id`, in the Log."""
    found, current = [], False
    for e in win.log.entries:
        if e.get("header") and "started" in e.get("message", ""):
            current = f"run #{run_id} · " in e["message"]
        elif current and e.get("block") == block and e.get("level") == "WARNING":
            found.append(e.get("message", ""))
    return found


def received(lines: list[str]) -> dict:
    """What show_report's lines say each value of the dict it got is (name: what it printed)."""
    return dict(line.strip().split(": ", 1) for line in lines[1:])


def frame_of(shown: str, rows: int) -> bool:
    """Whether show_report printed a value as a pandas DataFrame of `rows` rows."""
    return re.fullmatch(f"{DATAFRAME}, {rows} rows", shown) is not None


def drawn_lines(text: str) -> list[int]:
    """The numbers (from 0) of the starter's lines that are part of its drawings."""
    return [i for i, line in enumerate(text.splitlines()) if any(c in BOX for c in line)]


def strokes_continue(text: str) -> bool:
    """Every vertical stroke of the drawings goes on straight: a character with a stroke going down has one with a
    stroke going up right under it, in the same column, and the other way round."""
    lines = text.splitlines()

    def at(i, c):
        return lines[i][c] if 0 <= i < len(lines) and c < len(lines[i]) else " "

    return all((ch not in DOWN or at(i + 1, c) in UP) and (ch not in UP or at(i - 1, c) in DOWN)
               for i, line in enumerate(lines) for c, ch in enumerate(line))


def columns_on_screen(box: CodeEdit, numbers: list[int]) -> dict[int, set[int]]:
    """Where the Code box draws each column of the lines `numbers`: column -> the x of its left edge on each line."""
    found: dict[int, set[int]] = {}
    for n in numbers:
        block = box.document().findBlockByNumber(n)
        for column in range(len(block.text()) + 1):
            cursor = QTextCursor(block)
            cursor.setPosition(block.position() + column)
            found.setdefault(column, set()).add(box.cursorRect(cursor).left())
    return found


def doc_lines(win, browser, first: str, last: str) -> QRect:
    """The lines of the Documentation column's page from the one showing `first` to the one showing `last`, the
    width of the page, in the window's pixels."""
    doc = browser.document()
    start = doc.find(first)
    end = doc.find(last, start)
    assert not start.isNull() and not end.isNull(), (first, last)

    def line_at(position: int) -> QRectF:
        """The line of the page that `position` is on, in the column's text area."""
        block = doc.findBlock(position)
        layout = block.layout()
        line = layout.lineForTextPosition(position - block.position()).rect().translated(layout.position())
        return line.translated(-browser.horizontalScrollBar().value(), -browser.verticalScrollBar().value())

    top, bottom = line_at(start.selectionStart()).top(), line_at(end.selectionEnd()).bottom()
    viewport = browser.viewport()
    rect = QRect(0, round(top), viewport.width(), round(bottom - top)) & viewport.rect()
    assert not rect.isEmpty(), "those lines are not in view"
    return rect.translated(viewport.mapTo(win, QPoint(0, 0)))


def plain(text: str) -> str:
    return " ".join(text.split())


def test_a_python_code_block_passes_on_every_variable_it_creates(env, proof, win, qtbot, monkeypatch, tmp_path):
    p = proof("A Python Code block passes on every variable it creates, unless you set output",
              "Ideally I would like that all the variables I created are directly available to the next block through "
              "its output hole and if a user writes something like output = {...} then that output gets overridden "
              "by what the user wants but by default I'd like it to make all the user variables created within the "
              "code to be available for the next block(s) … make it the default with the option to override the "
              "output, the user should know how and can override the default output, all that should be part of the "
              "documentation and the default commented block of a new python block")
    p.idea("A Python Code block whose code never sets output now passes on, through its output, a dict of every "
           "variable its code creates, by name. When your code sets output = …, that is what goes out instead.")
    p.idea("What you report: a block in your Python environment that ended with output = {'percent_total': "
           "percent_total, 'combined': combined}, combined a pandas DataFrame, failed with “ModuleNotFoundError: No "
           "module named 'pandas'”, though pandas is in your environment. It now succeeds, and the next block gets "
           "the dict: combined as a DataFrame, percent_total as the number.")
    p.idea("The dict holds what your code made: not what the block was given (its inputs, params, print and log), "
           "not the modules it imports, not the functions and classes it defines, and no name starting with _. A "
           "table in it, at any depth, reaches the next blocks as a table: in your environment, as a pandas "
           "DataFrame again.")
    p.idea("A variable that cannot be passed on, such as an open file, is left out, and one WARNING line in the Log "
           "names it and says why; the rest still goes on. A new Python Code block's starter comment says the "
           "default and how to send something else; Help > Documentation says all this.")
    p.why_simplest("One rule, and no setting: if your code sets output, that is what goes out, as before; if it never "
                   "does, output is a dict of the variables it created. You override the default the way you "
                   "already wrote code, with output = …; there is nothing to switch on or off.")
    p.why_simplest("Your environment and Taskloom's own Python are two programs, and only Taskloom's runs the flow. "
                   "The block failed because the dict, a pandas DataFrame in it, was handed to Taskloom's own Python, "
                   "which has no pandas to read it with. Now each DataFrame in what goes out, also inside dicts, lists "
                   "and tuples at any depth, is written as a table's file of its own, as a DataFrame set as the "
                   "whole output already was. So Taskloom's own Python never has to read a pandas DataFrame, and the "
                   "next block in your environment reads each table as a DataFrame again.")
    p.why_simplest("Each variable is passed on by itself, so one that cannot be (an open file, a generator, a "
                   "database connection) is left out alone, and named in the Log, instead of failing the block.")
    p.alternative("A setting on the block: “Pass on all my variables”",
                  "One more box to find and to remember, and the default you asked for would be off until you ticked "
                  "it. Writing output = … is already how you choose what goes out, so it is the override.")
    p.alternative("One output on the block for each variable",
                  "The block's outputs would change each time you edit your code, and renaming a variable would break "
                  "its connections. You asked for the variables through the one output, as a dict.")
    p.alternative("Pass on everything the code can see, its inputs, modules and functions too",
                  "The next block would get back the very inputs it was given, and modules and functions, which "
                  "cannot be passed on at all. Only what your code made is its result.")
    p.alternative("Fail the block when one variable cannot be passed on",
                  "One file you forgot to close would stop the run, though nothing after it needs that file. Leaving "
                  "it out and saying so in the Log keeps the run going and still tells you. (In your environment, "
                  "an output you set yourself to such a value does fail the block, saying why: that value is the "
                  "one you asked for.)")
    p.alternative("Put pandas into Taskloom's own Python",
                  "Taskloom would grow by pandas and its parts, and still could not read whatever else your "
                  "environment has. A table is what both sides share, so tables travel as tables.")
    p.fits("A block that sets output still sends just what it sets, as before. The one difference is that a "
           "DataFrame inside it, in a dict or a list, now travels as a table, as one set as the whole output "
           "always did. That is what fixes your case. A block whose code never set output used to pass nothing "
           "on, and the blocks after it were skipped; now they run, with the dict (an empty one, {}, when the code "
           "creates nothing). The block's other Outputs, if you name some, keep their rule: each is produced only "
           "when your code sets it.")
    p.fits("The tables in the dict are kept with the run like any table, so “Run only this block” and “Run from this "
           "block on, with the same inputs” on a later block get the dict again, its tables included. Data preview "
           "shows each of them by where it is in the dict, e.g. output.combined.")
    p.fits("It is the same with Run in my Python environment unticked, in Taskloom's own Python: there a polars "
           "DataFrame in the dict comes out as a table. In your environment every table a block receives is a "
           "pandas DataFrame, as before, so it needs pandas and pyarrow there.")

    created = env.taskloom("env", "create", "--python", sys.executable, timeout=300)
    assert created.returncode == 0, created.stdout + created.stderr
    installed = env.taskloom("env", "pip", "install", "pandas", "pyarrow", timeout=900)
    if installed.returncode != 0:
        if os.environ.get("CI"):  # CI has internet: never skipped there
            raise AssertionError(installed.stdout + installed.stderr)
        pytest.skip(f"the package index is unreachable here, pip failed: {installed.stdout[-800:]}")
    flow = tmp_path / "sales-share.yaml"
    flow.write_text(textwrap.dedent(FLOW), encoding="utf-8")

    # --- the case you reported ------------------------------------------------------------------------------------
    p.step("Open the flow sales-share.yaml (File > Open…). load_sales, a DuckDB SQL block, makes a table of four "
           "sales, two in the North and two in the South. ① sales_report, a Python Code block in your Python "
           "environment, gets it and ends as your code did: output = {'percent_total': percent_total, 'combined': "
           "combined}. ② show_report, the next Python Code block, also in your environment, prints what it gets: "
           "each value with its type (str if it is text), and each table's type and size.")
    p.note("Before the steps, the proof made a small Python environment with pandas and pyarrow in its own Taskloom "
           "folder (taskloom env create, then taskloom env pip install pandas pyarrow), as yours would be. Taskloom's "
           "own Python, which runs the flow, has no pandas: that is why tables travel between the blocks as "
           "Taskloom's tables, each a file, and become DataFrames again in your environment.")
    p.check("Your Python environment has pandas and pyarrow", has(env, "pandas") and has(env, "pyarrow"))
    p.check("Taskloom's own Python, which runs the flow, has no pandas", importlib.util.find_spec("pandas") is None)
    tab = open_flow(win, monkeypatch, flow)
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    p.shot(win, "The flow as it opens: ① sales_report and ② show_report are the two Python Code blocks. Nothing has "
                "run yet.", highlight=["sales_report", "show_report"], badges=True, zoom=BLOCKS)

    p.step("Click sales_report: Properties, on the right, shows its code, and Run in my Python environment ticked.")
    click_block(tab, "sales_report")
    code, setting = code_box(win), environment_setting(win)
    p.shot(win, "① Run in my Python environment is ticked. Its code picks the North and the South sales, puts them "
                "together as combined with pandas, works out the North's share of the total, percent_total, with a "
                "function of its own, share, and ② ends with the line from your report.",
           highlight=[setting, (code, USERS_LINE)], badges=True, zoom=[setting, code])
    p.check("① Run in my Python environment is ticked on both blocks",
            setting[1].isChecked() and all(tab.doc.blocks[b]["config"].get("use_environment") is True
                                           for b in ("sales_report", "show_report")))
    p.check("② sales_report's code ends with output = {'percent_total': percent_total, 'combined': combined}",
            code.toPlainText().splitlines()[-1] == USERS_LINE)

    p.step("Press ▶ Run now in the toolbar.")
    first = settled(win, qtbot, run_now(win, tab, qtbot))
    p.shot(win, "Every block ran and succeeded: all green. ① sales_report did not fail on pandas; the line under it "
                "starts with what it passed on (output: {'percent_total': …). ② show_report got it.",
           highlight=["sales_report", "show_report"], badges=True, zoom=BLOCKS)
    p.check(f"Run #{first.run_id} succeeded: every block shows success, sales_report too, with no "
            "ModuleNotFoundError",
            first.status == "success" and all(status(tab, b) == ("success", first.run_id) for b in BLOCKS)
            and "ModuleNotFoundError" not in win.log.text.toPlainText())

    p.step("Click show_report: the Log, under the canvas, shows only its lines.")
    click_block(tab, "show_report")
    lines = printed(win, first.run_id)
    got = received(lines)
    p.shot(win, "What show_report printed, outlined: input is a dict, with percent_total, the number 60.0 (float64, "
                "the float pandas made it with: a number, not text), and combined, a pandas DataFrame of the four "
                "sales.",
           highlight=[in_log(win, first.run_id, lines)], zoom=in_log(win, first.run_id, lines))
    p.check("show_report's input is a dict", lines[:1] == ["input is <class 'dict'>"])
    p.check("It holds just what your line set: percent_total, the number 60.0 (North: 120 + 60 of 300), a float, "
            "not text, and combined, a pandas DataFrame of 4 rows",
            list(got) == ["percent_total", "combined"] and re.fullmatch(NUMBER, got["percent_total"]) is not None
            and frame_of(got["combined"], 4))
    p.note("Before this change, this same run failed at sales_report with “ModuleNotFoundError: No module named "
           "'pandas'”, as you reported. (This proof does not run the old version.)")

    # --- the default: every variable ------------------------------------------------------------------------------
    p.step("Click sales_report, delete the last line of its code, output = {…}, and press ▶ Run now.")
    click_block(tab, "sales_report")
    delete_last_line(win, qtbot)
    p.check("sales_report's code no longer sets output: its last line is the one that sets percent_total",
            code_box(win).toPlainText().splitlines()[-1].startswith("percent_total = share(")
            and "output" not in code_box(win).toPlainText())
    second = settled(win, qtbot, run_now(win, tab, qtbot, runs=2))
    p.check(f"Run #{second.run_id} succeeded", second.status == "success"
            and all(status(tab, b) == ("success", second.run_id) for b in BLOCKS))
    p.check("The flow file has the code without that line (Run now saved it first)",
            yaml.safe_load(flow.read_text(encoding="utf-8"))["blocks"]["sales_report"]["config"]["code"]
            == code_box(win).toPlainText())

    p.step("Click show_report again.")
    click_block(tab, "show_report")
    lines = printed(win, second.run_id)
    got = received(lines)
    p.shot(win, "Without the line, show_report got, outlined, a dict of every variable sales_report's code created: "
                "north, south and combined, each a pandas DataFrame, and percent_total, the number 60.0 (float64). "
                "Not its input, nor pd (the pandas it imports), nor share (the function it defines).",
           highlight=[in_log(win, second.run_id, lines)], zoom=in_log(win, second.run_id, lines))
    p.check("show_report's input is a dict", lines[:1] == ["input is <class 'dict'>"])
    p.check("Its names are those of the variables the code created, in the order it created them: north, south, "
            "combined, percent_total",
            list(got) == ["north", "south", "combined", "percent_total"])
    p.check("north (2 rows), south (2 rows) and combined (4 rows) are pandas DataFrames; percent_total is the number "
            "60.0, a float, not text",
            frame_of(got["north"], 2) and frame_of(got["south"], 2) and frame_of(got["combined"], 4)
            and re.fullmatch(NUMBER, got["percent_total"]) is not None)
    p.check("Not in it: input (what the block was given), pd (a module it imports), share (a function it defines)",
            not {"input", "pd", "share"} & set(got))

    p.step("Click sales_report, then the Data preview tab, under the canvas, and open the list next to its title.")
    click_block(tab, "sales_report")
    click_bottom_tab(win, win.preview)
    preview = win.preview
    combo = preview.ports
    names = [combo.itemText(i) for i in range(combo.count())]
    tables = ["output.combined", "output.north", "output.south"]

    def look_at_the_list(popup):
        view = combo.view()
        p.shot(popup, "The list offers ① each table in the dict on its own, by its name in it: output.combined, "
                      "output.north and output.south; and ② output, the dict itself.", over=win,
               highlight=[[(view, names.index(t)) for t in tables], (view, names.index("output"))], badges=True,
               zoom=[popup, preview.label])
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier,
                         view.visualRect(view.model().index(names.index("output"), 0)).center())

    p.while_open(combo.showPopup, look_at_the_list)
    QTest.qWait(100)
    p.check("① The list has output.combined, output.north and output.south, and ② output",
            sorted(names[:-1]) == tables and names[-1] == "output")
    dict_text = preview.text.toPlainText()
    shown = [(preview.text, line.strip()) for line in dict_text.splitlines()]
    p.shot(win, "Chose output: ① Data preview shows the dict, each name with its value; each table in it says its "
                "size, e.g. combined: 4 rows × 2 columns.",
           highlight=[shown], badges=True, zoom=[preview.label, *shown])
    p.check("① output shows north, south, combined and percent_total: 60.0, with each table's size",
            preview.label.text() == "sales_report.output:" and not preview.text.isHidden()
            and all(f'"{name}": "<table: {rows} rows × 2 columns>"' in dict_text
                    for name, rows in (("north", 2), ("south", 2), ("combined", 4)))
            and '"percent_total": 60.0' in dict_text)

    p.step("Open the list again and choose output.combined.")

    def choose_combined(popup):
        view = combo.view()
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier,
                         view.visualRect(view.model().index(names.index("output.combined"), 0)).center())

    p.while_open(combo.showPopup, choose_combined)
    QTest.qWait(100)
    model = preview.view.model()
    rows = [[model.index(r, c).data() for c in range(model.columnCount())] for r in range(model.rowCount())
            ] if model is not None else []
    p.shot(win, "① combined, the table inside the dict, as a grid: ② its four rows, North 120 and 60, then South 80 "
                "and 40.",
           highlight=[preview.label, [(preview.view, r) for r in range(len(rows))]], badges=True,
           zoom=[preview.label, *[(preview.view, r) for r in range(len(rows))]])
    p.check("① Data preview shows sales_report.output.combined: 4 rows × 2 columns",
            preview.label.text() == "sales_report.output.combined: 4 rows × 2 columns" and not preview.view.isHidden())
    p.check("② Its rows are North 120, North 60, South 80, South 40",
            rows == [["North", "120"], ["North", "60"], ["South", "80"], ["South", "40"]])
    click_bottom_tab(win, win.log)

    # --- Run from this block on ----------------------------------------------------------------------------------
    p.step(f"Right-click show_report and choose “Run from this block on, with the same inputs”. It takes what "
           f"reached it in run #{second.run_id}; the blocks before it do not run again.")
    item = "Run from this block on, with the same inputs"

    def run_from_show_report(menu):
        p.shot(menu, f"The menu of show_report, with the item to choose: it takes the inputs of run #{second.run_id}.",
               over=win, highlight=[menu_item(menu, item)], zoom=[menu, "show_report"])
        choose(menu, item)

    p.while_open(lambda: right_click(tab, "show_report"), run_from_show_report)
    qtbot.waitUntil(lambda: len(tab.runs) == 3 and not tab.running and tab.runs[-1].status is not None,
                    timeout=120_000)
    QTest.qWait(200)  # what the run printed last reaches the Log
    third = settled(win, qtbot, tab.runs[-1])
    click_block(tab, "show_report")
    note, *again = printed(win, third.run_id)
    p.shot(win, f"Run #{third.run_id}, outlined: show_report says it runs from the inputs it got in run "
                f"#{second.run_id}, then prints the same as in run #{second.run_id}: the same dict, its three tables, "
                "pandas DataFrames, included, and percent_total, the number 60.0 (float64).",
           highlight=[in_log(win, third.run_id, [note, *again])], zoom=in_log(win, third.run_id, [note, *again]))
    p.check(f"show_report ran from the inputs it got in run #{second.run_id}",
            note.startswith("running from show_report on") and f"with the inputs it got in run #{second.run_id}" in note)
    p.check(f"Run #{third.run_id} succeeded, and show_report got the same dict as in run #{second.run_id}: north, "
            "south and combined as pandas DataFrames of 2, 2 and 4 rows, and percent_total the number 60.0, a "
            "float",
            third.status == "success" and status(tab, "show_report") == ("success", third.run_id)
            and again == lines and len(again) == 5
            and re.fullmatch(NUMBER, received(again)["percent_total"]) is not None)
    p.check(f"Only show_report ran: sales_report and load_sales still show their results of run #{second.run_id}",
            all(status(tab, b) == ("success", second.run_id) for b in ("load_sales", "sales_report")))

    # --- what cannot be passed on --------------------------------------------------------------------------------
    p.step("Click sales_report, add two lines at the end of its code, and press ▶ Run now. The two lines open a file, "
           "report.txt, and write a line in it, but do not close it: report_file is an open file.")
    click_block(tab, "sales_report")
    add_lines(win, qtbot, *FILE_LINES)
    p.check("sales_report's code ends with the two lines", code_box(win).toPlainText().splitlines()[-2:] == FILE_LINES)
    fourth = settled(win, qtbot, run_now(win, tab, qtbot, runs=4))
    click_block(tab, "sales_report")
    warnings = warnings_of(win, "sales_report", fourth.run_id)
    p.text("The WARNING line, in full", warnings[0] if warnings else "(none)")
    p.check(f"Run #{fourth.run_id} succeeded", fourth.status == "success"
            and all(status(tab, b) == ("success", fourth.run_id) for b in BLOCKS))
    p.check("There is one WARNING line, and it names report_file and says why: Python cannot copy an open file "
            "(a TextIOWrapper)",
            len(warnings) == 1 and "\n" not in warnings[0] and warnings[0].startswith(LEFT_OUT)
            and "report_file (" in warnings[0] and "TextIOWrapper" in warnings[0])
    p.shot(win, "The Log, showing sales_report's lines: one WARNING line, outlined, says that report_file, the open "
                "file, was left out of output, as it cannot be passed on to the next blocks or kept with the run, and "
                "why (written out in full with this step). The block succeeded.",
           highlight=[(win.log.text, LEFT_OUT)], zoom=[(win.log.text, LEFT_OUT)])
    got = received(printed(win, fourth.run_id))
    p.check("show_report still got the four others: north, south, combined and percent_total",
            list(got) == ["north", "south", "combined", "percent_total"])

    # --- override ------------------------------------------------------------------------------------------------
    p.step("Add one more line at the end of sales_report's code, output = combined, and press ▶ Run now.")
    add_lines(win, qtbot, OVERRIDE)
    p.check("sales_report's code ends with output = combined", code_box(win).toPlainText().splitlines()[-1] == OVERRIDE)
    fifth = settled(win, qtbot, run_now(win, tab, qtbot, runs=5))
    p.shot(win, "① The line under sales_report says it passed on a table: output: 4 rows × 2 columns. ② show_report "
                "got it.", highlight=["sales_report", "show_report"], badges=True, zoom=BLOCKS)
    p.check(f"Run #{fifth.run_id} succeeded", fifth.status == "success"
            and all(status(tab, b) == ("success", fifth.run_id) for b in BLOCKS))
    p.check("① The line under sales_report says output: 4 rows × 2 columns",
            "output: 4 rows × 2 columns" in tab.scene.nodes["sales_report"].status_text)

    p.step("Click show_report.")
    click_block(tab, "show_report")
    lines = printed(win, fifth.run_id)
    p.shot(win, "show_report got only that table, outlined: input is a pandas DataFrame, of 4 rows, not a dict.",
           highlight=[in_log(win, fifth.run_id, lines)], zoom=in_log(win, fifth.run_id, lines))
    p.check("show_report's input is the table combined alone: a pandas DataFrame of 4 rows, not a dict",
            len(lines) == 2 and re.fullmatch(f"input is {DATAFRAME}", lines[0]) and lines[1].strip() == "4 rows")
    p.check("No WARNING this time: once your code sets output, its other variables (report_file too) do not go out",
            warnings_of(win, "sales_report", fifth.run_id) == [])

    # --- the starter comment of a new block ----------------------------------------------------------------------
    p.step("Double-click Python Code in the Blocks list, on the left, and click the new block, python, on the canvas.")
    tree = win.palette_panel.tree
    entry = tree.findItems("Python Code", Qt.MatchExactly | Qt.MatchRecursive)[0]
    tree.scrollToItem(entry)
    at = tree.visualItemRect(entry).center()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # a double-click: Qt's test one is only
    QTest.mouseDClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # its second click
    QTest.qWait(50)
    p.check("A Python Code block, python, is added to the flow",
            tab.doc.blocks.get("python", {}).get("type") == "logic.python")
    click_block(tab, "python")
    box = code_box(win)
    box.verticalScrollBar().setValue(0)
    starter = box.toPlainText()
    numbers = drawn_lines(starter)
    drawings = [(box, starter.splitlines()[n]) for n in numbers]
    p.shot(win, "The new block's Code box starts with the new comment: ① two drawings of what comes in, one block "
                "connected, then two, each followed by what input then is.",
           highlight=[drawings], badges=True, zoom=[(box, starter.splitlines()[0]), *drawings])
    on_screen = columns_on_screen(box, numbers)
    p.check("The Code box holds the starter comment of a new Python Code block, and it is only comments",
            starter == PythonCode.new_config["code"]
            and all(line.startswith("#") for line in starter.splitlines()))
    p.check("① Its drawings' lines line up: every vertical stroke goes on straight, in the same column, from one line "
            "to the next",
            len(numbers) == 10 and strokes_continue(starter))
    p.check("…and on the screen too: on every line of the drawings, each column is drawn at the same place",
            all(max(xs) - min(xs) <= 1 for xs in on_screen.values()))

    p.step("Scroll down to the end of the Code box.")
    box.verticalScrollBar().setValue(box.verticalScrollBar().maximum())
    QApplication.processEvents()
    goes_out = [(box, line) for line in starter.splitlines()[-5:]]
    p.shot(win, "① The comment ends with what goes out: by default every variable you create, as a dict; to send "
                "something else, set output yourself, e.g. output = north.",
           highlight=[goes_out], badges=True, zoom=goes_out)
    p.check("① It says that by default every variable you create goes out, as a dict, and how to send something "
            "else: set output yourself",
            "# What goes out:\n#   By default, every variable you create, as a dict:\n"
            '#     output = {"sales": ..., "region": ..., "north": ...}\n'
            "#   To send something else, set output yourself:\n#     output = north" in starter)

    # --- the documentation -----------------------------------------------------------------------------------------
    p.step("Choose Help > Documentation, click The editor, under Guide, and scroll down to “What a Python Code block "
           "passes on”.")
    from_menu(p, win, "&Help", "Documentation")
    browser = win.docs.browser
    QApplication.processEvents()
    p.check("The column opens on Contents", browser.source().fileName() == "index.html")
    click_link(browser, lambda href, text: page_and_anchor(href)[0] == "the-editor.html")
    p.check("The column shows The editor's page", browser.source().fileName() == "the-editor.html")
    heading = browser.document().find("What a Python Code block passes on")
    p.check("The editor's page has “What a Python Code block passes on”", not heading.isNull())
    bar = browser.verticalScrollBar()
    bar.setValue(int(browser.document().documentLayout().blockBoundingRect(heading.block()).top()) - 10)
    QApplication.processEvents()
    page = plain(browser.toPlainText())
    passes_on = doc_lines(win, browser, "What a Python Code block passes on", "your code sets them")
    p.shot(win, "The Documentation column, at the far right, and its entry in the close-up: unless your code sets "
                "output, its output is a dict of every variable your code creates, by name, and what is not in it; to "
                "send something else, set it yourself, e.g. output = north.", zoom=passes_on)
    p.check("It says that unless your code sets output, its output is a dict of every variable your code creates",
            "What a Python Code block passes on: unless your code sets output, its output is a dict of every "
            "variable your code creates" in page)
    p.check("It says how to override it: “To send something else, set it yourself, e.g. output = north”",
            "To send something else, set it yourself, e.g. output = north" in page)
    for each in win.tabs_list():  # the new block was added only to be shown: closing the window must not ask to save
        each.doc.dirty = False
