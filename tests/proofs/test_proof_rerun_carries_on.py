"""Proof: a block run again on its own is seen by the blocks after it, and what was made from older data is marked
old. See README.md here."""

import ast
import re
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_failure_inputs import finished, in_the_log, open_flow, press_run_now, scroll_log_to
from proofs.test_proof_html_template_formatting import click_block
from proofs.test_proof_inspect_log import double_click_run
from proofs.test_proof_preview_long_error import click_tab, in_window, line
from proofs.test_proof_result_time import hover
from proofs.test_proof_scheduler_start import click_empty
from taskloom.editor.panels import CodeEdit
from tests.test_rerun_carries_on import plain, rows_of
from tests.test_rerun_old_and_kept import strongest_colour

# A summary of the report, not the user's words; only the last four words are quoted.
ASKED = ("(A summary of what you reported, not your exact words.) A block would not run because the block before it "
         "had failed, although that block had been run again on its own and had succeeded. You added that it "
         "‘probably happens in other places’.")
ONLY = "Run only this block, with the same inputs"
CHOOSE = "Run only this block, with inputs I choose"
ITEMS = ["pen", "ink", "paper", "stapler"]
# orders.csv: 12 orders, the first 10 paid.
CSV = "id,item,qty,status\n" + "".join(
    f"{1001 + n},{ITEMS[n % 4]},{n % 3 + 1},{'paid' if n < 10 else 'open'}\n" for n in range(12))
WRONG = "select id, item, qty from 'order.csv'"  # the file is orders.csv: the query fails
EVERY = "select id, item, qty from 'orders.csv'"  # 12 rows
PAID = EVERY + " where status = 'paid'"  # 10 rows
BLOCKS = ["load_orders", "clean", "report", "send"]
AFTER = BLOCKS[1:]
TEN = {"load_orders": "✓ result: 10 rows × 3 columns", "clean": "✓ result: 10 rows × 3 columns",
       "report": "✓ text: '10 orders'", "send": "✓ done"}  # under each block after a full run of the 10 paid orders
TWELVE = "✓ result: 12 rows × 3 columns"
REFUSED = "clean still holds what it made from older data: retry clean first"
OLD_TIP = "Old: made before load_orders ran again (run #{}). Right-click → Run only this block to bring it up to date."


def python(code: str, x: int, y: int, inputs=(), outputs=("output",)) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code},
            "ui": {"x": x, "y": y}}


def orders(sql: str) -> dict:
    """start -> load_orders (the query) -> clean (says how many orders reached it) -> report (a text) -> send, on two
    rows: five blocks side by side would be drawn too small to read."""
    return {
        "blocks": {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
                   "load_orders": {"type": "data.duckdb_sql", "ui": {"x": 240, "y": 0},
                                   "config": {"inputs": [], "sql": sql}},
                   "clean": python("print(len(orders), 'orders in')\n"
                                   "result = orders.scan().filter(pl.col('qty') > 0)", 480, 0, ("orders",), ("result",)),
                   "report": python("text = f'{len(rows)} orders'", 240, 160, ("rows",), ("text",)),
                   "send": python("print('sent:', text)", 480, 160, ("text",), ())},
        "edges": ["start.start -> load_orders.after", "load_orders.result -> clean.orders",
                  "clean.result -> report.rows", "report.text -> send.text"],
    }


def open_orders(win, monkeypatch, folder: Path, sql: str):
    (folder / "orders.csv").write_text(CSV, encoding="utf-8")
    return open_flow(win, monkeypatch, folder / "orders.yaml", orders(sql))


def under(tab, block: str) -> str:
    """The line under a block on the canvas: its result."""
    return tab.scene.nodes[block].status_text


def sql_box(win) -> CodeEdit:
    """The SQL box of the block shown in Properties."""
    return next(c for c in win.properties.findChildren(CodeEdit) if c.isVisible())


