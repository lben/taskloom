"""The proof PDF (packaging/proof_pdf.py) is easy to move around in. On its cover, each change's title in 'In this
proof' is a link to that change's first page, as each title in the New list ("New since <base tag>") already is.
Each page of a change (its story pages, its step pages, its 'What was checked' page, also a second page one of these
runs on to) has one small go-back link at its top, over nothing else, to that change's row in 'In this proof': the
cover page that holds the row, at the row. The cover's own pages have no go-back link.

Each PDF is built by the real script, as CI builds it, from proof records like the ones tests/proofs writes (named as
real ones are), in a test repository made with the real git program (Git config of the test's own) with a v tag, so
that the New list is there; it is read back with QtPdf: QPdfLinkModel's links (the page each goes to and where on it)
and the text each page shows, with where each piece of it is. Only what is on a page's paper is read: Qt can also
write, clipped out of sight above a page's top, lines of the page before."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex, QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtPdf import QPdfDocument, QPdfLinkModel  # noqa: E402

from taskloom import __version__  # noqa: E402

PROOF_PDF = Path(__file__).resolve().parents[1] / "packaging" / "proof_pdf.py"
GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="what is new is found with the git program, not installed")

COUNT, NEW = 24, {3, 8, 13, 18, 23}  # the changes of the large PDF, and the new ones among them
SHOTS = 2  # its change with screenshots: 3 step pages, the last one a step's second screenshot ("continued")
MANY = 5  # its change with many long checks: its step page and its 'What was checked' run on to more pages
# The summaries (each proof's first idea) are made of these, 1 to 3 of them: one or two lines on the cover.
CLAUSES = ["The block now shows its result in the Log as soon as the run ends",
           "and the canvas keeps every line where you drew it",
           "so you can read what passed through the flow without opening another window"]
NEAR = 24  # points: how far above its title a row's top can be (its padding and border)
TOP = 0.15  # of a page's height: its top, where the go-back link is


def own_git(folder: Path) -> dict:
    """The environment for Git of the test's own: an empty global config, no system config, none of the developer's
    GIT_* variables, and no repository found above `folder`."""
    config = folder / "gitconfig"
    config.write_bytes(b"")
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    return {**env, "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(folder.resolve())}


def git(folder: Path, env: dict, *args):
    done = subprocess.run([GIT, "-C", str(folder), *map(str, args)], capture_output=True, encoding="utf-8",
                          errors="replace", timeout=60, env=env)
    assert done.returncode == 0, f"git {' '.join(map(str, args))}: {done.stderr}"


def squashed(text: str) -> str:
    return "".join(text.split()).lower()


# --- the changes' proof records, as tests/proofs/conftest.py writes them -------------------------------------------

class Change:
    def __init__(self, number: int, new: bool = False):
        self.number, self.new = number, new
        self.scenario = f"test_proof_a.test_{'new' if new else 'old'}_{number:02}"
        self.title = f"The report block keeps case {number:02} in view"
        clauses = ", ".join(CLAUSES[(number + k) % len(CLAUSES)] for k in range(1 + number % 3))
        self.summary = clauses[0].upper() + clauses[1:] + "."

    def entries(self) -> list:
        if self.number == SHOTS:
            return [{"kind": "step", "text": "Open the nightly flow in the editor, choose the report block on the "
                                             "canvas and press Run now in the toolbar: the run starts at once, its "
                                             "blocks light up on the canvas one after the other and the Log fills in "
                                             "under them, a line for each block as it ends."},
                    {"kind": "shot", "file": "01.png", "caption": "The editor, the run going on."},
                    {"kind": "check", "text": "The run started", "ok": True},
                    {"kind": "step", "text": "Open the Log."},
                    {"kind": "shot", "file": "02.png", "caption": "The Log, its first part."},
                    {"kind": "shot", "file": "03.png", "caption": "The Log, scrolled to its end."},
                    {"kind": "check", "text": "The Log shows each block", "ok": True}]
        if self.number == MANY:
            return [{"kind": "step", "text": "Run the load flow."},
                    *({"kind": "check", "ok": True,
                       "text": f"Check {k}: the report block's result for case {k} is in the Log, in the row of the "
                               "run that made it, with the time it ended, its status and what passed through it, "
                               "the same as the history of the run keeps it for the next time the flow is opened"}
                      for k in range(1, 41))]
        return [{"kind": "step", "text": "Open the flow."}, {"kind": "check", "text": "It opened", "ok": True},
                {"kind": "step", "text": "Press Run now."}, {"kind": "check", "text": "It ran", "ok": True}]

    def write(self, proofs: Path):
        folder = proofs / self.scenario
        folder.mkdir(parents=True)
        for entry in self.entries():
            if entry["kind"] == "shot":
                image = QImage(1280, 800, QImage.Format_RGB32)
                image.fill(QColor("#dfe6ee"))
                assert image.save(str(folder / entry["file"])), f"cannot save {entry['file']}"
        (folder / "proof.json").write_text(json.dumps({
            "title": self.title, "asked": "Can it do this?", "status": "passed", "error": "", "system": "Windows",
            "why_not_windows": "", "started": f"2026-10-01T10:{self.number:02}:00+00:00", "version": __version__,
            "idea": [self.summary], "why_simplest": ["It reuses what is there."],
            "alternatives": [{"option": "A separate window", "why_not": "One more thing to learn."}],
            "fits": ["As the rest of the editor does."], "entries": self.entries(),
            "platform": "Windows-11 · Python 3.12 · PySide6 6.9"}), encoding="utf-8")


def build(folder: Path, changes: list, repository: bool = True) -> Path:
    """The PDF the real script makes of these changes' proof records: in a repository whose v0.1.0 had the scenarios
    of the changes that are not new (the New list: "New since v0.1.0"), or, not `repository`, in a folder that is
    not one (no New list)."""
    env = own_git(folder)
    repo = folder / "repo"
    module = repo / "tests" / "proofs" / "test_proof_a.py"
    module.parent.mkdir(parents=True)
    if repository:
        git(repo, env, "init", "-q")
    for tag, listed in (("v0.1.0", [c for c in changes if not c.new]), ("", changes)):
        module.write_text("".join(f"\n\ndef {c.scenario.split('.')[1]}(proof, win, qtbot):\n    pass\n"
                                  for c in listed), encoding="utf-8", newline="")
        if repository:
            git(repo, env, "add", "-A")
            git(repo, env, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "proofs")
            if tag:
                git(repo, env, "tag", tag)
    proofs = folder / "proofs"
    for c in changes:
        c.write(proofs)
    (proofs / "session.json").write_text(json.dumps({"exitstatus": 0, "system": "Windows"}), encoding="utf-8")
    out = folder / "proof.pdf"
    done = subprocess.run([sys.executable, str(PROOF_PDF), str(proofs), str(out), "--repo", str(repo)],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=300, cwd=folder, env=env)
    assert done.returncode == 0, f"proof_pdf.py failed:\n{done.stdout}\n{done.stderr}"
    return out


# --- the PDF read back with QtPdf ----------------------------------------------------------------------------------

class Link(NamedTuple):
    """A link to a page of the PDF: where it is (points from its page's top left), the page it goes to and where on
    it, the text it is on (squashed), and other text under it."""
    rects: list
    to: int
    at: QPointF
    on: str
    covers: str


class Place(NamedTuple):
    """Where a change's title is on the cover: the page, and the top and bottom of its first letter."""
    page: int
    top: float
    bottom: float


def one_link(a: QRectF, b: QRectF) -> bool:
    """Whether two rectangles of links to the same place are parts of one link: side by side on a line (Qt writes a
    link for each run of text in one font)."""
    return a.top() < b.bottom() and b.top() < a.bottom() and max(a.left(), b.left()) - min(a.right(), b.right()) < 8


class Pdf:
    """Each change's pages (those with its footer), the cover's (those before change 1's), each page's height and
    links to pages of the PDF on its paper (in the order they are on it), and where each title is on the cover."""

    def __init__(self, path: Path, changes: list):
        doc = QPdfDocument()
        assert doc.load(str(path)) == QPdfDocument.Error.None_, f"cannot read {path}"
        count = doc.pageCount()
        self.height = [doc.pagePointSize(page).height() for page in range(count)]
        texts = [doc.getAllText(page).text() for page in range(count)]
        flat = [" ".join(text.lower().split()) for text in texts]
        self.pages = {c.number: [page for page, text in enumerate(flat) if f"change {c.number}: {c.title.lower()}"
                                 in text] for c in changes}
        assert all(self.pages.values()), f"a change with no page: {self.pages}"
        self.first = {number: pages[0] for number, pages in self.pages.items()}
        self.cover = range(min(self.first.values()))
        self.links = [self.read_links(doc, page, texts[page]) for page in range(count)]
        self.titles = {}
        for c in changes:
            pattern = re.compile(r"\s*".join(map(re.escape, squashed(c.title))))
            places = []
            for page in self.cover:
                for found in pattern.finditer(texts[page].lower()):
                    box = doc.getSelectionAtIndex(page, found.start(), 1).boundingRectangle()
                    if 0 <= box.center().y() <= self.height[page]:
                        places.append(Place(page, box.top(), box.bottom()))
            self.titles[c.number] = sorted(places)
        doc.close()

    def read_links(self, doc: QPdfDocument, page: int, text: str) -> list:
        model = QPdfLinkModel()
        model.setDocument(doc)
        model.setPage(page)
        raw = {}
        for row in range(model.rowCount(QModelIndex())):
            link = model.data(model.index(row, 0), QPdfLinkModel.Role.Link.value)
            rects = link.rectangles()
            if link.page() >= 0 and rects and all(0 <= r.center().y() <= self.height[page] for r in rects):
                # Qt's model can list a link once for each of its rectangles: each link once.
                raw[(tuple(r.getRect() for r in rects), link.page())] = (list(rects), link.page(), link.location())
        merged = []
        for rects, to, at in raw.values():
            same = next((m for m in merged if m[1] == to and m[2] == at
                         and any(one_link(a, b) for a in m[0] for b in rects)), None)
            if same:
                same[0].extend(rects)
            else:
                merged.append((rects, to, at))
        # The characters (and where each is) of the lines that meet a link.
        chars = []
        for line in re.finditer(r"[^\r\n]+", text):
            box = doc.getSelectionAtIndex(page, line.start(), len(line.group())).boundingRectangle()
            if any(box.intersects(r) for rects, _, _ in merged for r in rects):
                chars += [(text[i], doc.getSelectionAtIndex(page, i, 1).boundingRectangle())
                          for i in range(line.start(), line.end()) if not text[i].isspace()]
        links = []
        for rects, to, at in merged:
            inside = [any(r.contains(box.center()) for r in rects) for _, box in chars]
            on = "".join(char for (char, _), within in zip(chars, inside) if within)
            covers = "".join(char for (char, box), within in zip(chars, inside)
                             if not within and any(r.adjusted(.5, .5, -.5, -.5).intersects(box) for r in rects))
            links.append(Link(rects, to, at, squashed(on), covers))
        return sorted(links, key=lambda link: (min(r.top() for r in link.rects), min(r.left() for r in link.rects)))

    def row(self, number: int) -> Place:
        """Where change `number`'s row in 'In this proof' is: its title's last place on the cover."""
        return self.titles[number][-1]


def cover_links(pdf: Pdf, changes: list) -> tuple[list, list]:
    """The links on the cover, in the order they are on it: (the page each is on, the text it is on, the page it goes
    to); and the ones there should be: the New list's (each new change's title, on its row's page, to the change's
    first page) then 'In this proof''s (each change's title, on its row's page, to its first page)."""
    for c in changes:
        assert len(pdf.titles[c.number]) == (2 if c.new else 1), \
            f"change {c.number}'s title is not {'twice' if c.new else 'once'} on the cover: {pdf.titles[c.number]}"
    found = [(page, link.on, link.to) for page in pdf.cover for link in pdf.links[page]]
    expected = ([(pdf.titles[c.number][0].page, squashed(c.title), pdf.first[c.number]) for c in changes if c.new]
                + [(pdf.row(c.number).page, squashed(c.title), pdf.first[c.number]) for c in changes])
    return found, expected


def describe(found: list, expected: list) -> str:
    missing = [link for link in expected if link not in found]
    other = [link for link in found if link not in expected]
    return (f"(page index it is on, text it is on, page index it goes to)\nmissing: {missing}\nnot expected: "
            f"{other}\nall found: {found}")


def go_back_problems(pdf: Pdf, changes: list) -> list:
    """What is wrong with the go-back links of the changes' pages: a page without one (or with more), one that does
    not go to the cover page holding the change's row in 'In this proof', or not to the row, one not at the top
    of its page, one over other text, one not on the same short words as the others."""
    problems, wordings = [], {}  # {the text a go-back link is on: the pages it is so on}
    for c in changes:
        row = pdf.row(c.number)
        for page in pdf.pages[c.number]:
            where = f"page {page + 1} (change {c.number}, its row on cover page {row.page + 1})"
            links = pdf.links[page]
            if len(links) != 1:
                problems.append(f"{where}: {len(links)} links to the PDF's pages, not one go-back link: "
                                f"{[(link.on, link.to) for link in links]}")
                continue
            link = links[0]
            wordings.setdefault(link.on, []).append(page + 1)
            if link.to != row.page:
                problems.append(f"{where}: its go-back link goes to page {link.to + 1}")
            elif not row.top - NEAR <= link.at.y() <= row.bottom + 2:
                problems.append(f"{where}: its go-back link goes to y {link.at.y():.0f} of that page, not to the "
                                f"row (its title at y {row.top:.0f} to {row.bottom:.0f})")
            bottom = max(r.bottom() for r in link.rects)
            if bottom > TOP * pdf.height[page]:
                problems.append(f"{where}: its go-back link is not at the page's top: it ends at y {bottom:.0f} of "
                                f"{pdf.height[page]:.0f}")
            if link.covers:
                problems.append(f"{where}: its go-back link ({link.on!r}) is over other text: {link.covers!r}")
    if wordings:
        usual = max(wordings, key=lambda wording: len(wordings[wording]))
        problems += [f"pages {pages}: the go-back link is on {wording!r}, on the other pages on {usual!r} (over "
                     "other text, or other words)" for wording, pages in wordings.items() if wording != usual]
        if not (re.search(r"[a-z]", usual) and len(usual) <= 30):
            problems.append(f"the go-back links are on {usual!r}: not a few words")
    return problems


# --- the tests -----------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def large(tmp_path_factory, qapp):
    """A PDF as large as a release's: 24 changes, 5 of them new, a cover of several pages."""
    changes = [Change(number, number in NEW) for number in range(1, COUNT + 1)]
    return Pdf(build(tmp_path_factory.mktemp("links"), changes), changes), changes


@needs_git
def test_each_change_in_this_proof_is_a_link_to_its_first_page(large):
    """Spec 1 and 4, the user's case: on a cover of several pages, each change's title in 'In this proof' is a link,
    on the cover page that holds its row, to the change's first page, also when its row is on the cover's second
    page or later; the New list's links stay as they are (each new change's title, to its first page). Spec 3: the
    cover has no other link to the PDF's pages (no go-back link on the cover)."""
    pdf, changes = large
    assert len({pdf.row(c.number).page for c in changes}) >= 2, \
        f"the rows of 'In this proof' should be on several pages here: {[pdf.row(c.number) for c in changes]}"
    found, expected = cover_links(pdf, changes)
    assert found == expected, describe(found, expected)


@needs_git
def test_every_page_of_a_change_has_a_go_back_link_to_its_row_on_the_cover(large):
    """Spec 2 and 4, the user's case: every page of every change (its two story pages, its step pages, a step's
    second screenshot, its 'What was checked' page, and the pages these run on to) has one go-back link, at the top
    of the page and over no other text, that goes to the cover page holding the change's row in 'In this proof', at
    the row (a row low on its page: not the page's top; a new change: its row in 'In this proof', not in the New
    list). The links are short, the same on every page. Spec 3: the go-back link pushes nothing to an extra page."""
    pdf, changes = large
    assert len(pdf.pages[MANY]) > 4, f"change {MANY}'s pages should run on to more pages: {pdf.pages[MANY]}"
    problems = go_back_problems(pdf, changes)
    assert problems == [], "\n".join(problems)

    # Each page as before: a change of 2 steps without screenshots, 4 pages (its story, its steps, what was
    # checked); the one with screenshots, 6 (a page for each screenshot).
    counts = {c.number: len(pdf.pages[c.number]) for c in changes if c.number != MANY}
    assert counts == {number: 6 if number == SHOTS else 4 for number in counts}, counts


def test_without_the_new_list_too_each_title_and_page_links_to_the_other(tmp_path, qapp):
    """Spec 1, 2 and 4, when the cover has no New list (the PDF built where there is no Git history, so nothing is
    said to be new) and takes one page: each title in 'In this proof' links to its change's first page, and each page
    of a change goes back to that change's own row, lower down on that one page (not to the page's top)."""
    changes = [Change(number) for number in range(1, 4)]
    pdf = Pdf(build(tmp_path, changes, repository=False), changes)

    assert len(pdf.cover) == 1, f"the cover should take one page here: {len(pdf.cover)}"
    found, expected = cover_links(pdf, changes)
    assert found == expected, describe(found, expected)
    problems = go_back_problems(pdf, changes)
    assert problems == [], "\n".join(problems)
