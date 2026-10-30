"""Our own test tooling: the h2_server fixture fails clearly when its server does not start, and the editor fuzz
script runs every seed to its end."""

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sshserver import free_port

HERE = Path(__file__).parent
UNASSIGNABLE = "192.0.2.1"  # a documentation address (RFC 5737) no machine has: H2 cannot listen on it

USES_H2 = """
from pathlib import Path


def test_uses_the_server(h2_server):
    Path(__file__).with_name("reached").touch()
"""


@pytest.mark.parametrize("seed", [9, 11])  # their first random action is Save
def test_fuzz_editor_runs_to_the_end_when_it_saves_first(tmp_path, seed):
    try:
        proc = subprocess.run([sys.executable, str(HERE / "fuzz_editor.py"), str(seed), "250", str(tmp_path)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        pytest.fail(f"fuzz_editor.py seed {seed} did not finish within 120 s (a dialog waiting for a click?)")
    assert proc.returncode == 0 and proc.stdout.strip().endswith("ok"), proc.stdout[-2000:] + proc.stderr[-4000:]


def _accepts(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


@pytest.mark.parametrize("hangs", [False, True], ids=["server-exits", "server-never-answers"])
def test_h2_fixture_fails_clearly_when_its_server_does_not_come_up(tmp_path, hangs):
    # The real fixture (a copy of conftest.py) in a pytest session of its own, its Java told what cannot work.
    session = tmp_path / "session"
    session.mkdir()
    shutil.copyfile(HERE / "conftest.py", session / "conftest.py")
    (session / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (session / "test_uses_h2.py").write_text(USES_H2, encoding="utf-8")
    options = f"-Dh2.bindAddress={UNASSIGNABLE}"  # H2 prints why it cannot listen, and exits
    debug_port = free_port()
    if hangs:  # the JVM waits for a debugger before it runs anything: alive, never listening (a very slow start)
        options += f" -agentlib:jdwp=transport=dt_socket,server=y,suspend=y,address=127.0.0.1:{debug_port}"
    env = {**os.environ, "JAVA_TOOL_OPTIONS": options, "COLUMNS": "500",  # (no cut summary lines)
           "PYTHONPATH": os.pathsep.join([str(HERE), os.environ.get("PYTHONPATH", "")])}  # its helper modules
    log = session / "output.txt"
    try:
        try:
            with open(log, "w", encoding="utf-8") as out:  # a file, not a pipe: nothing it starts can keep us waiting
                subprocess.run([sys.executable, "-m", "pytest", "-q", "-rsE", "--tb=native", "-p", "no:cacheprovider",
                                "test_uses_h2.py"], cwd=session, env=env, stdout=out, stderr=subprocess.STDOUT,
                               timeout=90)
        except subprocess.TimeoutExpired:
            pytest.fail("the h2_server fixture did not finish within 90 s:\n"
                        + log.read_text(encoding="utf-8", errors="replace")[-3000:])
        text = log.read_text(encoding="utf-8", errors="replace")
        skipped = re.search(r"^SKIPPED .*(Java is not installed|cannot download the H2 JDBC driver).*$", text, re.M)
        if skipped:
            pytest.skip(skipped[0])

        assert not (session / "reached").exists(), \
            "the fixture handed over an H2 server that never accepted a connection:\n" + text[-3000:]
        error = re.search(r"^ERROR \S*::test_uses_the_server - (.+)$", text, re.M)
        assert error, "the fixture did not fail at setup:\n" + text[-3000:]
        assert re.search(r"h2.*start", error[1], re.I), f"the failure does not say the H2 server did not start: {error[1]}"
        printed = "Listening for transport dt_socket" if hangs else "BindException"  # (stdout; stderr)
        assert printed in text, f"what the server printed ({printed}...) is not in the failure:\n" + text[-3000:]
        if hangs:
            deadline = time.monotonic() + 10
            while _accepts(debug_port) and time.monotonic() < deadline:
                time.sleep(0.2)
            assert not _accepts(debug_port), "the H2 server's Java process was left running"
    finally:
        if hangs and _accepts(debug_port):  # let a JVM left waiting go on: it then cannot listen, and exits
            try:
                with socket.create_connection(("127.0.0.1", debug_port), timeout=5) as debugger:
                    debugger.sendall(b"JDWP-Handshake")
                    debugger.recv(14)
            except OSError:
                pass  # (it has just gone by itself)
