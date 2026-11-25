"""Local reusable blocks in the editor (stage 4, work package 4; docs/promises/local-blocks.yaml RB-1 to RB-6, RB-8,
RB-9 and RB-10, and TV-8 of typed-values.yaml): Create reusable block… (Global or Local), Properties of a copy (the
definition all copies share), copying, pasting and deleting copies, Promote to global reusable block, Save as, and
Commit History's preview, search and Restore. Rows L1 to L13, the docs part of L14, and L18 of the stage's
combinations. Through the real editor window (the `win` fixture, its Taskloom folder a temporary one) with real input:
clicks on the canvas, right-click menus, the Edit and File menus, the Delete key, the tab bar; and what the user sees
(the canvas, Properties, the blocks tree, the status bar) and the flow file saved. Flows are also run with the real
`taskloom run`. L18 is checked after every action that changes a flow: saved, the file holds a definition for every
Local block its copies use and none that no copy uses (`saved`).

The flow file, as tests/test_local_blocks.py fixes it: `local_blocks: {<name>: {title, description, inputs_help,
inputs, outputs, use_environment, code}}`; a copy is a block of type `local.<name>` with no config; a block's typed
values are under its key `inputs: {<hole>: {type, value}}` (tests/test_typed_values.py).

The UI contract these tests fix (the implementer follows it):
- Right-click a Python Code block: "Create reusable block…". Its window has two radio buttons, objectName
  "reusable_global" (Global) and "reusable_local" (Local), and as today Name, What it does, What its inputs expect,
  OK and Cancel; a name it refuses keeps the window open and says why.
- Local: the definition's name (its key in local_blocks; its copies are local.<name>) is made from the Name as a Global
  block's file name is ("Shout" -> shout); its title is the Name, description What it does, inputs_help What its
  inputs expect, with the source block's inputs, outputs, use_environment and code. The source block keeps its name,
  place, lines and typed values; its type becomes local.<name>, with no config.
- Properties of a copy: the definition's inputs and outputs on the rows labelled "Inputs" and "Outputs" (as a Python
  Code block's), its code in a CodeEdit with objectName "local_code"; when the flow has N >= 2 copies, a QLabel with
  objectName "shared_note" says exactly "Shared by N blocks: an edit changes all N". An edit there changes the
  definition, so every copy.
- Removing an input from the definition while copies have a line or a typed value on it is refused on Properties'
  problem line (the red line that says why a name is refused). It contains "Cannot remove the input <hole>" and, for
  each such copy, "<copy> has a line from <block>.<output>" or "<copy> has a typed value"; copies with nothing there
  are not named.
- Right-click a copy: an item starting "Promote to global reusable block". It first asks with QMessageBox.question,
  whose text contains "Undo cannot remove the global block's file"; only Yes promotes. The Global block is named with
  the definition's title; when that name is taken, a window with a "Name" text box and an OK button asks for another.
- A definition pasted into a flow whose Local block of that name has other code, or whose name a Global block has, is
  kept as <name>_<n>, the first of <name>_2, <name>_3, ... that neither the flow's Local blocks nor the Global blocks
  use, and titled unlike any Global block; the pasted copies are local.<name>_<n>, and the status bar's message starts
  "The pasted Local block <name> is kept as <name>_<n>".
- Commit History: a match in a Local definition's code is a code match (listed after the outputs' matches and before
  the flow file's); its card says "in the code of" and names the Local block or one of its copies.
"""

import copy
import json
import os
import re
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QRadioButton  # noqa: E402

from taskloom.blocks.logic import write_reusable_block  # noqa: E402
from taskloom.editor.canvas import HEADER_H  # noqa: E402
from taskloom.editor.panels import CodeEdit  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_commit_history import dates, make_history, open_history  # noqa: E402
from tests.test_lines import wait_runs  # noqa: E402
from tests.test_local_blocks import SHOUT, WHISPER, python, shouted, writer  # noqa: E402
from tests.test_paths_editor_editing import (clean, error_shown, field, menu, press, save_as, settle,  # noqa: E402
                                             taskloom, type_in)
from tests.test_paths_editor_running import right_click  # noqa: E402
from tests.test_reusable_block import block_files, fill, fill_all, in_the_window, try_name, user_created  # noqa: E402
from tests.test_save_and_commit import GIT, inside_a_repository  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
LOCAL = "local."  # a copy's type: local.<name>
CREATE = "Create reusable block…"  # the Python Code block's right-click item
PROMOTE = "Promote to global reusable block"  # a copy's right-click item (it may end with an ellipsis)
CHOICE = {"Global": "reusable_global", "Local": "reusable_local"}  # the window's radio buttons, by objectName
SHARED_NOTE, CODE_BOX = "shared_note", "local_code"  # Properties of a copy: the note and the code box
UNDO_WORDS = "Undo cannot remove the global block's file"  # in Promote's question
RENAMED = "The pasted Local block {old} is kept as {new}"  # the status bar's message starts so
REFUSED = "Cannot remove the input {hole}"  # RB-10, on Properties' problem line
NEW_CODE = "loud = word.title() + (tail or '.')\n"  # an edit of SHOUT's code


def shared(n: int) -> str:
    return f"Shared by {n} blocks: an edit changes all {n}"


def titled(word: str, tail: str | None = None) -> str:
    """What NEW_CODE gives."""
    return word.title() + (tail or ".")


def typed(type_: str, value) -> dict:
    """A typed value, as the flow file holds it under the block's `inputs`."""
    return {"type": type_, "value": value}


needs_git = pytest.mark.skipif(GIT is None, reason="checked with the real git program, which is not installed")


# --- flows -------------------------------------------------------------------------------------------------------

