"""Running flows from the editor, through its real windows and real runner processes: Run now (what it
says first, the Manual Start it offers, saving and checking first), the start blocks' menus, re-runs and
choosing inputs, Stop, the Log, Data preview, Run history, Results of this block, Ask User during a run,
the flows' tabs, the flows running list, and the runs from elsewhere (the scheduler, a terminal) that the
canvas follows."""

import os
import re
import subprocess
import sys
import textwrap
import time

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication, QDialogButtonBox, QFileDialog, QLabel,  # noqa: E402
                               QMessageBox)

from taskloom.config import Home  # noqa: E402
from taskloom.editor import dialogs  # noqa: E402
from taskloom.editor.canvas import HEADER_H  # noqa: E402
from taskloom.editor.dialogs import AskDialog, OverlapDialog, RunDialog  # noqa: E402
from taskloom.editor.panels import CodeEdit, status_icon  # noqa: E402
from taskloom.editor.window import MainWindow  # noqa: E402
from taskloom.history import History  # noqa: E402
from tests.test_editor import wait_done, write_flow  # noqa: E402
from tests.test_lines import (WHEN, click_row, in_block_results, menu_action, run_elsewhere, shown,  # noqa: E402
                              start_from, tab_icon_is, tooltip_lines, wait_runs)


# --- helpers ---------------------------------------------------------------------------

MANUAL = {"type": "trigger.manual"}
NIGHTLY = {"type": "trigger.schedule", "config": {"cron": "0 2 * * *"}}
REAL_CONFIRM_RUN_NOW = dialogs.confirm_run_now  # (the win fixture answers Run now's question; some tests ask it)


def printing(text: str) -> dict:
    return {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": f"print({text!r})"}}


def gated(gate: str, then: str = "pass") -> dict:
    """A Python Code block that waits until the file `gate` exists (next to the flow), then runs `then`."""
    code = f"import os, time\nwhile not os.path.exists({gate!r}):\n    time.sleep(0.05)\n{then}\n"
    return {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": code}}


def write_blocks(path, blocks: dict, edges: list):
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": edges}, sort_keys=False))
    return path


def runs_of(win, flow) -> list[dict]:
    history = History(win.home.history_db)
    try:
        return history.runs(str(flow.resolve()))
    finally:
        history.close()


def start_elsewhere(win, flow, *args) -> subprocess.Popen:
    """A run started outside the editor and left going (the scheduler runs `taskloom run FLOW --trigger BLOCK`)."""
    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root)}
    return subprocess.Popen([sys.executable, "-m", "taskloom.cli", "run", str(flow), *map(str, args)], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_elsewhere(proc: subprocess.Popen):
    if proc.poll() is None:
        proc.kill()
    proc.wait(30)


def running_run(win, flow) -> int | None:
    """The run of the flow file that the history says is going, if any."""
    if not win.home.history_db.exists():
        return None
    history = History(win.home.history_db)
    try:
        return next((r["id"] for r in history.runs(str(flow.resolve())) if r["status"] == "running"), None)
    finally:
        history.close()


def block_running(win, run_id, block) -> bool:
    history = History(win.home.history_db)
    try:
        return (history.blocks(run_id).get(block) or {}).get("status") == "running"
    finally:
        history.close()


def wait_without_the_editor(check, timeout=60):
    """Wait for check() without giving the editor a turn (no Qt events): it cannot look at the history meanwhile."""
    deadline = time.monotonic() + timeout
    while not (value := check()):
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)
    return value


def eventually(qtbot, check, timeout=6_000) -> bool:
    """Let the editor go on (it looks at the history every 2 s) until check() holds or time is up."""
    try:
        qtbot.waitUntil(check, timeout=timeout)
    except qtbot.TimeoutError:
        pass
    return bool(check())


def log_text(win) -> str:
    return win.log.text.toPlainText()


def visible_ask(qtbot, win) -> AskDialog:
    """The Ask User question on screen now."""
    qtbot.waitUntil(lambda: any(d.isVisible() for d in win.findChildren(AskDialog)), timeout=60_000)
    return next(d for d in win.findChildren(AskDialog) if d.isVisible())


def click(dialog, text: str):
    (button,) = [b for b in dialog.findChild(QDialogButtonBox).buttons() if b.text() == text]
    QTest.mouseClick(button, Qt.LeftButton)


def history_rows(win) -> list[list[str]]:
    table = win.history_panel.table
    return [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]


def in_bold(win, text: str) -> bool:
    """`text` is in the Log, written in bold."""
    found = win.log.text.document().find(text)
    return not found.isNull() and found.charFormat().font().bold()


def answer_run_now(win, button: str, tick: bool, questions: list):
    """When Run now's question shows: note what it says, tick "Don't ask me again" if `tick`, press `button`.
    (It keeps looking for the question for about 2 s; none showing is left to the test to notice.)"""
    tries = []

    def act():
        boxes = [w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()]
        if not boxes:
            if len(tries) < 100:
                tries.append(1)
                QTimer.singleShot(20, win, act)
            return
        (box,) = boxes
        questions.append(box.text())
        if tick:
            QTest.mouseClick(box.checkBox(), Qt.LeftButton)
        (pressed,) = [b for b in box.buttons() if b.text().replace("&", "") == button]
        QTest.mouseClick(pressed, Qt.LeftButton)

    QTimer.singleShot(0, win, act)


def tab_icon_shows(win, tab, status, clock: bool) -> bool:
    """The flow's tab shows the icon of `status`, with the scheduled clock beside it or not."""
    size = win.tabs.iconSize()
    shown_icon = win.tabs.tabIcon(win.tabs.indexOf(tab)).pixmap(size).toImage()
    return shown_icon == status_icon(status, clock=clock).pixmap(size).toImage()


def right_click(qtbot, win, tab, block, where, choose: str) -> list[str]:
    """Right-click the block on the canvas at `where` ("title", "footer" — the message under it —, "countdown" —
    a Schedule block's next-run line —, or a port as (name, is_output)); note the items of the menu that opens and
    click the one starting with `choose` (greyed out: close the menu)."""
    QApplication.processEvents()  # an opened flow centres itself just after: not under the mouse mid-click
    node = tab.scene.nodes[block]
    if where == "title":
        local = QPointF(node.footer.rect().center().x(), HEADER_H / 2)
    elif where == "footer":
        local = node.footer.rect().center()
    elif where == "countdown":
        local = node.countdown_area.rect().center()
    else:
        local = node.ports[where].pos()
    # Scrolled into view, as you would: the canvas can be narrower than the flow (on the Windows runner the
    # offscreen platform has no fonts, so the panels' text is wide and leaves the canvas about 110 px).
    tab.view.centerOn(node.mapToScene(local))
    at = tab.view.mapFromScene(node.mapToScene(local))
    assert tab.view.viewport().rect().contains(at), (where, at)  # on screen
    seen, tries, done = [], [], []

    def act():  # (the menu waits for you inside the right-click)
        menu = QApplication.activePopupWidget()
        if menu is None and len(tries) < 250:  # up to 5 s: a busy machine can take a while to open it
            tries.append(1)
            QTimer.singleShot(20, act)
            return
        if menu is not None:
            seen.extend(a.text() for a in menu.actions())
            item = next((a for a in menu.actions() if a.text().startswith(choose) and a.isEnabled()), None)
            if item is not None:
                QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(item).center())
            if menu.isVisible():
                menu.close()
        done.append(1)

    for _ in range(2):  # (once more if no menu opened: a click can be lost while the window is still settling)
        tries.clear()
        done.clear()
        QTimer.singleShot(0, act)
        # Through the window, as the mouse does (it is the window that turns a right-click into the menu).
        QTest.mouseClick(win.windowHandle(), Qt.RightButton, Qt.NoModifier, tab.view.viewport().mapTo(win, at))
        qtbot.waitUntil(lambda: bool(done), timeout=10_000)
        if seen:
            break
    return seen


