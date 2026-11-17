"""Proof: Ctrl + and Ctrl - zoom the pane under the mouse pointer, wherever the keyboard focus is (ZM-1); each kind of
pane keeps its size when the editor is started again (ZM-3); find highlights stay right after a zoom (ZM-5). The
promises are docs/promises/zoom.yaml. How proofs are written: README.md here."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from proofs.conftest import SCREEN, WINDOW
from proofs.test_proof_failure_inputs import finished, open_flow, press_run_now
from proofs.test_proof_html_template_formatting import click_block
from proofs.test_proof_preview_long_error import click_tab, in_window
from proofs.test_proof_retention_per_start import drag_up
from tests.test_code_editor import clean, code_box
from tests.test_find_bar import behind, differs, open_find, plain, where
from tests.test_zoom_panes import (EQUALS, IN, NORMAL, OUT, bigger, canvas_pane, list_pane, pointer_over, press,
                                   text_pane, top_line, wheel)

REPO = Path(__file__).resolve().parents[2]
ASKED = "Ctrl + / Ctrl - zooms the pane under the mouse pointer"
WORD = "late"  # what the find bar looks for in the Log
CODE = """\
lines = []
for number in range(1, 121):
    state = "late" if number % 4 == 0 else "on time"
    lines.append(f"order {number:03d} is {state}")
print("\\n".join(lines))
orders = "\\n".join(lines)
"""
FLOW = {"blocks": {"start_by_hand": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
                   "check_orders": {"type": "logic.python", "ui": {"x": 260, "y": 0},
                                    "config": {"inputs": [], "outputs": ["orders"], "code": CODE}}},
        "edges": ["start_by_hand.start -> check_orders.after"]}

# The editor started again, in a process of its own, as `taskloom editor` does: it reopens the flow; the script
# double-clicks the run in Run history and clicks check_orders, then measures the panes as this proof does, saves a
# picture of the window with the Log in front and one with Data preview in front, and ends.
AGAIN = r"""
import json, os, sys, time, traceback
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

# On the proofs' offscreen screen (screen.json, in the folder it runs in), in the proof's font and look.
app = QApplication(["taskloom", "-platform", "offscreen:configfile=screen.json"])
if sys.platform == "win32" and "Segoe UI" in QFontDatabase.families():
    app.setFont(QFont("Segoe UI", 9))
elif sys.platform == "linux" and "DejaVu Sans" in QFontDatabase.families():
    app.setFont(QFont("DejaVu Sans", 9))
app.setStyle(sys.argv[2])

from taskloom import cli
from taskloom.editor import window
from tests.test_code_editor import code_box
from tests.test_zoom_panes import list_pane, text_pane

boxes, editors, seen = [], [], {}
for name in ("information", "warning", "question", "critical"):  # (a message box would wait for a click)
    setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: boxes.append([_n, *map(str, a[1:3])])
                                            or QMessageBox.No))
window.MainWindow.start_scheduler = lambda self, by_you=False: None  # no scheduler service from a proof
show = window.MainWindow.show


def shown(self):
    show(self)
    editors.append(self)


window.MainWindow.show = shown


def wait(condition, what):
    deadline = time.monotonic() + 30
    while not condition():
        assert time.monotonic() < deadline, "waited 30 s for " + what
        QTest.qWait(50)


def click_tab(editor, text):
    bar = editor.bottom_tabs.tabBar()
    index = next(i for i in range(bar.count()) if bar.tabText(i) == text)
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(index).center())
    QTest.qWait(300)


def place(editor, widget):
    at = widget.mapTo(editor, QPoint(0, 0))
    return [at.x(), at.y(), widget.width(), widget.height()]


