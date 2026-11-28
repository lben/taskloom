"""Credentials (docs/promises/credentials.yaml) where a flow and its connections meet them, through what a person
meets: the Connections window choosing a card's row or storing a typed password (CR-4, CR-13), the open flows after
"also rewrite the open flows" (CR-16), Validate's messages naming a missing card (CR-8) and a run's Log, output and
tables showing a value only as *** (CR-5). The real editor (offscreen), the real `taskloom` program, the tests' own
credential store (tests/credential_store, never the real one). Made-up values only; no server is called.

What the tests take as given, beside the contracts of tests/test_credentials_editor.py, tests/test_credentials_window.py
and tests/test_credentials_refs.py, whose helpers they use:
- The Connections window shows, under the password, token or key of the connection shown, one of three texts: "A new
  password will be saved when you click Save." (one is typed and not saved yet), "A password is saved. Type here only
  to change it." (its setting names a row that has a value) or "No password saved. Type it here; it is stored
  securely, never in the connections file." Its list of rows (QComboBox "credential") shows the row the setting names,
  or "Type a new value".
- A password typed there is stored, on Save, as the row CONNECTION.password; an API connection's as
  CONNECTION.token for Bearer token auth, CONNECTION.password for Basic auth and CONNECTION.key for an API key. The
  setting is then secret:CONNECTION.key: a database's at properties.password, an API's at secret, the others' at
  password. connections.yaml keeps the connections in the order they were made.
- A Save that cannot store the password shows a box on the Connections window titled "Not saved", whose text ends
  "Nothing is saved: the connections and the credential store are as they were."; the window stays open.
- The rewrite renames {{secret:CARD.key}} in a templated setting and a typed value; in a setting the block names a
  credential with (Validate's "Stored secret (name)", "Code", "Headers"), also secret:CARD.key, secret("CARD.key") and
  the whole setting CARD.key. A text that only looks like one elsewhere stays as it is.
- Validate's error for a missing card is "error: BLOCK: SETTING: " and the reason.
"""

import os
import re

import polars as pl
import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QComboBox, QDialog, QMessageBox  # noqa: E402

import test_credentials_editor as E  # noqa: E402
import test_credentials_window as W  # noqa: E402
from test_credentials_refs import cards as listed_cards, lines_with, log_of, validate, write_flow  # noqa: E402
from test_credentials_sinks import connections, stored_with, table_cells  # noqa: E402
from test_credentials_store import entries, plant, run, told, write_cards  # noqa: E402

from taskloom.blocks.logic import write_reusable_block  # noqa: E402
from taskloom.config import Home  # noqa: E402

boxes = E.boxes  # (the Credentials window's boxes, answered as a step asks)

NEW = "A new password will be saved when you click Save."
SAVED = "A password is saved. Type here only to change it."
NONE_SAVED = "No password saved. Type it here; it is stored securely, never in the connections file."
NOT_SAVED_END = "\n\nNothing is saved: the connections and the credential store are as they were."
TYPE_NEW = "Type a new value"
LISTED = {"UAT": {"rows": {"password": None, "token": None}}, "PROD": {"rows": {"password": None}}}
STORED = {"UAT.password": "made-up-uat-pass-31", "PROD.password": "made-up-prod-pass-32"}  # (UAT.token: none)


def status(dialog) -> str:
    return re.sub(r"<[^>]+>", "", dialog.password_status.text())


def show(dialog, name: str):
    """The connection `name` clicked in the window's list."""
    found = dialog.names.findItems(name, Qt.MatchExactly)
    assert found, f"no connection {name}"
    dialog.names.setCurrentItem(found[0])
    W.settle()


def pick(dialog, reference: str):
    """`reference` chosen in the list of cards' rows."""
    picker = dialog.findChildren(QComboBox, "credential")[0]
    chosen = picker.findText(reference)
    assert chosen >= 0, [picker.itemText(i) for i in range(picker.count())]
    picker.setCurrentIndex(chosen)
    picker.activated.emit(chosen)
    W.settle()


