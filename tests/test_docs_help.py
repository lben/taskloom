"""Documentation inside the editor (phase 1). Resting the mouse on any row of the Properties tab shows a card: the
row's label, what it is for, examples of valid values (or the choices it offers, or the connection line), and
"Learn more ›".
Learn more, Help > Documentation and F1 open a Documentation column at the far right of the window, next to the canvas
and Properties, with Back, Forward and Contents. Its guide pages come from README.md and its reference pages are
generated from the live block registry (so they list the user's own blocks), by taskloom.editor.docs.pages(registry).
Every built-in setting has help, and 1 to 3 examples= that its Properties input and validate accept.

Hover is simulated as Qt does it: the mouse is moved onto the widget, then the widget under it gets
QHelpEvent(ToolTip)."""

import importlib
import json
import os
import re
import shutil
import string
import subprocess
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDateTime, QEvent, QObject, QPoint, Qt, QTime, QUrl, Slot  # noqa: E402
from PySide6.QtGui import Qt as GuiQt  # noqa: E402
from PySide6.QtGui import (QDesktopServices, QEnterEvent, QFocusEvent, QHelpEvent, QTextCursor,  # noqa: E402
                           QTextDocument)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QAbstractScrollArea, QAbstractSpinBox, QApplication,  # noqa: E402
                               QCheckBox, QComboBox, QDateTimeEdit, QDockWidget, QFormLayout, QGroupBox, QLabel,
                               QLineEdit, QPlainTextEdit, QTextBrowser, QTimeEdit, QWidget)

from taskloom import params as P  # noqa: E402
from taskloom.block import fields, setting_label  # noqa: E402
from taskloom.blocks.logic import write_reusable_block  # noqa: E402
from taskloom.blocks.triggers import KeptRuns  # noqa: E402
from taskloom.config import Home  # noqa: E402
from taskloom.editor.window import MainWindow  # noqa: E402
from taskloom.flow import FlowError, flow_from_data, validate  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_editor import add_from_palette, drag_port_to_port  # noqa: E402
from tests.test_paths_editor_editing import clean, error_shown, flow_file, press, select_only, settle  # noqa: E402
from tests.test_paths_editor_running import right_click  # noqa: E402
from tests.test_reusable_block import CREATE, fill_all, in_the_window, names_flow  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
LEARN_MORE = "Learn more ›"
NO_HELP = 'No help yet: add help="..." and examples=[...] to this setting in its block file.'
NO_EXAMPLES = "No examples yet"
KEEP_LINE = "One of: As in Settings · All of them · The last N · For a time"
SERVER_LINE = "Choose This computer, or one of your SSH connections by name, or add one with New…"
WHEN_FIELDS = ("cron", "every", "at")  # Schedule's: set through the When box, never a row (rule 3a)
# The When-box rows each of them is set by (rule 3a): their entries link there.
WHEN_ROWS = {"cron": {"Cron line", "How often"}, "every": {"Every"}, "at": {"On"}}
INPUTS = (QLineEdit, QComboBox, QAbstractSpinBox, QCheckBox, QPlainTextEdit)


def docs():
    """The new Qt-free module that builds the pages (taskloom/editor/docs.py)."""
    return importlib.import_module("taskloom.editor.docs")


def norm(text: str) -> str:
    return " ".join(str(text).split())


def builtin_blocks() -> list:
    registry = discover(None)
    return [cls for type_id, cls in registry.blocks.items() if registry.sources[type_id] == "built-in"]


def derived_line(f) -> str | None:
    """Rule 3: the line docs.py writes for a field that needs no examples= (None: it needs examples)."""
    if isinstance(f, KeptRuns):
        return KEEP_LINE
    if isinstance(f, fields.Choice):
        return "One of: " + " · ".join(f.options)
    if isinstance(f, fields.Bool):
        return "One of: ticked · unticked"
    if isinstance(f, fields.Server):
        return SERVER_LINE
    if isinstance(f, fields.Connection):
        return f"Choose one of your {f.kind.upper()} connections by name, or add one with New…"
    return None


def needs_examples(cls, key, f) -> bool:
    """Rule 2: every built-in field that is not derived (rule 3) and not Schedule's cron, every or at (rule 3a)."""
    return derived_line(f) is None and not (cls.type_id == "trigger.schedule" and key in WHEN_FIELDS)


def examples_of_field(f) -> tuple:
    return tuple(getattr(f, "examples", ()) or ())


# --- the pages, as HTML ---------------------------------------------------------------------------------------

BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "pre", "table", "tr", "td", "th",
              "br", "dl", "dt", "dd", "section", "blockquote", "hr"}


class Page(HTMLParser):
    """A page's (or a card's) text as shown, its anchors (id, and name on <a>) with where they are in that text, its
    links, and the colours its markup sets."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.parts, self.size = [], 0
        self.anchors: list[tuple[str, int]] = []
        self.links: list[list] = []  # [href, offset, text]
        self.colours: list[str] = []
        self._open, self._style = [], False
        self.feed(source)
        self.close()
        self.text = "".join(self.parts)

    def _add(self, text):
        self.parts.append(text)
        self.size += len(text)

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        for name in sorted({a.get("id")} | ({a.get("name")} if tag == "a" else set())):
            if name:
                self.anchors.append((name, self.size))
        for key in ("bgcolor", "color"):
            if key in a:
                self.colours.append(f"<{tag} {key}={a[key]}>")
        if re.search(r"(?i)\b(color|background)", a.get("style", "")):
            self.colours.append(f"<{tag} style={a['style']}>")
        self._style = tag == "style"
        if tag in BLOCK_TAGS:
            self._add("\n")
        if tag == "a":
            link = [a["href"], self.size, ""] if "href" in a else None
            if link:
                self.links.append(link)
            self._open.append(link)

    def handle_endtag(self, tag):
        if tag == "a" and self._open:
            self._open.pop()
        if tag == "style":
            self._style = False
        if tag in BLOCK_TAGS:
            self._add("\n")

    def handle_data(self, data):
        if self._style:
            if re.search(r"(?i)\b(color|background)", data):
                self.colours.append(f"<style>{data.strip()[:80]}")
            return
        self._add(data)
        for link in self._open:
            if link is not None:
                link[2] += data

    def ids(self) -> list[str]:
        return [name for name, _ in self.anchors]

    def span(self, name: str, until=None) -> tuple[int, int]:
        """Where the entry of anchor `name` is in the text: up to the next anchor (`until(anchor)`: the next one
        it says ends it)."""
        start = dict(reversed(self.anchors))[name]
        ends = [at for other, at in self.anchors if at > start and (until is None or until(other))]
        return start, min(ends, default=len(self.text))

    def section(self, name: str, until=None) -> str:
        start, end = self.span(name, until)
        return self.text[start:end]

    def links_in(self, start: int, end: int) -> list[str]:
        return [href for href, at, _ in self.links if start <= at < end]


class Pages:
    """docs.pages(registry), built when first needed and parsed once."""

    def __init__(self, registry):
        self.registry, self._pages, self._parsed = registry, None, {}

    def get(self) -> dict:
        if self._pages is None:
            self._pages = docs().pages(self.registry)
        return self._pages

    def page(self, name) -> Page | None:
        if name not in self.get():
            return None
        if name not in self._parsed:
            self._parsed[name] = Page(self.get()[name])
        return self._parsed[name]

    def holding(self, anchor) -> list[str]:
        return [name for name in self.get() if anchor in self.page(name).ids()]


def page_and_anchor(href: str) -> tuple[str, str]:
    """('blocks-files.html', 'files.copy.source') for a link, whatever base or scheme it is written with."""
    url = QUrl(href)
    return url.fileName() or urldefrag(href)[0], url.fragment()


# --- README ----------------------------------------------------------------------------------------------------

def slug(heading: str) -> str:
    """A GitHub-style heading id: lower case, punctuation dropped, each space a hyphen."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def readme_parts() -> tuple[list[str], list[tuple[str, list[str]]]]:
    """README's introduction (the lines before its first '## ') and its '## ' sections, in order (lines inside code
    blocks are not headings)."""
    intro, sections, fence = [], [], False
    for line in README.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fence = not fence
        elif not fence and line.startswith("## "):
            sections.append((line[3:].strip(), []))
            continue
        (sections[-1][1] if sections else intro).append("" if fence or line.startswith("```") else line)
    return intro, sections


def plain_line(lines: list[str]) -> str | None:
    """A line of README prose with no Markdown in it, as it reads on a page."""
    return next((line.strip() for line in lines
                 if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ,.;:()'/\-]{29,}", line.strip())
                 and not re.match(r"\d+[.)] ", line.strip())), None)  # (a numbered item loses its number)


# --- the card --------------------------------------------------------------------------------------------------

def docs_dock(win) -> QDockWidget | None:
    return next((d for d in win.findChildren(QDockWidget) if d.windowTitle() == "Documentation"), None)


def browser_of(win) -> QTextBrowser:
    dock = docs_dock(win)
    assert dock is not None, "no Documentation column"
    found = dock.findChild(QTextBrowser)
    assert found is not None, "the Documentation column shows no pages"
    return found


def visible_cards(win) -> list[QLabel]:
    """The labels showing a card's Learn more line (not the pages of the Documentation column)."""
    dock = docs_dock(win)
    return [w for w in QApplication.allWidgets()
            if isinstance(w, QLabel) and w.isVisible() and "Learn more" in w.text()
            and not (dock is not None and dock.isAncestorOf(w))]


def native_tips() -> list[QWidget]:
    return [w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()]


def hide_native_tips():
    for tip in native_tips():
        tip.hide()


def shown_doc(label: QLabel) -> QTextDocument:
    doc = QTextDocument()
    doc.setDefaultFont(label.font())
    rich = label.textFormat() == Qt.RichText or (
        label.textFormat() == Qt.AutoText and GuiQt.mightBeRichText(label.text()))
    (doc.setHtml if rich else doc.setPlainText)(label.text())
    return doc


def text_width(part: QLabel) -> tuple[float, int]:
    """The width the widest line of `part`'s text needs, and the width the label gives its text (as QLabel lays it
    out: inside its frame and margin, with no document margin)."""
    doc = shown_doc(part)
    doc.setDocumentMargin(0)
    width = part.contentsRect().width() - 2 * part.margin()
    doc.setTextWidth(width)
    return doc.idealWidth(), width


def card_parts(card: QLabel) -> list[QLabel]:
    top = card.window()
    if top is card or isinstance(top, MainWindow):
        return [card]
    labels = [w for w in top.findChildren(QLabel) if w.isVisible() and w.text().strip()]
    return sorted(labels, key=lambda w: w.mapTo(top, QPoint(0, 0)).y()) or [card]


