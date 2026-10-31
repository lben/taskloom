"""Ask User's question box in the editor. Kind text (and number, once it exists): the box starts empty with the
Default as grey placeholder text and a note under it that leaving it empty uses the default; OK or Enter with the
box empty answers the Default, and the Log says so (one line); typing replaces it; no Default: an empty OK answers
"". Kind choice: the Default is selected and Enter answers it. Kind yes_no: the Default is the button Enter presses
(Yes without one). Kind password: unchanged. When the run stops waiting (Stop, the block's Timeout, the run ending),
the box closes by itself, the Log says why, and nothing typed into it is used. "Don't answer" still fails cleanly.
Through the real editor and the real run process."""

import json
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit  # noqa: E402
from shiboken6 import isValid  # noqa: E402

from taskloom.blocks.logic import AskUser  # noqa: E402
from taskloom.editor.dialogs import AskDialog  # noqa: E402
from tests.test_lines import wait_runs  # noqa: E402
from tests.test_paths_editor_running import click, visible_ask  # noqa: E402

PROMPT = "Which region?"
CLOSES_WITHIN_MS = 20_000  # the run stops waiting within a second or so; generous for a slow CI runner
NUMBER = pytest.param("number", "3", marks=pytest.mark.skipif(
    "number" not in AskUser.config["kind"].options, reason="Ask User has no number kind (a separate change)"),
    id="number")


def ask(win, qtbot, tmp_path, config: dict, **region):
    """Run, from the editor, a Manual Start -> Ask User `region` (its config; `region`: e.g. its timeout) -> a
    Python Code block that writes the answer it gets to answer.txt; the run's tab and its question box on screen."""
    flow = {
        "blocks": {
            "start": {"type": "trigger.manual"},
            "region": {"type": "logic.ask_user", "config": {"prompt": PROMPT, **config}, **region},
            "save": {"type": "logic.python", "config": {
                "inputs": ["answer"], "outputs": [],
                "code": "open('answer.txt', 'w', encoding='utf-8').write(repr(answer))"}},
        },
        "edges": ["start.start -> region.after", "region.answer -> save.answer"],
    }
    path = tmp_path / "region.yaml"
    path.write_text(json.dumps(flow, indent=2), encoding="utf-8")  # (YAML reads JSON)
    tab = win.open_flow(path)
    win.run_flow(tab)
    dialog = visible_ask(qtbot, win)
    QApplication.processEvents()  # laid out and focused, as you see it
    return tab, dialog


def answered(tmp_path) -> str | None:
    """repr() of the answer the block after Ask User got; None if it did not run."""
    path = tmp_path / "answer.txt"
    return path.read_text(encoding="utf-8") if path.exists() else None


def answer_box(dialog) -> QLineEdit:
    (box,) = [e for e in dialog.findChildren(QLineEdit) if e.isVisibleTo(dialog)]
    return box


def notes_about_the_default(dialog) -> list[QLabel]:
    """The labels the box shows that speak of the default."""
    return [label for label in dialog.findChildren(QLabel)
            if label.isVisibleTo(dialog) and "default" in re.sub(r"<[^>]*>", "", label.text()).lower()]


def press_enter(dialog):
    """Enter, where you are in the box (what has the focus in it)."""
    QApplication.processEvents()
    QTest.keyClick(dialog.focusWidget() or dialog, Qt.Key_Return)


def log_lines(win) -> list[str]:
    return win.log.text.toPlainText().splitlines()


def saying_the_default_was_used(win) -> list[str]:
    return [line for line in log_lines(win) if "default" in line.lower()]


def saying_why_the_question_closed(win, why: str) -> list[str]:
    """Log lines about the question (it, its box or its prompt) that give the reason `why` (a pattern)."""
    return [line for line in log_lines(win)
            if (re.search(r"question|box", line, re.I) or PROMPT in line) and re.search(why, line, re.I)]


def closed(dialog) -> bool:
    return not isValid(dialog) or not dialog.isVisible()


# --- text (and number): the Default as a placeholder ------------------------------------------------------------