def look(app):  # instead of waiting for you to close it: what it shows, then it ends
    editor = editors[0]
    wait(lambda: editor.current is not None, "the flow")
    editor.resize(*json.loads(sys.argv[3]))
    QTest.qWait(500)
    tab = editor.current
    seen["flows"] = [t.doc.title for t in editor.tabs_list()]
    seen["canvas"] = [round(tab.view.zoom_percent(), 3), tab.view.zoom_bar.percent.text()]
    table = editor.history_panel.table
    wait(lambda: table.rowCount() > 0, "Run history")
    click_tab(editor, "Run history")
    at = table.visualItemRect(table.item(0, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # (QTest's is only the second click)
    QTest.qWait(300)
    view = tab.view
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier,
                     view.mapFromScene(tab.scene.nodes["check_orders"].sceneBoundingRect().center()))
    QTest.qWait(300)
    click_tab(editor, "Log")
    box = code_box(editor)
    panes = {"Log": (editor.log.text, text_pane), "code box": (box, text_pane),
             "Blocks list": (editor.palette_panel.tree, list_pane)}
    seen["sizes"] = {name: list(measure(name, widget).size()) for name, (widget, measure) in panes.items()}
    seen["points"] = {name: widget.font().pointSizeF() for name, (widget, _) in panes.items()}
    seen["parts"] = {name: place(editor, widget) for name, (widget, _) in panes.items()}
    seen["parts"]["zoom bar"] = place(editor, view.zoom_bar.percent)
    editor.grab().save(sys.argv[1])
    click_tab(editor, "Data preview")
    text = editor.preview.text
    wait(lambda: text.isVisible() and "order 120" in text.toPlainText(), "Data preview's text")
    seen["sizes"]["Data preview"] = list(text_pane("Data preview", text).size())
    seen["points"]["Data preview"] = text.font().pointSizeF()
    seen["parts"]["Data preview"] = place(editor, text)
    editor.grab().save(sys.argv[1].replace(".png", "-preview.png"))
    return 0


QApplication.exec = look
try:
    code = cli.main(["editor"])
except BaseException:  # (the editor's crash log would take it: said here instead)
    traceback.print_exc(file=sys.stdout)
    sys.stdout.flush()
    os._exit(2)
print(json.dumps({"seen": seen, "boxes": boxes, "code": code}), flush=True)
os._exit(0)
"""


def click_into(qtbot, widget, at: QPoint):
    """Click into a text box at `at` (in its text area), as you would: the keyboard focus is in it."""
    QTest.mouseClick(widget.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    qtbot.waitUntil(widget.hasFocus, timeout=5_000)


def pts(widget) -> float:
    """The size of the pane's text, in points."""
    return widget.font().pointSizeF()


def shown_whole(view, word: str) -> list[int]:
    """Where each `word` starts that the text box shows whole."""
    return [m.start() for m in re.finditer(word, view.document().toPlainText())
            if view.viewport().rect().contains(where(view, m.start(), len(word)))]


def not_highlighted(view, word: str, starts: list[int]) -> list[int]:
    """Of the matches starting at `starts`, those painted as the text box's plain background: not highlighted."""
    QTest.qWait(100)
    back = plain(view)
    return [s for s in starts if not differs(behind(view, where(view, s, len(word))), back)]


def around(win, view, word: str, starts) -> list[QRect]:
    """The matches starting at `starts`, in the window's pixels (one outline around them all, in a shot)."""
    origin = view.viewport().mapTo(win, QPoint(0, 0))
    return [where(view, s, len(word)).adjusted(-2, -1, 2, 1).translated(origin) for s in sorted(starts)]


def top(win, widget, height: int, width: int = 700) -> QRect:
    """The top of a widget, in the window's pixels: `width` wide and `height` high (for a close-up)."""
    rect = in_window(win, widget, width)
    return QRect(rect.left(), rect.top(), rect.width(), min(height, rect.height()))


def zoom_lines(ini: str) -> list[str]:
    """editor.ini's [zoom] part: the steps each kind of pane is kept at."""
    lines = ini.splitlines()
    start = lines.index("[zoom]")
    part = [lines[start]]
    for line in lines[start + 1:]:
        if not line.strip() or line.startswith("["):
            break
        part.append(line)
    return part


def test_ctrl_plus_and_minus_zoom_the_pane_under_the_pointer_wherever_the_focus_is(proof, win, qtbot, tmp_path,
                                                                                    monkeypatch):
    p = proof("Ctrl + and Ctrl - zoom the pane under the mouse pointer", ASKED)
    p.idea("Ctrl + and Ctrl - make the pane under the mouse pointer bigger or smaller, wherever you are typing, and "
           "Ctrl 0 puts it back to its normal size. Each kind of pane keeps its size for the next time.")
    p.idea("Each press is one step: the pane's text one point bigger or smaller, from half its normal size up to three "
           "times it. Ctrl = does what Ctrl + does (it is the same key, without Shift), and so do the number pad's "
           "keys. On macOS it is Cmd. Over a text pane, Ctrl and the mouse wheel do the same as the keys: a notch up "
           "is Ctrl +, a notch down is Ctrl -.")
    p.idea("The panes are the code boxes, the Log, Data preview, Run history, the Blocks list, the documentation and "
           "the canvas. The canvas zooms by its zoom bar's step, and keeps its zoom only until you close the flow.")
    p.idea("Only the size changes: the text, the selection, the top line in view and the find bar's highlights stay "
           "as they were.")
    p.why_simplest("One watcher, on the whole editor, sees each Ctrl +, -, = and 0 and each turn of the wheel with "
                   "Ctrl before any box does. It finds the pane the pointer is over and sets that pane's text one "
                   "point bigger or smaller. The key never reaches the box you are typing in, so nothing is typed. A "
                   "pane only has to say which kind it is.")
    p.why_simplest("What is kept is one number for each kind of pane, how many steps it is from normal, written when "
                   "you zoom. A pane that shows is given its kind's number: a code box made for another block too.")
    p.alternative("Zoom the pane that has the keyboard focus, as most programs do",
                  "You would first have to click into the pane, which moves the caret or the selection, and you could "
                  "not make the Log bigger while you type in a code box. You asked for the pane under the pointer.")
    p.alternative("One size for the whole editor",
                  "Everything would grow at once: the panes would push each other out of the window when you only "
                  "wanted to read the Log better.")
    p.alternative("A size for each pane in Tools > Settings",
                  "A window to open, a number to guess and Save before you see it. The keys show the size as you "
                  "press them, and it is kept just the same.")
    p.fits("Over the canvas, Ctrl + and Ctrl - do what the + and − of its zoom bar do, by the bar's step (5% unless "
           "you change it), and Ctrl 0 shows it at 100%. The mouse wheel over the canvas zooms it as before.")
    p.fits("The sizes are kept in editor.ini in your Taskloom folder, where the editor keeps its own preferences, such "
           "as the theme.")
    p.fits("Find (Ctrl+F) works as before at every size: its highlights follow the text as it grows or shrinks.")

    p.step("Open orders.yaml (File > Open…), press ▶ Run now, click check_orders once the run has ended, and drag "
           "the edge above the Log up, to see more of it. check_orders prints 120 orders, one in four late, and gives "
           "them as its output.")
    tab = open_flow(win, monkeypatch, tmp_path / "orders.yaml", FLOW)
    press_run_now(win)
    finished(qtbot, tab, 1)
    click_block(tab, "check_orders")
    click_tab(win.bottom_tabs.tabBar(), "Log")
    drag_up(win, win.output, 160)
    QTest.qWait(200)
    win.activateWindow()
    log, box, tree = win.log.text, code_box(win), win.palette_panel.tree
    panes = {"Log": text_pane("Log", log), "code box": text_pane("code box", box),
             "Blocks list": list_pane("Blocks list", tree)}

    def sizes() -> dict:
        return {name: pane.size() for name, pane in panes.items()}

    normal = sizes()
    normal_pts = {"Log": pts(log), "code box": pts(box), "Blocks list": pts(tree)}
    code = box.toPlainText()
    p.shot(win, "The panes at their normal size: ① the Log, with check_orders' lines, ② its code box in Properties, "
                "③ the Blocks list.",
           highlight=[log, box, tree], badges=True, zoom=[top(win, log, 300)])
    p.check("The run succeeded, and the Log shows check_orders' 120 orders",
            tab.runs[0].status == "success" and "order 120 is late" in log.toPlainText())

    p.step("Click into the code box, so that the keyboard focus is there. Put the mouse pointer over the Log and press "
           "Ctrl + twice.")
    click_into(qtbot, box, QPoint(box.viewport().width() - 20, 6))
    pointer_over(log, "Log")
    press(IN, IN)
    now = sizes()
    p.shot(win, f"① The Log, under the pointer, is two steps bigger: its text is {pts(log):g} points, not "
                f"{normal_pts['Log']:g}. ② The code box, where the caret is, keeps its size, and no + was typed in it. "
                "③ The Blocks list keeps its size.",
           highlight=[log, box, tree], badges=True, zoom=[top(win, log, 300)])
    p.check(f"① The Log is bigger: its text went from {normal_pts['Log']:g} to {pts(log):g} points (ZM-1)",
            bigger(now["Log"], normal["Log"]) and pts(log) == normal_pts["Log"] + 2)
    p.check("② The code box and ③ the Blocks list keep their size; the code box keeps its text and the keyboard "
            "focus, and the flow has no change to save (ZM-1)",
            now["code box"] == normal["code box"] and now["Blocks list"] == normal["Blocks list"]
            and box.toPlainText() == code and box.hasFocus() and not tab.doc.dirty)

    p.step("Click into the Log, so that the keyboard focus is there now. Put the pointer over the code box and press "
           "Ctrl = once (the key of +, without Shift).")
    log_zoomed = now["Log"]
    click_into(qtbot, log, QPoint(20, 6))
    pointer_over(box, "code box")
    press(EQUALS)
    now = sizes()
    p.shot(win, f"② The code box, under the pointer, is a step bigger: {pts(box):g} points, not "
                f"{normal_pts['code box']:g}. ① The Log, where the keyboard focus is, keeps the size you gave it. "
                "③ The Blocks list keeps its size.",
           highlight=[log, box, tree], badges=[1, 2, 3], zoom=[in_window(win, box)])
    p.check(f"② The code box is bigger: its text went from {normal_pts['code box']:g} to {pts(box):g} points (ZM-1)",
            bigger(now["code box"], normal["code box"]) and pts(box) == normal_pts["code box"] + 1)
    p.check("① The Log keeps the size you gave it and the keyboard focus, ③ the Blocks list its normal size (ZM-1)",
            now["Log"] == log_zoomed and log.hasFocus() and now["Blocks list"] == normal["Blocks list"])

    p.step("Put the pointer over the Blocks list and press Ctrl - once. The keyboard focus is still in the Log.")
    before = now
    pointer_over(tree, "Blocks list")
    press(OUT)
    now = sizes()
    p.shot(win, f"③ The Blocks list, under the pointer, is a step smaller: {pts(tree):g} points, not "
                f"{normal_pts['Blocks list']:g}. ① The Log and ② the code box keep their sizes.",
           highlight=[log, box, tree], badges=True, zoom=[top(win, tree, 380, tree.width())])
    p.check(f"③ The Blocks list is smaller: its text went from {normal_pts['Blocks list']:g} to {pts(tree):g} points "
            "(ZM-1)", bigger(normal["Blocks list"], now["Blocks list"]) and pts(tree) == normal_pts["Blocks list"] - 1)
    p.check("① The Log and ② the code box keep their sizes, and the keyboard focus stays in the Log (ZM-1)",
            now["Log"] == before["Log"] and now["code box"] == before["code box"] and log.hasFocus())

    p.step("Click the Data preview tab, under the canvas: it shows check_orders' output. Click into the code box, put "
           "the pointer over Data preview's text, hold Ctrl and turn the mouse wheel up two notches.")
    click_tab(win.bottom_tabs.tabBar(), "Data preview")
    preview = win.preview.text
    qtbot.waitUntil(lambda: preview.isVisible() and "order 120 is late" in preview.toPlainText(), timeout=10_000)
    shown = text_pane("Data preview", preview)
    preview_normal, preview_normal_pts = shown.size(), pts(preview)
    click_into(qtbot, box, QPoint(box.viewport().width() - 20, 6))
    before = sizes()
    pointer_over(preview, "Data preview")
    wheel(2)
    by_wheel, by_wheel_pts = shown.size(), pts(preview)
    p.shot(win, f"① Data preview's text, under the pointer, is two steps bigger: {by_wheel_pts:g} points, not "
                f"{preview_normal_pts:g}. ② The code box, where the keyboard focus is, keeps its size.",
           highlight=[preview, box], badges=True, zoom=[top(win, preview, 300)])
    p.check(f"① Two notches of the wheel up made Data preview's text two steps bigger: from {preview_normal_pts:g} to "
            f"{by_wheel_pts:g} points (ZM-1)",
            bigger(by_wheel, preview_normal) and by_wheel_pts == preview_normal_pts + 2)
    p.check("② The code box and the other panes keep their sizes (ZM-1)", sizes() == before)

    p.step("Press Ctrl 0, then Ctrl + twice. Then hold Ctrl and turn the wheel down one notch.")
    press(NORMAL)
    after_0, after_0_pts = shown.size(), pts(preview)
    press(IN)
    one_key = shown.size()
    press(IN)
    two_keys, two_keys_pts = shown.size(), pts(preview)
    wheel(-1)
    notch_down, preview_pts = shown.size(), pts(preview)
    p.text("Data preview's text, in points",
           f"normal: {preview_normal_pts:g}\nwheel up two notches: {by_wheel_pts:g}\nCtrl 0: {after_0_pts:g}\n"
           f"Ctrl + twice: {two_keys_pts:g}\nwheel down one notch: {preview_pts:g}")
    p.check("Ctrl 0 put it back to its normal size (ZM-1)", after_0 == preview_normal)
    p.check("Ctrl + twice gave it exactly the size the two notches up gave (ZM-1)", two_keys == by_wheel)
    p.check(f"One notch down gave exactly the size of one Ctrl +: {preview_pts:g} points (ZM-1)",
            notch_down == one_key and preview_pts == preview_normal_pts + 1)

    p.step("Put the pointer over the canvas and press Ctrl + twice. The keyboard focus is still in the code box.")
    canvas = canvas_pane(tab)
    opened_at = canvas.size()
    before = sizes()
    pointer_over(tab.view, "canvas")
    press(IN, IN)
    bar = tab.view.zoom_bar
    p.shot(win, f"① The zoom bar, at the top right of the canvas, says {bar.percent.text()}: each Ctrl + zoomed in by "
                f"the bar's step, {bar.step.value()}%, as its + does. ② The blocks are bigger.",
           highlight=[bar.percent, ["start_by_hand", "check_orders"]], badges=True,
           zoom=[in_window(win, bar).adjusted(-60, -10, 10, 40)])
    p.check(f"① The canvas went from {opened_at[0]:g}% to {canvas.size()[0]:g}%, two of the zoom bar's steps of "
            f"{bar.step.value()}%, and the bar says {bar.percent.text()} (ZM-1)",
            opened_at == (100,) and canvas.size() == (100 + 2 * bar.step.value(),)
            and bar.percent.text() == f"{100 + 2 * bar.step.value()}%")
    p.check("The code box, with the keyboard focus, and the other panes keep their sizes (ZM-1)",
            sizes() == before and box.hasFocus())

    p.step(f"Click into Data preview's text, press Ctrl+F and type {WORD}.")
    find = open_find(win, qtbot, preview)
    find.type(WORD)
    count, text, first_line = find.count(), preview.toPlainText(), top_line(preview)
    seen_before = shown_whole(preview, WORD)
    p.shot(win, f"① Each “{WORD}” in view is highlighted. ② The find bar, under Data preview's text, says {count}.",
           highlight=[around(win, preview, WORD, seen_before), find.count_label()], badges=True,
           zoom=[top(win, preview, 360)])
    p.check(f"Before the zoom, each of the {len(seen_before)} “{WORD}” in view is highlighted (as ZM-5 starts from)",
            seen_before and not_highlighted(preview, WORD, seen_before) == [])

    p.step("Leave the keyboard focus in the find bar's field, put the pointer over Data preview's text and press "
           "Ctrl - three times.")
    preview_before = shown.size()
    pointer_over(preview, "Data preview")
    press(OUT, OUT, OUT)
    seen_after = shown_whole(preview, WORD)
    came = sorted(set(seen_after) - set(seen_before))
    p.shot(win, f"Data preview's text is now two steps under its normal size ({pts(preview):g} points), and shows "
                f"more lines from the same top line. ① The “{WORD}” that came into view are highlighted like the "
                f"others. ② The find bar still says {find.count()}.",
           highlight=[around(win, preview, WORD, came or seen_after), find.count_label()], badges=True,
           zoom=[top(win, preview, 400)])
    p.check(f"Data preview's text is smaller: it went from {preview_pts:g} to {pts(preview):g} points, two under its "
            f"normal {preview_normal_pts:g} (ZM-1)",
            bigger(preview_before, shown.size()) and bigger(preview_normal, shown.size())
            and pts(preview) == preview_normal_pts - 2)
    p.check(f"{len(came)} “{WORD}” came into view with the zoom, and every one of the {len(seen_after)} in view is "
            "highlighted (ZM-5)", came and not_highlighted(preview, WORD, seen_after) == [])
    p.check(f"The find bar still says {count}, the keyboard focus is still in its field, the text is the same and the "
            f"top line is still “{first_line}” (ZM-5)",
            find.count() == count and find.field.hasFocus() and preview.toPlainText() == text
            and top_line(preview) == first_line)

    kept = {**sizes(), "Data preview": shown.size()}
    kept_pts = {"Log": pts(log), "code box": pts(box), "Blocks list": pts(tree), "Data preview": pts(preview)}
    zoomed_canvas = canvas.size()

    p.step("Close the editor.")
    clean(win)
    win.close()
    QTest.qWait(200)
    win.settings.sync()
    ini = (win.home.root / "editor.ini").read_text(encoding="utf-8")
    p.text("editor.ini, the editor's own preferences in your Taskloom folder: the steps it keeps for each kind of pane "
           "(code boxes, the Log, the Blocks list, Data preview's text)", "\n".join(zoom_lines(ini)))

    p.step("Start the editor again (taskloom editor). It opens orders.yaml again. Double-click the run in Run history, "
           "then click check_orders.")
    p.note("The proof starts the editor in a process of its own, as taskloom editor does, on the same Taskloom folder "
           "and the same screen, with no scheduler service. It does what this step says, measures the panes as above, "
           "saves a picture of its window with the Log in front and one with Data preview in front, and ends.")
    folder = tmp_path / "again"
    folder.mkdir()
    (folder / "screen.json").write_text(json.dumps({"screens": [{
        "name": "proof", "x": 0, "y": 0, "width": SCREEN[0], "height": SCREEN[1], "logicalDpi": 96,
        "logicalBaseDpi": 96}]}), encoding="utf-8")
    picture = folder / "editor.png"
    started = subprocess.run(
        [sys.executable, "-c", AGAIN, str(picture), QApplication.style().name(), json.dumps(list(WINDOW))],
        cwd=folder, capture_output=True, text=True, errors="replace", timeout=180,
        env={**os.environ, "TASKLOOM_HOME": str(win.home.root),
             "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
             "PYTHONPATH": os.pathsep.join([str(REPO), *filter(None, [os.environ.get("PYTHONPATH")])])})
    said = [line for line in started.stdout.splitlines() if line.startswith("{")]
    assert started.returncode == 0 and said and picture.exists(), started.stdout[-3000:] + started.stderr[-3000:]
    result = json.loads(said[-1])
    seen = result["seen"]

    def picture_of(path: Path) -> QLabel:
        label = QLabel()
        label.setPixmap(QPixmap(str(path)))
        label.resize(label.pixmap().size())
        qtbot.addWidget(label)
        return label

    parts = {name: QRect(*rect) for name, rect in seen["parts"].items()}
    log_part = parts["Log"]
    p.shot(picture_of(picture),
           f"The editor started again. ① The Log is at {seen['points']['Log']:g} points, ② the code box at "
           f"{seen['points']['code box']:g}, ③ the Blocks list at {seen['points']['Blocks list']:g}: each as you left "
           f"it. ④ The canvas shows the flow at {seen['canvas'][1]} again.",
           highlight=[parts["Log"], parts["code box"], parts["Blocks list"], parts["zoom bar"]], badges=True,
           zoom=[QRect(log_part.left(), log_part.top(), min(700, log_part.width()), min(300, log_part.height()))])
    preview_part = parts["Data preview"]
    p.shot(picture_of(folder / "editor-preview.png"),
           f"① Data preview's text, in the editor started again: {seen['points']['Data preview']:g} points, the size "
           "you left it at.", highlight=[preview_part], badges=True,
           zoom=[QRect(preview_part.left(), preview_part.top(), min(700, preview_part.width()),
                       min(300, preview_part.height()))])
    p.check("The editor started again with no message, and opened orders.yaml again (for ZM-3)",
            result["boxes"] == [] and result["code"] == 0 and seen["flows"] == ["orders.yaml"])
    for name, said_as in (("Log", "① The Log"), ("code box", "② The code box"), ("Blocks list", "③ The Blocks list"),
                          ("Data preview", "Data preview's text")):
        p.check(f"{said_as} shows at the size it had when the editor closed: {kept_pts[name]:g} points, its lines as "
                "high and as wide (ZM-3)",
                seen["sizes"][name] == list(kept[name]) and seen["points"][name] == kept_pts[name])
    p.check(f"④ The canvas shows the flow at {seen['canvas'][1]}, as it opened before, not at the "
            f"{zoomed_canvas[0]:g}% it had: the canvas keeps its zoom only until the flow is closed (ZM-3)",
            seen["canvas"] == [opened_at[0], f"{opened_at[0]:g}%"] and zoomed_canvas != opened_at)
