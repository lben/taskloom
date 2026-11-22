"""Proof: source diffs in Commit History (docs/promises/source-diff.yaml, CD-1..7, agreed 2026-11-20). Right-click a
commit in Commit History: "Show source diff against previous version" and "Show source diff against current version"
open a window on top with two versions of the flow file side by side, the changes marked by colour, and Next change
(F7) and Previous change (Shift+F7) jump between them. How proofs are written: README.md here. The flow's commits are
made with the real git program, as any Git tool makes them, and what Git holds is read with it."""

import os

import yaml
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QToolButton, QToolTip

from proofs.conftest import choose, menu_item
from proofs.test_proof_docs_help import click_block, open_flow
from proofs.test_proof_settings_wording import type_in
from proofs.test_proof_tab_plus import set_theme
from taskloom.editor import theme
from tests.test_commit_history import local, make_history
from tests.test_paths_editor_editing import clean, field
from tests.test_save_and_commit import GIT, git
from tests.test_theme import not_in_theme

ASKED = ("Add right click to commit on list and allow: 'Show source diff against previous version' and 'Show source "
         "diff against current version' which should show up a diff window on top that behaves like the jetbrains "
         "with a side by side view of the underlying source file of the flow")
PREVIOUS = "Show source diff against previous version"
CURRENT = "Show source diff against current version"
COMMIT_HISTORY = "Commit History…"
FLOW = "daily-sales-report.yaml"
SUM = "select region, sum(amount) from sales group by 1"
ROUNDED = "select region, round(sum(amount), 2) from sales group by 1"
SAVED_TO = "out/sales_{{day:%Y-%m-%d}}.csv"
SAVE_TO_NOW = "out/sales_{{day:%Y-%m-%d}}_by_region.csv"  # typed in the tab, not saved
# The flow's commits, oldest first: (message, author date, what that version holds).
COMMITS = [("Report the previous business day per region", "2026-11-10T09:41:07+01:00",
            dict(description=True, region="EU", retry=None, sql=SUM)),
           ("Retry reading the sales file; region EMEA", "2026-11-12T16:05:33+01:00",
            dict(description=True, region="EMEA", retry="5m", sql=SUM)),
           ("Keep the totals to the cent", "2026-11-17T08:12:48+01:00",
            dict(description=False, region="EMEA", retry="10m", sql=ROUNDED))]


def flow(description: bool, region: str, retry: str | None, sql: str, path: str = SAVED_TO) -> dict:
    """A version of the daily sales report: read the sales file, total them per region and save the totals; and
    archive the list of the stores still open. Long enough that the window scrolls to show its last changes."""
    data = {"taskloom": 1, "name": "daily-sales-report"}
    if description:
        data["description"] = "Totals per region for the previous business day, saved as CSV."
    data["params"] = {"day": {"type": "date", "expr": "today() - bdays(1)"},
                      "region": {"type": "text", "value": region}}
    sales = {"type": "data.read_file", "config": {"path": "data/sales.csv"}}
    if retry:
        sales["retry"] = {"policy": "fixed", "delay": retry, "max_attempts": 3}
    sales["ui"] = {"x": 0, "y": 0}
    data["blocks"] = {
        "manual": {"type": "trigger.manual", "ui": {"x": -260, "y": 75}},
        "sales": sales,
        "stores": {"type": "data.read_file", "config": {"path": "data/stores.csv"}, "ui": {"x": 0, "y": 150}},
        "open_stores": {"type": "data.duckdb_sql",
                        "config": {"inputs": ["stores"], "sql": "select * from stores where closed is null"},
                        "ui": {"x": 260, "y": 150}},
        "archive": {"type": "data.write_file", "config": {"path": "archive/open_stores_{{day:%Y-%m-%d}}.parquet"},
                    "ui": {"x": 520, "y": 150}},
        "totals": {"type": "data.duckdb_sql", "config": {"inputs": ["sales"], "sql": sql}, "ui": {"x": 260, "y": 0}},
        "save": {"type": "data.write_file", "config": {"path": path}, "ui": {"x": 520, "y": 0}}}
    data["edges"] = ["manual.start -> sales.after", "manual.start -> stores.after",
                     "stores.table -> open_stores.stores", "open_stores.result -> archive.table",
                     "sales.table -> totals.sales", "totals.result -> save.table"]
    return data


