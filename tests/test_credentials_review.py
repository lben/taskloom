"""Credentials on the PC (docs/promises/credentials.yaml), the review's two findings: a secret's value of several lines
(a key, as a PEM file holds one) that a block's code writes showing as *** in what `taskloom run` prints and in its Log,
however the writing cuts it (CR-5); and Validate's warning for a secret in every setting a block puts onto a server's
command line (CR-19). Through the real `taskloom run` (started unbuffered, as the editor starts a run) and `taskloom
validate` only, against the tests' own credential store (tests/credential_store, never the real one). Each test names
the rows of the stage's combinations table it covers (E, I2).

What the tests take as given (the stage's design contract, beside those of the other credentials tests):
- A value of several lines shows as *** as a whole, never a line of it, in what a run prints (stdout and stderr: what
  the editor's Log and the terminal show) and in its Log: written by Python Code's print or by Python's own
  (builtins.print), in one call or a line at a time, below print (to file descriptors 1 and 2) in parts that come
  seconds apart, or by sys.stdout or sys.stderr with a flush between its parts. The text before and after it on its
  lines shows as written.
- Output that only starts like such a value (its first lines, then other text; or its first lines and then nothing
  more) shows as written, in order, by the run's end.
- Validate warns, as it does for SSH Command's command, for a secret in every setting that goes onto the server's
  command line: Start Remote Process's Command, Workdir, Log file, PID file and Check after; Stop Remote Process's and
  Health Check's PID file; Remote Extract's Archive and Destination. Never for one that does not: Health Check's URL
  (checked from this computer), a path on a server that Copy Files reaches over SFTP.
"""

import pytest
import yaml

import test_credentials_refs
from test_credentials_refs import write_flow
from test_credentials_sinks import code
from test_credentials_store import plant, run, taskloom, told, write_cards

# The fixtures of the references' tests, used here by name.
in_environment = test_credentials_refs.in_environment
python_environment = test_credentials_refs.python_environment

# A made-up key of four lines (stored without a line break at its end, as `taskloom secret set --stdin` stores a file).
KEY_LINES = ["-----BEGIN TEST KEY-----",
             "TaskloomMadeUpKeyLineOneAbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
             "TaskloomMadeUpKeyLineTwoZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210wxyz",
             "-----END TEST KEY-----"]
KEY = "\n".join(KEY_LINES)
TOKEN = "Rq4 Beacon-t9W"  # UAT.token, for the commands' settings


@pytest.fixture(autouse=True)
def stored(env, credential_store):
    """The cards PEM (key), UAT (token) and appserver (password) listed and their values stored."""
    write_cards(env, {"PEM": {"rows": {"key": None}}, "UAT": {"rows": {"token": None}},
                      "appserver": {"rows": {"password": None}}})
    plant(credential_store, {"PEM.key": KEY, "UAT.token": TOKEN, "appserver.password": "ssh-Pass-4k2"})


def run_unbuffered(env, source: str, in_environment: bool = False):
    """`taskloom run` of a flow whose Python Code block `low` runs `source`, started as the editor starts a run
    (PYTHONUNBUFFERED=1: the bytes under sys.stdout and sys.stderr have no buffer of their own)."""
    env.env["PYTHONUNBUFFERED"] = "1"
    result = run(env, write_flow(env, {"low": code(source, in_environment=in_environment)}))
    assert result.returncode == 0, told(env, result)
    return result


def no_line_of_the_key(env, result):
    """No line of the key shows anywhere: not in what the run printed (stdout, stderr), its Log or its blocks'
    errors."""
    said = told(env, result)
    assert [line for line in KEY_LINES if line in said] == [], said


def runs_of(lines: list[str], *expected: str) -> list[int]:
    """Where in `lines` the `expected` come one right after the other (each line ending with its expected text: a Log
    line printed by the run starts with its time and block)."""
    return [i for i in range(len(lines) - len(expected) + 1)
            if all(lines[i + j].endswith(text) for j, text in enumerate(expected))]


# --- a value of several lines in what a run prints (CR-5) -----------------------------------------------------------

MANY_PRINTS = '''\
import builtins, time
k = secret("PEM.key")
for i in range(50):
    print("log-key:", k)
for i in range(200):
    builtins.print("out-key:", k)
    time.sleep(0.002)
'''


@pytest.mark.invariant("CR-5")
def test_a_value_of_several_lines_printed_many_times_shows_only_as_stars(env):
    """E (the Log, the terminal): Python Code prints the key with text before it, 50 times with its own print (to the
    Log) and 200 times with Python's own (builtins.print, to stdout, as a library prints). Each shows as `log-key: ***`
    and `out-key: ***`, every time, and no line of the key shows anywhere."""
    result = run_unbuffered(env, MANY_PRINTS)

    no_line_of_the_key(env, result)
    assert [line for line in result.stdout.splitlines() if "out-key" in line] == ["out-key: ***"] * 200, result.stdout
    logged = [line for line in result.stderr.splitlines() if "log-key" in line]
    assert len(logged) == 50 and all(line.endswith("log-key: ***") for line in logged), result.stderr


