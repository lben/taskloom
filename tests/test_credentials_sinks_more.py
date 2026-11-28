"""Credentials on the PC (docs/promises/credentials.yaml, CR-5: a secret's value never shows; it shows as ***), four
places a value could reach that tests/test_credentials_{sinks,gaps,editor}.py leave open: the scheduler service's
notice that it could not start the scheduler of a server it keeps alive, the Variables report of Python Code that
fails (both ways of running Python), the Connections window's message after Save and test connection, and what Copy as
cURL copies of an API Request holding {{secret:CARD.key}} inside its address, a header and its body. Through the real
`taskloom scheduler --headless`, `taskloom run` and the editor (offscreen) only, against the tests' own credential store
(tests/credential_store, never the real one), an SSH server and a web server on this computer started by the test
(never the internet). Made-up values only.

What the tests take as given (beside the contracts of tests/test_credentials_sinks.py and
tests/test_credentials_editor.py):
- A value of 4 characters or more shows as *** wherever text shows it: as it is and in the forms a web address gives
  it (URL-encoded), and no part of it shows where a long text is cut short (the Variables report cuts a variable's
  text at 300 characters).
- The keeper (Tools > Settings: the server, the Taskloom command there) tells "Server scheduler not started" with
  the last line the server's Taskloom wrote when that stops at once: `taskloom scheduler --headless` prints it, and
  the tray icon shows the service's notice as the service gives it.
- A Python Code block whose code fails shows a report of its variables, a line each written `NAME = its value`.
- Copy as cURL shows each stored secret as <hidden: secret CARD.key> (docs/guide.md, API Request), in a web address
  as a web address writes it (URL-encoded), and never reads its value.
"""

import datetime as dt
import os
import subprocess
import sys
import time
from urllib.parse import unquote

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import test_credentials_refs  # noqa: E402
import test_credentials_sinks  # noqa: E402
import test_credentials_window as W  # noqa: E402
from sshserver import PASSWORD, free_port  # noqa: E402
from test_credentials_editor import in_connections, no_proxy  # noqa: E402
from test_credentials_refs import api, lines_with, write_flow  # noqa: E402
from test_credentials_sinks import CARDS, KEY, TOKEN, VALUES, code, connections, shown, stored_with  # noqa: E402
from test_credentials_store import plant, run, told, write_cards  # noqa: E402
from tests.test_long_output_rest import as_seen  # noqa: E402
from tests.test_paths_editor_editing import clean, settle  # noqa: E402

from taskloom.config import Home  # noqa: E402
from taskloom.scheduler import Scheduler, acquire_lock  # noqa: E402
from taskloom.scheduler import _unlock as unlock  # noqa: E402

# The fixtures of the other tests, used here by name: both ways of running Python Code, and a web server on this
# computer whose /refuse and /broken answers repeat the Authorization header they got.
in_environment = test_credentials_refs.in_environment
python_environment = test_credentials_refs.python_environment
web = test_credentials_sinks.web

# A pass phrase with two spaces in a row (as typed): a report that writes a value's runs of spaces as one would no
# longer find it there.
PHRASE = "Wd2  Juniper+m/4X"  # UAT.phrase


@pytest.fixture(autouse=True)
def stored(env, credential_store):
    """tests/test_credentials_sinks.py's cards and values, and UAT.phrase."""
    write_cards(env, {**CARDS, "UAT": {"rows": {**CARDS["UAT"]["rows"], "phrase": None}}})
    plant(credential_store, {**VALUES, "UAT.phrase": PHRASE})


def pieces(value: str, size: int = 4) -> list[str]:
    """Every run of `size` characters of `value`: a part of it long enough to be read."""
    return [value[i:i + size] for i in range(len(value) - size + 1)]


# --- the scheduler service: the keeper's "Server scheduler not started" ------------------------------------------

STOPS_AT_ONCE = """\
#!/bin/sh
echo "error: the database refused the login with the password {value}"
exit 3
"""


