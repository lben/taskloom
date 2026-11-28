"""Credentials on the PC (docs/promises/credentials.yaml), where a secret's value could leave Taskloom outside the
editor window (CR-5, CR-6, CR-8): what `taskloom run` prints (also what a block prints, both ways of running Python),
the stored run (its Log, outputs, table files, parameters, inputs typed for a re-run), the history database, error
lines of `taskloom` commands, `taskloom connection test`, the scheduler service's messages and emails, the warning when
a notification email cannot be sent, the Python environment's result files; and that a run whose connection names a
missing card, key or stored value stops before its first block. Through the real `taskloom` program and the scheduler
service's loop (as `taskloom scheduler` runs it) only, against the tests' own credential store (tests/credential_store,
never the real one) and web and mail servers on this computer started by each test (never the internet). Each test
names the rows of the stage's combinations table it covers (A9, A14, E, ...). The editor's own sinks (Data preview,
hovers, the crash log, Copy executed query, the Log pane) are tested with the editor.

What the tests take as given (the stage's design contract, beside those of tests/test_credentials_store.py and
tests/test_credentials_refs.py):
- A value of 4 characters or more shows as *** wherever text shows it: as it is and in the forms a web address gives
  it (URL-encoded: a space as %20 or +, a + or / as it is or encoded). A value of 3 characters shows inside text as it
  is (CR-6: it cannot be hidden reliably).
- No file under the Taskloom home or the flow's folder holds such a value, once the run that used it has ended, or
  while it goes (but the tests' credential store itself); a table cell that held it is stored with *** in its place.
- A run stops before its first block naming every card, key or stored value that a connection its blocks use names
  (secret:CARD.key), as it does for the blocks' own settings.
"""

import base64
import datetime as dt
import gc
import http.server
import json
import socketserver
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlsplit

import polars as pl
import pytest
import yaml

from sshserver import free_port
from taskloom.config import Home
from taskloom.scheduler import Scheduler, acquire_lock
from taskloom.scheduler import _unlock as unlock
import test_credentials_refs
from test_credentials_refs import api, lines_with, log_of, write_flow
from test_credentials_store import names, plant, refused_cleanly, run, taskloom, told, write_cards

# The fixtures of the references' tests, used here by name.
in_environment = test_credentials_refs.in_environment
python_environment = test_credentials_refs.python_environment

# Made-up values that cannot occur by chance: each has a space, a + and a /, so its URL-encoded forms differ from it.
TOKEN = "Kv7 Lantern+q/9Z"  # UAT.token
MAIL = "Mx4 Harbor+w/2Q"  # MAIL.password: the notification emails' mail server
KEY = "Sk8 Meadow+r/5T"  # SHOP.key: an API key in the query
FOUR, THREE = "k9 Z", "x7Q"  # UAT.four, UAT.three (A10, A9)
CARDS = {"UAT": {"rows": {"token": None, "four": None, "three": None, "unset": None}},
         "MAIL": {"rows": {"password": None}},
         "SHOP": {"rows": {"key": None}}}
VALUES = {"UAT.token": TOKEN, "UAT.four": FOUR, "UAT.three": THREE, "MAIL.password": MAIL, "SHOP.key": KEY}
URL_SAFE = "!#$%&'()*+,/:;=?@[]~"  # what a web address holds as it is (a space in it is written %20)
# The tests' credential store: it holds the values. And the folder's scheduler.lock, which the service holds while a
# test runs (service): Taskloom never writes in it, and on Windows a file another handle has locked cannot be read.
NOT_SCANNED = {"credential-store.json", "scheduler.lock"}


@pytest.fixture(autouse=True)
def stored(env, credential_store):
    """CARDS listed (UAT.unset with no value stored) and VALUES stored, for every test."""
    write_cards(env, CARDS)
    plant(credential_store, VALUES)


# --- where a value could show -----------------------------------------------------------------------------------

def forms(value: str) -> set[str]:
    """The value as it is and URL-encoded, as a web address gives it."""
    return {value, quote(value, safe=""), quote(value), quote_plus(value), quote(value, safe=URL_SAFE)}


