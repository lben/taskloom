"""Proof: Check File, in the new Checks group: that a file, or the files matching a pattern, is there, not empty,
changed recently enough and of the expected size, on this computer or on the server of an SSH connection; each check
fails the block or only warns; with Retry it waits for a late file. How proofs are written: README.md here."""

import datetime as dt
import os
import re
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QHelpEvent, QPalette, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QPlainTextEdit,
                               QToolBar, QToolTip)

from proofs.test_proof_api_request import click_bottom_tab, open_flow, pick, preview_table
from proofs.test_proof_block_icons import list_row
from proofs.test_proof_python_reusable_block import connect, drag_block, plain, run_now, tooltip
from proofs.test_proof_run_output import click, type_into
from proofs.test_proof_tab_plus import set_theme
from proofs.test_proof_theme import theme_list
from taskloom.editor import theme
from tests.test_block_icons import rows
from tests.test_editor import add_from_palette
from tests.test_inspect_mermaid import contrast
from tests.test_paths_editor_editing import clean, field, flow_file, settle
from tests.test_theme import TEXT, not_in_theme

ASKED = ("Add Checks to the queue after this release. — Then, offered two extras (a row count compared with the "
         "last successful run, e.g. within 30%, and a Check File block: exists, not empty, modified since, size): "
         "“Yes to both extras. Also check for the data type of a column, sometimes I have found surprises of text "
         "data within a numeric column for instance, users may want to check if a column has null values as well.” — "
         "Agreed with you: a new Checks category; each check is ticked to use it, and either fails the block (the "
         "default) or only warns in the Log.")
CHECK = "checks.check_file"
LOG_HEIGHT = 260  # the Log, under the canvas: room for a run's lines
SINCE = "now() - days(1)"
MINIMUM, MAXIMUM = "1 KB", "200 MB"
CHECKS = ("Not empty", "Modified since", "Size")  # the ticked checks, each with its If it fails
DELAY, ATTEMPTS = "5s", 4  # Retry: every 5 seconds, 4 attempts in all
NOT_ON_WINDOWS = ("The SSH server of this proof is the small one Taskloom's own tests start on the computer they run "
                  "on. It serves that computer's folders the way a Linux server serves its own, with paths written "
                  "the Linux way, which Windows' folders are not: Taskloom's own tests of Check File on a server run "
                  "on Linux and macOS for that reason. On Windows, Check File reaches a real SSH server just the same.")


# --- the files ------------------------------------------------------------------------------------------------------

def sales(rows_: int) -> bytes:
    """A CSV of sales, a row per day and region: `rows_` rows."""
    regions = ("north", "south", "east", "west")
    return ("day,region,orders,total\n" + "".join(
        f"2026-10-{1 + i // 4 % 31:02},{regions[i % 4]},{10 + i % 7},{1200 + 37 * i}\n" for i in range(rows_))).encode()


def put(path: Path, data: bytes, when: float | None = None) -> Path:
    """A file with exactly these bytes, last changed at `when` (a timestamp) if given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if when is not None:
        os.utime(path, (when, when))
    return path


def shown_time(timestamp: float) -> str:
    """A time as the Log shows it: on this computer's clock (the flows here have no time zone of their own)."""
    return dt.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def three_days_ago() -> dt.datetime:
    return (dt.datetime.now() - dt.timedelta(days=3)).replace(hour=8, minute=0, second=0, microsecond=0)


# --- the flows ------------------------------------------------------------------------------------------------------

def start(x=0, y=0) -> dict:
    return {"type": "trigger.manual", "ui": {"x": x, "y": y}}


def python(inputs: list, code: str, x, y) -> dict:
    return {"type": "logic.python", "config": {"inputs": inputs, "code": code}, "ui": {"x": x, "y": y}}


def check_file(x, y, **config) -> dict:
    return {"type": CHECK, "config": config, "ui": {"x": x, "y": y}}


def press_run_now(win):
    """Press ▶ Run now in the toolbar (the run goes on: see wait_for_run)."""
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)


def wait_for_run(qtbot, tab, count: int):
    """Wait for the flow's `count`th run to end: the run."""
    qtbot.waitUntil(lambda: len(tab.runs) == count and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)
    QTest.qWait(200)  # what the run printed last reaches the Log
    return tab.runs[-1]


# --- Properties -----------------------------------------------------------------------------------------------------

def in_window(base, widget, rect: QRect | None = None) -> QRect:
    """`rect` of `widget` (all of it by default) in a screenshot of `base`: the window, or a panel of it."""
    rect = rect if rect is not None else widget.rect()
    return QRect(widget.mapTo(base, rect.topLeft()), rect.size())


def rows_named(win, label: str) -> list[tuple[QLabel, object]]:
    """Each setting Properties shows with the name `label` (its name and its field), top to bottom."""
    found = [(form.itemAt(r, QFormLayout.LabelRole).widget(), form.itemAt(r, QFormLayout.FieldRole).widget())
             for form in win.properties.widget().findChildren(QFormLayout) for r in range(form.rowCount())
             if (name := form.itemAt(r, QFormLayout.LabelRole)) and isinstance(name.widget(), QLabel)
             and name.widget().text() == label and name.widget().isVisible()]
    return sorted(found, key=lambda row: row[0].mapTo(win, QPoint(0, 0)).y())


def row_of(win, label: str) -> tuple[QLabel, object]:
    """The one setting named `label`: its name and its field (a code box, as Since, is under its name)."""
    found = rows_named(win, label)
    if not found:  # a code box: its name above it, not in a form
        layout = win.properties.widget().layout()
        items = [layout.itemAt(i).widget() for i in range(layout.count())]
        found = [(w, items[i + 1]) for i, w in enumerate(items) if isinstance(w, QLabel) and w.text() == label]
    assert len(found) == 1, f"Properties shows {len(found)} settings named {label}"
    return found[0]


