"""Proof: right-click a run in Run history and choose Load run state: every block shows its result from that run, a
bar names it, re-runs take their inputs from it, and Leave goes back to the newest results (docs/promises/
load-state.yaml, LS-1, LS-2, LS-3). See README.md here."""

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from proofs.conftest import choose, menu_item
from proofs.test_proof_copy_executed_query import run_cells, run_row
from proofs.test_proof_failure_inputs import finished, in_the_log, open_flow, press_run_now, scroll_log_to
from proofs.test_proof_preview_long_error import click_tab, in_window
from proofs.test_proof_rerun_carries_on import ONLY, history_row, hugged, python, run_only, started, under

# The maintainer's words of 2026-11-11 (docs/promises/load-state.yaml, asked).
ASKED = 'Run history: right-click a run, "Load run state" (inputs and outputs of every block, "like a savegame")'
LOAD = "Load run state"
ITEMS = ["pen", "ink", "paper", "stapler"]
BLOCKS = ["load_orders", "clean", "report", "send"]
DOWN = "the mail server did not answer"
# send "sends" report's text: it adds it to sent.txt, unless the mail server is down (here: server_down.txt exists).
SEND = ("import os\n"
        "if os.path.exists('server_down.txt'):\n"
        f"    raise ConnectionError('{DOWN}')\n"
        "with open('sent.txt', 'a') as f:\n"
        "    f.write(text + '\\n')\n"
        "print('sent:', text)")
YESTERDAY = {"load_orders": "✓ result: 10 rows × 3 columns", "clean": "✓ result: 10 rows × 3 columns",
             "report": "✓ text: '10 orders'"}  # (send failed)
TODAY = {"load_orders": "✓ result: 12 rows × 3 columns", "clean": "✓ result: 12 rows × 3 columns",
         "report": "✓ text: '12 orders'", "send": "✓ done"}


def orders_csv(n: int) -> str:
    return "id,item,qty\n" + "".join(f"{1001 + k},{ITEMS[k % 4]},{k % 3 + 1}\n" for k in range(n))


def orders_flow() -> dict:
    """start -> load_orders (reads orders.csv) -> clean -> report (a text) -> send, on two rows."""
    return {
        "blocks": {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
                   "load_orders": {"type": "data.duckdb_sql", "ui": {"x": 240, "y": 0},
                                   "config": {"inputs": [], "sql": "select id, item, qty from 'orders.csv'"}},
                   "clean": python("print(len(orders), 'orders in')\nresult = orders.scan().filter(pl.col('qty') > 0)",
                                   480, 0, ("orders",), ("result",)),
                   "report": python("text = f'{len(rows)} orders'", 240, 160, ("rows",), ("text",)),
                   "send": python(SEND, 480, 160, ("text",), ())},
        "edges": ["start.start -> load_orders.after", "load_orders.result -> clean.orders",
                  "clean.result -> report.rows", "report.text -> send.text"],
    }


def sent(folder: Path) -> list[str]:
    """What send has sent so far, in order."""
    path = folder / "sent.txt"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def right_click_run(win, run_id: int):
    """Right-click run `run_id` in Run history, as you would, near the left of its Parameters (its menu opens there,
    clear of the run's number and status; it waits for a choice: see Proof.while_open)."""
    table = win.history_panel.table
    viewport = table.viewport()
    cell = table.visualItemRect(table.item(run_row(win, run_id), 5))
    at = QPoint(cell.left() + 30, cell.center().y())
    where = viewport.mapToGlobal(at)
    for kind, buttons in ((QEvent.MouseButtonPress, Qt.RightButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
        QApplication.sendEvent(viewport, QMouseEvent(kind, QPointF(at), QPointF(where), Qt.RightButton, buttons,
                                                     Qt.NoModifier))
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, where))


def shown(tab) -> dict:
    return {block: under(tab, block) for block in BLOCKS}


def says(win, run: int) -> str:
    """What the bar says while run `run`'s state is loaded."""
    return (f"Loaded state of run #{run} · {started(win, run)}: every block shows its result from it, and re-runs "
            "take their inputs from it")


