"""Proof: Import from cURL. Paste a curl command, from an API's documentation or a browser, into the code box at the
top of an API Request's Properties, and Fill in sets the block's request from it: its secrets kept as stored secrets,
a request already there replaced only once you approve, in one Undo step."""

import base64
import copy
import json
import textwrap
import time

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QComboBox, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QTableWidget, QToolBar)

from proofs.conftest import choose, menu_item
from taskloom.editor.canvas import NODE_W
from taskloom.editor.panels import CODE_COLORS
from tests.test_code_editor import color
from tests.test_curl_import import Site, above_it
from tests.test_editor import add_from_palette, drag_port_to_port
from tests.test_paths_editor_editing import field, settle

REAL_INFORMATION = QMessageBox.information  # (the win fixture answers message boxes itself: here you see them)
REAL_WARNING = QMessageBox.warning
ASKED = ("add also an API block that is pretty much Postman in a block … Also in a clearly visible section of its "
         "Properties panel include a \"import from cURL String\" where a user can just paste a cURL sample it has "
         "seen on an example documentation and it automatically fills the available information from the String into "
         "the relevant fields of the Properties of the block. … Import from cURL shouldn't be a simply plain text "
         "area but rather a code block like the python or SQL blocks … if it is going to overwrite over what is "
         "already filled show a confirmation dialog and let the user approve or cancel")
# As an API's documentation writes it, for bash: a backslash at a line's end goes on on the next line.
DOCUMENTATION = ("curl -X POST https://api.example.com/v1/orders?expand=items \\\n"
                 "  -H 'Content-Type: application/json' \\\n"
                 "  -H 'X-API-Key: abc123secret' \\\n"
                 "  -u reports:s3cret \\\n"
                 "  -d '{\"region\": \"north\", \"day\": \"2026-11-03\"}' \\\n"
                 "  --compressed --http2 --frobnicate")
# The same request for Windows' cmd, written as a browser's Copy as cURL (cmd) writes its commands: each value in
# ^"…^", a ^ before what cmd would read itself ({, }, the quotes inside), a ^ at each line's end.
CMD = ('curl ^"https://api.example.com/v1/orders?expand=items^" ^\n'
       '  -H ^"Content-Type: application/json^" ^\n'
       '  -H ^"X-API-Key: abc123secret^" ^\n'
       '  -u ^"reports:s3cret^" ^\n'
       '  --data-raw ^"^{^\\^"region^\\^": ^\\^"north^\\^", ^\\^"day^\\^": ^\\^"2026-11-03^\\^"^}^" ^\n'
       '  --compressed')
ANOTHER = "curl https://api.example.com/v1/me -H 'Accept: application/json'"
NOT_CURL = "wget --header='Accept: application/json' https://api.example.com/v1/orders"
API_KEY, PASSWORD = "abc123secret", "s3cret"
KEY_NAME, PASSWORD_NAME = "api_example_com_x_api_key", "api_example_com_password"  # the names Fill in suggests
FILLED = ["Method: POST", "URL: https://api.example.com/v1/orders", "Params (the URL's query): expand",
          "Headers: Content-Type, X-API-Key", "Auth: Basic, user name reports", "Body: JSON"]


# --- the editor, as you see it --------------------------------------------------------------------------------------

def section(win) -> QGroupBox:
    (box,) = [b for b in win.properties.widget().findChildren(QGroupBox) if b.title() == "Import from cURL"]
    return box


def command_box(win) -> QPlainTextEdit:
    return section(win).findChild(QPlainTextEdit)


def fill_button(win) -> QPushButton:
    (button,) = [b for b in section(win).findChildren(QPushButton) if b.text() == "Fill in"]
    return button


def table(win, label: str) -> QTableWidget:
    """The table of the setting `label` in Properties (Params, Headers)."""
    for form in win.properties.widget().findChildren(QFormLayout):
        for row in range(form.rowCount()):
            name, value = form.itemAt(row, QFormLayout.LabelRole), form.itemAt(row, QFormLayout.FieldRole)
            if name and value and isinstance(name.widget(), QLabel) and name.widget().text() == label:
                return value.widget().findChild(QTableWidget)
    raise AssertionError(f"no table {label!r} in Properties")


