"""Formatting an email body that looks right in Outlook, from HTML Template's Template box alone: text-labelled
buttons above every html Code box (B, I, U, Heading, • List, 1. List, Link…, New line) write Outlook-safe tags
around the selection, each click one undo step, saved like typing; every email Send Email builds gives headings,
paragraphs, lists and table cells their font and spacing inline (no <style> element), and its plain-text part
puts each list item on its own line.

The buttons are driven in the real editor (the `win` fixture): flows opened from files, the block selected on the
canvas, text selected and typed in the box, the buttons clicked, the flow saved. The emails are sent by the real
`taskloom run` to the test SMTP server (the `smtp` fixture)."""

import html as html_lib
import os
import re
import textwrap

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6 import QtGui  # noqa: E402
from PySide6.QtGui import QAction, QKeySequence, QTextDocument  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QToolButton  # noqa: E402

from taskloom.report import FONT, INK  # noqa: E402
from tests.test_code_editor import (clean, code_box, cursor_at, focus, keys, leave, open_blocks, select,  # noqa: E402
                                    select_text, settle)

BUTTONS = ["B", "I", "U", "Heading", "• List", "1. List", "Link…", "New line"]
WRAPS = {"B": ("<b>", "</b>"), "I": ("<i>", "</i>"), "U": ("<u>", "</u>"), "Heading": ("<h2>", "</h2>")}
LISTS = {"• List": "ul", "1. List": "ol"}


# --- helpers ---------------------------------------------------------------------------------------------------

def template(text: str) -> dict:
    return {"type": "reports.html_template", "config": {"template": text}}


def template_box(win, qtbot, path, text: str):
    """Open a flow whose HTML Template `body` holds `text`, select it and click into its Template box."""
    tab = open_blocks(win, path, {"body": template(text)})
    select(tab, "body")
    box = code_box(win)
    focus(win, qtbot, box)
    return tab, box


def button(win, text: str) -> QToolButton:
    """The formatting button labelled `text` in Properties."""
    found = [b for b in win.properties.widget().findChildren(QToolButton) if b.text() == text]
    assert len(found) == 1, f"{len(found)} {text!r} buttons in Properties"
    return found[0]


def click(win, text: str):
    QTest.mouseClick(button(win, text), Qt.LeftButton)
    settle()


def undo_in(box):
    """Ctrl+Z in the box (it acts on the box's text)."""
    QTest.keySequence(box, QKeySequence(QKeySequence.Undo))
    settle()


def answer_link(win, address: str | None) -> list:
    """When the Link… question shows: note (its label, the text it starts with), then answer `address` with OK (None:
    Cancel). It looks for the question for about 5 s; none showing is left to the test to notice. Any other dialog is
    closed, so it never waits for a click."""
    asked, tries = [], []

    def act():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, QDialog):
            if len(tries) < 250:
                tries.append(1)
                QTimer.singleShot(20, win, act)
            return
        try:
            if isinstance(dialog, QInputDialog):
                asked.append((dialog.labelText(), dialog.textValue()))
                if address is not None:
                    dialog.setTextValue(address)
                    dialog.accept()
        finally:
            if dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, win, act)
    return asked


def shown_tip(widget) -> str:
    """A tooltip's text as it shows: Qt shows a tooltip that looks like HTML as HTML."""
    tip = widget.toolTip()
    if not QtGui.Qt.mightBeRichText(tip):
        return tip
    doc = QTextDocument()
    doc.setHtml(tip)
    return doc.toPlainText()


# --- F29: the buttons ------------------------------------------------------------------------------------------

def test_the_template_box_has_text_labelled_formatting_buttons_right_above_it(win, qtbot, tmp_path):
    """Spec F29: a row of text-labelled buttons above the Template box: B drawn bold, I italic, U underlined, each
    saying which tag it writes ("Bold <b>", "Italic (cursive) <i>", "Underline <u>", as the tooltip shows), then
    Heading, • List, 1. List, Link… and New line."""
    tab = open_blocks(win, tmp_path / "email.yaml", {"body": template("Hi team")})
    try:
        select(tab, "body")
        body = win.properties.widget()
        box = code_box(win)
        top_of_box = box.mapTo(body, QPoint(0, 0)).y()
        for label in BUTTONS:
            b = button(win, label)
            assert b.isVisible(), f"the {label!r} button is not shown"
            assert b.mapTo(body, QPoint(0, b.height())).y() <= top_of_box, f"the {label!r} button is not above the box"

        b, i, u = button(win, "B"), button(win, "I"), button(win, "U")
        assert (b.font().bold(), i.font().italic(), u.font().underline()) == (True, True, True)
        assert [shown_tip(b), shown_tip(i), shown_tip(u)] == ["Bold <b>", "Italic (cursive) <i>", "Underline <u>"]
    finally:
        clean(win)


