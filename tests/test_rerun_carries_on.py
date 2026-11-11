"""A re-run is seen by the blocks after it: the rows RR-1 to RR-10 of docs/promises/reruns.yaml (agreed 2026-11-11).
RR-5 (what a re-run keeps: a Loop's items, a Merger's lines, a Session's cookies) and RR-7 (a result made before an
earlier block ran again is marked old) are in tests/test_rerun_old_and_kept.py.

Everything goes through the real editor window (the `win` fixture: offscreen, a temporary Taskloom folder) and the
entries of a block's right-click menu; each run is a real runner process. A test reads only what a user can see:
what a block received (each block notes what reached it in <block>_got.txt, one line per time it ran), the Log's
words, the menu's words, the text under a block on the canvas and what the "inputs I choose" window is filled in with.

The rows' example is one flow, load_orders -> clean -> report -> send, and "re-run alone" is right-click > Run only
this block, with the same inputs. Run numbers start at 1 here: the rows' full run #5 is run #1, their #6 is run #2.
"""
import ast
import html
import os
import re
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml
from hypothesis import HealthCheck, Phase, example, given, settings
from hypothesis import strategies as st

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import polars as pl  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from taskloom.editor import dialogs  # noqa: E402
from taskloom.history import History  # noqa: E402

RUN_ONLY = "Run only this block, with the same inputs"
RUN_ON = "Run from this block on, with the same inputs"
CHOOSE_ONLY = "Run only this block, with inputs I choose"
CHOOSE_ON = "Run from this block on, with inputs I choose"
OLD_BETWEEN = "{0} still holds what it made from older data: retry {0} first"  # RR-8's refusal


# --- the flows ---------------------------------------------------------------------------------------------------------

def python(code: str, inputs=("input",), outputs=("output",)) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code}}


def noting(name: str, what: str = "input") -> str:
    """Code that notes what reached the block `name`: a line in <name>_got.txt each time it runs, and its name in
    ran.txt (the order the blocks ran in)."""
    return (f"with open('{name}_got.txt', 'a') as f:\n    f.write(str({what}) + '\\n')\n"
            f"with open('ran.txt', 'a') as f:\n    f.write('{name}\\n')\n")


def write_flow(folder: Path, blocks: dict, edges) -> Path:
    path = folder / "flow.yaml"
    path.write_text(yaml.safe_dump({"blocks": {"manual": {"type": "trigger.manual"}, **blocks}, "edges": list(edges)},
                                   sort_keys=False), encoding="utf-8")
    return path


QUERY = "int(open('orders.txt').read())"  # load_orders' "query": as many rows as orders.txt says (no file: it fails)
LOAD_ORDERS = f"n = {QUERY}\nresult = pl.DataFrame({{'id': list(range(n)), 'item': ['pen'] * n, 'qty': [1] * n}})"
ORDERS = ("load_orders", "clean", "report", "send")


def orders_flow(folder: Path) -> Path:
    """manual -> load_orders (a table of 3 columns) -> clean (the table as it got it) -> report (the text "<rows>
    orders") -> send (fails while mail_down.txt exists) -> archive (after send is done; it gets no data). clean and
    report note how many rows reached them, send the text."""
    return write_flow(folder, {
        "load_orders": python(LOAD_ORDERS, (), ("result",)),
        "clean": python(noting("clean", "len(orders)") + "result = orders", ("orders",), ("result",)),
        "report": python(noting("report", "len(rows)") + "text = f'{len(rows)} orders'", ("rows",), ("text",)),
        "send": python(noting("send", "text") + "import os\nif os.path.exists('mail_down.txt'):\n"
                       "    raise ValueError('mail server down')", ("text",), ()),
        "archive": python("open('archive_ran.txt', 'a').write('x')", (), ()),
    }, ["manual.start -> load_orders.after", "load_orders.result -> clean.orders", "clean.result -> report.rows",
        "report.text -> send.text", "send.done -> archive.after"])


