"""The editor, driven through its widgets, with real runner processes."""

import os
import signal
import sys
import textwrap

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QFileDialog, QMessageBox, QToolButton  # noqa: E402

from taskloom.editor.dialogs import AskDialog, OverlapDialog, RunDialog, SettingsDialog  # noqa: E402
from taskloom.editor.document import FlowDocument  # noqa: E402
from taskloom.editor.panels import CodeEdit  # noqa: E402
from taskloom.editor.window import MainWindow  # noqa: E402
REAL_START_SCHEDULER = MainWindow.start_scheduler
from taskloom.history import History  # noqa: E402


def write_flow(path, text):
    path.write_text(textwrap.dedent(text))
    return path


def wait_done(qtbot, tab, timeout=60_000):
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=timeout)


def add_from_palette(w, title):
    tree = w.palette_panel.tree
    item = tree.findItems(title, Qt.MatchExactly | Qt.MatchRecursive)[0]
    tree.itemDoubleClicked.emit(item, 0)


def drag_port_to_port(tab, src, src_port, dst, dst_port):
    view = tab.view
    a = view.mapFromScene(tab.scene.nodes[src].ports[(src_port, True)].scenePos())
    b = view.mapFromScene(tab.scene.nodes[dst].ports[(dst_port, False)].scenePos())
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(view.viewport(), (a + b) / 2)
    QTest.mouseMove(view.viewport(), b)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, b)


def set_code(w, text):
    editor = w.properties.findChild(CodeEdit)
    editor.setPlainText(text)
    editor.editing_finished.emit()


def test_build_save_reopen_and_run_a_flow_from_the_ui(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QCheckBox

    def in_taskloom_python():  # a new Python Code block runs in your Python environment; there is none here
        (box,) = [b for b in win.properties.findChildren(QCheckBox) if "own Python environment" in b.toolTip()]
        box.setChecked(False)

    flow_path = tmp_path / "doubler.yaml"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(flow_path), ""))
    tab = win.new_flow()

    assert [spec["type"] for spec in tab.doc.blocks.values()] == ["trigger.manual"]  # a new flow starts there
    add_from_palette(win, "Python Code")
    add_from_palette(win, "Python Code")
    first, second = [b for b in tab.doc.blocks if b != "start"]
    tab.scene.nodes[first].setSelected(True)
    set_code(win, "output = 21")
    in_taskloom_python()
    tab.scene.clearSelection()
    tab.scene.nodes[second].setSelected(True)
    set_code(win, "open('result.txt', 'w').write(str(input * 2))")
    in_taskloom_python()
    drag_port_to_port(tab, first, "output", second, "input")
    drag_port_to_port(tab, "start", "start", first, "after")
    assert tab.doc.edges() == [(first, "output", second, "input"), ("start", "start", first, "after")]

    assert win.save(tab)
    saved = tab.doc.data
    assert win.close_tab(tab)
    tab = win.open_flow(flow_path)
    assert tab.doc.data == saved

    win.run_flow(tab)
    wait_done(qtbot, tab)

    assert (tmp_path / "result.txt").read_text() == "42"
    assert {b: n.status for b, n in tab.scene.nodes.items()} == {"start": "success", first: "success", second: "success"}
    assert tab.list_status()[0] == "success"


def test_stop_cancels_a_running_flow(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "slow.yaml", """
        blocks:
          slow:
            type: logic.wait
            config: {seconds: 120}
    """)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.scene.nodes["slow"].status == "running", timeout=30_000)

    win.stop(tab)
    wait_done(qtbot, tab, timeout=15_000)

    assert tab.last["status"] == "cancelled"
    assert tab.scene.nodes["slow"].status == "cancelled"


