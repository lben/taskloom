"""The current tab of each of the editor's three tab bars (the flows' tabs, Properties / Parameters / Flow, and Log /
Data preview / Run history) is marked with a 3-pixel strip in the accent colour along its bottom edge; the strip follows
the current tab, and nothing else about the tabs changes (docs/promises/editor-fixes.yaml, rows TAB-1 to TAB-3).

Driven through the real editor window (the `win` fixture). The strip is read from a picture of each bar, pixel by
pixel. "Nothing else changes" is measured against Qt's own tabs: a plain QTabWidget in Qt's own style, with the same
tabs, icons and settings, beside the editor's."""

from collections import Counter

import pytest
import yaml
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QProxyStyle, QStyle, QStyleFactory, QStyleOptionTab, QTabBar, QTabWidget,
                               QWidget)

from test_editor import wait_done
from test_tab_plus import new_flow_button
from tests.test_theme import as_found, choose, settle

pytestmark = pytest.mark.usefixtures(as_found.__name__)  # each test leaves the look as it found it

STRIP = 3  # pixels high
ENDS = 2  # pixels at each end of the tab that the strip may leave out


def flow_file(folder, name: str, code: str = "output = 1"):
    """<name>.yaml: Manual Start `manual`, then the Python Code block `name`."""
    path = folder / f"{name}.yaml"
    path.write_text(yaml.safe_dump({
        "blocks": {"manual": {"type": "trigger.manual"},
                   name: {"type": "logic.python", "config": {"inputs": [], "code": code}}},
        "edges": [f"manual.start -> {name}.after"]}, sort_keys=False), encoding="utf-8")
    return path


def open_flows(win, folder, *names) -> list:
    """The window wide enough for the flows' tabs to show whole, with these flows open (the last one current)."""
    win.resize(1900, 900)
    tabs = [win.open_flow(flow_file(folder, name)) for name in names]
    settle()
    return tabs


def bars(win) -> dict[str, QTabBar]:
    found = {"the flows' tabs": win.tabs.tabBar(), "Properties / Parameters / Flow": win.right_tabs.tabBar(),
             "Log / Data preview / Run history": win.bottom_tabs.tabBar()}
    for name, bar in found.items():
        assert bar.isVisible() and bar.count() >= 2, f"{name}: not on screen with two tabs or more"
        for i in range(bar.count()):
            assert bar.rect().contains(bar.tabRect(i)), f"{name}: the tab {bar.tabText(i)!r} does not show whole"
    return found


def strips(bar: QTabBar) -> dict[str, float]:
    """Each tab's strip, by the tab's text: how many pixels high the accent colour runs along the bottom edge of the
    tab's rectangle, across its width (but for ENDS pixels at each end), counted up from the edge. 0: no strip."""
    settle()
    image = bar.grab().toImage()
    ratio = image.devicePixelRatio()  # the picture's pixels per pixel of the bar (high-DPI screens)
    accent = bar.palette().color(QPalette.Accent).rgb()
    heights = {}
    for i in range(bar.count()):
        rect = bar.tabRect(i)
        left, right = round((rect.left() + ENDS) * ratio), round((rect.right() + 1 - ENDS) * ratio)
        top, y = round(rect.top() * ratio), round((rect.bottom() + 1) * ratio) - 1
        while y >= top and all(image.pixel(x, y) == accent for x in range(left, right)):
            y -= 1
        heights[bar.tabText(i)] = (round((rect.bottom() + 1) * ratio) - 1 - y) / ratio
    return heights


def only_the_current(bar: QTabBar) -> dict[str, float]:
    """What strips(bar) must be: the current tab's is STRIP pixels high, no other tab has one."""
    return {bar.tabText(i): STRIP if i == bar.currentIndex() else 0 for i in range(bar.count())}


def check_strips(win, after: str):
    for name, bar in bars(win).items():
        assert strips(bar) == only_the_current(bar), (
            f"{after}: {name}, current tab {bar.tabText(bar.currentIndex())!r}, accent colour "
            f"{bar.palette().color(QPalette.Accent).name()}")


def click_tab(bar: QTabBar, index: int):
    QTest.mouseClick(bar, Qt.LeftButton, pos=bar.tabRect(index).center())
    settle()
    assert bar.currentIndex() == index


def close_button(bar: QTabBar, index: int):
    return bar.tabButton(index, QTabBar.RightSide) or bar.tabButton(index, QTabBar.LeftSide)


