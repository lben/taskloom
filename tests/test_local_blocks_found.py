"""Local and Global reusable blocks (docs/promises/local-blocks.yaml): two gaps found after stage 4's first tests, under
the maintainer's rule "no surprises: the flow's behaviour must always be visible and expected; no hidden fallbacks; when
something is wrong, fail, and let the user decide".

RB-6, a definition pasted into a flow whose Local block of that name has the same code: "the same code" is the same
inputs, outputs, use_environment and code (a Python Code block's settings, what every copy runs). Another title, What it
does (description) or What its inputs expect (inputs_help) does not make it other code: the pasted copy joins the
flow's Local block (type local.<name>), whose definition stays as the flow has it, and nothing is renamed. Other
inputs, other code or another environment choice still keep the pasted definition under a new name, as
tests/test_local_blocks_editor.py fixes it ("The pasted Local block <name> is kept as <name>_<n>" on the status bar).

RB-8, a Local and a Global block with the same name: Create reusable block… refuses it in the flow on screen, but a
Global block can still be made later with the name or the title of another flow's Local block (from another flow's tab,
or a file put in the blocks folder). The flow then has both. The contract these tests fix (the implementer follows it):
- Validate (`taskloom validate`, the editor's Validate) reports it as one error that says "the same name" and names
  each block as "<title> (<type>)": the Local block as "<its title> (local.<name>)", the Global block as "<its title>
  (user.<name>)". A Local and a Global block have the same name when the Global block's type is user.<name> for the
  Local block's local.<name>, or when their titles are the same.
- A run refuses to start with the same error: `taskloom run` (exit code 2, nothing recorded) and Run now in the editor
  (the box "The flow has problems", no run).
- In the editor the copies still show as copies of their Local block (its holes, not the Global block's), so the user
  can see what to rename.

RB-6 again: "the same code" compares the settings as they take effect. Create reusable block…, Local stores
use_environment: false; ticking then unticking "Run in my Python environment" in a copy's Properties removes the key
(the default, false, applies). A copy pasted after that, its definition still holding use_environment: false, has the
same code: it joins, as above.

RB-4 with RB-8: Promote to global reusable block writes the Global block's file; Undo makes the copies Local again and
leaves the file, so the flow then has a Local and a Global block with one name and refuses to run (RB-8), which is
right. The user is told before: Promote's question keeps the words "Undo cannot remove the global block's file" and
also says that after an Undo the flow does "not run" until the Promote is redone ("Redo") or the global block is
deleted ("Delete block…" in the blocks tree). Both ways out work: after Undo, Redo, and the copies use the Global
block and Run now runs; after Undo, Delete block… on the Global block, and the flow runs with its Local block.

Through the real entry points: the editor window (the `win` fixture, its Taskloom folder a temporary one) with real
clicks, menus, the tab bar and the blocks tree; `taskloom validate` and `taskloom run` on a temporary Taskloom
folder."""

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QCheckBox, QMessageBox, QStyle, QStyleOptionButton  # noqa: E402

from taskloom.blocks.logic import write_reusable_block  # noqa: E402
from taskloom.config import Home  # noqa: E402
from tests.test_delete_user_block import delete_with_right_click  # noqa: E402
from tests.test_local_blocks import SHOUT, errors, python, shouted, write_flow, writer  # noqa: E402
from tests.test_local_blocks_editor import (RENAMED, UNDO_WORDS, answer, click, code_box, copy_block,  # noqa: E402
                                            create, flow_file, greetings, note, paste, ports, promote, read_json,
                                            saved, shared, show_tab, source_flow, status_bar)
from tests.test_paths_editor_editing import clean, field, menu, settle, taskloom  # noqa: E402
from tests.test_reusable_block import block_files, user_created  # noqa: E402

KEPT_AS = "The pasted Local block"  # how the status bar's message starts when a pasted definition is renamed
SAME_NAME = "the same name"  # in Validate's error about a Local and a Global block with one name
GLOBAL_SHOUT = ("Shout", "Says a word with three !.", "word: a word",  # a Global block titled Shout: user.shout,
                {"inputs": ["word"], "outputs": ["loud"], "use_environment": False,  # with one input, not SHOUT's two
                 "code": "loud = word + '!!!'"})


