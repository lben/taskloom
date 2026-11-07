"""Design health (charter C14): the functions over a limit, and whether each one is allowed.

    python packaging/design_health.py          # the report; exit code 1 when a function is over a limit unexplained

A function is over a limit with more than 25 decision points (each if, loop, error handler, and, or) or more than
80 lines. One over a limit in the last release is listed under `baseline` in docs/design_health.yaml; one newly over
a limit must be split, or listed under `kept` with a one-line reason the reviewer accepts.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LIST = ROOT / "docs" / "design_health.yaml"
MAX_POINTS = 25
MAX_LINES = 80
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def decision_points(function: ast.AST) -> int:
    """Each if, loop, error handler, and, or in the function's own body (not in the functions defined in it)."""
    count, todo = 0, list(ast.iter_child_nodes(function))
    while todo:
        node = todo.pop()
        if isinstance(node, _NESTED):
            continue
        if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.match_case)):
            count += 1
        elif isinstance(node, ast.comprehension):
            count += 1 + len(node.ifs)
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1
        todo.extend(ast.iter_child_nodes(node))
    return count


def functions(root: Path = ROOT / "taskloom"):
    """(name, decision points, lines) of every function and method; name is file:Class.method or file:outer.inner."""
    def walk(node, prefix, path):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                yield f"{path}:{name}", decision_points(child), child.end_lineno - child.lineno + 1
                yield from walk(child, name + ".", path)
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.", path)
            else:
                yield from walk(child, prefix, path)

    for file in sorted(root.rglob("*.py")):
        path = file.relative_to(ROOT).as_posix()
        yield from walk(ast.parse(file.read_text(encoding="utf-8")), "", path)


def report(listed: dict | None = None) -> dict:
    """The functions over a limit, and those of them neither in the last release's list nor kept with a reason."""
    listed = listed if listed is not None else (yaml.safe_load(LIST.read_text(encoding="utf-8")) or {})
    baseline, kept = set(listed.get("baseline") or []), dict(listed.get("kept") or {})
    over = [(name, points, lines) for name, points, lines in functions()
            if points > MAX_POINTS or lines > MAX_LINES]
    names = {name for name, _, _ in over}
    return {
        "over_points": sum(points > MAX_POINTS for _, points, _ in over),
        "over_lines": sum(lines > MAX_LINES for _, _, lines in over),
        "over": over,
        "unexplained": [row for row in over if row[0] not in baseline and row[0] not in kept],
        "kept": {name: reason for name, reason in kept.items() if name in names},
        "no_longer_over": sorted((baseline | set(kept)) - names),
    }


def main() -> int:
    r = report()
    print(f"functions with more than {MAX_POINTS} decision points: {r['over_points']}")
    print(f"functions longer than {MAX_LINES} lines: {r['over_lines']}")
    for name, reason in r["kept"].items():
        print(f"kept: {name}: {reason}")
    for name in r["no_longer_over"]:
        print(f"no longer over a limit (take it off {LIST.name}): {name}")
    for name, points, lines in r["unexplained"]:
        print(f"NEW over a limit, split it or keep it with a reason: {name} ({points} decision points, {lines} lines)")
    return 1 if r["unexplained"] else 0


if __name__ == "__main__":
    sys.exit(main())
