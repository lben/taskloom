"""Proof: {name} placeholders in every setting you type a value into: a flow parameter, or the value arriving at one of
the block's own inputs. The Wait block's Seconds takes {value} or {pause}. How proofs are written: README.md here."""

import datetime as dt

import polars as pl
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from proofs.conftest import _area
from proofs.test_proof_canvas import open_flow
from proofs.test_proof_docs_help import card_parts, card_says, click_block
from proofs.test_proof_inspect_log import hugged, picture_area
from proofs.test_proof_run_scope import box_text, click_bottom_tab, delete_connection, press
from proofs.test_proof_scheduler_start import click_empty
from proofs.test_proof_tab_stays import click_button, click_inspector_tab, parameters_zoom, type_in, type_in_cell
from taskloom.editor.docs import PLACEHOLDERS
from taskloom.editor.panels import CodeEdit
from taskloom.history import History
from taskloom.report import render_chart
from tests.test_docs_help import dismiss, hover
from tests.test_editor import drag_port_to_port
from tests.test_inspect_log import images
from tests.test_paths_editor_editing import error_shown, field
from tests.test_placeholders import write

# Taken as this file is imported, before the win fixture answers message boxes itself: Validate's box is then shown
# as in the app.
REAL_WARNING = QMessageBox.warning
ASKED = ("WAIT block: The wait block has a value input, I should be able to use that value (cast it to float first) "
         "if I write {value} in the seconds field, otherwise I can just connect to the run_after input where I would "
         "obviously specify the number of seconds manually It should also support flow parameters as its value so "
         "that I can write {flow_parameter} and it picks up the parameter value and uses it. This probably should "
         "generalize outside of the WAIT block probably in order to make the entire app more powerful Display a "
         "countdown here as well for the seconds we are waiting")

WAITS = """
    params:
      pause: {type: float, value: 2}
    blocks:
      start: {type: trigger.manual, ui: {x: 0, y: 0}}
      make_number: {type: logic.python, config: {inputs: [], code: "output = 1.5"}, ui: {x: 260, y: 0}}
      wait_for_value: {type: logic.wait, config: {seconds: 5}, ui: {x: 520, y: 0}}
      wait_for_pause: {type: logic.wait, config: {seconds: 5}, ui: {x: 780, y: 0}}
    edges:
      - start.start -> make_number.after
      - make_number.output -> wait_for_value.value
      - wait_for_value.value -> wait_for_pause.after
"""
LINE = ["start", "make_number", "wait_for_value", "wait_for_pause"]
SALES = "pl.DataFrame({'month': ['Jan', 'Feb', 'Mar'], 'amount': [120, 80, 45], 'returns': [6, 9, 3]})"
CHART = """
    params:
      region: {type: text, value: North}
      w: {type: int, value: 480}
      col: {type: text, value: amount}
    blocks:
      start: {type: trigger.manual, ui: {x: 0, y: 0}}
      sales:
        type: logic.python
        config: {inputs: [], code: "output = SALES"}
        ui: {x: 260, y: 0}
      chart:
        type: reports.chart
        config: {kind: bar, x: month, y: ["{col}"], title: "{region} sales", width: "{w}", height: 240}
        ui: {x: 520, y: 0}
      show: {type: logic.inspect, ui: {x: 780, y: 0}}
      caption:
        type: logic.python
        config: {inputs: [], code: "output = 'Sales of {region}'"}
        ui: {x: 260, y: 190}
    edges:
      - start.start -> sales.after
      - sales.output -> chart.table
      - chart.image -> show.value
      - start.start -> caption.after
""".replace("SALES", SALES)
CHART_LINE = ["start", "sales", "chart", "show"]
LOG_HEIGHT = 480  # the Log made taller for the chart's picture (320 pixels high there) and the line above it
NEITHER = "wait_for_pause: Seconds: {pasue} is neither a flow parameter nor an input of this block"
BOTH = "wait_for_value: Seconds: {value} could be the parameter value or the input value: rename one"
NOT_A_NUMBER = "wait_for_value (Wait) failed: Seconds: {value} gave 'abc', which is not a number"
NOT_CONNECTED = "wait_for_value (Wait) failed: Seconds: {value}: nothing is connected to the input value"