def if_it_fails(win, check: str) -> tuple[QLabel, QComboBox]:
    """The If it fails of the check `check` (Not empty, Modified since, Size): the first one under its tick."""
    below = row_of(win, check)[0].mapTo(win, QPoint(0, 0)).y()
    return next(row for row in rows_named(win, "If it fails") if row[0].mapTo(win, QPoint(0, 0)).y() > below)


def area(base, win, *settings) -> QRect:
    """These settings of Properties (by their names, or (name, field) pairs), in a screenshot of `base`."""
    found = QRect()
    for s in settings:
        name, box = row_of(win, s) if isinstance(s, str) else s
        found |= in_window(base, name) | in_window(base, box)
    return found


def tick(win, check: str):
    """Click the tick of the check `check`, as you would."""
    box = row_of(win, check)[1]
    assert isinstance(box, QCheckBox), f"{check} is no tick"
    QTest.mouseClick(box, Qt.LeftButton, Qt.NoModifier, QPoint(8, box.height() // 2))
    QTest.qWait(100)


def code_box(win, label: str) -> QPlainTextEdit:
    """The text box of the code box `label` (Since)."""
    return row_of(win, label)[1].findChild(QPlainTextEdit)


def type_code(win, label: str, text: str):
    """Click the code box `label`, type `text` over what it holds, then click elsewhere, as you would."""
    box = code_box(win, label)
    QTest.mouseClick(box.viewport(), Qt.LeftButton)
    box.selectAll()
    QTest.keyClicks(box, text)
    box.clearFocus()  # (as a click elsewhere: what it holds goes into the flow)
    QTest.qWait(100)


def retry_box(win) -> QGroupBox:
    return next(b for b in win.properties.widget().findChildren(QGroupBox) if b.title() == "Retry and timeout")


# --- the Log and Data preview ---------------------------------------------------------------------------------------

def log_text(win) -> str:
    return win.log.text.toPlainText()


def to_the_end(win):
    """Scroll the Log down to its last line, as you would."""
    bar = win.log.text.verticalScrollBar()
    bar.setValue(bar.maximum())
    QTest.qWait(50)


def run_log(win, run) -> list[str]:
    """The Log's lines of `run`, from the line that says it started to the one that says how it ended."""
    lines = log_text(win).splitlines()
    first = next(i for i, line in enumerate(lines) if f"run #{run.run_id} · " in line)
    last = next(i for i in range(first, len(lines)) if re.search(rf"\brun #{run.run_id} (success|failed)$", lines[i]))
    return lines[first:last + 1]


def said(log: list[str], block: str, level: str, *texts: str, but=()) -> bool:
    """The Log has a line of `block` at `level` with each of `texts` in it, and none of `but`."""
    return any(f"[{block}] {level} " in line and all(t in line for t in texts) and not any(b in line for b in but)
               for line in log)


def lists(log: list[str], block: str, shown: str, path: Path) -> bool:
    """The Log lists the file at `path` (shown as `shown`) with its size and when it was last changed."""
    size, changed = path.stat().st_size, path.stat().st_mtime
    return said(log, block, "INFO", shown, f"{size:,} bytes", f"last changed {shown_time(changed)}")


def log_span(win, run, first: str, last: str) -> QRect:
    """The Log's lines of `run` from the one showing `first` to the one showing `last`, as far as the longest of them
    goes (within the Log), in the window."""
    box = win.log.text
    begin = box.document().find(f"run #{run.run_id} · ")
    a = box.document().find(first, begin)
    b = box.document().find(last, a)
    assert not a.isNull() and not b.isNull(), f"the Log of run #{run.run_id} shows no {first!r} then {last!r}"
    right, block = 0, a.block()
    while block.isValid() and block.position() <= b.position():  # (each line is a block of its own)
        end = QTextCursor(block)
        end.movePosition(QTextCursor.EndOfBlock)
        right = max(right, box.cursorRect(end).right() + 6)
        block = block.next()
    top, bottom = box.cursorRect(a).top(), box.cursorRect(b).bottom()
    viewport = box.viewport()
    return in_window(win, viewport, QRect(0, top, min(right, viewport.width()), bottom - top + 1))


def log_lines(win, *texts: str) -> list:
    """The Log's lines showing these texts (each shown in the lines of one run only), outlined together."""
    return [(win.log.text, text) for text in texts]


def fit_columns(view):
    """Double-click the right edge of each column's header, as you would to see a column's values whole."""
    header = view.horizontalHeader()
    for column in range(header.count()):
        edge = header.sectionViewportPosition(column) + header.sectionSize(column) - 1
        QTest.mouseDClick(header.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(edge, header.height() // 2))
    QTest.qWait(50)


def preview_rows(win) -> list[list[str]]:
    model = win.preview.view.model()
    return [[str(model.data(model.index(r, c))) for c in range(model.columnCount())]
            for r in range(model.rowCount())] if model is not None else []


# --- on this computer -----------------------------------------------------------------------------------------------

def test_check_file_checks_the_files_a_flow_is_about_to_use(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Check File: are the files there, as expected?", ASKED)
    p.idea("Check File, a new block in the new Checks group, checks the files a flow is about to use, before it uses "
           "them: that a file, or the files matching a pattern such as C:/inbox/sales_*.csv, is there; and, for each "
           "check you tick, that every file found is not empty, was last changed at or after a time you give "
           "(Modified since), and is within a size (Size).")
    p.idea("Each check either fails the block (the default) or only warns in the Log, the flow going on. The Log "
           "lists each file found with its size and when it was last changed, then a line per check; a failure names "
           "each file that breaks it, with its size or its time. Its outputs are files, the paths found, and results, "
           "a table with a row per check, which the block's error output carries too.")
    p.idea("The files can be on this computer or on the server of one of your SSH connections (Path on), as Copy "
           "Files' are; a folder whose name matches is not a file, and does not count. With the block's Retry set, a "
           "failed check is checked again later, so Check File can wait for a file that arrives late.")
    p.why_simplest("It is one block with a tick per check, like Check Table, the Checks group's other block. Under "
                   "each tick come the values its check needs, if any (Since for Modified since, Minimum and Maximum "
                   "for Size), then its If it fails. It is there has no tick: it is always checked, and its If none is "
                   "there says what a failure does. There is no new kind of setting to learn.")
    p.why_simplest("Waiting for a late file is the Retry and timeout every block already has. A check that fails the "
                   "block is an ordinary failure, which Retry tries again after its delay, as many times as you say, "
                   "the Log showing each attempt: Check File has no waiting of its own.")
    p.why_simplest("It only asks the file system what it knows of each file, its size and when it was last changed: "
                   "it never opens a file, so a 2 GB file is checked as quickly as a small one.")
    p.alternative("A Python Code block that checks the files itself",
                  "Every flow would need its own code, and its own way of saying what is wrong. The ticks say it "
                  "once, the same way in every flow, and the Log and the results table name what broke.")
    p.alternative("A “Wait for a file” block of its own, looking again every few seconds",
                  "It would be a second way to wait, with its own settings, next to Retry, which already waits, tries "
                  "again as many times as you say and then fails clearly; the block's Timeout limits each attempt.")
    p.alternative("Stopping at the first file that breaks a check",
                  "With a pattern you would fix one file, run again, and only then learn of the next. Each check is "
                  "made on every file found, and its failure names them all.")
    p.alternative("Sizes as a number of bytes only",
                  "200 MB is easier to write and to read than 209715200. A size is a number with its unit, B, KB, MB "
                  "or GB (1.5 GB too), and 1 KB is 1024 bytes, as Windows counts them. It is written without the "
                  "comma Windows puts in the thousands: 204800 KB, not 204,800 KB.")
    p.fits("It is in the Blocks list under Checks, with Check Table, with its own icon, a page with a tick; Help > "
           "Documentation and its hover help describe it, as every block's do.")
    p.fits("Path on and Path work as Copy Files' do: a path on this computer (network folders too) or on the server "
           "of an SSH connection (Tools > Connections), a pattern in the last part, ~ for your login folder there. Its "
           "files output is a list of paths, as Copy Files gives.")
    p.fits("Since is an expression, as an If's condition is: the flow parameters, and today(), now(), days() and "
           "bdays() on the flow's clock (its time zone). The results table is a table like any other: Data preview "
           "shows it, and a block connected to the error output gets it with the error.")

    monkeypatch.chdir(tmp_path)  # (the runs the editor starts work in this folder: see the note in step 4)
    inbox = tmp_path / "inbox"
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    try:
        # --- 1. In the Blocks list ---------------------------------------------------------------------------------
        p.step("Look at the Blocks list, on the left: the new group Checks.")
        checks = {item.text(0): item for item in rows(win.palette_panel).get("Checks", [])}
        listed = checks.get("Check File")
        p.check("The Blocks list has a group Checks, with Check File and Check Table",
                sorted(checks) == ["Check File", "Check Table"])
        tree = win.palette_panel.tree
        tree.scrollToItem(listed)
        QApplication.processEvents()
        p.shot(win, "① The group Checks, in indigo, between Data and Reports, with ② Check File: its icon, a page with "
                    "a tick, white on indigo. Check Table is the group's other block.",
               highlight=[list_row(win, listed.parent()), list_row(win, listed)], badges=True,
               zoom=[list_row(win, listed.parent()), list_row(win, checks["Check Table"])])

        p.step("Rest the mouse on Check File in the Blocks list.")
        viewport = tree.viewport()
        at = tree.visualItemRect(listed).center()
        QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, at, viewport.mapToGlobal(at)))
        qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
        tip = tooltip()
        hover = " ".join(plain(tip.text()).split())
        cls = win.registry.get(CHECK)
        p.shot(tip, "Its hover help says what it checks, that each check fails the block or only warns, and that with "
                    "Retry it can wait for a file that arrives late, a check that only warns never retrying.",
               over=win, highlight=[tip], zoom=[tip])
        tip.hide()
        p.check("The hover help has the block's name and says what it does",
                hover.startswith("Check File") and " ".join(cls.description.split()) in hover)

        # --- 2. Its Properties -----------------------------------------------------------------------------------
        flow = flow_file(tmp_path / "inbox.yaml", {
            "start_by_hand": start(0, 40),
            "list_files": python(["files"], "output = files", 620, 40),
        })
        p.step("Open the flow inbox.yaml (File > Open…), double-click Check File in the Blocks list to add it to the "
               "flow, and click it. The flow has a Manual Start, start_by_hand, and a Python Code block, list_files, "
               "that gives back the list of files it gets.")
        tab = open_flow(win, monkeypatch, flow)
        add_from_palette(win, "Check File")
        settle()
        click(tab, "check_file")
        added = [b for b, spec in tab.doc.blocks.items() if spec.get("type") == CHECK]
        p.check("A Check File was added to the flow, as check_file", added == ["check_file"])
        problem = win.properties.error
        p.shot(win, "Properties shows its settings: ① in red, Path is required, as ② Path is still empty, under Path "
                    "on, This computer; ③ If none is there, fail the block; ④ Not empty, ticked in a new block, with "
                    "its If it fails; ⑤ Modified since and Size, not ticked. A line above each check sets it apart.",
               highlight=[problem, area(win, win, "Path on", "Path"), area(win, win, "If none is there"),
                          area(win, win, "Not empty", if_it_fails(win, "Not empty")),
                          area(win, win, "Modified since", "Size")], badges=True,
               zoom=[problem, area(win, win, "Path on", "Size")])
        p.check("Path is empty, and Properties says, in red: “Path is required”",
                field(win, "Path").text() == "" and problem.isVisible() and problem.text() == "Path is required")
        p.check("Not empty is ticked in the new block; Modified since and Size are not",
                row_of(win, "Not empty")[1].isChecked() and not row_of(win, "Modified since")[1].isChecked()
                and not row_of(win, "Size")[1].isChecked())
        p.check("Path on says This computer; If none is there and Not empty's If it fails say fail the block",
                row_of(win, "Path on")[1].findChild(QComboBox).currentText() == "This computer"
                and row_of(win, "If none is there")[1].currentText() == "fail the block"
                and if_it_fails(win, "Not empty")[1].currentText() == "fail the block")

        p.step("In Properties, name it check_inbox, type the Path inbox/sales_*.csv, and tick Modified since and "
               "Size.")
        p.note("The proof keeps its files in its own temporary folder, which the runs it starts work in, so it writes "
               "Path relative to that folder: inbox/sales_*.csv. You would write the whole path, such as "
               "C:/inbox/sales_*.csv, or a network folder's, \\\\fileserver\\share\\sales_*.csv, which Check File "
               "reads as Copy Files does; this proof shows no network folder.")
        type_into(row_of(win, "Name")[1], "check_inbox")
        type_into(field(win, "Path"), "inbox/sales_*.csv")
        tick(win, "Modified since")
        tick(win, "Size")
        panel = win.right_tabs
        since = code_box(win, "Since")
        problem = win.properties.error  # (Properties, built again for the ticks: its message too)
        p.shot(panel, "Their own settings appear under their ticks: ① for Modified since, Since, empty, which is "
                      "today() (as it says in grey): changed today; ② for Size, Minimum and Maximum, empty; and each "
                      "its If it fails, fail the block. ③ Properties says, in red, what Size still needs.",
               highlight=[area(panel, win, "Since", if_it_fails(win, "Modified since")),
                          area(panel, win, "Minimum", "Maximum", if_it_fails(win, "Size")), problem], badges=True)
        p.check("Under Modified since: Since, empty, showing today() in grey, and its If it fails; under Size: Minimum "
                "and Maximum, empty, and its If it fails; each If it fails says fail the block",
                since.toPlainText() == "" and since.placeholderText() == "today()"
                and field(win, "Minimum").text() == "" and field(win, "Maximum").text() == ""
                and [if_it_fails(win, c)[1].currentText() for c in CHECKS] == ["fail the block"] * 3)
        p.check("Properties says, in red: “Size: write a Minimum, a Maximum or both (or untick Size)”",
                problem.isVisible() and problem.text() == "Size: write a Minimum, a Maximum or both (or untick Size)")

        p.step(f"Type {SINCE} in Since (changed in the last 24 hours), {MINIMUM} in Minimum and {MAXIMUM} in "
               "Maximum.")
        p.note("Sizes use 1 KB = 1024 bytes, as Windows shows them: 1 KB is 1,024 bytes, 200 MB 209,715,200.")
        type_code(win, "Since", SINCE)
        type_into(field(win, "Minimum"), MINIMUM)
        type_into(field(win, "Maximum"), MAXIMUM)
        p.shot(panel, f"check_inbox's checks, each outlined with its own settings: ① the files, inbox/sales_*.csv on "
                      f"this computer; ② It is there, always checked; ③ Not empty; ④ Modified since, Since {SINCE}; "
                      f"⑤ Size, at least {MINIMUM} and at most {MAXIMUM}. Each fails the block if it fails.",
               highlight=[area(panel, win, "Path on", "Path"), area(panel, win, "If none is there"),
                          area(panel, win, "Not empty", if_it_fails(win, "Not empty")),
                          area(panel, win, "Modified since", "Since", if_it_fails(win, "Modified since")),
                          area(panel, win, "Size", "Minimum", "Maximum", if_it_fails(win, "Size"))], badges=True)
        config = tab.doc.blocks.get("check_inbox", {}).get("config", {})
        p.check(f"The block is now check_inbox, with Path inbox/sales_*.csv, Not empty, Modified since and Size "
                f"ticked, Since {SINCE}, Minimum {MINIMUM} and Maximum {MAXIMUM}",
                {k: config.get(k) for k in ("path", "modified", "since", "size", "minimum", "maximum")}
                == {"path": "inbox/sales_*.csv", "modified": True, "since": SINCE, "size": True, "minimum": MINIMUM,
                    "maximum": MAXIMUM} and row_of(win, "Not empty")[1].isChecked())

        p.step("Drag check_inbox between the two blocks, then connect start_by_hand's start to its run after, and its "
               "files to list_files' files.")
        drag_block(tab, "check_inbox", QPointF(300, 20))
        connect(tab, "start_by_hand", "start", "check_inbox", "after")
        connect(tab, "check_inbox", "files", "list_files", "files")
        line = ["start_by_hand", "check_inbox", "list_files"]
        p.shot(win, "① The line: start_by_hand starts check_inbox, whose files go to list_files.",
               highlight=[line], badges=True, zoom=line)
        p.check("The flow has the two connections",
                {"start_by_hand.start -> check_inbox.after", "check_inbox.files -> list_files.files"}
                <= set(tab.doc.data["edges"]))

        # --- 3. Files that break the checks ----------------------------------------------------------------------
        ago = three_days_ago()
        files = {"sales_1.csv": put(inbox / "sales_1.csv", sales(150)),
                 "sales_2.csv": put(inbox / "sales_2.csv", b""),
                 "sales_3.csv": put(inbox / "sales_3.csv", sales(150), ago.timestamp())}
        put(inbox / "orders_1.csv", sales(20))  # (not sales_*.csv)
        size, old = files["sales_1.csv"].stat().st_size, shown_time(ago.timestamp())
        p.step(f"Put three files in the folder inbox (the proof writes them): sales_1.csv, {size:,} bytes, changed "
               f"just now; sales_2.csv, empty; sales_3.csv, {size:,} bytes, last changed three days ago, {old}. Then "
               "press ▶ Run now.")
        p.note("Modified since compares Since with the time the file system keeps for each file, the time it was last "
               "changed (the proof sets sales_3.csv's), on the flow's clock.")
        win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)  # (room for the run's lines in the Log)
        run = run_now(win, qtbot, tab)
        click(tab, "check_inbox")
        click_bottom_tab(win, win.log)
        to_the_end(win)
        log = run_log(win, run)
        p.shot(win, "① check_inbox failed: it is red, and the line under it starts with why, Not empty, the first "
                    "check that failed. list_files did not run: it is grey, skipped, as something before it failed.",
               highlight=[line], badges=True, zoom=line)
        p.shot(win, "② The Log, showing check_inbox's lines: the three files found, each with its size and when it "
                    "was last changed; then a line per check: It is there passed; Not empty, Modified since and Size "
                    "failed, each naming the files that break it, with their size or their time.",
               highlight=[log_lines(win, "found 3 file(s)", "(Check File) failed")], badges=[2],
               zoom=[log_span(win, run, "found 3 file(s)", "Size: failed")])
        p.check("The run failed: check_inbox failed, the line under it starting with Not empty; list_files did not "
                "run: skipped, as something before it failed",
                run.status == "failed" and tab.scene.nodes["check_inbox"].status == "failed"
                and tab.scene.nodes["check_inbox"].status_text.startswith("✕ Not empty: ")
                and tab.scene.nodes["list_files"].status == "skipped"
                and "something before it failed" in tab.scene.nodes["list_files"].status_text)
        p.check("The Log says it found 3 files at inbox/sales_*.csv, and lists each with its size and when it was "
                "last changed",
                said(log, "check_inbox", "INFO", "found 3 file(s) at inbox/sales_*.csv")
                and all(lists(log, "check_inbox", name, path) for name, path in files.items()))
        p.check("It is there passed: 3 file(s) found",
                said(log, "check_inbox", "INFO", "It is there: passed: 3 file(s) found"))
        p.check("Not empty failed, naming sales_2.csv, empty (0 bytes), and no other file",
                said(log, "check_inbox", "ERROR", "Not empty: failed: ", "sales_2.csv is empty (0 bytes)",
                     but=("sales_1", "sales_3")))
        p.check(f"Modified since failed, naming sales_3.csv with its time, {old}, and no other file",
                said(log, "check_inbox", "ERROR", "Modified since: failed: ", f"sales_3.csv at {old}",
                     but=("sales_1", "sales_2")))
        p.check(f"Size failed, naming sales_2.csv, 0 bytes, less than the minimum {MINIMUM}, and no other file",
                said(log, "check_inbox", "ERROR", "Size: failed: ",
                     f"sales_2.csv is 0 bytes, less than the minimum {MINIMUM}", but=("sales_1", "sales_3")))

        # --- 4. Set to warn ---------------------------------------------------------------------------------------
        p.step("Set the If it fails of Not empty, Modified since and Size to warn: click each, then warn in its "
               "list.")
        choice = if_it_fails(win, "Not empty")[1]
        view, places = theme_list(choice)
        p.shot(view.window(), "① Each If it fails has two choices: fail the block, the default, or warn.", over=win,
               highlight=[view], badges=True, zoom=[view, area(win, win, "Not empty", if_it_fails(win, "Not empty"))])
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, places["warn"].center())
        QTest.qWait(100)
        for check in CHECKS[1:]:
            pick(if_it_fails(win, check)[1], "warn")
        p.check("Each If it fails offers fail the block and warn", list(places) == ["fail the block", "warn"])
        p.check("The three If it fails say warn",
                [if_it_fails(win, c)[1].currentText() for c in CHECKS] == ["warn"] * 3)

        p.step("Press ▶ Run now.")
        run = run_now(win, qtbot, tab)
        click(tab, "check_inbox")
        click_bottom_tab(win, win.log)
        to_the_end(win)
        log = run_log(win, run)
        p.shot(win, "① check_inbox succeeded, and list_files ran: both are green. Under check_inbox, its files, the "
                    "list of the files it found; under list_files, its output, the list it got.",
               highlight=[line], badges=True, zoom=line)
        p.shot(win, "② In the Log, the same three checks now only warn, their lines in the warnings' colour, naming "
                    "the same files.",
               highlight=[log_lines(win, *(f"{c}: warned" for c in CHECKS))], badges=[2],
               zoom=[log_span(win, run, "found 3 file(s)", "Size: warned")])
        p.check("The run succeeded: check_inbox and list_files show success, under them the list of files, starting "
                "with sales_1.csv",
                run.status == "success" and all(tab.scene.nodes[b].status == "success" for b in line[1:])
                and re.match(r"✓ files: \[\S*sales_1\.csv", tab.scene.nodes["check_inbox"].status_text)
                and re.match(r"✓ output: \[\S*sales_1\.csv", tab.scene.nodes["list_files"].status_text))
        p.check("The Log has a WARNING line for Not empty (naming sales_2.csv), Modified since (sales_3.csv) and Size "
                "(sales_2.csv)",
                said(log, "check_inbox", "WARNING", "Not empty: warned: ", "sales_2.csv is empty (0 bytes)")
                and said(log, "check_inbox", "WARNING", "Modified since: warned: ", f"sales_3.csv at {old}")
                and said(log, "check_inbox", "WARNING", "Size: warned: ", "sales_2.csv is 0 bytes"))
        got = tab.scene.values.get("list_files", {}).get("output", "")
        p.check("list_files got the 3 files, sales_1.csv, sales_2.csv and sales_3.csv, and no other",
                all(name in got for name in files) and "orders_1" not in got)

        p.step("Click Data preview, under the canvas, with check_inbox selected, and double-click the right edge of "
               "each column's header, to see the columns whole.")
        click_bottom_tab(win, win.preview)
        fit_columns(win.preview.view)
        results = preview_rows(win)
        p.shot(win, "① check_inbox's results, its first table output: a row per check, with its outcome, It is there "
                    "passed, Not empty, Modified since and Size warned, and its details.",
               highlight=[preview_table(win)], badges=True, zoom=[preview_table(win)])
        p.check("Data preview shows check_inbox.results, 4 rows: It is there passed; Not empty, Modified since and "
                "Size warned, naming the files that break them",
                win.preview.label.text().startswith("check_inbox.results: 4 rows")
                and [r[:2] for r in results] == [["It is there", "passed"], ["Not empty", "warned"],
                                                 ["Modified since", "warned"], ["Size", "warned"]]
                and "sales_2.csv" in results[1][2] and "sales_3.csv" in results[2][2]
                and "sales_2.csv" in results[3][2])

        # --- 5. Nothing there -------------------------------------------------------------------------------------
        p.step("Change check_inbox's Path to inbox/returns_*.csv, a pattern no file matches, and press ▶ Run now.")
        type_into(field(win, "Path"), "inbox/returns_*.csv")
        run = run_now(win, qtbot, tab)
        click(tab, "check_inbox")
        click_bottom_tab(win, win.log)
        to_the_end(win)
        log = run_log(win, run)
        p.shot(win, "① check_inbox failed: it is red, and the line under it starts with why: “It is there: no file "
                    "at”, then the start of the Path. list_files did not run: it is grey, skipped.",
               highlight=[line], badges=True, zoom=line)
        p.shot(win, "② The Log says It is there failed: no file at inbox/returns_*.csv. Not empty, Modified since and "
                    "Size passed: there was no file for them to check.",
               highlight=[(win.log.text, "It is there: failed")], badges=[2],
               zoom=[log_span(win, run, "found 0 file(s)", "(Check File) failed")])
        p.check("The run failed: check_inbox failed, saying “It is there: failed: no file at inbox/returns_*.csv”; "
                "list_files was skipped",
                run.status == "failed" and tab.scene.nodes["check_inbox"].status == "failed"
                and tab.scene.nodes["check_inbox"].status_text.startswith("✕ It is there: no file at ")
                and said(log, "check_inbox", "ERROR", "It is there: failed: no file at inbox/returns_*.csv")
                and tab.scene.nodes["list_files"].status == "skipped")

        p.step("Set If none is there to warn, and press ▶ Run now.")
        pick(row_of(win, "If none is there")[1], "warn")
        run = run_now(win, qtbot, tab)
        click(tab, "check_inbox")
        click_bottom_tab(win, win.log)
        to_the_end(win)
        log = run_log(win, run)
        p.shot(win, "① check_inbox only warned, and succeeded: it is green, its files [], an empty list. list_files "
                    "ran, green, with no files: its output is [].",
               highlight=[line], badges=True, zoom=line)
        p.shot(win, "② The Log's warning: no file at inbox/returns_*.csv.",
               highlight=[(win.log.text, "It is there: warned")], badges=[2],
               zoom=[log_span(win, run, "found 0 file(s)", "Size: passed")])
        p.check("The run succeeded, with a WARNING: “It is there: warned: no file at inbox/returns_*.csv”",
                run.status == "success" and tab.scene.nodes["check_inbox"].status == "success"
                and said(log, "check_inbox", "WARNING", "It is there: warned: no file at inbox/returns_*.csv"))
        p.check("check_inbox's files and list_files' output are empty lists, [], as under them",
                tab.scene.nodes["check_inbox"].status_text.startswith("✓ files: []")
                and tab.scene.values.get("list_files", {}).get("output", "") == "[]"
                and tab.scene.nodes["list_files"].status == "success"
                and tab.scene.nodes["list_files"].status_text.startswith("✓ output: []"))
        inbox_tab = tab

        # --- 6. Waiting for a late file ----------------------------------------------------------------------------
        flow = flow_file(tmp_path / "late.yaml", {
            "start_by_hand": start(0, 40),
            "wait_for_orders": check_file(300, 20, path="inbox/orders.csv"),
            "read_orders": {"type": "data.read_file", "config": {"path": "inbox/orders.csv"},
                            "ui": {"x": 620, "y": 40}},
        }, ["start_by_hand.start -> wait_for_orders.after", "wait_for_orders.files -> read_orders.after"])
        late = ["start_by_hand", "wait_for_orders", "read_orders"]
        p.step(f"Open the flow late.yaml, click wait_for_orders and, under Retry and timeout, choose the Policy fixed, "
               f"type {ATTEMPTS} in Max attempts and {DELAY} in Delay. wait_for_orders is a Check File of "
               "inbox/orders.csv, a file not there yet, and read_orders a Read File of it; wait_for_orders is now "
               f"tried {ATTEMPTS} times in all, 5 seconds apart.")
        tab = open_flow(win, monkeypatch, flow)
        click(tab, "wait_for_orders")
        pick(row_of(win, "Policy")[1], "fixed")
        type_into(row_of(win, "Max attempts")[1], str(ATTEMPTS))
        type_into(row_of(win, "Delay")[1], DELAY)
        box = retry_box(win)
        p.shot(win, f"① wait_for_orders' Retry and timeout: fixed, {ATTEMPTS} attempts, {DELAY} between them.",
               highlight=[box], badges=True, zoom=[box, area(win, win, "Path")])
        p.check(f"wait_for_orders retries: fixed, {ATTEMPTS} attempts, {DELAY} apart",
                tab.doc.blocks["wait_for_orders"].get("retry") == {"policy": "fixed", "max_attempts": ATTEMPTS,
                                                                   "delay": DELAY})

        p.step("Press ▶ Run now. Once the Log says the first attempt failed and it waits before the second, the "
               "proof writes inbox/orders.csv, as the system that sends it would.")
        p.note("A check set to warn never retries: a warning does not fail the block, so there is nothing to try "
               "again. Here It is there fails the block, as it does unless set to warn.")
        orders = inbox / "orders.csv"
        waiting = f"waiting 5 s before attempt 2/{ATTEMPTS}"
        press_run_now(win)
        qtbot.waitUntil(lambda: waiting in log_text(win), timeout=60_000)
        arrived = not orders.exists()
        put(orders, sales(20))
        run = wait_for_run(qtbot, tab, 1)
        click(tab, "wait_for_orders")
        click_bottom_tab(win, win.log)
        to_the_end(win)
        log = run_log(win, run)
        p.shot(win, "① wait_for_orders and read_orders succeeded: both are green, and under read_orders is the table "
                    "it read, 20 rows × 4 columns.",
               highlight=[late], badges=True, zoom=late)
        p.shot(win, "② The Log: the first attempt failed, no file at inbox/orders.csv, and it waited 5 s; ③ the second "
                    "attempt found the file and every check passed.",
               highlight=[log_lines(win, f"attempt 1/{ATTEMPTS} failed", waiting),
                          log_lines(win, "found 1 file(s)", "more than 0 bytes")], badges=[2, 3],
               zoom=[log_span(win, run, "found 0 file(s)", "more than 0 bytes")])
        p.check("The run succeeded: wait_for_orders and read_orders show success",
                run.status == "success" and all(tab.scene.nodes[b].status == "success" for b in late))
        p.check("inbox/orders.csv was not there when the first attempt was made", arrived)
        p.check(f"The Log says the first attempt failed, no file at inbox/orders.csv, then “{waiting}”",
                said(log, "wait_for_orders", "ERROR", f"attempt 1/{ATTEMPTS} failed", "no file at inbox/orders.csv")
                and said(log, "wait_for_orders", "RETRY", waiting))
        after = log[next(i for i, line in enumerate(log) if waiting in line):]
        p.check("Then the second attempt passed: it found 1 file, It is there and Not empty passed, and no other "
                "attempt failed",
                said(after, "wait_for_orders", "INFO", "It is there: passed: 1 file(s) found")
                and said(after, "wait_for_orders", "INFO", "Not empty: passed")
                and not any(f"attempt {n}/{ATTEMPTS} failed" in line for line in log for n in range(2, ATTEMPTS + 1)))
        p.check("read_orders read the file: a table of 20 rows × 4 columns, as under it",
                "20 rows" in tab.scene.values.get("read_orders", {}).get("table", "")
                and tab.scene.nodes["read_orders"].status_text.startswith("✓ table: 20 rows × 4 columns"))

        # --- 7. Dark -----------------------------------------------------------------------------------------------
        p.step("Choose Tools > Settings…, set Theme to Dark and press Save. Then click the tab inbox.yaml and "
               "check_inbox.")
        set_theme(p, win, "Dark", shoot=False)
        win.tabs.setCurrentIndex(win.tabs.indexOf(inbox_tab))
        QTest.qWait(100)
        click(inbox_tab, "check_inbox")
        p.shot(panel, "① check_inbox's settings in Dark: their names in light text on the dark panel; the ticks, the "
                      "choices and Since's box dark, with light text; a line between the checks.",
               highlight=[area(panel, win, "Path on", "Size", if_it_fails(win, "Size"))], badges=True)
        p.check("The editor is dark", QApplication.palette().window().color().lightness() < 128)
        off = not_in_theme(win.properties, dark=True)
        p.check("Every part of Properties has a dark background, its text standing out from it"
                + (f" (not: {'; '.join(off)})" if off else ""), off == [])
        names = [name for name in win.properties.widget().findChildren(QLabel) if name.isVisible() and name.text()
                 in ("Path on", "Path", "If none is there", *CHECKS, "Since", "Minimum", "Maximum", "If it fails")]
        weakest = min(contrast(n.palette().color(QPalette.WindowText), n.palette().color(QPalette.Window))
                      for n in names)
        p.check(f"The names of the settings stand out from the background: {weakest:.1f} to 1 at the least (4.5 to 1 "
                "or more, the bar for text)", weakest >= TEXT)

        p.step("Set Theme back to System (Tools > Settings…) and press Save.")
        set_theme(p, win, "System", shoot=False)
        p.check("The Theme is System again, and the editor light",
                theme.chosen(win.settings) == "System"
                and QApplication.palette().window().color().lightness() >= 128)
    finally:  # no run left going; the Theme and the application's look as the proof found them
        for each in win.tabs_list():
            for r in each.runs:
                if r.running:
                    r.proc.kill()
                    r.proc.waitForFinished(5000)
        win.settings.setValue("theme", "System")
        theme.apply("System")
        if app.style().name() != as_found[0]:
            QApplication.setStyle(as_found[0])
        QApplication.setPalette(as_found[1])
        QToolTip.setPalette(as_found[2])
        QApplication.processEvents()
        clean(win)


# --- on the server of an SSH connection -----------------------------------------------------------------------------

@pytest.mark.linux_proof(NOT_ON_WINDOWS)
def test_check_file_on_the_server_of_an_ssh_connection(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Check File on the server of an SSH connection", ASKED)
    p.idea("Check File checks files on the server of one of your SSH connections just as on this computer: choose the "
           "connection in Path on, and write the Path as it is on the server, ~ being your login folder there. A "
           "pattern in the last part finds every file it matches; a folder whose name matches is not a file, and does "
           "not count.")
    p.idea("The Log lists each file found on the server with its size and when it was last changed, says which "
           "folders it left out, and the checks are made as they are on this computer.")
    p.why_simplest("It reaches the server as Copy Files does, with the same connection and the same way of reading "
                   "paths and patterns: where a file is, is said as in the Files blocks.")
    p.why_simplest("It asks the server only for what it knows of each file, through SFTP, the file transfer part of "
                   "SSH: no command is run there, so it works whatever the server's system or shell.")
    p.alternative("An SSH Command block running ls or stat on the server",
                  "Their output differs from one system to the next, and would have to be read apart by a Python "
                  "Code block in each flow. SFTP gives each file's size and time the same way everywhere.")
    p.alternative("Copying the files here first, then checking them",
                  "A large file would be copied only to learn that it is too small or too old. Check File only asks "
                  "the server what it knows of each file.")
    p.fits("Path on lists the SSH connections of Tools > Connections, as Delete Files' Path on and Copy Files' Source "
           "on and Destination on do; their passwords stay stored secrets.")
    p.fits("Its files output is the list of paths found on the server, as Copy Files gives, for the blocks after it.")
    if sys.platform == "win32":
        pytest.skip(f"Not shown on Windows. {NOT_ON_WINDOWS} CI shows this proof on Linux.")

    from tests.sshserver import PASSWORD, SSHTestServer

    server_home = tmp_path / "server"  # the server's login folder, ~ there
    server_home.mkdir()
    server = SSHTestServer(server_home).start()
    try:
        (win.home.root / "connections.yaml").write_text(server.connection(), encoding="utf-8")
        secrets = win.home.root / "secrets.yaml"
        secrets.write_text(f"appserver_password: {PASSWORD}\n", encoding="utf-8")
        secrets.chmod(0o600)
        exports = server_home / "exports"
        ago = three_days_ago()
        found = {"orders_1.csv": put(exports / "orders_1.csv", sales(150)),
                 "orders_2.csv": put(exports / "orders_2.csv", sales(80))}
        put(exports / "orders_archive" / "orders_0.csv", sales(150), ago.timestamp())  # a folder, matching orders_*
        put(exports / "returns_1.csv", sales(10))  # (not orders_*)
        flow = flow_file(tmp_path / "exports.yaml", {
            "start_by_hand": start(0, 40),
            "check_exports": check_file(300, 20, path="~/exports/orders_*", modified=True, since=SINCE),
            "list_files": python(["files"], "output = files", 620, 40),
        }, ["start_by_hand.start -> check_exports.after", "check_exports.files -> list_files.files"])
        line = ["start_by_hand", "check_exports", "list_files"]
        p.step("Open the flow exports.yaml, click check_exports and, in Properties, choose appserver in Path on. "
               f"check_exports is a Check File of ~/exports/orders_*, with Not empty and Modified since ({SINCE}) "
               "ticked.")
        p.note("appserver is a small SSH server the proof starts on this computer, the one Taskloom's own tests use, "
               "added to Tools > Connections as the tests add it, its password a stored secret in the proof's own "
               "Taskloom folder. In its exports folder: orders_1.csv, orders_2.csv, returns_1.csv, and a folder, "
               "orders_archive, with older orders in it.")
        tab = open_flow(win, monkeypatch, flow)
        click(tab, "check_exports")
        pick(row_of(win, "Path on")[1].findChild(QComboBox), "appserver")
        p.shot(win, "① Path on: appserver, one of your SSH connections; the Path, ~/exports/orders_*, is on that "
                    "server.", highlight=[area(win, win, "Path on", "Path")], badges=True,
               zoom=[area(win, win, "Path on", "Path"), "check_exports"])
        p.check("check_exports' Path on is appserver",
                tab.doc.blocks["check_exports"]["config"].get("server") == "appserver"
                and row_of(win, "Path on")[1].findChild(QComboBox).currentText() == "appserver")

        p.step("Press ▶ Run now.")
        p.note("On a server, Modified since compares with the time the server's file system keeps for each file, "
               "written by the server's clock: a server whose clock is wrong gives wrong times.")
        win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)
        run = run_now(win, qtbot, tab)
        click(tab, "check_exports")
        click_bottom_tab(win, win.log)
        to_the_end(win)
        log = run_log(win, run)
        p.shot(win, "① check_exports succeeded, and list_files ran: both are green. Under check_exports, its files, "
                    "the list of the files it found on appserver; under list_files, its output, the list it got.",
               highlight=[line], badges=True, zoom=line)
        p.shot(win, "② The Log: it found 2 files at ~/exports/orders_* on appserver, and says the folder "
                    "orders_archive, which the pattern matches too, does not count; each file with its size and when "
                    "it was last changed; every check passed.",
               highlight=[(win.log.text, "found 2 file(s)")], badges=[2],
               zoom=[log_span(win, run, "found 2 file(s)", "Modified since: passed")])
        p.check("The run succeeded: check_exports and list_files show success, under them the list of files, "
                "starting with orders_1.csv",
                run.status == "success" and all(tab.scene.nodes[b].status == "success" for b in line)
                and re.match(r"✓ files: \[\S*orders_1\.csv", tab.scene.nodes["check_exports"].status_text)
                and re.match(r"✓ output: \[\S*orders_1\.csv", tab.scene.nodes["list_files"].status_text))
        p.check("The Log says it found 2 files at ~/exports/orders_* on appserver, and that the folder orders_archive "
                "does not count",
                said(log, "check_exports", "INFO", "found 2 file(s) at ~/exports/orders_* on appserver",
                     "1 folder(s) there, which do not count", "orders_archive"))
        p.check("The Log lists orders_1.csv and orders_2.csv on appserver, each with its size and when it was last "
                "changed",
                all(lists(log, "check_exports", f"{name} on appserver", path) for name, path in found.items()))
        p.check("It is there, Not empty and Modified since passed",
                all(said(log, "check_exports", "INFO", f"{c}: passed") for c in ("It is there", "Not empty",
                                                                                  "Modified since")))
        got = tab.scene.values.get("list_files", {}).get("output", "")
        p.check("list_files got orders_1.csv and orders_2.csv, and not the folder orders_archive",
                "orders_1.csv" in got and "orders_2.csv" in got and "orders_archive" not in got)
    finally:  # (the editor stops any run still going as the proof ends)
        server.stop()
        clean(win)
