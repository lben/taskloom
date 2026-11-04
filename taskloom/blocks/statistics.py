"""The Statistics block: the most useful statistics of a table, as numbers (a stats table, and a report in the Log) and
as charts. DuckDB works them out on the table's Parquet file, so the table is never read into memory whole."""

from __future__ import annotations

import math
import re

import duckdb
import polars as pl

from ..block import Block, fields, ports
from ..engine import Failed
from ..report import GRID, SERIES, SURFACE, TEXT, TEXT_2, _cell, render_chart
from ..table import Table
from .data import _exact_whole_numbers, _sql_literal

TOP = 5  # the most common values a text column's row of stats names
TOP_BARS = 10  # the bars of a chart of the most common values, or of the rows per group
BINS = 20  # a histogram's
EXTRA_ROWS = 100  # an extra table's rows the report shows
# The stats table's statistics, in order, with their types; a ticked group's own ones (GROUP_STATS) are only there when
# it is ticked, estimated only when Exact is not.
STATS = {"count": pl.Int64, "missing": pl.Int64, "missing %": pl.Float64, "distinct": pl.Int64, "mean": pl.Float64,
         "std": pl.Float64, "min": pl.String, "25%": pl.Float64, "median": pl.Float64, "75%": pl.Float64,
         "max": pl.String, "sum": pl.Float64, "zeros": pl.Int64, "negatives": pl.Int64, "shortest": pl.Int64,
         "longest": pl.Int64, "most common": pl.String, "first": pl.String, "last": pl.String, "estimated": pl.String}
GROUP_STATS = {"numbers": ("mean", "std", "25%", "median", "75%", "sum", "zeros", "negatives"),
               "text": ("shortest", "longest", "most common"), "dates": ("first", "last")}
KINDS = {"numbers": "number", "text": "text", "dates": "date"}  # the columns a group's statistics are for
PERCENTILES = {"25%": 0.25, "median": 0.5, "75%": 0.75}
# The columns of a group's table in the report.
SECTIONS = {"numbers": ("count", "missing", "distinct", "mean", "std", "min", "25%", "median", "75%", "max", "sum",
                        "zeros", "negatives"),
            "text": ("count", "missing", "distinct", "shortest", "longest", "most common"),
            "dates": ("count", "missing", "distinct", "first", "last")}


def _q(name: str) -> str:
    """A column's name in SQL."""
    return '"' + name.replace('"', '""') + '"'


def _kind(dtype) -> str | None:
    """number, text or date: which statistics and chart a column of this type gets (None: only the counts)."""
    if dtype.is_numeric():
        return "number"
    if dtype in (pl.String, pl.Categorical, pl.Enum):
        return "text"
    if dtype == pl.Date or dtype == pl.Datetime:
        return "date"
    return None


def _md(value, estimated: bool = False) -> str:
    """A value in a cell of a Markdown table (numbers to 2 decimals, as in an email's tables; a NaN as nan)."""
    if value is None:
        return ""
    text = _cell(value)[0].replace("\r", " ").replace("\n", " ")
    text = re.sub(r"([\\|*_`\[<])", r"\\\1", text)
    return f"≈ {text}" if estimated else text


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in rows])


def _label(groups: list[str], key: tuple) -> str:
    """A group by its values, e.g. region: north."""
    return " · ".join(f"{g}: {'(missing)' if v is None else v}" for g, v in zip(groups, key))


