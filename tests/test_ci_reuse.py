"""CI skips the scheduler's TLA+ model check, and its mutation testing, when the files they read are byte-identical to
those of an earlier run whose results can still be downloaded, and uses those results instead: the key
(packaging/ci_reuse.py key) changes when one of those files changes and only then (A4 of the CI speed-up). The
records taken from the earlier run are marked with the run, its date and its commit (ci_reuse.py mark), so that the
proof PDF can name them (A5). Every other feature's mutation testing is reused the same way, its key over the whole
files it reads (key feature:<f>, B1); an earlier run's stored results are taken only when none is expired, none comes
from a fork and none is 30 days old or more, and of those runs the newest (ci_reuse.py pick, B4). A run by hand may name
the features whose mutation testing it runs: ci_reuse.py select lists them, each with the parts its mutation testing
runs in and the check whose key keeps its result (I1, W2, W6 of the CI work). An edit to a test's body in
tests/test_updates.py changes only the keys of the checks whose own tests name that file (W7). The script runs as CI
runs it, on a copy of the repository's files or on a made-up repository."""

import ast
import datetime as dt
import itertools
import json
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
REUSE = REPO / "packaging" / "ci_reuse.py"
SHA, OLD_SHA = "3f9c2a1d8e7b6c5a4f3e2d1c0b9a8f7e6d5c4b3a", "1b688b0aa11bb22cc33dd44ee55ff66aa77bb88c"
# What the keys read, and some of what they must not (README.md, the rest of taskloom/ and tests/, docs pages).
COPIED = ["specs/formal", "docs/promises", "packaging/checkers.py", "packaging/mutants.py", "pyproject.toml",
          "uv.lock", "README.md", "taskloom", "tests"]
EVIDENCE = "docs/promises/scheduler.evidence.yaml"
MODEL_FILES = {"specs/formal/SchedulerService.tla", "specs/formal/SchedulerService.cfg", EVIDENCE,
               "packaging/checkers.py"}


def reuse(*args) -> subprocess.CompletedProcess:
    assert REUSE.is_file(), "no packaging/ci_reuse.py"
    return subprocess.run([sys.executable, str(REUSE), *map(str, args)], cwd=REPO, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=120)


def key(check: str, root: Path, *more) -> str:
    done = reuse("key", check, "--root", root, *more)
    assert done.returncode == 0, f"key {check} exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
    printed = done.stdout.strip()
    assert re.fullmatch(r"[0-9a-fA-F]{16,}", printed), f"not a hex key: {printed!r}"
    return printed


def listed(check: str, root: Path) -> list[str]:
    """What `files` prints, each relative to the root."""
    done = reuse("files", check, "--root", root)
    assert done.returncode == 0, f"files {check} exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
    paths = [Path(line.strip()) for line in done.stdout.splitlines() if line.strip()]
    return [(p.resolve().relative_to(root.resolve()) if p.is_absolute() else p).as_posix() for p in paths]


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path) -> Path:
    """A copy of the repository's files, plus a made-up chain of imports: tests/test_scheduler.py (selected for
    mutation testing in pyproject.toml) imports tests/reuse_level_one.py, which imports tests/reuse_level_two.py in
    the other way tests import their helpers; and a test file no selected test imports."""
    root = tmp_path / "repo"
    for name in COPIED:
        if (REPO / name).is_dir():
            shutil.copytree(REPO / name, root / name, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        else:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, root / name)
    assert '"tests/test_scheduler.py"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    with open(root / "tests" / "test_scheduler.py", "a", encoding="utf-8") as selected:
        selected.write("\nfrom tests.reuse_level_one import LEVEL  # noqa: E402,F401\n")
    write(root / "tests" / "reuse_level_one.py", "from reuse_level_two import LEVEL  # noqa: F401\n")
    write(root / "tests" / "reuse_level_two.py", "LEVEL = 2\n")
    write(root / "tests" / "test_reuse_unrelated.py", "def test_nothing():\n    pass\n")
    return root


def change(root: Path, name: str, old: str | None = None, new: str = "", added: str = "# changed\n"):
    """Replace `old` (found once) by `new` in the file, or add a line at its end."""
    file = root / name
    text = file.read_text(encoding="utf-8")
    if old is None:
        text += "\n" + added
    else:
        assert text.count(old) == 1, f"{name}: {old!r} is there {text.count(old)} times"
        text = text.replace(old, new)
    file.write_text(text, encoding="utf-8")


# Changes after which the old result is still the result: no file the check reads changed.
UNRELATED = {
    "a taskloom module not mutated (a release's version)": ("taskloom/__init__.py", '__version__ = "',
                                                            '__version__ = "9'),
    "README.md": ("README.md", None, "", "A new paragraph.\n"),
    "a row's words in the evidence": (EVIDENCE, "The service takes the Taskloom folder's lock",
                                      "The service first takes the Taskloom folder's lock"),
    "another checker's entry in the evidence": (EVIDENCE, "--out {out}/leak.json", "--out {out}/leak-check.json"),
    "a test file no selected test imports": ("tests/test_reuse_unrelated.py", None),
}


