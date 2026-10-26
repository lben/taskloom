"""Editing flows in the editor, through its real widgets: the blocks tree, adding, moving, connecting, renaming,
deleting, copying and pasting blocks, undo and redo, text on the canvas, the Properties, Parameters and Flow
panels, New / Open / Save / Save as / closing, the flows that come back at start and the unsaved work recovered
after a crash, Check flows, and the Settings, Connections and Python environment dialogs. Flows built here are run
with the real `taskloom` command line to show they do what was built."""

import os
import stat
import subprocess
import sys

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,  # noqa: E402
                               QGraphicsPathItem, QLabel, QLineEdit, QMessageBox, QPushButton, QToolButton, QWidget)

from taskloom.editor.canvas import BLOCK_MIME, HEADER_H, NODE_W, EdgeItem  # noqa: E402
from taskloom.editor.panels import CodeEdit  # noqa: E402
from tests.test_editor import add_from_palette, drag_port_to_port  # noqa: E402


# --- helpers ---------------------------------------------------------------------------

def settle():
    """Let pending events run (an opened flow centres itself just after; some rebuilds wait a turn)."""
    for _ in range(3):
        QApplication.processEvents()


def select_only(tab, *blocks):
    tab.scene.clearSelection()
    for block in blocks:
        tab.scene.nodes[block].setSelected(True)
    settle()


def field(win, label: str):
    """The Properties widget on the row labelled `label` (a Path field's text box, not its … button)."""
    body = win.properties.widget()
    for form in body.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            name, value = form.itemAt(row, QFormLayout.LabelRole), form.itemAt(row, QFormLayout.FieldRole)
            if name and value and isinstance(name.widget(), QLabel) and name.widget().text() == label:
                widget = value.widget()
                if type(widget) is QWidget and widget.findChild(QLineEdit):  # a box: a text and its … button
                    return widget.findChild(QLineEdit)
                return widget
    raise AssertionError(f"no field {label!r} in Properties")


def type_in(edit: QLineEdit, text: str):
    """Type into a line and press Enter (what the panels act on)."""
    edit.setText(text)
    edit.editingFinished.emit()


def error_shown(win) -> str:
    """The red problem line of Properties ('' when hidden)."""
    return win.properties.error.text() if not win.properties.error.isHidden() else ""


def menu(win, text: str) -> QAction:
    return next(a for a in win.findChildren(QAction) if a.text() == text)


def port_point(tab, block, port, output: bool):
    return tab.view.mapFromScene(tab.scene.nodes[block].ports[(port, output)].scenePos())


def mouse_drag(tab, a, b):
    viewport = tab.view.viewport()
    QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(viewport, (a + b) / 2)
    QTest.mouseMove(viewport, b)
    QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, b)
    settle()


def save_as(monkeypatch, path):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))


def taskloom(win, *args, cwd=None) -> subprocess.CompletedProcess:
    """The real command line, on the editor's Taskloom folder."""
    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root), "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    return subprocess.run([sys.executable, "-m", "taskloom.cli", *map(str, args)], env=env, cwd=cwd,
                          capture_output=True, text=True, errors="replace", timeout=120)


def saved(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def clean(win):
    """Leave no unsaved changes: closing the window at the end of the test must not ask to save them."""
    for tab in win.tabs_list():
        tab.doc.dirty = False


def visible_blocks(win) -> list[str]:
    tree = win.palette_panel.tree
    return [tree.topLevelItem(i).child(j).text(0) for i in range(tree.topLevelItemCount())
            if not tree.topLevelItem(i).isHidden()
            for j in range(tree.topLevelItem(i).childCount()) if not tree.topLevelItem(i).child(j).isHidden()]


PYTHON = {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}}


def flow_file(path, blocks: dict, edges=(), **top):
    path.write_text(yaml.safe_dump({**top, "blocks": blocks, "edges": list(edges)}, sort_keys=False),
                    encoding="utf-8")
    return path


# --- the blocks tree -------------------------------------------------------------------

def test_searching_the_blocks_tree_shows_the_blocks_whose_name_or_type_matches(win):
    search = win.palette_panel.search
    everything = visible_blocks(win)
    assert {"Send Email", "Run Query", "Python Code", "Text"} <= set(everything)

    search.setText("email")
    assert visible_blocks(win) == ["Send Email"]
    search.setText("DB.RUN")  # its type, in any case
    assert visible_blocks(win) == ["Run Query"]
    search.setText("  text ")  # the Text of Notes too; spaces around are ignored
    assert "Text" in visible_blocks(win)
    search.setText("no block is called this")
    assert visible_blocks(win) == []
    tree = win.palette_panel.tree
    assert all(tree.topLevelItem(i).isHidden() for i in range(tree.topLevelItemCount()))  # no empty folders
    search.clear()
    assert visible_blocks(win) == everything


def test_double_clicking_a_block_adds_it_in_view_each_with_its_own_name(win):
    tab = win.new_flow()
    settle()
    win.palette_panel.search.setText("wait")
    add_from_palette(win, "Wait")
    add_from_palette(win, "Wait")
    tree = win.palette_panel.tree
    folder = tree.findItems("Logic", Qt.MatchStartsWith)[0]
    tree.itemDoubleClicked.emit(folder, 0)  # a folder is not a block: nothing is added

    assert [b for b in tab.doc.blocks if b != "start"] == ["wait", "wait_2"]
    visible = tab.view.mapToScene(tab.view.viewport().rect()).boundingRect()
    for block in ("wait", "wait_2"):
        assert visible.contains(tab.scene.nodes[block].sceneBoundingRect().center())
    assert tab.scene.nodes["wait"].pos() != tab.scene.nodes["wait_2"].pos()  # not on top of each other
    menu(win, "Undo").trigger()
    assert [b for b in tab.doc.blocks if b != "start"] == ["wait"]
    clean(win)


def drag_over_and_drop(tab, mime, at) -> bool:
    """As the mouse does: the drag enters the canvas at `at` (a point of its view), and is dropped there if the
    canvas takes it."""
    enter = QDragEnterEvent(at, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(tab.view.viewport(), enter)
    if enter.isAccepted():
        QApplication.sendEvent(tab.view.viewport(),
                               QDropEvent(QPointF(at), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    settle()
    return enter.isAccepted()


def dragged_from_the_tree(item) -> QMimeData:
    mime = QMimeData()
    mime.setData(BLOCK_MIME, item.data(0, Qt.UserRole).encode())  # what dragging it from the tree carries
    return mime


def test_a_block_dropped_from_the_tree_lands_where_it_is_dropped(win):
    tab = win.new_flow()
    settle()
    tree = win.palette_panel.tree
    item = tree.findItems("Read File", Qt.MatchExactly | Qt.MatchRecursive)[0]
    at = tab.view.viewport().rect().center()

    other = QMimeData()
    other.setText("some text dragged from elsewhere")
    blocks_before = dict(tab.doc.blocks)
    assert not drag_over_and_drop(tab, other, at)
    assert tab.doc.blocks == blocks_before  # not a block: nothing

    assert drag_over_and_drop(tab, dragged_from_the_tree(item), at)

    (added,) = [b for b in tab.doc.blocks if b != "start"]
    assert tab.doc.blocks[added]["type"] == "data.read_file"
    header = tab.scene.nodes[added].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))
    dropped = tab.view.mapToScene(at)
    assert abs(header.x() - dropped.x()) <= 1 and abs(header.y() - dropped.y()) <= 1
    clean(win)


GREET_BLOCK = '''
from taskloom import Block, fields, ports

class Greet(Block):
    type_id = "my.greet"
    title = "Greet"
    description = "Says hello to whoever you name."  # shown when hovering over the block
    inputs = {"name": ports.Any()}
    outputs = {"text": ports.Any()}
    config = {"greeting": fields.Text(default="Hello")}

    def run(self, ctx, name):
        ctx.log(f"greeting {name}")
        return {"text": f"{self.config.greeting}, {name}!"}
'''  # the block of README's "Writing a block"


def test_a_block_of_your_own_is_in_the_tree_under_user_blocks_and_is_added_and_run_like_the_others(win, qtbot,
                                                                                                tmp_path, monkeypatch):
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    blocks = win.home.root / "blocks"
    blocks.mkdir(parents=True, exist_ok=True)
    (blocks / "greet.py").write_text(GREET_BLOCK, encoding="utf-8")
    mine = MainWindow(Home(win.home.root))  # it finds your blocks when it starts
    qtbot.addWidget(mine)
    mine.show()
    tree = mine.palette_panel.tree
    (folder,) = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                 if tree.topLevelItem(i).text(0).startswith("User blocks")]
    assert [folder.child(i).text(0) for i in range(folder.childCount())] == ["Greet"]
    item = folder.child(0)
    assert "Says hello to whoever you name." in item.toolTip(0)  # on hover
    for words in ("greet", "MY.GREET"):  # its name, its type
        mine.palette_panel.search.setText(words)
        assert visible_blocks(mine) == ["Greet"], words
    mine.palette_panel.search.clear()

    tab = mine.new_flow()
    settle()
    at = tab.view.viewport().rect().center()
    assert drag_over_and_drop(tab, dragged_from_the_tree(item), at)  # dropped
    assert tab.doc.blocks["greet"]["type"] == "my.greet"
    menu(mine, "Undo").trigger()
    add_from_palette(mine, "Greet")  # double-clicked
    assert tab.doc.blocks["greet"]["type"] == "my.greet"
    tab.doc.add_block("logic.python", -100, 200, {"inputs": [], "code": "output = 'Ana'"})
    tab.doc.add_block("logic.python", 300, 200, {"outputs": [],
                      "code": "open('greeted.txt', 'w', encoding='utf-8').write(input)"})
    settle()
    select_only(tab, "greet")
    type_in(field(mine, "Greeting"), "Bonjour")
    for line in (("start", "start", "python", "after"), ("python", "output", "greet", "name"),
                 ("greet", "text", "python_2", "input")):
        tab.doc.connect(*line)
    save_as(monkeypatch, tmp_path / "greeting.yaml")
    assert mine.save(tab)
    clean(mine)

    assert saved(tmp_path / "greeting.yaml")["blocks"]["greet"]["config"] == {"greeting": "Bonjour"}
    result = taskloom(mine, "run", tmp_path / "greeting.yaml", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "greeted.txt").read_text(encoding="utf-8") == "Bonjour, Ana!"


# --- connecting ------------------------------------------------------------------------

def test_dragging_from_an_input_back_to_an_output_connects_them(win):
    tab = win.new_flow()
    a = tab.doc.add_block("logic.python", 0, 0)
    b = tab.doc.add_block("logic.python", 300, 0)
    settle()

    mouse_drag(tab, port_point(tab, b, "input", False), port_point(tab, a, "output", True))

    assert (a, "output", b, "input") in tab.doc.edges()
    assert len(tab.scene.edge_items) == 1
    clean(win)


def test_a_drag_that_ends_on_an_output_on_its_own_block_or_on_empty_canvas_connects_nothing(win):
    tab = win.new_flow()
    a = tab.doc.add_block("logic.python", 0, 0)
    before_b = yaml.safe_dump(tab.doc.data)
    b = tab.doc.add_block("logic.python", 300, 0)
    settle()
    data = yaml.safe_dump(tab.doc.data)

    mouse_drag(tab, port_point(tab, a, "output", True), port_point(tab, b, "output", True))  # output to output
    mouse_drag(tab, port_point(tab, a, "output", True), port_point(tab, a, "input", False))  # onto itself
    empty = tab.view.mapFromScene(QPointF(150, 600))
    mouse_drag(tab, port_point(tab, a, "output", True), empty)  # let go on the canvas

    assert yaml.safe_dump(tab.doc.data) == data
    leftovers = [i for i in tab.scene.items() if isinstance(i, QGraphicsPathItem) and not isinstance(i, EdgeItem)]
    assert leftovers == []  # the dashed line that followed the mouse is gone
    menu(win, "Undo").trigger()  # no step was added for them: Undo takes back the block added before
    assert yaml.safe_dump(tab.doc.data) == before_b
    clean(win)


def test_dragging_the_same_connection_twice_keeps_one(win):
    tab = win.new_flow()
    a = tab.doc.add_block("logic.python", 0, 0)
    b = tab.doc.add_block("logic.python", 300, 0)
    settle()

    drag_port_to_port(tab, a, "output", b, "input")
    drag_port_to_port(tab, a, "output", b, "input")
    drag_port_to_port(tab, a, "done", b, "after")
    drag_port_to_port(tab, a, "done", b, "after")

    assert sorted(tab.doc.data["edges"]) == [f"{a}.done -> {b}.after", f"{a}.output -> {b}.input"]
    clean(win)


