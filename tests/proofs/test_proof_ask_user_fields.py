"""Proof: Ask User shows only the settings its Kind uses; a new Number kind; only a password answer is hidden; an
unattended run takes a password only from its stored secret, or fails saying what is missing. See README.md here."""

from pathlib import Path

import keyring
from keyring.backends import fail
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox

from proofs.test_proof_docs_help import click_block, in_window, lines
from proofs.test_proof_handled_error import click_bottom_tab, log_has, open_flow, press_run_now
from proofs.test_proof_run_output import click as click_canvas
from proofs.test_proof_run_output import type_into
from proofs.test_proof_run_scope import box_text, press
from taskloom.config import Home
from taskloom.editor.dialogs import AskDialog
from tests.test_ask_no_answer import history, python_internals
from tests.test_docs_help import (LEARN_MORE, anchor_at, card_lines, click_learn_more, dismiss, heading_at_top,
                                  hover, property_rows, row_named, wait_for)
from tests.test_lines import shown, wait_runs
from tests.test_paths_editor_running import click, visible_ask

# Taken as this file is imported, before the win fixture answers message boxes itself: Validate's box is then shown
# as in the app.
REAL_WARNING = QMessageBox.warning
ASKED = ("On the ask_user block password kind why does it have options? What is the point in that? Probably needs to "
         "be removed. … On the ask_user block password kind the Default text is not used and probably that makes "
         "sense, in that case Default should also be removed here. … I think some fields are kind dependent and "
         "should change/show/hide based on the selected kind, otherwise it becomes confusing. … Lets add a Number kind "
         "so that if I have an IF block next the user can just compare the input value without having to cast it to "
         "int. If a user makes use of this block it means they are not tech savvy")
ORDERS = "order-check.yaml"
ORDERS_FLOW = """\
name: order-check
blocks:
  start:
    type: trigger.manual
    ui: {x: 0, y: 70}
  ask_count:
    type: logic.ask_user
    config: {prompt: "How many orders came in today?", kind: text}
    ui: {x: 250, y: 70}
  more_than_10:
    type: logic.if
    config: {condition: "value > 10"}
    ui: {x: 500, y: 70}
  big_day:
    type: logic.inspect
    config: {message: "{value!r} orders: more than 10"}
    ui: {x: 760, y: 0}
  quiet_day:
    type: logic.inspect
    config: {message: "{value!r} orders: 10 or fewer"}
    ui: {x: 760, y: 150}
edges:
  - start.start -> ask_count.after
  - ask_count.answer -> more_than_10.value
  - more_than_10.true -> big_day.value
  - more_than_10.false -> quiet_day.value
"""
ORDER_BLOCKS = ["start", "ask_count", "more_than_10", "big_day", "quiet_day"]
LOGIN = "warehouse-login.yaml"
# ask_password's Default is in the file as it is after you change a Kind from text to password: unused.
LOGIN_FLOW = """\
name: warehouse-login
blocks:
  start:
    type: trigger.manual
    ui: {x: 0, y: 70}
  ask_user_name:
    type: logic.ask_user
    config: {prompt: "User name for the warehouse?", kind: text, default: report_bot}
    ui: {x: 250, y: 70}
  show_user_name:
    type: logic.inspect
    config: {message: "user name: {value}"}
    ui: {x: 510, y: 0}
  ask_password:
    type: logic.ask_user
    config: {prompt: "Password for the warehouse?", kind: password, default: letmein}
    ui: {x: 510, y: 150}
  show_password:
    type: logic.inspect
    config: {message: "password: {value}"}
    ui: {x: 770, y: 150}
edges:
  - start.start -> ask_user_name.after
  - ask_user_name.answer -> show_user_name.value
  - ask_user_name.answer -> ask_password.value
  - ask_password.answer -> show_password.value
"""
PASSWORD_LINE = ["ask_user_name", "ask_password", "show_password"]  # what a close-up of a run in a terminal shows
SECRET_NAME = "warehouse_password"
TYPED_PASSWORD = "Blue-Heron-58"  # what you type in ask_password's box
STORED_PASSWORD = "Quiet-Lake-31"  # what the proof saves as warehouse_password, in its temporary Taskloom folder
STORED = "Stored secret (name)"
NO_SECRET = ("nobody can answer in this run; a password needs a stored secret for unattended runs: set Stored secret "
             "(name)")
NOT_SAVED = f"secret '{SECRET_NAME}' is not set (run: taskloom secret set {SECRET_NAME})"
NOT_A_NUMBER = "Default: 'lots' is not a number; kind number needs a number, e.g. 12 or 2.5"
TYPE_A_NUMBER = "Type a number, e.g. 12 or 2.5"


