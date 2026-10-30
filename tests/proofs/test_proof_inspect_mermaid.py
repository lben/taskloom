"""Proof: Inspect draws Mermaid diagrams in the Log, offline, with mmdr. How proofs are written: README.md here."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPalette, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel

from proofs.test_proof_inspect_log import (area, char_at, click_tab, double_click_run, find, hugged, lines, log_part,
                                           picture_area, plain, scroll_to, unreadable)
from proofs.test_proof_retention_per_start import drag_up, history_rows
from proofs.test_proof_run_output import click, run_now, setting
from taskloom.history import History
from tests.test_docs_help import click_link, nav_button, page_and_anchor
from tests.test_inspect_log import fixed_pitch_at
from tests.test_inspect_mermaid import (DARK, FAKE_PYINSTALLER, REPO, archive_files, as_text, contrast, fake_package,
                                        images, installed, is_grey_note, mmdr_error, near, note, on_path, size_of)
from tests.test_inspect_mermaid import painted as colours_of

ASKED = ("… the Inspect block should pride itself to be the best place to display text information, everything "
         "should be well formated, beautiful even (i.e: have markdown/mermaide support; I am most likely missing "
         "similar text beatify tech) … How should Taskloom draw Mermaid diagrams offline? — Try mmdr (+13 MB). Some "
         "of mmdr's parts are under the MPL-2.0 and the EPL-2.0 licences: ship them with their notices? — OK, ship "
         "with notices")
FLOWCHART = """\
flowchart LR
    read[Read orders] --> check(Check payments)
    check -- paid --> send[Send the report]
    check -- unpaid --> remind[Remind customers]
"""
SEQUENCE = """\
sequenceDiagram
    participant Taskloom
    participant Database
    participant Mail
    Taskloom->>Database: Read today's orders
    Database-->>Taskloom: {value} rows
    Taskloom->>Mail: Send the report
"""
CODE = "keep   these   spaces"
REPORT = f"""\
# Nightly orders

**{{value}}** orders came in today.

## How the flow runs

```mermaid
{FLOWCHART}```

## Who talks to whom

```mermaid
{SEQUENCE}```

| Step | Block |
|------|-------|
| Count | count_orders |
| Report | report |

```
{CODE}
```
"""  # the message is filled in like a Python f-string: {value} is count_orders' number
MISTAKE = "    read[Read orders --> send"  # the ] that closes the label is missing
DRAFT = f"""\
## Next week's plan

```mermaid
flowchart LR
{MISTAKE}
```

