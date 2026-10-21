"""Run Query (db.run_query) through its real paths, against a real H2 database server (needs Java).

Connections in connections.yaml, passwords as secrets in secrets.yaml or the system credential store (`taskloom
secret set`, the editor's Connections window, where connections are also edited),
`taskloom connection test`, bind parameters and text templates, result types and nulls, chunked streaming to
Parquet, statements that return no rows, failures and their messages, and passwords staying out of logs and history.
"""

import datetime as dt
import decimal
import gc
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
import yaml

from conftest import events

PASSWORD = 'Pa ss:w0rd "q" #é'  # spaces, YAML and shell specials, non-ASCII
WRONG_PASSWORD = "Wr0ng-Passw0rd"


# --- helpers ---------------------------------------------------------------------------------------------------

def h2_sql(h2, db: str, sql: str, password: str = "") -> str:
    """Run SQL on the H2 database `db` with H2's own Shell tool (not Taskloom); the database stays while the
    server runs, and the first connection sets its password."""
    url = f"jdbc:h2:tcp://localhost:{h2['port']}/mem:{db};DB_CLOSE_DELAY=-1"
    proc = subprocess.run([str(Path(h2["java_home"]) / "bin" / "java"), "-cp", str(h2["jar"]), "org.h2.tools.Shell",
                           "-url", url, "-user", "sa", "-password", password, "-sql", sql],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "Error:" not in proc.stdout, proc.stdout + proc.stderr
    return proc.stdout


def add_connection(env, h2, name: str, db: str, password: str | None = None, **conn):
    """Add the JDBC connection `name` (H2 database `db`, user sa) with its password as the secret NAME_password."""
    entry = {"kind": "jdbc", "driver": "org.h2.Driver", "url": f"jdbc:h2:tcp://localhost:{h2['port']}/mem:{db}",
             "jars": [h2["jar"].as_posix()], "properties": {"user": "sa", "password": f"secret:{name}_password"}}
    entry.update(conn)
    with (env.home / "connections.yaml").open("a", encoding="utf-8") as f:
        f.write(yaml.safe_dump({name: entry}, sort_keys=False))
    if password is not None:
        with (env.home / "secrets.yaml").open("a", encoding="utf-8") as f:
            f.write(yaml.safe_dump({f"{name}_password": password}))


def write_flow(env, flow: dict, name: str = "flow.yaml") -> Path:
    path = env.root / name
    path.write_text(yaml.safe_dump(flow, sort_keys=False), encoding="utf-8")
    return path


def query_flow(env, sql: str, connection: str = "warehouse", params: dict | None = None, **config) -> Path:
    """A flow of one Run Query block, `q`."""
    return write_flow(env, {"params": params or {}, "blocks": {
        "q": {"type": "db.run_query", "config": {"connection": connection, "sql": sql, **config}}}})


def result(env, block: str = "q") -> pq.ParquetFile:
    run_id, status = env.last_run()
    assert status == "success", env.query("SELECT block, error FROM blocks WHERE run_id=?", run_id)
    return pq.ParquetFile(env.home / "runs" / str(run_id) / block / "attempt-1" / "result.parquet")


def rows(env, block: str = "q") -> list[dict]:
    return result(env, block).read().to_pylist()


def block_error(env, block: str = "q") -> str:
    run_id, _ = env.last_run()
    return env.query("SELECT error FROM blocks WHERE run_id=? AND block=?", run_id, block)[0][0] or ""


def told(env, run) -> str:
    """What a failed `taskloom run` told the user: its error output, plus the block errors in history when a run
    was recorded (a flow refused before it runs records none)."""
    if not (env.home / "history.db").exists():
        return run.stderr
    return run.stderr + "\n".join(e or "" for (e,) in env.query("SELECT error FROM blocks"))


def split_driver(h2, folder: Path) -> list[Path]:
    """The H2 driver as two jars that only work together (a driver jar and the library jar it needs), the way
    some database vendors ship their JDBC drivers."""
    folder.mkdir(parents=True)
    jars = [folder / "h2-driver.jar", folder / "h2-engine.jar"]
    with zipfile.ZipFile(h2["jar"]) as whole, zipfile.ZipFile(jars[0], "w") as driver, \
            zipfile.ZipFile(jars[1], "w") as engine:
        for item in whole.infolist():
            in_driver = item.filename.startswith(("META-INF/", "org/h2/Driver", "org/h2/JdbcDriverBackwardsCompat",
                                                  "org/h2/jdbc/", "org/h2/api/"))
            (driver if in_driver else engine).writestr(item, whole.read(item))
    return jars


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


CREDENTIAL_STORE = '''
"""A stand-in for the system credential store (Keychain, Credential Manager): a JSON file, $TEST_CREDENTIAL_STORE."""
import json
import os

from keyring.backend import KeyringBackend


class FileStore(KeyringBackend):
    priority = 1

    def _read(self):
        path = os.environ["TEST_CREDENTIAL_STORE"]
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def get_password(self, service, username):
        return self._read().get(service + "/" + username)

    def set_password(self, service, username, password):
        data = self._read()
        data[service + "/" + username] = password
        with open(os.environ["TEST_CREDENTIAL_STORE"], "w", encoding="utf-8") as f:
            json.dump(data, f)
'''


def use_a_credential_store(env, monkeypatch=None) -> Path:
    """Give the taskloom programs `env` runs (and, with monkeypatch, the programs this process starts) a system
    credential store: a JSON file, never the developer's real one. Only the store is a stand-in; Taskloom finds
    and uses it through keyring as it does the real one. Returns the store's file."""
    folder = env.root / "credential-store"
    folder.mkdir()
    (folder / "taskloom_test_store.py").write_text(CREDENTIAL_STORE, encoding="utf-8")
    store = folder / "store.json"
    settings = {"PYTHON_KEYRING_BACKEND": "taskloom_test_store.FileStore", "TEST_CREDENTIAL_STORE": str(store),
                "PYTHONPATH": os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH")) if p)}
    env.env.update(settings)
    if monkeypatch is not None:
        for name, value in settings.items():
            monkeypatch.setenv(name, value)
        monkeypatch.syspath_prepend(str(folder))
    return store


def stored(store: Path) -> dict:
    return json.loads(store.read_text(encoding="utf-8")) if store.exists() else {}


# --- the main path ---------------------------------------------------------------------------------------------

def test_the_readme_flow_queries_by_day_key_and_saves_the_rows_to_csv(jdbc_env, h2_server):
    h2_sql(h2_server, "readme", "create table sales (day_key int, region varchar(20), amount decimal(10, 2));"
           "insert into sales values (20260116, 'north', 10.50), (20260116, 'south', 3.25), (20260115, 'north', 99.99)")
    add_connection(jdbc_env, h2_server, "sales_db", "readme", "")
    flow = jdbc_env.write("flow.yaml", """
        params:
          day: {type: date, expr: "today() - bdays(1)"}       # previous business day
          day_key: {type: int, expr: "int(format(day, '%Y%m%d'))"}
        blocks:
          q:
            type: db.run_query
            config: {connection: sales_db, sql: "select * from sales where day_key = :day_key"}
            retry: {policy: exponential, initial: 30s, factor: 2, max_delay: 10m, max_attempts: 5}
          save:
            type: data.write_file
            config: {path: "out/sales_{day:%Y-%m-%d}.csv"}
        edges:
          - q.result -> save.table
    """)

    run = jdbc_env.taskloom("run", flow, "--param", "day=2026-01-16")

    assert run.returncode == 0, run.stderr
    lines = (jdbc_env.root / "out" / "sales_2026-01-16.csv").read_text().splitlines()
    assert lines[0] == "DAY_KEY,REGION,AMOUNT"
    assert sorted(lines[1:]) == ["20260116,north,10.50", "20260116,south,3.25"]
    run_id, _ = jdbc_env.last_run()
    assert "2 rows" in jdbc_env.query("SELECT summary FROM blocks WHERE run_id=? AND block='q'", run_id)[0][0]


# --- result types ----------------------------------------------------------------------------------------------

def test_every_column_type_comes_back_with_its_value_and_nulls_stay_null(jdbc_env, h2_server):
    h2_sql(h2_server, "typed", """
        create table typed (id int primary key, amount decimal(10, 3), refund decimal(5, 2), day_v date,
          at_v timestamp, label varchar(200), note clob, flag boolean, big_v bigint, small_v smallint,
          ratio double, part real, time_v time);
        insert into typed values (1, 12345.678, -0.5, date '2026-01-16', timestamp '2026-01-16 10:20:30.123456',
          'say "hi" it''s' || char(10) || 'second line', 'a clob', true, 9223372036854775807, -7, 1.5, 0.25,
          time '10:20:30');
        insert into typed (id) values (2)""")
    add_connection(jdbc_env, h2_server, "typed_db", "typed", "")
    flow = query_flow(jdbc_env, """
        select t.*, 'héllo € 日本' as uni, '' as empty_text, repeat('x', 100000) as long_text,
               cast('123456789012345678901234567890.12' as decimal(40, 2)) as wide, 1 as "Net amount € (EUR)"
        from typed t order by id""", connection="typed_db")

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, run.stderr
    full, empty = rows(jdbc_env)
    expected = {
        "ID": 1, "AMOUNT": decimal.Decimal("12345.678"), "REFUND": decimal.Decimal("-0.50"),
        "DAY_V": dt.date(2026, 1, 16), "AT_V": dt.datetime(2026, 1, 16, 10, 20, 30, 123456),
        "LABEL": "say \"hi\" it's\nsecond line", "NOTE": "a clob", "FLAG": True, "BIG_V": 9223372036854775807,
        "SMALL_V": -7, "RATIO": 1.5, "PART": 0.25, "UNI": "héllo € 日本", "EMPTY_TEXT": "",
        "LONG_TEXT": "x" * 100000, "Net amount € (EUR)": 1,
    }
    for name, value in expected.items():
        assert full[name] == value and type(full[name]) is type(value), (name, full[name])
    assert str(full["TIME_V"]) == "10:20:30"
    assert decimal.Decimal(str(full["WIDE"])) == decimal.Decimal("123456789012345678901234567890.12")
    from_table = [k for k in empty if k.isupper() and k not in ("UNI", "EMPTY_TEXT", "LONG_TEXT", "WIDE")]
    assert len(from_table) == 13
    assert {k: empty[k] for k in from_table} == {"ID": 2, **{k: None for k in from_table if k != "ID"}}


def test_timestamps_read_and_bind_as_written_on_a_computer_in_a_dst_time_zone(jdbc_env):
    # A computer in New York: 02:30 on 2026-03-08 does not exist on its clock (it jumps from 02:00 to 03:00),
    # but a TIMESTAMP column holds wall-clock values from anywhere (e.g. event times kept in UTC).
    jdbc_env.env["JAVA_TOOL_OPTIONS"] += " -Duser.timezone=America/New_York"
    flow = query_flow(jdbc_env, "select timestamp '2026-03-08 02:30:00' as stored, cast(:at as varchar) as bound",
                      params={"at": {"type": "datetime", "value": "2026-03-08 02:30:00"}})

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, run.stderr
    assert rows(jdbc_env) == [{"STORED": dt.datetime(2026, 3, 8, 2, 30), "BOUND": "2026-03-08 02:30:00"}]


def test_year_one_dates_read_and_bind_as_written(jdbc_env):
    # 0001-01-01 is a common "no date" placeholder (the smallest date many systems allow).
    jdbc_env.env["JAVA_TOOL_OPTIONS"] += " -Duser.timezone=UTC"
    flow = query_flow(jdbc_env, "select date '0001-01-01' as first_day, timestamp '0001-01-01 00:00:00' as first_moment,"
                                " cast(:d as varchar) as bound",
                      params={"d": {"type": "date", "value": "0001-01-01"}})

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, run.stderr
    assert rows(jdbc_env) == [{"FIRST_DAY": dt.date(1, 1, 1), "FIRST_MOMENT": dt.datetime(1, 1, 1),
                               "BOUND": "0001-01-01"}]


RUN_WITH_A_DRIVER_WITHOUT_JAVA_TIME = '''
"""The taskloom program, with its JDBC connections wrapped to refuse java.time values the way some drivers do
(Hive's, for one): ResultSet.getObject(int, Class) is not supported, and PreparedStatement.setObject cannot tell
the SQL type of a java.time value. Everything else goes to the real H2 driver."""
import sys

import jpype

from taskloom import cli, jdbc


def wrap(real, interface, intercept):
    """`real` behind a proxy of `interface`; intercept(name, args) answers a call, or returns None to pass it on."""
    def invoke(proxy, method, args):
        answer = intercept(str(method.getName()), list(args or []))
        if answer is not None:
            return answer
        try:
            return method.invoke(real, args)
        except jpype.JClass("java.lang.reflect.InvocationTargetException") as e:
            raise e.getCause()
    handler = jpype.JProxy("java.lang.reflect.InvocationHandler", dict={"invoke": invoke})
    return jpype.JClass("java.lang.reflect.Proxy").newProxyInstance(
        jpype.JClass("java.lang.ClassLoader").getSystemClassLoader(), [jpype.JClass(interface)], handler)


def result_set(name, args):
    if name == "getObject" and len(args) == 2:
        raise jpype.JClass("java.sql.SQLFeatureNotSupportedException")("Method not supported")


def statement(real):
    def intercept(name, args):
        if name == "setObject" and len(args) == 2 and str(args[1].getClass().getName()).startswith("java.time."):
            raise jpype.JClass("java.sql.SQLException")("Can't infer the SQL type to use")
        if name == "executeQuery" and not args:
            return wrap(real.executeQuery(), "java.sql.ResultSet", result_set)
    return intercept


real_connect = jdbc.connect


def connect(*args, **kw):
    real = real_connect(*args, **kw)

    def connection(name, args):
        if name == "prepareStatement" and len(args) == 1:
            prepared = real.prepareStatement(args[0])
            return wrap(prepared, "java.sql.PreparedStatement", statement(prepared))
    return wrap(real, "java.sql.Connection", connection)


jdbc.connect = connect
cli.run_cli(sys.argv[1:])
'''


def test_dates_and_timestamps_read_and_bind_with_a_driver_without_java_time_support(jdbc_env):
    # Several rows in chunks of 2: values after the first are read the way the first one was.
    flow = query_flow(jdbc_env, "select date '2026-01-16' as day_v, timestamp '2026-01-16 10:20:30.123456' as at_v,"
                                " cast(null as date) as no_day, cast(null as timestamp) as no_at, :d as bound_day,"
                                " cast(:d as varchar) as bound_text, :at as bound_at from system_range(1, 3)",
                      params={"d": {"type": "date", "value": "2024-02-29"},
                              "at": {"type": "datetime", "value": "2026-01-16 10:20:30.123456"}}, chunk_size=2)

    run = subprocess.run([sys.executable, "-c", RUN_WITH_A_DRIVER_WITHOUT_JAVA_TIME, "run", str(flow)],
                         env=jdbc_env.env, cwd=jdbc_env.root, capture_output=True, text=True, timeout=120)

    assert run.returncode == 0, run.stderr
    assert rows(jdbc_env) == [{
        "DAY_V": dt.date(2026, 1, 16), "AT_V": dt.datetime(2026, 1, 16, 10, 20, 30, 123456), "NO_DAY": None,
        "NO_AT": None, "BOUND_DAY": dt.date(2024, 2, 29), "BOUND_TEXT": "2024-02-29",
        "BOUND_AT": dt.datetime(2026, 1, 16, 10, 20, 30, 123456)}] * 3


def test_binary_column_values_are_not_corrupted(jdbc_env):
    flow = write_flow(jdbc_env, {"blocks": {
        "q": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": "select X'0102ff' as raw_bytes"}},
        "csv": {"type": "data.write_file", "config": {"path": "out/r.csv"}},
        "xlsx": {"type": "data.write_file", "config": {"path": "out/r.xlsx"}}},
        "edges": ["q.result -> csv.table", "q.result -> xlsx.table"]})

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, told(jdbc_env, run)
    value = rows(jdbc_env)[0]["RAW_BYTES"]
    assert value == b"\x01\x02\xff" or str(value).lower() == "0102ff", repr(value)  # the bytes, or their hex
    assert (jdbc_env.root / "out" / "r.csv").read_text().lower().splitlines() == ["raw_bytes", "0102ff"]
    assert str(pl.read_excel(jdbc_env.root / "out" / "r.xlsx")["RAW_BYTES"][0]).lower() == "0102ff"


def test_same_named_columns_of_a_join_are_kept_or_named_in_a_clear_error(jdbc_env):
    flow = query_flow(jdbc_env, "select a.x, b.x from system_range(1, 2) a join system_range(1, 2) b on a.x = b.x")

    run = jdbc_env.taskloom("run", flow)

    if run.returncode == 0:
        assert [list(r.values()) for r in rows(jdbc_env)] == [[1, 1], [2, 2]]
    else:
        assert run.returncode == 1 and "'X'" in block_error(jdbc_env), block_error(jdbc_env)


# --- statements that return no rows ----------------------------------------------------------------------------

def test_a_query_with_no_rows_gives_an_empty_table_with_its_columns(jdbc_env):
    flow = write_flow(jdbc_env, {"blocks": {
        "q": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": (
            "select x as id, cast(x as decimal(8, 2)) as amount, 'a' as label from system_range(1, 3) where x > 10")}},
        "save": {"type": "data.write_file", "config": {"path": "empty.csv"}}},
        "edges": ["q.result -> save.table"]})

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, run.stderr
    table = result(jdbc_env).read()
    assert table.num_rows == 0 and table.column_names == ["ID", "AMOUNT", "LABEL"]
    assert [str(t) for t in table.schema.types] == ["int64", "decimal128(8, 2)", "string"]
    assert (jdbc_env.root / "empty.csv").read_text().splitlines() == ["ID,AMOUNT,LABEL"]