@pytest.mark.parametrize("check", ["model", "mutation"])
@pytest.mark.parametrize("what", UNRELATED)
def test_the_key_stays_the_same_when_no_file_the_check_reads_changed(root, check, what):
    """Otherwise a release, a README edit or a new test would rerun the 2-hour model check and the 3 hours of
    mutation testing for nothing (the user: "when no scheduler file changed")."""
    before = key(check, root)
    change(root, *UNRELATED[what])
    assert key(check, root) == before, f"{what} changed the {check} key"


MODEL_CHANGES = {
    "the spec": ("specs/formal/SchedulerService.tla", None, "", "\\* changed\n"),
    "its config": ("specs/formal/SchedulerService.cfg", None, "", "\\* changed\n"),
    "the model check's command": (EVIDENCE, "-workers auto", "-workers 4"),
    "the model check's key lines": (EVIDENCE, "key: '^(Model checking completed\\.|Error: )'",
                                    "key: '^(Model checking completed|Error: )'"),
    "packaging/checkers.py": ("packaging/checkers.py", None),
}
MUTATION_CHANGES = {
    "a mutated file (schedule.py)": ("taskloom/schedule.py", None),
    "a mutated file (scheduler.py)": ("taskloom/scheduler.py", None),
    "pyproject.toml's settings": ("pyproject.toml", "timeout_multiplier = 2.0", "timeout_multiplier = 3.0"),
    "uv.lock": ("uv.lock", None),
    "packaging/mutants.py": ("packaging/mutants.py", None),
    "the mutants not tested": ("docs/promises/scheduler.mutants.yaml", "\n  taskloom.scheduler.x__lock__mutmut_11: ",
                               "\n  taskloom.scheduler.x__lock__mutmut_99: "),
    "a test file selected whole": ("tests/test_scheduler.py", None),
    "a test file selected by its tests' names only": ("tests/test_scheduler_question_dropped.py", None),
    "a tests/ file a selected test imports": ("tests/reuse_level_one.py", None),
    "a tests/ file reached through two imports": ("tests/reuse_level_two.py", None),
    "tests/conftest.py": ("tests/conftest.py", None),
}


@pytest.mark.parametrize("check, what", [("model", what) for what in MODEL_CHANGES]
                         + [("mutation", what) for what in MUTATION_CHANGES])
def test_the_key_changes_when_a_file_the_check_reads_changes(root, check, what):
    """An old result is never taken for a check whose input changed: the check then runs, as before."""
    before = key(check, root)
    change(root, *{**MODEL_CHANGES, **MUTATION_CHANGES}[what])
    assert key(check, root) != before, f"{what} did not change the {check} key"


@pytest.mark.parametrize("check", ["model", "mutation"])
def test_the_key_changes_with_the_salt(root, check):
    """CI passes the TLC jar's sha256 and the Java and Python versions as the salt: a new one reruns the check."""
    assert key(check, root, "--salt", "tlc 936a2620 java 17 python 3.12") != \
        key(check, root, "--salt", "tlc 0f1e2d3c java 17 python 3.12")


def test_files_lists_what_the_model_check_reads(root):
    printed = listed("model", root)
    assert len(printed) == len(set(printed)) and set(printed) == MODEL_FILES, printed


PYPROJECT = '''[project]
name = "made-up"
version = "0.1.0"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.mutmut]
source_paths = ["taskloom/"]
only_mutate = ["taskloom/planner.py", "taskloom/keeper.py"]
also_copy = ["README.md"]
pytest_add_cli_args = ["-p", "no:randomly", "-p", "no:cacheprovider"]
pytest_add_cli_args_test_selection = [
    "tests/test_planner.py",
    "tests/test_keeper.py::test_keeps_one",
    "tests/test_keeper.py::test_keeps_two",
    "-k", """not (tray or editor)""",
]
'''
# A made-up repository: each file and what it holds. The selected tests import a test module (as `tests.`), which
# imports a helper (by its bare name, as tests import sshserver); a test that is not selected imports another.
MADE_UP = {
    "pyproject.toml": PYPROJECT, "uv.lock": "version = 1\n", "README.md": "# Made up\n",
    "packaging/mutants.py": "# mutation testing\n", "packaging/checkers.py": "# checkers\n",
    "docs/promises/scheduler.mutants.yaml": "not_tested: {}\n",
    "docs/promises/scheduler.evidence.yaml": "checkers: []\n", "docs/guide.md": "# Guide\n",
    "taskloom/__init__.py": '__version__ = "0.1.0"\n', "taskloom/planner.py": "def plan():\n    return 1\n",
    "taskloom/keeper.py": "def keep():\n    return 2\n", "taskloom/other.py": "def other():\n    return 3\n",
    "tests/conftest.py": "from pathlib import Path\n\nREPO = Path(__file__).parents[1]\n",
    "tests/test_planner.py": "from taskloom.other import other\nfrom taskloom.planner import plan\n"
                             "from tests.test_shared import shared\n\n\n"
                             "def test_plans():\n    assert plan() == shared()\n",
    "tests/test_shared.py": "from fakeserver import serve\n\n\ndef shared():\n    return serve()\n",
    "tests/fakeserver.py": "def serve():\n    return 1\n",
    "tests/test_keeper.py": "from conftest import REPO\nfrom taskloom.keeper import keep\n\n\n"
                            "def test_keeps_one():\n    assert keep() == 2\n\n\n"
                            "def test_keeps_two():\n    assert keep()\n",
    "tests/test_unselected.py": "from tests.test_lonely import lonely\n\n\n"
                                "def test_lonely():\n    assert lonely()\n",
    "tests/test_lonely.py": "def lonely():\n    return 1\n",
}