def card_lines(card: QLabel) -> list[str]:
    text = "\n".join(shown_doc(part).toPlainText() for part in card_parts(card))
    text = text.replace("\u2028", "\n").replace("\u2029", "\n").replace("\xa0", " ")
    return [line.strip() for line in text.split("\n") if line.strip()]


def label_is_bold(card: QLabel) -> bool:
    part = card_parts(card)[0]
    doc = shown_doc(part)
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.text().strip():
                return fragment.charFormat().font().bold() or fragment.charFormat().fontWeight() >= 600 \
                    or part.font().bold()
            it += 1
        block = block.next()
    return False


def learn_more_href(card: QLabel) -> str | None:
    for part in card_parts(card):
        for href, _, text in Page(part.text()).links:
            if "Learn more" in text:
                return href
    return None


def listed(third: str, prefix: str) -> list[str]:
    return [v.strip() for v in " ".join(third[len(prefix):].split("\n")).split(" · ") if v.strip()]


# --- the rows of the Properties tab, found in the live panel ----------------------------------------------------

@dataclass
class Row:
    where: str  # e.g. "Copy Files › Source", for messages
    label: str  # the label the card must show
    label_widget: QLabel | None  # the row's label, when it has one
    inputs: list  # the inputs of the row (a code box: its editor)
    top: QWidget  # the panel or group box the row is in
    field: tuple | None  # (block class, key, field) for a setting; None for a row ROWS documents


def parents_between(widget, top):
    parent = widget.parentWidget()
    while parent is not None and parent is not top:
        yield parent
        parent = parent.parentWidget()


def inputs_in(widget) -> list:
    """A row's inputs: the field itself, or the inputs inside it (not buttons, not the parts of an input)."""
    if isinstance(widget, INPUTS):
        return [widget]
    return [w for w in widget.findChildren(QWidget) if isinstance(w, INPUTS) and w.isVisible()
            and not any(isinstance(p, INPUTS) for p in parents_between(w, widget))]


def source_names_box(cls, box: str) -> bool:
    """Whether `box` is the box of a setting that lists the outputs connected into an input (fields.SourceNames, e.g.
    HTML Template's Inputs): each of its rows ("from block.port" and a name) is that setting's."""
    return cls is not None and any(isinstance(f, fields.SourceNames) and setting_label(k, f) == box
                                   for k, f in cls.config.items())


def field_for(cls, box: str, text: str):
    if cls is None or box in ("When", "Retry and timeout"):
        return None
    hidden = WHEN_FIELDS if cls.type_id == "trigger.schedule" else ()
    keys = [k for k, f in cls.config.items() if k not in hidden and setting_label(k, f) == text]
    if box == "Keep runs":
        keys = [k for k in keys if k in ("keep", "keep_failed")]
    return (cls, keys[0], cls.config[keys[0]]) if keys else None


def property_rows(win, title: str, cls=None) -> list[Row]:
    """Every input row the Properties tab shows now (`cls`: the selected block's class; None for a text on the
    canvas or a block type that is not installed), from its live widgets."""
    QApplication.processEvents()
    body = win.properties.widget()
    rows = []
    for form in body.findChildren(QFormLayout):
        top = form.parentWidget()
        box = top.title() if isinstance(top, QGroupBox) else ""
        for r in range(form.rowCount()):
            span = form.itemAt(r, QFormLayout.SpanningRole)
            label_item = None if span else form.itemAt(r, QFormLayout.LabelRole)
            field_item = span or form.itemAt(r, QFormLayout.FieldRole)
            widget = field_item.widget() if field_item else None
            if widget is None or isinstance(widget, QLabel) or not widget.isVisible():
                continue  # (the When box's preview and hint lines)
            inputs = inputs_in(widget)
            if not inputs:
                continue
            label = label_item.widget() if label_item else None
            label = label if isinstance(label, QLabel) and label.text() else None
            text = (box if source_names_box(cls, box) else label.text() if label else
                    widget.text() if isinstance(widget, QCheckBox) else box)
            rows.append(Row(f"{title} › {text}", text, label, inputs, top, field_for(cls, box, text)))
    layout = body.layout()
    for i in range(1, layout.count()):  # code boxes: a label, then the box under it
        widget = layout.itemAt(i).widget()
        if widget is None or isinstance(widget, QGroupBox):
            continue
        editor = widget if isinstance(widget, QPlainTextEdit) else widget.findChild(QPlainTextEdit)
        if editor is None:
            continue
        label = layout.itemAt(i - 1).widget()
        text = label.text() if isinstance(label, QLabel) else ""
        rows.append(Row(f"{title} › {text}", text, label if isinstance(label, QLabel) else None, [editor], body,
                        field_for(cls, "", text)))
        for wrap in widget.findChildren(QCheckBox):  # Wrap lines, under a Python or SQL code box
            rows.append(Row(f"{title} › {wrap.text()}", wrap.text(), None, [wrap], body, None))
    return rows


def row_named(win, label: str, box: str = "", cls=None, title: str = "") -> Row:
    for row in property_rows(win, title or (cls.title if cls else ""), cls):
        top_box = row.top.title() if isinstance(row.top, QGroupBox) else ""
        if row.label == label and top_box == box:
            return row
    raise AssertionError(f"no row {label!r} in {box or 'Properties'}: "
                         f"{[r.label for r in property_rows(win, '', cls)]}")


# --- hovering ------------------------------------------------------------------------------------------------

def hover_target(widget) -> QWidget:
    return widget.viewport() if isinstance(widget, QAbstractScrollArea) else widget


def rest_mouse_on(win, widget):
    """Move the mouse onto `widget` and let Qt's tooltip delay end: the widget under the mouse gets the help event
    (a code box: its text area; a spin box: its text)."""
    target = hover_target(widget)
    hide_native_tips()
    if win.properties.isAncestorOf(widget):
        win.properties.ensureWidgetVisible(widget)
    QApplication.processEvents()
    at = target.rect().center()
    QTest.mouseMove(target, at)
    under = target.childAt(at) or target
    pos = under.mapFrom(target, at) if under is not target else at
    QApplication.sendEvent(under, QHelpEvent(QEvent.ToolTip, pos, under.mapToGlobal(pos)))


def hover(qtbot, win, widget, label: str, where: str) -> QLabel:
    """Rest the mouse on `widget`: the one card it shows, for `label`."""
    rest_mouse_on(win, widget)

    def shown():
        cards = visible_cards(win)
        return len(cards) == 1 and (card_lines(cards[0]) or [""])[0] == label

    try:
        qtbot.waitUntil(shown, timeout=3_000)
    except qtbot.TimeoutError:
        cards = [card_lines(c) for c in visible_cards(win)]
        raise AssertionError(f"{where}: resting the mouse on it shows no card for {label!r} (cards shown: {cards}; "
                             f"native tooltips: {[t.text() for t in native_tips()]})") from None
    return visible_cards(win)[0]


def card_chain(card) -> list[QWidget]:
    """The card's window and its widgets down to `card` (a card that is its own window), as the mouse enters them."""
    top = card.window()
    if isinstance(top, MainWindow):
        return []  # (a card inside the main window gets its Enter and Leave from the moves themselves)
    return [*reversed(list(parents_between(card, None))), card]


def mouse_onto_card(card, at: QPoint = QPoint(4, 4)):
    """Move the mouse onto the card, at `at` in it, as the system does: the cursor goes there (the row's widget is
    left), the card's window gets the Enter and the move. Offscreen, the cursor alone hands the move to the first
    window containing the point: the main window, which the card lies over."""
    QTest.mouseMove(card, at)
    for widget in card_chain(card):
        local = widget.mapFromGlobal(card.mapToGlobal(at))
        QApplication.sendEvent(widget, QEnterEvent(local, local, card.mapToGlobal(at)))
    top = card.window()
    QTest.mouseMove(top.windowHandle(), card.mapTo(top, at))
    QApplication.processEvents()


def mouse_off_card(card, widget):
    """Move the mouse from the card onto `widget` of the main window: the card's window gets the Leave."""
    QTest.mouseMove(widget, widget.rect().center())
    for w in reversed(card_chain(card)):
        QApplication.sendEvent(w, QEvent(QEvent.Leave))
    QApplication.processEvents()


def wait_for(qtbot, condition, message: str, timeout: int = 3_000):
    try:
        qtbot.waitUntil(lambda: bool(condition()), timeout=timeout)
    except qtbot.TimeoutError:
        raise AssertionError(message) from None


def dismiss(qtbot, win):
    """Move the mouse away and wait for any card to go (so the next test starts with none)."""
    hide_native_tips()
    if not visible_cards(win):
        return
    QTest.mouseMove(win.palette_panel.tree.viewport(), win.palette_panel.tree.viewport().rect().center())
    try:
        qtbot.waitUntil(lambda: not visible_cards(win), timeout=3_000)
    except qtbot.TimeoutError:
        pass


def shown_tip(tip: str) -> str:
    """A tooltip as it reads: Qt shows a text that looks like HTML (e.g. help saying <br>) as HTML."""
    if not GuiQt.mightBeRichText(tip):
        return tip
    doc = QTextDocument()
    doc.setHtml(tip)
    return doc.toPlainText()


def effective_tooltip(widget, top) -> str:
    """The tooltip Qt shows for `widget`: its own, else that of the nearest parent (up to `top`) that has one."""
    while widget is not None and widget is not top:
        if widget.toolTip():
            return widget.toolTip()
        widget = widget.parentWidget()
    return ""


