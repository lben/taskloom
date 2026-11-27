"""Credentials (docs/promises/credentials.yaml) where other features fill or show a text: a reference
{{secret:CARD.key}} in a block's typed value (docs/promises/typed-values.yaml TV-3: a typed text and each item of a
typed list are filled like any field), and a value hidden in a table's cell as the Log shows the table
(docs/promises/long-output.yaml NC-7). Through the real `taskloom run` only, against the tests' own credential store
(tests/credential_store, never the real one) and a web server on this computer started by the test (never the
internet). Made-up values only.

What the tests take as given, beside the contracts of tests/test_credentials_refs.py, whose cards and values they use:
- A typed value is written in the flow file under the block's `inputs`, {hole: {type, value}}; a typed list is its
  items written with ", " between them.
- A run keeps each table a block makes with *** in place of a stored secret (CR-5), so a stored secret never reaches a
  table's look. A cookie a server set (API Request's headers output, Set-Cookie) is hidden as a secret is, but kept
  with the run's outputs: what hides it in a table's cell is the table's look itself. The Log shows a table's text
  cells cut at 60 characters, after a hidden value in them is replaced by ***.
"""

import http.server
import json
import threading

import pytest

from test_credentials_refs import PASSWORD, TOKEN, cards, log_of, none_shows, run, write_flow


# --- {{secret:CARD.key}} in a typed value (TV-3) ----------------------------------------------------------------------

@pytest.mark.invariant("CR-4")
@pytest.mark.invariant("CR-5")
@pytest.mark.invariant("TV-3")
def test_a_reference_in_a_typed_text_and_in_an_item_of_a_typed_list_gives_the_block_the_value(env, credential_store):
    """A Python Code block's typed text "Bearer {{secret:UAT.token}}" and typed list "plain, {{secret:UAT.password}}"
    arrive filled with the stored values, as if a line had brought them; the run tells neither value."""
    cards(env, credential_store)
    code = ('import json\nwith open("got.json", "w", encoding="utf-8") as f:\n    json.dump([note, items], f)\n'
            'print(note, items)')
    path = write_flow(env, {"use": {
        "type": "logic.python", "config": {"inputs": ["note", "items"], "outputs": [], "code": code},
        "inputs": {"note": {"type": "text", "value": "Bearer {{secret:UAT.token}}"},
                   "items": {"type": "list", "value": "plain, {{secret:UAT.password}}"}}}})

    result = run(env, path)

    none_shows(env, result, TOKEN, PASSWORD)
    assert json.loads((env.root / "got.json").read_text(encoding="utf-8")) == [f"Bearer {TOKEN}", ["plain", PASSWORD]]


# --- a hidden value in a table's cell, as the Log shows the table (NC-7) --------------------------------------------

COOKIE = "sid=a.b*c+Q7; Path=/"  # with . * + (which mean something in a regular expression); all of it is hidden
LINK = "/orders?status=open&sort=newest&per_page=100&token="  # 51 characters: with COOKIE 71, with *** in its place 54


@pytest.fixture
def cookie_site():
    """A web server on this computer whose every answer sets the cookie COOKIE."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Set-Cookie", COOKIE)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"in")

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.invariant("NC-7")
def test_a_hidden_value_with_punctuation_in_a_tables_cell_shows_in_the_log_as_exactly_three_stars(env, cookie_site):
    """A table whose cell holds a cookie the server set, after a link: the Inspect after it shows the cell in the Log
    whole, as the link and exactly *** (the cookie is replaced as it is written, its . * + being no pattern, before
    the cell is cut at 60 characters); the cookie shows nowhere in the run."""
    code = ('import polars as pl\n'
            f'orders = pl.DataFrame({{"id": [1], "link": [{LINK!r} + headers["Set-Cookie"]]}})')
    path = write_flow(env, {
        "login": {"type": "servers.api_request", "config": {"url": f"{cookie_site}/login"}},
        "make": {"type": "logic.python", "config": {"inputs": ["headers"], "outputs": ["orders"], "code": code}},
        "look": {"type": "logic.inspect"}},
        ["login.headers -> make.headers", "make.orders -> look.value"])

    result = run(env, path)

    none_shows(env, result, COOKIE, "a.b*c")
    shown = log_of(env, "look").splitlines()
    assert shown[0] == "1 rows × 2 columns", shown
    [row] = shown[next(i for i, line in enumerate(shown) if line.startswith("╞")) + 1:-1]
    assert [cell.strip() for cell in row.strip("│").split("┆")] == ["1", LINK + "***"], shown
