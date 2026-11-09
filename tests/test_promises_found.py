"""Each feature's bugs found while proving it (docs/promises/<feature>.found.yaml), which the proof PDF counts and
lists, names the test that now guards each one; that test must exist, so no bug is said to be guarded by nothing."""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_each_bug_found_says_what_found_it_and_names_a_test_that_exists():
    files = sorted((REPO / "docs" / "promises").glob("*.found.yaml"))
    assert files, "no docs/promises/*.found.yaml"
    for path in files:
        listed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert listed["found"] and listed["lighter"], path.name
        for bug in listed["found"]:
            assert {"what", "found_by", "fixed_in", "guarded_by"} <= bug.keys(), (path.name, bug)
            file, name = bug["guarded_by"].split("::")
            assert re.search(rf"^def {re.escape(name)}\(", (REPO / file).read_text(encoding="utf-8"), re.M), \
                f"{path.name}: {bug['guarded_by']} is not a test"
