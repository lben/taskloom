"""The editor ends cleanly after its dialogs were used: closed after Tools > Settings, Tools > Connections and a Run now
that asks a question (Run now's question, the Run dialog, the Ask User box), its process ends with exit code 0 and no
fatal error. A dialog left alive once answered was freed at a random moment of the exit, which crashed it (on Linux
nearly every time; on Windows now and then, with "Fatal Python error" and a process that did not end).

Through the real entry point (`taskloom editor`, as the taskloom command runs it), in a process of its own."""

import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from taskloom.editor import dialogs
from taskloom.launch import ARGS_VARIABLE
from tests.test_dialog_speed import run_to_files

FLOW = """\
params:
  day: {type: date, expr: "today()"}
blocks:
  start: {type: trigger.manual}
  check: {type: logic.ask_user, config: {prompt: "Go on with the nightly load?", kind: yes_no}}
edges: ["start.start -> check.after"]
"""

SCRIPT = r'''
import sys, time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from taskloom import cli

seen = []


def answer():  # each dialog as it opens: Run now's question and the Run dialog go on, every other one is cancelled
    box = QApplication.activeModalWidget() or QApplication.activeWindow()  # (the Ask User box is not modal)
    if isinstance(box, QMessageBox):
        seen.append("QMessageBox")  # (its title is not kept on macOS)
        box.button(QMessageBox.Yes).click()
    elif type(box).__name__ == "RunDialog":
        seen.append("RunDialog")
        box.accept()
    elif isinstance(box, QDialog):
        seen.append(type(box).__name__)
        box.reject()
    QTimer.singleShot(50, answer)


def item(window, menu, text):
    titles = {a.text().replace("&", ""): a.menu() for a in window.menuBar().actions() if a.menu()}
    return next(a for a in titles[menu].actions() if a.text().startswith(text))


def steps():
    window = next(w for w in QApplication.topLevelWidgets() if w.inherits("QMainWindow") and w.isVisible())
    tab = window.current
    item(window, "Tools", "Settings").trigger()
    item(window, "Tools", "Connections").trigger()
    item(window, "Run", "▶ Run now").trigger()
    deadline = time.monotonic() + 60
    while ("AskDialog" not in seen or tab.running) and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    print("seen", *seen, sep="\n", flush=True)
    window.close()


app = QApplication(sys.argv[:1])  # (the editor uses it)
QTimer.singleShot(0, answer)
QTimer.singleShot(1000, steps)
sys.argv = ["taskloom", "editor", sys.argv[1]]
cli.run_cli()  # ends the process, as the taskloom command does
'''


def test_the_editor_ends_cleanly_after_its_dialogs_were_used(tmp_path):
    """Settings and Connections opened and cancelled, a run started with Run now (its question, the Run dialog) whose
    Ask User box is closed without an answer; the window closed: exit code 0, nothing fatal, no uncaught error."""
    (tmp_path / "editor_steps.py").write_text(SCRIPT, encoding="utf-8")
    (tmp_path / "nightly.yaml").write_text(FLOW, encoding="utf-8")
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "TASKLOOM_HOME": str(tmp_path / "home")}

    done = run_to_files([sys.executable, str(tmp_path / "editor_steps.py"), str(tmp_path / "nightly.yaml")], tmp_path,
                        environment, 120)

    said = f"stdout:\n{done.stdout[-3000:]}\nstderr:\n{done.stderr[-3000:]}"
    shown = done.stdout.partition("seen\n")[2].splitlines()
    assert {"SettingsDialog", "ConnectionsDialog", "QMessageBox", "RunDialog", "AskDialog"} <= set(shown), said
    assert done.returncode == 0, said
    assert "Fatal Python error" not in done.stderr and "Traceback" not in done.stderr, said


# --- a dialog's own program is not stopped with it -------------------------------------------------------------------

def slow_runner(monkeypatch, done_file):
    """The taskloom program the dialogs start, made a slow stand-in: after 1.5 s it adds its arguments to `done_file`
    (a line once it has run to its end) and says it is done."""
    program = (f"import json, os, pathlib, time; time.sleep(1.5); "
               f"pathlib.Path({str(done_file)!r}).open('a', encoding='utf-8').write("
               f"' '.join(json.loads(os.environ[{ARGS_VARIABLE!r}])) + '\\n'); print('reachable')")
    monkeypatch.setattr(dialogs, "runner_command", lambda: [sys.executable, "-c", program])


def menu_item(win, menu: str, starts_with: str):
    """The item of the menu bar's `menu` whose text starts with `starts_with`."""
    titles = {a.text().replace("&", ""): a.menu() for a in win.menuBar().actions() if a.menu()}
    return next(a for a in titles[menu].actions() if a.text().startswith(starts_with))


def in_dialog(qtbot, then):
    """When the dialog the next menu item opens is shown, `then(dialog)`."""
    def look():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            QTimer.singleShot(20, look)
        else:
            then(dialog)
    QTimer.singleShot(0, look)


def test_connections_test_then_save_before_it_ends_lets_the_test_run_to_its_end(win, qtbot, tmp_path, monkeypatch):
    """Tools > Connections, Save and test connection, then Save before the test has ended: the test runs to its end
    (it is not stopped with the dialog), with no error."""
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "connections.yaml").write_text("box: {kind: ssh, host: localhost, port: 22, username: me}\n",
                                                   encoding="utf-8")
    done_file = tmp_path / "done.txt"
    slow_runner(monkeypatch, done_file)

    def test_then_save(dialog):
        assert dialog.current == "box"
        dialog.test_button.click()
        QTimer.singleShot(300, dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click)

    in_dialog(qtbot, test_then_save)
    menu_item(win, "Tools", "Connections").trigger()

    qtbot.waitUntil(done_file.exists, timeout=15_000)
    qtbot.wait(500)  # (what the dialog does with the result, after it has ended)
    assert done_file.read_text(encoding="utf-8").splitlines() == ["connection test box"]


def test_python_environment_closed_while_creating_lets_it_and_its_package_list_run_to_their_end(win, qtbot, tmp_path,
                                                                                              monkeypatch):
    """Tools > Python environment, Create environment, then Close before it has ended: creating it runs to its end, and
    so does the list of its packages that follows it (it is not stopped with the dialog)."""
    done_file = tmp_path / "done.txt"
    slow_runner(monkeypatch, done_file)

    def create_then_close(dialog):
        dialog.python.setText(sys.executable)
        dialog.create_button.click()
        QTimer.singleShot(300, dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Close).click)

    in_dialog(qtbot, create_then_close)
    menu_item(win, "Tools", "Python environment").trigger()

    qtbot.waitUntil(lambda: done_file.exists() and len(done_file.read_text(encoding="utf-8").splitlines()) == 2,
                    timeout=15_000)
    assert done_file.read_text(encoding="utf-8").splitlines() == [f"env create --python {sys.executable}",
                                                                  "env pip list"]
