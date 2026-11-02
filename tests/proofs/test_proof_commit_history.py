"""Proof: File > Commit History… searches a flow's commits (message, outputs, code, whole file), moves through them
with Up and Down, previews each version with the outputs of its latest run on hover, and restores one to keep working
on it (stage 5 of the 2026-10-30 feedback). How proofs are written: README.md here. What Taskloom wrote in Git is read
with the real git program, as you would with any Git tool."""

import html
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import yaml
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton, QToolBar, QToolButton, QToolTip

from proofs.conftest import choose, menu_item
from proofs.test_proof_tab_plus import set_theme
from taskloom.editor import theme
from taskloom.editor.canvas import HEADER_H, NODE_W
from tests.test_find_bar import red_pixels
from tests.test_reusable_block import text_box
from tests.test_save_and_commit import GIT, count, fill, git, git_bytes, lines, shown, snapshot
from tests.test_theme import is_dark, most_common, not_in_theme

ASKED = ("Lets add \"Commit History\" under Save & Commit... The idea is for it to open a new window with 2 panes, on "
         "the top left pane a search bar (with regex and case sensitive buttons to the right) that will allow me to "
         "search first by commit message and then by code outputs (is possible/available), then by code strings "
         "(Python, SQL, etc) and finally by anything withing the flow yaml file Under that search bar by default it "
         "shows up all the commits for that flow and the search filters those commits. Each commit shows the date "
         "and time of the commit on the bottom right of its card in the results. I should be able to navigate "
         "through the commits or search result commits using the up and down arrow keys On the panel of the right a "
         "read only preview of the flow of the selected commit shows up, ideally showing the outputs of each block "
         "of its latest run so that if on that panel I hover the mouse on top of the outputs I can see them. I "
         "should be able to restore any previous commit and work over it normally. I of course should be able to "
         "create new saves and new commits out of a restored version.")
SAVE_AND_COMMIT = "Save && Commit…"  # the File menu item: Qt shows it as "Save & Commit…"
COMMIT_HISTORY = "Commit History…"
NAME, EMAIL = "Ana Lima", "ana@example.com"
FIRST = "Load the sales of each region"
SECOND = "Add the total\n\nNot run yet: to be checked tomorrow."
THIRD = "Show the count with the total"
FOURTH = "Back to the first version"
SUBJECTS = {"v1": FIRST, "v2": SECOND.split("\n")[0], "v3": THIRD, "v4": FOURTH}  # each commit's first line
NO_RUN = "No run was made with exactly this version"
TEXT_V1 = "Weekly sales by region"
FLOW = f"""\
name: weekly-sales
blocks:
  start_by_hand:
    type: trigger.manual
    ui: {{x: 0, y: 0}}
  load_sales:
    type: logic.python
    config:
      inputs: []
      code: 'output = {{"North": 120, "South": 80, "West": 45}}'
    ui: {{x: 260, y: 0}}
edges:
  - start_by_hand.start -> load_sales.after
notes:
  note_1: {{text: {TEXT_V1}, x: 0, y: -90, style: heading}}
"""
TOTAL_V2 = "# Add up every region\noutput = sum(input.values())"
TOTAL_V3 = "# Add up every region\ntotal = sum(input.values())\noutput = f\"{total} sold in {len(input)} regions\""
TEXT_V3 = "Weekly sales by region, checked on Mondays"
COUNTED = "245 sold in 3 regions"  # what total_sales makes in the third version: 120 + 80 + 45, in 3 regions


# --- helpers -------------------------------------------------------------------------------------------------------

@pytest.fixture
def base():
    """A new folder with a short path, as Save & Commit's window shows it whole; removed after."""
    folder = Path(tempfile.mkdtemp(prefix="tl-")).resolve()
    yield folder
    # (Git keeps its objects read-only, which Windows does not delete.)
    shutil.rmtree(folder, onexc=lambda remove, path, _: (os.chmod(path, stat.S_IWRITE), remove(path)))


def settle():
    for _ in range(5):
        QApplication.processEvents()


def in_a_repository(folder) -> bool:
    """Whether git finds a repository for `folder` (in it or a folder above)."""
    return subprocess.run([GIT, "-C", str(folder), "rev-parse", "--git-dir"], capture_output=True,
                          timeout=60).returncode == 0


def file_menu(win):
    return next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")


def edit_menu(win):
    return next(a.menu() for a in win.menuBar().actions() if a.text() == "&Edit")


def save_and_commit(p, win, message: str, name=None, email=None, shoot: str = ""):
    """File > Save & Commit…: type `message` (and the name and email, if given) and click Save & Commit."""
    def in_the_window(window):
        fill(window, name=name, email=email, message=message)
        if shoot:
            p.shot(window, shoot, highlight=[[text_box(window, "Name"), text_box(window, "Email")],
                                             text_box(window, "Message")], badges=True)
        (button,) = [b for b in window.findChildren(QPushButton) if b.isVisible() and shown(b.text()) == "Save & Commit"]
        QTest.mouseClick(button, Qt.LeftButton)

    p.while_open(lambda: menu_item(file_menu(win), SAVE_AND_COMMIT).trigger(), in_the_window)
    settle()


