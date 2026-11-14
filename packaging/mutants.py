"""The mutation testing result of a feature, from mutmut's own records: its score and each mutant the tests missed.

    python packaging/mutants.py PROJECT_DIR --explained docs/promises/scheduler.mutants.yaml [--out mutants.json]
    python packaging/mutants.py . --run [--part K/N] [--workers W] --explained ...scheduler.mutants.yaml [--out ...]
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
mutate: the functions as where() names them (or a file: every function in it), and tests: the tests run on each of
their mutants. Each entry of tests: is one pytest run, written as on its command line: a file or several, with -k
and its words or without (e.g. `tests/test_paths_data.py -k cleaned_up`). An entry of mutate: may itself be a
mapping of mutate: and tests:, a group of functions with tests of its own, so that a mutant runs only the tests
that can see it. The run is in PROJECT_DIR/build/mutation/<feature> (the --explained file's name up to its first
dot; --run makes it anew): a copy of what pyproject.toml has mutmut copy, with the same settings but mutating only
the scope's files. Only the mutants of what the scope names count; what they were planted in is listed with how many
each, and an entry nothing was planted in (misspelt, renamed, or not a function: mutmut plants in functions only)
fails.

mutmut does not judge a scope's mutants itself. It runs a mutant only on the tests that called its function inside
the pytest process, and the tests of the command line and of the editor reach the code in child processes (each run
of a flow is one), so it finds no test for any of them. For a scope, --run has mutmut plant the mutants (its copy of
the code in mutants/, where an environment variable says which mutant is on; only in the functions the scope names,
so the others in their files stay as written) and runs the tests itself: pytest -x -q in that copy, with the copy
first on PYTHONPATH and the mutant's name in MUTANT_UNDER_TEST, so the tests and every process they start take it.
--workers of these runs go at a time (default: this machine's cores), each with a temporary folder of its own. A run
that fails (or whose process dies) catches the mutant and one that passes misses it; one still going at the time
limit (mutmut's: the tests' time with no mutant plus timeout_constant, times timeout_multiplier) is ended with every
process it started, and catches it. Before any mutant, every worker runs the tests at once with no mutant on: if one
of these runs fails, no mutant is judged (such tests would catch every mutant), and the slowest is the tests' time.
The verdicts go into mutmut's records, which the score is read from as after `mutmut run`; the output of each
mutant's tests is in logs/ next to mutants/.

Without a scope, --run first runs `mutmut run` (configured in pyproject.toml) here. Either way it is on every mutant
that counts: all mutmut plants in the files it mutates (listed by mutmut's own code, so none is missed) but the
not_tested ones. The scheduler's take hours, so CI runs them in parts on several machines: --part K/N runs only the
Kth of N parts, every Nth of those mutants (so each part has some of every function's and the parts take about as
long), and gives no score, as one part proves nothing. Its exit code is 1 when the run failed or left one of the
part's mutants without a verdict. --parts then scores the parts as one run: PROJECT_DIR/mutants gets mutmut's
records of the first part (each PART_DIR holds one part's mutants/), with each mutant's verdict from the part that
judged it. A mutant no part judged could not be judged.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

THRESHOLD = 90
CAUGHT = {1, 3, 36, 24, -24, 152, 255}  # a test failed (3: pytest's own error), or the time limit ended it
MISSED = {0, 5, 33}  # the tests passed, or none ran that code
TIME_LIMIT, NO_TESTS = 36, 33  # mutmut's codes for these two
# What `mutmut run` does before it runs anything: its copy of the code in mutants/, with the mutants of the files it
# mutates, here only those on the lines given (file -> lines; mutmut's own way to leave lines out, which it uses for
# lines no test runs). In a process of its own, started in the project: mutmut reads its settings from where it is
# imported.
PLANT = """\
import json
import sys
from pathlib import Path

from mutmut.__main__ import create_mutants_for_file
from mutmut.state import state
from mutmut.utils.file_utils import copy_also_copy_files, copy_src_dir, walk_mutatable_files

Path("mutants").mkdir()
copy_src_dir()
copy_also_copy_files()
state()._covered_lines = {str((Path("mutants") / file).absolute()): set(lines)
                          for file, lines in json.loads(sys.argv[1]).items()}
