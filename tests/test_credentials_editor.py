"""Credentials on the PC (docs/promises/credentials.yaml) in the editor, through what a person does there: the
Connections window choosing a row of a card for a connection's password, token or key (K1), Import from cURL storing
each secret as a row of the card typed (K2), a store that cannot be used said in a message (CR-13), a card made to
take its values from another card in the Credentials window, used by the next run (K4), a value hidden wherever the
editor shows or keeps what it got (CR-5: the Log, Data preview, hovers, the crash log, Copy executed query, the flow
file, a Save & Commit), and the Credentials window's Save refusing while a name being typed is refused or its rename
question was cancelled (CR-14, CR-16). The real editor (offscreen), the real `taskloom` program it starts, the tests'
own credential store (tests/credential_store, never the real one), an empty Git config of the test's own and a web
server on this computer (never the internet). Made-up values only.

What the tests take as given, beside the contracts of tests/test_credentials_window.py (the Credentials window),
tests/test_curl_import.py (Import from cURL) and tests/test_save_and_commit.py (Save & Commit):
- The Connections window (Tools > "Connections…") has, beside the password, token or key of the connection shown, a
  QComboBox named "credential" offering every row of the cards listed in credentials.yaml as CARD.key (and an item
  for typing a new value in the password box instead). Choosing a row there makes the connection's setting
  secret:CARD.key; nothing is stored, and no value shows.
- A value cannot be stored because the credential store cannot be used (the tests' store in its unavailable mode):
  the Connections window's Save and Import from cURL's Fill in each say so in a message box whose text has "credential
  store" and the store's reason (the tests' store says "locked"); nothing is stored and no file changes.
- A value of 4 characters or more shows as *** wherever the editor shows or keeps text that held it.
"""

import hashlib
import json
import os
import subprocess
import sys

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QInputDialog, QMessageBox, QPushButton  # noqa: E402

import test_credentials_sinks  # noqa: E402
import test_credentials_window as W  # noqa: E402
from test_credentials_store import PROXY_VARIABLES, entries  # noqa: E402
from tests import test_save_and_commit  # noqa: E402
from tests.test_copy_executed_query import copy_query  # noqa: E402
from tests.test_curl_import import fill_in, new_api  # noqa: E402
from tests.test_long_output_rest import as_seen  # noqa: E402
from tests.test_paths_editor_editing import clean  # noqa: E402

from taskloom.config import Home  # noqa: E402
from taskloom.editor.dialogs import ConnectionsDialog  # noqa: E402

web = test_credentials_sinks.web  # (a web server on this computer: .base, .seen)
git_config = test_save_and_commit.git_config  # (autouse: the test's own empty Git config, no GIT_* variables)
open_flows = W.open_flows  # (UAT and UAT2; orders_flow, billing_flow and unrelated_flow open in the editor)

SHOP_TOKEN = "made-up-shop-token-41"
UAT_TOKEN, UAT_KEY = "made-up-uat-token-5150", "made-up-uat-key-2718"
UAT_PASSWORD, PROD_PASSWORD = "made-up-uat-pass-1", "made-up-prod-pass-2"
SHOWN = "Zq8 lantern-77"  # the value CR-5's tests look for (with a space, as a pass phrase has)


@pytest.fixture
def boxes(qtbot, monkeypatch):
    """tests/test_credentials_window.py's watch: a Credentials window opened is handed to its steps; every other box
    is noted (messages(win) gives them) and closed; a box no step asked about fails the test."""
    monkeypatch.setattr(W, "WATCH", W.Watch())
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(
        lambda *a, **k: W.WATCH.said.append("\n".join(map(str, a[1:3]))) or QMessageBox.Ok))
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(W._look)
    timer.start()
    yield W.WATCH
    timer.stop()
    assert W.WATCH.said == [], f"message boxes no step of the test expected: {W.WATCH.said}"


def no_proxy(monkeypatch):
    """Requests go straight to the test's web server, whatever proxy this computer is set up with."""
    for name in list(os.environ):
        if name.lower() in PROXY_VARIABLES:
            monkeypatch.delenv(name)


def in_connections(win, monkeypatch, steps):
    """Tools > Connections… in the editor, then steps(dialog) in the window that opens (it is not shown: its exec is
    the steps, as the editor's other tests of it do)."""
    outcome = []

    def run(dialog):
        try:
            steps(dialog)
            outcome.append(None)
        except BaseException as e:  # (raised again below, outside Qt)
            outcome.append(e)
        return 0

    monkeypatch.setattr(ConnectionsDialog, "exec", run)
    W.menu_item(win, "Tools", "Connections…").trigger()
    assert outcome, "Tools > Connections… opened no Connections window"
    if outcome[0] is not None:
        raise outcome[0]