def card_problems(row: Row, card: QLabel, target, pages: Pages, drop=()) -> list[str]:
    """What is wrong with the card `target` (a widget of `row`) shows: spec rules 3, 4, 8, 11 and 12."""
    where, out = row.where, []
    if native_tips():
        out.append(f"{where}: a native tooltip shows too: {[t.text() for t in native_tips()]}")
    lines = card_lines(card)
    for part in card_parts(card):
        needs, has = text_width(part)
        if needs > has + 1:  # 1 px for rounding
            out.append(f"{where}: the card's text needs {needs:.0f} px but has {has} px: it is cut off at the card's "
                       "right edge")
    if len(lines) < 4 or lines[-1] != LEARN_MORE:
        return out + [f"{where}: the card is not its label, what it is for, examples and {LEARN_MORE!r}: {lines}"]
    label, what, third = lines[0], lines[1], "\n".join(lines[2:-1])
    if label != row.label:
        out.append(f"{where}: the card's label is {label!r}")
    if not label_is_bold(card):
        out.append(f"{where}: the label is not in bold")
    if not what or what.startswith("No help yet"):
        out.append(f"{where}: no help (says {what!r})")
    if NO_EXAMPLES in third:
        out.append(f"{where}: no examples (says {NO_EXAMPLES!r})")
    if row.field is not None:
        cls, key, f = row.field
        if norm(what) != norm(f.help):
            out.append(f"{where}: says it is for {what!r}, not its help {f.help!r}")
        line = derived_line(f)
        examples = examples_of_field(f)
        if line is not None:
            if third != line:
                out.append(f"{where}: shows {third!r}, not {line!r}")
        elif not third.startswith("Examples:") or not examples:
            out.append(f"{where}: no examples (shows {third!r}; examples= is {examples!r})")
        elif not 1 <= len(examples) <= 3:
            out.append(f"{where}: {len(examples)} examples, not 1 to 3")
        else:
            out += [f"{where}: example {e!r} is not on the card ({third!r})" for e in examples
                    if norm(e) not in norm(third[len("Examples:"):])]
    elif third.startswith("Examples:"):
        if not 1 <= len(listed(third, "Examples:")) <= 3:
            out.append(f"{where}: {len(listed(third, 'Examples:'))} examples, not 1 to 3: {third!r}")
    elif third.startswith("One of:"):
        if not listed(third, "One of:"):
            out.append(f"{where}: 'One of:' lists nothing")
    else:
        out.append(f"{where}: no examples (shows {third!r})")

    tip = row.label_widget.toolTip() if target is row.label_widget else effective_tooltip(target, row.top)
    if norm(shown_tip(tip)) != norm(what):
        out.append(f"{where}: its toolTip() is {tip!r} and reads {shown_tip(tip)!r}, the card says {what!r}")

    connection = row.field is not None and isinstance(row.field[2], fields.Connection)
    if isinstance(target, QComboBox) and third.startswith("One of:") and not connection \
            and row.label not in ("Font", "For"):
        items = [target.itemText(i) for i in range(target.count()) if target.itemText(i) not in ("Choose…", *drop)]
        if listed(third, "One of:") != items:
            out.append(f"{where}: the card lists {listed(third, 'One of:')}, the box offers {items}")

    href = learn_more_href(card)
    if href is None:
        return out + [f"{where}: no Learn more link"]
    name, anchor = page_and_anchor(href)
    page = pages.page(name)
    if page is None:
        out.append(f"{where}: Learn more leads to {href!r}, which is no page")
    elif anchor not in page.ids():
        out.append(f"{where}: Learn more leads to {href!r}: {name} has no {anchor!r}")
    if row.field is not None:
        cls, key, _ = row.field
        if anchor != f"{cls.type_id}.{key}" or not name.startswith("blocks-"):
            out.append(f"{where}: Learn more leads to {href!r}, not blocks-<category>.html#{cls.type_id}.{key}")
    elif name != "properties.html" or not anchor.startswith("properties."):
        out.append(f"{where}: Learn more leads to {href!r}, not properties.html#properties.<row>")
    return out


def check_rows(qtbot, win, rows: list[Row], pages: Pages, drop=()) -> list[str]:
    """Hover each row's label and each of its inputs: the card each shows, and what is wrong with it."""
    problems = []
    for row in rows:
        for target in ([row.label_widget] if row.label_widget is not None else []) + row.inputs:
            card = hover(qtbot, win, target, row.label, row.where)
            problems += card_problems(row, card, target, pages, drop)
    return list(dict.fromkeys(problems))


# --- the Documentation column ------------------------------------------------------------------------------

def documentation_action(win):
    """Help > Documentation: the Help menu comes right after Tools and holds this one item."""
    menus = [a for a in win.menuBar().actions() if a.menu() is not None]
    titles = [a.text().replace("&", "") for a in menus]
    assert "Help" in titles, f"no Help menu: {titles}"
    assert titles.index("Help") == titles.index("Tools") + 1, f"the Help menu is not right after Tools: {titles}"
    items = [a for a in menus[titles.index("Help")].menu().actions() if not a.isSeparator()]
    assert [a.text().replace("&", "") for a in items] == ["Documentation"], [a.text() for a in items]
    return items[0]


def column_shown(win) -> bool:
    dock = docs_dock(win)
    return dock is not None and dock.isVisible()


def open_column(win) -> QTextBrowser:
    if not column_shown(win):
        documentation_action(win).trigger()
        settle()
    assert column_shown(win), "Help > Documentation did not show the Documentation column"
    return browser_of(win)


def page_shown(browser) -> str:
    return browser.source().fileName()


def x_of(widget) -> int:
    return widget.mapToGlobal(QPoint(0, 0)).x()


def nav_button(win, name: str) -> QAbstractButton:
    dock = docs_dock(win)
    found = [b for b in dock.findChildren(QAbstractButton) if b.isVisible()
             and not b.objectName().startswith("qt_dockwidget")
             and name.lower() in f"{b.text()} {b.toolTip()}".lower()]
    assert len(found) == 1, f"no single {name} button in the column: {[(b.text(), b.toolTip()) for b in found]}"
    return found[0]


def anchor_at(browser, name: str) -> QTextCursor | None:
    """A cursor at the anchor `name` of the shown page (None: the page has no such anchor)."""
    doc = browser.document()
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            if name in it.fragment().charFormat().anchorNames():
                cursor = QTextCursor(doc)
                cursor.setPosition(it.fragment().position())
                return cursor
            it += 1
        block = block.next()
    return None


def heading_at_top(browser, name: str) -> bool:
    """The entry's heading line is in the top quarter of the visible area (or as near it as the page scrolls)."""
    cursor = anchor_at(browser, name)
    if cursor is None:
        return False
    y, height = browser.cursorRect(cursor).top(), browser.viewport().height()
    bar = browser.verticalScrollBar()
    return -2 <= y <= max(height / 4, browser.fontMetrics().height()) or (
        bar.value() == bar.maximum() and 0 <= y < height)


def click_link(browser, wanted) -> str:
    """Click, with the mouse, the first link of the shown page for which wanted(href, text) is true."""
    doc = browser.document()
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            href = fragment.charFormat().anchorHref() if fragment.charFormat().isAnchor() else ""
            if href and wanted(href, fragment.text()):
                top = doc.documentLayout().blockBoundingRect(block).top()
                browser.verticalScrollBar().setValue(int(top) - 10)  # scrolled to it, as you would
                settle()
                cursor = QTextCursor(doc)
                cursor.setPosition(fragment.position() + (1 if fragment.length() > 2 else 0))
                rect = browser.cursorRect(cursor)
                at = QPoint(rect.x() + 2, rect.center().y())
                assert browser.anchorAt(at) == href, (href, browser.anchorAt(at), at)
                QTest.mouseClick(browser.viewport(), Qt.LeftButton, Qt.NoModifier, at)
                settle()
                return href
            it += 1
        block = block.next()
    raise AssertionError(f"no such link on {page_shown(browser)}")


def click_learn_more(card: QLabel) -> str:
    """Move the mouse over the card's last line until it is on the Learn more link, and click it; its href."""
    hovered = []
    card.linkHovered.connect(lambda href: hovered.append(href))
    fm, rect = card.fontMetrics(), card.contentsRect()
    step = max(2, fm.averageCharWidth())
    top = card.window()
    mouse_onto_card(card)
    for dy in range(2, 3 * fm.height(), 2):
        for dx in range(1, fm.horizontalAdvance("Learn more"), step):
            at = QPoint(rect.left() + card.margin() + dx, rect.bottom() - card.margin() - dy)
            QTest.mouseMove(top.windowHandle(), card.mapTo(top, at))
            if hovered and hovered[-1]:
                QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, at)
                settle()
                return hovered[-1]
    raise AssertionError(f"the mouse found no Learn more link on the card's last line: {card_lines(card)} (card at "
                         f"{card.window().geometry()}, its screen {card.screen().availableGeometry()})")


class UrlCatcher(QObject):
    """Stands in for the system browser: QDesktopServices hands it the links it opens."""

    def __init__(self):
        super().__init__()
        self.opened = []

    @Slot(QUrl)
    def open(self, url):
        self.opened.append(url.toString())


# --- context for typing examples --------------------------------------------------------------------------------

CONNECTIONS = """\
warehouse: {kind: jdbc, driver: org.h2.Driver, url: "jdbc:h2:mem:test"}
mailer: {kind: smtp, host: 127.0.0.1, port: 2525}
appserver: {kind: ssh, host: 127.0.0.1, user: report}
"""
CONNECTION_OF_KIND = {"jdbc": "warehouse", "smtp": "mailer", "ssh": "appserver"}


def params_used(f, examples) -> dict:
    """The parameters the examples of a templated field (or SQL bind parameters) use, declared as a user would:
    a date where the template gives a date format, else text."""
    found = {}
    for example in examples:
        if f.template:
            try:
                for _, name, spec, _ in string.Formatter().parse(example):
                    if name:
                        found[name] = ({"type": "date", "value": "2026-01-16"} if spec and "%" in spec
                                       else {"type": "text", "value": "east"})
            except ValueError:
                pass  # (validate says what is wrong with it)
        if isinstance(f, fields.Code) and f.language == "sql":
            found.update({name: {"type": "text", "value": "east"} for name in P.to_qmark(example)[1]})
    return found


def type_in_row(row: Row, text: str):
    """Type `text` into the row's input and finish editing (leave it or press Enter), as Properties commits it."""
    widget = row.inputs[0]
    if isinstance(widget, QPlainTextEdit):
        widget.setPlainText(text)
        widget.editing_finished.emit()
    else:
        widget.setText(text)
        widget.editingFinished.emit()
    settle()


def choose(combo: QComboBox, data):
    """Choose the item of a box that holds `data`, as a click on it does."""
    index = combo.findData(data)
    assert index >= 0, (data, [combo.itemText(i) for i in range(combo.count())])
    combo.setCurrentIndex(index)
    combo.activated.emit(index)
    settle()


def problems_in_flow(win, tab, tmp_path) -> list[str]:
    """What validate (and reading the flow) says about the flow as the editor holds it."""
    data = yaml.safe_load(yaml.safe_dump(tab.doc.data, sort_keys=False, allow_unicode=True))
    try:
        errors, warnings = validate(flow_from_data(data, tmp_path / "examples.yaml"), win.registry, win.home)
    except FlowError as e:
        errors, warnings = list(e.errors), []
    return errors + warnings


def add_block(win, tab, title: str) -> str:
    before = set(tab.doc.blocks)
    add_from_palette(win, title)
    (block,) = set(tab.doc.blocks) - before
    select_only(tab, block)
    return block


def connect_into(tab, block: str, cls):
    """A setting that lists the outputs connected into an input (fields.SourceNames) has a row for each one connected
    there: connect the new flow's start, as a user does, so it has one."""
    for f in cls.config.values():
        if isinstance(f, fields.SourceNames):
            drag_port_to_port(tab, "start", "start", block, f.port)
            settle()
            select_only(tab, block)


def setting_row(win, cls, key: str) -> Row:
    """The row a setting's value is typed in: its own, or (fields.SourceNames) the first in its box."""
    label = setting_label(key, cls.config[key])
    return row_named(win, label, label if isinstance(cls.config[key], fields.SourceNames) else "", cls)