def test_bad_sql_fails_with_the_databases_reason_and_the_error_output_handles_it(jdbc_env):
    flow = write_flow(jdbc_env, {"blocks": {
        "q": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": "selec amount from sales"}},
        "handler": {"type": "logic.python", "config": {"inputs": ["err"], "outputs": [], "code": (
            "open('handled.txt', 'w', encoding='utf-8').write(err['block'] + ': ' + err['error'])")}}},
        "edges": ["q.error -> handler.err"]})

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, run.stderr  # the failure was handled
    assert jdbc_env.block_status(jdbc_env.last_run()[0]) == {"q": "failed", "handler": "success"}
    handled = (jdbc_env.root / "handled.txt").read_text(encoding="utf-8")
    assert handled.startswith("q: ") and "Syntax error" in handled.splitlines()[0]


def test_a_statement_that_is_not_a_query_fails_with_a_clear_error(jdbc_env):
    flow = query_flow(jdbc_env, "create table made_by_run_query (x int)")

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 1
    assert jdbc_env.block_status(jdbc_env.last_run()[0]) == {"q": "failed"}
    assert "query" in block_error(jdbc_env).lower()


# --- parameters ------------------------------------------------------------------------------------------------

def test_flow_parameters_bind_with_their_types_and_text_is_never_sql(jdbc_env):
    flow = query_flow(jdbc_env, """
        select :who as who, :n as n, :ratio as ratio, :flag as flag, :day as day_v, :at as at_v,
               cast(:n as int) * 2 as twice, ':who' as literal, 1::varchar as cast_text  -- :not_a_param
        from (values ('O''Brien''; drop table x; --')) t(name)
        where name = :who;
        """, params={
            "who": {"type": "text", "value": "O'Brien'; drop table x; --"},
            "n": {"type": "int", "value": 21}, "ratio": {"type": "float", "value": 0.25},
            "flag": {"type": "bool", "value": True}, "day": {"type": "date", "value": "2026-01-16"},
            "at": {"type": "datetime", "value": "2026-01-16 10:20:30"}})

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, run.stderr
    assert rows(jdbc_env) == [{"WHO": "O'Brien'; drop table x; --", "N": 21, "RATIO": 0.25, "FLAG": True,
                               "DAY_V": dt.date(2026, 1, 16), "AT_V": dt.datetime(2026, 1, 16, 10, 20, 30),
                               "TWICE": 42, "LITERAL": ":who", "CAST_TEXT": "1"}]


