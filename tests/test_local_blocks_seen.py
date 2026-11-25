"""Found by the pictures of stage 4's proof (tests/proofs/test_proof_local_blocks.py): Properties of a Local block's
copy did not fit the Properties panel. The grey line under its title (its type, and that it is a Local block kept in
this flow's file) was one line too long for the panel, so Properties scrolled sideways and the line was cut short.
The tests' offscreen screen draws text smaller than a 96-dpi screen does (the proof's, and Windows' at 100 %), so the
text is drawn here at the sizes such screens give it: as here, 4/3 of it (96 dpi) and twice it (150 %). Through the
real editor window, at the size of the proof's screenshots and at a smaller one."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from tests.test_local_blocks_editor import click, greetings, note, shared  # noqa: E402
from tests.test_paths_editor_editing import clean, settle  # noqa: E402


@pytest.mark.invariant("RB-2")
@pytest.mark.parametrize("size", [(1600, 1000), (1280, 800)], ids=["the proof's window", "a smaller window"])
@pytest.mark.parametrize("text", [1, 4 / 3, 2], ids=["text as here", "text as at 96 dpi", "text as at 150 %"])
def test_properties_of_a_local_copy_show_the_line_under_its_title_whole_and_never_scroll_sideways(win, tmp_path, size,
                                                                                                text):
    """Properties of greet_bo, a copy of the Local block Shout, in a window of `size`, with text `text` times its size
    here: the line under the title, which names its type local.shout, is shown whole (wrapped if it needs to be) inside
    the panel, and Properties do not scroll sideways."""
    app = QApplication.instance()
    font = app.font()
    bigger = QFont(font)
    bigger.setPointSizeF(font.pointSizeF() * text)
    app.setFont(bigger)
    try:
        shown_whole(win, tmp_path, size)
    finally:
        app.setFont(font)


def shown_whole(win, tmp_path, size):
    win.resize(*size)
    settle()
    flow, _ = greetings(tmp_path / "flows", ["ana", "bo"])
    tab = win.open_flow(flow)
    settle()

    click(tab, "greet_bo")
    assert note(win) == shared(2)

    panel = win.properties
    (head,) = [label for label in panel.widget().findChildren(QLabel) if "local.shout" in label.text()]
    right = head.mapTo(panel.viewport(), QPoint(head.width(), 0)).x()
    assert right <= panel.viewport().width(), f"the line under the title ends {right - panel.viewport().width()} px " \
                                              "right of the panel's edge"
    assert head.minimumSizeHint().width() <= head.width(), "the line under the title is cut short"
    assert panel.horizontalScrollBar().maximum() == 0, \
        f"Properties scroll {panel.horizontalScrollBar().maximum()} px sideways"
    clean(win)
