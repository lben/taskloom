"""Proof: in your Python environment every table a Python Code block receives is a pandas DataFrame, and a DataFrame
it sets comes back as a table; without pandas the block says what to install, before your code runs. See README.md
here.

The proof makes a real environment in its own Taskloom folder and installs pandas and pyarrow from the package index
through Tools > Python environment, as you would: it needs the internet (on CI it always has it)."""

import html
import os
import re
import subprocess
import sys
import textwrap

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QFileDialog, QLabel, QToolTip

from proofs.conftest import ZOOM_MARGIN
from proofs.test_proof_run_output import click, hover, run_now
from taskloom.editor.panels import CodeEdit
from taskloom.pyenv import python_in
from tests.test_python_new_block import ENV_BOX, environment_box

FLOW = """
    blocks:
      start_by_hand:
        type: trigger.manual
        ui: {x: 0, y: 140}
      eu_sales:
        type: data.duckdb_sql
        config: {inputs: [], sql: "select 'Berlin' as city, 120 as total union all select 'Paris', 80"}
        ui: {x: 260, y: 0}
      us_sales:
        type: data.duckdb_sql
        config: {inputs: [], sql: "select 'Boston' as city, 95 as total union all select 'Denver', 60"}
        ui: {x: 260, y: 200}
      one_table:
        type: logic.python
        config:
          inputs: [input]
          use_environment: true
          code: |
            print("input is", type(input))
            print(input)
        ui: {x: 540, y: 0}
      two_tables:
        type: logic.python
        config:
          inputs: [input]
          use_environment: true
          code: |
            import pandas
            print("input is", type(input))
            for name, table in input.items():
                print(name, "is", type(table))
            output = pandas.concat(input.values(), ignore_index=True)
        ui: {x: 540, y: 200}
      all_sales:
        type: logic.inspect
        ui: {x: 820, y: 200}
      no_table:
        type: logic.python
        config:
          inputs: []
          use_environment: true
          code: |
            import sys
            print("This code ran, in", sys.executable)
        ui: {x: 260, y: 400}
    edges:
      - start_by_hand.start -> eu_sales.after
      - start_by_hand.start -> us_sales.after
      - start_by_hand.start -> no_table.after
      - eu_sales.result -> one_table.input
      - eu_sales.result -> two_tables.input
      - us_sales.result -> two_tables.input
      - two_tables.output -> all_sales.value
"""
BLOCKS = ["start_by_hand", "eu_sales", "us_sales", "one_table", "two_tables", "all_sales", "no_table"]
SHOWN = ["one_table", "two_tables", "no_table"]  # highlighted as ①, ② and ③
DATAFRAME = r"<class 'pandas\.(core\.frame\.)?DataFrame'>"  # how print() shows its type (pandas 3: pandas.DataFrame)
MESSAGE = ("a table among this block's inputs arrives as a pandas DataFrame, which needs pandas and pyarrow in your "
           "Python environment: install them under Tools → Python environment (or taskloom env pip install pandas "
           "pyarrow). Your code did not run.")


def has(env, module: str) -> bool:
    """Whether your Python environment can import `module`."""
    return subprocess.run([str(python_in(env.home / "python-env")), "-I", "-c", f"import {module}"],
                          capture_output=True, timeout=120).returncode == 0


def said(win, block: str, run_id: int) -> list[str]:
    """What the Log shows of `block` in run #`run_id`: its lines, in order."""
    lines, current = [], False
    for e in win.log.entries:
        if e.get("header") and "started" in e.get("message", ""):
            current = f"run #{run_id} · " in e["message"]
        elif current and e.get("block") == block:
            lines.extend(e.get("message", "").splitlines())
    return lines


def status(tab, block: str) -> tuple:
    """What the canvas shows of a block: its status, and the run its result is from."""
    return tab.scene.nodes[block].status, (tab.block_info.get(block) or {}).get("run_id")


def split_at(tip, text: str, words: str) -> tuple[QRect, QRect]:
    """A tooltip that shows `text` on one line, in a font whose letters are all as wide, as two parts: up to the
    space before `words`, and from it on. Each part is given so that its close-up ends (or starts) at that space."""
    x = round(tip.width() * text.index(" " + words) / len(text))
    return (QRect(0, 0, x - ZOOM_MARGIN, tip.height()),
            QRect(x + ZOOM_MARGIN, 0, tip.width() - x - ZOOM_MARGIN, tip.height()))


