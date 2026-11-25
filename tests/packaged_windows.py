"""Helpers of the tests that start Taskloom as the packaged Windows Taskloom (tests/test_updates.py, the login tests of
tests/test_scheduler.py, tests/test_paths_cli_config.py and tests/test_paths_scheduler.py): Windows' per-user folders
made the test's own, a program folder as its zip unpacks, the Start menu and Startup entries as Windows reads and
writes them (on Windows real .lnk files, elsewhere a JSON file standing in for one), and `taskloom` started as that
Taskloom in a real process. In a module of their own, which imports nothing from tests/: a test file that uses them
does not read tests/test_updates.py, so an edit to an updates test changes the kept-result key of no other check
(packaging/ci_reuse.py)."""

import base64
import contextlib
import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

import yaml

from taskloom.launch import pid_alive

WINDOWS = sys.platform == "win32"
NO_KEYRING = "keyring.backends.fail.Keyring"
MARKER = "installed-by-taskloom.json"


@dataclasses.dataclass
class Folders:
    """Windows' per-user folders of the test (temporary): APPDATA and LOCALAPPDATA."""
    root: Path

    @property
    def appdata(self) -> Path:
        return self.root / "AppData" / "Roaming"

    @property
    def local(self) -> Path:
        return self.root / "AppData" / "Local"

    @property
    def programs(self) -> Path:
        """<LOCALAPPDATA>/Programs/Taskloom: where Taskloom installs its versions."""
        return self.local / "Programs" / "Taskloom"

    @property
    def start_menu(self) -> Path:
        return self.appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Taskloom.lnk"

    @property
    def startup(self) -> Path:
        return self.appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Taskloom Scheduler.lnk"

    def environ(self) -> dict:
        return {"APPDATA": str(self.appdata), "LOCALAPPDATA": str(self.local)}


def windows_folders(tmp_path, monkeypatch) -> Folders:
    """The test's Windows folders, made this process's APPDATA and LOCALAPPDATA (and so those of what it starts)."""
    made = Folders(tmp_path / "windows")
    for name, value in made.environ().items():
        monkeypatch.setenv(name, value)
    return made


def unpacked(folder: Path, version: str, marker_from: Path | None = None, extra: dict | None = None) -> Path:
    """A Taskloom program folder of `version` as its zip unpacks: stand-in programs, VERSION, README, an example flow
    (and `extra` files {relative path: text}). With `marker_from`: installed by Taskloom (updating from that folder)."""
    files = {"taskloomw.exe": f"stand-in windowed program {version}", "taskloom.exe": f"stand-in program {version}",
             "VERSION": version, "README.md": "Taskloom\n", "examples/hello.yaml": "blocks: {}\n", **(extra or {})}
    for name, text in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
    if marker_from is not None:
        time.sleep(0.02)  # (the marker is written after the files: none changed after it)
        (folder / MARKER).write_text(json.dumps({"from": str(marker_from), "files": sorted(files)}), encoding="utf-8")
    return folder


Entry = namedtuple("Entry", "target arguments workdir")


