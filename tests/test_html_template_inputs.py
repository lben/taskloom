"""HTML Template, the one block for composing an email body (next-specs/html_template.txt, phase 1): its `data` input
takes any number of connections, each with a name (at first its source block's name) shown and edited in Properties;
the Template is HTML with {name} / {name:format} placeholders for those inputs and the flow parameters, each value
rendered by its type in Outlook-safe HTML; its problems are shown live and stop runs before anything is sent; HTML
Table and Jinja2 are gone. Through the real `taskloom` command line, a real SMTP server and the offscreen editor.
The letters and numbers in the test docstrings (A1, B9, C13, ...) are the spec's."""

import copy
import datetime as dt
import os
import re
import subprocess
import sys
import tomllib
from html.parser import HTMLParser

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QTextDocument, Qt  # noqa: E402
from PySide6.QtWidgets import QGroupBox, QLabel, QLineEdit  # noqa: E402

from conftest import REPO  # noqa: E402
from taskloom.editor.panels import CodeEdit  # noqa: E402
from tests.test_editor import add_from_palette, drag_port_to_port, set_code  # noqa: E402
from tests.test_paths_editor_editing import (add_param, clean, error_shown, field, flow_file, menu,  # noqa: E402
                                             select_only, settle, taskloom, type_in)
from tests.test_paths_reports_email import errors, html_of, inline_images, png_size  # noqa: E402

# The user's sentence, verbatim (D24).
SENTENCE = "Hi {input1}, please see below the chart<br>{chart}<br>and below the table<br>{my_table}<br>thank you"
TOTALS_SQL = "select * from (values ('APAC', 10.5), ('EU', 20.0)) t(region, total)"
DAY = {"day": {"type": "date", "value": dt.date(2026, 10, 2)}}
MAIL = {"type": "email.send", "config": {"connection": "mailer", "to": ["team@example.com"], "subject": "Report"}}

# Wording the spec gives (A4, B10, C11).
TEMPLATE_HELP = ("The email body, in HTML. {name} shows an input or a flow parameter; {name:format} formats a number or "
                 "date, e.g. {total:,.2f} or {day:%d %B %Y}; write {{ and }} for literal braces. Enter makes no new "
                 "line in the email: use <br> (New line). Empty: every input in turn.")
HINT = ("Nothing is connected yet. Connect blocks (tables, charts, text…) to this block's data input; each one is "
        "listed here with a name, at first its block's name.")
ROWS_NOTE = ("Write {name} in the Template where an input should go; flow parameters work the same way. An empty "
             "Template shows every input in turn.")


def not_a_name(name: str) -> str:
    return f"Inputs: '{name}' is not a name (letters, digits and _, not starting with a digit)"


# --- flows ---------------------------------------------------------------------------------

def python(code: str, outputs=("output",)) -> dict:
    return {"type": "logic.python", "config": {"inputs": [], "outputs": list(outputs), "code": code}}


