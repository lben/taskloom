"""The proof PDF with features (packaging/proof_pdf.py --feature PROMISES_YAML --evidence DIR): what the maintainer
relies on when reading it. The cover's table gives each feature's rows counted from its ledger and its verdict
(Certified, BLOCKED: needs your decision, Not certified: N red); a red row says why; each checker's box shows its log,
the commit it ran on and its exit code, a trace validation before the model check; a blocked feature says so with its
question and options; each feature's proofs on screen follow its pages, the other proofs after all the features.
Without --feature the PDF is built as before.

The evidence is laid out as CI lays out proof/<release>/, its ledgers made by the real packaging/ledger.py from test
results; the PDF is built by the real script and read back with QtPdf, page by page."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtPdf import QPdfDocument  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PROOF_PDF, LEDGER = REPO / "packaging" / "proof_pdf.py", REPO / "packaging" / "ledger.py"
SHA, OTHER_SHA = "3f9c2a1d8e7b6c5a4f3e2d1c0b9a8f7e6d5c4b3a", "aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00"
WHY_RED = "AssertionError: the 09:00 run started twice"
QUESTION = "When the last run of a flow is too old, should it be deleted too?"
OPTIONS = ["Delete it: the setting means what it says.", "Keep the last run of each flow, whatever its age."]
# The changes' titles (the proofs on screen): one of feature Alpha, one of feature Beta, one of neither.
ALPHA_CHANGE, BETA_CHANGE, OTHER_CHANGE = "Runs begin on the second", "Old runs go away", "Lines bend round blocks"


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def record(proof_dir: Path, scenario: str, title: str, minute: int):
    """A proof record as tests/proofs/conftest.py writes it (no screenshot)."""
    write(proof_dir / scenario / "proof.json", {
        "title": title, "asked": "Can it do this?", "status": "passed", "error": "", "system": "Windows",
        "why_not_windows": "", "started": f"2026-11-07T10:{minute:02}:00+00:00", "version": "0.38.0",
        "idea": ["It is shown in the editor."], "why_simplest": ["It reuses what is there."],
        "alternatives": [{"option": "A separate window", "why_not": "One more thing to learn."}],
        "fits": ["As the rest of the editor does."], "platform": "Windows-11 · Python 3.12 · PySide6 6.9",
        "entries": [{"kind": "step", "text": "Open the flow."}, {"kind": "check", "text": "It opened", "ok": True}]})


def feature(promises: Path, evidence: Path, stem: str, name: str, rows: dict, results: list, proofs: list,
            checkers: list = (), blocked: dict | None = None):
    """A feature's agreed list (docs/promises/<stem>.yaml) and its evidence yaml; its ledger made by the real
    packaging/ledger.py from `results` and `checkers`; its checkers' output, mutants and leak check."""
    lines = [f"feature: {name}", "agreed: 2026-11-07", "asked:", "  - date: 2026-11-01",
             f"    words: \"please make {name} reliable\"", f"proofs: {json.dumps(proofs)}", "rows:"]
    lines += [f"  {row}: {text}" for row, (text, _) in rows.items()]
    write(promises / f"{stem}.yaml", "\n".join(lines) + "\n")
    write(promises / f"{stem}.evidence.yaml", {row: how for row, (_, how) in rows.items()})
    write(promises / f"{stem}.design.yaml", "- chosen: one lock file\n  revisit_if: the folder is shared\n")
    folder = evidence / stem
    write(folder / "checkers.json", list(checkers))
    write(folder / "results.json", {"commit": SHA, "platform": "linux", "tests": results})
    write(folder / "mutants.json", {"score": 95.0, "killed": 38, "total": 40, "threshold": 90, "survivors": []})
    write(folder / "leak.json", {"passed": True, "summary": "No leak.", "numbers": {"Memory after 1 hour": "88 MB"}})
    if blocked:
        write(folder / "blocked.json", blocked)
    done = subprocess.run([sys.executable, str(LEDGER), str(promises / f"{stem}.yaml"), str(folder / "results.json"),
                           "--checkers", str(folder / "checkers.json"), "--out", str(folder / "ledger.json")],
                          capture_output=True, encoding="utf-8", timeout=120)
    assert (folder / "ledger.json").is_file(), done.stderr


def result(name: str, row: str, outcome: str = "passed", every: str = "", why: str = "") -> dict:
    return {"id": f"tests/test_x.py::{name}", "rows": [row], "charter": [], "every": [every] if every else [],
            "outcome": outcome, "why": why}


def checker(name: str, kind: str, exit_code: int, commit: str = SHA) -> dict:
    return {"name": name, "kind": kind, "command": f"run-{name} --all", "exit": exit_code, "commit": commit,
            "log": f"alpha/{name}.log", "summary": f"{name} summary line.",
            "tail": [f"{name} line {i}" for i in range(1, 15)]}