def save_connections(dialog):
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
    W.settle()


def connections_file(win) -> dict:
    path = win.home.root / "connections.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_connections(win, **found):
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "connections.yaml").write_text(yaml.safe_dump(found, sort_keys=False), encoding="utf-8")


def python(code: str, outputs=()) -> dict:
    return {"type": "logic.python", "config": {"inputs": [], "outputs": list(outputs), "code": code,
                                               "use_environment": False}}


def flow_file(path, blocks: dict, edges=()):
    """The flow `blocks`, each without an input started by its Manual Start, and `edges`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flow = {"blocks": {"manual": {"type": "trigger.manual"}, **blocks},
            "edges": [f"manual.start -> {name}.after" for name in blocks
                      if not any(e.split("->")[1].strip().startswith(f"{name}.") for e in edges)] + list(edges)}
    path.write_text(yaml.safe_dump(flow, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def run_in_editor(win, qtbot, tab, ends: str = "success") -> int:
    """Run now in the editor; waits for the run to end and the editor to have shown it. The run's number."""
    count = len(tab.runs)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: len(tab.runs) > count and not tab.running and tab.runs[-1].status is not None,
                    timeout=300_000)
    qtbot.wait(300)  # (the editor's own follow-up: the history, the canvas)
    assert tab.runs[-1].status == ends, (tab.runs[-1].status, win.log.text.toPlainText()[-3000:])
    return tab.runs[-1].run_id


def select(tab, block: str):
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)
    W.settle()


def grid(win) -> list[str]:
    model = win.preview.view.model()
    assert model is not None, win.preview.label.text()
    return [str(model.data(model.index(r, c))) for r in range(model.rowCount()) for c in range(model.columnCount())]


def said_about_store(text: str) -> bool:
    return "credential store" in text.lower() and "locked" in text.lower()


# --- K1: the Connections window takes a row of a card ----------------------------------------------------------

@pytest.mark.invariant("CR-4")
def test_the_connections_window_picks_a_cards_row_for_a_token_stores_the_reference_and_test_connection_uses_it(
        win, monkeypatch, credential_store, web):
    """K1: a new API connection with Bearer auth; its token chosen in the window from the cards' rows (SHOP.token).
    connections.yaml holds secret:SHOP.token and no value, nothing is stored, and Save and test connection calls
    the web server with that row's value and says it worked, without the value."""
    W.cards(win, {"SHOP": {"rows": {"token": None}}, "UAT": {"rows": {"password": None}}},
            {"SHOP.token": SHOP_TOKEN, "UAT.password": UAT_PASSWORD})
    before = W.files(win, credential_store)
    no_proxy(monkeypatch)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("shop", True)))
    seen = {}

    def steps(dialog):
        next(b for b in dialog.findChildren(QPushButton) if b.text() == "New…").click()
        dialog.kind.setCurrentIndex(dialog.kind.findData("api"))
        dialog.edits["base_url"].setText(f"{web.base}/landing")
        bearer = next(i for i in range(dialog.auth.count()) if "bearer" in dialog.auth.itemText(i).lower())
        dialog.auth.setCurrentIndex(bearer)
        W.settle()
        pickers = dialog.findChildren(QComboBox, "credential")
        assert pickers, "the Connections window has no choice of a card's row (a QComboBox named credential)"
        picker = pickers[0]
        seen["offered"] = [picker.itemText(i) for i in range(picker.count())]
        chosen = picker.findText("SHOP.token")
        assert chosen >= 0, f"the rows offered do not hold SHOP.token: {seen['offered']}"
        picker.setCurrentIndex(chosen)
        picker.activated.emit(chosen)
        W.settle()
        dialog.test_button.click()
        W.wait_for(lambda: dialog.test_button.isEnabled() and "Testing" not in dialog.test_result.text(), 120,
                   "Save and test connection did not end")
        seen["result"] = dialog.test_result.text()
        save_connections(dialog)

    in_connections(win, monkeypatch, steps)

    assert {"SHOP.token", "UAT.password"} <= set(seen["offered"]), seen["offered"]
    assert "✓" in seen["result"] and SHOP_TOKEN not in seen["result"], seen["result"]
    assert any(s.headers.get("authorization") == f"Bearer {SHOP_TOKEN}" for s in web.seen), \
        [s.headers.get("authorization") for s in web.seen]
    text = (win.home.root / "connections.yaml").read_text(encoding="utf-8")
    assert SHOP_TOKEN not in text, text
    assert yaml.safe_load(text)["shop"].get("secret") == "secret:SHOP.token", text
    assert W.files(win, credential_store) == before  # (nothing stored, no card or row added)