def set_version(folder: Path, block: str, version) -> None:
    """What `block` (of chain_flow or branches_flow) adds to its output from its next run on: a change made outside
    the flow, as fixing a query's table or a file would be."""
    (folder / f"ver_{block}.txt").write_text(str(version), encoding="utf-8")


def versioned(name: str, first: bool = False) -> dict:
    """A block that gives its name and its version (see set_version), after what reached it."""
    version = f"open('ver_{name}.txt').read()"
    return python(f"output = '{name}' + {version}", ()) if first else python(
        noting(name) + f"output = input + '>{name}' + {version}")


def chain_flow(folder: Path) -> Path:
    """manual -> load_orders -> clean -> report -> send, each passing on a text: what reached it, then its name and
    version. All at version 1, send receives 'load_orders1>clean1>report1'."""
    for name in ORDERS:
        set_version(folder, name, 1)
    return write_flow(folder, {name: versioned(name, first=name == ORDERS[0]) for name in ORDERS},
                      ["manual.start -> load_orders.after", *(f"{a}.output -> {b}.input" for a, b in zip(ORDERS, ORDERS[1:]))])


def branches_flow(folder: Path, with_report: bool = True) -> Path:
    """manual -> load_orders -> clean and -> prices (two branches); both feed report, which notes 'rows|prices'."""
    for name in ("load_orders", "clean", "prices"):
        set_version(folder, name, 1)
    blocks = {"load_orders": versioned("load_orders", first=True), "clean": versioned("clean"), "prices": versioned("prices")}
    edges = ["manual.start -> load_orders.after", "load_orders.output -> clean.input", "load_orders.output -> prices.input"]
    if with_report:
        blocks["report"] = python(noting("report", "rows + '|' + prices"), ("rows", "prices"), ())
        edges += ["clean.output -> report.rows", "prices.output -> report.prices"]
    return write_flow(folder, blocks, edges)


# --- driving the editor, reading what it shows ----------------------------------------------------------------------------

class Step(NamedTuple):
    run: int | None  # the run's number (None: refused before a run was recorded)
    status: str  # how it ended: success, failed, ... (refused: the editor started no run)
    log: list  # the lines the Log gained

    @property
    def said(self) -> str:
        return "\n".join(self.log)


def log_text(win) -> str:
    return win.log.text.toPlainText()


def plain(tip: str) -> str:
    """A tooltip's (or a label's) HTML as text."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", tip or ""))).strip()


def do(win, qtbot, tab, act) -> Step:
    """Do `act` (it starts one run from the editor) and wait for that run to end."""
    count, before = len(tab.runs), log_text(win)
    act()
    started = len(tab.runs) > count
    if started:
        qtbot.waitUntil(lambda: not tab.running and tab.runs[-1].status is not None, timeout=90_000)
    qtbot.wait(300)  # the editor's own follow-up: the history, the canvas
    after = log_text(win)
    gained = [line for line in (after[len(before):] if after.startswith(before) else after).splitlines() if line.strip()]
    return Step(tab.runs[-1].run_id, tab.runs[-1].status, gained) if started else Step(None, "refused", gained)


def run_now(win, qtbot, tab) -> Step:
    """Run now: the whole flow."""
    return do(win, qtbot, tab, lambda: win.run_flow(tab))


def entry(win, tab, block: str, words: str):
    """The entry of `block`'s right-click menu that starts with `words`."""
    found = [a for a in win._block_menu_for(tab, block).actions() if a.text().startswith(words)]
    assert len(found) == 1, [a.text() for a in win._block_menu_for(tab, block).actions()]
    return found[0]


def choose(win, qtbot, tab, block: str, words: str) -> Step:
    """Right-click `block` and choose the entry that starts with `words`."""
    action = entry(win, tab, block, words)
    assert action.isEnabled(), f"{action.text()!r} cannot be chosen"
    return do(win, qtbot, tab, action.trigger)


