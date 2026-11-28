"""Proof: name/value rows changed and deleted (docs/promises/rows.yaml, AP-1..AP-5). In a name/value list of
Properties a row's name and value are changed where they are by a double-click; a row is deleted with Delete or
right-click > Delete row, and still by clearing its name; a grey hint under the list says so; each change is one undo
step and the flow file holds what the list shows; a secret: value stays hidden while its row is edited; two rows of
the same name in API Request's lists are both kept and both sent, in the list's order. How proofs are written:
README.md here."""

import yaml
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_api_request import (click_bottom_tab, open_flow, properties_row, run_only, said_under,
                                           scroll_to)
from proofs.test_proof_run_output import click
from tests.test_api_request import secret, serving
from tests.test_paths_editor_editing import flow_file, settle
from tests.test_rows import (HEADERS, LISTS, activate, add_line, as_rows, cell, everything_shown, open_cell, press,
                             save, saved_rows, select_row, setting, shown, table, under_the_list)
from tests.test_theme import in_place, strongest

ASKED = ("“Be able to edit and delete headers and params” (13 November 2026). Then, of the rows that say what must "
         "always be true of these lists (16 November 2026): “Approve all”.")
API, CHECK = "servers.api_request", "checks.check_table"
PARAMS = next(lst for lst in LISTS if lst.label == "Params")
ALLOWED = next(lst for lst in LISTS if lst.label == "Allowed values")
HINT_TEXT = "Double-click a cell to change it. Delete removes the selected row."
URL = "https://api.example.com/v1/orders"  # (nothing is sent to it: that flow is not run)

# The lists of get_orders, as the flow file has them, then after each of the six changes the proof makes.
P0 = [("page", "2"), ("limit", "100"), ("region", "north")]
H0 = [("Accept", "application/json"), ("X-Region", "north"), ("X-Trace", "nightly"), ("X-Debug", "on")]
H1 = [H0[0], ("X-Area", "north"), H0[2], H0[3]]  # X-Region renamed
H2 = [H0[0], ("X-Area", "south"), H0[2], H0[3]]  # its value changed
H3 = [H2[0], H2[1], H2[3]]  # X-Trace deleted with Delete
H4 = [H2[0], H2[1]]  # X-Debug deleted with right-click > Delete row
P5 = [P0[0], ("limit", "20"), P0[2]]  # limit's value changed
P6 = [P0[0], ("limit", "20")]  # region's name cleared
STATES = [(H0, P0), (H1, P0), (H2, P0), (H3, P0), (H4, P0), (H4, P5), (H4, P6)]
UNDONE = ["region is back in Params, third", "limit is 100 again", "X-Debug is back in Headers, last",
          "X-Trace is back in Headers, third", "X-Area's value is north again", "X-Area is named X-Region again"]
ALLOWED_0 = [("amount", "0..1000"), ("region", "north, south"), ("day", "2026-01-01..")]


def lists(win) -> tuple[list, list]:
    """What get_orders' Headers and Params show: their (name, value) rows."""
    return shown(table(win, HEADERS)), shown(table(win, PARAMS))


def hint_under(win, lst) -> QLabel:
    """The hint under the list `lst` in Properties: the text under its line to add a row."""
    hints = [w for w in under_the_list(win, table(win, lst), QLabel) if "click" in w.text()]
    assert len(hints) == 1, f"{lst.label}: {len(hints)} hints under the list"
    return hints[0]


def is_grey(win, lst) -> bool:
    """The hint under `lst` is painted fainter than the setting's name (each scrolled into view, to be painted)."""
    hint, name = hint_under(win, lst), setting(win, lst.label)[0]
    win.properties.ensureWidgetVisible(hint)
    faint = strongest(in_place(hint))
    win.properties.ensureWidgetVisible(name)
    return faint < strongest(in_place(name))