# --- F30 and F31: what each button writes, as one undo step, the box keeping the focus ----------------------------

@pytest.mark.parametrize("label", list(WRAPS))
def test_a_tag_button_wraps_the_selection_in_one_undo_step_and_the_box_keeps_the_focus(win, qtbot, tmp_path, label):
    """Spec F30 (B, I, U and Heading wrap the selection) and F31 (each click is one undo step in the box; the box keeps
    the focus): "Hi team!" with "team" selected; one Ctrl+Z gives back exactly the text before the click."""
    start, end = WRAPS[label]
    tab, box = template_box(win, qtbot, tmp_path / "email.yaml", "Hi team")
    try:
        cursor_at(box, 0)
        keys(box, "!")
        select_text(box, (0, 3), (0, 7))

        click(win, label)
        assert box.toPlainText() == f"Hi {start}team{end}!"
        assert box.hasFocus(), f"the box lost the focus to the {label!r} button"
        undo_in(box)
        assert box.toPlainText() == "Hi team!"
    finally:
        clean(win)


@pytest.mark.parametrize("label", list(WRAPS))
def test_a_tag_button_with_nothing_selected_puts_what_you_type_between_its_tags(win, qtbot, tmp_path, label):
    """Spec F30: with nothing selected, B, I, U and Heading insert the empty pair with the cursor between."""
    start, end = WRAPS[label]
    tab, box = template_box(win, qtbot, tmp_path / "email.yaml", "Hi ")
    try:
        cursor_at(box, 0)
        click(win, label)
        assert box.toPlainText() == f"Hi {start}{end}"
        keys(box, "team")
        assert box.toPlainText() == f"Hi {start}team{end}"
    finally:
        clean(win)


@pytest.mark.parametrize("label", list(LISTS))
def test_a_list_button_makes_each_line_the_selection_touches_an_item(win, qtbot, tmp_path, label):
    """Spec F30 (the list buttons turn every line the selection touches into <li>line</li>, each on its own line,
    inside <ul>\\n…\\n</ul> or <ol>\\n…\\n</ol>) and F31 (one undo step, the box keeps the focus): a selection from
    inside one line to inside the next."""
    tag = LISTS[label]
    text = "Hi team,\napples\npears\nThanks"
    tab, box = template_box(win, qtbot, tmp_path / "email.yaml", text)
    try:
        select_text(box, (1, 2), (2, 3))

        click(win, label)
        assert box.toPlainText() == f"Hi team,\n<{tag}>\n<li>apples</li>\n<li>pears</li>\n</{tag}>\nThanks"
        assert box.hasFocus(), f"the box lost the focus to the {label!r} button"
        undo_in(box)
        assert box.toPlainText() == text
    finally:
        clean(win)


@pytest.mark.parametrize("label", list(LISTS))
def test_a_list_button_with_nothing_selected_starts_a_list_with_the_cursor_in_its_item(win, qtbot, tmp_path, label):
    """Spec F30: with nothing selected the list buttons insert <ul>\\n<li></li>\\n</ul> (or <ol>…) with the cursor
    inside the li."""
    tag = LISTS[label]
    tab, box = template_box(win, qtbot, tmp_path / "email.yaml", "Items:\n\nThanks")
    try:
        cursor_at(box, 1)
        click(win, label)
        assert box.toPlainText() == f"Items:\n<{tag}>\n<li></li>\n</{tag}>\nThanks"
        keys(box, "apples")
        assert box.toPlainText() == f"Items:\n<{tag}>\n<li>apples</li>\n</{tag}>\nThanks"
    finally:
        clean(win)


LINKS = {  # what you type after "See", what you select, the address you answer (None: Cancel), the text then
    "around the selection": (" site", (4, 8), "https://example.com", 'See <a href="https://example.com">site</a>'),
    "nothing selected: the address is the text": (" ", (4, 4), "https://example.com",
                                                  'See <a href="https://example.com">https://example.com</a>'),
    "an address with & and quotes is escaped": (" site", (4, 8), 'https://example.com/?a=1&b="x"',
                                                'See <a href="https://example.com/?a=1&amp;b=&quot;x&quot;">site</a>'),
    "cancelled: nothing changes": (" site", (4, 8), None, "See site"),
}


