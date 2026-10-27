"""Proof: File > Save & Commit… saves the flow and commits it, and only it, to Git; a Save as… copy keeps the flow's
Git history (v0.24). What Taskloom wrote in Git is read with the real git program, as you would with any Git tool."""

import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QSettings, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QPushButton

from proofs.conftest import choose, menu_item
from taskloom.editor.canvas import HEADER_H, NODE_W
from tests.test_paths_editor_editing import clean, field, save_as
from tests.test_reusable_block import text_box
from tests.test_save_and_commit import (GIT, authored, blob, commit, conflicting_merge, count, fill, git, git_bytes,
                                        intro, lines, new_repo, prefilled, shown, slashes, snapshot, status, subjects)

# Taken as this file is imported, before the win fixture answers message boxes itself: the box Save & Commit
# refuses with is then shown as in the app.
REAL_WARNING = QMessageBox.warning
ASKED = ("Lets add: File -> \"Save & Commit\" where it will ask for a commit message and if I click on the Save & "
         "Commit message it does both. If later I do a Save as... that new version should have its own copy of the "
         "git history up to that point (Save as would work like a new branch probably, I am thinking)” — and again: "
         "“Save as should give the copy the original's history up to that point")
SAVE_AND_COMMIT = "Save && Commit…"  # the File menu item: Qt shows it as "Save & Commit…"
NAME, EMAIL = "Ana Lima", "ana@example.com"
BEN, BEN_EMAIL = "Ben Okafor", "ben@example.com"  # a colleague, who commits the EU copy
FIRST, SECOND, EU = "Add the daily sales report", "Save the totals by region", "Read the EU sales"
THIRD = "Save the totals as daily-totals.csv"  # committed on the original after the copy was made
COPY = "Copy daily-report.yaml to daily-report-eu.yaml (Save as)"
FLOW = """\
name: {name}
blocks:
  start_by_hand:
    type: trigger.manual
    ui: {{x: 0, y: 0}}
  sales:
    type: data.read_file
    config: {{path: sales.csv}}
    ui: {{x: 260, y: 0}}
  totals:
    type: data.duckdb_sql
    config:
      inputs: [sales]
      sql: select region, sum(amount) as total from sales group by region
    ui: {{x: 520, y: 0}}
  save:
    type: data.write_file
    config: {{path: out/totals.csv}}
    ui: {{x: 780, y: 0}}
edges:
  - start_by_hand.start -> sales.after
  - sales.table -> totals.sales
  - totals.result -> save.table
"""


@pytest.fixture
def base():
    """A new folder with a short path, as the windows and the status bar show it whole; removed after."""
    folder = Path(tempfile.mkdtemp(prefix="tl-")).resolve()
    yield folder
    # (Git keeps its objects read-only, which Windows does not delete.)
    shutil.rmtree(folder, onexc=lambda remove, path, _: (os.chmod(path, stat.S_IWRITE), remove(path)))


def write_flow(path, name):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FLOW.format(name=name).encode("utf-8"))
    return path.resolve()


def in_a_repository(folder) -> bool:
    """Whether git finds a repository for `folder` (in it or a folder above)."""
    found = subprocess.run([GIT, "-C", str(folder), "rev-parse", "--git-dir"], capture_output=True, timeout=60)
    return found.returncode == 0


def transcript(folder, *commands: str) -> str:
    """Each git command, as you would type it in `folder`, then what it printed (without its empty lines)."""
    said = []
    for command in commands:
        printed = [line for line in git(folder, *shlex.split(command)).splitlines() if line.strip()]
        said += [f"$ git {command}", *(printed or ["(it printed nothing)"])]
    return "\n".join(said)


def open_flow(win, monkeypatch, file_menu, path):
    """File > Open…, choosing `path` in the file dialog: the tab it opens in."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    menu_item(file_menu, "Open…").trigger()
    QApplication.processEvents()
    return win.current


def click_tab(win, tab):
    """Click the flow's tab, above the canvas."""
    bar = win.tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.tabs.indexOf(tab)).center())
    QApplication.processEvents()
    assert win.current is tab, "its tab did not come to the front"


