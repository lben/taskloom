"""Deleting a User Created block from the Blocks list: right-click it (or select it and press Delete) → "Delete
block…"; built-in blocks and the group rows have no such item. It asks first, in plain words (the block, its file,
the other blocks that file defines, the open flows that use it, which then show it as an unknown block), with Cancel
as the default button. On yes the file is deleted and the block is gone at once from the Blocks list, Help >
Documentation and the hover help; the open flows that use it show it as an unknown block, nothing else changed. A
file that cannot be deleted is said so, and nothing changes. With no User Created block left, the User Created group
is gone (and comes back when a block is created again)."""

import html
import os
import re
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QLabel, QListWidget, QMessageBox,  # noqa: E402
                               QPlainTextEdit, QPushButton, QTextEdit)

from taskloom.config import Home  # noqa: E402
from taskloom.editor import docs  # noqa: E402
from taskloom.editor.window import MainWindow  # noqa: E402
from tests.test_paths_editor_editing import GREET_BLOCK, clean, flow_file, select_only, settle  # noqa: E402
from tests.test_reusable_block import block_files, groups, make_reusable, names_flow, user_created  # noqa: E402

README = Path(__file__).resolve().parents[1] / "README.md"
DELETE = "Delete block"  # the menu item, followed by an ellipsis: it asks first
GREET_SAYS = "Says hello to whoever you name."  # GREET_BLOCK's description (its hover help)
SHOUT_SAYS = "Says the text it is given, in capitals."
SHOUT_BLOCK = f'''
from taskloom import Block, ports

class Shout(Block):
    type_id = "my.shout"
    title = "Shout"
    description = {SHOUT_SAYS!r}
    inputs = {{"text": ports.Any()}}
    outputs = {{"text": ports.Any()}}

    def run(self, ctx, text):
        return {{"text": str(text).upper()}}
'''
FAREWELL_TOO = GREET_BLOCK + '''

class Farewell(Block):
    type_id = "my.farewell"
    title = "Farewell"
    description = "Says goodbye to whoever you name."
    inputs = {"name": ports.Any()}
    outputs = {"text": ports.Any()}

    def run(self, ctx, name):
        return {"text": f"Goodbye, {name}!"}
'''  # one file, two blocks


# --- helpers ---------------------------------------------------------------------------------------------------

def editor_with(qtbot, win, files: dict):
    """A new editor on the test's Taskloom folder, started with these files in its blocks folder (it finds your
    blocks when it starts)."""
    folder = win.home.blocks_dir
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    mine = MainWindow(Home(win.home.root))
    qtbot.addWidget(mine)
    mine.show()
    qtbot.waitExposed(mine)
    mine.activateWindow()
    settle()
    return mine


def greet_flows(tmp_path) -> list[Path]:
    """Three saved flows: two use Greet (as say_hello, as greet_team), one uses Shout and not Greet."""
    return [
        flow_file(tmp_path / "nightly_report.yaml", {
            "start": {"type": "trigger.manual", "ui": {"x": -300, "y": 0}},
            "say_hello": {"type": "my.greet", "config": {"greeting": "Bonjour"}, "ui": {"x": 0, "y": 0}},
            "keep": {"type": "logic.python", "config": {"code": "print(input)"}, "ui": {"x": 300, "y": 0}},
        }, ["start.start -> say_hello.after", "say_hello.text -> keep.input"]),
        flow_file(tmp_path / "morning_check.yaml", {
            "start": {"type": "trigger.manual", "ui": {"x": -300, "y": 0}},
            "greet_team": {"type": "my.greet", "ui": {"x": 0, "y": 0}},
        }, ["start.start -> greet_team.after"]),
        flow_file(tmp_path / "manual_load.yaml", {
            "start": {"type": "trigger.manual", "ui": {"x": -300, "y": 0}},
            "shout_out": {"type": "my.shout", "ui": {"x": 0, "y": 0}},
        }, ["start.start -> shout_out.after"]),
    ]


def canvas(tab) -> dict:
    """What the canvas shows of the flow: each block (known or not, its type's title, its hover help, its ports, its
    icon and colour), the lines drawn, the blocks connected to no start block."""
    scene = tab.scene
    return {"blocks": {b: (n.known, n.type_title, n.toolTip(), list(n.inputs), list(n.outputs), n.icon,
                           n.color.name()) for b, n in scene.nodes.items()},
            "lines": sorted(e.text for e in scene.edge_items), "off": sorted(scene.off)}


