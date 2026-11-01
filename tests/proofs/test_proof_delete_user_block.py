"""Proof: a block of your own (User Created) can be deleted from the Blocks list; built-in blocks cannot. The User
Created group goes with the last one, and comes back with the next."""

import sys
import textwrap
import time

import pytest
import yaml
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent, QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialogButtonBox, QFileDialog, QFormLayout, QLabel, QMessageBox,
                               QToolButton)

from proofs.conftest import choose, menu_item, right_click
from taskloom.config import Home
from taskloom.editor.canvas import HEADER_H, NODE_W
from taskloom.editor.window import MainWindow
from tests.test_delete_user_block import undeletable
from tests.test_docs_help import click_link

REAL_QUESTION = QMessageBox.question  # (the win fixture answers questions itself: here you see them)
REAL_WARNING = QMessageBox.warning
DELETE = "Delete block…"
ROUND_SAYS = "Rounds each amount of a list to the number of decimals you choose."
REGION_SAYS = "Gives the full name of each region code: N is North, S is South."
BLOCKS = {  # two blocks of your own, each a Python file in the blocks folder of your Taskloom folder
    "round_amounts.py": f'''
        from taskloom import Block, fields, ports

        class RoundAmounts(Block):
            type_id = "my.round_amounts"
            title = "Round amounts"
            category = "User Created"
            description = {ROUND_SAYS!r}
            inputs = {{"amounts": ports.Any()}}
            outputs = {{"amounts": ports.Any()}}
            config = {{"decimals": fields.Int(default=0, minimum=0, help="How many decimals to keep",
                                              examples=["0", "2"])}}

            def run(self, ctx, amounts):
                return {{"amounts": [round(a, self.config.decimals) for a in amounts]}}
        ''',
    "region_names.py": f'''
        from taskloom import Block, ports

        NAMES = {{"N": "North", "S": "South", "E": "East", "W": "West"}}

        class RegionNames(Block):
            type_id = "my.region_names"
            title = "Region names"
            category = "User Created"
            description = {REGION_SAYS!r}
            inputs = {{"codes": ports.Any()}}
            outputs = {{"names": ports.Any()}}

            def run(self, ctx, codes):
                return {{"names": [NAMES.get(c, c) for c in codes]}}
        ''',
}
FLOW = """
    blocks:
      start_by_hand:
        type: trigger.manual
        ui: {x: 0, y: 0}
      load_sales:
        type: logic.python
        config: {inputs: [], outputs: [amounts], code: "amounts = [120.4, 80.6, 45.25]"}
        ui: {x: 260, y: 0}
      round_sales:
        type: my.round_amounts
        config: {decimals: 1}
        ui: {x: 520, y: 0}
    edges:
      - start_by_hand.start -> load_sales.after
      - load_sales.amounts -> round_sales.amounts
"""
NEW_NAME, NEW_FILE = "Daily sales", "daily_sales.py"  # made again, from load_sales, with Create Reusable block…
NEW_SAYS = "Gives the day's sales amounts."


@pytest.fixture
def own_blocks(tmp_path):
    """The two block files, in the blocks folder of the Taskloom folder the editor below starts on."""
    folder = tmp_path / "home" / "blocks"
    folder.mkdir(parents=True)
    for name, text in BLOCKS.items():
        (folder / name).write_text(textwrap.dedent(text), encoding="utf-8")
    return folder


@pytest.fixture
def win(own_blocks, win):
    """The editor, started once your blocks are in its blocks folder: it finds them when it starts."""
    return win


# --- helpers ---------------------------------------------------------------------------------------------------

def open_flow(win, monkeypatch, path):
    """File > Open…, choosing `path` in the file dialog: the tab it opens in."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    menu_item(file_menu, "Open…").trigger()
    QApplication.processEvents()
    return win.current


def toggle_documentation(win):
    """Help > Documentation (it opens the Documentation column, or closes it)."""
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    menu_item(help_menu, "Documentation").trigger()
    QApplication.processEvents()


def group(win, name: str):
    """The Blocks list's group row called `name` (e.g. "User Created  (2)"); None if there is none."""
    tree = win.palette_panel.tree
    return next((tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                 if tree.topLevelItem(i).text(0).startswith(name)), None)


