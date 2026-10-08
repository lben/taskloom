"""JDBC queries against a real H2 database server (needs Java; downloads the H2 jar once)."""

import datetime as dt
import decimal
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq
import pytest

H2_URL = "https://repo1.maven.org/maven2/com/h2database/h2/2.3.232/h2-2.3.232.jar"
H2_SHA1 = "4fcc05d966ccdb2812ae8b9a718f69226c0cf4e2"
MAXRSS_SCRIPT = (
    "import resource, sys; from taskloom.cli import main; rc = main(sys.argv[1:]); "
    "print('MAXRSS', resource.getrusage(resource.RUSAGE_SELF).ru_maxrss); sys.exit(rc)"
)


def _java_home() -> str | None:
    if os.environ.get("JAVA_HOME"):
        return os.environ["JAVA_HOME"]
    if sys.platform == "darwin" and Path("/usr/libexec/java_home").exists():
        found = subprocess.run(["/usr/libexec/java_home"], capture_output=True, text=True)
        return found.stdout.strip() or None
    java = shutil.which("java")
    return str(Path(java).resolve().parents[1]) if java else None


def _h2_jar() -> Path:
    jar = Path.home() / ".cache" / "taskloom-tests" / "h2-2.3.232.jar"
    if not jar.exists():
        jar.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = urllib.request.urlopen(H2_URL, timeout=60).read()
        except OSError as e:
            pytest.skip(f"cannot download the H2 JDBC driver: {e}")
        assert hashlib.sha1(data).hexdigest() == H2_SHA1
        jar.write_bytes(data)
    return jar


@pytest.fixture(scope="module")
def h2_server():
    java_home = _java_home()
    if not java_home:
        pytest.skip("Java is not installed")
    jar = _h2_jar()
    with socket.socket() as s:
        s.bind(("localhost", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen(
        [str(Path(java_home) / "bin" / "java"), "-cp", str(jar), "org.h2.tools.Server",
         "-tcp", "-tcpPort", str(port), "-ifNotExists"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            socket.create_connection(("localhost", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield {"jar": jar, "port": port, "java_home": java_home}
    server.terminate()
    server.wait()


@pytest.fixture
def jdbc_env(env, h2_server):
    env.env["JAVA_HOME"] = h2_server["java_home"]
    env.env["JAVA_TOOL_OPTIONS"] = "-Xmx256m"  # keep JVM heap growth from hiding Python memory use
    env.write("home/connections.yaml", f"""
        warehouse:
          kind: jdbc
          driver: org.h2.Driver
          url: "jdbc:h2:tcp://localhost:{h2_server['port']}/mem:test"
          jars: ["{h2_server['jar'].as_posix()}"]
          properties: {{user: sa, password: "secret:warehouse_password"}}
    """)
    secrets = env.write("home/secrets.yaml", "warehouse_password: ''\n")
    secrets.chmod(0o600)
    return env


QUERY_FLOW = """
    params:
      day: {{type: date, expr: "today() - bdays(1)"}}
      day_key: {{type: int, expr: "int(format(day, '%Y%m%d'))"}}
      n: {{type: int, value: {n}}}
    blocks:
      q:
        type: db.run_query
        config:
          connection: warehouse
          chunk_size: 50000
          sql: |
            select x as id, 'row-' || x as label, cast(x as decimal(12, 2)) / 4 as amount,
                   :day as run_day, :day_key as day_key
            from system_range(1, :n)
"""


def _result(env) -> pq.ParquetFile:
    run_id, status = env.last_run()
    assert status == "success"
    return pq.ParquetFile(env.home / "runs" / str(run_id) / "q" / "attempt-1" / "result.parquet")


def test_query_binds_typed_parameters_and_keeps_column_types(jdbc_env):
    flow = jdbc_env.write("flow.yaml", QUERY_FLOW.format(n=3))

    result = jdbc_env.taskloom("run", flow, "--param", "day=2026-01-16")

    assert result.returncode == 0, result.stderr
    rows = _result(jdbc_env).read().to_pylist()
    assert rows[1] == {"ID": 2, "LABEL": "row-2", "AMOUNT": rows[1]["AMOUNT"],
                       "RUN_DAY": dt.date(2026, 1, 16), "DAY_KEY": 20260116}
    assert rows[1]["AMOUNT"] == decimal.Decimal("0.5")


@pytest.mark.skipif(sys.platform == "win32", reason="measures memory with the resource module")
def test_millions_of_rows_stream_to_parquet_without_growing_memory(jdbc_env):
    def peak_memory(n: int) -> int:
        flow = jdbc_env.write("flow.yaml", QUERY_FLOW.format(n=n))
        proc = subprocess.run([sys.executable, "-c", MAXRSS_SCRIPT, "run", str(flow)],
                              env=jdbc_env.env, cwd=jdbc_env.root, capture_output=True, text=True, timeout=600)
        assert proc.returncode == 0, proc.stderr
        parquet = _result(jdbc_env)
        assert parquet.metadata.num_rows == n
        assert parquet.metadata.num_row_groups == -(-n // 50_000)
        return int(proc.stdout.split("MAXRSS")[1])

    small, large = peak_memory(200_000), peak_memory(2_000_000)

    # 10x the rows; holding them in memory would add hundreds of MB.
    assert large < small * 1.25, (small, large)