def shown(text: str, *values: str) -> list[str]:
    """Each form of `values` that `text` holds."""
    return sorted({f for value in values for f in forms(value) if f in text})


def table_cells(path) -> list[str]:
    """Every cell of a table file (Parquet, compressed: its bytes do not show its text), as text."""
    return [str(v) for column in pl.read_parquet(path).get_columns() for v in column.to_list()]


def stored_with(env, *values: str) -> list[str]:
    """Every file under the test's folder (the Taskloom home, its runs and history.db with its -wal, the flow's folder)
    that holds a form of one of `values`: in its bytes (UTF-8) or, for a table file, in a cell."""
    # A connection to history.db this process no longer uses (env.query's) closes only once garbage is collected, and
    # SQLite then removes history.db-wal and -shm: collected first, so that none goes while the files are read.
    gc.collect()
    found = []
    for path in sorted(p for p in env.root.rglob("*") if p.is_file() and p.name not in NOT_SCANNED):
        data = path.read_bytes()
        cells = table_cells(path) if path.suffix == ".parquet" else []
        found += [f"{path.relative_to(env.root)} holds {f!r}" for value in values for f in sorted(forms(value))
                  if f.encode("utf-8") in data or any(f in cell for cell in cells)]
    return found


def terminal(result) -> str:
    return result.stdout + result.stderr


def email_text(message) -> str:
    """An email's subject and every text part (plain and HTML)."""
    return "\n".join([str(message["Subject"])] + [part.get_content() for part in message.walk()
                                                  if part.get_content_maintype() == "text"])


def notify_settings(env, *when: str):
    (env.home / "settings.yaml").write_text(yaml.safe_dump(
        {"notify_email": "me@example.com", "smtp_connection": "mailer", "notify_on": list(when)}), encoding="utf-8")


def code(source: str, inputs=(), outputs=(), in_environment: bool = False) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": source,
                                               "use_environment": in_environment}}


def connections(env, **found):
    (env.home / "connections.yaml").write_text(yaml.safe_dump(found, sort_keys=False), encoding="utf-8")


# --- servers ------------------------------------------------------------------------------------------------------

@pytest.fixture
def web():
    """A web server on this computer; it records each request (its path as sent, its headers with lower-case names).
    /start/REST redirects to /landing/REST; /landing answers 200; /refuse answers 401 with a reason and a body that
    repeat the Authorization header it got; /broken answers a line that is not HTTP, repeating it too."""
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(SimpleNamespace(path=self.path, headers={k.lower(): v for k, v in self.headers.items()}))
            auth = self.headers.get("Authorization", "")
            if self.path.startswith("/start"):
                self.send_response(302)
                self.send_header("Location", "/landing" + self.path[len("/start"):])
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path.startswith("/broken"):
                self.wfile.write(f"BROKEN {auth}\r\n".encode("utf-8"))
                self.close_connection = True
            else:
                status, reason, body = (401, f"invalid token {auth}", f"the token {auth} is not valid") \
                    if self.path.startswith("/refuse") else (200, "OK", "landed")
                data = body.encode("utf-8")
                self.send_response(status, reason)
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


@pytest.fixture
def refusing_mail(env):
    """A mail server on this computer that asks for a login (AUTH PLAIN) and refuses it with a message repeating the
    password it got, as some servers do: the SMTP connection `mailer`, user me, its password MAIL.password. `tried`:
    the passwords it got."""
    tried = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"220 mail.test ESMTP\r\n")
            for raw in self.rfile:
                verb, _, rest = raw.decode("utf-8", "replace").strip().partition(" ")
                if verb.upper() in ("EHLO", "HELO"):
                    self.wfile.write(b"250-mail.test\r\n250 AUTH PLAIN\r\n")
                elif verb.upper() == "AUTH":
                    answer = rest.partition(" ")[2]
                    if not answer:
                        self.wfile.write(b"334 \r\n")
                        answer = self.rfile.readline().decode("ascii").strip()
                    password = base64.b64decode(answer).split(b"\0")[-1].decode("utf-8")
                    tried.append(password)
                    self.wfile.write(f"535 5.7.8 password {password} refused\r\n".encode("utf-8"))
                elif verb.upper() == "QUIT":
                    self.wfile.write(b"221 bye\r\n")
                    return
                else:
                    self.wfile.write(b"502 5.5.2 not here\r\n")

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connections(env, mailer={"kind": "smtp", "host": "127.0.0.1", "port": server.server_address[1],
                             "sender": "taskloom@example.com", "username": "me", "password": "secret:MAIL.password"})
    try:
        yield SimpleNamespace(tried=tried)
    finally:
        server.shutdown()
        server.server_close()


