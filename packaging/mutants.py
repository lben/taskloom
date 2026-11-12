"""The mutation testing result of a feature, from mutmut's own records: its score and each mutant the tests missed.

    python packaging/mutants.py PROJECT_DIR --explained docs/promises/scheduler.mutants.yaml [--out mutants.json]
    python packaging/mutants.py . --run [--part K/N] --explained docs/promises/scheduler.mutants.yaml [--out ...]
    python packaging/mutants.py PROJECT_DIR --parts PART_DIR [PART_DIR ...] --explained ... [--out mutants.json]

PROJECT_DIR is where `mutmut run` ran (it holds mutants/). A mutant is a small bug planted in the code on purpose:
- caught: a test failed, or the tests did not finish in time (the bug made them hang);
- missed: every test passed, or no test ran that code.
The score is the share caught. It passes at 90% or more when every missed mutant has a one-line reason in the
--explained file (mutant name: why it changes nothing a user could notice). It fails, too, when mutmut could not
judge a mutant (the tests crashed or ended in a way it does not know): such a run proves nothing.

Missed mutants of one kind may share one reason, in place of the same line for each: a kinds: list, each entry a
kind (a few words), its why, and mutants: the names it explains.

The --explained file may also have a not_tested: mapping, of functions as where() names them (e.g.
`taskloom/scheduler.py serve`, `taskloom/scheduler.py Scheduler.check_servers`; a nested function's mutants are its
outer function's), or of single mutants by name (e.g. a Windows-only line, which no run here can execute), to a one-
line reason: why mutation testing leaves them out, and which tests and proofs cover them instead. Their mutants
count in neither the score, the missed nor the could-not-judge ones; each such function is listed, in the output and
the JSON (not_tested: function, reason, number of mutants), so none is left out unseen.

A bug fix or small feature mutation-tests only what it changed. Its --explained file has a scope: mapping, of
mutate: the functions as where() names them (or a file: every function in it), and tests: what pytest is to select
(files, tests, -k and its expression, as pytest_add_cli_args_test_selection in pyproject.toml). mutmut then runs in
PROJECT_DIR/build/mutation/<feature> (the --explained file's name up to its first dot; --run makes it anew): a copy
of what pyproject.toml has mutmut copy, with the same settings but mutating only the scope's files and selecting its
tests. Only the mutants of what the scope names count; what they were planted in is listed with how many each, and
an entry nothing was planted in (misspelt, renamed, or not a function: mutmut plants in functions only) fails.

--run first runs `mutmut run` (configured in pyproject.toml) here, on every mutant that counts: all mutmut plants in
the files it mutates (listed by mutmut's own code, so none is missed) but the not_tested ones. It takes hours, so CI
runs it in parts on several machines: --part K/N runs only the Kth of N parts, every Nth of those mutants (so each
part has some of every function's and the parts take about as long), and gives no score, as one part proves nothing.
Its exit code is 1 when mutmut failed or left one of the part's mutants without a verdict. --parts then scores the
parts as one run: PROJECT_DIR/mutants gets mutmut's records of the first part (each PART_DIR holds one part's
mutants/), with each mutant's verdict from the part that judged it. A mutant no part judged could not be judged.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
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


def inside(name: str, scope: dict | None) -> bool:
    """Whether the mutant is of a function the scope names, or of a file it names whole (no scope: every mutant)."""
    return scope is None or where(name) in scope["mutate"] or where(name).split(" ")[0] in scope["mutate"]


def scoped(project: Path, feature: str, scope: dict, anew: bool) -> Path:
    """Where mutmut runs for a feature with a scope, project/build/mutation/<feature>: a copy of what project's
    pyproject.toml has mutmut copy (its source_paths and also_copy, and tests/), with a pyproject.toml of its own:
    project's pytest and mutmut settings, but mutating only the scope's files and selecting its tests."""
    work = project / "build" / "mutation" / feature
    if anew:
        shutil.rmtree(work, ignore_errors=True)
    if not work.exists():
        tool = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
        mutmut = {**tool["mutmut"], "only_mutate": sorted({entry.split(" ")[0] for entry in scope["mutate"]}),
                  "pytest_add_cli_args_test_selection": scope["tests"]}
        for path in [*mutmut["source_paths"], *mutmut.get("also_copy", []), "tests"]:
            if (project / path).is_dir():
                shutil.copytree(project / path, work / path, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                (work / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(project / path, work / path)
        # (a string, a number or a list of strings is written in TOML as in JSON)
        (work / "pyproject.toml").write_text("".join(
            f"[{table}]\n" + "".join(f"{key} = {json.dumps(value)}\n" for key, value in settings.items())
            for table, settings in (("tool.pytest.ini_options", tool["pytest"]["ini_options"]), ("tool.mutmut", mutmut))),
            encoding="utf-8")
    return work


def change(project: Path, name: str) -> str:
    """The planted change, as its removed and added lines."""
    shown = subprocess.run([sys.executable, "-m", "mutmut", "show", name], cwd=project, capture_output=True,
                           text=True, timeout=120).stdout
    lines = [line[1:].split("  #")[0].strip() for line in shown.splitlines() if re.match(r"^[-+](?![-+])", line)]
    return " → ".join(lines) or name


def counted(project: Path, explained: dict) -> list[str]:
    """The mutants that count, in mutmut's order (file, function, number): every one mutmut plants in the files it
    mutates in `project`, named as it names them, but those not_tested lists (or whose function it lists), and
    of them only what the scope names, when there is one."""
    from mutmut.mutation.file_mutation import mutate_file_contents
    from mutmut.utils.file_utils import walk_mutatable_files
    from mutmut.utils.format_utils import get_mutant_name

    not_tested = explained.get("not_tested") or {}
    with contextlib.chdir(project):  # (mutmut reads its configuration and the files from there)
        planted = [get_mutant_name(path, name) for path in sorted(walk_mutatable_files())
                   for name in mutate_file_contents(str(path), path.read_text(encoding="utf-8")).mutant_names]
    return [name for name in planted if inside(name, explained.get("scope")) and name not in not_tested
            and where(name) not in not_tested]


def run(project: Path, explained: dict, part: tuple[int, int]) -> int:
    """`mutmut run` on part k of n of the mutants that count (1 of 1: all of them); 1 if it failed or left one of
    them without a verdict."""
    k, n = part
    names = counted(project, explained)
    mine = names[k - 1::n]
    print(f"part {k} of {n}: {len(mine):,} of the {len(names):,} mutants that count", flush=True)
    if not mine:  # (mutmut run without names would run them all)
        return 0
    shutil.rmtree(project / "mutants", ignore_errors=True)  # (mutmut keeps a copied file it finds there: maybe old)
    code = subprocess.run([sys.executable, "-m", "mutmut", "run", *mine], cwd=project).returncode
    codes = results(project)
    left = [name for name in mine if codes.get(name) is None]
    print(f"part {k} of {n}: mutmut ended with exit {code}; {len(mine) - len(left):,} of its {len(mine):,} mutants "
          f"have a verdict" + (f", these do not: {', '.join(left)}" if left else ""), flush=True)
    return 1 if code or left else 0


def take_parts(project: Path, parts: list[Path]):
    """Into project/mutants: mutmut's records of the first part that has them (its copy of the code, every mutant
    planted, judged or not), with each mutant's verdict from the part that judged it."""
    parts = [part for part in parts if (part / "mutants").is_dir()]
    if not parts:
        return
    target = project / "mutants"
    shutil.copytree(parts[0] / "mutants", target, dirs_exist_ok=True)
    for meta in sorted(target.rglob("*.meta")):
        data = json.loads(meta.read_text(encoding="utf-8"))
        codes = data["exit_code_by_key"]
        for part in parts:
            theirs = part / "mutants" / meta.relative_to(target)
            if theirs.is_file():
                codes |= {name: code for name, code in
                          json.loads(theirs.read_text(encoding="utf-8"))["exit_code_by_key"].items()
                          if code is not None and name in codes and codes[name] is None}
        meta.write_text(json.dumps(data), encoding="utf-8")


