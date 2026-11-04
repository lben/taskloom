"""The Detect Anomalies block (data.detect_anomalies), in the Data category: it marks the values of a table of numbers
over time that are unusual for their weekday or hour of day, each with a reason. Through the real `taskloom run` (also
--only with --inputs-from, as the editor's Run only this block starts it) and `taskloom validate`, a real H2 database
(Run Query) and a local mail server (Send Email), the run history (history.db: each block's output tables, the Log's
lines and how the Log shows them, its start and end), what the blocks after it receive (written down by Python Code
blocks), the offscreen editor (the Blocks list, the canvas, Properties, Run only this block, Data preview, the Dark
theme), README.md and Help > Documentation. Each test names the point of the specification (1-11) it protects.

What the tests take as given (the specification leaves it to the change):
- Settings are found by the labels Properties shows (block.setting_label): Date column, Number columns, Group by,
  Pattern, Compare with the last, Sensitivity, Only the latest. A column setting takes a list of names when it is a
  list (fields.List), else the name as text. Pattern's options are told by the words none, weekday and hour (hour of
  day); Sensitivity's by low, medium and high.
- In `table`, anomaly is true, false or empty (null); reason is a text, empty when null or "".
- A reason or a Log line writes a date YYYY-MM-DD (a weekday's name may come before it), a number with , between
  thousands (and at most 2 decimals); "a usual point says it is within the range" with the word "within".
- A warning is a WARNING line of the block in the Log (ctx.warn); the summary, the reasons and the charts are the
  block's Markdown and image lines (show="markdown", show="image").
- With Only the latest unticked, the points with fewer than 4 comparable earlier points are not flagged (their anomaly
  is not true); whether the summary counts them as judged is left open (either count is taken).
Not tested: that the block reads only the columns it needs (spec 10). Its `table` output carries every input column,
so a correct implementation writes them all out, and Parquet writers hold a row group in memory while they write it:
peak memory cannot tell such an implementation from one that reads the whole table. The 100,000-point series is
timed (spec 10)."""

import html
import os
import random
import re
import statistics as pystats
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolTip  # noqa: E402

from taskloom.block import fields, setting_label  # noqa: E402
from taskloom.flow import BlockSpec, block_config  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_block_icons import built_in, canvas_icons, drawing, unlike  # noqa: E402
from tests.test_block_icons import rows as list_rows  # noqa: E402
from tests.test_lines import menu_action, shown, wait_runs  # noqa: E402
from tests.test_paths_editor_editing import (clean, error_shown, field, flow_file, select_only, settle,  # noqa: E402
                                             type_in)
from tests.test_statistics import (assert_light_png, error_of, has_table, keep, kept, log_of,  # noqa: E402
                                   output_table, pictures, read, report, run, ticks)

REPO = Path(__file__).resolve().parents[1]
DETECT = "data.detect_anomalies"
LATEST = date(2026, 11, 2)  # a Monday: the latest date of most tables here
FIRST = date(2026, 8, 31)  # a Monday, 9 weeks before
K = {"low": 4, "medium": 3, "high": 2}  # Sensitivity: how many spreads from the expected value
WORDS = {"none": "none", "weekday": "weekday", "hour of day": "hour", "low": "low", "medium": "medium", "high": "high"}
# The 8 Mondays before LATEST of server-a and server-b (their first Monday, 9 weeks before, is far from them).
A_MONDAYS = [29000, 30200, 31000, 29800, 29500, 30500, 28000, 32000]  # usual: 30,000 (26,664.15 to 33,335.85)
B_MONDAYS = [5000, 5100, 4900, 5050, 4950, 5200, 4800, 5000]  # usual: 5,000 (4,666.42 to 5,333.59)
BY_SERVER = {"Date column": "day", "Number columns": ["rows"], "Group by": ["server"]}


# --- the block, found by what Properties calls its settings -------------------------------------------------------------

@cache
def detect_class():
    cls = discover(None).get(DETECT)
    assert cls is not None, f"there is no {DETECT} block (Detect Anomalies)"
    return cls


def key(shown_as: str) -> str:
    """The key, in the flow file, of the setting Properties shows as `shown_as`."""
    keys = {setting_label(k, f): k for k, f in detect_class().config.items()}
    assert shown_as in keys, f"Detect Anomalies has no setting labelled {shown_as!r} (its settings: {sorted(keys)})"
    return keys[shown_as]


def option(label: str, word: str) -> str:
    """The option of the choice `label` that is `word` (none, weekday, hour of day; low, medium, high)."""
    f = detect_class().config[key(label)]
    assert isinstance(f, fields.Choice), f"{label} is not a choice: {f!r}"
    found = [o for o in f.options if WORDS[word] in str(o).lower().replace("_", " ")]
    assert len(found) == 1, f"{label}: which option is {word!r}? (its options: {f.options})"
    return found[0]


def setting(label: str, value):
    """(key, value) of a setting as the flow file writes it."""
    f = detect_class().config[key(label)]
    if isinstance(f, fields.Choice):
        value = option(label, value)
    elif isinstance(f, fields.List) and isinstance(value, str):
        value = [value]
    elif isinstance(f, fields.Text) and isinstance(value, list):
        value = ", ".join(value)
    return key(label), value


def detect(settings: dict, x=0, y=0) -> dict:
    """A Detect Anomalies block with these settings (by their labels in Properties); the others left out."""
    return {"type": DETECT, "config": dict(setting(label, value) for label, value in settings.items()),
            "ui": {"x": x, "y": y}}


# --- the method, as the specification gives it ------------------------------------------------------------------------

def usual(earlier: list, k: float = 3) -> tuple[float, float, float]:
    """(expected, low, high) from the comparable earlier values: the median; the spread 1.4826 x their median absolute
    deviation from it, or, when that is 0, 1.2533 x their mean absolute deviation from it; k spreads either side."""
    m = pystats.median(earlier)
    mad = pystats.median(abs(v - m) for v in earlier)
    spread = 1.4826 * mad if mad else 1.2533 * pystats.fmean(abs(v - m) for v in earlier)
    return m, m - k * spread, m + k * spread


def judged(values: list, k: float = 3, last: int = 8) -> list:
    """For each point of a series of comparable points in date order (None: a missing value), its (expected, low,
    high) from the latest `last` earlier values present, or None with fewer than 4 of them."""
    found, earlier = [], []
    for value in values:
        recent = earlier[-last:]
        found.append(usual(recent, k) if len(recent) >= 4 else None)
        if value is not None:
            earlier.append(value)
    return found


def unusual(value, band) -> bool:
    return value < band[1] or value > band[2]


def close(a, b, tolerance=1e-6) -> bool:
    return a is not None and abs(a - b) <= tolerance * max(1.0, abs(b))


# --- tables ---------------------------------------------------------------------------------------------------------------

def days(first: date, last: date = LATEST) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def mondays(n: int, last: date = LATEST) -> list[date]:
    """n Mondays a week apart, the last one `last`."""
    return [last - timedelta(weeks=n - 1 - i) for i in range(n)]


def write(env, name: str, frame: pl.DataFrame) -> str:
    frame.write_parquet(env.root / name)
    return name


def weekly(series: dict[str, list], last: date = LATEST) -> pl.DataFrame:
    """A table of server, day, rows: each server's values on Mondays a week apart, the last on `last`."""
    rows = [(server, day, value) for server, values in series.items()
            for day, value in zip(mondays(len(values), last), values)]
    return pl.DataFrame(rows, schema={"server": pl.String, "day": pl.Date, "rows": pl.Int64}, orient="row")


def daily_counts(latest_a=120431, latest_b=5100) -> list[tuple[date, str, int]]:
    """Daily row counts of server-a and server-b from FIRST to LATEST: on the 8 Mondays before LATEST A_MONDAYS and
    B_MONDAYS, on FIRST far from them (10,000 and 9,000), on the other days numbers of their own."""
    rows = []
    for i, day in enumerate(days(FIRST)):
        if day == LATEST:
            a, b = latest_a, latest_b
        elif day.weekday() == 0:
            week = (day - FIRST).days // 7 - 1
            a, b = (A_MONDAYS[week], B_MONDAYS[week]) if week >= 0 else (10000, 9000)
        else:
            a, b = 20000 + i * 137 % 900, 3000 + i * 53 % 300
        rows += [(day, "server-a", a), (day, "server-b", b)]
    return rows


# --- what a run gave ---------------------------------------------------------------------------------------------------------

def empty(value) -> bool:
    return value is None or str(value).strip() == ""


def warnings_of(env, run_id: int, block: str) -> list[str]:
    return [message for level, _, message in log_of(env, run_id, block) if level == "WARNING"]


def at(table: pl.DataFrame, day, server: str | None = None) -> dict:
    """The one row of `table` at `day` (of `server`)."""
    found = [r for r in table.iter_rows(named=True)
             if r["day"] == day and (server is None or r["server"] == server)]
    assert len(found) == 1, f"{len(found)} rows at {day} {server or ''}: {found}"
    return found[0]


def numbers(text: str) -> list[str]:
    """The numbers written in a text, as written (with , between thousands)."""
    return [t.rstrip(",") for t in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]