def test_editor_survives_a_flow_whose_process_crashes(win, qtbot, tmp_path):
    crash = write_flow(tmp_path / "crash.yaml", """
        blocks:
          boom:
            type: logic.python
            config: {inputs: [], code: "import os; os._exit(3)"}
    """)
    ok = write_flow(tmp_path / "ok.yaml", """
        blocks:
          fine:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)
    crash_tab = win.open_flow(crash)
    win.run_flow(crash_tab)
    wait_done(qtbot, crash_tab)

    assert crash_tab.last["status"] == "failed"
    assert crash_tab.scene.nodes["boom"].status == "failed"
    history = History(win.home.history_db)
    assert history.blocks(crash_tab.last["id"])["boom"]["status"] == "failed"
    history.close()

    ok_tab = win.open_flow(ok)
    win.run_flow(ok_tab)
    wait_done(qtbot, ok_tab)
    assert ok_tab.last["status"] == "success"


def test_ask_user_block_asks_during_a_manual_run(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "ask.yaml", """
        blocks:
          ask:
            type: logic.ask_user
            config: {prompt: "Which region?", kind: choice, options: [EU, US]}
          save:
            type: logic.python
            config: {outputs: [], code: "open('answer.txt', 'w').write(input)"}
        edges:
          - ask.answer -> save.input
    """)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: win.findChild(AskDialog) is not None, timeout=30_000)
    dialog = win.findChild(AskDialog)
    dialog.input.setCurrentText("US")
    ok = next(b for b in dialog.findChild(QDialogButtonBox).buttons() if b.text() == "OK")
    QTest.mouseClick(ok, Qt.LeftButton)
    wait_done(qtbot, tab)

    assert (tmp_path / "answer.txt").read_text() == "US"


def test_run_dialog_overrides_parameters(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "params.yaml", """
        params:
          day: {type: date, expr: "today() - bdays(1)"}
          day_key: {type: int, expr: "int(format(day, '%Y%m%d'))"}
        blocks:
          save:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('key.txt', 'w').write(str(params['day_key']))"}
    """)

    def exec_with_override(dialog):
        dialog.edits["day"].setText("2026-01-16")
        return True

    monkeypatch.setattr(RunDialog, "exec", exec_with_override)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_done(qtbot, tab)

    assert (tmp_path / "key.txt").read_text() == "20260116"


def test_queued_run_starts_after_the_running_one(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "wait.yaml", """
        blocks:
          pause:
            type: logic.wait
            config: {seconds: 1.5}
    """)

    def choose_queue(dialog):
        dialog.choice = OverlapDialog.QUEUE
        return True

    monkeypatch.setattr(OverlapDialog, "exec", choose_queue)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    win.run_flow(tab)
    assert len(tab.runs) == 1 and len(tab.queued) == 1

    qtbot.waitUntil(lambda: len(tab.runs) == 2 and not tab.running, timeout=60_000)
    history = History(win.home.history_db)
    runs = history.runs(str(flow))
    history.close()
    assert [r["status"] for r in runs] == ["success", "success"]
    assert runs[1]["finished"] <= runs[0]["started"]


def test_undo_restores_deleted_blocks_and_their_edges(tmp_path):
    doc = FlowDocument()
    a = doc.add_block("logic.python", 0, 0)
    b = doc.add_block("logic.python", 250, 0)
    doc.connect(a, "output", b, "input")
    before = [dict(doc.data["blocks"]), list(doc.data["edges"])]

    doc.remove([a])
    assert list(doc.blocks) == [b] and doc.data["edges"] == []
    doc.undo()

    assert [dict(doc.data["blocks"]), list(doc.data["edges"])] == before
    doc.redo()
    assert list(doc.blocks) == [b]


def test_a_schedule_block_schedules_the_flow_when_saved(win, tmp_path):
    flow = write_flow(tmp_path / "daily.yaml", """
        blocks:
          note:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
    """)
    tab = win.open_flow(flow)
    add_from_palette(win, "Schedule")
    schedule = next(b for b, spec in tab.doc.blocks.items() if spec["type"] == "trigger.schedule")
    drag_port_to_port(tab, schedule, "start", "note", "after")  # onto the block's header connector
    tab.doc.set_block_value(schedule, "cron", "0 7 * * MON-FRI")
    assert win.save(tab)

    assert (schedule, "start", "note", "after") in tab.doc.edges()
    assert win.home.scheduled_flows() == [flow.resolve()]
    assert tab.next_run is not None and tab.next_run.hour == 7 and tab.next_run.weekday() < 5

    tab.doc.remove([schedule])  # no schedule left: no longer scheduled
    assert win.save(tab)
    assert win.home.scheduled_flows() == []
    assert tab.next_run is None


def test_settings_dialog_keeps_settings_it_does_not_show(win):
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "settings.yaml").write_text(
        "calendar: US\nkeeper:\n- {connection: appserver, command: ~/taskloom/taskloom, home: ~/.taskloom}\n")
    dialog = SettingsDialog(win.home, win)
    dialog.keep_runs_for.setValue(50)
    dialog._save()

    settings = win.home.settings()
    assert settings["keep_runs_for"] == "50 years" and settings["calendar"] == "US"
    assert settings["keeper"] == [{"connection": "appserver", "command": "~/taskloom/taskloom", "home": "~/.taskloom"}]


@pytest.mark.skipif(sys.platform == "win32", reason="stops the scheduler with SIGTERM")
def test_scheduler_button_starts_and_stops_the_scheduler(win, qtbot, monkeypatch):
    import types

    from taskloom.scheduler import paused

    monkeypatch.setattr(win, "start_scheduler", types.MethodType(REAL_START_SCHEDULER, win))  # the real one

    assert win.scheduler_action.text() == "Scheduler service off"
    win.scheduler_action.trigger()
    try:
        qtbot.waitUntil(win.scheduler_running, timeout=30_000)
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=10_000)

        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
        win.scheduler_action.trigger()

        qtbot.waitUntil(lambda: not win.scheduler_running(), timeout=15_000)
        qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service off", timeout=10_000)
        assert paused(win.home)  # you paused it: it does not start by itself
    finally:
        if win.scheduler_running():
            os.kill(int((win.home.root / "scheduler.pid").read_text()), signal.SIGKILL)


def test_dragging_several_attachments_onto_send_email_keeps_them_all(win):
    tab = win.new_flow()
    a = tab.doc.add_block("reports.excel", 0, 0)
    b = tab.doc.add_block("reports.excel", 0, 200)
    m = tab.doc.add_block("email.send", 300, 100)
    q = tab.doc.add_block("logic.wait", 300, 300)

    drag_port_to_port(tab, a, "file", m, "attachments")
    drag_port_to_port(tab, b, "file", m, "attachments")
    drag_port_to_port(tab, a, "file", q, "value")
    drag_port_to_port(tab, b, "file", q, "value")  # an ordinary input: replaces the first edge

    assert sorted(tab.doc.edges()) == sorted([
        (a, "file", m, "attachments"), (b, "file", m, "attachments"), (b, "file", q, "value")])
    tab.doc.dirty = False  # the window closes before fixture teardown; don't ask to save


def test_every_block_explains_itself_on_hover(win):
    for type_id, cls in win.registry.blocks.items():
        assert len(cls.description) > 20, f"{type_id} needs a plain-language description"
    tree = win.palette_panel.tree
    item = tree.findItems("Health Check", Qt.MatchExactly | Qt.MatchRecursive)[0]
    assert "Checks that an app is alive" in item.toolTip(0)
    tab = win.new_flow()
    block = tab.doc.add_block("email.send", 0, 0)
    assert "Sends an email" in tab.scene.nodes[block].toolTip()
    tab.doc.dirty = False


def test_new_connection_from_a_block_is_created_selected_and_listed(win, monkeypatch):
    from taskloom.editor.dialogs import ConnectionsDialog

    tab = win.new_flow()
    block = tab.doc.add_block("servers.health_check", 0, 0)
    tab.scene.nodes[block].setSelected(True)
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", staticmethod(lambda *a, **k: ("appserver", True)))

    def fill_in(dialog):
        assert dialog._kind() == "ssh"  # opened from an SSH field: the new connection is an SSH one
        dialog.edits["host"].setText("server01.example.com")
        dialog.edits["username"].setText("me")
        dialog.password.setText("s3cret")
        dialog._save()
        return True

    monkeypatch.setattr(ConnectionsDialog, "exec", fill_in)
    new_button = next(b for b in win.properties.findChildren(QToolButton) if b.text() == "New…")
    new_button.click()

    assert tab.doc.blocks[block]["config"]["connection"] == "appserver"
    combo = next(c for c in win.properties.findChildren(QComboBox) if c.findData("appserver") >= 0)
    assert combo.currentData() == "appserver"
    conn = win.home.connections()["appserver"]
    assert conn == {"kind": "ssh", "host": "server01.example.com", "username": "me",
                    "password": "secret:appserver_password"}
    assert win.home.get_secret("appserver_password") == "s3cret"
    tab.doc.dirty = False


def test_data_preview_shows_any_output(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "out.yaml", """
        blocks:
          make:
            type: logic.python
            config: {inputs: [], code: "output = {'status': 'ok', 'rows': 3}"}
    """)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_done(qtbot, tab)
    tab.scene.nodes["make"].setSelected(True)

    assert "\"status\": \"ok\"" in win.preview.text.toPlainText() and not win.preview.text.isHidden()


KILLED_EDITOR = """
import os, signal, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
from PySide6.QtWidgets import QApplication
from taskloom.config import Home
from taskloom.editor.window import MainWindow
MainWindow._check_after_update = lambda self: None  # (the check after an update would wait for a click)
app = QApplication([])
w = MainWindow(Home(Path(sys.argv[1])))
w.show()
w.restore_session([sys.argv[2]])
w.current.doc.add_block("logic.wait", 300, 0)        # an unsaved change to a saved flow
w.new_flow().doc.add_block("logic.python", 0, 0)   # and a flow that was never saved
end = time.monotonic() + 3
while time.monotonic() < end:                        # long enough for the autosave
    app.processEvents()
    time.sleep(0.05)
os.kill(os.getpid(), signal.SIGKILL)                 # no chance to clean up, like a crash
"""


@pytest.mark.skipif(sys.platform == "win32", reason="kills the editor with SIGKILL")
def test_unsaved_work_comes_back_after_the_editor_is_killed(win, tmp_path):
    import subprocess

    flow = write_flow(tmp_path / "work.yaml", """
        blocks:
          first:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)
    script = tmp_path / "killed.py"
    script.write_text(KILLED_EDITOR)
    proc = subprocess.run([sys.executable, str(script), str(win.home.root), str(flow)], timeout=60)
    assert proc.returncode == -signal.SIGKILL
    assert "first" in flow.read_text() and "wait" not in flow.read_text()  # the file itself was not overwritten

    win.restore_session()

    restored = {t.doc.title: t for t in win.tabs_list()}
    work = restored["work.yaml"]
    assert work.doc.dirty and {s["type"] for s in work.doc.blocks.values()} == {"logic.python", "logic.wait"}
    untitled = next(t for t in win.tabs_list() if t.doc.path is None)
    assert untitled.doc.dirty and [s["type"] for s in untitled.doc.blocks.values()] == ["trigger.manual", "logic.python"]
    assert win.message_boxes and win.message_boxes[0][1][0] == "Work recovered"
    win.message_boxes.clear()
    for tab in win.tabs_list():
        tab.doc.dirty = False


