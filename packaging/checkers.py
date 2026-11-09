"""Runs a feature's checkers and records what each printed, for the proof: never written by hand.

    python packaging/checkers.py docs/promises/scheduler.yaml proof/<release> [--only NAME ...] [--command CMD]
    python packaging/checkers.py docs/promises/scheduler.yaml proof/<release> --merge JOB_DIR [JOB_DIR ...]

The checkers are listed in the feature's <name>.evidence.yaml under `checkers:`, each with a name (rows name it in
green_by), a kind (trace, model, mutation, leak, charter, tests) and a command, run by the shell from the repository
with {out} replaced by proof/<release>/<name> (where a checker writes its own result, e.g. leak.json). Each one's
whole output goes to proof/<release>/<name>/logs/; checkers.json there says, for each, its command, exit code,
commit, log, final lines, last line (its summary) and key (the pattern of the lines that show its verdict, which
the proof PDF boxes in orange). Exit 1 when any checker failed.

CI runs them in parallel jobs, as a GitHub job runs at most 6 hours. --only runs the checkers named (checkers.json
lists those); with one, --command runs CMD in place of its listed command and records CMD (CI scores the mutation
testing it ran in parts in a job of its own: packaging/mutants.py --parts). --merge puts the jobs' results together:
each JOB_DIR is one job's proof/<release>, whose files are copied into proof/<release>, and checkers.json there lists
every checker in the order of `checkers:`. A checker no job ran is recorded as not run, and fails.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
HOURS = 5  # the longest a checker may run: within the 6 hours of its job on CI


def commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                          timeout=30).stdout.strip()


def run(checker: dict, out: Path, evidence: Path) -> dict:
    command = checker["command"].replace("{out}", out.as_posix())
    log = out / "logs" / (re.sub(r"[^a-z0-9]+", "-", checker["name"].lower()).strip("-") + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        done = subprocess.run(command, shell=True, cwd=ROOT, capture_output=True, text=True, errors="replace",
                              timeout=HOURS * 3600)
        output, code = done.stdout + done.stderr, done.returncode
    except subprocess.TimeoutExpired as e:
        output, code = f"{e.stdout or ''}{e.stderr or ''}\nstopped: it had not finished after {HOURS} hours", 124
    log.write_text(output, encoding="utf-8")
    lines = [line for line in output.splitlines() if line.strip()]
    return {"name": checker["name"], "kind": checker["kind"], "command": command, "exit": code, "commit": commit(),
            "log": log.relative_to(evidence).as_posix(), "summary": lines[-1] if lines else "(it printed nothing)",
            "tail": lines[-12:], "key": checker.get("key")}


def merge(listed: list, out: Path, jobs: list[Path]) -> list:
    """Every listed checker's record from the jobs' folders (`out`'s name in each), in the listed order, their files
    copied into `out`; one no job ran is recorded as not run."""
    found = []
    for job in jobs:
        folder = job / out.name
        if folder.is_dir():
            shutil.copytree(folder, out, dirs_exist_ok=True, ignore=shutil.ignore_patterns("checkers.json"))
        if (folder / "checkers.json").is_file():
            found += json.loads((folder / "checkers.json").read_text(encoding="utf-8"))
    ran = {r["name"] for r in found}
    found += [{"name": c["name"], "kind": c["kind"], "command": c["command"].replace("{out}", out.as_posix()),
               "exit": "not run", "commit": commit(), "log": "", "summary": "No job ran it, or its job ended first.",
               "tail": []} for c in listed if c["name"] not in ran]
    order = [c["name"] for c in listed]
    return sorted(found, key=lambda r: order.index(r["name"]) if r["name"] in order else len(order))


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("promises", type=Path)
    p.add_argument("evidence", type=Path)
    p.add_argument("--only", action="append", default=[], metavar="NAME")
    p.add_argument("--command", metavar="CMD")
    p.add_argument("--merge", nargs="+", type=Path, metavar="JOB_DIR")
    a = p.parse_args(argv)
    listed = (yaml.safe_load(a.promises.with_name(a.promises.stem + ".evidence.yaml").read_text(encoding="utf-8"))
              .get("checkers") or [])
    unknown = sorted(set(a.only) - {c["name"] for c in listed})
    if unknown:
        p.error(f"not listed under checkers: {', '.join(unknown)}")
    if a.command and len(a.only) != 1:
        p.error("--command is for one checker: name it with --only")
    if a.merge and (a.only or a.command):
        p.error("--merge runs no checker")
    out = (a.evidence / a.promises.stem).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if a.merge:
        results = merge(listed, out, a.merge)
        for r in results:
            print(f"{r['name']}: exit {r['exit']}: {r['summary']}")
    else:
        results = []
        for checker in listed:
            if not a.only or checker["name"] in a.only:
                results.append(run({**checker, "command": a.command or checker["command"]}, out,
                                   a.evidence.resolve()))
                print(f"{results[-1]['name']}: exit {results[-1]['exit']}: {results[-1]['summary']}", flush=True)
    (out / "checkers.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    return 1 if any(r["exit"] != 0 for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
