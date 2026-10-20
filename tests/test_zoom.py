"""The zoom control floating over each flow canvas, in its top-right corner, driven through its widgets."""

import copy
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication, QGraphicsProxyWidget, QLabel, QStyle,  # noqa: E402
                               QStyleOptionSpinBox, QWidget)

from tests.test_editor import win, write_flow  # noqa: E402, F401


def settled(tab):
    """A new tab is centred (and zoomed out if the flow does not fit) once the window is idle."""
    QApplication.processEvents()
    return tab


def click(button):
    QTest.mouseClick(button, Qt.LeftButton)


def click_arrow(spin, arrow):
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    rect = spin.style().subControlRect(QStyle.CC_SpinBox, option, arrow, spin)
    QTest.mouseClick(spin, Qt.LeftButton, Qt.NoModifier, rect.center())


def wheel(view, up: bool, at=QPoint(120, 120)):
    viewport = view.viewport()
    event = QWheelEvent(QPointF(at), QPointF(viewport.mapToGlobal(at)), QPoint(0, 0), QPoint(0, 120 if up else -120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(viewport, event)


def assert_shows(view, percent: float):
    """The view is at that zoom, and the control says so (to a whole percent)."""
    assert view.zoom_percent() == pytest.approx(percent)
    assert view.zoom_percent() == pytest.approx(100 * view.transform().m11())
    assert view.zoom_bar.percent.text() == f"{round(percent)}%"


def rect_in(widget, ancestor) -> QRect:
    return QRect(widget.mapTo(ancestor, QPoint(0, 0)), widget.size())


def corner_gaps(view) -> tuple[int, int]:
    """How far the control is from the right and the top edge of the visible canvas (it must lie inside it)."""
    bar, canvas = rect_in(view.zoom_bar, view), view.viewport().geometry()
    assert view.zoom_bar.isVisible() and canvas.contains(bar), (bar, canvas)
    return canvas.right() - bar.right(), bar.top() - canvas.top()


def scene_centre(view) -> QPointF:
    return view.mapToScene(view.viewport().rect().center())


def exact_middle(view) -> QPointF:
    """The scene point exactly in the middle of the visible canvas (QRect.center() is up to a pixel off)."""
    return view.mapToScene(view.viewport().rect()).boundingRect().center()


def assert_middle_is(view, ref: QPointF):
    """The scene point in the middle is still ref, to within a view pixel."""
    now, zoom = exact_middle(view), view.zoom_percent() / 100
    assert abs(now.x() - ref.x()) * zoom <= 1 and abs(now.y() - ref.y()) * zoom <= 1, (ref, now)


def test_control_shows_magnifier_percent_plus_step_and_minus_from_left_to_right(win):
    view = settled(win.new_flow()).view
    bar = view.zoom_bar
    assert isinstance(bar, QWidget) and view.isAncestorOf(bar) and bar.isVisible()
    parts = (bar.percent, bar.plus, bar.step, bar.minus)
    icons = [w for w in bar.findChildren(QLabel) if not w.pixmap().isNull()]
    icons += [w for w in bar.findChildren(QAbstractButton) if w not in parts and not w.icon().isNull()]
    assert icons, "no magnifier icon"
    row = [min(icons, key=lambda w: rect_in(w, bar).left()), *parts]
    assert all(w.isVisible() and w.width() > 0 for w in row)
    rects = [rect_in(w, bar) for w in row]
    assert all(a.right() < b.left() for a, b in zip(rects, rects[1:])), rects  # icon, %, +, step, - in that order

    assert bar.percent.text() == "100%"
    assert bar.plus.text() == "+" and bar.minus.text() in ("-", "\N{MINUS SIGN}")
    step = bar.step
    assert (step.value(), step.text(), step.minimum(), step.maximum()) == (5, "5%", 1, 100)
    click_arrow(step, QStyle.SC_SpinBoxUp)
    assert (step.value(), step.text()) == (6, "6%")
    click_arrow(step, QStyle.SC_SpinBoxDown)
    click_arrow(step, QStyle.SC_SpinBoxDown)
    assert (step.value(), step.text()) == (4, "4%")
    assert_shows(view, 100)  # the arrows change the step, not the zoom


def test_plus_and_minus_zoom_by_the_step_in_percentage_points(win):
    view = settled(win.new_flow()).view
    bar = view.zoom_bar
    for expected in (105, 110, 115):
        click(bar.plus)
        assert_shows(view, expected)
    click(bar.minus)
    assert_shows(view, 110)

    click_arrow(bar.step, QStyle.SC_SpinBoxUp)
    assert bar.step.value() == 6
    click(bar.minus)
    assert_shows(view, 104)
    bar.step.setValue(10)
    click(bar.plus)
    assert_shows(view, 114)
    for expected in (104, 94, 84):
        click(bar.minus)
        assert_shows(view, expected)


def test_plus_and_minus_stop_at_300_and_20_percent(win):
    view = settled(win.new_flow()).view
    bar = view.zoom_bar
    bar.step.setValue(99)
    click(bar.plus)
    click(bar.plus)
    assert_shows(view, 298)
    bar.step.setValue(5)
    click(bar.plus)
    assert_shows(view, 300)  # not 303
    click(bar.plus)
    assert_shows(view, 300)

    bar.step.setValue(100)
    click(bar.minus)
    click(bar.minus)
    assert_shows(view, 100)
    bar.step.setValue(39)
    click(bar.minus)
    click(bar.minus)
    assert_shows(view, 22)
    bar.step.setValue(5)
    click(bar.minus)
    assert_shows(view, 20)  # not 17
    click(bar.minus)
    assert_shows(view, 20)


def test_percent_follows_the_mouse_wheel_and_the_buttons_go_on_from_there(win):
    view = settled(win.new_flow()).view
    bar = view.zoom_bar
    wheel(view, up=False)
    assert_shows(view, 100 / 1.15)  # 86.96: "87%"
    wheel(view, up=True)
    wheel(view, up=True)
    assert_shows(view, 115)
    wheel(view, up=True)
    assert_shows(view, 132.25)  # "132%"
    click(bar.plus)
    assert_shows(view, 137.25)
    wheel(view, up=False)
    assert_shows(view, 137.25 / 1.15)  # 119.35: "119%"

    for _ in range(30):  # the wheel keeps to the same limits
        wheel(view, up=True)
    assert 300 / 1.15 <= view.zoom_percent() <= 300
    assert_shows(view, view.zoom_percent())
    bar.step.setValue(50)
    click(bar.plus)
    assert_shows(view, 300)
    for _ in range(40):
        wheel(view, up=False)
    assert 20 <= view.zoom_percent() <= 20 * 1.15
    assert_shows(view, view.zoom_percent())
    click(bar.minus)
    assert_shows(view, 20)


def test_percent_shows_the_zoom_a_wide_flow_opens_with(win, tmp_path):
    flow = write_flow(tmp_path / "wide.yaml", """
        blocks:
          load:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
            ui: {x: 0, y: 0}
          report:
            type: logic.python
            config: {outputs: [], code: "print(input)"}
            ui: {x: 600, y: 300}
        edges:
          - load.output -> report.input
    """)
    view = settled(win.open_flow(flow)).view
    opened_at = 100 * view.transform().m11()
    assert 50 < opened_at < 100  # zoomed out (not to the 50% floor) to show the whole flow
    assert_shows(view, opened_at)
    click(view.zoom_bar.plus)
    assert_shows(view, opened_at + 5)


def test_buttons_zoom_around_the_centre_of_the_visible_canvas(win):
    view = settled(win.new_flow()).view
    bar = view.zoom_bar
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 250)  # look somewhere off the flow
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 150)
    QTest.mouseMove(view.viewport(), QPoint(40, 40))  # the mouse passed over the canvas on its way to the buttons
    middle = view.viewport().rect().center()
    bar.step.setValue(50)
    for button, expected in ((bar.plus, 150), (bar.plus, 200), (bar.minus, 150), (bar.minus, 100), (bar.minus, 50)):
        centre = scene_centre(view)
        click(button)
        assert_shows(view, expected)
        now = view.mapFromScene(centre)  # where the point that was in the middle is now
        assert abs(now.x() - middle.x()) <= 2 and abs(now.y() - middle.y()) <= 2, (expected, middle, now)

    bar.step.setValue(5)  # no drift that adds up over many clicks
    ref = exact_middle(view)
    for _ in range(10):
        click(bar.plus)
        click(bar.minus)
    assert_shows(view, 50)
    assert_middle_is(view, ref)

    bar.step.setValue(100)  # the view does not move when + or - is stopped at the limit
    for _ in range(3):
        click(bar.plus)
    assert_shows(view, 300)
    ref = exact_middle(view)
    for _ in range(3):
        click(bar.plus)
    assert_shows(view, 300)
    assert_middle_is(view, ref)
    for _ in range(3):
        click(bar.minus)  # 200, 100, 20
    assert_shows(view, 20)
    ref = exact_middle(view)
    for _ in range(3):
        click(bar.minus)
    assert_shows(view, 20)
    assert_middle_is(view, ref)