class Statistics(Block):
    type_id = "data.statistics"
    title = "Statistics"
    description = ("Shows the most useful statistics of a table, as numbers and as charts: for each column its type, "
                   "count, missing and distinct values; for numbers mean, spread, percentiles and sum; for text the "
                   "lengths and most common values; for dates the first and the last; correlations between the number "
                   "columns. Tick the groups of statistics you want; optionally per group, with statistics of your own "
                   "in SQL. The report shows in the Log, the stats table in Data preview, and stats and charts can go "
                   "into an HTML Template for an email. Exact by default; untick Exact to estimate distinct counts "
                   "and percentiles of very big tables (shown with ≈).")
    inputs_help = ("table: the table to describe. extra (optional): a dict (each key and value shown as a row) or a "
                   "table, e.g. from a Python Code block, shown in the report under its own heading as it is.")
    category = "Data"
    inputs = {"table": ports.Table(), "extra": ports.Any(required=False)}
    outputs = {"stats": ports.Table(), "charts": ports.Any(), "custom": ports.Table()}
    config = {
        "overview": fields.Bool(default=True, help=(
            "Ticked: the report starts with an overview of the table: its rows and columns, each column's type and "
            "missing values, the missing cells and the fully repeated rows (each the same as an earlier row in "
            "every column).")),
        "numbers": fields.Bool(default=True, help=(
            "Ticked: for each number column the mean, std (the sample standard deviation), 25%, median, 75%, sum, "
            "zeros and negatives, over the values present, and a histogram.")),
        "text": fields.Bool(default=True, help=(
            "Ticked: for each text column the shortest and longest length and its 5 most common values with their "
            "counts, and a chart of its 10 most common values.")),
        "dates": fields.Bool(default=True, help=(
            "Ticked: for each date (or date and time) column the first and the last, and a chart of its rows over "
            "time (per day, month or year).")),
        "correlations": fields.Bool(default=True, help=(
            "Ticked: the correlation (Pearson's r, from -1 to 1) between each two number columns, over the rows "
            "where both have a value, and a heatmap of them (with 2 or more number columns).")),
        "columns": fields.List(default=[], help=(
            "The columns to describe (and chart); none listed: every column (but the Group by ones). A column that is "
            "not in the table fails the block, and so does a number column holding NaN or an infinite value (e.g. "
            "from a division by 0), naming it: make them missing or drop them before this block."),
            examples=["amount, region", "amount"]),
        "group_by": fields.List(default=[], help=(
            "Optional columns to work out the statistics per group of: stats then has these columns first and a row "
            "per group and column, custom a row per group, and the report a section per group (rows with a missing "
            "group value are a group too). A column that is not in the table, or named as a column of stats "
            "(column, type, count, ...), fails the block."),
            examples=["region", "region, product"]),
        "custom": fields.KeyValues(default=[], label="Custom statistics", help=(
            "Statistics of your own, one per row: a name = an SQL aggregate expression over the table, worked out "
            "by DuckDB (per group with Group by), e.g. p95 = quantile_cont(amount, 0.95). Only the ticked rows are "
            "worked out. They go out in custom, a table with a column per row. One that is not valid SQL, or no "
            "aggregate (one value for the whole table), fails the block, naming it."),
            examples=["p95 = quantile_cont(amount, 0.95)",
                      "north_share = avg(case when region = 'north' then 1 else 0 end)",
                      "big_orders = count_if(amount > 1000)"]),
        "exact": fields.Bool(default=True, help=(
            "Ticked (the default): every statistic is exact. Unticked: distinct counts (also the fully repeated "
            "rows) and percentiles (25%, median, 75%) are estimated, faster and with less memory on very big tables; "
            "the report shows each estimated value with ≈ and stats says in its estimated column which statistics of "
            "the row were.")),
    }

    def run(self, ctx, table, extra=None):
        c = self.config
        shown_extra = None if extra is None else _extra(extra)  # (one it cannot show fails it before the work)
        schema = table.schema
        for label, names in (("Columns", c.columns), ("Group by", c.group_by)):
            unknown = [n for n in names if n not in schema]
            if unknown:
                raise Failed(f"{label}: {', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not a column of "
                             f"the table (its columns: {', '.join(schema)})")
        groups = list(dict.fromkeys(c.group_by))
        own = self._stat_columns()
        clash = [g for g in groups if g in own]
        if clash:
            raise Failed(f"Group by: {', '.join(clash)} {'is' if len(clash) == 1 else 'are'} also the name of a column "
                         f"of stats (its columns: {', '.join(own)}); rename it before this block, e.g. in a DuckDB "
                         "SQL block")
        columns = list(dict.fromkeys(c.columns)) or [n for n in schema if n not in groups]
        kinds = {n: _kind(schema[n]) for n in columns}
        numbers = [n for n in columns if kinds[n] == "number"]
        custom = [(r["key"], r["value"].strip()) for r in c.custom if r.get("on", True) is not False]
        con = duckdb.connect()
        try:
            con.execute(f"SET temp_directory = {_sql_literal(str(ctx.new_path('duckdb_tmp')))}")
            con.execute(f"CREATE VIEW input AS SELECT * FROM read_parquet({_sql_literal(str(table.path))})")
            _finite(con, [n for n in numbers if schema[n].is_float()])
            custom_table = self._custom(ctx, con, groups, custom)
            summary = self._summary(con, schema, groups, columns, kinds, numbers)
            report = [self._overview(con, schema, groups, len(summary))] if c.overview else []
            for key, rows, found, pairs in summary:
                sections = self._sections(found, pairs, columns, kinds, numbers, "###" if groups else "##")
                if groups:
                    sections = [f"## {_md(_label(groups, key))}", f"{rows:,} {'row' if rows == 1 else 'rows'}",
                                *sections]
                report.append("\n\n".join(sections))
            charts, without = self._charts(ctx, con, schema, groups, columns, kinds, numbers, summary)
        finally:
            con.close()
        if custom:
            frame = pl.read_parquet(custom_table.path)
            report.append("## Custom statistics\n\n" + _md_table(
                [_md(n) for n in frame.columns], [[_md(v) for v in row] for row in frame.iter_rows()]))
        if shown_extra is not None:
            report.append("## Extra input\n\n" + shown_extra)
        if without:
            report.append("## Columns without a chart\n\n" + "\n".join(f"- {_md(n)}: {why}" for n, why in without))
        for part in report:
            if part:
                ctx.log(part, show="markdown")
        for path in charts:
            ctx.log(path, show="image")
        return {"stats": Table.from_frame(self._stats(schema, groups, columns, kinds, summary),
                                          ctx.new_path("stats.parquet")),
                "charts": charts, "custom": custom_table}

    # --- the numbers --------------------------------------------------------------------------------------------

    def _aggregates(self, column: str, dtype) -> dict[str, str]:
        """The statistics of a column worked out in SQL: {statistic: its aggregate}."""
        c, q, kind = self.config, _q(column), _kind(dtype)
        found = {"count": f"count({q})",
                 "distinct": f"count(DISTINCT {q})" if c.exact else f"approx_count_distinct({q})"}
        if dtype == pl.Duration:  # (from the Parquet file, a bare number of its time unit: made a time span, readable)
            micros = {"ns": f"{q} // 1000", "us": q, "ms": f"{q} * 1000"}[dtype.time_unit]
            found |= {"min": f"CAST(to_microseconds(min({micros})) AS VARCHAR)",
                      "max": f"CAST(to_microseconds(max({micros})) AS VARCHAR)"}
        else:
            found |= {"min": f"CAST(min({q}) AS VARCHAR)", "max": f"CAST(max({q}) AS VARCHAR)"}
        x = f"CAST({q} AS DOUBLE)"
        if kind == "number" and c.numbers:  # (the percentiles: _percentiles)
            found |= {"mean": f"avg({x})", "std": f"stddev_samp({x})", "sum": f"CAST(sum({q}) AS DOUBLE)",
                      "zeros": f"count_if({q} = 0)", "negatives": f"count_if({q} < 0)"}
        if kind == "text" and c.text:
            found |= {"shortest": f"min(length({q}))", "longest": f"max(length({q}))"}
        return found

    def _summary(self, con, schema, groups, columns, kinds, numbers) -> list:
        """Per group (one without Group by), in order: (its values, its rows, {column: {statistic: value}},
        {(number column, number column): correlation})."""
        c = self.config
        wanted = [(n, stat, sql) for n in columns for stat, sql in self._aggregates(n, schema[n]).items()]
        wanted += [(pair, "r", _corr(*pair)) for pair in _pairs(numbers)] if c.correlations else []
        result = con.execute(f"SELECT {', '.join([*map(_q, groups), 'count(*)', *(s for _, _, s in wanted)])} "
                             f"FROM input{_grouped(groups)}").fetchall()
        tops = {n: _top(con, groups, n, TOP)[0] for n in columns if kinds[n] == "text" and c.text}
        percentiles = {n: _percentiles(con, groups, n, c.exact) for n in numbers if c.numbers}
        summary = []
        for row in result:
            key, rows, values = tuple(row[:len(groups)]), row[len(groups)], row[len(groups) + 1:]
            found, correlations = {n: {} for n in columns}, {}
            for (n, stat, _), value in zip(wanted, values):
                if stat == "r":
                    correlations[n] = value
                else:
                    found[n][stat] = value
            for n, stats in found.items():
                stats["missing"] = rows - stats["count"]
                stats["missing %"] = 100 * stats["missing"] / rows if rows else None
                if kinds[n] == "date" and c.dates:
                    stats["first"], stats["last"] = stats["min"], stats["max"]
                if n in percentiles:
                    stats.update(zip(PERCENTILES, percentiles[n].get(key) or [None] * len(PERCENTILES)))
                if n in tops:
                    stats["most common"] = ", ".join(f"{v} ({k:,})" for v, k in tops[n].get(key, []))
                if not c.exact:
                    stats["estimated"] = ", ".join(["distinct"] + (list(PERCENTILES) if "median" in stats else []))
            summary.append((key, rows, found, correlations))
        return summary

    def _stat_columns(self) -> dict:
        """The stats table's own columns (after the Group by ones), with their types."""
        c = self.config
        left_out = {s for group, names in GROUP_STATS.items() if not getattr(c, group) for s in names}
        left_out |= {"estimated"} if c.exact else set()
        return {"column": pl.String, "type": pl.String} | {s: t for s, t in STATS.items() if s not in left_out}

    def _stats(self, schema, groups, columns, kinds, summary) -> pl.DataFrame:
        types = {g: schema[g] for g in groups} | self._stat_columns()
        rows = [{**dict(zip(groups, key)), "column": n, "type": str(schema[n]), **found[n]}
                for key, _, found, _ in summary for n in columns]
        return pl.DataFrame([{k: row.get(k) for k in types} for row in rows], schema=types)

    def _custom(self, ctx, con, groups, custom) -> Table:
        """The custom statistics (with Group by, a row per group, its columns first)."""
        path = ctx.new_path("custom.parquet")
        names = [n for n, _ in custom]

        def check(name, expression, run=False):
            try:  # (with count(*), an expression that is no aggregate is refused, not taken from a row)
                found = con.sql(f"SELECT count(*), ({expression}\n) FROM input{_grouped(groups)}")
                if run:
                    found.fetchall()
            except duckdb.Error as e:
                raise Failed(f"Custom statistics: {name} = {expression}: {str(e).splitlines()[0]} (each is an SQL "
                             "aggregate over the table, one value for it or for each group, e.g. "
                             "quantile_cont(amount, 0.95))") from None

        for name, expression in custom:
            if not expression:
                raise Failed(f"Custom statistics: {name} has no expression: write {name} = an SQL aggregate over the "
                             "table, e.g. sum(amount)")
            if names.count(name) > 1 or name in groups:
                raise Failed(f"Custom statistics: {name} is the name of more than one row"
                             + (" (and a Group by column)" if name in groups else "") + "; give each its own name")
            check(name, expression)
        if not (custom or groups):
            return Table.from_frame(pl.DataFrame(), path)
        parts = [*map(_q, groups), *(f"({e}\n) AS {_q(n)}" for n, e in custom)]
        try:
            _exact_whole_numbers(con.sql(f"SELECT {', '.join(parts)} FROM input{_grouped(groups)}")).write_parquet(
                str(path))
        except duckdb.Error:  # (met while working them out, e.g. a text that is no number: whose, each alone tells)
            for name, expression in custom:
                check(name, expression, run=True)
            raise
        return Table(path)

    # --- the report -----------------------------------------------------------------------------------------------

    def _overview(self, con, schema, groups, n_groups: int) -> str:
        names = list(schema)
        rows, *counts = con.execute(f"SELECT count(*), {', '.join(f'count({_q(n)})' for n in names)} "
                                    "FROM input").fetchone()
        if self.config.exact:
            distinct = con.execute("SELECT count(*) FROM (SELECT DISTINCT * FROM input)").fetchone()[0]
        else:  # (counted exactly, every distinct row is held)
            distinct = con.execute(f"SELECT approx_count_distinct(hash({', '.join(map(_q, names))})) "
                                   "FROM input").fetchone()[0]
        missing = [rows - k for k in counts]
        cells = rows * len(names)
        lines = [f"- **Rows:** {rows:,}", f"- **Columns:** {len(names):,}",
                 f"- **Missing cells:** {sum(missing):,} of {cells:,}"
                 + (f" ({_md(100 * sum(missing) / cells)}%)" if cells else ""),
                 f"- **Fully repeated rows:** {_md(max(rows - distinct, 0), not self.config.exact)} (each the same "
                 "as an earlier row in every column)"]
        if groups:
            lines.append(f"- **Groups:** {n_groups:,}, by {_md(', '.join(groups))}")
        table = _md_table(["Column", "Type", "Missing", "Missing %"],
                          [[_md(n), _md(str(schema[n])), _md(m), _md(100 * m / rows) + "%" if rows else ""]
                           for n, m in zip(names, missing)])
        return "## Overview\n\n" + "\n".join(lines) + "\n\n" + table

    def _sections(self, found, pairs, columns, kinds, numbers, level: str) -> list[str]:
        """The sections of the ticked groups for one group of rows (the whole table without Group by)."""
        c, sections = self.config, []

        def cell(n, stat):
            return _md(found[n].get(stat), stat in found[n].get("estimated", "").split(", "))

        for group, stats in SECTIONS.items():
            if not getattr(c, group):
                continue
            mine = [n for n in columns if kinds[n] == KINDS[group]]
            body = f"No {KINDS[group]} columns."
            if mine:
                body = _md_table(["Column"] + [s.capitalize() for s in stats],
                                 [[_md(n)] + [cell(n, s) for s in stats] for n in mine])
            sections.append(f"{level} {group.capitalize()}\n\n{body}")
        if c.correlations:
            if len(numbers) < 2:
                body = (f"Correlations need 2 or more number columns; there {'is' if len(numbers) == 1 else 'are'} "
                        f"{len(numbers)}.")
            else:
                def r(a, b):
                    return "1.00" if a == b else _md(pairs.get((a, b), pairs.get((b, a))))
                body = ("Pearson's r of each two number columns (-1 to 1), over the rows where both have a value.\n\n"
                        + _md_table([""] + [_md(n) for n in numbers],
                                    [[_md(a)] + [r(a, b) for b in numbers] for a in numbers]))
            sections.append(f"{level} Correlations\n\n{body}")
        return sections

    # --- the charts -------------------------------------------------------------------------------------------------

    def _charts(self, ctx, con, schema, groups, columns, kinds, numbers, summary) -> tuple[list, list]:
        """The charts' paths, and the columns without one, each with why."""
        c, charts, without = self.config, [], []

        def path():
            return ctx.new_path(f"chart_{len(charts) + 1}.png")

        for n in columns:
            if kinds[n] is None:
                without.append((n, f"its type, {schema[n]}, gets no chart (numbers, text and dates do)"))
        if c.numbers and numbers:
            for n, (edges, counts) in _histograms(con, numbers, {n for n in numbers if schema[n].is_integer()}).items():
                if not counts:
                    without.append((n, "it has no values"))
                    continue
                fig, ax = _figure()
                widths = [b - a for a, b in zip(edges, edges[1:])]
                ax.bar(edges[:-1], counts, width=widths, align="edge", color=SERIES[0], edgecolor=SURFACE, linewidth=1)
                _axes(ax, n, "rows")
                charts.append(_save(fig, f"Histogram of {n} ({sum(counts):,} values)", path()))
        if c.text:
            for n in (n for n in columns if kinds[n] == "text"):
                top, distinct = _top(con, (), n, TOP_BARS)
                if not top:
                    without.append((n, "it has no values"))
                    continue
                values = [(v if len(v) <= 40 else v[:39] + "…", k) for v, k in top[()]]
                more = f" (the {TOP_BARS} most common of {distinct[()]:,})" if distinct[()] > TOP_BARS else ""
                charts.append(str(render_chart(pl.DataFrame(values, schema=["value", "rows"], orient="row"), path(),
                                               "barh", "value", ["rows"], f"Most common values of {n}{more}",
                                               x_label=n)))
        if c.dates:
            for n in (n for n in columns if kinds[n] == "date"):
                days = con.sql(f"SELECT CAST(date_trunc('day', {_q(n)}) AS DATE) AS period, count(*) AS rows "
                               f"FROM input WHERE {_q(n)} IS NOT NULL GROUP BY period ORDER BY period").pl()
                if not days.height:
                    without.append((n, "it has no values"))
                    continue
                span = (days["period"][-1] - days["period"][0]).days
                unit, every = ("day", "1d") if span <= 731 else ("month", "1mo") if span <= 50 * 366 else ("year", "1y")
                periods = days.group_by(pl.col("period").dt.truncate(every)).agg(pl.col("rows").sum()).sort("period")
                charts.append(_over_time(periods["period"].to_list(), periods["rows"].to_list(), unit,
                                         f"Rows per {unit} of {n}", path()))
        if c.correlations and len(numbers) >= 2:
            pairs = summary[0][3] if not groups else dict(zip(_pairs(numbers), con.execute(
                f"SELECT {', '.join(_corr(*pair) for pair in _pairs(numbers))} FROM input").fetchone()))
            charts.append(_heatmap(numbers, pairs, path()))
        if groups and summary:
            largest = sorted(summary, key=lambda s: -s[1])[:TOP_BARS]
            more = f" (the {TOP_BARS} largest of {len(summary):,} groups)" if len(summary) > TOP_BARS else ""
            frame = pl.DataFrame([(" · ".join("(missing)" if v is None else str(v) for v in key), rows)
                                  for key, rows, _, _ in largest], schema=["group", "rows"], orient="row")
            charts.append(str(render_chart(frame, path(), "barh", "group", ["rows"],
                                           f"Rows per {', '.join(groups)}{more}", x_label=", ".join(groups))))
        return charts, without