def problems_box(win) -> list[str]:
    """The lines of the one box that came up, "The flow has problems" (it is then answered)."""
    assert [(kind, title) for kind, (title, _) in win.message_boxes] == [("warning", "The flow has problems")], \
        win.message_boxes
    lines = win.message_boxes[0][1][1].splitlines()
    win.message_boxes.clear()
    return lines


def runs_recorded(home) -> int:
    db = home / "history.db"
    if not db.exists():
        return 0
    with sqlite3.connect(db) as con:
        return con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


# --- RB-6: a pasted copy joins a Local block with the same code, whatever its title and help say ---------------------

OTHER_WORDS = {  # what the target flow's shout has unlike SHOUT, its code being SHOUT's
    "another title": {"title": "Loud word"},
    "another description": {"description": "Shouts the word it gets."},
    "other inputs help": {"inputs_help": "word: any word. tail: the end."},
    "all three": {"title": "Loud word", "description": "Shouts the word it gets.",
                  "inputs_help": "word: any word. tail: the end."},
}


@pytest.mark.invariant("RB-6")
@pytest.mark.parametrize("other", list(OTHER_WORDS.values()), ids=list(OTHER_WORDS))
def test_a_copy_pasted_where_the_local_block_of_its_name_has_the_same_code_but_other_words_joins_it(
        win, qtbot, tmp_path, other):
    """The user copies greet_ana (a copy of the Local block shout) in one flow's tab (Edit → Copy) and pastes it
    (Edit → Paste) in another tab, whose Local block shout has the same inputs, outputs, environment choice and code
    but another title, What it does or What its inputs expect. The pasted block is a copy of that flow's shout (type
    local.shout): the flow file still holds one definition, shout, as it was; Properties of the pasted copy show its
    code under "Shared by 2 blocks: an edit changes all 2"; the status bar says nothing was kept under a new name."""
    theirs = {**SHOUT, **other}
    source = win.open_flow(greetings(tmp_path / "a", ["ana"])[0])
    target = win.open_flow(greetings(tmp_path / "b", ["bo"], definition=theirs)[0])
    settle()

    copy_block(win, source, "greet_ana")
    show_tab(win, target)
    (pasted,) = paste(win)
    told = status_bar(win)
    data = saved(win, target)  # (first: whatever the checks find, no unsaved change is left to ask about)

    assert not told.startswith(KEPT_AS) and "kept as" not in told, told
    assert data["blocks"][pasted]["type"] == "local.shout", data["blocks"][pasted]
    assert data["local_blocks"] == {"shout": theirs}
    click(target, pasted)
    assert note(win) == shared(2)
    assert code_box(win).toPlainText() == SHOUT["code"]
    assert not source.doc.dirty
    clean(win)


OTHER_CODE = {  # what the target flow's shout has unlike SHOUT: each makes it other code
    "other code and another title": {"code": "loud = word + '...'\n", "title": "Loud word"},
    "other inputs": {"inputs": ["word", "tail", "times"]},
    "another environment choice": {"use_environment": True},
}


@pytest.mark.invariant("RB-6")
@pytest.mark.parametrize("other", list(OTHER_CODE.values()), ids=list(OTHER_CODE))
def test_a_copy_pasted_where_the_local_block_of_its_name_has_other_code_is_still_kept_under_a_new_name(
        win, qtbot, tmp_path, other):
    """The same Copy and Paste, the other tab's shout having other code (and another title), other inputs, or another
    environment choice: the pasted definition is kept as shout_2 with SHOUT's inputs, outputs, environment choice and
    code, the pasted block is a copy of it, the flow's own shout is unchanged, and the status bar says "The pasted
    Local block shout is kept as shout_2"."""
    theirs = {**SHOUT, **other}
    source = win.open_flow(greetings(tmp_path / "a", ["ana"])[0])
    target = win.open_flow(greetings(tmp_path / "b", ["bo"], definition=theirs)[0])
    settle()

    copy_block(win, source, "greet_ana")
    show_tab(win, target)
    (pasted,) = paste(win)
    told = status_bar(win)
    data = saved(win, target)  # (first: whatever the checks find, no unsaved change is left to ask about)

    assert told.startswith(RENAMED.format(old="shout", new="shout_2")), told
    assert set(data["local_blocks"]) == {"shout", "shout_2"} and data["local_blocks"]["shout"] == theirs
    runs = ("inputs", "outputs", "use_environment", "code")
    assert {k: data["local_blocks"]["shout_2"][k] for k in runs} == {k: SHOUT[k] for k in runs}
    assert data["blocks"][pasted]["type"] == "local.shout_2" and data["blocks"]["greet_bo"]["type"] == "local.shout"
    clean(win)


