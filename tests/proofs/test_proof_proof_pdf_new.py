"""Proof: the proof PDF opens with what is new in this version, each new change a link to its pages, and marks the new
changes NEW (v0.24). How proofs are written: README.md here."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QImage
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtTest import QTest

from taskloom import __version__
from tests.test_proof_pdf_new import NEW as SAYS_NEW
from tests.test_proof_pdf_new import Pdf, git, loose, squashed

REPO = Path(__file__).resolve().parents[2]
ASKED = ("On the proof pdf what are the new items for 0.24.0? — then, asked whether the PDF should open with a “New in "
         "this version” list that links to those changes and mark each new change with a “new” label: Yes, that would "
         "be helpful")
OLD, NEW = "tests/proofs/test_proof_lines.py", "tests/proofs/test_proof_history_lock.py"  # two real proofs
_MAJOR, _MINOR, _ = __version__.split(".")
BEFORE = f"v{_MAJOR}.{int(_MINOR) - 1}.0"  # the release before this version, in the test repository
RELEASE = f"v{__version__}"
LABEL = r"\bN\s?E\s?W\b"  # in capitals: the NEW label, and headings saying what is new (titles say "new" in small)


def own_git(tmp_path: Path, monkeypatch):
    """Git of this proof's own (as in tests/test_proof_pdf_new.py): an empty global config, no system config, none of
    the developer's GIT_* variables, and no repository found above the proof's folder."""
    config = tmp_path / "gitconfig"
    config.write_bytes(b"")
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve()))


def commit(repo: Path, message: str, tag: str = ""):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", message)
    if tag:
        git(repo, "tag", tag)


def make_pdf(records: Path, out: Path, repo: Path) -> str:
    """The proof PDF of `records`, made by the real script as CI makes it, with `repo` as the history it reads; the
    command, as it was run."""
    command = ["packaging/proof_pdf.py", str(records), str(out), "--repo", str(repo)]
    done = subprocess.run([sys.executable, *command], cwd=REPO, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=300)
    assert done.returncode == 0, f"the proof PDF was not made:\n{done.stdout}\n{done.stderr}"
    return "python " + " ".join(command).replace(str(out.parent), "…")


def listed(pdf: Pdf, heading: str):
    """What the cover lists under `heading`, before "In this proof"; None when no such heading is before it."""
    cover = pdf.cover_text()
    found, proof_at = re.search(loose(heading), cover), re.search(loose("in this proof"), cover)
    if not found or not proof_at or found.start() > proof_at.start():
        return None
    return cover[found.end():proof_at.start()]


def in_this_proof(reader) -> str:
    """The cover's text from "In this proof" on, as the PDF has it (NEW in capitals)."""
    cover = "\n".join(reader.texts[:reader.cover_pages])
    found = re.search(loose("in this proof"), cover, re.I)
    return cover[found.end():] if found else ""


def over_headings(text: str, number: int) -> list:
    """The small headings over a page of change `number`: "Change 2 of 2 · …", "Change 2 · … · Step 1 of 5"…"""
    return [" ".join(line.split()) for line in text.splitlines()
            if re.search(rf"(?i)\b{loose('change')}\s+{number}\b(?!:)", line)]


def all_marked(pdf: Pdf, number: int, title: str, mark: str) -> bool:
    """Whether each page of the change has a small heading, and each of them says `mark`."""
    headings = [over_headings(pdf.texts[page], number) for page in pdf.pages_of(number, title)]
    return bool(headings) and all(over and all(re.search(loose(mark), line) for line in over) for over in headings)


def none_marked(pdf: Pdf, number: int, title: str) -> bool:
    """Whether the change has pages, and none of their small headings says new."""
    pages = pdf.pages_of(number, title)
    return bool(pages) and not any(re.search(SAYS_NEW, line) for page in pages
                                   for line in over_headings(pdf.texts[page], number))


def says_nothing_new(reader, version: str) -> bool:
    """No page has a NEW label or a heading saying what is new: "New since …", "New in …", "Nothing new since …"."""
    return not any(re.search(LABEL, text) or re.search(loose("new since"), text, re.I)
                   or re.search(loose(f"new in {version}"), text, re.I) for text in reader.texts)


def within(qtbot, condition, timeout: int = 10_000) -> bool:
    try:
        qtbot.waitUntil(condition, timeout=timeout)
        return True
    except qtbot.TimeoutError:
        return False