def _finite(con, floats: list[str]):
    """Fails the block, naming them, if these (float) columns hold a NaN or an infinite value: they have no mean,
    spread or order, so the statistics of such a column (or what is usual for it) would be wrong."""
    if not floats:
        return
    found = con.execute("SELECT " + ", ".join(f"count_if(isnan({_q(n)})), count_if(isinf({_q(n)}))" for n in floats)
                        + " FROM input").fetchone()
    bad = [(n, nan, inf) for n, nan, inf in zip(floats, found[::2], found[1::2]) if nan or inf]
    if bad:
        x = _q(bad[0][0])
        raise Failed(f"{', '.join(f'{n} ({nan:,} NaN, {inf:,} infinite)' for n, nan, inf in bad)}: a number column "
                     "holding NaN or an infinite value (e.g. from a division by 0) has no mean, spread or order; make "
                     "them missing or drop them before this block, e.g. in a DuckDB SQL block: "
                     f"select * replace (case when isfinite({x}) then {x} end as {x}) from input")


def _pairs(numbers: list[str]) -> list[tuple[str, str]]:
    """Each two number columns."""
    return [(a, b) for i, a in enumerate(numbers) for b in numbers[i + 1:]]


def _corr(a: str, b: str) -> str:
    """The correlation of two number columns in SQL: over the rows where both have a value."""
    return f"corr(CAST({_q(a)} AS DOUBLE), CAST({_q(b)} AS DOUBLE))"


