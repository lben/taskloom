"""Data blocks through their real paths: Read File, Write File, DuckDB SQL, Polars Transform, the tables
passed between blocks, and what the editor's Data preview shows of a table.

Flows are written into their own folder (flows/) and run from another one, so every relative path
in them is relative to the flow file, as the runner promises."""

import csv
import datetime as dt
import decimal
import io
import json
import textwrap
import zipfile
from pathlib import Path

import polars as pl
import xlsxwriter
from PySide6.QtCore import Qt

from test_editor import wait_done, write_flow


# --- helpers ----------------------------------------------------------------------------------

def put(env, relpath, content):
    """A file next to the flow, exactly as given (text or bytes)."""
    path = env.root / "flows" / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="")
    return path


def run(env, flow_text, *args):
    # UTF-8, as the editor saves flows and the runner reads them (not the system's default encoding).
    flow = put(env, "flow.yaml", textwrap.dedent(flow_text))
    return env.taskloom("run", flow, *args)


def last_blocks(env) -> tuple[str, dict]:
    """The last run's status and {block: (status, error, summary)}."""
    run_id, status = env.last_run()
    rows = env.query("SELECT block, status, error, summary FROM blocks WHERE run_id=?", run_id)
    return status, {block: (s, error, summary) for block, s, error, summary in rows}


def out(env, relpath):
    return env.root / "flows" / relpath


# --- Read File ---------------------------------------------------------------------------------

def test_read_csv_into_sql_keeps_every_row_value_and_type(env):
    put(env, "data/sales.csv", "day,region,store_id,amount\n"
                               "2026-10-01,EU,1,215.19\n2026-10-01,US,2,470.68\n"
                               "2026-10-02,EU,1,74.09\n2026-10-02,EU,3,12\n")

    result = run(env, """
        blocks:
          sales:
            type: data.read_file
            config: {path: data/sales.csv}
          everything:
            type: data.duckdb_sql
            config: {inputs: [sales], sql: "select * from sales order by day, store_id"}
          save:
            type: data.write_file
            config: {path: out/all.parquet}
        edges:
          - sales.table -> everything.sales
          - everything.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    df = pl.read_parquet(out(env, "out/all.parquet"))
    assert df["store_id"].dtype.is_integer() and df["amount"].dtype == pl.Float64
    assert df.with_columns(pl.col("day").cast(pl.String)).rows() == [
        ("2026-10-01", "EU", 1, 215.19), ("2026-10-01", "US", 2, 470.68),
        ("2026-10-02", "EU", 1, 74.09), ("2026-10-02", "EU", 3, 12.0)]
    status, blocks = last_blocks(env)
    assert status == "success"
    assert "4 rows" in blocks["sales"][2] and "4 columns" in blocks["sales"][2]  # shown under the block


def test_read_file_tells_csv_parquet_and_excel_apart_by_extension(env):
    put(env, "in/a.csv", "k,v\na,1\n")
    put(env, "in/B.CSV", "k,v\nb,2\n")  # as saved by some Windows programs
    pl.DataFrame({"k": ["c"], "v": [3]}).write_parquet(put(env, "in/c.parquet", b""))
    pl.DataFrame({"k": ["d"], "v": [4]}).write_excel(put(env, "in/d.xlsx", b""))

    result = run(env, """
        blocks:
          a: {type: data.read_file, config: {path: in/a.csv}}
          b: {type: data.read_file, config: {path: in/B.CSV}}
          c: {type: data.read_file, config: {path: in/c.parquet}}
          d: {type: data.read_file, config: {path: in/d.xlsx}}
          together:
            type: data.duckdb_sql
            config:
              inputs: [a, b, c, d]
              sql: |
                select k, v from a union all select k, v from b
                union all select k, v from c union all select k, v from d
                order by k
          save: {type: data.write_file, config: {path: out.parquet}}
        edges:
          - a.table -> together.a
          - b.table -> together.b
          - c.table -> together.c
          - d.table -> together.d
          - together.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert pl.read_parquet(out(env, "out.parquet")).rows() == [("a", 1), ("b", 2), ("c", 3), ("d", 4)]


def test_read_file_format_option_reads_files_whatever_their_extension(env):
    put(env, "in/export.dat", "k,v\na,1\n")
    pl.DataFrame({"k": ["b"], "v": [2]}).write_parquet(put(env, "in/snapshot", b""))
    pl.DataFrame({"k": ["c"], "v": [3]}).write_excel(put(env, "in/book.bin", b""))

    result = run(env, """
        blocks:
          a: {type: data.read_file, config: {path: in/export.dat, format: csv}}
          b: {type: data.read_file, config: {path: in/snapshot, format: parquet}}
          c: {type: data.read_file, config: {path: in/book.bin, format: excel}}
          together:
            type: data.duckdb_sql
            config:
              inputs: [a, b, c]
              sql: "select k, v from a union all select k, v from b union all select k, v from c order by k"
          save: {type: data.write_file, config: {path: out.parquet}}
        edges:
          - a.table -> together.a
          - b.table -> together.b
          - c.table -> together.c
          - together.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert pl.read_parquet(out(env, "out.parquet")).rows() == [("a", 1), ("b", 2), ("c", 3)]


XLSX_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
XLSM_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"


def _xlsm(path, df):
    """A macro-enabled workbook as Excel saves one under .xlsm: its package says it is macro-enabled."""
    plain = io.BytesIO()
    df.write_excel(plain)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(plain) as source, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
        for item in source.infolist():
            data = source.read(item)
            if item.filename == "[Content_Types].xml":
                assert XLSX_MAIN.encode() in data
                data = data.replace(XLSX_MAIN.encode(), XLSM_MAIN.encode())
            book.writestr(item, data)


def test_read_and_write_file_take_txt_as_csv_and_xlsm_as_excel(env):
    put(env, "in/export.txt", "k,v\na,1\n")
    _xlsm(env.root / "flows" / "in" / "macros.xlsm", pl.DataFrame({"k": ["b"], "v": [2]}))

    result = run(env, """
        blocks:
          a: {type: data.read_file, config: {path: in/export.txt}}
          b: {type: data.read_file, config: {path: in/macros.xlsm}}
          together:
            type: data.duckdb_sql
            config: {inputs: [a, b], sql: "select k, v from a union all select k, v from b order by k"}
          save: {type: data.write_file, config: {path: out/both.txt}}
        edges:
          - a.table -> together.a
          - b.table -> together.b
          - together.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "out/both.txt").read_text() == "k,v\na,1\nb,2\n"


def test_read_file_that_cannot_tell_the_format_says_to_set_it(env):
    put(env, "in/data.tsv", "k\tv\na\t1\n")

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/data.tsv}}
    """)

    assert result.returncode == 1
    status, blocks = last_blocks(env)
    assert blocks["r"][0] == "failed" and "set format to csv, parquet or excel" in blocks["r"][1]


def test_read_csv_with_the_separator_you_set(env):
    put(env, "in/semicolons.csv", "k;v\na;1\nb;2\n")
    put(env, "in/tabs.tsv", "k\tv\nc\t3\n")
    put(env, "in/pipes.csv", "k|v\nd|4\n")

    result = run(env, r"""
        blocks:
          semicolon: {type: data.read_file, config: {path: in/semicolons.csv, separator: ";"}}
          tab: {type: data.read_file, config: {path: in/tabs.tsv, format: csv, separator: "\t"}}
          pipe: {type: data.read_file, config: {path: in/pipes.csv, separator: "|"}}
          together:
            type: data.duckdb_sql
            config:
              inputs: [semicolon, tab, pipe]
              sql: "select k, v from semicolon union all select k, v from tab union all select k, v from pipe order by k"
          save: {type: data.write_file, config: {path: out.parquet}}
        edges:
          - semicolon.table -> together.semicolon
          - tab.table -> together.tab
          - pipe.table -> together.pipe
          - together.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert pl.read_parquet(out(env, "out.parquet")).rows() == [("a", 1), ("b", 2), ("c", 3), ("d", 4)]


