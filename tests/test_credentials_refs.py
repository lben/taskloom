"""Credentials on the PC (docs/promises/credentials.yaml), the references in text and in Python: {{secret:CARD.key}}
inside any text that takes placeholders (a setting's text, HTML Template's Template, Inspect's message, SQL code),
secret("CARD.key") in Python Code (in Taskloom's own Python and in your Python environment), what Validate says about
references, and the check a run makes before its first block. Through the real `taskloom run` and `taskloom validate`
only, against the tests' own credential store (tests/credential_store, never the real one), a web server, a mail server
and an SSH server on this computer, each started by the test (never the internet). Each test names the rows of the
stage's combinations table it covers (A1, G4, I3, ...).

What the tests take as given (the stage's design contract, beside that of tests/test_credentials_store.py):
- {{secret:CARD.key}} is filled once, by the value as it is: braces in a value are never filled.
- In Python Code, secret("CARD.key") gives the value; its name must be written in the code as text (Validate names
  anything else as an error, with the block). The value never reaches the Python process through an environment
  variable or an argument.
- Validate names each reference to a card or key that does not exist on a line of its own, with the block and the
  setting as Properties names them (URL, Headers, Content, Template, Message, Code, Stored secret (name)), as its other
  messages do: `<block>: <setting>: ...`.
- Validate refuses a flow parameter or a block's input named secret, and warns about a secret in an SSH Command's or a
  Start Remote Process's command (other programs on that machine can see a command while it runs).
"""

import http.server
import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
import yaml

from test_credentials_store import LONG, names, plant, refused_cleanly, run, taskloom, told, write_cards

# Cards as the window makes them: DEFAULT takes its values from UAT (A2), and its row archive points at MID.archive,
# which points at PROD.password (A3, A4: a chain of two).
CARDS = {"UAT": {"rows": {"host": None, "token": None, "password": None}},
         "PROD": {"rows": {"password": None}},
         "MID": {"rows": {"archive": "PROD.password"}},
         "DEFAULT": {"uses": "UAT", "rows": {"archive": "MID.archive"}}}
VALUES = {"UAT.host": "uat-host-7f3", "UAT.token": "uat-Token-7f3", "UAT.password": "uat-Pass-7f3",
          "PROD.password": "prod-Pass-7f3"}
HOST, TOKEN, PASSWORD, PROD = VALUES["UAT.host"], VALUES["UAT.token"], VALUES["UAT.password"], VALUES["PROD.password"]


def cards(env, store, extra: dict | None = None):
    """CARDS listed (with `extra` cards or rows added) and VALUES stored."""
    listed = {name: {**card, "rows": dict(card["rows"])} for name, card in CARDS.items()}
    for name, card in (extra or {}).items():
        listed.setdefault(name, {"rows": {}})["rows"].update(card["rows"])
    write_cards(env, listed)
    plant(store, VALUES)


# --- the servers --------------------------------------------------------------------------------------------------

@pytest.fixture
def site():
    """A web server on this computer. It records each request (method, path decoded, body as UTF-8 text, headers with
    lower-case names) and answers, as text, with its path, its body and its Authorization header, one a line."""
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def answer(self):
            data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            got = SimpleNamespace(method=self.command, path=unquote(self.path), body=data.decode("utf-8"),
                                  headers={k.lower(): v for k, v in self.headers.items()})
            seen.append(got)
            text = f"path={got.path}\nbody={got.body}\nauthorization={got.headers.get('authorization', '')}"
            out = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        do_GET = do_POST = answer

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(base=f"http://127.0.0.1:{server.server_port}", seen=seen)
    finally:
        server.shutdown()
        server.server_close()


def html_of(message) -> str:
    return message.get_body(preferencelist=("html",)).get_content()


@pytest.fixture(scope="module")
def python_environment(tmp_path_factory):
    """A Python environment (a venv of this Python), made once for the module: the python_env setting points at it."""
    folder = tmp_path_factory.mktemp("python-environment") / "env"
    made = subprocess.run([sys.executable, "-m", "venv", str(folder)], capture_output=True, text=True, timeout=300)
    assert made.returncode == 0, made.stdout + made.stderr
    return folder