for path in walk_mutatable_files():
    result = create_mutants_for_file(path, Path("mutants") / path)
    if result.error:
        raise result.error
"""


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


def tests_of(scope: dict) -> dict[str, list[str]]:
    """Each function (or file) the scope names -> the tests run on its mutants: those of its group (an entry of
    mutate: that is itself mutate: and tests:), or the scope's."""
    tests = {}
    for entry in scope["mutate"]:
        if isinstance(entry, dict):
            tests |= dict.fromkeys(entry["mutate"], entry["tests"])
        else:
            tests[entry] = scope.get("tests") or []
    return tests


def inside(name: str, scope: dict | None) -> bool:
    """Whether the mutant is of a function the scope names, or of a file it names whole (no scope: every mutant)."""
    return scope is None or where(name) in scope["mutate"] or where(name).split(" ")[0] in scope["mutate"]


def lines_of(project: Path, file: str, scope: dict) -> list[int]:
    """The lines of the file's functions (and methods of its classes) that the scope names; all, if it names the
    file whole."""
    source = (project / file).read_text(encoding="utf-8")
    if file in scope["mutate"]:
        return list(range(1, len(source.splitlines()) + 1))
    lines = []
    for node in ast.parse(source).body:
        for function in [node, *(node.body if isinstance(node, ast.ClassDef) else [])]:
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = function.name if function is node else f"{node.name}.{function.name}"
                if f"{file} {name}" in scope["mutate"]:
                    lines += range(function.lineno, function.end_lineno + 1)
    return lines


def scoped(project: Path, feature: str, scope: dict, anew: bool) -> Path:
    """Where mutmut runs for a feature with a scope, project/build/mutation/<feature>: a copy of what project's
    pyproject.toml has mutmut copy (its source_paths and also_copy, and tests/), with a pyproject.toml of its own:
    project's pytest and mutmut settings, but mutating only the scope's files."""
    work = project / "build" / "mutation" / feature
    if anew:
        shutil.rmtree(work, ignore_errors=True)
    if not work.exists():
        tool = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
        mutmut = {**tool["mutmut"], "only_mutate": sorted({entry.split(" ")[0] for entry in scope["mutate"]})}
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