# --- doing it, as you would -----------------------------------------------------------------------------------

def run_now(p, win, qtbot, tab, typed: dict | None = None, look=None) -> int:
    """Press ▶ Run now; in the Run dialog type each of `typed` ({parameter: value}) over its value, look(dialog) (to
    shoot it), then press Run; wait for the run to end. Its number."""
    before = len(tab.runs)

    def in_the_dialog(dialog):
        for name, value in (typed or {}).items():
            type_over(dialog.edits[name], value)
        if look is not None:
            look(dialog)
        QTest.mouseClick(next(b for b in dialog.findChildren(QPushButton) if b.text() == "Run"), Qt.LeftButton)

    p.while_open(lambda: press(win, win.run_action), in_the_dialog)
    qtbot.waitUntil(lambda: len(tab.runs) == before + 1 and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)
    QTest.qWait(200)  # the Log and the canvas have shown its last events
    return tab.runs[-1].run_id


def type_over(edit, text: str):
    """Click a box, select what it holds and type `text` over it (no Enter: in the Run dialog, Enter runs)."""
    QTest.mouseClick(edit, Qt.LeftButton)
    edit.selectAll()
    QTest.keyClicks(edit, text)


def set_code(win, text: str):
    """Click in the selected block's code box, type `text` over its code, then click elsewhere."""
    code = next(c for c in win.properties.findChildren(CodeEdit) if c.isVisible())
    code.setFocus()
    code.selectAll()
    QTest.keyClicks(code, text)
    code.editing_finished.emit()  # as when you click elsewhere
    QTest.qWait(50)


def validate(p, win, monkeypatch, caption: str) -> list[str]:
    """Press Validate in the toolbar; shoot the box it shows, with `caption`, and close it. The lines it says."""
    said = []

    def look_at_the_problems(box):
        said.extend(box.text().splitlines())
        p.shot(box, caption, highlight=[box_text(box)])
        box.accept()

    with monkeypatch.context() as real:  # the box as the app shows it
        real.setattr(QMessageBox, "warning", REAL_WARNING)
        p.while_open(lambda: press(win, win.validate_action), look_at_the_problems)
    QTest.qWait(100)
    return said


# --- what the run said ----------------------------------------------------------------------------------------

def kept(win, read):
    """What read(history) reads of the run history the editor keeps."""
    history = History(win.home.history_db)
    try:
        return read(history)
    finally:
        history.close()


def waiting(win, run_id: int, block: str) -> dict:
    """The line of `block` in the Log of run `run_id` that says how long it waits."""
    said = [e for e in kept(win, lambda h: h.logs(run_id))
            if e.get("block") == block and e["message"].startswith("waiting ")]
    assert len(said) == 1, f"{block} said how long it waits {len(said)} times in run #{run_id}"
    return said[0]


def says_waiting(win, run_id: int, block: str) -> bool:
    return any(e.get("block") == block and e["message"].startswith("waiting ")
               for e in kept(win, lambda h: h.logs(run_id)))


def waited(win, run_id: int, block: str) -> float:
    """The seconds from `block`'s line saying how long it waits to its end."""
    finished = kept(win, lambda h: h.blocks(run_id))[block]["finished"]
    return (dt.datetime.fromisoformat(finished) - dt.datetime.fromisoformat(waiting(win, run_id, block)["ts"])
            ).total_seconds()


def error_of(win, run_id: int, block: str) -> str:
    """The first line of what the Log of run `run_id` says about `block` failing."""
    said = [e["message"] for e in kept(win, lambda h: h.logs(run_id)) if e.get("block") == block
            and e["level"] == "ERROR"]
    assert said, f"the Log of run #{run_id} says nothing about {block} failing"
    return said[-1].splitlines()[0]


def statuses(tab) -> dict:
    return {block: tab.scene.nodes[block].status for block in tab.scene.nodes}


