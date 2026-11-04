"""Server blocks over SSH: run commands, start/stop apps, health checks, extract archives; API Request, a call to a
web API as Postman sends one; and the Session, whose API Request blocks share their cookies."""

from __future__ import annotations

import base64
import codecs
import http.client
import json
import re
import shlex
import socket
import ssl
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import polars as pl

from .. import ssh
from ..block import Block, fields, ports, setting_label
from ..config import SECRET_PREFIX
from ..engine import Failed, HiddenStr
from ..flow import API_REQUEST, SESSION, parse_duration
from ..table import Table


@contextmanager
def _client(ctx, name):
    session = ssh.Session(ctx.connection(name), ctx.home)
    try:
        yield session
    finally:
        session.close()


def _run(ctx, session, command) -> tuple[int, str, str]:
    return session.run(command, ctx.check_cancelled)


class SSHCommand(Block):
    type_id = "servers.ssh_command"
    title = "SSH Command"
    description = 'Runs a command on a server, like typing it in a terminal there, and gives you what it printed.'
    category = "Servers"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
    outputs = {"stdout": ports.Any(), "exit_code": ports.Any()}
    config = {
        "connection": fields.Connection(kind="ssh", help="The server to run the command on: one of your SSH connections (Tools > Connections)"),
        "command": fields.Text(help="Runs in your login shell on the server",
                               examples=["df -h", "systemctl --user restart report", "ls ~/incoming"]),
        "fail_on_error": fields.Bool(default=True, help="Fail the block when the command exits with a non-zero code"),
    }

    def run(self, ctx, after=None):
        with _client(ctx, self.config.connection) as client:
            code, out, err = _run(ctx, client, self.config.command)
        if out.strip():
            ctx.log(out.rstrip())
        if err.strip():
            ctx.log(err.rstrip(), "WARNING")
        if code != 0 and self.config.fail_on_error:
            raise RuntimeError(f"command exited with code {code}: {err.strip()[-500:]}")
        return {"stdout": out, "exit_code": code}


class StartProcess(Block):
    type_id = "servers.start_process"
    title = "Start Remote Process"
    description = 'Starts a program on a server so it keeps running after Taskloom disconnects, and checks it did not crash right away.'
    category = "Servers"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
    outputs = {"pid": ports.Any()}
    config = {
        "connection": fields.Connection(kind="ssh", help="The server to start it on: one of your SSH connections (Tools > Connections)"),
        "command": fields.Text(help="e.g. ~/venv/bin/python app.py --port 8501",
                               examples=["~/venv/bin/python app.py --port 8501", "./run-report.sh",
                                         "java -jar report.jar"]),
        "workdir": fields.Text(default="~", help="The folder on the server the command runs in (~: your home "
                                                 "folder)", examples=["~", "~/app", "/opt/report"]),
        "log_file": fields.Text(default="~/.taskloom/apps/app.log",
                                help="The file on the server that gets what the command prints; if it stops right "
                                     "away, its last 20 lines are in the error",
                                examples=["~/.taskloom/apps/app.log", "~/app/app.log"]),
        "pid_file": fields.Text(default="~/.taskloom/apps/app.pid",
                                help="The file on the server that keeps its process number, for Stop Remote "
                                     "Process and Health Check to find it",
                                examples=["~/.taskloom/apps/app.pid", "~/app/app.pid"]),
        "check_after": fields.Float(default=2.0, help="Seconds to wait before checking it is still running",
                                    examples=["2", "5", "0.5"]),
        "must_keep_running": fields.Bool(default=True, help="Untick for a command that does its job and finishes "
                                                            "(e.g. a script that starts a service): exit code 0 is then fine"),
    }

    def run(self, ctx, after=None):
        c = self.config
        command = c.command.rstrip()
        if command.endswith("&") and not command.endswith("&&"):
            command = command[:-1].rstrip()
            ctx.log("removed the & at the end: this block already runs the command in the background, "
                    "and it needs to keep track of it")
        exit_file = ssh.remote_path(ssh.exit_file(c.pid_file))
        # Start it, wait, and check it, all in one command: some servers allow one command per connection.
        script = (f"{ssh.start_detached(command, c.workdir, c.log_file, c.pid_file)} && sleep {c.check_after:g} && "
                  f"if kill -0 \"$(cat {ssh.remote_path(c.pid_file)})\" 2>/dev/null; then echo TASKLOOM_RUNNING; "
                  f"else echo \"TASKLOOM_EXITED $(cat {exit_file} 2>/dev/null)\"; tail -n 20 {ssh.remote_path(c.log_file)}; fi")
        with _client(ctx, c.connection) as session:
            code, out, err = _run(ctx, session, script)
        lines = out.strip().splitlines()
        if code != 0 or len(lines) < 2 or not lines[0].strip().isdigit():
            raise RuntimeError(f"could not start the process: {(err or out).strip()[-1000:]}")
        pid = int(lines[0])
        if lines[1].strip() == "TASKLOOM_RUNNING":
            ctx.log(f"started process {pid}; still running after {c.check_after:g} s")
            return {"pid": pid}
        exit_code = lines[1].split()[1] if len(lines[1].split()) > 1 else "unknown"
        log = "\n".join(lines[2:]).strip()
        if exit_code == "0" and not c.must_keep_running:
            ctx.log(f"the command finished with exit code 0{': ' + log if log else ''}")
            return {"pid": pid}
        if exit_code == "0":
            problem = ("the command finished right away (exit code 0), so nothing is left running. If it starts "
                       "the program in the background itself, or is a script that starts a service, untick "
                       "must_keep_running; otherwise check that it runs in the foreground.")
        else:
            problem = f"the command exited with code {exit_code} right after starting."
        if ">" in command:
            problem += " Your command writes its output to its own file: the reason is probably there."
        raise RuntimeError(problem + (f"\nLast lines of {c.log_file}:\n{log}" if log else ""))


class StopProcess(Block):
    type_id = "servers.stop_process"
    title = "Stop Remote Process"
    description = 'Stops a program on a server that was started with Start Remote Process.'
    category = "Servers"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
    outputs = {"stopped": ports.Any()}
    config = {
        "connection": fields.Connection(kind="ssh", help="The server it runs on: one of your SSH connections (Tools > Connections)"),
        "pid_file": fields.Text(default="~/.taskloom/apps/app.pid",
                                help="The PID file of the Start Remote Process that started it",
                                examples=["~/.taskloom/apps/app.pid", "~/app/app.pid"]),
        "grace": fields.Float(default=10.0, help="Seconds to wait after asking it to stop before forcing it",
                              examples=["10", "30", "2"]),
    }

    def run(self, ctx, after=None):
        c = self.config
        pid_file = ssh.remote_path(c.pid_file)
        with _client(ctx, c.connection) as client:
            code, out, _ = _run(ctx, client, f"cat {pid_file} 2>/dev/null")
            pid = out.strip()
            if code != 0 or not pid.isdigit() or _run(ctx, client, f"kill -0 {pid}")[0] != 0:
                ctx.log("the process was not running")
                return {"stopped": False}
            # Everything the command started, found before signalling: the shell running it can end
            # first while the app is still shutting down (or ignores the request).
            members = _run(ctx, client, ssh.processes(pid))[1].split()
            _run(ctx, client, f"kill -TERM {' '.join(members)} 2>/dev/null; true")
            deadline = time.monotonic() + c.grace
            running = members
            while running and time.monotonic() < deadline:
                ctx.wait(0.5)
                running = _run(ctx, client, ssh.alive(running))[1].split()
            if running:
                ctx.warn(f"{len(running)} process(es) did not stop in {c.grace:g} s; forcing them")
                _run(ctx, client, f"kill -KILL {' '.join(running)} 2>/dev/null; true")
        ctx.log(f"stopped process {pid}")
        return {"stopped": True}


