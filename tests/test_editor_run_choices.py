"""Running flows and the flows' tabs, in the real editor with real runner processes: Run now in a flow with
several Manual Starts asks which one to start (remembered per flow), the + button by the last tab opens a new
flow, a scheduled flow with unsaved changes says the scheduler runs its saved version, and a saved flow is
named by its file (never 'untitled') where its runs are listed."""

import os
import re

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox, QDialog,  # noqa: E402
                               QDockWidget, QFileDialog, QLabel, QListWidget, QMessageBox, QPushButton,
                               QRadioButton, QTabBar, QWidget)

from taskloom.editor import dialogs  # noqa: E402
from taskloom.editor.window import MainWindow  # noqa: E402
from taskloom.flow import load_flow  # noqa: E402
from tests.test_editor import add_from_palette, wait_done  # noqa: E402
from tests.test_lines import shown, wait_runs  # noqa: E402
from tests.test_paths_editor_running import (MANUAL, REAL_CONFIRM_RUN_NOW, eventually, gated,  # noqa: E402
                                             history_rows, start_elsewhere, stop_elsewhere)

ALL = "All of them"
RUN = object()  # answer the question with Run, choosing nothing
SAVED_VERSION = re.compile(r"saved version", re.I)


# --- helpers ---------------------------------------------------------------------------

def writes(name: str) -> dict:
    """A Python Code block that adds an x to the file `name` (next to the flow) each time it runs."""
    return {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": f"open({name!r}, 'a').write('x')"}}


def write_blocks(path, blocks: dict, edges: list, **top):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({**top, "blocks": blocks, "edges": edges}, sort_keys=False), encoding="utf-8")
    return path


def two_manual_starts(path):
    """A flow with two Manual Starts, each with its own line: load -> fetch, report -> summary."""
    return write_blocks(path, {"load": MANUAL, "fetch": writes("fetch.txt"),
                               "report": MANUAL, "summary": writes("summary.txt")},
                        ["load.start -> fetch.after", "report.start -> summary.after"])


def written(folder) -> dict:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.txt"))}


def menu_item(win, menu: str, text: str):
    (found,) = [a.menu() for a in win.menuBar().actions() if a.text().replace("&", "") == menu]
    return next(a for a in found.actions() if a.text() == text)


def _plain(text: str) -> str:
    return text.replace("&", "").strip()


def names(choice: str | None, start) -> bool:
    """The choice text is the one for the Manual Start `start` (ALL: the one for all of them)."""
    if choice is None:
        return False
    if start is ALL:
        return ALL.lower() in choice.lower()
    return ALL.lower() not in choice.lower() and re.search(rf"(?<!\w){re.escape(start)}(?!\w)", choice) is not None


ANSWERS = {"Run", "Run now", "OK", "Yes", "No", "Cancel"}


def _choices(dialog):
    """What the question offers to choose from: (the choices' texts, the one selected, select(i), whether
    selecting one answers the question). Radio buttons, a list to pick from, or a button per choice."""
    radios = [b for b in dialog.findChildren(QRadioButton) if b.isVisible()]
    if radios:
        return ([_plain(b.text()) for b in radios], next((_plain(b.text()) for b in radios if b.isChecked()), None),
                lambda i: radios[i].click(), False)
    combos = [c for c in dialog.findChildren(QComboBox) if c.isVisible()]
    if len(combos) == 1:
        (combo,) = combos

        def select(i):
            combo.setCurrentIndex(i)
            combo.activated.emit(i)
        return [combo.itemText(i) for i in range(combo.count())], combo.currentText(), select, False
    lists = [w for w in dialog.findChildren(QListWidget) if w.isVisible()]
    if len(lists) == 1:
        (items,) = lists
        current = items.currentItem()
        return ([items.item(i).text() for i in range(items.count())], current.text() if current else None,
                items.setCurrentRow, False)
    if isinstance(dialog, QMessageBox):
        buttons = [b for b in dialog.buttons() if _plain(b.text()) not in ANSWERS]
        if len(buttons) > 1:
            default = dialog.defaultButton()
            return ([_plain(b.text()) for b in buttons], _plain(default.text()) if default in buttons else None,
                    lambda i: buttons[i].click(), True)
    return [], None, None, False


