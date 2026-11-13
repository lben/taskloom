"""The mutation report (packaging/mutants.py) leaves out of its score only the functions the --explained file lists
under not_tested, and lists each of them with its reason and its number of mutants, so nothing is dropped unseen."""

import json
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "mutants.py"
SHELL = {"taskloom/scheduler.py serve": "The service's outer shell: the slow tests start it for real.",
         "taskloom/scheduler.py Scheduler.check_servers": "Checks other PCs over SSH: the keeper tests cover it."}


def report(tmp_path, codes: dict, explained: dict):
    """Runs the real script on a mutants/ tree that holds these exit codes, as mutmut records them."""
    (tmp_path / "mutants" / "taskloom").mkdir(parents=True)
    (tmp_path / "mutants" / "taskloom" / "scheduler.py.meta").write_text(
        json.dumps({"exit_code_by_key": codes}), encoding="utf-8")
    (tmp_path / "explained.yaml").write_text(yaml.safe_dump(explained, allow_unicode=True), encoding="utf-8")
    done = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), "--explained", str(tmp_path / "explained.yaml"),
                           "--out", str(tmp_path / "mutants.json")], capture_output=True, text=True, timeout=300)
    return done, json.loads((tmp_path / "mutants.json").read_text(encoding="utf-8"))


def test_a_not_tested_functions_mutants_do_not_count_and_are_listed_with_their_reason(tmp_path):
    """claim_time: 9 caught and 1 missed with a reason (90%). serve and Scheduler.check_servers are not tested: their
    missed, untested and unjudged mutants would fail the run, but they are left out of the score and listed."""
    claim = "taskloom.scheduler.x_claim_time__mutmut_"
    serve = "taskloom.scheduler.x_serve__mutmut_"
    servers = "taskloom.scheduler.xǁSchedulerǁcheck_servers__mutmut_"
    codes = ({f"{claim}{i}": 1 for i in range(1, 10)} | {f"{claim}10": 0}
             | {f"{serve}1": 0, f"{serve}2": 0, f"{serve}3": 0, f"{serve}4": 33, f"{serve}5": None, f"{serve}6": 1}
             | {f"{servers}1": 33, f"{servers}2": 0})
    done, result = report(tmp_path, codes, {f"{claim}10": "It changes nothing you see.", "not_tested": SHELL})

    assert done.returncode == 0, done.stdout + done.stderr
    assert (result["score"], result["killed"], result["total"], result["passed"]) == (90.0, 9, 10, True)
    assert [s["name"] for s in result["survivors"]] == [f"{claim}10"]
    assert result["unexplained"] == [] and result["unjudged"] == []
    assert sorted(result["not_tested"], key=lambda n: n["function"]) == [
        {"function": "taskloom/scheduler.py Scheduler.check_servers", "reason": SHELL[
            "taskloom/scheduler.py Scheduler.check_servers"], "mutants": 2},
        {"function": "taskloom/scheduler.py serve", "reason": SHELL["taskloom/scheduler.py serve"], "mutants": 6}]
    for function, reason in SHELL.items():
        assert any(function in line and reason in line for line in done.stdout.splitlines()), done.stdout
    assert serve not in done.stdout and servers not in done.stdout
    assert "PASSED: mutation score 90%" in done.stdout


def test_a_function_not_listed_as_not_tested_still_counts(tmp_path):
    """Only the very functions listed are left out: _serve_tray is not serve and Scheduler._check is not
    Scheduler.check_servers, so their missed mutants still count, without a reason, and fail the run."""
    codes = {"taskloom.scheduler.xǁSchedulerǁ_check__mutmut_1": 0, "taskloom.scheduler.x__serve_tray__mutmut_1": 0,
             "taskloom.scheduler.xǁSchedulerǁ_check__mutmut_2": 1}
    done, result = report(tmp_path, codes, {"not_tested": SHELL})

    assert done.returncode == 1
    assert (result["killed"], result["total"], result["passed"]) == (1, 3, False)
    assert sorted(result["unexplained"]) == ["taskloom.scheduler.x__serve_tray__mutmut_1",
                                             "taskloom.scheduler.xǁSchedulerǁ_check__mutmut_1"]
    assert {n["function"]: n["mutants"] for n in result["not_tested"]} == {f: 0 for f in SHELL}