class HealthCheck(Block):
    type_id = "servers.health_check"
    title = "Health Check"
    description = ("Checks that an app is alive: its web address answers, its process is running, or its port is "
                   "open. Continues down 'healthy' or 'unhealthy'; 'result' always says which, for checking or emails.")
    category = "Servers"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
    outputs = {"healthy": ports.Any(), "unhealthy": ports.Any(), "result": ports.Any()}
    config = {
        "url": fields.Text(required=False, help="Checked from this machine",
                           examples=["http://appserver:8501/", "https://reports.example.com/health"]),
        "expect_status": fields.Int(default=200, help="The HTTP status the URL must answer with",
                                    examples=["200", "204", "302"]),
        "connection": fields.Connection(kind="ssh", required=False, help="Needed for the PID file check"),
        "pid_file": fields.Text(required=False, help="On the server: the process in it must be running",
                                examples=["~/.taskloom/apps/app.pid", "~/app/app.pid"]),
        "port": fields.Int(required=False, help="TCP port on the connection's host that must accept connections",
                           examples=["8501", "5432", "22"]),
        "timeout": fields.Float(default=10.0, help="Seconds to wait for the URL to answer, and for the port to "
                                                   "accept a connection", examples=["10", "30", "2.5"]),
    }

    def run(self, ctx, after=None):
        c = self.config
        problems, checks = [], []
        if c.url:
            checks.append("url")
            try:
                with urllib.request.urlopen(c.url, timeout=c.timeout) as response:
                    status = response.status
            except urllib.error.HTTPError as e:
                status = e.code
            except OSError as e:
                status = None
                problems.append(f"{c.url}: {getattr(e, 'reason', e)}")
            except http.client.InvalidURL:
                raise  # a mistake in the url, not an unhealthy app
            except http.client.HTTPException as e:  # it answered, but not with HTTP (e.g. another program's port)
                status = None
                problems.append(f"{c.url} did not answer like a web server ({type(e).__name__}: {str(e).strip()})")
            if status is not None and status != c.expect_status:
                problems.append(f"{c.url} returned {status}, expected {c.expect_status}")
        if c.pid_file or c.port:
            if not c.connection:
                raise ValueError("the PID file and Port checks need a Connection")
            conn = ctx.connection(c.connection)
        if c.pid_file:
            checks.append("pid")
            with _client(ctx, c.connection) as client:
                path = ssh.remote_path(c.pid_file)
                if _run(ctx, client, f"kill -0 \"$(cat {path} 2>/dev/null)\" 2>/dev/null")[0] != 0:
                    problems.append(f"no running process for {c.pid_file}")
        if c.port:
            checks.append("port")
            try:
                socket.create_connection((conn["host"], c.port), timeout=c.timeout).close()
            except OSError as e:
                problems.append(f"port {c.port} on {conn['host']}: {e}")
        if not checks:
            raise ValueError("set at least one check: URL, PID file or Port")
        details = {"healthy": not problems, "checks": checks, "problems": problems}
        if problems:
            ctx.warn("unhealthy: " + "; ".join(problems))
            return {"unhealthy": details, "result": details}
        ctx.log(f"healthy ({', '.join(checks)})")
        return {"healthy": details, "result": details}


class RemoteExtract(Block):
    type_id = "servers.remote_extract"
    title = "Remote Extract"
    description = 'Unpacks a .zip or .tar.gz file on a server into a folder.'
    category = "Servers"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
    outputs = {"folder": ports.Any()}
    config = {
        "connection": fields.Connection(kind="ssh", help="The server the archive is on: one of your SSH connections (Tools > Connections)"),
        "archive": fields.Text(help=".tar.gz, .tgz, .tar or .zip on the server",
                               examples=["~/incoming/app-1.4.tar.gz", "~/releases/report.zip"]),
        "destination": fields.Text(help="The folder on the server to unpack it into (made if missing); files there "
                                        "with the same names are replaced", examples=["~/app", "~/releases/1.4"]),
    }

    def run(self, ctx, after=None):
        c = self.config
        archive, dest = ssh.remote_path(c.archive), ssh.remote_path(c.destination)
        tool = f"unzip -o -q {archive} -d {dest}" if c.archive.lower().endswith(".zip") else f"tar -xf {archive} -C {dest}"
        with _client(ctx, c.connection) as client:
            code, _, err = _run(ctx, client, f"mkdir -p {dest} && {tool}")
        if code != 0:
            raise RuntimeError(f"could not extract {c.archive}: {err.strip()}")
        ctx.log(f"extracted {c.archive} to {c.destination}")
        return {"folder": c.destination}


# --- API Request: a call to a web API, as Postman sends one ----------------------------------------------------------

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
NO_AUTH, BASIC, BEARER, KEY_HEADER, KEY_QUERY, DIGEST = (
    "none", "Basic", "Bearer token", "API key in a header", "API key in the query", "Digest")
AUTHS = (NO_AUTH, BASIC, BEARER, KEY_HEADER, KEY_QUERY, DIGEST)
# The settings each auth needs (with a connection: in the connection).
AUTH_NEEDS = {"username": (BASIC, DIGEST), "key_name": (KEY_HEADER, KEY_QUERY), "secret": AUTHS[1:]}
NO_BODY, JSON_BODY, TEXT_BODY, XML_BODY, FORM_BODY, MULTIPART, FILE_BODY = (
    "none", "JSON", "text", "XML", "form (url-encoded)", "multipart form-data", "file")
BODIES = (NO_BODY, JSON_BODY, TEXT_BODY, XML_BODY, FORM_BODY, MULTIPART, FILE_BODY)
# The Content-Type each kind of body sends, unless Headers has one (multipart: its own, with its boundary).
CONTENT_TYPES = {JSON_BODY: "application/json", TEXT_BODY: "text/plain; charset=utf-8",
                 XML_BODY: "application/xml; charset=utf-8", FORM_BODY: "application/x-www-form-urlencoded",
                 FILE_BODY: "application/octet-stream"}
AUTOMATIC, AS_TEXT, AS_TABLE, AS_FILE = "automatic", "text", "table", "file"
# What an API connection (kind: api in connections.yaml) gives the blocks that use it, besides its base_url, under the
# block's own names (its stored secret as secret:NAME); with a connection chosen, the block has none of these of its own.
FROM_CONNECTION = ("auth", "username", "key_name", "secret", "headers", "ca_file", "dont_verify")
_NO_CONNECTION = {"connection": [None]}  # (only_if of the block's own: with a connection chosen, they come from it)
NOTHING = object()  # (nothing came to the body input: it is not connected)
_WHOLE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")  # a whole web address, not one relative to a base URL
_URL_SAFE = "!#$%&'()*+,/:;=?@[]~"  # what a URL holds as it is (a space, say, is written %20)
_QUERY_SAFE = "{}:/@!$'()*,;"  # what Properties shows as typed in a URL's query (placeholders among them)


