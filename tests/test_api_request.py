"""The API Request block (servers.api_request), "Postman in a block": a method, a URL, params and headers tables
(each row with an on/off tick), auth with stored secrets, a body, Response as, the statuses that count as success,
redirects, certificates and proxies; a `body` input; `response`, `status` and `headers` outputs, and the usual error
and done outputs; an optional API connection (base URL, auth, default headers, certificate settings); Copy as cURL;
what the Log says. Through the real `taskloom run` (as the editor, the scheduler service or a terminal start it), its
run history (history.db), the offscreen editor (Properties, the block's menu, Data preview, the Connections window),
README.md and Help > Documentation, against web servers on this computer started by each test (never the internet).
Each test names the point of the specification (1-9) it protects.

What the tests take as given (the specification leaves it to the change):
- Settings are found by the labels Properties shows (block.setting_label): Method, URL, Params, Headers, Auth, Body,
  Response as, Follow redirects and Connection exactly; the statuses that count as success by the word "success" in
  its label, the CA certificate file by the word "CA", the Don't verify certificates switch (a tick: ticked = do not
  verify) by the word "verify". A choice is found by words of its option (e.g. Auth "basic", "bearer", "key" and
  "header", "key" and "query", "digest"; Body "json", "text", "form" and "url", "multipart", "file"; Response as
  "automatic", "text", "table", "file").
- The settings that belong to one choice are shown only for it (fields.Field.only_if): Basic and Digest have two (the
  user name, whose label has "user", and the password), Bearer token one (the token), each API key two (the name of
  the header or query parameter, whose label has "name", and the key); each Body kind but none one (its content);
  Response as file one (the path). A password, token or key setting holds the name of a stored secret
  (`taskloom secret set NAME`).
- Params, Headers, a form url-encoded body and a multipart body are written in the flow file as a list of rows
  {key: ..., value: ...}, with `on: false` for an unticked row (left out: ticked); a multipart row that sends a file
  has `file: true` and the file's path as its value.
- A Headers value secret:NAME sends the stored secret NAME, as the README documents.
- An API connection in connections.yaml is `kind: api` with `base_url`, and its other values under the same names
  as the block's own settings (Headers rows, Auth and its settings, the CA certificate file), a secret as
  "secret:NAME" as in every connection. A relative URL is added to the end of the base URL (base http://h/api and
  /orders/7: http://h/api/orders/7, as Postman's {{baseUrl}}/orders/7).
- Basic auth and the API keys go with the request itself, as Postman sends them (the server sends no challenge);
  Digest answers the server's challenge.
- The Log: one message has the method and the URL; one has the status, the size in bytes and the time (ms or s).
- In Properties the Params table is an item view (e.g. a QTableWidget, as Connections' driver settings) whose rows
  show the key and the value, with a tick (a checkable cell or a check box in a cell); the URL is a text box showing
  the URL with its ?query. With the body input connected, a text that has "body" and "input" in it says so. With a
  connection chosen, the header it replaces says so with one of the words replaced, overridden, instead, wins or
  precedence (in its text or its tooltip). The Connections window keeps its rows in its form
  (ConnectionsDialog.form): Base URL, and Auth (a choice with a Bearer option); the token goes in a row labelled
  Token, or else in its secret field (ConnectionsDialog.password), as for every kind. It shows an API connection's
  headers in a table of their own (the one table shown: a header name and its value per row, added with its Add
  button), its CA certificate file in a row whose label has "ca", and Don't verify certificates as a check box whose
  text has "verify".
- Copy as cURL is an item of the block's menu (MainWindow._block_menu_for) starting "Copy as cURL"; the copied text
  is read as bash reads it (shlex, with \\-newline continuations), so a secret is put back by replacing its
  <hidden: secret NAME> (e.g. Basic as --user 'name:<hidden: secret NAME>').
- A JSON body written in the block uses only [ ] here: whether { } in a JSON body must be doubled, as in every other
  setting, is left to the change.
What a request carried is read on the server's side."""

import base64
import csv
import hashlib
import html
import http.server
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import ssl
import subprocess
import threading
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialogButtonBox,  # noqa: E402
                               QFormLayout, QGroupBox, QHeaderView, QInputDialog, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QTableWidget, QTabWidget, QTextEdit, QWidget)

from taskloom.block import applies, setting_label  # noqa: E402
from taskloom.flow import BlockSpec, block_config  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_block_icons import built_in, canvas_icons, drawing, unlike  # noqa: E402
from tests.test_block_icons import rows as list_rows  # noqa: E402
from tests.test_paths_editor_editing import clean, flow_file, select_only, settle  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
API = "servers.api_request"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
PROXY_VARIABLES = {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy"}
# Tabler Icons' "api" (outline), as in its SVG: the letters A, P and I.
TABLER_API = ('<path d="M4 13h5"/><path d="M12 16v-8h3a2 2 0 0 1 2 2v1a2 2 0 0 1 -2 2h-3"/><path d="M20 8v8"/>'
              '<path d="M9 16v-5.5a2.5 2.5 0 0 0 -5 0v5.5"/>')


# --- the block, found by what Properties calls its settings -------------------------------------------------------------

@cache
def api_class():
    cls = discover(None).get(API)
    assert cls is not None, f"there is no {API} block (API Request)"
    return cls


def label(key_: str) -> str:
    return setting_label(key_, api_class().config[key_])


def key(shown: str) -> str:
    """The key, in the flow file, of the API Request setting Properties shows as `shown`."""
    keys = {label(k): k for k in api_class().config}
    assert shown in keys, f"API Request has no setting labelled {shown!r} (its settings: {sorted(keys)})"
    return keys[shown]


def words(text) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def labelled(*wanted: str) -> str:
    """The key of the one API Request setting whose label has all these words."""
    found = [k for k in api_class().config if set(wanted) <= words(label(k))]
    assert len(found) == 1, f"API Request should have one setting whose label has {wanted}: {[label(k) for k in found]}"
    return found[0]


def option(key_: str, *wanted: str, without=()) -> str:
    """The one choice of the setting `key_` whose text has every one of `wanted` in it and none of `without`."""
    options = getattr(api_class().config[key_], "options", None)
    assert options, f"{label(key_)} is not a choice"
    found = [o for o in options if all(w in str(o).lower() for w in wanted)
             and not any(w in str(o).lower() for w in without)]
    assert len(found) == 1, f"{label(key_)} should have one choice with {wanted} in it: {options}"
    return found[0]


def only_for(key_: str, value) -> list[str]:
    """The settings shown when the setting `key_` is `value` and not when it has its default."""
    cls = api_class()
    return [k for k in cls.config if k != key_ and applies(cls, k, {key_: value}) and not applies(cls, k, {})]


def rows(*pairs, off=()) -> list[dict]:
    """A key/value table as the flow file writes it: a row per (key, value), ticked unless its key is in `off`."""
    return [{"key": k, "value": v, **({"on": False} if k in off else {})} for k, v in pairs]


def _auth(wanted: tuple, named: set | None, name_value, secret: str) -> dict:
    k = key("Auth")
    value = option(k, *wanted)
    extra = only_for(k, value)
    config = {k: value}
    if named is None:
        assert len(extra) == 1, f"Auth {value}: one setting, for the secret: {[label(e) for e in extra]}"
        config[extra[0]] = secret
        return config
    assert len(extra) == 2, f"Auth {value}: two settings: {[label(e) for e in extra]}"
    names = [e for e in extra if words(label(e)) & named and "secret" not in words(label(e))]
    assert len(names) == 1, f"Auth {value}: which of {[label(e) for e in extra]} is the {named} one?"
    [secret_key] = [e for e in extra if e != names[0]]
    config.update({names[0]: name_value, secret_key: secret})
    return config


def basic(user: str, secret: str) -> dict:
    return _auth(("basic",), {"user", "username", "login"}, user, secret)


def digest(user: str, secret: str) -> dict:
    return _auth(("digest",), {"user", "username", "login"}, user, secret)


def bearer(secret: str) -> dict:
    return _auth(("bearer",), None, None, secret)


def key_in_header(name: str, secret: str) -> dict:
    return _auth(("key", "header"), {"name"}, name, secret)


def key_in_query(name: str, secret: str) -> dict:
    return _auth(("key", "query"), {"name"}, name, secret)


def body(wanted: tuple, content, without=()) -> dict:
    k = key("Body")
    value = option(k, *wanted, without=without)
    extra = only_for(k, value)
    assert len(extra) == 1, f"Body {value}: one setting, for what is sent: {[label(e) for e in extra]}"
    return {k: value, extra[0]: content}


def json_body(text: str) -> dict:
    return body(("json",), text)


def text_body(text: str) -> dict:
    return body(("text",), text)


def form_body(table: list) -> dict:
    return body(("form", "url"), table)


def multipart_body(table: list) -> dict:
    return body(("multipart",), table)


def file_body(path) -> dict:
    return body(("file",), str(path), without=("form", "multipart"))


def response_as(wanted: str, path=None) -> dict:
    k = key("Response as")
    value = option(k, wanted)
    config = {k: value}
    if path is not None:
        extra = only_for(k, value)
        assert len(extra) == 1, f"Response as {value}: one setting, for the path: {[label(e) for e in extra]}"
        config[extra[0]] = path
    return config


def success(statuses: str) -> dict:
    return {labelled("success"): statuses}


def request(url: str, *parts: dict, method=None, params=None, headers=None, connection=None, x=0, y=0, **spec) -> dict:
    """An API Request block calling `url`; the settings left out as it is added."""
    config = {key("URL"): url}
    if method is not None:
        options = getattr(api_class().config[key("Method")], "options", None) or []
        assert method in options, f"{method} is not one of the choices of Method: {options}"
        config[key("Method")] = method
    if params is not None:
        config[key("Params")] = params
    if headers is not None:
        config[key("Headers")] = headers
    if connection is not None:
        config[key("Connection")] = connection
    for part in parts:
        config.update(part)
    return {"type": API, "config": config, "ui": {"x": x, "y": y}, **spec}


def api_connection(base_url: str, *parts: dict, headers=None) -> dict:
    """An API connection as connections.yaml holds it."""
    conn = {"kind": "api", "base_url": base_url}
    if headers is not None:
        conn[key("Headers")] = headers
    for part in parts:
        conn.update(part)
    return conn


# --- the blocks around it, the flow and the run history --------------------------------------------------------------

def keep(name: str, *inputs: str, x=700, y=0) -> dict:
    """A Python Code block that writes what reaches its inputs to <name>.json, as {input: value}."""
    pairs = ", ".join(f"{i!r}: {i}" for i in inputs)
    code = ("import json\nwith open(%r, 'w', encoding='utf-8') as f:\n    json.dump({%s}, f, default=str)"
            % (f"{name}.json", pairs))
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": [], "code": code},
            "ui": {"x": x, "y": y}}


