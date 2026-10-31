"""Proof: every change on the proof PDF's cover is a link to its pages, and every page of a change has a small link
back to its row on the cover. How proofs are written: README.md here."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QLabel

from tests.test_proof_pdf_links import GIT, TOP, Change, Pdf, build, cover_links, describe, go_back_problems, squashed

REPO = Path(__file__).resolve().parents[2]
ASKED = ("On the proof pdf make each proof item to be a link towards its page and in every page add a small "
         "\"go back\" that returns to the source link of the current proof at the top")
REAL = "tests/proofs/test_proof_history_lock.py"  # a real proof: its change is the last one in the PDF
COUNT, NEW = 16, {3, 8, 13}  # the changes recorded for this proof, and the new ones among them
BEFORE = "d8b8b6e^"  # the commit before this change: the proof PDF script as it was
WIDTH = 1600  # a page's picture, in pixels


class Shown:
    """The change whose proof ran just now, as the PDF has it: its record, copied as the proof run wrote it. Its
    scenario is not in the test repository's release, so it is new."""
    new = True

    def __init__(self, number: int, folder: Path):
        self.number, self.folder, self.scenario = number, folder, folder.name
        self.record = json.loads((folder / "proof.json").read_text(encoding="utf-8"))
        self.title = self.record["title"]

    def write(self, proofs: Path):
        shutil.copytree(self.folder, proofs / self.scenario)


def picture(doc: QPdfDocument, page: int) -> tuple[QLabel, float]:
    """Page `page` (from 0) of the PDF, rendered as a viewer draws it, in a window of its own; and how many of its
    pixels make a point of the PDF."""
    size = doc.pagePointSize(page)
    scale = WIDTH / size.width()
    image = doc.render(page, QSize(WIDTH, round(size.height() * scale)))
    assert not image.isNull(), f"page {page + 1} could not be drawn"
    paper = QImage(image.size(), QImage.Format_RGB32)  # the page is drawn on white paper, as a viewer shows it
    paper.fill(Qt.white)
    painter = QPainter(paper)
    painter.drawImage(0, 0, image)
    painter.end()
    label = QLabel()
    label.setPixmap(QPixmap.fromImage(paper))
    label.resize(image.size())
    label.setWindowTitle(f"Page {page + 1}")
    label.show()
    return label, scale


def on_picture(rects, scale: float) -> QRect:
    """Where these rectangles of the PDF (in points) are on its page's picture, as one."""
    area = QRectF()
    for rect in rects:
        area |= QRectF(rect.x() * scale, rect.y() * scale, rect.width() * scale, rect.height() * scale)
    return area.toAlignedRect()


def around(area: QRect, left: int, down: int, right: int = 12) -> QRect:
    return area.adjusted(-left, -12, right, down)


def landing(at_y: float, scale: float) -> QRect:
    """A short band in the page's left margin, at the height a link lands at."""
    return QRect(10, round(at_y * scale) - 5, 40, 10)


def title_link(pdf: Pdf, number: int, title: str):
    """The change's title link in 'In this proof': on the cover page of its row, at its row."""
    row = pdf.row(number)
    links = [link for link in pdf.links[row.page] if link.on == squashed(title)]
    return min(links, key=lambda link: abs(min(r.top() for r in link.rects) - row.top))