def flow_file(path: Path, blocks: dict, edges=(), local_blocks=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {} if local_blocks is None else {"local_blocks": local_blocks}
    data.update(blocks=blocks, edges=list(edges))
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def greeting_blocks(out: Path, words, tail_line=(), typed_values=None, said=None) -> tuple[dict, list]:
    """For each word a copy greet_<word> of the Local block shout, given its word by a line from say_<word> (only the
    words in `said`, when given); the copies named in `tail_line` also get '?!' on their tail by a line from the block
    tail; `typed_values`: the copies' typed values, {copy: {hole: typed value}}; save writes what each copy gave, by
    word, to `out`. The blocks and the edges."""
    blocks, edges = {"start": {"type": "trigger.manual"}}, []
    if tail_line:
        blocks["tail"] = python("output = '?!'")
        edges.append("start.start -> tail.after")
    for word in words:
        copy_ = f"greet_{word}"
        if said is None or word in said:
            blocks[f"say_{word}"] = python(f"output = {word!r}")
            edges += [f"start.start -> say_{word}.after", f"say_{word}.output -> {copy_}.word"]
        blocks[copy_] = {"type": "local.shout", **({"inputs": typed_values[copy_]}
                                                     if typed_values and copy_ in typed_values else {})}
        edges.append(f"{copy_}.loud -> save.{word}")
        if copy_ in tail_line:
            edges.append(f"tail.output -> {copy_}.tail")
    blocks["save"] = writer(out, *words)
    return blocks, edges


def greetings(folder: Path, words, tail_line=(), typed_values=None, said=None, definition=SHOUT):
    """folder/greetings.yaml: greeting_blocks, the Local block shout being `definition`, save writing to out.json
    beside the folder. The flow and out.json."""
    out = folder.parent / "out.json"
    blocks, edges = greeting_blocks(out, words, tail_line, typed_values, said)
    return flow_file(folder / "greetings.yaml", blocks, edges, {"shout": definition}), out


def source_flow(folder: Path) -> tuple[Path, Path]:
    """A Python Code block `filter` running SHOUT's code (inputs word and tail, output loud), its word by a line from
    say, its tail a typed value ('?!'); save writes what it gave to out.json beside the folder."""
    out = folder.parent / "out.json"
    return flow_file(folder / "shouting.yaml", {
        "start": {"type": "trigger.manual"},
        "say": python("output = 'ana'"),
        "filter": {"type": "logic.python", "config": {key: SHOUT[key] for key in
                                                       ("inputs", "outputs", "use_environment", "code")},
                   "inputs": {"tail": typed("text", "?!")}},
        "save": writer(out, "loud"),
    }, ["start.start -> say.after", "say.output -> filter.word", "filter.loud -> save.loud"]), out


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def global_shout(tmp_path):
    """A Global block titled Shout (user.shout) in the blocks folder the editor starts with (list it before `win`); its
    code is not SHOUT's."""
    return write_reusable_block(tmp_path / "home" / "blocks", "Shout", "Says a word with three !.", "word: a word",
                                {"inputs": ["word"], "outputs": ["loud"], "use_environment": False,
                                 "code": "loud = word + '!!!'"})


# --- what the user does ------------------------------------------------------------------------------------------

def click(tab, block: str):
    """Click the block's title on the canvas: it is the one selected, and Properties shows it."""
    settle()  # (an opened flow centres itself just after)
    node = tab.scene.nodes[block]
    spot = node.mapToScene(QPointF(node.footer.rect().center().x(), HEADER_H / 2))
    tab.view.centerOn(spot)
    settle()
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, tab.view.mapFromScene(spot))
    settle()
    assert tab.scene.selected_block_ids() == [block], (block, tab.scene.selected_block_ids())


def press_delete(win, qtbot, tab):
    """The Delete key on the canvas (the selected blocks or line), the editor's window the active one."""
    qtbot.waitExposed(win)
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=5_000)
    tab.view.setFocus()
    QTest.keyClick(tab.view, Qt.Key_Delete)
    settle()


def show_tab(win, tab):
    """Click the flow's tab."""
    bar = win.tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.tabs.indexOf(tab)).center())
    settle()
    assert win.current is tab


def copy_block(win, tab, block: str):
    """Click the flow's tab and the block, Edit → Copy."""
    QApplication.clipboard().setText("")
    show_tab(win, tab)
    click(tab, block)
    menu(win, "Copy").trigger()
    settle()
    assert QApplication.clipboard().text(), "nothing was copied"


def paste(win) -> list[str]:
    """Edit → Paste; the blocks it added (selected after it)."""
    tab = win.current
    before = set(tab.doc.blocks)
    menu(win, "Paste").trigger()
    settle()
    return sorted(set(tab.doc.blocks) - before)


def choose(dialog, which: str):
    """Click the Global or the Local radio button of the Create reusable block window."""
    radio = dialog.findChild(QRadioButton, CHOICE[which])
    assert radio is not None, (f"the window {dialog.windowTitle()!r} has no {which} choice (a QRadioButton named "
                               f"{CHOICE[which]!r})")
    QTest.mouseClick(radio, Qt.LeftButton, Qt.NoModifier, radio.rect().center())
    assert radio.isChecked(), f"{which} is not chosen after a click"


def create(win, qtbot, tab, block: str, which: str, name: str, what=SHOUT["description"],
           expects=SHOUT["inputs_help"]) -> list[str]:
    """Right-click the block → Create reusable block…: choose Global or Local, fill in the window, OK. The menu's
    items."""
    def do(dialog):
        choose(dialog, which)
        fill_all(dialog, name, what, expects)
        press(dialog, "OK")

    items = in_the_window(win, qtbot, lambda: right_click(qtbot, win, tab, block, "title", CREATE[:-1]), do)
    assert CREATE in items, items
    return items


def answer(monkeypatch, button) -> list:
    """The questions asked from now on (QMessageBox.question) are answered with `button`; each (title, text)."""
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda parent, title, text, *a, **k: asked.append((title, text)) or button))
    return asked


def promote(qtbot, win, tab, block: str):
    items = right_click(qtbot, win, tab, block, "title", PROMOTE)
    assert any(item.startswith(PROMOTE) for item in items), items


# --- what the user sees ------------------------------------------------------------------------------------------

def saved(win, tab) -> dict:
    """File → Save (the flow's tab clicked first); the flow file as saved. L18: it holds a definition for every Local
    block its copies use, and none that no copy uses."""
    show_tab(win, tab)
    menu(win, "Save").trigger()
    settle()
    assert not tab.doc.dirty, "the flow was not saved"
    data = yaml.safe_load(tab.doc.path.read_text(encoding="utf-8"))
    used = {spec["type"][len(LOCAL):] for spec in data["blocks"].values()
            if str(spec.get("type")).startswith(LOCAL)}
    defined = set(data.get("local_blocks") or {})
    assert defined == used, f"L18: the file defines {sorted(defined)}; its copies use {sorted(used)}"
    return data


