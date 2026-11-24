"""When CI may take a long check from an earlier run instead of running it again: the scheduler's TLA+ model check
(about 2 hours) or its mutation testing (3 times 1 hour), or another feature's checkers (its mutation testing): when
every file it reads is byte for byte as it was there.

    python packaging/ci_reuse.py key model|mutation|feature:<f> [--root DIR] [--salt TEXT]
    python packaging/ci_reuse.py files model|mutation|feature:<f> [--root DIR]
    python packaging/ci_reuse.py pick --repo-id ID [--days 30] [--now ISO8601] NAME...
    python packaging/ci_reuse.py mark DIR --run RUN --url URL --date YYYY-MM-DD [--commit SHA]
    python packaging/ci_reuse.py select [--features NAMES] [--root DIR]

key prints a key over the contents of the files the check reads, and TEXT (CI passes the versions of the tools it
runs with); CI keeps a check's result under its key and looks for it there. files prints those files:
- model: the spec and its config, the model check's entry in the scheduler's evidence (its command and key lines; not
  the rows' words or the other checkers) and packaging/checkers.py, which writes its record.
- mutation: the files mutmut mutates, pyproject.toml, uv.lock, packaging/mutants.py, the scheduler's mutants yaml, the
  test files pyproject.toml selects, every tests/ file they import (followed through) and tests/conftest.py. Not the
  rest of taskloom/, README.md or the docs: the user's rule is "when no scheduler file changed"; the proof PDF says
  that other files may have changed, and the test job runs the scheduler's tests on every commit.
- feature:<f>, whole files: those the scope of docs/promises/<f>.mutants.yaml mutates, the test files its tests: lines
  name, every tests/ file they import (followed through) and tests/conftest.py, the files under its reads: (what else
  its code or those tests read), its mutants and evidence yaml, packaging/mutants.py and packaging/checkers.py (which
  run and score it), pyproject.toml and uv.lock. Not the rest of taskloom/, as for the scheduler. A feature with no
  scope, with a checker other than mutation testing, or naming a file that is not there gets no key (exit 1): its
  checkers always run.
pick reads artifacts as GitHub's API lists them (one JSON object per line) and prints the newest run (the highest id)
that has, for every NAME, one that is not expired, was uploaded by repository ID (not by a fork's pull request: anyone
can write what that uploads) and is less than DAYS days old (a result is checked again at least that often); nothing
when no run has them all.
mark adds to every checker record under DIR (each checkers.json) the run it was taken from, its link, its date and
the commit it checked (SHA, or the record's own), for the proof PDF to name; a record that already says where it
comes from keeps that.
select prints the features whose mutation testing a run runs, one line each, sorted: its name, the CI jobs it runs in
at once (parts: in its docs/promises/<name>.mutants.yaml, else 1) and the check whose key keeps its result (mutation
for the scheduler, feature:<name> for the others). Every docs/promises/*.mutants.yaml, or only those NAMES names
(separated by commas and/or spaces; a run by hand with features). A name that is no feature, NAMES that name none and
a parts: that is not a whole number of 1 or more are refused (exit 2).
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import re
import shlex
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE, MODEL_CHECK = "docs/promises/scheduler.evidence.yaml", "TLA+ model check"


def model(root: Path) -> dict:
    """What the model check reads: path -> the bytes that count."""
    checkers = (yaml.safe_load((root / EVIDENCE).read_text(encoding="utf-8")) or {}).get("checkers") or []
    entry = next((c for c in checkers if c.get("name") == MODEL_CHECK), None)
    if entry is None:
        sys.exit(f"ci_reuse.py: no {MODEL_CHECK} under checkers: in {EVIDENCE}")
    read = {name: (root / name).read_bytes() for name in ("specs/formal/SchedulerService.tla",
                                                         "specs/formal/SchedulerService.cfg", "packaging/checkers.py")}
    return read | {EVIDENCE: json.dumps(entry, sort_keys=True).encode()}


def imported(root: Path, file: Path) -> set[Path]:
    """The tests/ files a Python file imports: as tests.<name>, or by its bare name (pytest puts tests/ on the path)."""
    names = set()
    for node in ast.walk(ast.parse(file.read_bytes(), str(file))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names |= {node.module} | {f"{node.module}.{alias.name}" for alias in node.names}
    found = set()
    for name in names:
        for base in (root, root / "tests"):
            module = base.joinpath(*name.split("."))
            for path in (module.with_suffix(".py"), module / "__init__.py"):
                if path.is_file() and path.is_relative_to(root / "tests"):
                    found.add(path)
    return found


def walked(root: Path, names: set[str], what: str) -> set[str]:
    """The test files named, every tests/ file they import (followed through) and tests/conftest.py."""
    todo = [root / name for name in sorted(names)] + [root / "tests" / "conftest.py"]
    tests = set()
    while todo:
        file = todo.pop()
        if file not in tests:
            if not file.is_file():
                sys.exit(f"ci_reuse.py: {what} reads {file.relative_to(root).as_posix()}, which is not there")
            tests.add(file)
            todo += imported(root, file)
    return {file.relative_to(root).as_posix() for file in tests}


def mutation(root: Path) -> dict:
    """What mutation testing reads: path -> its bytes."""
    settings = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"]
    selected = {arg.split("::")[0] for arg in settings.get("pytest_add_cli_args_test_selection", [])
                if arg.split("::")[0].endswith(".py")}
    names = {*settings.get("only_mutate", []), "pyproject.toml", "uv.lock", "packaging/mutants.py",
             "docs/promises/scheduler.mutants.yaml", *walked(root, selected, "mutation testing")}
    return {name: (root / name).read_bytes() for name in names}


def feature(root: Path, name: str) -> dict:
    """What a feature's checkers read, all of them mutation testing of the scope in its mutants yaml: path -> its
    bytes."""
    mutants, evidence = f"docs/promises/{name}.mutants.yaml", f"docs/promises/{name}.evidence.yaml"
    for file in (mutants, evidence):
        if not (root / file).is_file():
            sys.exit(f"ci_reuse.py: {file} is not there")
    explained = yaml.safe_load((root / mutants).read_text(encoding="utf-8")) or {}
    scope = explained.get("scope")
    if not scope:
        sys.exit(f"ci_reuse.py: {mutants} has no scope")
    checkers = (yaml.safe_load((root / evidence).read_text(encoding="utf-8")) or {}).get("checkers") or []
    others = sorted({str(c.get("kind")) for c in checkers} - {"mutation"})
    if others:
        sys.exit(f"ci_reuse.py: {evidence} lists checkers of kind {', '.join(others)}, which the key does not cover")
    # The scope and each group in its mutate: (a mutate: and tests: of its own); a mutate: entry is a file and maybe a
    # function, a tests: line one pytest run.
    groups = [scope, *(entry for entry in scope["mutate"] if isinstance(entry, dict))]
    mutated = {entry.split(" ")[0] for group in groups for entry in group["mutate"] if isinstance(entry, str)}
    tests = {arg.split("::")[0] for group in groups for line in group.get("tests") or [] for arg in shlex.split(line)
             if arg.split("::")[0].endswith(".py")}
    names = {*mutated, *(explained.get("reads") or []), mutants, evidence, "packaging/mutants.py",
             "packaging/checkers.py", "pyproject.toml", "uv.lock", *walked(root, tests, f"{name}'s mutation testing")}
    missing = sorted(file for file in names if not (root / file).is_file())
    if missing:
        sys.exit(f"ci_reuse.py: {name}'s mutation testing reads {', '.join(missing)}, which is not there")
    return {file: (root / file).read_bytes() for file in names}


def pick(lines, repo: int, days: float, now: dt.datetime, names: list[str]) -> int | None:
    """The newest run (the highest id) that has every name among the artifacts it may use: not expired, uploaded by
    this repository, less than `days` days old."""
    found: dict[int, set] = {}
    for line in lines:
        if line.strip():
            artifact = json.loads(line)
            run = artifact.get("workflow_run") or {}
            if (artifact["name"] in names and not artifact["expired"] and run.get("head_repository_id") == repo
                    and now - dt.datetime.fromisoformat(artifact["created_at"]) < dt.timedelta(days=days)):
                found.setdefault(run["id"], set()).add(artifact["name"])
    return max((run for run, has in found.items() if has >= set(names)), default=None)


def mark(folder: Path, origin: dict, commit: str | None) -> int:
    files = sorted(folder.rglob("checkers.json"))
    if not files:
        sys.exit(f"ci_reuse.py: no checkers.json under {folder}")
    for file in files:
        records = json.loads(file.read_text(encoding="utf-8"))
        for record in records:
            record.setdefault("reused_from", {**origin, "commit": commit or record["commit"]})
        file.write_text(json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


def select(root: Path, features: str | None) -> list[str]:
    """`<name> <parts> <check>` of each mutation feature `features` names (all, when it is empty), sorted by name."""
    parts = {}
    for path in sorted((root / "docs" / "promises").glob("*.mutants.yaml")):
        name = path.name.removesuffix(".mutants.yaml")
        parts[name] = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("parts", 1)
        if type(parts[name]) is not int or parts[name] < 1:  # (YAML's true is a bool, which Python counts as an int)
            raise ValueError(f"{name}: parts: {parts[name]!r} in {path.relative_to(root).as_posix()} is not a whole "
                             "number of 1 or more")
    named = set(re.split(r"[,\s]+", features.strip())) - {""} if features else set(parts)
    unknown = sorted(named - set(parts))
    if not named or unknown:
        raise ValueError((f"no feature {', '.join(unknown)}" if unknown else f"{features!r} names no feature")
                         + f"; the features: {', '.join(parts)}")
    return [f"{name} {parts[name]} {'mutation' if name == 'scheduler' else f'feature:{name}'}"
            for name in sorted(named)]


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    do = p.add_subparsers(dest="do", required=True)
    for name in ("key", "files"):
        sub = do.add_parser(name)
        sub.add_argument("check", help="model, mutation or feature:<name>")
        sub.add_argument("--root", type=Path, default=ROOT)
        if name == "key":
            sub.add_argument("--salt", default="")
    sub = do.add_parser("pick")
    sub.add_argument("names", nargs="+", metavar="NAME")
    sub.add_argument("--repo-id", type=int, required=True)
    sub.add_argument("--days", type=float, default=30)
    sub.add_argument("--now", type=dt.datetime.fromisoformat)
    sub = do.add_parser("mark")
    sub.add_argument("folder", type=Path)
    sub.add_argument("--run", required=True)
    sub.add_argument("--url", required=True)
    sub.add_argument("--date", required=True)
    sub.add_argument("--commit")
    sub = do.add_parser("select")
    sub.add_argument("--features", metavar="NAMES")
    sub.add_argument("--root", type=Path, default=ROOT)
    a = p.parse_args(argv)
    if a.do == "select":
        try:
            print("\n".join(select(a.root.resolve(), a.features)))
        except ValueError as refused:
            print(f"ci_reuse.py select: {refused}", file=sys.stderr)
            return 2
        return 0
    if a.do == "mark":
        return mark(a.folder, {"run": a.run, "url": a.url, "date": a.date}, a.commit)
    if a.do == "pick":
        run = pick(sys.stdin, a.repo_id, a.days, a.now or dt.datetime.now(dt.timezone.utc), a.names)
        if run is not None:
            print(run)
        return 0
    if a.check in ("model", "mutation"):
        read = (model if a.check == "model" else mutation)(a.root.resolve())
    elif a.check.startswith("feature:"):
        read = feature(a.root.resolve(), a.check.removeprefix("feature:"))
    else:
        p.error(f"no check {a.check}: model, mutation or feature:<name>")
    if a.do == "files":
        print("\n".join(sorted(read)))
        return 0
    key = hashlib.sha256()
    for name in sorted(read):
        key.update(name.encode() + b"\0" + hashlib.sha256(read[name]).digest())
    key.update(b"\0" + a.salt.encode())
    print(key.hexdigest()[:32])
    return 0


if __name__ == "__main__":
    sys.exit(main())
