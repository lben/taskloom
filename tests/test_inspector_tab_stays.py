"""Editing on the Parameters or the Flow tab of the Inspector stays on that tab: adding, editing or deleting a flow
parameter, or changing the flow's name, description, calendar, time zone or overlap, with any block (Run Query or
another) or a text selected on the canvas, saved or with unsaved changes. Choosing a block or a text yourself (a
click on the canvas, a paste, Add text) still shows its Properties, and an edit never changes what is selected."""

import os

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from taskloom.editor.canvas import HEADER_H, NODE_W, NodeItem  # noqa: E402


# --- helpers ---------------------------------------------------------------------------

def settle():
    """Let pending events run (some rebuilds of the canvas wait a turn)."""
    for _ in range(3):
        QApplication.processEvents()


def showing(win) -> str:
    """The Inspector tab that shows: Properties, Parameters or Flow."""
    return win.right_tabs.tabText(win.right_tabs.currentIndex())


def selected(tab) -> tuple[list, list]:
    return tab.scene.selected_block_ids(), tab.scene.selected_note_ids()


def click(tab, item):
    """A left click on a block's title or on a text on the canvas, brought into view first."""
    tab.view.centerOn(item)
    settle()
    local = QPointF(NODE_W / 2, HEADER_H / 2) if isinstance(item, NodeItem) else item.boundingRect().center()
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier,
                     tab.view.mapFromScene(item.mapToScene(local)))
    settle()


def button(panel, text: str) -> QPushButton:
    return next(b for b in panel.findChildren(QPushButton) if b.text() == text)


def type_in(edit, text: str):
    """Type into a line and press Enter (what the Flow tab acts on)."""
    edit.setText(text)
    edit.editingFinished.emit()


def choose(box, index: int):
    """Pick an entry of a list, as a click on it does."""
    box.setCurrentIndex(index)
    box.activated.emit(index)


def parameter_edits(win, tab):
    """Each edit of the Parameters tab, as (what, do it, what the flow then has): add, name, type, kind, value,
    remove."""
    table = win.parameters.table

    def row():
        return table.rowCount() - 1

    def remove():
        table.setCurrentCell(row(), 0)
        button(win.parameters, "Remove").click()

    params = lambda: tab.doc.data.get("params") or {}  # noqa: E731
    return [
        ("add", lambda: button(win.parameters, "Add").click(), lambda: "param1" in params()),
        ("name", lambda: table.item(row(), 0).setText("region"), lambda: "region" in params()),
        ("type", lambda: table.cellWidget(row(), 1).setCurrentText("int"),
         lambda: params().get("region", {}).get("type") == "int"),
        ("kind", lambda: table.cellWidget(row(), 2).setCurrentText("expression"),
         lambda: "expr" in params().get("region", {})),
        ("value", lambda: table.item(row(), 3).setText("1 + 1"),
         lambda: params().get("region", {}).get("expr") == "1 + 1"),
        ("remove", remove, lambda: params() == {}),
    ]


def flow_edits(win, tab):
    """Each edit of the Flow tab, as (what, do it, what the flow then has)."""
    panel = win.flow_settings

    def typed_calendar():
        panel.calendar.setCurrentText("US")
        panel.calendar.lineEdit().editingFinished.emit()

    data = tab.doc.data
    return [
        ("name", lambda: type_in(panel.name, "nightly"), lambda: data.get("name") == "nightly"),
        ("description", lambda: type_in(panel.description, "the nightly load"),
         lambda: data.get("description") == "the nightly load"),
        ("calendar typed", typed_calendar, lambda: data.get("calendar") == "US"),
        ("calendar chosen", lambda: choose(panel.calendar, panel.calendar.findText("GB")),
         lambda: data.get("calendar") == "GB"),
        ("time zone", lambda: type_in(panel.timezone, "Europe/Madrid"),
         lambda: data.get("timezone") == "Europe/Madrid"),
        ("overlap", lambda: choose(panel.overlap, panel.overlap.findData("queue")),
         lambda: data.get("overlap") == "queue"),
    ]