# --- the scheduler service (its loop, as `taskloom scheduler` runs it) -------------------------------------------

T0 = dt.datetime(2026, 1, 5, 6, 30)
LOCKS = []  # the Taskloom folders' locks the services made here hold


@pytest.fixture(autouse=True)
def services_let_go():
    yield
    while LOCKS:
        unlock(LOCKS.pop())


def scheduled(source: str, wait: float = 0) -> str:
    """A flow scheduled every minute: it waits `wait` seconds, then runs the Python `source`."""
    return yaml.safe_dump({
        "params": {"when": {"type": "datetime", "expr": "now()"}},
        "triggers": [{"schedule": {"cron": "* * * * *", "misfire": "run_once"}}],
        "blocks": {"pause": {"type": "logic.wait", "config": {"seconds": wait}}, "work": code(source)},
        "edges": ["pause.value -> work.after"]}, sort_keys=False)


def service(env, text: str) -> tuple[Scheduler, list]:
    """The scheduler service's loop over the scheduled flow `text` (flow.yaml), holding the Taskloom folder's lock as
    `taskloom scheduler` does; the desktop notices it shows are recorded as (title, message)."""
    home = Home(env.home)
    path = env.root / "flow.yaml"
    path.write_text(text, encoding="utf-8")
    home.set_scheduled(path, True)
    LOCKS.append(acquire_lock(home))
    notes = []
    return Scheduler(home, lambda title, message: notes.append((title, message))), notes


def minutes(scheduler, *offsets: float):
    for offset in offsets:
        scheduler.tick(T0 + dt.timedelta(minutes=offset))
    scheduler.wait_for_runs()


# --- the terminal, the Log and the stored run (E: the Log, what a block prints, results, the terminal) -------------

@pytest.mark.invariant("CR-5")
def test_what_a_block_prints_outputs_and_fails_with_shows_as_stars_and_no_file_holds_it(env, in_environment):
    """E (the Log and what a block prints, both ways of running Python; the terminal; stored runs; the history
    database): a block prints the value (also to standard error) and outputs text, a dict and a list holding it; the
    next fails with it in its message."""
    path = write_flow(env, {
        "show": code('import sys\n'
                     't = secret("UAT.token")\n'
                     'print("printed", t)\n'
                     'print("to stderr", t, file=sys.stderr)\n'
                     'text, info, items = f"text {t}", {"token": t}, [t, "x"]\n',
                     outputs=["text", "info", "items"], in_environment=in_environment),
        "fail": code('raise RuntimeError(f"refused {secret(\'UAT.token\')}")', inputs=["text"],
                     in_environment=in_environment)}, ["show.text -> fail.text"])

    result = run(env, path)

    said = told(env, result)
    assert result.returncode != 0, said
    assert shown(said, TOKEN) == [], said
    assert "printed ***" in log_of(env, "show") and "to stderr ***" in log_of(env, "show"), log_of(env, "show")
    assert "refused ***" in said, said
    assert stored_with(env, TOKEN) == []