# --- K2, and the flow file and Save & Commit (CR-5): Import from cURL stores rows of the card typed ----------------

@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-5")
@test_save_and_commit.needs_git
def test_fill_in_stores_each_secret_as_a_row_of_the_card_typed_and_the_flow_and_its_commit_hold_only_references(
        win, tmp_path, web):
    """K2: Fill in on a curl command with a Bearer token and an X-API-Key header, the names typed UAT.token and
    UAT.api_key, UAT listed already with its row host: both are rows of UAT, the store holds their values and the
    block names them (its stored secret UAT.x, its header secret:UAT.y). Saved and committed with Save & Commit: the
    flow file, the commit, the history and the editor's recovery copies hold the references and no value."""
    W.cards(win, {"UAT": {"rows": {"host": None}}}, {"UAT.host": "uat-db.example.test"})
    path = flow_file(tmp_path / "orders" / "orders.yaml", {})
    tab = win.open_flow(path)
    _, block = new_api(win, tab)

    shown, said = fill_in(win, f"curl {web.base}/landing -H 'Authorization: Bearer {UAT_TOKEN}' "
                               f"-H 'X-API-Key: {UAT_KEY}'", names=["UAT.token", "UAT.api_key"])

    assert len(shown.names()) == 2, shown
    config = tab.doc.blocks[block]["config"]
    header = next(h["value"] for h in config.get("headers") or [] if h.get("key", "").lower() == "x-api-key")
    assert header.startswith("secret:"), config
    references = {config.get("secret"): UAT_TOKEN, header[len("secret:"):]: UAT_KEY}
    assert set(references) == {"UAT.token", "UAT.api_key"}, config
    for reference, value in references.items():
        assert W.value_of(win, reference) == value, reference
    listed = W.cards_of(win)
    assert [name for name, *_ in listed] == ["UAT"], listed
    assert dict(listed[0][2]) == {"host": None, "token": None, "api_key": None}, listed

    W.settle()
    try:
        windows = test_save_and_commit.save_and_commit(win, **test_save_and_commit.ANA, message="Call the orders API")
    finally:
        clean(win)
    assert windows, "File > Save & Commit… opened no window"
    folder = path.parent
    kept = {"the flow file": path.read_text(encoding="utf-8"),
            "the commit": test_save_and_commit.git(folder, "show", "HEAD:orders.yaml"),
            "the history": test_save_and_commit.git(folder, "log", "-p", "--all"),
            **{f"recovery/{p.name}": p.read_text(encoding="utf-8", errors="replace")
               for p in (win.home.root / "recovery").glob("*.yaml")}}
    assert "UAT.token" in kept["the commit"] and "secret:UAT.api_key" in kept["the commit"], kept["the commit"]
    for where, text in kept.items():
        assert UAT_TOKEN not in text and UAT_KEY not in text, f"{where} holds a value:\n{text}"


# --- CR-13: a store that cannot be used, said in a message --------------------------------------------------------

@pytest.mark.invariant("CR-13")
def test_connections_save_with_the_store_unavailable_says_so_naming_the_reason_and_stores_nothing(
        win, monkeypatch, credential_store, boxes):
    """C4: a password typed for a mail server's connection, then Save while the credential store cannot be used: a
    message says the credential store cannot be used and why; connections.yaml and the store are as before."""
    write_connections(win, mailer={"kind": "smtp", "host": "mail.example.test", "port": 25, "username": "reports"})
    before = (win.home.root / "connections.yaml").read_bytes(), entries(credential_store)

    def steps(dialog):
        dialog.password.setText("made-up-mail-pass-9")
        monkeypatch.setenv("TEST_CREDENTIAL_STORE_MODE", "unavailable")
        save_connections(dialog)

    in_connections(win, monkeypatch, steps)
    said = W.messages(win)

    assert any(said_about_store(text) for text in said), said or "no message"
    assert ((win.home.root / "connections.yaml").read_bytes(), entries(credential_store)) == before


