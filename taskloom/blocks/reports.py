"""Report blocks: HTML tables and templates, charts, Excel files, and sending email."""

from __future__ import annotations

import html as html_lib
import re
import uuid
from pathlib import Path

import jinja2
import polars as pl
from markupsafe import Markup

from .. import mail
from ..block import Block, fields, ports
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


def to_html(value, ctx=None) -> Html:
    """Turn a block output into email HTML: Html, a table, a PNG path, text, or a list of these."""
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
    if text.lower().endswith(".png") and Path(text).exists():
        cid = f"img-{uuid.uuid4().hex[:12]}"
        return Html(image_tag(cid, Path(text)), {cid: text})
    if re.search(r"<(/?[a-zA-Z][a-zA-Z0-9]*)(\s[^<>]*)?/?>", text):  # contains HTML tags
        return Html(text)
    paragraphs = html_lib.escape(text).split("\n\n")
    return Html("".join(f'<p style="margin:0 0 10px;">{p.replace(chr(10), "<br>")}</p>' for p in paragraphs))


class HtmlTable(Block):
    type_id = "reports.html_table"
    title = "HTML Table"
    category = "Reports"
    inputs = {"table": ports.Table()}
    outputs = {"html": ports.Any()}
    config = {
        "title": fields.Text(required=False, help="A heading above the table"),
        "max_rows": fields.Int(default=500, help="Emails get slow and get clipped with very long tables"),
    }

    def run(self, ctx, table):
        df, total = _frame(table, self.config.max_rows)
        heading = (f'<h3 style="font-size:15px;margin:0 0 8px;">{html_lib.escape(self.config.title)}</h3>'
                   if self.config.title else "")
        return {"html": Html(heading + html_table(df, self.config.max_rows, total))}


class Chart(Block):
    type_id = "reports.chart"
    title = "Chart"
    category = "Reports"
    inputs = {"table": ports.Table()}
    outputs = {"image": ports.Any()}
    config = {
        "kind": fields.Choice(CHART_KINDS, default="bar"),
        "x": fields.Text(required=False, help="Column for categories / the x axis"),
        "y": fields.List(help="One or more value columns (one per series)"),
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
        path = render_chart(df, ctx.new_path("chart.png"), c.kind, c.x, c.y, c.title or "", c.width, c.height)
        return {"image": str(path)}


class HtmlTemplate(Block):
    type_id = "reports.html_template"
    title = "HTML Template"
    category = "Reports"
    outputs = {"html": ports.Any()}
    config = {
        "inputs": fields.Names(default=["table"], help="Available in the template under these names"),
        "template": fields.Code("html", help="Jinja2: {{ table(sales) }}, {{ image(chart) }}, {{ params.day }}, "
                                             "{{ part }} for HTML from another block"),
    }

    @classmethod
    def ports(cls, config):
        return {n: ports.Any(required=False) for n in config["inputs"]}, cls.outputs

    def run(self, ctx, **inputs):
        images: dict[str, str] = {}

        def table(value, max_rows=500):
            df, total = _frame(value, max_rows)
            return Markup(html_table(df, max_rows, total))

        def image(path, width=None):
            cid = f"img-{uuid.uuid4().hex[:12]}"
            images[cid] = str(path)
            return Markup(image_tag(cid, Path(path), width))

        values = {}
        for name in self.config.inputs:
            value = inputs.get(name)
            if isinstance(value, Html):
                images.update(value.images)
                value = Markup(value.html)
            values[name] = value
        env = jinja2.Environment(autoescape=True, undefined=jinja2.StrictUndefined)
        rendered = env.from_string(self.config.template).render(
            **values, params=ctx.params, table=table, image=image)
        return {"html": Html(rendered, images)}


class ToExcel(Block):
    type_id = "reports.excel"
    title = "To Excel"
    category = "Reports"
    outputs = {"file": ports.Any()}
    config = {
        "path": fields.Path(help="e.g. reports/sales_{day:%Y-%m-%d}.xlsx"),
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
            raise ValueError(f"chart_sheet '{sheet}' is not one of the sheets {list(frames)}")
        df = frames[sheet]
        columns = list(df.columns)
        missing = [col for col in ([c.chart_x] if c.chart_x else []) + list(c.chart_y) if col not in columns]
        if missing or not c.chart_y:
            raise ValueError(f"chart needs chart_y columns from {columns}" + (f"; unknown: {missing}" if missing else ""))
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
    category = "Email"
    inputs = {"body": ports.Any(required=False, many=True), "attachments": ports.Any(required=False, many=True)}
    outputs = {"sent": ports.Any()}
    config = {
        "connection": fields.Connection(kind="smtp"),
        "to": fields.List(default=[], help="Addresses; empty means notify_email from settings"),
        "cc": fields.List(default=[]),
        "subject": fields.Text(help="e.g. Daily sales {day:%Y-%m-%d}"),
        "message": fields.Text(required=False, help="Text shown above the body"),
        "save_copy": fields.Path(required=False, help="Also save the email as a .eml file"),
    }

    def run(self, ctx, body=None, attachments=None):
        c = self.config
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
        msg = mail.build_message(conn.get("sender", ""), to, c.subject, content, files, c.cc)
        if c.save_copy:
            Path(c.save_copy).parent.mkdir(parents=True, exist_ok=True)
            Path(c.save_copy).write_bytes(bytes(msg))
        mail.send(conn, msg)
        ctx.log(f"sent '{c.subject}' to {msg['To']} with {len(files)} attachment(s)")
        return {"sent": {"to": msg["To"], "subject": c.subject, "attachments": [str(f) for f in files]}}
