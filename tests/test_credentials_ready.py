"""Credentials on the PC (docs/promises/credentials.yaml), a secret's value of several lines in what a run's code writes
(CR-5): shaped as a JSON key file is (its first line `{`, lines that open a nested object ending in `{`, its last line
`}`), its first line empty, two of them read at once, or holding a character the run's output has not. However the
code writes it, it shows as ***, never a line of it; the other text shows as written, in order; and what the code
writes shows while the code still runs, not only once it has ended. Through the real `taskloom run` only, started as
the editor starts a run (PYTHONUNBUFFERED=1), against the tests' own credential store (tests/credential_store, never
the real one), and read as it comes: a run that never ends is ended (with every process it started) and fails.

What the tests take as given (beside those of tests/test_credentials_review.py): what the code prints with Python
Code's print goes to the block's Log, which `taskloom run` prints to its error output as it comes, a line
`<time> [<block>] INFO: <text>`; what it writes to sys.stdout or below print to file descriptor 1 goes to the
output. A prompt written without a line break and flushed shows at once.
"""

import locale
import os
import signal
import subprocess
import threading
import time

import pytest

from test_credentials_relay import command, flow_of
from test_credentials_review import KEY, KEY_LINES, runs_of
from test_credentials_store import plant, told, write_cards

# A made-up key file in JSON (stored without a line break at its end): two nested objects, so `{` ends lines inside it.
JSON_LINES = ["{",
              '  "made_up_account": {',
              '    "taskloom_test_client": {',
              '      "client_id": "TaskloomMadeUpClient-4f7a"',
              '      "private_key": "TaskloomMadeUpPrivateKey-9c2e-AbCdEf0123456789"',
              "    }",
              "  }",
              "}"]
JSON_KEY = "\n".join(JSON_LINES)
OTHER_LINES = ["{", '  "report": "ready",', '  "rows": 12', "}"]  # another JSON document, which holds no value
# A key file copied with an empty line before it: its first line is empty.
BLANK_LINES = ["", "{", '  "made_up_service": {', '    "token": "TaskloomMadeUpToken-3b8d-ZyXwVu9876"', "  }", "}"]
BLANK_KEY = "\n".join(BLANK_LINES)
# A value of several lines with a letter that cp1252 (a Windows pipe's output) has not: ż.
POLISH_LINES = ["{", '  "made_up_owner": "żurawina-Lantern-61"', "}"]
POLISH_KEY = "\n".join(POLISH_LINES)


@pytest.fixture(autouse=True)
def stored(env, credential_store):
    write_cards(env, {"JSON": {"rows": {"key": None}}, "PEM": {"rows": {"key": None}},
                      "BLANK": {"rows": {"key": None}}, "PL": {"rows": {"key": None}}})
    plant(credential_store, {"JSON.key": JSON_KEY, "PEM.key": KEY, "BLANK.key": BLANK_KEY, "PL.key": POLISH_KEY})


class Going:
    """`taskloom run` of a flow whose Python Code block `low` runs `source`, started as the editor starts a run
    (PYTHONUNBUFFERED=1), what it prints to its output and its error output read as it comes (as text in `encoding`:
    this system's, as a terminal shows it). On Linux and macOS in a process group of its own, so that a run that
    never ends is ended with every process it started (its relays too)."""

    def __init__(self, env, source: str, encoding: str | None = None):
        env.env["PYTHONUNBUFFERED"] = "1"
        self.encoding = encoding or locale.getpreferredencoding(False)
        self.process = subprocess.Popen(command(flow_of(env, source)), env=env.env, cwd=env.root,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        start_new_session=os.name == "posix")
        self.printed = {"out": b"", "err": b""}
        self.readers = [threading.Thread(target=self._read, args=(name, pipe), daemon=True)
                        for name, pipe in (("out", self.process.stdout), ("err", self.process.stderr))]
        for reader in self.readers:
            reader.start()

    def _read(self, name, pipe):
        while chunk := os.read(pipe.fileno(), 65536):
            self.printed[name] += chunk

    def lines(self, name: str) -> list[str]:
        """The lines printed so far to the output ("out") or the error output ("err")."""
        return self.printed[name].decode(self.encoding, "replace").splitlines()

    def until(self, shows, within: float = 20) -> bool:
        """Whether shows() came true within `within` seconds, while the run still went."""
        deadline = time.monotonic() + within
        while not shows():
            if time.monotonic() > deadline or self.process.poll() is not None:
                return shows()
            time.sleep(0.02)
        return True

    def end(self, within: float = 90) -> subprocess.CompletedProcess:
        """Once the run has ended (or it fails, ending it, if it has not within `within` seconds): its exit code and
        all it printed."""
        try:
            code = self.process.wait(within)
        except subprocess.TimeoutExpired:
            self.stop()
            pytest.fail(f"the run had not ended {within:g} s later")
        for reader in self.readers:
            reader.join(10)
        return subprocess.CompletedProcess(self.process.args, code, "\n".join(self.lines("out")),
                                           "\n".join(self.lines("err")))

    def stop(self):
        if os.name == "posix":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:  # (none of its processes is left)
                pass
        elif self.process.poll() is None:
            self.process.kill()
        self.process.wait()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()


