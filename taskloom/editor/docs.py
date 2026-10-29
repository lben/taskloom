"""The documentation: the pages of Help > Documentation and what the cards of the Properties tab say, from one
source. The guide is README.md, one page per "## " section; the reference is made from the block registry it is
given, so it lists plugin blocks and your own blocks too. Pages are HTML, with names and anchors that stay put:
index.html (Contents), <slug of the heading>.html, properties.html#properties.<row> and
blocks-<category>.html#<type_id> (a block) or #<type_id>.<key> (its setting). No Qt here."""

from __future__ import annotations

import html
import re
from pathlib import Path

from markdown_it import MarkdownIt

from ..block import fields, setting_label
from ..blocks.triggers import KeptRuns

NO_HELP = 'No help yet: add help="..." and examples=[...] to this setting in its block file.'
NO_EXAMPLES = "No examples yet"


class OneOf(tuple):
    """The fixed choices of a row's box: in ROWS, in place of examples."""


# The choices of the boxes in Properties (copies of panels.py's: a test checks them against the boxes).
WHEN_KINDS = ("Every few minutes or hours", "Every hour", "Every day", "Every weekday (Mon–Fri)",
              "Some days of the week", "Every month", "Custom (a cron line)")
EVERY_CHOICES = ("1 minute", "2 minutes", "3 minutes", "4 minutes", "5 minutes", "6 minutes", "10 minutes",
                 "12 minutes", "15 minutes", "20 minutes", "30 minutes", "1 hour", "2 hours", "3 hours", "4 hours",
                 "6 hours", "8 hours", "12 hours")
KEEP_CHOICES = ("As in Settings", "All of them", "The last N", "For a time")
TICKED = OneOf(("ticked", "unticked"))
DURATION = "a number of seconds, or with ms, s, m or h"