def type_query(win, tab, sql: str) -> CodeEdit:
    """Click load_orders and type `sql` over its SQL, in Properties."""
    click_block(tab, "load_orders")
    box = sql_box(win)
    box.setFocus()
    box.selectAll()
    QTest.keyClicks(box, sql)
    return box


def leave_query(win, tab, sql: str):
    """Click an empty place of the canvas: what was typed goes into the flow, no block is selected, and the Log shows
    every block's lines."""
    click_empty(tab)
    QTest.qWait(100)
    assert tab.doc.blocks["load_orders"]["config"]["sql"] == sql
    assert win.log.block is None


def run_only(p, tab, block: str, look=None):
    """Right-click `block` and choose Run only this block, with the same inputs (`look(menu)` first, to show it)."""
    def pick(menu):
        if look is not None:
            look(menu)
        choose(menu, ONLY)

    p.while_open(lambda: right_click(tab, block), pick)


def started(win, run: int) -> str:
    """When run `run` started, as Run history shows it."""
    table = win.history_panel.table
    return next(table.item(row, 1).text() for row in range(table.rowCount()) if table.item(row, 0).text() == str(run))


def history_row(win, run: int) -> int:
    table = win.history_panel.table
    return next(row for row in range(table.rowCount()) if table.item(row, 0).text() == str(run))


def top_of(win, box, height: int = 80) -> QRect:
    """The first lines of a box of Properties, in the window's pixels (for a close-up)."""
    return in_window(win, box, 700).adjusted(0, 0, 0, height - box.height())


def hugged(lines: QRect) -> QRect:
    """Lines of the Log to outline: an outline is drawn 6 pixels around what it is given, which would cover the
    lines above and below."""
    return lines.adjusted(3, 5, -3, -5)


def grey(tab, block: str) -> bool:
    """A block's border and the line under it are drawn in grey: no green, no red."""
    return all(colour < 50 for colour in strongest_colour(tab, block).values())


def paid_orders_run(p, win, qtbot, tmp_path, monkeypatch):
    """The start of three stories: a full run that reads the 10 paid orders. Gives (the tab, the run)."""
    p.step("Open orders.yaml (File > Open…) and press ▶ Run now. load_orders, a DuckDB SQL block, reads the paid "
           "orders from orders.csv: 10 rows. clean keeps the orders with a quantity, report writes “10 orders”, send "
           "sends it. Every block succeeds.")
    tab = open_orders(win, monkeypatch, tmp_path, PAID)
    press_run_now(win)
    full = finished(qtbot, tab, 1)
    p.shot(win, f"Run #{full}: every block succeeded. load_orders and clean show 10 rows × 3 columns, report shows "
                "“10 orders”.", highlight=[BLOCKS[:2], BLOCKS[2:]], zoom=BLOCKS)
    p.check("Every block succeeded: load_orders and clean show 10 rows × 3 columns, report shows “10 orders”",
            {block: under(tab, block) for block in BLOCKS} == TEN)
    return tab, full


def every_order_alone(p, win, qtbot, tab, shoot=None) -> int:
    """Then load_orders' query is changed to read every order (`shoot(box)`: with the query typed), and load_orders
    runs alone: 12 rows. Gives that run."""
    p.step("Click load_orders and take “where status = 'paid'” out of its SQL, in Properties: it now reads every "
           "order. Click an empty place of the canvas, then right-click load_orders and choose “Run only this block, "
           "with the same inputs”.")
    box = type_query(win, tab, EVERY)
    if shoot is not None:
        shoot(box)
    leave_query(win, tab, EVERY)
    run_only(p, tab, "load_orders")
    return finished(qtbot, tab, 2)


