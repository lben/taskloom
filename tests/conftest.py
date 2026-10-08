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
            env=self.env, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
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
