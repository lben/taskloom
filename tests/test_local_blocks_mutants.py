"""Local and Global reusable blocks: what mutation testing of the feature (docs/promises/local-blocks.mutants.yaml)
found no test for, checked as the user meets it. Rows of docs/promises/local-blocks.yaml.

- What Validate says of a definition that is not a mapping or has a key no definition has, of a Local block with the
  title of a built-in or a plugin block (no Global block: it runs), and of a definition with no title whose name a
  Global block has; Properties of a copy (what its Local block does, its title) and of a block the flow cannot use.
- The words of the refusal to remove inputs copies use; reordering, replacing and removing a Local block's holes and
  emptying its Inputs; renaming its output or an input beside an HTML Template that names them.
- A pasted Local block titled as a Global block: renamed, titled with its number.
- A Python Code block beside a Local block named python keeps its own settings; Create reusable block…, Local, on a
  block written by hand with a version, in a file whose empty local_blocks: is at its end.
- The Create reusable block and Promote windows: over the editor, their words, choices and fields, how they wrap;
  the status bar's words, which go after a while; the box that says a block's settings cannot be read.
- Promote's question, Qt's own box over the editor: its words, with 1 copy or 2, its buttons, Enter answers No.
- Names refused with words; Promote of one Local block where another one's name is its title.

Through the real entry points: the editor window (the `win` fixture, its Taskloom folder a temporary one) with real
right-clicks, menus, Properties, the Delete key and Qt's own message boxes where their words or buttons are checked;
`taskloom validate` and `taskloom run` on a temporary Taskloom folder."""

import contextlib
import json
import os
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFormLayout, QLabel, QMessageBox, QRadioButton  # noqa: E402

from taskloom.blocks.logic import write_reusable_block  # noqa: E402
from taskloom.config import Home  # noqa: E402
from taskloom.editor.panels import LOCAL_NOTE, CodeEdit  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_local_blocks import SHOUT, WHISPER, errors, python, shouted, whispered, write_flow, writer  # noqa: E402
from tests.test_local_blocks_editor import (answer, click, copy_block, flow_file, greetings, paste,  # noqa: E402
                                            ports, press_delete, saved, source_flow, typed)
from tests.test_paths_editor_editing import (clean, error_shown, field, menu, press, select_only, settle,  # noqa: E402
                                             type_in)
from tests.test_paths_editor_running import right_click  # noqa: E402
from tests.test_reusable_block import block_files, fill, text_box, user_created  # noqa: E402
from tests.test_theme import in_place, strongest  # noqa: E402

# Qt's own message boxes (the win fixture records them in their place; these tests show them, to read them).
REAL_QUESTION, REAL_WARNING = QMessageBox.question, QMessageBox.warning
CREATE, PROMOTE = "Create reusable block", "Promote to global reusable block"  # the menu items (with an ellipsis)
ECHO = {"title": "Echo", "description": "Says a word again.", "inputs_help": "word: a word.", "inputs": ["word"],
        "outputs": ["again"], "use_environment": False, "code": "again = word\n"}


@pytest.fixture
def env(env):
    """conftest's temporary Taskloom folder, with an empty blocks folder."""
    Home(env.home).blocks_dir.mkdir()
    return env


@pytest.fixture
def global_shout(tmp_path):
    """A Global block titled Shout (user.shout) in the blocks folder the editor starts with (list it before `win`)."""
    return write_reusable_block(tmp_path / "home" / "blocks", "Shout", "Says a word with three !.", "word: a word",
                                {"inputs": ["word"], "outputs": ["loud"], "use_environment": False,
                                 "code": "loud = word + '!!!'"})


# --- what the user sees ------------------------------------------------------------------------------------------

@contextlib.contextmanager
def qt_boxes(kind: str):
    """Meanwhile QMessageBox.<kind> ("question", "warning") shows Qt's own box (then the win fixture's stand-in again,
    so closing the window never waits on one); yields each call's arguments (parent, title, text, ...): the title,
    which Qt leaves out of a message box on macOS, is read there."""
    calls, real, stand_in = [], {"question": REAL_QUESTION, "warning": REAL_WARNING}[kind], QMessageBox.__dict__[kind]
    setattr(QMessageBox, kind, staticmethod(lambda *a, **k: calls.append(a) or real(*a, **k)))
    try:
        yield calls
    finally:
        setattr(QMessageBox, kind, stand_in)


