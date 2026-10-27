"""A Python Code block added in the editor starts with "Run in my Python environment" ticked and with a short
commented starter snippet as its code; flow files, and copied blocks, keep the config they have."""

import os
import re
import sys

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QLabel  # noqa: E402

from taskloom.editor.canvas import BLOCK_MIME  # noqa: E402
from taskloom.editor.panels import CodeEdit  # noqa: E402

ENV_BOX = "Run in my Python environment"


def settle():
    for _ in range(3):
        QApplication.processEvents()


def tree_item(win, title):
    return win.palette_panel.tree.findItems(title, Qt.MatchExactly | Qt.MatchRecursive)[0]


def added(tab, add) -> str:
    """The name of the one block `add()` adds to the flow."""
    before = set(tab.doc.blocks)
    add()
    settle()
    (block,) = set(tab.doc.blocks) - before
    return block


def double_click_in_tree(win, tab, title) -> str:
    """As the user does: double-click the block in the Blocks tree."""
    return added(tab, lambda: win.palette_panel.tree.itemDoubleClicked.emit(tree_item(win, title), 0))


def drop_on_canvas(win, tab, title) -> str:
    """As the user does: drag the block from the Blocks tree onto the middle of the canvas."""
    settle()
    mime = QMimeData()
    mime.setData(BLOCK_MIME, tree_item(win, title).data(0, Qt.UserRole).encode())  # what the tree's drag carries
    viewport = tab.view.viewport()
    at = viewport.rect().center()

    def drag_and_drop():
        enter = QDragEnterEvent(at, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(viewport, enter)
        assert enter.isAccepted()
        QApplication.sendEvent(viewport, QDropEvent(QPointF(at), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))

    return added(tab, drag_and_drop)


def clean(win):
    """Leave no unsaved changes: closing the window at the end of the test must not ask to save them."""
    for tab in win.tabs_list():
        tab.doc.dirty = False


def select(tab, block):
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)
    settle()


def environment_box(win) -> QCheckBox:
    """The selected block's "Run in my Python environment" box in Properties."""
    label = next(w for w in win.properties.findChildren(QLabel) if w.text() == ENV_BOX)
    return next(b for b in win.properties.findChildren(QCheckBox) if b.toolTip() == label.toolTip())


def edit_action(win, text):
    (edit,) = [a.menu() for a in win.menuBar().actions() if a.text().replace("&", "") == "Edit"]
    return next(a for a in edit.actions() if a.text() == text)


def save_as(win, tab, path, monkeypatch) -> dict:
    """File > Save of a new flow, to `path`; returns the flow file as written."""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    assert win.save(tab)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def assert_starter_code(config: dict):
    """A new block's code: the starter snippet, only comments (and blank lines)."""
    code = config.get("code")
    assert isinstance(code, str) and code.strip(), f"a new Python Code block has no starter code: {config}"
    assert all(not line.strip() or line.strip().startswith("#") for line in code.splitlines()), code


def new_python_block_flow(win, tab) -> tuple[str, str]:
    """The user's case: a new flow, a Python Code block added from the tree after its Manual Start, and an
    Inspect block after the Python block's output (it runs only if the code sets output)."""
    python = double_click_in_tree(win, tab, "Python Code")
    show = double_click_in_tree(win, tab, "Inspect")
    tab.doc.connect("start", "start", python, "after")
    tab.doc.connect(python, "output", show, "value")
    return python, show


# --- a new block: ticked, with the starter code, written in the flow file ---------------------------------


def test_a_python_block_double_clicked_in_the_tree_starts_ticked_with_the_starter_code(win, tmp_path, monkeypatch):
    """Spec: a Python Code block added in the editor gets use_environment: true and the starter code written
    into its config, so the flow file says it; Properties shows the box ticked and the code."""
    tab = win.new_flow()
    try:
        block = double_click_in_tree(win, tab, "Python Code")

        config = tab.doc.blocks[block].get("config") or {}
        assert config.get("use_environment") is True, config
        assert_starter_code(config)
        select(tab, block)
        assert environment_box(win).isChecked()
        assert win.properties.findChild(CodeEdit).toPlainText() == config["code"]  # its text, not a placeholder

        saved = save_as(win, tab, tmp_path / "starter.yaml", monkeypatch)["blocks"][block]["config"]
        assert saved.get("use_environment") is True, saved
        assert saved.get("code") == config["code"]
    finally:
        clean(win)


def test_a_python_block_dropped_on_the_canvas_starts_the_same_way(win):
    """Spec: dragged or double-clicked from the block list, a new Python Code block starts the same way."""
    tab = win.new_flow()
    try:
        dropped = drop_on_canvas(win, tab, "Python Code")

        config = tab.doc.blocks[dropped].get("config") or {}
        assert config.get("use_environment") is True, config
        assert_starter_code(config)
        listed = double_click_in_tree(win, tab, "Python Code")
        assert config == tab.doc.blocks[listed].get("config")
    finally:
        clean(win)


# --- the starter snippet --------------------------------------------------------------------------------


