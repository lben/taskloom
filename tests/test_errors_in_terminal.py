"""An error the editor does not catch is printed in the terminal the editor was started from, with the same text as
crash.log, and the box still appears; started without a terminal, nothing breaks
(docs/promises/editor-fixes.yaml, rows ERR-1 to ERR-3).

The real editor is started in a process of its own, as `taskloom editor` starts it (taskloom.editor.window.main),
offscreen and with a Taskloom folder of its own, so that what it prints arrives through a real pipe. In it a button's
code raises an error, a background thread raises one and Qt reports a critical message; the box is recorded instead of
shown (it would wait for a click)."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

EDITOR = r'''
import json, os, sys, threading
from pathlib import Path

results, terminal = Path(sys.argv[1]), sys.argv[2] == "terminal"
if not terminal:  # as pythonw.exe starts it from a shortcut: Python has no stdout and no stderr at all
    sys.stdout = sys.__stdout__ = sys.stderr = sys.__stderr__ = None

from PySide6.QtCore import QTimer, qCritical
from PySide6.QtWidgets import QApplication, QMessageBox
from taskloom.editor import window

app = QApplication(sys.argv[:1])
boxes = []
QMessageBox.warning = staticmethod(lambda parent, title, text, *a, **k: boxes.append([title, text]))
window.MainWindow.start_scheduler = lambda self, by_you=False: None  # never a real scheduler service


def broken_slot():
    raise ValueError("a bug in a button")


def broken_thread():
    raise RuntimeError("a bug in a thread")


def in_thread():
    thread = threading.Thread(target=broken_thread, name="load_orders")
    thread.start()
    thread.join()


def finish():  # still running after the three: say which boxes appeared, and quit as File > Quit does
    results.write_text(json.dumps({"boxes": boxes}), encoding="utf-8")
    app.quit()


# Once the editor is up (main's event loop runs these in this order):
QTimer.singleShot(0, broken_slot)
QTimer.singleShot(100, in_thread)
QTimer.singleShot(200, lambda: qCritical("a critical message from Qt"))
QTimer.singleShot(1000, finish)
sys.exit(window.main())
'''
SLOT_ERROR = ("uncaught error:", "ValueError: a bug in a button")
THREAD_ERROR = ("uncaught error in thread load_orders:", "RuntimeError: a bug in a thread")
QT_MESSAGE = "Qt: a critical message from Qt"


def start_editor(folder, mode: str, terminal_closed: bool = False) -> SimpleNamespace:
    """Starts the editor above with ("terminal") or without ("no terminal") a terminal and waits until it has quit.
    Gives what it printed (stdout, stderr), its exit code, its crash.log and the boxes it showed (None if it never
    got as far as telling). With `terminal_closed`, what it prints as errors goes into a pipe whose reading end is
    closed, as into a terminal closed since the editor started: printing there fails, and nothing arrives."""
    script, results, home = folder / "editor.py", folder / "results.json", folder / "home"
    script.write_text(EDITOR, encoding="utf-8")
    env = {**os.environ, "TASKLOOM_HOME": str(home), "QT_QPA_PLATFORM": "offscreen",
           "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    errors = subprocess.PIPE
    if terminal_closed:
        reading, errors = os.pipe()
        os.close(reading)
    try:
        proc = subprocess.run([sys.executable, str(script), str(results), mode], env=env, stdout=subprocess.PIPE,
                              stderr=errors, text=True, timeout=180)
    finally:
        if terminal_closed:
            os.close(errors)
    log = home / "crash.log"
    return SimpleNamespace(
        stdout=proc.stdout, stderr=proc.stderr or "", code=proc.returncode,
        log=log.read_text(encoding="utf-8") if log.exists() else "",
        boxes=json.loads(results.read_text(encoding="utf-8"))["boxes"] if results.exists() else None)


@pytest.fixture(scope="module")
def in_terminal(tmp_path_factory):
    return start_editor(tmp_path_factory.mktemp("terminal"), "terminal")


@pytest.fixture(scope="module")
def no_terminal(tmp_path_factory):
    return start_editor(tmp_path_factory.mktemp("shortcut"), "no terminal")


@pytest.fixture(scope="module")
def terminal_closed(tmp_path_factory):
    return start_editor(tmp_path_factory.mktemp("closed"), "terminal", terminal_closed=True)


def logged(log: str, heading: str, last_line: str) -> str:
    """The error as crash.log holds it: from its heading to the error's own line, the traceback between them."""
    assert heading + "\n" in log and last_line in log, f"crash.log does not hold {heading!r} … {last_line!r}:\n{log}"
    start = log.index(heading + "\n")
    text = log[start:log.index(last_line, start) + len(last_line)]
    assert text.startswith(heading + "\nTraceback (most recent call last):\n"), text
    return text