def code_box(win) -> CodeEdit:
    box = win.properties.widget().findChild(CodeEdit, CODE_BOX)
    assert box is not None, f"Properties shows no code box named {CODE_BOX!r}"
    return box


def edit_code(win, code: str):
    """Write `code` in the copy's code box (as when you type it and leave the box)."""
    box = code_box(win)
    box.setPlainText(code)
    box.editing_finished.emit()
    settle()


def note(win) -> str | None:
    label = win.properties.widget().findChild(QLabel, SHARED_NOTE)
    return None if label is None else label.text()


def in_tree(win, title: str) -> bool:
    return bool(win.palette_panel.tree.findItems(title, Qt.MatchExactly | Qt.MatchRecursive))


def ports(tab, block: str) -> tuple[list, list]:
    """The block's holes on the canvas, as drawn; a block the canvas does not know fails."""
    node = tab.scene.nodes[block]
    assert node.known, f"{block} is drawn as an unknown block: {node.type_title}"
    return node.inputs, node.outputs


def status_bar(win) -> str:
    return win.statusBar().currentMessage()


def ran(win, flow: Path, cwd: Path):
    result = taskloom(win, "run", flow, cwd=cwd)
    assert result.returncode == 0, result.stdout + result.stderr[-3000:]


# --- RB-1, RB-3: Create reusable block… asks Global or Local ----------------------------------------------------

@pytest.mark.invariant("RB-1")
@pytest.mark.invariant("RB-3")
@pytest.mark.invariant("TV-8")
def test_create_local_turns_the_source_block_into_the_first_copy_kept_in_the_flow_file_not_in_the_tree(win, qtbot,
                                                                                                        tmp_path):
    """L1, the user's case: right-click a Python Code block → Create reusable block…, Local, OK. The block itself is
    the first copy: the same name, holes, line and typed value, its type local.shout; the definition (title, what it
    does, what its inputs expect, inputs, outputs, environment choice and code) is in the flow file. Nothing is in the
    blocks tree or the blocks folder; Properties shows its code; the flow runs as before. One Undo gives back the
    Python Code block, and the file no definition (L18)."""
    flow, out = source_flow(tmp_path / "flows")
    tab = win.open_flow(flow)
    settle()
    before = copy.deepcopy(tab.doc.data)
    holes = ports(tab, "filter")

    create(win, qtbot, tab, "filter", "Local", "Shout")

    spec = tab.doc.blocks["filter"]
    assert spec["type"] == "local.shout" and not spec.get("config"), spec
    assert spec.get("inputs") == {"tail": typed("text", "?!")}  # its typed value stays
    assert set(tab.doc.blocks) == set(before["blocks"]) and tab.doc.data["edges"] == before["edges"]
    assert spec["ui"] == before["blocks"]["filter"]["ui"]
    assert ports(tab, "filter") == holes
    assert user_created(win) is None and not in_tree(win, "Shout")  # RB-3
    assert block_files(win) == []
    click(tab, "filter")
    assert code_box(win).toPlainText() == SHOUT["code"]
    data = saved(win, tab)
    assert data["local_blocks"] == {"shout": SHOUT}
    ran(win, flow, tmp_path)
    assert read_json(out) == {"loud": shouted("ana", "?!")}
    assert block_files(win) == []

    menu(win, "Undo").trigger()
    settle()

    data = saved(win, tab)
    assert data["blocks"]["filter"] == before["blocks"]["filter"]
    assert not data.get("local_blocks")
    clean(win)


@pytest.mark.invariant("RB-1")
def test_create_global_saves_a_block_file_listed_in_the_tree_and_leaves_the_source_block_as_it_is(win, qtbot,
                                                                                                   tmp_path):
    """L1, Global: today's behaviour. A file in the blocks folder, the block under User Created in the blocks tree,
    the source block and the flow as they were; the flow file holds no Local block."""
    flow, _ = source_flow(tmp_path / "flows")
    tab = win.open_flow(flow)
    settle()
    before = yaml.safe_dump(tab.doc.data)

    create(win, qtbot, tab, "filter", "Global", "Shout")

    (path,) = block_files(win)
    assert path.name == "shout.py"
    assert discover(win.home.blocks_dir).get("user.shout").title == "Shout"
    assert user_created(win) == ["Shout"]
    assert yaml.safe_dump(tab.doc.data) == before
    data = saved(win, tab)
    assert "local_blocks" not in data and data["blocks"]["filter"]["type"] == "logic.python"
    clean(win)


# --- RB-8: a Local and a Global block cannot have the same name -------------------------------------------------

@pytest.mark.invariant("RB-8")
@pytest.mark.parametrize("which, name", [("Local", "Shout"), ("Global", "Whisper")],
                         ids=["local_named_as_a_global", "global_named_as_a_local"])
def test_a_name_a_global_or_a_local_block_already_has_is_refused(global_shout, win, qtbot, tmp_path, which, name):
    """L10: Create reusable block…, Local, named as the Global block Shout; Global, named as this flow's Local block
    Whisper: OK is refused, the window stays open and says why; nothing is written, the flow is unchanged."""
    flow = flow_file(tmp_path / "flows" / "mixed.yaml", {
        "start": {"type": "trigger.manual"}, "say": python("output = 'ana'"),
        "filter": {"type": "logic.python", "config": {"inputs": ["word"], "code": "output = word"}},
        "hush": {"type": "local.whisper"},
    }, ["start.start -> say.after", "say.output -> filter.word", "say.output -> hush.word"], {"whisper": WHISPER})
    tab = win.open_flow(flow)
    settle()
    before, made = copy.deepcopy(tab.doc.data), global_shout.read_bytes()
    seen = {}

    def do(dialog):
        choose(dialog, which)
        seen["tried"] = try_name(win, dialog, name)
        press(dialog, "Cancel")

    items = in_the_window(win, qtbot, lambda: right_click(qtbot, win, tab, "filter", "title", CREATE[:-1]), do)

    assert CREATE in items, items
    assert seen["tried"]["open"], "the window closed"
    assert seen["tried"]["messages"], "no message said why"
    assert block_files(win) == [global_shout] and global_shout.read_bytes() == made
    assert tab.doc.data == before
    assert user_created(win) == ["Shout"]
    clean(win)


