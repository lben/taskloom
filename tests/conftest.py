import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


class Env:
    """A temporary Taskloom home plus helpers to run the real CLI against it."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.env = {
            **os.environ,
            "TASKLOOM_HOME": str(self.home),
            # Never touch the developer's real credential store from tests.
            "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
        }

    def write(self, relpath: str, text: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    def taskloom(self, *args, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "taskloom.cli", *map(str, args)],
            env=self.env, cwd=self.root, capture_output=True, text=True, timeout=kw.pop("timeout", 120), **kw,
        )

    def popen(self, *args) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-m", "taskloom.cli", *map(str, args)],
            env=self.env, cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def query(self, sql, *args):
        with sqlite3.connect(self.home / "history.db") as db:
            return db.execute(sql, args).fetchall()

    def last_run(self) -> tuple[int, str]:
        return self.query("SELECT id, status FROM runs ORDER BY id DESC LIMIT 1")[0]

    def block_status(self, run_id: int) -> dict:
        return {b: s for b, s in self.query("SELECT block, status FROM blocks WHERE run_id=?", run_id)}


def events(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


@pytest.fixture
def server(env):
    """A local SSH server registered as the connection `appserver`; its home is env.root / "server"."""
    from sshserver import PASSWORD, SSHTestServer

    home = env.root / "server"
    home.mkdir()
    srv = SSHTestServer(home).start()
    env.write("home/connections.yaml", srv.connection())
    env.write("home/secrets.yaml", f"appserver_password: {PASSWORD}\n").chmod(0o600)
    srv.home_path = home
    yield srv
    srv.stop()


@pytest.fixture
def smtp(env):
    """A local SMTP server registered as the connection `mailer`; received messages land in smtp.messages."""
    import email
    from email import policy

    from aiosmtpd.controller import Controller
    from sshserver import free_port

    class Inbox:
        messages = []

        async def handle_DATA(self, server, session, envelope):
            self.messages.append(email.message_from_bytes(envelope.content, policy=policy.default))
            return "250 OK"

    inbox = Inbox()
    inbox.messages = []
    port = free_port()
    controller = Controller(inbox, hostname="127.0.0.1", port=port)
    controller.start()
    connections = env.home / "connections.yaml"
    existing = connections.read_text() if connections.exists() else ""
    connections.write_text(existing + f"mailer:\n  kind: smtp\n  host: 127.0.0.1\n  port: {port}\n"
                                      f"  sender: taskloom@example.com\n")
    yield inbox
    controller.stop()


@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    """The real editor window, offscreen; message boxes are recorded (and fail the test), Run now's question
    is answered yes, and no real scheduler service is started."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QMessageBox

    from taskloom.config import Home
    from taskloom.editor import dialogs
    from taskloom.editor.window import MainWindow

    # The runner processes inherit this; never touch the real credential store.
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    # A message box would wait forever for a click; record it instead and fail the test.
    shown = []
    for name in ("information", "warning", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: shown.append((_n, a[1:3])) or QMessageBox.No))
    monkeypatch.setattr(dialogs, "confirm_run_now", lambda *a, **k: (True, False))  # Run now: yes, run it
    monkeypatch.setattr(dialogs, "offer_manual_start", lambda *a, **k: True)  # no start block: add one
    # A real scheduler service would outlive the test (and run its flows): the tests that need one say so.
    monkeypatch.setattr(MainWindow, "start_scheduler", lambda self, by_you=False: None)
    w = MainWindow(Home(tmp_path / "home"))
    w.message_boxes = shown
    qtbot.addWidget(w)
    w.show()
    yield w
    assert shown == [], f"unexpected message boxes: {shown}"
    for tab in w.tabs_list():
        for run in tab.runs:
            if run.running:
                run.proc.kill()
                run.proc.waitForFinished(5000)
        tab.doc.dirty = False