def run_now(win, qtbot, tab, n: int):
    """Press ▶ Run now in the toolbar and wait until the tab's n-th run has ended: that run."""
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == n and not tab.running and tab.runs[-1].status is not None,
                    timeout=120_000)
    return tab.runs[-1]


def a_second_later(qtbot, folder):
    """Wait until the clock is a second past the last commit: each commit has a time of its own on its card."""
    made = int(git(folder, "log", "-1", "--format=%at"))
    qtbot.waitUntil(lambda: time.time() >= made + 1, timeout=5_000)


def cards(window) -> list[str]:
    """What each card holds, top to bottom: its message, where the search found it, its date and time and its id."""
    return [window.list.item(r).text() for r in range(window.list.count())]


def which(window, ids: dict) -> list[str]:
    """Which commit each card is, top to bottom (v1, v2… as `ids` names them), by the short id it shows."""
    found = []
    for text in cards(window):
        names = [name for name, commit in ids.items() if text.rsplit("\n", 1)[-1].endswith(" " + commit[:7])]
        assert len(names) <= 1, f"the card {text!r} shows the short id of {names}"
        found += names or ["?"]
    return found


def where(window) -> list[str]:
    """Where the search found each card's commit, as the card says it."""
    return [text.split("\n")[-2] for text in cards(window)]


def named(found: list[str]) -> str:
    """The commits `found` (v1, v2…), by their first lines: “Add the total” and “Load the sales of each region”."""
    return " and ".join(f"“{SUBJECTS[v]}”" for v in found)


def selected(window) -> int | None:
    rows = [window.list.row(i) for i in window.list.selectedItems()]
    return rows[0] if len(rows) == 1 else None


def card(window, row: int) -> QRect:
    """Where the card on row `row` is, in a screenshot of the window, inside its edges: its outline then lies on them,
    clear of the cards next to it."""
    view = window.list
    rect = view.visualItemRect(view.item(row)).adjusted(5, 5, -5, -5)
    return rect.translated(view.viewport().mapTo(window, QPoint(0, 0)))


def all_cards(window) -> list[QRect]:
    """The cards shown, top to bottom (for a highlight or a close-up)."""
    return [card(window, r) for r in range(window.list.count())]


def click_card(window, row: int):
    """Click the card on row `row`, as you would."""
    item = window.list.item(row)
    window.list.scrollToItem(item)
    settle()
    QTest.mouseClick(window.list.viewport(), Qt.LeftButton, Qt.NoModifier, window.list.visualItemRect(item).center())
    settle()


def activate(qtbot, window):
    """As when you click into a window: it is the active one, the keys you press go to it."""
    window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window, timeout=5_000)


def type_in(window, text: str):
    """Click the search field and type `text` over what it holds (nothing: empty it)."""
    field = window.field
    QTest.mouseClick(field, Qt.LeftButton)
    field.selectAll()
    if text:
        QTest.keyClicks(field, text)
    else:
        QTest.keyClick(field, Qt.Key_Backspace)
    settle()


def toggle(window, text: str) -> QToolButton:
    (button,) = [b for b in window.findChildren(QToolButton) if b.isVisible() and b.text() == text]
    return button


def drawn(scene) -> dict:
    """What a canvas or the preview draws: each block's place and the line under it, and the texts."""
    return {"blocks": {b: (round(n.pos().x()), round(n.pos().y()), n.status, n.status_text)
                       for b, n in scene.nodes.items()},
            "texts": sorted(n.toPlainText() for n in scene.note_items.values())}


def in_preview(window, *blocks) -> QRect:
    """Where `blocks` are drawn in the preview, in a screenshot of the window."""
    view, rect = window.preview, QRect()
    for block in blocks:
        rect |= view.mapFromScene(view.scene().nodes[block].sceneBoundingRect()).boundingRect()
    return rect.translated(view.viewport().mapTo(window, QPoint(0, 0)))


def tooltip():
    return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)


def hover(qtbot, view, block: str):
    """Rest the mouse on the line under `block` (its result) in `view`, as you would: the tooltip that shows."""
    QToolTip.hideText()
    qtbot.waitUntil(lambda: tooltip() is None, timeout=5_000)
    footer = view.scene().nodes[block].footer
    viewport = view.viewport()
    at = view.mapFromScene(footer.sceneBoundingRect().center())
    assert viewport.rect().contains(at), f"the line under {block} is out of view"
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, at, viewport.mapToGlobal(at)))
    qtbot.waitUntil(lambda: tooltip() is not None and tooltip().text() == footer.toolTip(), timeout=5_000)
    QTest.qWait(100)
    return tooltip()


