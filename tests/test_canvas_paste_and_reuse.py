"""Three canvas fixes, through the real editor: copying and pasting texts (alone or with blocks, in the same flow
or another tab), running a block you added after a run with the results its inputs show from that run, and the
zoom control (typing the zoom, the step box's arrows, larger + and -)."""

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics, QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDockWidget, QProxyStyle, QStyle, QStyleFactory,  # noqa: E402
                               QStyleOptionSpinBox)

from tests.test_lines import menu_action, shown, wait_runs  # noqa: E402
from tests.test_paths_editor_editing import clean, flow_file, menu, settle  # noqa: E402
from tests.test_paths_editor_running import MANUAL, right_click  # noqa: E402
from tests.test_zoom import assert_shows, click, click_arrow, scene_centre, settled, wheel  # noqa: E402


# --- 1. copying and pasting texts ------------------------------------------------------------

NOTE = {"text": "Nightly load\nruns at 7:00 · é ✓", "x": 120, "y": -160, "style": "heading", "bold": True,
        "italic": True, "underline": True, "font": "Courier New", "size": 18.5, "width": 220}
LOAD = {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}, "ui": {"x": 0, "y": 0}}


def noted_flow(path):
    return flow_file(path, {"start": {**MANUAL, "ui": {"x": -250, "y": 0}}, "load": LOAD},
                     ["start.start -> load.after"], notes={"note_1": NOTE})


def select(tab, blocks=(), notes=()):
    tab.scene.clearSelection()
    for block in blocks:
        tab.scene.nodes[block].setSelected(True)
    for note in notes:
        tab.scene.note_items[note].setSelected(True)
    settle()


def style_of(note: dict) -> dict:
    """A text's words and look: all of it but where it is."""
    return {k: v for k, v in note.items() if k not in ("x", "y")}


def moved(before: dict, after: dict) -> tuple:
    return after["x"] - before["x"], after["y"] - before["y"]


def test_a_text_alone_is_copied_and_pasted_from_the_edit_menu(win, tmp_path):
    """Spec 1: a selected note, Edit > Copy then Edit > Paste: a copy with a new id, the same text and style,
    offset, selected; one Undo removes each paste."""
    tab = win.open_flow(noted_flow(tmp_path / "noted.yaml"))
    QApplication.clipboard().setText("")
    original = copy.deepcopy(tab.doc.data)
    select(tab, notes=["note_1"])

    menu(win, "Copy").trigger()
    menu(win, "Paste").trigger()
    settle()

    assert len(tab.doc.notes) == 2, tab.doc.notes  # a copy was added
    (pasted,) = set(tab.doc.notes) - {"note_1"}
    assert style_of(tab.doc.notes[pasted]) == style_of(NOTE)
    assert moved(NOTE, tab.doc.notes[pasted]) != (0, 0)  # not on top of the original
    assert tab.doc.notes["note_1"] == NOTE and tab.doc.blocks == original["blocks"]
    assert tab.doc.data["edges"] == original["edges"]
    item = tab.scene.note_items[pasted]
    assert item.toPlainText() == NOTE["text"] and item.font().italic() and item.font().bold()
    assert tab.scene.selected_note_ids() == [pasted] and tab.scene.selected_block_ids() == []  # the copy, to move it
    after_first = copy.deepcopy(tab.doc.data)

    menu(win, "Paste").trigger()  # again: another copy
    settle()

    assert len(set(tab.doc.notes)) == 3 and style_of(tab.doc.notes[tab.scene.selected_note_ids()[0]]) == style_of(NOTE)
    menu(win, "Undo").trigger()  # one step per paste
    settle()
    assert tab.doc.data == after_first
    menu(win, "Undo").trigger()
    settle()
    assert tab.doc.data == original and set(tab.scene.note_items) == {"note_1"}
    clean(win)