def set_path(win, tab, block: str, text: str):
    """Click the block's title bar, then type `text` over its Path in Properties and press Enter."""
    at = tab.view.mapFromScene(tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2)))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()
    box = field(win, "Path")
    box.selectAll()
    QTest.keyClicks(box, text)
    QTest.keyClick(box, Qt.Key_Return)
    QApplication.processEvents()
    assert tab.doc.blocks[block]["config"]["path"] == text and tab.doc.dirty, "the Path was not changed"


def button(window, text: str) -> QPushButton:
    (found,) = [b for b in window.findChildren(QPushButton) if b.isVisible() and shown(b.text()) == text]
    return found


def intro_label(window) -> QLabel:
    """The Save & Commit window's first lines: what it will do."""
    return next(label for label in window.findChildren(QLabel) if label.text().startswith("Saves "))


def status_message(win) -> QRect:
    """Where the status bar's message is, in the window."""
    bar = win.statusBar()
    return QRect(bar.mapTo(win, QPoint(0, 0)),
                 QSize(bar.fontMetrics().horizontalAdvance(bar.currentMessage()) + 16, bar.height()))


def test_save_and_commit_and_save_as_keeps_the_history(proof, win, qtbot, tmp_path, base, monkeypatch):
    p = proof("Save & Commit, and a Save as copy keeps the flow's Git history", ASKED)
    p.idea("File > Save & Commit… saves the flow and commits it to Git in one go. A copy made with Save as… starts "
           "with the original's Git history.")
    p.idea("Its window asks what changed, and your name and email (filled in with the ones you typed last time, or "
           "else those of your Git settings). Its Save & Commit button saves the flow, then commits that one file, "
           "and only it, to the Git repository of the flow's folder. If the folder has none, it creates one there, "
           "which any Git tool can read. Files you staged stay staged; data, outputs and other flows are not "
           "committed.")
    p.idea("Save as… then gives the copy the original's history up to its last commit. In the same repository, that "
           "is one commit that copies the file, even in another of its folders: Git follows the copy back through the "
           "original's versions. In another repository (a folder outside the original's repository), each version "
           "is committed again there, oldest first, with its message, author and date; a folder with no repository "
           "gets one.")
    p.idea("Changes the original has not committed are not history: they reach the copy only as its content, so the "
           "copy shows as modified until you Save & Commit it. Taskloom carries its own Git, so no Git program has to "
           "be installed; its commits run no Git hooks and are not signed. Taskloom has no history view: you look at "
           "the history with any Git tool.")
    p.why_simplest("One menu item, one window, one file per commit. The commit holds exactly the flow as saved, so "
                   "nothing has to be chosen and nothing else can slip in: what you staged, your data and your "
                   "outputs are left as they are. A flow that is the same as in its last commit makes no commit.")
    p.why_simplest("Taskloom does the Git work itself, with a small Git library it carries (dulwich). It works the "
                   "same on a PC with no Git program, and it writes an ordinary Git repository, which every Git tool "
                   "reads.")
    p.why_simplest("A copy needs no branch: it is one more file. In the same repository, one commit, “Copy … (Save "
                   "as)”, is all Git needs to follow the copy back through the original's versions. In another "
                   "repository there is nothing to follow back to, so the versions are committed there again.")
    p.alternative("A Git branch for each Save as, as you thought it might work",
                  "A branch holds every file of the folder, and only one branch is checked out at a time: switching "
                  "to the copy's branch would take the original (and every other file) back to that branch's "
                  "versions. The copy is a second file, used next to the original, so it is one more file on the "
                  "same branch.")
    p.alternative("Committing the original's versions again in the same repository",
                  "Git would then see each version twice. Tried with git 2.54: git log --follow of the copy lists the "
                  "copies of the versions, then the original's own commits, so every version twice. One copy commit "
                  "gives the history once.")
    p.alternative("A separate history for the copy, joined to the branch with a merge",
                  "Each version would then be in the repository twice, as the original's commit and as the copy's, "
                  "and the history would have two beginnings and a merge to make sense of.")
    p.alternative("Using the Git program installed on the PC",
                  "Many PCs have none, and on a PC without administrator rights it may not be possible to install "
                  "one. Taskloom would also depend on that program's version and settings.")
    p.alternative("Shipping the Git program (MinGit) inside Taskloom",
                  "About 96 MB more, under the GPL licence, for commits of one file. The library Taskloom carries "
                  "is a few MB, under a licence (Apache 2.0) that fits Taskloom's MIT.")
    p.fits("The repository is the one of the flow's folder, as you chose when asked: “Git repo in the flow's folder "
           "(Recommended)”. It is found as Git finds it, in that folder or one above, so a flow inside a project's "
           "repository is committed there; the window says where before anything is written.")
    p.fits("Save & Commit saves through the editor's own Save, with what follows a save (the flow's schedule, the "
           "list of flows you have open). Save, and the saves that Run, Validate and closing make, never commit: "
           "saved does not mean committed, so Save & Commit compares the file with its last commit.")
    p.fits("Save as works as before: the copy is saved first and its tab shows it. The Git step after it only adds "
           "commits (and a repository, in a folder that has none), or says why it could not; it never changes the "
           "saved copy or the original's file.")
    p.fits("The name and email are kept with the editor's other preferences, in your Taskloom folder: Taskloom never "
           "writes your Git settings. Flow files are written with Unix line ends on every system, so Git on Windows "
           "sees no change that is not there.")
    assert GIT, "this proof reads what Taskloom wrote with the git program, which is not installed"
    # The test's own global Git config, empty: the computer's system config still applies, the user's is never read
    # or written. (Variables that would point git at another repository, as in a Git hook, are not inherited.)
    git_config = tmp_path / "gitconfig"
    git_config.write_bytes(b"")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git_config))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)
    folder = base / "flows é"
    flow = write_flow(folder / "daily-report.yaml", "daily-report")
    (folder / "sales.csv").write_bytes(b"region,amount\nEU,120\nUS,80\n")
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")

    # --- Save & Commit ---------------------------------------------------------------------------------------------
    p.step("Open daily-report.yaml (File > Open…), then open the File menu. The flow is in the folder flows é, next "
           "to sales.csv, the data it reads. The folder has no Git repository yet.")
    p.check("flows é has no Git repository yet: git finds none, in it or above it", not in_a_repository(folder))
    tab = open_flow(win, monkeypatch, file_menu, flow)
    p.check("The flow opens", tab is not None and tab.doc.path == flow)
    bar = win.menuBar()
    at = bar.mapToGlobal(bar.actionGeometry(file_menu.menuAction()).bottomLeft())
    found = {}

    def in_the_window(window):
        fill(window, name=NAME, email=EMAIL, message=FIRST)
        found["git"] = shutil.which("git")
        boxes = [text_box(window, label) for label in ("Name", "Email", "Message")]
        p.shot(window, f"① The window says what it will do: save daily-report.yaml and commit it, and only it, to a "
                       f"new Git repository in flows é (there is none yet). ② Your name, your email and what "
                       f"changed: “{FIRST}”. ③ The button that does both.",
               highlight=[intro_label(window), boxes, button(window, "Save & Commit")], badges=True)
        found["branch"] = intro(window).rsplit(" ", 1)[-1].rstrip(".")
        p.check(f"① It says it saves daily-report.yaml and commits it to a new Git repository in flows é, on branch "
                f"{found['branch']}",
                slashes(intro(window)) == slashes(f"Saves daily-report.yaml and commits it, and only it, to a new Git "
                                                  f"repository in {folder} (there is none yet), on branch "
                                                  f"{found['branch']}."))
        p.step("Click Save & Commit.")
        QTest.mouseClick(button(window, "Save & Commit"), Qt.LeftButton)

    def in_the_menu(menu):
        items = [a.text() for a in menu.actions() if not a.isSeparator()]
        p.shot(menu, "① Save & Commit… is new in the File menu, right under Save as… and above Close flow.", over=win,
               highlight=[menu_item(menu, SAVE_AND_COMMIT)], badges=True, zoom=[menu])
        p.check("① Save & Commit… is right under Save as…, before Close flow",
                items[items.index("Save as…") + 1:items.index("Save as…") + 3] == [SAVE_AND_COMMIT, "Close flow"])
        p.step("Choose Save & Commit…, and type your name, your email and what changed.")
        p.note("To show that no Git program is needed, the proof hides the git program from Taskloom while you do "
               "this, as on a PC without Git: Taskloom finds none, and needs none.")
        p.while_open(lambda: choose(menu, SAVE_AND_COMMIT), in_the_window)

    programs = tmp_path / "no programs"
    programs.mkdir()
    path = os.environ["PATH"]
    monkeypatch.setenv("PATH", str(programs))  # no git program to be found
    try:
        p.while_open(lambda: file_menu.exec(at), in_the_menu)
    finally:
        monkeypatch.setenv("PATH", path)
    said = win.statusBar().currentMessage()
    head = subprocess.run([GIT, "-C", str(folder), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True,
                          timeout=60)
    p.check("Git now finds a repository in flows é, with a commit in it",
            in_a_repository(folder) and head.returncode == 0)
    first = git(folder, "rev-parse", "HEAD").strip()
    branch = git(folder, "symbolic-ref", "--short", "HEAD").strip()
    p.shot(win, f"① The status bar says what was done: Taskloom created a Git repository in flows é, saved "
                f"daily-report.yaml and committed it there, on {branch}, as commit {first[:7]}.",
           highlight=[status_message(win)], badges=True, zoom=[status_message(win)])
    p.check("① The status bar says it created a Git repository in flows é, then saved and committed the flow there",
            slashes(said) == slashes(f"Created a Git repository in {folder}. Saved and committed daily-report.yaml "
                                     f"to {folder} ({branch}, {first[:7]})."))
    p.check("It was committed while no git program could be found", found["git"] is None)
    p.check("The flow is saved: its tab has no unsaved changes", not tab.doc.dirty)
    p.check("It is on the branch the window named, with one commit, holding daily-report.yaml and nothing else, "
            "byte for byte as saved",
            branch == found["branch"] and count(folder) == 1
            and lines(folder, "ls-tree", "-r", "--name-only", "HEAD") == ["daily-report.yaml"]
            and git_bytes(folder, "cat-file", "blob", "HEAD:daily-report.yaml") == flow.read_bytes())
    p.check("The commit has your message, and your name and email as its author and committer",
            git(folder, "log", "-1", "--format=%an|%ae|%cn|%ce").strip() == f"{NAME}|{EMAIL}|{NAME}|{EMAIL}"
            and git(folder, "cat-file", "commit", "HEAD").split("\n\n", 1)[1] == f"{FIRST}\n")
    p.check("sales.csv is not committed: git status shows it untracked, and nothing else (no false change to the "
            "flow)", status(folder) == ["?? sales.csv"])
    p.check("The saved flow has Unix line ends only (no CRLF), on Windows too", b"\r" not in flow.read_bytes())
    settings = QSettings(str(win.home.root / "editor.ini"), QSettings.IniFormat)
    p.check("Your name and email are remembered with the editor's preferences; your Git settings were not written",
            (settings.value("git/name"), settings.value("git/email")) == (NAME, EMAIL)
            and git_config.read_bytes() == b"")

    p.step("Stage a file of your own with another Git tool: write notes.txt in flows é, then git add notes.txt.")
    (folder / "notes.txt").write_bytes(b"Ask about the EU totals.\n")
    git(folder, "add", "notes.txt")  # with the git program, as any other Git tool would
    p.check("notes.txt is staged (A), not committed", status(folder) == ["A  notes.txt", "?? sales.csv"])

    p.step("Change where the report is saved, and Save & Commit again. Click the block save and type "
           "out/totals-by-region.csv as its Path, then choose Save & Commit… in the File menu, type “Save the totals "
           "by region” and click Save & Commit.")
    made = int(git(folder, "log", "-1", "--format=%at"))
    qtbot.waitUntil(lambda: time.time() >= made + 1, timeout=5_000)  # (so the two commits' times differ)
    set_path(win, tab, "save", "out/totals-by-region.csv")
    again = []

    def in_the_window_again(window):
        again.append(prefilled(window))
        fill(window, message=SECOND)
        p.shot(window, "① This time the window has your name and email filled in: the ones you typed the first time. "
                       "② What changed.",
               highlight=[[text_box(window, "Name"), text_box(window, "Email")], text_box(window, "Message")],
               badges=True)
        QTest.mouseClick(button(window, "Save & Commit"), Qt.LeftButton)

    p.while_open(lambda: menu_item(file_menu, SAVE_AND_COMMIT).trigger(), in_the_window_again)
    p.note("Taskloom has no history view: you look at the history with any Git tool. Here, the git program reads what "
           "Taskloom wrote, in flows é:")
    p.text("Your two commits, newest first, each with the one file it changed (A: added, M: modified); then what is "
           "not committed: notes.txt, which you staged, still staged (A), and sales.csv untracked (??)",
           transcript(folder, "log --format=\"%h %an <%ae>  %s\" --name-status", "status --short"))
    p.check("① Your name and email were filled in", again == [(NAME, EMAIL)])
    p.check(f"Two commits, the second (“{SECOND}”) on top of the first, each changing only daily-report.yaml",
            subjects(folder, "daily-report.yaml") == [SECOND, FIRST]
            and git(folder, "rev-parse", "HEAD^").strip() == first
            and lines(folder, "log", "--format=", "--name-only") == ["daily-report.yaml"] * 2)
    p.check("notes.txt is still staged, not committed by Taskloom; sales.csv is still not committed; the flow shows no "
            "change", status(folder) == ["A  notes.txt", "?? sales.csv"] and not tab.doc.dirty)

    p.step("Open team-report.yaml, whose repository is in the middle of a Git merge, change it, and choose Save & "
           "Commit… in the File menu. In its folder, team flows, a merge of another branch stopped on a conflict in "
           "notes.txt, not resolved yet. The change: the block save writes out/team-totals.csv.")
    team = new_repo(base / "team flows")
    team_flow = write_flow(team / "team-report.yaml", "team-report")
    commit(team, "Add the team report", "team-report.yaml")
    conflicting_merge(team)
    team_tab = open_flow(win, monkeypatch, file_menu, team_flow)
    set_path(win, team_tab, "save", "out/team-totals.csv")
    before, merging = snapshot(team), git(team, "rev-parse", "HEAD", "MERGE_HEAD").split()
    boxes, titles = [], []

    def warning(parent, title, *args, **kwargs):  # the real box, and its title, which the screenshot cannot show
        titles.append(title)
        return REAL_WARNING(parent, title, *args, **kwargs)

    def look_at_the_box(box):
        boxes.append((type(box).__name__, box.text()))
        text = next(label for label in box.findChildren(QLabel) if label.text() == box.text())
        p.shot(box, "Taskloom does not commit in the middle of a merge: it says so before anything is written, and "
                    "saves nothing. File > Save still saves the flow.", highlight=[text])
        QTest.mouseClick(box.button(QMessageBox.Ok), Qt.LeftButton)

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "warning", warning)
        p.while_open(lambda: menu_item(file_menu, SAVE_AND_COMMIT).trigger(), look_at_the_box)
    p.note("The screenshot shows the box without its title bar, which the system draws: its title is “Cannot Save & "
           "Commit”.")
    p.check("A box titled “Cannot Save & Commit” says the repository is in the middle of a merge, and that nothing "
            "was saved; no Save & Commit window opened",
            titles == ["Cannot Save & Commit"]
            and [(kind, slashes(text)) for kind, text in boxes]
            == [("QMessageBox", slashes(f"The Git repository in {team} is in the middle of a merge.\n\nNothing was "
                                        f"saved. File → Save still saves it."))])
    p.check("Nothing was written in team flows: the flow file, the repository and its merge are as they were",
            snapshot(team) == before and git(team, "rev-parse", "HEAD", "MERGE_HEAD").split() == merging)
    p.check("The flow still has its unsaved change", team_tab.doc.dirty)

    # --- Save as, in the same folder -------------------------------------------------------------------------------
    p.step("Click the daily-report.yaml tab, change the report to read sales-eu.csv, and Save as… "
           "daily-report-eu.yaml, in the same folder. Click the block sales and type sales-eu.csv as its Path: that "
           "change is not committed. Then choose Save as… in the File menu and save the copy next to the original.")
    click_tab(win, tab)
    original = flow.read_bytes()
    set_path(win, tab, "sales", "sales-eu.csv")
    eu = folder / "daily-report-eu.yaml"
    save_as(monkeypatch, eu)
    menu_item(file_menu, "Save as…").trigger()
    said = win.statusBar().currentMessage()
    p.shot(win, "① The status bar: daily-report-eu.yaml was saved with the Git history of daily-report.yaml, 2 "
                "commits, and the change since daily-report.yaml's last commit is not committed yet.",
           highlight=[status_message(win)], badges=True, zoom=[status_message(win)])
    p.check("① The status bar says the copy has the original's history, 2 commits, and that the change since its last "
            "commit is not committed yet",
            said == "Saved daily-report-eu.yaml with the Git history of daily-report.yaml (2 commits). Changes since "
                    "daily-report.yaml's last commit are not committed yet.")
    p.check("The copy holds your change, and its tab shows it; daily-report.yaml is as it was",
            tab.doc.path == eu and not tab.doc.dirty and "sales-eu.csv" in eu.read_text(encoding="utf-8")
            and flow.read_bytes() == original)
    p.check("One more commit, which adds the copy as daily-report.yaml was last committed, and names that commit",
            count(folder) == 3 and subjects(folder, "daily-report-eu.yaml") == [COPY]
            and blob(folder, "daily-report-eu.yaml") == blob(folder, "daily-report.yaml")
            and git(folder, "log", "-1", "--format=%b").strip()
            == f"From daily-report.yaml at {git(folder, 'rev-parse', 'HEAD^').strip()}.")
    p.check("git log --follow of the copy lists the copy commit, then the original's two commits",
            subjects(folder, "daily-report-eu.yaml", follow=True) == [COPY, SECOND, FIRST])
    p.check("Git shows the copy as modified (M): your change is in it, not in its history; notes.txt is still staged",
            sorted(status(folder)) == [" M daily-report-eu.yaml", "?? sales.csv", "A  notes.txt"])

    p.step(f"Save & Commit your change on the copy, this time as a colleague, {BEN}, then look at the copy's history "
           f"with Git. Choose Save & Commit… in the File menu, type his name and email over yours, type “{EU}” and "
           f"click Save & Commit. With --follow, git log follows a file back through a copy or a rename.")

    def commit_the_copy(window):
        fill(window, name=BEN, email=BEN_EMAIL, message=EU)
        p.shot(window, "① The copy is committed to the same repository, in flows é. ② This time by your colleague: "
                       "his name and email. ③ What changed.",
               highlight=[intro_label(window), [text_box(window, "Name"), text_box(window, "Email")],
                          text_box(window, "Message")], badges=True)
        QTest.mouseClick(button(window, "Save & Commit"), Qt.LeftButton)

    p.while_open(lambda: menu_item(file_menu, SAVE_AND_COMMIT).trigger(), commit_the_copy)
    said = win.statusBar().currentMessage()
    p.text("In flows é: the copy's history with --follow, then without it, then the original's",
           transcript(folder, "log --follow --oneline -- daily-report-eu.yaml", "log --oneline -- daily-report-eu.yaml",
                      "log --oneline -- daily-report.yaml"))
    p.check("Your change is committed: nothing shows as modified any more, and notes.txt is still staged",
            said.startswith("Saved and committed daily-report-eu.yaml to ")
            and status(folder) == ["A  notes.txt", "?? sales.csv"])
    p.check(f"② The commit has {BEN}'s name and email as its author and committer",
            git(folder, "log", "-1", "--format=%an|%ae|%cn|%ce").strip() == f"{BEN}|{BEN_EMAIL}|{BEN}|{BEN_EMAIL}")
    p.check(f"With --follow, the copy's history is {BEN.split()[0]}'s commit, the copy commit, then the original's two "
            f"commits; without it, only the copy's own two",
            subjects(folder, "daily-report-eu.yaml", follow=True) == [EU, COPY, SECOND, FIRST]
            and subjects(folder, "daily-report-eu.yaml") == [EU, COPY])
    p.check("The original's history is still its own two commits", subjects(folder, "daily-report.yaml")
            == [SECOND, FIRST] == subjects(folder, "daily-report.yaml", follow=True))

    p.step(f"Open daily-report.yaml again, change it, and Save & Commit it: a commit on the original, made after the "
           f"copy. {BEN.split()[0]} is still at the editor. Click the block save and type out/daily-totals.csv as its "
           f"Path, then choose Save & Commit… in the File menu, type “{THIRD}” and click Save & Commit.")
    reopened = open_flow(win, monkeypatch, file_menu, flow)
    set_path(win, reopened, "save", "out/daily-totals.csv")
    by = []

    def commit_the_original(window):
        by.append((slashes(intro(window)), prefilled(window)))
        fill(window, message=THIRD)
        p.shot(window, f"① The window commits daily-report.yaml, the original, to the same repository, in flows é. "
                       f"② {BEN.split()[0]}'s name and email are filled in: the ones typed last. ③ What changed.",
               highlight=[intro_label(window), [text_box(window, "Name"), text_box(window, "Email")],
                          text_box(window, "Message")], badges=True)
        QTest.mouseClick(button(window, "Save & Commit"), Qt.LeftButton)

    p.while_open(lambda: menu_item(file_menu, SAVE_AND_COMMIT).trigger(), commit_the_original)
    said = win.statusBar().currentMessage()
    p.text(f"In flows é: the copy's history with --follow, then the original's. The original's new commit, “{THIRD}”, "
           f"is not in the copy's history",
           transcript(folder, "log --follow --oneline -- daily-report-eu.yaml", "log --oneline -- daily-report.yaml"))
    p.check(f"① ② The window commits daily-report.yaml to the repository in flows é, with {BEN.split()[0]}'s name and "
            f"email filled in",
            by == [(slashes(f"Saves daily-report.yaml and commits it, and only it, to the Git repository in {folder}, "
                            f"on branch {branch}."), (BEN, BEN_EMAIL))])
    p.check(f"The original's change is committed by {BEN}, on top of its two commits; notes.txt is still staged",
            said.startswith("Saved and committed daily-report.yaml to ") and not reopened.doc.dirty
            and subjects(folder, "daily-report.yaml") == [THIRD, SECOND, FIRST]
            and git(folder, "log", "-1", "--format=%an|%ae").strip() == f"{BEN}|{BEN_EMAIL}"
            and status(folder) == ["A  notes.txt", "?? sales.csv"])
    p.check(f"With --follow, the copy's history is still {BEN.split()[0]}'s commit, the copy commit, then the "
            f"original's two commits: the original's history up to the copy, never “{THIRD}”",
            subjects(folder, "daily-report-eu.yaml", follow=True) == [EU, COPY, SECOND, FIRST])

    # --- Save as, into another folder ------------------------------------------------------------------------------
    p.step(f"Choose Save as… and save daily-report.yaml as sales-report.yaml in another folder, another project, which "
           f"has no Git repository. {BEN.split()[0]} is still at the editor: Taskloom commits there with the name and "
           f"email typed last, his.")
    other = base / "another project"
    other.mkdir()
    p.check("another project has no Git repository", not in_a_repository(other))
    commits = count(folder)
    newest = int(git(folder, "log", "-1", "--format=%at", "--", "daily-report.yaml"))
    qtbot.waitUntil(lambda: time.time() >= newest + 1, timeout=5_000)  # so the dates there show when it was copied
    copy = other / "sales-report.yaml"
    save_as(monkeypatch, copy)
    menu_item(file_menu, "Save as…").trigger()
    said = win.statusBar().currentMessage()
    p.shot(win, "① The status bar: sales-report.yaml was saved with the Git history of daily-report.yaml, 3 commits, "
                "and Taskloom created a Git repository in another project, as there was none.",
           highlight=[status_message(win)], badges=True, zoom=[status_message(win)])
    p.check("① The status bar says the copy has the original's history, 3 commits, in a repository created in "
            "another project",
            slashes(said) == slashes(f"Saved sales-report.yaml with the Git history of daily-report.yaml (3 commits). "
                                     f"Created a Git repository in {other}."))
    p.check("Git finds the new repository in another project", in_a_repository(other)
            and Path(git(other, "rev-parse", "--show-toplevel").strip()).resolve() == other)
    p.check("The original's repository got no commit", count(folder) == commits)

    p.step("Look at the history in another project with Git, next to the original's.")
    log = "log --date=iso --format=\"%h %an %ad %s\""
    p.text("In flows é, the original's history", transcript(folder, f"{log} -- daily-report.yaml"))
    p.text(f"In another project, the copy's: the same versions, with their authors ({BEN} for the newest, {NAME} "
           f"for the other two), dates and messages, as new commits (new ids), committed by {BEN.split()[0]} when he "
           f"made the copy; one line of history; git fsck, Git's own check of a repository, finds nothing wrong; "
           f"nothing is modified",
           transcript(other, f"{log} -- sales-report.yaml", "log --date=iso --format=\"%h committed by %cn on %cd\"",
                      "log --graph --oneline", "fsck --strict", "status --short"))
    fsck = subprocess.run([GIT, "-C", str(other), "fsck", "--strict"], capture_output=True, timeout=120)
    p.check(f"Its history is the original's three commits, with their messages, authors and author dates: the two "
            f"oldest by {NAME} <{EMAIL}>, not by {BEN.split()[0]}, who made the copy",
            subjects(other, "sales-report.yaml") == [THIRD, SECOND, FIRST]
            == subjects(other, "sales-report.yaml", follow=True)
            and authored(other, "sales-report.yaml") == authored(folder, "daily-report.yaml")
            and lines(other, "log", "--format=%an|%ae") == [f"{BEN}|{BEN_EMAIL}"] + [f"{NAME}|{EMAIL}"] * 2)
    p.check(f"They are new commits, committed by {BEN} <{BEN_EMAIL}> when he made the copy, after their author dates",
            not set(lines(other, "rev-list", "HEAD")) & set(lines(folder, "rev-list", "HEAD"))
            and lines(other, "log", "--format=%cn|%ce") == [f"{BEN}|{BEN_EMAIL}"] * 3
            and all(int(c) > int(a) for a, c in (e.split("|") for e in lines(other, "log", "--format=%at|%ct"))))
    p.check("The repository holds only sales-report.yaml, in one line of history, and git fsck finds no problem",
            lines(other, "ls-tree", "-r", "--name-only", "HEAD") == ["sales-report.yaml"]
            and lines(other, "rev-list", "--merges", "HEAD") == []
            and len(lines(other, "rev-list", "--max-parents=0", "HEAD")) == 1
            and fsck.returncode == 0 and not fsck.stdout.strip())
    p.check("Nothing shows as modified there", status(other) == [])
    clean(win)  # (closing the editor after the proof would ask about team-report.yaml's unsaved change)