# --- runs from elsewhere while you run the flow ------------------------------------------

def test_a_run_of_another_line_that_ends_while_yours_is_going_is_shown(win, qtbot, tmp_path):
    flow = write_blocks(tmp_path / "gates.yaml", {
        "manual": MANUAL, "yours": gated("yours_go.txt"),
        "nightly": NIGHTLY, "nightly_job": gated("nightly_go.txt", "print('the nightly job says hello')"),
    }, ["manual.start -> yours.after", "nightly.start -> nightly_job.after"])
    tab = win.open_flow(flow)
    scheduled = start_elsewhere(win, flow, "--trigger", "nightly")  # as the scheduler starts it
    try:
        scheduled_id = wait_without_the_editor(lambda: running_run(win, flow))
        win.run_flow(tab)  # Run now, before the editor's next look at the history (every 2 s)
        qtbot.waitUntil(lambda: tab.scene.nodes["yours"].status == "running", timeout=60_000)
        (tmp_path / "nightly_go.txt").write_text("")
        assert scheduled.wait(60) == 0  # the nightly run ends while yours is going
        qtbot.wait(2_100)  # (the editor looks meanwhile)
        (tmp_path / "yours_go.txt").write_text("")
        yours = wait_runs(qtbot, tab, 1)
    finally:
        stop_elsewhere(scheduled)
        (tmp_path / "yours_go.txt").write_text("")

    expected = {"manual": ("success", yours), "yours": ("success", yours),
                "nightly": ("success", scheduled_id), "nightly_job": ("success", scheduled_id)}
    assert eventually(qtbot, lambda: shown(tab) == expected), shown(tab)
    assert "the nightly job says hello" in log_text(win), log_text(win)


def test_a_run_of_another_line_that_started_and_ended_just_before_run_now_is_shown(win, qtbot, tmp_path):
    flow = write_blocks(tmp_path / "between.yaml", {
        "manual": MANUAL, "yours": printing("your job says hello"),
        "nightly": NIGHTLY, "nightly_job": printing("the nightly job says hello"),
    }, ["manual.start -> yours.after", "nightly.start -> nightly_job.after"])
    tab = win.open_flow(flow)
    # As the scheduler runs it: it starts and ends between two of the editor's looks at the history (every 2 s;
    # no Qt events meanwhile), and you press Run now before the next look.
    scheduled = run_elsewhere(win, flow, "--trigger", "nightly")

    win.run_flow(tab)
    yours = wait_runs(qtbot, tab, 1)

    expected = {"manual": ("success", yours), "yours": ("success", yours),
                "nightly": ("success", scheduled), "nightly_job": ("success", scheduled)}
    assert eventually(qtbot, lambda: shown(tab) == expected), shown(tab)
    text = log_text(win)
    assert "the nightly job says hello" in text and "your job says hello" in text, text


def test_a_flow_opened_while_it_runs_elsewhere_shows_that_run_once_it_ends(win, qtbot, tmp_path):
    flow = write_blocks(tmp_path / "going.yaml", {"nightly": NIGHTLY,
                                                  "job": gated("go.txt", "print('job finished its work')")},
                        ["nightly.start -> job.after"])
    win.close_tab(win.open_flow(flow))  # you saw it before
    proc = start_elsewhere(win, flow, "--trigger", "nightly")
    try:
        run_id = wait_without_the_editor(lambda: running_run(win, flow))
        tab = win.open_flow(flow)  # opened while that run is going
        (tmp_path / "go.txt").write_text("")
        assert proc.wait(60) == 0
    finally:
        stop_elsewhere(proc)

    assert eventually(qtbot, lambda: shown(tab) == {"nightly": ("success", run_id), "job": ("success", run_id)}), \
        shown(tab)
    assert "job finished its work" in log_text(win)


def test_a_scheduled_run_that_failed_while_the_flow_was_closed_shows_when_you_open_it(win, tmp_path):
    flow = write_flow(tmp_path / "failing.yaml", """
        blocks:
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          job:
            type: logic.python
            config: {inputs: [], outputs: [], code: "raise ValueError('the disk is full')"}
        edges:
          - nightly.start -> job.after
    """)
    win.close_tab(win.open_flow(flow))  # you saw it: nothing ran yet
    failed = run_elsewhere(win, flow, "--trigger", "nightly")

    tab = win.open_flow(flow)

    assert shown(tab, ["job"]) == {"job": ("failed", failed)}
    assert "the disk is full" in tab.scene.nodes["job"].status_text
    text = log_text(win)
    assert "job (Python Code) failed" in text and "the disk is full" in text, text
    assert tab_icon_is(win, tab, "failed")
    assert any(re.match(rf"nightly: failed {WHEN}", line) for line in tooltip_lines(win, tab)), tooltip_lines(win, tab)


def test_a_run_from_a_terminal_whose_process_is_killed_is_shown_failed_and_no_longer_running(win, qtbot, tmp_path):
    flow = write_blocks(tmp_path / "killed.yaml", {"manual": MANUAL, "work": gated("never.txt")},
                        ["manual.start -> work.after"])
    tab = win.open_flow(flow)
    terminal = start_elsewhere(win, flow)
    try:
        run_id = wait_without_the_editor(lambda: running_run(win, flow))
        wait_without_the_editor(lambda: block_running(win, run_id, "work"))
        assert eventually(qtbot, lambda: win.running_flows.text() == "1 flow running"), win.running_flows.text()
        terminal.kill()  # e.g. the computer restarted, or someone ended it
        terminal.wait(30)
    finally:
        stop_elsewhere(terminal)

    assert eventually(qtbot, lambda: win.running_flows.text() == "No flows running"), win.running_flows.text()
    assert eventually(qtbot, lambda: shown(tab, ["work"]) == {"work": ("failed", run_id)}), shown(tab)
    assert "stopped unexpectedly" in tab.scene.nodes["work"].status_text
    assert tab_icon_is(win, tab, "failed")


# --- Run now -----------------------------------------------------------------------------

def test_run_now_adds_a_manual_start_to_a_flow_without_one_and_runs_every_block(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "no_start.yaml", """
        blocks:
          start:
            type: logic.python
            config: {inputs: [], code: "output = 20"}
          add:
            type: logic.python
            config: {inputs: [input], outputs: [], code: "open('sum.txt', 'w').write(str(input + 1))"}
          other:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('other.txt', 'w').write('ran')"}
        edges:
          - start.output -> add.input
    """)
    offered = []
    monkeypatch.setattr(dialogs, "offer_manual_start", lambda parent, name: offered.append(name) or True)  # yes
    tab = win.open_flow(flow)

    win.run_action.trigger()  # Run now (F5)
    wait_done(qtbot, tab)

    assert len(offered) == 1
    saved = yaml.safe_load(flow.read_text())
    (added,) = [b for b, spec in saved["blocks"].items() if spec["type"] == "trigger.manual"]
    assert added != "start" and saved["blocks"]["start"]["type"] == "logic.python"  # your block keeps its name
    assert sorted(e for e in saved["edges"] if e.startswith(f"{added}.")) == [f"{added}.start -> other.after",
                                                                               f"{added}.start -> start.after"]
    assert (tmp_path / "sum.txt").read_text() == "21" and (tmp_path / "other.txt").read_text() == "ran"
    assert {b: n.status for b, n in tab.scene.nodes.items()} == {added: "success", "start": "success",
                                                                 "add": "success", "other": "success"}