class Reader:
    """A PDF in Qt's PDF viewer, in a window of its own, a page at a time, as big as the window allows."""

    def __init__(self, path: Path, pdf: Pdf, qtbot):
        self.qtbot, self.cover_pages = qtbot, len(pdf.cover)
        self.doc = QPdfDocument()
        assert self.doc.load(str(path)) == QPdfDocument.Error.None_, f"cannot read {path}"
        self.texts = [self.doc.getAllText(page).text() for page in range(self.doc.pageCount())]
        self.view = QPdfView()
        self.view.setWindowTitle(path.name)
        self.view.setDocument(self.doc)
        self.view.setPageMode(QPdfView.PageMode.SinglePage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitInView)
        self.view.resize(1400, 1000)
        self.view.show()
        self.wait_drawn(0)

    @property
    def page(self) -> int:
        return self.view.pageNavigator().currentPage()

    def go(self, page: int):
        self.view.pageNavigator().jump(page, QPointF())
        self.wait_drawn(page)

    def wait_drawn(self, page: int):
        """Wait until the viewer shows `page`, drawn (it draws pages as it gets to them: a page is white until then)."""
        def drawn() -> bool:
            if self.page != page:
                return False
            image = self.view.viewport().grab().toImage().convertToFormat(QImage.Format_Grayscale8)
            bits, line = image.constBits().tobytes(), image.bytesPerLine()
            return min(min(bits[y * line:y * line + image.width()]) for y in range(image.height())) < 90  # text
        self.qtbot.waitUntil(drawn, timeout=20_000)

    def on_screen(self, area: QRectF) -> QRect:
        """Where a part of the page shown (in the PDF's points) is in the window: the viewer fits the page to the
        window, at its top (below its margin), in its middle."""
        viewport, margins = self.view.viewport(), self.view.documentMargins()
        size = self.doc.pagePointSize(self.page)
        scale = min((viewport.width() - margins.left() - margins.right()) / size.width(),
                    (viewport.height() - self.view.pageSpacing()) / size.height())
        left = (viewport.width() - size.width() * scale) / 2
        return QRectF(left + area.x() * scale, margins.top() + area.y() * scale, area.width() * scale,
                      area.height() * scale).toAlignedRect().translated(viewport.mapTo(self.view, QPoint(0, 0)))

    def find(self, pattern: str) -> QRect:
        """Where the page shown says the first text that `pattern` matches (its first group, if it has one)."""
        text = self.texts[self.page]
        found = re.search(pattern, text)
        assert found, f"page {self.page + 1} does not say {pattern!r}: {text}"
        group = 1 if found.re.groups else 0
        return self.on_screen(self.doc.getSelectionAtIndex(self.page, found.start(group),
                                                           found.end(group) - found.start(group)).boundingRectangle())

    def over_heading(self, number: int) -> QRect:
        """Where the page shown has its small heading."""
        return self.find(rf"(?im)^[^\r\n]*?{loose('change')}\s+{number}\b(?!:)[^\r\n]*")

    def click(self, at: QRect):
        viewport = self.view.viewport()
        point = viewport.mapFrom(self.view, at.center())
        QTest.mouseMove(viewport, point)
        QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, point)

    def close(self):
        self.view.close()
        self.doc.close()


def listed_areas(reader, heading: str, title: str) -> tuple:
    """Where the cover shows ① the list under `heading` (its heading, and the line of the change `title`) and ② that
    change's title with its NEW label, in "In this proof"."""
    heading_at = reader.find(rf"(?i){loose(heading)}")
    number_at = reader.find(rf"(?si){loose(heading)}\W*(\d+)").adjusted(-12, 0, 0, 0)  # the outline clear of it
    link_at = reader.find(rf"(?i){loose(title)}")  # its first place on the cover: in the list
    return [heading_at, number_at, link_at], reader.find(rf"((?i:{loose(title)})\W*{LABEL})")


def unlisted_area(reader, title: str) -> list:
    """Where the cover shows its result, then "In this proof" down to the change `title`."""
    return [reader.find(r"(?i)all\s*\d+\s*changes\s*passed\s*their\s*proofs\."),
            reader.find(rf"(?i){loose('in this proof')}").adjusted(-12, 0, 0, 0),  # the outline clear of the numbers
            reader.find(rf"(?i){loose(title)}")]


def clear_of_badge(area: QRect) -> QRect:
    """Room on the right of a line of text, for the badge drawn on its outline's top right corner."""
    return area.adjusted(0, 0, 20, 0)