def windows(win, open_it, *answers):
    """open_it() opens windows that wait for an answer, one after another: the n-th is given to answers[n], which
    reads it and answers it (one still open after it is closed unanswered); one more window than answers fails the
    test. What open_it() returns."""
    handled, errors_ = [], []

    def look():
        window = QApplication.activeModalWidget()
        if window is None or window.property("answered"):
            return
        window.setProperty("answered", True)
        handled.append(window.windowTitle())
        try:
            assert len(handled) <= len(answers), f"a window no one expected: {texts(window)}"
            answers[len(handled) - 1](window)
        except BaseException as e:  # (not lost in Qt's event loop)
            errors_.append(e)
        finally:
            if window.isVisible():
                window.reject()

    timer = QTimer(win, interval=20)
    timer.timeout.connect(look)
    timer.start()
    try:
        opened = open_it()
        settle()
    finally:
        timer.stop()
    if errors_:
        raise errors_[0]
    assert len(handled) == len(answers), f"{len(handled)} windows opened, not {len(answers)}: {handled}"
    return opened


def texts(window) -> list[str]:
    return [label.text() for label in window.findChildren(QLabel) if label.isVisible() and label.text()]


def owner(window):
    """The window the system keeps it over (its transient parent; None: none)."""
    return window.windowHandle().transientParent()


def in_order(dialog) -> list:
    """The window's widgets as its layout lays them out, top to bottom (its form's left out)."""
    layout = dialog.layout()
    return [layout.itemAt(i).widget() for i in range(layout.count()) if layout.itemAt(i).widget() is not None]


def row_names(dialog) -> list[str]:
    (form,) = dialog.findChildren(QFormLayout)
    return [form.itemAt(row, QFormLayout.LabelRole).widget().text() for row in range(form.rowCount())]


def buttons(box: QMessageBox) -> list[str]:
    return sorted(b.text().replace("&", "") for b in box.buttons() if b.isVisible())


def reddest(widget) -> int:
    """How red the reddest pixel the widget is painted with is: its red less the larger of its green and blue."""
    image = in_place(widget)
    return max(c.red() - max(c.green(), c.blue())
               for c in (image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())))


def for_a_while(win) -> str:
    """The status bar's message, which goes after about 15 seconds: a timer is set to clear it."""
    bar = win.statusBar()
    timers = [t for t in bar.findChildren(QTimer, "", Qt.FindDirectChildrenOnly) if t.isActive()]
    assert len(timers) == 1 and 0 < timers[0].remainingTime() <= 16_000, (
        f"the message {bar.currentMessage()!r} stays: no timer clears it")
    return bar.currentMessage()


def properties(win) -> list[str]:
    """The texts Properties shows, top to bottom."""
    body = win.properties.widget()
    labels = [label for label in body.findChildren(QLabel) if label.isVisible() and label.text()]
    return [label.text() for label in sorted(labels, key=lambda label: label.mapTo(body, label.rect().topLeft()).y())]


def code_of(win) -> CodeEdit:
    box = win.properties.widget().findChild(CodeEdit)
    assert box is not None, "Properties shows no code box"
    return box


def write_code(win, code: str):
    """Write `code` in Properties' code box (as when you type it and leave the box)."""
    box = code_of(win)
    box.setPlainText(code)
    box.editing_finished.emit()
    settle()


def edit_names(win, tab, block: str, label: str, text: str):
    """In the block's Properties, `label` ("Inputs" or "Outputs") written as `text`, then Enter."""
    click(tab, block)
    type_in(field(win, label), text)
    settle()


# --- RB-9, RB-8: Validate on the command line --------------------------------------------------------------------

@pytest.mark.invariant("RB-9")
def test_validate_says_what_is_wrong_with_each_definition_and_still_knows_the_ones_after_it(env):
    """Three Local blocks, in this order in the flow file: shout written as a line of text (not a mapping), whisper
    with a key no definition has (colour), and echo, which is right; a copy of each. `taskloom validate` names the
    two that are wrong, each once, saying what a definition holds or which key it does not know, and nothing about
    echo or its copy: a wrong definition does not hide the ones after it."""
    flow = write_flow(env, {
        "start": {"type": "trigger.manual"}, "say": python("output = 'ana'"), "greet": {"type": "local.shout"},
        "hush": {"type": "local.whisper"}, "repeat": {"type": "local.echo"}, "save": writer(env.root / "o.json", "again"),
    }, ["start.start -> say.after", "say.output -> greet.word", "say.output -> hush.word", "say.output -> repeat.word",
        "repeat.again -> save.again"],
        {"shout": "says a word louder", "whisper": {**WHISPER, "colour": "grey"}, "echo": ECHO})

    result = env.taskloom("validate", flow)

    assert result.returncode == 2, result.stdout + result.stderr
    assert errors(result) == [
        "the Local block shout (used by greet): expected a mapping: title, description, inputs_help, inputs, outputs, "
        "code, use_environment",
        "the Local block whisper (used by hush): unknown key(s) ['colour']"], result.stderr


PLUGIN = '''\
from taskloom import Block, ports


class Hello(Block):
    type_id = "plugin.hello"
    title = "Hello"
    description = "Says hello."
    outputs = {"text": ports.Any()}

    def run(self, ctx):
        return {"text": "hello"}
'''