def clean_all():
    """Leave no unsaved changes in any editor window: closing them after the test must not ask to save them (what
    is being typed is committed first)."""
    focused = QApplication.focusWidget()
    if focused is not None:
        focused.clearFocus()
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MainWindow):
            clean(widget)


def window_with_blocks(win, qtbot, files: dict) -> MainWindow:
    """Another editor window on the same Taskloom folder, started after these block files were put in its blocks
    folder (it finds them when it starts)."""
    folder = win.home.blocks_dir
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    win.hide()  # (offscreen, the mouse goes to the first window under it: only this one is shown)
    mine = MainWindow(Home(win.home.root))
    qtbot.addWidget(mine)
    mine.show()
    qtbot.waitExposed(mine)
    mine.activateWindow()
    settle()
    return mine


# === B. The card: every Properties row shows one ===================================================================

def test_every_row_of_every_built_in_block_shows_a_card_with_what_it_is_for_examples_and_learn_more(win, qtbot):
    """Spec T1, rules 6, 7, 8, 12, 2, 3, 5: each registry block added from the palette and selected; every row the
    Properties tab builds (found in the live panel: form rows, container inputs, code boxes and their Wrap lines)
    shows one card when its label or any input is hovered, and no native tooltip: bold label, its help, exactly one of
    Examples (1 to 3) / One of (the box's own items) / the Connection or Server line, and Learn more, which leads to an
    anchor of a page of docs.pages(registry); toolTip() still returns what the card says it is for."""
    try:
        pages = Pages(win.registry)
        tab = win.new_flow()
        settle()
        problems, hovered = [], 0
        for cls in list(win.registry.blocks.values()):
            connect_into(tab, add_block(win, tab, cls.title), cls)
            rows = property_rows(win, cls.title, cls)
            assert rows and rows[0].label == "Name", f"{cls.title}: {[r.label for r in rows]}"
            shown = {r.field[1] for r in rows if r.field}
            expected = {k for k in cls.config if not (cls.type_id == "trigger.schedule" and k in WHEN_FIELDS)}
            assert shown == expected, f"{cls.title}: rows found for {sorted(shown)}, settings {sorted(expected)}"
            problems += check_rows(qtbot, win, rows, pages)
            hovered += len(rows)
        dismiss(qtbot, win)
        assert problems == [], "\n".join(problems)
        assert hovered > 100, hovered
    finally:
        clean_all()


def test_every_schedule_keep_runs_and_retry_row_shows_a_card_in_every_choice(win, qtbot):
    """Spec T1 and edge paths: a new Schedule with no timing yet ("Choose…": How often's card lists only its kinds),
    then each How often kind and One time; Keep runs in each choice; Retry in each policy. Rows appear and disappear
    with these, and every visible row gets a card; the choices a card lists are the box's items."""
    try:
        pages = Pages(win.registry)
        tab = win.new_flow()
        settle()
        schedule = win.registry.get("trigger.schedule")
        add_block(win, tab, schedule.title)
        problems, seen = [], set()

        def check(state: str):
            rows = property_rows(win, f"Schedule ({state})", schedule)
            seen.update(r.label for r in rows)
            problems.extend(check_rows(qtbot, win, rows, pages))

        how_often = row_named(win, "How often", "When").inputs[0]
        assert how_often.currentText() == "Choose…"  # no timing yet
        check("no timing yet")
        kinds = [how_often.itemData(i) for i in range(how_often.count()) if how_often.itemData(i)]
        for kind in kinds:
            choose(row_named(win, "How often", "When").inputs[0], data=kind)
            check(f"How often: {kind}")
        choose(row_named(win, "Runs", "When").inputs[0], data="at")
        check("One time")
        keep = row_named(win, "Keep runs", "Keep runs").inputs[0]
        for text in [keep.itemText(i) for i in range(keep.count())]:
            row_named(win, "Keep runs", "Keep runs").inputs[0].setCurrentText(text)
            settle()
            check(f"Keep runs: {text}")
        for policy in ("fixed", "exponential", "none"):
            row_named(win, "Policy", "Retry and timeout").inputs[0].setCurrentText(policy)
            settle()
            check(f"Retry: {policy}")
        dismiss(qtbot, win)
        assert problems == [], "\n".join(problems)
        every_row = {"Runs", "How often", "Every", "Cron line", "At minute", "On", "On day", "At", "For", "Keep runs",
                     "Always keep failed runs", "Policy", "Max attempts", "Delay", "Initial", "Factor", "Max delay",
                     "Jitter", "Timeout"}
        assert every_row <= seen, f"rows never shown: {sorted(every_row - seen)}"
    finally:
        clean_all()


def test_every_row_of_a_text_on_the_canvas_shows_a_card(win, qtbot):
    """Spec T1, rule 6 (the canvas Text form: Text, Style, Size, Bold, Italic, Underline, Font) and rule 4 (Size's
    tooltip keeps "Or drag the corner of the selected text on the canvas" inside its new sentence)."""
    try:
        pages = Pages(win.registry)
        tab = win.new_flow()
        settle()
        win.add_text(tab)
        settle()
        rows = property_rows(win, "Text on the canvas")
        assert [r.label for r in rows] == ["Text", "Style", "Size", "Bold", "Italic", "Underline", "Font"], \
            [r.label for r in rows]
        problems = check_rows(qtbot, win, rows, pages)
        size = hover(qtbot, win, rows[2].inputs[0], "Size", "Text on the canvas › Size")
        what = card_lines(size)[1]
        dismiss(qtbot, win)
        assert problems == [], "\n".join(problems)
        assert "drag the corner of the selected text on the canvas" in what.lower(), what
    finally:
        clean_all()


def test_a_block_whose_type_is_not_installed_shows_only_name_and_its_card_works(win, qtbot, tmp_path):
    """Edge path: a block type that is not installed: Properties shows only Name, and its card works."""
    try:
        pages = Pages(win.registry)
        tab = win.open_flow(flow_file(tmp_path / "older.yaml", {
            "start": {"type": "trigger.manual"}, "legacy": {"type": "gone.export", "config": {"target": "out"}}}))
        settle()
        select_only(tab, "legacy")
        rows = property_rows(win, "gone.export")
        assert [r.label for r in rows] == ["Name"], [r.label for r in rows]
        problems = check_rows(qtbot, win, rows, pages)
        dismiss(qtbot, win)
        assert problems == [], "\n".join(problems)
    finally:
        clean_all()


def test_a_reusable_block_has_cards_on_name_and_retry_only_and_none_on_its_what_it_does_text(win, qtbot):
    """Edge path: a Reusable block shows Name and Retry rows only, each with its card; its "What it does" text is not a
    row and shows no card."""
    try:
        what = "Tidies a list of names: trims and sorts them."
        write_reusable_block(win.home.blocks_dir, "Tidy names", what, "names: a list of names",
                             {"inputs": ["names"], "outputs": ["tidy"], "use_environment": False,
                              "code": "tidy = sorted(names)"})
        mine = window_with_blocks(win, qtbot, {})
        tab = mine.new_flow()
        settle()
        add_block(mine, tab, "Tidy names")
        pages = Pages(mine.registry)
        rows = property_rows(mine, "Tidy names", mine.registry.get("user.tidy_names"))
        assert [r.label for r in rows] == ["Name", "Policy", "Timeout"], [r.label for r in rows]
        problems = check_rows(qtbot, mine, rows, pages)
        dismiss(qtbot, mine)
        (text,) = [w for w in mine.properties.widget().findChildren(QLabel) if w.text() == what]
        rest_mouse_on(mine, text)
        qtbot.wait(500)
        assert visible_cards(mine) == [], f"the What it does text shows a card: {card_lines(visible_cards(mine)[0])}"
        assert problems == [], "\n".join(problems)
    finally:
        clean_all()


def test_values_written_by_hand_and_a_fresh_home_leave_the_choices_and_connection_line_of_the_cards(win, qtbot,
                                                                                                    tmp_path):
    """Edge paths: an every value the box does not offer (7 minutes) is appended to the Every box, and its card still
    lists only the box's own choices; a keep value the box cannot show selects no choice, and the card still lists the
    four; a connection that does not exist is appended as "<name> (missing)" and a fresh home with no connections
    shows "(no connections yet: click New…)": both cards show the rule 3 Connection line."""
    try:
        pages = Pages(win.registry)
        tab = win.open_flow(flow_file(tmp_path / "by-hand.yaml", {
            "nightly": {"type": "trigger.schedule", "config": {"every": "7 minutes", "keep": "whenever"}},
            "load": {"type": "db.run_query", "config": {"connection": "nowhere", "sql": "select 1"}},
            "fresh": {"type": "db.run_query", "config": {"sql": "select 1"}},
        }, ["nightly.start -> load.after", "nightly.start -> fresh.after"]))
        settle()
        schedule, query = win.registry.get("trigger.schedule"), win.registry.get("db.run_query")
        select_only(tab, "nightly")
        every = row_named(win, "Every", "When", schedule)
        assert every.inputs[0].findText("7 minutes") >= 0  # (the box shows the value written by hand)
        keep = row_named(win, "Keep runs", "Keep runs", schedule)
        assert keep.inputs[0].currentIndex() == -1
        problems = check_rows(qtbot, win, [every], pages, drop=("7 minutes",))
        problems += check_rows(qtbot, win, [keep], pages)
        card = hover(qtbot, win, every.inputs[0], "Every", "Schedule › Every")
        assert "7 minutes" not in listed("\n".join(card_lines(card)[2:-1]), "One of:")
        line = "Choose one of your JDBC connections by name, or add one with New…"
        for block, item in (("load", "nowhere (missing)"), ("fresh", "(no connections yet: click New…)")):
            select_only(tab, block)
            row = row_named(win, "Connection", "", query)
            combo = row.inputs[0]
            assert item in [combo.itemText(i) for i in range(combo.count())]
            problems += check_rows(qtbot, win, [row], pages)
            card = hover(qtbot, win, combo, "Connection", f"Run Query ({block}) › Connection")
            assert card_lines(card)[2:-1] == [line], card_lines(card)
        dismiss(qtbot, win)
        assert problems == [], "\n".join(problems)
    finally:
        clean_all()


# --- when the card stays and when it hides ------------------------------------------------------------------------

def copy_files_source(win, tab) -> tuple[str, Row]:
    block = add_block(win, tab, "Copy Files")
    return block, row_named(win, "Source", "", win.registry.get("files.copy"))


