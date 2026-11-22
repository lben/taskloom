"""Proof: Help > Check for updates installs the newest Taskloom into a folder of its own while you work (UP-1, UP-3,
UP-6), and the Start menu and Startup shortcuts follow the newest version (UP-2). The promises are
docs/promises/updates.yaml. How proofs are written: README.md here.

GitHub is the local HTTPS server of tests/test_updates.py, and Windows' per-user folders (APPDATA, LOCALAPPDATA) are
temporary folders: nothing reaches the internet, and no real shortcut is touched. The shortcuts are real .lnk files on
Windows only, so their proof runs there; Check for updates runs everywhere (shown with the shortcuts on Windows)."""

import base64
import json
import re
import sys
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressBar, QPushButton

from proofs.conftest import WINDOW, choose, menu_item
from taskloom import __version__
from taskloom.config import Home
import tests.test_updates as updates
from tests.test_updates import (API_PATH, HELLO, MARKER, NEW, NO_KEYRING, Packaged, PackagedSys, _powershell, _quoted,
                                both_entries, editor_args, entries_now, entry_for, leftovers, login_args, norm,
                                program_folder_contents, shown_widget, unpacked, zip_files)

# Fixtures of the updates tests, used here by name (bound, not imported, so that pyflakes passes): Windows' per-user
# folders made temporary, and the local HTTPS server standing for Taskloom's GitHub releases.
certificate, folders, releases = updates.certificate, updates.folders, updates.releases

WINDOWS = sys.platform == "win32"
OLDER = "0.30.0"  # the Taskloom the shortcuts start before the newer one's first start


def as_windows_reads(path: Path) -> tuple[str, str, str]:
    """What a shortcut starts, as Windows' own shell reads it (WScript.Shell, not Taskloom's code): its program, its
    arguments and the folder it starts in. (Each as base64 of its UTF-8: PowerShell's output code page cannot hold
    every name.)"""
    said = _powershell(f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_quoted(path)}); "
                       "foreach ($v in @($s.TargetPath, $s.Arguments, $s.WorkingDirectory)) { ':' + "
                       "[System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes([string]$v)) }")
    return tuple(base64.b64decode(line[1:]).decode("utf-8") for line in said.splitlines() if line.startswith(":"))


def named(text: str, places: dict) -> str:
    """`text` with each of these folders written as its name (e.g. %LOCALAPPDATA%), as you would read it."""
    for folder, name in places.items():
        text = re.sub(re.escape(str(folder)), lambda _: name, text, flags=re.IGNORECASE)
    return text


def places(folders, home: Path) -> dict:
    return {folders.local: "%LOCALAPPDATA%", folders.appdata: "%APPDATA%", home: "<your Taskloom folder>"}


def shortcuts(folders, home: Path) -> str:
    """The Startup folder and the Start menu's Taskloom shortcut, each shortcut as Windows reads it."""
    lines = []
    for folder, names in ((folders.startup.parent, None), (folders.start_menu.parent, {folders.start_menu.name})):
        lines.append(named(str(folder), places(folders, home)))
        found = sorted(p for p in folder.iterdir() if p.is_file() and (names is None or p.name in names)) \
            if folder.is_dir() else []
        for path in found:
            lines.append(f"  {path.name}")
            if path.suffix == ".lnk":
                program, arguments, start_in = (named(v, places(folders, home)) for v in as_windows_reads(path))
                lines += [f"    Program:   {program}", f"    Arguments: {arguments}", f"    Start in:  {start_in}"]
        if not found:
            lines.append("  (no Taskloom shortcut)")
    return "\n".join(lines)


def folder_tree(folder: Path) -> str:
    """The folders and files under `folder`, one a line, indented under their folder."""
    lines = []
    for path in sorted(folder.rglob("*"), key=lambda p: p.relative_to(folder).parts):
        depth = len(path.relative_to(folder).parts) - 1
        lines.append("  " * (depth + 1) + path.name + ("\\" if path.is_dir() else ""))
    return "\n".join([str(folder.name) + "\\", *lines])


def until(qtbot, done, said: list):
    """What p.while_open waits on: until done() (a message box was answered), or Taskloom said something went wrong."""
    def wait():
        qtbot.waitUntil(lambda: bool(done() or said), timeout=60_000)
        assert not said, f"Taskloom said: {said}"
    return wait


