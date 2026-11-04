"""The Session container (servers.api_session), like a requests.Session: a box on the canvas, drawn as the Loop's, around
API Request blocks (servers.api_request) that share one set of cookies during a run. Through the real `taskloom`
command (run, --only, --from), its run history (history.db), the offscreen editor and a web server on this computer
started by each test (never the internet). Each test names the point of the specification (1-6) it protects.

What the tests take as given (the specification leaves it to the change): the flow file records that a block is inside
a Session with the block key `inside: <the Session's name>`, as for the Loop; the blocks inside are connected to each
other and to the blocks outside as anywhere (nothing is connected to the Session itself); the API Request's URL setting
is found by its label "URL", its other settings keep their defaults (GET, success 200-299), and its `status` output
orders the block after it. What a request carried is read on the server's side."""

import http.cookies
import http.server
import os
import re
import secrets
import socket
import threading
from functools import cache
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolTip  # noqa: E402

from taskloom.block import setting_label  # noqa: E402
from taskloom.editor.canvas import FOOTER_H, HEADER_H, NODE_W, ROW_H  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_block_icons import GROUP_COLORS, has_icon, ink, pixels, rows  # noqa: E402
from tests.test_editor import add_from_palette, wait_done  # noqa: E402
from tests.test_inspect_mermaid import contrast  # noqa: E402
from tests.test_loop_container import (beside, box, box_texts, context_menu, drag, drawn, render,  # noqa: E402
                                       result_of, run, title)
from tests.test_parallel_lines import entry, plain  # noqa: E402
from tests.test_paths_editor_editing import clean, flow_file, saved, settle  # noqa: E402
from tests.test_theme import TEXT, choose, most_common  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SESSION, API = "servers.api_session", "servers.api_request"
INSIDE = "inside"  # the block key with which the flow file records the container a block is inside
SERVERS = GROUP_COLORS["Servers"]
MANUAL = {"type": "trigger.manual", "ui": {"x": -400, "y": 0}}


# --- the blocks, as the flow file writes them -----------------------------------------------------------------------

@cache
def block_class(type_id: str):
    cls = discover(None).get(type_id)
    assert cls is not None, f"there is no {type_id} block"
    return cls


def session(x=0, y=-60) -> dict:
    """A Session, as it is added (no settings)."""
    block_class(SESSION)
    return {"type": SESSION, "config": {}, "ui": {"x": x, "y": y}}


def url_key() -> str:
    """The key, in the flow file, of the API Request's setting Properties shows as URL."""
    keys = {setting_label(key, f): key for key, f in block_class(API).config.items()}
    assert "URL" in keys, f"the API Request has no setting URL (its settings: {sorted(keys)})"
    return keys["URL"]


def api(url: str, k: int = 0, within: str | None = "sess", y: int = 100) -> dict:
    """An API Request (GET `url`), the k-th from the left, inside the Session `within` (None: inside none)."""
    spec = {"type": API, "config": {url_key(): url}, "ui": {"x": 60 + k * (NODE_W + 80), "y": y}}
    return {**spec, INSIDE: within} if within else spec


def python(code: str, inputs=("input",), outputs=("output",), k: int = 0, within: str | None = "sess") -> dict:
    spec = {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code},
            "ui": {"x": 60 + k * (NODE_W + 80), "y": 100}}
    return {**spec, INSIDE: within} if within else spec


def chain(*blocks: str) -> list[str]:
    """Each block runs after the one before it (through its status: the request was answered)."""
    return [f"{a}.status -> {b}.after" for a, b in zip(blocks, blocks[1:])]


def token(what: str) -> str:
    """A cookie value no text of Taskloom's could hold by chance."""
    return f"{what}-{secrets.token_hex(8)}"


# --- the web server ----------------------------------------------------------------------------------------------------

class _Server(http.server.ThreadingHTTPServer):
    daemon_threads, block_on_close = True, False  # (closing it never waits for a request still being answered)


class _Server6(_Server):
    address_family = socket.AF_INET6


