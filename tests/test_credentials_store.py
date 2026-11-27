"""Credentials on the PC (docs/promises/credentials.yaml): the list of cards and keys, the credential store, how a
reference is followed, `taskloom secret list | set | delete`, and the whole-field reference secret:CARD.key where
secret:NAME works today (a Headers row's value, an API connection's stored secret). Through the real `taskloom` program
only, as a terminal starts it, against the tests' stand-in credential store (tests/credential_store: a JSON file of the
test's own, never the real store, but for the one test of Windows' real store, on Windows CI) and a web server on this
computer started by each test, which records what each request carried (never the internet). Each test names the
rows of the stage's combinations table it covers (A1, B4, ...).

What the tests take as given (the stage's design contract):
- The list file is <Taskloom home>/credentials.yaml, {cards: {CARD: {uses: OTHER (optional), rows: {key: null (its
  value is in the store) or "CARD.key" (a pointer at that row)}}}}, cards and rows in the order written; it never
  holds a value.
- The store: keyring service taskloom-cards; a value of up to 500 characters is the one entry CARD.key, as listed; a
  longer one is in parts of at most 500 characters, all under that service, named as the implementation likes. Entries
  under the service taskloom (before this version) are never read.
- `taskloom secret set CARD.key` takes the value from a hidden prompt (with no terminal of its own, getpass reads it
  from standard input), with --stdin from standard input, read as UTF-8, one trailing newline removed; a value on the
  command line is refused with exit code 2 and a message naming --stdin. `taskloom secret delete CARD.key | CARD`
  exits 1 for something missing or used by another card or row. `taskloom secret list` prints a pointer as
  `key -> CARD.key` and a listed key with no stored value with "no value stored" on its line.
- Errors: "The credential store cannot be used: <reason>" (macOS and Windows), "Circle in credentials: A -> B -> A",
  and a reference with no dot is an error naming CARD.key.
- A value a run used shows in its Log as *** (here: printed by a Python Code block that is given the server's answer,
  which repeats what the request carried).
"""

import base64
import contextlib
import http.server
import json
import locale
import os
import re
import stat
import subprocess
import sys
import threading
import uuid
from types import SimpleNamespace

import pytest
import yaml

SERVICE = "taskloom-cards"
ON_A_PC = sys.platform in ("darwin", "win32")  # (CR-13: there the store or nothing; elsewhere secrets.yaml until stage 6)
PROXY_VARIABLES = {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy"}
NO_STORE = "keyring.backends.fail.Keyring"
CANNOT = "The credential store cannot be used"
LONG = ("Ab9-é𝄞" * 1000)[:5000]  # 5,000 characters, some outside the basic plane (two UTF-16 units each)


# --- the program, the list file and the store ----------------------------------------------------------------------

def taskloom(env, *args, stdin: str | None = None, timeout: float = 120, **kw) -> subprocess.CompletedProcess:
    """`taskloom ARGS` as a terminal starts it; `stdin`: what is piped or typed into it, sent as UTF-8 bytes with its
    line breaks as written. What it prints is read as text in this system's encoding, as a terminal shows it."""
    env.env = {k: v for k, v in env.env.items() if k.lower() not in PROXY_VARIABLES}  # (straight to the test's server)
    done = subprocess.run([sys.executable, "-m", "taskloom.cli", *map(str, args)], env=env.env, cwd=env.root,
                          capture_output=True, timeout=timeout,
                          **({"input": stdin.encode("utf-8")} if stdin is not None else {"stdin": subprocess.DEVNULL}),
                          **kw)
    shown = locale.getpreferredencoding(False)
    return subprocess.CompletedProcess(done.args, done.returncode, done.stdout.decode(shown, "replace"),
                                       done.stderr.decode(shown, "replace"))


def secret_set(env, reference: str, value: str) -> subprocess.CompletedProcess:
    """`taskloom secret set REFERENCE --stdin`, with the value and a line break piped in."""
    return taskloom(env, "secret", "set", reference, "--stdin", stdin=value + "\n")


def write_cards(env, cards: dict):
    (env.home / "credentials.yaml").write_text(yaml.safe_dump({"cards": cards}, sort_keys=False, allow_unicode=True),
                                               encoding="utf-8")


def listed(env) -> list[tuple]:
    """The list file's cards, in order, each as (name, the card it takes from or None, [(key, None or pointer)])."""
    cards = yaml.safe_load((env.home / "credentials.yaml").read_text(encoding="utf-8"))["cards"]
    return [(name, card.get("uses"), list((card.get("rows") or {}).items())) for name, card in cards.items()]


def entries(store) -> dict:
    """Every entry of the store, {"service/username": value}."""
    return json.loads(store.read_text(encoding="utf-8")) if store.exists() else {}


def card_entries(store) -> dict:
    """The store's entries under the service taskloom-cards, by user name."""
    return {k[len(SERVICE) + 1:]: v for k, v in entries(store).items() if k.upper().startswith(SERVICE.upper() + "/")}


def plant(store, values: dict, service: str = SERVICE):
    """Values put in the store as Taskloom stores a value of up to 500 characters: {"CARD.key": value}."""
    data = entries(store)
    data.update({f"{service}/{name}": value for name, value in values.items()})
    store.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# --- a web server, flows that send a stored secret, and what a run told -------------------------------------------

@pytest.fixture
def web():
    """A web server on this computer. It records each request's headers (lower-case names) and, for Basic auth, the
    password, decoded; its answer, as text, repeats the X- headers and that password, one `name=value` a line."""
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got = {k.lower(): v for k, v in self.headers.items()}
            if got.get("authorization", "").startswith("Basic "):
                got["basic-password"] = base64.b64decode(got["authorization"][6:]).decode("utf-8").partition(":")[2]
            seen.append(got)
            body = "\n".join(f"{k}={v}" for k, v in got.items() if k.startswith("x-") or k == "basic-password")
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(base=f"http://127.0.0.1:{server.server_port}", seen=seen)
    finally:
        server.shutdown()
        server.server_close()


def flow(env, web, headers=(), connection: str | None = None, name: str = "flow"):
    """A flow whose API Request `call` sends the Headers rows (name, value) to the web server (through the API
    connection `connection`, if given), and whose Python Code `show` prints the answer."""
    config = {"url": "/echo" if connection else f"{web.base}/echo"}
    if headers:
        config["headers"] = [{"key": k, "value": v} for k, v in headers]
    if connection:
        config["connection"] = connection
    blocks = {"call": {"type": "servers.api_request", "config": config},
              "show": {"type": "logic.python", "config": {"inputs": ["response"], "outputs": [],
                                                          "code": "print(response)"}}}
    path = env.root / f"{name}.yaml"
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": ["call.response -> show.response"]}, sort_keys=False,
                                   allow_unicode=True), encoding="utf-8")
    return path