def plain(text: str) -> str:
    """A tooltip's text as it reads: its lines joined by spaces."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def status_message(win) -> QRect:
    """Where the status bar's message is, in the window."""
    bar = win.statusBar()
    return QRect(bar.mapTo(win, QPoint(0, 0)),
                 QSize(bar.fontMetrics().horizontalAdvance(bar.currentMessage()) + 16, bar.height()))


def canvas_text(win, tab) -> QRect:
    """Where the text on the canvas is, in a screenshot of the editor."""
    item = tab.scene.note_items["note_1"]
    return tab.view.mapFromScene(item.sceneBoundingRect()).boundingRect().translated(
        tab.view.viewport().mapTo(win, QPoint(0, 0)))


def run_line(window) -> QRect:
    """Where the words of the line above the preview are, in a screenshot of the window."""
    label = window.run_line
    width = label.fontMetrics().horizontalAdvance(label.text()) + 8
    return QRect(label.mapTo(window, QPoint(0, 0)), QSize(min(width, label.width()), label.height()))


def tab_title(win, tab) -> QRect:
    bar = win.tabs.tabBar()
    return bar.tabRect(win.tabs.indexOf(tab)).translated(bar.mapTo(win, QPoint(0, 0)))


def block_finished(win, run_id: int, block: str) -> str:
    """When `block` finished in run `run_id`, as the run history has it, to the second."""
    db = sqlite3.connect(win.home.history_db)
    try:
        (finished,) = db.execute("SELECT finished FROM blocks WHERE run_id=? AND block=?", (run_id, block)).fetchone()
    finally:
        db.close()
    return finished.replace("T", " ")[:19]