def settings_shown(win, cls) -> list:
    """The rows of Ask User's own settings that Properties shows now, top to bottom (not Name, nor Retry and
    timeout)."""
    return [r for r in property_rows(win, cls.title, cls) if r.field]


def labels(rows) -> list[str]:
    return [r.label for r in rows]


def parts(rows) -> list:
    """Each row's name and box, to outline them."""
    return [w for r in rows for w in (r.label_widget, r.inputs[0])]


def close_up(win, cls) -> list:
    """What a close-up of Properties shows: the block's type at its top, its Name and the settings shown."""
    head = win.properties.widget().layout().itemAt(0).widget()
    return [head, *parts(r for r in property_rows(win, cls.title, cls) if r.label == "Name" or r.field)]


def choose_kind(p, win, cls, kind: str, caption: str = ""):
    """Open Kind's list, as a click on it does, and click `kind`; with a caption, shoot the open list first."""
    box = row_named(win, "Kind", cls=cls).inputs[0]

    def look(popup):
        view = box.view()
        index = view.model().index(box.findText(kind), 0)
        if caption:
            p.shot(popup, caption, over=win, highlight=[(view, box.findText("number"))], badges=True,
                   zoom=[popup, box])
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, view.visualRect(index).center())

    p.while_open(box.showPopup, look)
    QTest.qWait(100)


def default_box(win, cls) -> QLineEdit:
    return row_named(win, "Default", cls=cls).inputs[0]


def file_menu(win, item: str):
    menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in menu.actions() if a.text() == item).trigger()
    QTest.qWait(100)


def block_in_file(path: Path, block: str) -> str:
    """The lines of `block` in the flow file, as saved, without where it is on the canvas."""
    text = path.read_text(encoding="utf-8").splitlines()
    start = text.index(f"  {block}:")
    end = next((i for i in range(start + 1, len(text))
                if not text[i].startswith("    ") or text[i] == "    ui:"), len(text))
    return "\n".join(text[start:end])


def card_line(win, card, text: str) -> QRect:
    """The line(s) of the card that show `text`, the card's width, in the window's pixels (the outline goes 6
    pixels around it). The card lays its text out as this document does."""
    doc = QTextDocument()
    doc.setDefaultFont(card.font())
    doc.setDocumentMargin(0)
    doc.setHtml(card.text())
    inner = card.contentsRect().adjusted(card.margin(), card.margin(), -card.margin(), -card.margin())
    doc.setTextWidth(inner.width())
    found = doc.find(text)
    assert not found.isNull(), f"the card does not say {text!r}: {doc.toPlainText()!r}"
    top = bottom = None
    block = doc.begin()
    while block.isValid():
        at = doc.documentLayout().blockBoundingRect(block).top()
        layout = block.layout()
        for i in range(layout.lineCount()):
            line = layout.lineAt(i)
            begins = block.position() + line.textStart()
            if begins < found.selectionEnd() and begins + max(line.textLength(), 1) > found.selectionStart():
                top = at + line.y() if top is None else top
                bottom = at + line.y() + line.height()
        block = block.next()
    outline = QRectF(inner.left() - 3, inner.top() + top, inner.width() + 6, bottom - top)
    return in_window(win, card, outline.adjusted(6, 6, -6, -6))


def page_line(win, browser, text: str) -> QRect:
    """The line of the Documentation column's page that shows `text`, in the window's pixels."""
    found = browser.document().find(text, 0, QTextDocument.FindCaseSensitively)
    assert not found.isNull(), f"the page does not say {text!r}"
    return lines(win, browser, found, found)


def unattended(env, qtbot, tab, flow: str, last_block: str) -> tuple[str, int, int, str]:
    """`taskloom run FLOW` in a terminal, as the scheduler service runs it: what it printed, its exit code, and the
    run's id and status in the run history; once the editor shows that run on the canvas (it looks at the run history
    every 2 seconds), up to `last_block`."""
    result = env.taskloom("run", flow, encoding="utf-8", errors="replace")
    run_id, status = env.last_run()
    qtbot.waitUntil(lambda: shown(tab, [last_block])[last_block][1] == run_id, timeout=20_000)
    QTest.qWait(100)
    return (result.stdout + result.stderr).strip(), result.returncode, run_id, status  # (a run's lines: stderr)


def clean(text: str) -> bool:
    """No Python internals: no traceback, no exception's name, no Taskloom files or line numbers."""
    return "Traceback" not in text and python_internals(text) == []


