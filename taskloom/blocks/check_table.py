"""Check Table (checks.check_table): checks a table and passes it on unchanged only when its checks pass. DuckDB works
each check out on the table's Parquet file, a row group at a time, so the table is never read into memory."""

from __future__ import annotations

import datetime as dt
import re
from contextlib import closing

import duckdb
import polars as pl

from ..block import Block, fields, has_placeholders, ports, setting_label
from ..engine import Failed
from ..expr import ExpressionError, evaluate
from ..history import History
from ..table import Table
from .checks import FAIL, WARN
from .data import _sql_literal as _literal

IF_FAILS = (f"What a failure of this check does: {FAIL} (the default: the table is not passed on, and the block's "
            f"error output carries results) or {WARN} (a warning in the Log, and the table goes on).")
TYPES = ("text", "int", "float", "bool", "date", "datetime")  # (the flow parameters' type names)
EXAMPLES = 5  # the example rows (or values) a failing check shows
NUMBER = r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?"  # as Python and SQL write numbers
DATE, TIME = "[0-9]{4}-[0-9]{2}-[0-9]{2}", "[0-9]{2}:[0-9]{2}(:[0-9]{2}([.][0-9]+)?)?"
ROW = "file_row_number"  # (DuckDB's: a row's place in the Parquet file, from 0)
# The table's summary in the run history, e.g. "table: 1,234 rows × 5 columns; results: ...": the row count this block
# passed on in an earlier run, as Run history shows it (engine._summary, Table.summary).
PASSED_ON = re.compile(r"(?:^|; )table: ([\d,]+) rows ×")


def _on(tick: str) -> dict:
    return {"only_if": {tick: [True]}}


def _if_fails(tick: str) -> fields.Choice:
    return fields.Choice([FAIL, WARN], default=FAIL, label="If it fails", help=IF_FAILS, **_on(tick))


class _Unchecked(Exception):
    """A check that could not be checked as it is set up (a column the table does not have, a condition that is not
    valid SQL, ...): it fails the block, also when it is set to warn, as nothing was checked."""


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _kind(dtype) -> str:
    """A column's type, named as the flow parameters' types are (text, int, float, bool, date, datetime), or else by
    Polars' name for it (time, list, ...)."""
    if dtype == pl.String or isinstance(dtype, (pl.Categorical, pl.Enum)):
        return "text"
    if dtype.is_integer():
        return "int"
    if dtype.is_float() or dtype.is_decimal():
        return "float"
    if dtype == pl.Boolean:
        return "bool"
    if dtype == pl.Date:
        return "date"
    if isinstance(dtype, pl.Datetime):
        return "datetime"
    return type(dtype).__name__.lower()


def _reads(kind: str, value: str) -> str:
    """SQL: whether the text `value` (an SQL expression) reads as `kind`, spaces around it aside: a number as Python
    and SQL write it (1234.5), a date YYYY-MM-DD, a date and time YYYY-MM-DD HH:MM[:SS], yes/no as a flow
    parameter's (true, false, yes, no, 1, 0)."""
    value = f"trim({value})"
    if kind == "int":
        return f"regexp_full_match({value}, '[+-]?[0-9]+')"
    if kind == "float":
        return f"regexp_full_match({value}, '{NUMBER}')"
    if kind == "date":
        return f"(regexp_full_match({value}, '{DATE}') AND TRY_CAST({value} AS DATE) IS NOT NULL)"
    if kind == "datetime":
        return f"(regexp_full_match({value}, '{DATE}[ T]{TIME}') AND TRY_CAST({value} AS TIMESTAMP) IS NOT NULL)"
    return f"lower({value}) IN ('true', 'false', 'yes', 'no', '1', '0')"  # (bool)


def _shown(value) -> str:
    """A value as a check's details show it: text quoted, a missing value as missing."""
    if value is None:
        return "missing"
    if isinstance(value, str):
        return repr(value if len(value) <= 60 else value[:60] + "...")
    return str(value)


def _rows(n: int) -> str:
    return f"{n:,} row{'' if n == 1 else 's'}"


def _allowed(text: str) -> tuple[float, bool]:
    """No missing values' Allowed: (the number, whether it is a percentage of the rows)."""
    found = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(%?)\s*", text)
    if not found:
        raise ValueError(f"{text!r} is not a number of values or a percentage of the rows: write e.g. 0, 10 or 5%")
    return float(found[1]), bool(found[2])


