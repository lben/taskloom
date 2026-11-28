"""Proof: credentials on the PC, kept as cards of keys (Tools > Credentials…, `taskloom secret`), named in a flow as
CARD.key in three ways, and shown only as *** wherever the editor shows a run. See README.md here. Made-up values only,
in the tests' stand-in credential store (never the real one)."""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QMessageBox

import test_credentials_store as S
import test_credentials_window as W
from proofs.test_proof_line_failures import box_text, click_bottom_tab, press
from proofs.test_proof_python_reusable_block import click_block, open_flow, run_now, write_flow
from proofs.test_proof_validate_docs_fixes import REAL_WARNING
from test_credentials_editor import grid, no_proxy
from tests.test_copy_executed_query import copy_query

ASKED = ("Credentials on the PC: cards such as UAT and PROD, each with its keys, kept in the computer's credential "
         "store and never in a file; a card DEFAULT that takes its values from one of them, switched in one place; a "
         "flow names a value as CARD.key, and a run shows it only as ***.")
VALUES = {"UAT.password": "uat-made-up-pass-7", "UAT.api_key": "uat-made-up-key-31",
          "PROD.password": "prod-made-up-pass-8", "PROD.api_key": "prod-made-up-key-42"}
web = S.web  # (a web server on this computer that answers with the X- headers it got)

NIGHTLY = """
    blocks:
      start:
        type: trigger.manual
        ui: {{x: 0, y: 0}}
      call_shop:
        type: servers.api_request
        config:
          url: {base}/echo
          headers: [{{key: X-Api-Key, value: "secret:DEFAULT.api_key"}}]
        ui: {{x: 260, y: 0}}
      read_orders:
        type: data.duckdb_sql
        config: {{inputs: [], sql: "select 'orders' as source, '{{{{secret:DEFAULT.password}}}}' as password"}}
        ui: {{x: 260, y: 200}}
      check_card:
        type: logic.python
        config:
          inputs: [response]
          outputs: []
          use_environment: false
          code: |-
            password = secret("DEFAULT.password")
            card = "UAT" if password == secret("UAT.password") else "PROD"
            print("DEFAULT.password is", password, "- the value of", card)
            print("the shop got", response)
        ui: {{x: 540, y: 0}}
    edges:
      - start.start -> call_shop.after
      - start.start -> read_orders.after
      - call_shop.response -> check_card.response
"""
MONTH_END = """
    blocks:
      start:
        type: trigger.manual
        ui: {x: 0, y: 0}
      send_invoices:
        type: logic.python
        config: {inputs: [], outputs: [], use_environment: false, code: "token = secret('DEFAULT.token')"}
        ui: {x: 260, y: 0}
    edges:
      - start.start -> send_invoices.after
"""


def in_credentials(p, win, then):
    """Tools > Credentials… in the editor, then then(window) in the window that opens."""
    p.while_open(lambda: W.menu_item(win, "Tools", "Credentials…").trigger(), then)


def inherited(window) -> list:
    """The rows the chosen card shows of the card it takes its values from (their key box greyed)."""
    return [r for r in W.rows(window) if W.key_box(r).text() and not W.key_box(r).isEnabled()]


def use(window, card: str):
    uses = window.findChild(QComboBox, "uses")
    uses.setCurrentIndex(uses.findText(card))
    W.settle()
    return uses


def told(win) -> str:
    return win.log.text.toPlainText()


def validated(p, win, monkeypatch, caption: str) -> str:
    """Press Validate in the toolbar; shoot the box of problems it opens over the window, its text outlined, and
    press OK. What the box said."""
    seen = []

    def look(box):
        seen.append(box.text())
        p.shot(box, caption, over=win, highlight=[box_text(box)], badges=True)
        box.accept()

    with monkeypatch.context() as real:  # (the `win` fixture records boxes in its place: here it is shown)
        real.setattr(QMessageBox, "warning", staticmethod(REAL_WARNING))
        p.while_open(lambda: press(win, win.validate_action), look)
    QTest.qWait(100)
    return seen[0]