# --- RB-2: one definition, shown and edited from any copy -------------------------------------------------------

@pytest.mark.invariant("RB-2")
@pytest.mark.invariant("RB-3")
def test_properties_of_any_copy_show_the_shared_definition_and_an_edit_there_changes_every_copy(win, qtbot,
                                                                                                tmp_path):
    """L3: Properties of greet_bo show the definition's inputs, outputs and code under "Shared by 2 blocks: an edit
    changes all 2". Its code edited there, greet_ana shows the new code, the file holds it once, and Run now runs it in
    greet_ana too. A third copy made with Copy and Paste: every copy says 3. Not in the blocks tree."""
    flow, out = greetings(tmp_path / "flows", ["ana", "bo"], tail_line=["greet_bo"])
    tab = win.open_flow(flow)
    settle()

    click(tab, "greet_bo")
    assert note(win) == shared(2)
    assert (field(win, "Inputs").text(), field(win, "Outputs").text()) == ("word, tail", "loud")
    assert code_box(win).toPlainText() == SHOUT["code"]

    edit_code(win, NEW_CODE)

    click(tab, "greet_ana")
    assert code_box(win).toPlainText() == NEW_CODE and note(win) == shared(2)
    data = saved(win, tab)
    assert data["local_blocks"] == {"shout": {**SHOUT, "code": NEW_CODE}}
    assert not any(data["blocks"][c].get("config") for c in ("greet_ana", "greet_bo"))
    win.run_action.trigger()
    wait_runs(qtbot, tab, 1)
    assert tab.runs[-1].status == "success"
    assert read_json(out) == {"ana": titled("ana"), "bo": titled("bo", "?!")}

    copy_block(win, tab, "greet_ana")
    (third,) = paste(win)

    assert tab.doc.blocks[third]["type"] == "local.shout"
    for block in (third, "greet_ana", "greet_bo"):
        click(tab, block)
        assert note(win) == shared(3), block
        assert code_box(win).toPlainText() == NEW_CODE, block
    assert saved(win, tab)["local_blocks"] == {"shout": {**SHOUT, "code": NEW_CODE}}
    assert not in_tree(win, "Shout") and user_created(win) is None
    clean(win)


@pytest.mark.invariant("RB-2")
def test_a_run_keeps_the_code_it_started_with_and_the_next_run_has_the_code_edited_meanwhile(win, qtbot, tmp_path):
    """L2: while a run waits in the block before the copy, the copy's code is edited: that run gives what the old code
    gives; the next Run now (which saves the flow first) gives what the new code gives."""
    folder = tmp_path / "flows"
    out = tmp_path / "out.json"
    wait = "import os, time\nwhile not os.path.exists('go.txt'):\n    time.sleep(0.05)\noutput = 'ana'\n"
    flow = flow_file(folder / "gated.yaml", {
        "start": {"type": "trigger.manual"}, "say": python(wait), "greet": {"type": "local.shout"},
        "save": writer(out, "loud"),
    }, ["start.start -> say.after", "say.output -> greet.word", "greet.loud -> save.loud"], {"shout": SHOUT})
    tab = win.open_flow(flow)
    settle()

    win.run_action.trigger()
    qtbot.waitUntil(lambda: tab.scene.nodes["say"].status == "running", timeout=60_000)
    try:
        click(tab, "greet")
        edit_code(win, NEW_CODE)
    finally:  # (the run goes on and ends, whatever happened)
        (folder / "go.txt").write_text("go", encoding="utf-8")
        wait_runs(qtbot, tab, 1)

    assert tab.runs[-1].status == "success"
    assert read_json(out) == {"loud": shouted("ana")}

    win.run_action.trigger()
    wait_runs(qtbot, tab, 2)

    assert tab.runs[-1].status == "success"
    assert read_json(out) == {"loud": titled("ana")}
    assert saved(win, tab)["local_blocks"] == {"shout": {**SHOUT, "code": NEW_CODE}}
    clean(win)


@pytest.mark.invariant("RB-2")
@pytest.mark.invariant("TV-8")
def test_renaming_the_definitions_inputs_and_output_moves_every_copys_lines_and_typed_values(win, qtbot, tmp_path):
    """L4: in greet_bo's Properties, Inputs "word, tail" -> "text, ending" and Outputs "loud" -> "said" (and the code
    to match): every copy's lines and typed values follow (greet_ana's typed ending, greet_bo's line into it,
    greet_cy's typed text), on the canvas and in the file, and the flow runs."""
    flow, out = greetings(tmp_path / "flows", ["ana", "bo", "cy"], tail_line=["greet_bo"], said=["ana", "bo"],
                          typed_values={"greet_ana": {"tail": typed("text", "?!")},
                                        "greet_cy": {"word": typed("text", "cy")}})
    tab = win.open_flow(flow)
    settle()

    click(tab, "greet_bo")
    type_in(field(win, "Inputs"), "text, ending")
    settle()
    click(tab, "greet_bo")
    type_in(field(win, "Outputs"), "said")
    settle()
    click(tab, "greet_bo")
    edit_code(win, "said = text.upper() + (ending or '!')\n")

    assert error_shown(win) == ""
    for copy_ in ("greet_ana", "greet_bo", "greet_cy"):
        assert ports(tab, copy_) == (["after", "text", "ending"], ["done", "said", "error"]), copy_
    data = saved(win, tab)
    assert data["local_blocks"]["shout"]["inputs"] == ["text", "ending"]
    assert data["local_blocks"]["shout"]["outputs"] == ["said"]
    assert data["blocks"]["greet_ana"]["inputs"] == {"ending": typed("text", "?!")}
    assert data["blocks"]["greet_cy"]["inputs"] == {"text": typed("text", "cy")}
    assert {"say_ana.output -> greet_ana.text", "say_bo.output -> greet_bo.text", "tail.output -> greet_bo.ending",
            "greet_ana.said -> save.ana", "greet_bo.said -> save.bo", "greet_cy.said -> save.cy"} <= set(data["edges"])
    ran(win, flow, tmp_path)
    assert read_json(out) == {"ana": shouted("ana", "?!"), "bo": shouted("bo", "?!"), "cy": shouted("cy")}
    clean(win)


# --- RB-10: an input copies use cannot be removed from the definition -------------------------------------------