@pytest.mark.invariant("CR-5")
def test_what_a_library_writes_straight_to_the_output_shows_as_stars(env, in_environment):
    """E (what a block prints; the terminal): code (or a library it calls) writes the value straight to standard
    output and standard error, and logs it with Python's logging, not through print."""
    path = write_flow(env, {"lib": code(
        'import logging, sys\n'
        't = secret("UAT.token")\n'
        'sys.stdout.write(f"lib-out {t}\\n")\n'
        'sys.stdout.flush()\n'
        'sys.stderr.write(f"lib-err {t}\\n")\n'
        'sys.stderr.flush()\n'
        'logging.getLogger("lib").error("lib-log %s", t)\n', in_environment=in_environment)})

    result = run(env, path)

    assert result.returncode == 0, told(env, result)
    assert shown(terminal(result), TOKEN) == [], terminal(result)
    for line in ("lib-out ***", "lib-err ***", "lib-log ***"):
        assert line in terminal(result), terminal(result)
    assert stored_with(env, TOKEN) == []


@pytest.mark.invariant("CR-5")
def test_a_value_in_a_table_cell_is_stored_as_stars(env):
    """A15 (the stored table), E (results and their table cells): a Python Code block outputs a DataFrame and a DuckDB
    SQL block a table, each with the value inside a cell. The table files the run keeps hold *** in its place."""
    path = write_flow(env, {
        "frame": code('t = pl.DataFrame({"note": ["token " + secret("UAT.token")], "n": [1]})', outputs=["t"]),
        "sql": {"type": "data.duckdb_sql",
                "config": {"inputs": [], "sql": "select 'token {{secret:UAT.token}}' as note"}}})

    result = run(env, path)

    assert result.returncode == 0, told(env, result)
    assert shown(told(env, result), TOKEN) == [], told(env, result)
    tables = {str(p.relative_to(env.root)): table_cells(p) for p in sorted(env.root.rglob("*.parquet"))}
    assert stored_with(env, TOKEN) == [], tables
    assert [where for where, cells in tables.items() if any("token ***" in cell for cell in cells)], tables


@pytest.mark.invariant("CR-5")
@pytest.mark.invariant("CR-6")
def test_a_value_in_a_web_address_shows_as_stars_url_encoded_too(env, web):
    """A14 (the URL-encoded value in an API address that is logged, and in the address it was redirected to), A10 (4
    characters: hidden inside text), A9 (3 characters: shown inside text)."""
    path = write_flow(env, {"call": api(
        f"{web.base}/start/{{{{secret:UAT.token}}}}/{{{{secret:UAT.four}}}}/{{{{secret:UAT.three}}}}")})

    result = run(env, path)

    said = told(env, result)
    assert result.returncode == 0, said
    assert [unquote(r.path) for r in web.seen] == [f"/start/{TOKEN}/{FOUR}/{THREE}",
                                                   f"/landing/{TOKEN}/{FOUR}/{THREE}"]
    assert shown(said, TOKEN, FOUR) == [], said
    log = log_of(env, "call")
    sent, redirected = lines_with(log, "GET"), lines_with(log, "redirected")
    assert sent and all(f"/start/***/***/{THREE}" in line for line in sent), log
    assert redirected and all(f"/landing/***/***/{THREE}" in line for line in redirected), log
    assert stored_with(env, TOKEN, FOUR) == []


@pytest.mark.invariant("CR-5")
def test_a_servers_error_message_holding_the_value_shows_as_stars_in_the_log_run_history_and_the_email(env, web,
                                                                                                       smtp):
    """A16 (a server's error message holding the value: the Log, the failure email, Run history), E (results: the
    error output a handler gets): the server refuses the Bearer token with a reason and a body repeating it; a
    handler on the error output prints what it got and fails the run; `taskloom run --notify` emails the failure."""
    notify_settings(env, "failure")
    path = write_flow(env, {
        "call": api(f"{web.base}/refuse", auth="Bearer token", secret="UAT.token"),
        "handle": code('print("got", error)\nraise RuntimeError("the call was refused")', inputs=["error"])},
        ["call.error -> handle.error"])

    result = taskloom(env, "run", path, "--notify")

    said = told(env, result)
    assert result.returncode != 0, said
    assert [r.headers.get("authorization") for r in web.seen] == [f"Bearer {TOKEN}"]
    assert shown(said, TOKEN) == [], said
    assert "***" in log_of(env, "handle"), log_of(env, "handle")
    [(error,)] = env.query("SELECT error FROM blocks WHERE block='call'")
    assert error is None or shown(error, TOKEN) == [], error
    [message] = smtp.messages
    assert shown(email_text(message), TOKEN) == [] and "***" in email_text(message), email_text(message)
    assert stored_with(env, TOKEN) == []