# The rows of Properties that are not a block's settings: id -> (label, what it is for, examples or OneOf).
ROWS = {
    "name": ("Name", "The block's name in this flow: letters, digits and _, not starting with a digit. The canvas, "
                     "the Log and messages show it; renaming it keeps its connections.",
             ("load_sales", "nightly", "send_report")),
    "retry.policy": ("Policy", "Whether a block that fails is tried again: none (it runs once), fixed (the same wait "
                               "before each new attempt) or exponential (each wait longer than the one before).",
                     OneOf(("none", "fixed", "exponential"))),
    "retry.max_attempts": ("Max attempts", "How many times the block is tried in all, the first time included: 1 or "
                                           "more (empty: 3).", ("3", "5", "10")),
    "retry.delay": ("Delay", f"How long to wait before each new attempt: {DURATION} (empty: no wait).",
                    ("30s", "2m", "1h")),
    "retry.initial": ("Initial", f"The wait before the second attempt: {DURATION} (empty: 1 s). Each later wait is "
                                 "the one before times Factor.", ("10s", "30s", "1m")),
    "retry.factor": ("Factor", "What each wait is multiplied by for the next one: 1 or more (empty: 2).",
                     ("2", "1.5", "3")),
    "retry.max_delay": ("Max delay", f"The longest wait between two attempts: {DURATION} (empty: 1 h).",
                        ("10m", "30m", "1h")),
    "retry.jitter": ("Jitter", "The share of each wait taken off at random, from 0 to 1, so blocks that failed "
                               "together do not all try again at once (empty: 0).", ("0", "0.2", "0.5")),
    "timeout": ("Timeout", f"How long an attempt may take before it is stopped and counts as failed: {DURATION} "
                           "(empty: no limit).", ("20m", "90s", "2h")),
    "when.runs": ("Runs", "Repeating: at times that come round again. One time: once, at a date and time.",
                  OneOf(("Repeating", "One time"))),
    "when.how_often": ("How often", "How a repeating schedule comes round: every few minutes or hours from when the "
                                    "scheduler service picks it up, or on the clock (every hour, day, weekday, chosen "
                                    "days or month, or a cron line).", OneOf(WHEN_KINDS)),
    "when.every": ("Every", "The time from one run to the next, counted from when the scheduler service picks the "
                            "schedule up (not on the clock).", OneOf(EVERY_CHOICES)),
    # (Its tooltip, as it always was.)
    "when.cron": ("Cron line", "minute hour day month weekday, e.g. 0 7-17/2 * * MON-FRI = every two hours from 7:00 "
                               "to 17:00 on weekdays; * means every, */N every N, a-b a range, a,b a list",
                  ("0 7 * * MON-FRI", "*/15 * * * *", "30 6 1 * *")),
    "when.minute": ("At minute", "The minute of every hour it runs at, from 0 to 59.", ("0", "15", "30")),
    "when.days": ("On", "The days of the week it runs on (at least one).",
                  ("Mon, Wed, Fri", "Mon, Tue, Wed, Thu, Fri", "Sat, Sun")),
    "when.day": ("On day", "The day of the month it runs on, from 1 to 31. Days 29 to 31 are skipped in months that "
                           "do not have them", ("1", "15", "28")),
    "when.time": ("At", "The time of day it runs at, on this computer's clock (24-hour).",
                  ("07:00", "13:30", "18:45")),
    "when.at": ("On", "The date and time it runs at, once, on this computer's clock.",
                ("2026-12-01 09:00", "2027-01-04 07:30")),
    "keep.runs": ("Runs", "How many of its latest finished runs are kept: as a run ends, older ones are deleted.",
                  ("100", "30", "1000")),
    "keep.for": ("For", "How long its runs are kept, in days, months or years: as a run ends, older ones are deleted.",
                 ("90 days", "18 months", "2 years")),
    "wrap": ("Wrap lines", "Wrap long lines in every Python and SQL code box (only on the screen)", TICKED),
    "text.text": ("Text", "What the text on the canvas says: a title or a remark, to explain the flow. It does not "
                          "run.", ("Daily sales report", "Runs at 07:00 on weekdays")),
    "text.style": ("Style", "Title (22 pt, bold), Heading (16 pt), Normal (11 pt) or Small (9 pt): choosing one "
                            "sets its size.",
                   OneOf(("Title", "Heading", "Normal", "Small"))),
    "text.size": ("Size", "Its size in points, from 6 to 300. Or drag the corner of the selected text on the canvas",
                  ("11", "16", "22")),
    "text.bold": ("Bold", "Ticked: the text is bold.", TICKED),
    "text.italic": ("Italic", "Ticked: the text is in italics.", TICKED),
    "text.underline": ("Underline", "Ticked: the text is underlined.", TICKED),
    "text.font": ("Font", "The font it is written in: one of the fonts on this computer, or (the usual font).",
                  ("Arial", "Georgia", "Courier New")),
}
# The groups of the Properties panel page, by the first part of a row's id.
GROUPS = {"Every block": ("name", "retry", "timeout"), "Schedule: When": ("when",), "Keep runs": ("keep",),
          "Code boxes": ("wrap",), "Text on the canvas": ("text",)}
# Schedule's settings that the When box sets (they are no rows): the rows that set them.
WHEN_SET_BY = {"cron": ("when.how_often", "when.cron"), "every": ("when.every",), "at": ("when.at",)}

_MARKDOWN = MarkdownIt("commonmark").enable("table")


def slug(text: str) -> str:
    """A GitHub-style id for a heading: lower case, without punctuation, a hyphen for each space."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def category_page(category: str) -> str:
    return f"blocks-{slug(category)}.html"


def field_href(cls, key: str) -> str:
    return f"{category_page(cls.category)}#{cls.type_id}.{key}"


def row_href(row: str) -> str:
    return f"properties.html#properties.{row}"


def examples_line(examples, code: bool = False) -> str:
    if code:  # each a small block of code, its long lines wrapped to the width of the card
        return "Examples:" + "".join(f'<pre style="white-space: pre-wrap">{html.escape(e)}</pre>' for e in examples)
    return "Examples: " + " · ".join(f"<code>{html.escape(e)}</code>" for e in examples)


def one_of(choices) -> str:
    return "One of: " + " · ".join(html.escape(str(c)) for c in choices)


def derived_line(f) -> str | None:
    """The line of a setting whose valid values are its choices, or the connections it takes (None: it has
    examples)."""
    if isinstance(f, KeptRuns):
        return one_of(KEEP_CHOICES)
    if isinstance(f, fields.Choice):
        return one_of(f.options)
    if isinstance(f, fields.Bool):
        return one_of(TICKED)
    if isinstance(f, fields.Server):
        return "Choose This computer, or one of your SSH connections by name, or add one with New…"
    if isinstance(f, fields.Connection):
        return f"Choose one of your {html.escape(f.kind.upper())} connections by name, or add one with New…"
    return None


def field_card(f) -> tuple[str, str]:
    """What a setting is for (plain text), and its card's line of valid values (HTML)."""
    line = derived_line(f)
    if line is None:
        examples = tuple(f.examples)
        line = examples_line(examples, isinstance(f, fields.Code)) if examples else NO_EXAMPLES
    return f.help or NO_HELP, line


