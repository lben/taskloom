"""Proof: documentation inside the app. Resting the mouse on a row of Properties shows a card (what it is for,
examples, Learn more); Learn more opens a Documentation column at the far right, next to the canvas and Properties."""

from urllib.parse import urldefrag

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from proofs.conftest import choose, menu_item
from taskloom.block import fields, setting_label
from taskloom.editor import docs
from taskloom.editor.canvas import HEADER_H, NODE_W
from tests.test_docs_help import (CONNECTION_OF_KIND, CONNECTIONS, LEARN_MORE, NO_EXAMPLES, NO_HELP, Pages,
                                  add_block, anchor_at, card_lines, check_rows, clean_all, click_learn_more,
                                  click_link, connect_into, examples_of_field, heading_at_top, hover,
                                  label_is_bold, learn_more_href, listed, nav_button, needs_examples, page_and_anchor,
                                  params_used, plain_line, problems_in_flow, property_rows, readme_parts, row_named,
                                  setting_row, type_in_row, wait_for)
from tests.test_docs_help import choose as choose_in_box
from tests.test_editor import write_flow
from tests.test_paths_editor_editing import error_shown

FLOW = """
    blocks:
      every_morning:
        type: trigger.schedule
        config: {cron: "30 7-17/2 * * MON-FRI"}
        ui: {x: 0, y: 0}
      sales:
        type: data.read_file
        config: {path: data/sales.csv}
        ui: {x: 240, y: 0}
      chart:
        type: reports.chart
        config: {kind: bar, x: region, y: [amount], title: Sales by region}
        ui: {x: 480, y: 0}
      body:
        type: reports.html_template
        config: {template: "<b>Daily sales</b><br>{chart}<br>{sales}"}
        ui: {x: 720, y: 0}
      archive_sales:
        type: files.copy
        config: {source: data/sales.csv, destination: C:/archive/}
        ui: {x: 240, y: 190}
    edges:
      - every_morning.start -> sales.after
      - sales.table -> chart.table
      - chart.image -> body.data
      - sales.table -> body.data
      - every_morning.start -> archive_sales.after
"""
# A block of your own, in your blocks folder: one setting with help and examples, one without.
TALLY = '''
from taskloom import Block, fields, ports


class Tally(Block):
    type_id = "my.tally"
    title = "Tally"
    description = "Counts the rows of a table."
    category = "My Tools"
    inputs = {"table": ports.Table()}
    outputs = {"count": ports.Any()}
    config = {
        "column": fields.Text(required=False, help="Count only the rows where this column has a value",
                              examples=["amount", "region"]),
        "caption": fields.Text(default="rows"),
    }

    def run(self, ctx, table):
        return {"count": len(table)}
'''


@pytest.fixture
def blocks_folder(tmp_path):
    """Your blocks folder, with tally.py in it, before the editor starts (it finds your blocks as it starts)."""
    folder = tmp_path / "home" / "blocks"  # in the Taskloom folder of the win fixture's editor
    folder.mkdir(parents=True)
    (folder / "tally.py").write_text(TALLY, encoding="utf-8")
    return folder


@pytest.fixture
def win(blocks_folder, win):
    """The editor, started once your block is in your blocks folder."""
    assert win.home.blocks_dir == blocks_folder
    return win


def open_flow(win, monkeypatch, path):
    """File > Open…, choosing `path` in the file dialog: the tab it opens in."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    menu_item(file_menu, "Open…").trigger()
    QApplication.processEvents()
    return win.current


def click_block(win, block: str):
    """Click a block's title bar on the canvas, as you would."""
    tab = win.current
    at = tab.view.mapFromScene(tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2)))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()
    assert win.properties.block_id == block, f"{block} is not the block Properties shows"


def in_window(win, widget, rect) -> QRect:
    """`rect` of `widget` in the window's pixels (of a screenshot of the window, or of something over it)."""
    return QRectF(rect).toAlignedRect().translated(widget.mapToGlobal(QPoint(0, 0)) - win.mapToGlobal(QPoint(0, 0)))