@pytest.mark.invariant("CR-5")
def test_a_value_given_as_a_parameter_shows_as_stars_in_run_history_the_database_and_the_email(env, smtp):
    """E (Run history, also parameters; the history database; the terminal): the value given with --param to a flow
    that reads it; `taskloom run --notify` emails the run, its parameters included."""
    notify_settings(env, "success")
    path = write_flow(env, {"use": code('print("same", params["note"] == secret("UAT.token"))')},
                      params={"note": {"type": "text", "value": ""}})

    result = taskloom(env, "run", path, "--param", f"note={TOKEN}", "--notify")

    said = told(env, result)
    assert result.returncode == 0, said
    assert "same True" in log_of(env, "use"), log_of(env, "use")
    assert shown(terminal(result), TOKEN) == [] and "note=***" in terminal(result), terminal(result)
    [(params,)] = env.query("SELECT params FROM runs")
    assert shown(params, TOKEN) == [] and "***" in params, params
    [message] = smtp.messages
    assert shown(email_text(message), TOKEN) == [] and "***" in email_text(message), email_text(message)
    assert stored_with(env, TOKEN) == []


@pytest.mark.invariant("CR-5")
def test_a_value_typed_as_an_input_of_a_re_run_is_never_stored(env):
    """E (stored runs): one block run on its own (--only) with the value typed as its input (--input), as Run block
    with these inputs does; the run keeps the inputs it got, for a later re-run."""
    path = write_flow(env, {"show": code('print("same", response == secret("UAT.token"))', inputs=["response"])})

    result = taskloom(env, "run", path, "--only", "show", "--input", f"response={TOKEN!r}")

    said = told(env, result)
    assert result.returncode == 0, said
    assert "same True" in log_of(env, "show"), log_of(env, "show")
    assert shown(said, TOKEN) == [], said
    assert stored_with(env, TOKEN) == []


# --- `taskloom` commands: error lines and connection test (E) ----------------------------------------------------

@pytest.mark.invariant("CR-5")
def test_an_error_line_of_taskloom_run_holding_the_value_shows_it_as_stars(env):
    """E (`taskloom` error lines): a flow that reads the value is given it, by mistake, as a whole-number parameter;
    the run is refused with an error line that quotes what it was given."""
    path = write_flow(env, {"use": code('print(secret("UAT.token"))')},
                      params={"count": {"type": "int", "value": 1}})

    result = taskloom(env, "run", path, "--param", f"count={TOKEN}")

    refused_cleanly(env, result, "count")
    errors = lines_with(result.stderr, "error:")
    assert errors, terminal(result)
    assert shown(terminal(result), TOKEN) == [], terminal(result)
    assert "***" in "\n".join(errors), errors


@pytest.mark.invariant("CR-5")
def test_connection_test_of_an_api_key_in_the_query_shows_it_as_stars(env, web):
    """E (the Connections test message, key in the web address): `taskloom connection test` of an API connection
    that sends its key in the query says the address it called."""
    connections(env, shop={"kind": "api", "base_url": f"{web.base}/landing", "auth": "API key in the query",
                           "key_name": "api_key", "secret": "secret:SHOP.key"})

    result = taskloom(env, "connection", "test", "shop")

    assert result.returncode == 0, terminal(result)
    assert [parse_qs(urlsplit(r.path).query).get("api_key") for r in web.seen] == [[KEY]]
    assert shown(terminal(result), KEY) == [], terminal(result)
    assert "***" in result.stdout, terminal(result)


