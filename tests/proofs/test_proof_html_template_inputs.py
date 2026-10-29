"""Proof: HTML Template takes several named inputs into its data input and shows each by its type; HTML Table and
the Jinja2 syntax are gone. See README.md here."""

import re
from pathlib import Path

import yaml
from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QLabel, QLineEdit, QPushButton, QTextBrowser, QToolBar

from taskloom.editor.dialogs import AskDialog
from taskloom.editor.panels import CodeEdit
from tests.test_editor import drag_port_to_port
from tests.test_html_template_inputs import box_texts, inputs_box, row, rows
from tests.test_paths_editor_editing import (clean, drag_over_and_drop, dragged_from_the_tree, error_shown, menu,
                                             settle)
from tests.test_paths_reports_email import html_of, inline_images

# The user's sentence, as they wrote it.
SENTENCE = "Hi {input1}, please see below the chart<br>{chart}<br>and below the table<br>{my_table}<br>thank you"
SALES_SQL = ("select * from (values ('North', 1250.5), ('South', 980.0), ('East', 1430.25), ('West', 760.75)) "
             "t(region, total)")
FLOW = {
    "blocks": {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
        "ask_name": {"type": "logic.ask_user", "config": {"prompt": "Who is the email for?"},
                     "ui": {"x": 224, "y": 0}},
        "totals": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": SALES_SQL}, "ui": {"x": 0, "y": 200}},
        "chart": {"type": "reports.chart",
                  "config": {"kind": "bar", "x": "region", "y": ["total"], "title": "Sales by region",
                             "width": 480, "height": 270},
                  "ui": {"x": 224, "y": 250}},
        "mail": {"type": "email.send",
                 "config": {"connection": "mailer", "to": ["team@example.com"], "subject": "Sales by region"},
                 "ui": {"x": 672, "y": 110}},
    },
    "edges": ["start.start -> ask_name.after", "totals.result -> chart.table"],
}
TEMPLATE_AT = QPointF(448, 110)  # where HTML Template is dropped: between chart and mail
# One block with two outputs, both to go into one HTML Template.
CHECK_FLOW = {
    "blocks": {
        "check": {"type": "logic.python",
                  "config": {"inputs": [], "outputs": ["result", "details"],
                             "code": "result = 'all rows add up'\ndetails = '4 rows, 4,421.50 in all'"},
                  "ui": {"x": 0, "y": 0}},
        "summary": {"type": "reports.html_template", "ui": {"x": 280, "y": 0}},
    },
    "edges": [],
}
# A flow made before this change: an HTML Table, and an HTML Template in the Jinja2 syntax with its own inputs.
OLD_FLOW = {
    "blocks": {
        "totals": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": SALES_SQL}},
        "table": {"type": "reports.html_table", "config": {"title": "Sales"}},
        "body": {"type": "reports.html_template",
                 "config": {"inputs": ["sales"], "template": "<p>Hi team</p>{{ table(sales) }}"}},
    },
    "edges": ["totals.result -> table.table", "totals.result -> body.sales"],
}
DUPLICATE = ("Inputs: chart is the name of more than one input (sales_chart.image, totals.result); give each its own "
             "name")
UNKNOWN = ("Template: {my_tabel} is not an input or a flow parameter (inputs: input1, chart; write {{ and }} for "
           "literal braces)")


def open_flow(win, monkeypatch, path: Path, flow: dict):
    """Write `flow` to `path` and open it (File > Open…), in a tab of its own."""
    path.write_text(yaml.safe_dump(flow, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    QTest.qWait(100)
    return win.current


def click(tab, block: str):
    """Click a block on the canvas, as you would: Properties shows it."""
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier,
                     tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center()))
    QTest.qWait(100)


def type_into(edit: QLineEdit, text: str):
    """Click into a text box, replace what it holds by `text`, as typed, and press Enter."""
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, text)
    QTest.keyClick(edit, Qt.Key_Return)
    QTest.qWait(100)