def picked(dialog) -> str:
    return dialog.findChildren(QComboBox, "credential")[0].currentText()


def type_password(dialog, text: str):
    dialog.password.setFocus()
    QTest.keyClicks(dialog.password, text)
    W.settle()


# --- the Connections window: a card's row or a typed password (CR-4, CR-13) -------------------------------------------

@pytest.mark.invariant("CR-4")
def test_the_connections_window_puts_a_chosen_row_where_each_kind_keeps_it_and_shows_it_when_opened_again(
        win, monkeypatch, credential_store):
    """A row chosen for a database, an SSH server, a mail server and a Bearer-token API: each says whether that row
    has a value; connections.yaml holds secret:CARD.key in each kind's own place, in the order the connections were
    made, and nothing is stored. Opened again, each shows its row and the same text; one without a password shows
    Type a new value."""
    W.cards(win, LISTED, STORED)
    E.write_connections(win, db={"kind": "jdbc", "url": "jdbc:h2:mem:orders"},
                        box={"kind": "ssh", "host": "box.example.test", "username": "ops"},
                        mail={"kind": "smtp", "host": "mail.example.test", "port": 25},
                        shop={"kind": "api", "base_url": "https://shop.example.test", "auth": "Bearer token"},
                        plain={"kind": "ssh", "host": "plain.example.test", "username": "ops"})
    before = W.files(win, credential_store)
    chosen = {"db": ("UAT.password", SAVED), "box": ("PROD.password", SAVED), "mail": ("UAT.token", NONE_SAVED),
              "shop": ("UAT.password", SAVED)}
    said, again = {}, {}

    def choose_rows(dialog):
        for name, (reference, _) in chosen.items():
            show(dialog, name)
            pick(dialog, reference)
            said[name] = status(dialog)
        E.save_connections(dialog)

    def look(dialog):
        for name in [*chosen, "plain"]:
            show(dialog, name)
            again[name] = (picked(dialog), status(dialog))

    E.in_connections(win, monkeypatch, choose_rows)
    saved = E.connections_file(win)
    E.in_connections(win, monkeypatch, look)

    assert said == {name: text for name, (_, text) in chosen.items()}
    assert list(saved) == ["db", "box", "mail", "shop", "plain"]
    assert saved["db"]["properties"] == {"password": "secret:UAT.password"} and "password" not in saved["db"]
    assert {k: saved["box"].get(k) for k in ("password", "secret")} == {"password": "secret:PROD.password",
                                                                       "secret": None}
    assert {k: saved["mail"].get(k) for k in ("password", "secret")} == {"password": "secret:UAT.token",
                                                                        "secret": None}
    assert {k: saved["shop"].get(k) for k in ("password", "secret")} == {"password": None,
                                                                        "secret": "secret:UAT.password"}
    assert again == {**chosen, "plain": (TYPE_NEW, NONE_SAVED)}
    assert W.files(win, credential_store) == before


@pytest.mark.invariant("CR-4")
def test_a_password_typed_then_a_row_chosen_instead_stores_nothing_and_the_setting_names_the_row(
        win, monkeypatch, credential_store):
    """A new password typed for a database whose setting names UAT.password: shown again it says a new password will
    be saved and the list shows Type a new value; PROD.password chosen then: it says a password is saved, and Save
    stores nothing and writes secret:PROD.password."""
    W.cards(win, LISTED, STORED)
    E.write_connections(win, db={"kind": "jdbc", "url": "jdbc:h2:mem:orders",
                                 "properties": {"password": "secret:UAT.password"}},
                        box={"kind": "ssh", "host": "box.example.test", "username": "ops"})
    before = W.files(win, credential_store)
    seen = []

    def steps(dialog):
        show(dialog, "db")
        seen.append((picked(dialog), status(dialog)))
        type_password(dialog, "made-up-typed-pass-33")
        show(dialog, "box")
        show(dialog, "db")
        seen.append((picked(dialog), status(dialog)))
        pick(dialog, "PROD.password")
        seen.append((picked(dialog), status(dialog)))
        E.save_connections(dialog)

    E.in_connections(win, monkeypatch, steps)

    assert seen == [("UAT.password", SAVED), (TYPE_NEW, NEW), ("PROD.password", SAVED)]
    assert E.connections_file(win)["db"]["properties"] == {"password": "secret:PROD.password"}
    assert W.files(win, credential_store) == before


