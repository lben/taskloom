"""Inspect, phase 2: with Show as Markdown on, a ```mermaid fence in an Inspect message (or text value) is drawn as a
picture in the Log, by mmdr, offline, in the editor only: the run and its history keep the Markdown source, runs
reopened from history draw the same, a diagram that cannot be drawn shows as text with a grey note, and the editor
build carries mmdr (with its licence notices) while the Linux runner build has none. Each test names the decision of
the specification (1-6) it protects."""

import dis
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import types
import uuid
import zipfile
from importlib import metadata
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPalette, QTextCursor, QTextFormat  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from taskloom.history import History  # noqa: E402
from taskloom.report import MAX_IMAGE_WIDTH  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PHASE_1_NOTE = "Mermaid diagrams are shown as text"
ENGINE = {"mmdr", "novasvg"}  # the Mermaid engine and the library it draws its pictures with

SMALL = "graph TD\n    load --> report\n"
WIDE = "graph LR\n" + "".join(f"    step{i} --> step{i + 1}\n" for i in range(12))
TALL = "graph TD\n" + "".join(f"    step{i} --> step{i + 1}\n" for i in range(12))
MALFORMED = "graph TD\n    start[Load --> report\n"


def fence(source: str) -> str:
    return f"```mermaid\n{source}```\n"


def unique(source: str, tag: str = "") -> str:
    """`source` with node names no other test uses (starting with `tag`, if given): the editor keeps the diagrams it
    drew, so each test draws its own."""
    tag = tag or "n" + uuid.uuid4().hex[:8]
    return source.replace("load", f"{tag}load").replace("report", f"{tag}report")


def note(error: str) -> str:
    return f"(could not draw this diagram: {error})"


def mmdr_error(source: str) -> str:
    """The first line of the error mmdr raises for `source`."""
    import mmdr

    try:
        mmdr.render(source).svg()
    except ValueError as e:
        return str(e).splitlines()[0]
    raise AssertionError(f"mmdr draws {source!r}")


def natural_size(source: str) -> tuple[int, int]:
    """The size mmdr draws `source` at by itself (its SVG's size)."""
    import mmdr

    image = QImage.fromData(mmdr.render(source).png())
    assert not image.isNull()
    return image.width(), image.height()


# --- flows, the run history and the Log --------------------------------------------------------------------------

def write_flow(path: Path, blocks: dict, edges=()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": list(edges)}, sort_keys=False), encoding="utf-8")
    return path


def python(code: str, outputs=("output",), inputs=()) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code}}


def look(**config) -> dict:
    return {"type": "logic.inspect", **({"config": config} if config else {})}


def entry(message: str, show=None, block="look", second=0) -> dict:
    """A log line as runs log them (and the history gives them back)."""
    e = {"ts": f"2026-10-28T09:00:{second:02d}.000", "block": block, "level": "INFO", "message": message}
    if show:
        e["show"] = show
    return e


def header(message: str = "", block="look", second=0) -> str:
    """How the Log writes an entry's line: HH:MM:SS [block] LEVEL message (a Markdown entry's has no message)."""
    return f"09:00:{second:02d} [{block}] INFO" + (f" {message}" if message else "")


def show_log(win, entries):
    win.bottom_tabs.setCurrentWidget(win.log)
    win.log.set_entries(entries)
    QApplication.processEvents()


def plain(win) -> str:
    return win.log.text.document().toPlainText()


def lines(win) -> list[str]:
    return [line.rstrip() for line in plain(win).split("\n")]


def index(found: list[str], line: str) -> int:
    assert found.count(line) == 1, (line, found)
    return found.index(line)


def line_start(edit, line: str) -> int:
    """The position of the Log's only line that is `line` (trailing spaces aside)."""
    starts = [m.start() for m in re.finditer(rf"(?m)^{re.escape(line)}[ \t]*$", edit.document().toPlainText())]
    assert len(starts) == 1, (line, edit.document().toPlainText())
    return starts[0]


def text_at(edit, text: str) -> int:
    pos = edit.document().toPlainText().find(text)
    assert pos >= 0, (text, edit.document().toPlainText())
    return pos