# --- RB-8: a Global block made later with a Local block's name or title ----------------------------------------------

CLASHES = {  # the flow's Local block: (its name, its title); the Global block is Shout (user.shout)
    "the same name and title": ("shout", "Shout"),
    "the same name, another title": ("shout", "Loud word"),
    "the same title, another name": ("yell", "Shout"),
}


def two_copies(env, name: str, title: str):
    """A flow (flows/flow.yaml) whose Local block `name`, titled `title`, runs SHOUT's code; its copies greet_ana and
    greet_bo get a word each by a line, and save writes what they gave to results.json. The flow and results.json."""
    out = env.root / "results.json"
    flow = write_flow(env, {
        "start": {"type": "trigger.manual"},
        "say_ana": python("output = 'ana'"), "say_bo": python("output = 'bo'"),
        "greet_ana": {"type": f"local.{name}"}, "greet_bo": {"type": f"local.{name}"},
        "save": writer(out, "ana", "bo"),
    }, ["start.start -> say_ana.after", "start.start -> say_bo.after", "say_ana.output -> greet_ana.word",
        "say_bo.output -> greet_bo.word", "greet_ana.loud -> save.ana", "greet_bo.loud -> save.bo"],
        {name: {**SHOUT, "title": title}})
    return flow, out


@pytest.mark.invariant("RB-8")
@pytest.mark.parametrize("name, title", list(CLASHES.values()), ids=list(CLASHES))
def test_validate_and_run_refuse_a_flow_whose_local_block_has_the_name_of_a_global_block_made_later(env, name, title):
    """A flow with a Local block (its name and title as each case says) used by two copies ran fine; then a Global
    block Shout (user.shout) is put in the blocks folder. `taskloom validate` refuses the flow with one error that
    names the Local block ("<title> (local.<name>)") and the Global block ("Shout (user.shout)") and says they have
    the same name; `taskloom run` refuses to start with the same error: no run is recorded, no copy runs."""
    flow, out = two_copies(env, name, title)
    first = env.taskloom("run", flow)
    assert first.returncode == 0, first.stderr[-3000:]  # (it ran fine before the Global block was made)
    out.unlink()
    write_reusable_block(Home(env.home).blocks_dir, *GLOBAL_SHOUT)

    validated, ran = env.taskloom("validate", flow), env.taskloom("run", flow)

    def clash(result) -> list[str]:
        return [line for line in errors(result) if SAME_NAME in line]

    assert validated.returncode == 2, validated.stdout + validated.stderr
    found = clash(validated)
    assert len(found) == 1, validated.stderr
    assert f"{title} (local.{name})" in found[0] and "Shout (user.shout)" in found[0], found[0]
    assert ran.returncode == 2, ran.stdout + ran.stderr[-3000:]
    assert clash(ran) == found, ran.stderr
    assert runs_recorded(env.home) == 1 and not out.exists()


