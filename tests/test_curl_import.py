"""Import from cURL, in the API Request block's Properties: a curl command pasted as documentation or a browser's Copy
as cURL writes it (bash or Windows cmd style), then Fill in, fills the block's request from it; the secrets in it are
stored as stored secrets, never in the flow; a request already filled in is replaced only after Approve; the import
is one Undo step; it says what it filled and what it could not use, and refuses text that is not a curl command.

Through the real editor (the `win` fixture): the API Request added from the Blocks list, the command pasted into the
section's code box, Fill in clicked and every window it opens answered; then the flow saved and run with the real
`taskloom run`, what each request carried read on the side of a web server on this computer started by the test
(never the internet). Each test names the point of the specification (1-6) it protects.

What the tests take as given (the specification leaves it to the change):
- the section is a box (QGroupBox) titled "Import from cURL", as Properties' "Retry and timeout" box is, holding the
  code box (an editable text box, coloured as the Python and SQL ones) and a button "Fill in";
- what Fill in says (what it filled, what it could not use, why it refuses the text) is shown in a message box, in
  the section itself, in Properties' message line or in the window's status bar;
- the confirmation is a message box (or a window) with the buttons Approve and Cancel; a secret's name is asked in an
  input box (QInputDialog, or a window with one text field) that holds the name it suggests;
- the API Request (built by another item of this release) shows its Params and Headers as tables, its Auth and Body
  kinds in lists (combo boxes) and its body in a text box, and has a setting labelled "Follow redirects";
- the tests' credential store is a file in the Taskloom folder (the keyring is the fail one): read with
  Home.get_secret, and secrets.yaml for "nothing was stored".
"""

import base64
import copy
import datetime
import email
import email.policy
import html
import ipaddress
import json
import os
import re
import sqlite3
import ssl
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication,  # noqa: E402
                               QCheckBox, QComboBox, QDialog, QGroupBox, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QWidget)

from taskloom.block import setting_label  # noqa: E402
from tests.test_code_editor import color  # noqa: E402
from tests.test_editor import add_from_palette  # noqa: E402
from tests.test_paths_editor_editing import clean, menu, select_only, settle, taskloom  # noqa: E402

API, TITLE = "servers.api_request", "API Request"
SECTION, FILL = "Import from cURL", "Fill in"
CSV = b"region,total\nnorth,3\n"


# --- the editor ---------------------------------------------------------------------------------------------------

def plain(text: str) -> str:
    """Text as it reads on the screen: without HTML tags, entities read, spaces and lines run together."""
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", text or "")).split())


def new_api(win, tab=None):
    """An API Request added from the Blocks list (to a new flow, or to `tab`) and selected: (its tab, its name)."""
    if tab is None:
        tab = win.new_flow()
        settle()
    assert win.palette_panel.tree.findItems(TITLE, Qt.MatchExactly | Qt.MatchRecursive), \
        f"there is no {TITLE} in the Blocks list"
    before = set(tab.doc.blocks)
    add_from_palette(win, TITLE)
    settle()
    (added,) = set(tab.doc.blocks) - before
    select_only(tab, added)
    return tab, added


def section(win) -> QGroupBox:
    boxes = [b for b in win.properties.widget().findChildren(QGroupBox) if plain(b.title()).replace("&", "") == SECTION]
    assert len(boxes) == 1, (f"no {SECTION!r} section in the {TITLE}'s Properties (its boxes: "
                             f"{[b.title() for b in win.properties.widget().findChildren(QGroupBox)]})")
    return boxes[0]


def import_box(win) -> QPlainTextEdit:
    boxes = [e for e in section(win).findChildren(QPlainTextEdit) if not e.isReadOnly()]
    assert len(boxes) == 1, f"{len(boxes)} code boxes to paste into in {SECTION!r}"
    return boxes[0]


def fill_button(win) -> QAbstractButton:
    buttons = [b for b in section(win).findChildren(QAbstractButton) if plain(b.text()).replace("&", "") == FILL]
    assert len(buttons) == 1, f"{len(buttons)} {FILL!r} buttons in {SECTION!r}"
    return buttons[0]


class Shown(list):
    """Every window Fill in opened, in order: {"kind": "box" (a message or question), "title", "text" (what it says,
    under its title), "buttons"} or {"kind": "name" (asking a secret's name), "text" (its title and question),
    "suggested"}."""

    def questions(self) -> list[dict]:
        return [s for s in self if s["kind"] == "box" and len(s["buttons"]) > 1]

    def names(self) -> list[dict]:
        return [s for s in self if s["kind"] == "name"]


STANDARD = {QMessageBox.Ok: "OK", QMessageBox.Yes: "Yes", QMessageBox.No: "No", QMessageBox.Cancel: "Cancel",
            QMessageBox.Apply: "Apply", QMessageBox.Discard: "Discard"}