def ran(env, source: str, **kw) -> subprocess.CompletedProcess:
    """The run of `source` (Going), once it has ended; it succeeded."""
    with Going(env, source, **kw) as going:
        result = going.end()
    assert result.returncode == 0, told(env, result)
    return result


def shown(env, result, lines: list[str]) -> list[str]:
    """The lines of a value (those with a letter: `{` and `}` show in other text too) that show anywhere: in what the
    run printed, its Log or its blocks' errors."""
    said = told(env, result)
    return [line for line in lines if any(c.isalpha() for c in line) and line in said]


def waits_for(steps) -> str:
    """Python for the run's code: seen(step) waits (at most 25 s) for the test to have seen what it wrote before."""
    return f'''\
import time
from pathlib import Path
def seen(step):
    for _ in range(500):
        if (Path({str(steps)!r}) / f"seen-{{step}}").exists():
            return
        time.sleep(0.05)
    raise TimeoutError(f"step {{step}} was not seen")
'''


# --- however it is written, only *** ------------------------------------------------------------------------------

JSON_THREE_WAYS = f'''\
import os, sys, time
k = secret("JSON.key")
other = {OTHER_LINES!r}
print("log-start")
for line in other + k.splitlines():
    print(line)
print("log-end")
sys.stdout.write("out-start\\n")
for line in other + k.splitlines():
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()
sys.stdout.write("out-end\\n")
sys.stdout.flush()
os.write(1, b"fd-start\\n")
for line in other + k.splitlines():
    os.write(1, (line + "\\n").encode("utf-8"))
    time.sleep(0.2)
os.write(1, b"fd-end\\n")
'''


@pytest.mark.invariant("CR-5")
def test_a_json_key_file_printed_a_line_at_a_time_shows_only_as_stars_and_a_json_document_before_it_as_written(env):
    """The code prints another JSON document and then the JSON key file, each a line at a time: with Python Code's
    print (to the Log), with sys.stdout.write and a flush after each line, and below print with os.write, a fifth of a
    second between the lines (so each comes on its own). Each way, the document shows as written, then one line ***,
    in order between its start and end lines; no line of the key shows anywhere; and the run ends."""
    result = ran(env, JSON_THREE_WAYS)

    assert shown(env, result, JSON_LINES) == [], told(env, result)
    assert runs_of(result.stderr.splitlines(), "log-start", *OTHER_LINES, "***", "log-end"), result.stderr
    for name in ("out", "fd"):
        assert runs_of(result.stdout.splitlines(), f"{name}-start", *OTHER_LINES, "***", f"{name}-end"), result.stdout


BLANK_FIRST_LINE = '''\
import sys
k = secret("BLANK.key")
print("log-before")
for line in k.split("\\n"):
    print(line)
print("log-after")
sys.stdout.write("out-before\\n")
for line in k.split("\\n"):
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()
sys.stdout.write("out-after\\n")
sys.stdout.flush()
'''


@pytest.mark.invariant("CR-5")
def test_a_value_whose_first_line_is_empty_printed_a_line_at_a_time_shows_only_as_stars(env):
    """A key file stored with an empty line before it: the code prints a line, then the value a line at a time (its
    empty first line too), then another line, with Python Code's print (to the Log) and with sys.stdout.write and a
    flush after each line. Between the two lines there is one line, ***, and no line of the value shows anywhere."""
    result = ran(env, BLANK_FIRST_LINE)

    assert shown(env, result, BLANK_LINES) == [], told(env, result)
    assert runs_of(result.stderr.splitlines(), "log-before", "***", "log-after"), result.stderr
    assert runs_of(result.stdout.splitlines(), "out-before", "***", "out-after"), result.stdout


TWO_READ = '''\
import sys
json_key, pem_key = secret("JSON.key"), secret("PEM.key")
for name, k in (("json", json_key), ("pem", pem_key)):
    sys.stdout.write(f"{name}-start\\n")
    for line in k.splitlines():
        sys.stdout.write(line + "\\n")
        sys.stdout.flush()
    sys.stdout.write(f"{name}-end\\n")
    sys.stdout.flush()
'''


@pytest.mark.invariant("CR-5")
def test_two_values_of_several_lines_read_in_one_block_each_show_only_as_stars_written_a_line_at_a_time(env):
    """The code reads two values of several lines (the JSON key file, then a PEM key), then writes each a line at a
    time to sys.stdout, a flush after each line: each shows as one line *** between its start and end lines, and no
    line of either shows anywhere."""
    result = ran(env, TWO_READ)

    assert shown(env, result, JSON_LINES + KEY_LINES) == [], told(env, result)
    for name in ("json", "pem"):
        assert runs_of(result.stdout.splitlines(), f"{name}-start", "***", f"{name}-end"), result.stdout