def test_ctrl_c_and_ctrl_v_copy_blocks_and_texts_selected_together(win, qtbot, tmp_path):
    """Spec 1: notes selected together with blocks, Ctrl+C then Ctrl+V on the canvas: copies of both, the notes
    offset as the blocks are, all selected; one Undo removes the whole paste."""
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    tab = win.open_flow(noted_flow(tmp_path / "together.yaml"))
    QApplication.clipboard().setText("")
    original = copy.deepcopy(tab.doc.data)
    select(tab, blocks=["load"], notes=["note_1"])
    tab.view.setFocus()

    QTest.keySequence(tab.view, QKeySequence(QKeySequence.Copy))  # Ctrl+C (on a Mac, Cmd+C)
    QTest.keySequence(tab.view, QKeySequence(QKeySequence.Paste))
    settle()

    new_blocks = set(tab.doc.blocks) - set(original["blocks"])
    new_notes = set(tab.doc.notes) - {"note_1"}
    assert len(new_blocks) == 1 and len(new_notes) == 1, (tab.doc.blocks, tab.doc.notes)
    (block,), (note,) = new_blocks, new_notes
    assert tab.doc.blocks[block]["config"] == LOAD["config"]
    assert style_of(tab.doc.notes[note]) == style_of(NOTE)
    block_offset = moved(LOAD["ui"], tab.doc.blocks[block]["ui"])
    assert block_offset != (0, 0) and moved(NOTE, tab.doc.notes[note]) == block_offset  # moved alike
    assert tab.scene.selected_block_ids() == [block] and tab.scene.selected_note_ids() == [note]

    QTest.keySequence(tab.view, QKeySequence(QKeySequence.Undo))  # one step takes back the whole paste
    settle()
    assert tab.doc.data == original
    assert set(tab.scene.nodes) == set(original["blocks"]) and set(tab.scene.note_items) == {"note_1"}
    clean(win)


def test_texts_and_blocks_pasted_into_another_flow(win, tmp_path):
    """Spec 1: copied in one flow, pasted in another tab: the text arrives there with its text and style, under an
    id that does not clash with the texts already there; the flow it came from is unchanged."""
    source = win.open_flow(noted_flow(tmp_path / "source.yaml"))
    QApplication.clipboard().setText("")
    original = copy.deepcopy(source.doc.data)
    select(source, blocks=["load"], notes=["note_1"])
    menu(win, "Copy").trigger()
    target = win.new_flow()  # (a new flow has no texts yet)
    settle()
    assert win.current is target

    menu(win, "Paste").trigger()
    settle()

    assert set(target.doc.blocks) == {"start", "load"}
    assert len(target.doc.notes) == 1, target.doc.notes
    (first,) = target.doc.notes
    assert style_of(target.doc.notes[first]) == style_of(NOTE)
    assert set(target.scene.selected_note_ids()) == {first} and target.scene.selected_block_ids() == ["load"]
    target.doc.add_note(300, 300, "kept")  # a text of its own
    win.tabs.setCurrentWidget(source)
    select(source, notes=["note_1"])
    menu(win, "Copy").trigger()
    win.tabs.setCurrentWidget(target)
    settle()

    menu(win, "Paste").trigger()
    settle()

    assert len(target.doc.notes) == 3, target.doc.notes  # a new id: the ones there are kept
    assert sorted(n["text"] for n in target.doc.notes.values()) == sorted([NOTE["text"], NOTE["text"], "kept"])
    assert source.doc.data == original and not source.doc.dirty
    clean(win)


# --- 2. a new block uses the results of the latest run -------------------------------------------

def loaded_flow(tmp_path, name):
    """A flow whose load block counts its runs in loads.txt; run once from the editor."""
    return flow_file(tmp_path / name, {
        "manual": MANUAL,
        "load": {"type": "logic.python", "config": {
            "inputs": [], "code": "open('loads.txt', 'a').write('x'); output = 21"}},
    }, ["manual.start -> load.after"])


