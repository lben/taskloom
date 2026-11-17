"""Proof: Validate's status bar and the Documentation column's width (docs/promises/validate-docs-fixes.yaml, VAL-1,
DOC-1). Right after a Validate that found no problems, a Validate, and then a Run now, that find one open the box of
problems with the status bar empty; in Help > Documentation, 360 px wide, the Checks page's long YAML line goes on to
the next line, and no page scrolls sideways."""

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from proofs.conftest import choose
from proofs.test_proof_docs_help import open_flow
from proofs.test_proof_line_failures import box_text, press
from proofs.test_proof_readme import on, scroll_to, span
from proofs.test_proof_settings_wording import type_in
from taskloom.editor.window import DocsColumn
from tests.test_docs_help import click_link, wait_for
from tests.test_paths_editor_editing import flow_file, settle
from tests.test_tooltip_font_and_status import click_tab

ASKED = ("Two things to fix, in the rows you approved: after a Validate that found no problems, a Validate or a Run "
         "that finds one opened its box of problems over a status bar still saying “no problems found”; and in "
         "Help > Documentation, at the 360 px the column opens with, pages such as Checks scrolled sideways, their "
         "long code lines cut off at the column's edge.")
REAL_WARNING = QMessageBox.warning  # (the `win` fixture records boxes in its place: here they are shown)
BLOCKS = {"start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
          "daily_totals": {"type": "logic.python", "config": {"inputs": [], "code": "pass"}, "ui": {"x": 0, "y": 170}}}
NO_SUCH_ZONE = "Mars/Olympus_Mons"  # a time zone that does not exist: the flow's problem
LONG_LINE = ("retry: {policy: fixed, delay: 10m", "50 minutes")  # the Checks page's longest YAML line, its two ends


def said_area(win) -> QRect:
    """Where the status bar shows its message, in the window's pixels; the whole bar when it says nothing."""
    status = win.statusBar()
    said = status.currentMessage()
    width = status.fontMetrics().horizontalAdvance(said) + 24 if said else status.width()
    return QRect(status.mapTo(win, QPoint(0, 0)), QSize(width, status.height()))


def boxed(p, win, monkeypatch, action, caption: str) -> dict:
    """Press a toolbar button (Validate, ▶ Run now) that opens the box of problems; shoot it over the window, with
    its text and the status bar outlined, and press OK. Its title and text, and while it was open the status bar's
    message and its usual line (the flows running now, which shows when there is no message)."""
    seen = {}

    def warning(parent, title, *rest):  # the real box; its title is in its title bar, which is not drawn here
        seen["title"] = title
        return REAL_WARNING(parent, title, *rest)

    def look(box):
        seen["text"], seen["status"] = box.text(), win.statusBar().currentMessage()
        seen["usual"] = win.running_flows.text() if win.running_flows.isVisible() else None
        p.shot(box, caption, over=win, highlight=[box_text(box), said_area(win)], badges=True)
        box.accept()

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "warning", staticmethod(warning))
        p.while_open(lambda: press(win, action), look)
    QTest.qWait(100)
    return seen


def validated(win) -> str:
    """Press Validate in the toolbar, as you would: what the status bar then says."""
    press(win, win.validate_action)
    QApplication.processEvents()
    return win.statusBar().currentMessage()