def rect_at(edit, pos: int) -> QRect:
    cursor = QTextCursor(edit.document())
    cursor.setPosition(pos)
    return edit.cursorRect(cursor)


def images(edit) -> list[tuple]:
    """(position, QTextImageFormat) of each picture in the Log's document, in order."""
    found, block = [], edit.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.charFormat().isImageFormat():
                found += [(fragment.position() + i, fragment.charFormat().toImageFormat())
                          for i in range(fragment.length())]
            it += 1
        block = block.next()
    return found


def size_of(fmt) -> tuple[float, float]:
    return fmt.width(), fmt.height()


def painted(edit, pos: int, fmt, corner=False) -> list[QColor]:
    """The colours the Log paints for the picture at `pos`, scrolled into view: in its top-left corner (2 to 4 px in,
    the diagram's margin) with `corner`, else across the part of it the view shows."""
    width, height = (round(v) for v in size_of(fmt))
    viewport, bars = edit.viewport(), (edit.horizontalScrollBar(), edit.verticalScrollBar())
    rect = rect_at(edit, pos)
    bars[0].setValue(bars[0].value() + rect.left() - 10)
    bars[1].setValue(bars[1].value() + rect.top() - 10)
    QApplication.processEvents()
    rect = rect_at(edit, pos)
    area = QRect(rect.left() + 2, rect.top() + 2, 3, 3) if corner else QRect(rect.left(), rect.top(), width, height)
    area = area.intersected(viewport.rect())
    assert not area.isEmpty(), (rect, viewport.rect())
    image = viewport.grab().toImage()
    ratio = image.devicePixelRatio()
    return [image.pixelColor(int(x * ratio), int(y * ratio))
            for x in range(area.left(), area.right() + 1) for y in range(area.top(), area.bottom() + 1)]


def contrast(a: QColor, b: QColor) -> float:
    """The WCAG contrast ratio of two colours (1 to 21)."""
    def luminance(c):
        def channel(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def near(a: QColor, b: QColor, tolerance=12) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))


LIGHT = {"Window": "#efefef", "WindowText": "#000000", "Base": "#ffffff", "AlternateBase": "#f7f7f7",
         "Text": "#000000", "Button": "#efefef", "ButtonText": "#000000", "Highlight": "#308cc6",
         "HighlightedText": "#ffffff", "PlaceholderText": "#7f7f7f", "Link": "#0000ff", "LinkVisited": "#ff00ff"}
DARK = {"Window": "#2b2d30", "WindowText": "#dfe1e5", "Base": "#1e1f22", "AlternateBase": "#26282b",
        "Text": "#dfe1e5", "Button": "#2b2d30", "ButtonText": "#dfe1e5", "Highlight": "#2f65ca",
        "HighlightedText": "#ffffff", "PlaceholderText": "#8c8c8c", "Link": "#589df6", "LinkVisited": "#b589ec"}


@pytest.fixture
def theme():
    """theme("light" | "dark") switches the editor's theme (the application's palette, as a system theme does); the
    palette is put back afterwards."""
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


def run_in_editor(win, qtbot, flow: Path):
    tab = win.open_flow(flow)
    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=90_000)
    assert tab.last["status"] == "success", (tab.last, plain(win))
    win.bottom_tabs.setCurrentWidget(win.log)
    QApplication.processEvents()
    return tab


def fake_package(folder: Path, name: str, code: str) -> Path:
    """A package `name` in `folder`, found before the installed one when `folder` is on PYTHONPATH."""
    (folder / name).mkdir(parents=True)
    (folder / name / "__init__.py").write_text(code, encoding="utf-8")
    return folder


def on_path(folder: Path) -> str:
    return os.pathsep.join([str(folder), *filter(None, [os.environ.get("PYTHONPATH")])])


# --- 3: drawn in the Log -----------------------------------------------------------------------------------------