BELOW_PRINT_IN_PARTS = '''\
import os, time
k = secret("PEM.key")
for fd, name in ((1, "fd-out"), (2, "fd-err")):
    for part in f"{name}: {k} {name}-end\\n".splitlines(True):
        os.write(fd, part.encode("utf-8"))
        time.sleep(0.5)
'''


@pytest.mark.invariant("CR-5")
def test_a_value_of_several_lines_written_below_print_a_line_at_a_time_shows_only_as_stars(env, in_environment):
    """E (the Log, both ways of running Python; the terminal): the code writes `fd-out: KEY fd-out-end` to file
    descriptor 1 and `fd-err: KEY fd-err-end` to 2, each a line at a time with half a second between the lines (so
    each comes on its own). What the run prints has each line with *** (in Taskloom's own Python on stdout and stderr
    as written to), and no line of the key shows anywhere."""
    result = run_unbuffered(env, BELOW_PRINT_IN_PARTS, in_environment)

    no_line_of_the_key(env, result)
    for name, stream in (("fd-out", result.stdout), ("fd-err", result.stderr)):
        shown = (result.stdout + result.stderr) if in_environment else stream
        assert runs_of(shown.splitlines(), f"{name}: *** {name}-end"), shown


FLUSHED_BETWEEN = '''\
import sys, time
k = secret("PEM.key")
first, rest = k.split("\\n", 1)
for stream, name in ((sys.stdout, "out"), (sys.stderr, "err")):
    stream.write(f"{name}-key: {first}\\n")
    stream.flush()
    time.sleep(0.5)
    stream.write(f"{rest} {name}-end\\n")
    stream.flush()
'''


@pytest.mark.invariant("CR-5")
def test_a_value_of_several_lines_written_to_sys_stdout_with_a_flush_after_its_first_line_shows_only_as_stars(env):
    """E (the terminal, the editor's Log): the code writes the key's first line (after `out-key: `) to sys.stdout,
    flushes, and half a second later the rest (and ` out-end`); then the same to sys.stderr. stdout shows
    `out-key: *** out-end`, stderr `err-key: *** err-end`, and no line of the key shows anywhere."""
    result = run_unbuffered(env, FLUSHED_BETWEEN)

    no_line_of_the_key(env, result)
    assert "out-key: *** out-end" in result.stdout.splitlines(), result.stdout
    assert "err-key: *** err-end" in result.stderr.splitlines(), result.stderr


LINE_BY_LINE = '''\
import builtins
k = secret("PEM.key")
print("log-start")
for line in k.splitlines():
    print(line)
print("log-end")
builtins.print("out-start")
for line in k.splitlines():
    builtins.print(line)
builtins.print("out-end")
'''


@pytest.mark.invariant("CR-5")
def test_a_value_of_several_lines_printed_a_line_at_a_time_shows_only_as_stars(env):
    """E (the Log, the terminal): the code prints the key a line at a time, with Python Code's print (to the Log)
    between `log-start` and `log-end`, and with Python's own (to stdout) between `out-start` and `out-end`. Between
    each two there is one line, ***, and no line of the key shows anywhere."""
    result = run_unbuffered(env, LINE_BY_LINE)

    no_line_of_the_key(env, result)
    assert runs_of(result.stderr.splitlines(), "log-start", "***", "log-end"), result.stderr
    assert runs_of(result.stdout.splitlines(), "out-start", "***", "out-end"), result.stdout


LOGGED_LINE_BY_LINE = '''\
k = secret("PEM.key")
log("log2-start")
for line in k.splitlines():
    log(line)
log("log2-end")
print("mix-start")
for i, line in enumerate(k.splitlines()):
    (print if i % 2 == 0 else log)(line)
log("mix-end")
'''


@pytest.mark.invariant("CR-5")
def test_a_value_of_several_lines_logged_a_line_at_a_time_or_printed_and_logged_in_turn_shows_only_as_stars(env):
    """E (the Log, the terminal): the code writes the key a line at a time with Python Code's log() between
    `log2-start` and `log2-end`, and then with print and log() in turn (print the first line, log the second, ...)
    between `mix-start` (printed) and `mix-end` (logged). Between each two there is one line, ***, in the order
    written, and no line of the key shows anywhere."""
    result = run_unbuffered(env, LOGGED_LINE_BY_LINE)

    no_line_of_the_key(env, result)
    assert runs_of(result.stderr.splitlines(), "log2-start", "***", "log2-end"), result.stderr
    assert runs_of(result.stderr.splitlines(), "mix-start", "***", "mix-end"), result.stderr


STARTS_LIKE_IT = '''\
import builtins, os, sys, time
k = secret("PEM.key")
first, second = k.split("\\n")[:2]  # the key's first two lines: then other text, or nothing more
for text in (f"out-a {first}", second, "out differs", f"out-tail {first}", second):
    sys.stdout.write(text + "\\n")
    sys.stdout.flush()
    time.sleep(0.5)
for text in (f"log-a {first}", second, "log differs", f"log-tail {first}", second):
    print(text)
for text in (f"fd-a {first}", second, "fd differs", f"fd-tail {first}", second):
    os.write(1, (text + "\\n").encode("utf-8"))
    time.sleep(0.5)
'''


