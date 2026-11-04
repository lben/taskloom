"""The Statistics block (data.statistics), in the Data category: the most useful statistics of a table, as numbers and as
charts. Groups of statistics to tick (Overview, Numbers, Text, Dates, Correlations), Columns, Group by, Custom
statistics (name = an SQL aggregate expression, computed by DuckDB), Exact; a `table` input and an optional `extra`
input (a dict or a table, e.g. from a Python Code block); `stats`, `charts` and `custom` outputs; the report in the Log;
Data preview. Through the real `taskloom run` (also --only with --inputs-from, as the editor's Run only this block
starts it), its run history (history.db: each block's output tables, the Log's lines and how the Log shows them), the
offscreen editor (the Blocks list, the canvas, Properties, Run only this block, Data preview, the Dark theme), a local
mail server (Send Email), README.md and Help > Documentation. Each test names the point of the specification (1-7)
it protects.

What the tests take as given (the specification leaves it to the change):
- Settings are found by the labels Properties shows (block.setting_label): Overview, Numbers, Text, Dates,
  Correlations and Exact are ticks (true or false in the flow file); Columns and Group by are lists of column names;
  Custom statistics is a table of rows, each {key: name, value: SQL expression} in the flow file (fields.KeyValues,
  "name = expression" as Properties adds a row).
- The `stats` table: with Group by, the group columns first; then the input column's name, then its type; then the
  statistics as columns named as the specification names them: count (the values present), missing, missing %
  (a percentage, 0 to 100), distinct, mean, std (the sample standard deviation, as DuckDB's stddev and pandas'
  describe give it), min, 25%, median, 75%, max, sum, zeros, negatives, first, last and, with Exact unticked,
  estimated; the text statistics in columns whose names have "short" (the shortest length), "long" (the longest
  length) and "common" (the most common values with their counts). A statistic that is not for a column's type is
  empty there; an unticked group's statistics are left out of the table or empty in it. min and max are readable for
  every type: a number as its digits, a date as YYYY-MM-DD, a text as it is. Values are read leniently: a number
  may be written with , between thousands, a % or a leading ≈.
- `charts` is a list of PNG paths (as Chart's `image`); `custom` a table with the custom statistics as columns (with
  Group by, the group columns first).
- The report is the block's Markdown lines in the Log (show="markdown", the text the Log draws as Markdown), in order:
  each ticked group's section, and the custom statistics and the extra input, under a heading of its own (a line
  starting with #) whose words name the group (Overview, Numbers, Text, Dates, Correlations), "custom" for the custom
  statistics; with Group by, a heading per group naming its value. Then each chart as a picture (show="image", its
  path). A number in the report may be rounded to 2 decimals.
- Charts follow Columns: a chart per listed column of its type, the correlations heatmap when 2 or more of them are
  numbers; with Group by, one more: the rows per group.
- A column named in Columns or Group by that is not in the table fails the block, naming it (the no-surprises rule).
The memory test reads the peak memory of the process that runs only the Statistics block (`taskloom run --only` with
--inputs-from, as the editor's Run only this block does), so the table it is given was made by another process."""

import html
import json
import math
import os
import re
import statistics as pystats
import subprocess
import sys
from datetime import date
from functools import cache
from pathlib import Path

import duckdb
import polars as pl
import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QFormLayout, QLabel, QToolTip  # noqa: E402

from taskloom.block import setting_label  # noqa: E402
from taskloom.flow import BlockSpec, block_config  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_block_icons import built_in, canvas_icons, drawing, unlike  # noqa: E402
from tests.test_block_icons import rows as list_rows  # noqa: E402
from tests.test_lines import menu_action, shown, wait_runs  # noqa: E402
from tests.test_paths_editor_editing import clean, flow_file, select_only, settle  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STATS = "data.statistics"
GROUPS = ("Overview", "Numbers", "Text", "Dates", "Correlations")
WORD = {"Overview": "overview", "Numbers": "number", "Text": "text", "Dates": "date", "Correlations": "correlation"}
NUMBER_STATS = ("mean", "std", "25%", "median", "75%", "sum", "zeros", "negatives")
MAXRSS_SCRIPT = (  # like the taskloom program (cli.run_cli), then the process's peak memory
    "import os, resource, sys; from taskloom.cli import main; rc = main(sys.argv[1:]); "
    "print('MAXRSS', resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, flush=True); os._exit(rc)"
)

# The sales table of most tests: a text, two number and a date column, each but qty with a missing value.
REGION = ["north", "south", "north", None, "north", "east"]
AMOUNT = [4.0, 0.0, -2.0, 10.0, None, 0.0]
QTY = [3, 1, 2, 4, 7, 5]
DAY = [date(2026, 1, 5), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), None, date(2026, 1, 9)]
COLUMNS = ("region", "amount", "qty", "day")


# --- the block, found by what Properties calls its settings -------------------------------------------------------------

@cache
def stats_class():
    cls = discover(None).get(STATS)
    assert cls is not None, f"there is no {STATS} block (Statistics)"
    return cls


def key(shown_as: str) -> str:
    """The key, in the flow file, of the Statistics setting Properties shows as `shown_as`."""
    cls = stats_class()
    keys = {setting_label(k, f): k for k, f in cls.config.items()}
    assert shown_as in keys, f"Statistics has no setting labelled {shown_as!r} (its settings: {sorted(keys)})"
    return keys[shown_as]


def statistics(settings: dict | None = None, x=0, y=0) -> dict:
    """A Statistics block with these settings (by their labels in Properties); the others left out."""
    stats_class()
    return {"type": STATS, "config": {key(label): value for label, value in (settings or {}).items()},
            "ui": {"x": x, "y": y}}


def only(*groups: str) -> dict:
    """The group ticks: these ticked, the others not."""
    return {g: g in groups for g in GROUPS}


def custom(*pairs) -> list[dict]:
    """Custom statistics rows as the flow file writes them: (name, SQL expression)."""
    return [{"key": name, "value": expression} for name, expression in pairs]


def read(path: str, x=-400, y=0) -> dict:
    return {"type": "data.read_file", "config": {"path": path}, "ui": {"x": x, "y": y}}


def python(code: str, inputs=(), x=-400, y=300) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "code": code}, "ui": {"x": x, "y": y}}


def keep(name: str, *inputs: str, x=500, y=0) -> dict:
    """A Python Code block that writes what reaches its inputs to <name>.json, as {input: value}."""
    pairs = ", ".join(f"{i!r}: {i}" for i in inputs)
    code = ("import json\nwith open(%r, 'w', encoding='utf-8') as f:\n    json.dump({%s}, f, default=str)"
            % (f"{name}.json", pairs))
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": [], "code": code},
            "ui": {"x": x, "y": y}}