def shows(text: str, value: float) -> bool:
    """Whether the text writes `value` (to 2 decimals; with , between thousands from 1,000 on)."""
    return any(abs(float(t.replace(",", "")) - value) <= 0.0051 and (abs(value) < 1000 or "," in t)
               for t in numbers(text))


def says(line: str, *wanted: int) -> bool:
    """Whether a line writes each of these whole numbers (with or without , between thousands)."""
    written = {int(float(t.replace(",", ""))) for t in numbers(line) if "." not in t}
    return all(n in written for n in wanted)


def shows_band(reason: str, band) -> bool:
    """Whether a reason writes the usual value and its range (each to 2 decimals at most)."""
    return all(shows(reason, v) for v in band) and not re.search(r"\d\.\d{3}", reason)


def colour_counts(path: str) -> np.ndarray:
    """How many pixels of a picture are of each of 12 hues (30 degrees each), counting only clear colours: no greys,
    no pale tints."""
    from PIL import Image  # installed with matplotlib

    with Image.open(path) as image:
        hsv = np.asarray(image.convert("RGB").convert("HSV")).reshape(-1, 3).astype(int)
    clear = hsv[(hsv[:, 1] >= 100) & (hsv[:, 2] >= 80)]
    return np.bincount(clear[:, 0] * 12 // 256, minlength=12)


# --- 1, 11: the block, in the Data group, with a Tabler outline icon, its hover help -------------------------------------------

def test_detect_anomalies_is_in_the_data_group_and_explains_itself_on_hover(win):
    """Spec 1 (block type data.detect_anomalies, title "Detect Anomalies", category Data; no Machine Learning category)
    and 11 (the hover help describes the block): the Blocks list shows Detect Anomalies in the Data group, and resting
    on it shows what it does."""
    cls = detect_class()
    assert (cls.title, cls.category) == ("Detect Anomalies", "Data")
    groups = list_rows(win.palette_panel)
    assert not any("machine" in g.lower() for g in groups), sorted(groups)
    data = {item.text(0): item for item in groups.get("Data", [])}
    assert "Detect Anomalies" in data and "Statistics" in data, sorted(data)
    tip = html.unescape(re.sub(r"<[^>]+>", " ", data["Detect Anomalies"].toolTip(0)))
    assert cls.description and " ".join(cls.description.split()) in " ".join(tip.split())
    assert re.search(r"unusual|anomal", cls.description, re.I), cls.description


def test_its_icon_is_a_tabler_outline_drawing_of_unusual_values_shown_on_the_canvas(win, tmp_path):
    """Spec 1 (an icon from Tabler's outline set fitting unusual values, e.g. chart-dots or zoom-exclamation, same
    style as the others): the icon the block has (BLOCK_ICONS) is a Tabler outline SVG, white strokes of width 2 on a
    24x24 box as the others; of every built-in block's icon on the canvas, Detect Anomalies' is the most like that
    drawing."""
    from taskloom.editor.canvas import BLOCK_ICONS

    detect_class()
    assert DETECT in BLOCK_ICONS, "Detect Anomalies has no icon in BLOCK_ICONS"
    icon = BLOCK_ICONS[DETECT]
    assert len(icon) == 1, f"Detect Anomalies' icon is one drawing, with no badge: {icon}"
    name = icon[0]
    assert re.search(r"chart|dots|exclamation|alert|zoom|scatter|radar|spark|activity", name), (
        f"{name}: not an icon of unusual values")
    svg = (REPO / "taskloom" / "editor" / "icons" / f"{name}.svg").read_text(encoding="utf-8")
    assert "icons-tabler-outline" in svg and f"icon-tabler-{name}" in svg, f"{name}.svg is not Tabler's outline {name}"
    for attribute in ('viewBox="0 0 24 24"', 'stroke="#fff"', 'stroke-width="2"', 'stroke-linecap="round"',
                      'stroke-linejoin="round"', 'fill="none"'):
        assert attribute in svg, f"{name}.svg is not drawn as the other icons: no {attribute}"
    body = re.search(r"<svg\b[^>]*>(.*)</svg>", svg, re.S).group(1)
    icons = canvas_icons(win, tmp_path, built_in(win.registry))
    reference = drawing(body)
    closest = min(icons, key=lambda t: unlike(icons[t], reference))
    assert closest == DETECT, (f"{name}.svg is most like the icon of {closest} ({unlike(icons[closest], reference):.3f} "
                               f"against {unlike(icons[DETECT], reference):.3f})")


# --- the user's flow: a query of daily counts, Detect Anomalies, If anomalies, an email with the charts -----------------------

def counts_query(rows) -> str:
    """H2 SQL giving these (day, server, rows) rows."""
    values = ",\n".join(f"(DATE '{day.isoformat()}', '{server}', {n})" for day, server, n in rows)
    return f'select * from (values {values}) as t("day", "server", "rows") order by "day", "server"'


def emailed_flow(env, name: str, rows) -> dict:
    blocks = {
        "counts": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": counts_query(rows)},
                   "ui": {"x": -400, "y": 0}},
        "detect": detect({**BY_SERVER, "Pattern": "weekday"}),
        "any": {"type": "logic.if", "config": {"condition": "value > 0"}, "ui": {"x": 400, "y": 0}},
        "body": {"type": "reports.html_template", "ui": {"x": 800, "y": 0}, "config": {
            "names": {"detect.anomalies": "anomalies", "detect.charts": "charts"},
            "template": "<h2>Unusual row counts</h2>{anomalies}<br>{charts}"}},
        "mail": {"type": "email.send", "ui": {"x": 1200, "y": 0},
                 "config": {"connection": "mailer", "to": ["team@example.com"], "subject": "Unusual row counts"}},
        "keep": keep(name, "count", "charts", y=400),
    }
    edges = ["counts.result -> detect.table", "detect.count -> any.value", "any.true -> body.after",
             "detect.anomalies -> body.data", "detect.charts -> body.data", "body.html -> mail.body",
             "detect.count -> keep.count", "detect.charts -> keep.charts"]
    return run(env, blocks, edges, name=name)


def test_a_query_of_daily_counts_per_server_then_detect_anomalies_then_if_count_an_email_with_the_charts(jdbc_env, smtp):
    """The user's flow (the design: a query of daily counts, then Detect Anomalies, then If anomalies, then an email
    with the chart) and spec 11's example (a Run Query of daily row counts per server, Detect Anomalies by weekday per
    server, an If on count > 0, an HTML Template email with the anomalies and the charts), with spec 2 (anomalies,
    count an int for an If, charts PNG files HTML Template and Send Email show): server-a's 120,431 rows on Monday
    2026-11-02 against usual Mondays of about 30,000 is emailed, with a chart per server; on a day when nothing is
    unusual, count is 0 and no email goes."""
    result, run_id, status = emailed_flow(jdbc_env, "spike", daily_counts())

    assert set(status.values()) == {"success"}, result.stdout[-3000:] + result.stderr[-3000:]
    found = kept(jdbc_env, "spike")
    assert found["count"] == 1 and isinstance(found["count"], int), found
    [msg] = smtp.messages
    body = msg.get_body(preferencelist=("html",)).get_content()
    assert "server-a" in body and "120,431" in body, body[:3000]
    assert ">server-b<" not in body, "server-b's usual Monday is among the anomalies emailed"
    images = [p for p in msg.walk() if p.get_content_type() == "image/png" and p.get_content_disposition() == "inline"]
    assert len(found["charts"]) == 2 and len(images) == 2, (found["charts"], len(images))

    result, run_id, status = emailed_flow(jdbc_env, "calm", daily_counts(latest_a=30100))

    assert status.get("detect") == "success", result.stdout[-3000:] + result.stderr[-3000:]
    assert kept(jdbc_env, "calm")["count"] == 0
    assert status.get("mail") != "success" and len(smtp.messages) == 1, (status, len(smtp.messages))


# --- 2, 3, 4, 6: the table it gives ---------------------------------------------------------------------------------------------

def test_table_is_the_input_in_its_order_plus_expected_low_high_anomaly_and_reason_and_anomalies_its_unusual_rows(env):
    """Spec 2 (table: the input's rows and columns in the same order, plus <column>_expected, _low, _high, then
    anomaly and reason; anomalies: only its rows whose anomaly is true; count their number, an int), 3 (the defaults:
    Pattern weekday, Compare with the last 8, Sensitivity medium, Only the latest ticked; Group by one series per
    server), 4 (the median and spread of the same weekday's latest 8 earlier values, 3 spreads either side) and 6
    (the reason names the weekday and date, the group, the column, the value with thousands separators and the usual
    value with its range; a usual point says it is within it): daily row counts of two servers, in no order, with a
    text column; server-a's latest Monday is unusual, server-b's is not."""
    frame = pl.DataFrame([(s, d, f"note {i}", n) for i, (d, s, n) in enumerate(daily_counts())],
                         schema={"server": pl.String, "day": pl.Date, "note": pl.String, "rows": pl.Int64}, orient="row")
    order = list(range(frame.height))
    random.Random(7).shuffle(order)
    frame = frame[order]
    blocks = {"load": read(write(env, "counts.parquet", frame)), "detect": detect(BY_SERVER),
              "keep": keep("main", "count", y=300)}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table", "detect.count -> keep.count"])

    assert status == {"load": "success", "detect": "success", "keep": "success"}, result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    assert table.columns == [*frame.columns, "rows_expected", "rows_low", "rows_high", "anomaly", "reason"], table.columns
    assert table.select(frame.columns).to_dicts() == frame.to_dicts(), "the input's rows or values are not kept as given"

    a, b = at(table, LATEST, "server-a"), at(table, LATEST, "server-b")
    band_a, band_b = usual(A_MONDAYS), usual(B_MONDAYS)
    for row, band in ((a, band_a), (b, band_b)):
        assert all(close(row[f"rows_{name}"], v) for name, v in zip(("expected", "low", "high"), band)), (row, band)
    assert a["anomaly"] is True and b["anomaly"] is False, (a, b)
    for word in ("Mon", "2026-11-02", "server-a", "rows", "120,431"):
        assert word in a["reason"], f"{word!r} is not in the reason {a['reason']!r}"
    assert shows_band(a["reason"], band_a), f"{a['reason']!r} does not give the usual {band_a}"
    assert re.search(r"within", b["reason"], re.I) and "server-b" in b["reason"] and shows(b["reason"], 5100), b
    assert all(r["anomaly"] is None and empty(r["reason"]) for r in table.iter_rows(named=True) if r["day"] != LATEST)

    anomalies = output_table(env, run_id, "detect", "anomalies")
    assert anomalies.to_dicts() == table.filter(pl.col("anomaly").fill_null(False)).to_dicts(), anomalies
    count = kept(env, "main")["count"]
    assert count == 1 and isinstance(count, int), count


