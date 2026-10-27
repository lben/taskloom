"""File → Save & Commit…: a window asks for a commit message (and the name and email to commit with); its Save &
Commit button saves the flow, then commits that flow file, and only it, to the Git repository of the flow's folder,
creating one there if there is none. Save as… then gives the copy the original's Git history up to the original's
last commit: one copy commit in the same repository, the versions replayed in another (or a new) one. Taskloom
carries its own Git, so no git program has to be installed.

What Taskloom did in Git is checked with the real git program (the oracle), never with Taskloom's own Git library.
Every repository here is the test's own, and Git config is the test's own: an empty global config file, no system
config, no Git variables of the developer's."""

import datetime as dt
import html
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QPushButton  # noqa: E402

from tests.test_paths_editor_editing import PYTHON, clean, menu, save_as  # noqa: E402
from tests.test_reusable_block import text_box  # noqa: E402
from tests.test_upgrade import FLOW as FIXABLE_FLOW  # noqa: E402

GIT = shutil.which("git")  # read once, before a test empties PATH
needs_git = pytest.mark.skipif(GIT is None, reason="checked with the real git program, which is not installed")

SAVE_AND_COMMIT = "Save && Commit…"  # the File menu item; Qt shows it as "Save & Commit…"
OK = "Save & Commit"  # the window's button, as shown
ANA = {"name": "Ana Lima", "email": "ana@example.com"}  # the name and email typed in the window
SETUP = ("-c", "user.name=Setup", "-c", "user.email=setup@example.com")  # the oracle's own commits
# The original's commits m1..m3, each with an author and an author date (and time zone) of its own; m2 has a body.
MESSAGES = ["m1", "m2\n\nWhy: a second version.", "m3"]
DATES = ["2026-01-05T09:30:00+05:30", "2026-02-06T10:40:00-03:00", "2026-03-07T11:50:00+00:00"]
COPY_AB = "Copy a.yaml to b.yaml (Save as)"
NOT_SAVED = ".\n\nNothing was saved. File → Save still saves it."  # after the reason Save & Commit refuses with


# --- isolation ---------------------------------------------------------------------------------------------------

def inside_a_repository(folder: Path) -> bool:
    if GIT is None:
        return any((p / ".git").exists() for p in (folder, *folder.parents))
    found = subprocess.run([GIT, "-C", str(folder), "rev-parse", "--git-dir"], capture_output=True, timeout=60)
    return found.returncode == 0


@pytest.fixture(autouse=True)
def git_config(tmp_path, monkeypatch) -> Path:
    """The test's own Git config: this empty global config file, no system config, no GIT_* variables."""
    config = tmp_path / "gitconfig"
    config.write_bytes(b"")
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    if inside_a_repository(tmp_path):
        pytest.skip(f"{tmp_path} is inside a Git repository: these tests need folders outside any")
    return config


def known_identity(config: Path):
    """The user's Git config gives the name and email to commit with."""
    config.write_bytes(b"[user]\n\tname = Ana Lima\n\temail = ana@example.com\n")


# --- the oracle: the real git program ----------------------------------------------------------------------------

def git_bytes(folder, *args) -> bytes:
    done = subprocess.run([GIT, "-c", "core.quotepath=off", "-C", str(folder), *map(str, args)],
                          capture_output=True, timeout=60)
    assert done.returncode == 0, f"git {' '.join(map(str, args))}: {done.stderr.decode('utf-8', 'replace')}"
    return done.stdout


def git(folder, *args) -> str:
    return git_bytes(folder, *args).decode("utf-8")


def lines(folder, *args) -> list[str]:
    return [line for line in git(folder, *args).splitlines() if line]


