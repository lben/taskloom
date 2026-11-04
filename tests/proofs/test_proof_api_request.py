"""Proof: API Request, Postman in a block: a method, a URL, Params and Headers with ticks, Auth from stored secrets, a
Body, Response as, the statuses that count as success, an API connection or values set in the block, Run only this
block as Postman's Send, and Copy as cURL. How proofs are written: README.md here."""

import hashlib
import json
import re
from pathlib import Path

import keyring
import yaml
from keyring.backends import fail
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QHelpEvent, QPalette, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QGroupBox, QLabel, QLineEdit, QTableWidget,
                               QToolTip)

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_block_icons import list_row
from proofs.test_proof_jdbc_fields import activate, answering
from proofs.test_proof_python_reusable_block import plain, run_now, tooltip
from proofs.test_proof_run_output import click, setting, type_into
from proofs.test_proof_settings_wording import menu_action, row
from proofs.test_proof_tab_plus import set_theme
from proofs.test_proof_theme import theme_list
from taskloom.editor import theme
from taskloom.editor.canvas import FOOTER_H, HEADER_H, NODE_W, ROW_H
from tests.test_api_request import json_answer, secret, serving
from tests.test_block_icons import rows
from tests.test_inspect_mermaid import contrast
from tests.test_jdbc_fields import field, type_in
from tests.test_paths_editor_editing import clean, flow_file, press
from tests.test_theme import HINT, TEXT, in_place, not_in_theme, strongest

ASKED = ("add also an API block that is pretty much Postman in a block with support to GET, POST, PUT, etc, headers, "
         "and all the other good stuff that Postman supports. Also in a clearly visible section of its Properties "
         "panel include a \"import from cURL String\" where a user can just paste a cURL sample it has seen on an "
         "example documentation and it automatically fills the available information from the String into the "
         "relevant fields of the Properties of the block. — Agreed with you: Response as (automatic, text, table, "
         "file), which you loved; the error output stays, carrying the status, headers and body; per block, either "
         "an API connection or values filled in the block (“having the flexibility to choose one or the other would "
         "be great”); Copy as cURL: “nice, yes”.")
API = "servers.api_request"
ORDERS = [{"id": 1, "item": "paper", "quantity": 10, "status": "open"},
          {"id": 2, "item": "toner", "quantity": 2, "status": "open"},
          {"id": 3, "item": "labels", "quantity": 5, "status": "open"}]
TOKEN_NAME = "orders_token"  # the stored secret list_orders sends as its Bearer token
NEW_ORDER = {"item": "paper", "quantity": 3}  # what new_order makes, the body create_order sends
MISSING = {"error": "no order 999"}  # the server's answer to /orders/999, with 404
HANDLE = ("print('the error output carries:', ', '.join(error))\n"
          "output = {'status': error['status'], 'content type': error['headers']['Content-Type'], "
          "'body': error['body']}")
CHECK_COOKIE = ("import hashlib\nprint('login answered with the headers', headers)\ncookie = headers['Set-Cookie']\n"
                "output = 'fingerprint ' + hashlib.sha256(cookie.encode()).hexdigest()[:16]")
LOG_HEIGHT = 230  # the Log, under the canvas


def start(x=0, y=0) -> dict:
    return {"type": "trigger.manual", "ui": {"x": x, "y": y}}


def request(x, y, **config) -> dict:
    return {"type": API, "config": config, "ui": {"x": x, "y": y}}


def python(inputs: list, code: str, x, y) -> dict:
    return {"type": "logic.python", "config": {"inputs": inputs, "code": code}, "ui": {"x": x, "y": y}}


def open_flow(win, monkeypatch, path):
    """File > Open…, choosing `path` in the file dialog: the tab it opens in."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    menu_item(file_menu, "Open…").trigger()
    QTest.qWait(100)
    tab = win.current
    assert tab is not None and tab.doc.path == path.resolve(), f"{path.name} does not open"
    return tab


def in_window(base, widget, rect: QRect | None = None) -> QRect:
    """`rect` of `widget` (all of it by default) in a screenshot of `base`: the window, or a panel around it."""
    rect = rect if rect is not None else widget.rect()
    return QRect(widget.mapTo(base, rect.topLeft()), rect.size())


def click_bottom_tab(win, panel):
    """Click one of the tabs under the canvas: the Log, Data preview or Run history."""
    bar = win.bottom_tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.bottom_tabs.indexOf(panel)).center())
    QTest.qWait(100)


def log_text(win) -> str:
    return win.log.text.toPlainText()


def run_log(win, run, api) -> str:
    """The Log's lines of `run`, from the line that says it started to the one that says how it ended, the server's
    port written as PORT."""
    lines = log_text(win).splitlines()
    first = next(i for i, line in enumerate(lines) if f"run #{run.run_id} · " in line)
    last = next(i for i, line in enumerate(lines) if f"DONE run #{run.run_id} " in line)
    return "\n".join(lines[first:last + 1]).replace(api.base, "http://127.0.0.1:PORT")


def run_only(p, win, qtbot, tab, block: str, look=None):
    """Right-click `block`, choose Run only this block, with inputs I choose… (`look(menu)` first, to shoot it), press
    Run in the window that asks for its inputs, leaving its body input empty, and wait for the run to end: the run."""
    count = len(tab.runs)

    def in_the_menu(menu):
        if look:
            look(menu)
        p.while_open(lambda: choose(menu, "Run only this block, with inputs I choose…"),
                     lambda dialog: press(dialog, "Run"))

    p.while_open(lambda: right_click(tab, block), in_the_menu)
    qtbot.waitUntil(lambda: len(tab.runs) == count + 1 and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)
    QTest.qWait(200)  # what the run printed last reaches the Log
    return tab.runs[-1]


def properties_row(win, label: str, base=None) -> QRect:
    """The setting `label` of Properties (its name and its field), in a screenshot of the window (or of `base`)."""
    name, box = setting(win, label)
    return in_window(base or win, name) | in_window(base or win, box)


def params_box(win) -> QRect:
    return properties_row(win, "Params")


def headers_box(win) -> QRect:
    return properties_row(win, "Headers")


def params_table(win) -> QTableWidget:
    """The Params table, as Properties shows it now (it is built again as the block changes)."""
    return setting(win, "Params")[1].findChild(QTableWidget)


def table_rows(table: QTableWidget) -> list[tuple[bool, str, str]]:
    """A key/value table's rows: (ticked, name, value)."""
    return [(table.item(r, 0).checkState() == Qt.Checked, table.item(r, 1).text(), table.item(r, 2).text())
            for r in range(table.rowCount())]