def test_the_errors_of_several_blocks_dragged_into_one_input_all_reach_that_one_handler(win, tmp_path):
    flow = flow_file(tmp_path / "handled.yaml", {
        "start": {"type": "trigger.manual", "ui": {"x": -250, "y": 0}},
        "load": {"type": "data.read_file", "config": {"path": "missing.csv"}, "ui": {"x": 0, "y": 0}},
        "compute": {"type": "logic.python", "config": {"inputs": [], "code": "output = 1 / 0"}, "ui": {"x": 0, "y": 200}},
        "handler": {"type": "logic.python", "config": {
            "inputs": ["errors"], "outputs": [],
            "code": "errors = errors if isinstance(errors, list) else [errors]\n"
                    "open('handled.txt', 'w').write(','.join(sorted(e['block'] for e in errors)))"},
            "ui": {"x": 300, "y": 100}},
    }, ["start.start -> load.after", "start.start -> compute.after"])
    tab = win.open_flow(flow)
    settle()

    drag_port_to_port(tab, "load", "error", "handler", "errors")
    drag_port_to_port(tab, "compute", "error", "handler", "errors")  # a Python Code input keeps them all
    assert {("load", "error", "handler", "errors"), ("compute", "error", "handler", "errors")} <= set(tab.doc.edges())
    assert win.save(tab)

    taskloom(win, "run", flow, cwd=tmp_path)
    assert (tmp_path / "handled.txt").read_text() == "compute,load"
    clean(win)


def test_the_table_dragged_last_into_write_files_input_is_the_one_written(win, tmp_path):
    flow = flow_file(tmp_path / "replace.yaml", {
        "start": {"type": "trigger.manual", "ui": {"x": -250, "y": 100}},
        "first": {"type": "logic.python", "config": {"inputs": [], "code": "output = pl.DataFrame({'n': [1]})"},
                  "ui": {"x": 0, "y": 0}},
        "second": {"type": "logic.python", "config": {"inputs": [], "code": "output = pl.DataFrame({'n': [2]})"},
                   "ui": {"x": 0, "y": 200}},
        "write": {"type": "data.write_file", "config": {"path": "out.csv"}, "ui": {"x": 300, "y": 100}},
    }, ["start.start -> first.after", "start.start -> second.after"])
    tab = win.open_flow(flow)
    settle()

    drag_port_to_port(tab, "first", "output", "write", "table")
    drag_port_to_port(tab, "second", "output", "write", "table")  # takes one table: replaces the first line
    assert [e for e in tab.doc.edges() if e[2] == "write"] == [("second", "output", "write", "table")]
    assert win.save(tab)

    result = taskloom(win, "run", flow, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out.csv").read_text().split() == ["n", "2"]
    clean(win)


# --- moving and deleting -----------------------------------------------------------------

def test_dragging_selected_blocks_moves_them_together_and_undo_puts_them_back(win, tmp_path):
    flow = flow_file(tmp_path / "moved.yaml", {
        "a": {**PYTHON, "ui": {"x": 0, "y": 0}},
        "b": {"type": "logic.python", "config": {"code": "pass"}, "ui": {"x": 250, "y": 0}},
    }, ["a.output -> b.input"])
    tab = win.open_flow(flow)
    settle()
    tab.view.zoom_to(100)  # (one screen pixel is one point of the flow: the move is exact)
    select_only(tab, "a", "b")
    header = tab.view.mapFromScene(tab.scene.nodes["a"].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2)))
    by = tab.view.mapFromScene(QPointF(40, 60)) - tab.view.mapFromScene(QPointF(0, 0))

    mouse_drag(tab, header, header + by)

    assert [(s["ui"]["x"], s["ui"]["y"]) for s in tab.doc.blocks.values()] == [(40, 60), (290, 60)]
    (edge,) = tab.scene.edge_items  # the line follows its blocks
    assert edge.path().pointAtPercent(0) == tab.scene.nodes["a"].ports[("output", True)].scenePos()
    assert win.save(tab)
    assert [s["ui"] for s in saved(flow)["blocks"].values()] == [{"x": 40, "y": 60}, {"x": 290, "y": 60}]
    menu(win, "Undo").trigger()
    assert [(s["ui"]["x"], s["ui"]["y"]) for s in tab.doc.blocks.values()] == [(0, 0), (250, 0)]
    assert tab.scene.nodes["b"].pos() == QPointF(250, 0)
    clean(win)


def test_delete_removes_a_selected_line_and_backspace_a_selected_block_with_its_lines(win, qtbot, tmp_path):
    flow = flow_file(tmp_path / "trim.yaml", {
        "a": {**PYTHON, "ui": {"x": 0, "y": 0}},
        "b": {"type": "logic.python", "config": {"code": "pass"}, "ui": {"x": 250, "y": 0}},
        "c": {"type": "logic.python", "config": {"code": "pass"}, "ui": {"x": 500, "y": 0}},
    }, ["a.output -> b.input", "b.output -> c.input", "a.done -> c.after"])
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.open_flow(flow)
    settle()
    original = yaml.safe_dump(tab.doc.data)
    tab.scene.clearSelection()
    next(e for e in tab.scene.edge_items if e.text == "a.done -> c.after").setSelected(True)
    tab.view.setFocus()

    QTest.keyClick(tab.view, Qt.Key_Delete)
    assert set(tab.doc.blocks) == {"a", "b", "c"}
    assert tab.doc.data["edges"] == ["a.output -> b.input", "b.output -> c.input"]

    select_only(tab, "b")
    tab.view.setFocus()
    QTest.keyClick(tab.view, Qt.Key_Backspace)
    assert set(tab.doc.blocks) == {"a", "c"} and tab.doc.data["edges"] == []
    assert set(tab.scene.nodes) == {"a", "c"} and tab.scene.edge_items == []

    menu(win, "Undo").trigger()
    menu(win, "Undo").trigger()
    assert yaml.safe_dump(tab.doc.data) == original and len(tab.scene.edge_items) == 3
    clean(win)


# --- renaming --------------------------------------------------------------------------

def test_a_block_renamed_in_properties_keeps_its_connections_in_the_saved_flow_that_runs(win, tmp_path):
    flow = flow_file(tmp_path / "renamed.yaml", {
        "start": {"type": "trigger.manual"},
        "python": {"type": "logic.python", "config": {"inputs": [], "code": "output = 'from the renamed block'"}},
        "save": {"type": "logic.python", "config": {"outputs": [], "code": "open('got.txt', 'w').write(input)"}},
    }, ["start.start -> python.after", "python.output -> save.input"])
    tab = win.open_flow(flow)
    select_only(tab, "python")

    type_in(field(win, "Name"), "load_sales")

    assert set(tab.doc.edges()) == {("start", "start", "load_sales", "after"), ("load_sales", "output", "save", "input")}
    assert len(tab.scene.edge_items) == 2
    assert win.save(tab)
    assert taskloom(win, "run", flow, cwd=tmp_path).returncode == 0
    assert (tmp_path / "got.txt").read_text() == "from the renamed block"

    menu(win, "Undo").trigger()  # the old name, with its connections
    assert "python" in tab.doc.blocks and ("python", "output", "save", "input") in tab.doc.edges()
    clean(win)


def test_a_block_name_that_is_taken_reserved_or_not_a_name_is_refused_with_why(win):
    tab = win.new_flow()
    tab.doc.add_block("logic.python", 0, 0)
    tab.doc.add_block("logic.wait", 300, 0)
    tab.doc.connect("python", "output", "wait", "value")
    data = yaml.safe_dump(tab.doc.data)

    for name, why in (("wait", "already exists"), ("my block", "not a valid block name"),
                      ("error", "not a valid block name"), ("2nd", "not a valid block name"),
                      ("", "not a valid block name"), ("día", "not a valid block name")):
        select_only(tab, "python")
        type_in(field(win, "Name"), name)
        assert why in error_shown(win), name
        assert yaml.safe_dump(tab.doc.data) == data, name
    clean(win)


# --- copy and paste ----------------------------------------------------------------------

def test_pasted_blocks_get_new_names_and_only_the_connections_between_them(win, tmp_path):
    flow = flow_file(tmp_path / "copied.yaml", {
        "start": {"type": "trigger.manual", "ui": {"x": -250, "y": 0}},
        "load": {"type": "logic.python", "config": {"inputs": [], "code": "output = 'é ✓'"}, "ui": {"x": 0, "y": 0}},
        "save": {"type": "logic.python", "config": {"code": "pass"}, "ui": {"x": 250, "y": 0}},
        "after": {"type": "logic.python", "config": {"code": "pass"}, "ui": {"x": 500, "y": 0}},
    }, ["start.start -> load.after", "load.output -> save.input", "save.output -> after.input"])
    tab = win.open_flow(flow)
    select_only(tab, "load", "save")

    win.copy_blocks()
    win.paste_blocks()
    settle()

    assert set(tab.doc.blocks) == {"start", "load", "save", "after", "load_2", "save_2"}
    assert tab.doc.blocks["load_2"]["config"] == tab.doc.blocks["load"]["config"]
    assert tab.doc.blocks["load_2"]["ui"] != tab.doc.blocks["load"]["ui"]  # not on top of the original
    new_edges = {e for e in tab.doc.edges() if "load_2" in e or "save_2" in e}
    assert new_edges == {("load_2", "output", "save_2", "input")}  # not from start, not into after
    assert sorted(tab.scene.selected_block_ids()) == ["load_2", "save_2"]  # the copies, to move them
    menu(win, "Undo").trigger()  # one step
    assert set(tab.doc.blocks) == {"start", "load", "save", "after"}
    clean(win)


def test_blocks_pasted_into_another_flow_keep_the_names_that_are_free_there(win, tmp_path):
    source = win.open_flow(flow_file(tmp_path / "source.yaml", {
        "load": {**PYTHON, "ui": {"x": 0, "y": 0}},
        "python": {"type": "logic.python", "config": {"code": "pass"}, "ui": {"x": 250, "y": 0}},
    }, ["load.output -> python.input"]))
    original = yaml.safe_dump(source.doc.data)
    select_only(source, "load", "python")
    win.copy_blocks()
    target = win.new_flow()
    target.doc.add_block("logic.python", 0, 300)  # a "python" is already there
    settle()

    win.paste_blocks()

    assert set(target.doc.blocks) == {"start", "python", "load", "python_2"}
    assert ("load", "output", "python_2", "input") in target.doc.edges()
    assert yaml.safe_dump(source.doc.data) == original and not source.doc.dirty
    clean(win)


def test_pasting_text_that_is_not_blocks_changes_nothing(win):
    tab = win.new_flow()
    tab.doc.add_block("logic.python", 0, 0)
    settle()
    data = yaml.safe_dump(tab.doc.data)
    clipboard = QApplication.clipboard()

    for text in ("", "hello", "- a\n- b", "key: [unclosed", "taskloom_blocks: not blocks", "edges: [x]"):
        clipboard.setText(text)
        win.paste_blocks()
        settle()
        assert yaml.safe_dump(tab.doc.data) == data, text
    menu(win, "Undo").trigger()  # no step was added for them: Undo takes back the block added before
    assert list(tab.doc.blocks) == ["start"]

    tab.scene.clearSelection()
    clipboard.setText("kept")
    win.copy_blocks()  # nothing selected: the clipboard keeps what it had
    assert clipboard.text() == "kept"
    clean(win)


# --- undo and redo -----------------------------------------------------------------------

def canvas_matches(tab) -> bool:
    """The canvas shows the flow as it is: its blocks where they are, its lines and its texts."""
    doc, scene = tab.doc, tab.scene
    return (set(scene.nodes) == set(doc.blocks) and {e.text for e in scene.edge_items} == set(doc.data["edges"])
            and set(scene.note_items) == set(doc.notes)
            and all((n.pos().x(), n.pos().y()) == (doc.blocks[b]["ui"]["x"], doc.blocks[b]["ui"]["y"])
                    for b, n in scene.nodes.items()))