@pytest.mark.parametrize("how", ["OK", "Enter"])
@pytest.mark.parametrize("kind, default", [pytest.param("text", "North", id="text"), NUMBER])
def test_the_default_is_a_grey_placeholder_in_an_empty_box_and_an_empty_answer_uses_it(
        win, qtbot, tmp_path, kind, default, how):
    """Spec 1, the user's report: the box starts empty with the Default as placeholder text (Qt draws it grey) and a
    note under the box says leaving it empty uses the default; OK or Enter with the box empty answers the Default,
    and the Log says the default was used, in one line."""
    tab, dialog = ask(win, qtbot, tmp_path, {"kind": kind, "default": default})
    box = answer_box(dialog)
    text, placeholder, notes = box.text(), box.placeholderText(), notes_about_the_default(dialog)
    box_bottom = box.mapTo(dialog, QPoint(0, box.height() - 1)).y()
    under = [note for note in notes if note.mapTo(dialog, QPoint(0, 0)).y() > box_bottom]

    if how == "OK":  # (answered before the checks, so that the run ends whatever they find)
        click(dialog, "OK")
    else:
        press_enter(dialog)
    wait_runs(qtbot, tab, 1)

    assert text == "", "the Default is real text in the box, not a placeholder"
    assert default in placeholder, placeholder
    assert notes, "no note says that leaving the box empty uses the default"
    assert under, "the note is not under the box"
    assert tab.runs[-1].status == "success"
    got = answered(tmp_path)
    if kind == "text":
        assert got == repr(default)
    else:
        assert got is not None and float(got.strip("'\"")) == float(default), got
    assert len(saying_the_default_was_used(win)) == 1, log_lines(win)


def test_typing_replaces_the_default(win, qtbot, tmp_path):
    """Spec 1: what you type is the answer (the Default does not have to be deleted first), and the Log does not
    say a default was used."""
    tab, dialog = ask(win, qtbot, tmp_path, {"kind": "text", "default": "North"})

    QTest.keyClicks(answer_box(dialog), "South")
    click(dialog, "OK")
    wait_runs(qtbot, tab, 1)

    assert answered(tmp_path) == repr("South")
    assert saying_the_default_was_used(win) == [], log_lines(win)


def test_without_a_default_an_empty_ok_answers_empty_text(win, qtbot, tmp_path):
    """Spec 1: no Default, an empty OK answers "" as today; nothing speaks of a default."""
    tab, dialog = ask(win, qtbot, tmp_path, {"kind": "text"})
    text, notes = answer_box(dialog).text(), notes_about_the_default(dialog)

    click(dialog, "OK")
    wait_runs(qtbot, tab, 1)

    assert text == "" and notes == []
    assert answered(tmp_path) == repr("")
    assert saying_the_default_was_used(win) == [], log_lines(win)


# --- choice, yes_no, password -----------------------------------------------------------------------------------


def test_choice_opens_on_the_default_and_enter_answers_it(win, qtbot, tmp_path):
    """Spec 2: the Default option is selected when the box opens; Enter answers it."""
    tab, dialog = ask(win, qtbot, tmp_path, {"kind": "choice", "options": ["North", "South", "East"],
                                             "default": "South"})

    assert dialog.findChild(QComboBox).currentText() == "South"
    press_enter(dialog)
    wait_runs(qtbot, tab, 1)

    assert answered(tmp_path) == repr("South")


@pytest.mark.parametrize("default, expected", [("no", False), ("yes", True), (None, True)],
                         ids=["default-no", "default-yes", "no-default"])
def test_enter_presses_the_yes_no_default(win, qtbot, tmp_path, default, expected):
    """Spec 3: the Default (yes or no) is the button Enter presses; with no Default, Yes, as today."""
    config = {"kind": "yes_no"} if default is None else {"kind": "yes_no", "default": default}
    tab, dialog = ask(win, qtbot, tmp_path, config)

    press_enter(dialog)
    wait_runs(qtbot, tab, 1)

    assert answered(tmp_path) == repr(expected)


