"""A table's look in an HTML Template, and {name:html} (docs/promises/table-styles.yaml, STY-1..7): a rule in the
template's own <style> wins over the table's built-in look, property by property, with no !important; what it does not
set keeps the built-in look; with no <style> the table is as it was; {name:html} puts text in as HTML, with the <style>
block inside it applied, and stops the run on anything that is not text; the template's rules style what the template
shows, not the email's own frame. Through the real `taskloom run`: a table (or
the text) into an HTML Template into Send Email and a local mail server, then the email's HTML. An element is judged
by its style as it applies (the last declaration of a property wins, and a shorthand such as `border` replaces what
was set before it), never by the text of its style attribute, so the built-in look and the template's rules may be
combined in any way."""

import re
import threading
from html import unescape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from tests.test_paths_reports_email import errors, html_of, inline_images

MAIL = {"type": "email.send", "config": {"connection": "mailer", "to": ["team@example.com"], "subject": "Report"}}
CHART = {"type": "reports.chart", "config": {"kind": "bar", "x": "region", "y": ["stores"]}}

# A small table with text, numbers, a date and empty values.
SALES = '''\
import datetime as dt
table = pl.DataFrame({"region": ["EU <north>", "US", "APAC"], "stores": [1234567, 2, None],
                      "total": [1234.5, 0.25, None], "opened": [dt.date(2026, 10, 2), None, dt.date(2026, 1, 31)]})
'''
COLUMNS = ["region", "stores", "total", "opened"]
# As the email shows it: each row's background, and each of its cells' (text, alignment).
SHOWN = [
    ("#ffffff", [("EU <north>", "left"), ("1,234,567", "right"), ("1,234.50", "right"), ("2026-10-02", "left")]),
    ("#f3f6f9", [("US", "left"), ("2", "right"), ("0.25", "right"), ("", "left")]),
    ("#ffffff", [("APAC", "left"), ("", "left"), ("", "left"), ("2026-01-31", "left")]),
]

# The built-in look, recorded on 2026-11-11 from the email as it was before a template's own rules could win.
DARK = "#2c5d8f"
TABLE_LOOK = "border-collapse: collapse; font-family: Arial, Helvetica, sans-serif; font-size: 13px; color: #1c2530"
HEAD_LOOK = ("font-family: Arial, Helvetica, sans-serif; border: 1px solid #d5dce4; padding: 6px 10px; "
             "background: #2c5d8f; color: #ffffff; font-weight: bold; text-align: left; white-space: nowrap")
CELL_LOOK = "font-family: Arial, Helvetica, sans-serif; border: 1px solid #d5dce4; padding: 5px 10px; text-align: {}"

# STY-1's example, as the Template holds it (braces doubled).
TD_RULE = "<style>td {{padding: 15px; border: 1px solid black; font-size: 15px}}</style>"

# Text holding HTML, in the shape pandas gives for df.style.apply(colour, axis=1).to_html() where colour paints the
# first row green: a <style> block with one rule for the painted cells' ids, then the table.
STYLED = '''\
<style type="text/css">
#T_5f2c1_row0_col0, #T_5f2c1_row0_col1 {
  background-color: green;
}
</style>
<table id="T_5f2c1">
  <thead>
    <tr>
      <th class="blank level0" >&nbsp;</th>
      <th id="T_5f2c1_level0_col0" class="col_heading level0 col0" >region</th>
      <th id="T_5f2c1_level0_col1" class="col_heading level0 col1" >total</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th id="T_5f2c1_level0_row0" class="row_heading level0 row0" >0</th>
      <td id="T_5f2c1_row0_col0" class="data row0 col0" >EU</td>
      <td id="T_5f2c1_row0_col1" class="data row0 col1" >10</td>
    </tr>
    <tr>
      <th id="T_5f2c1_level0_row1" class="row_heading level0 row1" >1</th>
      <td id="T_5f2c1_row1_col0" class="data row1 col0" >US</td>
      <td id="T_5f2c1_row1_col1" class="data row1 col1" >20</td>
    </tr>
  </tbody>
</table>
'''
STYLED_CELLS = [["\xa0", "region", "total"], ["0", "EU", "10"], ["1", "US", "20"]]


# --- flows ---------------------------------------------------------------------------------