def test_a_mermaid_fence_in_an_inspect_message_is_drawn_in_the_log(win, qtbot, tmp_path):
    """Decision 3 (the user's case): run in the editor, an Inspect block with Show as Markdown whose message has a
    ```mermaid fence shows, under its line, the Markdown with the diagram drawn as a picture in place of the fence's
    text: no fence text, no phase-1 note, no error note."""
    flow = write_flow(tmp_path / "steps.yaml", {
        "manual": {"type": "trigger.manual"},
        "make": python("output = 'nightly'"),
        "look": look(message="## Run {value}\n\n" + fence(SMALL) + "\nAll steps.", markdown=True),
    }, ["manual.start -> make.after", "make.output -> look.value"])

    run_in_editor(win, qtbot, flow)

    edit = win.log.text
    found = lines(win)
    [at] = [i for i, line in enumerate(found) if re.fullmatch(r"\d\d:\d\d:\d\d \[look\] INFO", line)]
    assert found[at + 1] == "Run nightly", found
    pictures = images(edit)
    assert len(pictures) == 1, found
    [(pos, fmt)] = pictures
    assert text_at(edit, "Run nightly") < pos < text_at(edit, "All steps.")
    assert "    load --> report" not in found and "graph TD" not in found, found
    assert PHASE_1_NOTE not in plain(win) and "(could not draw" not in plain(win)
    base = edit.palette().color(QPalette.Base)
    colors = painted(edit, pos, fmt)
    assert sum(not near(c, base) for c in colors) > 0.02 * len(colors), "the diagram is not drawn"


@pytest.mark.parametrize("source", [SMALL, WIDE, TALL], ids=["small", "wide", "tall"])
def test_a_diagram_is_shown_at_its_own_size_scaled_down_to_fit_like_a_picture(win, theme, source):
    """Decision 3: a diagram shows like an Inspect picture: at the size mmdr draws it at, scaled down (never up) to fit
    in report.MAX_IMAGE_WIDTH × report.MAX_IMAGE_WIDTH, keeping its proportions."""
    theme("light")
    show_log(win, [entry("Plan:\n\n" + fence(source), "markdown"), entry("next", second=1)])

    pictures = images(win.log.text)

    assert len(pictures) == 1, lines(win)
    width, height = size_of(pictures[0][1])
    natural_w, natural_h = natural_size(source)
    scale = min(1, MAX_IMAGE_WIDTH / max(natural_w, natural_h))
    assert max(width, height) <= MAX_IMAGE_WIDTH + 0.5, (width, height)
    if scale < 1:
        assert abs(max(width, height) - MAX_IMAGE_WIDTH) <= 1, (width, height)
    assert abs(width - natural_w * scale) <= max(1.5, 0.08 * natural_w * scale), (width, height, natural_w, natural_h)
    assert abs(height - natural_h * scale) <= max(1.5, 0.08 * natural_h * scale), (width, height, natural_w, natural_h)


@pytest.mark.parametrize("name", ["light", "dark"])
def test_a_diagram_is_drawn_on_the_logs_background_in_the_light_and_the_dark_theme(win, theme, name):
    """Decision 3: the diagram is drawn on the Log's own background (light or dark theme): its margin is the Log's
    background colour, and the diagram itself stands out from it."""
    theme(name)
    show_log(win, [entry(fence(unique(SMALL)), "markdown")])
    edit = win.log.text
    base = edit.palette().color(QPalette.Base)
    assert (base.lightness() < 128) == (name == "dark")
    pictures = images(edit)
    assert len(pictures) == 1, lines(win)
    [(pos, fmt)] = pictures

    margin = painted(edit, pos, fmt, corner=True)
    drawn = painted(edit, pos, fmt)

    assert all(near(c, base) for c in margin), ([c.name() for c in margin], base.name())
    assert max(contrast(c, base) for c in drawn) >= 3, "the diagram does not stand out from the background"


