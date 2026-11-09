"""The mutation report (packaging/mutants.py) leaves out of its score only the functions the --explained file lists
under not_tested, and lists each of them with its reason and its number of mutants, so nothing is dropped unseen."""

import json
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "mutants.py"
SHELL = {"taskloom/scheduler.py serve": "The service's outer shell: the slow tests start it for real.",
         "taskloom/scheduler.py Scheduler.check_servers": "Checks other PCs over SSH: the keeper tests cover it."}


def report(tmp_path, codes: dict, explained: dict):
    """Runs the real script on a mutants/ tree that holds these exit codes, as mutmut records them."""
    (tmp_path / "mutants" / "taskloom").mkdir(parents=True)
    (tmp_path / "mutants" / "taskloom" / "scheduler.py.meta").write_text(
        json.dumps({"exit_code_by_key": codes}), encoding="utf-8")
    (tmp_path / "explained.yaml").write_text(yaml.safe_dump(explained, allow_unicode=True), encoding="utf-8")
    done = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), "--explained", str(tmp_path / "explained.yaml"),
                           "--out", str(tmp_path / "mutants.json")], capture_output=True, text=True, timeout=300)
    return done, json.loads((tmp_path / "mutants.json").read_text(encoding="utf-8"))


def test_a_not_tested_functions_mutants_do_not_count_and_are_listed_with_their_reason(tmp_path):
    """claim_time: 9 caught and 1 missed with a reason (90%). serve and Scheduler.check_servers are not tested: their
    missed, untested and unjudged mutants would fail the run, but they are left out of the score and listed."""
    claim = "taskloom.scheduler.x_claim_time__mutmut_"
    serve = "taskloom.scheduler.x_serve__mutmut_"
    servers = "taskloom.scheduler.xǁSchedulerǁcheck_servers__mutmut_"
    codes = ({f"{claim}{i}": 1 for i in range(1, 10)} | {f"{claim}10": 0}
             | {f"{serve}1": 0, f"{serve}2": 0, f"{serve}3": 0, f"{serve}4": 33, f"{serve}5": None, f"{serve}6": 1}
             | {f"{servers}1": 33, f"{servers}2": 0})
    done, result = report(tmp_path, codes, {f"{claim}10": "It changes nothing you see.", "not_tested": SHELL})

    assert done.returncode == 0, done.stdout + done.stderr
    assert (result["score"], result["killed"], result["total"], result["passed"]) == (90.0, 9, 10, True)
    assert [s["name"] for s in result["survivors"]] == [f"{claim}10"]
    assert result["unexplained"] == [] and result["unjudged"] == []
    assert sorted(result["not_tested"], key=lambda n: n["function"]) == [
        {"function": "taskloom/scheduler.py Scheduler.check_servers", "reason": SHELL[
            "taskloom/scheduler.py Scheduler.check_servers"], "mutants": 2},
        {"function": "taskloom/scheduler.py serve", "reason": SHELL["taskloom/scheduler.py serve"], "mutants": 6}]
    for function, reason in SHELL.items():
        assert any(function in line and reason in line for line in done.stdout.splitlines()), done.stdout
    assert serve not in done.stdout and servers not in done.stdout
    assert "PASSED: mutation score 90%" in done.stdout


def test_a_function_not_listed_as_not_tested_still_counts(tmp_path):
    """Only the very functions listed are left out: _serve_tray is not serve and Scheduler._check is not
    Scheduler.check_servers, so their missed mutants still count, without a reason, and fail the run."""
    codes = {"taskloom.scheduler.xǁSchedulerǁ_check__mutmut_1": 0, "taskloom.scheduler.x__serve_tray__mutmut_1": 0,
             "taskloom.scheduler.xǁSchedulerǁ_check__mutmut_2": 1}
    done, result = report(tmp_path, codes, {"not_tested": SHELL})

    assert done.returncode == 1
    assert (result["killed"], result["total"], result["passed"]) == (1, 3, False)
    assert sorted(result["unexplained"]) == ["taskloom.scheduler.x__serve_tray__mutmut_1",
                                             "taskloom.scheduler.xǁSchedulerǁ_check__mutmut_1"]
    assert {n["function"]: n["mutants"] for n in result["not_tested"]} == {f: 0 for f in SHELL}


def test_a_single_mutant_listed_as_not_tested_is_left_out_and_its_functions_other_mutants_still_count(tmp_path):
    """A mutant named on its own under not_tested (e.g. a Windows-only line no run here can execute) is left out of
    the score and listed with its reason; the other mutants of its function still count."""
    start = "taskloom.scheduler.xǁSchedulerǁ_start__mutmut_"
    windows = {f"{start}46": "Windows only: a console window per run, which no run on Linux or macOS can show."}
    done, result = report(tmp_path, {f"{start}46": 0, f"{start}47": 0, f"{start}48": 1}, {"not_tested": windows})

    assert done.returncode == 1
    assert (result["killed"], result["total"]) == (1, 2)
    assert result["unexplained"] == [f"{start}47"]
    assert result["not_tested"] == [{"function": f"{start}46", "reason": windows[f"{start}46"], "mutants": 1}]