@pytest.mark.invariant("RB-8")
def test_a_local_block_titled_as_a_built_in_or_a_plugin_block_is_no_global_block_and_runs(env):
    """A plugin block titled Hello is installed. A flow's Local blocks are titled Python Code (a built-in block's
    title) and Hello: neither is a Global block (a file in the blocks folder), so `taskloom validate` finds nothing
    wrong and `taskloom run` gives each copy's result."""
    site = env.root / "site"
    (site / "taskloom_lb_plugin-1.0.dist-info").mkdir(parents=True)
    (site / "taskloom_lb_plugin.py").write_text(PLUGIN, encoding="utf-8")
    (site / "taskloom_lb_plugin-1.0.dist-info" / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: taskloom-lb-plugin\nVersion: 1.0\n", encoding="utf-8")
    (site / "taskloom_lb_plugin-1.0.dist-info" / "entry_points.txt").write_text(
        "[taskloom.blocks]\nhello = taskloom_lb_plugin\n", encoding="utf-8")
    env.env["PYTHONPATH"] = os.pathsep.join(p for p in (str(site), env.env.get("PYTHONPATH")) if p)
    out = env.root / "out.json"
    flow = write_flow(env, {
        "start": {"type": "trigger.manual"}, "say": python("output = 'ana'"), "greet": {"type": "local.code"},
        "hush": {"type": "local.hi"}, "save": writer(out, "loud", "soft"),
    }, ["start.start -> say.after", "say.output -> greet.word", "say.output -> hush.word", "greet.loud -> save.loud",
        "hush.soft -> save.soft"], {"code": {**SHOUT, "title": "Python Code"}, "hi": {**WHISPER, "title": "Hello"}})

    checked, ran = env.taskloom("validate", flow), env.taskloom("run", flow)

    assert checked.returncode == 0 and errors(checked) == [], checked.stderr
    assert ran.returncode == 0, ran.stderr
    assert json_of(out) == {"loud": shouted("ana"), "soft": whispered("ana")}


def json_of(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.invariant("RB-8")
def test_a_definition_with_no_title_is_named_by_its_name_when_a_global_block_has_that_name(env):
    """The Global block Shout (user.shout) is in the blocks folder; a flow's Local block shout has no title. `taskloom
    validate` says the two have the same name, naming the Local block by its name: shout (local.shout)."""
    write_reusable_block(Home(env.home).blocks_dir, "Shout", "Says a word with three !.", "word: a word",
                         {"inputs": ["word"], "outputs": ["loud"], "use_environment": False, "code": "loud = word"})
    flow = write_flow(env, {"start": {"type": "trigger.manual"}, "say": python("output = 'ana'"),
                            "greet": {"type": "local.shout"}},
                      ["start.start -> say.after", "say.output -> greet.word"],
                      {"shout": {"inputs": ["word"], "outputs": ["loud"], "code": "loud = word.upper()\n"}})

    result = env.taskloom("validate", flow)

    assert result.returncode == 2, result.stdout + result.stderr
    assert errors(result) == ["the Local block shout (used by greet): shout (local.shout) and the Global block Shout "
                              "(user.shout) have the same name: rename one of them"], result.stderr


# --- RB-2, RB-9: Properties of a copy, and of a block the flow cannot use ---------------------------------------

@pytest.mark.invariant("RB-2")
@pytest.mark.invariant("RB-9")
def test_properties_of_a_copy_say_what_its_local_block_does_and_of_a_block_the_flow_cannot_use_why(win, tmp_path):
    """Properties of a copy of shout say what it does and what its inputs expect, as its definition has them; of a
    copy of bare, whose definition has no title and neither text, are titled bare and say neither. Of a copy whose
    definition has a problem, of one whose definition is missing, and of a block whose type is not installed, they
    say so, each in its own words."""
    flow = flow_file(tmp_path / "flows" / "kinds.yaml", {
        "start": {"type": "trigger.manual"}, "greet": {"type": "local.shout"}, "plain": {"type": "local.bare"},
        "noisy": {"type": "local.loud"}, "hush": {"type": "local.whisper"}, "thing": {"type": "acme.thing"},
    }, [], {"shout": SHOUT, "bare": {"inputs": ["word"], "outputs": ["loud"], "code": "loud = word\n"},
            "loud": {**SHOUT, "inputs": ["first word"]}})
    tab = win.open_flow(flow)
    settle()

    click(tab, "greet")
    said = properties(win)
    assert said[0].startswith("<b>Shout</b>"), said
    at = said.index("<b>What it does</b>")
    assert said[at:at + 4] == ["<b>What it does</b>", SHOUT["description"], "<b>What its inputs expect</b>",
                               SHOUT["inputs_help"]], said
    click(tab, "plain")
    said = properties(win)
    assert said[0].startswith("<b>bare</b>"), said
    assert not [text for text in said if "What it" in text or text in ("None", "XXXX")], said
    click(tab, "noisy")
    assert [text for text in properties(win) if text.startswith(
        "The definition of its Local block loud has a problem: ") and "first word" in text], properties(win)
    click(tab, "hush")
    assert "The definition of its Local block whisper is missing from the flow file." in properties(win)
    click(tab, "thing")
    assert "This block type is not installed." in properties(win)
    clean(win)


# --- RB-10, RB-2: editing a Local block's Inputs and Outputs in Properties ----------------------------------------

@pytest.mark.invariant("RB-10")
def test_removing_inputs_copies_use_is_refused_in_words_naming_every_copy_and_what_to_do(win, tmp_path):
    """greet_ana and greet_bo have lines into word, greet_bo a typed value on tail. In greet_cy's Properties, Inputs
    "word, tail" -> "text" (both removed): refused, in these words, input by input, copy by copy, then what to do."""
    flow, _ = greetings(tmp_path / "flows", ["ana", "bo", "cy"], said=["ana", "bo"],
                        typed_values={"greet_bo": {"tail": typed("text", "?!")}})
    tab = win.open_flow(flow)
    settle()

    edit_names(win, tab, "greet_cy", "Inputs", "text")

    assert error_shown(win) == (
        "Cannot remove the input word: greet_ana has a line from say_ana.output; greet_bo has a line from "
        "say_bo.output. Cannot remove the input tail: greet_bo has a typed value. Delete those lines and clear those "
        "values (right-click the input → Clear value) first: every copy of a Local block has its inputs.")
    assert saved(win, tab)["local_blocks"] == {"shout": SHOUT}
    clean(win)


@pytest.mark.invariant("RB-2")
def test_reordering_replacing_and_removing_a_local_blocks_holes_moves_only_what_was_renamed(win, tmp_path):
    """Copies greet_ana and greet_bo of shout, each with a line into word; copies e1 and e2 of echo, with nothing on
    their inputs. Inputs "word, tail" -> "tail, word", then -> "word, extra": every line stays on word. Outputs
    "loud" -> "said, more": only the lines from loud go. echo's Inputs emptied: its copies have the default input."""
    blocks, edges = {"start": {"type": "trigger.manual"}}, ["start.start -> e1.after"]
    for word in ("ana", "bo"):
        blocks |= {f"say_{word}": python(f"output = {word!r}"), f"greet_{word}": {"type": "local.shout"}}
        edges += [f"start.start -> say_{word}.after", f"say_{word}.output -> greet_{word}.word",
                  f"greet_{word}.loud -> save.{word}"]
    blocks |= {"save": writer(tmp_path / "out.json", "ana", "bo"), "e1": {"type": "local.echo"},
               "e2": {"type": "local.echo"}}
    edges.append("e1.again -> e2.after")
    tab = win.open_flow(flow_file(tmp_path / "flows" / "holes.yaml", blocks, edges,
                                  {"shout": SHOUT, "echo": {**ECHO, "inputs": ["a", "b"]}}))
    settle()

    edit_names(win, tab, "greet_ana", "Inputs", "tail, word")
    assert (tab.doc.data["edges"], ports(tab, "greet_bo")[0]) == (edges, ["after", "tail", "word"]), error_shown(win)
    edit_names(win, tab, "greet_bo", "Inputs", "word, extra")
    assert (tab.doc.data["edges"], ports(tab, "greet_ana")[0]) == (edges, ["after", "word", "extra"]), error_shown(win)
    edit_names(win, tab, "greet_bo", "Outputs", "said, more")
    assert tab.doc.data["edges"] == [e for e in edges if ".loud ->" not in e], error_shown(win)
    edit_names(win, tab, "e1", "Inputs", "")

    assert error_shown(win) == ""
    assert "inputs" not in tab.doc.data["local_blocks"]["echo"]
    assert ports(tab, "e1")[0] == ports(tab, "e2")[0] == ["after", "input"]
    clean(win)


@pytest.mark.invariant("RB-2")
def test_renaming_a_local_blocks_output_or_input_keeps_the_names_a_template_gives_them_and_other_typed_values(
        win, tmp_path):
    """Two Local blocks, upper and lower, each with inputs text and tail and an output text, two copies each with typed
    values; an HTML Template names each copy's output (first, second, third, fourth). upper's output text -> said:
    the Template's names follow it, and the copies' typed values stay on their inputs, text too. lower's input text ->
    words: the Template's names stay, and the typed values on tail stay there."""
    definition = {"inputs": ["text", "tail"], "outputs": ["text"], "use_environment": False,
                  "code": "text = text.upper() + (tail or '')\n"}
    names = {"u1.text": "first", "u2.text": "second", "l1.text": "third", "l2.text": "fourth"}
    tab = win.open_flow(flow_file(tmp_path / "flows" / "named.yaml", {
        "start": {"type": "trigger.manual"},
        "u1": {"type": "local.upper", "inputs": {"text": typed("text", "ana"), "tail": typed("text", "!")}},
        "u2": {"type": "local.upper", "inputs": {"tail": typed("text", "?")}},
        "l1": {"type": "local.lower", "inputs": {"text": typed("text", "bo"), "tail": typed("text", ".")}},
        "l2": {"type": "local.lower", "inputs": {"tail": typed("text", ",")}},
        "report": {"type": "reports.html_template", "config": {"names": names, "template": "{{first}}"}},
    }, [f"{source} -> report.data" for source in names],
        {"upper": {"title": "Upper", **definition}, "lower": {"title": "Lower", **definition}}))
    settle()

    edit_names(win, tab, "u1", "Outputs", "said")

    blocks = tab.doc.data["blocks"]
    assert error_shown(win) == ""
    assert blocks["u1"]["inputs"] == {"text": typed("text", "ana"), "tail": typed("text", "!")}
    assert blocks["u2"]["inputs"] == {"tail": typed("text", "?")}
    assert blocks["report"]["config"]["names"] == {"u1.said": "first", "u2.said": "second", "l1.text": "third",
                                                   "l2.text": "fourth"}

    edit_names(win, tab, "l1", "Inputs", "words, tail")

    blocks = tab.doc.data["blocks"]
    assert error_shown(win) == ""
    assert blocks["l1"]["inputs"] == {"words": typed("text", "bo"), "tail": typed("text", ".")}
    assert blocks["l2"]["inputs"] == {"tail": typed("text", ",")}
    assert blocks["report"]["config"]["names"] == {"u1.said": "first", "u2.said": "second", "l1.text": "third",
                                                   "l2.text": "fourth"}
    clean(win)


# --- RB-6, RB-8: a pasted Local block titled as a Global block ----------------------------------------------------

@pytest.mark.invariant("RB-6")
@pytest.mark.invariant("RB-8")
def test_a_pasted_local_block_titled_as_a_global_block_is_kept_renamed_and_titled_with_its_number(
        global_shout, win, tmp_path):
    """The Global block Shout exists; a flow's Local block yell is titled Shout too. Its copy pasted into a new flow:
    the definition is kept as yell_2, titled "Shout 2", the copy uses it, and the status bar says so."""
    source = win.open_flow(flow_file(tmp_path / "flows" / "yell.yaml", {
        "start": {"type": "trigger.manual"}, "say": python("output = 'ana'"), "greet_ana": {"type": "local.yell"}},
        ["start.start -> say.after", "say.output -> greet_ana.word"], {"yell": SHOUT}))
    settle()

    copy_block(win, source, "greet_ana")
    menu(win, "New flow").trigger()
    target = win.current
    settle()
    (pasted,) = paste(win)

    assert win.statusBar().currentMessage().startswith("The pasted Local block yell is kept as yell_2")
    assert target.doc.blocks[pasted]["type"] == "local.yell_2"
    assert list(target.doc.data["local_blocks"]) == ["yell_2"]
    assert target.doc.data["local_blocks"]["yell_2"]["title"] == "Shout 2"
    clean(win)


# --- RB-2, RB-1: a block that is no copy, and Create reusable block… on a block written by hand -------------------

@pytest.mark.invariant("RB-2")
def test_a_python_code_block_beside_a_local_block_named_python_keeps_its_own_settings(win, tmp_path):
    """A flow with a Local block named python (a copy py), a Python Code block plain with its own code, and one, bare,
    written with no settings (no code yet). plain's Properties show its own code, not as a Local block's; its code
    edited there is its own, the Local block's unchanged. bare's Inputs written as "a": bare has that input, in its
    own settings."""
    tab = win.open_flow(flow_file(tmp_path / "flows" / "beside.yaml", {
        "start": {"type": "trigger.manual"}, "py": {"type": "local.python"},
        "plain": python("output = 'mine'\n", ["word"]), "bare": {"type": "logic.python"},
    }, [], {"python": SHOUT}))
    settle()

    click(tab, "plain")
    assert code_of(win).toPlainText() == "output = 'mine'\n"
    assert not [text for text in properties(win) if LOCAL_NOTE.strip(" ·") in text], properties(win)
    write_code(win, "output = 'edited'\n")
    assert tab.doc.blocks["plain"]["config"]["code"] == "output = 'edited'\n"
    assert tab.doc.data["local_blocks"] == {"python": SHOUT}
    edit_names(win, tab, "bare", "Inputs", "a")

    assert tab.doc.blocks["bare"]["config"] == {"inputs": ["a"]}, error_shown(win)
    assert ports(tab, "bare")[0] == ["after", "a"]
    clean(win)


@pytest.mark.invariant("RB-1")
@pytest.mark.invariant("RB-2")
def test_create_local_on_a_hand_written_block_with_a_version_keeps_the_definition_before_the_blocks(win, qtbot,
                                                                                                    tmp_path):
    """A flow file written by hand: a Python Code block bare with its code and a version, and an empty local_blocks:
    at the end. Create reusable block…, Local, Echo: the file, saved, holds the definition echo under local_blocks,
    before the blocks; bare is its copy, with no settings and no version of its own."""
    path = tmp_path / "flows" / "by_hand.yaml"
    path.parent.mkdir()
    path.write_text("blocks:\n  start: {type: trigger.manual}\n  bare: {type: logic.python, version: 1, config: {code: output = 1}}\n"
                    "edges:\n  - start.start -> bare.after\nlocal_blocks:\n", encoding="utf-8")
    tab = win.open_flow(path)
    settle()

    def local(dialog):
        radio = next(w for w in in_order(dialog) if isinstance(w, QRadioButton) and w.text() == "Local")
        QTest.mouseClick(radio, Qt.LeftButton, Qt.NoModifier, radio.rect().center())
        fill(dialog, "Name", "Echo")
        press(dialog, "OK")

    windows(win, lambda: right_click(qtbot, win, tab, "bare", "title", CREATE), local)

    data = saved(win, tab)
    assert list(data).index("local_blocks") < list(data).index("blocks"), list(data)
    assert list(data["local_blocks"]) == ["echo"] and data["local_blocks"]["echo"]["title"] == "Echo"
    assert {key: value for key, value in data["blocks"]["bare"].items() if key != "ui"} == {"type": "local.echo"}
    clean(win)


# --- RB-1: the Create reusable block window, and its status bar ---------------------------------------------------

@pytest.mark.invariant("RB-1")
def test_create_reusable_blocks_window_over_the_editor_shows_its_choices_and_says_why_a_name_is_refused(win, qtbot,
                                                                                                         tmp_path):
    """Right-click filter → Create reusable block…: a window over the editor, titled so, says what it makes of filter
    and asks where it is kept: Global (chosen) and Local, in bold, each with a fainter, indented line about it (Local's
    naming the flow file), then Name, What it does, What its inputs expect. Its texts wrap in it. A name whose file is
    in the blocks folder is refused, in red, in words that wrap.
    Then Local, Shout, OK, and Global, Say, OK, on say: the status bar says what each did, for a while."""
    flow, _ = source_flow(tmp_path / "flows")
    tab = win.open_flow(flow)
    settle()
    win.home.blocks_dir.mkdir(parents=True, exist_ok=True)
    (win.home.blocks_dir / "loud.py").write_text("# a helper of the blocks folder: no block in it\n")

    def look(dialog):
        assert owner(dialog) is win.windowHandle(), "the window is not over the editor"
        assert dialog.windowTitle() == CREATE
        intro, global_, about_global, local, about_local, why, _ = in_order(dialog)
        assert intro.text() == ("Makes <b>filter</b> a block of your own that runs its code on what is connected to its "
                                "inputs. Where is it kept?")
        assert [(r.text(), r.isChecked(), r.font().bold()) for r in (global_, local)] == [
            ("Global", True, True), ("Local", False, True)]
        assert [about_global.text(), about_local.text()] == [
            "Saved in the blocks tree, under User Created, for every flow (a file in your blocks folder).",
            "Only this flow, kept in the flow file (shouting.yaml); not in the blocks tree. Copy and paste the block "
            "to make more copies."]
        for radio, about in ((global_, about_global), (local, about_local)):
            assert strongest(in_place(about)) < strongest(in_place(radio)), f"{about.text()!r} is not fainter"
            assert about.contentsRect().left() >= 15, f"{about.text()!r} is not under its choice's name"
        assert row_names(dialog) == ["Name", "What it does", "What its inputs expect"]
        assert dialog.width() < 530, f"a text does not wrap: the window is {dialog.width()} pixels wide"
        fill(dialog, "Name", "Loud")
        press(dialog, "OK")
        settle()
        assert dialog.isVisible() and why.isVisible()
        assert why.text() == (f"loud.py is already in your blocks folder ({win.home.blocks_dir}): choose another "
                              "name.")
        assert dialog.width() < 530, f"the reason does not wrap: the window is {dialog.width()} pixels wide"
        assert reddest(why) > 80, "the reason is not in red"
        press(dialog, "Cancel")

    windows(win, lambda: right_click(qtbot, win, tab, "filter", "title", CREATE), look)

    def local_shout(dialog):
        radio = next(w for w in in_order(dialog) if isinstance(w, QRadioButton) and w.text() == "Local")
        QTest.mouseClick(radio, Qt.LeftButton, Qt.NoModifier, radio.rect().center())
        fill(dialog, "Name", "Shout")
        press(dialog, "OK")

    windows(win, lambda: right_click(qtbot, win, tab, "filter", "title", CREATE), local_shout)
    assert tab.doc.blocks["filter"]["type"] == "local.shout"
    assert for_a_while(win) == ("filter is the first copy of the Local block Shout, kept in this flow's file: Copy "
                                "and Paste it to make more")
    win.statusBar().clearMessage()

    windows(win, lambda: right_click(qtbot, win, tab, "say", "title", CREATE),
            lambda dialog: (fill(dialog, "Name", "Say"), press(dialog, "OK")))
    assert for_a_while(win) == (f"Say saved in {win.home.blocks_dir / 'say.py'}: it is under User Created in the "
                                "blocks tree")
    clean(win)


@pytest.mark.invariant("RB-1")
def test_create_reusable_block_on_a_block_whose_settings_cannot_be_read_says_why_over_the_window(win, qtbot, tmp_path):
    """A Python Code block whose Inputs hold something that is not a name: Create reusable block… says, in a box over
    the editor titled so, that the block cannot be saved as a block yet and why; OK, and nothing is written."""
    tab = win.open_flow(flow_file(tmp_path / "flows" / "odd.yaml", {
        "start": {"type": "trigger.manual"}, "filter": python("output = 1\n", ["first word"])}, []))
    settle()
    seen = []

    def ok(box):
        seen.append((owner(box) is win.windowHandle(), box.text(), buttons(box)))
        QTest.mouseClick(box.button(QMessageBox.Ok), Qt.LeftButton)

    with qt_boxes("warning") as asked:
        windows(win, lambda: right_click(qtbot, win, tab, "filter", "title", CREATE), ok)

    assert [a[1] for a in asked] == [CREATE]
    ((over, text, choices),) = seen
    assert over, "the box is not over the editor"
    assert text.startswith("filter cannot be saved as a block yet: ") and "first word" in text, text
    assert choices == ["OK"]
    assert block_files(win) == [] and tab.doc.blocks["filter"]["type"] == "logic.python"
    clean(win)


# --- RB-4: Promote's question, and its window for a name that is taken ---------------------------------------------

def question(win, copies: int) -> str:
    return (f"Save the Local block Shout as a global reusable block: a file in your blocks folder "
            f"({win.home.blocks_dir}), under User Created in the blocks tree, for every flow. The {copies} "
            f"{'copy' if copies == 1 else 'copies'} in this flow then use it, and the flow file no longer holds its "
            "definition.\n\nUndo cannot remove the global block's file: Undo makes them Local again, the file stays, "
            "and the flow does not run (a Local and a Global block cannot have the same name) until you Redo, or "
            "delete the global block (Delete block… in the blocks tree).")


@pytest.mark.invariant("RB-4")
def test_promote_asks_over_the_window_and_enter_answers_no_then_yes_saves_the_local_blocks_words(win, qtbot, tmp_path):
    """Promote on a copy of Shout with two copies: Qt's own question, over the editor, titled so, in these words (2
    copies), with Yes and No; Enter answers No: nothing is written. greet_bo deleted, Promote again: the words say
    1 copy; Yes: the Global block is saved with the Local block's title, what it does and what its inputs expect,
    asking no name, the copy uses it, and the status bar says so, for a while."""
    flow, _ = greetings(tmp_path / "flows", ["ana", "bo"])
    tab = win.open_flow(flow)
    settle()
    before = yaml.safe_dump(tab.doc.data)
    seen = []

    def read(box):
        seen.append((owner(box) is win.windowHandle(), box.text(), buttons(box),
                     box.defaultButton().text().replace("&", "")))

    with qt_boxes("question") as asked:
        windows(win, lambda: right_click(qtbot, win, tab, "greet_ana", "title", PROMOTE),
                lambda box: (read(box), QTest.keyClick(box, Qt.Key_Return)))

    assert [a[1] for a in asked] == [PROMOTE]
    assert seen == [(True, question(win, 2), ["No", "Yes"], "No")], seen
    assert block_files(win) == [] and yaml.safe_dump(tab.doc.data) == before

    select_only(tab, "greet_bo")
    press_delete(win, qtbot, tab)
    assert "greet_bo" not in tab.doc.blocks
    seen.clear()
    with qt_boxes("question"):
        windows(win, lambda: right_click(qtbot, win, tab, "greet_ana", "title", PROMOTE),
                lambda box: (read(box), QTest.mouseClick(box.button(QMessageBox.Yes), Qt.LeftButton)))

    assert [text for _, text, _, _ in seen] == [question(win, 1)]
    (path,) = block_files(win)
    made = discover(win.home.blocks_dir).get("user.shout")
    assert (made.title, made.description, made.inputs_help) == (SHOUT["title"], SHOUT["description"],
                                                                 SHOUT["inputs_help"])
    assert tab.doc.blocks["greet_ana"]["type"] == "user.shout"
    assert for_a_while(win) == (f"Shout saved in {path}: it is under User Created in the blocks tree, and the copies "
                                "in this flow use it")
    clean(win)


@pytest.mark.invariant("RB-4")
def test_promote_with_a_taken_name_asks_over_the_editor_filled_in_with_the_local_blocks_words(global_shout, win, qtbot,
                                                                                             tmp_path, monkeypatch):
    """The Global block Shout exists. Promote on a copy of the Local block Shout, Yes: a window over the editor,
    titled Promote to global reusable block, says the name is taken and asks for another, with no choice of where it
    is kept, its Name, What it does and What its inputs expect filled in with the Local block's. Named Loud words, OK:
    the Global block has the Local block's words, and the status bar says so, for a while."""
    flow, _ = greetings(tmp_path / "flows", ["ana", "bo"])
    tab = win.open_flow(flow)
    settle()
    answer(monkeypatch, QMessageBox.Yes)

    def look(dialog):
        assert owner(dialog) is win.windowHandle(), "the window is not over the editor"
        assert dialog.windowTitle() == PROMOTE
        assert [w.text() for w in in_order(dialog) if isinstance(w, QLabel) and w.isVisible()] == [
            "A block called <b>Shout</b> is already in the blocks tree or this flow: give the global block another "
            "name."]
        assert not [r for r in dialog.findChildren(QRadioButton) if r.isVisible()]
        assert row_names(dialog) == ["Name", "What it does", "What its inputs expect"]
        assert [text_box(dialog, "Name").text(), text_box(dialog, "What it does").toPlainText(),
                text_box(dialog, "What its inputs expect").toPlainText()] == [
            "Shout", SHOUT["description"], SHOUT["inputs_help"]]
        fill(dialog, "Name", "Loud words")
        press(dialog, "OK")

    windows(win, lambda: right_click(qtbot, win, tab, "greet_ana", "title", PROMOTE), look)

    made = discover(win.home.blocks_dir).get("user.loud_words")
    assert (made.title, made.description, made.inputs_help) == ("Loud words", SHOUT["description"],
                                                                 SHOUT["inputs_help"])
    assert for_a_while(win) == (f"Loud words saved in {win.home.blocks_dir / 'loud_words.py'}: it is under User "
                                "Created in the blocks tree, and the copies in this flow use it")
    assert sorted(user_created(win)) == ["Loud words", "Shout"]
    clean(win)


# --- RB-8, RB-4: names refused, in words; Promote skips only its own Local block -----------------------------------

@pytest.mark.invariant("RB-8")
@pytest.mark.invariant("RB-4")
def test_names_refused_say_why_and_promote_skips_only_its_own_local_block(win, qtbot, tmp_path, monkeypatch):
    """Create reusable block… on filter, in a flow whose Local block loud has a problem: the Name !!! is refused as
    having no letter or digit, the Name Loud as a Local block's this flow already has. In another flow, the Local block
    yell is titled Shout and, after it in the file, the Local block shout is titled Other: Promote on yell's copy, Yes,
    asks for another name, its name being shout's."""
    tab = win.open_flow(flow_file(tmp_path / "flows" / "broken.yaml", {
        "start": {"type": "trigger.manual"}, "filter": python("output = 1\n"), "noisy": {"type": "local.loud"}},
        [], {"loud": {**SHOUT, "inputs": ["first word"]}}))
    settle()
    reasons = []

    def try_names(dialog):
        for name in ("!!!", "Loud"):
            fill(dialog, "Name", name)
            press(dialog, "OK")
            settle()
            reasons.append(dialog.why.text() if dialog.isVisible() and dialog.why.isVisible() else None)
        press(dialog, "Cancel")

    windows(win, lambda: right_click(qtbot, win, tab, "filter", "title", CREATE), try_names)

    assert reasons == ["Give it a name, with letters or digits.",
                       "This flow already has a Local block loud: choose another name."]
    other = win.open_flow(flow_file(tmp_path / "flows" / "two.yaml", {
        "start": {"type": "trigger.manual"}, "a": {"type": "local.yell"}, "b": {"type": "local.shout"}},
        [], {"yell": SHOUT, "shout": {**SHOUT, "title": "Other"}}))
    settle()
    answer(monkeypatch, QMessageBox.Yes)
    asked_for = []

    windows(win, lambda: right_click(qtbot, win, other, "a", "title", PROMOTE),
            lambda dialog: (asked_for.append(text_box(dialog, "Name").text()), press(dialog, "Cancel")))

    assert asked_for == ["Shout"]
    assert block_files(win) == [] and other.doc.blocks["a"]["type"] == "local.yell"
    clean(win)
