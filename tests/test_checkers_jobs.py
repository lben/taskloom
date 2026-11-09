"""The checkers (packaging/checkers.py) run on CI in parallel jobs, each within GitHub's 6 hours: --only runs those
named, and --merge puts the jobs' results together in one checkers.json, every listed checker in its listed order, as
if one job had run them all; a checker no job ran is recorded as not run, and fails. The real script runs here on a
feature whose checkers are small shell commands."""

import json
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "checkers.py"
# Each checker leaves a mark in {out}, where the job keeps its results, so a test can see which ones ran.
CHECKERS = [
    {"name": "Trace", "kind": "trace", "command": "echo traced > {out}/trace.txt && echo all traces match"},
    {"name": "Model", "kind": "model", "command": "echo checked > {out}/model.txt && echo no error found"},
    {"name": "Leak", "kind": "leak", "command": "echo '{\"passed\": true}' > {out}/leak.json && echo no leak"},
    {"name": "Design", "kind": "charter", "command": "echo designed > {out}/design.txt && echo no worse"},
]


def promises(tmp_path) -> Path:
    path = tmp_path / "promises" / "alpha.yaml"
    path.parent.mkdir()
    path.write_text("feature: Alpha\n", encoding="utf-8")
    (path.parent / "alpha.evidence.yaml").write_text(yaml.safe_dump({"checkers": CHECKERS}), encoding="utf-8")
    return path


def checkers(listed: Path, evidence: Path, *args) -> tuple[subprocess.CompletedProcess, list]:
    done = subprocess.run([sys.executable, str(SCRIPT), str(listed), str(evidence), *args], capture_output=True,
                          text=True, timeout=120)
    found = evidence / "alpha" / "checkers.json"
    return done, json.loads(found.read_text(encoding="utf-8")) if found.is_file() else None


def test_only_runs_the_checkers_named_and_command_runs_another_command_for_the_one_named(tmp_path):
    listed, job = promises(tmp_path), tmp_path / "job"
    done, entries = checkers(listed, job, "--only", "Leak", "--only", "Trace")

    assert done.returncode == 0, done.stdout + done.stderr
    assert [(e["name"], e["exit"], e["summary"]) for e in entries] == [("Trace", 0, "all traces match"),
                                                                       ("Leak", 0, "no leak")]
    assert sorted(p.name for p in (job / "alpha").iterdir()) == ["checkers.json", "leak.json", "logs", "trace.txt"]

    other = tmp_path / "scored"
    command = "echo scored > {out}/parts.txt && echo scored the parts"
    done, entries = checkers(listed, other, "--only", "Model", "--command", command)

    assert done.returncode == 0, done.stdout + done.stderr
    assert [(e["name"], e["kind"], e["command"], e["summary"]) for e in entries] == [
        ("Model", "model", command.replace("{out}", (other / "alpha").resolve().as_posix()), "scored the parts")]
    assert (other / "alpha" / "parts.txt").is_file() and not (other / "alpha" / "model.txt").exists()


def test_merge_puts_the_jobs_results_together_in_listed_order_and_a_checker_no_job_ran_fails(tmp_path):
    listed, jobs = promises(tmp_path), [tmp_path / "job-1", tmp_path / "job-2"]
    checkers(listed, jobs[0], "--only", "Model")
    checkers(listed, jobs[1], "--only", "Leak", "--only", "Trace")

    done, entries = checkers(listed, tmp_path / "proof", "--merge", *map(str, jobs))

    assert done.returncode == 1, done.stdout + done.stderr  # no job ran Design
    assert [(e["name"], e["exit"]) for e in entries] == [("Trace", 0), ("Model", 0), ("Leak", 0),
                                                         ("Design", "not run")]
    for entry in entries[:3]:  # each one's log and results, where the proof looks for them
        assert (tmp_path / "proof" / entry["log"]).is_file(), entry
    assert all((tmp_path / "proof" / "alpha" / name).is_file() for name in ("leak.json", "trace.txt", "model.txt"))

    jobs.append(tmp_path / "job-3")
    checkers(listed, jobs[2], "--only", "Design")
    done, entries = checkers(listed, tmp_path / "proof-all", "--merge", *map(str, jobs))

    assert done.returncode == 0, done.stdout + done.stderr
    assert [(e["name"], e["exit"]) for e in entries] == [(c["name"], 0) for c in CHECKERS]
