"""Inspect's Mermaid diagrams follow the editor's theme: when the application's palette changes (the system's dark mode
turned on or off), the diagrams already in the Log are drawn again at once on the Log's new background, without any
click, the Log keeping where it is scrolled to, the rest of its content and its block filter; switching back draws
from the diagrams kept; nothing is drawn when no diagram is shown. Each test names the point of the specification
(1, 2) it protects."""

import os
import time
import uuid

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.test_inspect_mermaid import (DARK, LIGHT, SMALL, contrast, entry, fence, header, images, lines,  # noqa: E402
                                        look, near, painted, plain, python, rect_at, run_in_editor, show_log, size_of,
                                        unique, write_flow)


@pytest.fixture
def theme():
    """theme("light" | "dark") gives the application the palette a light or a dark system theme gives it, as the
    operating system does when its dark mode is turned on or off; the palette is put back afterwards."""
    original = QApplication.palette()

    def switch(name):
        palette = QPalette()
        for role, value in (DARK if name == "dark" else LIGHT).items():
            palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
        QApplication.setPalette(palette)
        QApplication.processEvents()

    yield switch
    QApplication.setPalette(original)
    QApplication.processEvents()


def assert_drawn_on_the_logs_background(edit, pos: int, fmt, theme_name: str):
    """The diagram at `pos` is painted on the Log's background of `theme_name` (its margin is that colour) and stands
    out from it."""
    base = edit.palette().color(QPalette.Base)
    assert (base.lightness() < 128) == (theme_name == "dark"), base.name()
    margin = painted(edit, pos, fmt, corner=True)
    assert all(near(c, base) for c in margin), (
        f"the diagram is painted on {sorted({c.name() for c in margin})}, not on the Log's {theme_name} background "
        f"{base.name()}")
    assert max(contrast(c, base) for c in painted(edit, pos, fmt)) >= 3, "the diagram does not stand out"


def counting_layouts(monkeypatch) -> list[str]:
    """The source of each diagram mmdr lays out from now on (mmdr 0.4.0, pinned: _mmdr.render lays a diagram out)."""
    from mmdr import _mmdr

    laid_out, lay_out = [], _mmdr.render
    monkeypatch.setattr(_mmdr, "render", lambda text, *a, **k: (laid_out.append(text), lay_out(text, *a, **k))[1])
    return laid_out


def settled(edit, deadline_s=5.0):
    """Let the Log finish laying itself out: until its scroll range stays the same for a few rounds of events."""
    bar, end, same, last = edit.verticalScrollBar(), time.monotonic() + deadline_s, 0, None
    while same < 3 and time.monotonic() < end:
        QApplication.processEvents()
        same = same + 1 if bar.maximum() == last else 0
        last = bar.maximum()


# --- 1: the diagrams already in the Log are drawn again on the new background, at once -----------------------------

@pytest.mark.parametrize("before, after", [("light", "dark"), ("dark", "light")], ids=["to-dark", "to-light"])
def test_a_diagram_in_the_log_is_drawn_again_on_the_new_background_when_the_theme_changes(
        win, qtbot, tmp_path, theme, before, after):
    """Spec 1 (the reported case): run in the editor, an Inspect block with Show as Markdown shows its ```mermaid
    diagram in the Log on the Log's background; the system theme then changes (light to dark, dark to light) and,
    without any click, the diagram already in the Log is painted on the Log's new background."""
    theme(before)
    tag = "n" + uuid.uuid4().hex[:8]
    flow = write_flow(tmp_path / "nightly.yaml", {
        "manual": {"type": "trigger.manual"},
        "make": python("output = 'nightly'"),
        "look": look(message="## Run {value}\n\n" + fence(unique(SMALL, tag)) + "\nAll steps.", markdown=True),
    }, ["manual.start -> make.after", "make.output -> look.value"])
    run_in_editor(win, qtbot, flow)
    edit = win.log.text
    [(pos, fmt)] = images(edit)
    assert_drawn_on_the_logs_background(edit, pos, fmt, before)
    shown = plain(win)

    theme(after)

    assert plain(win) == shown
    [(pos, fmt)] = images(edit)
    assert_drawn_on_the_logs_background(edit, pos, fmt, after)


def test_a_theme_change_redraws_every_diagram_and_keeps_the_rest_of_the_log(win, theme, tmp_path):
    """Specs 1, 2: on a theme change every diagram of the Log (two in one entry, one in another) is painted on the new
    background, while its text (plain lines, the Markdown around the diagrams) stays as it was and a picture that is
    not a diagram (an Inspect picture path) stays the same picture at the same size."""
    photo = QImage(64, 48, QImage.Format_RGB32)
    photo.fill(QColor("red"))
    assert photo.save(str(tmp_path / "chart.png"))
    first, second, third = (unique(SMALL.replace("TD", way)) for way in ("TD", "LR", "BT"))
    theme("light")
    show_log(win, [entry("start", block="load"),
                   entry(str(tmp_path / "chart.png"), "image", block="chart", second=1),
                   entry("# Plan\n\nbefore\n\n" + fence(first) + "\nbetween\n\n" + fence(second) + "\nafter",
                         "markdown", second=2),
                   entry("## Checks\n\n" + fence(third), "markdown", block="check", second=3),
                   entry("done", block="report", second=4)])
    edit = win.log.text
    shown, pictures = plain(win), [(pos, size_of(fmt)) for pos, fmt in images(edit)]
    assert len(pictures) == 4, lines(win)

    theme("dark")

    assert plain(win) == shown, "the Log's text changed"
    found = images(edit)
    assert len(found) == 4, lines(win)
    assert (found[0][0], size_of(found[0][1])) == pictures[0], "the picture changed"
    for pos, fmt in found[1:]:
        assert_drawn_on_the_logs_background(edit, pos, fmt, "dark")
    pos, fmt = found[0]
    colors = painted(edit, pos, fmt)
    assert sum(c.red() > 200 and c.green() < 80 and c.blue() < 80 for c in colors) >= 0.9 * len(colors), (
        "the picture is no longer shown")