def text_box(win, holding: str) -> QLineEdit:
    """The text box of Properties that holds `holding` (e.g. a block's Name)."""
    return next(f for f in win.properties.findChildren(QLineEdit) if f.text() == holding)


def template_box(win) -> CodeEdit:
    return next(c for c in win.properties.findChildren(CodeEdit) if c.isVisible())


def write_template(win, text: str):
    """Select what the Template box holds and type `text` over it; then leave the box, which saves it."""
    box = template_box(win)
    box.setFocus()
    box.selectAll()
    QTest.keyClicks(box, text)
    box.editing_finished.emit()  # as when you click elsewhere
    QTest.qWait(100)


def on_window(win, widget, rect: QRect) -> QRect:
    """`rect`, in `widget`'s pixels, in the window's (what a highlight in a shot of the window takes)."""
    return rect.translated(widget.mapTo(win, QPoint(0, 0)))


def reports_group(win) -> tuple[QRect, list[str]]:
    """Where the Reports group of the Blocks list is, in the window, and the names of its blocks."""
    tree = win.palette_panel.tree
    folder = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                  if tree.topLevelItem(i).text(0).startswith("Reports"))
    blocks = [folder.child(i) for i in range(folder.childCount())]
    rect = tree.visualItemRect(folder) | tree.visualItemRect(blocks[-1])
    return on_window(win, tree.viewport(), rect), [b.text(0) for b in blocks]


def into(tab, block: str) -> list[str]:
    """The outputs connected into `block`'s data input, in the order of the connections: block.output."""
    return [f"{s}.{sp}" for s, sp, d, dp in tab.doc.edges() if (d, dp) == (block, "data")]


def names_of(tab, block: str) -> dict:
    return (tab.doc.blocks[block].get("config") or {}).get("names") or {}


def row_widgets(win, source: str) -> list:
    """The row of the Inputs box for `source` (block.output): its label and its name box."""
    label = next(w for w in inputs_box(win).findChildren(QLabel) if w.text() == f"from {source}")
    return [label, row(win, source)]


def as_shown(html: str, images: dict) -> QTextBrowser:
    """The email's HTML in Qt's HTML viewer, its cid: pictures taken from the email's own picture parts."""
    browser = QTextBrowser()
    for cid, data in images.items():
        browser.document().addResource(QTextDocument.ImageResource, QUrl(f"cid:{cid}"), QImage.fromData(data))
    browser.setHtml(html)
    browser.resize(560, 400)
    browser.show()
    QTest.qWait(100)
    browser.resize(560, round(browser.document().size().height()) + 2 * browser.frameWidth() + 4)  # all of it
    QTest.qWait(100)
    return browser


def body_excerpt(html: str) -> str:
    """What the template made, as the email holds it, a line per <br> and per table row; styles left out (…)."""
    start, end = html.index("Hi Bob"), html.index("thank you") + len("thank you")
    body = re.sub(r'style="[^"]*"', 'style="…"', html[start:end])
    return re.sub(r"(<br>|</tr>|<table[^>]*>)", r"\1\n", body)