def kept(env, name: str) -> dict:
    return json.loads((env.root / f"{name}.json").read_text(encoding="utf-8"))


def write_sales(env, name="sales.parquet") -> str:
    frame = pl.DataFrame({"region": REGION, "amount": AMOUNT, "qty": QTY, "day": DAY},
                         schema={"region": pl.String, "amount": pl.Float64, "qty": pl.Int64, "day": pl.Date})
    frame.write_parquet(env.root / name)
    return name


# --- running flows and reading what they did ------------------------------------------------------------------------------

def run(env, blocks: dict, edges=(), name="flow"):
    """`taskloom run` of the flow: its result, the run's number (None: no run began) and each block's status."""
    path = flow_file(env.root / f"{name}.yaml", blocks, edges)
    result = env.taskloom("run", path, timeout=300)
    found = env.query("SELECT id FROM runs ORDER BY id DESC LIMIT 1") if (env.home / "history.db").exists() else []
    run_id = found[0][0] if found else None
    return result, run_id, (env.block_status(run_id) if run_id is not None else {})


def error_of(env, run_id: int, block: str) -> str:
    return env.query("SELECT error FROM blocks WHERE run_id=? AND block=?", run_id, block)[0][0] or ""


def output_table(env, run_id: int, block: str, port: str) -> pl.DataFrame:
    [(tables,)] = env.query("SELECT tables FROM blocks WHERE run_id=? AND block=?", run_id, block)
    tables = json.loads(tables or "{}")
    assert port in tables, f"{block} gave no table {port} (its tables: {sorted(tables)})"
    return pl.read_parquet(tables[port])


def log_of(env, run_id: int, block: str) -> list[tuple[str, str | None, str]]:
    """The block's lines in the Log, in order: (level, how the Log shows it, message)."""
    return env.query("SELECT level, show, message FROM logs WHERE run_id=? AND block=? ORDER BY rowid", run_id, block)


def report(env, run_id: int, block: str) -> str:
    """The block's Markdown in the Log, in order."""
    return "\n\n".join(m for _, how, m in log_of(env, run_id, block) if how == "markdown")


def pictures(env, run_id: int, block: str) -> list[str]:
    return [m for _, how, m in log_of(env, run_id, block) if how == "image"]


def headings(markdown: str) -> list[tuple[int, str]]:
    """(where it starts, its text in lower case) of each Markdown heading."""
    return [(m.start(), m.group(1).strip().lower()) for m in re.finditer(r"(?m)^[ \t]*#{1,6}[ \t]+(.+)$", markdown)]


def group_of(heading: str) -> str | None:
    """The group of statistics a heading names: the one whose word comes first in it (Correlations in "correlations
    between the number columns"), or None."""
    found = [(heading.find(WORD[g]), g) for g in GROUPS if WORD[g] in heading]
    return min(found)[1] if found else None


def section(markdown: str, wanted) -> str:
    """The text under the first heading that names the group `wanted` (one of GROUPS) or else has the word `wanted` in
    it, up to the next heading."""
    heads = headings(markdown)
    for i, (at, text) in enumerate(heads):
        if (group_of(text) == wanted) if wanted in GROUPS else (wanted in text):
            end = heads[i + 1][0] if i + 1 < len(heads) else len(markdown)
            return markdown[at:end]
    raise AssertionError(f"the report has no heading for {wanted!r}: {[t for _, t in heads]}\n{markdown}")


def has_table(text: str) -> bool:
    """Whether the Markdown has a table (its line under the header row: |---|---|)."""
    return re.search(r"(?m)^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$", text) is not None


def says(text: str, words: str, *numbers: int) -> bool:
    """Whether a line of `text` has a word matching `words` and one of `numbers` (written with or without , between
    thousands)."""
    forms = [re.escape(f"{n:,}") for n in numbers] + [re.escape(str(n)) for n in numbers]
    number = re.compile(r"(?<![\d.,])(" + "|".join(forms) + r")(?![\d,]|\.\d)")
    return any(re.search(words, line, re.I) and number.search(line) for line in text.splitlines())


def has_number_near(text: str, value: float, tolerance: float) -> bool:
    return any(abs(float(n) - value) <= tolerance for n in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", "")))


def number(value) -> float | None:
    """A statistic as a number (None: empty)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(value) else float(value)
    text = str(value).replace(",", "").replace("≈", "").replace("%", "").strip()
    return float(text) if text and text.lower() not in ("nan", "none", "null") else None


def text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, pl.Series):
        value = value.to_list()
    if isinstance(value, (list, tuple)):
        return ", ".join(map(str, value))
    return str(value)


def stats_rows(stats: pl.DataFrame, groups: int = 0) -> dict:
    """The stats table's rows by (group values..., column name), or by column name without Group by."""
    name = stats.columns[groups]
    found = {}
    for row in stats.iter_rows(named=True):
        where = tuple(row[c] for c in stats.columns[:groups]) + (row[name],)
        found[where if groups else row[name]] = row
    return found


def named(stats: pl.DataFrame, word: str) -> str:
    found = [c for c in stats.columns if word in c.lower()]
    assert found, f"the stats table has no column with {word!r} in its name: {stats.columns}"
    return found[0]


def empty(value) -> bool:
    return value is None or isinstance(value, float) and math.isnan(value) or not text(value).strip()


def left_out(stats: pl.DataFrame, column: str) -> bool:
    """Whether the statistic is not in the stats table, or empty for every row."""
    return column not in stats.columns or all(empty(v) for v in stats[column])


def assert_light_png(path: str):
    """A PNG picture of a chart drawn on a light background (its corners), with something drawn on it."""
    from PIL import Image  # installed with matplotlib

    assert Path(path).is_file(), f"no chart file {path}"
    assert Path(path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG file"
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        w, h = rgb.size
        corners = [rgb.getpixel(p) for p in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2))]
        assert all(min(c) >= 235 for c in corners), f"{path}: not a light background: {corners}"
        colors = rgb.getcolors(maxcolors=w * h)
        assert len(colors) > 2, f"{path}: nothing drawn"


# --- 1, 7: the block, in the Data group, with a Tabler outline icon, its hover help ----------------------------------------

def test_statistics_is_in_the_data_group_and_explains_itself_on_hover(win):
    """Spec 1 (block type data.statistics, title "Statistics", category Data) and 7 (the hover help describes the
    block): the Blocks list shows Statistics in the Data group, and resting on it shows what it does."""
    cls = stats_class()
    assert (cls.title, cls.category) == ("Statistics", "Data")
    data = {item.text(0): item for item in list_rows(win.palette_panel).get("Data", [])}
    assert "Statistics" in data and "DuckDB SQL" in data, sorted(data)
    tip = html.unescape(re.sub(r"<[^>]+>", " ", data["Statistics"].toolTip(0)))
    assert cls.description and " ".join(cls.description.split()) in " ".join(tip.split())
    assert re.search(r"statistic|chart|summar", cls.description, re.I), cls.description


