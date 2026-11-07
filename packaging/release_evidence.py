"""The release-wide numbers on the proof's first page (proof/<release>/release.json), from the evidence CI gathered.

    python packaging/release_evidence.py proof/<release> RESULTS.json [RESULTS.json ...]

RESULTS.json: one per platform, from a test run with TASKLOOM_LEDGER=<file> (tests/conftest.py): every test's
outcome, and those that check a row or a charter rule. The features' own results are read from proof/<release>/
<feature>/ (checkers.json, mutants.json, leak.json); design health is measured on the code as it is.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))
import design_health  # noqa: E402
from ledger import PLATFORMS  # noqa: E402

from taskloom import __version__  # noqa: E402


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def release(evidence: Path, results: list[dict]) -> dict:
    features = [d for d in sorted(evidence.iterdir()) if d.is_dir() and (d / "checkers.json").exists()]
    mutants = [m for d in features if (m := read(d / "mutants.json"))]
    leaks = [leak for d in features if (leak := read(d / "leak.json"))]
    checkers = [c for d in features for c in read(d / "checkers.json")]
    killed, total = sum(m["killed"] for m in mutants), sum(m["total"] for m in mutants)
    platforms = {PLATFORMS.get(r["platform"], r["platform"]): r["totals"] for r in results}
    rules = {}
    for r in results:
        for t in r["tests"]:
            for rule in t["charter"]:
                rules[rule] = rules.get(rule, True) and t["outcome"] in ("passed", "skipped")
    health = design_health.report()
    return {
        "version": __version__,
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                 timeout=30).stdout.strip(),
        "mutation": {"score": 100 * killed / total if total else 0.0, "killed": killed, "total": total},
        "tests": {**{o: sum(p[o] for p in platforms.values()) for o in ("passed", "failed", "skipped")},
                  "platforms": platforms},
        "checkers": {"run": len(checkers), "passed": sum(c["exit"] == 0 for c in checkers)},
        "leak": {"passed": bool(leaks) and all(leak["passed"] for leak in leaks),
                 "summary": "; ".join(leak["summary"] for leak in leaks) or "No leak check ran."},
        "charter": {"rules_checked": len(rules), "violations": sum(not ok for ok in rules.values())},
        "design_health": {"over_points": health["over_points"], "over_lines": health["over_lines"],
                          "kept": health["kept"]},
    }


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    evidence = Path(argv[0])
    built = release(evidence, [json.loads(Path(f).read_text(encoding="utf-8")) for f in argv[1:]])
    (evidence / "release.json").write_text(json.dumps(built, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in built.items() if k != "design_health"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