def in_group(win, name: str):
    """The titles of the blocks listed in that group (None: there is no such group)."""
    folder = group(win, name)
    return None if folder is None else [folder.child(i).text(0) for i in range(folder.childCount())]


def groups(win) -> list[str]:
    tree = win.palette_panel.tree
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def block_row(win, title: str):
    tree = win.palette_panel.tree
    found = tree.findItems(title, Qt.MatchExactly | Qt.MatchRecursive)
    assert found, f"no {title!r} in the Blocks list: {groups(win)}"
    return found[0]


def rows(win, first, last) -> QRect:
    """The Blocks list scrolled to show the rows from `first` to `last`, as you would: where they are in a
    screenshot of the window."""
    tree = win.palette_panel.tree
    tree.scrollToItem(last)
    tree.scrollToItem(first)
    QApplication.processEvents()
    area = tree.visualItemRect(first) | tree.visualItemRect(last)
    return area.translated(tree.viewport().mapTo(win, QPoint(0, 0)))


def whole_group(win, name: str) -> QRect:
    """A group's row and its blocks' rows, scrolled into view."""
    folder = group(win, name)
    return rows(win, folder, folder.child(folder.childCount() - 1))


def right_click_row(win, item):
    """Right-click the row in the Blocks list, just right of its name (a menu that opens waits for a choice: see
    Proof.while_open)."""
    tree = win.palette_panel.tree
    viewport = tree.viewport()
    tree.scrollToItem(item)
    QApplication.processEvents()
    rect = tree.visualItemRect(item)
    at = QPoint(min(rect.right(), viewport.width()) - 30, rect.center().y())
    assert tree.itemAt(at) is item, item.text(0)
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, viewport.mapToGlobal(at)))


def opens_a_menu(open_) -> bool:
    """Whether open_() opens a menu (closed at once if it does: nothing is left waiting for it)."""
    opened = []

    def look():
        menu = QApplication.activePopupWidget()
        if menu is not None:
            opened.append(menu)
            menu.close()

    timer = QTimer(interval=20)
    timer.timeout.connect(look)
    timer.start()
    try:
        open_()
        QTest.qWait(300)
    finally:
        timer.stop()
    return bool(opened)


def boxes_in_turn(open_, *looks):
    """open_() shows message boxes one after the other, each waiting for a click; looks[i](box) is called on the
    i-th once it shows, to shoot it and answer it (closed after, if it is still open)."""
    seen, errors = [], []

    def look():
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox) or box.property("proof_seen") or len(seen) == len(looks):
            return
        box.setProperty("proof_seen", True)  # (looked at once: a screenshot lets the clock tick here again)
        seen.append(box.text())
        try:
            looks[len(seen) - 1](box)
        except BaseException as e:  # raised again below
            errors.append(e)
        if box.isVisible():
            box.close()

    timer = QTimer(interval=50)
    timer.timeout.connect(look)
    timer.start()
    try:
        open_()
        deadline = time.monotonic() + 10
        while len(seen) < len(looks) and time.monotonic() < deadline:
            QTest.qWait(50)
    finally:
        timer.stop()
    if errors:
        raise errors[0]
    assert len(seen) == len(looks), f"{len(looks)} message boxes expected, {len(seen)} shown: {seen}"


def press_delete(qtbot, win, item):
    """Click the row in the Blocks list, then press the Delete key (a question that opens waits for an answer)."""
    tree = win.palette_panel.tree
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=5_000)
    tree.scrollToItem(item)
    QApplication.processEvents()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, tree.visualItemRect(item).center())
    tree.setFocus()
    QApplication.processEvents()
    assert tree.currentItem() is item, item.text(0)
    QTest.keyClick(tree, Qt.Key_Delete)
    QApplication.processEvents()


def title_bar(tab, block: str) -> QPoint:
    return tab.view.mapFromScene(tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2)))


def click_block(tab, block: str):
    """Click a block's title bar, as you would."""
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, title_bar(tab, block))
    QApplication.processEvents()


def tooltip():
    return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)


def hover_block(qtbot, tab, block: str):
    """Rest the mouse on a block's title bar: its hover help, once it shows (None: none shows)."""
    viewport, at = tab.view.viewport(), title_bar(tab, block)
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, at, viewport.mapToGlobal(at)))
    try:
        qtbot.waitUntil(lambda: tooltip() is not None, timeout=1_500)
    except qtbot.TimeoutError:
        return None
    return tooltip()