def test_values_from_a_block_before_bind_through_the_params_input(jdbc_env, h2_server):
    h2_sql(h2_server, "by_input", "create table sales (day_v date, region varchar(20), amount decimal(10, 2));"
           "insert into sales values (date '2026-01-16', 'north', 10.50), (date '2026-01-16', 'north', 1.00),"
           "(date '2026-01-15', 'north', 20.00), (date '2026-01-16', 'south', 30.00)")
    add_connection(jdbc_env, h2_server, "sales_db", "by_input", "")
    flow = write_flow(jdbc_env, {
        "params": {"top": {"type": "int", "value": 5}},
        "blocks": {
            "latest": {"type": "logic.python", "config": {"inputs": [], "code": (
                "import datetime, decimal\n"
                "output = {'region': 'north', 'min_amount': decimal.Decimal('5.00'), 'since': datetime.date(2026, 1, 16),"
                " 'until': datetime.datetime(2026, 1, 16, 23, 59, 59), 'nothing': None}")}},
            "q": {"type": "db.run_query", "config": {"connection": "sales_db", "sql": (
                "select region, amount from sales where region = :region and amount >= :min_amount"
                " and day_v >= :since and day_v <= :until and :nothing is null order by amount limit :top")}}},
        "edges": ["latest.output -> q.params"]})

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, run.stderr
    assert rows(jdbc_env) == [{"REGION": "north", "AMOUNT": decimal.Decimal("10.50")}]


