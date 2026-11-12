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

from PySide6.QtCore import QRectF, QSize  # noqa: E402
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


def checker(name: str, kind: str, exit_code: int, commit: str = SHA, key: str | None = None) -> dict:
    return {"name": name, "kind": kind, "command": f"run-{name} --all", "exit": exit_code, "commit": commit,
            "log": f"alpha/{name}.log", "summary": f"{name} summary line.",
            "tail": [f"{name} line {i}" for i in range(1, 15)], "key": key}


AMBER = {"enforced": "Tests of the service.", "not_green": "Tests try the cases we thought of."}
# The bugs proving Alpha found (alpha.found.yaml), and where it is checked more lightly.
FOUND = [{"what": "Two services could run at once after a restart.", "found_by": "the model check",
          "fixed_in": "abc1234", "guarded_by": "tests/test_x.py::t1"},
         {"what": "A run began a second late.", "found_by": "review", "fixed_in": "def5678",
          "guarded_by": "tests/test_x.py::t2"}]
LIGHTER = "A real PC going to sleep: tests use a fake clock."
# Alpha (4 rows: 2 green, 1 amber, 1 red), its checkers listed with the model check before the trace validation.
# tlc's key is its line 13, among its final lines; trace's matches none of them.
ALPHA_CHECKERS = [checker("tlc", "model", 0, key=r"^tlc line 13$"), checker("trace", "trace", 0, key="no such line"),
                  checker("pytest", "tests", 1, OTHER_SHA)]
# What Alpha's mutation testing leaves out (mutants.json's not_tested): a function, and a single mutant.
NOT_TESTED = [{"function": "alpha/service.py serve", "mutants": 212,
               "reason": "The service process itself: tests start it as a process and see it run on time."},
              {"function": "alpha.service.x_lock__mutmut_3", "mutants": 1,
               "reason": "Windows only: no run where mutation testing runs can execute it."}]


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    """The PDF of five features (Alpha: a red row; Beta: blocked; Gamma: certified; Delta: its mutation testing
    result missing; Epsilon: a checker failed, no red row) and three proofs on screen,
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
    write(promises / "alpha.found.yaml", {"found": FOUND, "lighter": [LIGHTER]})
    write(evidence / "alpha" / "mutants.json", {"score": 95.0, "killed": 38, "total": 40, "threshold": 90,
                                                "survivors": [], "not_tested": NOT_TESTED})
    feature(promises, evidence, "beta", "Beta retention", {
        "B-1": ("Old runs are deleted.", AMBER), "B-2": ("A run going is never deleted.", AMBER),
        "B-3": ("No flow is ever deleted.", AMBER)},
        [result(f"b{n}", f"B-{n}") for n in (1, 2, 3)], ["test_proof_beta"],
        blocked={"question": QUESTION, "options": OPTIONS})
    feature(promises, evidence, "gamma", "Gamma mail", {"G-1": ("A mail is sent once.", AMBER)},
            [result("g1", "G-1")], [])
    feature(promises, evidence, "delta", "Delta export", {"D-1": ("An export is written once.", AMBER)},
            [result("d1", "D-1")], [])
    (evidence / "delta" / "mutants.json").unlink()
    feature(promises, evidence, "epsilon", "Epsilon import", {"E-1": ("An import reads each file once.", AMBER)},
            [result("e1", "E-1")], [], [checker("leaks", "leak", 1)])
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

    features = [arg for stem in ("alpha", "beta", "gamma", "delta", "epsilon") for arg in ("--feature", f"promises/{stem}.yaml")]
    return {"features": pdf("features.pdf", "--evidence", "evidence", *features), "plain": pdf("plain.pdf"), "root": root,
            "ledgers": {stem: json.loads((evidence / stem / "ledger.json").read_text(encoding="utf-8"))
                        for stem in ("alpha", "beta", "gamma", "delta", "epsilon")}}


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
    Not certified: N red; BLOCKED: needs your decision (blocked.json); Not certified when its mutation testing or
    leak check result is missing, or when one of its checkers failed; Certified (no red, not blocked, nothing missing,
    every checker passed)."""
    cover = flat(built["features"][0])
    assert "Read this page if you read nothing else" in cover, cover
    verdicts = {"alpha": "Not certified: 1 red", "beta": "BLOCKED: needs your decision", "gamma": "Certified",
                "delta": "Not certified: no mutation testing result", "epsilon": "Not certified: leaks failed"}
    for number, stem in enumerate(("alpha", "beta", "gamma", "delta", "epsilon"), 1):
        ledger = built["ledgers"][stem]
        counts = [len(ledger["rows"]), *(ledger["counts"][s] for s in ("GREEN", "AMBER", "RED"))]
        assert re.search(rf"\b{number} {re.escape(ledger['feature'])} {' '.join(map(str, counts))} "
                         rf"{re.escape(verdicts[stem])}", cover), f"{stem}: {counts}, {verdicts[stem]}: {cover}"
    assert built["ledgers"]["alpha"]["counts"] == {"GREEN": 2, "AMBER": 1, "RED": 1}  # the case meant