def shown_params(win) -> list[tuple[bool, str, str]]:
    return table_rows(params_table(win))


def edit_cell(table: QTableWidget, row: int, column: int, text: str):
    """Double-click a cell of the table, type `text` over what it has and press Enter, as you would."""
    at = table.visualItemRect(table.item(row, column)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.qWait(50)
    editor = QApplication.focusWidget()
    assert isinstance(editor, QLineEdit), f"double-clicking the cell opened no text box: {editor}"
    editor.selectAll()
    QTest.keyClicks(editor, text)
    QTest.keyClick(editor, Qt.Key_Return)
    QTest.qWait(100)


def scroll_to(win, label: str):
    """Scroll Properties down until the setting `label` is at its top, as dragging its scroll bar does."""
    name = setting(win, label)[0]
    bar = win.properties.verticalScrollBar()
    bar.setValue(bar.value() + name.mapTo(win.properties.viewport(), QPoint(0, 0)).y() - 30)
    QTest.qWait(50)


def tops(win, *shown) -> bool:
    """Properties shows these (settings by their names, or widgets) one under the other, in this order."""
    body = win.properties.widget()
    ys = [(setting(win, s)[0] if isinstance(s, str) else s).mapTo(body, QPoint(0, 0)).y() for s in shown]
    return ys == sorted(ys) and len(set(ys)) == len(ys)


def preview_columns(win) -> list[str]:
    """The names of the columns of the table Data preview shows (each header says its name, then its type)."""
    model = win.preview.view.model()
    return [str(model.headerData(c, Qt.Horizontal)).split("\n")[0] for c in range(model.columnCount())]


def preview_lines(win) -> QRect:
    """What Data preview shows as text: the line naming it, and its text as far as it goes, in the window."""
    box = win.preview.text
    lines = box.toPlainText().splitlines() or [""]
    metrics = box.fontMetrics()
    used = QRect(0, 0, min(max(metrics.horizontalAdvance(line) for line in lines) + 16, box.viewport().width()),
                 min(len(lines) * metrics.lineSpacing() + 12, box.viewport().height()))
    return in_window(win, win.preview.label) | in_window(win, box.viewport(), used)


def preview_table(win) -> QRect:
    """The table Data preview shows, from the line naming it to its last cell, in the window."""
    view = win.preview.view
    model = view.model()
    last = view.visualRect(model.index(model.rowCount() - 1, model.columnCount() - 1))
    return in_window(win, win.preview.label) | in_window(win, view.viewport(), last)


def run_lines(win, run, width: int = 720) -> QRect:
    """The Log's lines of `run`, from the line that says it started to the one that says how it ended, as far as
    `width` pixels from their start (a line may go on far to the right), in the window."""
    first = span(win, win.log.text, f"run #{run.run_id} · ")
    last = span(win, win.log.text, f"DONE run #{run.run_id} ")
    return QRect(in_window(win, win.log.text.viewport()).left(), first.top(), width, last.bottom() - first.top() + 1)


def pick(combo: QComboBox, text: str):
    """Open the list of a choice box and click `text` in it, as you would (Properties may then be built again)."""
    view, places = theme_list(combo)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, places[text].center())
    QTest.qWait(100)


def said_under(win, starts: str) -> QLabel:
    """The text Properties shows that starts with `starts` (e.g. a note under a setting)."""
    found = [w for w in win.properties.widget().findChildren(QLabel) if w.isVisible() and w.text().startswith(starts)]
    assert len(found) == 1, f"Properties shows {len(found)} texts starting {starts!r}"
    return found[0]


def properties_text(win) -> str:
    """Every text Properties shows: its labels, its text boxes and the cells of its tables."""
    body = win.properties.widget()
    texts = [w.text() for w in body.findChildren(QLabel)] + [w.text() for w in body.findChildren(QLineEdit)]
    texts += [t.item(r, c).text() for t in body.findChildren(QTableWidget) for r in range(t.rowCount())
              for c in range(t.columnCount()) if t.item(r, c) is not None]
    return "\n".join(texts)


def shows_setting(win, label: str) -> bool:
    return any(w.isVisible() and w.text() == label for w in win.properties.widget().findChildren(QLabel))


def name_it(name: str):
    """What you do in the window that asks a new connection's name: type it, then Enter."""
    def answer(ask):
        edit = ask.findChild(QLineEdit)
        QTest.keyClicks(edit, name)
        QTest.keyClick(edit, Qt.Key_Return)
    return answer


def block_texts(tab, block: str) -> dict[str, float]:
    """How much the parts of a block's text stand out from what is behind them on the canvas, as painted (inside the
    block's edge, clear of its outline and its ports): its name, the names of its outputs response, status and
    headers, and its result at its foot."""
    node = tab.scene.nodes[block]
    top, bottom = node.ports[("response", True)].y(), node.ports[("headers", True)].y()
    parts = {"name": QRectF(30, 5, NODE_W - 40, HEADER_H - 10),
             "outputs": QRectF(NODE_W / 2, top - ROW_H / 2 + 2, NODE_W / 2 - 14, bottom - top + ROW_H - 4),
             "result": QRectF(8, node.height - FOOTER_H + 4, NODE_W - 16, FOOTER_H - 8)}
    return {name: strongest(in_place(tab.view.viewport(), tab.view.mapFromScene(node.mapToScene(part)).boundingRect()))
            for name, part in parts.items()}


