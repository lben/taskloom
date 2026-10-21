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
_BINARY = {-2, -3, -4, 2004}  # BINARY, VARBINARY, LONGVARBINARY, BLOB
_BOOLEAN, _DATE, _TIMESTAMP = 16, 91, 93


def start_jvm(classpath: list[str], java_home: str | None = None):
    """Start the JVM once per process with every JDBC driver jar on the classpath.

    The classpath cannot change after the JVM starts, so all known jars go on it up front
    (Java skips a jar that is missing; `connect` checks the jars of the connection it opens).
    `java_home` (the java_home setting) wins over the JAVA_HOME environment variable.
    """
    import jpype
    import jpype.config

    with _jvm_lock:
        if not jpype.isJVMStarted():
            if java_home:
                os.environ["JAVA_HOME"] = str(Path(java_home).expanduser())  # where JPype looks
            try:
                jvm_path = jpype.getDefaultJVMPath()
            except jpype.JVMNotFoundException:
                jvm_path = None
            # JPype falls back to a Java elsewhere (e.g. /usr/lib/jvm) when the java_home setting has none:
            # the setting is what you asked for, so a Java elsewhere does not count.
            if jvm_path and java_home and not Path(os.fsdecode(jvm_path)).resolve().is_relative_to(
                    Path(java_home).expanduser().resolve()):
                jvm_path = None
            if not jvm_path or not Path(os.fsdecode(jvm_path)).is_file():
                raise FileNotFoundError(
                    "Java was not found; set java_home in settings.yaml (or the JAVA_HOME "
                    "environment variable) to your Java folder")
            # The JVM starts in a block's worker thread; destroying it at exit from the main
            # thread can hang the process, so let the OS reclaim it instead.
            jpype.config.destroy_jvm = False
            jpype.startJVM(jvm_path, classpath=classpath, convertStrings=True)


def _jars(conn: dict) -> list[str]:
    value = conn.get("jars") or []
    return [str(Path(j).expanduser()) for j in ([value] if isinstance(value, str) else value)]


def all_jars(connections: dict) -> list[str]:
    return list(dict.fromkeys(j for conn in connections.values() if conn.get("kind") == "jdbc" for j in _jars(conn)))


def connect(conn: dict, classpath: list[str], java_home: str | None = None):
    """Open a JDBC connection: driver class, URL and driver properties (user, password, …)."""
    for key in ("driver", "url"):
        if not conn.get(key):
            raise ValueError(f"JDBC connection needs '{key}'")
    missing = [j for j in _jars(conn) if not Path(j).exists()]
    if missing:
        raise FileNotFoundError(f"JDBC driver jar(s) not found: {', '.join(missing)}")
    start_jvm(classpath, java_home)
    import jpype

    jpype.JClass(conn["driver"])  # loads and registers the driver
    props = jpype.JClass("java.util.Properties")()
    for key, value in (conn.get("properties") or {}).items():
        props.setProperty(str(key), str(value))
    return jpype.JClass("java.sql.DriverManager").getConnection(conn["url"], props)


def _bind(stmt, args):
    import jpype

    refused = jpype.JClass("java.sql.SQLException")
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
            # As java.time values dates and times pass as written; java.sql.Timestamp/Date would shift them by
            # the computer's time zone (a time its clock skips) and the old Julian calendar (before 1582-10-15).
            # Those are only for a driver that refuses java.time (no JDBC 4.2 support).
            value = value.replace(tzinfo=None)
            try:
                stmt.setObject(i, jpype.JClass("java.time.LocalDateTime").parse(value.isoformat()))
            except refused:
                stmt.setTimestamp(i, jpype.JClass("java.sql.Timestamp").valueOf(value.isoformat(" ")))
        elif isinstance(value, dt.date):
            try:
                stmt.setObject(i, jpype.JClass("java.time.LocalDate").parse(value.isoformat()))
            except refused:
                stmt.setDate(i, jpype.JClass("java.sql.Date").valueOf(value.isoformat()))
        else:
            stmt.setString(i, str(value))


def _time_reader(i, java_time: str, legacy, parse):
    """Reads column i as a java.time value, which comes as written (see _bind). A driver without java.time
    support refuses that (a driver older than JDBC 4.1 lacks the method); from then on the column is read
    with `legacy`."""
    import jpype

    cls = jpype.JClass(java_time).class_
    refused = (jpype.JClass("java.sql.SQLException"), jpype.JClass("java.lang.AbstractMethodError"))
    use_java_time = True

    def read(rs):
        nonlocal use_java_time
        value = None
        if use_java_time:
            try:
                value = rs.getObject(i, cls)
            except refused:
                use_java_time = False
        if not use_java_time:
            value = legacy(rs)
        return None if value is None else parse(str(value.toString()))
    return read


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
        return name, pa.date32(), _time_reader(i, "java.time.LocalDate", lambda rs: rs.getDate(i), dt.date.fromisoformat)
    if sql_type == _TIMESTAMP:
        return name, pa.timestamp("us"), _time_reader(
            i, "java.time.LocalDateTime", lambda rs: rs.getTimestamp(i), lambda s: dt.datetime.fromisoformat(s[:26]))
    if sql_type in _BINARY:
        def read_hex(rs):  # as hex text: getString can garble bytes, and CSV and Excel files take text
            v = rs.getBytes(i)
            return None if v is None else bytes(v).hex()
        return name, pa.string(), read_hex
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