def sql(query: str = TOTALS_SQL) -> dict:
    return {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": query}}


def chart() -> dict:
    return {"type": "reports.chart", "config": {"kind": "bar", "x": "region", "y": ["total"]}}


def template(text: str | None = None, names: dict | None = None, **config) -> dict:
    """An HTML Template block; `names` as the flow file holds them ({"block.port": name})."""
    config = {**({"names": names} if names is not None else {}), **({"template": text} if text is not None else {}),
              **config}
    return {"type": "reports.html_template", "config": config}


def at(spec: dict, x: int, y: int) -> dict:
    """The block placed there on the canvas."""
    return {**spec, "ui": {"x": x, "y": y}}


def users_flow(text: str = SENTENCE) -> tuple[dict, list]:
    """The user's case: a text, a chart and a table into one template's `data`, the table named my_table."""
    blocks = {"input1": python("output = 'Bob'"), "totals": sql(), "chart": chart(),
              "body": template(text, {"totals.result": "my_table"}), "mail": MAIL}
    edges = ["totals.result -> chart.table", "input1.output -> body.data", "chart.image -> body.data",
             "totals.result -> body.data", "body.html -> mail.body"]
    return blocks, edges


def write(env, blocks: dict, edges=(), name="flow.yaml", **top):
    return flow_file(env.root / name, blocks, edges, **top)


def error_lines(result) -> list[str]:
    return result.stderr.splitlines()


def runs_recorded(env) -> int:
    return env.query("SELECT count(*) FROM runs")[0][0] if (env.home / "history.db").exists() else 0


def blocks_of(env, run_id: int) -> dict:
    """{block: (status, summary)} of a run, from the history."""
    return {b: (s, m) for b, s, m in env.query("SELECT block, status, summary FROM blocks WHERE run_id=?", run_id)}


class _Links(HTMLParser):
    """The href of each <a>, as a mail program reads it (entities resolved)."""

    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.append(dict(attrs).get("href"))


def hrefs(html: str) -> list:
    parser = _Links()
    parser.feed(html)
    return parser.hrefs


def without_cids(html: str) -> str:
    return re.sub(r"cid:[^\"']+", "cid:X", html)


# --- the editor ----------------------------------------------------------------------------

def inputs_box(win) -> QGroupBox:
    settle()
    boxes = [b for b in win.properties.widget().findChildren(QGroupBox) if b.title() == "Inputs"]
    assert len(boxes) == 1, "Properties has no Inputs box"
    return boxes[0]


def input_rows(win) -> list[tuple[str, QLineEdit]]:
    """The Inputs box's rows, top to bottom: ("from block.port", its name box)."""
    box = inputs_box(win)

    def y(widget):
        return widget.mapTo(box, QPoint(0, 0)).y()

    labels = sorted((w for w in box.findChildren(QLabel) if w.text().startswith("from ")), key=y)
    edits = sorted(box.findChildren(QLineEdit), key=y)
    assert len(labels) == len(edits), ([w.text() for w in labels], [w.text() for w in edits])
    return [(label.text(), edit) for label, edit in zip(labels, edits)]


def rows(win) -> list[tuple[str, str]]:
    return [(label, edit.text()) for label, edit in input_rows(win)]


def row(win, source: str) -> QLineEdit:
    return dict(input_rows(win))[f"from {source}"]


def box_texts(win) -> list[str]:
    """The texts shown in the Inputs box."""
    return [w.text() for w in inputs_box(win).findChildren(QLabel) if not w.isHidden()]


def names_of(tab, block: str) -> dict:
    return (tab.doc.blocks[block].get("config") or {}).get("names") or {}


def into(tab, block: str, port: str = "data") -> list[str]:
    return [f"{s}.{sp}" for s, sp, d, dp in tab.doc.edges() if (d, dp) == (block, port)]


def undo(win):
    menu(win, "Undo").trigger()
    settle()


def delete_edge(win, tab, text: str):
    tab.scene.clearSelection()
    next(e for e in tab.scene.edge_items if e.text == text).setSelected(True)
    menu(win, "Delete").trigger()
    settle()


def shown_tip(tip: str) -> str:
    """A tooltip as it reads on the screen (Qt shows text that looks like HTML as HTML)."""
    if Qt.mightBeRichText(tip):
        doc = QTextDocument()
        doc.setHtml(tip)
        return doc.toPlainText()
    return tip


# --- the user's case through the command line ------------------------------------------------

def test_the_users_sentence_sends_hi_bob_on_one_line_then_the_chart_then_the_table(env, smtp):
    """D24, B5-B8, D20: three outputs into one `data`; {input1} and {chart} by their blocks' names, {my_table} by the
    name stored in the file; the text on one line, the chart inline, the table's rows."""
    blocks, edges = users_flow()

    result = env.taskloom("run", write(env, blocks, edges))

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    html = html_of(msg)
    images = inline_images(msg)
    assert len(images) == 1, html
    [cid] = images
    order = [html.index("Hi Bob, please see below the chart<br>"), html.index(f'<img src="cid:{cid}"'),
             html.index("<br>and below the table<br>"), html.index(">APAC<"), html.index(">EU<"),
             html.index("thank you")]
    assert order == sorted(order)
    assert not re.search(r"<p[^>]*>\s*Hi Bob,", html)  # text is not put in a paragraph of its own


def test_a_single_connection_without_names_in_the_file_is_named_after_its_block(env, smtp):
    """B6, B8: one edge, and a flow file with no `names` (written by hand): the input is {totals}; the file is valid."""
    flow = write(env, {"totals": sql(), "body": template("<p>Totals</p>{totals}"), "mail": MAIL},
                 ["totals.result -> body.data", "body.html -> mail.body"])

    assert env.taskloom("validate", flow).returncode == 0
    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    html = html_of(smtp.messages[0])
    assert ">APAC<" in html and ">EU<" in html
    assert "{totals}" not in html


def test_two_outputs_of_one_block_and_a_block_named_like_a_parameter_get_names_of_their_own(env, smtp):
    """B8 and the edge paths: check.result is {check}, check.details {check_details}; a block `day` beside the
    parameter `day` is {day_output}, and {day} stays the parameter. A stored name whose edge is gone is ignored (C18)."""
    flow = write(env, {
        "check": python("result = 'ok'\ndetails = '3 late'", outputs=["result", "details"]),
        "day": python("output = 'Mon'"),
        "body": template("{check} / {check_details} / {day_output} / {day:%Y}", {"gone.result": "old"}),
        "mail": MAIL,
    }, ["check.result -> body.data", "check.details -> body.data", "day.output -> body.data",
        "body.html -> mail.body"], params=DAY)

    assert env.taskloom("validate", flow).returncode == 0
    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert "ok / 3 late / Mon / 2026" in html_of(smtp.messages[0])


# --- each value by its type ---------------------------------------------------------------------

VALUES = r"""
import datetime as dt
note = 'Revenue < 5 & "rising"\nsecond line'
nothing = None
revenue = 1234.5
year = 2026
opened = dt.date(2026, 10, 2)
items = ['a', 'b <c>', 3]
info = {'k': 1}
url = 'https://example.com/?a=1&b="x"'
unused = 'NOT-IN-THE-TEMPLATE'
"""
VALUE_NAMES = ["note", "nothing", "revenue", "year", "opened", "items", "info", "url", "unused"]


def test_text_numbers_dates_lists_and_dicts_are_shown_escaped_as_written_without_paragraphs(env, smtp):
    """D20, D21, D22: text escaped (also inside an attribute) with its line breaks as <br>, no <p>; None as nothing,
    even with a format; numbers as written unless formatted; dates formatted; a list item by item; a dict as its
    text; flow parameters too; an input the template does not mention is left out."""
    text = ("Note: {note}|Nothing: [{nothing}][{nothing:,.2f}]|Revenue: {revenue:,.2f} or {revenue}|Year: {year}|"
            "Day: {day:%d %B %Y}|Opened: {opened:%A %d %B %Y}|Items: {items}|Info: {info}|"
            '<a href="{url}">site</a>|end')
    flow = write(env, {
        "v": python(VALUES, outputs=VALUE_NAMES),
        "body": template(text, {f"v.{n}": n for n in VALUE_NAMES}),
        "mail": MAIL,
    }, [f"v.{n} -> body.data" for n in VALUE_NAMES] + ["body.html -> mail.body"], params=DAY)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    html = html_of(smtp.messages[0])
    for shown in ('Note: Revenue &lt; 5 &amp; "rising"<br>second line|', "Nothing: [][]|",
                  "Revenue: 1,234.50 or 1234.5|", "Year: 2026|", "Day: 02 October 2026|",
                  "Opened: Friday 02 October 2026|", "Items: a<br>b &lt;c&gt;<br>3|", "Info: {'k': 1}|"):
        assert shown in html, (shown, html)
    assert hrefs(html) == ['https://example.com/?a=1&b="x"']  # the quotes did not end the attribute
    assert "NOT-IN-THE-TEMPLATE" not in html
    assert "<p" not in html


def test_tables_are_outlook_tables_cut_at_max_table_rows_and_an_empty_one_keeps_its_header(env, smtp):
    """D20 (tables), A1 (Max table rows): 12 rows with Max table rows 5 show 5 and say so; an empty table shows its
    header."""
    flow = write(env, {
        "big": python("table = pl.DataFrame({'n': list(range(1, 13))})", outputs=["table"]),
        "empty": sql("select 'x' as region, 1.5 as total where false"),
        "body": template("{big}<br>{empty}", max_rows=5),
        "mail": MAIL,
    }, ["big.table -> body.data", "empty.result -> body.data", "body.html -> mail.body"])

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    html = html_of(smtp.messages[0])
    assert re.search(r'<th [^>]*style="[^"]+"[^>]*>n</th>', html)  # Outlook: inline styles
    assert ">5<" in html and ">6<" not in html
    assert "Showing 5 of 12 rows." in html
    assert ">region<" in html and ">total<" in html


def test_pictures_and_html_from_another_template_keep_their_images_and_a_chart_used_twice_is_attached_once(env, smtp):
    """D20 (Html, a .png path in any case, a missing .png as text), D22 ({chart} twice: one attachment)."""
    from PIL import Image

    logo = env.root / "logo.PNG"
    Image.new("RGB", (240, 60), "#2c5d8f").save(logo, format="PNG")
    flow = write(env, {
        "totals": sql(),
        "chart": chart(),
        "pics": python(f"logo = {str(logo)!r}\nold = 'old/chart.png'", outputs=["logo", "old"]),
        "inner": template("<p>Inner part</p>{chart}"),
        "body": template("{inner}<br>{chart}<br>{chart}<br>{logo}<br>{old}", {"pics.logo": "logo", "pics.old": "old"}),
        "mail": MAIL,
    }, ["totals.result -> chart.table", "chart.image -> inner.data", "inner.html -> body.data",
        "chart.image -> body.data", "pics.logo -> body.data", "pics.old -> body.data", "body.html -> mail.body"])

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    html = html_of(msg)
    images = inline_images(msg)
    shown = re.findall(r'<img src="cid:([^"]+)"', html)
    assert len(shown) == 4, html  # the inner part's chart, the chart twice, the logo
    assert shown[1] == shown[2]  # the same picture, attached once
    assert set(shown) == set(images)  # every picture shown is attached, nothing else is
    assert png_size(images[shown[3]]) == (240, 60)
    assert html.index("Inner part") < html.index(f'<img src="cid:{shown[0]}"')
    assert "old/chart.png" in html  # a picture that is not there: its path as text


@pytest.mark.parametrize("config", [{}, {"template": ""}, {"template": "  \n  "}], ids=["missing", "empty", "spaces"])
def test_an_empty_template_shows_every_input_in_edge_order(env, smtp, config):
    """A3: no template (or only spaces): every connected input in edge order, joined by <br>."""
    flow = write(env, {"totals": sql(), "chart": chart(), "body": {"type": "reports.html_template", "config": config},
                       "mail": MAIL},
                 ["totals.result -> chart.table", "chart.image -> body.data", "totals.result -> body.data",
                  "body.html -> mail.body"])

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    html = html_of(msg)
    [cid] = inline_images(msg)
    assert re.search(rf'<img src="cid:{cid}"[^>]*>\s*<br>\s*<table', html), html  # the chart, then the table
    assert ">APAC<" in html


def test_a_bare_html_template_is_valid_and_with_nothing_connected_shows_text_and_parameters(env, smtp):
    """A2: `type: reports.html_template` with no config validates (rc 0); D23: with nothing connected it runs with
    its text and the flow parameters."""
    bare = write(env, {"body": {"type": "reports.html_template"}}, name="bare.yaml")
    flow = write(env, {"body": template("Report for {day:%Y-%m-%d}"), "mail": MAIL}, ["body.html -> mail.body"],
                 params=DAY)

    checked = env.taskloom("validate", bare)
    result = env.taskloom("run", flow)

    assert checked.returncode == 0, checked.stderr
    assert "required" not in checked.stderr
    assert result.returncode == 0, result.stderr
    assert "Report for 2026-10-02" in html_of(smtp.messages[0])


# --- problems: before anything is sent ---------------------------------------------------------

def _duplicate():
    """Both names stored, as the editor stores them (a stored name is taken: a default never repeats it)."""
    return ({"data": python("table = pl.DataFrame({'region': ['EU'], 'total': [1.5]})", outputs=["table"]),
             "chart": chart(), "logo": chart(),
             "body": template("{chart}", {"chart.image": "chart", "logo.image": "chart"}), "mail": MAIL},
            ["data.table -> chart.table", "data.table -> logo.table", "chart.image -> body.data",
             "logo.image -> body.data", "body.html -> mail.body"], {})


def _template_only(text, params=None):
    return {"body": template(text), "mail": MAIL}, ["body.html -> mail.body"], params or {}


def _totals_into(body):
    return {"totals": sql(), "body": body, "mail": MAIL}, ["totals.result -> body.data", "body.html -> mail.body"], {}


PROBLEMS = {
    "duplicate (C13)": (_duplicate, "Inputs: chart is the name of more than one input (chart.image, logo.image); "
                                    "give each its own name"),
    "parameter (C14)": (lambda: ({"dates": sql(), "body": template("{day}", {"dates.result": "day"}), "mail": MAIL},
                                 ["dates.result -> body.data", "body.html -> mail.body"], DAY),
                        "Inputs: day (from dates.result) is also a flow parameter; give the input another name"),
    "unknown (C15)": (lambda: (*users_flow("Hi {input1}<br>{chart}<br>{my_tabel}"), {}),
                      "Template: {my_tabel} is not an input or a flow parameter (inputs: input1, chart, my_table; "
                      "write {{ and }} for literal braces)"),
    "unknown, nothing connected (C15)": (lambda: _template_only("Hi {who}"),
                                         "Template: {who} is not an input or a flow parameter (inputs: none; "
                                         "write {{ and }} for literal braces)"),
    "css braces (C16)": (lambda: _template_only('<style>.big { font-size: 20px; }</style><p class="big">Hi</p>'),
                         "Template: '{ font-size}' is not a name; write {{ and }} for literal braces"),
    "empty braces (C16)": (lambda: _template_only("Total: {}"),
                           "Template: '{}' is not a name; write {{ and }} for literal braces"),
    "conversion (C16)": (lambda: _template_only("{x!r}", {"x": {"type": "text", "value": "hi"}}),
                         "Template: '{x!r}' is not a name; write {{ and }} for literal braces"),
    "unmatched brace (C17)": (lambda: _template_only("Total } here"),
                              "Template: Single '}' encountered in format string; write {{ and }} for literal braces"),
    "not a name in the file (C18)": (lambda: _totals_into(template("{totals}", {"totals.result": "2nd"})),
                                     not_a_name("2nd")),
    "max table rows below 1 (A1)": (lambda: _totals_into(template("{totals}", max_rows=0)),
                                    "Max table rows: expected 1 or more, got 0"),
}


@pytest.mark.parametrize("case", PROBLEMS)
def test_a_problem_stops_validate_and_run_with_its_message_and_nothing_is_sent(env, smtp, case):
    """C12-C18: `taskloom validate` exits 2 with the message after "body: "; `taskloom run` is refused the same way
    and sends nothing."""
    make, message = PROBLEMS[case]
    blocks, edges, params = make()
    flow = write(env, blocks, edges, params=params)

    checked = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow)

    for result in (checked, ran):
        assert result.returncode == 2, result.stderr
        assert f"error: body: {message}" in error_lines(result), result.stderr
    assert smtp.messages == []
    assert runs_recorded(env) == 0