def right_press(win, view, row: int):
    """Right-click the name of the list's row `row`, as the mouse does: its menu opens just after, and waits (see
    p.while_open)."""
    point = view.viewport().mapTo(win, cell(view, row, "Name"))
    under = win.childAt(point)
    assert under is view.viewport() or view.viewport().isAncestorOf(under), f"the pointer is over {under}"
    QCursor.setPos(win.mapToGlobal(point))  # (Qt opens a right-click's menu where the pointer is)
    QTest.mouseClick(win.windowHandle(), Qt.RightButton, Qt.NoModifier, point)


def edit_menu(win):
    """Open the Edit menu of the menu bar (it waits for a choice: see p.while_open)."""
    menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Edit")
    menu.exec(win.mapToGlobal(QPoint(60, 30)))


def from_edit_menu(p, win, item: str, look=None):
    """Choose Edit > `item` (Undo, Redo), `look(menu)` first."""
    def then(menu):
        if look:
            look(menu)
        choose(menu, item)

    p.while_open(lambda: edit_menu(win), then)
    settle()


def type_in_cell(view, row: int, column: str, text: str):
    """Double-click the cell, type `text` over what it holds (nothing: Backspace) and press Enter."""
    editor = open_cell(view, row, column)
    if text:
        QTest.keyClicks(editor, text)
    else:
        QTest.keyClick(editor, Qt.Key_Backspace)
    QTest.keyClick(editor, Qt.Key_Return)
    settle()


def the_file(path, block: str, *keys) -> str:
    """The settings `keys` of `block`, as the flow file holds them."""
    config = yaml.safe_load(path.read_text(encoding="utf-8"))["blocks"][block].get("config") or {}
    return yaml.safe_dump({k: config.get(k) for k in keys}, sort_keys=False, allow_unicode=True).strip()