def test_its_icon_is_a_tabler_outline_drawing_of_statistics_shown_on_the_canvas(win, tmp_path):
    """Spec 1 (an icon from Tabler's outline set fitting statistics, e.g. chart-histogram, same style as the others):
    the icon the block has (BLOCK_ICONS) is a Tabler outline SVG with a chart or statistics name, white strokes of
    width 2 on a 24x24 box as the others; of every built-in block's icon on the canvas, the Statistics block's is the
    most like that drawing."""
    from taskloom.editor.canvas import BLOCK_ICONS

    stats_class()
    assert STATS in BLOCK_ICONS, "Statistics has no icon in BLOCK_ICONS"
    icon = BLOCK_ICONS[STATS]
    assert len(icon) == 1, f"Statistics' icon is one drawing, with no badge: {icon}"
    name = icon[0]
    assert re.search(r"chart|histogram|statistic|analytics|graph", name), f"{name}: not a statistics icon"
    svg = (REPO / "taskloom" / "editor" / "icons" / f"{name}.svg").read_text(encoding="utf-8")
    assert "icons-tabler-outline" in svg and f"icon-tabler-{name}" in svg, f"{name}.svg is not Tabler's outline {name}"
    for attribute in ('viewBox="0 0 24 24"', 'stroke="#fff"', 'stroke-width="2"', 'stroke-linecap="round"',
                      'stroke-linejoin="round"', 'fill="none"'):
        assert attribute in svg, f"{name}.svg is not drawn as the other icons: no {attribute}"
    body = re.search(r"<svg\b[^>]*>(.*)</svg>", svg, re.S).group(1)
    icons = canvas_icons(win, tmp_path, built_in(win.registry))
    reference = drawing(body)
    closest = min(icons, key=lambda t: unlike(icons[t], reference))
    assert closest == STATS, (f"{name}.svg is most like the icon of {closest} ({unlike(icons[closest], reference):.3f} "
                              f"against {unlike(icons[STATS], reference):.3f})")


# --- 3: a new block has every group and Exact ticked --------------------------------------------------------------------------

def ticks(win) -> dict[str, bool]:
    """Each check box Properties shows: by the label of its row, or by its own text, with whether it is ticked."""
    body = win.properties.widget()
    found = {}
    for form in body.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            name, value = form.itemAt(row, QFormLayout.LabelRole), form.itemAt(row, QFormLayout.FieldRole)
            if name and value and isinstance(name.widget(), QLabel) and isinstance(value.widget(), QCheckBox):
                if value.widget().isVisibleTo(body):
                    found[name.widget().text()] = value.widget().isChecked()
    for box in body.findChildren(QCheckBox):
        if box.text() and box.isVisibleTo(body):
            found.setdefault(box.text().replace("&", ""), box.isChecked())
    return found


def test_a_statistics_block_added_from_the_blocks_list_has_every_group_and_exact_ticked(win, tmp_path):
    """Spec 3 (the groups to tick, all ticked in a new block; Exact ticked by default): a Statistics block added to a
    flow as the Blocks list adds it shows ticked Overview, Numbers, Text, Dates, Correlations and Exact in
    Properties, and its settings hold them so (as the flow file has them, with the defaults for the rest), with
    Columns, Group by and Custom statistics empty; it is valid as added."""
    cls = stats_class()
    tab = win.open_flow(flow_file(tmp_path / "new.yaml", {"manual": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}}}))
    settle()
    tab.view.add_item(STATS, QPointF(400, 0))
    settle()
    [added] = [b for b, spec in tab.doc.blocks.items() if spec["type"] == STATS]
    select_only(tab, added)

    seen = ticks(win)
    for label in (*GROUPS, "Exact"):
        assert seen.get(label) is True, f"{label} is not shown ticked in Properties: {seen}"
    config = block_config(cls, BlockSpec(id=added, type=STATS, config=dict(tab.doc.blocks[added].get("config") or {})))
    assert all(config[key(label)] is True for label in (*GROUPS, "Exact")), config
    assert not config[key("Columns")] and not config[key("Group by")] and not config[key("Custom statistics")], config
    clean(win)


# --- 2, 6: the stats table ----------------------------------------------------------------------------------------------------

def test_stats_has_a_row_per_column_with_its_name_type_and_the_statistics_of_its_type(env):
    """Spec 2 (stats: a row per column, its name and type, then count, missing, missing %, distinct; for numbers mean,
    std, min, 25%, median, 75%, max, sum, zeros, negatives; for text the shortest and longest length and the most
    common values with their counts; for dates the first and the last; min and max readable for every type) and 6
    (missing values counted, each statistic over the values present): the sales table read by Read File, every group
    ticked (a new block's settings)."""
    result, run_id, status = run(env, {"load": read(write_sales(env)), "summary": statistics()},
                                 ["load.table -> summary.table"])

    assert status == {"load": "success", "summary": "success"}, result.stdout[-3000:]
    stats = output_table(env, run_id, "summary", "stats")
    rows = stats_rows(stats)
    assert sorted(rows) == sorted(COLUMNS), stats
    for column, values in (("region", REGION), ("amount", AMOUNT), ("qty", QTY), ("day", DAY)):
        present = [v for v in values if v is not None]
        row = rows[column]
        assert text(row[stats.columns[1]]).strip(), f"{column} has no type: {row}"
        assert number(row["count"]) == len(present), (column, row)
        assert number(row["missing"]) == len(values) - len(present), (column, row)
        assert abs(number(row["missing %"]) - 100 * (len(values) - len(present)) / len(values)) < 0.05, (column, row)
        assert number(row["distinct"]) == len(set(present)), (column, row)
    assert "date" in text(rows["day"][stats.columns[1]]).lower(), rows["day"]

    amount = rows["amount"]
    present = [v for v in AMOUNT if v is not None]
    expected = {"mean": pystats.mean(present), "std": pystats.stdev(present), "min": -2.0, "25%": 0.0, "median": 0.0,
                "75%": 4.0, "max": 10.0, "sum": 12.0, "zeros": 2, "negatives": 1}
    for name, value in expected.items():
        assert abs(number(amount[name]) - value) < 0.005, (name, amount[name], value)
    qty = rows["qty"]
    for name, value in {"mean": pystats.mean(QTY), "std": pystats.stdev(QTY), "min": 1, "max": 7, "sum": 22,
                        "zeros": 0, "negatives": 0}.items():
        assert abs(number(qty[name]) - value) < 0.005, (name, qty[name], value)

    region = rows["region"]
    assert number(region[named(stats, "short")]) == 4 and number(region[named(stats, "long")]) == 5, region
    common = text(region[named(stats, "common")])
    assert re.search(r"north\D{0,20}3", common), f"the most common values do not say north 3 times: {common!r}"
    others = [common.index(v) for v in ("south", "east") if v in common]
    assert not others or common.index("north") < min(others), common
    assert text(region["min"]) == "east" and text(region["max"]) == "south", region

    day = rows["day"]
    assert text(day["first"]).startswith("2026-01-05") and text(day["last"]).startswith("2026-01-09"), day
    assert text(day["min"]).startswith("2026-01-05") and text(day["max"]).startswith("2026-01-09"), day