def success_statuses(text) -> list[tuple[int, int]]:
    """Statuses that count as success as written, e.g. 200-299, 404: each status, or range of them, as (first, last)."""
    found = []
    for part in str(text).split(","):
        m = re.fullmatch(r"\s*([1-5]\d\d)\s*(?:-\s*([1-5]\d\d)\s*)?", part)
        if m is None or m[2] and int(m[2]) < int(m[1]):
            raise ValueError(f"{part.strip()!r} is neither a status (100 to 599) nor a range of them (e.g. 200-299): "
                             "write them separated by commas, e.g. 200-299, 404")
        found.append((int(m[1]), int(m[2] or m[1])))
    return found


class _Statuses(fields.Text):
    """Statuses that count as success: read before the run starts (so they take no placeholders)."""

    template = False

    def coerce(self, value):
        success_statuses(value)
        return str(value)


def ticked(rows) -> list[dict]:
    """The ticked rows of a key/value table (fields.KeyValues), the ones that are sent."""
    return [r for r in rows if isinstance(r, dict) and r.get("on", True)] if isinstance(rows, list) else []


def hidden(name: str) -> str:
    """A stored secret as the Log and Copy as cURL show it: by its name, never its value."""
    return f"<hidden: secret {name}>"


def joined_url(base: str, url: str) -> str:
    """The block's URL with its connection's base URL: as it is when it is whole (http://…), else added to the end of
    the base URL (http://h/api and /orders/7: http://h/api/orders/7)."""
    if _WHOLE.match(url):
        return url
    if not url or url.startswith("?"):
        return base + url
    return f"{base.rstrip('/')}/{url.lstrip('/')}"


def _with_query(url: str, query: list[str]) -> str:
    if not query:
        return url
    return url + ("" if url.endswith(("?", "&")) else "&" if "?" in url else "?") + "&".join(query)


def shown_url(url, params) -> str:
    """The URL as Properties shows it: with its ticked Params as its query (each as typed, but for what would break it
    up, e.g. & and =, written as a URL writes it)."""
    return _with_query(str(url or ""), [f"{quote(str(r.get('key', '')), safe=_QUERY_SAFE)}="
                                        f"{quote(str(r.get('value', '')), safe=_QUERY_SAFE)}" for r in ticked(params)])


def url_and_params(text: str, params) -> tuple[str, list[dict]]:
    """A URL typed in Properties: the URL before its ?, and Params made of its query (each ticked), followed by the
    Params that were not ticked (they stay)."""
    url, _, query = text.partition("?")
    rows = [{"key": k, "value": v} for k, v in parse_qsl(query, keep_blank_values=True)]
    return url, rows + [r for r in (params if isinstance(params, list) else []) if isinstance(r, dict)
                        and not r.get("on", True)]


def api_connection(home, name: str | None) -> dict | None:
    """The API connection `name` (None: none is chosen), its base_url and each of FROM_CONNECTION checked as the block's
    own settings are (its secret: the stored secret's name)."""
    if not name:
        return None
    conns = home.connections()
    if name not in conns:
        raise Failed(f"Connection: no connection named '{name}' in connections.yaml")
    conn, where = conns[name], f"connection '{name}'"
    if conn["kind"] != "api":
        raise Failed(f"Connection: {where} is {conn['kind']}, not an API connection")
    unknown = set(conn) - {"kind", "base_url", *FROM_CONNECTION}
    if unknown:
        raise Failed(f"{where}: unknown setting(s) {sorted(unknown)}; an API connection has base_url, "
                     f"{', '.join(FROM_CONNECTION)}")
    checked = {"base_url": str(conn.get("base_url") or "")}
    for key in FROM_CONNECTION:
        try:
            checked[key] = ApiRequest.config[key].default if conn.get(key) is None else ApiRequest.config[key].coerce(
                conn[key])
        except ValueError as e:
            raise Failed(f"{where}: {key}: {e}") from None
    if checked["secret"] is not None:
        if not checked["secret"].startswith(SECRET_PREFIX):
            raise Failed(f"{where}: secret: write {SECRET_PREFIX}NAME, NAME a stored secret (taskloom secret set NAME), "
                         f"not the secret itself")
        checked["secret"] = checked["secret"][len(SECRET_PREFIX):]
    return checked


@dataclass
class _Call:
    """A request as the block sends it, or as Copy as cURL writes it."""
    method: str
    url: str  # with the ticked Params as its query (and an API key in the query)
    headers: dict  # {lower-case name: (name, value)}, in the order they are sent
    credentials: set  # the lower-case names of those not sent on to another host: Authorization, and those that
    # carry a stored secret
    auth: tuple | None  # (Basic or Digest, user name, password)
    body: str  # the kind of body (BODIES), or "input": the body input's value
    sends: object  # its content (text), its rows, the path of its file, or the input's value
    ca_file: str | None
    verify: bool


def _call(c: dict, conn: dict | None, secret, given=NOTHING) -> _Call:
    """The request of the API Request whose settings (their placeholders filled in) are `c`, with its API connection
    `conn` (api_connection; None: none), and what came to its body input (`given`). secret(name): a stored secret, as
    it is sent (Home.get_secret) or shown (hidden)."""
    own = conn or c  # (where its auth and certificate settings are)
    url = c["url"]
    if conn is not None and not _WHOLE.match(url):
        if not conn["base_url"]:
            raise Failed(f"URL: {url!r} is relative to the connection's base URL, but the connection has no base_url")
        url = joined_url(conn["base_url"], url)
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise Failed(f"URL: {url!r} is not a web address: write it whole, from http:// or https:// (or relative to the "
                     "base URL of the API connection you choose in Connection)")
    auth = own["auth"]
    missing = [setting_label(k, ApiRequest.config[k]) for k, kinds in AUTH_NEEDS.items() if auth in kinds and not own[k]]
    if missing:
        raise Failed(f"Auth {auth} needs {' and '.join(missing)}" + (f" in the connection {c['connection']}" if conn
                                                                     else ""))
    password = secret(own["secret"]) if auth != NO_AUTH else None
    query = [f"{quote(r['key'], safe='')}={quote(r['value'], safe='')}" for r in ticked(c["params"])]
    if auth == KEY_QUERY:  # (shown as hidden() shows it, as it is: Copy as cURL is read by people)
        query.append(f"{quote(own['key_name'], safe='')}={password if secret is hidden else quote(password, safe='')}")
    rows = {r["key"].lower(): r for r in ticked(conn and conn["headers"])}
    rows.update({r["key"].lower(): r for r in ticked(c["headers"])})  # the block's win
    def value(text: str) -> str:  # (secret:NAME: the stored secret NAME)
        return secret(text[len(SECRET_PREFIX):]) if text.startswith(SECRET_PREFIX) else text

    headers = {name: (r["key"], value(r["value"])) for name, r in rows.items()}
    credentials = {"authorization"} | {name for name, r in rows.items() if r["value"].startswith(SECRET_PREFIX)}
    if auth == BEARER:
        headers["authorization"] = ("Authorization", f"Bearer {password}")
    elif auth == KEY_HEADER:
        headers[own["key_name"].lower()] = (own["key_name"], password)
        credentials.add(own["key_name"].lower())
    body = c["body"] if given is NOTHING else "input"
    sends = {FORM_BODY: c["form"], MULTIPART: c["parts"], FILE_BODY: c["body_file"], "input": given}.get(body,
                                                                                                       c["content"])
    return _Call(c["method"], _with_query(quote(url, safe=_URL_SAFE), query), headers, credentials,
                 (auth, own["username"], password) if auth in (BASIC, DIGEST) else None, body, sends, own["ca_file"],
                 not own["dont_verify"])


