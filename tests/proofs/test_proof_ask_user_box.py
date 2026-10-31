"""Proof: Ask User's question box shows the Default as a grey placeholder, Enter gives the Default, and the box
closes by itself when the run stops waiting. See README.md here."""

import json

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QLabel, QLineEdit, QToolBar

from proofs.test_proof_handled_error import log_has, open_flow, press_run_now
from proofs.test_proof_run_output import click as click_block, setting
from taskloom.editor.runs import RunProcess
from tests.test_lines import shown, wait_runs
from tests.test_paths_editor_running import click, visible_ask

ASKED = ("Can we make the default text to be presented on a grayish color as if it were a placeholder (which it "
         "is)? In order to convey the idea that this is the default value but can/needs to be changed")
REGION = "region.yaml"
DAYS = "days-back.yaml"
CHOICE = "region-choice.yaml"
SEND = "send-report.yaml"
COPY = "keep-copy.yaml"
WAREHOUSE = "warehouse.yaml"
IN_TIME = "region-in-time.yaml"
QUESTION = "Which region's sales?"
USED_NORTH = "nothing was typed: the default 'North' is the answer"
STOPPED = "question closed without an answer: the run was stopped"
TIMED_OUT = "question closed without an answer: the block's Timeout ran out"
FAILED = "no answer was given"


def write_flow(folder, name: str, ask: str, config: dict, inspect: dict | None = None, **settings):
    """start -> the Ask User block `ask` (its `config`; `settings`: e.g. its timeout) -> show_answer, an Inspect
    block (its `inspect` config)."""
    flow = {"blocks": {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 60}},
                       ask: {"type": "logic.ask_user", "config": config, **settings, "ui": {"x": 260, "y": 60}},
                       "show_answer": {"type": "logic.inspect", "config": inspect or {}, "ui": {"x": 540, "y": 60}}},
            "edges": [f"start.start -> {ask}.after", f"{ask}.answer -> show_answer.value"]}
    (folder / name).write_text(json.dumps(flow, indent=2), encoding="utf-8")  # (a flow file in YAML reads JSON)
    return folder / name


def answer_box(box) -> QLineEdit:
    (edit,) = [e for e in box.findChildren(QLineEdit) if e.isVisibleTo(box)]
    return edit


def button(box, text: str):
    (found,) = [b for b in box.findChild(QDialogButtonBox).buttons() if b.text() == text]
    return found


def labels(box) -> list[QLabel]:
    """The texts the box shows, top to bottom: the question first."""
    return sorted((label for label in box.findChildren(QLabel) if label.isVisibleTo(box)),
                  key=lambda label: label.mapTo(box, QPoint(0, 0)).y())


def around(widget, win) -> QRect:
    """Where `widget` is in a screenshot of the window, with a little room: its outline then hugs it, and leaves
    the grey text in it as it is."""
    at = widget.mapToGlobal(QPoint(0, 0)) - win.mapToGlobal(QPoint(0, 0))
    return QRect(at, widget.size()).adjusted(-3, 0, 3, 0)


def press_enter(box):
    """Press Enter, where you are in the box."""
    QTest.keyClick(box.focusWidget() or box, Qt.Key_Return)