def _grouped(groups) -> str:
    """The GROUP BY (and ORDER BY) of a query per group: '' without Group by."""
    if not groups:
        return ""
    return (f" GROUP BY {', '.join(map(_q, groups))}"
            f" ORDER BY {', '.join(_q(g) + ' NULLS LAST' for g in groups)}")


def _top(con, groups, column: str, k: int) -> tuple[dict, dict]:
    """The k most common values of a column, per group (by its values; () without Group by): {group: [(value,
    rows), ...]}, most common first, and {group: its number of distinct values}."""
    q, g = _q(column), [_q(n) for n in groups]
    within = f"PARTITION BY {', '.join(g)}" if g else ""
    # (named __...: not as a column of the table may be)
    counted = (f"SELECT {', '.join([*g, f'{q} AS __value'])}, count(*) AS __rows, "
               f"row_number() OVER ({within} ORDER BY count(*) DESC, {q}) AS __rank, "
               f"count(*) OVER ({within}) AS __distinct "
               f"FROM input WHERE {q} IS NOT NULL GROUP BY {', '.join([*g, q])}")
    found = con.execute(f"SELECT {', '.join([*g, '__value', '__rows', '__distinct'])} FROM ({counted}) "
                        f"WHERE __rank <= {k} ORDER BY {', '.join([*g, '__rank'])}").fetchall()
    top, distinct = {}, {}
    for row in found:
        key = tuple(row[:len(g)])
        top.setdefault(key, []).append((str(row[len(g)]), row[len(g) + 1]))
        distinct[key] = row[len(g) + 2]
    return top, distinct