def as_opened_now(qtbot, win, flows) -> list[dict]:
    """The canvases of these flows in an editor started now, on the same Taskloom folder (a block type whose file is
    missing shows as an unknown block)."""
    fresh = MainWindow(Home(win.home.root))
    qtbot.addWidget(fresh)
    fresh.show()
    tabs = [fresh.open_flow(flow) for flow in flows]
    settle()
    shown = [canvas(tab) for tab in tabs]
    clean(fresh)
    return shown


def flow_state(tabs, flows) -> list:
    """Each open flow as the editor holds it (its data, unsaved or not) and as it is on disk."""
    return [(yaml.safe_dump(tab.doc.data), tab.doc.dirty, flow.read_bytes()) for tab, flow in zip(tabs, flows)]


def block_row(window, title: str):
    tree = window.palette_panel.tree
    found = tree.findItems(title, Qt.MatchExactly | Qt.MatchRecursive)
    assert found, f"no {title!r} in the Blocks list: {groups(window)}"
    return found[0]


def group_row(window, name: str):
    tree = window.palette_panel.tree
    found = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
             if tree.topLevelItem(i).text(0).startswith(name)]
    assert found, f"no group {name!r} in the Blocks list: {groups(window)}"
    return found[0]


def tooltips(window) -> list[str]:
    """The hover help of every row of the Blocks list."""
    tree = window.palette_panel.tree
    rows = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    rows += [r.child(j) for r in list(rows) for j in range(r.childCount())]
    return [r.toolTip(0) for r in rows]


def row_point(window, item) -> QPoint:
    """Where the row is in the list's viewport, scrolled into view (the list can be narrow)."""
    tree = window.palette_panel.tree
    QApplication.processEvents()
    tree.scrollToItem(item)
    QApplication.processEvents()
    rect, view = tree.visualItemRect(item), tree.viewport().rect()
    at = QPoint((max(rect.left(), 0) + min(rect.right(), view.right())) // 2, rect.center().y())
    assert view.contains(at) and tree.itemAt(at) is item, (item.text(0), rect, view)
    return at


def right_click_row(qtbot, window, item, choose: str = "", tries: int = 2, wait: float = 5.0) -> list[str]:
    """Right-click the row in the Blocks list through the window, as the mouse does: the items of the menu that opens
    ([]: none opened); the one starting with `choose` is clicked (otherwise the menu is closed)."""
    tree = window.palette_panel.tree
    at = tree.viewport().mapTo(window, row_point(window, item))
    seen, done = [], []

    def act(deadline):  # (a menu waits for you inside the right-click)
        menu = QApplication.activePopupWidget()
        if menu is None and time.monotonic() < deadline:
            QTimer.singleShot(20, window, lambda: act(deadline))
            return
        if menu is not None:
            seen.extend(a.text() for a in menu.actions() if a.text())
            chosen = next((a for a in menu.actions() if choose and a.text().startswith(choose) and a.isEnabled()),
                          None)
            if chosen is not None:
                QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(chosen).center())
            if menu.isVisible():
                menu.close()
        done.append(1)

    for _ in range(tries):  # (once more if no menu opened: a click can be lost while the window is still settling)
        done.clear()
        QTimer.singleShot(0, window, lambda: act(time.monotonic() + wait))
        QCursor.setPos(window.mapToGlobal(at))  # (the pointer is on the row, as with a mouse: Qt's menu goes there)
        QTest.mouseClick(window.windowHandle(), Qt.RightButton, Qt.NoModifier, at)
        qtbot.waitUntil(lambda: bool(done), timeout=int(wait * 1000) + 30_000)
        if seen:
            break
    settle()
    return seen


def press_delete_on(qtbot, window, item):
    """Click the row in the Blocks list, then press the Delete key there (in the window you work in: after a
    question closes, the offscreen platform does not always give the window back its focus)."""
    tree = window.palette_panel.tree
    window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window, timeout=5_000)
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, row_point(window, item))
    tree.setFocus()
    settle()
    QTest.keyClick(tree, Qt.Key_Delete)
    settle()