def as_saved(data: dict) -> str:
    """The text of a flow file holding `data`, as File > Save writes it (docs/guide.md: YAML, keys in their order)."""
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def settle():
    for _ in range(5):
        QApplication.processEvents()


def right_click_card(history, row: int):
    """Right-click the card `row` (from the top) of Commit History's list, as you would: its menu opens, and waits
    for a choice (see Proof.while_open)."""
    view = history.list
    item = view.item(row)
    view.scrollToItem(item)
    settle()
    viewport = view.viewport()
    at = view.visualItemRect(item).center()
    where = viewport.mapToGlobal(at)
    for kind, buttons in ((QEvent.MouseButtonPress, Qt.RightButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
        QApplication.sendEvent(viewport, QMouseEvent(kind, QPointF(at), QPointF(where), Qt.RightButton, buttons,
                                                     Qt.NoModifier))
    QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, at, where))


def diff_windows() -> list:
    return [w for w in QApplication.topLevelWidgets() if w.objectName() == "source-diff" and w.isVisible()]


def open_diff(p, history, row: int, item: str, caption: str, highlight) -> tuple:
    """Right-click the card `row`, shoot its menu over the window with `highlight(menu)` outlined, and click `item`:
    the source diff window that opens, and what the menu offered ({item: enabled})."""
    before, offered = set(diff_windows()), {}

    def look(menu):
        offered.update({a.text(): a.isEnabled() for a in menu.actions() if a.text()})
        p.shot(menu, caption, over=history, highlight=highlight(menu), badges=True, zoom=[menu])
        choose(menu, item)

    p.while_open(lambda: right_click_card(history, row), look)
    settle()
    opened = [w for w in diff_windows() if w not in before]
    assert len(opened) == 1, f"{item!r} opened {len(opened)} windows"
    return opened[0], offered


def part(diff, kind, name: str):
    return diff.findChild(kind, name)


def side_lines(side) -> list[str]:
    """The lines the side shows, without its filler lines (empty: the versions here have no empty line)."""
    return [line for line in side.toPlainText().split("\n") if line]


def row_of(side, text: str) -> int:
    """The row (from 0) of the side whose line holds `text`."""
    block = side.document().begin()
    while block.isValid():
        if text in block.text():
            return block.blockNumber()
        block = block.next()
    raise AssertionError(f"{side.objectName()} shows no line with {text!r}")


def row_rect(side, row: int) -> QRect:
    """Where the side's viewport shows row `row`, across its width."""
    block = side.document().findBlockByNumber(row)
    r = side.blockBoundingGeometry(block).translated(side.contentOffset()).toAlignedRect()
    return QRect(0, r.top(), side.viewport().width(), r.height())


def in_view(side, row: int) -> bool:
    r = row_rect(side, row)
    return r.top() >= 0 and r.bottom() < side.viewport().height()


def rows(diff, side, first: int, last: int) -> QRect:
    """Rows `first` to `last` of a side, with its line numbers, in the window's pixels."""
    top, bottom = row_rect(side, first), row_rect(side, last)
    origin = side.viewport().mapTo(diff, QPoint(0, 0))
    left = side.mapTo(diff, QPoint(0, 0)).x()
    return QRect(left, origin.y() + top.top(), origin.x() - left + side.viewport().width(),
                 bottom.bottom() - top.top() + 1)


def change(diff, row: int) -> list:
    """Row `row` of both sides, to outline close around their text (a filler line: across its side)."""
    return [(side, text) if (text := side.document().findBlockByNumber(row).text()).strip()
            else rows(diff, side, row, row) for side in (diff.left, diff.right)]


def close_up(diff, first: int, last: int, right_text: int = 175) -> QRect:
    """Rows `first` to `last`, two more above and below, of the left side and of the first `right_text` pixels of
    the right one (with the outline's badge): a part of the window small enough to be shown bigger, in the window's
    pixels."""
    first, last = max(0, first - 2), min(diff.left.document().blockCount() - 1, last + 2)
    while not in_view(diff.left, first):
        first += 1
    while not in_view(diff.left, last):
        last -= 1
    area = rows(diff, diff.left, first, last)
    right = diff.right.viewport().mapTo(diff, QPoint(0, 0)).x()
    return QRect(area.left(), area.top(), right - area.left() + right_text, area.height())


