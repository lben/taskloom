"""Credentials on the PC (docs/promises/credentials.yaml), what tests/test_credentials_{store,refs,sinks}.py left open:
Validate naming a missing card or key that a connection a block uses names (CR-8); a secret's value written below
Python's print (straight to the file descriptors, to the bytes under sys.stdout, by a program the code starts)
showing as *** in what `taskloom run` prints and in its Log, and in what Start Remote Process shows of its command's
output (CR-5, CR-19); the scheduler service's "Cannot check server" holding a card's value (CR-5); and what the guide
and the release notes say about secrets stored before this version (CR-7). Through the real `taskloom run`, `taskloom
validate` and `taskloom scheduler --headless` only, against the tests' own credential store (tests/credential_store,
never the real one) and an SSH server on this computer started by the test (never the internet). Each test names the
rows of the stage's combinations table it covers (I3, E, ...).

What the tests take as given (the stage's design contract, beside those of the other credentials tests):
- Validate names a reference to a card or key that does not exist in a connection a block uses (secret:CARD.key as its
  password, token or key) on an error line of its own, `<block>: Connection: ...`, with the connection's name, as a
  run names it before its first block.
- What a block's code, or a program it starts (which shares the run's output), writes by whatever means reaches the
  terminal, the editor and the scheduler service with every secret the run read as ***.
- The release notes are docs/release-notes.md; the version's section is a heading holding 0.42.0. Secrets stored
  before this version are keyring entries of the service taskloom: on Windows the Credential Manager entries named
  taskloom and NAME@taskloom, on macOS the Keychain items of the service taskloom; the docs name them as written
  (`taskloom`, `NAME@taskloom`). The guide writes every reference as CARD.key (a name without a card is described in
  words, if at all).
"""

import re
import subprocess
import sys
import time

import pytest
import yaml

import test_credentials_refs
from conftest import REPO
from sshserver import free_port
from test_credentials_refs import api, lines_with, log_of, write_flow
from test_credentials_sinks import CARDS, TOKEN, VALUES, code, connections, shown, stored_with, terminal
from test_credentials_store import names, plant, run, taskloom, told, write_cards

# The fixtures of the references' tests, used here by name.
in_environment = test_credentials_refs.in_environment
python_environment = test_credentials_refs.python_environment


@pytest.fixture(autouse=True)
def stored(env, credential_store):
    """The output tests' cards listed (UAT: token, four, three, unset; MAIL; SHOP) and their values stored."""
    write_cards(env, CARDS)
    plant(credential_store, VALUES)


# --- Validate: a connection's missing card or key (CR-8) ----------------------------------------------------------

@pytest.mark.invariant("CR-8")
def test_validate_names_a_missing_card_or_key_that_a_connection_a_block_uses_names(env):
    """I3 for connections, A6 (a missing card), A7 (a missing key): the blocks use connections whose token (Bearer),
    key (in the query) or password (SMTP, SSH, JDBC) is secret:CARD.key of a card or key that does not exist. Validate
    names each on its line with the block, its Connection setting and the connection; not the block whose connection's
    value is there."""
    nowhere = f"http://127.0.0.1:{free_port()}"
    connections(env,
                bearer={"kind": "api", "base_url": nowhere, "auth": "Bearer token", "secret": "secret:NOPE.token"},
                keyed={"kind": "api", "base_url": nowhere, "auth": "API key in the query", "key_name": "api_key",
                       "secret": "secret:UAT.nokey"},
                mailer={"kind": "smtp", "host": "127.0.0.1", "port": free_port(), "sender": "taskloom@example.com",
                        "username": "me", "password": "secret:NOMAIL.password"},
                box={"kind": "ssh", "host": "127.0.0.1", "port": free_port(), "username": "me",
                     "password": "secret:UAT.nopass"},
                warehouse={"kind": "jdbc", "driver": "org.h2.Driver", "url": "jdbc:h2:mem:test", "jars": [],
                           "properties": {"user": "sa", "password": "secret:NODB.password"}},
                fine={"kind": "api", "base_url": nowhere, "auth": "Bearer token", "secret": "secret:UAT.token"})
    missing = {  # block: (its connection; the card; the key; True: the card is missing)
        "call": ("bearer", "NOPE", "token", True),
        "keyed": ("keyed", "UAT", "nokey", False),
        "mail": ("mailer", "NOMAIL", "password", True),
        "cmd": ("box", "UAT", "nopass", False),
        "query": ("warehouse", "NODB", "password", True),
    }
    path = write_flow(env, {
        "call": api("/orders", connection="bearer"),
        "keyed": api("/orders", connection="keyed"),
        "mail": {"type": "email.send", "config": {"connection": "mailer", "to": ["team@example.com"],
                                                  "subject": "Codes", "message": "hello"}},
        "cmd": {"type": "servers.ssh_command", "config": {"connection": "box", "command": "echo hello"}},
        "query": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": "select 1"}},
        "good": api("/orders", connection="fine")})

    checked = taskloom(env, "validate", path)

    errors = [line for line in checked.stderr.splitlines() if line.startswith("error:")]
    assert checked.returncode == 2, checked.stdout + checked.stderr
    for block, (connection, card, key, no_card) in missing.items():
        found = [line for line in errors if line.startswith(f"error: {block}: Connection") and connection in line
                 and (card in line if no_card else names(line, card, key))]
        assert found, f"{block} (its connection {connection}: {card}.{key}) not named:\n{checked.stderr}"
    assert [line for line in errors if line.startswith("error: good:")] == [], checked.stderr