def inputs_window(monkeypatch, typed: dict | None = None, press_run: bool = True) -> dict:
    """Answer the "inputs I choose" window: what it is filled in with is noted (each field's text, and what the
    window says above them), `typed` ({input: text}) is typed into it, then Run (or Cancel) is pressed."""
    seen = {}

    def exec_(dialog):
        seen["fields"] = {name: field.text() for name, field in dialog.fields.items()}
        seen["says"] = " ".join(plain(label.text()) for label in dialog.findChildren(QLabel))
        for name, text in (typed or {}).items():
            dialog.fields[name].setText(text)
        return press_run

    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", exec_)
    return seen


def rows_of(field: str) -> int:
    """How many rows the table a field of the inputs window is filled in with has (the field reads table('<file>'))."""
    found = re.fullmatch(r"table\((.+)\)", field)
    assert found, f"not filled in with a table: {field!r}"
    return pl.read_parquet(ast.literal_eval(found[1])).height


def received(folder: Path, block: str) -> list:
    """What reached `block`, one entry per time it ran."""
    return lines_of(folder / f"{block}_got.txt")


def lines_of(path: Path) -> list:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def under(tab, block: str) -> str:
    """The text under a block on the canvas: its result."""
    return tab.scene.nodes[block].status_text


def history_rows(win) -> dict:
    """Run history as it is shown: {run number: when it started}."""
    table = win.history_panel.table
    return {int(table.item(row, 0).text()): table.item(row, 1).text() for row in range(table.rowCount())}


def open_from_history(win, run: int) -> None:
    """Double-click a run in Run history."""
    table = win.history_panel.table
    row = next(row for row in range(table.rowCount()) if table.item(row, 0).text() == str(run))
    table.cellDoubleClicked.emit(row, 0)
    QApplication.processEvents()


def full_run(win, qtbot, folder: Path, rows: int | None = 10, mail_down: bool = False):
    """The rows' run #5: the orders flow, opened and run whole; load_orders gives `rows` rows (None: it fails, there is
    no orders.txt). With `mail_down`, send fails and archive is skipped. Gives (the flow's tab, the run)."""
    if rows is not None:
        (folder / "orders.txt").write_text(str(rows))
    if mail_down:
        (folder / "mail_down.txt").write_text("")
    tab = win.open_flow(orders_flow(folder))
    return tab, run_now(win, qtbot, tab)


def load_orders_alone(win, qtbot, tab, folder: Path, rows: int | None = 12) -> Step:
    """The rows' run #6: the query now gives `rows` rows (None: it fails), and load_orders is re-run alone."""
    if rows is None:
        (folder / "orders.txt").unlink(missing_ok=True)
    else:
        (folder / "orders.txt").write_text(str(rows))
    return choose(win, qtbot, tab, "load_orders", RUN_ONLY)


# --- RR-1, RR-2: the block after a block re-run alone gets what that re-run made --------------------------------------------

@pytest.mark.invariant("RR-1")
def test_a_block_that_failed_then_succeeded_when_rerun_alone_is_seen_by_the_block_after_it(win, qtbot, tmp_path):
    tab, full = full_run(win, qtbot, tmp_path, rows=None)
    assert full.status == "failed" and under(tab, "load_orders").startswith("✕"), full.log
    assert received(tmp_path, "clean") == []
    tab.doc.set_block_value("load_orders", "code", LOAD_ORDERS.replace(QUERY, "12"))  # you fix the query
    alone = choose(win, qtbot, tab, "load_orders", RUN_ONLY)
    assert alone.status == "success" and "12 rows" in under(tab, "load_orders"), alone.log

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert "would not run" not in clean.said, clean.log
    assert clean.status == "success", clean.log
    assert received(tmp_path, "clean") == ["12"]


@pytest.mark.invariant("RR-2")
@pytest.mark.parametrize("change", ["its table changed outside the flow", "its query edited"])
def test_the_block_after_a_block_rerun_alone_gets_its_new_output_never_the_old_one(win, qtbot, tmp_path, change):
    tab, full = full_run(win, qtbot, tmp_path, rows=10)
    assert full.status == "success" and received(tmp_path, "clean") == ["10"], full.log
    if change == "its query edited":
        tab.doc.set_block_value("load_orders", "code", LOAD_ORDERS.replace(QUERY, "12"))
        alone = choose(win, qtbot, tab, "load_orders", RUN_ONLY)
    else:
        alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=12)
    assert alone.status == "success" and "12 rows" in under(tab, "load_orders"), alone.log

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert clean.status == "success", clean.log
    assert received(tmp_path, "clean") == ["10", "12"]  # the 12 rows, not the 10 again