def test_python_environment_dialog_creates_it_and_installs_packages(win, qtbot, tmp_path):
    from taskloom.editor.dialogs import PythonEnvDialog
    from test_pyenv import _tiny_wheel

    dialog = PythonEnvDialog(win.home, win)
    assert not dialog.install_button.isEnabled()  # nothing to install into yet
    dialog.python.setText(sys.executable)
    dialog.create_button.click()
    qtbot.waitUntil(lambda: dialog.proc is None, timeout=180_000)
    assert dialog.install_button.isEnabled() and win.home.settings()["python"] == sys.executable

    dialog.packages.setText(f"--no-index {_tiny_wheel(tmp_path)}")
    dialog.install_button.click()
    qtbot.waitUntil(lambda: dialog.proc is None and dialog.output.toPlainText().count("done") >= 3,
                    timeout=180_000)  # create, install, then the list of packages again

    assert "tinypkg" in dialog.output.toPlainText().split("$ taskloom env pip list")[-1]
    assert "failed" not in dialog.output.toPlainText()


def test_python_environment_output_has_no_blank_lines_between_windows_lines(win):
    from taskloom.editor.dialogs import PythonEnvDialog

    dialog = PythonEnvDialog(win.home, win)
    dialog.output.clear()
    dialog._add_output(b"Package Version\r\n------- -------\r\npip     24.")  # pieces can end mid-line
    dialog._add_output(b"0\r\n")

    assert dialog.output.toPlainText() == "Package Version\n------- -------\npip     24.0"


def test_connecting_done_or_an_output_to_run_after_by_dragging(win):
    tab = win.new_flow()
    a = tab.doc.add_block("logic.python", 0, 0)
    b = tab.doc.add_block("logic.python", 300, 0)
    c = tab.doc.add_block("files.delete", 0, 200)
    assert tab.scene.nodes[a].outputs[0] == "done" and tab.scene.nodes[b].inputs[0] == "after"
    assert tab.scene.nodes[c].outputs[0] == "deleted"  # no done: its output always arrives

    drag_port_to_port(tab, a, "done", b, "after")
    drag_port_to_port(tab, c, "deleted", b, "after")

    assert tab.doc.edges() == [(a, "done", b, "after"), (c, "deleted", b, "after")]
    tab.doc.dirty = False


def test_hovering_shows_values_of_the_run_on_screen_and_of_older_runs(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "values.yaml", """
        params:
          n: {type: int, value: 1}
        blocks:
          make:
            type: logic.python
            config: {inputs: [], code: "output = {'run': params['n']}"}
          use:
            type: logic.python
            config: {inputs: [input], outputs: [], code: "pass"}
        edges:
          - make.output -> use.input
    """)
    tab = win.open_flow(flow)
    for n in (1, 2):
        win.start_run(tab, {"n": n})
        qtbot.waitUntil(lambda: not tab.running and tab.last is not None and len(tab.runs) == n, timeout=60_000)

    out_tip = tab.scene.nodes["make"].ports[("output", True)].toolTip()
    in_tip = tab.scene.nodes["use"].ports[("input", False)].toolTip()
    assert '"run": 2' in out_tip and "from <b>make.output</b>" in in_tip and '"run": 2' in in_tip
    assert '"run": 2' in tab.scene.edge_items[0].toolTip()

    history = History(win.home.history_db)
    first = history.runs(str(flow))[-1]["id"]
    history.close()
    win.show_history_run(first)  # an older run: its values
    assert '"run": 1' in tab.scene.nodes["make"].ports[("output", True)].toolTip()


def test_rerun_a_fixed_block_from_the_canvas(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "fix.yaml", """
        blocks:
          load:
            type: logic.python
            config: {inputs: [], code: "open('loads.txt', 'a').write('x'); output = 21"}
          compute:
            type: logic.python
            config: {inputs: [input], outputs: [], code: "raise ValueError('typo')"}
        edges:
          - load.output -> compute.input
    """)
    tab = win.open_flow(flow)
    win.start_run(tab, {})
    wait_done(qtbot, tab)
    assert tab.scene.nodes["compute"].status == "failed"
    first_run = tab.result_run("compute")

    tab.doc.set_block_value("compute", "code", "open('result.txt', 'w').write(str(input * 2))")  # the fix
    win.rerun_block(tab, "compute")
    qtbot.waitUntil(lambda: len(tab.runs) == 2 and not tab.running and tab.runs[1].status is not None, timeout=60_000)

    assert (tmp_path / "result.txt").read_text() == "42" and (tmp_path / "loads.txt").read_text() == "x"
    assert tab.scene.nodes["compute"].status == "success"
    assert tab.scene.nodes["load"].status == "success"  # still showing the earlier run's result
    assert tab.result_run("compute") != first_run and not tab.doc.dirty  # the fix was saved before running


def test_run_a_block_with_inputs_you_choose_from_the_canvas(win, qtbot, tmp_path, monkeypatch):
    from taskloom.editor import dialogs

    flow = write_flow(tmp_path / "choose.yaml", """
        blocks:
          a:
            type: logic.python
            config: {inputs: [], code: "output = 5"}
          check:
            type: logic.if
            config: {condition: "False"}
          ran:
            type: logic.python
            config: {inputs: [x], outputs: [], code: "open('ran.txt', 'w').write(repr(x))"}
          skipped:
            type: logic.python
            config: {inputs: [y], outputs: [], code: "open('skipped.txt', 'w').write(repr(y))"}
        edges:
          - a.output -> ran.x
          - check.true -> skipped.y
    """)
    tab = win.open_flow(flow)
    win.start_run(tab, {})
    wait_done(qtbot, tab)
    shown = {}

    def answer(values):
        def exec_(dialog):
            shown.update({name: field.text() for name, field in dialog.fields.items()})
            for name, text in values.items():
                dialog.fields[name].setText(text)
            return True
        return exec_

    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", answer({"y": "{'k': [1, 2]}"}))
    win.run_block_with_inputs(tab, "skipped")  # it did not run: nothing reached it
    qtbot.waitUntil(lambda: len(tab.runs) == 2 and not tab.running and tab.runs[1].status is not None, timeout=60_000)
    assert shown == {"y": ""} and (tmp_path / "skipped.txt").read_text() == "{'k': [1, 2]}"

    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", answer({"x": "6"}))
    win.run_block_with_inputs(tab, "ran")
    qtbot.waitUntil(lambda: len(tab.runs) == 3 and not tab.running and tab.runs[2].status is not None, timeout=60_000)
    assert shown["x"] == "5" and (tmp_path / "ran.txt").read_text() == "6"
    assert tab.scene.nodes["ran"].status == "success"


def test_a_table_file_you_typed_is_read_again_when_you_test_again(win, qtbot, tmp_path, monkeypatch):
    from taskloom.editor import dialogs

    (tmp_path / "d.csv").write_text("a\n1\n2\n")
    flow = write_flow(tmp_path / "typed.yaml", """
        blocks:
          use:
            type: logic.python
            config: {inputs: [t], outputs: [], code: "open('got.txt', 'a').write(f'{len(t)} ')"}
    """)
    tab = win.open_flow(flow)
    shown = {}

    def answer(values):
        def exec_(dialog):
            shown.update({name: field.text() for name, field in dialog.fields.items()})
            for name, text in values.items():
                dialog.fields[name].setText(text)
            return True
        return exec_

    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", answer({"t": "table('d.csv')"}))
    win.run_block_with_inputs(tab, "use")
    qtbot.waitUntil(lambda: len(tab.runs) == 1 and not tab.running and tab.runs[0].status is not None, timeout=60_000)
    (tmp_path / "d.csv").write_text("a\n1\n2\n3\n")  # you edit the data

    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", answer({}))  # and just press Run again
    win.run_block_with_inputs(tab, "use")
    qtbot.waitUntil(lambda: len(tab.runs) == 2 and not tab.running and tab.runs[1].status is not None, timeout=60_000)

    assert shown["t"] == "table('d.csv')" and (tmp_path / "got.txt").read_text() == "2 3 "


