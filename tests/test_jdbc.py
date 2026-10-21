"""JDBC queries against a real H2 database server (the h2_server and jdbc_env fixtures in conftest.py)."""

import datetime as dt
import decimal
import subprocess
import sys

import pyarrow.parquet as pq
import pytest

MAXRSS_SCRIPT = (  # like the taskloom program (cli.run_cli): exit right away once the Java bridge has run
    "import os, resource, sys; from taskloom.cli import main; rc = main(sys.argv[1:]); "
    "print('MAXRSS', resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, flush=True); os._exit(rc)"
)


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