def printed_by_the_service(env) -> str:
    """What `taskloom scheduler --headless` prints until the keeper has told how the server's scheduler is (at most
    60 s): the time, then each notice as title: message."""
    printed = env.root / "scheduler-output.txt"
    with printed.open("wb") as out:
        service = subprocess.Popen([sys.executable, "-m", "taskloom.cli", "scheduler", "--headless"], env=env.env,
                                   cwd=env.root, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while ("Server scheduler" not in printed.read_text(errors="replace") and service.poll() is None
                   and time.monotonic() < deadline):
                time.sleep(0.2)
        finally:
            service.terminate()
            try:
                service.wait(30)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait()
    return printed.read_text(errors="replace")


def shown_on_the_desktop(env) -> str:
    """The desktop notices (as the tray icon shows them) of the scheduler service, made as `taskloom scheduler` makes
    it (holding the Taskloom folder's lock), after the keeper's check and the service's next check, which tells what
    the keeper found: each as title: message, a line each."""
    home = Home(env.home)
    lock = acquire_lock(home)
    try:
        notes = []
        scheduler = Scheduler(home, lambda title, message: notes.append(f"{title}: {message}"))
        scheduler.check_servers()  # (the keeper's thread)
        scheduler.tick(dt.datetime.now())
        return "\n".join(notes)
    finally:
        unlock(lock)


@pytest.mark.parametrize("told_by", [printed_by_the_service, shown_on_the_desktop],
                         ids=["taskloom scheduler --headless", "the desktop notice"])
@pytest.mark.invariant("CR-5")
@pytest.mark.skipif(sys.platform == "win32", reason="the test SSH server runs commands in /bin/sh")
def test_the_keepers_server_scheduler_not_started_shows_a_cards_value_as_stars(env, server, told_by):
    """E (the scheduler's messages): the keeper (Tools > Settings) keeps the scheduler of the server appserver alive,
    an SSH connection whose password is the card's row appserver.password; the Taskloom there stops at once, its last
    line repeating that password. The service says the server's scheduler was not started, with *** where the line
    names the password: printed by `taskloom scheduler --headless`, and in the notice the desktop shows."""
    taskloom_there = server.home_path / "taskloom" / "taskloom"
    taskloom_there.parent.mkdir()
    taskloom_there.write_text(STOPS_AT_ONCE.format(value=PASSWORD), encoding="utf-8")
    taskloom_there.chmod(0o755)
    (env.home / "settings.yaml").write_text(yaml.safe_dump(
        {"keeper": [{"connection": "appserver", "command": "~/taskloom/taskloom", "home": "~/.taskloom"}]}),
        encoding="utf-8")

    said = told_by(env)

    not_started = lines_with(said, "Server scheduler not started", "appserver")
    assert not_started, said
    assert shown(said, PASSWORD) == [], said
    assert "the password ***" in not_started[0], said


# --- Python Code's Variables report, both ways of running Python -------------------------------------------------

FAILS_WITH_VARIABLES = (
    't = secret("UAT.token")\n'
    'phrase = secret("UAT.phrase")\n'
    'header = "Bearer " + t\n'
    'login = {"user": "me", "password": t}\n'
    'long = "x" * 292 + t + "y" * 50\n'  # the value across the cut at 300 characters
    'raise RuntimeError("the server refused the login")\n')


def report_lines(text: str, name: str) -> list[str]:
    """The lines of the Variables report in `text` that give the variable `name`."""
    return [line for line in text.splitlines() if line.strip().startswith(f"{name} = ")]


@pytest.mark.invariant("CR-5")
def test_the_variables_report_of_code_that_fails_shows_each_variable_holding_a_value_as_stars(env, in_environment):
    """E (block errors, the Log, the terminal): Python Code reads two values, builds text, a dict and a text of over 300
    characters from them, then fails. Its report of the variables gives each, with *** for the value, and no part of
    it where the long one is cut short; no file holds a value."""
    path = write_flow(env, {"work": code(FAILS_WITH_VARIABLES, in_environment=in_environment)})

    result = run(env, path)

    said = told(env, result)
    assert result.returncode != 0, said
    assert "Variables" in said and "the server refused the login" in said, said
    assert shown(said, TOKEN, PHRASE) == [], said
    for name, holds in (("t", "***"), ("phrase", "***"), ("header", "Bearer ***"), ("login", "***"),
                        ("long", "***")):
        lines = report_lines(said, name)
        assert lines and all(holds in line for line in lines), (name, lines, said)
    cut = report_lines(said, "long")
    assert all("characters)" in line for line in cut), cut  # (cut short, it says so)
    assert [p for p in pieces(TOKEN) if any(p in line for line in cut)] == [], cut
    assert stored_with(env, TOKEN, PHRASE) == []


# --- the Connections window: Save and test connection ------------------------------------------------------------

@pytest.mark.parametrize("where", ["/refuse", "/broken", "key in the query"],
                         ids=["a refusal's reason", "an answer that is not HTTP", "the address it called"])
@pytest.mark.invariant("CR-5")
def test_the_connections_windows_test_message_shows_a_value_the_server_or_address_holds_as_stars(
        env, win, monkeypatch, web, where):
    """E (the Connections window's test message): Tools > Connections…, the API connection shop chosen, Save and test
    connection. The web server's answer repeats the Bearer token it got (in the reason of a 401, or in a line that is
    not HTTP), or the connection sends its key in the query (the test says the address it called). The window says how
    the test went with *** where the value was."""
    no_proxy(monkeypatch)
    if where == "key in the query":
        value = KEY
        connections(env, shop={"kind": "api", "base_url": f"{web.base}/landing", "auth": "API key in the query",
                               "key_name": "api_key", "secret": "secret:SHOP.key"})
    else:
        value = TOKEN
        connections(env, shop={"kind": "api", "base_url": f"{web.base}{where}", "auth": "Bearer token",
                               "secret": "secret:UAT.token"})
    seen = {}

    def steps(dialog):
        assert dialog.current == "shop", dialog.current
        dialog.test_button.click()
        W.wait_for(lambda: dialog.test_button.isEnabled() and "Testing" not in dialog.test_result.text(), 120,
                   "Save and test connection did not end")
        seen["result"] = dialog.test_result.text()

    in_connections(win, monkeypatch, steps)

    message = as_seen(seen["result"])
    assert web.seen, "the test called no web server"
    assert message.startswith(("✓", "✕")), message  # (how the test went)
    assert shown(seen["result"], value) == [] and shown(message, value) == [], seen["result"]
    assert "***" in message, message
    clean(win)


# --- Copy as cURL: {{secret:CARD.key}} inside the address, a header and the body ---------------------------------

@pytest.mark.invariant("CR-5")
def test_copy_as_curl_shows_a_reference_inside_the_address_a_header_and_the_body_hidden_never_its_value(env, win):
    """E (what the editor copies): an API Request whose address has {{secret:UAT.token}} in its path and in a Params
    row, a header row "me {{secret:UAT.token}}" and a text body "password={{secret:UAT.token}}"; right-click it > Copy
    as cURL. The command copied has <hidden: secret UAT.token> in each place (in the address URL-encoded, as a web
    address writes it) and no form of the value, as it is or URL-encoded."""
    nowhere = f"http://127.0.0.1:{free_port()}"  # (never called: copying sends nothing)
    path = write_flow(env, {"call": api(
        f"{nowhere}/orders/{{{{secret:UAT.token}}}}", method="POST",
        params=[{"key": "code", "value": "{{secret:UAT.token}}"}],
        headers=[{"key": "X-Login", "value": "me {{secret:UAT.token}}"}],
        body="text", content="password={{secret:UAT.token}}")})
    tab = win.open_flow(path)
    settle()
    QApplication.clipboard().setText("")

    items = [a for a in win._block_menu_for(tab, "call").actions()
             if a.text().replace("&", "").startswith("Copy as cURL")]
    assert len(items) == 1 and items[0].isEnabled(), [a.text() for a in win._block_menu_for(tab, "call").actions()]
    items[0].trigger()
    copied = QApplication.clipboard().text()

    marker = "<hidden: secret UAT.token>"
    assert copied.startswith("curl"), copied
    assert shown(copied, TOKEN) == [], copied
    address = next(word.strip("'") for word in copied.split() if word.strip("'").startswith(nowhere))
    assert unquote(address) == f"{nowhere}/orders/{marker}?code={marker}", copied
    assert f"X-Login: me {marker}" in copied, copied
    assert f"password={marker}" in copied, copied
    clean(win)