# --- written below print (CR-5) -----------------------------------------------------------------------------------

BELOW_PRINT = '''\
import os, subprocess, sys
t = secret("UAT.token")
os.write(1, f"fd-out {t}\\n".encode("utf-8"))
os.write(2, f"fd-err {t}\\n".encode("utf-8"))
sys.stdout.buffer.write(f"buffer-out {t}\\n".encode("utf-8"))
sys.stdout.buffer.flush()
sys.stderr.buffer.write(f"buffer-err {t}\\n".encode("utf-8"))
sys.stderr.buffer.flush()
child = ("import os, sys\\n"
         "print('child-out', os.environ['FOR_CHILD'], flush=True)\\n"
         "print('child-err', os.environ['FOR_CHILD'], file=sys.stderr, flush=True)\\n")
subprocess.run([sys.executable, "-c", child], env={**os.environ, "FOR_CHILD": t}, check=True)
'''


@pytest.mark.invariant("CR-5")
def test_what_code_writes_below_print_or_a_program_it_starts_prints_shows_as_stars(env, in_environment):
    """E (the Log, also what a block prints, both ways of running Python; the terminal; stored runs): the code writes
    the value straight to file descriptors 1 and 2, to the bytes under sys.stdout and sys.stderr, and starts a program
    that inherits the output and prints it to both. What `taskloom run` prints has each line with ***, and neither it,
    the Log, the blocks' errors nor any file the run keeps holds the value."""
    path = write_flow(env, {"low": code(BELOW_PRINT, in_environment=in_environment)})

    result = run(env, path)

    said = told(env, result)
    assert result.returncode == 0, said
    assert shown(said, TOKEN) == [], said
    for line in ("fd-out ***", "fd-err ***", "buffer-out ***", "buffer-err ***", "child-out ***", "child-err ***"):
        assert line in terminal(result), terminal(result)
    assert stored_with(env, TOKEN) == []


@pytest.mark.invariant("CR-19")
@pytest.mark.invariant("CR-5")
@pytest.mark.skipif(sys.platform == "win32", reason="the test SSH server runs commands in /bin/sh")
def test_what_start_remote_process_shows_of_its_commands_output_shows_the_secret_as_stars(env, server):
    """I2 (the run), E (the Log, block errors): Start Remote Process's command prints the value and exits with code 3
    at once; the block fails with the last lines of the command's log file, and its Log shows them with ***."""
    path = write_flow(env, {"start": {"type": "servers.start_process", "config": {
        "connection": "appserver", "command": "echo token={{secret:UAT.token}}; exit 3", "log_file": "~/app/app.log",
        "pid_file": "~/app/app.pid", "check_after": 1}}})

    result = run(env, path)

    said = told(env, result)
    assert result.returncode != 0, said
    assert f"token={TOKEN}" in (server.home_path / "app" / "app.log").read_text(encoding="utf-8")
    assert shown(said, TOKEN) == [], said
    assert "token=***" in log_of(env, "start"), log_of(env, "start")