@pytest.fixture(params=[False, True], ids=["Taskloom's own Python", "my Python environment"])
def in_environment(request, env) -> bool:
    """Both ways of running Python Code: in Taskloom's own Python, and in your Python environment (Run in my Python
    environment ticked; the environment is the python_env setting's)."""
    if request.param:
        folder = request.getfixturevalue("python_environment")
        env.write("home/settings.yaml", f"python_env: '{folder.as_posix()}'\n")
    return request.param


# --- flows ----------------------------------------------------------------------------------------------------------

def write_flow(env, blocks: dict, edges=(), params: dict | None = None, name: str = "flow"):
    data = {**({"params": params} if params else {}), "blocks": blocks, "edges": list(edges)}
    path = env.root / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def api(url: str, **config) -> dict:
    return {"type": "servers.api_request", "config": {"url": url, **config}}


def python(code: str, inputs=(), in_environment: bool = False) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": [], "code": code,
                                               "use_environment": in_environment}}


SHOW = python("print(response)", inputs=["response"])  # (what the web server answered, in the Log)


def validate(env, path) -> subprocess.CompletedProcess:
    return taskloom(env, "validate", path)


def log_of(env, block: str) -> str:
    """What the block's Log said in the last run."""
    run_id, _ = env.last_run()
    return "\n".join(m or "" for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block=?", run_id,
                                                   block))


def lines_with(text: str, *words: str) -> list[str]:
    """The lines of `text` that hold every one of `words`."""
    return [line for line in text.splitlines() if all(w in line for w in words)]


def none_shows(env, result, *values: str):
    """The run succeeded, and none of `values` shows in what it printed, its Log or its blocks' errors."""
    said = told(env, result)
    assert result.returncode == 0, said
    for value in values:
        assert value not in said, said


# --- {{secret:CARD.key}} in a setting's text (A1-A4) ----------------------------------------------------------------

@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-5")
def test_a_reference_inside_a_settings_text_sends_the_value_and_the_log_shows_stars(env, site, credential_store):
    """A1 (a direct value), A2 (a card that takes from another), A3, A4 (a row pointing at a row, a chain of two): in
    the URL, inside a header's text and in a text body. The server gets each value; the Log shows ***."""
    cards(env, credential_store)
    path = write_flow(env, {
        "call": api(f"{site.base}/orders/{{{{secret:UAT.host}}}}", method="POST", body="text",
                    headers=[{"key": "Authorization", "value": "Bearer {{secret:DEFAULT.token}}"}],
                    content="archive={{secret:DEFAULT.archive}}; password={{secret:UAT.password}}"),
        "show": SHOW}, ["call.response -> show.response"])

    result = run(env, path)

    none_shows(env, result, HOST, TOKEN, PASSWORD, PROD)
    [got] = site.seen
    assert (got.path, got.headers.get("authorization"), got.body) == (
        f"/orders/{HOST}", f"Bearer {TOKEN}", f"archive={PROD}; password={PASSWORD}")
    assert "***" in log_of(env, "show"), log_of(env, "show")


@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-5")
def test_a_reference_in_an_html_template_puts_the_value_in_the_email_and_the_log_shows_none(env, smtp,
                                                                                           credential_store):
    """A1, A2 in HTML Template's Template: the email that arrives holds the values; the run tells none of them."""
    cards(env, credential_store)
    path = write_flow(env, {
        "body": {"type": "reports.html_template",
                 "config": {"template": "<p>Code {{secret:UAT.token}}, archive {{secret:DEFAULT.archive}}</p>"}},
        "mail": {"type": "email.send", "config": {"connection": "mailer", "to": ["team@example.com"],
                                                  "subject": "Codes"}}},
        ["body.html -> mail.body"])

    result = run(env, path)

    none_shows(env, result, TOKEN, PROD)
    [message] = smtp.messages
    assert f"Code {TOKEN}, archive {PROD}" in html_of(message), html_of(message)


@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-5")
def test_a_reference_in_inspects_message_shows_as_stars(env, credential_store):
    """A1, A2 in Inspect's message (whose {{ }} otherwise hold Python expressions): the run succeeds and the message
    shows *** where the reference is, next to the value that arrived."""
    cards(env, credential_store)
    path = write_flow(env, {
        "make": {"type": "logic.python", "config": {"inputs": [], "outputs": ["output"], "code": "output = 'orders'"}},
        "look": {"type": "logic.inspect",
                 "config": {"message": "token {{secret:UAT.token}} via {{secret:DEFAULT.token}} for {{value}}"}}},
        ["make.output -> look.value"])

    result = run(env, path)

    none_shows(env, result, TOKEN)
    assert "token *** via *** for orders" in log_of(env, "look"), log_of(env, "look")


