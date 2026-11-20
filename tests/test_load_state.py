"""Load run state: right-click a run in Run history and choose "Load run state" to have every block show its result from
that run ("like a savegame"), re-run blocks from it and leave it. The rows LS-1..LS-9 of docs/promises/load-state.yaml
(agreed 2026-11-20); each test names the combination (L1..L17) of the stage's plan it covers.

Through the real editor window (the `win` fixture: offscreen, a temporary Taskloom folder), right-clicks on Run
history's rows and on blocks on the canvas, the real `taskloom run` processes (as a terminal or the scheduler service
starts them) and the engine's public run_flow (to make many runs quickly, or one stopped at once). A test reads what a
user sees: the text under each block and what hovering it says, the bar on the flow's tab, the menus' items, the Log,
Run history, the questions asked; for LS-6 also the stored run files and history rows. Each block of the flows notes
what reached it (<block>_got.txt). Git is the test's own (git_config), and nothing reaches the internet or the
credential store.

The flows and ways of driving the editor are those of tests/test_rerun_carries_on.py (the orders flow: manual ->
load_orders -> clean -> report -> send -> archive; load_orders gives as many rows as orders.txt says, none: it fails).
"""
# What Load run state must be called and show (chosen by these tests: the implementer's contract):
# - Run history, right-click a run: the item "Load run state". A run that cannot be loaded: the item is disabled and its
#   text (after "Load run state") or its tooltip says why: with "files" in it when the run's files are gone (partly or
#   all, or it kept none), with "still going" for a run that has not ended.
# - The bar: a widget inside the flow's tab, objectName "loaded-state-bar", shown while a state is loaded (and when a
#   loaded run turned out cleaned up); its words are in QLabels: "run #N" and the time Run history shows for that run;
#   a button "Leave". After a re-run from the state it names that re-run. The run's files cleaned up: it says
#   "cleaned up". LS-8: it says "today's flow" or "as it was", as was chosen.
# - A value not kept, on its block (the text under it or hovering that text): the port's name, then
#   "value not kept: <reason>", the reason with "password" for a password, "cannot be saved" for e.g. an open
#   connection.
# - A block carried over from a run that is cleaned up: on its block "gone" and that run ("run #N").
# - A refused re-run says why in the Log (no message box), as refused re-runs do today.
# - LS-8: a modal window that says "earlier version of the flow", with the buttons "Show on today's flow", "Bring back
#   the flow as it was" and "Cancel"; the second disabled when that version is not in the flow's Git history, its
#   tooltip then saying so ("Git history"). Before a re-run saves a brought-back flow: one question (a message box or a
#   modal window) with a "No" answer, which saves nothing and runs nothing.
import contextlib
import copy
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog, QLabel, QMessageBox, QWidget  # noqa: E402

from taskloom.flow import real_path  # noqa: E402
from tests.test_api_session import MANUAL as SESSION_START  # noqa: E402
from tests.test_api_session import LoginSite, api, chain, session  # noqa: E402
from tests.test_copy_executed_query import right_click_run, row_of  # noqa: E402
from tests.test_history_list_scheduled import double_click, rows, run_cli  # noqa: E402
from tests.test_loop_container import per_item  # noqa: E402
from tests.test_paths_editor_editing import flow_file, menu  # noqa: E402
from tests.test_paths_editor_running import eventually  # noqa: E402
from tests.test_rerun_carries_on import (RUN_ON, RUN_ONLY, chain_flow, full_run, history_rows, noting,  # noqa: E402
                                         orders_flow, plain, python, received, run_now, set_version, under,
                                         write_flow)
from tests.test_rerun_old_and_kept import lines_flow  # noqa: E402
from tests.test_rerun_refusal_seen import point_of, right_click_and_choose  # noqa: E402
import tests.test_save_and_commit as save_and_commit  # noqa: E402
from tests.test_save_and_commit import commit, needs_git, new_repo  # noqa: E402

git_config = save_and_commit.git_config  # its autouse fixture, bound (not imported) so that pyflakes passes

LOAD = "Load run state"
BAR = "loaded-state-bar"
LEAVE = "Leave"
NOT_KEPT = "value not kept: "
EARLIER = "earlier version of the flow"
TODAY = "Show on today's flow"
AS_IT_WAS = "Bring back the flow as it was"
CANCEL = "Cancel"
STANDARD = {"Yes": QMessageBox.Yes, "No": QMessageBox.No, "Cancel": QMessageBox.Cancel, "OK": QMessageBox.Ok}
CLEAN = noting("clean", "len(orders)") + "result = orders"  # the orders flow's clean
CLEAN_BUG = noting("clean", "len(orders)") + "raise ValueError('clean has a bug')"


# --- the flows -----------------------------------------------------------------------------------------------------

def edited(path: Path, change) -> Path:
    """The flow file at `path` after change(its data) (a change made as by hand, before the editor opens it)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def keep_last(path: Path, n: int) -> Path:
    """The flow's Manual Start keeps its last `n` runs (Keep runs): a run that ends cleans up the ones before."""
    def keep(data):
        data["blocks"]["manual"]["config"] = {**(data["blocks"]["manual"].get("config") or {}), "keep": f"last {n}"}
    return edited(path, keep)


def scheduled(path: Path) -> Path:
    """The flow started by the Schedule block `nightly` (once a year) instead of its Manual Start."""
    def swap(data):
        del data["blocks"]["manual"]
        data["blocks"] = {"nightly": {"type": "trigger.schedule", "config": {"cron": "0 2 1 1 *"}}, **data["blocks"]}
        data["edges"] = [e.replace("manual.start", "nightly.start") for e in data["edges"]]
    return edited(path, swap)


def flow_of(data: dict) -> tuple:
    """What a flow is, apart from where its blocks are placed: each block's type and settings, its connections."""
    return {b: (s["type"], s.get("config")) for b, s in data["blocks"].items()}, sorted(data["edges"])


def engine_runs(win, path: Path, count: int, stopped_at_once: bool = False) -> list[int]:
    """`count` runs of the flow through the engine's public run_flow (in this process; the caller is in the flow's
    folder); stopped_at_once: each is stopped before any block ends. Their numbers."""
    from taskloom import engine
    from taskloom.config import Home
    from taskloom.flow import load_flow
    from taskloom.registry import discover

    flow, registry, home = load_flow(path), discover(None), Home(win.home.root)
    made = []
    for _ in range(count):
        stop = threading.Event()
        if stopped_at_once:
            stop.set()
        made.append(engine.run_flow(flow, registry, home, cancel=stop).run_id)
    return made


# --- what the editor shows -------------------------------------------------------------------------------------------

def canvas(tab) -> dict:
    """The text under each block on the canvas."""
    return {block: under(tab, block) for block in tab.scene.nodes}