def test_a_block_fixed_and_run_alone_is_seen_by_the_block_after_it(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("After you fix a failed block and run it alone, the block after it runs too", ASKED)
    p.idea("A block you run again on its own now gets what the block before it made the last time that block ran, "
           "also when that was a run of that one block. load_orders fails. You fix its query and run it alone: 12 "
           "rows. Now clean, run alone, runs with those 12 rows.")
    p.idea("Before, a block took its inputs from the run its own result came from. clean's came from the full run, "
           "where load_orders had failed. So the Log said “clean would not run: load_orders, before it, failed”, "
           "although load_orders had just succeeded. The only way on was to run the whole flow again.")
    p.idea("The menu names the run a block will take its inputs from: “Run only this block, with the same inputs (run "
           "#2 · its date and time)”. And the blocks after the one you ran again read “old · ” in grey until they run "
           "again too: what they show was made before.")
    p.why_simplest("The canvas already shows, under each block, the result of the latest run it took part in, and a "
                   "run of one block already carried on from one earlier run. The rule only chooses that run better: "
                   "the newest run among the results that the block and the blocks before it show, then the newest "
                   "re-run that carries on from it. All four “Run only…” and “Run from…” entries use it.")
    p.alternative("Always take the flow's newest run",
                  "The newest run may hold nothing of these blocks: a flow can have several lines, each run on its "
                  "own, and a run you stopped early never reached the blocks after.")
    p.alternative("Run the blocks before it again each time",
                  "You run one block again so as not to wait for the query a second time, nor to send again what "
                  "was already sent.")
    p.fits("The menu already named the run whose inputs a block gets. It still does: it now names the newer one.")
    p.fits("A block before it that fails again when run alone is reported as it is now: clean is then told “clean "
           "would not run: load_orders, before it, failed”, which is true.")

    p.step("Open orders.yaml (File > Open…) and press ▶ Run now. load_orders, a DuckDB SQL block, fails: its query "
           "reads the file order.csv, and the file is called orders.csv.")
    tab = open_orders(win, monkeypatch, tmp_path, WRONG)
    press_run_now(win)
    full = finished(qtbot, tab, 1)
    failed = "load_orders (DuckDB SQL) failed"
    scroll_log_to(win, full, failed)
    p.shot(win, "① load_orders failed: ✕ under it. ② clean, report and send did not run: “skipped · something before "
                "it failed”. ③ The Log says why: no file matches “order.csv”.",
           highlight=[*BLOCKS, (win.log.text, failed)], badges=[1, 2, 2, 2, 3], zoom=BLOCKS)
    p.check("load_orders failed: no file matches “order.csv”",
            under(tab, "load_orders").startswith("✕") and 'No files found that match the pattern "order.csv"'
            in win.log.text.toPlainText())
    p.check("clean, report and send were skipped",
            all(under(tab, block) == "skipped · something before it failed" for block in AFTER))

    p.step("Click load_orders and correct the file's name in its SQL, in Properties: orders.csv.")
    box = type_query(win, tab, EVERY)
    p.shot(win, "The query, corrected: it reads orders.csv.", highlight=[(box, "orders.csv")], zoom=[top_of(win, box)])
    p.check("The SQL box holds the corrected query", box.toPlainText() == EVERY)

    p.step("Click an empty place of the canvas, then right-click load_orders and choose “Run only this block, with "
           "the same inputs”. Only load_orders runs, and it succeeds: 12 rows.")
    leave_query(win, tab, EVERY)
    run_only(p, tab, "load_orders")
    alone = finished(qtbot, tab, 2)
    p.shot(win, f"① load_orders ran alone (run #{alone}) and succeeded: 12 rows × 3 columns. ② clean, report and send "
                "read “old · ” before what they showed, in grey: it was made before load_orders ran again.",
           highlight=BLOCKS, badges=[1, 2, 2, 2], zoom=BLOCKS)
    p.check(f"load_orders succeeded when run alone (run #{alone}): 12 rows × 3 columns",
            alone == full + 1 and under(tab, "load_orders") == TWELVE)
    p.check("clean, report and send read “old · skipped · something before it failed”, in grey; load_orders is green",
            all(under(tab, block) == "old · skipped · something before it failed" and grey(tab, block)
                for block in AFTER) and not grey(tab, "load_orders"))

    named = f"{ONLY} (run #{alone} · {started(win, alone)})"
    p.step(f"Right-click clean and choose “{named}”. clean runs, with the 12 rows.")
    seen = {}

    def clean_menu(menu):
        seen["entry"] = menu_item(menu, ONLY).text()
        p.shot(menu, f"The menu of clean names the run it will take its inputs from: run #{alone}, the one that has "
                     "just run load_orders alone.", over=win, highlight=[menu_item(menu, ONLY)], zoom=[menu, "clean"])

    run_only(p, tab, "clean", clean_menu)
    again = finished(qtbot, tab, 3)
    p.check(f"The entry reads “{named}”", seen["entry"] == named)
    p.shot(win, f"① clean ran (run #{again}): 12 rows × 3 columns, and it is no longer old. ② report and send still "
                "are: run them the same way, one after the other.",
           highlight=AFTER, badges=[1, 2, 2], zoom=BLOCKS)
    p.check(f"clean ran alone (run #{again}) and shows 12 rows × 3 columns, not old",
            again == alone + 1 and under(tab, "clean") == TWELVE and not grey(tab, "clean"))
    p.check("report and send still read “old · ”", all(under(tab, block).startswith("old · ") for block in AFTER[1:]))

    p.step(f"Look at run #{again} in the Log.")
    scroll_log_to(win, again, "running only clean")
    took = f"running only clean, with the inputs it got in run #{alone} · {started(win, alone)}"
    lines = [took, "12 orders in", "success · result: 12 rows × 3 columns"]
    p.shot(win, f"The outlined lines: run #{again} ran only clean, with the inputs it got in run #{alone}. clean says "
                "“12 orders in” (it prints how many orders reached it), and gives 12 rows × 3 columns.",
           highlight=[hugged(in_the_log(win, again, lines))], zoom=[in_the_log(win, again, lines, upto=took)])
    p.note("Before this change the Log said “clean would not run: load_orders, before it, failed” here, and clean did "
           "not run.")
    said = win.log.text.toPlainText()
    p.check(f"The Log says “{took}”", took in said)
    p.check("clean got the 12 rows: it printed “12 orders in”", "[clean] INFO 12 orders in" in said)
    p.check("The Log does not say “would not run”", "would not run" not in said)
    tab.doc.dirty = False  # nothing to save when the window closes


def test_what_was_made_from_older_data_is_marked_old_and_never_passed_on_silently(proof, win, qtbot, tmp_path,
                                                                                  monkeypatch):
    p = proof("What was made from older data is marked old, and is never passed on without a word", ASKED)
    p.idea("This is the case you would not notice, because nothing fails. A full run reads the 10 paid orders. You "
           "change load_orders' query to read every order and run it alone: 12 rows. clean, report and send still "
           "hold what they made from the 10.")
    p.idea("Now each of them reads “old · ” before its result, in grey with a grey border. Rest the mouse on it and "
           "it says why: “Old: made before load_orders ran again (run #2). Right-click → Run only this block to bring "
           "it up to date.” Data preview says “(old)” too.")
    p.idea("And an old result is never handed on. Run report alone while clean is still old, and Taskloom refuses: "
           "“clean still holds what it made from older data: retry clean first”. Run clean alone, then report alone: "
           "report gets the 12 rows. Each mark goes when its block has run again.")
    p.idea("Before, nothing marked these results, and clean run alone took the 10 rows of the full run again, without "
           "a word.")
    p.why_simplest("A result is old when a block before it shows a result of a newer run. The editor works that out "
                   "from what the canvas already shows under each block, each time those results change. Nothing "
                   "more is saved for it.")
    p.alternative("Run the blocks after it again, automatically",
                  "send would send again and a slow block would run again, when you only wanted to check one query. "
                  "You choose: one block (“Run only this block”) or all of them in order (“Run from this block on”).")
    p.alternative("Clear the results of the blocks after it",
                  "You would lose what they made, which you may still want to look at or compare. Grey with “old · ” "
                  "keeps it and says it is not up to date.")
    p.alternative("Let report run with clean's old rows",
                  "A report made of old rows looks just like a right one. So Taskloom refuses, and names the block "
                  "to retry first.")
    p.fits("It goes with “each block shows its latest result”: the mark says when that latest result is older than "
           "what is before it.")
    p.fits("A full run starts afresh: after ▶ Run now nothing is old. “Run from this block on”, chosen on clean, "
           "brings clean, report and send up to date in one run.")

    tab, full = paid_orders_run(p, win, qtbot, tmp_path, monkeypatch)
    alone = every_order_alone(p, win, qtbot, tab, lambda box: p.shot(
        win, "The query now reads every order: “where status = 'paid'” is gone.", highlight=[(box, "orders.csv")],
        zoom=[top_of(win, box)]))
    p.shot(win, f"① load_orders ran alone (run #{alone}): 12 rows × 3 columns. ② clean, report and send read “old · ” "
                "before their results, in grey with a grey border: they were made from the 10 rows.",
           highlight=BLOCKS, badges=[1, 2, 2, 2], zoom=BLOCKS)
    p.check(f"load_orders ran alone (run #{alone}) and shows 12 rows × 3 columns",
            alone == full + 1 and under(tab, "load_orders") == TWELVE)
    p.check("clean, report and send read “old · ” before what they showed after the full run",
            {block: under(tab, block) for block in AFTER} == {block: "old · " + TEN[block] for block in AFTER})
    p.check("The three are drawn in grey, border and text; load_orders is green",
            all(grey(tab, block) for block in AFTER) and not grey(tab, "load_orders"))

    p.step("Rest the mouse on the line under clean, “old · ✓ result: 10 rows × 3 columns”.")
    tip = hover(qtbot, tab, "clean")
    says = plain(tip.text())
    p.shot(tip, f"The tooltip starts with why it is old, “made before load_orders ran again (run #{alone})”, and how "
                "to bring it up to date. Under that, the 10 rows clean still holds.",
           over=win, highlight=[tip], zoom=[tip])
    tip.hide()
    p.check(f"It says “{OLD_TIP.format(alone)}”", says.startswith(OLD_TIP.format(alone)))

    p.step("Click clean, then the Data preview tab under the canvas.")
    click_block(tab, "clean")
    click_tab(win.bottom_tabs.tabBar(), "Data preview")
    preview = win.preview
    p.shot(win, "The line above the table says “clean.result (old): 10 rows × 3 columns”.",
           highlight=[line(win, preview.label)], zoom=[in_window(win, preview, 700)])
    p.check("Data preview's line reads “clean.result (old): 10 rows × 3 columns”",
            preview.label.text() == "clean.result (old): 10 rows × 3 columns")

    p.step("Click the Log tab, and Show all above it. Then right-click report and choose “Run only this block, with "
           "the same inputs”, without running clean first. Taskloom refuses.")
    click_tab(win.bottom_tabs.tabBar(), "Log")
    QTest.mouseClick(win.log.clear_filter, Qt.LeftButton)
    run_only(p, tab, "report")
    refused = finished(qtbot, tab, 3)  # (what was refused is the third the editor started; it has no number)
    scroll_log_to(win, None, REFUSED)
    p.shot(win, f"The outlined line of the Log: “{REFUSED}”. report did not run: on the canvas it still reads "
                "“old · ”.",
           highlight=[(win.log.text, REFUSED)], zoom=[in_the_log(win, None, [REFUSED], upto=REFUSED)])
    p.text("That line of the Log, in full",
           next(text for text in win.log.text.toPlainText().splitlines() if REFUSED in text))
    p.note("The Log shows this line while it shows all blocks. After you click a block, the Log shows that block's "
           "lines only, and this one is not among them: press Show all.")
    p.check(f"The Log says “{REFUSED}”", REFUSED in win.log.text.toPlainText())
    p.check("report did not run: no run was recorded, and report still reads “old · ✓ text: '10 orders'”",
            refused is None and win.history_panel.table.rowCount() == 2
            and under(tab, "report") == "old · " + TEN["report"])

    p.step("Right-click clean and choose “Run only this block, with the same inputs”.")
    run_only(p, tab, "clean")
    clean = finished(qtbot, tab, 4)
    p.shot(win, f"① clean ran (run #{clean}) with the 12 rows, and is no longer old. ② report and send still are.",
           highlight=AFTER, badges=[1, 2, 2], zoom=BLOCKS)
    p.check(f"clean ran alone (run #{clean}): 12 rows × 3 columns, in green, not old",
            clean == alone + 1 and under(tab, "clean") == TWELVE and not grey(tab, "clean"))
    p.check("report and send still read “old · ”, in grey",
            all(under(tab, block) == "old · " + TEN[block] and grey(tab, block) for block in AFTER[1:]))

    p.step("Right-click report and choose the same entry. This time it runs.")
    run_only(p, tab, "report")
    report = finished(qtbot, tab, 5)
    p.shot(win, f"① report ran (run #{report}) and says “12 orders”: it got clean's 12 rows. ② send is the only one "
                "still old, until it runs again too.", highlight=AFTER[1:], badges=True, zoom=BLOCKS)
    p.check(f"report ran alone (run #{report}) and shows “12 orders”, not old",
            report == clean + 1 and under(tab, "report") == "✓ text: '12 orders'" and not grey(tab, "report"))
    p.check("send still reads “old · ✓ done”", under(tab, "send") == "old · ✓ done")
    p.note("To bring clean, report and send up to date in one go, choose “Run from this block on, with the same "
           "inputs” on clean instead.")
    tab.doc.dirty = False


def test_inputs_i_choose_opens_filled_in_from_the_newest_run(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("“With inputs I choose…” opens filled in with what the newest run made", ASKED)
    p.idea("“Run only this block, with inputs I choose…” opens a window with each input filled in. It is now filled "
           "in from the same run as “with the same inputs”. After load_orders ran alone and gave 12 rows, clean's "
           "window opens with those 12 rows, and says which run they are from.")
    p.idea("Before, it was filled in from the run clean's own result came from, the full run: the 10 rows load_orders "
           "had made before you changed its query.")
    p.why_simplest("The window asks the same question as the menu entry above it, “which run?”, and gets the same "
                   "answer. One rule for all the entries: they cannot disagree.")
    p.alternative("Open the window empty",
                  "You would have to find the table's file yourself each time, although Taskloom knows which run "
                  "made it.")
    p.alternative("Keep filling it in from the full run",
                  "“With the same inputs” would give clean 12 rows and “with inputs I choose” 10, on the same "
                  "right-click.")
    p.fits("The window works as before: a table is given as the file it was saved in, you change what you want and "
           "press Run.")
    p.fits("It says “Filled in: what reached the block in run #2 · its date and time”: the run's name as the menu "
           "writes it.")

    tab, full = paid_orders_run(p, win, qtbot, tmp_path, monkeypatch)
    alone = every_order_alone(p, win, qtbot, tab)
    p.shot(win, f"① load_orders ran alone (run #{alone}): 12 rows. ② clean still shows the 10 rows of run #{full}, "
                "marked old.", highlight=BLOCKS[:2], badges=True, zoom=BLOCKS)
    p.check(f"load_orders shows the 12 rows of run #{alone}, clean the 10 rows of run #{full}, marked old",
            alone == full + 1 and under(tab, "load_orders") == TWELVE and under(tab, "clean") == "old · " + TEN["clean"])

    p.step("Right-click clean and choose “Run only this block, with inputs I choose…”. Drag the lower edge of the "
           "window that opens down, until its first lines show whole. Click in orders and press End, to see the end "
           "of the file's name. Then press Run.")
    seen = {}

    def look(dialog):
        intro, field = dialog.findChild(QLabel), dialog.fields["orders"]
        seen["says"], seen["orders"] = plain(intro.text()), field.text()
        seen["cut"] = intro.height() < intro.heightForWidth(intro.width())
        dialog.resize(dialog.width(), dialog.layout().totalHeightForWidth(dialog.width()))
        QTest.mouseClick(field, Qt.LeftButton)
        QTest.keyClick(field, Qt.Key_End)
        p.shot(dialog, f"① The window says “Filled in: what reached the block in run #{alone}”. ② orders is filled in "
                       f"with a table's file: result.parquet, of load_orders, in the folder of run {alone}.",
               highlight=[intro, field], badges=True)
        QTest.mouseClick(next(b for b in dialog.findChildren(QPushButton) if b.text() == "Run"), Qt.LeftButton)

    p.while_open(lambda: right_click(tab, "clean"),
                 lambda menu: p.while_open(lambda: choose(menu, CHOOSE), look))
    chosen = finished(qtbot, tab, 3)
    # Once the window opens tall enough for its introduction, take the dragging out of the step and the note below.
    assert seen["cut"], "the window now shows its introduction whole as it opens: this proof still drags it taller"
    p.note("As it opens, the window is too short for its first lines: “Filled in: what reached the block in run …” "
           "is cut off until you make the window taller. The picture is taken after that.")
    p.text("What orders was filled in with", seen["orders"])
    file = Path(ast.literal_eval(re.fullmatch(r"table\((.+)\)", seen["orders"])[1])).resolve()
    p.check(f"The window says “Filled in: what reached the block in run #{alone} · {started(win, alone)}.”",
            f"Filled in: what reached the block in run #{alone} · {started(win, alone)}." in seen["says"])
    p.check(f"orders was filled in with the table load_orders made in run #{alone}: 12 rows",
            rows_of(seen["orders"]) == 12
            and (win.home.runs_dir / str(alone) / "load_orders").resolve() in file.parents)
    p.shot(win, f"clean ran (run #{chosen}) with what the window was filled in with: 12 rows × 3 columns. It is no "
                "longer old.", highlight=["clean"], zoom=BLOCKS)
    p.check(f"clean ran alone (run #{chosen}) and shows 12 rows × 3 columns, not old",
            chosen == alone + 1 and under(tab, "clean") == TWELVE and not grey(tab, "clean"))
    tab.doc.dirty = False


def test_a_run_opened_from_run_history_is_used_as_it_is(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("A run you open from Run history is used as it is", ASKED)
    p.idea("This is the one exception to the new rule. Double-click a run in Run history and the canvas shows that "
           "run. While it does, a block you run again takes that very run, not a newer one: you opened it to work "
           "with it.")
    p.idea("Run #1 read 10 orders. Run #2 ran load_orders alone and gave 12. Open run #1 from Run history: clean's "
           "menu names run #1, and clean, run alone, gets run #1's 10 rows.")
    p.why_simplest("What you see is what is used: the canvas shows one run, whole, and the menu names it. Nothing is "
                   "marked old either, since every result on the canvas is that run's.")
    p.alternative("Look for a newer re-run here too",
                  "The canvas would show run #1's 10 rows while clean ran with run #2's 12: the menu would name a "
                  "run you are not looking at.")
    p.fits("Double-clicking a run in Run history already showed that run on the canvas and in the Log, and the "
           "status bar says “Showing run #1 · its date and time” while it does.")
    p.fits("Starting any run puts the canvas back to each block's latest result, as before.")

    tab, full = paid_orders_run(p, win, qtbot, tmp_path, monkeypatch)
    alone = every_order_alone(p, win, qtbot, tab)
    p.shot(win, f"① load_orders ran alone (run #{alone}): 12 rows. ② The blocks after it still show what they made "
                f"in run #{full}, marked old.", highlight=BLOCKS, badges=[1, 2, 2, 2], zoom=BLOCKS)
    p.check(f"load_orders shows the 12 rows of run #{alone}; clean, report and send read “old · ”",
            alone == full + 1 and under(tab, "load_orders") == TWELVE
            and all(under(tab, block) == "old · " + TEN[block] for block in AFTER))

    p.step(f"Click the Run history tab, under the canvas, and double-click run #{full}, the full run.")
    click_tab(win.bottom_tabs.tabBar(), "Run history")
    table = win.history_panel.table
    p.shot(win, f"Run history lists the two runs: ① run #{alone} ran only load_orders, ② run #{full} is the full run. "
                "Double-click ②.",
           highlight=[[table.item(history_row(win, run), column) for column in range(5)] for run in (alone, full)],
           badges=True,
           zoom=[top_of(win, table, 90)])
    p.check(f"Run history lists run #{alone} as “only load_orders” and run #{full} as started by start",
            table.item(history_row(win, alone), 2).text() == "only load_orders"
            and table.item(history_row(win, full), 2).text() == "start")
    double_click_run(win, full)
    qtbot.waitUntil(lambda: win.bottom_tabs.currentWidget() is win.log, timeout=10_000)
    QTest.qWait(200)
    status = win.statusBar()
    showing = QRect(status.mapTo(win, QPoint(0, 0)),
                    QSize(max(status.fontMetrics().horizontalAdvance(status.currentMessage()) + 16, 300),
                          status.height()))
    p.shot(win, f"① The canvas shows run #{full} again: 10 rows, “10 orders”, and nothing is old. ② The status bar, "
                f"at the bottom, says “{status.currentMessage()}”.",
           highlight=[BLOCKS[:2], BLOCKS[2:], showing], badges=[1, 1, 2], zoom=BLOCKS)
    p.check(f"The canvas shows what run #{full} made, and nothing is old",
            {block: under(tab, block) for block in BLOCKS} == TEN)
    p.check(f"The status bar says “Showing run #{full} · {started(win, full)}”",
            status.currentMessage() == f"Showing run #{full} · {started(win, full)}")

    named = f"{ONLY} (run #{full} · {started(win, full)})"
    p.step(f"Right-click clean and choose “{named}”.")
    seen = {}

    def clean_menu(menu):
        seen["entry"] = menu_item(menu, ONLY).text()
        p.shot(menu, f"The menu of clean names run #{full}, the run on the canvas, not run #{alone}.",
               over=win, highlight=[menu_item(menu, ONLY)], zoom=[menu, "clean"])

    run_only(p, tab, "clean", clean_menu)
    again = finished(qtbot, tab, 3)
    p.check(f"The entry reads “{named}”", seen["entry"] == named)
    scroll_log_to(win, again, "running only clean")
    took = f"running only clean, with the inputs it got in run #{full} · {started(win, full)}"
    lines = [took, "10 orders in", "success · result: 10 rows × 3 columns"]
    p.shot(win, f"The outlined lines of the Log: run #{again} ran only clean, with the inputs it got in run #{full}. "
                f"clean says “10 orders in”: run #{full}'s 10 rows, not the 12 of run #{alone}.",
           highlight=[hugged(in_the_log(win, again, lines))], zoom=[in_the_log(win, again, lines, upto=took)])
    said = win.log.text.toPlainText()
    p.check(f"The Log says “{took}”", took in said)
    p.check(f"clean got run #{full}'s 10 rows: it printed “10 orders in”, as in run #{full}",
            said.count("[clean] INFO 10 orders in") == 2 and "12 orders in" not in said)
    p.note(f"Once that run has started, the canvas shows each block's latest result again: load_orders the 12 rows of "
           f"run #{alone}, and clean the 10 rows it has just made from run #{full}'s. clean is not marked old: its "
           "result is the newest on the canvas.")
    p.check("After it the canvas shows each block's latest result: load_orders 12 rows, clean 10 rows",
            under(tab, "load_orders") == TWELVE and under(tab, "clean") == TEN["clean"])
    tab.doc.dirty = False