The rest of this message still shows.
"""
ORDERS = 42
FILLED = REPORT.replace("{value}", str(ORDERS))  # the message, as the run filled it in
FLOW = {
    "blocks": {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 180}},
        "count_orders": {"type": "logic.python", "config": {"inputs": [], "code": f"output = {ORDERS}"},
                         "ui": {"x": 260, "y": 180}},
        "report": {"type": "logic.inspect", "config": {"message": REPORT, "markdown": True}, "ui": {"x": 540, "y": 0}},
        "draft": {"type": "logic.inspect", "config": {"message": DRAFT, "markdown": True}, "ui": {"x": 540, "y": 180}},
        "as_written": {"type": "logic.inspect", "config": {"message": REPORT}, "ui": {"x": 540, "y": 360}},
    },
    "edges": ["start_by_hand.start -> count_orders.after", "count_orders.output -> report.value",
              "count_orders.output -> draft.value", "count_orders.output -> as_written.value"],
}
INSPECTS = ["report", "draft", "as_written"]
LOG_HEIGHT = 640  # the Log made taller: a diagram (up to 640 pixels high) and the lines around it


# --- what the Log shows -------------------------------------------------------------------------------------------

def pictures_after(win, start: int) -> list[tuple]:
    """(position, format) of each picture the Log shows after the position `start`."""
    return [(pos, fmt) for pos, fmt in images(win.log.text) if pos > start]


def drawn(win, pos: int, fmt) -> tuple[bool, float]:
    """Whether the Log paints a diagram at `pos` (some of it is not the Log's background), and how much its lines and
    words stand out from that background (the highest contrast, 1 to 21)."""
    edit = win.log.text
    base = edit.palette().color(QPalette.Base)
    colours = colours_of(edit, pos, fmt)
    return sum(not near(c, base) for c in colours) > 0.02 * len(colours), max(contrast(c, base) for c in colours)


def on_background(win, pos: int, fmt) -> bool:
    """The diagram at `pos` is drawn on the Log's background: its margin is the Log's background colour."""
    edit = win.log.text
    return all(near(c, edit.palette().color(QPalette.Base)) for c in colours_of(edit, pos, fmt, corner=True))


def run_start(win, run_id: int) -> int:
    return find(win, f"run #{run_id} · ")


def heading_level(win, text: str, start: int = 0) -> int:
    return win.log.text.document().findBlock(find(win, text, start)).blockFormat().headingLevel()


def table_at(win, text: str, start: int = 0):
    return QTextCursor(win.log.text.document().findBlock(find(win, text, start))).currentTable()


def cells(table) -> list[list[str]]:
    return [[table.cellAt(r, c).firstCursorPosition().block().text() for c in range(table.columns())]
            for r in range(table.rows())]


def message_field(win) -> tuple:
    """The name and the box of the selected Inspect block's Message, as Properties shows them (the name above it)."""
    [label] = [w for w in win.properties.widget().findChildren(QLabel) if w.text() == "Message"]
    layout = label.parentWidget().layout()
    return label, layout.itemAt(layout.indexOf(label) + 1).widget()


def picture_rect(win, pos: int, fmt) -> QRect:
    """The picture at `pos`, hugged by its outline (it does not cover the lines above and below it)."""
    return hugged(picture_area(win, pos, fmt))


# --- the terminal and the download ------------------------------------------------------------------------------

def printed_for(stderr: str, block: str) -> list[str]:
    """What `taskloom run` printed for `block`: its lines, each with the lines of its message."""
    printed, inside = [], False
    for line in stderr.splitlines():
        said = re.match(r"\d\d:\d\d:\d\d (\[(\w+)\] )?", line)
        if said and said.group(0):
            inside = said.group(2) == block and " INFO: " in line
        if inside:
            printed.append(line)
    return printed


def build_licences(tmp_path: Path) -> dict[str, str]:
    """packaging/build.py run on a copy of Taskloom, as the release runs it, with PyInstaller's bundling (minutes
    long) left out: the text of each file of the download's licenses folder."""
    repo = tmp_path / "taskloom-copy"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", ".venv", "dist", "build", "tests", "__pycache__",
                                                             ".pytest_cache"))
    fake = fake_package(tmp_path / "fake-pyinstaller", "PyInstaller", "")
    (fake / "PyInstaller" / "__main__.py").write_text(FAKE_PYINSTALLER, encoding="utf-8")
    done = subprocess.run([sys.executable, str(repo / "packaging" / "build.py")], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300,
                          env={**os.environ, "PYTHONPATH": on_path(fake)})
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    [archive] = [p for p in (repo / "dist").iterdir() if p.name.endswith((".zip", ".tar.gz"))]
    return {name[len("taskloom/"):]: as_text(data) for name, data in archive_files(archive).items()
            if name.startswith("taskloom/licenses/")}