@pytest.fixture
def packaged_editor(qtbot, tmp_path, monkeypatch, folders, releases):
    """The real editor (offscreen) as the packaged Windows Taskloom unzipped by hand into Downloads\\taskloom, on a
    temporary Taskloom folder, asking the local release server. Its questions are real, answered on screen; its
    information and warnings are written down in `said` (none is expected)."""
    from taskloom.editor import window as window_module
    from taskloom.editor.window import MainWindow

    this = unpacked(tmp_path / "Downloads" / "taskloom", __version__)
    home = tmp_path / "home"
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", NO_KEYRING)
    monkeypatch.setenv("TASKLOOM_HOME", str(home))  # (never ~/.taskloom, whatever starts)
    monkeypatch.setattr(window_module, "frozen", lambda: True)
    monkeypatch.setattr(window_module, "sys", PackagedSys(this / "taskloomw.exe"))
    monkeypatch.setattr(MainWindow, "start_scheduler", lambda self, by_you=False: None)  # (no service from a proof)
    said = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, _k=kind, **k: said.append((_k, *a[1:3]))
                                                            or QMessageBox.Ok))
    win = MainWindow(Home(home))
    qtbot.addWidget(win)
    win.resize(*WINDOW)
    win.show()
    qtbot.wait(50)
    win.said, win.this = said, this
    yield win
    for tab in win.tabs_list():
        tab.doc.dirty = False


