"""Create Reusable block: right-click a Python Code block, choose "Create Reusable block...", give it a name, what it
does and what its inputs expect, and it is saved as a block of your own (one Python file in your blocks folder),
listed under "User Created" in the block list, to use in any flow: in the editor, with `taskloom run` and in the
scheduler service, it runs the code of the block it was made from on the inputs connected to it."""

import json
import os
import re
import sqlite3
import time

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QFormLayout, QLabel, QLineEdit, QMessageBox,  # noqa: E402
                               QPlainTextEdit, QTextEdit)

from taskloom.config import Home  # noqa: E402
from taskloom.editor.panels import CodeEdit  # noqa: E402
from taskloom.editor.window import MainWindow  # noqa: E402
from taskloom.history import History  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_editor import add_from_palette, set_code, wait_done  # noqa: E402
from tests.test_paths_editor_editing import (GREET_BLOCK, clean, field, flow_file, press, select_only,  # noqa: E402
                                             settle, taskloom)
from tests.test_paths_editor_running import right_click  # noqa: E402
from tests.test_scheduler_start_at import runs, start_service, stop_service, wait_until_finished  # noqa: E402

CREATE = "Create Reusable block"  # the menu item, followed by an ellipsis: it opens a window
# The code of the block it is made from: a comment with a character outside Windows' ANSI code page, and a print
# (what a Python Code block prints goes to its Log).
CODE = ("# names → tidy names, sorted\n"
        "tidy = sorted(n.strip().title() for n in names)\n"
        "count = len(tidy)\n"
        "print(count, 'names tidied')\n")
TIDY = {"type": "logic.python", "config": {"inputs": ["names"], "outputs": ["tidy", "count"], "code": CODE}}
LOAD = {"type": "logic.python",
        "config": {"inputs": [], "outputs": ["names"], "code": "names = ['  ana ', 'josé', 'Bo']"}}
TIDIED = [["Ana", "Bo", "José"], 3]  # what the code gives for those names: the tidy names and their count
WHAT_IT_DOES = "Tidies a list of names (José, Zoë): trims, capitalises and sorts them, and counts them."
INPUTS_EXPECT = "names: a list of names, as text."


# --- helpers ---------------------------------------------------------------------------------------------------

def writer(out, *inputs) -> dict:
    """A Python Code block that writes the values of its inputs to the file `out`, as a JSON list."""
    code = f"import json\nopen({str(out)!r}, 'w').write(json.dumps([{', '.join(inputs)}]))\n"
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": [], "code": code}}


def names_flow(tmp_path, out):
    """The user's flow: names loaded, tidied by a Python Code block, written to `out`; open it in the editor."""
    return flow_file(tmp_path / "names.yaml", {
        "start": {"type": "trigger.manual", "ui": {"x": -300, "y": 0}},
        "load": {**LOAD, "ui": {"x": 0, "y": 0}},
        "tidy": {**TIDY, "ui": {"x": 300, "y": 0}},
        "save": {**writer(out, "a_tidy", "a_count", "b_tidy", "b_count"), "ui": {"x": 650, "y": 0}},
    }, ["start.start -> load.after", "load.names -> tidy.names", "tidy.tidy -> save.a_tidy",
        "tidy.count -> save.a_count"])


def block_files(win) -> list:
    folder = win.home.blocks_dir
    return sorted(folder.glob("*.py")) if folder.is_dir() else []


def user_created(win) -> list[str] | None:
    """The blocks listed under User Created in the block list (None: there is no such group)."""
    tree = win.palette_panel.tree
    for i in range(tree.topLevelItemCount()):
        folder = tree.topLevelItem(i)
        if folder.text(0).startswith("User Created"):
            return [folder.child(j).text(0) for j in range(folder.childCount())]
    return None


def groups(win) -> list[str]:
    tree = win.palette_panel.tree
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def menu_item(win, tab, block):
    """The block's right-click menu item Create Reusable block…"""
    items = win._block_menu_for(tab, block).actions()
    found = [a for a in items if a.text().startswith(CREATE)]
    assert found, f"no {CREATE}... in the menu of {block}: {[a.text() for a in items]}"
    assert found[0].isEnabled()
    return found[0]


def plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).replace("&", "").strip().lower()


TEXT_BOXES = (QLineEdit, QPlainTextEdit, QTextEdit)


def text_boxes(widget) -> list:
    return [box for kind in TEXT_BOXES for box in widget.findChildren(kind)]


def text_box(dialog, label: str):
    """The window's text box for `label` (the box on its row, else the first one at or below it)."""
    for form in dialog.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            name, value = form.itemAt(row, QFormLayout.LabelRole), form.itemAt(row, QFormLayout.FieldRole)
            if name and value and isinstance(name.widget(), QLabel) and plain(name.widget().text()).startswith(
                    label.lower()):
                widget = value.widget()
                if isinstance(widget, TEXT_BOXES):
                    return widget
                if widget is not None and text_boxes(widget):
                    return text_boxes(widget)[0]
    labels = [w for w in dialog.findChildren(QLabel) if w.isVisible() and plain(w.text()).startswith(label.lower())]
    assert labels, f"no {label!r} in the window: {[w.text() for w in dialog.findChildren(QLabel)]}"
    if isinstance(labels[0].buddy(), TEXT_BOXES):
        return labels[0].buddy()
    top = labels[0].mapTo(dialog, QPoint(0, 0)).y()
    below = [b for b in text_boxes(dialog)
             if b.isVisible() and b.mapTo(dialog, QPoint(0, 0)).y() >= top - 4]
    assert below, f"no text box for {label!r}"
    return min(below, key=lambda b: b.mapTo(dialog, QPoint(0, 0)).y())


def fill(dialog, label: str, text: str):
    box = text_box(dialog, label)
    if isinstance(box, QLineEdit):
        box.setText(text)
    else:
        box.setPlainText(text)


def fill_all(dialog, name: str, what: str = WHAT_IT_DOES, expects: str = INPUTS_EXPECT):
    fill(dialog, "Name", name)
    fill(dialog, "What it does", what)
    fill(dialog, "What its inputs expect", expects)


def in_the_window(win, qtbot, open_it, do):
    """open_it() opens the Create Reusable block window (a window waits for you inside the call that opens it);
    when it shows, do(window). What open_it() returns is returned; an error in do() is raised here."""
    done, errors, returned, tries = [], [], [], []

    def act():
        dialog = QApplication.activeModalWidget() or next(
            (w for w in QApplication.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()), None)
        if dialog is None:
            # While open_it() runs (a right-click can take two tries, of up to 5 s each), then up to 5 s after it.
            if not returned or len(tries) < 250:
                tries.extend([1] if returned else [])
                QTimer.singleShot(20, win, act)
            else:
                done.append(None)
            return
        try:
            do(dialog)
        except BaseException as e:  # (not lost in Qt's event loop)
            errors.append(e)
        finally:
            if dialog.isVisible():  # (do not leave it waiting for a click)
                dialog.reject()
            done.append(dialog)

    QTimer.singleShot(0, win, act)
    try:
        opened = open_it()
    except BaseException:
        returned.append(1)
        tries.extend([1] * 250)  # stop looking for the window: the next test's must not be taken for it
        raise
    returned.append(1)
    qtbot.waitUntil(lambda: bool(done), timeout=15_000)
    if errors:
        raise errors[0]
    assert done[0] is not None, f"no window opened ({opened})"
    settle()
    return opened


def make_reusable(win, qtbot, tab, block, name="Tidy names"):
    """Right-click menu → Create Reusable block…, fill in the window and press OK."""
    in_the_window(win, qtbot, lambda: menu_item(win, tab, block).trigger(),
                  lambda dialog: (fill_all(dialog, name), press(dialog, "OK")))


def close_message_boxes(win, closed: list, watching: list):
    """While `watching` is not empty: a message box that waits for a click (not one of the kinds the win fixture
    answers) is noted in `closed` and closed, so the test cannot hang on it."""
    for box in QApplication.topLevelWidgets():
        if isinstance(box, QMessageBox) and box.isVisible():
            closed.append(box.text())
            box.done(0)
    if watching:
        QTimer.singleShot(20, win, lambda: close_message_boxes(win, closed, watching))