def test_the_starter_code_shows_what_the_code_receives_and_sets_without_taskloom_only_helpers(win):
    """Spec: in a few short lines with examples it shows the input variable (by default `input`), the
    dictionary that several connected blocks give (keyed by the source block's name, then input["name"]),
    tables as pandas DataFrames, params, print() to the Log, setting output; and it does not use the ctx, pl
    or Table helpers, which exist only in Taskloom's own Python."""
    tab = win.new_flow()
    try:
        config = tab.doc.blocks[double_click_in_tree(win, tab, "Python Code")].get("config") or {}
    finally:
        clean(win)
    assert_starter_code(config)
    code = config["code"]

    assert len(code.splitlines()) <= 30, code  # a short snippet
    assert re.search(r"""\{\s*["'][A-Za-z_]\w*["']\s*:""", code), code  # a dictionary keyed by a block's name
    assert re.search(r"""\binput\[\s*["'][A-Za-z_]\w*["']\s*\]""", code), code  # then input["load_sales"]
    assert "dict" in code.lower(), code
    assert "pandas" in code and "DataFrame" in code, code
    assert re.search(r"\bparams\b", code), code
    assert "print(" in code and "log" in code.lower(), code
    assert re.search(r"\boutput\s*=", code), code
    for helper in (r"\bctx\s*\.", r"\bpl\s*\.", r"\bTable\s*\("):
        assert not re.search(helper, code), (helper, code)


def test_the_starter_code_runs_as_is_in_your_python_environment(env, win, tmp_path, monkeypatch):
    """Spec: run as is, the starter snippet sets no output and does not fail, in your environment."""
    tab = win.new_flow()
    try:
        python, show = new_python_block_flow(win, tab)
        assert (tab.doc.blocks[python].get("config") or {}).get("use_environment") is True, tab.doc.blocks[python]
        assert_starter_code(tab.doc.blocks[python]["config"])
        flow = tmp_path / "starter.yaml"
        save_as(win, tab, flow, monkeypatch)
    finally:
        clean(win)
    created = env.taskloom("env", "create", "--python", sys.executable, timeout=180)
    assert created.returncode == 0, created.stderr

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stdout + result.stderr
    run_id, status = env.last_run()
    assert status == "success"
    statuses = env.block_status(run_id)
    assert statuses[python] == "success", statuses
    assert statuses[show] == "skipped", statuses  # no output set: nothing reaches the block after it


def test_the_starter_code_runs_as_is_in_taskloom_own_python_once_unticked(env, win, tmp_path, monkeypatch):
    """Spec: run as is, the starter snippet sets no output and does not fail in Taskloom's own Python (the box
    unticked: the flow file no longer says use_environment: true). No environment exists here."""
    tab = win.new_flow()
    try:
        python, show = new_python_block_flow(win, tab)
        select(tab, python)
        box = environment_box(win)
        assert box.isChecked()
        box.click()  # untick it
        assert not box.isChecked()
        flow = tmp_path / "starter.yaml"
        saved = save_as(win, tab, flow, monkeypatch)["blocks"][python]["config"]
    finally:
        clean(win)
    assert saved.get("use_environment") in (None, False), saved
    assert_starter_code(saved)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stdout + result.stderr
    run_id, status = env.last_run()
    assert status == "success"
    statuses = env.block_status(run_id)
    assert statuses[python] == "success", statuses
    assert statuses[show] == "skipped", statuses


# --- what is not a new block keeps its config ------------------------------------------------------------

OLD_STYLE = {"inputs": [], "code": "output = 1"}  # a flow file's block that leaves use_environment out


def old_style_flow(path):
    blocks = {"start": {"type": "trigger.manual", "ui": {"x": -250, "y": 0}},
              "check": {"type": "logic.python", "config": dict(OLD_STYLE), "ui": {"x": 0, "y": 0}},
              "show": {"type": "logic.inspect", "ui": {"x": 250, "y": 0}}}
    path.write_text(yaml.safe_dump({"name": "check", "blocks": blocks,
                                    "edges": ["start.start -> check.after", "check.output -> show.value"]},
                                   sort_keys=False), encoding="utf-8")
    return path


def test_a_flow_files_python_block_without_use_environment_still_runs_in_taskloom_own_python(env, win, tmp_path):
    """Spec: a flow file whose Python Code block leaves use_environment out keeps running in Taskloom's own
    Python (the field's default for files is unchanged); opening and saving it in the editor keeps it so."""
    flow = old_style_flow(tmp_path / "check.yaml")
    tab = win.open_flow(flow)
    try:
        select(tab, "check")
        assert not environment_box(win).isChecked()
        assert win.save(tab)
    finally:
        clean(win)
    assert yaml.safe_load(flow.read_text(encoding="utf-8"))["blocks"]["check"]["config"] == OLD_STYLE

    result = env.taskloom("run", flow)  # no Python environment exists: in it, the block would fail

    assert result.returncode == 0, result.stdout + result.stderr
    run_id, status = env.last_run()
    assert status == "success"
    assert env.block_status(run_id) == {"start": "success", "check": "success", "show": "success"}


def test_a_copied_and_pasted_python_block_keeps_its_config(win, tmp_path):
    """Spec: copy/paste of an existing block keeps that block's config as it is (it is not a new block)."""
    tab = win.open_flow(old_style_flow(tmp_path / "check.yaml"))
    try:
        QApplication.clipboard().setText("")
        select(tab, "check")

        edit_action(win, "Copy").trigger()
        pasted = added(tab, edit_action(win, "Paste").trigger)

        assert tab.doc.blocks[pasted]["type"] == "logic.python"
        assert tab.doc.blocks[pasted].get("config") == OLD_STYLE
        assert tab.doc.blocks["check"]["config"] == OLD_STYLE
    finally:
        clean(win)
