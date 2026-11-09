"""The mutation testing result of a feature, from mutmut's own records: its score and each mutant the tests missed.

    python packaging/mutants.py PROJECT_DIR --explained docs/promises/scheduler.mutants.yaml [--out mutants.json]

PROJECT_DIR is where `mutmut run` ran (it holds mutants/). A mutant is a small bug planted in the code on purpose:
- caught: a test failed, or the tests did not finish in time (the bug made them hang);
- missed: every test passed, or no test ran that code.
The score is the share caught. It passes at 90% or more when every missed mutant has a one-line reason in the
--explained file (mutant name: why it changes nothing a user could notice). It fails, too, when mutmut could not
judge a mutant (the tests crashed or ended in a way it does not know): such a run proves nothing.

The --explained file may also have a not_tested: mapping, of functions as where() names them (e.g.
`taskloom/scheduler.py serve`, `taskloom/scheduler.py Scheduler.check_servers`; a nested function's mutants are its
outer function's) to a one-line reason: why mutation testing leaves them out, and which tests and proofs cover them
instead. Their mutants count in neither the score, the missed nor the could-not-judge ones; each such function is
listed, in the output and the JSON (not_tested: function, reason, number of mutants), so none is left out unseen.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

THRESHOLD = 90
CAUGHT = {1, 3, 36, 24, -24, 152, 255}  # a test failed (3: pytest's own error), or the time limit ended it
MISSED = {0, 5, 33}  # the tests passed, or none ran that code


def results(project: Path) -> dict[str, int | None]:
    """mutant name -> the exit code of its tests, for every mutant of every mutated file."""
    codes = {}
    for meta in sorted((project / "mutants").rglob("*.meta")):
        codes |= json.loads(meta.read_text(encoding="utf-8"))["exit_code_by_key"]
    return codes


def where(name: str) -> str:
    """taskloom.schedule.xǁCronǁnext_after__mutmut_2 -> taskloom/schedule.py Cron.next_after (mutmut's naming: x_ and
    the function, or xǁ, the class, ǁ and the method)."""
    m = re.fullmatch(r"(.+?)\.x(?:_(.+)|ǁ([^ǁ]+)ǁ(.+))__mutmut_\d+", name)
    if not m:
        return name
    module, function, cls, method = m.groups()
    return f"{module.replace('.', '/')}.py {function or f'{cls}.{method}'}"


def change(project: Path, name: str) -> str:
    """The planted change, as its removed and added lines."""
    shown = subprocess.run([sys.executable, "-m", "mutmut", "show", name], cwd=project, capture_output=True,
                           text=True, timeout=120).stdout
    lines = [line[1:].split("  #")[0].strip() for line in shown.splitlines() if re.match(r"^[-+](?![-+])", line)]
    return " → ".join(lines) or name


def summary(project: Path, explained: dict) -> dict:
    not_tested = explained.get("not_tested") or {}
    codes = {}
    left_out = dict.fromkeys(not_tested, 0)
    for name, code in results(project).items():
        if where(name) in left_out:
            left_out[where(name)] += 1
        else:
            codes[name] = code
    unjudged = sorted(name for name, code in codes.items() if code not in CAUGHT | MISSED)
    missed = sorted(name for name, code in codes.items() if code in MISSED)
    killed = sum(code in CAUGHT for code in codes.values())
    judged = killed + len(missed)
    score = 100 * killed / judged if judged else 0.0
    survivors = [{"where": where(name), "name": name, "change": change(project, name),
                  "why_equivalent": explained.get(name, "")} for name in missed]
    unexplained = [s["name"] for s in survivors if not s["why_equivalent"]]
    passed = bool(judged) and score >= THRESHOLD and not unexplained and not unjudged
    return {"score": round(score, 1), "killed": killed, "total": judged, "threshold": THRESHOLD, "passed": passed,
            "survivors": survivors, "unexplained": unexplained,
            "unjudged": [f"{name} (exit {codes[name]})" for name in unjudged],
            "not_tested": [{"function": function, "reason": reason, "mutants": left_out[function]}
                           for function, reason in not_tested.items()]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("project", type=Path)
    p.add_argument("--explained", type=Path, required=True)
    p.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    explained = (yaml.safe_load(a.explained.read_text(encoding="utf-8")) or {}) if a.explained.exists() else {}
    result = summary(a.project, explained)
    if a.out:
        a.out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    for s in result["survivors"]:
        print(f"missed {s['name']}: {s['change']}" + (f" — {s['why_equivalent']}" if s["why_equivalent"]
                                                      else " — NO REASON GIVEN"))
    for name in result["unjudged"]:
        print(f"could not judge {name}")
    for n in result["not_tested"]:
        print(f"not tested {n['function']} ({n['mutants']:,} mutants, left out of the score) — {n['reason']}")
    print(("PASSED: " if result["passed"] else "FAILED: ") + f"mutation score {result['score']:g}%: the tests caught "
          f"{result['killed']:,} of {result['total']:,} (bar {THRESHOLD}%); {len(result['survivors'])} missed, "
          f"{len(result['unexplained'])} without a reason; {len(result['unjudged'])} could not be judged")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