def test_run_now_on_a_flow_with_a_problem_says_what_and_runs_nothing(win, tmp_path):
    flow = write_flow(tmp_path / "broken.yaml", """
        blocks:
          manual:
            type: trigger.manual
          a:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
          b:
            type: logic.python
            config: {inputs: [x], outputs: [], code: "open('ran.txt', 'w').write('x')"}
        edges:
          - manual.start -> a.after
          - a.output -> b.nope
    """)
    tab = win.open_flow(flow)

    win.run_action.trigger()

    problem = "'b' has no input 'nope'"
    assert [(kind, title) for kind, (title, _) in win.message_boxes] == [("warning", "The flow has problems")]
    assert problem in win.message_boxes[0][1][1]
    win.message_boxes.clear()
    assert problem in log_text(win) and not tab.runs and not (tmp_path / "ran.txt").exists()
    assert not win.home.history_db.exists() or not runs_of(win, flow)


def test_run_now_saves_what_you_are_typing_and_runs_that(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "typing.yaml", """
        blocks:
          manual:
            type: trigger.manual
          write:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('out.txt', 'w').write('before')"}
        edges:
          - manual.start -> write.after
    """)
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.open_flow(flow)
    tab.scene.nodes["write"].setSelected(True)
    editor = win.properties.findChild(CodeEdit)
    editor.setFocus()
    qtbot.waitUntil(editor.hasFocus)
    editor.setPlainText("open('out.txt', 'w').write('after')")  # still in the code box

    win.run_action.trigger()
    wait_done(qtbot, tab)

    assert (tmp_path / "out.txt").read_text() == "after"
    assert "write('after')" in flow.read_text() and not tab.doc.dirty


def test_run_now_on_a_new_flow_asks_where_to_save_it_and_cancelling_that_runs_nothing(win, qtbot, tmp_path,
                                                                                     monkeypatch):
    tab = win.new_flow()  # File > New flow: it comes with a Manual Start
    work = tab.doc.add_block("logic.python", 0, 0, {"inputs": [], "outputs": [],
                                                    "code": "open('ran.txt', 'w').write('ran')"})
    tab.doc.connect("start", "start", work, "after")
    asked = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: asked.append(a) or ("", "")))

    win.run_action.trigger()  # Run now; where to save it? Cancel

    assert len(asked) == 1 and not tab.runs and tab.doc.path is None and tab.doc.dirty
    assert not (tmp_path / "ran.txt").exists() and not win.home.history_db.exists()

    flow = tmp_path / "first flow.yaml"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: asked.append(a) or (str(flow), "")))
    win.run_action.trigger()  # Run now; saved there
    run_id = wait_runs(qtbot, tab, 1)

    assert len(asked) == 2 and tab.doc.path == flow.resolve() and not tab.doc.dirty
    assert yaml.safe_load(flow.read_text())["edges"] == [f"start.start -> {work}.after"]
    assert (tmp_path / "ran.txt").read_text() == "ran"  # it ran next to the file it was saved as
    assert shown(tab) == {"start": ("success", run_id), work: ("success", run_id)}
    assert [r[0] for r in history_rows(win)] == [str(run_id)] and tab_icon_is(win, tab, "success")


