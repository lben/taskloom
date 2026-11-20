"""Updates on Windows: Help > Check for updates fetches the newest Taskloom from its GitHub releases into
%LOCALAPPDATA%\\Programs\\Taskloom\\<version>; the Start menu entry and the Startup entry always start the newest version;
the newer version takes the scheduler service over by itself at a quiet moment; the editor offers to restart in it; old
versions Taskloom installed are removed once nothing runs from them. The rows UP-1..UP-8 of docs/promises/updates.yaml
and SCH-1 of docs/promises/scheduler.yaml (agreed 2026-11-20); each test names the combination (U1..U11) of the stage's
plan it covers.

Through the real entry points: the editor window (offscreen) and its Help menu, Settings dialog and questions;
`taskloom` started as a real process; the scheduler service as real `taskloom scheduler --headless` processes. What
cannot be real in a test is stood in for, and only that:
- GitHub: a local HTTPS server on 127.0.0.1 (a certificate made for the test, trusted by this process's Qt only);
  taskloom.update's RELEASE_URL and HOSTS are pointed at it. Nothing reaches the internet.
- Windows' per-user folders: APPDATA (Start menu, Startup) and LOCALAPPDATA are temporary folders; the Taskloom
  folder is a temporary one; never ~/.taskloom, the real credential store or the user's Git config.
- The packaged Windows Taskloom: where Taskloom asks "packaged, on Windows, in which program folder", it is answered
  as tests/test_paths_scheduler.py's _OnWindows does (the module's `frozen` and `sys`), the program folder a temporary
  one holding VERSION and stand-in taskloom.exe / taskloomw.exe files; in a real process the same is done by the
  small start script PACKAGED before it calls taskloom.cli.run_cli.
- A shortcut (.lnk) away from Windows: taskloom.entries.read_link / write_link are replaced by a JSON file standing in
  for one, so everything above them runs for real; on Windows the real .lnk files are written by Taskloom and read and
  written by the test through Windows' own WScript.Shell (PowerShell), not through Taskloom's code.
- A program started from the new version's folder (its stand-in exe cannot run): subprocess.Popen records it.
- A full disk: shutil.disk_usage says 1 kB is free. An hour without a quiet moment: the waiting service's
  scheduler.waiting says it began waiting 61 minutes ago. An older service: a real service whose scheduler.version is
  rewritten to an older version (or removed, as before 0.14.1).

Windows-only (skipped elsewhere): test_u10_a_folder_in_use_is_kept_and_removed_at_a_later_start (only Windows refuses
to rename a folder holding an open file). Every other test runs on Windows, macOS and Linux; on Windows with real
.lnk files.
"""
# What updates on Windows must be called and show (chosen by these tests: the implementer's contract):
# - taskloom.update: RELEASE_URL (GitHub's /releases/latest API URL), ASSET = "taskloom-windows-amd64.zip", HOSTS (the
#   hosts a URL or redirect may name); read when a check runs, so tests can point them at the local server. One GET of
#   RELEASE_URL with the header Accept: application/vnd.github+json and a User-Agent starting "taskloom/"; tag_name
#   ("v0.40.0") and the asset named ASSET (browser_download_url, size, digest "sha256:<hex>"). Downloads through Qt's
#   QNetworkAccessManager (the default QSslConfiguration: the test adds its certificate there).
# - Help menu: the action "Check for updates" (a trailing "…" allowed), only where the editor is the packaged Windows
#   Taskloom: taskloom.editor.window's `frozen()` true and its `sys.platform == "win32"`; the program folder is
#   Path(sys.executable).parent, read through taskloom.editor.window's `sys`.
# - Questions and messages are QMessageBox.question / information / warning (static calls, as elsewhere in the editor):
#   question with "Install?" and the new version in its title or text (Yes installs, No does nothing); information
#   with "You have the newest version"; a failure: a warning whose title or text says what went wrong, with: the HTTP
#   status ("404", "500"); "incomplete" (fewer bytes than the release says); "checksum" (sha256 differs);
#   "damaged" (a member's CRC); "taskloomw.exe" (missing from the zip); "VERSION" (not the release's version);
#   "disk space" (shutil.disk_usage, called as shutil.disk_usage, says less is free than the zip unpacks to);
#   "refused" and the URL or host (plain http, or a host not in HOSTS, also on a redirect). Cancel of the download
#   shows no message.
# - The download window: a non-modal widget, objectName "update-progress", with a QProgressBar (value/maximum follow
#   the bytes) and a button "Cancel"; the editor stays usable meanwhile.
# - Install: <LOCALAPPDATA>/Programs/Taskloom/<version> holds the zip's taskloom/ folder's files, byte-equal, plus the
#   marker installed-by-taskloom.json = {"from": "<program folder it was installed from>", "files": ["<every unpacked
#   file, relative, / separated>", ...]}. Leftovers while working: <LOCALAPPDATA>/Programs/Taskloom/.partial-* (and
#   .removing-* while removing); none remains after a success, a failure or a Cancel.
# - Then QMessageBox.question with "Restart Taskloom now with version <v>?" in its title or text. No: nothing more. Yes:
#   while a flow this editor started runs, a non-modal widget objectName "restart-waits" whose text names the flow (its
#   file name without .yaml) with a button "Cancel" (cancels the restart); then the usual close (session and loaded
#   states saved), subprocess.Popen([<new folder>/taskloomw.exe, "--home", <Taskloom folder>, "editor"], ...) and the
#   window closes (never sys.exit / os._exit).
# - An install while a scheduler service runs and is not turned off starts subprocess.Popen of the new version's
#   program (taskloom.exe or taskloomw.exe in the new folder) with "scheduler" and "--unless-paused" and the Taskloom
#   folder (--home <folder>, or TASKLOOM_HOME in its env); none if no service runs or it is turned off.
# - taskloom.entries.read_link(path) -> (target, arguments, workdir) and write_link(path, target, arguments, workdir):
#   the only code that reads or writes .lnk bytes, always called through the module (entries.read_link(...)). Entries:
#   %APPDATA%/Microsoft/Windows/Start Menu/Programs/Taskloom.lnk = <folder>/taskloomw.exe,
#   arguments '--home "<Taskloom folder>" editor', workdir <folder>; .../Start Menu/Programs/Startup/Taskloom
#   Scheduler.lnk = the same program, arguments '--home "<Taskloom folder>" scheduler --unless-paused'. Re-pointing keeps
#   the arguments and sets target and workdir to the new folder; each entry is written once by two quick starts.
# - The start hook, in taskloom.cli.run_cli before any command, where taskloom.cli's `frozen()` is true and its
#   `sys.platform == "win32"`, with the program folder Path(sys.executable).parent (both read through taskloom.cli's
#   own names `frozen` and `sys`): entries pointing at a missing program or an older VERSION are pointed at this folder
#   (an equal or newer existing one is left alone; no entry is created); an entry whose folder was gone is said: one
#   line on stderr (the editor: its status bar) with "gone" and this version; a Startup "Taskloom Scheduler.cmd" of
#   today is replaced by the .lnk for the Taskloom folder of its TASKLOOM_HOME line. Then the cleanup: removes folders
#   in <LOCALAPPDATA>/Programs/Taskloom with a marker, older than this version, other than this one and the one this
#   one's marker names as "from"; keeps, saying so on stderr (a line naming the folder), one holding a flow listed in
#   scheduled.yaml (the line also says "scheduled") or a file not in its marker's list; never a folder without a marker;
#   removes leftover .partial-* and .removing-*.
# - The global option --home <folder> (before the command): the Taskloom folder, as TASKLOOM_HOME.
# - Tools > Settings: a QCheckBox "Add Taskloom to the Start menu" with the same parent widget as "Start the scheduler
#   service when I log in"; each ticked when its entry exists; Save writes or removes its .lnk for this program folder
#   (taskloom.editor.dialogs' `sys`: platform and executable).
# - The scheduler service (`taskloom scheduler`) finding the folder's lock held by an older Taskloom (scheduler.version
#   older, or missing) waits and takes over at a quiet moment (no run going, none waiting, no scheduled time due within
#   2 minutes; after an hour without one, the first check after a run ends): it stops the old service (it ends before
#   the new one holds the lock) and serves. While it waits it writes scheduler.waiting = "<version> <pid> <ISO local
#   time it began waiting>" and reads that time back at each check; a second newer service exits at once; turned off
#   meanwhile, it gives up (ends). Same or newer holding the lock: exits as today. The question "Restart the scheduler
#   service?" is gone, also on macOS and Linux; the editor's start of the service also starts it while an older one runs.
# - docs/guide.md: a sentence with "taskbar", "pin" and "not" (taskbar pins are Windows' own copies, not changed).
import contextlib
import dataclasses
import datetime as dt
import hashlib
import http.server
import io
import ipaddress
import json
import os
import random
import re
import shutil
import signal
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QDialogButtonBox, QLabel,  # noqa: E402
                               QMessageBox, QProgressBar, QWidget)

from sshserver import free_port  # noqa: E402
from taskloom import __version__  # noqa: E402
from taskloom.config import Home  # noqa: E402
from taskloom.launch import pid_alive  # noqa: E402
from taskloom.scheduler import request_turn_off, set_paused  # noqa: E402
from tests.test_load_state import bar, load_state, names, start_again  # noqa: E402
from tests.test_paths_editor_running import eventually  # noqa: E402
from tests.test_paths_scheduler import stand_in_service  # noqa: E402
from tests.test_rerun_carries_on import full_run  # noqa: E402
from tests.test_scheduler_new_promises import (HOURLY, end_runs, ended, flow_yaml, history, hold_until,  # noqa: E402
                                               schedule, stop_the_service, wait_until)

WINDOWS = sys.platform == "win32"
NEW = "0.40.0"  # the release the local server offers (newer than this Taskloom, __version__)
ASSET = "taskloom-windows-amd64.zip"
API_PATH = "/repos/lben/taskloom/releases/latest"
DOWNLOAD_PATH = f"/lben/taskloom/releases/download/v{NEW}/{ASSET}"
ASSET_PATH = "/release-assets/1"
NO_KEYRING = "keyring.backends.fail.Keyring"
MARKER = "installed-by-taskloom.json"
CHECK = "Check for updates"
INSTALL = "Install?"
RESTART = f"Restart Taskloom now with version {NEW}?"
START_MENU_BOX = "Add Taskloom to the Start menu"
LOGIN_BOX = "Start the scheduler service when I log in"