def test_a_diagram_is_drawn_once_per_theme_not_on_each_repaint(win, theme, monkeypatch):
    """Decision 3: a diagram is laid out once per (source, theme) and kept: painting the Log again, scrolling, a block
    filter switch, Show all, new lines and the same entries shown again draw nothing new; in the dark theme it is
    drawn on the dark background, and going back to the light theme and to the dark one again draws nothing new."""
    from mmdr import _mmdr, diagram  # mmdr 0.4.0 (pinned): lays a diagram out in _mmdr.render, rasterizes it in
    # Diagram._bitmap; the two steps that take time, counted here for this test's diagram only

    tag = "n" + uuid.uuid4().hex[:8]
    source = unique(SMALL, tag)
    laid_out, rasterized = [], []
    lay_out, rasterize = _mmdr.render, diagram.Diagram._bitmap
    monkeypatch.setattr(_mmdr, "render", lambda text, *a, **k: (laid_out.append(1) if tag in text else None,
                                                                lay_out(text, *a, **k))[1])
    monkeypatch.setattr(diagram.Diagram, "_bitmap", lambda self, *a, **k: (
        rasterized.append(1) if tag in self._source else None, rasterize(self, *a, **k))[1])
    entries = [entry("before", block="other"), entry("Plan:\n\n" + fence(source), "markdown", second=1)]

    def shown():
        show_log(win, list(entries))
        assert len(images(win.log.text)) == 1, lines(win)
        win.log.text.viewport().grab()
        return len(laid_out), len(rasterized)

    def shown_again(times=2):
        for _ in range(times):
            shown()
            win.log.text.verticalScrollBar().setValue(0)
            win.log.text.viewport().grab()
            win.log.set_filter("other")
            win.log.set_filter("look")
            win.log.clear_filter.click()  # Show all
            win.log.append(entry("more", block="other", second=2))
            QApplication.processEvents()
            win.log.text.viewport().grab()
        return len(laid_out), len(rasterized)

    theme("light")
    light = shown()
    assert light[0] == 1, f"laid out {light[0]} times"
    assert shown_again() == light, "drawn again"
    theme("dark")
    both = shown()
    assert both[0] <= 2, f"laid out {both[0]} times for two themes"
    [(pos, fmt)] = images(win.log.text)
    dark = win.log.text.palette().color(QPalette.Base)
    assert all(near(c, dark) for c in painted(win.log.text, pos, fmt, corner=True)), "not drawn for the dark theme"
    assert shown_again() == both, "drawn again"
    theme("light")
    shown_again(1)
    theme("dark")
    assert shown_again(1) == both, "drawn again on going back to a theme"


def test_the_log_draws_diagrams_without_the_network(win, theme, monkeypatch):
    """Decision 3: no network: the editor opens no connection and looks up no address while it draws a diagram."""
    tried = []

    def refuse(*args, **kwargs):
        tried.append(args[1:] if args and isinstance(args[0], socket.socket) else args)
        raise OSError("no network in this test")

    for target, name in ((socket, "create_connection"), (socket, "getaddrinfo"), (socket.socket, "connect"),
                         (socket.socket, "connect_ex")):
        monkeypatch.setattr(target, name, refuse)
    theme("light")

    show_log(win, [entry(fence(unique(SMALL)), "markdown")])
    win.log.text.viewport().grab()

    assert len(images(win.log.text)) == 1, lines(win)
    assert tried == []


# --- 3: a diagram that cannot be drawn ---------------------------------------------------------------------------

def is_grey_note(edit, text: str) -> bool:
    cursor = QTextCursor(edit.document())
    cursor.setPosition(text_at(edit, text) + 1)
    fmt = cursor.charFormat()
    color = fmt.foreground().color()
    return fmt.hasProperty(QTextFormat.ForegroundBrush) and color.hsvSaturation() < 40 and 64 <= color.lightness() <= 192


def test_a_diagram_mmdr_cannot_draw_shows_as_text_with_a_grey_note(win, theme):
    """Decision 3: a malformed diagram (mmdr raises ValueError) shows its fence as text and, after it, a grey line
    "(could not draw this diagram: <the error's first line>)"; the entry's other diagram is still drawn and the Log
    goes on."""
    theme("light")
    good, bad = unique(SMALL), MALFORMED
    show_log(win, [entry("Two diagrams:\n\n" + fence(good) + "\nand\n\n" + fence(bad), "markdown"),
                   entry("after", second=1)])
    edit = win.log.text
    found = lines(win)
    expected = note(mmdr_error(bad))

    assert "    start[Load --> report" in found and expected in found, found
    assert index(found, "    start[Load --> report") < index(found, expected) < index(found, header("after", second=1))
    assert is_grey_note(edit, expected)
    assert PHASE_1_NOTE not in plain(win)
    pictures = images(edit)
    assert len(pictures) == 1, found  # the good one
    [(pos, _)] = pictures
    assert text_at(edit, "Two diagrams:") < pos < text_at(edit, "    start[Load --> report")
    assert good.split("\n")[1] not in found

    win.log.append(entry("still going", second=2))
    assert header("still going", second=2) in lines(win)


