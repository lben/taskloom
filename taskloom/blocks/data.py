"""Data blocks: DuckDB SQL, Polars transforms, reading and writing files."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

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


class DuckDBSQL(Block):
    type_id = "data.duckdb_sql"
    title = "DuckDB SQL"
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
        sql, names = to_qmark(self.config.sql.strip().rstrip(";"))
        args = [ctx.params[n] for n in names]
        con = duckdb.connect()
        try:
            # Spill to the run's workspace instead of failing when data exceeds memory.
            con.execute(f"SET temp_directory = {_sql_literal(str(ctx.new_path('duckdb_tmp')))}")
            for name, table in inputs.items():
                con.execute(f'CREATE VIEW "{name}" AS SELECT * FROM read_parquet({_sql_literal(str(table.path))})')
            con.sql(sql, params=args or None).write_parquet(str(out))
        finally:
            con.close()
        return {"result": Table(out)}


class PolarsTransform(Block):
    type_id = "data.polars"
    title = "Polars Transform"
    category = "Data"
    inputs = {"table": ports.Table()}
    outputs = {"table": ports.Table()}
    config = {"code": fields.Code("python", help='An expression using df (a LazyFrame), pl and params, e.g. df.filter(pl.col("amount") > 0)')}

    def run(self, ctx, table):
        result = eval(compile(f"(\n{self.config.code}\n)", f"<block {ctx.block_id}>", "eval"),
                      {"df": table.scan(), "pl": pl, "params": ctx.params})
        if not isinstance(result, (pl.LazyFrame, pl.DataFrame)):
            raise TypeError(f"the expression must return a Polars frame, got {type(result).__name__}")
        return {"table": result}


class ReadFile(Block):
    type_id = "data.read_file"
    title = "Read File"
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
        if fmt == "csv":
            pl.scan_csv(path, separator=self.config.separator, infer_schema_length=10_000).sink_parquet(out)
        elif fmt == "parquet":
            pl.scan_parquet(path).sink_parquet(out)
        else:
            pl.read_excel(path, sheet_name=self.config.sheet or None).write_parquet(out)
        ctx.log(f"read {path}")
        return {"table": Table(out)}


class WriteFile(Block):
    type_id = "data.write_file"
    title = "Write File"
    category = "Data"
    inputs = {"table": ports.Table()}
    outputs = {"file": ports.Any()}
    config = {
        "path": fields.Path(help="e.g. reports/sales_{day:%Y-%m-%d}.csv"),
        "format": fields.Choice(_FORMATS, default="auto"),
    }

    def run(self, ctx, table):
        path, fmt = Path(self.config.path), _format_of(self.config.path, self.config.format)
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            table.scan().sink_csv(path)
        elif fmt == "parquet":
            table.scan().sink_parquet(path)
        else:
            if table.num_rows > EXCEL_MAX_ROWS:
                raise ValueError(f"{table.num_rows:,} rows do not fit in an Excel sheet (max {EXCEL_MAX_ROWS:,})")
            table.scan().collect().write_excel(path)
        ctx.log(f"wrote {table.num_rows:,} rows to {path}")
        return {"file": str(path)}