def test_commit_history_searches_previews_and_restores(proof, win, qtbot, tmp_path, base, monkeypatch):
    p = proof("Commit History: search a flow's commits, preview each version with its outputs, restore one", ASKED)
    p.idea("File > Commit History…, just under Save & Commit…, opens a window of its own with the Git history of the "
           "flow on screen. On its left, a search field with .* and Aa, and under it a card for each commit of the "
           "flow's file, newest first. On its right, a preview of the flow as it was in the selected commit, with the "
           "outputs of its latest run, and Restore this version.")
    p.idea("Each card shows the commit's message, its first line in bold, and at its bottom right the date and time "
           "and the commit's short id. The date is the commit's author date, in your computer's time: when its change "
           "was made. For your own Save & Commit, that is when you committed; a copy made with Save as… keeps the "
           "original's dates.")
    p.idea("Typing in the search field filters the cards at once. A commit is looked for first in its message, then "
           "in the outputs of the latest run made with that version, then in the code of its blocks (Python, SQL, and "
           "every other code setting), then anywhere else in its flow file: a text, a block's name, a setting. The "
           "cards come in that order, newest first within each, and each says where it was found. .* (regular "
           "expressions) is on as the window opens and Aa (match case) off, as in the find bars. Up and Down move "
           "through the cards, also from the search field.")
    p.idea("The preview draws the selected version as the canvas does, and nothing in it can be changed. Under each "
           "block is its result in the latest run made with exactly that version, and resting the mouse on it shows "
           "the canvas's tooltip of its outputs. For that, each run now keeps Git's id of the exact bytes of the flow "
           "file it ran. A run of changes not committed counts for no version, and neither do runs made before this "
           "change, which kept no id.")
    p.idea("Restore this version puts the selected version into the flow's tab, as one change that Undo takes back. "
           "Nothing is written until you save it: Save & Commit then commits it on top of the history, and the older "
           "versions stay. What it does not do: there is no diff view, no branches, and Restore restores only the "
           "flow file, not a run's outputs.")
    p.why_simplest("The window reads the history the way Save as… already reads it to give a copy the original's "
                   "history: from the newest commit back, a commit counting when it changed the flow file, and "
                   "through a Save as… copy back to the original's versions. Nothing about the commits is kept "
                   "anywhere else: their messages, dates and files are Git's.")
    p.why_simplest("The preview is the canvas's own drawing (the same blocks, connections, texts and result lines) in "
                   "a view that takes no clicks, drags or keys, only the mouse resting for a tooltip. The result under "
                   "each block and its tooltip are made by the very piece of the editor that makes them on the canvas, "
                   "now shared by both, so they cannot differ.")
    p.why_simplest("One id ties a run to a version: Git's id of the bytes the run read, which Git computes the same "
                   "way for the file in each commit. Finding a version's run is a plain lookup: no copy of the flow is "
                   "kept with the runs, and nothing is guessed from dates.")
    p.why_simplest("Restore uses the editor's own way of putting back a whole flow in one step, the one that brings "
                   "back unsaved changes after a crash: one change on the Undo list, nothing written. Keeping it is "
                   "File > Save and File > Save & Commit…, unchanged.")
    p.alternative("Counting a run for the commit made just before it",
                  "A run between two commits may have run changes that were never committed, and a run made after "
                  "you went back to an older version would be counted for the newer one. The id of the bytes says "
                  "exactly which version ran, or that none did.")
    p.alternative("Keeping a copy of the flow file with every run",
                  "It would keep, for every run, what Git already holds, and it would still have to be compared with "
                  "each commit. One id per run is enough.")
    p.alternative("Restore writing the old version into the flow file, as git checkout does",
                  "It would replace your file at once, unsaved changes and all, with no Undo. In the tab, it is one "
                  "change you can undo, and it is written only when you save it.")
    p.alternative("A window that waits for you, as Save & Commit's does",
                  "The editor could not be used while it is open: no looking at the flow, running it, or saving and "
                  "committing with the history in view. A window of its own stays open beside the editor, and a Save "
                  "& Commit adds its commit to it.")
    p.alternative("The commit date, not the author date",
                  "Save as… into another folder commits the original's versions again there, at the moment of the "
                  "copy: every card would show that moment. The author date stays when each version was made; for "
                  "your own Save & Commit the two are the same.")
    p.fits("It sits in the File menu with Save & Commit… and Save as…, and is available once the flow is saved (its "
           "file is what has a history); before that, its tooltip says to save first. Each flow has one window, "
           "titled with its file name: Commit History… again brings it to the front, and closing the flow's tab "
           "closes it.")
    p.fits("The code searched is every code setting of every block: Python Code's code, the SQL of DuckDB SQL and Run "
           "Query, Polars Transform's code, HTML templates, If conditions, Inspect messages, and the code settings of "
           "blocks of your own. The results in the preview are the canvas's, with “results from” and the time at the "
           "bottom right of their tooltips, and the line above the preview numbers the run as Run history does.")
    p.fits("The search field's .* and Aa work as the find bars' (Ctrl+F): the same regular expressions, the same red "
           "for a pattern that is not valid. They are the window's own: turning them here leaves the find bars' as "
           "they are.")
    p.fits("Taskloom reads the history with the Git it carries, as Save & Commit writes it: no Git program is needed. "
           "The window follows the Theme setting, as the rest of the editor does.")
    assert GIT, "this proof reads what Taskloom wrote with the git program, which is not installed"
    # The proof's own global Git config, empty, and no system one: the user's Git settings are never read or written.
    git_config = tmp_path / "gitconfig"
    git_config.write_bytes(b"")
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings writes Windows' Startup script there)
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    folder = base / "sales"
    folder.mkdir()
    flow = folder / "weekly-sales.yaml"
    flow.write_text(FLOW, encoding="utf-8")
    history = None
    try:
        # --- three versions, two of them run ------------------------------------------------------------------
        p.step("Open weekly-sales.yaml (File > Open…) and choose File > Save & Commit…. Type your name, your email and "
               f"“{FIRST}”, and click Save & Commit. Its folder, sales, has no Git repository yet: Save & Commit "
               "creates one.")
        p.check("sales has no Git repository yet: git finds none, in it or above it", not in_a_repository(folder))
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
        menu_item(file_menu(win), "Open…").trigger()
        tab = win.current
        p.check("The flow opens", tab is not None and tab.doc.path == flow)
        save_and_commit(p, win, FIRST, name=NAME, email=EMAIL)
        p.check("It is committed: one commit, holding the flow file as saved", count(folder) == 1
                and git_bytes(folder, "cat-file", "blob", "HEAD:weekly-sales.yaml") == flow.read_bytes())

        p.step("Press ▶ Run now.")
        first_run = run_now(win, qtbot, tab, 1)
        p.shot(win, f"The first version ran (run #{first_run.run_id}): start_by_hand, then load_sales, which makes "
                    f"the sales of three regions. Above them, the text {TEXT_V1}.",
               highlight=[["start_by_hand", "load_sales"]], zoom=[canvas_text(win, tab), "start_by_hand", "load_sales"])
        p.check("The run succeeded", first_run.status == "success")

        p.step("Add total_sales, a Python Code block, after load_sales, and Save & Commit it without running it. Its "
               f"code: “{TOTAL_V2.splitlines()[1]}”, under the comment “{TOTAL_V2.splitlines()[0]}”. In Save & "
               "Commit's window, type “Add the total” and, on the lines under it, “Not run yet: to be checked "
               "tomorrow.”.")
        p.note("The proof makes the edits between the commits through the editor's own editing, as dragging a block "
               "in from Blocks, connecting it and typing in Properties make them (other proofs show those), and "
               "scrolls the canvas to show the whole flow. What matters here is what each Save & Commit commits.")
        a_second_later(qtbot, folder)
        added = tab.doc.add_block("logic.python", 520, 0, {"inputs": ["input"], "code": TOTAL_V2})
        tab.doc.rename_block(added, "total_sales")
        tab.doc.connect("load_sales", "output", "total_sales", "input")
        settle()
        tab.view.center_on_flow()
        save_and_commit(p, win, SECOND, shoot="① Your name and email are filled in: those you typed the first "
                                                "time. ② The message: its first line, then why.")
        p.check("Two commits now", count(folder) == 2)

        p.step("Change total_sales' code so that it also counts the regions, and the text, then Save & Commit and "
               f"press ▶ Run now. The text now says “{TEXT_V3}”; the commit message is “{THIRD}”.")
        a_second_later(qtbot, folder)
        tab.doc.set_block_value("total_sales", "code", TOTAL_V3)
        tab.doc.set_note_value("note_1", "text", TEXT_V3)
        save_and_commit(p, win, THIRD)
        second_run = run_now(win, qtbot, tab, 2)
        p.shot(win, f"The third version ran (run #{second_run.run_id}): total_sales says {COUNTED}. The second version "
                    "was never run.", highlight=["total_sales"], zoom=["start_by_hand", "total_sales"])
        p.check(f"The run succeeded, and total_sales made “{COUNTED}”",
                second_run.status == "success" and COUNTED in tab.scene.values.get("total_sales", {}).get("output", ""))
        ids = dict(zip(["v3", "v2", "v1"], lines(folder, "log", "--format=%H", "--", "weekly-sales.yaml")))
        p.text("git log, in sales: the three commits Save & Commit made, newest first, with their dates and times",
               git(folder, "log", "--format=%h  %ad  %s", "--date=format-local:%Y-%m-%d %H:%M:%S").strip())
        p.check("Three commits of weekly-sales.yaml, one per Save & Commit", len(ids) == 3 and count(folder) == 3)

        # --- the window -------------------------------------------------------------------------------------------
        p.step("Open the File menu, and choose Commit History….")
        items = []

        def in_the_menu(menu):
            items.extend(a.text() for a in menu.actions() if not a.isSeparator())
            p.shot(menu, "① Commit History… is new in the File menu, just under Save & Commit….", over=win,
                   highlight=[menu_item(menu, COMMIT_HISTORY)], badges=True, zoom=[menu])
            choose(menu, COMMIT_HISTORY)

        bar = win.menuBar()
        p.while_open(lambda: file_menu(win).exec(bar.mapToGlobal(bar.actionGeometry(
            file_menu(win).menuAction()).bottomLeft())), in_the_menu)
        p.check("① Commit History… is just under Save & Commit…",
                items[items.index(SAVE_AND_COMMIT) + 1] == COMMIT_HISTORY)
        qtbot.waitUntil(lambda: tab in win.histories and win.histories[tab].isVisible(), timeout=10_000)
        history = win.histories[tab]
        settle()
        p.shot(history, "The Commit History window of weekly-sales.yaml. ① The search field, with .* pressed in (on) "
                        "and Aa not (off). ② The three commits, newest first; the first is selected. ③ The preview of "
                        "the selected version, under the line that says which run its outputs are from. ④ Restore this "
                        "version.",
               highlight=[[history.field, toggle(history, "Aa")], all_cards(history),
                          [history.run_line, history.preview], history.restore_button], badges=True)
        p.note("The screenshots show the window without the title bar the system draws around it: its title is "
               "“weekly-sales.yaml — Commit History”.")
        p.check("It is a window of its own, titled with the flow's file name",
                history.isWindow() and history.windowTitle() == "weekly-sales.yaml — Commit History")
        p.check("① .* is on and Aa off", toggle(history, ".*").isChecked() and not toggle(history, "Aa").isChecked())
        p.check("② Three cards, newest first, the first selected",
                which(history, ids) == ["v3", "v2", "v1"] and selected(history) == 0)
        p.shot(history, "The cards close up: each message's first line in bold (“Add the total” has a second line, "
                        "under it), and at the bottom right the date and time it was committed, then the short id.",
               zoom=all_cards(history))
        when = {v: git(folder, "log", "-1", "--format=%ad", "--date=format-local:%Y-%m-%d %H:%M:%S", ids[v]).strip()
                for v in ids}
        p.check("Each card shows its message, and the date and time it was committed with its short id, as git log "
                "shows them", [(c.split("\n")[0], c.rsplit("\n", 1)[-1]) for c in cards(history)]
                == [(SUBJECTS[v], f"{when[v]} {ids[v][:7]}") for v in ("v3", "v2", "v1")])

        p.step("With the window open, click total_sales on the editor's canvas.")
        at = tab.view.mapFromScene(tab.scene.nodes["total_sales"].mapToScene(NODE_W / 2, HEADER_H / 2))
        QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
        settle()
        p.check("The window does not wait for an answer (it is not modal): the editor takes the click, and its "
                "Properties show total_sales",
                not history.isModal() and QApplication.activeModalWidget() is None
                and win.properties.block_id == "total_sales" and history.isVisible())

        # --- the preview ------------------------------------------------------------------------------------------
        p.step("In the window, rest the mouse on the result under load_sales, in the preview of the newest version.")
        activate(qtbot, history)
        line = history.run_line.text()
        p.check(f"The line above the preview says its outputs are those of run #{second_run.run_id}, the latest run "
                "made with exactly this version",
                re.fullmatch(rf"Outputs of run #{second_run.run_id}, \d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d: the latest "
                             "run made with exactly this version", line))
        p.check("It draws the third version as the canvas does: the same blocks in the same places, with the same "
                "result lines, and the same text", drawn(history.preview.scene()) == drawn(tab.scene)
                and drawn(tab.scene)["texts"] == [TEXT_V3])
        canvas_tip = hover(qtbot, tab.view, "load_sales").text()
        tip = hover(qtbot, history.preview, "load_sales")
        said = plain(tip.text())
        finished = block_finished(win, second_run.run_id, "load_sales")
        p.shot(tip, f"① The line above the preview: {line}. ② Resting the mouse on load_sales' result shows its "
                    f"output, the sales of the three regions laid out, and at the bottom right results from {finished}: when "
                    f"load_sales finished in run #{second_run.run_id}.", over=history,
               highlight=[history.run_line, tip], badges=True, zoom=[tip, in_preview(history, "load_sales")])
        tip.hide()
        p.check(f"② The tooltip shows load_sales' output, North 120, South 80 and West 45, and ends with “results "
                f"from {finished}”", all(f'"{region}": {n}' in said for region, n in
                                          (("North", 120), ("South", 80), ("West", 45)))
                and said.endswith(f"results from {finished}"))
        p.check("It is the tooltip the canvas shows for load_sales, word for word", tip.text() == canvas_tip)

        p.step("Press Down, with the cursor in the search field.")
        QTest.mouseClick(history.field, Qt.LeftButton)
        qtbot.waitUntil(history.field.hasFocus, timeout=5_000)
        QTest.keyClick(history.field, Qt.Key_Down)
        settle()
        p.shot(history, f"① The second card, “Add the total”, is selected, and the preview follows: ② total_sales is "
                        f"there, with its connection, but no block shows a result: ③ {NO_RUN}.",
               highlight=[card(history, 1), in_preview(history, "start_by_hand", "total_sales"), history.run_line],
               badges=True, zoom=[in_preview(history, "start_by_hand", "total_sales"), history.run_line])
        p.check("① Down selected the second card, and the cursor stayed in the search field",
                selected(history) == 1 and history.field.hasFocus())
        p.check(f"② The preview shows the second version: its three blocks, the text {TEXT_V1}, no results; ③ the "
                f"line above says “{NO_RUN}”",
                set(history.preview.scene().nodes) == {"start_by_hand", "load_sales", "total_sales"}
                and drawn(history.preview.scene())["texts"] == [TEXT_V1]
                and all(n.status is None for n in history.preview.scene().nodes.values())
                and history.run_line.text() == NO_RUN)
        p.note("A version counts as run only when a run's flow file had exactly its bytes. The second version was "
               "saved, committed and never run, so it has no outputs to show. Runs made before this change kept no "
               "version, so they count for none (all the runs here are new).")

        p.step("Press Down again, then Up twice.")
        QTest.keyClick(history.field, Qt.Key_Down)
        settle()
        oldest = (selected(history), history.run_line.text(), set(history.preview.scene().nodes),
                  history.preview.scene().nodes["load_sales"].status)
        QTest.keyClick(history.field, Qt.Key_Up)
        settle()
        back_one = (selected(history), history.run_line.text())
        QTest.keyClick(history.field, Qt.Key_Up)
        settle()
        p.check(f"Down: the oldest card, previewed with the outputs of run #{first_run.run_id} (its two blocks, "
                "load_sales with its result); Up: the second card again, with no run, then the first, with run "
                f"#{second_run.run_id}'s outputs", oldest[0] == 2
                and oldest[1].startswith(f"Outputs of run #{first_run.run_id}, ")
                and oldest[2] == {"start_by_hand", "load_sales"} and oldest[3] == "success"
                and back_one == (1, NO_RUN) and selected(history) == 0
                and history.run_line.text().startswith(f"Outputs of run #{second_run.run_id}, "))

        # --- search -----------------------------------------------------------------------------------------------
        for word, why, found, place, caption in (
                ("tomorrow", "a word that is only in the second commit's message, on its second line", ["v2"],
                 "in the message", "tomorrow finds one commit: ① “Add the total”, in the message (its second line)."),
                ("245 sold", f"what total_sales made in run #{second_run.run_id}; its code says “{{total}} sold”, "
                             "not 245", ["v3"], "in the outputs of total_sales",
                 "245 sold finds one commit: ① “Show the count with the total”, in the outputs of total_sales."),
                ("sum", "a word that is only in total_sales' code", ["v3", "v2"], "in the code of total_sales",
                 "sum finds the two commits with total_sales, ① newest first, each in the code of total_sales."),
                ("Mondays", "a word that is only in the text on the canvas, in the third version", ["v3"],
                 "in the flow file", "Mondays finds one commit: ① “Show the count with the total”, in the flow "
                                     "file.")):
            p.step(f"Type {word} in the search field: {why}.")
            type_in(history, word)
            p.shot(history, caption, highlight=[all_cards(history)], badges=True,
                   zoom=[history.field, *all_cards(history)])
            p.check(f"{word} finds {named(found)}, {place}", which(history, ids) == found
                    and where(history) == [place] * len(found) and selected(history) == 0)

        p.step("Type sum( (an opening bracket after sum), then click .* to turn regular expressions off.")
        before = red_pixels(history.field)
        type_in(history, "sum(")
        problem = history.empty.text()
        p.shot(history, f"With .* on, sum( is not a valid regular expression: ① it is red, and ② the window says why: "
                        f"{problem}", highlight=[history.field, history.empty], badges=True,
               zoom=[history.field, history.empty])
        p.check("① The field's text turns red", red_pixels(history.field) - before >= 10)
        p.check("② The window says it is not a valid regular expression, and shows no card",
                problem.startswith("Not a valid regular expression: ") and history.list.count() == 0
                and history.field.toolTip() == problem)
        QTest.mouseClick(toggle(history, ".*"), Qt.LeftButton)
        settle()
        p.shot(history, "With .* off, sum( is found as typed: ① in the code of total_sales, in the third and the "
                        "second version.", highlight=[all_cards(history)], badges=True,
               zoom=[history.field, *all_cards(history)])
        p.check(f"With .* off, sum( finds {named(['v3', 'v2'])}, in the code of total_sales, and is no longer red",
                not toggle(history, ".*").isChecked() and which(history, ids) == ["v3", "v2"]
                and where(history) == ["in the code of total_sales"] * 2 and red_pixels(history.field) - before < 10)
        QTest.mouseClick(toggle(history, ".*"), Qt.LeftButton)
        type_in(history, "")
        p.check("Click .* on again and empty the field: every card shows again, the first selected",
                toggle(history, ".*").isChecked() and which(history, ids) == ["v3", "v2", "v1"]
                and selected(history) == 0)

        # --- restore ----------------------------------------------------------------------------------------------
        p.step("Click the oldest card, “Load the sales of each region”, then click Restore this version.")
        held = yaml.safe_load(yaml.safe_dump(tab.doc.data))  # what the tab holds: the third version
        on_disk = snapshot(folder)  # every file of the folder, its Git repository too
        click_card(history, 2)
        p.check("The oldest card is selected", selected(history) == 2)
        QTest.mouseClick(history.restore_button, Qt.LeftButton)
        settle()
        v1 = git_bytes(folder, "cat-file", "blob", f"{ids['v1']}:weekly-sales.yaml")
        said = win.statusBar().currentMessage()
        p.shot(win, f"① The flow's tab holds the first version now: start_by_hand and load_sales, and the text "
                    f"{TEXT_V1}; total_sales is gone. ② The tab is marked modified (*). ③ The status bar says what was "
                    "restored, and that it is kept only once saved.",
               highlight=[["start_by_hand", "load_sales"], tab_title(win, tab), status_message(win)], badges=True,
               zoom=[tab_title(win, tab), "start_by_hand", "load_sales"])
        p.shot(win, f"③ The status bar, close up: {said}", highlight=[status_message(win)], badges=[3],
               zoom=[status_message(win)])
        p.check("① The tab holds the first version, as it was committed",
                tab.doc.data == yaml.safe_load(v1) and set(tab.scene.nodes) == {"start_by_hand", "load_sales"})
        p.check("② The tab is marked modified", tab.doc.dirty and win.tabs.tabText(win.tabs.indexOf(tab)).endswith("*"))
        p.check("③ The status bar says “Restored the version of <its date and time> (<its message>): Save or Save & "
                "Commit to keep it”",
                said == f"Restored the version of {when['v1']} ({FIRST}): Save or Save & Commit to keep it")
        p.check("Nothing on disk changed: the flow file and the Git repository are as they were",
                snapshot(folder) == on_disk)
        p.check(f"The canvas goes on showing start_by_hand's and load_sales' latest results, from run "
                f"#{second_run.run_id}", all(tab.scene.nodes[b].status == "success"
                                             and tab.block_info[b]["run_id"] == second_run.run_id
                                             for b in ("start_by_hand", "load_sales")))
        p.note(f"Restore restores the flow file only: the run history and the folder's other files stay as they were. "
               f"The canvas goes on showing each block's latest result, from run #{second_run.run_id}, as after any "
               f"edit: Restore does not bring back the outputs of the first version's run (here they are the same).")

        p.step("Choose Edit > Undo, then Edit > Redo.")
        menu_item(edit_menu(win), "Undo").trigger()
        settle()
        undone = yaml.safe_load(yaml.safe_dump(tab.doc.data))
        menu_item(edit_menu(win), "Redo").trigger()
        settle()
        p.check("Undo brings back what the tab held, the third version; Redo the restored first version again",
                undone == held and tab.doc.data == yaml.safe_load(v1))
        p.check("Still nothing on disk changed", snapshot(folder) == on_disk)

        # --- save & commit the restored version -------------------------------------------------------------------
        p.step(f"Choose File > Save & Commit…, type “{FOURTH}”, and click Save & Commit. The Commit History window "
               "is still open.")
        a_second_later(qtbot, folder)
        save_and_commit(p, win, FOURTH)
        qtbot.waitUntil(lambda: history.list.count() == 4, timeout=5_000)
        ids = {"v4": git(folder, "rev-parse", "HEAD").strip(), **ids}
        p.shot(history, f"① The new commit, “{FOURTH}”, is on top of the list in the open window; ② the three "
                        "before it stay.", highlight=[card(history, 0), [card(history, r) for r in (1, 2, 3)]],
               badges=True, zoom=all_cards(history))
        p.shot(history, f"③ It is selected, and its preview has the outputs of run #{first_run.run_id}, the first "
                        "version's run: its file is byte for byte the first version's.",
               highlight=[run_line(history), in_preview(history, "start_by_hand", "load_sales")], badges=[3, 3],
               zoom=[run_line(history), in_preview(history, "start_by_hand", "load_sales")])
        p.check("① ② The window lists the new commit on top, then the three before it",
                which(history, ids) == ["v4", "v3", "v2", "v1"] and cards(history)[0].startswith(FOURTH))
        p.check("In Git, the new commit is on top of the third; the older commits stay",
                count(folder) == 4 and git(folder, "rev-parse", "HEAD^").strip() == ids["v3"]
                and lines(folder, "log", "--format=%s") == [SUBJECTS[v] for v in ("v4", "v3", "v2", "v1")])
        p.check("Its flow file is exactly the restored version's bytes: the same as in the first commit, and as "
                "saved", git_bytes(folder, "cat-file", "blob", "HEAD:weekly-sales.yaml") == v1 == flow.read_bytes()
                and not tab.doc.dirty)
        p.check(f"③ Its preview shows the outputs of run #{first_run.run_id}, made with those very bytes",
                selected(history) == 0 and history.run_line.text().startswith(f"Outputs of run #{first_run.run_id}, "))

        p.step("Type region in the search field: it is in each of the four commits, in a different place.")
        type_in(history, "region")
        p.shot(history, "region: the cards are grouped by where it was found, not by date. ① The first commit, in its "
                        "message. ② The third, in its run's outputs (3 regions). ③ The second, in its code (Add up "
                        "every region). ④ The newest, in its flow file (the text).",
               highlight=all_cards(history), badges=True, zoom=[history.field, *all_cards(history)])
        p.check("region finds the first commit in its message, the third in its outputs, the second in its code, then "
                "the newest in its flow file: message, outputs, code, flow file, in that order",
                which(history, ids) == ["v1", "v3", "v2", "v4"]
                and where(history) == ["in the message", "in the outputs of total_sales",
                                       "in the code of total_sales", "in the flow file"])
        p.note("A commit shows once, where region is found first: the third version's code and flow file hold it "
               "too. The newest commit holds the first version's flow file, whose text says region; its message, its "
               "run's outputs and its code do not.")

        # --- dark --------------------------------------------------------------------------------------------------
        p.step("Choose Tools > Settings…, set Theme to Dark and press Save. The Commit History window is still open.")
        set_theme(p, win, "Dark", shoot=False)
        settle()
        p.shot(history, "In Dark, the window, its cards and its preview are dark, and their text stands out.")
        p.check("The window, its cards and its preview are dark, with text that stands out",
                not not_in_theme(history, dark=True)
                and is_dark(most_common(history.preview.viewport().grab().toImage(), step=4)))
        p.step("Set Theme back to System (Tools > Settings…), and press Save.")
        set_theme(p, win, "System", shoot=False)
        p.check("The Theme is System again, and the editor light",
                theme.chosen(win.settings) == "System" and QApplication.palette().window().color().lightness() >= 128)
    finally:  # the Theme and the application's look as the proof found them; no window left open
        if history is not None and history.isVisible():
            history.close()
        win.settings.setValue("theme", "System")
        theme.apply("System")
        if app.style().name() != as_found[0]:
            QApplication.setStyle(as_found[0])
        QApplication.setPalette(as_found[1])
        QToolTip.setPalette(as_found[2])
        QApplication.processEvents()
