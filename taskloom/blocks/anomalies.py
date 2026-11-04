"""Detect Anomalies (data.detect_anomalies): marks the values of a table of numbers over time that are unusual for their
weekday or hour of day, each with a reason. What is usual for a point is worked out from the comparable earlier values
of its series: their median, and their typical spread from it, so one past spike does not skew it."""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import polars as pl

from ..block import Block, fields, ports
from ..engine import Failed
from ..report import GRID, SERIES, SURFACE, TEXT_2
from ..table import Table
from .check_table import _kind, _rows, unread
from .data import _sql_literal
from .statistics import _figure, _finite, _md, _md_table, _q, _save

NONE, WEEKDAY, HOUR = "none", "weekday", "hour of day"
SPREADS = {"low": 4, "medium": 3, "high": 2}  # Sensitivity: how many spreads from the expected value is unusual
NEEDED = 4  # the comparable earlier points a judged point needs
CHARTS = 12  # at most
REASONS = 100  # the reasons of unusual points the Log shows, at most (anomalies has them all)
MOST_DATES = 5_000_000  # a series' dates at its usual step that are checked for gaps, at most
WINDOWS = 2_000_000  # the earlier values held at once while working out what is usual
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DAY, HOUR_US = 86_400_000_000, 3_600_000_000  # in microseconds, as the dates are worked with
UNITS = {"day": DAY, "hour": HOUR_US, "minute": 60_000_000, "second": 1_000_000}
MONTH = 28  # days: dates this far apart or more have no one step (months differ in length)
EPOCH = dt.datetime(1970, 1, 1)


def _number(x: float) -> str:
    """A number with , between thousands and at most 2 decimals."""
    text = f"{x:,.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _moment(us: int) -> dt.datetime:
    return EPOCH + dt.timedelta(microseconds=int(us))


def _date(us: int, daily: bool) -> str:
    """A date (with its time, unless the dates have none) as warnings name it."""
    moment = _moment(us)
    if daily:
        return f"{moment:%Y-%m-%d}"
    return f"{moment:%Y-%m-%d %H:%M}" + (f":{moment:%S}" if moment.second else "")


def _step(us: int) -> str:
    for unit, size in UNITS.items():
        if us % size == 0:
            return f"{us // size:,} {unit}{'' if us == size else 's'}"
    return f"{us / 1_000_000:g} seconds"


def _unit(dates: np.ndarray) -> int:
    """What a series' dates are compared in: whole days when the differences between them are mostly a day or more
    (their median, to the whole hour), else whole hours, minutes or seconds; so times that vary by some seconds or
    minutes from run to run are not taken for a step of their own."""
    sizes = list(UNITS.values())
    if len(dates) > 1:
        middle = np.median(np.diff(dates))
        for size, finer in zip(sizes, sizes[1:]):
            if round(middle / finer) * finer >= size:
                return size
    return sizes[-1]


def _at(whole: int, unit: int, daily: bool) -> str:
    """A date in whole units of its series as warnings name it: 2026-11-02 for days, 2026-11-02 05:00 for hours."""
    return _date(whole * unit, daily or unit == DAY)


def _named(items: list[str], total: int) -> str:
    """Up to 5 of them, and how many more."""
    return ", ".join(items[:5]) + (f" and {total - 5:,} more" if total > 5 else "")