def _read(path: str, label: str) -> bytes:
    try:
        return Path(path).expanduser().read_bytes()
    except OSError as e:
        raise Failed(f"{label}: cannot read {path}: {e.strerror or e}") from None


def _payload(call: _Call) -> tuple[bytes | None, str | None]:
    """What the request sends, and its Content-Type (None: none)."""
    kind, sends = call.body, call.sends
    if kind == "input":
        if isinstance(sends, (dict, list)):
            try:
                return json.dumps(sends, ensure_ascii=False).encode("utf-8"), CONTENT_TYPES[JSON_BODY]
            except (TypeError, ValueError) as e:
                raise Failed(f"the body input: a dict or list is sent as JSON, and this one cannot be: {e}") from None
        if isinstance(sends, Table):
            return sends.scan().collect().write_json().encode("utf-8"), CONTENT_TYPES[JSON_BODY]
        if isinstance(sends, str):
            return sends.encode("utf-8"), CONTENT_TYPES[TEXT_BODY]
        got = "None" if sends is None else f"a {type(sends).__name__}"
        raise Failed(f"the body input got {got}: it sends a dict or list (as JSON), a table (as JSON records) or text")
    if kind == NO_BODY:
        return None, None
    if kind in (JSON_BODY, TEXT_BODY, XML_BODY):
        if kind == JSON_BODY:
            try:
                json.loads(sends)
            except ValueError as e:
                raise Failed(f"Content: Body is JSON, but this is not JSON ({e})") from None
        return sends.encode("utf-8"), CONTENT_TYPES[kind]
    if kind == FORM_BODY:
        return urlencode([(r["key"], r["value"]) for r in ticked(sends)]).encode("ascii"), CONTENT_TYPES[kind]
    if kind == FILE_BODY:
        return _read(sends, "File to send"), CONTENT_TYPES[kind]
    boundary, data = uuid.uuid4().hex, b""  # multipart form-data
    for r in ticked(sends):
        head = f'Content-Disposition: form-data; name="{r["key"].replace(chr(34), "%22")}"'
        if r.get("file"):
            name = Path(r["value"]).name.replace('"', "%22")
            head += f'; filename="{name}"\r\nContent-Type: application/octet-stream'
            content = _read(r["value"], f"Parts: {r['key']}")
        else:
            content = r["value"].encode("utf-8")
        data += f"--{boundary}\r\n{head}\r\n\r\n".encode("utf-8") + content + b"\r\n"
    return data + f"--{boundary}--\r\n".encode("ascii"), f"multipart/form-data; boundary={boundary}"


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kw):
        return None  # (urllib then gives the redirect as the answer)


def _place(url: str) -> tuple:
    """Where a URL leads: its scheme, host and port."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, parts.hostname, parts.port or (443 if scheme == "https" else 80)


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect, but to another place (scheme, host or port) without the headers that carry credentials
    (_Call.credentials), as curl and Postman do (urllib's own sends every header on, wherever the redirect leads)."""

    def __init__(self, credentials: set):
        self.credentials = credentials

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and _place(new.full_url) != _place(req.full_url):
            new.headers = {k: v for k, v in new.headers.items() if k.lower() not in self.credentials}
        return new


def _tls(call: _Call) -> ssl.SSLContext:
    context = ssl.create_default_context()  # (the system's certificate store)
    if call.ca_file:
        try:
            context.load_verify_locations(cafile=str(Path(call.ca_file).expanduser()))
        except OSError as e:  # (ssl.SSLError too: not a certificate)
            raise Failed(f"CA certificate file: cannot use {call.ca_file}: {getattr(e, 'strerror', None) or e}") from None
    if not call.verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _send(call: _Call, data: bytes | None, content_type: str | None, follow: bool, cookies,
          timeout: float | None = None) -> tuple:
    """Send the request (through the proxy HTTPS_PROXY etc. or the system's settings name): (its status, reason,
    headers (an email.message.Message), body (bytes) and the URL it came from (after redirects)). timeout: seconds to
    wait for each answer (None: as long as it takes; a block's own Timeout stops it)."""
    headers = dict(call.headers.values())
    if content_type and "content-type" not in call.headers:
        headers["Content-Type"] = content_type
    handlers = [urllib.request.HTTPSHandler(context=_tls(call)), _Redirects(call.credentials) if follow
                else _NoRedirects()]
    if cookies is not None:  # (inside a Session: its cookies)
        handlers.append(urllib.request.HTTPCookieProcessor(cookies))
    if call.auth and call.auth[0] == BASIC:  # (with the request itself, as Postman sends it)
        user_password = f"{call.auth[1]}:{call.auth[2]}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(user_password).decode("ascii")
    elif call.auth:  # Digest: in answer to the server's challenge
        passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        passwords.add_password(None, call.url, call.auth[1], call.auth[2])
        handlers.append(urllib.request.HTTPDigestAuthHandler(passwords))
    request = urllib.request.Request(call.url, data=data, headers=headers, method=call.method)
    _, host, port = _place(call.url)
    where = f"{host}:{port}"
    try:
        try:
            response = urllib.request.build_opener(*handlers).open(request, timeout=timeout)
        except urllib.error.HTTPError as e:
            response = e  # an answer all the same: e.g. a 404, or a redirect not followed
        with response:
            return response.status, response.reason, response.headers, response.read(), response.geturl()
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            raise Failed(f"{where}: its certificate is not trusted ({e.reason.verify_message}): set CA certificate "
                         "file to the certificate of the authority that signed it, or tick Don't verify "
                         "certificates") from None
        raise Failed(f"could not reach {where}: {getattr(e.reason, 'strerror', None) or e.reason}") from None
    except (http.client.HTTPException, OSError) as e:
        raise Failed(f"{where} did not answer as a web server does ({type(e).__name__}: {e})") from None


def _headers(message) -> dict:
    """An answer's headers as a dict (a header sent more than once: its values joined with , as HTTP allows). Set-Cookie
    is a HiddenStr: the cookies it sets (a login's session) never show in the Log, a summary or the run history."""
    found = {}
    for name, value in message.items():
        found[name] = f"{found[name]}, {value}" if name in found else value
    return {name: HiddenStr(value) if name.lower() == "set-cookie" else value for name, value in found.items()}


def _text(raw: bytes, message) -> str:
    charset = message.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset)
    except (LookupError, UnicodeDecodeError) as e:
        raise Failed(f"the answer is not text in {charset} ({e}): choose Response as file to save it as it is") from None


def _json(raw: bytes, message, what: str):
    try:
        return json.loads(_text(raw, message))
    except ValueError as e:
        raise Failed(f"{what} is not JSON ({e})") from None


def _response(c: dict, raw: bytes, message):
    """The response output, as Response as says."""
    how = c["response_as"]
    if how == AS_FILE:
        path = Path(c["save_to"]).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        except OSError as e:
            raise Failed(f"Save to: cannot write {c['save_to']}: {e.strerror or e}") from None
        return c["save_to"]
    if how == AS_TABLE:
        records = _json(raw, message, "Response as table: the answer")
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            got = ("a list with other things than records (objects) in it" if isinstance(records, list) else
                   f"a JSON {'object' if isinstance(records, dict) else type(records).__name__}")
            raise Failed(f"Response as table: the answer is {got}, not a list of records (an object per row)")
        try:
            return pl.from_dicts(records, infer_schema_length=None) if records else pl.DataFrame()
        except Exception as e:  # (e.g. a column with numbers and texts)
            raise Failed(f"Response as table: its records do not make a table: {e}") from None
    content_type = message.get_content_type()
    if how == AUTOMATIC and raw and (content_type == "application/json" or content_type.endswith("+json")):
        return _json(raw, message, f"the answer, which says it is JSON ({content_type}),")
    return _text(raw, message)  # (an answer with no body, e.g. to HEAD: the empty text)