@pytest.mark.invariant("RB-8")
def test_a_global_block_made_in_another_tab_with_a_local_blocks_name_is_named_by_validate_and_run_now_refuses(
        win, qtbot, tmp_path):
    """Tab A holds the flow greetings, with the Local block Shout (shout) and its copies greet_ana and greet_bo. In tab
    B the user right-clicks a Python Code block → Create reusable block…, Global, named Shout: it is made (tab B's
    flow has no Local block Shout), shout.py in the blocks folder. Back in tab A the copies still show as copies of
    the Local block (its holes word and tail, not the Global block's one input). Validate's box "The flow has problems"
    names the Local block Shout (local.shout) and the Global block Shout (user.shout) and says they have the same
    name, as `taskloom validate` does; Run now shows the same box with the same words and starts no run."""
    flow_a, out = greetings(tmp_path / "a", ["ana", "bo"])
    tab_a = win.open_flow(flow_a)
    tab_b = win.open_flow(flow_file(tmp_path / "b" / "other.yaml", {
        "start": {"type": "trigger.manual"}, "say": python("output = 'ana'"),
        "filter": {"type": "logic.python", "config": {key: GLOBAL_SHOUT[3][key] for key in
                                                       ("inputs", "outputs", "use_environment", "code")}},
    }, ["start.start -> say.after", "say.output -> filter.word"]))
    settle()

    create(win, qtbot, tab_b, "filter", "Global", "Shout")
    assert [path.name for path in block_files(win)] == ["shout.py"]
    show_tab(win, tab_a)

    for copy_ in ("greet_ana", "greet_bo"):
        assert ports(tab_a, copy_)[0] == ["after", "word", "tail"], copy_
    menu(win, "Validate").trigger()
    settle()
    validated = [line for line in problems_box(win) if SAME_NAME in line]
    assert len(validated) == 1, validated
    assert "Shout (local.shout)" in validated[0] and "Shout (user.shout)" in validated[0], validated[0]
    by_command = taskloom(win, "validate", flow_a, cwd=tmp_path)
    assert by_command.returncode == 2 and validated[0] in errors(by_command), by_command.stderr

    win.run_action.trigger()
    settle()

    assert [line for line in problems_box(win) if SAME_NAME in line] == validated
    assert tab_a.runs == [] and not out.exists()
    clean(win)


# --- RB-6: the environment choice compared as it takes effect --------------------------------------------------------

ENVIRONMENT = "Run in my Python environment"  # the definition's use_environment, in Properties of a copy


def environment_box(win) -> QCheckBox:
    widget = field(win, ENVIRONMENT)
    box = widget if isinstance(widget, QCheckBox) else widget.findChild(QCheckBox)
    assert box is not None, f"Properties shows no check box {ENVIRONMENT!r}"
    return box


def tick(box: QCheckBox):
    """A click on the check box's square."""
    option = QStyleOptionButton()
    box.initStyleOption(option)
    spot = box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box).center()
    QTest.mouseClick(box, Qt.LeftButton, Qt.NoModifier, spot)
    settle()


@pytest.mark.invariant("RB-6")
def test_a_copy_pasted_after_its_environment_choice_was_ticked_and_unticked_joins_its_local_block(win, qtbot, tmp_path):
    """The user makes the Python Code block filter a Local block (right-click → Create reusable block…, Local, Shout)
    and copies it (Edit → Copy). In its Properties they tick "Run in my Python environment", then untick it: the
    definition runs as before. Edit → Paste: the pasted block is a second copy of shout (local.shout), the flow file
    holds the one definition shout, not running in the Python environment, and the status bar says nothing was kept
    under a new name."""
    flow, _ = source_flow(tmp_path / "flows")
    tab = win.open_flow(flow)
    settle()
    create(win, qtbot, tab, "filter", "Local", "Shout")
    copy_block(win, tab, "filter")
    click(tab, "filter")
    assert not environment_box(win).isChecked()
    tick(environment_box(win))
    assert environment_box(win).isChecked()
    tick(environment_box(win))
    assert not environment_box(win).isChecked()

    (pasted,) = paste(win)
    told = status_bar(win)
    data = saved(win, tab)  # (first: whatever the checks find, no unsaved change is left to ask about)

    assert not told.startswith(KEPT_AS) and "kept as" not in told, told
    assert data["blocks"][pasted]["type"] == "local.shout", data["blocks"][pasted]
    assert list(data["local_blocks"]) == ["shout"], data["local_blocks"]
    assert data["local_blocks"]["shout"].get("use_environment", False) is False, data["local_blocks"]
    clean(win)


# --- RB-4 with RB-8: Promote, then Undo: said before, and both ways out work -----------------------------------------