def _usual(block: np.ndarray, rank: np.ndarray, values: np.ndarray, last: int):
    """For each point: how many comparable earlier values it has (those of its block, a series' points comparable with
    each other, at earlier times: `rank` numbers the times in order; a missing value, NaN, is none), and, from the
    latest `last` of them, the expected value (their median) and the spread (1.4826 x their median absolute deviation
    from it, or, when that is 0, 1.2533 x their mean absolute deviation from it), NaN with fewer than NEEDED."""
    width = int(rank.max()) + 2
    key = block * width + rank  # (block, time) as one number, in that order
    order = np.argsort(key, kind="stable")
    history = order[~np.isnan(values[order])]
    earlier_key, earlier = key[history], np.append(values[history], np.nan)
    end = np.searchsorted(earlier_key, key, side="left")  # (blocks before, and strictly earlier times of its own)
    start = np.searchsorted(earlier_key, block * width, side="left")  # (the block's first)
    count = end - start
    expected, spread = np.full(len(values), np.nan), np.full(len(values), np.nan)
    enough = np.flatnonzero(count >= NEEDED)
    step = max(1, WINDOWS // last)
    for i in range(0, len(enough), step):  # (a part at a time: the windows take last values per point)
        rows = enough[i:i + step]
        at = end[rows, None] - last + np.arange(last)
        window = np.where(at >= start[rows, None], earlier[np.maximum(at, 0)], np.nan)
        median = np.nanmedian(window, axis=1)
        deviation = np.abs(window - median[:, None])
        mad = np.nanmedian(deviation, axis=1)
        expected[rows] = median
        spread[rows] = np.where(mad > 0, 1.4826 * mad, 1.2533 * np.nanmean(deviation, axis=1))
    return count, expected, spread


class DetectAnomalies(Block):
    type_id = "data.detect_anomalies"
    title = "Detect Anomalies"
    description = ("Marks the values of a table of numbers over time that are unusual for their weekday or hour of "
                   "day, each with a reason: is today's row count, duration or error count unusual? For each point "
                   "it works out what is usual from the same weekday over the last few weeks (or the same hour of "
                   "day): the median of those earlier values and their typical spread, so one past spike does not "
                   "skew it, and flags a value more than 2, 3 or 4 spreads away (Sensitivity). Optionally one series "
                   "per group (e.g. per server). Its outputs: the table with expected, low, high, anomaly and reason "
                   "columns; the anomalies only; their count, for an If (count > 0); and a chart of each series, "
                   "also shown in the Log. Too little history, missing values, repeated dates and gaps are warned "
                   "about in the Log.")
    inputs_help = ("table: values over time, e.g. daily row counts per server from a Run Query: a date column, one "
                   "or more number columns and optional group columns.")
    category = "Data"
    inputs = {"table": ports.Table()}
    outputs = {"table": ports.Table(), "anomalies": ports.Table(), "count": ports.Any(), "charts": ports.Any()}
    config = {
        "date_column": fields.Text(help=(
            "The column of each value's date: a date or date and time column, or a text column of YYYY-MM-DD or "
            "YYYY-MM-DD HH:MM[:SS] values, read as such. A text value that does not read, or a row without a date, "
            "fails the block, naming them."), examples=["day", "run_date", "started_at"]),
        "number_columns": fields.List(help=(
            "The columns of numbers to check, each on its own: for each, the table gets <column>_expected, _low and "
            "_high. A text column whose values all read as numbers (1234.5) is read as numbers; one with a value "
            "that does not fails the block, naming it. A missing value is not judged, nor taken as a usual value."),
            examples=["rows", "rows, seconds", "error_count"]),
        "group_by": fields.List(default=[], help=(
            "Optional columns whose values make separate series, each judged against its own history, e.g. server: "
            "one series per server."), examples=["server", "server, region"]),
        "pattern": fields.Choice([NONE, WEEKDAY, HOUR], default=WEEKDAY, help=(
            "Which earlier points of its series (at earlier dates) a point is compared with: weekday (the default), "
            "those on the same day of the week, Mondays with Mondays; hour of day, those at the same hour of the "
            "day (the dates need times); none, all of them.")),
        "compare_with_the_last": fields.Int(default=8, minimum=NEEDED, help=(
            "How many of the latest comparable earlier points make what is usual (4 or more; empty: 8): with "
            "weekday and daily values, 8 is the same weekday over the last 8 weeks. A point with fewer than 4 is "
            "not flagged, its reason saying there is not enough history."), examples=["8", "4", "12"]),
        "sensitivity": fields.Choice(list(SPREADS), default="medium", help=(
            "How far from the expected value (the median of the comparable earlier values) a value must be to be "
            "unusual, in spreads (their typical spread): low, 4 spreads (the fewest flagged); medium (the default), "
            "3 spreads; high, 2 spreads (the most flagged).")),
        "only_the_latest": fields.Bool(default=True, help=(
            "Ticked (the default): only the points at the table's latest date are judged (is today's value "
            "unusual?), in whole days for daily values (whole hours for hourly ones), so runs minutes apart on "
            "the same day are each judged; the other rows get expected, low and high, for the chart, but no "
            "anomaly or reason. "
            "Unticked: every point with enough history is judged.")),
    }

    @classmethod
    def problems(cls, config, sources, params):
        return [] if config["number_columns"] else ["Number columns: list at least one column of numbers"]

    def run(self, ctx, table):
        c = self.config
        schema = table.schema
        date, numbers, groups = c.date_column, list(dict.fromkeys(c.number_columns)), list(dict.fromkeys(c.group_by))
        for label, names in (("Date column", [date]), ("Number columns", numbers), ("Group by", groups)):
            unknown = [n for n in names if n not in schema]
            if unknown:
                raise Failed(f"{label}: {', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not a column of "
                             f"the table (its columns: {', '.join(schema)})")
        added = [f"{n}_{part}" for n in numbers for part in ("expected", "low", "high")] + ["anomaly", "reason"]
        taken = [n for n in added if n in schema]
        if taken:
            raise Failed(f"the table already has {'a column' if len(taken) == 1 else 'columns'} named "
                         f"{', '.join(taken)}, which this block adds: rename {'it' if len(taken) == 1 else 'them'} "
                         "first, e.g. in a DuckDB SQL block")
        kinds = {n: _kind(schema[n]) for n in [date, *numbers]}
        if kinds[date] not in ("date", "datetime", "text"):
            raise Failed(f"Date column: {date} is {kinds[date]}: a date or date and time column, or a text column of "
                         "YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS] values, is needed")
        wrong = [f"{n} is {kinds[n]}" for n in numbers if kinds[n] not in ("int", "float", "text")]
        if wrong:
            raise Failed(f"Number columns: {'; '.join(wrong)}: a column of numbers, or a text column of numbers "
                         "(1234.5), is needed")
        frame, daily = self._read(ctx, table, schema, date, numbers, groups, kinds)
        missing = frame["w"].null_count()
        if missing:
            raise Failed(f"Date column: {date} has no value in {_rows(missing)}: each row needs its date; leave such "
                         "rows out first, e.g. in a DuckDB SQL block")
        if c.pattern == HOUR and daily:
            raise Failed(f"Pattern hour of day: the Date column, {date}, has dates without times, and hour of day "
                         "compares the points at the same hour of the day: choose weekday or none, or a date column "
                         "with times (YYYY-MM-DD HH:MM)")
        if frame.height == 0:
            ctx.warn("the table has no rows: nothing was judged")
            return self._outputs(ctx, table, {name: pl.Series(name, [], pl.Float64) for name in added[:-2]},
                                 pl.Series([], dtype=pl.Boolean), [], [])

        n, k, last = frame.height, SPREADS[c.sensitivity], c.compare_with_the_last
        t = frame["w"].dt.epoch("us").to_numpy()
        if groups:
            named = [f"g{i}" for i in range(len(groups))]
            series = frame.select(pl.struct(named).rank("dense")).to_series().to_numpy().astype(np.int64) - 1
            keys = pl.DataFrame({"s": series}).hstack(frame.select(named)).unique("s").sort("s").rows()
            labels = [", ".join("(missing)" if v is None else str(v) for v in key[1:]) for key in keys]
        else:
            series, labels = np.zeros(n, np.int64), [""]
        kind = {NONE: np.zeros(n, np.int64), WEEKDAY: (t // DAY + 3) % 7, HOUR: t // HOUR_US % 24}[c.pattern]
        order = np.lexsort((t, series))  # each series' points in time order
        parts = np.split(order, np.flatnonzero(np.diff(series[order])) + 1)
        unit = np.empty(n, np.int64)
        for rows, size in zip(parts, self._warn_dates(ctx, labels, parts, t, daily, groups)):
            unit[rows] = size
        whole = t // unit  # each date in whole units of its series (see _unit): earlier, latest and same are in them
        rank = np.unique(whole, return_inverse=True)[1].astype(np.int64)
        judge = whole == t.max() // unit if c.only_the_latest else np.ones(n, bool)

        columns, flagged, judged = {}, np.zeros(n, bool), np.zeros(n, bool)
        reasons = {i: [] for i in np.flatnonzero(judge)}
        summary, charts, without = [], [], []
        for j, name in enumerate(numbers):
            values = frame[f"v{j}"].to_numpy()
            count, expected, spread = _usual(series * 24 + kind, rank, values, last)
            low, high = expected - k * spread, expected + k * spread
            present = ~np.isnan(values)
            short = judge & present & (count < NEEDED)
            sure = judge & present & (count >= NEEDED)
            unusual = sure & ((values < low) | (values > high))
            flagged |= unusual
            judged |= short | sure
            for i in reasons:
                reasons[i].append(self._part(name, values[i], count[i], expected[i], spread[i], low[i], high[i],
                                             kind[i], unusual[i]))
            columns |= {f"{name}_expected": expected, f"{name}_low": low, f"{name}_high": high}
            for s, rows in enumerate(parts):
                label = name + (f" ({labels[s]})" if groups else "")
                few, gone = int(short[rows].sum()), int((~present[rows]).sum())
                if few:
                    ctx.warn(f"{label}: {few:,} {'point' if few == 1 else 'points'} with fewer than {NEEDED} "
                             f"comparable earlier points: not enough history, so not flagged")
                if gone:
                    ctx.warn(f"{label}: {gone:,} missing {'value' if gone == 1 else 'values'}: not judged, nor taken "
                             "as a usual value")
                summary.append([_md(label), _md(int((short | sure)[rows].sum())), _md(int(unusual[rows].sum()))])
                if len(charts) < CHARTS:
                    charts.append(_chart(name, label, t[rows], values[rows], low[rows], high[rows], unusual[rows],
                                         date, ctx.new_path(f"chart_{len(charts) + 1}.png")))
                else:
                    without.append(label)

        reason = [None] * n
        for i, found in reasons.items():
            head = f"{DAYS[(t[i] // DAY + 3) % 7][:3]} {_date(t[i], daily)}" + (f", {labels[series[i]]}" if groups
                                                                               else "")
            reason[i] = f"{head}: {' · '.join(found)}"
        anomaly = pl.DataFrame({"u": flagged, "j": judged}).select(pl.when("j").then("u")).to_series()
        self._report(ctx, summary, [reason[i] for i in np.flatnonzero(flagged)], int(judged.sum()), without)
        for path in charts:
            ctx.log(path, show="image")
        return self._outputs(ctx, table, {name: pl.Series(name, v).fill_nan(None) for name, v in columns.items()},
                             anomaly, reason, charts)

    def _read(self, ctx, table, schema, date, numbers, groups, kinds) -> tuple[pl.DataFrame, bool]:
        """The columns it needs, read from the table's file: w (the dates, as dates and times), v0, v1, ... (the
        numbers, NaN when missing) and g0, g1, ... (the groups); and whether the dates have no times. A text column
        that does not read fails the block."""
        source = f"read_parquet({_sql_literal(str(table.path))})"
        con = duckdb.connect()
        try:
            con.execute(f"SET temp_directory = {_sql_literal(str(ctx.new_path('duckdb_tmp')))}")
            con.execute(f"CREATE VIEW input AS SELECT * FROM {source}")
            q = _q(date)
            if kinds[date] == "text":
                found = unread(con, source, date, "date or datetime")
                if found:
                    raise Failed(f"Date column: {found[1]} (a date is YYYY-MM-DD, a date and time YYYY-MM-DD "
                                 "HH:MM[:SS])")
                when = f"TRY_CAST(trim({q}) AS TIMESTAMP)"
                daily = not con.execute(f"SELECT bool_or(length(trim({q})) > 10) FROM input").fetchone()[0]
            else:
                zone = getattr(schema[date], "time_zone", None)  # (a date and time on its own clock)
                when = f"timezone({_sql_literal(zone)}, {q})" if zone else f"CAST({q} AS TIMESTAMP)"
                daily = kinds[date] == "date"
            for name in numbers:
                if kinds[name] == "text":
                    found = unread(con, source, name, "float")
                    if found:
                        raise Failed(f"Number columns: {found[1]} (a number is written as 1234.5)")
            _finite(con, [name for name in numbers if schema[name].is_float()])
            values = [f"TRY_CAST(trim({_q(name)}) AS DOUBLE)" if kinds[name] == "text"
                      else f"CAST({_q(name)} AS DOUBLE)" for name in numbers]
            picked = [f"{when} AS w", *(f"{v} AS v{i}" for i, v in enumerate(values)),
                      *(f"{_q(g)} AS g{i}" for i, g in enumerate(groups))]
            frame = con.sql(f"SELECT {', '.join(picked)} FROM input").pl()
        finally:
            con.close()
        return frame.with_columns(pl.col(f"v{i}").fill_null(np.nan) for i in range(len(numbers))), daily

    def _warn_dates(self, ctx, labels, parts, t, daily, groups) -> list[int]:
        """Warns of each series' repeated dates and gaps, and of a series with no row at the table's latest date, its
        dates taken in its unit (see _unit): two runs on the same day of daily times are a repeated date. Returns each
        series' unit."""
        latest, units = t.max(), []
        for s, rows in enumerate(parts):
            of = f"{labels[s]}: " if groups else ""
            unit = _unit(np.unique(t[rows]))
            units.append(unit)
            dates, counts = np.unique(t[rows] // unit, return_counts=True)  # in whole units
            repeated = [_at(d, unit, daily) for d in dates[counts > 1]]
            if repeated:
                ctx.warn(f"{of}{len(repeated):,} {'date has' if len(repeated) == 1 else 'dates have'} more than one "
                         f"row: {_named(repeated, len(repeated))}; each row is judged on its own. If one value per "
                         "date is meant, add them up first, e.g. in a DuckDB SQL block (sum(...) ... group by ...)")
            if self.config.only_the_latest and dates[-1] != latest // unit:
                ctx.warn(f"{of}no row at the table's latest date, {_at(latest // unit, unit, daily)}: not judged (its "
                         f"latest date is {_at(dates[-1], unit, daily)})")
            if len(dates) < 2:
                continue
            apart = np.diff(dates)
            if unit == DAY and np.median(apart) >= MONTH:
                ctx.warn(f"{of}not checked for gaps: its dates are a month or more apart, and months differ in "
                         "length, so there is no one step to check them at")
                continue
            steps, times = np.unique(apart, return_counts=True)
            if 2 * times.max() < len(apart):
                name = dict(zip(UNITS.values(), UNITS))[unit]
                ctx.warn(f"{of}not checked for gaps: it has no regular step (fewer than half of the differences "
                         f"between its consecutive dates, in whole {name}s, are the same)")
                continue
            step = int(steps[np.argmax(times)])  # its usual step: the most common
            if (dates[-1] - dates[0]) // step + 1 > MOST_DATES:
                ctx.warn(f"{of}not checked for gaps: at its usual step, {_step(step * unit)}, it would have over "
                         f"{MOST_DATES:,} dates")
                continue
            expected = np.arange(dates[0], dates[-1] + 1, step)
            gaps = expected[~np.isin(expected, dates)]
            # (but on a day of the week, or at an hour of the day, the series never has)
            gaps = gaps[np.isin((gaps * unit // DAY + 3) % 7, (dates * unit // DAY + 3) % 7)
                        & np.isin(gaps * unit // HOUR_US % 24, dates * unit // HOUR_US % 24)]
            if len(gaps):
                ctx.warn(f"{of}{len(gaps):,} {'date is' if len(gaps) == 1 else 'dates are'} missing between its "
                         f"first and last date at its usual step, {_step(step * unit)}: "
                         f"{_named([_at(d, unit, daily) for d in gaps[:5]], len(gaps))}")
        return units

    def _part(self, name, value, count, expected, spread, low, high, kind, unusual) -> str:
        """What a reason says of one number column of a judged row."""
        pattern = self.config.pattern
        one, many = {NONE: ("point", "points"), WEEKDAY: (DAYS[kind % 7], DAYS[kind % 7] + "s"),
                     HOUR: (f"point at {kind:02}:00", f"points at {kind:02}:00")}[pattern]
        if np.isnan(value):
            return f"{name}: missing value"
        if count < NEEDED:
            return f"{name}: not enough history: {count} earlier {many}, {NEEDED} needed"
        usual = f"{_number(expected)} ({_number(low)}–{_number(high)})"
        if unusual and spread == 0:
            return f"{name} {_number(value)}; the {min(count, self.config.compare_with_the_last)} earlier {many} " \
                   f"were all {_number(expected)}"
        if unusual:
            return f"{name} {_number(value)}; a usual {one} is {usual}"
        return f"{name} {_number(value)}; within the usual range of a {one}: {usual}"

    def _report(self, ctx, summary, reasons, judged, without):
        c = self.config
        compared = {NONE: "", WEEKDAY: " on the same weekday", HOUR: " at the same hour of the day"}[c.pattern]
        parts = [f"**{judged:,} {'row' if judged == 1 else 'rows'} judged, {len(reasons):,} unusual**: each value "
                 f"against the latest {c.compare_with_the_last} earlier points of its series{compared}, unusual more "
                 f"than {SPREADS[c.sensitivity]} spreads from their median (Sensitivity {c.sensitivity}).",
                 _md_table(["Series", "Judged", "Unusual"], summary)]
        if reasons:
            parts.append("### Unusual points\n\n" + _md_table(["Reason"], [[_md(r)] for r in reasons[:REASONS]]))
            if len(reasons) > REASONS:
                parts.append(f"The first {REASONS} of {len(reasons):,} unusual points: anomalies has them all.")
        if without:
            parts.append(f"No chart for {len(without):,} series ({CHARTS} charts at most): "
                         + ", ".join(_md(label) for label in without) + ".")
        ctx.log("\n\n".join(parts), show="markdown")

    def _outputs(self, ctx, table, columns, anomaly, reason, charts) -> dict:
        added = pl.DataFrame({**columns, "anomaly": anomaly, "reason": pl.Series(reason, dtype=pl.String)})
        result = Table.from_frame(pl.concat([table.scan(), added.lazy()], how="horizontal"),
                                  ctx.new_path("table.parquet"))
        anomalies = Table.from_frame(result.scan().filter(pl.col("anomaly")), ctx.new_path("anomalies.parquet"))
        return {"table": result, "anomalies": anomalies, "count": anomalies.num_rows, "charts": charts}


def _chart(name, label, t, values, low, high, unusual, date, path) -> str:
    """A series' values over time as a line, its usual range (low to high) shaded, its unusual points marked."""
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter

    fig, ax = _figure()
    x = t.astype("datetime64[us]")
    ax.fill_between(x, low, high, color=SERIES[0], alpha=0.15, linewidth=0, label="usual range")
    ax.plot(x, values, color=SERIES[0], linewidth=1.5, label=name)
    if unusual.any():
        ax.scatter(x[unusual], values[unusual], s=36, color=SERIES[7], edgecolors=SURFACE, linewidths=1, zorder=3,
                   label="unusual")
    locator = AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlabel(date, color=TEXT_2, fontsize=9)
    ax.set_ylabel(name, color=TEXT_2, fontsize=9)
    ax.legend(frameon=False, fontsize=8, labelcolor=TEXT_2, loc="upper left")
    return _save(fig, label, path)