def test_a_theme_change_keeps_the_logs_block_filter(win, theme):
    """Specs 1, 2: with the Log showing one block (Showing look, after a click on it), a theme change paints that
    block's diagram on the new background and the Log still shows only that block (and the run's start and end
    lines), with its Showing look and Show all."""
    theme("light")
    show_log(win, [entry("Run started", block=None) | {"header": True},
                   entry("loaded", block="load", second=1),
                   entry("Plan:\n\n" + fence(unique(SMALL)), "markdown", second=2),
                   entry("sent", block="report", second=3)])
    win.log.set_filter("look")
    QApplication.processEvents()
    shown = plain(win)
    assert header("loaded", block="load", second=1) not in lines(win) and "Run started" in shown, lines(win)

    theme("dark")

    assert plain(win) == shown, "the filter changed"
    assert win.log.showing.text() == "Showing <b>look</b>"
    assert not win.log.clear_filter.isHidden(), "Show all is gone"
    [(pos, fmt)] = images(win.log.text)
    assert_drawn_on_the_logs_background(win.log.text, pos, fmt, "dark")


@pytest.mark.parametrize("where", ["scrolled-up", "at-the-bottom"])
def test_a_theme_change_keeps_where_the_log_is_scrolled_to(win, theme, where):
    """Spec 1 ("keeping the Log's scroll position"): the Log keeps where it is scrolled to, up and down and sideways (a
    long line does not wrap): a Log scrolled up to a diagram stays there through a theme change (and does not then
    jump to new lines); a Log at the bottom stays at the bottom and keeps following new lines; in both, the diagram is
    painted on the new background."""
    theme("light")
    show_log(win, [entry("wide " + "x" * 400, block="load")]
             + [entry(f"row {i}", block="load") for i in range(80)]
             + [entry("Plan:\n\n" + fence(unique(SMALL)), "markdown", second=1)]
             + [entry(f"row {i}", block="report", second=2) for i in range(80 if where == "scrolled-up" else 3)])
    edit, bar = win.log.text, win.log.text.verticalScrollBar()
    settled(edit)
    [(pos, fmt)] = images(edit)
    if where == "scrolled-up":
        bar.setValue(bar.value() + rect_at(edit, pos).top() - 10)  # the diagram near the top of the view
        QApplication.processEvents()
        assert 0 < bar.value() < bar.maximum(), (bar.value(), bar.maximum())
    else:
        bar.setValue(bar.maximum())
        QApplication.processEvents()
    side = edit.horizontalScrollBar()
    side.setValue(side.maximum() // 2)
    QApplication.processEvents()
    across = side.value()
    assert across > 0, (side.value(), side.maximum())
    kept, top = bar.value(), rect_at(edit, pos).top()

    theme("dark")
    settled(edit)
    assert side.value() == across, (side.value(), across, side.maximum())

    if where == "scrolled-up":
        assert bar.value() == kept and rect_at(edit, pos).top() == top, (bar.value(), kept, bar.maximum())
    else:
        assert bar.value() == bar.maximum(), (bar.value(), bar.maximum())
    win.log.append(entry("later", block="report", second=3))
    settled(edit)
    if where == "scrolled-up":
        assert bar.value() == kept, "the Log jumped to the new line"
    else:
        assert bar.value() == bar.maximum(), "the Log no longer follows new lines"
    [(pos, fmt)] = images(edit)
    assert_drawn_on_the_logs_background(edit, pos, fmt, "dark")


# --- 2: switching back draws from the diagrams kept; nothing is drawn without a diagram ------------------------------

def test_switching_the_theme_back_and_forth_draws_each_diagram_once_per_theme(win, theme, monkeypatch):
    """Spec 2: the diagrams stay kept by (source, background, pixel ratio): light, dark, light, dark again paints the
    diagram on each theme's background each time, and mmdr lays it out once for each theme only."""
    tag = "n" + uuid.uuid4().hex[:8]
    laid_out = counting_layouts(monkeypatch)
    theme("light")
    show_log(win, [entry("Plan:\n\n" + fence(unique(SMALL, tag)), "markdown")])
    edit = win.log.text

    for name in ("dark", "light", "dark"):
        theme(name)
        [(pos, fmt)] = images(edit)
        assert_drawn_on_the_logs_background(edit, pos, fmt, name)

    assert sum(tag in text for text in laid_out) == 2, f"laid out {sum(tag in t for t in laid_out)} times"


def test_a_theme_change_draws_nothing_when_no_diagram_is_shown(win, theme, monkeypatch):
    """Spec 2: a theme change draws no diagram when the Log shows none: its Markdown has only other code blocks and
    its only diagram is in a block the filter hides; the Log shows the same text."""
    theme("light")
    show_log(win, [entry("# Plan\n\n```python\nx  =  1\n```\n", "markdown", second=1),
                   entry("Hidden:\n\n" + fence(unique(SMALL)), "markdown", block="other", second=2),
                   entry("done", second=3)])
    win.log.set_filter("look")
    QApplication.processEvents()
    shown = plain(win)
    assert images(win.log.text) == [], lines(win)
    laid_out = counting_layouts(monkeypatch)

    theme("dark")
    theme("light")

    assert laid_out == [], "a diagram was drawn"
    assert plain(win) == shown and images(win.log.text) == []