def test_the_card_keeps_focus_in_the_field_stays_while_the_mouse_is_on_it_and_hides_after_the_mouse_leaves(win,
                                                                                                         qtbot):
    """Spec T3, rules 7 and 9: showing the card never takes the keyboard focus from the field; it stays while the mouse
    is on the row's widget or on the card (past 300 ms), when the card itself is pressed and when the main window
    merely loses focus; it hides about 300 ms after the mouse has left both."""
    try:
        tab = win.new_flow()
        settle()
        _, source = copy_files_source(win, tab)
        edit = source.inputs[0]
        win.activateWindow()
        qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=3_000)
        edit.setFocus()
        settle()
        assert QApplication.focusWidget() is edit

        card = hover(qtbot, win, edit, "Source", "Copy Files › Source")
        assert QApplication.focusWidget() is edit, f"the card took the focus: {QApplication.focusWidget()}"
        qtbot.wait(600)  # the mouse stays on the field
        assert card.isVisible(), "the card hid while the mouse was on its row"

        mouse_onto_card(card)
        qtbot.wait(600)
        assert card.isVisible(), "the card hid while the mouse was on it"
        QTest.mousePress(card, Qt.LeftButton, Qt.NoModifier, QPoint(4, 4))  # a press on the card itself
        QTest.mouseRelease(card, Qt.LeftButton, Qt.NoModifier, QPoint(4, 4))
        settle()
        assert card.isVisible(), "a press on the card hid it"
        QApplication.sendEvent(edit, QFocusEvent(QEvent.FocusOut, Qt.ActiveWindowFocusReason))  # the window loses focus
        QApplication.sendEvent(win, QEvent(QEvent.WindowDeactivate))
        settle()
        assert card.isVisible(), "the main window losing focus hid the card"

        left = time.monotonic()
        mouse_off_card(card, tab.view.viewport())  # off the row and the card
        settle()
        assert card.isVisible(), "the card hid at once when the mouse left: no time to reach it"
        wait_for(qtbot, lambda: not visible_cards(win), "the card stayed after the mouse left the row and the card")
        assert time.monotonic() - left >= 0.2, "the card hid well before 300 ms"
    finally:
        clean_all()


def test_the_card_hides_at_once_on_esc_typing_a_press_a_rebuild_and_the_application_going_inactive(win, qtbot):
    """Spec T3, rule 9: it hides at once on Esc, a key press or a mouse press in the row's widget, Properties
    rebuilding (another block selected, a value committed that rebuilds it) even while the mouse is on the card (no
    deleted widget is touched afterwards), and applicationStateChanged reporting Inactive."""
    try:
        tab = win.new_flow()
        settle()
        block, source = copy_files_source(win, tab)
        win.activateWindow()
        settle()

        def shown_then(action, why: str):
            edit = row_named(win, "Source", "", win.registry.get("files.copy")).inputs[0]
            edit.setFocus()
            card = hover(qtbot, win, edit, "Source", "Copy Files › Source")
            action(edit, card)
            settle()
            assert not visible_cards(win), f"the card stayed after {why}"

        shown_then(lambda edit, card: QTest.keyClick(edit, Qt.Key_Escape), "Esc")
        shown_then(lambda edit, card: QTest.keyClick(edit, Qt.Key_X), "a key press in the field")
        shown_then(lambda edit, card: QTest.mouseClick(edit, Qt.LeftButton), "a mouse press in the field")
        shown_then(lambda edit, card: QApplication.instance().applicationStateChanged.emit(Qt.ApplicationInactive),
                   "the application went inactive")

        def select_start(edit, card):
            mouse_onto_card(card)  # the mouse is on the card while Properties rebuilds
            select_only(tab, "start")

        shown_then(select_start, "another block was selected")
        qtbot.wait(500)  # (past the grace period: nothing touches the rebuilt panel's deleted widgets)
        settle()

        select_only(tab, block)
        policy = row_named(win, "Policy", "Retry and timeout").inputs[0]
        hover(qtbot, win, policy, "Policy", "Copy Files › Policy")
        policy.setCurrentText("fixed")  # a value committed: Properties rebuilds with the fixed policy's rows
        settle()
        assert not visible_cards(win), "the card stayed after Properties rebuilt"
        qtbot.wait(500)
        settle()
    finally:
        clean_all()


# --- Learn more ---------------------------------------------------------------------------------------------------

def test_learn_more_opens_the_column_right_of_the_inspector_at_the_settings_entry_and_back_returns(win, qtbot):
    """Spec T2, rules 10, 5 and 14: the user's scenario. With Contents open in the column, hover Copy Files' Source
    and click Learn more (the card's last line) with the mouse: the card closes; the column, right of the Inspector,
    shows blocks-files.html#files.copy.source with that heading at the top once the event loop has turned; Back
    returns to Contents."""
    try:
        tab = win.new_flow()
        settle()
        _, source = copy_files_source(win, tab)
        browser = open_column(win)
        assert page_shown(browser) == "index.html"  # the first opening in a session shows Contents

        card = hover(qtbot, win, source.inputs[0], "Source", "Copy Files › Source")
        href = click_learn_more(card)

        assert not visible_cards(win), "the card stayed after Learn more"
        assert page_and_anchor(href) == ("blocks-files.html", "files.copy.source"), href
        dock = docs_dock(win)
        assert dock.isVisible() and not dock.isFloating()
        assert x_of(dock) > x_of(win.inspector), (x_of(dock), x_of(win.inspector))
        assert (page_shown(browser), browser.source().fragment()) == page_and_anchor(href), browser.source()
        wait_for(qtbot, lambda: heading_at_top(browser, "files.copy.source"),
                 "the entry's heading is not at the top of the column once the event loop has turned")

        QTest.mouseClick(nav_button(win, "Back"), Qt.LeftButton)
        settle()
        assert page_shown(browser) == "index.html"
    finally:
        clean_all()


def test_learn_more_opens_a_closed_column_docked_where_it_was_and_shows_a_floating_one(win, qtbot):
    """Spec rule 10 and edge path: Learn more while the column has never been opened, or was closed with its close
    button, opens it docked right of the Inspector; while it floats it stays floating and shows the entry."""
    try:
        pages = Pages(win.registry)
        tab = win.new_flow()
        settle()
        _, source = copy_files_source(win, tab)

        href = click_learn_more(hover(qtbot, win, source.inputs[0], "Source", "Copy Files › Source"))
        dock = docs_dock(win)
        assert column_shown(win) and not dock.isFloating() and x_of(dock) > x_of(win.inspector)
        assert (page_shown(browser_of(win)), browser_of(win).source().fragment()) == page_and_anchor(href)

        close = dock.findChild(QAbstractButton, "qt_dockwidget_closebutton")
        assert close is not None and close.isVisible(), "the column has no close button"
        QTest.mouseClick(close, Qt.LeftButton)
        settle()
        assert not column_shown(win)
        policy = row_named(win, "Policy", "Retry and timeout")
        href = click_learn_more(hover(qtbot, win, policy.inputs[0], "Policy", "Copy Files › Policy"))
        assert column_shown(win) and not dock.isFloating() and x_of(dock) > x_of(win.inspector)
        assert (page_shown(browser_of(win)), browser_of(win).source().fragment()) == page_and_anchor(href)
        assert page_and_anchor(href)[0] == "properties.html" and pages.page("properties.html") is not None

        float_button = dock.findChild(QAbstractButton, "qt_dockwidget_floatbutton")
        assert float_button is not None and float_button.isVisible(), "the column has no float button"
        QTest.mouseClick(float_button, Qt.LeftButton)
        settle()
        assert dock.isFloating()
        timeout = row_named(win, "Timeout", "Retry and timeout")
        href = click_learn_more(hover(qtbot, win, timeout.inputs[0], "Timeout", "Copy Files › Timeout"))
        assert column_shown(win) and dock.isFloating()
        assert (page_shown(browser_of(win)), browser_of(win).source().fragment()) == page_and_anchor(href)
        dock.setFloating(False)
    finally:
        clean_all()


# === C. The Documentation column ===================================================================================

def test_the_column_is_hidden_at_launch_and_help_documentation_and_f1_show_and_hide_it(win, qtbot):
    """Spec T4, rules 13 and 14: a Help menu after Tools with one checkable item, Documentation (F1); the column is
    hidden at launch and at the next launch too (its state is not remembered); it is a dock titled Documentation
    with its own title bar, float and close buttons, right of the Inspector."""
    try:
        action = documentation_action(win)
        assert action.isCheckable()
        assert "F1" in [s.toString() for s in action.shortcuts()], [s.toString() for s in action.shortcuts()]
        assert not column_shown(win) and not action.isChecked()

        action.trigger()
        settle()
        dock = docs_dock(win)
        assert column_shown(win) and action.isChecked()
        assert dock.titleBarWidget() is None, "the column has no visible title bar of its own"
        for name in ("qt_dockwidget_closebutton", "qt_dockwidget_floatbutton"):
            button = dock.findChild(QAbstractButton, name)
            assert button is not None and button.isVisible(), f"no {name}"
        assert x_of(dock) > x_of(win.inspector)
        action.trigger()
        settle()
        assert not column_shown(win) and not action.isChecked()

        tab = win.new_flow()
        win.activateWindow()
        tab.view.setFocus()
        settle()
        QTest.keyClick(tab.view, Qt.Key_F1)
        settle()
        assert column_shown(win), "F1 did not show the column"
        QTest.keyClick(tab.view, Qt.Key_F1)
        settle()
        assert not column_shown(win), "F1 did not hide the column"

        action.trigger()  # left open when the editor closes
        settle()
        assert column_shown(win)
        win.close()
        again = MainWindow(Home(win.home.root))
        qtbot.addWidget(again)
        again.show()
        settle()
        assert not column_shown(again), "the column is open at launch"
    finally:
        clean_all()


def test_back_forward_and_contents_move_through_the_pages_and_reopening_shows_the_last_page(win, qtbot):
    """Spec T4, rules 15 and 18: Back and Forward are disabled with nowhere to go; links between pages open in the
    column; Back, Forward and Contents work; the first opening shows Contents, later openings the last page."""
    try:
        win.new_flow()
        browser = open_column(win)
        back, forward = nav_button(win, "Back"), nav_button(win, "Forward")
        assert page_shown(browser) == "index.html"
        assert not back.isEnabled() and not forward.isEnabled()

        click_link(browser, lambda href, text: page_and_anchor(href)[0] == "schedules.html")
        assert page_shown(browser) == "schedules.html"
        assert back.isEnabled() and not forward.isEnabled()
        QTest.mouseClick(back, Qt.LeftButton)
        settle()
        assert page_shown(browser) == "index.html" and forward.isEnabled()
        QTest.mouseClick(forward, Qt.LeftButton)
        settle()
        assert page_shown(browser) == "schedules.html"
        QTest.mouseClick(nav_button(win, "Contents"), Qt.LeftButton)
        settle()
        assert page_shown(browser) == "index.html"

        click_link(browser, lambda href, text: page_and_anchor(href)[0] == "schedules.html")
        action = documentation_action(win)
        action.trigger()
        settle()
        assert not column_shown(win)
        action.trigger()
        settle()
        assert page_shown(browser_of(win)) == "schedules.html", "reopening did not show the last page"
    finally:
        clean_all()