@pytest.mark.parametrize("where", ["/refuse", "/broken"], ids=["a refusal's reason", "an answer that is not HTTP"])
@pytest.mark.invariant("CR-5")
def test_connection_test_shows_a_servers_message_holding_the_value_with_stars(env, web, where):
    """A16 for the Connections test message (E): the server's answer repeats the Bearer token it got, in the reason
    of a 401 or in a line that is not HTTP (an error of `taskloom connection test`)."""
    connections(env, shop={"kind": "api", "base_url": f"{web.base}{where}", "auth": "Bearer token",
                           "secret": "secret:UAT.token"})

    result = taskloom(env, "connection", "test", "shop")

    assert [r.headers.get("authorization") for r in web.seen] == [f"Bearer {TOKEN}"]
    assert shown(terminal(result), TOKEN) == [], terminal(result)
    assert "***" in terminal(result), terminal(result)


@pytest.mark.invariant("CR-5")
def test_the_warning_that_a_notification_email_could_not_be_sent_shows_the_password_as_stars(env, refusing_mail):
    """E (the notify-failure warning): `taskloom run --notify` with a mail server that refuses the login, repeating
    the password in its message."""
    notify_settings(env, "success")
    path = write_flow(env, {"work": code("pass")})

    result = taskloom(env, "run", path, "--notify")

    assert result.returncode == 0, terminal(result)
    assert refusing_mail.tried == [MAIL]
    warning = lines_with(result.stderr, "warning:", "notification")
    assert warning, terminal(result)
    assert shown(terminal(result), MAIL) == [], terminal(result)
    assert "***" in "\n".join(warning), warning


# --- the scheduler service: its messages and emails (E) ----------------------------------------------------------

@pytest.mark.invariant("CR-5")
def test_the_scheduler_says_an_email_could_not_be_sent_with_the_password_as_stars(env, refusing_mail):
    """E (the scheduler's messages): a skipped run is to be emailed; the mail server refuses the login, repeating the
    password; the service says it cannot send the email."""
    notify_settings(env, "skipped")
    scheduler, notes = service(env, scheduled("pass", wait=2))

    minutes(scheduler, 0, 1, 2)  # at 2 the run of 1 still goes: skipped

    cannot = [message for title, message in notes if title == "Cannot send email"]
    assert cannot and refusing_mail.tried == [MAIL], notes
    assert shown("\n".join(cannot), MAIL) == [], cannot
    assert "***" in cannot[0], cannot


@pytest.mark.invariant("CR-5")
def test_a_scheduled_run_that_fails_with_the_value_is_told_and_emailed_with_stars(env, smtp):
    """A16, E (the scheduler's failure notice and failure email): a scheduled run fails with the value in its
    error."""
    notify_settings(env, "failure")
    scheduler, notes = service(env, scheduled('raise RuntimeError(f"login refused for {secret(\'UAT.token\')}")'))

    minutes(scheduler, 0, 1)
    scheduler.tick(T0 + dt.timedelta(minutes=1, seconds=30))

    [(title, message)] = notes
    assert "failed" in title and "login refused for ***" in message, notes
    assert shown(message, TOKEN) == [], notes
    [email] = smtp.messages
    assert shown(email_text(email), TOKEN) == [] and "***" in email_text(email), email_text(email)
    assert stored_with(env, TOKEN) == []


@pytest.mark.invariant("CR-5")
def test_a_scheduled_run_that_crashes_after_writing_the_value_is_told_and_emailed_with_stars(env, smtp):
    """E (the scheduler's crash notice and email): the run's code writes the value straight to its output (as a
    library might) and its process dies; the service tells and emails what it last wrote."""
    notify_settings(env, "failure")
    scheduler, notes = service(env, scheduled(
        'import os, sys\nsys.stdout.write(f"connecting with {secret(\'UAT.token\')}\\n")\nsys.stdout.flush()\n'
        'os._exit(3)'))

    minutes(scheduler, 0, 1)

    [(title, message)] = notes
    assert "crashed" in title, notes
    assert shown(message, TOKEN) == [] and "***" in message, notes
    [email] = smtp.messages
    assert "crashed" in str(email["Subject"]), email["Subject"]
    assert shown(email_text(email), TOKEN) == [] and "***" in email_text(email), email_text(email)
    assert stored_with(env, TOKEN) == []