def test_read_csv_separator_of_several_characters_is_a_clear_error(env):
    put(env, "in/data.csv", "k::v\na::1\n")

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/data.csv, separator: "::"}}
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["r"][0] == "failed" and "separator" in blocks["r"][1]


def _workbook(path):
    """Two sheets: Summary (first) and 'Detail data' with dates and empty cells."""
    path.parent.mkdir(parents=True, exist_ok=True)
    book = xlsxwriter.Workbook(path)
    dates = book.add_format({"num_format": "yyyy-mm-dd"})
    summary = book.add_worksheet("Summary")
    summary.write_row(0, 0, ["k", "v"])
    summary.write_row(1, 0, ["total", 2])
    detail = book.add_worksheet("Detail data")
    detail.write_row(0, 0, ["day", "amount", "note"])
    detail.write_datetime(1, 0, dt.datetime(2026, 10, 1), dates)
    detail.write(1, 1, 1.5)
    detail.write(1, 2, "Zürich")
    detail.write_datetime(2, 0, dt.datetime(2026, 10, 2), dates)  # amount and note left empty
    book.close()


def test_read_excel_reads_the_first_sheet_or_the_sheet_you_name(env):
    _workbook(env.root / "flows" / "in" / "book.xlsx")

    result = run(env, """
        blocks:
          first: {type: data.read_file, config: {path: in/book.xlsx}}
          detail: {type: data.read_file, config: {path: in/book.xlsx, sheet: Detail data}}
          save_first: {type: data.write_file, config: {path: first.parquet}}
          save_detail: {type: data.write_file, config: {path: detail.parquet}}
        edges:
          - first.table -> save_first.table
          - detail.table -> save_detail.table
    """)

    assert result.returncode == 0, result.stderr
    assert pl.read_parquet(out(env, "first.parquet")).rows() == [("total", 2)]
    detail = pl.read_parquet(out(env, "detail.parquet"))
    assert detail["day"].dtype == pl.Date
    assert detail.rows() == [(dt.date(2026, 10, 1), 1.5, "Zürich"), (dt.date(2026, 10, 2), None, None)]


def test_read_excel_sheet_that_does_not_exist_is_a_clear_error(env):
    _workbook(env.root / "flows" / "in" / "book.xlsx")

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/book.xlsx, sheet: Totals}}
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["r"][0] == "failed" and "Totals" in blocks["r"][1]


def test_read_file_path_from_parameters_and_a_missing_file_goes_to_the_error_output(env):
    put(env, "in/sales_20261001.csv", "region,amount\nEU,10\nUS,5\n")
    flow = """
        params:
          day: {type: date, value: 2026-10-01}
        blocks:
          sales: {type: data.read_file, config: {path: "in/sales_{day:%Y%m%d}.csv"}}
          count:
            type: data.duckdb_sql
            config: {inputs: [sales], sql: "select count(*) as n from sales"}
          save: {type: data.write_file, config: {path: "out/count_{day:%Y%m%d}.csv"}}
          missing:
            type: logic.python
            config: {inputs: [err], outputs: [], code: "open('missing.txt', 'w').write(err['error'])"}
        edges:
          - sales.table -> count.sales
          - count.result -> save.table
          - sales.error -> missing.err
    """

    assert run(env, flow).returncode == 0
    assert out(env, "out/count_20261001.csv").read_text() == "n\n2\n"

    result = run(env, flow, "--param", "day=2026-10-02")

    assert result.returncode == 0, result.stderr  # the failure was handled
    status, blocks = last_blocks(env)
    assert {b: s for b, (s, _, _) in blocks.items()} == {
        "sales": "failed", "count": "skipped", "save": "skipped", "missing": "success"}
    assert "in/sales_20261002.csv" in blocks["sales"][1]  # which day's file is missing
    assert "in/sales_20261002.csv" in out(env, "missing.txt").read_text()
    assert not out(env, "out/count_20261002.csv").exists()