# --- the scheduler service's "Cannot check server" (CR-5) ---------------------------------------------------------

@pytest.mark.invariant("CR-5")
def test_the_schedulers_cannot_check_server_shows_a_cards_value_as_stars(env, credential_store):
    """E (the scheduler's messages): the keeper (Tools > Settings) keeps the scheduler of the server appserver alive,
    an SSH connection whose host is kept in a card (secret:UAT.host, as the window's cards hold a host); nothing
    answers at its port. `taskloom scheduler --headless` says it cannot check the server, with *** where it names the
    address it could not reach."""
    host = "127.0.0.1"
    write_cards(env, {**CARDS, "UAT": {"rows": {**CARDS["UAT"]["rows"], "host": None}}})
    plant(credential_store, {"UAT.host": host})
    connections(env, appserver={"kind": "ssh", "host": "secret:UAT.host", "port": free_port(), "username": "me",
                                "password": "secret:UAT.token"})
    (env.home / "settings.yaml").write_text(yaml.safe_dump(
        {"keeper": [{"connection": "appserver", "command": "~/taskloom/taskloom", "home": "~/.taskloom"}]}),
        encoding="utf-8")
    printed = env.root / "scheduler-output.txt"

    with printed.open("wb") as out:
        service = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"], env=env.env,
                                   cwd=env.root, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while ("Cannot check server" not in printed.read_text(errors="replace") and service.poll() is None
                   and time.monotonic() < deadline):
                time.sleep(0.2)
        finally:
            service.terminate()
            try:
                service.wait(30)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait()

    said = printed.read_text(errors="replace")
    cannot = lines_with(said, "Cannot check server", "appserver")
    assert cannot, said
    assert host not in said and "***" in cannot[0], said


# --- the docs: secrets stored before this version (CR-7) and references written CARD.key --------------------------

GUIDE, NOTES = REPO / "docs" / "guide.md", REPO / "docs" / "release-notes.md"
AS_WRITTEN = re.compile(r"[`\"*]taskloom[`\"*]")  # the name taskloom as written: `taskloom`, "taskloom", **taskloom**
ENTER_AGAIN = re.compile(r"taskloom secret set (?:--stdin )?[\w-]+\.[\w-]+|Tools\W{1,8}Credentials")
CARD_KEY = re.compile(r"[\w-]+\.[\w-]+")


def in_fence(lines: list[str]) -> list[bool]:
    """For each line of a Markdown text, whether it is inside fenced code (a ``` line included)."""
    inside, fenced = [], False
    for line in lines:
        fence = line.lstrip().startswith("```")
        inside.append(fenced or fence)
        fenced = fenced != fence
    return inside


def headings(lines: list[str]) -> list[tuple[int, int]]:
    """(line number, level) of each Markdown heading, outside fenced code (whose # lines are comments)."""
    return [(i, len(m.group(1))) for i, (line, fenced) in enumerate(zip(lines, in_fence(lines)))
            if not fenced and (m := re.match(r"(#+)\s", line))]


def section(text: str, at: str | int) -> str | None:
    """The section of a Markdown text (its heading to the next heading of the same level or higher) whose heading
    matches the pattern `at`, or (`at` a line number) the innermost one holding that line; None if there is none."""
    lines = text.splitlines()
    marks = headings(lines)
    if isinstance(at, int):
        n = max((n for n, (i, _) in enumerate(marks) if i <= at), default=None)
    else:
        n = next((n for n, (i, _) in enumerate(marks) if re.search(at, lines[i])), None)
    if n is None:
        return None
    start, level = marks[n]
    end = next((i for i, other in marks[n + 1:] if other <= level), len(lines))
    return "\n".join(lines[start:end])