class Site:
    """A web server for the test, on 127.0.0.1 and (where the system has it) ::1, on one free port, so that both
    http://127.0.0.1:port and http://localhost:port reach it. It answers each request with answer(path, cookies) ->
    (status, [(header, value), ...], text), and notes each request in `seen` as (path, {cookie name: value}) in the
    order they came."""

    def __init__(self, answer):
        self.answer, self.seen, self.lock = answer, [], threading.Lock()
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                site._serve(self)

            do_POST = do_PUT = do_PATCH = do_DELETE = do_GET

            def log_message(self, *args):
                pass

        self.servers = self._bound(Handler)
        self.port = self.servers[0].server_address[1]
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in self.servers]
        for thread in self.threads:
            thread.start()

    @staticmethod
    def _bound(handler) -> list:
        for _ in range(5):
            v4 = _Server(("127.0.0.1", 0), handler)
            try:
                return [v4, _Server6(("::1", v4.server_address[1]), handler)]
            except OSError:  # (that port is taken on ::1, or the system has no IPv6)
                v4.server_close()
        return [_Server(("127.0.0.1", 0), handler)]

    def _serve(self, handler):
        if int(handler.headers.get("Content-Length") or 0):
            handler.rfile.read(int(handler.headers["Content-Length"]))
        cookies = {name: m.value for name, m in http.cookies.SimpleCookie(handler.headers.get("Cookie") or "").items()}
        with self.lock:
            self.seen.append((handler.path, cookies))
        status, headers, text = self.answer(handler.path, cookies)
        body = text.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(body)

    def url(self, path: str, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"

    def paths(self) -> list[str]:
        with self.lock:
            return [path for path, _ in self.seen]

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class LoginSite(Site):
    """/login gives a new session (cookie sessionid, for the whole site); /prefs sets theme; /later gives a new
    session and sets extra; any other path needs a session it gave and still knows (else 401)."""

    def __init__(self):
        self.issued, self.valid, self.theme = [], set(), token("theme")
        super().__init__(self._answer)

    def _new(self) -> str:
        sid = token(f"session{len(self.issued) + 1}")
        self.issued.append(sid)
        self.valid.add(sid)
        return sid

    def _answer(self, path, cookies):
        if path == "/login":
            return 200, [("Set-Cookie", f"sessionid={self._new()}; Path=/")], "logged in"
        if path == "/prefs":
            return 200, [("Set-Cookie", f"theme={self.theme}; Path=/")], "saved"
        if path == "/later":
            return 200, [("Set-Cookie", f"sessionid={self._new()}; Path=/"), ("Set-Cookie", "extra=1; Path=/")], "ok"
        if cookies.get("sessionid") in self.valid:
            return 200, [], "your data"
        return 401, [], "not logged in"


# --- the run history ---------------------------------------------------------------------------------------------------

def said(result) -> str:
    """What a `taskloom run` printed (its events, then its errors), the end of it: why a run went as it did."""
    return (result.stdout + result.stderr)[-3000:]


def logs(env, run_id: int) -> list[tuple[str, str]]:
    """The Log of a run as its history keeps it: (block, message) for each line, in order."""
    return env.query("SELECT COALESCE(block, ''), message FROM logs WHERE run_id=? ORDER BY rowid", run_id)


def lists_cookies(message: str, names, domain: str) -> bool:
    """The Log line tells of the session's cookies, naming each of `names` and the domain."""
    return "cookie" in message.lower() and domain in message and all(re.search(rf"\b{n}\b", message) for n in names)


def says_from_run(message: str, run_id: int) -> bool:
    """The Log line says the cookies came from run #run_id."""
    return "cookie" in message.lower() and re.search(rf"run #{run_id}\b", message) is not None


# --- 2: the API Request blocks inside share one cookie jar; those outside keep none -----------------------------------

def test_requests_inside_a_session_share_its_cookies_other_blocks_run_as_usual_and_those_outside_keep_none(env):
    """Spec 2 (the user's case): inside a Session, login's response sets a cookie and the call after it carries it, a
    Python Code block between them running as usual; two API Request blocks outside any Session (the same calls) keep
    no cookies between them, nor get the Session's."""
    issued = {}

    def answer(path, cookies):
        if path.endswith("/login"):
            issued[path] = token("session")
            return 200, [("Set-Cookie", f"sessionid={issued[path]}; Path=/")], "logged in"
        return 200, [], "data"

    with Site(answer) as site:
        flow = flow_file(env.root / "flow.yaml", {
            "manual": MANUAL, "sess": session(),
            "login": api(site.url("/login"), 0),
            "note": python("open('note.txt', 'w', encoding='utf-8').write(str(input))\noutput = input", k=1),
            "data": api(site.url("/data"), 2),
            "out_login": api(site.url("/out/login"), 0, within=None, y=500),
            "out_data": api(site.url("/out/data"), 1, within=None, y=500),
        }, ["manual.start -> login.after", "login.status -> note.input", "note.output -> data.after",
            "manual.start -> out_login.after", *chain("out_login", "out_data")])

        result, run_id = run(env, flow)

    assert result.returncode == 0, said(result)
    assert env.last_run() == (run_id, "success")
    assert sorted(site.paths()) == ["/data", "/login", "/out/data", "/out/login"], site.seen
    assert dict(site.seen) == {"/login": {}, "/data": {"sessionid": issued["/login"]},
                               "/out/login": {}, "/out/data": {}}, site.seen
    assert (env.root / "note.txt").read_text(encoding="utf-8") == "200"


def test_the_sessions_cookies_go_only_where_the_domain_and_path_rules_of_cookies_send_them(env):
    """Spec 2 (by the domain and path rules of cookies): a cookie set by 127.0.0.1 for the path /account goes with the
    calls to /account/... there, not with /public there, nor to another host (localhost, the same server); a cookie
    the response sets for another domain (example.com) is not taken."""
    sid = token("session")

    def answer(path, cookies):
        if path == "/account/login":
            return 200, [("Set-Cookie", f"sessionid={sid}; Path=/account"),
                         ("Set-Cookie", "other=1; Domain=example.com; Path=/")], "logged in"
        return 200, [], "ok"

    with Site(answer) as site:
        flow = flow_file(env.root / "flow.yaml", {
            "manual": MANUAL, "sess": session(),
            "login": api(site.url("/account/login"), 0),
            "orders": api(site.url("/account/orders"), 1),
            "public": api(site.url("/public"), 2),
            "elsewhere": api(site.url("/account/orders", host="localhost"), 3),
        }, ["manual.start -> login.after", *chain("login", "orders", "public", "elsewhere")])

        result, run_id = run(env, flow)

    assert result.returncode == 0, said(result)
    assert site.seen == [("/account/login", {}), ("/account/orders", {"sessionid": sid}), ("/public", {}),
                         ("/account/orders", {})], site.seen


def test_each_run_starts_its_session_with_no_cookies(env):
    """Spec 2 (one cookie jar for the run; not saved between runs, a non-goal): the flow run a second time logs in
    with no cookie (not the first run's), and its call after it carries the session that login got."""
    with LoginSite() as site:
        flow = flow_file(env.root / "flow.yaml", {
            "manual": MANUAL, "sess": session(),
            "login": api(site.url("/login"), 0), "data": api(site.url("/data"), 1),
        }, ["manual.start -> login.after", *chain("login", "data")])
        for _ in range(2):
            result, run_id = run(env, flow)
            assert result.returncode == 0, said(result)

    assert site.seen == [("/login", {}), ("/data", {"sessionid": site.issued[0]}),
                         ("/login", {}), ("/data", {"sessionid": site.issued[1]})], site.seen


# --- 3: the Log names the session's cookies and their domains, never their values ---------------------------------------

def test_after_each_request_the_log_lists_the_sessions_cookies_by_name_and_domain_and_never_their_values(env):
    """Spec 3: after login (whose response sets sessionid and theme) and after the call that follows it, the Log of
    each lists the cookies the session holds, by name and domain (127.0.0.1); no line of the Log holds a value, nor
    what the run printed, nor a block's summary, error or preview in the run history (login's headers output carries
    the Set-Cookie headers); the run history keeps those lines."""
    sid, theme = token("session"), token("theme")

    def answer(path, cookies):
        if path == "/login":
            return 200, [("Set-Cookie", f"sessionid={sid}; Path=/"), ("Set-Cookie", f"theme={theme}; Path=/")], "in"
        return 200, [], "data"

    with Site(answer) as site:
        flow = flow_file(env.root / "flow.yaml", {
            "manual": MANUAL, "sess": session(),
            "login": api(site.url("/login"), 0), "data": api(site.url("/data"), 1),
        }, ["manual.start -> login.after", *chain("login", "data")])

        result, run_id = run(env, flow)

    assert result.returncode == 0, said(result)
    assert site.seen[1] == ("/data", {"sessionid": sid, "theme": theme}), site.seen
    lines = logs(env, run_id)
    for block in ("login", "data"):
        assert [m for b, m in lines if b == block and lists_cookies(m, ("sessionid", "theme"), "127.0.0.1")], \
            (block, lines)
    assert [(b, m) for b, m in lines if sid in m or theme in m] == [], "a Log line holds a cookie's value"
    shown = [("printed", result.stdout + result.stderr)] + [
        (f"{block} {column}", text) for block, *texts in env.query(
            "SELECT block, COALESCE(error, ''), COALESCE(summary, ''), COALESCE(previews, '') FROM blocks "
            "WHERE run_id=?", run_id) for column, text in zip(("error", "summary", "previews"), texts)]
    assert [where for where, text in shown if sid in text or theme in text] == [], "a cookie's value is shown"


@pytest.mark.parametrize("own_python", [True, False], ids=["in Taskloom's Python", "in your environment"])
def test_a_block_after_the_headers_gets_the_cookie_and_runs_again_from_the_run_before_without_showing_it(env, own_python):
    """Spec 3 with spec 4 (a hidden cookie is no password: kept with the run, as the session's cookies are): login's
    headers output, whose Set-Cookie the Log and the results show as ***, gives a Python Code block after it the real
    value, in Taskloom's own Python or in your environment, and that block passes it on to one after it; Run only
    either block with the inputs of run #1 gets the same value, and no run shows it."""
    import hashlib
    import sys

    if not own_python:
        created = env.taskloom("env", "create", "--python", sys.executable, timeout=180)
        assert created.returncode == 0, created.stderr
    sid = token("session")
    sent = f"sessionid={sid}; Path=/"
    digest = hashlib.sha256(sent.encode()).hexdigest()

    def answer(path, cookies):
        return 200, [("Set-Cookie", sent)], "in"

    pass_on = python("import hashlib\nraw = input['Set-Cookie']\noutput = hashlib.sha256(raw.encode()).hexdigest()",
                     outputs=("output", "raw"), k=1, within=None)
    pass_on["config"]["use_environment"] = not own_python
    with Site(answer) as site:
        flow = flow_file(env.root / "flow.yaml", {
            "manual": MANUAL, "sess": session(), "login": api(site.url("/login"), 0), "pass_on": pass_on,
            "after": python("import hashlib\noutput = hashlib.sha256(input.encode()).hexdigest()", k=2, within=None),
        }, ["manual.start -> login.after", "login.headers -> pass_on.input", "pass_on.raw -> after.input"])
        first, first_id = run(env, flow)
        assert first.returncode == 0, said(first)
        runs = [(first_id, first, ("pass_on", "after"))]
        for block in ("pass_on", "after"):
            again, again_id = run(env, flow, "--only", block, "--inputs-from", first_id)
            assert again.returncode == 0, said(again)
            runs.append((again_id, again, (block,)))

    for run_id, result, ran in runs:
        for block in ran:
            assert digest in result_of(env, run_id, block)["summary"], (run_id, block)
        shown = [result.stdout + result.stderr, *(m for _, m in logs(env, run_id)),
                 *(t for row in env.query("SELECT COALESCE(error, ''), COALESCE(summary, ''), COALESCE(previews, '') "
                                          "FROM blocks WHERE run_id=?", run_id) for t in row)]
        assert [t for t in shown if sid in t] == [], f"run #{run_id} shows the cookie's value"


# --- 4: Run only / Run from on a block inside carry on with the cookies held just before it --------------------------------

def four_calls(env, site: LoginSite) -> Path:
    """manual -> login -> prefs -> data -> later, all inside the Session sess."""
    return flow_file(env.root / "flow.yaml", {
        "manual": MANUAL, "sess": session(),
        **{name: api(site.url(f"/{name}"), k) for k, name in enumerate(("login", "prefs", "data", "later"))},
    }, ["manual.start -> login.after", *chain("login", "prefs", "data", "later")])


@pytest.mark.parametrize("scope, again", [("--only", ["/data"]), ("--from", ["/data", "/later"])])
def test_run_only_or_from_a_call_inside_starts_with_the_cookies_held_just_before_it_in_the_run_it_carries_on_from(
        env, scope, again):
    """Spec 4 (the user's case: re-run a later call without logging in again): run #1 logs in, sets theme, calls data,
    then later replaces the session and adds extra. Run only data (--only) and Run from data (--from), with the inputs
    of run #1, send exactly the cookies the session held just before data in run #1 (not those after it), log in
    again nowhere, and the Log says the cookies came from run #1."""
    with LoginSite() as site:
        flow = four_calls(env, site)
        first, first_id = run(env, flow)
        assert first.returncode == 0, said(first)
        assert site.paths() == ["/login", "/prefs", "/data", "/later"]
        before = {"sessionid": site.issued[0], "theme": site.theme}

        rerun, rerun_id = run(env, flow, scope, "data", "--inputs-from", first_id)

    assert rerun.returncode == 0, said(rerun)
    assert env.last_run() == (rerun_id, "success")
    assert site.seen[4:] == [(path, before) for path in again], site.seen[4:]
    assert [m for _, m in logs(env, rerun_id) if says_from_run(m, first_id)], logs(env, rerun_id)


def test_a_rerun_whose_session_the_server_has_expired_fails_clearly_and_does_not_log_in_again(env):
    """Spec 4 (if the server has expired the session, that call fails clearly): after run #1 the server forgets its
    sessions; Run only data with the inputs of run #1 sends the kept cookie, the server answers 401, and the block and
    the run fail, the error saying 401; nothing logs in again behind the user's back."""
    with LoginSite() as site:
        flow = four_calls(env, site)
        first, first_id = run(env, flow)
        assert first.returncode == 0, said(first)
        site.valid.clear()

        rerun, rerun_id = run(env, flow, "--only", "data", "--inputs-from", first_id)

    assert rerun.returncode == 1, said(rerun)
    assert env.last_run() == (rerun_id, "failed")
    assert site.seen[4:] == [("/data", {"sessionid": site.issued[0], "theme": site.theme})], site.seen[4:]
    failed = result_of(env, rerun_id, "data")
    assert failed["status"] == "failed" and "401" in failed["error"], failed


def test_the_cookies_kept_for_running_again_are_in_no_plain_text_file_nor_in_the_log(env):
    """Spec 4 (kept with the run like outputs, never written in plain text where others could read the values): after
    a run and a Run only data that carries on from it, no file outside the runs' kept results (the flow's folder, the
    Taskloom folder; the history database aside, whose Log lines are checked) holds a cookie's value, and no Log line
    of either run does."""
    with LoginSite() as site:
        flow = four_calls(env, site)
        first, first_id = run(env, flow)
        assert first.returncode == 0, said(first)
        rerun, rerun_id = run(env, flow, "--only", "data", "--inputs-from", first_id)
        assert rerun.returncode == 0, said(rerun)
        assert site.seen[4:] == [("/data", {"sessionid": site.issued[0], "theme": site.theme})], site.seen[4:]
        values = [*site.issued, site.theme]

    kept_results, written = env.home / "runs", []
    for path in env.root.rglob("*"):
        if not path.is_file() or kept_results in path.parents or path.name.startswith("history.db"):
            continue
        data = path.read_bytes()
        written += [(str(path.relative_to(env.root)), v) for v in values if v.encode("utf-8") in data]
    assert written == [], f"a cookie's value is written in plain text: {written}"
    for run_id in (first_id, rerun_id):
        assert [(b, m) for b, m in logs(env, run_id) if any(v in m for v in values)] == [], run_id


# --- 5: a Session inside a Loop; a Splitter's lines inside a Session ------------------------------------------------------

def test_a_session_inside_a_loop_gives_each_item_its_own_cookies(env):
    """Spec 5: a Session inside a Loop (three items, one after another): each item's login starts with no cookies (not
    the item before's), and its call after it carries the session its own login got."""
    with LoginSite() as site:
        flow = flow_file(env.root / "flow.yaml", {
            "manual": MANUAL,
            "make": python("output = ['north', 'south', 'east']", inputs=(), within=None),
            "each": {"type": "logic.loop", "config": {}, "ui": {"x": -40, "y": -120}},
            "sess": {**session(), INSIDE: "each"},
            "login": api(site.url("/login"), 0), "whoami": api(site.url("/whoami"), 1),
        }, ["manual.start -> make.after", "make.output -> each.items", "each.item -> login.after",
            *chain("login", "whoami")])

        result, run_id = run(env, flow)

    assert result.returncode == 0, said(result)
    assert env.last_run() == (run_id, "success")
    assert len(site.issued) == 3, site.seen
    assert site.seen == [step for sid in site.issued for step in (("/login", {}), ("/whoami", {"sessionid": sid}))], \
        site.seen


def test_the_lines_of_a_splitter_inside_a_session_run_at_the_same_time_and_share_its_cookies(env):
    """Spec 5 (lines running at the same time inside share its cookies safely): login, then a Splitter's four lines
    inside the Session, whose calls the server holds until all four are in (so they run at the same time), each
    carrying login's cookie and each setting a cookie of its own; after the Merger, the last call carries login's
    cookie and all four."""
    sid, together = token("session"), threading.Barrier(4, timeout=30)

    def answer(path, cookies):
        if path == "/login":
            return 200, [("Set-Cookie", f"sessionid={sid}; Path=/")], "logged in"
        if path.startswith("/mark/"):
            try:
                together.wait()
            except threading.BrokenBarrierError:
                return 503, [], "the four lines' calls did not come at the same time"
            return 200, [("Set-Cookie", f"line{path[-1]}=on; Path=/")], "marked"
        return 200, [], "ok"

    marks = [f"mark{k}" for k in range(1, 5)]
    with Site(answer) as site:
        flow = flow_file(env.root / "flow.yaml", {
            "manual": MANUAL, "sess": session(),
            "login": api(site.url("/login"), 0),
            "split": {"type": "logic.splitter", "config": {}, "ui": {"x": 60 + NODE_W + 80, "y": 100}, INSIDE: "sess"},
            **{m: {**api(site.url(f"/mark/{k}"), 2), "ui": {"x": 60 + 2 * (NODE_W + 80), "y": 100 + 160 * k}}
               for k, m in enumerate(marks, 1)},
            "merge": {"type": "logic.merger", "config": {}, "ui": {"x": 60 + 3 * (NODE_W + 80), "y": 100},
                      INSIDE: "sess"},
            "last": api(site.url("/last"), 4),
        }, ["manual.start -> login.after", "login.status -> split.value", *(f"split.lines -> {m}.after" for m in marks),
            *(f"{m}.status -> merge.lines" for m in marks), "merge.value -> last.after"])

        result, run_id = run(env, flow)

    assert result.returncode == 0, said(result) + str(site.seen)
    assert env.last_run() == (run_id, "success")
    by_path = dict(site.seen)
    assert {f"/mark/{k}": by_path.get(f"/mark/{k}") for k in range(1, 5)} == \
        {f"/mark/{k}": {"sessionid": sid} for k in range(1, 5)}, site.seen
    assert by_path.get("/last") == {"sessionid": sid, **{f"line{k}": "on" for k in range(1, 5)}}, site.seen


# --- 1: the editor: added from the Blocks list, drawn as a container like the Loop -----------------------------------------

def test_the_session_is_added_from_the_blocks_list_under_servers_as_a_box_with_its_name_and_icon_at_the_top(win):
    """Spec 1: the Blocks list has Session under Servers; added from it, it is a servers.api_session drawn as a box a
    block fits in, with its name at its top and an icon at its top left on the Servers colour."""
    servers = [item.text(0) for item in rows(win.palette_panel).get("Servers", [])]
    assert "Session" in servers, servers
    tab = win.new_flow()
    try:
        add_from_palette(win, "Session")
        settle()
        [sess] = [b for b, spec in tab.doc.blocks.items() if spec["type"] == SESSION]
        rect, _ = box(tab, sess)
        assert rect.width() > NODE_W and rect.height() > HEADER_H + 6 + ROW_H + FOOTER_H, rect  # a block fits in it
        assert [t for t in box_texts(tab, sess) if sess in t["text"] and t["center"][1] < rect.top() + 40], \
            box_texts(tab, sess)
        top = tab.scene.nodes[sess].sceneBoundingRect().top()
        icon = ink(pixels(render(tab.scene, QRectF(rect.left() - 4, top, 60, rect.top() + 48 - top), 2.0)), SERVERS)
        assert has_icon(icon, 2.0), "no icon on the Servers colour at the top left of the Session's box"
    finally:
        clean(win)


def test_an_api_request_dragged_into_the_sessions_box_is_inside_it_and_dragged_out_is_not(win, tmp_path):
    """Spec 1 (the Loop's membership, reused): an API Request dragged into the Session's box is inside the Session (the
    saved flow file says so); dragged out again, it is not."""
    sess = session(0, 0)
    outside = {**api("http://127.0.0.1:9/login", within=None), "ui": {"x": -NODE_W - 160, "y": 100}}
    path = flow_file(tmp_path / "flow.yaml", {"sess": sess, "login": outside})
    tab = win.open_flow(path)
    try:
        settle()
        rect, _ = box(tab, "sess")
        assert not box(tab, "sess")[0].intersects(drawn(tab, "login")), "login should start outside the box"
        drag(tab, title(tab, "login"), title(tab, "login") + (rect.center() - drawn(tab, "login").center()))

        assert tab.doc.blocks["login"].get(INSIDE) == "sess", tab.doc.blocks["login"]
        assert box(tab, "sess")[0].contains(drawn(tab, "login"))
        assert win.save(tab)
        assert saved(path)["blocks"]["login"].get(INSIDE) == "sess"

        held = box(tab, "sess")[0]
        drag(tab, title(tab, "login"), QPointF(title(tab, "login").x(), held.bottom() + 200))

        assert not tab.doc.blocks["login"].get(INSIDE), tab.doc.blocks["login"]
    finally:
        clean(win)


# A block inside that waits until go.txt is there (60 s at most).
GATE = ("import os, time\nfor _ in range(1200):\n    if os.path.exists('go.txt'):\n        break\n"
        "    time.sleep(0.05)\noutput = 1")


def progress_bar(tab, rect: QRectF, background: QColor) -> bool:
    """Whether a bar runs across the box under its top, where the Loop draws its progress bar: a row there most of
    whose width is unlike the box's background."""
    area = QRectF(round(rect.left()) + 12, round(rect.top()) + HEADER_H - 4, round(rect.width()) - 24, 14)
    rgb = pixels(render(tab.scene, area))
    off = np.abs(rgb - background.getRgb()[:3]).max(axis=2) > 24
    return bool((off.sum(axis=1) >= 0.6 * area.width()).any())


def how_the_session_looks(tab, sess: str) -> list[str]:
    """What does not read well on the Session's box, or is not as specified: its background not grey or hardly unlike
    the canvas, no shadow darker than it, its name missing or in a colour that does not stand out from what is behind
    it; a counter or a progress bar on it."""
    rect, background = box(tab, sess)
    problems = []
    rgb = background.getRgb()[:3]
    if max(rgb) - min(rgb) > 20:
        problems.append(f"its background {background.name()} is not grey")
    canvas = most_common(render(tab.scene, QRectF(round(rect.right()) + 60, round(rect.top()), 40,
                                                  round(rect.height()))))
    if abs(background.lightness() - canvas.lightness()) < 8:
        problems.append(f"its background {background.name()} hardly differs from the canvas {canvas.name()}")
    shadow = beside(tab, rect, sess)
    if shadow > background.lightness() - 8 or shadow > canvas.lightness() - 4:
        problems.append(f"no darker shadow beside it (darkest lightness {shadow:.0f}; its background "
                        f"{background.lightness()}, the canvas {canvas.lightness()})")
    texts = box_texts(tab, sess)
    names = [t for t in texts if sess in t["text"]]
    if not names:
        problems.append(f"its name is not on it: {[t['text'] for t in texts]}")
    counters = [t["text"] for t in texts if re.search(r"\d+\s*/\s*\d+", t["text"])]
    if counters:
        problems.append(f"it shows a counter: {counters}")
    if progress_bar(tab, rect, background):
        problems.append("it shows a progress bar")
    for t in names:
        s = max(t["size"], 6.0)
        cx, cy = t["center"]
        behind = most_common(render(tab.scene, QRectF(cx - 2 * s, cy - 2 * s, 4 * s, 4 * s), 2.0))
        ink_colour, alpha = QColor(t["fill"]), t["opacity"]
        shown = QColor(*(round(alpha * f + (1 - alpha) * b) for f, b in zip(ink_colour.getRgb()[:3],
                                                                           behind.getRgb()[:3])))
        if contrast(shown, behind) < TEXT:
            problems.append(f"{t['text']!r}: {shown.name()} on {behind.name()} ({contrast(shown, behind):.2f}:1)")
    return problems


def test_the_box_is_grey_with_a_darker_shadow_its_name_reads_well_in_light_and_dark_and_it_has_no_progress_bar(
        win, qtbot, tmp_path, monkeypatch):
    """Spec 1 (drawn like the Loop, no progress bar, reading well in Light and Dark): while a block inside it runs, in
    the Light and the Dark theme (Tools > Settings > Theme), the Session's box is grey, unlike the canvas, with a shadow
    darker than both beside it; its name stands out from what is behind it (4.5:1, WCAG's bar for text); it shows no
    counter and no progress bar."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings may write a Startup script there)
    app = QApplication.instance()
    look = app.style().name(), app.styleSheet(), QApplication.palette(), QToolTip.palette()
    path = flow_file(tmp_path / "flow.yaml", {
        "manual": MANUAL, "sess": session(0, 0), "work": python(GATE, inputs=()),
    }, ["manual.start -> work.after"])
    tab = win.open_flow(path)
    found = {}
    try:
        win.run_flow(tab)
        assert tab.runs, f"the run did not start: {win.message_boxes}"
        qtbot.waitUntil(lambda: tab.scene.nodes["work"].status == "running" or not tab.running, timeout=60_000)
        assert tab.scene.nodes["work"].status == "running", f"the run ended before work ran: {tab.last}"
        for theme in ("Light", "Dark"):
            choose(win, theme)
            settle()
            found[theme] = how_the_session_looks(tab, "sess")
    finally:
        (tmp_path / "go.txt").write_text("", encoding="utf-8")
        wait_done(qtbot, tab)
        QGuiApplication.styleHints().unsetColorScheme()
        if app.style().name() != look[0]:
            QApplication.setStyle(look[0])
        if app.styleSheet() != look[1]:
            app.setStyleSheet(look[1])
        QApplication.setPalette(look[2])
        QToolTip.setPalette(look[3])
        settle()
        clean(win)
    assert {theme: problems for theme, problems in found.items() if problems} == {}


# --- 4: in the editor, right-click a call inside > Run only this block ------------------------------------------------------

def test_right_click_run_only_this_block_on_a_call_inside_carries_on_with_the_sessions_cookies(win, qtbot, tmp_path):
    """Spec 4, the user's path in the editor: after a run (login, then data, inside a Session), right-click data > Run
    only this block, with the same inputs: it can be chosen, the call carries the cookie login got in that run, login
    does not run again, and the Log says the cookies came from that run."""
    with LoginSite() as site:
        path = flow_file(tmp_path / "flow.yaml", {
            "manual": MANUAL, "sess": session(0, 0),
            "login": api(site.url("/login"), 0), "data": api(site.url("/data"), 1),
        }, ["manual.start -> login.after", *chain("login", "data")])
        tab = win.open_flow(path)
        try:
            win.run_flow(tab)
            assert tab.runs, f"the run did not start: {win.message_boxes}"
            wait_done(qtbot, tab)
            assert tab.last["status"] == "success", tab.last
            assert site.paths() == ["/login", "/data"]
            first_id = tab.result_run("data")

            seen = context_menu(qtbot, win, tab, title(tab, "data"), "Run only this block, with the same inputs")

            assert any(text.startswith("Run only this block, with the same inputs") and can for text, can, _ in seen), \
                seen
            qtbot.waitUntil(lambda: len(tab.runs) == 2 and not tab.running and tab.runs[-1].status is not None,
                            timeout=60_000)
            assert tab.runs[-1].status == "success"
            assert site.seen[2:] == [("/data", {"sessionid": site.issued[0]})], site.seen
            assert [line for line in win.log.text.toPlainText().splitlines() if says_from_run(line, first_id)], \
                win.log.text.toPlainText()[-3000:]
        finally:
            clean(win)


# --- 6: Help > Documentation, the hover help, the README ------------------------------------------------------------------

def test_the_documentation_and_the_hover_help_describe_the_session(win):
    """Spec 6: Help > Documentation has the Session (in Contents and on the Servers page), telling that the API Request
    blocks inside it share their cookies; its hover help in the Blocks list tells of cookies."""
    from taskloom.editor import docs

    pages = docs.pages(win.registry)
    assert f'#{SESSION}"' in pages["index.html"]
    said = entry(pages[docs.category_page("Servers")], SESSION)
    for words in ("cookie", "api request", "inside"):
        assert words in said, f"the Session's entry does not tell about {words!r}: {said}"
    items = win.palette_panel.tree.findItems("Session", Qt.MatchExactly | Qt.MatchRecursive)
    assert items, "no Session in the Blocks list"
    assert "cookie" in plain(items[0].toolTip(0)), items[0].toolTip(0)


def test_readme_has_a_section_on_the_session_with_a_login_then_a_call_that_needs_its_cookie():
    """Spec 6: README has a section on the Session, telling that the API Request blocks inside it share their cookies,
    with an example: a login request, then a call that needs its cookie."""
    lines = (REPO / "README.md").read_text(encoding="utf-8").splitlines()
    fenced = [line.startswith("```") for line in lines]
    in_code = [sum(fenced[:i]) % 2 == 1 for i in range(len(lines))]  # (a # line in an example is no heading)
    heads = [(i, len(m.group(1))) for i, line in enumerate(lines) if not in_code[i] and (m := re.match(r"(#+) ", line))]
    found = [(i, level) for i, level in heads if "Session" in lines[i]]
    assert found, "README has no section whose heading names the Session"
    start, level = found[0]
    end = next((i for i, lv in heads if i > start and lv <= level), len(lines))
    section = "\n".join(lines[start:end])
    assert "```" in section or "example" in section.lower(), "README's Session section has no example"
    said = plain(section)
    for words in ("cookie", "api request", "inside", "login"):
        assert words in said, f"README's Session section does not tell about {words!r}"