def test_read_file_missing_with_nothing_handling_it_fails_the_run(env):
    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/nowhere.csv}}
    """)

    assert result.returncode == 1
    status, blocks = last_blocks(env)
    assert status == "failed" and "in/nowhere.csv" in blocks["r"][1]


def test_read_csv_with_only_a_header_gives_an_empty_table(env):
    put(env, "in/empty.csv", "day,amount\n")

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/empty.csv}}
          count:
            type: data.duckdb_sql
            config: {inputs: [r], sql: "select count(*) as n, count(amount) as with_amount from r"}
          save_count: {type: data.write_file, config: {path: count.csv}}
          save_rows: {type: data.write_file, config: {path: rows.csv}}
        edges:
          - r.table -> count.r
          - count.result -> save_count.table
          - r.table -> save_rows.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "count.csv").read_text() == "n,with_amount\n0,0\n"
    assert out(env, "rows.csv").read_text() == "day,amount\n"
    _, blocks = last_blocks(env)
    assert "0 rows" in blocks["r"][2]


def test_read_csv_that_is_an_empty_file_is_a_clear_error(env):
    put(env, "in/nothing.csv", "")

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/nothing.csv}}
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["r"][0] == "failed" and "empty" in blocks["r"][1].lower()


def test_read_csv_with_bom_windows_line_ends_quotes_and_accents_in_names_and_path(env):
    # As Excel saves "CSV UTF-8" on Windows: a byte order mark and CRLF line ends.
    put(env, "données 2026/ventes été (copie).csv",
        "﻿Région,Montant €,Note\r\n"
        "Zürich,10.5,\"Lake, north side\"\r\n"
        "Genève,2,\"He said \"\"hi\"\"\"\r\n".encode("utf-8"))
    put(env, "données 2026/lines.csv", "id,text\n1,\"first line\nsecond line\"\n2,plain\n")

    result = run(env, """
        blocks:
          ventes: {type: data.read_file, config: {path: "données 2026/ventes été (copie).csv"}}
          lines: {type: data.read_file, config: {path: "données 2026/lines.csv"}}
          totals:
            type: data.duckdb_sql
            config: {inputs: [ventes], sql: 'select "Région", sum("Montant €") as total from ventes group by 1 order by 1'}
          save_ventes: {type: data.write_file, config: {path: "sorties/ventes été.parquet"}}
          save_totals: {type: data.write_file, config: {path: "sorties/résumé.csv"}}
          save_lines: {type: data.write_file, config: {path: "sorties/lines.parquet"}}
        edges:
          - ventes.table -> totals.ventes
          - ventes.table -> save_ventes.table
          - totals.result -> save_totals.table
          - lines.table -> save_lines.table
    """)

    assert result.returncode == 0, result.stderr
    ventes = pl.read_parquet(out(env, "sorties/ventes été.parquet"))
    assert ventes.columns == ["Région", "Montant €", "Note"]
    assert ventes.rows() == [("Zürich", 10.5, "Lake, north side"), ("Genève", 2.0, 'He said "hi"')]
    assert out(env, "sorties/résumé.csv").read_text(encoding="utf-8") == "Région,total\nGenève,2.0\nZürich,10.5\n"
    assert pl.read_parquet(out(env, "sorties/lines.parquet")).rows() == [(1, "first line\nsecond line"), (2, "plain")]


def test_read_csv_empty_fields_are_nulls_and_are_written_back_empty(env):
    put(env, "in/notes.csv", "id,amount,note\n1,5,a\n2,,\n3,7,\n")

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/notes.csv}}
          counts:
            type: data.duckdb_sql
            config:
              inputs: [r]
              sql: "select count(*) as n, count(amount) as amounts, count(note) as notes, max(amount) as top from r"
          save_counts: {type: data.write_file, config: {path: counts.csv}}
          save_rows: {type: data.write_file, config: {path: rows.csv}}
        edges:
          - r.table -> counts.r
          - counts.result -> save_counts.table
          - r.table -> save_rows.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "counts.csv").read_text() == "n,amounts,notes,top\n3,2,1,7\n"
    assert out(env, "rows.csv").read_text() == "id,amount,note\n1,5,a\n2,,\n3,7,\n"


def test_read_csv_not_in_utf8_is_a_clear_error(env):
    # The encoding Excel's plain "CSV" uses on many Windows computers.
    put(env, "in/latin.csv", "name,city\nJosé,München\n".encode("cp1252"))

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/latin.csv}}
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["r"][0] == "failed" and "utf-8" in blocks["r"][1].lower()


def test_read_files_whose_names_have_square_brackets(env):
    put(env, "in/sales [EU].csv", "region,amount\nEU,10\n")
    pl.DataFrame({"region": ["US"], "amount": [5]}).write_parquet(put(env, "in/sales [US].parquet", b""))

    result = run(env, """
        blocks:
          eu: {type: data.read_file, config: {path: "in/sales [EU].csv"}}
          us: {type: data.read_file, config: {path: "in/sales [US].parquet"}}
          together:
            type: data.duckdb_sql
            config: {inputs: [eu, us], sql: "select * from eu union all select * from us order by region"}
          save: {type: data.write_file, config: {path: out.parquet}}
        edges:
          - eu.table -> together.eu
          - us.table -> together.us
          - together.result -> save.table
    """)

    _, blocks = last_blocks(env)
    assert result.returncode == 0, blocks
    assert pl.read_parquet(out(env, "out.parquet")).rows() == [("EU", 10), ("US", 5)]


def test_read_a_million_row_csv_and_transform_it_without_losing_rows(env):
    n = 1_000_000
    big = pl.DataFrame({"id": range(n)}).with_columns(group=pl.col("id") % 10, amount=pl.col("id") % 100)
    big.write_csv(put(env, "in/big.csv", b""))

    result = run(env, """
        blocks:
          big: {type: data.read_file, config: {path: in/big.csv}}
          by_group:
            type: data.polars
            config: {code: "df.group_by('group').agg(pl.len().alias('rows'), pl.col('amount').sum()).sort('group')"}
          save: {type: data.write_file, config: {path: by_group.parquet}}
        edges:
          - big.table -> by_group.table
          - by_group.table -> save.table
    """)

    assert result.returncode == 0, result.stderr
    expected = big.group_by("group").agg(pl.len().alias("rows"), pl.col("amount").sum()).sort("group")
    got = pl.read_parquet(out(env, "by_group.parquet"))
    assert got.rows() == expected.rows() and got["rows"].sum() == n
    _, blocks = last_blocks(env)
    assert "1,000,000 rows" in blocks["big"][2]


def test_read_csv_whose_numbers_turn_to_text_late_in_the_file_keeps_every_value(env):
    # Codes that look like numbers for the first 12,000 rows, then one that does not.
    lines = ["code,amount"] + [f"{i},1" for i in range(12_000)] + ["A-17,1"]
    put(env, "in/codes.csv", "\n".join(lines) + "\n")

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/codes.csv}}
          save: {type: data.write_file, config: {path: codes.csv}}
        edges:
          - r.table -> save.table
    """)

    _, blocks = last_blocks(env)
    assert result.returncode == 0, blocks.get("r")
    assert out(env, "codes.csv").read_text().splitlines() == lines


def test_read_excel_column_with_text_after_many_numbers_does_not_lose_it(env):
    path = env.root / "flows" / "in" / "codes.xlsx"
    path.parent.mkdir(parents=True)
    book = xlsxwriter.Workbook(path)
    sheet = book.add_worksheet()
    sheet.write_row(0, 0, ["code", "amount"])
    for row in range(1, 1500):
        sheet.write_row(row, 0, [row, 1])
    sheet.write_row(1500, 0, ["A-17", 1])
    book.close()

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/codes.xlsx}}
          save: {type: data.write_file, config: {path: codes.parquet}}
        edges:
          - r.table -> save.table
    """)

    # Reading it, or a clear failure; never a table where the code silently became empty.
    if result.returncode == 0:
        codes = pl.read_parquet(out(env, "codes.parquet"))["code"]
        assert codes.null_count() == 0 and "A-17" in codes.cast(pl.String).to_list()
    else:
        _, blocks = last_blocks(env)
        assert blocks["r"][0] == "failed" and "code" in blocks["r"][1]


def test_data_block_config_mistakes_are_reported_before_anything_runs(env):
    flow = env.write("flows/flow.yaml", """
        blocks:
          a: {type: data.read_file, config: {path: in/a.xlsx, format: xlsx}}
          b: {type: data.read_file, config: {}}
          c: {type: data.read_file, config: {path: in/c.csv, delimiter: ";"}}
          d: {type: data.duckdb_sql, config: {inputs: []}}
          e: {type: data.polars}
          f: {type: data.write_file, config: {path: out.json, format: json}}
          g: {type: data.duckdb_sql, config: {inputs: [], sql: "select 1 as x"}}
        edges:
          - g.result -> e.table
          - g.result -> f.table
    """)

    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert result.returncode == 2
        errors = result.stderr
        assert "blocks.a: config.format: expected one of" in errors and "'excel'" in errors and "'xlsx'" in errors
        assert "blocks.b: config.path is required" in errors
        assert "blocks.c: unknown config key(s) ['delimiter']" in errors
        assert "blocks.d: config.sql is required" in errors
        assert "blocks.e: config.code is required" in errors
        assert "blocks.f: config.format: expected one of" in errors
    assert not (env.home / "history.db").exists()  # nothing ran


# --- Write File --------------------------------------------------------------------------------

def test_write_csv_to_a_dated_path_in_new_folders_and_pass_the_file_on(env):
    result = run(env, """
        params:
          day: {type: date, expr: "today()"}
        blocks:
          totals:
            type: data.duckdb_sql
            config:
              inputs: []
              sql: "select :day as day, 'EU' as region, 3 as stores, 753.73 as total, null::varchar as note"
          save: {type: data.write_file, config: {path: "reports/{day:%Y}/sales_{day:%Y-%m-%d}.csv"}}
          use:
            type: logic.python
            config: {inputs: [file], outputs: [], code: "open('written.txt', 'w').write(file)"}
        edges:
          - totals.result -> save.table
          - save.file -> use.file
    """, "--param", "day=2026-10-02")

    assert result.returncode == 0, result.stderr
    written = out(env, "reports/2026/sales_2026-10-02.csv")
    assert written.read_text() == "day,region,stores,total,note\n2026-10-02,EU,3,753.73,\n"
    # The file output names the file written, so the next block can find it.
    assert (out(env, "") / out(env, "written.txt").read_text()).resolve() == written.resolve()


def _typed_table_sql():
    return ("select date '2026-10-02' as day, timestamp '2026-10-02 08:30:00' as at, 42 as n, 2.5::double as x, "
            "'Zürich' as city, true as ok, null::integer as missing, 753.73 as total")


def test_write_parquet_keeps_every_type_date_and_null(env):
    result = run(env, f"""
        blocks:
          q: {{type: data.duckdb_sql, config: {{inputs: [], sql: "{_typed_table_sql()}"}}}}
          save: {{type: data.write_file, config: {{path: out/typed.parquet}}}}
        edges:
          - q.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    df = pl.read_parquet(out(env, "out/typed.parquet"))
    assert df.schema["day"] == pl.Date and df.schema["at"] == pl.Datetime("us") and df.schema["ok"] == pl.Boolean
    assert df.rows() == [(dt.date(2026, 10, 2), dt.datetime(2026, 10, 2, 8, 30), 42, 2.5, "Zürich", True, None,
                          decimal.Decimal("753.73"))]


