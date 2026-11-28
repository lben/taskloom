"""Credentials on the PC (docs/promises/credentials.yaml): what a run's code writes below print (straight to file
descriptors 1 and 2, a program it starts) reaches the terminal exactly as written, with every secret as *** (CR-5),
also when it is written in parts, is long, holds another secret, is not UTF-8, is written just before the run dies,
or comes from a program still running as the run ends; it comes as it is written (not only once the run ends), in the
order written to the output and the error output; and what `taskloom secret set` and `taskloom secret delete` say
(CR-9). Through the real `taskloom` program only (python -m taskloom.cli, as a terminal starts it), against the tests'
own credential store (tests/credential_store). What the program printed is read as bytes, exactly as written.

What the tests take as given (beside tests/test_credentials_sinks.py's): the run's own lines (run started, a block
finished, ...) go to its error output; its output carries only what code writes there. A Python Code block's print()
goes to its Log, so the code writes to the terminal with os.write, sys.stdout.write and sys.stderr.write."""

import os
import signal
import subprocess
import sys
import threading
import time
import pytest

from test_credentials_refs import write_flow
from test_credentials_sinks import TOKEN, URL_SAFE, code, forms
from test_credentials_store import card_entries, plant, taskloom, write_cards

ACCENT = "Ünïcødé Wren-58"  # UAT.accent: letters cp1252 holds, so a Windows pipe's output can carry it
LONG = ("Lantern-é-" * 30)[:300]  # UAT.long: 330 bytes as UTF-8 (more than one part of what the run tells)
INNER, OUTER = "Wren-Lantern-42", "Alder-Wren-Lantern-42-Moss"  # UAT.inner, UAT.outer: one holds the other
VALUES = {"UAT.token": TOKEN, "UAT.accent": ACCENT, "UAT.long": LONG, "UAT.inner": INNER, "UAT.outer": OUTER}
CARDS = {"UAT": {"rows": {name.split(".")[1]: None for name in VALUES}}}


@pytest.fixture(autouse=True)
def stored(env, credential_store):
    write_cards(env, CARDS)
    plant(credential_store, VALUES)


def command(path) -> list[str]:
    return [sys.executable, "-m", "taskloom.cli", "run", str(path)]


def run_bytes(env, path, timeout: float = 120, **kw) -> subprocess.CompletedProcess:
    """`taskloom run PATH`; what it printed as bytes."""
    kw.setdefault("stdout", subprocess.PIPE)
    kw.setdefault("stderr", subprocess.PIPE)
    return subprocess.run(command(path), env=env.env, cwd=env.root, stdin=subprocess.DEVNULL, timeout=timeout, **kw)


def lines(data: bytes) -> bytes:
    """Line breaks as \\n (text written by sys.stdout gets \\r\\n on Windows)."""
    return data.replace(b"\r\n", b"\n")


def secret_shown(data: bytes, *values: str) -> list[str]:
    return sorted({form for value in values for form in forms(value) if form.encode("utf-8") in data})


def flow_of(env, source: str):
    return write_flow(env, {"low": code(source)})