def on_block(tab, block: str) -> str:
    """What a block says of its result: the text under it, then what hovering that text says."""
    return under(tab, block) + "\n" + plain(tab.scene.nodes[block].footer.toolTip())


def bars(tab) -> list:
    return [w for w in tab.findChildren(QWidget, BAR) if w.isVisibleTo(tab)]


def bar(tab) -> str | None:
    """What the loaded-state bar on the flow's tab says (None: the tab shows no such bar)."""
    shown = bars(tab)
    assert len(shown) <= 1, f"{len(shown)} loaded-state bars on the tab"
    if not shown:
        return None
    words = [shown[0].text()] if isinstance(shown[0], QLabel) else []
    words += [label.text() for label in shown[0].findChildren(QLabel) if label.isVisibleTo(shown[0])]
    return plain(" ".join(words))


def leave_button(tab) -> QAbstractButton:
    (shown,) = bars(tab)
    found = [b for b in shown.findChildren(QAbstractButton)
             if b.text().replace("&", "") == LEAVE and b.isVisibleTo(shown)]
    assert len(found) == 1, f"the bar has no {LEAVE} button: {[b.text() for b in shown.findChildren(QAbstractButton)]}"
    return found[0]


def leave(tab) -> None:
    """Click the bar's Leave button."""
    QTest.mouseClick(leave_button(tab), Qt.LeftButton)
    QApplication.processEvents()


def names(text: str | None, run: int) -> bool:
    """`text` names run `run` ("run #12": not run #120)."""
    return bool(text) and re.search(rf"\brun #{run}(?!\d)", text, re.I) is not None


def started(win, run: int) -> str:
    """When the run started, as Run history shows it."""
    return history_rows(win)[run]


def run_ids(win) -> set[int]:
    """Every run in the history."""
    with contextlib.closing(sqlite3.connect(win.home.history_db)) as db:
        return {run for (run,) in db.execute("SELECT id FROM runs")}


# --- questions, menus, re-runs ---------------------------------------------------------------------------------------

@contextlib.contextmanager
def answering(answer: str | None = None, monkeypatch=None):
    """Meanwhile, every question the editor asks in a window of its own that waits for the answer (modal) is answered by
    clicking its button whose text starts with `answer` (None, or that button disabled: the window is closed
    unanswered), and with monkeypatch also every message box (QMessageBox.question, warning, information: answered
    with its button of that name). Yields the questions asked, each {"text": what it says, "buttons": [{"text",
    "enabled", "tip"}]} (a message box: no buttons)."""
    asked, handled = [], []

    def box(parent, title, text, *args, **kw):
        asked.append({"text": plain(f"{title}: {text}"), "buttons": []})
        return STANDARD.get(answer, QMessageBox.Cancel)

    if monkeypatch is not None:
        for name in ("question", "warning", "information"):
            monkeypatch.setattr(QMessageBox, name, staticmethod(box))

    def look():
        window = QApplication.activeModalWidget()
        if window is None or any(window is seen for seen in handled):
            return
        handled.append(window)
        buttons = [b for b in window.findChildren(QAbstractButton) if b.isVisibleTo(window) and b.text()]
        words = [window.windowTitle(), *(label.text() for label in window.findChildren(QLabel)
                                         if label.isVisibleTo(window))]
        asked.append({"text": plain(" ".join(words)),
                      "buttons": [{"text": b.text().replace("&", ""), "enabled": b.isEnabled(),
                                   "tip": plain(b.toolTip())} for b in buttons]})
        chosen = next((b for b in buttons if answer and b.text().replace("&", "").startswith(answer)
                       and b.isEnabled()), None)
        if chosen is not None:
            QTest.mouseClick(chosen, Qt.LeftButton)
        elif isinstance(window, QDialog):
            window.reject()
        else:
            window.close()

    timer = QTimer(interval=20)
    timer.timeout.connect(look)
    timer.start()
    try:
        yield asked
    finally:
        timer.stop()


def the_item(menus: list) -> dict:
    """Run history's Load run state item, in the menus a right-click on a run opened."""
    assert menus, "right-clicking the run opened no menu"
    found = [item for item in menus[0] if item["text"].startswith(LOAD)]
    assert len(found) == 1, f"no {LOAD!r} item in Run history's menu: {[item['text'] for item in menus[0]]}"
    return found[0]


def load_item(win, qtbot, run: int) -> dict:
    """Right-click run `run` in Run history: its Load run state item ({"text", "enabled", "tip"}); nothing chosen."""
    with answering() as asked:
        menus = right_click_run(win, qtbot, run)
    assert asked == [], f"a right-click asked: {asked}"
    return the_item(menus)


def load_state(win, qtbot, run: int, answer: str | None = None, monkeypatch=None) -> list:
    """Right-click run `run` in Run history and choose Load run state; a question it asks is answered `answer` (None:
    it must ask none). The questions it asked."""
    with answering(answer, monkeypatch) as asked:
        menus = right_click_run(win, qtbot, run, (LOAD,))
    item = the_item(menus)
    assert item["enabled"], f"{item['text']!r} cannot be chosen ({item['tip']})"
    QApplication.processEvents()
    if answer is None:
        assert asked == [], f"loading run #{run} asked: {asked}"
    return asked


def rerun(win, qtbot, tab, block: str, words: str):
    """Right-click `block` on the canvas and choose the entry that starts with `words`; wait for the run it starts. It
    must ask nothing."""
    with answering() as asked:
        step = right_click_and_choose(win, qtbot, tab, block, words)
    assert asked == [], f"re-running {block} asked: {asked}"
    return step


def block_menu(win, qtbot, tab, block: str) -> list[tuple[str, bool]]:
    """Right-click `block` on the canvas: its menu's entries (text, enabled); nothing chosen."""
    viewport, at = tab.view.viewport(), point_of(tab, block)
    entries = []

    def in_the_menu():
        popup = QApplication.activePopupWidget()
        if popup is None or entries:
            return
        timer.stop()
        entries.extend((a.text(), a.isEnabled()) for a in popup.actions() if a.text())
        popup.close()

    timer = QTimer(interval=20)
    timer.timeout.connect(in_the_menu)
    QTest.mousePress(viewport, Qt.RightButton, Qt.NoModifier, at)
    QTest.mouseRelease(viewport, Qt.RightButton, Qt.NoModifier, at)
    timer.start()
    try:
        QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, viewport.mapToGlobal(at)))
    finally:
        timer.stop()
    assert entries, f"right-clicking {block} opened no menu"
    return entries


def start_again(win, qtbot):
    """Taskloom started again on the same Taskloom folder, as `taskloom editor` does: its window, its flows reopened."""
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    again = MainWindow(Home(win.home.root))
    qtbot.addWidget(again)
    again.show()
    again.restore_session()
    QApplication.processEvents()
    return again