def test_write_excel_is_readable_with_its_dates_and_empty_cells(env):
    result = run(env, f"""
        blocks:
          q: {{type: data.duckdb_sql, config: {{inputs: [], sql: "{_typed_table_sql()}"}}}}
          save: {{type: data.write_file, config: {{path: out/typed.xlsx}}}}
        edges:
          - q.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    df = pl.read_excel(out(env, "out/typed.xlsx"))
    assert df.columns == ["day", "at", "n", "x", "city", "ok", "missing", "total"]
    row = df.row(0, named=True)
    assert row["day"] == dt.date(2026, 10, 2) and row["at"] == dt.datetime(2026, 10, 2, 8, 30)
    assert (row["n"], row["x"], row["city"], row["ok"], row["missing"]) == (42, 2.5, "Zürich", True, None)
    assert float(row["total"]) == 753.73


AT_SQL = "timestamptz '2026-10-02 08:30:00+00' as at"  # a TIMESTAMP WITH TIME ZONE, as now() gives
AT = dt.datetime(2026, 10, 2, 8, 30, tzinfo=dt.timezone.utc)


def _is_at(value) -> bool:
    """value is the instant AT: with its time zone, or as a clock time in UTC or in this computer's zone."""
    if value.tzinfo is not None:
        return value == AT
    return value in (AT.replace(tzinfo=None), AT.astimezone().replace(tzinfo=None))


def test_write_excel_of_timestamps_with_a_time_zone(env):
    # A run timestamp (now()) or a database's TIMESTAMP WITH TIME ZONE column, saved as a report.
    result = run(env, f"""
        blocks:
          q: {{type: data.duckdb_sql, config: {{inputs: [], sql: "select {AT_SQL}, now() as generated_at, 1 as n"}}}}
          save: {{type: data.write_file, config: {{path: out/report.xlsx}}}}
        edges:
          - q.result -> save.table
    """)

    _, blocks = last_blocks(env)
    assert result.returncode == 0, blocks["save"]
    row = pl.read_excel(out(env, "out/report.xlsx")).row(0, named=True)
    assert _is_at(row["at"]) and row["n"] == 1
    assert abs(row["generated_at"] - dt.datetime.now()) < dt.timedelta(days=1)


def test_write_csv_and_parquet_of_a_timestamp_with_a_time_zone_keep_the_instant(env):
    result = run(env, f"""
        blocks:
          q: {{type: data.duckdb_sql, config: {{inputs: [], sql: "select {AT_SQL}"}}}}
          csv: {{type: data.write_file, config: {{path: out/at.csv}}}}
          parquet: {{type: data.write_file, config: {{path: out/at.parquet}}}}
        edges:
          - q.result -> csv.table
          - q.result -> parquet.table
    """)

    assert result.returncode == 0, result.stderr
    assert pl.read_parquet(out(env, "out/at.parquet"))["at"].to_list() == [AT]  # still with its zone
    header, value = out(env, "out/at.csv").read_text().splitlines()
    assert header == "at" and _is_at(dt.datetime.fromisoformat(value))


def test_write_csv_quotes_values_with_the_separator_quotes_or_line_breaks(env):
    notes = ["Lake, north side", 'He said "hi"', "first line\nsecond line", "plain"]
    pl.DataFrame({"id": [1, 2, 3, 4], "Note, free text": notes}).write_parquet(put(env, "in/notes.parquet", b""))

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: in/notes.parquet}}
          save: {type: data.write_file, config: {path: out/notes.csv}}
          reread: {type: data.read_file, config: {path: out/notes.csv}}
          save_again: {type: data.write_file, config: {path: out/notes_again.parquet}}
        edges:
          - r.table -> save.table
          - save.done -> reread.after
          - reread.table -> save_again.table
    """)

    assert result.returncode == 0, result.stderr
    with open(out(env, "out/notes.csv"), newline="", encoding="utf-8") as f:
        assert list(csv.reader(f)) == [["id", "Note, free text"]] + [[str(i), n] for i, n in enumerate(notes, 1)]
    again = pl.read_parquet(out(env, "out/notes_again.parquet"))
    assert again.columns == ["id", "Note, free text"] and again.rows() == list(enumerate(notes, 1))


def test_write_file_format_option_writes_whatever_the_extension(env):
    result = run(env, """
        blocks:
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select 'a' as k, 1 as v"}}
          as_csv: {type: data.write_file, config: {path: export.dat, format: csv}}
          as_parquet: {type: data.write_file, config: {path: snapshot, format: parquet}}
          as_excel: {type: data.write_file, config: {path: book.out, format: excel}}
        edges:
          - q.result -> as_csv.table
          - q.result -> as_parquet.table
          - q.result -> as_excel.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "export.dat").read_text() == "k,v\na,1\n"
    assert pl.read_parquet(out(env, "snapshot")).rows() == [("a", 1)]
    with zipfile.ZipFile(out(env, "book.out")) as book:  # an Excel workbook
        assert "xl/workbook.xml" in book.namelist()


def test_write_file_to_xlsm_gives_a_workbook_excel_opens_under_that_name(env):
    # Excel refuses a plain .xlsx package named .xlsm ("the file format or file extension is not valid").
    result = run(env, """
        blocks:
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select 'a' as k, 1 as v"}}
          save: {type: data.write_file, config: {path: out/report.xlsm}}
        edges:
          - q.result -> save.table
    """)

    _, blocks = last_blocks(env)
    if result.returncode == 0:
        with zipfile.ZipFile(out(env, "out/report.xlsm")) as book:
            types = book.read("[Content_Types].xml").decode()
        assert XLSM_MAIN in types, types
        assert pl.read_excel(out(env, "out/report.xlsm")).rows() == [("a", 1)]
    else:  # or it says it cannot write that kind of workbook
        assert blocks["save"][0] == "failed" and "xlsm" in blocks["save"][1]


def test_write_file_that_cannot_tell_the_format_says_to_set_it(env):
    result = run(env, """
        blocks:
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select 1 as v"}}
          save: {type: data.write_file, config: {path: out.tsv}}
        edges:
          - q.result -> save.table
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["save"][0] == "failed" and "set format to csv, parquet or excel" in blocks["save"][1]
    assert not out(env, "out.tsv").exists()


def test_write_file_replaces_the_file_of_an_earlier_run(env):
    flow = """
        params:
          n: {type: int, value: 3}
        blocks:
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select {n} as run, range as i from range({n})"}}
          csv: {type: data.write_file, config: {path: out/rows.csv}}
          parquet: {type: data.write_file, config: {path: out/rows.parquet}}
          excel: {type: data.write_file, config: {path: out/rows.xlsx}}
        edges:
          - q.result -> csv.table
          - q.result -> parquet.table
          - q.result -> excel.table
    """
    assert run(env, flow).returncode == 0
    assert pl.read_parquet(out(env, "out/rows.parquet")).height == 3

    result = run(env, flow, "--param", "n=1")

    assert result.returncode == 0, result.stderr
    assert out(env, "out/rows.csv").read_text() == "run,i\n1,0\n"
    assert pl.read_parquet(out(env, "out/rows.parquet")).rows() == [(1, 0)]
    assert pl.read_excel(out(env, "out/rows.xlsx")).rows() == [(1, 0)]


def test_write_file_back_over_the_file_that_was_read(env):
    put(env, "data.csv", "id,amount\n1,150\n2,20\n3,300\n4,5\n")

    result = run(env, """
        blocks:
          r: {type: data.read_file, config: {path: data.csv}}
          big: {type: data.polars, config: {code: "df.filter(pl.col('amount') > 100)"}}
          save: {type: data.write_file, config: {path: data.csv}}
        edges:
          - r.table -> big.table
          - big.table -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "data.csv").read_text() == "id,amount\n1,150\n3,300\n"