@pytest.mark.invariant("CR-4")
def test_validate_takes_a_reference_in_every_text_that_takes_placeholders_and_in_python(env, site, credential_store):
    """Validate (and so a run) takes {{secret:CARD.key}} in a setting's text, a header, a body, SQL code, HTML
    Template's Template and Inspect's message, and secret("CARD.key") in Python Code run either way, also through a
    card that takes from another and a pointer."""
    cards(env, credential_store)
    path = write_flow(env, {
        "call": api(f"{site.base}/a/{{{{secret:UAT.host}}}}", method="POST", body="text",
                    headers=[{"key": "X-Token", "value": "Bearer {{secret:DEFAULT.token}}"}],
                    content="p={{secret:DEFAULT.archive}}"),
        "query": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": "select '{{secret:UAT.token}}' as t"}},
        "body": {"type": "reports.html_template", "config": {"template": "<p>{{secret:UAT.token}}</p>"}},
        "look": {"type": "logic.inspect", "config": {"message": "{{secret:UAT.token}}"}},
        "own": python('t = secret("UAT.token")'),
        "outside": python('t = secret("DEFAULT.archive")', in_environment=True)})

    checked = validate(env, path)

    assert checked.returncode == 0 and "OK" in checked.stdout, checked.stdout + checked.stderr
    assert "warning" not in checked.stderr, checked.stderr


# --- secret("CARD.key") in Python Code (G1, G4) -------------------------------------------------------------------

@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-5")
def test_python_code_gets_a_value_by_secret_and_it_is_in_no_environment_variable_or_argument(env, credential_store,
                                                                                            in_environment):
    """G1 (both ways of running), A1-A4, G4: secret("CARD.key") gives the value, also through a card that takes from
    another and a chain of pointers; what the code prints shows *** in the Log; the Python process running the code
    has none of the values in an environment variable or an argument."""
    cards(env, credential_store)
    code = """\
import json, os, sys
got = [secret("UAT.token"), secret("DEFAULT.token"), secret("DEFAULT.archive")]
with open("got.json", "w", encoding="utf-8") as f:
    json.dump([str(v) for v in got], f)
with open("process.json", "w", encoding="utf-8") as f:
    json.dump({"environment": dict(os.environ), "arguments": sys.argv,
               "command line": list(getattr(sys, "orig_argv", []))}, f)
print(f"direct={got[0]}")
"""
    path = write_flow(env, {"fetch": python(code, in_environment=in_environment)})

    result = run(env, path)

    none_shows(env, result, TOKEN, PROD)
    assert json.loads((env.root / "got.json").read_text(encoding="utf-8")) == [TOKEN, TOKEN, PROD]
    process = (env.root / "process.json").read_text(encoding="utf-8")
    for value in VALUES.values():
        assert value not in process, process
    assert "direct=***" in log_of(env, "fetch"), log_of(env, "fetch")


@pytest.mark.invariant("CR-10")
@pytest.mark.parametrize("code", ['name = "UAT.token"\nvalue = secret(name)',
                                  'card = "UAT"\nvalue = secret(f"{card}.token")'],
                         ids=["a variable", "an f-string"])
def test_secret_with_a_name_not_written_as_text_is_a_validate_error_naming_the_block(env, credential_store,
                                                                                     in_environment, code):
    """G2 (both ways of running): Validate names the block in an error; the run is refused and the code never runs.
    The same code with the name written as text validates (for comparison)."""
    cards(env, credential_store)
    good = write_flow(env, {"fetch": python('value = secret("UAT.token")', in_environment=in_environment)},
                      name="good")
    bad = write_flow(env, {"fetch": python('open("ran.txt", "w").write("ran")\n' + code,
                                           in_environment=in_environment)}, name="bad")

    accepted = validate(env, good)
    refused = validate(env, bad)
    result = run(env, bad)

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert lines_with(refused.stderr, "error:", "fetch", "secret"), refused.stderr
    assert result.returncode != 0 and "Traceback" not in told(env, result), told(env, result)
    assert not (env.root / "ran.txt").exists()