def test_files_lists_what_mutation_testing_reads(tmp_path):
    """The files mutmut mutates (pyproject.toml's only_mutate), pyproject.toml, uv.lock, packaging/mutants.py, the
    scheduler's mutants yaml, the test files pyproject selects (whole or by their tests' names), every tests/ file
    they import, followed through, and tests/conftest.py. Not the rest of taskloom/, not README.md, not docs pages, not
    a test only an unselected test imports."""
    for name, text in MADE_UP.items():
        write(tmp_path / name, text)
    printed = listed("mutation", tmp_path)
    assert len(printed) == len(set(printed)), printed
    assert set(printed) == {"taskloom/planner.py", "taskloom/keeper.py", "pyproject.toml", "uv.lock",
                            "packaging/mutants.py", "docs/promises/scheduler.mutants.yaml", "tests/test_planner.py",
                            "tests/test_keeper.py", "tests/test_shared.py", "tests/fakeserver.py",
                            "tests/conftest.py"}, sorted(printed)


# --- mark: the records taken from an earlier run say which run -------------------------------------------------------

RUN, URL, DATE = "43676917412", "https://github.com/example/taskloom/actions/runs/43676917412", "2026-11-15"


def checker_record(name: str, kind: str, commit: str) -> dict:
    """A record of checkers.json, as packaging/checkers.py writes it."""
    return {"name": name, "kind": kind, "command": f"run {name}", "exit": 0, "commit": commit,
            "log": f"scheduler/logs/{kind}.log", "summary": f"{name} passed.", "tail": [f"{name} passed."],
            "key": "passed"}