def test_a_single_mutant_listed_as_not_tested_is_left_out_and_its_functions_other_mutants_still_count(tmp_path):
    """A mutant named on its own under not_tested (e.g. a Windows-only line no run here can execute) is left out of
    the score and listed with its reason; the other mutants of its function still count."""
    start = "taskloom.scheduler.xǁSchedulerǁ_start__mutmut_"
    windows = {f"{start}46": "Windows only: a console window per run, which no run on Linux or macOS can show."}
    done, result = report(tmp_path, {f"{start}46": 0, f"{start}47": 0, f"{start}48": 1}, {"not_tested": windows})

    assert done.returncode == 1
    assert (result["killed"], result["total"]) == (1, 2)
    assert result["unexplained"] == [f"{start}47"]
    assert result["not_tested"] == [{"function": f"{start}46", "reason": windows[f"{start}46"], "mutants": 1}]


def test_the_parts_verdicts_count_as_one_run_and_a_mutant_no_part_judged_fails_it(tmp_path):
    """CI runs mutation testing in parts on several machines (--run --part K/N); --parts takes each mutant's verdict
    from the part that judged it (each part's mutmut records hold every mutant, judged or not) and scores them as one
    run. Without one part, its mutants could not be judged and the run fails: none is dropped unseen."""
    claim = "taskloom.scheduler.x_claim_time__mutmut_"
    codes = {f"{claim}{i}": 1 for i in range(1, 10)} | {f"{claim}10": 0}
    parts = []
    for k in (1, 2):  # part 1 judged the odd ones, part 2 the even ones
        part = tmp_path / f"part-{k}"
        (part / "mutants" / "taskloom").mkdir(parents=True)
        (part / "mutants" / "taskloom" / "scheduler.py.meta").write_text(json.dumps({"exit_code_by_key": {
            name: code if int(name.rsplit("_", 1)[1]) % 2 == k % 2 else None for name, code in codes.items()}}),
            encoding="utf-8")
        parts.append(part)
    explained = tmp_path / "explained.yaml"
    explained.write_text(yaml.safe_dump({f"{claim}10": "It changes nothing you see."}), encoding="utf-8")

    def scored(project: Path, *with_parts: Path):
        done = subprocess.run([sys.executable, str(SCRIPT), str(project), "--parts", *map(str, with_parts),
                               "--explained", str(explained), "--out", str(project / "mutants.json")],
                              capture_output=True, text=True, timeout=300)
        return done, json.loads((project / "mutants.json").read_text(encoding="utf-8"))

    done, result = scored(tmp_path / "both", *parts)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (result["score"], result["killed"], result["total"], result["unjudged"]) == (90.0, 9, 10, [])

    done, result = scored(tmp_path / "one", parts[0])
    assert done.returncode == 1, done.stdout + done.stderr
    assert result["unjudged"] == [f"{claim}{i} (exit None)" for i in (10, 2, 4, 6, 8)]


def scoped(tmp_path, records: dict, explained: dict):
    """Runs the real script for a feature with a scope: its mutmut records are where a scoped run leaves them,
    PROJECT_DIR/build/mutation/<feature>/mutants (the feature: the --explained file's name up to its first dot)."""
    for file, codes in records.items():
        meta = tmp_path / "build" / "mutation" / "styles" / "mutants" / f"{file}.meta"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"exit_code_by_key": codes}), encoding="utf-8")
    (tmp_path / "styles.mutants.yaml").write_text(yaml.safe_dump(explained, allow_unicode=True), encoding="utf-8")
    done = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), "--explained",
                           str(tmp_path / "styles.mutants.yaml"), "--out", str(tmp_path / "mutants.json")],
                          capture_output=True, text=True, timeout=300)
    return done, json.loads((tmp_path / "mutants.json").read_text(encoding="utf-8"))


TABLE = "taskloom.report.x_html_table__mutmut_"
RECORDS = {"taskloom/report.py": {f"{TABLE}{i}": 1 for i in range(1, 10)} | {f"{TABLE}10": 0}
           | {"taskloom.report.x_plain_text__mutmut_1": None, "taskloom.report.x_plain_text__mutmut_2": None},
           "taskloom/blocks/reports.py": {"taskloom.blocks.reports.x__placed__mutmut_1": 1,
                                          "taskloom.blocks.reports.xǁTemplateǁrun__mutmut_1": 1}}