@pytest.mark.skipif(GIT is None, reason="the New list is found with the git program, not installed")
def test_the_proof_pdf_links_each_change_and_back(proof, qtbot, tmp_path):
    p = proof("Every change on the proof's cover is a link to its pages, and every change page links back to its "
              "row", ASKED)
    p.idea("The proof PDF is now easy to move around in. On its cover, each change's title in “In this proof” is a "
           "link to the change's first page. And every page of a change has a small “↑ Back to the list” link at its "
           "top right, which takes you back to that change's row on the cover.")
    p.idea("The go-back link goes to the cover page that holds the change's row, at the row itself: on a cover of "
           "two pages, a change listed on the second page goes back to the second page, at its row's height, not to "
           "the top of the cover. For a new change it is its row in “In this proof”, where its summary is.")
    p.idea("The links of the “New since …” list stay as they were: each new change's title goes to its first page. "
           "The cover's own pages have no go-back link: they are the list.")
    p.why_simplest("They are ordinary links inside the PDF, which PDF viewers follow. Each title goes to a named "
                   "place at the top of its change's first page (the one the New list's links already went to), and "
                   "each go-back link to a named place in its change's row. No page number is written into them: "
                   "Qt, which writes the PDF, puts each link where its name ended up, so they stay right however "
                   "long the cover and the changes are.")
    p.why_simplest("The go-back link is drawn in the page's top margin, above the page's text, not as a line of it. "
                   "Nothing on the page moves or is drawn smaller, and it covers nothing. When a page starts in the "
                   "middle of a paragraph, Qt also writes into the PDF, out of sight, the paragraph's line before. "
                   "Each page is laid out with blank room below it, so that hidden line lands far above the paper, "
                   "not in the top margin by the go-back link, where selecting text around the link would pick it "
                   "up too.")
    p.alternative("Going back to the top of the cover",
                  "The same link on every page, but on a cover of two or more pages it would land on its first page "
                  "and you would have to look for your change again, perhaps on the next page.")
    p.alternative("Leaving it to the viewer's Back button",
                  "Not every viewer has one, or shows it, and you asked for a link on the page.")
    p.alternative("A line with the link at the top of each page's text",
                  "It would push everything on the page down a line, and a full page would be drawn a little smaller "
                  "to fit. And when a step, or the list of what was checked, runs on to a second page, only the "
                  "first would have it. In the margin, it moves nothing.")
    p.alternative("The link in the footer",
                  "You asked for it at the top, where you start reading a page. The footer already holds the "
                  "version, the change and the page number.")
    p.fits("The titles in “In this proof” are now links in the same blue as the New list's titles and the CI run's "
           "link on the cover, and the go-back link is too: a link looks the same everywhere in the PDF.")
    p.fits("An earlier change keeps each row of the cover's lists whole on one page: the place a go-back link goes is "
           "in its row, so it is on the same page as the row's title.")
    p.fits("Each page of a change already says which change it is in its footer, and in a small heading at the top "
           "left where a part of it starts (its story, a step, what was checked); the go-back link is at the top "
           "right of every one of its pages, in the margin above the text.")
    windows = []

    def show(page: int) -> tuple[QLabel, float]:
        windows.append(picture(doc, page))
        return windows[-1]

    doc = QPdfDocument()
    try:
        p.step("Run a real proof, “The editor carries on while a run writes a new history file”, as CI does, and "
               f"make the proof PDF of it with {COUNT} more changes recorded for this proof, {len(NEW)} of them new "
               "since the last release.")
        records = tmp_path / "records"
        ran = subprocess.run([sys.executable, "-m", "pytest", REAL, "-q", "-p", "no:cacheprovider"], cwd=REPO,
                             env={**os.environ, "PROOF_DIR": str(records)}, capture_output=True, encoding="utf-8",
                             errors="replace", timeout=600)
        output = ran.stdout.strip().splitlines()
        p.text("What the run of the real proof printed, at its end", output[-1] if output else ran.stderr[-2000:])
        folders = [file.parent for file in records.glob("*/proof.json")]
        p.check("The real proof passed and recorded its pages", ran.returncode == 0 and len(folders) == 1)
        real = Shown(COUNT + 1, folders[0])
        changes = [*(Change(number, number in NEW) for number in range(1, COUNT + 1)), real]
        made = tmp_path / "pdf"
        made.mkdir()
        out = build(made, changes)
        pdf = Pdf(out, changes)
        assert doc.load(str(out)) == QPdfDocument.Error.None_, f"cannot read {out}"
        p.text("How the PDF was made", "python packaging/proof_pdf.py <the recorded proofs> proof.pdf --repo <a test "
                                       "repository with the release v0.1.0>")
        p.note(f"The {COUNT} other changes are written for this proof the way the proof runs write theirs (their own "
               "titles, steps, checks, and plain grey screenshots for one of them), so that the cover runs on to a "
               "second page; the test repository's release has all of them but the new ones. The PDF is made by "
               "Taskloom's real script, as CI makes it. Each page below is drawn from the PDF with Qt's PDF "
               "renderer, as a viewer draws it.")
        cover = list(pdf.cover)
        p.check(f"The cover takes {len(cover)} pages, two or more", len(cover) >= 2)
        found, expected = cover_links(pdf, changes)
        p.check(f"The cover has exactly these links to the PDF's pages: each of the {len(NEW) + 1} new changes' "
                f"titles in the New list, and each of the {len(changes)} titles in “In this proof”, each to its "
                "change's first page, and no go-back link", found == expected)
        assert found == expected, describe(found, expected)
        p.check("No cover page says “Back to the list”",
                not any("backtothelist" in squashed(doc.getAllText(page).text()) for page in cover))

        on_first = next(c for c in changes if pdf.row(c.number).page == 0 and not c.new)
        # The last change on cover page 2 (the real one, unless the cover takes more pages): low on its page.
        on_second = max((c for c in changes if pdf.row(c.number).page == 1), key=lambda c: pdf.row(c.number).top)

        p.step("Look at the cover's first page: the links the PDF has on it are outlined.")
        label, scale = show(0)
        links = pdf.links[0]
        rows = [title_link(pdf, c.number, c.title) for c in changes if pdf.row(c.number).page == 0]
        news = [link for link in links if link not in rows]
        rects = [on_picture(link.rects, scale) for link in links]
        p.shot(label, f"Cover page 1, with each of its links outlined where the PDF has it: ① the titles of the New "
                      f"list ({len(news)} on this page), still links; ② each title in “In this proof”, now a link to "
                      "its change's first page.",
               highlight=rects, badges=[1 if link in news else 2 for link in links],
               zoom=[*(on_picture(link.rects, scale) for link in news + rows[:2])])

        p.step(f"Follow the link of change {on_first.number}, “{on_first.title}”, from cover page 1.")
        link = title_link(pdf, on_first.number, on_first.title)
        p.check(f"It goes to page {link.to + 1}: change {on_first.number}'s first page",
                link.to == pdf.first[on_first.number])
        label, scale = show(link.to)
        back = pdf.links[link.to]
        p.check(f"Page {link.to + 1} has one link, “↑ Back to the list”, at its top right, over no other text",
                len(back) == 1 and back[0].on == squashed("↑Back to the list") and not back[0].covers
                and max(r.bottom() for r in back[0].rects) < TOP * pdf.height[link.to]
                and min(r.left() for r in back[0].rects) > 0.7 * doc.pagePointSize(link.to).width())
        at = on_picture(back[0].rects, scale)
        p.shot(label, f"Page {link.to + 1}, where the link goes: the first page of change {on_first.number}. ① At its "
                      "top right, small, the link back to the list, above the page's text and covering none of it.",
               highlight=[at], badges=True, zoom=around(at, 450, 170))

        p.step("Follow its “↑ Back to the list”.")
        row = pdf.row(on_first.number)
        went = back[0]
        p.check(f"It goes to cover page {went.to + 1}, which holds change {on_first.number}'s row",
                went.to == row.page == 0)
        p.check(f"It lands at the row's height (at {went.at.y():.0f} points down the page, the row's title at "
                f"{row.top:.0f}), not at the page's top",
                row.top - 24 <= went.at.y() <= row.bottom + 2 and went.at.y() > 0.3 * pdf.height[0])
        label, scale = show(went.to)
        marks = [landing(went.at.y(), scale), on_picture(title_link(pdf, on_first.number, on_first.title).rects, scale)]
        p.shot(label, f"Cover page {went.to + 1}: ① in the margin, the height where the go-back link lands: ② change "
                      f"{on_first.number}'s row, its title.",
               highlight=marks, badges=True, zoom=marks)

        number, title = on_second.number, on_second.title
        p.step(f"Go to cover page 2, and follow the link of change {number}, “{title}”.")
        label, scale = show(1)
        link = title_link(pdf, number, title)
        here = [on_picture(each.rects, scale) for each in pdf.links[1]]
        p.shot(label, f"Cover page 2: ① each title on it is a link; ② change {number}'s, followed now.",
               highlight=here + [on_picture(link.rects, scale)],
               badges=[1] * len(here) + [2], zoom=around(on_picture(link.rects, scale), 30, 60, 90))
        p.check(f"It goes to page {link.to + 1}: change {number}'s first page", link.to == pdf.first[number])
        label, scale = show(link.to)
        back = pdf.links[link.to]
        at = on_picture(back[0].rects, scale)
        p.shot(label, f"Page {link.to + 1}: the first page of change {number}, with ① its own “↑ Back to the list”.",
               highlight=[at], badges=True, zoom=around(at, 450, 170))

        p.step("Follow its “↑ Back to the list”.")
        row, went = pdf.row(number), back[0]
        p.check(f"It goes to cover page 2, which holds change {number}'s row, not to cover page 1",
                went.to == row.page == 1)
        p.check(f"It lands at the row's height (at {went.at.y():.0f} points down the page, the row's title at "
                f"{row.top:.0f}), not at the page's top",
                row.top - 24 <= went.at.y() <= row.bottom + 2 and went.at.y() > 0.15 * pdf.height[1])
        label, scale = show(went.to)
        marks = [landing(went.at.y(), scale), on_picture(link.rects, scale)]
        p.shot(label, f"Cover page 2: ① in the margin, the height where the go-back link lands: ② change {number}'s "
                      "row, its title, low on the page.",
               highlight=marks, badges=True, zoom=marks)

        pages = pdf.pages[number]
        step = next(page for page in pages if "seeitworking" in squashed(doc.getAllText(page).text()))
        last = pages[-1]
        p.step(f"Go on to a later page of change {number}: page {step + 1}, its first step, and page {last + 1}, "
               "“What was checked”.")
        for page, what in ((step, "its first step"), (last, "“What was checked”")):
            label, scale = show(page)
            here = pdf.links[page]
            at = on_picture(here[0].rects, scale)
            p.shot(label, f"Page {page + 1}, {what}: ① the same “↑ Back to the list” at its top right, over nothing.",
                   highlight=[at], badges=True, zoom=around(at, 450, 170))
        p.check("“What was checked” is its last page",
                "whatwaschecked" in squashed(doc.getAllText(last).text()))
        p.check(f"Both have the same go-back link, to change {number}'s row on cover page 2",
                all(len(pdf.links[page]) == 1 and pdf.links[page][0].to == went.to
                    and pdf.links[page][0].at == went.at for page in (step, last)))
        problems = go_back_problems(pdf, changes)
        p.check(f"Each of the {sum(len(pages) for pages in pdf.pages.values())} pages of the {len(changes)} changes "
                "has one go-back link, at its top and over no other text, to its change's row on the cover",
                problems == [])
        assert problems == [], "\n".join(problems)

        p.step("Make the PDF again from the same proofs with the script as it was before this change, and compare.")
        before = tmp_path / "proof_pdf_before.py"
        shown = subprocess.run([GIT, "-C", str(REPO), "show", f"{BEFORE}:packaging/proof_pdf.py"],
                               capture_output=True, timeout=60)
        assert shown.returncode == 0, f"cannot read the script before this change: {shown.stderr!r}"
        before.write_bytes(shown.stdout)
        old = made / "proof-before.pdf"
        done = subprocess.run([sys.executable, str(before), str(made / "proofs"), str(old), "--repo",
                               str(made / "repo")], capture_output=True, encoding="utf-8", errors="replace",
                              timeout=300, cwd=made)
        assert done.returncode == 0, f"the PDF before this change was not made:\n{done.stdout}\n{done.stderr}"
        older = QPdfDocument()
        assert older.load(str(old)) == QPdfDocument.Error.None_, f"cannot read {old}"
        counts = (older.pageCount(), doc.pageCount())
        older.close()
        p.text("The pages of the two PDFs",
               f"Before this change: {counts[0]} pages\nNow:                {counts[1]} pages")
        p.note("“Before this change” is the script of the commit before it, read from the project's Git history.")
        p.check(f"The PDF has as many pages as before this change: {counts[1]}", counts[0] == counts[1])
        p.note("The links are followed as a PDF viewer reads them: from the list of links Qt's PDF reader finds in "
               "the PDF, each with the page it goes to and where on it. The proof does not click them in a "
               "particular viewer, such as Acrobat or a web browser.")
    finally:
        for label, _ in windows:
            label.close()
        doc.close()
