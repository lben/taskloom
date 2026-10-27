"""Proof: a run fails when a block's error reaches no block that runs. See README.md here."""

from pathlib import Path

import yaml
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QToolBar

from proofs.conftest import choose, menu_item, right_click
from tests.test_lines import shown, tab_icon_is, wait_runs

ASKED = ("Not asked by you: found while proving v0.23.0's Python Code changes. A run said “success” although a block "
         "failed and the block its error went to was skipped, so the failure was hidden.")
WRONG = "ValueError: the order totals do not add up"


def flow(handler_takes: list[str]) -> dict:
    """check fails; its connections in `handler_takes` go into handler's one input; invoice needs check's orders and
    load_prices' prices."""
    python = "logic.python"
    return {
        "blocks": {
            "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 110}},
            "check": {"type": python, "config": {"inputs": [], "outputs": ["orders"],
                                                 "code": "raise ValueError('the order totals do not add up')"},
                      "ui": {"x": 260, "y": 0}},
            "handler": {"type": python, "config": {"inputs": ["input"], "outputs": [],
                                                   "code": "print('check failed:', input['error'])"},
                        "ui": {"x": 560, "y": 0}},
            "load_prices": {"type": python, "config": {"inputs": [], "outputs": ["prices"],
                                                       "code": "prices = {'tea': 3.5, 'coffee': 4.0}"},
                            "ui": {"x": 260, "y": 220}},
            "invoice": {"type": python, "config": {"inputs": ["prices", "orders"], "outputs": [],
                                                   "code": "print('invoice:', len(orders), 'orders')"},
                        "ui": {"x": 560, "y": 220}},
        },
        "edges": ["start.start -> check.after", *[f"check.{output} -> handler.input" for output in handler_takes],
                  "start.start -> load_prices.after", "load_prices.prices -> invoice.prices",
                  "check.orders -> invoice.orders"],
    }


SKIPPED = "check-orders.yaml"  # handler takes check's orders and its error: it is skipped
HANDLED = "check-orders-handled.yaml"  # handler takes only check's error: it runs
BLOCKS = ["start", "check", "handler", "load_prices", "invoice"]