# --- Windows' per-user folders, program folders, entries --------------------------------------------------------------

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


@pytest.fixture
def folders(tmp_path, monkeypatch) -> Folders:
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


def editor_args(home) -> str:
    return f'--home "{home}" editor'


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
    if WINDOWS:
        target, arguments, workdir = (_powershell(
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_quoted(path)}); "
            "$s.TargetPath; $s.Arguments; $s.WorkingDirectory").splitlines() + ["", "", ""])[:3]
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        target, arguments, workdir = data["target"], data["arguments"], data["workdir"]
    return Entry(norm(target), arguments, norm(workdir))


def both_entries(folders: Folders, folder: Path, home) -> None:
    """The Start menu entry and the Startup entry both start Taskloom in `folder`, for the Taskloom folder `home`."""
    write_entry(folders.start_menu, folder / "taskloomw.exe", editor_args(home), folder)
    write_entry(folders.startup, folder / "taskloomw.exe", login_args(home), folder)


def entries_now(folders: Folders) -> dict:
    return {"Start menu": read_entry(folders.start_menu), "Startup": read_entry(folders.startup)}


def _json_write_link(path, target, arguments, workdir):
    Path(path).write_text(json.dumps({"target": str(target), "arguments": arguments, "workdir": str(workdir)}),
                          encoding="utf-8")


