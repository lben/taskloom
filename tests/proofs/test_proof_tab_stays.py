"""Proof: an edit on the Parameters or the Flow tab of the Inspector stays on that tab, whatever is selected on the
canvas; choosing a block or a text yourself still shows its Properties."""

import re

import yaml
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from proofs.conftest import ZOOM_MARGIN
from proofs.test_proof_canvas import open_flow
from taskloom.editor.canvas import HEADER_H, NODE_W, NodeItem
from tests.test_inspector_tab_stays import button, selected, settle, showing

CONNECTIONS = {"sales_db": {"kind": "jdbc", "driver": "org.example.Driver", "url": "jdbc:example://localhost/sales",
                            "jars": []}}
LOAD_SALES = {
    "name": "load-sales",
    "blocks": {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
        "load_sales": {"type": "db.run_query", "config": {"connection": "sales_db",
                                                          "sql": "select * from sales where day = :day"},
                       "ui": {"x": 260, "y": 0}},
    },
    "edges": ["start.start -> load_sales.after"],
    "notes": {"note_1": {"text": "Loads the sales of the day before", "x": 0, "y": -110}},
}
TEXT = "the text “Loads the sales of the day before”"
YESTERDAY = "today() - days(1)"


def write(path, data: dict):
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def every_kind(kinds: dict) -> dict:
    """A flow with one block of each kind (`kinds`: block -> (its type, its title)), in rows of five, and a
    parameter."""
    blocks = {block_id: {"type": type_id, "ui": {"x": n % 5 * 240, "y": n // 5 * 220}}
              for n, (block_id, (type_id, _)) in enumerate(kinds.items())}
    return {"name": "every-kind", "params": {"day": {"type": "date", "expr": YESTERDAY}}, "blocks": blocks,
            "edges": []}


# --- where things are, in a screenshot of the window ------------------------------------------------------

def tab_area(win, tabs, index: int) -> QRect:
    """The tab `index` of `tabs` (the Inspector's, or the flows' above the canvas)."""
    bar = tabs.tabBar()
    return bar.tabRect(index).translated(bar.mapTo(win, QPoint(0, 0)))


def inspector_tab(win, panel) -> QRect:
    return tab_area(win, win.right_tabs, win.right_tabs.indexOf(panel))


def flow_tab(win) -> QRect:
    """The tab of the flow on screen, above the canvas: its name, and * when it has unsaved changes."""
    return tab_area(win, win.tabs, win.tabs.currentIndex())


def text_on_canvas(win, tab, note_id: str) -> QRect:
    view = tab.view
    area = view.mapFromScene(tab.scene.note_items[note_id].sceneBoundingRect()).boundingRect()
    return area.translated(view.viewport().mapTo(win, QPoint(0, 0)))


def inspector_down_to(win, bottom: int) -> QRect:
    """For a close-up of the Inspector from just above its tabs (room for their outline and badge) to just below
    `bottom`: a close-up adds a margin around what it shows, taken off here so that it shows little else."""
    QTest.qWait(100)  # laid out (as a screenshot waits before it is taken)
    top = win.right_tabs.mapTo(win, QPoint(0, 0))
    return QRect(top.x(), top.y(), win.right_tabs.width(), bottom - top.y()).adjusted(
        ZOOM_MARGIN - 14, ZOOM_MARGIN - 18, -ZOOM_MARGIN, 8 - ZOOM_MARGIN)


def parameters_zoom(win) -> QRect:
    """The Inspector's tabs and the parameters (or the table's header, when there are none)."""
    table = win.parameters.table
    if table.rowCount():
        row = table.visualItemRect(table.item(table.rowCount() - 1, 0))
        bottom = table.viewport().mapTo(win, QPoint(0, row.bottom())).y()
    else:
        header = table.horizontalHeader()
        bottom = header.mapTo(win, QPoint(0, header.height())).y() + 20
    return inspector_down_to(win, bottom)


# --- doing it, as you would -------------------------------------------------------------------------------

def click_on_canvas(tab, item):
    """Click a block's title, or a text, on the canvas; scrolled into view first if it is not in view."""
    view = tab.view
    if not view.viewport().rect().contains(view.mapFromScene(item.sceneBoundingRect()).boundingRect()):
        view.centerOn(item)
        settle()
    local = QPointF(NODE_W / 2, HEADER_H / 2) if isinstance(item, NodeItem) else item.boundingRect().center()
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, view.mapFromScene(item.mapToScene(local)))
    settle()


def click_inspector_tab(win, panel):
    """Click a tab of the Inspector: Properties, Parameters or Flow."""
    bar = win.right_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.right_tabs.indexOf(panel)).center())
    settle()


