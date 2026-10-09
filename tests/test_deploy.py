"""The deploy-runner example flow against a real SSH server."""

import io
import json
import os
import signal
import sys
import tarfile

import pytest

from conftest import REPO

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the test server runs commands in /bin/sh")

FAKE_RUNNER = "#!/bin/sh\nexec sleep 600\n"  # stands in for the real runner's scheduler


def _archive(path, version):
    with tarfile.open(path, "w:gz") as tar:
        for name, text, mode in (("taskloom/VERSION", version, 0o644), ("taskloom/taskloom", FAKE_RUNNER, 0o755)):
            info = tarfile.TarInfo(name)
            data = text.encode()
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))


def _run_answering(env, flow, archive, answer):
    """Run interactively like the editor does, answering the Ask User question."""
    proc = env.popen("run", flow, "--json-events", "--interactive", "--param", f"archive={archive}")
    asked = False
    for line in proc.stdout:
        event = json.loads(line)
        if event["event"] == "ask":
            asked = True
            proc.stdin.write(json.dumps({"ask_id": event["ask_id"], "value": answer}) + "\n")
            proc.stdin.flush()
    assert proc.wait(timeout=60) == 0, proc.stderr.read()
    return asked


def test_deploy_installs_then_asks_before_replacing(env, server):
    env.write("home/connections.yaml", server.connection().replace("appserver:", "deploy_target:"))
    env.write("home/secrets.yaml", "deploy_target_password: s3cret\n").chmod(0o600)
    env.home.joinpath("connections.yaml").write_text(
        env.home.joinpath("connections.yaml").read_text().replace("secret:appserver_password", "secret:deploy_target_password"))
    flow = env.root / "deploy.yaml"
    flow.write_text((REPO / "examples" / "deploy-runner.yaml").read_text())
    version_file, pid_file = server.home_path / "taskloom" / "VERSION", server.home_path / ".taskloom" / "scheduler.pid"
    _archive(env.root / "v1.tar.gz", "1.0.0")
    _archive(env.root / "v2.tar.gz", "2.0.0")
    try:
        first = env.taskloom("run", flow, "--param", f"archive={env.root / 'v1.tar.gz'}")
        assert first.returncode == 0, first.stderr
        assert version_file.read_text() == "1.0.0"
        run_id, _ = env.last_run()
        assert env.block_status(run_id)["confirm"] == "skipped" and env.block_status(run_id)["running"] == "success"
        first_pid = pid_file.read_text().strip()

        unattended = env.taskloom("run", flow, "--param", f"archive={env.root / 'v2.tar.gz'}")  # default answer: no
        assert unattended.returncode == 0 and version_file.read_text() == "1.0.0"

        assert _run_answering(env, flow, env.root / "v2.tar.gz", False)
        assert version_file.read_text() == "1.0.0"
        assert pid_file.read_text().strip() == first_pid

        assert _run_answering(env, flow, env.root / "v2.tar.gz", True)
        assert version_file.read_text() == "2.0.0"
        assert pid_file.read_text().strip() != first_pid
        with pytest.raises(ProcessLookupError):
            os.kill(int(first_pid), 0)  # the old scheduler was stopped
    finally:
        try:
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass
