"""The proof PDF of a CI run that took the scheduler's model check and mutation testing from an earlier run, whose
records say so (reused_from: packaging/ci_reuse.py mark): each of their checker boxes names that run, its date and
its commit, and the release's "Commit" line is not failed for them; a record of another commit that does not say it
was reused still fails that line, as before. (A5 of the CI speed-up.) Every other feature's mutation testing is reused
the same way: its box reads as the scheduler's, in words that name no scheduler, and the "Commit" line names each
reused result with its feature, as every feature's checker is called Mutation testing (B7).

The evidence is laid out as CI lays out proof/<release>/ and the PDF is built by the real script and read back with
QtPdf, as tests/test_proof_pdf_v2.py does."""

import datetime as dt
import os
import re
import subprocess
import sys

import pytest

from tests.test_proof_pdf_v2 import (OTHER_SHA, PROOF_PDF, SHA, checker, feature, feature_pages, flat, pages, record,
                                     result, write)

OLD_SHA = "1b688b0aa11bb22cc33dd44ee55ff66aa77bb88c"
MODEL_RUN = {"run": "43676917412", "url": "https://github.com/example/taskloom/actions/runs/43676917412",
             "commit": OLD_SHA, "date": "2026-11-15"}
MUTATION_RUN = {"run": "43594508147", "url": "https://github.com/example/taskloom/actions/runs/43594508147",
                "commit": OTHER_SHA, "date": "2026-11-09"}
MODEL, MUTATION, TESTS = "TLA+ model check", "Mutation testing", "pytest"


def checkers(reused: bool) -> list:
    """The model check's record is the earlier run's (its commit); mutation testing's was written by this run (this
    commit) from the earlier run's verdicts; the tests ran on this commit."""
    model, mutation, tests = (checker(MODEL, "model", 0, OLD_SHA), checker(MUTATION, "mutation", 0, SHA),
                              checker(TESTS, "tests", 0, SHA))
    if reused:
        model["reused_from"], mutation["reused_from"] = MODEL_RUN, MUTATION_RUN
    return [model, mutation, tests]


RELEASE = {"version": "0.38.0", "commit": SHA, "mutation": {"score": 93.5, "killed": 432, "total": 462},
           "tests": {"passed": 2412, "failed": 0, "skipped": 9,
                     "platforms": {"Windows": {"passed": 2405, "failed": 0, "skipped": 3}}},
           "checkers": {"run": 3, "passed": 3}, "leak": {"passed": True, "summary": "No leak."},
           "charter": {"violations": 0, "rules_checked": 14},
           "design_health": {"over_points": 0, "over_lines": 0, "kept": {}}}
SCHEDULER = "Scheduler service"


def proof(root, *stems) -> list:
    """The pages of the PDF of these features (promises/<stem>.yaml, their evidence in evidence/<stem>/) and one
    proof on screen, built by the real script."""
    evidence, proofs = root / "evidence", root / "proofs"
    write(evidence / "release.json", RELEASE)
    record(proofs, "test_proof_other.test_lines", "Lines bend round blocks", 1)
    write(proofs / "session.json", {"exitstatus": 0, "system": "Windows"})
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(root)}  # no repository: nothing is said to be new
    features = [arg for stem in stems for arg in ("--feature", f"promises/{stem}.yaml")]
    done = subprocess.run([sys.executable, str(PROOF_PDF), "proofs", "proof.pdf", "--repo", str(root),
                           "--evidence", "evidence", *features], cwd=root, env=env,
                          capture_output=True, encoding="utf-8", errors="replace", timeout=300)
    assert done.returncode == 0, f"proof_pdf.py failed:\n{done.stdout}\n{done.stderr}"
    return pages(root / "proof.pdf")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    """Two PDFs of the same feature: its model check and mutation testing records marked as reused, and not."""
    made = {}
    for name, reused in (("reused", True), ("not-reused", False)):
        root = tmp_path_factory.mktemp(name)
        feature(root / "promises", root / "evidence", "scheduler", SCHEDULER, {
            "S-1": ("Only one service runs at a time.", {"enforced": "A lock file.", "green_by": [MODEL]})},
            [result("s1", "S-1")], [], checkers(reused))
        made[name] = proof(root, "scheduler")
    return made


# Two features whose mutation testing, each one's only checker, was taken from the run the scheduler's was taken from
# (one earlier run keeps every check's result). Their records are their own job's, of the commit it tested.
FEATURES = {2: ("braces", "Braces everywhere"), 3: ("rows", "Rows in requests")}


@pytest.fixture(scope="module")
def with_features(tmp_path_factory) -> list:
    """The PDF of the scheduler, its checks reused as above, then the two features (features 2 and 3)."""
    root = tmp_path_factory.mktemp("features")
    promises, evidence = root / "promises", root / "evidence"
    feature(promises, evidence, "scheduler", SCHEDULER, {
        "S-1": ("Only one service runs at a time.", {"enforced": "A lock file.", "green_by": [MODEL]})},
        [result("s1", "S-1")], [], checkers(True))
    for stem, name in FEATURES.values():
        mutation = {**checker(MUTATION, "mutation", 0, OTHER_SHA), "reused_from": MUTATION_RUN}
        feature(promises, evidence, stem, name, {
            "R-1": (f"{name} keep their values.", {"enforced": "Tests.", "not_green": "Tests try some values."})},
            [result(stem, "R-1")], [], [mutation])
    return proof(root, "scheduler", *(stem for stem, _ in FEATURES.values()))


def box(pdf: list, name: str) -> str:
    """The text of checker `name`'s box on the feature's pages: from the end of the box before it to the start of the
    one after it (each box: what it checks and its summary, its log and commit, its command and final lines)."""
    text = " ".join(flat(pdf[i]) for i in feature_pages(pdf, 1))
    order = sorted((MODEL, MUTATION, TESTS), key=lambda n: text.index(f"{n} summary line."))
    at = order.index(name)
    start = text.index(f"{order[at - 1]} line 14") + len(f"{order[at - 1]} line 14") if at else 0
    end = text.index(f"{order[at + 1]} summary line.") if at + 1 < len(order) else len(text)
    return text[start:end]


def commit_line(pdf: list) -> tuple[str, str]:
    """The release-wide "Commit" line on the first page: its mark (✓ or ✗) and its words."""
    found = re.search(r"\bCommit ([✓✗]) (.+?) Mutation testing [✓✗]", flat(pdf[0]))
    assert found, flat(pdf[0])
    return found[1], found[2]


@pytest.mark.parametrize("name, run", [(MODEL, MODEL_RUN), (MUTATION, MUTATION_RUN)])
def test_a_reused_checkers_box_names_the_run_its_date_and_its_commit(built, name, run):
    """Whoever reads the proof sees that this result was not made by this run, and which run, when and on which
    commit made it."""
    date = dt.date.fromisoformat(run["date"])
    when = f"({re.escape(run['date'])}|{date.day} {date:%B} {date.year})"
    text = box(built["reused"], name)
    assert re.search(rf"Reused from CI run {run['run']} \({when}, commit {run['commit'][:7]}\)", text), text


def test_a_reused_record_does_not_fail_the_commit_line(built):
    """The model check's record is of the earlier commit, and says so: the line passes and lists it as reused."""
    mark, words = commit_line(built["reused"])
    assert mark == "✓" and "Not all of this commit" not in words, words
    assert MODEL in words and "reused" in words.lower(), words


def test_a_record_of_another_commit_not_marked_as_reused_still_fails_the_commit_line(built):
    mark, words = commit_line(built["not-reused"])
    assert mark == "✗" and f"Not all of this commit ({SHA[:7]})" in words, words
    assert f"{MODEL} ran on {OLD_SHA[:7]}" in words, words


def test_the_heading_over_the_checker_boxes_does_not_say_a_reused_one_is_of_this_commit(built):
    """The heading over the boxes said their final lines were all of this commit, also those of a reused check."""
    text = " ".join(flat(built["reused"][i]) for i in feature_pages(built["reused"], 1))
    start = text.index("Checker output")
    heading = text[start:text.index("summary line.", start)]  # up to the first box's summary
    assert "this commit" not in heading or "reused" in heading.lower(), heading


GENERIC = "Other Taskloom files may have changed since; its tests ran on this commit."


def checker_output(pdf: list, number: int) -> str:
    """The checker boxes of feature `number`, under their heading."""
    text = " ".join(flat(pdf[i]) for i in feature_pages(pdf, number))
    start = text.index("Checker output")
    return text[start:text.index("Surviving mutants", start)]


@pytest.mark.parametrize("number", FEATURES, ids=[stem for stem, _ in FEATURES.values()])
def test_a_reused_features_box_names_the_run_its_date_and_its_commit_and_no_scheduler(with_features, number):
    """A feature's mutation testing taken from an earlier run reads as the scheduler's: which run, when, which commit,
    and that other files may have changed but its tests ran on this one; not that the scheduler's tests did."""
    date = dt.date.fromisoformat(MUTATION_RUN["date"])
    when = f"({re.escape(MUTATION_RUN['date'])}|{date.day} {date:%B} {date.year})"
    text = checker_output(with_features, number)
    assert re.search(rf"Reused from CI run {MUTATION_RUN['run']} \({when}, commit {OTHER_SHA[:7]}\)", text), text
    assert GENERIC in text, text
    assert "scheduler" not in text.lower(), text


def test_the_schedulers_reused_mutation_box_says_its_tests_ran_on_this_commit(built):
    """The same words on every reused mutation testing box."""
    text = box(built["reused"], MUTATION)
    assert GENERIC in text, text


def test_the_commit_line_names_each_reused_result_with_its_feature(with_features):
    """The scheduler's and both features' checkers are all called Mutation testing, reused from one run of one commit:
    each is listed, with its feature's name, so that they can be told apart."""
    mark, words = commit_line(with_features)
    assert mark == "✓", words
    names = [SCHEDULER, *(name for _, name in FEATURES.values())]
    listed = [f"{SCHEDULER}: {MODEL} (CI run {MODEL_RUN['run']}, commit {OLD_SHA[:7]})"] + [
        f"{name}: {MUTATION} (CI run {MUTATION_RUN['run']}, commit {OTHER_SHA[:7]})" for name in names]
    assert [entry for entry in listed if entry not in words] == [], words