def test_the_proof_pdf_opens_with_what_is_new(proof, qtbot, tmp_path, monkeypatch):
    p = proof("The proof PDF opens with what is new in this version", ASKED)
    p.idea("The proof PDF now opens with what is new in this version: a list of the new changes, each a link to its "
           "pages, and a NEW label on each of them.")
    p.idea(f"On the cover, before “In this proof”, a list headed “New in {__version__}” (in the PDF of the release "
           f"itself) or “New since {BEFORE}” (in the PDF of a commit after that release) has a line for each new "
           "change: its number, its title as a link to its first page, its result, the system it was shown on and "
           "its page. In “In this proof”, each new change has a NEW label after its title, and the small heading "
           f"over each of its pages says so too (such as “Change 2 of 2 · New since {BEFORE}”).")
    p.idea("A change is new when its proof was not there at the previous release. That is read from the project's "
           f"Git history: the previous release is the nearest release tag (such as {BEFORE}) before the commit the "
           "PDF is made from.")
    p.idea("So a change that only improved a proof that was already there (a fix shown by adding to an existing "
           "proof) is not listed as new. And when the PDF is made without Git, without the project's history or "
           "without a release tag, it says nothing is new: no list and no labels, as before.")
    p.why_simplest("It asks Git one thing: which proofs did the project have at the previous release? Every proof "
                   "has a name of its own (its file and the test in it), which is also the name of the folder its "
                   "pages are made from. A proof in the PDF that was not there then is a new change. Nothing has to "
                   "be written down by hand, and nothing can fall out of step.")
    p.why_simplest("When Git cannot answer (no Git, no history, no release tag), the PDF is made as before. A wrong "
                   "“new” would be worse than none.")
    p.why_simplest("The links are ordinary links inside the PDF, each to the top of its change's first page, where "
                   "its title is.")
    p.alternative("A list of what is new, written by hand for each release",
                  "One more thing to write at each release and keep in step with the proofs, and easy to forget or "
                  "get wrong. Git already knows which proofs were added since the release.")
    p.alternative("Calling a change new when its proof file changed since the release",
                  "Every proof touched up after the release (a clearer caption, a longer wait) would be listed as "
                  "new, so the list would no longer say what is new. The price of the rule chosen: a fix shown by "
                  "improving a proof that was already there is not listed, as change 1 shows here.")
    p.alternative("Comparing with the previous release's proof PDF",
                  "That PDF would have to be downloaded from the release when the PDF is made, and its pages read "
                  "back for titles, which can change. The proofs' names are already in the history CI checks out.")
    p.alternative("Each proof saying which version it is new in",
                  "A version number in every proof, set by hand; a change that slips to the next release would then "
                  "say the wrong one.")
    p.fits("The list uses the cover's own lines: the same number, result, system and page as in “In this proof”, so "
           "a new change reads the same in both places. NEW is in the orange of the highlights and badges on the "
           "screenshots; the links are in the blue of the CI run's link on the cover.")
    p.fits("CI makes one PDF of the proofs shown on Windows and those shown on Linux: what is new is worked out once "
           "for the whole PDF, so a change shown on Linux is listed in the same way.")
    p.fits(f"Releases are tagged v and their version ({BEFORE}, {RELEASE}). The PDF made for a release's tag says "
           f"“New in {__version__}”; the PDF of any commit after it says “New since {RELEASE}”. CI's proof job now "
           "checks out the whole history and its tags, which it did not before, so that it can tell.")
    own_git(tmp_path, monkeypatch)
    records, plain, repo = tmp_path / "records", tmp_path / "no-git", tmp_path / "test-repository"
    readers = []

    def open_pdf(name: str, history: Path) -> tuple:
        """Make the proof PDF `name` of the recorded proofs, reading what is new from `history`, and open it."""
        command = make_pdf(records, tmp_path / name, history)
        pdf = Pdf(tmp_path / name)
        readers.append(Reader(tmp_path / name, pdf, qtbot))
        return pdf, readers[-1], command

    try:
        p.step("Run the proofs of two changes, as CI does, and make the proof PDF of what they recorded, from a "
               "folder with no Git history. Open it.")
        ran = subprocess.run([sys.executable, "-m", "pytest", OLD, NEW, "-q", "-p", "no:cacheprovider"], cwd=REPO,
                             env={**os.environ, "PROOF_DIR": str(records)}, capture_output=True, encoding="utf-8",
                             errors="replace", timeout=600)
        output = ran.stdout.strip().splitlines()
        p.text("What the run of the two proofs printed, at its end", output[-1] if output else ran.stderr[-2000:])
        shown = sorted(((file.parent.name, json.loads(file.read_text(encoding="utf-8")))
                        for file in records.glob("*/proof.json")),
                       key=lambda shown: shown[1]["started"])  # in the order they ran, as in the PDF
        p.check("Both proofs passed and recorded their pages, “Each start block runs its own line” first",
                ran.returncode == 0 and [(name.split(".")[0], record["status"]) for name, record in shown]
                == [(Path(OLD).stem, "passed"), (Path(NEW).stem, "passed")])
        (_, old), (_, new) = shown
        for file in (OLD, NEW):
            (plain / file).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / file, plain / file)
        pdf, reader, command = open_pdf("proof-no-git.pdf", plain)
        p.text("The command that made the PDF (…: this proof's folder; --repo: the folder whose Git history it reads)",
               command)
        p.note(f"Two of Taskloom's real proofs, “{old['title']}” and “{new['title']}”: they drove the editor and "
               "took their screenshots just now. The PDF is shown in Qt's PDF viewer (Qt is what Taskloom is built "
               "with), in a window of this proof: Taskloom has no PDF viewer of its own.")
        area = unlisted_area(reader, new["title"])
        p.shot(reader.view, "With no Git history, the PDF cannot tell what is new, so it says nothing is: ① the cover "
                            "goes from the result straight to “In this proof”, with no list and no NEW label. This is "
                            "the cover as it was before this change.",
               highlight=[area], badges=True, zoom=area)
        p.check("No list, no NEW label, and no page that says new", says_nothing_new(reader, __version__))
        p.check("No link to the PDF's pages", pdf.links == [])

        p.step(f"Make a test Git repository with the release {BEFORE} and a later commit that adds a proof, and make "
               "the PDF again from it.")
        (repo / "tests" / "proofs").mkdir(parents=True)
        git(repo, "init", "-q", "-b", "main")
        shutil.copy(REPO / OLD, repo / OLD)
        commit(repo, old["title"], tag=BEFORE)
        shutil.copy(REPO / NEW, repo / NEW)
        with (repo / OLD).open("a", encoding="utf-8") as file:
            file.write("\n# Added after the release: a fix shown by improving this proof.\n")
        commit(repo, f"{new['title']}; the proof of '{old['title']}' improved")
        log = git(repo, "log", "--format=%h%d  %s", "--name-status")
        p.text("Its history, newest first (git log; A: a file added, M: a file changed)",
               "\n".join(line.replace("\t", "  ") for line in log.splitlines() if line.strip()))
        p.note(f"Made for this proof, so that what is new is known: at {BEFORE}, the proof of change 1; the later "
               "commit adds the proof of change 2, and adds a line to the proof of change 1, as when a later fix is "
               "shown by improving a proof that was already there.")
        pdf, reader, _ = open_pdf("proof.pdf", repo)
        first_new, first_old = pdf.first_page(2, new["title"]), pdf.first_page(1, old["title"])
        news = listed(pdf, f"new since {BEFORE}")  # checked first: what is not there cannot be outlined
        p.check(f"The cover lists “New since {BEFORE}”, before “In this proof”", news is not None)
        p.check(f"It lists change 2, “{new['title']}”: passed, on {new['system']}, page {first_new + 1}",
                re.search(rf"\b2\b.*?{re.escape(new['title'].lower())}.*?passed.*?on {new['system'].lower()}"
                          rf".*?page {first_new + 1}\b", news or ""))
        p.check(f"It does not list change 1, “{old['title']}”, whose proof the later commit only improved",
                news is not None and old["title"].lower() not in news)
        rest = in_this_proof(reader)
        p.check("In “In this proof”, NEW follows change 2's title, and no other",
                re.search(rf"(?i:{loose(new['title'])})\W*{LABEL}", rest) and len(re.findall(LABEL, rest)) == 1)
        the_list, label = listed_areas(reader, f"new since {BEFORE}", new["title"])
        p.shot(reader.view, f"① The cover now opens with “New since {BEFORE}”: change 2, “{new['title']}”, its title a "
                            f"link to its first page, {first_new + 1}. Change 1 is not in it: its proof was there at "
                            f"{BEFORE}. ② In “In this proof”, change 2 has a NEW label after its title; change 1 has "
                            "none.",
               highlight=[the_list, label], badges=True, zoom=[*the_list, label])

        p.step("In the viewer, click change 2's title in the list.")
        reader.click(the_list[-1])
        followed = within(qtbot, lambda: reader.page == first_new)
        p.check(f"The list has one link, on change 2's title, to page {first_new + 1}: the change's first page",
                len(pdf.links) == 1 and squashed(new["title"]) in pdf.links[0][0] and pdf.links[0][1] == first_new)
        p.check(f"Clicked, the viewer goes to page {first_new + 1}", followed)
        reader.wait_drawn(first_new)
        over = clear_of_badge(reader.over_heading(2))
        p.shot(reader.view, f"Page {first_new + 1}, where the link goes: change 2's first page. ① Its small heading "
                            f"says “Change 2 of 2 · New since {BEFORE}”.",
               highlight=[over], badges=True, zoom=[over, reader.find(rf"(?i){loose(new['title'])}"),
                                                    reader.find(r"(?i)passed\s*on")])
        p.check(f"Its small heading says “Change 2 of 2 · New since {BEFORE}”",
                any(re.search(loose("change 2 of 2"), line) and re.search(loose(f"new since {BEFORE}"), line)
                    for line in over_headings(pdf.texts[first_new], 2)))

        step_page = next(page for page in pdf.pages_of(2, new["title"]) if re.search(loose("step 1 of"), pdf.flat[page]))
        p.step(f"In the viewer, go on to page {step_page + 1}, the first step of change 2.")
        reader.go(step_page)
        over = clear_of_badge(reader.over_heading(2))
        steps = sum(entry["kind"] == "step" for entry in new["entries"])
        step_one = " ".join(next(e["text"] for e in new["entries"] if e["kind"] == "step").split()[:3])
        p.shot(reader.view, f"① Each page of change 2 says so in its small heading: here “Change 2 · New since "
                            f"{BEFORE} · See it working · Step 1 of {steps}”.",
               highlight=[over], badges=True, zoom=[over, reader.find(rf"(?i){loose(step_one)}")])
        p.check(f"Each of change 2's {len(pdf.pages_of(2, new['title']))} pages says “New since {BEFORE}” in its "
                "small heading", all_marked(pdf, 2, new["title"], f"new since {BEFORE}"))

        p.step(f"In the viewer, go to page {first_old + 1}, the first page of change 1, which is not new.")
        reader.go(first_old)
        over = clear_of_badge(reader.over_heading(1))
        p.shot(reader.view, "① Change 1's small heading says only “Change 1 of 2”: it is not new.",
               highlight=[over], badges=True, zoom=[over, reader.find(rf"(?i){loose(old['title'])}"),
                                                    reader.find(r"(?i)passed\s*on")])
        p.check(f"None of change 1's {len(pdf.pages_of(1, old['title']))} pages says new in its small heading",
                none_marked(pdf, 1, old["title"]))
        p.note("What is new is worked out from Git: a proof that was not there at the previous release. Change 1 "
               "stands here for a fix shown by improving a proof that was already there: such a change is not "
               "listed as new.")

        p.step(f"Tag the later commit as the release {RELEASE}, and make the PDF again: the PDF of the release, as "
               "CI makes it for the release's tag.")
        git(repo, "tag", RELEASE)
        pdf, reader, _ = open_pdf("proof-release.pdf", repo)
        news = listed(pdf, f"new in {__version__}")
        p.check(f"The cover lists “New in {__version__}”, before “In this proof”, and no longer says “New since”",
                news is not None and not re.search(loose("new since"), pdf.cover_text()))
        p.check(f"It lists change 2 and not change 1; change 2's title links to page {first_new + 1}",
                re.search(re.escape(new["title"].lower()), news or "") and old["title"].lower() not in (news or "")
                and len(pdf.links) == 1 and pdf.links[0][1] == pdf.first_page(2, new["title"]) == first_new)
        p.check(f"Each of change 2's pages says “New in {__version__}” in its small heading; none of change 1's says "
                "new", all_marked(pdf, 2, new["title"], f"new in {__version__}") and none_marked(pdf, 1, old["title"]))
        the_list, label = listed_areas(reader, f"new in {__version__}", new["title"])
        p.shot(reader.view, f"① The list is now headed “New in {__version__}”: the answer to “what are the new items "
                            f"for {__version__}?”. ② Change 2 is still marked NEW in “In this proof”.",
               highlight=[the_list, label], badges=True, zoom=[*the_list, label])

        p.step("Delete the release tags from the test repository, and make the PDF again.")
        git(repo, "tag", "-d", BEFORE, RELEASE)
        pdf, reader, _ = open_pdf("proof-no-tag.pdf", repo)
        area = unlisted_area(reader, new["title"])
        p.shot(reader.view, "With no release tag, Git cannot say what is new, so the PDF says nothing is: ① no list "
                            "before “In this proof”, and no NEW label.",
               highlight=[area], badges=True, zoom=area)
        p.check("No list, no NEW label, and no page that says new", says_nothing_new(reader, __version__))
        p.check("No link to the PDF's pages", pdf.links == [])
    finally:
        for reader in readers:
            reader.close()