def _static_box(shown, approve, kind, parent, title, text, buttons=None, *rest, **kw):
    """QMessageBox.question / warning / information / critical: noted and answered (a question: approved when it
    offers Yes or OK and `approve`, else cancelled)."""
    buttons = kw.get("buttons", buttons)
    if buttons is None:
        buttons = QMessageBox.Yes | QMessageBox.No if kind == "question" else QMessageBox.Ok
    offered = [b for b in STANDARD if buttons & b]
    shown.append({"kind": "box", "title": title, "text": plain(text), "buttons": [STANDARD[b] for b in offered]})
    yes = [b for b in offered if b in (QMessageBox.Yes, QMessageBox.Ok)]
    no = [b for b in offered if b in (QMessageBox.Cancel, QMessageBox.No)]
    return (yes if approve else no or [QMessageBox.Cancel])[0] if kind == "question" else QMessageBox.Ok


def _answer(shown, approve, names, dialog):
    """A window waiting for an answer: noted and answered."""
    fields = [e for e in dialog.findChildren(QLineEdit) if e.isVisible()]
    if isinstance(dialog, QInputDialog) or (not isinstance(dialog, QMessageBox) and len(fields) == 1):
        asking = dialog.labelText() if isinstance(dialog, QInputDialog) else " ".join(
            label.text() for label in dialog.findChildren(QLabel) if label.isVisible())
        suggested = dialog.textValue() if isinstance(dialog, QInputDialog) else fields[0].text()
        shown.append({"kind": "name", "text": plain(f"{dialog.windowTitle()} {asking}"), "suggested": suggested})
        if names:
            name = names.pop(0)
            dialog.setTextValue(name) if isinstance(dialog, QInputDialog) else fields[0].setText(name)
        dialog.accept()
        return
    if isinstance(dialog, QMessageBox):
        text = " ".join([dialog.text(), dialog.informativeText(), dialog.detailedText()])
        buttons = {plain(b.text()).replace("&", ""): b for b in dialog.buttons()}
    else:
        text = " ".join(label.text() for label in dialog.findChildren(QLabel) if label.isVisible())
        buttons = {plain(b.text()).replace("&", ""): b for b in dialog.findChildren(QPushButton) if b.isVisible()}
    shown.append({"kind": "box", "title": dialog.windowTitle(), "text": plain(text), "buttons": list(buttons)})
    wanted = "Approve" if approve else "Cancel"
    if wanted in buttons:
        buttons[wanted].click()
    elif len(buttons) == 1:  # a message: closed
        next(iter(buttons.values())).click()
    if dialog.isVisible():
        dialog.reject()


@contextmanager
def answering(win, approve: bool = True, names=()):
    """While in it, every window opened is noted in the list it gives and answered: a question by clicking Approve
    (`approve`) or Cancel, a message by closing it, a secret's name by keeping the name suggested or, while there
    are, typing the next of `names`."""
    shown, names, watching = Shown(), list(names), [True]

    def watch():
        if not watching:
            return
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                _answer(shown, approve, names, widget)
        QTimer.singleShot(10, win, watch)

    with pytest.MonkeyPatch.context() as patch:
        for kind in ("question", "warning", "information", "critical"):
            patch.setattr(QMessageBox, kind, staticmethod(
                lambda *a, _kind=kind, **k: _static_box(shown, approve, _kind, *a, **k)))
        QTimer.singleShot(0, win, watch)
        try:
            yield shown
        finally:
            watching.clear()


def on_screen(win) -> list[str]:
    """What the section, Properties' message line and the status bar say now."""
    texts = [win.statusBar().currentMessage()]
    error = getattr(win.properties, "error", None)
    if error is not None and error.isVisible():
        texts.append(error.text())
    boxes = [b for b in win.properties.widget().findChildren(QGroupBox) if plain(b.title()).replace("&", "") == SECTION]
    texts += [label.text() for box in boxes for label in box.findChildren(QLabel) if label.isVisible()]
    return [plain(t) for t in texts if plain(t)]


def fill_in(win, text: str, approve: bool = True, names=()) -> tuple[Shown, str]:
    """Paste `text` into the Import from cURL box and click Fill in, answering what it asks (see answering): (the
    windows it opened, what it said: those windows' texts, not their titles, and what is newly said in the section,
    Properties' message line and the status bar)."""
    import_box(win).setPlainText(text)
    settle()
    before = on_screen(win)
    with answering(win, approve, names) as shown:
        fill_button(win).click()
        QTest.qWait(200)  # (a window it opens a moment later is answered too)
    said = [s["text"] for s in shown if s["kind"] == "box"] + [t for t in on_screen(win) if t not in before]
    return shown, "\n".join(said)


def combo_texts(win) -> list[str]:
    return [c.currentText() for c in win.properties.widget().findChildren(QComboBox)]


def chosen(win, word: str) -> bool:
    """Whether a list in Properties (e.g. Auth, Body) shows a choice with this word in it."""
    return any(re.search(rf"\b{word}\b", text, re.IGNORECASE) for text in combo_texts(win))