# --- missing cards and keys: Validate (I3, A6, A7, G3) and the run's check before its first block (CR-8) -------------

@pytest.mark.invariant("CR-8")
def test_validate_names_every_missing_card_or_key_with_its_block_and_setting(env, site, credential_store):
    """I3, A6 (a missing card), A7 (a missing key), G3: one flow refers to them in every kind of place; Validate names
    each, on its line with the block and the setting, and none of the references that are there."""
    cards(env, credential_store)
    missing = {  # block: (its setting, as Properties names it; the card; the key; True: the card is missing)
        "url_ref": ("URL", "NOPE", "host", True),
        "header_ref": ("Headers", "UAT", "nothere", False),
        "body_ref": ("Content", "UAT", "nothere", False),
        "stored_ref": ("Stored secret (name)", "NOPE", "token", True),
        "template_ref": ("Template", "NOPE", "token", True),
        "message_ref": ("Message", "UAT", "nothere", False),
        "code_ref": ("Code", "NOPE", "token", True),
        "env_code_ref": ("Code", "UAT", "nothere", False),
        "ask_ref": ("Stored secret (name)", "NOPE", "password", True),
    }
    path = write_flow(env, {
        "url_ref": api(f"{site.base}/{{{{secret:NOPE.host}}}}"),
        "header_ref": api(f"{site.base}/h", headers=[{"key": "X-Key", "value": "secret:UAT.nothere"}]),
        "body_ref": api(f"{site.base}/b", method="POST", body="text", content="t={{secret:UAT.nothere}}"),
        "stored_ref": api(f"{site.base}/s", auth="Bearer token", secret="NOPE.token"),
        "template_ref": {"type": "reports.html_template", "config": {"template": "<p>{{secret:NOPE.token}}</p>"}},
        "message_ref": {"type": "logic.inspect", "config": {"message": "{{secret:UAT.nothere}}"}},
        "code_ref": python('t = secret("NOPE.token")'),
        "env_code_ref": python('t = secret("UAT.nothere")', in_environment=True),
        "ask_ref": {"type": "logic.ask_user", "config": {"prompt": "Password?", "kind": "password",
                                                         "secret": "NOPE.password"}},
        "good": api(f"{site.base}/{{{{secret:UAT.token}}}}/{{{{secret:DEFAULT.archive}}}}",
                    headers=[{"key": "X-Key", "value": "secret:DEFAULT.token"}]),
    })

    checked = validate(env, path)

    errors = "\n".join(line for line in checked.stderr.splitlines() if line.startswith("error:"))
    assert checked.returncode == 2, checked.stdout + checked.stderr
    for block, (setting, card, key, no_card) in missing.items():
        found = [line for line in lines_with(errors, f"{block}:", setting, card) if no_card or names(line, card, key)]
        assert found, f"{block} ({setting}: {card}.{key}) not named:\n{checked.stderr}"
    assert lines_with(errors, "good:") == [], checked.stderr
    assert site.seen == []


@pytest.mark.invariant("CR-8")
@pytest.mark.invariant("CR-15")
def test_a_run_stops_before_its_first_block_naming_every_missing_card_key_and_stored_value(env, site,
                                                                                         credential_store):
    """A run whose blocks need a missing card (NOPE.host, in the URL), a missing key (UAT.nothere, a header) and a
    listed key with no stored value (UAT.unset, in Python Code): it ends before its first block runs, names all three
    and sends nothing. The same flow with references that are there runs (for comparison)."""
    cards(env, credential_store, {"UAT": {"rows": {"unset": None}}})

    def flow_needing(host: str, header: str, code: str, name: str):
        return write_flow(env, {
            "first": python(f'open("started-{name}.txt", "w").write("started")'),
            "call": api(f"{site.base}/r/{{{{secret:{host}}}}}", headers=[{"key": "X-Key", "value": f"secret:{header}"}]),
            "code": python(f'open("code-{name}.txt", "w").write(secret("{code}"))'),
            "show": SHOW,
        }, ["first.done -> call.after", "first.done -> code.after", "call.response -> show.response"], name=name)

    good = run(env, flow_needing("UAT.host", "UAT.token", "UAT.password", "good"))
    none_shows(env, good, HOST, TOKEN, PASSWORD)
    assert (env.root / "started-good.txt").exists() and len(site.seen) == 1
    assert (env.root / "code-good.txt").read_text(encoding="utf-8") == PASSWORD

    bad = run(env, flow_needing("NOPE.host", "UAT.nothere", "UAT.unset", "bad"))

    said = told(env, bad)
    refused_cleanly(env, bad)
    assert not (env.root / "started-bad.txt").exists(), said
    assert len(site.seen) == 1
    assert "NOPE" in said and names(said, "UAT", "nothere") and names(said, "UAT", "unset"), said