def test_a_block_added_after_a_run_runs_alone_with_the_inputs_its_input_blocks_show(win, qtbot, tmp_path):
    """Spec 2, the user's scenario: after a run, add a block connected from one that shows a result; its "with the
    same inputs" items are enabled and name that run; running it uses that result (load does not run again) and the
    block then shows its own result."""
    tab = win.open_flow(loaded_flow(tmp_path, "added.yaml"))
    win.run_flow(tab)
    first = wait_runs(qtbot, tab, 1)
    added = tab.doc.add_block("logic.python", 300, 0, {
        "inputs": ["input"], "outputs": [], "code": "open('added.txt', 'w').write(str(input * 2))"})
    tab.doc.connect("load", "output", added, "input")
    settle()
    assert shown(tab, [added]) == {added: (None, None)}  # it shows no result of its own
    for item in ("Run only this block, with the same inputs", "Run from this block on, with the same inputs"):
        action = menu_action(win, tab, added, item)
        assert action.isEnabled() and f"(run #{first} · " in action.text(), action.text()

    items = right_click(qtbot, win, tab, added, "title", "Run only this block, with the same inputs")

    assert any(i.startswith(f"Run only this block, with the same inputs (run #{first} · ") for i in items), items
    second = wait_runs(qtbot, tab, 2)
    assert tab.runs[-1].status == "success"
    assert (tmp_path / "added.txt").read_text(encoding="utf-8") == "42"
    assert (tmp_path / "loads.txt").read_text(encoding="utf-8") == "x"  # not run again
    assert shown(tab, ["load", added]) == {"load": ("success", first), added: ("success", second)}
    assert not tab.doc.dirty  # the new block was saved before it ran


def test_blocks_added_after_a_run_run_from_the_first_of_them_on_with_its_inputs_results(win, qtbot, tmp_path):
    """Spec 2: "Run from this block on, with the same inputs" on a new block whose input block shows a result: it and
    the blocks after it run, the blocks before it do not. A new block whose input block shows nothing stays
    disabled."""
    tab = win.open_flow(loaded_flow(tmp_path, "onwards.yaml"))
    win.run_flow(tab)
    first = wait_runs(qtbot, tab, 1)
    compute = tab.doc.add_block("logic.python", 300, 0, {"inputs": ["input"], "code": "output = input + 1"})
    report = tab.doc.add_block("logic.python", 600, 0, {
        "inputs": ["input"], "outputs": [], "code": "open('report.txt', 'w').write(str(input))"})
    tab.doc.connect("load", "output", compute, "input")
    tab.doc.connect(compute, "output", report, "input")
    settle()
    # report's input block (compute) shows nothing yet: nothing to take its inputs from
    assert not menu_action(win, tab, report, "Run only this block, with the same inputs").isEnabled()
    onwards = menu_action(win, tab, compute, "Run from this block on, with the same inputs")
    assert onwards.isEnabled() and f"(run #{first} · " in onwards.text(), onwards.text()

    onwards.trigger()
    second = wait_runs(qtbot, tab, 2)

    assert tab.runs[-1].status == "success"
    assert (tmp_path / "report.txt").read_text(encoding="utf-8") == "22"
    assert (tmp_path / "loads.txt").read_text(encoding="utf-8") == "x"  # not run again
    assert shown(tab, ["load", compute, report]) == {"load": ("success", first), compute: ("success", second),
                                                     report: ("success", second)}


def test_a_new_block_uses_the_most_recent_run_among_its_input_blocks_results(win, qtbot, tmp_path):
    """Spec 2: the input blocks show results of different runs (one was re-run alone): the new block's items name,
    and its run uses, the most recent of them."""
    flow = flow_file(tmp_path / "recent.yaml", {
        "manual": MANUAL,
        "a": {"type": "logic.python", "config": {"inputs": [], "code": "open('a.txt', 'a').write('x'); output = 'a'"}},
        "b": {"type": "logic.python", "config": {
            "inputs": [], "code": "open('b.txt', 'a').write('x'); output = len(open('b.txt').read())"}},
    }, ["manual.start -> a.after", "manual.start -> b.after"])
    tab = win.open_flow(flow)
    win.run_flow(tab)
    first = wait_runs(qtbot, tab, 1)
    menu_action(win, tab, "b", "Run only this block, with the same inputs").trigger()  # b again: it now gives 2
    second = wait_runs(qtbot, tab, 2)
    assert shown(tab, ["a", "b"]) == {"a": ("success", first), "b": ("success", second)}
    both = tab.doc.add_block("logic.python", 300, 0, {
        "inputs": ["x", "y"], "outputs": [], "code": "open('both.txt', 'w').write(f'{x},{y}')"})
    tab.doc.connect("a", "output", both, "x")
    tab.doc.connect("b", "output", both, "y")
    settle()
    for item in ("Run only this block, with the same inputs", "Run from this block on, with the same inputs"):
        action = menu_action(win, tab, both, item)
        assert action.isEnabled() and f"(run #{second} · " in action.text(), action.text()

    menu_action(win, tab, both, "Run only this block, with the same inputs").trigger()
    third = wait_runs(qtbot, tab, 3)

    assert tab.runs[-1].status == "success"
    assert (tmp_path / "both.txt").read_text(encoding="utf-8") == "a,2"  # b's result of the later run
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "x" and (tmp_path / "b.txt").read_text() == "xx"
    assert shown(tab, [both]) == {both: ("success", third)}