def table_rows(win) -> list[list[str]]:
    """The rows of every table in Properties, each as the texts of its cells that have one."""
    rows = []
    for view in win.properties.widget().findChildren(QAbstractItemView):
        model = view.model()
        for r in range(model.rowCount() if model is not None else 0):
            cells = []
            for c in range(model.columnCount()):
                index = model.index(r, c)
                widget = view.indexWidget(index)
                text = widget.text() if isinstance(widget, QLineEdit) else str(model.data(index) or "")
                if text.strip():
                    cells.append(text.strip())
            rows.append(cells)
    return rows


def has_row(rows, key: str, value: str) -> bool:
    return any(key in row and row.index(key) + 1 < len(row) and row[row.index(key) + 1] == value for row in rows)


def text_boxes(win) -> list[str]:
    """The texts of Properties' text boxes, but the Import from cURL one."""
    pasted = import_box(win)
    return [e.toPlainText() for e in win.properties.widget().findChildren(QPlainTextEdit) if e is not pasted]


def setting_key(win, label: str) -> str:
    keys = {setting_label(key, f): key for key, f in win.registry.get(API).config.items()}
    assert label in keys, f"the {TITLE} has no setting {label!r} (its settings: {sorted(keys)})"
    return keys[label]


def run_flow(win, tab, path, *blocks):
    """Each of `blocks` after the flow's Manual Start, the flow saved to `path` and run with `taskloom run`."""
    for block in blocks:
        tab.doc.connect("start", "start", block, "after")
    tab.doc.save(path)
    return taskloom(win, "run", path)


def output(result) -> str:
    return (result.stdout or "")[-3000:] + (result.stderr or "")[-3000:]


def block_statuses(win) -> dict[str, str]:
    """The blocks of the last run, as its history keeps them: {block: status}."""
    with sqlite3.connect(win.home.root / "history.db") as db:
        return dict(db.execute("SELECT block, status FROM blocks WHERE run_id = (SELECT MAX(id) FROM runs)"))


def stored_text(win) -> str:
    """Everything the tests' credential store holds (its file), '' when nothing was ever stored."""
    store = win.home.root / "secrets.yaml"
    return store.read_text(encoding="utf-8") if store.exists() else ""


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# --- the web server -----------------------------------------------------------------------------------------------

class Request(NamedTuple):
    method: str
    path: str
    query: dict
    headers: object  # (http.client.HTTPMessage: get / get_all by name, in any case)
    body: bytes


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # (a client that went away, e.g. after its timeout)


class Site:
    """A web server on 127.0.0.1 for the test (HTTPS with `tls`): notes each request in `seen` and answers 200 with
    the text ok; /old answers with a redirect to /landed, /pause after 1.5 s, /slow only once the test is done (at
    most 40 s later)."""

    def __init__(self, tls: ssl.SSLContext | None = None):
        self.seen, self.lock, self.release = [], threading.Lock(), threading.Event()
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                site._serve(self)

            do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_GET

            def log_message(self, *args):
                pass

        self.server = _Server(("127.0.0.1", 0), Handler)
        if tls is not None:
            self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.base = f"{'https' if tls else 'http'}://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _serve(self, handler):
        if "chunked" in (handler.headers.get("Transfer-Encoding") or "").lower():
            body = b""
            while (size := int(handler.rfile.readline().split(b";")[0].strip() or b"0", 16)) > 0:
                body += handler.rfile.read(size)
                handler.rfile.readline()
            handler.rfile.readline()
        else:
            body = handler.rfile.read(int(handler.headers.get("Content-Length") or 0))
        url = urlsplit(handler.path)
        with self.lock:
            self.seen.append(Request(handler.command, url.path, parse_qs(url.query, keep_blank_values=True),
                                     handler.headers, body))
        extra = []
        if url.path == "/old":
            status, extra = 302, [("Location", f"{self.base}/landed")]
        else:
            status = 200
            if url.path == "/slow":
                self.release.wait(40)
            elif url.path == "/pause":
                time.sleep(1.5)
        answer = b"ok"
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.send_header("Content-Length", str(len(answer)))
            for name, value in extra:
                handler.send_header(name, value)
            handler.end_headers()
            if handler.command != "HEAD":
                handler.wfile.write(answer)
        except OSError:
            pass  # (the client went away)

    def requests(self) -> list[Request]:
        with self.lock:
            return list(self.seen)

    def only(self) -> Request:
        seen = self.requests()
        assert len(seen) == 1, f"{len(seen)} requests: {[(r.method, r.path) for r in seen]}"
        return seen[0]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


def self_signed(folder) -> ssl.SSLContext:
    """A server's TLS settings with a certificate for 127.0.0.1 that no one vouches for (as on a test server)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = folder / "server.crt", folder / "server.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    return context


def multipart(request: Request) -> dict:
    """A multipart/form-data body's parts: {name: (file name or None, content)}."""
    message = email.message_from_bytes(b"Content-Type: " + request.headers["Content-Type"].encode() + b"\r\n\r\n"
                                       + request.body, policy=email.policy.HTTP)
    assert message.is_multipart(), f"not a multipart body: {request.headers['Content-Type']}"
    return {part.get_param("name", header="content-disposition"): (part.get_filename(), part.get_payload(decode=True))
            for part in message.iter_parts()}