def click_button(win, text: str):
    QTest.mouseClick(button(win.parameters, text), Qt.LeftButton)
    settle()


def type_in_cell(table, row: int, column: int, text: str):
    """Double-click a cell of a table, type `text` over what it holds and press Enter."""
    item = table.item(row, column)
    table.scrollToItem(item)
    at = table.visualItemRect(item).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # a double click: a click, then the
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # second one (QTest sends only that)
    editor = next(e for e in table.viewport().findChildren(QLineEdit) if e.isVisible())
    editor.selectAll()
    QTest.keyClicks(editor, text)
    QTest.keyClick(editor, Qt.Key_Return)
    settle()


def type_in(edit: QLineEdit, text: str):
    """Click a field, type `text` over what it holds and press Enter."""
    QTest.mouseClick(edit, Qt.LeftButton)
    edit.selectAll()
    QTest.keyClicks(edit, text)
    QTest.keyClick(edit, Qt.Key_Return)
    settle()


def choose_in_list(p, combo, text: str):
    """Click a list to open it, then click `text` in it."""
    index = combo.findText(text)
    assert index >= 0, f"{text!r} is not in the list"

    def pick(_list):
        # A click just after the list opened is taken as the end of the click that opened it, not as a choice.
        QTest.qWait(QApplication.doubleClickInterval() + 100)
        view = combo.view()
        view.scrollTo(view.model().index(index, 0))
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier,
                         view.visualRect(view.model().index(index, 0)).center())

    p.while_open(lambda: QTest.mouseClick(combo, Qt.LeftButton), pick)
    settle()
    assert combo.currentText() == text, f"{text!r} was not chosen"


def select_row(table, row: int):
    """Click a parameter's name (before Remove)."""
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, table.visualItemRect(table.item(row, 0)).center())
    settle()