def _percentiles(con, groups, column: str, exact: bool) -> dict:
    """A number column's PERCENTILES, per group: {group: [value, ...]}. A query of its own: worked out exactly,
    they need the column's values in memory (one column at a time, then)."""
    quantile = "quantile_cont" if exact else "approx_quantile"
    found = con.execute(f"SELECT {', '.join([*map(_q, groups), f'{quantile}(CAST({_q(column)} AS DOUBLE), '
                                                                f'{list(PERCENTILES.values())})'])} "
                        f"FROM input{_grouped(groups)}").fetchall()
    return {tuple(row[:-1]): row[-1] for row in found}


def _histograms(con, numbers, whole: set) -> dict[str, tuple[list[float], list[int]]]:
    """Each number column's histogram, as (edges, rows in each bin); none for a column without values: BINS bins of
    the same width from its smallest value to its largest, or a bin per number for whole numbers (`whole`: their
    columns) fewer apart, and one for a column whose values are all the same."""
    doubles = [f"CAST({_q(n)} AS DOUBLE)" for n in numbers]  # (all finite: _finite)
    bounds = con.execute("SELECT " + ", ".join(f"min({x}), max({x})" for x in doubles) + " FROM input").fetchone()
    found, parts = {}, []
    for n, x, low, high in zip(numbers, doubles, bounds[::2], bounds[1::2]):
        if low is None:
            found[n] = ([], [])
            continue
        if n in whole and high - low < BINS or high == low:
            start, width, bins = low - 0.5, 1.0, int(high - low) + 1
        else:
            start, width, bins = low, (high - low) / BINS, BINS
        found[n] = ([start + i * width for i in range(bins + 1)], bins)
        # (least() passes a missing value over: those are left out, not put in the last bin)
        parts.append((n, f"histogram(least(CAST(floor(({x} - ({start!r})) / {width!r}) AS INTEGER), {bins - 1})) "
                         f"FILTER (WHERE {x} IS NOT NULL)"))
    if parts:
        for (n, _), counted in zip(parts, con.execute(f"SELECT {', '.join(s for _, s in parts)} FROM input")
                                   .fetchone()):
            edges, bins = found[n]
            found[n] = (edges, [(counted or {}).get(i, 0) for i in range(bins)])
    return found