def test_validates_status_bar_and_the_documentation_columns_width(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Validate's status bar and the Documentation column's width", ASKED)
    p.idea("When Validate or Run finds problems, the status bar's message is emptied just before the box that lists "
           "them opens: a “no problems found” from an earlier Validate no longer says the opposite under it, and the "
           "bar shows its usual line, the flows running now. A Validate that finds no problems still says so for 5 "
           "seconds.")
    p.idea("In Help > Documentation, a line of a code example longer than the column goes on to the next line, so at "
           "the 360 px the column opens with, no page scrolls sideways: each reads from top to bottom.")
    p.why_simplest("Validate and Run (Run now, Run from here, Run only) all check the flow in one place before the box "
                   "of problems opens: one line there, emptying the status bar's message, covers them all.")
    p.why_simplest("One rule in the column's style, pre { white-space: pre-wrap; }: Qt keeps a code example's spaces "
                   "and line breaks, and wraps only a line too long for the column. No page of the guide was "
                   "rewritten.")
    p.alternative("Shorten the guide's long code lines to fit 360 px",
                  "Ten or more of the guide's pages had such lines, not only Checks, and any new example could bring "
                  "one back; the rule wraps them all, now and later.")
    p.alternative("Say “problems found” in the status bar instead of emptying it",
                  "The box and the Log already list the problems; the status bar would only say it a third time.")
    p.fits("The Log lists the problems as before, and the box's words are the same; a Validate with no problems "
           "says so in the status bar as before.")
    p.fits("The documentation website, made from the same guide, is unchanged: its pages are wider (1280 px), and "
           "none of them scrolls sideways.")

    # --- VAL-1: Validate, then Run, finding problems ----------------------------------------------------------------
    p.step("Open a flow with no problems (File > Open…), click the Flow tab on the right, and press Validate in the "
           "toolbar.")
    tab = open_flow(win, monkeypatch, flow_file(tmp_path / "daily-totals.yaml", BLOCKS,
                                                ["start_by_hand.start -> daily_totals.after"]))
    click_tab(win.right_tabs, win.flow_settings)
    settle()
    zone = win.flow_settings.timezone
    clean = f"{tab.doc.title}: no problems found"
    said = validated(win)
    p.shot(win, f"① Validate found no problems: the status bar says “{said}”, for 5 seconds, in place of its "
                "usual line. ② The flow's time zone, on the Flow tab, is empty: the computer's own.",
           highlight=[said_area(win), [win.flow_settings.form.labelForField(zone), zone]], badges=True,
           zoom=said_area(win))
    p.check(f"① The status bar says “{clean}” (VAL-1)", said == clean and win.message_boxes == [])

    p.step(f"At once, type {NO_SUCH_ZONE} in Time zone, a time zone that does not exist, press Enter, and press "
           "Validate again.")
    type_in(zone, NO_SUCH_ZONE)
    p.check(f"Just before pressing Validate, the status bar still says “{clean}”",
            win.statusBar().currentMessage() == clean)
    seen = boxed(p, win, monkeypatch, win.validate_action,
                 f"① The box lists the flow's problem: no time zone {NO_SUCH_ZONE}. ② The status bar under it has "
                 "no message: the “no problems found” of a moment ago is gone, and it shows its usual line, “No flows "
                 "running”.")
    p.note("These screenshots draw no window title bar, so the box's title, “The flow has problems”, is not in the "
           "picture: the checks read it from what Taskloom gives the box.")
    p.check("① Validate opens the box “The flow has problems”, naming the time zone (VAL-1)",
            seen["title"] == "The flow has problems" and NO_SUCH_ZONE in seen["text"])
    p.check("② While the box is open, the status bar has no message: it shows its usual line, “No flows running” "
            "(VAL-1)", seen["status"] == "" and seen["usual"] == "No flows running")

    p.step("Type Europe/Madrid in Time zone and press Enter, press Validate (no problems found), then at once type "
           f"{NO_SUCH_ZONE} again, press Enter, and press ▶ Run now.")
    type_in(zone, "Europe/Madrid")
    said = validated(win)
    p.check(f"With Europe/Madrid, Validate finds no problems: the status bar says “{said}” (VAL-1)", said == clean)
    type_in(zone, NO_SUCH_ZONE)
    p.check(f"Just before pressing ▶ Run now, the status bar still says “{clean}”",
            win.statusBar().currentMessage() == clean)
    seen = boxed(p, win, monkeypatch, win.run_action,
                 "Run now finds the problem before it starts: ① the same box, listing it, and ② under it the status "
                 "bar with no message, showing its usual line.")
    p.check("① Run now opens the same box, “The flow has problems”, naming the time zone (VAL-1)",
            seen["title"] == "The flow has problems" and NO_SUCH_ZONE in seen["text"])
    p.check("② While the box is open, the status bar has no message: it shows its usual line, “No flows running” "
            "(VAL-1)", seen["status"] == "" and seen["usual"] == "No flows running")
    p.check("Nothing ran", not tab.runs)

    # --- DOC-1: the column at 360 px ----------------------------------------------------------------------------------
    p.step("Choose Help > Documentation, click Checks under Guide, and scroll down to “To wait for a file that "
           "arrives late”, on Check File.")
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    bar = win.menuBar()
    at = bar.mapToGlobal(bar.actionGeometry(help_menu.menuAction()).bottomLeft())
    p.while_open(lambda: help_menu.exec(at), lambda menu: choose(menu, "Documentation"))
    column, browser = win.docs, win.docs.browser
    wait_for(qtbot, lambda: column.isVisible() and browser.source().fileName() == "index.html",
             "Help > Documentation did not open the column at Contents")
    QApplication.sendPostedEvents(None, QEvent.LayoutRequest)  # the window laid out with the column
    QTest.qWait(200)
    click_link(browser, lambda href, text: href == "checks.html")
    wait_for(qtbot, lambda: browser.source().fileName() == "checks.html", "Checks did not open")
    QTest.qWait(100)
    scroll_to(browser, "To wait for a file that arrives late")
    line = span(browser, *LONG_LINE)
    doc = browser.document()
    start = doc.find(LONG_LINE[0])
    end = doc.find(LONG_LINE[1], start.selectionStart())
    wraps = browser.cursorRect(end).top() > browser.cursorRect(start).top()
    sideways = browser.horizontalScrollBar()
    p.shot(win, f"① The Documentation column, {column.width()} px wide as it opens, at the guide's Checks page. ② The "
                "example's long line, retry: … # waits up to 50 minutes, goes on to the next line; no scroll bar "
                "along the column's bottom, nothing cut off at its edge.",
           highlight=[column, on(win, browser, line)], badges=True,
           zoom=on(win, browser, line.adjusted(0, -110, 0, 110) & browser.viewport().rect()))
    p.check(f"① The column is {DocsColumn.WIDTH} px wide, the width it opens with (DOC-1)",
            column.width() == DocsColumn.WIDTH == 360)
    p.check("② The line retry: {policy: fixed, delay: 10m, max_attempts: 6} # waits up to 50 minutes starts on one "
            "line and ends on the next (DOC-1)", wraps)
    p.check("The Checks page does not scroll sideways: its sideways scroll bar is hidden, with nothing to scroll "
            "(DOC-1)", sideways.maximum() == 0 and not sideways.isVisible())

    p.step("Open every page of the column in turn: Contents, each page of the guide and each block's page.")
    wide = {}
    for name in browser.pages():
        browser.setSource(QUrl(name))
        settle()
        if sideways.maximum() > 0:
            wide[name] = sideways.maximum()
    p.text(f"The column's pages at {column.width()} px: how far each that scrolls sideways does",
           f"{len(browser.pages())} pages, " + (", ".join(f"{name}: {px} px" for name, px in wide.items())
                                               if wide else "none of which scrolls sideways"))
    p.check(f"None of the column's {len(browser.pages())} pages scrolls sideways at {column.width()} px (DOC-1)",
            column.width() == 360 and wide == {})