# --- LS-1: load a run: every block shows its result from it; the bar ------------------------------------------------

@pytest.mark.invariant("LS-1")
def test_l1_a_loaded_failed_run_shows_the_error_on_the_failed_block_nothing_after_it_and_a_bar_naming_it(
        win, qtbot, tmp_path):
    """L1: run #1 failed (load_orders), run #2 succeeded. Load run #1: the canvas shows run #1 as it showed when it
    ended: load_orders' error; the blocks after it no result of their own ("skipped · something before it failed", as
    run #1 left them); a bar on the tab names run #1 and its time and has Leave."""
    tab, first = full_run(win, qtbot, tmp_path, rows=None)
    assert first.status == "failed" and under(tab, "load_orders").startswith("✕"), first.log
    as_it_ended = canvas(tab)
    (tmp_path / "orders.txt").write_text("10")
    second = run_now(win, qtbot, tab)
    assert second.status == "success" and "10 rows" in under(tab, "load_orders"), second.log

    load_state(win, qtbot, first.run)

    assert canvas(tab) == as_it_ended
    assert under(tab, "load_orders").startswith("✕") and "orders.txt" in on_block(tab, "load_orders")
    assert {block: under(tab, block) for block in ("clean", "report", "send", "archive")} == dict.fromkeys(
        ("clean", "report", "send", "archive"), "skipped · something before it failed")
    said = bar(tab)
    assert said is not None, "no loaded-state bar on the tab"
    assert names(said, first.run) and started(win, first.run) in said, said
    assert leave_button(tab).isEnabled()


@pytest.mark.invariant("LS-1")
def test_l17_double_clicking_a_run_still_shows_it_once_and_loads_no_state(win, qtbot, tmp_path):
    """L17 (RR-9 unchanged; passes before Load run state exists): double-click run #1 in Run history: the canvas shows
    it, no loaded-state bar; one re-run from it takes run #1's inputs, and after it the canvas follows the newest runs
    again (load_orders shows run #2's 12 rows), still no bar."""
    tab, first = full_run(win, qtbot, tmp_path, rows=10)
    as_it_ended = canvas(tab)
    (tmp_path / "orders.txt").write_text("12")
    second = run_now(win, qtbot, tab)
    assert (first.status, second.status) == ("success", "success"), second.log

    double_click(win, first.run)

    assert canvas(tab) == as_it_ended and bar(tab) is None
    clean = rerun(win, qtbot, tab, "clean", RUN_ONLY)
    assert clean.status == "success" and received(tmp_path, "clean") == ["10", "12", "10"], clean.log
    assert "12 rows" in under(tab, "load_orders") and bar(tab) is None


# --- LS-2: re-runs take their inputs from the loaded state and become it -------------------------------------------

@pytest.mark.invariant("LS-2")
def test_l2_a_fixed_block_of_a_loaded_failed_run_run_from_on_takes_its_inputs_and_becomes_the_loaded_state(
        win, qtbot, tmp_path):
    """L2: clean has a bug: runs #1 (10 orders) and #2 (12 orders) fail at clean. Load run #1, fix clean's code, Run
    from this block on: clean and the blocks after it get run #1's 10 rows, not run #2's 12; the re-run becomes the
    loaded state: the bar names it."""
    (tmp_path / "orders.txt").write_text("10")
    path = edited(orders_flow(tmp_path), lambda d: d["blocks"]["clean"]["config"].update(code=CLEAN_BUG))
    tab = win.open_flow(path)
    first = run_now(win, qtbot, tab)
    (tmp_path / "orders.txt").write_text("12")
    second = run_now(win, qtbot, tab)
    assert (first.status, second.status) == ("failed", "failed") and under(tab, "clean").startswith("✕"), second.log
    assert received(tmp_path, "clean") == ["10", "12"]
    load_state(win, qtbot, first.run)
    tab.doc.set_block_value("clean", "code", CLEAN)  # you fix it

    on = rerun(win, qtbot, tab, "clean", RUN_ON)

    assert on.status == "success" and on.run is not None, on.log
    assert received(tmp_path, "clean") == ["10", "12", "10"]
    assert received(tmp_path, "report") == ["10"] and received(tmp_path, "send") == ["10 orders"]
    assert names(bar(tab), on.run), bar(tab)
    assert "10 rows" in under(tab, "clean") and "10 rows" in under(tab, "load_orders")


@pytest.mark.invariant("LS-2")
def test_l3_two_reruns_in_a_row_from_a_loaded_state_the_second_takes_its_inputs_from_the_first(win, qtbot, tmp_path):
    """L3: the chain load_orders -> clean -> report -> send; run #1 at version 1, run #2 with load_orders at version 2.
    Load run #1; clean (now version 2) re-run alone gets run #1's load_orders1; the bar names that re-run and report and
    send, which it did not reach, show their old results greyed as old. Then report re-run alone gets what that first
    re-run made (load_orders1>clean2), and the bar names the second re-run."""
    tab = win.open_flow(chain_flow(tmp_path))
    first = run_now(win, qtbot, tab)
    set_version(tmp_path, "load_orders", 2)
    second = run_now(win, qtbot, tab)
    assert (first.status, second.status) == ("success", "success"), second.log
    assert received(tmp_path, "clean") == ["load_orders1", "load_orders2"]
    load_state(win, qtbot, first.run)
    set_version(tmp_path, "clean", 2)

    clean = rerun(win, qtbot, tab, "clean", RUN_ONLY)

    assert clean.status == "success" and received(tmp_path, "clean")[-1] == "load_orders1", clean.log
    assert names(bar(tab), clean.run), bar(tab)
    assert under(tab, "report").startswith("old · ") and under(tab, "send").startswith("old · "), canvas(tab)

    report = rerun(win, qtbot, tab, "report", RUN_ONLY)

    assert report.status == "success" and received(tmp_path, "report")[-1] == "load_orders1>clean2", report.log
    assert names(bar(tab), report.run), bar(tab)


@pytest.mark.invariant("LS-1")
@pytest.mark.invariant("LS-2")
def test_l4_loading_a_second_state_replaces_the_first_in_the_bar_on_the_canvas_and_for_reruns(win, qtbot, tmp_path):
    """L4: runs #1 (10 orders), #2 (12) and #3 (15). Load run #1, then run #2: the bar names run #2 (not run #1), the
    canvas shows run #2, clean's "same inputs" entry names run #2 and clean re-run alone gets its 12 rows."""
    tab, first = full_run(win, qtbot, tmp_path, rows=10)
    (tmp_path / "orders.txt").write_text("12")
    second = run_now(win, qtbot, tab)
    as_second_ended = canvas(tab)
    (tmp_path / "orders.txt").write_text("15")
    third = run_now(win, qtbot, tab)
    assert (first.status, second.status, third.status) == ("success",) * 3, third.log
    load_state(win, qtbot, first.run)

    load_state(win, qtbot, second.run)

    said = bar(tab)
    assert names(said, second.run) and not names(said, first.run), said
    assert canvas(tab) == as_second_ended
    same = [text for text, _ in block_menu(win, qtbot, tab, "clean") if text.startswith(RUN_ONLY)]
    assert same == [f"{RUN_ONLY} (run #{second.run} · {started(win, second.run)})"], same
    clean = rerun(win, qtbot, tab, "clean", RUN_ONLY)
    assert clean.status == "success" and received(tmp_path, "clean")[-1] == "12", clean.log