def test_min_and_max_are_readable_for_every_type(env):
    """Spec 2 (min and max kept readable for every type) and 6 (missing values counted): a Boolean, a Time, a Datetime
    and a Duration column (as a DuckDB SQL block's `ended - started` gives), each with a missing value."""
    make = python("from datetime import datetime, time, timedelta\n"
                  "output = pl.DataFrame({'ok': [True, False, None, True],\n"
                  "                       'at': [time(9, 30), time(17, 5, 7), None, time(0, 0)],\n"
                  "                       'ts': [datetime(2026, 1, 5, 10), datetime(2026, 1, 9, 11, 30), None,\n"
                  "                              datetime(2026, 1, 6)],\n"
                  "                       'took': [timedelta(hours=1), timedelta(minutes=5), None, timedelta(days=2)]})")
    result, run_id, status = run(env, {"make": make, "summary": statistics()}, ["make.output -> summary.table"])

    assert status == {"make": "success", "summary": "success"}, result.stdout[-3000:]
    rows = stats_rows(output_table(env, run_id, "summary", "stats"))
    for column in ("ok", "at", "ts", "took"):
        assert (number(rows[column]["count"]), number(rows[column]["missing"])) == (3, 1), rows[column]
    ok, at, ts, took = (rows[c] for c in ("ok", "at", "ts", "took"))
    assert (text(ok["min"]).lower(), text(ok["max"]).lower()) == ("false", "true"), ok
    assert text(at["min"]).startswith("00:00:00") and text(at["max"]).startswith("17:05:07"), at
    assert text(ts["min"]).startswith("2026-01-05") and "10:00" in text(ts["min"]), ts
    assert text(ts["max"]).startswith("2026-01-09") and "11:30" in text(ts["max"]), ts
    least, most = text(took["min"]).strip(), text(took["max"]).strip()
    for value in (least, most):
        assert value and not re.fullmatch(r"-?\d+", value), f"took's min and max are not readable: {took}"
    assert re.search(r"\b0?0:05:00\b|\b5\s*m", least), f"took's min is not 5 minutes: {least!r}"
    assert re.search(r"\b2\s*d|\b48:00:00\b", most), f"took's max is not 2 days: {most!r}"


def test_a_column_with_no_value_at_all_is_counted_as_all_missing(env):
    """Spec 6 (missing values are counted, never silently dropped; each statistic over the values present): a number
    column every row of which is empty has count 0, every row missing (100 %), and no mean; the block succeeds."""
    make = python("output = pl.DataFrame({'id': [1, 2, 3], 'amount': [None, None, None]},\n"
                  "                      schema={'id': pl.Int64, 'amount': pl.Float64})")
    result, run_id, status = run(env, {"make": make, "summary": statistics()}, ["make.output -> summary.table"])

    assert status == {"make": "success", "summary": "success"}, result.stdout[-3000:]
    amount = stats_rows(output_table(env, run_id, "summary", "stats"))["amount"]
    assert number(amount["count"]) == 0 and number(amount["missing"]) == 3, amount
    assert abs(number(amount["missing %"]) - 100) < 0.05, amount
    assert number(amount["mean"]) is None, amount


def test_columns_limits_the_statistics_to_the_columns_listed(env):
    """Spec 3 (Columns; none listed: every column): with Columns amount and region, stats has their rows alone."""
    result, run_id, status = run(env, {"load": read(write_sales(env)),
                                       "summary": statistics({"Columns": ["amount", "region"]})},
                                 ["load.table -> summary.table"])

    assert status["summary"] == "success", result.stdout[-3000:]
    assert sorted(stats_rows(output_table(env, run_id, "summary", "stats"))) == ["amount", "region"]


@pytest.mark.parametrize("setting", ["Columns", "Group by"])
def test_a_column_that_is_not_in_the_table_fails_the_block_naming_it(env, setting):
    """Spec 3 with the no-surprises rule (nothing silently left out; what is wrong fails clearly): a Columns or Group
    by entry that is no column of the table fails the block, the error naming it."""
    result, run_id, status = run(env, {"load": read(write_sales(env)),
                                       "summary": statistics({setting: ["amount", "regoin"]})},
                                 ["load.table -> summary.table"])

    assert status.get("summary") == "failed", result.stdout[-3000:]
    assert "regoin" in error_of(env, run_id, "summary"), error_of(env, run_id, "summary")


def test_a_number_column_holding_nan_or_an_infinite_value_fails_the_block_naming_it(env):
    """Spec 6 with the no-surprises rule (statistics over the values present; what is wrong fails clearly, the user
    decides): a number column holding NaN or an infinite value has no mean, spread or order, so the block fails, naming
    the column, with no traceback."""
    (env.root / "ratios.csv").write_text("region,ratio\nnorth,0.5\nsouth,inf\neast,0.25\nwest,NaN\n",
                                         encoding="utf-8", newline="")
    result, run_id, status = run(env, {"load": read("ratios.csv"), "summary": statistics()},
                                 ["load.table -> summary.table"])

    assert status.get("summary") == "failed", result.stdout[-3000:]
    error = error_of(env, run_id, "summary")
    assert re.search(r"\bratio\b", error) and "Traceback" not in error, error
    assert not any("Traceback" in m for _, _, m in log_of(env, run_id, "summary"))


@pytest.mark.parametrize("name", ["type", "count"])
def test_group_by_a_column_named_as_a_column_of_stats_fails_the_block_naming_it(env, name):
    """Spec 3 (Group by: stats has the group columns first) with the no-surprises rule (nothing silently replaced; what
    is wrong fails clearly, the user decides): a group column named as a column of stats (its type, its count) would
    clash with it, so the block fails, naming the column and saying to rename it."""
    make = python(f"output = pl.DataFrame({{'{name}': ['a', 'b', 'a'], 'amount': [1.0, 2.0, 3.0]}})")
    result, run_id, status = run(env, {"make": make, "summary": statistics({"Group by": [name]})},
                                 ["make.output -> summary.table"])

    assert status.get("summary") == "failed", result.stdout[-3000:]
    error = error_of(env, run_id, "summary")
    assert re.search(rf"\b{name}\b", error) and re.search(r"renam", error, re.I), error
    assert "Traceback" not in error, error


