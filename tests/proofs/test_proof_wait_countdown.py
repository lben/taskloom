"""Proof: while a Wait block waits, the canvas shows the time left at the bottom of the block, as a Schedule block
shows its next run; the Log says it once (stage 6 of the 2026-10-30 feedback). How proofs are written: README.md
here."""

import datetime as dt
import re
from collections import Counter

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QToolBar, QToolTip

from proofs.test_proof_editor_runs import file_menu, scheduler_service
from proofs.test_proof_run_output import click, setting
from proofs.test_proof_scheduler_misfire import wait_till
from proofs.test_proof_tab_plus import set_theme
from taskloom.editor import theme
from taskloom.editor.canvas import ROW_H
from tests.test_inspect_mermaid import contrast
from tests.test_wait_countdown import Watch

ASKED = ("WAIT block: The wait block has a value input, I should be able to use that value (cast it to float first) "
         "if I write {value} in the seconds field […] Display a countdown here as well for the seconds we are "
         "waiting. Then, on the countdown: “this shows up at the bottom of the block right? I am ok with it having "
         "the same look and feel of the scheduler block countdown”; and on the Log: “I don't want the log spamming "
         "the same message every second”.")
CLOCK = "⏱ "  # how the Schedule block's line begins
LEFT = re.compile(r"⏱ (?:(\d+)d )?(\d\d):(\d\d):(\d\d) left")
READABLE = 4.5  # the contrast text needs to read well (WCAG)

SCHEDULED = """\
blocks:
  every_morning:
    type: trigger.schedule
    config: {{at: "{at}"}}
    ui: {{x: 0, y: {low}}}
  pause:
    type: logic.wait
    config: {{seconds: 90}}
    ui: {{x: 230, y: 0}}
  morning_report:
    type: logic.python
    config: {{inputs: [], code: "output = 'report made'"}}
    ui: {{x: 460, y: 0}}
edges:
  - every_morning.start -> pause.after
  - pause.value -> morning_report.after
"""
GIVEN = """\
blocks:
  start_by_hand:
    type: trigger.manual
    ui: {x: 0, y: 0}
  how_long:
    type: logic.python
    config: {inputs: [], code: "output = 12"}
    ui: {x: 250, y: 0}
  pause:
    type: logic.wait
    config: {seconds: "{value}"}
    ui: {x: 500, y: 0}
edges:
  - start_by_hand.start -> how_long.after
  - how_long.output -> pause.value
"""


# --- what the canvas draws ------------------------------------------------------------------------------------------

class _Texts(QPainter):
    """A painter that keeps each text drawn with it: where, the text, its colour and its font."""

    def __init__(self, device):
        super().__init__(device)
        self.texts = []

    def drawText(self, *args):
        if isinstance(args[0], (QRectF, QRect)) and isinstance(args[-1], str):
            font = self.font()
            self.texts.append({"text": args[-1], "box": self.transform().mapRect(QRectF(args[0])),
                               "colour": self.pen().color().name(),
                               "font": (font.family(), round(font.pointSizeF(), 1), font.bold(), font.italic())})
        return super().drawText(*args)


def clock_line(tab, block) -> dict | None:
    """The block's ⏱ line, as the canvas draws it: its text, colour and font, how far above the bottom of the block
    it is, and whether it is the line just above the block's last line (its result, or its type)."""
    QApplication.processEvents()
    area = tab.scene.nodes[block].sceneBoundingRect()
    image = QImage(int(area.width()) + 1, int(area.height()) + 1, QImage.Format_ARGB32)
    image.fill(0)
    painter = _Texts(image)
    tab.scene.render(painter, QRectF(0, 0, area.width(), area.height()), area)
    painter.end()
    texts = sorted(painter.texts, key=lambda t: t["box"].center().y())
    found = [i for i, t in enumerate(texts) if t["text"].startswith(CLOCK)]
    if len(found) != 1:
        return None
    line = dict(texts[found[0]])
    line["above"] = round(area.height() - line["box"].center().y(), 1)
    line["just_above_last"] = found[0] == len(texts) - 2
    return line