def content_box(win) -> QPlainTextEdit | None:
    """The body's Content box (shown for a JSON, text or XML body): the text box that is not Import from cURL's."""
    boxes = [e for e in win.properties.widget().findChildren(QPlainTextEdit) if not section(win).isAncestorOf(e)]
    return boxes[0] if boxes else None


def shown(win) -> dict:
    """What Properties shows of the request: each setting's text (a list: its choice), a table's rows as (ticked,
    name, value)."""
    seen = {}
    for label in ("Method", "URL", "Auth", "User name", "Stored secret (name)", "Body"):
        try:
            widget = field(win, label)
        except AssertionError:  # (not shown: it does not apply, e.g. User name with no Auth)
            continue
        seen[label] = widget.currentText() if isinstance(widget, QComboBox) else widget.text()
    for label in ("Params", "Headers"):
        rows = table(win, label)
        seen[label] = [(rows.item(r, 0).checkState() == Qt.Checked, rows.item(r, 1).text(), rows.item(r, 2).text())
                       for r in range(rows.rowCount())]
    if content_box(win) is not None:
        seen["Content"] = content_box(win).toPlainText()
    return seen


def config(tab, block: str) -> dict:
    return copy.deepcopy(tab.doc.blocks[block].get("config") or {})


def scroll_to(win, label: str):
    """Properties scrolled so that the setting `label` is at its top (once what it showed before is gone: Properties
    is made again after each change)."""
    QTest.qWait(50)
    widget = field(win, label)
    win.properties.verticalScrollBar().setValue(widget.mapTo(win.properties.widget(), QPoint(0, 0)).y() - 8)
    settle()


def message_label(box: QMessageBox) -> QLabel:
    return box.findChild(QLabel, "qt_msgbox_label")


def lines_of(box: QMessageBox, first: str, last: str | None = None) -> QRect:
    """Where a message box shows its text's lines from the one that starts with `first` up to `last` (None: to its
    end), in a screenshot of the box (its text wraps as the box draws it)."""
    label = message_label(box)
    text, area = label.text(), label.contentsRect()
    start = text.index(first)
    end = text.index(last, start) if last else len(text)

    def height(part: str) -> int:
        return label.fontMetrics().boundingRect(QRect(0, 0, area.width(), 100_000), Qt.TextWordWrap, part).height()

    top = area.top() + height(text[:end]) - height(text[start:end])
    return QRect(area.left(), top, area.width(), height(text[start:end])).translated(label.mapTo(box, QPoint(0, 0)))


def in_turn(open_, *looks):
    """open_() opens windows one after the other, each waiting for an answer (a name to type, a question, a message);
    looks[i](window) is called on the i-th once it shows, to shoot it and answer it as you would. Any other window
    fails the proof (it is closed, so that nothing waits for it)."""
    seen, extra, errors = [], [], []

    def look():
        window = QApplication.activeModalWidget()
        if window is None or window.property("proof_seen"):
            return
        window.setProperty("proof_seen", True)  # (looked at once: a screenshot lets the clock tick here again)
        if len(seen) == len(looks):
            extra.append(f"{window.windowTitle()}: {getattr(window, 'text', lambda: '')()}")
            window.close()
            return
        seen.append(window.windowTitle())
        try:
            looks[len(seen) - 1](window)
        except BaseException as e:  # raised again below
            errors.append(e)
        if window.isVisible():
            window.close()

    timer = QTimer(interval=50)
    timer.timeout.connect(look)
    timer.start()
    try:
        open_()
        deadline = time.monotonic() + 10
        while len(seen) < len(looks) and time.monotonic() < deadline:
            QTest.qWait(50)
        QTest.qWait(200)  # (one more, a moment later, is noticed too)
    finally:
        timer.stop()
    if errors:
        raise errors[0]
    assert extra == [], f"windows that were not expected: {extra}"
    assert len(seen) == len(looks), f"{len(looks)} windows expected, {len(seen)} shown: {seen}"


def press_fill_in(win):
    QTest.mouseClick(fill_button(win), Qt.LeftButton)