# --- RR-3: every re-run carries on from the newest one, on any branch -------------------------------------------------------

@pytest.mark.invariant("RR-3")
def test_two_branches_rerun_one_after_the_other_then_the_block_both_feed_gets_both_new_results(win, qtbot, tmp_path):
    tab = win.open_flow(branches_flow(tmp_path))
    assert run_now(win, qtbot, tab).status == "success"
    assert received(tmp_path, "report") == ["load_orders1>clean1|load_orders1>prices1"]
    set_version(tmp_path, "clean", 2)
    assert choose(win, qtbot, tab, "clean", RUN_ONLY).status == "success"
    set_version(tmp_path, "prices", 2)
    prices = choose(win, qtbot, tab, "prices", RUN_ONLY)
    assert prices.status == "success", prices.log

    report = choose(win, qtbot, tab, "report", RUN_ONLY)

    assert report.status == "success", report.log
    assert received(tmp_path, "report")[1:] == ["load_orders1>clean2|load_orders1>prices2"]


@pytest.mark.invariant("RR-3")
def test_a_chain_of_three_reruns_each_alone_reaches_the_last_block(win, qtbot, tmp_path):
    tab = win.open_flow(chain_flow(tmp_path))
    assert run_now(win, qtbot, tab).status == "success"
    assert received(tmp_path, "send") == ["load_orders1>clean1>report1"]
    for block in ("load_orders", "clean", "report"):  # each changed, then re-run alone, in the flow's order
        set_version(tmp_path, block, 2)
        step = choose(win, qtbot, tab, block, RUN_ONLY)
        assert step.status == "success", (block, step.log)

    send = choose(win, qtbot, tab, "send", RUN_ONLY)

    assert send.status == "success", send.log
    assert received(tmp_path, "clean")[1:] == ["load_orders2"]
    assert received(tmp_path, "report")[1:] == ["load_orders2>clean2"]
    assert received(tmp_path, "send")[1:] == ["load_orders2>clean2>report2"]


@pytest.mark.invariant("RR-3")
def test_the_block_after_one_run_with_inputs_you_chose_gets_what_that_run_made(win, qtbot, tmp_path, monkeypatch):
    tab = win.open_flow(chain_flow(tmp_path))
    assert run_now(win, qtbot, tab).status == "success"
    inputs_window(monkeypatch, typed={"input": "'typed'"})
    chosen = choose(win, qtbot, tab, "clean", CHOOSE_ONLY)
    assert chosen.status == "success" and received(tmp_path, "clean")[1:] == ["typed"], chosen.log

    report = choose(win, qtbot, tab, "report", RUN_ONLY)

    assert report.status == "success", report.log
    assert received(tmp_path, "report")[1:] == ["typed>clean1"]


@pytest.mark.invariant("RR-3")
def test_a_block_added_since_the_run_gets_the_new_results_of_both_branches_rerun_before_it(win, qtbot, tmp_path):
    tab = win.open_flow(branches_flow(tmp_path, with_report=False))
    assert run_now(win, qtbot, tab).status == "success"
    for block in ("clean", "prices"):  # each branch changed, then re-run alone
        set_version(tmp_path, block, 2)
        assert choose(win, qtbot, tab, block, RUN_ONLY).status == "success"
    added = tab.doc.add_block("logic.python", 600, 0, python(noting("summary", "rows + '|' + prices"),
                                                             ("rows", "prices"), ())["config"])
    tab.doc.connect("clean", "output", added, "rows")
    tab.doc.connect("prices", "output", added, "prices")
    QApplication.processEvents()
    assert under(tab, added) == ""  # it shows no result of its own

    step = choose(win, qtbot, tab, added, RUN_ONLY)

    assert step.status == "success", step.log
    assert received(tmp_path, "summary") == ["load_orders1>clean2|load_orders1>prices2"]