def _json_read_link(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["target"], data["arguments"], data["workdir"]


@pytest.fixture(autouse=True)
def links(monkeypatch):
    """Away from Windows a JSON file stands in for a .lnk, in this process (the real-process starts: PACKAGED)."""
    if WINDOWS:
        return
    try:
        from taskloom import entries
    except ImportError:  # (not written yet: nothing reads or writes an entry)
        return
    monkeypatch.setattr(entries, "read_link", _json_read_link)
    monkeypatch.setattr(entries, "write_link", _json_write_link)


# --- the release: a local HTTPS server standing for Taskloom's GitHub releases ----------------------------------------

def release_zip(version: str = NEW, *, leave_out: tuple = (), version_text: str | None = None,
                damaged: bool = False) -> bytes:
    """The Windows zip as packaging/build.py makes it: a top folder taskloom/ (with its folders' entries) holding the
    programs, VERSION, the libraries (a 1.5 MB file), examples. `damaged`: a byte of the libraries changed after the zip
    was made (its CRC no longer fits)."""
    files = {
        "taskloom/taskloomw.exe": f"stand-in windowed program {version}".encode(),
        "taskloom/taskloom.exe": f"stand-in program {version}".encode(),
        "taskloom/VERSION": (version_text or version).encode(),
        "taskloom/README.md": b"Taskloom\n",
        "taskloom/_internal/base_library.zip": random.Random(1).randbytes(1_500_000),
        "taskloom/examples/hello.yaml": b"blocks: {}\n",
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for folder in ("taskloom/", "taskloom/_internal/", "taskloom/examples/"):
            z.writestr(zipfile.ZipInfo(folder), b"")
        for name, data in files.items():
            if name not in leave_out:
                z.writestr(name, data)
    data = bytearray(out.getvalue())
    if damaged:
        info = zipfile.ZipFile(io.BytesIO(bytes(data))).getinfo("taskloom/_internal/base_library.zip")
        name_length, extra_length = int.from_bytes(data[info.header_offset + 26:info.header_offset + 28], "little"), \
            int.from_bytes(data[info.header_offset + 28:info.header_offset + 30], "little")
        data[info.header_offset + 30 + name_length + extra_length + 1000] ^= 0xFF
    return bytes(data)


def zip_files(data: bytes) -> dict:
    """The files the zip holds under taskloom/: {relative path: bytes}."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {name.removeprefix("taskloom/"): z.read(name) for name in z.namelist() if not name.endswith("/")}


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    """A certificate authority made for this test module and a server certificate for 127.0.0.1 it signed."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = dt.datetime.now(dt.timezone.utc)
    usage = dict(content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False,
                 encipher_only=False, decipher_only=False)
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Taskloom test releases")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
          .not_valid_after(now + dt.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, key_cert_sign=True, crl_sign=True, **usage),
                         critical=True)
          .sign(ca_key, hashes.SHA256()))
    server = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
              .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
              .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                             critical=False)
              .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
              .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
              .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False, **usage),
                             critical=True)
              .sign(ca_key, hashes.SHA256()))
    folder = tmp_path_factory.mktemp("certificate")
    (folder / "server.pem").write_bytes(server.public_bytes(serialization.Encoding.PEM))
    (folder / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    return {"ca": ca.public_bytes(serialization.Encoding.PEM), "cert": folder / "server.pem",
            "key": folder / "server.key"}


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # (a client that went away mid-answer: nothing to report)
        pass


class Releases:
    """Taskloom's GitHub releases, on 127.0.0.1: the API's /releases/latest, the asset's download (redirected to the
    asset host, as GitHub does) and the asset itself. What it answers can be changed by the test; every request it got
    is in `requests` ({"path", "headers"}); the plain-http server beside it (for a download URL that is not https) has
    its own, `plain_requests`."""

    def __init__(self, certificate):
        self.tag, self.zip = f"v{NEW}", release_zip()
        self.api_status = self.download_status = 200
        self.digest = None  # (None: the zip's own)
        self.cut_short = False  # the download stops after half, though it said its full size
        self.hold = None  # a threading.Event: the download sends half, then waits for it
        self.redirect = "same host"  # or "another host" (https://localhost: not in HOSTS)
        self.scheme = "https"  # of the download URL ("http": the plain server)
        self.requests, self.plain_requests = [], []
        releases = self

        class Answer(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def send(self, status: int, body: bytes = b"", headers: dict | None = None):
                self.send_response(status)
                for name, value in {"Content-Length": str(len(body)), **(headers or {})}.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                (releases.plain_requests if self.server is releases.plain else releases.requests).append(
                    {"path": self.path, "headers": dict(self.headers)})
                if self.server is releases.plain:
                    return self.send(404)
                if self.path == API_PATH:
                    if releases.api_status != 200:
                        return self.send(releases.api_status, b'{"message": "Not Found"}')
                    return self.send(200, json.dumps(releases.release()).encode(),
                                     {"Content-Type": "application/json"})
                if self.path == DOWNLOAD_PATH:
                    host = "127.0.0.1" if releases.redirect == "same host" else "localhost"
                    return self.send(302, b"", {"Location": f"https://{host}:{releases.port}{ASSET_PATH}"})
                if self.path == ASSET_PATH:
                    return self.asset()
                return self.send(404)

            def asset(self):
                if releases.download_status != 200:
                    return self.send(releases.download_status, b"server error")
                data = releases.zip
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                half = len(data) // 2
                try:
                    for start in range(0, half, 65536):
                        self.wfile.write(data[start:min(start + 65536, half)])
                        self.wfile.flush()
                    if releases.cut_short:
                        self.close_connection = True
                        self.connection.close()
                        return
                    if releases.hold is not None:
                        releases.hold.wait(120)
                    for start in range(half, len(data), 65536):
                        self.wfile.write(data[start:start + 65536])
                    self.wfile.flush()
                except OSError:  # (the editor cancelled: it went away)
                    self.close_connection = True

            def finish(self):
                with contextlib.suppress(OSError):
                    super().finish()

        self.server = _Server(("127.0.0.1", 0), Answer)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate["cert"], certificate["key"])
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.plain = _Server(("127.0.0.1", 0), Answer)
        self.port = self.server.server_address[1]
        self.url = f"https://127.0.0.1:{self.port}{API_PATH}"

    def release(self) -> dict:
        """What GitHub's API says of the newest release."""
        download = (f"https://127.0.0.1:{self.port}{DOWNLOAD_PATH}" if self.scheme == "https"
                    else f"http://127.0.0.1:{self.plain.server_address[1]}{DOWNLOAD_PATH}")
        return {"tag_name": self.tag, "name": f"Taskloom {self.tag[1:]}", "draft": False, "prerelease": False,
                "assets": [
                    {"name": "taskloom-runner-linux-x86_64.tar.gz", "size": 1000, "digest": "sha256:" + "1" * 64,
                     "browser_download_url": f"https://127.0.0.1:{self.port}/elsewhere/runner.tar.gz"},
                    {"name": ASSET, "size": len(self.zip), "content_type": "application/zip",
                     "digest": self.digest or "sha256:" + hashlib.sha256(self.zip).hexdigest(),
                     "browser_download_url": download}]}

    def downloads(self) -> list[str]:
        return [r["path"] for r in self.requests if r["path"] != API_PATH]

    def start(self):
        for server in (self.server, self.plain):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.hold is not None:
            self.hold.set()
        for server in (self.server, self.plain):
            server.shutdown()
            server.server_close()


@pytest.fixture
def releases(certificate, monkeypatch, qapp):
    """The local release server, trusted by this process's Qt for the test, with taskloom.update pointed at it."""
    from PySide6.QtNetwork import QSslCertificate, QSslConfiguration

    before = QSslConfiguration.defaultConfiguration()
    trusting = QSslConfiguration.defaultConfiguration()
    trusting.addCaCertificate(QSslCertificate(certificate["ca"]))
    QSslConfiguration.setDefaultConfiguration(trusting)
    server = Releases(certificate).start()
    point_updates_at(monkeypatch, server.url)
    yield server
    server.stop()
    QSslConfiguration.setDefaultConfiguration(before)


def point_updates_at(monkeypatch, url: str):
    """taskloom.update asks `url` for the newest release and accepts no host but 127.0.0.1."""
    try:
        from taskloom import update
    except ImportError:  # (not written yet: there is no Check for updates either)
        return
    for name in ("RELEASE_URL", "HOSTS"):
        if not hasattr(update, name):  # (never let a check reach the real GitHub)
            pytest.fail(f"the contract names taskloom.update.{name}: the tests point it at their own server")
    monkeypatch.setattr(update, "RELEASE_URL", url)
    monkeypatch.setattr(update, "HOSTS", {"127.0.0.1"})


# --- the editor as the packaged Windows Taskloom ---------------------------------------------------------------------

class PackagedSys:
    """What a module of Taskloom sees as `sys` in the packaged Taskloom whose program is `executable`, on `platform`."""
    frozen = True

    def __init__(self, executable, platform: str = "win32"):
        self.executable, self.platform = str(executable), platform

    def __getattr__(self, name):
        return getattr(sys, name)


class Boxes:
    """The message boxes the editor shows (QMessageBox.question / information / warning / critical), written down as
    (kind, "title\\ntext"); a question is answered by the first of `answers` ((words, button)) whose words it has, else
    No. A window that waits for an answer some other way (modal) is closed and written down as ("modal", its words)."""

    def __init__(self):
        self.seen, self.answers = [], []

    def box(self, kind):
        def shown(parent, title, text, *args, **kwargs):
            said = f"{title}\n{text}"
            self.seen.append((kind, said))
            if kind == "question":
                return next((button for words, button in self.answers if words in said), QMessageBox.No)
            return QMessageBox.Ok
        return staticmethod(shown)

    def look(self):
        window = QApplication.activeModalWidget()
        if window is not None and window.isVisible():
            words = [window.windowTitle(), *(label.text() for label in window.findChildren(QLabel))]
            self.seen.append(("modal", "\n".join(words)))
            window.close()

    def take(self, kind: str | None = None) -> list[str]:
        """The boxes of `kind` (None: all) shown so far; they are looked at (the test ends with none unlooked at)."""
        taken = [said for k, said in self.seen if kind is None or k == kind]
        self.seen = [(k, said) for k, said in self.seen if kind is not None and k != kind]
        return taken


@pytest.fixture
def boxes(monkeypatch, qapp):
    found = Boxes()
    for kind in ("question", "information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, found.box(kind))
    watch = QTimer(interval=200)
    watch.timeout.connect(found.look)
    watch.start()
    yield found
    watch.stop()
    assert found.seen == [], f"message boxes or windows the test did not expect: {found.seen}"


@pytest.fixture
def this(folders, tmp_path) -> Path:
    """This Taskloom's program folder: the zip of this version, unpacked by hand (into Downloads)."""
    return unpacked(tmp_path / "Downloads" / "taskloom", __version__)


@pytest.fixture
def editor(qtbot, tmp_path, monkeypatch, folders, this, releases, boxes):
    """The real editor window (offscreen) as the packaged Windows Taskloom from `this` folder, on a temporary Taskloom
    folder (made after the releases are the local server's: whatever it asks never reaches GitHub); Run now's question
    answered yes; no real scheduler service started by it."""
    from taskloom.editor import dialogs
    from taskloom.editor import window as window_module
    from taskloom.editor.window import MainWindow

    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", NO_KEYRING)
    monkeypatch.setenv("TASKLOOM_HOME", str(tmp_path / "home"))  # (never ~/.taskloom, whatever starts)
    monkeypatch.setattr(window_module, "frozen", lambda: True)
    monkeypatch.setattr(window_module, "sys", PackagedSys(this / "taskloomw.exe"))
    monkeypatch.setattr(dialogs, "confirm_run_now", lambda *a, **k: (True, False))
    monkeypatch.setattr(dialogs, "offer_manual_start", lambda *a, **k: True)
    monkeypatch.setattr(MainWindow, "start_scheduler", lambda self, by_you=False: None)
    window = MainWindow(Home(tmp_path / "home"))
    qtbot.addWidget(window)
    window.show()
    yield window
    for tab in window.tabs_list():
        for run in tab.runs:
            if run.running:
                run.proc.kill()
                run.proc.waitForFinished(5000)
        tab.doc.dirty = False


class Started:
    """A program started from a version's folder under <LOCALAPPDATA>/Programs/Taskloom (its stand-in exe cannot run)."""

    def __init__(self, args, env):
        self.args, self.env, self.pid, self.returncode = [str(a) for a in args], env, 999_999, None
        self.stdin = self.stdout = self.stderr = None

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass

    terminate = kill

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def starts(monkeypatch, folders) -> list[Started]:
    """The programs started (subprocess.Popen) from a version's folder under <LOCALAPPDATA>/Programs/Taskloom; other
    programs start as usual."""
    real, started = subprocess.Popen, []

    def popen(args, *rest, **kwargs):
        program = args[0] if isinstance(args, (list, tuple)) else args
        if norm(program).startswith(norm(folders.programs) + os.sep):
            started.append(Started(args, kwargs.get("env")))
            return started[-1]
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    return started


def editors_started(starts) -> list[Started]:
    return [s for s in starts if s.args[1:] and s.args[-1] == "editor"]


def services_started(starts) -> list[Started]:
    return [s for s in starts if "scheduler" in s.args]


def help_items(window) -> list[str]:
    """The items of the editor's Help menu."""
    (help_menu,) = [a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "Help"]
    return [a.text().replace("&", "") for a in help_menu.actions() if not a.isSeparator()]


def check_for_updates(window):
    """Help > Check for updates."""
    (help_menu,) = [a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "Help"]
    found = [a for a in help_menu.actions() if a.text().replace("&", "").rstrip("…").strip() == CHECK]
    assert found, f"Help has no {CHECK!r}: {help_items(window)}"
    found[0].trigger()


def shown_widget(window, name: str) -> QWidget | None:
    """The visible widget of this objectName, in the editor window or a window of its own."""
    for top in [window, *QApplication.topLevelWidgets()]:
        found = top if top.objectName() == name else top.findChild(QWidget, name)
        if found is not None and found.isVisible():
            return found
    return None


def button(widget: QWidget, text: str) -> QAbstractButton:
    found = [b for b in widget.findChildren(QAbstractButton) if b.text().replace("&", "") == text and b.isVisible()]
    assert found, f"no {text!r} button: {[b.text() for b in widget.findChildren(QAbstractButton)]}"
    return found[0]


def words_of(widget: QWidget) -> str:
    return " ".join([widget.windowTitle(), *(label.text() for label in widget.findChildren(QLabel))])


def program_folder_contents(folder: Path) -> dict:
    """{relative path: bytes} of every file in a program folder, the install marker left out."""
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*"))
            if p.is_file() and p.name != MARKER}


def leftovers(folders: Folders) -> list[str]:
    """What is in <LOCALAPPDATA>/Programs/Taskloom other than the versions' folders the test made itself."""
    if not folders.programs.exists():
        return []
    return sorted(p.name for p in folders.programs.iterdir())


def taskloom_folder(home: Path) -> dict:
    """The Taskloom folder's settings, history, credentials, schedules and runs: every file's bytes, the history as its
    rows (SQLite's own side files and the editor's window settings, recovery copies and crash log left out)."""
    found = {}
    for path in sorted(home.rglob("*")):
        relative = path.relative_to(home).as_posix()
        if (not path.is_file() or relative.split("/")[0] in ("recovery", "editor.ini", "crash.log")
                or relative.endswith(("-wal", "-shm"))):
            continue
        if relative == "history.db":
            with contextlib.closing(sqlite3.connect(path)) as db:
                found[relative] = list(db.iterdump())
        else:
            found[relative] = path.read_bytes()
    return found


def a_taskloom_folder(home: Path, flows: Path) -> Path:
    """A Taskloom folder in use: settings, connections, a secret, a scheduled flow and a run of it (by `taskloom run`, as
    from a terminal). The flow (in `flows`)."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.yaml").write_text("max_concurrent_runs: 2\n", encoding="utf-8")
    (home / "connections.yaml").write_text("mailer: {kind: smtp, host: 127.0.0.1, port: 2525}\n", encoding="utf-8")
    (home / "secrets.yaml").write_text("api_token: not-a-real-one\n", encoding="utf-8")
    flow = flows / "nightly.yaml"
    flow.parent.mkdir(parents=True, exist_ok=True)
    flow.write_text(yaml.safe_dump({"blocks": {"nightly": {"type": "trigger.schedule", "config": {"cron": "0 2 1 1 *"}},
                                               "work": {"type": "logic.python",
                                                        "config": {"inputs": [], "outputs": [], "code": "pass"}}},
                                    "edges": ["nightly.start -> work.after"]}, sort_keys=False), encoding="utf-8")
    Home(home).set_scheduled(flow.resolve(), True)
    done = subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow), "--trigger", "nightly"],
                          env={**os.environ, "TASKLOOM_HOME": str(home), "PYTHON_KEYRING_BACKEND": NO_KEYRING},
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    return flow


def install(editor, qtbot, boxes, restart: int = QMessageBox.No, timeout: int = 60_000):
    """Help > Check for updates; Install? Yes; wait until the restart is offered (answered `restart`)."""
    boxes.answers = [(INSTALL, QMessageBox.Yes), (RESTART, restart)]
    check_for_updates(editor)
    qtbot.waitUntil(lambda: any(RESTART in said for kind, said in boxes.seen if kind == "question")
                    or any(kind == "warning" for kind, _ in boxes.seen), timeout=timeout)
    assert [said for kind, said in boxes.seen if kind == "warning"] == [], "the install failed"


# --- U1: Check for updates, Install, the download --------------------------------------------------------------------

@pytest.mark.invariant("UP-1")
@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-3")
@pytest.mark.invariant("UP-5")
@pytest.mark.invariant("UP-6")
def test_u1_u9_check_for_updates_installs_the_newer_release_while_the_editor_stays_usable(
        editor, qtbot, tmp_path, folders, this, releases, boxes, starts):
    """U1, U9: Taskloom 0.39.x unpacked by hand; its Start menu and Startup entries start it. Help > Check for updates:
    the newest release, 0.40.0, is newer: "Install?" with its version. Yes: the zip downloads with a progress bar in a
    window that does not block the editor (a new flow can be made meanwhile), then unpacks into
    <LOCALAPPDATA>/Programs/Taskloom/0.40.0: exactly the zip's files, plus the marker naming the folder it updated from;
    nothing else is left there. Both entries now start 0.40.0, their arguments (the Taskloom folder) kept. The Taskloom
    folder (settings, history, credentials, schedules) and the flow are byte-equal before and after. Then "Restart
    Taskloom now with version 0.40.0?"; No keeps this editor open and starts nothing (U8)."""
    home = editor.home.root
    flow = a_taskloom_folder(home, tmp_path / "flows")
    flow_bytes, flow_time = flow.read_bytes(), flow.stat().st_mtime_ns
    both_entries(folders, this, home)
    before = taskloom_folder(home)
    releases.hold = threading.Event()
    boxes.answers = [(INSTALL, QMessageBox.Yes), (RESTART, QMessageBox.No)]
    tabs = len(editor.tabs_list())

    check_for_updates(editor)
    qtbot.waitUntil(lambda: shown_widget(editor, "update-progress") is not None, timeout=30_000)
    progress = shown_widget(editor, "update-progress")
    (progress_bar,) = progress.findChildren(QProgressBar)
    qtbot.waitUntil(lambda: 0 < progress_bar.value() < progress_bar.maximum(), timeout=30_000)
    assert QApplication.activeModalWidget() is None, "the download window blocks the editor"
    next(a for a in editor.findChildren(QAction) if a.text() == "New flow").trigger()
    assert len(editor.tabs_list()) == tabs + 1, "the editor did not make a new flow while the download went on"
    releases.hold.set()
    qtbot.waitUntil(lambda: any(RESTART in said for _, said in boxes.seen), timeout=60_000)

    (asked_install, asked_restart) = boxes.take("question")
    assert NEW in asked_install and INSTALL in asked_install, asked_install
    assert RESTART in asked_restart
    installed = folders.programs / NEW
    assert program_folder_contents(installed) == zip_files(releases.zip), "the version's folder is not the zip's"
    marker = json.loads((installed / MARKER).read_text(encoding="utf-8"))
    assert norm(marker["from"]) == norm(this) and sorted(marker["files"]) == sorted(zip_files(releases.zip)), marker
    assert leftovers(folders) == [NEW], f"left in {folders.programs}: {leftovers(folders)}"
    assert entries_now(folders) == {"Start menu": entry_for(installed, editor_args(home)),
                                    "Startup": entry_for(installed, login_args(home))}
    assert taskloom_folder(home) == before, "an install changed the Taskloom folder"
    assert flow.read_bytes() == flow_bytes and flow.stat().st_mtime_ns == flow_time, "an install changed the flow"
    (api, *_) = [r for r in releases.requests if r["path"] == API_PATH]
    headers = {k.lower(): v for k, v in api["headers"].items()}
    assert headers.get("accept") == "application/vnd.github+json", headers
    assert headers.get("user-agent", "").startswith("taskloom/"), headers
    assert shown_widget(editor, "update-progress") is None, "the download window stays open"
    qtbot.wait(1500)
    assert editor.isVisible() and editors_started(starts) == [], "No to the restart did not keep this editor"


@pytest.mark.invariant("UP-1")
@pytest.mark.invariant("UP-3")
def test_u1_cancel_during_the_download_leaves_nothing_behind(editor, qtbot, folders, this, releases, boxes, starts):
    """U1: Install? Yes; while the zip downloads, Cancel in the download window: the window closes, nothing is left in
    <LOCALAPPDATA>/Programs/Taskloom (no version folder, no part of a download), the entries still start this version,
    no message (you cancelled), no restart offered."""
    both_entries(folders, this, editor.home.root)
    entries = entries_now(folders)
    releases.hold = threading.Event()
    boxes.answers = [(INSTALL, QMessageBox.Yes)]

    check_for_updates(editor)
    qtbot.waitUntil(lambda: shown_widget(editor, "update-progress") is not None, timeout=30_000)
    progress = shown_widget(editor, "update-progress")
    (progress_bar,) = progress.findChildren(QProgressBar)
    qtbot.waitUntil(lambda: progress_bar.value() > 0, timeout=30_000)
    QTest.mouseClick(button(progress, "Cancel"), Qt.LeftButton)
    qtbot.waitUntil(lambda: shown_widget(editor, "update-progress") is None, timeout=10_000)
    releases.hold.set()
    qtbot.wait(2000)

    asked = boxes.take("question")
    assert len(asked) == 1 and INSTALL in asked[0], asked
    assert boxes.take() == [], "a message after Cancel"
    assert leftovers(folders) == [], f"left in {folders.programs}: {leftovers(folders)}"
    assert entries_now(folders) == entries
    assert starts == []


# --- U2: nothing newer; never by itself; only on Windows ---------------------------------------------------------------

@pytest.mark.parametrize("tag", [f"v{__version__}", "v0.38.0"], ids=["the same version", "an older version"])
@pytest.mark.invariant("UP-1")
def test_u2_no_newer_release_says_you_have_the_newest_version_and_downloads_nothing(editor, qtbot, folders, releases,
                                                                                   boxes, tag):
    """U2: the newest release is this version, or older (a fix of an older line): "You have the newest version", no
    question, no download, nothing installed."""
    releases.tag = tag

    check_for_updates(editor)
    qtbot.waitUntil(lambda: bool(boxes.seen), timeout=30_000)
    qtbot.wait(500)

    (said,) = boxes.take()
    assert "You have the newest version" in said, said
    assert releases.downloads() == [] and leftovers(folders) == []


@pytest.mark.invariant("UP-1")
def test_u2_taskloom_never_checks_for_updates_by_itself(editor, qtbot, tmp_path, releases, boxes):
    """U2: opened, and while you work (a flow opened, saved, closed), the editor never asks the releases anything."""
    qtbot.wait(3000)
    tab = editor.new_flow()
    editor.close_tab(tab)
    qtbot.wait(2500)

    assert releases.requests == [], [r["path"] for r in releases.requests]


@pytest.mark.parametrize("platform", [None, "darwin", "linux"], ids=["from source", "macOS", "Linux"])
@pytest.mark.invariant("UP-1")
def test_u2_help_has_check_for_updates_only_in_the_windows_taskloom(qtbot, tmp_path, monkeypatch, releases,
                                                                    platform):
    """U2: Check for updates is in Help only in the packaged Windows Taskloom: not when Taskloom runs from its source,
    not in a packaged Taskloom on macOS or Linux; in the packaged Windows one it is."""
    from taskloom.editor import window as window_module
    from taskloom.editor.window import MainWindow

    monkeypatch.setattr(MainWindow, "start_scheduler", lambda self, by_you=False: None)
    program = tmp_path / "taskloom" / "taskloomw.exe"

    def opened(packaged_on):
        with monkeypatch.context() as m:
            if packaged_on:
                m.setattr(window_module, "frozen", lambda: True)
                m.setattr(window_module, "sys", PackagedSys(program, packaged_on))
            window = MainWindow(Home(tmp_path / "home"))
        qtbot.addWidget(window)
        return [item.rstrip("…").strip() for item in help_items(window)]

    assert CHECK not in opened(platform)
    assert CHECK in opened("win32")


# --- U3: a failed update changes nothing ------------------------------------------------------------------------------

FAILURES = {
    "no connection": ({"no connection": True}, []),
    "release info 404": ({"api_status": 404}, ["404"]),
    "download 500": ({"download_status": 500}, ["500"]),
    "download cut short": ({"cut_short": True}, ["incomplete"]),
    "checksum differs": ({"digest": "sha256:" + "0" * 64}, ["checksum"]),
    "broken zip": ({"zip": release_zip(damaged=True)}, ["damaged"]),
    "no taskloomw.exe": ({"zip": release_zip(leave_out=("taskloom/taskloomw.exe",))}, ["taskloomw.exe"]),
    "VERSION not the release's": ({"zip": release_zip(version_text="0.39.9")}, ["VERSION"]),
    "full disk": ({"free": 1000}, ["disk space"]),
    "plain http": ({"scheme": "http"}, ["refused", "http://127.0.0.1:"]),
    "redirect to another host": ({"redirect": "another host"}, ["refused", "localhost"]),
}


@pytest.mark.parametrize("failure", list(FAILURES))
@pytest.mark.invariant("UP-3")
def test_u3_a_failed_update_says_what_went_wrong_and_changes_nothing(editor, qtbot, monkeypatch, folders, this,
                                                                     releases, boxes, starts, failure):
    """U3: no connection, the release info or the download answered with an HTTP error, a download cut short, a zip
    whose checksum is not the release's, a damaged zip, a zip without taskloomw.exe or with another VERSION, a full
    disk, a download URL that is not https, a redirect to a host that is not GitHub's: a message says what went wrong;
    nothing is installed or left behind; the entries still start this version; the editor goes on working."""
    change, says = FAILURES[failure]
    both_entries(folders, this, editor.home.root)
    entries = entries_now(folders)
    for name, value in change.items():
        if name == "no connection":
            point_updates_at(monkeypatch, f"https://127.0.0.1:{free_port()}{API_PATH}")
        elif name == "free":
            usage = shutil.disk_usage(folders.root.parent)
            monkeypatch.setattr(shutil, "disk_usage",
                                lambda path, free=value: type(usage)(usage.total, usage.total - free, free))
        else:
            setattr(releases, name, value)
    boxes.answers = [(INSTALL, QMessageBox.Yes)]
    tabs = len(editor.tabs_list())

    check_for_updates(editor)
    qtbot.waitUntil(lambda: any(kind == "warning" for kind, _ in boxes.seen), timeout=60_000)
    qtbot.wait(1000)

    told = boxes.take("warning")
    boxes.take("question")
    assert len(told) == 1 and told[0].strip(), told
    for words in says:
        assert words in told[0], f"the message does not say {words!r}: {told[0]!r}"
    assert boxes.take() == []
    assert leftovers(folders) == [], f"left in {folders.programs}: {leftovers(folders)}"
    assert entries_now(folders) == entries
    assert starts == []
    if failure == "plain http":
        assert releases.plain_requests == [], "the plain-http URL was asked"
    if failure == "redirect to another host":
        assert ASSET_PATH not in releases.downloads(), "the redirect to another host was followed"
    editor.new_flow()
    assert len(editor.tabs_list()) == tabs + 1 and shown_widget(editor, "update-progress") is None


# --- U4, U5, U6: the entries follow the newest version -----------------------------------------------------------------

@pytest.mark.parametrize("before", ["this version", "a folder that is gone", "none", "only the Startup entry",
                                    "a newer version"])
@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-4")
def test_u4_an_install_points_the_entries_at_the_new_version_straight_away(editor, qtbot, tmp_path, folders, this,
                                                                           releases, boxes, before):
    """U4: right after an install of 0.40.0, each entry there is starts 0.40.0 (its arguments kept): one that started
    this version, or a folder that is gone; an entry that was not there is not made; one that starts a newer version
    still there is left as it was."""
    home, installed = editor.home.root, folders.programs / NEW
    if before == "this version":
        both_entries(folders, this, home)
    elif before == "a folder that is gone":
        both_entries(folders, tmp_path / "deleted" / "taskloom", home)
    elif before == "only the Startup entry":
        write_entry(folders.startup, this / "taskloomw.exe", login_args(home), this)
    elif before == "a newer version":
        both_entries(folders, unpacked(folders.programs / "99.0.0", "99.0.0"), home)
    entries = entries_now(folders)

    install(editor, qtbot, boxes)

    boxes.take("question")
    expected = {"Start menu": entry_for(installed, editor_args(home)), "Startup": entry_for(installed, login_args(home))}
    if before == "none":
        expected = {"Start menu": None, "Startup": None}
    elif before == "only the Startup entry":
        expected["Start menu"] = None
    elif before == "a newer version":
        expected = entries
    assert entries_now(folders) == expected


PACKAGED = r'''
import json, os, sys, time
from pathlib import Path

Path(os.environ["TEST_PID"]).write_text(str(os.getpid()), encoding="utf-8")


class Packaged:
    """What taskloom.cli sees as `sys`: the packaged Windows Taskloom whose program is TEST_PROGRAM."""
    platform = "win32"
    frozen = True
    executable = os.environ["TEST_PROGRAM"]

    def __getattr__(self, name):
        return getattr(sys, name)


import taskloom.cli as cli

cli.sys = Packaged()
cli.frozen = lambda: True
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

    def run(self, *args) -> subprocess.CompletedProcess:
        """`taskloom <args>` from a terminal (taskloom.exe), to its end."""
        return subprocess.run([sys.executable, "-c", PACKAGED, *map(str, args)], env=self.environ("taskloom.exe"),
                              cwd=self.env.root, capture_output=True, text=True, timeout=120)

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


def gone_said(lines) -> list[str]:
    return [line for line in lines if "gone" in line and __version__ in line]


@pytest.mark.parametrize("start", ["editor", "taskloom scheduler", "taskloom run"])
@pytest.mark.parametrize("before", ["an older version", "a folder that is gone"])
@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-4")
def test_u4_the_first_start_of_a_newer_version_unpacked_by_hand_points_the_entries_at_itself(env, folders, before,
                                                                                            start):
    """U4: both entries start 0.30.0 (still there), or a folder that is gone. This version, unpacked by hand into
    <LOCALAPPDATA>/Programs/Taskloom, starts for the first time: its editor (from taskloomw.exe), `taskloom scheduler`
    (as at login; the service is off, so it ends at once) or `taskloom run`. Both entries now start this version,
    their arguments kept. An entry that started a folder that is gone is said: on stderr, or in the editor's status
    bar, with "gone" and this version."""
    home = env.home
    this = unpacked(folders.programs / __version__, __version__)
    old = (unpacked(folders.programs / "0.30.0", "0.30.0") if before == "an older version"
           else env.root / "deleted" / "taskloom")
    both_entries(folders, old, home)
    taskloom = Packaged(env, folders, this)

    if start == "editor":
        proc = taskloom.popen("editor", program="taskloomw.exe", status=True)
        try:
            pointed = wait_until(lambda: entries_now(folders)["Startup"] == entry_for(this, login_args(home))
                                 and entries_now(folders)["Start menu"] == entry_for(this, editor_args(home))
                                 and (before != "a folder that is gone" or gone_said(taskloom.said_in_status_bar())),
                                 45)
        finally:
            output = taskloom.end(proc)
        said = taskloom.said_in_status_bar()
    else:
        if start == "taskloom scheduler":
            set_paused(Home(home), True)
            done = taskloom.run("scheduler", "--unless-paused")
        else:
            done = taskloom.run("run", env.write("hello.yaml", HELLO))
        assert done.returncode == 0, done.stdout + done.stderr
        pointed, said, output = True, done.stderr.splitlines(), done.stdout + done.stderr

    assert pointed and entries_now(folders) == {"Start menu": entry_for(this, editor_args(home)),
                                                "Startup": entry_for(this, login_args(home))}, (entries_now(folders),
                                                                                                 output)
    if before == "a folder that is gone":
        assert gone_said(said), f"not said that the entries' folder was gone: {said}"


@pytest.mark.parametrize("before", ["this version already", "none", "only the Startup entry, older"])
@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-4")
def test_u4_a_start_makes_no_entry_and_leaves_one_that_needs_no_change(env, folders, before):
    """U4: entries that already start this version are left as they are (not written again); no entry is made where
    there is none (only the two Settings boxes make them); with only the Startup entry (older), only it is changed."""
    home = env.home
    this = unpacked(folders.programs / __version__, __version__)
    if before == "this version already":
        both_entries(folders, this, home)
    elif before == "only the Startup entry, older":
        old = unpacked(folders.programs / "0.30.0", "0.30.0")
        write_entry(folders.startup, old / "taskloomw.exe", login_args(home), old)
    taskloom = Packaged(env, folders, this)

    done = taskloom.run("run", env.write("hello.yaml", HELLO))

    assert done.returncode == 0, done.stdout + done.stderr
    if before == "this version already":
        assert entries_now(folders) == {"Start menu": entry_for(this, editor_args(home)),
                                        "Startup": entry_for(this, login_args(home))}
        assert taskloom.written() == [], f"entries written again: {taskloom.written()}"
    elif before == "none":
        assert entries_now(folders) == {"Start menu": None, "Startup": None}
        assert not folders.startup.with_suffix(".cmd").exists()
    else:
        assert entries_now(folders) == {"Start menu": None, "Startup": entry_for(this, login_args(home))}


@pytest.mark.invariant("UP-2")
def test_u4_todays_startup_script_is_replaced_by_a_shortcut_for_its_taskloom_folder(env, folders):
    """U4: the Startup folder has today's "Taskloom Scheduler.cmd" (Taskloom 0.30.0, for a Taskloom folder of its
    own). The first start of this version replaces it by the Startup entry Taskloom Scheduler.lnk: this version's
    program, for that same Taskloom folder; the .cmd is gone (no console window flashes at login any more)."""
    old = unpacked(folders.programs / "0.30.0", "0.30.0")
    other_home = env.root / "other home"
    script = folders.startup.with_suffix(".cmd")
    script.parent.mkdir(parents=True)
    script.write_text(f'@echo off\nchcp 65001 >nul\nrem Taskloom 0.30.0\nset "TASKLOOM_HOME={other_home}"\n'
                      f'start "" "{old / "taskloomw.exe"}" scheduler --unless-paused\n', encoding="utf-8",
                      newline="\r\n")
    this = unpacked(folders.programs / __version__, __version__)

    done = Packaged(env, folders, this).run("run", env.write("hello.yaml", HELLO))

    assert done.returncode == 0, done.stdout + done.stderr
    assert read_entry(folders.startup) == entry_for(this, login_args(other_home))
    assert not script.exists(), "today's start script is still in the Startup folder"
    assert read_entry(folders.start_menu) is None


@pytest.mark.invariant("UP-2")
def test_u4_settings_adds_and_removes_the_start_menu_entry_next_to_starting_at_login(qtbot, tmp_path, monkeypatch,
                                                                                    folders, this):
    """U4: Tools > Settings has "Add Taskloom to the Start menu" next to "Start the scheduler service when I log in".
    Ticked and saved: the Start menu entry starts this Taskloom's editor for this Taskloom folder, the Startup entry
    its scheduler service (unless you turned it off); each box shows ticked when opened again. Unticked and saved: both
    entries are gone."""
    from taskloom.editor import dialogs
    from taskloom.editor.window import MainWindow

    monkeypatch.setattr(MainWindow, "start_scheduler", lambda self, by_you=False: None)
    monkeypatch.setattr(dialogs, "sys", PackagedSys(this / "taskloomw.exe"))
    window = MainWindow(Home(tmp_path / "home"))
    qtbot.addWidget(window)
    home = window.home.root

    def box(dialog, text) -> QCheckBox:
        found = [b for b in dialog.findChildren(QCheckBox) if b.text().replace("&", "") == text]
        assert len(found) == 1, f"no box {text!r}: {[b.text() for b in dialog.findChildren(QCheckBox)]}"
        return found[0]

    def saved_with(ticked: bool) -> tuple[bool, bool]:
        dialog = dialogs.SettingsDialog(window.home, window)
        start_menu, at_login = box(dialog, START_MENU_BOX), box(dialog, LOGIN_BOX)
        assert start_menu.isEnabled() and start_menu.parentWidget() is at_login.parentWidget()
        start_menu.setChecked(ticked)
        at_login.setChecked(ticked)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
        again = dialogs.SettingsDialog(window.home, window)
        return box(again, START_MENU_BOX).isChecked(), box(again, LOGIN_BOX).isChecked()

    assert saved_with(True) == (True, True)
    assert entries_now(folders) == {"Start menu": entry_for(this, editor_args(home)),
                                    "Startup": entry_for(this, login_args(home))}
    assert not folders.startup.with_suffix(".cmd").exists()
    assert saved_with(False) == (False, False)
    assert entries_now(folders) == {"Start menu": None, "Startup": None}


@pytest.mark.invariant("UP-2")
def test_u4_home_given_on_the_command_line_is_the_taskloom_folder_used(env, tmp_path):
    """U4: the entries start Taskloom with --home <Taskloom folder> (a shortcut cannot set TASKLOOM_HOME): `taskloom
    --home <folder> run` records its run in that folder, not in the one TASKLOOM_HOME names."""
    chosen = tmp_path / "chosen home"
    flow = env.write("hello.yaml", HELLO)

    done = env.taskloom("--home", chosen, "run", flow)

    assert done.returncode == 0, done.stdout + done.stderr
    with contextlib.closing(sqlite3.connect(chosen / "history.db")) as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone() == (1,)
    assert not (env.home / "history.db").exists(), "the run went to TASKLOOM_HOME's folder"


@pytest.mark.invariant("UP-4")
def test_u5_an_older_version_leaves_a_newer_versions_entries_until_that_folder_is_gone(env, folders):
    """U5: the entries start 99.0.0, which is there. This (older) version started does not take them. Once 99.0.0's
    folder is deleted, the next start of this version points them at itself and says so."""
    home = env.home
    newer = unpacked(folders.programs / "99.0.0", "99.0.0")
    both_entries(folders, newer, home)
    this = unpacked(folders.programs / __version__, __version__)
    taskloom = Packaged(env, folders, this)
    flow = env.write("hello.yaml", HELLO)

    first = taskloom.run("run", flow)
    assert first.returncode == 0, first.stdout + first.stderr
    assert entries_now(folders) == {"Start menu": entry_for(newer, editor_args(home)),
                                    "Startup": entry_for(newer, login_args(home))}, "an older version took the entries"
    shutil.rmtree(newer)
    again = taskloom.run("run", flow)

    assert again.returncode == 0, again.stdout + again.stderr
    assert entries_now(folders) == {"Start menu": entry_for(this, editor_args(home)),
                                    "Startup": entry_for(this, login_args(home))}
    assert gone_said(again.stderr.splitlines()), again.stderr


@pytest.mark.invariant("UP-2")
def test_u6_two_quick_starts_of_the_new_version_write_each_entry_once_and_whole(env, folders):
    """U6: both entries start 0.30.0; this version is started twice at the same moment (`taskloom run` twice). Each
    entry is written exactly once, and each reads whole: this version, its arguments kept."""
    home = env.home
    old = unpacked(folders.programs / "0.30.0", "0.30.0")
    both_entries(folders, old, home)
    this = unpacked(folders.programs / __version__, __version__)
    flow, go = env.write("hello.yaml", HELLO), env.root / "go"
    starts = [Packaged(env, folders, this, name=f"start{n}").popen("run", flow, go=go) for n in (1, 2)]
    time.sleep(3)  # (both have loaded Taskloom and wait for the file)
    go.write_text("go", encoding="utf-8")
    outputs = [proc.communicate(timeout=120) for proc in starts]

    assert [proc.returncode for proc in starts] == [0, 0], outputs
    assert entries_now(folders) == {"Start menu": entry_for(this, editor_args(home)),
                                    "Startup": entry_for(this, login_args(home))}
    written = Packaged(env, folders, this).written()
    assert sorted("Scheduler" in name for name in written) == [False, True], f"entries written: {written}"


# --- U7, U6: the scheduler service's hand-over -------------------------------------------------------------------------

def holder(home: Path) -> int | None:
    """The process that holds the Taskloom folder (scheduler.pid), if it is alive."""
    try:
        pid = int((home / "scheduler.pid").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return None if ended(pid) else pid


def holder_version(home: Path) -> str | None:
    """The Taskloom version of the process that holds the folder, as scheduler.version says."""
    try:
        version, _, pid = (home / "scheduler.version").read_text(encoding="utf-8").strip().partition(" ")
    except OSError:
        return None
    return version if pid.isdigit() and int(pid) == holder(home) else None


def service(env, name: str) -> subprocess.Popen:
    """`taskloom scheduler --headless` of this Taskloom, for the Taskloom folder env.home; what it says goes to
    <name>.log."""
    log = open(env.root / f"{name}.log", "w", encoding="utf-8")
    try:
        return subprocess.Popen([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"],
                                env={**env.env, "PYTHON_KEYRING_BACKEND": NO_KEYRING}, cwd=env.root,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    finally:
        log.close()


def log_of(env, name: str) -> str:
    path = env.root / f"{name}.log"
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def older_service(env, version: str | None = "0.0.1", name: str = "old") -> tuple[subprocess.Popen, int]:
    """A scheduler service of an older Taskloom holds the folder: a real one whose scheduler.version is made `version`'s
    (None: none, as before 0.14.1). Its process and the pid it holds the folder with."""
    proc = service(env, name)
    assert wait_until(lambda: holder(env.home) is not None and holder_version(env.home) == __version__, 30), \
        log_of(env, name)
    pid = holder(env.home)
    if version is None:
        (env.home / "scheduler.version").unlink()
    else:
        (env.home / "scheduler.version").write_text(f"{version} {pid}", encoding="utf-8")
    return proc, pid


class Owners:
    """Watches the Taskloom folder every 20 ms: the process scheduler.pid names (the service that holds it) and whether
    a process it named before is still alive meanwhile (then two services would own the folder). `taken`: (moment,
    pid) each time it names another process."""

    def __init__(self, home: Path):
        self.home, self.named, self.both, self.taken = home, [], [], []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)

    def _watch(self):
        while not self._stop.is_set():
            try:
                pid = int((self.home / "scheduler.pid").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pid = None
            if pid is not None and pid not in self.named:
                self.named.append(pid)
                self.taken.append((dt.datetime.now(), pid))
            alive = [p for p in self.named if p != pid and pid_alive(p) and not ended(p)]
            if pid is not None and alive:
                self.both.append((dt.datetime.now().strftime("%H:%M:%S.%f"), pid, alive))
            time.sleep(0.02)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=10)


@contextlib.contextmanager
def cleaned_up(env, procs: list):
    """At the end: every service and run of the test's Taskloom folder is stopped."""
    try:
        yield procs
    finally:
        for _ in range(3):
            stop_the_service(env.home)
            with contextlib.suppress(OSError, ValueError):
                waiting = int((env.home / "scheduler.waiting").read_text(encoding="utf-8").split()[1])
                if pid_alive(waiting) and waiting != os.getpid():
                    os.kill(waiting, signal.SIGTERM)
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=15)
        for path in env.root.glob("*.yaml"):
            (path.parent / f"{path.stem}.go").write_text("go", encoding="utf-8")
        end_runs(env.home)


@pytest.mark.parametrize("older", ["0.0.1", None], ids=["an older version", "before 0.14.1 (says no version)"])
@pytest.mark.invariant("UP-8")
@pytest.mark.invariant("SCH-1")
def test_u7_a_newer_service_takes_over_from_an_older_one_at_a_quiet_moment(env, older):
    """U7: an older Taskloom's scheduler service runs (nothing scheduled: every moment is quiet). This version's
    `taskloom scheduler` starts: it does not end as "already running"; it takes over: the old service has ended, this
    one holds the folder (scheduler.pid, scheduler.version: this version) and checks (its heartbeat goes on). At no
    moment do both own the folder."""
    with cleaned_up(env, []) as procs:
        old, old_pid = older_service(env, older)
        procs.append(old)
        with Owners(env.home) as owners:
            procs.append(service(env, "new"))
            took_over = wait_until(lambda: holder(env.home) not in (None, old_pid)
                                   and holder_version(env.home) == __version__, 30)
            beat = (env.home / "scheduler.heartbeat").read_text(encoding="utf-8") if took_over else None
            checking = took_over and wait_until(
                lambda: (env.home / "scheduler.heartbeat").read_text(encoding="utf-8") != beat, 10)

    assert took_over, f"the newer service did not take over; it said:\n{log_of(env, 'new')}"
    assert ended(old_pid), "the old service still runs"
    assert checking, "the newer service holds the folder but does not check"
    assert owners.both == [], f"two services owned the folder: {owners.both}"


@pytest.mark.invariant("UP-8")
@pytest.mark.invariant("SCH-1")
@pytest.mark.invariant("SCH-6")
def test_u7_a_time_due_within_2_minutes_runs_on_the_old_service_then_the_newer_takes_over(env):
    """U7: soon's time is 20 s away when this version's service starts beside an older one: no quiet moment (a time is
    due within 2 minutes), so it waits; the old service runs that time; once that run has ended and nothing is due, the
    newer one takes over. The time ran exactly once; at no moment did both own the folder."""
    due = (dt.datetime.now() + dt.timedelta(seconds=20)).replace(microsecond=0)
    schedule(env.home, env.root, {"soon": flow_yaml({"at": f"{due:%Y-%m-%d %H:%M:%S}", "misfire": "catch_up"})})
    with cleaned_up(env, []) as procs:
        old, old_pid = older_service(env)
        procs.append(old)
        with Owners(env.home) as owners:
            procs.append(service(env, "new"))
            took_over = wait_until(lambda: holder(env.home) not in (None, old_pid)
                                   and holder_version(env.home) == __version__, 60)
        runs = [r for r in history(env.home) if r["flow"] == "soon"]

    assert took_over, f"the newer service did not take over; it said:\n{log_of(env, 'new')}"
    assert [(r["status"], r["when"] and dt.datetime.fromisoformat(r["when"]).replace(tzinfo=None)) for r in runs] \
        == [("success", due)], runs
    moment = next(moment for moment, pid in owners.taken if pid != old_pid)
    assert moment >= runs[0]["finished"], \
        f"it took over at {moment:%H:%M:%S.%f}, before soon's run for {due:%H:%M:%S} had ended ({runs[0]['finished']})"
    assert owners.both == [], f"two services owned the folder: {owners.both}"


def waited_since(home: Path, minutes: int) -> bool:
    """The waiting service began waiting `minutes` ago, as its scheduler.waiting says from now on."""
    try:
        version, pid, _ = (home / "scheduler.waiting").read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        return False
    since = (dt.datetime.now() - dt.timedelta(minutes=minutes)).replace(microsecond=0).isoformat()
    (home / "scheduler.waiting").write_text(f"{version} {pid} {since}", encoding="utf-8")
    return True


@pytest.mark.invariant("UP-8")
@pytest.mark.invariant("SCH-1")
@pytest.mark.invariant("SCH-6")
def test_u7_after_an_hour_without_a_quiet_moment_it_takes_over_when_a_run_ends_and_adopts_the_runs_going(env):
    """U7: the old service has started a run of `first` and one of `second`; both go on. This version's service starts
    and waits (runs are going: no quiet moment). It has waited an hour: the first check after `first`'s run ends, it
    takes over, though `second`'s run still goes; that run finishes and is recorded once, a success (the new service
    took it over); neither flow runs again."""
    schedule(env.home, env.root, {"first": flow_yaml(HOURLY, work=hold_until("first")),
                                  "second": flow_yaml(HOURLY, work=hold_until("second"))})
    with cleaned_up(env, []) as procs:
        old, old_pid = older_service(env)
        procs.append(old)
        assert wait_until(lambda: {r["flow"] for r in history(env.home) if r["status"] == "running"}
                          == {"first", "second"}, 60), history(env.home)
        with Owners(env.home) as owners:
            procs.append(service(env, "new"))
            time.sleep(5)
            waited = holder(env.home) == old_pid and procs[-1].poll() is None
            said_waiting = wait_until(lambda: waited_since(env.home, 61), 10)
            time.sleep(2)
            still_old = holder(env.home) == old_pid
            (env.root / "first.go").write_text("go", encoding="utf-8")
            took_over = wait_until(lambda: holder(env.home) not in (None, old_pid)
                                   and holder_version(env.home) == __version__, 30)
            second_going = [r["status"] for r in history(env.home) if r["flow"] == "second"] == ["running"]
            (env.root / "second.go").write_text("go", encoding="utf-8")
            wait_until(lambda: all(r["status"] != "running" for r in history(env.home)), 30)
            time.sleep(3)  # (a few checks of the new service)
        runs = [(r["flow"], r["status"]) for r in history(env.home)]

    assert waited, f"the newer service did not wait while runs went; it said:\n{log_of(env, 'new')}"
    assert said_waiting, "the waiting service wrote no scheduler.waiting"
    assert still_old, "it took over while runs went, before an hour had passed"
    assert took_over, f"after an hour, it did not take over when first's run ended:\n{log_of(env, 'new')}"
    assert second_going, "second's run did not go on through the take-over"
    assert sorted(runs) == [("first", "success"), ("second", "success")], runs
    assert owners.both == [], f"two services owned the folder: {owners.both}"


@pytest.mark.invariant("UP-8")
@pytest.mark.invariant("SCH-2")
def test_u7_turned_off_while_the_newer_waits_it_gives_up_and_the_service_stays_off(env):
    """U7: the old service runs `long`'s run; this version's service waits beside it. You turn the service off (Let it
    finish): the waiting one ends without ever holding the folder; the old one lets the run finish, then ends; no
    service runs afterwards, and it is still off."""
    schedule(env.home, env.root, {"long": flow_yaml(HOURLY, work=hold_until("long"))})
    with cleaned_up(env, []) as procs:
        old, old_pid = older_service(env)
        procs.append(old)
        assert wait_until(lambda: [r["status"] for r in history(env.home)] == ["running"], 60), history(env.home)
        with Owners(env.home) as owners:
            procs.append(service(env, "new"))
            time.sleep(5)
            waited = procs[-1].poll() is None
            request_turn_off(Home(env.home), {})  # Proceed: its runs finish
            gave_up = wait_until(lambda: procs[-1].poll() is not None, 15)
            (env.root / "long.go").write_text("go", encoding="utf-8")
            old_ended = wait_until(lambda: ended(old_pid), 30)
            time.sleep(3)
        nobody = holder(env.home) is None

    assert waited, f"the newer service did not wait beside the older one; it said:\n{log_of(env, 'new')}"
    assert gave_up, "turned off, the waiting service went on waiting"
    assert old_ended and nobody, "the service did not turn off once the run finished"
    assert [pid for pid in owners.named if pid != old_pid] == [], "the waiting service took the folder although off"
    assert (env.home / "scheduler.paused").exists(), "the service is no longer off"
    assert [r["status"] for r in history(env.home)] == ["success"]


@pytest.mark.invariant("UP-8")
@pytest.mark.invariant("SCH-1")
def test_u6_the_new_version_started_twice_quickly_takes_over_once(env):
    """U6: the old service runs `long`'s run. This version's service is started twice at once: one ends at once, the
    other waits; when the run ends, that one takes over. At no moment do two services own the folder."""
    schedule(env.home, env.root, {"long": flow_yaml(HOURLY, work=hold_until("long"))})
    with cleaned_up(env, []) as procs:
        old, old_pid = older_service(env)
        procs.append(old)
        assert wait_until(lambda: [r["status"] for r in history(env.home)] == ["running"], 60), history(env.home)
        with Owners(env.home) as owners:
            newer = [service(env, "new1"), service(env, "new2")]
            procs.extend(newer)
            one_ended = wait_until(lambda: sum(p.poll() is not None for p in newer) == 1, 20)
            time.sleep(2)
            still_one = sum(p.poll() is None for p in newer) == 1
            (env.root / "long.go").write_text("go", encoding="utf-8")
            took_over = wait_until(lambda: holder(env.home) not in (None, old_pid)
                                   and holder_version(env.home) == __version__, 30)
            time.sleep(2)
            alive = [p for p in newer if p.poll() is None]

    said = log_of(env, "new1") + log_of(env, "new2")
    assert one_ended and still_one, f"not one waiting and one ended at once:\n{said}"
    assert took_over and len(alive) == 1, f"the waiting one did not take over:\n{said}"
    assert owners.both == [], f"two services owned the folder: {owners.both}"


@pytest.mark.parametrize("state", ["on", "not running", "turned off"])
@pytest.mark.invariant("UP-8")
def test_u7_an_install_starts_the_new_versions_service_only_if_one_runs_and_is_on(editor, qtbot, folders, releases,
                                                                                 boxes, starts, state):
    """U7: an install starts the new version's scheduler service at once (it then takes over at a quiet moment) when a
    service runs and you have not turned it off; none when no service runs, or it is off."""
    home = editor.home.root
    service_proc = stand_in_service(home, __version__) if state != "not running" else None
    try:
        if state == "turned off":
            set_paused(Home(home), True)
        install(editor, qtbot, boxes)
    finally:
        if service_proc is not None:
            service_proc.kill()
            service_proc.wait(timeout=15)

    boxes.take("question")
    started = services_started(starts)
    if state != "on":
        assert started == [], [s.args for s in started]
        return
    (one,) = started
    assert norm(Path(one.args[0]).parent) == norm(folders.programs / NEW), one.args
    assert Path(one.args[0]).name in ("taskloom.exe", "taskloomw.exe") and "--unless-paused" in one.args, one.args
    named = (one.args[one.args.index("--home") + 1] if "--home" in one.args
             else (one.env or os.environ).get("TASKLOOM_HOME"))
    assert norm(named) == norm(home), one.args


@pytest.mark.invariant("UP-8")
@pytest.mark.invariant("SCH-1")
@pytest.mark.invariant("SCH-6")
def test_u7_lifecycle_older_and_newer_services_in_random_orders_never_two_and_every_time_once(env):
    """U7, the hand-over's lifecycle: an older Taskloom's service is on. Then, in a random order (the seed is printed;
    TASKLOOM_LIFECYCLE_SEED repeats it): older and newer services start, the service holding the folder or the one
    waiting is stopped (as by a crash or a PC shutting down), `long`'s run ends, time passes, while `tick`'s six times
    come, 6 s apart (Catch up: one missed while no service runs still runs). At the end the newer version is started
    and given time. Never do two services own the folder; every one of tick's times runs exactly once (none skipped,
    none twice); long runs once; the folder ends held by the newer version."""
    seed = int(os.environ.get("TASKLOOM_LIFECYCLE_SEED") or random.randrange(1_000_000))
    print(f"lifecycle seed {seed} (TASKLOOM_LIFECYCLE_SEED={seed} repeats it)")
    rnd = random.Random(seed)
    first = (dt.datetime.now() + dt.timedelta(seconds=15)).replace(microsecond=0)
    times = [first + dt.timedelta(seconds=6 * n) for n in range(6)]
    schedule(env.home, env.root, {
        "tick": flow_yaml(*({"at": f"{t:%Y-%m-%d %H:%M:%S}", "misfire": "catch_up"} for t in times), overlap="queue"),
        "long": flow_yaml(HOURLY, work=hold_until("long"))})
    steps, count = [], iter(range(1, 1000))

    def start(older: bool):
        name = f"{'old' if older else 'new'}{next(count)}"
        before = holder(env.home)
        proc = service(env, name)
        procs.append(proc)
        took = ""
        if older and before is None:  # nothing holds the folder: it takes it, and is made an older Taskloom's
            wait_until(lambda: proc.poll() is not None or (holder(env.home) is not None
                                                            and holder_version(env.home) == __version__), 20)
            if holder(env.home) is not None and holder_version(env.home) == __version__:
                pid = holder(env.home)
                (env.home / "scheduler.version").write_text(f"0.0.{rnd.randrange(1, 9)} {pid}", encoding="utf-8")
                took = f" (it holds the folder as 0.0.x, pid {pid})"
        steps.append(f"{dt.datetime.now():%H:%M:%S} started {name}{took}")

    def stop_waiting():
        with contextlib.suppress(OSError, ValueError):
            pid = int((env.home / "scheduler.waiting").read_text(encoding="utf-8").split()[1])
            if pid_alive(pid) and pid != os.getpid():
                os.kill(pid, signal.SIGTERM)
                wait_until(lambda: ended(pid), 15)
                steps.append(f"{dt.datetime.now():%H:%M:%S} stopped the waiting one")

    with cleaned_up(env, []) as procs:
        with Owners(env.home) as owners:
            start(older=True)  # an older Taskloom's service is on; an install starts the newer one beside it
            start(older=False)
            while dt.datetime.now() < times[-1] + dt.timedelta(seconds=6):
                action = rnd.choice(["older", "newer", "newer", "stop holder", "stop waiting", "end long", "wait",
                                     "wait"])
                if action in ("older", "newer"):
                    start(older=action == "older")
                elif action == "stop holder" and holder(env.home):
                    os.kill(holder(env.home), signal.SIGTERM)
                    steps.append(f"{dt.datetime.now():%H:%M:%S} stopped the holder")
                elif action == "stop waiting":
                    stop_waiting()
                elif action == "end long":
                    (env.root / "long.go").write_text("go", encoding="utf-8")
                    steps.append(f"{dt.datetime.now():%H:%M:%S} long's run may end")
                time.sleep(rnd.uniform(0.3, 3.0))
            # The end: long's run may end; an older Taskloom's service holds the folder (none waits beside it), and
            # the newer version is started (as an install starts it): it takes over at a quiet moment.
            (env.root / "long.go").write_text("go", encoding="utf-8")
            stop_waiting()
            if holder(env.home) is None or holder_version(env.home) == __version__:
                if holder(env.home) is not None:
                    gone = holder(env.home)
                    os.kill(gone, signal.SIGTERM)
                    wait_until(lambda: ended(gone), 15)
                    steps.append(f"{dt.datetime.now():%H:%M:%S} stopped the holder")
                start(older=True)
            start(older=False)
            done = wait_until(lambda: holder_version(env.home) == __version__
                              and len([r for r in history(env.home) if r["flow"] == "tick"]) >= len(times)
                              and all(r["status"] != "running" for r in history(env.home)), 120)
            time.sleep(3)  # (a few more checks: nothing more runs)
        runs = history(env.home)
        last = holder_version(env.home)

    story = f"seed {seed}:\n" + "\n".join(steps)
    assert owners.both == [], f"two services owned the folder: {owners.both}\n{story}"
    ran = sorted(dt.datetime.fromisoformat(r["when"]).replace(tzinfo=None) for r in runs if r["flow"] == "tick")
    assert ran == times, (f"tick's times {[f'{t:%H:%M:%S}' for t in times]} ran as "
                          f"{[f'{t:%H:%M:%S}' for t in ran]}\n{story}")
    assert {r["status"] for r in runs if r["flow"] == "tick"} == {"success"}, (runs, story)
    assert [r["status"] for r in runs if r["flow"] == "long"] == ["success"], (runs, story)
    assert done and last == __version__, f"the folder is not held by the newer version at the end ({last})\n{story}"


# --- U8: the restart offer ---------------------------------------------------------------------------------------------

@pytest.mark.invariant("UP-6")
@pytest.mark.invariant("UP-5")
def test_u8_yes_reopens_the_editor_in_the_new_version_with_its_tabs_and_loaded_states_and_starts_no_flow(
        editor, qtbot, tmp_path, folders, releases, boxes, starts):
    """U8: two flows open, one with a run loaded (Load run state). Installed 0.40.0; "Restart Taskloom now with version
    0.40.0?" Yes: this editor closes as usual and 0.40.0's editor is started for the same Taskloom folder. That editor,
    opening the folder, has both tabs back, the loaded run still loaded, and starts no flow."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    flow_b = b / "other.yaml"
    flow_b.write_text(HELLO, encoding="utf-8")
    editor.open_flow(flow_b)
    tab_a, first = full_run(editor, qtbot, a, rows=10)
    assert first.status == "success", first.log
    load_state(editor, qtbot, first.run)
    assert names(bar(tab_a), first.run), bar(tab_a)
    opened = {t.doc.path for t in editor.tabs_list()}
    with contextlib.closing(sqlite3.connect(editor.home.history_db)) as db:
        runs_before = db.execute("SELECT count(*) FROM runs").fetchone()

    install(editor, qtbot, boxes, restart=QMessageBox.Yes)
    qtbot.waitUntil(lambda: bool(editors_started(starts)) and not editor.isVisible(), timeout=30_000)

    boxes.take("question")
    (started,) = editors_started(starts)
    assert norm(started.args[0]) == norm(folders.programs / NEW / "taskloomw.exe"), started.args
    assert started.args[1:] == ["--home", str(editor.home.root), "editor"], started.args
    again = start_again(editor, qtbot)  # 0.40.0's editor, on the same Taskloom folder
    assert {t.doc.path for t in again.tabs_list()} == opened
    reopened_a = next(t for t in again.tabs_list() if t.doc.path == tab_a.doc.path)
    qtbot.waitUntil(lambda: names(bar(reopened_a), first.run), timeout=10_000)
    qtbot.wait(3000)
    with contextlib.closing(sqlite3.connect(editor.home.history_db)) as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone() == runs_before, "reopening started a flow"
    assert not any(run.running for t in again.tabs_list() for run in t.runs)


@pytest.mark.parametrize("then", ["the flow ends", "Cancel"])
@pytest.mark.invariant("UP-6")
def test_u8_yes_while_a_flow_started_here_runs_waits_for_it_and_cancel_stops_the_restart(
        editor, qtbot, tmp_path, folders, releases, boxes, starts, then):
    """U8: a flow started from this editor (long-export) is still running when Yes is answered: a window that does not
    block the editor says Taskloom restarts when long-export ends, with Cancel; the editor stays open, nothing starts
    yet. When the flow ends, the editor restarts in 0.40.0; after Cancel it does not (also not when the flow ends)."""
    folder = tmp_path / "flows"
    folder.mkdir()
    flow = folder / "long-export.yaml"
    flow.write_text(yaml.safe_dump({"blocks": {"manual": {"type": "trigger.manual"}, "hold": hold_until("hold")},
                                    "edges": ["manual.start -> hold.after"]}, sort_keys=False), encoding="utf-8")
    tab = editor.open_flow(flow)
    editor.run_flow(tab)
    qtbot.waitUntil(lambda: tab.running, timeout=30_000)
    try:
        install(editor, qtbot, boxes, restart=QMessageBox.Yes)
        qtbot.waitUntil(lambda: shown_widget(editor, "restart-waits") is not None, timeout=10_000)
        waits = shown_widget(editor, "restart-waits")
        assert "long-export" in words_of(waits), words_of(waits)
        assert QApplication.activeModalWidget() is None, "the restart's window blocks the editor"
        qtbot.wait(1500)
        assert editor.isVisible() and editors_started(starts) == [], "it restarted while the flow ran"
        if then == "Cancel":
            QTest.mouseClick(button(waits, "Cancel"), Qt.LeftButton)
            qtbot.waitUntil(lambda: shown_widget(editor, "restart-waits") is None, timeout=5_000)
    finally:
        (folder / "hold.go").write_text("go", encoding="utf-8")  # the flow ends
        ran_out = eventually(qtbot, lambda: not tab.running, timeout=60_000)
    assert ran_out, "the flow did not end"

    boxes.take("question")
    if then == "the flow ends":
        qtbot.waitUntil(lambda: bool(editors_started(starts)) and not editor.isVisible(), timeout=15_000)
        assert norm(editors_started(starts)[0].args[0]) == norm(folders.programs / NEW / "taskloomw.exe")
    else:
        qtbot.wait(4000)
        assert editor.isVisible() and editors_started(starts) == [], "it restarted after Cancel"


# --- U10: old versions --------------------------------------------------------------------------------------------------

@pytest.mark.invariant("UP-7")
def test_u10_a_start_removes_older_versions_taskloom_installed_and_keeps_the_rest(env, folders):
    """U10: in <LOCALAPPDATA>/Programs/Taskloom: this version (installed from 0.38.0), 0.38.0 (installed from 0.37.0),
    0.37.0 (installed), 0.36.0 (unpacked by you: no marker), 0.35.0 (installed; a flow in it is scheduled), 0.34.0
    (installed; you saved a flow in it), 99.0.0 (installed, newer), and a part of a download and of a removal left by
    a start that was cut off. A start of this version removes 0.37.0 and the two leftovers; it keeps the one it updated
    from, the one you unpacked, the newer one, and the two holding your flows, and says which and why."""
    p = folders.programs
    this = unpacked(p / __version__, __version__, marker_from=p / "0.38.0")
    unpacked(p / "0.38.0", "0.38.0", marker_from=p / "0.37.0")
    unpacked(p / "0.37.0", "0.37.0", marker_from=p / "0.36.0")
    unpacked(p / "0.36.0", "0.36.0")
    scheduled = unpacked(p / "0.35.0", "0.35.0", marker_from=p / "0.34.0",
                         extra={"examples/nightly.yaml": flow_yaml({"cron": "0 2 1 1 *"})})
    saved = unpacked(p / "0.34.0", "0.34.0", marker_from=p / "0.33.0")
    time.sleep(0.05)
    (saved / "examples" / "my-report.yaml").write_text(HELLO, encoding="utf-8")
    unpacked(p / "99.0.0", "99.0.0", marker_from=p / __version__)
    unpacked(p / ".partial-k3j9" / "taskloom", NEW)
    unpacked(p / ".removing-0.33.0", "0.33.0")
    Home(env.home).set_scheduled((scheduled / "examples" / "nightly.yaml").resolve(), True)

    done = Packaged(env, folders, this).run("run", env.write("hello.yaml", HELLO))

    assert done.returncode == 0, done.stdout + done.stderr
    assert leftovers(folders) == sorted(["0.34.0", "0.35.0", "0.36.0", "0.38.0", "99.0.0", __version__]), done.stderr
    assert (saved / "examples" / "my-report.yaml").read_text(encoding="utf-8") == HELLO
    lines = done.stderr.splitlines()
    assert [line for line in lines if "0.35.0" in line and "scheduled" in line], f"not said: {lines}"
    assert [line for line in lines if "0.34.0" in line], f"not said: {lines}"


@pytest.mark.skipif(not WINDOWS, reason="only Windows refuses to rename a folder while a file in it is open")
@pytest.mark.invariant("UP-7")
def test_u10_a_folder_in_use_is_kept_and_removed_at_a_later_start(env, folders):
    """U10: 0.37.0 (installed by Taskloom, older) has a file open (something still runs from it). A start of this
    version keeps it whole; once the file is closed, the next start removes it."""
    p = folders.programs
    this = unpacked(p / __version__, __version__, marker_from=p / "0.38.0")
    unpacked(p / "0.38.0", "0.38.0", marker_from=p / "0.37.0")
    old = unpacked(p / "0.37.0", "0.37.0", marker_from=p / "0.36.0")
    before = program_folder_contents(old)
    taskloom, flow = Packaged(env, folders, this), env.write("hello.yaml", HELLO)

    with open(old / "taskloom.exe", "rb"):  # in use
        first = taskloom.run("run", flow)
        kept = program_folder_contents(old) if old.exists() else None
        listed = leftovers(folders)
    again = taskloom.run("run", flow)

    assert first.returncode == 0 and again.returncode == 0, first.stderr + again.stderr
    assert kept == before and "0.37.0" in listed and not [n for n in listed if n.startswith(".removing")], listed
    assert leftovers(folders) == sorted(["0.38.0", __version__]), again.stderr


# --- U11: the docs -----------------------------------------------------------------------------------------------------

@pytest.mark.invariant("UP-4")
def test_u11_the_docs_say_taskbar_pins_are_not_changed():
    """U11: the guide (Help > Documentation) says that a Taskloom pinned to the taskbar is Windows' own copy, which an
    update does not change."""
    guide = (Path(__file__).resolve().parents[1] / "docs" / "guide.md").read_text(encoding="utf-8")
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", guide))
    said = [s for s in sentences if "taskbar" in s.lower() and "pin" in s.lower() and re.search(r"\bnot\b|n't", s)]
    assert said, "the guide does not say that taskbar pins are not changed"
    assert CHECK in guide, f"the guide does not tell of Help > {CHECK}"