SAME = {"kind": "The same style, written another way", "why": "An email program reads both the same way.",
        "mutants": [f"{TABLE}10"]}


def test_a_scope_counts_only_the_functions_and_files_it_names(tmp_path):
    """A small feature mutation-tests only what it changed (scope: mutate, in its mutants.yaml): a function, as
    where() names it, or a whole file. The file's other functions have mutants too (mutmut plants them in the whole
    file) that no test ran: they are not the feature's, so they count in nothing. What was planted in is listed."""
    done, result = scoped(tmp_path, RECORDS, {"kinds": [SAME], "scope": {
        "mutate": ["taskloom/report.py html_table", "taskloom/blocks/reports.py"], "tests": ["tests/test_styles.py"]}})

    assert done.returncode == 0, done.stdout + done.stderr
    assert (result["score"], result["killed"], result["total"], result["passed"]) == (91.7, 11, 12, True)
    assert result["unjudged"] == [] and "plain_text" not in done.stdout
    assert result["scope"] == [{"function": "taskloom/report.py html_table", "mutants": 10},
                               {"function": "taskloom/blocks/reports.py", "mutants": 2}]
    assert "planted in: taskloom/report.py html_table (10), taskloom/blocks/reports.py (2)" in done.stdout


def test_a_scope_entry_nothing_is_planted_in_fails_the_run(tmp_path):
    """A function the scope names in which mutmut planted nothing (misspelt, renamed, or not a function) fails the
    run and is named: a scope must not get smaller unseen."""
    done, result = scoped(tmp_path, RECORDS, {"kinds": [SAME], "scope": {
        "mutate": ["taskloom/report.py html_table", "taskloom/report.py email_documnet"], "tests": []}})

    assert done.returncode == 1
    assert (result["killed"], result["total"], result["passed"]) == (9, 10, False)
    assert {"function": "taskloom/report.py email_documnet", "mutants": 0} in result["scope"]
    assert "nothing planted in taskloom/report.py email_documnet" in done.stdout, done.stdout


def test_one_reason_explains_every_missed_mutant_of_its_kind_and_no_other(tmp_path):
    """Missed mutants of one kind share one reason (kinds: a kind, its why, the mutants it explains by name), in
    place of the same line written for each. A missed mutant no kind names, and with no line of its own, has no
    reason and fails the run."""
    codes = {f"{TABLE}1": 0, f"{TABLE}2": 0, f"{TABLE}3": 0} | {f"{TABLE}{i}": 1 for i in range(4, 40)}
    kind = {**SAME, "mutants": [f"{TABLE}1", f"{TABLE}2"]}
    scope = {"mutate": ["taskloom/report.py html_table"], "tests": []}
    done, result = scoped(tmp_path, {"taskloom/report.py": codes}, {"kinds": [kind], "scope": scope})

    assert done.returncode == 1
    assert [(s["name"], s["kind"], s["why_equivalent"]) for s in result["survivors"]] == [
        (f"{TABLE}1", SAME["kind"], SAME["why"]), (f"{TABLE}2", SAME["kind"], SAME["why"]), (f"{TABLE}3", "", "")]
    assert result["unexplained"] == [f"{TABLE}3"]

    done, result = scoped(tmp_path, {"taskloom/report.py": codes},
                          {"kinds": [kind], "scope": scope, f"{TABLE}3": "Its own reason."})
    assert done.returncode == 0, done.stdout + done.stderr
    assert (result["score"], result["unexplained"], result["passed"]) == (92.3, [], True)


PYPROJECT = """\
[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.mutmut]
source_paths = ["pkg/"]
timeout_multiplier = 2.0
timeout_constant = 2.0
pytest_add_cli_args = ["-p", "no:cacheprovider"]
"""
COMMAND = "import sys\n\nfrom pkg import calc\n\nprint(getattr(calc, sys.argv[1])(int(sys.argv[2])))\n"
SHOWN = '''\
import subprocess
import sys

import pytest


@pytest.fixture
def shown(tmp_path):
    """What `python -m pkg FUNCTION N` prints, run as a child process in another folder."""
    return lambda function, n: subprocess.run([sys.executable, "-m", "pkg", function, str(n)], cwd=tmp_path,
                                              capture_output=True, text=True).stdout.strip()
'''
DOUBLE = '"""Small sums."""\n\n\ndef double(n):\n    return n * 2\n'
TRIPLE = "def triple(n):\n    return n * 3\n"
DOUBLE_TEST = 'def test_double(shown):\n    assert shown("double", 3) == "6"\n'