def new_repo(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    git(folder, "init", "-q")
    return folder.resolve()


def commit(folder, message, *paths, author=None, date=None) -> str:
    """The oracle commits `paths`, and only them; the new commit's id."""
    git(folder, "add", "--", *paths)
    extra = [*(["--author", author] if author else []), *(["--date", date] if date else [])]
    git(folder, *SETUP, "commit", "-q", "-m", message, *extra, "--", *paths)
    return head(folder)


def head(folder) -> str:
    return git(folder, "rev-parse", "HEAD").strip()


def count(folder) -> int:
    return int(git(folder, "rev-list", "--count", "HEAD"))


def subjects(folder, path, follow=False) -> list[str]:
    return lines(folder, "log", *(["--follow"] if follow else []), "--format=%s", "--", path)


def authored(folder, path) -> list[str]:
    """Each commit of `path`, newest first: its author, email, date (as stored: seconds, time zone) and message."""
    out = git(folder, "log", "-z", "--date=raw", "--format=%an|%ae|%ad|%B", "--", path)
    return [entry for entry in out.split("\0") if entry]


def status(folder) -> list[str]:
    return lines(folder, "status", "--porcelain")


def blob(folder, path) -> str:
    return git(folder, "rev-parse", f"HEAD:{path}").strip()


def conflicting_merge(folder):
    """A real merge of a branch that changed notes.txt another way: it stops with a conflict, MERGE_HEAD left."""
    notes = folder / "notes.txt"
    notes.write_bytes(b"base\n")
    commit(folder, "base", "notes.txt")
    branch = git(folder, "symbolic-ref", "--short", "HEAD").strip()
    git(folder, "checkout", "-q", "-b", "other")
    notes.write_bytes(b"theirs\n")
    commit(folder, "theirs", "notes.txt")
    git(folder, "checkout", "-q", branch)
    notes.write_bytes(b"ours\n")
    commit(folder, "ours", "notes.txt")
    merged = subprocess.run([GIT, *SETUP, "-C", str(folder), "merge", "other"], capture_output=True, timeout=60)
    assert merged.returncode != 0 and (folder / ".git" / "MERGE_HEAD").exists(), merged.stdout


# --- flows -------------------------------------------------------------------------------------------------------

def write_flow(path: Path, x=0, crlf=False) -> Path:
    """A flow file with one Python Code block at (x, 0); with CRLF line ends if crlf (edited by hand on Windows)."""
    text = yaml.safe_dump({"name": "report", "blocks": {"python": {**PYTHON, "ui": {"x": x, "y": 0}}}, "edges": []},
                          sort_keys=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", "\r\n" if crlf else "\n").encode("utf-8"))
    return path.resolve()


def edit(tab, x):
    """An unsaved change: the block moved."""
    tab.doc.move_blocks({"python": (x, 0)})


def saved_x(path) -> int:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["blocks"]["python"]["ui"]["x"]


def versions(win, flow: Path, messages):
    """`flow` opened in the editor and, once per message, changed, saved with File → Save and committed by the
    oracle with that message, an author and an author date of its own. The tab and the commits' ids."""
    tab = win.open_flow(flow)
    ids = []
    for i, message in enumerate(messages, 1):
        edit(tab, 100 * i)
        assert win.save(tab)
        ids.append(commit(flow.parent, message, flow.name, author=f"Setup {i} <setup{i}@example.com>",
                          date=DATES[(i - 1) % len(DATES)]))
    return tab, ids


def snapshot(folder: Path) -> dict:
    """Every file under `folder` (its .git too), with its bytes."""
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


# --- the editor --------------------------------------------------------------------------------------------------

def slashes(text: str) -> str:
    """A text the same whichever way the paths in it are written (Windows shows them with backslashes)."""
    return text.replace("\\", "/")


def shown(text: str) -> str:
    """A menu or button text as Qt shows it (&& is an &; a single & marks the shortcut letter)."""
    return re.sub(r"&(.)", r"\1", text)


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def labels(window) -> list[str]:
    """The texts the window shows."""
    return [plain(w.text()) for w in window.findChildren(QLabel) if w.isVisible() and w.text().strip()]


def intro(window) -> str:
    return next(text for text in labels(window) if text.startswith("Saves "))


def click(window, button: str):
    (pressed,) = [b for b in window.findChildren(QPushButton) if b.isVisible() and shown(b.text()) == button]
    pressed.click()


def type_into(box, text: str):
    """Clear the box, then type `text` as the keyboard does (Return between lines)."""
    box.clear()
    for i, line in enumerate(text.split("\n")):
        if i:
            QTest.keyClick(box, Qt.Key_Return)
        QTest.keyClicks(box, line)


def fill(window, name=None, email=None, message=None):
    for label, text in (("Name", name), ("Email", email), ("Message", message)):
        if text is not None:
            type_into(text_box(window, label), text)


def prefilled(window) -> tuple[str, str]:
    return text_box(window, "Name").text(), text_box(window, "Email").text()


def windows_opened(win, open_it, do=None) -> list:
    """Call open_it() (a menu item). Each window that opens while it runs (a window waits for you inside the call
    that opens it) is handed to do(window), then closed if it is still open. The windows that opened."""
    opened, errors = [], []

    def look():
        for window in QApplication.topLevelWidgets():
            if isinstance(window, QDialog) and window.isVisible() and window not in opened:
                opened.append(window)
                try:
                    if do:
                        do(window)
                except BaseException as e:  # (not lost in Qt's event loop)
                    errors.append(e)
                finally:
                    if window.isVisible():  # (never left waiting for a click)
                        window.reject()

    timer = QTimer(win)
    timer.timeout.connect(look)
    timer.start(20)
    try:
        open_it()
    finally:
        timer.stop()
        timer.deleteLater()
    if errors:
        raise errors[0]
    return opened


def save_and_commit(win, name=None, email=None, message=None, button=OK, check=None) -> list:
    """File → Save & Commit…: in the window, check(window) first if given, then the boxes given filled in and
    `button` clicked. The windows that opened (none when it was refused before the window)."""
    def do(window):
        if check:
            check(window)
        fill(window, name, email, message)
        click(window, button)
    return windows_opened(win, lambda: menu(win, SAVE_AND_COMMIT).trigger(), do)


def save_as_copy(win, monkeypatch, path):
    """File → Save as…, choosing `path` in the file dialog."""
    save_as(monkeypatch, path)
    menu(win, "Save as…").trigger()


def one_box(win) -> tuple[str, str, str]:
    """The one message box shown (its kind, title and text); it is answered here."""
    (box,) = win.message_boxes
    win.message_boxes.clear()
    kind, (title, text) = box
    return kind, title, text


def remembered(win) -> tuple:
    settings = QSettings(str(win.home.root / "editor.ini"), QSettings.IniFormat)
    return settings.value("git/name"), settings.value("git/email")


def status_bar(win) -> str:
    return win.statusBar().currentMessage()


# --- the menu item -----------------------------------------------------------------------------------------------

def test_save_and_commit_is_in_the_file_menu_after_save_as_with_no_shortcut_and_enabled(win, tmp_path):
    """§5: "Save & Commit…" directly after "Save as…" and before "Close flow", no shortcut, enabled for a flow
    that is saved (saved does not mean committed)."""
    win.open_flow(write_flow(tmp_path / "nightly" / "report.yaml"))
    (file_menu,) = [a.menu() for a in win.menuBar().actions() if shown(a.text()) == "File"]
    actions = file_menu.actions()
    at = [a.text() for a in actions].index("Save as…")

    assert [a.text() for a in actions[at + 1:at + 3]] == [SAVE_AND_COMMIT, "Close flow"]
    assert shown(actions[at + 1].text()) == "Save & Commit…"
    assert actions[at + 1].shortcuts() == [] and actions[at + 1].isEnabled()


# --- Save & Commit -----------------------------------------------------------------------------------------------

TYPED = "\nLoad the nightly numbers   \n\n# kept: a line that starts with a hash\n- and the totals  \n\n"
COMMITTED = "Load the nightly numbers\n\n# kept: a line that starts with a hash\n- and the totals\n"


def test_save_and_commit_creates_a_repository_in_the_flow_folder_and_commits_only_the_flow(
        win, tmp_path, monkeypatch, git_config):
    """T1 (§5, §6, §8, §9, §10, §11, §3; the user's case): a folder with no repository and a data file beside the
    flow, no git program on PATH. The window says a new repository will be created; its button saves the flow
    (with LF line ends, though the file had CRLF) and commits it, and only it, in one new commit, with the
    message, name and email typed; the name and email are remembered; no Git config is written."""
    flow = write_flow(tmp_path / "nightly" / "report.yaml", crlf=True)
    folder = flow.parent
    (folder / "data.csv").write_bytes(b"day,amount\n1,10\n")
    tab = win.open_flow(flow)
    edit(tab, 300)
    no_programs = tmp_path / "no programs"
    no_programs.mkdir()
    path = os.environ["PATH"]
    monkeypatch.setenv("PATH", str(no_programs))  # no git program to be found
    seen = {}
    started = time.time()

    windows = save_and_commit(win, **ANA, message=TYPED,
                              check=lambda w: seen.update(title=w.windowTitle(), intro=intro(w),
                                                          prefilled=prefilled(w)))
    finished = time.time()
    monkeypatch.setenv("PATH", path)

    assert len(windows) == 1 and seen["title"] == "Save & Commit"
    assert seen["prefilled"] == ("", "")  # nothing remembered, no name or email in Git config
    assert slashes(f"Saves report.yaml and commits it, and only it, to a new Git repository in {folder} (there is "
                   f"none yet), on branch master.") in slashes(seen["intro"]), seen["intro"]
    assert "The flow's folder is inside that repository." not in seen["intro"]
    assert win.message_boxes == [] and not tab.doc.dirty
    written = flow.read_bytes()
    assert b"\r" not in written and saved_x(flow) == 300
    assert (folder / ".git" / "HEAD").is_file()
    assert remembered(win) == ("Ana Lima", "ana@example.com")
    assert git_config.read_bytes() == b""
    said = re.fullmatch(r"Created a Git repository in (.+)\. Saved and committed report\.yaml to (.+) "
                        r"\(master, ([0-9a-f]{7})\)\.", status_bar(win))
    assert said and slashes(said[1]) == slashes(said[2]) == slashes(str(folder)), status_bar(win)
    if GIT is None:
        return

    assert said[3] == head(folder)[:7]
    assert Path(git(folder, "rev-parse", "--show-toplevel").strip()).resolve() == folder
    assert git(folder, "symbolic-ref", "--short", "HEAD").strip() == "master"
    assert count(folder) == 1
    assert lines(folder, "ls-tree", "-r", "--name-only", "HEAD") == ["report.yaml"]
    assert git_bytes(folder, "cat-file", "blob", "HEAD:report.yaml") == written  # the file's bytes as written
    assert status(folder) == ["?? data.csv"]  # the data file is not committed; the flow shows no change
    headers, message = git(folder, "cat-file", "commit", "HEAD").split("\n\n", 1)
    assert message == COMMITTED
    for role in ("author", "committer"):
        (line,) = [line for line in headers.splitlines() if line.startswith(f"{role} ")]
        who, when, zone = re.fullmatch(rf"{role} (.+) (\d+) ([+-]\d{{4}})", line).groups()
        assert who == "Ana Lima <ana@example.com>"
        assert started - 2 <= int(when) <= finished + 2  # now
        assert zone == dt.datetime.fromtimestamp(int(when)).astimezone().strftime("%z")  # the local UTC offset


@needs_git
def test_save_and_commit_in_a_bigger_repository_commits_only_the_flow_and_leaves_what_you_staged(win, tmp_path):
    """T2 (§8, §9; edge paths: the flow's folder inside a bigger repository, a flow not tracked yet, spaces and
    non-ASCII in names): the window names the repository's root and says the flow's folder is inside it; the one
    new commit changes only the flow, on top of HEAD; a file you staged stays staged."""
    project = new_repo(tmp_path / "project")
    (project / "README.txt").write_bytes(b"the project\n")
    p0 = commit(project, "p0", "README.txt")
    flow = write_flow(project / "flows é" / "sales report.yaml")  # not tracked yet
    (project / "other.txt").write_bytes(b"staged by you\n")
    git(project, "add", "other.txt")
    branch = git(project, "symbolic-ref", "--short", "HEAD").strip()
    tab = win.open_flow(flow)
    edit(tab, 300)
    seen = []

    save_and_commit(win, **ANA, message="Add the sales report", check=lambda w: seen.append(intro(w)))

    assert slashes(f"Saves sales report.yaml and commits it, and only it, to the Git repository in {project}, on "
                   f"branch {branch}.") in slashes(seen[0]), seen[0]
    assert "The flow's folder is inside that repository." in seen[0]
    assert win.message_boxes == [] and not tab.doc.dirty
    assert count(project) == 2 and git(project, "rev-parse", "HEAD^").strip() == p0
    assert lines(project, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == ["flows é/sales report.yaml"]
    assert sorted(lines(project, "ls-tree", "-r", "--name-only", "HEAD")) == ["README.txt", "flows é/sales report.yaml"]
    assert status(project) == ["A  other.txt"]  # still staged, not committed
    assert subjects(project, "flows é/sales report.yaml") == ["Add the sales report"]
    assert not (flow.parent / ".git").exists()  # no repository of its own
    assert slashes(status_bar(win)) == slashes(f"Saved and committed sales report.yaml to {project} "
                                                f"({branch}, {head(project)[:7]}).")


def test_cancel_saves_nothing_and_commits_nothing(win, tmp_path, monkeypatch):
    """T3 (§6): Cancel in the window: the file as it was, no repository, the tab still has its unsaved changes. An
    untitled flow gets the "Save flow" file dialog first; Cancel there: nothing happens, no window opens."""
    flow = write_flow(tmp_path / "nightly" / "report.yaml")
    before = flow.read_bytes()
    tab = win.open_flow(flow)
    edit(tab, 300)

    windows = save_and_commit(win, **ANA, message="Not this time", button="Cancel")

    assert len(windows) == 1
    assert flow.read_bytes() == before and not (flow.parent / ".git").exists() and tab.doc.dirty
    assert win.message_boxes == []

    asked = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: asked.append(a[1]) or ("", "")))
    untitled = win.new_flow()
    untitled.doc.add_block("logic.python", 0, 0, dict(PYTHON["config"]))

    assert save_and_commit(win, **ANA, message="Not this time") == []
    assert asked == ["Save flow"] and untitled.doc.path is None and untitled.doc.dirty
    assert list(tmp_path.rglob(".git")) == [] and win.message_boxes == []
    clean(win)


def test_the_window_needs_a_message_and_a_name_and_an_email_before_it_saves(win, tmp_path):
    """T4 (§8): OK with an empty message, then with no name, then with no email: the window says why in red,
    stays open and saves nothing. Filled in, it saves and commits."""
    flow = write_flow(tmp_path / "nightly" / "report.yaml")
    before = flow.read_bytes()
    tab = win.open_flow(flow)
    edit(tab, 300)
    seen = []

    def nothing_saved():
        return flow.read_bytes() == before and not (flow.parent / ".git").exists()

    def do(window):
        reasons = ("Write what changed.", "Give your name and email for the commit.")
        seen.append(("at first", [r for r in reasons if r in labels(window)]))
        fill(window, **ANA, message="  \n   \n")  # only spaces
        click(window, OK)
        seen.append(("no message", window.isVisible(), "Write what changed." in labels(window), nothing_saved()))
        fill(window, name="", message="Move the block")
        click(window, OK)
        seen.append(("no name", window.isVisible(), "Give your name and email for the commit." in labels(window),
                     nothing_saved()))
        fill(window, name="Ana Lima", email="")
        click(window, OK)
        seen.append(("no email", window.isVisible(), "Give your name and email for the commit." in labels(window),
                     nothing_saved()))
        fill(window, email="ana@example.com")
        click(window, OK)
        seen.append(("filled in", window.isVisible()))

    windows_opened(win, lambda: menu(win, SAVE_AND_COMMIT).trigger(), do)

    assert seen == [("at first", []), ("no message", True, True, True), ("no name", True, True, True),
                    ("no email", True, True, True), ("filled in", False)]
    assert saved_x(flow) == 300 and not tab.doc.dirty and (flow.parent / ".git" / "HEAD").is_file()
    assert status_bar(win).startswith("Created a Git repository in")
    if GIT:
        assert subjects(flow.parent, "report.yaml") == ["Move the block"]


def test_save_and_commit_with_nothing_new_makes_no_commit_and_after_a_change_commits_on_top(win, tmp_path,
                                                                                           monkeypatch):
    """T5 (§6, §10, §4): a new flow, saved through the file dialog and committed; again with no change: no commit,
    "Nothing new to commit". A change saved with File → Save is not committed; the next Save & Commit commits it
    (it compares with the last commit, not with unsaved changes), on top of the last commit."""
    folder = tmp_path / "nightly"
    folder.mkdir()
    flow = folder.resolve() / "nightly.yaml"
    folder = flow.parent
    asked = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: asked.append(a[1]) or (str(flow), "")))
    tab = win.new_flow()
    block = tab.doc.add_block("logic.python", 0, 0, dict(PYTHON["config"]))

    save_and_commit(win, **ANA, message="A new flow")

    assert asked == ["Save flow"] and tab.doc.path == flow and not tab.doc.dirty
    assert (folder / ".git" / "HEAD").is_file()
    first = head(folder) if GIT else None

    assert len(save_and_commit(win, **ANA, message="Nothing changed")) == 1
    assert status_bar(win) == "Saved nightly.yaml. Nothing new to commit: it is the same as in its last commit."
    assert win.message_boxes == []
    if GIT:
        assert count(folder) == 1 and head(folder) == first

    tab.doc.move_blocks({block: (400, 0)})
    menu(win, "Save").trigger()
    assert not tab.doc.dirty
    if GIT:
        assert count(folder) == 1  # File → Save does not commit

    save_and_commit(win, **ANA, message="Moved the block")

    assert win.message_boxes == []
    said = re.fullmatch(r"Saved and committed nightly\.yaml to (.+) \(master, ([0-9a-f]{7})\)\.", status_bar(win))
    assert said and slashes(said[1]) == slashes(str(folder)), status_bar(win)
    if GIT:
        assert count(folder) == 2 and git(folder, "rev-parse", "HEAD^").strip() == first
        assert said[2] == head(folder)[:7]
        assert subjects(folder, "nightly.yaml") == ["Moved the block", "A new flow"] and status(folder) == []


