"""JDBC access through JPype, streaming query results to Parquet in chunks."""

from __future__ import annotations

import datetime as dt
import decimal
import os
import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

_jvm_lock = threading.Lock()

# java.sql.Types constants
_INTS = {-7, -6, 5, 4, -5}  # BIT, TINYINT, SMALLINT, INTEGER, BIGINT
_FLOATS = {6, 7, 8}  # FLOAT, REAL, DOUBLE
_DECIMALS = {2, 3}  # NUMERIC, DECIMAL
_BOOLEAN, _DATE, _TIMESTAMP = 16, 91, 93


def start_jvm(classpath: list[str]):
    """Start the JVM once per process with every JDBC driver jar on the classpath.

    The classpath cannot change after the JVM starts, so all known jars go on it up front.
    """
    import jpype
    import jpype.config

    with _jvm_lock:
        if not jpype.isJVMStarted():
            missing = [p for p in classpath if not Path(p).exists()]
            if missing:
                raise FileNotFoundError(f"JDBC driver jar(s) not found: {', '.join(missing)}")
            try:
                jvm_path = jpype.getDefaultJVMPath()
            except jpype.JVMNotFoundException:
                jvm_path = None
            if not jvm_path or not Path(os.fsdecode(jvm_path)).is_file():
                raise FileNotFoundError("Java was not found; set the JAVA_HOME environment variable to your Java folder")
            # The JVM starts in a block's worker thread; destroying it at exit from the main
            # thread can hang the process, so let the OS reclaim it instead.
            jpype.config.destroy_jvm = False
            jpype.startJVM(jvm_path, classpath=classpath, convertStrings=True)


def all_jars(connections: dict) -> list[str]:
    jars: list[str] = []
    for conn in connections.values():
        if conn.get("kind") == "jdbc":
            value = conn.get("jars") or []
            jars += [value] if isinstance(value, str) else list(value)
    return [str(Path(j).expanduser()) for j in dict.fromkeys(jars)]


def connect(conn: dict, classpath: list[str]):
    """Open a JDBC connection: driver class, URL and driver properties (user, password, …)."""
    for key in ("driver", "url"):
        if not conn.get(key):
            raise ValueError(f"JDBC connection needs '{key}'")
    start_jvm(classpath)
    import jpype

    jpype.JClass(conn["driver"])  # loads and registers the driver
    props = jpype.JClass("java.util.Properties")()
    for key, value in (conn.get("properties") or {}).items():
        props.setProperty(str(key), str(value))
    return jpype.JClass("java.sql.DriverManager").getConnection(conn["url"], props)


def _bind(stmt, args):
    import jpype

    for i, value in enumerate(args, start=1):
        if value is None:
            stmt.setNull(i, 0)  # java.sql.Types.NULL
        elif isinstance(value, bool):
            stmt.setBoolean(i, value)
        elif isinstance(value, int):
            stmt.setLong(i, value)
        elif isinstance(value, float):
            stmt.setDouble(i, value)
        elif isinstance(value, decimal.Decimal):
            stmt.setBigDecimal(i, jpype.JClass("java.math.BigDecimal")(str(value)))
        elif isinstance(value, dt.datetime):
            stmt.setTimestamp(i, jpype.JClass("java.sql.Timestamp").valueOf(value.strftime("%Y-%m-%d %H:%M:%S.%f")))
        elif isinstance(value, dt.date):
            stmt.setDate(i, jpype.JClass("java.sql.Date").valueOf(value.isoformat()))
        else:
            stmt.setString(i, str(value))


def _column(meta, i):
    """(name, arrow type, reader) for column i, chosen once from the result metadata."""
    name, sql_type = str(meta.getColumnLabel(i)), int(meta.getColumnType(i))
    precision, scale = int(meta.getPrecision(i)), int(meta.getScale(i))

    def nullable(read):
        def reader(rs):
            value = read(rs)
            return None if rs.wasNull() else value
        return reader

    if sql_type in _INTS:
        return name, pa.int64(), nullable(lambda rs: int(rs.getLong(i)))
    if sql_type in _FLOATS:
        return name, pa.float64(), nullable(lambda rs: float(rs.getDouble(i)))
    if sql_type in _DECIMALS and 0 < precision <= 38 and 0 <= scale <= precision:
        def read_decimal(rs):
            v = rs.getBigDecimal(i)
            return None if v is None else decimal.Decimal(str(v.toPlainString()))
        return name, pa.decimal128(precision, scale), read_decimal
    if sql_type == _BOOLEAN:
        return name, pa.bool_(), nullable(lambda rs: bool(rs.getBoolean(i)))
    if sql_type == _DATE:
        def read_date(rs):
            v = rs.getDate(i)
            return None if v is None else dt.date.fromisoformat(str(v.toString()))
        return name, pa.date32(), read_date
    if sql_type == _TIMESTAMP:
        def read_timestamp(rs):
            v = rs.getTimestamp(i)
            return None if v is None else dt.datetime.fromisoformat(str(v.toString())[:26])
        return name, pa.timestamp("us"), read_timestamp
    return name, pa.string(), lambda rs: rs.getString(i)


def query_to_parquet(jconn, sql: str, args: list, path: Path, chunk_size: int, ctx) -> int:
    """Run a query and write its rows to `path`, one Parquet row group per chunk.

    Only one chunk is in memory at a time. Returns the number of rows written.
    """
    stmt = jconn.prepareStatement(sql)
    try:
        stmt.setFetchSize(chunk_size)
        _bind(stmt, args)
        rs = stmt.executeQuery()
        meta = rs.getMetaData()
        columns = [_column(meta, i) for i in range(1, meta.getColumnCount() + 1)]
        schema = pa.schema([(name, type_) for name, type_, _ in columns])
        readers = [reader for _, _, reader in columns]
        total = 0
        with pq.ParquetWriter(path, schema) as writer:
            while True:
                ctx.check_cancelled()
                data = [[] for _ in readers]
                rows = 0
                while rows < chunk_size and rs.next():
                    for values, read in zip(data, readers):
                        values.append(read(rs))
                    rows += 1
                if rows:
                    writer.write_table(pa.Table.from_arrays([pa.array(v, t) for v, t in zip(data, schema.types)], schema=schema))
                    total += rows
                    ctx.log(f"streamed {total:,} rows to parquet")
                if rows < chunk_size:
                    break
        return total
    finally:
        stmt.close()