def edit_on(win, tab, panel, edits) -> list:
    """Make each edit with `panel`'s tab showing; what showed after each one, and what was selected, as
    (what, tab, selection) wherever that is not `panel`'s tab and what was selected before (or the edit did not
    reach the flow)."""
    win.right_tabs.setCurrentWidget(panel)
    settle()
    expected, before, wrong = showing(win), selected(tab), []
    for what, do, done in edits:
        do()
        settle()
        if not done():
            wrong.append((what, "did not reach the flow"))
        if (showing(win), selected(tab)) != (expected, before):
            wrong.append((what, showing(win), selected(tab)))
            win.right_tabs.setCurrentWidget(panel)  # (each edit from that tab)
            settle()
    return wrong


def flow_file(path, blocks: dict, notes: dict | None = None):
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": [], **({"notes": notes} if notes else {})},
                                   sort_keys=False), encoding="utf-8")
    return path


QUERY = {"type": "db.run_query", "config": {"sql": "select 1"}, "ui": {"x": 0, "y": 0}}
REPORT = {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}, "ui": {"x": 400, "y": 0}}
NOTE = {"text": "Nightly load", "x": 0, "y": -200}


def clean(win):
    """Leave no unsaved changes: closing the window at the end of the test must not ask to save them (done before
    the checks, so that a failed check is the only failure)."""
    for tab in win.tabs_list():
        tab.doc.dirty = False


# --- the Parameters tab ---------------------------------------------------------------------

@pytest.mark.parametrize("unsaved", [False, True], ids=["saved", "unsaved changes"])
def test_adding_editing_and_removing_a_parameter_with_the_run_query_block_selected_stays_on_parameters(
        win, tmp_path, unsaved):
    tab = win.open_flow(flow_file(tmp_path / "load.yaml", {"query": QUERY}))
    settle()
    if unsaved:
        tab.doc.move_blocks({"query": (30, 20)})  # the block was dragged: the flow has unsaved changes
        settle()
    dirty = tab.doc.dirty
    click(tab, tab.scene.nodes["query"])
    chosen = showing(win), win.properties.block_id

    wrong = edit_on(win, tab, win.parameters, parameter_edits(win, tab))
    win.right_tabs.setCurrentWidget(win.properties)
    clean(win)

    assert (dirty, chosen) == (unsaved, ("Properties", "query"))  # its Properties, as you chose it
    assert wrong == [], f"after these edits on the Parameters tab, another tab showed or the selection changed: {wrong}"
    assert win.properties.block_id == "query"  # Properties still shows the selected block