def headers(diff) -> list[QLabel]:
    """The line over each side naming its version: left, right."""
    found = [label for label in diff.findChildren(QLabel) if label.isVisible() and label.objectName() == ""
             and label.text()]
    return sorted(found, key=lambda label: label.mapTo(diff, QPoint(0, 0)).x())


def press(qtbot, diff, key, modifiers=Qt.NoModifier):
    """Press a key in the window, the keyboard focus in its right side (as after a click there)."""
    diff.raise_()
    diff.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is diff, timeout=5_000)
    diff.right.setFocus()
    qtbot.waitUntil(diff.right.hasFocus, timeout=5_000)
    QTest.keyClick(diff.right, key, modifiers)
    settle()


def level(diff) -> bool:
    """The two sides have as many rows, scrolled as far: each row is level with the other side's."""
    return (diff.left.document().blockCount() == diff.right.document().blockCount()
            and diff.left.verticalScrollBar().value() == diff.right.verticalScrollBar().value())


def test_source_diffs_against_the_previous_and_the_current_version(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Source diffs in Commit History: a commit against the version before it, or against the current one",
              ASKED)
    p.idea("In Commit History, right-click a commit. “Show source diff against previous version” compares it with "
           "the commit before it in the flow's history; “Show source diff against current version” compares it with "
           "what the flow's tab holds now, changes not saved included. Each opens a window of its own on top, with "
           "the flow file's text side by side: the older version on the left, the newer on the right, each side "
           "numbering its own lines.")
    p.idea("Removed lines are red, added lines green and changed lines blue, and inside a changed line the characters "
           "that changed are a darker blue. Where one side has lines the other has not, the other side has hatched "
           "filler lines with no number, so the lines that did not change stay level; both sides scroll together. "
           "Next change (F7) and Previous change (Shift+F7) jump from change to change, and the count beside them "
           "says which one you are at. Nothing can be typed in either side.")
    p.idea("The window's title names both versions: a commit by its message and time, or “current”, or “current "
           "(not saved)” while the tab holds changes not saved; the line over each side says the same, with the "
           "commit's short id. On the oldest commit there is no version before it: that item is greyed out. "
           "Looking changes nothing: no file, no commit, no Git setting.")
    p.idea("When the two versions differ in their line ends (Windows or Unix) or in the newline at the end, which the "
           "lines cannot show, a line above the sides says so; when they are the same, the window says “No "
           "differences”.")
    p.why_simplest("The lines are compared with Python's own difflib, which comes with Python: no new package and no "
                   "Git program. A changed line's characters are compared the same way, so only those that changed "
                   "are marked. Its tests compare what it marks with git diff on ten real pairs of commits: the same "
                   "lines, none more, none fewer.")
    p.why_simplest("Each side is the editor's own code box, made read-only: its line numbers, its find bar (Ctrl+F) "
                   "and its zoom (Ctrl + and Ctrl -) come with it, and the YAML is coloured as the code boxes colour "
                   "code. A filler line is an empty line of that box, so the two sides have as many lines and one "
                   "scroll bar moves both.")
    p.why_simplest("Both versions are text Taskloom already has: each commit's file, which Commit History read from "
                   "Git for its cards, and the flow file, or with changes not saved what File > Save would write. "
                   "Nothing is asked of Git again, and nothing is written.")
    p.alternative("One column of + and - lines, as git diff prints it",
                  "You asked for a side-by-side view like JetBrains'. One column also puts the old and the new form "
                  "of a changed line apart, one under the other, where the characters that changed are harder to "
                  "see.")
    p.alternative("The flow file on disk as the current version",
                  "Agreed on 2026-11-20: the tab's text, so you can see your changes before you save and commit them; "
                  "the title then says “current (not saved)”. With nothing unsaved the two are the same.")
    p.alternative("The code's colours kept as they are on every mark",
                  "On the blue of the changed characters some of them read at under 3:1. They are shaded in their "
                  "own hue just enough to read at 4.5:1 there, and only on the marks: elsewhere they are the code "
                  "boxes' colours.")
    p.fits("It is in Commit History's right-click menu, beside where you choose a version to preview or restore; "
           "the cards, the search and Restore this version are unchanged.")
    p.fits("The window is not modal: the editor and Commit History stay usable while it is open, and several can be "
           "open at once. It follows the Theme setting, light or dark, as the rest of the editor does.")
    assert GIT, "this proof makes the flow's commits with the git program, which is not installed"
    # The proof's own global Git config, empty, and no system one: the user's Git settings are never read or written.
    git_config = tmp_path / "gitconfig"
    git_config.write_bytes(b"")
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings writes nothing real)
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    folder = tmp_path / "sales"
    versions = [flow(**holds) for _, _, holds in COMMITS]
    texts = [as_saved(v) for v in versions]
    ids = make_history(folder, [(message, {FLOW: text.encode("utf-8")}, when)
                                for (message, when, _), text in zip(COMMITS, texts)])
    path = folder / FLOW
    when = [local(w) for _, w, _ in COMMITS]
    history = None
    try:
        # --- the flow and its history ---------------------------------------------------------------------------
        p.step(f"Open {FLOW} (File > Open…) and choose File > Commit History…. The flow has three commits: "
               f"“{COMMITS[0][0]}”, then “{COMMITS[1][0]}”, then “{COMMITS[2][0]}”.")
        p.note("The three commits were made beforehand with the git program, each with its own date; Save & Commit "
               "makes such commits from the editor, and has a proof of its own.")
        tab = open_flow(win, monkeypatch, path)
        p.check("The flow opens, with no problem to tell", tab is not None and tab.doc.path == path
                and not tab.doc.dirty and win.message_boxes == [])
        file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
        menu_item(file_menu, COMMIT_HISTORY).trigger()
        qtbot.waitUntil(lambda: tab in win.histories and win.histories[tab].isVisible(), timeout=10_000)
        history = win.histories[tab]
        settle()
        p.check("Commit History lists the three commits, newest first",
                [v.id for v in history.versions] == list(reversed(ids)) and history.list.count() == 3)
        status = git(folder, "status", "--porcelain")
        before = path.read_bytes()

        # --- against the previous version ---------------------------------------------------------------------
        p.step(f"Right-click the newest commit, “{COMMITS[2][0]}”, and choose {PREVIOUS}.")
        diff, offered = open_diff(
            p, history, 0, PREVIOUS,
            "① Right-click a commit: its menu has the two source diffs. The newest commit has a version before it, "
            "so both can be chosen.",
            lambda menu: [[menu_item(menu, PREVIOUS), menu_item(menu, CURRENT)]])
        p.check("① The menu offers both source diffs, neither greyed out (CD-1)",
                offered.get(PREVIOUS) is True and offered.get(CURRENT) is True)
        title = f'{FLOW}:  "{COMMITS[1][0]}", {when[1]}  ↔  "{COMMITS[2][0]}", {when[2]}'
        left, right = headers(diff)
        count = part(diff, QLabel, "change-count")
        previous, next_ = part(diff, QToolButton, "previous-change"), part(diff, QToolButton, "next-change")
        removed = row_of(diff.left, "description:")
        p.shot(diff, "The window that opens, on top. ① Over each side, its version: the commit before on the left, "
                     "the one you right-clicked on the right, each by its message, time and short id. ② “1 of 3 "
                     "changes”: the window opens at the first change, so Previous change is greyed out. ③ The first "
                     "change: the description line, removed (red) on the left, a hatched filler line with no number "
                     "on the right. Below it the lines stay level: line 4 on the left beside line 3 on the right.",
               highlight=[[left, right], [previous, next_, count], change(diff, removed)], badges=True,
               zoom=close_up(diff, removed, removed))
        p.note(f"These screenshots draw no title bar: the window's title is “{title}”, which the checks read.")
        p.check("The window is a window of its own, on top and not modal: the editor stays usable (CD-1)",
                diff.isWindow() and not diff.isModal() and QApplication.activeModalWidget() is None
                and QApplication.activeWindow() is diff)
        p.check("The title names both commits by their message and time, the older first (CD-3)",
                diff.windowTitle() == title)
        p.check("The left side shows the version before, the right side the commit right-clicked, line for line "
                "(CD-1)", side_lines(diff.left) == texts[1].splitlines()
                and side_lines(diff.right) == texts[2].splitlines())
        p.check("② “1 of 3 changes”, Previous change greyed out and Next change not (CD-2)",
                count.text() == "1 of 3 changes" and not previous.isEnabled() and next_.isEnabled())
        p.check("③ The first change is in view on both sides, and the rows are level (CD-2)",
                in_view(diff.left, removed) and in_view(diff.right, removed) and level(diff))

        p.step("Press F7 (Next change).")
        press(qtbot, diff, Qt.Key_F7)
        delay = row_of(diff.right, "delay:")
        p.shot(diff, "① “2 of 3 changes”, and both buttons can be pressed now. ② The second change, in view on both "
                     "sides: the retry's delay, 5m on the left and 10m on the right; the line is blue, and the "
                     "characters that changed are a darker blue.",
               highlight=[[previous, next_, count], change(diff, delay)], badges=True,
               zoom=close_up(diff, delay, delay))
        p.check("① “2 of 3 changes”, both buttons enabled (CD-2)",
                count.text() == "2 of 3 changes" and previous.isEnabled() and next_.isEnabled())
        p.check("② The delay line is in view on both sides, level with each other (CD-2)",
                row_of(diff.left, "delay: 5m") == delay and in_view(diff.left, delay) and in_view(diff.right, delay)
                and level(diff))

        p.step("Press F7 again, and once more.")
        press(qtbot, diff, Qt.Key_F7)
        sql = row_of(diff.right, "sql: select region")
        top = diff.right.verticalScrollBar().value()
        p.shot(diff, "① “3 of 3 changes”: the last, so Next change is greyed out. ② Both sides scrolled down together "
                     "to it, still level: the SQL line, where sum(amount) became round(sum(amount), 2); only round( "
                     "and , 2) are the darker blue.",
               highlight=[[previous, next_, count], change(diff, sql)], badges=True, zoom=close_up(diff, sql, sql))
        at_last = count.text()
        press(qtbot, diff, Qt.Key_F7)
        p.check("① “3 of 3 changes”, Next change greyed out; F7 at the last change stays there (CD-2)",
                at_last == count.text() == "3 of 3 changes" and previous.isEnabled() and not next_.isEnabled())
        p.check("② The sides scrolled to the SQL line, which is in view on both, level (CD-2)",
                top > 0 and in_view(diff.left, sql) and in_view(diff.right, sql) and level(diff))

        p.step("Press Shift+F7 (Previous change), then click Previous change.")
        press(qtbot, diff, Qt.Key_F7, Qt.ShiftModifier)
        back = count.text()
        QTest.mouseClick(previous, Qt.LeftButton)
        settle()
        p.check("Shift+F7 goes back to “2 of 3 changes”, and Previous change to “1 of 3 changes”, the first change "
                "in view again (CD-2)", back == "2 of 3 changes" and count.text() == "1 of 3 changes"
                and in_view(diff.right, removed) and level(diff))
        p.check("Nothing typed or jumped changed either side's text (CD-2)",
                side_lines(diff.left) == texts[1].splitlines() and side_lines(diff.right) == texts[2].splitlines())

        # --- against the current version, with a change not saved ----------------------------------------------
        p.step(f"In the editor, click the save block and change its Path to {SAVE_TO_NOW}, without saving. Then, in "
               f"Commit History, right-click the oldest commit, “{COMMITS[0][0]}”, and choose {CURRENT}.")
        click_block(win, "save")
        type_in(field(win, "Path"), SAVE_TO_NOW)
        p.check("The tab holds a change not saved: its Path", tab.doc.dirty
                and tab.doc.blocks["save"]["config"]["path"] == SAVE_TO_NOW and path.read_bytes() == before)
        diff_2, offered = open_diff(
            p, history, 2, CURRENT,
            "① The oldest commit has no version before it in the flow's history: “against previous version” is "
            "greyed out. ② “against current version” can be chosen.",
            lambda menu: [menu_item(menu, PREVIOUS), menu_item(menu, CURRENT)])
        p.check("① On the oldest commit, “against previous version” is greyed out; ② “against current version” is "
                "not (CD-4)", offered.get(PREVIOUS) is False and offered.get(CURRENT) is True)
        now = as_saved(tab.doc.data)
        title_2 = f'{FLOW}:  "{COMMITS[0][0]}", {when[0]}  ↔  current (not saved)'
        left_2, right_2 = headers(diff_2)
        count_2 = part(diff_2, QLabel, "change-count")
        next_2 = part(diff_2, QToolButton, "next-change")
        p.shot(diff_2, "① On the right, “Current (not saved)”: what the tab holds now, your change of Path "
                       "included. ② “1 of 5 changes”: since the oldest commit, the description was removed, the "
                       "region changed, a retry was added, the SQL changed, and the Path you just typed.",
               highlight=[right_2, count_2], badges=True, zoom=right_2)
        p.check(f"The title names the oldest commit and “current (not saved)” (CD-3): “{title_2}”",
                diff_2.windowTitle() == title_2 and right_2.text() == "Current (not saved)")
        p.check("The left side is the oldest commit, the right side what File > Save would write now, line for line "
                "(CD-1, CD-3)", side_lines(diff_2.left) == texts[0].splitlines()
                and side_lines(diff_2.right) == now.splitlines())
        p.check("② “1 of 5 changes” (CD-2)", count_2.text() == "1 of 5 changes")

        p.step("Press F7 four times, to the last change.")
        for _ in range(4):
            press(qtbot, diff_2, Qt.Key_F7)
        path_row = row_of(diff_2.right, "path: out/")
        p.shot(diff_2, "① “5 of 5 changes”, Next change greyed out. ② The last change is the one not saved: the save "
                       "block's Path, …csv on the left and …_by_region.csv on the right.",
               highlight=[[part(diff_2, QToolButton, "previous-change"), next_2, count_2],
                          change(diff_2, path_row)], badges=True,
               zoom=close_up(diff_2, path_row, path_row, 395))
        p.check("① “5 of 5 changes”, Next change greyed out (CD-2)",
                count_2.text() == "5 of 5 changes" and not next_2.isEnabled())
        p.check("② The Path line is in view on both sides, level: the change not saved is shown (CD-2, CD-3)",
                SAVE_TO_NOW in diff_2.right.document().findBlockByNumber(path_row).text()
                and SAVED_TO in diff_2.left.document().findBlockByNumber(path_row).text()
                and in_view(diff_2.left, path_row) and in_view(diff_2.right, path_row) and level(diff_2))

        p.step("Choose Tools > Settings…, set Theme to Dark and press Save. The window is still open.")
        set_theme(p, win, "Dark", shoot=False)
        settle()
        p.shot(diff_2, "In Dark, the window and both sides are dark; the changes keep their colours, darker, and the "
                       "text on them reads well.", zoom=close_up(diff_2, path_row, path_row, 395))
        p.check("In Dark, the window and both sides are dark (CD-2)", not not_in_theme(diff_2, dark=True))
        p.step("Set Theme back to System (Tools > Settings…), and press Save.")
        set_theme(p, win, "System", shoot=False)

        # --- looking changed nothing ---------------------------------------------------------------------------
        p.check("Looking changed nothing: the flow file holds the same bytes, git status and the last commit are "
                "as before, and the Git config is still empty (CD-5)",
                path.read_bytes() == before and git(folder, "status", "--porcelain") == status
                and git(folder, "rev-parse", "HEAD").strip() == ids[-1] and git_config.read_bytes() == b"")
        p.check("The tab's change is still not saved (CD-5)",
                tab.doc.dirty and tab.doc.blocks["save"]["config"]["path"] == SAVE_TO_NOW)
    finally:  # the Theme and the application's look as the proof found them; no window left open
        for window in diff_windows():
            window.close()
        if history is not None and history.isVisible():
            history.close()
        clean(win)
        win.settings.setValue("theme", "System")
        theme.apply("System")
        if app.style().name() != as_found[0]:
            QApplication.setStyle(as_found[0])
        QApplication.setPalette(as_found[1])
        QToolTip.setPalette(as_found[2])
        QApplication.processEvents()
