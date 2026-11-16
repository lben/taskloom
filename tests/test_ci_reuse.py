"""CI skips the scheduler's TLA+ model check, and its mutation testing, when the files they read are byte-identical to
those of an earlier run whose results can still be downloaded, and uses those results instead: the key
(packaging/ci_reuse.py key) changes when one of those files changes and only then (A4 of the CI speed-up). The
records taken from the earlier run are marked with the run, its date and its commit (ci_reuse.py mark), so that the
proof PDF can name them (A5). The script runs as CI runs it, on a copy of the repository's files."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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