def test_a_loaded_run_state_shows_that_run_re_runs_start_from_it_and_leave_goes_back(proof, win, qtbot, tmp_path,
                                                                                        monkeypatch):
    p = proof("Load a run's state: every block shows that run, re-runs start from it, and Leave goes back", ASKED)
    p.idea("Right-click a run in Run history and choose “Load run state”. Every block then shows its result from that "
           "run, as a saved game brings back where you were. A bar above the canvas names the run and when it "
           "started, and has a Leave button.")
    p.idea("While it is loaded, “Run only this block” and “Run from this block on” take their inputs from that run, "
           "not from the newest one. A re-run becomes the loaded state, and the bar names it. Leave, or ▶ Run now, "
           "goes back to the newest results.")
    p.idea("Here, yesterday's run could not send its report: the mail server did not answer. Today's run sent today's. "
           "Load yesterday's run and run send alone: it sends yesterday's “10 orders”, which today's run could not "
           "give it.")
    p.why_simplest("A block run again already takes its inputs from one earlier run, and its menu names that run. "
                   "Loading a state only says which run that is, for every block of the tab, until you leave it: "
                   "the menu's words, “with the same inputs” and “with inputs I choose” all follow. Nothing is copied "
                   "and nothing is written into the stored run; a re-run is a new run, listed in Run history.")
    p.alternative("Double-click the run in Run history, as before",
                  "It shows the run once: the first block you run again puts the newest results back, so the next "
                  "block would take today's data. And a run that ran one block alone would show only that block, not "
                  "the results it carried over. Double-clicking still works as before.")
    p.alternative("Copy the run's results into a new run",
                  "That would be a run nobody ran, in Run history beside the real ones. Loading only reads the run.")
    p.alternative("Choose the run in each block's menu",
                  "You would choose the same run on every block, every time, and nothing would say which run the "
                  "canvas shows. The bar says it once, for the whole tab.")
    p.fits("It is what a block run again already does: the re-run carries the run it starts from over, so the blocks "
           "it does not run keep that run's results, and blocks after it that it does not reach are greyed “old · ”.")
    p.fits("The loaded state belongs to its tab: runs that finish meanwhile, scheduled or from a terminal, are listed "
           "in Run history but do not change the tab, and Taskloom opens with the state still loaded after you close "
           "it. If the flow has changed since that run, Taskloom asks first.")

    p.step("Open orders.yaml (File > Open…) and press ▶ Run now. orders.csv holds yesterday's 10 orders: load_orders "
           "reads them, clean keeps them and report writes “10 orders”. send fails: the mail server did not answer.")
    (tmp_path / "orders.csv").write_text(orders_csv(10), encoding="utf-8")
    (tmp_path / "server_down.txt").write_text("", encoding="utf-8")
    tab = open_flow(win, monkeypatch, tmp_path / "orders.yaml", orders_flow())
    press_run_now(win)
    yesterday = finished(qtbot, tab, 1)
    as_it_ended = shown(tab)
    p.shot(win, f"Run #{yesterday}: ① load_orders, clean and report succeeded: 10 rows, “10 orders”. ② send failed: "
                f"{DOWN}.", highlight=[BLOCKS[:3], "send"], badges=True, zoom=BLOCKS)
    p.check("load_orders and clean show 10 rows × 3 columns, report “10 orders”",
            {block: under(tab, block) for block in YESTERDAY} == YESTERDAY)
    p.check(f"send failed: “{DOWN}”, and sent nothing",
            under(tab, "send").startswith("✕") and DOWN in win.log.text.toPlainText() and sent(tmp_path) == [])

    p.step("The next day 2 more orders have come (orders.csv holds 12) and the mail server is back. Press ▶ Run now "
           "again: everything succeeds, and send sends “12 orders”. Yesterday's “10 orders” was never sent.")
    (tmp_path / "orders.csv").write_text(orders_csv(12), encoding="utf-8")
    (tmp_path / "server_down.txt").unlink()
    press_run_now(win)
    today = finished(qtbot, tab, 2)
    p.shot(win, f"Run #{today}: every block succeeded with today's 12 orders; report wrote “12 orders” and send sent "
                "it.", highlight=[BLOCKS[:2], BLOCKS[2:]], zoom=BLOCKS)
    p.check(f"Run #{today} shows 12 rows × 3 columns and “12 orders”, and send succeeded", shown(tab) == TODAY)
    p.check("send has sent “12 orders” only", sent(tmp_path) == ["12 orders"])

    p.step(f"Click the Run history tab under the canvas, right-click run #{yesterday} and choose “{LOAD}”.")
    click_tab(win.bottom_tabs.tabBar(), "Run history")
    table = win.history_panel.table
    seen = {}

    def load(menu):
        item = menu_item(menu, LOAD)
        seen["item"], seen["enabled"] = item.text(), item.isEnabled()
        p.shot(menu, f"① Run #{yesterday}'s row, right-clicked. ② Its menu: “{LOAD}”.", over=win,
               highlight=[run_cells(win, yesterday), item], badges=True, zoom=[menu, run_cells(win, yesterday)])
        choose(menu, LOAD)

    p.while_open(lambda: right_click_run(win, yesterday), load)
    QTest.qWait(200)
    p.check(f"The menu has “{LOAD}”, and it can be chosen", seen == {"item": LOAD, "enabled": True})
    bar = tab.bar_text.text()
    p.shot(win, f"① The bar above the canvas: “{bar}”. ② Leave. ③ Every block shows what it showed when run "
                f"#{yesterday} ended: 10 rows, “10 orders”, and send's failure.",
           highlight=[in_window(win, tab.bar_text), tab.leave, [BLOCKS[:2], BLOCKS[2:]]], badges=True,
           zoom=[tab.bar, *BLOCKS])
    p.check(f"Every block shows what it showed when run #{yesterday} ended", shown(tab) == as_it_ended)
    p.check(f"The bar says “{says(win, yesterday)}”", tab.bar.isVisible() and bar == says(win, yesterday))
    p.check("The bar has a Leave button", tab.leave.isVisible() and tab.leave.text() == "Leave")
    p.check("Loading made no run: Run history still lists two", table.rowCount() == 2)

    named = f"{ONLY} (run #{yesterday} · {started(win, yesterday)})"
    p.step(f"Right-click send and choose “{named}”. Only send runs, with what it got in run #{yesterday}.")

    def send_menu(menu):
        seen["entry"] = menu_item(menu, ONLY).text()
        p.shot(menu, f"send's menu names the loaded run, #{yesterday}, as the one it takes its inputs from, not run "
                     f"#{today}, the newest.", over=win, highlight=[menu_item(menu, ONLY)], zoom=[menu, "send"])

    run_only(p, tab, "send", send_menu)
    again = finished(qtbot, tab, 3)
    p.check(f"The entry reads “{named}”", seen["entry"] == named)
    scroll_log_to(win, again, "running only send")
    took = f"running only send, with the inputs it got in run #{yesterday} · {started(win, yesterday)}"
    lines = [took, "sent: 10 orders"]
    bar = tab.bar_text.text()
    p.shot(win, f"① send ran (run #{again}) and succeeded. ② The bar names run #{again} now: the re-run is the loaded "
                f"state. ③ The Log: run #{again} ran only send, with the inputs of run #{yesterday}, and sent “10 "
                "orders”. load_orders, clean and report still show yesterday's results.",
           highlight=["send", in_window(win, tab.bar_text), hugged(in_the_log(win, again, lines))], badges=True,
           zoom=[tab.bar, *BLOCKS])
    p.check(f"send ran alone (run #{again}) and succeeded", again == today + 1 and under(tab, "send") == "✓ done")
    p.check("It sent yesterday's report: sent.txt now reads “12 orders”, then “10 orders”",
            sent(tmp_path) == ["12 orders", "10 orders"])
    p.check(f"The Log says “{took}”", took in win.log.text.toPlainText())
    p.check(f"The bar says “{says(win, again)}”", bar == says(win, again))
    p.check("load_orders, clean and report still show yesterday's 10 rows and “10 orders”",
            {block: under(tab, block) for block in YESTERDAY} == YESTERDAY)

    p.step("Click Leave on the bar.")
    QTest.mouseClick(tab.leave, Qt.LeftButton)
    QTest.qWait(200)
    p.shot(win, f"The bar is gone. ① load_orders, clean and report show their newest results again, today's run "
                f"#{today}: 12 rows, “12 orders”. ② send shows its newest, run #{again}, which has just sent "
                "yesterday's report.", highlight=[BLOCKS[:3], "send"], badges=True, zoom=BLOCKS)
    p.check("The bar is gone", not tab.bar.isVisible())
    p.check(f"load_orders, clean and report show run #{today}'s 12 rows and “12 orders”; send its success",
            shown(tab) == TODAY)
    click_tab(win.bottom_tabs.tabBar(), "Run history")
    p.shot(win, f"Run history lists three runs: ① the re-run, #{again}, is a run of its own (“only send”); ② run "
                f"#{yesterday}, which was loaded, still says it failed.",
           highlight=[run_cells(win, again), run_cells(win, yesterday)], badges=True, zoom=[in_window(win, table, 700)])
    p.check(f"Run history lists runs #{again}, #{today} and #{yesterday}: #{again} started by “only send”, "
            f"#{yesterday} still failed",
            table.rowCount() == 3 and table.item(history_row(win, again), 2).text() == "only send"
            and table.item(history_row(win, yesterday), 3).text() == "failed")
    tab.doc.dirty = False  # nothing to save when the window closes