@pytest.mark.invariant("CR-4")
def test_a_typed_password_is_stored_as_the_row_its_kind_and_auth_name_and_the_setting_names_that_row(
        win, monkeypatch, credential_store):
    """A password typed for a database, an SSH server, a mail server and API connections with Bearer token, Basic and
    API-key auth: Save stores each as CONNECTION.password, .token or .key (in lower case) and each setting names it."""
    E.write_connections(win, db={"kind": "jdbc", "url": "jdbc:h2:mem:orders"},
                        box={"kind": "ssh", "host": "box.example.test", "username": "ops"},
                        mail={"kind": "smtp", "host": "mail.example.test", "port": 25},
                        tok={"kind": "api", "base_url": "https://tok.example.test", "auth": "Bearer token"},
                        basic={"kind": "api", "base_url": "https://basic.example.test", "auth": "Basic",
                               "username": "ops"},
                        hdr={"kind": "api", "base_url": "https://hdr.example.test", "auth": "API key in a header",
                             "key_name": "X-Key"})
    typed = {name: f"made-up-{name}-value-34" for name in ("db", "box", "mail", "tok", "basic", "hdr")}
    said = []

    def steps(dialog):
        for name, value in typed.items():
            show(dialog, name)
            type_password(dialog, value)
        E.save_connections(dialog)
        said.append(status(dialog))

    E.in_connections(win, monkeypatch, steps)
    saved = E.connections_file(win)

    rows = {"db": "db.password", "box": "box.password", "mail": "mail.password", "tok": "tok.token",
            "basic": "basic.password", "hdr": "hdr.key"}
    assert {"db": saved["db"]["properties"].get("password"), "box": saved["box"].get("password"),
            "mail": saved["mail"].get("password"), "tok": saved["tok"].get("secret"),
            "basic": saved["basic"].get("secret"), "hdr": saved["hdr"].get("secret")} == \
        {name: f"secret:{row}" for name, row in rows.items()}
    assert {name: W.value_of(win, row) for name, row in rows.items()} == typed
    assert said == [SAVED]
    text = (win.home.root / "connections.yaml").read_text(encoding="utf-8")
    assert not any(value in text for value in typed.values()), text


@pytest.mark.invariant("CR-13")
def test_a_save_that_cannot_store_says_not_saved_on_the_connections_window_and_stays_open_changing_nothing(
        win, monkeypatch, credential_store):
    """A password typed for a mail server, the store then unavailable: Save, and Save and test connection, each show a
    box on the Connections window titled Not saved, with the store's reason and that nothing is saved; the window
    stays open, no test starts, connections.yaml and the store are as before."""
    E.write_connections(win, mail={"kind": "smtp", "host": "mail.example.test", "port": 25, "username": "reports"})
    before = (win.home.root / "connections.yaml").read_bytes(), entries(credential_store)
    warned, seen = [], {}
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda parent, title, text, *a, **k: warned.append((parent, title, text)) or QMessageBox.Ok))

    def steps(dialog):
        seen["dialog"] = dialog
        type_password(dialog, "made-up-mail-pass-35")
        monkeypatch.setenv("TEST_CREDENTIAL_STORE_MODE", "unavailable")
        E.save_connections(dialog)
        seen["result"] = dialog.result()
        dialog.test_button.click()
        W.settle()
        seen["test"] = dialog.test_result.text()

    E.in_connections(win, monkeypatch, steps)

    assert [(parent is seen["dialog"], title) for parent, title, _ in warned] == [(True, "Not saved")] * 2, warned
    assert all(text.endswith(NOT_SAVED_END) and E.said_about_store(text) for _, _, text in warned), warned
    assert seen["result"] != QDialog.Accepted
    assert "Testing" not in seen["test"], seen["test"]
    assert ((win.home.root / "connections.yaml").read_bytes(), entries(credential_store)) == before