def test_credentials_as_cards_named_in_a_flow_and_shown_only_as_stars(proof, env, win, qtbot, monkeypatch, web,
                                                                       credential_store):
    p = proof("Credentials as cards, named in a flow, shown only as ***", ASKED)
    p.idea("A credential is a row of a card: UAT.password is the row password of the card UAT. Its value is kept in "
           "the computer's credential store (Windows Credential Manager, macOS Keychain); the list of cards and keys, "
           "never a value, is in credentials.yaml in the Taskloom home. `taskloom secret set UAT.password` asks for "
           "the value without showing it, `taskloom secret list` shows the cards and keys.")
    p.idea("Tools > Credentials… shows the cards on the left and the chosen card's rows on the right, every value as "
           "the same dots whatever its length; a row's eye shows that value alone. A card can take its values from "
           "another (DEFAULT from UAT): its own rows win. Save stores every change at once, Cancel none.")
    p.idea("A flow names a value as secret:CARD.key (a whole field: a connection's password, a header), "
           "{{secret:CARD.key}} (inside a text: a query, a body) or secret(\"CARD.key\") (in Python Code). A run "
           "checks every reference before its first block, and shows each value it read only as *** in the Log, "
           "the results, Run history and Copy executed query.")
    p.why_simplest("One way of naming (CARD.key) and one resolver for the three places a value is written; the store "
                   "the PC already has keeps the values, so Taskloom keeps no file of secrets and no key of its own. "
                   "DEFAULT switched in one place switches every flow that names it.")
    p.alternative("An encrypted file of secrets in the Taskloom home",
                  "Taskloom would need a key to open it, kept somewhere: the credential store keeps values safe "
                  "already, behind the person's own sign-in.")
    p.alternative("One flat list of names (warehouse_password, uat_warehouse_password, ...)",
                  "Switching from UAT to PROD would mean editing every flow; a card that takes its values from "
                  "another switches them all.")
    p.fits("Values stay out of flow files, their commits, the history and every place the editor shows a run; the "
           "editor's windows are built as Connections is, in both themes.")
    no_proxy(monkeypatch)

    # --- 1. Values stored from the command line --------------------------------------------------------------------
    p.step("Four made-up values are stored with `taskloom secret set CARD.key --stdin`, each piped into it (never on "
           "the command line): the cards UAT and PROD are made as their first value is stored.")
    for reference, value in VALUES.items():
        done = S.secret_set(env, reference, value)
        assert done.returncode == 0, done.stderr

    # --- 2. The Credentials window -------------------------------------------------------------------------------
    p.step("Tools > Credentials…: UAT chosen, its rows with their values as dots, one empty row last; then its "
           "api_key's eye pressed, and pressed again.")

    def look_at_uat(window):
        W.choose(window, "UAT")
        api_key = W.row(window, "api_key")
        p.check("UAT's rows show every value as the same dots, and an empty row last",
                W.keys(window)[-1] == "" and all(W.dots(W.shown(r)) for r in W.rows(window)[:-1])
                and len({W.shown(r) for r in W.rows(window)[:-1]}) == 1)
        p.shot(window, "① UAT chosen; ② its rows: every value the same dots, whatever its length; one empty row "
                       "last, to add a key.",
               highlight=[W.card_list(window), W.rows(window)], badges=True)
        W.eye(api_key).click()
        W.settle()
        p.check("api_key's eye shows that value alone", W.shown(api_key) == VALUES["UAT.api_key"]
                and W.dots(W.shown(W.row(window, "password"))))
        p.shot(window, "The one picture with an eye open: ① api_key's eye shows its made-up value; the other row "
                       "stays dots.", highlight=[api_key], badges=True)
        W.eye(api_key).click()
        W.settle()
        p.check("pressed again, the eye shows dots again", W.dots(W.shown(api_key)))
        W.asked_names(monkeypatch, "DEFAULT")
        W.press(window, "Add…")
        W.choose(window, "DEFAULT")
        uses = use(window, "UAT")
        p.check("DEFAULT shows UAT's rows, greyed, from UAT",
                sorted(W.key_box(r).text() for r in inherited(window)) == ["api_key", "password"]
                and all("from uat" in W.notes(r) for r in inherited(window)))
        p.shot(window, "Add… made the card DEFAULT: ① Takes its values from UAT; ② UAT's rows show greyed, "
                       "\"from UAT\": DEFAULT.password is UAT.password, until DEFAULT has a row of its own.",
               highlight=[uses, inherited(window)], badges=True)
        W.press(window, "Save")

    in_credentials(p, win, look_at_uat)
    p.check("Save wrote the list: DEFAULT takes its values from UAT, and no value is in it",
            ("DEFAULT", "UAT", []) in W.cards_of(win)
            and not any(v in (win.home.root / "credentials.yaml").read_text(encoding="utf-8")
                        for v in VALUES.values()))

    # --- 3. taskloom secret list -----------------------------------------------------------------------------------
    p.step("`taskloom secret list` shows the cards, what each takes its values from and their keys, never a value.")
    listed = S.taskloom(env, "secret", "list")
    p.text("taskloom secret list", listed.stdout)
    p.check("the list names the three cards and DEFAULT's UAT, and shows no value",
            listed.returncode == 0 and all(c in listed.stdout for c in ("UAT", "PROD", "DEFAULT"))
            and not any(v in listed.stdout + listed.stderr for v in VALUES.values()))

    # --- 4. A flow naming DEFAULT in the three ways ----------------------------------------------------------------
    p.step("The flow nightly names DEFAULT's values in the three ways: call_shop's header X-Api-Key is "
           "secret:DEFAULT.api_key, read_orders' query has {{secret:DEFAULT.password}} in it, and check_card's Python "
           "reads secret(\"DEFAULT.password\"), says whose value it is and prints what the shop got.")
    path = write_flow(env.root / "nightly.yaml", NIGHTLY.format(base=web.base))
    tab = open_flow(win, monkeypatch, path)
    first = run_now(win, qtbot, tab)
    p.check("the run ended well, and the shop got UAT's api_key",
            first.status == "success" and web.seen[-1].get("x-api-key") == VALUES["UAT.api_key"])
    click_bottom_tab(win, win.log)
    p.check("the Log shows the value as *** and no value",
            "DEFAULT.password is *** - the value of UAT" in told(win) and "x-api-key=***" in told(win)
            and not any(v in told(win) for v in VALUES.values()))
    p.shot(win, "▶ Run now: the Log shows ① DEFAULT.password as ***, UAT's value; ② the shop's answer, holding the "
                "api_key it got, as ***.",
           highlight=[(win.log.text, "DEFAULT.password is"), (win.log.text, "x-api-key=")], badges=True)
    click_block(tab, "read_orders")
    click_bottom_tab(win, win.preview)
    p.check("read_orders' result shows *** in the password column", "***" in grid(win)
            and not any(v in " ".join(grid(win)) for v in VALUES.values()))
    p.shot(win, "read_orders chosen: ① its result in Data preview, the value written into the query as ***.",
           highlight=[win.preview], badges=True)

    # --- 5. DEFAULT switched to PROD --------------------------------------------------------------------------------
    p.step("Tools > Credentials… again: DEFAULT made to take its values from PROD, and saved; the flow is not "
           "changed, and its next run uses PROD's values.")

    def switch(window):
        W.choose(window, "DEFAULT")
        uses = use(window, "PROD")
        p.shot(window, "① DEFAULT takes its values from PROD now; ② its rows are PROD's.",
               highlight=[uses, inherited(window)], badges=True)
        W.press(window, "Save")

    in_credentials(p, win, switch)
    second = run_now(win, qtbot, tab)
    click_block(tab, "check_card")  # (the Log shows the chosen block's lines)
    click_bottom_tab(win, win.log)
    p.check("the next run used PROD's values, shown as ***",
            second.status == "success" and web.seen[-1].get("x-api-key") == VALUES["PROD.api_key"]
            and "DEFAULT.password is *** - the value of PROD" in told(win)
            and not any(v in told(win) for v in VALUES.values()))
    p.shot(win, "▶ Run now again, check_card chosen: ① DEFAULT.password is PROD's value now, as ***.",
           highlight=[(win.log.text, "the value of PROD")], badges=True)

    # --- 6. Run history and Copy executed query --------------------------------------------------------------------
    p.step("Run history: the first run double-clicked shows its Log as kept in the history, with *** in place of "
           "each value; Copy executed query on it copies the query with *** written in.")
    click_bottom_tab(win, win.history_panel)
    table = win.history_panel.table
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == str(first.run_id))
    at = table.visualItemRect(table.item(row, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # (QTest's is only the second click)
    QTest.qWait(200)
    p.check("the first run's Log, from the history, shows UAT's value as *** and no line of the second run",
            win.bottom_tabs.currentWidget() is win.log and "DEFAULT.password is *** - the value of UAT" in told(win)
            and "the value of PROD" not in told(win)
            and not any(v in told(win) for v in VALUES.values()))
    p.shot(win, "Run #1 double-clicked in Run history: its Log, read back from the history, shows ① UAT's value "
                "as ***.", highlight=[(win.log.text, "DEFAULT.password is")], badges=True)
    copied = copy_query(win, qtbot, first.run_id)
    p.text("Copy executed query, on the first run", copied)
    p.check("the query copied has *** where the value was written in",
            copied == "select 'orders' as source, '***' as password")

    # --- 7. Validate names a missing key ---------------------------------------------------------------------------
    p.step("The flow month_end reads secret('DEFAULT.token'), which no card has: Validate names the block and the "
           "key.")
    open_flow(win, monkeypatch, write_flow(env.root / "month_end.yaml", MONTH_END))
    said = validated(p, win, monkeypatch, "Validate: ① the problem names the block send_invoices and "
                                          "DEFAULT.token, and the row it comes from now, PROD.token, which has no value.")
    p.check("Validate names the block and the missing key", "send_invoices" in said and "DEFAULT.token" in said)
    for t in win.tabs_list():
        t.doc.dirty = False