IN_CP1252 = '''\
import sys
k = secret("PL.key")
sys.stdout.write(f"key: {k} end\\n")
sys.stdout.flush()
sys.stdout.write("after the key\\n")
sys.stdout.flush()
'''


@pytest.mark.invariant("CR-5")
def test_a_value_of_several_lines_with_a_letter_the_output_has_not_shows_as_stars_and_what_follows_still_shows(env):
    """The run's output is in cp1252 (as printed into a pipe on Windows; PYTHONIOENCODING gives it so on any
    system), which has no ż; the value, of three lines, holds one. The code writes it inside a line, then another
    line: the output is these two lines, the value as ***."""
    env.env["PYTHONIOENCODING"] = "cp1252"

    result = ran(env, IN_CP1252, encoding="cp1252")

    assert result.stdout.splitlines() == ["key: *** end", "after the key"], told(env, result)
    assert shown(env, result, POLISH_LINES) == [], told(env, result)


IN_CP1252_IN_PARTS = '''\
import sys
k = secret("PL.key")
sys.stdout.write("lines-start\\n")
for line in k.split("\\n"):
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()
sys.stdout.write("lines-end\\n")
sys.stdout.write("parts " + k[:20])
sys.stdout.flush()
sys.stdout.write(k[20:] + " parts-end\\n")
sys.stdout.flush()
'''


@pytest.mark.invariant("CR-5")
def test_a_value_of_several_lines_with_a_letter_the_output_has_not_written_in_parts_shows_only_as_stars(env):
    """As above (the output in cp1252, the value of three lines holding ż), the code writes the value a line at a time
    to sys.stdout, a flush after each line, and then in two parts with a flush between them (the first ending inside
    its second line): each shows as one ***, and no line of the value shows anywhere, not even with ż written as
    Python writes a letter the output has not (\\u017c)."""
    env.env["PYTHONIOENCODING"] = "cp1252"

    result = ran(env, IN_CP1252_IN_PARTS, encoding="cp1252")

    assert result.stdout.splitlines() == ["lines-start", "***", "lines-end", "parts *** parts-end"], result.stdout
    assert "Lantern-61" not in told(env, result), told(env, result)


# --- what the code writes shows while it still runs ---------------------------------------------------------------

def log_line(going, text: str) -> bool:
    return any(line.endswith(f"[low] INFO: {text}") for line in going.lines("err"))


@pytest.mark.invariant("CR-5")
def test_what_code_prints_shows_while_it_still_runs_also_next_to_a_value_of_several_lines(env):
    """Each time, the code writes, then waits for the test to have seen it: a line with Python Code's print (in the
    Log); the JSON key file whole after `key:` with print (`key: ***` in the Log); and, in one write below print,
    another JSON document and the key file's first three lines (the document shows, while the rest of the key is
    still to come). Then the rest of the key and an end line: the output is the document, ***, the end line, and no
    line of the key shows anywhere."""
    steps = env.root / "steps"
    steps.mkdir()
    source = waits_for(steps) + f'''\
import os
k = secret("JSON.key")
print("live-print")
seen("print")
print("key:", k)
seen("key")
lines = k.split("\\n")
os.write(1, ("\\n".join({OTHER_LINES!r} + lines[:3]) + "\\n").encode("utf-8"))
seen("before")
os.write(1, ("\\n".join(lines[3:]) + "\\nfd-end\\n").encode("utf-8"))
print("seen")
'''
    with Going(env, source) as going:
        assert going.until(lambda: log_line(going, "live-print")), going.lines("err")
        (steps / "seen-print").touch()
        assert going.until(lambda: log_line(going, "key: ***")), going.lines("err")
        (steps / "seen-key").touch()
        assert going.until(lambda: runs_of(going.lines("out"), *OTHER_LINES)), going.lines("out")
        (steps / "seen-before").touch()
        result = going.end()

    assert result.returncode == 0, told(env, result)
    assert result.stdout.splitlines() == OTHER_LINES + ["***", "fd-end"], result.stdout
    assert shown(env, result, JSON_LINES) == [], told(env, result)
    assert [line for line in result.stderr.splitlines() if line.endswith("[low] INFO: seen")], result.stderr


@pytest.mark.invariant("CR-5")
def test_a_prompt_without_a_line_break_written_and_flushed_shows_before_the_code_goes_on(env):
    """The code writes `Name? ` to sys.stdout with no line break, flushes, and waits for the test to have seen it;
    then it writes `seen`: the output is `Name? seen`."""
    steps = env.root / "steps"
    steps.mkdir()
    source = waits_for(steps) + '''\
import sys
sys.stdout.write("Name? ")
sys.stdout.flush()
seen("prompt")
sys.stdout.write("seen\\n")
'''
    with Going(env, source) as going:
        assert going.until(lambda: going.printed["out"] == b"Name? "), going.printed["out"]
        (steps / "seen-prompt").touch()
        result = going.end()

    assert result.returncode == 0, told(env, result)
    assert result.stdout.splitlines() == ["Name? seen"], result.stdout