def test_the_canvas_shows_runs_started_elsewhere(win, qtbot, tmp_path):
    import subprocess

    flow = write_flow(tmp_path / "elsewhere.yaml", """
        params:
          n: {type: int, value: 1}
        blocks:
          make:
            type: logic.python
            config: {inputs: [], code: "output = params['n']"}
    """)
    tab = win.open_flow(flow)
    win.start_run(tab, {})
    wait_done(qtbot, tab)
    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root)}
    subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow), "--param", "n=7"], env=env, check=True,
                   capture_output=True)  # e.g. the scheduler

    win._refresh_statuses()

    assert "7" in tab.scene.nodes["make"].ports[("output", True)].toolTip()


def test_the_log_keeps_earlier_runs_including_scheduled_ones(win, qtbot, tmp_path):
    import subprocess

    flow = write_flow(tmp_path / "kept.yaml", """
        params:
          n: {type: int, value: 1}
        blocks:
          make:
            type: logic.python
            config: {inputs: [], code: "print('got', params['n'])"}
    """)
    tab = win.open_flow(flow)
    win.start_run(tab, {})
    wait_done(qtbot, tab)
    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root)}
    for n in (7, 8):  # e.g. two scheduled runs between two looks at the history
        subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow), "--param", f"n={n}"], env=env,
                       check=True, capture_output=True)

    win._refresh_statuses()

    text = win.log.text.toPlainText()
    assert text.index("got 1") < text.index("run #2 · ") < text.index("got 7") < text.index("got 8")
    win.log.set_filter("make")  # one block's messages, still split by run
    text = win.log.text.toPlainText()
    assert "run #3 · " in text and "got 8" in text


def test_python_environment_screen_shows_an_existing_environment(win, tmp_path, monkeypatch, qtbot):
    import subprocess

    from taskloom.editor.dialogs import PythonEnvDialog

    existing = tmp_path / "my-venv"
    subprocess.run([sys.executable, "-m", "venv", str(existing)], check=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(existing)))
    dialog = PythonEnvDialog(win.home, win)
    assert "Not created yet" in dialog.status.text()

    dialog.existing_button.click()
    qtbot.waitUntil(lambda: dialog.proc is None, timeout=120_000)

    assert str(existing) in dialog.status.text() and "Python 3" in dialog.status.text()
    assert win.home.settings()["python_env"] == str(existing)
    assert "pip" in dialog.output.toPlainText()  # its packages are listed
    reopened = PythonEnvDialog(win.home, win)
    qtbot.waitUntil(lambda: reopened.proc is None, timeout=120_000)
    assert str(existing) in reopened.status.text() and "pip" in reopened.output.toPlainText()


def test_text_in_the_log_can_be_copied(win, qtbot):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    win.bottom_tabs.setCurrentWidget(win.log)
    win.log.set_entries([{"ts": "2026-10-15T09:00:00", "level": "INFO", "message": "copy me"}])
    QApplication.clipboard().setText("")
    win.log.text.setFocus()
    win.log.text.selectAll()

    QTest.keyClick(win.log.text, Qt.Key_C, Qt.ControlModifier)

    assert "copy me" in QApplication.clipboard().text()


def test_ctrl_c_and_ctrl_v_on_the_canvas_copy_and_paste_blocks(win, qtbot):
    from PySide6.QtTest import QTest

    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.new_flow()
    block = tab.doc.add_block("logic.python", 0, 0)
    tab.scene.nodes[block].setSelected(True)
    tab.view.setFocus()

    QTest.keyClick(tab.view, Qt.Key_C, Qt.ControlModifier)
    QTest.keyClick(tab.view, Qt.Key_V, Qt.ControlModifier)

    assert [spec["type"] for spec in tab.doc.blocks.values()].count("logic.python") == 2
    tab.doc.dirty = False


def test_renaming_an_input_or_output_keeps_its_connections(win):
    from PySide6.QtWidgets import QLineEdit

    tab = win.new_flow()
    p = tab.doc.add_block("logic.python", 0, 0)
    q = tab.doc.add_block("logic.python", 300, 0)
    tab.doc.connect(p, "output", q, "input")
    tab.doc.connect(p, "output", q, "after", many=True)

    def set_names(block, current, text):
        tab.scene.clearSelection()
        tab.scene.nodes[block].setSelected(True)
        field = next(e for e in win.properties.findChildren(QLineEdit) if current in (e.text(), e.placeholderText()))
        field.setText(text)
        field.editingFinished.emit()

    set_names(p, "output", "result")
    set_names(q, "input", "data")
    assert sorted(tab.doc.edges()) == [(p, "result", q, "after"), (p, "result", q, "data")]

    set_names(p, "result", "other, more")  # result is gone: so are its connections
    assert tab.doc.edges() == []
    tab.doc.dirty = False


def test_code_typed_in_one_flow_stays_in_that_flow(win, qtbot):
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    first, second = win.new_flow(), win.new_flow()
    for tab in (first, second):
        assert tab.doc.add_block("logic.python", 0, 0) == "python"  # the same name in both flows
    win.tabs.setCurrentWidget(first)
    first.scene.nodes["python"].setSelected(True)
    editor = win.properties.findChild(CodeEdit)
    editor.setFocus()
    qtbot.waitUntil(editor.hasFocus)  # as when you are typing in it
    editor.setPlainText("output = 'first'")

    win.tabs.setCurrentWidget(second)  # without leaving the code box first
    second.scene.nodes["python"].setSelected(True)

    assert first.doc.blocks["python"]["config"].get("code") == "output = 'first'"
    assert "code" not in second.doc.blocks["python"].get("config", {})
    for tab in (first, second):
        tab.doc.dirty = False


def test_runs_are_named_with_their_date_and_time(win, qtbot, tmp_path):
    import re

    flow = write_flow(tmp_path / "when.yaml", """
        blocks:
          python:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)
    tab = win.open_flow(flow)
    win.start_run(tab, {})
    wait_done(qtbot, tab)
    when = r"run #\d+ · \d{4}-\d\d-\d\d \d\d:\d\d:\d\d"

    assert re.search(when + " started", win.log.text.toPlainText())
    menu = win._block_menu_for(tab, "python")
    assert any(re.search(when, a.text()) for a in menu.actions())


def test_hovering_a_blocks_message_shows_all_of_it(win, qtbot, tmp_path):
    long = "this message is much longer than the space under the block " * 3
    flow = write_flow(tmp_path / "long.yaml", f"""
        blocks:
          python:
            type: logic.python
            config: {{inputs: [], code: "raise ValueError('{long}')"}}
    """)
    tab = win.open_flow(flow)
    win.start_run(tab, {})
    wait_done(qtbot, tab)
    node = tab.scene.nodes["python"]

    assert long.strip() in " ".join(node.footer.toolTip().split())
    assert node.footer.acceptedMouseButtons() == Qt.NoButton  # clicks still select and move the block


def test_opening_a_flow_shows_only_runs_you_have_not_seen(win, tmp_path):
    import subprocess

    flow = write_flow(tmp_path / "seen.yaml", """
        blocks:
          python:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)
    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root)}

    def run_elsewhere():  # e.g. the scheduler
        subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow)], env=env, check=True, capture_output=True)

    run_elsewhere()  # before you ever opened it here
    tab = win.open_flow(flow)
    assert tab.scene.nodes["python"].status is None  # starts clean
    win.close_tab(tab)

    run_elsewhere()  # while it was closed
    tab = win.open_flow(flow)
    assert tab.scene.nodes["python"].status == "success"
    win.close_tab(tab)

    tab = win.open_flow(flow)  # nothing new since you saw it
    assert tab.scene.nodes["python"].status is None