def test_a_stored_name_whose_key_is_not_block_port_is_refused_under_inputs(env, smtp):
    """C18: a hand-written `names` entry keyed by something other than block.port fails with an Inputs: message."""
    blocks, edges, _ = _totals_into(template("{totals}", {"totals": "sales"}))
    flow = write(env, blocks, edges)

    for result in (env.taskloom("validate", flow), env.taskloom("run", flow)):
        assert result.returncode == 2, result.stderr
        assert any(line.startswith("error: body: Inputs: ") for line in error_lines(result)), result.stderr
    assert smtp.messages == []


def test_a_format_a_value_cannot_take_fails_its_template_when_it_runs_and_nothing_is_sent(env, smtp):
    """D21: a format on a picture, a table, a list or HTML, and a format a text cannot take, fail the block at run
    time; the email after them does not run."""
    flow = write(env, {
        "data": python("table = pl.DataFrame({'region': ['EU'], 'total': [1.5]})", outputs=["table"]),
        "chart": chart(),
        "v": python("name = 'Bob'\nitems = ['a', 'b']", outputs=["name", "items"]),
        "inner": template(),
        "pic": template("{chart:,.2f}"),
        "tab": template("{data:,.2f}"),
        "lst": template("{items:>5}", {"v.items": "items"}),
        "part": template("{inner:>5}"),
        "txt": template("{name:,.2f}", {"v.name": "name"}),
        "mail": MAIL,
    }, ["data.table -> chart.table", "chart.image -> pic.data", "data.table -> tab.data", "v.items -> lst.data",
        "inner.html -> part.data", "v.name -> txt.data"] + [f"{b}.html -> mail.body" for b in ("pic", "tab", "lst",
                                                                                               "part", "txt")])

    result = env.taskloom("run", flow)

    assert result.returncode == 1, result.stderr
    failed = errors(env)
    assert set(failed) == {"pic", "tab", "lst", "part", "txt"}, failed
    for block, placeholder in (("pic", "{chart:,.2f}"), ("tab", "{data:,.2f}"), ("lst", "{items:>5}"),
                               ("part", "{inner:>5}")):
        assert f"Template: {placeholder}: a format applies only to text, numbers and dates" in failed[block]
    assert "Template: {name:,.2f}: Unknown format code 'f' for object of type 'str'" in failed["txt"]
    assert smtp.messages == []
    assert env.block_status(env.last_run()[0])["mail"] == "skipped"


