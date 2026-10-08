"""The editor, driven through its widgets, with real runner processes."""

import os
import textwrap

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QDialogButtonBox, QFileDialog  # noqa: E402

from taskloom.config import Home  # noqa: E402
from taskloom.editor.dialogs import AskDialog, OverlapDialog, RunDialog  # noqa: E402
from taskloom.editor.document import FlowDocument  # noqa: E402
from taskloom.editor.panels import CodeEdit  # noqa: E402
from taskloom.editor.window import MainWindow  # noqa: E402
from taskloom.history import History  # noqa: E402


@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    # The runner processes inherit this; never touch the real credential store.
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    w = MainWindow(Home(tmp_path / "home"))
    qtbot.addWidget(w)
    w.show()
    yield w
    for tab in w.tabs_list():
        for run in tab.runs:
            if run.running:
                run.proc.kill()
                run.proc.waitForFinished(5000)
        tab.doc.dirty = False


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
    flow_path = tmp_path / "doubler.yaml"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(flow_path), ""))
    tab = win.new_flow()

    add_from_palette(win, "Python Code")
    add_from_palette(win, "Python Code")
    first, second = list(tab.doc.blocks)
    tab.scene.nodes[first].setSelected(True)
    set_code(win, "output = 21")
    tab.scene.clearSelection()
    tab.scene.nodes[second].setSelected(True)
    set_code(win, "open('result.txt', 'w').write(str(input * 2))")
    drag_port_to_port(tab, first, "output", second, "input")
    assert tab.doc.edges() == [(first, "output", second, "input")]

    assert win.save(tab)
    saved = tab.doc.data
    assert win.close_tab(tab)
    tab = win.open_flow(flow_path)
    assert tab.doc.data == saved

    win.run_flow(tab)
    wait_done(qtbot, tab)

    assert (tmp_path / "result.txt").read_text() == "42"
    assert {b: n.status for b, n in tab.scene.nodes.items()} == {first: "success", second: "success"}
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
