"""What the scheduler's proofs share: clicking in the editor, its countdowns and the flows they open. (Its proof
"Scheduled runs begin at their time", of runs whose program the service started 5 seconds early, was withdrawn on
2026-11-07: a run now begins at its time, at most 5 seconds after it, and no run's program is started before it.)"""

import datetime as dt

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QLabel, QStyle, QStyleOptionButton

ONE = dt.timedelta(seconds=1.5)  # a countdown shows whole seconds, and moves on once a second
REPORT = """\
blocks:
  report_time:
    type: trigger.schedule
    config: {{at: "{at}"}}
    ui: {{x: 0, y: 0}}
  make_report:
    type: logic.python
    config:
      inputs: []
      code: |
        import datetime
        output = "began at " + datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(output)
    ui: {{x: 260, y: 0}}
edges:
  - report_time.start -> make_report.after
"""
def file_menu(win, item: str):
    menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    next(a for a in menu.actions() if a.text() == item).trigger()


def click_tab(tabs, widget):
    bar = tabs.tabBar()
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(tabs.indexOf(widget)).center())


def click_block(tab, block: str):
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier,
                     tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center()))


def click_empty(tab):
    """Click an empty part of the canvas: nothing is selected then, and the Log shows every block's lines."""
    viewport = tab.view.viewport()
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, QPoint(20, viewport.height() - 20))


def active_box(win) -> QCheckBox:
    """The Active tick box of the Schedule block shown in Properties (made anew whenever the panel is)."""
    return next(c for c in win.properties.findChildren(QCheckBox) if c.toolTip().startswith("Untick it and this"))


def tick(box: QCheckBox) -> QRect:
    """Where a tick box's box is, in it (its row is wider, and a click in the empty part of the row does nothing)."""
    option = QStyleOptionButton()
    box.initStyleOption(option)
    return box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box)


def active_area(win) -> QRect:
    """The label Active and its tick box, in a screenshot of the window."""
    box = active_box(win)
    label = next(w for w in win.properties.findChildren(QLabel) if w.text() == "Active")
    return QRect(label.mapTo(win, QPoint(0, 0)), label.size()) | tick(box).translated(box.mapTo(win, QPoint(0, 0)))


def countdown(tab, block: str) -> str:
    return tab.scene.nodes[block].countdown


def reaches_zero(text: str, read_at: dt.datetime | None = None) -> dt.datetime:
    """When a countdown such as "next run in 00:01:05", read at `read_at` (default: now), reaches 0. Pass when it
    was read if a screenshot came between: a slow computer can take over a second to take one."""
    hours, minutes, seconds = map(int, text.removeprefix("next run in ").split(":"))
    return (read_at or dt.datetime.now()) + dt.timedelta(hours=hours, minutes=minutes, seconds=seconds)


def clock(moment: dt.datetime) -> str:
    return f"{moment:%H:%M:%S.%f}"[:-3]