def check(request: Request, expected: dict):
    """The request is what `expected` says: method (GET), path, query ({}), headers (each exactly once, with that
    value), content_type (how its Content-Type starts), and its body: json, form (url-encoded), multipart, or body
    (b'')."""
    assert request.method == expected.get("method", "GET")
    assert request.path == expected["path"]
    assert request.query == expected.get("query", {})
    for name, value in expected.get("headers", {}).items():
        assert request.headers.get_all(name) == [value], f"{name}: {request.headers.get_all(name)}"
    if "content_type" in expected:
        assert (request.headers.get("Content-Type") or "").startswith(expected["content_type"]), \
            request.headers.get("Content-Type")
    if "json" in expected:
        assert json.loads(request.body) == expected["json"], request.body
    elif "form" in expected:
        assert parse_qs(request.body.decode("utf-8"), keep_blank_values=True) == expected["form"], request.body
    elif "multipart" in expected:
        assert multipart(request) == expected["multipart"]
    else:
        assert request.body == expected.get("body", b"")


@contextmanager
def editing(win):
    """The test's work in the editor; after it no unsaved changes are left (closing the window must not ask)."""
    try:
        yield
    finally:
        clean(win)


def one(items: list, what: str):
    assert len(items) == 1, f"{len(items)} {what}: {items}"
    return items[0]


def json_of(text: str):
    """The JSON a body box shows: as written, or with its braces doubled as other settings' are, so that no {name}
    is read as a placeholder (which of the two is left to the change); None if not JSON."""
    for written in (text, text.replace("{{", "{").replace("}}", "}")):
        try:
            return json.loads(written)
        except ValueError:
            pass
    return None


# --- 1. the section: at the top, a code box that colours curl, Fill in ------------------------------------------

def above_it(win, box: QGroupBox, block: str) -> list[str]:
    """The visible inputs of Properties above the top of `box`, but the block's name (and those in the box)."""
    body = win.properties.widget()
    top = box.mapTo(body, QPoint(0, 0)).y()
    inputs = (QLineEdit, QComboBox, QAbstractSpinBox, QCheckBox, QPlainTextEdit, QAbstractItemView, QAbstractButton)
    return [f"{type(w).__name__} {getattr(w, 'text', lambda: '')()!r}" for w in body.findChildren(QWidget)
            if isinstance(w, inputs) and w.isVisible() and not box.isAncestorOf(w)
            and not (isinstance(w, QLineEdit) and w.text() == block) and w.mapTo(body, QPoint(0, 0)).y() < top]


def test_import_from_curl_is_a_section_at_the_top_of_the_properties_with_a_code_box_and_fill_in_always_shown(win):
    """Spec 1: on an API Request just added and on one filled in, Properties shows the section Import from cURL
    (not folded away) above all of the block's settings, with a code box to paste into and a Fill in button."""
    with editing(win):
        tab, api = new_api(win)
        added = copy.deepcopy(tab.doc.data)
        for when in ("as added", "once filled in"):
            box = section(win)
            assert box.isVisible() and (not box.isCheckable() or box.isChecked()), f"{SECTION} is not shown {when}"
            assert import_box(win).isVisible() and fill_button(win).isVisible(), when
            assert above_it(win, box, api) == [], f"{when}, these are above {SECTION}: {above_it(win, box, api)}"
            if when == "as added":
                fill_in(win, """curl -X POST https://api.example.com/v1/orders -H 'Accept: application/json' """
                             """-d '{"item": "widget"}'""")
                assert tab.doc.data != added, "Fill in filled in nothing"


def test_the_import_box_colours_curls_options_quoted_strings_and_the_url(win):
    """Spec 1 (a code editor with highlighting, as the Python and SQL ones): curl's options (short and long), its
    quoted strings (single and double quotes) and the URL are each shown in their own colour, none in the plain
    text's."""
    with editing(win):
        new_api(win)
        box = import_box(win)
        box.setPlainText("""curl -X POST https://api.example.com/v1/orders \\\n"""
                         """  -H "Accept: application/json" \\\n"""
                         """  --data-raw '{"item": "widget"}'""")
        settle()
        text = box.palette().text().color().name()
        option, string, url = color(box, "-X"), color(box, '"Accept: application/json"'), color(
            box, "https://api.example.com/v1/orders")
        assert color(box, "  -H", "-H") == option and color(box, "--data-raw") == option
        assert color(box, """'{"item": "widget"}'""") == string
        assert len({option, string, url, text}) == 4, f"options {option}, strings {string}, URL {url}, text {text}"


# --- 2, 3, 4, 6: the user's case: a curl from documentation -------------------------------------------------------

