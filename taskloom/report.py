"""Report pieces that survive Outlook: inline-styled tables, a table-based email layout, PNG charts.

Desktop Outlook renders HTML with Word's engine: no flexbox/grid, no SVG, no data: images,
little CSS outside style="" attributes. So everything here is tables + inline styles, and
images travel as inline (cid:) attachments.
"""

from __future__ import annotations

import datetime as dt
import decimal
import html
from dataclasses import dataclass, field
from pathlib import Path

import css_inline
import polars as pl

FONT = "Arial, Helvetica, sans-serif"
INK, MUTED, LINE, HEADER_BG, STRIPE = "#1c2530", "#5f6d7c", "#d5dce4", "#2c5d8f", "#f3f6f9"
MAX_IMAGE_WIDTH = 640  # px; Outlook ignores CSS widths on images, so the width attribute is set

# Categorical colors in fixed order (a palette validated for color-vision deficiencies).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE, TEXT, TEXT_2, GRID = "#ffffff", "#0b0b0b", "#52514e", "#e6e4df"


@dataclass
class Html:
    """HTML for an email body, plus the images it shows: {content id: PNG path}."""

    html: str
    images: dict = field(default_factory=dict)

    def __str__(self):
        return self.html

    def __repr__(self):
        return f"Html({len(self.html):,} characters, {len(self.images)} image(s))"


def _cell(value) -> tuple[str, str]:
    """(text, alignment) for a table cell."""
    if value is None:
        return "", "left"
    if isinstance(value, bool):
        return ("yes" if value else "no"), "left"
    if isinstance(value, int):
        return f"{value:,}", "right"
    if isinstance(value, (float, decimal.Decimal)):
        return f"{value:,.2f}", "right"
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M"), "left"
    if isinstance(value, dt.date):
        return value.isoformat(), "left"
    return str(value), "left"


def html_table(df: pl.DataFrame, max_rows: int = 500, total_rows: int | None = None) -> str:
    """An inline-styled HTML table (header row, striped rows, numbers right-aligned)."""
    border = f"border:1px solid {LINE};"
    head = "".join(
        f'<th align="left" bgcolor="{HEADER_BG}" style="{border}padding:6px 10px;background:{HEADER_BG};'
        f'color:#ffffff;font-weight:bold;text-align:left;white-space:nowrap;">{html.escape(c)}</th>'
        for c in df.columns)
    rows = []
    for i, row in enumerate(df.head(max_rows).iter_rows()):
        bg = STRIPE if i % 2 else "#ffffff"
        cells = []
        for value in row:
            text, align = _cell(value)
            cells.append(f'<td align="{align}" style="{border}padding:5px 10px;text-align:{align};">{html.escape(text)}</td>')
        rows.append(f'<tr bgcolor="{bg}" style="background:{bg};">{"".join(cells)}</tr>')
    total = df.height if total_rows is None else total_rows
    note = ""
    if total > min(max_rows, df.height):
        note = (f'<p style="font-family:{FONT};font-size:12px;color:{MUTED};margin:6px 0 0;">'
                f"Showing {min(max_rows, df.height):,} of {total:,} rows.</p>")
    return (f'<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;'
            f'font-family:{FONT};font-size:13px;color:{INK};"><tr>{head}</tr>{"".join(rows)}</table>{note}')


def email_document(body: str) -> str:
    """Wrap body HTML in a table-based layout and move all CSS into style attributes."""
    if "<html" not in body.lower():
        body = (
            '<!DOCTYPE html><html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            f'<body style="margin:0;padding:0;background:#ffffff;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff">'
            f'<tr><td align="left" style="padding:16px;font-family:{FONT};font-size:14px;line-height:1.45;color:{INK};">'
            f"{body}</td></tr></table></body></html>")
    return css_inline.inline(body, keep_style_tags=False, load_remote_stylesheets=False)