@pytest.mark.invariant("CR-3")
@pytest.mark.invariant("CR-4")
def test_a_circle_reached_from_text_or_python_stops_the_run_naming_it_and_never_hangs(env, site, credential_store):
    """A5 (in stored data) for the reference in text and in Python: A takes from B and B from A; C.x points at D.y and
    D.y at C.x. Each run ends within a minute, fails naming the circle, and sends nothing."""
    write_cards(env, {"A": {"uses": "B", "rows": {}}, "B": {"uses": "A", "rows": {}},
                      "C": {"rows": {"x": "D.y"}}, "D": {"rows": {"y": "C.x"}}})
    in_text = write_flow(env, {"call": api(f"{site.base}/{{{{secret:A.key}}}}")}, name="text")
    in_python = write_flow(env, {"code": python('open("got.txt", "w").write(secret("C.x"))')}, name="code")

    by_cards = run(env, in_text, timeout=60)
    by_rows = run(env, in_python, timeout=60)

    for result, words in ((by_cards, ("A -> B -> A",)), (by_rows, ("C.x", "D.y"))):
        said = told(env, result)
        assert result.returncode != 0, said
        assert "Circle in credentials" in said and all(w in said for w in words), said
    assert site.seen == [] and not (env.root / "got.txt").exists()


# --- values of any content (A8-A13) and braces in a value (A11) ---------------------------------------------------

CONTENTS = {"empty": "", "three": "x7Q", "four": "k9#Z",
            "mixed": 'He said "hi", it\'s a \\ test\nzweite Zeile: Grüße, пароль, 密码 𝄞', "long": LONG}


def store_contents(env):
    """CONTENTS stored as the card V's rows, as taskloom secret set stores them (long ones in parts)."""
    for key, value in CONTENTS.items():
        env.set_secret(f"V.{key}", value)


def check_contents_log(env, result, block: str):
    """The run succeeded without telling a value of 4 characters or more; the Log, where the block printed
    key=[value], shows *** for those and the 3-character one as it is (A9, A10: only 4 or more can be hidden inside
    text)."""
    none_shows(env, result, CONTENTS["four"], CONTENTS["mixed"], CONTENTS["long"])
    shown = log_of(env, block)
    for line in ("empty=[]", "three=[x7Q]", "four=[***]", "mixed=[***]"):
        assert line in shown, shown


@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-5")
@pytest.mark.invariant("CR-17")
def test_values_of_any_content_come_back_exactly_inside_a_settings_text(env, site, credential_store):
    """A8 (empty: filled as empty text), A9, A10 (3 and 4 characters), A12 (quotes, a line break, other alphabets),
    A13 (5,000 characters), in a text body."""
    store_contents(env)
    path = write_flow(env, {
        "call": api(f"{site.base}/v", method="POST", body="text",
                    content="\n".join(f"{key}=[{{{{secret:V.{key}}}}}]" for key in CONTENTS)),
        "show": SHOW}, ["call.response -> show.response"])

    result = run(env, path)

    assert result.returncode == 0, told(env, result)
    [request] = site.seen
    assert request.body == "\n".join(f"{key}=[{value}]" for key, value in CONTENTS.items())
    check_contents_log(env, result, "show")


@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-5")
@pytest.mark.invariant("CR-17")
def test_values_of_any_content_come_back_exactly_from_secret_in_python(env, credential_store, in_environment):
    """A8, A9, A10, A12, A13 from secret("CARD.key"), both ways of running Python."""
    store_contents(env)
    code = ("import json\ngot = {}\n"
            + "".join(f'got["{key}"] = secret("V.{key}")\nprint(f"{key}=[{{got[\'{key}\']}}]")\n' for key in CONTENTS)
            + 'json.dump({k: str(v) for k, v in got.items()}, open("got.json", "w", encoding="utf-8"))\n')
    path = write_flow(env, {"fetch": python(code, in_environment=in_environment)})

    result = run(env, path)

    assert result.returncode == 0, told(env, result)
    assert json.loads((env.root / "got.json").read_text(encoding="utf-8")) == CONTENTS
    check_contents_log(env, result, "fetch")