# --- LS-3: a full run or Leave ends the loaded state -----------------------------------------------------------------

@pytest.mark.invariant("LS-3")
def test_l13_leave_and_a_full_run_each_end_the_loaded_state_and_the_newest_results_show(win, qtbot, tmp_path):
    """L13: runs #1 (10 orders) and #2 (12). Load run #1, Leave: no bar, the canvas shows run #2 again. Load run #1
    again, then Run now (15 orders): no bar, the canvas shows that run, nothing old."""
    tab, first = full_run(win, qtbot, tmp_path, rows=10)
    (tmp_path / "orders.txt").write_text("12")
    second = run_now(win, qtbot, tab)
    newest = canvas(tab)
    assert (first.status, second.status) == ("success", "success"), second.log
    load_state(win, qtbot, first.run)
    assert "10 rows" in under(tab, "load_orders") and bar(tab) is not None

    leave(tab)

    assert bar(tab) is None and canvas(tab) == newest
    load_state(win, qtbot, first.run)
    assert "10 rows" in under(tab, "load_orders") and bar(tab) is not None
    (tmp_path / "orders.txt").write_text("15")

    third = run_now(win, qtbot, tab)

    assert third.status == "success", third.log
    assert bar(tab) is None
    assert "15 rows" in under(tab, "load_orders") and "15 rows" in under(tab, "clean")
    assert not [block for block, text in canvas(tab).items() if text.startswith("old")], canvas(tab)


# --- LS-4: values not kept -------------------------------------------------------------------------------------------

def not_kept_flow(folder: Path) -> Path:
    """manual -> ask (Ask User, a password: from the stored secret pw when nobody can answer) -> use_pw; manual ->
    connect (an open database connection) -> query; manual -> make (5) -> show."""
    return write_flow(folder, {
        "ask": {"type": "logic.ask_user", "config": {"prompt": "Password?", "kind": "password", "secret": "pw"}},
        "use_pw": python(noting("use_pw", "'got it'"), ("pw",), ()),
        "connect": python("import sqlite3\nconn = sqlite3.connect(':memory:')", (), ("conn",)),
        "query": python(noting("query", "conn.execute('select 1').fetchone()[0]"), ("conn",), ()),
        "make": python("n = 5", (), ("n",)),
        "show": python(noting("show", "n"), ("n",), ()),
    }, ["manual.start -> ask.after", "manual.start -> connect.after", "manual.start -> make.after",
        "ask.answer -> use_pw.pw", "connect.conn -> query.conn", "make.n -> show.n"])


@pytest.mark.invariant("LS-4")
def test_l9_a_password_and_an_open_connection_are_named_not_kept_and_reruns_needing_them_are_refused(
        win, qtbot, tmp_path):
    """L9: a run from a terminal: ask gave a password (the stored secret), connect an open connection, make 5. Load
    it: ask and connect each name their value as not kept, with the reason. use_pw and query, re-run alone, are
    refused with that reason (not the other one) before anything runs (no new run, they do not run); show, re-run
    alone, runs."""
    win.home.root.mkdir(parents=True, exist_ok=True)
    secrets = win.home.root / "secrets.yaml"
    secrets.write_text("pw: hunter2SECRET\n", encoding="utf-8")
    secrets.chmod(0o600)
    path = not_kept_flow(tmp_path)
    first = run_cli(win, path)
    assert received(tmp_path, "use_pw") == ["got it"] and received(tmp_path, "query") == ["1"]
    tab = win.open_flow(path)

    load_state(win, qtbot, first)

    assert re.search(rf"\banswer\b.*{NOT_KEPT}.*password", on_block(tab, "ask"), re.S), on_block(tab, "ask")
    assert re.search(rf"\bconn\b.*{NOT_KEPT}.*cannot be saved", on_block(tab, "connect"), re.S), \
        on_block(tab, "connect")
    before = run_ids(win)
    use = rerun(win, qtbot, tab, "use_pw", RUN_ONLY)
    query = rerun(win, qtbot, tab, "query", RUN_ONLY)
    assert "password" in use.said and use.status != "success", use.log
    assert "cannot be saved" in query.said and query.status != "success", query.log
    assert "cannot be saved" not in use.said and "password" not in query.said, (use.log, query.log)
    assert run_ids(win) == before
    assert received(tmp_path, "use_pw") == ["got it"] and received(tmp_path, "query") == ["1"]

    show = rerun(win, qtbot, tab, "show", RUN_ONLY)

    assert show.status == "success" and received(tmp_path, "show") == ["5", "5"], show.log


# --- LS-5: the loaded state belongs to its tab ---------------------------------------------------------------------

@pytest.mark.invariant("LS-5")
def test_l5_runs_finishing_while_a_state_is_loaded_do_not_change_the_tab_and_are_listed_in_run_history(
        win, qtbot, tmp_path):
    """L5: a flow started by a Schedule block: run #1 as the scheduler service starts it (10 orders), run #2 from a
    terminal (12). Load run #1. Then (15 orders) a scheduled run and a terminal run of it finish, and another flow
    runs in another tab: the tab still shows run #1 and its bar; Run history lists the two new runs. (The same flow
    cannot be open in two tabs: opening it again shows its tab.)"""
    flows, other = tmp_path / "nightly", tmp_path / "other"
    flows.mkdir()
    other.mkdir()
    (flows / "orders.txt").write_text("10")
    path = scheduled(orders_flow(flows))
    first = run_cli(win, path, "--trigger", "nightly")
    (flows / "orders.txt").write_text("12")
    run_cli(win, path)
    tab = win.open_flow(path)
    load_state(win, qtbot, first)
    shown, said = canvas(tab), bar(tab)
    assert "10 rows" in shown["load_orders"] and names(said, first), (shown, said)

    (flows / "orders.txt").write_text("15")
    by_schedule = run_cli(win, path, "--trigger", "nightly")
    from_terminal = run_cli(win, path)
    other_tab, elsewhere = full_run(win, qtbot, other, rows=7)
    assert elsewhere.status == "success", elsewhere.log
    win.tabs.setCurrentWidget(tab)

    assert eventually(qtbot, lambda: {str(by_schedule), str(from_terminal)} <= {r[0] for r in rows(win)},
                      timeout=10_000), rows(win)
    qtbot.wait(2500)  # the editor looks at the history every 2 s: once more after it listed them
    assert canvas(tab) == shown and bar(tab) == said