def test_an_https_link_opens_in_the_system_browser_and_the_column_stays_on_its_page(win, qtbot):
    """Spec T4, rule 18: an http(s) link goes to QDesktopServices (the system browser); the column keeps its page."""
    try:
        win.new_flow()
        browser = open_column(win)
        click_link(browser, lambda href, text: page_and_anchor(href)[0] == "install.html")
        assert page_shown(browser) == "install.html"
        shown = browser.source().toString()
        catcher = UrlCatcher()
        QDesktopServices.setUrlHandler("https", catcher, "open")
        try:
            href = click_link(browser, lambda href, text: href.startswith("https://"))
            wait_for(qtbot, lambda: catcher.opened, "the https link did not reach the system browser")
        finally:
            QDesktopServices.unsetUrlHandler("https")
        assert catcher.opened == [QUrl(href).toString()]
        assert browser.source().toString() == shown
    finally:
        clean_all()


def test_with_the_column_hidden_the_window_has_its_layout_and_open_it_sits_right_of_the_inspector(win, qtbot):
    """Spec T4 and rules 13, 14: at 1440x900 with the column hidden the Blocks, canvas and Inspector widths are those
    of the window before the column was ever shown; open, it is right of the Inspector, which keeps its width, and it
    opens 360 px wide when the canvas has room; in a 1280 px window it still opens, floats and F1 hides it."""
    try:
        win.new_flow()
        settle()
        assert (win.width(), win.height()) == (1440, 900)
        blocks = next(d for d in win.findChildren(QDockWidget) if d.windowTitle() == "Blocks")

        def widths():
            return blocks.width(), win.centralWidget().width(), win.inspector.width()

        before = widths()
        action = documentation_action(win)
        action.trigger()
        settle()
        dock = docs_dock(win)
        assert column_shown(win) and x_of(dock) > x_of(win.inspector)
        if before[1] >= 500:  # (the Windows runner's canvas can be narrow: no room to open it 360 px wide)
            assert abs(dock.width() - 360) <= 30, dock.width()
            assert abs(win.inspector.width() - before[2]) <= 2, (win.inspector.width(), before)
        action.trigger()
        settle()
        assert widths() == before, (widths(), before)

        win.resize(1280, 800)
        settle()
        action.trigger()
        settle()
        assert column_shown(win) and dock.width() > 0 and x_of(dock) > x_of(win.inspector)
        QTest.mouseClick(dock.findChild(QAbstractButton, "qt_dockwidget_floatbutton"), Qt.LeftButton)
        settle()
        assert dock.isFloating() and column_shown(win)
        win.activateWindow()
        win.current.view.setFocus()
        settle()
        QTest.keyClick(win.current.view, Qt.Key_F1)
        settle()
        assert not column_shown(win)
        dock.setFloating(False)
    finally:
        clean_all()


# === User blocks =================================================================================================

LITERAL_HELP = 'Counts rows where this column is < 100 & not "<b>bold</b>"'
LITERAL_EXAMPLES = ["amount", "<total> & tax"]
MY_BLOCKS = f'''
from taskloom import Block, fields, ports


class Tally(Block):
    type_id = "my.tally"
    title = "Tally"
    description = "Counts the rows of a table."
    category = "My Tools"
    inputs = {{"table": ports.Table()}}
    outputs = {{"count": ports.Any()}}
    config = {{
        "column": fields.Text(required=False, help={LITERAL_HELP!r}, examples={LITERAL_EXAMPLES!r}),
        "caption": fields.Text(default="total rows"),
        "mode": fields.Choice(["rows", "distinct"], default="rows"),
    }}

    def run(self, ctx, table):
        return {{"count": 0}}


class Archive(Block):
    type_id = "my.archive"
    title = "Archive Files"
    description = "Moves old files into an archive folder."
    category = "Files"
    config = {{"folder": fields.Text(default="archive", help="Where the files go", examples=["archive", "old/2026"])}}

    def run(self, ctx):
        return {{}}
'''


def user_window(win, qtbot) -> MainWindow:
    mine = window_with_blocks(win, qtbot, {"tally.py": MY_BLOCKS})
    assert mine.registry.get("my.tally") is not None and mine.registry.get("my.archive") is not None, \
        mine.registry.warnings
    return mine


def test_a_user_blocks_setting_with_help_and_examples_shows_them_literally_and_learn_more_opens_its_entry(win, qtbot):
    """Spec T5, rules 1, 8 and 11, edge path (HTML characters): a user block's field with help= and examples= shows
    them on its card, with < and & as written; Learn more opens its generated entry, which shows them too."""
    try:
        mine = user_window(win, qtbot)
        tab = mine.new_flow()
        settle()
        add_block(mine, tab, "Tally")
        tally = mine.registry.get("my.tally")
        column = row_named(mine, "Column", "", tally)
        pages = Pages(mine.registry)
        problems = check_rows(qtbot, mine, [column], pages)
        card = hover(qtbot, mine, column.inputs[0], "Column", "Tally › Column")
        lines = card_lines(card)
        assert lines[1] == LITERAL_HELP, lines
        assert all(e in lines[2] for e in LITERAL_EXAMPLES), lines
        href = click_learn_more(card)

        assert page_and_anchor(href)[1] == "my.tally.column", href
        browser = browser_of(mine)
        assert column_shown(mine) and (page_shown(browser), browser.source().fragment()) == page_and_anchor(href)
        wait_for(qtbot, lambda: heading_at_top(browser, "my.tally.column"),
                 "the entry's heading is not at the top of the column once the event loop has turned")
        entry = norm(pages.page(page_and_anchor(href)[0]).section("my.tally.column"))
        assert LITERAL_HELP in entry and all(e in entry for e in LITERAL_EXAMPLES), entry
        assert problems == [], "\n".join(problems)
    finally:
        clean_all()


def test_a_user_blocks_settings_without_help_or_examples_show_the_hint_and_their_entry_says_what_it_takes(win, qtbot):
    """Spec T5 and rule 11: a field with no help shows the rule 11 hint as what it is for, and "No examples yet" (a
    Choice shows its derived "One of" line instead); Learn more still opens its entry, which shows its type, that it
    is optional, and its default."""
    try:
        mine = user_window(win, qtbot)
        tab = mine.new_flow()
        settle()
        add_block(mine, tab, "Tally")
        tally = mine.registry.get("my.tally")

        caption = row_named(mine, "Caption", "", tally)
        card = hover(qtbot, mine, caption.inputs[0], "Caption", "Tally › Caption")
        assert card_lines(card)[1:] == [NO_HELP, NO_EXAMPLES, LEARN_MORE], card_lines(card)
        mode = row_named(mine, "Mode", "", tally)
        card = hover(qtbot, mine, mode.inputs[0], "Mode", "Tally › Mode")
        assert card_lines(card)[1:] == [NO_HELP, "One of: rows · distinct", LEARN_MORE], card_lines(card)

        card = hover(qtbot, mine, caption.inputs[0], "Caption", "Tally › Caption")
        href = click_learn_more(card)
        assert page_and_anchor(href)[1] == "my.tally.caption", href
        browser = browser_of(mine)
        assert column_shown(mine) and (page_shown(browser), browser.source().fragment()) == page_and_anchor(href)
        entry = norm(Pages(mine.registry).page(page_and_anchor(href)[0]).section("my.tally.caption"))
        assert "Text" in entry and "optional" in entry.lower() and "total rows" in entry, entry
    finally:
        clean_all()


def test_user_blocks_are_in_contents_on_a_page_of_their_own_category_or_on_the_built_in_categorys_page(tmp_path):
    """Spec T5, rule 16 and edge path: Contents lists the user's blocks; a block in a category of its own gets a new
    category page, one in a built-in category (Files) joins that page."""
    folder = tmp_path / "blocks"
    folder.mkdir()
    (folder / "tally.py").write_text(MY_BLOCKS, encoding="utf-8")
    registry = discover(folder)
    assert registry.get("my.tally") is not None and registry.get("my.archive") is not None, registry.warnings
    pages = Pages(registry)
    index = pages.page("index.html")
    assert "Tally" in index.text and "Archive Files" in index.text, norm(index.text)
    (own,) = pages.holding("my.tally")
    assert own.startswith("blocks-") and own not in pages.holding("files.copy"), own
    assert "My Tools" in pages.page(own).text
    assert own in [page_and_anchor(href)[0] for href, _, _ in index.links], "Contents does not link the new page"
    assert pages.holding("my.archive") == pages.holding("files.copy") == ["blocks-files.html"]


def test_a_reusable_block_created_while_the_column_is_open_is_in_contents_and_on_the_user_created_page(win, qtbot,
                                                                                                      tmp_path):
    """Spec T5, rule 19 and edge path: with the column open on Contents, right-click a Python Code block → Create
    Reusable block…; without a restart the column reloads: Contents and the User Created page list the new block and
    its <type_id> anchor resolves."""
    try:
        tab = win.open_flow(names_flow(tmp_path, tmp_path / "out.json"))
        settle()
        browser = open_column(win)
        assert page_shown(browser) == "index.html" and "Tidy names" not in browser.toPlainText()

        in_the_window(win, qtbot, lambda: right_click(qtbot, win, tab, "tidy", "title", CREATE),
                      lambda dialog: (fill_all(dialog, "Tidy names"), press(dialog, "OK")))
        assert win.registry.get("user.tidy_names") is not None

        wait_for(qtbot, lambda: "Tidy names" in browser_of(win).toPlainText(),  # Contents, reloaded
                 "the column did not reload Contents with the new block", timeout=5_000)
        pages = Pages(win.registry)
        (holder,) = pages.holding("user.tidy_names")
        assert holder.startswith("blocks-") and "User Created" in pages.page(holder).text
        click_link(browser_of(win), lambda href, text: page_and_anchor(href)[0] == holder)
        browser = browser_of(win)
        assert page_shown(browser) == holder
        assert "Tidy names" in browser.toPlainText() and anchor_at(browser, "user.tidy_names") is not None
    finally:
        clean_all()