def test_edits_on_parameters_and_flow_stay_on_their_tab(proof, win, tmp_path, monkeypatch):
    p = proof("Edits on Parameters and Flow stay on that tab",
              "Run Query: For some reason when creating, editing or deleting parameters it jumps to the Properties "
              "tab and doesn't stay on the Parameters tab, not sure if this happens only in this block, worth "
              "checking the others as well")
    p.idea("When you add, change or delete a parameter on the Parameters tab, or change a setting on the Flow tab, "
           "that tab stays in front, and what you had selected on the canvas stays selected. Properties comes to "
           "the front when you choose something on the canvas yourself, as before.")
    p.idea("Why it jumped: every change to the flow draws the canvas again. Once drawn, the canvas said which block "
           "was selected, just as it does when you click one, and the window took that as you choosing the block: "
           "it brought its Properties to the front. Now the canvas also says whether it was your choice. After it "
           "is drawn again it says “still selected, not a new choice”, and the window then only keeps Properties up "
           "to date, behind the tab you are on.")
    p.idea("You were right to suspect the others: it was not only Run Query. It happened with any block selected, "
           "and with a text on the canvas selected too, on the Parameters and on the Flow tab. With nothing "
           "selected the tab stayed, which is why it seemed to depend on the block.")
    p.why_simplest("One fact was missing: whether a selection was your choice or only the canvas being drawn again. "
                   "The canvas knows it, so it now says it, and the window brings Properties to the front only for a "
                   "choice. Nothing else changed: each edit still draws the canvas, and Properties still follows the "
                   "selection.")
    p.why_simplest("So one rule covers every edit on either tab, every kind of block and texts, without a list of "
                   "which edits, tabs or blocks are concerned.")
    p.alternative("Not drawing the canvas again after an edit of a parameter or of a Flow setting",
                  "The canvas draws itself again after every change to the flow: one rule, which is why it never "
                  "shows something out of date. Telling the kinds of change apart would be a second rule to keep "
                  "right, and the jump would remain after other changes made while Parameters shows, such as Edit > "
                  "Undo.")
    p.alternative("Bringing the tab you were on back to the front after each edit",
                  "It would undo the jump instead of not making it: every place that changes the flow, on every "
                  "tab, would have to remember to do it, and the next one added could forget.")
    p.alternative("The canvas no longer saying what is selected after it is drawn again",
                  "The window needs it: when you rename the selected block, or a change takes it away (Edit > Undo, "
                  "for one), the Log, which then shows only that block's lines, follows it to its new name, or back "
                  "to all blocks. So the canvas still says what is selected, only no longer as a choice.")
    p.fits("Choosing on the canvas works as before: a click on a block or on a text, Edit > Paste (the copy is "
           "selected) and Edit > Add text (the new text is selected, for you to write it) bring Properties to the "
           "front, from any tab.")
    p.fits("Properties is kept up to date behind the tab you are on: when you go back to it, it shows what is "
           "selected. The same holds whether the flow is saved or has unsaved changes (a * after its name).")

    write(win.home.root / "connections.yaml", CONNECTIONS)  # the database load_sales queries
    flow = write(tmp_path / "load-sales.yaml", LOAD_SALES)
    params = lambda: tab.doc.data.get("params") or {}  # noqa: E731
    table = win.parameters.table

    def stays(what: str, edit: str, reached, thing="load_sales", chosen=(["load_sales"], []), tab_name="Parameters"):
        p.check(f"{what}: {edit}; {tab_name} still shows, and {thing} is still selected",
                reached() and showing(win) == tab_name and selected(tab) == chosen)

    def look(caption: str, row=0, text=False):
        """A screenshot: ① the Parameters tab, ② the selected block (or text); with a parameter, ③ its row and ④ the
        flow's tab."""
        marks = [inspector_tab(win, win.parameters), text_on_canvas(win, tab, "note_1") if text else "load_sales"]
        if row is not None:
            marks += [(table, row), flow_tab(win)]
        p.shot(win, caption, highlight=marks, badges=True, zoom=parameters_zoom(win))

    p.step("Open the flow load-sales.yaml (File > Open…). It has a Manual Start block, start, and a Run Query block, "
           f"load_sales, whose query uses a parameter :day; above them, {TEXT}.")
    tab = open_flow(win, monkeypatch, flow)
    settle()
    p.check("The flow opens, saved: its tab says load-sales.yaml, with no *",
            tab.doc.path == flow.resolve() and not tab.doc.dirty
            and win.tabs.tabText(win.tabs.currentIndex()) == "load-sales.yaml")

    p.step("Click load_sales on the canvas.")
    click_on_canvas(tab, tab.scene.nodes["load_sales"])
    name = next(e for e in win.properties.findChildren(QLineEdit) if e.text() == "load_sales")
    p.shot(win, "① load_sales is selected, so ② Properties comes to the front with its settings: choosing a block "
                "yourself shows its Properties, as before.",
           highlight=["load_sales", inspector_tab(win, win.properties)], badges=True,
           zoom=inspector_down_to(win, name.mapTo(win, QPoint(0, name.height())).y()))
    p.check("① load_sales is selected and ② Properties shows it",
            selected(tab) == (["load_sales"], []) and showing(win) == "Properties"
            and win.properties.block_id == "load_sales")

    p.step("Click the Parameters tab of the Inspector, then Add.")
    click_inspector_tab(win, win.parameters)
    click_button(win, "Add")
    look("① Parameters is still in front, with ③ the new parameter param1, and ② load_sales is still selected. "
         "④ The flow's tab now says load-sales.yaml *: it has unsaved changes.")
    stays("Add", "the flow has a new parameter, param1", lambda: list(params()) == ["param1"])
    p.check("④ The flow now has unsaved changes: its tab says load-sales.yaml *, and the window's title says "
            "Taskloom — load-sales.yaml *",
            tab.doc.dirty and win.tabs.tabText(win.tabs.currentIndex()) == "load-sales.yaml *"
            and win.windowTitle() == "Taskloom — load-sales.yaml *")
    p.note("A screenshot shows the inside of the window, not its title bar: the pictures point at the flow's tab, "
           "which says the same.")

    p.step("Double-click param1 in the Name column, type day and press Enter.")
    type_in_cell(table, 0, 0, "day")
    look("③ The parameter is now called day. ① Parameters is still in front, and ② load_sales is still selected.")
    stays("Name", "the parameter is now called day", lambda: list(params()) == ["day"])

    p.step("In the Type column, click text and choose date.")
    choose_in_list(p, table.cellWidget(0, 1), "date")
    look("③ day is now a date (its Preview is red: it has no value yet). ① Parameters is still in front, and "
         "② load_sales is still selected.")
    stays("Type", "day is a date", lambda: params().get("day", {}).get("type") == "date")

    p.step("In the Kind column, click value and choose expression.")
    choose_in_list(p, table.cellWidget(0, 2), "expression")
    look("③ day is now worked out from an expression (its Preview is red until you type one). ① Parameters is "
         "still in front, and ② load_sales is still selected.")
    stays("Kind", "day is an expression", lambda: "expr" in params().get("day", {}))

    p.step(f"Double-click day's Value, type {YESTERDAY} and press Enter.")
    type_in_cell(table, 0, 3, YESTERDAY)
    look(f"③ day is {YESTERDAY}: its Preview shows the date of the day before. ① Parameters is still in front, and "
         "② load_sales is still selected.")
    stays("Value", f"day is {YESTERDAY}", lambda: params().get("day", {}).get("expr") == YESTERDAY)

    p.step("Click day's name, then Remove.")
    select_row(table, 0)
    click_button(win, "Remove")
    look("The parameter is gone. ① Parameters is still in front, and ② load_sales is still selected.", row=None)
    stays("Remove", "the flow has no parameter", lambda: params() == {})

    p.step(f"Click {TEXT}, on the canvas.")
    click_on_canvas(tab, tab.scene.note_items["note_1"])
    p.shot(win, "① The text is selected, so ② Properties comes to the front with its settings. ③ The flow still has "
                "unsaved changes.",
           highlight=[text_on_canvas(win, tab, "note_1"), inspector_tab(win, win.properties), flow_tab(win)],
           badges=True)
    text_chosen = ([], ["note_1"])
    p.check("① The text is selected and ② Properties shows it",
            selected(tab) == text_chosen and showing(win) == "Properties" and win.properties.note_id == "note_1")

    p.step(f"Click the Parameters tab, and make the same edits: Add, rename param1 to day, choose date and "
           f"expression, and type {YESTERDAY}.")
    click_inspector_tab(win, win.parameters)
    for what, edit, do, reached in [
            ("Add", "the flow has param1", lambda: click_button(win, "Add"), lambda: list(params()) == ["param1"]),
            ("Name", "it is called day", lambda: type_in_cell(table, 0, 0, "day"), lambda: list(params()) == ["day"]),
            ("Type", "day is a date", lambda: choose_in_list(p, table.cellWidget(0, 1), "date"),
             lambda: params().get("day", {}).get("type") == "date"),
            ("Kind", "day is an expression", lambda: choose_in_list(p, table.cellWidget(0, 2), "expression"),
             lambda: "expr" in params().get("day", {})),
            ("Value", f"day is {YESTERDAY}", lambda: type_in_cell(table, 0, 3, YESTERDAY),
             lambda: params().get("day", {}).get("expr") == YESTERDAY)]:
        do()
        stays(what, edit, reached, thing="the text", chosen=text_chosen)
    look(f"③ day is {YESTERDAY}. ① Parameters is still in front, ② the text is still selected, and ④ the flow "
         "still has unsaved changes.", text=True)

    p.step("Click day's name, then Remove.")
    select_row(table, 0)
    click_button(win, "Remove")
    look("The parameter is gone. ① Parameters is still in front, and ② the text is still selected.", row=None,
         text=True)
    stays("Remove", "the flow has no parameter", lambda: params() == {}, thing="the text", chosen=text_chosen)

    p.step("Click load_sales again, then the Flow tab. Click Description, type Loads yesterday's sales and press "
           "Enter.")
    click_on_canvas(tab, tab.scene.nodes["load_sales"])
    p.check("The click on load_sales brought Properties to the front, with load_sales",
            showing(win) == "Properties" and win.properties.block_id == "load_sales")
    click_inspector_tab(win, win.flow_settings)
    panel = win.flow_settings
    type_in(panel.description, "Loads yesterday's sales")
    stays("Description", "the flow's description is “Loads yesterday's sales”",
          lambda: tab.doc.data.get("description") == "Loads yesterday's sales", tab_name="Flow")

    p.step("In “If a scheduled run starts while the last one is still going”, choose “run it after the current one”.")
    choose_in_list(p, panel.overlap, "run it after the current one")
    p.shot(win, "① Flow is still in front, with ③ the description and ④ the choice you made, and ② load_sales is "
                "still selected.",
           highlight=[inspector_tab(win, win.flow_settings), "load_sales", panel.description, panel.overlap],
           badges=True, zoom=inspector_down_to(win, panel.schedule.mapTo(win, QPoint(0, panel.schedule.height())).y()))
    stays("If a scheduled run starts…", "the flow runs it after the current one",
          lambda: tab.doc.data.get("overlap") == "queue", tab_name="Flow")

    kinds = {re.sub(r"\W+", "_", cls.title.lower()).strip("_"): (type_id, cls.title)
             for type_id, cls in win.registry.blocks.items()}
    p.step(f"Open the flow every-kind.yaml. It has one block of each of the {len(kinds)} kinds of block Taskloom "
           f"has ({', '.join(title for _, title in kinds.values())}), and a parameter, day.")
    tab = open_flow(win, monkeypatch, write(tmp_path / "every-kind.yaml", every_kind(kinds)))
    settle()
    p.note("Its blocks come from the file without their settings, so many say what they still need. That does not "
           "matter here: only which tab shows does.")

    p.step("For each block in turn: click it on the canvas, then the Parameters tab; double-click day's Value, type "
           "today() - days(n), with n the block's number, and press Enter.")
    wrong = {}  # kind of block -> what went wrong with it selected
    for n, block_id in enumerate(kinds, 1):
        click_on_canvas(tab, tab.scene.nodes[block_id])
        problems = []
        if (showing(win), win.properties.block_id, selected(tab)) != ("Properties", block_id, ([block_id], [])):
            problems.append(f"the click showed {showing(win)}")
        click_inspector_tab(win, win.parameters)
        value = f"today() - days({n})"
        type_in_cell(table, 0, 3, value)
        if params().get("day", {}).get("expr") != value:
            problems.append("the edit did not reach the flow")
        if (showing(win), selected(tab)) != ("Parameters", ([block_id], [])):
            problems.append(f"after the edit, {showing(win)} showed, with {selected(tab)} selected")
        if problems:
            wrong[kinds[block_id][1]] = problems
    last = block_id
    p.shot(win, f"After the last one, {kinds[last][1]}: ③ day is today() - days({len(kinds)}), ① Parameters is "
                f"still in front, and ② {last} is still selected.",
           highlight=[inspector_tab(win, win.parameters), last, (table, 0)], badges=True, zoom=parameters_zoom(win))
    p.check(f"With each of the {len(kinds)} kinds of block selected, a click on it showed its Properties, and the "
            f"edit of day reached the flow and kept Parameters in front and the block selected"
            + (f"; not so for {wrong}" if wrong else ""),
            not wrong and {t for t, _ in kinds.values()} == set(win.registry.blocks)
            and len(tab.doc.blocks) == len(win.registry.blocks))

    p.step("With the Parameters tab in front, click run_query on the canvas.")
    click_on_canvas(tab, tab.scene.nodes["run_query"])
    p.shot(win, "① Choosing run_query yourself brings ② Properties to the front, with its settings, as before.",
           highlight=["run_query", inspector_tab(win, win.properties)], badges=True)
    p.check("① run_query is selected and ② Properties shows it",
            selected(tab) == (["run_query"], []) and showing(win) == "Properties"
            and win.properties.block_id == "run_query")
    for each in win.tabs_list():
        each.doc.dirty = False  # the edits were made only to be shown: closing the window must not ask