def basic_connection(env, web, reference: str):
    """The API connection `shop`: the web server, with Basic auth, user name me, its password the stored secret."""
    (env.home / "connections.yaml").write_text(yaml.safe_dump({"shop": {
        "kind": "api", "base_url": web.base, "auth": "Basic", "username": "me", "secret": f"secret:{reference}"}},
        allow_unicode=True), encoding="utf-8")


def run(env, path, timeout: float = 120) -> subprocess.CompletedProcess:
    return taskloom(env, "run", path, timeout=timeout)


def told(env, result) -> str:
    """Everything a command told: what it printed and, once runs are recorded, every run's Log and block errors."""
    text = [result.stdout, result.stderr]
    if (env.home / "history.db").exists():
        text += [m or "" for (m,) in env.query("SELECT message FROM logs")]
        text += [f"{e or ''} {s or ''}" for e, s in env.query("SELECT error, summary FROM blocks")]
    return "\n".join(text)


def printed(env) -> str:
    """What the Python Code block `show` printed in the last run, as its Log has it."""
    [(run_id,)] = env.query("SELECT id FROM runs ORDER BY id DESC LIMIT 1")
    return "\n".join(m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block='show'", run_id))


def sent(env, web, result, values: dict):
    """The run succeeded, the request carried each of `values` ({header or basic-password: value}), and none of them
    shows: not printed, not in any Log or block error; the Log shows *** where the block printed them."""
    assert result.returncode == 0, told(env, result)
    got = web.seen[-1]
    assert {k: got.get(k) for k in values} == values
    for value in values.values():
        assert value not in told(env, result)
    assert "***" in printed(env), printed(env)


def names(text: str, card: str, key: str) -> bool:
    """The text names the row CARD.key: so written, or the card and the key on one line."""
    return f"{card}.{key}" in text or any(card in line and key in line for line in text.splitlines())


def refused_cleanly(env, result, *wanted: str):
    """The command failed without a crash, and what it told has each of `wanted`."""
    said = told(env, result)
    assert result.returncode != 0, said
    assert "Traceback" not in said, said
    for text in wanted:
        assert text in said, said


# --- set, list and delete (F1-F4) -------------------------------------------------------------------------------

@pytest.mark.invariant("CR-9")
@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-5")
@pytest.mark.invariant("CR-7")
def test_a_value_set_from_standard_input_is_stored_under_its_card_and_key_and_a_run_sends_it(env, web,
                                                                                            credential_store):
    """F2 (--stdin), F4 (a card that does not exist is made), A1 (a direct value, whole field): the store holds the
    value as CARD.key, the list file only the names, and the run's Log shows ***."""
    password, host = 'uat-Pa55 "w" #1', "uat-host-internal-7"

    first = secret_set(env, "UAT.password", password)
    second = secret_set(env, "UAT.host", host)
    result = run(env, flow(env, web, [("X-Pass", "secret:UAT.password")]))

    for done, value in ((first, password), (second, host)):
        assert done.returncode == 0, done.stderr
        assert value not in done.stdout + done.stderr
    assert listed(env) == [("UAT", None, [("password", None), ("host", None)])]
    assert card_entries(credential_store) == {"UAT.password": password, "UAT.host": host}
    text = (env.home / "credentials.yaml").read_bytes()
    assert password.encode() not in text and host.encode() not in text
    sent(env, web, result, {"x-pass": password})


@pytest.mark.invariant("CR-9")
@pytest.mark.skipif(sys.platform == "win32", reason="getpass reads the Windows console, not standard input")
def test_secret_set_takes_the_value_from_a_hidden_prompt(env, credential_store):
    """F2 (the prompt): with no terminal of its own (a new session), getpass reads what is typed from standard input."""
    value = "Typed-at-the-prompt-3"

    done = taskloom(env, "secret", "set", "UAT.password", stdin=value + "\n", start_new_session=True)

    assert done.returncode == 0, done.stderr
    assert value not in done.stdout + done.stderr
    assert card_entries(credential_store) == {"UAT.password": value}
    assert listed(env) == [("UAT", None, [("password", None)])]


@pytest.mark.invariant("CR-9")
@pytest.mark.invariant("CR-5")
def test_a_value_on_the_command_line_is_refused_and_says_to_use_the_prompt_or_stdin(env, credential_store):
    """F2: `taskloom secret set CARD.key VALUE` stores nothing and does not repeat the value."""
    value = "on-the-Command-line-4"

    done = taskloom(env, "secret", "set", "UAT.password", value)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "--stdin" in done.stderr, done.stderr
    assert value not in done.stdout + done.stderr
    assert entries(credential_store) == {}
    assert not (env.home / "credentials.yaml").exists() or listed(env) == []


@pytest.mark.invariant("CR-9")
def test_setting_a_value_keeps_the_rest_of_the_list_as_written(env, credential_store):
    """F2, F4: a new row goes at the end of its card; the other cards, rows, pointers and the card another takes
    from stay as written, in their order."""
    write_cards(env, {"UAT": {"rows": {"host": None, "password": None}},
                      "DEFAULT": {"uses": "UAT", "rows": {"archive": "PROD.password", "username": None}},
                      "PROD": {"rows": {"password": None}}})
    plant(credential_store, {"UAT.host": "uat-host-1", "UAT.password": "uat-pass-1", "DEFAULT.username": "def-user-1",
                             "PROD.password": "prod-pass-1"})

    added = secret_set(env, "PROD.user", "prod-user-2")
    changed = secret_set(env, "UAT.host", "uat-host-2")

    assert added.returncode == 0 and changed.returncode == 0, added.stderr + changed.stderr
    assert listed(env) == [("UAT", None, [("host", None), ("password", None)]),
                           ("DEFAULT", "UAT", [("archive", "PROD.password"), ("username", None)]),
                           ("PROD", None, [("password", None), ("user", None)])]
    assert card_entries(credential_store) == {"UAT.host": "uat-host-2", "UAT.password": "uat-pass-1",
                                              "DEFAULT.username": "def-user-1", "PROD.password": "prod-pass-1",
                                              "PROD.user": "prod-user-2"}


@pytest.mark.invariant("CR-9")
@pytest.mark.invariant("CR-15")
def test_secret_list_shows_cards_keys_what_a_card_takes_from_and_pointers_never_a_value(env, credential_store):
    """F1, D1 (the list part): a listed key with no stored value says "no value stored" on its line."""
    write_cards(env, {"UAT": {"rows": {"host": None, "password": None, "token": None}},
                      "PROD": {"rows": {"password": None}},
                      "DEFAULT": {"uses": "UAT", "rows": {"username": None, "archive_password": "PROD.password"}}})
    values = {"UAT.host": "uat-host-5", "UAT.password": "uat-pass-5", "PROD.password": "prod-pass-5",
              "DEFAULT.username": "default-user-5"}
    plant(credential_store, values)

    done = taskloom(env, "secret", "list")

    out = done.stdout
    assert done.returncode == 0, done.stderr
    for word in ("UAT", "PROD", "DEFAULT", "host", "password", "token", "username"):
        assert word in out, out
    assert "archive_password -> PROD.password" in out, out
    assert re.search(r"(uses|takes[\w ]* from)\W+UAT", out), out
    missing = [line for line in out.splitlines() if "no value stored" in line]
    assert missing and all("token" in line for line in missing), out
    for value in values.values():
        assert value not in out + done.stderr


@pytest.mark.invariant("CR-9")
@pytest.mark.invariant("CR-12")
def test_secret_delete_removes_a_row_or_a_whole_card_from_the_list_and_from_the_store(env, credential_store):
    """F3, CR-12: deleting a row removes its value from the store; deleting a card removes every one of its values."""
    write_cards(env, {"UAT": {"rows": {"host": None, "password": None}},
                      "PROD": {"rows": {"user": None, "password": None}}})
    values = {"UAT.host": "uat-host-6", "UAT.password": "uat-pass-6", "PROD.user": "prod-user-6",
              "PROD.password": "prod-pass-6"}
    plant(credential_store, values)

    row = taskloom(env, "secret", "delete", "UAT.password")
    card = taskloom(env, "secret", "delete", "PROD")

    assert row.returncode == 0 and card.returncode == 0, row.stderr + card.stderr
    assert listed(env) == [("UAT", None, [("host", None)])]
    assert card_entries(credential_store) == {"UAT.host": "uat-host-6"}
    for value in values.values():
        assert value not in row.stdout + row.stderr + card.stdout + card.stderr


@pytest.mark.invariant("CR-9")
def test_deleting_a_card_or_key_that_does_not_exist_fails_with_exit_code_1_and_names_it(env, credential_store):
    """F3: something that does not exist: a clear message, exit code 1; nothing else changes."""
    write_cards(env, {"UAT": {"rows": {"password": None}}})
    plant(credential_store, {"UAT.password": "uat-pass-7"})

    no_card = taskloom(env, "secret", "delete", "NOPE")
    no_key = taskloom(env, "secret", "delete", "UAT.nothere")
    neither = taskloom(env, "secret", "delete", "NOPE.password")

    for done, name in ((no_card, "NOPE"), (no_key, "nothere"), (neither, "NOPE")):
        assert done.returncode == 1, done.stdout + done.stderr
        assert name in done.stderr and "Traceback" not in done.stderr, done.stderr
    assert listed(env) == [("UAT", None, [("password", None)])]
    assert card_entries(credential_store) == {"UAT.password": "uat-pass-7"}


@pytest.mark.invariant("CR-3")
@pytest.mark.invariant("CR-9")
def test_deleting_a_card_another_takes_from_or_a_row_a_row_points_at_is_refused_naming_who(env, credential_store):
    """B2: refused, exit code 1, naming the card that uses it; once nothing uses them, they can be deleted."""
    cards = {"UAT": {"rows": {"password": None}}, "PROD": {"rows": {"password": None}},
             "DEFAULT": {"uses": "UAT", "rows": {"archive_password": "PROD.password"}}}
    write_cards(env, cards)
    plant(credential_store, {"UAT.password": "uat-pass-8", "PROD.password": "prod-pass-8"})
    before = listed(env)

    used_card = taskloom(env, "secret", "delete", "UAT")
    pointed_at = taskloom(env, "secret", "delete", "PROD.password")

    for done in (used_card, pointed_at):
        assert done.returncode == 1, done.stdout + done.stderr
        assert "DEFAULT" in done.stderr and "Traceback" not in done.stderr, done.stderr
    assert listed(env) == before
    assert card_entries(credential_store) == {"UAT.password": "uat-pass-8", "PROD.password": "prod-pass-8"}

    user = taskloom(env, "secret", "delete", "DEFAULT")
    then = [taskloom(env, "secret", "delete", name) for name in ("UAT", "PROD.password")]

    assert [d.returncode for d in (user, *then)] == [0, 0, 0], [d.stderr for d in (user, *then)]
    assert card_entries(credential_store) == {}


# --- names (B4, B5) -------------------------------------------------------------------------------------------------

@pytest.mark.invariant("CR-14")
@pytest.mark.parametrize("reference, why", [
    ("UAT.pass.word", r"dot|['\"‘’“”]\.['\"‘’“”]"),  # (the dot separates the card and the key)
    ("U.AT.password", r"dot|['\"‘’“”]\.['\"‘’“”]"),
    ("U AT.password", r"space"),
    ("UAT.pass word", r"space"),
    ("UAT.", None),
    (".password", None),
    ("UAT", None),
], ids=["dot in the key", "dot in the card", "space in the card", "space in the key", "no key", "no card", "no dot"])
def test_a_name_with_a_dot_or_a_space_or_empty_is_refused_saying_why(env, credential_store, reference, why):
    """B4: refused with a message saying why; nothing is stored or listed. A good name works (for comparison)."""
    good = secret_set(env, "UAT.password", "uat-pass-9")
    assert good.returncode == 0, good.stderr

    done = secret_set(env, reference, "refused-Value-9")

    assert done.returncode != 0, done.stdout + done.stderr
    assert "Traceback" not in done.stderr and "refused-Value-9" not in done.stdout + done.stderr, done.stderr
    if why:
        assert re.search(why, done.stderr, re.I), done.stderr
    assert listed(env) == [("UAT", None, [("password", None)])]
    assert card_entries(credential_store) == {"UAT.password": "uat-pass-9"}


@pytest.mark.invariant("CR-14")
def test_names_of_one_character_of_100_characters_of_other_alphabets_digits_underscores_and_dashes_work(env, web,
                                                                                                     credential_store):
    """B4 (allowed): each is stored under its name, listed, and found by a run."""
    long_card, long_key = "C" * 100, "k" * 100
    values = {"A.b": "one-char-10", f"{long_card}.{long_key}": "hundred-chars-10", "Zürich.Größe": "zurich-10",
              "東京.пароль": "tokyo-10", "uat_2-x.key_1-B": "digits-10"}

    done = [secret_set(env, name, value) for name, value in values.items()]
    shown = taskloom(env, "secret", "list")
    result = run(env, flow(env, web, [("X-Long", f"secret:{long_card}.{long_key}"), ("X-Tokyo", "secret:東京.пароль"),
                                      ("X-Mixed", "secret:uat_2-x.key_1-B")]))

    assert [d.returncode for d in done] == [0] * len(values), [d.stderr for d in done]
    assert card_entries(credential_store) == values
    assert [(card, uses, rows) for card, uses, rows in listed(env)] == [
        (n.partition(".")[0], None, [(n.partition(".")[2], None)]) for n in values]
    for word in ("A", "b", long_card, long_key, "Zürich", "Größe", "uat_2-x", "key_1-B"):  # (as a console shows them)
        assert word in shown.stdout, shown.stdout
    sent(env, web, result, {"x-long": "hundred-chars-10", "x-tokyo": "tokyo-10", "x-mixed": "digits-10"})


@pytest.mark.invariant("CR-14")
@pytest.mark.invariant("CR-4")
def test_a_reference_written_in_other_case_finds_the_row_as_listed(env, web, credential_store):
    """B5: uat.PASSWORD and default.Host find UAT.password and (through DEFAULT's card) UAT.host, in a store whose
    names are not alike in other case (as macOS's)."""
    write_cards(env, {"UAT": {"rows": {"password": None, "host": None}}, "DEFAULT": {"uses": "UAT", "rows": {}}})
    plant(credential_store, {"UAT.password": "uat-pass-11", "UAT.host": "uat-host-11"})

    result = run(env, flow(env, web, [("X-Pass", "secret:uat.PASSWORD"), ("X-Host", "secret:default.Host")]))

    sent(env, web, result, {"x-pass": "uat-pass-11", "x-host": "uat-host-11"})


@pytest.mark.invariant("CR-14")
@pytest.mark.invariant("CR-9")
def test_setting_a_name_written_in_other_case_changes_the_listed_row_and_makes_no_second_one(env, web,
                                                                                            credential_store):
    """B4 (UAT then uat): the second is the same card and row, kept as first written."""
    first = secret_set(env, "UAT.password", "first-Value-12")
    second = secret_set(env, "uat.PASSWORD", "second-Value-12")
    result = run(env, flow(env, web, [("X-Pass", "secret:UAT.password")]))

    assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
    assert listed(env) == [("UAT", None, [("password", None)])]
    assert card_entries(credential_store) == {"UAT.password": "second-Value-12"}
    sent(env, web, result, {"x-pass": "second-Value-12"})


# --- following a reference (A2-A7, B1, K4) ---------------------------------------------------------------------------

@pytest.mark.invariant("CR-3")
@pytest.mark.invariant("CR-4")
def test_a_card_takes_its_values_from_another_through_a_chain_and_its_own_row_wins(env, web, credential_store):
    """A2 (a card that takes from another), A4 (a chain of two), B1 (its own row for the same key wins)."""
    write_cards(env, {"UAT": {"rows": {"host": None, "password": None}},
                      "MID": {"uses": "UAT", "rows": {}},
                      "DEFAULT": {"uses": "MID", "rows": {"password": None}}})
    plant(credential_store, {"UAT.host": "uat-host-13", "UAT.password": "uat-pass-13",
                             "DEFAULT.password": "own-pass-13"})

    result = run(env, flow(env, web, [("X-Host", "secret:DEFAULT.host"), ("X-Own", "secret:DEFAULT.password"),
                                      ("X-Mid", "secret:MID.password")]))

    sent(env, web, result, {"x-host": "uat-host-13", "x-own": "own-pass-13", "x-mid": "uat-pass-13"})


@pytest.mark.invariant("CR-3")
@pytest.mark.invariant("CR-4")
def test_a_row_that_points_at_a_row_is_followed_to_its_end(env, web, credential_store):
    """A3 (a row pointing at a row), A4 (a chain of two pointers; a pointer at a card that takes from another)."""
    write_cards(env, {"PROD": {"rows": {"password": None, "token": None}},
                      "UAT": {"rows": {"password": None}},
                      "MID": {"rows": {"archive": "PROD.token"}},
                      "SUB": {"uses": "UAT", "rows": {}},
                      "DEFAULT": {"rows": {"direct": "PROD.password", "chained": "MID.archive",
                                           "through": "SUB.password"}}})
    plant(credential_store, {"PROD.password": "prod-pass-14", "PROD.token": "prod-token-14",
                             "UAT.password": "uat-pass-14"})

    result = run(env, flow(env, web, [("X-Direct", "secret:DEFAULT.direct"), ("X-Chained", "secret:DEFAULT.chained"),
                                      ("X-Through", "secret:DEFAULT.through")]))

    sent(env, web, result, {"x-direct": "prod-pass-14", "x-chained": "prod-token-14", "x-through": "uat-pass-14"})


@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-3")
def test_switching_the_card_default_takes_from_makes_the_next_run_use_its_values(env, web, credential_store):
    """K4: DEFAULT takes from UAT, then from PROD; the same flow sends UAT's value, then PROD's."""
    plant(credential_store, {"UAT.password": "uat-pass-15", "PROD.password": "prod-pass-15"})
    cards = {"UAT": {"rows": {"password": None}}, "PROD": {"rows": {"password": None}}}
    path = flow(env, web, [("X-Pass", "secret:DEFAULT.password")])

    write_cards(env, {**cards, "DEFAULT": {"uses": "UAT", "rows": {}}})
    on_uat = run(env, path)
    sent(env, web, on_uat, {"x-pass": "uat-pass-15"})
    write_cards(env, {**cards, "DEFAULT": {"uses": "PROD", "rows": {}}})
    on_prod = run(env, path)
    sent(env, web, on_prod, {"x-pass": "prod-pass-15"})


@pytest.mark.invariant("CR-3")
def test_a_circle_in_stored_data_stops_the_run_naming_it_and_never_hangs(env, web, credential_store):
    """A5 (in stored data): A takes from B and B from A; C.x points at D.y and D.y at C.x. The run stops, names the
    circle, sends nothing, within a minute. A good reference works (for comparison)."""
    write_cards(env, {"UAT": {"rows": {"password": None}},
                      "A": {"uses": "B", "rows": {}}, "B": {"uses": "A", "rows": {}},
                      "C": {"rows": {"x": "D.y"}}, "D": {"rows": {"y": "C.x"}}})
    plant(credential_store, {"UAT.password": "uat-pass-16"})

    good = run(env, flow(env, web, [("X-Pass", "secret:UAT.password")], name="good"))
    sent(env, web, good, {"x-pass": "uat-pass-16"})
    asked = len(web.seen)
    by_cards = run(env, flow(env, web, [("X-Key", "secret:A.key")], name="cards"), timeout=60)
    by_rows = run(env, flow(env, web, [("X-Key", "secret:C.x")], name="rows"), timeout=60)

    refused_cleanly(env, by_cards, "Circle in credentials", "A -> B -> A")
    refused_cleanly(env, by_rows, "Circle in credentials", "C.x", "D.y")
    assert len(web.seen) == asked


@pytest.mark.invariant("CR-8")
def test_a_missing_card_or_key_stops_the_run_naming_it(env, web, credential_store):
    """A6 (a missing card), A7 (a missing key): the run fails naming it and sends nothing. A good reference works (for
    comparison)."""
    write_cards(env, {"UAT": {"rows": {"password": None}}})
    plant(credential_store, {"UAT.password": "uat-pass-17"})

    good = run(env, flow(env, web, [("X-Pass", "secret:UAT.password")], name="good"))
    sent(env, web, good, {"x-pass": "uat-pass-17"})
    asked = len(web.seen)
    no_card = run(env, flow(env, web, [("X-Pass", "secret:NOPE.password")], name="card"))
    no_key = run(env, flow(env, web, [("X-Pass", "secret:UAT.nothere")], name="key"))

    refused_cleanly(env, no_card, "NOPE")
    refused_cleanly(env, no_key)
    assert names(told(env, no_key), "UAT", "nothere"), told(env, no_key)
    assert len(web.seen) == asked


@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-7")
def test_a_reference_without_a_card_is_an_error_naming_card_and_key(env, web, credential_store):
    """References: secret:NAME (no dot) is an error saying a reference needs CARD.key; a value stored under that name
    before this version is not sent. A good reference works (for comparison)."""
    write_cards(env, {"UAT": {"rows": {"password": None}}})
    plant(credential_store, {"UAT.password": "uat-pass-18"})
    plant(credential_store, {"vault_password": "earlier-Value-18"}, service="taskloom")

    good = run(env, flow(env, web, [("X-Pass", "secret:UAT.password")], name="good"))
    sent(env, web, good, {"x-pass": "uat-pass-18"})
    asked = len(web.seen)
    flat = run(env, flow(env, web, [("X-Pass", "secret:vault_password")], name="flat"))

    refused_cleanly(env, flat, "CARD.key")
    assert len(web.seen) == asked
    assert "earlier-Value-18" not in told(env, flat)


# --- the list and the store disagree (D1-D3), earlier secrets (CR-7) -----------------------------------------------

@pytest.mark.invariant("CR-15")
@pytest.mark.invariant("CR-8")
def test_a_listed_key_with_no_stored_value_stops_a_run_that_needs_it_naming_it(env, web, credential_store):
    """D1 (the run part): a run that needs UAT.token, listed with no stored value, stops naming it, without a crash;
    the same card's stored UAT.host works."""
    write_cards(env, {"UAT": {"rows": {"host": None, "token": None}}})
    plant(credential_store, {"UAT.host": "uat-host-19"})

    good = run(env, flow(env, web, [("X-Host", "secret:UAT.host")], name="good"))
    sent(env, web, good, {"x-host": "uat-host-19"})
    asked = len(web.seen)
    missing = run(env, flow(env, web, [("X-Token", "secret:UAT.token")], name="missing"))

    refused_cleanly(env, missing)
    assert names(told(env, missing), "UAT", "token"), told(env, missing)
    assert len(web.seen) == asked


@pytest.mark.invariant("CR-15")
def test_a_stored_value_that_is_not_listed_is_not_found_and_setting_its_row_replaces_it(env, web, credential_store):
    """D2: a value in the store under UAT.password with no card UAT listed: a run stops naming UAT, `secret list`
    does not show it; `secret set UAT.password` lists it and replaces the value."""
    write_cards(env, {"PROD": {"rows": {"password": None}}})
    plant(credential_store, {"PROD.password": "prod-pass-20", "UAT.password": "left-over-20"})
    path = flow(env, web, [("X-Pass", "secret:UAT.password")])

    before = run(env, path)
    shown = taskloom(env, "secret", "list")
    refused_cleanly(env, before, "UAT")
    assert web.seen == []
    assert shown.returncode == 0 and "PROD" in shown.stdout and "UAT" not in shown.stdout, shown.stdout + shown.stderr

    done = secret_set(env, "UAT.password", "new-pass-20")
    after = run(env, path)

    assert done.returncode == 0, done.stderr
    assert card_entries(credential_store) == {"PROD.password": "prod-pass-20", "UAT.password": "new-pass-20"}
    sent(env, web, after, {"x-pass": "new-pass-20"})


DAMAGED = {"bad YAML": b"cards: {UAT: [\n", "cards is not a mapping": b"cards: [UAT, PROD]\n",
           "not UTF-8": b"cards:\n  \xff\xfe\x00UAT: {rows: {password: null}}\n"}


@pytest.mark.invariant("CR-15")
@pytest.mark.parametrize("content", list(DAMAGED.values()), ids=list(DAMAGED))
def test_a_damaged_list_file_is_named_in_a_message_never_a_crash_and_left_as_it_was(env, web, credential_store,
                                                                                    content):
    """D3: `secret list`, `secret set` and a run that needs a value say which file is damaged; nothing crashes,
    nothing is sent, and set changes neither the file nor the store."""
    (env.home / "credentials.yaml").write_bytes(content)
    plant(credential_store, {"UAT.password": "uat-pass-21"})

    shown = taskloom(env, "secret", "list")
    stored = secret_set(env, "UAT.password", "new-pass-21")
    result = run(env, flow(env, web, [("X-Pass", "secret:UAT.password")]))

    for done in (shown, stored, result):
        refused_cleanly(env, done, "credentials.yaml")
    assert web.seen == []
    assert (env.home / "credentials.yaml").read_bytes() == content
    assert card_entries(credential_store) == {"UAT.password": "uat-pass-21"}


@pytest.mark.invariant("CR-7")
def test_a_value_stored_before_this_version_is_never_read_and_left_alone(env, web, credential_store):
    """CR-7: an entry under the service taskloom (an earlier version's), named as the listed UAT.password: a run
    stops naming UAT.password, the earlier value is not sent, and setting the row leaves that entry as it was."""
    write_cards(env, {"UAT": {"rows": {"password": None}}})
    plant(credential_store, {"UAT.password": "earlier-Value-22"}, service="taskloom")
    path = flow(env, web, [("X-Pass", "secret:UAT.password")])

    before = run(env, path)
    refused_cleanly(env, before)
    assert names(told(env, before), "UAT", "password") and "earlier-Value-22" not in told(env, before)
    assert web.seen == []

    done = secret_set(env, "UAT.password", "new-pass-22")
    after = run(env, path)

    assert done.returncode == 0, done.stderr
    sent(env, web, after, {"x-pass": "new-pass-22"})
    assert entries(credential_store) == {"taskloom/UAT.password": "earlier-Value-22",
                                         "taskloom-cards/UAT.password": "new-pass-22"}


# --- values of any length and content (A12, A13) --------------------------------------------------------------------

@pytest.mark.invariant("CR-17")
@pytest.mark.invariant("CR-5")
@pytest.mark.invariant("CR-4")
def test_a_value_with_quotes_a_line_break_and_other_alphabets_comes_back_exactly_and_hidden(env, web,
                                                                                           credential_store):
    """A12: set from standard input, sent as an API connection's Basic auth password (secret:CARD.key), it arrives
    exactly; the Log shows ***."""
    value = 'He said "hi", it\'s a \\ test\nzweite Zeile: Grüße, пароль, 密码 𝄞'
    basic_connection(env, web, "UAT.password")

    done = secret_set(env, "UAT.password", value)
    result = run(env, flow(env, web, connection="shop"))

    assert done.returncode == 0, done.stderr
    assert card_entries(credential_store) == {"UAT.password": value}
    sent(env, web, result, {"basic-password": value})


@pytest.mark.invariant("CR-17")
@pytest.mark.invariant("CR-12")
@pytest.mark.invariant("CR-5")
@pytest.mark.parametrize("length", [500, 501, 5000])
def test_a_long_value_is_stored_in_parts_of_at_most_500_characters_read_back_whole_and_deleted_whole(
        env, web, credential_store, length):
    """A13 (also either side of 500): every entry holds at most 500 characters; a run sends the value whole and the
    Log shows ***; a shorter value set over it comes back exactly; deleting the row leaves no entry behind."""
    value = LONG[:length]
    basic_connection(env, web, "UAT.password")
    path = flow(env, web, connection="shop")

    done = secret_set(env, "UAT.password", value)
    result = run(env, path)

    assert done.returncode == 0, done.stderr
    parts = card_entries(credential_store)
    assert all(len(part) <= 500 for part in parts.values()), {k: len(v) for k, v in parts.items()}
    if length == 500:
        assert parts == {"UAT.password": value}
    else:
        assert len(parts) >= 2, {k: len(v) for k, v in parts.items()}
    sent(env, web, result, {"basic-password": value})

    shorter = secret_set(env, "UAT.password", "short-Value-23")
    again = run(env, path)
    gone = taskloom(env, "secret", "delete", "UAT.password")

    assert shorter.returncode == 0, shorter.stderr
    sent(env, web, again, {"basic-password": "short-Value-23"})
    assert gone.returncode == 0, gone.stderr
    assert card_entries(credential_store) == {}


@pytest.mark.invariant("CR-17")
@pytest.mark.invariant("CR-14")
@pytest.mark.invariant("CR-12")
def test_in_a_store_like_windows_long_values_and_names_in_other_case_work(env, web, credential_store):
    """CR-17 on Windows, in the tests' store: names alike in other case are one entry and an entry over 1,280 UTF-16
    units is refused. A 5,000-character value (10,000 UTF-16 units) is stored and sent whole; set again as
    uat.PASSWORD it replaces the value; deleting it leaves no entry behind."""
    env.env["TEST_CREDENTIAL_STORE_MODE"] = "case-insensitive,size-limit"
    first, second = ("𝄞Ab9é" * 1000), ("é𝄞Zy8" * 1000)
    basic_connection(env, web, "Uat.Password")
    path = flow(env, web, connection="shop")

    stored = secret_set(env, "UAT.password", first)
    sent_first = run(env, path)
    assert stored.returncode == 0, stored.stderr
    sent(env, web, sent_first, {"basic-password": first})

    replaced = secret_set(env, "uat.PASSWORD", second)
    sent_second = run(env, path)
    gone = taskloom(env, "secret", "delete", "UAT.password")

    assert replaced.returncode == 0, replaced.stderr
    sent(env, web, sent_second, {"basic-password": second})
    assert listed(env) == [] or listed(env) == [("UAT", None, [])]
    assert gone.returncode == 0, gone.stderr
    assert card_entries(credential_store) == {}


# --- by operating system (H1-H3) --------------------------------------------------------------------------------------

@pytest.mark.invariant("CR-13")
@pytest.mark.invariant("CR-7")
@pytest.mark.skipif(not ON_A_PC, reason="macOS and Windows only: on Linux the private file stays until stage 6")
def test_on_a_pc_secrets_yaml_is_never_read_or_written(env, web, credential_store):
    """CR-13: with a store that works, a value only in secrets.yaml is not found (the run stops naming it, the file's
    value is not sent), and setting a value leaves secrets.yaml as it was."""
    write_cards(env, {"UAT": {"rows": {"host": None, "password": None}}})
    plant(credential_store, {"UAT.host": "store-host-24"})
    secrets_file = env.home / "secrets.yaml"
    secrets_file.write_text(yaml.safe_dump({"UAT.password": "from-the-File-24", "UAT.host": "file-host-24"}),
                            encoding="utf-8")
    secrets_file.chmod(0o600)
    before = secrets_file.read_bytes()

    good = run(env, flow(env, web, [("X-Host", "secret:UAT.host")], name="good"))
    sent(env, web, good, {"x-host": "store-host-24"})
    asked = len(web.seen)
    only_in_file = run(env, flow(env, web, [("X-Pass", "secret:UAT.password")], name="file"))
    done = secret_set(env, "UAT.password", "new-pass-24")

    refused_cleanly(env, only_in_file)
    assert names(told(env, only_in_file), "UAT", "password"), told(env, only_in_file)
    assert len(web.seen) == asked and "from-the-File-24" not in told(env, only_in_file)
    assert done.returncode == 0, done.stderr
    assert secrets_file.read_bytes() == before


@pytest.mark.invariant("CR-13")
@pytest.mark.skipif(not ON_A_PC, reason="macOS and Windows only: on Linux the private file stays until stage 6")
@pytest.mark.parametrize("unusable", ["unavailable", "no store"])
def test_on_a_pc_a_store_that_cannot_be_used_is_said_and_nothing_is_stored_or_read_from_a_file(env, web,
                                                                                               credential_store,
                                                                                               unusable):
    """H1: the store raises a keyring error (locked), or there is none (keyring's fail backend). set and delete say
    so and change nothing, the list file included; a run says so and sends nothing, not the value in secrets.yaml."""
    write_cards(env, {"UAT": {"rows": {"password": None}}})
    secrets_file = env.home / "secrets.yaml"
    secrets_file.write_text(yaml.safe_dump({"UAT.password": "from-the-File-25"}), encoding="utf-8")
    secrets_file.chmod(0o600)
    files = {p: p.read_bytes() for p in (env.home / "credentials.yaml", secrets_file)}
    if unusable == "unavailable":
        env.env["TEST_CREDENTIAL_STORE_MODE"] = "unavailable"
    else:
        env.env["PYTHON_KEYRING_BACKEND"] = NO_STORE

    stored = secret_set(env, "UAT.password", "new-pass-25")
    added = secret_set(env, "PROD.password", "new-pass-25")
    deleted = taskloom(env, "secret", "delete", "UAT.password")
    result = run(env, flow(env, web, [("X-Pass", "secret:UAT.password")]))

    for done in (stored, added, deleted, result):
        refused_cleanly(env, done, CANNOT)
        assert "new-pass-25" not in told(env, done) and "from-the-File-25" not in told(env, done)
    assert web.seen == []
    assert {p: p.read_bytes() for p in files} == files
    assert entries(credential_store) == {}


@pytest.mark.invariant("CR-13")
@pytest.mark.skipif(sys.platform != "linux", reason="Linux only: on macOS and Windows never a plain file")
def test_on_linux_with_no_store_the_value_goes_to_the_private_file_and_runs_use_it(env, web, credential_store):
    """H2: with no credential store, set writes secrets.yaml (readable only by the user) keyed CARD.key, the list file
    lists the row, a run sends the value and the Log shows ***; delete removes it from the file."""
    env.env["PYTHON_KEYRING_BACKEND"] = NO_STORE
    secrets_file = env.home / "secrets.yaml"

    done = secret_set(env, "UAT.password", "linux-pass-26")
    result = run(env, flow(env, web, [("X-Pass", "secret:UAT.password")]))

    assert done.returncode == 0, done.stderr
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
    assert yaml.safe_load(secrets_file.read_text(encoding="utf-8")) == {"UAT.password": "linux-pass-26"}
    assert listed(env) == [("UAT", None, [("password", None)])]
    sent(env, web, result, {"x-pass": "linux-pass-26"})

    gone = taskloom(env, "secret", "delete", "UAT.password")

    assert gone.returncode == 0, gone.stderr
    assert "linux-pass-26" not in secrets_file.read_text(encoding="utf-8")
    assert entries(credential_store) == {}


@pytest.mark.invariant("CR-17")
@pytest.mark.invariant("CR-14")
@pytest.mark.invariant("CR-12")
@pytest.mark.skipif(not (os.environ.get("CI") and sys.platform == "win32"),
                    reason="the real Windows credential store: on Windows CI only")
def test_the_real_windows_credential_store_takes_a_long_value_names_in_other_case_and_deletes_it_whole(env, web):
    """H3: Windows' Credential Manager itself, under a made-up service name (tests/credential_store's
    WindowsUnderTestName): a 5,000-character value in and out, set again as uat.PASSWORD, deleted; afterwards no
    entry of that name is left, and every entry the test made is removed whatever happens."""
    from keyring.backends.Windows import WinVaultKeyring
    from keyring.errors import PasswordDeleteError

    service, asked = f"taskloom-test-{uuid.uuid4().hex}", env.root / "asked.jsonl"
    env.env.update(PYTHON_KEYRING_BACKEND="taskloom_test_store.WindowsUnderTestName", TEST_CREDENTIAL_SERVICE=service,
                   TEST_CREDENTIAL_STORE=str(asked))
    real = WinVaultKeyring()

    def calls() -> list[dict]:
        return [json.loads(line) for line in asked.read_text(encoding="utf-8").splitlines()] if asked.exists() else []

    first, second = LONG, ("é𝄞Zy8" * 1000)
    basic_connection(env, web, "Uat.Password")
    path = flow(env, web, connection="shop")
    try:
        stored = secret_set(env, "UAT.password", first)
        sent_first = run(env, path)
        assert stored.returncode == 0, stored.stderr
        sent(env, web, sent_first, {"basic-password": first})

        replaced = secret_set(env, "uat.PASSWORD", second)
        sent_second = run(env, path)
        assert replaced.returncode == 0, replaced.stderr
        sent(env, web, sent_second, {"basic-password": second})

        gone = taskloom(env, "secret", "delete", "UAT.password")
        assert gone.returncode == 0, gone.stderr
        made = {c["username"] for c in calls() if c["call"] == "set"}
        assert made and {c["service"] for c in calls()} == {SERVICE}
        assert [n for n in made if real.get_password(service, n) is not None] == []
    finally:
        for name in {c["username"] for c in calls() if c["call"] == "set"}:
            with contextlib.suppress(PasswordDeleteError):
                real.delete_password(service, name)