AMBER = {"enforced": "Tests of the service.", "not_green": "Tests try the cases we thought of."}
# Alpha (4 rows: 2 green, 1 amber, 1 red), its checkers listed with the model check before the trace validation.
ALPHA_CHECKERS = [checker("tlc", "model", 0), checker("trace", "trace", 0), checker("pytest", "tests", 1, OTHER_SHA)]


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    """The PDF of three features (Alpha: a red row; Beta: blocked; Gamma: certified) and three proofs on screen,
    and the PDF of the same proofs without features."""
    root = tmp_path_factory.mktemp("v2")
    promises, evidence, proofs = root / "promises", root / "evidence", root / "proofs"
    feature(promises, evidence, "alpha", "Alpha service", {
        "A-1": ("Only one service runs at a time.", {"enforced": "A lock file.", "green_by": ["tlc"]}),
        "A-2": ("A run begins at its time.", {"enforced": "Tried at every second."}),
        "A-3": ("Each due time runs at most once. Even after a restart.", AMBER),
        "A-4": ("Missed times follow the Misfire setting.", AMBER)},
        [result("t1", "A-1"), result("t2", "A-2", every="every second of a day"), result("t3", "A-3"),
         result("t4", "A-4", "failed", why=WHY_RED)], ["test_proof_alpha"], ALPHA_CHECKERS)
    write(evidence / "alpha" / "statechart.mmd", "stateDiagram-v2\n  [*] --> Off\n  Off --> On: you turn it on\n")
    feature(promises, evidence, "beta", "Beta retention", {
        "B-1": ("Old runs are deleted.", AMBER), "B-2": ("A run going is never deleted.", AMBER),
        "B-3": ("No flow is ever deleted.", AMBER)},
        [result(f"b{n}", f"B-{n}") for n in (1, 2, 3)], ["test_proof_beta"],
        blocked={"question": QUESTION, "options": OPTIONS})
    feature(promises, evidence, "gamma", "Gamma mail", {"G-1": ("A mail is sent once.", AMBER)},
            [result("g1", "G-1")], [])
    write(evidence / "release.json", {
        "version": "0.38.0", "commit": SHA, "mutation": {"score": 93.5, "killed": 432, "total": 462},
        "tests": {"passed": 2412, "failed": 1, "skipped": 9,
                  "platforms": {"Windows": {"passed": 2405, "failed": 1, "skipped": 3}}},
        "checkers": {"run": 3, "passed": 2}, "leak": {"passed": True, "summary": "No leak."},
        "charter": {"violations": 0, "rules_checked": 14},
        "design_health": {"over_points": 1, "over_lines": 0, "kept": {"engine.run_line": "one ordered walk"}}})
    write(evidence / "c4" / "context.mmd", "flowchart LR\n  you([\"You\"]) --> taskloom[\"Taskloom\"]\n")
    write(evidence / "c4" / "containers.mmd", "flowchart LR\n  editor[\"Editor\"] -->|starts| run[\"Run\"]\n")
    record(proofs, "test_proof_other.test_lines", OTHER_CHANGE, 1)
    record(proofs, "test_proof_alpha_start.test_second", ALPHA_CHANGE, 2)
    record(proofs, "test_proof_beta.test_old_runs", BETA_CHANGE, 3)
    write(proofs / "session.json", {"exitstatus": 0, "system": "Windows"})

    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(root)}  # no repository: nothing is said to be new

    def pdf(out: str, *args) -> list:
        done = subprocess.run([sys.executable, str(PROOF_PDF), "proofs", out, "--repo", str(root), *args],
                              cwd=root, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=300)
        assert done.returncode == 0, f"proof_pdf.py failed:\n{done.stdout}\n{done.stderr}"
        return pages(root / out)

    features = [arg for stem in ("alpha", "beta", "gamma") for arg in ("--feature", f"promises/{stem}.yaml")]
    return {"features": pdf("features.pdf", "--evidence", "evidence", *features), "plain": pdf("plain.pdf"),
            "ledgers": {stem: json.loads((evidence / stem / "ledger.json").read_text(encoding="utf-8"))
                        for stem in ("alpha", "beta", "gamma")}}


def pages(path: Path) -> list:
    """Each page's text: its lines, each line's spaces (also non-breaking ones) as one space."""
    doc = QPdfDocument()
    assert doc.load(str(path)) == QPdfDocument.Error.None_, f"cannot read {path}"
    texts = [[" ".join(line.split()) for line in doc.getAllText(i).text().replace(" ", " ").splitlines()]
             for i in range(doc.pageCount())]
    doc.close()
    return texts


def flat(page: list) -> str:
    return " ".join(page)


def feature_pages(pdf: list, number: int) -> list:
    """The pages of feature `number`: those with its footer."""
    found = [i for i, page in enumerate(pdf) if f"Feature {number}:" in flat(page)]
    assert found, f"no page of feature {number}"
    return found


def first_page_of_change(pdf: list, title: str) -> int:
    return next(i for i, page in enumerate(pdf) if re.search(rf"Change \d+: {re.escape(title)}", flat(page)))