def refusal(case, tmp_path, monkeypatch) -> tuple[Path, Path, str]:
    """A flow (report.yaml) in a folder whose repository Save & Commit refuses: the folder, the flow, the reason."""
    folder = tmp_path / "flows"
    flow = write_flow(folder / "report.yaml")
    folder = flow.parent
    if case == "home folder":
        monkeypatch.setenv("HOME", str(folder))  # (Path.home() reads it)
        monkeypatch.setenv("USERPROFILE", str(folder))  # (on Windows)
        return folder, flow, (f"Taskloom does not create a Git repository in {folder}: every folder in it would be "
                              f"in that repository. Keep the flow in a folder of its own")
    new_repo(folder)
    if case == "ignored":
        (folder / ".gitignore").write_bytes(b"*.yaml\n")
        commit(folder, "ignore flows", ".gitignore")
        return folder, flow, f"report.yaml is ignored by Git in {folder} (a .gitignore or exclude rule matches it)"
    commit(folder, "m1", "report.yaml")
    if case == "detached HEAD":
        git(folder, "checkout", "-q", "--detach")
        return folder, flow, f"The Git repository in {folder} has no branch checked out (detached HEAD)"
    if case == "merge":
        conflicting_merge(folder)
        return folder, flow, f"The Git repository in {folder} is in the middle of a merge"
    assert case == "split index"
    git(folder, "update-index", "--split-index")
    return folder, flow, f"Cannot use the Git repository in {folder}: "


