"""Which test files one of CI's test machines runs: shard K of N, by whole files.

    python packaging/test_shards.py --shard K/N [--root DIR]

Prints the test files of shard K, one path per line, relative to DIR (default: the repository): every tests/test_*.py
not under tests/proofs (the proof jobs run those), each in exactly one of the N shards, the same on every call. CI
passes them to pytest as arguments, never through PYTEST_ADDOPTS: pytest runs a test starts would take them too.
The shards are balanced by the minutes each file took on CI, on the OS this runs on; a file not measured counts as a
short one.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Minutes per file (Windows, Linux): the median of 5 full CI runs of 2026-11 (one progress line of `pytest -q` holds
# 72 tests; its time is shared among them), the scheduler's slowest files judged by the lines they fill alone.
MINUTES = {
    "test_scheduler_promises": (26.0, 10.0), "test_scheduler_on_time": (4.0, 2.3),
    "test_scheduler_service_calls": (4.0, 5.0), "test_scheduler_stops_and_sleeps": (2.5, 1.9),
    "test_paths_engine": (2.1, 1.6), "test_paths_reports_email": (1.9, 1.3), "test_loop_container": (1.9, 0.8),
    "test_paths_logic": (1.7, 1.1), "test_detect_anomalies": (1.6, 1.3), "test_reliability": (1.6, 1.1),
    "test_paths_scheduler": (1.5, 1.8), "test_scheduler_new_promises": (1.5, 1.5),
    "test_scheduler_start_at": (1.5, 1.2), "test_scheduler_turn_off_and_restart": (1.5, 1.1),
    "test_find_bar": (1.5, 1.3), "test_engine": (1.4, 1.0), "test_paths_flow_format": (1.4, 1.1),
    "test_paths_editor_editing": (1.3, 1.1), "test_api_request": (1.3, 0.7), "test_scheduler_while_off": (1.3, 1.0),
    "test_paths_data": (1.2, 0.8), "test_merger_each_line": (1.2, 0.9), "test_docs_help": (1.2, 0.9),
    "test_editor": (1.1, 0.8), "test_paths_servers": (1.0, 1.5), "test_python_default_output": (0.9, 0.5),
    "test_parallel_lines": (0.8, 0.8), "test_paths_editor_running": (0.8, 0.7), "test_rerun_carries_on": (0.8, 0.7),
    "test_retention_by_age": (0.8, 0.6), "test_scheduler_question_dropped": (0.8, 0.4),
    "test_handled_error": (0.8, 0.7), "test_rerun_old_and_kept": (0.8, 0.6), "test_long_output_rest": (0.7, 0.3),
    "test_paths_cli_config": (0.7, 0.6), "test_paths_database": (0.7, 0.6), "test_curl_import": (0.7, 0.5),
    "test_rerun_mutants": (0.7, 0.6), "test_paths_files": (0.7, 0.6), "test_scheduler_trace_findings": (0.7, 0.4),
    "test_statistics": (0.7, 0.7), "test_api_session": (0.6, 0.3), "test_html_template_formatting": (0.6, 0.5),
    "test_proof_pdf_v2": (0.6, 0.4), "test_inspect_log": (0.6, 0.6), "test_rerun_refusal_seen": (0.6, 0.5),
    "test_theme": (0.6, 0.5), "test_long_output_mutants": (0.5, 0.4), "test_schedule_times": (0.5, 0.4),
    "test_scheduler_at_its_time": (0.5, 0.4), "test_html_template_inputs": (0.5, 0.4),
    "test_long_output_log": (0.5, 0.5), "test_scheduler_held_times": (0.5, 0.4),
    "test_rerun_stopped_and_cleaned": (0.5, 0.4), "test_starter_comments": (0.5, 0.6),
    "test_inspect_mermaid": (0.5, 0.6), "test_interval_schedules": (0.5, 0.6),
}
SHORT = 0.25  # minutes, for a file not measured (most take a few seconds)


def shards(root: Path, n: int) -> list[list[str]]:
    """The test files split in n, each file to the shard with the fewest minutes so far, the longest first."""
    files = sorted(p.relative_to(root).as_posix() for p in (root / "tests").rglob("test_*.py")
                   if p.relative_to(root / "tests").parts[0] != "proofs")
    column = 0 if sys.platform == "win32" else 1
    minutes = {f: MINUTES[Path(f).stem][column] if Path(f).stem in MINUTES else SHORT for f in files}
    split, loads = [[] for _ in range(n)], [0.0] * n
    for f in sorted(files, key=lambda f: (-minutes[f], f)):
        k = loads.index(min(loads))
        split[k].append(f)
        loads[k] += minutes[f]
    return [sorted(shard) for shard in split]


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--shard", required=True, metavar="K/N")
    p.add_argument("--root", type=Path, default=ROOT)
    a = p.parse_args(argv)
    shard = re.fullmatch(r"(\d+)/(\d+)", a.shard)
    if not shard or not 1 <= int(shard[1]) <= int(shard[2]):
        p.error(f"--shard {a.shard}: K/N with 1 <= K <= N")
    split = shards(a.root.resolve(), int(shard[2]))
    if not all(split):  # (pytest given no file would run every test)
        p.error(f"{shard[2]} shards, but only {sum(map(len, split))} test files")
    sys.stdout.reconfigure(newline="\n")  # (bash on Windows reads them: no carriage returns)
    print("\n".join(split[int(shard[1]) - 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