def test_with_nothing_shown_for_a_block_or_its_input_blocks_the_same_inputs_items_stay_disabled(win, qtbot, tmp_path):
    """Spec 2, as today: a new block connected to nothing, or only to a block that shows no result (it is off),
    has nothing to take its inputs from."""
    flow = flow_file(tmp_path / "nothing.yaml", {
        "manual": MANUAL,
        "load": {"type": "logic.python", "config": {"inputs": [], "code": "output = 1"}},
        "spare": {"type": "logic.python", "config": {"inputs": [], "code": "output = 2"}},  # off: no start block
    }, ["manual.start -> load.after"])
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    assert shown(tab, ["load", "spare"])["spare"] == ("off", None)
    lonely = tab.doc.add_block("logic.python", 300, 0, {"inputs": [], "outputs": [], "code": "pass"})
    after_spare = tab.doc.add_block("logic.python", 300, 200, {"inputs": ["input"], "outputs": [], "code": "pass"})
    tab.doc.connect("spare", "output", after_spare, "input")
    settle()

    for block in (lonely, after_spare):
        for item in ("Run only this block, with the same inputs", "Run from this block on, with the same inputs"):
            action = menu_action(win, tab, block, item)
            assert not action.isEnabled() and "(run #" not in action.text(), (block, action.text())
    clean(win)


# --- 3. the zoom control ----------------------------------------------------------------------

def canvas_ready(win, qtbot):
    """A new flow on a canvas that has the window to itself (the panels float, as in test_zoom.py: the Windows
    runner's screen is small), the window active, as when you click in it."""
    for dock in win.findChildren(QDockWidget):
        dock.setFloating(True)
    tab = settled(win.new_flow())
    win.activateWindow()
    qtbot.waitUntil(win.isActiveWindow, timeout=5_000)
    QApplication.processEvents()
    return tab


def type_zoom(view, text: str):
    """Click the percentage (which selects what it shows), type `text` (for "", Backspace deletes the selected
    value), press Enter."""
    bar = view.zoom_bar
    QApplication.processEvents()
    QTest.mouseClick(bar.percent, Qt.LeftButton)
    field = QApplication.focusWidget()
    if field is None or not bar.isAncestorOf(field):
        field = bar.percent
    QTest.keyClicks(field, text) if text else QTest.keyClick(field, Qt.Key_Backspace)
    QTest.keyClick(field, Qt.Key_Return)
    QApplication.processEvents()


def test_typing_a_zoom_zooms_there_around_the_centre_of_the_visible_canvas(win, qtbot):
    """Spec 3a: click the percentage, type 150 (or 80%), Enter: that zoom, around the middle of the visible canvas;
    the buttons and the wheel go on from it and the field follows them."""
    view = canvas_ready(win, qtbot).view
    bar = view.zoom_bar
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 250)  # look somewhere off the flow
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 150)
    QTest.mouseMove(view.viewport(), QPoint(40, 40))  # the mouse passed over the canvas on its way
    middle = view.viewport().rect().center()

    for text, expected in (("150", 150), ("80%", 80)):
        centre = scene_centre(view)
        type_zoom(view, text)
        assert_shows(view, expected)
        now = view.mapFromScene(centre)  # where the point that was in the middle is now
        assert abs(now.x() - middle.x()) <= 2 and abs(now.y() - middle.y()) <= 2, (text, middle, now)

    click(bar.plus)
    assert_shows(view, 85)
    wheel(view, up=True)
    assert_shows(view, 85 * 1.15)  # 97.75: "98%"