def test_dont_ask_me_again_turns_off_run_nows_question_only_when_you_run(win, qtbot, tmp_path, monkeypatch):
    flow = write_blocks(tmp_path / "asked.yaml", {"manual": MANUAL, "count": {
        "type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "open('count.txt', 'a').write('x')"}}},
        ["manual.start -> count.after"])
    monkeypatch.setattr(dialogs, "confirm_run_now", REAL_CONFIRM_RUN_NOW)  # the real question
    questions = []
    tab = win.open_flow(flow)

    answer_run_now(win, "Cancel", tick=True, questions=questions)
    win.run_action.trigger()  # ticked, but Cancel: nothing runs

    assert len(questions) == 1 and "Manual Start block manual" in questions[0], questions
    assert not tab.runs and not (tmp_path / "count.txt").exists()

    answer_run_now(win, "Run", tick=True, questions=questions)
    win.run_action.trigger()  # still asked; ticked and Run
    assert len(questions) == 2
    wait_runs(qtbot, tab, 1)

    answer_run_now(win, "Cancel", tick=False, questions=questions)  # (were it asked again)
    win.run_action.trigger()
    assert len(questions) == 2  # not asked again
    wait_runs(qtbot, tab, 2)

    assert (tmp_path / "count.txt").read_text() == "xx" and [r.status for r in tab.runs] == ["success"] * 2


def test_run_now_with_all_of_them_runs_every_manual_start_and_a_start_blocks_menu_only_its_own(
        win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "two_manual.yaml", """
        blocks:
          manual:
            type: trigger.manual
          manual_2:
            type: trigger.manual
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          first:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
          second:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
          night:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - manual.start -> first.after
          - manual_2.start -> second.after
          - nightly.start -> night.after
    """)
    said = []
    monkeypatch.setattr(dialogs, "confirm_run_now", lambda parent, name, what, starts=(), chosen=None:
                        said.append((what, list(starts))) or (True, False))  # (True: All of them)
    tab = win.open_flow(flow)

    win.run_action.trigger()  # asked which Manual Start: All of them
    both = wait_runs(qtbot, tab, 1)

    assert "Manual Start blocks (manual, manual_2)" in said[0][0] and said[0][1] == ["manual", "manual_2"], said
    assert shown(tab) == {"manual": ("success", both), "first": ("success", both), "manual_2": ("success", both),
                          "second": ("success", both), "nightly": (None, None), "night": (None, None)}

    start_from(win, tab, "manual_2")  # right-click it: Run the flow from here
    only_second = wait_runs(qtbot, tab, 2)

    assert len(said) == 1  # you chose what runs: not asked
    assert shown(tab) == {"manual": ("success", both), "first": ("success", both),
                          "manual_2": ("success", only_second), "second": ("success", only_second),
                          "nightly": (None, None), "night": (None, None)}


def test_run_now_without_a_manual_start_starts_from_each_schedule_block(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "two_schedules.yaml", """
        blocks:
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          weekly:
            type: trigger.schedule
            config: {cron: "0 3 * * 1"}
          night:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
          week:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - nightly.start -> night.after
          - weekly.start -> week.after
    """)
    said = []
    monkeypatch.setattr(dialogs, "confirm_run_now", lambda parent, name, what: said.append(what) or (True, False))
    tab = win.open_flow(flow)

    win.run_action.trigger()
    run_id = wait_runs(qtbot, tab, 1)

    assert "each of its Schedule blocks (nightly, weekly)" in said[0], said
    assert shown(tab) == {b: ("success", run_id) for b in ("nightly", "weekly", "night", "week")}
    assert history_rows(win)[0][2] == "nightly, weekly"  # Run history: what started it


def test_run_now_while_it_runs_skips_or_runs_alongside_and_remembers(win, qtbot, tmp_path, monkeypatch):
    flow = write_blocks(tmp_path / "overlap.yaml", {"manual": MANUAL,
                                                    "work": gated("go.txt", "open('count.txt', 'a').write('x')")},
                        ["manual.start -> work.after"])
    asked = []

    def choose(choice, remember=False):
        def exec_(dialog):
            asked.append(choice)
            dialog.remember.setChecked(remember)
            dialog.choice = choice
            return True
        return exec_

    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.scene.nodes["work"].status == "running", timeout=60_000)
    try:
        monkeypatch.setattr(OverlapDialog, "exec", choose(OverlapDialog.SKIP))
        win.run_flow(tab)
        assert asked == ["skip"] and len(tab.runs) == 1  # skipped: nothing more runs

        monkeypatch.setattr(OverlapDialog, "exec", choose(OverlapDialog.ALONGSIDE, remember=True))
        win.run_flow(tab)
        assert len(tab.runs) == 2 and all(r.running for r in tab.runs)

        monkeypatch.setattr(OverlapDialog, "exec", choose("not asked again"))
        win.run_flow(tab)
        assert len(asked) == 2 and len(tab.runs) == 3  # remembered for this flow
    finally:
        (tmp_path / "go.txt").write_text("")
    qtbot.waitUntil(lambda: not tab.running and all(r.status for r in tab.runs), timeout=60_000)

    assert [r.status for r in tab.runs] == ["success"] * 3 and (tmp_path / "count.txt").read_text() == "xxx"


def test_f5_runs_the_flow_and_shift_f5_stops_it(win, qtbot, tmp_path):
    flow = write_blocks(tmp_path / "keys.yaml", {"manual": MANUAL, "work": gated("never.txt"),
                                                 "after": appending("after.txt")},
                        ["manual.start -> work.after", "work.done -> after.after"])
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.open_flow(flow)
    tab.view.setFocus()  # working on the canvas

    QTest.keyClick(tab.view, Qt.Key_F5)  # Run now
    qtbot.waitUntil(lambda: tab.scene.nodes["work"].status == "running", timeout=60_000)
    QTest.keyClick(tab.view, Qt.Key_F5, Qt.ShiftModifier)  # Stop
    run_id = wait_runs(qtbot, tab, 1)

    assert tab.runs[-1].status == "cancelled" and shown(tab, ["manual", "work"]) == {
        "manual": ("success", run_id), "work": ("cancelled", run_id)}
    assert not (tmp_path / "after.txt").exists()


# --- Stop --------------------------------------------------------------------------------

def test_stop_ends_a_run_whose_block_is_busy_and_nothing_after_it_runs(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "busy.yaml", """
        blocks:
          manual:
            type: trigger.manual
          busy:
            type: logic.python
            config: {inputs: [], outputs: [], code: "import time; time.sleep(120)"}
          after:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('after.txt', 'w').write('ran')"}
        edges:
          - manual.start -> busy.after
          - busy.done -> after.after
    """)
    tab = win.open_flow(flow)
    win.run_action.trigger()
    qtbot.waitUntil(lambda: tab.scene.nodes["busy"].status == "running", timeout=60_000)
    reruns = [a for a in win._block_menu_for(tab, "busy").actions() if a.text().startswith("Run ")]
    assert len(reruns) == 4 and not any(a.isEnabled() for a in reruns)  # no re-run while a run is going

    asked = time.monotonic()
    win.stop_action.trigger()  # Stop (Shift+F5)
    wait_done(qtbot, tab, timeout=30_000)

    assert time.monotonic() - asked < 20
    assert tab.last["status"] == "cancelled" and tab.scene.nodes["busy"].status == "cancelled"
    assert not (tmp_path / "after.txt").exists()
    lines = tooltip_lines(win, tab)  # how the line last ended
    assert any(re.match(rf"manual: cancelled {WHEN}", line) for line in lines), lines


def test_closing_the_editor_while_a_run_is_going_asks_and_no_keeps_it_going(win, qtbot, tmp_path, monkeypatch):
    flow = write_blocks(tmp_path / "going.yaml", {"manual": MANUAL, "work": gated("go.txt")},
                        ["manual.start -> work.after"])
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.scene.nodes["work"].status == "running", timeout=60_000)
    run = tab.runs[-1]

    win.close()  # answered No (the fixture)
    assert [(kind, title) for kind, (title, _) in win.message_boxes] == [("question", "Runs in progress")]
    win.message_boxes.clear()
    assert win.isVisible() and run.running

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    win.close()  # Yes: stop them and quit

    assert not win.isVisible() and not run.running
    assert runs_of(win, flow)[0]["status"] == "cancelled"


# --- the Log -----------------------------------------------------------------------------

def test_the_log_keeps_your_earlier_runs_above_the_next_run_now_and_a_rerun(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "passes.yaml", """
        blocks:
          manual:
            type: trigger.manual
          count:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                import os
                n = os.path.getsize('count.txt') + 1 if os.path.exists('count.txt') else 1
                open('count.txt', 'a').write('x')
                print(f'this is pass {n}')
        edges:
          - manual.start -> count.after
    """)
    tab = win.open_flow(flow)
    win.run_action.trigger()  # Run now
    first = wait_runs(qtbot, tab, 1)
    win.run_action.trigger()  # Run now again
    second = wait_runs(qtbot, tab, 2)
    menu_action(win, tab, "count", "Run only this block, with the same inputs").trigger()
    third = wait_runs(qtbot, tab, 3)

    text = log_text(win)
    order = [f"run #{first} · ", "this is pass 1", f"run #{second} · ", "this is pass 2",
             f"run #{third} · ", "this is pass 3"]
    at = [text.find(part) for part in order]
    assert -1 not in at and at == sorted(at), text
    for run_id in (first, second, third):  # each run starts with a bold line: its number, date and time
        assert re.search(rf"run #{run_id} · {WHEN}", text), text
        assert in_bold(win, f"run #{run_id} · "), run_id

def test_the_log_shows_the_selected_blocks_messages_until_show_all_and_copies_them_as_written(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "talk.yaml", """
        blocks:
          manual:
            type: trigger.manual
          first:
            type: logic.python
            config: {inputs: [], outputs: [], code: "print('<b>first</b> & co,   three spaces')"}
          second:
            type: logic.python
            config: {inputs: [], outputs: [], code: "print('second speaks')"}
        edges:
          - manual.start -> first.after
          - first.done -> second.after
    """)
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.open_flow(flow)
    win.run_flow(tab)
    run_id = wait_runs(qtbot, tab, 1)
    message = "<b>first</b> & co,   three spaces"
    assert message in log_text(win) and "second speaks" in log_text(win)  # as written, not as markup

    tab.scene.nodes["second"].setSelected(True)  # select a block: its messages
    text = log_text(win)
    assert "second speaks" in text and message not in text and f"run #{run_id} · " in text

    win.log.clear_filter.click()  # Show all
    assert message in log_text(win) and "second speaks" in log_text(win)
    assert tab.scene.selected_block_ids() == []

    QApplication.clipboard().setText("")
    win.log.text.setFocus()
    win.log.text.selectAll()
    QTest.keyClick(win.log.text, Qt.Key_C, Qt.ControlModifier)
    assert message in QApplication.clipboard().text()  # its spaces are spaces