@pytest.mark.invariant("RR-3")
@pytest.mark.invariant("RR-4")
def test_with_the_newest_reruns_files_cleaned_up_the_newest_run_that_still_exists_is_named_and_used(win, qtbot, tmp_path):
    tab = win.open_flow(chain_flow(tmp_path))
    full = run_now(win, qtbot, tab)
    set_version(tmp_path, "load_orders", 2)
    first = choose(win, qtbot, tab, "load_orders", RUN_ONLY)
    newest = choose(win, qtbot, tab, "clean", RUN_ONLY)
    assert (full.status, first.status, newest.status) == ("success",) * 3 and newest.run is not None, newest.log
    shutil.rmtree(win.home.runs_dir / str(newest.run))  # as Keep runs cleans a run up: its folder, then its history
    history = History(win.home.history_db)
    history.forget(newest.run)
    history.close()

    named = entry(win, tab, "clean", RUN_ONLY).text()
    again = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert named.startswith(f"{RUN_ONLY} (run #{first.run} · "), named
    assert again.status == "success", again.log
    assert received(tmp_path, "clean")[-1] == "load_orders2"  # what load_orders made in the run that still exists


# --- RR-4: all four menu entries use that same run and name it ---------------------------------------------------------------

@pytest.mark.invariant("RR-4")
def test_the_same_inputs_entries_name_the_newest_run_with_its_date_and_time(win, qtbot, tmp_path):
    tab, full = full_run(win, qtbot, tmp_path, rows=10)
    alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=12)
    assert (full.status, alone.status) == ("success", "success"), alone.log
    started = history_rows(win)[alone.run]  # e.g. 2026-11-11 09:14:02, as Run history shows it

    names = {(block, words): entry(win, tab, block, words).text() for block in ORDERS for words in (RUN_ONLY, RUN_ON)}

    assert names == {(block, words): f"{words} (run #{alone.run} · {started})" for block, words in names}


@pytest.mark.invariant("RR-4")
@pytest.mark.parametrize("words, blocks_after_run", [(CHOOSE_ONLY, False), (CHOOSE_ON, True)])
def test_inputs_i_choose_opens_filled_in_from_the_newest_run_and_runs_with_it(win, qtbot, tmp_path, monkeypatch, words,
                                                                              blocks_after_run):
    tab, full = full_run(win, qtbot, tmp_path, rows=10)
    alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=12)
    assert (full.status, alone.status) == ("success", "success"), alone.log
    window = inputs_window(monkeypatch)  # Run is pressed with the window as it opened

    chosen = choose(win, qtbot, tab, "clean", words)

    assert rows_of(window["fields"]["orders"]) == 12
    assert f"what reached the block in run #{alone.run} · " in window["says"], window["says"]
    assert chosen.status == "success", chosen.log
    assert received(tmp_path, "clean") == ["10", "12"]
    assert received(tmp_path, "report") == (["10", "12"] if blocks_after_run else ["10"])
    assert received(tmp_path, "send") == (["10 orders", "12 orders"] if blocks_after_run else ["10 orders"])


# --- RR-6: a re-run that failed again is reported as it is now -----------------------------------------------------------------

@pytest.mark.invariant("RR-6")
def test_after_a_rerun_that_failed_again_the_block_after_it_is_told_the_block_before_it_failed(win, qtbot, tmp_path):
    tab, full = full_run(win, qtbot, tmp_path, rows=None)
    again = load_orders_alone(win, qtbot, tab, tmp_path, rows=None)
    assert (full.status, again.status) == ("failed", "failed") and again.run is not None, again.log

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert "clean would not run: load_orders, before it, failed" in clean.said, clean.log
    assert clean.status != "success" and received(tmp_path, "clean") == []