def image_tag(cid: str, path: Path, width: int | None = None) -> str:
    """<img> for an inline attachment, with an explicit width (Outlook honours only the attribute)."""
    from PIL import Image  # installed with matplotlib

    with Image.open(path) as im:
        natural_w, natural_h = im.size
    dpi_scale = 2  # charts are rendered at twice their display size for sharpness
    shown = min(width or natural_w // dpi_scale, MAX_IMAGE_WIDTH)
    height = round(natural_h * shown / natural_w)
    return (f'<img src="cid:{cid}" width="{shown}" height="{height}" alt="" '
            f'style="display:block;border:0;width:{shown}px;height:{height}px;">')


CHART_KINDS = ["bar", "barh", "line", "area", "scatter", "pie", "hist"]


def render_chart(df: pl.DataFrame, path: Path, kind: str, x: str | None, y: list[str], title: str = "",
                 width: int = 640, height: int = 360) -> Path:
    """Draw a chart to a PNG at 2x pixel density. One y axis; colors follow series order."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if kind not in CHART_KINDS:
        raise ValueError(f"chart kind must be one of {', '.join(CHART_KINDS)}")
    missing = [c for c in ([x] if x else []) + y if c not in df.columns]
    if missing:
        raise ValueError(f"no column(s) {', '.join(missing)}; columns are {', '.join(df.columns)}")
    if not y:
        raise ValueError("choose at least one y column")
    if len(y) > len(SERIES):
        raise ValueError(f"at most {len(SERIES)} series; fold the rest into 'Other' first")

    dpi = 100
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi * 2)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    xs = df[x].to_list() if x else list(range(df.height))
    labels = [str(v) for v in xs]
    if kind == "pie":
        if len(y) != 1:
            raise ValueError("a pie chart shows one y column")
        values = df[y[0]].to_list()
        if len(values) > len(SERIES):
            raise ValueError(f"a pie chart can show at most {len(SERIES)} slices")
        ax.pie(values, labels=labels, colors=SERIES[: len(values)], startangle=90, counterclock=False,
               wedgeprops={"linewidth": 2, "edgecolor": SURFACE}, textprops={"color": TEXT_2, "fontsize": 9})
        ax.axis("equal")
    elif kind == "hist":
        for i, col in enumerate(y):
            ax.hist(df[col].drop_nulls().to_list(), bins=20, color=SERIES[i], alpha=0.85 if len(y) > 1 else 1,
                    edgecolor=SURFACE, linewidth=1, label=col)
    elif kind in ("bar", "barh"):
        n = len(y)
        slot = 0.8 / n
        positions = range(len(labels))
        # Bars stay thin (at most 24 px at display size); the rest of each slot is air.
        axis_px = (width if kind == "bar" else height) * 0.8
        thickness = min(slot * 0.9, 24 / (axis_px / max(len(labels), 1)))
        gap = thickness * 0.08  # a thin surface gap between the bars of a group
        for i, col in enumerate(y):
            offset = [p + (i - (n - 1) / 2) * (thickness + gap) for p in positions]
            values = df[col].to_list()
            if kind == "bar":
                ax.bar(offset, values, width=thickness, color=SERIES[i], label=col)
            else:
                ax.barh(offset, values, height=thickness, color=SERIES[i], label=col)
        if kind == "bar":
            ax.set_xticks(list(positions), labels, rotation=30 if len(labels) > 8 else 0, ha="right" if len(labels) > 8 else "center")
        else:
            ax.set_yticks(list(positions), labels)
            ax.invert_yaxis()
        if n == 1 and len(labels) <= 12:  # few bars: label them directly
            ax.bar_label(ax.containers[0], labels=[_cell(v)[0] for v in df[y[0]].to_list()],
                         padding=3, color=TEXT_2, fontsize=8)
    else:
        for i, col in enumerate(y):
            values = df[col].to_list()
            if kind == "scatter":
                ax.scatter(xs, values, s=36, color=SERIES[i], edgecolors=SURFACE, linewidths=1.5, label=col, zorder=3)
            else:
                ax.plot(xs, values, color=SERIES[i], linewidth=1.5, solid_capstyle="round", label=col, zorder=3)
                if kind == "area":
                    ax.fill_between(xs, values, color=SERIES[i], alpha=0.18, linewidth=0)
    if kind != "pie":
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        ax.tick_params(colors=TEXT_2, labelsize=8, length=0)
        ax.grid(axis="x" if kind == "barh" else "y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        if x and kind not in ("hist",):
            (ax.set_ylabel if kind == "barh" else ax.set_xlabel)(x, color=TEXT_2, fontsize=9)
        if len(y) == 1 and kind != "hist":
            (ax.set_xlabel if kind == "barh" else ax.set_ylabel)(y[0], color=TEXT_2, fontsize=9)
        if len(y) > 1:
            ax.legend(frameon=False, fontsize=8, labelcolor=TEXT_2, loc="upper left", bbox_to_anchor=(0, 1.12), ncol=len(y))
    if title:
        fig.suptitle(title, x=0.02, ha="left", color=TEXT, fontsize=11, fontweight="bold")
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path