def test_with_several_number_columns_each_is_judged_on_its_own_and_the_reason_names_each_unusual_one(env):
    """Spec 2 (for each number column <column>_expected, _low, _high, in order, then anomaly and reason), 3 (Number
    columns: each checked on its own) and 6 (a row unusual in several number columns: its reason names each of them;
    numbers with at most 2 decimals): weekly rows and seconds of two jobs; the latest of load is unusual in both, of
    report only in seconds."""
    rows = [100, 104, 98, 101, 99, 103, 97, 102]
    secs = [60.5, 61.25, 59.75, 60.0, 62.5, 58.5, 61.0, 60.25]
    frame = pl.DataFrame({"job": ["load"] * 9 + ["report"] * 9, "day": mondays(9) * 2,
                          "rows": rows + [900] + rows + [101], "seconds": secs + [912.3456] + secs + [912.3456]})
    blocks = {"load": read(write(env, "jobs.parquet", frame)),
              "detect": detect({"Date column": "day", "Number columns": ["rows", "seconds"], "Group by": ["job"]})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    assert table.columns == ["job", "day", "rows", "seconds", "rows_expected", "rows_low", "rows_high",
                             "seconds_expected", "seconds_low", "seconds_high", "anomaly", "reason"], table.columns
    latest = {r["job"]: r for r in table.iter_rows(named=True) if r["day"] == LATEST}
    for job in ("load", "report"):
        row = latest[job]
        assert all(close(row[f"rows_{n}"], v) for n, v in zip(("expected", "low", "high"), usual(rows))), row
        assert all(close(row[f"seconds_{n}"], v) for n, v in zip(("expected", "low", "high"), usual(secs))), row
        assert row["anomaly"] is True, row
    both, one = latest["load"]["reason"], latest["report"]["reason"]
    assert "rows" in both and "900" in both and "seconds" in both and shows(both, 912.35), both
    assert not re.search(r"\d\.\d{3}", both), f"a number with more than 2 decimals: {both!r}"
    assert "seconds" in one and shows(one, 912.35), one


# --- 4: the method --------------------------------------------------------------------------------------------------------------

def test_when_the_median_absolute_deviation_is_0_the_spread_is_from_the_mean_absolute_deviation(env):
    """Spec 4 (spread = 1.4826 x the median absolute deviation from the median, or, when that is 0, 1.2533 x the mean
    absolute deviation from the median): 8 earlier Mondays, 6 of them 100, one 120 and one 80: the usual range is 100
    +- 3 x 1.2533 x 5 (81.2 to 118.8): 115 is usual, 125 unusual."""
    history = [100, 100, 100, 100, 100, 120, 80, 100]
    frame = weekly({"server-a": history + [115], "server-b": history + [125]})
    result, run_id, status = run(env, {"load": read(write(env, "flat.parquet", frame)), "detect": detect(BY_SERVER)},
                                 ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    band = usual(history)
    for server, flagged in (("server-a", False), ("server-b", True)):
        row = at(table, LATEST, server)
        assert all(close(row[f"rows_{n}"], v) for n, v in zip(("expected", "low", "high"), band)), (row, band)
        assert row["anomaly"] is flagged, row


def test_when_all_earlier_values_are_equal_any_other_value_is_unusual_and_the_reason_says_so(env):
    """Spec 4 (when all comparable earlier values are equal, any other value is unusual, and the reason says the
    earlier ones were all that value): 8 earlier Mondays of 500; 501 is unusual, saying they were all 500; 500 is
    not."""
    frame = weekly({"server-a": [500] * 8 + [501], "server-b": [500] * 8 + [500]})
    result, run_id, status = run(env, {"load": read(write(env, "same.parquet", frame)), "detect": detect(BY_SERVER)},
                                 ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    a, b = at(table, LATEST, "server-a"), at(table, LATEST, "server-b")
    for row in (a, b):
        assert all(close(row[f"rows_{n}"], 500) for n in ("expected", "low", "high")), row
    assert a["anomaly"] is True and b["anomaly"] is False, (a, b)
    assert re.search(r"\ball\b", a["reason"], re.I) and "500" in a["reason"] and "501" in a["reason"], a["reason"]


def test_sensitivity_low_medium_high_is_4_3_or_2_spreads_from_the_expected_value(env):
    """Spec 3 and 4 (Sensitivity low, medium, high: a value 4, 3 or 2 spreads from the expected value is unusual): the
    same Mondays, the latest at 2.5, 3.5 and 4.5 spreads above and 3.5 below; each Sensitivity's low and high are k
    spreads from the expected value, and it flags the values outside them."""
    expected, low, _ = usual(A_MONDAYS, 1)
    spread = expected - low
    latest = {"up-2.5": 2.5, "up-3.5": 3.5, "up-4.5": 4.5, "down-3.5": -3.5}
    frame = weekly({group: A_MONDAYS + [round(expected + n * spread)] for group, n in latest.items()})
    blocks = {"load": read(write(env, "spreads.parquet", frame))}
    for i, level in enumerate(K):
        blocks[level] = detect({**BY_SERVER, "Sensitivity": level}, y=300 * i)
    result, run_id, status = run(env, blocks, [f"load.table -> {level}.table" for level in K])

    assert set(status.values()) == {"success"}, result.stdout[-3000:]
    for level, k in K.items():
        table = output_table(env, run_id, level, "table")
        for group, n in latest.items():
            row = at(table, LATEST, group)
            assert close(row["rows_low"], expected - k * spread) and close(row["rows_high"], expected + k * spread), (
                level, row)
            assert row["anomaly"] is (abs(n) > k), f"{level} (k={k}): {group} at {n} spreads: {row}"


def test_compare_with_the_last_takes_the_latest_comparable_earlier_points_8_by_default(env):
    """Spec 3 (Compare with the last: how many comparable earlier points make "usual", default 8: the latest ones):
    12 earlier Mondays, the first 4 of them 1,000: by default the latest 8 make the usual value; with 4, the latest 4;
    with 12, all of them."""
    history = [1000] * 4 + [29000, 29800, 29500, 28000, 31000, 30500, 32000, 30200]
    frame = weekly({"server-a": history + [30000]})
    blocks = {"load": read(write(env, "long.parquet", frame)), "default": detect(BY_SERVER),
              "four": detect({**BY_SERVER, "Compare with the last": 4}, y=300),
              "twelve": detect({**BY_SERVER, "Compare with the last": 12}, y=600)}
    result, run_id, status = run(env, blocks, [f"load.table -> {b}.table" for b in ("default", "four", "twelve")])

    assert set(status.values()) == {"success"}, result.stdout[-3000:]
    bands = {"default": usual(history[-8:]), "four": usual(history[-4:]), "twelve": usual(history)}
    assert len({band[0] for band in bands.values()}) == 3, bands  # (the test tells them apart)
    for block, band in bands.items():
        row = at(output_table(env, run_id, block, "table"), LATEST)
        assert all(close(row[f"rows_{n}"], v) for n, v in zip(("expected", "low", "high"), band)), (block, row, band)


# --- 3: the pattern -------------------------------------------------------------------------------------------------------------

def test_weekday_compares_a_point_with_the_same_weekday_none_with_every_earlier_point(env):
    """Spec 3 (Pattern: the comparable earlier points are those of the same weekday (weekday), or all of them
    (none)): daily counts, Mondays about 1,000 and the other days about 100; the latest Monday, 1,000, is usual by
    weekday, and unusual against the 8 days before it."""
    values = {day: (1000 + (i * 7 % 30) - 15 if day.weekday() == 0 else 100 + i * 3 % 20)
              for i, day in enumerate(days(FIRST))}
    values[LATEST] = 1000
    frame = pl.DataFrame({"day": list(values), "rows": list(values.values())})
    blocks = {"load": read(write(env, "daily.parquet", frame)),
              "weekday": detect({"Date column": "day", "Number columns": ["rows"], "Pattern": "weekday"}),
              "none": detect({"Date column": "day", "Number columns": ["rows"], "Pattern": "none"}, y=300)}
    result, run_id, status = run(env, blocks, ["load.table -> weekday.table", "load.table -> none.table"])

    assert set(status.values()) == {"success"}, result.stdout[-3000:]
    earlier = [v for day, v in values.items() if day < LATEST]
    for block, band in (("weekday", usual([v for day, v in values.items() if day < LATEST and day.weekday() == 0][-8:])),
                        ("none", usual(earlier[-8:]))):
        row = at(output_table(env, run_id, block, "table"), LATEST)
        assert all(close(row[f"rows_{n}"], v) for n, v in zip(("expected", "low", "high"), band)), (block, row, band)
        assert row["anomaly"] is unusual(1000, band), (block, row)
    assert at(output_table(env, run_id, "none", "table"), LATEST)["anomaly"] is True


def hourly(first: datetime, last: datetime) -> list[datetime]:
    return [first + timedelta(hours=i) for i in range(int((last - first).total_seconds() // 3600) + 1)]


def test_hour_of_day_compares_the_same_hour_and_reads_text_dates_and_times(env):
    """Spec 3 (Pattern hour of day: the earlier points at the same hour of the day; a text Date column of YYYY-MM-DD
    HH:MM[:SS] values is read as such) and 5 (not enough history: "N earlier points at 09:00, 4 needed"): hourly
    values, high at 09:00, written as text; server-a's latest 09:00 is usual against its 5 earlier 09:00 values;
    server-b has only 2 earlier ones."""
    end = datetime(2026, 11, 2, 9)
    rows, values = [], {}
    for server, start in (("server-a", datetime(2026, 10, 28)), ("server-b", datetime(2026, 10, 31))):
        for i, moment in enumerate(hourly(start, end)):
            value = 500 + i % 7 * 3 if moment.hour == 9 else 50 + i % 5
            values[server, moment] = value
            written = moment.strftime("%Y-%m-%d %H:%M" if i % 2 else "%Y-%m-%d %H:%M:%S")
            rows.append((server, written, value))
    frame = pl.DataFrame(rows, schema={"server": pl.String, "at": pl.String, "rows": pl.Int64}, orient="row")
    blocks = {"load": read(write(env, "hourly.parquet", frame)),
              "detect": detect({"Date column": "at", "Number columns": ["rows"], "Group by": ["server"],
                                "Pattern": "hour of day"})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    assert table.height == frame.height and table["at"].to_list() == frame["at"].to_list()
    latest = {r["server"]: r for r in table.iter_rows(named=True) if r["at"].startswith("2026-11-02 09:00")}
    band = usual([v for (s, m), v in values.items() if s == "server-a" and m.hour == 9 and m < end])
    a, b = latest["server-a"], latest["server-b"]
    assert all(close(a[f"rows_{n}"], v) for n, v in zip(("expected", "low", "high"), band)), (a, band)
    assert a["anomaly"] is False and "2026-11-02" in a["reason"], a
    assert b["anomaly"] is False, b
    assert "not enough history: 2 earlier points at 09:00, 4 needed" in b["reason"], b["reason"]


@pytest.mark.parametrize("written", ["date", "text"])
def test_hour_of_day_with_a_date_column_without_times_fails_saying_so(env, written):
    """Spec 3 (Hour of day with a date column that has no times fails the block clearly, saying so): a Date column of
    dates (or of YYYY-MM-DD text) with Pattern hour of day fails, the error naming the column (or the Date column) and
    the missing times, with no traceback."""
    frame = weekly({"server-a": A_MONDAYS + [30000]})
    if written == "text":
        frame = frame.with_columns(pl.col("day").cast(pl.String))
    blocks = {"load": read(write(env, "dates.parquet", frame)),
              "detect": detect({**BY_SERVER, "Pattern": "hour of day"})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status.get("detect") == "failed", result.stdout[-3000:]
    error = error_of(env, run_id, "detect")
    assert re.search(r"\bday\b|date column", error, re.I) and re.search(r"\btimes?\b", error, re.I), error
    assert "Traceback" not in error, error


# --- 3: Only the latest -----------------------------------------------------------------------------------------------------------

def test_only_the_latest_judges_the_latest_date_alone_and_unticked_every_point_with_enough_history(env):
    """Spec 3 (Only the latest, ticked by default: only the points at the latest date of the whole table are judged;
    the other rows get expected, low and high (for the chart) but an empty anomaly and reason; unticked, every point
    with enough history is judged) and 2 (count): 14 weekly values with a spike in the middle; ticked, only the
    latest is judged and nothing is unusual; unticked, the spike is."""
    values = [100, 104, 98, 101, 99, 103, 97, 102, 400, 100, 98, 101, 99, 102]
    bands = judged(values)
    frame = weekly({"server-a": values})
    blocks = {"load": read(write(env, "spike.parquet", frame)), "latest": detect(BY_SERVER),
              "every": detect({**BY_SERVER, "Only the latest": False}, y=300),
              "keep": keep("counts", "latest", "every", y=600)}
    edges = ["load.table -> latest.table", "load.table -> every.table", "latest.count -> keep.latest",
             "every.count -> keep.every"]
    result, run_id, status = run(env, blocks, edges)

    assert set(status.values()) == {"success"}, result.stdout[-3000:]
    ticked = output_table(env, run_id, "latest", "table").to_dicts()
    unticked = output_table(env, run_id, "every", "table").to_dicts()
    for i, (value, band) in enumerate(zip(values, bands)):
        if band is not None:
            for row in (ticked[i], unticked[i]):
                assert all(close(row[f"rows_{n}"], v) for n, v in zip(("expected", "low", "high"), band)), (i, row)
        if i < len(values) - 1:
            assert ticked[i]["anomaly"] is None and empty(ticked[i]["reason"]), (i, ticked[i])
        else:
            assert not unusual(value, band) and ticked[i]["anomaly"] is False, ticked[i]
        if band is None:
            assert unticked[i]["anomaly"] is not True, (i, unticked[i])
        else:
            assert unticked[i]["anomaly"] is unusual(value, band), (i, value, band, unticked[i])
            assert not empty(unticked[i]["reason"]), (i, unticked[i])
    assert unticked[8]["anomaly"] is True and "400" in unticked[8]["reason"], unticked[8]
    assert kept(env, "counts") == {"latest": 0, "every": sum(unusual(v, b) for v, b in zip(values, bands) if b)}


# --- 5: not enough history, missing values -----------------------------------------------------------------------------------------

def test_a_point_with_fewer_than_4_comparable_earlier_points_says_not_enough_history_and_the_log_warns_once(env):
    """Spec 5 (a judged point with fewer than 4 comparable earlier points gets anomaly false and the reason "not enough
    history: N earlier <Mondays|points>, 4 needed"; the Log warns once per series, saying how many points were
    affected): daily counts; server-a has 9 earlier Mondays, server-b 2 (and 14 earlier days), server-c 0 (and 3
    earlier days); by weekday and with no pattern."""
    rows = []
    for server, first in (("server-a", FIRST), ("server-b", date(2026, 10, 19)), ("server-c", date(2026, 10, 30))):
        rows += [(server, day, 1000 + i * 37 % 100) for i, day in enumerate(days(first))]
    frame = pl.DataFrame(rows, schema={"server": pl.String, "day": pl.Date, "rows": pl.Int64}, orient="row")
    blocks = {"load": read(write(env, "young.parquet", frame)), "weekday": detect(BY_SERVER),
              "none": detect({**BY_SERVER, "Pattern": "none"}, y=300)}
    result, run_id, status = run(env, blocks, ["load.table -> weekday.table", "load.table -> none.table"])

    assert set(status.values()) == {"success"}, result.stdout[-3000:]
    weekday, none = (output_table(env, run_id, b, "table") for b in ("weekday", "none"))
    said = {("weekday", "server-b"): "not enough history: 2 earlier Mondays, 4 needed",
            ("weekday", "server-c"): "not enough history: 0 earlier Mondays, 4 needed",
            ("none", "server-c"): "not enough history: 3 earlier points, 4 needed"}
    for (block, server), reason in said.items():
        row = at(weekday if block == "weekday" else none, LATEST, server)
        assert row["anomaly"] is False and reason in (row["reason"] or ""), (block, server, row)
    for block, table in (("weekday", weekday), ("none", none)):
        assert "not enough history" not in (at(table, LATEST, "server-a")["reason"] or ""), block
    assert "not enough history" not in (at(none, LATEST, "server-b")["reason"] or "")

    for block, short in (("weekday", {"server-b", "server-c"}), ("none", {"server-c"})):
        about = [w for w in warnings_of(env, run_id, block) if "history" in w.lower()]
        for server in ("server-a", "server-b", "server-c"):
            lines = [w for w in about if server in w]
            if server in short:
                assert len(lines) == 1 and says(lines[0], 1), f"{block}: the warnings about {server}: {about}"
            else:
                assert lines == [], f"{block}: {server} has enough history: {about}"


def test_a_missing_number_is_not_judged_nor_used_as_history_and_the_log_says_how_many(env):
    """Spec 5 (a row whose number is missing is not judged and not used as history: its anomaly is empty, its reason
    "missing value", and the Log warns with how many): server-a misses a value among its earlier Mondays, so its usual
    value is from the 8 latest earlier values present; server-b misses its latest value."""
    a = [5000, 100, 102, 98, 101, 99, None, 103, 97, 101]
    b = [100, 104, 98, 101, 99, 103, 97, 102, 100, None]
    frame = weekly({"server-a": a, "server-b": b})
    blocks = {"load": read(write(env, "gaps.parquet", frame)), "detect": detect(BY_SERVER)}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    row_a, row_b = at(table, LATEST, "server-a"), at(table, LATEST, "server-b")
    band = usual([v for v in a[:-1] if v is not None][-8:])
    assert band[0] == 100.5, band  # (5000 is among them: the missing one is not)
    assert all(close(row_a[f"rows_{n}"], v) for n, v in zip(("expected", "low", "high"), band)), (row_a, band)
    assert row_a["anomaly"] is False, row_a
    assert row_b["anomaly"] is None and "missing value" in (row_b["reason"] or "").lower(), row_b
    about = [w for w in warnings_of(env, run_id, "detect") if "missing" in w.lower()]
    assert any(says(w, 2) for w in about) or all(any(s in w and says(w, 1) for w in about)
                                                 for s in ("server-a", "server-b")), about


# --- 3: what it reads -------------------------------------------------------------------------------------------------------------

def test_text_dates_and_text_numbers_that_all_read_are_read_as_dates_and_numbers(env):
    """Spec 3 (a text column of YYYY-MM-DD values is read as dates; a text Number column whose values all read as
    numbers is read as numbers): the dates and the row counts written as text, with spaces around some; the result is
    as for typed columns, and the input's values stay as they were."""
    history = [29000, 30200, 31000, 29800, 29500, 30500, 28000, 32000]
    written = ["29000", " 30200 ", "3.1e4", "29800.0", "29500", "30500", "28000", "32000", "120431"]
    frame = pl.DataFrame({"day": [d.isoformat() for d in mondays(9)], "rows": written})
    blocks = {"load": read(write(env, "text.parquet", frame)),
              "detect": detect({"Date column": "day", "Number columns": ["rows"]})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:] + error_of(env, run_id, "detect")
    table = output_table(env, run_id, "detect", "table")
    assert table["rows"].to_list() == written and table["day"].to_list() == frame["day"].to_list()
    row = table.row(-1, named=True)
    band = usual(history)
    assert all(close(row[f"rows_{n}"], v) for n, v in zip(("expected", "low", "high"), band)), (row, band)
    assert row["anomaly"] is True and "120,431" in row["reason"] and "2026-11-02" in row["reason"], row


UNREAD = {"day": {"2026-13-01": 3, "yesterday": 2, "2026/11/02": 1, "02-11-2026": 1, "2026-11-31": 1,
                  "next monday": 1, "TBD": 1, "Nov 2nd": 1},
          "rows": {"n/a": 3, "29,000": 2, "unknown": 1, "1.2.3": 1, "about 500": 1, "TBD": 1, "lots": 1}}


@pytest.mark.parametrize("column", ["day", "rows"], ids=["date-column", "number-column"])
def test_text_that_does_not_read_fails_the_block_listing_5_values_with_their_rows_and_how_many_more(env, column):
    """Spec 3 (a Date column value that does not read as YYYY-MM-DD [HH:MM[:SS]], or a Number column text that does
    not read as a number, fails the block, listing such values with how many rows have each, at most 5, and how many
    more) with the no-surprises rule: 8 (or 7) such values among good ones."""
    bad = [v for v, n in UNREAD[column].items() for _ in range(n)]
    good = 30 - len(bad)
    frame = pl.DataFrame({"day": [d.isoformat() for d in mondays(30)], "rows": [str(1000 + i) for i in range(30)]})
    frame = frame.with_columns(pl.Series(column, frame[column].to_list()[:good] + bad))
    blocks = {"load": read(write(env, "unread.parquet", frame)),
              "detect": detect({"Date column": "day", "Number columns": ["rows"]})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status.get("detect") == "failed", result.stdout[-3000:]
    error = error_of(env, run_id, "detect")
    assert re.search(rf"\b{column}\b", error) and "Traceback" not in error, error
    listed = [v for v in UNREAD[column] if v in error]
    assert len(listed) == 5, f"{len(listed)} of the values that do not read are listed: {listed}\n{error}"
    for value in listed:
        n = UNREAD[column][value]
        assert re.search(rf"{re.escape(value)}\D{{0,12}}\b{n} rows?\b", error), f"{value}: not {n} rows: {error}"
    assert re.search(rf"\b{len(UNREAD[column]) - 5} more\b", error), error


@pytest.mark.parametrize("label, value", [("Date column", "dya"), ("Number columns", ["rows", "rowz"]),
                                          ("Group by", ["srever"])])
def test_a_column_that_is_not_in_the_table_fails_the_block_naming_it(env, label, value):
    """Spec 3 with the no-surprises rule (when something is wrong it fails clearly): a Date column, Number column or
    Group by column that is not in the table fails the block, the error naming it, with no traceback."""
    frame = weekly({"server-a": A_MONDAYS + [30000]})
    blocks = {"load": read(write(env, "counts.parquet", frame)), "detect": detect({**BY_SERVER, label: value})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status.get("detect") == "failed", result.stdout[-3000:]
    error = error_of(env, run_id, "detect")
    wrong = value if isinstance(value, str) else value[-1]
    assert re.search(rf"\b{wrong}\b", error) and "Traceback" not in error, error


# --- 7: warnings in the Log ---------------------------------------------------------------------------------------------------------

def test_repeated_dates_are_warned_naming_5_and_how_many_and_each_row_is_judged_on_its_own(env):
    """Spec 7 (repeated dates in a series: each such row is still judged on its own; the warning names up to 5 dates
    and how many in all, and says to add them up first, e.g. with DuckDB SQL) and 3 (the comparable earlier points
    are of strictly earlier dates): 7 earlier Mondays twice and the latest twice (101 and 500), with Compare with the
    last 20: both latest rows are judged against the 17 earlier values alone."""
    weeks = mondays(11)
    rows = [(d, 100 + 2 * i) for i, d in enumerate(weeks[:-1])]
    rows += [(d, 91 + 4 * i) for i, d in enumerate(weeks[1:8])]
    history = [v for _, v in rows]
    rows += [(LATEST, 101), (LATEST, 500)]
    frame = pl.DataFrame({"server": ["server-a"] * len(rows), "day": [d for d, _ in rows], "rows": [v for _, v in rows]})
    blocks = {"load": read(write(env, "twice.parquet", frame)),
              "detect": detect({**BY_SERVER, "Compare with the last": 20})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    latest = [r for r in table.iter_rows(named=True) if r["day"] == LATEST]
    band = usual(history)
    assert len(history) == 17 and band[0] not in (usual(history + [101])[0], usual(history + [500])[0]), band
    assert [r["anomaly"] for r in latest] == [False, True], latest
    for row in latest:
        assert all(close(row[f"rows_{n}"], v) for n, v in zip(("expected", "low", "high"), band)), (row, band)

    repeated = [d.isoformat() for d in [*weeks[1:8], LATEST]]
    named = [(w, [d for d in repeated if d in w]) for w in warnings_of(env, run_id, "detect")]
    about = [(w, dates) for w, dates in named if dates]
    assert len(about) == 1, f"no single warning names the repeated dates: {named}"
    [(warning, dates)] = about
    assert len(dates) == 5 and says(warning, 8), warning
    assert re.search(r"\badd|\bsum|DuckDB", warning, re.I), warning


def test_two_runs_on_the_same_day_of_daily_times_are_a_repeated_date_and_neither_is_the_others_history(env):
    """Spec 7 (repeated dates: each row judged on its own; the warning names the date and says to add them up first)
    and 3 (the comparable earlier points are of strictly earlier dates) with the no-surprises rule: a daily job's
    started_at is compared in whole days, so its run at 02:00:05 and its retry at 04:30 on 2026-10-20 are the same
    date: the Log warns of it, and both rows are judged against the same 8 earlier days, the first run's 1,000 not
    among the retry's."""
    shift = random.Random(5)
    rows = [(datetime(d.year, d.month, d.day, 2) + timedelta(seconds=shift.randrange(60)), 100 + i * 37 % 11)
            for i, d in enumerate(days(date(2026, 10, 1), date(2026, 10, 31)))]
    same_day = [(datetime(2026, 10, 20, 2, 0, 5), 1000), (datetime(2026, 10, 20, 4, 30), 101)]
    before = [v for t, v in rows if t.date() < date(2026, 10, 20)][-8:]
    rows = [r for r in rows if r[0].date() < date(2026, 10, 20)] + same_day + \
        [r for r in rows if r[0].date() > date(2026, 10, 20)]
    band = usual(before)
    assert band != usual(before[1:] + [1000]), band  # (the first run among the retry's history would change it)
    frame = pl.DataFrame(rows, schema={"started_at": pl.Datetime("us"), "rows": pl.Int64}, orient="row")
    blocks = {"load": read(write(env, "retried.parquet", frame)),
              "detect": detect({"Date column": "started_at", "Number columns": ["rows"], "Pattern": "none",
                                "Only the latest": False})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    judged_rows = [r for r in table.iter_rows(named=True) if r["started_at"].date() == date(2026, 10, 20)]
    for row in judged_rows:
        assert all(close(row[f"rows_{n}"], v) for n, v in zip(("expected", "low", "high"), band)), (row, band)
    assert [r["anomaly"] for r in judged_rows] == [True, False], judged_rows

    named = [(w, set(re.findall(r"\d{4}-\d{2}-\d{2}", w))) for w in warnings_of(env, run_id, "detect")]
    about = [(w, found) for w, found in named if "2026-10-20" in found]
    assert len(about) == 1, f"no single warning names the repeated date: {named}"
    [(warning, found)] = about
    assert found == {"2026-10-20"} and re.search(r"\badd|\bsum|DuckDB", warning, re.I), warning


def test_gaps_are_warned_naming_5_and_how_many_but_never_the_weekdays_a_series_never_has(env):
    """Spec 7 (gaps: dates missing between a series' first and last date at its usual step, except days of the week
    on which the series never has a row, e.g. weekends in a weekdays-only series: up to 5 named and how many in all):
    two weekdays-only series; steady has every weekday and gets no warning at all; holey misses 7 weekdays."""
    weekdays = [d for d in days(FIRST) if d.weekday() < 5]
    holes = [date(2026, 9, 8), date(2026, 9, 15), date(2026, 9, 23), date(2026, 10, 1), date(2026, 10, 9),
             date(2026, 10, 14), date(2026, 10, 22)]
    rows = [("steady", d, 100 + i % 7) for i, d in enumerate(weekdays)]
    rows += [("holey", d, 100 + i % 7) for i, d in enumerate(weekdays) if d not in holes]
    frame = pl.DataFrame(rows, schema={"server": pl.String, "day": pl.Date, "rows": pl.Int64}, orient="row")
    blocks = {"load": read(write(env, "weekdays.parquet", frame)), "detect": detect(BY_SERVER)}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    warnings = warnings_of(env, run_id, "detect")
    assert not any("steady" in w for w in warnings), warnings
    weekends = [d.isoformat() for d in days(FIRST) if d.weekday() >= 5]
    assert not any(d in w for w in warnings for d in weekends), f"a weekend is named: {warnings}"
    gaps = [(w, [d for d in holes if d.isoformat() in w]) for w in warnings]
    about = [(w, named) for w, named in gaps if named]
    assert len(about) == 1, f"no single warning names the missing weekdays: {warnings}"
    [(warning, named)] = about
    assert "holey" in warning and len(named) == 5 and says(warning, 7), warning


@pytest.mark.parametrize("step, seconds, removed, shown, written", [
    (timedelta(days=1), 300, datetime(2026, 10, 14, 2), "2026-10-14", r"\d{4}-\d{2}-\d{2}"),
    (timedelta(hours=1), 41, datetime(2026, 10, 14, 5), "2026-10-14 05", r"\d{4}-\d{2}-\d{2} \d{2}")],
    ids=["daily", "hourly"])
def test_times_that_vary_by_some_seconds_or_minutes_are_checked_for_gaps_in_whole_days_or_hours(
        env, step, seconds, removed, shown, written):
    """Spec 7 (gaps: dates missing between a series' first and last date at its usual step, the most common difference
    between consecutive dates) with the no-surprises rule: the differences are counted in whole days (or hours), so a
    daily job's started_at at 02:00 plus up to 5 minutes, or hourly times at hh:00:00 to hh:00:40, are not short of
    dates for their varying times. Two such series: steady has every day (hour) and no warning of gaps; holey misses
    one, and one warning names it, and only it."""
    shift = random.Random(12)
    times = {i: removed + i * step + timedelta(seconds=shift.randrange(seconds)) for i in range(-120, 30)}
    rows = [(server, moment, 100 + i % 7) for server in ("steady", "holey") for i, moment in times.items()
            if server == "steady" or i != 0]
    frame = pl.DataFrame(rows, schema={"server": pl.String, "started_at": pl.Datetime("us"), "rows": pl.Int64},
                         orient="row")
    blocks = {"load": read(write(env, "started.parquet", frame)),
              "detect": detect({**BY_SERVER, "Date column": "started_at"})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    warnings = warnings_of(env, run_id, "detect")
    steady = [w for w in warnings if "steady" in w and re.search(r"\bgaps?\b|\bmissing\b|\d{4}-\d{2}-\d{2}", w, re.I)]
    assert steady == [], f"steady misses nothing: {steady}"
    named = [(w, set(re.findall(written, w))) for w in warnings if "holey" in w]
    about = [(w, found) for w, found in named if found]
    assert len(about) == 1, f"no single warning names holey's missing {shown}: {warnings}"
    [(warning, found)] = about
    assert found == {shown}, warning


MONTHLY = [date(2024 + m // 12, m % 12 + 1, 1) for m in range(35)]  # the 1st of each month, 2024-01 to 2026-11
IRREGULAR = [date(2026, 5, 1) + timedelta(days=sum(1 + j % 4 for j in range(i))) for i in range(49)]  # 1, 2, 3, 4 apart


@pytest.mark.parametrize("dates, why", [(MONTHLY, r"\bmonths?\b"), (IRREGULAR, r"regular|\bsteps?\b")],
                         ids=["a-month-apart", "no-regular-step"])
def test_gaps_are_not_checked_of_dates_a_month_apart_or_without_a_regular_step_and_the_log_says_why(env, dates, why):
    """Spec 7 (gaps at a series' usual step) with the no-surprises rule: the 1st of each month, 28 to 31 days apart,
    have no one step; dates 1, 2, 3 and 4 days apart in turn have none that most of their differences share. Their
    gaps are not checked: no date is named as missing, and the Log says so once, saying why (dates a month or more
    apart; no regular step)."""
    frame = pl.DataFrame({"day": dates, "rows": [100 + i % 7 for i in range(len(dates))]})
    blocks = {"load": read(write(env, "dates.parquet", frame)),
              "detect": detect({"Date column": "day", "Number columns": ["rows"], "Pattern": "none"})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    warnings = warnings_of(env, run_id, "detect")
    rows = {d.isoformat() for d in dates}
    named = [d for w in warnings for d in re.findall(r"\d{4}-\d{2}-\d{2}", w) if d not in rows]
    assert named == [], f"dates that are not missing are named: {named[:10]}\n{warnings}"
    said = [w for w in warnings if re.search(r"\bgaps?\b", w, re.I)
            and re.search(r"\bnot\b|n't\b|\bno\b|\bskip", w, re.I) and re.search(why, w, re.I)]
    assert len(said) == 1, f"no single warning says the gaps were not checked, and why: {warnings}"


def test_a_series_with_no_row_at_the_latest_date_is_not_judged_and_the_log_names_it_with_its_own_latest_date(env):
    """Spec 7 (a series with no row at the table's latest date, with Only the latest: it is not judged, and the
    warning names it and its own latest date): server-c's last Monday is 2026-10-26, a week before the table's."""
    frame = pl.concat([weekly({"server-a": A_MONDAYS + [30000]}),
                       weekly({"server-c": A_MONDAYS}, last=date(2026, 10, 26))])
    blocks = {"load": read(write(env, "behind.parquet", frame)), "detect": detect(BY_SERVER)}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    assert all(r["anomaly"] is None for r in table.iter_rows(named=True) if r["server"] == "server-c"), table
    assert at(table, LATEST, "server-a")["anomaly"] is False
    warnings = warnings_of(env, run_id, "detect")
    assert any("server-c" in w and "2026-10-26" in w for w in warnings), warnings


def test_only_the_latest_takes_the_latest_day_of_daily_times_so_a_series_minutes_apart_is_judged(env):
    """Spec 3 (Only the latest: the points at the table's latest date are judged) and 7 (a series with no row at the
    table's latest date is not judged, and the Log names it) with the no-surprises rule: the latest date of daily
    started_at times is a day, as their gaps are counted in whole days. server-a's last run started at 02:00:07 and
    server-b's at 02:03:41 on the same day: both are judged, neither warned of; server-c's last is a day earlier."""
    shift = random.Random(3)
    last = {"server-a": (LATEST, 7), "server-b": (LATEST, 221), "server-c": (LATEST - timedelta(days=1), 95)}
    rows = []
    for server, (end, seconds) in last.items():
        for i, day in enumerate(days(FIRST, end)):
            started = datetime(day.year, day.month, day.day, 2)
            rows.append((server, started + timedelta(seconds=seconds if day == end else shift.randrange(300)),
                         100 + i % 7))
    frame = pl.DataFrame(rows, schema={"server": pl.String, "started_at": pl.Datetime("us"), "rows": pl.Int64},
                         orient="row")
    blocks = {"load": read(write(env, "started.parquet", frame)),
              "detect": detect({**BY_SERVER, "Date column": "started_at"})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    for server in ("server-a", "server-b"):
        row = table.filter(pl.col("server") == server).row(-1, named=True)
        assert row["anomaly"] is not None and not empty(row["reason"]), (server, row)
        assert "missing value" not in row["reason"], (server, row)
    assert all(r["anomaly"] is None for r in table.iter_rows(named=True) if r["server"] == "server-c")
    warnings = warnings_of(env, run_id, "detect")
    behind = [w for w in warnings if re.search(r"\blatest\b", w, re.I) and ("server-a" in w or "server-b" in w)]
    assert behind == [], behind
    assert any("server-c" in w and "latest" in w.lower() and "2026-11-01" in w for w in warnings), warnings


# --- 8, 9: the charts and the Log ------------------------------------------------------------------------------------------------------

def test_a_chart_per_series_on_a_light_background_the_unusual_points_in_a_contrasting_colour(env):
    """Spec 8 (charts: one per series, number column x group; on a light background; the unusual points marked in a
    contrasting colour) and 2 (charts: a list of PNG files): two number columns of two servers give 4 charts; the
    chart of a series whose latest value is unusual has a clear colour, in a good number of pixels, that the chart of
    the same series with a usual latest value hardly has."""
    secs = [60.5, 61.25, 59.75, 60.0, 62.5, 58.5, 61.0, 60.25, 60.0]
    two = weekly({"server-a": A_MONDAYS + [30000], "server-b": B_MONDAYS + [5000]})
    two = two.with_columns(pl.Series("seconds", secs * 2))
    blocks = {"flagged_data": read(write(env, "flagged.parquet", weekly({"server-a": A_MONDAYS + [120431]}))),
              "calm_data": read(write(env, "calm.parquet", weekly({"server-a": A_MONDAYS + [30500]})), y=300),
              "two_data": read(write(env, "two.parquet", two), y=600),
              "flagged": detect(BY_SERVER), "calm": detect(BY_SERVER, y=300),
              "four": detect({**BY_SERVER, "Number columns": ["rows", "seconds"]}, y=600),
              "keep": keep("charts", "flagged", "calm", "four", x=500, y=900)}
    edges = ["flagged_data.table -> flagged.table", "calm_data.table -> calm.table", "two_data.table -> four.table",
             "flagged.charts -> keep.flagged", "calm.charts -> keep.calm", "four.charts -> keep.four"]
    result, run_id, status = run(env, blocks, edges)

    assert set(status.values()) == {"success"}, result.stdout[-3000:]
    charts = kept(env, "charts")
    assert (len(charts["flagged"]), len(charts["calm"]), len(charts["four"])) == (1, 1, 4), charts
    for path in [p for paths in charts.values() for p in paths]:
        assert str(path).lower().endswith(".png"), path
        assert_light_png(path)
    flagged, calm = colour_counts(charts["flagged"][0]), colour_counts(charts["calm"][0])
    new = [h for h in range(12) if flagged[h] - calm[h] >= 40 and calm[h] < calm.max() / 2]
    assert new, f"no colour marks the unusual point: hues {flagged.tolist()} against {calm.tolist()}"


def test_over_12_series_the_log_names_those_left_without_a_chart(env):
    """Spec 8 (one chart per series, up to 12; if the limit leaves series without a chart, the Log says which; nothing
    is dropped silently): 15 servers give 12 charts, and the Log names the others."""
    servers = [f"server-{i:02}" for i in range(1, 16)]
    frame = weekly({s: [v + i for v in A_MONDAYS] + [30000] for i, s in enumerate(servers)})
    blocks = {"load": read(write(env, "many.parquet", frame)), "detect": detect(BY_SERVER),
              "keep": keep("many", "charts", y=300)}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table", "detect.charts -> keep.charts"])

    assert status["detect"] == "success", result.stdout[-3000:]
    charts = kept(env, "many")["charts"]
    assert len(charts) == 12, charts
    named = set()
    for _, how, message in log_of(env, run_id, "detect"):
        if how != "image" and re.search(r"chart", message, re.I):
            named |= set(re.findall(r"server-\d\d", message))
    assert len(named) >= 3, f"the Log names {sorted(named)} as without a chart"


def test_the_log_shows_a_summary_per_series_the_reasons_of_the_unusual_points_as_a_table_then_each_chart(env):
    """Spec 9 (the Log shows a short summary: how many points judged, how many unusual, per series; the reasons of the
    unusual points as a Markdown table; then each chart as a picture): two series judged at every point (Only the
    latest unticked), north with 2 unusual points and south with 1."""
    north = [100, 104, 98, 101, 99, 103, 97, 102, 400, 100, 98, 101, 30, 102]
    south = [50, 52, 49, 51, 48, 53, 47, 50, 10, 51, 49]
    frame = weekly({"north": north, "south": south})
    blocks = {"load": read(write(env, "regions.parquet", frame)),
              "detect": detect({**BY_SERVER, "Only the latest": False}), "keep": keep("log", "charts", y=300)}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table", "detect.charts -> keep.charts"])

    assert status["detect"] == "success", result.stdout[-3000:]
    table = output_table(env, run_id, "detect", "table")
    markdown = report(env, run_id, "detect").replace("\\", "")
    lines = markdown.splitlines()
    for series, values in (("north", north), ("south", south)):
        bands = judged(values)
        flagged = sum(unusual(v, b) for v, b in zip(values, bands) if b)
        enough = sum(b is not None for b in bands)
        assert flagged == {"north": 2, "south": 1}[series]
        assert any(series in line and says(line, flagged) and (says(line, enough) or says(line, len(values)))
                   for line in lines), f"no summary line for {series} ({enough} judged, {flagged} unusual):\n{markdown}"
    reasons = [r["reason"] for r in table.iter_rows(named=True) if r["anomaly"] is True]
    assert len(reasons) == 3 and has_table(markdown), (reasons, markdown)
    rows = [line for line in lines if line.lstrip().startswith("|")]
    for reason in reasons:
        assert any(reason in row for row in rows), f"{reason!r} is not in a table of the Log:\n{markdown}"

    log = log_of(env, run_id, "detect")
    last_markdown = max(i for i, (_, how, _) in enumerate(log) if how == "markdown")
    first_picture = min((i for i, (_, how, _) in enumerate(log) if how == "image"), default=None)
    assert first_picture is not None and first_picture > last_markdown, log
    charts = kept(env, "log")["charts"]
    assert len(charts) == 2
    assert [Path(p).resolve() for p in pictures(env, run_id, "detect")] == [Path(p).resolve() for p in charts]


def test_run_only_this_block_shows_table_in_data_preview_and_its_charts_stay_light_in_the_dark_theme(env, win, qtbot):
    """Spec 9 (Run only this block shows `table` in Data preview) and 8 (the charts on a light background, also in the
    Dark theme: they are for emails too): in the Dark theme, run the flow, then Run only this block on Detect
    Anomalies; only it ran again; selected, Data preview shows its table (the input's rows, with anomaly and reason);
    the pictures it shows in the Log are drawn on a light background."""
    from taskloom.editor import theme

    frame = weekly({"server-a": A_MONDAYS + [120431], "server-b": B_MONDAYS + [5100]})
    app = QApplication.instance()
    style, palette, tips = app.style().name(), QApplication.palette(), QToolTip.palette()
    try:
        theme.apply("Dark")
        path = flow_file(env.root / "detect.yaml", {
            "manual": {"type": "trigger.manual", "ui": {"x": -800, "y": 0}},
            "load": read(write(env, "counts.parquet", frame)),
            "detect": detect(BY_SERVER),
        }, ["manual.start -> load.after", "load.table -> detect.table"])
        tab = win.open_flow(path)
        settle()
        win.run_flow(tab)
        first = wait_runs(qtbot, tab, 1)
        assert tab.runs[-1].status == "success", win.log.text.toPlainText()[-3000:]

        menu_action(win, tab, "detect", "Run only this block, with the same inputs").trigger()
        second = wait_runs(qtbot, tab, 2)

        assert tab.runs[-1].status == "success", win.log.text.toPlainText()[-3000:]
        assert shown(tab, ["load", "detect"]) == {"load": ("success", first), "detect": ("success", second)}
        select_only(tab, "detect")
        assert win.preview.label.text().startswith("detect.table"), win.preview.label.text()
        model = win.preview.view.model()
        assert model is not None and model.rowCount() == frame.height
        header = [model.headerData(c, Qt.Horizontal).split("\n")[0] for c in range(model.columnCount())]
        assert {"server", "day", "rows", "anomaly", "reason"} <= set(header), header
        charts = pictures(env, second, "detect")
        assert len(charts) == 2, log_of(env, second, "detect")
        for chart in charts:
            assert_light_png(chart)
        clean(win)
    finally:
        if app.style().name() != style:
            QApplication.setStyle(style)
        QApplication.setPalette(palette)
        QToolTip.setPalette(tips)
        QApplication.processEvents()


# --- 3: what Properties and Validate refuse -----------------------------------------------------------------------------------------

def test_validate_refuses_a_missing_date_column_or_number_columns_and_compare_with_the_last_under_4(env):
    """Spec 3 (Properties and Validate refuse a missing Date column or Number columns, and a Compare with the last
    under 4, saying which): each such block is named with the setting; one that has what it needs is not."""
    frame = weekly({"server-a": A_MONDAYS + [30000]})
    blocks = {"load": read(write(env, "counts.parquet", frame)),
              "no_date": detect({"Number columns": ["rows"]}), "no_numbers": detect({"Date column": "day"}, y=300),
              "three": detect({**BY_SERVER, "Compare with the last": 3}, y=600),
              "fine": detect({**BY_SERVER, "Compare with the last": 4}, y=900)}
    edges = [f"load.table -> {b}.table" for b in blocks if b != "load"]
    checked = env.taskloom("validate", flow_file(env.root / "flow.yaml", blocks, edges))
    errors = [line for line in checked.stderr.splitlines() if line.startswith("error:")]

    assert checked.returncode != 0, checked.stdout + checked.stderr

    def about(block: str) -> str:
        return " ".join(line for line in errors if line.startswith(f"error: {block}:")).lower()

    assert "date column" in about("no_date"), errors
    assert "number column" in about("no_numbers"), errors
    assert "compare with the last" in about("three") and "4" in about("three"), errors
    assert about("fine") == "", errors


def test_a_new_block_has_the_defaults_and_properties_says_what_it_needs_until_it_has_it(win, tmp_path):
    """Spec 3 (Pattern weekday, Compare with the last 8, Sensitivity medium, Only the latest ticked by default;
    Properties refuses a missing Date column or Number columns, and a Compare with the last under 4, saying which): a
    block added from the Blocks list starts with the defaults, Only the latest shown ticked; selected, a block with no
    settings is refused for its Date column, then for its Number columns, then not; Compare with the last 3 is
    refused."""
    cls = detect_class()
    frame = weekly({"server-a": A_MONDAYS + [30000]})
    frame.write_parquet(tmp_path / "counts.parquet")
    path = flow_file(tmp_path / "props.yaml", {
        "load": {"type": "data.read_file", "config": {"path": str(tmp_path / "counts.parquet")}, "ui": {"x": -400, "y": 0}},
        "detect": {"type": DETECT, "ui": {"x": 0, "y": 0}}}, ["load.table -> detect.table"])
    tab = win.open_flow(path)
    settle()
    tab.view.add_item(DETECT, QPointF(400, 300))
    settle()
    [added] = [b for b, spec in tab.doc.blocks.items() if spec["type"] == DETECT and b != "detect"]
    select_only(tab, added)
    assert ticks(win).get("Only the latest") is True, ticks(win)
    written = {**(tab.doc.blocks[added].get("config") or {}), **dict([setting("Date column", "day"),
                                                                      setting("Number columns", "rows")])}
    config = block_config(cls, BlockSpec(id=added, type=DETECT, config=written))
    assert config[key("Pattern")] == option("Pattern", "weekday"), config
    assert config[key("Sensitivity")] == option("Sensitivity", "medium"), config
    assert config[key("Compare with the last")] == 8 and config[key("Only the latest")] is True, config

    select_only(tab, "detect")
    assert "date column" in error_shown(win).lower(), error_shown(win)
    type_in(field(win, "Date column"), "day")
    settle()
    assert "number column" in error_shown(win).lower(), error_shown(win)
    type_in(field(win, "Number columns"), "rows")
    settle()
    assert error_shown(win) == ""
    type_in(field(win, "Compare with the last"), "3")
    settle()
    assert "compare with the last" in error_shown(win).lower(), error_shown(win)
    type_in(field(win, "Compare with the last"), "8")
    settle()
    assert error_shown(win) == ""
    clean(win)


# --- 10: a long series -------------------------------------------------------------------------------------------------------------

def test_a_series_of_100000_points_is_judged_in_a_few_seconds(env):
    """Spec 10 (it handles a series of 100,000 points in a few seconds on a CPU) with 3 and 4 (hour of day, every point
    judged with Only the latest unticked, by the specification's method): 100,000 hourly values (over 11 years);
    the block takes under 10 seconds, and its anomaly of every point with enough history is the method's."""
    start = datetime(2015, 1, 1)
    rng = np.random.default_rng(1)
    n = 100_000
    values = (1000 + 300 * np.sin(np.arange(n) * 2 * np.pi / 24) + rng.normal(0, 20, n)).round(3).tolist()
    frame = pl.DataFrame({"at": [start + timedelta(hours=i) for i in range(n)], "rows": values})
    blocks = {"load": read(write(env, "long.parquet", frame)),
              "detect": detect({"Date column": "at", "Number columns": ["rows"], "Pattern": "hour of day",
                                "Only the latest": False})}
    result, run_id, status = run(env, blocks, ["load.table -> detect.table"])

    assert status["detect"] == "success", result.stdout[-3000:] + error_of(env, run_id, "detect")
    [(began, ended)] = env.query("SELECT started, finished FROM blocks WHERE run_id=? AND block=?", run_id, "detect")
    took = (datetime.fromisoformat(ended) - datetime.fromisoformat(began)).total_seconds()
    assert took < 10, f"Detect Anomalies took {took:.1f} s for {n:,} points"

    flags = output_table(env, run_id, "detect", "table")["anomaly"].to_list()
    hours = [values[h::24] for h in range(24)]
    wrong = []
    for h, series in enumerate(hours):
        for j, band in enumerate(judged(series)):
            i = h + 24 * j
            if band is None or min(abs(series[j] - band[1]), abs(series[j] - band[2])) < 1e-6:
                continue
            if flags[i] is not unusual(series[j], band):
                wrong.append((i, series[j], band, flags[i]))
    assert not wrong, f"{len(wrong)} points judged otherwise than by the method, e.g. {wrong[:3]}"


# --- 11: README and Help > Documentation -------------------------------------------------------------------------------------------

def readme_about(readme: str, title: str) -> str:
    """README's text from each line naming `title` (outside code blocks) to the next heading."""
    found, taking, fenced = [], False, False
    for line in readme.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        heading = not fenced and re.match(r"#{1,6} ", line)
        if heading and title not in line:
            taking = False
        if not fenced and title in line:
            taking = True
        if taking:
            found.append(line)
    return "\n".join(found)


def test_the_readme_and_the_documentation_describe_the_block_the_method_and_the_example_flow():
    """Spec 11 (Help > Documentation, the hover help and the README describe the block, the method in plain words (the
    median and typical spread of the same weekday over the last few weeks, Sensitivity as spreads), and an example
    flow: a Run Query of daily row counts per server, then Detect Anomalies by weekday per server, then an If on
    count > 0, then an HTML Template email with the anomalies and the charts) and 3 (Sensitivity's help says plainly
    4, 3 or 2 spreads): README lists data.detect_anomalies with the built-in blocks, speaks of the median, the spread,
    the weekday and Sensitivity where it names the block, and has a YAML example of that flow the block accepts; Help
    > Documentation has the block's entry on the Data page, and a guide page shows the example."""
    from taskloom.editor import docs

    cls = detect_class()
    helps = " ".join([cls.description, *(f.help for f in cls.config.values())]).lower()
    assert "median" in helps and "spread" in helps, helps
    sensitivity = " ".join(cls.config[key("Sensitivity")].help.split()).lower()
    assert all(re.search(rf"\b{k}\b", sensitivity) for k in ("4", "3", "2")) and "spread" in sensitivity, sensitivity

    readme = (REPO / "README.md").read_text(encoding="utf-8")
    listed = readme.split("Built-in blocks:", 1)[1].split("\n\n", 1)[0]
    assert f"`{DETECT}`" in listed, listed
    about = " ".join(readme_about(readme, "Detect Anomalies").lower().split())
    for word in ("median", "spread", "weekday", "sensitivity"):
        assert word in about, f"README says nothing of the {word} where it names Detect Anomalies:\n{about[:3000]}"

    def the_example(flow) -> bool:
        if not isinstance(flow, dict) or not isinstance(flow.get("blocks"), dict):
            return False
        blocks, edges = flow["blocks"], {" ".join(str(e).split()) for e in flow.get("edges") or []}
        typed = {t: [b for b, spec in blocks.items() if isinstance(spec, dict) and spec.get("type") == t]
                 for t in (DETECT, "db.run_query", "logic.if", "reports.html_template", "email.send")}
        for d in typed[DETECT]:
            config = block_config(cls, BlockSpec(id=d, type=DETECT, config=dict(blocks[d].get("config") or {})))
            if not (config[key("Group by")] and config[key("Pattern")] == option("Pattern", "weekday")):
                continue
            queried = any(f"{q}.result -> {d}.table" in edges for q in typed["db.run_query"])
            checked = any(f"{d}.count -> {i}.value" in edges
                          and re.search(r">\s*0\b", str((blocks[i].get("config") or {}).get("condition")))
                          for i in typed["logic.if"])
            emailed = any(f"{d}.anomalies -> {t}.data" in edges and f"{d}.charts -> {t}.data" in edges
                          and any(f"{t}.html -> {m}.body" in edges for m in typed["email.send"])
                          for t in typed["reports.html_template"])
            if queried and checked and emailed:
                return True
        return False

    examples = [yaml.safe_load(code) for code in re.findall(r"```ya?ml\n(.*?)```", readme, re.S) if DETECT in code]
    assert any(the_example(flow) for flow in examples), (
        "README has no YAML example of Run Query, then Detect Anomalies by weekday per server, then an If on count > 0, "
        "then an HTML Template with the anomalies and the charts into Send Email")

    pages = docs.pages(discover(None))
    data = pages["blocks-data.html"]
    assert f'id="{DETECT}"' in data and html.escape(cls.description) in data
    guide = [name for name, page in pages.items()
             if name not in ("index.html", "properties.html") and not name.startswith("blocks-") and DETECT in page]
    assert guide, "no guide page of Help > Documentation shows the Detect Anomalies example"