def make(expression: str, x=-700, y=0, imports: str = "") -> dict:
    """A Python Code block whose output is `expression`."""
    return {"type": "logic.python", "config": {"inputs": [], "code": f"{imports}output = {expression}"},
            "ui": {"x": x, "y": y}}


def kept(env, name: str) -> dict:
    return json.loads((env.root / f"{name}.json").read_text(encoding="utf-8"))


def lower(headers: dict) -> dict:
    return {str(k).lower(): v for k, v in headers.items()}


def _env_for_runs(env, proxy: str | None = None):
    """No proxy of this computer's: a request to the test's server goes straight to it (`proxy`: through that one)."""
    env.env = {k: v for k, v in env.env.items() if k.lower() not in PROXY_VARIABLES}
    if proxy is not None:
        env.env.update({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy})


def run(env, blocks: dict, edges=(), params=None, name="flow", proxy=None):
    """`taskloom run` of the flow: its result, the run's number (None: no run began) and each block's status."""
    path = flow_file(env.root / f"{name}.yaml", blocks, edges, **({"params": params} if params else {}))
    _env_for_runs(env, proxy)
    result = env.taskloom("run", path)
    found = env.query("SELECT id FROM runs ORDER BY id DESC LIMIT 1") if (env.home / "history.db").exists() else []
    run_id = found[0][0] if found else None
    return result, run_id, (env.block_status(run_id) if run_id is not None else {})


def error_of(env, run_id: int, block: str) -> str:
    return env.query("SELECT error FROM blocks WHERE run_id=? AND block=?", run_id, block)[0][0] or ""


def logs_of(env, run_id: int, block: str) -> list[tuple[str, str]]:
    return env.query("SELECT level, message FROM logs WHERE run_id=? AND block=? ORDER BY rowid", run_id, block)


def took(env, run_id: int, block: str) -> float:
    [(began, ended)] = env.query("SELECT started, finished FROM blocks WHERE run_id=? AND block=?", run_id, block)
    return (datetime.fromisoformat(ended) - datetime.fromisoformat(began)).total_seconds()


def files_holding(root: Path, text: str) -> list[Path]:
    """Files under root (secrets.yaml aside, which holds it on purpose) whose bytes contain `text`."""
    data = text.encode()
    return [p for p in root.rglob("*") if p.is_file() and p.name != "secrets.yaml" and data in p.read_bytes()]


def write_secrets(env, **values: str):
    path = env.home / "secrets.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    path.chmod(0o600)


def write_connections(env, **conns: dict):
    (env.home / "connections.yaml").write_text(yaml.safe_dump(conns, sort_keys=False), encoding="utf-8")


def secret(what: str) -> str:
    """A secret's value no text of Taskloom's could hold by chance."""
    return f"{what}-{secrets.token_hex(8)}"


# --- the web servers -------------------------------------------------------------------------------------------------

class _Threading(http.server.ThreadingHTTPServer):
    daemon_threads, block_on_close = True, False  # (closing it never waits for a request still being answered)


def _read_body(h) -> bytes:
    if "chunked" in (h.headers.get("Transfer-Encoding") or "").lower():
        data = b""
        while True:
            size = int(h.rfile.readline().split(b";")[0].strip() or b"0", 16)
            if size == 0:
                while h.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                return data
            data += h.rfile.read(size)
            h.rfile.readline()
    length = int(h.headers.get("Content-Length") or 0)
    return h.rfile.read(length) if length else b""


def json_answer(value, status=200, headers=None, content_type="application/json"):
    return status, {"Content-Type": content_type, **(headers or {})}, json.dumps(value).encode("utf-8")


class Api:
    """A web API on this computer for one test: it notes every request in `requests` ({"method", "target" (as sent,
    e.g. a whole URL to a proxy), "path", "query": [(name, value), ...] decoded, "raw_query", "headers": {lower-case
    name: value}, "body": bytes}), in the order they came, and answers each as answers[path](request) gives
    (status, {header: value}, body bytes), or else 200 with {"ok": true} as JSON. CONNECT (a tunnel through a proxy)
    is refused with 403. `tls`: (certificate chain file, key file), for HTTPS."""

    def __init__(self, tls=None):
        self.requests, self.answers, self.lock = [], {}, threading.Lock()
        api = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

        for method in (*METHODS, "CONNECT"):
            setattr(Handler, f"do_{method}", lambda handler: api._serve(handler))
        self.httpd = _Threading(("127.0.0.1", 0), Handler)
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*tls)
            self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.base = f"{'https' if tls else 'http'}://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def _serve(self, h):
        data = b"" if h.command == "CONNECT" else _read_body(h)
        url = urlsplit(h.path)
        got = {"method": h.command, "target": h.path, "path": url.path, "raw_query": url.query,
               "query": parse_qsl(url.query, keep_blank_values=True),
               "headers": {k.lower(): v for k, v in h.headers.items()}, "body": data}
        with self.lock:
            self.requests.append(got)
            answer = self.answers.get(url.path)
        if h.command == "CONNECT":
            status, headers, out = 403, {"Content-Type": "text/plain"}, b"no tunnels here"
        else:
            status, headers, out = answer(got) if answer else json_answer({"ok": True})
        h.send_response(status)
        for name, value in headers.items():
            h.send_header(name, value)
        h.send_header("Content-Length", str(len(out)))
        h.end_headers()
        if h.command != "HEAD":
            h.wfile.write(out)

    def on(self, path: str, status=200, body_=b"", headers=None):
        self.answers[path] = lambda got: (status, dict(headers or {}), body_)

    def on_json(self, path: str, value, status=200, headers=None, content_type="application/json"):
        self.answers[path] = lambda got: json_answer(value, status, headers, content_type)

    def got(self, path: str) -> list[dict]:
        with self.lock:
            return [r for r in self.requests if r["path"] == path and r["method"] != "CONNECT"]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@contextmanager
def serving(tls=None):
    api = Api(tls)
    try:
        yield api
    finally:
        api.close()