def test_text_on_the_canvas(win, tmp_path):
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QCheckBox

    from taskloom.flow import load_flow

    flow = write_flow(tmp_path / "noted.yaml", """
        blocks:
          python:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)
    tab = win.open_flow(flow)
    menu = win._canvas_menu_for(tab, QPointF(40, -80))
    next(a for a in menu.actions() if a.text() == "Add text here").trigger()

    (note_id,) = tab.doc.notes
    assert tab.scene.note_items[note_id].isSelected()  # ready to write in Properties
    editor = win.properties.findChild(CodeEdit)
    editor.setPlainText("Daily load\nruns at 7:00")
    editor.editing_finished.emit()
    next(b for b in win.properties.findChildren(QCheckBox) if b.text() == "Italic").setChecked(True)
    style = next(c for c in win.properties.findChildren(QComboBox) if c.findData("title") >= 0)
    style.setCurrentIndex(style.findData("title"))
    style.activated.emit(style.currentIndex())

    item = tab.scene.note_items[note_id]
    assert item.toPlainText() == "Daily load\nruns at 7:00" and item.font().italic() and item.font().bold()
    assert win.save(tab)
    assert load_flow(flow).blocks  # the flow still loads (and runs): text is only for the editor
    tab.scene.note_items[note_id].setSelected(True)
    tab.scene.delete_selected()
    assert tab.doc.notes == {}
    tab.doc.undo()
    assert tab.doc.notes[note_id]["style"] == "title"
    tab.doc.dirty = False


def test_check_flows_backs_up_and_fixes_open_flows(win, tmp_path, monkeypatch):
    from taskloom.editor import dialogs

    leftover = """
        blocks:
          start:
            type: trigger.manual
          python:
            type: logic.python
            config: {inputs: [], outputs: [result], code: "result = 1"}
          after:
            type: logic.python
            config: {code: "pass"}
        edges:
          - start.start -> python.after
          - python.output -> after.after
    """
    clean_tab = win.open_flow(write_flow(tmp_path / "saved.yaml", leftover))
    busy_tab = win.open_flow(write_flow(tmp_path / "busy.yaml", leftover))
    busy_tab.doc.move_blocks({"python": (10, 10)})  # unsaved changes
    monkeypatch.setattr(dialogs.CheckFlowsDialog, "exec", lambda self: True)  # Back up and fix

    win.check_flows()

    assert clean_tab.doc.edges() == [("start", "start", "python", "after")] and not clean_tab.doc.dirty
    assert len(list((win.home.root / "backups").iterdir())) == 1
    assert "python.output -> after.after" in (tmp_path / "busy.yaml").read_text()  # left for you to save first
    assert win.message_boxes and "busy.yaml" in win.message_boxes[0][1][1]
    win.message_boxes.clear()
    busy_tab.doc.dirty = False


def test_hovering_a_blocks_result_shows_its_outputs_laid_out(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "laid.yaml", """
        blocks:
          check:
            type: logic.python
            config: {inputs: [], code: "output = {'healthy': True, 'checks': [1, 2]}"}
    """)
    tab = win.open_flow(flow)
    win.start_run(tab, {})
    wait_done(qtbot, tab)

    tip = tab.scene.nodes["check"].footer.toolTip()
    assert "<b>output</b><pre>" in tip and '\n  "healthy": true,\n' in tip  # as on the connections


def test_schedule_countdown():
    import datetime as dt

    from taskloom.editor.window import schedule_countdown

    now = dt.datetime(2026, 10, 15, 4, 46, 55)  # a Thursday
    daily = {"cron": "0 7 * * *"}
    assert schedule_countdown(daily, now, True, True, False)[0] == "next run in 02:13:05"
    assert schedule_countdown({"cron": "0 7 * * MON"}, now, True, True, False)[0] == "next run in 4d 02:13:05"
    assert schedule_countdown(daily, now, True, False, False)[0] == "scheduler service off"  # no time: none comes
    assert schedule_countdown(daily, now, False, True, True)[0] == "next Thu 07:00 · save to schedule"
    assert schedule_countdown(daily, now, True, True, True)[0] == "unsaved · the scheduler runs the saved version"
    assert schedule_countdown({**daily, "active": False}, now, True, True, False)[0] == "not active"
    assert schedule_countdown({}, now, True, True, False)[0] == "needs a time"
    assert schedule_countdown({"at": "2026-10-01 10:00"}, now, True, True, False)[0] == "no more runs"
    assert "Fri 16 Oct 2026 07:00" in schedule_countdown(daily, now, True, True, False)[1]


def test_a_schedule_block_is_not_shown_skipped_when_you_run_the_flow(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "both.yaml", """
        blocks:
          manual:
            type: trigger.manual
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          work:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - manual.start -> work.after
          - nightly.start -> work.after
    """)
    tab = win.open_flow(flow)
    win.start_run(tab, {})
    wait_done(qtbot, tab)
    win._show_countdowns()

    node = tab.scene.nodes["nightly"]
    assert node.status is None and node.countdown.startswith("next ")  # its countdown, not "skipped"
    assert tab.scene.nodes["work"].status == "success"


def test_text_from_the_blocks_tree_written_and_resized_on_the_canvas(win, qtbot):
    from PySide6.QtCore import QPointF

    from taskloom.editor.canvas import TEXT_ITEM

    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.new_flow()
    tree = win.palette_panel.tree
    notes = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).text(0).startswith("Notes"))
    assert notes.child(0).text(0) == "Text" and notes.child(0).data(0, Qt.UserRole) == TEXT_ITEM
    tab.view.add_item(TEXT_ITEM, QPointF(0, 0))  # as when dropping it on the canvas
    (note_id,) = tab.doc.notes
    item = tab.scene.note_items[note_id]
    qtbot.waitUntil(item.isSelected)

    view = tab.view.viewport()
    center = tab.view.mapFromScene(item.mapToScene(item.boundingRect().center()))
    QTest.mouseDClick(view, Qt.LeftButton, Qt.NoModifier, center)
    assert item.editing
    QTest.keyClick(tab.view, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(tab.view, "Hello world")
    QTest.keyClick(tab.view, Qt.Key_Backspace)
    QTest.keyClick(tab.view, Qt.Key_Delete)  # keys go to the text, not to deleting it
    QTest.keyClick(tab.view, Qt.Key_Escape)  # done writing
    qtbot.waitUntil(lambda: tab.doc.notes.get(note_id, {}).get("text") == "Hello worl")

    def drag(grip, by):
        item = tab.scene.note_items[note_id]
        item.setSelected(True)
        a = tab.view.mapFromScene(item.mapToScene(grip(item).center()))
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, a)
        QTest.mouseMove(view, a + by / 2)
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, a + by)

    drag(lambda i: i._handle(), QPointF(0, 40).toPoint())  # the corner, straight down: bigger text
    qtbot.waitUntil(lambda: tab.doc.notes[note_id].get("size", 0) > 15)
    assert tab.scene.note_items[note_id].font().pointSizeF() == tab.doc.notes[note_id]["size"]
    size = tab.doc.notes[note_id]["size"]
    width = tab.scene.note_items[note_id].boundingRect().width()
    drag(lambda i: i._edge(), QPointF(100, 0).toPoint())  # the right edge: where its lines wrap
    qtbot.waitUntil(lambda: tab.doc.notes[note_id].get("width", 0) > width + 50)
    assert tab.doc.notes[note_id]["size"] == size  # the text kept its size
    tab.doc.undo()
    assert "width" not in tab.doc.notes[note_id]
    tab.doc.dirty = False


def test_choosing_when_a_schedule_runs(win):
    from PySide6.QtCore import QTime
    from PySide6.QtWidgets import QCheckBox, QLabel, QTimeEdit

    tab = win.new_flow()
    block = tab.doc.add_block("trigger.schedule", 0, 0)
    tab.scene.nodes[block].setSelected(True)
    props = win.properties

    def combo(item):
        return next(c for c in props.findChildren(QComboBox) if c.findText(item) >= 0)

    def choose(item):
        box = combo(item)
        box.setCurrentIndex(box.findText(item))
        box.activated.emit(box.currentIndex())

    def config():
        return tab.doc.blocks[block].get("config", {})

    def preview():
        return " ".join(label.text() for label in props.findChildren(QLabel))

    assert "Not set yet" in preview()
    choose("Every weekday (Mon–Fri)")
    assert config()["cron"] == "0 7 * * MON-FRI"
    edit = props.findChild(QTimeEdit)
    edit.setTime(QTime(7, 30))
    edit.editingFinished.emit()
    assert config()["cron"] == "30 7 * * MON-FRI" and "Monday to Friday at 07:30" in preview() and "Next:" in preview()

    choose("Some days of the week")
    for day in ("Wed", "Fri"):
        next(b for b in props.findChildren(QCheckBox) if b.text() == day).setChecked(True)
    assert config()["cron"] == "0 7 * * MON,WED,FRI"

    choose("One time")
    assert "cron" not in config() and config()["at"]
    assert "Once, on" in preview()
    tab.doc.dirty = False


def test_a_custom_cron_line_can_be_chosen_and_written(win):
    from PySide6.QtWidgets import QLabel, QLineEdit

    tab = win.new_flow()
    block = tab.doc.add_block("trigger.schedule", 0, 0)
    tab.scene.nodes[block].setSelected(True)
    props = win.properties

    def choose(item):
        box = next(c for c in props.findChildren(QComboBox) if c.findText(item) >= 0)
        box.setCurrentIndex(box.findText(item))
        box.activated.emit(box.currentIndex())

    def cron_line():
        return next((e for e in props.findChildren(QLineEdit) if e.toolTip().startswith("minute hour")), None)

    for start in (None, "Every day"):  # from a new block, and from a plan
        if start:
            choose(start)
        choose("Custom (a cron line)")
        line = cron_line()
        assert line is not None
        line.setText("0 7,13 * * *")
        line.editingFinished.emit()
        assert tab.doc.blocks[block]["config"]["cron"] == "0 7,13 * * *"
        assert "Custom: 0 7,13 * * *" in " ".join(label.text() for label in props.findChildren(QLabel))
    tab.doc.dirty = False


def test_text_being_written_on_the_canvas_is_kept_when_saving_or_closing(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "titled.yaml", """
        notes:
          note_1: {text: Text, x: 0, y: 0}
    """)
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.open_flow(flow)

    def write(text):
        item = tab.scene.note_items["note_1"]
        QTest.mouseDClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier,
                          tab.view.mapFromScene(item.mapToScene(item.boundingRect().center())))
        assert item.editing
        QTest.keyClick(tab.view, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(tab.view, text)

    write("My title")
    QTest.keyClick(tab.view, Qt.Key_S, Qt.ControlModifier)  # save while still writing
    assert "My title" in flow.read_text() and not tab.doc.dirty

    write("Changed")
    win.close_tab(tab)  # asks: the text is an unsaved change
    assert win.message_boxes and win.message_boxes[0][1][0] == "Unsaved changes"
    win.message_boxes.clear()


def test_saving_while_typing_in_properties_keeps_what_you_typed(win, qtbot, tmp_path):
    from PySide6.QtWidgets import QLineEdit

    flow = write_flow(tmp_path / "named.yaml", """
        blocks:
          python:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.open_flow(flow)
    tab.scene.nodes["python"].setSelected(True)
    name = next(e for e in win.properties.findChildren(QLineEdit) if e.text() == "python")
    name.setFocus()
    qtbot.waitUntil(name.hasFocus)
    name.setText("load_sales")

    QTest.keyClick(name, Qt.Key_S, Qt.ControlModifier)  # save before leaving the field

    assert "load_sales:" in flow.read_text() and not tab.doc.dirty


def test_an_older_scheduler_running_is_offered_a_restart(win, monkeypatch):
    import os

    from taskloom.scheduler import acquire_lock

    lock = acquire_lock(win.home)  # a scheduler from an older Taskloom: it writes no version
    try:
        (win.home.root / "scheduler.pid").write_text(str(os.getpid()))
        win._show_scheduler_state(True)
        win._show_scheduler_state(True)  # once per session, not every few seconds
    finally:
        lock.close()

    assert [box[1][0] for box in win.message_boxes] == ["Restart the scheduler service?"]
    assert "older Taskloom" in win.scheduler_action.toolTip()
    win.message_boxes.clear()


def test_restarting_the_scheduler_stops_the_old_one_first(win, qtbot, monkeypatch):
    import subprocess
    import time

    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root)}
    old = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while not win.scheduler_running() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert win.scheduler_running()
        started = []
        monkeypatch.setattr(win, "start_scheduler", lambda: started.append(win.scheduler_running()))

        win.restart_scheduler()

        qtbot.waitUntil(lambda: bool(started), timeout=20_000)
        assert started == [False]  # this version's starts only once the old one has stopped
        assert old.wait(timeout=10) is not None
    finally:
        if old.poll() is None:
            old.kill()