# `env` and `smtp` before `win`: the editor uses their Taskloom folder, where the test mail server is `mailer`.
def test_html_template_takes_several_named_inputs_and_shows_each_by_its_type(proof, env, smtp, win, qtbot,
                                                                            monkeypatch):
    p = proof("HTML Template takes several named inputs and shows each by its type",
              'HTML Template: "config.template is required" <-- what? This block should accept several connections '
              "as input to its data hole, identify their source blocks and on the Properties panel add a new entry "
              "per source where I can specify the name of the input, being the source block name the default input "
              "name (be aware of collisions of 2 source blocks share the same name). Then I can use those names "
              'like: "Hi {input1}, please see below the chart<br>{chart}<br>and below the table<br>{my_table}<br>'
              'thank you" I think if this block is well designed and implemented could replace HTML Table since '
              "that would become redundant. This block can detect the type of its input and know if it needs for a "
              "given input if it needs to render a table, a chart, just text, something else, an image?, etc.")
    p.idea("HTML Template's data input takes as many connections as you like. Properties lists each connected output "
           "in an Inputs box, with a name you can change: at first the name of the block it comes from. The "
           "Template is your email in HTML, and {name} puts that input where you write it.")
    p.idea("Each value is shown by what it is: a table as a table made for Outlook (a header row, striped rows, "
           "numbers on the right), a chart (or any PNG picture a block gives) as a picture inside the email, and "
           "text as text, where you put it. HTML made by another HTML Template goes in as it is, a list item by "
           "item, and an empty value as nothing. A new HTML Template needs nothing filled in: an empty Template "
           "shows every input in turn.")
    p.idea("Names cannot clash unnoticed. Two blocks of a flow never share a name, but a default name can still be "
           "taken: by another output of the same block, by a name you typed, or by a flow parameter (since {name} "
           "shows those too). It then becomes block_output, such as check_details. A clash you make yourself, or a "
           "{name} that is neither an input nor a parameter, is shown in red in Properties, and Validate, taskloom "
           "validate and every run refuse the flow, so nothing is sent.")
    p.idea("HTML Table is gone, and so is the old {{ … }} syntax (Jinja2) of HTML Template: one table connected to "
           "an HTML Template with an empty Template gives what HTML Table gave, without its title (a title is now "
           "a line you write).")
    p.why_simplest("{name} and {name:format} are the placeholders Taskloom's text settings already use, such as "
                   "Send Email's subject “Daily sales {day:%Y-%m-%d}”, with {{ and }} for a brace. So the Template "
                   "is written like every other text setting, and the flow's parameters work in it the same way.")
    p.why_simplest("The value already says what it is: a table is a table, Chart gives the path of a picture file, "
                   "anything else is text. So there is nothing to choose per input, and nothing to get wrong.")
    p.why_simplest("The names are one setting of the block, written when you connect an output and kept in step when "
                   "you rename, disconnect, delete or paste a block, in the same undo step. One rule gives the "
                   "default name, the same in the editor, in taskloom validate and in a run, so a flow file written "
                   "by hand, without names, works too.")
    p.alternative("Keep the Jinja2 template, with an Inputs list that makes one input hole per name",
                  "That is what it was: {{ table(sales) }} and {{ image(chart) }} meant learning a second way to "
                  "write placeholders, and saying how to show each value. You had to name each hole before "
                  "connecting it, and a second connection into one hole replaced the first.")
    p.alternative("Use the block names as they are, with nothing stored (as Python Code's dictionary does)",
                  "You asked to choose the names (input1, my_table). And {chart} would stop working the day the "
                  "chart block was renamed; a stored name stays as it is when its block is renamed.")
    p.alternative("A choice per input: show it as a table, a picture or text",
                  "Taskloom already knows from the value, so it would be one more setting that could only be set "
                  "wrong.")
    p.alternative("Keep HTML Table next to it",
                  "As you said, it would be redundant: a table connected into HTML Template is shown as the same "
                  "table, and two blocks doing one thing make you choose for no reason.")
    p.fits("The tables and pictures are made by the same Outlook-safe pieces Send Email always used: tables with "
           "their style written on each cell, and pictures sent inside the email (not as links or data: URIs), with "
           "their size written out.")
    p.fits("Its problems are told like any other setting's: in red at the top of Properties, in Validate, in a "
           "refused run, and by taskloom validate, which exits with code 2.")
    p.fits("The data input is the kind of input Taskloom already had for several connections: every connected "
           "block must deliver, in the order of the connections (after an If, the path not taken skips the template, "
           "and so the email). Like Python Code's dictionary, two outputs of one block are told apart by the "
           "output's name.")
    p.fits("A flow that used HTML Table or the {{ … }} syntax must be changed by hand: nothing converts it. By your "
           "rule, Taskloom adds no conversions until you have flows in production. Validate names an HTML Table block and the "
           "old list of input names; {{ … }} left in a Template is not an error, and the email shows it as written, "
           "with single braces.")

    assert win.home.root == env.home, "the editor and the command line use the same Taskloom folder"
    path = env.root / "sales-email.yaml"

    # (a) HTML Table is gone --------------------------------------------------------------------------------------
    p.step("Open the flow sales-email.yaml (File > Open…), and look at the Reports group of the Blocks list, on the "
           "left.")
    tab = open_flow(win, monkeypatch, path, FLOW)
    group, listed = reports_group(win)
    p.shot(win, "① The Reports group: HTML Template, Chart and To Excel. HTML Table is gone. ② The flow: ask_name "
                "asks who the email is for, totals makes a table of sales by region, chart draws it, and mail sends "
                "the email (to the test mail server), still with no body.",
           highlight=[group, ["start", "ask_name", "totals", "chart", "mail"]], badges=True, zoom=[group])
    p.check("The flow opens", tab is not None and tab.doc.path == path.resolve())
    p.check("① The Reports group lists HTML Template, Chart and To Excel, and no HTML Table",
            {"HTML Template", "Chart", "To Excel"} <= set(listed) and "HTML Table" not in listed)

    # (b) a new HTML Template: nothing required --------------------------------------------------------------------
    p.step("Drag HTML Template from the Blocks list onto the canvas, between chart and mail, and click it.")
    tree = win.palette_panel.tree
    item = tree.findItems("HTML Template", Qt.MatchExactly | Qt.MatchRecursive)[0]
    at = tab.view.mapFromScene(TEMPLATE_AT + QPointF(92, 14))  # (its title bar's middle lands there)
    dropped = drag_over_and_drop(tab, dragged_from_the_tree(item), at)
    p.check("The canvas takes it: a new block, html_template", dropped and "html_template" in tab.doc.blocks)
    click(tab, "html_template")
    box, template = inputs_box(win), template_box(win)
    p.shot(win, "① The new html_template. ② Its Inputs box says how to connect blocks. ③ The Template box is empty. "
                "There is no red text at the top of Properties: nothing is required.",
           highlight=["html_template", box, template], badges=True,
           zoom=[text_box(win, "html_template"), box, template])
    p.check("No red text in Properties", error_shown(win) == "")
    p.check("② The Inputs box says nothing is connected yet, and how to connect blocks",
            any(t.startswith("Nothing is connected yet. Connect blocks") for t in box_texts(win)))
    p.check("③ The Template box is empty", template.toPlainText() == "")
    assert win.save(tab)
    result = env.taskloom("validate", path)
    p.check("Saved as it is, the flow passes taskloom validate: a new HTML Template needs nothing filled in",
            result.returncode == 0)

    # (c) three blocks into one data input, named, and the user's sentence ----------------------------------------
    p.step("Drag from ask_name's answer, chart's image and totals' result, one after the other, to html_template's "
           "data input; then from html_template's html to mail's body.")
    for src, port in (("ask_name", "answer"), ("chart", "image"), ("totals", "result")):
        drag_port_to_port(tab, src, port, "html_template", "data")
        settle()
    drag_port_to_port(tab, "html_template", "html", "mail", "body")
    settle()
    click(tab, "html_template")
    sources = ["ask_name", "chart", "totals"]
    p.shot(win, "① ask_name, chart and totals, all three connected into html_template's one data input. ② Its "
                "html goes to mail's body.",
           highlight=[[*sources, "html_template"], "mail"], badges=True, zoom=[*sources, "html_template", "mail"])
    p.check("① ask_name, chart and totals are all connected into data: each new connection kept the earlier ones",
            into(tab, "html_template") == ["ask_name.answer", "chart.image", "totals.result"])
    p.check("② html_template's html goes to mail's body", ("html_template", "html", "mail", "body") in tab.doc.edges())
    p.shot(win.properties, "html_template's Properties: its Inputs box lists the three, one row each (where it comes "
                           "from, and its name), named after their blocks: ask_name, chart and totals.",
           highlight=[inputs_box(win)])
    p.check("The Inputs box has one row per source, named after its block",
            rows(win) == [("from ask_name.answer", "ask_name"), ("from chart.image", "chart"),
                          ("from totals.result", "totals")])

    p.step("Rename two of the inputs, and write your sentence in the Template. In the Inputs box, click ask_name's "
           "name, type input1 and press Enter; name totals' input my_table the same way. Then click into the "
           "Template box and type: " + SENTENCE)
    type_into(row(win, "ask_name.answer"), "input1")
    type_into(row(win, "totals.result"), "my_table")
    write_template(win, SENTENCE)
    template = template_box(win)
    p.shot(win.properties, "① The rows now say input1, chart and my_table. ② The Template is your sentence, using "
                           "them. There is no red text: every name is known.",
           highlight=[[*row_widgets(win, "ask_name.answer"), *row_widgets(win, "totals.result")], template],
           badges=True)
    p.check("① The names are input1, chart and my_table, and the flow holds them",
            rows(win) == [("from ask_name.answer", "input1"), ("from chart.image", "chart"),
                          ("from totals.result", "my_table")]
            and names_of(tab, "html_template") == {"ask_name.answer": "input1", "chart.image": "chart",
                                                   "totals.result": "my_table"})
    p.check("② The Template holds your sentence, and Properties shows no red text",
            tab.doc.blocks["html_template"]["config"].get("template") == SENTENCE and error_shown(win) == "")

    # (d) a source block renamed: the name stays ------------------------------------------------------------------
    p.step("Rename the block chart to sales_chart. Click it, type sales_chart in its Name, at the top of Properties, "
           "and press Enter; then click html_template again.")
    click(tab, "chart")
    type_into(text_box(win, "chart"), "sales_chart")
    click(tab, "html_template")
    p.shot(win, "① chart is now sales_chart. ② Its row says from sales_chart.image, and its name is still chart, so "
                "{chart} in the Template still finds it.",
           highlight=["sales_chart", row_widgets(win, "sales_chart.image")], badges=True, zoom=[inputs_box(win)])
    p.check("② The row follows the renamed block, and keeps the name chart",
            rows(win)[1] == ("from sales_chart.image", "chart")
            and names_of(tab, "html_template").get("sales_chart.image") == "chart" and error_shown(win) == "")

    p.step("Choose Run > Validate.")
    win.validate_action.trigger()
    QTest.qWait(100)
    bar = win.statusBar()
    said = bar.currentMessage()
    message = on_window(win, bar, QRect(0, 0, bar.fontMetrics().horizontalAdvance(said) + 16, bar.height()))
    p.shot(win, "The status bar, at the bottom of the window: no problems found.", highlight=[message],
           zoom=[message])
    p.check("Validate finds no problems", said.endswith("no problems found"))

    # (e) a clash and a misspelt name: shown in red, and the flow is refused --------------------------------------
    p.step("Make two mistakes on purpose: name totals' input chart, like the chart's; and in the Template, misspell "
           "{my_table} as {my_tabel}.")
    type_into(row(win, "totals.result"), "chart")
    write_template(win, SENTENCE.replace("{my_table}", "{my_tabel}"))
    clash = [*row_widgets(win, "sales_chart.image"), *row_widgets(win, "totals.result")]
    p.shot(win.properties, "① Two inputs are now named chart. ② The Template says {my_tabel}. ③ In red, at the top "
                           "of Properties, what is wrong with each, and how to put it right.",
           highlight=[clash, template_box(win), win.properties.error], badges=True)
    shown = error_shown(win).splitlines()
    p.check(f"③ The first red line says: {DUPLICATE}", shown[:1] == [DUPLICATE])
    p.check(f"③ The second says: {UNKNOWN}", shown[1:] == [UNKNOWN])
    p.check("The flow holds what you typed, so you can put right either one",
            names_of(tab, "html_template")["totals.result"] == "chart"
            and "{my_tabel}" in tab.doc.blocks["html_template"]["config"]["template"])

    p.step("Save the flow (File > Save) and check it in a terminal: taskloom validate sales-email.yaml, then taskloom "
           "run sales-email.yaml.")
    assert win.save(tab)
    validated = env.taskloom("validate", path)
    p.text(f"taskloom validate sales-email.yaml (exit code {validated.returncode})",
           (validated.stdout + validated.stderr).strip())
    ran = env.taskloom("run", path)
    p.note("taskloom run refuses the flow the same way, before any block runs, so the test mail server got nothing.")
    p.step("Put both right: choose Edit > Undo twice.")
    menu(win, "Undo").trigger()
    settle()
    menu(win, "Undo").trigger()
    settle()
    click(tab, "html_template")
    p.shot(win.properties, "Each Undo took back one change: the names are input1, chart and my_table again, the "
                           "Template is your sentence, and the red text is gone.",
           highlight=[inputs_box(win), template_box(win)])
    p.check("taskloom validate exits with code 2 and names both problems, each after the block's name",
            validated.returncode == 2 and f"error: html_template: {DUPLICATE}" in validated.stderr
            and f"error: html_template: {UNKNOWN}" in validated.stderr)
    p.check("taskloom run refuses the flow (exit code 2), and no email is sent",
            ran.returncode == 2 and smtp.messages == [])
    p.check("After Undo twice: the names are input1, chart and my_table, the Template is your sentence, no red text",
            rows(win) == [("from ask_name.answer", "input1"), ("from sales_chart.image", "chart"),
                          ("from totals.result", "my_table")]
            and template_box(win).toPlainText() == SENTENCE and error_shown(win) == "")

    # A clash Taskloom avoids on its own --------------------------------------------------------------------------
    p.step("Open the flow check-two-outputs.yaml and drag both outputs of its block check, result and details, to "
           "the data input of summary, an HTML Template. (File > Open…; then click summary.)")
    check_tab = open_flow(win, monkeypatch, env.root / "check-two-outputs.yaml", CHECK_FLOW)
    drag_port_to_port(check_tab, "check", "result", "summary", "data")
    settle()
    drag_port_to_port(check_tab, "check", "details", "summary", "data")
    settle()
    click(check_tab, "summary")
    p.shot(win, "① Both outputs of check go into summary's data. ② The first is named check, after its block; check "
                "is then taken, so the second is check_details. No red text: the names do not clash.",
           highlight=[["check", "summary"], inputs_box(win)], badges=True, zoom=[inputs_box(win)])
    p.check("① check's result and details both go into summary's data",
            into(check_tab, "summary") == ["check.result", "check.details"])
    p.check("② Their names are check and check_details, and there is no red text",
            rows(win) == [("from check.result", "check"), ("from check.details", "check_details")]
            and error_shown(win) == "")

    p.step("Click the tab of sales-email.yaml, above the canvas, to go back to it.")
    bar = win.tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.tabs.indexOf(tab)).center())
    QTest.qWait(100)
    p.check("sales-email.yaml is shown again", win.current is tab)

    # (f) the email ------------------------------------------------------------------------------------------------
    p.step("Press ▶ Run now in the toolbar. ask_name asks who the email is for: type Bob and press OK.")
    assert win.current is tab
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)
    qtbot.waitUntil(lambda: any(d.isVisible() for d in win.findChildren(AskDialog)), timeout=120_000)
    ask = next(d for d in win.findChildren(AskDialog) if d.isVisible())
    QTest.keyClicks(ask.input, "Bob")
    p.shot(ask, "ask_name's question, answered Bob.")
    p.check("ask_name asks who the email is for, and Bob is typed",
            ask.windowTitle() == "ask_name asks" and ask.input.text() == "Bob")
    QTest.mouseClick(next(b for b in ask.findChildren(QPushButton) if b.text() == "OK"), Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == 1 and not tab.running and tab.runs[0].status is not None,
                    timeout=180_000)
    qtbot.waitUntil(lambda: len(smtp.messages) == 1, timeout=30_000)
    everything = ["start", "ask_name", "totals", "sales_chart", "html_template", "mail"]
    p.shot(win, "The run succeeded: every block is green, and mail sent the email.",
           highlight=[everything], zoom=everything)
    p.check("The run ends with success, and every block shows success",
            tab.runs[0].status == "success" and all(tab.scene.nodes[b].status == "success" for b in everything))

    p.step("Look at the email the test mail server received.")
    p.note("Shown in Qt's own HTML viewer: an approximation, not Outlook, which is not run in CI (and Qt's viewer "
           "leaves more room under the picture). The email's HTML is checked against Outlook's rules in the next "
           "step.")
    [msg] = smtp.messages
    html, images = html_of(msg), inline_images(msg)
    browser = as_shown(html, images)
    p.shot(browser, "The email: “Hi Bob, please see below the chart” on one line, the chart as a picture, then “and "
                    "below the table”, the table, and “thank you”.")
    browser.close()
    [cid] = images
    order = [html.index("Hi Bob, please see below the chart<br>"), html.index(f'<img src="cid:{cid}"'),
             html.index("<br>and below the table<br>"), html.index("<table cellpadding"), html.index(">North<"),
             html.index(">West<"), html.index("thank you")]
    p.check("In order: “Hi Bob, please see below the chart”, the chart, “and below the table”, the table from North "
            "to West, and “thank you”", order == sorted(order))
    p.check("Text as text: “Hi Bob,” is written where {input1} was, in no paragraph of its own (so on one line)",
            "Hi Bob, please see below the chart<br>" in html and not re.search(r"<p[^>]*>\s*Hi Bob", html))
    p.check("The chart as a picture inside the email: one PNG picture, sent with it, with its size written out",
            re.search(rf'<img src="cid:{cid}" width="\d+" height="\d+"', html) is not None
            and images[cid].startswith(b"\x89PNG"))
    p.check("The table as a table: a header row region, total, and its numbers on the right, as 1,250.50",
            re.search(r"<th[^>]*>region</th><th[^>]*>total</th>", html) is not None
            and re.search(r'<td align="right"[^>]*>1,250.50</td>', html) is not None)

    p.step("Look at the email's HTML, from Hi Bob to thank you, as the mail server received it.")
    p.text("The email's HTML (each style=\"…\" holds the element's style, left out here)", body_excerpt(html))
    p.note("Desktop Outlook draws an email with Word's engine: it reads little CSS beyond each element's own style, "
           "and shows no SVG and no data: pictures. So every style here is written on its element, and the picture "
           "comes with the email. To see it in Outlook yourself, set Save copy on Send Email and open the .eml file "
           "it writes.")
    p.check("Outlook-safe: no <style> block (every style is written on its element), every table cell styled, no "
            "data: pictures, no SVG",
            "<style" not in html.lower() and all('style="' in td for td in re.findall(r"<t[dh][ >][^>]*>", html))
            and 'src="data:' not in html and "<svg" not in html.lower())

    # What changes for a flow made before ---------------------------------------------------------------------------
    p.step("Check a flow made before this change, with an HTML Table and a Jinja2 template: taskloom validate "
           "old-report.yaml.")
    old = env.root / "old-report.yaml"
    old.write_text(yaml.safe_dump(OLD_FLOW, sort_keys=False), encoding="utf-8")
    checked = env.taskloom("validate", old)
    p.text(f"taskloom validate old-report.yaml (exit code {checked.returncode})",
           (checked.stdout + checked.stderr).strip())
    p.note("Such a flow must be changed by hand: nothing converts it. Connect totals' result into an HTML Template's "
           "data instead of an HTML Table, name that input sales in the Inputs box (at first it is named totals, "
           "after its block), and write {sales} where {{ table(sales) }} was (or keep the name totals and write "
           "{totals} there). {{ … }} left in a Template is not an error: it is shown as written, with single braces.")
    p.check("It is refused (exit code 2): HTML Table is an unknown block type, and HTML Template no longer has the "
            "old list of input names, one hole per name", checked.returncode == 2
            and "unknown block type 'reports.html_table'" in checked.stderr
            and "unknown setting(s) ['inputs']" in checked.stderr)
    clean(win)  # check-two-outputs.yaml is not saved: closing the editor after the proof must not ask about it
