"""Check a packaged build works on this machine: python packaging/smoke.py dist/taskloom [--h2-jar PATH]

Runs the packaged programs only (not the source), in a temporary Taskloom folder.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

EXE = ".exe" if sys.platform == "win32" else ""


def check(title, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {title}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--h2-jar", type=Path, help="also run a JDBC query (needs Java)")
    args = parser.parse_args()
    folder = args.folder.resolve()
    cli, gui = folder / f"taskloom{EXE}", folder / f"taskloomw{EXE}"
    work = Path(tempfile.mkdtemp())
    env = {**os.environ, "TASKLOOM_HOME": str(work / "home"), "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}

    def run(*cmd, timeout=180):
        return subprocess.run([str(c) for c in cmd], env=env, cwd=work, capture_output=True, text=True, timeout=timeout)

    version = run(cli, "--version")
    check("version", version.returncode == 0 and (folder / "VERSION").read_text() in version.stdout, version.stderr)

    shutil.copytree(folder / "examples", work / "examples")
    result = run(cli, "run", work / "examples" / "daily-sales-report.yaml", "--param", "day=2026-10-02")
    out = work / "examples" / "out" / "sales_2026-10-02.csv"
    check("example flow (CSV, DuckDB, parameters)", result.returncode == 0 and "753.73" in out.read_text(), result.stderr)

    report = work / "report.yaml"
    report.write_text(textwrap.dedent("""
        blocks:
          sales:
            type: data.read_file
            config: {path: examples/data/sales.csv}
          totals:
            type: data.duckdb_sql
            config: {inputs: [sales], sql: "select region, sum(amount) as total from sales group by region order by region"}
          chart:
            type: reports.chart
            config: {kind: bar, x: region, y: [total]}
          excel:
            type: reports.excel
            config: {path: out/report.xlsx, sheets: [totals], chart: bar, chart_x: region, chart_y: [total]}
          body:
            type: reports.html_template
            config: {inputs: [totals, chart], template: "<h2>Sales</h2>{{ image(chart) }}{{ table(totals) }}"}
          save:
            type: logic.python
            config: {inputs: [html], outputs: [], code: "open('out/body.html', 'w').write(html.html)"}
        edges:
          - sales.table -> totals.sales
          - totals.result -> chart.table
          - totals.result -> excel.totals
          - totals.result -> body.totals
          - chart.image -> body.chart
          - body.html -> save.html
    """))
    result = run(cli, "run", report)
    check("report flow (chart, Excel, HTML template, Python)",
          result.returncode == 0 and (work / "out" / "report.xlsx").exists() and "cid:" in (work / "out" / "body.html").read_text(),
          result.stderr)

    if args.h2_jar:
        java_home = os.environ.get("JAVA_HOME", "")
        (work / "home").mkdir(exist_ok=True)
        (work / "home" / "settings.yaml").write_text(f"java_home: '{Path(java_home).as_posix()}'\n")
        (work / "home" / "connections.yaml").write_text(
            f"h2: {{kind: jdbc, driver: org.h2.Driver, url: 'jdbc:h2:mem:smoke', jars: ['{args.h2_jar.resolve().as_posix()}'],"
            " properties: {user: sa, password: ''}}\n")
        query = work / "query.yaml"
        query.write_text(textwrap.dedent("""
            blocks:
              q:
                type: db.run_query
                config: {connection: h2, sql: "select x, x * 2 as y from system_range(1, 1000)"}
              count:
                type: logic.python
                config: {inputs: [t], outputs: [], code: "assert len(t) == 1000"}
            edges:
              - q.result -> count.t
        """))
        result = run(cli, "run", query)
        check("JDBC query through the bundled Java bridge", result.returncode == 0, result.stdout + result.stderr)

    scheduler = subprocess.Popen([str(cli), "scheduler", "--headless"], env=env, cwd=work,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    heartbeat = work / "home" / "scheduler.heartbeat"
    deadline = time.monotonic() + 60
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.5)
    scheduler.terminate()
    scheduler.wait(timeout=30)
    check("headless scheduler starts", heartbeat.exists())

    if gui.exists():
        result = subprocess.run([str(gui), "editor", str(work / "examples" / "daily-sales-report.yaml")],
                                env={**env, "TASKLOOM_EDITOR_SMOKE": "1", "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")},
                                cwd=work, timeout=180)
        check("editor runs a flow through the runner program", result.returncode == 0, f"exit code {result.returncode}")
    shutil.rmtree(work, ignore_errors=True)
    print("all checks passed")


if __name__ == "__main__":
    main()