def test_inspect_draws_mermaid_diagrams_in_the_log(env, proof, win, qtbot, monkeypatch, tmp_path):
    p = proof("Inspect draws Mermaid diagrams in the Log", ASKED)
    p.idea("With Show as Markdown ticked, each Mermaid diagram in an Inspect block's message (a ```mermaid code "
           "block) is now drawn as a picture in the Log, where the code block was. The rest of the Markdown is drawn "
           "as before.")
    p.idea("The diagrams are drawn offline, on your computer, by mmdr, as you chose: a small Mermaid engine inside "
           "the editor, with no browser and no internet. A diagram is drawn on the Log's own background, light or "
           "dark, and scaled down to fit in 640 × 640 pixels like Inspect's pictures, never up.")
    p.idea("A diagram mmdr cannot draw (a mistake in it, or a kind of diagram it does not know) shows as its text, "
           "with a grey line under it that says why; the rest of the message, and the Log, go on.")
    p.idea("Only the editor's Log draws. The run history keeps the message as written, so a run reopened from Run "
           "history is drawn again, and taskloom run in a terminal prints the Markdown as written.")
    p.why_simplest("A Mermaid code block is cut out of the message, handed to mmdr, and the picture it draws is put "
                   "where the code block was, the way the Log already shows Inspect's pictures. The Markdown around "
                   "it is drawn as before.")
    p.why_simplest("The message stays text. The run history, the terminal and the Linux runner keep and print it as "
                   "they did, and need nothing new: the editor draws a diagram only when the Log shows its line, and "
                   "keeps it, so it is not drawn again each time the Log repaints.")
    p.alternative("A browser engine inside Taskloom (Qt WebEngine) running Mermaid's own JavaScript",
                  "About 130 MB more in each download, a whole browser, for one kind of picture. mmdr adds about "
                  "13 MB, the cost you agreed to.")
    p.alternative("A web service that draws diagrams (such as mermaid.ink)",
                  "Your diagrams, and the data in them, would leave your computer, and a company proxy may block it. "
                  "The Log never goes on the internet.")
    p.alternative("Mermaid's own command-line tool (mermaid-cli)",
                  "It needs Node.js and a Chrome of its own to draw: hundreds of MB more, and a browser started for "
                  "each diagram.")
    p.alternative("Drawing the diagrams when the flow runs, and keeping the pictures with the run",
                  "The Linux runner would then need mmdr too, every run would store pictures, and a picture drawn when "
                  "the flow ran cannot follow the Log's light or dark colours when you look at it later.")
    p.fits("It is the Show as Markdown setting you already have: nothing new to tick. A diagram is shown as Inspect "
           "shows a picture: under the block's line, scaled down to fit in 640 × 640, never up.")
    p.fits("The run history keeps each line as written, with its mark “Markdown”, so a reopened run, or one the "
           "scheduler or a terminal ran, is drawn in the Log like a live one. The Linux runner has no mmdr, and the "
           "packaging check every release must pass verifies that; the editor app's check draws a diagram, so a "
           "build that lost mmdr fails.")
    p.fits("Only Inspect's Log draws Mermaid diagrams: an email (HTML Template) and the documentation do not draw "
           "them; there a Mermaid block stays text.")
    p.fits("The download carries mmdr's licences, as you said: “ship with notices”. mmdr and novasvg, which it draws "
           "with, are under the MIT licence; some of mmdr's parts are under the MPL-2.0 or the EPL-2.0, so the "
           "download has those licences' texts and where each part's source is. Near its end, the proof shows "
           "them.")
    flow = tmp_path / "nightly-orders.yaml"
    flow.write_text(yaml.safe_dump(FLOW, sort_keys=False), encoding="utf-8")
    flow = flow.resolve()
    edit = win.log.text

    p.step("Open the flow nightly-orders.yaml (File > Open…). count_orders makes a number, 42; three Inspect blocks "
           "show messages about it.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in file_menu.actions() if a.text() == "Open…").trigger()
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow)
    p.shot(win, "① report and ② draft show their messages as Markdown (Show as Markdown is ticked); ③ as_written "
                "shows the same message as report, with Show as Markdown unticked.",
           highlight=INSPECTS, badges=True, zoom=["count_orders", *INSPECTS])

    p.step("Click report. Its message, in Properties on the right, is Markdown with two Mermaid diagrams: a "
           "flowchart and a sequence diagram.")
    click(tab, "report")
    message_label, message_box = message_field(win)
    markdown_label, markdown_box = setting(win, "Show as Markdown")
    p.shot(win, "① report's Message, its Markdown as you write it: a heading, the number in bold ({value} is "
                "count_orders' number), a ```mermaid code block with the flowchart, another with the sequence diagram, "
                "then a table (and a code block, further down the box). ② Show as Markdown is ticked.",
           highlight=[[message_label, message_box], [markdown_label, markdown_box]], badges=True,
           zoom=[message_label, message_box, markdown_label, markdown_box])
    p.check("report's Show as Markdown is ticked", markdown_box.isChecked())

    # --- drawn in the Log ---------------------------------------------------------------------------------------
    p.step("Press ▶ Run now, drag the top edge of the Log up to make it taller, and click report. The Log then "
           "shows only report's lines.")
    first = run_now(win, tab, qtbot)
    p.check("The run ended with success", first.status == "success")
    drag_up(win, win.output, LOG_HEIGHT - win.output.height())
    qtbot.waitUntil(lambda: win.output.height() >= LOG_HEIGHT - 30, timeout=5_000)
    click(tab, "report")
    start_1 = run_start(win, first.run_id)
    header = next(line for line in lines(win, start_1) if re.fullmatch(r"\d\d:\d\d:\d\d \[report\] INFO", line))
    found = pictures_after(win, start_1)
    assert len(found) == 2, plain(win)
    (flowchart, flowchart_fmt), (sequence, sequence_fmt) = found
    shown = lines(win, start_1)
    p.check("report's line is its time, [report] and INFO, with its message drawn under it",
            shown[shown.index(header) + 1] == "Nightly orders")
    p.check("The Log shows two pictures for report, one for each Mermaid diagram, and neither diagram as text",
            "flowchart LR" not in shown and "sequenceDiagram" not in shown
            and not any(line.lstrip().startswith(("read[", "Taskloom->>")) for line in shown))
    p.check("Each is where its code block was: the flowchart under “How the flow runs”, the sequence diagram under "
            "“Who talks to whom”, and the table after them",
            find(win, "How the flow runs", start_1) < flowchart < find(win, "Who talks to whom", start_1) < sequence
            < find(win, "Count", start_1))
    sizes = [size_of(flowchart_fmt), size_of(sequence_fmt)]
    p.check("Each fits in 640 × 640", all(max(size) <= 640.5 for size in sizes))
    flowchart_drawn, sequence_drawn = drawn(win, flowchart, flowchart_fmt), drawn(win, sequence, sequence_fmt)
    p.check("Both are drawn on the Log's own white background: their margins are that background, and their lines "
            "and words stand out from it (a contrast of at least 3 to 1)",
            on_background(win, flowchart, flowchart_fmt) and on_background(win, sequence, sequence_fmt)
            and flowchart_drawn[0] and sequence_drawn[0] and min(flowchart_drawn[1], sequence_drawn[1]) >= 3)
    p.check("No grey line says a diagram could not be drawn", "(could not draw this diagram" not in plain(win))

    scroll_to(win, find(win, header, start_1))
    line_at = area(win, find(win, header, start_1), find(win, header, start_1))
    picture = picture_area(win, flowchart, flowchart_fmt)
    p.shot(win, "report's message, drawn as Markdown under its line: the heading, the number in bold, and ① where the "
                "first ```mermaid code block was, the flowchart, drawn as a picture.",
           highlight=[picture_rect(win, flowchart, flowchart_fmt)], badges=True,
           zoom=log_part(win, line_at.top(), picture.bottom() + 4))

    p.step("Scroll down the Log to the second diagram.")
    scroll_to(win, find(win, "Who talks to whom", start_1))
    heading_at = area(win, find(win, "Who talks to whom", start_1), find(win, "Who talks to whom", start_1))
    picture = picture_area(win, sequence, sequence_fmt)
    p.shot(win, f"② Where the second code block was, the sequence diagram: who sends what to whom, in order. "
                f"“{ORDERS} rows” is count_orders' number, filled into the diagram before it was drawn.",
           highlight=[picture_rect(win, sequence, sequence_fmt)], badges=[2],
           zoom=log_part(win, heading_at.top(), picture.bottom() + 4))
    p.note("mmdr is young (version 0.4, by one maintainer). It draws the common kinds of Mermaid diagram, with small "
           "differences of style from Mermaid's own site: the colours (its flowchart boxes are white, where Mermaid's "
           "site draws them lavender) and the arrow ends, for two.")

    p.step("Scroll down to the end of report's message: the rest of the Markdown.")
    table = table_at(win, "Count", start_1)
    code = find(win, CODE, start_1)
    scroll_to(win, table.firstPosition())
    table_rect = hugged(area(win, table.firstPosition(), table.lastPosition())).adjusted(0, 0, 16, 0)
    p.shot(win, "After the diagrams, the rest of the Markdown is drawn as before: ③ a table with borders, ④ a code "
                "block in the fixed-width font, its spaces kept.",
           highlight=[table_rect, (edit, CODE)], badges=[3, 4], zoom=[table_rect, area(win, code, code)])
    body = find(win, f"{ORDERS} orders came in today.", start_1)
    p.check("The rest of the Markdown is unchanged: the headings are headings, the number is bold, the table has 3 "
            "rows of 2 columns, the code block keeps its spaces in the fixed-width font",
            heading_level(win, "Nightly orders", start_1) == 1 and heading_level(win, "How the flow runs", start_1) == 2
            and heading_level(win, "Who talks to whom", start_1) == 2 and char_at(edit, body).fontWeight() >= 600
            and table is not None and cells(table) == [["Step", "Block"], ["Count", "count_orders"],
                                                       ["Report", "report"]]
            and table.format().border() > 0 and fixed_pitch_at(edit, code) and not fixed_pitch_at(edit, body + 4))

    # --- a diagram that cannot be drawn -------------------------------------------------------------------------
    p.step("Click draft. Its message has a flowchart with a mistake: the ] that closes the label “Read orders” is "
           "missing.")
    click(tab, "draft")
    start_1 = run_start(win, first.run_id)
    expected = note(mmdr_error("flowchart LR\n" + MISTAKE + "\n"))
    draft_header = next(line for line in lines(win, start_1) if re.fullmatch(r"\d\d:\d\d:\d\d \[draft\] INFO", line))
    done_line = f"DONE run #{first.run_id} success"
    scroll_to(win, find(win, draft_header, start_1))
    top, bottom = (area(win, find(win, text, start_1), find(win, text, start_1)) for text in (draft_header, done_line))
    p.shot(win, "① The diagram mmdr could not draw shows as its text, ② followed by a grey line that says why. Then "
                "the rest of the message still shows, and the Log goes on: the run's last line comes after it.",
           highlight=[[(edit, "flowchart LR"), (edit, MISTAKE)], (edit, expected)],
           badges=True, zoom=log_part(win, top.top(), bottom.bottom() + 4))
    shown = lines(win, start_1)
    p.check("① The diagram shows as its text, in the fixed-width font", "flowchart LR" in shown and MISTAKE in shown
            and fixed_pitch_at(edit, find(win, MISTAKE, start_1) + 4))
    p.check(f"② Under it, in grey italics: “{expected}”, mmdr's own reason", expected in shown
            and shown.index(MISTAKE) < shown.index(expected) and is_grey_note(edit, expected)
            and char_at(edit, find(win, expected, start_1)).fontItalic())
    p.check("Then the rest of the message, and the run's last line: the run succeeded, the Log goes on",
            shown.index(expected) < shown.index("The rest of this message still shows.")
            < next(i for i, line in enumerate(shown) if line.endswith(done_line)) and first.status == "success")
    p.check("No picture is drawn for draft", pictures_after(win, start_1) == [])

    # --- Show as Markdown off -----------------------------------------------------------------------------------
    p.step("Click as_written. Its message is report's, and its Show as Markdown is unticked.")
    click(tab, "as_written")
    start_1 = run_start(win, first.run_id)
    as_written = next(line for line in lines(win, start_1) if line.endswith("[as_written] INFO # Nightly orders"))
    after_fence = find(win, "```", find(win, "    check -- unpaid", start_1))  # the first code block's closing ```
    scroll_to(win, find(win, as_written, start_1))
    first_fence = area(win, find(win, "```mermaid", start_1), after_fence)
    p.shot(win, "Without Show as Markdown, the message shows exactly as written, every line and space kept: ① its # "
                "and ** are plain text, and ② the Mermaid code block is its text. Nothing is drawn.",
           highlight=[[(edit, f"**{ORDERS}** orders came in today."), (edit, "## How the flow runs")],
                      hugged(first_fence).adjusted(0, 0, 40, 0)], badges=True,
           zoom=log_part(win, area(win, find(win, as_written, start_1), find(win, as_written, start_1)).top(),
                         first_fence.bottom() + 4))
    shown = lines(win, start_1)
    p.check("as_written's Show as Markdown is not ticked", not setting(win, "Show as Markdown")[1].isChecked())
    p.check("Its message shows as written: each of its lines, in order, spaces kept",
            shown[shown.index(as_written) + 1:shown.index(as_written) + FILLED.count("\n")]
            == [line.rstrip() for line in FILLED.split("\n")[1:-1]])
    p.check("No picture is drawn for as_written", pictures_after(win, start_1) == [])

    # --- dark colours -------------------------------------------------------------------------------------------
    p.step("See report's Log in dark colours, as in dark mode.")
    p.note("The proof gives the editor the dark colours a dark mode gives it. A diagram already in the Log keeps the "
           "colours it was drawn in until the Log is drawn again, as it is when you click a block (or Show all): so "
           "the proof clicks as_written, then report.")
    original = QApplication.palette()
    dark = QPalette()
    for role, value in DARK.items():
        dark.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    try:
        QApplication.setPalette(dark)
        click(tab, "as_written")
        click(tab, "report")
        start_1 = run_start(win, first.run_id)
        (flowchart, flowchart_fmt), (sequence, sequence_fmt) = pictures_after(win, start_1)
        checked = [(on_background(win, pos, fmt), *drawn(win, pos, fmt))
                   for pos, fmt in ((flowchart, flowchart_fmt), (sequence, sequence_fmt))]
        base = edit.palette().color(QPalette.Base)
        scroll_to(win, find(win, "How the flow runs", start_1))
        heading_at = area(win, find(win, "How the flow runs", start_1), find(win, "How the flow runs", start_1))
        p.shot(win, "In dark colours, ① the flowchart and ② the sequence diagram are drawn on the Log's dark "
                    "background, in Mermaid's dark colours: their lines and words are light.",
               highlight=[picture_rect(win, flowchart, flowchart_fmt), picture_rect(win, sequence, sequence_fmt)],
               badges=True, zoom=[heading_at, picture_area(win, sequence, sequence_fmt)])
        p.check("The Log's background is dark", base.lightness() < 128)
        p.check("Both diagrams are drawn on it: their margins are the Log's dark background",
                all(on for on, _, _ in checked))
        p.check("Their lines and words stand out from it (a contrast of at least 3 to 1)",
                all(painted and highest >= 3 for _, painted, highest in checked))
        p.check("Every character of the Log's text stands out from what is behind it (at least 3 to 1)",
                unreadable(win) == [])
    finally:
        QApplication.setPalette(original)
        QApplication.processEvents()

    # --- the run history ----------------------------------------------------------------------------------------
    p.step("Press ▶ Run now again, then click the Run history tab, under the canvas.")
    second = run_now(win, tab, qtbot, runs=2)
    p.check("That run ended with success too", second.status == "success")
    click_tab(win, win.history_panel)
    runs = history_rows(win)
    table_view = win.history_panel.table
    p.shot(win, f"Run history lists the two runs, newest first: run {second.run_id}, and ① run {first.run_id}, the "
                "first one.",
           highlight=[(table_view, 1)], badges=True, zoom=[table_view.item(0, 0), table_view.item(len(runs) - 1, 4)])
    p.check("Run history lists the two runs, newest first, both succeeded",
            [(r[0], r[2]) for r in runs] == [(str(second.run_id), "success"), (str(first.run_id), "success")])

    p.step(f"Double-click run {first.run_id}, and click report: the run, reopened from the run history.")
    double_click_run(win, first.run_id)
    click(tab, "report")
    reopened = pictures_after(win, 0)
    p.check(f"The Log shows run {first.run_id} only", win.bottom_tabs.currentWidget() is win.log
            and f"run #{first.run_id} · " in plain(win) and f"run #{second.run_id} · " not in plain(win))
    history = History(win.home.history_db)
    try:
        stored = [(e["message"], e.get("show")) for e in history.logs(first.run_id) if e["block"] == "report"]
    finally:
        history.close()
    p.check("The run history kept report's message as written, the Mermaid code blocks as text, marked “Markdown”",
            stored == [(FILLED, "markdown")])
    p.check("Reopened, both diagrams are drawn again, at the same sizes as live",
            [size_of(fmt) for _, fmt in reopened] == sizes and all(drawn(win, *each)[0] for each in reopened))
    scroll_to(win, find(win, "How the flow runs"))
    heading_at = area(win, find(win, "How the flow runs"), find(win, "How the flow runs"))
    p.shot(win, f"Run {first.run_id}, reopened: ① the flowchart and ② the sequence diagram are drawn again, from the "
                "message the run history kept.",
           highlight=[picture_rect(win, *reopened[0]), picture_rect(win, *reopened[1])], badges=True,
           zoom=[heading_at, picture_area(win, *reopened[1])])

    # --- the licence notices ------------------------------------------------------------------------------------
    p.step("Choose Help > Documentation, then Contents and License; then look in the download's licenses folder.")
    p.note("The proof makes the download with Taskloom's build script, as a release does, but without bundling the "
           "programs (PyInstaller, minutes long). The editor does not show these files, so they are shown as text.")
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    next(a for a in help_menu.actions() if a.text() == "Documentation").trigger()
    qtbot.waitUntil(lambda: win.docs.isVisible(), timeout=5_000)
    QTest.mouseClick(nav_button(win, "Contents"), Qt.LeftButton)
    browser = win.docs.browser
    click_link(browser, lambda href, text: page_and_anchor(href)[0] == "license.html")
    QTest.qWait(100)
    said = [(browser, "MIT. The packaged editor"), (browser, "full list of components.")]
    p.shot(win, "① The documentation's License page says what the download carries for mmdr, in its licenses folder.",
           highlight=[said], badges=True, zoom=[(browser, "License"), *said])
    page = " ".join(browser.toPlainText().split())
    p.check("① The License page says so", browser.source().fileName() == "license.html"
            and "mmdr and novasvg (MIT), which draw Mermaid diagrams" in page
            and "licenses/mmdr has those licence texts, where each one's source is" in page)
    licences = build_licences(tmp_path)
    beside = ", ".join(name.split("/", 1)[1] for name in sorted(licences)
                       if name.startswith(("licenses/mmdr/", "licenses/novasvg/")) and not name.endswith("SOURCES.txt"))
    sources = licences.get("licenses/mmdr/SOURCES.txt", "")
    p.text(f"licenses/mmdr/SOURCES.txt (and in licenses: {beside})", sources)
    own = {name: installed(name, "licenses") for name in ("mmdr", "novasvg")}
    p.check("mmdr's and novasvg's own licences (MIT) are in the download",
            all(texts and all(as_text(text) in licences.values() for text in texts) for texts in own.values()))
    [sbom] = installed("mmdr", "sboms")
    p.check("So is mmdr's full list of its parts and their licences", as_text(sbom) in licences.values())
    mpl, epl = (" ".join(licences.get(f"licenses/mmdr/{name}", "").split()) for name in ("MPL-2.0.txt", "EPL-2.0.txt"))
    p.check("And the whole texts of the MPL-2.0 and of the EPL-2.0",
            mpl.startswith("Mozilla Public License Version 2.0") and "Exhibit B - \"Incompatible With Secondary "
            "Licenses\"" in mpl and epl.startswith("Eclipse Public License - v 2.0")
            and "Exhibit A - Form of Secondary Licenses Notice" in epl)
    copyleft = [c for c in json.loads(sbom)["components"]
                if re.search(r"\b(MPL|EPL)-2\.0\b", json.dumps(c.get("licenses") or []))]
    p.check(f"SOURCES.txt names each of mmdr's {len(copyleft)} parts under the MPL-2.0 or the EPL-2.0, with where "
            "its source is", len(copyleft) >= 4 and all(
                f"{c['name']} {c['version']} " in sources and f"https://crates.io/crates/{c['name']}/{c['version']}"
                in sources for c in copyleft))

    # --- the terminal -------------------------------------------------------------------------------------------
    p.step("In a terminal, run the flow: taskloom run nightly-orders.yaml.")
    p.note("The proof runs it with mmdr taken away, as on the Linux runner, which does not carry it: a stand-in for "
           "mmdr that leaves a mark if anything loads it. The proof reads what the run prints in UTF-8, as a terminal "
           "shows it.")
    saved = yaml.safe_load(flow.read_text(encoding="utf-8"))["blocks"]
    p.check("The flow file is as the editor shows it: report shows its message as Markdown, as_written does not",
            not tab.doc.dirty and saved["report"]["config"].get("markdown") is True
            and not saved["as_written"]["config"].get("markdown"))
    fake = fake_package(tmp_path / "no-mmdr", "mmdr", "from pathlib import Path\n"
                        "Path(__file__).parents[2].joinpath('mmdr-was-loaded').write_text('yes', encoding='utf-8')\n"
                        "raise ImportError('this runner has no mmdr')\n")
    env.env.update(PYTHONPATH=on_path(fake), PYTHONIOENCODING="utf-8")
    done = env.taskloom("run", flow, encoding="utf-8", errors="replace", timeout=180)
    printed = printed_for(done.stderr, "report")
    p.text("What it printed for report (its other lines left out)", "\n".join(printed).rstrip("\n"))
    p.check("The run succeeded", done.returncode == 0)
    p.check("It never loaded mmdr: it draws nothing", not (tmp_path / "mmdr-was-loaded").exists())
    p.check(f"report's lines are its Markdown as written, the Mermaid code blocks too, with {ORDERS} filled in",
            bool(printed) and re.match(r"\d\d:\d\d:\d\d \[report\] INFO: ", printed[0])
            and "\n".join(printed).split(" INFO: ", 1)[1] == FILLED)