# --- TAB-1: the strip, in each bar and each theme ---------------------------------------------------------------------

@pytest.mark.invariant("TAB-1")
@pytest.mark.parametrize("theme", ["Light", "Dark"])
def test_the_current_tab_of_each_bar_has_a_3_pixel_strip_in_the_accent_colour_along_its_bottom_edge(
        win, tmp_path, theme):
    open_flows(win, tmp_path, "load_orders", "clean", "report")

    choose(win, theme)  # Tools > Settings > Theme, Save

    assert (win.palette().color(QPalette.Window).lightness() < 128) == (theme == "Dark")
    check_strips(win, f"in the {theme} theme")


# --- TAB-2: the strip follows the current tab -------------------------------------------------------------------------

@pytest.mark.invariant("TAB-2")
def test_clicking_another_tab_moves_the_strip_to_it_in_each_bar(win, tmp_path):
    open_flows(win, tmp_path, "load_orders", "clean", "report")
    check_strips(win, "as the flows open")

    for name, bar in bars(win).items():
        for index in (0, 1, bar.count() - 1):
            click_tab(bar, index)
            check_strips(win, f"after a click on {bar.tabText(index)!r}")


@pytest.mark.invariant("TAB-2")
def test_closing_the_current_flow_moving_it_elsewhere_and_opening_a_flow_leave_the_strip_on_the_current_tab_only(
        win, tmp_path):
    load_orders, clean = open_flows(win, tmp_path, "load_orders", "clean")
    bar = win.tabs.tabBar()

    report = win.open_flow(flow_file(tmp_path, "report"))  # open a flow: its tab is the current one
    settle()
    assert win.tabs.currentWidget() is report
    check_strips(win, "after a flow is opened")

    bar.moveTab(win.tabs.indexOf(report), 0)  # the current tab, dragged to the front
    settle()
    assert [bar.tabText(i) for i in range(3)] == ["report.yaml", "load_orders.yaml", "clean.yaml"]
    assert bar.currentIndex() == 0
    check_strips(win, "after the current tab is moved to the front")

    bar.moveTab(2, 0)  # another tab, dragged in front of the current one
    settle()
    assert bar.tabText(bar.currentIndex()) == "report.yaml" and bar.currentIndex() == 1
    check_strips(win, "after another tab is moved in front of the current one")

    close_button(bar, bar.currentIndex()).click()  # close the current one
    settle()
    assert [bar.tabText(i) for i in range(bar.count())] == ["clean.yaml", "load_orders.yaml"]
    check_strips(win, "after the current tab is closed")

    click_tab(bar, 0)
    close_button(bar, 1).click()  # close another one
    settle()
    assert [bar.tabText(i) for i in range(bar.count())] == ["clean.yaml"]
    assert strips(bar) == {"clean.yaml": STRIP}, "after the other tab is closed, the only tab left"


# --- TAB-3: nothing else about the tabs changes -----------------------------------------------------------------------

def qts_own(tabs: QTabWidget, qtbot) -> QTabWidget:
    """A plain QTabWidget in Qt's own style, as large as `tabs` and with the same tabs (texts, icons, close buttons)
    and settings. No style sheet, size override or style of the editor's reaches it: it is a window of its own, with
    its own copy of the style Qt gave the application."""
    assert not QApplication.instance().styleSheet(), (
        "the application has a style sheet: it would reach Qt's own tabs too, so they cannot be measured beside it")
    style = QApplication.style()
    while isinstance(style, QProxyStyle):
        style = style.baseStyle()
    own, bar = QTabWidget(), tabs.tabBar()
    qtbot.addWidget(own)
    style = QStyleFactory.create(style.name())
    style.setParent(own)
    own.setStyle(style)
    own.tabBar().setStyle(style)
    own.setTabsClosable(tabs.tabsClosable())
    own.setMovable(tabs.isMovable())
    own.setDocumentMode(tabs.documentMode())
    own.setIconSize(tabs.iconSize())
    own.setElideMode(tabs.elideMode())
    own.setUsesScrollButtons(tabs.usesScrollButtons())
    own.setFont(tabs.font())
    own.tabBar().setFont(bar.font())
    for i in range(bar.count()):
        own.addTab(QWidget(), bar.tabIcon(i), bar.tabText(i))
    own.resize(tabs.size())
    own.show()
    settle()
    return own