def test_a_params_input_that_is_not_a_dict_or_misses_a_value_fails_clearly(jdbc_env):
    flow = write_flow(jdbc_env, {"blocks": {
        "as_list": {"type": "logic.python", "config": {"inputs": [], "code": "output = ['north']"}},
        "as_partial": {"type": "logic.python", "config": {"inputs": [], "code": "output = {'region': 'north'}"}},
        "q_list": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": "select :region as r"}},
        "q_partial": {"type": "db.run_query", "config": {
            "connection": "warehouse", "sql": "select :region as r, :day_key as k"}}},
        "edges": ["as_list.output -> q_list.params", "as_partial.output -> q_partial.params"]})

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 1
    assert "dict" in block_error(jdbc_env, "q_list")
    assert ":day_key" in block_error(jdbc_env, "q_partial")


def test_a_list_parameter_is_not_bound_as_its_python_text(jdbc_env):
    flow = query_flow(jdbc_env, "select region from (values ('north'), ('south'), ('east')) t(region)"
                                " where region in (:regions) order by region",
                      params={"regions": {"type": "list", "value": "north,south"}})

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, told(jdbc_env, run)
    assert rows(jdbc_env) == [{"REGION": "north"}, {"REGION": "south"}]  # bound as its items


def test_a_list_from_the_command_line_or_a_block_binds_as_its_items_and_an_empty_one_fails_clearly(jdbc_env):
    flow = write_flow(jdbc_env, {
        "params": {"regions": {"type": "list", "value": "north"}},
        "blocks": {
            "ids": {"type": "logic.python", "config": {"inputs": [], "code": (
                "import datetime\noutput = {'ids': [3, 1], 'days': [datetime.date(2026, 1, 16)]}")}},
            "q": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": (
                "select id, region from (values (1, 'north', date '2026-01-16'), (2, 'south', date '2026-01-16'),"
                " (3, 'south', date '2026-01-16'), (4, 'east', date '2026-01-16'), (5, 'south', date '2026-01-15'))"
                " t(id, region, day_v)"
                " where region in (:regions) and id in (:ids) and day_v in (:days)"
                " order by id")}}},
        "edges": ["ids.output -> q.params"]})

    run = jdbc_env.taskloom("run", flow, "--param", "regions=north, south ,east")
    found = rows(jdbc_env) if run.returncode == 0 else told(jdbc_env, run)
    empty = jdbc_env.taskloom("run", flow, "--param", "regions=")

    assert run.returncode == 0, found
    assert found == [{"ID": 1, "REGION": "north"}, {"ID": 3, "REGION": "south"}]
    assert empty.returncode == 1
    assert ":regions" in block_error(jdbc_env) and "empty" in block_error(jdbc_env), block_error(jdbc_env)


def test_text_templates_fill_sql_text_such_as_a_table_name(jdbc_env, h2_server):
    h2_sql(h2_server, "templated", "create table sales_20260116 (amount decimal(10, 2));"
           "insert into sales_20260116 values (1.00), (2.00)")
    add_connection(jdbc_env, h2_server, "sales_db", "templated", "")
    flow = query_flow(jdbc_env, "select '{{literal braces}}' as braces, count(*) as n from sales_{day:%Y%m%d}",
                      connection="sales_db", params={"day": {"type": "date", "value": "2026-01-15"}})

    run = jdbc_env.taskloom("run", flow, "--param", "day=2026-01-16")

    assert run.returncode == 0, run.stderr
    assert rows(jdbc_env) == [{"BRACES": "{literal braces}", "N": 2}]


