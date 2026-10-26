"""A table's text (its rows laid out in columns) is shown in a fixed-width font wherever the editor shows it, so its
columns line up: hovering it on the canvas, an Inspect block's line in the Log, and a block's results. And the status
bar's "Showing run #N" of one flow's tab is gone once you switch to another flow's tab."""

import os
import textwrap

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontDatabase, QFontInfo, QHelpEvent, QTextDocument  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from taskloom.editor import dialogs  # noqa: E402
from taskloom.history import History  # noqa: E402


def write_flow(path, text):
    path.write_text(textwrap.dedent(text))
    return path


def run_now(win, qtbot, tab):
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=60_000)
    assert tab.last["status"] == "success", tab.last
    return tab.last["id"]


def click_tab(tabs, widget):
    bar = tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(tabs.indexOf(widget)).center())


def hover(tab, qtbot, at):
    """Rest the mouse on the canvas at the scene point `at`: the tooltip it shows."""
    viewport = tab.view.viewport()
    pos = tab.view.mapFromScene(at)
    QApplication.sendEvent(viewport, QHelpEvent(QHelpEvent.ToolTip, pos, viewport.mapToGlobal(pos)))
    qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
    return tooltip()


def tooltip():
    return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)


def not_fixed_width(doc: QTextDocument, table: str) -> list[str]:
    """The fonts (as this platform resolves them) that some of `table`'s rows and borders are drawn in in `doc` and
    that are not fixed-width."""
    lines = table.splitlines()  # its size (e.g. 15 rows × 3 columns), then the table from its top border down
    plain = doc.toPlainText()
    start = plain.find(lines[1])
    end = plain.find(lines[-1], start) + len(lines[-1])
    assert start >= 0 and plain[start:end] == "\n".join(lines[1:]), plain
    fonts = set()
    block = doc.findBlock(start)
    while block.isValid() and block.position() < end:
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.position() < end and fragment.position() + fragment.length() > start:
                info = QFontInfo(fragment.charFormat().font().resolve(doc.defaultFont()))
                if not info.fixedPitch():
                    fonts.add(info.family())
            it += 1
        block = block.next()
    return sorted(fonts)


def as_shown(label) -> QTextDocument:
    """The rich text of a label (a tooltip) laid out as the label lays it out: in the label's font."""
    doc = QTextDocument()
    doc.setDefaultFont(label.font())
    doc.setHtml(label.text())
    return doc


SALES = """
    blocks:
      manual:
        type: trigger.manual
      sales:
        type: logic.python
        config:
          inputs: []
          outputs: [output]
          code: |
            output = pl.DataFrame({"publishing_system_id": [f"PS-{100 + i}" for i in range(1, 16)],
                                   "articles": [i * 37 % 23 + 5 for i in range(1, 16)],
                                   "total_sales": [i * 7919 % 900 + 300 for i in range(1, 16)]})
      look:
        type: logic.inspect
    edges:
      - manual.start -> sales.after
      - sales.output -> look.value
"""


def test_a_tables_rows_are_in_a_fixed_width_font_when_hovering_it_in_the_log_and_in_its_results(
        win, qtbot, tmp_path, monkeypatch):
    if not QFontDatabase.families():
        pytest.skip("Qt has no fonts here (Windows offscreen without QT_QPA_FONTDIR): no font to tell apart")
    tab = win.open_flow(write_flow(tmp_path / "sales-report.yaml", SALES))
    run_id = run_now(win, qtbot, tab)
    history = History(win.home.history_db)
    table = history.blocks(run_id)["sales"]["previews"]["output"]
    history.close()
    assert "15 rows × 3 columns" in table and "PS-115" in table, table

    sales = tab.scene.nodes["sales"]
    output, look_input = sales.ports[("output", True)], tab.scene.nodes["look"].ports[("value", False)]
    [edge] = [e for e in tab.scene.edge_items if e.src_port is output]
    hovered = {"sales' output": (output, output.sceneBoundingRect().center()),
               "look's input": (look_input, look_input.sceneBoundingRect().center()),
               "the connection": (edge, edge.mapToScene(edge.path().pointAtPercent(0.5))),
               "the result under sales": (sales.footer, sales.footer.sceneBoundingRect().center())}
    proportional = {}
    for where, (item, at) in hovered.items():
        tip = hover(tab, qtbot, at)
        assert tip.text() == item.toolTip(), (where, tip.text())  # the tooltip of what was hovered
        proportional[where] = not_fixed_width(as_shown(tip), table)
        tip.hide()

    # An Inspect block shows the table in the Log.
    proportional["the Log"] = not_fixed_width(win.log.text.document(), table)

    def look_at(dialog):  # Results of this block…: the selected run's values, under its runs
        dialog.show()
        QApplication.processEvents()
        [values] = dialog.findChildren(QPlainTextEdit)
        proportional["sales' results"] = not_fixed_width(values.document(), table)
        dialog.close()
        return False

    monkeypatch.setattr(dialogs.BlockResultsDialog, "exec", look_at)
    win.show_block_results(tab, "sales")

    assert {where: fonts for where, fonts in proportional.items() if fonts} == {}, proportional


FLOW = """
    blocks:
      manual:
        type: trigger.manual
      work:
        type: logic.python
        config: {inputs: [], outputs: [], code: "print('working')"}
    edges:
      - manual.start -> work.after
"""


def test_switching_to_another_flows_tab_clears_showing_a_run_of_the_flow_you_left(win, qtbot, tmp_path):
    report = win.open_flow(write_flow(tmp_path / "daily-report.yaml", FLOW))
    clean = win.open_flow(write_flow(tmp_path / "clean-up.yaml", FLOW))
    click_tab(win.tabs, report)
    assert win.current is report
    run_id = run_now(win, qtbot, report)

    click_tab(win.bottom_tabs, win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 1, timeout=10_000)
    at = table.visualItemRect(table.item(0, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # show that run
    assert win.statusBar().currentMessage().startswith(f"Showing run #{run_id} · "), win.statusBar().currentMessage()

    click_tab(win.tabs, clean)

    assert win.current is clean
    assert "Showing run" not in win.statusBar().currentMessage(), win.statusBar().currentMessage()