@pytest.mark.parametrize("case", [
    pytest.param("detached HEAD", marks=needs_git),
    pytest.param("merge", marks=needs_git),
    pytest.param("ignored", marks=needs_git),
    "home folder",
    pytest.param("split index", marks=needs_git),
])
def test_save_and_commit_refuses_before_saving_when_the_repository_cannot_take_the_commit(
        win, tmp_path, monkeypatch, case):
    """T6 (§7; edge paths: detached HEAD, a merge in progress, an ignored untracked flow, the home folder, a split
    index): "Cannot Save & Commit" with the reason, before any window; nothing is written or created (the flow
    file, HEAD, MERGE_HEAD and the index as they were; no repository in the home folder), the tab as it was."""
    folder, flow, reason = refusal(case, tmp_path, monkeypatch)
    tab = win.open_flow(flow)
    edit(tab, 300)
    before = snapshot(folder)

    windows = save_and_commit(win, **ANA, message="Not allowed")

    kind, title, text = one_box(win)
    assert (kind, title) == ("warning", "Cannot Save & Commit")
    if case == "split index":
        assert slashes(text).startswith(slashes(reason)) and text.endswith(NOT_SAVED), text
    else:
        assert slashes(text) == slashes(reason + NOT_SAVED)
    assert windows == []
    assert snapshot(folder) == before
    assert tab.doc.dirty and tab.doc.path == flow
    clean(win)