def test_the_words_this_proof_uses_come_after_the_summary_and_before_the_c4_view(built):
    """So that the reader and the proof mean the same: what a test, a proof on screen, a promise, mutation testing and
    a surviving mutant are (one never means the code has a bug), and each kind of checker a feature here ran."""
    pdf = built["features"]
    at = {words: next(i for i, page in enumerate(pdf) if words in flat(page))
          for words in ("Read this page if you read nothing else", "The words this proof uses", "How Taskloom is built")}
    assert list(at.values()) == sorted(at.values()) and len(set(at.values())) == 3, at
    text = flat(pdf[at["The words this proof uses"]])
    for term in ("A test", "A proof on screen", "A promise", "Mutation testing", "A surviving mutant", "A model check",
                 "Trace validation"):
        assert term in text, term
    assert "a survivor is never a bug in your code" in text, text
    # A change that does nothing, shown on a real line of the code: the user asked for examples, not abstractions.
    assert "SELECT id FROM service_on" in text and "select id from service_on" in text, text
    assert "Design health" not in text  # no feature here ran one


def test_a_feature_opens_with_what_its_proof_shows_before_the_details(built):
    """Before what you asked and the evidence, in a highlighted box: what its mutation score means, how many real bugs
    proving it found and what found them, and where it is checked more lightly. After the ledger, each bug, what found
    it, its fix and the test that now guards it."""
    pdf = built["features"]
    text = " ".join(flat(pdf[i]) for i in feature_pages(pdf, 1))
    shows, asked = text.index("What this proof shows"), text.index("You asked")
    assert shows < asked, text[:600]
    box = text[shows:asked]
    assert "The tests caught 38 of the 40 bugs planted in the code on purpose (95%)" in box, box
    assert "2 real bugs were found while proving it, and fixed: 1 by the model check, 1 by review" in box, box
    assert "Checked more lightly" in box and LIGHTER in box, box
    listed = text[text.index("Bugs found while proving it"):]
    for bug in FOUND:
        assert all(bug[k] in listed for k in ("what", "found_by", "fixed_in", "guarded_by")), (bug, listed[:800])


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


