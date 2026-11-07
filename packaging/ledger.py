"""The evidence ledger of a feature, computed from the test results and the checkers' results, never written by hand.

    python packaging/ledger.py docs/promises/scheduler.yaml RESULTS.json [RESULTS.json ...] [--checkers C.json] [--out L.json]

RESULTS.json files come from a test run with TASKLOOM_LEDGER=<file> (tests/conftest.py), one per platform. Each row
of the agreed list gets a status:
- GREEN: checked for every input or state. A checker that explores every state passed (named in the row's
  green_by), or a test that tries every case of a stated range passed (marked invariant(..., every="<range>")),
  and every other check of the row passed.
- AMBER: checked by tests only, all passing; the row's not_green sentence says why it is not green.
- RED: no check, a check failed, or AMBER without a reason.
Done needs no RED row: the exit code is 1 otherwise.
The words of how each row is enforced, and why it is not green, are in the feature's <name>.evidence.yaml next to
the agreed list; the reviewer checks them against the code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

PLATFORMS = {"win32": "Windows", "linux": "Linux", "darwin": "macOS"}


def merge(results: list[dict]) -> dict:
    """test id -> {rows, every, outcomes: {platform: (outcome, why)}}, over the platforms' result files."""
    tests = {}
    for result in results:
        platform = PLATFORMS.get(result["platform"], result["platform"])
        for t in result["tests"]:
            entry = tests.setdefault(t["id"], {"rows": t["rows"], "every": t["every"], "outcomes": {}})
            entry["outcomes"][platform] = (t["outcome"], t["why"])
    return tests


def row_status(row: str, evidence: dict, tests: dict, checkers: dict) -> dict:
    mine = {tid: t for tid, t in tests.items() if row in t["rows"]}
    ran = {tid: t for tid, t in mine.items() if any(o == "passed" for o, _ in t["outcomes"].values())}
    failed = [(tid, p, why) for tid, t in mine.items() for p, (o, why) in t["outcomes"].items()
              if o in ("failed", "not run")]
    named = evidence.get("green_by") or []
    checker_runs = [checkers[name] for name in named if name in checkers]
    broken = [c for c in checker_runs if c["exit"] != 0] + [{"name": n, "exit": "not run"} for n in named
                                                            if n not in checkers]
    every = sorted({e for t in ran.values() for e in t["every"]})
    platforms = sorted({p for t in ran.values() for p, (o, _) in t["outcomes"].items() if o == "passed"})
    checked = f"{len(ran)} test(s) passed on {', '.join(platforms) or 'no platform'}" if ran else "no test"
    if checker_runs:
        checked += "; " + "; ".join(f"{c['name']}: {c['summary']}" for c in checker_runs if c["exit"] == 0)
    status, why = "AMBER", evidence.get("not_green", "")
    if failed or broken:
        status = "RED"
        why = "; ".join([f"{tid.split('::')[-1]} {o_platform}: {w}" for tid, o_platform, w in failed[:3]] +
                        [f"{c['name']} exit {c['exit']}" for c in broken])
    elif not ran and not checker_runs:
        status, why = "RED", "no check covers this row yet"
    elif (checker_runs and not broken) or every:
        status, why = "GREEN", "; ".join(every)
    elif not why:
        status, why = "RED", "checked by tests only, and no reason is given why it is not green"
    return {"row": row, "status": status, "enforced": evidence.get("enforced", ""), "checked": checked, "why": why,
            "tests": sorted(mine)}


def ledger(promises: Path, results: list[dict], checkers: list[dict] | None = None) -> dict:
    agreed = yaml.safe_load(promises.read_text(encoding="utf-8"))
    evidence_file = promises.with_name(promises.stem + ".evidence.yaml")
    evidence = yaml.safe_load(evidence_file.read_text(encoding="utf-8")) if evidence_file.exists() else {}
    tests = merge(results)
    by_name = {c["name"]: c for c in checkers or []}
    rows = [{"text": " ".join(str(text).split()), **row_status(row, (evidence or {}).get(row) or {}, tests, by_name)}
            for row, text in agreed["rows"].items()]
    counts = {s: sum(r["status"] == s for r in rows) for s in ("GREEN", "AMBER", "RED")}
    commits = sorted({r["commit"] for r in results})
    return {"feature": agreed["feature"], "agreed": str(agreed["agreed"]), "rows": rows, "counts": counts,
            "commits": commits, "checkers": checkers or []}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("promises", type=Path)
    p.add_argument("results", type=Path, nargs="+")
    p.add_argument("--checkers", type=Path, action="append", default=[])
    p.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    checkers = [c for f in a.checkers for c in json.loads(f.read_text(encoding="utf-8"))]
    built = ledger(a.promises, [json.loads(f.read_text(encoding="utf-8")) for f in a.results], checkers)
    if a.out:
        a.out.write_text(json.dumps(built, indent=1), encoding="utf-8")
    for r in built["rows"]:
        print(f"{r['row']:7} {r['status']:5} {r['checked']}" + (f" — {r['why']}" if r["why"] else ""))
    print(f"{built['counts']} on {', '.join(c[:7] for c in built['commits'])}")
    return 1 if built["counts"]["RED"] else 0


if __name__ == "__main__":
    sys.exit(main())
