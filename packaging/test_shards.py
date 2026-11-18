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
# Minutes per file (Windows, Linux): the setup, call and teardown times of its tests that `pytest --durations=0`
# printed in full CI run 44193671785 (2026-11-17), summed, to 0.1 minute; 0.0 for under 3 seconds, or where its tests
# are skipped on that OS (the test server's commands need /bin/sh; TLC is not installed on the test machines).
MINUTES = {
    "test_scheduler_promises": (26.5, 6.8), "test_scheduler_on_time": (4.6, 4.6), "test_braces": (4.4, 2.4),
    "test_rows": (3.8, 1.9), "test_scheduler_start_at": (3.1, 2.9), "test_zoom_panes": (2.7, 1.9),
    "test_scheduler_stops_and_sleeps": (2.5, 4.8), "test_paths_scheduler": (2.4, 2.4), "test_docs_help": (2.2, 1.2),
    "test_rerun_carries_on": (2.1, 1.6), "test_paths_engine": (1.9, 1.0), "test_long_output_rest": (1.9, 0.4),
    "test_paths_flow_format": (1.7, 0.6), "test_interval_schedules": (1.6, 1.5), "test_paths_logic": (1.6, 0.9),
    "test_history_list_scheduled": (1.5, 1.3), "test_engine": (1.5, 0.9), "test_paths_reports_email": (1.5, 0.6),
    "test_paths_editor_running": (1.4, 1.0), "test_editor": (1.4, 0.6), "test_paths_data": (1.3, 0.7),
    "test_scheduler_turn_off_and_restart": (1.2, 1.2), "test_detect_anomalies": (1.2, 0.7),
    "test_paths_database": (1.2, 0.7), "test_paths_editor_editing": (1.2, 0.7), "test_wait_countdown": (1.1, 1.0),
    "test_merger_each_line": (1.1, 0.8), "test_html_template_inputs": (1.1, 0.4),
    "test_pyenv_tables_pandas": (1.1, 0.4), "test_python_default_output": (1.1, 0.4),
    "test_scheduler_at_its_time": (1.0, 0.8), "test_api_request": (1.0, 0.5),
    "test_scheduler_new_promises": (0.9, 0.8), "test_curl_import": (0.9, 0.7), "test_paths_cli_config": (0.9, 0.7),
    "test_parallel_lines": (0.9, 0.6), "test_reliability": (0.9, 0.6), "test_retention_per_start": (0.9, 0.5),
    "test_statistics": (0.9, 0.5), "test_rerun_old_and_kept": (0.9, 0.4), "test_scheduler_while_off": (0.8, 0.7),
    "test_harness_robustness": (0.8, 0.6), "test_loop_container": (0.8, 0.5),
    "test_rerun_stopped_and_cleaned": (0.8, 0.5), "test_api_session": (0.8, 0.4),
    "test_history_selection": (0.7, 0.5), "test_scheduler_model_fixes": (0.6, 0.5), "test_stuck_run": (0.6, 0.5),
    "test_lines": (0.6, 0.4), "test_rerun_mutants": (0.6, 0.4), "test_scheduler_mistakes_and_counts": (0.6, 0.4),
    "test_code_editor": (0.6, 0.3), "test_ci_reuse": (0.6, 0.2), "test_proof_pdf_new": (0.6, 0.2),
    "test_retention_by_age": (0.5, 0.4), "test_timeout_grace": (0.5, 0.4), "test_rerun_refusal_seen": (0.5, 0.3),
    "test_failure_inputs": (0.5, 0.2), "test_inspect_mermaid": (0.5, 0.1), "test_check_table": (0.4, 0.4),
    "test_paths_files": (0.4, 0.4), "test_inspect_log": (0.4, 0.3), "test_inspector_tab_stays": (0.4, 0.3),
    "test_long_output_log": (0.4, 0.3), "test_parallel_line_membership": (0.4, 0.3), "test_ask_user_box": (0.4, 0.2),
    "test_check_file": (0.4, 0.2), "test_commit_history": (0.4, 0.2), "test_find_bar": (0.4, 0.2),
    "test_long_output_mutants": (0.4, 0.2), "test_placeholders": (0.4, 0.2), "test_rerun_died": (0.4, 0.2),
    "test_run_scope": (0.4, 0.2), "test_starter_comments": (0.4, 0.2), "test_scheduler_held_times": (0.3, 0.3),
    "test_ask_user_fields": (0.3, 0.2), "test_block_icons": (0.3, 0.2), "test_done_where_needed": (0.3, 0.2),
    "test_handled_error": (0.3, 0.2), "test_scheduler_service_calls": (0.3, 0.2), "test_proof_pdf_links": (0.3, 0.1),
    "test_pyenv": (0.3, 0.1), "test_save_and_commit": (0.3, 0.1), "test_template_styles": (0.3, 0.1),
    "test_docs_site": (0.2, 0.2), "test_editor_run_choices": (0.2, 0.2), "test_theme": (0.2, 0.2),
    "test_ask_no_answer": (0.2, 0.1), "test_copy_executed_query": (0.2, 0.1), "test_delete_user_block": (0.2, 0.1),
    "test_html_template_formatting": (0.2, 0.1), "test_preview_long_error": (0.2, 0.1),
    "test_python_inputs_by_source": (0.2, 0.1), "test_python_new_block": (0.2, 0.1), "test_reports": (0.2, 0.1),
    "test_result_time": (0.2, 0.1), "test_reusable_block": (0.2, 0.1), "test_run_output_and_tables": (0.2, 0.1),
    "test_run_status_fixes": (0.2, 0.1), "test_scheduler": (0.2, 0.1), "test_scheduler_manual_runs": (0.2, 0.1),
    "test_triggers": (0.2, 0.1), "test_upgrade": (0.2, 0.1), "test_current_tab": (0.2, 0.0),
    "test_dialog_speed": (0.2, 0.0), "test_mutants_report": (0.1, 0.6), "test_service_off": (0.1, 0.3),
    "test_canvas_paste_and_reuse": (0.1, 0.1), "test_charter": (0.1, 0.1), "test_editor_exit": (0.1, 0.1),
    "test_errors_in_terminal": (0.1, 0.1), "test_inspect_mermaid_theme": (0.1, 0.1), "test_jdbc_fields": (0.1, 0.1),
    "test_proof_pdf_cover_rows": (0.1, 0.1), "test_proof_pdf_v2": (0.1, 0.1),
    "test_scheduler_ci_findings": (0.1, 0.1), "test_scheduler_mutants_ends": (0.1, 0.1),
    "test_scheduler_mutants_times": (0.1, 0.1), "test_settings_wording_and_drivers": (0.1, 0.1),
    "test_zoom": (0.1, 0.1), "test_paste_at_cursor": (0.1, 0.0), "test_readme_guide": (0.1, 0.0),
    "test_scheduler_mutants_state": (0.1, 0.0), "test_scheduler_question_dropped": (0.1, 0.0),
    "test_tab_plus": (0.1, 0.0), "test_paths_servers": (0.0, 1.4), "test_keeper_lock": (0.0, 0.6),
    "test_jdbc": (0.0, 0.5), "test_servers": (0.0, 0.5), "test_deploy": (0.0, 0.1), "test_checkers_jobs": (0.0, 0.0),
    "test_ci_shards": (0.0, 0.0), "test_dates": (0.0, 0.0), "test_history_lock": (0.0, 0.0),
    "test_merger_each_line_stop": (0.0, 0.0), "test_promises_found": (0.0, 0.0), "test_promises_text": (0.0, 0.0),
    "test_proof_pdf_reuse": (0.0, 0.0), "test_schedule_times": (0.0, 0.0),
    "test_scheduler_mutants_final": (0.0, 0.0), "test_scheduler_mutants_starts": (0.0, 0.0),
    "test_scheduler_trace_findings": (0.0, 0.0), "test_tla_trace": (0.0, 0.0),
    "test_tooltip_font_and_status": (0.0, 0.0),
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