@pytest.mark.invariant("CR-13")
def test_fill_in_with_the_store_unavailable_says_so_naming_the_reason_and_changes_nothing(win, monkeypatch,
                                                                                         credential_store, web):
    """C4: Fill in on a curl command with a Bearer token while the credential store cannot be used: a message says
    the credential store cannot be used and why; the block, credentials.yaml and the store are as before."""
    W.cards(win, {"UAT": {"rows": {"host": None}}}, {"UAT.host": "uat-db.example.test"})
    before = W.cards_of(win), entries(credential_store)
    tab, block = new_api(win)
    config = json.dumps(tab.doc.blocks[block].get("config") or {}, sort_keys=True)
    monkeypatch.setenv("TEST_CREDENTIAL_STORE_MODE", "unavailable")
    try:
        _, said = fill_in(win, f"curl {web.base}/landing -H 'Authorization: Bearer {UAT_TOKEN}'",
                          names=["UAT.token"])
        monkeypatch.delenv("TEST_CREDENTIAL_STORE_MODE")
        assert said_about_store(said), said or "nothing said"
        assert json.dumps(tab.doc.blocks[block].get("config") or {}, sort_keys=True) == config
        assert (W.cards_of(win), entries(credential_store)) == before
    finally:
        clean(win)


# --- K4: a card made to take its values from another one, used by the next run ----------------------------------

DEFAULT_PASSWORD = python('import hashlib\nprint("DEFAULT.password is", '
                          'hashlib.sha256(secret("DEFAULT.password").encode()).hexdigest()[:16])')


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


@pytest.mark.invariant("CR-4")
def test_switching_what_a_card_takes_its_values_from_in_the_credentials_window_makes_the_next_run_use_it(
        win, qtbot, tmp_path, boxes):
    """K4: DEFAULT takes its values from UAT; a flow reads DEFAULT.password. Run, it gets UAT's; DEFAULT made to take
    its values from PROD in the Credentials window and saved, the next run gets PROD's."""
    W.cards(win, {"UAT": {"rows": {"password": None}}, "PROD": {"rows": {"password": None}},
                  "DEFAULT": {"uses": "UAT", "rows": {}}},
            {"UAT.password": UAT_PASSWORD, "PROD.password": PROD_PASSWORD})
    tab = win.open_flow(flow_file(tmp_path / "flows" / "nightly.yaml", {"read_password": DEFAULT_PASSWORD}))

    def last_said() -> str:
        return [line for line in win.log.text.toPlainText().splitlines() if "DEFAULT.password is" in line][-1]

    run_in_editor(win, qtbot, tab)
    assert fingerprint(UAT_PASSWORD) in last_said(), last_said()

    def steps(window):
        W.choose(window, "DEFAULT")
        uses = window.findChild(QComboBox, "uses")
        uses.setCurrentIndex(uses.findText("PROD"))
        W.settle()
        W.press(window, "Save")

    W.open_credentials(win, steps)
    assert W.cards_of(win)[2] == ("DEFAULT", "PROD", [])

    run_in_editor(win, qtbot, tab)
    assert fingerprint(PROD_PASSWORD) in last_said(), last_said()


# --- CR-5: what the editor shows of a run -----------------------------------------------------------------------

PRINTS = python('import os, sys\nv = secret("UAT.token")\nprint("by print: " + v)\n'
                'sys.stderr.write("by stderr: " + v + "\\n")\nsys.stderr.flush()\n'
                'sys.stdout.flush()\nos.write(1, ("by fd 1: " + v + "\\n").encode())\nlog("by log: " + v)')


@pytest.mark.invariant("CR-5")
def test_a_value_a_run_prints_or_logs_shows_only_as_stars_in_the_log_pane(win, qtbot, tmp_path):
    """A Python Code block prints the value (print, stderr, file descriptor 1) and logs it: the Log pane shows each
    line with *** in its place."""
    Home(win.home.root).set_secret("UAT.token", SHOWN)
    tab = win.open_flow(flow_file(tmp_path / "flows" / "prints.yaml", {"say_token": PRINTS}))

    run_in_editor(win, qtbot, tab)

    text = win.log.text.toPlainText()
    assert SHOWN not in text, text[-3000:]
    for how in ("by print: ", "by stderr: ", "by fd 1: ", "by log: "):
        lines = [line for line in text.splitlines() if how in line]
        assert lines and all("***" in line for line in lines), (how, text[-3000:])