def curl_command(c: dict, conn: dict | None, body_from_input: bool) -> str:
    """The request of the API Request whose settings (their placeholders filled in) are `c`, with its API connection
    `conn`, as a curl command read as bash reads it, an option a line, each stored secret as hidden() shows it: for
    Copy as cURL. With its body input connected, it has no body (only a run has it)."""
    call = _call(c, conn, hidden)
    body, content_type = [], None if body_from_input else CONTENT_TYPES.get(call.body)
    if body_from_input or call.body == NO_BODY:
        pass
    elif call.body == FORM_BODY:
        body = [["--data-raw", urlencode([(r["key"], r["value"]) for r in ticked(call.sends)])]]
    elif call.body == FILE_BODY:
        body = [["--data-binary", f"@{call.sends}"]]
    elif call.body == MULTIPART:
        body = [["-F", f"{r['key']}=@{r['value']};type=application/octet-stream"] if r.get("file") else
                ["--form-string", f"{r['key']}={r['value']}"] for r in ticked(call.sends)]
    else:
        body = [["--data-raw", call.sends]]
    method = (["--head"] if call.method == "HEAD" else [] if call.method == "GET" and not body else
              ["-X", call.method])
    parts = [["curl", *method, call.url]]
    if c["follow_redirects"]:
        parts.append(["--location"])
    if call.auth:
        parts.append(["--digest"] * (call.auth[0] == DIGEST) + ["--user", f"{call.auth[1]}:{call.auth[2]}"])
    parts += [["-H", f"{name}: {value}"] for name, value in call.headers.values()]
    if content_type and "content-type" not in call.headers:
        parts.append(["-H", f"Content-Type: {content_type}"])
    parts += body
    if call.ca_file:
        parts.append(["--cacert", call.ca_file])
    if not call.verify:
        parts.append(["--insecure"])
    return " \\\n  ".join(" ".join(shlex.quote(a) for a in part) for part in parts)


# --- Import from cURL: a curl command, as documentation or a browser's Copy as cURL writes it, read into the settings --

# The settings that make up the request: Fill in replaces them all (those the command does not give are left empty).
# Follow redirects, Don't verify certificates and the timeout change only when the command sets them (-L, -k, -m).
REQUEST = ("connection", "method", "url", "params", "auth", "username", "key_name", "secret", "headers", "body",
           "content", "form", "parts", "body_file")
# The options Fill in reads, by their long names: those that take a value, and those that do not.
_CURL_VALUED = {"-X": "--request", "--request": "--request", "--url": "--url", "-H": "--header", "--header": "--header",
                "-d": "--data", "--data": "--data", "--data-ascii": "--data", "--data-binary": "--data-binary",
                "--data-raw": "--data-raw", "--data-urlencode": "--data-urlencode", "--json": "--json",
                "-F": "--form", "--form": "--form", "-u": "--user", "--user": "--user", "-A": "--user-agent",
                "--user-agent": "--user-agent", "-e": "--referer", "--referer": "--referer", "-b": "--cookie",
                "--cookie": "--cookie", "-m": "--max-time", "--max-time": "--max-time"}
_CURL_FLAGS = {"-G": "--get", "--get": "--get", "-I": "--head", "--head": "--head", "-k": "--insecure",
               "--insecure": "--insecure", "-L": "--location", "--location": "--location",
               "--compressed": "--compressed"}  # (--compressed: the block reads any answer as it comes)
# Options it does not read that take a value: listed with their value as not used (the value is not a URL).
_CURL_SKIPPED = {"-o", "--output", "-w", "--write-out", "-x", "--proxy", "-U", "--proxy-user", "-c", "--cookie-jar",
                 "-D", "--dump-header", "-E", "--cert", "--key", "--cacert", "--capath", "-r", "--range", "-T",
                 "--upload-file", "-K", "--config", "-z", "--time-cond", "-C", "--continue-at", "--connect-timeout",
                 "--retry", "--retry-delay", "--retry-max-time", "--max-redirs", "--resolve", "--interface",
                 "--limit-rate", "--oauth2-bearer", "--form-string", "--noproxy"}
_SECRET_HEADER = re.compile(r"token|key|secret|password|^(proxy-)?authorization$", re.IGNORECASE)
_BASH = re.compile(r"""(?P<space>\s+)|(?P<joined>\\(?:\n|$))|\\(?P<escaped>.)|'(?P<single>[^']*)'"""
                   r"""|\$'(?P<ansi>(?:\\.|[^\\'])*)'|"(?P<double>(?:\\.|[^\\"])*)"|(?P<plain>[^\s\\'"$]+|\$)""", re.S)
_EXAMPLE = "e.g. curl https://api.example.com/v1/orders -H 'Accept: application/json'"


@dataclass
class CurlImport:
    """What Fill in takes from a curl command (from_curl)."""
    config: dict  # each of REQUEST (None: left empty), and follow_redirects / dont_verify when the command sets them
    timeout: str | None  # -m, e.g. 10s
    secrets: list  # (what it is, its value, a name to suggest, its row of config["headers"], or None: Auth's secret)
    filled: list[str]  # what it fills, a line each
    unused: list[str]  # what it could not use, as written


def _bash_words(text: str) -> list[str]:
    """The words of a command as bash reads them: \\ at a line's end goes on on the next line; 'single', "double" (\\
    takes ", \\, $ and ` as they are) and $'ANSI-C' quotes (as browsers write \\n and \\'); nothing is expanded."""
    words, word, at = [], None, 0
    while at < len(text):
        m = _BASH.match(text, at)
        if m is None:
            raise ValueError(f"The curl command cannot be read: its quote {text[at]} at {text[at:at + 30]!r} is not "
                             "closed")
        at, kind = m.end(), m.lastgroup
        if kind == "space":
            if word is not None:
                words.append(word)
            word = None
        elif kind != "joined":
            piece = m[kind]
            if kind == "ansi":
                try:
                    piece = codecs.decode(piece.encode("latin-1", "backslashreplace"), "unicode_escape")
                except UnicodeDecodeError as e:
                    raise ValueError(f"The curl command cannot be read: $'{piece}': {e.reason}") from None
            elif kind == "double":
                piece = re.sub(r'\\([\\"$`\n])', lambda e: "" if e[1] == "\n" else e[1], piece)
            word = (word or "") + piece
    return words + ([word] if word is not None else [])