def summary(project: Path, explained: dict) -> dict:
    not_tested = explained.get("not_tested") or {}
    scope = explained.get("scope")
    kinds = {name: kind for kind in explained.get("kinds") or [] for name in kind["mutants"]}
    codes = {}
    left_out = dict.fromkeys(not_tested, 0)
    planted = dict.fromkeys(scope["mutate"] if scope else [], 0)
    for name, code in results(project).items():
        if not inside(name, scope):
            continue
        if scope:
            planted[where(name) if where(name) in planted else where(name).split(" ")[0]] += 1
        if (key := name if name in left_out else where(name)) in left_out:
            left_out[key] += 1
        else:
            codes[name] = code
    unjudged = sorted(name for name, code in codes.items() if code not in CAUGHT | MISSED)
    missed = sorted(name for name, code in codes.items() if code in MISSED)
    killed = sum(code in CAUGHT for code in codes.values())
    judged = killed + len(missed)
    score = 100 * killed / judged if judged else 0.0
    survivors = [{"where": where(name), "name": name, "change": change(project, name),
                  "kind": kinds[name]["kind"] if name in kinds else "",
                  "why_equivalent": kinds[name]["why"] if name in kinds else explained.get(name, "")}
                 for name in missed]
    unexplained = [s["name"] for s in survivors if not s["why_equivalent"]]
    passed = bool(judged) and score >= THRESHOLD and not unexplained and not unjudged and all(planted.values())
    return {"score": round(score, 1), "killed": killed, "total": judged, "threshold": THRESHOLD, "passed": passed,
            "survivors": survivors, "unexplained": unexplained,
            "unjudged": [f"{name} (exit {codes[name]})" for name in unjudged],
            "scope": [{"function": function, "mutants": mutants} for function, mutants in planted.items()],
            "not_tested": [{"function": function, "reason": reason, "mutants": left_out[function]}
                           for function, reason in not_tested.items()]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("project", type=Path)
    p.add_argument("--explained", type=Path, required=True)
    p.add_argument("--out", type=Path)
    p.add_argument("--run", action="store_true")
    p.add_argument("--part", metavar="K/N")
    p.add_argument("--parts", nargs="+", type=Path, metavar="PART_DIR")
    a = p.parse_args(argv)
    part = re.fullmatch(r"(\d+)/(\d+)", a.part or "1/1")
    if not part or not 1 <= int(part[1]) <= int(part[2]) or (a.part and not a.run) or (a.run and a.parts):
        p.error("--part K/N (1 <= K <= N) goes with --run, and --parts without it")
    explained = (yaml.safe_load(a.explained.read_text(encoding="utf-8")) or {}) if a.explained.exists() else {}
    if explained.get("scope"):
        a.project = scoped(a.project, a.explained.name.split(".")[0], explained["scope"], anew=a.run)
    ran = 0
    if a.run:
        ran = run(a.project, explained, (int(part[1]), int(part[2])))
        if a.part:
            return ran
    for folder in a.parts or []:
        judged = sum(code is not None for code in results(folder).values()) if (folder / "mutants").is_dir() else 0
        print(f"{folder.as_posix()}: {judged:,} verdicts" + ("" if judged else " (no mutmut records)"))
    if a.parts:
        take_parts(a.project, a.parts)
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
    for n in result["scope"]:
        if not n["mutants"]:
            print(f"nothing planted in {n['function']}: misspelt, renamed, or not a function")
    if result["scope"]:
        print("planted in: " + ", ".join(f"{n['function']} ({n['mutants']:,})" for n in result["scope"]))
    print(("PASSED: " if result["passed"] else "FAILED: ") + f"mutation score {result['score']:g}%: the tests caught "
          f"{result['killed']:,} of {result['total']:,} (bar {THRESHOLD}%); {len(result['survivors'])} missed, "
          f"{len(result['unexplained'])} without a reason; {len(result['unjudged'])} could not be judged")
    return 0 if result["passed"] and not ran else 1


if __name__ == "__main__":
    sys.exit(main())