def test_a_flow_running_in_another_tab_keeps_its_messages_to_its_own_log(win, qtbot, tmp_path):
    slow = write_blocks(tmp_path / "slow.yaml", {"manual": MANUAL,
                                                 "talk": gated("go.txt", "print('message from the slow flow')")},
                        ["manual.start -> talk.after"])
    quick = write_flow(tmp_path / "quick.yaml", """
        blocks:
          manual:
            type: trigger.manual
          talk:
            type: logic.python
            config: {inputs: [], outputs: [], code: "print('message from the quick flow')"}
        edges:
          - manual.start -> talk.after
    """)
    slow_tab = win.open_flow(slow)
    win.run_flow(slow_tab)
    qtbot.waitUntil(lambda: slow_tab.scene.nodes["talk"].status == "running", timeout=60_000)
    quick_tab = win.open_flow(quick)  # now on screen
    win.run_flow(quick_tab)
    wait_done(qtbot, quick_tab)
    (tmp_path / "go.txt").write_text("")  # the slow flow speaks while the quick one is on screen
    wait_done(qtbot, slow_tab)

    assert "message from the quick flow" in log_text(win) and "message from the slow flow" not in log_text(win)
    win.tabs.setCurrentWidget(slow_tab)
    assert "message from the slow flow" in log_text(win) and "message from the quick flow" not in log_text(win)


# --- Data preview ------------------------------------------------------------------------

def test_data_preview_shows_a_table_as_a_grid_and_each_output_and_why_a_failed_block_has_none(win, qtbot, tmp_path):
    flow = tmp_path / "preview.yaml"
    flow.write_text(textwrap.dedent("""
        blocks:
          manual:
            type: trigger.manual
          make:
            type: logic.python
            config:
              inputs: []
              outputs: [rows, note]
              code: |
                rows = pl.DataFrame({'n': list(range(250)), 'name': ['Zoë'] * 250})
                note = 'a note'
          broken:
            type: logic.python
            config: {inputs: [], outputs: [], code: "raise ValueError('it broke')"}
        edges:
          - manual.start -> make.after
          - manual.start -> broken.after
    """), encoding="utf-8")  # a flow file is UTF-8 (as Taskloom saves it), not the system's default encoding
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    preview = win.preview

    tab.scene.nodes["make"].setSelected(True)
    assert [preview.ports.itemText(i) for i in range(preview.ports.count())] == ["rows", "note"]
    assert not preview.ports.isHidden()  # several outputs: choose one
    model = preview.view.model()
    assert (model.rowCount(), model.columnCount()) == (200, 2)  # a grid of the first rows
    assert model.data(model.index(0, 1)) == "Zoë" and model.data(model.index(199, 0)) == "199"
    assert "250 rows" in preview.label.text() and "200" in preview.label.text(), preview.label.text()

    preview.ports.setCurrentText("note")
    assert not preview.text.isHidden() and "a note" in preview.text.toPlainText()

    tab.scene.clearSelection()
    tab.scene.nodes["broken"].setSelected(True)
    assert "failed" in preview.label.text() and "it broke" in preview.label.text(), preview.label.text()


# --- Run history -------------------------------------------------------------------------

def test_run_history_lists_each_run_with_its_parameters_and_reopens_an_older_one(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "days.yaml", """
        params:
          n: {type: int, value: 1}
        blocks:
          manual:
            type: trigger.manual
          check:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                print(f"n is {params['n']}")
                if params['n'] == 2:
                    raise ValueError('two is not allowed')
        edges:
          - manual.start -> check.after
    """)
    typed = iter([{"n": "2"}, {}])

    def exec_(dialog):  # the Run window: change n the first time, keep it the second
        for name, text in next(typed).items():
            dialog.edits[name].setText(text)
        return True

    monkeypatch.setattr(RunDialog, "exec", exec_)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    first = wait_runs(qtbot, tab, 1)
    win.run_flow(tab)
    second = wait_runs(qtbot, tab, 2)

    rows = history_rows(win)
    assert [h.text() for h in map(win.history_panel.table.horizontalHeaderItem, range(6))] == \
        ["Run", "Started", "Started by", "Status", "Duration", "Parameters"]
    assert [(r[0], r[2], r[3], r[5]) for r in rows] == [(str(second), "manual", "success", "n=1"),
                                                        (str(first), "manual", "failed", "n=2")]
    assert all(re.fullmatch(WHEN, r[1]) and re.fullmatch(r"\d+\.\d s", r[4]) for r in rows), rows

    click_row(win.history_panel.table, 1, double=True)  # double-click the older run

    assert shown(tab, ["check"]) == {"check": ("failed", first)}
    assert "n is 2" in log_text(win) and "two is not allowed" in log_text(win) and "n is 1" not in log_text(win)


def test_run_history_lists_a_scheduled_run_that_ended_while_you_look(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "listed.yaml", """
        blocks:
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          job:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - nightly.start -> job.after
    """)
    tab = win.open_flow(flow)
    assert history_rows(win) == []

    scheduled = run_elsewhere(win, flow, "--trigger", "nightly")  # as the scheduler runs it
    assert eventually(qtbot, lambda: shown(tab, ["job"]) == {"job": ("success", scheduled)})  # the canvas shows it

    assert [(r[0], r[2], r[3]) for r in history_rows(win)] == [(str(scheduled), "nightly", "success")], \
        history_rows(win)


def test_a_flow_in_a_folder_with_an_accented_name_keeps_its_runs(win, qtbot, tmp_path, monkeypatch):
    folder = tmp_path / "Informes del Año"  # ñ as a keyboard types it (one letter)
    folder.mkdir()
    flow = write_flow(folder / "report.yaml", """
        blocks:
          manual:
            type: trigger.manual
          work:
            type: logic.python
            config: {inputs: [], outputs: [], code: "print('report written')"}
        edges:
          - manual.start -> work.after
    """)
    tab = win.open_flow(flow)

    win.run_flow(tab)
    run_id = wait_runs(qtbot, tab, 1)

    assert [r[0] for r in history_rows(win)] == [str(run_id)], history_rows(win)  # Run history lists it
    assert tab_icon_is(win, tab, "success")  # the tab shows how it ended
    assert in_block_results(win, tab, "work", monkeypatch, lambda dialog: dialog.table.rowCount()) == 1


# --- Results of this block ---------------------------------------------------------------

def test_results_of_a_block_that_never_ran_say_so(win, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "never.yaml", """
        blocks:
          manual:
            type: trigger.manual
          work:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - manual.start -> work.after
    """)
    tab = win.open_flow(flow)

    def look(dialog):
        texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
        (show,) = [b for b in dialog.findChildren(QAbstractButton) if b.text().replace("&", "") == "Show this run"]
        return texts, show.isEnabled(), dialog.table.rowCount()

    texts, can_show, rows = in_block_results(win, tab, "work", monkeypatch, look)

    assert "work has not taken part in a run of this flow yet" in texts and not can_show and rows == 0
    unsaved = win.new_flow()
    block = unsaved.doc.add_block("logic.python", 0, 0)
    (results,) = [a for a in win._block_menu_for(unsaved, block).actions() if a.text() == "Results of this block…"]
    assert not results.isEnabled()  # a flow never saved has no runs
    unsaved.doc.dirty = False


# --- Ask User during a run ---------------------------------------------------------------

PASSWORD = "Tr0ub4dor-and-3"
ASK_PASSWORD = """
    blocks:
      manual:
        type: trigger.manual
      ask:
        type: logic.ask_user
        config: {prompt: "Your password?", kind: password}
      use:
        type: logic.python
        config: {inputs: [pw], outputs: [], code: "open('got.txt', 'w').write(pw); print('using', pw)"}
    edges:
      - manual.start -> ask.after
      - ask.answer -> use.pw
"""