def _cmd_words(text: str) -> list[str]:
    """The words of a command as Windows' cmd reads it, then the program's C runtime: outside "quotes" ^ takes the next
    character as it is (^ at a line's end: it goes on on the next line); then the words are split at spaces outside
    "quotes", in which \\" is a " (and \\\\" a \\ before a quote)."""
    line, quoted, at = [], False, 0
    while at < len(text):
        char = text[at]
        if char == "^" and not quoted:
            line.append(text[at + 1:at + 2].replace("\n", ""))
            at += 2
            continue
        quoted ^= char == '"'
        line.append(char)
        at += 1
    words, word, quoted = [], None, False
    for m in re.finditer(r'(\\*)"|\\+|\s+|[^\\"\s]+', "".join(line)):
        if m[1] is not None:  # backslashes before a quote: one for each two; one left over makes the quote a "
            word = (word or "") + "\\" * (len(m[1]) // 2) + '"' * (len(m[1]) % 2)
            quoted ^= len(m[1]) % 2 == 0
        elif m[0].isspace() and not quoted:
            if word is not None:
                words.append(word)
            word = None
        else:
            word = (word or "") + m[0]
    if quoted:
        raise ValueError('The curl command cannot be read: one of its " quotes is not closed')
    return words + ([word] if word is not None else [])


def _curl_options(words):
    """(option, its value) for each option of a curl command's words (the value None: it takes none, or nothing
    follows it), and (None, word) for each other word. Short options may be several in a word (-sSL), and the value of
    one may follow it in the word (-XPOST)."""
    words = iter(words)
    for word in words:
        if not word.startswith("-") or word == "-":
            yield None, word
            continue
        options, rest = ([(word, None)], "") if word.startswith("--") else ([], word[1:])
        while rest:
            option, rest = "-" + rest[0], rest[1:]
            takes = option in _CURL_VALUED or option in _CURL_SKIPPED
            options.append((option, rest if takes and rest else None))
            rest = "" if takes else rest
        for option, value in options:
            if value is None and (option in _CURL_VALUED or option in _CURL_SKIPPED):
                value = next(words, None)
            yield option, value


def from_curl(text: str) -> CurlImport:
    """The API Request's settings for the request a curl command sends, the command written as bash or Windows' cmd
    reads it (as an API's documentation or a browser's Copy as cURL writes it), with every brace written {{ }} so that
    it is sent as it is. ValueError, saying why, for text that is not a curl command with a URL."""
    text = re.sub(r"[ \t]+\n", "\n", text.replace("\r\n", "\n")).strip()  # (a \ or ^ before spaces copied with it)
    start = re.match(r"(?:\$\s+)?curl(?:\.exe)?(?:\s|$)", text, re.IGNORECASE)  # (a $ prompt, as documentation shows)
    if start is None:
        first = text.split(None, 1)[0] if text else ""
        raise ValueError(f"This is not a curl command: it starts with {first!r}, not curl. Paste a curl command, "
                         f"{_EXAMPLE}" if first else f"Paste a curl command first, {_EXAMPLE}")
    cmd = re.search(r"\^\n", text) or '^"' in text  # (cmd's line ends and quotes)
    words = (_cmd_words if cmd else _bash_words)(text[start.end():])

    method = url = user = timeout = None
    headers, cookies, data, form, unused, flags = [], [], [], [], [], set()
    for option, value in _curl_options(words):
        name = _CURL_VALUED.get(option) or _CURL_FLAGS.get(option)
        if option is None or name == "--url":
            if url is None:
                url = value
            elif value is not None:
                unused.append(f"{value} (a second URL: the block calls one)")
        elif name is None or value is None and option in _CURL_VALUED:
            unused.append(option if value is None else f"{option} {value}")
        elif option in _CURL_FLAGS:
            flags.add(name)
        elif name == "--request":
            method = value.upper()
        elif name == "--header":
            key, colon, header = value.partition(":")
            if colon and key.strip():
                headers.append((key.strip(), header.strip()))
            else:
                unused.append(f"{option} {value}")
        elif name in ("--user-agent", "--referer"):
            headers.append(({"--user-agent": "User-Agent", "--referer": "Referer"}[name], value))
        elif name == "--cookie" and "=" in value:
            cookies.append(value)
        elif name == "--cookie":
            unused.append(f"{option} {value} (a file of cookies)")
        elif name == "--user":
            user = value
        elif name == "--max-time":
            try:
                parse_duration(f"{value}s")
                timeout = f"{value}s"
            except ValueError:
                unused.append(f"{option} {value} (not a number of seconds)")
        elif name == "--form":
            form.append(value)
        else:  # the data options
            data.append((name, value))
    if url is None:
        raise ValueError(f"The curl command has no URL: write the address it calls, {_EXAMPLE}")
    if cookies:
        headers.append(("Cookie", "; ".join(cookies)))
    kinds = {kind for kind, _ in data}
    if "--json" in kinds:  # (as curl sends it: with the JSON headers, unless given)
        headers += [(k, "application/json") for k in ("Content-Type", "Accept")
                    if k.lower() not in {key.lower() for key, _ in headers}]

    def pairs(kind: str, value: str) -> list[dict]:
        """The rows of a form or query that a data option gives ([] and listed as not used: none)."""
        if kind == "--data-urlencode":
            key, eq, content = value.partition("=")
            if eq and key:
                return [{"key": key, "value": content}]
        elif not (value.startswith("@") and kind != "--data-raw"):
            return [{"key": k, "value": v} for k, v in parse_qsl(value, keep_blank_values=True)]
        unused.append(f"{kind} {value}")
        return []

    url, params = url_and_params(url, None)
    config = {**dict.fromkeys(REQUEST), "url": url, "params": params, "form": [], "parts": []}
    body = NO_BODY
    if form:
        body = MULTIPART
        for part in form:
            key, eq, value = part.partition("=")
            if not eq or not key or value.startswith("<"):
                unused.append(f"-F {part}")
            elif value.startswith("@"):  # a file: its path, then ;type=… and the like, which the block does not set
                path, semicolon, rest = value[1:].partition(";")
                config["parts"].append({"key": key, "value": path, "file": True})
                if semicolon:
                    unused.append(f"-F {part}: its ;{rest} (a file is sent as application/octet-stream)")
            else:
                config["parts"].append({"key": key, "value": value})
        unused += [f"{kind} {value} (curl sends no data with -F)" for kind, value in data]
    elif "--get" in flags:
        config["params"] += [row for kind, value in data for row in pairs(kind, value)]
    elif "--data-urlencode" in kinds:
        body, config["form"] = FORM_BODY, [row for kind, value in data for row in pairs(kind, value)]
    elif data:
        files = [(k, v) for k, v in data if v.startswith("@") and k != "--data-raw"]
        if files and len(data) == 1:
            body, config["body_file"] = FILE_BODY, data[0][1][1:]
        else:
            unused += [f"{kind} {value} (a file among other data)" for kind, value in files]
            sent = ("" if kinds == {"--json"} else "&").join(v for k, v in data if (k, v) not in files)
            try:
                parsed = json.loads(sent) if sent.lstrip().startswith(("{", "[")) else None
            except ValueError:
                parsed = None
            body, config["content"] = ((JSON_BODY, json.dumps(parsed, indent=2, ensure_ascii=False))
                                       if parsed is not None else (TEXT_BODY, sent))
        if body != JSON_BODY and "content-type" not in {key.lower() for key, _ in headers}:
            headers.append(("Content-Type", "application/x-www-form-urlencoded"))  # (what curl sends with -d)
    if method is not None and method not in METHODS:
        unused.append(f"-X {method} (the block sends {', '.join(METHODS)})")
        method = None
    method = method or ("HEAD" if "--head" in flags else "GET" if body == NO_BODY else "POST")

    secrets, said = [], []
    host = re.sub(r"\W+", "_", urlsplit(url).hostname or "").strip("_") or "api"
    bearer = [h for h in headers if h[0].lower() == "authorization" and re.match(r"bearer\s+\S", h[1], re.IGNORECASE)]
    if bearer:
        headers = [h for h in headers if h not in bearer]
        config["auth"] = BEARER
        secrets.append(("the Bearer token", bearer[-1][1].split(None, 1)[1], f"{host}_token", None))
        said.append("Auth: Bearer token")
        if user is not None:
            unused.append("-u (its Authorization header is replaced by the Bearer one, as curl does)")
    elif user is not None:
        name, colon, password = user.partition(":")
        config["auth"], config["username"] = BASIC, name
        said.append(f"Auth: Basic, user name {name}" + ("" if colon else " (the command has no password: name the "
                                                                          "stored secret that holds it in Stored "
                                                                          "secret)"))
        if colon:
            secrets.append((f"the password of -u {name}", password, f"{host}_password", None))
    config["headers"] = []
    for key, value in headers:
        if _SECRET_HEADER.search(key) and value:
            name = re.sub(r"\W+", "_", key).strip("_").lower()
            secrets.append((f"the value of the header {key}", value, f"{host}_{name}", len(config["headers"])))
            value = ""  # (secret:NAME, once named)
        config["headers"].append({"key": key, "value": value})

    filled = [f"Method: {method}", f"URL: {url}"]
    filled += [f"{label}: {', '.join(r['key'] for r in config[key])}" for key, label in (
        ("params", "Params (the URL's query)"), ("headers", "Headers")) if config[key]]
    filled += said
    if body != NO_BODY:
        rows = config["form"] or config["parts"]
        filled.append(f"Body: {body}" + (f": {', '.join(r['key'] for r in rows)}" if rows else
                                         f": {config['body_file']}" if body == FILE_BODY else ""))
    filled += [line for flag, line in (("--location", "Follow redirects: ticked"),
                                       ("--insecure", "Don't verify certificates: ticked")) if flag in flags]
    if timeout:
        filled.append(f"Timeout: {timeout}")
    if "--location" in flags:
        config["follow_redirects"] = None  # (its default: ticked)
    if "--insecure" in flags:
        config["dont_verify"] = True
    config.update(method=None if method == "GET" else method, body=None if body == NO_BODY else body)
    for key in ("url", "username", "content", "body_file"):
        config[key] = None if config[key] is None else _literal(config[key])
    for key in ("params", "headers", "form", "parts"):
        config[key] = [{**r, "key": _literal(r["key"]), "value": _literal(r["value"])} for r in config[key] or []] or None
    return CurlImport(config, timeout, secrets, filled, unused)


def _literal(text: str) -> str:
    """Text as a setting that takes placeholders sends it as it is: its braces written {{ and }}."""
    return text.replace("{", "{{").replace("}", "}}")


def connection_note(home, name: str, headers) -> str:
    """What Properties shows under an API Request's Connection: what the API connection `name` gives it (its secret by
    name, never its value), and which of its headers the block's own (`headers`) replace."""
    try:
        conn = api_connection(home, name)
    except (Failed, ValueError) as e:  # (ValueError: connections.yaml cannot be read)
        return str(e)
    mine = {r["key"].lower(): r for r in ticked(headers) if "key" in r}
    said = [f"Base URL: {conn['base_url'] or '(none)'}",
            f"Auth: {conn['auth']}" + "".join(f", {words} {conn[k]}" for k, words in (
                ("username", "user name"), ("key_name", "key name"), ("secret", "with the stored secret")) if conn[k])]
    for r in ticked(conn["headers"]):
        replaced = mine.get(r["key"].lower())
        said.append(f"Header {r['key']}: {r['value']}"
                    + (f" (replaced by this block's {replaced['key']}: {replaced.get('value', '')})" if replaced else ""))
    if conn["ca_file"]:
        said.append(f"CA certificate file: {conn['ca_file']}")
    if conn["dont_verify"]:
        said.append("Certificates are not verified")
    return f"From the connection {name} (change it in Tools > Connections):\n" + "\n".join(said)


def try_connection(home, name: str) -> str:
    """Tools > Connections > Save and test connection, for an API connection: a GET of its base URL, with its auth,
    headers and certificate settings."""
    c = {**{k: f.default for k, f in ApiRequest.config.items()}, "url": "", "connection": name}
    call = _call(c, api_connection(home, name), home.get_secret)
    status, reason, *_ = _send(call, None, None, True, None, timeout=30)  # (as an SMTP connection's test)
    return f"{call.url} answered {status} {reason}"


class ApiRequest(Block):
    type_id = API_REQUEST
    title = "API Request"
    description = ("Calls a web API, as Postman's Send does: a method (GET, POST, …), a URL, query params, headers, "
                   "auth with stored secrets and a body. Gives the response (JSON read when the server says it is "
                   "JSON, otherwise text; or a table or a file, as Response as says), its status and its headers. A "
                   "status that does not count as success fails it, its error output carrying the status, headers and "
                   "body. A Connection can give the base URL, auth, headers and certificate settings. Right-click it > "
                   "Copy as cURL copies the request as a curl command, its secrets hidden; Import from cURL, at the top "
                   "of its Properties, fills the request from a curl command pasted there.")
    inputs_help = ("body (optional): what it sends instead of its Body setting: a dict or list is sent as JSON, a table "
                   "as JSON records (an object per row), text as it is.")
    category = "Servers"
    inputs = {"body": ports.Any(required=False)}
    outputs = {"response": ports.Any(), "status": ports.Any(), "headers": ports.Any()}
    has_done = True
    config = {
        "connection": fields.Connection(
            kind="api", required=False,
            help="none (empty): everything is set in this block. Or an API connection (Tools > Connections): its base "
                 "URL (the URL here may then be relative to it, e.g. /orders/{id}), its auth with its stored secret, "
                 "its headers (this block's own Headers are added, this block's winning on the same name) and its "
                 "certificate settings."),
        "method": fields.Choice(METHODS, default="GET", help="The HTTP method of the request"),
        "url": fields.Text(help="The web address to call, with its ?query if it has one (kept in step with Params). "
                                "With a Connection: whole, or relative to its base URL.",
                           examples=["https://api.example.com/v1/orders", "/orders/{id}",
                                     "https://api.example.com/v1/orders?status=open"]),
        "params": fields.KeyValues(
            required=False,
            help="Query parameters, added to the URL's query in their order (encoded as a URL needs): only the ticked "
                 "rows are sent. Type name=value in the line under the table to add one; empty a name to remove its "
                 "row. Kept in step with the URL's ?query.",
            examples=["page=2", "region={region}", "limit=100"]),
        "auth": fields.Choice(
            AUTHS, default=NO_AUTH, only_if=_NO_CONNECTION,
            help="How the request proves who sends it: Basic (a user name and password, sent with the request), a "
                 "Bearer token (an Authorization header), an API key in a header or in the query, or Digest (in "
                 "answer to the server's challenge). The password, token or key is a stored secret."),
        "username": fields.Text(label="User name", only_if={"auth": [BASIC, DIGEST], **_NO_CONNECTION},
                                help="The user name to log in with", examples=["report", "svc_reports"]),
        "key_name": fields.Text(label="Key name", only_if={"auth": [KEY_HEADER, KEY_QUERY], **_NO_CONNECTION},
                                help="The name of the header (e.g. X-Api-Key) or query parameter (e.g. api_key) that "
                                     "carries the API key", examples=["X-Api-Key", "api_key"]),
        "secret": fields.Text(label="Stored secret (name)", only_if={"auth": list(AUTHS[1:]), **_NO_CONNECTION},
                              help="The name of the stored secret that holds the password, token or key (store it "
                                   "with: taskloom secret set NAME). Its value is never written in the flow, the Log "
                                   "or the run history.",
                              examples=["orders_token", "report_password"]),
        "headers": fields.KeyValues(
            required=False, sep=": ",
            help="Request headers: only the ticked rows are sent. Type Name: value in the line under the table to add "
                 "one; empty a name to remove its row. A value secret:NAME sends the stored secret NAME (taskloom "
                 "secret set NAME), never written in the flow. A Content-Type here replaces the one Body sets; Auth's "
                 "header replaces one of the same name here.",
            examples=["Accept: application/json", "X-Region: {region}", "X-Api-Key: secret:orders_key"]),
        "body": fields.Choice(
            BODIES, default=NO_BODY,
            help="What the request sends: nothing, JSON, text or XML (written in Content), a form url-encoded or "
                 "multipart form-data (with files), or a file as it is. Each sets its Content-Type. With the body "
                 "input connected, the body comes from it instead (set none here)."),
        "content": fields.Code(
            "text", only_if={"body": [JSON_BODY, TEXT_BODY, XML_BODY]},
            help="The body to send, as it is (JSON is checked before it is sent). {name} fills in a placeholder: "
                 "write {{ and }} for the braces of JSON.",
            examples=['{{"region": "{region}", "top": 5}}', '["north", "south"]', '<order id="{id}"/>']),
        "form": fields.KeyValues(required=False, label="Form", only_if={"body": [FORM_BODY]},
                                 help="The form's fields, url-encoded: only the ticked rows are sent. Type name=value "
                                      "in the line under the table to add one.",
                                 examples=["region=north", "day={day:%Y-%m-%d}"]),
        "parts": fields.KeyValues(required=False, files=True, label="Parts", only_if={"body": [MULTIPART]},
                                  help="The multipart form's fields: only the ticked rows are sent; a row ticked File "
                                       "sends the file at the path its value gives. Type name=value in the line under "
                                       "the table to add one.",
                                  examples=["note=nightly upload", "file=out/report.csv"]),
        "body_file": fields.Path(label="File to send", only_if={"body": [FILE_BODY]},
                                 help="The file sent as the body, as it is (application/octet-stream unless Headers "
                                      "has a Content-Type)", examples=["out/orders.json", "C:/exports/report.xml"]),
        "response_as": fields.Choice(
            (AUTOMATIC, AS_TEXT, AS_TABLE, AS_FILE), default=AUTOMATIC,
            help="What the response output is: automatic (JSON read into dicts and lists when the server says it is "
                 "JSON, text otherwise), text, a table (the answer must be a JSON list of records, an object per row) "
                 "or a file (saved to Save to; the output is its path)."),
        "save_to": fields.Path(label="Save to", only_if={"response_as": [AS_FILE]}, writes=True,
                               help="The file the answer is saved to, as it came (its folder is made if missing)",
                               examples=["downloads/orders.json", "out/report_{day:%Y-%m-%d}.pdf"]),
        "success": _Statuses(default="200-299", label="Statuses that count as success",
                             help="The answer's statuses that count as success, separated by commas: any other fails "
                                  "the block, its error output carrying the status, headers and body.",
                             examples=["200-299", "200-299, 404", "200, 201, 204"]),
        "follow_redirects": fields.Bool(default=True, help="Follow a redirect (3xx) to where it leads (to another host, "
                                                           "scheme or port without its Authorization header and the "
                                                           "headers that carry a stored secret, as curl and Postman "
                                                           "do); unticked, the redirect itself is the answer"),
        "ca_file": fields.Path(required=False, label="CA certificate file", only_if=_NO_CONNECTION,
                               files=["Certificates (*.pem *.crt *.cer)"],
                               help="A certificate authority to trust besides the system's, e.g. your company's (a PEM "
                                    "file): for a server whose certificate it signed",
                               examples=["C:/certs/company-ca.pem", "~/certs/ca.crt"]),
        "dont_verify": fields.Bool(default=False, label="Don't verify certificates", only_if=_NO_CONNECTION,
                                   help="Ticked: the server's certificate is not checked (the Log warns about it on "
                                        "every run). Only for a test server: anyone in between could read or change "
                                        "what is sent."),
    }

    @classmethod
    def problems(cls, config: dict, sources: dict, params: set) -> list[str]:
        if sources.get("body") and config["body"] != NO_BODY:
            return [f"Body: the body input is connected, so the body comes from it: set Body to none (not "
                    f"{config['body']}), or disconnect the input"]
        return []

    def run(self, ctx, **inputs):
        c = vars(self.config)
        conn = api_connection(ctx.home, c["connection"])
        try:
            call = _call(c, conn, ctx.home.get_secret, inputs.get("body", NOTHING))
        except KeyError as e:  # a stored secret that is not set: nothing is sent
            raise Failed(e.args[0]) from None
        shown = _call(c, conn, hidden).url
        ctx.log(f"{call.method} {shown}")
        if not call.verify:
            ctx.warn("the server's certificate is not verified (Don't verify certificates is ticked): anyone between "
                     "this computer and the server could read or change what is sent and what comes back")
        data, content_type = _payload(call)
        started = time.monotonic()
        status, reason, message, raw, came_from = _send(call, data, content_type, c["follow_redirects"], ctx.cookies)
        took = time.monotonic() - started
        ctx.log(f"{status} {reason}: {len(raw)} bytes in " + (f"{took * 1000:.0f} ms" if took < 1 else f"{took:.1f} s")
                + (f", from {came_from} (redirected)" if came_from != call.url else ""))
        headers = _headers(message)
        if not any(first <= status <= last for first, last in success_statuses(c["success"])):
            raise Failed(f"{call.method} {shown} answered {status} {reason}, which does not count as success "
                         f"({c['success']})", status=status, headers=headers,
                         body=raw.decode(message.get_content_charset() or "utf-8", "replace"))
        return {"response": _response(c, raw, message), "status": status, "headers": headers}


class Session(Block):
    """A box on the canvas, not a block that runs: the blocks inside it are in the flow where it is (flow_from_data),
    and the engine gives the API Request blocks among them its cookie jar as ctx.cookies (see
    engine._Runner._session_cookies), which an API Request sends its requests through (urllib's HTTPCookieProcessor)."""

    type_id = SESSION
    title = "Session"
    description = ("A box on the canvas around API Request blocks, like a requests.Session: drag them into it. The API "
                   "Request blocks inside it share one set of cookies during a run: the cookies a response sets (a "
                   "login's, say) go with the requests after it, by the domain and path rules of cookies. API Request "
                   "blocks outside any Session keep no cookies from one block to the next. Other blocks may be inside "
                   "it too, and run as usual; nothing is connected to the Session itself. After each request the Log "
                   "lists the cookies the session holds, by name and domain, never their values. Every run starts it "
                   "with no cookies (inside a Loop, every item); lines running at the same time inside it share them. "
                   "Run only this block and Run from this block on, on a block inside it, start with the cookies it "
                   "held just before that block in the run they carry on from (kept with that run's results), so a "
                   "later call runs again without logging in again; if the server has ended the session since, that "
                   "call fails, saying what the server answered.")
    category = "Servers"