def seen(tab, block) -> tuple[float, QColor, QColor]:
    """`block`'s ⏱ line as it is on the screen (a faded block's line is faded too): the contrast of its strongest
    pixel with the colour its block is filled with there, that pixel's colour, and the fill's."""
    line = clock_line(tab, block)
    area = tab.scene.nodes[block].sceneBoundingRect()

    def on_screen(scene_area: QRectF) -> list[QColor]:
        image = tab.view.viewport().grab(tab.view.mapFromScene(scene_area).boundingRect()).toImage()
        return [QColor.fromRgb(image.pixel(x, y)) for y in range(image.height()) for x in range(image.width())]

    fill = QColor(Counter(c.name() for c in on_screen(area)).most_common(1)[0][0])
    strongest = max(on_screen(line["box"].translated(area.topLeft())), key=lambda c: contrast(c, fill))
    return contrast(strongest, fill), strongest, fill


def says(tab, block) -> str | None:
    line = clock_line(tab, block)
    return line and line["text"]


def seconds_left(text: str | None) -> int | None:
    """The seconds a "⏱ 00:01:29 left" line says; None if it says no time left."""
    match = LEFT.fullmatch(text or "")
    if not match:
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def counting(text: str | None) -> bool:
    return seconds_left(text) is not None


def height(tab, block) -> float:
    return tab.scene.nodes[block].sceneBoundingRect().height()


# --- the Log --------------------------------------------------------------------------------------------------------

def lines_from(win, block: str) -> list[str]:
    """The Log's lines from `block`."""
    return [text for text in win.log.text.toPlainText().splitlines() if f"[{block}]" in text]


def until(line: str) -> dt.datetime:
    """When a “waiting 90 s, until 14:06:30” line says the wait ends (today, or tomorrow past midnight)."""
    at = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(re.search(r"until (\d\d:\d\d:\d\d)$", line)[1]))
    return at + dt.timedelta(days=1) if at < dt.datetime.now() - dt.timedelta(hours=12) else at


def the_wait(win, block: str, seconds: str) -> tuple[str | None, dt.datetime | None]:
    """`block`'s one line in the Log, if it has one line, and it is “waiting <seconds> s, until <time>”; when that
    says the wait ends."""
    lines = lines_from(win, block)
    if len(lines) != 1 or not re.search(rf"\[{block}\] INFO waiting {seconds} s, until \d\d:\d\d:\d\d$", lines[0]):
        return None, None
    return lines[0], until(lines[0])


# --- doing it -------------------------------------------------------------------------------------------------------

def open_flow(win, monkeypatch, path):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu(win, "Open…").trigger()
    return win.current


def press(win, action):
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(action), Qt.LeftButton)


def click_flow_tab(win, tab):
    bar = win.tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.tabs.indexOf(tab)).center())
    QTest.qWait(100)


def after_a_tick(qtbot) -> dt.datetime:
    """Wait until just after the countdowns moved on (each does just after a second of the clock): what is read then
    is what a screenshot taken right after shows. When that was."""
    moment = dt.datetime.now().replace(microsecond=0) + dt.timedelta(seconds=1, milliseconds=250)
    wait_till(qtbot, moment)
    return moment


def start_and_count(win, tab, qtbot, block: str) -> Watch:
    """Press ▶ Run now and wait until `block`'s line counts down (its run's program starts first)."""
    before = len(tab.runs)
    press(win, win.run_action)
    assert len(tab.runs) == before + 1, "Run now started no run"
    watch = Watch(tab, block)  # the line of `block` after each event of the run, as the editor showed it
    qtbot.waitUntil(lambda: counting(says(tab, block)) or watch.ended, timeout=60_000)
    return watch


def is_dark() -> bool:
    return QApplication.palette().window().color().lightness() < 128