DOCUMENTATION = """curl -X POST "{base}/orders?status=open&limit=2" \\
  -H 'Content-Type: application/json' \\
  -H "Accept: application/json" \\
  -d '{"item": "widget", "count": 2}' \\
  -u 'report:s3cr3t-pass'"""


def test_a_curl_from_documentation_fills_in_the_request_it_makes_with_the_password_as_a_stored_secret(win, tmp_path):
    """Spec 2 (bash: backslash line continuations, single and double quotes), 3 (-X; the URL and its query into
    Params; -H into Headers; -d JSON into Body, recognised and pretty-printed; -u into Basic auth), 4 (the password
    is not written into the flow: Fill in asks for a secret name, suggesting one, stores the password under it, and
    the block uses it) and 6 (it says what it filled): the user's case, on a block just added (nothing to replace:
    no confirmation). Saved and run, the block sends the request the curl command sends."""
    path = tmp_path / "orders.yaml"
    with editing(win), Site() as site:
        tab, api = new_api(win)
        shown, said = fill_in(win, DOCUMENTATION.replace("{base}", site.base))

        assert shown.questions() == [], f"asked to confirm on a block with nothing to replace: {shown.questions()}"
        # The password only: the user name and the other headers are not secrets.
        asked = one(shown.names(), "secret names asked")
        assert "secret" in asked["text"].lower() and asked["suggested"].strip(), asked
        for words in (("url",), ("param", "query"), ("header",), ("body",), ("auth", "basic")):
            assert any(w in said.lower() for w in words), f"it does not say it filled {words[0]}: {said!r}"
        rows = table_rows(win)
        assert has_row(rows, "status", "open") and has_row(rows, "limit", "2"), f"the query is not in Params: {rows}"
        pretty = [t for t in text_boxes(win) if json_of(t) == {"item": "widget", "count": 2}]
        assert any(len(t.strip().splitlines()) > 1 and all(line.startswith((" ", "\t"))
                                                           for line in t.strip().splitlines()[1:-1]) for t in pretty), \
            f"the body is not shown as pretty-printed JSON: {text_boxes(win)}"
        assert chosen(win, "json"), f"Body is not JSON: {combo_texts(win)}"
        assert chosen(win, "basic"), f"Auth is not Basic: {combo_texts(win)}"

        result = run_flow(win, tab, path, api)
        assert result.returncode == 0, output(result)
        check(site.only(), {"method": "POST", "path": "/orders", "query": {"status": ["open"], "limit": ["2"]},
                            "headers": {"Accept": "application/json", "Authorization": basic("report", "s3cr3t-pass")},
                            "content_type": "application/json", "json": {"item": "widget", "count": 2}})
    assert "s3cr3t-pass" not in path.read_text(encoding="utf-8")
    assert win.home.get_secret(asked["suggested"].strip()) == "s3cr3t-pass"


# --- 2: as browsers copy it; Windows cmd style ----------------------------------------------------------------------

WRITTEN = {
    "a browser's Copy as cURL (bash)": (
        """curl '{base}/orders?status=open' \\\n"""
        """  -H 'accept: application/json' \\\n"""
        """  -H 'content-type: application/json' \\\n"""
        """  -b 'theme=dark' \\\n"""
        """  -H 'sec-ch-ua: "Chromium";v="130", "Not?A_Brand";v="99"' \\\n"""
        """  -H 'user-agent: Mozilla/5.0 (X11; Linux x86_64)' \\\n"""
        """  --data-raw '{"item":"widget","count":2}'""",
        {"query": {"status": ["open"]}, "headers": {
            "Cookie": "theme=dark", "sec-ch-ua": '"Chromium";v="130", "Not?A_Brand";v="99"',
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}}),
    "a browser's Copy as cURL (cmd)": (
        """curl ^"{base}/orders?status=open^&q=red^%^20box^" ^\n"""
        """  -H ^"Accept: application/json^" ^\n"""
        """  -H ^"Content-Type: application/json^" ^\n"""
        """  --data-raw ^"^{^\\^"item^\\^":^\\^"widget^\\^",^\\^"count^\\^":2^}^" ^\n"""
        """  --compressed""",
        {"query": {"status": ["open"], "q": ["red box"]}}),
    "documentation for Windows (cmd)": (
        """curl -X POST "{base}/orders?status=open" ^\n"""
        """  -H "Accept: application/json" ^\n"""
        """  -H "Content-Type: application/json" ^\n"""
        """  -d "{\\"item\\": \\"widget\\", \\"count\\": 2}\"""",
        {"query": {"status": ["open"]}}),
}