def open_flow(win, monkeypatch, path: Path):
    """Open a flow file (File > Open…), in a tab of its own."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    QTest.qWait(100)
    return win.current


def press_run_now(win):
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)


def click_bottom_tab(win, panel):
    """Click one of the tabs under the canvas (the Log, Run history…)."""
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.bottom_tabs.indexOf(panel)).center())
    QTest.qWait(100)


def flow_tab(win, tab) -> QRect:
    """The flow's tab, above the canvas, in the window's pixels."""
    bar = win.tabs.tabBar()
    rect = bar.tabRect(win.tabs.indexOf(tab))
    return QRect(bar.mapTo(win, rect.topLeft()), rect.size())


def hover(tab, item, qtbot):
    """Rest the mouse on a canvas item: the tooltip it shows."""
    viewport = tab.view.viewport()
    at = tab.view.mapFromScene(item.sceneBoundingRect().center())
    QApplication.sendEvent(viewport, QHelpEvent(QHelpEvent.ToolTip, at, viewport.mapToGlobal(at)))
    qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
    return tooltip()


def tooltip():
    return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)


def log_has(win, *texts) -> bool:
    log = win.log.text.toPlainText()
    return all(text in log for text in texts)


def taskloom_run(env, *args):
    """`taskloom run check-orders.yaml ARGS` in a terminal: (what it printed, its exit code, the run's id)."""
    result = env.taskloom("run", SKIPPED, *args, encoding="utf-8", errors="replace")
    run_id, _ = env.last_run()
    return (result.stdout + result.stderr).strip(), result.returncode, run_id  # (a run's lines go to stderr)


# `env` before `win`: its Taskloom folder is the editor's (the same temporary one), and it is made first.
def test_a_run_fails_when_a_failed_blocks_error_reaches_no_block_that_runs(proof, env, win, qtbot, monkeypatch):
    p = proof("A run fails when a block's error reaches no block that runs", ASKED)
    p.idea("A failed block's failure counts as handled only when its error reaches a block that runs. When every "
           "block its red error output goes to is skipped, nothing dealt with the failure: the run now ends failed, "
           "as if the error output were connected to nothing.")
    p.idea("A block is skipped when something it needs never comes. The handler in this proof takes check's orders "
           "as well as check's error, so when check fails, no orders come and the handler is skipped. Before, the "
           "run still said success, because an error handler was connected: the failure was hidden from Run "
           "history, the flow's tab, the notifications and Always keep failed runs.")
    p.idea("Which blocks run and which are skipped did not change: only how the run ends. This holds for Run now, "
           "for taskloom run in a terminal, for the scheduler service, and for a run from a later block with the "
           "inputs of an earlier run.")
    p.why_simplest("The run already knows which of its blocks ran. When it ends, it asks one question of each block "
                   "that failed: did a block its error goes to run? If not, the run failed. The answer comes from "
                   "what happened, not from how the blocks are connected.")
    p.why_simplest("A run from a later block asks the same question of the earlier run it takes its inputs from: a "
                   "failure there whose error reached no block that ran fails this run too when it stops one of this "
                   "run's blocks, unless a block its error goes to runs this time.")
    p.alternative("Count a failure as handled when its error output is connected to a block of the run",
                  "That was the rule, and it is how the failure was hidden: a handler that was skipped counted as "
                  "having dealt with it. The run said success, nobody was told, and Keep runs could delete it like "
                  "any successful run.")
    p.alternative("Run the handler anyway when the error reaches it",
                  "That would change which blocks run. A block connected to a normal output of a failed block does "
                  "not run, because what it needs from it never came: the handler would start without the orders it "
                  "asks for. Which blocks run is right; only the verdict was wrong.")
    p.alternative("Warn about such a handler when you save the flow",
                  "A handler can be skipped for reasons only the run knows: an If took the other path, another block "
                  "it takes data from failed too, or it has one input for each block it covers and only some of them "
                  "failed. Judging from what actually ran covers every case, and needs no new message.")
    p.alternative("A new way for a run to end, such as “success with errors”",
                  "Run history, the flow's tab, the notifications, Always keep failed runs and the exit code of "
                  "taskloom run all know success and failed. A failure nothing dealt with is a failed run, as when "
                  "the error output is connected to nothing.")
    p.fits("A run that fails this way ends as any failed run does: Run history and the flow's tab say failed, "
           "taskloom run ends with exit code 1, a scheduled run tells you (and emails you, when you set that up), "
           "and Always keep failed runs keeps it.")
    p.fits("A handler that runs and then fails itself still handled the failure that reached it (it ran): its own "
           "failure is then judged the same way. A handler shared by several blocks, with all their errors going "
           "into one input, handles each failure that reaches it.")
    p.fits("A flow that relied on a skipped handler to keep its runs green will now show failed runs. That is the "
           "point: the failure was real. To handle it, connect the error output to a block that takes nothing else "
           "from the failed block, as check-orders-handled.yaml does in step 7.")

    assert win.home.root == env.home, "the editor and the command line use the same Taskloom folder"
    env.env["PYTHONIOENCODING"] = "utf-8"  # what taskloom run prints, as a terminal shows it (on Windows too)
    for name, handler_takes in ((SKIPPED, ["orders", "error"]), (HANDLED, ["error"])):
        (env.root / name).write_text(yaml.safe_dump(flow(handler_takes), sort_keys=False), encoding="utf-8")
    log = win.log.text

    p.step(f"Open the flow {SKIPPED} (File > Open…). check is set up to fail. Its orders and its red error output "
           "both go into handler's one input. invoice takes check's orders too, with the prices from load_prices.")
    tab = open_flow(win, monkeypatch, env.root / SKIPPED)
    p.check("The flow opens, with check's orders and check's error both connected into handler",
            tab is not None and [(s, o) for s, o, to, _ in tab.doc.edges() if to == "handler"]
            == [("check", "orders"), ("check", "error")])
    p.shot(win, "① check, set up to fail. ② handler: check's orders and its red, dashed error output both go into it. "
                "③ invoice, which needs check's orders and load_prices' prices.",
           highlight=["check", "handler", "invoice"], badges=True, zoom=BLOCKS)

    p.step("Press ▶ Run now in the toolbar.")
    press_run_now(win)
    first = wait_runs(qtbot, tab, 1)
    QTest.qWait(300)  # what the run printed last reaches the Log
    p.shot(win, "① check failed: it is red and says why. ② handler was skipped: check's orders never came. ③ invoice "
                "was skipped too, for the same reason. load_prices ran.",
           highlight=["check", "handler", "invoice"], badges=True, zoom=BLOCKS)
    p.check(f"① check failed, ② handler and ③ invoice were skipped, and load_prices ran, all in run #{first}",
            shown(tab) == {"start": ("success", first), "check": ("failed", first), "handler": ("skipped", first),
                           "load_prices": ("success", first), "invoice": ("skipped", first)})
    lines = [(log, "[check] ERROR"), (log, f"run #{first} failed")]
    p.shot(win, "The outlined lines of the Log, under the canvas: check failed, and why (the order totals do not add "
                f"up); then run #{first} failed.", highlight=lines, zoom=lines)
    p.check("The Log says check failed, and why", log_has(win, f"[check] ERROR check (Python Code) failed: {WRONG}"))
    p.check(f"The run ended failed: the Log says run #{first} failed",
            tab.runs[0].status == "failed" and log_has(win, f"run #{first} failed"))
    p.note("These blocks ran, or were skipped, just as before this change. What changed is how the run ends: "
           f"before, this run ended with “run #{first} success”.")

    p.step("Rest the mouse on the line under handler.")
    p.note("The Log shows what the blocks say and how each run ends; a skipped block says nothing there. The canvas "
           "says it was skipped, and why, under its name; taskloom run lists it too (step 8).")
    tip = hover(tab, tab.scene.nodes["handler"].footer, qtbot)
    said = tip.text()
    p.shot(tip, "handler was skipped because something before it failed: check, whose orders it takes.", over=win,
           highlight=[tip], zoom=[tip, "handler"])
    tip.hide()
    p.check("handler says it was skipped because something before it failed",
            said == "skipped: something before it failed")

    p.step("Look at the flow's tab, above the canvas.")
    name = flow_tab(win, tab)
    p.shot(win, "The flow's tab shows that its run failed: a red ✕, and its name in red.", highlight=[name],
           zoom=[name])
    p.check("The flow's tab shows the red ✕ of a failed run", tab_icon_is(win, tab, "failed"))

    p.step("Click the Run history tab, under the canvas.")
    click_bottom_tab(win, win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 1, timeout=10_000)
    p.shot(win, f"Run history lists run #{first}, started by start, as failed.", highlight=[table.item(0, 3)],
           zoom=[table.item(0, 0), table.item(0, 4)])
    p.check(f"Run history lists run #{first} as failed",
            [table.item(0, c).text() for c in (0, 2, 3)] == [str(first), "start", "failed"])

    p.step("Right-click load_prices and choose “Run from this block on, with the same inputs”. load_prices runs "
           f"again and invoice takes part again, with what the blocks before them gave in run #{first}. check gave "
           f"no orders in run #{first}, because it failed.")
    item = "Run from this block on, with the same inputs"

    def run_from_load_prices(menu):
        p.shot(menu, f"The menu of load_prices, with the item to choose: it takes the inputs of run #{first}.",
               over=win, highlight=[menu_item(menu, item)], zoom=[menu, "load_prices"])
        choose(menu, item)

    p.while_open(lambda: right_click(tab, "load_prices"), run_from_load_prices)
    second = wait_runs(qtbot, tab, 2)
    QTest.qWait(300)
    click_bottom_tab(win, win.log)
    p.shot(win, f"① load_prices ran again, in run #{second}. ② invoice was skipped again: check failed in run "
                f"#{first}, so there are no orders to give it. check and handler did not run again: they still show "
                f"what they showed after run #{first}.",
           highlight=["load_prices", "invoice"], badges=True, zoom=BLOCKS)
    p.check(f"① load_prices ran and ② invoice was skipped, in run #{second}; check and handler still show their "
            f"results of run #{first}",
            shown(tab) == {"start": ("success", first), "check": ("failed", first), "handler": ("skipped", first),
                           "load_prices": ("success", second), "invoice": ("skipped", second)})
    lines = [(log, f"run #{second} · "), (log, "running from load_prices on"), (log, f"run #{second} failed")]
    p.shot(win, f"The outlined lines of the Log: run #{second} started, ran from load_prices on, with the inputs of "
                f"run #{first}, and failed.", highlight=[lines], zoom=lines)
    p.check(f"The Log says run #{second} ran from load_prices on, with the inputs of run #{first}",
            log_has(win, f"running from load_prices on (it and the blocks after it), with the inputs it got in run "
                         f"#{first}"))
    p.check(f"Run #{second} ended failed", tab.runs[1].status == "failed" and log_has(win, f"run #{second} failed"))
    p.note(f"Run #{second} is stopped by check's failure in run #{first}, which nothing dealt with: handler was "
           f"skipped there, and it is not part of this run. Before this change, run #{second} ended with success.")

    p.step(f"Open {HANDLED} (File > Open…) and press ▶ Run now. It is the same flow, but only check's red error "
           "output goes into handler.")
    handled = open_flow(win, monkeypatch, env.root / HANDLED)
    p.check("Only check's error is connected into handler",
            [(s, o) for s, o, to, _ in handled.doc.edges() if to == "handler"] == [("check", "error")])
    press_run_now(win)
    third = wait_runs(qtbot, handled, 1)
    QTest.qWait(300)
    p.shot(win, "① check failed, as before. ② handler ran: only check's error goes into it, and that came. invoice "
                "was skipped, as always after check fails: it needs check's orders.",
           highlight=["check", "handler"], badges=True, zoom=BLOCKS)
    p.check(f"① check failed, ② handler ran, and invoice was skipped, in run #{third}",
            shown(handled, ["check", "handler", "invoice"]) == {
                "check": ("failed", third), "handler": ("success", third), "invoice": ("skipped", third)})
    lines = [(log, "[handler]"), (log, f"run #{third} success")]
    p.shot(win, f"The outlined lines of the Log: handler got check's error, and wrote it; then run #{third} ended "
                "with success: check's failure was handled.", highlight=[lines], zoom=lines)
    p.check("handler got check's error: it wrote “check failed: ValueError: the order totals do not add up”",
            log_has(win, f"[handler] INFO check failed: {WRONG}"))
    p.check(f"Run #{third} ended with success", handled.runs[0].status == "success"
            and log_has(win, f"run #{third} success"))
    both = [flow_tab(win, tab), flow_tab(win, handled)]
    p.shot(win, f"The two flows' tabs: ① {SKIPPED}, its last run failed (a red ✕); ② {HANDLED}, its run succeeded "
                "(a green dot).", highlight=both, badges=True, zoom=both)
    p.check(f"{HANDLED}'s tab shows the green dot of a run that succeeded, {SKIPPED}'s still the red ✕",
            tab_icon_is(win, handled, "success") and tab_icon_is(win, tab, "failed"))
    p.note("This is as before this change: a failure whose error reaches a block that runs is handled, and the run "
           "succeeds.")

    p.step(f"In a terminal, in the folder of the flows, run the first flow: taskloom run {SKIPPED}.")
    printed, code, fourth = taskloom_run(env)
    p.text(f"taskloom run {SKIPPED} printed", printed)
    p.check(f"taskloom run ends with “run #{fourth} failed” and exit code 1, and Run history has it as failed",
            printed.endswith(f"run #{fourth} failed") and code == 1
            and env.last_run() == (fourth, "failed"))
    p.check("taskloom run says check failed, and why, and that handler and invoice were skipped",
            f"[check] failed · {WRONG}" in printed and "[handler] skipped · something before it failed" in printed
            and "[invoice] skipped · something before it failed" in printed)
    p.note(f"Before this change, this same command ended with “run #{fourth} success” and exit code 0. The scheduler "
           "service starts scheduled runs with this same command, so it did not tell you either; now it does. (This "
           "proof does not run the old version.)")

    p.step(f"Then run it from load_prices on, with the inputs of that run: taskloom run {SKIPPED} --from load_prices "
           f"--inputs-from {fourth}.")
    printed, code, fifth = taskloom_run(env, "--from", "load_prices", "--inputs-from", fourth)
    p.text(f"taskloom run {SKIPPED} --from load_prices --inputs-from {fourth} printed", printed)
    p.check(f"taskloom run --from load_prices ends with “run #{fifth} failed” and exit code 1",
            printed.endswith(f"run #{fifth} failed") and code == 1)
    p.check(f"load_prices ran, and invoice was skipped: check failed in run #{fourth}",
            "[load_prices] success" in printed and "[invoice] skipped · something before it failed" in printed
            and "[check]" not in printed)
    p.note("This is the run that “Run from this block on, with the same inputs” starts in the editor (step 6). "
           "Before this change, it too ended with success and exit code 0.")
