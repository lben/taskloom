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
- A full disk: shutil.disk_usage says 1 kB is free; or, while the zip downloads, Python's open (builtins.open and
  io.open) of a file in a .partial-* folder gives one whose writes fail once a third of the zip is written (OSError
  ENOSPC, as a real full disk). A server, proxy or firewall that never answers: a local socket that takes each
  connection and sends nothing. An hour without a quiet moment: the waiting service's
  scheduler.waiting says it began waiting 61 minutes ago. An older service: a real service whose scheduler.version is
  rewritten to an older version (or removed, as before 0.14.1).
- A Cancel pressed while the zip unpacks (that takes a moment only): zipfile's ZipFile.extract, once it has written a
  chosen file of the zip, waits until the test has pressed Cancel.

Windows-only (skipped elsewhere): test_u10_a_folder_in_use_is_kept_and_removed_at_a_later_start (only Windows refuses
to rename a folder holding an open file). Every other test runs on Windows, macOS and Linux; on Windows with real
.lnk files.
"""
# What updates on Windows must be called and show (chosen by these tests: the implementer's contract):
# - taskloom.update: RELEASE_URL (GitHub's /releases/latest API URL), ASSET = "taskloom-windows-amd64.zip", HOSTS (the
#   hosts a URL or redirect may name); read when a check runs, so tests can point them at the local server. One GET of
#   RELEASE_URL with the header Accept: application/vnd.github+json and a User-Agent starting "taskloom/"; tag_name
#   ("v<version>") and the asset named ASSET (browser_download_url, size, digest "sha256:<hex>"). Downloads through Qt's
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
#   "refused" and the URL or host (plain http, or a host not in HOSTS, also on a redirect); the error's own words
#   ("No space left on device", os.strerror(ENOSPC)) when a write of the download fails (the download is written
#   through Python's open); "timed out" when the check or the download gets no byte for taskloom.update.TIMEOUT_SECONDS
#   (seconds, read at each request; Charter C8). Cancel of the download shows no message.
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
# - `taskloom scheduler --at-login on|off` in the packaged Windows Taskloom (taskloom.cli's `frozen` and `sys`, as for
#   the start hook) writes or removes the Startup entry above, for its program folder and the Taskloom folder in use,
#   and prints "start at login enabled: <the .lnk>" / "start at login disabled: <the .lnk>"; with no APPDATA (not
#   Windows) it ends, exit code not 0, with the stderr line "error: starting the scheduler at login is supported on
#   Windows only". The start hook counts an entry whose program folder has no VERSION as older, and re-points an
#   entry whatever Taskloom folder it is for (its arguments kept). (The login tests of tests/test_scheduler.py,
#   test_paths_cli_config.py, test_paths_scheduler.py and test_scheduler_service_calls.py.)
# - The scheduler service (`taskloom scheduler`) finding the folder's lock held by an older Taskloom (scheduler.version
#   older, or missing) waits and takes over at a quiet moment (no run going, none waiting, no scheduled time due within
#   2 minutes; after an hour without one, the first check after a run ends): it stops the old service (it ends before
#   the new one holds the lock) and serves. While it waits it writes scheduler.waiting = "<version> <pid> <ISO local
#   time it began waiting>" and reads that time back at each check; a second newer service exits at once; turned off
#   meanwhile, it gives up (ends). Same or newer holding the lock: exits as today. The question "Restart the scheduler
#   service?" is gone, also on macOS and Linux; the editor's start of the service also starts it while an older one runs.
# - docs/guide.md: a sentence with "taskbar", "pin" and "not" (taskbar pins are Windows' own copies, not changed).
import builtins
import contextlib
import datetime as dt
import errno
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
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import zipfile
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
from tests.packaged_windows import (HELLO, MARKER, NO_KEYRING, WINDOWS, Folders, Packaged,  # noqa: E402
                                    PackagedSys, entry_for, json_links, login_args, norm, read_entry,
                                    unpacked, windows_folders, write_entry)
from tests.test_load_state import bar, load_state, names, start_again  # noqa: E402
from tests.test_paths_editor_running import eventually  # noqa: E402
from tests.test_paths_scheduler import stand_in_service  # noqa: E402
from tests.test_rerun_carries_on import full_run  # noqa: E402
from tests.test_scheduler_new_promises import (HOURLY, end_runs, ended, flow_yaml, history, hold_until,  # noqa: E402
                                               schedule, stop_the_service, wait_until)

# The release the local server offers: the minor version after this Taskloom's (__version__), so that it is newer
# whatever version this is (a fixed 0.40.0 stopped being newer when Taskloom became 0.40.0).
_MAJOR, _MINOR = __version__.split(".")[:2]
NEW = f"{_MAJOR}.{int(_MINOR) + 1}.0"
ASSET = "taskloom-windows-amd64.zip"
API_PATH = "/repos/lben/taskloom/releases/latest"
DOWNLOAD_PATH = f"/lben/taskloom/releases/download/v{NEW}/{ASSET}"
ASSET_PATH = "/release-assets/1"
CHECK = "Check for updates"
INSTALL = "Install?"
RESTART = f"Restart Taskloom now with version {NEW}?"
START_MENU_BOX = "Add Taskloom to the Start menu"
LOGIN_BOX = "Start the scheduler service when I log in"


# --- Windows' per-user folders, program folders, entries --------------------------------------------------------------

@pytest.fixture
def folders(tmp_path, monkeypatch) -> Folders:
    return windows_folders(tmp_path, monkeypatch)


def editor_args(home) -> str:
    return f'--home "{home}" editor'


def both_entries(folders: Folders, folder: Path, home) -> None:
    """The Start menu entry and the Startup entry both start Taskloom in `folder`, for the Taskloom folder `home`."""
    write_entry(folders.start_menu, folder / "taskloomw.exe", editor_args(home), folder)
    write_entry(folders.startup, folder / "taskloomw.exe", login_args(home), folder)


def entries_now(folders: Folders) -> dict:
    return {"Start menu": read_entry(folders.start_menu), "Startup": read_entry(folders.startup)}


@pytest.fixture(autouse=True)
def links(monkeypatch):
    json_links(monkeypatch)


# --- the release: a local HTTPS server standing for Taskloom's GitHub releases ----------------------------------------

def release_zip(version: str = NEW, *, leave_out: tuple = (), version_text: str | bytes | None = None,
                damaged: bool = False, extra: dict | None = None) -> bytes:
    """The Windows zip as packaging/build.py makes it: a top folder taskloom/ (with its folders' entries) holding the
    programs, VERSION, the libraries (a 1.5 MB file), examples. `damaged`: a byte of the libraries changed after the zip
    was made (its CRC no longer fits). `extra`: more entries {name as written in the zip: bytes}, after the others."""
    files = {
        "taskloom/taskloomw.exe": f"stand-in windowed program {version}".encode(),
        "taskloom/taskloom.exe": f"stand-in program {version}".encode(),
        "taskloom/VERSION": version_text if isinstance(version_text, bytes) else (version_text or version).encode(),
        "taskloom/README.md": b"Taskloom\n",
        "taskloom/_internal/base_library.zip": random.Random(1).randbytes(1_500_000),
        "taskloom/examples/hello.yaml": b"blocks: {}\n",
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for folder in ("taskloom/", "taskloom/_internal/", "taskloom/examples/"):
            z.writestr(zipfile.ZipInfo(folder), b"")
        for name, data in {**files, **(extra or {})}.items():
            if name not in leave_out:
                z.writestr(name, data)
    data = bytearray(out.getvalue())
    if damaged:
        info = zipfile.ZipFile(io.BytesIO(bytes(data))).getinfo("taskloom/_internal/base_library.zip")
        name_length, extra_length = int.from_bytes(data[info.header_offset + 26:info.header_offset + 28], "little"), \
            int.from_bytes(data[info.header_offset + 28:info.header_offset + 30], "little")
        data[info.header_offset + 30 + name_length + extra_length + 1000] ^= 0xFF
    return bytes(data)


def with_a_password(data: bytes, name: str = "taskloom/README.md") -> bytes:
    """The zip `data` with its entry `name` marked as encrypted, as in a zip made with a password: no tool reads it
    without one (Python's zipfile: RuntimeError)."""
    data = bytearray(data)
    data[zipfile.ZipFile(io.BytesIO(bytes(data))).getinfo(name).header_offset + 6] |= 0x1  # (its local header's flags)
    at = data.find(b"PK\x01\x02")
    while data[at + 46:at + 46 + len(name)] != name.encode():  # (its central directory entry)
        at = data.find(b"PK\x01\x02", at + 1)
    data[at + 8] |= 0x1
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
        self.digest = self.size = None  # (None: the zip's own)
        self.asset_name = ASSET  # the Windows zip's name in the release
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
                    {"name": self.asset_name, "size": self.size or len(self.zip), "content_type": "application/zip",
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

class Boxes:
    """The message boxes the editor shows (QMessageBox.question / information / warning / critical), written down as
    (kind, "title\\ntext"); a question is answered by the first of `answers` ((words, button)) whose words it has, else
    No. A window that waits for an answer some other way (modal) is closed and written down as ("modal", its words)."""

    def __init__(self):
        self.seen, self.answers, self.over = [], [], []  # over: the window each box was shown over (its parent)

    def box(self, kind):
        def shown(parent, title, text, *args, **kwargs):
            said = f"{title}\n{text}"
            self.seen.append((kind, said))
            self.over.append(parent)
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

    def __init__(self, args, env, how: dict):
        self.args, self.env, self.pid, self.returncode = [str(a) for a in args], env, 999_999, None
        self.how = how  # Popen's other arguments
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
            started.append(Started(args, kwargs.get("env"), kwargs))
            return started[-1]
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    return started


def editors_started(starts) -> list[Started]:
    return [s for s in starts if s.args[1:] and s.args[-1] == "editor"]


def services_started(starts) -> list[Started]:
    return [s for s in starts if "scheduler" in s.args]


def started_on_its_own(started: Started) -> bool:
    """Whether a program was started on its own: its input and output not this editor's (nothing to read or write
    there), on Windows detached from any console and with no console window of its own."""
    how = started.how
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW if WINDOWS else 0  # (only Windows has them)
    return (all(how.get(name) == subprocess.DEVNULL for name in ("stdin", "stdout", "stderr"))
            and how.get("creationflags", 0) == flags)


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


def at_the_right(cancel: QWidget, window: QWidget) -> bool:
    """Whether a button is at the right of its window, below its text (not stretched across it)."""
    texts = [label.geometry() for label in window.findChildren(QLabel) if label.isVisible()]
    return (cancel.width() < window.width() // 2 and cancel.geometry().right() > window.width() * 3 // 4
            and all(cancel.geometry().top() > text.bottom() for text in texts))


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
    assert [said for kind, said in boxes.seen if kind == "question"] == [
        f"{CHECK}\nTaskloom {NEW} is out; this is {__version__}. {INSTALL}", f"Taskloom updated\n{RESTART}"]
    assert all(window is editor for window in boxes.over), "a question not shown over the editor window"


# --- U1: Check for updates, Install, the download --------------------------------------------------------------------

@pytest.mark.invariant("UP-1")
@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-3")
@pytest.mark.invariant("UP-5")
@pytest.mark.invariant("UP-6")
def test_u1_u9_check_for_updates_installs_the_newer_release_while_the_editor_stays_usable(
        editor, qtbot, tmp_path, folders, this, releases, boxes, starts):
    """U1, U9: Taskloom 0.39.x unpacked by hand; its Start menu and Startup entries start it. Help > Check for updates:
    the newest release, NEW, is newer: "Install?" with its version. Yes: the zip downloads with a progress bar in a
    window that does not block the editor (a new flow can be made meanwhile), then unpacks into
    <LOCALAPPDATA>/Programs/Taskloom/NEW: exactly the zip's files, plus the marker naming the folder it updated from;
    nothing else is left there. Both entries now start NEW, their arguments (the Taskloom folder) kept. The Taskloom
    folder (settings, history, credentials, schedules) and the flow are byte-equal before and after. Then "Restart
    Taskloom now with version NEW?"; No keeps this editor open and starts nothing (U8)."""
    home = editor.home.root
    flow = a_taskloom_folder(home, tmp_path / "flows")
    flow_bytes, flow_time = flow.read_bytes(), flow.stat().st_mtime_ns
    both_entries(folders, this, home)
    editor.scheduler_running()  # the editor looks whether a service runs as it opens and every 2 s, so before anyone
    #                             can click Check for updates (the look makes the folder's empty lock files)
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
    told = editor.statusBar().currentMessage()

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
    (download,) = [r for r in releases.requests if r["path"] == DOWNLOAD_PATH]
    headers = {k.lower(): v for k, v in download["headers"].items()}
    assert headers.get("accept") == "application/octet-stream", headers  # (GitHub's asset URLs: the file, not its info)
    assert told == f"Taskloom {NEW} is installed in {installed}.", f"the status bar says {told!r}"
    assert shown_widget(editor, "update-progress") is None, "the download window stays open"
    qtbot.wait(1500)
    assert editor.isVisible() and editors_started(starts) == [], "No to the restart did not keep this editor"


@pytest.mark.invariant("UP-1")
@pytest.mark.invariant("UP-3")
def test_u1_cancel_during_the_download_leaves_nothing_behind(editor, qtbot, folders, this, releases, boxes, starts):
    assert True  # an edit to this test's body
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
    assert progress.windowTitle() == f"Taskloom {NEW}", progress.windowTitle()
    assert [label.text() for label in progress.findChildren(QLabel)] == [f"Downloading Taskloom {NEW}…"]
    assert at_the_right(button(progress, "Cancel"), progress), "Cancel is not at the right of the download window"
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


@pytest.mark.parametrize("after", ["nothing yet", "the first file", "the last file"])
@pytest.mark.invariant("UP-1")
@pytest.mark.invariant("UP-3")
def test_u1_cancel_while_unpacking_leaves_nothing_behind(editor, qtbot, monkeypatch, folders, this, releases, boxes,
                                                         starts, after):
    """U1: Install? Yes; the zip is downloaded and is being unpacked (nothing is written yet, or the first of its files,
    or the last, is written in the part of the download): the download window says so and its bar shows the bytes
    unpacked so far of all (empty before the first), then Cancel is pressed in it: nothing is left in
    <LOCALAPPDATA>/Programs/Taskloom (no version folder, no part of the download), the entries still start this
    version, no message (you cancelled), no restart offered."""
    both_entries(folders, this, editor.home.root)
    entries = entries_now(folders)
    members = zipfile.ZipFile(io.BytesIO(releases.zip)).infolist()
    files = [m.filename for m in members if not m.is_dir()]
    chosen = {"nothing yet": None, "the first file": files[0], "the last file": files[-1]}[after]
    before = [m.file_size for m in members][:[m.filename for m in members].index(chosen)] if chosen else []
    reached, go, extract = threading.Event(), threading.Event(), zipfile.ZipFile.extract

    def extracting(self, member, *args, **kwargs):
        if chosen is None:  # (waits before the first is written)
            reached.set()
            go.wait(60)
        done = extract(self, member, *args, **kwargs)
        if getattr(member, "filename", member) == chosen:
            reached.set()
            go.wait(60)
        return done

    monkeypatch.setattr(zipfile.ZipFile, "extract", extracting)
    boxes.answers = [(INSTALL, QMessageBox.Yes)]

    check_for_updates(editor)
    try:
        qtbot.waitUntil(reached.is_set, timeout=60_000)
        progress = shown_widget(editor, "update-progress")
        assert progress is not None, "the download window closed while the zip was unpacked"
        assert [label.text() for label in progress.findChildren(QLabel)] == [f"Checking and unpacking Taskloom {NEW}…"]
        (progress_bar,) = progress.findChildren(QProgressBar)
        if chosen is None:  # (an empty bar: Qt shows a value at or below its minimum as nothing done)
            qtbot.waitUntil(lambda: progress_bar.maximum() != len(releases.zip), timeout=5_000)  # (not the download's)
            assert progress_bar.value() <= progress_bar.minimum(), (progress_bar.value(), progress_bar.minimum())
        else:
            partly = [p for p in folders.programs.iterdir() if p.name.startswith(".partial-")]
            assert [p for p in partly if (p / chosen).is_file()], f"{chosen} is not unpacked yet: {leftovers(folders)}"
            shows = (sum(before), sum(m.file_size for m in members))  # (the files before the one being written)
            eventually(qtbot, lambda: (progress_bar.value(), progress_bar.maximum()) == shows, timeout=5_000)
            assert (progress_bar.value(), progress_bar.maximum()) == shows, (progress_bar.value(), shows)
        QTest.mouseClick(button(progress, "Cancel"), Qt.LeftButton)
    finally:
        go.set()
    eventually(qtbot, lambda: leftovers(folders) == [], timeout=10_000)
    qtbot.wait(1500)

    asked = boxes.take("question")
    assert len(asked) == 1 and INSTALL in asked[0], f"asked after Cancel: {asked}"
    assert boxes.take() == [], "a message after Cancel"
    assert shown_widget(editor, "update-progress") is None, "the download window stays open"
    assert leftovers(folders) == [], f"left in {folders.programs}: {leftovers(folders)}"
    assert entries_now(folders) == entries
    assert starts == []


def files_in(folder: Path) -> dict:
    """{relative path: bytes} of every file in `folder`."""
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("how", ["installed by Taskloom", "unpacked by hand"])
@pytest.mark.invariant("UP-1")
@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-7")
def test_u1_a_version_already_there_is_used_as_it_is_and_nothing_is_downloaded(editor, qtbot, folders, this,
                                                                               releases, boxes, starts, how):
    """U1: <LOCALAPPDATA>/Programs/Taskloom/NEW is there already, whole (installed by Taskloom before, or unpacked
    there by hand: no marker). Check for updates; Install? Yes: nothing is downloaded; that folder is used as it is (not
    a byte of it changed; one unpacked by hand gets no marker, so it is never removed as Taskloom's); the entries now
    start it; the restart is offered; nothing else is left in <LOCALAPPDATA>/Programs/Taskloom."""
    home = editor.home.root
    both_entries(folders, this, home)
    there = unpacked(folders.programs / NEW, NEW, marker_from=this if how == "installed by Taskloom" else None)
    before = files_in(there)

    install(editor, qtbot, boxes)

    asked = boxes.take("question")
    assert len(asked) == 2 and INSTALL in asked[0] and RESTART in asked[1], asked
    assert releases.downloads() == [], f"downloaded: {releases.downloads()}"
    assert files_in(there) == before, f"{there} was changed"
    assert leftovers(folders) == [NEW], f"left in {folders.programs}: {leftovers(folders)}"
    assert entries_now(folders) == {"Start menu": entry_for(there, editor_args(home)),
                                    "Startup": entry_for(there, login_args(home))}
    assert starts == []


@pytest.mark.invariant("UP-1")
@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-7")
def test_u1_an_update_of_a_version_taskloom_installed_whose_entries_cannot_change_yet_says_so(
        editor, qtbot, monkeypatch, folders, this, releases, boxes, starts):
    """U1: this Taskloom is one Taskloom installed (<LOCALAPPDATA>/Programs/Taskloom/<this version>), its entries start
    it, and they cannot be changed just now (where Taskloom locks them, <LOCALAPPDATA>/Taskloom, is a file). Install?
    Yes: NEW is installed beside it, its marker naming this one's folder, which is left as it is (a later start
    removes it, U10); the entries are left as they are (NEW's first start points them at itself); the status bar
    says where NEW is installed and why the entries could not be changed; the restart is offered."""
    from taskloom.editor import window as window_module

    home = editor.home.root
    installed = unpacked(folders.programs / __version__, __version__, marker_from=this)
    monkeypatch.setattr(window_module, "sys", PackagedSys(installed / "taskloomw.exe"))
    both_entries(folders, installed, home)
    entries, before = entries_now(folders), files_in(installed)
    locks = folders.local / "Taskloom"
    locks.write_text("not a folder\n", encoding="utf-8")

    install(editor, qtbot, boxes)
    told = editor.statusBar().currentMessage()

    boxes.take("question")
    new = folders.programs / NEW
    assert program_folder_contents(new) == zip_files(releases.zip), "the version's folder is not the zip's"
    assert norm(json.loads((new / MARKER).read_text(encoding="utf-8"))["from"]) == norm(installed)
    assert leftovers(folders) == sorted([__version__, NEW]), f"left in {folders.programs}: {leftovers(folders)}"
    assert files_in(installed) == before and entries_now(folders) == entries
    assert told.startswith(f"Taskloom {NEW} is installed in {new}.  Could not put it in use yet: "), told
    assert str(locks) in told and repr(str(locks)) not in told, f"{locks} is not shown as it is: {told}"
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

    (said,) = boxes.take("information")
    assert said == f"{CHECK}\nYou have the newest version, Taskloom {__version__}.", said
    assert boxes.over == [editor], "the message is not shown over the editor window"
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

NOT_UPDATED = "Taskloom was not updated\n"  # (the title of a message of a failed download or install)
ZIP_SIZE = len(release_zip())
FAILURES = {  # what changes: the start of the message (its title, a new line, its text), more words it has
    "no connection": ({"no connection": True}, f"{CHECK}\nCould not reach Taskloom's releases: ", []),
    "release info 404": ({"api_status": 404}, f"{CHECK}\nTaskloom's releases answered HTTP 404 for https://127.0.0.1:",
                         []),
    "release info 400": ({"api_status": 400}, f"{CHECK}\nTaskloom's releases answered HTTP 400 for https://127.0.0.1:",
                         []),
    "a tag without a version": ({"tag": "latest"},
                                f"{CHECK}\nTaskloom's releases gave an answer Taskloom cannot read: no version in "
                                f"'latest'", []),
    "no Windows zip in the release": ({"asset_name": "taskloom-windows-arm64.zip"},
                                      f"{CHECK}\nTaskloom {NEW} has no {ASSET} to download", []),
    "download 500": ({"download_status": 500},
                     f"{NOT_UPDATED}Taskloom's releases answered HTTP 500 for https://127.0.0.1:", []),
    "download cut short": ({"cut_short": True},
                           f"{NOT_UPDATED}The download is incomplete ({ZIP_SIZE // 2} of {ZIP_SIZE} bytes): ", []),
    "download shorter than the release says": ({"size": ZIP_SIZE + 1}, f"{NOT_UPDATED}The download is incomplete: "
                                               f"{ZIP_SIZE} of {ZIP_SIZE + 1} bytes", []),
    "checksum differs": ({"digest": "sha256:" + "0" * 64}, f"{NOT_UPDATED}The download's checksum is not the "
                         "release's: it is not the zip Taskloom published", []),
    "not a zip": ({"zip": b"<html>Not a zip</html>"}, f"{NOT_UPDATED}The download is damaged: File is not a zip file",
                  []),
    "broken zip": ({"zip": release_zip(damaged=True)}, f"{NOT_UPDATED}The download is damaged: "
                   "taskloom/_internal/base_library.zip does not read back as it was packed", []),
    "a zip that needs a password": ({"zip": with_a_password(release_zip())},
                                    f"{NOT_UPDATED}Taskloom {NEW} could not be installed", []),
    "no taskloomw.exe": ({"zip": release_zip(leave_out=("taskloom/taskloomw.exe",))},
                         f"{NOT_UPDATED}The download has no taskloom/taskloomw.exe", []),
    "VERSION not the release's": ({"zip": release_zip(version_text="0.39.9")},
                                  f"{NOT_UPDATED}The download's VERSION says 0.39.9, not the release's {NEW}", []),
    "VERSION not text": ({"zip": release_zip(version_text=b"\xff" + NEW.encode())},
                         f"{NOT_UPDATED}The download's VERSION says \ufffd{NEW}, not the release's {NEW}", []),
    "full disk": ({"free": 2**20 + 5},
                  f"{NOT_UPDATED}Not enough disk space to unpack Taskloom {NEW}: it needs 2 MB, 1 MB are free", []),
    "plain http": ({"scheme": "http"}, f"{CHECK}\nDownload refused: http://127.0.0.1:",
                   ["is not Taskloom's GitHub releases over https"]),
    "redirect to another host": ({"redirect": "another host"}, f"{NOT_UPDATED}Download refused: https://localhost:",
                                 []),
}


@pytest.mark.parametrize("failure", list(FAILURES))
@pytest.mark.invariant("UP-3")
def test_u3_a_failed_update_says_what_went_wrong_and_changes_nothing(editor, qtbot, monkeypatch, folders, this,
                                                                     releases, boxes, starts, failure):
    """U3: no connection, the release info or the download answered with an HTTP error, a release whose tag holds no
    version or that has no Windows zip, a download cut short or shorter than the release says, a zip whose checksum
    is not the release's, a download that is not a zip, a damaged zip, one that needs a password, a zip without
    taskloomw.exe or with another VERSION (or one that is not text), a full disk (1 MB and a little free, 2 MB
    needed), a download URL that is not https, a redirect to a host that is not GitHub's: a message over the editor
    window says what went wrong, word for word; nothing is installed or left behind; the entries still start this
    version; the editor goes on working."""
    change, starts_with, says = FAILURES[failure]
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
    assert len(told) == 1 and told[0].startswith(starts_with), f"the message does not start {starts_with!r}: {told}"
    for words in says:
        assert words in told[0], f"the message does not say {words!r}: {told[0]!r}"
    assert all(window is editor for window in boxes.over), "a message not shown over the editor window"
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


class FullDisk:
    """A disk with `free` bytes left for what is written in <LOCALAPPDATA>/Programs/Taskloom/.partial-*: a file opened
    there by Python's open (builtins.open, io.open: Path.open too) takes writes until they are used up; that write and
    every later one fail as on a full disk (OSError ENOSPC). Every other file opens as usual. `refused`: the writes
    that failed."""

    def __init__(self, folders: Folders, free: int):
        self.inside, self.free, self.refused, self.real = norm(folders.programs) + os.sep, free, [], io.open

    def open(self, file, mode="r", *args, **kwargs):
        opened = self.real(file, mode, *args, **kwargs)
        where = norm(file) if isinstance(file, (str, os.PathLike)) else ""
        if (where.startswith(self.inside) and where[len(self.inside):].startswith(".partial-")
                and set(mode) & set("wax+")):
            return _Filling(opened, self)
        return opened


class _Filling:
    """A file on a FullDisk."""

    def __init__(self, file, disk: FullDisk):
        self._file, self._disk = file, disk

    def write(self, data):
        if len(data) > self._disk.free:
            self._disk.free = 0
            self._disk.refused.append(len(data))
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        self._disk.free -= len(data)
        return self._file.write(data)

    def __getattr__(self, name):
        return getattr(self._file, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._file.close()
        return False


@pytest.mark.invariant("UP-3")
def test_u3_a_disk_that_fills_up_during_the_download_says_so_and_leaves_nothing_behind(
        editor, qtbot, monkeypatch, tmp_path, folders, this, releases, boxes, starts):
    """U3: Install? Yes; the disk fills up while the zip downloads (a third of it is written, then each write fails: no
    space left on device): one message says so; the download window closes; nothing is left in
    <LOCALAPPDATA>/Programs/Taskloom (no part of the download); the entries still start this version; the Taskloom
    folder is byte-equal to before; the editor goes on working."""
    home = editor.home.root
    a_taskloom_folder(home, tmp_path / "flows")
    both_entries(folders, this, home)
    entries = entries_now(folders)
    editor.scheduler_running()  # (as in U1: the look makes the folder's empty lock files before the test looks at it)
    before = taskloom_folder(home)
    disk = FullDisk(folders, free=len(releases.zip) // 3)
    monkeypatch.setattr(builtins, "open", disk.open)
    monkeypatch.setattr(io, "open", disk.open)
    boxes.answers = [(INSTALL, QMessageBox.Yes)]
    tabs = len(editor.tabs_list())

    check_for_updates(editor)
    qtbot.waitUntil(lambda: any(kind == "warning" or RESTART in said for kind, said in boxes.seen), timeout=60_000)
    qtbot.wait(1000)

    assert disk.refused, "no write of the download failed: it was not written through Python's open (the contract)"
    told = boxes.take("warning")
    asked = boxes.take("question")
    assert len(asked) == 1 and INSTALL in asked[0], asked
    assert len(told) == 1, told
    assert os.strerror(errno.ENOSPC) in told[0], f"the message does not say the disk is full: {told[0]!r}"
    assert boxes.take() == []
    assert shown_widget(editor, "update-progress") is None, "the download window stays open"
    assert leftovers(folders) == [], f"left in {folders.programs}: {leftovers(folders)}"
    assert entries_now(folders) == entries
    assert taskloom_folder(home) == before, "a failed update changed the Taskloom folder"
    assert starts == []
    editor.new_flow()
    assert len(editor.tabs_list()) == tabs + 1


LIMIT, MARGIN = 2, 3  # seconds: taskloom.update.TIMEOUT_SECONDS in the time-limit tests; what more a message may take


class Silent:
    """A server on 127.0.0.1 (or a proxy or firewall on the way) that takes each connection and never answers."""

    def __init__(self):
        self.socket = socket.create_server(("127.0.0.1", 0))
        self.socket.settimeout(0.2)
        self.port, self.held, self.stopped = self.socket.getsockname()[1], [], threading.Event()
        threading.Thread(target=self._take, daemon=True).start()

    def _take(self):
        while not self.stopped.is_set():
            with contextlib.suppress(TimeoutError, OSError):
                self.held.append(self.socket.accept()[0])

    def stop(self):
        self.stopped.set()
        for connection in [self.socket, *self.held]:
            connection.close()


@pytest.fixture
def silent():
    server = Silent()
    yield server
    server.stop()


@pytest.fixture
def calls_dropped(editor):
    """As the test ends (before the editor, its message boxes and the servers go), a web call of the editor still
    going is dropped unanswered: a test whose call never ended leaves nothing to answer it in a later test."""
    yield
    from PySide6.QtNetwork import QNetworkReply
    for reply in editor.findChildren(QNetworkReply):
        reply.blockSignals(True)
        reply.abort()


def short_time_limit(monkeypatch):
    """Check for updates' web calls give up after LIMIT seconds without a byte (the contract's TIMEOUT_SECONDS)."""
    from taskloom import update
    monkeypatch.setattr(update, "TIMEOUT_SECONDS", LIMIT, raising=False)


def message_within(qtbot, boxes, seconds: float) -> bool:
    """Whether a message box is shown within `seconds` (the editor's event loop runs meanwhile)."""
    end = time.monotonic() + seconds
    while not boxes.seen and time.monotonic() < end:
        qtbot.wait(50)
    return bool(boxes.seen)


@pytest.mark.invariant("UP-3")
@pytest.mark.charter("C8")
def test_u3_a_check_that_gets_no_answer_says_so_within_the_time_limit(editor, qtbot, monkeypatch, folders, releases,
                                                                     boxes, silent, calls_dropped):
    """U3, C8: the releases' server (or a proxy or firewall on the way) takes the connection and never answers: within
    the time limit (taskloom.update.TIMEOUT_SECONDS, 2 s here) one message says the check timed out; nothing is
    downloaded or installed; the editor goes on working."""
    short_time_limit(monkeypatch)
    point_updates_at(monkeypatch, f"https://127.0.0.1:{silent.port}{API_PATH}")
    tabs = len(editor.tabs_list())

    check_for_updates(editor)
    told_in_time = message_within(qtbot, boxes, LIMIT + MARGIN)
    qtbot.wait(1000)

    assert silent.held, "the check never reached the silent server"
    assert told_in_time, f"no message {LIMIT + MARGIN} s after a check the server never answers (time limit {LIMIT} s)"
    told = boxes.take("warning")
    assert told == [f"{CHECK}\nTaskloom's releases timed out: nothing came from https://127.0.0.1:{silent.port}"
                    f"{API_PATH} for {LIMIT} seconds"], told
    assert boxes.take() == []
    assert releases.requests == [] and leftovers(folders) == []
    editor.new_flow()
    assert len(editor.tabs_list()) == tabs + 1


@pytest.mark.invariant("UP-3")
@pytest.mark.charter("C8")
def test_u3_a_download_that_stalls_ends_within_the_time_limit_and_leaves_nothing_behind(
        editor, qtbot, monkeypatch, folders, this, releases, boxes, starts, calls_dropped):
    """U3, C8: Install? Yes; the zip's download stops halfway, the connection open, nothing more coming: within the time
    limit (2 s here) of its last byte, one message says the download timed out; the download window closes; nothing is
    left in <LOCALAPPDATA>/Programs/Taskloom; the entries still start this version; the editor goes on working."""
    short_time_limit(monkeypatch)
    both_entries(folders, this, editor.home.root)
    entries = entries_now(folders)
    releases.hold = threading.Event()  # (set only as the test ends)
    boxes.answers = [(INSTALL, QMessageBox.Yes)]
    tabs = len(editor.tabs_list())

    check_for_updates(editor)
    qtbot.waitUntil(lambda: shown_widget(editor, "update-progress") is not None, timeout=30_000)
    (progress_bar,) = shown_widget(editor, "update-progress").findChildren(QProgressBar)
    qtbot.waitUntil(lambda: progress_bar.value() >= len(releases.zip) // 2, timeout=30_000)
    asked = boxes.take("question")
    told_in_time = message_within(qtbot, boxes, LIMIT + MARGIN)
    qtbot.wait(1000)

    assert len(asked) == 1 and INSTALL in asked[0], asked
    assert told_in_time, f"no message {LIMIT + MARGIN} s after the download stalled (time limit {LIMIT} s)"
    told = boxes.take("warning")
    assert told == [f"{NOT_UPDATED}Taskloom's releases timed out: nothing came from https://127.0.0.1:{releases.port}"
                    f"{ASSET_PATH} for {LIMIT} seconds"], told
    assert boxes.take() == []
    assert shown_widget(editor, "update-progress") is None, "the download window stays open"
    assert leftovers(folders) == [], f"left in {folders.programs}: {leftovers(folders)}"
    assert entries_now(folders) == entries
    assert starts == []
    editor.new_flow()
    assert len(editor.tabs_list()) == tabs + 1


ESCAPING = {  # an entry of the zip whose name leaves its folder (None: the absolute path of a file outside, from the test)
    "up out of its folder": "../../../escaped.txt",
    "up out from inside its taskloom folder": "taskloom/../../../../escaped.txt",
    "up out with backslashes": "taskloom/..\\..\\..\\..\\escaped.txt",
    "an absolute path": None,
    "a drive letter": "C:/escaped.txt",
    "a drive letter inside its taskloom folder": "taskloom/C:escaped.txt",
}


@pytest.mark.parametrize("entry", list(ESCAPING))
@pytest.mark.invariant("UP-3")
def test_u3_a_zip_with_an_entry_that_leaves_its_folder_is_refused_and_nothing_is_written(
        editor, qtbot, tmp_path, folders, this, releases, boxes, starts, entry):
    """U3: the release's zip (its size and checksum the release's own) holds, after its files, an entry whose name
    leaves its folder: up out of it (../), from inside its taskloom folder, with Windows' backslashes, an absolute path
    (on Windows with its drive letter) or a drive letter (at its start, or inside its taskloom folder). Install? Yes:
    one message says what went wrong, naming that entry; no file of it is written anywhere; nothing is installed or
    left in <LOCALAPPDATA>/Programs/Taskloom; the entries still start this version; the editor goes on working."""
    name = ESCAPING[entry] or (tmp_path / "outside" / "escaped.txt").as_posix()
    releases.zip = release_zip(extra={name: b"not a file of Taskloom\n"})
    both_entries(folders, this, editor.home.root)
    entries = entries_now(folders)
    boxes.answers = [(INSTALL, QMessageBox.Yes)]
    tabs = len(editor.tabs_list())

    check_for_updates(editor)
    qtbot.waitUntil(lambda: any(kind == "warning" or RESTART in said for kind, said in boxes.seen), timeout=60_000)
    qtbot.wait(1000)

    told = boxes.take("warning")
    asked = boxes.take("question")
    assert len(asked) == 1 and INSTALL in asked[0], asked
    assert len(told) == 1 and "escaped.txt" in told[0], f"the message does not name the entry {name!r}: {told}"
    assert boxes.take() == []
    assert sorted(tmp_path.rglob("escaped.txt")) == [], "the entry was written"
    assert leftovers(folders) == [], f"left in {folders.programs}: {leftovers(folders)}"
    assert entries_now(folders) == entries
    assert starts == []
    editor.new_flow()
    assert len(editor.tabs_list()) == tabs + 1 and shown_widget(editor, "update-progress") is None


@pytest.mark.parametrize("there", ["another version's folder", "a folder without taskloomw.exe",
                                   "one made while the zip downloads"])
@pytest.mark.invariant("UP-3")
@pytest.mark.invariant("UP-7")
def test_u3_a_folder_in_the_new_versions_place_is_left_as_it_is_and_the_update_says_so(
        editor, qtbot, folders, this, releases, boxes, starts, there):
    """U3: where NEW goes (<LOCALAPPDATA>/Programs/Taskloom/NEW) there is a folder that is not that version whole:
    another version's (its VERSION says 0.39.9), or NEW without its taskloomw.exe; or, while the zip downloads,
    NEW's folder is made there whole (as by another install of it). Install? Yes: one message says what went wrong
    (before downloading, it names the folder); that folder is left byte-equal, nothing else is left in
    <LOCALAPPDATA>/Programs/Taskloom; the entries still start this version; nothing is started; the editor goes on
    working."""
    place = folders.programs / NEW
    both_entries(folders, this, editor.home.root)
    entries = entries_now(folders)
    if there == "another version's folder":
        unpacked(place, "0.39.9")
    elif there == "a folder without taskloomw.exe":
        (unpacked(place, NEW) / "taskloomw.exe").unlink()
    else:
        releases.hold = threading.Event()
    before = files_in(place) if place.exists() else None
    boxes.answers = [(INSTALL, QMessageBox.Yes)]
    tabs = len(editor.tabs_list())

    check_for_updates(editor)
    if there == "one made while the zip downloads":
        qtbot.waitUntil(lambda: shown_widget(editor, "update-progress") is not None, timeout=30_000)
        (progress_bar,) = shown_widget(editor, "update-progress").findChildren(QProgressBar)
        qtbot.waitUntil(lambda: progress_bar.value() > 0, timeout=30_000)
        before = files_in(unpacked(place, NEW, marker_from=this))
        releases.hold.set()
    qtbot.waitUntil(lambda: any(kind == "warning" or RESTART in said for kind, said in boxes.seen), timeout=60_000)
    qtbot.wait(1000)

    told = boxes.take("warning")
    asked = boxes.take("question")
    assert len(asked) == 1 and INSTALL in asked[0], asked
    assert len(told) == 1 and told[0].startswith(NOT_UPDATED), told
    assert all(window is editor for window in boxes.over), "a message not shown over the editor window"
    if there != "one made while the zip downloads":
        assert told[0] == f"{NOT_UPDATED}A folder is in the way: {place}", told[0]
        assert releases.downloads() == [], f"downloaded: {releases.downloads()}"
    else:
        assert told[0].startswith(f"{NOT_UPDATED}Could not unpack Taskloom {NEW}: "), told[0]
        assert str(place) in told[0] and repr(str(place)) not in told[0], f"{place} is not shown as it is: {told[0]}"
    assert boxes.take() == []
    assert files_in(place) == before, f"{place} was changed"
    assert leftovers(folders) == [NEW], f"left in {folders.programs}: {leftovers(folders)}"
    assert entries_now(folders) == entries
    assert starts == []
    editor.new_flow()
    assert len(editor.tabs_list()) == tabs + 1 and shown_widget(editor, "update-progress") is None


# --- U4, U5, U6: the entries follow the newest version -----------------------------------------------------------------

@pytest.mark.parametrize("before", ["this version", "a folder that is gone", "none", "only the Startup entry",
                                    "a newer version"])
@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-4")
def test_u4_an_install_points_the_entries_at_the_new_version_straight_away(editor, qtbot, tmp_path, folders, this,
                                                                           releases, boxes, before):
    """U4: right after an install of NEW, each entry there is starts NEW (its arguments kept): one that started
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


def gone_said(lines) -> list[str]:
    return [line for line in lines if "gone" in line and __version__ in line]


GONE = re.compile(r"The (Start menu|Startup) entry started Taskloom from (.+), a folder that is gone: it now starts "
                  r"Taskloom (\S+) in (.+)")


def gone_told(lines) -> dict:
    """{entry: (the folder that was gone, the version it starts now, its folder)} of each line that tells of one."""
    return {m[1]: (norm(m[2]), m[3], norm(m[4])) for m in map(GONE.fullmatch, lines) if m}


@pytest.mark.parametrize("start", ["editor", "taskloom scheduler", "taskloom run"])
@pytest.mark.parametrize("before", ["an older version", "a folder that is gone"])
@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-4")
def test_u4_the_first_start_of_a_newer_version_unpacked_by_hand_points_the_entries_at_itself(env, folders, before,
                                                                                            start):
    """U4: both entries start 0.30.0 (still there), or folders that are gone (each its own). This version, unpacked by
    hand into <LOCALAPPDATA>/Programs/Taskloom, starts for the first time: its editor (from taskloomw.exe), `taskloom
    scheduler` (as at login; the service is off, so it ends at once) or `taskloom run`. Both entries now start this
    version, their arguments kept. Each entry that started a folder that is gone is said, on stderr or in the editor's
    status bar: which entry, that folder, and this version and its folder."""
    home = env.home
    this = unpacked(folders.programs / __version__, __version__)
    old = (unpacked(folders.programs / "0.30.0", "0.30.0") if before == "an older version"
           else env.root / "deleted" / "taskloom")
    both_entries(folders, old, home)
    if before == "a folder that is gone":
        old_login = env.root / "deleted" / "taskloom 2"
        write_entry(folders.startup, old_login / "taskloomw.exe", login_args(home), old_login)
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
        said = [line for shown in taskloom.said_in_status_bar() for line in shown.split("  ")]  # (lines 2 spaces apart)
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
        assert gone_told(said) == {"Start menu": (norm(old), __version__, norm(this)),
                                   "Startup": (norm(old_login), __version__, norm(this))}, said


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


@pytest.mark.parametrize("seen", [{"TEST_FROZEN": "no", "TEST_PROGRAM": sys.executable}, {"TEST_PLATFORM": "linux"}],
                         ids=["from source on Windows", "packaged, not on Windows"])
@pytest.mark.invariant("UP-2")
def test_u4_only_the_packaged_windows_taskloom_points_the_entries_at_itself(env, folders, seen):
    """U4: both entries start a folder that is gone. Taskloom run from source on Windows (its program is Python), or a
    packaged Taskloom on another platform, starts: the entries are left as they are and nothing is said of them."""
    home = env.home
    both_entries(folders, env.root / "deleted" / "taskloom", home)
    before = entries_now(folders)
    taskloom = Packaged(env, folders, unpacked(folders.programs / __version__, __version__))

    done = taskloom.run("run", env.write("hello.yaml", HELLO), environ=seen)

    assert done.returncode == 0, done.stdout + done.stderr
    assert entries_now(folders) == before and taskloom.written() == [], (entries_now(folders), done.stderr)
    assert not [line for line in done.stderr.splitlines() if "gone" in line or "Could not" in line], done.stderr


@pytest.mark.invariant("UP-2")
def test_u4_todays_startup_script_is_replaced_by_a_shortcut_for_its_taskloom_folder(env, folders):
    """U4: the Startup folder has today's "Taskloom Scheduler.cmd" (Taskloom 0.30.0, for a Taskloom folder of its
    own, named with a % and an é: the script writes the % as %%, as cmd.exe needs, in UTF-8). The first start of this
    version replaces it by the Startup entry Taskloom Scheduler.lnk: this version's program, for that same Taskloom
    folder; the .cmd is gone (no console window flashes at login any more)."""
    old = unpacked(folders.programs / "0.30.0", "0.30.0")
    other_home = env.root / "other home 100% José"
    script = folders.startup.with_suffix(".cmd")
    script.parent.mkdir(parents=True)
    in_script = str(other_home).replace("%", "%%")
    script.write_text(f'@echo off\nchcp 65001 >nul\nrem Taskloom 0.30.0\nset "TASKLOOM_HOME={in_script}"\n'
                      f'start "" "{old / "taskloomw.exe"}" scheduler --unless-paused\n', encoding="utf-8",
                      newline="\r\n")
    this = unpacked(folders.programs / __version__, __version__)

    done = Packaged(env, folders, this).run("run", env.write("hello.yaml", HELLO))

    assert done.returncode == 0, done.stdout + done.stderr
    assert read_entry(folders.startup) == entry_for(this, login_args(other_home))
    assert not script.exists(), "today's start script is still in the Startup folder"
    assert read_entry(folders.start_menu) is None


def a_script_naming_no_taskloom_folder(folders: Folders, how: str, program: Path) -> Path:
    """The Startup folder's "Taskloom Scheduler.cmd" from which no Taskloom folder can be told: with no
    `set "TASKLOOM_HOME=..."` line (changed by hand: it starts `program`; empty; or cut off before that line's end), or
    not in UTF-8: today's script for the Taskloom folder .taskloom next to the Windows folders, starting `program`,
    saved by hand in Windows' ANSI code page (cp1252), so an é in those folders' path is a byte UTF-8 cannot read."""
    script = folders.startup.with_suffix(".cmd")
    script.parent.mkdir(parents=True, exist_ok=True)
    text = {"changed by hand": f'@echo off\nstart "" "{program}" scheduler --unless-paused\n', "empty": "",
            "cut off": '@echo off\nchcp 65001 >nul\nrem Taskloom 0.30.0\nset "TASKLO',
            "not in UTF-8": f'@echo off\nrem Taskloom 0.30.0\nset "TASKLOOM_HOME={folders.root.parent / ".taskloom"}"\n'
                            f'start "" "{program}" scheduler --unless-paused\n'}[how]
    script.write_text(text, encoding="cp1252" if how == "not in UTF-8" else "utf-8", newline="\r\n")
    return script


COULD_NOT = "Could not see to Taskloom's Start menu and Startup entries and older versions: "


@pytest.mark.parametrize("how", ["changed by hand", "empty", "cut off", "not in UTF-8"])
@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-4")
@pytest.mark.invariant("UP-7")
def test_u4_a_startup_script_that_names_no_taskloom_folder_is_kept_and_the_start_says_why(env, tmp_path, monkeypatch,
                                                                                        how):
    """U4, U10: the Startup folder's "Taskloom Scheduler.cmd" has no `set "TASKLOOM_HOME=..."` line (changed by hand to
    start 0.37.0, empty, or cut off), or is today's script starting 0.37.0 saved by hand in Windows' ANSI code page, not
    UTF-8 (the é of its folders' path cannot be read), so which Taskloom folder it is for cannot be told; there is no
    Startup entry. The Start menu entry starts 0.38.0; 0.38.0 and 0.37.0 were installed by Taskloom. The user's Windows
    folders are named with an é (as a user José's are). A start of this version (unpacked by hand) goes on, keeps the
    script as it is (the service still starts at login) and says on stderr which script it is and what it lacks or why
    it cannot be read; all the same, the Start menu entry now starts this version (its arguments kept), no Startup
    entry is made, and the older versions are removed but the one the script starts (the one a script not in UTF-8
    starts may be removed too: its text cannot be read)."""
    folders = windows_folders(tmp_path / "José", monkeypatch)
    home, p = env.home, folders.programs
    older = unpacked(p / "0.38.0", "0.38.0", marker_from=p / "0.37.0")
    named = unpacked(p / "0.37.0", "0.37.0", marker_from=p / "0.36.0")
    write_entry(folders.start_menu, older / "taskloomw.exe", editor_args(home), older)
    script = a_script_naming_no_taskloom_folder(folders, how, named / "taskloomw.exe")
    before = script.read_bytes()
    this = unpacked(p / __version__, __version__)

    done = Packaged(env, folders, this).run("run", env.write("hello.yaml", HELLO))

    assert done.returncode == 0, done.stdout + done.stderr
    assert entries_now(folders) == {"Start menu": entry_for(this, editor_args(home)), "Startup": None}, done.stderr
    if how == "not in UTF-8":
        assert [name for name in leftovers(folders) if name != "0.37.0"] == [__version__], done.stderr
    else:
        assert leftovers(folders) == sorted([__version__, *(["0.37.0"] if how == "changed by hand" else [])]), \
            done.stderr
    assert script.read_bytes() == before, "the Startup script was changed"
    lines = done.stderr.splitlines()
    lacks = "it is not in UTF-8" if how == "not in UTF-8" else "it has no TASKLOOM_HOME line"
    kept = f"Kept the Startup script {script}: {lacks}, so which Taskloom folder it is for cannot be told"
    assert kept in lines, lines
    assert not [line for line in lines if line.startswith(COULD_NOT)], lines


@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-7")
def test_u4_a_start_that_cannot_see_to_the_entries_goes_on_says_why_and_the_next_start_does_it(env, folders):
    """U4, U10: both entries start 0.30.0, which Taskloom installed, and they cannot be changed just now (where Taskloom
    locks them, <LOCALAPPDATA>/Taskloom, is a file). A start of this version (unpacked by hand) goes on: the entries and
    0.30.0 (they still start it) are left as they are, and a line on stderr says it could not see to them and why,
    with the path as it is (not quoted). Once that file is gone, the next start points both entries at this version
    and removes 0.30.0, without a word."""
    home, p = env.home, folders.programs
    older = unpacked(p / "0.30.0", "0.30.0", marker_from=p / "0.29.0")
    both_entries(folders, older, home)
    entries = entries_now(folders)
    locks = folders.local / "Taskloom"
    locks.write_text("not a folder\n", encoding="utf-8")
    this = unpacked(p / __version__, __version__)
    taskloom, flow = Packaged(env, folders, this), env.write("hello.yaml", HELLO)

    done = taskloom.run("run", flow)

    assert done.returncode == 0, done.stdout + done.stderr
    assert entries_now(folders) == entries, done.stderr
    assert leftovers(folders) == sorted(["0.30.0", __version__]), done.stderr
    lines = done.stderr.splitlines()
    assert [line for line in lines if re.fullmatch(re.escape(COULD_NOT) + r".+: " + re.escape(str(locks)), line)], lines

    locks.unlink()
    done = taskloom.run("run", flow)

    assert done.returncode == 0, done.stdout + done.stderr
    assert entries_now(folders) == {"Start menu": entry_for(this, editor_args(home)),
                                    "Startup": entry_for(this, login_args(home))}, done.stderr
    assert leftovers(folders) == [__version__], done.stderr
    assert not [line for line in done.stderr.splitlines() if line.startswith("Could not")], done.stderr


@pytest.mark.invariant("UP-2")
@pytest.mark.invariant("UP-8")
def test_u4_u7_an_install_beside_a_startup_script_that_names_no_taskloom_folder_puts_the_new_version_in_use(
        editor, qtbot, folders, this, releases, boxes, starts):
    """U4, U7: the Startup folder's "Taskloom Scheduler.cmd" was changed by hand (no `set "TASKLOOM_HOME=..."` line; it
    starts this version); the Start menu entry starts this version; a scheduler service runs, not turned off. Install?
    Yes: the Start menu entry starts NEW straight away, no Startup entry is made, the script is kept as it is, the
    status bar says which script it is and what it lacks (not that NEW could not be put in use), and NEW's
    service is started to take over."""
    home, new = editor.home.root, folders.programs / NEW
    write_entry(folders.start_menu, this / "taskloomw.exe", editor_args(home), this)
    script = a_script_naming_no_taskloom_folder(folders, "changed by hand", this / "taskloomw.exe")
    before = script.read_bytes()
    service_proc = stand_in_service(home, __version__)
    try:
        install(editor, qtbot, boxes)
    finally:
        service_proc.kill()
        service_proc.wait(timeout=15)
    told = editor.statusBar().currentMessage()

    boxes.take("question")
    assert entries_now(folders) == {"Start menu": entry_for(new, editor_args(home)), "Startup": None}, told
    assert script.read_bytes() == before, "the Startup script was changed"
    assert str(script) in told and "TASKLOOM_HOME" in told and "Could not put it in use" not in told, told
    assert [norm(Path(s.args[0]).parent) for s in services_started(starts)] == [norm(new)], told


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


@pytest.mark.invariant("UP-4")
def test_u5_start_at_login_turned_on_in_an_older_version_keeps_the_newer_versions_startup_entry(env, folders):
    """U5: the Startup entry starts 99.0.0, which is there. `taskloom scheduler --at-login on` of this (older) version
    leaves it starting 99.0.0: an older version never takes an entry away from a newer one."""
    home = env.home
    newer = unpacked(folders.programs / "99.0.0", "99.0.0")
    write_entry(folders.startup, newer / "taskloomw.exe", login_args(home), newer)
    this = unpacked(folders.programs / __version__, __version__)

    done = Packaged(env, folders, this).run("scheduler", "--at-login", "on")

    assert done.returncode == 0, done.stdout + done.stderr
    assert entries_now(folders) == {"Start menu": None, "Startup": entry_for(newer, login_args(home))}


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
            self._look()
            time.sleep(0.02)

    def _look(self):
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

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=10)
        self._look()  # a last look: the watch may have stopped before seeing what the test's own wait just saw


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
    assert started_on_its_own(one), one.how


@pytest.mark.invariant("UP-8")
def test_u7_turned_on_again_while_this_versions_service_still_runs_it_starts_no_second_one(qtbot, tmp_path, monkeypatch,
                                                                                          boxes):
    """U7: this version's scheduler service runs, turned off (it runs on until its runs are done). Its button in the
    editor turns it on again: that same service goes on, and the status bar says the service is on; no second service
    is started (only an older version's is taken over by a new one)."""
    from taskloom.editor.window import MainWindow

    home = tmp_path / "home"
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", NO_KEYRING)
    monkeypatch.setenv("TASKLOOM_HOME", str(home))
    real, started = subprocess.Popen, []

    def popen(args, *rest, **kwargs):  # (a service the editor starts is written down, not started)
        if isinstance(args, (list, tuple)) and "scheduler" in map(str, args):
            started.append(Started(args, kwargs.get("env"), kwargs))
            return started[-1]
        return real(args, *rest, **kwargs)

    service_proc = stand_in_service(home, __version__)
    try:
        set_paused(Home(home), True)
        monkeypatch.setattr(subprocess, "Popen", popen)
        window = MainWindow(Home(home))
        qtbot.addWidget(window)
        window.show()
        assert window.scheduler_running() and not window.scheduler_on()
        window.scheduler_action.trigger()
        told = window.statusBar().currentMessage()
        qtbot.wait(500)
    finally:
        service_proc.kill()
        service_proc.wait(timeout=15)

    assert told == "The scheduler service is on", told
    assert [s.args for s in started] == [], "a second service was started"


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
    """U8: two flows open, one with a run loaded (Load run state). Installed NEW; "Restart Taskloom now with version
    NEW?" Yes: this editor closes as usual and NEW's editor is started for the same Taskloom folder. That editor,
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
    assert started_on_its_own(started), started.how
    again = start_again(editor, qtbot)  # NEW's editor, on the same Taskloom folder
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
    """U8: two flows started from this editor (long-export, nightly) are still running when Yes is answered: a window
    over the editor that does not block it ("Restart Taskloom with version NEW") says Taskloom restarts with
    version NEW when long-export, nightly end, with Cancel at its right; the editor stays open, nothing starts yet.
    When the flows end, the editor restarts in NEW; after Cancel it does not (also not when the flows end)."""
    folder = tmp_path / "flows"
    folder.mkdir()
    tabs = []
    for name in ("nightly", "long-export"):
        flow = folder / f"{name}.yaml"
        flow.write_text(yaml.safe_dump({"blocks": {"manual": {"type": "trigger.manual"}, "hold": hold_until("hold")},
                                        "edges": ["manual.start -> hold.after"]}, sort_keys=False), encoding="utf-8")
        tabs.append(editor.open_flow(flow))
        editor.run_flow(tabs[-1])
    qtbot.waitUntil(lambda: all(tab.running for tab in tabs), timeout=30_000)
    try:
        install(editor, qtbot, boxes, restart=QMessageBox.Yes)
        qtbot.waitUntil(lambda: shown_widget(editor, "restart-waits") is not None, timeout=10_000)
        waits = shown_widget(editor, "restart-waits")
        assert waits.windowTitle() == f"Restart Taskloom with version {NEW}", waits.windowTitle()
        assert [label.text() for label in waits.findChildren(QLabel)] == [
            f"Taskloom restarts with version {NEW} when long-export, nightly ends."], words_of(waits)
        assert waits.parentWidget() is editor, "the restart's window is not the editor's (over it, closing with it)"
        assert at_the_right(button(waits, "Cancel"), waits), "Cancel is not at the right of the restart's window"
        assert QApplication.activeModalWidget() is None, "the restart's window blocks the editor"
        qtbot.wait(1500)
        assert editor.isVisible() and editors_started(starts) == [], "it restarted while the flow ran"
        if then == "Cancel":
            QTest.mouseClick(button(waits, "Cancel"), Qt.LeftButton)
            qtbot.waitUntil(lambda: shown_widget(editor, "restart-waits") is None, timeout=5_000)
    finally:
        (folder / "hold.go").write_text("go", encoding="utf-8")  # the flows end
        ran_out = eventually(qtbot, lambda: not any(tab.running for tab in tabs), timeout=60_000)
    assert ran_out, "the flows did not end"

    boxes.take("question")
    if then == "the flow ends":
        qtbot.waitUntil(lambda: bool(editors_started(starts)) and not editor.isVisible(), timeout=15_000)
        assert norm(editors_started(starts)[0].args[0]) == norm(folders.programs / NEW / "taskloomw.exe")
    else:
        qtbot.wait(4000)
        assert editor.isVisible() and editors_started(starts) == [], "it restarted after Cancel"


@pytest.mark.invariant("UP-6")
def test_u8_cancel_at_the_unsaved_changes_question_of_the_restart_keeps_the_editor_and_your_edits(
        editor, qtbot, tmp_path, folders, releases, boxes, starts):
    """U8: two flows open, the second with a change not saved. Installed NEW; "Restart Taskloom now with version
    NEW?" Yes; closing asks "Save changes to changed.yaml?"; Cancel: this editor stays open with both flows, the
    change still there and still not saved (the file as it was); NEW's editor is not started, not even later."""
    folder = tmp_path / "flows"
    folder.mkdir()
    for name in ("saved.yaml", "changed.yaml"):
        (folder / name).write_text(HELLO, encoding="utf-8")
    saved, changed = editor.open_flow(folder / "saved.yaml"), editor.open_flow(folder / "changed.yaml")
    changed.doc.move_blocks({"work": (55, 77)})
    assert changed.doc.dirty
    open_before = editor.tabs_list()
    boxes.answers = [(INSTALL, QMessageBox.Yes), (RESTART, QMessageBox.Yes), ("Save changes to", QMessageBox.Cancel)]

    check_for_updates(editor)
    qtbot.waitUntil(lambda: any("Save changes to" in said for _, said in boxes.seen)
                    or any(kind == "warning" for kind, _ in boxes.seen), timeout=60_000)
    qtbot.wait(2500)

    asked = boxes.take("question")
    try:
        assert [said for said in asked if "Save changes to" in said] == [
            "Unsaved changes\nSave changes to changed.yaml?"], asked
        assert len(asked) == 3 and INSTALL in asked[0] and RESTART in asked[1], asked
        assert boxes.take() == []
        assert editor.isVisible() and editors_started(starts) == [], "it restarted though closing was cancelled"
        assert editor.tabs_list() == open_before and saved in open_before, "closing was cancelled, yet flows closed"
        assert changed.doc.dirty and changed.doc.blocks["work"]["ui"] == {"x": 55, "y": 77}, changed.doc.blocks["work"]
        assert (folder / "changed.yaml").read_text(encoding="utf-8") == HELLO, "the change was saved"
        assert shown_widget(editor, "restart-waits") is None
        assert leftovers(folders) == [NEW]
    finally:
        changed.doc.dirty = False  # (the test's end closes the editor: it asks nothing then)


# --- U10: old versions --------------------------------------------------------------------------------------------------

@pytest.mark.invariant("UP-7")
def test_u10_a_start_removes_older_versions_taskloom_installed_and_keeps_the_rest(env, folders):
    """U10: in <LOCALAPPDATA>/Programs/Taskloom: this version (installed from 0.38.0), 0.38.0 (installed from 0.37.0),
    0.37.0 (installed; a file of it written in the same clock tick as its marker), 0.36.0 (unpacked by you: no
    marker), 0.35.0 (installed; a flow in it is scheduled), 0.34.0 (installed; you saved a flow in it), 0.32.0 (its
    marker is not Taskloom's: not a JSON object), 0.31.0 (installed; you moved a flow into it: a move keeps the
    flow's time, older than the marker), a copy of this version's folder, 99.0.0 (installed, newer), and a part of a
    download and of a removal left by a start that was cut off. A start of this version removes 0.37.0 and the two
    leftovers; it keeps the one it updated from, the one you unpacked, the one whose marker is not Taskloom's, the copy
    of this version, the newer one, and the three holding your flows, and says which and why."""
    p = folders.programs
    this = unpacked(p / __version__, __version__, marker_from=p / "0.38.0")
    unpacked(p / "0.38.0", "0.38.0", marker_from=p / "0.37.0")
    installed = unpacked(p / "0.37.0", "0.37.0", marker_from=p / "0.36.0")
    tick = (installed / MARKER).stat().st_mtime_ns
    os.utime(installed / "taskloomw.exe", ns=(tick, tick))
    unpacked(p / "0.36.0", "0.36.0")
    scheduled = unpacked(p / "0.35.0", "0.35.0", marker_from=p / "0.34.0",
                         extra={"examples/nightly.yaml": flow_yaml({"cron": "0 2 1 1 *"})})
    saved = unpacked(p / "0.34.0", "0.34.0", marker_from=p / "0.33.0")
    time.sleep(0.05)
    (saved / "examples" / "my-report.yaml").write_text(HELLO, encoding="utf-8")
    (unpacked(p / "0.32.0", "0.32.0") / MARKER).write_text("[]", encoding="utf-8")
    moved = unpacked(p / "0.31.0", "0.31.0", marker_from=p / "0.30.0") / "examples" / "moved.yaml"
    moved.write_text(HELLO, encoding="utf-8")
    earlier = (p / "0.31.0" / MARKER).stat().st_mtime_ns - 3600 * 10**9
    os.utime(moved, ns=(earlier, earlier))
    unpacked(p / f"{__version__} - Copy", __version__, marker_from=p / "0.38.0")
    unpacked(p / "99.0.0", "99.0.0", marker_from=p / __version__)
    unpacked(p / ".partial-k3j9" / "taskloom", NEW)
    unpacked(p / ".removing-0.33.0", "0.33.0")
    Home(env.home).set_scheduled((scheduled / "examples" / "nightly.yaml").resolve(), True)

    done = Packaged(env, folders, this).run("run", env.write("hello.yaml", HELLO))

    assert done.returncode == 0, done.stdout + done.stderr
    assert leftovers(folders) == sorted(["0.31.0", "0.32.0", "0.34.0", "0.35.0", "0.36.0", "0.38.0", "99.0.0",
                                         __version__, f"{__version__} - Copy"]), done.stderr
    assert (saved / "examples" / "my-report.yaml").read_text(encoding="utf-8") == HELLO
    assert moved.read_text(encoding="utf-8") == HELLO
    lines = done.stderr.splitlines()
    assert [line for line in lines if "0.35.0" in line and "scheduled" in line], f"not said: {lines}"
    assert [line for line in lines if "0.34.0" in line], f"not said: {lines}"
    assert [line for line in lines if "0.31.0" in line and moved.name in line], f"not said: {lines}"
    assert not [line for line in lines if "Could not" in line], lines


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


def writable(folder: Path):
    """Every file and folder in `folder` can be deleted again."""
    for path in folder.rglob("*"):
        path.chmod(0o700)


@pytest.mark.skipif(not WINDOWS and os.geteuid() == 0, reason="root deletes a file in a read-only folder all the same")
@pytest.mark.invariant("UP-7")
def test_u10_a_removal_cut_off_leaves_a_removing_folder_that_later_starts_finish_without_a_word(env, folders):
    """U10: 0.37.0 (installed by Taskloom, older) holds a file that cannot be deleted (on Windows a read-only file,
    elsewhere a file in a read-only folder). A start of this version removes what it can: the rest is left in
    .removing-0.37.0, and nothing is said. The next start, the file still not deletable, leaves it so and says nothing
    either; once the file can be deleted, the next start removes it."""
    p = folders.programs
    this = unpacked(p / __version__, __version__, marker_from=p / "0.38.0")
    unpacked(p / "0.38.0", "0.38.0", marker_from=p / "0.37.0")
    old = unpacked(p / "0.37.0", "0.37.0", marker_from=p / "0.36.0")
    taskloom, flow = Packaged(env, folders, this), env.write("hello.yaml", HELLO)
    stuck, mode = (old / "taskloom.exe", 0o400) if WINDOWS else (old / "examples", 0o500)
    try:
        stuck.chmod(mode)
        first = taskloom.run("run", flow)
        after_first = leftovers(folders)
        again = taskloom.run("run", flow)
        after_again = leftovers(folders)
    finally:
        writable(p)
    last = taskloom.run("run", flow)

    said = first.stderr + again.stderr
    assert [first.returncode, again.returncode, last.returncode] == [0, 0, 0], said + last.stderr
    assert after_first == after_again == sorted([".removing-0.37.0", "0.38.0", __version__]), said
    assert not [line for line in said.splitlines() if "0.37.0" in line or "Could not" in line], said
    assert leftovers(folders) == sorted(["0.38.0", __version__]), last.stderr


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