@needs_git
def test_a_held_index_lock_leaves_the_flow_saved_but_not_committed_and_a_retry_commits(win, tmp_path):
    """T7 (§10; edge path: index.lock held): "Saved, not committed" with the lock reason; the file is saved and
    the tab clean, HEAD, the index and the lock as they were. Once the lock is gone, Save & Commit commits."""
    folder = new_repo(tmp_path / "flows")
    flow = write_flow(folder / "report.yaml")
    commit(folder, "m1", "report.yaml")
    tab = win.open_flow(flow)
    edit(tab, 400)
    lock = folder / ".git" / "index.lock"
    lock.write_bytes(b"")  # another program is using the repository
    first, index = head(folder), (folder / ".git" / "index").read_bytes()

    save_and_commit(win, **ANA, message="m2")

    kind, title, text = one_box(win)
    assert (kind, title) == ("warning", "Saved, not committed")
    assert text.startswith("report.yaml is saved, but it was not committed: another program is using this Git "
                           "repository ("), text
    assert text.endswith("index.lock exists); close it or wait.\n\nFix that, then Save & Commit again."), text
    assert saved_x(flow) == 400 and not tab.doc.dirty
    assert lock.exists() and head(folder) == first and (folder / ".git" / "index").read_bytes() == index
    assert git(folder, "diff", "--cached", "--name-only") == ""

    lock.unlink()
    save_and_commit(win, **ANA, message="m2")

    assert win.message_boxes == []
    assert subjects(folder, "report.yaml") == ["m2", "m1"] and status(folder) == []