# --- the open flows after "also rewrite the open flows" (CR-16) --------------------------------------------------------

REWRITTEN = {  # block: (as written, after UAT is renamed TEST with the rewrite)
    "typed_code": ({"note": 'secret:UAT.password stays; {{secret:UAT.password}} changes; secret("UAT.password") stays',
                    "items": "secret:UAT.password, {{secret:UAT.password}}"},
                   {"note": 'secret:UAT.password stays; {{secret:TEST.password}} changes; secret("UAT.password") stays',
                    "items": "secret:UAT.password, {{secret:TEST.password}}"}),
    "plain_note": ("UAT.password", "UAT.password"),
    "header_rows": ([{"key": "X-Pass", "value": "{{secret:UAT.password}}"},
                     {"key": "X-Doc", "value": 'secret("UAT.password") is how code names it'}],
                    [{"key": "X-Pass", "value": "{{secret:TEST.password}}"},
                     {"key": "X-Doc", "value": 'secret("UAT.password") is how code names it'}]),
    "unreadable": ("https://orders.example.test/{{secret:UAT.password}}",
                   "https://orders.example.test/{{secret:TEST.password}}"),
    "future_call": ({"secret": "UAT.password", "headers": [{"key": "X-Pass", "value": "secret:UAT.password"}]},
                    {"secret": "UAT.password", "headers": [{"key": "X-Pass", "value": "secret:UAT.password"}]}),
}