# --- the Python environment's result files (E) -----------------------------------------------------------------

LOOK_FOR_THE_VALUE = '''\
import atexit, json, os
from urllib.parse import quote, quote_plus

token = secret("UAT.token")
header = "Bearer " + token


def look():  # once the code has ended and its outputs are written for Taskloom: every file holding the value
    sought = [f.encode("utf-8") for f in {token, quote(token, safe=""), quote(token), quote_plus(token)}]
    found = set()
    for top in (os.environ["TASKLOOM_HOME"], os.getcwd()):
        for folder, _, files in os.walk(top):
            for name in files:
                if name in ("credential-store.json", "found.json"):
                    continue
                try:
                    with open(os.path.join(folder, name), "rb") as f:
                        data = f.read()
                except OSError:
                    continue
                if any(s in data for s in sought):
                    found.add(os.path.relpath(os.path.join(folder, name), os.getcwd()))
    with open("found.json", "w", encoding="utf-8") as f:
        json.dump(sorted(found), f)


atexit.register(look)
'''


@pytest.mark.invariant("CR-5")
def test_the_python_environment_never_writes_the_value_to_a_file(env, python_environment):
    """E (the Python environment's result files): code run in your Python environment outputs text built from the
    value; as its process ends, after its outputs are written for Taskloom, it looks for the value in every file under
    the Taskloom home and the flow's folder."""
    (env.home / "settings.yaml").write_text(f"python_env: '{python_environment.as_posix()}'\n", encoding="utf-8")
    path = write_flow(env, {"login": code(LOOK_FOR_THE_VALUE, outputs=["header"], in_environment=True)})

    result = run(env, path)

    said = told(env, result)
    assert result.returncode == 0, said
    assert shown(said, TOKEN) == [], said
    assert json.loads((env.root / "found.json").read_text(encoding="utf-8")) == []
    assert stored_with(env, TOKEN) == []


# --- a run stops before its first block for a connection's missing credential (CR-8) -----------------------------

@pytest.mark.invariant("CR-8")
def test_a_run_whose_connection_names_a_missing_card_or_value_stops_before_its_first_block_naming_it(env, web):
    """A run whose API Request's connection names a card that does not exist (secret:NOPE.token) and whose Send
    Email's connection names a listed key with no stored value (secret:UAT.unset): it ends before its first block
    runs, names both and sends nothing. The same flow with connections whose values are there runs (for
    comparison, after it)."""
    mail = {"kind": "smtp", "host": "127.0.0.1", "port": free_port(), "sender": "taskloom@example.com",
            "username": "me"}
    connections(env,
                good_api={"kind": "api", "base_url": web.base, "auth": "Bearer token", "secret": "secret:UAT.token"},
                good_mail={**mail, "password": "secret:MAIL.password"},
                bad_api={"kind": "api", "base_url": web.base, "auth": "Bearer token", "secret": "secret:NOPE.token"},
                bad_mail={**mail, "password": "secret:UAT.unset"})

    def flow_using(which: str):
        return write_flow(env, {
            "first": code(f'open("started-{which}.txt", "w").write("started")'),
            "call": api("/landing", connection=f"{which}_api"),
            "mail": {"type": "email.send", "config": {"connection": f"{which}_mail", "to": ["team@example.com"],
                                                      "subject": "Codes", "message": "hello"}}},
            ["first.done -> call.after", "first.done -> mail.after"], name=which)

    bad = run(env, flow_using("bad"))

    said = told(env, bad)
    assert not (env.root / "started-bad.txt").exists(), said
    refused_cleanly(env, bad)
    assert web.seen == [], said
    assert names(said, "NOPE", "token") and names(said, "UAT", "unset"), said
    good = run(env, flow_using("good"))  # (its email finds no mail server: it fails after its first block ran)
    assert (env.root / "started-good.txt").exists() and len(web.seen) == 1, told(env, good)