def test_a_scheduled_run_shows_its_result_and_the_countdown(win, tmp_path):
    import subprocess

    flow = write_flow(tmp_path / "nightly.yaml", """
        blocks:
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          work:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - nightly.start -> work.after
    """)
    tab = win.open_flow(flow)  # a flow that never ran: its first run, on schedule, is new to you
    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root)}
    subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow), "--trigger", "nightly"], env=env,
                   check=True, capture_output=True)  # as the scheduler does

    win._refresh_statuses()
    win._show_countdowns()

    node = tab.scene.nodes["nightly"]
    assert node.status == "success" and node.countdown.startswith("next ")  # both: result and countdown
    assert node.countdown_area.isVisible() and "Next runs" in node.countdown_area.toolTip()
    assert tab.scene.nodes["work"].status == "success"


def test_clicking_a_texts_grips_without_dragging_changes_nothing(win, qtbot):
    from PySide6.QtCore import QPointF

    win.show()
    qtbot.waitExposed(win)
    tab = win.new_flow()
    tab.view.add_item("canvas.text", QPointF(0, 0))
    (note_id,) = tab.doc.notes
    before = dict(tab.doc.notes[note_id])
    for grip in ("_handle", "_edge"):
        item = tab.scene.note_items[note_id]
        item.setSelected(True)
        at = tab.view.mapFromScene(item.mapToScene(getattr(item, grip)().center()))
        QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
        qtbot.wait(50)

    assert {k: v for k, v in tab.doc.notes[note_id].items() if k not in ("x", "y")} == \
           {k: v for k, v in before.items() if k not in ("x", "y")}
    tab.doc.dirty = False


