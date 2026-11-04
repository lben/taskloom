"""Checks blocks: check that what a flow is about to use is as expected. Each check is ticked to use it and either
fails the block (the default) or only warns in the Log; a `results` table has a row per check, and the error output
carries it too."""

from __future__ import annotations

import datetime as dt
import re
import stat
from contextlib import ExitStack

import polars as pl

from ..block import Block, fields, has_placeholders, ports
from ..engine import Failed
from ..expr import ExpressionError, evaluate
from ..table import Table
from .files import WHERE, _open_location, _Path, _shown

FAIL, WARN = "fail the block", "warn"
IF_FAILS = (f"What a failure of this check does: {FAIL} (the default; with the block's Retry set, it is checked again "
            f"later) or {WARN} (a warning in the Log, and the flow goes on; a check that only warns never retries).")
UNITS = {"B": 1, "KB": 1 << 10, "MB": 1 << 20, "GB": 1 << 30}  # 1 KB = 1024 bytes, as Windows shows sizes
_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*([KMG]?B)", re.I)


def _if_fails(tick: str) -> fields.Choice:
    return fields.Choice([FAIL, WARN], default=FAIL, label="If it fails", only_if={tick: [True]}, help=IF_FAILS)


def _bytes(text: str) -> float:
    """The number of bytes a size written with its unit is."""
    found = _SIZE.fullmatch(text.strip())
    if not found:
        raise ValueError(f"{text!r} is not a size: write a number and a unit, B, KB, MB or GB (1 KB = 1024 bytes), "
                         "e.g. 1 KB or 200 MB")
    return float(found[1]) * UNITS[found[2].upper()]


class _Size(fields.Text):
    """A size with its unit (1 KB, 200 MB): one that is not is refused before the run (with {placeholders}: once
    they are filled in, as the block runs)."""

    def coerce(self, value):
        value = str(value)
        if not has_placeholders(value):
            _bytes(value)
        return value


def _size(n: int) -> str:
    """A size as Windows shows it, with its exact number of bytes: 7 bytes, 2 KB (2,048 bytes)."""
    if n < 1024:
        return f"{n:,} byte{'' if n == 1 else 's'}"
    value, unit = next((n / UNITS[u], u) for u in ("GB", "MB", "KB") if n >= UNITS[u])
    return f"{value:,.1f}".removesuffix(".0") + f" {unit} ({n:,} bytes)"


def _when(timestamp: float, zone) -> str:
    """A time on the flow's clock (its time zone; without one, this computer's)."""
    return dt.datetime.fromtimestamp(timestamp, zone).strftime("%Y-%m-%d %H:%M:%S")