def card_parts(win, card) -> list[QRect]:
    """Where the card shows ① what the row is for, ② its line of valid values and ③ Learn more ›, each as the
    rectangle to outline (the outline goes 6 pixels around it), in the window's pixels. Each outline is on the edges
    of its lines, so the outlines of parts next to each other cover neither, and runs past the card's right edge, so
    its badge covers no text. The card lays its text out as this document does."""
    doc = QTextDocument()
    doc.setDefaultFont(card.font())
    doc.setDocumentMargin(0)
    doc.setHtml(card.text())
    inner = card.contentsRect().adjusted(card.margin(), card.margin(), -card.margin(), -card.margin())
    doc.setTextWidth(inner.width())
    breaks = [i for i in range(doc.characterCount()) if doc.characterAt(i) in "\u2028\u2029"]  # its line breaks
    learn_more = doc.find(LEARN_MORE).selectionStart()
    before = [i for i in breaks if i < learn_more]
    # The label, a break, what it is for, a break, the valid values (one or more lines), a break, Learn more ›.
    parts = [(breaks[0] + 1, breaks[1]), (breaks[1] + 1, before[-1]), (learn_more, doc.characterCount() - 1)]
    right = card.contentsRect().right() + 14
    found = []
    for start, end in parts:
        top = bottom = None
        block = doc.begin()
        while block.isValid():
            at = doc.documentLayout().blockBoundingRect(block).top()
            layout = block.layout()
            for i in range(layout.lineCount()):
                line = layout.lineAt(i)
                begins = block.position() + line.textStart()
                if begins < end and begins + max(line.textLength(), 1) > start:
                    top = at + line.y() if top is None else top
                    bottom = at + line.y() + line.height()
            block = block.next()
        assert top is not None, f"the card has no part from {start} to {end}: {doc.toPlainText()!r}"
        outline = QRectF(inner.left() - 3, inner.top() + top, right - inner.left() + 3, bottom - top)
        found.append(in_window(win, card, outline.adjusted(6, 6, -6, -6)))
    return found


def lines(base, browser, start: QTextCursor, end: QTextCursor) -> QRect:
    """The lines of the column's page from `start`'s to `end`'s, the width of the page, in the pixels of `base`
    (the window, or the column)."""
    top, bottom = browser.cursorRect(start), browser.cursorRect(end)
    viewport = browser.viewport()
    rect = QRect(4, top.top() - 4, viewport.width() - 8, bottom.bottom() - top.top() + 8) & viewport.rect()
    assert not rect.isEmpty(), "those lines are not in view"
    return in_window(base, viewport, rect)


def entry(base, browser, anchor: str, until: str) -> QRect:
    """The entry of the column's page from its heading (`anchor`) to the line before the next entry (`until`)."""
    start, end = anchor_at(browser, anchor), anchor_at(browser, until)
    assert start is not None and end is not None, (anchor, until)
    end.movePosition(QTextCursor.PreviousCharacter)
    return lines(base, browser, start, end)


def text_lines(base, browser, first: str, last: str) -> QRect:
    """The lines of the column's page from the one that shows `first` to the one after it that shows `last`."""
    start = browser.document().find(first, 0, QTextDocument.FindCaseSensitively)
    end = browser.document().find(last, start, QTextDocument.FindCaseSensitively)
    assert not start.isNull() and not end.isNull(), (first, last)
    return lines(base, browser, start, end)


def shown_at(browser) -> tuple[str, str]:
    """The page and the entry the column shows."""
    return page_and_anchor(browser.source().toString())


def card_says(card) -> tuple[str, str, str, str]:
    """The card's label, what it is for, its line of valid values and its last line."""
    lines = card_lines(card)
    return lines[0], lines[1], "\n".join(lines[2:-1]), lines[-1]