def test_any_error_while_drawing_shows_its_first_line_and_the_editor_goes_on(win, theme, monkeypatch):
    """Decision 3: any exception while drawing (not only mmdr's ValueError) shows the fence as text and the note with
    the error's first line; the editor does not crash, and the Log goes on."""
    from mmdr import _mmdr

    def stopped(*args, **kwargs):
        raise RuntimeError("the engine stopped\nat step 2")

    monkeypatch.setattr(_mmdr, "render", stopped)
    theme("light")
    source = unique(SMALL)

    show_log(win, [entry(fence(source), "markdown"), entry("after", second=1)])

    found = lines(win)
    assert source.split("\n")[1] in found, found
    assert note("the engine stopped") in found, found
    assert is_grey_note(win.log.text, note("the engine stopped"))
    assert images(win.log.text) == []
    win.log.append(entry("still going", second=2))
    assert header("still going", second=2) in lines(win)


# --- 4: other fences and Markdown are unchanged; Show as Markdown off draws nothing -------------------------------

def test_each_mermaid_fence_becomes_a_picture_in_place_and_the_rest_of_the_markdown_is_unchanged(win, theme):
    """Decisions 3, 4: each ```mermaid fence of an entry becomes a picture where the fence was, also two fences one
    right after the other; a heading, a ```python fence and the paragraphs around stay as phase 1 draws them, and
    the next entry is an ordinary Log line."""
    theme("light")
    first, second, third = (unique(SMALL.replace("TD", way)) for way in ("TD", "LR", "BT"))
    text = ("# Plan\n\nbefore\n\n```python\nx  =  1\n```\n\n" + fence(first) + "\nbetween\n\n" + fence(second)
            + "\n" + fence(third) + "\nafter")
    show_log(win, [entry("start"), entry(text, "markdown", second=1), entry("next", second=2)])
    edit = win.log.text
    found = lines(win)

    pictures = [pos for pos, _ in images(edit)]
    assert len(pictures) == 3, found
    assert (text_at(edit, "before") < text_at(edit, "x  =  1") < pictures[0] < text_at(edit, "between")
            < pictures[1] < pictures[2] < text_at(edit, "after"))
    assert "x  =  1" in found and "Plan" in found
    for source in (first, second, third):
        assert source.split("\n")[1] not in found
    heading = edit.document().findBlock(text_at(edit, "Plan"))
    assert heading.blockFormat().headingLevel() > 0
    code = edit.document().findBlock(text_at(edit, "x  =  1"))
    assert code.blockFormat().stringProperty(QTextFormat.BlockCodeLanguage) == "python"
    after = line_start(edit, header("next", second=2))
    block = edit.document().findBlock(after)
    assert block.position() == after and block.blockFormat().headingLevel() == 0 and block.textList() is None
    assert QTextCursor(block).currentTable() is None
    assert rect_at(edit, after).x() == rect_at(edit, line_start(edit, header("start"))).x()


# --- 2, 4: the run keeps the source; reopened from history it is drawn the same -----------------------------------