# --- 3: the groups to tick, Group by, Custom statistics, Exact ------------------------------------------------------------------

@pytest.mark.parametrize("ticked", [("Overview", "Numbers"), ("Text", "Dates", "Correlations")],
                         ids=["numbers-only", "no-numbers"])
def test_only_the_ticked_groups_are_computed_and_reported(env, ticked):
    """Spec 3 (the groups to tick) and 5 (a section per ticked group): an unticked group has no section in the report
    and its statistics are not in stats (Numbers: mean, std, the percentiles, sum, zeros, negatives; Text: the
    lengths and the most common values; Dates: the first and the last)."""
    result, run_id, status = run(env, {"load": read(write_sales(env)), "summary": statistics(only(*ticked))},
                                 ["load.table -> summary.table"])

    assert status["summary"] == "success", result.stdout[-3000:]
    stats = output_table(env, run_id, "summary", "stats")
    titles = [t for _, t in headings(report(env, run_id, "summary"))]
    for group in GROUPS:
        reported = any(group_of(t) == group for t in titles)
        assert reported == (group in ticked), f"{group} ticked: {group in ticked}; the report's headings: {titles}"
    text_stats = [c for c in stats.columns if any(w in c.lower() for w in ("short", "long", "common"))]
    if "Numbers" in ticked:
        assert not left_out(stats, "mean") and not left_out(stats, "median"), stats.columns
        assert all(left_out(stats, c) for c in ("first", "last", *text_stats)), stats
    else:
        assert all(left_out(stats, c) for c in NUMBER_STATS), stats
        assert not left_out(stats, "first") and text_stats and not all(left_out(stats, c) for c in text_stats), stats


def test_custom_statistics_are_sql_aggregates_over_the_table_in_one_row(env):
    """Spec 2 (custom: a table of the custom statistics, one row) and 3 (Custom statistics: name = an SQL aggregate
    expression over the table, e.g. p95 = quantile_cont(amount, 0.95), north_share = avg(case when region = 'north'
    then 1 else 0 end), computed by DuckDB)."""
    rows = custom(("p95", "quantile_cont(amount, 0.95)"),
                  ("north_share", "avg(case when region = 'north' then 1 else 0 end)"),
                  ("total", "sum(amount)"))
    result, run_id, status = run(env, {"load": read(write_sales(env)), "summary": statistics({"Custom statistics": rows})},
                                 ["load.table -> summary.table"])

    assert status["summary"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "summary", "custom")
    assert table.height == 1 and {"p95", "north_share", "total"} <= set(table.columns), table
    [row] = table.iter_rows(named=True)
    assert abs(number(row["p95"]) - 8.8) < 1e-9 and abs(number(row["north_share"]) - 0.5) < 1e-9, row
    assert number(row["total"]) == 12, row


@pytest.mark.parametrize("expression", ["sum(amount", "avg(no_such_column)", "amount * 2"],
                         ids=["syntax-error", "unknown-column", "not-an-aggregate"])
def test_a_custom_statistic_that_is_not_valid_sql_fails_the_block_naming_it(env, expression):
    """Spec 3 (a custom statistic that is not valid SQL fails the block clearly, naming it): an unclosed bracket, a
    column the table does not have, an expression that is no aggregate. (One the block can tell is wrong before it
    runs may instead stop the run from starting, as Validate refuses a flow, naming it.)"""
    rows = custom(("total", "sum(amount)"), ("broken_one", expression))
    result, run_id, status = run(env, {"load": read(write_sales(env)), "summary": statistics({"Custom statistics": rows})},
                                 ["load.table -> summary.table"])

    if run_id is None:
        assert result.returncode != 0, result.stdout[-3000:]
        error = result.stdout + result.stderr
    else:
        assert status.get("summary") == "failed", result.stdout[-3000:]
        error = error_of(env, run_id, "summary")
    assert re.search(r"\bbroken_one\b", error), f"the error does not name the custom statistic: {error!r}"
    assert "Traceback" not in error, error


def test_group_by_gives_a_row_per_group_and_column_custom_per_group_and_a_section_per_group(env):
    """Spec 3 (Group by: stats has the group columns first and a row per group and column, custom a row per group,
    the report a section per group; custom statistics per group) and 6 (nothing silently dropped: the rows whose group
    value is missing are counted too)."""
    rows = custom(("p95", "quantile_cont(amount, 0.95)"), ("total", "sum(amount)"))
    result, run_id, status = run(env, {"load": read(write_sales(env)),
                                       "summary": statistics({"Group by": ["region"], "Custom statistics": rows})},
                                 ["load.table -> summary.table"])

    assert status["summary"] == "success", result.stdout[-3000:]
    stats = output_table(env, run_id, "summary", "stats")
    assert stats.columns[0] == "region", stats.columns
    by = stats_rows(stats, groups=1)
    for group, (count, missing, total) in {"north": (2, 1, 2.0), "south": (1, 0, 0.0), "east": (1, 0, 0.0)}.items():
        row = by.get((group, "amount"))
        assert row is not None, f"no row for amount in group {group}: {sorted(by, key=str)}"
        assert (number(row["count"]), number(row["missing"]), number(row["sum"])) == (count, missing, total), row
        assert (group, "qty") in by, sorted(by, key=str)
    amounts = [r for (_, name), r in by.items() if name == "amount"]
    assert sum(number(r["count"]) + number(r["missing"]) for r in amounts) == len(AMOUNT), amounts

    table = output_table(env, run_id, "summary", "custom")
    assert table.columns[0] == "region" and {"p95", "total"} <= set(table.columns), table
    per_group = {r["region"]: r for r in table.iter_rows(named=True)}
    assert {"north", "south", "east"} <= set(per_group), table
    assert abs(number(per_group["north"]["p95"]) - 3.7) < 1e-9 and number(per_group["north"]["total"]) == 2, table
    assert sum(number(r["total"]) or 0 for r in per_group.values()) == 12, table

    titles = [t for _, t in headings(report(env, run_id, "summary"))]
    for group in ("north", "south", "east"):
        assert any(re.search(rf"\b{group}\b", t) for t in titles), f"no section for {group}: {titles}"


