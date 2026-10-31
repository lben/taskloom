"""Proof: when a block run again fails, its failure report shows the inputs it received. See README.md here."""

import re
from pathlib import Path

import yaml
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QPushButton, QToolBar

from proofs.conftest import choose, menu_item, right_click
from taskloom.editor.dialogs import AskDialog
from taskloom.history import History
from tests.test_lines import shown, wait_runs

ASKED = ("When running a Python Code using the \"run from this block on, with the same inputs ....\" it returned me "
         "this: … (Python Code) failed: … Inputs: input ← <query 1>.result: (nothing arrived) input ← "
         "<query 2>.result: (nothing arrived) input ← <query 3>.result: (nothing arrived) input ← <query 4>.result: "
         "(nothing arrived) …")
NOTHING = "(nothing arrived)"
SAME = "Run from this block on, with the same inputs"
PASSWORD = "Blue-Kettle-42"  # typed when ask_password asks

COMPARE_CODE = """\
totals = dict(sales.scan().collect().iter_rows())
for change in adjustments.values():
    for region, amount in change.items():
        totals[region] += amount
short = [region for region, target in targets.items() if totals[region] < target]
if short:
    raise ValueError("below target: " + ", ".join(short))
output = totals"""


def python(code: str, x: int, y: int, inputs=(), outputs=("output",)) -> dict:
    """A Python Code block, in Taskloom's own Python (the flow file leaves Run in my Python environment out)."""
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code},
            "ui": {"x": x, "y": y}}


SALES_FLOW = {
    "blocks": {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 210}},
        "load_sales": python("result = pl.DataFrame({'region': ['North', 'South', 'East'], 'amount': [120, 80, 60]})",
                             260, 0, outputs=("result",)),
        "load_targets": python("output = {'North': 100, 'South': 90}", 260, 140),
        "returns": python("output = {'South': -5}", 260, 280),
        "late_orders": python("output = {'East': 15}", 260, 420),
        "compare_targets": python(COMPARE_CODE, 580, 190, inputs=("sales", "targets", "adjustments")),
        "write_report": python("print(input)", 880, 190, inputs=("input",), outputs=()),
    },
    "edges": ["start.start -> load_sales.after", "start.start -> load_targets.after", "start.start -> returns.after",
              "start.start -> late_orders.after", "load_sales.result -> compare_targets.sales",
              "load_targets.output -> compare_targets.targets", "returns.output -> compare_targets.adjustments",
              "late_orders.output -> compare_targets.adjustments", "compare_targets.output -> write_report.input"],
}
SOURCES = ["load_sales", "load_targets", "returns", "late_orders"]
FULL_LINES = ["  sales ← load_sales.result: 3 rows × 2 columns",  # then the table's rows, on the same line
              '  targets ← load_targets.output: { "North": 100, "South": 90 }',
              '  adjustments ← returns.output: { "South": -5 }', '  adjustments ← late_orders.output: { "East": 15 }']
ROWS = ["region", "amount", "North", "120", "South", "80", "East", "60"]  # load_sales' table, in its line

ORDERS_FLOW = {
    "blocks": {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 110}},
        "load_orders": python("output = ['A-101', 'A-102', 'A-103']", 240, 110),
        "is_big": {"type": "logic.if", "config": {"condition": "len(value) > 5"}, "ui": {"x": 490, "y": 110}},
        "split_batches": python("output = [input[i:i + 5] for i in range(0, len(input), 5)]", 740, 0,
                                inputs=("input",)),
        "one_batch": python("output = [input]", 740, 220, inputs=("input",)),
        "send_orders": python("raise ConnectionError('the warehouse did not answer')", 1000, 110,
                              inputs=("batches",), outputs=()),
    },
    "edges": ["start.start -> load_orders.after", "load_orders.output -> is_big.value",
              "is_big.true -> split_batches.input", "is_big.false -> one_batch.input",
              "split_batches.output -> send_orders.batches", "one_batch.output -> send_orders.batches"],
}
ORDER_LINES = [f"  batches ← split_batches.output: {NOTHING}",
               '  batches ← one_batch.output: [ [ "A-101", "A-102", "A-103" ] ]']

