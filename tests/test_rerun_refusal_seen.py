"""What a re-run says is on screen: two things the proof pictures of docs/promises/reruns.yaml showed (the flows and
the ways of driving the editor are those of tests/test_rerun_carries_on.py).

RR-8, RR-6: a block run again that is refused says why in the Log whichever block is selected. A click on a block
makes the Log show that block's lines only, and a right-click leaves the selection as it is: the refusal has to be
among the lines on screen all the same, without pressing Show all. Here a block is clicked and right-clicked on the
canvas itself, with the mouse, and the entry is chosen in the menu that opens.

RR-4: the window of "Run only this block, with inputs I choose…" names the run it is filled in from in its
introduction. When the window opens, the whole introduction is on screen: each label that says it is as tall as its
text needs at the label's width, inside the window. The window is opened from the block's menu, shown and laid out
as on a screen, looked at, then closed with Cancel.
"""
import os
from typing import NamedTuple

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from taskloom.editor import dialogs  # noqa: E402
from tests.test_rerun_carries_on import (CHOOSE_ONLY, OLD_BETWEEN, RUN_ONLY, Step, branches_flow, chain_flow,  # noqa: E402
                                         choose, do, full_run, inputs_window, load_orders_alone, log_text,
                                         orders_flow, plain, received, run_now)

BEFORE_IT_FAILED = "clean would not run: load_orders, before it, failed"  # RR-6's refusal
SELECTED = pytest.mark.parametrize("clicked", ["refused", "load_orders", None],
                                   ids=["the_refused_block_selected", "another_block_selected", "no_block_selected"])


# --- the mouse on the canvas ----------------------------------------------------------------------------------------------

def point_of(tab, block: str) -> QPoint:
    """The middle of `block`, in the canvas as it is scrolled."""
    tab.view.ensureVisible(tab.scene.nodes[block])
    return tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center())


def select(win, qtbot, tab, block: str | None) -> None:
    """Click `block` on the canvas (None: no click, no block is selected): the Log then says whose lines it shows."""
    qtbot.waitExposed(win)
    if block is not None:
        QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, point_of(tab, block))
        QApplication.processEvents()
    assert plain(win.log.showing.text()) == (f"Showing {block}" if block else "All blocks")


def right_click_and_choose(win, qtbot, tab, block: str, words: str) -> Step:
    """Right-click `block` on the canvas itself (the right button pressed and released on it, then the menu it opens:
    whatever a right-click does to the selection happens) and click the menu's entry that starts with `words`."""
    viewport, at = tab.view.viewport(), point_of(tab, block)
    entries = []

    def in_the_menu():
        menu = QApplication.activePopupWidget()
        if menu is None or entries:
            return
        timer.stop()
        entries.extend(a.text() for a in menu.actions())
        found = [a for a in menu.actions() if a.text().startswith(words) and a.isEnabled()]
        if len(found) == 1:
            QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(found[0]).center())
        if menu.isVisible():  # nothing chosen: the right-click would wait for a choice forever
            menu.close()

    def right_click():
        QTest.mousePress(viewport, Qt.RightButton, Qt.NoModifier, at)
        QTest.mouseRelease(viewport, Qt.RightButton, Qt.NoModifier, at)
        timer.start()
        try:  # the menu waits for a choice: made in_the_menu
            QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, viewport.mapToGlobal(at)))
        finally:
            timer.stop()

    timer = QTimer(interval=20)
    timer.timeout.connect(in_the_menu)
    step = do(win, qtbot, tab, right_click)
    assert [text for text in entries if text.startswith(words)], f"no entry {words!r} in {block}'s menu: {entries}"
    return step


def the_log(win) -> tuple:
    """(Whose lines the Log shows, its lines as they are on screen, all its lines: for these, Show all is pressed
    when the Log shows one block's lines.)"""
    showing, on_screen = plain(win.log.showing.text()), log_text(win)
    if not win.log.clear_filter.isHidden():
        win.log.clear_filter.click()
        QApplication.processEvents()
    return showing, on_screen, log_text(win)


# --- RR-8, RR-6: a refusal is on screen in the Log whichever block is selected ---------------------------------------------

@pytest.mark.invariant("RR-8")
@SELECTED
def test_a_block_refused_past_an_old_one_says_so_in_the_log_whichever_block_is_selected(
        win, qtbot, tmp_path, clicked):
    tab, full = full_run(win, qtbot, tmp_path, rows=10)
    alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=12)
    assert (full.status, alone.status) == ("success", "success"), alone.log
    select(win, qtbot, tab, "report" if clicked == "refused" else clicked)

    step = right_click_and_choose(win, qtbot, tab, "report", RUN_ONLY)  # without re-running clean

    showing, on_screen, whole = the_log(win)
    assert step.status != "success" and received(tmp_path, "report") == ["10"]  # it did not run
    assert OLD_BETWEEN.format("clean") in whole  # the Log holds why
    assert OLD_BETWEEN.format("clean") in on_screen, f"the Log ({showing}) ends with {on_screen.splitlines()[-2:]}"


@pytest.mark.invariant("RR-6")
@SELECTED
def test_a_block_refused_after_a_rerun_that_failed_again_says_so_in_the_log_whichever_block_is_selected(
        win, qtbot, tmp_path, clicked):
    tab, full = full_run(win, qtbot, tmp_path, rows=None)
    again = load_orders_alone(win, qtbot, tab, tmp_path, rows=None)
    assert (full.status, again.status) == ("failed", "failed") and again.run is not None, again.log
    select(win, qtbot, tab, "clean" if clicked == "refused" else clicked)

    step = right_click_and_choose(win, qtbot, tab, "clean", RUN_ONLY)

    showing, on_screen, whole = the_log(win)
    assert step.status != "success" and received(tmp_path, "clean") == []  # it did not run
    assert BEFORE_IT_FAILED in whole  # the Log holds why
    assert BEFORE_IT_FAILED in on_screen, f"the Log ({showing}) ends with {on_screen.splitlines()[-2:]}"