def test_save_as_over_a_flow_you_saw_does_not_replay_its_runs(win, tmp_path, monkeypatch):
    import subprocess

    seen = write_flow(tmp_path / "seen.yaml", """
        blocks:
          python:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)
    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root)}
    subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(seen)], env=env, check=True, capture_output=True)
    win.close_tab(win.open_flow(seen))  # you saw its runs
    fresh = win.open_flow(write_flow(tmp_path / "fresh.yaml", seen.read_text()))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(seen), "")))

    win.save(fresh, ask=True)

    assert fresh.scene.nodes["python"].status is None and not fresh.entries


def test_a_size_field_left_unchanged_keeps_a_dragged_size(win):
    from PySide6.QtWidgets import QAbstractSpinBox

    tab = win.new_flow()
    note_id = tab.doc.add_note(0, 0)
    tab.doc.set_note_value(note_id, "size", 10.7)
    tab.scene.note_items[note_id].setSelected(True)
    size = next(s for s in win.properties.findChildren(QAbstractSpinBox) if s.suffix() == " pt")

    size.editingFinished.emit()
    assert tab.doc.notes[note_id]["size"] == 10.7  # left unchanged: kept

    size.setValue(11)  # the style's own size: no size of its own
    size.editingFinished.emit()
    assert "size" not in tab.doc.notes[note_id]
    tab.doc.dirty = False


def test_renaming_a_block_then_changing_the_selection(win, qtbot):
    from PySide6.QtWidgets import QLineEdit

    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.new_flow()
    tab.doc.add_block("logic.python", 0, 0)
    tab.scene.nodes["python"].setSelected(True)
    name = next(e for e in win.properties.findChildren(QLineEdit) if e.text() == "python")
    name.setFocus()
    qtbot.waitUntil(name.hasFocus)
    name.setText("load")

    win.copy_blocks()
    win.paste_blocks()  # the selection changes (to the copy) while the name is being typed
    qtbot.wait(50)

    assert "load" in tab.doc.blocks and "python" not in tab.doc.blocks
    tab.doc.dirty = False


def test_the_right_edge_grip_of_a_one_line_text_only_changes_where_it_wraps(win, qtbot):
    from PySide6.QtCore import QPointF

    win.show()
    qtbot.waitExposed(win)
    tab = win.new_flow()
    note_id = tab.doc.add_note(0, 0)
    tab.doc.set_note_value(note_id, "style", "small")  # one short line
    item = tab.scene.note_items[note_id]
    item.setSelected(True)
    assert not item._edge().intersects(item._handle())
    edge = item._edge()
    for y in (edge.top() + 1, edge.center().y(), edge.bottom() - 1):  # anywhere in the drawn square
        item = tab.scene.note_items[note_id]
        item.setSelected(True)
        a = tab.view.mapFromScene(item.mapToScene(QPointF(edge.center().x(), y)))
        QTest.mousePress(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, a)
        QTest.mouseMove(tab.view.viewport(), a + QPointF(20, 0).toPoint())
        QTest.mouseRelease(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, a + QPointF(40, 0).toPoint())
        qtbot.wait(50)

    assert "size" not in tab.doc.notes[note_id] and tab.doc.notes[note_id]["width"] > 0
    tab.doc.dirty = False


def test_a_renamed_block_stays_selected(win):
    from PySide6.QtWidgets import QLineEdit

    tab = win.new_flow()
    tab.doc.add_block("logic.python", 0, 0)
    tab.scene.nodes["python"].setSelected(True)
    name = next(e for e in win.properties.findChildren(QLineEdit) if e.text() == "python")
    name.setText("load")
    name.editingFinished.emit()  # Enter

    assert tab.scene.selected_block_ids() == ["load"]
    assert any(e.text() == "load" for e in win.properties.findChildren(QLineEdit))
    tab.doc.dirty = False


def test_the_space_next_to_an_unselected_text_is_empty_canvas(win, qtbot):
    from PySide6.QtCore import QPointF

    win.show()
    qtbot.waitExposed(win)
    tab = win.new_flow()
    note_id = tab.doc.add_note(0, 0, "Body remark line one")
    tab.scene.clearSelection()
    item = tab.scene.note_items[note_id]
    beside = item.mapToScene(QPointF(item._text_rect().right() + 6, item._text_rect().center().y()))
    a = tab.view.mapFromScene(beside)
    steps = len(tab.doc._undo)

    QTest.mousePress(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(tab.view.viewport(), a + QPointF(30, 20).toPoint())
    QTest.mouseRelease(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, a + QPointF(60, 40).toPoint())
    qtbot.wait(50)

    assert (tab.doc.notes[note_id]["x"], tab.doc.notes[note_id]["y"]) == (0, 0) and len(tab.doc._undo) == steps
    tab.doc.dirty = False


def test_dragging_a_texts_corner_straight_left_makes_it_smaller(win, qtbot):
    from PySide6.QtCore import QPointF

    win.show()
    qtbot.waitExposed(win)
    tab = win.new_flow()
    note_id = tab.doc.add_note(0, 0, "A remark to shrink")
    item = tab.scene.note_items[note_id]
    item.setSelected(True)
    a = tab.view.mapFromScene(item.mapToScene(item._handle().center()))

    QTest.mousePress(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(tab.view.viewport(), a - QPointF(20, 0).toPoint())
    QTest.mouseRelease(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, a - QPointF(40, 0).toPoint())
    qtbot.wait(50)

    assert 0 < tab.doc.notes[note_id].get("size", 99) < 11
    tab.doc.dirty = False


def test_a_start_blocks_menu_runs_the_flow_from_it(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "starts.yaml", """
        blocks:
          manual:
            type: trigger.manual
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          health_check:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('checked.txt', 'w').write('x')"}
          nightly_job:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('nightly.txt', 'w').write('x')"}
        edges:
          - manual.start -> health_check.after
          - nightly.start -> nightly_job.after
    """)
    tab = win.open_flow(flow)

    # The other line's block keeps what it showed: nothing yet, then its result from the first run.
    for start, ran, other, shows in (("manual", "health_check", "nightly_job", None),
                                     ("nightly", "nightly_job", "health_check", "success")):
        actions = {a.text(): a for a in win._block_menu_for(tab, start).actions()}
        assert not any("same inputs" in t or "inputs I choose" in t for t in actions)  # nothing to retry in it
        (run,) = actions.values()
        run.trigger()
        qtbot.waitUntil(lambda: not tab.running and tab.runs[-1].status is not None, timeout=60_000)
        assert tab.scene.nodes[ran].status == "success" and tab.scene.nodes[other].status == shows
        assert tab.scene.nodes[start].status == "success"
    assert (tmp_path / "checked.txt").exists() and (tmp_path / "nightly.txt").exists()


def test_each_flows_tab_shows_its_last_result_and_next_run(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "shown.yaml", """
        blocks:
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          work:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - nightly.start -> work.after
    """)
    tab = win.open_flow(flow)
    win.save(tab)  # saved with a Schedule block: scheduled
    win.start_run(tab, {})
    wait_done(qtbot, tab)
    win._refresh_statuses()

    index = win.tabs.indexOf(tab)
    assert not win.tabs.tabIcon(index).isNull()
    import re
    tip = win.tabs.tabToolTip(index)  # e.g. ok 2026-10-16 13:15:42 · scheduler service off
    # (no scheduler service in these tests: while it is on, "· next 2026-10-17 02:00:00", see test_service_off.py)
    assert re.search(r"ok \d{4}-\d\d-\d\d \d\d:\d\d:\d\d · scheduler service off$", tip), tip
    assert not hasattr(win, "flows_panel")  # the tabs say it: no separate list of flows


def test_run_from_a_block_on_from_the_canvas(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "from.yaml", """
        blocks:
          load:
            type: logic.python
            config: {inputs: [], code: "open('loads.txt', 'a').write('x'); output = 20"}
          compute:
            type: logic.python
            config: {inputs: [input], code: "import os; assert os.path.exists('fix.txt'), 'not fixed'; output = input + 1"}
          report:
            type: logic.python
            config: {inputs: [input], outputs: [], code: "open('report.txt', 'w').write(str(input))"}
        edges:
          - load.output -> compute.input
          - compute.output -> report.input
    """)
    tab = win.open_flow(flow)
    win.start_run(tab, {})
    wait_done(qtbot, tab)
    assert tab.scene.nodes["report"].status == "skipped"
    (tmp_path / "fix.txt").write_text("")

    actions = [a.text() for a in win._block_menu_for(tab, "compute").actions()]
    assert [t.split(" (")[0] for t in actions] == ["Run only this block, with the same inputs",
                                                   "Run only this block, with inputs I choose…",
                                                   "Run from this block on, with the same inputs",
                                                   "Run from this block on, with inputs I choose…",
                                                   "Results of this block…", "Create Reusable block…"]
    next(a for a in win._block_menu_for(tab, "compute").actions() if a.text().startswith("Run from")).trigger()
    qtbot.waitUntil(lambda: not tab.running and tab.runs[-1].status is not None, timeout=60_000)

    assert tab.scene.nodes["compute"].status == "success" and tab.scene.nodes["report"].status == "success"
    assert tab.scene.nodes["load"].status == "success"  # not run again: it keeps its result from before
    assert (tmp_path / "report.txt").read_text() == "21" and (tmp_path / "loads.txt").read_text() == "x"


def test_run_now_says_what_it_will_run_first(win, qtbot, tmp_path, monkeypatch):
    from taskloom.editor import dialogs

    asked, offered = [], []
    monkeypatch.setattr(dialogs, "confirm_run_now", lambda parent, name, what: asked.append(what) or (False, False))
    monkeypatch.setattr(dialogs, "offer_manual_start", lambda *a: offered.append(a) or False)
    none = win.open_flow(write_flow(tmp_path / "none.yaml", "blocks:\n  a:\n    type: logic.python\n"
                                                            "    config: {inputs: [], code: 'pass'}\n"))
    win.run_flow(none)  # no start block: you are offered a Manual Start; no thanks: nothing runs
    assert len(offered) == 1 and not asked and not none.runs
    flows = {
        "manual.yaml": ("blocks:\n  manual:\n    type: trigger.manual\n  a:\n    type: logic.python\n"
                        "    config: {inputs: [], code: 'pass'}\nedges: ['manual.start -> a.after']\n",
                        "runs from its Manual Start block manual"),
        "nightly.yaml": ("blocks:\n  nightly:\n    type: trigger.schedule\n    config: {cron: '0 2 * * *'}\n"
                         "  a:\n    type: logic.python\n    config: {inputs: [], code: 'pass'}\n"
                         "edges: ['nightly.start -> a.after']\n",
                         "no Manual Start block, so it starts from its Schedule block nightly, as if its time had come"),
    }
    for name, (text, expected) in flows.items():
        tab = win.open_flow(write_flow(tmp_path / name, text))
        win.run_flow(tab)
        assert expected in asked[-1] and not tab.runs  # you said no: nothing ran

    monkeypatch.setattr(dialogs, "confirm_run_now", lambda *a: asked.append("again") or (True, True))  # yes, never ask
    win.run_flow(tab)
    wait_done(qtbot, tab)
    win.run_flow(tab)  # not asked again
    wait_done(qtbot, tab)
    assert asked.count("again") == 1 and len(tab.runs) == 2
    win.run_flow(tab, start="nightly")  # from a start block's menu: you chose what runs, no question
    qtbot.waitUntil(lambda: len(tab.runs) == 3 and not tab.running, timeout=60_000)
    assert asked.count("again") == 1


def test_flows_running_anywhere_are_listed_and_open_from_there(win, qtbot, tmp_path):
    import subprocess

    flow = write_flow(tmp_path / "slow.yaml", """
        blocks:
          hourly:
            type: trigger.schedule
            config: {cron: "0 * * * *"}
          wait:
            type: logic.wait
            config: {seconds: 60}
        edges:
          - hourly.start -> wait.after
    """)
    win.home.set_scheduled(flow.resolve(), True)  # (as saving it in the editor does)
    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root)}
    elsewhere = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "run", str(flow)], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # e.g. the scheduler's
    try:
        def shown():
            win._refresh_statuses()
            return win.running_flows.text() == "1 flow running"
        qtbot.waitUntil(shown, timeout=30_000)

        (item,) = win.running_flows.menu().actions()
        assert item.text().startswith("slow")
        # its schedule: no next run while the scheduler service is off (as in these tests; see test_service_off.py)
        assert item.text().endswith("  ·  scheduler service off") and "next run" not in item.text(), item.text()
        item.trigger()  # opens it
        assert win.current is not None and win.current.doc.path == flow.resolve()
    finally:
        elsewhere.kill()
        elsewhere.wait()

    win._refresh_statuses()  # its process is gone: not running any more (and recorded as failed)
    assert win.running_flows.text() == "No flows running"
    history = win._history()
    assert history.runs(str(flow.resolve()))[0]["status"] == "failed"
    history.close()


def test_run_from_a_block_on_with_inputs_you_choose_from_the_canvas(win, qtbot, tmp_path, monkeypatch):
    from taskloom.editor import dialogs

    flow = write_flow(tmp_path / "from_chosen.yaml", """
        blocks:
          load:
            type: logic.python
            config: {inputs: [], code: "output = 20"}
          compute:
            type: logic.python
            config: {inputs: [input], code: "output = input + 1"}
          report:
            type: logic.python
            config: {inputs: [input], outputs: [], code: "open('report.txt', 'w').write(str(input))"}
        edges:
          - load.output -> compute.input
          - compute.output -> report.input
    """)
    tab = win.open_flow(flow)
    win.start_run(tab, {})
    wait_done(qtbot, tab)

    def exec_(dialog):
        dialog.fields["input"].setText("100")
        return True
    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", exec_)
    next(a for a in win._block_menu_for(tab, "compute").actions() if a.text().startswith("Run from this block on, with inputs")).trigger()
    qtbot.waitUntil(lambda: len(tab.runs) == 2 and not tab.running and tab.runs[1].status is not None, timeout=60_000)

    assert (tmp_path / "report.txt").read_text() == "101"
    assert tab.scene.nodes["report"].status == "success"


def test_the_scheduler_service_starts_by_itself_unless_you_paused_it(win, tmp_path, monkeypatch):
    from taskloom.scheduler import paused

    started = []
    monkeypatch.setattr(win, "scheduler_running", lambda: False)
    monkeypatch.setattr(win, "start_scheduler", lambda by_you=False: started.append(by_you))
    tab = win.open_flow(write_flow(tmp_path / "nightly.yaml", """
        blocks:
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          work:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - nightly.start -> work.after
    """))

    tab.doc.move_blocks({"work": (10, 10)})
    win.save(tab)  # a scheduled flow saved: the service starts
    assert started == [False]
    win._start_scheduler_if_needed()  # (and when the editor opens with scheduled flows)
    assert started == [False, False]

    monkeypatch.setattr(win, "stop_scheduler", lambda: None)
    win.pause_scheduler()  # you paused it: it stays off
    assert paused(win.home)
    tab.doc.move_blocks({"work": (20, 20)})
    win.save(tab)
    win._start_scheduler_if_needed()
    assert started == [False, False]