def test_the_name_and_email_come_from_git_config_then_from_what_you_last_typed(win, tmp_path, git_config):
    """T8 (§11): user.name and user.email of the Git config fill in the window; after a commit, the values typed
    there fill it in the next time, even when the Git config changed. Taskloom writes no Git config."""
    git_config.write_bytes(b"[user]\n\tname = Config Name\n\temail = config@example.com\n")
    flow = write_flow(tmp_path / "nightly" / "report.yaml")
    folder = flow.parent
    tab = win.open_flow(flow)
    edit(tab, 300)
    seen = []

    save_and_commit(win, name="Typed Name", message="first", check=lambda w: seen.append(prefilled(w)))

    assert seen == [("Config Name", "config@example.com")]
    assert remembered(win) == ("Typed Name", "config@example.com")
    assert git_config.read_bytes() == b"[user]\n\tname = Config Name\n\temail = config@example.com\n"
    changed = b"[user]\n\tname = Changed Name\n\temail = changed@example.com\n"
    git_config.write_bytes(changed)
    edit(tab, 400)

    save_and_commit(win, message="second", check=lambda w: seen.append(prefilled(w)))

    assert seen[1] == ("Typed Name", "config@example.com")
    assert git_config.read_bytes() == changed
    assert win.message_boxes == [] and not tab.doc.dirty
    if GIT:
        assert lines(folder, "log", "--format=%an|%ae|%s") == ["Typed Name|config@example.com|second",
                                                               "Typed Name|config@example.com|first"]
        assert "[user]" not in (folder / ".git" / "config").read_text(encoding="utf-8")


# --- Save as carries the original's history ----------------------------------------------------------------------

@needs_git
def test_save_as_in_the_same_folder_gives_the_copy_the_original_history_with_one_copy_commit(
        win, tmp_path, monkeypatch, git_config):
    """T9 (§12, §13, §16b, §17; the user's case): a.yaml with commits m1..m3 and an unsaved change, Save as b.yaml
    in the same folder: one copy commit, so git log --follow of the copy lists it and m3, m2, m1; the copy holds
    the current content (shown as modified), the original file is unchanged, a staged file stays staged. Later
    commits of the original never show in the copy's history."""
    known_identity(git_config)
    folder = new_repo(tmp_path / "flows")
    a = write_flow(folder / "a.yaml")
    tab, ids = versions(win, a, MESSAGES)
    (folder / "staged.txt").write_bytes(b"staged by you\n")
    git(folder, "add", "staged.txt")
    before, original = count(folder), a.read_bytes()
    edit(tab, 900)  # not saved, not committed

    save_as_copy(win, monkeypatch, folder / "b.yaml")

    assert win.message_boxes == []
    assert count(folder) == before + 1
    assert subjects(folder, "b.yaml", follow=True) == [COPY_AB, "m3", "m2", "m1"]
    assert subjects(folder, "b.yaml") == [COPY_AB]
    assert git(folder, "log", "-1", "--format=%b").strip() == f"From a.yaml at {ids[-1]}."  # m3, in full
    assert git(folder, "log", "-1", "--format=%an|%ae|%cn|%ce").strip() == "Ana Lima|ana@example.com|Ana Lima|ana@example.com"
    assert blob(folder, "b.yaml") == blob(folder, "a.yaml")  # its last committed version, not the unsaved change
    assert sorted(status(folder)) == [" M b.yaml", "A  staged.txt"]
    assert a.read_bytes() == original and saved_x(folder / "b.yaml") == 900
    assert status_bar(win) == ("Saved b.yaml with the Git history of a.yaml (3 commits). Changes since a.yaml's "
                               "last commit are not committed yet.")

    edit(tab, 1000)
    save_and_commit(win, **ANA, message="b1")  # on b.yaml
    a_tab = win.open_flow(a)
    edit(a_tab, 1100)
    save_and_commit(win, **ANA, message="a4")  # on a.yaml

    assert win.message_boxes == []
    assert subjects(folder, "b.yaml", follow=True) == ["b1", COPY_AB, "m3", "m2", "m1"]  # never a4
    assert subjects(folder, "a.yaml") == ["a4", "m3", "m2", "m1"]
    assert status(folder) == ["A  staged.txt"]


@needs_git
def test_save_as_over_a_tracked_file_first_commits_its_removal(win, tmp_path, monkeypatch, git_config):
    """T10 (§16a): Save as over b.yaml, which has commits of its own (b1, b2): a commit removing it, then the copy
    commit; the copy's history (--follow) is the original's, b.yaml's old commits stay in its plain log."""
    known_identity(git_config)
    folder = new_repo(tmp_path / "flows")
    b = folder / "b.yaml"
    b.write_bytes(b"first b\n")
    commit(folder, "b1", "b.yaml")
    b.write_bytes(b"second b\n")
    commit(folder, "b2", "b.yaml")
    a = write_flow(folder / "a.yaml")
    versions(win, a, MESSAGES)
    before = count(folder)

    save_as_copy(win, monkeypatch, b)

    assert win.message_boxes == []
    assert count(folder) == before + 2
    assert subjects(folder, "b.yaml", follow=True) == [COPY_AB, "m3", "m2", "m1"]
    assert subjects(folder, "b.yaml") == [COPY_AB, "Remove b.yaml: Save as of a.yaml replaces it", "b2", "b1"]
    assert blob(folder, "b.yaml") == blob(folder, "a.yaml") and status(folder) == []
    assert status_bar(win) == "Saved b.yaml with the Git history of a.yaml (3 commits)."