def test_the_password_box_stays_masked_and_shows_no_default(win, qtbot, tmp_path):
    """Spec 4: password is unchanged: a masked, empty box; the Default is not shown, nor a note about it."""
    tab, dialog = ask(win, qtbot, tmp_path, {"kind": "password", "default": "warehouse-pass"})
    box = answer_box(dialog)
    masked, text, placeholder = box.echoMode() == QLineEdit.Password, box.text(), box.placeholderText()
    labels, notes = [label.text() for label in dialog.findChildren(QLabel)], notes_about_the_default(dialog)

    click(dialog, "Don't answer")
    wait_runs(qtbot, tab, 1)

    assert masked and text == ""
    assert "warehouse-pass" not in placeholder
    assert all("warehouse-pass" not in label for label in labels), labels
    assert notes == []


# --- the box closes when the run no longer waits for it ---------------------------------------------------------


def test_stop_closes_the_question_and_the_log_says_why(win, qtbot, tmp_path):
    """Spec 5: Stop pressed while the question waits: the box closes by itself, the Log says why, and what was
    typed into it (not confirmed) is not used."""
    tab, dialog = ask(win, qtbot, tmp_path, {"kind": "text", "default": "North"})
    QTest.keyClicks(answer_box(dialog), "South")

    win.stop_action.trigger()
    qtbot.waitUntil(lambda: closed(dialog), timeout=CLOSES_WITHIN_MS)
    wait_runs(qtbot, tab, 1)

    assert tab.runs[-1].status == "cancelled"
    assert saying_why_the_question_closed(win, r"stop|cancel"), log_lines(win)
    assert answered(tmp_path) is None


def test_the_blocks_timeout_closes_the_question_and_its_retry_asks_in_a_new_box(win, qtbot, tmp_path):
    """Spec 5: the block's Timeout ends the wait: the box closes by itself and the Log says why; what was typed into
    it is not used. The retry asks again in a new box, which stays open and gives the answer."""
    tab, first = ask(win, qtbot, tmp_path, {"kind": "text"}, timeout="5s",
                     retry={"policy": "fixed", "delay": "100ms", "max_attempts": 2})
    QTest.keyClicks(answer_box(first), "North")

    qtbot.waitUntil(lambda: closed(first), timeout=CLOSES_WITHIN_MS)
    assert saying_why_the_question_closed(win, r"time"), log_lines(win)
    qtbot.waitUntil(lambda: any(d.isVisible() and d is not first for d in win.findChildren(AskDialog)),
                    timeout=CLOSES_WITHIN_MS)  # asked again
    second = visible_ask(qtbot, win)
    assert answer_box(second).text() == ""
    QTest.keyClicks(answer_box(second), "South")
    click(second, "OK")
    wait_runs(qtbot, tab, 1)

    assert tab.runs[-1].status == "success"
    assert answered(tmp_path) == repr("South")


def test_the_run_ending_while_the_question_waits_closes_it(win, qtbot, tmp_path):
    """Spec 5: the run ends while the question waits (its process stops unexpectedly): the box closes by itself and
    the Log says why."""
    tab, dialog = ask(win, qtbot, tmp_path, {"kind": "text", "default": "North"})

    tab.runs[-1].proc.kill()
    qtbot.waitUntil(lambda: closed(dialog), timeout=CLOSES_WITHIN_MS)
    wait_runs(qtbot, tab, 1)

    assert tab.runs[-1].status == "failed"
    assert saying_why_the_question_closed(win, r"end|stop|fail|finish|crash|exit|died"), log_lines(win)
    assert answered(tmp_path) is None


# --- "Don't answer" ---------------------------------------------------------------------------------------------


def test_dont_answer_with_a_default_still_fails_cleanly(win, qtbot, tmp_path):
    """Spec 6: "Don't answer" fails the block (no answer was given), even with a Default: the Default is not used."""
    tab, dialog = ask(win, qtbot, tmp_path, {"kind": "text", "default": "North"})

    click(dialog, "Don't answer")
    wait_runs(qtbot, tab, 1)

    node = tab.scene.nodes["region"]
    assert tab.runs[-1].status == "failed"
    assert node.status == "failed" and "no answer" in node.status_text.lower(), node.status_text
    assert answered(tmp_path) is None
    assert saying_the_default_was_used(win) == [], log_lines(win)