def test_check_for_updates_installs_the_newest_version_in_a_folder_of_its_own_while_you_work(
        proof, packaged_editor, qtbot, folders, releases):
    p = proof("Check for updates installs the newest Taskloom while you work",
              "It would be c which fetches the latest version, doesn't require admin rights. So the version opened "
              "from the start menu and the version that auto loads from the startup folder always points to the "
              "latest fetched version")
    p.idea("In the Windows Taskloom, Help > Check for updates asks Taskloom's releases on GitHub for the newest "
           "version. It asks only when you choose it: Taskloom never checks by itself.")
    p.idea("If that version is newer, Taskloom asks “Install?”. Yes downloads its Windows zip in a small window with a "
           "progress bar and Cancel, and the editor goes on working meanwhile. The zip is checked whole, then unpacked "
           "into a folder of its own, %LOCALAPPDATA%\\Programs\\Taskloom\\<version>, where you can write without "
           "admin rights.")
    p.idea("Then your Start menu and Startup shortcuts start the new version, and Taskloom offers to restart in it. "
           "No keeps this editor open until you close it.")
    p.why_simplest("Each version gets a folder of its own, filled first and moved into place in one step at the end. "
                   "A cancelled or broken download never touches the version you use, so there is nothing to undo: "
                   "a message says what went wrong and Taskloom works as before.")
    p.why_simplest("It reuses what is there: the zip each release already publishes, and Qt's own network access, "
                   "which keeps the editor usable and goes through Windows' proxy settings as your browser does.")
    p.alternative("An installer (a setup program) that puts Taskloom in Program Files",
                  "It needs admin rights, which you do not have at work, and it cannot replace Taskloom's files "
                  "while Taskloom or its scheduler service runs.")
    p.alternative("Unpacking the new version over the folder Taskloom runs from",
                  "Windows refuses to replace a program while it runs, and an update cut off halfway would leave a "
                  "mix of two versions that may not start.")
    p.alternative("Checking for updates by itself at each start",
                  "You asked for it only when you click. It would also ask the internet at every start, the "
                  "scheduler's at login too.")
    p.fits("Your settings, run history, credentials and schedules are in your Taskloom folder (~/.taskloom), not in "
           "the program's folder, so the new version finds them as they were; your flows stay where you saved them.")
    p.fits("After an install, the new version's scheduler service takes over from the old one by itself, at a quiet "
           "moment (no flow running, nothing due within 2 minutes), so no scheduled time is run twice or skipped.")
    win, said = packaged_editor, packaged_editor.said
    home = win.home.root
    if WINDOWS:
        both_entries(folders, win.this, home)
    releases.hold = threading.Event()  # (the download waits halfway, so you can see it going)
    qtbot.wait(1000)
    asked_before = list(releases.requests)

    # --- 1. Help > Check for updates ----------------------------------------------------------------------------------
    p.step("Choose Check for updates… in the Help menu.")
    p.note("GitHub is stood in for by a test server on this computer: its address is the one thing the proof changes. "
           f"The newest release it offers is Taskloom {NEW}, a zip made for the proof whose programs are stand-ins; "
           f"this Taskloom, {__version__}, was unzipped by hand into Downloads\\taskloom.")
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    bar = win.menuBar()
    at = bar.mapToGlobal(bar.actionGeometry(help_menu.menuAction()).bottomLeft())

    def look_at_help(menu):
        p.shot(menu, "① Check for updates… in the Help menu.", over=win,
               highlight=[menu_item(menu, "Check for updates")], badges=True, zoom=menu)
        choose(menu, "Check for updates")

    p.while_open(lambda: help_menu.exec(at), look_at_help)
    p.check("Taskloom asked nothing of GitHub before you chose it", asked_before == [])

    # --- 2. Install? ----------------------------------------------------------------------------------------------------
    p.step("Taskloom asks whether to install the newer version: click Yes.")
    install = []

    def look_at_install(box):
        install.append(box.text())
        p.shot(box, f"Taskloom {NEW} is newer than this one: Install? ① Yes.",
               highlight=[box.button(QMessageBox.Yes)], badges=True)
        QTest.mouseClick(box.button(QMessageBox.Yes), Qt.LeftButton)

    p.while_open(until(qtbot, lambda: install, said), look_at_install)
    p.check(f"It names the new version, {NEW}, and this one, {__version__}, and asks Install?",
            NEW in install[0] and __version__ in install[0] and "Install?" in install[0])
    api = [r for r in releases.requests if r["path"] == API_PATH]
    p.check("It asked GitHub's releases once, as Taskloom", len(api) == 1
            and {k.lower(): v for k, v in api[0]["headers"].items()}.get("user-agent", "").startswith("taskloom/"))

    # --- 3. The download, while you work --------------------------------------------------------------------------------
    p.step("While it downloads, choose File > New flow.")
    qtbot.waitUntil(lambda: (shown := shown_widget(win, "update-progress")) is not None
                    and 0 < shown.findChild(QProgressBar).value() < shown.findChild(QProgressBar).maximum(),
                    timeout=30_000)
    progress = shown_widget(win, "update-progress")
    progress_bar = progress.findChild(QProgressBar)
    cancel = next(b for b in progress.findChildren(QPushButton) if b.text() == "Cancel")
    tabs = len(win.tabs_list())
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    menu_item(file_menu, "New flow").trigger()
    QApplication.processEvents()
    tab_bar = win.tabs.tabBar()
    new_tab = tab_bar.tabRect(win.tabs.indexOf(win.current))
    p.shot(progress, f"① The bar shows how much of Taskloom {NEW} has come; ② Cancel would stop it and leave nothing "
                     "behind. ③ The editor is not held up meanwhile: the new flow opened in a tab of its own.",
           over=win, highlight=[progress_bar, cancel, QRect(tab_bar.mapTo(win, new_tab.topLeft()), new_tab.size())],
           badges=True, zoom=progress)
    p.check("The download window waits for no answer, and the editor made a new flow while the download went on",
            QApplication.activeModalWidget() is None and len(win.tabs_list()) == tabs + 1)

    # --- 4. Installed: restart now? -------------------------------------------------------------------------------------
    p.step("When it is installed, Taskloom offers to restart in the new version: click No.")
    p.note("The proof answers No: the new version's programs here are stand-ins that cannot run. Yes would close this "
           "editor as usual, once no flow you started from it runs, and open the new version's editor with your tabs.")
    restart, status = [], []

    def look_at_restart(box):
        restart.append(box.text())
        status.append(win.statusBar().currentMessage())
        p.shot(box, f"Restart Taskloom now with version {NEW}? ① No keeps this editor open.",
               highlight=[box.button(QMessageBox.No)], badges=True)
        QTest.mouseClick(box.button(QMessageBox.No), Qt.LeftButton)

    releases.hold.set()  # (the rest of the download comes)
    p.while_open(until(qtbot, lambda: restart, said), look_at_restart)
    p.text("The status bar said", named(status[0], places(folders, home)))
    installed = folders.programs / NEW
    p.text("What %LOCALAPPDATA%\\Programs\\Taskloom holds now", folder_tree(folders.programs))
    p.check(f"It asked: Restart Taskloom now with version {NEW}?", restart == [f"Restart Taskloom now with version "
                                                                              f"{NEW}?"])
    p.check(f"Taskloom {NEW} is in a folder of its own, {NEW}, holding exactly the zip's files",
            installed.is_dir() and program_folder_contents(installed) == zip_files(releases.zip))
    marker = json.loads((installed / MARKER).read_text(encoding="utf-8"))
    p.check(f"Beside them, {MARKER} says it was installed by Taskloom, updating from Downloads\\taskloom",
            norm(marker["from"]) == norm(win.this))
    p.check("Nothing else was left there: no part of the download", leftovers(folders) == [NEW])
    p.check("No message said anything went wrong, and this editor is still open", said == [] and win.isVisible())
    if WINDOWS:
        p.text("The Startup folder and the Start menu's Taskloom shortcut now", shortcuts(folders, home))
        p.check(f"Both shortcuts start Taskloom {NEW} now, each as before: the scheduler service at login, the editor "
                "from the Start menu, for your Taskloom folder",
                entries_now(folders) == {"Start menu": entry_for(installed, editor_args(home)),
                                         "Startup": entry_for(installed, login_args(home))})
    else:
        p.note("This run is not on Windows, which alone has these shortcuts: when the proof runs on Windows (as it "
               "does for each release), it shows them starting the new version.")