TABLE = python('import polars as pl\nresult = pl.DataFrame({"name": ["svc"], "token": [secret("UAT.token")]})',
               outputs=["result"])
TEXT = python('result = "the token is " + secret("UAT.token")', outputs=["result"])


@pytest.mark.invariant("CR-5")
def test_a_value_in_a_blocks_output_shows_only_as_stars_in_data_preview_and_the_hovers(win, qtbot, tmp_path):
    """A table with the value in a cell, and a text holding it carried to an Inspect: Data preview's grid and text,
    and the hovers over the block's result, its output and the line to Inspect, show *** in its place."""
    Home(win.home.root).set_secret("UAT.token", SHOWN)
    tab = win.open_flow(flow_file(tmp_path / "flows" / "outputs.yaml",
                                  {"make_table": TABLE, "make_text": TEXT, "show_text": {"type": "logic.inspect"}},
                                  edges=["make_text.result -> show_text.value"]))

    run_in_editor(win, qtbot, tab)

    select(tab, "make_table")
    cells = grid(win)
    assert "***" in cells and SHOWN not in " ".join(cells), cells
    select(tab, "make_text")
    preview = win.preview.text.toPlainText()
    assert "***" in preview and SHOWN not in preview, preview
    node = tab.scene.nodes["make_text"]
    hovers = {"under the block": as_seen(node.footer.toolTip()),
              "its output": as_seen(node.ports["result", True].toolTip()),
              **{f"line {i}": as_seen(edge.toolTip()) for i, edge in enumerate(tab.scene.edge_items)}}
    assert any("the token is ***" in text for text in hovers.values()), hovers
    assert [where for where, text in hovers.items() if SHOWN in text] == [], hovers