@pytest.mark.invariant("RB-10")
def test_removing_an_input_that_copies_use_is_refused_naming_each_copy_and_what_it_has_until_they_are_cleared(
        win, qtbot, tmp_path):
    """L5: Inputs "word, tail" -> "word" from greet_cy's Properties, while greet_ana has a line on tail and greet_bo a
    typed value there: refused, the message names both and what each has, not greet_cy; nothing changes. The line
    deleted and the value cleared ("Clear value"), the same edit removes the input."""
    flow, _ = greetings(tmp_path / "flows", ["ana", "bo", "cy"], tail_line=["greet_ana"],
                        typed_values={"greet_bo": {"tail": typed("text", "?!")}})
    tab = win.open_flow(flow)
    settle()
    before = copy.deepcopy(tab.doc.data)

    click(tab, "greet_cy")
    type_in(field(win, "Inputs"), "word")
    settle()

    said = error_shown(win)
    assert REFUSED.format(hole="tail") in said, said
    assert "greet_ana has a line from tail.output" in said and "greet_bo has a typed value" in said, said
    assert "greet_cy" not in said, said
    assert tab.doc.data == before
    assert saved(win, tab)["local_blocks"] == {"shout": SHOUT}

    tab.scene.clearSelection()
    next(e for e in tab.scene.edge_items if e.text == "tail.output -> greet_ana.tail").setSelected(True)
    press_delete(win, qtbot, tab)
    right_click(qtbot, win, tab, "greet_bo", ("tail", False), "Clear value")
    assert "tail" not in (tab.doc.blocks["greet_bo"].get("inputs") or {}), "Clear value did not clear it"
    click(tab, "greet_cy")
    type_in(field(win, "Inputs"), "word")
    settle()

    assert error_shown(win) == ""
    for copy_ in ("greet_ana", "greet_bo", "greet_cy"):
        assert ports(tab, copy_)[0] == ["after", "word"], copy_
    data = saved(win, tab)
    assert data["local_blocks"]["shout"]["inputs"] == ["word"]
    assert not [e for e in data["edges"] if e.endswith(".tail")]
    clean(win)


# --- RB-5: deleting the last copy removes the definition ---------------------------------------------------------

@pytest.mark.invariant("RB-5")
def test_deleting_the_copies_one_by_one_removes_the_definition_with_the_last_and_one_undo_brings_back_both(
        win, qtbot, tmp_path):
    """L6: three copies deleted one by one with the Delete key: the definition stays while a copy is left and goes
    with the last; one Undo brings back the last copy and the definition; Undo again brings back the others, the flow
    as it was."""
    flow, _ = greetings(tmp_path / "flows", ["ana", "bo", "cy"])
    tab = win.open_flow(flow)
    settle()
    original = saved(win, tab)

    for left, copy_ in zip((2, 1, 0), ("greet_ana", "greet_bo", "greet_cy")):
        click(tab, copy_)
        press_delete(win, qtbot, tab)
        assert copy_ not in tab.doc.blocks, copy_
        data = saved(win, tab)
        assert bool(data.get("local_blocks")) == bool(left), (copy_, data.get("local_blocks"))

    menu(win, "Undo").trigger()
    settle()

    data = saved(win, tab)
    assert data["blocks"]["greet_cy"]["type"] == "local.shout" and data["local_blocks"] == {"shout": SHOUT}
    assert ports(tab, "greet_cy")[0] == ["after", "word", "tail"]
    menu(win, "Undo").trigger()
    menu(win, "Undo").trigger()
    settle()
    data = saved(win, tab)
    assert (data["local_blocks"], data["blocks"], data["edges"]) == (original["local_blocks"], original["blocks"],
                                                                    original["edges"])
    clean(win)


# --- RB-6: Copy and Paste carry the definition -------------------------------------------------------------------

@pytest.mark.invariant("RB-6")
@pytest.mark.invariant("TV-8")
def test_the_last_copy_copied_then_deleted_is_pasted_with_its_definition_and_typed_value_in_another_tab_and_back(
        win, qtbot, monkeypatch, tmp_path):
    """L7: the only copy, with a typed value, copied, then deleted (its definition goes with it); pasted in a new
    flow's tab: the copy with its typed value and the definition, drawn with its holes, its code in Properties, both
    in the file saved. Pasted back in its own flow: the definition is back there too."""
    flow, _ = greetings(tmp_path / "flows", ["ana"], typed_values={"greet_ana": {"tail": typed("text", "?!")}})
    source = win.open_flow(flow)
    settle()

    copy_block(win, source, "greet_ana")
    press_delete(win, qtbot, source)
    data = saved(win, source)
    assert "greet_ana" not in data["blocks"] and not data.get("local_blocks")
    menu(win, "New flow").trigger()
    target = win.current
    settle()
    (pasted,) = paste(win)

    assert target is not source and target.doc.blocks[pasted]["type"] == "local.shout"
    assert target.doc.blocks[pasted].get("inputs") == {"tail": typed("text", "?!")}
    assert ports(target, pasted)[0] == ["after", "word", "tail"]
    click(target, pasted)
    assert code_box(win).toPlainText() == SHOUT["code"]
    (tmp_path / "other").mkdir()
    save_as(monkeypatch, tmp_path / "other" / "target.yaml")
    assert saved(win, target)["local_blocks"] == {"shout": SHOUT}

    show_tab(win, source)
    (back,) = paste(win)

    assert source.doc.blocks[back]["type"] == "local.shout"
    assert saved(win, source)["local_blocks"] == {"shout": SHOUT}
    clean(win)


@pytest.mark.invariant("RB-6")
def test_a_copy_pasted_into_a_flow_whose_local_block_of_that_name_has_the_same_code_joins_it(win, qtbot, tmp_path):
    """L8, same code: greet_ana copied from one flow, pasted into another whose Local block shout is the same: it is
    a copy of that one (one definition), every copy there says "Shared by 2 blocks", and nothing was renamed."""
    source = win.open_flow(greetings(tmp_path / "a", ["ana"])[0])
    target = win.open_flow(greetings(tmp_path / "b", ["bo"])[0])
    settle()

    copy_block(win, source, "greet_ana")
    show_tab(win, target)
    (pasted,) = paste(win)

    assert not status_bar(win).startswith("The pasted Local block"), status_bar(win)
    assert target.doc.blocks[pasted]["type"] == "local.shout"
    assert saved(win, target)["local_blocks"] == {"shout": SHOUT}
    click(target, "greet_bo")
    assert note(win) == shared(2)
    assert not source.doc.dirty
    clean(win)