@pytest.mark.parametrize("case", list(LINKS))
def test_link_asks_for_the_address_and_makes_a_link(win, qtbot, tmp_path, case):
    """Spec F30 (Link… asks "Link address", prefilled https://; OK wraps the selection in <a href="ESCAPED">…</a>,
    with nothing selected the address is also the link text; Cancel changes nothing; the box gets the focus back)
    and F31 (one undo step). The real question is answered, after typing in the box, as you would: what you typed
    is not lost while the question shows."""
    typed, (start, end), address, expected = LINKS[case]
    tab, box = template_box(win, qtbot, tmp_path / "email.yaml", "See")
    try:
        cursor_at(box, 0)
        keys(box, typed)
        select_text(box, (0, start), (0, end))
        before = box.toPlainText()

        link = button(win, "Link…")
        asked = answer_link(win, address)
        QTest.mouseClick(link, Qt.LeftButton)
        settle()
        assert len(asked) == 1, "Link… did not ask for the address"
        label, prefilled = asked[0]
        assert "Link address" in label and prefilled == "https://", asked[0]
        win.activateWindow()  # (as the system does when the question closes; the offscreen platform does not)
        qtbot.waitUntil(win.isActiveWindow, timeout=5_000)
        box = code_box(win)
        assert box.toPlainText() == expected
        assert box.hasFocus(), "the box did not get the focus back"
        if address is not None:
            undo_in(box)
            assert box.toPlainText() == before
    finally:
        clean(win)


def test_new_line_inserts_a_br_and_a_line_break_at_the_cursor(win, qtbot, tmp_path):
    """Spec F30 (New line inserts "<br>\\n" at the cursor: Enter alone makes no new line in the email) and F31."""
    tab, box = template_box(win, qtbot, tmp_path / "email.yaml", "Hello,Bye")
    try:
        cursor_at(box, 0, 6)
        click(win, "New line")
        assert box.toPlainText() == "Hello,<br>\nBye"
        assert box.hasFocus(), "the box lost the focus to the New line button"
        undo_in(box)
        assert box.toPlainText() == "Hello,Bye"
    finally:
        clean(win)


def test_a_formatted_template_is_saved_like_typing_and_written_to_the_flow_file(win, qtbot, tmp_path):
    """Spec F31: the template the buttons wrote is saved like typing: in the flow when the box loses focus, and in
    the flow file by Save."""
    path = tmp_path / "email.yaml"
    tab, box = template_box(win, qtbot, path, "Hi team")
    try:
        select_text(box, (0, 3), (0, 7))
        click(win, "B")
        cursor_at(box, 0)
        click(win, "New line")
        keys(box, "Thanks")
        formatted = "Hi <b>team</b><br>\nThanks"
        assert box.toPlainText() == formatted
        leave(tab, qtbot, box)
        assert tab.doc.blocks["body"]["config"]["template"] == formatted

        next(a for a in win.findChildren(QAction) if a.text() == "Save").trigger()
        settle()
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert saved["blocks"]["body"]["config"]["template"] == formatted
    finally:
        clean(win)


OWN_HTML_BLOCK = '''
from taskloom import Block, fields, ports

class Banner(Block):
    type_id = "my.banner"
    title = "Banner"
    outputs = {"html": ports.Any()}
    config = {"html": fields.Code("html")}

    def run(self, ctx):
        return {"html": self.config.html}
'''


def test_an_html_code_box_of_your_own_block_has_the_same_buttons(win, qtbot):
    """Spec F29: every Code field of language html, also one of a block of your own, gets the buttons."""
    from taskloom.config import Home
    from taskloom.editor.window import MainWindow

    blocks = win.home.root / "blocks"
    blocks.mkdir(parents=True, exist_ok=True)
    (blocks / "banner.py").write_text(OWN_HTML_BLOCK, encoding="utf-8")
    mine = MainWindow(Home(win.home.root))  # it finds your blocks when it starts
    qtbot.addWidget(mine)
    mine.show()
    tab = mine.new_flow()
    try:
        block = tab.doc.add_block("my.banner", 0, 0)
        settle()
        select(tab, block)
        for label in BUTTONS:
            button(mine, label)
        box = code_box(mine)
        focus(mine, qtbot, box)
        keys(box, "Hi team")
        select_text(box, (0, 3), (0, 7))
        click(mine, "B")
        assert box.toPlainText() == "Hi <b>team</b>"
    finally:
        clean(mine)


# --- G32 and G33: the email Send Email builds ---------------------------------------------------------------------

WEEKLY = """
blocks:
  data:
    type: logic.python
    config:
      inputs: []
      outputs: [table]
      code: |
        table = pl.DataFrame({"region": ["EU", "US"], "total": [20.0, 15.25]})
  body:
    type: reports.html_template
    config:
      template: |
        <h2>Weekly report</h2><p>Hi team,</p><p style="color:red;margin:0 0 20px">Two regions are late.</p><ul><li>apples</li><li>pears</li></ul><ol><li>first</li><li>second</li></ol>
  mail:
    type: email.send
    config: {connection: mailer, to: [team@example.com], subject: Weekly}
edges:
  - body.html -> mail.body
  - data.table -> mail.body
"""


