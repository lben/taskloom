"""Proof: a short README, details in the guide (docs/promises/readme.yaml, RD-1..RD-3; its example flow, BR-8).
README.md is shown as Help > Documentation shows Markdown, and its link opens docs/guide.md; in the editor, Help >
Documentation shows the guide's section on Check Table, beside the one line the README now gives it; the README's
example flow passes Validate."""

import re
from pathlib import Path

import yaml
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QUrl
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QTextBrowser, QToolBar, QVBoxLayout, QWidget

from proofs.conftest import choose
from proofs.test_proof_docs_help import open_flow
from proofs.test_proof_docs_site import side_by_side
from taskloom.editor import docs
from tests.test_docs_help import click_link, heading_at_top, wait_for
from tests.test_readme_guide import (CAN_DO, GITHUB, GUIDE, OLD_HEADINGS, README, REPO, SITE_URL, connections_named,
                                     headings, link_problems, links, parts, plain_line, readme_flows)

ASKED = ("Too verbose, should sell it, give an overview of what it can do, for details there is the docs — then, of "
         "what must always be true of the README and the guide, with at most about 150 lines for the README: "
         "“Approve all”; and of the new README's text, shown as a draft: “Approve the draft”")
PAGE = (960, 1000)  # the window README.md is shown in: about as wide as GitHub shows a README
CHECK_TABLE = "### Check Table: is the table as expected?"  # a section the README had, now the guide's


class MarkdownPage(QWidget):
    """A Markdown file of the repository as Help > Documentation shows Markdown (the same reader, Qt's text viewer,
    the column's style), under a line with its name and count of lines. A link to another Markdown file of the
    repository opens that file here; each link clicked is kept in `clicked`."""

    def __init__(self, path: Path):
        super().__init__()
        self.title, self.view = QLabel(), QTextBrowser(openLinks=False)
        self.view.document().setDefaultStyleSheet(  # the column's
            "code, pre { font-family: monospace; } pre { white-space: pre-wrap; }")
        self.view.anchorClicked.connect(self.follow)
        self.clicked = []
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.view)
        self.resize(*PAGE)
        self.show_file(path)

    def show_file(self, path: Path):
        text = path.read_text(encoding="utf-8")
        intro, sections = docs._guide(text)  # the column's pages are made from Markdown so
        self.path = path
        self.title.setText(f"<b>{path.relative_to(REPO).as_posix()}</b> · {len(text.splitlines()):,} lines")
        self.view.setHtml(intro + "".join(body for _, body in sections))

    def follow(self, url: QUrl):
        self.clicked.append(url.toString())
        target = (self.path.parent / url.path()).resolve()
        if not url.scheme() and target.suffix == ".md" and target.is_file() and target.is_relative_to(REPO):
            self.show_file(target)


def span(view, first: str, last: str) -> QRect:
    """Where the view shows its text from `first` to the end of the first `last` after it, in its viewport's pixels;
    all of it is in view."""
    doc = view.document()
    start = doc.find(first, 0, QTextDocument.FindCaseSensitively)
    assert not start.isNull(), f"the page does not show {first!r}"
    end = doc.find(last, start.selectionStart(), QTextDocument.FindCaseSensitively)
    assert not end.isNull(), f"the page shows no {last!r} after {first!r}"
    cursor, rect = QTextCursor(doc), QRect()
    for at in range(start.selectionStart(), end.selectionEnd() + 1):
        cursor.setPosition(at)
        rect |= view.cursorRect(cursor)
    assert 0 <= rect.top() and rect.bottom() < view.viewport().height(), f"{first!r} … {last!r} is not all in view"
    return rect.adjusted(-4, 2, 4, -2) & view.viewport().rect()  # (its outline goes 6 pixels around it)


def on(base: QWidget, view, rect: QRect) -> QRect:
    """`rect`, of the view's viewport, in the pixels of `base` (a screenshot of it)."""
    return rect.translated(view.viewport().mapTo(base, QPoint(0, 0)))