@pytest.mark.invariant("RB-6")
def test_a_copy_pasted_into_a_flow_whose_local_block_of_that_name_has_other_code_is_kept_renamed_and_said(
        win, qtbot, tmp_path):
    """L8, other code: the pasted definition is kept as shout_2 with its own code, the pasted copy uses it, the flow's
    own shout and its copy are unchanged, and the status bar says so. One Undo takes back the paste and shout_2."""
    other = {**SHOUT, "code": "loud = word + '...'\n"}
    source = win.open_flow(greetings(tmp_path / "a", ["ana"])[0])
    target = win.open_flow(greetings(tmp_path / "b", ["bo"], definition=other)[0])
    settle()

    copy_block(win, source, "greet_ana")
    show_tab(win, target)
    (pasted,) = paste(win)

    assert status_bar(win).startswith(RENAMED.format(old="shout", new="shout_2")), status_bar(win)
    data = saved(win, target)
    assert set(data["local_blocks"]) == {"shout", "shout_2"} and data["local_blocks"]["shout"] == other
    assert {k: data["local_blocks"]["shout_2"][k] for k in ("inputs", "outputs", "use_environment", "code")} == {
        k: SHOUT[k] for k in ("inputs", "outputs", "use_environment", "code")}
    assert data["blocks"][pasted]["type"] == "local.shout_2" and data["blocks"]["greet_bo"]["type"] == "local.shout"
    click(target, pasted)
    assert code_box(win).toPlainText() == SHOUT["code"]
    click(target, "greet_bo")
    assert code_box(win).toPlainText() == other["code"]

    menu(win, "Undo").trigger()
    settle()

    data = saved(win, target)
    assert data["local_blocks"] == {"shout": other} and pasted not in data["blocks"]
    clean(win)


@pytest.mark.invariant("RB-6")
@pytest.mark.invariant("RB-8")
def test_a_local_block_pasted_where_a_global_block_has_its_name_is_kept_renamed_and_said(global_shout, win, qtbot,
                                                                                          monkeypatch, tmp_path):
    """L9: a flow written by hand with a Local block Shout while the Global block Shout exists; its copy pasted into a
    new flow: the definition is kept as shout_2, not titled Shout, the status bar says so; the Global block is
    untouched."""
    made = global_shout.read_bytes()
    source = win.open_flow(greetings(tmp_path / "a", ["ana"])[0])
    settle()

    copy_block(win, source, "greet_ana")
    menu(win, "New flow").trigger()
    target = win.current
    settle()
    (pasted,) = paste(win)

    assert status_bar(win).startswith(RENAMED.format(old="shout", new="shout_2")), status_bar(win)
    (tmp_path / "other").mkdir()
    save_as(monkeypatch, tmp_path / "other" / "target.yaml")
    data = saved(win, target)
    assert set(data["local_blocks"]) == {"shout_2"} and data["blocks"][pasted]["type"] == "local.shout_2"
    assert data["local_blocks"]["shout_2"].get("title") != "Shout"
    assert data["local_blocks"]["shout_2"]["code"] == SHOUT["code"]
    assert block_files(win) == [global_shout] and global_shout.read_bytes() == made
    assert user_created(win) == ["Shout"]
    clean(win)


# --- RB-4: Promote to global reusable block -----------------------------------------------------------------------

@pytest.mark.invariant("RB-4")
@pytest.mark.invariant("TV-8")
def test_promote_asks_first_then_every_copy_uses_the_global_block_and_undo_brings_the_local_back_not_the_file(
        win, qtbot, monkeypatch, tmp_path):
    """L11: Promote asks first, saying undo cannot remove the global block's file; No changes nothing. Yes: the block
    file is in the blocks folder and the block under User Created, both copies are copies of it with their own typed
    values, the flow file holds no definition, and the flow runs. Undo: the copies are Local again with the definition
    in the file; the block file and the tree's entry stay."""
    flow, out = greetings(tmp_path / "flows", ["ana", "bo"],
                          typed_values={"greet_ana": {"tail": typed("text", "?!")},
                                        "greet_bo": {"tail": typed("text", "!!")}})
    tab = win.open_flow(flow)
    settle()
    before = copy.deepcopy(tab.doc.data)
    asked = answer(monkeypatch, QMessageBox.No)

    promote(qtbot, win, tab, "greet_ana")

    assert len(asked) == 1 and UNDO_WORDS in asked[0][1], asked
    assert tab.doc.data == before and block_files(win) == [] and user_created(win) is None

    asked = answer(monkeypatch, QMessageBox.Yes)
    promote(qtbot, win, tab, "greet_ana")

    assert len(asked) == 1 and UNDO_WORDS in asked[0][1], asked
    (path,) = block_files(win)
    assert path.name == "shout.py"
    registry = discover(win.home.blocks_dir)
    assert registry.warnings == [] and registry.get("user.shout").title == "Shout"
    assert user_created(win) == ["Shout"]
    for copy_, tail in (("greet_ana", "?!"), ("greet_bo", "!!")):
        assert tab.doc.blocks[copy_]["type"] == "user.shout", copy_
        assert tab.doc.blocks[copy_].get("inputs") == {"tail": typed("text", tail)}, copy_
        assert ports(tab, copy_)[0] == ["after", "word", "tail"]
    data = saved(win, tab)
    assert not data.get("local_blocks")
    ran(win, flow, tmp_path)
    assert read_json(out) == {"ana": shouted("ana", "?!"), "bo": shouted("bo", "!!")}
    made = path.read_bytes()

    show_tab(win, tab)
    menu(win, "Undo").trigger()
    settle()

    data = saved(win, tab)
    assert data["local_blocks"] == {"shout": SHOUT}
    assert {data["blocks"][c]["type"] for c in ("greet_ana", "greet_bo")} == {"local.shout"}
    assert data["blocks"]["greet_bo"]["inputs"] == {"tail": typed("text", "!!")}
    assert block_files(win) == [path] and path.read_bytes() == made
    assert user_created(win) == ["Shout"]
    clean(win)