@pytest.mark.invariant("CR-5")
def test_a_value_written_into_a_query_shows_only_as_stars_in_copy_executed_query(win, qtbot, tmp_path):
    """A DuckDB query with {{secret:UAT.token}} written in: Copy executed query copies it with *** in its place."""
    Home(win.home.root).set_secret("UAT.token", SHOWN)
    query = {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": "select '{{secret:UAT.token}}' as token"}}
    tab = win.open_flow(flow_file(tmp_path / "flows" / "query.yaml", {"ask_token": query}))

    run_id = run_in_editor(win, qtbot, tab)
    copied = copy_query(win, qtbot, run_id)

    assert copied == "select '***' as token", copied


# --- CR-5: the crash log ------------------------------------------------------------------------------------------

EDITOR = r'''
import json, sys, threading
from pathlib import Path

results, value = Path(sys.argv[1]), sys.argv[2]

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from taskloom.editor import dialogs, window

app = QApplication(sys.argv[:1])
boxes = []
for kind in ("warning", "information"):
    setattr(QMessageBox, kind, staticmethod(lambda parent, title, text, *a, **k: boxes.append([title, text])))
window.MainWindow.start_scheduler = lambda self, by_you=False: None  # never a real scheduler service
dialogs.ConnectionsDialog.exec = lambda self: 0  # opened, then closed with Cancel


def open_connections():  # Tools > Connections…: it shows whether the connection's password is stored
    next(w for w in app.topLevelWidgets() if isinstance(w, window.MainWindow)).edit_connections()


def broken_slot():
    raise ValueError(f"cannot read the reply: {value}")


def broken_thread():
    raise RuntimeError(f"the server answered {value}")


def in_thread():
    thread = threading.Thread(target=broken_thread, name="check_server")
    thread.start()
    thread.join()


def finish():
    results.write_text(json.dumps({"boxes": boxes}), encoding="utf-8")
    app.quit()


QTimer.singleShot(0, open_connections)
QTimer.singleShot(200, broken_slot)
QTimer.singleShot(400, in_thread)
QTimer.singleShot(1500, finish)
sys.exit(window.main())
'''


@pytest.mark.invariant("CR-5")
def test_a_value_in_an_error_the_editor_does_not_catch_shows_only_as_stars_in_crash_log_the_terminal_and_the_box(
        tmp_path):
    """The editor, started as `taskloom editor` starts it, reads a connection's stored password (the Connections
    window says one is stored); then a button's code and a background thread raise errors whose text holds it:
    crash.log, what the editor prints in its terminal and the box that tells of the error show *** in its place."""
    home = tmp_path / "home"
    home.mkdir()
    Home(home).set_secret("UAT.password", SHOWN)
    (home / "connections.yaml").write_text(yaml.safe_dump(
        {"appserver": {"kind": "ssh", "host": "server01.example.test", "username": "svc",
                       "password": "secret:UAT.password"}}), encoding="utf-8")
    script, results = tmp_path / "editor.py", tmp_path / "results.json"
    script.write_text(EDITOR, encoding="utf-8")
    env = {**os.environ, "TASKLOOM_HOME": str(home), "QT_QPA_PLATFORM": "offscreen"}  # (the test's store)

    done = subprocess.run([sys.executable, str(script), str(results), SHOWN], env=env, capture_output=True,
                          text=True, errors="replace", timeout=180)

    log = (home / "crash.log").read_text(encoding="utf-8", errors="replace") if (home / "crash.log").exists() else ""
    assert results.exists(), f"the editor did not keep running to its end:\n{done.stderr[-3000:]}\n{log}"
    told = [text for _, text in json.loads(results.read_text(encoding="utf-8"))["boxes"]]
    for where, text in {"crash.log": log, "the terminal": done.stderr,
                        "the boxes": "\n".join(t for t in told if "cannot read" in t or "server answered" in t)
                        }.items():
        assert "cannot read the reply: ***" in text and "the server answered ***" in text, f"{where}:\n{text}"
        assert SHOWN not in text, f"{where} shows the value:\n{text}"


# --- the Credentials window's Save while a name typed is refused, or its rename question was cancelled -------------

@pytest.mark.invariant("CR-14")
@pytest.mark.parametrize("name", ["pass word", "pass.word", "HOST"], ids=["a space", "a dot", "another case"])
def test_save_clicked_while_a_refused_key_is_being_typed_saves_nothing_and_keeps_the_window_open(
        win, credential_store, boxes, name):
    """UAT's host given a new value, then a key typed in UAT's empty row, not left with Tab, and Save clicked with the
    mouse: the name is refused in a message, the window stays open (the change can still be saved), and nothing is
    stored, neither the name nor the new value."""
    W.cards(win, {"UAT": {"rows": {"host": None}}}, {"UAT.host": "uat-db.example.test"})
    before = W.files(win, credential_store)
    seen = {}

    def steps(window):
        W.choose(window, "UAT")
        W.type_into(W.value_box(W.row(window, "host")), "uat-db2.example.test")
        edit = W.key_box(W.rows(window)[-1])
        QTest.mouseClick(edit, Qt.LeftButton)
        QTest.keyClicks(edit, name)
        save = next(b for b in window.findChildren(QPushButton) if W.plain(b.text()) == "Save" and b.isVisible())
        QTest.mouseClick(save, Qt.LeftButton)
        W.settle()
        seen["said"], seen["open"] = W.messages(win), window.isVisible()

    W.open_credentials(win, steps)

    assert seen["said"], "Save with a refused name being typed said nothing"
    assert W.files(win, credential_store) == before
    assert seen["open"], "the window closed"


@pytest.mark.invariant("CR-16")
def test_save_clicked_when_the_question_about_renaming_a_used_key_is_cancelled_saves_nothing_and_keeps_the_window_open(
        win, credential_store, open_flows, boxes):
    """UAT's host given a new value, then its key password, used by orders_flow, typed over as pass_word and Save
    clicked with the mouse: the question naming orders_flow is closed without an answer; the window stays open,
    nothing is stored (neither the name nor the new value) and no flow changes."""
    paths, tabs = open_flows
    before = W.files(win, credential_store), paths["orders"].read_bytes()
    seen = {}

    def steps(window):
        W.choose(window, "UAT")
        W.type_into(W.value_box(W.row(window, "host")), "uat-db2.example.test")
        edit = W.key_box(W.row(window, "password"))
        QTest.mouseClick(edit, Qt.LeftButton)
        QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(edit, "pass_word")
        save = next(b for b in window.findChildren(QPushButton) if W.plain(b.text()) == "Save" and b.isVisible())
        QTest.mouseClick(save, Qt.LeftButton)
        W.wait_for(lambda: W.WATCH.said, 10, "renaming a key open flows use asked nothing")
        W.settle()
        seen["said"], seen["open"] = W.messages(win), window.isVisible()

    W.open_credentials(win, steps)

    assert any("orders_flow" in text for text in seen["said"]), seen["said"]
    assert (W.files(win, credential_store), paths["orders"].read_bytes()) == before
    assert not any(W.unsaved(win, tab) for tab in tabs.values())
    assert seen["open"], "the window closed"