def scroll_to(view, text: str):
    """Scroll the view so the line that shows `text` is at its top, as you would."""
    found = view.document().find(text, 0, QTextDocument.FindCaseSensitively)
    assert not found.isNull(), f"the page does not show {text!r}"
    bar = view.verticalScrollBar()
    bar.setValue(bar.value() + view.cursorRect(found).top() - 8)
    QApplication.processEvents()


def in_order(wanted: list, have: list) -> bool:
    """Each of `wanted` is in `have`, in the same order (others may come between)."""
    rest = iter(have)
    return all(any(w == h for h in rest) for w in wanted)


def section_text(sections, pattern: str) -> str:
    """The README's sections whose heading matches `pattern`, as one text."""
    return "\n".join("\n".join(lines) for heading, lines in sections if re.search(pattern, heading, re.I))


def leads_to(href: str) -> str:
    """Where a link of the README leads, in words."""
    if href.startswith(SITE_URL):
        return "the documentation website, made from the guide and the blocks"
    if href.startswith(GITHUB + "/releases"):
        return "the project's releases on GitHub"
    if href.startswith(GITHUB):
        return "the project on GitHub"
    return ("a folder" if (REPO / href.split("#")[0]).is_dir() else "a file") + " of the repository"


def words(text: str) -> str:
    return " ".join(text.split())