@pytest.mark.parametrize("written", list(WRITTEN))
def test_a_curl_copied_from_a_browser_or_written_for_windows_cmd_fills_in_the_same_request(win, tmp_path, written):
    """Spec 2 (as browsers' Copy as cURL writes it, bash or cmd; Windows cmd style: ^ line continuations, ^-escaped
    characters, \\" in double quotes) with 3 (POST when there is data, -b, --compressed needing nothing): run, the
    block sends what curl sends."""
    command, expected = WRITTEN[written]
    with editing(win), Site() as site:
        tab, api = new_api(win)
        shown, said = fill_in(win, command.replace("{base}", site.base))
        assert shown.names() == [] and shown.questions() == [], list(shown)
        result = run_flow(win, tab, tmp_path / "copied.yaml", api)
        assert result.returncode == 0, output(result)
        check(site.only(), {"method": "POST", "path": "/orders", "content_type": "application/json",
                            "json": {"item": "widget", "count": 2}, **expected,
                            "headers": {"Accept": "application/json", **expected.get("headers", {})}})


# --- 3: each option ------------------------------------------------------------------------------------------------

OPTIONS = {
    "--request, --url, --header, --data-binary": (
        "curl --request PUT --url '{base}/items/7' --header 'Content-Type: text/plain' --data-binary 'line one'",
        {"method": "PUT", "path": "/items/7", "content_type": "text/plain", "body": b"line one"}),
    "-X, -H": (
        "curl -X DELETE {base}/items/7 -H 'Accept: application/json'",
        {"method": "DELETE", "path": "/items/7", "headers": {"Accept": "application/json"}}),
    "-d makes it a POST; quotes as bash reads them": (
        r'''curl {base}/notes -H 'X-Note: it'\''s fine' -d "{\"text\": \"say \\\"hi\\\"\"}"''',
        {"method": "POST", "path": "/notes", "headers": {"X-Note": "it's fine"}, "json": {"text": 'say "hi"'}}),
    "--data": (
        "curl {base}/plain --data 'a=1&b=2'",
        {"method": "POST", "path": "/plain", "body": b"a=1&b=2"}),
    "--data-raw": (
        """curl {base}/things --data-raw '{"a": [1, 2]}'""",
        {"method": "POST", "path": "/things", "json": {"a": [1, 2]}}),
    "-G turns the data into the query": (
        "curl -G {base}/search -d q=widget -d limit=5",
        {"method": "GET", "path": "/search", "query": {"q": ["widget"], "limit": ["5"]}}),
    "--get": (
        "curl --get {base}/search --data 'q=widget'",
        {"method": "GET", "path": "/search", "query": {"q": ["widget"]}}),
    "-I": ("curl -I {base}/health", {"method": "HEAD", "path": "/health"}),
    "--head": ("curl --head {base}/health", {"method": "HEAD", "path": "/health"}),
    "--json sets the JSON headers too": (
        """curl --json '{"a": 1}' {base}/things""",
        {"method": "POST", "path": "/things", "headers": {"Accept": "application/json"},
         "content_type": "application/json", "json": {"a": 1}}),
    "--data-urlencode": (
        "curl {base}/form --data-urlencode 'note=red & blue' --data-urlencode 'size=10 cm'",
        {"method": "POST", "path": "/form", "content_type": "application/x-www-form-urlencoded",
         "form": {"note": ["red & blue"], "size": ["10 cm"]}}),
    "-F, --form with a file": (
        "curl -F 'title=Weekly report' --form 'file=@{csv}' {base}/upload",
        {"method": "POST", "path": "/upload", "content_type": "multipart/form-data",
         "multipart": {"title": (None, b"Weekly report"), "file": ("report.csv", CSV)}}),
    "--user": (
        "curl --user 'nightly:pw-0042' {base}/me",
        {"path": "/me", "headers": {"Authorization": basic("nightly", "pw-0042")}}),
    "-b, -A, -e": (
        "curl -b 'theme=dark; lang=en' -A 'report-bot/1.0' -e '{base}/from' {base}/page",
        {"path": "/page", "headers": {"Cookie": "theme=dark; lang=en", "User-Agent": "report-bot/1.0",
                                      "Referer": "{base}/from"}}),
    "--cookie, --user-agent, --referer, --compressed": (
        "curl --cookie 'k=v' --user-agent 'nightly/2' --referer '{base}/start' --compressed {base}/page",
        {"path": "/page", "headers": {"Cookie": "k=v", "User-Agent": "nightly/2", "Referer": "{base}/start"}}),
}


@pytest.mark.parametrize("options", list(OPTIONS))
def test_each_curl_option_fills_in_what_makes_the_same_request(win, tmp_path, options):
    """Spec 3: the method (-X/--request, POST with data, -G/--get, -I/--head), --url, -H/--header, the body
    (-d/--data/--data-raw/--data-binary/--json, JSON or not; --json also sets the JSON headers), --data-urlencode
    and -F/--form (a form or multipart body, a file with @path), -u/--user, -b/--cookie, -A/--user-agent,
    -e/--referer, --compressed: saved and run, the block sends what curl sends (a secret's name: the one
    suggested)."""
    command, expected = OPTIONS[options]
    csv = tmp_path / "report.csv"
    csv.write_bytes(CSV)
    with editing(win), Site() as site:
        tab, api = new_api(win)
        fill_in(win, command.replace("{base}", site.base).replace("{csv}", csv.as_posix()))
        result = run_flow(win, tab, tmp_path / "options.yaml", api)
        assert result.returncode == 0, output(result)
        headers = {name: value.replace("{base}", site.base) for name, value in expected.get("headers", {}).items()}
        check(site.only(), {**expected, "headers": headers})