def test_write_an_empty_table_keeps_its_columns(env):
    result = run(env, """
        blocks:
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select 1 as id, 'x' as name where false"}}
          csv: {type: data.write_file, config: {path: empty.csv}}
          parquet: {type: data.write_file, config: {path: empty.parquet}}
          excel: {type: data.write_file, config: {path: empty.xlsx}}
        edges:
          - q.result -> csv.table
          - q.result -> parquet.table
          - q.result -> excel.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "empty.csv").read_text() == "id,name\n"
    parquet = pl.read_parquet(out(env, "empty.parquet"))
    assert parquet.height == 0 and parquet.columns == ["id", "name"]
    excel = pl.read_excel(out(env, "empty.xlsx"))
    assert excel.height == 0 and excel.columns == ["id", "name"]


def test_write_a_million_rows_to_csv_and_parquet(env):
    result = run(env, """
        blocks:
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select range as i, range % 7 as g from range(1000000)"}}
          csv: {type: data.write_file, config: {path: big.csv}}
          parquet: {type: data.write_file, config: {path: big.parquet}}
        edges:
          - q.result -> csv.table
          - q.result -> parquet.table
    """)

    assert result.returncode == 0, result.stderr
    from_csv = pl.read_csv(out(env, "big.csv"))
    assert from_csv.height == 1_000_000 and from_csv["i"].sum() == sum(range(1_000_000))
    assert pl.read_parquet(out(env, "big.parquet")).equals(from_csv.cast(pl.read_parquet_schema(out(env, "big.parquet"))))


def test_write_excel_refuses_more_rows_than_a_sheet_holds(env):
    result = run(env, """
        blocks:
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select range as i from range(1048576)"}}
          save: {type: data.write_file, config: {path: big.xlsx}}
        edges:
          - q.result -> save.table
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["save"][0] == "failed"
    assert "1,048,576 rows" in blocks["save"][1] and "Excel" in blocks["save"][1]
    assert not out(env, "big.xlsx").exists()


def test_write_file_given_something_that_is_not_a_table_is_a_clear_error(env):
    result = run(env, """
        blocks:
          make: {type: logic.python, config: {inputs: [], code: "output = {'rows': 3}"}}
          save: {type: data.write_file, config: {path: out.csv}}
        edges:
          - make.output -> save.table
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["save"][0] == "failed" and "expects a table, got dict" in blocks["save"][1]
    assert not out(env, "out.csv").exists()


def test_write_a_list_column_to_parquet_or_a_clear_error_for_csv(env):
    result = run(env, """
        blocks:
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select 'EU' as region, [1, 2] as stores"}}
          parquet: {type: data.write_file, config: {path: stores.parquet}}
          csv: {type: data.write_file, config: {path: stores.csv}}
        edges:
          - q.result -> parquet.table
          - q.result -> csv.table
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert pl.read_parquet(out(env, "stores.parquet")).rows() == [("EU", [1, 2])]
    assert blocks["csv"][0] == "failed" and "nested" in blocks["csv"][1]


# --- DuckDB SQL --------------------------------------------------------------------------------

def test_sql_input_is_called_input_unless_you_name_it(env):
    result = run(env, """
        blocks:
          src: {type: logic.python, config: {inputs: [], code: "output = pl.DataFrame({'x': [1, 2, 3]})"}}
          q: {type: data.duckdb_sql, config: {sql: "select count(*) as n, max(x) as top from input"}}
          save: {type: data.write_file, config: {path: out.csv}}
        edges:
          - src.output -> q.input
          - q.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "out.csv").read_text() == "n,top\n3,3\n"


def test_sql_sum_of_whole_numbers_stays_an_exact_whole_number(env):
    result = run(env, f"""
        blocks:
          src: {{type: logic.python, config: {{inputs: [], code: "output = pl.DataFrame({{'x': [{2**53}, 1]}})"}}}}
          q: {{type: data.duckdb_sql, config: {{sql: "select sum(x) as total from input"}}}}
          save: {{type: data.write_file, config: {{path: out.csv}}}}
        edges:
          - src.output -> q.input
          - q.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "out.csv").read_text() == f"total\n{2**53 + 1}\n"


def test_sql_joins_and_groups_several_input_tables(env):
    put(env, "orders.csv", "id,customer_id,amount\n1,10,5.5\n2,10,4.5\n3,20,7\n4,30,1\n")
    put(env, "customers.csv", "id,name,region_id\n10,Ann,1\n20,Bo,2\n30,Cy,1\n")

    result = run(env, """
        blocks:
          orders: {type: data.read_file, config: {path: orders.csv}}
          customers: {type: data.read_file, config: {path: customers.csv}}
          regions:
            type: logic.python
            config: {inputs: [], code: "output = pl.DataFrame({'id': [1, 2], 'name': ['North', 'South']})"}
          totals:
            type: data.duckdb_sql
            config:
              inputs: [orders, customers, regions]
              sql: |
                select r.name as region, count(*) as orders, sum(o.amount) as total
                from orders o
                join customers c on c.id = o.customer_id
                join regions r on r.id = c.region_id
                group by r.name
                order by r.name
          joined:
            type: data.duckdb_sql
            config:
              inputs: [orders, customers]
              sql: "select * from orders o join customers c on c.id = o.customer_id order by o.id"
          save_totals: {type: data.write_file, config: {path: totals.csv}}
          save_joined: {type: data.write_file, config: {path: joined.parquet}}
        edges:
          - orders.table -> totals.orders
          - customers.table -> totals.customers
          - regions.output -> totals.regions
          - orders.table -> joined.orders
          - customers.table -> joined.customers
          - totals.result -> save_totals.table
          - joined.result -> save_joined.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "totals.csv").read_text() == "region,orders,total\nNorth,3,11.0\nSouth,1,7.0\n"
    # Both tables have an id column: neither one is lost.
    joined = pl.read_parquet(out(env, "joined.parquet"))
    assert joined.width == 6 and joined.row(0) == (1, 10, 5.5, 10, "Ann", 1)


def test_sql_bind_parameters_keep_their_types_and_text_templates_fill_text(env):
    result = run(env, """
        params:
          name: {type: text, value: "O'Brien; drop table x --"}
          n: {type: int, value: 7}
          rate: {type: float, value: 0.5}
          flag: {type: bool, value: true}
          day: {type: date, value: 2026-10-02}
          at: {type: datetime, value: "2026-10-02 08:30:00"}
        blocks:
          q:
            type: data.duckdb_sql
            config:
              inputs: []
              sql: |
                select :name as name, :n as n, :n * 2 as twice, :rate as rate, :flag as flag,
                       :day as day, :day + 1 as next_day, :at as at, '{day:%Y-%m}' as month
          save: {type: data.write_file, config: {path: out.parquet}}
        edges:
          - q.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    df = pl.read_parquet(out(env, "out.parquet"))
    assert df.schema["n"].is_integer() and df.schema["rate"] == pl.Float64 and df.schema["flag"] == pl.Boolean
    assert df.schema["day"] == pl.Date and isinstance(df.schema["at"], pl.Datetime)
    assert df.row(0, named=True) == {
        "name": "O'Brien; drop table x --", "n": 7, "twice": 14, "rate": 0.5, "flag": True,
        "day": dt.date(2026, 10, 2), "next_day": dt.date(2026, 10, 3), "at": dt.datetime(2026, 10, 2, 8, 30),
        "month": "2026-10"}


def test_sql_list_parameter_binds_as_its_items_in_an_in_list(env):
    flow = env.write("flows/flow.yaml", """
        params:
          regions: {type: list, value: [EU, US]}
        blocks:
          q:
            type: data.duckdb_sql
            config:
              inputs: []
              sql: |
                select region from (values ('EU'), ('US'), ('JP')) t(region)
                where region in (:regions) order by region
          save: {type: data.write_file, config: {path: out.parquet}}
        edges:
          - q.result -> save.table
    """)

    declared = env.taskloom("run", flow)
    assert declared.returncode == 0, declared.stderr
    assert pl.read_parquet(out(env, "out.parquet"))["region"].to_list() == ["EU", "US"]

    overridden = env.taskloom("run", flow, "--param", "regions=JP")
    assert overridden.returncode == 0, overridden.stderr
    assert pl.read_parquet(out(env, "out.parquet"))["region"].to_list() == ["JP"]


def test_sql_list_parameter_between_other_parameters_and_used_twice_binds_each_in_its_place(env):
    result = run(env, """
        params:
          low: {type: int, value: 2}
          regions: {type: list, value: [EU, JP]}
          high: {type: int, value: 5}
          ids: {type: list, value: [3, 4, 6]}
        blocks:
          q:
            type: data.duckdb_sql
            config:
              inputs: []
              sql: |
                select region, n from (values ('EU', 1), ('EU', 3), ('US', 3), ('JP', 4), ('JP', 6)) t(region, n)
                where n >= :low and region in (:regions) and n <= :high
                  and n in (:ids) and region in (:regions)
                order by n
          save: {type: data.write_file, config: {path: out.parquet}}
        edges:
          - q.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert pl.read_parquet(out(env, "out.parquet")).rows() == [("EU", 3), ("JP", 4)]


def test_sql_list_parameter_given_no_items_is_a_clear_error(env):
    flow = env.write("flows/flow.yaml", """
        params:
          regions: {type: list, value: [EU]}
        blocks:
          q:
            type: data.duckdb_sql
            config:
              inputs: []
              sql: "select region from (values ('EU')) t(region) where region in (:regions)"
    """)

    result = env.taskloom("run", flow, "--param", "regions=")

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["q"][0] == "failed" and ":regions" in blocks["q"][1], blocks["q"][1]


def test_sql_colons_in_text_casts_and_comments_are_not_parameters(env):
    result = run(env, """
        blocks:
          src: {type: logic.python, config: {inputs: [], code: "output = pl.DataFrame({'x': [1]})"}}
          q:
            type: data.duckdb_sql
            config:
              sql: |
                select '10:30' as t, x::double as d, x as "a:b"  -- :not_a_parameter
                from input /* :nor_this */ ;
          save: {type: data.write_file, config: {path: out.parquet}}
        edges:
          - src.output -> q.input
          - q.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert pl.read_parquet(out(env, "out.parquet")).row(0, named=True) == {"t": "10:30", "d": 1.0, "a:b": 1}


def test_sql_unknown_bind_parameter_is_reported_before_running(env):
    flow = env.write("flows/flow.yaml", """
        params:
          region: {type: text, value: EU}
        blocks:
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select 1 as x where 'EU' = :regoin"}}
    """)

    result = env.taskloom("validate", flow)

    assert result.returncode == 2 and "unknown bind parameter :regoin" in result.stderr


def test_sql_wrong_column_table_or_syntax_fails_with_a_clear_error(env):
    put(env, "sales.csv", "region,amount\nEU,1\n")

    result = run(env, """
        blocks:
          sales: {type: data.read_file, config: {path: sales.csv}}
          wrong_column: {type: data.duckdb_sql, config: {inputs: [sales], sql: "select sum(amout) from sales"}}
          wrong_table: {type: data.duckdb_sql, config: {inputs: [sales], sql: "select * from sale"}}
          bad_syntax: {type: data.duckdb_sql, config: {inputs: [sales], sql: "selec * from sales"}}
        edges:
          - sales.table -> wrong_column.sales
          - sales.table -> wrong_table.sales
          - sales.table -> bad_syntax.sales
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    column, table, syntax = blocks["wrong_column"], blocks["wrong_table"], blocks["bad_syntax"]
    assert column[0] == "failed" and "amout" in column[1] and "amount" in column[1]  # names it, offers the right one
    assert table[0] == "failed" and "sale" in table[1]
    assert syntax[0] == "failed" and "syntax error" in syntax[1].lower()


def test_sql_statement_that_gives_no_table_is_a_clear_error(env):
    result = run(env, """
        blocks:
          src: {type: logic.python, config: {inputs: [], code: "output = pl.DataFrame({'x': [1]})"}}
          q: {type: data.duckdb_sql, config: {sql: "create table copy_of_input as select * from input"}}
        edges:
          - src.output -> q.input
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["q"][0] == "failed"  # never a success with no result table
    assert "NoneType" not in blocks["q"][1], blocks["q"][1]  # a clear message, not an internal AttributeError


def test_sql_over_an_empty_table(env):
    result = run(env, """
        blocks:
          src: {type: data.duckdb_sql, config: {inputs: [], sql: "select 1 as id, 2.5 as amount where false"}}
          totals:
            type: data.duckdb_sql
            config: {inputs: [t], sql: "select count(*) as n, sum(amount) as total from t"}
          positive: {type: data.duckdb_sql, config: {inputs: [t], sql: "select * from t where amount > 0"}}
          save_totals: {type: data.write_file, config: {path: totals.csv}}
          save_positive: {type: data.write_file, config: {path: positive.csv}}
        edges:
          - src.result -> totals.t
          - src.result -> positive.t
          - totals.result -> save_totals.table
          - positive.result -> save_positive.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "totals.csv").read_text() == "n,total\n0,\n"
    assert out(env, "positive.csv").read_text() == "id,amount\n"


def test_sql_input_names_must_be_names_and_be_connected(env):
    flow = env.write("flows/flow.yaml", """
        blocks:
          a: {type: data.duckdb_sql, config: {inputs: [after], sql: "select 1"}}
          b: {type: data.duckdb_sql, config: {inputs: [my table], sql: "select 1"}}
          c: {type: data.duckdb_sql, config: {inputs: [x, x], sql: "select 1"}}
          d: {type: data.duckdb_sql, config: {inputs: [orders], sql: "select * from orders"}}
    """)

    result = env.taskloom("validate", flow)

    assert result.returncode == 2
    assert "blocks.a: config.inputs: after: every block already has these" in result.stderr
    assert "blocks.b: config.inputs: expected a list of names" in result.stderr
    assert "blocks.c: config.inputs: duplicate names" in result.stderr
    assert "blocks.d: required input 'orders' is not connected" in result.stderr


def test_sql_sorts_more_data_than_the_memory_it_may_use(env):
    # "Works even on data bigger than your memory": here, bigger than the memory DuckDB is allowed.
    result = run(env, """
        blocks:
          big:
            type: data.duckdb_sql
            config: {inputs: [], sql: "select range as id, md5(range::varchar) as h from range(3000000)"}
          sorted:
            type: data.duckdb_sql
            config:
              inputs: [big]
              sql: "set memory_limit = '150MB'; set threads = 2; select * from big order by h"
          save: {type: data.write_file, config: {path: sorted.parquet}}
        edges:
          - big.result -> sorted.big
          - sorted.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    h = pl.read_parquet(out(env, "sorted.parquet"), columns=["h"])["h"]
    assert h.len() == 3_000_000 and h.is_sorted()


# --- Polars Transform ----------------------------------------------------------------------------

AMOUNTS = "output = pl.DataFrame({'id': [1, 2, 3, 4], 'amount': [5.0, -1.0, 0.0, 2.5]})"


def test_polars_help_example_filters_rows(env):
    result = run(env, f"""
        blocks:
          src: {{type: logic.python, config: {{inputs: [], code: "{AMOUNTS}"}}}}
          t: {{type: data.polars, config: {{code: 'df.filter(pl.col("amount") > 0)'}}}}
          save: {{type: data.write_file, config: {{path: out.csv}}}}
        edges:
          - src.output -> t.table
          - t.table -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "out.csv").read_text() == "id,amount\n1,5.0\n4,2.5\n"


def test_polars_several_lines_with_params_and_comments(env):
    result = run(env, f"""
        params:
          factor: {{type: float, value: 2}}
          day: {{type: date, value: 2026-10-02}}
        blocks:
          src: {{type: logic.python, config: {{inputs: [], code: "{AMOUNTS}"}}}}
          t:
            type: data.polars
            config:
              code: |
                df.with_columns(
                    doubled=pl.col("amount") * params["factor"],  # from the flow's parameters
                    day=pl.lit(params["day"]),
                )
                .filter(pl.col("id") <= 2)
                # the last line is a comment
          save: {{type: data.write_file, config: {{path: out.parquet}}}}
        edges:
          - src.output -> t.table
          - t.table -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert pl.read_parquet(out(env, "out.parquet")).rows() == [
        (1, 5.0, 10.0, dt.date(2026, 10, 2)), (2, -1.0, -2.0, dt.date(2026, 10, 2))]


def test_polars_may_give_an_eager_frame(env):
    result = run(env, f"""
        blocks:
          src: {{type: logic.python, config: {{inputs: [], code: "{AMOUNTS}"}}}}
          t: {{type: data.polars, config: {{code: "df.collect().sort('amount').head(2)"}}}}
          save: {{type: data.write_file, config: {{path: out.csv}}}}
        edges:
          - src.output -> t.table
          - t.table -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "out.csv").read_text() == "id,amount\n2,-1.0\n3,0.0\n"


def test_polars_result_that_is_not_a_frame_is_a_clear_error(env):
    result = run(env, f"""
        blocks:
          src: {{type: logic.python, config: {{inputs: [], code: "{AMOUNTS}"}}}}
          t: {{type: data.polars, config: {{code: "df.collect().height"}}}}
        edges:
          - src.output -> t.table
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["t"][0] == "failed" and "must return a Polars frame, got int" in blocks["t"][1]


def test_polars_wrong_column_name_is_a_clear_error(env):
    result = run(env, f"""
        blocks:
          src: {{type: logic.python, config: {{inputs: [], code: "{AMOUNTS}"}}}}
          t: {{type: data.polars, config: {{code: "df.filter(pl.col('amout') > 0)"}}}}
        edges:
          - src.output -> t.table
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["t"][0] == "failed" and "amout" in blocks["t"][1] and "amount" in blocks["t"][1]


def test_polars_syntax_error_names_the_line_of_your_code(env):
    result = run(env, f"""
        blocks:
          src: {{type: logic.python, config: {{inputs: [], code: "{AMOUNTS}"}}}}
          t:
            type: data.polars
            config:
              code: |
                df.filter(pl.col("amount") > 0)
                  .select(pl.col("amount") +)
        edges:
          - src.output -> t.table
    """)

    assert result.returncode == 1
    _, blocks = last_blocks(env)
    assert blocks["t"][0] == "failed" and "SyntaxError" in blocks["t"][1]
    assert "line 2" in blocks["t"][1], blocks["t"][1]  # the second line of the code is wrong


def test_polars_on_an_empty_table_keeps_the_columns(env):
    result = run(env, """
        blocks:
          src: {type: data.duckdb_sql, config: {inputs: [], sql: "select 1 as id, 2.5 as amount where false"}}
          t: {type: data.polars, config: {code: "df.with_columns(doubled=pl.col('amount') * 2)"}}
          save: {type: data.write_file, config: {path: out.csv}}
        edges:
          - src.result -> t.table
          - t.table -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert out(env, "out.csv").read_text() == "id,amount,doubled\n"


# --- tables between blocks ---------------------------------------------------------------------

def test_frames_from_python_code_become_tables_for_the_next_blocks(env):
    result = run(env, """
        blocks:
          make:
            type: logic.python
            config:
              inputs: []
              outputs: [eager, lazy, arrow]
              code: |
                import pyarrow as pa
                eager = pl.DataFrame({'k': [1, 2], 'x': ['p', 'q']})
                lazy = pl.LazyFrame({'k': [1, 2], 'y': [10.5, 20.5]})
                arrow = pa.table({'k': [2, 1], 'z': [True, False]})
          joined:
            type: data.duckdb_sql
            config:
              inputs: [eager, lazy, arrow]
              sql: "select e.k, x, y, z from eager e join lazy l using (k) join arrow a using (k) order by k"
          save: {type: data.write_file, config: {path: out.parquet}}
        edges:
          - make.eager -> joined.eager
          - make.lazy -> joined.lazy
          - make.arrow -> joined.arrow
          - joined.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert pl.read_parquet(out(env, "out.parquet")).rows() == [(1, "p", 10.5, False), (2, "q", 20.5, True)]
    _, blocks = last_blocks(env)
    assert "2 rows" in blocks["make"][2] and "2 columns" in blocks["make"][2]


def test_a_table_reaches_python_code_as_a_table_and_feeds_several_blocks(env):
    result = run(env, """
        blocks:
          src: {type: data.duckdb_sql, config: {inputs: [], sql: "select range as x, 'r' || range as name from range(5)"}}
          look:
            type: logic.python
            config:
              inputs: [table]
              outputs: []
              code: |
                import json
                open('seen.json', 'w').write(json.dumps({
                    'rows': table.num_rows, 'len': len(table), 'columns': list(table.schema),
                    'head': table.head(2).to_dicts(), 'total': table.scan().select(pl.col('x').sum()).collect().item()}))
          total: {type: data.duckdb_sql, config: {inputs: [t], sql: "select count(*) as n, max(x) as top from t"}}
          save: {type: data.write_file, config: {path: total.csv}}
        edges:
          - src.result -> look.table
          - src.result -> total.t
          - total.result -> save.table
    """)

    assert result.returncode == 0, result.stderr
    assert json.loads(out(env, "seen.json").read_text()) == {
        "rows": 5, "len": 5, "columns": ["x", "name"], "head": [{"x": 0, "name": "r0"}, {"x": 1, "name": "r1"}],
        "total": 10}
    assert out(env, "total.csv").read_text() == "n,top\n5,4\n"


# --- Data preview in the editor -------------------------------------------------------------------

def _run_in_editor(win, qtbot, path, text):
    tab = win.open_flow(write_flow(path, text))
    win.run_flow(tab)
    wait_done(qtbot, tab)
    return tab


def _select(tab, block):
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)


def _grid(win) -> tuple[list[str], list[list[str]]]:
    model = win.preview.view.model()
    headers = [model.headerData(c, Qt.Horizontal) for c in range(model.columnCount())]
    cells = [[model.data(model.index(r, c)) for c in range(model.columnCount())] for r in range(model.rowCount())]
    return headers, cells


def test_data_preview_shows_a_table_as_a_grid_with_its_types_dates_and_nulls(win, qtbot, tmp_path):
    (tmp_path / "sales.csv").write_text("day,region,amount,note\n2026-10-01,Zürich,1.5,a\n2026-10-02,EU,,\n",
                                        encoding="utf-8")
    tab = _run_in_editor(win, qtbot, tmp_path / "preview.yaml", """
        blocks:
          start: {type: trigger.manual}
          sales: {type: data.read_file, config: {path: sales.csv}}
          dated:
            type: data.duckdb_sql
            config: {inputs: [sales], sql: "select cast(day as date) as day, region, amount, note from sales"}
        edges:
          - start.start -> sales.after
          - sales.table -> dated.sales
    """)
    assert tab.last["status"] == "success"

    _select(tab, "dated")

    assert win.preview.label.text() == "dated.result: 2 rows × 4 columns"
    headers, cells = _grid(win)
    assert [h.split("\n")[0] for h in headers] == ["day", "region", "amount", "note"]
    assert "date" in headers[0].lower() and "float" in headers[2].lower()  # each column says its type
    assert cells == [["2026-10-01", "Zürich", "1.5", "a"], ["2026-10-02", "EU", "", ""]]  # nulls show empty
    # Hovering the output shows the same table.
    tip = tab.scene.nodes["dated"].ports[("result", True)].toolTip()
    assert "2 rows" in tip and "Zürich" in tip


def test_data_preview_of_big_empty_and_wide_tables(win, qtbot, tmp_path):
    wide_columns = ", ".join(f"{i} as c{i}" for i in range(300))
    tab = _run_in_editor(win, qtbot, tmp_path / "sizes.yaml", f"""
        blocks:
          start: {{type: trigger.manual}}
          big: {{type: data.duckdb_sql, config: {{inputs: [], sql: "select range as i from range(1000)"}}}}
          empty: {{type: data.duckdb_sql, config: {{inputs: [], sql: "select 1 as id, 'x' as name where false"}}}}
          wide: {{type: data.duckdb_sql, config: {{inputs: [], sql: "select {wide_columns}"}}}}
        edges:
          - start.start -> big.after
          - start.start -> empty.after
          - start.start -> wide.after
    """)
    assert tab.last["status"] == "success"

    _select(tab, "big")
    assert "1,000 rows" in win.preview.label.text() and "first 200" in win.preview.label.text()
    headers, cells = _grid(win)
    assert len(cells) == 200 and cells[0] == ["0"] and cells[-1] == ["199"]

    _select(tab, "empty")
    assert win.preview.label.text() == "empty.result: 0 rows × 2 columns"
    headers, cells = _grid(win)
    assert [h.split("\n")[0] for h in headers] == ["id", "name"] and cells == []

    _select(tab, "wide")
    headers, cells = _grid(win)
    assert len(headers) == 300 and headers[-1].startswith("c299") and cells[0][-1] == "299"


def test_data_preview_shows_values_that_are_not_tables_and_each_output(win, qtbot, tmp_path):
    tab = _run_in_editor(win, qtbot, tmp_path / "outputs.yaml", """
        blocks:
          start: {type: trigger.manual}
          make:
            type: logic.python
            config:
              inputs: []
              outputs: [rows, info]
              code: |
                rows = pl.DataFrame({'a': [1, 2]})
                info = {'status': 'ok'}
          save: {type: data.write_file, config: {path: out/rows.csv}}
        edges:
          - start.start -> make.after
          - make.rows -> save.table
    """)
    assert tab.last["status"] == "success"

    _select(tab, "save")
    # The written file's path, with this system's separator (out\rows.csv on Windows).
    assert not win.preview.text.isHidden() and str(Path("out/rows.csv")) in win.preview.text.toPlainText()

    _select(tab, "make")
    ports = win.preview.ports
    assert not ports.isHidden() and [ports.itemText(i) for i in range(ports.count())] == ["rows", "info"]
    assert win.preview.label.text().startswith("make.rows: 2 rows × 1 column")
    ports.setCurrentText("info")
    assert not win.preview.text.isHidden() and '"status": "ok"' in win.preview.text.toPlainText()


def test_data_preview_shows_the_table_of_the_run_opened_from_run_history(win, qtbot, tmp_path):
    data = tmp_path / "data.csv"
    flow = write_flow(tmp_path / "history.yaml", """
        blocks:
          start: {type: trigger.manual}
          r: {type: data.read_file, config: {path: data.csv}}
        edges:
          - start.start -> r.after
    """)
    tab = win.open_flow(flow)
    for n, text in ((1, "x\nfirst\n"), (2, "x\nsecond\nthird\nfourth\n")):
        data.write_text(text)
        win.run_flow(tab)
        qtbot.waitUntil(lambda: not tab.running and tab.last is not None and len(tab.runs) == n, timeout=60_000)
    first, second = sorted(r.run_id for r in tab.runs)

    win.show_history_run(first)
    _select(tab, "r")
    assert win.preview.label.text().startswith("r.table: 1 row") and _grid(win)[1] == [["first"]]

    win.show_history_run(second)
    _select(tab, "r")
    assert win.preview.label.text().startswith("r.table: 3 rows") and _grid(win)[1][0] == ["second"]


def test_data_preview_of_a_run_whose_files_were_cleaned_up_says_so(win, qtbot, tmp_path):
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home" / "settings.yaml").write_text("keep_runs: 1\n")
    flow = write_flow(tmp_path / "cleaned.yaml", """
        blocks:
          start: {type: trigger.manual}
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select 1 as x"}}
        edges:
          - start.start -> q.after
    """)
    tab = win.open_flow(flow)
    for n in (1, 2):
        win.run_flow(tab)
        qtbot.waitUntil(lambda: not tab.running and tab.last is not None and len(tab.runs) == n, timeout=60_000)
    first = min(r.run_id for r in tab.runs)

    win.show_history_run(first)
    _select(tab, "q")

    assert win.preview.label.text().startswith("Cannot preview:")
    assert "keep_runs" in win.preview.label.text()


def test_data_preview_shows_a_timestamp_with_a_time_zone(win, qtbot, tmp_path):
    tab = _run_in_editor(win, qtbot, tmp_path / "zone.yaml", f"""
        blocks:
          start: {{type: trigger.manual}}
          q: {{type: data.duckdb_sql, config: {{inputs: [], sql: "select {AT_SQL}"}}}}
        edges:
          - start.start -> q.after
    """)
    assert tab.last["status"] == "success"

    _select(tab, "q")

    headers, cells = _grid(win)
    assert headers[0].split("\n")[0] == "at" and "datetime" in headers[0].lower()
    assert _is_at(dt.datetime.fromisoformat(cells[0][0])), cells


def test_data_preview_shows_the_values_of_a_list_cell(win, qtbot, tmp_path):
    tab = _run_in_editor(win, qtbot, tmp_path / "lists.yaml", """
        blocks:
          start: {type: trigger.manual}
          q: {type: data.duckdb_sql, config: {inputs: [], sql: "select 'EU' as region, [1, 2] as stores"}}
        edges:
          - start.start -> q.after
    """)
    assert tab.last["status"] == "success"

    _select(tab, "q")

    _, cells = _grid(win)
    assert cells[0][0] == "EU"
    assert "1" in cells[0][1] and "2" in cells[0][1] and "shape" not in cells[0][1], cells[0][1]