def row_card(row: str) -> tuple[str, str, str]:
    """A ROWS row's label and what it is for (plain text), and its line of valid values (HTML)."""
    label, what, values = ROWS[row]
    return label, what, one_of(values) if isinstance(values, OneOf) else examples_line(values)


# --- the pages ---------------------------------------------------------------------------------------------

def _readme() -> str | None:
    """README.md: next to this package's modules (as installed), or above them (a source checkout)."""
    package = Path(__file__).resolve().parents[1]
    for path in (package / "README.md", package.parent / "README.md"):
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def _guide(text: str) -> tuple[str, list[tuple[str, str]]]:
    """README as HTML: its introduction (before its first "## "), and each "## " section's heading and HTML.
    Headings get their slug as id."""
    parts = [("", [])]
    tokens = _MARKDOWN.parse(text)
    for i, token in enumerate(tokens):
        if token.type == "heading_open":
            heading = tokens[i + 1].content
            token.attrSet("id", slug(heading))
            if token.tag == "h2":
                parts.append((heading, []))
        parts[-1][1].append(token)
    rendered = [(heading, _MARKDOWN.renderer.render(part, _MARKDOWN.options, {})
                 .replace("<table>", '<table border="1" cellspacing="0" cellpadding="4">')) for heading, part in parts]
    return rendered[0][1], rendered[1:]


def pages(registry) -> dict[str, str]:
    """Every page of the documentation: file name -> HTML."""
    readme = _readme()
    intro, sections = _guide(readme) if readme is not None else (
        "<h1>Taskloom</h1><p>The guide is not included in this installation (its README.md is missing); the "
        "reference below is.</p>", [])
    categories: dict[str, list] = {}
    for cls in registry.blocks.values():
        categories.setdefault(cls.category, []).append(cls)
    result = {"index.html": _contents(intro, sections, categories), "properties.html": _properties_page()}
    result.update((f"{slug(heading)}.html", body) for heading, body in sections)
    result.update((category_page(category), _category_page(category, blocks))
                  for category, blocks in categories.items())
    return result


def _contents(intro: str, sections, categories: dict) -> str:
    guide = "".join(f'<li><a href="{slug(heading)}.html">{html.escape(heading)}</a></li>' for heading, _ in sections)
    reference = ['<li><a href="properties.html">Properties panel</a>: Name, Retry and timeout, the When and Keep runs '
                 'boxes, code boxes and texts on the canvas</li>']
    for category, blocks in categories.items():
        page = category_page(category)
        titles = " · ".join(f'<a href="{page}#{cls.type_id}">{html.escape(cls.title or cls.type_id)}</a>'
                            for cls in _by_title(blocks))
        reference.append(f'<li><a href="{page}">{html.escape(category)}</a>: {titles}</li>')
    return (f"{intro}<h2>Guide</h2><ul>{guide}</ul><h2>Reference</h2>"
            f"<p>The rows of the Properties tab, and every block with its settings.</p><ul>{''.join(reference)}</ul>")


def _by_title(blocks: list) -> list:
    return sorted(blocks, key=lambda cls: cls.title or cls.type_id)