def log_lines(win, texts: list[str], clear: str) -> list:
    """The Log's lines that show `texts`, next to each other, as one outline: two outlines would share the border
    between the lines, over their text. It reaches past the line that shows `clear` (the one above them), so its
    badge, on its top right, covers no text."""
    lines = [(win.log.text, text) for text in texts]
    top = _area(lines, win)[0].top()
    right = max(_area((win.log.text, text), win)[0].right() for text in [*texts, clear]) + 8
    return [*lines, QRect(right, top, 1, 1)]


def test_placeholders_in_every_setting(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Placeholders in every setting: a flow parameter, or one of the block's own inputs", ASKED)
    p.idea("Every setting you type a value into, in every block, can now hold {name} placeholders, where name is a "
           "flow parameter or one of the block's own inputs. The Wait block's Seconds can be {value} or {pause}.")
    p.idea("As the block runs, each {name} is replaced by that parameter's value, or by the value arriving at that "
           "input, and a number setting (Seconds, a width, a count) then reads the text it got as its number: 1.5 "
           "arriving at the value input makes Seconds {value} wait 1.5 seconds, and so would the text 1.5 with "
           "spaces around it. It works in text, paths, whole numbers, numbers and each item of a list, with a format "
           "if you want one ({value:.1f}, {day:%Y-%m-%d}). Write {{ and }} for braces.")
    p.idea("A placeholder is a name, not a calculation: {value * 60} is not one. Work a number out in a Python Code "
           "block first, and connect it. The run after input only says when a block starts: it is never a "
           "placeholder, so a Wait connected only there waits the seconds you type, or a flow parameter's.")
    p.idea("Nothing is filled in silently. Validate (F7) refuses a name that is neither a flow parameter nor an input "
           "of the block, and one that is both, before any run. As the block runs, it fails before it does anything, "
           "naming the setting, the placeholder and why: nothing is connected to that input, nothing arrived there, "
           "a table arrived (only a single value can be written into a setting), or a number setting got a text "
           "that is not a number.")
    p.idea("Code is as before. Python Code, Polars, If and Inspect's message take no placeholders: their braces are "
           "Python's own. An HTML Template fills in its own {name}, and SQL takes flow parameters only. Yes/no and "
           "choice settings take none: you tick or pick them.")
    p.why_simplest("Text settings already had {name} filled in with flow parameters, just before each block ran. "
                   "That same filling now also takes the values arriving at the block's own inputs, which have "
                   "arrived by then, and works in number and list settings too. A number setting is read as its "
                   "number after it, within its own limits: a whole number stays whole, a minimum stays.")
    p.why_simplest("So it is one rule for every block, in one place. The Wait block has no code of its own for "
                   "{value}: it gets its Seconds as a number, filled in, as any block gets its settings.")
    p.alternative("A Wait-only option, e.g. “take the seconds from the value input”",
                  "It would solve the Wait block alone, and each block that needs a value from its input would need "
                  "an option of its own. You asked for it to work outside the Wait block too.")
    p.alternative("Expressions in settings, such as {value * 60} or a Python f-string",
                  "A second language in every box, with its own ways to fail as a run goes, that Validate could not "
                  "check before it. A name can be checked before any run; a calculation already has the Python "
                  "Code block.")
    p.alternative("Filling in quietly: a name that is not known left as written, nothing when no value arrives, or "
                  "one of a parameter and an input of the same name chosen for you",
                  "You would find out only when a Wait waited no time, or a title said {region}. Each of these is "
                  "now said, by Validate or as the block starts, so you decide what to change.")
    p.fits("It is the {name} and {name:format} that text settings and SQL already took for flow parameters, and the "
           "{name} an HTML Template fills in from its inputs: one way of writing it everywhere.")
    p.fits("Validate (F7), Tools > Check flows and the taskloom validate and check commands report a wrong name in the "
           "same words, and a run refuses to start for it. The help card of each setting that takes placeholders "
           "says so. A parameter you change in the Run dialog for one run fills in the placeholders of that run.")

    flow = write(tmp_path / "waits.yaml", WAITS)

    # --- (1) {value} and {pause} in Properties, and the help on hover -------------------------------------------
    p.step("Open waits.yaml (File > Open…). make_number gives the number 1.5, connected into wait_for_value's value "
           "input; wait_for_pause runs after wait_for_value. Both Waits say 5 seconds for now. The flow has a "
           "parameter pause, 2.")
    tab = open_flow(win, monkeypatch, flow)
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    p.shot(win, "The flow: make_number's output goes into ① wait_for_value's value input; ② wait_for_pause runs "
                "after wait_for_value. The line at the bottom of each Wait says how long it waits: ⏱ waits 5 s.",
           highlight=["wait_for_value", "wait_for_pause"], badges=True, zoom=LINE)

    p.step("Click wait_for_value, type {value} into its Seconds in Properties, and press Enter.")
    click_block(win, "wait_for_value")
    type_in(field(win, "Seconds"), "{value}")
    seconds = field(win, "Seconds")  # (Properties shows the block again, with what you typed)
    p.shot(win, "① Seconds says {value}, and Properties shows no red line above it: it is taken as it is. ② On the "
                "canvas, wait_for_value's bottom line says ⏱ waits {value} s.",
           highlight=[seconds, "wait_for_value"], badges=True, zoom=[win.properties.widget(), "wait_for_value"])
    p.check("① Seconds says {value} and no red line says something is wrong",
            seconds.text() == "{value}" and error_shown(win) == "")
    p.check("The flow holds Seconds {value}, as you typed it",
            tab.doc.blocks["wait_for_value"]["config"]["seconds"] == "{value}")

    p.step("Rest the mouse on Seconds.")
    card = hover(qtbot, win, seconds, "Seconds", "Wait › Seconds")
    label, what, values, last = card_says(card)
    parts = card_parts(win, card)
    p.shot(card, "The card of Seconds: ① a number, {value} for the number arriving at its value input, or {name} for "
                 "a flow parameter; ② its examples, and how placeholders work.",
           over=win, highlight=parts[:2], badges=True, zoom=[card, *parts[:2]])
    p.check("① The card says Seconds is “a number (0.5 for half a second), {value} for the number arriving at its "
            "value input, or {name} for a flow parameter”",
            label == "Seconds" and "a number (0.5 for half a second), {value} for the number arriving at its value "
                                   "input, or {name} for a flow parameter" in what)
    p.check("② It says how placeholders work: “" + PLACEHOLDERS + "”", " ".join(values.split()).endswith(PLACEHOLDERS))
    dismiss(qtbot, win)

    p.step("Click wait_for_pause, type {pause} into its Seconds, and press Enter.")
    click_block(win, "wait_for_pause")
    type_in(field(win, "Seconds"), "{pause}")
    p.shot(win, "① wait_for_pause's Seconds says {pause}, with no red line; ② its bottom line says ⏱ waits {pause} s.",
           highlight=[field(win, "Seconds"), "wait_for_pause"], badges=True,
           zoom=[win.properties.widget(), "wait_for_pause"])
    p.check("① Seconds says {pause}, with no red line, and the flow holds it",
            error_shown(win) == "" and tab.doc.blocks["wait_for_pause"]["config"]["seconds"] == "{pause}")

    # --- (2) run it: each Wait waits what it was given ---------------------------------------------------------
    p.step("Click an empty part of the canvas, so the Log shows every block's lines, and press ▶ Run now. The Run "
           "dialog shows the parameter pause: press Run.")
    p.note("While a Wait waits, the line at its bottom counts down the time left. That countdown, which you asked "
           "for too, has a proof of its own: “While a Wait block waits, it shows the time left”.")
    click_empty(tab)

    def look_at_the_dialog(dialog):
        p.shot(dialog, "The Run dialog: pause is 2.0, the flow's value. Run leaves it so.",
               highlight=[dialog.edits["pause"]])

    first = run_now(p, win, qtbot, tab, look=look_at_the_dialog)
    said = {block: waiting(win, first, block)["message"] for block in ("wait_for_value", "wait_for_pause")}
    lines = log_lines(win, list(said.values()), f"run #{first} · ")
    run_1 = [(win.log.text, f"run #{first} · "), (win.log.text, f"DONE run #{first} ")]
    p.shot(win, "The run ended with success. ① In the Log, wait_for_value says waiting 1.5 s, the number that arrived "
                "at its value input, and under it wait_for_pause says waiting 2 s, the parameter pause.",
           highlight=[lines], badges=True, zoom=run_1 + lines)
    p.check("The run ended with success, all four blocks too",
            tab.runs[-1].status == "success" and all(s == "success" for s in statuses(tab).values()))
    p.check("① wait_for_value's Log line says waiting 1.5 s",
            said["wait_for_value"].startswith("waiting 1.5 s, until "))
    p.check("① wait_for_pause's Log line says waiting 2 s", said["wait_for_pause"].startswith("waiting 2 s, until "))
    p.check(f"And they waited so: wait_for_value ended {waited(win, first, 'wait_for_value'):.2f} s after its line, "
            f"wait_for_pause {waited(win, first, 'wait_for_pause'):.2f} s after its own",
            1.5 - 0.05 <= waited(win, first, "wait_for_value") < 1.5 + 2
            and 2 - 0.05 <= waited(win, first, "wait_for_pause") < 2 + 2)

    p.step("Press ▶ Run now again. In the Run dialog, type 0.5 over pause, then press Run.")

    def look_at_the_new_pause(dialog):
        p.shot(dialog, "pause is now 0.5, for this run only (Reset puts back the flow's value).",
               highlight=[dialog.edits["pause"]])

    second = run_now(p, win, qtbot, tab, {"pause": "0.5"}, look_at_the_new_pause)
    said = {block: waiting(win, second, block)["message"] for block in ("wait_for_value", "wait_for_pause")}
    lines = log_lines(win, list(said.values()), f"run #{second} · ")
    run_2 = [(win.log.text, f"run #{second} · "), (win.log.text, f"DONE run #{second} ")]
    p.shot(win, "The second run: ① wait_for_value waits 1.5 s again, and wait_for_pause follows the new pause: "
                "waiting 0.5 s.", highlight=[lines], badges=True, zoom=run_2 + lines)
    p.check("The second run ended with success", tab.runs[-1].status == "success")
    p.check("① wait_for_value says waiting 1.5 s again", said["wait_for_value"].startswith("waiting 1.5 s, until "))
    p.check("① wait_for_pause says waiting 0.5 s: the pause typed in the Run dialog",
            said["wait_for_pause"].startswith("waiting 0.5 s, until "))
    p.check(f"And it waited so: it ended {waited(win, second, 'wait_for_pause'):.2f} s after its line",
            0.5 - 0.05 <= waited(win, second, "wait_for_pause") < 0.5 + 2)
    p.check("The flow still says pause is 2: the 0.5 was for that run",
            tab.doc.data["params"]["pause"]["value"] == 2)

    # --- (4) the clear failures ----------------------------------------------------------------------------------
    p.step("Make two mistakes on purpose. On the Parameters tab, press Add and name the new parameter value: "
           "wait_for_value's {value} could now be either. Then click wait_for_pause and type {pasue}, a typo, into "
           "its Seconds.")
    click_inspector_tab(win, win.parameters)
    click_button(win, "Add")
    table = win.parameters.table
    type_in_cell(table, 1, 0, "value")
    p.shot(win, "① The flow now has a parameter named value, next to pause.",
           highlight=[(table, 1)], badges=True, zoom=parameters_zoom(win))
    p.check("① The flow has the parameters pause and value", list(tab.doc.data["params"]) == ["pause", "value"])
    click_block(win, "wait_for_pause")
    type_in(field(win, "Seconds"), "{pasue}")

    p.step("Press Validate in the toolbar (F7).")
    said = validate(p, win, monkeypatch, "Validate's box names both mistakes, each with its block and setting: "
                                         "{value} could be the parameter value or the input value: rename one; "
                                         "{pasue} is neither a flow parameter nor an input of this block.")
    p.check(f"It says “{BOTH}”", BOTH in said)
    p.check(f"It says “{NEITHER}”", NEITHER in said)

    p.step("Put both right: on the Parameters tab, click value and press Remove; type {pause} back into "
           "wait_for_pause's Seconds. Press Validate again.")
    click_inspector_tab(win, win.parameters)
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, table.visualItemRect(table.item(1, 0)).center())
    click_button(win, "Remove")
    click_block(win, "wait_for_pause")
    type_in(field(win, "Seconds"), "{pause}")
    press(win, win.validate_action)
    QTest.qWait(100)
    bar = win.statusBar()
    message = QRect(bar.x() + 4, bar.y() + 4, bar.fontMetrics().horizontalAdvance(bar.currentMessage()) + 8,
                    bar.height() - 8)
    p.shot(win, "No box this time: ① the status bar, at the bottom of the window, says waits.yaml: no problems found.",
           highlight=[message], badges=True, zoom=[message.adjusted(0, 0, 120, 0)])
    p.check("① Validate finds no problems now: the status bar says “waits.yaml: no problems found”",
            bar.currentMessage() == "waits.yaml: no problems found" and win.message_boxes == [])

    p.step("Click make_number and type output = 'abc' over its code: a text that is not a number. Click an empty "
           "part of the canvas, press ▶ Run now, then Run.")
    click_block(win, "make_number")
    set_code(win, "output = 'abc'")
    click_empty(tab)
    third = run_now(p, win, qtbot, tab)
    told = error_of(win, third, "wait_for_value")
    p.shot(win, "On the canvas: ① wait_for_value failed (red); ② wait_for_pause did not run: skipped, as something "
                "before it failed.", highlight=["wait_for_value", "wait_for_pause"], badges=True, zoom=LINE)
    line = (win.log.text, "gave 'abc', which is not a number")
    p.shot(win, "③ The Log says why: Seconds: {value} gave 'abc', which is not a number. Under it, it shows what "
                "arrived at the value input: abc.",
           highlight=[line], badges=[3], zoom=[line, (win.log.text, f"run #{third} failed")])
    p.check(f"③ The Log says “{NOT_A_NUMBER}”", told == NOT_A_NUMBER)
    p.check("① wait_for_value failed before it waited: no line of it says waiting in this run",
            statuses(tab)["wait_for_value"] == "failed" and not says_waiting(win, third, "wait_for_value"))
    p.check("② wait_for_pause did not run: it shows skipped, as something before it failed",
            statuses(tab)["wait_for_pause"] == "skipped" and not says_waiting(win, third, "wait_for_pause"))

    p.step("Connect make_number's output to wait_for_value's run after instead: drag it onto run after, then click "
           "the connection into value and press Delete. Seconds still says {value}. Run it again.")
    p.note("This is the other way you described: the Wait connected to run after only, with its seconds typed. "
           "Here Seconds was left at {value}, which now has nothing to take its number from.")
    drag_port_to_port(tab, "make_number", "output", "wait_for_value", "after")
    win.activateWindow()  # (made active first: becoming active, the canvas would let go of the connection clicked)
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=5_000)
    delete_connection(win, tab, qtbot, "make_number.output -> wait_for_value.value")
    edges = tab.doc.edges()
    p.shot(win, "① make_number's output now goes into ② wait_for_value's run after; its value input has nothing "
                "connected, and its bottom line still says ⏱ waits {value} s.",
           highlight=["make_number", "wait_for_value"], badges=True, zoom=["make_number", "wait_for_value"])
    p.check("① make_number's output now goes to wait_for_value's run after, and nothing into its value",
            ("make_number", "output", "wait_for_value", "after") in edges
            and not any(e[2:] == ("wait_for_value", "value") for e in edges))
    click_empty(tab)
    fourth = run_now(p, win, qtbot, tab)
    told = error_of(win, fourth, "wait_for_value")
    line = (win.log.text, "nothing is connected to the input value")
    p.shot(win, "wait_for_value failed again, and ③ the Log says: Seconds: {value}: nothing is connected to the input "
                "value. What arrived came in at run after, which only says when the block starts.",
           highlight=[line], badges=[3], zoom=[line, (win.log.text, f"run #{fourth} failed")])
    p.check(f"③ The Log says “{NOT_CONNECTED}”", told == NOT_CONNECTED)
    p.check("wait_for_value failed before it waited", statuses(tab)["wait_for_value"] == "failed"
            and not says_waiting(win, fourth, "wait_for_value"))

    # --- (3) other kinds of settings: a Chart's title, width and columns ---------------------------------------
    chart_flow = write(tmp_path / "sales-chart.yaml", CHART)
    p.step("Open sales-chart.yaml and click its Chart block, chart. Its Title is {region} sales, its Width {w} and "
           "its Y {col}: a text, a whole number and a list, each from a flow parameter.")
    tab = open_flow(win, monkeypatch, chart_flow)
    click_block(win, "chart")
    settings = [field(win, label) for label in ("Y", "Title", "Width")]
    p.shot(win, "chart's settings: ① Y (the columns it draws) {col}, ② Title {region} sales, ③ Width {w}.",
           highlight=settings, badges=True, zoom=[win.properties.widget(), *CHART_LINE])
    p.check("① Y says {col}, ② Title {region} sales and ③ Width {w}, with no red line",
            [s.text() for s in settings] == ["{col}", "{region} sales", "{w}"] and error_shown(win) == "")

    p.step("Click show, the Inspect block after chart: the Log shows its lines only. Press ▶ Run now, then Run.")
    click_block(win, "show")
    log_height = win.output.height()
    win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)  # the Log made taller (drag its top edge up)
    QTest.qWait(100)

    def look_at_the_parameters(dialog):
        p.shot(dialog, "The Run dialog lists the flow's parameters: region North, w 480 and col amount. Run "
                       "leaves them so.",
               highlight=[list(dialog.edits.values())])

    chart_run = run_now(p, win, qtbot, tab, look=look_at_the_parameters)
    QTest.qWait(300)  # the picture drawn
    picture = next(e["message"] for e in kept(win, lambda h: h.logs(chart_run)) if e.get("block") == "show")
    found = images(win.log.text)
    shown = hugged(picture_area(win, *found[-1]))
    p.shot(win, "The run ended with success, and show draws chart's picture in the Log: ① its title is North sales, "
                "and its bars are the column amount.", highlight=[shown], badges=True, zoom=[shown])
    reference = tmp_path / "north-sales.png"
    render_chart(pl.DataFrame({"month": ["Jan", "Feb", "Mar"], "amount": [120, 80, 45], "returns": [6, 9, 3]}),
                 reference, "bar", "month", ["amount"], "North sales", 480, 240)
    p.check("The run ended with success", tab.runs[-1].status == "success")
    p.check("① The picture is exactly the chart drawn with the title North sales and the column amount, 480 by 240",
            QImage(picture) == QImage(str(reference)))
    p.check("Its width is from w: 480, drawn at twice the pixels for sharp screens (960 pixels wide)",
            QImageReader(picture).size().width() == 2 * 480)
    p.note("To check the picture, this proof also draws the chart itself, with the title North sales, the column "
           "amount, 480 wide and 240 high, and compares the two pictures pixel by pixel.")

    win.resizeDocks([win.output], [log_height], Qt.Vertical)  # and back

    p.step("Click caption, the Python Code block, then the Data preview tab under the canvas.")
    p.note("Code takes no placeholders: Python Code, Polars, If and Inspect's message are as they were. SQL takes "
           "flow parameters only, as before; an HTML Template fills in its own {name}.")
    click_block(win, "caption")
    code = next(c for c in win.properties.findChildren(CodeEdit) if c.isVisible())
    written = (code, "output = 'Sales of {region}'")
    p.shot(win, "① caption's code: output = 'Sales of {region}'.", highlight=[written], badges=True,
           zoom=[written, field(win, "Name")])
    click_bottom_tab(win, win.preview)
    made = [(win.preview.text, "Sales of {region}"), win.preview.label]
    p.shot(win, "② What caption made: Sales of {region}, its braces left as they are.", highlight=[made],
           badges=[2], zoom=[made, win.bottom_tabs.tabBar()])
    p.check("② caption made the text “Sales of {region}”, braces and all",
            win.preview.text.toPlainText().strip() == "Sales of {region}"
            and kept(win, lambda h: h.blocks(chart_run))["caption"]["status"] == "success")