def test_documentation_inside_the_app(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Documentation inside the app: a card on every setting, and a Documentation column",
              "Documentation: When hover over, every field on the properties panel should show 1. what is it for, "
              "2. examples of valid values, 3. a \"learn more\" link pointing to its documentation on the main docs. "
              "... Ideally it could be open within the app itself so that I can read/remember what any section does "
              "without losing focus of what I am doing so that I can have the canvas, properties, etc visible and "
              "the documentation open maybe to the far-rigtht as a column so that the work and the documentation all "
              "of it is on sight")
    p.idea("Rest the mouse on any row of the Properties tab: a card says what the row is for, gives examples of "
           "valid values, and its Learn more › opens the row's entry in a Documentation column at the far right of "
           "the window, with the canvas and Properties still in view.")
    p.idea("The card shows the row's name in bold, what it is for, then one line of valid values: “Examples:” with "
           "one to three values written as you would type them, or “One of:” with the choices of a box, or, for a "
           "connection, which of your connections it takes. Last comes Learn more ›. The card stays while the mouse "
           "is on the row or on the card, so you can reach the link, and goes when the mouse leaves both, when you "
           "type or press Esc, or when you switch to another program. It never takes the keyboard from the box "
           "you are typing in.")
    p.idea("Help > Documentation, or F1, shows or hides the column. Back, Forward and Contents are at its top. Its "
           "guide pages are the sections of Taskloom's README; its reference pages are made from the blocks "
           "installed, so every block and every setting is there, your own blocks too, as they are in the version "
           "you run. Nothing is fetched from the internet: the column works offline.")
    p.idea("The documentation website you asked for comes in a later change (on GitHub Pages, as you chose). Learn "
           "more never depends on it: it opens the column, which works offline either way.")
    p.why_simplest("Each setting is described once, where the block defines it: its help (what it is for) and its "
                   "examples. The card, the setting's tooltip and its reference entry all read those, so they cannot "
                   "disagree. The rows that are not a block's settings (Name, Retry and timeout, the When and Keep "
                   "runs boxes, the texts on the canvas) are described once, in one table. A box of choices needs no "
                   "examples: its card lists the box's own choices.")
    p.why_simplest("The column is one more panel of the window, like Blocks and the Inspector, and shows plain pages "
                   "with Qt's own text viewer: no web browser inside the app, no server, no internet. The pages are "
                   "made when first needed, from the README and the blocks the editor has, and made again when you "
                   "create a reusable block.")
    p.alternative("Longer tooltips",
                  "A tooltip goes as soon as the mouse leaves the row, or at any click, so a link in it can never be "
                  "clicked. The card is a tooltip that waits for you: it stays while the mouse is on it.")
    p.alternative("Learn more opening the website in your web browser",
                  "You would leave the editor to read it, which is what you wanted to avoid; it would need the "
                  "internet, and could describe a version other than the one you run.")
    p.alternative("A web browser engine inside the app (QtWebEngine)",
                  "About 130 MB more to download, on a 205 MB download, for pages that are text, headings, tables and "
                  "links, which Qt's text viewer already shows.")
    p.alternative("Reference pages written by hand, or by a documentation tool such as Sphinx or MkDocs",
                  "A second copy of what the blocks already say, which would fall behind them, and a build step. "
                  "Made from the blocks as the app runs, the pages also list your own blocks.")
    p.fits("The guide is the README, section by section: the same text you read on GitHub, so there is one guide "
           "to keep up to date.")
    p.fits("The reference is made from the same block definitions that build the Properties tab, so a block of "
           "your own in your blocks folder, a plugin's block and a reusable block you create are documented like "
           "Taskloom's own. A setting that has no help yet says so on its card, and says how to add it.")
    p.fits("The column is a panel like Blocks and the Inspector: drag its edge to resize it, float it (onto a "
           "second screen, say) or close it. It is hidden when the editor starts; while it is hidden, the window "
           "looks as it did before. The card takes the colours of tooltips, so it follows light and dark mode, and "
           "each row's tooltip still says what the row is for, for screen readers.")

    flow = write_flow(tmp_path / "morning-report.yaml", FLOW)

    # --- a text setting: Copy Files' Source -------------------------------------------------------------------
    p.step("Open morning-report.yaml, click archive_sales, and rest the mouse on its Source. Open it with File > "
           "Open…; archive_sales is its Copy Files block, and Source a row of the Properties tab.")
    p.note("The proof rests the mouse on a row as Qt does: it moves the mouse onto the row, then gives the row the "
           "event Qt gives it once the mouse has stayed there for the usual tooltip delay.")
    tab = open_flow(win, monkeypatch, flow)
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    click_block(win, "archive_sales")
    copy_files = win.registry.get("files.copy")
    source = row_named(win, "Source", "", copy_files)
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=3_000)
    source.inputs[0].setFocus()  # you were typing in it
    QApplication.processEvents()
    canvas_before = tab.view.width()
    card = hover(qtbot, win, source.inputs[0], "Source", "Copy Files › Source")
    label, what, values, last = card_says(card)
    parts = card_parts(win, card)
    p.shot(card, "The card, just under Source: its name in bold, ① what it is for, ② examples of valid values, ③ "
                 "Learn more ›.", over=win, highlight=parts, badges=True, zoom=[card, source.label_widget, *parts])
    p.check("The card shows Source in bold", label == "Source" and label_is_bold(card))
    p.check("① It says what Source is for: the help Copy Files gives it", what == copy_files.config["source"].help)
    p.check("② It gives its three examples: " + " · ".join(copy_files.config["source"].examples),
            listed(values, "Examples:") == list(copy_files.config["source"].examples))
    p.check("③ Its last line is Learn more ›", last == LEARN_MORE)
    p.check("The Source box keeps the keyboard: the card took no focus", QApplication.focusWidget() is source.inputs[0])

    # --- Learn more: the column, at the far right -------------------------------------------------------------
    p.step("Click Learn more › on the card.")
    href = click_learn_more(card)
    column, browser = win.docs, win.docs.browser
    wait_for(qtbot, lambda: heading_at_top(browser, "files.copy.source"),
             "Source's entry is not at the top of the column")
    source_entry = entry(win, browser, "files.copy.source", "files.copy.destination_server")
    p.shot(win, "① The canvas and ② the Properties tab are as they were; ③ the Documentation column is open at the "
                "far right, at ④ Source, the entry of the setting you hovered (on the Files page, under Copy Files). "
                "The card has gone.",
           highlight=[tab.view, win.right_tabs, column, source_entry], badges=True, zoom=source_entry)
    p.check("The card closed", not card.isVisible())
    p.check("③ The column is open, docked right of the Inspector, at the window's right edge",
            column.isVisible() and not column.isFloating()
            and column.mapTo(win, QPoint(0, 0)).x() > win.inspector.mapTo(win, QPoint(0, 0)).x()
            and win.width() - column.geometry().right() < 20)
    p.check("① The canvas and ② Properties are still on screen beside it, Properties still showing archive_sales",
            tab.view.isVisible() and tab.view.width() > 200 and win.properties.isVisible()
            and win.properties.block_id == "archive_sales")
    p.check("④ It shows the entry of Copy Files' Source, on the Files page, its heading at the top",
            page_and_anchor(href) == shown_at(browser) == ("blocks-files.html", "files.copy.source")
            and heading_at_top(browser, "files.copy.source"))
    p.note("Offscreen, the proof clicks Learn more with Qt's mouse events: it moves the mouse along the card's last "
           "line until the link lights up, and clicks there. Clicking the card with a real mouse on Windows is not "
           "shown by this proof: it is to be checked by hand once.")

    # --- a choice: Chart's Kind ----------------------------------------------------------------------------------
    p.step("Click the Chart block chart, and rest the mouse on its Kind.")
    click_block(win, "chart")
    chart = win.registry.get("reports.chart")
    kind = row_named(win, "Kind", "", chart)
    card = hover(qtbot, win, kind.inputs[0], "Kind", "Chart › Kind")
    label, what, values, last = card_says(card)
    box = kind.inputs[0]
    items = [box.itemText(i) for i in range(box.count())]
    parts = card_parts(win, card)
    p.shot(card, "A box of choices has no examples: ① its card lists the box's own choices, “One of: bar · barh · "
                 "line · …”. (The column stays where you left it, at Source.)", over=win, highlight=parts[1:2],
           badges=True, zoom=[card, kind.label_widget, *parts])
    p.check("① “One of:” lists exactly the choices of the Kind box: " + " · ".join(items),
            listed(values, "One of:") == items)
    p.check("It says what Kind is for, and ends with Learn more ›",
            what == chart.config["kind"].help and last == LEARN_MORE)

    # --- a row every block has: Retry and timeout's Delay, and its Learn more ----------------------------------
    p.step("Choose fixed in Policy, and rest the mouse on Delay. Both are under Retry and timeout. Then click Learn "
           "more › on its card.")
    row_named(win, "Policy", "Retry and timeout").inputs[0].setCurrentText("fixed")
    QApplication.processEvents()
    delay = row_named(win, "Delay", "Retry and timeout")
    card = hover(qtbot, win, delay.inputs[0], "Delay", "Chart › Retry and timeout › Delay")
    label, what, values, last = card_says(card)
    delay_label, delay_what, delay_examples = docs.ROWS["retry.delay"]
    parts = card_parts(win, card)
    p.shot(card, "A row every block has, which is not one of its settings: ① how long to wait before each new "
                 "attempt, ② three examples.", over=win, highlight=parts[:2], badges=True,
           zoom=[card, delay.label_widget, *parts])
    p.check("① It says what Delay is for", (label, what) == (delay_label, delay_what))
    p.check("② Examples: " + " · ".join(delay_examples), listed(values, "Examples:") == list(delay_examples))

    href = click_learn_more(card)
    wait_for(qtbot, lambda: heading_at_top(browser, "properties.retry.delay"),
             "Delay's entry is not at the top of the column")
    delay_entry = entry(win, browser, "properties.retry.delay", "properties.retry.initial")
    p.shot(win, "After Learn more: ① the column moves to Delay, on the Properties panel page, under Every block, "
                "with the other rows of Retry and timeout. The canvas and Properties stay as they were.",
           highlight=[delay_entry], badges=True, zoom=delay_entry)
    p.check("① The column shows the entry of Delay, on the Properties panel page, its heading at the top",
            page_and_anchor(href) == shown_at(browser) == ("properties.html", "properties.retry.delay")
            and heading_at_top(browser, "properties.retry.delay"))

    # --- the When box: Cron line, and its Learn more -------------------------------------------------------------
    p.step("Click every_morning, and rest the mouse on Cron line. It is the Schedule block, and Cron line a row of "
           "its When box. Then click Learn more › on its card.")
    click_block(win, "every_morning")
    cron = row_named(win, "Cron line", "When")
    card = hover(qtbot, win, cron.inputs[0], "Cron line", "Schedule › When › Cron line")
    label, what, values, last = card_says(card)
    cron_examples = list(docs.ROWS["when.cron"][2])
    parts = card_parts(win, card)
    p.shot(card, "A row of the When box, which sets the Schedule's timing: ① how a cron line is written, ② three "
                 "examples.", over=win, highlight=parts[:2], badges=True, zoom=[card, cron.label_widget, *parts])
    p.check("① It says how a cron line is written", what.startswith("minute hour day month weekday"))
    p.check("② Examples: " + " · ".join(cron_examples), listed(values, "Examples:") == cron_examples)

    href = click_learn_more(card)
    wait_for(qtbot, lambda: heading_at_top(browser, "properties.when.cron"),
             "Cron line's entry is not at the top of the column")
    p.shot(win, "After Learn more: ① the column moves to Cron line, on its Properties panel page, under Schedule: "
                "When, the rows of the When box. The canvas and Properties stay as they were.",
           highlight=[entry(win, browser, "properties.when.cron", "properties.when.minute")], badges=True,
           zoom=entry(win, browser, "properties.when.cron", "properties.when.minute"))
    p.check("① The column shows the entry of Cron line, on the Properties panel page, its heading at the top",
            page_and_anchor(href) == shown_at(browser) == ("properties.html", "properties.when.cron")
            and heading_at_top(browser, "properties.when.cron"))
    p.step("Choose Custom (a cron line) in How often, and type the card's three examples into Cron line.")
    p.note("Choosing Custom keeps the Cron line row while you type: otherwise a line such as 0 7 * * MON-FRI turns "
           "How often into Every weekday at 07:00, which is the same schedule.")
    choose_in_box(row_named(win, "How often", "When").inputs[0], data="custom")
    taken = []
    for example in cron_examples:  # what the card says is valid is
        type_in_row(row_named(win, "Cron line", "When"), example)
        taken.append((tab.doc.blocks["every_morning"]["config"]["cron"], error_shown(win),
                      [m for m in problems_in_flow(win, tab, tmp_path) if "every_morning" in m]))
    when = row_named(win, "Cron line", "When").top
    p.shot(win, f"The last example, {cron_examples[-1]}, in Cron line: ① the When box reads it back in words, with "
                "its next runs.", highlight=[when], badges=True, zoom=[when])
    p.check("Each of the three examples is taken as the schedule's cron line, with no problem shown, and the flow "
            "check says nothing about every_morning", taken == [(example, "", []) for example in cron_examples])

    # --- the HTML Template's Inputs box -----------------------------------------------------------------------
    p.step("Click the HTML Template block body, and rest the mouse on its first input's name, in Inputs.")
    click_block(win, "body")
    template = win.registry.get("reports.html_template")
    inputs = row_named(win, "Inputs", "Inputs", template)
    card = hover(qtbot, win, inputs.inputs[0], "Inputs", "HTML Template › Inputs")
    label, what, values, last = card_says(card)
    parts = card_parts(win, card)
    p.shot(card, "Each row of the Inputs box (one per block connected into data, here chart's image and sales' "
                 "table) has the Inputs card: ① what the names are for, ② examples.", over=win, highlight=parts[:2],
           badges=True, zoom=[card, inputs.label_widget, *parts])
    p.check("The Inputs box has a row for each block connected into data: from chart.image, from sales.table",
            [r.label_widget.text() for r in property_rows(win, "HTML Template", template) if r.label == "Inputs"]
            == ["from chart.image", "from sales.table"])
    p.check("① It says what the names are for: Inputs' help", label == "Inputs"
            and what == template.config["names"].help)
    p.check("② Examples: chart · my_table · input1", listed(values, "Examples:") == ["chart", "my_table", "input1"])
    p.check("Its Learn more › leads to the entry of HTML Template's Inputs, on the Reports page",
            page_and_anchor(learn_more_href(card)) == ("blocks-reports.html", "reports.html_template.names"))

    # --- Contents, a guide page, Back and Forward --------------------------------------------------------------
    p.step("Press Contents, at the top of the column.")
    QTest.mouseClick(nav_button(win, "Contents"), Qt.LeftButton)
    QApplication.processEvents()
    intro, sections = readme_parts()
    headings = [heading for heading, _ in sections]
    guide = browser.toPlainText()
    p.shot(win, "Contents begins with the README's introduction. Then ① the guide: a page for each section of the "
                "README; ② the reference: the rows of the Properties panel, then each kind of block, with its blocks.",
           highlight=[text_lines(win, browser, "Guide", headings[-1]),
                      text_lines(win, browser, "Reference", "My Tools")],
           badges=True, zoom=text_lines(win, browser, "Guide", "Properties panel"))
    p.check("The column shows Contents, which begins with the README's introduction",
            browser.source().fileName() == "index.html"
            and plain_line(intro) in " ".join(guide[:guide.index("Guide")].split()))
    p.check(f"① The guide lists the README's {len(headings)} sections, in order: " + ", ".join(headings),
            all(h in guide for h in headings)
            and [guide.index(h, guide.index("Guide")) for h in headings]
            == sorted(guide.index(h, guide.index("Guide")) for h in headings))

    p.step("Click Schedules, under Guide; then press ‹ Back, and Forward ›.")
    click_link(browser, lambda href, text: page_and_anchor(href)[0] == "schedules.html")
    sentence = plain_line(dict(sections)["Schedules"])
    back, forward = nav_button(win, "Back"), nav_button(win, "Forward")
    p.shot(win, "① The Schedules section of the README, as a page of its own. ② ‹ Back is now available; Forward › "
                "is not, yet.",
           highlight=[(browser, sentence), [back, forward]], badges=True, zoom=[back, (browser, sentence)])
    p.check(f"① The page is the README's Schedules section: it says “{sentence}”",
            browser.source().fileName() == "schedules.html" and sentence in " ".join(browser.toPlainText().split()))
    p.check("② ‹ Back is available, Forward › is not", back.isEnabled() and not forward.isEnabled())

    QTest.mouseClick(back, Qt.LeftButton)
    QApplication.processEvents()
    after_back = browser.source().fileName(), forward.isEnabled()
    QTest.mouseClick(forward, Qt.LeftButton)
    QApplication.processEvents()
    p.check("‹ Back returns to Contents, and Forward › becomes available", after_back == ("index.html", True))
    p.check("Forward › goes to Schedules again", browser.source().fileName() == "schedules.html")

    # --- a block's reference page ---------------------------------------------------------------------------
    p.step("Press Contents, and click Copy Files, under Reference.")
    QTest.mouseClick(nav_button(win, "Contents"), Qt.LeftButton)
    QApplication.processEvents()
    click_link(browser, lambda href, text: href == "blocks-files.html#files.copy")
    wait_for(qtbot, lambda: heading_at_top(browser, "files.copy"), "Copy Files' entry is not at the top")
    page = " ".join(browser.toPlainText().split())
    head = entry(win, browser, "files.copy", "files.copy.source_server")
    source_entry = entry(win, browser, "files.copy.source", "files.copy.destination_server")
    p.shot(win, "Copy Files' entry, made from the block itself: ① its title, its type, what it does and its inputs "
                "and outputs; then each of its settings, as ② Source: what it is for, its name in the flow file, its "
                "kind, whether it is required, and its examples (or its choices).",
           highlight=[head, source_entry], badges=True, zoom=head | source_entry)
    keys = list(copy_files.config)
    p.check("① The entry says what Copy Files says of itself: its title, its type files.copy and its description",
            f"Copy Files Block type: files.copy {copy_files.description}" in page)
    p.check("② Every setting of Copy Files is there, in the order Properties shows them, each with its help: "
            + ", ".join(setting_label(k, copy_files.config[k]) for k in keys),
            all(" ".join(copy_files.config[k].help.split()) in page for k in keys)
            and [anchor_at(browser, f"files.copy.{k}").position() for k in keys]
            == sorted(anchor_at(browser, f"files.copy.{k}").position() for k in keys))

    # --- F1 and Help > Documentation ------------------------------------------------------------------------
    p.step("Press F1, then choose Help > Documentation.")
    win.activateWindow()
    tab.view.setFocus()
    QTest.keyClick(tab.view, Qt.Key_F1)
    QApplication.processEvents()
    wait_for(qtbot, lambda: tab.view.width() == canvas_before, "the canvas did not get its width back")
    p.shot(win, "After F1, the column is closed: ① the canvas has its width back.", highlight=[tab.view], badges=True)
    p.check("F1 closed the column", not column.isVisible())
    p.check(f"① The canvas is as wide as before the column opened ({canvas_before} px)",
            tab.view.width() == canvas_before)

    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    bar = win.menuBar()
    at = bar.mapToGlobal(bar.actionGeometry(help_menu.menuAction()).bottomLeft())
    item = help_menu.actions()[0]

    def in_the_menu(menu):
        p.shot(menu, "① The Help menu, after Tools, has Documentation, with its key, F1: choosing it opens the "
                     "column again.", over=win,
               highlight=[menu_item(menu, "Documentation")], badges=True, zoom=[menu])
        choose(menu, "Documentation")

    p.while_open(lambda: help_menu.exec(at), in_the_menu)
    QApplication.processEvents()
    p.check("① Help has one item, Documentation, with F1 as its key",
            [a.text() for a in help_menu.actions()] == ["Documentation"] and item.shortcut().toString() == "F1")
    p.check("The column is open again, on the page it showed last (Copy Files)",
            column.isVisible() and item.isChecked() and browser.source().fileName() == "blocks-files.html")

    # --- your own block -----------------------------------------------------------------------------------
    p.step("Press Contents, and scroll down to the end of its Reference.")
    QTest.mouseClick(nav_button(win, "Contents"), Qt.LeftButton)
    QApplication.processEvents()
    browser.verticalScrollBar().setValue(browser.verticalScrollBar().maximum())
    tally = win.registry.get("my.tally")
    tree = win.palette_panel.tree
    (in_tree,) = tree.findItems("Tally", Qt.MatchExactly | Qt.MatchRecursive)
    tree.scrollToItem(in_tree)
    mine = (browser, "My Tools: Tally")
    p.shot(win, "Your own block Tally, from tally.py in your blocks folder: ① in the Blocks tree, under User Created, "
                "with all your own blocks; ② in Contents, under My Tools, the category tally.py gives it, which has "
                "a page of its own.",
           highlight=[[in_window(win, tree.viewport(), tree.visualItemRect(item))
                       for item in (in_tree.parent(), in_tree)], mine],
           badges=True, zoom=text_lines(win, browser, "Reference", "My Tools: Tally"))
    p.check("① Tally, from tally.py, is one of the editor's blocks, under User Created in the Blocks tree",
            tally is not None and in_tree.parent().text(0).startswith("User Created"))
    p.check("② Contents lists Tally under My Tools", "My Tools: Tally" in browser.toPlainText())

    p.step("Add a Tally block, and rest the mouse on its Caption. To add it, double-click Tally in the Blocks tree, "
           "then click the new block tally on the canvas. Then click Learn more › on its card.")
    at = tree.visualItemRect(in_tree).center()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # a double click: a click, then the
    QTest.mouseDClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # second one (QTest sends only that)
    QApplication.processEvents()
    p.check("A Tally block, tally, is added to the flow", tab.doc.blocks.get("tally", {}).get("type") == "my.tally")
    click_block(win, "tally")
    caption = row_named(win, "Caption", "", tally)
    card = hover(qtbot, win, caption.inputs[0], "Caption", "Tally › Caption")
    label, what, values, last = card_says(card)
    parts = card_parts(win, card)
    p.shot(card, "tally.py gives Caption no help and no examples: ① its card says so, and how to add them.",
           over=win, highlight=[parts[:2]], badges=True, zoom=[card, caption.label_widget, *parts])
    p.check("① The card says “" + NO_HELP + "” and “" + NO_EXAMPLES + "”", (what, values) == (NO_HELP, NO_EXAMPLES))
    href = click_learn_more(card)
    wait_for(qtbot, lambda: heading_at_top(browser, "my.tally.caption"), "Caption's entry is not at the top")
    said = " ".join(browser.toPlainText().split())
    column_entry = entry(win, browser, "my.tally.column", "my.tally.caption")
    caption_entry = text_lines(win, browser, "Caption", NO_EXAMPLES)
    p.shot(win, "After Learn more, Tally's page, made from tally.py: ① Column, with the help and examples tally.py "
                "gives it; ② Caption, which has none, still says what it takes: text, optional, rows when left empty.",
           highlight=[column_entry, caption_entry], badges=True, zoom=column_entry | caption_entry)
    p.check("The column shows Caption's entry, on the My Tools page",
            page_and_anchor(href) == ("blocks-my-tools.html", "my.tally.caption")
            and browser.source().fileName() == "blocks-my-tools.html")
    p.check("① Column shows the help and the examples tally.py gives it",
            "Count only the rows where this column has a value" in said and "Examples: amount · region" in said)
    p.check("② Caption says what it takes: “caption · Text, optional, default: rows”",
            "caption · Text, optional, default: rows" in said)

    # --- every row, every example, every link ------------------------------------------------------------
    p.step("Try every row of every built-in block, and every link. In a new flow, add each built-in block in turn, "
           "rest the mouse on each row of its Properties and type each example into its box; then follow every link "
           "of the documentation.")
    p.note("These are done the same way as above, with no screenshot: there are too many.")
    counts = sweep(win, qtbot, tmp_path)
    p.text("What the proof found", counts["summary"])
    p.check(f"Each of the {counts['rows']} rows of the {counts['blocks']} built-in blocks shows a card with what it "
            "is for, its examples or choices, and Learn more ›, which leads to an entry that exists",
            counts["row_problems"] == [])
    p.check(f"Each of the {counts['examples']} examples, typed into its box, is taken by Properties and by the flow "
            "check", counts["example_problems"] == [])
    p.check(f"Every one of the {counts['links']} links between the documentation's {counts['pages']} pages leads "
            "to a page and an entry that exist", counts["link_problems"] == [])
    clean_all()  # what is typed is committed and nothing is left unsaved: closing the editor asks nothing


