"""Proof: buttons above HTML Template's Template box write Outlook-safe formatting (bold, italic, underline, a heading,
lists, a link, a new line), and the emails Send Email builds give what they write its font and spacing inline.
See README.md here."""

import math
import re

import yaml
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QKeySequence, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QDialogButtonBox, QFileDialog, QLabel, QLineEdit, QTextBrowser, QToolBar, QVBoxLayout,
                               QWidget)

from taskloom.report import FONT
from tests.test_code_editor import code_box, settle
from tests.test_html_template_formatting import (BUTTONS, button, elements, html_of, margins, shown_tip, style_of,
                                                 text_of)

ASKED = ("I would also like the Template field on the Properties panel to provide buttons for formatting, at least "
         "the most common ones like bold, italics, cursive, bullet points, etc that are compatible with Outlook. So "
         "that I can compose a good looking email just from this block Template.")
TEMPLATE = """\
Weekly sales
Hi team, sales are up this week.
The highlights:
North beat its target
South is catching up
Next steps:
Call the new stores
Send the plan by Friday
For Monday:
<ul><li>Bring the figures</li><li>Book the room</li></ul>
More on our site."""
ONE_LINE = "<ul><li>Bring the figures</li><li>Book the room</li></ul>"
ADDRESS = "https://example.com/sales"
FORMATTED = f"""\
<h2>Weekly sales</h2>
Hi team, sales are <b>up</b> <i>this week</i>.
The highlights:
<ul>
<li>North beat its target</li>
<li>South is catching up</li>
</ul>
Next steps:
<ol>
<li>Call the new stores</li>
<li>Send the plan by <u>Friday</u></li>
</ol>
For Monday:
<ul><li>Bring the figures</li><li>Book the room</li></ul>
More on <a href="{ADDRESS}">our site</a>.<br>
Thanks, <b>Ana</b>"""
FLOW = {
    "blocks": {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
        "weekly_email": {"type": "reports.html_template", "config": {"template": TEMPLATE}, "ui": {"x": 250, "y": 0}},
        "send_email": {"type": "email.send",
                       "config": {"connection": "mailer", "to": ["team@example.com"], "subject": "Weekly sales"},
                       "ui": {"x": 520, "y": 0}},
    },
    "edges": ["start.start -> weekly_email.after", "weekly_email.html -> send_email.body"],
}
TAGS = {"B": "<b>", "I": "<i>", "U": "<u>", "Heading": "<h2>", "• List": "<ul>", "1. List": "<ol>",
        "Link…": "<a href>", "New line": "<br>"}


def open_flow(win, monkeypatch, path, flow: dict):
    """Write `flow` to `path` and open it (File > Open…)."""
    path.write_text(yaml.safe_dump(flow, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    QTest.qWait(100)
    return win.current


def click_block(tab, block: str):
    """Click a block on the canvas: Properties shows it, the Log only its lines."""
    viewport = tab.view.viewport()
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier,
                     tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center()))
    QTest.qWait(100)


def at(box, text: str, offset: int = 0):
    """Where the cursor would be, `offset` characters into the first `text` of the box, in its text area."""
    cursor = QTextCursor(box.document())
    cursor.setPosition(box.toPlainText().index(text) + offset)
    return box.cursorRect(cursor).center()


def click_in(box, text: str, offset: int = 0, modifier=Qt.NoModifier):
    """Click in the box, `offset` characters into `text`; with Shift held, the selection goes up to there."""
    QTest.mouseClick(box.viewport(), Qt.LeftButton, modifier, at(box, text, offset))
    settle()