def records(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def put(path: Path, data: list) -> Path:
    write(path, json.dumps(data, indent=1))
    return path


def test_mark_adds_the_run_its_url_its_date_and_each_records_own_commit(tmp_path):
    """The model check's record from the earlier run, wherever its checkers.json is under the folder, keeps all it
    had and says which run, link and date it comes from, and the commit it ran on."""
    model = checker_record("TLA+ model check", "model", OLD_SHA)
    other = checker_record("TLA+ trace validation", "trace", SHA)
    files = [put(tmp_path / "jobs" / "checkers-model" / "scheduler" / "checkers.json", [model]),
             put(tmp_path / "jobs" / "deeper" / "proof" / "scheduler" / "checkers.json", [other, model])]
    done = reuse("mark", tmp_path / "jobs", "--run", RUN, "--url", URL, "--date", DATE)
    assert done.returncode == 0, done.stdout + done.stderr
    assert records(files[0]) == [{**model, "reused_from": {"run": RUN, "url": URL, "commit": OLD_SHA, "date": DATE}}]
    assert records(files[1]) == [{**other, "reused_from": {"run": RUN, "url": URL, "commit": SHA, "date": DATE}},
                                 {**model, "reused_from": {"run": RUN, "url": URL, "commit": OLD_SHA, "date": DATE}}]


def test_mark_with_a_commit_names_that_commit(tmp_path):
    """Mutation testing's record is written by this run from the earlier run's verdicts: its own commit is this one,
    the verdicts are of the commit given."""
    mutation = checker_record("Mutation testing", "mutation", SHA)
    file = put(tmp_path / "jobs" / "mutation" / "scheduler" / "checkers.json", [mutation])
    done = reuse("mark", tmp_path / "jobs", "--run", RUN, "--url", URL, "--date", DATE, "--commit", OLD_SHA)
    assert done.returncode == 0, done.stdout + done.stderr
    assert records(file) == [{**mutation, "reused_from": {"run": RUN, "url": URL, "commit": OLD_SHA, "date": DATE}}]


def test_mark_leaves_a_record_that_already_says_where_it_comes_from(tmp_path):
    """A result reused from a run that had itself reused it still names the run that made it."""
    first = {"run": "43594508147", "url": "https://github.com/example/taskloom/actions/runs/43594508147",
             "commit": "21dcc42aa11bb22cc33dd44ee55ff66aa77bb88c", "date": "2026-11-09"}
    marked = {**checker_record("TLA+ model check", "model", first["commit"]), "reused_from": first}
    plain = checker_record("Mutation testing", "mutation", OLD_SHA)
    file = put(tmp_path / "jobs" / "scheduler" / "checkers.json", [marked, plain])
    done = reuse("mark", tmp_path / "jobs", "--run", RUN, "--url", URL, "--date", DATE)
    assert done.returncode == 0, done.stdout + done.stderr
    assert records(file) == [marked, {**plain, "reused_from": {"run": RUN, "url": URL, "commit": OLD_SHA,
                                                                "date": DATE}}]


# --- every feature's mutation testing: a key over the whole files it reads (B1, interface 6) --------------------------

FEATURE = "feature:plans"
CHECKER = """  - name: Mutation testing
    kind: mutation
    command: >-
      python packaging/mutants.py . --run --explained docs/promises/{stem}.mutants.yaml --out {{out}}/mutants.json
    key: '^(PASSED|FAILED): mutation score'
"""
# The made-up feature `plans`: a group of functions with its tests (a file with -k words, a file by its tests' names),
# a file mutated whole with the scope's own tests, and the guide its code reads. And another feature, `colours`.
PLANS_MUTANTS = '''# Mutation testing of the planner.
scope:
  mutate:
    - mutate:
        - taskloom/planner.py plan
        - taskloom/planner.py Planner.next
      tests:
        - tests/test_planner.py -k "plans or next"
        - tests/test_dates.py::test_one_day tests/test_dates.py::test_two_days -p no:randomly
    - taskloom/keeper.py
  tests:
    - tests/test_keeper.py
reads:
  - docs/guide.md
not_tested:
  taskloom/planner.py Planner.next: Drawn on screen only.
'''
FEATURE_REPO = {
    "pyproject.toml": PYPROJECT, "uv.lock": "version = 1\n", "README.md": "# Made up\n",
    "packaging/mutants.py": "# mutation testing\n", "packaging/checkers.py": "# checkers\n",
    "docs/guide.md": "# Guide\n\nPlans are made once a day.\n",
    "docs/promises/plans.yaml": "feature: Plans\nagreed: 2026-11-17\nrows:\n  P-1: A plan is made once a day.\n",
    "docs/promises/plans.mutants.yaml": PLANS_MUTANTS,
    "docs/promises/plans.evidence.yaml": "P-1:\n  enforced: The planner.\n  not_green: Tests only.\ncheckers:\n"
                                         + CHECKER.format(stem="plans"),
    "docs/promises/colours.yaml": "feature: Colours\nagreed: 2026-11-17\nrows:\n  C-1: Red is red.\n",
    "docs/promises/colours.mutants.yaml": "scope:\n  mutate:\n    - mutate:\n        - taskloom/colours.py\n"
                                          "      tests:\n        - tests/test_colours.py\n",
    "docs/promises/colours.evidence.yaml": "C-1:\n  enforced: The colours.\ncheckers:\n"
                                           + CHECKER.format(stem="colours"),
    "taskloom/__init__.py": '__version__ = "0.1.0"\n',
    "taskloom/planner.py": "def plan():\n    return 1\n\n\nclass Planner:\n    def next(self):\n        return 2\n",
    "taskloom/keeper.py": "def keep():\n    return 2\n", "taskloom/other.py": "def other():\n    return 3\n",
    "taskloom/colours.py": "def colour():\n    return 'red'\n",
    "tests/conftest.py": "from pathlib import Path\n\nREPO = Path(__file__).parents[1]\n",
    "tests/test_planner.py": "from taskloom.other import other\nfrom taskloom.planner import Planner, plan\n"
                             "from tests.test_shared import shared\n\n\n"
                             "def test_plans():\n    assert plan() == shared() == other() - 2\n\n\n"
                             "def test_next():\n    assert Planner().next() == 2\n",
    "tests/test_shared.py": "from fakeserver import serve\n\n\ndef shared():\n    return serve()\n",
    "tests/fakeserver.py": "def serve():\n    return 1\n",
    "tests/test_dates.py": "def test_one_day():\n    pass\n\n\ndef test_two_days():\n    pass\n",
    "tests/test_keeper.py": "from conftest import REPO\nfrom taskloom.keeper import keep\n\n\n"
                            "def test_keeps():\n    assert keep() == 2 and REPO\n",
    "tests/test_colours.py": "from taskloom.colours import colour\nfrom tests.test_lonely import lonely\n\n\n"
                             "def test_colour():\n    assert colour() == lonely()\n",
    "tests/test_lonely.py": "def lonely():\n    return 'red'\n",
}
# What the made-up feature's mutation testing reads: each kind of file, one of them.
PLANS_READS = {
    "a file mutated by its functions": "taskloom/planner.py",
    "a file mutated whole": "taskloom/keeper.py",
    "a test file named with -k words": "tests/test_planner.py",
    "a test file named by its tests' names": "tests/test_dates.py",
    "a test file the scope's own tests: name": "tests/test_keeper.py",
    "a tests/ file a named test imports": "tests/test_shared.py",
    "a tests/ helper reached through two imports": "tests/fakeserver.py",
    "tests/conftest.py": "tests/conftest.py",
    "a file under reads:": "docs/guide.md",
    "the feature's mutants yaml": "docs/promises/plans.mutants.yaml",
    "the feature's evidence yaml": "docs/promises/plans.evidence.yaml",
    "packaging/mutants.py": "packaging/mutants.py",
    "packaging/checkers.py": "packaging/checkers.py",
    "pyproject.toml": "pyproject.toml",
    "uv.lock": "uv.lock",
}
# Files whose change leaves the feature's old result its result.
PLANS_UNRELATED = {
    "a taskloom module a named test imports, not mutated": "taskloom/other.py",
    "a module another feature mutates": "taskloom/colours.py",
    "README.md": "README.md",
    "another feature's agreed list": "docs/promises/colours.yaml",
    "another feature's mutants yaml": "docs/promises/colours.mutants.yaml",
    "another feature's evidence yaml": "docs/promises/colours.evidence.yaml",
    "another feature's test file": "tests/test_colours.py",
}


@pytest.fixture
def made_up(tmp_path) -> Path:
    for name, text in FEATURE_REPO.items():
        write(tmp_path / name, text)
    return tmp_path


def one_byte(root: Path, name: str):
    """Add one byte at the end of the file (a comment in each of these files' languages)."""
    with open(root / name, "ab") as file:
        file.write(b"#")


def test_files_lists_what_a_features_mutation_testing_reads(made_up):
    """The files its scope mutates, the test files its scope's tests: lines name, every tests/ file they import
    (followed through) and tests/conftest.py, the files under its reads:, its mutants and evidence yaml, the scripts
    that run and score it, pyproject.toml and uv.lock. Not another taskloom module, README.md, another feature's files
    or a test only another feature's test imports."""
    printed = listed(FEATURE, made_up)
    assert len(printed) == len(set(printed)), printed
    assert set(printed) == set(PLANS_READS.values()), sorted(printed)


@pytest.mark.parametrize("what", PLANS_READS)
def test_a_features_key_changes_when_one_byte_of_a_file_it_reads_changes(made_up, what):
    """A feature's old result is never taken when one of the files its mutation testing reads changed: it runs."""
    before = key(FEATURE, made_up)
    one_byte(made_up, PLANS_READS[what])
    assert key(FEATURE, made_up) != before, f"{what} did not change the key"


@pytest.mark.parametrize("what", PLANS_UNRELATED)
def test_a_features_key_stays_the_same_when_no_file_it_reads_changed(made_up, what):
    """Otherwise a README edit or another feature's change would rerun every feature's mutation testing."""
    before = key(FEATURE, made_up)
    one_byte(made_up, PLANS_UNRELATED[what])
    assert key(FEATURE, made_up) == before, f"{what} changed the key"


def test_a_features_key_changes_with_the_salt(made_up):
    """CI passes the Java and Python versions as the salt: a new one reruns the feature's mutation testing."""
    assert key(FEATURE, made_up, "--salt", "java 17 python 3.12") != \
        key(FEATURE, made_up, "--salt", "java 17 python 3.13")


MODEL_CHECKER = """  - name: TLA+ model check
    kind: model
    command: java -jar tla2tools.jar -workers auto specs/formal/Planner.tla
    key: '^Model checking completed'
"""
# What the key cannot cover: the feature then gets no key (it always runs, its result is never kept), and the message
# says why.
REFUSED = {
    "no mutants yaml": (lambda root: (root / "docs/promises/plans.mutants.yaml").unlink(), "plans.mutants.yaml"),
    "a mutants yaml with no scope": (lambda root: write(root / "docs/promises/plans.mutants.yaml",
                                                        "not_tested: {}\n"), "scope"),
    "a checker that is not mutation testing": (lambda root: write(
        root / "docs/promises/plans.evidence.yaml", FEATURE_REPO["docs/promises/plans.evidence.yaml"] + MODEL_CHECKER),
        "model"),
    "a mutated file that is not there": (lambda root: (root / "taskloom/keeper.py").unlink(), "keeper.py"),
    "a named test file that is not there": (lambda root: (root / "tests/test_dates.py").unlink(), "test_dates.py"),
    "a file under reads: that is not there": (lambda root: (root / "docs/guide.md").unlink(), "guide.md"),
    "no evidence yaml": (lambda root: (root / "docs/promises/plans.evidence.yaml").unlink(), "plans.evidence.yaml"),
}


@pytest.mark.parametrize("what", REFUSED)
def test_a_feature_the_key_cannot_cover_gets_no_key_and_a_message(made_up, what):
    """A key that left out what the feature reads would reuse a result its change made wrong."""
    key(FEATURE, made_up)  # the same feature, whole, has a key
    spoil, named = REFUSED[what]
    spoil(made_up)
    done = reuse("key", FEATURE, "--root", made_up)
    assert done.returncode != 0, f"{what}: exited 0 and printed {done.stdout!r}"
    assert not re.search(r"[0-9a-fA-F]{16,}", done.stdout), done.stdout
    assert named in done.stderr + done.stdout, f"{what}: the message does not name {named}:\n{done.stderr}"


def ci_features() -> list[str]:
    """The features whose checkers CI runs and keeps under a key of the feature's: each docs/promises/<f>.yaml but
    <f>.<part>.yaml and the scheduler's (whose checks have keys of their own)."""
    return sorted(path.stem for path in (REPO / "docs" / "promises").glob("*.yaml")
                  if path.name.count(".") == 1 and path.stem != "scheduler")


def test_every_feature_ci_runs_has_a_key_in_this_repository():
    """A feature that got no key would rerun on every run, and nothing would say why."""
    features = ci_features()
    assert features, "no feature found"
    refused = {}
    for name in features:
        done = reuse("key", f"feature:{name}")
        if done.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{16,}", done.stdout.strip()):
            refused[name] = f"exit {done.returncode}: {done.stdout.strip()} {done.stderr.strip()}"
    assert not refused, refused


def test_the_readme_features_key_reads_the_guide():
    """Its mutated _guide_text reads docs/guide.md, and its tests check what it reads there: a changed guide reruns
    its mutation testing."""
    assert "docs/guide.md" in listed("feature:readme", REPO)


# --- pick: the newest earlier run whose stored results may be used (B4, interface 7) ----------------------------------

REPO_ID, FORK_ID = 1083920123, 2210456789
NOW = dt.datetime(2026, 11, 17, 12, 0, tzinfo=dt.timezone.utc)
PARTS = [f"reuse-mutation-0a1b2c3d4e5f60718293a4b5c6d7e8f9-part-{k}" for k in (1, 2, 3)]
OLDER, NEWER, NEWEST = 43594508147, 43676917412, 43712000045
numbers = itertools.count(4813000000)


def stamp(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def artifact(name: str, run: int, made: dt.datetime, expired: bool = False, repo: int = REPO_ID) -> dict:
    """An artifact as the GitHub API lists it (repos/<repo>/actions/artifacts)."""
    number = next(numbers)
    return {"id": number, "node_id": f"MDg6QXJ0aWZhY3Q{number}", "name": name, "size_in_bytes": 52817,
            "url": f"https://api.github.com/repos/example/taskloom/actions/artifacts/{number}",
            "archive_download_url": f"https://api.github.com/repos/example/taskloom/actions/artifacts/{number}/zip",
            "expired": expired, "digest": "sha256:" + "ab" * 32, "created_at": stamp(made), "updated_at": stamp(made),
            "expires_at": stamp(made + dt.timedelta(days=1 if expired else 90)),
            "workflow_run": {"id": run, "repository_id": REPO_ID, "head_repository_id": repo,
                             "head_branch": "main", "head_sha": SHA}}


def uploaded(run: int, names=tuple(PARTS), age=dt.timedelta(days=2), **how) -> list:
    """The artifacts one run uploaded under these names, `age` before NOW."""
    return [artifact(name, run, NOW - age, **how) for name in names]


def pick(objects: list, *args, now: dt.datetime | None = NOW, names=tuple(PARTS)) -> str:
    """What `pick` prints for these artifacts (one JSON object per line, as `gh api --jq '.artifacts[]'` gives them)."""
    assert REUSE.is_file(), "no packaging/ci_reuse.py"
    more = ["--now", stamp(now)] if now else []
    done = subprocess.run([sys.executable, str(REUSE), "pick", "--repo-id", str(REPO_ID), *args, *more, *names],
                          input="".join(json.dumps(o) + "\n" for o in objects), cwd=REPO, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert done.returncode == 0, f"pick exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
    return done.stdout.strip()


def test_pick_prints_the_newest_run_that_has_every_name():
    """Of the runs with all 3 parts, the newest (the highest run id, not the highest as text); a newer run that has
    only 2 of them is not taken, as all 3 parts must come from one run."""
    objects = (uploaded(OLDER) + uploaded(NEWEST, PARTS[:2]) + uploaded(9876543210) + uploaded(NEWER)
               + uploaded(NEWEST + 1, PARTS[1:]))
    assert pick(objects) == str(NEWER)


UNUSABLE = {
    "expired": {"expired": True},
    "uploaded by a fork's pull request": {"repo": FORK_ID},
}


@pytest.mark.parametrize("what", UNUSABLE)
def test_pick_does_not_take_a_run_with_an_artifact_it_may_not_use(what):
    """An expired artifact cannot be downloaded; a fork's pull request can upload anything under any name."""
    objects = uploaded(OLDER) + uploaded(NEWER, PARTS[:2]) + uploaded(NEWER, PARTS[2:], **UNUSABLE[what])
    assert pick(objects) == str(OLDER)


@pytest.mark.parametrize("days, age, taken", [
    ([], dt.timedelta(days=30) - dt.timedelta(seconds=1), True),
    ([], dt.timedelta(days=30), False),
    (["--days", "7"], dt.timedelta(days=7) - dt.timedelta(seconds=1), True),
    (["--days", "7"], dt.timedelta(days=7), False),
], ids=["29 days 23:59:59", "30 days", "--days 7: 6 days 23:59:59", "--days 7: 7 days"])
def test_pick_takes_a_result_only_while_it_is_less_than_30_days_old(days, age, taken):
    """The user: a stored result older than 30 days reruns, so that a check is run again at least once a month."""
    assert pick(uploaded(NEWER, age=age), *days) == (str(NEWER) if taken else "")


def test_pick_counts_the_days_up_to_now_when_not_told_when_now_is():
    """CI does not say when now is."""
    now = dt.datetime.now(dt.timezone.utc)
    young = [artifact(name, NEWER, now - dt.timedelta(days=29)) for name in PARTS]
    old = [artifact(name, NEWER, now - dt.timedelta(days=31)) for name in PARTS]
    assert (pick(young, now=None), pick(old, now=None)) == (str(NEWER), "")


@pytest.mark.parametrize("objects", [[], uploaded(OLDER, PARTS[:2]) + uploaded(NEWER, PARTS[1:])],
                         ids=["no artifact", "no run with every name"])
def test_pick_prints_nothing_when_no_run_has_every_name(objects):
    """Then the check runs; a lookup that finds nothing is no error."""
    assert pick(objects) == ""


def test_on_a_tag_release_and_pages_run_also_when_a_long_check_was_taken_from_an_earlier_run():
    """A long check taken from an earlier run skips its job (mutation, model-check, checkers-features). GitHub then
    skips every job after it whose `if` has no status function, however far down (v0.39.1's tag run skipped release
    and pages so, though all they need had passed). So each says !cancelled(), the tag, and that every job it needs
    succeeded."""
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))["jobs"]
    for name in ("release", "pages"):
        needs, condition = jobs[name]["needs"], jobs[name]["if"]
        assert "!cancelled()" in condition and "startsWith(github.ref, 'refs/tags/v')" in condition, (name, condition)
        for need in [needs] if isinstance(needs, str) else needs:
            assert f"needs.{need}.result == 'success'" in condition, (name, need, condition)


# --- select: the features a run by hand names, each with its parts and its check (I1; W2, W6 of the CI work) ---------

# The jobs each feature's mutation testing runs in at once (`parts:` in its mutants yaml; without it, 1): the
# scheduler's 3 as before, and the three that took longest whole in stage 3's full runs, updates (168-178 minutes),
# braces (76-99) and rows (77 and 103). Every other feature: 1.
SPLIT = {"scheduler": 3, "updates": 4, "braces": 2, "rows": 2}
LISTING = re.compile(r"^\S+ \d+ (mutation|feature:\S+)\s*$", re.M)


def mutation_features() -> list[str]:
    """The name of each docs/promises/<name>.mutants.yaml of this repository."""
    return sorted(path.name.removesuffix(".mutants.yaml")
                  for path in (REPO / "docs" / "promises").glob("*.mutants.yaml"))


def check_of(name: str) -> str:
    """The check whose key keeps a feature's mutation testing result (what `key` takes)."""
    return "mutation" if name == "scheduler" else f"feature:{name}"


def selected(*args) -> list[str]:
    """What `select` prints, line by line."""
    done = reuse("select", *args)
    assert done.returncode == 0, f"select exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def lines(*names) -> list[str]:
    """`select`'s lines for these features of this repository: sorted by name, each once."""
    return [f"{name} {SPLIT.get(name, 1)} {check_of(name)}" for name in sorted(set(names))]


def test_select_lists_every_mutation_feature_with_its_parts_and_its_check():
    """A full run (no features named, or the by-hand input left empty) runs every feature's mutation testing: one line
    for each docs/promises/*.mutants.yaml, sorted by name, with the jobs it runs in and the check whose key keeps its
    result."""
    names = mutation_features()
    assert set(SPLIT) <= set(names), names
    assert selected() == lines(*names)
    assert selected("--features", "") == lines(*names)


NAMED = {
    "one name": ("updates", ["updates"]),
    "commas": ("updates,braces,scheduler", ["updates", "braces", "scheduler"]),
    "spaces": ("rows zoom", ["rows", "zoom"]),
    "commas and spaces": ("rows, zoom ,scheduler", ["rows", "zoom", "scheduler"]),
    "spaces before and after": ("  rows,  zoom  ", ["rows", "zoom"]),
    "a name more than once": ("rows,rows rows, zoom", ["rows", "zoom"]),
}


@pytest.mark.parametrize("what", NAMED)
def test_select_lists_the_features_named_each_once(what):
    """A run by hand with features runs only the mutation testing of those named, however the names are separated
    (commas, spaces or both), each once."""
    given, names = NAMED[what]
    assert selected("--features", given) == lines(*names)


def assert_refused(done: subprocess.CompletedProcess, *named: str):
    """Exit 2, no feature listed, and the message on stderr names each of `named`."""
    assert done.returncode == 2, f"exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
    assert not LISTING.search(done.stdout), f"it listed features all the same:\n{done.stdout}"
    missing = [name for name in named if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", done.stderr)]
    assert not missing, f"the message does not name {missing}:\n{done.stderr}"


UNKNOWN = {
    "a name that is no feature": ("nonesuch", ["nonesuch"]),
    "beside a feature": ("rows, nonesuch", ["nonesuch"]),
    "two of them": ("nonesuch,rows other-thing", ["nonesuch", "other-thing"]),
}


@pytest.mark.parametrize("what", UNKNOWN)
def test_select_refuses_a_name_that_is_no_feature_and_names_it_and_the_known_ones(what):
    """A mistyped name would otherwise run nothing for it, and the run would pass: the run fails at once, its message
    naming what is unknown and every feature it could have named."""
    given, unknown = UNKNOWN[what]
    assert_refused(reuse("select", "--features", given), *unknown, *mutation_features())


@pytest.mark.parametrize("given", [",", " ", " , ,", ",,"], ids=["a comma", "a space", "commas and spaces", "commas"])
def test_select_refuses_features_that_name_none(given):
    """Only separators names no feature: not a full run (that is the input left empty) and not a run of nothing; the
    message lists the features it could name."""
    assert_refused(reuse("select", "--features", given), *mutation_features())


# A made-up repository's mutation features: one in 2 parts, one without parts:, the scheduler in 3; and the other files
# of a feature, which are no mutation feature.
FAKE_MUTANTS = {
    "docs/promises/alpha.mutants.yaml": "parts: 2\nnot_tested: {}\n",
    "docs/promises/beta.mutants.yaml": "not_tested: {}\n",
    "docs/promises/scheduler.mutants.yaml": "parts: 3\nnot_tested: {}\n",
    "docs/promises/alpha.yaml": "feature: Alpha\nagreed: 2026-11-24\nrows:\n  A-1: Alpha works.\n",
    "docs/promises/alpha.evidence.yaml": "A-1:\n  enforced: Alpha.\ncheckers: []\n",
}


@pytest.mark.parametrize("bad", ["0", "x", "true", "2.5"])
def test_select_refuses_parts_that_are_not_a_whole_number_of_at_least_one(tmp_path, bad):
    """parts: is how many jobs a feature's mutation testing runs in; anything but a whole number of 1 or more is a
    mistake in the mutants yaml (YAML's true is no number of parts, 2.5 is not 2), said rather than guessed at."""
    for name, text in FAKE_MUTANTS.items():
        write(tmp_path / name, text)
    assert selected("--root", tmp_path) == ["alpha 2 feature:alpha", "beta 1 feature:beta", "scheduler 3 mutation"]
    write(tmp_path / "docs/promises/alpha.mutants.yaml", f"parts: {bad}\nnot_tested: {{}}\n")
    done = reuse("select", "--root", tmp_path)
    assert_refused(done, "alpha")
    assert "parts" in done.stderr, done.stderr


# --- W7: an edit to a test of updates reruns only the checks that run that test ---------------------------------------

UPDATES_TESTS = "tests/test_updates.py"


def own_tests(check: str) -> set[str]:
    """The test files a check's own test selection names (not those they import): pyproject.toml's selection for the
    scheduler's mutation testing, each tests: line of a feature's scope and its groups; none for the model check."""
    if check == "model":
        return set()
    if check == "mutation":
        args = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"][
            "pytest_add_cli_args_test_selection"]
    else:
        mutants = REPO / "docs" / "promises" / f"{check.removeprefix('feature:')}.mutants.yaml"
        scope = yaml.safe_load(mutants.read_text(encoding="utf-8"))["scope"]
        groups = [scope, *(entry for entry in scope["mutate"] if isinstance(entry, dict))]
        args = [arg for group in groups for line in group.get("tests") or [] for arg in shlex.split(line)]
    return {arg.split("::")[0] for arg in args if arg.split("::")[0].endswith(".py")}


def add_a_statement_to_a_test(file: Path) -> str:
    """Add a statement at the end of the body of the file's first test function (its other bytes as they were);
    return the test's name."""
    text = file.read_bytes().decode("utf-8")
    test = next(node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)
                and node.name.startswith("test_") and node.body[0].lineno > node.lineno)
    rows = text.splitlines(keepends=True)
    rows.insert(test.body[-1].end_lineno, " " * test.body[0].col_offset + "assert True  # an edit to this test\n")
    edited = "".join(rows)
    ast.parse(edited)
    file.write_bytes(edited.encode("utf-8"))
    return test.name


def test_an_edit_to_a_test_of_updates_changes_only_the_keys_of_the_checks_whose_tests_name_it(tmp_path):
    """v0.40.1's one-line fix to a test in tests/test_updates.py changed the keys of the scheduler's mutation testing
    and of six features (other test files imported helpers from it), and their mutation testing, hours of it, ran again
    for nothing. In a copy of what the keys read, a statement added to a test's body there changes the key of no check
    (the model check, the scheduler's mutation testing, every feature's) but those whose own tests name that file."""
    checks = ["model", *(check_of(name) for name in mutation_features())]
    read = {UPDATES_TESTS}.union(*(listed(check, REPO) for check in checks))
    root = tmp_path / "repo"
    for name in read:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, root / name)
    before = {check: key(check, root) for check in checks}
    test = add_a_statement_to_a_test(root / UPDATES_TESTS)
    changed = {check for check in checks if key(check, root) != before[check]}
    running = {check for check in checks if UPDATES_TESTS in own_tests(check)}
    assert running, f"no check's own tests name {UPDATES_TESTS}"
    assert changed == running, (f"{test} edited: changed also {sorted(changed - running)}; "
                                f"unchanged though their tests name it {sorted(running - changed)}")
