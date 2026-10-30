"""The proof PDF's cover (packaging/proof_pdf.py) lists the changes in rows: under "New in <version>" (or "New since
<base tag>") each new change with its number, title (a link to its first page), result, system and page; under
"In this proof" every change, with its summary too. When the cover takes several pages, each row is whole on one
page: a row that does not fit at the bottom of a page starts the next one. (In the released 0.25.0 PDF the New
list's last row had its title, result and page on page 1, and its number and "on Windows" on page 2.)

Each PDF is built by the real script from proof records like the ones tests/proofs writes, in a test repository
made with the real git program (Git config of the test's own), as large as a release's (some 30 to 40 changes, a
cover of several pages), and read back with QtPdf. A row is looked for in the text each page shows on its paper:
Qt also writes, clipped out of sight above a page's top, lines of the page before, which are not read."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex  # noqa: E402
from PySide6.QtPdf import QPdfDocument, QPdfLinkModel  # noqa: E402

from taskloom import __version__  # noqa: E402

PROOF_PDF = Path(__file__).resolve().parents[1] / "packaging" / "proof_pdf.py"
GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="what is new is found with the git program, not installed")

# The summaries (each proof's first idea) are made of these, 2 to 6 of them: one to three lines on the cover.
CLAUSES = ["The block now shows its result in the Log as soon as the run ends",
           "and the canvas keeps every line where you drew it",
           "so you can read what passed through the flow without opening another window",
           "while the scheduler goes on running the saved version of each flow"]


@pytest.fixture(autouse=True)
def own_git(tmp_path, monkeypatch):
    """Git of the test's own: an empty global config, no system config, none of the developer's GIT_* variables,
    and no repository found above the test's folder."""
    config = tmp_path / "gitconfig"
    config.write_bytes(b"")
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve()))


def git(folder: Path, *args):
    done = subprocess.run([GIT, "-C", str(folder), *map(str, args)], capture_output=True, encoding="utf-8",
                          errors="replace", timeout=60)
    assert done.returncode == 0, f"git {' '.join(map(str, args))}: {done.stderr}"


def squashed(text: str) -> str:
    return "".join(text.split()).lower()


def loose(text: str) -> str:
    """A pattern for `text` in a page's shown text (squashed lines joined by '|'): it may run on to a next line."""
    return r"\|?".join(map(re.escape, squashed(text)))


class Change:
    def __init__(self, number: int, new: bool):
        self.number, self.new = number, new
        self.scenario = f"test_proof_a.test_{'new' if new else 'old'}_{number:02}"
        self.title = f"The report block keeps case {number:02} in view"
        clauses = ", ".join(CLAUSES[(number + k) % len(CLAUSES)] for k in range(2 * (1 + number % 3)))
        self.summary = clauses[0].upper() + clauses[1:] + "."
        self.system = "Windows"


def build(tmp_path: Path, changes: list, release: bool) -> Path:
    """The PDF the real script makes of these changes' proof records, in the repository of a release (HEAD tagged
    v<version>: "New in <version>") or of a later commit ("New since v0.1.0"); the new ones were not at v0.1.0."""
    repo = tmp_path / "repo"
    module = repo / "tests" / "proofs" / "test_proof_a.py"
    module.parent.mkdir(parents=True)
    git(repo, "init", "-q")
    for tag, listed in (("v0.1.0", [c for c in changes if not c.new]), (f"v{__version__}" if release else "", changes)):
        module.write_text("".join(f"\n\ndef {c.scenario.split('.')[1]}(proof, win, qtbot):\n    pass\n"
                                  for c in listed), encoding="utf-8", newline="")
        git(repo, "add", "-A")
        git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "proofs")
        if tag:
            git(repo, "tag", tag)
    proofs = tmp_path / "proofs"
    for c in changes:
        folder = proofs / c.scenario
        folder.mkdir(parents=True)
        (folder / "proof.json").write_text(json.dumps({
            "title": c.title, "asked": "Can it do this?", "status": "passed", "error": "", "system": c.system,
            "why_not_windows": "", "started": f"2026-10-01T10:{c.number:02}:00+00:00", "version": __version__,
            "idea": [c.summary], "why_simplest": ["It reuses what is there."],
            "alternatives": [{"option": "A separate window", "why_not": "One more thing to learn."}],
            "fits": ["As the rest of the editor does."],
            "entries": [{"kind": "step", "text": "Open the flow."}, {"kind": "check", "text": "It opened", "ok": True}],
            "platform": "Windows-11 · Python 3.12 · PySide6 6.9"}), encoding="utf-8")
    (proofs / "session.json").write_text(json.dumps({"exitstatus": 0, "system": "Windows"}), encoding="utf-8")
    out = tmp_path / "proof.pdf"
    done = subprocess.run([sys.executable, str(PROOF_PDF), str(proofs), str(out), "--repo", str(repo)],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=180, cwd=tmp_path)
    assert done.returncode == 0, f"proof_pdf.py failed:\n{done.stdout}\n{done.stderr}"
    return out


