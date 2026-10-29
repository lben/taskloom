"""Report blocks: HTML templates, charts, Excel files, and sending email."""

from __future__ import annotations

import html as html_lib
import string
import uuid
from pathlib import Path

import polars as pl

from .. import mail
from ..block import IDENTIFIER, Block, fields, ports
from ..report import CHART_KINDS, SERIES, Html, html_table, image_tag, render_chart
from ..table import Table
from .data import EXCEL_MAX_ROWS

_EXCEL_CHART_TYPES = {"bar": "column", "barh": "bar", "line": "line", "area": "area", "pie": "pie", "scatter": "scatter"}


def _frame(value, max_rows: int) -> tuple[pl.DataFrame, int]:
    if isinstance(value, Table):
        return value.scan().head(max_rows).collect(), value.num_rows
    if isinstance(value, pl.DataFrame):
        return value.head(max_rows), value.height
    raise TypeError(f"expected a table, got {type(value).__name__}")


def _is_picture(text: str) -> bool:
    """Whether text is the path of a PNG file there is (as Chart gives)."""
    return text.lower().endswith(".png") and Path(text).exists()


def to_html(value, ctx=None) -> Html:
    """Turn a block output into email HTML: Html, a table, a PNG path, text (shown as written), or a list of these."""
    if value is None:
        return Html("")
    if isinstance(value, Html):
        return value
    if isinstance(value, (list, tuple)):
        parts = [to_html(v, ctx) for v in value]
        return Html('<div style="height:12px;line-height:12px;">&nbsp;</div>'.join(p.html for p in parts),
                    {k: v for p in parts for k, v in p.images.items()})
    if isinstance(value, (Table, pl.DataFrame)):
        df, total = _frame(value, 500)
        return Html(html_table(df, total_rows=total))
    text = str(value)
    if _is_picture(text):
        cid = f"img-{uuid.uuid4().hex[:12]}"
        return Html(image_tag(cid, Path(text)), {cid: text})
    paragraphs = html_lib.escape(text).split("\n\n")
    return Html("".join(f'<p style="margin:0 0 10px;">{p.replace(chr(10), "<br>")}</p>' for p in paragraphs))


class Chart(Block):
    type_id = "reports.chart"
    title = "Chart"
    description = 'Draws a chart (bar, line, pie...) from a table as a picture you can put in an email.'
    category = "Reports"
    inputs = {"table": ports.Table()}
    outputs = {"image": ports.Any()}
    config = {
        "kind": fields.Choice(CHART_KINDS, default="bar"),
        "x": fields.Text(required=False, help="Column for categories / the x axis"),
        "x_label": fields.Text(required=False, help="Name shown on the x axis, e.g. Store ID; "
                                                    "default the x column's"),
        "y": fields.List(help="One or more value columns (one per series)"),
        "y_label": fields.Text(required=False, help="Name shown on the value axis; default the y column's"),
        "title": fields.Text(required=False),
        "width": fields.Int(default=640),
        "height": fields.Int(default=360),
        "max_points": fields.Int(default=5000, help="Rows used; summarise bigger tables first"),
    }

    def run(self, ctx, table):
        c = self.config
        df, total = _frame(table, c.max_points)
        if total > c.max_points:
            ctx.warn(f"charting the first {c.max_points:,} of {total:,} rows")
        path = render_chart(df, ctx.new_path("chart.png"), c.kind, c.x, c.y, c.title or "", c.width, c.height,
                            c.x_label or "", c.y_label or "")
        return {"image": str(path)}


def _shown(value, max_rows: int) -> Html:
    """A value where a template says {name}: HTML (e.g. from another template) as it is, with its pictures; a table
    as an Outlook-safe table of at most max_rows rows; a picture inline; None as nothing; a list item by item, one
    per line; anything else as its text, escaped (also its quotes, for an attribute), its line breaks as <br>."""
    if isinstance(value, (list, tuple)):
        parts = [_shown(v, max_rows) for v in value]
        return Html("<br>".join(p.html for p in parts), {k: v for p in parts for k, v in p.images.items()})
    if isinstance(value, (Table, pl.DataFrame)):
        df, total = _frame(value, max_rows)
        return Html(html_table(df, max_rows, total))
    if value is None or isinstance(value, Html) or isinstance(value, str) and _is_picture(value):
        return to_html(value)
    return _text(str(value))


def _text(text: str) -> Html:
    return Html(html_lib.escape(text).replace("\n", "<br>"))


def _placed(value, name: str, spec: str, max_rows: int) -> Html:
    """A value where a template says {name:spec} (spec empty: {name})."""
    if not spec or value is None:
        return _shown(value, max_rows)
    where = f"Template: {{{name}:{spec}}}"
    if isinstance(value, (Html, Table, pl.DataFrame, list, tuple)) or isinstance(value, str) and _is_picture(value):
        raise ValueError(f"{where}: a format applies only to text, numbers and dates")
    try:
        return _text(format(value, spec))
    except (ValueError, TypeError) as e:
        raise ValueError(f"{where}: {e}") from None