def test_a_short_readme_with_its_details_in_the_guide(proof, win, qtbot, tmp_path, monkeypatch):
    readme, guide = README.read_text(encoding="utf-8"), GUIDE.read_text(encoding="utf-8")
    lines = readme.splitlines()
    intro, sections = parts(readme)
    p = proof("A short README, details in the guide", ASKED)
    p.idea(f"README.md is now a short page that sells Taskloom: what it is, who it is for, what you can build and what "
           f"it can do, how to get it and start it, and where the documentation is, in {len(lines)} lines.")
    p.idea("Its details moved, whole, into the guide, docs/guide.md: the old README as it was, from Install to "
           "License, with nothing cut. The README's Documentation section links to it.")
    p.idea("Help > Documentation in the editor and the documentation website are made from the guide: each of its "
           "sections is a page. So the details that left the README are a click away in the app, and on the website.")
    p.idea("Every link in the README works, and its example flow is a real one: it passes Validate.")
    p.why_simplest("The details were moved, not rewritten: the guide is the old README, line for line, so nothing "
                   "could be lost on the way. Only the README was written anew, and you approved its draft.")
    p.why_simplest("Help > Documentation and the website already made their guide pages from one Markdown file, a "
                   "page for each section. They now read docs/guide.md instead of README.md; nothing else about "
                   "them changed.")
    p.alternative("Keep the long README, with a short summary at its top",
                  "GitHub would still show all of it, over 1,600 lines, on the project's page: still too long to "
                  "sell Taskloom, which is what you asked to fix.")
    p.alternative("Shorten the README and drop the rest",
                  "The details are what you look up while you work: what each check of Check Table does, how a "
                  "schedule decides. Dropped, they would be gone from Help > Documentation and the website too, "
                  "which are made from them.")
    p.alternative("Split the details into many files in docs/, one per topic",
                  "The app and the website would need a list of the files, in order, kept up to date by hand, and "
                  "the links between the files would break when one moved. One file, cut at its sections, already "
                  "gives a page per topic.")
    p.fits("Help > Documentation (F1) and the website show the guide as before: a page for each section, listed in "
           "Contents in the guide's order. The installed app and the Windows build carry the guide inside them, so "
           "Help > Documentation works offline.")
    p.fits("The tests that checked the README's words now check the guide's, so the details stay as true as they "
           "were.")
    p.fits("The README's example follows the brace rule, as every example Taskloom ships does: {{day:%Y-%m-%d}} "
           "fills in the flow parameter day, and Validate finds nothing to warn about.")

    # --- the README, as GitHub shows it --------------------------------------------------------------------------
    p.step("Open README.md, as GitHub shows it on the project's page.")
    p.note("GitHub needs the internet, which a proof does not use. So the proof shows README.md as Help > "
           "Documentation shows Markdown: read with the same Markdown reader, in Qt's text viewer, in a window of its "
           "own about as wide as GitHub's. The line above it, with the file's name and its count of lines, is the "
           "proof's.")
    page = MarkdownPage(README)
    qtbot.addWidget(page)
    page.show()
    QTest.qWait(50)
    view = page.view
    what_it_is = on(page, view, span(view, "Taskloom lets you build", "admin or root rights."))
    p.shot(page, f"① README.md has {len(lines)} lines. Its top says ② what Taskloom is, ③ who it is for and ④ what "
                 "you can build with it.",
           highlight=[page.title, what_it_is, on(page, view, span(view, "Who it is for", "when you want them.")),
                      on(page, view, span(view, "What you can build", "and why."))], badges=True,
           zoom=[page.title, what_it_is])
    p.check(f"① README.md has {len(lines)} lines: at most 150 (RD-1)", len(lines) <= 150)
    said = "\n".join(intro)
    p.check("② Before its first section, it says what Taskloom is: automations you build from blocks (RD-1)",
            re.search(r"\bblocks?\b", said, re.I) and re.search(r"automat|\bflows?\b", said, re.I))
    p.check("③ A section says who it is for (RD-1)", section_text(sections, r"\bwho\b").strip())

    p.step("Scroll down to the example flow and What it can do.")
    scroll_to(view, "A flow is a short YAML file")
    table, row = span(view, "Group", "Mermaid diagrams"), span(view, "Checks", "ranges, ...)")
    p.shot(page, "① The example flow, written as a flow file is. ② What it can do: the blocks, a group to a row; "
                 "③ Checks has one line, on Check File and Check Table.",
           highlight=[on(page, view, span(view, "taskloom: 1", "orders.result -> save.table")),
                      on(page, view, table), (view, "Check File (there, not empty, recent, size), Check Table")],
           badges=True, zoom=on(page, view, table))
    can = section_text(sections, r"\bwhat\b.*\b(?:can|do|does|build)\b")
    named = [thing for thing, pattern in CAN_DO.items() if re.search(pattern, can, re.I)]
    p.check(f"② What it can do, with What you can build above it, names {len(named)} of the {len(CAN_DO)} things "
            "Taskloom does: " + ", ".join(named) + " (RD-1)", len(named) >= 6)
    crop = table.adjusted(-8, -8, 8, 8) & view.viewport().rect()
    readme_picture, readme_row = view.viewport().grab().toImage().copy(crop), row.translated(-crop.topLeft())

    p.step("Scroll down to the end: Get it and start it, and Documentation.")
    scroll_to(view, "Get it and start it")
    where = on(page, view, span(view, "Documentation", "docs/ARCHITECTURE.md"))
    p.shot(page, "① How to get it (from the releases) and start it (taskloomw.exe, or taskloom on Linux and from "
                 "source). ② Where the docs are: Help → Documentation in the app, the website, the examples, and "
                 "③ the whole guide, docs/guide.md.",
           highlight=[on(page, view, span(view, "Get it and start it", "examples/out/sales_<day>.csv")),
                      where, (view, "The whole guide as one page")], badges=True, zoom=where)
    get, about = section_text(sections, r"\b(?:get|start)\b"), section_text(sections, r"\bdoc")
    p.check("① Get it and start it links the releases, and says to run taskloomw.exe for the editor (RD-1)",
            any(href.startswith(GITHUB + "/releases") for href in links(get)) and "taskloomw.exe" in get)
    p.check(f"② Documentation names Help → Documentation, links the website {SITE_URL} and ③ the guide, "
            "docs/guide.md (RD-1)",
            re.search(r"\bHelp\W+Documentation\b", about) and any(h.startswith(SITE_URL) for h in links(about))
            and "docs/guide.md" in links(about))

    # --- its link to the guide ------------------------------------------------------------------------------------
    p.step("Click docs/guide.md, under Documentation.")
    href = click_link(view, lambda href, text: href == "docs/guide.md")
    QApplication.processEvents()
    guide_lines = len(guide.splitlines())
    p.shot(page, f"The link opens ① the guide, docs/guide.md: {guide_lines:,} lines, the old README as it was. "
                 "② Its first section, Install, is what Get it and start it sums up.",
           highlight=[page.title, on(page, view, span(view, "Install", "taskloom.exe run flow.yaml"))], badges=True,
           zoom=[page.title, on(page, view, span(view, "Install", "taskloom.exe run flow.yaml"))])
    p.check(f"① The link docs/guide.md opens the guide: docs/guide.md, {guide_lines:,} lines (RD-3)",
            href == "docs/guide.md" and page.clicked == ["docs/guide.md"] and page.path == GUIDE)
    p.check(f"The guide keeps each of the {len(OLD_HEADINGS)} headings the README had before it was made short, at "
            "its level and in its order: nothing was lost (RD-2)", in_order(OLD_HEADINGS, headings(guide)))
    found, problems = list(dict.fromkeys(links(readme))), link_problems(readme)  # (a web address written out is
    # found twice: as a link, and as its text)
    broken = {href for href in found if any(problem.startswith(href + ":") for problem in problems)}
    p.text("All the README's links work: each, and where it leads (checked without the internet)",
           "\n".join(f"{href}  →  {leads_to(href)}" + ("  (BROKEN)" if href in broken else "") for href in found)
           + ("\n\n" + "\n".join(problems) if problems else ""))
    p.check(f"Each of the README's {len(found)} links leads to a file or folder of the repository, the project's "
            "releases or the documentation website: none is broken (RD-3)", found and problems == [])

    # --- the guide in the editor: Help > Documentation -------------------------------------------------------------
    p.step("In the editor, choose Help > Documentation.")
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    bar = win.menuBar()
    at = bar.mapToGlobal(bar.actionGeometry(help_menu.menuAction()).bottomLeft())
    p.while_open(lambda: help_menu.exec(at), lambda menu: choose(menu, "Documentation"))
    column, browser = win.docs, win.docs.browser
    wait_for(qtbot, lambda: column.isVisible() and browser.source().fileName() == "index.html",
             "Help > Documentation did not open the column at Contents")
    QApplication.sendPostedEvents(None, QEvent.LayoutRequest)  # the window laid out with the column, and its page
    QTest.qWait(200)
    old = [heading[3:] for heading in OLD_HEADINGS if heading.startswith("## ")]
    p.shot(win, "① The Documentation column opens at Contents, at the far right. ② Under Guide, a page for each "
                "section of docs/guide.md: the sections that were the README's, from Install to License.",
           highlight=[column, on(win, browser, span(browser, "Guide", old[-1]))], badges=True,
           zoom=on(win, browser, span(browser, "Guide", old[-1])))
    p.check("① The column is open at Contents (RD-2)", column.isVisible() and browser.source().fileName() == "index.html")
    contents = browser.toPlainText()
    listed = contents[contents.index("Guide"):contents.index("Reference")]
    at_ = [listed.find(heading) for heading in old]
    p.check(f"② Under Guide, Contents lists the {len(old)} sections the README had, in its order: " + ", ".join(old)
            + " (RD-2)", -1 not in at_ and at_ == sorted(at_))

    p.step("Click Checks, under Guide, and scroll down to Check Table.")
    click_link(browser, lambda href, text: href == "checks.html")
    anchor = docs.slug(CHECK_TABLE[4:])
    browser.scrollToAnchor(anchor)  # (where scrolling down brings it)
    wait_for(qtbot, lambda: heading_at_top(browser, anchor), "Check Table's section is not at the top of the column")
    entry = span(browser, CHECK_TABLE[4:], "1 row.")
    p.shot(win, "① The column shows the guide's Checks page, at ② Check Table: what the block does; under it, each "
                "of its checks, one by one. The canvas and the rest of the editor stay as they were.",
           highlight=[column, on(win, browser, entry)], badges=True, zoom=on(win, browser, entry))
    p.check("② The column shows the guide's Checks page, at Check Table: a section the README had, and no longer has "
            "(RD-2)", browser.source().fileName() == "checks.html" and heading_at_top(browser, anchor)
            and CHECK_TABLE in OLD_HEADINGS and CHECK_TABLE in headings(guide) and CHECK_TABLE not in headings(readme))

    p.step("Compare the two: the README's line on Check Table, beside the guide's page on it in the app.")
    row_widget, pictures = side_by_side([("README.md: What it can do", readme_picture),
                                         ("Help > Documentation: the guide's Checks page", browser.viewport().grab()
                                          .toImage())])
    qtbot.addWidget(row_widget)
    guide_part = guide[guide.index(CHECK_TABLE):]
    guide_part = guide_part[:re.search(r"\n#{2,3} ", guide_part).start()].splitlines()  # up to the next heading
    told = words(" ".join(guide_part))
    start = told.index(words(plain_line(guide_part)))
    sentence = told[start:told.index(". ", start) + 1]  # the whole sentence that line is in
    p.shot(row_widget, "① The README gives Check Table a few words, in the Checks row of its table. ② The guide "
                       "gives the details, and the app shows them in its Documentation column: the README points, "
                       "the guide explains.",
           highlight=[readme_row.translated(pictures[0].mapTo(row_widget, QPoint(0, 0))),
                      entry.translated(pictures[1].mapTo(row_widget, QPoint(0, 0)))], badges=True)
    row_widget.close()
    readme_lines = [line for line in lines if "Check Table" in line]
    cells = [cell.strip() for cell in readme_lines[0].strip().strip("|").split("|")]
    p.check("① In README.md, Check Table is in one line, the Checks row of its table: “" + ": ".join(cells) + "” "
            "(RD-1)",
            len(readme_lines) == 1 and cells[0] == "Checks")
    p.check(f"② The guide's section on it is {len(guide_part)} lines long, and the column shows it: “{sentence}” "
            "(RD-2)", len(guide_part) > 20 and sentence in words(browser.toPlainText()))

    # --- the README's example passes Validate ---------------------------------------------------------------------
    p.step("Open the README's example flow, and press Validate in the toolbar. First choose Help > Documentation "
           "again, to close the column; then save the example as daily-orders.yaml and open it (File > Open…).")
    p.note("Validate checks that warehouse, the connection its query uses, is one of yours. The proof's Taskloom "
           "folder, a new one made for it, has a JDBC connection of that name and nothing else: Validate only looks "
           "the name up, and nothing connects.")
    (flow_text,) = readme_flows()
    flow_path = tmp_path / "daily-orders.yaml"
    flow_path.write_text(flow_text, encoding="utf-8")
    (win.home.root / "connections.yaml").write_text(yaml.safe_dump(connections_named(yaml.safe_load(flow_text))),
                                                    encoding="utf-8")
    p.while_open(lambda: help_menu.exec(at), lambda menu: choose(menu, "Documentation"))
    wait_for(qtbot, lambda: not column.isVisible(), "Help > Documentation did not close the column")
    tab = open_flow(win, monkeypatch, flow_path)
    p.check("The README's example flow opens (BR-8)", tab is not None and tab.doc.path == flow_path.resolve())
    toolbar = win.findChild(QToolBar, "run-toolbar")
    QTest.mouseClick(toolbar.widgetForAction(win.validate_action), Qt.LeftButton)
    QApplication.processEvents()
    status = win.statusBar()
    said = status.currentMessage()
    message = QRect(status.mapTo(win, QPoint(0, 0)),
                    QSize(status.fontMetrics().horizontalAdvance(said) + 24, status.height()))
    p.shot(win, "① The README's example on the canvas: weekdays starts orders, which saves its result with save. "
                f"② Validate found no problems: “{said}”. The Log, under the canvas, is empty: no warning.",
           highlight=[["weekdays", "orders", "save"], message], badges=True, zoom=message)
    p.check(f"② Validate found no problems: the status bar says “{said}” (BR-8)",
            said == f"{tab.doc.title}: no problems found")
    p.check("No warning and no error: the Log shows none, so no single-brace {name} that would be plain text (did "
            "you mean {{name}}?), and no box of problems opened (BR-8)",
            [e for e in tab.entries if e.get("level") in ("WARNING", "ERROR")] == [] and win.message_boxes == [])
    p.check("The flow Validate checked is the README's example, as written there (BR-8)",
            flow_path.read_text(encoding="utf-8") == flow_text)