class Cover:
    """The PDF read back: the first page of each change (the first with its footer); the cover's pages (those
    before change 1's), the text each shows on its paper (each line squashed, the lines joined by '|'), and its
    links to pages of the PDF, in the order they are on the cover: (the page they are on, the page they go to)."""

    def __init__(self, path: Path, changes: list):
        doc = QPdfDocument()
        assert doc.load(str(path)) == QPdfDocument.Error.None_, f"cannot read {path}"
        flat = [" ".join(doc.getAllText(page).text().lower().split()) for page in range(doc.pageCount())]
        self.first = {}
        for c in changes:
            pages = [page for page, text in enumerate(flat) if f"change {c.number}: {c.title.lower()}" in text]
            assert pages, f"no page of change {c.number}"
            self.first[c.number] = pages[0]
        self.pages = range(self.first[changes[0].number])
        self.shown, links = [], {}
        model = QPdfLinkModel()
        model.setDocument(doc)
        for page in self.pages:
            height = doc.pagePointSize(page).height()
            text = doc.getAllText(page).text()
            on_paper = [line.group() for line in re.finditer(r"[^\r\n]+", text) if 0 <= doc.getSelectionAtIndex(
                page, line.start(), len(line.group())).boundingRectangle().center().y() <= height]
            self.shown.append("|".join(squashed(line) for line in on_paper))
            model.setPage(page)
            for row in range(model.rowCount(QModelIndex())):
                link = model.data(model.index(row, 0), QPdfLinkModel.Role.Link.value)
                rects = link.rectangles()
                if link.page() >= 0 and rects and all(0 <= r.center().y() <= height for r in rects):
                    # Qt's model can list a link once for each of its rectangles: each link once.
                    links[(page, min(r.top() for r in rects), tuple(r.getRect() for r in rects))] = link.page()
        self.links = [(page, to) for (page, _, _), to in sorted(links.items())]
        doc.close()


def where(cover: Cover, pattern: str) -> list:
    """(page, offset in its shown text, the match) of each page whose shown text has `pattern`."""
    return [(page, found.start(), found) for page, text in enumerate(cover.shown)
            if (found := re.search(pattern, text))]


def check_rows(cover: Cover, changes: list) -> list:
    """What is wrong with the rows of the New list and of 'In this proof': a row not whole on one page (its number,
    title, NEW label, summary, result, system and page, as they follow each other in the row), a row showing a page
    that is not its change's first page, rows out of order, a row (of either list) whose link is not on its page or
    does not go to its change's first page."""
    problems, places = [], {"new": [], "all": []}
    for listed, rows in (("new", [c for c in changes if c.new]), ("all", changes)):
        for c in rows:
            about = loose(c.title)
            if listed == "all":
                about += (rf"\W*{loose('new')}" if c.new else "") + rf"\W*{loose(c.summary)}"
            # The result's mark (✓) may read back as any one character.
            pattern = (rf"(?<!\d){c.number}\W*{about}\W*.?\W*{loose('passed')}\W*{loose('on ' + c.system)}"
                       rf"\W*{loose('page')}\|?(\d+)")
            name = f"{'New list' if listed == 'new' else 'In this proof'} row {c.number} ({c.title})"
            found = where(cover, pattern)
            if len(found) != 1:
                problems.append(f"{name} is not whole on one page: whole on pages {[p + 1 for p, _, _ in found]}, "
                                f"its title on pages {[p + 1 for p, _, _ in where(cover, loose(c.title))]}")
                places[listed].append(None)
                continue
            page, offset, match = found[0]
            shown = int(match.group(1))
            if shown != cover.first[c.number] + 1:
                problems.append(f"{name} says page {shown}: change {c.number} starts on page "
                                f"{cover.first[c.number] + 1}")
            places[listed].append((page, offset))
    for listed, at in places.items():
        whole = [place for place in at if place]
        if whole != sorted(whole):
            problems.append(f"the rows of {'the New list' if listed == 'new' else 'In this proof'} are not in "
                            f"the changes' order: {whole}")
    if all(places["new"]) and all(places["all"]) and places["new"] and max(places["new"]) > min(places["all"]):
        problems.append("the New list is not before 'In this proof'")
    expected = [(place[0] if place else "its row's page", cover.first[c.number])
                for listed, rows in (("new", [c for c in changes if c.new]), ("all", changes))
                for place, c in zip(places[listed], rows)]
    if cover.links != expected:
        problems.append(f"the links of the New list, then of 'In this proof' (the page each is on, the page index "
                        f"it goes to): {cover.links}; expected {expected}")
    return problems


@pytest.mark.parametrize("count, new_numbers, release", [
    (38, range(31, 39), True),
    (30, range(3, 31, 3), False),
    (40, range(3, 41, 5), True),
], ids=["a release, its last 8 changes new", "a later commit, new since the last release",
        "a release of 40 changes, 8 new among them"])
def test_each_row_of_the_cover_lists_is_whole_on_one_page(tmp_path, qapp, count, new_numbers, release):
    """Spec 1: the user's case. On a cover of several pages, every row of the New list ("New in <version>", or
    "New since v0.1.0") and of 'In this proof' has its number, title, NEW label (new changes only), summary
    ('In this proof'), result, system and page on the same page. Spec 2: the rows keep the changes' order, the New
    list before 'In this proof'; each row's page is its change's first page, and each row (of either list) is a link, on
    the page the row is on, to that page (after all the cover's pages, however many it takes)."""
    changes = [Change(number, number in new_numbers) for number in range(1, count + 1)]
    cover = Cover(build(tmp_path, changes, release), changes)

    assert len(cover.pages) >= 2, "the cover should take several pages here"
    heading = f"new in {__version__}" if release else "new since v0.1.0"
    assert re.search(loose(heading), cover.shown[0]), f"no {heading!r} on the cover's first page: {cover.shown[0]}"
    problems = check_rows(cover, changes)
    assert problems == [], "\n".join(problems)