def code_parts(text: str) -> tuple[list[str], list[str]]:
    """The lines of fenced code; and the inline code spans of the other lines (a span may go on to the next line)."""
    lines = text.splitlines()
    fenced = [line for line, inside in zip(lines, in_fence(lines)) if inside]
    prose = "\n".join("" if inside else line for line, inside in zip(lines, in_fence(lines)))
    return fenced, [span for part in prose.split("\n\n") for span in re.findall(r"`([^`]+)`", part)]


def left_out(text: str) -> list[str]:
    """What `text` leaves out of how to enter secrets stored before this version again, and remove their entries."""
    paragraphs = re.split(r"\n\s*\n", text)
    gaps = []
    if not ENTER_AGAIN.search(text):
        gaps.append("enter each again as a card's row: taskloom secret set CARD.key, or Tools > Credentials")
    if not any("Credential Manager" in p and "@taskloom" in p and AS_WRITTEN.search(p) for p in paragraphs):
        gaps.append("Windows: remove the Credential Manager entries `taskloom` and `NAME@taskloom`")
    if not any("Keychain" in p and AS_WRITTEN.search(p) for p in paragraphs):
        gaps.append("macOS: remove the Keychain items of the service `taskloom`")
    return gaps


@pytest.mark.invariant("CR-7")
def test_the_release_notes_of_0_42_0_say_how_to_enter_earlier_secrets_again_and_remove_the_old_entries():
    """The release notes' section 0.42.0 says how to enter a secret stored before as a card's row (taskloom secret set
    CARD.key, or Tools > Credentials) and how to remove the old entries: on Windows the Credential Manager entries
    taskloom and NAME@taskloom, on macOS the Keychain items of the service taskloom."""
    assert NOTES.exists(), f"{NOTES.relative_to(REPO)} is missing"
    part = section(NOTES.read_text(encoding="utf-8"), r"0\.42\.0")

    assert part is not None, f"{NOTES.relative_to(REPO)} has no section 0.42.0"
    assert left_out(part) == []


@pytest.mark.invariant("CR-7")
def test_the_guide_says_how_to_enter_earlier_secrets_again_and_remove_the_old_entries():
    """The guide's section that names the old Windows entries (NAME@taskloom) says the same as the release notes."""
    text = GUIDE.read_text(encoding="utf-8")
    where = next((i for i, line in enumerate(text.splitlines()) if "@taskloom" in line), None)

    assert where is not None, "the guide never names the old Windows entries NAME@taskloom"
    assert left_out(section(text, where) or text) == []


def secret_names(text: str) -> list[str]:
    """Every name the text gives a stored secret: secret:NAME (a whole field, or inside {{ }}), secret("NAME") in
    Python, `taskloom secret set NAME`, and a Stored secret setting (secret: NAME) in a flow's YAML."""
    found = re.findall(r"secret:([^\s`\"'})\]>,|]+)", text)
    found += re.findall(r"""secret\(\s*["']([^"']*)["']\s*\)""", text)
    fenced, spans = code_parts(text)
    for code_text in fenced + spans:
        found += re.findall(r"taskloom secret set\s+(?:--stdin\s+)?([^\s`]+)", code_text)
    for line in fenced:
        found += [n for n in re.findall(r"""(?:^|[\s{,])secret:\s+["']?([^\s"',}]+)""", line)
                  if not n.startswith("secret:")]  # (secret: "secret:NAME", a connection's: read above)
    names_given = [n.rstrip(".,;:") for n in found]
    return [n for n in names_given if n.strip(".…")]  # (not {{secret:...}}, which names none)


@pytest.mark.invariant("CR-7")
@pytest.mark.invariant("CR-4")
def test_every_reference_in_the_guide_is_written_card_dot_key():
    """Every secret:, {{secret:}}, secret("...") and Stored secret the guide's examples and text give, and every name
    its `taskloom secret set` commands take, is CARD.key: a name without a card is refused since this version."""
    given = secret_names(GUIDE.read_text(encoding="utf-8"))

    wrong = sorted({n for n in given if not CARD_KEY.fullmatch(n)})
    assert given, "the guide gives no stored secret's name"
    assert wrong == [], f"names without a card: {wrong}"