@pytest.mark.invariant("CR-5")
def test_output_that_only_starts_like_a_value_of_several_lines_shows_as_written_in_order_by_the_runs_end(env):
    """E (what a run prints is never lost): the code writes the key's first two lines and then other text, then the
    two lines again as its last output: to sys.stdout (a flush and half a second after each line), with Python Code's
    print (to the Log), and below print to file descriptor 1 (half a second after each line). Each shows as written,
    once, in the order written, by the run's end: what was written last too."""
    result = run_unbuffered(env, STARTS_LIKE_IT)

    first, second = KEY_LINES[:2]
    for name, stream in (("out", result.stdout), ("log", result.stderr), ("fd", result.stdout)):
        lines = stream.splitlines()
        differs = runs_of(lines, f"{name}-a {first}", second, f"{name} differs")
        tail = runs_of(lines, f"{name}-tail {first}", second)
        assert len(differs) == 1 and len(tail) == 1 and differs[0] < tail[0], (name, stream)


# --- Validate: a secret in a setting that goes onto a server's command line (CR-19) --------------------------------

ON_THE_COMMAND_LINE = {  # block: (its type, its settings, the setting holding the secret as Properties names it)
    "start_command": ("servers.start_process", {"command": "./report --token {{secret:UAT.token}}"}, "Command"),
    "start_workdir": ("servers.start_process", {"command": "./report", "workdir": "~/{{secret:UAT.token}}"},
                      "Workdir"),
    "start_log": ("servers.start_process", {"command": "./report", "log_file": "~/logs/{{secret:UAT.token}}.log"},
                  "Log file"),
    "start_pid": ("servers.start_process", {"command": "./report", "pid_file": "~/run/{{secret:UAT.token}}.pid"},
                  "PID file"),
    "start_check": ("servers.start_process", {"command": "./report", "check_after": "{{secret:UAT.token}}"},
                    "Check after"),
    "stop_pid": ("servers.stop_process", {"pid_file": "~/run/{{secret:UAT.token}}.pid"}, "PID file"),
    "health_pid": ("servers.health_check", {"pid_file": "~/run/{{secret:UAT.token}}.pid"}, "PID file"),
    "extract_archive": ("servers.remote_extract", {"archive": "~/in/{{secret:UAT.token}}.tar.gz",
                                                   "destination": "~/app"}, "Archive"),
    "extract_destination": ("servers.remote_extract", {"archive": "~/in/app.tar.gz",
                                                       "destination": "/data/{{secret:UAT.token}}"}, "Destination"),
}
NOT_ON_A_COMMAND_LINE = {  # block: its block (a secret in a setting that never reaches a server's command line)
    "health_url": {"type": "servers.health_check", "config": {"url": "http://127.0.0.1:9/{{secret:UAT.token}}"}},
    "copy_from_server": {"type": "files.copy", "config": {
        "source_server": "appserver", "source": "~/out/{{secret:UAT.token}}.csv", "destination": "copied.csv"}},
}


@pytest.mark.invariant("CR-19")
def test_validate_warns_for_a_secret_in_every_setting_that_goes_onto_a_servers_command_line(env):
    """I2: each block holds the secret in one setting that goes onto the server's command line (Start Remote
    Process's Command, Workdir, Log file, PID file, Check after; Stop Remote Process's and Health Check's PID file;
    Remote Extract's Archive and Destination). Validate takes the flow (exit code 0) and warns for each, on a line
    naming the block and the setting, that other programs on that machine can see a command while it runs. A secret
    in Health Check's URL (checked from this computer) or in a server path Copy Files reaches over SFTP gets no such
    warning."""
    (env.home / "connections.yaml").write_text(yaml.safe_dump({"appserver": {
        "kind": "ssh", "host": "127.0.0.1", "port": 22, "username": "tester",
        "password": "secret:appserver.password"}}), encoding="utf-8")
    blocks = {block: {"type": kind, "config": {"connection": "appserver", **settings}}
              for block, (kind, settings, _) in ON_THE_COMMAND_LINE.items()}
    path = write_flow(env, {**blocks, **NOT_ON_A_COMMAND_LINE})

    checked = taskloom(env, "validate", path)

    warnings = [line for line in checked.stderr.splitlines() if line.startswith("warning:")
                and ("other programs" in line or "can see" in line)]
    assert checked.returncode == 0, checked.stdout + checked.stderr
    unwarned = [f"{block}: {setting}" for block, (_, _, setting) in ON_THE_COMMAND_LINE.items()
                if not [w for w in warnings if f"{block}: {setting}:" in w]]
    assert unwarned == [], checked.stderr
    assert [w for w in warnings if any(f"{block}:" in w for block in NOT_ON_A_COMMAND_LINE)] == [], checked.stderr
