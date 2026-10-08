"""Database blocks."""

from __future__ import annotations

from .. import jdbc
from ..block import Block, fields, ports
from ..params import to_qmark
from ..table import Table


class RunQuery(Block):
    type_id = "db.run_query"
    title = "Run Query"
    category = "Database"
    inputs = {"params": ports.Any(required=False)}
    outputs = {"result": ports.Table()}
    config = {
        "connection": fields.Connection(kind="jdbc"),
        "sql": fields.Code("sql", help="Use :name for parameters, e.g. where day_key = :day_key"),
        "chunk_size": fields.Int(default=50_000, help="Rows fetched and written per chunk"),
    }

    def run(self, ctx, params=None):
        if params is not None and not isinstance(params, dict):
            raise TypeError(f"input 'params' must be a dict of name -> value, got {type(params).__name__}")
        values = {**ctx.params, **(params or {})}
        sql, names = to_qmark(self.config.sql.strip().rstrip(";"))
        missing = [n for n in names if n not in values]
        if missing:
            raise KeyError(f"no value for bind parameter(s): {', '.join(':' + n for n in missing)}")
        out = ctx.new_path("result.parquet")
        conn = jdbc.connect(ctx.connection(self.config.connection), jdbc.all_jars(ctx.home.connections()))
        try:
            rows = jdbc.query_to_parquet(conn, sql, [values[n] for n in names], out, self.config.chunk_size, ctx)
        finally:
            conn.close()
        ctx.log(f"query returned {rows:,} rows")
        return {"result": Table(out)}