@pytest.mark.invariant("RB-4")
def test_promote_with_a_name_a_global_block_has_asks_for_another_and_writes_over_nothing(global_shout, win, qtbot,
                                                                                         monkeypatch, tmp_path):
    """L11, taken name: the Global block Shout exists (other code); Promote of the Local block Shout asks for another
    name; given "Loud words", the block is saved as loud_words.py, every copy uses it, Shout's file is unchanged, and
    the flow runs with the Local block's code."""
    made = global_shout.read_bytes()
    flow, out = greetings(tmp_path / "flows", ["ana", "bo"], tail_line=["greet_bo"])
    tab = win.open_flow(flow)
    settle()
    asked = answer(monkeypatch, QMessageBox.Yes)

    in_the_window(win, qtbot, lambda: promote(qtbot, win, tab, "greet_ana"),
                  lambda dialog: (fill(dialog, "Name", "Loud words"), press(dialog, "OK")))

    assert len(asked) == 1 and UNDO_WORDS in asked[0][1], asked
    assert sorted(p.name for p in block_files(win)) == ["loud_words.py", "shout.py"]
    assert global_shout.read_bytes() == made
    registry = discover(win.home.blocks_dir)
    assert registry.get("user.loud_words").title == "Loud words" and registry.get("user.shout").title == "Shout"
    assert sorted(user_created(win)) == ["Loud words", "Shout"]
    assert {tab.doc.blocks[c]["type"] for c in ("greet_ana", "greet_bo")} == {"user.loud_words"}
    assert not saved(win, tab).get("local_blocks")
    ran(win, flow, tmp_path)
    assert read_json(out) == {"ana": shouted("ana"), "bo": shouted("bo", "?!")}
    clean(win)


# --- RB-2, RB-7: Save as, reopening, Commit History ---------------------------------------------------------------

@pytest.mark.invariant("RB-2")
@pytest.mark.invariant("RB-7")
def test_save_as_then_reopen_keeps_the_local_blocks_and_the_copy_runs_with_no_other_file(win, qtbot, monkeypatch,
                                                                                         tmp_path):
    """L12: File → Save as… to another folder: the new file holds the definition; closed and opened again, its copies
    are drawn with their holes and Properties show the shared code; `taskloom run` on it gives each copy's result,
    with nothing in the blocks folder."""
    flow, out = greetings(tmp_path / "flows", ["ana", "bo"], tail_line=["greet_bo"])
    tab = win.open_flow(flow)
    settle()
    moved = tmp_path / "moved" / "greetings.yaml"
    moved.parent.mkdir()
    save_as(monkeypatch, moved)

    menu(win, "Save as…").trigger()
    settle()

    assert tab.doc.path.resolve() == moved.resolve()
    assert saved(win, tab)["local_blocks"] == {"shout": SHOUT}
    menu(win, "Close flow").trigger()
    settle()
    assert tab not in win.tabs_list()
    again = win.open_flow(moved)
    settle()
    assert ports(again, "greet_ana")[0] == ["after", "word", "tail"]
    click(again, "greet_bo")
    assert code_box(win).toPlainText() == SHOUT["code"] and note(win) == shared(2)
    ran(win, moved, tmp_path)
    assert read_json(out) == {"ana": shouted("ana"), "bo": shouted("bo", "?!")}
    assert block_files(win) == []
    clean(win)


@pytest.fixture
def own_git(tmp_path, monkeypatch):
    """The test's own Git config (an empty global config file, no system config, no GIT_* variables); the windows it
    opened are closed after it."""
    config = tmp_path / "gitconfig"
    config.write_bytes(b"")
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    if inside_a_repository(tmp_path):
        pytest.skip(f"{tmp_path} is inside a Git repository: these tests need folders outside any")
    before = set(QApplication.topLevelWidgets())
    yield
    for window in QApplication.topLevelWidgets():
        try:
            if window not in before and window.isVisible():
                window.close()
        except RuntimeError:  # (already deleted)
            pass
    settle()


def version(out: Path, definition: dict, words, notes=None) -> bytes:
    """greetings.yaml's bytes, as Taskloom writes them, for a version whose shout is `definition`, with a copy of it per
    word (greeting_blocks, save writing to `out`), each block with its place on the canvas (a restored version's blocks
    are drawn where it says)."""
    blocks, edges = greeting_blocks(out, words)
    rows = {}
    for block, spec in blocks.items():
        x = {"start": -300, "say": 0, "greet": 300, "save": 600}[block.split("_")[0]]
        rows[x] = rows.get(x, -1) + 1
        spec["ui"] = {"x": x, "y": 150 * rows[x]}
    data = {"taskloom": 1, "name": "greetings", "params": {}, "local_blocks": {"shout": definition},
            "blocks": blocks, "edges": edges}
    if notes:
        data["notes"] = notes
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")


ONE_INPUT = {**SHOUT, "inputs": ["word"], "code": "loud = word.upper() + '?'\n"}  # an older definition of shout


@needs_git
@pytest.mark.invariant("RB-2")
@pytest.mark.invariant("RB-7")
def test_restoring_an_older_commit_with_another_definition_gives_the_copies_that_definition(own_git, win, qtbot,
                                                                                            tmp_path):
    """L12: Commit History, the older version (shout with one input and other code), Restore this version: the copies
    are drawn with that version's holes, Properties show its code, the file saved holds it, and the flow runs it."""
    folder, out = tmp_path / "flows", tmp_path / "out.json"
    when = dates(2)
    ids = make_history(folder, [("One input", {"greetings.yaml": version(out, ONE_INPUT, ["ana", "bo"])}, when[0]),
                                ("Add a tail", {"greetings.yaml": version(out, SHOUT, ["ana", "bo"])}, when[1])])
    tab = win.open_flow(folder / "greetings.yaml")
    settle()
    assert ports(tab, "greet_bo")[0] == ["after", "word", "tail"]
    window = open_history(win, qtbot, "greetings.yaml")
    window.click_card(1)
    qtbot.waitUntil(lambda: window.selected_id(ids) == ids[0], timeout=3_000)

    QTest.mouseClick(window.restore, Qt.LeftButton)
    settle()

    assert ports(tab, "greet_bo")[0] == ["after", "word"]
    click(tab, "greet_bo")
    assert code_box(win).toPlainText() == ONE_INPUT["code"]
    assert saved(win, tab)["local_blocks"] == {"shout": ONE_INPUT}
    ran(win, folder / "greetings.yaml", tmp_path)
    assert read_json(out) == {"ana": "ANA?", "bo": "BO?"}
    clean(win)