def visible_texts(dialog) -> set[str]:
    return {w.text() for w in dialog.findChildren(QLabel) if w.isVisible() and w.text().strip()}


def try_name(win, dialog, name: str) -> dict:
    """In the window (filled in with a good name first, to see what changes): type `name`, press OK. Whether the
    window is still open, the messages that came up (a message box, or new text in the window), the block files."""
    fill_all(dialog, "A name no block has")
    settle()
    before, shown, closed, watching = visible_texts(dialog), len(win.message_boxes), [], [True]
    close_message_boxes(win, closed, watching)
    fill(dialog, "Name", name)
    press(dialog, "OK")
    settle()
    watching.clear()
    messages = [text for _, (_, text) in win.message_boxes[shown:]] + closed + sorted(visible_texts(dialog) - before)
    del win.message_boxes[shown:]  # (answered here: not unexpected)
    return {"open": dialog.isVisible(), "messages": messages, "files": block_files(win)}


def logs(win, block: str) -> str:
    with sqlite3.connect(win.home.history_db) as db:
        (run_id,) = db.execute("SELECT max(id) FROM runs").fetchone()
        return "\n".join(m for (m,) in db.execute("SELECT message FROM logs WHERE run_id=? AND block=?",
                                                    (run_id, block)))


# --- the user's case: right-click, fill in the window, OK --------------------------------------------------------

def test_right_click_create_reusable_block_saves_a_block_of_your_own_listed_under_user_created(win, qtbot, tmp_path):
    """The user's case through the real right-click: the window saves one readable Python file in the blocks folder
    (made when missing) that defines the block (title, description, category User Created, type made from the
    name); the block list shows it at once under User Created, its tooltip says what it does and what its inputs
    expect; the block it was made from stays as it is."""
    flow = names_flow(tmp_path, tmp_path / "out.json")
    tab = win.open_flow(flow)
    settle()
    data, on_disk = yaml.safe_dump(tab.doc.data), flow.read_bytes()
    assert not win.home.blocks_dir.exists() and user_created(win) is None

    menu = in_the_window(win, qtbot, lambda: right_click(qtbot, win, tab, "tidy", "title", CREATE),
                         lambda dialog: (fill_all(dialog, "Tidy names"), press(dialog, "OK")))

    assert any(t.startswith(CREATE) for t in menu), menu
    (path,) = block_files(win)  # one Python file, in the blocks folder (made: it was missing)
    assert path.stem.lower() == "tidy_names"
    text = path.read_text(encoding="utf-8")  # readable: UTF-8 text, the code in it as written
    compile(text, str(path), "exec")
    for line in ("tidy = sorted(n.strip().title() for n in names)", "count = len(tidy)", "Tidy names"):
        assert line in text, line
    registry = discover(win.home.blocks_dir)  # an ordinary user block file, as every run loads them
    assert registry.warnings == []
    cls = registry.get("user.tidy_names")
    assert cls is not None, sorted(registry.blocks)
    assert (cls.title, cls.category) == ("Tidy names", "User Created")
    assert WHAT_IT_DOES in cls.description

    assert user_created(win) == ["Tidy names"]  # at once, no restart
    (item,) = win.palette_panel.tree.findItems("Tidy names", Qt.MatchExactly | Qt.MatchRecursive)
    assert WHAT_IT_DOES in item.toolTip(0) and INPUTS_EXPECT in item.toolTip(0), item.toolTip(0)
    assert yaml.safe_dump(tab.doc.data) == data and flow.read_bytes() == on_disk  # the block it came from: as it was
    clean(win)


