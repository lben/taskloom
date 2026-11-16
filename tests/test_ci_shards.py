"""CI splits each OS's tests over several machines: packaging/test_shards.py says which whole test files each machine
runs, each machine writes its own results file (tests/conftest.py, with TASKLOOM_LEDGER), and
packaging/merge_results.py joins them into the one results file per OS that the proof job reads. Together the
machines run every test file outside tests/proofs exactly once, and a missing or overlapping machine's file is not
taken for a whole run. (A2 of the CI speed-up; the scripts run as CI runs them.)"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO = HERE.parent
SHARDS, MERGE = REPO / "packaging" / "test_shards.py", REPO / "packaging" / "merge_results.py"
SHA = "3f9c2a1d8e7b6c5a4f3e2d1c0b9a8f7e6d5c4b3a"


def run(script: Path, *args) -> subprocess.CompletedProcess:
    assert script.is_file(), f"no {script.relative_to(REPO).as_posix()}"  # (its absence would also exit non-zero)
    return subprocess.run([sys.executable, str(script), *map(str, args)], cwd=REPO, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=120)


def shards(n: int, root: Path | None = None) -> list[list[str]]:
    """The test files of shard 1, 2, ... n, as printed (relative paths, one per line)."""
    printed = []
    for k in range(1, n + 1):
        done = run(SHARDS, "--shard", f"{k}/{n}", *(["--root", root] if root else []))
        assert done.returncode == 0, f"--shard {k}/{n} exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
        printed.append([Path(line.strip()).as_posix() for line in done.stdout.splitlines() if line.strip()])
    return printed


def every_test_file(root: Path) -> set[str]:
    """What pytest --ignore=tests/proofs collects from: tests/test_*.py, not under tests/proofs."""
    return {p.relative_to(root).as_posix() for p in (root / "tests").rglob("test_*.py")
            if p.relative_to(root / "tests").parts[0] != "proofs"}


def runs_each_once(printed: list[list[str]], files: set[str]):
    together = [file for shard in printed for file in shard]
    twice = sorted({file for file in together if together.count(file) > 1})
    assert not twice, f"in more than one shard: {twice}"
    assert set(together) == files, (f"in no shard: {sorted(files - set(together))}; "
                                    f"not a test file: {sorted(set(together) - files)}")


@pytest.mark.parametrize("n", [2, 3])
def test_the_shards_of_the_repository_run_every_test_file_outside_the_proofs_once(n):
    """Linux runs 2 shards, Windows 3. Each machine works out its own shard, so every call gives the same; a shard with
    no file would leave pytest with no file argument, and pytest would then run every test."""
    printed = shards(n)
    assert shards(n) == printed, "a second call gave other shards"
    runs_each_once(printed, every_test_file(REPO))
    assert all(printed), f"an empty shard: {[len(shard) for shard in printed]}"


def test_the_shards_of_any_folder_run_each_of_its_test_files_once_also_one_never_timed(tmp_path):
    """--root: the test files there, measured or not (a new test file has no measured time yet); not its helpers, not
    its proofs, not a test_*.py outside tests/."""
    files = ["tests/test_scheduler_promises.py", "tests/test_paths_engine.py", "tests/test_made_up_never_timed.py",
             "tests/test_made_up_other.py", "tests/test_made_up_third.py"]
    for name in files + ["tests/conftest.py", "tests/sshserver.py", "tests/proofs/conftest.py",
                         "tests/proofs/test_proof_made_up.py", "docs/test_not_a_test.py"]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("def test_nothing():\n    pass\n", encoding="utf-8")
    for n in (2, 3):
        printed = shards(n, tmp_path)
        assert shards(n, tmp_path) == printed, f"{n} shards: a second call gave other shards"
        runs_each_once(printed, set(files))


@pytest.mark.parametrize("spec", ["0/3", "4/3", "1/0", "a/3", "3"])
def test_a_shard_that_does_not_exist_fails_and_says_so(spec):
    done = run(SHARDS, "--shard", spec)
    said = (done.stdout + done.stderr).strip()
    assert done.returncode != 0, f"--shard {spec} exited 0 and printed: {done.stdout[:500]}"
    assert said and "Traceback" not in done.stderr, f"--shard {spec}: no message, or a crash:\n{said[-2000:]}"


PLAIN = """
import pytest


def test_passes():
    pass


def test_is_skipped():
    pytest.skip("not on this machine")