def promoted_then_undone(win, qtbot, monkeypatch, tmp_path):
    """The user's case: the Python Code block filter made a Local block Shout (Create reusable block…, Local),
    promoted (Yes to the question), then Edit → Undo. The tab, the Global block's file and what save writes."""
    flow, out = source_flow(tmp_path / "flows")
    tab = win.open_flow(flow)
    settle()
    create(win, qtbot, tab, "filter", "Local", "Shout")
    answer(monkeypatch, QMessageBox.Yes)
    promote(qtbot, win, tab, "filter")
    (path,) = block_files(win)
    show_tab(win, tab)
    menu(win, "Undo").trigger()
    settle()
    assert tab.doc.blocks["filter"]["type"] == "local.shout" and path.exists()
    return tab, path, out


@pytest.mark.invariant("RB-4")
@pytest.mark.invariant("RB-8")
def test_promotes_question_says_that_after_an_undo_the_flow_runs_again_only_after_redo_or_delete_block(
        win, qtbot, monkeypatch, tmp_path):
    """Right-click the copy filter of the Local block Shout → Promote to global reusable block: before anything is
    written, the question says "Undo cannot remove the global block's file" and that after an Undo the flow does not
    run until the Promote is redone (Redo) or the global block is deleted (Delete block… in the blocks tree). No:
    nothing is written."""
    flow, _ = source_flow(tmp_path / "flows")
    tab = win.open_flow(flow)
    settle()
    create(win, qtbot, tab, "filter", "Local", "Shout")
    asked = answer(monkeypatch, QMessageBox.No)

    promote(qtbot, win, tab, "filter")

    assert len(asked) == 1, asked
    text = asked[0][1]
    assert UNDO_WORDS in text, text
    assert "not run" in text.lower() and "Redo" in text and "Delete block" in text, text
    assert block_files(win) == [] and tab.doc.blocks["filter"]["type"] == "local.shout"
    clean(win)


@pytest.mark.invariant("RB-4")
@pytest.mark.invariant("RB-8")
@pytest.mark.parametrize("way_out", ["Redo", "Delete block…"])
def test_after_promote_and_undo_the_flow_runs_again_once_redone_or_once_its_global_block_is_deleted(
        win, qtbot, monkeypatch, tmp_path, way_out):
    """Create reusable block…, Local, Shout; Promote (Yes); Undo: the copy filter is Local again, the Global block's
    file stays, and Run now refuses with the box "The flow has problems" naming the two blocks of the same name.
    Then the way out: Edit → Redo (the copy uses the Global block again, the flow file holds no definition), or
    right-click Shout in the blocks tree → Delete block…, Yes (the file is gone, the copy is still a copy of its Local
    block). Run now runs, and filter gives what Shout's code gives."""
    tab, path, out = promoted_then_undone(win, qtbot, monkeypatch, tmp_path)
    win.run_action.trigger()
    settle()
    assert [line for line in problems_box(win) if SAME_NAME in line], "Run now did not name the two blocks"
    assert tab.runs == []

    if way_out == "Redo":
        menu(win, "Redo").trigger()
        settle()
    else:
        delete_with_right_click(qtbot, win, "Shout", "yes")
    data = saved(win, tab)  # (first: whatever the checks find, no unsaved change is left to ask about)
    win.run_action.trigger()
    qtbot.waitUntil(lambda: bool(win.message_boxes) or (len(tab.runs) == 1 and not tab.running
                                                        and tab.runs[-1].status is not None), timeout=60_000)
    boxes = list(win.message_boxes)
    win.message_boxes.clear()  # (said below, not by the fixture)

    if way_out == "Redo":
        assert data["blocks"]["filter"]["type"] == "user.shout" and not data.get("local_blocks"), data
        assert path.exists() and user_created(win) == ["Shout"]
    else:
        assert data["blocks"]["filter"]["type"] == "local.shout" and data["local_blocks"] == {"shout": SHOUT}, data
        assert not path.exists() and user_created(win) is None
        assert ports(tab, "filter")[0] == ["after", "word", "tail"]
    assert boxes == [], boxes
    assert tab.runs[-1].status == "success", win.log.text.toPlainText()[-2000:]
    assert read_json(out) == {"loud": shouted("ana", "?!")}
    clean(win)