def double_click(box, word: str):
    """Double-click a word of the box: it is selected."""
    QTest.mouseDClick(box.viewport(), Qt.LeftButton, Qt.NoModifier, at(box, word, len(word) // 2))
    settle()


def press(win, label: str):
    """Click the formatting button labelled `label`."""
    QTest.mouseClick(button(win, label), Qt.LeftButton)
    settle()


def selected(box) -> str:
    return box.textCursor().selectedText().replace(chr(0x2029), "\n")  # (Qt ends a selection's lines so)


def lines_of(box, *starts: str) -> list:
    """The lines of the box that start with `starts`, to outline as one."""
    return [(box, start) for start in starts]


def line_at(box, base, at: int) -> QRect:
    """The line of the box with character `at` in it, from its start to its end, in `base`'s pixels, as lines_of
    outlines a line: for a line whose text is on other lines too, such as <ul>."""
    line = QTextCursor(box.document())
    line.setPosition(at)
    line.movePosition(QTextCursor.StartOfLine)
    rect = box.cursorRect(line)
    line.movePosition(QTextCursor.EndOfLine)
    rect |= box.cursorRect(line)
    return rect.adjusted(-3, 0, 3, 0).translated(box.viewport().mapTo(base, QPoint(0, 0)))


def email_reader(qtbot, msg) -> tuple[QWidget, QTextBrowser]:
    """The email as received, drawn by Qt's own HTML view (a stand-in for a mail program), as tall as it is."""
    reader = QWidget()
    layout = QVBoxLayout(reader)
    layout.addWidget(QLabel(f"From: {msg['From']}\nTo: {msg['To']}\nSubject: {msg['Subject']}",
                            textFormat=Qt.PlainText))
    view = QTextBrowser()
    view.setHtml(html_of(msg))
    layout.addWidget(view)
    qtbot.addWidget(reader)
    reader.resize(640, 600)
    reader.show()
    qtbot.waitExposed(reader)
    view.setFixedHeight(math.ceil(view.document().size().height()) + 2 * view.frameWidth() + 8)
    reader.resize(640, reader.sizeHint().height())
    settle()
    return reader, view


def excerpt(html: str) -> str:
    """The heading and the bulleted list of the sent HTML, each element on its own line."""
    heading = re.search(r"<h2\b.*?</h2>", html, re.S).group()
    bullets = re.search(r"<ul\b.*?</ul>", html, re.S).group()
    return "\n".join(line.strip() for line in f"{heading}\n{bullets}".splitlines() if line.strip())


# `smtp` (and with it `env`) before `win`: its Taskloom folder is the editor's, and it is made first.
def test_formatting_buttons_write_outlook_safe_html(proof, smtp, env, win, qtbot, monkeypatch):
    p = proof("Formatting buttons for HTML Template, safe for Outlook", ASKED)
    p.idea("Above the Template box of an HTML Template block there is now a row of buttons: B, I, U, Heading, "
           "• List, 1. List, Link… and New line. Select some text in the box and press one: it writes the HTML tags "
           "for that formatting around what you selected.")
    p.idea("B makes it bold, I italic (the cursive you asked for), U underlined, Heading a heading. • List and "
           "1. List make each line you selected an item of a bulleted or a numbered list. Link… asks for the address "
           "and makes the selection a link to it. New line writes <br>, a line break in the email: Enter makes a new "
           "line in the box only. With nothing selected, B, I, U, Heading and the list buttons write their tags "
           "with the cursor between them, ready for you to type, and Link… uses the address as the link's text.")
    p.idea("Each press is one step for Ctrl+Z, and the cursor stays in the box, so you can go on typing.")
    p.idea("Send Email now gives the paragraphs, list items and table cells of the emails it builds, and the "
           "heading that Heading writes (<h2>), the email's font, Arial; that heading also gets its size, and "
           "paragraphs, lists and the heading even space under them. Each is written into the element's own style "
           "attribute, which is where Outlook reads it. Its plain-text part, for mail programs that show no HTML, "
           "puts each list item on its own line.")
    p.why_simplest("The buttons only type for you. What they write is ordinary text in the box, the same as if you had "
                   "typed the tags, so the rest stays as it was: the box is the same plain box, you see exactly the "
                   "HTML you wrote, and the email carries it as you wrote it (only the {name} places are filled in, "
                   "and Send Email puts it in the email's layout and adds the font and spacing to each element), "
                   "and the Template is saved as before, when you leave the box or press Run or Save.")
    p.why_simplest("The tags are the few that every mail program shows, Outlook too: <b>, <i>, <u>, <h2>, <ul>, <ol>, "
                   "<li>, <a href> and <br>. The look that goes with them is one short list of rules: the font, the "
                   "size of a heading, the space under a paragraph or a list. Send Email writes them into each "
                   "element of the email with the step it already had to move a <style> section you wrote into the "
                   "elements, for Outlook.")
    p.why_simplest("There are no buttons for colours, fonts or sizes, and no Ctrl+B: you asked for the most common "
                   "ones.")
    p.alternative("A box that shows the formatting as you write it, like a word processor",
                  "Qt's own rich-text box keeps only what it understands of the HTML, and writes its own HTML back: "
                  "the editor's font and size, and its own spacing on every paragraph. The email would carry the "
                  "editor's look, the HTML you wrote could change on the way, and you would no longer see what is "
                  "sent.")
    p.alternative("An editor that runs as a web page inside Taskloom",
                  "It needs Qt WebEngine, a web browser engine of well over a hundred megabytes in the packaged app, "
                  "and a bridge to pass the Template back and forth, for what a row of buttons does.")
    p.alternative("Write the email in Markdown (**bold**, - item) and turn it into HTML",
                  "The Template is HTML: tables and charts go into it as HTML. Markdown would be a second way of "
                  "writing to learn and to mix with it, and it has no underline.")
    p.alternative("A button for a cursive, handwriting font",
                  "Outlook shows a font the reader's computer does not have in Times New Roman, so it would rarely "
                  "look cursive. Cursive is taken as italics: I.")
    p.alternative("Put the look in a <style> section at the top of the email",
                  "Desktop Outlook reads little CSS outside an element's own style attribute. So the rules are "
                  "written into each element, as the styles of Taskloom's tables always were, and no <style> "
                  "section is left in the email.")
    p.fits("The buttons are above every HTML box in Properties: HTML Template's Template, and an HTML setting of a "
           "block of your own. The Python and SQL boxes keep their code editor; the other boxes are as before.")
    p.fits("Send Email already wrapped the email in a layout made of tables, with every style in the elements "
           "themselves, as Outlook needs. The new rules come first, so a style you write yourself still wins, e.g. "
           "style=\"color:red\" on a paragraph. Table cells get the font too, because Outlook does not pass the font "
           "on to a table inside a table. A Template that is a whole HTML page of its own, with its own <html>, "
           "gets neither that layout nor these rules.")
    p.fits("The plain-text part was already made from the HTML, with a line break at each <br>, paragraph, table "
           "row and heading; now also at the end of each list item.")
    p.fits("The buttons work with HTML Template's {name} placeholders: select {total} and press B, and the total is "
           "shown in bold.")

    assert win.home.root == env.home, "the editor and the test mail server's connection use the same Taskloom folder"
    flow = env.root / "weekly-email.yaml"

    # --- the buttons -----------------------------------------------------------------------------------------------
    p.step("Open weekly-email.yaml (File > Open…) and click the HTML Template block weekly_email. Its Template holds "
           "the text of a weekly email, with no formatting yet but one: a short list for Monday, written as HTML on "
           "one line. send_email, a Send Email block, sends it to "
           "team@example.com, through a mail server that this proof runs on this computer.")
    tab = open_flow(win, monkeypatch, flow, FLOW)
    click_block(tab, "weekly_email")
    box = code_box(win)
    row = [button(win, label) for label in BUTTONS]
    p.shot(win, "① The row of buttons, right above ② the Template box: B is drawn bold, I italic, U underlined, then "
                "Heading, • List, 1. List, Link… and New line. ③ The list for Monday, written on one line.",
           highlight=[row, box, (box, ONE_LINE)], badges=True, zoom=[row, box])
    p.check("① Eight buttons, in this order: B, I, U, Heading, • List, 1. List, Link…, New line",
            [b.text() for b in row] == BUTTONS and all(b.isVisible() for b in row))
    top_of_box = box.mapTo(win, box.rect().topLeft()).y()
    p.check("① They are in one row, right above ② the Template box",
            len({b.mapTo(win, b.rect().topLeft()).y() for b in row}) == 1
            and all(b.mapTo(win, b.rect().bottomLeft()).y() < top_of_box for b in row))
    p.check("① B is drawn bold, I italic and U underlined",
            (row[0].font().bold(), row[1].font().italic(), row[2].font().underline()) == (True, True, True))
    p.check("When you rest the mouse on a button, it names the tag it writes: Bold <b>, Italic (cursive) <i>, "
            "Underline <u>, Heading <h2>, then <ul>, <ol>, <a href> and <br>",
            all(TAGS[b.text()] in shown_tip(b) for b in row)
            and [shown_tip(b) for b in row[:3]] == ["Bold <b>", "Italic (cursive) <i>", "Underline <u>"])

    p.step("Double-click up, on the second line of the Template.")
    qtbot.waitExposed(win)
    win.activateWindow()
    double_click(box, "up")
    qtbot.waitUntil(box.hasFocus, timeout=5_000)
    p.shot(win, "① up is selected. B, to make it bold, is the first button above the box.",
           highlight=[(box, "sales are up")], badges=True, zoom=[row, box])
    p.check("① up is selected", selected(box) == "up")

    p.step("Press B.")
    press(win, "B")
    p.shot(win, "① up is now between <b> and </b>, the tags of bold, and is still selected. The cursor is still in "
                "the box.",
           highlight=[(box, "<b>up</b>")], badges=True, zoom=[row, box])
    p.check("① The line is now: Hi team, sales are <b>up</b> this week.",
            box.toPlainText().splitlines()[1] == "Hi team, sales are <b>up</b> this week.")
    p.check("The box kept the focus, and up is still selected", box.hasFocus() and selected(box) == "up")

    p.step("Press Ctrl+Z, then Ctrl+Y.")
    QTest.keySequence(box, QKeySequence(QKeySequence.Undo))
    settle()
    p.shot(win, "After Ctrl+Z: ① up is plain again. One Ctrl+Z took back all that B wrote.",
           highlight=[(box, "sales are up")], badges=True, zoom=[row, box])
    p.check("① Ctrl+Z takes the bold back in one step: the Template is as it was", box.toPlainText() == TEMPLATE)
    QTest.keySequence(box, QKeySequence(QKeySequence.Redo))
    settle()
    p.check("Ctrl+Y puts it back",
            box.toPlainText().splitlines()[1] == "Hi team, sales are <b>up</b> this week.")

    p.step("Click just before this week, Shift+click just after it, and press I.")
    click_in(box, "this week")
    click_in(box, "this week", len("this week"), Qt.ShiftModifier)
    p.check("this week is selected", selected(box) == "this week")
    press(win, "I")
    p.check("The line is now: Hi team, sales are <b>up</b> <i>this week</i>.",
            box.toPlainText().splitlines()[1] == "Hi team, sales are <b>up</b> <i>this week</i>.")

    p.step("Click at the start of the first line, Weekly sales, press Shift+End to select it, and press Heading.")
    click_in(box, "Weekly sales")
    QTest.keyClick(box, Qt.Key_End, Qt.ShiftModifier)
    settle()
    p.check("Weekly sales is selected", selected(box) == "Weekly sales")
    press(win, "Heading")
    p.shot(win, "① Weekly sales is now between <h2> and </h2>: a heading. ② this week is between <i> and </i>: "
                "italics.",
           highlight=[(box, "<h2>Weekly sales</h2>"), (box, "<i>this week</i>")], badges=True, zoom=[row, box])
    p.check("① The first line is now: <h2>Weekly sales</h2>",
            box.toPlainText().splitlines()[0] == "<h2>Weekly sales</h2>")

    p.step("Click in the line North beat its target, then Shift+click in the line under it, South is catching up.")
    click_in(box, "North beat", 3)
    click_in(box, "South is", 3, Qt.ShiftModifier)
    p.shot(win, "① The selection goes from inside the first line to inside the second: it touches both. ② • List, to "
                "make them a bulleted list.",
           highlight=[lines_of(box, "North beat", "South is"), button(win, "• List")], badges=True, zoom=[row, box])
    p.check("① The selection touches the two lines", selected(box) == "th beat its target\nSou")

    p.step("Press • List.")
    press(win, "• List")
    new_list = box.toPlainText().index("\n<ul>\n<li>North") + 1  # (Monday's line has a <ul> and a </ul> too)
    the_list = lines_of(box, "<li>North", "<li>South") + [line_at(box, win, new_list),
                                                          line_at(box, win, box.toPlainText().index("</ul>", new_list))]
    p.shot(win, "① Each of the two lines is now a list item, between <li> and </li>, on its own line, and the two "
                "items are between <ul> and </ul>: a bulleted list.",
           highlight=[the_list], badges=True, zoom=[row, box])
    p.check("① The two lines are now: <ul>, <li>North beat its target</li>, <li>South is catching up</li>, </ul>",
            "\n<ul>\n<li>North beat its target</li>\n<li>South is catching up</li>\n</ul>\n" in box.toPlainText())

    p.step("Select the two lines under Next steps the same way, and press the button for a numbered list. It is "
           "1. List.")
    click_in(box, "Call the new")
    click_in(box, "Send the plan by Friday", len("Send the plan by Friday"), Qt.ShiftModifier)
    press(win, "1. List")
    p.check("The two lines are now a numbered list: <ol>, <li>Call the new stores</li>, <li>Send the plan by "
            "Friday</li>, </ol>",
            "\n<ol>\n<li>Call the new stores</li>\n<li>Send the plan by Friday</li>\n</ol>\n" in box.toPlainText())

    p.step("Double-click Friday and press U.")
    double_click(box, "Friday")
    p.check("Friday is selected", selected(box) == "Friday")
    press(win, "U")
    p.shot(win, "① Next steps is followed by a numbered list, between <ol> and </ol>. ② Friday is between <u> and "
                "</u>: underlined.",
           highlight=[lines_of(box, "<ol>", "<li>Call", "<li>Send", "</ol>"), (box, "<u>Friday</u>")], badges=True,
           zoom=[row, box])
    p.check("② The line is now: <li>Send the plan by <u>Friday</u></li>",
            "\n<li>Send the plan by <u>Friday</u></li>\n" in box.toPlainText())

    p.step("Click just before our site, Shift+click just after it, press Link…, type example.com/sales after "
           "https:// and press OK.")
    click_in(box, "our site")
    click_in(box, "our site", len("our site"), Qt.ShiftModifier)
    p.check("our site is selected", selected(box) == "our site")
    asked = []

    def give_the_address(dialog):
        field = dialog.findChild(QLineEdit)
        asked.append((dialog.labelText(), field.text()))
        field.end(False)
        QTest.keyClicks(field, "example.com/sales")
        p.shot(dialog, "① Link… asks for the link's address: it starts as https://, and example.com/sales is typed "
                       "after it. ② our site is still selected in the Template box.", over=win,
               highlight=[field, (box, "our site")], badges=True, zoom=[dialog, (box, "our site")])
        QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok), Qt.LeftButton)

    p.while_open(lambda: press(win, "Link…"), give_the_address)
    win.activateWindow()  # (as the system does when the question closes; the offscreen screen does not)
    qtbot.waitUntil(box.hasFocus, timeout=5_000)
    p.check("Link… asks for the “Link address:”, which starts as https://", asked == [("Link address:", "https://")])
    p.check(f"our site is now a link to the address: <a href=\"{ADDRESS}\">our site</a>",
            f'More on <a href="{ADDRESS}">our site</a>.' in box.toPlainText())
    p.check("The box has the focus again, to go on typing", box.hasFocus())

    p.step("Press Ctrl+End to go to the end of the Template, press New line, type Thanks and a comma, press B with "
           "nothing selected, and type Ana.")
    QTest.keyClick(box, Qt.Key_End, Qt.ControlModifier)
    press(win, "New line")
    QTest.keyClicks(box, "Thanks, ")
    press(win, "B")
    QTest.keyClicks(box, "Ana")
    settle()
    p.shot(win, "① Link… made our site a link to https://example.com/sales. New line wrote <br> after it and went to "
                "a new line in the box, where B, with nothing selected, wrote <b></b> and Ana was typed between "
                "them.",
           highlight=[lines_of(box, "More on", "Thanks,")], badges=True, zoom=[row, box])
    p.check("① New line wrote <br> where the cursor was and went to a new line; B with nothing selected put Ana, "
            "typed after it, between <b> and </b>: …our site</a>.<br>, then Thanks, <b>Ana</b>",
            box.toPlainText().endswith("our site</a>.<br>\nThanks, <b>Ana</b>"))
    p.check("The Template is now exactly as the close-up shows it", box.toPlainText() == FORMATTED)

    # --- the email -------------------------------------------------------------------------------------------------
    p.step("Press ▶ Run now in the toolbar. When the run has ended, click send_email: the Log, under the canvas, "
           "shows what it wrote.")
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == 1 and not tab.running and tab.runs[0].status is not None,
                    timeout=180_000)
    QTest.qWait(300)  # what the run printed last reaches the Log
    click_block(tab, "send_email")
    blocks, sent = ["start", "weekly_email", "send_email"], (win.log.text, "sent 'Weekly sales'")
    p.shot(win, "① The run ended with success: send_email sent the email. ② The Log says so: sent 'Weekly sales' to "
                "team@example.com.",
           highlight=[blocks, sent], badges=True, zoom=[blocks, sent])
    p.check("① The run ends with success", tab.runs[0].status == "success")
    p.check("② The Log says the email was sent to team@example.com",
            "sent 'Weekly sales' to team@example.com" in win.log.text.toPlainText())
    saved = yaml.safe_load(flow.read_text(encoding="utf-8"))["blocks"]["weekly_email"]["config"]["template"]
    p.check("Run now saved the flow first: its file holds the Template as the box shows it", saved == FORMATTED)
    p.check("The mail server received one email", len(smtp.messages) == 1)
    msg = smtp.messages[0]
    html, text = html_of(msg), text_of(msg)

    p.step("Look at the email the mail server received.")
    reader, view = email_reader(qtbot, msg)
    assert view.verticalScrollBar().maximum() == 0, "the whole email shows"
    p.shot(reader, "The email, drawn by Qt's own HTML view: the heading; up in bold and this week in italics; The "
                   "highlights: on the same line, since Enter in the box makes no new line in the email; the bulleted "
                   "list; the numbered list, with Friday underlined; the list for Monday; the link, our site; and Thanks, Ana on a line of "
                   "its own, Ana in bold.")
    p.note("This is not Outlook: the build machine has no Outlook, so none runs in this proof. Qt's HTML view draws "
           "the email close to how a mail program shows it. What is checked is the HTML that was sent: the tags "
           "are the ones Outlook shows, and each element has its look in its own style attribute, where Outlook "
           "reads it. To see it in Outlook, set Send Email's Save copy to a file name such as sent/report.eml, run "
           "the flow, and open that file in Outlook.")
    p.check("The sent HTML has every tag the buttons wrote: <h2>, <b>, <i>, <ul> and <li>, <ol>, <u>, <a href> "
            "and <br>",
            all(tag in html for tag in ("<h2", "<b>up</b>", "<i>this week</i>", "<ul", "<li", "<ol", "<u>Friday</u>",
                                        f'<a href="{ADDRESS}">our site</a>', "<br", "Thanks, <b>Ana</b>")))
    reader.close()

    p.step("Look at the HTML that was sent, its heading and its bulleted list, and at the email's plain-text part.")
    p.text("The sent HTML: the heading and the bulleted list", excerpt(html))
    font = " ".join(FONT.lower().split())
    h2 = style_of(html, "h2", "Weekly sales")
    p.check("The heading has its look in its own style: Arial, 18px, bold, with 10px under it",
            (h2.get("font-family"), h2.get("font-size"), h2.get("font-weight"), margins(h2)[2])
            == (font, "18px", "bold", "10px"))
    north, south = style_of(html, "li", "North beat its target"), style_of(html, "li", "South is catching up")
    p.check("Each list item has the font and 4px under it; the last item of the list 10px",
            (north.get("font-family"), margins(north)[2], south.get("font-family"), margins(south)[2])
            == (font, "4px", font, "10px"))
    p.check("Each bulleted list, this one and the one for Monday, is indented 24px, with 10px under it",
            [margins(style) for tag, style, _ in elements(html) if tag == "ul"] == [("0", "0", "10px", "24px")] * 2)
    p.check("No <style> section is left in the email", "<style" not in html.lower())
    p.text("The email's plain-text part, as received", text)
    p.note("In the plain-text part a tag becomes a space, so a full stop right after italics or a link has a space "
           "before it (this week .). Mail programs that show HTML, as Outlook does, show the HTML part.")
    p.note("The buttons already write each list item on a line of its own in the box, and those lines stay lines in "
           "the plain-text part. A list written on one line, like the one for Monday, is where the new line break at "
           "the end of each item matters: without it, Bring the figures and Book the room would run together on one "
           "line.")
    lines = [line.strip() for line in text.splitlines()]
    p.check("The plain-text part has each list item on its own line, the two of the list for Monday, written on one "
            "line, too: Bring the figures, then Book the room",
            all(item in lines for item in ("North beat its target", "South is catching up", "Call the new stores",
                                           "Send the plan by Friday", "Bring the figures", "Book the room")))