def test_a_typed_zoom_outside_20_to_300_percent_is_set_to_the_nearest_limit(win, qtbot):
    """Spec 3a: a value outside 20-300 is set to the nearest limit."""
    view = canvas_ready(win, qtbot).view
    for text, expected in (("1000", 300), ("5", 20), ("400%", 300)):
        type_zoom(view, text)
        assert_shows(view, expected)


def test_typing_text_that_is_not_a_number_keeps_the_zoom_and_shows_it_again(win, qtbot):
    """Spec 3a: text that is not a number restores the current value."""
    view = canvas_ready(win, qtbot).view
    type_zoom(view, "150")
    assert_shows(view, 150)
    for text in ("abc", ""):
        type_zoom(view, text)
        assert_shows(view, 150)


class SideBySideArrows(QProxyStyle):
    """Lays a spin box's arrows out side by side at its right, full height, as the Windows 11 style does (the style
    the editor gets on Windows; it is not available elsewhere)."""

    def subControlRect(self, control, option, sub, widget=None):
        if control == QStyle.CC_SpinBox:
            r, w = option.rect, 16
            if sub == QStyle.SC_SpinBoxUp:
                return QRect(r.right() - 2 * w + 1, r.top(), w, r.height())
            if sub == QStyle.SC_SpinBoxDown:
                return QRect(r.right() - w + 1, r.top(), w, r.height())
            if sub == QStyle.SC_SpinBoxEditField:
                return QRect(r.left(), r.top(), r.width() - 2 * w, r.height())
        return super().subControlRect(control, option, sub, widget)


def assert_arrows_stacked_on_the_right(spin, why: str):
    QApplication.processEvents()
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    up, down, number = (spin.style().subControlRect(QStyle.CC_SpinBox, option, part, spin)
                        for part in (QStyle.SC_SpinBoxUp, QStyle.SC_SpinBoxDown, QStyle.SC_SpinBoxEditField))
    assert not up.isEmpty() and not down.isEmpty(), (why, up, down)
    assert spin.rect().contains(up) and spin.rect().contains(down), (why, spin.rect(), up, down)
    assert up.bottom() <= down.top() + 1, (why, up, down)  # up above down
    shared = min(up.right(), down.right()) - max(up.left(), down.left())
    assert shared >= min(up.width(), down.width()) / 2, (why, up, down)  # one column
    assert min(up.left(), down.left()) >= number.right() - 1, (why, number, up, down)  # right of the number
    before = spin.value()
    click_arrow(spin, QStyle.SC_SpinBoxUp)
    assert spin.value() == before + 1, why
    click_arrow(spin, QStyle.SC_SpinBoxDown)
    assert spin.value() == before, why


def test_the_step_boxs_arrows_are_stacked_on_its_right(win, qtbot):
    """Spec 3b: up above down on the step box's right, like a standard number spin box, also with a style that
    lays them side by side (Windows 11's, which the user gets)."""
    step = canvas_ready(win, qtbot).view.zoom_bar.step
    assert_arrows_stacked_on_the_right(step, f"style {QApplication.style().name()}")
    original = QApplication.style().name()
    styles = [("side by side", lambda: SideBySideArrows(QStyleFactory.create("Fusion")))]
    if "windows11" in [k.lower() for k in QStyleFactory.keys()]:
        styles.append(("windows11", lambda: QStyleFactory.create("windows11")))
    kept = []  # (each style is the application's while it is used)
    try:
        for name, make in styles:
            kept.append(make())
            QApplication.setStyle(kept[-1])
            assert_arrows_stacked_on_the_right(step, f"style {name}")
    finally:
        QApplication.setStyle(QStyleFactory.create(original))
        QApplication.processEvents()


def test_plus_and_minus_are_larger_and_bold(win, qtbot):
    """Spec 3c: + and - are clearly visible: bold, larger than the usual text of a button, and not cut off."""
    bar = canvas_ready(win, qtbot).view.zoom_bar
    QApplication.processEvents()
    for button in (bar.plus, bar.minus):
        font = button.font()
        assert font.bold(), button.text()
        assert font.pointSizeF() > QApplication.font(button).pointSizeF(), button.text()
        metrics = QFontMetrics(font)
        assert metrics.horizontalAdvance(button.text()) <= button.width() and metrics.height() <= button.height(), (
            button.text(), button.size())