def test_undo_and_redo_step_back_and_forth_through_every_kind_of_change(win):
    import copy

    tab = win.new_flow()
    settle()
    undo, redo = menu(win, "Undo"), menu(win, "Redo")
    undo.trigger()  # nothing to undo yet
    assert list(tab.doc.blocks) == ["start"] and not tab.doc.dirty
    states = [copy.deepcopy(tab.doc.data)]

    def step(what, action):
        action()
        settle()
        assert tab.doc.data != states[-1], what
        states.append(copy.deepcopy(tab.doc.data))

    def move(item_pos, by=QPointF(30, 20)):
        a = tab.view.mapFromScene(item_pos())
        mouse_drag(tab, a, a + (tab.view.mapFromScene(by) - tab.view.mapFromScene(QPointF(0, 0))))

    def set_code(text):
        select_only(tab, "python")
        editor = win.properties.findChild(CodeEdit)
        editor.setPlainText(text)
        editor.editing_finished.emit()

    def text_value(name, value):
        select_only(tab, "python")
        type_in(field(win, name), value)

    step("add a block", lambda: add_from_palette(win, "Python Code"))
    step("move it", lambda: move(lambda: tab.scene.nodes["python"].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))))
    step("connect", lambda: drag_port_to_port(tab, "start", "start", "python", "after"))
    step("its code", lambda: set_code("result = 2"))
    step("its outputs", lambda: text_value("Outputs", "result"))
    step("its retry", lambda: (select_only(tab, "python"), field(win, "Policy").setCurrentText("fixed")))
    step("its timeout", lambda: text_value("Timeout", "5m"))
    step("rename it", lambda: text_value("Name", "compute"))
    step("paste a copy", lambda: (select_only(tab, "compute"), win.copy_blocks(), win.paste_blocks()))
    step("delete the copy", lambda: (select_only(tab, "compute_2"), menu(win, "Delete").trigger()))
    step("add a text", lambda: win.add_text(tab, QPointF(-280, -250)))  # (clear of the blocks)
    note = next(iter(tab.doc.notes))
    step("make it bold", lambda: next(b for b in win.properties.findChildren(QCheckBox) if b.text() == "Bold").setChecked(True))
    step("move the text", lambda: move(lambda: tab.scene.note_items[note].mapToScene(
        tab.scene.note_items[note]._text_rect().center())))
    step("add a parameter", lambda: next(b for b in win.parameters.findChildren(QPushButton) if b.text() == "Add").click())
    step("name the flow", lambda: type_in(win.flow_settings.name, "Daily"))
    assert canvas_matches(tab)

    for expected in reversed(states[:-1]):
        undo.trigger()
        settle()
        assert tab.doc.data == expected and canvas_matches(tab)
    for expected in states[1:]:
        redo.trigger()
        settle()
        assert tab.doc.data == expected and canvas_matches(tab)
    assert win.flow_settings.name.text() == "Daily" and win.parameters.table.rowCount() == 1
    clean(win)


def test_undoing_a_field_change_shows_the_old_value_in_properties(win):
    tab = win.new_flow()
    tab.doc.add_block("data.write_file", 0, 0)
    select_only(tab, "write_file")
    type_in(field(win, "Path"), "first.csv")
    type_in(field(win, "Path"), "second.csv")

    menu(win, "Undo").trigger()
    settle()

    assert tab.doc.blocks["write_file"]["config"]["path"] == "first.csv"
    assert field(win, "Path").text() == "first.csv"
    clean(win)


def test_the_undo_and_redo_keys_work_on_the_canvas_and_paste_while_writing_a_text_pastes_words(win, qtbot):
    from PySide6.QtGui import QKeySequence

    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.new_flow()
    tab.doc.add_block("logic.python", 0, 0)
    settle()
    tab.view.setFocus()

    QTest.keySequence(tab.view, QKeySequence(QKeySequence.Undo))  # Ctrl+Z (on a Mac, Cmd+Z)
    assert list(tab.doc.blocks) == ["start"]
    QTest.keySequence(tab.view, QKeySequence(QKeySequence.Redo))
    assert list(tab.doc.blocks) == ["start", "python"]

    select_only(tab, "python")
    win.copy_blocks()  # blocks on the clipboard
    QApplication.clipboard().setText("words to paste ")  # then words copied elsewhere
    note = tab.scene.add_note_at(QPointF(0, -200))
    settle()
    item = tab.scene.note_items[note]
    QTest.mouseDClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier,
                      tab.view.mapFromScene(item.mapToScene(item.boundingRect().center())))
    assert item.editing
    QTest.keySequence(tab.view, QKeySequence(QKeySequence.Paste))
    QTest.keyClick(tab.view, Qt.Key_Escape)  # done writing
    qtbot.waitUntil(lambda: "words to paste" in tab.doc.notes[note]["text"], timeout=3_000)
    assert list(tab.doc.blocks) == ["start", "python"]  # no block pasted
    clean(win)


# --- Properties: each kind of field ---------------------------------------------------------

def test_a_flow_built_with_each_kind_of_field_saves_them_as_their_types_and_runs(win, tmp_path, monkeypatch):
    tab = win.new_flow()
    settle()
    for title in ("Python Code", "Write File", "Chart", "Wait"):
        add_from_palette(win, title)
    tab.doc.move_blocks({"python": (0, 0), "write_file": (300, -120), "chart": (300, 120), "wait": (600, -120)})
    settle()

    select_only(tab, "python")
    code = win.properties.findChild(CodeEdit)
    code.setPlainText("sales = pl.DataFrame({'day': ['mon', 'tue'], 'amount': [3, 5]})")  # code
    code.editing_finished.emit()
    type_in(field(win, "Outputs"), "sales")  # a list of names
    field(win, "Run in my Python environment").setChecked(True)  # yes / no
    assert tab.doc.blocks["python"]["config"]["use_environment"] is True
    field(win, "Run in my Python environment").setChecked(False)  # back to its default: not written
    select_only(tab, "write_file")
    type_in(field(win, "Path"), "out/sales report.csv")  # a file path
    field(win, "Format").setCurrentText("csv")  # a choice
    select_only(tab, "chart")
    field(win, "Kind").setCurrentText("line")
    type_in(field(win, "X"), "day")  # text
    type_in(field(win, "Y"), "amount")  # a list
    type_in(field(win, "Width"), "320")  # whole numbers
    type_in(field(win, "Height"), "200")
    select_only(tab, "wait")
    type_in(field(win, "Seconds"), "0.1")  # a number
    for line in (("start", "start", "python", "after"), ("python", "sales", "write_file", "table"),
                 ("python", "sales", "chart", "table"), ("write_file", "done", "wait", "after")):
        drag_port_to_port(tab, *line)
    assert error_shown(win) == ""
    save_as(monkeypatch, tmp_path / "built.yaml")
    assert win.save(tab)

    blocks = saved(tmp_path / "built.yaml")["blocks"]
    assert blocks["python"]["config"] == {"code": "sales = pl.DataFrame({'day': ['mon', 'tue'], 'amount': [3, 5]})",
                                          "outputs": ["sales"]}
    assert blocks["write_file"]["config"] == {"path": "out/sales report.csv", "format": "csv"}
    assert blocks["chart"]["config"] == {"kind": "line", "x": "day", "y": ["amount"], "width": 320, "height": 200}
    assert blocks["wait"]["config"] == {"seconds": 0.1}

    result = taskloom(win, "run", tmp_path / "built.yaml", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "sales report.csv").read_text().split() == ["day,amount", "mon,3", "tue,5"]
    clean(win)


def test_a_number_field_given_text_or_a_value_too_small_says_so(win):
    default = win.registry.get("db.run_query").config["chunk_size"].default
    tab = win.new_flow()
    tab.doc.add_block("db.run_query", 0, 0, {"connection": "warehouse", "sql": "select 1"})
    tab.doc.add_block("logic.wait", 300, 0)
    select_only(tab, "run_query")
    chunk = field(win, "Chunk size")
    assert chunk.placeholderText() == str(default)  # its default, while empty

    type_in(chunk, "many")
    assert "Chunk size" in error_shown(win) and "chunk_size" not in tab.doc.blocks["run_query"]["config"]
    type_in(field(win, "Chunk size"), "0")
    assert "1 or more" in error_shown(win)
    type_in(field(win, "Chunk size"), "1000")
    assert tab.doc.blocks["run_query"]["config"]["chunk_size"] == 1000
    type_in(field(win, "Chunk size"), "")  # emptied: its default again
    assert "chunk_size" not in tab.doc.blocks["run_query"]["config"]

    select_only(tab, "wait")
    type_in(field(win, "Seconds"), "1,5")
    assert "Seconds" in error_shown(win) and "seconds" not in tab.doc.blocks["wait"]["config"]
    type_in(field(win, "Seconds"), "1.5")
    assert tab.doc.blocks["wait"]["config"]["seconds"] == 1.5 and error_shown(win) == ""
    clean(win)


def test_a_text_field_keeps_backslashes_accents_and_brackets_exactly(win, tmp_path):
    flow = flow_file(tmp_path / "paths.yaml", {"read": {"type": "data.read_file", "config": {"path": "x.csv"}}})
    tab = win.open_flow(flow)
    select_only(tab, "read")
    typed = r"C:\Users\José\Ventes été\sales [EU] #1.csv"

    type_in(field(win, "Path"), typed)
    type_in(field(win, "Separator"), ";")
    assert win.save(tab)

    assert "José" in flow.read_bytes().decode("utf-8")  # written as UTF-8 text, readable as written
    assert saved(flow)["blocks"]["read"]["config"] == {"path": typed, "separator": ";"}
    win.close_tab(tab)
    reopened = win.open_flow(flow)
    select_only(reopened, "read")
    assert field(win, "Path").text() == typed
    clean(win)


def test_input_and_output_names_that_are_not_allowed_are_refused_and_keep_the_connections(win):
    tab = win.new_flow()
    tab.doc.add_block("logic.python", 0, 0)
    tab.doc.add_block("logic.python", 300, 0)
    tab.doc.connect("python", "output", "python_2", "input")
    data = yaml.safe_dump(tab.doc.data)

    for label, names, why in (("Outputs", "1st", "names"), ("Outputs", "a, a", "duplicate"),
                              ("Outputs", "done", "every block already has"), ("Outputs", "error", "every block already has"),
                              ("Outputs", "my value", "names"), ("Inputs", "after", "every block already has")):
        select_only(tab, "python" if label == "Outputs" else "python_2")
        type_in(field(win, label), names)
        assert why in error_shown(win), names
        assert yaml.safe_dump(tab.doc.data) == data, names
    clean(win)


def test_a_choice_set_back_to_its_default_is_not_written(win):
    tab = win.new_flow()
    tab.doc.add_block("data.read_file", 0, 0)
    select_only(tab, "read_file")
    fmt = field(win, "Format")
    assert fmt.currentText() == "auto"

    fmt.setCurrentText("parquet")
    assert tab.doc.blocks["read_file"]["config"]["format"] == "parquet"
    field(win, "Format").setCurrentText("auto")
    assert "format" not in tab.doc.blocks["read_file"]["config"]
    clean(win)


def test_a_connection_field_lists_the_connections_of_its_kind_and_names_a_missing_one(win, tmp_path):
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "connections.yaml").write_text(
        "appserver: {kind: ssh, host: server01.example.com, username: me}\n"
        "mailer: {kind: smtp, host: mail.example.com, sender: me@example.com}\n", encoding="utf-8")
    flow = flow_file(tmp_path / "conn.yaml", {
        "cmd": {"type": "servers.ssh_command", "config": {"connection": "gone", "command": "uptime"}}})
    tab = win.open_flow(flow)
    select_only(tab, "cmd")
    combo = field(win, "Connection").findChild(QComboBox)

    assert [combo.itemText(i) for i in range(combo.count())] == ["", "appserver", "gone (missing)"]  # SSH only
    assert combo.currentText() == "gone (missing)"
    combo.setCurrentIndex(combo.findData("appserver"))
    combo.activated.emit(combo.currentIndex())

    assert tab.doc.blocks["cmd"]["config"]["connection"] == "appserver"
    clean(win)


# --- Properties: retry and timeout -------------------------------------------------------------

FLAKY = ("import pathlib\np = pathlib.Path('tries.txt')\nn = int(p.read_text()) + 1 if p.exists() else 1\n"
         "p.write_text(str(n))\nif n < 3:\n    raise RuntimeError(f'attempt {n} failed')\noutput = n\n")


def test_retry_and_timeout_set_in_properties_are_what_the_run_does(win, tmp_path):
    from taskloom.history import History

    flow = flow_file(tmp_path / "retried.yaml", {
        "start": {"type": "trigger.manual"},
        "flaky": {"type": "logic.python", "config": {"inputs": [], "code": FLAKY}},
        "slow": {"type": "logic.wait", "config": {"seconds": 60}},
    }, ["start.start -> flaky.after", "start.start -> slow.after"])
    tab = win.open_flow(flow)
    select_only(tab, "flaky")
    field(win, "Policy").setCurrentText("fixed")
    type_in(field(win, "Max attempts"), "3")
    type_in(field(win, "Delay"), "0s")
    select_only(tab, "slow")
    type_in(field(win, "Timeout"), "1s")
    assert win.save(tab)
    blocks = saved(flow)["blocks"]
    assert blocks["flaky"]["retry"] == {"policy": "fixed", "max_attempts": 3, "delay": "0s"}
    assert blocks["slow"]["timeout"] == "1s"

    taskloom(win, "run", flow, cwd=tmp_path)
    history = History(win.home.history_db)
    try:
        (run,) = history.runs(str(flow.resolve()))
        results = history.blocks(run["id"])
    finally:
        history.close()
    assert results["flaky"]["status"] == "success" and results["flaky"]["attempts"] == 3
    assert results["slow"]["status"] == "failed" and "timed out" in results["slow"]["error"]
    clean(win)


TIMED_FLAKY = ("import pathlib, time\np = pathlib.Path('tries.txt')\n"
               "tries = p.read_text().split() if p.exists() else []\ntries.append(repr(time.time()))\n"
               "p.write_text(' '.join(tries))\nif len(tries) < 3:\n    raise RuntimeError(f'attempt {len(tries)} failed')\n"
               "output = len(tries)\n")