UPLOAD_FLOW = {
    "blocks": {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 110}},
        "make_report": python("output = 'weekly sales: North 120, South 75, East 75'", 260, 0),
        "ask_password": {"type": "logic.ask_user",
                         "config": {"prompt": "Password for the report server", "kind": "password"},
                         "ui": {"x": 260, "y": 220}},
        "upload_report": python("raise PermissionError('the report server refused the password')", 580, 110,
                                inputs=("report", "password"), outputs=()),
    },
    "edges": ["start.start -> make_report.after", "start.start -> ask_password.after",
              "make_report.output -> upload_report.report", "ask_password.answer -> upload_report.password"],
}
UPLOAD_LINES = ["  report ← make_report.output: weekly sales: North 120, South 75, East 75",
                "  password ← ask_password.answer: ***"]


def open_flow(win, monkeypatch, path: Path, flow: dict):
    """Write `flow` to `path` and open it (File > Open…), in a tab of its own."""
    path.write_text(yaml.safe_dump(flow, sort_keys=False, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    QTest.qWait(100)
    return win.current


def press_run_now(win):
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)


def finished(qtbot, tab, n: int) -> int:
    """Wait for the tab's n-th run to end, and for what it wrote last to reach the Log: its id."""
    run_id = wait_runs(qtbot, tab, n)
    QTest.qWait(300)
    return run_id


def answer_password(qtbot, win, shoot=None):
    """ask_password asks: type the password and press OK (`shoot(dialog)` first, to show it)."""
    qtbot.waitUntil(lambda: any(d.isVisible() for d in win.findChildren(AskDialog)), timeout=60_000)
    ask = next(d for d in win.findChildren(AskDialog) if d.isVisible())
    QTest.keyClicks(ask.input, PASSWORD)
    if shoot is not None:
        shoot(ask)
    QTest.mouseClick(next(b for b in ask.findChildren(QPushButton) if b.text() == "OK"), Qt.LeftButton)


def report(win, run_id: int, block: str) -> tuple[str, list[str]]:
    """The failure report `block` wrote in run `run_id`, as the Log shows it: its first line, and its Inputs lines."""
    run, found = None, []
    for entry in win.log.entries:
        message = entry.get("message", "")
        started = re.match(r"run #(\d+) · ", message) if entry.get("header") else None
        if started:
            run = int(started.group(1))
        elif (run == run_id and entry.get("block") == block and entry.get("level") == "ERROR"
              and message.startswith(f"{block} (")):
            found.append(message.splitlines())
    assert len(found) == 1, f"{block} wrote {len(found)} failure reports in run #{run_id}"
    lines = found[0]
    assert "Inputs:" in lines, lines
    inputs = []
    for line in lines[lines.index("Inputs:") + 1:]:
        if not (line.startswith("  ") and " ← " in line):
            break
        inputs.append(line)
    return lines[0], inputs


def log_text(win) -> str:
    return win.log.text.toPlainText()


def _found(win, run_id: int | None, text: str) -> QTextCursor:
    """Where the Log shows `text` first in run `run_id` (from the line that run started with; None: anywhere)."""
    doc = win.log.text.document()
    started = QTextCursor(doc)
    if run_id is not None:
        started = doc.find(f"INFO run #{run_id} · ")
        assert not started.isNull(), f"the Log does not show run #{run_id}"
    found = doc.find(text, started)
    assert not found.isNull(), f"the Log does not show {text!r} in run #{run_id}"
    return found


def in_the_log(win, run_id: int | None, texts: list[str], upto: str | None = None) -> QRect:
    """The lines of the Log that show `texts` in run `run_id` (None: anywhere), in the window's pixels, from their
    start to their end, cut at the Log's right edge, or at the end of the text `upto` (for a close-up)."""
    box = win.log.text
    rect = QRect()
    for text in texts:
        line = QTextCursor(_found(win, run_id, text))
        line.movePosition(QTextCursor.StartOfLine)
        rect |= box.cursorRect(line)
        line.movePosition(QTextCursor.EndOfLine)
        rect |= box.cursorRect(line)
    visible = box.viewport().rect()
    if upto is not None:
        visible.setRight(box.cursorRect(_found(win, run_id, upto)).right() + 6)
    rect = rect.adjusted(-3, 0, 3, 0) & visible
    assert not rect.isEmpty(), f"the Log does not show {texts} of run #{run_id} where you look"
    return rect.translated(box.viewport().mapTo(win, QPoint(0, 0)))


def the_report(win, run_id: int, first: str, inputs: list[str], upto: str) -> QRect:
    """The Log's lines of a failure report in run `run_id`: from the line showing `first` to its last Inputs line,
    cut at the end of `upto`."""
    return in_the_log(win, run_id, [first, *(line.strip() for line in inputs)], upto)


def scroll_log_to(win, run_id: int | None, text: str):
    """Scroll the Log, as you would, so that the line showing `text` in run `run_id` (None: anywhere) is its second
    line, or as near it as the Log goes."""
    box = win.log.text
    line = QTextCursor(_found(win, run_id, text))
    line.movePosition(QTextCursor.StartOfLine)
    bar = box.verticalScrollBar()
    bar.setValue(bar.value() + box.cursorRect(line).top() - box.fontMetrics().lineSpacing() - 2)
    QTest.qWait(100)


def recorded(win) -> int:
    """How many runs the run history has."""
    history = History(win.home.history_db)
    try:
        return len(history.runs())
    finally:
        history.close()


def run_history_logs(win, *runs: int) -> list[str]:
    """Every message the run history keeps for these runs."""
    history = History(win.home.history_db)
    try:
        return [log["message"] for run_id in runs for log in history.logs(run_id)]
    finally:
        history.close()


def test_a_block_run_again_reports_the_inputs_it_received(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("When a block run again fails, its report shows the inputs it received", ASKED)
    p.idea("When a block you run again fails, its failure report in the Log now shows what came into each of its "
           "inputs, just as the report of a full run does. “(nothing arrived)” means what it says: nothing came by "
           "that connection.")
    p.idea("You run a block again by right-clicking it: Run only this block, or Run from this block on, with the same "
           "inputs or with inputs you choose. The report lists each connection into the block's inputs on a line of "
           "its own: the input, the block and output it comes from, and the value. That value is what the block was "
           "given for this run: what reached it in the earlier run, kept with that run, or what you typed.")
    p.idea("The values are written in the same short form as in a full run's report: each on one line, cut short "
           "when it is long. A table shows its size and its rows as far as that one line goes: a big table is never "
           "shown whole.")
    p.idea("Before, the report looked for each value among what the blocks before it had made in the same run. In a "
           "re-run those blocks do not run again, so for the block the re-run starts at every line said “(nothing "
           "arrived)”, although the block had its inputs: the Variables of a Python Code block, further down the "
           "same report, showed them.")
    p.why_simplest("The block already has these values: they are what it was given to run. The report now writes "
                   "those, instead of looking for them where they are not.")
    p.why_simplest("An input fed by two blocks receives both values together (a Python Code block gets them keyed by "
                   "block name). So that each connection still gets its own line, the re-run notes which connection "
                   "brought which part, by the same rule that put them together, and the report takes each part back "
                   "by that note. The note is kept with the inputs Taskloom already saves with every re-run, so "
                   "running the block again from that re-run shows the same lines.")
    p.alternative("Look each value up in the earlier run, in what the connected blocks made then",
                  "That is what those blocks made, which is not always what this block received: a value you typed "
                  "is not among them, so the report would show the old value instead of yours.")
    p.alternative("One line per input, with the whole value it received",
                  "A full run writes one line per connection, naming where each value came from. The two reports "
                  "would no longer read alike, and for an input fed by two blocks you could not see which of them "
                  "brought nothing, such as the path an If did not take.")
    p.alternative("Leave the inputs out of a re-run's report, and point to the earlier run",
                  "The report is where you look first when a block fails, and the earlier run does not have the "
                  "values you typed. You asked to see what the block received.")
    p.fits("The lines read as in a full run: “input ← block.output: value”, in the same short form, with “(nothing "
           "arrived)” only for a connection by which nothing came, in a full run and in a re-run alike.")
    p.fits("Passwords stay hidden as everywhere in Taskloom: a password shows as ***, and a password the run has "
           "asked for, or taken from Taskloom's password store, is replaced by *** wherever it appears in the "
           "report. Passwords are never saved with a run, so a block that takes one cannot run again on its own with "
           "the same inputs: Taskloom refuses and says why (step 9).")
    p.fits("The blocks after the one a re-run starts at already showed their inputs: what the blocks that run again "
           "give them, and what the blocks before the start held in the earlier run. That did not change (step 5).")
    p.fits("From a terminal it is the same: taskloom run FLOW --only BLOCK (or --from BLOCK) --inputs-from RUN writes "
           "the same report, and Run history keeps it with the run.")

    p.step("Open the flow sales-targets.yaml (File > Open…). compare_targets, a Python Code block, has three inputs: "
           "sales from load_sales (a table), targets from load_targets, and adjustments, fed by two blocks: returns "
           "and late_orders.")
    tab = open_flow(win, monkeypatch, tmp_path / "sales-targets.yaml", SALES_FLOW)
    p.check("compare_targets has its three inputs: sales and targets from one block each, adjustments from two",
            [(src, port, dst_port) for src, port, dst, dst_port in tab.doc.edges() if dst == "compare_targets"]
            == [("load_sales", "result", "sales"), ("load_targets", "output", "targets"),
                ("returns", "output", "adjustments"), ("late_orders", "output", "adjustments")])
    p.shot(win, "① compare_targets, with its inputs: sales from ② load_sales, targets from ③ load_targets, and "
                "adjustments from both ④ returns and late_orders. It adds the adjustments to each region's sales and "
                "fails when a region is below its target.",
           highlight=["compare_targets", "load_sales", "load_targets", ["returns", "late_orders"]], badges=True,
           zoom=[*SOURCES, "compare_targets"])

    p.step("Press ▶ Run now in the toolbar. compare_targets fails: South is below its target (80 − 5 = 75, against "
           "90). Scroll the Log, under the canvas, up to its failure report.")
    press_run_now(win)
    full = finished(qtbot, tab, 1)
    head, in_full = report(win, full, "compare_targets")
    scroll_log_to(win, full, "compare_targets (Python Code) failed")
    lines = the_report(win, full, "compare_targets (Python Code) failed", in_full, "below target: South (line 7)")
    p.shot(win, "The outlined lines of the Log: the full run's report, the one to compare with. compare_targets "
                "failed, and why; then its Inputs, one line per connection, each with the value that came by it.",
           highlight=[lines], zoom=[lines])
    p.text("The four Inputs lines of that report, in full (the first is wider than the Log)", "\n".join(in_full))
    p.check("compare_targets failed: “below target: South”",
            shown(tab, ["compare_targets"]) == {"compare_targets": ("failed", full)}
            and "failed: ValueError: below target: South" in head)
    p.check("The report has a line for each of the four connections, each with its value: the table's size and "
            "rows, and the three dictionaries",
            len(in_full) == 4 and in_full[0].startswith(FULL_LINES[0]) and all(v in in_full[0] for v in ROWS)
            and in_full[1:] == FULL_LINES[1:])
    p.note("load_sales' table shows as its size and its rows, squeezed onto one line. A value is cut short when it is "
           "long, so a big table is never shown whole. A re-run's report shows it the same way.")

    p.step(f"Right-click compare_targets and choose “{SAME}”. compare_targets runs again, with what reached it in "
           f"run #{full}, and fails again.")

    def run_from_compare_targets(menu):
        p.shot(menu, f"The menu of compare_targets, with the item to choose: it takes the inputs of run #{full}.",
               over=win, highlight=[menu_item(menu, SAME)], zoom=[menu, "compare_targets"])
        choose(menu, SAME)

    p.while_open(lambda: right_click(tab, "compare_targets"), run_from_compare_targets)
    again = finished(qtbot, tab, 2)
    _, in_again = report(win, again, "compare_targets")
    scroll_log_to(win, again, "compare_targets (Python Code) failed")
    said = f"with the inputs it got in run #{full}"
    lines = the_report(win, again, said, in_again, said)
    p.shot(win, f"The outlined lines: run #{again} ran from compare_targets on, with the inputs it got in run "
                f"#{full}. compare_targets failed again, and each of its Inputs lines shows the value that came by "
                "that connection, as in the full run: none says “(nothing arrived)”.",
           highlight=[lines], zoom=[lines])
    p.note("Before this change, each of these four lines said “(nothing arrived)”.")
    for n, name in enumerate(["sales ← load_sales.result: the table, 3 rows × 2 columns",
                              'targets ← load_targets.output: { "North": 100, "South": 90 }',
                              'adjustments ← returns.output: { "South": -5 }',
                              'adjustments ← late_orders.output: { "East": 15 }']):
        p.check(f"{name}, as in the full run", len(in_again) == 4 and in_again[n] == in_full[n])
    p.check("No line says “(nothing arrived)”", not any(NOTHING in line for line in in_again))

    p.step("Right-click compare_targets, choose “Run only this block, with inputs I choose…”, type other targets, "
           "{'North': 130, 'South': 70}, and press Run.")

    def type_targets(dialog):
        field = dialog.fields["targets"]
        field.selectAll()
        QTest.keyClicks(field, "{'North': 130, 'South': 70}")
        p.shot(dialog, f"Each input is filled in with what reached compare_targets in run #{again} (sales as the file "
                       "of its table); the targets are typed over.", highlight=[field])
        QTest.mouseClick(next(b for b in dialog.findChildren(QPushButton) if b.text() == "Run"), Qt.LeftButton)

    p.while_open(lambda: right_click(tab, "compare_targets"),
                 lambda menu: p.while_open(lambda: choose(menu, "Run only this block, with inputs I choose"),
                                           type_targets))
    typed = finished(qtbot, tab, 3)
    head, in_typed = report(win, typed, "compare_targets")
    scroll_log_to(win, typed, "compare_targets (Python Code) failed")
    lines = the_report(win, typed, "running only compare_targets", in_typed, "inputs you chose: targets")
    p.shot(win, f"The outlined lines: run #{typed} ran only compare_targets, with the inputs it got in run #{again} "
                "but the targets you chose. It failed again, now for North (120 against 130), so it used your "
                "targets, and its targets line shows them. The other lines show what reached it, as before.",
           highlight=[lines], zoom=[lines])
    p.check("It used the typed targets: it fails with “below target: North”",
            "failed: ValueError: below target: North" in head)
    p.check('targets ← load_targets.output shows the typed { "North": 130, "South": 70 }',
            len(in_typed) == 4 and in_typed[1] == '  targets ← load_targets.output: { "North": 130, "South": 70 }')
    p.check("The other three lines are as in the full run",
            [in_typed[n] for n in (0, 2, 3)] == [in_full[n] for n in (0, 2, 3)])

    p.step(f"Right-click returns, an earlier block, and choose “{SAME}”. returns runs again, then compare_targets; "
           f"load_sales, load_targets and late_orders do not run again: what they held in run #{full} is used.")
    p.while_open(lambda: right_click(tab, "returns"), lambda menu: choose(menu, SAME))
    later = finished(qtbot, tab, 4)
    _, in_later = report(win, later, "compare_targets")
    scroll_log_to(win, later, "compare_targets (Python Code) failed")
    said = f"with the inputs it got in run #{full}"
    lines = the_report(win, later, said, in_later, said)
    p.shot(win, f"The outlined lines: run #{later} ran from returns on, with what the blocks held in run #{full}. "
                "compare_targets failed again, and its report shows each value it received, as in the full run: also "
                "those from load_sales, load_targets and late_orders, which did not run again.",
           highlight=[lines], zoom=[lines])
    p.check(f"returns and compare_targets ran in run #{later}; load_sales, load_targets and late_orders still show "
            f"their results of run #{full}",
            shown(tab, [*SOURCES, "compare_targets"]) == {
                "load_sales": ("success", full), "load_targets": ("success", full), "returns": ("success", later),
                "late_orders": ("success", full), "compare_targets": ("failed", later)})
    p.check("compare_targets' report shows the same four lines as in the full run", in_later == in_full)
    p.note("This case already worked before this change: a block after the one a re-run starts at always took its "
           "values from the run itself. It is shown so you can see that every kind of re-run now agrees.")

    p.step("Open route-orders.yaml and press ▶ Run now. The If block is_big sends the orders either to split_batches "
           "(more than 5) or to one_batch; both are connected into send_orders' one input, batches. send_orders is "
           "set up to fail, as if the warehouse did not answer.")
    orders = open_flow(win, monkeypatch, tmp_path / "route-orders.yaml", ORDERS_FLOW)
    press_run_now(win)
    first = finished(qtbot, orders, 1)
    _, in_first = report(win, first, "send_orders")
    paths = ["split_batches", "one_batch"]
    p.shot(win, "With three orders, is_big took its false path: ① split_batches was skipped, ② one_batch ran. "
                "③ send_orders failed.",
           highlight=[*paths, "send_orders"], badges=True, zoom=["is_big", *paths, "send_orders"])
    p.check("① split_batches was skipped, ② one_batch ran, ③ send_orders failed",
            shown(orders, [*paths, "send_orders"]) == {"split_batches": ("skipped", first),
                                                       "one_batch": ("success", first),
                                                       "send_orders": ("failed", first)})
    p.check("Its report says nothing came from split_batches, and shows what came from one_batch",
            in_first == ORDER_LINES)

    p.step("Right-click send_orders and choose “Run only this block, with the same inputs”.")
    p.while_open(lambda: right_click(orders, "send_orders"),
                 lambda menu: choose(menu, "Run only this block, with the same inputs"))
    second = finished(qtbot, orders, 2)
    _, in_second = report(win, second, "send_orders")
    scroll_log_to(win, second, "send_orders (Python Code) failed")
    said = f"with the inputs it got in run #{first}"
    lines = the_report(win, second, said, in_second, said)
    p.shot(win, f"The outlined lines: run #{second} ran only send_orders, with the inputs it got in run #{first}. "
                "Its report, as in the full run: nothing came by split_batches, the path not taken; the orders came "
                "by one_batch.", highlight=[lines], zoom=[lines])
    p.check(f"batches ← split_batches.output: {NOTHING}, as in the full run",
            len(in_second) == 2 and in_second[0] == in_first[0] == ORDER_LINES[0])
    p.check('batches ← one_batch.output: [ [ "A-101", "A-102", "A-103" ] ], as in the full run',
            len(in_second) == 2 and in_second[1] == in_first[1] == ORDER_LINES[1])

    p.step("Open upload-report.yaml and press ▶ Run now. ask_password asks for the password of the report server: "
           "type it and press OK. upload_report takes the report from make_report and that password; it is set up to "
           "fail, as if the server refused the password.")
    upload = open_flow(win, monkeypatch, tmp_path / "upload-report.yaml", UPLOAD_FLOW)
    press_run_now(win)
    answer_password(qtbot, win, lambda ask: p.shot(ask, "ask_password asks; the password shows as dots.",
                                                   highlight=[ask.input]))
    first = finished(qtbot, upload, 1)
    _, in_first = report(win, first, "upload_report")
    scroll_log_to(win, first, "upload_report (Python Code) failed")
    lines = the_report(win, first, "upload_report (Python Code) failed", in_first, "refused the password (line 1)")
    p.shot(win, "The outlined lines: upload_report's report shows the report it got, and the password as ***.",
           highlight=[lines], zoom=[lines])
    p.check("The report shows make_report's text, and the password as ***", in_first == UPLOAD_LINES)

    p.step("Right-click upload_report and choose “Run only this block, with the same inputs”.")
    before = recorded(win)
    p.while_open(lambda: right_click(upload, "upload_report"),
                 lambda menu: choose(menu, "Run only this block, with the same inputs"))
    refused = "ask_password.answer held a password (or something that cannot be saved), so it was not kept"
    qtbot.waitUntil(lambda: refused in log_text(win) and not upload.running, timeout=60_000)
    QTest.qWait(300)
    scroll_log_to(win, None, refused)
    line = in_the_log(win, None, [refused])
    p.shot(win, "The outlined line: Taskloom refuses. The password was never saved, so there is no “same input” to "
                "give upload_report, and no run starts.", highlight=[line], zoom=[line])
    p.check("It is refused: the Log says the password was not kept, and no run is recorded",
            refused in log_text(win) and upload.runs[-1].run_id is None and recorded(win) == before)

    p.step(f"Right-click ask_password and choose “{SAME}”. ask_password asks again: type the password and press OK. "
           "make_report does not run again: its text of the first run is used.")
    p.while_open(lambda: right_click(upload, "ask_password"), lambda menu: choose(menu, SAME))
    answer_password(qtbot, win)
    second = finished(qtbot, upload, 3)
    _, in_second = report(win, second, "upload_report")
    scroll_log_to(win, second, "upload_report (Python Code) failed")
    said = f"with the inputs it got in run #{first}"
    lines = the_report(win, second, said, in_second, said)
    p.shot(win, f"The outlined lines: run #{second} ran from ask_password on. upload_report's report shows the report "
                f"from make_report, which did not run again (its text of run #{first}), and the password, still ***.",
           highlight=[lines], zoom=[lines])
    p.check("The report shows make_report's text from the first run, and the password as ***",
            in_second == UPLOAD_LINES
            and shown(upload, ["make_report", "upload_report"]) == {"make_report": ("success", first),
                                                                    "upload_report": ("failed", second)})
    p.check("The password appears nowhere: not in the Log, nor in the run history of either run",
            PASSWORD not in log_text(win) and not any(PASSWORD in m for m in run_history_logs(win, first, second)))