@contextmanager
def silent_port():
    """A port that accepts connections and never answers."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    try:
        yield s.getsockname()[1]
    finally:
        s.close()


def closed_port() -> int:
    """A port nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def digest_answer(user: str, password: str, realm="api"):
    """An answer that wants HTTP Digest authentication (MD5, qop=auth) of `user` and `password`."""
    nonce = secrets.token_hex(8)

    def md5(text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    def answer(got):
        auth = got["headers"].get("authorization", "")
        if auth.lower().startswith("digest "):
            f = urllib.request.parse_keqv_list(urllib.request.parse_http_list(auth[7:]))
            ha1, ha2 = md5(f"{user}:{realm}:{password}"), md5(f"{got['method']}:{f.get('uri')}")
            expected = (md5(f"{ha1}:{f.get('nonce')}:{f.get('nc')}:{f.get('cnonce')}:{f.get('qop')}:{ha2}")
                        if f.get("qop") else md5(f"{ha1}:{f.get('nonce')}:{ha2}"))
            if f.get("username") == user and f.get("nonce") == nonce and f.get("response") == expected:
                return json_answer({"digest": "ok"})
        return 401, {"WWW-Authenticate": f'Digest realm="{realm}", nonce="{nonce}", qop="auth", algorithm=MD5',
                     "Content-Type": "text/plain"}, b"who are you?"

    return answer


def basic_answer(user: str, password: str):
    """An answer that wants this Basic authorization on the request itself (it sends no challenge)."""
    wanted = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def answer(got):
        if got["headers"].get("authorization") == wanted:
            return json_answer({"basic": "ok"})
        return json_answer({"error": "not authorized"}, 401)

    return answer


def certificates(folder: Path) -> tuple[Path, Path, Path]:
    """A certificate authority of the test's own and a server certificate it signed for 127.0.0.1 and localhost:
    (the CA's certificate file, the server's certificate file, the server's key file)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ca_key, server_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Taskloom test CA")])

    def usage(**on):
        names = ("digital_signature", "content_commitment", "key_encipherment", "data_encipherment", "key_agreement",
                 "key_cert_sign", "crl_sign", "encipher_only", "decipher_only")
        return x509.KeyUsage(**{n: on.get(n, False) for n in names})

    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
          .not_valid_after(now + timedelta(days=7))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(usage(digital_signature=True, key_cert_sign=True, crl_sign=True), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    server = (x509.CertificateBuilder()
              .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])).issuer_name(ca_name)
              .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=7))
              .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                                                          x509.DNSName("localhost")]), critical=False)
              .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
              .add_extension(usage(digital_signature=True), critical=True)
              .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    ca_file, cert_file, key_file = folder / "ca.pem", folder / "server.pem", folder / "server-key.pem"
    ca_file.write_bytes(ca.public_bytes(pem))
    cert_file.write_bytes(server.public_bytes(pem))
    key_file.write_bytes(server_key.private_bytes(pem, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
    return ca_file, cert_file, key_file


def multipart(got: dict) -> dict:
    """A multipart/form-data body: {field name: (its file name or None, its content)}."""
    content_type = got["headers"].get("content-type", "")
    assert content_type.startswith("multipart/form-data"), content_type
    boundary = re.search(r'boundary="?([^";]+)"?', content_type).group(1).encode()
    found = {}
    for chunk in got["body"].split(b"--" + boundary)[1:]:
        if chunk.startswith(b"--"):
            break
        head, _, content = chunk[2:].partition(b"\r\n\r\n")  # (after the CRLF that ends the boundary line)
        content = content[:-2] if content.endswith(b"\r\n") else content
        disposition = next(line for line in head.split(b"\r\n") if line.lower().startswith(b"content-disposition"))
        name = re.search(rb'(?<![A-Za-z])name="([^"]*)"', disposition).group(1).decode()
        filename = re.search(rb'filename="([^"]*)"', disposition)
        found[name] = (filename.group(1).decode() if filename else None, content)
    return found


# --- 1: the block, in the Servers group, with Tabler's api icon -----------------------------------------------------------

def test_api_request_is_in_the_servers_group_with_health_check_and_explains_itself_on_hover(win):
    """Spec 1 (block type servers.api_request, title "API Request", category Servers, next to Health Check) and 9 (the
    hover help describes the block): the Blocks list shows API Request in the Servers group, where Health Check is,
    and resting on it shows what it does."""
    cls = api_class()
    assert (cls.title, cls.category) == ("API Request", "Servers")
    groups = list_rows(win.palette_panel)
    servers = {item.text(0): item for item in groups.get("Servers", [])}
    assert "Health Check" in servers and "API Request" in servers, sorted(servers)
    tip = html.unescape(re.sub(r"<[^>]+>", " ", servers["API Request"].toolTip(0)))
    assert " ".join(cls.description.split()) in " ".join(tip.split())
    assert words(cls.description) & {"api", "apis", "http", "https", "web", "request", "requests"}, cls.description


def test_its_icon_is_tablers_api_drawing(win, tmp_path):
    """Spec 1 (icon: Tabler "api", outline, same style): of every built-in block's icon on the canvas, the API
    Request's is the most like Tabler's api drawing (the letters A, P, I in white strokes)."""
    api_class()
    icons = canvas_icons(win, tmp_path, built_in(win.registry))
    reference = drawing(TABLER_API)
    closest = min(icons, key=lambda t: unlike(icons[t], reference))
    assert closest == API, (f"Tabler's api drawing is most like the icon of {closest} "
                            f"({unlike(icons[closest], reference):.3f} against {unlike(icons[API], reference):.3f})")


# --- 2: method, URL, params, headers ------------------------------------------------------------------------------------

def test_each_method_is_sent_as_chosen_and_get_is_the_default(env):
    """Spec 2 (Method: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS; default GET). The answers are JSON; a HEAD
    answer has no body (as HTTP has it), which is no JSON to read: it still succeeds."""
    options = getattr(api_class().config[key("Method")], "options", None)
    assert options and sorted(options) == sorted(METHODS), options
    with serving() as api:
        blocks = {"default": request(f"{api.base}/m/default", y=-200)}
        blocks.update({m.lower(): request(f"{api.base}/m/{m.lower()}", method=m, y=200 * i) for i, m in enumerate(METHODS)})
        result, run_id, status = run(env, blocks)

    assert status == {b: "success" for b in blocks}, result.stdout[-3000:] + result.stderr[-3000:]
    sent = {r["path"]: r["method"] for r in api.requests}
    assert sent == {"/m/default": "GET", **{f"/m/{m.lower()}": m for m in METHODS}}


def test_params_are_added_to_the_query_in_order_and_an_unticked_row_is_not_sent(env):
    """Spec 2 (Params: a key/value table, each row with an on/off tick; the URL's own ?query): the ticked rows go into
    the query in their order, encoded (spaces, & and = in a value arrive as typed); an unticked row is not sent; a URL
    with its own query and no params sends that query."""
    with serving() as api:
        blocks = {
            "search": request(f"{api.base}/search", params=rows(("q", "monthly report"), ("page", "2"),
                                                                 ("debug", "1"), ("sign", "a&b=c"), off=("debug",))),
            "own": request(f"{api.base}/own?region=north&top=5", y=200),
        }
        result, _, status = run(env, blocks)

    assert status == {"search": "success", "own": "success"}, result.stderr[-3000:]
    [search] = api.got("/search")
    assert search["query"] == [("q", "monthly report"), ("page", "2"), ("sign", "a&b=c")]
    [own] = api.got("/own")
    assert own["query"] == [("region", "north"), ("top", "5")]


def test_headers_are_sent_and_an_unticked_one_is_not(env):
    """Spec 2 (Headers: the same kind of table, each row with an on/off tick)."""
    with serving() as api:
        result, _, status = run(env, {"call": request(
            f"{api.base}/report", headers=rows(("Accept", "application/json"), ("X-Trace", "nightly-1"),
                                               ("X-Debug", "1"), off=("X-Debug",)))})

    assert status == {"call": "success"}, result.stderr[-3000:]
    [got] = api.got("/report")
    assert got["headers"]["accept"] == "application/json"
    assert got["headers"]["x-trace"] == "nightly-1"
    assert "x-debug" not in got["headers"]


# --- 2: auth ------------------------------------------------------------------------------------------------------------

def test_each_auth_kind_sends_its_stored_secret_which_is_never_written_anywhere(env):
    """Spec 2 (Auth: none, Basic, Bearer token, API key in a header or in the query, Digest; passwords, tokens and keys
    are stored secrets referenced by name, never written in the flow file) and 8 (never a secret in the Log): each
    request carries its credentials (Basic and the keys with the request itself, as Postman sends them; Digest after
    the server's challenge); no secret is in the flow file, the output of `taskloom run`, the run history or any file
    of the run."""
    values = {"api_password": secret("pw"), "api_token": secret("tok"), "api_key": secret("key"),
              "digest_password": secret("dg")}
    write_secrets(env, **values)
    with serving() as api:
        api.answers["/basic"] = basic_answer("report", values["api_password"])
        api.answers["/digest"] = digest_answer("report", values["digest_password"])
        blocks = {
            "none": request(f"{api.base}/none", y=0),
            "basic": request(f"{api.base}/basic", basic("report", "api_password"), y=200),
            "bearer": request(f"{api.base}/bearer", bearer("api_token"), y=400),
            "in_header": request(f"{api.base}/in_header", key_in_header("X-Api-Key", "api_key"), y=600),
            "in_query": request(f"{api.base}/in_query", key_in_query("api_key", "api_key"), y=800),
            "digest": request(f"{api.base}/digest", digest("report", "digest_password"), y=1000),
        }
        result, run_id, status = run(env, blocks)

    assert status == {b: "success" for b in blocks}, result.stdout[-3000:] + result.stderr[-3000:]
    assert "authorization" not in api.got("/none")[-1]["headers"]
    assert api.got("/basic")[-1]["headers"]["authorization"] == "Basic " + base64.b64encode(
        f"report:{values['api_password']}".encode()).decode()
    assert api.got("/bearer")[-1]["headers"]["authorization"] == f"Bearer {values['api_token']}"
    assert api.got("/in_header")[-1]["headers"]["x-api-key"] == values["api_key"]
    assert ("api_key", values["api_key"]) in api.got("/in_query")[-1]["query"]
    assert api.got("/digest")[-1]["headers"]["authorization"].lower().startswith("digest ")
    flow_text = (env.root / "flow.yaml").read_text(encoding="utf-8")
    for value in values.values():
        assert value not in flow_text
        assert value not in result.stdout + result.stderr
        assert files_holding(env.home, value) == [], value


def test_a_secret_that_is_not_stored_fails_before_sending_and_says_how_to_store_it(env):
    """Spec 2 (secrets referenced by name) and the no-surprises rule (it fails clearly, nothing is sent without its
    credentials): the error names the secret and the command that stores it."""
    write_secrets(env, other="x")
    with serving() as api:
        result, run_id, status = run(env, {"call": request(f"{api.base}/orders", bearer("orders_token"))})

    assert status == {"call": "failed"}, result.stderr[-3000:]
    error = error_of(env, run_id, "call")
    assert "orders_token" in error and "taskloom secret set orders_token" in error, error
    assert api.requests == []


# --- 2, 3: the body and placeholders ---------------------------------------------------------------------------------------

def test_each_body_kind_is_sent_as_chosen(env):
    """Spec 2 (Body: none, JSON, text/XML, form url-encoded, multipart form-data with files, a file sent as is)."""
    upload = env.root / "data.csv"
    upload.write_bytes(b"id,name\n1,alpha\n")
    raw = env.root / "payload.bin"
    raw.write_bytes(bytes(range(256)))
    with serving() as api:
        blocks = {
            "nothing": request(f"{api.base}/nothing", method="POST", y=0),
            "as_json": request(f"{api.base}/json", json_body('["report", 2, true]'), method="POST", y=200),
            "as_text": request(f"{api.base}/text", text_body('<order id="7"/>'), method="PUT", y=400),
            "as_form": request(f"{api.base}/form", form_body(rows(("a", "1"), ("b", "two words & more"), ("c", "3"),
                                                                  off=("c",))), method="POST", y=600),
            "as_parts": request(f"{api.base}/parts", multipart_body(
                [{"key": "note", "value": "hello"}, {"key": "upload", "value": str(upload), "file": True},
                 {"key": "skipped", "value": "no", "on": False}]), method="POST", y=800),
            "as_file": request(f"{api.base}/file", file_body(raw), method="POST", y=1000),
        }
        result, _, status = run(env, blocks)

    assert status == {b: "success" for b in blocks}, result.stdout[-3000:] + result.stderr[-3000:]
    assert api.got("/nothing")[0]["body"] == b""
    [as_json] = api.got("/json")
    assert as_json["headers"]["content-type"].startswith("application/json")
    assert json.loads(as_json["body"]) == ["report", 2, True]
    assert api.got("/text")[0]["body"] == b'<order id="7"/>'
    [as_form] = api.got("/form")
    assert as_form["headers"]["content-type"].startswith("application/x-www-form-urlencoded")
    assert parse_qsl(as_form["body"].decode(), keep_blank_values=True) == [("a", "1"), ("b", "two words & more")]
    parts = multipart(api.got("/parts")[0])
    assert parts == {"note": (None, b"hello"), "upload": ("data.csv", b"id,name\n1,alpha\n")}
    assert api.got("/file")[0]["body"] == bytes(range(256))


def test_placeholders_are_filled_in_the_url_params_headers_and_body(env):
    """Spec 3 (placeholders {name} work in the URL, params, headers and body, with flow parameters)."""
    with serving() as api:
        result, _, status = run(env, {"call": request(
            "{base}/orders/{id}", json_body('["{region}", {id}]'), method="POST",
            params=rows(("region", "{region}")), headers=rows(("X-Region", "{region}-{id}")))},
            params={"base": {"type": "text", "value": api.base}, "id": {"type": "int", "value": 7},
                    "region": {"type": "text", "value": "north"}})

    assert status == {"call": "success"}, result.stdout[-3000:] + result.stderr[-3000:]
    [got] = api.got("/orders/7")
    assert got["query"] == [("region", "north")]
    assert got["headers"]["x-region"] == "north-7"
    assert json.loads(got["body"]) == ["north", 7]


def test_placeholders_are_filled_from_the_blocks_input_in_the_url_params_and_headers(env):
    """Spec 3 (placeholders {name} work in the URL, params and headers with the block's inputs as well as flow
    parameters): the text reaching the body input fills {body} in each (and is the body, sent as is)."""
    with serving() as api:
        blocks = {"value": make("'abc'"),
                  "call": request(f"{api.base}/orders/{{body}}", method="POST", params=rows(("q", "{body}")),
                                  headers=rows(("X-Value", "{body}")))}
        result, _, status = run(env, blocks, ["value.output -> call.body"])

    assert status == {"value": "success", "call": "success"}, result.stdout[-3000:] + result.stderr[-3000:]
    [got] = api.got("/orders/abc")
    assert got["query"] == [("q", "abc")]
    assert got["headers"]["x-value"] == "abc"
    assert got["body"] == b"abc"


def test_the_body_input_sends_a_dict_or_list_as_json_a_table_as_json_records_and_text_as_is(env):
    """Spec 4 (an optional `body` input: a dict or list is sent as JSON, a table as JSON records, text as is)."""
    with serving() as api:
        blocks = {
            "a_dict": make("{'id': 7, 'tags': ['a', 'b']}", y=0),
            "a_list": make("[1, 2, 3]", y=200),
            "a_table": make("pl.DataFrame({'id': [1, 2], 'name': ['alpha', 'beta']})", y=400,
                            imports="import polars as pl\n"),
            "a_text": make("'plain café'", y=600),
            "send_dict": request(f"{api.base}/dict", method="POST", y=0),
            "send_list": request(f"{api.base}/list", method="POST", y=200),
            "send_table": request(f"{api.base}/table", method="POST", y=400),
            "send_text": request(f"{api.base}/text", method="POST", y=600),
        }
        edges = [f"a_{k}.output -> send_{k}.body" for k in ("dict", "list", "table", "text")]
        result, _, status = run(env, blocks, edges)

    assert status == {b: "success" for b in blocks}, result.stdout[-3000:] + result.stderr[-3000:]
    for path, value in (("/dict", {"id": 7, "tags": ["a", "b"]}), ("/list", [1, 2, 3]),
                        ("/table", [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}])):
        [got] = api.got(path)
        assert got["headers"]["content-type"].startswith("application/json"), (path, got["headers"])
        assert json.loads(got["body"]) == value, path
    assert api.got("/text")[0]["body"] == "plain café".encode("utf-8")


# --- 4: outputs ---------------------------------------------------------------------------------------------------------

def test_its_outputs_are_the_response_the_status_as_a_number_and_the_headers_and_done_fires(env):
    """Spec 4 (outputs: `response` as Response as says, `status` the status code, a number, `headers` a dict, and the
    usual done output)."""
    with serving() as api:
        api.on_json("/order", {"id": 7, "state": "shipped"}, status=201, headers={"X-Request-Id": "r-17"})
        blocks = {"call": request(f"{api.base}/order"), "got": keep("got", "response", "status", "headers"),
                  "then": make("1", x=700, y=300)}
        edges = ["call.response -> got.response", "call.status -> got.status", "call.headers -> got.headers",
                 "call.done -> then.after"]
        result, _, status = run(env, blocks, edges)

    assert status == {"call": "success", "got": "success", "then": "success"}, result.stdout[-3000:] + result.stderr
    got = kept(env, "got")
    assert got["response"] == {"id": 7, "state": "shipped"}
    assert got["status"] == 201 and isinstance(got["status"], int)
    assert isinstance(got["headers"], dict) and lower(got["headers"])["x-request-id"] == "r-17"


# --- 2: Response as -------------------------------------------------------------------------------------------------------

def test_response_as_automatic_reads_json_only_when_the_server_says_it_is_json(env):
    """Spec 2 (Response as automatic, the default: JSON read when the server says it is JSON, text otherwise, as its
    charset says)."""
    with serving() as api:
        api.on_json("/json", {"a": [1, 2]}, content_type="application/json; charset=utf-8")
        api.on("/looks_like_json", body_=b'{"a": 1}', headers={"Content-Type": "text/plain; charset=utf-8"})
        api.on("/latin", body_="café".encode("latin-1"), headers={"Content-Type": "text/plain; charset=iso-8859-1"})
        blocks, edges = {}, []
        for i, name in enumerate(("json", "looks_like_json", "latin")):
            blocks[name] = request(f"{api.base}/{name}", y=200 * i)
            blocks[f"keep_{name}"] = keep(name, "response", y=200 * i)
            edges.append(f"{name}.response -> keep_{name}.response")
        result, _, status = run(env, blocks, edges)

    assert set(status.values()) == {"success"}, result.stdout[-3000:] + result.stderr[-3000:]
    assert kept(env, "json")["response"] == {"a": [1, 2]}
    assert kept(env, "looks_like_json")["response"] == '{"a": 1}'
    assert kept(env, "latin")["response"] == "café"


def test_response_as_text_table_and_file(env):
    """Spec 2 (Response as text; table: a JSON list of records becomes a table; file: saved to a path setting, the
    output is the path)."""
    with serving() as api:
        api.on_json("/json", {"a": 1})
        api.on_json("/records", [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}])
        api.on("/download", body_=bytes(range(256)), headers={"Content-Type": "application/octet-stream"})
        blocks = {
            "as_text": request(f"{api.base}/json", response_as("text"), y=0),
            "keep_text": keep("as_text", "response", y=0),
            "as_table": request(f"{api.base}/records", response_as("table"), y=200),
            "write": {"type": "data.write_file", "config": {"path": "orders.csv"}, "ui": {"x": 700, "y": 200}},
            "as_file": request(f"{api.base}/download", response_as("file", path="downloads/data.bin"), y=400),
            "keep_file": keep("as_file", "response", y=400),
        }
        edges = ["as_text.response -> keep_text.response", "as_table.response -> write.table",
                 "as_file.response -> keep_file.response"]
        result, _, status = run(env, blocks, edges)

    assert set(status.values()) == {"success"}, result.stdout[-3000:] + result.stderr[-3000:]
    assert kept(env, "as_text")["response"] == '{"a": 1}'
    with open(env.root / "orders.csv", encoding="utf-8", newline="") as f:
        assert list(csv.reader(f)) == [["id", "name"], ["1", "alpha"], ["2", "beta"]]
    saved = env.root / "downloads" / "data.bin"
    assert saved.read_bytes() == bytes(range(256))
    output = Path(kept(env, "as_file")["response"])
    assert (output if output.is_absolute() else env.root / output).resolve() == saved.resolve()


def test_response_as_table_of_json_that_is_no_list_of_records_fails_clearly(env):
    """Spec 2 (table: a JSON list of records becomes a table) and the no-surprises rule: anything else fails the block,
    saying it is not a list (no silent conversion)."""
    with serving() as api:
        api.on_json("/object", {"id": 1, "name": "alpha"})
        result, run_id, status = run(env, {"call": request(f"{api.base}/object", response_as("table"))})

    assert status == {"call": "failed"}, result.stderr[-3000:]
    assert "list" in error_of(env, run_id, "call").lower()


# --- 5: failures ----------------------------------------------------------------------------------------------------------

def test_a_status_not_counted_as_success_fails_and_its_error_output_carries_the_status_headers_and_body(env):
    """Spec 5 (a status not counted as success fails the block, its error output carrying the error, the status, the
    headers and the body)."""
    with serving() as api:
        api.on_json("/orders/9", {"message": "no such order"}, status=404, headers={"X-Thing": "missing"})
        blocks = {"call": request(f"{api.base}/orders/9"), "handle": keep("failure", "input")}
        result, run_id, status = run(env, blocks, ["call.error -> handle.input"])

    assert status == {"call": "failed", "handle": "success"}, result.stdout[-3000:] + result.stderr[-3000:]
    failure = kept(env, "failure")["input"]
    assert isinstance(failure, dict), failure
    assert "404" in str(failure.get("error")), failure
    assert failure.get("status") == 404, failure
    assert lower(failure.get("headers") or {}).get("x-thing") == "missing", failure
    body_ = failure.get("body")
    assert body_ == {"message": "no such order"} or json.loads(body_) == {"message": "no such order"}, failure
    assert "404" in error_of(env, run_id, "call")


def test_the_statuses_that_count_as_success(env):
    """Spec 2 (Statuses that count as success: default 200-299; e.g. "200-299, 404")."""
    with serving() as api:
        api.on_json("/created", {"id": 1}, status=201)
        api.on_json("/missing", {"message": "gone"}, status=404)
        blocks = {
            "created": request(f"{api.base}/created", y=0), "keep_created": keep("created", "status", y=0),
            "missing": request(f"{api.base}/missing", y=200),
            "allowed": request(f"{api.base}/missing", success("200-299, 404"), y=400),
            "keep_allowed": keep("allowed", "status", "response", y=400),
            "exact": request(f"{api.base}/created", success("200"), y=600),
        }
        edges = ["created.status -> keep_created.status", "allowed.status -> keep_allowed.status",
                 "allowed.response -> keep_allowed.response"]
        result, run_id, status = run(env, blocks, edges)

    assert status == {"created": "success", "keep_created": "success", "missing": "failed", "allowed": "success",
                      "keep_allowed": "success", "exact": "failed"}, result.stdout[-3000:] + result.stderr[-3000:]
    assert kept(env, "created")["status"] == 201
    assert kept(env, "allowed") == {"status": 404, "response": {"message": "gone"}}
    assert "404" in error_of(env, run_id, "missing") and "201" in error_of(env, run_id, "exact")


def test_statuses_written_wrong_are_refused_before_anything_is_sent_naming_the_setting(env):
    """Spec 2 (Statuses that count as success) and the no-surprises rule (when something is wrong it fails clearly):
    a value that is no status or range of statuses stops the run before the request is sent, naming the setting."""
    shown = label(labelled("success"))
    with serving() as api:
        result, run_id, status = run(env, {"call": request(f"{api.base}/orders", success("two hundred"))})

    assert result.returncode != 0
    assert api.requests == []
    said = result.stdout + result.stderr + (error_of(env, run_id, "call") if status.get("call") else "")
    assert shown in said, said[-3000:]


def test_redirects_are_followed_by_default_and_not_when_switched_off(env):
    """Spec 2 (Follow redirects, default on): followed, the block gets the page redirected to; switched off, it gets
    the redirect itself (here counted as success), and with the default statuses a redirect fails it."""
    with serving() as api:
        api.on("/old", status=302, headers={"Location": "/new", "Content-Type": "text/plain"}, body_=b"moved")
        api.on_json("/new", {"page": "new"})
        off = {key("Follow redirects"): False}
        blocks = {
            "followed": request(f"{api.base}/old", y=0), "keep_followed": keep("followed", "status", "response", y=0),
            "stays": request(f"{api.base}/old", off, success("200-299, 302"), y=200),
            "keep_stays": keep("stays", "status", "headers", y=200),
            "refused": request(f"{api.base}/old", off, y=400),
        }
        edges = ["followed.status -> keep_followed.status", "followed.response -> keep_followed.response",
                 "stays.status -> keep_stays.status", "stays.headers -> keep_stays.headers"]
        result, run_id, status = run(env, blocks, edges)

    assert status == {"followed": "success", "keep_followed": "success", "stays": "success", "keep_stays": "success",
                      "refused": "failed"}, result.stdout[-3000:] + result.stderr[-3000:]
    assert kept(env, "followed") == {"status": 200, "response": {"page": "new"}}
    stays = kept(env, "stays")
    assert stays["status"] == 302 and lower(stays["headers"])["location"].endswith("/new")
    assert len(api.got("/new")) == 1  # only the block that follows redirects went there
    assert "302" in error_of(env, run_id, "refused")


def test_a_redirect_to_another_host_gets_none_of_the_credentials_and_one_on_the_same_host_keeps_them(env):
    """Spec 2 (Auth with stored secrets; Follow redirects) and the no-surprises rule: urllib on its own sends every
    header on to wherever a redirect leads; as curl and Postman do, a redirect to another host gets no Authorization
    (Bearer, Basic), no API key header and no header carrying a stored secret, while a plain header still goes; a
    redirect on the same host keeps the credentials."""
    values = {"token": secret("tok"), "password": secret("pw"), "key": secret("key"), "header": secret("hdr")}
    write_secrets(env, **values)
    names = ("bearer", "basic", "key", "header")
    with serving() as api, serving() as other:
        elsewhere = f"http://localhost:{other.httpd.server_address[1]}"  # (another host name for this same computer)
        for n in names:
            api.on(f"/away-{n}", status=302, body_=b"moved",
                   headers={"Location": f"{elsewhere}/landed-{n}", "Content-Type": "text/plain"})
        api.on("/stay", status=302, headers={"Location": "/landed", "Content-Type": "text/plain"}, body_=b"moved")
        blocks = {
            "bearer": request(f"{api.base}/away-bearer", bearer("token"), y=0),
            "basic": request(f"{api.base}/away-basic", basic("ann", "password"), y=200),
            "key": request(f"{api.base}/away-key", key_in_header("X-Api-Key", "key"), y=400),
            "header": request(f"{api.base}/away-header", y=600,
                              headers=rows(("X-Token", "secret:header"), ("X-Plain", "visible"))),
            "stay": request(f"{api.base}/stay", bearer("token"), y=800),
        }
        result, run_id, status = run(env, blocks)

    assert status == {b: "success" for b in blocks}, result.stdout[-3000:] + result.stderr[-3000:]
    # The first request did carry them.
    assert lower(api.got("/away-bearer")[0]["headers"])["authorization"] == f"Bearer {values['token']}"
    assert api.got("/away-key")[0]["headers"]["x-api-key"] == values["key"]
    assert api.got("/away-header")[0]["headers"]["x-token"] == values["header"]
    for n in names:
        [landed] = other.got(f"/landed-{n}")
        assert not {"authorization", "x-api-key", "x-token"} & set(landed["headers"]), (n, landed["headers"])
    for r in other.requests:
        assert not [v for v in values.values() if v in str(r)], r
    assert other.got("/landed-header")[0]["headers"]["x-plain"] == "visible"
    assert api.got("/landed")[0]["headers"]["authorization"] == f"Bearer {values['token']}"


# --- 2: certificates and proxies -------------------------------------------------------------------------------------------

def test_certificates_are_checked_a_ca_file_trusts_a_private_one_and_dont_verify_warns_on_every_run(env, tmp_path):
    """Spec 2 (Certificates: checked against the system's store by default; a CA certificate file setting; a "Don't
    verify certificates" switch, which warns in the Log on every run) and 6 (an API connection's certificate
    settings): a server whose certificate a private CA signed fails clearly by default; trusted with that CA's file
    (in the block or in its connection) or not verified, it answers."""
    ca, cert, private_key = certificates(tmp_path / "tls")
    ca_key, dont_verify = labelled("ca"), labelled("verify")
    with serving(tls=(cert, private_key)) as api:
        write_connections(env, secure=api_connection(api.base, {ca_key: str(ca)}))
        blocks = {
            "checked": request(f"{api.base}/data", y=0),
            "trusted": request(f"{api.base}/data", {ca_key: str(ca)}, y=200),
            "via_connection": request("/data", connection="secure", y=400),
            "unchecked": request(f"{api.base}/data", {dont_verify: True}, y=600),
        }
        result, run_id, status = run(env, blocks)
        again, again_id, again_status = run(env, {"unchecked": blocks["unchecked"]}, name="again")

    assert status == {"checked": "failed", "trusted": "success", "via_connection": "success",
                      "unchecked": "success"}, result.stdout[-3000:] + result.stderr[-3000:]
    assert "certificate" in error_of(env, run_id, "checked").lower()
    assert again_status == {"unchecked": "success"}, again.stderr[-3000:]

    def warned(run_number):
        return [m for level, m in logs_of(env, run_number, "unchecked")
                if level == "WARNING" and ("certificate" in m.lower() or "verif" in m.lower())]

    assert warned(run_id) and warned(again_id)
    assert not [m for level, m in logs_of(env, run_id, "trusted") if level == "WARNING"]


def test_proxy_settings_from_the_environment_are_honoured(env):
    """Spec 2 (proxy settings, HTTPS_PROXY etc., are honoured): with HTTP_PROXY and HTTPS_PROXY set, a request to a
    host this computer cannot even look up goes through the proxy (which answers it), and an HTTPS one asks the proxy
    for a tunnel to that host."""
    with serving() as proxy:
        proxy.on_json("/v1/ping", {"via": "proxy"})
        blocks = {"plain": request("http://orders.example.test/v1/ping", y=0), "keep_plain": keep("plain", "response"),
                  "secure": request("https://orders.example.test/v1/ping", y=200)}
        result, run_id, status = run(env, blocks, ["plain.response -> keep_plain.response"], proxy=proxy.base)

    assert status["plain"] == "success" and status["keep_plain"] == "success", (
        result.stdout[-3000:] + result.stderr[-3000:])
    assert kept(env, "plain")["response"] == {"via": "proxy"}
    targets = [(r["method"], r["target"]) for r in proxy.requests]
    assert ("GET", "http://orders.example.test/v1/ping") in targets, targets
    assert ("CONNECT", "orders.example.test:443") in targets, targets
    assert status["secure"] == "failed"  # (the proxy refuses the tunnel)


# --- 2, 5: retry, timeout and network errors -------------------------------------------------------------------------------

def test_the_retry_setting_tries_again_after_a_status_that_fails(env):
    """Spec 2 (the usual Retry and timeout section applies) and 5 (a status not counted as success fails the
    attempt): a server that is busy once (503) is asked again, and the second answer is the block's."""
    calls = []

    def busy_once(got):
        calls.append(got)
        return (503, {"Content-Type": "text/plain"}, b"busy") if len(calls) == 1 else json_answer({"done": True})

    with serving() as api:
        api.answers["/job"] = busy_once
        blocks = {"call": request(f"{api.base}/job", retry={"policy": "fixed", "delay": "100ms", "max_attempts": 3}),
                  "got": keep("got", "status", "response")}
        result, _, status = run(env, blocks, ["call.status -> got.status", "call.response -> got.response"])

    assert status == {"call": "success", "got": "success"}, result.stdout[-3000:] + result.stderr[-3000:]
    assert len(api.got("/job")) == 2
    assert kept(env, "got") == {"status": 200, "response": {"done": True}}


def test_a_server_that_never_answers_fails_at_the_timeout(env):
    """Spec 5 (a timeout fails clearly) and 2 (the usual Retry and timeout section applies): the block's Timeout of
    1 s stops it, well before Health Check's 10 s, saying it timed out."""
    with silent_port() as port:
        result, run_id, status = run(env, {"call": request(f"http://127.0.0.1:{port}/slow", timeout="1s")})

    assert status == {"call": "failed"}, result.stderr[-3000:]
    assert "timed out" in error_of(env, run_id, "call").lower()
    assert took(env, run_id, "call") < 8


def test_a_server_nobody_listens_on_fails_clearly_naming_the_address(env):
    """Spec 5 (a network error fails clearly)."""
    port = closed_port()
    result, run_id, status = run(env, {"call": request(f"http://127.0.0.1:{port}/orders")})

    assert status == {"call": "failed"}, result.stderr[-3000:]
    assert f"127.0.0.1:{port}" in error_of(env, run_id, "call")


def test_a_body_that_is_not_the_json_the_server_says_it_is_fails_clearly(env):
    """Spec 5 (a body that is not the JSON it says it is fails clearly) and 2 (Response as automatic)."""
    with serving() as api:
        api.on("/broken", body_=b"{not json", headers={"Content-Type": "application/json"})
        result, run_id, status = run(env, {"call": request(f"{api.base}/broken")})

    assert status == {"call": "failed"}, result.stderr[-3000:]
    assert "json" in error_of(env, run_id, "call").lower()


# --- 6: connections ----------------------------------------------------------------------------------------------------------

def test_a_connection_gives_its_base_url_auth_and_default_headers_and_the_blocks_own_headers_win(env):
    """Spec 6 (an API connection: base URL, auth with its stored secret, default headers; the block's URL relative to
    the base URL (/orders/{id}) or absolute; the block's own headers added to the connection's, the block's value
    winning on the same name)."""
    token = secret("tok")
    write_secrets(env, shop_token=token)
    with serving() as api:
        write_connections(env, shop=api_connection(f"{api.base}/api", bearer("secret:shop_token"),
                                                   headers=rows(("X-Client", "taskloom"), ("X-Version", "1"))))
        blocks = {"order": request("/orders/{id}", connection="shop",
                                   headers=rows(("X-Version", "2"), ("X-Extra", "yes"))),
                  "health": request(f"{api.base}/health", connection="shop", y=200)}
        result, _, status = run(env, blocks, params={"id": {"type": "int", "value": 7}})

    assert status == {"order": "success", "health": "success"}, result.stdout[-3000:] + result.stderr[-3000:]
    [order] = api.got("/api/orders/7")
    assert {name: order["headers"].get(name) for name in ("x-client", "x-version", "x-extra", "authorization")} == {
        "x-client": "taskloom", "x-version": "2", "x-extra": "yes", "authorization": f"Bearer {token}"}
    assert len(api.got("/health")) == 1
    assert token not in result.stdout + result.stderr
    assert files_holding(env.home, token) == []


# --- 8: the Log ------------------------------------------------------------------------------------------------------------

def test_the_log_says_what_was_sent_and_what_came_back_and_never_a_secret(env):
    """Spec 8 (the Log says what was sent: method and URL, never a secret; and what came back: status, size, time): an
    API key in the query is not shown in the URL the Log gives."""
    key_value = secret("key")
    write_secrets(env, report_key=key_value)
    answer = json.dumps({"note": "n" * 30}).encode()
    assert len(answer) == 42
    with serving() as api:
        api.on("/report", body_=answer, headers={"Content-Type": "application/json"})
        result, run_id, status = run(env, {"call": request(f"{api.base}/report", key_in_query("key", "report_key"))})

    assert status == {"call": "success"}, result.stderr[-3000:]
    messages = [m for _, m in logs_of(env, run_id, "call")]
    assert any("GET" in m and f"{api.base}/report" in m for m in messages), messages
    assert any(re.search(r"\b200\b", m) and re.search(r"\b42\b", m) and re.search(r"\d\s?(ms|s)\b", m)
               for m in messages), messages
    assert all(key_value not in m for m in messages)
    assert key_value not in result.stdout + result.stderr
    assert files_holding(env.home, key_value) == []


# --- 9: README and Help > Documentation -------------------------------------------------------------------------------------

def test_the_readme_and_the_documentation_describe_the_block_with_an_example():
    """Spec 9 (Help > Documentation and the README describe the block, with an example): README has a YAML example
    with an API Request whose settings the block accepts; Help > Documentation has the block's entry on the Servers
    page, and its guide (made from README) shows the example."""
    from taskloom.editor import docs

    cls = api_class()
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    examples = [text for text in re.findall(r"```ya?ml\n(.*?)```", readme, re.S) if API in text]
    assert examples, "README has no YAML example with an API Request (servers.api_request)"

    def api_specs(node):
        if isinstance(node, dict):
            if node.get("type") == API:
                yield node
            for value in node.values():
                yield from api_specs(value)
        elif isinstance(node, list):
            for value in node:
                yield from api_specs(value)

    specs = [spec for text in examples for spec in api_specs(yaml.safe_load(text))]
    assert specs, examples
    for spec in specs:
        block_config(cls, BlockSpec(id="example", type=API, config=dict(spec.get("config") or {})))
    pages = docs.pages(discover(None))
    servers = pages["blocks-servers.html"]
    assert f'id="{API}"' in servers and html.escape(cls.description) in servers
    guide = [name for name, page in pages.items()
             if name not in ("index.html", "properties.html") and not name.startswith("blocks-") and API in page]
    assert guide, "no guide page of Help > Documentation shows the API Request example"


# --- the editor: Send, Copy as cURL, Properties, the Connections window ------------------------------------------------------

def test_run_only_this_block_is_send_and_data_preview_shows_the_response(env, win, qtbot, monkeypatch):
    """Spec 5 (Run only this block is Postman's Send: the response shows in Data preview): right-click the block, Run
    only this block (with no input given), then its response is in Data preview; only that block ran."""
    from taskloom.editor import dialogs

    _env_for_runs(env)
    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", lambda self: True)
    with serving() as api:
        api.on_json("/orders/summary", {"orders": 3, "region": "north"})
        path = flow_file(env.root / "send.yaml", {"start": {"type": "trigger.manual", "ui": {"x": -400, "y": 0}},
                                                  "call": request(f"{api.base}/orders/summary")},
                         ["start.start -> call.after"])
        tab = win.open_flow(path)
        settle()
        [send] = [a for a in win._block_menu_for(tab, "call").actions()
                  if a.text().startswith("Run only this block") and a.isEnabled()][:1]
        send.trigger()
        qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.runs[-1].status is not None, timeout=60_000)

    assert tab.scene.nodes["call"].status == "success", win.log.text.toPlainText()[-3000:]
    select_only(tab, "call")
    ports = [win.preview.ports.itemText(i) for i in range(win.preview.ports.count())]
    assert "response" in ports, ports
    win.preview.ports.setCurrentText("response")
    settle()
    assert not win.preview.text.isHidden() and '"region": "north"' in win.preview.text.toPlainText()
    assert [r["path"] for r in api.requests] == ["/orders/summary"]
    clean(win)


def curl_args(command: str, values: dict) -> list[str]:
    """The arguments of a copied bash curl command, with each <hidden: secret NAME> put back as its value."""
    for name, value in values.items():
        command = command.replace(f"<hidden: secret {name}>", value)
    argv = shlex.split(re.sub(r"\\\r?\n", " ", command))
    assert argv and argv[0] == "curl", command
    return argv[1:]


def same_request(a: dict, b: dict) -> dict:
    """What two requests differ in (method, path, query, the headers a block sets, the body), as {what: (a, b)}."""
    def body_of(r):
        if r["headers"].get("content-type", "").startswith("application/json"):
            return json.loads(r["body"])
        return r["body"]

    differ = {what: (a[what], b[what]) for what in ("method", "path", "query") if a[what] != b[what]}
    for name in ("accept", "x-trace", "x-client", "x-version", "authorization", "content-type"):
        if name == "accept" and name not in a["headers"] and b["headers"].get(name) == "*/*":
            continue  # (curl's own Accept when none is set: any answer, as with none)
        if a["headers"].get(name) != b["headers"].get(name):
            differ[name] = (a["headers"].get(name), b["headers"].get(name))
    if body_of(a) != body_of(b):
        differ["body"] = (a["body"], b["body"])
    return differ


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is not installed here, to run the copied command")
def test_copy_as_curl_copies_the_request_filled_in_with_every_secret_hidden(env, win):
    """Spec 7 (right-click the block > Copy as cURL copies the request as a curl command, bash style, with the values
    filled in and every secret shown as <hidden: secret NAME>) and 6 (with a connection: its base URL, auth and
    headers, the block's header winning): the copied command holds no secret, and run with each secret put back where
    it says hidden, it sends the server the same request as the block does in a run."""
    values = {"api_password": secret("pw"), "shop_token": secret("tok")}
    write_secrets(env, **values)
    _env_for_runs(env)
    with serving() as api:
        write_connections(env, shop=api_connection(f"{api.base}/api", bearer("secret:shop_token"),
                                                   headers=rows(("X-Client", "taskloom"), ("X-Version", "1"))))
        blocks = {
            "create": request("{base}/orders", basic("report", "api_password"), json_body('["{region}", 2]'),
                              method="POST", params=rows(("region", "{region}"), ("debug", "1"), off=("debug",)),
                              headers=rows(("Accept", "application/json"), ("X-Trace", "{region}-1"))),
            "fetch": request("/orders/{region}", connection="shop", headers=rows(("X-Version", "2")), y=300),
        }
        params = {"base": {"type": "text", "value": api.base}, "region": {"type": "text", "value": "north"}}
        path = flow_file(env.root / "copy.yaml", blocks, (), params=params)
        result = env.taskloom("run", path)
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
        sent = {"create": api.got("/orders")[-1], "fetch": api.got("/api/orders/north")[-1]}

        tab = win.open_flow(path)
        settle()
        copied = {}
        for block in blocks:
            QApplication.clipboard().setText("")
            items = [a for a in win._block_menu_for(tab, block).actions() if a.text().replace("&", "").startswith(
                "Copy as cURL")]
            assert len(items) == 1 and items[0].isEnabled(), [a.text() for a in win._block_menu_for(tab, block).actions()]
            items[0].trigger()
            copied[block] = QApplication.clipboard().text()
        for block, command in copied.items():
            for value in values.values():
                assert value not in command, command
            assert "{region}" not in command and "north" in command, command
            done = subprocess.run([shutil.which("curl"), "--silent", "--show-error", "--output", os.devnull,
                                   *curl_args(command, values)], env=env.env, capture_output=True, text=True,
                                  timeout=60)
            assert done.returncode == 0, (command, done.stderr)
        by_curl = {"create": api.got("/orders")[-1], "fetch": api.got("/api/orders/north")[-1]}

    assert "<hidden: secret api_password>" in copied["create"], copied["create"]
    assert "<hidden: secret shop_token>" in copied["fetch"], copied["fetch"]
    for block in blocks:
        assert same_request(sent[block], by_curl[block]) == {}, (block, copied[block])
    clean(win)


# --- the editor: Properties ----------------------------------------------------------------------------------------------

def _in_combo(widget: QWidget) -> bool:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QComboBox):
            return True
        parent = parent.parentWidget()
    return False


def _plain(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def views(win) -> list[QAbstractItemView]:
    """The tables (item views) Properties shows: not the lists of its choice boxes."""
    return [v for v in win.properties.findChildren(QAbstractItemView)
            if v.model() is not None and not _in_combo(v) and not isinstance(v, QHeaderView)]


def shown(win) -> list[tuple[str, bool, str]]:
    """Every text Properties shows, as (text, whether you can change it there, its tooltip); a table's row is one
    text, its cells joined by " | "."""
    found = []
    for w in win.properties.findChildren(QWidget):
        if _in_combo(w):
            continue
        tip = _plain(w.toolTip())
        if isinstance(w, QLabel):
            found.append((_plain(w.text()), False, tip))
        elif isinstance(w, QLineEdit):
            found.append((w.text(), w.isEnabled() and not w.isReadOnly(), tip))
        elif isinstance(w, (QPlainTextEdit, QTextEdit)):
            found.append((w.toPlainText(), w.isEnabled() and not w.isReadOnly(), tip))
        elif isinstance(w, QComboBox):
            found.append((w.currentText(), w.isEnabled(), tip))
        elif isinstance(w, (QGroupBox, QCheckBox)):
            found.append((w.title() if isinstance(w, QGroupBox) else w.text(), False, tip))
        elif isinstance(w, QTabWidget):
            found += [(w.tabText(i), False, _plain(w.tabToolTip(i))) for i in range(w.count())]
        elif isinstance(w, QAbstractItemView) and w.model() is not None and not isinstance(w, QHeaderView):
            model, editing = w.model(), w.isEnabled() and w.editTriggers() != QAbstractItemView.EditTrigger.NoEditTriggers
            for r in range(model.rowCount()):
                cells = [model.index(r, c) for c in range(model.columnCount())]
                texts = [str(model.data(i, Qt.DisplayRole)) for i in cells if model.data(i, Qt.DisplayRole) is not None]
                editable = editing and any(model.flags(i) & Qt.ItemIsEditable for i in cells)
                tips = " ".join(_plain(str(model.data(i, Qt.ToolTipRole) or "")) for i in cells)
                found.append((" | ".join(texts), bool(editable), tips))
    return found


def _check_box(view, index):
    widget = view.indexWidget(index)
    if isinstance(widget, QCheckBox):
        return widget
    return widget.findChild(QCheckBox) if widget is not None else None


def _tick_state(model, index) -> bool | None:
    """Whether the cell's tick is on (None: the cell has no tick; a cell that can take one but shows none has none)."""
    state = model.data(index, Qt.CheckStateRole)
    if state is None or not model.flags(index) & Qt.ItemIsUserCheckable:
        return None
    return state in (Qt.Checked, 2) or getattr(state, "value", None) == 2


def table_rows(view) -> list[tuple[list[str], bool | None]]:
    """A table's rows: (its cells' texts, whether its tick is on; None: it has none)."""
    model, found = view.model(), []
    for r in range(model.rowCount()):
        texts, tick = [], None
        for c in range(model.columnCount()):
            index = model.index(r, c)
            text = model.data(index, Qt.DisplayRole)
            if isinstance(text, str) and text:
                texts.append(text)
            if (state := _tick_state(model, index)) is not None:
                tick = state
            elif (box := _check_box(view, index)) is not None:
                tick = box.isChecked()
        found.append((texts, tick))
    return found


def params_table(win, wanted_key: str):
    """The table in Properties with a row whose key is `wanted_key`, and that row's number."""
    for view in views(win):
        for r, (texts, _) in enumerate(table_rows(view)):
            if wanted_key in texts:
                return view, r
    raise AssertionError(f"no table in Properties shows a row {wanted_key!r}: "
                         f"{[table_rows(v) for v in views(win)]}")


def set_cell_after(view, r: int, cell_text: str, new: str):
    """Change the cell right of the cell showing `cell_text` (the value next to a key) in row r, as typing does."""
    model = view.model()
    columns = [c for c in range(model.columnCount()) if model.data(model.index(r, c), Qt.DisplayRole) == cell_text]
    index = model.index(r, columns[0] + 1)
    widget = view.indexWidget(index)
    if isinstance(widget, QLineEdit):
        widget.setText(new)
        widget.editingFinished.emit()
    else:
        assert model.setData(index, new, Qt.EditRole), "the value cell cannot be changed"


def untick(view, r: int):
    model = view.model()
    for c in range(model.columnCount()):
        index = model.index(r, c)
        if _tick_state(model, index) is not None:
            assert model.setData(index, Qt.CheckState.Unchecked, Qt.CheckStateRole)
            return
        if (box := _check_box(view, index)) is not None:
            if box.isChecked():
                box.click()
            return
    raise AssertionError("the row has no tick")


def url_field(win, base: str) -> QLineEdit:
    found = [e for e in win.properties.findChildren(QLineEdit) if e.text().startswith(base) and e.isEnabled()
             and not e.isReadOnly()]
    assert len(found) == 1, [e.text() for e in win.properties.findChildren(QLineEdit)]
    return found[0]


def test_params_and_the_urls_query_stay_in_step_when_either_is_edited_in_properties(env, win):
    """Spec 2 (Params kept in step with the URL's ?query when edited in either): typing a query in the URL shows its
    rows in Params, ticked; changing a value and unticking a row in Params changes the URL; the flow saved so runs
    with each param once, as Params shows it."""
    with serving() as api:
        path = flow_file(env.root / "params.yaml", {"call": request(f"{api.base}/search")})
        tab = win.open_flow(path)
        select_only(tab, "call")
        edit = url_field(win, api.base)
        edit.setText(f"{api.base}/search?q=report&page=2")
        edit.editingFinished.emit()
        settle()
        view, r = params_table(win, "q")
        shown_rows = {texts[0]: (texts[1:], tick) for texts, tick in table_rows(view) if texts}
        assert shown_rows.get("q") == (["report"], True) and shown_rows.get("page") == (["2"], True), shown_rows

        set_cell_after(view, r, "q", "invoice")
        settle()
        view, page = params_table(win, "page")
        untick(view, page)
        settle()
        assert url_field(win, api.base).text() == f"{api.base}/search?q=invoice"
        assert win.save(tab)
        _env_for_runs(env)
        result = env.taskloom("run", path)

    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    assert [r["query"] for r in api.got("/search")] == [[("q", "invoice")]]
    clean(win)


def test_with_the_body_input_connected_the_body_section_says_the_body_comes_from_it(env, win):
    """Spec 4 (when the `body` input is connected the Body section says the body comes from that input)."""
    path = flow_file(env.root / "fed.yaml", {
        "make": make("{'id': 7}"),
        "call": request("http://127.0.0.1:9/orders", method="POST"),
        "other": request("http://127.0.0.1:9/orders", method="POST", y=300),
    }, ["make.output -> call.body"])
    tab = win.open_flow(path)
    select_only(tab, "other")
    unconnected = {text for text, _, _ in shown(win)}
    select_only(tab, "call")
    connected = {text for text, _, _ in shown(win)}

    said = [t for t in connected - unconnected if "body" in t.lower() and "input" in t.lower()]
    assert said, sorted(connected - unconnected)
    clean(win)


def test_with_a_connection_chosen_its_values_show_read_only_and_the_blocks_header_that_wins_is_shown(env, win):
    """Spec 6 (with a connection chosen its values show read-only in Properties; the block's own headers are added to
    the connection's, the block's value winning on the same name, and Properties shows that): the base URL, the
    connection's headers and the name of its stored secret show where they cannot be changed, the replaced header
    says so, and the secret's value shows nowhere."""
    token = secret("tok")
    write_secrets(env, shop_token=token)
    base = "http://127.0.0.1:9/api"
    write_connections(env, shop=api_connection(base, bearer("secret:shop_token"),
                                               headers=rows(("X-Client", "taskloom"), ("X-Version", "1"))))
    path = flow_file(env.root / "conn.yaml", {"call": request("/orders/7", connection="shop",
                                                              headers=rows(("X-Version", "2")))})
    tab = win.open_flow(path)
    select_only(tab, "call")
    texts = shown(win)

    with_base = [(t, editable) for t, editable, _ in texts if base in t]
    assert with_base and not any(editable for _, editable in with_base), with_base
    with_client = [(t, editable) for t, editable, _ in texts if "X-Client" in t]
    assert with_client and not any(editable for _, editable in with_client), with_client
    assert any("shop_token" in t and not editable for t, editable, _ in texts), texts
    replaced = re.compile(r"replac|overrid|instead|wins|precedence", re.I)
    assert any("X-Version" in t and (replaced.search(t) or replaced.search(tip)) for t, _, tip in texts), [
        x for x in texts if "X-Version" in x[0]]
    assert not any(token in t or token in tip for t, _, tip in texts)
    clean(win)


def form_field(dialog, wanted: str):
    """The field of the shown row of the Connections window whose label is `wanted` (or else has it in it); None: no
    such row is shown."""
    form, rows_ = dialog.form, []
    for r in range(form.rowCount()):
        name, value = form.itemAt(r, QFormLayout.LabelRole), form.itemAt(r, QFormLayout.FieldRole)
        if not (name and value and isinstance(name.widget(), QLabel) and value.widget()):
            continue
        if value.widget().isHidden():
            continue
        rows_.append((" ".join(name.widget().text().lower().split()), value.widget()))
    found = [w for text, w in rows_ if text == wanted] or [w for text, w in rows_ if wanted in text]
    if not found:
        return None
    widget = found[0]
    if type(widget) is QWidget:  # a box: a text and its buttons
        widget = widget.findChild(QLineEdit) or widget.findChild(QComboBox) or widget
    return widget


def test_an_api_connection_made_in_the_connections_window_keeps_its_token_secret_and_runs(env, win, monkeypatch):
    """Spec 6 (an API connection: a new kind in the Connections window, with its base URL and auth with its stored
    secret): made there, it is saved as an api connection with the token in the secret store, never in
    connections.yaml, and a block using it calls its base URL with the token."""
    import keyring.core
    from keyring.backends import fail

    from taskloom.editor.dialogs import ConnectionsDialog

    monkeypatch.setattr(keyring.core, "_keyring_backend", fail.Keyring())  # never the real credential store
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("shop", True)))
    token = secret("tok")
    with serving() as api:
        dialog = ConnectionsDialog(win.home, win, new_kind="api")
        kinds = {dialog.kind.itemData(i): dialog.kind.itemText(i) for i in range(dialog.kind.count())}
        assert "api" in kinds and "API" in kinds["api"], kinds
        base = form_field(dialog, "base url")
        assert isinstance(base, QLineEdit), "the Connections window shows no Base URL row for an API connection"
        base.setText(f"{api.base}/v1")
        auth = form_field(dialog, "auth")
        assert isinstance(auth, QComboBox), "the Connections window shows no Auth row for an API connection"
        choice = next(i for i in range(auth.count()) if "bearer" in auth.itemText(i).lower())
        auth.setCurrentIndex(choice)
        auth.activated.emit(choice)
        QApplication.processEvents()
        (form_field(dialog, "token") or dialog.password).setText(token)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()

        text = (env.home / "connections.yaml").read_text(encoding="utf-8")
        assert token not in text
        shop = yaml.safe_load(text)["shop"]
        assert shop["kind"] == "api" and shop["base_url"] == f"{api.base}/v1", shop
        refs = [v for v in shop.values() if isinstance(v, str) and v.startswith("secret:")]
        assert len(refs) == 1 and win.home.get_secret(refs[0][len("secret:"):]) == token, shop
        result, _, status = run(env, {"ping": request("/ping", connection="shop")})

    assert status == {"ping": "success"}, result.stdout[-3000:] + result.stderr[-3000:]
    [got] = api.got("/v1/ping")
    assert got["headers"].get("authorization") == f"Bearer {token}"


