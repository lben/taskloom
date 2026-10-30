import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest

# Never the real credential store (macOS Keychain, Windows Credential Manager): not in the runs the tests start, and
# not in the test process either (a test that looks up a secret itself would otherwise pick the real one for the
# rest of the session). A test that needs a store sets its own.
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"

REPO = Path(__file__).resolve().parents[1]
H2_URL = "https://repo1.maven.org/maven2/com/h2database/h2/2.3.232/h2-2.3.232.jar"
H2_SHA1 = "4fcc05d966ccdb2812ae8b9a718f69226c0cf4e2"


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


def _java_home() -> str | None:
    if os.environ.get("JAVA_HOME"):
        return os.environ["JAVA_HOME"]
    if sys.platform == "darwin" and Path("/usr/libexec/java_home").exists():
        found = subprocess.run(["/usr/libexec/java_home"], capture_output=True, text=True)
        return found.stdout.strip() or None
    java = shutil.which("java")
    return str(Path(java).resolve().parents[1]) if java else None


def _h2_jar() -> Path:
    jar = Path.home() / ".cache" / "taskloom-tests" / "h2-2.3.232.jar"
    if not jar.exists():
        jar.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = urllib.request.urlopen(H2_URL, timeout=60).read()
        except OSError as e:
            pytest.skip(f"cannot download the H2 JDBC driver: {e}")
        assert hashlib.sha1(data).hexdigest() == H2_SHA1
        jar.write_bytes(data)
    return jar


@pytest.fixture(scope="module")
def h2_server():
    """A real H2 database server (needs Java; downloads the H2 jar once): {"jar", "port", "java_home"}."""
    java_home = _java_home()
    if not java_home:
        pytest.skip("Java is not installed")
    jar = _h2_jar()
    with socket.socket() as s:
        s.bind(("localhost", 0))
        port = s.getsockname()[1]
    output = tempfile.TemporaryFile()  # (a file, not a pipe: the server never blocks writing to it)
    server = subprocess.Popen(
        [str(Path(java_home) / "bin" / "java"), "-cp", str(jar), "org.h2.tools.Server",
         "-tcp", "-tcpPort", str(port), "-ifNotExists"],
        stdout=output, stderr=output,
    )
    deadline = time.monotonic() + 30
    while True:
        try:
            socket.create_connection(("localhost", port), timeout=0.2).close()
            break
        except OSError:
            if server.poll() is not None or time.monotonic() > deadline:
                server.kill()
                server.wait()
                output.seek(0)
                printed = output.read().decode("utf-8", "replace")
                output.close()
                pytest.fail(f"the H2 server did not start; it printed:\n{printed}")
            time.sleep(0.1)
    yield {"jar": jar, "port": port, "java_home": java_home}
    server.terminate()
    server.wait()
    output.close()


@pytest.fixture
def jdbc_env(env, h2_server):
    """`env` with the H2 server as the JDBC connection `warehouse` (user sa, an empty password secret)."""
    env.env.pop("JAVA_HOME", None)  # found through the java_home setting instead
    env.write("home/settings.yaml", f"java_home: '{Path(h2_server['java_home']).as_posix()}'\n")
    env.env["JAVA_TOOL_OPTIONS"] = "-Xmx256m"  # keep JVM heap growth from hiding Python memory use
    env.write("home/connections.yaml", f"""
        warehouse:
          kind: jdbc
          driver: org.h2.Driver
          url: "jdbc:h2:tcp://localhost:{h2_server['port']}/mem:test"
          jars: ["{h2_server['jar'].as_posix()}"]
          properties: {{user: sa, password: "secret:warehouse_password"}}
    """)
    secrets = env.write("home/secrets.yaml", "warehouse_password: ''\n")
    secrets.chmod(0o600)
    return env


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