def test_the_cover_counts_each_features_rows_from_its_ledger_and_gives_its_verdict(built):
    """The first page: each feature's invariants, green, amber and red counted from its ledger, and its verdict:
    Not certified: N red; BLOCKED: needs your decision (blocked.json); Certified (no red, not blocked)."""
    cover = flat(built["features"][0])
    assert "Read this page if you read nothing else" in cover, cover
    verdicts = {"alpha": "Not certified: 1 red", "beta": "BLOCKED: needs your decision", "gamma": "Certified"}
    for number, stem in enumerate(("alpha", "beta", "gamma"), 1):
        ledger = built["ledgers"][stem]
        counts = [len(ledger["rows"]), *(ledger["counts"][s] for s in ("GREEN", "AMBER", "RED"))]
        assert re.search(rf"\b{number} {re.escape(ledger['feature'])} {' '.join(map(str, counts))} "
                         rf"{re.escape(verdicts[stem])}", cover), f"{stem}: {counts}, {verdicts[stem]}: {cover}"
    assert built["ledgers"]["alpha"]["counts"] == {"GREEN": 2, "AMBER": 1, "RED": 1}  # the case meant


def test_a_red_row_says_why_it_is_red(built):
    """The evidence ledger's red row (a test failed) shows RED and, under it, why: the failure."""
    pdf = built["features"]
    text = " ".join(flat(pdf[i]) for i in feature_pages(pdf, 1))
    row = re.search(r"\b4 Missed times follow the Misfire setting\. .*?RED Why red: (.*?)(?: Checker output|$)", text)
    assert row, text
    assert WHY_RED in row.group(1), row.group(1)


def test_each_checker_box_shows_its_log_commit_and_exit_code(built):
    """The first line of each checker's box: its log (under the evidence folder), the commit it ran on (7
    characters) and its exit code; then its command and its final lines (at most 12)."""
    pdf = built["features"]
    lines = [line for i in feature_pages(pdf, 1) for line in pdf[i]]
    for c in ALPHA_CHECKERS:
        top = [line for line in lines if f"evidence/{c['log']}" in line]
        assert len(top) == 1, f"{c['name']}: {top}"
        assert f"commit {c['commit'][:7]}" in top[0] and re.search(rf"exit {c['exit']}\b", top[0]), top[0]
        after = " ".join(lines[lines.index(top[0]) + 1:])
        shown = " ".join([f"$ {c['command']}", *c["tail"][-12:]])
        assert after.startswith(shown + " "), f"{c['name']}: {after[:400]}"


def test_trace_validation_comes_before_the_model_check(built):
    """checkers.json lists the model check first; the PDF shows the trace validation's box before it."""
    pdf = built["features"]
    text = " ".join(flat(pdf[i]) for i in feature_pages(pdf, 1))
    assert text.index("evidence/alpha/trace.log") < text.index("evidence/alpha/tlc.log")


def test_a_blocked_feature_says_blocked_with_its_question_and_options(built):
    """Beta (blocked.json) opens with BLOCKED, that it can't be certified until you choose, the question and each
    option; the rest of its pages are there as for any feature."""
    pdf = built["features"]
    beta = feature_pages(pdf, 2)
    first = flat(pdf[beta[0]])
    found = re.search(r"Beta retention BLOCKED: needs your decision I can't certify this until you choose\. "
                      rf"{re.escape(QUESTION)} A\. {re.escape(OPTIONS[0])} B\. {re.escape(OPTIONS[1])}", first)
    assert found, first
    rest = " ".join(flat(pdf[i]) for i in beta)
    assert all(section in rest for section in ("You asked", "Evidence ledger", "Checker output", "It works on screen"))
    assert not any("BLOCKED" in flat(pdf[i]) for i in feature_pages(pdf, 1) + feature_pages(pdf, 3))


def test_each_features_proofs_on_screen_follow_its_pages_and_the_others_come_last(built):
    """The proofs whose module starts with one of a feature's `proofs` come right after its pages; the others
    after all the features."""
    pdf = built["features"]
    order = [feature_pages(pdf, 1)[0], first_page_of_change(pdf, ALPHA_CHANGE), feature_pages(pdf, 2)[0],
             first_page_of_change(pdf, BETA_CHANGE), feature_pages(pdf, 3)[0], first_page_of_change(pdf, OTHER_CHANGE)]
    assert order == sorted(order), order
    assert first_page_of_change(pdf, ALPHA_CHANGE) == feature_pages(pdf, 1)[-1] + 1


def test_without_features_the_pdf_is_as_before(built):
    """The old invocation (no --feature, as CI runs it today) still builds today's PDF: its cover, then each change
    in the order the proofs ran."""
    pdf = built["plain"]
    cover = flat(pdf[0])
    assert "in this proof" in cover.lower() and "All 3 changes passed their proofs." in cover, cover
    assert not any("Read this page" in flat(page) or "Feature 1:" in flat(page) for page in pdf)
    order = [first_page_of_change(pdf, title) for title in (OTHER_CHANGE, ALPHA_CHANGE, BETA_CHANGE)]
    assert order == sorted(order), order