def test_an_api_connections_headers_and_certificate_settings_made_in_the_connections_window_are_kept_and_used(
        env, win, monkeypatch, tmp_path):
    """Spec 6 (an API connection in the Connections window: default headers, certificate settings) and 2 (a "Don't
    verify certificates" switch, which warns in the Log): the headers, the CA certificate file and Don't verify set
    there are saved, kept when the connection is opened again and saved with no change, and used by the blocks."""
    import keyring.core
    from keyring.backends import fail

    from taskloom.editor.dialogs import ConnectionsDialog

    monkeypatch.setattr(keyring.core, "_keyring_backend", fail.Keyring())  # never the real credential store
    ca, cert, server_key = certificates(tmp_path / "tls")
    headers_key, ca_key, dont_verify = key("Headers"), labelled("ca"), labelled("verify")

    def new_connection(name: str, base_url: str):
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (name, True)))
        dialog = ConnectionsDialog(win.home, win, new_kind="api")
        form_field(dialog, "base url").setText(base_url)
        return dialog

    def save(dialog):
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()

    def saved() -> dict:
        return yaml.safe_load((env.home / "connections.yaml").read_text(encoding="utf-8"))

    with serving(tls=(cert, server_key)) as api:
        dialog = new_connection("trusted", api.base)
        tables = [t for t in dialog.findChildren(QTableWidget) if not t.isHidden()]
        assert len(tables) == 1, "the Connections window shows no one table for an API connection's headers"
        table = tables[0]
        next(b for b in dialog.findChildren(QPushButton) if b.text().lower().startswith("add") and not b.isHidden()
             ).click()
        table.item(table.rowCount() - 1, 0).setText("X-Client")
        table.item(table.rowCount() - 1, 1).setText("taskloom")
        form_field(dialog, "ca").setText(str(ca))
        save(dialog)

        dialog = new_connection("unchecked", api.base)
        next(b for b in dialog.findChildren(QCheckBox) if "verify" in b.text().lower() and not b.isHidden()
             ).setChecked(True)
        save(dialog)

        first = saved()
        trusted, unchecked = first["trusted"], first["unchecked"]
        assert trusted.get(headers_key) == rows(("X-Client", "taskloom")), trusted
        assert trusted.get(ca_key) == str(ca) and not trusted.get(dont_verify), trusted
        assert unchecked.get(dont_verify) is True, unchecked
        for name in ("trusted", "unchecked"):
            save(ConnectionsDialog(win.home, win, select=name))  # opened again, saved with no change
            assert saved() == first, name

        result, run_id, status = run(env, {"a": request("/data", connection="trusted"),
                                           "b": request("/data", connection="unchecked", y=200)})

    assert status == {"a": "success", "b": "success"}, result.stdout[-3000:] + result.stderr[-3000:]
    assert len([r for r in api.got("/data") if r["headers"].get("x-client") == "taskloom"]) == 1

    def warned(block: str) -> list[str]:
        return [m for level, m in logs_of(env, run_id, block)
                if level == "WARNING" and ("certificate" in m.lower() or "verif" in m.lower())]

    assert warned("b") and not warned("a"), (logs_of(env, run_id, "a"), logs_of(env, run_id, "b"))