@pytest.mark.invariant("CR-11")
@pytest.mark.invariant("CR-4")
def test_braces_inside_a_value_are_never_filled(env, site, smtp, credential_store):
    """A11: a value holding {{day}} (a flow parameter) and {{secret:UAT.host}} (a stored value) arrives as it is, in a
    text body, in HTML Template's email and as a whole-field header."""
    cards(env, credential_store)
    braces = "in{{day}}side{{secret:UAT.host}}end"
    env.set_secret("UAT.braces", braces)
    path = write_flow(env, {
        "call": api(f"{site.base}/braces", method="POST", body="text", content="v={{secret:UAT.braces}}",
                    headers=[{"key": "X-Braces", "value": "secret:UAT.braces"}]),
        "body": {"type": "reports.html_template", "config": {"template": "<p>v={{secret:UAT.braces}}</p>"}},
        "mail": {"type": "email.send", "config": {"connection": "mailer", "to": ["team@example.com"],
                                                  "subject": "Braces"}}},
        ["body.html -> mail.body"], params={"day": {"type": "text", "value": "Monday"}})

    result = run(env, path)

    none_shows(env, result, braces)
    [request] = site.seen
    assert (request.body, request.headers.get("x-braces")) == (f"v={braces}", braces)
    [message] = smtp.messages
    assert f"v={braces}" in html_of(message), html_of(message)


# --- names in other case (B5), the Stored secret fields (K3) ---------------------------------------------------------

@pytest.mark.invariant("CR-14")
@pytest.mark.invariant("CR-4")
def test_a_reference_in_text_or_python_written_in_other_case_finds_the_row_as_listed(env, site, credential_store):
    """B5 for each form: {{secret:Uat.Host}} in a URL, uat.TOKEN in API Request's Stored secret, {{secret:uat.HOST}}
    in Inspect's message and secret("default.TOKEN") in Python Code find UAT's rows (DEFAULT's through UAT); Validate
    takes them too."""
    cards(env, credential_store)
    path = write_flow(env, {
        "call": api(f"{site.base}/c/{{{{secret:Uat.Host}}}}", auth="Bearer token", secret="uat.TOKEN"),
        "show": SHOW,
        "look": {"type": "logic.inspect", "config": {"message": "host {{secret:uat.HOST}}"}},
        "code": python('open("got.txt", "w", encoding="utf-8").write(secret("default.TOKEN"))')},
        ["call.response -> show.response"])

    checked = validate(env, path)
    result = run(env, path)

    assert checked.returncode == 0, checked.stdout + checked.stderr
    none_shows(env, result, HOST, TOKEN)
    [request] = site.seen
    assert (request.path, request.headers.get("authorization")) == (f"/c/{HOST}", f"Bearer {TOKEN}")
    assert (env.root / "got.txt").read_text(encoding="utf-8") == TOKEN
    assert "host ***" in log_of(env, "look"), log_of(env, "look")


@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-5")
def test_the_stored_secret_fields_of_api_request_and_ask_user_take_card_dot_key(env, site, credential_store):
    """K3: API Request's Stored secret UAT.token sends the token; Ask User's Stored secret UAT.password is the answer
    of a run nobody can answer (here: not interactive); the Log shows neither. A name without a card (token_only)
    fails the run with a message saying a reference needs CARD.key, and sends nothing."""
    cards(env, credential_store)
    path = write_flow(env, {
        "call": api(f"{site.base}/k", auth="Bearer token", secret="UAT.token"),
        "show": SHOW,
        "ask": {"type": "logic.ask_user", "config": {"prompt": "Password?", "kind": "password", "secret": "UAT.password"}},
        "keep": python('open("answer.txt", "w", encoding="utf-8").write(answer)\nprint(f"answer={answer}")',
                       inputs=["answer"])},
        ["call.response -> show.response", "ask.answer -> keep.answer"])
    flat = write_flow(env, {"call": api(f"{site.base}/f", auth="Bearer token", secret="token_only")}, name="flat")

    result = run(env, path)

    none_shows(env, result, TOKEN, PASSWORD)
    [request] = site.seen
    assert request.headers.get("authorization") == f"Bearer {TOKEN}"
    assert (env.root / "answer.txt").read_text(encoding="utf-8") == PASSWORD
    assert "answer=***" in log_of(env, "keep"), log_of(env, "keep")

    refused = run(env, flat)

    refused_cleanly(env, refused, "CARD.key")
    assert len(site.seen) == 1