def test_a_reusable_block_created_while_the_column_is_closed_is_in_contents_when_the_column_opens_again(win, qtbot,
                                                                                                         tmp_path):
    """Spec T5, rules 15 and 19: the column is opened on Contents and closed; right-click a Python Code block →
    Create Reusable block…; opening the column again shows the last page, Contents, and it lists the new block, which
    is on its category page at its <type_id> anchor."""
    try:
        tab = win.open_flow(names_flow(tmp_path, tmp_path / "out.json"))
        settle()
        browser = open_column(win)
        assert page_shown(browser) == "index.html" and "Tidy names" not in browser.toPlainText()
        documentation_action(win).trigger()
        settle()
        assert not column_shown(win)

        in_the_window(win, qtbot, lambda: right_click(qtbot, win, tab, "tidy", "title", CREATE),
                      lambda dialog: (fill_all(dialog, "Tidy names"), press(dialog, "OK")))
        assert win.registry.get("user.tidy_names") is not None

        browser = open_column(win)
        assert page_shown(browser) == "index.html"
        assert "Tidy names" in browser.toPlainText(), \
            "the reopened Contents does not list the block created while the column was closed"
        (holder,) = Pages(win.registry).holding("user.tidy_names")
        click_link(browser_of(win), lambda href, text: page_and_anchor(href)[0] == holder)
        assert page_shown(browser_of(win)) == holder and anchor_at(browser_of(win), "user.tidy_names") is not None
    finally:
        clean_all()


# === A. Examples are accepted where they are typed ===============================================================

def test_every_built_in_example_is_accepted_by_its_properties_input_and_by_validate(win, qtbot, tmp_path):
    """Spec T6, rules 1 and 2: each explicit example of a built-in setting, typed into its real Properties input and
    committed (with the context a user would build first: the parameters a templated example uses, a connection of
    the right kind chosen, an output connected into an input whose sources are named, the names first), shows no error
    line, and validate reports nothing naming that setting."""
    try:
        win.home.root.mkdir(parents=True, exist_ok=True)
        (win.home.root / "connections.yaml").write_text(CONNECTIONS, encoding="utf-8")
        tab = win.new_flow()
        settle()
        problems, tried = [], 0
        for cls in list(win.registry.blocks.values()):
            wanted = {k: examples_of_field(f) for k, f in cls.config.items() if needs_examples(cls, k, f)}
            if not wanted:
                continue
            missing = [setting_label(k, cls.config[k]) for k, e in wanted.items() if not e]
            if missing:
                problems.append(f"{cls.title}: no examples= to type for {missing}")
                continue
            block = add_block(win, tab, cls.title)
            params = {}
            for key, examples in wanted.items():
                params.update(params_used(cls.config[key], examples))
            if params:
                tab.doc.set_params({**(tab.doc.data.get("params") or {}), **params})
                settle()
            for key, f in cls.config.items():
                if isinstance(f, fields.Connection) and not isinstance(f, fields.Server):
                    choose(row_named(win, setting_label(key, f), "", cls).inputs[0], data=CONNECTION_OF_KIND[f.kind])
            connect_into(tab, block, cls)
            keys = sorted(wanted, key=lambda k: not isinstance(cls.config[k], (fields.Names, fields.SourceNames)))
            for key in keys:  # the names first; every setting filled, so the settings are read (and checked) whole
                type_in_row(setting_row(win, cls, key), wanted[key][0])
            for key in keys:
                label = setting_label(key, cls.config[key])
                for example in wanted[key]:
                    type_in_row(setting_row(win, cls, key), example)
                    tried += 1
                    if error_shown(win):
                        problems.append(f"{cls.title} › {label}: {example!r} is refused: {error_shown(win)}")
                    said = [m for m in problems_in_flow(win, tab, tmp_path)
                            if f"{block}: {label}:" in m or f"{block}: {label} is required" in m]
                    problems += [f"{cls.title} › {label}: {example!r}: validate says {m}" for m in said]
                type_in_row(setting_row(win, cls, key), wanted[key][0])
        assert problems == [], "\n".join(problems)
        assert tried >= 64, tried
    finally:
        clean_all()


def test_every_example_on_the_card_of_a_typed_properties_row_is_accepted_where_it_is_typed(win, qtbot, tmp_path):
    """Spec T6, rule 4: the examples of the rows that are not settings (as their cards show them) go through the same
    commit paths: Name, the When box's typed rows (At minute, At, On days, On day, Cron line, the one-time On), Keep
    runs' Runs and For (number and unit), the retry values and Timeout. Spin, time and date examples lie within the
    box's range or format; after each, no error line shows and validate says nothing about the block's schedule, keep,
    retry or timeout. A canvas text's Size examples lie within its box's range."""
    try:
        tab = win.new_flow()
        settle()
        schedule = win.registry.get("trigger.schedule")
        add_block(win, tab, schedule.title)
        problems = []

        def block_id():
            return next(b for b, spec in tab.doc.blocks.items() if spec["type"] == "trigger.schedule")

        def examples_on_card(label, box) -> list[str]:
            row = row_named(win, label, box, schedule)
            lines = card_lines(hover(qtbot, win, row.inputs[0], label, f"Schedule › {label}"))
            third = "\n".join(lines[2:-1])
            if not third.startswith("Examples:"):
                problems.append(f"Schedule › {label}: no examples on its card ({third!r})")
                return []
            return listed(third, "Examples:")

        def enter(label, box, example) -> str:
            """Type or set `example` in the row as its widgets take it; why it cannot be ('' when it can)."""
            row = row_named(win, label, box, schedule)
            inputs = row.inputs
            widget = inputs[0]
            if all(isinstance(w, QCheckBox) for w in inputs) and len(inputs) > 1:  # On: the days of the week
                days = [d for d in re.split(r"[,\s]+", example) if d]
                boxes = {w.text(): w for w in inputs}
                if not set(days) <= set(boxes):
                    return f"names days the row does not have ({sorted(boxes)})"
                for day in days:
                    boxes[day].setChecked(True)
                for name, w in boxes.items():
                    if name not in days:
                        w.setChecked(False)
            elif len(inputs) == 2:  # For: a number and a unit
                match = re.fullmatch(r"\s*(\d+)\s*([A-Za-z]+)\s*", example)
                count = next(w for w in inputs if isinstance(w, QAbstractSpinBox))
                unit = next(w for w in inputs if isinstance(w, QComboBox))
                units = [unit.itemText(i) for i in range(unit.count())]
                chosen = [u for u in units if match and u.rstrip("s") == match[2].lower().rstrip("s")]
                if not match or not chosen or not count.minimum() <= int(match[1]) <= count.maximum():
                    return f"is not a number from {count.minimum()} to {count.maximum()} and one of {units}"
                count.setValue(int(match[1]))
                unit.setCurrentText(chosen[0])
            elif isinstance(widget, QTimeEdit):
                when = QTime.fromString(example.strip(), widget.displayFormat())
                if not when.isValid():
                    return f"is not a time as the box shows it ({widget.displayFormat()})"
                widget.setTime(when)
                widget.editingFinished.emit()
            elif isinstance(widget, QDateTimeEdit):
                when = QDateTime.fromString(example.strip(), widget.displayFormat())
                if not when.isValid():
                    return f"is not a date and time as the box shows it ({widget.displayFormat()})"
                widget.setDateTime(when)
                widget.editingFinished.emit()
            elif isinstance(widget, QAbstractSpinBox):
                match = re.match(r"\s*(-?\d+(?:\.\d+)?)", example)
                if not match or not widget.minimum() <= float(match[1]) <= widget.maximum():
                    return f"is not a number from {widget.minimum()} to {widget.maximum()}"
                widget.setValue(type(widget.value())(float(match[1])))
                widget.editingFinished.emit()
            else:
                type_in_row(row, example)
            settle()
            return ""

        def check(label, box):
            for example in examples_on_card(label, box):
                why = enter(label, box, example)
                if why:
                    problems.append(f"Schedule › {label}: {example!r} {why}")
                    continue
                if error_shown(win):
                    problems.append(f"Schedule › {label}: {example!r} is refused in Properties: {error_shown(win)}")
                bid = block_id()
                said = [m for m in problems_in_flow(win, tab, tmp_path)
                        if f"blocks.{bid}:" in m and "required input" not in m or f"{bid}: Keep runs" in m]
                problems.extend(f"Schedule › {label}: {example!r}: validate says {m}" for m in said)

        choose(row_named(win, "How often", "When").inputs[0], data="daily")  # (a schedule needs a time to be valid)
        check("Name", "")
        for kind, labels in (("hourly", ["At minute"]), ("daily", ["At"]), ("weekly", ["On", "At"]),
                             ("monthly", ["On day", "At"]), ("custom", ["Cron line"])):
            choose(row_named(win, "How often", "When").inputs[0], data=kind)
            for label in labels:
                check(label, "When")
        choose(row_named(win, "Runs", "When").inputs[0], data="at")
        check("On", "When")
        for choice, label in (("The last N", "Runs"), ("For a time", "For")):
            row_named(win, "Keep runs", "Keep runs", schedule).inputs[0].setCurrentText(choice)
            settle()
            check(label, "Keep runs")
        for policy, labels in (("fixed", ["Max attempts", "Delay"]),
                               ("exponential", ["Max attempts", "Initial", "Factor", "Max delay", "Jitter"])):
            row_named(win, "Policy", "Retry and timeout").inputs[0].setCurrentText(policy)
            settle()
            for label in labels:
                check(label, "Retry and timeout")
        check("Timeout", "Retry and timeout")

        win.add_text(tab)
        settle()
        size = row_named(win, "Size", "").inputs[0]
        card = hover(qtbot, win, size, "Size", "Text on the canvas › Size")
        for example in listed("\n".join(card_lines(card)[2:-1]), "Examples:"):
            match = re.match(r"\s*(\d+(?:\.\d+)?)", example)
            if not match or not size.minimum() <= float(match[1]) <= size.maximum():
                problems.append(f"Text on the canvas › Size: {example!r} is not {size.minimum()} to {size.maximum()}")
        dismiss(qtbot, win)
        assert problems == [], "\n".join(problems)
    finally:
        clean_all()


def test_every_field_type_takes_examples_and_built_in_settings_have_help_and_one_to_three():
    """Spec rules 1 and 2: fields.Field(..., examples=()) on every Field type (user blocks and plugins can set it);
    every built-in setting has help; every one outside rule 3 and 3a has 1 to 3 examples, each a non-empty string."""
    made = {
        "Text": fields.Text(examples=("a",)), "Int": fields.Int(examples=("a",)),
        "Float": fields.Float(examples=("a",)),
        "Bool": fields.Bool(default=False, examples=("a",)), "Choice": fields.Choice(["a"], examples=("a",)),
        "Code": fields.Code("python", examples=("a",)), "Path": fields.Path(examples=("a",)),
        "Connection": fields.Connection("ssh", examples=("a",)), "Server": fields.Server(examples=("a",)),
        "List": fields.List(examples=("a",)), "Names": fields.Names(examples=("a",)),
        "KeptRuns": KeptRuns(examples=("a",)),
    }
    assert {name: tuple(f.examples) for name, f in made.items() if tuple(f.examples) != ("a",)} == {}
    assert tuple(fields.Text().examples) == ()

    problems = []
    for cls in builtin_blocks():
        for key, f in cls.config.items():
            where = f"{cls.title} › {setting_label(key, f)}"
            if not f.help.strip():
                problems.append(f"{where}: no help")
            examples = examples_of_field(f)
            if needs_examples(cls, key, f) and not (
                    1 <= len(examples) <= 3 and all(isinstance(e, str) and e.strip() for e in examples)):
                problems.append(f"{where}: examples= is {examples!r}, not 1 to 3 strings")
    assert problems == [], "\n".join(problems)