def _press(dialog, texts) -> bool:
    button = next((b for b in dialog.findChildren(QPushButton) if b.isVisible() and _plain(b.text()) in texts), None)
    if button is not None:
        QTest.mouseClick(button, Qt.LeftButton)
    return button is not None


def answer_run_now(win, questions: list, choose, tick: bool = False):
    """When Run now's question shows: note what it offers ({"choices", "selected"} in `questions`), tick its
    "Don't ask me again" if `tick`, choose the Manual Start named `choose` (ALL: all of them) and run; RUN: run
    without choosing; None: Cancel. Nothing to choose `choose` by: Cancel (nothing runs). It looks for the
    question for about 5 s; none showing is left to the test to notice."""
    tries = []

    def act():
        dialog = QApplication.activeModalWidget() or next(
            (w for w in QApplication.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()), None)
        if not isinstance(dialog, QDialog) or not dialog.isVisible():
            if len(tries) < 250:
                tries.append(1)
                QTimer.singleShot(20, win, act)
            return
        try:
            texts, selected, select, answers = _choices(dialog)
            questions.append({"choices": texts, "selected": selected})
            if tick:
                never = [b for b in dialog.findChildren(QCheckBox) if b.isVisible() and "ask" in b.text().lower()]
                for box in never:
                    QTest.mouseClick(box, Qt.LeftButton)
                questions[-1]["ticked"] = bool(never)
            matching = [i for i, text in enumerate(texts) if names(text, choose)] if choose not in (None, RUN) else []
            if choose is RUN or len(matching) == 1:
                if choose is not RUN:
                    select(matching[0])
                if dialog.isVisible() and not answers:
                    questions[-1]["ran"] = _press(dialog, ("Run", "Run now", "OK"))
            elif not _press(dialog, ("Cancel",)):
                dialog.reject()
        finally:
            if dialog.isVisible():  # whatever happened, the question never stays waiting (it would hang the test)
                dialog.reject()

    QTimer.singleShot(0, win, act)


def asked(qtbot, questions: list, n: int):
    def check():
        assert len(questions) == n, f"Run now's question did not show (questions so far: {questions})"
    qtbot.waitUntil(check, timeout=8_000)


def _side_docks(win) -> list:
    return [d for d in win.findChildren(QDockWidget)
            if win.dockWidgetArea(d) in (Qt.LeftDockWidgetArea, Qt.RightDockWidgetArea)]


def plus_button(win) -> QAbstractButton:
    """The + button by the flows' tabs (not the canvas's zoom +, nor one in a panel)."""
    pages, docks = win.tabs_list(), win.findChildren(QDockWidget)
    found = [b for b in win.findChildren(QAbstractButton)
             if b.isVisible() and (_plain(b.text()) in ("+", "＋") or "new flow" in b.toolTip().lower())
             and not any(p.isAncestorOf(b) for p in pages) and not any(d.isAncestorOf(b) for d in docks)]
    assert len(found) == 1, f"no single + button by the tabs: {[(b.text(), b.toolTip()) for b in found]}"
    return found[0]


def check_plus_after_last_tab(win):
    """The + button is just right of the last tab, on the tabs' row, and nothing covers or clips it there."""
    QApplication.processEvents()
    bar = win.tabs.tabBar()
    last = bar.tabRect(bar.count() - 1)
    right, top, bottom = (bar.mapTo(win, last.topRight()).x(), bar.mapTo(win, last.topLeft()).y(),
                          bar.mapTo(win, last.bottomLeft()).y())
    edge = win.tabs.mapTo(win, QPoint(win.tabs.width(), 0)).x()
    button = plus_button(win)
    assert right + button.width() + 30 < edge, f"(the tabs do not fit: last tab ends at {right}, room up to {edge})"
    left, center = button.mapTo(win, QPoint(0, 0)).x(), button.mapTo(win, button.rect().center())
    assert -2 <= left - right <= 30, f"the + starts at x={left}, the last tab ends at x={right}"
    assert top <= center.y() <= bottom, f"the + is at y={center.y()}, the tabs' row from {top} to {bottom}"
    hit = win.childAt(center)
    assert hit is button or button.isAncestorOf(hit), f"at the + is {hit!r}"