def norm(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def login_args(home) -> str:
    return f'--home "{home}" scheduler --unless-paused'


def entry_for(folder: Path, arguments: str) -> Entry:
    return Entry(norm(folder / "taskloomw.exe"), arguments, norm(folder))


def _quoted(text) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _powershell(script: str) -> str:
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout


def write_entry(path: Path, target, arguments: str, workdir) -> None:
    """An entry as a user's earlier Taskloom (or Windows) left it: on Windows a real .lnk written by WScript.Shell."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        _powershell(f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_quoted(path)}); "
                    f"$s.TargetPath = {_quoted(target)}; $s.Arguments = {_quoted(arguments)}; "
                    f"$s.WorkingDirectory = {_quoted(workdir)}; $s.Save()")
    else:
        path.write_text(json.dumps({"target": str(target), "arguments": arguments, "workdir": str(workdir)}),
                        encoding="utf-8")


def read_entry(path: Path) -> Entry | None:
    """What the entry starts, as Windows reads it (on Windows: WScript.Shell); None: there is no such entry."""
    if not path.exists():
        return None
    if WINDOWS:  # (each as base64 of its UTF-8: PowerShell's output code page cannot hold every name, e.g. José)
        said = _powershell(f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_quoted(path)}); "
                           "foreach ($v in @($s.TargetPath, $s.Arguments, $s.WorkingDirectory)) { ':' + "
                           "[System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes([string]$v)) }")
        target, arguments, workdir = [base64.b64decode(line[1:]).decode("utf-8") for line in said.splitlines()
                                      if line.startswith(":")]
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        target, arguments, workdir = data["target"], data["arguments"], data["workdir"]
    return Entry(norm(target), arguments, norm(workdir))


def _json_write_link(path, target, arguments, workdir):
    Path(path).write_text(json.dumps({"target": str(target), "arguments": arguments, "workdir": str(workdir)}),
                          encoding="utf-8")


def _json_read_link(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["target"], data["arguments"], data["workdir"]


def json_links(monkeypatch):
    """Away from Windows a JSON file stands in for a .lnk, in this process (the real-process starts: PACKAGED)."""
    if WINDOWS:
        return
    try:
        from taskloom import entries
    except ImportError:  # (not written yet: nothing reads or writes an entry)
        return
    monkeypatch.setattr(entries, "read_link", _json_read_link)
    monkeypatch.setattr(entries, "write_link", _json_write_link)


class PackagedSys:
    """What a module of Taskloom sees as `sys` in the packaged Taskloom whose program is `executable`, on `platform`."""
    frozen = True

    def __init__(self, executable, platform: str = "win32"):
        self.executable, self.platform = str(executable), platform

    def __getattr__(self, name):
        return getattr(sys, name)


PACKAGED = r'''
import json, os, sys, time
from pathlib import Path

Path(os.environ["TEST_PID"]).write_text(str(os.getpid()), encoding="utf-8")


class Packaged:
    """What taskloom.cli sees as `sys`: the packaged Windows Taskloom whose program is TEST_PROGRAM (TEST_PLATFORM:
    on another platform; TEST_FROZEN "no": not packaged, run from source)."""
    platform = os.environ.get("TEST_PLATFORM", "win32")
    frozen = os.environ.get("TEST_FROZEN") != "no"
    executable = os.environ["TEST_PROGRAM"]

    def __getattr__(self, name):
        return getattr(sys, name)


import taskloom.cli as cli

cli.sys = Packaged()
cli.frozen = lambda: Packaged.frozen
try:
    from taskloom import entries
except ImportError:  # (not written yet)
    entries = None
if entries is not None:
    if sys.platform != "win32":  # no .lnk away from Windows: a JSON file stands in for one
        def write_link(path, target, arguments, workdir):
            Path(path).write_text(json.dumps({"target": str(target), "arguments": arguments,
                                              "workdir": str(workdir)}), encoding="utf-8")

        def read_link(path):
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data["target"], data["arguments"], data["workdir"]

        entries.read_link = read_link
    else:
        write_link = entries.write_link

    def written_down(path, *args, **kwargs):  # each entry written: its file's name, in TEST_WRITES
        with open(os.environ["TEST_WRITES"], "a", encoding="utf-8") as f:
            f.write(json.dumps(Path(path).name) + "\n")
        return write_link(path, *args, **kwargs)

    entries.write_link = written_down
if os.environ.get("TEST_STATUS"):  # what the editor's status bar says, in TEST_STATUS
    from PySide6.QtWidgets import QStatusBar

    show = QStatusBar.showMessage

    def shown(self, text, *rest):
        with open(os.environ["TEST_STATUS"], "a", encoding="utf-8") as f:
            f.write(json.dumps(text) + "\n")
        return show(self, text, *rest)

    QStatusBar.showMessage = shown
if os.environ.get("TEST_GO"):  # (started together: each goes once the file is there)
    while not Path(os.environ["TEST_GO"]).exists():
        time.sleep(0.005)
cli.run_cli(sys.argv[1:])
'''


HELLO = yaml.safe_dump({"blocks": {"manual": {"type": "trigger.manual"},
                                   "work": {"type": "logic.python",
                                            "config": {"inputs": [], "outputs": [], "code": "pass"}}},
                        "edges": ["manual.start -> work.after"]}, sort_keys=False)


class Packaged:
    """`taskloom` of the packaged Windows Taskloom in a program folder, started as a real process (PACKAGED), on the
    test's Taskloom folder and Windows folders."""

    def __init__(self, env, folders: Folders, folder: Path, name: str = "start"):
        self.env, self.folders, self.folder = env, folders, folder
        self.pid_file = env.root / f"{name}.pid"
        self.writes = env.root / "entry-writes.jsonl"
        self.status = env.root / f"{name}-status.jsonl"

    def environ(self, program: str, go: Path | None = None, status: bool = False) -> dict:
        return {**self.env.env, **self.folders.environ(), "PYTHON_KEYRING_BACKEND": NO_KEYRING,
                "TEST_PROGRAM": str(self.folder / program), "TEST_PID": str(self.pid_file),
                "TEST_WRITES": str(self.writes), **({"TEST_GO": str(go)} if go else {}),
                **({"TEST_STATUS": str(self.status), "QT_QPA_PLATFORM": "offscreen"} if status else {})}

    def run(self, *args, environ: dict | None = None) -> subprocess.CompletedProcess:
        """`taskloom <args>` from a terminal (taskloom.exe), to its end (`environ`: more environment variables)."""
        return subprocess.run([sys.executable, "-c", PACKAGED, *map(str, args)],
                              env={**self.environ("taskloom.exe"), **(environ or {})}, cwd=self.env.root,
                              capture_output=True, text=True, timeout=120)

    def popen(self, *args, program: str = "taskloom.exe", go: Path | None = None, status: bool = False):
        return subprocess.Popen([sys.executable, "-c", PACKAGED, *map(str, args)],
                                env=self.environ(program, go, status), cwd=self.env.root, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def said_in_status_bar(self) -> list[str]:
        if not self.status.exists():
            return []
        return [json.loads(line) for line in self.status.read_text(encoding="utf-8").splitlines()]

    def written(self) -> list[str]:
        if not self.writes.exists():
            return []
        return [json.loads(line) for line in self.writes.read_text(encoding="utf-8").splitlines()]

    def end(self, proc: subprocess.Popen) -> str:
        """Ends the process (Taskloom's own: on Windows a venv's python.exe starts it); what it printed."""
        with contextlib.suppress(OSError, ValueError):
            pid = int(self.pid_file.read_text(encoding="utf-8"))
            if pid != os.getpid() and pid_alive(pid):
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        if proc.poll() is None:
            proc.kill()
        out, err = proc.communicate(timeout=30)
        return (out or "") + (err or "")