def span(win, box, text: str) -> QRect:
    """Where `text` is shown in a text box (the Log), from its first letter to its last, in the window."""
    found = box.document().find(text)
    assert not found.isNull(), f"the text box does not show {text!r}"
    start, end = QTextCursor(found), QTextCursor(found)
    start.setPosition(found.selectionStart())
    end.setPosition(found.selectionEnd())
    rect = box.cursorRect(start) | box.cursorRect(end)
    return in_window(win, box.viewport(), rect.adjusted(-3, 0, 3, 0))


def test_api_request_is_postman_in_a_block(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("API Request: Postman in a block", ASKED)
    p.idea("API Request, a new block under Servers, calls a web API as Postman's Send does. Its Properties hold the "
           "request, grouped in the order of Postman's tabs: the Method (GET, POST, PUT, PATCH, DELETE, HEAD or "
           "OPTIONS) and the URL, then Params, Auth, Headers, Body, Response as, the statuses that count as success, "
           "and the redirect and certificate settings.")
    p.idea("Its outputs are the response, as Response as says (automatic: JSON read when the server says it is JSON, "
           "otherwise text; or text, a table, or a file), the status (a number) and the headers. A status that does "
           "not count as success (200-299 unless you write others) fails the block, and its error output carries "
           "the status, the headers and the body to the blocks that handle it.")
    p.idea("A password, token or key is a stored secret: the block names it, and its value is never in the flow "
           "file, the Log, the run history or a copied curl command. A cookie a server sets shows as *** too, while "
           "the blocks after it get its value.")
    p.idea("Each block either has its request set in it, or chooses an API connection (Tools > Connections, kind "
           "Web API) that gives it a base URL, its auth, the headers every request sends and the certificate "
           "settings; the block's own headers are added, its own winning on the same name. Right-click > Run only "
           "this block is Postman's Send; right-click > Copy as cURL copies the request as a curl command.")
    p.why_simplest("It sends its requests with Python's own web library, which comes with Taskloom: nothing else to "
                   "install on the computers that run your flows.")
    p.why_simplest("Every part of the request is an ordinary setting of a block. So placeholders such as {region}, "
                   "the flow file, Retry and Timeout, the Log, the run history, Data preview and Run only this block "
                   "work for it as for every block, with nothing new to learn. Params, Headers and a form's fields "
                   "are one kind of table: a row per name and value, with a tick.")
    p.why_simplest("Each value is kept once: the flow file holds the URL without its ?query, and the Params. "
                   "Properties shows the URL with the ticked Params as its query, and a query you type into the URL "
                   "goes into Params: there is no second copy to keep in step.")
    p.why_simplest("A failed request uses the error output every block has: the block adds the status, the headers "
                   "and the body to it.")
    p.alternative("Running curl, or Postman's own command-line runner, for each request",
                  "Postman's runner needs Node.js installed on every computer that runs flows, and curl's versions "
                  "and options differ from one computer to the next. A token passed on their command line can also "
                  "be seen by other programs while it runs. Python's web library is always there, and a secret "
                  "stays inside the run until it is sent.")
    p.alternative("Adding the “requests” package to Taskloom",
                  "One more package to ship and keep up to date, for what Python's own library already does for "
                  "this block.")
    p.alternative("Only API connections, or only values set in the block",
                  "You asked for the choice per block. A one-off call is quickest set in the block; many calls to "
                  "one API share its base URL, token and headers through one connection.")
    p.alternative("A window of its own for the request, as Postman has",
                  "Properties already shows every block's settings; a second place to edit them would have to be "
                  "kept in step with it.")
    p.fits("It is in the Blocks list under Servers, next to Health Check, with its own icon, and in Help > "
           "Documentation and the hover help, as every block.")
    p.fits("Stored secrets are the ones connections already use (taskloom secret set NAME), in the system's "
           "credential store or a file only you can read; the Log and the run history already hide them as ***.")
    p.fits("An API connection is one more kind in Tools > Connections, next to databases, SSH servers and email "
           "servers; its token is saved as a stored secret, as their passwords are.")
    p.fits("A response read as a table is a table like any other block's: Data preview shows it, and a Write File, "
           "a DuckDB SQL or a To Excel block can take it.")

    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)  # (the runs the editor starts inherit this process's)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(keyring.core, "_keyring_backend", fail.Keyring())  # never the real credential store
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings writes Windows' Startup script there)
    win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)
    token = secret("token")

    with serving() as api:
        def orders(got):
            if got["method"] == "POST":
                return json_answer({"id": 4, **json.loads(got["body"]), "status": "open"}, 201)
            return json_answer(ORDERS)

        api.answers["/orders"] = orders
        api.answers["/orders/999"] = lambda got: json_answer(MISSING, 404)
        app = QApplication.instance()
        as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
        try:
            # --- 1. In the Blocks list ---------------------------------------------------------------------------
            p.step("Look at the Blocks list, on the left, under Servers.")
            servers = {item.text(0): item for item in rows(win.palette_panel)["Servers"]}
            tree = win.palette_panel.tree
            tree.scrollToItem(servers["Stop Remote Process"])
            QApplication.processEvents()
            listed = servers.get("API Request")
            p.check("The Blocks list has API Request under Servers, next to Health Check",
                    listed is not None and "Health Check" in servers
                    and listed.parent().indexOfChild(servers["Health Check"]) - listed.parent().indexOfChild(listed)
                    == 1)
            p.shot(win, "Under Servers: ① API Request, with its icon, the letters API, white on the Servers olive; "
                        "② Health Check just under it.",
                   highlight=[list_row(win, listed), list_row(win, servers["Health Check"])], badges=True,
                   zoom=[list_row(win, listed.parent()), list_row(win, servers["Stop Remote Process"])])

            p.step("Rest the mouse on API Request in the Blocks list.")
            viewport = tree.viewport()
            at = tree.visualItemRect(listed).center()
            QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, at, viewport.mapToGlobal(at)))
            qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
            tip = tooltip()
            said = " ".join(plain(tip.text()).split())
            cls = win.registry.get(API)
            p.shot(tip, "Its hover help says what it does: it calls a web API as Postman's Send does, with what it "
                        "gives and how it fails, and names Copy as cURL and Import from cURL.", over=win,
                   highlight=[tip], zoom=[tip])
            tip.hide()
            p.check("The hover help has the block's name and says what it does",
                    said.startswith("API Request") and " ".join(cls.description.split()) in said)

            # --- 2. Its Properties ---------------------------------------------------------------------------------
            flow = flow_file(tmp_path / "orders.yaml", {
                "start_by_hand": start(0, 40),
                "list_orders": request(300, 0, url=f"{api.base}/orders", auth="Bearer token", secret=TOKEN_NAME,
                                       response_as="table"),
            }, ["start_by_hand.start -> list_orders.after"])
            p.step("Open the flow orders.yaml (File > Open…) and click list_orders, an API Request that calls the "
                   "server's /orders with a Bearer token and reads the answer as a table.")
            p.note("The web API in this proof is a small web server the proof starts on this computer, at "
                   "127.0.0.1 and a free port: nothing goes to the internet. The proof's runs use no proxy: it clears "
                   "HTTPS_PROXY and the like, and names this computer in NO_PROXY. So the system's own proxy settings, "
                   "which Python's web library reads when no such variable is set, are not shown here.")
            tab = open_flow(win, monkeypatch, flow)
            click(tab, "list_orders")
            body = win.properties.widget()
            curl_box = next(b for b in body.findChildren(QGroupBox) if b.title() == "Import from cURL")
            p.shot(win, "Properties shows list_orders: ① at the top, Import from cURL, to fill the request from a curl "
                        "command (a proof of its own shows it); ② then the request, from Connection (none: everything "
                        "is set in this block), Method and URL to Params, Auth and the rest, further down.",
                   highlight=[curl_box, [properties_row(win, "Connection"), params_box(win)]], badges=True,
                   zoom=[curl_box, properties_row(win, "URL")])
            p.check("Properties shows Import from cURL above Connection, Method, URL and Params, in that order",
                    tops(win, curl_box, "Connection", "Method", "URL", "Params"))

            p.step("Click at the end of the URL, type ?status=open&limit=50 and press Enter.")
            url = setting(win, "URL")[1]
            QTest.mouseClick(url, Qt.LeftButton)
            QTest.keyClick(url, Qt.Key_End)
            QTest.keyClicks(url, "?status=open&limit=50")
            QTest.keyClick(url, Qt.Key_Return)
            QTest.qWait(100)
            p.shot(win, "① Params now lists the query's two parameters, status=open and limit=50, each ticked; the "
                        "URL shows them as its query.",
                   highlight=[params_table(win)], badges=True, zoom=[properties_row(win, "URL"), params_box(win)])
            p.check("Params lists status = open and limit = 50, both ticked",
                    shown_params(win) == [(True, "status", "open"), (True, "limit", "50")])
            p.check("The flow keeps the URL without its query, and the two rows in Params",
                    tab.doc.blocks["list_orders"]["config"]["url"] == f"{api.base}/orders"
                    and tab.doc.blocks["list_orders"]["config"]["params"] == [{"key": "status", "value": "open"},
                                                                                {"key": "limit", "value": "50"}])

            p.step("In Params, double-click 50, type 20 and press Enter.")
            edit_cell(params_table(win), 1, 2, "20")
            p.shot(win, "① The URL follows Params: it ends with limit=20 now.",
                   highlight=[setting(win, "URL")[1]], badges=True,
                   zoom=[properties_row(win, "URL"), params_box(win)])
            p.check("The URL shows ?status=open&limit=20",
                    setting(win, "URL")[1].text() == f"{api.base}/orders?status=open&limit=20")

            p.step("Click limit's tick, to untick it.")
            table = params_table(win)
            QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier,
                             table.visualItemRect(table.item(1, 0)).center())
            QTest.qWait(100)
            p.shot(win, "① limit is unticked: it stays in Params, with 20, but is not sent. ② The URL no longer has it: "
                        "?status=open.",
                   highlight=[(params_table(win), 1), setting(win, "URL")[1]], badges=True,
                   zoom=[properties_row(win, "URL"), params_box(win)])
            p.check("Params has status = open ticked and limit = 20 unticked",
                    shown_params(win) == [(True, "status", "open"), (False, "limit", "20")])
            p.check("The URL shows ?status=open", setting(win, "URL")[1].text() == f"{api.base}/orders?status=open")

            p.step("Scroll Properties down, to see the whole request.")
            scroll_to(win, "Connection")
            panel = win.right_tabs

            def rows_of(*labels):
                return [properties_row(win, k, panel) for k in labels]

            groups = [rows_of("Connection", "Method", "URL"), rows_of("Params"), rows_of("Auth", "Stored secret (name)"),
                      rows_of("Headers"), rows_of("Body"), rows_of("Response as", "Statuses that count as success"),
                      rows_of("Follow redirects", "CA certificate file", "Don't verify certificates")]
            p.shot(panel, "The request, in the order of Postman's tabs, each group outlined and numbered: ① Connection, "
                          "Method and URL; ② Params; ③ Auth, a Bearer token from the stored secret orders_token; "
                          "④ Headers (none yet); ⑤ Body (none, for a GET); ⑥ Response as table, and the statuses "
                          "that count as success, 200-299 unless you write others; ⑦ Follow redirects, and the "
                          "certificate settings: a CA certificate file (an authority to trust besides the system's) "
                          "and Don't verify certificates.",
                   highlight=groups, badges=True)
            p.check("The settings come in this order: Connection, Method, URL, Params, Auth, Stored secret (name), "
                    "Headers, Body, Response as, Statuses that count as success, Follow redirects, CA certificate "
                    "file, Don't verify certificates",
                    tops(win, "Connection", "Method", "URL", "Params", "Auth", "Stored secret (name)", "Headers",
                         "Body", "Response as", "Statuses that count as success", "Follow redirects",
                         "CA certificate file", "Don't verify certificates"))

            # --- 3. Run only this block: Postman's Send ----------------------------------------------------------
            p.step(f"Store the orders API's token as the stored secret {TOKEN_NAME}, as taskloom secret set "
                   f"{TOKEN_NAME} does.")
            p.note("taskloom secret set reads the token from the keyboard, which a proof cannot type into. So the "
                   "proof stores it with the same code that command runs, in this proof's own temporary Taskloom "
                   "folder: never in your system's credential store.")
            assert isinstance(keyring.get_keyring(), fail.Keyring), "the proof never uses the real credential store"
            where = win.home.set_secret(TOKEN_NAME, token)
            p.check(f"{TOKEN_NAME} is stored in the proof's temporary Taskloom folder",
                    Path(where).resolve() == (win.home.root / "secrets.yaml").resolve())

            p.step("Right-click list_orders and choose “Run only this block, with inputs I choose…”: Postman's "
                   "Send. In the window that asks for its input, body, leave it empty (a GET sends no body) and "
                   "press Run.")

            def look(menu):
                p.shot(menu, "The block's menu: ① Run only this block, with inputs I choose… (with the same inputs "
                             "is greyed out: it has not run yet). ② Copy as cURL, at the bottom.", over=win,
                       highlight=[menu_item(menu, "Run only this block, with inputs I choose…"),
                                  menu_item(menu, "Copy as cURL")], badges=True, zoom=[menu, "list_orders"])

            run = run_only(p, win, qtbot, tab, "list_orders", look)
            got = api.got("/orders")
            click_bottom_tab(win, win.log)
            sent = f"GET {api.base}/orders?status=open"
            p.shot(win, f"① list_orders is green: it ran on its own, and its response is a table of 3 rows. ② The "
                        f"Log says what it sent, {sent}, and what came back: 200 OK, its size and how long it took.",
                   highlight=["list_orders", [(win.log.text, sent), (win.log.text, "200 OK: ")]], badges=True,
                   zoom=[run_lines(win, run)])
            log = log_text(win)
            answered = re.search(r"200 OK: (\d+) bytes in (\d+) ms", log)
            p.check("The run succeeded, and ran list_orders only", run.status == "success"
                    and tab.scene.nodes["list_orders"].status == "success"
                    and tab.scene.nodes["start_by_hand"].status is None)
            p.check(f"The Log says “{sent}”", sent in log)
            p.check(f"The Log says “200 OK: {answered[1] if answered else 'N'} bytes in "
                    f"{answered[2] if answered else 'M'} ms”: the size of the server's answer",
                    answered is not None and int(answered[1]) == len(json.dumps(ORDERS)))
            p.check("The server got one GET /orders?status=open (limit, unticked, was not sent), with the stored "
                    "token as its Bearer token",
                    [(r["method"], r["query"], r["headers"].get("authorization")) for r in got]
                    == [("GET", [("status", "open")], f"Bearer {token}")])
            p.check("The token's value is nowhere in the Log", token not in log)

            p.step("Click Data preview, under the canvas.")
            click_bottom_tab(win, win.preview)
            p.shot(win, "① Data preview shows list_orders' response as a table: the server's 3 orders, a column for "
                        "each of their fields.", highlight=[preview_table(win)], badges=True,
                   zoom=[preview_table(win)])
            p.check("Data preview shows list_orders.response as a table of 3 rows × 4 columns: id, item, quantity "
                    "and status",
                    win.preview.label.text() == "list_orders.response: 3 rows × 4 columns"
                    and preview_columns(win) == ["id", "item", "quantity", "status"])

            # --- 4. A POST whose body comes from a Python Code block ------------------------------------------------
            flow = flow_file(tmp_path / "new-order.yaml", {
                "start_by_hand": start(0, 40),
                "new_order": python([], f"output = {NEW_ORDER!r}", 260, 20),
                "create_order": request(540, 0, method="POST", url=f"{api.base}/orders"),
            }, ["start_by_hand.start -> new_order.after", "new_order.output -> create_order.body"])
            p.step("Open the flow new-order.yaml and click create_order: a POST to /orders whose body input takes "
                   f"what the Python Code block new_order makes, the dict {NEW_ORDER}.")
            tab = open_flow(win, monkeypatch, flow)
            click(tab, "create_order")
            scroll_to(win, "Auth")
            from_input = said_under(win, "The body comes from the body input")
            p.shot(win, "① Under Body, Properties says the body comes from the body input, new_order's output: a "
                        "dict or list is sent as JSON.",
                   highlight=[[properties_row(win, "Body"), in_window(win, from_input)]], badges=True,
                   zoom=[properties_row(win, "Body"), in_window(win, from_input)])
            p.check("Properties says: “The body comes from the body input (new_order.output): a dict or list is sent "
                    "as JSON, a table as JSON records, text as it is.”",
                    from_input.text() == "The body comes from the body input (new_order.output): a dict or list is "
                                         "sent as JSON, a table as JSON records, text as it is.")

            p.step("Press ▶ Run now.")
            run = run_now(win, qtbot, tab)
            posted = [r for r in api.got("/orders") if r["method"] == "POST"]
            p.shot(win, "① new_order made the dict and ② create_order sent it: the server answered 201 Created, "
                        "as the Log's lines above say, with the new order, id 4, which create_order's response shows.",
                   highlight=["new_order", "create_order"], badges=True, zoom=["new_order", "create_order"])
            p.text("What the Log says of this run (the server's port written as PORT)", run_log(win, run, api))
            p.check("The run succeeded", run.status == "success"
                    and tab.scene.nodes["create_order"].status == "success")
            p.check("The server got one POST /orders, its Content-Type application/json and its body the dict as "
                    f"JSON: {json.dumps(NEW_ORDER)}",
                    len(posted) == 1 and posted[0]["headers"].get("content-type") == "application/json"
                    and json.loads(posted[0]["body"]) == NEW_ORDER)
            p.check("The Log says “201 Created”", re.search(r"201 Created: \d+ bytes in \d+ ms", log_text(win)))

            # --- 5. A status that does not count as success ----------------------------------------------------------
            flow = flow_file(tmp_path / "missing-order.yaml", {
                "start_by_hand": start(0, 40),
                "order_999": request(260, 0, url=f"{api.base}/orders/999"),
                "handle_missing": python(["error"], HANDLE, 540, 20),
            }, ["start_by_hand.start -> order_999.after", "order_999.error -> handle_missing.error"])
            p.step("Open the flow missing-order.yaml and press ▶ Run now. order_999 asks the server for order 999, "
                   "which it does not have; its error output goes to handle_missing, a Python Code block.")
            tab = open_flow(win, monkeypatch, flow)
            run = run_now(win, qtbot, tab)
            failed = f"GET {api.base}/orders/999 answered 404 Not Found"
            p.shot(win, "① order_999 failed: the server answered 404 Not Found, which is not one of the statuses "
                        "that count as success (200-299). ② handle_missing ran with what order_999's error output "
                        "carried, so the run succeeded: the failure was handled.",
                   highlight=["order_999", "handle_missing"], badges=True, zoom=["order_999", "handle_missing"])
            p.text("What the Log says of this run (the server's port written as PORT)", run_log(win, run, api))
            p.check("order_999 failed, handle_missing ran, and the run succeeded (the error was handled)",
                    tab.scene.nodes["order_999"].status == "failed"
                    and tab.scene.nodes["handle_missing"].status == "success" and run.status == "success")
            p.check(f"The Log says “{failed}, which does not count as success (200-299)”",
                    f"{failed}, which does not count as success (200-299)" in log_text(win))
            p.check("handle_missing printed what the error output carries: block, error, traceback, attempts, "
                    "status, headers and body",
                    "the error output carries: block, error, traceback, attempts, status, headers, body"
                    in log_text(win))

            p.step("Click handle_missing, then Data preview.")
            click(tab, "handle_missing")
            click_bottom_tab(win, win.preview)
            handled = win.preview.text.toPlainText()
            p.shot(win, "① handle_missing's output: the status, 404, the Content-Type header and the body, the "
                        "server's answer, which it took from order_999's error output.",
                   highlight=[preview_lines(win)], badges=True, zoom=[preview_lines(win)])
            p.check("handle_missing's output has the status 404, the Content-Type application/json and the body, "
                    f"{json.dumps(MISSING)}",
                    json.loads(handled) == {"status": 404, "content type": "application/json",
                                            "body": json.dumps(MISSING)})

            # --- 6. An API connection --------------------------------------------------------------------------------
            shop_token = secret("shop")
            p.step("Choose Tools > Connections…, press New… and name the connection shop. Choose the Kind Web API "
                   f"(HTTP), type the Base URL {api.base}/v1, choose Auth Bearer token and type the token. Press "
                   "Add header and type X-Version and 1: a header every request sends. Then press Save.")
            p.note("The token is a made-up one for this proof's server; the proof types it as you would.")

            def new_shop(dialog):
                activate(qtbot, dialog)
                assert answering(lambda: press(dialog, "New…"), name_it("shop")) == 1, "New… asked no name"
                activate(qtbot, dialog)
                pick(row(dialog, "Kind")[1], "Web API (HTTP)")
                activate(qtbot, dialog)
                type_in(qtbot, field(dialog, "Base URL"), f"{api.base}/v1")
                pick(row(dialog, "Auth")[1], "Bearer token")
                activate(qtbot, dialog)
                type_in(qtbot, field(dialog, "Token"), shop_token)
                press(dialog, "Add header")
                edit_cell(dialog.props, 0, 0, "X-Version")
                edit_cell(dialog.props, 0, 1, "1")
                p.shot(dialog, "The new connection shop: ① Kind Web API (HTTP), with what it is for; ② its Base URL; "
                               "③ Auth, a Bearer token; ④ the certificate settings, as in the block; ⑤ the token, "
                               "shown as dots, stored as a stored secret when you press Save; ⑥ the headers every "
                               "request sends: X-Version 1.",
                       highlight=[list(row(dialog, "Kind")), list(row(dialog, "Base URL")), list(row(dialog, "Auth")),
                                  [*row(dialog, "CA certificate file"), dialog.dont_verify], list(row(dialog, "Token")),
                                  [dialog.props_label, dialog.props]], badges=True)
                press(dialog, "Save")

            p.while_open(lambda: menu_action(win, "&Tools", "Connections…").trigger(), new_shop)

            written = (win.home.root / "connections.yaml").read_text(encoding="utf-8")
            p.check("Save wrote shop to connections.yaml, in the Taskloom folder: kind api, the base URL, auth Bearer "
                    "token, secret: secret:shop_token (the stored secret's name) and the header X-Version: 1",
                    yaml.safe_load(written).get("shop") == {
                        "kind": "api", "base_url": f"{api.base}/v1", "auth": "Bearer token",
                        "secret": "secret:shop_token", "headers": [{"key": "X-Version", "value": "1"}]})
            p.check("The token is stored as the stored secret shop_token, and is nowhere in connections.yaml",
                    win.home.get_secret("shop_token") == shop_token and shop_token not in written)

            api.answers["/v1/orders"] = lambda got: json_answer(ORDERS)
            flow = flow_file(tmp_path / "shop-orders.yaml", {
                "start_by_hand": start(0, 40), "shop_orders": request(300, 0)},
                ["start_by_hand.start -> shop_orders.after"])
            p.step("Open the flow shop-orders.yaml and click shop_orders, a new API Request. In Properties, choose "
                   "the Connection shop, type /orders as its URL, and in the line under Headers type X-Version: 2 "
                   "and press Enter.")
            tab = shop_tab = open_flow(win, monkeypatch, flow)
            click(tab, "shop_orders")
            pick(setting(win, "Connection")[1].findChild(QComboBox), "shop")
            type_into(setting(win, "URL")[1], "/orders")
            add = setting(win, "Headers")[1].findChild(QLineEdit)
            QTest.mouseClick(add, Qt.LeftButton)
            QTest.keyClicks(add, "X-Version: 2")
            QTest.keyClick(add, Qt.Key_Return)
            QTest.qWait(100)
            scroll_to(win, "Connection")
            note = said_under(win, "From the connection shop")
            p.shot(win, "① What the connection gives, shown here and changed in Tools > Connections: its base URL, "
                        "its Auth with the stored secret shop_token (by its name, never its value), and its header "
                        "X-Version: 1, replaced by this block's X-Version: 2. ② The URL, relative to the base URL. "
                        "③ The block's own header. Auth and the certificate settings are no longer shown: they come "
                        "from the connection.",
                   highlight=[in_window(win, note), properties_row(win, "URL"), headers_box(win)], badges=True,
                   zoom=[properties_row(win, "Connection"), headers_box(win)])
            p.check("Properties shows the connection's values where they cannot be changed: “From the connection "
                    "shop (change it in Tools > Connections): Base URL … Auth: Bearer token, with the stored secret "
                    "shop_token; Header X-Version: 1 (replaced by this block's X-Version: 2)”",
                    note.text().splitlines() == [
                        "From the connection shop (change it in Tools > Connections):", f"Base URL: {api.base}/v1",
                        "Auth: Bearer token, with the stored secret shop_token",
                        "Header X-Version: 1 (replaced by this block's X-Version: 2)"])
            p.check("Its own settings are the URL /orders and the header X-Version: 2; Auth is not shown",
                    tab.doc.blocks["shop_orders"]["config"] == {
                        "connection": "shop", "url": "/orders", "headers": [{"key": "X-Version", "value": "2"}]}
                    and not shows_setting(win, "Auth"))
            p.check("The token's value is nowhere in Properties", shop_token not in properties_text(win))

            p.step("Right-click shop_orders, choose “Run only this block, with inputs I choose…” and press Run, "
                   "leaving its body input empty.")
            run = run_only(p, win, qtbot, tab, "shop_orders")
            got = api.got("/v1/orders")
            click_bottom_tab(win, win.log)
            sent = f"GET {api.base}/v1/orders"
            p.shot(win, f"① shop_orders called the base URL with its /orders added: the Log says {sent}, and 200 OK.",
                   highlight=["shop_orders", [(win.log.text, sent), (win.log.text, "200 OK: ")]], badges=[1, 1],
                   zoom=[run_lines(win, run)])
            p.check("The run succeeded", run.status == "success" and tab.scene.nodes["shop_orders"].status == "success")
            p.check("The server got one GET /v1/orders with X-Version: 2 (the block's, not the connection's 1) and "
                    "the connection's token as its Bearer token",
                    [(r["method"], r["headers"].get("x-version"), r["headers"].get("authorization")) for r in got]
                    == [("GET", "2", f"Bearer {shop_token}")])
            p.check("The token's value is nowhere in the Log", shop_token not in log_text(win))

            # --- 7. Copy as cURL -----------------------------------------------------------------------------------
            p.step("Right-click shop_orders and choose Copy as cURL.")
            QApplication.clipboard().setText("")

            def copy(menu):
                p.shot(menu, "① Copy as cURL, at the bottom of the block's menu.", over=win,
                       highlight=[menu_item(menu, "Copy as cURL")], badges=True, zoom=[menu, "shop_orders"])
                choose(menu, "Copy as cURL")

            p.while_open(lambda: right_click(tab, "shop_orders"), copy)
            QTest.qWait(100)
            copied = QApplication.clipboard().text()
            status_bar = win.statusBar()
            message = QRect(in_window(win, status_bar).topLeft(),
                            QSize(status_bar.fontMetrics().horizontalAdvance(status_bar.currentMessage()) + 16,
                                  status_bar.height()))
            p.shot(win, "① The status bar, at the foot of the window, says it was copied, its secrets hidden.",
                   highlight=[message], badges=True, zoom=[message])
            p.text("What Copy as cURL put on the clipboard (the server's port written as PORT)",
                   copied.replace(api.base, "http://127.0.0.1:PORT"))
            p.check("The status bar says “Copied shop_orders as a curl command, its secrets hidden”",
                    win.statusBar().currentMessage() == "Copied shop_orders as a curl command, its secrets hidden")
            p.check("The clipboard holds a curl command, an option a line as bash reads it, with the base URL and "
                    "/orders, the block's X-Version: 2, and the token as <hidden: secret shop_token>",
                    copied == (f"curl {api.base}/v1/orders \\\n  --location \\\n  -H 'X-Version: 2' \\\n"
                               "  -H 'Authorization: Bearer <hidden: secret shop_token>'"))
            p.check("The token's value is not in it", shop_token not in copied)

            # --- 8. A cookie set by the server ------------------------------------------------------------------------
            session = f"sessionid={secret('session')}; Path=/; HttpOnly"
            api.answers["/login"] = lambda got: json_answer({"user": "report"}, headers={"Set-Cookie": session})
            flow = flow_file(tmp_path / "login.yaml", {
                "start_by_hand": start(0, 40),
                "login": request(260, 0, method="POST", url=f"{api.base}/login"),
                "check_cookie": python(["headers"], CHECK_COOKIE, 540, 20),
            }, ["start_by_hand.start -> login.after", "login.headers -> check_cookie.headers"])
            p.step("Open the flow login.yaml and press ▶ Run now. login logs in: the server's answer sets a cookie "
                   "(Set-Cookie: sessionid=…). Its headers output goes to check_cookie, a Python Code block that "
                   "prints the headers it gets, and works out a fingerprint of the cookie (the start of its SHA-256): "
                   "it shows that the block got the real value, without showing the value.")
            p.note("To send a login's cookies with the API Request blocks after it, put them inside a Session box: a "
                   "proof of its own shows it. Here no block is inside one: the cookie only reaches check_cookie, "
                   "through login's headers output.")
            tab = open_flow(win, monkeypatch, flow)
            run = run_now(win, qtbot, tab)
            fingerprint = hashlib.sha256(session.encode()).hexdigest()[:16]
            p.shot(win, "① check_cookie printed the headers it got, and the Log shows the cookie as ***: "
                        "'Set-Cookie': '***' (the Log's lines are written out above). ② Yet check_cookie got the real "
                        f"value: its output is its fingerprint, {fingerprint}, the fingerprint of the cookie the "
                        "server sent.",
                   highlight=[(win.log.text, "login answered with the headers"), "check_cookie"], badges=True,
                   zoom=["login", "check_cookie"])
            p.text("What the Log says of this run (the server's port written as PORT)", run_log(win, run, api))
            log = log_text(win)
            p.check("The run succeeded", run.status == "success")
            p.check("The Log shows the headers check_cookie printed, with 'Set-Cookie': '***', and nowhere the "
                    "cookie's value",
                    re.search(r"login answered with the headers \{.*'Set-Cookie': '\*\*\*'", log)
                    and session.split(";")[0] not in log)
            p.check(f"check_cookie's output is the fingerprint of the cookie the server sent: {fingerprint}",
                    tab.scene.values.get("check_cookie", {}).get("output") == f"fingerprint {fingerprint}")

            p.step("Click login, then Data preview, and choose its headers output.")
            click(tab, "login")
            click_bottom_tab(win, win.preview)
            pick(win.preview.ports, "headers")
            headers_shown = win.preview.text.toPlainText()
            p.shot(win, "① login's headers output, in Data preview: Set-Cookie shows as ***, as a stored secret "
                        "does. ② login on the canvas: the result it shows there holds no cookie value either.",
                   highlight=[preview_lines(win), "login"], badges=True, zoom=[preview_lines(win)])
            p.check("Data preview shows login's headers with Set-Cookie as ***, and not the cookie's value",
                    json.loads(headers_shown).get("Set-Cookie") == "***" and session.split(";")[0] not in headers_shown)
            p.check("The canvas shows no cookie value under login",
                    all(session.split(";")[0] not in str(v) for v in tab.scene.values.get("login", {}).values()))

            # --- 9. Dark --------------------------------------------------------------------------------------------
            p.step("Choose Tools > Settings…, set Theme to Dark and press Save. Then click the tab shop-orders.yaml "
                   "and shop_orders.")
            set_theme(p, win, "Dark", shoot=False)
            win.tabs.setCurrentIndex(win.tabs.indexOf(shop_tab))
            QTest.qWait(100)
            click(shop_tab, "shop_orders")
            scroll_to(win, "Connection")
            note = said_under(win, "From the connection shop")
            p.shot(win, "In Dark: ① shop_orders on the dark canvas; ② its Properties, the connection's values in grey "
                        "under Connection, and the tables (close-up).",
                   highlight=["shop_orders", win.properties], badges=True,
                   zoom=[properties_row(win, "Connection"), headers_box(win)])
            p.shot(win, "Close-up of ① shop_orders in Dark: its name in light text, the names of its inputs and "
                        "outputs in grey, its icon white on the Servers olive.",
                   highlight=["shop_orders"], badges=True, zoom=["shop_orders"])
            p.check("The editor is dark", QApplication.palette().window().color().lightness() < 128)
            off = not_in_theme(win.properties, dark=True)
            p.check("Every part of Properties has a dark background, its text standing out from it"
                    + (f" (not: {'; '.join(off)})" if off else ""), off == [])
            hint = contrast(note.palette().color(QPalette.PlaceholderText), note.palette().color(QPalette.Window))
            p.check(f"The connection's values, in grey, stand out from the background: {hint:.1f} to 1 (3 to 1 or "
                    "more, the bar for grey hints)", hint >= HINT)
            texts = block_texts(shop_tab, "shop_orders")
            p.check(f"On the canvas, shop_orders' name stands out from what is behind it: {texts['name']:.1f} to 1 "
                    "(4.5 to 1 or more, the bar for text)", texts["name"] >= TEXT)
            p.check(f"The names of its outputs response, status and headers, in grey, stand out from what is behind "
                    f"them: {texts['outputs']:.1f} to 1 (3 to 1 or more, the bar for grey hints)",
                    texts["outputs"] >= HINT)
            p.note(f"The result under the block is written in the green every block's result is written in when it "
                   f"succeeded, which this change does not touch: in Dark it stands out {texts['result']:.1f} to 1 "
                   "from the block behind it" + (", under the 4.5 to 1 asked of text." if texts["result"] < TEXT
                                                 else "."))

            p.step("Set Theme back to System (Tools > Settings…) and press Save.")
            set_theme(p, win, "System", shoot=False)
            p.check("The Theme is System again, and the editor light",
                    theme.chosen(win.settings) == "System"
                    and QApplication.palette().window().color().lightness() >= 128)
            p.note("Not part of this change, as agreed with you: Postman's JavaScript pre-request and test scripts (a "
                   "Python Code block does that), importing Postman collections or environments, OAuth 2.0 logins "
                   "in a browser, WebSockets, and paging (a Loop does that).")
        finally:  # no run left going; the Theme and the application's look as the proof found them
            for tab in win.tabs_list():
                for run in tab.runs:
                    if run.running:
                        run.proc.kill()
                        run.proc.waitForFinished(5000)
            win.settings.setValue("theme", "System")
            theme.apply("System")
            if app.style().name() != as_found[0]:
                QApplication.setStyle(as_found[0])
            QApplication.setPalette(as_found[1])
            QToolTip.setPalette(as_found[2])
            QApplication.processEvents()
            clean(win)