def plain(text: str) -> str:
    """Text as it reads: no HTML tags or entities, no & of a button's shortcut letter, spaces as one."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).replace("&", "").split())


class Asked(list):
    """Every question or message shown while answering(): {"kind", "text", "default"} (the default button's text)."""
    answer = "cancel"


YES_ROLES = (QMessageBox.YesRole, QMessageBox.AcceptRole, QMessageBox.DestructiveRole)
NO_ROLES = (QMessageBox.RejectRole, QMessageBox.NoRole)


def _static_box(asked, kind, *a, **k):
    """QMessageBox.question / warning / information / critical: noted, answered."""
    buttons = k.get("buttons", a[3] if len(a) > 3 else None)
    default = k.get("defaultButton", a[4] if len(a) > 4 else None)
    asked.append({"kind": kind, "text": plain(f"{a[1]}\n{a[2]}"),
                  "default": "Cancel" if default == QMessageBox.Cancel else
                  None if default in (None, QMessageBox.NoButton) else str(default)})
    if kind != "question":
        return QMessageBox.Ok
    offered = buttons if buttons is not None else QMessageBox.Yes | QMessageBox.No
    order = ((QMessageBox.Yes, QMessageBox.Ok, QMessageBox.Discard, QMessageBox.Apply) if asked.answer == "yes"
             else (QMessageBox.Cancel, QMessageBox.No))
    return next((b for b in order if offered & b), QMessageBox.Cancel)


def _dialog_box(asked, dialog):
    """A dialog waiting for a click (a QMessageBox of its own or another window): noted, answered."""
    if isinstance(dialog, QMessageBox):
        words = [dialog.windowTitle(), dialog.text(), dialog.informativeText(), dialog.detailedText()]
        default = dialog.defaultButton() or next(
            (b for b in dialog.buttons() if isinstance(b, QPushButton) and b.isDefault()), None)
        yes = [b for b in dialog.buttons() if dialog.buttonRole(b) in YES_ROLES]
        no = [b for b in dialog.buttons() if dialog.buttonRole(b) in NO_ROLES]
    else:
        words = [dialog.windowTitle()] + [w.text() for w in dialog.findChildren(QLabel) if w.isVisible()]
        words += [w.toPlainText() for kind in (QPlainTextEdit, QTextEdit) for w in dialog.findChildren(kind)
                  if w.isVisible()]
        words += [w.item(i).text() for w in dialog.findChildren(QListWidget) for i in range(w.count())]
        buttons = [b for b in dialog.findChildren(QPushButton) if b.isVisible()]
        default = next((b for b in buttons if b.isDefault()), None)
        yes = [b for b in buttons if plain(b.text()).lower().startswith(("delete", "yes", "ok"))]
        no = [b for b in buttons if plain(b.text()).lower() in ("cancel", "no")]
    asked.append({"kind": "dialog", "text": plain("\n".join(words)),
                  "default": plain(default.text()) if default is not None else None})
    chosen = yes if asked.answer == "yes" else no
    if chosen:
        chosen[0].click()
    if dialog.isVisible():
        dialog.reject()


@contextmanager
def answering(window, answer: str):
    """While in it, every question the editor asks is noted and answered `answer` ("yes" or "cancel"), and every
    message is noted and closed: those of QMessageBox's own functions and any window that waits for a click."""
    asked, watching = Asked(), [True]
    asked.answer = answer

    def watch():
        if not watching:
            return
        for dialog in QApplication.topLevelWidgets():
            if isinstance(dialog, QDialog) and dialog.isVisible():
                _dialog_box(asked, dialog)
        QTimer.singleShot(20, window, watch)

    with pytest.MonkeyPatch.context() as patch:
        for kind in ("question", "warning", "information", "critical"):
            patch.setattr(QMessageBox, kind, staticmethod(lambda *a, _kind=kind, **k: _static_box(asked, _kind, *a,
                                                                                                    **k)))
        QTimer.singleShot(0, window, watch)
        try:
            yield asked
        finally:
            watching.clear()


def wait_for(qtbot, asked: Asked, count: int, what: str):
    """Until `count` questions or messages were shown (up to 10 s), then let what follows them run."""
    deadline = time.monotonic() + 10
    while len(asked) < count and time.monotonic() < deadline:
        qtbot.wait(20)
    assert len(asked) >= count, f"{what}: {list(asked)}"
    settle()


def names_path(text: str, path: Path) -> bool:
    shown = {str(path), str(path.resolve()), path.as_posix(), path.resolve().as_posix()}
    return any(p in text for p in shown)


def delete_with_right_click(qtbot, window, title: str, answer: str, shown: int = 1) -> Asked:
    """Right-click the block in the Blocks list → Delete block…, answer its question `answer`; what was shown (at
    least `shown` questions or messages)."""
    with answering(window, answer) as asked:
        menu = right_click_row(qtbot, window, block_row(window, title), choose=DELETE)
        assert any(t.startswith(DELETE) for t in menu), f"no {DELETE}… in the menu of {title}: {menu}"
        wait_for(qtbot, asked, shown, f"{DELETE}… showed too little")  # (it may ask just after the menu closes)
    return asked


# --- the user's case: right-click → Delete block…, yes ---------------------------------------------------------

def test_delete_block_asks_naming_the_file_and_the_flows_then_deletes_it_and_it_is_gone_everywhere_at_once(
        win, qtbot, tmp_path):
    """Spec 1, 2, 3: right-click a User Created block → Delete block… asks first, naming the block, the file to be
    deleted and the open flows that use it (by flow and block name), saying they will show it as an unknown block,
    with Cancel as the default button. On yes the file is deleted; the block is gone at once (no restart) from the
    Blocks list, Help > Documentation (the page on screen too) and the hover help; the open flows that use it show
    it as an unknown block, exactly as a flow opened now with its file missing does, nothing else changed in them;
    the other User Created block stays."""
    mine = editor_with(qtbot, win, {"greet.py": GREET_BLOCK, "shout.py": SHOUT_BLOCK})
    greet, shout = mine.home.blocks_dir / "greet.py", mine.home.blocks_dir / "shout.py"
    flows = greet_flows(tmp_path)
    tabs = [mine.open_flow(flow) for flow in flows]
    settle()
    before = flow_state(tabs, flows)
    shout_text = shout.read_bytes()
    assert user_created(mine) == ["Greet", "Shout"], groups(mine)
    say_hello = tabs[0].scene.nodes["say_hello"]
    assert say_hello.known and GREET_SAYS in say_hello.toolTip()  # its hover help on the canvas, before
    greet_page = f"{docs.category_page(mine.registry.get('my.greet').category)}#my.greet"
    mine.docs.open_at(greet_page)  # Help > Documentation showing the block's entry
    settle()
    assert GREET_SAYS in mine.docs.browser.toPlainText()

    asked = delete_with_right_click(qtbot, mine, "Greet", "yes")

    question = asked[0]
    text = question["text"]
    assert "Greet" in text, text
    assert names_path(text, greet), (greet, text)  # the file that will be deleted
    for flow, block in (("nightly_report", "say_hello"), ("morning_check", "greet_team")):
        assert flow in text and block in text, (flow, block, text)
    assert "manual_load" not in text, text  # it does not use Greet
    assert "unknown" in text.lower(), text  # they will show as unknown blocks
    assert question["default"] == "Cancel", question

    assert not greet.exists()
    assert shout.read_bytes() == shout_text
    assert user_created(mine) == ["Shout"], groups(mine)  # gone from the Blocks list, at once
    assert not any(GREET_SAYS in tip for tip in tooltips(mine))
    assert GREET_SAYS not in mine.docs.browser.toPlainText()  # the page on screen, at once
    assert SHOUT_SAYS in mine.docs.browser.toPlainText()  # (made again: Shout's entry is on that page)
    mine.docs.open_at("index.html")
    settle()
    contents = mine.docs.browser.toPlainText()
    assert "Greet" not in contents and "Shout" in contents, contents
    assert flow_state(tabs, flows) == before  # nothing else changed in the open flows
    assert [canvas(tab) for tab in tabs] == as_opened_now(qtbot, win, flows)  # Greet's blocks: unknown
    assert not tabs[0].scene.nodes["say_hello"].known and not tabs[1].scene.nodes["greet_team"].known
    assert tabs[2].scene.nodes["shout_out"].known
    clean(mine)


# --- only User Created blocks ------------------------------------------------------------------------------------

def test_delete_block_is_offered_for_user_created_blocks_only_and_the_delete_key_does_nothing_elsewhere(
        win, qtbot, tmp_path):
    """Spec 1: a User Created block's right-click menu has Delete block…; a built-in block's (Wait, the Notes'
    Text) and the group rows' have none, and the Delete key on those rows does nothing: no question, no file
    deleted, the Blocks list and the open flow (with a block selected on the canvas) as they were."""
    mine = editor_with(qtbot, win, {"greet.py": GREET_BLOCK})
    greet = mine.home.blocks_dir / "greet.py"
    (flow, *_) = greet_flows(tmp_path)
    tab = mine.open_flow(flow)
    settle()
    select_only(tab, "keep")
    before, listed = flow_state([tab], [flow]), groups(mine)

    menu = right_click_row(qtbot, mine, block_row(mine, "Greet"))
    assert any(t.startswith(DELETE) for t in menu), f"no {DELETE}… for a User Created block: {menu}"

    others = [("Wait", block_row(mine, "Wait")), ("Text", block_row(mine, "Text")),
              ("Logic", group_row(mine, "Logic")), ("User Created", group_row(mine, "User Created"))]
    for name, item in others:  # (right-clicks work here: the one above opened its menu)
        menu = right_click_row(qtbot, mine, item, tries=1, wait=1.5)
        assert not any(t.startswith(DELETE) for t in menu), (name, menu)
    with answering(mine, "yes") as asked:
        for name, item in others:
            press_delete_on(qtbot, mine, item)
            qtbot.wait(300)
            assert list(asked) == [], (name, asked)
    assert greet.exists()
    assert groups(mine) == listed and user_created(mine) == ["Greet"]
    assert flow_state([tab], [flow]) == before and set(tab.scene.nodes) == {"start", "say_hello", "keep"}
    clean(mine)


# --- the Delete key, and Cancel ----------------------------------------------------------------------------------

def test_the_delete_key_on_a_user_created_block_asks_the_same_cancel_keeps_everything_and_yes_deletes(
        win, qtbot, tmp_path):
    """Spec 1, 2: the Delete key with a User Created block's row selected asks the same question (Cancel the default
    button). Cancel deletes nothing: the file, the Blocks list, the documentation and the open flow are as they were.
    Asked again and answered yes, the file is deleted and the block is gone from the list."""
    mine = editor_with(qtbot, win, {"greet.py": GREET_BLOCK, "shout.py": SHOUT_BLOCK})
    greet = mine.home.blocks_dir / "greet.py"
    greet_text = greet.read_bytes()
    (flow, *_) = greet_flows(tmp_path)
    tab = mine.open_flow(flow)
    settle()
    before, shown = flow_state([tab], [flow]), canvas(tab)

    with answering(mine, "cancel") as asked:
        press_delete_on(qtbot, mine, block_row(mine, "Greet"))
        assert mine.palette_panel.tree.currentItem() is block_row(mine, "Greet")  # (the row it was pressed on)
        wait_for(qtbot, asked, 1, "the Delete key on a User Created block asked nothing")
    assert "Greet" in asked[0]["text"] and names_path(asked[0]["text"], greet), asked
    assert asked[0]["default"] == "Cancel", asked

    assert greet.read_bytes() == greet_text
    assert user_created(mine) == ["Greet", "Shout"]
    assert flow_state([tab], [flow]) == before and canvas(tab) == shown
    mine.docs.open_at("index.html")
    settle()
    assert "Greet" in mine.docs.browser.toPlainText()

    with answering(mine, "yes") as asked:
        press_delete_on(qtbot, mine, block_row(mine, "Greet"))
        wait_for(qtbot, asked, 1, "the Delete key on a User Created block asked nothing")
    assert not greet.exists()
    assert user_created(mine) == ["Shout"]
    assert not tab.scene.nodes["say_hello"].known
    clean(mine)


# --- a file with several blocks ----------------------------------------------------------------------------------

def test_a_file_that_defines_other_blocks_too_is_named_with_them_and_they_go_with_it(win, qtbot, tmp_path):
    """Spec 2, 3, 4: the file of the block also defines Farewell: the question names it too; on yes the file is
    deleted, both blocks are gone from the Blocks list and, none being left, so is the User Created group."""
    mine = editor_with(qtbot, win, {"greetings.py": FAREWELL_TOO})
    path = mine.home.blocks_dir / "greetings.py"
    assert user_created(mine) == ["Farewell", "Greet"]

    asked = delete_with_right_click(qtbot, mine, "Greet", "yes")

    text = asked[0]["text"]
    assert "Greet" in text and "Farewell" in text and names_path(text, path), text
    assert not path.exists()
    assert mine.palette_panel.tree.findItems("Farewell", Qt.MatchExactly | Qt.MatchRecursive) == []
    assert user_created(mine) is None, groups(mine)
    clean(mine)


# --- a file that cannot be deleted -------------------------------------------------------------------------------

def undeletable(path: Path):
    """Make the file impossible to delete (Windows: read-only; elsewhere: in a folder that cannot be written to);
    returns what undoes it."""
    if sys.platform == "win32":
        path.chmod(stat.S_IREAD)
        return lambda: path.chmod(stat.S_IREAD | stat.S_IWRITE)
    mode = path.parent.stat().st_mode
    path.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return lambda: path.parent.chmod(mode)


@pytest.mark.skipif(sys.platform != "win32" and os.geteuid() == 0, reason="root can delete any file")
def test_a_file_that_cannot_be_deleted_is_said_so_and_nothing_changes(win, qtbot, tmp_path):
    """Spec 3: answered yes, but the file cannot be deleted: a message says why (the system's reason: access or
    permission denied), and nothing changes: the file, the Blocks list, the documentation, the open flow and its
    block's hover help are as they were."""
    mine = editor_with(qtbot, win, {"greet.py": GREET_BLOCK})
    greet = mine.home.blocks_dir / "greet.py"
    greet_text = greet.read_bytes()
    (flow, *_) = greet_flows(tmp_path)
    tab = mine.open_flow(flow)
    settle()
    before, shown = flow_state([tab], [flow]), canvas(tab)
    undo = undeletable(greet)
    try:
        asked = delete_with_right_click(qtbot, mine, "Greet", "yes", shown=2)  # the question, then why not deleted
    finally:
        undo()

    assert "denied" in asked[1]["text"].lower(), asked
    assert greet.read_bytes() == greet_text
    assert user_created(mine) == ["Greet"]
    assert any(GREET_SAYS in tip for tip in tooltips(mine))
    assert flow_state([tab], [flow]) == before and canvas(tab) == shown
    assert GREET_SAYS in tab.scene.nodes["say_hello"].toolTip()
    mine.docs.open_at("index.html")
    settle()
    assert "Greet" in mine.docs.browser.toPlainText()
    clean(mine)


# --- the User Created group --------------------------------------------------------------------------------------

def test_the_user_created_group_goes_with_the_last_block_and_comes_back_when_one_is_created_again(win, qtbot,
                                                                                                tmp_path):
    """Spec 4: a reusable block made with Create Reusable block… and then deleted: no User Created block is left, so
    the User Created group is gone from the Blocks list; made again (the same name: it is free again), the group
    comes back with it."""
    tab = win.open_flow(names_flow(tmp_path, tmp_path / "out.json"))
    settle()
    make_reusable(win, qtbot, tab, "tidy")
    (path,) = block_files(win)
    assert user_created(win) == ["Tidy names"]

    asked = delete_with_right_click(qtbot, win, "Tidy names", "yes")

    assert "Tidy names" in asked[0]["text"] and names_path(asked[0]["text"], path), asked
    assert block_files(win) == []
    assert user_created(win) is None, groups(win)
    assert not any(g.startswith("User Created") for g in groups(win)), groups(win)

    make_reusable(win, qtbot, tab, "tidy")
    assert block_files(win) == [path]
    assert user_created(win) == ["Tidy names"], groups(win)
    clean(win)


# --- README ------------------------------------------------------------------------------------------------------

def test_readme_says_how_to_delete_a_user_created_block():
    """Spec 5: where README describes User Created (reusable) blocks, it says how to delete one: Delete block…"""
    text = README.read_text(encoding="utf-8")
    passages = [p for p in re.split(r"\n\s*\n|\n(?=\s*[-*|] )", text)
                if any(words in p for words in ("User Created", "Reusable", "of your own"))]
    said = " ".join(" ".join(passages).replace("*", "").split()).lower()
    assert passages and "delete block" in said, passages