def judge(project: Path, scope: dict, names: list[str], workers: int) -> int:
    """A scope's own run of these mutants, `workers` at a time: mutmut plants them, then each one's tests run with
    it on, in the pytest process and in every process that starts; the verdicts go into mutmut's records. 1, and
    no verdict, if the tests fail with no mutant on. mutmut plants only in the functions the scope names: it wraps
    each function it plants in, and the wrapper takes any arguments, so code that looks at what a function takes
    would see something else in all of the file (Qt gives a slot as many of a signal's values as it takes)."""
    settings = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"]
    lines = {file: lines_of(project, file, scope) for file in settings["only_mutate"]}
    subprocess.run([sys.executable, "-c", PLANT, json.dumps(lines)], cwd=project, check=True,
                   stdout=subprocess.DEVNULL)
    copy = (project / "mutants").resolve()
    path = os.pathsep.join(filter(None, [str(copy), os.environ.get("PYTHONPATH")]))
    (project / "logs").mkdir()

    def tests(item: str, mutant: str, log: str, limit: float | None) -> tuple[int, float]:
        """One pytest run with the mutant on ("": none) -> its exit code (TIME_LIMIT: ended there), its seconds."""
        started = time.monotonic()
        with (tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp,
              (project / "logs" / f"{log}.txt").open("ab") as out):
            run = subprocess.Popen(
                [sys.executable, "-m", "pytest", "-x", "-q", "--rootdir=.", *settings.get("pytest_add_cli_args", []),
                 *shlex.split(item)], cwd=copy, stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
                # (not the options of a pytest run this one is started from, e.g. which tests to run)
                env={**{k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"},
                     "MUTANT_UNDER_TEST": mutant, "PYTHONPATH": path, "TMPDIR": tmp})
            try:
                code = run.wait(limit)
            except subprocess.TimeoutExpired:
                os.killpg(run.pid, signal.SIGKILL)  # (pytest and all it started: a mutant may hang any of them)
                run.wait()
                code = TIME_LIMIT
        return (1 if code < 0 else code), time.monotonic() - started  # (below 0: the tests' own process died)

    def own(name: str) -> list[str]:
        return scope["mutate"][where(name) if where(name) in scope["mutate"] else where(name).split(" ")[0]]

    def verdict(name: str) -> int:
        code = NO_TESTS
        for item in own(name):
            code, _ = tests(item, name, name, (clean[item] + settings.get("timeout_constant", 1.0))
                            * settings.get("timeout_multiplier", 15.0))
            if code:
                break
        return code

    started = time.monotonic()
    with ThreadPoolExecutor(workers) as pool:
        clean = dict.fromkeys((item for name in names for item in own(name)), 0.0)
        runs = [(item, f"no-mutant-{n}") for n, item in enumerate(list(clean) * workers, 1)]
        failed = {}
        for (item, log), (code, seconds) in zip(runs, pool.map(lambda run: tests(run[0], "", run[1], None), runs)):
            clean[item] = max(clean[item], seconds)
            if code:
                failed.setdefault(item, (code, log))
        for item, (code, log) in failed.items():
            print(f"{item}: the tests fail with no mutant planted (exit {code}), so no mutant is judged. The end of "
                  f"their output:\n" + "\n".join((project / "logs" / f"{log}.txt").read_text(
                      encoding="utf-8", errors="replace").splitlines()[-15:]), flush=True)
        if failed:
            return 1
        print("the tests pass with no mutant planted: " + ", ".join(
            f"{item} within {seconds:.0f} s" for item, seconds in clean.items()), flush=True)
        codes = {}
        for name, code in zip(names, pool.map(verdict, names)):
            codes[name] = code
            print(f"{name}: " + ("missed" if code in MISSED else "caught, at the time limit" if code == TIME_LIMIT
                                 else "caught" if code in CAUGHT else f"exit {code}"), flush=True)
    for meta in sorted(copy.rglob("*.meta")):
        data = json.loads(meta.read_text(encoding="utf-8"))
        data["exit_code_by_key"] |= {name: codes[name] for name in data["exit_code_by_key"] if name in codes}
        meta.write_text(json.dumps(data), encoding="utf-8")
    print(f"{len(names):,} mutants in {time.monotonic() - started:.0f} s, {workers} at a time", flush=True)
    return 0


def run(project: Path, explained: dict, part: tuple[int, int], workers: int) -> int:
    """Part k of n of the mutants that count (1 of 1: all of them), by `mutmut run` or, for a scope, by judge(); 1
    if the run failed or left one of them without a verdict."""
    k, n = part
    names = counted(project, explained)
    mine = names[k - 1::n]
    print(f"part {k} of {n}: {len(mine):,} of the {len(names):,} mutants that count", flush=True)
    if not mine:  # (mutmut run without names would run them all)
        return 0
    shutil.rmtree(project / "mutants", ignore_errors=True)  # (mutmut keeps a copied file it finds there: maybe old)
    if explained.get("scope"):
        code = judge(project, explained["scope"], mine, workers)
    else:
        code = subprocess.run([sys.executable, "-m", "mutmut", "run", *mine], cwd=project).returncode
    codes = results(project)
    left = [name for name in mine if codes.get(name) is None]
    print(f"part {k} of {n}: the run ended with exit {code}; {len(mine) - len(left):,} of its {len(mine):,} mutants "
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
    p.add_argument("--workers", type=int, default=os.cpu_count())
    a = p.parse_args(argv)
    part = re.fullmatch(r"(\d+)/(\d+)", a.part or "1/1")
    if not part or not 1 <= int(part[1]) <= int(part[2]) or (a.part and not a.run) or (a.run and a.parts):
        p.error("--part K/N (1 <= K <= N) goes with --run, and --parts without it")
    explained = (yaml.safe_load(a.explained.read_text(encoding="utf-8")) or {}) if a.explained.exists() else {}
    if explained.get("scope"):
        explained["scope"]["mutate"] = tests_of(explained["scope"])
        a.project = scoped(a.project, a.explained.name.split(".")[0], explained["scope"], anew=a.run)
    ran = 0
    if a.run:
        ran = run(a.project, explained, (int(part[1]), int(part[2])), a.workers)
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