def test_a_wait_block_counts_down_at_its_bottom_as_a_schedule_block_does(proof, env, win, qtbot, tmp_path,
                                                                          monkeypatch):
    p = proof("While a Wait block waits, it shows the time left, as a Schedule block shows its next run", ASKED)
    p.idea("A Wait block now has the line a Schedule block has at its bottom, just above its result. While it waits, "
           "that line counts down the time left, every second: “⏱ 00:01:29 left”, as a Schedule block counts down "
           "to its next run (“⏱ next run in 02:59:41”). When it is not waiting, the line says how long it waits: "
           "“⏱ waits 90 s”, or “⏱ waits {value} s” when its Seconds is the number arriving at its value input.")
    p.idea("The Log says it once, when the wait begins: “waiting 90 s, until 14:06:30”, how long and until when on "
           "the computer's clock. Nothing more while it waits: no line every second.")
    p.idea("When the wait ends (the time is up, you press Stop, the block's Timeout ends it, or the run fails), the "
           "line says again how long it waits, at once. A Wait of 0 seconds does not count down.")
    p.why_simplest("When a Wait block begins waiting, its run tells the editor once when the wait will end, and again "
                   "when it has ended. The editor already moves the Schedule blocks' countdowns on once a second, just "
                   "after each second of the computer's clock; that same refresh now also writes each waiting Wait "
                   "block's time left, worked out from when its wait ends. So a wait of an hour sends the editor two "
                   "messages, not one a second, and the time shown stays right even when the editor was busy for a "
                   "moment.")
    p.why_simplest("The line is the Schedule block's line: drawn by the same code, in the same place, font and "
                   "colour, and the time written the same way (00:01:29; from a day, 1d 01:01:05). A Wait block "
                   "always has it, so the block keeps its size when it begins waiting.")
    p.alternative("A Log line, or a message from the run, every second",
                  "You did not want the Log spamming the same message every second. A message each second from the "
                  "run to the editor would also be traffic for nothing: the end time, sent once, is enough to count "
                  "down from.")
    p.alternative("The editor counting down by itself from Seconds, from when the block begins",
                  "With {value} or a {parameter} the editor does not know the number: it arrives in the run. The run "
                  "sends the end time once that number is filled in, so the countdown is right whatever Seconds "
                  "says.")
    p.alternative("A line that shows only while it waits",
                  "The block would grow taller when it begins waiting and shrink when it ends, and could cover a "
                  "block placed just below it. A line that is always there keeps the block's size, and tells you how "
                  "long it waits before you run it.")
    p.fits("It is the Schedule block's countdown line, refreshed by the same timer, just after each second of the "
           "clock, and it reads well in Light and in Dark as that line does: it is drawn in the theme's text "
           "colour.")
    p.fits("It works for the runs the editor starts: ▶ Run now, and the block's menu (Run only this block, Run from "
           "this block on). The Log line is an ordinary line of the block's, kept with the run in Run history, and "
           "printed by taskloom run.")
    p.fits("Seconds takes placeholders, as the text and number settings of every block now do: {value} for the "
           "number arriving at the block's value input, or {name} for a flow parameter. The line shows Seconds as "
           "written; the countdown, the number that arrived.")

    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings writes Windows' Startup script there)
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    due = (dt.datetime.now() + dt.timedelta(hours=3)).replace(second=0, microsecond=0)
    scheduled = tmp_path / "morning-report.yaml"
    # every_morning one row lower: the Wait block has one row of ports more, so the two bottoms are level.
    scheduled.write_text(SCHEDULED.format(at=f"{due:%Y-%m-%d %H:%M}", low=ROW_H), encoding="utf-8")
    given = tmp_path / "wait-for-value.yaml"
    given.write_text(GIVEN, encoding="utf-8")
    log = win.log.text
    both = ["pause", "every_morning"]
    try:
        # --- 1. The line, before any run ------------------------------------------------------------------------
        p.step("Open the flow morning-report.yaml (File > Open…) and save it (File > Save), with the scheduler "
               f"service on. Its Schedule block every_morning starts it once, at {due:%H:%M}: pause, a Wait block, "
               "waits 90 seconds, then morning_report runs.")
        p.note("For this proof the real scheduler service is started (as from a terminal), so that every_morning "
               "counts down to its next run; it is stopped at the end.")
        with scheduler_service(win.home):
            tab = open_flow(win, monkeypatch, scheduled)
            file_menu(win, "Save").trigger()  # saved with a Schedule block: on the scheduler service's list
            qtbot.waitUntil(lambda: win.scheduler_action.text() == "Scheduler service on", timeout=60_000)
            qtbot.waitUntil(lambda: (says(tab, "every_morning") or "").startswith("⏱ next run in"), timeout=20_000)
            after_a_tick(qtbot)
            wait_line, schedule_line = clock_line(tab, "pause"), clock_line(tab, "every_morning")
            p.shot(win, f"① pause's line, at its bottom: “{wait_line['text']}”. ② every_morning's line, at its "
                        f"bottom: “{schedule_line['text']}”, counting down to {due:%H:%M}. The same line, in the same "
                        "place, font and colour.",
                   highlight=both, badges=True, zoom=both)
            p.check("① pause's line says “⏱ waits 90 s”", wait_line["text"] == "⏱ waits 90 s")
            p.check("② every_morning's line says “⏱ next run in …”, counting down to its run",
                    schedule_line["text"].startswith("⏱ next run in "))
            p.check(f"Both are the line just above their block's last line, {wait_line['above']:g} pixels above the "
                    "bottom of their block",
                    wait_line["just_above_last"] and schedule_line["just_above_last"]
                    and abs(wait_line["above"] - schedule_line["above"]) <= 0.5)
            family, size, bold, italic = wait_line["font"]
            p.check(f"Both are in the same font ({family}, {size:g} points, {'bold' if bold else 'not bold'}, "
                    f"{'italic' if italic else 'upright'}) and the same colour ({wait_line['colour']})",
                    (wait_line["font"], wait_line["colour"]) == (schedule_line["font"], schedule_line["colour"]))
            size_before = height(tab, "pause")

            # --- 2. It counts down --------------------------------------------------------------------------------
            p.step("Press ▶ Run now in the toolbar. The flow has no Manual Start, so it runs from every_morning, as "
                   "when its time comes: pause begins waiting.")
            watch = start_and_count(win, tab, qtbot, "pause")
            first_at = after_a_tick(qtbot)
            first, said, ends = says(tab, "pause"), *the_wait(win, "pause", "90")
            p.shot(win, f"① pause is running (its outline), and its line counts down: “{first}”, at "
                        f"{first_at:%H:%M:%S}. ② every_morning goes on counting down to its run.",
                   highlight=both, badges=True, zoom=both)
            real = round((ends - first_at).total_seconds()) if ends else None
            p.check("① pause's line counts down", counting(first))
            p.check(f"It is the time left: {seconds_left(first)} s, and the Log says the wait ends at "
                    f"{ends and f'{ends:%H:%M:%S}'}, {real} s after {first_at:%H:%M:%S}",
                    real is not None and counting(first) and abs(real - seconds_left(first)) <= 2)

            p.step("Wait 4 seconds and look again.")
            wait_till(qtbot, first_at + dt.timedelta(seconds=4))
            second_at = dt.datetime.now()
            second = says(tab, "pause")
            p.shot(win, f"① 4 seconds later, at {second_at:%H:%M:%S}: “{second}”.",
                   highlight=both, badges=True, zoom=both)
            fewer = (seconds_left(first) or 0) - (seconds_left(second) or 0)
            passed = (second_at - first_at).total_seconds()
            p.check(f"It counted down by the time that passed: {fewer} s less, {passed:.1f} s later",
                    counting(second) and abs(fewer - passed) <= 1.5)
            p.check(f"The block did not change size: as high as before the run ({size_before:g} pixels)",
                    height(tab, "pause") == size_before)

            # --- 3. The Log says it once ----------------------------------------------------------------------------
            p.step("Look at the Log, under the canvas.")
            waited = (dt.datetime.now() - first_at).total_seconds() + 1
            p.shot(win, "① The one line pause wrote in the Log: how long it waits, and until when. The close-up is "
                        "the run's lines so far.",
                   highlight=[(log, " waiting 90 s")], badges=True,
                   zoom=[(log, f"run #{watch.run.run_id} · "), (log, " waiting 90 s")])
            p.check(f"① The Log has one line from pause: “waiting 90 s, until {ends and f'{ends:%H:%M:%S}'}”",
                    said is not None)
            p.check(f"Only that one, also after {waited:.0f} seconds of waiting", lines_from(win, "pause") == [said])

            # --- 4. Stop ----------------------------------------------------------------------------------------------
            p.step("Press ■ Stop in the toolbar.")
            press(win, win.stop_action)
            qtbot.waitUntil(lambda: watch.ended, timeout=30_000)
            QTest.qWait(2000)  # (two ticks of a countdown: pause's line may not count)
            p.shot(win, f"① The run was cancelled: pause says “cancelled”, and its line “{says(tab, 'pause')}” again. "
                        "Two seconds later, it still does not count down; ② every_morning's line still counts down to "
                        "its run.",
                   highlight=both, badges=True, zoom=both)
            p.check(f"① Run #{watch.run.run_id} was cancelled, and pause shows it",
                    watch.run.status == "cancelled" and tab.scene.nodes["pause"].status == "cancelled")
            p.check("At once: when the editor showed pause cancelled, its line already said “⏱ waits 90 s”",
                    watch.after_finished() == "⏱ waits 90 s")
            p.check("Two seconds later it still says “⏱ waits 90 s”: no Wait block counts down any more (no line says "
                    "“… left”); ② every_morning's line still says “⏱ next run in …”",
                    says(tab, "pause") == "⏱ waits 90 s"
                    and not any(counting(says(tab, b)) for b in tab.scene.nodes)
                    and says(tab, "every_morning").startswith("⏱ next run in "))
            p.check("The block is still the same size", height(tab, "pause") == size_before)
            p.check("The Log still has just that one line from pause", lines_from(win, "pause") == [said])

            # --- 5. Seconds {value} ---------------------------------------------------------------------------------
            p.step("Open the flow wait-for-value.yaml (File > Open…). how_long, a Python block, makes 12 and gives it "
                   "to pause's value input; pause's Seconds is {value}.")
            tab2 = open_flow(win, monkeypatch, given)
            p.shot(win, f"① how_long gives its output to pause's value input. ② pause's line says "
                        f"“{says(tab2, 'pause')}”: its Seconds, as written.",
                   highlight=["how_long", "pause"], badges=True, zoom=["how_long", "pause"])
            p.check("② Before the run, pause's line says “⏱ waits {value} s”", says(tab2, "pause") == "⏱ waits {value} s")

            p.step("Click pause.")
            click(tab2, "pause")
            label, seconds_field = setting(win, "Seconds")
            p.shot(win, "① In Properties, pause's Seconds is {value}.", highlight=[[label, seconds_field]], badges=True,
                   zoom=[label, seconds_field])
            p.check("① Its Seconds is {value}", seconds_field.text() == "{value}")

            p.step("Click an empty part of the canvas, then press ▶ Run now.")
            click(tab2)
            watch2 = start_and_count(win, tab2, qtbot, "pause")
            at = after_a_tick(qtbot)
            text, said2, ends2 = says(tab2, "pause"), *the_wait(win, "pause", "12")
            p.shot(win, f"① how_long made 12, and ② pause counts down those 12 seconds: “{text}”.",
                   highlight=["how_long", "pause"], badges=True, zoom=["how_long", "pause"])
            real2 = round((ends2 - at).total_seconds()) if ends2 else None
            p.check("① how_long's result is 12", "12" in tab2.scene.values.get("how_long", {}).get("output", ""))
            p.check(f"② pause counts down the 12 seconds that arrived: {seconds_left(text)} s left, and the Log says "
                    f"the wait ends {real2} s later, at {ends2 and f'{ends2:%H:%M:%S}'}",
                    counting(text) and real2 is not None and abs(real2 - seconds_left(text)) <= 2
                    and seconds_left(text) < 12)

            p.step("Wait until the 12 seconds are up.")
            qtbot.waitUntil(lambda: watch2.ended, timeout=60_000)
            QTest.qWait(1500)
            p.shot(win, f"① The time is up: pause succeeded, and its line says “{says(tab2, 'pause')}” again.",
                   highlight=["pause"], badges=True, zoom=["how_long", "pause"])
            p.check("① The run succeeded, and pause shows it",
                    watch2.run.status == "success" and tab2.scene.nodes["pause"].status == "success")
            p.check("At once, when the editor showed pause's result, its line said “⏱ waits {value} s” again",
                    watch2.after_finished() == "⏱ waits {value} s")
            p.shot(win, "① In the Log, pause's one line, between the run's start and its success: how long it "
                        "waited, and until when.",
                   highlight=[(log, " waiting 12 s")], badges=True,
                   zoom=[(log, f"run #{watch2.run.run_id} · "), (log, f"run #{watch2.run.run_id} success")])
            p.check(f"① The Log has one line from pause: “waiting 12 s, until {ends2 and f'{ends2:%H:%M:%S}'}”, and no "
                    "other", said2 is not None and lines_from(win, "pause") == [said2])

            # --- 6. Dark ---------------------------------------------------------------------------------------------
            p.step("Choose Tools > Settings…, set Theme to Dark and press Save.")
            p.note("This is still wait-for-value.yaml, where pause's run succeeded. In morning-report.yaml pause still "
                   "shows its cancelled run, and the canvas fades a cancelled block as a whole, its line included.")
            set_theme(p, win, "Dark", shoot=False)
            dark_wait = says(tab2, "pause")
            ratio, glyph, fill = seen(tab2, "pause")
            p.shot(win, f"In Dark: ① pause's line, “{dark_wait}”, light on its dark block.",
                   highlight=["pause"], badges=True, zoom=["how_long", "pause"])
            p.check("The editor is dark", is_dark())
            p.check(f"① pause's line reads well: as it is on the screen, its strongest pixels ({glyph.name()}) have a "
                    f"contrast of {ratio:.1f} to 1 with its block ({fill.name()}), at least {READABLE} to 1",
                    dark_wait == "⏱ waits {value} s" and ratio >= READABLE)

            p.step("Click the tab morning-report.yaml and press ▶ Run now.")
            click_flow_tab(win, tab)
            watch3 = start_and_count(win, tab, qtbot, "pause")
            after_a_tick(qtbot)
            dark_wait, dark_schedule = clock_line(tab, "pause"), clock_line(tab, "every_morning")
            ratio, glyph, fill = seen(tab, "pause")
            p.shot(win, f"In Dark, pause counts down: ① “{dark_wait['text']}”, beside ② every_morning's "
                        f"“{dark_schedule['text']}”.", highlight=both, badges=True, zoom=both)
            p.check(f"① The countdown reads well too: as it is on the screen, its strongest pixels ({glyph.name()}) "
                    f"have a contrast of {ratio:.1f} to 1 with its block ({fill.name()}); it is drawn in the same colour "
                    f"({dark_wait['colour']}) and font as ② every_morning's line",
                    counting(dark_wait["text"]) and ratio >= READABLE
                    and (dark_wait["colour"], dark_wait["font"]) == (dark_schedule["colour"], dark_schedule["font"]))

            p.step("Press ■ Stop, then set Theme back to System (Tools > Settings…) and press Save.")
            press(win, win.stop_action)
            qtbot.waitUntil(lambda: watch3.ended, timeout=30_000)
            set_theme(p, win, "System", shoot=False)
            after_a_tick(qtbot)
            p.shot(win, f"The editor is light again, and ① pause's line says “{says(tab, 'pause')}”.",
                   highlight=["pause"], badges=True, zoom=both)
            p.check("① The run was cancelled, and pause's line says “⏱ waits 90 s” again",
                    watch3.run.status == "cancelled" and says(tab, "pause") == "⏱ waits 90 s")
            p.check("The Theme is System again, and the editor light",
                    theme.chosen(win.settings) == "System" and not is_dark())

        # --- the same, without the editor ---------------------------------------------------------------------------
        p.step("Run wait-for-value.yaml from a command line: taskloom run wait-for-value.yaml.")
        p.note("The countdown is the editor's. A run without the editor (taskloom run, or the scheduler service when a "
               "flow's time comes) has just this one line in its Log. A run shown from Run history shows no countdown "
               "either, even one still waiting (a run the scheduler service started, say): when its wait ends is "
               "told only to the editor that started it, and is not kept in the history. Other blocks' Timeout, and "
               "Ask User's, do not count down.")
        result = env.taskloom("run", given, encoding="utf-8", errors="replace")
        printed = (result.stdout + result.stderr).strip().splitlines()  # (a run's lines go to stderr)
        p.text("taskloom run printed", "\n".join(printed))
        p.check("It succeeded, and printed one line about pause's wait: “waiting 12 s, until …”",
                result.returncode == 0
                and len([t for t in printed if "[pause]" in t and "waiting" in t]) == 1
                and any(re.search(r"\[pause\] INFO: waiting 12 s, until \d\d:\d\d:\d\d$", t) for t in printed))
    finally:  # the Theme and the application's look as the proof found them; no run left going
        for run in [r for t in win.tabs_list() for r in t.runs if r.running]:  # (a proof that failed mid-run)
            run.proc.kill()
            run.proc.waitForFinished(5000)
        win.settings.setValue("theme", "System")
        theme.apply("System")
        if app.style().name() != as_found[0]:
            QApplication.setStyle(as_found[0])
        QApplication.setPalette(as_found[1])
        QToolTip.setPalette(as_found[2])
        QApplication.processEvents()