def test_a_run_keeps_the_markdown_source_and_reopened_from_history_draws_it(win, qtbot, tmp_path):
    """Decisions 2, 3, 4: `taskloom run` never draws (it does not even load mmdr): its history keeps each Markdown
    source with the fence text and it prints the source; reopened from Run history, the editor draws the message's
    diagram and the text value's, while the Inspect block without Show as Markdown shows its text exactly as it is."""
    text = "Steps:\n\n" + fence(SMALL)
    flow = write_flow(tmp_path / "steps.yaml", {
        "manual": {"type": "trigger.manual"},
        "make": python(f"output = {text!r}"),
        "drawn": look(markdown=True),
        "said": look(message="## Checks\n\n" + fence("graph LR\n    check --> send\n"), markdown=True),
        "plain": look(),
    }, ["manual.start -> make.after", "make.output -> drawn.value", "make.output -> said.value",
        "make.output -> plain.value"])
    fake = fake_package(tmp_path / "fake", "mmdr", "from pathlib import Path\n"
                        "Path(__file__).parents[2].joinpath('mmdr-was-imported').write_text('yes', encoding='utf-8')\n"
                        "raise ImportError('the run imported mmdr')\n")

    done = subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow)], cwd=tmp_path, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120,
                          env={**os.environ, "TASKLOOM_HOME": str(win.home.root), "PYTHONPATH": on_path(fake)})

    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "mmdr-was-imported").exists(), "the run loaded mmdr"
    history = History(win.home.history_db)
    try:
        [run] = history.runs()
        logged = {e["block"]: (e["message"], e.get("show")) for e in history.logs(run["id"]) if e["block"]}
    finally:
        history.close()
    said = "## Checks\n\n" + fence("graph LR\n    check --> send\n")
    assert logged["drawn"] == (text, "markdown") and logged["said"] == (said, "markdown"), logged
    assert logged["plain"] == (text, None), logged
    assert "\n```mermaid\ngraph LR\n    check --> send\n```" in done.stderr, done.stderr

    win.open_flow(flow)
    win.bottom_tabs.setCurrentWidget(win.history_panel)
    table = win.history_panel.table
    qtbot.waitUntil(lambda: table.rowCount() == 1, timeout=10_000)
    at = table.visualItemRect(table.item(0, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # show that run
    QApplication.processEvents()
    assert win.bottom_tabs.currentWidget() is win.log
    edit = win.log.text
    found = lines(win)

    assert len(images(edit)) == 2, found
    assert "    check --> send" not in found
    [plain_at] = [i for i, line in enumerate(found) if re.fullmatch(r"\d\d:\d\d:\d\d \[plain\] INFO Steps:", line)]
    assert found[plain_at:plain_at + 6] == [found[plain_at], "", "```mermaid", "graph TD", "    load --> report", "```"]
    assert found.count("    load --> report") == 1, found  # only the plain one: the drawn one is a picture
    assert "(could not draw" not in plain(win) and PHASE_1_NOTE not in plain(win)


# --- 6: what the help, the description and README say ------------------------------------------------------------

def sentences_about_mermaid(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if "Mermaid" in s]


def readme_inspect() -> str:
    """README's paragraph about the Inspect block, with its sub-points, up to the next point."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    start = text.index("**Inspect** block")
    end = re.search(r"\n- ", text[start:])
    return text[start:start + end.start()] if end else text[start:]


def test_the_help_the_description_and_readme_say_mermaid_diagrams_are_drawn():
    """Decision 6: Show as Markdown's help, the Inspect block's description and README's Inspect paragraph say that
    Mermaid diagrams are drawn; phase 1's "Mermaid diagrams show as their text" is gone."""
    from taskloom.blocks.logic import Inspect

    for where, text in (("help", Inspect.config["markdown"].help), ("description", Inspect.description),
                        ("README", readme_inspect())):
        said = sentences_about_mermaid(text)
        assert said, f"the {where} does not mention Mermaid: {text}"
        assert any(re.search(r"\bdraw", s, re.I) for s in said), f"the {where} does not say they are drawn: {said}"
        flat = " ".join(text.split())
        assert "show as their text" not in flat and "no offline Mermaid engine" not in flat, (where, text)
    readme = " ".join((REPO / "README.md").read_text(encoding="utf-8").split())
    assert "no offline Mermaid engine" not in readme and "Mermaid diagrams show as their text" not in readme


# --- 1, 5: the packaged builds -----------------------------------------------------------------------------------

def spec_build(monkeypatch, headless: bool) -> dict:
    """What packaging/taskloom.spec hands PyInstaller's Analysis for the editor build or the runner build (headless):
    its hidden imports and excludes. PyInstaller's own collect_* helpers run; the build steps are recorded, not run."""
    monkeypatch.setenv("TASKLOOM_HEADLESS", "1" if headless else "0")
    seen = {}

    def analysis(scripts, **options):
        seen.update(options, scripts=scripts)
        return types.SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[], zipfiles=[])

    spec = REPO / "packaging" / "taskloom.spec"
    namespace = {"__name__": "__main__", "__file__": str(spec), "SPECPATH": str(spec.parent), "Analysis": analysis,
                 "PYZ": lambda *a, **k: None, "EXE": lambda *a, **k: None, "COLLECT": lambda *a, **k: None}
    exec(compile(spec.read_text(encoding="utf-8"), str(spec), "exec"), namespace)
    return seen


def excluded(build: dict, module: str) -> bool:
    return any(module == e or module.startswith(e + ".") for e in build.get("excludes") or [])


def imported_by(module: str) -> set[str]:
    """Every module `module`'s code imports by its full name, at its top or inside a function (PyInstaller finds
    both)."""
    origin = importlib.util.find_spec(module).origin
    found, codes = set(), [compile(Path(origin).read_text(encoding="utf-8"), origin, "exec")]
    while codes:
        code = codes.pop()
        steps = list(dis.get_instructions(code))
        found |= {step.argval for i, step in enumerate(steps) if step.opname == "IMPORT_NAME" and steps[i - 2].argval == 0}
        codes += [c for c in code.co_consts if isinstance(c, types.CodeType)]
    return found


def taskloom_modules(build: dict) -> list[str]:
    return [m for m in build["hiddenimports"] if m.split(".")[0] == "taskloom" and not excluded(build, m)]


def test_the_runner_build_has_no_mermaid_engine(monkeypatch):
    """Decision 1: the Linux runner build (headless) collects neither mmdr nor novasvg: none of its modules imports
    them (they are imported only in the editor, which it leaves out) and they are not added to it."""
    build = spec_build(monkeypatch, headless=True)

    assert excluded(build, "taskloom.editor")
    assert [m for m in build["hiddenimports"] if m.split(".")[0] in ENGINE and not excluded(build, m)] == []
    found = [(m, name) for m in taskloom_modules(build) for name in imported_by(m)
             if name.split(".")[0] in ENGINE and not excluded(build, name)]
    assert found == []


def test_the_editor_build_bundles_the_mermaid_engine(monkeypatch):
    """Decisions 1, 5: the editor build (the Windows zip) collects mmdr (and novasvg, which mmdr draws its pictures
    with): the editor imports mmdr (or the build adds it) and the build excludes neither."""
    build = spec_build(monkeypatch, headless=False)

    assert not excluded(build, "mmdr") and not excluded(build, "novasvg"), build["excludes"]
    users = [m for m in taskloom_modules(build) if any(n.split(".")[0] == "mmdr" for n in imported_by(m))]
    assert users or "mmdr" in build["hiddenimports"], "nothing in the editor build imports mmdr"


FAKE_PYINSTALLER = '''\
import sys
from pathlib import Path

args = sys.argv[1:]
folder = Path(args[args.index("--distpath") + 1]) / "taskloom"
folder.mkdir(parents=True, exist_ok=True)
(folder / "programs.txt").write_text("(the programs PyInstaller makes)", encoding="utf-8")
'''


def archive_files(archive: Path) -> dict[str, bytes]:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            return {n: z.read(n) for n in z.namelist() if not n.endswith("/")}
    with tarfile.open(archive) as t:
        return {m.name: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}


def installed(distribution: str, folder: str) -> list[bytes]:
    """The files of an installed distribution's .dist-info/`folder` (licenses, sboms)."""
    return [f.locate().read_bytes() for f in metadata.distribution(distribution).files
            if f.parent.name == folder and ".dist-info" in str(f)]


def as_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()


def test_the_editor_build_carries_the_mermaid_engines_licence_notices(tmp_path):
    """Decision 5: the editor build's archive has, in its licenses folder, mmdr's and novasvg's licence files, mmdr's
    component list (the SBOM in its wheel), the MPL-2.0 and EPL-2.0 licence texts, and for each MPL-2.0 or EPL-2.0
    component of mmdr a link to its source. packaging/build.py runs as in CI, on a copy of the project, with
    PyInstaller's (minutes long) bundling left out."""
    repo = tmp_path / "repo"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", ".venv", "dist", "build", "tests", "__pycache__",
                                                             ".pytest_cache"))
    fake = fake_package(tmp_path / "fake", "PyInstaller", "")
    (fake / "PyInstaller" / "__main__.py").write_text(FAKE_PYINSTALLER, encoding="utf-8")

    done = subprocess.run([sys.executable, str(repo / "packaging" / "build.py")], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300,
                          env={**os.environ, "PYTHONPATH": on_path(fake)})

    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    [archive] = [p for p in (repo / "dist").iterdir() if p.name.endswith((".zip", ".tar.gz"))]
    files = {name: data for name, data in archive_files(archive).items() if name.startswith("taskloom/licenses/")}
    texts = {name: as_text(data) for name, data in files.items()}
    for distribution in ("mmdr", "novasvg"):
        licences = installed(distribution, "licenses")
        assert licences, distribution
        for licence in licences:
            assert as_text(licence) in texts.values(), f"{distribution}'s licence is not in {sorted(files)}"
    [sbom] = installed("mmdr", "sboms")
    components = json.loads(sbom)

    def is_sbom(text):
        try:
            return json.loads(text) == components
        except ValueError:
            return False

    assert any(is_sbom(text) for text in texts.values()), f"mmdr's SBOM is not in {sorted(files)}"
    notices = " ".join(" ".join(text.split()) for text in texts.values() if not is_sbom(text))
    for marker in ("Mozilla Public License Version 2.0", "Source Code Form License Notice",
                   "Incompatible With Secondary Licenses", "Eclipse Public License",
                   "THE ACCOMPANYING PROGRAM IS PROVIDED UNDER THE TERMS OF THIS ECLIPSE PUBLIC LICENSE",
                   "Form of Secondary Licenses Notice"):
        assert marker in notices, f"no {marker!r} (the MPL-2.0 and EPL-2.0 texts) in {sorted(files)}"
    copyleft = [c for c in components["components"]
                if re.search(r"\b(MPL|EPL)-2\.0\b", json.dumps(c.get("licenses") or []))]
    assert {c["name"] for c in copyleft} >= {"cssparser", "dtoa-short", "selectors", "merman-elk-layered"}
    for component in copyleft:
        name = component["name"]
        sources = [r["url"] for r in component.get("externalReferences", []) if r.get("type") == "vcs"]
        links = [*sources, f"crates.io/crates/{name}"]
        assert name in notices and any(link in notices for link in links), f"no link to {name}'s source ({links})"