@pytest.mark.invariant("RR-6")
@pytest.mark.invariant("RR-2")
def test_a_block_that_succeeded_then_failed_when_rerun_alone_is_reported_failed_to_the_block_after_it(win, qtbot,
                                                                                                    tmp_path):
    tab, full = full_run(win, qtbot, tmp_path, rows=10)
    alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=None)  # its table is gone: it fails now
    assert (full.status, alone.status) == ("success", "failed") and under(tab, "load_orders").startswith("✕"), alone.log

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert "clean would not run: load_orders, before it, failed" in clean.said, clean.log
    assert clean.status != "success" and received(tmp_path, "clean") == ["10"]  # never again with the 10 rows of before


# --- RR-8: an old block in between is never passed over silently -------------------------------------------------------------

@pytest.mark.invariant("RR-8")
@pytest.mark.parametrize("block, got_before", [("report", ["10"]), ("send", ["10 orders"])])
def test_rerunning_a_block_past_an_old_one_is_refused_naming_the_block_to_retry_first(win, qtbot, tmp_path, block,
                                                                                    got_before):
    tab, full = full_run(win, qtbot, tmp_path, rows=10)
    alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=12)
    assert (full.status, alone.status) == ("success", "success"), alone.log

    step = choose(win, qtbot, tab, block, RUN_ONLY)  # without re-running clean (for send: nor report)

    assert OLD_BETWEEN.format("clean") in step.said, step.log
    assert step.status != "success" and received(tmp_path, block) == got_before  # it did not run


@pytest.mark.invariant("RR-8")
def test_run_from_this_block_on_does_the_old_blocks_in_order_with_the_new_data(win, qtbot, tmp_path):
    tab, full = full_run(win, qtbot, tmp_path, rows=10)
    alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=12)
    assert (full.status, alone.status) == ("success", "success"), alone.log
    ran_before = lines_of(tmp_path / "ran.txt")

    on = choose(win, qtbot, tab, "clean", RUN_ON)

    assert on.status == "success", on.log
    assert lines_of(tmp_path / "ran.txt")[len(ran_before):] == ["clean", "report", "send"]
    assert received(tmp_path, "clean") == ["10", "12"] and received(tmp_path, "report") == ["10", "12"]
    assert received(tmp_path, "send") == ["10 orders", "12 orders"]


# --- RR-9: a run opened from Run history is used as it is ------------------------------------------------------------------

@pytest.mark.invariant("RR-9")
def test_a_run_opened_from_run_history_is_shown_named_and_used_as_it_is(win, qtbot, tmp_path):
    tab, full = full_run(win, qtbot, tmp_path, rows=10)
    as_it_was = {block: under(tab, block) for block in ORDERS}
    alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=12)
    assert (full.status, alone.status) == ("success", "success") and "12 rows" in under(tab, "load_orders"), alone.log

    open_from_history(win, full.run)

    assert {block: under(tab, block) for block in ORDERS} == as_it_was  # the canvas shows that run
    for words in (RUN_ONLY, RUN_ON):
        assert entry(win, tab, "clean", words).text().startswith(f"{words} (run #{full.run} · ")
    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)
    assert clean.status == "success", clean.log
    assert received(tmp_path, "clean") == ["10", "10"]  # that run's own 10 rows, not the 12 of the newer run


# --- RR-10: a full run starts afresh ------------------------------------------------------------------------------------------

@pytest.mark.invariant("RR-10")
def test_after_a_full_run_every_block_shows_it_nothing_is_old_and_reruns_take_it(win, qtbot, tmp_path):
    tab, first = full_run(win, qtbot, tmp_path, rows=10)
    alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=12)
    (tmp_path / "orders.txt").write_text("15")

    again = run_now(win, qtbot, tab)

    assert (first.status, alone.status, again.status) == ("success",) * 3, again.log
    for block in ORDERS:
        assert not under(tab, block).startswith("old"), (block, under(tab, block))
        for words in (RUN_ONLY, RUN_ON):
            assert entry(win, tab, block, words).text().startswith(f"{words} (run #{again.run} · ")
    for block in ("clean", "report"):  # each re-run alone takes the full run; nothing is refused
        step = choose(win, qtbot, tab, block, RUN_ONLY)
        assert step.status == "success", (block, step.log)
    assert received(tmp_path, "clean") == ["10", "15", "15"] and received(tmp_path, "report") == ["10", "15", "15"]