def test_exact_by_default_and_unticked_the_estimated_values_are_marked(env):
    """Spec 3 (Exact ticked by default; unticked, distinct counts and percentiles are estimated, the report shows each
    estimated value with ≈, the stats table saying in an `estimated` column which statistics of the row were
    estimated): ticked, nothing is marked ≈ and nothing is said estimated."""
    blocks = {"load": read(write_sales(env)), "exact": statistics(), "rough": statistics({"Exact": False}, y=300)}
    result, run_id, status = run(env, blocks, ["load.table -> exact.table", "load.table -> rough.table"])

    assert status == {"load": "success", "exact": "success", "rough": "success"}, result.stdout[-3000:]
    exact = output_table(env, run_id, "exact", "stats")
    assert "≈" not in report(env, run_id, "exact")
    assert left_out(exact, "estimated"), exact
    rough = output_table(env, run_id, "rough", "stats")
    assert "estimated" in rough.columns, rough.columns
    said = {name: text(row["estimated"]).lower() for name, row in stats_rows(rough).items()}
    assert "distinct" in said["amount"] and "median" in said["amount"], said
    assert "distinct" in said["region"], said
    assert "≈" in report(env, run_id, "rough")


# --- 2: the extra input ---------------------------------------------------------------------------------------------------------

def test_the_extra_input_is_shown_in_the_report_under_its_own_heading_as_it_is(env):
    """Spec 2 (an optional `extra`: a dict, each key and value shown as a row, or a table, e.g. from a Python Code
    block, shown in the report under its own heading as it is)."""
    blocks = {"load": read(write_sales(env)),
              "targets": python("output = {'target': 100, 'owner': 'ops team'}"),
              "checks": python("output = pl.DataFrame({'check': ['rows', 'amounts'], 'result': ['ok', 'low']})",
                               y=600),
              "with_dict": statistics(), "with_table": statistics(y=300)}
    edges = ["load.table -> with_dict.table", "targets.output -> with_dict.extra",
             "load.table -> with_table.table", "checks.output -> with_table.extra"]
    result, run_id, status = run(env, blocks, edges)

    assert status["with_dict"] == "success" and status["with_table"] == "success", result.stdout[-3000:]
    for block, shown_rows in (("with_dict", [("target", "100"), ("owner", "ops team")]),
                              ("with_table", [("check", "result"), ("rows", "ok"), ("amounts", "low")])):
        markdown = report(env, run_id, block)
        lines = markdown.splitlines()
        found = [i for i, line in enumerate(lines) if all(re.search(rf"\b{re.escape(w)}\b", line) for w in shown_rows[0])]
        assert found, f"{block}: the report does not show {shown_rows[0]} in a row:\n{markdown}"
        for pair in shown_rows[1:]:
            assert any(all(re.search(rf"\b{re.escape(w)}\b", line) for w in pair) for line in lines), (pair, markdown)
        above = [line.strip().lstrip("#").strip().lower() for line in lines[:found[0]] if re.match(r"\s*#", line)]
        assert above, f"{block}: the extra input has no heading above it:\n{markdown}"
        assert group_of(above[-1]) is None and "custom" not in above[-1], (
            f"{block}: the extra input is under the heading {above[-1]!r}, not one of its own")


# --- 5: the report in the Log ---------------------------------------------------------------------------------------------------

def test_the_log_shows_the_overview_each_group_the_custom_statistics_and_extra_then_each_chart(env):
    """Spec 5 (the Log shows the report: the overview, then a section per ticked group with its numbers as Markdown
    tables, the custom statistics and the extra input, then each chart as a picture) and 3 (Overview: rows, columns,
    each column's type, missing cells, fully repeated rows; Correlations between the number columns)."""
    blocks = {"load": read(write_sales(env)), "targets": python("output = {'target': 100}"),
              "summary": statistics({"Custom statistics": custom(("p95", "quantile_cont(amount, 0.95)"))}),
              "keep": keep("summary", "charts")}
    edges = ["load.table -> summary.table", "targets.output -> summary.extra", "summary.charts -> keep.charts"]
    result, run_id, status = run(env, blocks, edges)

    assert status["summary"] == "success", result.stdout[-3000:]
    markdown = report(env, run_id, "summary")
    overview = section(markdown, "Overview")
    assert says(overview, r"\brows?\b", 6) and says(overview, r"\bcolumns?\b", 4), overview
    assert says(overview, r"missing", 3) and says(overview, r"repeat|duplicat", 0), overview
    assert all(re.search(rf"\b{c}\b", overview) for c in COLUMNS), overview
    starts = {}
    for group in GROUPS:
        part = section(markdown, group)
        assert has_table(part), f"the {group} section has no Markdown table:\n{part}"
        starts[group] = markdown.index(part)
    numbers = section(markdown, "Numbers")
    assert any("amount" in line and has_number_near(line, 2.4, 0.005) and has_number_near(line, 12, 0.005)
               for line in numbers.splitlines()), numbers
    pairs = [(a, q) for a, q in zip(AMOUNT, QTY) if a is not None]
    r = pystats.correlation([a for a, _ in pairs], [q for _, q in pairs])
    correlations = section(markdown, "Correlations")
    assert any(("amount" in line or "qty" in line) and has_number_near(line, r, 0.006)
               for line in correlations.splitlines()), (r, correlations)
    assert starts["Overview"] < min(starts[g] for g in GROUPS[1:]), starts
    customs = section(markdown, "custom")
    custom_at = markdown.index(customs)
    assert max(starts.values()) < custom_at, "the custom statistics come before a group's section"
    assert re.search(r"\bp95\b", customs) and has_number_near(customs, 8.8, 0.006), customs
    extra_at = re.search(r"(?m)^.*\btarget\b.*\b100\b.*$", markdown)
    assert extra_at and extra_at.start() > custom_at, "the extra input is not shown after the custom statistics"

    lines = log_of(env, run_id, "summary")
    last_markdown = max(i for i, (_, how, _) in enumerate(lines) if how == "markdown")
    first_picture = min((i for i, (_, how, _) in enumerate(lines) if how == "image"), default=None)
    assert first_picture is not None and first_picture > last_markdown, lines
    charts = kept(env, "summary")["charts"]
    assert [Path(p).resolve() for p in pictures(env, run_id, "summary")] == [Path(p).resolve() for p in charts]


def test_fully_repeated_rows_and_missing_cells_are_in_the_overview(env):
    """Spec 3 (Overview: rows, columns, missing cells, fully repeated rows): a row written three times (two repeats
    of it, or three rows that repeat) and a missing value."""
    make = python("output = pl.DataFrame({'region': ['north', 'north', 'north', 'south', 'east'],\n"
                  "                       'amount': [1.0, 1.0, 1.0, None, 2.0]})")
    result, run_id, status = run(env, {"make": make, "summary": statistics()}, ["make.output -> summary.table"])

    assert status["summary"] == "success", result.stdout[-3000:]
    overview = section(report(env, run_id, "summary"), "Overview")
    assert says(overview, r"\brows?\b", 5) and says(overview, r"\bcolumns?\b", 2), overview
    assert says(overview, r"missing", 1), overview
    assert says(overview, r"repeat|duplicat", 2, 3), overview