def tabs_scroll(bar) -> bool:
    return bar.tabRect(0).left() < 0 or bar.tabRect(bar.count() - 1).right() >= bar.width()


def check_plus_clear_of_the_tabs(win):
    """The + button shows and takes the click, over none of the tabs nor the tab bar's own controls (its scroll
    arrows, the tabs' close buttons), as far as they show in the bar."""
    QApplication.processEvents()
    bar, button = win.tabs.tabBar(), plus_button(win)
    assert button.isVisible()
    place = QRect(button.mapTo(win, QPoint(0, 0)), button.size())
    hit = win.childAt(place.center())
    assert hit is button or button.isAncestorOf(hit), f"at the + is {hit!r}"
    in_bar = QRect(bar.mapTo(win, QPoint(0, 0)), bar.size())
    for i in range(bar.count()):
        tab = QRect(bar.mapTo(win, bar.tabRect(i).topLeft()), bar.tabRect(i).size()).intersected(in_bar)
        assert not place.intersects(tab), f"the + at {place} is over tab {i} at {tab}"
    for child in bar.findChildren(QWidget):
        if child.isVisible() and child is not button and not button.isAncestorOf(child):
            shows = QRect(child.mapTo(win, QPoint(0, 0)), child.size()).intersected(in_bar)
            assert not place.intersects(shows), f"the + at {place} is over the tab bar's {child!r} at {shows}"


# --- 1. Run now with several Manual Starts -------------------------------------------------

def test_run_now_with_two_manual_starts_asks_which_and_runs_only_its_line(win, qtbot, tmp_path, monkeypatch):
    """The user's case: a new flow (its Manual Start `start`) and a second Manual Start added to it. Run now
    asks which one to start; choosing the second runs only the blocks after it."""
    monkeypatch.setattr(dialogs, "confirm_run_now", REAL_CONFIRM_RUN_NOW)  # the real question
    flow = tmp_path / "two starts.yaml"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(flow), "")))
    menu_item(win, "File", "New flow").trigger()
    tab = win.current
    add_from_palette(win, "Manual Start")
    (second,) = [b for b, spec in tab.doc.blocks.items() if spec["type"] == "trigger.manual" and b != "start"]
    first_job = tab.doc.add_block("logic.python", 0, -150, writes("first.txt")["config"])
    second_job = tab.doc.add_block("logic.python", 0, 150, writes("second.txt")["config"])
    tab.doc.connect("start", "start", first_job, "after")
    tab.doc.connect(second, "start", second_job, "after")
    questions = []

    answer_run_now(win, questions, second)
    win.run_action.trigger()  # Run now: saved where you said, then asked which start
    asked(qtbot, questions, 1)

    (question,) = questions
    assert len(question["choices"]) == 3, question
    assert [any(names(c, s) for c in question["choices"]) for s in ("start", second, ALL)] == [True] * 3, question
    run_id = wait_runs(qtbot, tab, 1)
    assert written(tmp_path) == {"second.txt": "x"}  # only the second start's line ran
    assert shown(tab) == {"start": (None, None), first_job: (None, None),
                          second: ("success", run_id), second_job: ("success", run_id)}
    assert history_rows(win)[0][2] == second  # Run history: started by it alone (as its own Run the flow from here)