@pytest.mark.parametrize("option", ["-k", "--insecure"])
def test_insecure_fills_in_dont_verify_certificates(win, tmp_path, option):
    """Spec 3 (-k/--insecure into Don't verify certificates): run, the block calls an HTTPS server whose certificate
    no one vouches for (as curl -k does)."""
    with editing(win), Site(self_signed(tmp_path)) as site:
        tab, api = new_api(win)
        fill_in(win, f"curl {option} {site.base}/secure")
        result = run_flow(win, tab, tmp_path / "insecure.yaml", api)
        assert result.returncode == 0, output(result)
        check(site.only(), {"path": "/secure"})


@pytest.mark.parametrize("option", ["-L", "--location"])
def test_location_fills_in_follow_redirects(win, tmp_path, option):
    """Spec 3 (-L/--location into Follow redirects): on a block whose Follow redirects was off, run, the block
    follows the server's redirect."""
    with editing(win), Site() as site:
        tab, api = new_api(win)
        tab.doc.set_block_value(api, setting_key(win, "Follow redirects"), False)
        settle()
        fill_in(win, f"curl {option} {site.base}/old")
        result = run_flow(win, tab, tmp_path / "redirect.yaml", api)
        assert result.returncode == 0, output(result)
        assert [r.path for r in site.requests()] == ["/old", "/landed"]


def test_max_time_fills_in_the_timeout_in_seconds(win, tmp_path):
    """Spec 3 (-m/--max-time into the timeout): -m 1 on a server that does not answer fails the block about a second
    later (well before the server would answer, 40 s); --max-time 10 on one answering after 1.5 s succeeds (the
    time is in seconds)."""
    with editing(win), Site() as site:
        tab, short = new_api(win)
        fill_in(win, f"curl -m 1 {site.base}/slow")
        tab, patient = new_api(win, tab)
        fill_in(win, f"curl --max-time 10 {site.base}/pause")
        started = time.monotonic()
        result = run_flow(win, tab, tmp_path / "timeouts.yaml", short, patient)
        took = time.monotonic() - started
        assert sorted(r.path for r in site.requests()) == ["/pause", "/slow"]
    statuses = block_statuses(win)
    assert (statuses.get(short), statuses.get(patient)) == ("failed", "success"), (statuses, output(result))
    assert took < 20, f"the run took {took:.1f} s: the 1 s timeout was not in effect"


# --- 4: secrets -----------------------------------------------------------------------------------------------------

def test_a_bearer_token_fills_in_bearer_auth_with_the_token_stored_under_the_name_typed(win, tmp_path):
    """Spec 3 (an Authorization: Bearer header into Bearer auth) and 4 (the token is not written into the flow; it is
    stored under the name the user typed instead of the one suggested, and the block uses it): run, the request
    carries the token once, as the curl command's did."""
    path = tmp_path / "me.yaml"
    with editing(win), Site() as site:
        tab, api = new_api(win)
        shown, _ = fill_in(win, f"curl {site.base}/me -H 'Accept: application/json' "
                                "-H 'Authorization: Bearer tok-5f2a9c71'", names=["nightly_token"])
        asked = one(shown.names(), "secret names asked")
        assert "secret" in asked["text"].lower() and asked["suggested"].strip(), asked
        assert chosen(win, "bearer"), f"Auth is not Bearer: {combo_texts(win)}"
        result = run_flow(win, tab, path, api)
        assert result.returncode == 0, output(result)
        check(site.only(), {"path": "/me", "headers": {"Accept": "application/json",
                                                       "Authorization": "Bearer tok-5f2a9c71"}})
    assert "tok-5f2a9c71" not in path.read_text(encoding="utf-8")
    assert win.home.get_secret("nightly_token") == "tok-5f2a9c71"


def test_api_key_and_token_like_headers_are_each_stored_as_a_secret_under_its_own_name(win, tmp_path):
    """Spec 4 (an API key header such as X-API-Key, and headers named like *token* or *secret*): Fill in asks a name
    for each, suggesting a different one for each (keeping the suggestions loses none), stores each value; none is
    written into the flow; Accept is not a secret. Run, the request carries each header with its value."""
    values = {"X-API-Key": "key-a1b2c3d4", "X-Session-Token": "tok-e5f6a7b8", "X-Client-Secret": "sec-c9d0e1f2"}
    path = tmp_path / "data.yaml"
    with editing(win), Site() as site:
        tab, api = new_api(win)
        headers = " ".join(f"-H '{name}: {value}'" for name, value in values.items())
        shown, _ = fill_in(win, f"curl {site.base}/data -H 'Accept: application/json' {headers}")
        suggested = [asked["suggested"].strip() for asked in shown.names()]
        assert len(suggested) == 3 and len(set(suggested)) == 3 and all(suggested), shown.names()
        result = run_flow(win, tab, path, api)
        assert result.returncode == 0, output(result)
        check(site.only(), {"path": "/data", "headers": {"Accept": "application/json", **values}})
    flow = path.read_text(encoding="utf-8")
    assert [v for v in values.values() if v in flow] == []
    assert sorted(win.home.get_secret(name) for name in suggested) == sorted(values.values())