def run_with_password(win, qtbot, tab):
    win.run_flow(tab)
    dialog = visible_ask(qtbot, win)
    dialog.input.setText(PASSWORD)
    click(dialog, "OK")
    return wait_runs(qtbot, tab, len(tab.runs))


def test_a_password_you_give_ask_user_shows_as_stars_in_the_log_and_nowhere_on_the_canvas(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "password.yaml", ASK_PASSWORD)
    tab = win.open_flow(flow)

    run_with_password(win, qtbot, tab)

    assert (tmp_path / "got.txt").read_text() == PASSWORD and tab.scene.nodes["use"].status == "success"
    assert "using ***" in log_text(win) and PASSWORD not in log_text(win)
    for node in tab.scene.nodes.values():  # hovering the canvas: inputs, outputs, the message under a block
        assert all(PASSWORD not in port.toolTip() for port in node.ports.values())
        assert PASSWORD not in node.footer.toolTip()
    assert tab.scene.edge_items and all(PASSWORD not in edge.toolTip() for edge in tab.scene.edge_items)


def test_a_rerun_refused_for_a_password_says_why_and_the_block_keeps_its_result(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "password.yaml", ASK_PASSWORD)
    tab = win.open_flow(flow)
    run_id = run_with_password(win, qtbot, tab)
    assert shown(tab, ["use"]) == {"use": ("success", run_id)}

    menu_action(win, tab, "use", "Run only this block, with the same inputs").trigger()
    wait_runs(qtbot, tab, 2)

    text = log_text(win)
    assert "held a password" in text and "run the whole flow" in text, text
    assert shown(tab, ["use"]) == {"use": ("success", run_id)}  # the refused re-run did not run: it still shows that
    assert menu_action(win, tab, "use", "Run only this block, with the same inputs").isEnabled()


def test_two_ask_user_questions_in_a_run_come_one_after_another_each_titled_by_its_block(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "questions.yaml", """
        blocks:
          manual:
            type: trigger.manual
          go_on:
            type: logic.ask_user
            config: {prompt: "Go on?", kind: yes_no}
          name:
            type: logic.ask_user
            config: {prompt: "Whose report?", kind: text}
          use:
            type: logic.python
            config: {inputs: [ok, who], outputs: [], code: "open('answers.txt', 'w').write(repr((ok, who)))"}
        edges:
          - manual.start -> go_on.after
          - go_on.done -> name.after
          - go_on.answer -> use.ok
          - name.answer -> use.who
    """)
    tab = win.open_flow(flow)
    win.run_flow(tab)

    first = visible_ask(qtbot, win)
    assert first.windowTitle() == "go_on asks"
    assert [d for d in win.findChildren(AskDialog) if d.isVisible()] == [first]  # one question at a time
    click(first, "Yes")
    qtbot.waitUntil(lambda: any(d.isVisible() and d is not first for d in win.findChildren(AskDialog)),
                    timeout=60_000)
    second = visible_ask(qtbot, win)
    assert second.windowTitle() == "name asks"
    second.input.setText("the night team")
    click(second, "OK")
    wait_runs(qtbot, tab, 1)

    assert (tmp_path / "answers.txt").read_text() == "(True, 'the night team')"


def test_stop_while_ask_user_waits_for_an_answer_cancels_the_run(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "waiting.yaml", """
        blocks:
          manual:
            type: trigger.manual
          ask:
            type: logic.ask_user
            config: {prompt: "Which region?", kind: text}
          use:
            type: logic.python
            config: {inputs: [region], outputs: [], code: "open('region.txt', 'w').write(region)"}
        edges:
          - manual.start -> ask.after
          - ask.answer -> use.region
    """)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    dialog = visible_ask(qtbot, win)

    asked = time.monotonic()
    win.stop_action.trigger()
    qtbot.waitUntil(lambda: not tab.running and tab.runs[-1].status is not None, timeout=30_000)

    assert time.monotonic() - asked < 5  # it stopped when asked (not killed later)
    assert tab.runs[-1].status == "cancelled" and tab.scene.nodes["ask"].status == "cancelled"
    if dialog.isVisible():  # answering the question of a run that ended does nothing
        dialog.input.setText("North")
        click(dialog, "OK")
        QApplication.processEvents()
    assert not (tmp_path / "region.txt").exists()


# --- re-runs and inputs you choose -------------------------------------------------------

CHOOSE_FLOW = """
    blocks:
      manual:
        type: trigger.manual
      a:
        type: logic.python
        config: {inputs: [], code: "output = 5"}
      b:
        type: logic.python
        config: {inputs: [], code: "output = 'b'"}
      use:
        type: logic.python
        config:
          inputs: [x, y]
          outputs: []
          code: "open('got.txt', 'w', encoding='utf-8').write(repr((x, y)))"
    edges:
      - manual.start -> a.after
      - manual.start -> b.after
      - a.output -> use.x
      - b.output -> use.y
"""


def choose_inputs(monkeypatch, typed: dict, seen: dict | None = None):
    """Answer the inputs window: note what it shows, type `typed`, press Run."""
    def exec_(dialog):
        if seen is not None:
            seen.update({name: (f.text(), f.placeholderText()) for name, f in dialog.fields.items()})
        for name, text in typed.items():
            dialog.fields[name].setText(text)
        return True
    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", exec_)


def test_an_input_left_empty_has_no_value_and_typed_text_keeps_quotes_and_backslashes(win, qtbot, tmp_path,
                                                                                       monkeypatch):
    flow = write_flow(tmp_path / "choose.yaml", CHOOSE_FLOW)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    assert (tmp_path / "got.txt").read_text(encoding="utf-8") == "(5, 'b')"
    seen = {}
    choose_inputs(monkeypatch, {"x": "", "y": "'it\\'s \"quoted\" \\\\ 100% ✓ {x}'"}, seen)

    menu_action(win, tab, "use", "Run only this block, with inputs I choose").trigger()
    wait_runs(qtbot, tab, 2)

    assert seen == {"x": ("5", ""), "y": ("'b'", "")}  # filled in with what reached it
    assert (tmp_path / "got.txt").read_text(encoding="utf-8") == repr((None, 'it\'s "quoted" \\ 100% ✓ {x}'))
    assert tab.scene.nodes["use"].status == "success"


def test_accented_text_you_type_for_an_input_reaches_the_block_as_typed(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "choose.yaml", CHOOSE_FLOW)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    typed = "Zo\u00eb Beyonc\u00e9"  # precomposed letters, as a keyboard types them
    choose_inputs(monkeypatch, {"y": repr(typed)})

    menu_action(win, tab, "use", "Run only this block, with inputs I choose").trigger()
    wait_runs(qtbot, tab, 2)

    assert ascii((tmp_path / "got.txt").read_text(encoding="utf-8")) == ascii(repr((5, typed)))


def test_an_input_value_that_is_not_python_is_refused_with_which_input(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "choose.yaml", CHOOSE_FLOW)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    (tmp_path / "got.txt").unlink()
    choose_inputs(monkeypatch, {"x": "[1,"})

    menu_action(win, tab, "use", "Run only this block, with inputs I choose").trigger()
    wait_runs(qtbot, tab, 2)

    text = log_text(win)
    assert "the value for x ([1,)" in text and "SyntaxError" in text and "Traceback" not in text, text
    assert not (tmp_path / "got.txt").exists()
    assert len(runs_of(win, flow)) == 1  # no run was recorded