def test_a_checkers_key_line_is_boxed_in_orange_as_on_the_screenshots(built):
    """The line of a checker's output that its key matches (what to look at, as the box on a screenshot) has an
    orange box around it, in the screenshots' orange, and only that line is inside the box."""
    doc = QPdfDocument()
    assert doc.load(str(built["root"] / "features.pdf")) == QPdfDocument.Error.None_
    pdf = built["features"]
    page = next(i for i in feature_pages(pdf, 1) if "tlc line 13" in pdf[i])
    scale = 2
    size = doc.pagePointSize(page)
    image = doc.render(page, QSize(round(size.width() * scale), round(size.height() * scale)))
    dark = [(x, y) for y in range(0, image.height(), 2) for x in range(0, image.width(), 2)
            if image.pixelColor(x, y).name() == "#0d1117"]  # the terminals' background
    assert dark, "no terminal on the page"
    left, right = min(x for x, _ in dark), max(x for x, _ in dark)
    top, bottom = min(y for _, y in dark), max(y for _, y in dark)

    def orange(x, y):
        c = image.pixelColor(x, y)
        return c.red() > 180 and 50 < c.green() < 130 and c.blue() < 70

    box = [(x, y) for y in range(top, bottom) for x in range(left, right) if orange(x, y)]
    assert box, "no orange box in the checkers' output"
    x0, x1 = min(x for x, _ in box) / scale, max(x for x, _ in box) / scale
    y0, y1 = min(y for _, y in box) / scale, max(y for _, y in box) / scale
    frame = QRectF(x0, y0, x1 - x0, y1 - y0)
    text = doc.getAllText(page).text()

    def where(line: str) -> QRectF:  # where the line's text is on the page, in points
        return doc.getSelectionAtIndex(page, re.search(rf"{line}(?!\d)", text).start(), len(line)).boundingRectangle()

    around = {line: where(line) for line in ("tlc line 12", "tlc line 13", "tlc line 14")}
    doc.close()
    assert frame.contains(around["tlc line 13"]), (frame, around)
    assert not frame.intersects(around["tlc line 12"]) and not frame.intersects(around["tlc line 14"]), (frame, around)


def test_a_checker_whose_key_matches_none_of_its_final_lines_says_so(built):
    """No box is drawn round nothing: when no shown line matches a checker's key, its box says so in red."""
    text = " ".join(flat(built["features"][i]) for i in feature_pages(built["features"], 1))
    assert "✗ None of its final lines shows what to look for (no such line)" in text, text


def test_trace_validation_comes_before_the_model_check(built):
    """checkers.json lists the model check first; the PDF shows the trace validation's box before it."""
    pdf = built["features"]
    text = " ".join(flat(pdf[i]) for i in feature_pages(pdf, 1))
    assert text.index("evidence/alpha/trace.log") < text.index("evidence/alpha/tlc.log")


def test_what_mutation_testing_leaves_out_is_listed_with_its_number_of_mutants_and_what_checks_it(built):
    """Under its own heading, each function or single mutant mutants.json lists as not tested: its number of mutants
    and its reason (which tests and proofs check it instead), so none is left out unseen."""
    pdf = built["features"]
    text = " ".join(flat(pdf[i]) for i in feature_pages(pdf, 1))
    assert "Not mutation-tested, and what checks it instead" in text, text
    listed = text[text.index("Not mutation-tested, and what checks it instead"):]
    for n in NOT_TESTED:
        count = f"{n['mutants']:,} mutant{'s' * (n['mutants'] != 1)}"
        assert re.search(rf"{re.escape(n['function'])} {count}\b.*? {re.escape(n['reason'])}", listed), listed


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


def test_a_row_tried_over_a_range_is_amber_when_its_evidence_says_what_the_range_leaves_out(tmp_path):
    """A row whose test tries every case of a range is GREEN only when that range is the whole row: when its evidence
    says why it is not green (a part no test tries every case of), it is AMBER, with that reason."""
    promises, evidence = tmp_path / "promises", tmp_path / "evidence"
    partly = "Only its time is tried at every second; how soon it begins after it is checked by tests."
    feature(promises, evidence, "delta", "Delta service", {
        "D-1": ("A run begins at its time.", {"enforced": "Tried at every second."}),
        "D-2": ("A run begins at its time, and soon after it.", {"enforced": "Tried at every second.",
                                                                  "not_green": partly})},
            [result("d1", "D-1", every="every second of a day"), result("d2", "D-2", every="every second of a day")],
            [])
    rows = {r["row"]: r for r in json.loads((evidence / "delta" / "ledger.json").read_text(encoding="utf-8"))["rows"]}
    assert (rows["D-1"]["status"], rows["D-2"]["status"]) == ("GREEN", "AMBER"), rows
    assert rows["D-2"]["why"] == partly, rows["D-2"]