def test_css_with_doubled_braces_still_moves_into_style_attributes_for_outlook(env, smtp):
    """D19: a <style> block doubles its braces; the CSS still ends up in style attributes, with no <style> left."""
    flow = write(env, {"body": template('<style>.big {{ font-size: 20px; color: #2c5d8f; }}</style>\n'
                                        '<p class="big">Totals</p>'), "mail": MAIL}, ["body.html -> mail.body"])

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    html = html_of(smtp.messages[0])
    assert "<style" not in html
    assert re.search(r'<p class="big" style="[^"]*font-size: ?20px[^"]*color: ?#2c5d8f', html)


def test_running_the_template_again_from_a_finished_run_sends_the_same_body(env, smtp):
    """B6: a re-run from a finished run (run --from body --inputs-from) gets its inputs the same way as the full run."""
    blocks, edges = users_flow()
    flow = write(env, blocks, edges)
    assert env.taskloom("run", flow).returncode == 0
    run_id, _ = env.last_run()

    result = env.taskloom("run", flow, "--from", "body", "--inputs-from", run_id)

    assert result.returncode == 0, result.stderr
    first, again = smtp.messages
    assert "Hi Bob, please see below the chart<br>" in html_of(again)
    assert without_cids(html_of(again)) == without_cids(html_of(first))
    assert len(inline_images(again)) == 1