def test_a_value_too_big_to_show_left_empty_is_what_the_block_got(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "big.yaml", """
        blocks:
          manual:
            type: trigger.manual
          make:
            type: logic.python
            config: {inputs: [], code: "output = list(range(3000))"}
          use:
            type: logic.python
            config: {inputs: [items], outputs: [], code: "open('count.txt', 'w').write(str(sum(items)))"}
        edges:
          - manual.start -> make.after
          - make.output -> use.items
    """)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    run_id = wait_runs(qtbot, tab, 1)
    (tmp_path / "count.txt").unlink()
    seen = {}
    choose_inputs(monkeypatch, {}, seen)  # press Run without typing

    menu_action(win, tab, "use", "Run only this block, with inputs I choose").trigger()
    wait_runs(qtbot, tab, 2)

    text, placeholder = seen["items"]
    assert text == "" and placeholder.startswith(f"empty: the list it got in run #{run_id} · "), placeholder
    assert (tmp_path / "count.txt").read_text() == str(sum(range(3000)))


def test_a_rerun_with_the_same_inputs_uses_the_parameters_of_that_run(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "day.yaml", """
        params:
          day: {type: date, value: "2026-10-01"}
        blocks:
          manual:
            type: trigger.manual
          load:
            type: logic.python
            config: {inputs: [], code: "output = 21"}
          report:
            type: logic.python
            config:
              inputs: [input]
              outputs: []
              code: |
                import os
                assert os.path.exists('fixed.txt'), 'not fixed'
                open('report.txt', 'w').write(f"{params['day']}: {input * 2}")
        edges:
          - manual.start -> load.after
          - load.output -> report.input
    """)

    def exec_(dialog):  # the Run window: another day
        dialog.edits["day"].setText("2026-09-15")
        return True

    monkeypatch.setattr(RunDialog, "exec", exec_)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    assert tab.scene.nodes["report"].status == "failed"
    (tmp_path / "fixed.txt").write_text("")

    menu_action(win, tab, "report", "Run only this block, with the same inputs").trigger()
    wait_runs(qtbot, tab, 2)

    assert (tmp_path / "report.txt").read_text() == "2026-09-15: 42"
    assert tab.scene.nodes["report"].status == "success"


def press_run_as_shown(monkeypatch, seen: dict):
    """Answer the inputs window by pressing Run without typing; note its fields and what it says."""
    def exec_(dialog):
        seen["fields"] = list(dialog.fields)
        seen["says"] = [label.text() for label in dialog.findChildren(QLabel)]
        return True
    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", exec_)


def appending(name: str) -> dict:
    """A Python Code block without inputs that adds 'ran ' to the file `name` each time it runs."""
    return {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": f"open({name!r}, 'a').write('ran ')"}}


def test_a_block_without_inputs_added_after_one_that_ran_runs_alone_with_inputs_i_choose(win, qtbot, tmp_path,
                                                                                          monkeypatch):
    flow = write_blocks(tmp_path / "no_inputs.yaml", {"manual": MANUAL, "first": appending("first.txt")},
                        ["manual.start -> first.after"])
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    added = tab.doc.add_block("logic.python", 400, 0, appending("added.txt")["config"])  # a block you add
    tab.doc.connect("first", "done", added, "after")
    # It shows no result, but the block connected into it does: "with the same inputs" takes that run.
    assert menu_action(win, tab, added, "Run only this block, with the same inputs").isEnabled()
    seen = {}
    press_run_as_shown(monkeypatch, seen)

    menu_action(win, tab, added, "Run only this block, with inputs I choose").trigger()
    run_id = wait_runs(qtbot, tab, 2)

    assert seen["fields"] == [] and "This block has no inputs: it just runs." in seen["says"], seen
    assert tab.runs[-1].status == "success"
    assert (tmp_path / "added.txt").read_text() == "ran "
    assert (tmp_path / "first.txt").read_text() == "ran "  # not run again
    assert shown(tab, [added]) == {added: ("success", run_id)}
    assert tab.scene.nodes["first"].status == "success"  # it keeps its result


def test_a_block_without_inputs_after_a_failed_one_runs_from_there_on_with_inputs_i_choose(win, qtbot, tmp_path,
                                                                                           monkeypatch):
    flow = write_blocks(tmp_path / "after_failed.yaml", {
        "manual": MANUAL,
        "broken": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "raise ValueError('no')"}},
        "cleanup": appending("cleanup.txt"), "report": appending("report.txt"),
    }, ["manual.start -> broken.after", "broken.done -> cleanup.after", "cleanup.done -> report.after"])
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    assert tab.scene.nodes["broken"].status == "failed" and not (tmp_path / "cleanup.txt").exists()
    seen = {}
    press_run_as_shown(monkeypatch, seen)

    menu_action(win, tab, "cleanup", "Run from this block on, with inputs I choose").trigger()
    run_id = wait_runs(qtbot, tab, 2)

    assert seen["fields"] == [] and "This block has no inputs: it just runs." in seen["says"], seen
    assert tab.runs[-1].status == "success", log_text(win)
    assert (tmp_path / "cleanup.txt").read_text() == "ran " and (tmp_path / "report.txt").read_text() == "ran "
    assert shown(tab, ["cleanup", "report"]) == {"cleanup": ("success", run_id), "report": ("success", run_id)}
    assert tab.scene.nodes["broken"].status == "failed"  # not run again: it keeps its result


def test_a_table_you_type_as_a_polars_dataframe_reaches_the_block_as_a_table(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "frame.yaml", """
        blocks:
          manual:
            type: trigger.manual
          make:
            type: logic.python
            config: {inputs: [], code: "output = pl.DataFrame({'a': [9]})"}
          save:
            type: data.write_file
            config: {path: out.csv}
        edges:
          - manual.start -> make.after
          - make.output -> save.table
    """)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    assert (tmp_path / "out.csv").read_text() == "a\n9\n"
    choose_inputs(monkeypatch, {"table": "pl.DataFrame({'a': [1, 2]})"})

    menu_action(win, tab, "save", "Run only this block, with inputs I choose").trigger()
    wait_runs(qtbot, tab, 2)

    assert tab.runs[-1].status == "success", log_text(win)
    assert (tmp_path / "out.csv").read_text() == "a\n1\n2\n"


# --- right-clicking a block on the canvas ------------------------------------------------

BLOCK_MENU = ["Run only this block, with the same inputs", "Run only this block, with inputs I choose…",
              "Run from this block on, with the same inputs", "Run from this block on, with inputs I choose…",
              "Results of this block…"]