def _extra(extra) -> str:
    """The extra input as it is: a dict a row per key and value, a table its rows (at most EXTRA_ROWS)."""
    if isinstance(extra, dict):
        return _md_table(["Name", "Value"], [[_md(str(k)), _md(v)] for k, v in extra.items()])
    if isinstance(extra, Table):
        frame, total = extra.head(EXTRA_ROWS), extra.num_rows
        shown = _md_table([_md(n) for n in frame.columns], [[_md(v) for v in row] for row in frame.iter_rows()])
        return shown + (f"\n\nThe first {EXTRA_ROWS:,} of {total:,} rows." if total > EXTRA_ROWS else "")
    raise Failed(f"extra: a dict or a table is shown in the report, but {type(extra).__name__} {extra!r:.100} arrived")


# --- drawing (as report.render_chart draws, on a light background: also in the Dark theme, they go in emails) --------

def _figure(width: int = 640, height: int = 360):
    from matplotlib.figure import Figure  # (no pyplot: blocks may draw at the same time)

    fig = Figure(figsize=(width / 100, height / 100), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax = fig.subplots()
    ax.set_facecolor(SURFACE)
    ax.tick_params(colors=TEXT_2, labelsize=8, length=0)
    return fig, ax


def _axes(ax, x_label: str, y_label: str):
    from matplotlib.ticker import MaxNLocator

    ax.yaxis.set_major_locator(MaxNLocator(integer=True))  # (rows: whole numbers)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlabel(x_label, color=TEXT_2, fontsize=9)
    ax.set_ylabel(y_label, color=TEXT_2, fontsize=9)


def _save(fig, title: str, path) -> str:
    fig.suptitle(title, x=0.02, ha="left", color=TEXT, fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    return str(path)


def _over_time(periods: list, rows: list[int], unit: str, title: str, path) -> str:
    """Rows per period (a day, month or year): a bar each, none for a period without rows."""
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter

    fig, ax = _figure()
    ax.bar(periods, rows, width={"day": 0.8, "month": 25, "year": 300}[unit], color=SERIES[0])
    locator = AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    if len(periods) <= 12:  # (not between them, e.g. at noon)
        ax.set_xticks(periods)
    _axes(ax, unit, "rows")
    return _save(fig, title, path)


def _heatmap(numbers: list[str], pairs: dict, path) -> str:
    """The correlations between the number columns: blue for -1, grey for 0, red for 1."""
    from matplotlib.colors import LinearSegmentedColormap

    n = len(numbers)
    matrix = [[1.0 if a == b else pairs.get((a, b), pairs.get((b, a))) for b in numbers] for a in numbers]
    matrix = [[math.nan if r is None else r for r in row] for row in matrix]
    side = min(max(360, 120 + 24 * n), 900)
    fig, ax = _figure(max(640, side + 120), side)
    shown = ax.imshow(matrix, cmap=LinearSegmentedColormap.from_list("r", [SERIES[0], "#f0efec", SERIES[7]]),
                      vmin=-1, vmax=1)
    ax.set_xticks(range(n), numbers, rotation=45 if n > 4 else 0, ha="right" if n > 4 else "center")
    ax.set_yticks(range(n), numbers)
    for spine in ax.spines.values():
        spine.set_visible(False)
    if n <= 12:  # (more: the numbers are in the report)
        for i, row in enumerate(matrix):
            for j, r in enumerate(row):
                if not math.isnan(r):
                    ax.text(j, i, f"{r:.2f}", ha="center", va="center", color=TEXT, fontsize=8)
    bar = fig.colorbar(shown, ax=ax, fraction=0.046, pad=0.04)
    bar.ax.tick_params(colors=TEXT_2, labelsize=8, length=0)
    bar.outline.set_edgecolor(GRID)
    return _save(fig, "Correlations between the number columns (Pearson's r)", path)