@pytest.mark.invariant("RR-10")
@pytest.mark.invariant("RR-2")
def test_after_a_new_full_run_in_between_reruns_carry_on_from_the_newest_one_again(win, qtbot, tmp_path):
    tab, first = full_run(win, qtbot, tmp_path, rows=10)
    load_orders_alone(win, qtbot, tab, tmp_path, rows=12)
    (tmp_path / "orders.txt").write_text("15")
    again = run_now(win, qtbot, tab)
    alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=20)
    assert (first.status, again.status, alone.status) == ("success",) * 3, alone.log

    clean = choose(win, qtbot, tab, "clean", RUN_ONLY)

    assert clean.status == "success", clean.log
    assert received(tmp_path, "clean") == ["10", "15", "20"]  # not the full run's 15, nor the 12 of before it


# --- generated: change a block, re-run a block alone, in any order ---------------------------------------------------------

class Chain:
    """What the rows say of chain_flow's blocks after a full run and any re-runs alone: what each block last gave
    (what it shows), and which are old (made before a block before them ran again)."""

    def __init__(self):
        self.version = {block: 1 for block in ORDERS}
        self.gives, self.old = {}, set()
        for block in ORDERS:  # the full run
            self.ran(block)

    def before(self, block: str) -> str | None:
        return ORDERS[ORDERS.index(block) - 1] if block != ORDERS[0] else None

    def retry_first(self, block: str) -> str | None:
        """The first old block before `block` (None: none is)."""
        return next((b for b in ORDERS[:ORDERS.index(block)] if b in self.old), None)

    def ran(self, block: str) -> None:
        before = self.before(block)
        self.gives[block] = (f"{self.gives[before]}>" if before else "") + f"{block}{self.version[block]}"
        self.old = (self.old - {block}) | set(ORDERS[ORDERS.index(block) + 1:])


STEPS = st.lists(st.tuples(st.sampled_from(["change", "re-run alone"]), st.sampled_from(ORDERS)), min_size=1, max_size=6)


@pytest.mark.invariant("RR-2")
@pytest.mark.invariant("RR-8")
@settings(max_examples=15, deadline=None, database=None, phases=(Phase.explicit, Phase.generate),
          suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(steps=STEPS)
@example(steps=[("change", "load_orders"), ("re-run alone", "load_orders"), ("re-run alone", "clean")])
@example(steps=[("re-run alone", "clean"), ("re-run alone", "send"), ("re-run alone", "report"), ("re-run alone", "send")])
def test_generated_a_block_rerun_alone_gets_what_the_block_before_it_now_shows_or_is_told_which_to_retry_first(
        win, qtbot, tmp_path_factory, steps):
    """After a full run of load_orders -> clean -> report -> send, any 1 to 6 steps, each "change a block" (outside the
    flow: its next output differs) or "re-run a block alone". A block re-run alone either runs with exactly what the
    block before it shows now, or (a block before it is old) is refused naming the first block to retry and does not
    run; it never runs with an output older than the one the block before it shows."""
    folder = tmp_path_factory.mktemp("chain")
    tab = win.open_flow(chain_flow(folder))
    try:
        assert run_now(win, qtbot, tab).status == "success"
        chain = Chain()
        for n, (what, block) in enumerate(steps, 1):
            if what == "change":
                chain.version[block] += 1
                set_version(folder, block, chain.version[block])
                continue
            had, first_old = received(folder, block), chain.retry_first(block)
            step = choose(win, qtbot, tab, block, RUN_ONLY)
            where = f"step {n} of {steps}: {step.log}"
            if first_old is not None:
                assert OLD_BETWEEN.format(first_old) in step.said, where
                assert step.status != "success" and received(folder, block) == had, where  # it did not run
                continue
            assert step.status == "success", where
            if chain.before(block):
                assert received(folder, block) == had + [chain.gives[chain.before(block)]], where
            chain.ran(block)
    finally:
        tab.doc.dirty = False
        win.close_tab(tab)