# --- RR-4: the window of "inputs I choose" opens with its whole introduction on screen ----------------------------------

INTRO = "Write each value as Python"  # how the introduction starts
LEAVE_EMPTY = "Leave a field empty for no value."
FILLED_IN = "Filled in: what reached the block in run #{0} · "  # then the run's date and time
TYPED_LAST_TIME = "What you typed last time is shown as you typed it"


class Opened(NamedTuple):
    fields: list  # the inputs it asks for
    labels: list  # (what a label says, why it is not wholly on screen: "" when it is)

    @property
    def says(self) -> str:
        return " ".join(text for text, _ in self.labels)


def not_whole(dialog, label: QLabel) -> str:
    """Why a label of the open window is not wholly on screen ("" when it is)."""
    needs = label.heightForWidth(label.width()) if label.wordWrap() else label.sizeHint().height()
    if not label.isVisible():
        return "it is hidden"
    if not dialog.rect().contains(QRect(label.mapTo(dialog, QPoint(0, 0)), label.size())):
        return "it reaches outside the window"
    if label.height() < needs:
        return f"it is {label.height()} pixels high where its text needs {needs} at its width of {label.width()}"
    if not label.wordWrap() and label.width() < label.sizeHint().width():
        return f"it is {label.width()} pixels wide where its text needs {label.sizeHint().width()}"
    return ""


def looked_at(monkeypatch, qtbot) -> list:
    """Answer the "inputs I choose" window: each time it opens it is shown and laid out as on a screen, how it shows
    its labels is noted (an Opened), then Cancel is pressed."""
    windows = []

    def exec_(dialog):
        dialog.show()
        qtbot.waitExposed(dialog)
        QApplication.processEvents()
        windows.append(Opened(list(dialog.fields), [(plain(label.text()), not_whole(dialog, label))
                                                    for label in dialog.findChildren(QLabel)]))
        dialog.close()
        return False

    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", exec_)
    return windows


def cut_off(window: Opened, *sentences: str) -> list:
    """What is wrong with how the window shows `sentences` (nothing: each is said, by a label wholly on screen)."""
    wrong = []
    for sentence in sentences:
        said = [why for text, why in window.labels if sentence in text]
        wrong += [f"{sentence!r}: {why}" for why in said if why] or ([] if said else [f"{sentence!r} is not said"])
    return wrong


@pytest.mark.invariant("RR-4")
def test_inputs_i_choose_opens_with_the_whole_sentence_that_names_the_run_it_is_filled_in_from_on_screen(
        win, qtbot, tmp_path, monkeypatch):
    tab, full = full_run(win, qtbot, tmp_path, rows=10)
    alone = load_orders_alone(win, qtbot, tab, tmp_path, rows=12)
    assert (full.status, alone.status) == ("success", "success"), alone.log
    windows = looked_at(monkeypatch, qtbot)

    choose(win, qtbot, tab, "clean", CHOOSE_ONLY)

    (window,) = windows
    assert window.fields == ["orders"]
    assert cut_off(window, INTRO, LEAVE_EMPTY, FILLED_IN.format(alone.run)) == []


@pytest.mark.invariant("RR-4")
def test_inputs_i_choose_for_a_block_with_several_inputs_opens_with_its_whole_introduction_on_screen(
        win, qtbot, tmp_path, monkeypatch):
    tab = win.open_flow(branches_flow(tmp_path))
    full = run_now(win, qtbot, tab)
    assert full.status == "success", full.log
    windows = looked_at(monkeypatch, qtbot)

    choose(win, qtbot, tab, "report", CHOOSE_ONLY)

    (window,) = windows
    assert window.fields == ["rows", "prices"]
    assert cut_off(window, INTRO, LEAVE_EMPTY, FILLED_IN.format(full.run)) == []


@pytest.mark.invariant("RR-4")
def test_inputs_i_choose_with_no_run_to_fill_it_in_from_opens_with_its_whole_introduction_on_screen(
        win, qtbot, tmp_path, monkeypatch):
    tab = win.open_flow(orders_flow(tmp_path))  # never run
    windows = looked_at(monkeypatch, qtbot)

    choose(win, qtbot, tab, "clean", CHOOSE_ONLY)

    (window,) = windows
    assert window.fields == ["orders"] and "Filled in" not in window.says, window.says
    assert cut_off(window, INTRO, LEAVE_EMPTY) == []


@pytest.mark.invariant("RR-4")
def test_inputs_i_choose_opened_again_shows_whole_what_it_says_of_what_you_typed_last_time(
        win, qtbot, tmp_path, monkeypatch):
    tab = win.open_flow(chain_flow(tmp_path))
    assert run_now(win, qtbot, tab).status == "success"
    inputs_window(monkeypatch, typed={"input": "'typed'"})
    typed = choose(win, qtbot, tab, "clean", CHOOSE_ONLY)
    assert typed.status == "success" and received(tmp_path, "clean")[1:] == ["typed"], typed.log
    windows = looked_at(monkeypatch, qtbot)

    choose(win, qtbot, tab, "clean", CHOOSE_ONLY)

    (window,) = windows
    assert window.fields == ["input"]
    assert cut_off(window, INTRO, LEAVE_EMPTY, FILLED_IN.format(typed.run), TYPED_LAST_TIME) == []