def test_a_row_is_changed_where_it_is_and_deleted_each_change_one_undo_step(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("A row of a name/value list is changed where it is and deleted, each change one undo step", ASKED)
    p.idea("In each name/value list of Properties, a row is changed where it is: double-click its name or its value, "
           "type, and press Enter. The row keeps its place in the list.")
    p.idea("A row is deleted by clicking it and pressing Delete (on a Mac's keyboard, Backspace too), or by "
           "right-clicking it and choosing Delete row. Clearing its name still deletes it. Under each list, a grey hint "
           f"says so: “{HINT_TEXT}”")
    p.idea("Each change is one step that Edit > Undo takes back, and Edit > Redo does again. Saved, the flow file holds "
           "the rows the list shows, in its order.")
    p.idea("The lists: API Request's Params, Headers, Form and Parts; Check Table's Column types and Allowed values; "
           "Statistics' Custom statistics. Before, a cell could be changed with a double-click, but nothing said so, "
           "and clearing a row's name was the only way to delete it.")
    p.why_simplest("The list is the same table it was, with its tick, Name and Value. What it lacked were ways in that "
                   "you already know: a double-click opens a cell, and Delete, or a right-click, deletes what is "
                   "selected. Nothing is added to the rows themselves.")
    p.why_simplest("Each change saves the list's rows as the table shows them, the way every setting of Properties is "
                   "saved: as one change of the flow. So Undo, Redo, Save and the mark of unsaved changes treat it as "
                   "they treat any setting.")
    p.why_simplest("Delete deletes a row only when the list itself has the keyboard. In a cell being edited, Delete "
                   "deletes text, as in any text box.")
    p.alternative("A delete button (×) at the end of each row",
                  "One more control on every row of every list, in a narrow panel, where it is easy to hit by mistake. "
                  "The rows you approved leave it out: Delete and a right-click work on any row and take no room.")
    p.alternative("A window of its own to edit a list's rows",
                  "A second place to edit the same rows, to keep in step with Properties, and one more window to open "
                  "and close for a one-word change.")
    p.fits("It is how deleting works elsewhere in the editor: Delete deletes the selected blocks on the canvas, and a "
           "block of your own in the Blocks list goes with Delete or a right-click.")
    p.fits("The seven lists are one kind of table in Properties, so each gets all of this at once. For API Request, "
           "the URL keeps showing the ticked Params as its query, so it follows each change and each Undo.")

    path = flow_file(tmp_path / "orders.yaml", {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 40}},
        "get_orders": {"type": API, "config": {"url": URL, "params": as_rows(P0), "headers": as_rows(H0)},
                       "ui": {"x": 260, "y": 0}},
        "check_orders": {"type": CHECK, "config": {"values": True, "allowed_values": as_rows(ALLOWED_0)},
                         "ui": {"x": 560, "y": 0}},
    }, ["start_by_hand.start -> get_orders.after", "get_orders.response -> check_orders.table"])

    p.step("Open orders.yaml (File > Open…) and click get_orders, an API Request with three Params and four Headers.")
    p.note("This flow is not run: its URL, at api.example.com, is only there to be shown.")
    tab = open_flow(win, monkeypatch, path)
    click(tab, "get_orders")
    activate(qtbot, win)
    scroll_to(win, "URL")
    both = [properties_row(win, "URL"), properties_row(win, "Params"), properties_row(win, "Headers")]
    p.shot(win, f"① Params and ② Headers, each with its rows, the line to add a row, and under it the grey hint: "
                f"“{HINT_TEXT}”",
           highlight=[hint_under(win, PARAMS), hint_under(win, HEADERS)], badges=True, zoom=both)
    p.check("Params and Headers show the rows the flow file holds, in its order (AP-3)", lists(win) == (H0, P0))
    p.check(f"① ② Under each list, the hint reads “{HINT_TEXT}” (AP-2)",
            hint_under(win, PARAMS).text() == HINT_TEXT and hint_under(win, HEADERS).text() == HINT_TEXT)
    p.check("Each hint is grey: painted fainter than the list's name, Params or Headers (AP-2)",
            is_grey(win, PARAMS) and is_grey(win, HEADERS))

    p.step("In Headers, double-click X-Region, type X-Area and press Enter.")
    view = table(win, HEADERS)
    editor = open_cell(view, 1, "Name")
    QTest.keyClicks(editor, "X-Area")
    p.shot(win, "① Before Enter: the cell opened as a text box where it is, X-Area typed over X-Region.",
           highlight=[editor], badges=True, zoom=[properties_row(win, "Headers")])
    p.check("Double-clicking X-Region opened a text box in its cell, holding what was typed (AP-2)",
            view.viewport().isAncestorOf(editor) and editor.text() == "X-Area")
    QTest.keyClick(editor, Qt.Key_Return)
    settle()
    scroll_to(win, "URL")
    p.shot(win, "① After Enter, the second row is X-Area, with its value north, where X-Region was.",
           highlight=[(table(win, HEADERS), 1)], badges=True, zoom=[properties_row(win, "Headers")])
    p.check("Headers: Accept, X-Area north, X-Trace, X-Debug: the row renamed in its place (AP-2)",
            lists(win) == STATES[1])

    p.step("Double-click north, X-Area's value, type south and press Enter.")
    type_in_cell(table(win, HEADERS), 1, "Value", "south")
    scroll_to(win, "URL")
    p.shot(win, "① X-Area's value is south now.", highlight=[table(win, HEADERS).item(1, 2)], badges=True,
           zoom=[properties_row(win, "Headers")])
    p.check("Headers: Accept, X-Area south, X-Trace, X-Debug: the value changed in its place (AP-2)",
            lists(win) == STATES[2])

    p.step("Click X-Trace, then press Delete.")
    view = table(win, HEADERS)
    select_row(view, 2)
    p.shot(win, "① X-Trace is selected.", highlight=[(view, 2)], badges=True, zoom=[properties_row(win, "Headers")])
    press(view, Qt.Key_Delete)
    scroll_to(win, "URL")
    p.shot(win, "X-Trace is gone: ① X-Debug moved up, under X-Area.", highlight=[(table(win, HEADERS), 2)],
           badges=True, zoom=[properties_row(win, "Headers")])
    p.check("Headers: Accept, X-Area, X-Debug: X-Trace deleted, the others in their order (AP-1)",
            lists(win) == STATES[3])
    p.check("get_orders is still on the canvas: Delete in the list deleted a row, not the block (AP-1)",
            "get_orders" in tab.doc.blocks and "get_orders" in tab.scene.nodes)

    p.step("Right-click X-Debug and choose Delete row.")
    view = table(win, HEADERS)
    activate(qtbot, win)

    def delete_row(menu):
        p.shot(menu, "The menu of the row X-Debug: ① Delete row.", over=win,
               highlight=[menu_item(menu, "Delete row")], badges=True, zoom=[menu, view])
        choose(menu, "Delete row")

    p.while_open(lambda: right_press(win, view, 2), delete_row)
    settle()
    activate(qtbot, win)  # (the menu had the keyboard)
    scroll_to(win, "URL")
    p.shot(win, "① Headers holds Accept and X-Area: X-Debug is gone.", highlight=[table(win, HEADERS)], badges=True,
           zoom=[properties_row(win, "Headers")])
    p.check("Headers: Accept, X-Area: X-Debug deleted by right-click > Delete row (AP-1)", lists(win) == STATES[4])

    p.step("In Params, double-click 100, limit's value, type 20 and press Enter.")
    type_in_cell(table(win, PARAMS), 1, "Value", "20")
    scroll_to(win, "URL")
    p.shot(win, "① limit is 20 now, and ② the URL, which shows the ticked Params as its query, follows: limit=20.",
           highlight=[table(win, PARAMS).item(1, 2), setting(win, "URL")[1]], badges=True,
           zoom=[properties_row(win, "URL"), properties_row(win, "Params")])
    p.check("Params: page 2, limit 20, region north; the URL ends with ?page=2&limit=20&region=north (AP-2)",
            lists(win) == STATES[5] and setting(win, "URL")[1].text() == f"{URL}?page=2&limit=20&region=north")

    p.step("In Params, double-click region, delete its name with Backspace and press Enter.")
    type_in_cell(table(win, PARAMS), 2, "Name", "")
    scroll_to(win, "URL")
    p.shot(win, "① region is gone from Params, and ② from the URL: a row whose name is cleared is deleted.",
           highlight=[table(win, PARAMS), setting(win, "URL")[1]], badges=True,
           zoom=[properties_row(win, "URL"), properties_row(win, "Params")])
    p.check("Params: page 2, limit 20: clearing region's name deleted the row; the URL ends with ?page=2&limit=20 "
            "(AP-1)", lists(win) == STATES[6] and setting(win, "URL")[1].text() == f"{URL}?page=2&limit=20")

    p.step("Choose Edit > Undo, once. Then choose File > Save.")
    from_edit_menu(p, win, "Undo", lambda menu: p.shot(menu, "The Edit menu: ① Undo.", over=win,
                                                       highlight=[menu_item(menu, "Undo")], badges=True))
    save(win)
    scroll_to(win, "URL")
    p.shot(win, "One Undo took back one change, the last: ① region north is back in Params, third, and in the URL. "
                "② Headers keeps its changes.",
           highlight=[table(win, PARAMS), table(win, HEADERS)], badges=True, zoom=both)
    p.check("After one Undo, only the last change is taken back: region is back, limit is still 20, Headers keeps its "
            "changes (AP-3)", lists(win) == STATES[5])
    p.check("Saved, the flow file holds exactly what the two lists show (AP-3)",
            saved_rows(path, HEADERS, "get_orders") == as_rows(STATES[5][0])
            and saved_rows(path, PARAMS, "get_orders") == as_rows(STATES[5][1]))

    p.step("Choose Edit > Undo five more times, looking at the lists after each.")
    for k in range(2, 7):
        from_edit_menu(p, win, "Undo")
        p.check(f"Undo {k}: {UNDONE[k - 1]}, and nothing else changed (AP-3)", lists(win) == STATES[6 - k])
    scroll_to(win, "URL")
    p.shot(win, "After six Undos, one for each change, ① Params and ② Headers are as the flow file had them when it "
                "opened.", highlight=[table(win, PARAMS), table(win, HEADERS)], badges=True, zoom=both)

    p.step("Choose Edit > Redo six times. Then choose File > Save.")
    redone = []
    for k in range(1, 7):
        from_edit_menu(p, win, "Redo")
        redone.append(lists(win) == STATES[k])
    save(win)
    scroll_to(win, "URL")
    p.shot(win, "Each Redo did one change again: ① Params is page 2, limit 20; ② Headers is Accept, X-Area south.",
           highlight=[table(win, PARAMS), table(win, HEADERS)], badges=True, zoom=both)
    p.text("What the flow file holds of get_orders' lists", the_file(path, "get_orders", "params", "headers"))
    p.check("Each of the six Redos did one change again, in the order they were made (AP-3)", all(redone))
    p.check("Saved, the flow file holds exactly what the lists show: Params page 2, limit 20; Headers Accept, X-Area "
            "south (AP-3)",
            lists(win) == STATES[6] and saved_rows(path, HEADERS, "get_orders") == as_rows(H4)
            and saved_rows(path, PARAMS, "get_orders") == as_rows(P6))

    p.step("Click check_orders, a Check Table. In its Allowed values, click region and press Delete. Then choose "
           "File > Save.")
    click(tab, "check_orders")
    activate(qtbot, win)
    view = table(win, ALLOWED)
    select_row(view, 1)
    press(view, Qt.Key_Delete)
    save(win)
    scroll_to(win, "Allowed values")
    allowed = properties_row(win, "Allowed values")
    p.shot(win, "Another kind of block, the same list: ① Allowed values holds amount and day, region is deleted; "
                "② under it, the same grey hint.",
           highlight=[table(win, ALLOWED), hint_under(win, ALLOWED)], badges=True, zoom=[allowed])
    p.check("Allowed values: amount, day: region deleted with Delete (AP-1)",
            shown(table(win, ALLOWED)) == [ALLOWED_0[0], ALLOWED_0[2]])
    p.check(f"② Under it, the grey hint “{HINT_TEXT}” (AP-2)",
            hint_under(win, ALLOWED).text() == HINT_TEXT and is_grey(win, ALLOWED))
    p.check("Saved, the flow file holds exactly what Allowed values shows (AP-3)",
            saved_rows(path, ALLOWED, "check_orders") == as_rows([ALLOWED_0[0], ALLOWED_0[2]]))
    for each in win.tabs_list():  # nothing to save when the window closes
        each.doc.dirty = False