def test_an_exponential_retry_typed_in_properties_is_saved_with_numbers_and_waits_as_set(win, tmp_path):
    from taskloom.history import History

    flow = flow_file(tmp_path / "backoff.yaml", {
        "start": {"type": "trigger.manual"},
        "flaky": {"type": "logic.python", "config": {"inputs": [], "code": TIMED_FLAKY}},
    }, ["start.start -> flaky.after"])
    tab = win.open_flow(flow)
    select_only(tab, "flaky")
    field(win, "Policy").setCurrentText("exponential")
    settle()
    # Waits of 0.2 s, then 0.2 s × 50 = 10 s, but never more than 0.3 s; up to half of each taken off at random.
    for label, value in (("Max attempts", "3"), ("Initial", "200ms"), ("Factor", "50"), ("Max delay", "300ms"),
                         ("Jitter", "0.5")):
        type_in(field(win, label), value)
        assert error_shown(win) == "", (label, value)
    assert win.save(tab)

    retry = saved(flow)["blocks"]["flaky"]["retry"]
    assert retry == {"policy": "exponential", "max_attempts": 3, "initial": "200ms", "factor": 50, "max_delay": "300ms",
                     "jitter": 0.5}
    assert all(isinstance(retry[k], (int, float)) for k in ("max_attempts", "factor", "jitter"))  # numbers, not text
    result = taskloom(win, "run", flow, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    history = History(win.home.history_db)
    try:
        (run,) = history.runs(str(flow.resolve()))
        assert history.blocks(run["id"])["flaky"]["attempts"] == 3
    finally:
        history.close()
    first, second, third = map(float, (tmp_path / "tries.txt").read_text().split())
    assert 0.1 - 0.02 <= second - first  # the first wait: at least half of 0.2 s
    assert 0.15 - 0.02 <= third - second < 3  # the second: at most 0.3 s (Max delay), not 10 s
    clean(win)


def test_retry_and_timeout_values_that_do_not_make_sense_are_refused_with_why(win):
    tab = win.new_flow()
    tab.doc.add_block("logic.python", 0, 0)
    select_only(tab, "python")
    field(win, "Policy").setCurrentText("exponential")
    assert tab.doc.blocks["python"]["retry"] == {"policy": "exponential"}

    for label, value, why in (("Max attempts", "0", "max_attempts"), ("Max attempts", "two", "two"),
                              ("Initial", "soon", "invalid duration"), ("Jitter", "2", "jitter"),
                              ("Factor", "0.5", "factor"), ("Timeout", "an hour", "invalid duration")):
        type_in(field(win, label), value)
        assert why in error_shown(win), (label, value)
        assert tab.doc.blocks["python"].get("retry") == {"policy": "exponential"}, (label, value)
        assert "timeout" not in tab.doc.blocks["python"], (label, value)
        type_in(field(win, label), "")
    field(win, "Policy").setCurrentText("none")
    assert "retry" not in tab.doc.blocks["python"]
    clean(win)


# --- Properties: when a Schedule block runs ----------------------------------------------------

def when_text(win) -> str:
    return " ".join(label.text() for label in win.properties.findChildren(QLabel))


def choose(win, item: str):
    box = next(c for c in win.properties.findChildren(QComboBox) if c.findText(item) >= 0)
    box.setCurrentIndex(box.findText(item))
    box.activated.emit(box.currentIndex())
    settle()


def test_every_few_minutes_writes_every_and_every_hour_and_every_month_write_their_cron_lines(win, tmp_path,
                                                                                               monkeypatch):
    from PySide6.QtCore import QTime
    from PySide6.QtWidgets import QSpinBox, QTimeEdit

    tab = win.new_flow()
    tab.doc.add_block("trigger.schedule", 0, 200)
    select_only(tab, "schedule")

    def config():
        return tab.doc.blocks["schedule"].get("config", {})

    def cron():
        return config().get("cron")

    choose(win, "Every few minutes or hours")  # from when the scheduler service picks it up, not on the clock
    assert config().get("every") == "15 minutes" and cron() is None and "Every 15 minutes" in when_text(win)
    choose(win, "5 minutes")
    assert config().get("every") == "5 minutes" and cron() is None and "Every 5 minutes" in when_text(win)
    choose(win, "Every hour")
    minute = field(win, "At minute")
    assert isinstance(minute, QSpinBox)
    minute.setValue(30)
    minute.editingFinished.emit()
    assert cron() == "30 * * * *" and "every" not in config() and "Every hour at :30" in when_text(win)
    choose(win, "Every month")
    day = field(win, "On day")
    day.setValue(15)
    day.editingFinished.emit()
    at = win.properties.findChild(QTimeEdit)
    at.setTime(QTime(6, 45))
    at.editingFinished.emit()
    assert cron() == "45 6 15 * *" and "Day 15 of every month at 06:45" in when_text(win)

    save_as(monkeypatch, tmp_path / "monthly.yaml")
    assert win.save(tab)
    assert win.home.scheduled_flows() == [(tmp_path / "monthly.yaml").resolve()]
    assert (tab.next_run.day, tab.next_run.hour, tab.next_run.minute) == (15, 6, 45)
    clean(win)


def test_one_time_then_repeating_again_and_a_wrong_cron_line_says_what_is_wrong(win, qtbot):
    tab = win.new_flow()
    tab.doc.add_block("trigger.schedule", 0, 200)
    select_only(tab, "schedule")

    choose(win, "One time")
    config = tab.doc.blocks["schedule"]["config"]
    assert set(config) == {"at"} and "Once, on" in when_text(win)
    choose(win, "Repeating")
    assert "at" not in tab.doc.blocks["schedule"].get("config", {}) and "Not set yet" in when_text(win)

    choose(win, "Custom (a cron line)")
    line = next(e for e in win.properties.findChildren(QLineEdit) if e.toolTip().startswith("minute hour"))
    type_in(line, "61 * * * *")
    preview = when_text(win)
    assert "61" in preview and "Next:" not in preview  # says what is wrong, no next runs
    qtbot.waitUntil(lambda: tab.scene.nodes["schedule"].countdown == "needs a time", timeout=3_000)  # under it
    clean(win)


def click_a_day_in_the_calendar_of(on, day: int):
    """Open the calendar of a date and time box with its arrow, as the mouse does, and click `day` of the month
    it shows."""
    from PySide6.QtWidgets import QCalendarWidget, QStyle, QStyleOptionComboBox, QTableView

    option = QStyleOptionComboBox()
    option.initFrom(on)
    option.editable = True
    option.subControls = QStyle.SC_All
    arrow = on.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, on).center()
    QTest.mouseMove(on, arrow)
    QTest.mouseClick(on, Qt.LeftButton, Qt.NoModifier, arrow)
    settle()
    calendar = on.calendarWidget()
    assert calendar.isVisible()
    days = calendar.findChild(QTableView)
    model = days.model()
    first_row = 0 if calendar.horizontalHeaderFormat() == QCalendarWidget.NoHorizontalHeader else 1  # day names
    first_col = 0 if calendar.verticalHeaderFormat() == QCalendarWidget.NoVerticalHeader else 1  # week numbers
    (cell,) = [model.index(r, c) for r in range(first_row, model.rowCount())
               for c in range(first_col, model.columnCount()) if str(model.data(model.index(r, c))) == str(day)]
    QTest.mouseClick(days.viewport(), Qt.LeftButton, Qt.NoModifier, days.visualRect(cell).center())
    settle()


def test_a_one_time_schedule_typed_or_picked_in_its_calendar_is_saved_and_scheduled_for_that_time(win, tmp_path,
                                                                                                  monkeypatch):
    import datetime as dt

    from PySide6.QtWidgets import QDateTimeEdit

    tab = win.new_flow()
    tab.doc.add_block("trigger.schedule", 0, 200)
    tab.doc.add_block("logic.python", 300, 200, {"inputs": [], "code": "pass"})
    tab.doc.connect("schedule", "start", "python", "after")
    select_only(tab, "schedule")
    choose(win, "One time")
    when = (dt.datetime.now() + dt.timedelta(days=40)).replace(hour=15, minute=30, second=0, microsecond=0)

    on = field(win, "On")
    assert isinstance(on, QDateTimeEdit)
    on.setFocus()
    QTest.keyClick(on, Qt.Key_A, Qt.ControlModifier)  # (all of it, to type over)
    QTest.keyClicks(on, f"{when:%Y-%m-%d %H:%M}")
    QTest.keyClick(on, Qt.Key_Return)
    settle()

    assert tab.doc.blocks["schedule"]["config"] == {"at": f"{when:%Y-%m-%d %H:%M}"}
    assert f"Once, on {when:%A %d %B %Y at %H:%M}" in when_text(win) and f"Next: {when:%a %d %b %H:%M}" in when_text(win)
    save_as(monkeypatch, tmp_path / "once.yaml")
    assert win.save(tab)
    assert saved(tmp_path / "once.yaml")["blocks"]["schedule"]["config"] == {"at": f"{when:%Y-%m-%d %H:%M}"}
    assert win.home.scheduled_flows() == [(tmp_path / "once.yaml").resolve()]
    assert tab.next_run == when

    day = 15 if when.day != 15 else 16  # another day of that month (still to come: it is a month from now)
    settle()  # (saving shows Properties again)
    click_a_day_in_the_calendar_of(field(win, "On"), day)

    picked = when.replace(day=day)
    assert tab.doc.blocks["schedule"]["config"] == {"at": f"{picked:%Y-%m-%d %H:%M}"}  # the time is kept
    assert f"Once, on {picked:%A %d %B %Y at %H:%M}" in when_text(win)
    assert win.save(tab)
    assert saved(tmp_path / "once.yaml")["blocks"]["schedule"]["config"] == {"at": f"{picked:%Y-%m-%d %H:%M}"}
    assert tab.next_run == picked
    clean(win)


def test_an_inactive_schedule_is_not_scheduled_and_its_misfire_choice_is_saved(win, qtbot, tmp_path):
    flow = flow_file(tmp_path / "paused.yaml", {
        "nightly": {"type": "trigger.schedule", "config": {"cron": "0 2 * * *"}},
        "work": {"type": "logic.python", "config": {"inputs": [], "code": "pass"}},
    }, ["nightly.start -> work.after"])
    tab = win.open_flow(flow)
    assert win.save(tab)
    assert win.home.scheduled_flows() == [flow.resolve()]
    select_only(tab, "nightly")

    field(win, "Misfire").setCurrentText("catch_up")
    field(win, "Active").setChecked(False)
    assert win.save(tab)

    assert saved(flow)["blocks"]["nightly"]["config"] == {"cron": "0 2 * * *", "misfire": "catch_up", "active": False}
    assert win.home.scheduled_flows() == []
    qtbot.waitUntil(lambda: tab.scene.nodes["nightly"].countdown == "not active", timeout=3_000)  # under it
    clean(win)


# --- Parameters ---------------------------------------------------------------------------

def param_rows(win) -> list[tuple]:
    t = win.parameters.table
    return [(t.item(r, 0).text(), t.cellWidget(r, 1).currentText(), t.cellWidget(r, 2).currentText(),
             t.item(r, 3).text(), t.item(r, 4).text()) for r in range(t.rowCount())]


def add_param(win, name, type_, kind, value):
    next(b for b in win.parameters.findChildren(QPushButton) if b.text() == "Add").click()
    table = win.parameters.table
    row = table.rowCount() - 1
    table.item(row, 0).setText(name)
    table.cellWidget(row, 1).setCurrentText(type_)
    table.cellWidget(row, 2).setCurrentText(kind)
    table.item(row, 3).setText(value)
    settle()


def test_parameters_added_in_the_panel_preview_on_a_chosen_day_and_reach_the_run(win, tmp_path):
    from PySide6.QtCore import QDate

    flow = flow_file(tmp_path / "params.yaml", {
        "start": {"type": "trigger.manual"},
        "use": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code":
                "open('params.txt', 'w', encoding='utf-8').write("
                "f\"{params['day']}|{params['twice']}|{params['label']}\")"}},
    }, ["start.start -> use.after"])
    tab = win.open_flow(flow)
    win.parameters.as_of.setDate(QDate(2026, 10, 5))  # a Monday

    add_param(win, "day", "date", "expression", "today() - bdays(1)")
    add_param(win, "n", "int", "value", "21")
    add_param(win, "twice", "int", "expression", "n * 2")
    add_param(win, "label", "text", "value", "Ventes été ✓")

    assert [row[4] for row in param_rows(win)] == ["2026-10-02 (Fri)", "21", "42", "Ventes été ✓"]
    assert tab.doc.data["params"] == {
        "day": {"type": "date", "expr": "today() - bdays(1)"}, "n": {"type": "int", "value": "21"},
        "twice": {"type": "int", "expr": "n * 2"}, "label": {"type": "text", "value": "Ventes été ✓"}}
    assert win.save(tab)

    result = taskloom(win, "run", flow, "--param", "day=2026-09-30", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "params.txt").read_text(encoding="utf-8") == "2026-09-30|42|Ventes été ✓"

    table = win.parameters.table
    table.setCurrentCell(1, 0)
    next(b for b in win.parameters.findChildren(QPushButton) if b.text() == "Remove").click()
    assert list(tab.doc.data["params"]) == ["day", "twice", "label"]
    assert "n" in [row[4] for row in param_rows(win)][1]  # twice: n is gone, it says so
    clean(win)