def small_pdf(root: Path, *stems: str) -> list:
    """The pages of the PDF of these features alone: no proof on screen and no release-wide numbers."""
    features = [arg for stem in stems for arg in ("--feature", f"promises/{stem}.yaml")]
    done = subprocess.run([sys.executable, str(PROOF_PDF), "proofs", "small.pdf", "--repo", str(root), "--evidence",
                           "evidence", *features], cwd=root, env={**os.environ, "GIT_CEILING_DIRECTORIES": str(root)},
                          capture_output=True, encoding="utf-8", errors="replace", timeout=300)
    assert done.returncode == 0, f"proof_pdf.py failed:\n{done.stdout}\n{done.stderr}"
    return pages(root / "small.pdf")


def test_a_leak_check_is_asked_only_of_a_feature_that_lists_one(tmp_path):
    """A bug fix or small feature has nothing that runs for weeks, so it lists no leak check: with no leak result it
    is Certified, and its pages have no Memory and leaks section. A feature that lists a leak check which no job ran
    is not certified, and its pages say the result was not found."""
    promises, evidence = tmp_path / "promises", tmp_path / "evidence"
    feature(promises, evidence, "zeta", "Zeta styles", {"Z-1": ("A style of your own is kept.", AMBER)},
            [result("z1", "Z-1")], [], [checker("mutation", "mutation", 0)])
    feature(promises, evidence, "eta", "Eta service", {"H-1": ("It runs for weeks.", AMBER)},
            [result("h1", "H-1")], [], [{**checker("leaks", "leak", 0), "exit": "not run"}])
    for stem in ("zeta", "eta"):
        (evidence / stem / "leak.json").unlink()

    pdf = small_pdf(tmp_path, "zeta", "eta")

    cover = flat(pdf[0])
    assert re.search(r"\b1 Zeta styles 1 0 1 0 Certified\b", cover), cover
    assert re.search(r"\b2 Eta service 1 0 1 0 Not certified: no leak check result", cover), cover
    zeta, eta = (" ".join(flat(pdf[i]) for i in feature_pages(pdf, number)) for number in (1, 2))
    assert "Surviving mutants" in zeta and "Memory and leaks" not in zeta, zeta
    assert "Memory and leaks" in eta and "Not found: evidence/eta/leak.json" in eta, eta


def test_missed_mutants_of_one_kind_are_under_its_one_reason_and_what_was_mutated_is_said(tmp_path):
    """mutants.json of a feature that mutation-tests only what it changed (scope) and explains its missed mutants by
    kind: the PDF says what the bugs were planted in, with how many each; each kind's reason is written once, over its
    mutants' changes; a mutant with a reason of its own is listed as before."""
    promises, evidence = tmp_path / "promises", tmp_path / "evidence"
    feature(promises, evidence, "zeta", "Zeta styles", {"Z-1": ("A style of your own is kept.", AMBER)},
            [result("z1", "Z-1")], [], [checker("mutation", "mutation", 0)])
    kind, why, own = "The same style, written another way", "An email program reads both the same way.", \
        "Nothing reads this value."
    missed = [{"where": "zeta/report.py html_table", "name": f"zeta.report.x_html_table__mutmut_{n}",
               "change": f"border: {n}px to BORDER: {n}px", "kind": kind if n < 4 else "",
               "why_equivalent": why if n < 4 else own} for n in (1, 2, 3, 4)]
    write(evidence / "zeta" / "mutants.json", {
        "score": 92.0, "killed": 46, "total": 50, "threshold": 90, "survivors": missed,
        "scope": [{"function": "zeta/report.py html_table", "mutants": 44}, {"function": "zeta/blocks.py", "mutants": 6}]})

    pdf = small_pdf(tmp_path, "zeta")

    text = " ".join(flat(pdf[i]) for i in feature_pages(pdf, 1))
    listed = text[text.index("Surviving mutants, one line each"):]
    assert "Planted only in what this feature changed: zeta/report.py html_table (44), zeta/blocks.py (6)" in listed, \
        listed
    assert listed.count(why) == 1 and listed.count(own) == 1, listed
    assert re.search(rf"{re.escape(kind)} 3 mutants\. {re.escape(why)} .*border: 1px to BORDER: 1px.*border: 3px to "
                     rf"BORDER: 3px", listed), listed
    assert re.search(rf"zeta/report\.py html_table border: 4px to BORDER: 4px\. {re.escape(own)}", listed), listed