def test_run_now_remembers_the_choice_per_flow_and_all_of_them_runs_every_line_and_cancel_nothing(
        win, qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(dialogs, "confirm_run_now", REAL_CONFIRM_RUN_NOW)
    daily = two_manual_starts(tmp_path / "a" / "daily.yaml")
    weekly = two_manual_starts(tmp_path / "b" / "weekly.yaml")  # another flow, with the same start blocks
    questions = []
    tab = win.open_flow(daily)

    answer_run_now(win, questions, "report")
    win.run_action.trigger()
    asked(qtbot, questions, 1)
    assert any(names(c, "report") for c in questions[0]["choices"]), questions[0]
    wait_runs(qtbot, tab, 1)
    assert written(daily.parent) == {"summary.txt": "x"}  # only report's line

    other = win.open_flow(weekly)
    answer_run_now(win, questions, None)
    win.run_action.trigger()  # its own choice: not the one made in the other flow; Cancel
    asked(qtbot, questions, 2)
    assert not names(questions[1]["selected"], "report"), questions[1]
    assert not other.runs and written(weekly.parent) == {}  # cancelled: nothing ran

    restarted = MainWindow(win.home)  # the editor started again: the choice is in editor.ini
    qtbot.addWidget(restarted)
    restarted.show()
    again = restarted.open_flow(daily)
    answer_run_now(restarted, questions, ALL)
    restarted.run_action.trigger()
    asked(qtbot, questions, 3)
    assert names(questions[2]["selected"], "report"), questions[2]  # the choice made last time for this flow
    wait_runs(qtbot, again, 1)
    assert written(daily.parent) == {"fetch.txt": "x", "summary.txt": "xx"}  # all of them: both lines
    assert history_rows(restarted)[0][2] == "load, report"

    answer_run_now(restarted, questions, None)
    restarted.run_action.trigger()  # Cancel
    asked(qtbot, questions, 4)
    assert names(questions[3]["selected"], ALL), questions[3]
    assert len(again.runs) == 1 and written(daily.parent) == {"fetch.txt": "x", "summary.txt": "xx"}


def test_dont_ask_me_again_does_not_hide_the_choice_of_manual_start(win, qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(dialogs, "confirm_run_now", REAL_CONFIRM_RUN_NOW)
    single = write_blocks(tmp_path / "one" / "single.yaml", {"manual": MANUAL, "count": writes("count.txt")},
                          ["manual.start -> count.after"])
    both = two_manual_starts(tmp_path / "two" / "both.yaml")
    questions = []
    tab = win.open_flow(single)

    answer_run_now(win, questions, RUN, tick=True)
    win.run_action.trigger()  # one Manual Start: asked as before (nothing to choose); Don't ask me again, Run
    asked(qtbot, questions, 1)
    assert questions[0]["choices"] == [] and questions[0]["ticked"], questions[0]
    wait_runs(qtbot, tab, 1)

    two = win.open_flow(both)
    answer_run_now(win, questions, "load")
    win.run_action.trigger()  # several Manual Starts: still asked which
    asked(qtbot, questions, 2)
    assert any(names(c, "load") for c in questions[1]["choices"]), questions[1]
    wait_runs(qtbot, two, 1)
    assert written(both.parent) == {"fetch.txt": "x"}  # only load's line


# --- 2. The + button by the last tab ------------------------------------------------------

def test_the_plus_button_right_of_the_last_tab_opens_a_new_flow_and_follows_the_last_tab(win, qtbot, tmp_path):
    for dock in _side_docks(win):  # room for the tabs: the Windows runner's offscreen canvas can be narrow
        dock.hide()
    first = win.open_flow(write_blocks(tmp_path / "a.yaml", {"manual": MANUAL}, []))
    qtbot.waitUntil(lambda: check_plus_after_last_tab(win), timeout=3_000)

    QTest.mouseClick(plus_button(win), Qt.LeftButton)

    assert win.tabs.count() == 2 and win.current is not first
    assert win.current.doc.path is None  # a new flow, as File > New: unsaved, with a Manual Start
    assert [spec["type"] for spec in win.current.doc.blocks.values()] == ["trigger.manual"]
    qtbot.waitUntil(lambda: check_plus_after_last_tab(win), timeout=3_000)

    opened = win.open_flow(write_blocks(tmp_path / "longer name.yaml", {"manual": MANUAL}, []))  # added
    qtbot.waitUntil(lambda: check_plus_after_last_tab(win), timeout=3_000)

    win.tabs.tabBar().moveTab(win.tabs.indexOf(opened), 0)  # moved
    qtbot.waitUntil(lambda: check_plus_after_last_tab(win), timeout=3_000)

    bar = win.tabs.tabBar()
    index = win.tabs.indexOf(opened)
    close = bar.tabButton(index, QTabBar.RightSide) or bar.tabButton(index, QTabBar.LeftSide)
    QTest.mouseClick(close, Qt.LeftButton)  # closed
    assert win.tabs.count() == 2 and opened not in win.tabs_list()
    qtbot.waitUntil(lambda: check_plus_after_last_tab(win), timeout=3_000)


def test_the_plus_button_stays_clear_of_the_tabs_when_they_do_not_all_fit(win, qtbot, tmp_path):
    """The editor as it opens (its side docks shown): a few flows already make the tabs scroll."""
    bar = win.tabs.tabBar()
    for n in range(30):
        win.open_flow(write_blocks(tmp_path / f"flow {n}.yaml", {"manual": MANUAL}, []))
        QApplication.processEvents()
        if tabs_scroll(bar):
            break
    assert tabs_scroll(bar), f"{bar.count()} tabs still fit in {bar.width()} px"
    qtbot.waitUntil(lambda: check_plus_clear_of_the_tabs(win), timeout=3_000)

    size = win.size()
    win.resize(win.width() * 2 // 3, win.height())  # narrower
    qtbot.waitUntil(lambda: check_plus_clear_of_the_tabs(win), timeout=3_000)
    win.resize(size)  # and back
    qtbot.waitUntil(lambda: check_plus_clear_of_the_tabs(win), timeout=3_000)

    count = win.tabs.count()
    QTest.mouseClick(plus_button(win), Qt.LeftButton)
    assert win.tabs.count() == count + 1 and win.current.doc.path is None  # a new flow, as File > New
    qtbot.waitUntil(lambda: check_plus_clear_of_the_tabs(win), timeout=3_000)


# --- 3. Unsaved changes to a scheduled flow --------------------------------------------------

def test_a_scheduled_flow_with_unsaved_changes_says_the_scheduler_runs_the_saved_version(win, qtbot, tmp_path):
    """The user's case: in a scheduled flow, a Schedule block's Active box unticked and not saved yet."""
    flow = write_blocks(tmp_path / "nightly.yaml", {
        "nightly": {"type": "trigger.schedule", "config": {"cron": "0 2 * * *"}}, "night": writes("night.txt"),
        "weekly": {"type": "trigger.schedule", "config": {"cron": "0 3 * * 1"}}, "week": writes("week.txt")},
        ["nightly.start -> night.after", "weekly.start -> week.after"])
    tab = win.open_flow(flow)
    menu_item(win, "File", "Save").trigger()  # saved with Schedule blocks: scheduled
    assert win.home.scheduled_flows() == [flow.resolve()]
    schedules = ("nightly", "weekly")
    qtbot.waitUntil(lambda: all(tab.scene.nodes[b].countdown for b in schedules), timeout=5_000)
    before = {b: tab.scene.nodes[b].countdown for b in schedules}
    tip_before = win.tabs.tabToolTip(win.tabs.indexOf(tab))
    assert not any(SAVED_VERSION.search(t) for t in [*before.values(), tip_before]), (before, tip_before)

    tab.scene.nodes["nightly"].setSelected(True)
    label = next(w for w in win.properties.findChildren(QLabel) if w.text() == "Active")
    active = next(b for b in win.properties.findChildren(QCheckBox) if b.toolTip() == label.toolTip())
    assert active.isChecked()
    active.click()  # unticked; not saved
    try:
        assert tab.doc.dirty and tab.doc.blocks["nightly"]["config"]["active"] is False

        def unsaved_said():
            texts = {b: tab.scene.nodes[b].countdown for b in schedules}
            tip = win.tabs.tabToolTip(win.tabs.indexOf(tab))
            assert all(SAVED_VERSION.search(t) for t in texts.values()), texts  # under each of its Schedule blocks
            assert SAVED_VERSION.search(tip) and "scheduler" in tip.lower(), tip
        qtbot.waitUntil(unsaved_said, timeout=5_000)

        menu_item(win, "File", "Save").trigger()

        def back_to_normal():
            texts = {b: tab.scene.nodes[b].countdown for b in schedules}
            assert not any(SAVED_VERSION.search(t) for t in texts.values()), texts
            assert texts["weekly"] == before["weekly"], texts
            assert win.tabs.tabToolTip(win.tabs.indexOf(tab)) == tip_before
        qtbot.waitUntil(back_to_normal, timeout=5_000)
        assert win.home.scheduled_flows() == [flow.resolve()]  # (still scheduled: weekly is active)
    finally:
        tab.doc.dirty = False  # (closing the editor at the end would ask to save it)


def test_a_new_flow_not_scheduled_yet_does_not_say_the_scheduler_runs_a_saved_version(win, qtbot):
    tab = win.new_flow()
    block = tab.doc.add_block("trigger.schedule", 0, 150, {"cron": "0 2 * * *"})  # unsaved, so not scheduled
    try:
        qtbot.waitUntil(lambda: bool(tab.scene.nodes[block].countdown), timeout=5_000)
        assert not SAVED_VERSION.search(tab.scene.nodes[block].countdown), tab.scene.nodes[block].countdown
        assert not SAVED_VERSION.search(win.tabs.tabToolTip(win.tabs.indexOf(tab)))
    finally:
        tab.doc.dirty = False


# --- 4. A saved flow is named by its file, never 'untitled' --------------------------------------

def test_a_new_flow_saved_as_report_runs_listed_as_report_not_untitled(win, qtbot, tmp_path, monkeypatch):
    """The user's case: File > New, Run now saves it as report.yaml; the flows running list says report."""
    flow = tmp_path / "report.yaml"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(flow), "")))
    menu_item(win, "File", "New flow").trigger()
    tab = win.current
    work = tab.doc.add_block("logic.python", 0, 0, gated("go.txt")["config"])
    tab.doc.connect("start", "start", work, "after")

    win.run_action.trigger()  # where to save it: report.yaml; it runs (and waits for go.txt)
    try:
        assert eventually(qtbot, lambda: win.running_flows.text() == "1 flow running", 30_000), \
            win.running_flows.text()
        (item,) = win.running_flows.menu().actions()
        assert item.text().startswith("report") and not item.text().startswith("report.y"), item.text()
        assert "untitled" not in item.text(), item.text()
    finally:
        (tmp_path / "go.txt").write_text("", encoding="utf-8")
        wait_done(qtbot, tab)
    assert load_flow(flow).name == "report"  # named after its file when saved the first time