def test_a_parameter_given_a_name_that_is_not_allowed_is_flagged_and_not_lost(win):
    tab = win.new_flow()
    tab.doc.add_block("logic.python", 0, 0)
    add_param(win, "region", "text", "value", "north, south")

    win.parameters.table.item(0, 0).setText("my region")  # not a name: a space
    settle()
    assert "'my region' is not a valid, unique name" in win.parameters.note.text()

    tab.doc.move_blocks({"python": (50, 50)})  # you go on editing the flow
    settle()
    rows = [row[:4] for row in param_rows(win)]
    clean(win)
    assert rows == [("my region", "text", "value", "north, south")]  # still there, to put right


# --- Flow -------------------------------------------------------------------------------

def test_the_flows_name_description_calendar_time_zone_and_overlap_are_saved(win, tmp_path, monkeypatch):
    tab = win.new_flow()
    panel = win.flow_settings
    type_in(panel.name, "Daily sales")
    assert win.tabs.tabText(win.tabs.indexOf(tab)).startswith("Daily sales (unsaved)")
    type_in(panel.description, "Totals of the day before, for the team")
    panel.calendar.setCurrentText("US")
    panel.calendar.lineEdit().editingFinished.emit()
    type_in(panel.timezone, "Europe/Madrid")
    panel.overlap.setCurrentIndex(panel.overlap.findData("queue"))
    panel.overlap.activated.emit(panel.overlap.currentIndex())
    save_as(monkeypatch, tmp_path / "daily.yaml")
    assert win.save(tab)

    data = saved(tmp_path / "daily.yaml")
    assert {k: data.get(k) for k in ("name", "description", "calendar", "timezone", "overlap")} == {
        "name": "Daily sales", "description": "Totals of the day before, for the team", "calendar": "US",
        "timezone": "Europe/Madrid", "overlap": "queue"}
    assert taskloom(win, "validate", tmp_path / "daily.yaml").returncode == 0
    panel.overlap.setCurrentIndex(panel.overlap.findData("skip"))  # the default: not written
    panel.overlap.activated.emit(panel.overlap.currentIndex())
    assert "overlap" not in tab.doc.data
    clean(win)


def test_a_time_zone_that_does_not_exist_is_named_when_checking_the_flow(win, tmp_path):
    flow = flow_file(tmp_path / "zoned.yaml", {"start": {"type": "trigger.manual"},
                                                "work": {"type": "logic.python", "config": {"inputs": [], "code": "pass"}}},
                     ["start.start -> work.after"])
    win.open_flow(flow)
    type_in(win.flow_settings.timezone, "Mars/Olympus_Mons")

    menu(win, "Validate").trigger()

    assert [kind for kind, _ in win.message_boxes] == ["warning"]
    assert "Mars/Olympus_Mons" in win.message_boxes[0][1][1]
    win.message_boxes.clear()
    clean(win)


# --- text on the canvas ---------------------------------------------------------------------