def test_in_your_python_environment_every_table_is_a_pandas_dataframe(env, proof, win, qtbot, monkeypatch, tmp_path):
    p = proof("In your Python environment, every table is a pandas DataFrame",
              "In 'Run in my Python environment', what should a table input be, so it is always the same? — Always a "
              "pandas DataFrame (Consistent and familiar. If pandas is missing from your environment, the block says "
              "so and offers to install it, Tools > Python environment). It came from: a parquet path without "
              "pandas, a DataFrame with it was inconsistent.")
    p.idea("A Python Code block with Run in my Python environment ticked now always gets a table as a pandas "
           "DataFrame. Before, it depended on what the environment had: a pandas DataFrame with pandas, a polars "
           "one with only polars, and the path of the table's file with neither.")
    p.idea("Every table becomes a DataFrame wherever it is in what the block receives: an input of its own, or inside "
           "a dictionary or a list. Two blocks connected to one input give a dictionary keyed by their names, so "
           "two tables there are two DataFrames.")
    p.idea("A DataFrame your code sets as an output comes back out as a table, for the blocks after it (a polars "
           "DataFrame or a pyarrow Table too).")
    p.idea("You install pandas and pyarrow yourself, once: under Tools > Python environment, or with taskloom env pip "
           "install pandas pyarrow in a terminal. pandas reads the tables' files with pyarrow. Without them, a block "
           "that receives a table fails before your code runs, and its message names both and says where to install "
           "them. A block that receives no table runs as before. The block does not install them itself: its message "
           "points to the window where you do.")
    p.idea("In Taskloom's own Python (the box unticked) nothing changes: a table there is still Taskloom's own table, "
           "not a pandas DataFrame.")
    p.why_simplest("One rule, with no setting and no guessing: a table is a pandas DataFrame. Each table still travels "
                   "to your environment as the path of its file, as before; your environment reads every one with "
                   "pandas before your code starts.")
    p.why_simplest("When pandas or pyarrow is missing, reading the first table fails right there, and Taskloom turns "
                   "that into one plain message: what to install, and where. So your code never starts with a file "
                   "path where it expects a DataFrame.")
    p.alternative("Keep giving whatever the environment can read: pandas, else polars, else the file's path",
                  "The same block got different things on different computers, the inconsistency you pointed out: "
                  "code written where pandas was installed broke where it was not.")
    p.alternative("Install pandas and pyarrow automatically when a run finds them missing",
                  "A run, perhaps the scheduler's at night, would change your environment without you, wait on the "
                  "package index, and could fail half-way through installing. The message says what to install and "
                  "where; you do it once, and see pip's output as it installs.")
    p.alternative("A polars DataFrame instead",
                  "Taskloom works with polars inside, but you chose pandas: the DataFrame most people who write "
                  "Python know.")
    p.fits("It is the same environment and the same window as before: Tools > Python environment makes it and "
           "installs packages into it with pip (your pip settings, e.g. a company package mirror, apply). The "
           "message names that window, and the command for a terminal.")
    p.fits("A new Python Code block's example code says it too: a table arrives as a pandas DataFrame in your Python "
           "environment, and a DataFrame you set as output comes out as a table.")
    p.fits("A DataFrame your code sets is kept as a table's file in the run's folder, like every table: the next "
           "blocks, Data preview and the run history show it like any other table.")

    created = env.taskloom("env", "create", "--python", sys.executable, timeout=300)
    assert created.returncode == 0, created.stdout + created.stderr
    flow = tmp_path / "sales-in-pandas.yaml"
    flow.write_text(textwrap.dedent(FLOW), encoding="utf-8")

    p.step("Open the flow sales-in-pandas.yaml (File > Open…). Two DuckDB SQL blocks, eu_sales and us_sales, each make "
           "a table of two cities' sales. ① one_table gets eu_sales' table and prints its type and its rows: "
           "print(\"input is\", type(input)), then print(input). ② two_tables gets both tables into its one input, "
           "prints what it got, and sets output to the two put together; all_sales, an Inspect block, shows that "
           "output. ③ no_table receives no table: it prints which Python it runs in.")
    p.note("Before the steps, the proof made your Python environment in its own Taskloom folder, with taskloom env "
           "create (what Create environment does under Tools > Python environment). A new environment has only what "
           "Python itself comes with.")
    p.check("Your Python environment has no pandas and no pyarrow yet", not has(env, "pandas") and not has(env, "pyarrow"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    p.shot(win, "The flow as it opens: ① one_table, ② two_tables and ③ no_table are Python Code blocks. Nothing has "
                "run yet.", highlight=SHOWN, badges=True, zoom=BLOCKS)

    p.step("Click two_tables: Properties, on the right, shows its code, and Run in my Python environment ticked.")
    click(tab, "two_tables")
    code = win.properties.findChild(CodeEdit)
    box = environment_box(win)
    label = next(w for w in win.properties.findChildren(QLabel) if w.text() == ENV_BOX)
    p.shot(win, "① Its code prints what input is, and the type of each value in it, then sets output to the tables put "
                "together with pandas. ② Run in my Python environment is ticked (one_table and no_table have it "
                "ticked too).", highlight=[[(code, "import pandas"), (code, "output = ")], [label, box]], badges=True,
           zoom=[(code, "import pandas"), label, box])
    p.check("① two_tables' code is the one in the flow file", "pandas.concat(input.values()" in code.toPlainText())
    p.check("② Run in my Python environment is ticked on two_tables, one_table and no_table",
            box.isChecked() and all((tab.doc.blocks[b].get("config") or {}).get("use_environment") is True
                                    for b in SHOWN))

    p.step("Click an empty spot of the canvas, so the Log shows every block, and press ▶ Run now in the toolbar, while "
           "your environment has neither pandas nor pyarrow.")
    click(tab)
    first = run_now(win, tab, qtbot)
    p.check("① one_table and ② two_tables failed, each with the message naming pandas and pyarrow, and Tools → Python "
            "environment",
            all(status(tab, b) == ("failed", first.run_id)
                and MESSAGE in (tab.block_info[b].get("error") or "") for b in ("one_table", "two_tables")))
    p.check("Their code did not run: the Log has none of their lines (input is …)",
            not any(line.startswith("input is") for b in ("one_table", "two_tables")
                    for line in said(win, b, first.run_id)))
    no_table = [line for line in said(win, "no_table", first.run_id) if line.startswith("This code ran, in")]
    p.text("③ no_table's line in the Log, in full", "\n".join(no_table))
    p.check("③ no_table, in the same environment, succeeded, and its line names your environment's Python",
            status(tab, "no_table") == ("success", first.run_id) and len(no_table) == 1
            and "python-env" in no_table[0])
    p.shot(win, "① one_table and ② two_tables failed: they are red, and the line under them starts the message (the "
                "next step shows all of it). all_sales, after two_tables, did not run. ③ no_table, in the same "
                "environment, ran: it is green, and its line in the Log, at the bottom, names your environment's "
                "Python (written out in full with this step).",
           highlight=[*SHOWN, (win.log.text, "This code ran, in")], badges=[1, 2, 3, 3], zoom=BLOCKS)

    p.step("Rest the mouse on the red line under one_table: it shows the whole message.")
    tip = hover(tab, tab.scene.nodes["one_table"].footer, qtbot)
    words = html.unescape(re.sub(r"<[^>]+>", "", QToolTip.text())).strip()  # its text, without the HTML around it
    p.check("① It names pandas and pyarrow, ② says to install them under Tools → Python environment (or with taskloom "
            "env pip install pandas pyarrow), and ③ that your code did not run", MESSAGE in words)
    start, rest = split_at(tip, words, "install them under")
    p.shot(tip, "The message shows over the canvas, wider than the window: the close-up is its first part. ① A table "
                "among this block's inputs arrives as a pandas DataFrame, which needs pandas and pyarrow in your "
                "Python environment.",
           over=win, zoom=start.translated(tip.mapToGlobal(QPoint(0, 0)) - win.mapToGlobal(QPoint(0, 0))))
    p.shot(tip, "The message on its own, and the close-up of its second part: ② install them under Tools → Python "
                "environment, or with the command taskloom env pip install pandas pyarrow in a terminal. ③ Your code "
                "did not run.", zoom=rest)
    QToolTip.hideText()

    p.step("Choose Tools > Python environment…, type pandas pyarrow next to Install, and press Install. When pip has "
           "installed them, the window lists the environment's packages again; then close it.")
    p.note("You do this once, for every block in your environment. The same is done in a terminal with taskloom env "
           "pip install pandas pyarrow.")
    tools = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Tools")
    installed = []

    def install(dialog):
        qtbot.waitUntil(lambda: dialog.proc is None, timeout=120_000)  # it lists the packages as it opens
        QTest.mouseClick(dialog.packages, Qt.LeftButton)
        QTest.keyClicks(dialog.packages, "pandas pyarrow")
        p.shot(dialog, "① Your environment is in use, with only pip in it, as the list below says: no pandas, no "
                       "pyarrow. ② pandas pyarrow typed, Install to press.",
               highlight=[dialog.status, [dialog.packages, dialog.install_button]], badges=True)
        QTest.mouseClick(dialog.install_button, Qt.LeftButton)
        output = dialog.output
        qtbot.waitUntil(lambda: dialog.proc is None and (output.toPlainText().count("$ taskloom env pip list") == 2
                                                         or "failed (exit code" in output.toPlainText()),
                        timeout=900_000)
        text = output.toPlainText()
        if "failed (exit code" in text and not os.environ.get("CI"):  # CI has internet: never skipped there
            pytest.skip(f"the package index is unreachable here, pip failed: {text[-800:]}")
        listed = text[text.rindex("$ taskloom env pip list"):].splitlines()
        installed.extend(line for line in listed if re.fullmatch(r"(pandas|pyarrow)\s+\S+", line))
        p.shot(dialog, "pip installed them, and the list of the environment's packages now has ① pandas and ② pyarrow "
                       "(with numpy and the others pandas needs).",
               highlight=[(output, line) for line in installed], badges=True)
        QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Close), Qt.LeftButton)

    p.while_open(lambda: next(a for a in tools.actions() if a.text() == "Python environment…").trigger(), install)
    p.check("The list has ① pandas and ② pyarrow, and your environment can now use them",
            [line.split()[0] for line in installed] == ["pandas", "pyarrow"]
            and has(env, "pandas") and has(env, "pyarrow"))

    p.step("Press ▶ Run now again.")
    second = run_now(win, tab, qtbot, runs=2)
    p.shot(win, "Every block ran: all green, ① one_table and ② two_tables too. The line under two_tables says it "
                "made a table, output: 4 rows × 2 columns, and all_sales after it got it, value: 4 rows × 2 columns. "
                "③ no_table ran as before.", highlight=SHOWN, badges=True, zoom=BLOCKS)
    p.check("The run succeeded: every block shows success, from the second run",
            second.status == "success" and all(status(tab, b) == ("success", second.run_id) for b in BLOCKS))

    p.step("Click one_table: the Log, under the canvas, shows only its lines.")
    click(tab, "one_table")
    lines = said(win, "one_table", second.run_id)
    p.check("① one_table's input is a pandas DataFrame", bool(lines) and re.fullmatch(f"input is {DATAFRAME}", lines[0]))
    p.check("① Its rows are eu_sales' two rows: Berlin 120 and Paris 80",
            [line.split() for line in lines[1:]] == [["city", "total"], ["0", "Berlin", "120"], ["1", "Paris", "80"]])
    p.shot(win, "① What one_table printed: input is a pandas DataFrame, then its rows, Berlin 120 and Paris 80, as "
                "pandas prints a DataFrame.",
           highlight=[[(win.log.text, line) for line in lines]], badges=True,
           zoom=[(win.log.text, line) for line in lines])

    p.step("Click two_tables.")
    click(tab, "two_tables")
    lines = said(win, "two_tables", second.run_id)
    printed = lines[:3]
    p.check("① two_tables' input is a dictionary", printed[:1] == ["input is <class 'dict'>"])
    p.check("② Its two values, eu_sales and us_sales, are both pandas DataFrames",
            len(printed) == 3 and all(re.fullmatch(f"{name} is {DATAFRAME}", line)
                                      for name, line in zip(("eu_sales", "us_sales"), printed[1:])))
    p.shot(win, "① two_tables' input is a dictionary, keyed by the two blocks connected to it: ② eu_sales and "
                "us_sales, each a pandas DataFrame. The line under them says two_tables made a table: output, 4 rows "
                "× 2 columns.",
           highlight=[(win.log.text, printed[0]), [(win.log.text, line) for line in printed[1:]]], badges=True,
           zoom=[(win.log.text, line) for line in printed])

    p.step("Click the Data preview tab, under the canvas: it shows what two_tables made.")
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.bottom_tabs.indexOf(win.preview)).center())
    preview = win.preview
    model = preview.view.model()
    rows = [[model.index(r, c).data() for c in range(model.columnCount())] for r in range(model.rowCount())
            ] if model is not None else []
    p.check("① two_tables' output is a table of 4 rows × 2 columns: Berlin 120, Paris 80, Boston 95, Denver 60",
            preview.label.text() == "two_tables.output: 4 rows × 2 columns"
            and rows == [["Berlin", "120"], ["Paris", "80"], ["Boston", "95"], ["Denver", "60"]])
    p.check("② all_sales, the next block, got that table: 4 rows × 2 columns",
            (tab.block_info["all_sales"].get("previews") or {}).get("value", "").startswith("4 rows × 2 columns"))
    p.shot(win, "① The DataFrame two_tables set as its output came back as a table: Data preview shows its four "
                "rows, Berlin, Paris, Boston and Denver. ② all_sales, the block after it, got it as a table too: the "
                "line under it says value: 4 rows × 2 columns (readable in the close-up of step 6).",
           highlight=[[(preview.view, r) for r in range(len(rows))], "all_sales"], badges=True,
           zoom=[preview.label, *[(preview.view, r) for r in range(len(rows))]])