def click(box: QMessageBox, text: str):
    (button,) = [b for b in box.buttons() if b.text().replace("&", "") == text]
    QTest.mouseClick(button, Qt.LeftButton)


def keep_the_name(dialog: QInputDialog):
    """Press OK in the box asking a secret's name: the name it suggests is kept."""
    QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok), Qt.LeftButton)


def paste(win, text: str):
    """Put `text` on the clipboard, click into the Import from cURL code box, select all in it and paste (Ctrl+V)."""
    QApplication.clipboard().setText(text)
    box = command_box(win)
    QTest.mouseClick(box.viewport(), Qt.LeftButton)
    box.selectAll()
    QTest.keyClick(box, Qt.Key_V, Qt.ControlModifier)
    settle()


def add_api_request(win, tab, name: str, x: int, y: int) -> str:
    """Double-click API Request in the Blocks list, select it on the canvas and type its name in Properties. The canvas
    is first scrolled so that the block lands at (x, y), clear of the others."""
    settle()  # (a new flow's canvas centres itself a moment after it opens)
    n = len(tab.doc.blocks) + len(tab.doc.notes)  # (a block added by double-click goes to the canvas' middle, a little
    tab.view.centerOn(QPointF(x + NODE_W / 2 - n % 5 * 24, y + 40 - n % 5 * 24))  # lower right for each one there)
    settle()
    before = set(tab.doc.blocks)
    add_from_palette(win, "API Request")
    settle()
    (added,) = set(tab.doc.blocks) - before
    tab.scene.clearSelection()
    tab.scene.nodes[added].setSelected(True)
    settle()
    edit = field(win, "Name")
    edit.selectAll()
    QTest.keyClicks(edit, name)
    QTest.keyClick(edit, Qt.Key_Return)
    settle()
    return name


def menu_of(win, title: str):
    return next(a.menu() for a in win.menuBar().actions() if a.text() == title)


# --- the proof ------------------------------------------------------------------------------------------------------