def boxes_with(editor, last_line: str) -> list:
    assert editor.boxes is not None, f"the editor did not keep running to its end:\n{editor.stderr}\n{editor.log}"
    return [title for title, text in editor.boxes if last_line in text]


# --- ERR-1: an error raised in the editor's own code (a button, a menu) -----------------------------------------------

@pytest.mark.invariant("ERR-1")
def test_an_error_the_editor_does_not_catch_is_printed_in_the_terminal_with_the_same_text_as_crash_log(in_terminal):
    error = logged(in_terminal.log, *SLOT_ERROR)

    assert "in broken_slot" in error and error.endswith("ValueError: a bug in a button")
    assert error in in_terminal.stderr, f"the terminal got:\n{in_terminal.stderr!r}\ncrash.log holds:\n{error}"


@pytest.mark.parametrize("last_line", [pytest.param(SLOT_ERROR[1], marks=pytest.mark.invariant("ERR-1"), id="button"),
                                       pytest.param(THREAD_ERROR[1], marks=pytest.mark.invariant("ERR-2"),
                                                    id="background thread")])
def test_the_box_still_appears_with_the_error_and_the_editor_keeps_running(in_terminal, last_line):
    assert boxes_with(in_terminal, last_line) == ["Something went wrong"], in_terminal.boxes
    assert in_terminal.code == 0, in_terminal.stderr


# --- ERR-2: a background thread, and Qt's own critical messages -------------------------------------------------------

@pytest.mark.invariant("ERR-2")
def test_an_error_in_a_background_thread_is_printed_in_the_terminal_with_the_same_text_as_crash_log(in_terminal):
    error = logged(in_terminal.log, *THREAD_ERROR)

    assert "in broken_thread" in error and error.endswith("RuntimeError: a bug in a thread")
    assert error in in_terminal.stderr, f"the terminal got:\n{in_terminal.stderr!r}\ncrash.log holds:\n{error}"


@pytest.mark.invariant("ERR-2")
def test_a_critical_message_of_qt_is_printed_in_the_terminal_with_the_same_text_as_crash_log(in_terminal):
    assert QT_MESSAGE + "\n" in in_terminal.log, in_terminal.log
    assert QT_MESSAGE + "\n" in in_terminal.stderr, f"the terminal got:\n{in_terminal.stderr!r}"


# --- ERR-3: no terminal -----------------------------------------------------------------------------------------------

@pytest.mark.invariant("ERR-3")
def test_started_without_a_terminal_nothing_breaks_crash_log_has_each_error_and_the_boxes_appear(no_terminal):
    assert boxes_with(no_terminal, SLOT_ERROR[1]) == ["Something went wrong"], no_terminal.boxes
    assert boxes_with(no_terminal, THREAD_ERROR[1]) == ["Something went wrong"], no_terminal.boxes
    assert no_terminal.code == 0, (no_terminal.stderr, no_terminal.log)
    assert "in broken_slot" in logged(no_terminal.log, *SLOT_ERROR)
    assert "in broken_thread" in logged(no_terminal.log, *THREAD_ERROR)
    assert QT_MESSAGE + "\n" in no_terminal.log, no_terminal.log


# --- ERR-1, ERR-3: the terminal was closed after the editor started ---------------------------------------------------

@pytest.mark.invariant("ERR-1")
@pytest.mark.invariant("ERR-3")
def test_with_the_terminal_closed_since_crash_log_has_the_error_the_box_still_appears_and_the_editor_keeps_running(
        terminal_closed):
    assert "in broken_slot" in logged(terminal_closed.log, *SLOT_ERROR)
    assert boxes_with(terminal_closed, SLOT_ERROR[1]) == ["Something went wrong"], terminal_closed.boxes
    # It kept running: the error after it (in a background thread) is in crash.log and in a box too.
    assert "in broken_thread" in logged(terminal_closed.log, *THREAD_ERROR)
    assert boxes_with(terminal_closed, THREAD_ERROR[1]) == ["Something went wrong"], terminal_closed.boxes