@needs_git
def test_save_as_into_a_folder_with_no_repository_creates_one_with_the_versions_replayed(
        win, tmp_path, monkeypatch, git_config):
    """T11 (§15, §16c, §17; edge path: Save as into a folder with no repository, spaces and non-ASCII in names):
    a repository is created there, holding one commit per version of the original, oldest first, each with the
    original's message, author and author date; committed by you, with new ids; only the copy in it; git fsck
    clean, nothing shown as changed; the original's repository not written."""
    known_identity(git_config)
    folder = new_repo(tmp_path / "flows")
    a = write_flow(folder / "a.yaml")
    versions(win, a, MESSAGES)
    other = tmp_path / "copies é"
    other.mkdir()
    other = other.resolve()
    copy = other / "sales copy.yaml"

    save_as_copy(win, monkeypatch, copy)

    assert win.message_boxes == []
    assert (other / ".git").is_dir()
    assert Path(git(other, "rev-parse", "--show-toplevel").strip()).resolve() == other
    assert subjects(other, copy.name) == ["m3", "m2", "m1"] == subjects(other, copy.name, follow=True)
    assert authored(other, copy.name) == authored(folder, "a.yaml")
    assert lines(other, "log", "--format=%cn|%ce") == ["Ana Lima|ana@example.com"] * 3
    assert not set(lines(other, "rev-list", "HEAD")) & set(lines(folder, "rev-list", "HEAD"))
    assert lines(other, "ls-tree", "-r", "--name-only", "HEAD") == [copy.name]
    assert lines(other, "rev-list", "--merges", "HEAD") == [] and len(lines(other, "rev-list", "--max-parents=0",
                                                                             "HEAD")) == 1
    git(other, "fsck", "--strict")
    assert status(other) == []
    assert count(folder) == 3 and status(folder) == []
    assert slashes(status_bar(win)) == slashes(f"Saved sales copy.yaml with the Git history of a.yaml (3 commits). "
                                                f"Created a Git repository in {other}.")


@needs_git
def test_save_as_into_another_repository_replays_the_versions_on_top_of_its_head(
        win, tmp_path, monkeypatch, git_config):
    """T12 (§16c): into a repository with its own commit p1 and a staged file, the copy at flows/sub/y.yaml: the
    versions are replayed on top of p1 (linear: no merge, one root), each changing only the copy; the staged
    file stays staged; git fsck clean."""
    known_identity(git_config)
    project = new_repo(tmp_path / "project")
    (project / "p.txt").write_bytes(b"p\n")
    p1 = commit(project, "p1", "p.txt")
    (project / "staged.txt").write_bytes(b"staged by you\n")
    git(project, "add", "staged.txt")
    (project / "flows" / "sub").mkdir(parents=True)
    folder = new_repo(tmp_path / "flows")
    a = write_flow(folder / "a.yaml")
    versions(win, a, MESSAGES)

    save_as_copy(win, monkeypatch, project / "flows" / "sub" / "y.yaml")

    assert win.message_boxes == []
    chain = lines(project, "rev-list", "--reverse", "HEAD")  # oldest first
    assert len(chain) == 4 and chain[0] == p1 and git(project, "rev-parse", f"{chain[1]}^").strip() == p1
    assert subjects(project, "flows/sub/y.yaml") == ["m3", "m2", "m1"]
    assert lines(project, "log", "--format=", "--name-only", f"{p1}..HEAD") == ["flows/sub/y.yaml"] * 3
    assert lines(project, "rev-list", "--merges", "HEAD") == []
    assert lines(project, "rev-list", "--max-parents=0", "HEAD") == [p1]
    assert sorted(lines(project, "ls-tree", "-r", "--name-only", "HEAD")) == ["flows/sub/y.yaml", "p.txt"]
    assert status(project) == ["A  staged.txt"]
    git(project, "fsck", "--strict")
    assert not (project / "flows" / "sub" / ".git").exists()
    assert status_bar(win) == "Saved y.yaml with the Git history of a.yaml (3 commits)."


@needs_git
def test_a_copy_of_a_copy_carries_the_copy_commits_and_the_original_commits(win, tmp_path, monkeypatch, git_config):
    """T13 (§13; edge path: a copy of a copy): a.yaml (m1..m3) saved as b.yaml in the same folder, b1 committed on
    b.yaml, then b.yaml saved as into another folder: its history there is b1, m3, m2, m1."""
    known_identity(git_config)
    folder = new_repo(tmp_path / "flows")
    a = write_flow(folder / "a.yaml")
    tab, _ = versions(win, a, MESSAGES)
    save_as_copy(win, monkeypatch, folder / "b.yaml")
    edit(tab, 700)
    assert win.save(tab)
    commit(folder, "b1", "b.yaml")
    other = tmp_path / "copies"
    other.mkdir()
    other = other.resolve()

    save_as_copy(win, monkeypatch, other / "c.yaml")

    assert win.message_boxes == [] and (other / ".git").is_dir()
    assert subjects(other, "c.yaml") == ["b1", "m3", "m2", "m1"]
    assert slashes(status_bar(win)) == slashes(f"Saved c.yaml with the Git history of b.yaml (4 commits). Created a "
                                                f"Git repository in {other}.")