def test_a_run_of_a_flow_whose_file_says_untitled_is_listed_by_its_file_name(win, qtbot, tmp_path):
    """A flow saved earlier with name: untitled, run from a terminal (or by the scheduler)."""
    flow = write_blocks(tmp_path / "load.yaml", {"manual": MANUAL, "work": gated("go.txt")},
                        ["manual.start -> work.after"], name="untitled")
    elsewhere = start_elsewhere(win, flow)
    try:
        assert eventually(qtbot, lambda: win.running_flows.text() == "1 flow running", 30_000), \
            win.running_flows.text()
        (item,) = win.running_flows.menu().actions()
        assert item.text().startswith("load") and "untitled" not in item.text(), item.text()
    finally:
        stop_elsewhere(elsewhere)


def test_save_as_names_a_flow_still_called_untitled_after_its_new_file(win, tmp_path, monkeypatch):
    old = write_blocks(tmp_path / "old.yaml", {"manual": MANUAL}, [], name="untitled")
    flow = tmp_path / "nightly.yaml"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(flow), "")))
    win.open_flow(old)

    menu_item(win, "File", "Save as…").trigger()

    assert win.current.doc.path == flow.resolve() and load_flow(flow).name == "nightly"


def test_saving_keeps_a_name_you_gave_the_flow(win, tmp_path, monkeypatch):
    chosen = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(chosen[-1]), "")))
    menu_item(win, "File", "New flow").trigger()
    win.right_tabs.setCurrentWidget(win.flow_settings)
    win.flow_settings.name.setText("Weekly numbers")
    win.flow_settings.name.editingFinished.emit()  # Flow > Name

    for path in (tmp_path / "first.yaml", tmp_path / "second.yaml"):  # saved the first time, then Save as
        chosen.append(path)
        menu_item(win, "File", "Save" if path.name == "first.yaml" else "Save as…").trigger()
        assert win.current.doc.path == path.resolve() and load_flow(path).name == "Weekly numbers"