# --- 4: the charts ----------------------------------------------------------------------------------------------------------------

def test_the_charts_are_chosen_by_the_columns_types_and_drawn_on_a_light_background(env):
    """Spec 4 (a histogram per number column, the most common values of each text column, rows over time for each
    date column, a heatmap of the correlations when there are 2 or more number columns; with Group by, rows per group;
    a light background) and 2 (charts: a list of PNG files): the number of charts for each kind of column, as Columns
    lists them."""
    cases = {"one_number": ({"Columns": ["amount"]}, 1), "two_numbers": ({"Columns": ["amount", "qty"]}, 3),
             "a_text": ({"Columns": ["region"]}, 1), "a_date": ({"Columns": ["day"]}, 1),
             "every_column": ({}, 5), "grouped": ({"Columns": ["amount"], "Group by": ["region"]}, 2)}
    blocks = {"load": read(write_sales(env)), "keep": keep("charts", *cases, x=800)}
    edges = []
    for i, (name, (settings, _)) in enumerate(cases.items()):
        blocks[name] = statistics(settings, y=300 * i)
        edges += [f"load.table -> {name}.table", f"{name}.charts -> keep.{name}"]
    result, run_id, status = run(env, blocks, edges)

    assert all(status.get(name) == "success" for name in cases), result.stdout[-3000:]
    charts = kept(env, "charts")
    counts = {name: len(charts[name]) for name in cases}
    assert counts == {name: n for name, (_, n) in cases.items()}, counts
    for name in cases:
        for path in charts[name]:
            assert str(path).lower().endswith(".png"), path
            assert_light_png(path)


def test_columns_left_without_a_chart_by_a_limit_are_named_in_the_report(env):
    """Spec 4 (if a limit leaves some columns without a chart, the report says which; nothing is dropped silently):
    a table of 30 number columns gets a histogram for each and the heatmap, or the Log names each column that has no
    chart."""
    make = python("output = pl.DataFrame({f'c{i:02}': [(j * 7 + i * 3 + j * i % 5) % 17 for j in range(40)]\n"
                  "                       for i in range(1, 31)})")
    blocks = {"make": make, "summary": statistics(), "keep": keep("wide", "charts")}
    result, run_id, status = run(env, blocks, ["make.output -> summary.table", "summary.charts -> keep.charts"])

    assert status["summary"] == "success", result.stdout[-3000:]
    charts = kept(env, "wide")["charts"]
    if len(charts) >= 31:
        return
    named_without = set()
    for _, _, message in log_of(env, run_id, "summary"):
        for part in re.split(r"(?m)^[ \t]*#{1,6}[ \t]", message):
            if re.search(r"chart", part, re.I):
                named_without |= set(re.findall(r"\bc\d\d\b", part))
    assert len(named_without) >= 30 - len(charts), (
        f"{len(charts)} charts for 30 number columns; the Log names {sorted(named_without)} as without a chart")


# --- 6: millions of rows, on the table's Parquet file ------------------------------------------------------------------------------

# A big table: an amount empty in every 100th row; every row different (its id), as in real tables.
BIG = """copy (select ['north', 'south', 'east', 'west'][1 + (i % 4)::int] as region,
                      case when i % 100 = 0 then null else (i % 1000) / 4.0 end as amount,
                      (i % 5)::bigint as qty, i as id, date '2026-01-01' + (i % 250)::int as day
               from range({n}) t(i)) to '{path}' (format parquet)"""


def write_big(env, n: int) -> str:
    name = f"big_{n}.parquet"
    duckdb.sql(BIG.format(n=n, path=(env.root / name).as_posix()))
    return name


def test_statistics_of_millions_of_rows_are_exact(env):
    """Spec 6 (it works on tables of millions of rows; missing values counted) and 3 (Exact by default): 3 million
    rows read by Read File, an amount empty in every 100th row: its count, missing values, distinct values, sum, mean,
    median, min and max are exact, and the overview gives the 3,000,000 rows."""
    n = 3_000_000
    result, run_id, status = run(env, {"load": read(write_big(env, n)), "summary": statistics()},
                                 ["load.table -> summary.table"])

    assert status == {"load": "success", "summary": "success"}, result.stdout[-3000:]
    rows = stats_rows(output_table(env, run_id, "summary", "stats"))
    amount = rows["amount"]
    expected = {"count": 2_970_000, "missing": 30_000, "distinct": 990, "sum": 371_250_000, "mean": 125.0,
                "median": 125.0, "min": 0.25, "max": 249.75}
    assert {k: number(amount[k]) for k in expected} == expected, amount
    assert number(rows["region"]["distinct"]) == 4 and number(rows["region"]["count"]) == n, rows["region"]
    assert says(section(report(env, run_id, "summary"), "Overview"), r"\brows?\b", n)


@pytest.mark.skipif(sys.platform == "win32", reason="measures memory with the resource module")
def test_estimated_statistics_of_millions_of_rows_never_hold_the_table_in_memory(env):
    """Spec 6 (it computes on the table's Parquet file, never reading the whole table into memory) with Exact unticked
    (spec 3: faster on very big tables): run alone (as Run only this block runs it, on the table kept from a run),
    the block's process needs under 80 MB more at its peak for 6 million rows than for 600,000, while the 5.4 million
    rows more take about 240 MB (16 + 8 + 8 + 8 + 4 bytes a row)."""
    sizes = (600_000, 6_000_000)
    blocks, edges = {}, []
    for i, n in enumerate(sizes):
        blocks[f"load_{n}"] = read(write_big(env, n), y=300 * i)
        blocks[f"stats_{n}"] = statistics({"Exact": False}, y=300 * i)
        edges.append(f"load_{n}.table -> stats_{n}.table")
    result, run_id, status = run(env, blocks, edges)
    assert set(status.values()) == {"success"}, result.stdout[-3000:]
    assert number(stats_rows(output_table(env, run_id, f"stats_{sizes[1]}", "stats"))["qty"]["count"]) == sizes[1]

    def peak_mb(block: str) -> float:
        proc = subprocess.run([sys.executable, "-c", MAXRSS_SCRIPT, "run", str(env.root / "flow.yaml"), "--only", block,
                               "--inputs-from", str(run_id)], env=env.env, cwd=env.root, capture_output=True,
                              text=True, timeout=600)
        assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
        peak = int(proc.stdout.split("MAXRSS")[1])
        return peak / 2**20 if sys.platform == "darwin" else peak / 2**10  # (bytes on macOS, KB on Linux)

    small, large = peak_mb(f"stats_{sizes[0]}"), peak_mb(f"stats_{sizes[1]}")

    assert large - small < 80, f"peak memory {small:.0f} MB for {sizes[0]:,} rows, {large:.0f} MB for {sizes[1]:,}"