"""


def test_a_shard_with_no_marked_test_still_writes_its_results_file_with_its_numbers(tmp_path):
    """Most test files have no test marked with an agreed row or a charter rule; a shard of only such files must still
    leave its results file, or its passed, failed and skipped tests would be missing from the release's numbers.
    The real conftest.py, in a pytest session of its own."""
    session = tmp_path / "session"
    session.mkdir()
    shutil.copyfile(HERE / "conftest.py", session / "conftest.py")
    (session / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (session / "test_plain.py").write_text(PLAIN, encoding="utf-8")
    results = tmp_path / "results-Linux-2.json"
    inner = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}  # (not a by-hand CI run's list of tests)
    env = {**inner, "TASKLOOM_LEDGER": str(results),
           "PYTHONPATH": os.pathsep.join([str(HERE), os.environ.get("PYTHONPATH", "")])}  # (conftest's helpers)
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_plain.py"],
                          cwd=session, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert results.is_file(), "no results file was written:\n" + done.stdout[-2000:]
    written = json.loads(results.read_text(encoding="utf-8"))
    assert written["tests"] == [], written
    assert written["totals"] == {"passed": 1, "failed": 0, "skipped": 1}, written
    assert written["platform"] == sys.platform, written


def entry(name: str, outcome: str = "passed", why: str = "") -> dict:
    """A test's entry in a results file, as tests/conftest.py writes it."""
    return {"id": f"tests/test_x.py::{name}", "rows": ["SCH-1"], "charter": [], "every": [], "outcome": outcome,
            "why": why}


def results(path: Path, totals: tuple, tests: list) -> Path:
    path.write_text(json.dumps({"commit": SHA, "platform": "win32",
                                "totals": dict(zip(("passed", "failed", "skipped"), totals)), "tests": tests}),
                    encoding="utf-8")
    return path


def test_merge_joins_the_shards_results_into_one_file_for_the_os(tmp_path):
    """The proof job reads one results file per OS, as before: every shard's tests in it, as they were, and the
    numbers of all of them."""
    first = [entry("t1"), entry("t2", "failed", "AssertionError: two runs at 09:00")]
    second = [entry("t3", "skipped", "not on Windows")]
    inputs = [results(tmp_path / "results-Windows-1.json", (3, 1, 2), first),
              results(tmp_path / "results-Windows-2.json", (5, 0, 1), second),
              results(tmp_path / "results-Windows-3.json", (7, 0, 0), [])]  # (no marked test there)
    out = tmp_path / "merged" / "results-Windows.json"
    out.parent.mkdir()
    done = run(MERGE, "--expect", 3, out, *inputs)
    assert done.returncode == 0, done.stdout + done.stderr
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert merged["commit"] == SHA and merged["platform"] == "win32", merged
    assert merged["totals"] == {"passed": 15, "failed": 1, "skipped": 3}, merged["totals"]
    assert sorted(merged["tests"], key=lambda t: t["id"]) == first + second, merged["tests"]


def test_merge_fails_and_says_so_when_a_shards_file_is_missing(tmp_path):
    """A shard that crashed or ran out of time leaves no file: its tests must not silently drop out of the proof."""
    inputs = [results(tmp_path / "results-Windows-1.json", (3, 0, 0), [entry("t1")]),
              results(tmp_path / "results-Windows-2.json", (5, 0, 0), [entry("t2")])]
    done = run(MERGE, "--expect", 3, tmp_path / "results-Windows.json", *inputs)
    said = (done.stdout + done.stderr).strip()
    assert done.returncode != 0, "two files of three shards were merged as a whole run"
    assert "3" in said and "Traceback" not in done.stderr, f"no message saying 3 were expected, or a crash:\n{said}"


def test_merge_fails_and_names_the_test_when_two_shards_ran_the_same_test(tmp_path):
    """Each test file runs on one machine only; a test in two files means the shards overlap, and one of its outcomes
    would hide the other's."""
    inputs = [results(tmp_path / "results-Windows-1.json", (2, 0, 0), [entry("t1"), entry("t2")]),
              results(tmp_path / "results-Windows-2.json", (1, 1, 0), [entry("t2", "failed", "AssertionError")])]
    done = run(MERGE, "--expect", 2, tmp_path / "results-Windows.json", *inputs)
    said = (done.stdout + done.stderr).strip()
    assert done.returncode != 0, "a test in two shards' files was merged"
    assert "tests/test_x.py::t2" in said and "Traceback" not in done.stderr, f"the test is not named:\n{said}"