class Live:
    """A `taskloom run` going: what it prints to its output, read as it comes."""

    def __init__(self, env, path):
        self.process = subprocess.Popen(command(path), env=env.env, cwd=env.root, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.out, self.err = b"", []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: self.err.append(self.process.stderr.read()), daemon=True).start()

    def _read(self):
        while chunk := os.read(self.process.stdout.fileno(), 65536):
            self.out += chunk

    def until(self, wanted: bytes, within: float = 20) -> bytes:
        """What it has printed once that is `wanted`, or after `within` seconds (or once the run ended)."""
        deadline = time.monotonic() + within
        while lines(self.out) != wanted and time.monotonic() < deadline and self.process.poll() is None:
            time.sleep(0.02)
        time.sleep(0.1)  # (nothing more comes after it)
        return lines(self.out)

    def stop(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()


# --- exactly as written, with *** --------------------------------------------------------------------------------

EXACT = f'''\
import os, sys, time
from urllib.parse import quote, quote_plus
t = secret("UAT.token")
for form in (t, quote(t, safe=""), quote(t), quote_plus(t), quote(t, safe={URL_SAFE!r})):
    os.write(1, f"form {{form}} .\\n".encode("utf-8"))
os.write(1, b"bytes \\xff\\xfe caf\\xe9 " + t.encode("utf-8") + b" .\\n")
os.write(1, b"split .\\n" + t[:5].encode("utf-8"))
time.sleep(0.5)
os.write(1, t[5:].encode("utf-8") + b" .\\n")
os.write(2, f"err {{t}} .\\n".encode("utf-8"))
'''


@pytest.mark.invariant("CR-5")
def test_what_code_writes_below_print_reaches_the_terminal_exactly_as_written_with_every_form_as_stars(env):
    """The code writes the value in each form a web address gives it, inside bytes that are not UTF-8, split across
    two writes half a second apart (the first part after a line it ends), and to the error output: the output is
    exactly what it wrote, line by line, with *** in place of each form; the error output has its line once."""
    result = run_bytes(env, flow_of(env, EXACT))

    assert result.returncode == 0, result.stderr
    assert result.stdout == b"form *** .\n" * 5 + b"bytes \xff\xfe caf\xe9 *** .\n" + b"split .\n*** .\n"
    assert lines(result.stderr).count(b"err *** .\n") == 1, result.stderr
    assert secret_shown(result.stdout + result.stderr, TOKEN) == [], result.stdout + result.stderr


@pytest.mark.invariant("CR-5")
def test_a_long_value_and_one_holding_another_show_whole_as_stars(env):
    """A value of 300 characters (330 bytes, more than one part of what the run tells), as it is and URL-encoded,
    and two values where the longer holds the shorter (the shorter read first): each shows as *** whole."""
    source = '''\
import os
from urllib.parse import quote
inner, outer, long = secret("UAT.inner"), secret("UAT.outer"), secret("UAT.long")
os.write(1, f"long {long} .\\nquoted {quote(long)} .\\n".encode("utf-8"))
os.write(1, f"outer {outer} .\\ninner {inner} .\\n".encode("utf-8"))
'''
    result = run_bytes(env, flow_of(env, source))

    assert result.returncode == 0, result.stderr
    assert result.stdout == b"long *** .\nquoted *** .\nouter *** .\ninner *** .\n"
    assert secret_shown(result.stdout + result.stderr, LONG, INNER) == [], result.stdout + result.stderr


@pytest.mark.invariant("CR-5")
def test_what_a_run_writes_just_before_it_dies_reaches_the_terminal_with_stars(env):
    """The code writes the value without a line break to the output and to the error output, then the run dies at
    once (os._exit(3)): both end with what it wrote, the value as ***, and the run's exit code is 3."""
    source = '''\
import os
t = secret("UAT.token")
os.write(1, f"last words {t}".encode("utf-8"))
os.write(2, f"last error {t}".encode("utf-8"))
os._exit(3)
'''
    result = run_bytes(env, flow_of(env, source))

    assert result.returncode == 3, result.stderr
    assert result.stdout == b"last words ***"
    assert result.stderr.endswith(b"last error ***"), result.stderr
    assert secret_shown(result.stdout + result.stderr, TOKEN) == [], result.stdout + result.stderr


@pytest.mark.invariant("CR-5")
def test_text_flushed_without_a_line_break_shows_with_stars(env):
    """sys.stdout.write of the value with no line break, then a flush, then more bytes: one line, with ***."""
    source = '''\
import os, sys
sys.stdout.write("no newline " + secret("UAT.token"))
sys.stdout.flush()
os.write(1, b" after\\n")
'''
    result = run_bytes(env, flow_of(env, source))

    assert result.returncode == 0, result.stderr
    assert lines(result.stdout) == b"no newline *** after\n"


@pytest.mark.invariant("CR-5")
@pytest.mark.parametrize("unbuffered", [False, True], ids=["buffered", "unbuffered as the editor starts a run"])
def test_a_value_shows_as_stars_in_the_encoding_the_output_has(env, unbuffered):
    """Printed into a pipe on Windows, the output is in the ANSI code page (cp1252), not UTF-8 (PYTHONIOENCODING
    gives it so on any system): text and bytes the code writes in it reach the terminal in it, the value as ***."""
    env.env["PYTHONIOENCODING"] = "cp1252"
    if unbuffered:
        env.env["PYTHONUNBUFFERED"] = "1"
    source = '''\
import os, sys
t = secret("UAT.accent")
sys.stdout.write(f"text {t} café\\n")
sys.stdout.flush()
os.write(1, f"bytes {t} café\\n".encode("cp1252"))
'''
    result = run_bytes(env, flow_of(env, source))

    assert result.returncode == 0, result.stderr
    assert lines(result.stdout) == "text *** café\nbytes *** café\n".encode("cp1252")


@pytest.mark.invariant("CR-5")
@pytest.mark.skipif(sys.platform == "win32", reason="a program's output closed before it starts: POSIX only")
def test_with_the_output_closed_what_code_writes_to_the_error_output_shows_with_stars(env):
    """`taskloom run` started with its output closed (as `>&-` in a shell): what the code writes to the error output
    shows with ***."""
    source = 'import os\nos.write(2, ("to stderr " + secret("UAT.token") + "\\n").encode("utf-8"))\n'

    result = run_bytes(env, flow_of(env, source), stdout=subprocess.DEVNULL, preexec_fn=lambda: os.close(1))

    assert result.returncode == 0, result.stderr
    assert b"to stderr ***\n" in result.stderr, result.stderr
    assert secret_shown(result.stderr, TOKEN) == [], result.stderr


# --- as it is written, in order ----------------------------------------------------------------------------------

@pytest.mark.invariant("CR-5")
def test_what_code_writes_comes_as_it_is_written_not_when_the_run_ends(env):
    """Started as the editor starts a run (PYTHONUNBUFFERED=1, whose Log shows a block's output as it comes): two
    lines in one write, an empty line, then two lines by sys.stdout.write with no flush. Each comes, with ***, while
    the code waits for the test to have seen it."""
    env.env["PYTHONUNBUFFERED"] = "1"
    steps = env.root / "steps"
    steps.mkdir()
    source = f'''\
import os, sys, time
from pathlib import Path
t = secret("UAT.token")
def seen(step):
    for _ in range(1200):
        if (Path({str(steps)!r}) / f"seen-{{step}}").exists():
            return
        time.sleep(0.05)
    raise TimeoutError(f"step {{step}} was not seen")
os.write(1, f"alpha\\nbeta {{t}}\\n".encode("utf-8"))
seen(1)
os.write(1, b"\\n")
seen(2)
sys.stdout.write(f"gamma\\ndelta {{t}}\\n")
seen(3)
'''
    live = Live(env, flow_of(env, source))
    try:
        wanted = b""
        for step, comes in enumerate([b"alpha\nbeta ***\n", b"\n", b"gamma\ndelta ***\n"], start=1):
            wanted += comes
            assert live.until(wanted) == wanted, f"step {step}"
            (steps / f"seen-{step}").touch()
        assert live.process.wait(60) == 0, live.err
    finally:
        live.stop()
    assert lines(live.out) == wanted


@pytest.mark.invariant("CR-5")
def test_what_code_writes_to_the_output_and_then_the_error_output_shows_in_that_order(env):
    """Output and error output to one terminal (2>&1): three times, many lines to the output and a flush, then one
    line to the error output and a flush. The terminal shows them in the order written, each value as ***."""
    source = '''\
import sys
t = secret("UAT.token")
sys.stdout.write(f"start {t}\\n")
sys.stdout.flush()
for round in range(3):
    sys.stdout.write("".join(f"line {round} {i} {t}\\n" for i in range(4000)))
    sys.stdout.flush()
    sys.stderr.write(f"after {round} {t}\\n")
    sys.stderr.flush()
'''
    result = run_bytes(env, flow_of(env, source), stderr=subprocess.STDOUT)

    assert result.returncode == 0, result.stdout[-2000:]
    shown = [line for line in lines(result.stdout).split(b"\n") if line.startswith((b"start", b"line", b"after"))]
    wanted = [b"start ***"] + [line for round in range(3) for line in
                               [f"line {round} {i} ***".encode() for i in range(4000)] + [f"after {round} ***".encode()]]
    assert shown == wanted


@pytest.mark.invariant("CR-5")
def test_a_run_whose_output_nobody_reads_any_more_still_succeeds(env):
    """As `taskloom run flow.yaml | head -1`: once the first line is read, nothing reads the output any more; the
    code writes 40 more lines to it and goes on. The run succeeds and says so on its error output."""
    steps = env.root / "steps"
    steps.mkdir()
    source = f'''\
import os, time
from pathlib import Path
t = secret("UAT.token")
os.write(1, f"first {{t}}\\n".encode("utf-8"))
for _ in range(1200):
    if (Path({str(steps)!r}) / "closed").exists():
        break
    time.sleep(0.05)
for i in range(40):
    os.write(1, f"more {{i}} {{t}}\\n".encode("utf-8"))
    time.sleep(0.05)
(Path({str(steps)!r}) / "done").touch()
'''
    process = subprocess.Popen(command(flow_of(env, source)), env=env.env, cwd=env.root, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        first = process.stdout.readline()
        process.stdout.close()
        (steps / "closed").touch()
        said = process.stderr.read()
        exit_code = process.wait(60)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    assert lines(first) == b"first ***\n"
    assert exit_code == 0, said
    assert (steps / "done").exists(), said
    assert env.last_run()[1] == "success", said
    assert secret_shown(said, TOKEN) == [], said


# --- a program the code started, still running as the run ends ---------------------------------------------------

@pytest.mark.invariant("CR-5")
def test_what_a_program_started_writes_just_after_the_run_ends_shows_with_stars(env):
    """The code starts a program that writes the value a second later, and ends without waiting for it: the
    program's line reaches the terminal, with ***."""
    source = '''\
import os, subprocess, sys
child = "import os, time; time.sleep(1); os.write(1, ('late child ' + os.environ['FOR_CHILD'] + '\\\\n').encode())"
subprocess.Popen([sys.executable, "-c", child], env={**os.environ, "FOR_CHILD": secret("UAT.token")})
'''
    result = run_bytes(env, flow_of(env, source))

    assert result.returncode == 0, result.stderr
    assert lines(result.stdout) == b"late child ***\n"


@pytest.mark.invariant("CR-5")
def test_a_program_started_that_goes_on_running_does_not_keep_the_run_going(env):
    """The code starts a program that keeps running for a minute, sharing the run's output: the run still ends
    within seconds of its last block."""
    pid_file = env.root / "child.pid"
    source = f'''\
import subprocess, sys
child = "import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
subprocess.Popen([sys.executable, "-c", child])
'''
    try:
        started = time.monotonic()
        result = run_bytes(env, flow_of(env, source), timeout=40)
        took = time.monotonic() - started
    finally:
        for _ in range(100):
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.05)
        if pid_file.exists() and pid_file.read_text():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except OSError:
                pass

    assert result.returncode == 0, result.stderr
    assert took < 25, took


# --- taskloom secret set and delete: what they say (CR-9) ----------------------------------------------------------

@pytest.mark.invariant("CR-9")
def test_taskloom_secret_set_with_a_value_on_the_command_line_says_how_to_give_it(env, credential_store):
    done = taskloom(env, "secret", "set", "UAT.password", "on-the-Line-5")

    assert done.returncode == 2
    assert done.stdout == ""
    assert done.stderr.splitlines() == [
        "error: taskloom secret set never takes the value on the command line: it asks for it (hidden as you type), "
        "or reads it from standard input with --stdin"]
    assert "UAT.password" not in card_entries(credential_store)


@pytest.mark.invariant("CR-9")
@pytest.mark.parametrize("ending", ["\r\n", "\n"], ids=["CRLF", "LF"])
def test_taskloom_secret_set_from_standard_input_drops_the_line_break_and_says_where_it_saved(env, credential_store,
                                                                                               ending):
    """--stdin: one line break at the end (as Windows or another system types it) is not part of the value."""
    done = taskloom(env, "secret", "set", "UAT.password", "--stdin", stdin="Piped-Value-77" + ending)

    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.splitlines() == ["saved secret 'UAT.password' in the system credential store"]
    assert card_entries(credential_store)["UAT.password"] == "Piped-Value-77"


@pytest.mark.invariant("CR-9")
@pytest.mark.skipif(sys.platform == "win32", reason="getpass reads the Windows console, not standard input")
def test_taskloom_secret_set_asks_for_the_value_naming_its_reference(env, credential_store):
    """With no terminal of its own (a new session), the prompt shows on the error output and the value typed is
    read from standard input."""
    done = taskloom(env, "secret", "set", "UAT.password", stdin="Typed-Value-78\n", start_new_session=True)

    assert done.returncode == 0, done.stderr
    assert "Value for secret 'UAT.password': " in done.stderr, done.stderr
    assert card_entries(credential_store)["UAT.password"] == "Typed-Value-78"


@pytest.mark.invariant("CR-9")
def test_taskloom_secret_delete_says_what_it_deleted(env, credential_store):
    done = taskloom(env, "secret", "delete", "UAT.token")

    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.splitlines() == ["deleted UAT.token and its values"]
    assert "UAT.token" not in card_entries(credential_store)