class CheckFile(Block):
    type_id = "checks.check_file"
    title = "Check File"
    description = ("Checks that a file, or the files matching a pattern, are as expected before the flow uses them: "
                   "there, not empty, changed since a time, within a size. Each check fails the block (the default) or "
                   "only warns in the Log. With the block's Retry set, a failed check is checked again later, so it can "
                   "wait for a file that arrives late; a check that only warns never retries.")
    category = "Checks"
    outputs = {"files": ports.Any(), "results": ports.Table()}
    config = {
        "server": fields.Server(label="Path on", help=f"Where the path is: {WHERE}"),
        "path": _Path("Path on", help=(
            "The file to check (C:/inbox/sales.csv, ~/exports/orders.parquet), or a pattern in the last part "
            "(C:/inbox/sales_*.csv): every file it matches is checked. A folder is not a file: it does not count as "
            "one found."), examples=["C:/inbox/sales_*.csv", "~/exports/orders-*.parquet", "C:/inbox/sales.csv"]),
        "if_none": fields.Choice([FAIL, WARN], default=FAIL, label="If none is there", help=(
            f"It is there, always checked: at least one file is found. When none is: {FAIL} (the default; with the "
            f"block's Retry set, it looks again later, so it can wait for a file that arrives late) or {WARN} (a "
            "warning in the Log, and the flow goes on; a warning never retries).")),
        "not_empty": fields.Bool(default=True, help="Ticked: every file found has more than 0 bytes."),
        "not_empty_fails": _if_fails("not_empty"),
        "modified": fields.Bool(default=False, label="Modified since", help=(
            "Ticked: every file found was last changed at or after the time in Since.")),
        "since": fields.Code("expression", default="today()", only_if={"modified": [True]}, help=(
            "The time each file must have been last changed at or after: an expression, as an If's condition, with "
            "the flow parameters and today(), now(), days(), bdays() on the flow's clock (its time zone). A date is "
            "its midnight. Empty: today()."), examples=["today()", "now() - days(1)", "today() - bdays(1)"]),
        "modified_fails": _if_fails("modified"),
        "size": fields.Bool(default=False, help=(
            "Ticked: every file found is at least Minimum and at most Maximum (write one of them, or both).")),
        "minimum": _Size(required=False, only_if={"size": [True]}, help=(
            "The smallest size a file may have, with its unit: B, KB, MB or GB (1 KB = 1024 bytes, as Windows shows "
            "sizes). Empty: no minimum."), examples=["1 KB", "200 MB", "10 B"]),
        "maximum": _Size(required=False, only_if={"size": [True]}, help=(
            "The largest size a file may have, with its unit: B, KB, MB or GB (1 KB = 1024 bytes). Empty: no "
            "maximum."), examples=["200 MB", "2 GB", "500 KB"]),
        "size_fails": _if_fails("size"),
    }

    @classmethod
    def problems(cls, config, sources, params):
        if config["size"] and config["minimum"] is None and config["maximum"] is None:
            return ["Size: write a Minimum, a Maximum or both (or untick Size)"]
        return []

    def run(self, ctx):
        c = self.config
        where = _shown(c.path, c.server)
        zone = ctx.as_of.tzinfo  # the flow's (None: this computer's)
        with ExitStack() as stack:
            fs, _ = _open_location(ctx, c.server, stack)
            found, folders = [], []
            for match in fs.expand(c.path):
                st = fs.stat(match)
                if st is None:
                    continue  # (a link to nothing)
                if stat.S_ISDIR(st.st_mode):
                    folders.append(match)
                else:
                    found.append((match, st.st_size, st.st_mtime))
        skipped = f" ({len(folders)} folder(s) there, which do not count: {', '.join(folders)})" if folders else ""
        ctx.log(f"found {len(found)} file(s) at {where}{skipped}")
        for path, size, mtime in found:
            ctx.log(f"{_shown(path, c.server)} · {_size(size)} · last changed {_when(mtime, zone)}")
        rows, failures = [], []

        def outcome(check: str, broken: list[str], passed: str, if_fails: str):
            if not broken:
                result, details = "passed", passed
                ctx.log(f"{check}: passed: {details}")
            else:
                result, details = ("warned" if if_fails == WARN else "failed"), ", ".join(broken)
                if result == "warned":
                    ctx.warn(f"{check}: warned: {details}")
                else:
                    ctx.log(f"{check}: failed: {details}", "ERROR")
                    failures.append(f"{check}: {details}")
            rows.append((check, result, details))

        outcome("It is there", [] if found else [f"no file at {where}{skipped}"], f"{len(found)} file(s) found",
                c.if_none)
        checked = "every file found" if found else "no file found to check"
        if c.not_empty:
            outcome("Not empty", [f"{path} is empty (0 bytes)" for path, size, _ in found if size == 0],
                    f"{checked} has more than 0 bytes" if found else checked, c.not_empty_fails)
        if c.modified:
            limit = self._since(ctx, zone)
            shown = f"{_when(limit, zone)} ({c.since.strip()})"
            older = [f"{path} at {_when(mtime, zone)}" for path, _, mtime in found if mtime < limit]
            outcome("Modified since", older and [f"last changed before {shown}: {', '.join(older)}"],
                    f"{checked} was last changed at or after {shown}" if found else checked, c.modified_fails)
        if c.size:
            least = None if c.minimum is None else self._limit("Minimum", c.minimum)
            most = None if c.maximum is None else self._limit("Maximum", c.maximum)
            broken = []
            for path, size, _ in found:
                if least is not None and size < least:
                    broken.append(f"{path} is {size:,} bytes, less than the minimum {c.minimum}")
                elif most is not None and size > most:
                    broken.append(f"{path} is {size:,} bytes, more than the maximum {c.maximum}")
            limits = " and ".join(f"{word} {text}" for word, text in (("at least", c.minimum), ("at most", c.maximum))
                                  if text is not None)
            outcome("Size", broken, f"{checked} is {limits}" if found else checked, c.size_fails)
        results = Table.from_frame(pl.DataFrame(rows, schema=["check", "outcome", "details"], orient="row"),
                                   ctx.new_path("results.parquet"))
        if failures:
            raise Failed("; ".join(failures), results=results)
        return {"files": [path for path, _, _ in found], "results": results}

    def _since(self, ctx, zone) -> float:
        """The time (a timestamp) Since's expression gives, a date being its midnight, on the flow's clock."""
        text = self.config.since
        try:
            since = evaluate(text, {**ctx.functions, **ctx.params})
        except ExpressionError as e:
            raise Failed(f"Modified since: {e}") from None
        except Exception as e:
            raise Failed(f"Modified since: {text!r} cannot be worked out: {type(e).__name__}: {e}") from None
        if not isinstance(since, dt.date):
            raise Failed(f"Modified since: {text!r} gives {since!r}, which is not a date or a time")
        if not isinstance(since, dt.datetime):
            since = dt.datetime.combine(since, dt.time())
        return (since if since.tzinfo else since.replace(tzinfo=zone)).timestamp()

    @staticmethod
    def _limit(label: str, text: str) -> float:
        try:
            return _bytes(text)
        except ValueError as e:  # (written with {placeholders}, filled in as the block runs)
            raise Failed(f"{label}: {e}") from None
