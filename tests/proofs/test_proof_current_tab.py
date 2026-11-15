"""Proof: the current tab of each of the editor's three tab bars has a strip along its bottom edge. See README.md
here."""

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest

from proofs.test_proof_failure_inputs import finished, open_flow, press_run_now
from proofs.test_proof_theme import choose_theme
from tests.test_current_tab import STRIP, bars, only_the_current, strips
from tests.test_theme import as_found

pytestmark = pytest.mark.usefixtures(as_found.__name__)  # the proof leaves the look as it found it

ASKED = "The current tab visibly highlighted: do what the entire industry does"
FLOWS, RIGHT, BOTTOM = "the flows' tabs", "Properties / Parameters / Flow", "Log / Data preview / Run history"
WHERE = {FLOWS: "above the canvas", RIGHT: "on the right", BOTTOM: "under the canvas"}


def flow(block: str, code: str) -> dict:
    """Manual Start `start`, then the Python Code block `block`."""
    return {"blocks": {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
                       block: {"type": "logic.python", "config": {"inputs": [], "code": code}, "ui": {"x": 260, "y": 0}}},
            "edges": [f"start.start -> {block}.after"]}


def click_tab(bar, text: str):
    """Click the tab that reads `text` in a tab bar."""
    index = next(i for i in range(bar.count()) if bar.tabText(i) == text)
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(index).center())
    QTest.qWait(100)
    assert bar.currentIndex() == index


def tabs_of(win, bar) -> QRect:
    """A bar's tabs, all of them, in the window's pixels."""
    area = QRect()
    for i in range(bar.count()):
        area |= bar.tabRect(i)
    return QRect(bar.mapTo(win, area.topLeft()), area.size())


def current(bar) -> str:
    return bar.tabText(bar.currentIndex())


def show_bars(p, win, where: str, expected: dict):
    """One picture per bar, with its tabs enlarged, and the checks: only the current tab has the strip."""
    found = bars(win)
    for name, bar in found.items():
        others = ", ".join(bar.tabText(i) for i in range(bar.count()) if i != bar.currentIndex())
        p.shot(win, f"{where}, {name}, {WHERE[name]} (enlarged): the strip is under {current(bar)}, the current "
                    f"tab, along its bottom edge. {others}: no strip.", zoom=[tabs_of(win, bar)])
    for name, bar in found.items():
        p.check(f"{where}, {name}: the current tab is {expected[name]}; it has a strip {STRIP} pixels high in the "
                "accent colour along its bottom edge, and no other tab has one",
                current(bar) == expected[name] and strips(bar) == only_the_current(bar))


def test_the_current_tab_has_a_strip_in_each_bar_and_each_theme(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("The current tab is marked with a strip, in each of the three tab bars", ASKED)
    p.idea("The current tab of each tab bar now has a strip along its bottom edge, 3 pixels high, in the accent "
           "colour: the colour your system uses for what is selected. You see at a glance which flow you are in, "
           "and which panel is open.")
    p.idea("The editor has three tab bars: the flows' tabs above the canvas, Properties / Parameters / Flow on the "
           "right, and Log / Data preview / Run history at the bottom. All three get the strip, in the light and in "
           "the dark theme. It follows the current tab: click another tab, close one, drag one elsewhere, and the "
           "strip is under the tab you are on, and only there.")
    p.idea("Before, the current tab differed from the others only by its background, a little lighter: easy to "
           "miss, most of all in the dark theme, where the difference is slight.")
    p.why_simplest("The strip is drawn over the tabs as they already are. Nothing else about them changes: their "
                   "sizes are the same to the pixel, a failed flow's tab stays red, the + and the close buttons "
                   "stay where they were.")
    p.alternative("Restyle the tabs with a style sheet, the usual way to change how tabs look",
                  "A style sheet replaces the whole look of the tabs: their sizes change, and the red of a failed "
                  "flow's tab, the close buttons and the + would have to be made again by hand.")
    p.alternative("Write the current tab's name in bold",
                  "Bold letters are wider: the tabs would shift each time you click one.")
    p.fits("It is what browsers and code editors do: a coloured line on the tab you are on. The colour is the one "
           "the rest of the editor already uses for what is selected, so it follows the theme.")

    p.step("Choose the light theme (Tools > Settings… > Theme: Light, Save). Open load_orders.yaml, press ▶ Run now "
           "(its block fails, so its tab turns red), then open clean.yaml and report.yaml.")
    choose_theme(p, win, "Light")
    failed = open_flow(win, monkeypatch, tmp_path / "load_orders.yaml",
                       flow("load_orders", "raise FileNotFoundError('orders.csv is not in the folder')"))
    press_run_now(win)
    finished(qtbot, failed, 1)
    open_flow(win, monkeypatch, tmp_path / "clean.yaml", flow("clean", "output = 1"))
    open_flow(win, monkeypatch, tmp_path / "report.yaml", flow("report", "output = 1"))
    click_tab(win.bottom_tabs.tabBar(), "Log")
    p.check("The theme is light", win.palette().color(QPalette.Window).lightness() >= 128)
    show_bars(p, win, "Light theme", {FLOWS: "report.yaml", RIGHT: "Properties", BOTTOM: "Log"})
    sizes = {name: [bar.tabRect(i) for i in range(bar.count())] for name, bar in bars(win).items()}

    p.step("Click another tab in each bar: load_orders.yaml (the flow that failed), Parameters, and Run history.")
    found = bars(win)
    click_tab(found[FLOWS], "load_orders.yaml")
    click_tab(found[RIGHT], "Parameters")
    click_tab(found[BOTTOM], "Run history")
    show_bars(p, win, "After the clicks", {FLOWS: "load_orders.yaml", RIGHT: "Parameters", BOTTOM: "Run history"})
    p.check("The tabs have the sizes and places they had before the clicks, to the pixel",
            {name: [bar.tabRect(i) for i in range(bar.count())] for name, bar in bars(win).items()} == sizes)
    p.note("load_orders.yaml's tab keeps its red ✕ and red name, since its last run failed: the strip is added "
           "under it, nothing is taken away.")

    p.step("Choose the dark theme (Tools > Settings… > Theme: Dark, Save).")
    choose_theme(p, win, "Dark")
    p.check("The theme is dark", win.palette().color(QPalette.Window).lightness() < 128)
    show_bars(p, win, "Dark theme", {FLOWS: "load_orders.yaml", RIGHT: "Parameters", BOTTOM: "Run history"})
    for each in win.tabs_list():  # nothing to save when the window closes
        each.doc.dirty = False