def sizes(bar: QTabBar) -> dict:
    """Each tab's rectangle in its bar, the size it asks for and its close button's rectangle (None: it has none)."""
    settle()
    buttons = [close_button(bar, i) for i in range(bar.count())]
    return {bar.tabText(i): (bar.tabRect(i), bar.tabSizeHint(i), buttons[i] and buttons[i].geometry())
            for i in range(bar.count())}


@pytest.mark.invariant("TAB-3")
def test_each_tab_and_close_button_is_where_and_as_large_as_qts_own_whichever_tab_is_the_current_one(
        win, qtbot, tmp_path):
    open_flows(win, tmp_path, "load_orders", "clean", "report")
    panels = (win.right_tabs, win.bottom_tabs)

    for tabs in (win.tabs, *panels):
        own = qts_own(tabs, qtbot)
        bar, own_bar = tabs.tabBar(), own.tabBar()
        for index in range(bar.count()):
            tabs.setCurrentIndex(index)
            own.setCurrentIndex(index)
            assert sizes(bar) == sizes(own_bar), f"with {bar.tabText(index)!r} the current tab"
            if tabs in panels:  # (the flows' bar leaves room for the + at its right, Qt's own has none)
                assert bar.geometry() == own_bar.geometry() and bar.sizeHint() == own_bar.sizeHint()
    assert all(close_button(win.tabs.tabBar(), i).isVisible() for i in range(3))


@pytest.mark.invariant("TAB-3")
def test_the_plus_stays_just_right_of_the_last_flow_tab_and_as_tall_as_the_bar(win, tmp_path):
    open_flows(win, tmp_path, "load_orders", "clean", "report")
    bar, plus = win.tabs.tabBar(), new_flow_button(win)

    for index in (2, 0):
        click_tab(bar, index)
        last = bar.tabRect(bar.count() - 1)
        assert QRect(plus.mapTo(win, QPoint(0, 0)), plus.size()) == QRect(
            bar.mapTo(win, QPoint(last.right() + 1 + 4, 0)), plus.size()), "the + is not 4 pixels right of the last tab"
        assert plus.isVisible() and plus.height() == bar.height()


def text_redness(bar: QTabBar, index: int) -> float:
    """How red the tab's text is, in a picture of the bar: over its ink (the pixels far from the tab's background),
    the average of red less the stronger of green and blue. About 100 for a failed flow's red, about 0 for ordinary
    text: where a font is drawn with coloured edges (Linux), black text has single strongly red and blue pixels, which
    even out over the ink. The tab's icon (a failed flow's is a red ✕) is left out: the style lets a narrow icon
    reach into the left of the text's rectangle, by less than the bar's icon width."""
    settle()
    option = QStyleOptionTab()
    bar.initStyleOption(option, index)
    text = bar.style().subElementRect(QStyle.SE_TabBarTabText, option, bar).adjusted(bar.iconSize().width(), 0, 0, 0)
    image = bar.grab(text).toImage()
    pixels = [image.pixelColor(x, y).getRgb()[:3] for y in range(image.height()) for x in range(image.width())]
    background = Counter(pixels).most_common(1)[0][0]
    far = {pixel: sum(abs(a - b) for a, b in zip(pixel, background)) for pixel in set(pixels)}
    ink = [pixel for pixel in pixels if far[pixel] >= max(far.values()) / 2 > 0]
    return sum(r - max(g, b) for r, g, b in ink) / len(ink) if ink else 0.0


@pytest.mark.invariant("TAB-3")
def test_a_failed_flows_tab_stays_red_while_it_is_the_current_tab_and_while_another_is(win, qtbot, tmp_path):
    win.resize(1900, 900)
    clean = win.open_flow(flow_file(tmp_path, "clean"))
    failed = win.open_flow(flow_file(tmp_path, "load_orders", "raise ValueError('no orders')"))
    for tab in (clean, failed):
        win.run_flow(tab)
        wait_done(qtbot, tab)
    bar = win.tabs.tabBar()
    qtbot.waitUntil(lambda: text_redness(bar, win.tabs.indexOf(failed)) >= 60, timeout=5_000)

    for current in (failed, clean):
        win.tabs.setCurrentWidget(current)
        assert text_redness(bar, win.tabs.indexOf(failed)) >= 60, "the failed flow's tab is not red"
        assert text_redness(bar, win.tabs.indexOf(clean)) <= 20, "the tab of the flow that ran well is red"