def test_right_clicking_a_blocks_title_a_port_or_the_message_under_it_opens_its_menu(win, qtbot, tmp_path,
                                                                                     monkeypatch):
    flow = write_blocks(tmp_path / "clicked.yaml", {
        "manual": MANUAL,
        "make": {"type": "logic.python", "config": {
            "inputs": [], "code": "open('made.txt', 'a').write('x')\noutput = 5"}},
        "use": {"type": "logic.python", "config": {
            "inputs": ["x"], "outputs": [], "code": "open('got.txt', 'a').write(f'{x};')"}},
    }, ["manual.start -> make.after", "make.output -> use.x"])
    tab = win.open_flow(flow)
    win.run_flow(tab)
    first = wait_runs(qtbot, tab, 1)
    choose_inputs(monkeypatch, {"x": "7"})

    items = right_click(qtbot, win, tab, "use", "title", "Run only this block, with inputs I choose")

    assert [item.split(" (run #")[0] for item in items] == BLOCK_MENU, items
    assert all(f"(run #{first} · " in items[i] for i in (0, 2)), items  # the run its inputs would come from
    second = wait_runs(qtbot, tab, 2)
    assert (tmp_path / "got.txt").read_text() == "5;7;" and (tmp_path / "made.txt").read_text() == "x"

    items = right_click(qtbot, win, tab, "make", ("output", True), "Run from this block on, with the same inputs")

    assert [item.split(" (run #")[0] for item in items] == BLOCK_MENU, items  # make's menu, from its output
    third = wait_runs(qtbot, tab, 3)
    assert (tmp_path / "made.txt").read_text() == "xx" and (tmp_path / "got.txt").read_text() == "5;7;5;"
    assert shown(tab, ["make", "use"]) == {"make": ("success", third), "use": ("success", third)}

    rows = []
    monkeypatch.setattr(dialogs.BlockResultsDialog, "exec",
                        lambda dialog: rows.extend(dialog.table.item(r, 0).text()
                                                   for r in range(dialog.table.rowCount())) or 0)
    items = right_click(qtbot, win, tab, "use", "footer", "Results of this block")

    assert [item.split(" (run #")[0] for item in items] == BLOCK_MENU, items
    assert rows == [str(third), str(second), str(first)], rows  # use's results, newest first


def test_right_clicking_a_start_block_runs_the_flow_from_it_and_only_its_line(win, qtbot, tmp_path):
    flow = write_blocks(tmp_path / "starts.yaml", {
        "manual": MANUAL, "first": printing("first line"), "nightly": NIGHTLY, "night": printing("night line"),
    }, ["manual.start -> first.after", "nightly.start -> night.after"])
    tab = win.open_flow(flow)

    items = right_click(qtbot, win, tab, "nightly", "countdown", "Run the flow now, as if its time had come")

    assert items == ["Run the flow now, as if its time had come"], items
    night = wait_runs(qtbot, tab, 1)
    assert shown(tab) == {"manual": (None, None), "first": (None, None),
                          "nightly": ("success", night), "night": ("success", night)}

    items = right_click(qtbot, win, tab, "manual", "title", "Run the flow from here")

    assert items == ["Run the flow from here"], items
    manual = wait_runs(qtbot, tab, 2)
    assert shown(tab) == {"manual": ("success", manual), "first": ("success", manual),
                          "nightly": ("success", night), "night": ("success", night)}
    assert [(r[0], r[2]) for r in history_rows(win)] == [(str(manual), "manual"), (str(night), "nightly")]


# --- the flows' tabs and the flows running now -------------------------------------------

def test_a_scheduled_flows_tab_shows_a_clock_until_its_schedule_block_is_removed(win, tmp_path):
    flow = write_blocks(tmp_path / "clocked.yaml", {"manual": MANUAL, "nightly": NIGHTLY, "work": printing("hi")},
                        ["manual.start -> work.after", "nightly.start -> work.after"])
    tab = win.open_flow(flow)

    assert win.save(tab)  # saving a flow with a Schedule block schedules it
    assert tab_icon_shows(win, tab, None, clock=True)  # not run yet, and a clock
    assert tooltip_lines(win, tab)[-1].endswith("· scheduler service off"), tooltip_lines(win, tab)
    win.close_tab(tab)
    tab = win.open_flow(flow)
    assert tab_icon_shows(win, tab, None, clock=True)  # opened again: still scheduled

    tab.scene.nodes["nightly"].setSelected(True)
    tab.scene.delete_selected()  # Edit > Delete
    assert win.save(tab)

    assert tab_icon_shows(win, tab, None, clock=False)
    said = tooltip_lines(win, tab)[1:]  # (under the file's path): no next run any more
    assert said and not any("scheduler" in line or "next" in line for line in said), said


def test_flows_running_lists_your_runs_and_those_from_a_terminal_and_opens_them(win, qtbot, tmp_path):
    mine = write_blocks(tmp_path / "mine.yaml", {"manual": MANUAL, "work": gated("mine_go.txt")},
                        ["manual.start -> work.after"])
    theirs = write_blocks(tmp_path / "theirs.yaml", {"manual": MANUAL, "work": gated("theirs_go.txt")},
                          ["manual.start -> work.after"])
    tab = win.open_flow(mine)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.scene.nodes["work"].status == "running", timeout=60_000)
    terminal = start_elsewhere(win, theirs)
    try:
        assert eventually(qtbot, lambda: win.running_flows.text() == "2 flows running", 30_000), \
            win.running_flows.text()
        items = {a.text().split("  ·  ")[0]: a for a in win.running_flows.menu().actions()}
        assert sorted(items) == ["mine", "theirs"], list(items)

        items["theirs"].trigger()  # choose one: it opens
        assert win.current.doc.path == theirs.resolve()
    finally:
        (tmp_path / "mine_go.txt").write_text("")
        (tmp_path / "theirs_go.txt").write_text("")
        terminal.wait(60)
        stop_elsewhere(terminal)
    wait_done(qtbot, tab)

    assert eventually(qtbot, lambda: win.running_flows.text() == "No flows running"), win.running_flows.text()


# --- the scheduler service when the editor opens -----------------------------------------

YEARLY = {"type": "trigger.schedule", "config": {"cron": "0 2 1 1 *"}}  # its time never comes during a test


def open_the_editor(qtbot, home) -> MainWindow:
    """The editor opened (again) on this Taskloom folder, as `taskloom editor` opens it, and given a turn."""
    window = MainWindow(Home(home.root))
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(500)
    return window


def test_the_editor_opening_with_flows_scheduled_starts_the_scheduler_service(win, qtbot, tmp_path, monkeypatch):
    starts = []  # (not a real service: it would outlive the test)
    monkeypatch.setattr(MainWindow, "start_scheduler", lambda self, by_you=False: starts.append(by_you))
    open_the_editor(qtbot, win.home)
    assert starts == []  # nothing scheduled: nothing to start
    tab = win.open_flow(write_blocks(tmp_path / "yearly.yaml", {"yearly": YEARLY, "work": printing("hi")},
                                     ["yearly.start -> work.after"]))
    assert win.save(tab)  # saved with a Schedule block: scheduled
    assert starts == [False]
    win.close()

    open_the_editor(qtbot, win.home)  # later (the service is not running): the editor opens again

    assert starts == [False, False]  # it started by itself, not as if you had turned it on


@pytest.mark.skipif(sys.platform == "win32", reason="the editor turns the service off with SIGTERM")
def test_the_editor_opening_with_flows_scheduled_leaves_the_service_off_if_you_turned_it_off(win, qtbot, tmp_path,
                                                                                             monkeypatch):
    tab = win.open_flow(write_blocks(tmp_path / "yearly.yaml", {"yearly": YEARLY, "work": printing("hi")},
                                     ["yearly.start -> work.after"]))
    assert win.save(tab)  # scheduled
    service = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"],
                               env={**os.environ, "TASKLOOM_HOME": str(win.home.root)},
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        qtbot.waitUntil(win.scheduler_running, timeout=30_000)
        asked = []
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: asked.append(a[1]) or QMessageBox.Yes))
        win.scheduler_action.trigger()  # the Scheduler service button: turn it off (yes)
        assert asked == ["Turn off the scheduler service?"]
        assert service.wait(30) is not None
    finally:
        stop_elsewhere(service)
    starts = []
    monkeypatch.setattr(MainWindow, "start_scheduler", lambda self, by_you=False: starts.append(by_you))

    open_the_editor(qtbot, win.home)

    assert starts == [] and not win.scheduler_running()  # it stays off until you turn it on
