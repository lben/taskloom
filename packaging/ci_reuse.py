"""When CI may take the scheduler's TLA+ model check, or its mutation testing, from an earlier run instead of running
it again (about 2 hours, and 3 times 1 hour): when every file it reads is byte for byte as it was there.

    python packaging/ci_reuse.py key model|mutation [--root DIR] [--salt TEXT]
    python packaging/ci_reuse.py files model|mutation [--root DIR]
    python packaging/ci_reuse.py mark DIR --run RUN --url URL --date YYYY-MM-DD [--commit SHA]

key prints a key over the contents of the files the check reads, and TEXT (CI passes the versions of the tools it
runs with); CI keeps a check's result under its key and looks for it there. files prints those files:
- model: the spec and its config, the model check's entry in the scheduler's evidence (its command and key lines; not
  the rows' words or the other checkers) and packaging/checkers.py, which writes its record.
- mutation: the files mutmut mutates, pyproject.toml, uv.lock, packaging/mutants.py, the scheduler's mutants yaml, the
  test files pyproject.toml selects, every tests/ file they import (followed through) and tests/conftest.py. Not the
  rest of taskloom/, README.md or the docs: the user's rule is "when no scheduler file changed"; the proof PDF says
  that other files may have changed, and the test job runs the scheduler's tests on every commit.
mark adds to every checker record under DIR (each checkers.json) the run it was taken from, its link, its date and
the commit it checked (SHA, or the record's own), for the proof PDF to name; a record that already says where it
comes from keeps that.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
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


def mutation(root: Path) -> dict:
    """What mutation testing reads: path -> its bytes."""
    settings = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"]
    selected = {arg.split("::")[0] for arg in settings.get("pytest_add_cli_args_test_selection", [])
                if arg.split("::")[0].endswith(".py")}
    todo = [root / name for name in sorted(selected)] + [root / "tests" / "conftest.py"]
    tests = set()
    while todo:
        file = todo.pop()
        if file not in tests:
            if not file.is_file():
                sys.exit(f"ci_reuse.py: mutation testing reads {file.relative_to(root).as_posix()}, which is not there")
            tests.add(file)
            todo += imported(root, file)
    names = {*settings.get("only_mutate", []), "pyproject.toml", "uv.lock", "packaging/mutants.py",
             "docs/promises/scheduler.mutants.yaml", *(f.relative_to(root).as_posix() for f in tests)}
    return {name: (root / name).read_bytes() for name in names}


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


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    do = p.add_subparsers(dest="do", required=True)
    for name in ("key", "files"):
        sub = do.add_parser(name)
        sub.add_argument("check", choices=["model", "mutation"])
        sub.add_argument("--root", type=Path, default=ROOT)
        if name == "key":
            sub.add_argument("--salt", default="")
    sub = do.add_parser("mark")
    sub.add_argument("folder", type=Path)
    sub.add_argument("--run", required=True)
    sub.add_argument("--url", required=True)
    sub.add_argument("--date", required=True)
    sub.add_argument("--commit")
    a = p.parse_args(argv)
    if a.do == "mark":
        return mark(a.folder, {"run": a.run, "url": a.url, "date": a.date}, a.commit)
    read = (model if a.check == "model" else mutation)(a.root.resolve())
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