def label(widget, text: str) -> QLabel:
    """The label of `widget` (a window, a panel) whose text is or has `text`."""
    return next(w for w in widget.findChildren(QLabel) if w.isVisible() and (w.text() == text or text in w.text()))


def setting_row(win, name: str) -> list:
    """The row of Properties for the setting `name`: its name and its box."""
    for form in win.properties.widget().findChildren(QFormLayout):
        for row in range(form.rowCount()):
            name_item, box_item = form.itemAt(row, QFormLayout.LabelRole), form.itemAt(row, QFormLayout.FieldRole)
            if name_item and box_item and isinstance(name_item.widget(), QLabel) and name_item.widget().text() == name:
                return [name_item.widget(), box_item.widget()]
    raise AssertionError(f"no setting {name!r} in Properties")


def texts(widget) -> list[str]:
    """What the labels of `widget` show."""
    return [w.text() for w in widget.findChildren(QLabel) if w.isVisible()]


def names(path, text: str) -> bool:
    """`text` names the file `path` (as it is written, or as its real path)."""
    return str(path) in text or str(path.resolve()) in text


def flow_state(tab, path) -> tuple:
    """The flow as the editor holds it (its blocks, settings and lines; unsaved changes or not) and on disk."""
    return yaml.safe_dump(tab.doc.data), tab.doc.dirty, path.read_bytes()


def canvas(tab) -> dict:
    """What the canvas shows: each block (known or not, the title at its foot, its hover help, ports, icon), the
    lines drawn."""
    return {"blocks": {b: (n.known, n.type_title, n.toolTip(), list(n.inputs), list(n.outputs), n.icon)
                       for b, n in tab.scene.nodes.items()},
            "lines": sorted(e.text for e in tab.scene.edge_items)}


def as_opened_now(qtbot, win, path) -> dict:
    """What the canvas of the flow shows in an editor started now on the same Taskloom folder (then closed)."""
    fresh = MainWindow(Home(win.home.root))
    qtbot.addWidget(fresh)
    fresh.show()
    shown = canvas(fresh.open_flow(path))
    fresh.close()
    win.activateWindow()
    QApplication.processEvents()
    return shown


def docs_page(win) -> str:
    return win.docs.browser.toPlainText()


def tooltips(win) -> list[str]:
    """The hover help of every row of the Blocks list."""
    tree = win.palette_panel.tree
    folders = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    return [r.toolTip(0) for r in folders + [f.child(j) for f in folders for j in range(f.childCount())]]