def contrast(widget) -> int:
    """How strongly the text in `widget` stands out from its background, as drawn on the screen: the difference in
    lightness (0 to 255) between its background and the inside of its letters (the pixels of its text, inside its
    frame, that stand out more than three quarters of them: their soft edges left out)."""
    image = widget.grab().toImage()
    pixels = [QColor(image.pixel(x, y)).lightness()
              for y in range(4, image.height() - 4) for x in range(4, image.width() - 4)]
    background = max(set(pixels), key=pixels.count)
    drawn = sorted(abs(lightness - background) for lightness in pixels if abs(lightness - background) > 24)
    assert drawn, "nothing is drawn in it"
    return drawn[len(drawn) * 3 // 4]


def under(box, label, widget) -> bool:
    """`label` is under `widget` in the box."""
    return label.mapTo(box, QPoint(0, 0)).y() >= widget.mapTo(box, QPoint(0, widget.height())).y()


def lines_with(win, text: str) -> int:
    """How many lines of the Log show `text`."""
    return sum(text in line for line in win.log.text.toPlainText().splitlines())


def scroll_log_to(win, text: str):
    """Scroll the Log so that the first line showing `text` is at its top."""
    view = win.log.text
    bar = view.verticalScrollBar()
    bar.setValue(bar.value() + view.cursorRect(view.document().find(text)).top() - 2)
    QTest.qWait(50)


def test_ask_users_box_shows_the_default_as_a_grey_placeholder(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Ask User's box shows the Default as a grey placeholder, and closes when the run stops waiting", ASKED)
    p.idea("The question box shows Ask User's Default in grey, inside an empty box, as a placeholder. Leave the box "
           "empty and press Enter (or OK): the Default is the answer, and the Log says so in one line. Type, and what "
           "you type is the answer: there is nothing to delete first.")
    p.idea("A short grey note under the box says it too: “Leave it empty to use the default: North”. That note is "
           "how you know that an empty box means the Default; nothing is filled in without the Log saying so.")
    p.idea("Each kind of answer shows its Default its own way. A number shows it in grey like a text, and takes only "
           "a number. A choice opens on its Default. For yes/no, the Default is the button Enter presses (Yes, "
           "without a Default). A password is unchanged: a masked, empty box, and nothing about a default.")
    p.idea("When the run stops waiting for the answer, because the block's Timeout ran out or the run ended, the box "
           "closes by itself. The Log says why, and nothing typed into the box is sent. A stopped run closes its box "
           "the same way, but you cannot press Stop while a box is open: the box keeps the editor's window waiting, "
           "as any dialog does. This proof presses Stop from the program to show it.")
    p.why_simplest("The Default was already the answer Ask User uses when nobody can be asked (taskloom run, the "
                   "scheduler service). The box now shows it as Qt, the toolkit the editor is built with, shows any "
                   "placeholder: in the theme's placeholder grey, only while the box is empty, gone at the first key. "
                   "The note under it is in the same grey.")
    p.why_simplest("The box sends what is in it, as before: an empty box, an empty answer. The Ask User block, in the "
                   "run, takes an empty answer as its Default, and writes that one line in its Log. So the line and "
                   "the answer come from the same place, and the run history keeps both.")
    p.why_simplest("A choice and yes/no have no empty box: their Default is the one already chosen, so Enter "
                   "answers it there too. When the run stops waiting, it says why in its Log and tells the editor, "
                   "which closes that box without sending anything. When the run ends with a box still open, the "
                   "editor closes it, and writes why in the Log itself.")
    p.alternative("Keep the Default as real text in the box, as before, and select it so that typing replaces it",
                  "It looks like an answer already given, in the same black as your own: what you described. You "
                  "asked for grey, as a placeholder, to say that it is the default and can be changed.")
    p.alternative("Grey real text in the box, turned black when you type",
                  "That is a placeholder rebuilt by hand: the box would have to tell its grey text from yours, and "
                  "drop it at the first key. Qt's placeholder already does exactly that, in the theme's own grey.")
    p.alternative("Let the box put the Default in when you press OK on an empty box",
                  "The run would get the Default as if you had typed it, and could not tell the two apart: its Log "
                  "and the run history could not say that the Default was used, a surprise. The block takes it, and "
                  "says so.")
    p.alternative("Close the open boxes from the editor, when it stops their run",
                  "The block's Timeout runs out inside the run, where the editor does not see it. The run knows when "
                  "it stops waiting and why, so a stop and the Timeout close the box the same way, and the reason "
                  "comes from the block.")
    p.alternative("Leave the box open after the run stopped waiting",
                  "It would ask a question nobody waits for, and what you typed there would go nowhere, without a "
                  "word. That is how it was before.")
    p.fits("The Default is one setting with one meaning: the answer you give by pressing Enter without changing "
           "anything in the box, and the answer when nobody can be asked, as before. In a text or number box, it is "
           "the grey text an empty box gives.")
    p.fits("The grey is the theme's placeholder grey, the same as Taskloom's other grey hints, such as the note under "
           "Driver class in the Connections dialog.")
    p.fits("The lines the block writes (the Default was the answer; the question closed, and why) are Log lines like "
           "any other: filtered by the block, and kept with the run in the run history.")
    p.fits("“Don't answer” is unchanged: a clean failure that goes out the error output, as the proof “Ask User's "
           "“Don't answer” is a clean failure that goes out the error output” shows. The last step here shows that a "
           "Default changes nothing.")

    sent = []  # what the editor sends to its runs (answers, Stop), as it sends it
    real_send = RunProcess._send
    monkeypatch.setattr(RunProcess, "_send", lambda run, message: (sent.append(message), real_send(run, message)))
    log = win.log.text
    region = write_flow(tmp_path, REGION, "ask_region", {"prompt": QUESTION, "kind": "text", "default": "North"})

    # --- text: the Default in grey, an empty answer gives it ---------------------------------------------------------
    p.step(f"Open the flow {REGION} (File > Open…) and click ask_region. start runs ask_region, an Ask User block of "
           "kind text that asks “Which region's sales?”, with North as its Default. show_answer, an Inspect block, "
           "shows the answer it gets.")
    tab = open_flow(win, monkeypatch, region)
    click_block(tab, "ask_region")
    kind, default = setting(win, "Kind"), setting(win, "Default")
    p.shot(win, "① ask_region asks the question; ② show_answer shows the answer. ③ In Properties, ask_region's "
                "Default is North.",
           highlight=["ask_region", "show_answer", list(default)], badges=True, zoom=[*kind, *default])
    p.check("ask_region is of kind text, with North as its Default",
            (kind[1].currentText(), default[1].text()) == ("text", "North"))

    p.step("Click an empty spot of the canvas, so that the Log shows every block's lines, and press ▶ Run now in the "
           "toolbar. ask_region's question box opens.")
    click_block(tab)
    press_run_now(win)
    box = visible_ask(qtbot, win)
    edit = answer_box(box)
    question, note = labels(box)
    p.shot(box, "① The box is empty: North is in grey, as a placeholder. ② The grey note under it says that leaving "
                "it empty uses the default.",
           over=win, highlight=[around(edit, win), around(note, win)], badges=True, zoom=[box])
    p.check("The box is empty, with North as its grey placeholder",
            (edit.text(), edit.placeholderText()) == ("", "North"))
    grey = contrast(edit)
    p.check("The note under the box says “Leave it empty to use the default: North”",
            note.text() == "Leave it empty to use the default: North" and under(box, note, edit))
    p.check("The note is drawn in grey: it stands out much less than the question",
            contrast(note) + 50 < contrast(question))

    p.step("Press Enter, leaving the box empty.")
    press_enter(box)
    first = wait_runs(qtbot, tab, 1)
    QTest.qWait(300)  # what the run printed last reaches the Log
    used = [(log, USED_NORTH), (log, "[show_answer] INFO North")]  # one outline around them both
    p.shot(win, "The outlined lines of the Log. First, ask_region's one line: nothing was typed, so the default "
                "North is the answer. Then show_answer's: North reached it.",
           highlight=[used], zoom=used)
    p.check(f"Run #{first} succeeded: ask_region and show_answer show success",
            tab.runs[0].status == "success" and shown(tab) == {b: ("success", first) for b in tab.doc.blocks})
    p.check("The Log says, in one line, that the default North is the answer",
            lines_with(win, "default") == 1 and log_has(win, f"[ask_region] INFO {USED_NORTH}"))
    p.check("North reached show_answer", log_has(win, "[show_answer] INFO North"))
    p.note("Without a Default, the box has no grey text and no note, and an empty box answers an empty text, as "
           "before.")

    # --- typing replaces it ------------------------------------------------------------------------------------------
    p.step("Press ▶ Run now again, type South in the box, and click OK.")
    press_run_now(win)
    box = visible_ask(qtbot, win)
    edit = answer_box(box)
    QTest.mouseClick(edit, Qt.LeftButton)
    QTest.keyClicks(edit, "South")
    p.shot(box, "① South, as you type it: in black. The grey North went at the first key: nothing had to be deleted.",
           over=win, highlight=[around(edit, win)], badges=True, zoom=[box])
    p.check("What you type is in the box, drawn in black: it stands out much more than the grey North",
            edit.text() == "South" and contrast(edit) > grey + 50)
    click(box, "OK")
    second = wait_runs(qtbot, tab, 2)
    QTest.qWait(300)
    p.shot(win, "① show_answer got South. The Log says nothing about a default this time.",
           highlight=[(log, "[show_answer] INFO South")], badges=True,
           zoom=[(log, f"run #{second} "), (log, "[show_answer] INFO South")])
    p.check(f"Run #{second}: South is the answer, and reached show_answer",
            tab.runs[1].status == "success" and log_has(win, "[show_answer] INFO South"))
    p.check("The Log still has only the one line about a default, from the first run", lines_with(win, "default") == 1)

    # --- number ------------------------------------------------------------------------------------------------------
    days = write_flow(tmp_path, DAYS, "ask_days", {"prompt": "How many days back?", "kind": "number", "default": "7"},
                      {"message": "{value} days back: {value * 24} hours"})
    p.step(f"Open {DAYS} and press ▶ Run now. ask_days is of kind number, with 7 as its Default; show_answer works "
           "out the hours, which it can do only with a number.")
    days_tab = open_flow(win, monkeypatch, days)
    before = len(sent)
    press_run_now(win)
    box = visible_ask(qtbot, win)
    edit = answer_box(box)
    question, note = labels(box)
    p.shot(box, "① The same grey placeholder: 7. ② The same note under the box.",
           over=win, highlight=[around(edit, win), around(note, win)], badges=True, zoom=[box])
    p.check("The box is empty, with 7 as its grey placeholder and the note under it",
            (edit.text(), edit.placeholderText(), note.text()) == ("", "7", "Leave it empty to use the default: 7"))

    p.step("Type “a week” and click OK.")
    QTest.mouseClick(edit, Qt.LeftButton)
    QTest.keyClicks(edit, "a week")
    click(box, "OK")
    QTest.qWait(300)
    why = labels(box)[-1]
    p.shot(box, "The box only takes a number: ① it says in red to type one, and stays open with the question. The "
                "grey note above it is whole.",
           over=win, highlight=[around(why, win).adjusted(0, 5, 0, 0)],  # (its outline clear of the note above it)
           badges=True, zoom=[box])
    p.check("The box is still open, and says “Type a number, e.g. 12 or 2.5”",
            box.isVisible() and why.text() == "Type a number, e.g. 12 or 2.5")
    p.check("The grey note above it still has room for all its text: the red line does not cut it off",
            note.height() >= note.heightForWidth(note.width()))
    p.check("Nothing was sent to the run", sent[before:] == [])
    p.note("A number's box with no Default refuses an empty box the same way: the question stays until you type a "
           "number (or click Don't answer).")

    p.step("Delete what you typed, and press Enter with the box empty.")
    for _ in "a week":
        QTest.keyClick(edit, Qt.Key_Backspace)
    p.check("The grey 7 is back in the empty box", (edit.text(), edit.placeholderText()) == ("", "7"))
    press_enter(box)
    number_run = wait_runs(qtbot, days_tab, 1)
    QTest.qWait(300)
    lines = [(log, "nothing was typed: the default '7' is the answer"), (log, "7 days back: 168 hours")]
    p.shot(win, "The outlined lines of the Log. First, nothing was typed, so the default 7 is the answer. Then "
                "show_answer worked out the hours: 7 days back is 168 hours, so 7 came as a number.",
           highlight=[lines], zoom=lines)
    p.check(f"Run #{number_run}: the Log says the default 7 is the answer, and show_answer says “7 days back: 168 "
            "hours”",
            days_tab.runs[0].status == "success"
            and log_has(win, "[ask_days] INFO nothing was typed: the default '7' is the answer",
                        "[show_answer] INFO 7 days back: 168 hours"))

    # --- choice ------------------------------------------------------------------------------------------------------
    choice = write_flow(tmp_path, CHOICE, "pick_region", {"prompt": QUESTION, "kind": "choice",
                                                          "options": ["North", "South", "East"], "default": "South"})
    p.step(f"Open {CHOICE}, press ▶ Run now, and press Enter in the box. pick_region is of kind choice, with the "
           "Options North, South and East, and South as its Default.")
    choice_tab = open_flow(win, monkeypatch, choice)
    press_run_now(win)
    box = visible_ask(qtbot, win)
    options = box.findChild(QComboBox)
    p.shot(box, "① The box opens on the Default: South is chosen. Enter answers it.", over=win,
           highlight=[around(options, win)], badges=True, zoom=[box])
    p.check("South is chosen when the box opens", options.currentText() == "South")
    press_enter(box)
    wait_runs(qtbot, choice_tab, 1)
    QTest.qWait(300)
    p.check("Enter answered South: it reached show_answer",
            choice_tab.runs[0].status == "success" and log_has(win, "[show_answer] INFO South"))

    # --- yes/no ------------------------------------------------------------------------------------------------------
    send_flow = write_flow(tmp_path, SEND, "send_now", {"prompt": "Send the report now?", "kind": "yes_no",
                                                        "default": "no"})
    p.step(f"Open {SEND}, press ▶ Run now, and press Enter in the box. send_now is of kind yes_no, with no as its "
           "Default.")
    send_tab = open_flow(win, monkeypatch, send_flow)
    press_run_now(win)
    box = visible_ask(qtbot, win)
    yes, no = button(box, "Yes"), button(box, "No")
    p.shot(box, "① No is the default button, drawn as the default: the one Enter presses.", over=win,
           highlight=[around(no, win)], badges=True, zoom=[box])
    p.check("No is the default button, and Yes is not", no.isDefault() and not yes.isDefault())
    press_enter(box)
    wait_runs(qtbot, send_tab, 1)
    QTest.qWait(300)
    p.check("Enter answered no: show_answer got False",
            send_tab.runs[0].status == "success" and log_has(win, "[show_answer] INFO False"))

    copy_flow = write_flow(tmp_path, COPY, "keep_copy", {"prompt": "Keep a copy of the report?", "kind": "yes_no"})
    p.step(f"Open {COPY}, press ▶ Run now, and press Enter in the box. keep_copy is of kind yes_no, with no Default.")
    copy_tab = open_flow(win, monkeypatch, copy_flow)
    press_run_now(win)
    box = visible_ask(qtbot, win)
    yes, no = button(box, "Yes"), button(box, "No")
    p.shot(box, "The outlined button: without a Default, Yes is the default button, as before.", over=win,
           highlight=[around(yes, win)], zoom=[box])  # (no badge: it would cover the question)
    p.check("Yes is the default button, and No is not", yes.isDefault() and not no.isDefault())
    press_enter(box)
    wait_runs(qtbot, copy_tab, 1)
    QTest.qWait(300)
    p.check("Enter answered yes: show_answer got True",
            copy_tab.runs[0].status == "success" and log_has(win, "[show_answer] INFO True"))

    # --- password ----------------------------------------------------------------------------------------------------
    warehouse = write_flow(tmp_path, WAREHOUSE, "ask_password", {"prompt": "Password for the warehouse",
                                                                 "kind": "password"})
    p.step(f"Open {WAREHOUSE}, press ▶ Run now, type example-pass in the box, and click OK. ask_password is of kind "
           "password.")
    warehouse_tab = open_flow(win, monkeypatch, warehouse)
    press_run_now(win)
    box = visible_ask(qtbot, win)
    edit = answer_box(box)
    p.check("The box is masked and empty, with no grey text and no note about a default",
            edit.echoMode() == QLineEdit.Password and edit.text() == "" and edit.placeholderText() == ""
            and [label.text() for label in labels(box)] == ["Password for the warehouse"])
    QTest.mouseClick(edit, Qt.LeftButton)
    QTest.keyClicks(edit, "example-pass")
    p.shot(box, "① What you type is masked, as before. No grey text, no note: a password has no Default.", over=win,
           highlight=[around(edit, win)], badges=True, zoom=[box])
    click(box, "OK")
    wait_runs(qtbot, warehouse_tab, 1)
    QTest.qWait(300)
    p.check("show_answer shows the password as *** in the Log, never what was typed",
            warehouse_tab.runs[0].status == "success" and log_has(win, "[show_answer] INFO ***")
            and "example-pass" not in log.toPlainText())

    # --- Stop: the box closes by itself ------------------------------------------------------------------------------
    p.step(f"Click the {REGION} tab, above the canvas, press ▶ Run now, and type South in the box, without "
           "clicking OK.")
    win.tabs.setCurrentWidget(tab)
    QTest.qWait(100)
    press_run_now(win)
    box = visible_ask(qtbot, win)
    edit = answer_box(box)
    QTest.mouseClick(edit, Qt.LeftButton)
    QTest.keyClicks(edit, "South")
    stop = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.stop_action)
    p.shot(box, "① Stop, in the toolbar. ② The question waits, with South typed in it.", over=win,
           highlight=[stop, around(edit, win)], badges=True, zoom=[box])

    p.step("Stop the run while the box is open: the proof presses Stop from the program, because the box keeps the "
           "button from being clicked.")
    before = len(sent)
    # A click on Stop and its key, Shift+F5, as the system delivers them: to the window under the mouse, and to the
    # box, which has the keys.
    QTest.mouseClick(win.windowHandle(), Qt.LeftButton, Qt.NoModifier, stop.mapTo(win, stop.rect().center()))
    QTest.keyClick(box.windowHandle(), Qt.Key_F5, Qt.ShiftModifier)
    QTest.qWait(300)
    p.check("A click on Stop, and Shift+F5, made as the system makes them, do not reach the editor's window while "
            "the box is open: nothing is sent, and the box stays open",
            len(sent) == before and box.isVisible())
    QTest.mouseClick(stop, Qt.LeftButton)  # as the button does, from the program
    qtbot.waitUntil(lambda: not box.isVisible(), timeout=20_000)
    closed_at = len(sent)
    stopped_run = wait_runs(qtbot, tab, 3)
    QTest.qWait(300)
    stopped = [(log, STOPPED), (log, f"run #{stopped_run} cancelled")]
    p.shot(win, "The box closed by itself. The outlined lines of the Log say why: the question was closed without an "
                f"answer, because the run was stopped. Then run #{stopped_run} was cancelled.",
           highlight=[stopped], zoom=stopped)
    p.check("The box closed by itself", not box.isVisible())
    p.check("The Log says “question closed without an answer: the run was stopped”",
            log_has(win, f"[ask_region] WARNING {STOPPED}"))
    p.check(f"Run #{stopped_run} was cancelled, and show_answer did not get South",
            tab.runs[2].status == "cancelled" and lines_with(win, "[show_answer] INFO South") == 1)
    p.check("Nothing was sent to the run after the box closed, and no answer at all: only Stop",
            sent[closed_at:] == [] and sent[before:] == [{"cancel": True}])

    qtbot.waitUntil(lambda: win.statusBar().currentMessage() == "", timeout=10_000)  # its “Stopping…” is gone

    # --- the block's Timeout ----------------------------------------------------------------------------------------
    in_time = write_flow(tmp_path, IN_TIME, "ask_region", {"prompt": QUESTION, "kind": "text"}, timeout="5s")
    p.step(f"Open {IN_TIME}, press ▶ Run now, type South in the box, and wait. Its ask_region has a Timeout of 5 "
           "seconds.")
    in_time_tab = open_flow(win, monkeypatch, in_time)
    before = len(sent)
    press_run_now(win)
    box = visible_ask(qtbot, win)
    edit = answer_box(box)
    QTest.mouseClick(edit, Qt.LeftButton)
    QTest.keyClicks(edit, "South")
    p.shot(box, "① The question waits, with South typed in it, until ask_region's Timeout runs out.", over=win,
           highlight=[around(edit, win)], badges=True, zoom=[box])
    qtbot.waitUntil(lambda: not box.isVisible(), timeout=30_000)
    timed_out_run = wait_runs(qtbot, in_time_tab, 1)
    QTest.qWait(300)
    p.check("The box closed by itself", not box.isVisible())
    p.check(f"Run #{timed_out_run} failed: ask_region failed, and show_answer was skipped",
            in_time_tab.runs[0].status == "failed"
            and shown(in_time_tab) == {"start": ("success", timed_out_run), "ask_region": ("failed", timed_out_run),
                                       "show_answer": ("skipped", timed_out_run)})
    p.check("Nothing was sent to the run, after the box closed or before", sent[before:] == [])

    p.step("Scroll the Log up a little, to ask_region's lines.")
    scroll_log_to(win, TIMED_OUT)
    timed_out = [(log, TIMED_OUT), (log, "ask_region (Ask User) failed")]
    p.shot(win, "The outlined lines of the Log say why the box closed: the question was closed without an answer, "
                "because the block's Timeout ran out. Then ask_region failed: it timed out, as any block whose "
                "Timeout runs out.",
           highlight=[timed_out], zoom=timed_out)
    p.check("The Log says “question closed without an answer: the block's Timeout ran out”",
            log_has(win, f"[ask_region] WARNING {TIMED_OUT}"))
    p.check("Then ask_region failed: it timed out after 5 s", log_has(win, "timed out after 5 s"))

    # --- Don't answer ------------------------------------------------------------------------------------------------
    p.step(f"Click the {REGION} tab, press ▶ Run now, and click Don't answer.")
    win.tabs.setCurrentWidget(tab)
    QTest.qWait(100)
    press_run_now(win)
    click(visible_ask(qtbot, win), "Don't answer")
    unanswered_run = wait_runs(qtbot, tab, 4)
    QTest.qWait(300)
    failed = [(log, f"ask_region (Ask User) failed: {FAILED}"), (log, f"run #{unanswered_run} failed")]
    p.shot(win, "The outlined lines of the Log. ask_region failed cleanly: no answer was given; the Default was not "
                "used. Below it, the input it got, as for any failure. Last, the run failed: nothing is connected "
                "to ask_region's error output here.",
           highlight=[failed], zoom=failed)
    p.check("ask_region failed with “no answer was given”, and the Default was not used",
            tab.runs[3].status == "failed" and tab.scene.nodes["ask_region"].status_text == f"✕ {FAILED}"
            and lines_with(win, "default") == 1)