def test_validate_and_run_report_connection_and_parameter_mistakes(jdbc_env):
    with (jdbc_env.home / "connections.yaml").open("a") as f:
        f.write("appserver: {kind: ssh, host: localhost, username: me}\n")
    flow = write_flow(jdbc_env, {"params": {"day": {"type": "date", "value": "2026-01-16"}}, "blocks": {
        "a": {"type": "db.run_query", "config": {"connection": "nope", "sql": "select 1"}},
        "b": {"type": "db.run_query", "config": {"connection": "appserver", "sql": "select 1"}},
        "c": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": "select :missing_bind as x"}},
        "d": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": "select '{missing_text}' as x"}}}})

    checked = jdbc_env.taskloom("validate", flow)
    run = jdbc_env.taskloom("run", flow)

    for output in (checked, run):
        assert output.returncode == 2
        errors = [line for line in output.stderr.splitlines() if line.startswith("error:")]
        assert len(errors) == 4, output.stderr
        assert "blocks.a" in errors[0] and "'nope'" in errors[0]
        assert "blocks.b" in errors[1] and "ssh" in errors[1] and "jdbc" in errors[1]
        assert "blocks.c" in errors[2] and ":missing_bind" in errors[2]
        assert "blocks.d" in errors[3] and "{missing_text}" in errors[3]
    assert not (jdbc_env.home / "history.db").exists() or jdbc_env.query("SELECT count(*) FROM runs") == [(0,)]


# --- streaming big results -------------------------------------------------------------------------------------

@pytest.mark.parametrize("n, chunk_size, row_groups", [(10, 3, 4), (9, 3, 3)])
def test_chunk_size_rows_are_written_per_chunk_and_none_are_lost(jdbc_env, n, chunk_size, row_groups):
    flow = query_flow(jdbc_env, f"select x as id from system_range(1, {n})", chunk_size=chunk_size)

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, run.stderr
    parquet = result(jdbc_env)
    assert parquet.metadata.num_row_groups == row_groups
    assert parquet.read().column("ID").to_pylist() == list(range(1, n + 1))


def test_a_chunk_size_of_zero_does_not_hang_the_run(jdbc_env):
    flow = query_flow(jdbc_env, "select x as id from system_range(1, 3)", chunk_size=0)

    try:
        run = jdbc_env.taskloom("run", flow, timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail("the run never ended (chunk_size: 0)")

    if run.returncode == 0:
        assert result(jdbc_env).read().column("ID").to_pylist() == [1, 2, 3]
    else:  # or refused (before or during the run), naming the field
        assert "chunk_size" in told(jdbc_env, run), told(jdbc_env, run)


def test_stop_during_a_big_query_cancels_the_run(jdbc_env, h2_server):
    # Lazy execution: H2 sends the rows as they are fetched, the way a database server streams a big result. By
    # default it first builds all 50 million in its memory (about 5 GB), which can take many minutes on a smaller
    # computer before the first row comes.
    add_connection(jdbc_env, h2_server, "big_db", "big", "",
                   url=f"jdbc:h2:tcp://localhost:{h2_server['port']}/mem:big;LAZY_QUERY_EXECUTION=TRUE")
    flow = query_flow(jdbc_env, "select x as id, 'row-' || x as label from system_range(1, 50000000)",
                      connection="big_db", chunk_size=10_000)
    proc = jdbc_env.popen("run", flow, "--json-events", "--interactive")
    deadline = threading.Timer(120, proc.kill)  # rows that never come fail the test instead of hanging it
    deadline.start()
    try:
        for line in proc.stdout:  # wait until rows are streaming
            event = json.loads(line) if line.startswith("{") else {}
            if event.get("event") == "log" and event.get("block") == "q":
                break
        else:  # the run ended, or was ended at the deadline, before any rows streamed
            pytest.fail("no rows streamed: " + proc.communicate(timeout=60)[1])
        deadline.cancel()
        proc.stdin.write('{"cancel": true}\n')  # what the editor's Stop sends
        proc.stdin.flush()
        out, _ = proc.communicate(timeout=60)
    finally:
        deadline.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert proc.returncode == 130
    assert jdbc_env.last_run()[1] == "cancelled"
    assert [e["status"] for e in events(out) if e["event"] == "block_finished"] == ["cancelled"]


def test_a_block_timeout_ends_a_query_that_never_returns(jdbc_env, h2_server):
    h2_sql(h2_server, "slow", "create alias sleep_ms for 'java.lang.Thread.sleep(long)'")
    add_connection(jdbc_env, h2_server, "slow_db", "slow", "")
    flow = write_flow(jdbc_env, {"blocks": {"q": {
        "type": "db.run_query", "timeout": "2s",
        "config": {"connection": "slow_db", "sql": "select sleep_ms(30000) as s"}}}})

    started = time.monotonic()
    run = jdbc_env.taskloom("run", flow, timeout=60)

    assert time.monotonic() - started < 20
    assert run.returncode == 1
    assert "timed out" in block_error(jdbc_env)


# --- connections, secrets and their failures -------------------------------------------------------------------

def test_a_wrong_password_fails_clearly_and_no_password_reaches_logs_or_history(jdbc_env, h2_server):
    h2_sql(h2_server, "locked", "create table sales (amount decimal(10, 2)); insert into sales values (10.50)",
           password=PASSWORD)
    add_connection(jdbc_env, h2_server, "locked", "locked", PASSWORD)
    flow = query_flow(jdbc_env, "select amount from sales", connection="locked")

    good_run = jdbc_env.taskloom("run", flow, "--json-events")
    good_test = jdbc_env.taskloom("connection", "test", "locked")
    good_rows = rows(jdbc_env)
    secrets = jdbc_env.home / "secrets.yaml"
    secrets.write_text(yaml.safe_dump({"warehouse_password": "", "locked_password": WRONG_PASSWORD}))
    bad_run = jdbc_env.taskloom("run", flow, "--json-events")
    bad_test = jdbc_env.taskloom("connection", "test", "locked")

    assert good_run.returncode == 0, good_run.stderr
    assert good_rows == [{"AMOUNT": decimal.Decimal("10.50")}]
    assert good_test.returncode == 0 and "Connected to H2" in good_test.stdout
    assert bad_run.returncode == 1 and "Wrong user name or password" in block_error(jdbc_env)
    assert bad_test.returncode == 1 and "Wrong user name or password" in bad_test.stderr
    printed = [p.stdout + p.stderr for p in (good_run, good_test, bad_run, bad_test)]
    gc.collect()  # closes the history.db connections env.query left open, so no file vanishes while it is read
    files = [p for p in jdbc_env.root.rglob("*") if p.is_file() and p != secrets]
    assert len(files) > 3
    for password in (PASSWORD, WRONG_PASSWORD):
        assert not any(password in text for text in printed), password
        assert not [p for p in files if password.encode() in p.read_bytes()], password


@pytest.mark.skipif(sys.platform == "win32", reason="getpass reads the Windows console, not stdin; POSIX file modes")
def test_secret_set_stores_a_password_that_queries_then_use(jdbc_env, h2_server):
    h2_sql(h2_server, "vault", "create table t (x int); insert into t values (7)", password=PASSWORD)
    add_connection(jdbc_env, h2_server, "vault", "vault")
    flow = query_flow(jdbc_env, "select x from t", connection="vault")

    stored = jdbc_env.taskloom("secret", "set", "vault_password", input=PASSWORD + "\n", start_new_session=True)
    run = jdbc_env.taskloom("run", flow)

    assert stored.returncode == 0 and "saved secret 'vault_password'" in stored.stdout, stored.stderr
    assert PASSWORD not in stored.stdout + stored.stderr
    secrets = jdbc_env.home / "secrets.yaml"
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert yaml.safe_load(secrets.read_text()) == {"warehouse_password": "", "vault_password": PASSWORD}
    assert run.returncode == 0, run.stderr
    assert rows(jdbc_env) == [{"X": 7}]


@pytest.mark.skipif(sys.platform == "win32", reason="getpass reads the Windows console, not stdin")
def test_secret_set_saves_in_the_system_credential_store_and_queries_log_in_with_it(jdbc_env, h2_server):
    store = use_a_credential_store(jdbc_env)
    h2_sql(h2_server, "keychain", "create table t (x int); insert into t values (7)", password=PASSWORD)
    add_connection(jdbc_env, h2_server, "vault", "keychain")
    secrets = jdbc_env.home / "secrets.yaml"  # an older value of the same name in secrets.yaml (e.g. copied over)
    secrets.write_text(yaml.safe_dump({"warehouse_password": "", "vault_password": WRONG_PASSWORD}))
    flow = write_flow(jdbc_env, {"blocks": {
        "from_store": {"type": "db.run_query", "config": {"connection": "vault", "sql": "select x from t"}},
        "from_file": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": "select 1 as one"}}}})

    saved = jdbc_env.taskloom("secret", "set", "vault_password", input=PASSWORD + "\n", start_new_session=True)
    run = jdbc_env.taskloom("run", flow)
    tested = jdbc_env.taskloom("connection", "test", "vault")

    assert saved.returncode == 0, saved.stderr
    assert "saved secret 'vault_password' in the system credential store" in saved.stdout
    assert stored(store) == {"taskloom/vault_password": PASSWORD}
    assert yaml.safe_load(secrets.read_text()) == {"warehouse_password": "", "vault_password": WRONG_PASSWORD}
    assert run.returncode == 0, told(jdbc_env, run)
    assert rows(jdbc_env, "from_store") == [{"X": 7}]  # the value just set, not the older one in the file
    assert rows(jdbc_env, "from_file") == [{"ONE": 1}]  # a secret only in secrets.yaml is still found
    assert tested.returncode == 0 and "Connected to H2" in tested.stdout, tested.stderr
    assert not [p for p in (saved, run, tested) if PASSWORD in p.stdout + p.stderr]


def test_a_password_secret_that_is_not_set_fails_with_how_to_set_it(jdbc_env):
    (jdbc_env.home / "secrets.yaml").write_text("other: x\n")
    flow = query_flow(jdbc_env, "select 1 as one")

    run = jdbc_env.taskloom("run", flow)
    tested = jdbc_env.taskloom("connection", "test", "warehouse")

    assert run.returncode == 1 and "taskloom secret set warehouse_password" in block_error(jdbc_env)
    assert tested.returncode == 1 and "taskloom secret set warehouse_password" in tested.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_a_secrets_file_others_can_read_is_refused(jdbc_env):
    (jdbc_env.home / "secrets.yaml").chmod(0o644)
    flow = query_flow(jdbc_env, "select 1 as one")

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 1 and "chmod 600" in block_error(jdbc_env)


def _missing_jar(env, h2, conn):
    conn["jars"] = [(env.root / "drivers" / "missing-driver.jar").as_posix()]
    return "missing-driver.jar"


def _unknown_driver(env, h2, conn):
    conn["driver"] = "org.example.NoSuchDriver"
    return "org.example.NoSuchDriver"


def _no_url(env, h2, conn):
    del conn["url"]
    return "url"


def _unreachable(env, h2, conn):
    env.env["JAVA_TOOL_OPTIONS"] += " -Dh2.socketConnectRetry=0"  # H2 retries a refused connection 16 times
    conn["url"] = f"jdbc:h2:tcp://localhost:{free_port()}/mem:test"
    return "Connection refused"


@pytest.mark.parametrize("breaks", [_missing_jar, _unknown_driver, _no_url, _unreachable],
                         ids=["missing-jar", "unknown-driver-class", "no-url", "unreachable-server"])
def test_a_broken_connection_fails_the_query_and_the_connection_test_clearly(jdbc_env, h2_server, breaks):
    conns = yaml.safe_load((jdbc_env.home / "connections.yaml").read_text())
    shown = breaks(jdbc_env, h2_server, conns["warehouse"])
    (jdbc_env.home / "connections.yaml").write_text(yaml.safe_dump(conns))
    flow = query_flow(jdbc_env, "select 1 as one")

    started = time.monotonic()
    run = jdbc_env.taskloom("run", flow)
    tested = jdbc_env.taskloom("connection", "test", "warehouse")

    assert time.monotonic() - started < 60
    assert run.returncode == 1 and shown in block_error(jdbc_env), block_error(jdbc_env)
    assert tested.returncode == 1 and shown in tested.stderr, tested.stderr
    assert "Traceback" not in tested.stderr


def test_a_missing_jar_of_another_connection_does_not_break_this_one(jdbc_env):
    with (jdbc_env.home / "connections.yaml").open("a") as f:
        f.write(yaml.safe_dump({"old_db": {"kind": "jdbc", "driver": "com.example.OldDriver", "url": "jdbc:old://x",
                                           "jars": [(jdbc_env.root / "gone" / "old-driver.jar").as_posix()]}}))
    flow = query_flow(jdbc_env, "select 1 as one")

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, block_error(jdbc_env)
    assert rows(jdbc_env) == [{"ONE": 1}]


def test_unknown_connection_name_in_connection_test_is_reported(env):
    tested = env.taskloom("connection", "test", "nope")

    assert tested.returncode == 1 and "no connection named 'nope'" in tested.stderr


def test_a_connection_without_a_kind_is_reported(env):
    env.write("home/connections.yaml", "broken:\n  driver: org.h2.Driver\n")
    flow = query_flow(env, "select 1 as one", connection="broken")

    run = env.taskloom("run", flow)

    assert run.returncode == 2 and "connection 'broken' needs a 'kind'" in run.stderr


def test_driver_properties_reach_the_driver_and_jars_can_be_one_path(jdbc_env, h2_server):
    add_connection(jdbc_env, h2_server, "lower_db", "lower_case", "", jars=h2_server["jar"].as_posix(),
                   properties={"user": "sa", "password": "secret:lower_db_password", "DATABASE_TO_LOWER": "TRUE"})
    flow = query_flow(jdbc_env, "select 1 as amount", connection="lower_db")

    run = jdbc_env.taskloom("run", flow)

    assert run.returncode == 0, run.stderr
    assert rows(jdbc_env) == [{"amount": 1}]  # the setting made H2 keep names in lower case


def test_java_is_found_through_java_home_or_the_JAVA_HOME_variable(jdbc_env, h2_server):
    flow = query_flow(jdbc_env, "select 1 as one")
    settings = jdbc_env.home / "settings.yaml"
    nowhere = (jdbc_env.root / "no-java-here").as_posix()

    settings.write_text("{}\n")
    jdbc_env.env["JAVA_HOME"] = h2_server["java_home"]
    by_variable = jdbc_env.taskloom("run", flow)
    settings.write_text(f"java_home: '{Path(h2_server['java_home']).as_posix()}'\n")
    jdbc_env.env["JAVA_HOME"] = nowhere
    setting_wins = jdbc_env.taskloom("run", flow)
    settings.write_text(f"java_home: '{nowhere}'\n")
    del jdbc_env.env["JAVA_HOME"]
    not_found = jdbc_env.taskloom("run", flow)

    assert by_variable.returncode == 0, by_variable.stderr
    assert setting_wins.returncode == 0, setting_wins.stderr
    assert not_found.returncode == 1
    assert "Java was not found" in block_error(jdbc_env) and "java_home" in block_error(jdbc_env)


# --- the editor ------------------------------------------------------------------------------------------------

def test_a_database_connection_made_in_the_editor_is_tested_and_used_by_a_query(
        jdbc_env, h2_server, win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QToolButton

    from taskloom.editor.dialogs import ConnectionsDialog

    h2_sql(h2_server, "from_editor", "create table sales (amount decimal(10, 2)); insert into sales values (10.50)",
           password=PASSWORD)
    flow = jdbc_env.write("flow.yaml", """
        blocks:
          start: {type: trigger.manual}
          q:
            type: db.run_query
            config: {sql: "select amount from sales"}
        edges:
          - start.start -> q.after
    """)
    tab = win.open_flow(flow)
    tab.scene.nodes["q"].setSelected(True)
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", staticmethod(lambda *a, **k: ("sales_db", True)))
    tested = []

    def test_with(dialog, password):
        dialog.password.setText(password)
        dialog.test_button.click()
        qtbot.waitUntil(lambda: dialog.test_button.isEnabled() and "Testing" not in dialog.test_result.text(),
                        timeout=60_000)
        tested.append(dialog.test_result.text())

    def fill_in(dialog):
        assert dialog._kind() == "jdbc"  # opened from Run Query's connection field: a database connection
        dialog.edits["driver"].setText("org.h2.Driver")
        dialog.edits["url"].setText(f"jdbc:h2:tcp://localhost:{h2_server['port']}/mem:from_editor")
        dialog.edits["jars"].setText(h2_server["jar"].as_posix())
        dialog.edits["username"].setText("sa")
        test_with(dialog, WRONG_PASSWORD)  # a typo first
        test_with(dialog, PASSWORD)
        dialog._save()
        return True

    monkeypatch.setattr(ConnectionsDialog, "exec", fill_in)
    next(b for b in win.properties.findChildren(QToolButton) if b.text() == "New…").click()

    wrong, right = tested
    assert "✓" in right and "Connected to H2" in right
    assert tab.doc.blocks["q"]["config"]["connection"] == "sales_db"
    assert yaml.safe_load((jdbc_env.home / "connections.yaml").read_text())["sales_db"] == {
        "kind": "jdbc", "driver": "org.h2.Driver", "url": f"jdbc:h2:tcp://localhost:{h2_server['port']}/mem:from_editor",
        "jars": [h2_server["jar"].as_posix()], "properties": {"user": "sa", "password": "secret:sales_db_password"}}
    assert win.home.get_secret("sales_db_password") == PASSWORD

    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=60_000)

    assert tab.scene.nodes["q"].status == "success"
    assert rows(jdbc_env) == [{"AMOUNT": decimal.Decimal("10.50")}]
    assert "✕" in wrong and WRONG_PASSWORD not in wrong
    assert "Wrong user name or password" in wrong, wrong  # the reason, not just the end of a Java stack trace


def test_a_password_typed_in_the_editor_goes_to_the_system_credential_store_and_queries_log_in_with_it(
        jdbc_env, h2_server, win, qtbot, monkeypatch, request):
    import importlib

    import keyring
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QDialogButtonBox, QPushButton

    from taskloom.editor.dialogs import ConnectionsDialog

    before = keyring.get_keyring()  # the `win` fixture's: no store
    store = use_a_credential_store(jdbc_env, monkeypatch)  # for the runs and tests the editor starts
    keyring.set_keyring(importlib.import_module("taskloom_test_store").FileStore())  # for the editor itself
    request.addfinalizer(lambda: keyring.set_keyring(before))
    h2_sql(h2_server, "editor_store", "create table t (x int); insert into t values (7)", password=PASSWORD)
    flow = jdbc_env.write("flow.yaml", """
        blocks:
          start: {type: trigger.manual}
          q:
            type: db.run_query
            config: {connection: vault_db, sql: "select x from t"}
        edges:
          - start.start -> q.after
    """)
    tab = win.open_flow(flow)
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", staticmethod(lambda *a, **k: ("vault_db", True)))
    seen = {}

    def fill_in(dialog):
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "New…").click()
        dialog.edits["driver"].setText("org.h2.Driver")
        dialog.edits["url"].setText(f"jdbc:h2:tcp://localhost:{h2_server['port']}/mem:editor_store")
        dialog.edits["jars"].setText(h2_server["jar"].as_posix())
        dialog.edits["username"].setText("sa")
        dialog.password.setText(PASSWORD)
        dialog.test_button.click()
        qtbot.waitUntil(lambda: dialog.test_button.isEnabled() and "Testing" not in dialog.test_result.text(),
                        timeout=60_000)
        seen["tested"] = dialog.test_result.text()
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
        seen["status"] = dialog.password_status.text()
        return True

    monkeypatch.setattr(ConnectionsDialog, "exec", fill_in)
    next(a for a in win.findChildren(QAction) if a.text() == "Connections…").trigger()  # Tools -> Connections

    assert "✓" in seen["tested"] and "Connected to H2" in seen["tested"], seen["tested"]
    assert "A password is saved" in seen["status"], seen["status"]
    assert stored(store) == {"taskloom/vault_db_password": PASSWORD}
    conns = yaml.safe_load((jdbc_env.home / "connections.yaml").read_text())
    assert conns["vault_db"]["properties"] == {"user": "sa", "password": "secret:vault_db_password"}

    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=60_000)

    assert tab.scene.nodes["q"].status == "success"
    assert rows(jdbc_env) == [{"X": 7}]
    gc.collect()  # closes the history.db connections env.query left open, so no file vanishes while it is read
    files = [p for p in jdbc_env.home.rglob("*") if p.is_file()]
    assert not [p for p in files if PASSWORD.encode() in p.read_bytes()]  # "never in a file"


def test_editing_a_database_connection_keeps_its_password_and_adds_driver_settings(
        jdbc_env, h2_server, win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialogButtonBox, QToolButton

    from taskloom.editor.dialogs import ConnectionsDialog

    h2_sql(h2_server, "edited", "create schema reports; create table reports.sales (amount decimal(10, 2));"
           "insert into reports.sales values (10.50)", password=PASSWORD)
    add_connection(jdbc_env, h2_server, "sales_db", "edited", PASSWORD, properties={
        "user": "sa", "password": "secret:sales_db_password", "QUERY_TIMEOUT": 600000})
    before = yaml.safe_load((jdbc_env.home / "connections.yaml").read_text())
    flow = jdbc_env.write("flow.yaml", """
        blocks:
          start: {type: trigger.manual}
          q:
            type: db.run_query
            config: {connection: sales_db, sql: "select amount from sales"}
        edges:
          - start.start -> q.after
    """)
    tab = win.open_flow(flow)
    tab.scene.nodes["q"].setSelected(True)
    seen = {}

    def edit(dialog):
        seen["selected"] = dialog.names.currentItem().text()
        seen["status"] = dialog.password_status.text()
        table = dialog.props
        seen["settings"] = [(table.item(r, 0).text(), table.item(r, 1).text()) for r in range(table.rowCount())]
        dialog.add_prop.click()  # Add setting: the schema the query's table is in
        table.item(table.rowCount() - 1, 0).setText("SCHEMA")
        table.item(table.rowCount() - 1, 1).setText("REPORTS")
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()  # the password is not retyped
        return True

    monkeypatch.setattr(ConnectionsDialog, "exec", edit)
    next(b for b in win.properties.findChildren(QToolButton) if b.text() == "Edit…").click()

    assert seen["selected"] == "sales_db"
    assert "A password is saved" in seen["status"], seen["status"]
    assert seen["settings"] == [("QUERY_TIMEOUT", "600000")]  # user and password have their own fields
    after = yaml.safe_load((jdbc_env.home / "connections.yaml").read_text())
    assert after["warehouse"] == before["warehouse"]
    assert {k: v for k, v in after["sales_db"].items() if k != "properties"} == {
        k: v for k, v in before["sales_db"].items() if k != "properties"}
    assert {k: str(v) for k, v in after["sales_db"]["properties"].items()} == {
        "user": "sa", "password": "secret:sales_db_password", "QUERY_TIMEOUT": "600000", "SCHEMA": "REPORTS"}

    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=60_000)

    assert tab.scene.nodes["q"].status == "success", block_error(jdbc_env)
    assert rows(jdbc_env) == [{"AMOUNT": decimal.Decimal("10.50")}]  # logged in, and found sales in REPORTS


def test_several_driver_jars_typed_in_the_editor_are_saved_kept_when_edited_and_used_by_a_query(
        jdbc_env, h2_server, win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialogButtonBox, QToolButton

    from taskloom.editor.dialogs import ConnectionsDialog

    jars = split_driver(h2_server, jdbc_env.root / "my drivers")
    (jdbc_env.home / "connections.yaml").write_text("{}\n")  # no other connection puts the whole H2 jar in use
    h2_sql(h2_server, "two_jars", "create table sales (amount decimal(10, 2)); insert into sales values (10.50)")
    flow = jdbc_env.write("flow.yaml", """
        blocks:
          start: {type: trigger.manual}
          q:
            type: db.run_query
            config: {sql: "select amount from sales"}
        edges:
          - start.start -> q.after
    """)
    tab = win.open_flow(flow)
    tab.scene.nodes["q"].setSelected(True)
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", staticmethod(lambda *a, **k: ("sales_db", True)))
    seen = {}

    def button(text):
        return next(b for b in win.properties.widget().findChildren(QToolButton) if b.text() == text)  # shown now

    def fill_in(dialog):
        dialog.edits["driver"].setText("org.h2.Driver")
        dialog.edits["url"].setText(f"jdbc:h2:tcp://localhost:{h2_server['port']}/mem:two_jars")
        dialog.edits["jars"].setText(";".join(j.as_posix() for j in jars))  # "separate several files with ;"
        dialog.edits["username"].setText("sa")
        dialog.test_button.click()
        qtbot.waitUntil(lambda: dialog.test_button.isEnabled() and "Testing" not in dialog.test_result.text(),
                        timeout=60_000)
        seen["tested"] = dialog.test_result.text()
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
        return True

    def edit_again(dialog):
        seen["shown"] = dialog.edits["jars"].text()
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
        return True

    monkeypatch.setattr(ConnectionsDialog, "exec", fill_in)
    button("New…").click()
    first = yaml.safe_load((jdbc_env.home / "connections.yaml").read_text())["sales_db"]
    monkeypatch.setattr(ConnectionsDialog, "exec", edit_again)
    button("Edit…").click()
    again = yaml.safe_load((jdbc_env.home / "connections.yaml").read_text())["sales_db"]

    assert "✓" in seen["tested"] and "Connected to H2" in seen["tested"], seen["tested"]
    assert first["jars"] == [j.as_posix() for j in jars]
    assert seen["shown"] == "; ".join(j.as_posix() for j in jars)
    assert again == first
    assert tab.doc.blocks["q"]["config"]["connection"] == "sales_db"

    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=60_000)

    assert tab.scene.nodes["q"].status == "success", block_error(jdbc_env)
    assert rows(jdbc_env) == [{"AMOUNT": decimal.Decimal("10.50")}]