def _properties_page() -> str:
    intros = {
        "Every block": "Every block has a Name, and a <b>Retry and timeout</b> box: whether a block that fails is "
                       "tried again, and how long an attempt may take.",
        "Schedule: When": 'The When box of a <a href="blocks-start.html#trigger.schedule">Schedule</a> block: when it '
                          "starts the flow. It writes the block's cron, every or at setting.",
        "Keep runs": 'The Keep runs box of <a href="blocks-start.html#trigger.manual">Manual Start</a> and '
                     '<a href="blocks-start.html#trigger.schedule">Schedule</a>: its Keep runs choice and Always keep '
                     "failed runs are those blocks' settings; these rows show for The last N and For a time.",
        "Code boxes": "Under each Python and SQL code box.",
        "Text on the canvas": "The rows of a text on the canvas (Edit > Add text).",
    }
    parts = ["<h1>Properties panel</h1><p>When you select a block, the Properties tab shows its settings (on its "
             "block's page) and the rows below. Rest the mouse on a row to see what it is for, with examples.</p>"]
    for group, prefixes in GROUPS.items():
        parts.append(f'<h2 id="{slug(group)}">{html.escape(group)}</h2><p>{intros[group]}</p>')
        for row in (r for r in ROWS if r.split(".")[0] in prefixes):
            label, what, line = row_card(row)
            parts.append(f'<h3 id="properties.{row}">{html.escape(label)}</h3><p>{html.escape(what)}</p>'
                         f"<div>{line}</div>")
    return "".join(parts)


def _shown(value) -> str:
    if isinstance(value, bool):
        return "ticked" if value else "unticked"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return "" if value is None else str(value)


def _category_page(category: str, blocks: list) -> str:
    parts = [f"<h1>{html.escape(category)}</h1>"]
    for cls in _by_title(blocks):
        try:
            inputs, outputs = cls.ports({key: f.default for key, f in cls.config.items()})
        except Exception:  # (a block's own code that works its ports out from its settings)
            inputs, outputs = cls.inputs, cls.outputs
        parts.append(f'<h2 id="{html.escape(cls.type_id)}">{html.escape(cls.title or cls.type_id)}</h2>'
                     f"<p>Block type: <code>{html.escape(cls.type_id)}</code></p>")
        if cls.description:
            parts.append(f"<p>{html.escape(cls.description)}</p>")
        if cls.inputs_help:
            parts.append(f"<p><b>What its inputs expect:</b> {html.escape(cls.inputs_help)}</p>")
        parts.append(f"<p><b>Inputs:</b> {_ports(inputs)} · <b>Outputs:</b> {_ports(outputs)}</p>")
        if cls.type_id == "trigger.schedule":
            parts.append('<p>See also its <a href="properties.html#schedule-when">When</a> and '
                         '<a href="properties.html#keep-runs">Keep runs</a> boxes in Properties.</p>')
        elif cls.type_id == "trigger.manual":
            parts.append('<p>See also its <a href="properties.html#keep-runs">Keep runs</a> box in Properties.</p>')
        for key, f in cls.config.items():
            parts.append(_setting(cls, key, f))
    return "".join(parts)


def _ports(found: dict) -> str:
    return ", ".join(html.escape(name) + (" (a table)" if getattr(port, "kind", "") == "table" else "")
                     for name, port in found.items()) or "none"


def _setting(cls, key: str, f) -> str:
    kind = next(c.__name__ for c in type(f).__mro__ if not c.__name__.startswith("_"))
    default = _shown(f.default)
    takes = (f"In the flow file: <code>{html.escape(key)}</code> · {kind}, "
             f"{'required' if f.required else 'optional'}"
             + (f", default: <code>{html.escape(default)}</code>" if default else ""))
    if cls.type_id == "trigger.schedule" and key in WHEN_SET_BY:
        line = "Set in Properties by the When box: " + " and ".join(
            f'<a href="{row_href(row)}">{html.escape(ROWS[row][0])}</a>' for row in WHEN_SET_BY[key]) + "."
    else:
        line = field_card(f)[1]
    return (f'<h3 id="{html.escape(cls.type_id)}.{html.escape(key)}">{html.escape(setting_label(key, f))}</h3>'
            f"<p>{html.escape(f.help or NO_HELP)}</p><p>{takes}</p><div>{line}</div>")