@pytest.mark.invariant("LS-5")
def test_l16_loading_a_state_in_one_tab_leaves_another_tab_as_it_was(win, qtbot, tmp_path):
    """L16: flow A (runs #1, #2) and flow B (run #3) open in two tabs. Load A's run #1: B's canvas is as it was and
    B shows no bar."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    tab_a, first = full_run(win, qtbot, a, rows=10)
    (a / "orders.txt").write_text("12")
    second = run_now(win, qtbot, tab_a)
    tab_b, other = full_run(win, qtbot, b, rows=7)
    assert (first.status, second.status, other.status) == ("success",) * 3, other.log
    shown_b = canvas(tab_b)
    win.tabs.setCurrentWidget(tab_a)

    load_state(win, qtbot, first.run)

    assert names(bar(tab_a), first.run) and "10 rows" in under(tab_a, "load_orders")
    win.tabs.setCurrentWidget(tab_b)
    QApplication.processEvents()
    assert canvas(tab_b) == shown_b and bar(tab_b) is None


# --- LS-6: loading never changes a stored run ------------------------------------------------------------------------

def stored_files(win, runs) -> dict:
    """Every file in these runs' folders, with a checksum of its bytes."""
    found = {}
    for run in runs:
        for path in sorted((win.home.runs_dir / str(run)).rglob("*")):
            if path.is_file():
                found[path.relative_to(win.home.runs_dir).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def history_of(win, run: int) -> dict:
    """The run's rows in the history: the run, its blocks, its Log, what it carried on from."""
    with contextlib.closing(sqlite3.connect(win.home.history_db)) as db:
        return {table: sorted(map(repr, db.execute(f"SELECT * FROM {table} WHERE {key}=?", (run,)).fetchall()))
                for table, key in (("runs", "id"), ("blocks", "run_id"), ("logs", "run_id"), ("carried_on", "run_id"))}


@pytest.mark.invariant("LS-6")
def test_l12_nothing_is_written_into_a_stored_run_by_load_leave_a_refused_rerun_a_rerun_or_closing(
        win, qtbot, tmp_path):
    """L12: run #1 failed (load_orders), run #2 succeeded. The files of both runs and run #1's history rows stay the
    same through: loading run #1, Leave, loading it again, a refused re-run (clean: load_orders failed), a re-run
    that runs (load_orders, 12 orders: a new run), closing Taskloom."""
    tab, first = full_run(win, qtbot, tmp_path, rows=None)
    (tmp_path / "orders.txt").write_text("10")
    second = run_now(win, qtbot, tab)
    assert (first.status, second.status) == ("failed", "success"), second.log
    stored = lambda: (stored_files(win, [first.run, second.run]), history_of(win, first.run))  # noqa: E731
    before = stored()
    assert before[0] and before[1]["runs"] and before[1]["blocks"]

    load_state(win, qtbot, first.run)
    assert stored() == before, "after loading it"
    leave(tab)
    assert stored() == before, "after Leave"
    load_state(win, qtbot, first.run)
    refused = rerun(win, qtbot, tab, "clean", RUN_ONLY)
    assert "would not run" in refused.said and received(tmp_path, "clean") == ["10"], refused.log
    assert stored() == before, "after a refused re-run"
    (tmp_path / "orders.txt").write_text("12")
    again = rerun(win, qtbot, tab, "load_orders", RUN_ONLY)
    assert again.status == "success" and again.run not in (first.run, second.run), again.log
    assert stored() == before, "after a re-run"
    win.close()
    QApplication.processEvents()
    assert stored() == before, "after closing Taskloom"


# --- LS-7: runs that cannot be loaded; a loaded run cleaned up -------------------------------------------------------

@pytest.mark.invariant("LS-7")
def test_l6_the_loaded_runs_files_cleaned_up_by_keep_runs_the_bar_says_so_and_a_rerun_is_refused(win, qtbot, tmp_path):
    """L6: Keep runs: the last 2. Runs #1 (10 orders) and #2 (12); load run #1. A run from a terminal ends and Keep runs
    cleans run #1 up: the bar says so, the blocks still show what they showed, and clean re-run alone is refused
    saying so (no new run; clean does not run)."""
    (tmp_path / "orders.txt").write_text("10")
    tab = win.open_flow(keep_last(orders_flow(tmp_path), 2))
    first = run_now(win, qtbot, tab)
    (tmp_path / "orders.txt").write_text("12")
    second = run_now(win, qtbot, tab)
    assert (first.status, second.status) == ("success", "success"), second.log
    load_state(win, qtbot, first.run)
    shown = canvas(tab)
    assert "10 rows" in shown["load_orders"]

    run_cli(win, tab.doc.path)
    assert not (win.home.runs_dir / str(first.run)).exists(), "Keep runs did not clean run #1 up"

    assert eventually(qtbot, lambda: "cleaned up" in (bar(tab) or ""), timeout=10_000), bar(tab)
    assert canvas(tab) == shown
    before, had = run_ids(win), received(tmp_path, "clean")
    clean = rerun(win, qtbot, tab, "clean", RUN_ONLY)
    assert "cleaned up" in clean.said and clean.status != "success", clean.log
    assert run_ids(win) == before and received(tmp_path, "clean") == had
    assert canvas(tab) == shown


@pytest.mark.invariant("LS-7")
def test_l7_a_run_whose_files_are_gone_or_that_kept_none_cannot_be_loaded_and_the_item_says_why(
        win, qtbot, tmp_path, monkeypatch):
    """L7: four runs of the orders flow: one with a block's folder deleted (partly cleaned up), one whose folder was
    deleted by hand, one stopped at once (it kept no files), one whole. Right-clicking each in Run history: Load run
    state is disabled and says its files are gone for the first three; it can be chosen for the whole one."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "orders.txt").write_text("10")
    path = orders_flow(tmp_path)
    partly, by_hand, whole = engine_runs(win, path, 3)
    (none_kept,) = engine_runs(win, path, 1, stopped_at_once=True)
    shutil.rmtree(win.home.runs_dir / str(partly) / "clean")
    shutil.rmtree(win.home.runs_dir / str(by_hand))
    assert not (win.home.runs_dir / str(none_kept)).exists()
    win.open_flow(path)

    for run in (partly, by_hand, none_kept):
        item = load_item(win, qtbot, run)
        assert not item["enabled"] and "files" in f"{item['text']} {item['tip']}", (run, item)
    assert load_item(win, qtbot, whole)["enabled"]


@pytest.mark.invariant("LS-7")
def test_l7_a_run_still_going_cannot_be_loaded_until_it_ends(win, qtbot, tmp_path):
    """L7: a run from a terminal waits 8 s. While it goes, Run history's Load run state item for it is disabled and
    says it is still going; once it has ended, the item can be chosen."""
    path = write_flow(tmp_path, {"pause": {"type": "logic.wait", "config": {"seconds": 8}}},
                      ["manual.start -> pause.after"])
    win.open_flow(path)
    proc = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "run", str(path)],
                            env={**os.environ, "TASKLOOM_HOME": str(win.home.root)}, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        qtbot.waitUntil(lambda: win.home.history_db.exists() and bool(run_ids(win)), timeout=30_000)
        (run,) = run_ids(win)
        item = load_item(win, qtbot, run)
        assert proc.poll() is None, "the run ended before it was looked at"
        assert not item["enabled"] and "still going" in f"{item['text']} {item['tip']}", item
    finally:
        assert proc.wait(timeout=60) == 0

    assert eventually(qtbot, lambda: (str(run), "manual", "success") in rows(win), timeout=10_000), rows(win)
    assert load_item(win, qtbot, run)["enabled"]


# --- LS-1, LS-2 in Loops, Mergers, Sessions --------------------------------------------------------------------------

LOOP_WORK = ("import os\nif index in (2, 4) and not os.path.exists(f'fixed_{index}.txt'):\n"
             "    raise ValueError(f'no data for {item}')\noutput = item.upper()")


@pytest.mark.invariant("LS-1")
@pytest.mark.parametrize("kind", ["loop", "merger"])
def test_l8_a_loaded_run_shows_the_results_of_a_loop_and_its_items_and_of_a_merger_set_to_each_line(
        win, qtbot, tmp_path, kind):
    """L8: a Loop over 5 items (items 2 and 4 fail), or three lines into a Merger set to each line as it finishes
    (report fails for costs and stock): run #1 fails so; fixed, run #2 succeeds. Load run #1: every block shows what
    it showed when run #1 ended (the Loop and the block inside it over its items; the blocks per line)."""
    if kind == "loop":
        path, fixes = per_item(tmp_path, ["a", "b", "c", "d", "e"], LOOP_WORK), ("fixed_2.txt", "fixed_4.txt")
    else:
        path, fixes = lines_flow(tmp_path), ("fixed_costs.txt", "fixed_stock.txt")
    tab = win.open_flow(path)
    first = run_now(win, qtbot, tab)
    as_it_ended = canvas(tab)
    for fix in fixes:
        (tmp_path / fix).write_text("")
    second = run_now(win, qtbot, tab)
    assert (first.status, second.status) == ("failed", "success"), second.log
    assert canvas(tab) != as_it_ended

    load_state(win, qtbot, first.run)

    assert canvas(tab) == as_it_ended


@pytest.mark.invariant("LS-1")
@pytest.mark.invariant("LS-2")
def test_l8_a_request_in_a_session_rerun_from_a_loaded_run_sends_that_runs_cookie(win, qtbot, tmp_path):
    """L8: login -> data -> more inside a Session (a web server on this computer); each login gives a new session
    cookie. Runs #1 and #2. Load run #1: data, re-run alone, sends run #1's cookie, not run #2's."""
    with LoginSite() as site:
        tab = win.open_flow(flow_file(tmp_path / "flow.yaml", {
            "manual": SESSION_START, "sess": session(0, 0),
            **{name: api(site.url(f"/{name}"), k) for k, name in enumerate(("login", "data", "more"))},
        }, ["manual.start -> login.after", *chain("login", "data", "more")]))
        first = run_now(win, qtbot, tab)
        second = run_now(win, qtbot, tab)
        assert (first.status, second.status) == ("success", "success"), second.log
        assert len(site.issued) == 2
        load_state(win, qtbot, first.run)

        data = rerun(win, qtbot, tab, "data", RUN_ONLY)

    assert data.status == "success", data.log
    assert site.seen[-1] == ("/data", {"sessionid": site.issued[0]})
    assert len(site.issued) == 2  # nothing logged in again


# --- LS-1, LS-7: a loaded re-run shows what it carried over ----------------------------------------------------------

@pytest.mark.invariant("LS-1")
def test_l10_a_loaded_rerun_shows_the_results_it_carried_over_from_the_run_before(win, qtbot, tmp_path):
    """L10: run #1 (10 orders); clean re-run alone (run #2: load_orders carried over from run #1); run #3 with 15
    orders. Load run #2: load_orders shows its 10 rows (carried over), clean the 10 rows it made; the bar names run
    #2."""
    tab, first = full_run(win, qtbot, tmp_path, rows=10)
    again = rerun(win, qtbot, tab, "clean", RUN_ONLY)
    (tmp_path / "orders.txt").write_text("15")
    third = run_now(win, qtbot, tab)
    assert (first.status, again.status, third.status) == ("success",) * 3, third.log
    assert "15 rows" in under(tab, "load_orders")

    load_state(win, qtbot, again.run)

    assert names(bar(tab), again.run), bar(tab)
    assert re.search(r"✓.*10 rows", under(tab, "load_orders")), under(tab, "load_orders")
    assert re.search(r"✓.*10 rows", under(tab, "clean")), under(tab, "clean")


@pytest.mark.invariant("LS-1")
@pytest.mark.invariant("LS-7")
def test_l10_a_loaded_rerun_shows_carried_blocks_inside_a_loop_and_says_when_their_run_is_cleaned_up(
        win, qtbot, tmp_path):
    """L10: a Loop (work inside it, over 3 items) then report; Keep runs: the last 3. Run #1; report re-run alone (run
    #2: the Loop and work carried over from run #1, whose own folder holds nothing of work's); run #3, item b failing.
    Load run #2: the Loop and work show what they showed when run #1 ended, not run #3's. A run from a terminal then
    makes Keep runs clean run #1 up: loaded again, run #2's work says its result is gone, naming run #1."""
    work = ("import os\nif item == 'b' and os.path.exists('fail_b.txt'):\n    raise ValueError('no b')\n"
            "output = item.upper()")
    path = keep_last(per_item(tmp_path, ["a", "b", "c"], work), 3)
    tab = win.open_flow(path)
    first = run_now(win, qtbot, tab)
    as_first_ended = canvas(tab)
    again = rerun(win, qtbot, tab, "report", RUN_ONLY)
    (tmp_path / "fail_b.txt").write_text("")
    third = run_now(win, qtbot, tab)
    (tmp_path / "fail_b.txt").unlink()
    assert (first.status, again.status, third.status) == ("success", "success", "failed"), third.log
    assert as_first_ended["work"].startswith("✓") and under(tab, "work") != as_first_ended["work"], canvas(tab)

    load_state(win, qtbot, again.run)

    assert names(bar(tab), again.run), bar(tab)
    assert (under(tab, "each"), under(tab, "work")) == (as_first_ended["each"], as_first_ended["work"])
    run_cli(win, path)
    assert not (win.home.runs_dir / str(first.run)).exists(), "Keep runs did not clean run #1 up"
    leave(tab)
    load_state(win, qtbot, again.run)
    assert "gone" in on_block(tab, "work") and names(on_block(tab, "work"), first.run), on_block(tab, "work")


@pytest.mark.invariant("LS-1")
def test_l11_the_oldest_of_the_200_runs_run_history_lists_loads(win, qtbot, tmp_path, monkeypatch):
    """L11: 201 runs of a flow (the engine's run); the first two fail (n.txt missing), the others succeed. Run history
    lists the newest 200: run #2 is the oldest listed. Load it: load shows its error; the bar names it."""
    monkeypatch.chdir(tmp_path)
    path = write_flow(tmp_path, {"load": python("output = open('n.txt').read()", (), ("output",))},
                      ["manual.start -> load.after"])
    made = engine_runs(win, path, 2)
    (tmp_path / "n.txt").write_text("later")
    made += engine_runs(win, path, 199)
    tab = win.open_flow(path)
    assert eventually(qtbot, lambda: len(history_rows(win)) == 200, timeout=10_000), len(history_rows(win))
    assert row_of(win, made[0]) is None and row_of(win, made[1]) is not None

    load_state(win, qtbot, made[1])

    assert under(tab, "load").startswith("✕") and "n.txt" in on_block(tab, "load"), on_block(tab, "load")
    assert names(bar(tab), made[1]), bar(tab)


# --- LS-8: the flow changed since the run ----------------------------------------------------------------------------

def changed_since_run(win, folder: Path, in_git: bool = True):
    """The orders flow at version 1 (committed in the folder's own Git repository, if in_git), run from a terminal with
    10 orders (run #1); then changed as by hand: archive deleted, extra added after report (committed too, if in_git);
    run again with 12 orders (run #2). The flow opened. Gives (its tab, its file, version 1's bytes, version 2's
    bytes, run #1)."""
    if in_git:
        new_repo(folder)
    else:
        folder.mkdir()
    (folder / "orders.txt").write_text("10")
    path = orders_flow(folder)
    v1 = path.read_bytes()
    if in_git:
        commit(folder, "Version 1", path.name)
    first = run_cli(win, path)

    def change(data):
        del data["blocks"]["archive"]
        data["blocks"]["extra"] = python(noting("extra", "text"), ("text",), ())
        data["edges"] = [e for e in data["edges"] if "archive" not in e] + ["report.text -> extra.text"]
    edited(path, change)
    v2 = path.read_bytes()
    if in_git:
        commit(folder, "Version 2", path.name)
    (folder / "orders.txt").write_text("12")
    run_cli(win, path)
    assert received(folder, "extra") == ["12 orders"]
    tab = win.open_flow(path)
    assert not tab.doc.dirty and path.read_bytes() == v2
    return tab, path, v1, v2, first


def the_question(asked: list) -> dict:
    """The one question loading asked: that the run was made with an earlier version of the flow, and its three
    answers."""
    assert len(asked) == 1, f"asked {len(asked)} questions: {asked}"
    (question,) = asked
    assert EARLIER in question["text"], question
    assert [b for b in (TODAY, AS_IT_WAS, CANCEL)
            if not any(button["text"].startswith(b) for button in question["buttons"])] == [], question
    return question


@needs_git
@pytest.mark.invariant("LS-8")
def test_l14_cancel_on_the_question_changes_nothing(win, qtbot, tmp_path, monkeypatch):
    """L14: the flow changed since run #1. Loading run #1 asks first; Cancel: nothing changes (the canvas, the flow in
    the tab and in its file, no bar)."""
    tab, path, v1, v2, first = changed_since_run(win, tmp_path / "flows")
    shown, held = canvas(tab), copy.deepcopy(tab.doc.data)

    the_question(load_state(win, qtbot, first, CANCEL, monkeypatch))

    assert canvas(tab) == shown and tab.doc.data == held and not tab.doc.dirty
    assert bar(tab) is None and path.read_bytes() == v2


@needs_git
@pytest.mark.invariant("LS-8")
def test_l14_show_on_todays_flow_leaves_out_deleted_blocks_and_a_block_added_since_shows_nothing(
        win, qtbot, tmp_path, monkeypatch):
    """L14: the flow changed since run #1; "Show on today's flow": the blocks of both versions show run #1's results,
    extra (added since) shows nothing, archive (deleted since) is not brought back; the flow and its file stay as they
    are; the bar names run #1 and says it is shown on today's flow."""
    tab, path, v1, v2, first = changed_since_run(win, tmp_path / "flows")

    the_question(load_state(win, qtbot, first, TODAY, monkeypatch))

    assert "10 rows" in under(tab, "load_orders") and "10 rows" in under(tab, "clean"), canvas(tab)
    assert under(tab, "extra") == ""
    assert set(tab.scene.nodes) == set(yaml.safe_load(v2)["blocks"])
    assert flow_of(tab.doc.data) == flow_of(yaml.safe_load(v2)) and not tab.doc.dirty and path.read_bytes() == v2
    said = bar(tab)
    assert names(said, first) and "today's flow" in said, said


@needs_git
@pytest.mark.invariant("LS-8")
def test_l14_bring_back_puts_the_old_flow_in_the_tab_as_unsaved_edits_and_ctrl_z_brings_back_what_it_held(
        win, qtbot, tmp_path, monkeypatch):
    """L14: the flow changed since run #1, and the tab has an unsaved edit. "Bring back the flow as it was": the tab
    holds version 1 (archive back, extra gone) as unsaved edits, every block showing run #1's result; the file is
    not written; the bar says the flow is as it was. Ctrl Z (Edit → Undo): the tab holds what it held, the unsaved
    edit too."""
    tab, path, v1, v2, first = changed_since_run(win, tmp_path / "flows")
    tab.doc.move_blocks({"clean": (77, 77)})  # an unsaved edit
    held = copy.deepcopy(tab.doc.data)

    question = the_question(load_state(win, qtbot, first, AS_IT_WAS, monkeypatch))

    assert next(b for b in question["buttons"] if b["text"].startswith(AS_IT_WAS))["enabled"]
    assert flow_of(tab.doc.data) == flow_of(yaml.safe_load(v1)) and tab.doc.dirty
    assert set(tab.scene.nodes) == set(yaml.safe_load(v1)["blocks"])
    assert "10 rows" in under(tab, "load_orders") and under(tab, "archive").startswith("✓"), canvas(tab)
    assert path.read_bytes() == v2
    said = bar(tab)
    assert names(said, first) and "as it was" in said, said

    menu(win, "Undo").trigger()  # Ctrl+Z

    assert tab.doc.data == held and tab.doc.dirty and path.read_bytes() == v2


@needs_git
@pytest.mark.invariant("LS-8")
def test_l14_after_bring_back_ctrl_z_the_bar_says_todays_flow_and_ctrl_y_as_it_was_again(
        win, qtbot, tmp_path, monkeypatch):
    """L14: the flow brought back as it was at run #1. Ctrl Z (Edit → Undo): the tab holds today's flow, its blocks
    showing run #1's results, and the bar keeps naming run #1 and says today's flow, not as it was. Ctrl Y (Edit →
    Redo): the flow as it was again, and the bar says so."""
    tab, path, v1, v2, first = changed_since_run(win, tmp_path / "flows")
    the_question(load_state(win, qtbot, first, AS_IT_WAS, monkeypatch))
    assert "as it was" in bar(tab), bar(tab)

    menu(win, "Undo").trigger()  # Ctrl+Z

    assert flow_of(tab.doc.data) == flow_of(yaml.safe_load(v2))
    assert "10 rows" in under(tab, "load_orders") and under(tab, "extra") == "", canvas(tab)
    said = bar(tab)
    assert names(said, first) and "today's flow" in said and "as it was" not in said, said

    menu(win, "Redo").trigger()  # Ctrl+Y

    assert flow_of(tab.doc.data) == flow_of(yaml.safe_load(v1))
    said = bar(tab)
    assert names(said, first) and "as it was" in said and "today's flow" not in said, said


@needs_git
@pytest.mark.invariant("LS-8")
def test_l14_a_rerun_of_a_brought_back_flow_asks_before_saving_it_over_the_file_and_no_saves_and_runs_nothing(
        win, qtbot, tmp_path, monkeypatch):
    """L14: the flow brought back as it was at run #1; clean, Run from this block on: Taskloom asks once before saving
    it over the file; No: the file is not written, no run is made, clean does not run, the tab still holds version 1
    unsaved."""
    folder = tmp_path / "flows"
    tab, path, v1, v2, first = changed_since_run(win, folder)
    the_question(load_state(win, qtbot, first, AS_IT_WAS, monkeypatch))
    before, had = run_ids(win), received(folder, "clean")

    with answering("No", monkeypatch) as asked:
        right_click_and_choose(win, qtbot, tab, "clean", RUN_ON)

    assert len(asked) == 1, asked
    assert path.read_bytes() == v2
    assert run_ids(win) == before and received(folder, "clean") == had
    assert flow_of(tab.doc.data) == flow_of(yaml.safe_load(v1)) and tab.doc.dirty


@pytest.mark.invariant("LS-8")
def test_l14_a_version_not_in_the_flows_git_history_cannot_be_brought_back_and_the_question_says_why(
        win, qtbot, tmp_path, monkeypatch):
    """L14: the flow, in no Git repository, changed since run #1: the question's "Bring back the flow as it was" is
    disabled, its tooltip saying the version is not in the flow's Git history."""
    tab, path, v1, v2, first = changed_since_run(win, tmp_path / "flows", in_git=False)

    question = the_question(load_state(win, qtbot, first, CANCEL, monkeypatch))

    (back,) = [b for b in question["buttons"] if b["text"].startswith(AS_IT_WAS)]
    assert not back["enabled"] and "Git history" in back["tip"], back


@pytest.mark.invariant("LS-8")
def test_l14_a_run_of_the_flow_as_it_is_loads_without_a_question(win, qtbot, tmp_path, monkeypatch):
    """L14: runs #1 and #2 of the flow as it is now: loading run #1 asks nothing and loads it."""
    tab, first = full_run(win, qtbot, tmp_path, rows=10)
    (tmp_path / "orders.txt").write_text("12")
    second = run_now(win, qtbot, tab)
    assert (first.status, second.status) == ("success", "success"), second.log

    asked = load_state(win, qtbot, first.run, CANCEL, monkeypatch)

    assert asked == []
    assert names(bar(tab), first.run) and "10 rows" in under(tab, "load_orders")


# --- LS-9: a loaded state comes back after Taskloom is closed --------------------------------------------------------

@pytest.mark.invariant("LS-9")
def test_l15_states_loaded_in_two_tabs_come_back_after_closing_taskloom_a_cleaned_up_one_says_so(win, qtbot, tmp_path):
    """L15: flow A (Keep runs: the last 2) has its run #1 loaded (10 orders; #2 had 12); flow B has a re-run loaded:
    its run #1 (10 orders; #2 had 12) loaded, then clean re-run alone. Taskloom closed; a run of A from a terminal
    (15 orders) then cleans A's run #1 up. Taskloom started again: B's tab has the re-run loaded (the bar names it,
    load_orders and clean show 10 rows); A's bar says its run was cleaned up and A shows the newest results (15)."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "orders.txt").write_text("10")
    tab_a = win.open_flow(keep_last(orders_flow(a), 2))
    a_first = run_now(win, qtbot, tab_a)
    (a / "orders.txt").write_text("12")
    a_second = run_now(win, qtbot, tab_a)
    load_state(win, qtbot, a_first.run)
    tab_b, b_first = full_run(win, qtbot, b, rows=10)
    (b / "orders.txt").write_text("12")
    b_second = run_now(win, qtbot, tab_b)
    load_state(win, qtbot, b_first.run)
    b_again = rerun(win, qtbot, tab_b, "clean", RUN_ONLY)
    assert {s.status for s in (a_first, a_second, b_first, b_second, b_again)} == {"success"}, b_again.log
    assert names(bar(tab_a), a_first.run) and names(bar(tab_b), b_again.run), (bar(tab_a), bar(tab_b))
    a_path, b_path = tab_a.doc.path, tab_b.doc.path

    win.close()
    QApplication.processEvents()
    (a / "orders.txt").write_text("15")
    run_cli(win, a_path)
    assert not (win.home.runs_dir / str(a_first.run)).exists(), "Keep runs did not clean A's run #1 up"
    again = start_again(win, qtbot)

    tabs = {t.doc.path: t for t in again.tabs_list()}
    assert set(tabs) == {real_path(a_path), real_path(b_path)}
    tab_a, tab_b = tabs[real_path(a_path)], tabs[real_path(b_path)]
    assert eventually(qtbot, lambda: names(bar(tab_b), b_again.run), timeout=10_000), bar(tab_b)
    assert "10 rows" in under(tab_b, "load_orders") and "10 rows" in under(tab_b, "clean"), canvas(tab_b)
    assert eventually(qtbot, lambda: "cleaned up" in (bar(tab_a) or ""), timeout=10_000), bar(tab_a)
    assert eventually(qtbot, lambda: "15 rows" in under(tab_a, "load_orders"), timeout=10_000), canvas(tab_a)