def test_create_reusable_block_is_in_the_menu_of_python_code_blocks_only(win, qtbot, tmp_path):
    """Python Code blocks have it; other blocks (a start block, Wait, Read File, and a reusable block itself, which
    has no code of its own to save) do not."""
    tab = win.open_flow(flow_file(tmp_path / "mixed.yaml", {
        "start": {"type": "trigger.manual"}, "tidy": TIDY,
        "pause": {"type": "logic.wait", "config": {"seconds": 1}},
        "read": {"type": "data.read_file", "config": {"path": "names.csv"}},
    }))
    settle()
    menu_item(win, tab, "tidy")
    make_reusable(win, qtbot, tab, "tidy")
    before = set(tab.doc.blocks)
    add_from_palette(win, "Tidy names")
    (reusable,) = set(tab.doc.blocks) - before
    settle()

    for block in ("start", "pause", "read", reusable):
        items = [a.text() for a in win._block_menu_for(tab, block).actions()]
        assert not any(t.startswith(CREATE) for t in items), (block, items)
    clean(win)


# --- the new block in flows: editor, taskloom run, the scheduler service ----------------------------------------------

def test_the_new_block_added_to_the_flow_runs_the_saved_code_in_the_editor_and_with_taskloom_run(win, qtbot, tmp_path):
    """Added from the block list to the same open flow (no restart) and connected, it runs the saved code on its
    inputs and gives the same outputs as the block it was made from, in the editor and with `taskloom run`; what
    the code prints is in its Log."""
    out = tmp_path / "out.json"
    flow = names_flow(tmp_path, out)
    tab = win.open_flow(flow)
    settle()
    make_reusable(win, qtbot, tab, "tidy")

    before = set(tab.doc.blocks)
    add_from_palette(win, "Tidy names")
    (reusable,) = set(tab.doc.blocks) - before
    assert tab.doc.blocks[reusable]["type"] == "user.tidy_names"
    for line in (("load", "names", reusable, "names"), (reusable, "tidy", "save", "b_tidy"),
                 (reusable, "count", "save", "b_count")):
        tab.doc.connect(*line)
    settle()
    assert win.save(tab)

    win.run_flow(tab)
    assert tab.runs, f"the run did not start: {win.message_boxes}"
    wait_done(qtbot, tab)
    assert {b: n.status for b, n in tab.scene.nodes.items()} == dict.fromkeys(tab.doc.blocks, "success")
    assert json.loads(out.read_text(encoding="utf-8")) == TIDIED + TIDIED  # the original's outputs, then the new one's
    assert "3 names tidied" in logs(win, reusable)

    out.unlink()
    result = taskloom(win, "run", flow, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == TIDIED + TIDIED
    clean(win)


def test_a_python_code_block_added_from_the_block_list_gives_a_block_with_its_default_input_and_output(win, qtbot,
                                                                                                       tmp_path):
    """A Python Code block as you add it from the block list (its input and output as they come: input and output),
    given its code and Run in my Python environment unticked in Properties, in a new flow not saved yet: the
    reusable block made from it has that input and output and runs that code in Taskloom's own Python."""
    tab = win.new_flow()
    settle()
    add_from_palette(win, "Python Code")
    (block,) = [b for b in tab.doc.blocks if b != "start"]
    select_only(tab, block)
    set_code(win, "output = input * 2")
    field(win, "Run in my Python environment").setChecked(False)
    settle()
    clean(win)  # (this flow is not saved: closing the window at the end must not ask to)
    make_reusable(win, qtbot, tab, block, name="Double it")
    out = tmp_path / "doubled.json"
    flow = flow_file(tmp_path / "double.yaml", {
        "start": {"type": "trigger.manual"},
        "seed": {"type": "logic.python", "config": {"inputs": [], "code": "output = 21"}},
        "double_it": {"type": "user.double_it"}, "save": writer(out, "value"),
    }, ["start.start -> seed.after", "seed.output -> double_it.input", "double_it.output -> save.value"])

    result = taskloom(win, "run", flow, cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == [42]


def test_the_scheduler_service_runs_a_flow_with_the_new_block(env, win, qtbot, tmp_path):
    """A scheduled flow that uses the new block runs in the scheduler service (it loads the blocks folder too)."""
    assert win.home.root == env.home
    tab = win.open_flow(flow_file(tmp_path / "source.yaml", {"tidy": TIDY}))
    settle()
    make_reusable(win, qtbot, tab, "tidy")
    out = tmp_path / "scheduled.json"
    flow = flow_file(tmp_path / "nightly.yaml", {
        "every_minute": {"type": "trigger.schedule", "config": {"every": "1 minute"}},  # its first run: when seen
        "load": LOAD, "tidy_names": {"type": "user.tidy_names"}, "save": writer(out, "tidy", "count"),
    }, ["every_minute.start -> load.after", "load.names -> tidy_names.names", "tidy_names.tidy -> save.tidy",
        "tidy_names.count -> save.count"])
    History(env.home / "history.db").close()  # its tables exist before any run
    Home(env.home).set_scheduled(flow, True)

    service = start_service(env, tray=False)
    try:
        deadline = time.monotonic() + 60
        while not [r for r in runs(env) if r["status"] != "running"] and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        stop_service(env, service)
        wait_until_finished(env)

    finished = [r for r in runs(env) if r["status"] != "running"]
    assert finished, "the scheduler service ran nothing in 60 s"
    assert finished[0]["status"] == "success", env.query("SELECT block, status, error FROM blocks")
    assert json.loads(out.read_text(encoding="utf-8")) == TIDIED
    clean(win)


def test_the_new_block_keeps_the_run_in_my_python_environment_choice(win, qtbot, tmp_path):
    """Made from a block that runs in your own Python environment, it runs there too: with no environment made
    yet, it fails asking for one, as that block does (made from one that does not, it runs in Taskloom's own
    Python: the editor and taskloom run test above)."""
    tab = win.open_flow(flow_file(tmp_path / "source.yaml", {
        "tidy": {**TIDY, "config": {**TIDY["config"], "use_environment": True}}}))
    settle()
    make_reusable(win, qtbot, tab, "tidy")
    flow = flow_file(tmp_path / "in_env.yaml", {
        "start": {"type": "trigger.manual"}, "load": LOAD, "tidy_names": {"type": "user.tidy_names"},
    }, ["start.start -> load.after", "load.names -> tidy_names.names"])

    result = taskloom(win, "run", flow, cwd=tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    with sqlite3.connect(win.home.history_db) as db:
        status, error = db.execute("SELECT status, error FROM blocks WHERE block='tidy_names' "
                                   "ORDER BY run_id DESC LIMIT 1").fetchone()
    assert status == "failed" and "Python environment" in (error or ""), (status, error)
    assert tab.doc.blocks["tidy"]["config"]["use_environment"] is True
    clean(win)


# --- the new block in the editor ---------------------------------------------------------------------------------

def test_the_new_block_has_its_ports_no_code_field_and_its_properties_say_what_it_does_and_expects(win, qtbot,
                                                                                                    tmp_path):
    """On the canvas it has the inputs and outputs of the block it was made from; its Properties have no Code field
    (it runs its saved code) and show what it does and what its inputs expect. The original keeps its code."""
    tab = win.open_flow(names_flow(tmp_path, tmp_path / "out.json"))
    settle()
    make_reusable(win, qtbot, tab, "tidy")
    before = set(tab.doc.blocks)
    add_from_palette(win, "Tidy names")
    (reusable,) = set(tab.doc.blocks) - before
    settle()

    node = tab.scene.nodes[reusable]
    assert (node.inputs, node.outputs) == (tab.scene.nodes["tidy"].inputs, tab.scene.nodes["tidy"].outputs)
    assert node.inputs == ["after", "names"] and node.outputs == ["done", "tidy", "count", "error"]
    select_only(tab, reusable)
    body = win.properties.widget()
    assert body.findChildren(CodeEdit) == []
    shown = "\n".join([w.text() for w in body.findChildren(QLabel) if w.isVisible()] +
                      [w.toPlainText() for w in text_boxes(body) if w.isVisible() and not isinstance(w, QLineEdit)])
    assert WHAT_IT_DOES in shown and INPUTS_EXPECT in shown, shown

    select_only(tab, "tidy")
    (code,) = win.properties.widget().findChildren(CodeEdit)
    assert code.toPlainText() == CODE
    clean(win)


# --- the window: Cancel, and the names it refuses -----------------------------------------------------------------

def test_cancel_saves_nothing(win, qtbot, tmp_path):
    tab = win.open_flow(names_flow(tmp_path, tmp_path / "out.json"))
    settle()
    data = yaml.safe_dump(tab.doc.data)

    in_the_window(win, qtbot, lambda: menu_item(win, tab, "tidy").trigger(),
                  lambda dialog: (fill_all(dialog, "Tidy names"), press(dialog, "Cancel")))

    assert block_files(win) == []
    assert user_created(win) is None
    assert win.palette_panel.tree.findItems("Tidy names", Qt.MatchExactly | Qt.MatchRecursive) == []
    assert yaml.safe_dump(tab.doc.data) == data
    clean(win)


def test_an_empty_name_is_refused_with_a_message_and_the_window_stays_open_to_fix_it(win, qtbot, tmp_path):
    tab = win.open_flow(names_flow(tmp_path, tmp_path / "out.json"))
    settle()
    seen = {}

    def do(dialog):
        seen["empty"] = try_name(win, dialog, "")
        fill(dialog, "Name", "Tidy names")  # fixed, in the same window
        press(dialog, "OK")

    in_the_window(win, qtbot, lambda: menu_item(win, tab, "tidy").trigger(), do)

    assert seen["empty"]["open"], "the window closed"
    assert seen["empty"]["messages"], "no message said why"
    assert seen["empty"]["files"] == []
    (path,) = block_files(win)
    assert path.stem.lower() == "tidy_names"
    assert user_created(win) == ["Tidy names"]
    clean(win)


@pytest.mark.parametrize("name", [
    "Wait",  # the title of a built-in block
    "Tidy names",  # the title of the block of your own made just before
    "tidy names",  # its block type would be user.tidy_names: that block's
])
def test_a_name_another_block_already_has_is_refused_and_overwrites_nothing(win, qtbot, tmp_path, name):
    tab = win.open_flow(names_flow(tmp_path, tmp_path / "out.json"))
    settle()
    make_reusable(win, qtbot, tab, "tidy")
    (first,) = block_files(win)
    made = first.read_bytes()
    seen = {}

    def do(dialog):
        seen["tried"] = try_name(win, dialog, name)
        press(dialog, "Cancel")

    in_the_window(win, qtbot, lambda: menu_item(win, tab, "tidy").trigger(), do)

    assert seen["tried"]["open"], "the window closed"
    assert seen["tried"]["messages"], "no message said why"
    assert seen["tried"]["files"] == [first] and block_files(win) == [first]
    assert first.read_bytes() == made
    assert user_created(win) == ["Tidy names"]
    clean(win)


# --- the block list ----------------------------------------------------------------------------------------------

def test_the_blocks_of_your_blocks_folder_and_the_reusable_ones_are_all_under_user_created(win, qtbot, tmp_path):
    """The block list's group for the blocks in your blocks folder is User Created (no longer "User blocks"): a
    block file written by hand is there when the editor starts, and a reusable block joins it."""
    blocks = win.home.root / "blocks"
    blocks.mkdir(parents=True, exist_ok=True)
    (blocks / "greet.py").write_text(GREET_BLOCK, encoding="utf-8")
    mine = MainWindow(Home(win.home.root))  # it finds your blocks when it starts
    qtbot.addWidget(mine)
    mine.show()
    settle()
    assert user_created(mine) == ["Greet"], groups(mine)
    assert not any(g.startswith("User blocks") for g in groups(mine)), groups(mine)

    tab = mine.open_flow(names_flow(tmp_path, tmp_path / "out.json"))
    settle()
    make_reusable(mine, qtbot, tab, "tidy")

    assert user_created(mine) == ["Greet", "Tidy names"]
    assert not any(g.startswith("User blocks") for g in groups(mine)), groups(mine)
    clean(mine)