def test_control_stays_in_the_top_right_corner_of_the_visible_canvas(win, qtbot):
    tab = settled(win.new_flow())
    view, bar = tab.view, tab.view.zoom_bar
    gaps = corner_gaps(view)
    assert all(0 <= gap <= 40 for gap in gaps), gaps

    bar.step.setValue(40)
    click(bar.minus)
    click(bar.minus)  # "20%": a narrower percentage keeps the control at the right edge
    assert_shows(view, 20)
    assert corner_gaps(view) == gaps
    bar.step.setValue(100)
    click(bar.plus)
    click(bar.plus)
    assert_shows(view, 220)

    view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 300)  # scrolled
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 200)
    assert corner_gaps(view) == gaps
    viewport = view.viewport()
    before = scene_centre(view)
    QTest.mousePress(viewport, Qt.MiddleButton, Qt.NoModifier, QPoint(300, 300))  # panned with the middle button
    QTest.mouseMove(viewport, QPoint(200, 220))
    QTest.mouseMove(viewport, QPoint(100, 150))
    QTest.mouseRelease(viewport, Qt.MiddleButton, Qt.NoModifier, QPoint(100, 150))
    assert scene_centre(view) != before
    assert corner_gaps(view) == gaps

    width = viewport.width()
    win.resize(1700, 1000)  # the window is resized
    qtbot.waitUntil(lambda: viewport.width() > width, timeout=5000)
    assert corner_gaps(view) == gaps
    width = viewport.width()
    win.resizeDocks([win.inspector], [win.inspector.width() + 200], Qt.Horizontal)  # only the canvas is resized
    qtbot.waitUntil(lambda: viewport.width() < width, timeout=5000)
    assert corner_gaps(view) == gaps