# --- 2, 7: into an HTML Template and an email ------------------------------------------------------------------------------------

def test_read_file_then_statistics_its_charts_and_stats_into_an_html_template_for_an_email(env, smtp):
    """Spec 2 (charts: PNG files HTML Template and Send Email can show) and 7's example (Read File, then Statistics,
    its charts and stats into an HTML Template for an email): a CSV of sales, Group by region, a custom statistic and
    an extra input from a Python Code block; the email shows the stats table and every chart."""
    env.write("sales.csv", "region,amount,qty\nnorth,4,3\nsouth,0,1\nnorth,-2,2\n,10,4\nnorth,,7\neast,0,5\n")
    blocks = {
        "load": read("sales.csv"),
        "targets": python("output = {'target': 100}"),
        "summary": statistics({"Group by": ["region"], "Custom statistics": custom(("total", "sum(amount)"))}),
        "keep": keep("email", "charts", y=600),
        "body": {"type": "reports.html_template", "ui": {"x": 400, "y": 0}, "config": {
            "names": {"summary.stats": "stats", "summary.charts": "charts"},
            "template": "<h2>Sales statistics</h2>{stats}<br>{charts}"}},
        "mail": {"type": "email.send", "ui": {"x": 800, "y": 0},
                 "config": {"connection": "mailer", "to": ["team@example.com"], "subject": "Sales statistics"}},
    }
    edges = ["load.table -> summary.table", "targets.output -> summary.extra", "summary.stats -> body.data",
             "summary.charts -> body.data", "summary.charts -> keep.charts", "body.html -> mail.body"]
    result, run_id, status = run(env, blocks, edges)

    assert set(status.values()) == {"success"}, result.stdout[-3000:]
    [msg] = smtp.messages
    body = msg.get_body(preferencelist=("html",)).get_content()
    assert ">amount<" in body and ">north<" in body and ">mean<" in body, body[:3000]
    images = [p for p in msg.walk() if p.get_content_type() == "image/png" and p.get_content_disposition() == "inline"]
    charts = kept(env, "email")["charts"]
    assert charts and len(images) == len(charts), (len(images), charts)


# --- 4, 5: in the editor: Run only this block, Data preview, the Dark theme ------------------------------------------------------------

def test_run_only_this_block_shows_stats_in_data_preview_and_its_charts_stay_light_in_the_dark_theme(env, win, qtbot):
    """Spec 5 (Run only this block shows stats in Data preview) and 4 (the charts on a light background, also in the
    Dark theme: they are for emails too): in the Dark theme, run the flow, then Run only this block on Statistics;
    only it ran again; selected, Data preview shows its stats table (a row per column of the table); the pictures it
    shows in the Log are drawn on a light background."""
    from taskloom.editor import theme

    app = QApplication.instance()
    style, palette, tips = app.style().name(), QApplication.palette(), QToolTip.palette()
    try:
        theme.apply("Dark")
        path = flow_file(env.root / "stats.yaml", {
            "manual": {"type": "trigger.manual", "ui": {"x": -800, "y": 0}},
            "load": read(write_sales(env)),
            "summary": statistics(),
        }, ["manual.start -> load.after", "load.table -> summary.table"])
        tab = win.open_flow(path)
        settle()
        win.run_flow(tab)
        first = wait_runs(qtbot, tab, 1)
        assert tab.runs[-1].status == "success", win.log.text.toPlainText()[-3000:]

        menu_action(win, tab, "summary", "Run only this block, with the same inputs").trigger()
        second = wait_runs(qtbot, tab, 2)

        assert tab.runs[-1].status == "success", win.log.text.toPlainText()[-3000:]
        assert shown(tab, ["load", "summary"]) == {"load": ("success", first), "summary": ("success", second)}
        select_only(tab, "summary")
        assert win.preview.label.text().startswith("summary.stats"), win.preview.label.text()
        model = win.preview.view.model()
        assert model is not None and model.rowCount() == len(COLUMNS)
        header = [model.headerData(c, Qt.Horizontal).split("\n")[0] for c in range(model.columnCount())]
        assert {"count", "missing", "mean"} <= set(header), header
        names = {model.index(r, 0).data() for r in range(model.rowCount())}
        assert names == set(COLUMNS), names
        charts = pictures(env, second, "summary")
        assert charts, log_of(env, second, "summary")
        for chart in charts:
            assert_light_png(chart)
        clean(win)
    finally:
        if app.style().name() != style:
            QApplication.setStyle(style)
        QApplication.setPalette(palette)
        QToolTip.setPalette(tips)
        QApplication.processEvents()


# --- 7: README and Help > Documentation -------------------------------------------------------------------------------------------

def test_the_readme_and_the_documentation_describe_the_block_with_an_example():
    """Spec 7 (Help > Documentation, the hover help and the README describe the block, with an example: Read File,
    then Statistics, its charts and stats into an HTML Template for an email): README lists data.statistics with the
    built-in blocks and has a YAML example with Read File, Statistics (settings the block accepts) and an HTML Template
    its stats and charts go into; Help > Documentation has the block's entry on the Data page, and its guide (made
    from README) shows the example."""
    from taskloom.editor import docs

    cls = stats_class()
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    listed = readme.split("Built-in blocks:", 1)[1].split("\n\n", 1)[0]
    assert f"`{STATS}`" in listed, listed
    examples = [yaml.safe_load(t) for t in re.findall(r"```ya?ml\n(.*?)```", readme, re.S) if STATS in t]
    assert examples, "README has no YAML example with a Statistics block (data.statistics)"
    found = False
    for flow in examples:
        flow = flow or {}
        blocks = flow.get("blocks") or {}
        stats_blocks = [b for b, spec in blocks.items() if spec.get("type") == STATS]
        for block in stats_blocks:
            block_config(cls, BlockSpec(id=block, type=STATS, config=dict(blocks[block].get("config") or {})))
        templates = {b for b, spec in blocks.items() if spec.get("type") == "reports.html_template"}
        edges = [tuple(part.strip().split(".") for part in edge.split("->")) for edge in flow.get("edges") or []]
        into = {(src[0], src[1]) for src, dst in edges if dst[0] in templates}
        found |= any(spec.get("type") == "data.read_file" for spec in blocks.values()) and any(
            (b, "stats") in into and (b, "charts") in into for b in stats_blocks)
    assert found, "no README example has Read File, then Statistics, its stats and charts into an HTML Template"
    pages = docs.pages(discover(None))
    data = pages["blocks-data.html"]
    assert f'id="{STATS}"' in data and html.escape(cls.description) in data
    guide = [name for name, page in pages.items()
             if name not in ("index.html", "properties.html") and not name.startswith("blocks-") and STATS in page]
    assert guide, "no guide page of Help > Documentation shows the Statistics example"