def test_a_block_of_your_own_deleted_from_the_blocks_list(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Deleting a block of your own",
              "I should be able to delete a 'User Created' block and ONLY User Created blocks. If there are no User "
              "Created blocks the entire 'User Created' group dissapears as well.")
    p.idea("Right-click a block of your own under User Created in the Blocks list and choose Delete block…, or "
           "click it and press Delete. Taskloom asks first; once you say yes, the block's file is deleted and "
           "the block is gone at once from the Blocks list, Help > Documentation and the hover help.")
    p.idea("The question names the block, the file that will be deleted, any other block that file defines (they go "
           "with it), and the blocks of your open flows that use it: they will show as unknown blocks. Cancel is "
           "the default button, so Enter answers Cancel. There is no undo: the question is the safeguard.")
    p.idea("A block that comes with Taskloom cannot be deleted: right-clicking it opens no menu, and Delete does "
           "nothing. When no block of your own is left, the User Created group is gone; it comes back with the "
           "next one you make.")
    p.why_simplest("A block of your own is one Python file in the blocks folder of your Taskloom folder: you wrote "
                   "it there, or Create Reusable block… did. Deleting the block is deleting that file. Taskloom then "
                   "forgets the blocks that file gave it, and draws the Blocks list, the Documentation and the open "
                   "canvases again from what it still knows. No list of deleted blocks is kept anywhere.")
    p.why_simplest("Taskloom already knows where each block comes from: with Taskloom, or from a file of yours. Delete "
                   "block… is offered only for a block from a file of yours, so built-in blocks need no rule of "
                   "their own.")
    p.why_simplest("The User Created group has no rule of its own either. The Blocks list shows a group for each kind "
                   "of block it has: with no block of your own left there is no such group, and it is back with the "
                   "next one.")
    p.alternative("Moving the file to the Recycle Bin, or to a folder of deleted blocks, so that a delete can be undone",
                  "Taskloom would need another place to look after and a way to bring files back, and each system's "
                  "bin works its own way. The question names exactly what will go before anything does; you can "
                  "copy the file first if you may want it again.")
    p.alternative("Refusing to delete a block that a flow uses",
                  "You would first have to change or close every flow that uses it, and it still could not cover "
                  "the flows you have not opened: Taskloom does not know every flow file on your computer. The "
                  "question says which blocks of your open flows will be unknown, and you decide.")
    p.alternative("Taking the block out of the flows that use it",
                  "That would change your flows without your asking, and lose the block's settings and lines. "
                  "Shown as an unknown block, it keeps them in the flow: with its file back in the blocks folder "
                  "(the editor reads it when it starts), the flow is as it was.")
    p.alternative("A separate window to manage your blocks",
                  "One more window to find. The Blocks list already lists every block of your own, and a "
                  "right-click menu is where Taskloom puts what you can do with a thing, as on the canvas.")
    p.fits("It is the reverse of Create Reusable block…: that writes a file and adds its block to the Blocks list "
           "and the Documentation at once; Delete block… deletes the file and takes its blocks away at once. "
           "Neither needs the editor started again.")
    p.fits("An unknown block is what Taskloom already shows for a block whose type it does not know, as in a flow "
           "opened on a computer without that block's file: its name, settings and lines stay in the flow, and its "
           "Properties say “This block type is not installed.”")
    p.fits("Right-click and Delete work in the Blocks list as they do on the canvas, where a block's menu holds "
           "what you can do with it and Delete deletes what is selected.")
    blocks_dir = win.home.blocks_dir
    round_file, region_file = blocks_dir / "round_amounts.py", blocks_dir / "region_names.py"
    flow = tmp_path / "load_sales.yaml"
    flow.write_text(textwrap.dedent(FLOW), encoding="utf-8")
    tree = win.palette_panel.tree

    # --- 1. two blocks of your own, one of them used in an open flow ------------------------------------------
    p.step("Start the editor with two blocks of your own in the blocks folder of your Taskloom folder: Round amounts "
           "(round_amounts.py) and Region names (region_names.py). Open the flow load_sales.yaml (File > Open…) and "
           "click its block round_sales, a Round amounts block.")
    tab = open_flow(win, monkeypatch, flow)
    click_block(tab, "round_sales")
    decimals = setting_row(win, "Decimals")
    p.check("The Blocks list has the group User Created, with Region names and Round amounts",
            groups(win).count("User Created  (2)") == 1
            and in_group(win, "User Created") == ["Region names", "Round amounts"])
    p.check("round_sales is a Round amounts block, and its Properties show its setting Decimals",
            tab.scene.nodes["round_sales"].known and tab.scene.nodes["round_sales"].type_title == "Round amounts"
            and "Decimals" in texts(win.properties.widget()))
    mine = whole_group(win, "User Created")
    p.shot(win, "① The Blocks list has the group User Created: your two blocks. ② The flow load_sales.yaml uses one "
                "of them, Round amounts, as its block round_sales: ③ its Properties show its setting, Decimals.",
           highlight=[mine, "round_sales", decimals], badges=True, zoom=mine)
    before, shown = flow_state(tab, flow), canvas(tab)
    round_text, region_text = round_file.read_bytes(), region_file.read_bytes()

    p.step("Rest the mouse on round_sales.")
    tip = hover_block(qtbot, tab, "round_sales")
    p.check("Its hover help says what Round amounts does", tip is not None and ROUND_SAYS in tip.text())
    p.shot(tip, "Its hover help says what Round amounts does.", over=win, highlight=[tip], zoom=[tip, "round_sales"])
    tip.hide()

    p.step("Open Help > Documentation, and click User Created in its Contents.")
    toggle_documentation(win)
    qtbot.waitUntil(lambda: win.docs.isVisible() and "Reference" in docs_page(win), timeout=5_000)
    click_link(win.docs.browser, lambda href, text: href == "blocks-user-created.html")
    qtbot.waitUntil(lambda: ROUND_SAYS in docs_page(win), timeout=5_000)
    browser = win.docs.browser
    p.shot(win.docs, "The page User Created has an entry for each of your blocks: ① Region names, ② Round amounts.",
           highlight=[(browser, "Region names"), (browser, "Round amounts")], badges=True)
    p.check("The Documentation's User Created page has Region names and Round amounts",
            all(s in docs_page(win) for s in (REGION_SAYS, ROUND_SAYS)))
    p.step("Close the Documentation column (Help > Documentation again), to give the canvas back its room.")
    toggle_documentation(win)
    assert not win.docs.isVisible()

    # --- 2. only blocks of your own can be deleted ------------------------------------------------------------
    p.step("Right-click Round amounts in the Blocks list.")
    mine = whole_group(win, "User Created")

    def look_at_menu(menu):
        p.check("Its menu has Delete block…", [a.text() for a in menu.actions() if a.text()] == [DELETE])
        p.shot(menu, "① Delete block…: the one item of its menu. Its … says that it asks first.", over=win,
               highlight=[menu_item(menu, DELETE)], badges=True, zoom=[menu, mine])

    p.while_open(lambda: right_click_row(win, block_row(win, "Round amounts")), look_at_menu)

    p.step("Right-click Wait, a block that comes with Taskloom, then the row of the group User Created itself.")
    for title, item in (("Wait", block_row(win, "Wait")), ("the row of the group User Created",
                                                             group(win, "User Created"))):
        p.check(f"Right-clicking {title} opens no menu", not opens_a_menu(lambda: right_click_row(win, item)))

    p.step("Click Wait, and press Delete.")
    press_delete(qtbot, win, block_row(win, "Wait"))
    QTest.qWait(300)
    logic = rows(win, group(win, "Logic"), block_row(win, "Wait"))
    p.shot(win, "Delete on ① Wait did nothing: nothing was asked, and the Blocks list and the flow are as they were. "
                "② round_sales, selected on the canvas, is still there too: Delete in the Blocks list deletes "
                "nothing from the flow.",
           highlight=[rows(win, block_row(win, "Wait"), block_row(win, "Wait")), "round_sales"], badges=True,
           zoom=logic)
    p.check("Nothing was asked, and both files are still there",
            win.message_boxes == [] and round_file.read_bytes() == round_text
            and region_file.read_bytes() == region_text)
    p.check("The Blocks list and the flow are as they were, round_sales still selected",
            in_group(win, "User Created") == ["Region names", "Round amounts"] and block_row(win, "Wait")
            and flow_state(tab, flow) == before and canvas(tab) == shown
            and tab.scene.nodes["round_sales"].isSelected())

    # --- 3. the question, and Cancel --------------------------------------------------------------------------
    p.step("Right-click Round amounts again and choose Delete block….")
    p.note("Only open flows are named: Taskloom does not know every flow file on your computer. A flow you open "
           "later that uses the block shows it as an unknown block in the same way. If the file also defines other "
           "blocks, the question names them too, since they go with it.")
    questions = []

    def look_at_question(box):
        default = box.defaultButton()
        questions.append((box.text(), default.text().replace("&", "") if default is not None else None))
        p.shot(box, "① It names the block, the file that will be deleted, and the block of the open flow that uses "
                    "it, which will show as an unknown block. ② Cancel is the default button.",
               highlight=[label(box, box.text()), box.button(QMessageBox.Cancel)], badges=True)
        p.step("Press Enter: Cancel is the default button, so Enter answers Cancel.")
        QTest.keyClick(box, Qt.Key_Return)

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "question", REAL_QUESTION)
        p.while_open(lambda: right_click_row(win, block_row(win, "Round amounts")),
                     lambda menu: p.while_open(lambda: choose(menu, DELETE), look_at_question))
    (question, default), = questions
    mine = whole_group(win, "User Created")
    p.shot(win, "Cancel: nothing changed. ① Round amounts is still under User Created, and ② round_sales is as it "
                "was.", highlight=[mine, "round_sales"], badges=True, zoom=mine)
    p.check("It names the block, Round amounts, and the file it will delete: round_amounts.py, with its folder",
            "Round amounts" in question and names(round_file, question))
    p.check("It names the open flow and its block that use it, “load_sales.yaml: round_sales”, which will show as "
            "an unknown block", "load_sales.yaml: round_sales" in question and "unknown block" in question)
    p.check("Cancel is the default button", default == "Cancel")
    p.check("Cancel kept everything: the file, the Blocks list, the Documentation and the flow",
            round_file.read_bytes() == round_text
            and in_group(win, "User Created") == ["Region names", "Round amounts"]
            and ROUND_SAYS in docs_page(win) and flow_state(tab, flow) == before and canvas(tab) == shown)

    # --- 4. Yes: the file is deleted, the block is gone at once ----------------------------------------------
    p.step("This time, click Round amounts in the Blocks list and press Delete. The same question opens: click Yes.")
    p.note("There is no undo: Edit > Undo undoes changes to a flow, and this changed no flow. To have the block "
           "again, put its file back in the blocks folder (the editor reads it when it starts), or make it again.")
    asked = []

    def say_yes(box):
        asked.append(box.text())
        QTest.mouseClick(box.button(QMessageBox.Yes), Qt.LeftButton)

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "question", REAL_QUESTION)
        p.while_open(lambda: press_delete(qtbot, win, block_row(win, "Round amounts")), say_yes)
    QApplication.processEvents()
    node = tab.scene.nodes["round_sales"]
    props = win.properties.widget()
    not_installed = [label(props, "<b>my.round_amounts</b>"), label(props, "This block type is not installed.")]
    p.check("Delete asked the same question", asked == [question])
    mine = whole_group(win, "User Created")
    p.shot(win, "At once, without starting the editor again: ① under User Created, only Region names is left. ② "
                "round_sales shows as an unknown block, and ③ its Properties, which showed its Decimals, now say "
                "“This block type is not installed.”",
           highlight=[mine, "round_sales", not_installed], badges=True, zoom=mine)
    p.check("round_amounts.py is deleted; region_names.py is as it was",
            not round_file.exists() and region_file.read_bytes() == region_text)
    p.check("① Under User Created, only Region names is left; Round amounts is nowhere in the Blocks list",
            in_group(win, "User Created") == ["Region names"]
            and not tree.findItems("Round amounts", Qt.MatchExactly | Qt.MatchRecursive))
    p.check("No row of the Blocks list has Round amounts' hover help",
            not any(ROUND_SAYS in tip for tip in tooltips(win)))
    p.check("② round_sales shows as an unknown block: “unknown: my.round_amounts” at its foot, and no icon",
            not node.known and node.type_title == "unknown: my.round_amounts" and node.icon == ())
    p.check("③ Its Properties say “This block type is not installed.”",
            "This block type is not installed." in texts(props) and "Decimals" not in texts(props))

    p.step("Rest the mouse on round_sales.")
    tip = hover_block(qtbot, tab, "round_sales")
    p.check("No hover help shows: round_sales has none any more", tip is None and node.toolTip() == "")
    p.shot(win, "No hover help shows on ① round_sales. Its line from load_sales is not drawn: Taskloom no longer "
                "knows the block's inputs and outputs (only error, which every block but a start block has), so the "
                "line has nowhere to end; it is still in the flow. ② Its Properties keep its name.",
           highlight=["round_sales", not_installed], badges=True, zoom=["round_sales", *not_installed])
    p.check("Nothing else changed in the flow: in the editor and on disk it is as it was, round_sales with its "
            "setting (decimals: 1) and its lines, and nothing unsaved",
            flow_state(tab, flow) == before and tab.doc.blocks["round_sales"]["config"] == {"decimals": 1}
            and ("load_sales", "amounts", "round_sales", "amounts") in tab.doc.edges())
    p.check("The canvas shows exactly what an editor started now shows of load_sales.yaml, round_amounts.py being "
            "gone", canvas(tab) == as_opened_now(qtbot, win, flow))

    p.step("Open Help > Documentation again; then click Contents, at its top.")
    toggle_documentation(win)
    qtbot.waitUntil(lambda: win.docs.isVisible(), timeout=5_000)
    p.shot(win.docs, "The page User Created now has ① Region names only: Round amounts is gone from it.",
           highlight=[(browser, "Region names")], badges=True)
    p.check("The page User Created has Region names, and not Round amounts",
            REGION_SAYS in docs_page(win) and ROUND_SAYS not in docs_page(win))
    QTest.mouseClick(next(b for b in win.docs.findChildren(QToolButton) if b.text() == "Contents"), Qt.LeftButton)
    qtbot.waitUntil(lambda: browser.source().fileName() == "index.html", timeout=5_000)
    p.check("Contents lists Region names, and not Round amounts",
            "Region names" in docs_page(win) and "Round amounts" not in docs_page(win))
    toggle_documentation(win)

    # --- 5. the last block of your own: the group goes, and comes back ---------------------------------------
    p.step("Delete Region names too, the last block of your own: right-click it, choose Delete block…, and click Yes.")
    asked = []
    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "question", REAL_QUESTION)
        p.while_open(lambda: right_click_row(win, block_row(win, "Region names")),
                     lambda menu: p.while_open(lambda: choose(menu, DELETE), say_yes))
    QApplication.processEvents()
    p.check("It asked first, naming Region names and its file, and saying that no open flow uses it",
            len(asked) == 1 and "Region names" in asked[0] and names(region_file, asked[0])
            and "No open flow uses it." in asked[0])
    p.check("region_names.py is deleted: the blocks folder has no block file left",
            not region_file.exists() and sorted(blocks_dir.glob("*.py")) == [])
    p.check("The Blocks list has no User Created group any more",
            not any(g.startswith("User Created") for g in groups(win)))
    end = rows(win, group(win, "Debug"), group(win, "Notes").child(0))
    p.shot(win, "① At the end of the Blocks list, Debug is now followed by Notes: the User Created group is gone, as "
                "no block of your own is left.", highlight=[end], badges=True, zoom=end)

    p.step(f"Make a block of your own again: right-click load_sales on the canvas, choose Create Reusable block…, name "
           f"it {NEW_NAME}, say what it does, and press OK.")

    def fill_in(dialog):
        dialog.name.setText(NEW_NAME)
        dialog.what.setPlainText(NEW_SAYS)
        QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok), Qt.LeftButton)

    p.while_open(lambda: right_click(tab, "load_sales"),
                 lambda menu: p.while_open(lambda: choose(menu, "Create Reusable block…"), fill_in))
    QApplication.processEvents()
    new_file = blocks_dir / NEW_FILE
    p.check(f"The group User Created is back, with {NEW_NAME}",
            groups(win).count("User Created  (1)") == 1 and in_group(win, "User Created") == [NEW_NAME]
            and new_file.exists())
    end = rows(win, group(win, "Debug"), group(win, "Notes").child(0))
    p.shot(win, f"① The group User Created is back, with {NEW_NAME}, between Debug and Notes.",
           highlight=[whole_group(win, "User Created")], badges=True, zoom=end)

    # --- 6. a file that cannot be deleted --------------------------------------------------------------------
    p.step(f"Make {NEW_FILE} impossible to delete, then right-click {NEW_NAME}, choose Delete block… and click Yes.")
    p.note("On Windows the file is made read-only; elsewhere its folder is made one that cannot be written to. "
           "Either way the system refuses to delete the file, as it would a file in a folder you may only read.")
    new_text, before, listed, documented = new_file.read_bytes(), flow_state(tab, flow), groups(win), docs_page(win)
    warned = []

    def look_at_warning(box):
        warned.append(box.text())
        p.shot(box, "It says why the file could not be deleted, in the system's words, and that nothing was "
                    "changed.", highlight=[label(box, box.text())])
        QTest.mouseClick(box.button(QMessageBox.Ok), Qt.LeftButton)

    undo = undeletable(new_file)
    try:
        with monkeypatch.context() as real:
            real.setattr(QMessageBox, "question", REAL_QUESTION)
            real.setattr(QMessageBox, "warning", REAL_WARNING)
            p.while_open(lambda: right_click_row(win, block_row(win, NEW_NAME)),
                         lambda menu: boxes_in_turn(lambda: choose(menu, DELETE), say_yes, look_at_warning))
    finally:
        undo()
    QApplication.processEvents()
    reason = "Access is denied" if sys.platform == "win32" else "Permission denied"
    p.check(f"A message names the file, says it could not be deleted and why (“{reason}”), and that nothing was "
            "changed", len(warned) == 1 and names(new_file, warned[0]) and "could not be deleted" in warned[0]
            and reason.lower() in warned[0].lower() and "Nothing was changed." in warned[0])
    p.check("Nothing changed: the file, the Blocks list, the Documentation and the flow are as they were",
            new_file.read_bytes() == new_text and groups(win) == listed and in_group(win, "User Created") == [NEW_NAME]
            and docs_page(win) == documented and NEW_NAME in documented and flow_state(tab, flow) == before)