def standin(tmp_path, calc: str, tests: dict, scope: dict):
    """Runs the real script with --run on a small project of its own: pkg/calc.py, the functions the bugs are
    planted in; `python -m pkg FUNCTION N`, which prints FUNCTION(N); and tests that never import pkg: they only
    start that command, as a child process in another folder. So a test passes only if the child process imports
    the copy of the code that mutmut planted the bugs in, and catches a bug only if the child takes the one under
    test."""
    project = tmp_path / "project"
    for name, text in {"pyproject.toml": PYPROJECT, "pkg/__init__.py": "", "pkg/__main__.py": COMMAND,
                       "pkg/calc.py": calc, "tests/conftest.py": SHOWN, **tests}.items():
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_text(text, encoding="utf-8")
    (project / "small.mutants.yaml").write_text(yaml.safe_dump({"scope": scope}), encoding="utf-8")
    done = subprocess.run([sys.executable, str(SCRIPT), str(project), "--run", "--workers", "3", "--explained",
                           str(project / "small.mutants.yaml"), "--out", str(tmp_path / "mutants.json")],
                          capture_output=True, text=True, timeout=600)
    return done, json.loads((tmp_path / "mutants.json").read_text(encoding="utf-8"))


def test_a_bug_is_caught_through_a_child_process_and_one_no_test_notices_is_reported_with_its_change(tmp_path):
    """The tests reach the code only through a child process, where mutmut links no test to a function and so
    judges nothing. Each planted bug is run with the scope's tests all the same: those in double, whose result a
    test checks, are all caught; those in note that only change its words, which no test reads, are missed and
    reported with their change and no reason, so the run fails."""
    note = 'def note(n):\n    return "n is " + str(n)\n'
    done, result = standin(tmp_path, DOUBLE + "\n\n" + note, {
        "tests/test_calc.py": DOUBLE_TEST + '\n\ndef test_note_says_something(shown):\n    assert shown("note", 3)\n'},
        {"mutate": ["pkg/calc.py double", "pkg/calc.py note"], "tests": ["tests/test_calc.py"]})

    planted = {n["function"]: n["mutants"] for n in result["scope"]}
    assert done.returncode == 1 and result["unjudged"] == [], done.stdout + done.stderr
    assert planted["pkg/calc.py double"] > 0 and result["total"] == sum(planted.values())
    assert result["survivors"] and {s["where"] for s in result["survivors"]} == {"pkg/calc.py note"}
    assert result["killed"] == result["total"] - len(result["survivors"]) >= planted["pkg/calc.py double"] + 1
    assert all("n is" in s["change"] and "→" in s["change"] for s in result["survivors"]), result["survivors"]
    assert result["unexplained"] == [s["name"] for s in result["survivors"]] and not result["passed"]
    assert f"missed {result['survivors'][0]['name']}: " in done.stdout and "NO REASON GIVEN" in done.stdout


def test_a_group_of_functions_runs_its_own_tests_and_the_others_run_the_scopes(tmp_path):
    """An entry of mutate: may be a group, mutate: and tests: of its own (each of tests: one pytest run, a file with
    -k words or without), so that a bug runs only the tests that can see it. triple's bugs run test_more.py -k
    triple, which catches them: the scope's tests would not (they never call triple), and without -k the other
    test there fails with no bug planted. double is not in a group: its bugs run the scope's tests."""
    more = 'def test_triple(shown):\n    assert shown("triple", 3) == "9"\n\n\ndef test_other():\n    assert False\n'
    done, result = standin(tmp_path, DOUBLE + "\n\n" + TRIPLE, {
        "tests/test_double.py": DOUBLE_TEST, "tests/test_more.py": more},
        {"tests": ["tests/test_double.py"], "mutate": [
            "pkg/calc.py double", {"mutate": ["pkg/calc.py triple"], "tests": ["tests/test_more.py -k triple"]}]})

    assert done.returncode == 0, done.stdout + done.stderr
    assert [n["function"] for n in result["scope"]] == ["pkg/calc.py double", "pkg/calc.py triple"]
    assert all(n["mutants"] > 0 for n in result["scope"])
    assert result["killed"] == result["total"] == sum(n["mutants"] for n in result["scope"])
    assert (result["score"], result["survivors"], result["unjudged"], result["passed"]) == (100.0, [], [], True)