def test_save_as_of_a_flow_in_no_repository_only_saves_the_copy(win, tmp_path, monkeypatch):
    """T14 (§14, §3): the original is in no repository: the copy is saved (LF line ends), no repository appears
    anywhere, no message box, nothing about Git in the status bar."""
    flow = write_flow(tmp_path / "plain" / "a.yaml")
    tab = win.open_flow(flow)
    edit(tab, 200)
    copy = tmp_path / "elsewhere" / "b.yaml"
    copy.parent.mkdir()

    save_as_copy(win, monkeypatch, copy)

    assert saved_x(copy) == 200 and b"\r" not in copy.read_bytes() and not tab.doc.dirty
    assert list(tmp_path.rglob(".git")) == []
    assert win.message_boxes == [] and "Git" not in status_bar(win)


@needs_git
def test_save_as_of_a_flow_never_committed_only_saves_the_copy(win, tmp_path, monkeypatch, git_config):
    """T14 (§14): the original is in a repository but was never committed: the copy is saved, no repository is
    created at the copy, the original's repository is not written, no box, nothing about Git in the status bar."""
    known_identity(git_config)
    folder = new_repo(tmp_path / "flows")
    (folder / "README.txt").write_bytes(b"flows\n")
    commit(folder, "p0", "README.txt")
    flow = write_flow(folder / "a.yaml")  # untracked
    tab = win.open_flow(flow)
    edit(tab, 200)
    copy = tmp_path / "elsewhere" / "b.yaml"
    copy.parent.mkdir()

    save_as_copy(win, monkeypatch, copy)

    assert saved_x(copy) == 200
    assert not (copy.parent / ".git").exists()
    assert count(folder) == 1 and status(folder) == ["?? a.yaml"]
    assert win.message_boxes == [] and "Git" not in status_bar(win)


@needs_git
def test_save_as_into_a_repository_in_the_middle_of_a_merge_saves_the_copy_without_history(
        win, tmp_path, monkeypatch, git_config):
    """T15 (§15, §17; edge path: a merge in progress): "Saved, history not copied" with the reason; the copy is
    saved; the target repository (HEAD, MERGE_HEAD, index) and the original's are as they were."""
    known_identity(git_config)
    folder = new_repo(tmp_path / "flows")
    a = write_flow(folder / "a.yaml")
    versions(win, a, MESSAGES)
    project = new_repo(tmp_path / "project")
    conflicting_merge(project)
    before = snapshot(project / ".git")

    save_as_copy(win, monkeypatch, project / "copy.yaml")

    kind, title, text = one_box(win)
    assert (kind, title) == ("warning", "Saved, history not copied")
    assert slashes(text) == slashes(f"copy.yaml is saved, but without the Git history of a.yaml: The Git repository "
                                    f"in {project} is in the middle of a merge.")
    assert saved_x(project / "copy.yaml") == 300
    assert snapshot(project / ".git") == before
    assert count(folder) == 3


@needs_git
def test_save_as_with_no_name_and_email_known_saves_the_copy_without_history_until_you_give_them(
        win, tmp_path, monkeypatch):
    """T15 (§15, §17): no name and email known yet: "Saved, history not copied" with the reason, the copy saved,
    no repository created for it. Once given in Save & Commit, Save as again over the copy carries the history."""
    folder = new_repo(tmp_path / "flows")
    a = write_flow(folder / "a.yaml")
    versions(win, a, MESSAGES)
    copies = tmp_path / "copies"
    copies.mkdir()
    copies = copies.resolve()
    copy = copies / "copy.yaml"

    save_as_copy(win, monkeypatch, copy)

    kind, title, text = one_box(win)
    assert (kind, title) == ("warning", "Saved, history not copied")
    assert text == ("copy.yaml is saved, but without the Git history of a.yaml: Taskloom does not know the name and "
                    "email to commit with yet: give them once in File → Save & Commit…, then Save as… again.")
    assert saved_x(copy) == 300 and not (copies / ".git").exists()

    win.open_flow(a)
    save_and_commit(win, **ANA, message="Nothing changed")  # a.yaml is as in its last commit
    assert status_bar(win) == "Saved a.yaml. Nothing new to commit: it is the same as in its last commit."
    save_as_copy(win, monkeypatch, copy)

    assert win.message_boxes == []
    assert subjects(copies, "copy.yaml") == ["m3", "m2", "m1"]
    assert slashes(status_bar(win)) == slashes(f"Saved copy.yaml with the Git history of a.yaml (3 commits). "
                                                f"Created a Git repository in {copies}.")


# --- line ends ---------------------------------------------------------------------------------------------------

def test_check_flows_fix_writes_the_flow_with_lf_line_ends(env):
    """T16 (§3): taskloom check --fix rewrites a flow edited by hand with CRLF line ends with LF only (on Windows
    too)."""
    flow = env.root / "flow.yaml"
    flow.write_bytes(FIXABLE_FLOW.replace("\n", "\r\n").encode("utf-8"))

    fixed = env.taskloom("check", flow, "--fix")

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert b"\r" not in flow.read_bytes()
    assert "old_setting" not in flow.read_text(encoding="utf-8")  # (fixed)