def rewrite_flow(path):
    """A block of a type this install does not have first; then every block of REWRITTEN as written."""
    blocks = {
        "manual": {"type": "trigger.manual"},
        "mystery": {"type": "plugin.not_installed", "config": {"message": "{{secret:UAT.password}}"}},
        "typed_code": {"type": "logic.python", "config": {"inputs": ["note", "items"], "outputs": [], "code": "pass",
                                                          "use_environment": False},
                       "inputs": {"note": {"type": "text", "value": REWRITTEN["typed_code"][0]["note"]},
                                  "items": {"type": "list", "value": REWRITTEN["typed_code"][0]["items"]}}},
        "plain_note": W.note(REWRITTEN["plain_note"][0]),
        "header_rows": W.api("https://orders.example.test/h", headers=REWRITTEN["header_rows"][0]),
        "unreadable": {"type": "servers.api_request",  # (a setting this block does not have: Validate names it)
                       "config": {"url": REWRITTEN["unreadable"][0], "colour": "blue"}},
        "future_call": {"type": "servers.api_request", "version": 2,  # (saved by a newer install: Validate reads none)
                        "config": {"url": "https://orders.example.test/f", "auth": "Bearer token",
                                   **REWRITTEN["future_call"][0]}},
    }
    flow = {"blocks": blocks, "edges": [f"manual.start -> {name}.after" for name in REWRITTEN]}
    path.write_text(yaml.safe_dump(flow, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def as_rewritten(blocks: dict) -> dict:
    return {"typed_code": {hole: typed["value"] for hole, typed in blocks["typed_code"]["inputs"].items()},
            "plain_note": blocks["plain_note"]["config"]["message"],
            "header_rows": blocks["header_rows"]["config"]["headers"],
            "unreadable": blocks["unreadable"]["config"]["url"],
            "future_call": {key: blocks["future_call"]["config"][key] for key in ("secret", "headers")}}


@pytest.mark.invariant("CR-16")
def test_the_rewrite_renames_only_what_names_the_card_in_typed_values_messages_header_rows_and_unreadable_blocks(
        win, monkeypatch, tmp_path, boxes):
    """UAT renamed TEST with "also rewrite the open flows", in a flow that has a block of a type not installed first:
    a typed text and list, Inspect's message, an API Request's header rows and an API Request with a setting it does
    not have each hold TEST where they held {{secret:UAT.password}}; secret:UAT.password and secret("UAT.password")
    written as text there, a message that is only UAT.password, and the Stored secret and secret: header row of an API
    Request saved by a newer version (whose settings Validate does not read), stay as they were."""
    W.cards(win, {"UAT": {"rows": {"password": None}}}, {"UAT.password": "made-up-uat-pass-36"})
    tab = win.open_flow(rewrite_flow(tmp_path / "rewrite_flow.yaml"))
    assert tab
    W.asked_names(monkeypatch, "TEST")
    box = W.answer_next_box("rewrite")

    def steps(window):
        W.choose(window, "UAT")
        W.press(window, "Rename…")
        W.wait_for(lambda: box, 10, "renaming a card an open flow uses showed no warning")
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.unsaved(win, tab)
    assert as_rewritten(W.save_tab(win, tab)) == {name: after for name, (_, after) in REWRITTEN.items()}


@pytest.mark.invariant("CR-16")
def test_the_rewrite_renames_the_reference_in_a_message_that_starts_with_the_cards_name(win, monkeypatch, tmp_path,
                                                                                      boxes):
    """UAT renamed TEST with "also rewrite the open flows": Inspect's message "UAT.password is shown as
    {{secret:UAT.password}}", and one starting "secret:UAT.password", each name TEST in their {{secret:...}}: no
    reference to UAT is left in them."""
    W.cards(win, {"UAT": {"rows": {"password": None}}}, {"UAT.password": "made-up-uat-pass-37"})
    messages = {"named_first": "UAT.password is shown as {{secret:UAT.password}}",
                "prefixed_first": "secret:UAT.password is how a header names {{secret:UAT.password}}"}
    tab = win.open_flow(W.flow_file(tmp_path / "messages_flow.yaml",
                                    {name: W.note(text) for name, text in messages.items()}))
    assert tab
    W.asked_names(monkeypatch, "TEST")
    box = W.answer_next_box("rewrite")

    def steps(window):
        W.choose(window, "UAT")
        W.press(window, "Rename…")
        W.wait_for(lambda: box, 10, "renaming a card an open flow uses showed no warning")
        W.press(window, "Save")

    W.open_credentials(win, steps)

    blocks = W.save_tab(win, tab)
    shown = {name: blocks[name]["config"]["message"] for name in messages}
    assert {name: ("{{secret:TEST.password}}" in text, "{{secret:UAT.password}}" in text)
            for name, text in shown.items()} == {name: (True, False) for name in messages}, shown


# --- Validate names a missing card with its block and setting (CR-8) ---------------------------------------------------

@pytest.mark.invariant("CR-8")
def test_validate_names_a_missing_card_in_a_message_a_template_code_header_rows_and_a_connections_headers_exactly(
        env, credential_store):
    """References to the card NOPE, not listed, in Inspect's message, HTML Template's template, Python Code's code, a
    header row's name and another row's value (a third row naming none), and a header of the API connection a block
    uses: Validate gives one error line for each place, naming the block and the setting as Properties shows them."""
    listed_cards(env, credential_store)
    connections(env, shop={"kind": "api", "base_url": "https://shop.example.test",
                           "headers": [{"key": "X-Key", "value": "secret:NOPE.key"}]})
    path = write_flow(env, {
        "message_ref": {"type": "logic.inspect", "config": {"message": "{{secret:NOPE.token}}"}},
        "template_ref": {"type": "reports.html_template", "config": {"template": "<p>{{secret:NOPE.token}}</p>"}},
        "code_ref": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": 't = secret("NOPE.token")'}},
        "rows_ref": {"type": "servers.api_request", "config": {
            "url": "https://orders.example.test/r", "headers": [
                {"key": "X-{{secret:NOPE.host}}", "value": "plain"},
                {"key": "X-Pass", "value": "{{secret:NOPE.password}}"},
                {"key": "X-Plain", "value": "plain"}]}},
        "conn_ref": {"type": "servers.api_request", "config": {"url": "/orders", "connection": "shop"}},
    })

    checked = validate(env, path)

    assert [line for line in checked.stderr.splitlines() if line.startswith("error:")] == [
        f"error: message_ref: Message: {not_set('NOPE.token')}",
        f"error: template_ref: Template: {not_set('NOPE.token')}",
        f"error: code_ref: Code: {not_set('NOPE.token')}",
        f"error: rows_ref: Headers: {not_set('NOPE.host')}",
        f"error: rows_ref: Headers: {not_set('NOPE.password')}",
        f"error: conn_ref: Connection: the connection shop: {not_set('NOPE.key')}"], checked.stderr


def not_set(reference: str) -> str:
    """Validate's reason for a reference to a card that is not listed."""
    return f"secret '{reference}' is not set (run: taskloom secret set {reference})"


def user_block(env, title: str, code: str):
    """A block of your own made from a Python Code block (Create reusable block…), in the Taskloom folder's blocks."""
    write_reusable_block(Home(env.home).blocks_dir, title, "Made for this test", "",
                         {"inputs": [], "outputs": [], "use_environment": False, "code": code})


@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-8")
def test_validate_names_a_secret_call_without_a_written_name_and_a_missing_card_in_code_and_in_a_block_of_your_own(
        env, credential_store):
    """secret(name) with the name in a variable, in Python Code and in a block of your own made from one: Validate
    says on which line and how to write it; secret("NOPE.token") in a block of your own: Validate names its Code."""
    listed_cards(env, credential_store)
    user_block(env, "Fetch by name", 'name = "UAT.token"\nvalue = secret(name)')
    user_block(env, "Fetch nope", 'value = secret("NOPE.token")')
    path = write_flow(env, {
        "bad_code": {"type": "logic.python", "config": {"inputs": [], "outputs": [],
                                                         "code": 'name = "UAT.token"\nvalue = secret(name)'}},
        "mine_bad": {"type": "user.fetch_by_name", "config": {}},
        "mine_ref": {"type": "user.fetch_nope", "config": {}},
    })

    checked = validate(env, path)

    errors = [line for line in checked.stderr.splitlines() if line.startswith("error:")]
    how = 'Code: secret() on line 2 needs the name written in the code as text, e.g. secret("UAT.password")'
    assert f"error: bad_code: {how}" in errors and f"error: mine_bad: {how}" in errors, checked.stderr
    assert lines_with("\n".join(errors), "error: mine_ref: Code:", "NOPE"), checked.stderr
    assert "Traceback" not in checked.stderr, checked.stderr


# --- a run's Log, output and tables show a value only as *** (CR-5) ---------------------------------------------------

QUOTED = "Pq3'Wren"  # Python writes it \' inside a text that holds both quotes
DOUBLE = 'Rv5"Kite'  # JSON writes it \"
ACCENTED = 'Sé9"Lark'  # JSON written with ensure_ascii=False keeps é
CONTROL = "Bx2'\x7fMoth"  # in a text with no ": Python writes it ' and \x7f, JSON \u007f
INNER, OUTER = "pass-7f3x", "my-pass-7f3x-long"  # one holds the other


@pytest.fixture
def awkward(env, credential_store):
    write_cards(env, {"ODD": {"rows": {"quoted": None, "double": None, "accented": None, "control": None,
                                       "inner": None, "outer": None}}})
    plant(credential_store, {"ODD.quoted": QUOTED, "ODD.double": DOUBLE, "ODD.accented": ACCENTED,
                             "ODD.control": CONTROL, "ODD.inner": INNER, "ODD.outer": OUTER})


@pytest.mark.invariant("CR-5")
def test_a_value_printed_as_python_or_json_writes_it_or_holding_another_shows_as_exactly_three_stars(env, awkward):
    """Each value printed as Python writes a text inside a list (with \\' or \\x7f), as JSON writes it (\\", é as
    \\u00e9 or kept), and a value that holds another one: the Log shows each line with *** in the value's place, and
    nothing of it."""
    code = ('import json\n'
            'print([secret("ODD.quoted") + \' "q"\'])\n'
            'print([secret("ODD.control") + "!"])\n'
            'print(json.dumps({"k": secret("ODD.double")}))\n'
            'print(json.dumps({"a": secret("ODD.accented")}))\n'
            'print(json.dumps({"u": secret("ODD.accented")}, ensure_ascii=False))\n'
            'print("outer:", secret("ODD.outer"), "inner:", secret("ODD.inner"))\n')
    path = write_flow(env, {"show": {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": code}}})

    result = run(env, path)

    assert result.returncode == 0, told(env, result)
    log = log_of(env, "show")
    for line in ("['*** \"q\"']", '["***!"]', '{"k": "***"}', '{"a": "***"}', '{"u": "***"}', "outer: *** inner: ***"):
        assert line in log.splitlines(), log
    for leak in ("Wren", "Moth", "Kite", "Lark", "7f3x"):
        assert leak not in told(env, result), told(env, result)


@pytest.mark.invariant("CR-5")
def test_a_table_holding_a_value_that_holds_another_is_kept_with_three_stars_after_a_table_without_text(env, awkward):
    """A Python Code block's output: a dict of a table of numbers only, then a table whose cell holds a value that
    holds another value (both got by the block): the run keeps that cell as *** and no file holds either value."""
    code = ('import polars as pl\n'
            'inner = secret("ODD.inner")\n'
            'result = {"counts": pl.DataFrame({"n": [1, 2]}),\n'
            '          "notes": pl.DataFrame({"name": ["svc"], "token": [secret("ODD.outer")]})}\n')
    path = write_flow(env, {"make": {"type": "logic.python",
                                     "config": {"inputs": [], "outputs": ["result"], "code": code}}})

    result = run(env, path)

    assert result.returncode == 0, told(env, result)
    tables = {tuple(pl.read_parquet(p).columns): table_cells(p) for p in env.root.rglob("*.parquet")}
    assert tables.get(("name", "token")) == ["svc", "***"], tables
    assert stored_with(env, OUTER, INNER) == []


@pytest.mark.invariant("CR-5")
def test_a_table_of_a_file_you_have_passed_on_before_a_new_table_keeps_the_new_tables_cell_as_three_stars(
        env, awkward):
    """A Python Code block's output: a dict of a table of a Parquet file you have (Table(Path(...)), outside the run's
    folder), then a new table whose cell holds a stored secret: the run keeps that cell as *** and no file holds the
    value."""
    yours = env.root / "data" / "counts.parquet"
    yours.parent.mkdir()
    pl.DataFrame({"n": ["one", "two"]}).write_parquet(yours)
    code = ('from pathlib import Path\n'
            'import polars as pl\n'
            f'result = {{"old": Table(Path({yours.as_posix()!r})),\n'
            '          "notes": pl.DataFrame({"name": ["svc"], "token": [secret("ODD.outer")]})}\n')
    path = write_flow(env, {"make": {"type": "logic.python",
                                     "config": {"inputs": [], "outputs": ["result"], "code": code}}})

    result = run(env, path)

    assert result.returncode == 0, told(env, result)
    kept = [table_cells(p) for p in Home(env.home).runs_dir.rglob("*.parquet")]
    assert ["svc", "***"] in kept, kept
    assert stored_with(env, OUTER) == []