def python(code: str, outputs=("output",)) -> dict:
    return {"type": "logic.python", "config": {"inputs": [], "outputs": list(outputs), "code": code}}


def template(text: str, names: dict | None = None) -> dict:
    """An HTML Template block; `names` as the flow file holds them ({"block.port": name})."""
    return {"type": "reports.html_template", "config": {"template": text, **({"names": names} if names else {})}}


def run(env, blocks: dict, edges, **top):
    path = env.root / "flow.yaml"
    path.write_text(yaml.safe_dump({**top, "blocks": blocks, "edges": list(edges)}, sort_keys=False),
                    encoding="utf-8")
    return env.taskloom("run", path)


def sent(result, smtp):
    """The one email of a run that succeeded."""
    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    return msg


def table_email(env, smtp, text: str) -> str:
    """The HTML of the email sent with the table, named my_table, in an HTML Template holding `text`."""
    return html_of(sent(run(env, {"data": python(SALES, ["table"]), "body": template(text, {"data.table": "my_table"}),
                                  "mail": MAIL}, ["data.table -> body.data", "body.html -> mail.body"]), smtp))


def styled_email(env, smtp, text: str) -> str:
    """The HTML of the email sent with the text holding HTML, named styled, in an HTML Template holding `text`."""
    return html_of(sent(run(env, {"styled": python(f"output = {STYLED!r}"), "body": template(text), "mail": MAIL},
                            ["styled.output -> body.data", "body.html -> mail.body"]), smtp))


# --- the email's HTML, read as a mail program reads it ---------------------------------------

SIDES = ("top", "right", "bottom", "left")
LINE_STYLES = {"none", "hidden", "dotted", "dashed", "solid", "double", "groove", "ridge", "inset", "outset"}
COLOUR_NAMES = {"black": "#000000", "white": "#ffffff", "green": "#008000", "red": "#ff0000"}
LOOK_ATTRIBUTES = ("align", "bgcolor", "border", "cellpadding", "cellspacing", "height", "valign", "width")
VOID = {"br", "col", "hr", "img", "input", "link", "meta"}


def _colour(value: str) -> str:
    value = COLOUR_NAMES.get(value, value)
    return "#" + "".join(2 * c for c in value[1:]) if re.fullmatch(r"#[0-9a-f]{3}", value) else value


def _length(value: str) -> str:
    return "0" if re.fullmatch(r"0[a-z%]*", value) else value


def _per_side(values: list) -> list:
    """A shorthand's one to four values as top, right, bottom, left."""
    return {1: values * 4, 2: values * 2, 3: values + values[1:2], 4: values}[len(values)]


def _line(value: str) -> dict:
    """The width, style and colour a border shorthand sets; what it leaves out goes back to CSS's initial value."""
    line = {"width": "medium", "style": "none", "color": "currentcolor"}
    for part in value.split():
        if part in LINE_STYLES:
            line["style"] = part
        elif part[0].isdigit() or part[0] == "." or part in ("thin", "medium", "thick"):
            line["width"] = _length(part)
        else:
            line["color"] = _colour(part)
    return line


def effective(style: str) -> dict:
    """{property: value} of a style attribute as it applies: a later declaration of a property wins; padding, margin
    and border are held per side (border per side as width, style and colour) and `background` as background-color,
    so a shorthand replaces what was set before it and a single property after a shorthand replaces only its part."""
    found = {}
    for declaration in style.split(";"):
        name, _, value = declaration.partition(":")
        name, value = name.strip().lower(), " ".join(value.split()).lower()
        border = re.fullmatch(r"border(?:-(top|right|bottom|left))?", name)
        part = re.fullmatch(r"border-(width|style|color)", name)
        if not name:
            continue
        if border:
            for side in [border.group(1)] if border.group(1) else SIDES:
                found.update({f"border-{side}-{k}": v for k, v in _line(value).items()})
        elif part:
            values = [_colour(v) if part.group(1) == "color" else _length(v) for v in value.split()]
            found.update({f"border-{side}-{part.group(1)}": v for side, v in zip(SIDES, _per_side(values))})
        elif name in ("padding", "margin"):
            values = [_length(v) for v in value.split()]
            found.update({f"{name}-{side}": v for side, v in zip(SIDES, _per_side(values))})
        elif name == "background":
            found = {k: v for k, v in found.items() if not k.startswith("background-")}
            found["background-color"] = _colour(value)
        else:
            found[name] = _colour(value) if name.endswith("color") else _length(value)
    return found