BRACES = "write {{ and }} for literal braces"


class HtmlTemplate(Block):
    type_id = "reports.html_template"
    title = "HTML Template"
    description = 'Builds an email body from your own text, mixing in tables, charts and parameters.'
    category = "Reports"
    inputs = {"data": ports.Any(required=False, many=True, by_source=True)}
    outputs = {"html": ports.Any()}
    config = {
        "names": fields.SourceNames("data", label="Inputs"),
        "template": fields.Code("html", default="", help=(
            "The email body, in HTML. {name} shows an input or a flow parameter; {name:format} formats a number or "
            "date, e.g. {total:,.2f} or {day:%d %B %Y}; write {{ and }} for literal braces. Enter makes no new "
            "line in the email: use <br> (New line). Empty: every input in turn.")),
        "max_rows": fields.Int(default=500, minimum=1, label="Max table rows",
                               help="Emails get slow and get clipped with very long tables"),
    }

    @classmethod
    def problems(cls, config, sources, params):
        names = fields.SourceNames.in_effect(config["names"], sources.get("data", []), params)
        found = []
        for name in dict.fromkeys(names.values()):
            using = [source for source, n in names.items() if n == name]
            if len(using) > 1:
                found.append(f"Inputs: {name} is the name of more than one input ({', '.join(using)}); "
                             "give each its own name")
            if name in params:
                found.append(f"Inputs: {name} (from {', '.join(using)}) is also a flow parameter; "
                             "give the input another name")
        try:
            placeholders = [(name, conversion) for _, name, _, conversion
                            in string.Formatter().parse(config["template"]) if name is not None]
        except ValueError as e:  # an unmatched brace
            return found + [f"Template: {e}; {BRACES}"]
        listed = ", ".join(dict.fromkeys(names.values())) or "none"
        for name, conversion in placeholders:
            if conversion is not None or not IDENTIFIER.fullmatch(name):
                written = "{" + name + (f"!{conversion}" if conversion is not None else "") + "}"
                found.append(f"Template: '{written}' is not a name; {BRACES}")
            elif name not in names.values() and name not in params:
                found.append(f"Template: {{{name}}} is not an input or a flow parameter (inputs: {listed}; {BRACES})")
        return list(dict.fromkeys(found))

    def run(self, ctx, data=None):
        c, data = self.config, {} if data is None else data
        problems = self.problems(vars(c), {"data": list(data)}, set(ctx.params))
        if problems:  # (run on its own, it may get inputs kept from an earlier run, connected otherwise then)
            raise ValueError(problems[0])
        if not c.template.strip():
            return {"html": _shown(list(data.values()), c.max_rows)}
        names = fields.SourceNames.in_effect(c.names, list(data), ctx.params)
        values = {**ctx.params, **{name: data[source] for source, name in names.items()}}
        shown, parts = {}, []
        for text, name, spec, _ in string.Formatter().parse(c.template):
            parts.append(text)
            if name is not None:
                if (name, spec) not in shown:  # each once: a chart shown twice is attached once
                    shown[name, spec] = _placed(values[name], name, spec, c.max_rows)
                parts.append(shown[name, spec].html)
        return {"html": Html("".join(parts), {k: v for part in shown.values() for k, v in part.images.items()})}