# --- the name secret (CR-18) and a secret in a command (CR-19) -----------------------------------------------------

@pytest.mark.invariant("CR-18")
@pytest.mark.parametrize("named", ["a flow parameter", "a Python Code input", "an HTML Template input"])
def test_a_flow_parameter_or_an_input_named_secret_is_refused_by_validate(env, credential_store, named):
    """I1: Validate refuses it with an error naming secret and the parameter, or the block whose input it is; the run
    is refused too and nothing runs."""
    start = python('open("ran.txt", "w").write("ran")')
    flows = {
        "a flow parameter": ({"start": start}, [], {"secret": {"type": "text", "value": "plain-7f3"}}, "param"),
        "a Python Code input": ({"start": start, "fetch": python("pass", inputs=["secret"])}, [], None, "fetch"),
        "an HTML Template input": ({"start": start, "body": {"type": "reports.html_template", "config": {
            "names": {"start.done": "secret"}, "template": "<p>hello</p>"}}}, ["start.done -> body.data"], None,
                                   "body"),
    }
    blocks, edges, params, where = flows[named]
    path = write_flow(env, blocks, edges, params)

    checked = validate(env, path)
    result = run(env, path)

    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert lines_with(checked.stderr, "error:", "secret", where), checked.stderr
    assert result.returncode == 2 and not (env.root / "ran.txt").exists(), told(env, result)


@pytest.mark.invariant("CR-19")
def test_a_secret_in_an_ssh_or_start_process_command_is_allowed_with_a_warning(env, credential_store):
    """I2: Validate takes the flow (exit code 0) and warns, for each of the two blocks, that other programs on that
    machine can see a command while it runs; a command without a secret gets no such warning."""
    (env.home / "connections.yaml").write_text(yaml.safe_dump({"appserver": {
        "kind": "ssh", "host": "127.0.0.1", "port": 22, "username": "tester",
        "password": "secret:appserver.password"}}), encoding="utf-8")
    env.set_secret("appserver.password", "ssh-Pass-7f3")
    env.set_secret("UAT.token", TOKEN)
    path = write_flow(env, {
        "cmd": {"type": "servers.ssh_command",
                "config": {"connection": "appserver", "command": "curl -H 'X-Token: {{secret:UAT.token}}' http://x"}},
        "start": {"type": "servers.start_process",
                  "config": {"connection": "appserver", "command": "./report --token {{secret:UAT.token}}"}},
        "plain": {"type": "servers.ssh_command", "config": {"connection": "appserver", "command": "echo hello"}}})

    checked = validate(env, path)

    warnings = [line for line in checked.stderr.splitlines() if line.startswith("warning:")]
    assert checked.returncode == 0, checked.stdout + checked.stderr
    for block in ("cmd", "start"):
        assert [w for w in warnings if f"{block}:" in w and ("other programs" in w or "can see" in w)], checked.stderr
    assert not [w for w in warnings if "plain:" in w], checked.stderr


@pytest.mark.invariant("CR-19")
@pytest.mark.invariant("CR-4")
@pytest.mark.skipif(sys.platform == "win32", reason="the test SSH server runs commands in /bin/sh")
def test_a_secret_in_an_ssh_command_reaches_the_server_and_the_log_shows_stars(env, server, credential_store):
    """I2 (the run): the server's command gets the value; what it prints shows *** in the Log."""
    env.set_secret("UAT.token", TOKEN)
    path = write_flow(env, {"cmd": {"type": "servers.ssh_command", "config": {
        "connection": "appserver", "command": "echo {{secret:UAT.token}} > got.txt && echo token={{secret:UAT.token}}"}}})

    result = run(env, path)

    none_shows(env, result, TOKEN)
    assert (server.home_path / "got.txt").read_text().strip() == TOKEN
    assert "token=***" in log_of(env, "cmd"), log_of(env, "cmd")