def test_a_source_that_is_skipped_skips_the_template_and_no_email_is_sent(env, smtp):
    """D23: every connected source must deliver; the If's path not taken skips the template and the email."""
    flow = write(env, {
        "gate": {"type": "logic.if", "config": {"condition": "1 > 2"}},
        "note": python("output = 'hello'"),
        "body": template("{gate} {note}"),
        "mail": MAIL,
    }, ["gate.true -> body.data", "note.output -> body.data", "body.html -> mail.body"])

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    statuses = blocks_of(env, env.last_run()[0])
    assert statuses["note"][0] == "success"
    assert statuses["body"] == ("skipped", "nothing reached its 'data' input")
    assert statuses["mail"][0] == "skipped"
    assert smtp.messages == []


def test_a_blocks_error_connected_into_data_is_named_after_the_block_and_shown_as_text(env, smtp):
    """Edge path: an `error` output into `data` gets its block's name by default ({load}); the error arrives as text."""
    flow = write(env, {"load": python("raise ValueError('no file today')"), "body": template("Failed: {load}"),
                       "mail": MAIL}, ["load.error -> body.data", "body.html -> mail.body"])

    result = env.taskloom("run", flow)

    assert len(smtp.messages) == 1, result.stderr
    html = html_of(smtp.messages[0])
    assert "Failed: {" in html and "no file today" in html, html


# --- HTML Table and Jinja2 are gone ----------------------------------------------------------

def test_html_table_the_inputs_setting_and_jinja_syntax_are_gone(env, smtp):
    """E25: reports.html_table is an unknown block type, the old `inputs:` an unknown setting, and Jinja's {{ }}
    is literal braces."""
    table = write(env, {"table": {"type": "reports.html_table"}}, name="table.yaml")
    old = write(env, {"body": template("<p>x</p>", inputs=["totals"])}, name="old.yaml")
    jinja = write(env, {"body": template("<p>Sales {{ params.day }}</p>"), "mail": MAIL}, ["body.html -> mail.body"],
                  name="jinja.yaml", params=DAY)

    old_type, old_setting = env.taskloom("validate", table), env.taskloom("validate", old)
    result = env.taskloom("run", jinja)

    assert old_type.returncode == 2
    assert "error: table: unknown block type 'reports.html_table'" in error_lines(old_type), old_type.stderr
    assert old_setting.returncode == 2
    assert "error: body: unknown setting(s) ['inputs']" in error_lines(old_setting), old_setting.stderr
    assert result.returncode == 0, result.stderr
    assert "Sales { params.day }" in html_of(smtp.messages[0])


# Runs the command line with jinja2 and markupsafe impossible to import.
WITHOUT_JINJA = ("import sys; sys.modules['jinja2'] = sys.modules['markupsafe'] = None; "
                 "from taskloom.cli import run_cli; run_cli(sys.argv[1:])")