class ToExcel(Block):
    type_id = "reports.excel"
    title = "To Excel"
    description = 'Saves one or more tables to an Excel file, optionally with an Excel chart.'
    category = "Reports"
    outputs = {"file": ports.Any()}
    config = {
        "path": fields.Path(help="e.g. reports/sales_{day:%Y-%m-%d}.xlsx", files=["Excel workbook (*.xlsx)"]),
        "sheets": fields.Names(default=["table"], help="One input and sheet per name"),
        "chart": fields.Choice(["none"] + list(_EXCEL_CHART_TYPES), default="none", help="A native Excel chart"),
        "chart_sheet": fields.Text(required=False, help="Sheet with the chart data; default the first"),
        "chart_x": fields.Text(required=False),
        "chart_y": fields.List(default=[]),
        "chart_title": fields.Text(required=False),
    }

    @classmethod
    def ports(cls, config):
        return {n: ports.Table() for n in config["sheets"]}, cls.outputs

    def run(self, ctx, **tables):
        import xlsxwriter

        c = self.config
        path = Path(c.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        frames = {}
        for name in c.sheets:
            if tables[name].num_rows > EXCEL_MAX_ROWS:
                raise ValueError(f"sheet {name}: {tables[name].num_rows:,} rows do not fit in Excel (max {EXCEL_MAX_ROWS:,})")
            frames[name] = tables[name].scan().collect()
        with xlsxwriter.Workbook(path) as workbook:
            for name, df in frames.items():
                df.write_excel(workbook=workbook, worksheet=name[:31], autofit=True, freeze_panes=(1, 0),
                               header_format={"bold": True, "font_color": "#ffffff", "bg_color": "#2c5d8f"},
                               table_style=None)
            if c.chart != "none":
                self._add_chart(workbook, frames, c)
        ctx.log(f"wrote {', '.join(f'{n} ({len(df):,} rows)' for n, df in frames.items())} to {path}")
        return {"file": str(path)}

    @staticmethod
    def _add_chart(workbook, frames, c):
        sheet = c.chart_sheet or next(iter(frames))
        if sheet not in frames:
            raise ValueError(f"Chart sheet: '{sheet}' is not one of the sheets {list(frames)}")
        df = frames[sheet]
        columns = list(df.columns)
        missing = [col for col in ([c.chart_x] if c.chart_x else []) + list(c.chart_y) if col not in columns]
        if missing or not c.chart_y:
            raise ValueError(f"the chart needs Chart y: columns from {columns}" + (f"; unknown: {missing}" if missing else ""))
        chart = workbook.add_chart({"type": _EXCEL_CHART_TYPES[c.chart]})
        last = len(df)
        for i, col in enumerate(c.chart_y):
            j = columns.index(col)
            series = {"name": col, "values": [sheet[:31], 1, j, last, j]}
            if c.chart_x:
                k = columns.index(c.chart_x)
                series["categories"] = [sheet[:31], 1, k, last, k]
            if c.chart == "pie":
                series["points"] = [{"fill": {"color": SERIES[p % len(SERIES)]}} for p in range(last)]
            elif c.chart in ("line", "scatter"):
                series["line"] = {"color": SERIES[i % len(SERIES)], "width": 2}
                series["marker"] = {"type": "circle", "fill": {"color": SERIES[i % len(SERIES)]},
                                    "border": {"color": "#ffffff"}} if c.chart == "scatter" else {"type": "none"}
            else:
                series["fill"] = {"color": SERIES[i % len(SERIES)]}
                series["border"] = {"none": True}
            chart.add_series(series)
        chart.set_title({"name": c.chart_title} if c.chart_title else {"none": True})
        if len(c.chart_y) == 1:
            chart.set_legend({"none": True})
        if c.chart != "pie":
            chart.set_y_axis({"major_gridlines": {"visible": True, "line": {"color": "#e6e4df"}}})
        chart.set_size({"width": 720, "height": 400})
        workbook.get_worksheet_by_name(sheet[:31]).insert_chart(1, len(columns) + 1, chart)


class SendEmail(Block):
    type_id = "email.send"
    title = "Send Email"
    description = 'Sends an email with a body (text, tables, charts) and attachments, made to look right in Outlook.'
    category = "Email"
    inputs = {"body": ports.Any(required=False, many=True), "attachments": ports.Any(required=False, many=True),
              "subject": ports.Any(required=False)}
    outputs = {"sent": ports.Any()}
    config = {
        "connection": fields.Connection(kind="smtp"),
        "to": fields.List(default=[], help="Addresses; empty means notify_email from settings"),
        "cc": fields.List(default=[]),
        "subject": fields.Text(required=False, help="e.g. Daily sales {day:%Y-%m-%d}; a connected 'subject' input wins"),
        "message": fields.Text(required=False, help="Text shown above the body"),
        "save_copy": fields.Path(required=False, help="Also save the email as a .eml file"),
    }

    def run(self, ctx, body=None, attachments=None, subject=None):
        c = self.config
        subject = str(subject) if subject is not None else c.subject
        if not subject:
            raise ValueError("set a subject, or connect one to the 'subject' input")
        content = to_html(([c.message] if c.message else []) + list(body or []))
        files = []
        for item in attachments or []:
            for value in item if isinstance(item, (list, tuple)) else [item]:
                if isinstance(value, Table):
                    csv = ctx.new_path(f"attachment_{len(files) + 1}.csv")
                    value.scan().sink_csv(csv)
                    value = csv
                if not Path(value).is_file():
                    raise FileNotFoundError(f"attachment not found: {value}")
                files.append(Path(value))
        to = c.to or ctx.home.settings()["notify_email"]
        conn = ctx.connection(c.connection)
        msg = mail.build_message(conn.get("sender", ""), to, subject, content, files, c.cc)
        if c.save_copy:
            Path(c.save_copy).parent.mkdir(parents=True, exist_ok=True)
            Path(c.save_copy).write_bytes(bytes(msg))
        refused = mail.send(conn, msg)
        ctx.log(f"sent '{subject}' to {msg['To']} with {len(files)} attachment(s)")
        for address, (code, reply) in refused.items():
            ctx.warn(f"the mail server refused {address}, so it did not get the email: {code} {reply.decode(errors='replace')}")
        return {"sent": {"to": msg["To"], "subject": subject, "attachments": [str(f) for f in files]}}