def test_a_texts_words_bold_underline_font_and_size_are_saved_and_come_back(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QAbstractSpinBox

    tab = win.new_flow()
    win.add_text(tab, QPointF(0, -150))
    settle()
    (note,) = tab.doc.notes
    words = win.properties.findChild(CodeEdit)
    words.setPlainText("Ventes du jour — été\nsecond line")
    words.editing_finished.emit()
    for box in ("Bold", "Underline"):
        next(b for b in win.properties.findChildren(QCheckBox) if b.text() == box).setChecked(True)
    fonts = next(c for c in win.properties.findChildren(QComboBox) if c.findText("(the usual font)") >= 0)
    family = fonts.itemText(1) if fonts.count() > 1 else ""  # (a machine can have no fonts to choose)
    if family:
        fonts.setCurrentIndex(1)
        fonts.activated.emit(1)
    size = next(s for s in win.properties.findChildren(QAbstractSpinBox) if s.suffix() == " pt")
    size.setValue(18)
    size.editingFinished.emit()
    save_as(monkeypatch, tmp_path / "noted.yaml")
    assert win.save(tab)
    win.close_tab(tab)

    tab = win.open_flow(tmp_path / "noted.yaml")
    item = tab.scene.note_items[note]
    assert item.toPlainText() == "Ventes du jour — été\nsecond line"
    assert item.font().bold() and item.font().underline() and not item.font().italic()
    assert item.font().pointSizeF() == 18
    if family:
        assert tab.doc.notes[note]["font"] == family
    assert taskloom(win, "validate", tmp_path / "noted.yaml").returncode == 0  # text does not run
    clean(win)


def test_dragging_a_text_moves_it_and_undo_moves_it_back(win, tmp_path):
    flow = flow_file(tmp_path / "titled.yaml", {}, notes={"note_1": {"text": "Title", "x": 0, "y": 0}})
    tab = win.open_flow(flow)
    settle()
    tab.view.zoom_to(100)  # (one screen pixel is one point of the flow: the move is exact)
    item = tab.scene.note_items["note_1"]
    a = tab.view.mapFromScene(item.mapToScene(item._text_rect().center()))
    by = tab.view.mapFromScene(QPointF(50, 30)) - tab.view.mapFromScene(QPointF(0, 0))

    mouse_drag(tab, a, a + by)

    assert (tab.doc.notes["note_1"]["x"], tab.doc.notes["note_1"]["y"]) == (50, 30)
    assert win.save(tab)
    assert saved(flow)["notes"]["note_1"] == {"text": "Title", "x": 50, "y": 30}
    menu(win, "Undo").trigger()
    assert (tab.doc.notes["note_1"]["x"], tab.doc.notes["note_1"]["y"]) == (0, 0)
    clean(win)


def right_click(tab, at: QPointF, choose: str = "") -> list[str]:
    """Right-click the canvas at `at` (a point of the flow): the items of the menu that opens ([]: none opens);
    the one called `choose` is clicked (otherwise the menu is closed)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QMenu

    opened, finished, tries = [], [], []

    def look():  # (a menu waits for you inside the right-click)
        if finished:
            return
        menu = QApplication.activePopupWidget()
        if menu is None:
            tries.append(1)
            if len(tries) < 250:  # up to 5 s
                QTimer.singleShot(20, look)
            else:  # (never leave a menu waiting for a click)
                for widget in QApplication.topLevelWidgets():
                    if isinstance(widget, QMenu) and widget.isVisible():
                        widget.close()
            return
        opened.append([a.text() for a in menu.actions() if a.text()])
        item = next((a for a in menu.actions() if a.text() == choose and a.isEnabled()), None)
        if item is not None:
            QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(item).center())
        if menu.isVisible():
            menu.close()

    settle()  # (an opened flow centres itself just after)
    tab.view.centerOn(at)  # scrolled into view, as you would (the canvas can be narrow)
    point = tab.view.mapFromScene(at)
    assert tab.view.viewport().rect().contains(point)
    QTimer.singleShot(0, look)
    # What the window sends the canvas when you right-click it.
    QApplication.sendEvent(tab.view.viewport(), QContextMenuEvent(QContextMenuEvent.Mouse, point,
                                                                  tab.view.viewport().mapToGlobal(point)))
    finished.append(1)
    settle()
    popup = QApplication.activePopupWidget()  # (one that does not wait)
    if popup is not None:
        opened.append([a.text() for a in popup.actions() if a.text()])
        popup.close()
    assert len(opened) <= 1, opened
    return opened[0] if opened else []


def test_right_clicking_an_empty_spot_offers_add_text_here_and_puts_a_text_there(win, tmp_path):
    flow = flow_file(tmp_path / "menu.yaml", {
        "a": {**PYTHON, "ui": {"x": 0, "y": 0}},
        "b": {"type": "logic.python", "config": {"code": "pass"}, "ui": {"x": 400, "y": 0}},
    }, ["a.output -> b.input"], notes={"note_1": {"text": "A remark", "x": 0, "y": -200}})
    tab = win.open_flow(flow)
    settle()
    (line,) = tab.scene.edge_items
    note = tab.scene.note_items["note_1"]
    on_a_block = tab.scene.nodes["a"].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2))
    on_the_line = line.path().pointAtPercent(0.5)
    on_the_text = note.mapToScene(note._text_rect().center())

    block_menu = right_click(tab, on_a_block, choose="Add text here")
    assert block_menu and "Add text here" not in block_menu  # the block's own menu
    for where, at in (("a line", on_the_line), ("a text", on_the_text)):
        assert "Add text here" not in right_click(tab, at, choose="Add text here"), where
    assert tab.doc.notes == {"note_1": {"text": "A remark", "x": 0, "y": -200}} and not tab.doc.dirty

    empty = QPointF(200, 300)
    assert "Add text here" in right_click(tab, empty, choose="Add text here")

    (added,) = set(tab.doc.notes) - {"note_1"}
    pixel = 1 / tab.view.transform().m11()  # (the spot is a pixel of the screen: one pixel of the view, in points)
    x, y = tab.doc.notes[added]["x"], tab.doc.notes[added]["y"]
    assert abs(x - 200) <= pixel + 1 and abs(y - 300) <= pixel + 1  # where you right-clicked
    assert tab.scene.note_items[added].isSelected()  # selected, to write in it
    assert win.properties.findChild(CodeEdit) is not None  # Properties shows its words to write
    assert win.save(tab)
    assert added in saved(flow)["notes"]
    menu(win, "Undo").trigger()
    assert set(tab.doc.notes) == {"note_1"}
    clean(win)


# --- New, Open, Save, Save as, closing ---------------------------------------------------------

def answer(monkeypatch, button) -> list:
    """Answer the next questions with `button`; returns what they asked."""
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[1:3]) or button))
    return asked


def test_opening_a_flow_already_open_shows_its_tab_and_cancelling_open_opens_nothing(win, tmp_path, monkeypatch):
    flow = flow_file(tmp_path / "once.yaml", {"python": PYTHON})
    chosen = [str(flow)]
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (chosen[0], "")))
    open_ = menu(win, "Open…")

    open_.trigger()
    win.new_flow()
    open_.trigger()  # again: back to its tab
    chosen[0] = ""
    open_.trigger()  # the dialog cancelled

    assert [t.doc.title for t in win.tabs_list()] == ["once.yaml", "untitled (unsaved)"]
    assert win.current.doc.path == flow.resolve()
    clean(win)


def test_a_file_that_is_not_a_flow_is_refused_with_a_message_and_opens_no_tab(win, tmp_path):
    files = {"list.yaml": "- a\n- b\n".encode(), "broken.yaml": b"blocks: {a: [unclosed\n",
             "ansi.yaml": "name: Ventas año\n".encode("cp1252"), "image.yaml": bytes(range(256))}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        assert win.open_flow(tmp_path / name) is None, name

    assert win.tabs_list() == []
    assert [(kind, title) for kind, (title, _) in win.message_boxes] == [("warning", "Cannot open flow")] * len(files)
    win.message_boxes.clear()


def test_save_as_writes_a_new_file_the_tab_then_saves_to(win, tmp_path, monkeypatch):
    first = flow_file(tmp_path / "first.yaml", {"python": PYTHON})
    original = first.read_text(encoding="utf-8")
    tab = win.open_flow(first)
    tab.doc.move_blocks({"python": (10, 10)})
    save_as(monkeypatch, tmp_path / "copy" / "second.yaml")
    (tmp_path / "copy").mkdir()

    assert win.save(tab, ask=True)
    assert first.read_text(encoding="utf-8") == original  # left as it was
    assert win.tabs.tabText(win.tabs.indexOf(tab)) == "second.yaml" and not tab.doc.dirty
    tab.doc.move_blocks({"python": (20, 20)})
    assert win.tabs.tabText(win.tabs.indexOf(tab)) == "second.yaml *" and win.windowTitle().endswith("second.yaml *")
    menu(win, "Save").trigger()
    assert saved(tmp_path / "copy" / "second.yaml")["blocks"]["python"]["ui"] == {"x": 20, "y": 20}
    assert first.read_text(encoding="utf-8") == original
    save_as(monkeypatch, "")  # Save as… cancelled
    assert not win.save(tab, ask=True) and tab.doc.path == (tmp_path / "copy" / "second.yaml").resolve()
    clean(win)


def test_closing_a_flow_with_unsaved_changes_asks_and_does_what_you_answer(win, tmp_path, monkeypatch):
    flow = flow_file(tmp_path / "work.yaml", {"python": PYTHON})
    original = flow.read_text(encoding="utf-8")

    def changed_tab():
        tab = win.open_flow(flow)
        tab.doc.move_blocks({"python": (99, 99)})
        return tab

    asked = answer(monkeypatch, QMessageBox.Cancel)
    tab = changed_tab()
    assert not win.close_tab(tab) and tab in win.tabs_list() and tab.doc.dirty
    assert asked == [("Unsaved changes", "Save changes to work.yaml?")]

    answer(monkeypatch, QMessageBox.Discard)
    assert win.close_tab(tab) and tab not in win.tabs_list()
    assert flow.read_text(encoding="utf-8") == original

    answer(monkeypatch, QMessageBox.Save)
    assert win.close_tab(changed_tab())
    assert saved(flow)["blocks"]["python"]["ui"] == {"x": 99, "y": 99}

    untitled = win.new_flow()
    untitled.doc.add_block("logic.python", 0, 0)
    save_as(monkeypatch, "")  # Save, then the file dialog cancelled: it stays open
    assert not win.close_tab(untitled) and untitled in win.tabs_list()
    clean(win)


def test_a_flow_with_accents_and_symbols_is_saved_as_utf8_and_runs(win, tmp_path, monkeypatch):
    tab = win.new_flow()
    type_in(win.flow_settings.name, "Ventes été")
    tab.doc.add_block("logic.python", 0, 0, {"inputs": [], "outputs": [],
                                             "code": "open('out.txt', 'w', encoding='utf-8').write('café ✓ 日本')"})
    tab.doc.connect("start", "start", "python", "after")
    win.add_text(tab, QPointF(0, -100))
    settle()
    tab.doc.set_note_value(next(iter(tab.doc.notes)), "text", "Résumé — 日本")
    save_as(monkeypatch, tmp_path / "ventes.yaml")
    assert win.save(tab)

    text = (tmp_path / "ventes.yaml").read_bytes().decode("utf-8")
    assert "Ventes été" in text and "Résumé — 日本" in text  # readable in the file, not escaped
    data = tab.doc.data
    win.close_tab(tab)
    assert win.open_flow(tmp_path / "ventes.yaml").doc.data == data
    assert taskloom(win, "run", tmp_path / "ventes.yaml", cwd=tmp_path).returncode == 0
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "café ✓ 日本"
    clean(win)


# --- the flows that come back, and unsaved work ------------------------------------------------

def test_the_flows_open_when_the_editor_closes_open_again_next_time(win, qtbot, tmp_path, monkeypatch):
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    folder = tmp_path / "Données été"
    folder.mkdir()
    flows = [flow_file(folder / "ventes.yaml", {"python": PYTHON}), flow_file(tmp_path / "other.yaml", {"python": PYTHON}),
             flow_file(tmp_path / "deleted.yaml", {"python": PYTHON})]
    for flow in flows:
        win.open_flow(flow)
    win.new_flow().doc.add_block("logic.python", 0, 0)  # never saved: nothing to reopen
    answer(monkeypatch, QMessageBox.Discard)
    win.close()
    settle()
    assert win.tabs_list() == [] and (win.home.root / "editor.ini").exists()
    flows[2].unlink()  # gone since

    given = flow_file(tmp_path / "given.yaml", {"python": PYTHON})  # `taskloom editor given.yaml other.yaml`

    again = MainWindow(Home(win.home.root))
    qtbot.addWidget(again)
    again.restore_session([given, flows[1]])

    assert [t.doc.path for t in again.tabs_list()] == [flows[0].resolve(), flows[1].resolve(), given.resolve()]
    assert win.message_boxes == []


def wait_for(qtbot, check, timeout=10_000):
    qtbot.waitUntil(lambda: bool(check()), timeout=timeout)


def test_unsaved_changes_are_copied_to_the_recovery_folder_until_saved_or_discarded(win, qtbot, tmp_path, monkeypatch):
    flow = flow_file(tmp_path / "work.yaml", {"python": PYTHON})
    recovery = win.home.root / "recovery"
    tab = win.open_flow(flow)

    tab.doc.move_blocks({"python": (70, 70)})
    wait_for(qtbot, lambda: list(recovery.glob("*.yaml")))
    (copy,) = recovery.glob("*.yaml")
    entry = yaml.safe_load(copy.read_text(encoding="utf-8"))
    assert entry["path"] == str(flow.resolve()) and entry["data"] == tab.doc.data
    assert saved(flow)["blocks"]["python"].get("ui") is None  # the flow file itself is not written

    assert win.save(tab)
    assert not list(recovery.glob("*.yaml"))

    tab.doc.move_blocks({"python": (80, 80)})
    wait_for(qtbot, lambda: list(recovery.glob("*.yaml")))
    answer(monkeypatch, QMessageBox.Discard)
    assert win.close_tab(tab)
    qtbot.wait(1700)  # longer than the autosave waits
    assert not list(recovery.glob("*.yaml"))  # discarded: nothing to bring back


ENDED_EDITOR = """
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
from PySide6.QtWidgets import QApplication
from taskloom.config import Home
from taskloom.editor.window import MainWindow
MainWindow._check_after_update = lambda self: None  # (it would wait for a click)
MainWindow.start_scheduler = lambda self, by_you=False: None  # (no scheduler service from a test)
app = QApplication([])
home = Path(sys.argv[1])
w = MainWindow(Home(home))
w.show()
w.restore_session(sys.argv[2:])  # `taskloom editor a.yaml b.yaml`
changes = []
for tab in w.tabs_list():  # an unsaved change to each saved flow
    change = f"unsaved change to {tab.doc.path.stem}"
    tab.doc.add_block("logic.python", 300, 0, {"inputs": [], "code": f"output = {change!r}"})
    changes.append(change)
w.new_flow().doc.add_block("logic.python", 0, 0, {"inputs": [], "code": "output = 'never saved'"})
changes.append("never saved")


def copied():  # every change is in a file of the recovery folder
    files = [f for f in (home / "recovery").iterdir() if f.is_file()]
    return all(any(c in f.read_text(encoding="utf-8", errors="replace") for f in files) for c in changes)


end = time.monotonic() + 30
while not copied() and time.monotonic() < end:
    app.processEvents()
    time.sleep(0.05)
os._exit(1 if copied() else 3)  # ends there and then, without closing: as a crash does
"""


def end_an_editor_abruptly(home, tmp_path, *flows):
    """An editor (another process) opens `flows`, changes each, starts a new flow, and ends without closing
    once its unsaved changes are in the recovery folder."""
    script = tmp_path / "ended_editor.py"
    script.write_text(ENDED_EDITOR, encoding="utf-8")
    ended = subprocess.run([sys.executable, str(script), str(home), *map(str, flows)], cwd=tmp_path,
                           capture_output=True, text=True, errors="replace", timeout=120)
    assert ended.returncode == 1, ended.stderr  # (3: its changes never reached the recovery folder)


def start_the_editor_again(win, qtbot):
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    again = MainWindow(Home(win.home.root))
    qtbot.addWidget(again)
    again.restore_session()
    said = list(win.message_boxes)  # (its message boxes are recorded with those of `win`)
    win.message_boxes.clear()
    return again, said


def codes_in(tab) -> list[str]:
    return [s["config"]["code"] for s in tab.doc.blocks.values() if s["type"] == "logic.python"]


def test_after_an_editor_ends_abruptly_a_saved_flows_unsaved_changes_come_back_in_its_own_tab(win, qtbot, tmp_path,
                                                                                               monkeypatch):
    flow = flow_file(tmp_path / "work.yaml", {"start": {"type": "trigger.manual"}, "python": PYTHON},
                     ["start.start -> python.after"])
    original = flow.read_bytes()
    end_an_editor_abruptly(win.home.root, tmp_path, flow)
    assert flow.read_bytes() == original  # the file itself is written only when you save

    again, said = start_the_editor_again(win, qtbot)

    tabs = again.tabs_list()
    shown = [(again.tabs.tabText(again.tabs.indexOf(t)), t.doc.dirty) for t in tabs]
    clean(again)
    work = [t for t in tabs if t.doc.path == flow.resolve()]
    assert len(work) == 1 and len(tabs) == 2  # in the flow's own tab, not a second one
    (work,), (untitled,) = work, [t for t in tabs if t.doc.path is None]
    assert shown == [("work.yaml *", True), (f"{untitled.doc.title} *", True)]  # changed, not saved
    assert codes_in(work) == ["output = 1", "output = 'unsaved change to work'"]
    assert codes_in(untitled) == ["output = 'never saved'"]
    [(kind, (title, text))] = said
    assert title == "Work recovered" and "did not close normally" in text and "crash.log" in text
    assert "work.yaml" in text and untitled.doc.title in text
    assert flow.read_bytes() == original  # still not written: it is yours to save or not

    assert again.save(work)
    result = taskloom(again, "run", flow, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    clean(again)


def test_a_recovered_copy_that_cannot_be_read_is_skipped_and_the_others_come_back(win, qtbot, tmp_path):
    first = flow_file(tmp_path / "first.yaml", {"python": PYTHON})
    second = flow_file(tmp_path / "second.yaml", {"python": PYTHON})
    end_an_editor_abruptly(win.home.root, tmp_path, first, second)
    (damaged,) = [f for f in (win.home.root / "recovery").iterdir()
                  if f.is_file() and "unsaved change to second" in f.read_text(encoding="utf-8", errors="replace")]
    damaged.write_bytes(b"\0" * damaged.stat().st_size)  # what a power cut can leave of a file being written

    again, said = start_the_editor_again(win, qtbot)

    tabs = {t.doc.path: t for t in again.tabs_list()}
    dirty = {path: t.doc.dirty for path, t in tabs.items()}
    clean(again)
    assert set(tabs) == {first.resolve(), second.resolve(), None}
    assert dirty == {first.resolve(): True, second.resolve(): False, None: True}  # second: as saved
    assert codes_in(tabs[first.resolve()]) == ["output = 1", "output = 'unsaved change to first'"]
    assert codes_in(tabs[second.resolve()]) == ["output = 1"]
    [(kind, (title, text))] = said
    assert title == "Work recovered" and "first.yaml" in text and "second.yaml" not in text


# --- Check flows ----------------------------------------------------------------------------

LEFTOVER = {  # a connection from an output the block no longer has (e.g. after renaming it): Check flows fixes it
    "start": {"type": "trigger.manual"},
    "python": {"type": "logic.python", "config": {"inputs": [], "outputs": ["result"], "code": "result = 1"}},
    "after": {"type": "logic.python", "config": {"code": "pass"}},
}
LEFTOVER_EDGES = ["start.start -> python.after", "python.output -> after.after"]


def when_dialog_shows(win, do):
    """When a dialog opens (it waits for you inside the call that opens it), do(dialog). It looks for one for
    about 5 s, and stops when the window goes."""
    from PySide6.QtCore import QTimer

    tries = []

    def act():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            if len(tries) < 250:
                tries.append(1)
                QTimer.singleShot(20, win, act)
            return
        try:
            do(dialog)
        finally:
            if dialog.isVisible():  # (something went wrong: do not leave it waiting for a click)
                dialog.reject()

    QTimer.singleShot(0, win, act)


def press(dialog, button: str):
    (pressed,) = [b for b in dialog.findChildren(QPushButton) if b.text().replace("&", "") == button]
    pressed.click()


def press_when_shown(win, button: str, seen: list):
    """When a dialog opens: note it, then press `button`."""
    when_dialog_shows(win, lambda dialog: (seen.append(dialog), press(dialog, button)))


def test_check_flows_with_nothing_to_fix_says_so_and_changes_nothing(win, tmp_path):
    flow = flow_file(tmp_path / "fine.yaml", {"start": {"type": "trigger.manual"}, "python": PYTHON},
                     ["start.start -> python.after"])
    original = flow.read_bytes()
    win.open_flow(flow)
    seen = []
    press_when_shown(win, "Close", seen)

    menu(win, "Check flows…").trigger()

    (dialog,) = seen
    assert "Nothing to fix." in " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert dialog.details.toPlainText() == f"{flow.resolve()}: OK"
    assert flow.read_bytes() == original and not (win.home.root / "backups").exists()
    assert win.message_boxes == []
    clean(win)


def test_check_flows_backs_up_and_fixes_scheduled_flows_that_are_not_open(win, tmp_path):
    flows = []
    for folder in ("one", "two"):  # two flows with the same file name: each its own backup
        (tmp_path / folder).mkdir()
        flows.append(flow_file(tmp_path / folder / "daily.yaml", LEFTOVER, LEFTOVER_EDGES))
        win.home.set_scheduled(flows[-1].resolve(), True)
    originals = [f.read_bytes() for f in flows]
    seen = []
    press_when_shown(win, "Back up and fix", seen)

    menu(win, "Check flows…").trigger()

    assert all("can fix:" in seen[0].details.toPlainText() and str(f.resolve()) in seen[0].details.toPlainText()
               for f in flows)
    for flow in flows:
        assert saved(flow)["edges"] == ["start.start -> python.after"]
    backups = sorted((win.home.root / "backups").iterdir())
    assert len(backups) == 2 and sorted(b.read_bytes() for b in backups) == sorted(originals)
    [(kind, (title, text))] = win.message_boxes
    assert title == "Check flows" and text.count("daily.yaml (backup:") == 2
    win.message_boxes.clear()


def test_closing_check_flows_without_fixing_leaves_the_flows_as_they_are(win, tmp_path):
    flow = flow_file(tmp_path / "old.yaml", LEFTOVER, LEFTOVER_EDGES)
    original = flow.read_bytes()
    tab = win.open_flow(flow)
    seen = []
    press_when_shown(win, "Close", seen)

    menu(win, "Check flows…").trigger()

    assert "can fix:" in seen[0].details.toPlainText()
    assert flow.read_bytes() == original and not (win.home.root / "backups").exists()
    assert tab.doc.data["edges"] == LEFTOVER_EDGES and win.message_boxes == []
    clean(win)


def test_check_flows_lists_what_needs_you_and_offers_no_fix_when_it_can_fix_nothing(win, tmp_path):
    flow = flow_file(tmp_path / "unconnected.yaml", {
        "nightly": {"type": "trigger.schedule", "config": {"cron": "0 2 * * *"}},  # starts nothing
        "work": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "pass"}},
    })
    original = flow.read_bytes()
    tab = win.open_flow(flow)
    seen = []

    def look_and_close(dialog):
        seen.append((" ".join(label.text() for label in dialog.findChildren(QLabel)), dialog.details.toPlainText(),
                     [b.text().replace("&", "") for b in dialog.findChildren(QPushButton)]))
        press(dialog, "Close")

    when_dialog_shows(win, look_and_close)
    menu(win, "Check flows…").trigger()

    [(words, details, buttons)] = seen
    assert "needs you" in words and "Nothing it can fix by itself" in words
    assert f"{flow.resolve()}: " in details
    assert "needs you: nightly is not connected to anything" in details and "can fix:" not in details
    assert "Back up and fix" not in buttons
    assert flow.read_bytes() == original and not (win.home.root / "backups").exists()
    assert not tab.doc.dirty and win.message_boxes == []
    clean(win)


def test_the_first_start_of_a_new_version_checks_the_flows_and_shows_only_what_needs_doing(win, qtbot, tmp_path):
    from taskloom import __version__
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    old = flow_file(tmp_path / "old.yaml", LEFTOVER, LEFTOVER_EDGES)
    original = old.read_bytes()
    win.home.set_scheduled(old.resolve(), True)
    win.settings.setValue("checked_version", "0.0.1")  # an older version was the last to start
    seen = []

    def start():
        window = MainWindow(Home(win.home.root))
        qtbot.addWidget(window)
        when_dialog_shows(window, lambda dialog: (seen.append(dialog.findChild(QLabel).text()), press(dialog, "Close")))
        window.show()
        qtbot.wait(300)
        return window

    start()
    qtbot.waitUntil(lambda: bool(seen), timeout=5_000)
    assert seen[0].startswith(f"Taskloom {__version__} checked your open and scheduled flows.")
    assert old.read_bytes() == original  # closed without fixing: as it was

    seen.clear()
    start()  # the same version again: no check
    assert seen == []


# --- Settings and Connections ------------------------------------------------------------------

def labelled(root, label: str):
    """The field on the form row labelled `label` in a dialog."""
    for form in root.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            name, value = form.itemAt(row, QFormLayout.LabelRole), form.itemAt(row, QFormLayout.FieldRole)
            if name and value and isinstance(name.widget(), QLabel) and name.widget().text() == label:
                widget = value.widget()
                return widget.findChild(QLineEdit) if type(widget) is QWidget and widget.findChild(QLineEdit) else widget
    raise AssertionError(f"no field {label!r}")


def test_settings_saved_in_the_dialog_are_the_settings_taskloom_uses_and_cancel_keeps_them(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QSpinBox, QTableWidget, QTableWidgetItem

    from taskloom.editor.dialogs import SettingsDialog

    # On Windows, Save also writes (or removes) the Startup script of the box "Start the scheduler service when I
    # log in": in a Startup folder of this test, never the real one of the person running the tests.
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "settings.yaml").write_text("python_env: envs/mine\n", encoding="utf-8")
    seen = []

    def fill_in_and_save(dialog):
        seen.append(dialog)
        labelled(dialog, "Default calendar").setText("US")
        dialog.keep_runs_for.setValue(7)
        dialog.keep_runs_unit.setCurrentText("months")
        spin = labelled(dialog, "Scheduled runs at once")
        assert isinstance(spin, QSpinBox)
        spin.setValue(2)
        labelled(dialog, "Java folder").setText(r"C:\Program Files\Java\jdk-21")
        labelled(dialog, "Email notifications to").setText("a@example.com; b@example.com")
        labelled(dialog, "Send them with").setCurrentText("mailer")
        for box in dialog.findChildren(QCheckBox):
            if box.text() in ("a run fails", "a run succeeds", "runs are skipped"):
                box.setChecked(box.text() != "a run fails")
        press(dialog, "Add server")
        dialog.findChild(QTableWidget).setItem(0, 0, QTableWidgetItem("appserver"))
        press(dialog, "Save")

    when_dialog_shows(win, fill_in_and_save)
    menu(win, "Settings…").trigger()

    assert isinstance(seen[0], SettingsDialog)
    settings = win.home.settings()
    assert {k: settings[k] for k in ("calendar", "keep_runs_for", "max_concurrent_runs", "java_home", "notify_email",
                                     "smtp_connection", "notify_on", "keeper", "python_env")} == {
        "calendar": "US", "keep_runs_for": "7 months", "max_concurrent_runs": 2,
        "java_home": r"C:\Program Files\Java\jdk-21",
        "notify_email": "a@example.com; b@example.com", "smtp_connection": "mailer",
        "notify_on": ["success", "skipped"],
        "keeper": [{"connection": "appserver", "command": "~/taskloom/taskloom", "home": "~/.taskloom"}],
        "python_env": "envs/mine"}  # not in the dialog: kept

    before = (win.home.root / "settings.yaml").read_bytes()
    seen.clear()
    press_when_shown(win, "Cancel", seen)
    menu(win, "Settings…").trigger()
    assert seen and (win.home.root / "settings.yaml").read_bytes() == before


def test_settings_with_a_broken_settings_file_says_what_is_wrong(win):
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "settings.yaml").write_text("keep_run: 5\n", encoding="utf-8")  # a typo, made by hand
    when_dialog_shows(win, lambda dialog: dialog.reject())  # (if one opens, it would wait for a click)

    menu(win, "Settings…").trigger()
    settings_said = [text for _, (_, text) in win.message_boxes]
    win.message_boxes.clear()
    when_dialog_shows(win, lambda dialog: dialog.reject())
    menu(win, "Python environment…").trigger()  # it shows the python setting too
    python_env_said = [text for _, (_, text) in win.message_boxes]
    win.message_boxes.clear()

    for said in (settings_said, python_env_said):
        assert any("settings.yaml" in text and "keep_run" in text for text in said), said


def test_settings_with_a_number_written_as_words_says_what_is_wrong(win):
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "settings.yaml").write_text("keep_runs_for: twenty years\n", encoding="utf-8")  # by hand
    when_dialog_shows(win, lambda dialog: dialog.reject())  # (if one opens, it would wait for a click)

    menu(win, "Settings…").trigger()

    said = [text for _, (_, text) in win.message_boxes]
    win.message_boxes.clear()
    assert any("keep_runs_for" in text and "twenty" in text for text in said), said


def test_connections_with_a_broken_connections_file_says_what_is_wrong(win):
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "connections.yaml").write_text("warehouse: {driver: org.example.Driver}\n",  # no kind
                                                     encoding="utf-8")
    when_dialog_shows(win, lambda dialog: dialog.reject())  # (if one opens, it would wait for a click)

    menu(win, "Connections…").trigger()

    texts = [text for _, (_, text) in win.message_boxes]
    win.message_boxes.clear()
    assert any("connections.yaml" in text and "warehouse" in text for text in texts), texts


def test_saving_the_connections_keeps_a_connection_of_a_kind_of_your_own_blocks_as_it_is(win):
    from taskloom.editor.dialogs import ConnectionsDialog

    win.home.root.mkdir(parents=True, exist_ok=True)
    # A block of your own can take a connection of its own kind (fields.Connection("ftp")); you write it by hand.
    (win.home.root / "connections.yaml").write_text(
        "files_ftp: {kind: ftp, host: ftp.example.com, username: me, password: 'secret:files_ftp_password', "
        "passive: true}\nappserver: {kind: ssh, host: server01.example.com, username: me}\n", encoding="utf-8")
    before = win.home.connections()
    dialog = ConnectionsDialog(win.home, win)
    dialog.show()

    press(dialog, "Save")  # nothing changed

    assert win.home.connections() == before


def test_a_new_ssh_connection_is_saved_with_its_password_kept_out_of_the_file_and_tested(server, win, qtbot,
                                                                                          monkeypatch):
    import keyring
    from keyring.backends import fail

    from sshserver import PASSWORD, USER
    from taskloom.editor.dialogs import ConnectionsDialog

    assert isinstance(keyring.get_keyring(), fail.Keyring)  # never the real credential store
    appserver = win.home.connections()["appserver"]
    names = iter(["backup", "wrong"])
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", staticmethod(lambda *a, **k: (next(names), True)))
    dialog = ConnectionsDialog(win.home, win)
    dialog.show()
    new, test = (next(b for b in dialog.findChildren(QPushButton) if b.text() == t)
                 for t in ("New…", "Save and test connection"))

    def add(password):
        new.click()
        kind = labelled(dialog, "Kind")
        kind.setCurrentIndex(kind.findData("ssh"))
        labelled(dialog, "Host").setText("127.0.0.1")
        labelled(dialog, "Port").setText(str(server.port))
        labelled(dialog, "User name").setText(USER)
        labelled(dialog, "Password").setText(password)
        test.click()
        qtbot.waitUntil(lambda: dialog.test_result.text() not in ("", "Testing…"), timeout=60_000)
        return dialog.test_result.text()

    assert "✓" in add(PASSWORD) and f"as {USER}" in dialog.test_result.text()
    wrong = add("not the password")
    assert "✕" in wrong and "Authentication" in wrong
    next(b for b in dialog.findChildren(QPushButton) if b.text().replace("&", "") == "Save").click()

    text = (win.home.root / "connections.yaml").read_text(encoding="utf-8")
    assert PASSWORD not in text and "not the password" not in text
    conns = win.home.connections()
    assert conns["backup"] == {"kind": "ssh", "host": "127.0.0.1", "port": server.port, "username": USER,
                               "password": "secret:backup_password"}
    assert conns["appserver"] == appserver  # the connection that was there is kept as it was
    assert win.home.get_secret("backup_password") == PASSWORD
    assert taskloom(win, "connection", "test", "backup").returncode == 0


def test_connection_names_that_are_taken_or_not_names_are_refused_and_a_rename_is_saved(server, win, monkeypatch):
    from taskloom.editor.dialogs import ConnectionsDialog

    names = iter(["my server", "appserver", "app_server"])
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText",
                        staticmethod(lambda *a, **k: (next(names), True)))
    dialog = ConnectionsDialog(win.home, win)
    buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}

    buttons["New…"].click()
    buttons["New…"].click()
    assert [(kind, title) for kind, (title, _) in win.message_boxes] == [("warning", "New connection")] * 2
    assert "letters, digits and _" in win.message_boxes[0][1][1] and "already" in win.message_boxes[1][1][1]
    win.message_boxes.clear()
    assert [dialog.names.item(i).text() for i in range(dialog.names.count())] == ["appserver"]

    buttons["Rename…"].click()  # flows that use it are named in a message
    assert win.message_boxes and "app_server" in win.message_boxes[0][1][1]
    win.message_boxes.clear()
    next(b for b in dialog.findChildren(QPushButton) if b.text().replace("&", "") == "Save").click()

    assert list(win.home.connections()) == ["app_server"]
    assert taskloom(win, "connection", "test", "app_server").returncode == 0  # its password still found


# --- blocks this computer does not have, and blocks of your own ------------------------------------

def test_a_block_whose_type_is_not_installed_keeps_its_settings_when_the_flow_is_edited_and_saved(win, tmp_path):
    flow = flow_file(tmp_path / "shared.yaml", {
        "theirs": {"type": "team.custom_export", "version": 3, "config": {"target": "x", "rows": [1, 2]},
                   "retry": {"policy": "fixed", "max_attempts": 2}, "ui": {"x": 0, "y": 0}},
        "python": {**PYTHON, "ui": {"x": 300, "y": 0}},
    }, ["theirs.result -> python.input"])
    tab = win.open_flow(flow)
    select_only(tab, "theirs")
    assert "not installed" in when_text(win)

    tab.doc.move_blocks({"theirs": (0, 100)})
    type_in(field(win, "Name"), "export")
    assert win.save(tab)

    data = saved(flow)
    assert data["blocks"]["export"] == {"type": "team.custom_export", "version": 3,
                                        "config": {"target": "x", "rows": [1, 2]},
                                        "retry": {"policy": "fixed", "max_attempts": 2}, "ui": {"x": 0, "y": 100}}
    assert data["edges"] == ["export.result -> python.input"]
    clean(win)


USER_BLOCK = '''
from taskloom import Block, fields, ports


class Mapping(fields.Field):
    """A field of my own: a mapping of names to values."""

    def coerce(self, value):
        if not isinstance(value, dict):
            raise ValueError(f"expected a mapping, got {value!r}")
        return value


class Rename(Block):
    type_id = "my.rename"
    title = "Rename Columns"
    description = "Renames the columns of a table, old name to new name."
    inputs = {"table": ports.Table()}
    outputs = {"table": ports.Table()}
    config = {"columns": Mapping(default={})}

    def run(self, ctx, table):
        return {"table": table}
'''


def test_a_field_of_your_own_blocks_is_written_as_yaml_and_a_mistake_in_it_is_reported(win, qtbot):
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    blocks = win.home.root / "blocks"
    blocks.mkdir(parents=True, exist_ok=True)
    (blocks / "rename.py").write_text(USER_BLOCK, encoding="utf-8")
    mine = MainWindow(Home(win.home.root))  # it finds your blocks when it starts
    qtbot.addWidget(mine)
    tab = mine.new_flow()
    tab.doc.add_block("my.rename", 0, 0)
    select_only(tab, "rename")

    type_in(field(mine, "Columns"), "{amount: total, day: date}")
    assert tab.doc.blocks["rename"]["config"]["columns"] == {"amount": "total", "day": "date"}
    type_in(field(mine, "Columns"), "[a, b]")
    assert "expected a mapping" in error_shown(mine)  # what your block says about it
    type_in(field(mine, "Columns"), "{amount: total, day: date}")

    type_in(field(mine, "Columns"), "{amount: [total")  # not finished
    shown = error_shown(mine)
    clean(mine)
    assert shown and tab.doc.blocks["rename"]["config"]["columns"] == {"amount": "total", "day": "date"}


# --- quitting --------------------------------------------------------------------------------

def test_cancelling_quit_at_a_flow_with_unsaved_changes_keeps_every_flow_open(win, tmp_path, monkeypatch):
    saved_flow = win.open_flow(flow_file(tmp_path / "saved.yaml", {"python": PYTHON}))
    changed = win.open_flow(flow_file(tmp_path / "changed.yaml", {"python": PYTHON}))
    changed.doc.move_blocks({"python": (5, 5)})
    asked = answer(monkeypatch, QMessageBox.Cancel)

    win.close()

    open_now = win.tabs_list()
    clean(win)
    assert asked == [("Unsaved changes", "Save changes to changed.yaml?")]
    assert win.isVisible()
    assert open_now == [saved_flow, changed]


def new_connection(dialog, kind: str, values: dict, password: str = ""):
    """New… (its name already answered), then fill in the form."""
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "New…").click()
    box = labelled(dialog, "Kind")
    box.setCurrentIndex(box.findData(kind))
    for label, value in values.items():
        labelled(dialog, label).setText(value)
    labelled(dialog, "Password").setText(password)


def save_and_test(qtbot, dialog) -> str:
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Save and test connection").click()
    qtbot.waitUntil(lambda: dialog.test_result.text() not in ("", "Testing…"), timeout=90_000)
    return dialog.test_result.text()


def test_a_new_database_connection_is_saved_for_jdbc_and_tested(jdbc_env, win, qtbot, monkeypatch, h2_server):
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

    from taskloom.editor.dialogs import ConnectionsDialog

    names = iter(["h2db", "other_db"])
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", staticmethod(lambda *a, **k: (next(names), True)))
    dialog = ConnectionsDialog(win.home, win)
    dialog.show()
    jar = str(h2_server["jar"])

    new_connection(dialog, "jdbc", {"Driver class": "org.h2.Driver", "URL": f"jdbc:h2:tcp://localhost:{h2_server['port']}/mem:test",
                                    "Driver .jar file(s)": jar, "User name": "sa"})
    result = save_and_test(qtbot, dialog)
    assert "✓" in result and "H2" in result, result
    new_connection(dialog, "jdbc", {"Driver class": "com.example.jdbc.Driver", "URL": "jdbc:example://db.example.com/x",
                                    "Driver .jar file(s)": "C:/drivers/a.jar; C:/drivers/b.jar", "User name": "me"},
                   password="pw for the example")
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Add setting").click()
    table = dialog.findChild(QTableWidget)
    table.setItem(0, 0, QTableWidgetItem("queryTimeout"))
    table.setItem(0, 1, QTableWidgetItem("600"))
    press(dialog, "Save")

    conns = win.home.connections()
    assert conns["h2db"] == {"kind": "jdbc", "driver": "org.h2.Driver",
                             "url": f"jdbc:h2:tcp://localhost:{h2_server['port']}/mem:test", "jars": [jar],
                             "properties": {"user": "sa"}}
    other = conns["other_db"]
    timeout = other["properties"].pop("queryTimeout")
    assert str(timeout) == "600"  # (written as a number or as text: the driver gets 600 either way)
    assert other == {"kind": "jdbc", "driver": "com.example.jdbc.Driver", "url": "jdbc:example://db.example.com/x",
                     "jars": ["C:/drivers/a.jar", "C:/drivers/b.jar"],
                     "properties": {"user": "me", "password": "secret:other_db_password"}}
    assert "pw for the example" not in (win.home.root / "connections.yaml").read_text(encoding="utf-8")
    assert "warehouse" in conns  # the one that was there is kept


def test_a_new_email_server_connection_is_saved_and_tested(smtp, win, qtbot, monkeypatch):
    from taskloom.editor.dialogs import ConnectionsDialog

    port = win.home.connections()["mailer"]["port"]
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", staticmethod(lambda *a, **k: ("relay", True)))
    dialog = ConnectionsDialog(win.home, win)
    dialog.show()

    new_connection(dialog, "smtp", {"Host": "127.0.0.1", "Port": str(port), "Security": "none",
                                    "Send as": "reports@example.com"})
    result = save_and_test(qtbot, dialog)

    assert "✓" in result and f"127.0.0.1:{port}" in result, result
    assert win.home.connections()["relay"] == {"kind": "smtp", "host": "127.0.0.1", "port": port, "security": "none",
                                               "sender": "reports@example.com"}


def in_a_code_page_that_is_not_utf8(monkeypatch):
    """The `taskloom` program the editor starts then writes its output in the console code page. On Windows that is
    always so: into a pipe, Python writes in the ANSI code page (e.g. cp1252), not UTF-8. A Mac does the same under a
    locale that is not UTF-8 (on Linux the test runs in UTF-8)."""
    if sys.platform == "darwin":
        monkeypatch.setenv("LC_ALL", "en_US.ISO8859-1")


def test_save_and_test_connection_shows_a_user_name_with_accents_as_written(server, win, qtbot, monkeypatch):
    import sshserver
    from taskloom.editor.dialogs import ConnectionsDialog

    monkeypatch.setattr(sshserver._Auth, "validate_password",  # (the test server: any user name, its password)
                        lambda self, username, password: password == sshserver.PASSWORD)
    in_a_code_page_that_is_not_utf8(monkeypatch)
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", staticmethod(lambda *a, **k: ("accented", True)))
    dialog = ConnectionsDialog(win.home, win)
    dialog.show()

    new_connection(dialog, "ssh", {"Host": "127.0.0.1", "Port": str(server.port), "User name": "José"},
                   password=sshserver.PASSWORD)
    result = save_and_test(qtbot, dialog)

    assert "✓" in result and "Connected to 127.0.0.1 as José" in result, result
    assert win.home.connections()["accented"]["username"] == "José"


def test_the_python_environment_dialog_shows_an_error_naming_a_path_with_accents_as_written(win, qtbot, tmp_path,
                                                                                           monkeypatch):
    import unicodedata

    from taskloom.editor.dialogs import PythonEnvDialog

    in_a_code_page_that_is_not_utf8(monkeypatch)
    missing = tmp_path / "Pythön José" / ("python.exe" if sys.platform == "win32" else "python")  # not there
    dialog = PythonEnvDialog(win.home, win)
    dialog.python.setText(str(missing))

    dialog.create_button.click()
    qtbot.waitUntil(lambda: dialog.proc is None, timeout=60_000)

    # (On a Mac, Qt passes ö as o + ¨ to the program it starts: the same text on screen.)
    lines = unicodedata.normalize("NFC", dialog.output.toPlainText()).splitlines()
    errors = [line for line in lines if line.startswith("error:")]
    assert lines[-1].startswith("failed") and errors, lines
    assert str(missing) in errors[0], errors  # the Python it could not start, named as you typed it


def test_edit_next_to_a_blocks_connection_opens_it_and_an_empty_password_keeps_the_saved_one(server, win, tmp_path):
    before = win.home.connections()["appserver"]
    flow = flow_file(tmp_path / "remote.yaml", {
        "cmd": {"type": "servers.ssh_command", "config": {"connection": "appserver", "command": "uptime"}}})
    tab = win.open_flow(flow)
    select_only(tab, "cmd")
    seen = []

    def look_and_save(dialog):
        seen.append((dialog.names.currentItem().text(), labelled(dialog, "Host").text(),
                     " ".join(label.text() for label in dialog.findChildren(QLabel))))
        press(dialog, "Save")  # the password field left empty

    when_dialog_shows(win, look_and_save)
    next(b for b in win.properties.findChildren(QToolButton) if b.text() == "Edit…").click()

    [(selected, host, words)] = seen
    assert (selected, host) == ("appserver", "127.0.0.1") and "A password is saved" in words
    assert win.home.connections()["appserver"] == before
    assert taskloom(win, "connection", "test", "appserver").returncode == 0
    clean(win)


# --- flow files written by hand, and files that cannot be written -----------------------------------

ONE_BLOCK = "  a:\n    type: logic.python\n    config: {inputs: [], code: 'output = 1'}\n    ui: {x: 0, y: 0}\n"


@pytest.mark.parametrize("text, edit", [
    ("blocks:\n" + ONE_BLOCK + "edges:\n", "connect"),  # every connection deleted by hand, `edges:` left
    ("name: draft\nblocks:\nedges: []\n", "add a block"),  # a flow begun by hand
    ("notes:\nblocks:\n" + ONE_BLOCK, "add a text"),
], ids=["no edges listed", "no blocks listed", "no notes listed"])
def test_a_flow_file_with_an_empty_list_opens_as_the_runner_reads_it_and_can_be_edited(win, tmp_path, text, edit):
    flow = tmp_path / "by-hand.yaml"
    flow.write_text(text, encoding="utf-8")
    assert taskloom(win, "validate", flow).returncode == 0  # the runner reads it

    tab = win.open_flow(flow)
    said = list(win.message_boxes)
    win.message_boxes.clear()
    assert tab is not None, said
    if edit == "connect":
        tab.doc.add_block("logic.python", 300, 0, {"code": "output = input"})
        settle()
        drag_port_to_port(tab, "a", "output", "python", "input")
        assert tab.doc.edges() == [("a", "output", "python", "input")]
    elif edit == "add a block":
        add_from_palette(win, "Python Code")
        assert list(tab.doc.blocks) == ["python"]
        tab.doc.set_block_value("python", "code", "output = 1")  # a Python Code block needs its code
    else:
        win.add_text(tab, QPointF(0, -100))
        assert len(tab.doc.notes) == 1
    assert win.save(tab)
    assert taskloom(win, "validate", flow).returncode == 0
    clean(win)


def test_saving_a_flow_whose_file_cannot_be_written_says_so_and_keeps_the_changes(win, tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can write a read-only file")
    flow = flow_file(tmp_path / "locked.yaml", {"python": PYTHON})
    original = flow.read_bytes()
    tab = win.open_flow(flow)
    tab.doc.move_blocks({"python": (10, 10)})
    os.chmod(flow, stat.S_IREAD)  # read-only (also on Windows: its read-only attribute)
    try:
        menu(win, "Save").trigger()
    finally:
        os.chmod(flow, stat.S_IREAD | stat.S_IWRITE)

    said = [(kind, text) for kind, (_, text) in win.message_boxes]
    win.message_boxes.clear()
    still_unsaved = tab.doc.dirty
    clean(win)
    assert flow.read_bytes() == original
    assert still_unsaved  # the changes are still there, to save elsewhere
    assert any(kind == "warning" and "locked.yaml" in text for kind, text in said), said