def test_reports_are_made_without_jinja2_which_is_no_longer_a_dependency(env, smtp):
    """E26: jinja2 (and markupsafe with it) is not a dependency of the project or its lock file, and a report is
    built and sent without them."""
    dependencies = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert not [d for d in dependencies if re.match(r"(?i)\s*(jinja2|markupsafe)\b", d)], dependencies
    lock = (REPO / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "jinja2"' not in lock and 'name = "markupsafe"' not in lock
    flow = write(env, {"totals": sql(), "body": template("Hi team<br>{totals}"), "mail": MAIL},
                 ["totals.result -> body.data", "body.html -> mail.body"])

    result = subprocess.run([sys.executable, "-c", WITHOUT_JINJA, "run", str(flow)], env=env.env, cwd=env.root,
                            capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr
    html = html_of(smtp.messages[0])
    assert "Hi team<br>" in html and ">APAC<" in html


# --- the editor ---------------------------------------------------------------------------------

def test_the_reports_list_has_no_html_table_and_a_new_html_template_shows_no_red_text_and_the_inputs_hint(win):
    """E25, A1, A2, A4, B10: the Reports blocks have no HTML Table; a freshly added HTML Template has the inputs
    `run after` and `data` and the output `html`, no red text, the Inputs hint, and the Template help."""
    try:
        tree = win.palette_panel.tree
        [reports] = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                     if tree.topLevelItem(i).text(0).startswith("Reports")]
        listed = [reports.child(i).text(0) for i in range(reports.childCount())]
        assert {"HTML Template", "Chart", "To Excel"} <= set(listed) and "HTML Table" not in listed, listed

        tab = win.new_flow()
        add_from_palette(win, "HTML Template")
        settle()
        [block] = [b for b, spec in tab.doc.blocks.items() if spec["type"] == "reports.html_template"]
        select_only(tab, block)

        node = tab.scene.nodes[block]
        assert (node.inputs, node.outputs) == (["after", "data"], ["html", "error"])
        assert error_shown(win) == ""
        assert HINT in box_texts(win)
        [box] = win.properties.widget().findChildren(CodeEdit)
        [label] = [w for w in win.properties.widget().findChildren(QLabel) if w.text() == "Template"]
        assert shown_tip(box.toolTip()) == TEMPLATE_HELP
        assert shown_tip(label.toolTip()) == TEMPLATE_HELP
        assert field(win, "Max table rows").placeholderText() == "500"
    finally:
        clean(win)


def test_the_users_three_blocks_dragged_into_data_are_named_in_properties_and_the_email_is_sent(env, smtp, win,
                                                                                                tmp_path):
    """The user's case in the editor. B5: each drag onto `data` keeps the earlier lines; B9, B10: each one adds a row
    and its default name, live, in the step that connects it (one Undo takes back both); B11: names typed in the rows
    are saved; D24: the user's sentence then sends Hi Bob, the chart and the table."""
    try:
        path = flow_file(tmp_path / "report.yaml", {
            "ask_name": at(python("answer = 'Bob'", outputs=["answer"]), 0, 0),
            "totals": at(sql(), 0, 160),
            "chart": at(chart(), 250, 260),
            "body": at(template(), 500, 80),
            "mail": at(MAIL, 500, 260),
        }, ["totals.result -> chart.table", "body.html -> mail.body"])
        tab = win.open_flow(path)
        settle()
        select_only(tab, "body")
        assert HINT in box_texts(win) and rows(win) == []

        drag_port_to_port(tab, "ask_name", "answer", "body", "data")
        assert rows(win) == [("from ask_name.answer", "ask_name")]
        assert ROWS_NOTE in box_texts(win) and HINT not in box_texts(win)
        drag_port_to_port(tab, "chart", "image", "body", "data")
        drag_port_to_port(tab, "totals", "result", "body", "data")

        assert into(tab, "body") == ["ask_name.answer", "chart.image", "totals.result"]
        assert rows(win) == [("from ask_name.answer", "ask_name"), ("from chart.image", "chart"),
                             ("from totals.result", "totals")]
        assert names_of(tab, "body") == {"ask_name.answer": "ask_name", "chart.image": "chart", "totals.result": "totals"}
        undo(win)  # the last line and its name, together
        assert into(tab, "body") == ["ask_name.answer", "chart.image"]
        assert "totals.result" not in names_of(tab, "body")
        assert rows(win) == [("from ask_name.answer", "ask_name"), ("from chart.image", "chart")]
        menu(win, "Redo").trigger()
        settle()
        assert names_of(tab, "body")["totals.result"] == "totals"

        type_in(row(win, "ask_name.answer"), "input1")
        type_in(row(win, "totals.result"), "my_table")
        set_code(win, SENTENCE)
        settle()

        assert rows(win) == [("from ask_name.answer", "input1"), ("from chart.image", "chart"),
                             ("from totals.result", "my_table")]
        assert error_shown(win) == ""
        assert win.save(tab)
        assert taskloom(win, "validate", path, cwd=tmp_path).returncode == 0
        result = taskloom(win, "run", path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        [msg] = smtp.messages
        html = html_of(msg)
        [cid] = inline_images(msg)
        order = [html.index("Hi Bob, please see below the chart<br>"), html.index(f'<img src="cid:{cid}"'),
                 html.index("<br>and below the table<br>"), html.index(">APAC<"), html.index("thank you")]
        assert order == sorted(order)
    finally:
        clean(win)


def test_input_names_typed_in_properties_are_saved_refused_restored_or_flagged_and_each_change_is_one_undo(win,
                                                                                                         tmp_path):
    """B11: a name is saved in one step; a non-name is refused with why and changes nothing; an empty box restores
    the default; a duplicate is saved and shown in red (C13). B9: deleting a line or a source block drops its name
    in the same step."""
    try:
        path = flow_file(tmp_path / "names.yaml", {
            "ask_name": at(python("answer = 'Bob'", outputs=["answer"]), 0, 0),
            "totals": at(sql(), 0, 160),
            "chart": at(chart(), 250, 260),
            "body": at(template("", {"ask_name.answer": "input1", "chart.image": "chart", "totals.result": "totals"}),
                       500, 80),
        }, ["totals.result -> chart.table", "ask_name.answer -> body.data", "chart.image -> body.data",
            "totals.result -> body.data"])
        tab = win.open_flow(path)
        settle()
        select_only(tab, "body")
        assert rows(win) == [("from ask_name.answer", "input1"), ("from chart.image", "chart"),
                             ("from totals.result", "totals")]
        assert error_shown(win) == ""
        before = copy.deepcopy(tab.doc.data)

        type_in(row(win, "totals.result"), "my_table")
        assert names_of(tab, "body")["totals.result"] == "my_table"
        assert dict(rows(win))["from totals.result"] == "my_table"
        undo(win)
        assert tab.doc.data == before

        for bad in ("my table", "2nd"):
            type_in(row(win, "totals.result"), bad)
            assert error_shown(win) == not_a_name(bad)
            assert tab.doc.data == before, bad

        select_only(tab, "chart")
        select_only(tab, "body")
        type_in(row(win, "ask_name.answer"), "")
        assert names_of(tab, "body").get("ask_name.answer", "ask_name") == "ask_name"  # its block's name again
        assert dict(rows(win))["from ask_name.answer"] == "ask_name"
        undo(win)
        assert tab.doc.data == before

        type_in(row(win, "totals.result"), "chart")
        assert names_of(tab, "body")["totals.result"] == "chart"  # saved, to put right either one
        assert error_shown(win) == ("Inputs: chart is the name of more than one input (chart.image, totals.result); "
                                    "give each its own name")
        undo(win)
        assert tab.doc.data == before
        assert error_shown(win) == ""

        delete_edge(win, tab, "chart.image -> body.data")
        assert "chart.image" not in names_of(tab, "body")
        select_only(tab, "body")
        assert rows(win) == [("from ask_name.answer", "input1"), ("from totals.result", "totals")]
        undo(win)
        assert tab.doc.data == before

        select_only(tab, "ask_name")
        menu(win, "Delete").trigger()
        settle()
        assert names_of(tab, "body") == {"chart.image": "chart", "totals.result": "totals"}
        undo(win)
        assert tab.doc.data == before
    finally:
        clean(win)


def test_a_stored_name_that_is_a_number_in_the_file_shows_in_red_in_properties_and_can_be_put_right(win, tmp_path):
    """C18, C12: a hand-written name YAML reads as a number (123) is not a name; Properties still shows its row and
    the Inputs message in red; B11: typing a name in the row stores it and the red line goes."""
    try:
        path = flow_file(tmp_path / "number.yaml", {
            "totals": at(sql(), 0, 0),
            "body": at(template("{totals}", {"totals.result": 123}), 300, 0),
        }, ["totals.result -> body.data"])
        tab = win.open_flow(path)
        settle()
        select_only(tab, "body")

        assert error_shown(win) == not_a_name("123")
        assert rows(win) == [("from totals.result", "123")]

        type_in(row(win, "totals.result"), "totals")
        settle()
        assert names_of(tab, "body") == {"totals.result": "totals"}
        assert error_shown(win) == ""
    finally:
        clean(win)


def test_two_outputs_of_one_block_keep_their_names_when_one_is_disconnected_and_a_parameter_named_alike_shows(win,
                                                                                                           tmp_path):
    """Edge paths: two outputs of `check` are check and check_details (typing check on the second shows C13); a block
    `day` beside the parameter `day` is day_result; deleting the first line leaves check_details as it was (names
    never shift); a parameter added later with a stored name shows C14."""
    try:
        path = flow_file(tmp_path / "two.yaml", {
            "check": at(python("result = 'ok'\ndetails = '3 late'", outputs=["result", "details"]), 0, 0),
            "day": at(sql("select 1 as n"), 0, 200),
            "body": at(template(), 300, 100),
        }, params=DAY)
        tab = win.open_flow(path)
        settle()
        select_only(tab, "body")

        drag_port_to_port(tab, "check", "result", "body", "data")
        drag_port_to_port(tab, "check", "details", "body", "data")
        drag_port_to_port(tab, "day", "result", "body", "data")

        assert rows(win) == [("from check.result", "check"), ("from check.details", "check_details"),
                             ("from day.result", "day_result")]
        assert error_shown(win) == ""
        type_in(row(win, "check.details"), "check")
        assert error_shown(win) == ("Inputs: check is the name of more than one input (check.result, check.details); "
                                    "give each its own name")
        undo(win)
        assert names_of(tab, "body")["check.details"] == "check_details"

        delete_edge(win, tab, "check.result -> body.data")
        select_only(tab, "body")
        assert names_of(tab, "body") == {"check.details": "check_details", "day.result": "day_result"}
        assert rows(win) == [("from check.details", "check_details"), ("from day.result", "day_result")]

        add_param(win, "check_details", "text", "value", "x")
        select_only(tab, "day")
        select_only(tab, "body")
        assert error_shown(win) == ("Inputs: check_details (from check.details) is also a flow parameter; "
                                    "give the input another name")
    finally:
        clean(win)


def test_renaming_a_source_block_or_a_python_code_output_keeps_the_input_name_and_the_report_still_sends(env, smtp,
                                                                                                         win, tmp_path):
    """B9: a renamed source block, and a renamed Python Code output, move their names' keys in every template they
    feed; the names stay, so the template still validates and the run sends the table. Undo puts both back."""
    try:
        path = flow_file(tmp_path / "renamed.yaml", {
            "ask_name": at(python("answer = reply = 'Bob'", outputs=["answer"]), 0, 0),
            "totals": at(sql(), 0, 160),
            "body": at(template("Hi {input1}<br>{my_table}", {"ask_name.answer": "input1", "totals.result": "my_table"}),
                       300, 0),
            "footer": at(template("{sums}", {"totals.result": "sums"}), 300, 200),
            "mail": at(MAIL, 550, 100),
        }, ["ask_name.answer -> body.data", "totals.result -> body.data", "totals.result -> footer.data",
            "body.html -> mail.body", "footer.html -> mail.body"])
        tab = win.open_flow(path)
        settle()

        select_only(tab, "totals")
        type_in(field(win, "Name"), "sales_totals")
        select_only(tab, "ask_name")
        type_in(field(win, "Outputs"), "reply")

        assert names_of(tab, "body") == {"ask_name.reply": "input1", "sales_totals.result": "my_table"}
        assert names_of(tab, "footer") == {"sales_totals.result": "sums"}
        select_only(tab, "body")
        assert rows(win) == [("from ask_name.reply", "input1"), ("from sales_totals.result", "my_table")]
        assert error_shown(win) == ""
        assert win.save(tab)
        assert taskloom(win, "validate", path, cwd=tmp_path).returncode == 0
        result = taskloom(win, "run", path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        html = html_of(smtp.messages[0])
        assert "Hi Bob<br>" in html and ">APAC<" in html

        undo(win)  # the output's old name, with its connection and its key
        assert names_of(tab, "body") == {"ask_name.answer": "input1", "sales_totals.result": "my_table"}
        undo(win)  # the block's old name
        assert names_of(tab, "body") == {"ask_name.answer": "input1", "totals.result": "my_table"}
        assert names_of(tab, "footer") == {"totals.result": "sums"}
    finally:
        clean(win)


def test_the_example_whose_file_stores_no_names_keeps_its_input_names_when_a_source_is_renamed_or_pasted(env, smtp,
                                                                                                        win, tmp_path):
    """B8, B9, E27: the shipped example has edges into body.data and no `names`; renaming chart to sales_chart keeps
    {chart} (it still validates; one Undo puts the file back), and a pasted copy of totals and body keeps {totals}."""
    try:
        path = tmp_path / "sales.yaml"
        path.write_text((REPO / "examples" / "sales-report-email.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        tab = win.open_flow(path)
        settle()
        assert "names" not in (tab.doc.blocks["body"].get("config") or {})
        select_only(tab, "body")
        assert rows(win) == [("from chart.image", "chart"), ("from totals.result", "totals")]
        before = copy.deepcopy(tab.doc.data)

        select_only(tab, "chart")
        type_in(field(win, "Name"), "sales_chart")
        select_only(tab, "body")
        assert rows(win) == [("from sales_chart.image", "chart"), ("from totals.result", "totals")]
        assert error_shown(win) == ""
        assert win.save(tab)
        assert taskloom(win, "validate", path, cwd=tmp_path).returncode == 0
        undo(win)  # the rename and the name it kept, in one step
        assert tab.doc.data == before

        select_only(tab, "totals", "body")
        win.copy_blocks()
        win.paste_blocks()
        settle()
        assert names_of(tab, "body_2") == {"totals_2.result": "totals"}
        select_only(tab, "body")
        assert rows(win) == [("from chart.image", "chart"), ("from totals.result", "totals")]
    finally:
        clean(win)


def test_a_pasted_template_keeps_the_names_of_its_copied_sources_and_drops_the_others(win, tmp_path):
    """B9 (paste): the copy's names follow the copied sources' new names, a line that was not copied loses its name,
    and the original template keeps its own; pasted alone it has no names and its {my_table} shows C15."""
    try:
        names = {"ask_name.answer": "input1", "totals.result": "my_table"}
        path = flow_file(tmp_path / "pasted.yaml", {
            "ask_name": at(python("answer = 'Bob'", outputs=["answer"]), 0, 0),
            "totals": at(sql(), 0, 160),
            "body": at(template("Hi {input1}<br>{my_table}", names), 300, 80),
        }, ["ask_name.answer -> body.data", "totals.result -> body.data"])
        tab = win.open_flow(path)
        settle()
        original = copy.deepcopy(tab.doc.data)

        select_only(tab, "totals", "body")
        win.copy_blocks()
        win.paste_blocks()
        settle()

        assert names_of(tab, "body_2") == {"totals_2.result": "my_table"}
        assert names_of(tab, "body") == names
        assert into(tab, "body_2") == ["totals_2.result"]
        select_only(tab, "body_2")
        assert rows(win) == [("from totals_2.result", "my_table")]
        assert ("Template: {input1} is not an input or a flow parameter (inputs: my_table; write {{ and }} for literal "
                "braces)") in error_shown(win).splitlines()
        undo(win)  # the paste, in one step
        assert tab.doc.data == original

        select_only(tab, "body")
        win.copy_blocks()
        win.paste_blocks()
        settle()

        assert names_of(tab, "body_2") == {}
        assert names_of(tab, "body") == names
        select_only(tab, "body_2")
        assert ("Template: {my_table} is not an input or a flow parameter (inputs: none; write {{ and }} for literal "
                "braces)") in error_shown(win).splitlines()
        select_only(tab, "body")
        assert error_shown(win) == ""
    finally:
        clean(win)