def test_a_hidden_value_stays_hidden_while_its_row_is_edited(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("A hidden value (secret:) stays hidden while its row is edited", ASKED)
    p.idea("A header can take its value from a stored secret: its value is secret: and the secret's name, e.g. "
           "secret:orders.key. The list shows that, and only that.")
    p.idea("Double-click the value, and the text box in the cell holds secret:orders.key: the name, never the secret's "
           "value. Change the row's name, and the value stays secret:orders.key. Saved, the flow file holds the name.")
    p.why_simplest("A cell of the list holds the text the flow file holds, secret:orders.key, and the secret's value "
                   "is looked up only when the request is sent. So there is no value to hide while you edit: it is "
                   "never put in the list.")
    p.alternative("Show the secret's value in the cell as dots, to be edited there",
                  "Properties would have to read the value from the credential store each time it shows the block, "
                  "and a change typed there would have to be written back to it. A stored secret is changed with "
                  "taskloom secret set; the list only names it.")
    p.fits("It is the same secret:NAME that Import from cURL writes for a header named like a secret (X-API-Key), "
           "and that an API connection's headers take. The Log, the run history and Copy as cURL show it hidden too.")
    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    hidden = secret("key")

    p.step("Store the API's key as the stored secret orders.key, as taskloom secret set orders.key does.")
    p.note("taskloom secret set reads the key from the keyboard, which a proof cannot type into. So the proof stores "
           "it with the same code that command runs, in the tests' stand-in for the system's credential store, a "
           "file in this proof's temporary folder: never in your system's credential store. The key is made up "
           "for this proof.")
    where = win.home.set_secret("orders.key", hidden)
    assert where == "the system credential store" and win.home.get_secret("orders.key") == hidden

    rows = [("Accept", "application/json"), ("X-Api-Key", "secret:orders.key"), ("X-Trace", "nightly")]
    path = flow_file(tmp_path / "keyed-orders.yaml", {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 40}},
        "get_orders": {"type": API, "config": {"url": URL, "headers": as_rows(rows)}, "ui": {"x": 260, "y": 0}},
    }, ["start_by_hand.start -> get_orders.after"])
    p.step("Open keyed-orders.yaml (File > Open…) and click get_orders. Its Headers have X-Api-Key, whose value is "
           "secret:orders.key.")
    tab = open_flow(win, monkeypatch, path)
    click(tab, "get_orders")
    activate(qtbot, win)
    scroll_to(win, "Auth")
    p.shot(win, "① X-Api-Key's value shows as secret:orders.key: the stored secret's name.",
           highlight=[(table(win, HEADERS), 1)], badges=True, zoom=[properties_row(win, "Headers")])
    p.check("The row shows X-Api-Key secret:orders.key, and no window shows the secret's value (AP-4)",
            shown(table(win, HEADERS)) == rows and hidden not in everything_shown(win))

    p.step("Double-click secret:orders.key, look at the text box, and press Enter without changing it.")
    editor = open_cell(table(win, HEADERS), 1, "Value")
    p.shot(win, "① The text box in the cell holds secret:orders.key, the name: the value is not there to edit.",
           highlight=[editor], badges=True, zoom=[properties_row(win, "Headers")])
    p.check("While the value is edited, its text box holds secret:orders.key, and no window shows the secret's value "
            "(AP-4)", editor.text() == "secret:orders.key" and hidden not in everything_shown(win))
    QTest.keyClick(editor, Qt.Key_Return)
    settle()
    p.check("After Enter, the row is still X-Api-Key secret:orders.key, and no window shows the secret's value (AP-4)",
            shown(table(win, HEADERS)) == rows and hidden not in everything_shown(win))

    p.step("Double-click X-Api-Key, type X-Key and press Enter.")
    editor = open_cell(table(win, HEADERS), 1, "Name")
    QTest.keyClicks(editor, "X-Key")
    while_named = hidden not in everything_shown(win)
    QTest.keyClick(editor, Qt.Key_Return)
    settle()
    scroll_to(win, "Auth")
    p.shot(win, "① The row is X-Key now, its value still secret:orders.key.", highlight=[(table(win, HEADERS), 1)],
           badges=True, zoom=[properties_row(win, "Headers")])
    p.check("While its name was edited, and after, no window showed the secret's value; the row is X-Key "
            "secret:orders.key (AP-4)",
            while_named and hidden not in everything_shown(win)
            and shown(table(win, HEADERS)) == [rows[0], ("X-Key", "secret:orders.key"), rows[2]])

    p.step("Choose File > Save.")
    save(win)
    p.text("What the flow file holds of get_orders' Headers", the_file(path, "get_orders", "headers"))
    p.check("The flow file holds X-Key: secret:orders.key, the secret's name, and nowhere its value (AP-3, AP-4)",
            saved_rows(path, HEADERS, "get_orders") == as_rows([rows[0], ("X-Key", "secret:orders.key"), rows[2]])
            and hidden not in path.read_text(encoding="utf-8"))
    for each in win.tabs_list():
        each.doc.dirty = False