def test_editing_parameters_stays_on_parameters_whatever_kind_of_block_is_selected(win):
    tab = win.new_flow()
    ids = {}
    for n, (type_id, cls) in enumerate(win.registry.blocks.items()):
        ids[type_id] = tab.doc.add_block(type_id, (n % 5) * 400, (n // 5) * 400, cls.new_config)
    settle()

    wrong = {}  # kind of block -> the steps after which another tab showed (or the selection changed)
    for type_id, block_id in ids.items():
        tab.scene.clearSelection()
        tab.scene.nodes[block_id].setSelected(True)
        settle()
        steps = [] if (showing(win), win.properties.block_id) == ("Properties", block_id) else ["selecting it"]
        edits = parameter_edits(win, tab)
        steps += [f"parameter {w[0]}" for w in edit_on(win, tab, win.parameters, [edits[0], edits[1], edits[-1]])]
        description = ("description", lambda b=block_id: type_in(win.flow_settings.description, f"with {b}"),
                       lambda b=block_id: tab.doc.data.get("description") == f"with {b}")
        steps += [f"flow {w[0]}" for w in edit_on(win, tab, win.flow_settings, [description])]
        if steps:
            wrong[type_id] = steps
    clean(win)

    assert "db.run_query" in ids  # Run Query among every kind of block there is
    assert wrong == {}, f"with these blocks selected, an edit on Parameters or Flow showed another tab: {wrong}"


def test_editing_parameters_with_a_text_selected_stays_on_parameters(win, tmp_path):
    tab = win.open_flow(flow_file(tmp_path / "load.yaml", {"query": QUERY}, {"note_1": NOTE}))
    settle()
    click(tab, tab.scene.note_items["note_1"])
    chosen = showing(win), win.properties.note_id

    wrong = edit_on(win, tab, win.parameters, parameter_edits(win, tab))
    win.right_tabs.setCurrentWidget(win.properties)
    clean(win)

    assert chosen == ("Properties", "note_1")
    assert wrong == [], f"with the text selected, these edits on the Parameters tab showed another tab: {wrong}"
    assert win.properties.note_id == "note_1"


# --- the Flow tab ---------------------------------------------------------------------------

@pytest.mark.parametrize("chosen", ["block", "text"])
def test_every_edit_on_the_flow_tab_stays_on_the_flow_tab(win, tmp_path, chosen):
    tab = win.open_flow(flow_file(tmp_path / "load.yaml", {"query": QUERY}, {"note_1": NOTE}))
    settle()
    click(tab, tab.scene.nodes["query"] if chosen == "block" else tab.scene.note_items["note_1"])
    before = showing(win), tab.doc.dirty  # the first edit is on a saved flow, the others on unsaved changes

    wrong = edit_on(win, tab, win.flow_settings, flow_edits(win, tab))
    win.right_tabs.setCurrentWidget(win.properties)
    clean(win)

    assert before == ("Properties", False)
    assert wrong == [], f"with the {chosen} selected, these edits on the Flow tab showed another tab: {wrong}"
    assert (win.properties.block_id, win.properties.note_id) == (
        ("query", None) if chosen == "block" else (None, "note_1"))


# --- choosing on purpose still shows Properties ---------------------------------------------------

def test_clicking_a_block_or_a_text_on_the_canvas_shows_its_properties_from_parameters_and_flow(win, tmp_path):
    tab = win.open_flow(flow_file(tmp_path / "load.yaml", {"query": QUERY, "report": REPORT}, {"note_1": NOTE}))
    settle()
    click(tab, tab.scene.nodes["query"])
    seen = []

    def shown_after(panel, edit, item):
        """Make `edit` on `panel`'s tab, then click `item()` on the canvas: what the Inspector shows."""
        win.right_tabs.setCurrentWidget(panel)
        edit()
        settle()
        click(tab, item())  # (the canvas is drawn again after an edit: its items are new)
        seen.append((showing(win), win.properties.block_id, win.properties.note_id, selected(tab)))

    shown_after(win.parameters, lambda: button(win.parameters, "Add").click(), lambda: tab.scene.nodes["report"])
    shown_after(win.flow_settings, lambda: type_in(win.flow_settings.name, "nightly"),
                lambda: tab.scene.note_items["note_1"])
    shown_after(win.parameters, lambda: None, lambda: tab.scene.nodes["query"])
    clean(win)

    assert seen == [("Properties", "report", None, (["report"], [])),
                    ("Properties", None, "note_1", ([], ["note_1"])),
                    ("Properties", "query", None, (["query"], []))]


def test_pasted_blocks_and_an_added_text_show_their_properties_from_the_parameters_tab(win, tmp_path):
    tab = win.open_flow(flow_file(tmp_path / "load.yaml", {"query": QUERY}))
    settle()
    click(tab, tab.scene.nodes["query"])
    win.copy_blocks()  # Edit > Copy
    win.right_tabs.setCurrentWidget(win.parameters)

    win.paste_blocks()  # Edit > Paste: the copy is selected
    settle()
    pasted = selected(tab), showing(win), win.properties.block_id

    win.right_tabs.setCurrentWidget(win.parameters)
    win.add_text(tab)  # Edit > Add text: the new text is selected, to write it in Properties
    settle()
    added = selected(tab), showing(win), win.properties.note_id
    clean(win)

    copy = next(b for b in tab.doc.blocks if b != "query")
    (note,) = tab.doc.notes
    assert pasted == (([copy], []), "Properties", copy)
    assert added == (([], [note]), "Properties", note)