def sweep(win, qtbot, tmp_path) -> dict:
    """Every built-in block added to a new flow: each row of its Properties hovered, each example typed in; and
    every link of the pages followed. What was found."""
    (win.home.root / "connections.yaml").write_text(CONNECTIONS, encoding="utf-8")  # one connection of each kind
    pages = Pages(win.registry)
    built_in = [cls for type_id, cls in win.registry.blocks.items() if win.registry.sources[type_id] == "built-in"]
    tab = win.new_flow()
    QApplication.processEvents()
    rows, row_problems = 0, []
    examples, example_problems = 0, []
    for cls in built_in:
        block = add_block(win, tab, cls.title)
        connect_into(tab, block, cls)
        found = property_rows(win, cls.title, cls)
        rows += len(found)
        row_problems += check_rows(qtbot, win, found, pages)
        wanted = {k: examples_of_field(f) for k, f in cls.config.items() if needs_examples(cls, k, f)}
        params = {}
        for key, said in wanted.items():
            params.update(params_used(cls.config[key], said))
        if params:
            tab.doc.set_params({**(tab.doc.data.get("params") or {}), **params})
        for key, f in cls.config.items():  # the connection of its kind chosen, as you would first
            if isinstance(f, fields.Connection) and not isinstance(f, fields.Server):
                choose_in_box(row_named(win, setting_label(key, f), "", cls).inputs[0], data=CONNECTION_OF_KIND[f.kind])
        keys = sorted(wanted, key=lambda k: not isinstance(cls.config[k], (fields.Names, fields.SourceNames)))
        for key in keys:  # the names first, and every setting filled: the block's settings are checked as a whole
            type_in_row(setting_row(win, cls, key), wanted[key][0])
        for key in keys:
            label = setting_label(key, cls.config[key])
            for example in wanted[key]:
                type_in_row(setting_row(win, cls, key), example)
                examples += 1
                if error_shown(win):
                    example_problems.append(f"{cls.title} › {label}: {example!r}: {error_shown(win)}")
                example_problems += [f"{cls.title} › {label}: {example!r}: {m}"
                                     for m in problems_in_flow(win, tab, tmp_path)
                                     if f"{block}: {label}:" in m or f"{block}: {label} is required" in m]
            type_in_row(setting_row(win, cls, key), wanted[key][0])
    links, link_problems = 0, []
    for name in pages.get():
        for href, _, text in pages.page(name).links:
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            links += 1
            target, anchor = urldefrag(href)
            target = pages.page(target or name)
            if target is None or anchor and anchor not in target.ids():
                link_problems.append(f"{name}: {href!r} ({text.strip()})")
    problems = row_problems + example_problems + link_problems
    summary = (f"{len(built_in)} built-in blocks, {rows} rows of Properties: each hovered, each showed its card.\n"
               f"{len(row_problems)} problems with those cards (what the row is for, its examples or choices, Learn "
               f"more and where it leads).\n"
               f"{examples} examples of their settings typed into their boxes: {len(example_problems)} refused.\n"
               f"{links} links between {len(pages.get())} pages: {len(link_problems)} broken."
               + ("\n\n" + "\n".join(problems) if problems else ""))
    return {"blocks": len(built_in), "rows": rows, "row_problems": row_problems, "examples": examples,
            "example_problems": example_problems, "links": links, "pages": len(pages.get()),
            "link_problems": link_problems, "summary": summary}