# === T7: the pages' links and entries ==============================================================================

def test_every_link_and_anchor_in_the_pages_resolves_and_every_built_in_anchor_exists():
    """Spec T7 and rule 5: every internal href resolves to a page and an anchor there; anchors are unique per page;
    every built-in <type_id> and <type_id>.<key> is on a blocks-<category>.html page, and every properties.<ROWS id>
    on properties.html; links that leave the docs (README's too) are absolute http(s)."""
    pages = Pages(discover(None))
    assert "index.html" in pages.get() and "properties.html" in pages.get(), sorted(pages.get())
    problems = []
    for name in pages.get():
        page = pages.page(name)
        ids = page.ids()
        problems += [f"{name}: {i!r} is there {ids.count(i)} times" for i in sorted(set(ids)) if ids.count(i) > 1]
        for href, _, text in page.links:
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            target, anchor = urldefrag(href)
            target = target or name
            if pages.page(target) is None:
                problems.append(f"{name}: the link {href!r} ({text.strip()}) leads to no page")
            elif anchor and anchor not in pages.page(target).ids():
                problems.append(f"{name}: the link {href!r} ({text.strip()}): {target} has no {anchor!r}")
    for cls in builtin_blocks():
        holders = pages.holding(cls.type_id)
        if len(holders) != 1 or not holders[0].startswith("blocks-"):
            problems.append(f"{cls.type_id}: on {holders}, not on one blocks-<category>.html page")
            continue
        ids = pages.page(holders[0]).ids()
        problems += [f"{holders[0]}: no {cls.type_id}.{key}" for key in cls.config if f"{cls.type_id}.{key}" not in ids]
    properties = pages.page("properties.html").ids()
    problems += [f"properties.html: no properties.{row}" for row in docs().ROWS
                 if f"properties.{row}" not in properties]
    assert problems == [], "\n".join(problems)


def test_every_built_in_block_and_setting_entry_says_what_it_is_and_what_it_takes():
    """Spec T7 and rule 17: a block's entry shows its title, type id, description, what its inputs expect and its
    inputs and outputs; each setting's entry its label, flow-file key, help, required or optional, and its examples,
    its "One of" / Connection / Server line, or (cron, every, at) links to the When-box rows that set it; Schedule and
    Manual Start link to the Properties panel page."""
    registry = discover(None)
    pages = Pages(registry)
    rows = docs().ROWS
    problems = []
    for cls in builtin_blocks():
        holders = pages.holding(cls.type_id)
        if len(holders) != 1:
            problems.append(f"{cls.type_id}: on {holders}")
            continue
        page = pages.page(holders[0])

        def entry_ends(other, cls=cls):  # the next setting's or the next block's entry
            return other.startswith(cls.type_id + ".") or other in registry.blocks

        head = norm(page.section(cls.type_id, until=entry_ends))
        for what, text in (("title", cls.title), ("type id", cls.type_id), ("description", cls.description),
                           ("inputs help", cls.inputs_help)):
            if text and norm(text) not in head:
                problems.append(f"{cls.type_id}: its entry does not show its {what} {text!r}")
        try:
            inputs, outputs = cls.ports({k: f.default for k, f in cls.config.items()})
        except Exception:
            inputs, outputs = cls.inputs, cls.outputs
        problems += [f"{cls.type_id}: its entry does not show its port {p!r}" for p in [*inputs, *outputs]
                     if p not in ("after", "done") and p not in head]
        start, end = page.span(cls.type_id, until=lambda other: not other.startswith(cls.type_id + "."))
        when_links = set()
        for key, f in cls.config.items():
            anchor, label = f"{cls.type_id}.{key}", setting_label(key, f)
            if anchor not in page.ids():
                continue  # (the anchors test says so)
            entry = norm(page.section(anchor, until=entry_ends))
            where = f"{cls.type_id}.{key}"
            for what, text in (("label", label), ("key", key), ("help", f.help)):
                if norm(text) not in entry:
                    problems.append(f"{where}: its entry does not show its {what} {text!r}")
            if ("required" if f.required else "optional") not in entry.lower():
                problems.append(f"{where}: its entry does not say it is {'required' if f.required else 'optional'}")
            if cls.type_id == "trigger.schedule" and key in WHEN_FIELDS:
                links = page.links_in(*page.span(anchor, until=entry_ends))
                when_links.update(links)
                linked = {rows.get(page_and_anchor(h)[1].removeprefix("properties."), ("",))[0] for h in links
                          if page_and_anchor(h)[0] == "properties.html"}
                if not WHEN_ROWS[key] <= linked:
                    problems.append(f"{where}: its entry links to {sorted(linked)}, not to {sorted(WHEN_ROWS[key])}")
            elif derived_line(f) is not None:
                if norm(derived_line(f)) not in entry:
                    problems.append(f"{where}: its entry does not show {derived_line(f)!r}")
            elif "Examples" not in entry or not examples_of_field(f) or any(
                    norm(e) not in entry for e in examples_of_field(f)):
                problems.append(f"{where}: its entry does not show its examples {examples_of_field(f)!r}")
        if cls.type_id in ("trigger.manual", "trigger.schedule"):
            to_panel = [h for h in page.links_in(start, end)
                        if page_and_anchor(h)[0] == "properties.html" and h not in when_links]
            if not to_panel:
                problems.append(f"{cls.type_id}: its entry links to no section of the Properties panel page")
    assert problems == [], "\n".join(problems)


def test_the_pages_set_no_text_or_background_colour_of_their_own():
    """Spec rule 19: text and background colours are not hard-coded, so the pages read in dark mode too."""
    pages = Pages(discover(None))
    assert {name: pages.page(name).colours for name in pages.get() if pages.page(name).colours} == {}


# === T8: README is the source of the guide =========================================================================

def test_the_guide_is_readme_section_by_section_in_readme_order():
    """Spec T8, rules 5 and 16: every README "## " section is a guide page <GitHub slug>.html, linked from Contents in
    README order; its heading has the slug as its id; its text is README's (a sentence of each section, among them the
    Schedules section, is on its page)."""
    _, sections = readme_parts()
    expected = [slug(heading) + ".html" for heading, _ in sections]
    assert "schedules.html" in expected
    pages = Pages(discover(None))
    assert [name for name in expected if name not in pages.get()] == []
    linked = []
    for href, _, _ in pages.page("index.html").links:
        name = page_and_anchor(href)[0]
        if name in expected and name not in linked:
            linked.append(name)
    assert linked == expected
    problems = []
    for (heading, lines), name in zip(sections, expected):
        page = pages.page(name)
        if slug(heading) not in page.ids():
            problems.append(f"{name}: its heading has no id {slug(heading)!r}")
        if norm(heading) not in norm(page.text):
            problems.append(f"{name}: no heading {heading!r}")
        sentence = plain_line(lines)
        if sentence and norm(sentence) not in norm(page.text):
            problems.append(f"{name}: README's {sentence!r} is not on it")
    assert plain_line(dict(sections)["Schedules"]), "no plain sentence in README's Schedules section"
    assert problems == [], "\n".join(problems)


def test_contents_begins_with_readmes_introduction_then_the_guide_and_the_reference():
    """Spec T8 and rule 16: Contents begins with README's introduction (the text before its first "## "), then the
    guide pages, then the reference: the Properties panel page (grouped as Every block, Schedule: When, Keep runs, Code
    boxes and Text on the canvas) and one page per block category."""
    intro, sections = readme_parts()
    pages = Pages(discover(None))
    index = pages.page("index.html")
    guide = {slug(heading) + ".html" for heading, _ in sections}
    first_guide_link = min(at for href, at, _ in index.links if page_and_anchor(href)[0] in guide)
    before = norm(index.text[:first_guide_link])
    for line in (line for line in intro if plain_line([line])):
        assert norm(line) in before, f"README's introduction {line!r} is not at the start of Contents: {before!r}"
    linked = {page_and_anchor(href)[0] for href, _, _ in index.links}
    categories = {name for name in pages.get() if name.startswith("blocks-")}
    assert categories and "properties.html" in linked and categories <= linked, (sorted(categories), sorted(linked))
    panel = norm(pages.page("properties.html").text)
    for group in ("Every block", "Schedule: When", "Keep runs", "Code boxes", "Text on the canvas"):
        assert group in panel, f"the Properties panel page has no {group!r} group"


def test_readme_tells_about_the_card_the_column_and_examples():
    """Spec rule 20 (and 1): "The editor" describes the hover card and Help > Documentation (F1); "Writing a block"
    shows help= and examples=."""
    _, sections = readme_parts()
    text = {heading: "\n".join(lines) for heading, lines in sections}
    assert "F1" in text["The editor"] and "Documentation" in text["The editor"]
    assert "help=" in text["Writing a block"] and "examples=" in text["Writing a block"]


NO_README = r'''
import json, sys
from taskloom.editor import docs
from taskloom.registry import discover
pages = docs.pages(discover(None))
print(json.dumps({"module": docs.__file__, "qt": sorted(m for m in sys.modules if m.startswith("PySide6")),
                  "pages": pages}))
'''


def test_without_readme_contents_says_the_guide_is_not_included_and_the_reference_still_works(tmp_path):
    """Spec rules 4 and 22, edge path: an installation without README.md (a broken install): Contents says the guide
    is not included, the reference pages are there with their anchors, nothing crashes; and building the pages
    needs no Qt."""
    shutil.copytree(REPO / "taskloom", tmp_path / "taskloom",
                    ignore=shutil.ignore_patterns("README.md", "__pycache__"))
    env = {**os.environ, "TASKLOOM_HOME": str(tmp_path / "home"),
           "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    done = subprocess.run([sys.executable, "-c", NO_README], cwd=tmp_path, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout.splitlines()[-1])
    assert Path(result["module"]).resolve().is_relative_to(tmp_path.resolve()), result["module"]
    assert result["qt"] == [], f"building the pages loaded Qt: {result['qt']}"
    pages = result["pages"]
    assert "not included" in norm(Page(pages["index.html"]).text).lower(), Page(pages["index.html"]).text
    assert any(i.startswith("properties.") for i in Page(pages["properties.html"]).ids())
    holders = [name for name, page in pages.items() if "files.copy.source" in Page(page).ids()]
    assert holders == ["blocks-files.html"], holders