def send_weekly(env, smtp):
    """Send the weekly email (a heading, paragraphs, a bullet and a numbered list, then a table) and return it."""
    flow = env.root / "weekly.yaml"
    flow.write_text(textwrap.dedent(WEEKLY), encoding="utf-8")
    result = env.taskloom("run", flow)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(smtp.messages) == 1
    return smtp.messages[0]


def html_of(msg) -> str:
    return msg.get_body(preferencelist=("html",)).get_content().replace("\r\n", "\n")


def text_of(msg) -> str:
    return msg.get_body(preferencelist=("plain",)).get_content().replace("\r\n", "\n")


def _length(value: str) -> str:
    return "0" if value in ("0", "0px") else value


def declarations(style: str) -> dict:
    """{property: value} of a style attribute as it applies: a later declaration wins; margin and padding per side."""
    found = {}
    for item in style.split(";"):
        prop, _, value = item.partition(":")
        prop, value = prop.strip().lower(), " ".join(value.split()).lower()
        if not prop:
            continue
        if prop in ("margin", "padding"):
            v = [_length(x) for x in value.split()]
            sides = {1: v * 4, 2: v * 2, 3: v + v[1:2], 4: v}[len(v)]
            found.update(zip((f"{prop}-top", f"{prop}-right", f"{prop}-bottom", f"{prop}-left"), sides))
        else:
            found[prop] = _length(value) if prop.startswith(("margin-", "padding-")) else value
    return found


def elements(html: str) -> list[tuple[str, dict, str]]:
    """(tag, its style, the text right after its start tag) of each element of the sent HTML."""
    found = []
    for tag, attrs, text in re.findall(r"<([a-z0-9]+)\b([^>]*)>([^<]*)", html):
        style = re.search(r'\sstyle="([^"]*)"', attrs)
        found.append((tag, declarations(html_lib.unescape(style.group(1)) if style else ""), html_lib.unescape(text).strip()))
    return found


def style_of(html: str, tag: str, text: str = "") -> dict:
    found = [style for t, style, after in elements(html) if t == tag and after == text]
    assert len(found) == 1, f"{len(found)} <{tag}> elements with the text {text!r} in the sent HTML"
    return found[0]


def margins(style: dict) -> tuple:
    return tuple(style.get(f"margin-{side}") for side in ("top", "right", "bottom", "left"))


def test_the_email_gives_headings_paragraphs_lists_and_table_cells_their_font_and_spacing_inline(env, smtp):
    """Spec G32: in the sent HTML, p, h2, li, td and th carry the email font; p and h2 a 10px bottom margin, h2 18px
    bold in the ink colour; ul and ol a 24px indent, no padding and a 10px bottom margin; li a 4px bottom margin, the
    last item of a list 10px; an element's own style attribute still wins; no <style> element is left."""
    html = html_of(send_weekly(env, smtp))
    font = " ".join(FONT.lower().split())

    assert "<style" not in html
    h2 = style_of(html, "h2", "Weekly report")
    assert (h2.get("font-family"), h2.get("font-size"), h2.get("font-weight"), h2.get("color")) == \
        (font, "18px", "bold", INK.lower()), h2
    assert margins(h2) == ("0", "0", "10px", "0"), h2
    hi = style_of(html, "p", "Hi team,")
    assert hi.get("font-family") == font and margins(hi) == ("0", "0", "10px", "0"), hi
    own = style_of(html, "p", "Two regions are late.")
    assert own.get("color") == "red" and margins(own) == ("0", "0", "20px", "0") and own.get("font-family") == font, own
    for tag in ("ul", "ol"):
        listed = style_of(html, tag)
        assert margins(listed) == ("0", "0", "10px", "24px"), (tag, listed)
        assert tuple(listed.get(f"padding-{side}") for side in ("top", "right", "bottom", "left")) == ("0",) * 4, listed
    for item, bottom in (("apples", "4px"), ("pears", "10px"), ("first", "4px"), ("second", "10px")):
        li = style_of(html, "li", item)
        assert li.get("font-family") == font and margins(li) == ("0", "0", bottom, "0"), (item, li)
    cells = [(tag, style) for tag, style, _ in elements(html) if tag in ("td", "th")]
    assert any(tag == "th" for tag, _ in cells)
    assert [(tag, style) for tag, style in cells if style.get("font-family") != font] == []


def test_the_emails_plain_text_puts_each_list_item_on_its_own_line(env, smtp):
    """Spec G33: the plain-text alternative ends a line at </li>, so list items written on one line are not run
    together."""
    lines = [line.strip() for line in text_of(send_weekly(env, smtp)).splitlines()]
    assert [item for item in ("Weekly report", "Hi team,", "apples", "pears", "first", "second") if item not in lines] \
        == [], lines