def test_tests_that_fail_with_no_bug_planted_stop_the_run_before_any_bug_is_judged(tmp_path):
    """A test that fails on the code as it is would catch every bug: the scope's tests first run with no bug
    planted, and when they fail the run stops, says so, and no bug gets a verdict."""
    done, result = standin(tmp_path, DOUBLE, {
        "tests/test_calc.py": 'def test_double(shown):\n    assert shown("double", 3) == "7"\n'},
        {"mutate": ["pkg/calc.py double"], "tests": ["tests/test_calc.py"]})

    assert done.returncode == 1
    assert "tests/test_calc.py: the tests fail with no mutant planted" in done.stdout, done.stdout + done.stderr
    assert (result["killed"], result["total"], result["survivors"], result["passed"]) == (0, 0, [], False)
    assert result["unjudged"] and all(name.endswith("(exit None)") for name in result["unjudged"])


def test_a_bug_that_makes_the_tests_hang_is_caught_by_the_time_limit(tmp_path):
    """A planted bug can make the code never end (here: counting down by one becomes counting up, and the child
    process never reaches 0). Its tests are stopped at a time limit, a multiple of the time they take with no bug
    planted, and the bug counts as caught; the run goes on to the next bug."""
    countdown = ("def countdown(n):\n    steps = 0\n    while n != 0:\n        n -= 1\n        steps += 1\n"
                 "    return steps\n")
    done, result = standin(tmp_path, countdown, {
        "tests/test_calc.py": 'def test_countdown(shown):\n    assert shown("countdown", 3) == "3"\n'},
        {"mutate": ["pkg/calc.py countdown"], "tests": ["tests/test_calc.py"]})

    assert done.returncode == 0, done.stdout + done.stderr
    assert "caught, at the time limit" in done.stdout, done.stdout
    assert (result["score"], result["survivors"], result["unjudged"]) == (100.0, [], [])


def test_a_function_the_scope_does_not_name_stays_as_it_is_written(tmp_path):
    """mutmut wraps every function it plants a bug in, and the wrapper takes any arguments. Code that looks at
    what a function takes then sees something else: Qt gives a slot as many of a signal's values as it takes, so a
    wrapped slot that takes none gets them all and fails, with no bug on. Bugs are planted only in the functions
    the scope names, so the file's other functions stay as written: here pair, which arguments() looks at."""
    calc = (DOUBLE + "\n\ndef pair(a, b):\n    return a + b\n"
            "\n\ndef arguments(n):\n    return pair.__code__.co_argcount + n\n")
    takes_two = 'def test_pair_takes_two(shown):\n    assert shown("arguments", 0) == "2"\n'
    done, result = standin(tmp_path, calc, {"tests/test_calc.py": DOUBLE_TEST + "\n\n" + takes_two},
                           {"mutate": ["pkg/calc.py double"], "tests": ["tests/test_calc.py"]})

    assert done.returncode == 0, done.stdout + done.stderr
    assert result["scope"] == [{"function": "pkg/calc.py double", "mutants": result["killed"]}]
    assert (result["score"], result["survivors"], result["unjudged"]) == (100.0, [], [])


def test_a_bug_that_makes_the_tests_own_process_die_is_caught(tmp_path):
    """A planted bug can end the process of the tests themselves (in the editor's tests: a grid given no table
    crashes Qt; here: the signal that only asks whether a process lives becomes one that ends it). The tests passed
    with no bug planted and did not pass with it, so it counts as caught, not as a run that proves nothing."""
    alive = "import os\n\n\ndef alive(n):\n    os.kill(os.getpid(), 0)\n    return n\n"
    done, result = standin(tmp_path, alive, {
        "tests/test_calc.py": "from pkg.calc import alive\n\n\ndef test_alive():\n    assert alive(3) == 3\n"},
        {"mutate": ["pkg/calc.py alive"], "tests": ["tests/test_calc.py"]})

    assert done.returncode == 0, done.stdout + done.stderr
    assert (result["score"], result["survivors"], result["unjudged"]) == (100.0, [], [])