# --- 5: a request already filled in: Approve or Cancel; one Undo step -----------------------------------------------

def test_filling_in_a_filled_request_asks_first_cancel_changes_nothing_approve_replaces_it_in_one_undo_step(
        win, tmp_path):
    """Spec 5: the first import (on a block just added) asks nothing and is one Undo step. A second import on the
    filled request shows a box saying what will be replaced (the URL, headers, body), with Approve and Cancel;
    Cancel changes nothing (the block as it was, no secret stored); Approve replaces the request (nothing of the old
    one left) as one Undo step, which Redo puts back. Run, the block sends the new request only."""
    undo, redo = menu(win, "Undo"), menu(win, "Redo")
    with editing(win), Site() as site:
        tab, api = new_api(win)
        added = copy.deepcopy(tab.doc.data)
        shown, _ = fill_in(win, f"curl -X POST {site.base}/old-orders -H 'X-Old: 1' -d 'old body'")
        assert shown.questions() == [], shown.questions()
        filled = copy.deepcopy(tab.doc.data)
        assert filled != added, "Fill in filled in nothing"
        undo.trigger()
        settle()
        assert tab.doc.data == added, "one Undo does not take the whole import back"
        redo.trigger()
        settle()
        assert tab.doc.data == filled

        new = f"curl {site.base}/new-orders -H 'Accept: text/plain' -u 'report:pw-new-7731'"
        shown, _ = fill_in(win, new, approve=False)
        box = one(shown.questions(), "questions")
        assert {"Approve", "Cancel"} <= set(box["buttons"]), box
        for part in ("url", "header", "body"):
            assert part in box["text"].lower(), f"the box does not say the {part} will be replaced: {box['text']!r}"
        assert tab.doc.data == filled, "Cancel changed the block"
        assert "pw-new-7731" not in stored_text(win), "Cancel stored the password"

        shown, _ = fill_in(win, new, approve=True)
        assert len(shown.questions()) == 1 and len(shown.names()) == 1, list(shown)
        replaced = copy.deepcopy(tab.doc.data)
        assert replaced != filled
        undo.trigger()
        settle()
        assert tab.doc.data == filled, "one Undo does not take the whole import back"
        redo.trigger()
        settle()
        assert tab.doc.data == replaced

        result = run_flow(win, tab, tmp_path / "replaced.yaml", api)
        assert result.returncode == 0, output(result)
        request = site.only()
    check(request, {"path": "/new-orders", "headers": {"Accept": "text/plain",
                                                       "Authorization": basic("report", "pw-new-7731")}})
    assert request.headers.get_all("X-Old") is None


# --- 6: what it could not use; text that is not a curl command -----------------------------------------------------

def test_an_unknown_option_and_a_second_url_are_listed_as_not_used_the_rest_is_filled_in(win, tmp_path):
    """Spec 6: after filling, it lists what it could not use (an unknown option, a second URL), dropping nothing
    silently; the rest is filled in: run, the block calls the first URL with the header."""
    with editing(win), Site() as site:
        tab, api = new_api(win)
        _, said = fill_in(win, f"curl {site.base}/first -H 'Accept: text/plain' {site.base}/second --fizzbuzz")
        assert "--fizzbuzz" in said and "/second" in said, f"it does not list what it could not use: {said!r}"
        result = run_flow(win, tab, tmp_path / "first.yaml", api)
        assert result.returncode == 0, output(result)
        check(site.only(), {"path": "/first", "headers": {"Accept": "text/plain"}})


NOT_CURL = ["wget https://api.example.com/v1/report.csv",
            "Invoke-WebRequest -Uri https://api.example.com/v1/orders -Method Post -Body '{\"item\": 1}'"]


@pytest.mark.parametrize("text", NOT_CURL)
def test_text_that_is_not_a_curl_command_is_refused_saying_so_changing_nothing(win, text):
    """Spec 6: Fill in on text that is not a curl command says so (naming curl) and changes nothing: the block as it
    was, nothing asked, no Undo step (Undo then takes back the block's adding)."""
    with editing(win):
        tab, api = new_api(win)
        before = copy.deepcopy(tab.doc.data)
        shown, said = fill_in(win, text)
        assert "curl" in said.lower(), f"no message saying it is not a curl command: {said!r}"
        assert shown.names() == [] and shown.questions() == [], list(shown)
        assert tab.doc.data == before
        menu(win, "Undo").trigger()
        settle()
        assert api not in tab.doc.blocks, "Fill in left an Undo step"