def _instants(*values, zone) -> list[dt.datetime]:
    """Dates and times as moments to compare: a date is its midnight. When one has a time zone, one without is on the
    flow's clock (`zone`; None: this computer's)."""
    values = [v if isinstance(v, dt.datetime) else dt.datetime.combine(v, dt.time()) for v in values]
    if any(v.tzinfo for v in values):
        values = [v if v.tzinfo else v.replace(tzinfo=zone) if zone else v.astimezone() for v in values]
    return values


def _duckdb_message(error: Exception) -> str:
    """DuckDB's message without the SQL it quotes."""
    return " ".join(str(error).split("\n\nLINE")[0].split())


class CheckTable(Block):
    type_id = "checks.check_table"
    title = "Check Table"
    description = ("Checks a table before the flow uses it, and passes it on unchanged only when its checks pass: its "
                   "row count (also compared with the last successful run), no missing values, unique columns, the "
                   "columns and their types, values within a range or a list, freshness, and conditions every row "
                   "must meet. Each ticked check fails the block (the default) or only warns in the Log; a failure "
                   "says how many rows break it, with example rows. results has a row per check. It works on the "
                   "table's file, so tables of millions of rows are fine.")
    category = "Checks"
    inputs = {"table": ports.Table()}
    outputs = {"table": ports.Table(), "results": ports.Table()}
    config = {
        "row_count": fields.Bool(default=False, label="Row count", help=(
            "Ticked: the table has at least At least rows and/or at most At most rows.")),
        "at_least": fields.Int(required=False, minimum=0, label="At least", help=(
            "The fewest rows the table may have (empty: no minimum)."), examples=["1", "1000", "50000"],
            **_on("row_count")),
        "at_most": fields.Int(required=False, minimum=0, label="At most", help=(
            "The most rows the table may have (empty: no maximum)."), examples=["100000", "1000000", "5000000"],
            **_on("row_count")),
        "row_count_fails": _if_fails("row_count"),
        "compared": fields.Bool(default=False, label="Row count compared with the last successful run", help=(
            "Ticked: the table's rows differ by at most Within % from the rows this block passed on in the latest "
            "earlier run of this flow in which it succeeded (as Run history shows). With no such run it passes, "
            "saying there is nothing to compare with yet. Not inside a Loop: its items have no single earlier "
            "count.")),
        "within": fields.Float(default=30, label="Within %", help=(
            "How far, in percent of the earlier run's rows, the row count may be from it, more or fewer "
            "(empty: 30)."), examples=["30", "10", "50"], **_on("compared")),
        "compared_fails": _if_fails("compared"),
        "missing": fields.Bool(default=False, label="No missing values", help=(
            "Ticked: no column of In columns (none listed: every column) has more missing values than Allowed. A null "
            "is missing; so is an empty text, or one of only spaces, with Count empty text as missing ticked.")),
        "missing_columns": fields.List(required=False, label="In columns", help=(
            "The columns that must have no missing values (empty: every column)."),
            examples=["amount", "order_id, amount", "day, region, amount"], **_on("missing")),
        "empty_text": fields.Bool(default=False, label="Count empty text as missing", help=(
            "Ticked: an empty text, or one of only spaces, is missing too, as a null is."), **_on("missing")),
        "allowed": fields.Text(default="0", label="Allowed", help=(
            "How many missing values each column may have: a number of values, or a percentage of the rows written "
            "with % (empty: 0)."), examples=["0", "10", "5%"], **_on("missing")),
        "missing_fails": _if_fails("missing"),
        "unique": fields.Bool(default=False, label="Unique", help=(
            "Ticked: no value of Key columns repeats (several columns: no two rows have the same values in all of them "
            "together, e.g. day and region). A row missing one of them is left out (No missing values checks those). "
            "A failure says how many values repeat, with examples and their rows.")),
        "unique_columns": fields.List(required=False, label="Key columns", help=(
            "The column, or the columns together, whose values must not repeat."),
            examples=["order_id", "day, region"], **_on("unique")),
        "unique_fails": _if_fails("unique"),
        "types": fields.Bool(default=False, label="Columns and types", help=(
            "Ticked: each column of Column types is in the table, with its type: text, int, float (any number), bool, "
            "date or datetime (empty: any type). A text column whose type is to be a number, a date or yes/no passes "
            "only if every value reads as one (numbers as 1234.5, dates YYYY-MM-DD, dates and times YYYY-MM-DD "
            "HH:MM[:SS], yes/no true, false, yes, no, 1 or 0; missing values aside); a failure lists the values "
            "that do not, with how many rows have each.")),
        "types_columns": fields.KeyValues(required=False, sep=": ", label="Column types", help=(
            "A row per column the table must have: its name and its type (text, int, float, bool, date or "
            "datetime), or no type to only check it is there."),
            examples=["amount: float", "day: date", "order_id: int"], **_on("types")),
        "types_fails": _if_fails("types"),
        "values": fields.Bool(default=False, label="Values within a range or a list", help=(
            "Ticked: each column of Allowed values has only those values: a range min..max (an end may be left "
            "empty: 0.., ..100) or a list a, b, c. Missing values are not counted (No missing values counts them). A "
            "range on a text column reads its values as numbers or dates (YYYY-MM-DD), as its ends are, and a value "
            "that does not read is not within it.")),
        "allowed_values": fields.KeyValues(required=False, label="Allowed values", help=(
            "A row per column: its name and its range (min..max, min.., ..max) or its list of values (a, b, c)."),
            examples=["amount=0..1000", "region=north, south, east", "day=2026-01-01.."], **_on("values")),
        "values_fails": _if_fails("values"),
        "freshness": fields.Bool(default=False, label="Freshness", help=(
            "Ticked: the latest value of Date column is at least Latest at least: a date or datetime column, or a "
            "text column of YYYY-MM-DD dates, read as dates.")),
        "fresh_column": fields.Text(required=False, label="Date column", help=(
            "The column whose latest value is checked: a date or datetime column, or a text column of YYYY-MM-DD "
            "dates."), examples=["day", "updated_at", "load_date"], **_on("freshness")),
        "fresh_since": fields.Code("expression", default="today() - days(1)", label="Latest at least", help=(
            "The date or time the latest value must be at least: an expression, as an If's condition, with the flow "
            "parameters and today(), now(), days(), bdays() on the flow's clock (its time zone). A date is its "
            "midnight. Empty: today() - days(1)."),
            examples=["today() - days(1)", "today() - bdays(1)", "now() - days(2)"], **_on("freshness")),
        "freshness_fails": _if_fails("freshness"),
        "conditions": fields.Bool(default=False, label="Conditions every row must meet", help=(
            "Ticked: every row meets each of Conditions, SQL conditions on the table's columns (as DuckDB SQL "
            "writes them), each checked on its own. A row where one is not true breaks it: false, or unknown because "
            "of a missing value (amount >= 0 is unknown when amount is missing; write amount is null or amount >= 0 "
            "to allow missing ones). A condition that is not valid SQL fails the block, naming it.")),
        "rules": fields.List(required=False, lines=True, label="Conditions", help=(
            "One SQL condition per line, each checked on its own, e.g. amount >= 0 or end_date >= start_date. A "
            "missing value breaks a condition: write amount is null or amount >= 0 to allow missing ones."),
            examples=["amount >= 0", "end_date >= start_date", "amount is null or amount >= 0"],
            **_on("conditions")),
        "conditions_fails": _if_fails("conditions"),
    }
    # A new block checks that the table is not empty.
    new_config = {"row_count": True, "at_least": 1}
    CHECKS = ("row_count", "compared", "missing", "unique", "types", "values", "freshness", "conditions")

    @classmethod
    def _name(cls, check: str) -> str:
        return setting_label(check, cls.config[check])

    @classmethod
    def problems(cls, config, sources, params):
        if not any(config[check] for check in cls.CHECKS):
            return ["tick at least one check: " + ", ".join(cls._name(check) for check in cls.CHECKS)]
        found = []
        if config["row_count"] and config["at_least"] is None and config["at_most"] is None:
            found.append("Row count: write At least, At most or both (or untick Row count)")
        if config["missing"] and not has_placeholders(config["allowed"]):
            try:
                _allowed(config["allowed"])
            except ValueError as e:
                found.append(f"No missing values: Allowed: {e}")
        if config["unique"] and not config["unique_columns"]:
            found.append("Unique: list the column, or the columns together, whose values must not repeat")
        if config["types"]:
            rows = [r for r in config["types_columns"] or [] if r.get("on") is not False]
            if not rows:
                found.append("Columns and types: add a row Column: type for each column the table must have")
            found += [f"Columns and types: {r['key']}: {r['value']!r} is not a type: write text, int, float, bool, "
                      "date or datetime, or leave it empty" for r in rows
                      if r["value"].strip().lower() not in ("", *TYPES) and not has_placeholders(r["value"])]
        if config["values"]:
            rows = [r for r in config["allowed_values"] or [] if r.get("on") is not False]
            if not rows:
                found.append("Values within a range or a list: add a row Column=min..max or Column=a, b, c")
            found += [f"Values within a range or a list: {r['key']}: write its range (min..max) or its list "
                      "(a, b, c)" for r in rows if not r["value"].strip()]
        if config["freshness"] and not (config["fresh_column"] or "").strip():
            found.append("Freshness: write the Date column whose latest value it checks")
        if config["conditions"] and not config["rules"]:
            found.append("Conditions every row must meet: write at least one condition, e.g. amount >= 0")
        return found

    @classmethod
    def loop_problems(cls, config, loop):
        if config["compared"]:
            return [f"{cls._name('compared')}: not inside a Loop: {loop} runs this block once per item, and its "
                    "items have no single earlier row count to compare with (untick it, or check the table outside "
                    "the Loop)"]
        return []

    def run(self, ctx, table):
        self.ctx, self.table, self.schema = ctx, table, table.schema
        if ROW in self.schema:
            raise Failed(f"the table has a column named {ROW}, the name DuckDB gives a row's number in the table's "
                         "file, by which Check Table numbers the rows: rename it first (e.g. in a DuckDB SQL block)")
        self.source = f"read_parquet({_literal(str(table.path))})"
        rows, failures = [], []
        with closing(duckdb.connect()) as self.con:
            # Spill to the run's workspace instead of failing when a check needs more than memory (e.g. Unique).
            self.con.execute(f"SET temp_directory = {_literal(str(ctx.new_path('duckdb_tmp')))}")
            for check in self.CHECKS:
                if not getattr(self.config, check):
                    continue
                name = self._name(check)
                try:
                    passed, broken, details = getattr(self, f"_{check}")()
                    outcome = "passed" if passed else "warned" if getattr(self.config, f"{check}_fails") == WARN \
                        else "failed"
                except _Unchecked as e:  # (also set to warn: nothing was checked)
                    outcome, broken, details = "failed", None, str(e)
                if outcome == "passed":
                    ctx.log(f"{name}: passed: {details}")
                elif outcome == "warned":
                    ctx.warn(f"{name}: warned: {details}")
                else:
                    ctx.log(f"{name}: failed: {details}", "ERROR")
                    failures.append(f"{name}: {details}")
                rows.append((name, outcome, broken, details))
        results = Table.from_frame(pl.DataFrame(rows, schema={"check": pl.String, "outcome": pl.String,
                                                              "rows": pl.Int64, "details": pl.String}, orient="row"),
                                   ctx.new_path("results.parquet"))
        if failures:
            raise Failed("\n".join(failures), results=results)
        return {"table": table, "results": results}

    # --- working out on the table ---------------------------------------------------------------------------

    def _one(self, sql: str):
        return self.con.execute(sql).fetchone()

    def _count(self, where: list[str]) -> int:
        """How many rows break at least one of `where` (SQL conditions, true where a row breaks it)."""
        return self._one(f"SELECT count(*) FROM {self.source} WHERE {' OR '.join(f'({w})' for w in where)}")[0]

    def _examples(self, where: str, columns) -> str:
        """Up to EXAMPLES rows for which `where` is true, in the table's order: each its row number (from 1) and its
        values of `columns`."""
        picked = "".join(f", {_q(c)}" for c in columns)
        found = self.con.execute(f"SELECT {ROW} + 1{picked} FROM {self.source} WHERE {where} ORDER BY {ROW} "
                                 f"LIMIT {EXAMPLES}").fetchall()
        return "; ".join(f"row {r[0]:,}" + (f" ({', '.join(f'{c} {_shown(v)}' for c, v in zip(columns, r[1:]))})"
                                            if columns else "") for r in found)

    def _there(self, columns, check: str):
        missing = [c for c in columns if c not in self.schema]
        if missing:
            raise _Unchecked(f"{', '.join(missing)}: not a column of the table (its columns: "
                             f"{', '.join(self.schema)}); {self._name(check)} could not be checked")

    # --- the checks: each (passed, how many rows break it or None, details) ----------------------------------

    def _row_count(self):
        n, c = self.table.num_rows, self.config
        if c.at_least is not None and n < c.at_least:
            return False, None, f"{_rows(n)}; it must have at least {c.at_least:,}"
        if c.at_most is not None and n > c.at_most:
            return False, None, f"{_rows(n)}; it may have at most {c.at_most:,}"
        limits = " and ".join(f"{word} {limit:,}" for word, limit in (("at least", c.at_least),
                                                                      ("at most", c.at_most)) if limit is not None)
        return True, None, f"{_rows(n)} ({limits})"

    def _compared(self):
        n, ctx, within = self.table.num_rows, self.ctx, self.config.within
        with closing(History(ctx.home.history_db)) as history:
            earlier = history.last_success(ctx.flow_path, ctx.block_id, ctx.run_id)
        if earlier is None:
            return True, None, (f"{_rows(n)}; no earlier run of this flow in which {ctx.block_id} succeeded: nothing "
                                "to compare with yet")
        found = PASSED_ON.search(earlier["summary"] or "")
        if not found:
            raise _Unchecked(f"run #{earlier['run_id']} kept no row count for {ctx.block_id}: {earlier['summary']!r}")
        before, run = int(found[1].replace(",", "")), f"run #{earlier['run_id']}"
        if n == before:
            return True, None, f"{_rows(n)}, as many as in {run} (within {within:g}%)"
        apart = abs(n - before) / before * 100 if before else float("inf")
        return apart <= within, None, (f"{_rows(n)}, {round(apart, 1):g}% {'more' if n > before else 'fewer'} than "
                                       f"the {_rows(before)} of {run} (within {within:g}%)")

    def _missing(self):
        c = self.config
        columns = c.missing_columns or list(self.schema)
        self._there(columns, "missing")
        try:
            limit, percent = _allowed(c.allowed)
        except ValueError as e:  # (written with a {placeholder}, filled in as the block runs)
            raise _Unchecked(f"Allowed: {e}") from None
        n = self.table.num_rows
        most = limit * n / 100 if percent else limit
        allowed = f"at most {limit:g}% of {_rows(n)} allowed" if percent else f"at most {limit:g} allowed"

        def missing(column):
            empty = c.empty_text and _kind(self.schema[column]) == "text"
            return f"{_q(column)} IS NULL" + (f" OR trim({_q(column)}) = ''" if empty else "")

        counts = self._one("SELECT " + ", ".join(f"count(*) FILTER (WHERE {missing(col)})" for col in columns)
                           + f" FROM {self.source}")
        over = [(col, k) for col, k in zip(columns, counts) if k > most]
        if not over:
            some = ", ".join(f"{col} {k:,}" for col, k in zip(columns, counts) if k)
            return True, 0, f"no column has more missing values than allowed ({allowed})" + (
                f"; missing: {some}" if some else "")
        details = "; ".join(f"{col}: {k:,} missing ({allowed}): {self._examples(missing(col), [col])}"
                            for col, k in over)
        return False, self._count([missing(col) for col, _ in over]), details

    def _unique(self):
        columns = self.config.unique_columns
        self._there(columns, "unique")
        key = ", ".join(_q(col) for col in columns)
        groups = (f"SELECT {key}, count(*) AS __n, min({ROW} + 1, 3) AS __at FROM {self.source} WHERE "
                  + " AND ".join(f"{_q(col)} IS NOT NULL" for col in columns) + f" GROUP BY {key} HAVING count(*) > 1")
        found = self.con.execute(f"SELECT *, count(*) OVER (), sum(__n) OVER () FROM ({groups}) ORDER BY __at[1] "
                                 f"LIMIT {EXAMPLES}").fetchall()
        what = " + ".join(columns)
        if not found:
            return True, 0, f"no value of {what} repeats"
        values, rows = found[0][-2], int(found[0][-1])
        shown = "; ".join(", ".join(f"{col} {_shown(v)}" for col, v in zip(columns, r)) + f" in {_rows(r[-4])}: rows "
                          + ", ".join(f"{at:,}" for at in r[-3]) + (", ..." if r[-4] > 3 else "") for r in found)
        repeat = f"1 value of {what} repeats" if values == 1 else f"{values:,} values of {what} repeat"
        return False, rows, f"{repeat} ({_rows(rows)}): {shown}"

    def _types(self):
        wrong, right, unread = [], [], []
        for row in self.config.types_columns or []:
            if row.get("on") is False:
                continue
            column, wanted = row["key"].strip(), row["value"].strip().lower()
            if wanted not in ("", *TYPES):  # (written with a {placeholder}, filled in as the block runs)
                raise _Unchecked(f"{column}: {wanted!r} is not a type: write text, int, float, bool, date or datetime")
            if column not in self.schema:
                wrong.append(f"{column} is not in the table")
                continue
            has = _kind(self.schema[column])
            if wanted in ("", has) or wanted == "float" and has == "int":
                right.append(f"{column} is {has}" if wanted else f"{column} is there")
            elif has != "text" or wanted == "text":
                wrong.append(f"{column} is {has}, not {wanted}")
            else:  # text that is to read as a number, a date or yes/no
                where = f"{_q(column)} IS NOT NULL AND NOT {_reads(wanted, _q(column))}"
                found = self.con.execute(
                    f"SELECT v, n, first, count(*) OVER (), sum(n) OVER () FROM (SELECT {_q(column)} AS v, "
                    f"count(*) AS n, min({ROW}) + 1 AS first FROM {self.source} WHERE {where} GROUP BY v) "
                    f"ORDER BY n DESC, first LIMIT {EXAMPLES}").fetchall()
                if not found:
                    right.append(f"{column} is text, and every value reads as {wanted}")
                    continue
                values, n = found[0][3], int(found[0][4])
                shown = ", ".join(f"{_shown(v)} ({_rows(k)}, the first row {first:,})" for v, k, first, *_ in found)
                more = f", and {values - len(found):,} more" if values > len(found) else ""
                wrong.append(f"{column} is text, and {_rows(n)} have a value that does not read as {wanted}: "
                             f"{shown}{more}")
                unread.append(where)
        return not wrong, self._count(unread) if unread else 0, "; ".join(wrong or right)

    def _values(self):
        wrong, right, broken = [], [], []
        for row in self.config.allowed_values or []:
            if row.get("on") is False:
                continue
            column, allowed = row["key"].strip(), row["value"].strip()
            self._there([column], "values")
            within, what = self._within(column, allowed)
            where = f"{_q(column)} IS NOT NULL AND ({within}) IS NOT TRUE"  # (a missing value is not counted)
            n = self._count([where])
            if n:
                wrong.append(f"{column}: {_rows(n)} not {what}: {self._examples(where, [column])}")
                broken.append(where)
            else:
                right.append(f"{column}: every value {what}")
        return not wrong, self._count(broken) if broken else 0, "; ".join(wrong or right)

    def _within(self, column: str, allowed: str) -> tuple[str, str]:
        """SQL true where the value of `column` is within `allowed` (min..max, min.., ..max, or a, b, c), and how to
        say what it must be."""
        q, has = _q(column), _kind(self.schema[column])
        if ".." not in allowed:
            items = [item.strip() for item in allowed.split(",") if item.strip()]
            literals = [_literal(item) if has == "text" else self._bound(column, has, item) for item in items]
            return f"{q} IN ({', '.join(literals)})", f"one of {', '.join(items)}"
        ends = [end.strip() for end in allowed.split("..", 1)]
        given = [end for end in ends if end]
        if not given:
            raise _Unchecked(f"{column}: {allowed!r} has no ends: write min..max, min.. or ..max")
        if has in ("int", "float") or has == "text" and all(re.fullmatch(NUMBER, end) for end in given):
            kind = "float"
        elif has in ("date", "datetime") or has == "text" and all(re.fullmatch(DATE, end) for end in given):
            kind = "date" if has == "text" else has
        else:
            raise _Unchecked(f"{column} is {has}: a range is of numbers, or of dates (YYYY-MM-DD) or times, not "
                             f"{allowed!r}; for other values write a list a, b, c")
        value = q if has != "text" else (f"(CASE WHEN {_reads(kind, q)} THEN TRY_CAST(trim({q}) AS "
                                         f"{'DOUBLE' if kind == 'float' else 'DATE'}) END)")  # (else not within)
        low, high = (self._bound(column, kind, end) if end else None for end in ends)
        sql = [f"{value} >= {low}"] * bool(low) + [f"{value} <= {high}"] * bool(high)
        return " AND ".join(sql), f"within {allowed}"

    @staticmethod
    def _bound(column: str, kind: str, text: str) -> str:
        """An end of a range, or an item of a list, of a column of type `kind`, as an SQL literal."""
        try:
            if kind in ("int", "float") and re.fullmatch(NUMBER, text):
                return text
            if kind == "date" and re.fullmatch(DATE, text):
                return f"DATE '{dt.date.fromisoformat(text)}'"
            if kind == "datetime" and re.fullmatch(f"{DATE}([ T]{TIME})?", text):
                return f"TIMESTAMP '{dt.datetime.fromisoformat(text)}'"
            if kind == "bool" and text.lower() in ("true", "false", "yes", "no", "1", "0"):
                return "TRUE" if text.lower() in ("true", "yes", "1") else "FALSE"
        except ValueError:  # (e.g. 2026-13-01)
            pass
        if kind not in ("int", "float", "date", "datetime", "bool"):
            raise _Unchecked(f"{column} is {kind}: values are checked in text, number, bool, date and datetime "
                             "columns")
        raise _Unchecked(f"{column} is {kind}: {text!r} is not a {'number' if kind in ('int', 'float') else kind}")

    def _freshness(self):
        c, zone = self.config, self.ctx.as_of.tzinfo  # (the flow's time zone; None: this computer's)
        column = c.fresh_column.strip()
        self._there([column], "freshness")
        q, has = _q(column), _kind(self.schema[column])
        if has == "text":
            where = f"{q} IS NOT NULL AND NOT {_reads('date', q)}"
            n = self._count([where])
            if n:
                return False, None, (f"{column} is text, and {_rows(n)} do not read as a date (YYYY-MM-DD): "
                                     f"{self._examples(where, [column])}")
            value = f"TRY_CAST(trim({q}) AS DATE)"
        elif has in ("date", "datetime"):
            value = q
        else:
            raise _Unchecked(f"{column} is {has}: Freshness checks a date or datetime column, or a text column of "
                             "YYYY-MM-DD dates")
        expected = self._expected()
        latest = self._one(f"SELECT max({value}) FROM {self.source}")[0]
        if latest is None:
            return False, None, f"{column} has no value at all, to be at least {expected}"
        found, wanted = _instants(latest, expected, zone=zone)
        fresh = found >= wanted
        return fresh, None, (f"the latest {column} is {latest}, {'at or after' if fresh else 'before'} {expected} "
                             f"({c.fresh_since.strip()})")

    def _expected(self):
        """What Latest at least gives: a date or a time."""
        text = self.config.fresh_since
        try:
            value = evaluate(text, {**self.ctx.functions, **self.ctx.params})
        except ExpressionError as e:
            raise _Unchecked(f"Latest at least: {e}") from None
        except Exception as e:
            raise _Unchecked(f"Latest at least: {text!r} cannot be worked out: {type(e).__name__}: {e}") from None
        if not isinstance(value, dt.date):
            raise _Unchecked(f"Latest at least: {text!r} gives {value!r}, which is not a date or a time")
        return value

    def _conditions(self):
        wrong, right, broken, invalid = [], [], [], False
        for rule in self.config.rules:
            where = f"({rule}\n) IS NOT TRUE"  # (false, or unknown: a missing value)
            try:
                n = self._count([where])
            except duckdb.Error as e:
                wrong.append(f"{rule!r} is not a condition DuckDB can check on this table: {_duckdb_message(e)}")
                invalid = True
                continue
            if n:
                named = [col for col in self.schema if re.search(rf"(?<![\w.]){re.escape(col)}(?!\w)", rule, re.I)]
                wrong.append(f"{rule}: {_rows(n)} break it: {self._examples(where, named)}")
                broken.append(where)
            else:
                right.append(f"{rule}: every row meets it")
        if invalid:  # (fails the block also when set to warn: that condition checked no row)
            raise _Unchecked("; ".join(wrong))
        return not wrong, self._count(broken) if broken else 0, "; ".join(wrong or right)
