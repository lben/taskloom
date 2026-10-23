"""Data blocks: DuckDB SQL, Polars transforms, reading and writing files."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl
import polars.selectors as cs

from ..block import Block, fields, ports
from ..params import to_qmark
from ..table import Table

EXCEL_MAX_ROWS = 1_048_575  # one row is the header
_FORMATS = ["auto", "csv", "parquet", "excel"]
_EXTENSIONS = {".csv": "csv", ".txt": "csv", ".parquet": "parquet", ".xlsx": "excel", ".xlsm": "excel"}


def _format_of(path: str, fmt: str) -> str:
    if fmt != "auto":
        return fmt
    try:
        return _EXTENSIONS[Path(path).suffix.lower()]
    except KeyError:
        raise ValueError(f"cannot tell the file format of '{path}'; set format to csv, parquet or excel") from None


def _sql_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _exact_whole_numbers(result):
    """Parquet has no 128-bit integers, so DuckDB would store them (what sum() of whole numbers gives) as
    rounded floats. As 64-bit integers they stay exact, like the numbers summed; one too big fails clearly."""
    wide = ("HUGEINT", "UHUGEINT")
    if not any(str(t) in wide for t in result.types):
        return result
    columns = []
    for i, (name, t) in enumerate(zip(result.columns, result.types), 1):
        quoted = '"' + name.replace('"', '""') + '"'
        columns.append(f"CAST(#{i} AS BIGINT) AS {quoted}" if str(t) in wide else f"#{i}")
    return result.project(", ".join(columns))


class DuckDBSQL(Block):
    type_id = "data.duckdb_sql"
    title = "DuckDB SQL"
    description = 'Lets you write SQL over tables from earlier blocks (filter, join, group, sum...). Works even on data bigger than your memory.'
    category = "Data"
    config = {
        "inputs": fields.Names(default=["input"], help="Each input table is available in SQL under this name."),
        "sql": fields.Code("sql", help="Use :name for parameters, e.g. where day = :day"),
    }
    outputs = {"result": ports.Table()}

    @classmethod
    def ports(cls, config):
        return {n: ports.Table() for n in config["inputs"]}, cls.outputs

    def run(self, ctx, **inputs):
        out = ctx.new_path("result.parquet")
        sql, names = to_qmark(self.config.sql.strip().rstrip(";"), ctx.params)
        args = [a for n in names for a in (ctx.params[n] if isinstance(ctx.params[n], list) else [ctx.params[n]])]
        con = duckdb.connect()
        try:
            # Spill to the run's workspace instead of failing when data exceeds memory.
            con.execute(f"SET temp_directory = {_sql_literal(str(ctx.new_path('duckdb_tmp')))}")
            for name, table in inputs.items():
                con.execute(f'CREATE VIEW "{name}" AS SELECT * FROM read_parquet({_sql_literal(str(table.path))})')
            result = con.sql(sql, params=args or None)
            if result is None:
                raise ValueError("the SQL must end with a query that gives a table, e.g. select * from ...")
            _exact_whole_numbers(result).write_parquet(str(out))
        finally:
            con.close()
        return {"result": Table(out)}


class PolarsTransform(Block):
    type_id = "data.polars"
    title = "Polars Transform"
    description = 'Changes a table with one Polars expression, for example filtering rows or adding a column.'
    category = "Data"
    inputs = {"table": ports.Table()}
    outputs = {"table": ports.Table()}
    config = {"code": fields.Code("python", help='An expression using df (a LazyFrame), pl and params, e.g. df.filter(pl.col("amount") > 0)')}

    def run(self, ctx, table):
        result = eval(compile(f"({self.config.code}\n)", f"<block {ctx.block_id}>", "eval"),
                      {"df": table.scan(), "pl": pl, "params": ctx.params})
        if not isinstance(result, (pl.LazyFrame, pl.DataFrame)):
            raise TypeError(f"the expression must return a Polars frame, got {type(result).__name__}")
        return {"table": result}


class ReadFile(Block):
    type_id = "data.read_file"
    title = "Read File"
    description = 'Reads a CSV, Excel or Parquet file into a table that other blocks can use.'
    category = "Data"
    outputs = {"table": ports.Table()}
    config = {
        "path": fields.Path(),
        "format": fields.Choice(_FORMATS, default="auto"),
        "separator": fields.Text(default=",", help="CSV only"),
        "sheet": fields.Text(required=False, help="Excel only; default is the first sheet"),
    }

    def run(self, ctx):
        path, fmt = self.config.path, _format_of(self.config.path, self.config.format)
        if not Path(path).exists():
            raise FileNotFoundError(f"file not found: {path}")
        out = ctx.new_path("table.parquet")
        # The path is never a pattern: "sales [EU].csv" is that file.
        if fmt == "csv":
            try:
                pl.scan_csv(path, separator=self.config.separator, infer_schema_length=10_000, glob=False).sink_parquet(out)
            except pl.exceptions.ComputeError:
                # A value after the first rows does not fit the column types guessed from them: guess from
                # every row. Only then, as that holds the whole file in memory.
                pl.scan_csv(path, separator=self.config.separator, infer_schema_length=None, glob=False).sink_parquet(out)
        elif fmt == "parquet":
            pl.scan_parquet(path, glob=False).sink_parquet(out)
        else:
            # Types from every row: otherwise a cell that does not fit the first rows' type becomes empty.
            pl.read_excel(path, sheet_name=self.config.sheet or None, infer_schema_length=None).write_parquet(out)
        ctx.log(f"read {path}")
        return {"table": Table(out)}


class WriteFile(Block):
    type_id = "data.write_file"
    title = "Write File"
    description = 'Saves a table to a CSV, Excel or Parquet file.'
    category = "Data"
    inputs = {"table": ports.Table()}
    outputs = {"file": ports.Any()}
    config = {
        "path": fields.Path(help="e.g. reports/sales_{day:%Y-%m-%d}.csv",
                            files=["CSV file (*.csv)", "Excel workbook (*.xlsx)", "Parquet file (*.parquet)"]),
        "format": fields.Choice(_FORMATS, default="auto"),
    }

    def run(self, ctx, table):
        path, fmt = Path(self.config.path), _format_of(self.config.path, self.config.format)
        if fmt == "excel" and path.suffix.lower() == ".xlsm":
            raise ValueError(f"cannot write a macro-enabled workbook (.xlsm): {path}; save it as .xlsx")
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            table.scan().sink_csv(path)
        elif fmt == "parquet":
            table.scan().sink_parquet(path)
        else:
            if table.num_rows > EXCEL_MAX_ROWS:
                raise ValueError(f"{table.num_rows:,} rows do not fit in an Excel sheet (max {EXCEL_MAX_ROWS:,})")
            # Excel has no time zones: a time with one is written as its clock time in that zone.
            frame = table.scan().collect().with_columns(cs.datetime(time_zone="*").dt.replace_time_zone(None))
            frame.write_excel(path)
        ctx.log(f"wrote {table.num_rows:,} rows to {path}")
        return {"file": str(path)}