def test_the_editor_smoke_check_fails_when_it_cannot_draw_a_diagram(tmp_path):
    """Decision 5: the editor's smoke mode (TASKLOOM_EDITOR_SMOKE, which packaging/smoke.py runs on the frozen app)
    also draws a Mermaid diagram: it passes with mmdr, and fails (exits non-zero) when mmdr cannot be loaded, as in a
    build that left it out."""
    flow = write_flow(tmp_path / "hello.yaml", {"manual": {"type": "trigger.manual"},
                                                "hello": python("print('hello')", outputs=())},
                      ["manual.start -> hello.after"])
    missing = fake_package(tmp_path / "fake", "mmdr", "raise ImportError('mmdr is not in this build')\n")

    def smoke(name, extra=None):
        environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "TASKLOOM_HOME": str(tmp_path / name / "home"),
                       "TASKLOOM_EDITOR_SMOKE": str(tmp_path / name / "committed"),
                       "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
        if extra:
            environment["PYTHONPATH"] = on_path(extra)
        return subprocess.run([sys.executable, "-m", "taskloom.cli", "editor", str(flow)], env=environment,
                              cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=200)

    working = smoke("with-mmdr")
    assert working.returncode == 0, (working.returncode, working.stdout[-1500:], working.stderr[-1500:])

    broken = smoke("without-mmdr", missing)
    assert broken.returncode != 0, "the smoke check passed although no Mermaid diagram can be drawn"