# `env` before `win`: its Taskloom folder is the editor's (the same temporary one), and it is made first.
def test_ask_user_shows_only_the_settings_of_its_kind_and_has_a_number_kind(proof, env, win, qtbot, monkeypatch):
    p = proof("Ask User shows only the settings its Kind uses, and has a Number kind", ASKED)
    p.idea("Ask User now shows only the settings its Kind uses, and changes them the moment you change Kind. A new "
           "Kind, number, gives the If after it a number to compare, with nothing to convert. Only a password answer "
           "is hidden, and a run nobody can answer takes a password only from a stored secret, or fails saying what "
           "is missing.")
    p.idea("Prompt and Kind are there for every kind. Options is only for choice. Default is for every kind but "
           "password. Stored secret (name), which used to be called Secret, is only for password. The card you see "
           "when you rest the mouse on a setting, and Help > Documentation, say which kinds it is for, e.g. “Only "
           "when Kind is password.”")
    p.idea("Kind number: the question box takes only a number. For anything else it says “Type a number, e.g. 12 or "
           "2.5” and keeps asking. The answer is a whole number such as 12, or a number such as 2.5, so value > 10 in "
           "the If after it just works. A number question's Default must be a number too: Validate, and runs, refuse "
           "the flow otherwise, naming the block and Default.")
    p.idea("A password answer is shown as *** in the Log, Inspect, Data preview and the run history. Any other "
           "answer shows as you typed it. Runs nobody can answer (taskloom run, and the runs the scheduler service "
           "starts) use the Default; a password, never: it comes from the stored secret named in Stored secret "
           "(name). With no name there, or a name never saved, such a run fails cleanly, saying what is missing.")
    p.why_simplest("Each setting says, once, where Ask User defines it, which kinds it is for. Properties leaves out a "
                   "setting that is not for the chosen Kind; the card and the documentation print “Only when Kind is "
                   "…” from that same line; and a run leaves such a setting empty. One statement read in four "
                   "places, so they cannot disagree.")
    p.why_simplest("Changing Kind draws Properties again at once, as choosing in the Keep runs box of a start block "
                   "already did. Nothing is deleted: a setting the Kind hides keeps its value in the flow file, and "
                   "comes back with it when you choose a Kind that uses it.")
    p.why_simplest("A number answer is made a number in one place, by one rule, for both the question box and the "
                   "Default of a run nobody can answer: 12 stays a whole number, 2.5 a number with a point. Any "
                   "block after it, an If or Python Code, gets a number.")
    p.why_simplest("Hidden or not is decided by the Kind alone: a password answer is marked as a secret, which the "
                   "Log, Inspect, Data preview and the run history already show as ***. No other answer is marked, "
                   "whatever else is filled in.")
    p.alternative("Grey out the settings a Kind does not use, instead of hiding them",
                  "Options and Default would still be on a password question, and you would still wonder what they "
                  "are for: what you asked to remove. Hidden, Properties shows only what matters for that kind.")
    p.alternative("Delete a hidden setting's value from the flow file when you change Kind",
                  "Trying another Kind, or choosing one by mistake, would lose what you had typed. Kept, it comes "
                  "back when you switch back. The cost: the file can hold a value no run uses, which this proof "
                  "shows.")
    p.alternative("Write int(value) > 10 in the If",
                  "It works, but you must know to write it, and if you forget, the If stops with a Python error "
                  "about comparing text with a number: what you wanted to spare someone who is not technical.")
    p.alternative("A separate Ask Number block",
                  "One more block to find and learn, for one more kind of answer; Ask User already takes text, "
                  "yes/no and choices the same way.")
    p.alternative("Let a run nobody can answer use a password's Default",
                  "The password would then sit as plain text in the flow file, which is often shared or kept in Git. "
                  "Taskloom's secret store keeps it out of the file.")
    p.fits("“Only for some choices of another setting” is a rule any block's setting can use; Ask User is the first. "
           "Properties, the card, Help > Documentation and the run all read it from the block.")
    p.fits("The stored secret lives in Taskloom's secret store, where connections keep their passwords too: taskloom "
           "secret set NAME saves one, in the system's credential store or, where there is none, in a file only you "
           "can read.")
    p.fits("The runs nobody can answer are those of taskloom run, without the editor: the scheduler service starts "
           "each scheduled run with that same command. That is why this proof runs them in a terminal. The editor "
           "shows such a run on the canvas of the open flow, as it shows a scheduled one.")
    p.fits("A Default that is not a number is one more problem Validate finds, told like the others: the block, the "
           "setting and what is wrong. A run is refused for it, as for any problem in the blocks it runs.")

    assert win.home.root == env.home, "the editor and the command line use the same Taskloom folder"
    env.env["PYTHONIOENCODING"] = "utf-8"  # what taskloom run prints, as a terminal shows it (on Windows too)
    orders, login = env.root / ORDERS, env.root / LOGIN
    orders.write_text(ORDERS_FLOW, encoding="utf-8")
    login.write_text(LOGIN_FLOW, encoding="utf-8")
    cls = win.registry.get("logic.ask_user")
    log = win.log.text

    # --- the settings of each kind -------------------------------------------------------------------------------
    p.step(f"Open {ORDERS} (File > Open…) and click ask_count. start runs ask_count, an Ask User block of kind text "
           "that asks “How many orders came in today?”. The If more_than_10 checks value > 10: big_day runs when it "
           "is true, quiet_day when it is false. Both are Inspect blocks that show what they get.")
    tab = open_flow(win, monkeypatch, orders)
    p.check("The flow opens", tab is not None and tab.doc.path == orders.resolve())
    click_block(win, "ask_count")
    rows = settings_shown(win, cls)
    p.shot(win, "Kind text: ① Properties shows Prompt, Kind and Default, and no other setting of Ask User.",
           highlight=[parts(rows)], badges=True, zoom=close_up(win, cls))
    p.check("Kind text: Properties shows Prompt, Kind and Default, and nothing else of Ask User",
            labels(rows) == ["Prompt", "Kind", "Default"])

    p.step("Type 30 in Default, and press Enter.")
    type_into(default_box(win, cls), "30")
    p.check("Default holds 30", tab.doc.blocks["ask_count"]["config"].get("default") == "30")

    p.step("Click Kind, and choose choice in its list.")
    kinds = [row_named(win, "Kind", cls=cls).inputs[0].itemText(i) for i in range(5)]
    choose_kind(p, win, cls, "choice", "Kind's list, open: text, ① number, the new kind, password, yes_no and choice.")
    p.check("Kind offers text, number, password, yes_no and choice",
            kinds == ["text", "number", "password", "yes_no", "choice"])
    rows = settings_shown(win, cls)
    p.shot(win, "Kind choice: ① Options appears at once, under Kind: the answers to choose from. Default still holds "
                "30.", highlight=[parts(rows[2:3])], badges=True, zoom=close_up(win, cls))
    p.check("Kind choice: Prompt, Kind, Options and Default, at once",
            labels(rows) == ["Prompt", "Kind", "Options", "Default"]
            and tab.doc.blocks["ask_count"]["config"].get("kind") == "choice")
    p.check("Default still holds 30", default_box(win, cls).text() == "30")

    p.step("Choose password in Kind's list.")
    choose_kind(p, win, cls, "password")
    rows = settings_shown(win, cls)
    p.shot(win, f"Kind password: Options and Default are gone; ① {STORED} appears, the only setting a password "
                "adds.", highlight=[parts(rows[2:3])], badges=True, zoom=close_up(win, cls))
    p.check(f"Kind password: Prompt, Kind and {STORED}; no Options, no Default",
            labels(rows) == ["Prompt", "Kind", STORED])

    p.step(f"Save the flow (File > Save), and look at ask_count in {ORDERS}.")
    file_menu(win, "Save")
    saved = block_in_file(orders, "ask_count")
    p.text(f"ask_count in {ORDERS}, as saved", saved)
    p.check("The file still holds the Default, 30, which Properties no longer shows",
            "default: '30'" in saved and "kind: password" in saved)
    p.note("A setting the Kind hides keeps its value in the flow file: choose a Kind that uses it again, and it is "
           "back. Runs do not use it: a password never answers with a Default, as the runs in a terminal below show.")

    p.step("Choose number in Kind's list.")
    choose_kind(p, win, cls, "number")
    rows = settings_shown(win, cls)
    p.shot(win, f"Kind number: ① Default is back, with the 30 you typed; {STORED} has gone.",
           highlight=[parts(rows[2:3])], badges=True, zoom=close_up(win, cls))
    p.check("Kind number: Prompt, Kind and Default, and Default holds 30 again",
            labels(rows) == ["Prompt", "Kind", "Default"] and default_box(win, cls).text() == "30")
    file_menu(win, "Save")

    # --- the number kind, in the editor --------------------------------------------------------------------------
    p.step("Click an empty spot of the canvas, so that the Log shows every block's lines, and press ▶ Run now in the "
           "toolbar. ask_count asks its question.")
    click_canvas(tab)
    press_run_now(win)
    box = visible_ask(qtbot, win)
    note = next((label.text() for label in box.findChildren(QLabel) if label.text().startswith("Leave it empty")), "")
    p.shot(box, "The question box: the Default, 30, in grey in the empty box, and the note under it.", over=win,
           zoom=[box])
    p.check("The box asks “How many orders came in today?”, its Default 30 in grey",
            box.windowTitle() == "ask_count asks" and box.input.placeholderText() == "30"
            and note == "Leave it empty to use the default: 30")

    p.step("Type “a dozen” and click OK.")
    QTest.keyClicks(box.input, "a dozen")
    click(box, "OK")
    QTest.qWait(100)
    p.shot(box, f"Only a number is taken: the box says, in red, “{TYPE_A_NUMBER}”, and keeps asking.", over=win,
           zoom=[box])
    p.check(f"The box is still open, saying “{TYPE_A_NUMBER}”",
            box.isVisible() and box.why.isVisible() and box.why.text() == TYPE_A_NUMBER)
    p.note("The box takes what Python reads as a number: 12, 2.5, -3, and also 1e3, which is 1000.")

    p.step("Type 12 in place of it, and click OK.")
    box.input.selectAll()
    QTest.keyClicks(box.input, "12")
    click(box, "OK")
    first = wait_runs(qtbot, tab, 1)
    QTest.qWait(300)  # what the run printed last reaches the Log
    p.shot(win, "① more_than_10 took its true path: big_day ran. ② quiet_day, on its false path, was skipped. The "
                "If compared 12 with 10 as numbers.",
           highlight=[["more_than_10", "big_day"], "quiet_day"], badges=True, zoom=ORDER_BLOCKS)
    p.check(f"Run #{first} succeeded: ① more_than_10 took its true path, big_day ran; ② quiet_day was skipped",
            tab.runs[0].status == "success"
            and shown(tab) == {"start": ("success", first), "ask_count": ("success", first),
                               "more_than_10": ("success", first), "big_day": ("success", first),
                               "quiet_day": ("skipped", first)})
    said = (log, "[big_day] INFO 12 orders: more than 10")
    p.shot(win, "In the Log, under the canvas: ① big_day says “12 orders: more than 10”.", highlight=[said],
           badges=True, zoom=[(log, f"run #{first} "), said])
    p.check("① big_day shows the answer as 12, a number: “12 orders: more than 10”, not “'12' orders”",
            log_has(win, "[big_day] INFO 12 orders: more than 10") and "'12'" not in log.toPlainText())
    p.note("big_day's message shows the answer as Python writes it: a number as 12, a text in quotes, as '12'. A "
           "text '12' could not have been compared with 10 at all: the If would have failed.")

    p.step("Click ask_count, then the Data preview tab, under the canvas.")
    click_block(win, "ask_count")
    click_bottom_tab(win, win.preview)
    preview = [win.preview.label, (win.preview.text, "12")]
    p.shot(win, "① Data preview shows ask_count's answer: 12.", highlight=[preview], badges=True, zoom=preview)
    p.check("① Data preview shows ask_count.answer: 12",
            win.preview.label.text() == "ask_count.answer:" and win.preview.text.toPlainText() == "12")

    p.step("Type lots in Default, and press Enter.")
    type_into(default_box(win, cls), "lots")
    error = win.properties.error
    p.shot(win, f"① Properties says what is wrong, in red, at its top: {NOT_A_NUMBER}.", highlight=[error],
           badges=True, zoom=[error, *close_up(win, cls)])
    p.check(f"① Properties says “{NOT_A_NUMBER}”", not error.isHidden() and error.text() == NOT_A_NUMBER)

    p.step("Press Validate in the toolbar (F7).")
    problems = []

    def look_at_the_problems(box):
        problems.append(box.text())
        p.shot(box, "Validate's box names the block, ask_count, and the setting, Default, and says what is wrong.",
               highlight=[box_text(box)])
        box.accept()

    with monkeypatch.context() as real:  # the box as the app shows it
        real.setattr(QMessageBox, "warning", REAL_WARNING)
        p.while_open(lambda: press(win, win.validate_action), look_at_the_problems)
    QTest.qWait(100)
    p.check(f"Validate says “ask_count: {NOT_A_NUMBER}”", problems == [f"ask_count: {NOT_A_NUMBER}"])

    # --- the number kind, in a run nobody can answer -----------------------------------------------------------
    p.step(f"Type 30 in Default again, save the flow, and run it in a terminal, in the folder of the flows: taskloom "
           f"run {ORDERS}. Nobody is there to answer, as in the runs the scheduler service starts.")
    type_into(default_box(win, cls), "30")
    file_menu(win, "Save")
    printed, code, second, status = unattended(env, qtbot, tab, ORDERS, "big_day")
    p.text(f"taskloom run {ORDERS} printed", printed)
    p.shot(win, f"The editor shows run #{second}, from the terminal, on the canvas: ① ask_count answered with its "
                "Default, 30; ② more_than_10 took its true path, so big_day ran and quiet_day was skipped.",
           highlight=["ask_count", ["more_than_10", "big_day"]], badges=True, zoom=ORDER_BLOCKS)
    p.check("① ask_count answered with its Default as a number: “answer: 30”, with no quotes (a text answer is "
            "printed in quotes, as 'report_bot' is below); big_day says “30 orders: more than 10”",
            "[ask_count] success · answer: 30" in printed and "[big_day] INFO: 30 orders: more than 10" in printed)
    p.check("② quiet_day was skipped, and the run succeeded: exit code 0, success in the run history",
            "[quiet_day] skipped" in printed and (code, status) == (0, "success")
            and shown(tab, ["big_day", "quiet_day"]) == {"big_day": ("success", second),
                                                         "quiet_day": ("skipped", second)})

    # --- the card and the documentation ---------------------------------------------------------------------
    p.step(f"Open {LOGIN} (File > Open…) and click ask_password. ask_user_name asks for a user name (kind text, "
           "Default report_bot), and show_user_name shows it; then ask_password asks for the password (kind "
           "password), and show_password shows it. ask_password's file also holds a Default, letmein, as it would "
           "after you changed its Kind from text to password.")
    tab = open_flow(win, monkeypatch, login)
    p.check("The flow opens", tab is not None and tab.doc.path == login.resolve())
    click_block(win, "ask_password")
    rows = settings_shown(win, cls)
    p.shot(win, f"① ask_password, of kind password: ② Properties shows Prompt, Kind and {STORED}, and no Default, "
                "though the file has one.", highlight=["ask_password", parts(rows)], badges=True,
           zoom=close_up(win, cls))
    p.check(f"② Properties shows Prompt, Kind and {STORED}, though the file has a Default",
            labels(rows) == ["Prompt", "Kind", STORED]
            and tab.doc.blocks["ask_password"]["config"].get("default") == "letmein")

    p.step(f"Rest the mouse on {STORED}.")
    p.note("The proof rests the mouse on it as Qt does: it moves the mouse there, then gives it the event Qt gives "
           "once the mouse has stayed there for the usual tooltip delay.")
    stored = row_named(win, STORED, cls=cls)
    card = hover(qtbot, win, stored.label_widget, STORED, f"Ask User › {STORED}")
    said = card_lines(card)
    help_text = cls.config["secret"].help
    p.shot(card, f"The card of {STORED}: ① what it is: the name of a password saved in Taskloom's secret store, "
                 "what runs nobody can answer use, and a name never saved makes the run fail. ② When it is shown: "
                 "only when Kind is password.", over=win,
           highlight=[card_line(win, card, help_text), card_line(win, card, "Only when Kind is password.")],
           badges=True, zoom=[card, stored.label_widget])
    p.check("① The card says it is the name of a password saved in Taskloom's secret store, that runs nobody can "
            "answer (unattended, e.g. scheduled ones) use it, and that a name never saved makes the run fail",
            said[0] == STORED and said[1] == help_text
            and "The name of a password saved in Taskloom's secret store" in help_text
            and "unattended" in help_text and "A name never saved makes the run fail." in help_text)
    p.check("② It says when the setting is shown: “Only when Kind is password.”",
            "Only when Kind is password." in said and said[-1] == LEARN_MORE)

    p.step("Click Learn more › on the card. It opens Help > Documentation's column at the setting's entry.")
    href = click_learn_more(card)
    browser = win.docs.browser
    wait_for(qtbot, lambda: heading_at_top(browser, "logic.ask_user.secret"),
             f"{STORED}'s entry is not at the top of the column")
    browser.scrollToAnchor("logic.ask_user.options")  # the three settings that are only for some kinds, in view
    QTest.qWait(100)
    only = [page_line(win, browser, f"Only when Kind is {kinds}.")
            for kinds in ("choice", "text, number, yes_no or choice", "password")]
    options = anchor_at(browser, "logic.ask_user.options")  # its heading
    p.shot(win, "The Documentation column, on Ask User's settings: each that is only for some kinds says so. ① "
                "Options: only when Kind is choice. ② Default: only when Kind is text, number, yes_no or choice. ③ "
                f"{STORED}: only when Kind is password, with the same help as the card.",
           highlight=only, badges=True, zoom=[lines(win, browser, options, options), *only])
    page = " ".join(browser.toPlainText().split())
    p.check("The column shows Ask User's settings, on the Logic page",
            href.endswith("#logic.ask_user.secret") and browser.source().fileName() == "blocks-logic.html")
    p.check(f"③ Its entry for {STORED} has the card's help and “Only when Kind is password.”",
            f"{STORED} {help_text} Only when Kind is password." in page)
    p.check("① Options says “Only when Kind is choice.” and ② Default “Only when Kind is text, number, yes_no or "
            "choice.”", "Only when Kind is choice." in page
            and "Only when Kind is text, number, yes_no or choice." in page)
    dismiss(qtbot, win)

    # --- only a password is hidden -------------------------------------------------------------------------------
    p.step("Press F1 to close the Documentation column, click an empty spot of the canvas and press ▶ Run now. Type "
           "analyst7 in ask_user_name's box and click OK; then type a password in ask_password's box and click OK.")
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=5_000)
    tab.view.setFocus()
    QTest.keyClick(tab.view, Qt.Key_F1)
    qtbot.waitUntil(lambda: not win.docs.isVisible(), timeout=5_000)
    click_canvas(tab)
    press_run_now(win)
    box = visible_ask(qtbot, win)
    QTest.keyClicks(box.input, "analyst7")
    click(box, "OK")
    qtbot.waitUntil(lambda: any(d.isVisible() and d.windowTitle() == "ask_password asks"
                                for d in win.findChildren(AskDialog)), timeout=60_000)
    box = visible_ask(qtbot, win)
    QTest.keyClicks(box.input, TYPED_PASSWORD)
    p.shot(box, "ask_password's box: the password shows as dots as you type it, and no Default is offered.",
           over=win, zoom=[box])
    p.check("The password box hides what is typed, and offers no Default",
            box.input.echoMode() == QLineEdit.Password and box.input.placeholderText() == ""
            and not any(label.text().startswith("Leave it empty") for label in box.findChildren(QLabel)))
    click(box, "OK")
    third = wait_runs(qtbot, tab, 1)
    QTest.qWait(300)
    answers = [(log, "[show_user_name] INFO user name: analyst7"), (log, "[show_password] INFO password: ***")]
    p.shot(win, "The outlined lines of the Log: show_user_name says “user name: analyst7”, as you typed it; "
                "show_password says “password: ***”.", highlight=[answers], zoom=answers)
    p.check(f"Run #{third} succeeded", tab.runs[0].status == "success")
    p.check("The text answer shows as typed: “user name: analyst7”", log_has(win, "user name: analyst7"))
    p.check("The password shows as ***, and what you typed is nowhere in the Log",
            log_has(win, "password: ***") and TYPED_PASSWORD not in log.toPlainText())

    p.step("Click ask_password, then the Data preview tab.")
    click_block(win, "ask_password")
    click_bottom_tab(win, win.preview)
    preview = [win.preview.label, (win.preview.text, "***")]
    p.shot(win, "① Data preview shows ask_password's answer as ***.", highlight=[preview], badges=True, zoom=preview)
    p.check("① Data preview shows ask_password.answer: ***",
            win.preview.label.text() == "ask_password.answer:" and win.preview.text.toPlainText() == "***")

    p.step(f"Click an empty spot of the canvas, then the Run history tab, and double-click run #{third}. The Log shows "
           "it as the run history keeps it.")
    click_canvas(tab)
    click_bottom_tab(win, win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 1, timeout=10_000)
    at = table.visualItemRect(table.item(0, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # (QTest's is only the second click)
    qtbot.waitUntil(lambda: win.bottom_tabs.currentWidget() is win.log, timeout=10_000)
    QTest.qWait(200)
    status = win.statusBar()
    showing = QRect(status.mapTo(win, QPoint(0, 0)),
                    QSize(status.fontMetrics().horizontalAdvance(status.currentMessage()) + 16, status.height()))
    p.shot(win, f"① The window shows run #{third} from the run history. ② Its Log, as kept: the user name as typed, "
                "the password as ***.", highlight=[showing, answers], badges=True, zoom=[showing, *answers])
    _, kept = history(env.home, third)
    p.check(f"① The status bar says it shows run #{third}",
            status.currentMessage().startswith(f"Showing run #{third} "))
    p.check("② The run history keeps “user name: analyst7” and “password: ***”, and nowhere the password typed",
            "user name: analyst7" in kept and "password: ***" in kept and TYPED_PASSWORD not in kept)

    # --- a password, in a run nobody can answer ----------------------------------------------------------------
    p.step(f"Run the flow in the terminal: taskloom run {LOGIN}. ask_password has no {STORED}, and its Default "
           "letmein is still in the file.")
    printed, code, fourth, status = unattended(env, qtbot, tab, LOGIN, "ask_password")
    p.text(f"taskloom run {LOGIN} printed", printed)
    p.shot(win, f"The canvas shows run #{fourth}, from the terminal: ① ask_password failed, and says why on its last "
                "line; ② show_password was skipped.",
           highlight=["ask_password", "show_password"], badges=True, zoom=PASSWORD_LINE)
    p.check("ask_user_name, of kind text, answered with its Default: “user name: report_bot”",
            "[show_user_name] INFO: user name: report_bot" in printed)
    p.check(f"① ask_password failed, with no traceback, file paths or line numbers: “{NO_SECRET}”",
            f"[ask_password] failed · {NO_SECRET}" in printed and clean(printed)
            and shown(tab, ["ask_password"])["ask_password"] == ("failed", fourth))
    p.check("② It never used the Default letmein: show_password was skipped, and the run failed (exit code 1)",
            "[show_password] skipped" in printed and "letmein" not in printed and (code, status) == (1, "failed")
            and shown(tab, ["show_password"])["show_password"] == ("skipped", fourth))

    p.step(f"Type {SECRET_NAME} in ask_password's {STORED}, press Enter, and save the flow.")
    p.note("Only a password needs a stored secret: ask_user_name, of kind text, answered with its Default. Before "
           "this change Secret was used by every kind, so a text Ask User with a Secret filled in answered with that "
           "stored secret in such runs. Now it is only for the password kind, and such a block uses its Default: "
           "change its Kind to password to keep using the secret.")
    click_bottom_tab(win, win.log)
    click_block(win, "ask_password")
    type_into(row_named(win, STORED, cls=cls).inputs[0], SECRET_NAME)
    file_menu(win, "Save")
    rows = settings_shown(win, cls)
    p.shot(win, f"① ask_password's {STORED}: {SECRET_NAME}.", highlight=[parts(rows[2:3])], badges=True,
           zoom=close_up(win, cls))
    p.check(f"① The file names {SECRET_NAME} as ask_password's stored secret",
            f"secret: {SECRET_NAME}" in block_in_file(login, "ask_password"))

    p.step(f"Run it in the terminal again: taskloom run {LOGIN}. No password named {SECRET_NAME} has been saved yet.")
    printed, code, fifth, status = unattended(env, qtbot, tab, LOGIN, "ask_password")
    p.text(f"taskloom run {LOGIN} printed", printed)
    p.shot(win, f"The canvas shows run #{fifth}: ① ask_password failed: the secret {SECRET_NAME} is not set, and the "
                "message says how to set it.", highlight=["ask_password"], badges=True, zoom=PASSWORD_LINE)
    p.check(f"① ask_password failed, saying the secret is not set and how to set it: “{NOT_SAVED}”",
            f"[ask_password] failed · {NOT_SAVED}" in printed
            and shown(tab, ["ask_password"])["ask_password"] == ("failed", fifth))
    p.check("show_password was skipped, and the run failed (exit code 1), cleanly",
            "[show_password] skipped" in printed and (code, status) == (1, "failed") and clean(printed))

    p.step(f"Save a password named {SECRET_NAME}, as taskloom secret set {SECRET_NAME} does, and run the flow in "
           f"the terminal once more: taskloom run {LOGIN}.")
    p.note("taskloom secret set reads the password from the keyboard, which a proof cannot type into. So the proof "
           "saves it with the same code that command runs, in this proof's own temporary Taskloom folder: never in "
           "your system's credential store.")
    assert isinstance(keyring.get_keyring(), fail.Keyring), "the tests never use the real credential store"
    where = Home(env.home).set_secret(SECRET_NAME, STORED_PASSWORD)
    secrets = env.home / "secrets.yaml"
    p.check(f"{SECRET_NAME} is saved in the proof's temporary Taskloom folder, not in the system's credential store",
            Path(where).resolve() == secrets.resolve() and f"{SECRET_NAME}:" in secrets.read_text(encoding="utf-8"))
    printed, code, sixth, status = unattended(env, qtbot, tab, LOGIN, "show_password")
    p.text(f"taskloom run {LOGIN} printed", printed)
    p.shot(win, f"The canvas shows run #{sixth}: ① ask_password answered with the stored {SECRET_NAME}, shown as "
                "***, and ② show_password ran.", highlight=["ask_password", "show_password"], badges=True,
           zoom=PASSWORD_LINE)
    _, kept = history(env.home, sixth)
    p.check(f"① ask_password answered with the stored {SECRET_NAME}: ② show_password says “password: ***”, and the "
            "run succeeded (exit code 0)",
            "[show_password] INFO: password: ***" in printed and (code, status) == (0, "success")
            and shown(tab, ["show_password"])["show_password"] == ("success", sixth))
    p.check("The password is nowhere in what it printed, nor in the run history",
            STORED_PASSWORD not in printed and STORED_PASSWORD not in kept)