@needs_git
@pytest.mark.invariant("RB-7")
def test_commit_history_previews_and_searches_each_version_with_its_own_local_definitions(own_git, win, qtbot,
                                                                                          tmp_path):
    """L13: three versions: v1's shout has one input and "zebra" in its code, v2 has a text "Zebra crossing" on the
    canvas, v3 has neither. The preview of v1 draws its copy with v1's holes, of v3 with v3's; "zebra" finds v1 in the
    code (listed first) and v2 in the flow file."""
    out = tmp_path / "out.json"
    zebra = {**SHOUT, "inputs": ["word"], "code": "loud = word.upper() + ' zebra'\n"}
    when = dates(3)
    ids = make_history(tmp_path / "flows", [
        ("First version", {"greetings.yaml": version(out, zebra, ["ana"])}, when[0]),
        ("Explain the crossing", {"greetings.yaml": version(out, SHOUT, ["ana"], {"note_1": {
            "text": "Zebra crossing", "x": 0, "y": 300}})}, when[1]),
        ("Tidy up", {"greetings.yaml": version(out, SHOUT, ["ana"])}, when[2])])
    v1, v2, v3 = ids
    win.open_flow(tmp_path / "flows" / "greetings.yaml")
    settle()
    window = open_history(win, qtbot, "greetings.yaml")

    window.click_card(2)
    qtbot.waitUntil(lambda: window.selected_id(ids) == v1, timeout=3_000)
    drawn = window.nodes()["greet_ana"]
    assert drawn.known and drawn.inputs == ["after", "word"], (drawn.type_title, drawn.inputs)
    window.click_card(0)
    qtbot.waitUntil(lambda: window.selected_id(ids) == v3, timeout=3_000)
    drawn = window.nodes()["greet_ana"]
    assert drawn.known and drawn.inputs == ["after", "word", "tail"], (drawn.type_title, drawn.inputs)

    window.type("zebra")

    def found():
        assert window.ids(ids) == [v1, v2]
    qtbot.waitUntil(found, timeout=3_000)
    where = dict(zip(window.ids(ids), window.cards()))
    assert "in the code of" in where[v1] and re.search(r"in the code of .*\b(shout|greet_ana)\b", where[v1]), where[v1]
    assert "in the flow file" in where[v2], where[v2]
    clean(win)


# --- RB-9: Validate in the editor ---------------------------------------------------------------------------------

@pytest.mark.invariant("RB-9")
def test_validate_in_the_editor_reports_a_problem_in_a_definition_once_naming_each_of_its_copies(win, tmp_path):
    """The editor's Validate on a definition whose Inputs hold "first word", with three copies: the box of problems
    and the Log say it once, naming the definition and each copy, and nothing else."""
    copies = ["greet_ana", "greet_bo", "greet_cy"]
    blocks, edges = {"start": {"type": "trigger.manual"}}, []
    for copy_ in copies:  # (a line into each copy's tail, an input the definition has)
        blocks[f"say_{copy_}"] = python("output = '?!'")
        blocks[copy_] = {"type": "local.shout"}
        edges += [f"start.start -> say_{copy_}.after", f"say_{copy_}.output -> {copy_}.tail"]
    flow = flow_file(tmp_path / "flows" / "broken.yaml", blocks, edges,
                     {"shout": {**SHOUT, "inputs": ["first word", "tail"]}})
    win.open_flow(flow)
    settle()

    menu(win, "Validate").trigger()

    assert [(kind, title) for kind, (title, _) in win.message_boxes] == [("warning", "The flow has problems")]
    problems = win.message_boxes[0][1][1].splitlines()
    win.message_boxes.clear()
    about = [p for p in problems if "first word" in p]
    assert len(about) == 1 and "shout" in about[0] and all(c in about[0] for c in copies), problems
    assert problems == about, problems
    clean(win)


@pytest.mark.invariant("RB-9")
def test_validate_in_the_editor_names_a_copy_whose_definition_is_missing_from_the_flow_file(win, tmp_path):
    """A copy of whisper in a flow file (edited by hand) with no definition of whisper: Validate's box names the copy
    and says its definition is missing; the copies of shout are not named."""
    flow, _ = greetings(tmp_path / "flows", ["ana"])
    data = yaml.safe_load(flow.read_text(encoding="utf-8"))
    data["blocks"]["hush_bo"] = {"type": "local.whisper"}
    data["edges"].append("say_ana.output -> hush_bo.word")
    flow.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    win.open_flow(flow)
    settle()

    menu(win, "Validate").trigger()

    assert [(kind, title) for kind, (title, _) in win.message_boxes] == [("warning", "The flow has problems")]
    problems = win.message_boxes[0][1][1].splitlines()
    win.message_boxes.clear()
    named = [p for p in problems if "hush_bo" in p]
    assert len(named) == 1, problems
    assert all(word in named[0].lower() for word in ("whisper", "definition", "missing")), named[0]
    assert not [p for p in problems if "greet_ana" in p], problems
    clean(win)


# --- L14: the guide -----------------------------------------------------------------------------------------------

@pytest.mark.invariant("RB-7")
def test_the_guide_tells_of_local_reusable_blocks_and_of_local_blocks_in_the_flow_file():
    """L14, docs: the guide's item on Create reusable block… tells of Global and Local, copy and paste, the "Shared
    by" note and Promote to global reusable block; its section on flow files shows local_blocks and local.<name>."""
    text = (REPO / "docs" / "guide.md").read_text(encoding="utf-8")
    items = [item for item in re.split(r"\n(?=- )|\n\s*\n", text) if "Create reusable block" in item]
    assert items, "the guide does not tell of Create reusable block…"
    told = " ".join(items)
    for words in ("Global", "Local", "Paste", "Shared by", "Promote to global reusable block"):
        assert words in told, words
    (flows,) = [s for s in re.split(r"\n(?=## )", text) if s.startswith("## Flows")]
    assert "local_blocks:" in flows and "local." in flows