class Element:
    def __init__(self, tag: str, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    @property
    def text(self) -> str:
        """The text it shows, with that of the elements inside it."""
        return "".join(c if isinstance(c, str) else c.text for c in self.children)

    @property
    def style(self) -> dict:
        return effective(self.attrs.get("style") or "")

    def all(self, *tags) -> list:
        """The elements inside it with one of these tags, in the order they are written."""
        found = []
        for child in self.children:
            if isinstance(child, Element):
                found += ([child] if child.tag in tags else []) + child.all(*tags)
        return found


class _Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = self.at = Element("", (), None)

    def handle_starttag(self, tag, attrs):
        element = Element(tag, attrs, self.at)
        self.at.children.append(element)
        if tag not in VOID:
            self.at = element

    def handle_endtag(self, tag):
        element = self.at
        while element.parent is not None and element.tag != tag:
            element = element.parent
        if element.parent is not None:
            self.at = element.parent

    def handle_data(self, data):
        self.at.children.append(data)


def document(html: str) -> Element:
    parser = _Document()
    parser.feed(html)
    parser.close()
    return parser.root


def my_table(html: str) -> Element:
    """The table the email shows for {my_table}: the one whose header cells are its columns."""
    [table] = [t for t in document(html).all("table")
               if not t.all("table") and [th.text for th in t.all("th")] == COLUMNS]
    return table


def styled_table(html: str) -> Element:
    """The <table id=...> element of the text holding HTML, with its cells' texts as written."""
    tables = [t for t in document(html).all("table") if t.attrs.get("id") == "T_5f2c1"]
    assert len(tables) == 1, f"{len(tables)} <table id=\"T_5f2c1\"> elements in the email: {html}"
    assert [[c.text for c in row.all("th", "td")] for row in tables[0].all("tr")] == STYLED_CELLS
    return tables[0]


def look(table: Element) -> list:
    """Each element of a table as it looks: (tag, its attributes that set a look, its style as it applies, a cell's
    text)."""
    return [(e.tag, {k: v for k, v in e.attrs.items() if k in LOOK_ATTRIBUTES}, e.style,
             e.text if e.tag in ("th", "td") else None) for e in [table] + table.all("tr", "th", "td")]


def look_as_it_was() -> list:
    """The table of SALES as the email showed it on 2026-11-11, in the form `look` gives."""
    cells = effective(CELL_LOOK)
    assert cells["padding-top"] == "5px" and cells["padding-left"] == "10px" and cells["border-top-color"] == "#d5dce4"
    found = [("table", {"cellpadding": "0", "cellspacing": "0", "border": "0"}, effective(TABLE_LOOK), None),
             ("tr", {}, {}, None)]
    found += [("th", {"align": "left", "bgcolor": DARK}, effective(HEAD_LOOK), column) for column in COLUMNS]
    for background, row in SHOWN:
        found.append(("tr", {"bgcolor": background}, {"background-color": background}, None))
        found += [("td", {"align": align}, effective(CELL_LOOK.format(align)), text) for text, align in row]
    return found


def borders(style: dict) -> list:
    return [tuple(style.get(f"border-{side}-{part}") for part in ("width", "style", "color")) for side in SIDES]


def paddings(style: dict) -> list:
    return [style.get(f"padding-{side}") for side in SIDES]


def cells_with_the_td_rule(html: str) -> list:
    """The table's data cells, each with what TD_RULE sets: 15px padding, a 1px solid black border, 15px text."""
    assert "!important" not in html.lower()
    cells = my_table(html).all("td")
    assert [td.text for td in cells] == [text for _, row in SHOWN for text, _ in row]
    for td in cells:
        style = td.style
        assert paddings(style) == ["15px"] * 4, f"padding of the cell {td.text!r}: {td.attrs.get('style')}"
        assert borders(style) == [("1px", "solid", "#000000")] * 4, f"border of {td.text!r}: {td.attrs.get('style')}"
        assert style.get("font-size") == "15px", f"text size of the cell {td.text!r}: {td.attrs.get('style')}"
    return cells


def background_shown(td: Element) -> str | None:
    """The background a cell shows: its own, else its row's."""
    row = td.parent
    return td.style.get("background-color") or row.style.get("background-color") or row.attrs.get("bgcolor")


# --- STY-1: a rule in the template's own <style> wins, property by property, without !important ---------------------

@pytest.mark.invariant("STY-1")
def test_a_td_rule_in_the_templates_own_style_gives_every_cell_its_padding_border_and_text_size(env, smtp):
    html = table_email(env, smtp, TD_RULE + "{my_table}")

    assert len(cells_with_the_td_rule(html)) == 12


@pytest.mark.invariant("STY-1")
@pytest.mark.invariant("STY-2")
def test_a_th_rule_changes_only_what_it_sets_in_the_header_and_the_cells_keep_the_built_in_look(env, smtp):
    html = table_email(env, smtp, "<style>th {{background-color: #444444; padding: 12px}}</style>{my_table}")

    assert "!important" not in html.lower()
    table = my_table(html)
    for th in table.all("th"):
        assert (th.style.get("background-color"), paddings(th.style)) == ("#444444", ["12px"] * 4), (
            f"the header cell {th.text!r}: {th.attrs.get('style')}")
        assert th.style == {**effective(HEAD_LOOK), **effective("background-color: #444444; padding: 12px")}
    assert [(td.text, td.style) for td in table.all("td")] == [
        (text, effective(CELL_LOOK.format(align))) for _, row in SHOWN for text, align in row]


# --- STY-2: what you do not set keeps the built-in look -------------------------------------------------------------

@pytest.mark.invariant("STY-2")
def test_with_the_td_rule_the_header_stays_dark_with_white_bold_text_rows_striped_numbers_right_and_text_left(env,
                                                                                                             smtp):
    html = table_email(env, smtp, TD_RULE + "{my_table}")

    cells = cells_with_the_td_rule(html)  # the example: its rule is in effect, so what follows is what it left alone
    table = my_table(html)
    assert [(th.text, th.style) for th in table.all("th")] == [(column, effective(HEAD_LOOK)) for column in COLUMNS]
    assert [(td.text, background_shown(td), td.style.get("text-align")) for td in cells] == [
        (text, background, align) for background, row in SHOWN for text, align in row]
    assert all(td.style.get("font-family") == "arial, helvetica, sans-serif" for td in cells)


# --- STY-3: with no <style> of your own, the table is as it is today ------------------------------------------------

@pytest.mark.invariant("STY-3")
def test_with_no_style_of_your_own_a_table_in_a_template_looks_as_it_did(env, smtp):
    html = table_email(env, smtp, "Hi team,<br>{my_table}<br>thank you")

    assert look(my_table(html)) == look_as_it_was()


@pytest.mark.invariant("STY-3")
def test_a_table_connected_straight_to_send_emails_body_looks_as_it_did(env, smtp):
    result = run(env, {"data": python(SALES, ["table"]), "mail": MAIL}, ["data.table -> mail.body"])

    assert look(my_table(html_of(sent(result, smtp)))) == look_as_it_was()


@pytest.mark.invariant("STY-3")
def test_a_table_shown_whole_puts_only_its_header_and_cells_between_the_words_around_it(env, smtp):
    html = table_email(env, smtp, "above{my_table}below")

    around = html[html.index("above"):html.index("below") + len("below")]
    texts = [unescape(text) for text in re.split(r"<[^>]*>", around) if text]
    assert texts == ["above", *COLUMNS, *(text for _, row in SHOWN for text, _ in row if text), "below"], around


# --- STY-4: {name:html} puts text in as HTML, as it is; {name} shows its tags as text -------------------------------

@pytest.mark.invariant("STY-4")
def test_name_html_puts_text_holding_html_in_as_it_is(env, smtp):
    html = styled_email(env, smtp, "Scores<br>{styled:html}<br>thank you")

    styled_table(html)
    assert "&lt;" not in html


@pytest.mark.invariant("STY-4")
def test_name_alone_shows_the_tags_of_text_holding_html_as_text(env, smtp):
    html = styled_email(env, smtp, "Scores<br>{styled}<br>thank you")

    email = document(html)
    assert [e.tag for e in email.all("table", "th", "td")] == ["table", "td"]  # only the email's own layout
    for written in ('<style type="text/css">', "background-color: green;", '<table id="T_5f2c1">',
                    '<td id="T_5f2c1_row0_col0" class="data row0 col0" >EU</td>', "</table>"):
        assert written in email.text, (written, email.text)
    assert "&lt;table" in html


# --- STY-5: the <style> block inside that HTML is applied and moved into each cell's style="" -----------------------

@pytest.mark.invariant("STY-5")
def test_the_style_block_inside_the_html_colours_its_cells_in_their_style_attributes_and_is_not_left(env, smtp):
    html = styled_email(env, smtp, "Scores<br>{styled:html}<br>thank you")

    assert {td.text: td.style.get("background-color") for td in styled_table(html).all("td")} == {
        "EU": "#008000", "10": "#008000", "US": None, "20": None}
    assert "<style" not in html.lower()


# --- STY-6: {name:html} on something that is not text stops the run -------------------------------------------------

def only_to_text(placeholder: str) -> str:
    return f"Template: {placeholder}: html applies only to text"


def says(error: str, words: str) -> bool:
    """Whether a failed block's error is these words (after the kind of error, if the history names it)."""
    return re.fullmatch(rf"(\w+: )?{re.escape(words)}", error.strip()) is not None


@pytest.mark.invariant("STY-6")
def test_html_on_a_table_a_number_a_list_or_a_picture_stops_the_run_saying_html_applies_only_to_text(env, smtp):
    placeholders = {"tab": "{my_table:html}", "num": "{count:html}", "lst": "{items:html}", "pic": "{chart:html}"}
    result = run(env, {
        "data": python(SALES, ["table"]),
        "chart": CHART,
        "v": python("count = 5\nitems = ['a', 'b']", ["count", "items"]),
        "tab": template(placeholders["tab"], {"data.table": "my_table"}),
        "num": template(placeholders["num"], {"v.count": "count"}),
        "lst": template(placeholders["lst"], {"v.items": "items"}),
        "pic": template(placeholders["pic"]),
        "mail": MAIL,
    }, ["data.table -> chart.table", "data.table -> tab.data", "v.count -> num.data", "v.items -> lst.data",
        "chart.image -> pic.data"] + [f"{block}.html -> mail.body" for block in placeholders])

    assert result.returncode == 1, result.stderr
    failed = errors(env)
    assert set(failed) == set(placeholders), failed
    for block, placeholder in placeholders.items():
        assert says(failed[block], only_to_text(placeholder)), failed[block]
    assert smtp.messages == []
    assert env.block_status(env.last_run()[0])["mail"] == "skipped"


@pytest.mark.invariant("STY-6")
def test_html_on_a_date_stops_the_run_too_and_does_not_show_the_word_html(env, smtp):
    """A date is not text; as a date's format, `html` would be shown as the word html."""
    result = run(env, {"body": template("Day: {day:html}"), "mail": MAIL}, ["body.html -> mail.body"],
                 params={"day": {"type": "date", "value": "2026-10-02"}})

    assert result.returncode == 1, result.stderr
    assert says(errors(env).get("body", ""), only_to_text("{day:html}")), errors(env)
    assert smtp.messages == []


@pytest.mark.invariant("STY-6")
def test_html_from_another_template_with_html_is_passed_as_it_is_with_its_picture_and_an_empty_value_shows_nothing(
        env, smtp):
    result = run(env, {
        "data": python(SALES, ["table"]),
        "chart": CHART,
        "v": python("nothing = None", ["nothing"]),
        "inner": template("<p><b>Inner</b> part</p>{chart}"),
        "body": template("[{inner:html}][{nothing:html}]", {"v.nothing": "nothing"}),
        "mail": MAIL,
    }, ["data.table -> chart.table", "chart.image -> inner.data", "inner.html -> body.data", "v.nothing -> body.data",
        "body.html -> mail.body"])

    msg = sent(result, smtp)
    html = html_of(msg)
    email = document(html)
    assert email.text.strip() == "[Inner part][]", html
    assert [(p.text, [b.text for b in p.all("b")]) for p in email.all("p")] == [("Inner part", ["Inner"])]
    [picture] = email.all("img")
    assert [f"cid:{cid}" for cid in inline_images(msg)] == [picture.attrs["src"]]


# --- STY-7: the template's own rules style what the template shows, not the email's own frame -----------------------

OWN_TABLE = '<table id="mine"><tr><td>my own cell</td></tr></table>'  # a table the user wrote in the Template
TABLE_RULE = "<style>table {{border: 2px solid red; width: 400px}}</style>"


def frame(html: str) -> list:
    """The email's own frame: the layout table that wraps the whole body, and its one cell."""
    [layout] = [t for t in document(html).all("table") if t.attrs.get("role") == "presentation"]
    return [layout, layout.all("td")[0]]


def own_look(element: Element) -> tuple:
    """An element as it looks itself: (its attributes that set a look, its style as it applies)."""
    return {k: v for k, v in element.attrs.items() if k in LOOK_ATTRIBUTES}, element.style


def bordered(style: dict) -> bool:
    return any(line not in (None, "none", "hidden") and width != "0" for width, line, _ in borders(style))


def own_table(html: str) -> Element:
    [table] = [t for t in document(html).all("table") if t.attrs.get("id") == "mine"]
    return table


def without_and_with(env, smtp, rule: str) -> tuple:
    """The HTML of two emails, each showing a table the user wrote and my_table: one with no <style> of the user's
    own, one with `rule`."""
    plain = table_email(env, smtp, OWN_TABLE + "{my_table}")
    smtp.messages.clear()
    return plain, table_email(env, smtp, rule + OWN_TABLE + "{my_table}")


@pytest.mark.invariant("STY-7")
def test_a_td_rule_leaves_the_emails_own_frame_as_it_is_and_styles_the_tables_cells_and_a_cell_you_wrote(env, smtp):
    plain, html = without_and_with(env, smtp, TD_RULE)

    _, cell = frame(html)
    assert not bordered(cell.style), f"the email's frame took the td rule: {cell.attrs.get('style')}"
    assert (paddings(cell.style), cell.style.get("font-size")) == (["16px"] * 4, "14px"), cell.attrs.get("style")
    assert [own_look(e) for e in frame(html)] == [own_look(e) for e in frame(plain)]
    assert len(cells_with_the_td_rule(html)) == 12
    [mine] = own_table(html).all("td")
    assert (mine.text, paddings(mine.style), borders(mine.style), mine.style.get("font-size")) == (
        "my own cell", ["15px"] * 4, [("1px", "solid", "#000000")] * 4, "15px"), mine.attrs.get("style")


@pytest.mark.invariant("STY-7")
def test_a_table_rule_styles_the_generated_table_and_a_table_you_wrote_and_not_the_emails_frame(env, smtp):
    plain, html = without_and_with(env, smtp, TABLE_RULE)

    layout, _ = frame(html)
    assert not bordered(layout.style) and "width" not in layout.style, (
        f"the email's frame took the table rule: {layout.attrs.get('style')}")
    assert [own_look(e) for e in frame(html)] == [own_look(e) for e in frame(plain)]
    for table in (my_table(html), own_table(html)):
        assert (borders(table.style), table.style.get("width")) == ([("2px", "solid", "#ff0000")] * 4, "400px"), (
            table.attrs.get("id"), table.attrs.get("style"))


# --- the email as a whole --------------------------------------------------------------------------------------------

def test_a_template_that_is_a_whole_html_document_is_sent_without_the_emails_own_frame_around_it(env, smtp):
    html = table_email(env, smtp, "<!DOCTYPE html><html><head><title>Report</title></head>"
                                  "<body><p>Hi team,</p>{my_table}</body></html>")

    assert [[th.text for th in table.all("th")] for table in document(html).all("table")] == [COLUMNS], html


def test_a_stylesheet_the_template_links_to_is_never_fetched_and_the_table_keeps_its_look(env, smtp):
    asked = []

    class Stylesheet(BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)
            css = b"td {color: red}"
            self.send_response(200)
            self.send_header("Content-Type", "text/css")
            self.send_header("Content-Length", str(len(css)))
            self.end_headers()
            self.wfile.write(css)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Stylesheet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        html = table_email(env, smtp, f'<link rel="stylesheet" href="http://127.0.0.1:{server.server_port}/look.css">'
                                      "{my_table}")
    finally:
        server.shutdown()
        server.server_close()

    assert asked == []
    assert look(my_table(html)) == look_as_it_was()