def test_control_is_not_part_of_the_flow(win, tmp_path):
    flow = write_flow(tmp_path / "nightly.yaml", """
        blocks:
          start:
            type: trigger.manual
            ui: {x: 0, y: 0}
          load:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
            ui: {x: 250, y: 0}
        edges:
          - start.start -> load.after
    """)
    tab = settled(win.open_flow(flow))
    saved = copy.deepcopy(tab.doc.data)
    bar = tab.view.zoom_bar
    click(bar.plus)
    click_arrow(bar.step, QStyle.SC_SpinBoxUp)
    click(bar.minus)
    click(bar.minus)
    assert_shows(tab.view, 93)
    assert not tab.doc.dirty and tab.doc.data == saved  # zooming is not a change to the flow
    assert bar.graphicsProxyWidget() is None
    assert not any(isinstance(item, QGraphicsProxyWidget) for item in tab.scene.items())


def test_each_flow_tab_has_its_own_zoom_control(win):
    first = settled(win.new_flow())
    second = settled(win.new_flow())
    assert first.view.zoom_bar is not second.view.zoom_bar
    assert first.view.isAncestorOf(first.view.zoom_bar) and second.view.isAncestorOf(second.view.zoom_bar)

    click(second.view.zoom_bar.minus)
    assert_shows(second.view, 95)
    assert_shows(first.view, 100)
    win.tabs.setCurrentWidget(first)
    QApplication.processEvents()
    click(first.view.zoom_bar.plus)
    click(first.view.zoom_bar.plus)
    assert_shows(first.view, 110)
    assert_shows(second.view, 95)