def test_two_rows_with_the_same_name_are_both_kept_and_both_sent(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Two rows with the same name are both kept and both sent, in the list's order", ASKED)
    p.idea("In API Request's Params, Headers, Form and Parts, two rows may have the same name: both are kept, and "
           "both are sent, in the list's order. Params region=north and region=south go as ?region=north&region=south.")
    p.idea("Two headers of the same name go as one header, their values joined in the list's order: X-Tag one and "
           "X-Tag two are sent as “X-Tag: one, two” (Cookie's values are joined by “; ”). Before, only the last one "
           "was sent.")
    p.idea("When the block's headers replace one of its API connection's, the note under Connection says so, with "
           "the value that is sent: “Header X-Tag: zero (replaced by this block's X-Tag: one, two)”. Copy as cURL "
           "writes the same.")
    p.why_simplest("Python's web library, which sends the request, takes one value per header name. HTTP says that a "
                   "header sent twice means the same as one with its values joined by commas, in order; for cookies, "
                   "by “; ”, as browsers send them. So joining them sends both, in one rule.")
    p.why_simplest("The request, the note under Connection and Copy as cURL join the values with the same rule, kept "
                   "in one place: they cannot disagree.")
    p.alternative("Refuse a second row of the same name, or warn about it",
                  "Many APIs take a name more than once (?tag=a&tag=b, a header with a list of values). You approved "
                  "that both are kept and sent.")
    p.alternative("Keep only the last row of a name, as headers were before",
                  "The other row would be dropped without a word: the list would show two rows and send one.")
    p.fits("A connection's header still gives way to the block's header of the same name, whatever its case: now to "
           "all the block's rows of that name. The list stays the one table, with its tick: an unticked row is not "
           "sent.")
    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)  # (the runs the editor starts inherit this process's)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    with serving() as api:
        connections = win.home.root / "connections.yaml"
        connections.write_text(yaml.safe_dump({"shop": {
            "kind": "api", "base_url": f"{api.base}/v1", "headers": as_rows([("X-Tag", "zero")])}}), encoding="utf-8")
        p.note("The web API in this proof is a small web server the proof starts on this computer, at 127.0.0.1 and a "
               "free port: nothing goes to the internet. The connection shop points at it. Tools > Connections saves "
               "a connection in connections.yaml, in the Taskloom folder (the API Request proof shows it); here the "
               "proof writes it there, in its own temporary Taskloom folder.")
        p.text("connections.yaml (the server's port written as PORT)",
               connections.read_text(encoding="utf-8").replace(api.base, "http://127.0.0.1:PORT").strip())
        params = [("region", "north"), ("region", "south")]
        path = flow_file(tmp_path / "shop-orders.yaml", {
            "start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 40}},
            "send_orders": {"type": API, "config": {"connection": "shop", "url": "/orders", "params": as_rows(params),
                                                    "headers": as_rows([("X-Tag", "one")])}, "ui": {"x": 260, "y": 0}},
        }, ["start_by_hand.start -> send_orders.after"])

        p.step("Open shop-orders.yaml (File > Open…) and click send_orders, an API Request through the connection "
               "shop. Its Params have two rows named region, north and south; its Headers have X-Tag: one.")
        tab = open_flow(win, monkeypatch, path)
        click(tab, "send_orders")
        activate(qtbot, win)
        scroll_to(win, "Connection")
        p.shot(win, "① Params keeps both rows named region, and ② the URL shows both, in the list's order: "
                    "?region=north&region=south.",
               highlight=[table(win, PARAMS), setting(win, "URL")[1]], badges=True,
               zoom=[properties_row(win, "URL"), properties_row(win, "Params")])
        p.check("Params shows region north, then region south, and the URL /orders?region=north&region=south (AP-5)",
                shown(table(win, PARAMS)) == params
                and setting(win, "URL")[1].text() == "/orders?region=north&region=south")

        p.step("In the line under Headers, type X-Tag: two and press Enter.")
        add = add_line(win, HEADERS)
        QTest.mouseClick(add, Qt.LeftButton)
        QTest.keyClicks(add, "X-Tag: two")
        QTest.keyClick(add, Qt.Key_Return)
        settle()
        scroll_to(win, "Connection")
        note = said_under(win, "From the connection shop")
        p.shot(win, "① Headers keeps both rows named X-Tag: one, then two. ② The note under Connection: the "
                    "connection's X-Tag: zero is replaced by this block's X-Tag: one, two, as it is sent.",
               highlight=[table(win, HEADERS), note], badges=True,
               zoom=[properties_row(win, "Connection"), properties_row(win, "Headers")])
        p.check("Headers shows X-Tag one, then X-Tag two: both kept (AP-5)",
                shown(table(win, HEADERS)) == [("X-Tag", "one"), ("X-Tag", "two")])
        p.check("② The note says “Header X-Tag: zero (replaced by this block's X-Tag: one, two)” (AP-5)",
                "Header X-Tag: zero (replaced by this block's X-Tag: one, two)" in note.text().splitlines())

        p.step("Choose File > Save.")
        save(win)
        p.text("What the flow file holds of send_orders' lists", the_file(path, "send_orders", "params", "headers"))
        p.check("The flow file holds both region rows and both X-Tag rows, in the lists' order (AP-3, AP-5)",
                saved_rows(path, PARAMS, "send_orders") == as_rows(params)
                and saved_rows(path, HEADERS, "send_orders") == as_rows([("X-Tag", "one"), ("X-Tag", "two")]))

        p.step("Right-click send_orders, choose “Run only this block, with inputs I choose…” and press Run, leaving "
               "its body input empty.")
        run = run_only(p, win, qtbot, tab, "send_orders")
        got = api.got("/v1/orders")
        click_bottom_tab(win, win.log)
        sent = f"GET {api.base}/v1/orders?region=north&region=south"
        p.shot(win, f"① send_orders ran on its own. ② The Log says what it sent: {sent}, both region rows in the "
                    "list's order, and 200 OK.",
               highlight=["send_orders", (win.log.text, sent)], badges=True,
               zoom=[(win.log.text, sent), (win.log.text, "200 OK: ")])
        p.text("The headers the server got (as it notes them, in small letters)",
               "\n".join(f"{name}: {value}" for name, value in (got[0]["headers"].items() if got else [])
                         if name not in ("user-agent", "accept-encoding")).replace(api.base[7:], "127.0.0.1:PORT"))
        p.check("The run succeeded, and the server got one GET /v1/orders with the query region=north, then "
                "region=south (AP-5)",
                run.status == "success" and [(r["method"], r["query"]) for r in got] == [("GET", params)])
        p.check("The server got X-Tag once, as “X-Tag: one, two”: both rows' values, in the list's order, and not the "
                "connection's zero (AP-5)", len(got) == 1 and got[0]["headers"].get("x-tag") == "one, two")

        p.step("Right-click send_orders and choose Copy as cURL.")
        QApplication.clipboard().setText("")
        p.while_open(lambda: right_click(tab, "send_orders"), lambda menu: choose(menu, "Copy as cURL"))
        QTest.qWait(100)
        copied = QApplication.clipboard().text()
        p.text("What Copy as cURL put on the clipboard (the server's port written as PORT)",
               copied.replace(api.base, "http://127.0.0.1:PORT"))
        p.check("The curl command sends -H 'X-Tag: one, two' and the query ?region=north&region=south (AP-5)",
                "-H 'X-Tag: one, two'" in copied and "region=north&region=south" in copied and "zero" not in copied)
    for each in win.tabs_list():
        each.doc.dirty = False