def test_the_shortcuts_start_the_newest_version_once_it_has_started(proof, env, folders):
    p = proof("The Start menu and Startup shortcuts start the newest Taskloom",
              "In order to add it to the startup folder there should be a way to update new versions while keeping "
              "the shortcut towards the latest version")
    p.idea("Two shortcuts start Taskloom: Taskloom Scheduler in your Startup folder starts the scheduler service when "
           "you log in, and Taskloom in the Start menu opens the editor. Both always start the newest Taskloom you "
           "have.")
    p.idea("Each start of a Taskloom, however it starts (its editor, the scheduler, any command), looks at the two "
           "shortcuts. One that starts an older version, or a folder that is gone, is pointed at the Taskloom "
           "starting, and keeps what it starts it with: your Taskloom folder, and the scheduler or the editor. One "
           "that starts the same or a newer version is left as it is.")
    p.why_simplest("The shortcuts themselves say which version they start: the folder of the program they open holds "
                   "its version. So nothing else is kept (no list of versions, no registry entry), and any start can "
                   "tell whether it is newer.")
    p.why_simplest("A start only points the shortcuts there are; only the two boxes in Tools > Settings add or remove "
                   "them, so none appears that you did not ask for.")
    p.alternative("A fixed launcher in the shortcuts that looks for the newest version each time",
                  "One more program to keep working and to update: if it broke, nothing would start, the scheduler "
                  "at login neither.")
    p.alternative("Pointing the shortcuts at the new version only when Check for updates installs it",
                  "A version you unzipped yourself would never be started by them.")
    p.alternative("Keeping the login script (Taskloom Scheduler.cmd) and rewriting it",
                  "A script flashes a console window at every login; a shortcut starts the windowed program "
                  "directly. The first start of this version replaces the script by the shortcut.")
    p.fits("The shortcut starts the program with --home and your Taskloom folder, as TASKLOOM_HOME does, since a "
           "shortcut cannot set it; your settings, history and schedules stay where they are.")
    p.fits("A Taskloom pinned to the taskbar is Windows' own copy of a shortcut: an update does not change it, and "
           "the guide says so.")
    if not WINDOWS:
        pytest.skip("Shown on Windows only: the shortcuts are Windows' .lnk files, which only Windows writes and reads "
                    "(the tests check the same rule elsewhere with a file standing in for a shortcut).")
    home = env.home
    older = unpacked(folders.programs / OLDER, OLDER)
    this = unpacked(folders.programs / __version__, __version__)
    both_entries(folders, older, home)

    p.step(f"Before: your Startup folder and Start menu have Taskloom's shortcuts from an older Taskloom, {OLDER}.")
    p.note("Windows' per-user folders (%APPDATA%, %LOCALAPPDATA%) are temporary folders of the proof, so no shortcut "
           "of yours is touched. The shortcuts are real .lnk files, read here by Windows' own shell, not by Taskloom.")
    p.text("The Startup folder and the Start menu's Taskloom shortcut", shortcuts(folders, home))
    p.check(f"Both start Taskloom {OLDER}", entries_now(folders) == {
        "Start menu": entry_for(older, editor_args(home)), "Startup": entry_for(older, login_args(home))})

    p.step(f"Unzip Taskloom {__version__} into %LOCALAPPDATA%\\Programs\\Taskloom\\{__version__} and start it once: "
           "here with taskloom run hello.yaml in a terminal (its editor, or its scheduler at login, does the same).")
    p.note("The program started is this Taskloom, told it is the packaged Windows one in that folder; the folder "
           "holds stand-in programs, not the built zip's.")
    done = Packaged(env, folders, this).run("run", env.write("hello.yaml", HELLO))
    p.text("taskloom run hello.yaml printed", named(done.stdout + done.stderr, places(folders, home)).strip())
    p.check("It ran as usual", done.returncode == 0)

    p.step("After: look at the shortcuts again.")
    p.text("The Startup folder and the Start menu's Taskloom shortcut", shortcuts(folders, home))
    after = entries_now(folders)
    p.check(f"The Startup shortcut starts Taskloom {__version__} now, still the scheduler service for your Taskloom "
            "folder", after["Startup"] == entry_for(this, login_args(home)))
    p.check(f"The Start menu shortcut starts Taskloom {__version__}'s editor now, for your Taskloom folder",
            after["Start menu"] == entry_for(this, editor_args(home)))
    p.check("The Startup folder holds only the shortcut: none left half written",
            sorted(path.name for path in folders.startup.parent.iterdir()) == [folders.startup.name])
    p.check(f"{OLDER}'s folder is still there: you unzipped it yourself, so Taskloom does not remove it",
            (older / "taskloomw.exe").is_file())