def test_import_from_curl_fills_in_the_request(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Import from cURL: paste a curl command, and Fill in sets the request", ASKED)
    p.idea("At the top of an API Request's Properties, just under its name, there is now a section Import from cURL, "
           "always open. Paste a curl command into its code box, as an API's documentation or a browser's Copy as "
           "cURL gives it, and press Fill in: the block's method, URL, Params, Headers, Auth and Body are set to the "
           "request the command sends.")
    p.idea("The password of -u, a Bearer token, and the value of each header named like a secret (Authorization, or "
           "with token, key, secret or password in its name, as X-API-Key) are not written into the flow: Fill in "
           "asks a name for each, suggesting one, and keeps it as a stored secret under that name; the block refers "
           "to it by that name. Anything else in the command, such as a key in the URL's query or in the data, is "
           "filled in as written.")
    p.idea("When the block has a request already, Fill in first says what it will replace, and changes nothing until "
           "you approve. One Edit > Undo takes the whole import back. At the end it says what it filled in, and lists "
           "every part of the command it could not use: nothing is dropped silently.")
    p.why_simplest("Fill in reads the command as the shell would, bash or Windows cmd, and sets the block's ordinary "
                   "settings, the ones you would otherwise type. Nothing of the import is left over afterwards: the "
                   "block is an API Request like any other, which you read and change in Properties, and it runs as "
                   "any other does.")
    p.why_simplest("It reads the curl options examples use: the method (-X, or POST when there is data), the URL and "
                   "its query, headers (-H, and -A, -e, -b), Basic auth (-u), a Bearer token, data and forms (-d, "
                   "--data-raw, --json, --data-urlencode, -F), -G (the data in the query), -I (HEAD), -L (follow "
                   "redirects), -k (don't verify certificates) and -m (the timeout). Anything else is listed at the "
                   "end, as written. Only --compressed needs nothing: it lets curl ask for a compressed answer, and "
                   "the block gets the same answer uncompressed.")
    p.alternative("Run curl itself on the command",
                  "It would send the request, with its secrets, only to read it: pasting an example must call no "
                  "one's API. And Taskloom does not need curl: the block sends its requests itself.")
    p.alternative("A plain text box to paste into",
                  "A pasted command is code: long, over several lines, with quotes inside quotes. As we agreed, it "
                  "goes into a code box like the Python and SQL ones, which colours curl's options, its quoted "
                  "strings and the URL, so you see where each part begins and ends.")
    p.alternative("Fill in only what is empty, keeping the rest of the request",
                  "The old request's body or auth would go with the new URL: a request nobody wrote. Fill in "
                  "replaces the whole request instead (what the command does not give is left empty) and, as we "
                  "agreed, asks you first, saying what it will replace.")
    p.alternative("Write the command's password and keys into the flow as they are",
                  "A flow is a file you share and keep in Git: Fill in keeps the secrets it recognises out of it. A "
                  "secret is kept where Taskloom keeps all of them: the system's credential store, or where there is "
                  "none, a file only you can read.")
    p.fits("The code box is the editor's own code box, the Python and SQL blocks' one, with the same line numbers: "
           "curl's options take the colour of their keywords, its quoted strings that of their strings, and the URL "
           "one of its own, in the light theme as in the dark one. It wraps long lines, as a pasted command often "
           "has one.")
    p.fits("The secrets are stored secrets, as those that the command taskloom secret set NAME and Tools > "
           "Connections store. A header's value written secret:NAME, and Auth's Stored secret (name), are how you "
           "would refer to one by hand.")
    p.fits("The import is one change of the flow, which Undo and Redo treat as any other. It goes the other way from "
           "the block's Copy as cURL (right-click it), which writes its request as a curl command, each stored secret "
           "shown by its name only.")
    flow = tmp_path / "orders.yaml"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(flow), ""))
    file_menu = menu_of(win, "&File")

    # --- 1. the section, and the command pasted -------------------------------------------------------------------
    p.step("Make a new flow (File > New flow), double-click API Request in the Blocks list, and name it orders in its "
           "Properties.")
    next(a for a in file_menu.actions() if a.text() == "New flow").trigger()
    tab = win.current
    orders = add_api_request(win, tab, "orders", 0, 0)
    box = section(win)
    p.check("orders is a new API Request: nothing is set in it",
            tab.doc.blocks[orders]["type"] == "servers.api_request" and not tab.doc.blocks[orders].get("config"))
    p.check("Its Properties show Import from cURL, open (it cannot be folded away), with a code box and Fill in",
            box.isVisible() and not box.isCheckable() and command_box(win).isVisible() and fill_button(win).isVisible())
    p.check("It is above all of the block's settings: only the block's name is above it",
            above_it(win, box, orders) == [])
    p.shot(win, "① Import from cURL, at the top of orders' Properties, just under its name and above its settings: a "
                "line saying what it is for, the code box to paste into, and ② Fill in.",
           highlight=[box, fill_button(win)], badges=True, zoom=box)

    p.step("Copy a curl command from an API's documentation and paste it into the code box (Ctrl+V). It is written "
           "for bash, over six lines, each but the last ending in a backslash.")
    p.note("--http2 is a curl option the block has no setting for: it does not choose the HTTP version. --frobnicate "
           "is no curl option at all. Both are there to show what Fill in does with what it cannot use.")
    paste(win, DOCUMENTATION)
    code = command_box(win)
    option, string, url = (color(code, "-X"), color(code, "'X-API-Key: abc123secret'"),
                           color(code, "https://api.example.com/v1/orders?expand=items"))
    p.check("The code box holds the command as pasted", code.toPlainText() == DOCUMENTATION)
    p.check("Its options (-X, -H, -u, -d, --compressed) are in one colour, its quoted strings in another, the URL in "
            "a third, and none in the plain text's",
            {color(code, "  -H 'Content-Type", "-H"), color(code, "-u"), color(code, "-d"), color(code, "--compressed")}
            == {option} and color(code, """'{"region": "north", "day": "2026-11-03"}'""") == string
            and len({option, string, url, code.palette().text().color().name()}) == 4)
    p.check("These are the code boxes' colours: the Python and SQL boxes' colours of keywords and of strings, and the "
            "URLs' own", (option, string, url) in [tuple(CODE_COLORS[k][i] for k in ("keyword", "string", "url"))
                                                    for i in (0, 1)])
    p.shot(win, "The command in the code box, coloured as the Python and SQL boxes colour code: the options (-X, -H, "
                "-u, -d, --compressed…) in blue, the quoted strings in green, the URL in purple; what is left, such as "
                "POST and reports:s3cret, in the text's own colour. Each line has its number on the left.",
           highlight=[code], zoom=code)

    # --- 2. Fill in -----------------------------------------------------------------------------------------------
    p.step("Press Fill in. It asks a name for each secret in the command: first for the password of -u, then for the "
           "API key. Keep each name it suggests: press OK.")
    p.note("This proof never touches your own credential store: the stored secrets go to a Taskloom folder made for "
           "the proof.")
    asked, said = [], []

    def name_it(what: str):
        def look(dialog):
            assert isinstance(dialog, QInputDialog), f"not asking a name: {dialog.windowTitle()}"
            asked.append((dialog.labelText(), dialog.textValue()))
            p.shot(dialog, f"It says that {what} goes into a stored secret, never into the flow, and suggests a name "
                           f"for it, made of the URL's host and what it is: {dialog.textValue()}.",
                   highlight=[dialog.findChild(QLineEdit)])
            keep_the_name(dialog)
        return look

    def read_message(box):
        said.append(box.text())
        p.shot(box, "① What it filled in, and the two secrets it stored, under the names you kept. ② What it could not "
                    "use, as written: --http2 and --frobnicate.",
               highlight=[lines_of(box, "Filled in", "\n\nNot used"), lines_of(box, "Not used")], badges=True)
        click(box, "OK")

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "information", REAL_INFORMATION)
        in_turn(lambda: press_fill_in(win), name_it("the password of -u reports"),
                name_it("the value of the header X-API-Key"), read_message)
    p.check("It asked two names: for the password of -u, suggesting api_example_com_password, then for the API key, "
            "suggesting api_example_com_x_api_key",
            [suggested for _, suggested in asked] == [PASSWORD_NAME, KEY_NAME]
            and "password of -u reports" in asked[0][0] and "X-API-Key" in asked[1][0])
    message = said[0]
    p.check("It says what it filled in: the method, the URL, the Params, the headers, Basic auth and the JSON body",
            all(line in message.splitlines() for line in FILLED))
    p.check("Under “Not used” it lists --http2 and --frobnicate, and nothing else",
            message.split("\n\nNot used")[1].splitlines()[1:] == ["--http2", "--frobnicate"])
    p.check("Both secrets are stored, under the names it suggested",
            win.home.get_secret(PASSWORD_NAME) == PASSWORD and win.home.get_secret(KEY_NAME) == API_KEY)
    filled = config(tab, orders)

    p.step("Save the flow as orders.yaml (File > Save as…). Then look at what orders has now, in its Properties and in "
           "the file.")
    next(a for a in file_menu.actions() if a.text() == "Save as…").trigger()
    saved = flow.read_text(encoding="utf-8")
    lines = [line.strip() for line in saved.splitlines()]  # (orders' settings, from Auth up to Body)
    shown_lines = saved.splitlines()[lines.index("auth: Basic"):lines.index("body: JSON")]
    p.text("orders.yaml: orders' auth and headers, as saved", textwrap.dedent("\n".join(shown_lines)))
    now = shown(win)
    scroll_to(win, "Method")
    request = [field(win, "Method"), field(win, "URL"), table(win, "Params")]
    p.shot(win, "orders, filled in: ① Method POST; ② the URL, with ③ its query in Params, expand = items, ticked: "
                "it is sent.", highlight=request, badges=True, zoom=request)
    p.check("Neither secret is in the flow file: not the API key abc123secret, not the password s3cret",
            API_KEY not in saved and PASSWORD not in saved)
    p.check("The file names them instead: secret:api_example_com_x_api_key, and api_example_com_password",
            f"secret:{KEY_NAME}" in saved and PASSWORD_NAME in saved)
    p.check("Method is POST, the URL https://api.example.com/v1/orders?expand=items, and Params has expand = items, "
            "ticked", now["Method"] == "POST" and now["URL"] == "https://api.example.com/v1/orders?expand=items"
            and now["Params"] == [(True, "expand", "items")])
    scroll_to(win, "Method")
    auth = [field(win, "Auth"), field(win, "User name"), field(win, "Stored secret (name)")]
    headers = table(win, "Headers")
    p.shot(win, "① Auth is Basic: user name reports, its password the stored secret api_example_com_password. ② "
                "Headers has Content-Type, and X-API-Key, whose value secret:api_example_com_x_api_key names the "
                "stored secret: the key itself is not in the block.",
           highlight=[auth, headers], badges=True, zoom=[auth, headers])
    p.check("Auth is Basic, user name reports, with the stored secret api_example_com_password",
            (now["Auth"], now["User name"], now["Stored secret (name)"]) == ("Basic", "reports", PASSWORD_NAME))
    p.check("Headers has Content-Type: application/json, and X-API-Key: secret:api_example_com_x_api_key, the stored "
            "key's name", now["Headers"] == [(True, "Content-Type", "application/json"),
                                             (True, "X-API-Key", f"secret:{KEY_NAME}")])
    scroll_to(win, "Method")
    body = [field(win, "Body"), content_box(win)]
    p.shot(win, "① Body is JSON, and ② Content holds the data of -d, pretty-printed. Its braces are written twice, "
                "{{ and }}: in a setting, {name} fills in a placeholder, and a brace written twice is sent as one.",
           highlight=body, badges=True, zoom=body)
    p.check("Body is JSON, pretty-printed (a line for each field, indented), its braces written {{ and }}",
            now["Body"] == "JSON" and now["Content"] == '{{\n  "region": "north",\n  "day": "2026-11-03"\n}}')

    # --- 3. a request already there: Approve or Cancel; one Undo ------------------------------------------------------
    p.step("Paste another command into orders' code box, now that orders is filled in, and press Fill in. A box asks "
           "first: press Cancel.")
    p.text("The other command", ANOTHER)
    paste(win, ANOTHER)
    questions = []

    def cancel(box):
        questions.append((box.text(), [b.text().replace("&", "") for b in box.buttons()]))
        p.shot(box, "① It says that the request of orders is filled in already, and what Fill in would replace with "
                    "what the command sends: its Method, URL, Params, Auth, User name, Stored secret (name), Headers, "
                    "Body and Content. ② Approve, or Cancel: press Cancel.",
               highlight=[message_label(box), box.buttons()], badges=True)
        click(box, "Cancel")

    in_turn(lambda: press_fill_in(win), cancel)
    question, buttons = questions[0]
    p.check("The box names what it would replace: the Method, URL, Params, Auth, User name, Stored secret (name), "
            "Headers, Body and Content", all(word in question for word in (
                "Method", "URL", "Params", "Auth", "User name", "Stored secret (name)", "Headers", "Body", "Content")))
    p.check("Its buttons are Approve and Cancel", sorted(buttons) == ["Approve", "Cancel"])
    p.check("Cancel changed nothing: orders' request is as Fill in left it", config(tab, orders) == filled)
    scroll_to(win, "Name")
    kept = [field(win, "Method"), field(win, "URL")]
    p.shot(win, "After Cancel: the other command is still in the code box, and ① orders keeps the request it had: "
                "POST, the URL with ?expand=items.", highlight=[kept], badges=True, zoom=[command_box(win), kept])

    p.step("Press Fill in again; this time press Approve.")
    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "information", REAL_INFORMATION)
        in_turn(lambda: press_fill_in(win), lambda box: click(box, "Approve"), lambda box: click(box, "OK"))
    now = shown(win)
    p.check("Approve replaced the request: GET https://api.example.com/v1/me with Accept: application/json, and no "
            "Params, Auth or Body",
            (now["Method"], now["URL"], now["Params"], now["Headers"], now["Auth"], now["Body"])
            == ("GET", "https://api.example.com/v1/me", [], [(True, "Accept", "application/json")], "none", "none"))
    scroll_to(win, "Method")
    replaced = [field(win, "Method"), field(win, "URL"), field(win, "Auth"), table(win, "Headers"), field(win, "Body")]
    p.shot(win, "After Approve, ① orders has the other command's request: GET https://api.example.com/v1/me with the "
                "header Accept. The Params, the Basic auth, X-API-Key and the JSON body are gone.",
           highlight=[replaced], badges=True, zoom=replaced)

    p.step("Choose Edit > Undo, once.")
    p.note("Undo puts back the block's request. A secret that an import stored stays stored (the other command had "
           "none): the request it brings back needs it.")

    def undo(menu):
        p.shot(menu, "The Edit menu: Undo.", over=win, highlight=[menu_item(menu, "Undo")])
        choose(menu, "Undo")

    p.while_open(lambda: menu_of(win, "&Edit").exec(win.mapToGlobal(QPoint(60, 30))), undo)
    settle()
    scroll_to(win, "Method")
    back = [field(win, "Method"), field(win, "URL"), table(win, "Params"), field(win, "Auth"), table(win, "Headers")]
    p.shot(win, "After one Undo, ① orders has its first request again: POST, the URL with expand = items, Basic auth "
                "and the two headers.", highlight=[back], badges=True, zoom=back)
    p.check("One Undo brought back the request as it was before: the method, URL, Params, auth, headers and JSON body",
            config(tab, orders) == filled and shown(win)["Method"] == "POST")

    # --- 4. as Windows cmd writes it --------------------------------------------------------------------------------
    p.step("Add another API Request and name it orders_cmd. Paste into its code box the same request written for "
           "Windows' cmd, as a browser's Copy as cURL (cmd) writes it, and press Fill in, keeping the names it "
           "suggests.")
    p.note("In cmd a ^ at a line's end goes on on the next line, as a backslash does in bash, and a ^ before a "
           "character takes it as it is: a browser writes each value in ^\"…^\", and a quote inside the JSON as "
           "^\\^\". The command has no -X POST: curl sends a POST when there is data, and Fill in sets POST for it.")
    orders_cmd = add_api_request(win, tab, "orders_cmd", 0, 240)
    paste(win, CMD)
    code = command_box(win)
    p.check("orders_cmd's code box holds the cmd command as pasted", code.toPlainText() == CMD)
    p.shot(win, "① The same request, written for cmd, in orders_cmd's code box.", highlight=[code], badges=True,
           zoom=code)
    cmd_asked, cmd_said = [], []

    def keep(dialog):
        cmd_asked.append(dialog.textValue())
        keep_the_name(dialog)

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "information", REAL_INFORMATION)
        in_turn(lambda: press_fill_in(win), keep, keep, lambda box: (cmd_said.append(box.text()), click(box, "OK")))
    p.check("It asked the same two names, suggesting the same ones: each of those stored secrets holds that same "
            "value already", cmd_asked == [PASSWORD_NAME, KEY_NAME])
    p.check("orders_cmd is filled in exactly as orders: the same method, URL, Params, headers, auth and JSON body",
            config(tab, orders_cmd) == config(tab, orders) == filled)
    p.check("It says it filled in the same, and lists nothing as not used (the command has no --http2 or "
            "--frobnicate)", cmd_said[0] == message.split("\n\nNot used")[0])
    scroll_to(win, "Method")
    same = [field(win, "Method"), field(win, "URL"), table(win, "Params"), field(win, "Auth"), table(win, "Headers")]
    p.shot(win, "① orders_cmd, filled in from the cmd command, has the same request as orders: POST, the URL with "
                "expand = items, Basic auth with the same stored secret, the same headers (and, below, the same JSON "
                "body).", highlight=[same], badges=True, zoom=same)

    # --- 5. not a curl command -----------------------------------------------------------------------------------
    p.step("Paste a command that is not curl, a wget one, into orders_cmd's code box and press Fill in.")
    p.text("The wget command", NOT_CURL)
    paste(win, NOT_CURL)
    before = config(tab, orders_cmd)
    warned = []

    def look_at_warning(box):
        warned.append(box.text())
        p.shot(box, "It refuses it: this is not a curl command, as it starts with wget. It says what to paste instead, "
                    "with an example.", highlight=[message_label(box)])
        click(box, "OK")

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "warning", REAL_WARNING)
        in_turn(lambda: press_fill_in(win), look_at_warning)
    p.check("It said “This is not a curl command: it starts with 'wget', not curl”, and asked nothing",
            warned[0].startswith("This is not a curl command: it starts with 'wget', not curl."))
    p.check("Nothing changed: orders_cmd's request is as it was", config(tab, orders_cmd) == before)

    # --- 6. it runs -----------------------------------------------------------------------------------------------
    p.step("Connect orders after start. In its URL put the address of a web server on this computer in place of "
           "https://api.example.com, and press Enter.")
    p.note("The proof starts that web server itself, on this computer: a proof never calls the internet. It notes "
           "each request it gets, and answers ok.")
    with Site() as site:
        tab.scene.clearSelection()
        tab.scene.nodes[orders].setSelected(True)
        tab.view.centerOn(QPointF(-40, 120))
        settle()
        drag_port_to_port(tab, "start", "start", orders, "after")
        settle()
        p.shot(win, "① orders is connected after start: it no longer says it is off.", highlight=[["start", orders]],
               badges=True, zoom=["start", orders])
        address = field(win, "URL")
        address.selectAll()
        QTest.keyClicks(address, f"{site.base}/v1/orders?expand=items")
        QTest.keyClick(address, Qt.Key_Return)
        settle()
        now = shown(win)
        scroll_to(win, "Method")
        p.shot(win, "① Its URL is the web server's, and ② Params still has expand = items.",
               highlight=[field(win, "URL"), table(win, "Params")], badges=True,
               zoom=[field(win, "URL"), table(win, "Params")])
        p.check("orders runs after start, and its URL is the web server's, with expand = items still in Params",
                ("start", "start", orders, "after") in tab.doc.edges()
                and now["URL"] == f"{site.base}/v1/orders?expand=items"
                and now["Params"] == [(True, "expand", "items")])

        p.step("Press ▶ Run now.")
        run_now = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action)
        QTest.mouseClick(run_now, Qt.LeftButton)
        qtbot.waitUntil(lambda: len(tab.runs) == 1 and not tab.running and tab.runs[0].status is not None,
                        timeout=60_000)
        got = site.requests()
    p.shot(win, "① orders ran after start, and succeeded: the server answered 200, with the text ok.",
           highlight=[["start", orders]], badges=True, zoom=["start", orders])
    p.check("The run succeeded: orders shows success", tab.scene.nodes[orders].status == "success")
    sent_and_answer = [(win.log.text, f"POST {site.base}/"), (win.log.text, "200 OK: ")]
    p.shot(win, "① The Log shows the request orders sent, POST to the web server's address with ?expand=items, then "
                "the server's answer, 200 OK.", highlight=[sent_and_answer], badges=True, zoom=sent_and_answer)

    p.step("Look at what the web server got.")
    p.check("The web server got one request", len(got) == 1)
    sent = got[0]
    p.text("What the web server got (in HTTP, a header's name is read whatever its capitals)", "\n".join(
        [f"{sent.method} {sent.path}?{'&'.join(f'{k}={v[0]}' for k, v in sent.query.items())}",
         *[f"{k}: {v}" for k, v in sent.headers.items()], "", sent.body.decode("utf-8")]))
    p.check("It is a POST of /v1/orders?expand=items",
            (sent.method, sent.path, sent.query) == ("POST", "/v1/orders", {"expand": ["items"]}))
    p.check("With Content-Type: application/json, and X-API-Key: abc123secret, the stored key",
            sent.headers.get_all("Content-Type") == ["application/json"]
            and sent.headers.get_all("X-API-Key") == [API_KEY])
    p.check("With Basic auth: user reports, password s3cret, the stored password",
            sent.headers.get_all("Authorization") == ["Basic " + base64.b64encode(b"reports:s3cret").decode()])
    p.check("And the JSON body of the command: {\"region\": \"north\", \"day\": \"2026-11-03\"}",
            json.loads(sent.body) == {"region": "north", "day": "2026-11-03"})
