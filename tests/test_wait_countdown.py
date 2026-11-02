"""While a Wait block waits, the editor shows the time left on a line at the bottom of the block, with the look of the
Schedule block's countdown, counting down every second; the Log says once how long it waits and until when. Each test
names the point of the specification it protects:

1. When a Wait block starts waiting, the run's Log gets exactly one line from that block, once: "waiting 90 s, until
   14:05:30" (local time; a fraction of a second as given, "waiting 0.5 s, until …"); nothing more while it waits. In
   the editor's Log, in `taskloom run`'s output and in the run history.
2. A Wait block on the canvas always has a line of its own at the bottom, just above its result line, exactly like a
   Schedule block's countdown line ("⏱ " and the text colour), so the block does not change size when it starts
   waiting. While it waits it counts down every second, "⏱ 00:00:42 left" (with days as the Schedule block shows
   them, "⏱ 1d 01:01:05 left"); when it does not, it says how long it waits, "⏱ waits 5 s" (the seconds as written,
   "⏱ waits 0.5 s", "⏱ waits {value} s"), updated when the setting is edited. It reads well in Light and in Dark. The
   countdown is computed from the end time the run gives, so it stays right after the editor was busy for a moment.
3. When the wait ends (time up, Stop, the block's timeout, the run failing) the line goes back at once to saying how
   long it waits; nothing is left counting; the result line shows the block's usual result or status. A Wait of 0
   seconds shows no countdown.
4. The same in a re-run of the block (Run only this block / from this block on); past runs (from the run history,
   e.g. one the scheduler started) show no countdown.
5. Nothing changes for runs without the editor beyond the Log line (no other output).
6. README: the Wait block's line says the editor shows the time left on the block while it waits, as a Schedule block
   shows its next run.

Through the real editor (the `win` fixture, with real runner processes) and the real `taskloom run` (`env`). What the
canvas shows is read from what it draws: the texts painted where the block is, where and in which colour."""

import datetime as dt
import math
import os
import re
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem, QGraphicsTextItem, QToolTip  # noqa: E402

from tests.test_inspect_mermaid import contrast  # noqa: E402
from tests.test_lines import menu_action, run_elsewhere  # noqa: E402
from tests.test_paths_editor_editing import field, select_only, type_in  # noqa: E402
from tests.test_theme import choose  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CLOCK = "⏱ "  # how the Schedule block's countdown line begins
LEFT = re.compile(r"⏱ (?:(\d+)d )?(\d\d):(\d\d):(\d\d) left")
UNTIL = re.compile(r"waiting (\S+) s, until (\d\d:\d\d:\d\d)\b")
TOLERANCE = 2.0  # seconds: a time left shown against the real one (whole seconds shown, a tick each second)
READABLE = 4.5  # WCAG contrast for text


# --- flows --------------------------------------------------------------------------------------------------------

def wait(seconds, **more) -> dict:
    return {"type": "logic.wait", "config": {"seconds": seconds}, **more}


OUTPUT = {"trigger.manual": "start", "trigger.schedule": "start", "logic.python": "output"}  # (else value)


def flow_file(path: Path, blocks: dict, start: dict | None = None, edges=(), apart: dict | None = None) -> Path:
    """A flow: a start block `start` (a Manual Start), then `blocks` one after the other (each run after the one
    before), side by side on the canvas; plus `edges`, and the blocks `apart` (connected to nothing, placed by their
    own ui)."""
    names = ["start", *blocks]
    specs = {"start": start or {"type": "trigger.manual"}, **blocks}
    chained = [f"{a}.{OUTPUT.get(specs[a]['type'], 'value')} -> {b}.after" for a, b in zip(names, names[1:])]
    data = {"blocks": {**{name: {**specs[name], "ui": {"x": 300 * i, "y": 0}} for i, name in enumerate(names)},
                       **(apart or {})},
            "edges": chained + list(edges)}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


# --- what the canvas draws ----------------------------------------------------------------------------------------

class _Recording(QPainter):
    """A painter that keeps each text drawn with it: (how far down, the text, its colour)."""

    def __init__(self, device):
        super().__init__(device)
        self.texts = []

    def drawText(self, *args):
        text = next((a for a in reversed(args) if isinstance(a, str)), None)
        if text is not None:
            first = args[0]
            if isinstance(first, (QRectF, QRect)):
                y = self.transform().map(QRectF(first).center()).y()
            elif isinstance(first, (QPointF, QPoint)):
                y = self.transform().map(QPointF(first)).y()
            else:
                y = self.transform().map(QPointF(args[0], args[1])).y()
            self.texts.append((y, text, self.pen().color().name()))
        return super().drawText(*args)

    def drawStaticText(self, *args):
        point = QPointF(args[0]) if len(args) == 2 else QPointF(args[0], args[1])
        self.texts.append((self.transform().map(point).y(), args[-1].text(), self.pen().color().name()))
        return super().drawStaticText(*args)


def _render(tab, block, painter_class=QPainter) -> tuple[QImage, QPainter, QRectF]:
    area = tab.scene.nodes[block].sceneBoundingRect()
    image = QImage(math.ceil(area.width()), math.ceil(area.height()), QImage.Format_ARGB32)
    image.fill(0)
    painter = painter_class(image)
    tab.scene.render(painter, QRectF(0, 0, area.width(), area.height()), area)
    painter.end()
    return image, painter, area


def drawn(tab, block) -> list[tuple[float, str, str]]:
    """The texts the canvas draws where `block` is (its name, its ports, its lines at the bottom), from the top down:
    (how far above the bottom of the block, the text, its colour)."""
    QApplication.processEvents()
    _, painter, area = _render(tab, block, _Recording)
    texts = list(painter.texts)
    for item in tab.scene.items(area):  # text items Qt draws itself (not through the painter's drawText)
        if item.isVisible() and isinstance(item, QGraphicsSimpleTextItem):
            texts.append((item.sceneBoundingRect().center().y() - area.top(), item.text(),
                          item.brush().color().name()))
        elif item.isVisible() and isinstance(item, QGraphicsTextItem):
            texts.append((item.sceneBoundingRect().center().y() - area.top(), item.toPlainText(),
                          item.defaultTextColor().name()))
    return [(round(area.height() - y, 1), text, colour) for y, text, colour in sorted(texts, key=lambda t: t[0])]


def background(tab, block) -> QColor:
    """The colour the block is filled with (most of what it paints)."""
    image, _, _ = _render(tab, block)
    counts = Counter(image.pixel(x, y) for y in range(image.height()) for x in range(image.width())
                     if QColor.fromRgba(image.pixel(x, y)).alpha() == 255)
    return QColor.fromRgb(counts.most_common(1)[0][0])


def clock_line(tab, block) -> tuple[float, str, str] | None:
    """The line of `block` that begins as the Schedule block's countdown does ("⏱ "): (how far above the bottom of the
    block, its text, its colour); None if it has none."""
    found = [t for t in drawn(tab, block) if t[1].startswith(CLOCK)]
    assert len(found) <= 1, f"{block} draws several ⏱ lines: {drawn(tab, block)}"
    return found[0] if found else None


def line(tab, block) -> str | None:
    found = clock_line(tab, block)
    return found[1] if found else None


def assert_line_above_the_result(tab, block):
    """Spec 2: the ⏱ line is the block's own line at its bottom, just above its result line (the last line)."""
    texts = drawn(tab, block)
    assert len(texts) >= 2 and texts[-2][1].startswith(CLOCK), \
        f"{block}'s ⏱ line is not the line just above its result line (the last one): {texts}"


def as_shown(seconds: int) -> str:
    """A time left as the Schedule block's countdown writes it: 00:01:30, from a day 1d 01:01:05."""
    days, rest = divmod(seconds, 86400)
    return f"{f'{days}d ' if days else ''}{rest // 3600:02d}:{rest % 3600 // 60:02d}:{rest % 60:02d}"


def seconds_left(text: str | None) -> int:
    """The seconds a "⏱ … left" line says, which must be written as the Schedule block's countdown writes them."""
    match = LEFT.fullmatch(text or "")
    assert match, f"{text!r} is not a time left written as the Schedule block's countdown writes it (⏱ 00:00:42 left)"
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    n = days * 86400 + hours * 3600 + minutes * 60 + seconds
    assert text == f"{CLOCK}{as_shown(n)} left", f"{text!r} is not written as {CLOCK}{as_shown(n)} left"
    return n


def counting(text: str | None) -> bool:
    return text is not None and text.endswith(" left")


def until_times(start: dt.datetime, seconds: float) -> set[str]:
    """The local times (HH:MM:SS) a wait of `seconds` begun at `start` ends at, to the second either way."""
    end = start + dt.timedelta(seconds=seconds)
    return {(end + dt.timedelta(seconds=d)).strftime("%H:%M:%S") for d in (-1, 0, 1)}


def poll(qtbot, condition, seconds: float, message):
    """Let the editor run until condition() holds, for up to `seconds`; else fail with message() (a text)."""
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(message())
        qtbot.wait(20)


# --- a run of the editor, followed ---------------------------------------------------------------------------------

class Watch:
    """The run the editor just started, as the editor shows it: after each of its events, and after its process
    ended, once the editor has handled it, the ⏱ line of `block`."""

    def __init__(self, tab, block: str):
        self.tab, self.block, self.run = tab, block, tab.runs[-1]
        self.seen: list[tuple[dict, str | None]] = []
        self.started: dt.datetime | None = None  # when the block began (the run's clock: local time)
        self.ended = False
        self.run.event.connect(self._event)
        self.run.finished.connect(self._ended)

    def _event(self, e: dict):
        if e.get("event") == "block_started" and e.get("block") == self.block:
            self.started = dt.datetime.fromisoformat(e["ts"])
        self.seen.append((e, line(self.tab, self.block)))

    def _ended(self, status: str):
        self.ended = True
        self.seen.append(({"event": "process ended", "status": status}, line(self.tab, self.block)))

    def left_now(self, seconds: float) -> float:
        """The real time left of a wait of `seconds` begun when the block began."""
        return seconds - (dt.datetime.now() - self.started).total_seconds()

    def after_finished(self) -> str | None:
        """The ⏱ line right after the editor handled the block's end (its block_finished event)."""
        ends = [shown for e, shown in self.seen if e.get("event") == "block_finished" and e.get("block") == self.block]
        assert ends, f"the run never finished {self.block}: {[e for e, _ in self.seen]}"
        return ends[-1]


@contextmanager
def started(win, tab, block: str, how=None):
    """Start a run from the editor (Run now, or how()) and follow it (a Watch). A test that fails while it is going
    kills it, so that closing the editor after the test does not ask about it."""
    before = len(tab.runs)
    (how or (lambda: win.run_flow(tab)))()
    assert len(tab.runs) == before + 1, "no run was started"
    watch = Watch(tab, block)
    try:
        yield watch
    finally:
        if watch.run.running:
            watch.run.proc.kill()
            watch.run.proc.waitForFinished(5000)


def wait_counting(qtbot, watch: Watch) -> str:
    """Wait for the block to begin (its run's process starts first), then for its line to count down."""
    poll(qtbot, lambda: watch.started is not None or watch.ended, 60,
         lambda: f"{watch.block} did not begin: {[e for e, _ in watch.seen]}")
    assert watch.started is not None, f"the run ended before {watch.block} began: {[e for e, _ in watch.seen]}"
    poll(qtbot, lambda: counting(line(watch.tab, watch.block)), 5,
         lambda: f"{watch.block}'s line does not count down 5 s after it began waiting; it draws "
                 f"{drawn(watch.tab, watch.block)}")
    return line(watch.tab, watch.block)


def wait_ended(qtbot, watch: Watch):
    poll(qtbot, lambda: watch.ended, 60, lambda: f"the run did not end: {[e for e, _ in watch.seen]}")


def height(tab, block) -> float:
    return tab.scene.nodes[block].sceneBoundingRect().height()


def assert_usual_result(tab, block: str, status: str):
    """Spec 3: the block shows its result or status as it does today, as its last line."""
    node = tab.scene.nodes[block]
    texts = drawn(tab, block)
    assert node.status == status, (node.status, texts)
    last = texts[-1][1]  # (cut to fit, ending in …, when it is long)
    assert last == node.status_text or last.endswith("…") and node.status_text.startswith(last[:-1]), \
        f"the last line of {block} is not its {status} result {node.status_text!r}: {texts}"


def assert_nothing_left_counting(qtbot, tab):
    """Spec 3: a little over a second later (a tick of a countdown), no block's line counts down."""
    qtbot.wait(1500)
    still = {block: line(tab, block) for block in tab.scene.nodes}
    assert [text for text in still.values() if counting(text)] == [], still


def waiting_lines(win, seconds_text: str) -> list[str]:
    return [text for text in win.log.text.toPlainText().splitlines()
            if (match := UNTIL.search(text)) and match.group(1) == seconds_text]


def assert_logged_once(win, block: str, seconds_text: str, started: dt.datetime, seconds: float):
    """Spec 1: one line in the editor's Log, from `block`: waiting <seconds> s, until <its end, local time>."""
    lines = waiting_lines(win, seconds_text)
    assert len(lines) == 1, f"not one 'waiting {seconds_text} s, until …' line in the Log:\n" \
                            f"{win.log.text.toPlainText()}"
    assert f"[{block}]" in lines[0], lines[0]
    assert UNTIL.search(lines[0]).group(2) in until_times(started, seconds), \
        (lines[0], sorted(until_times(started, seconds)))


# --- spec 1 and 5: the Log line, in `taskloom run` and in the run history -------------------------------------------

TODAY = [  # the lines `taskloom run` prints today for such a run (each after the time it prints first)
    r"run #\d+ of pause started",
    r"\[\w+\] started \(attempt 1\)",
    r"\[\w+\] success · .*",
    r"run #\d+ success",
]


def test_taskloom_run_logs_once_how_long_each_wait_waits_and_until_when_and_nothing_else(env):
    """Spec 1 (one line from each Wait block, once, also over a wait of several seconds; a fraction of a second as
    given; the end in local time; kept in the run history like any log line) and spec 5 (nothing else in the output
    of a run without the editor)."""
    flow = flow_file(env.root / "pause.yaml", {"half": wait(0.5), "three": wait(3)})

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    printed = {}
    for block, given in (("half", "0.5"), ("three", "3")):
        found = [text for text in lines if UNTIL.search(text) and f"[{block}]" in text]
        assert len(found) == 1, f"not one 'waiting … s, until …' line from {block}:\n{result.stderr}"
        assert re.fullmatch(rf"\d\d:\d\d:\d\d \[{block}\] \w+: waiting {re.escape(given)} s, until \d\d:\d\d:\d\d",
                            found[0]), found[0]
        printed[block] = found[0].split(": ", 1)[1]
    others = [text for text in lines if not UNTIL.search(text)]
    assert len(others) == 8 and all(any(re.fullmatch(r"\d\d:\d\d:\d\d " + shape, text) for shape in TODAY)
                                    for text in others), result.stderr
    run_id, status = env.last_run()
    assert status == "success"
    kept = env.query("SELECT block, ts, message FROM logs WHERE run_id=? AND block IN ('half', 'three')", run_id)
    assert sorted((block, message) for block, _, message in kept) == sorted(printed.items()), kept
    for block, ts, message in kept:
        seconds = {"half": 0.5, "three": 3}[block]
        assert UNTIL.search(message).group(2) in until_times(dt.datetime.fromisoformat(ts), seconds), (ts, message)


# --- spec 2: the line at the bottom of a Wait block, when it does not wait ------------------------------------------

def test_a_wait_block_has_the_schedule_blocks_line_saying_how_long_it_waits_updated_when_edited(win, tmp_path):
    """Spec 2 (a Wait block always has a line of its own at its bottom, just above its result line, where and in the
    colour a Schedule block draws its countdown, "⏱ "; when it does not wait it says how long it waits, the seconds as
    written, a placeholder as written; updated when Seconds is edited in Properties)."""
    path = flow_file(tmp_path / "lines.yaml", {"five": wait(5), "half": wait(0.5), "given": wait("{value}")},
                     start={"type": "trigger.schedule", "config": {"cron": "0 7 * * *"}})
    tab = win.open_flow(path)
    QApplication.processEvents()

    schedule = clock_line(tab, "start")
    assert schedule is not None, f"the Schedule block draws no ⏱ line: {drawn(tab, 'start')}"
    for block, says in (("five", "⏱ waits 5 s"), ("half", "⏱ waits 0.5 s"), ("given", "⏱ waits {value} s")):
        found = clock_line(tab, block)
        assert found is not None and found[1] == says, f"{block} does not draw {says!r}: {drawn(tab, block)}"
        assert_line_above_the_result(tab, block)
        assert abs(found[0] - schedule[0]) <= 1, \
            f"{block}'s line is not where the Schedule block's is (above the bottom): {found} vs {schedule}"
        assert found[2] == schedule[2], f"{block}'s line is not in the Schedule line's colour: {found} vs {schedule}"

    select_only(tab, "five")
    try:
        type_in(field(win, "Seconds"), "90")
        assert line(tab, "five") == "⏱ waits 90 s", drawn(tab, "five")
        type_in(field(win, "Seconds"), "{value}")
        assert line(tab, "five") == "⏱ waits {value} s", drawn(tab, "five")
    finally:
        tab.doc.dirty = False  # (closing the editor after the test does not ask to save)


# --- spec 1, 2 and 3: the countdown in the editor -------------------------------------------------------------------

def test_a_wait_counts_down_every_second_on_its_line_and_stop_ends_it(win, qtbot, tmp_path):
    """The user's scenario. Spec 2 (the line at the bottom counts down, "⏱ 00:01:29 left" for 90 s, a new value each
    second that stays the real time left; just above the result line, which still says it is running; the block keeps
    its size), spec 1 (the Log line, once, also after several seconds of waiting) and spec 3 (Stop: at once the line
    says how long it waits again, the block shows its usual status; nothing left counting)."""
    tab = win.open_flow(flow_file(tmp_path / "pause.yaml", {"pause": wait(90)}))
    QApplication.processEvents()
    before = height(tab, "pause")
    assert line(tab, "pause") == "⏱ waits 90 s", drawn(tab, "pause")
    with started(win, tab, "pause") as watch:
        text = wait_counting(qtbot, watch)

        assert_line_above_the_result(tab, "pause")
        assert drawn(tab, "pause")[-1][1] == "running", drawn(tab, "pause")
        assert height(tab, "pause") == before, "the block changed size when it began waiting"
        assert abs(seconds_left(text) - watch.left_now(90)) <= TOLERANCE, (text, watch.left_now(90))
        changes, shown, deadline = [], text, time.monotonic() + 6.2
        while time.monotonic() < deadline:
            qtbot.wait(20)
            now = line(tab, "pause")
            assert counting(now), f"the line stopped counting down while it waits: {drawn(tab, 'pause')}"
            if now != shown:
                changes.append((seconds_left(now), round(watch.left_now(90), 2)))
                shown = now
        assert len(changes) >= 5, f"it does not count down every second; in 6.2 s it showed {text}, then {changes}"
        assert all(abs(n - real) <= TOLERANCE for n, real in changes), changes
        assert all(b[0] < a[0] for a, b in zip(changes, changes[1:])), changes
        assert height(tab, "pause") == before, "the block changed size while it waits"
        assert_logged_once(win, "pause", "90", watch.started, 90)

        win.stop(tab)
        wait_ended(qtbot, watch)

    assert watch.after_finished() == "⏱ waits 90 s", f"not at once back to how long it waits: {watch.seen}"
    assert_usual_result(tab, "pause", "cancelled")
    assert line(tab, "pause") == "⏱ waits 90 s" and height(tab, "pause") == before, drawn(tab, "pause")
    assert_nothing_left_counting(qtbot, tab)


def test_from_a_day_the_time_left_has_days_and_the_blocks_timeout_ends_it(win, qtbot, tmp_path):
    """Spec 2 (with days as the Schedule block shows them: "⏱ 1d 01:01:05 left") and spec 3 (the block's timeout
    ends the wait: at once the line says how long it waits, the block shows its usual failed result; nothing left
    counting)."""
    tab = win.open_flow(flow_file(tmp_path / "long.yaml", {"pause": wait(90065, timeout="3s")}))
    with started(win, tab, "pause") as watch:
        text = wait_counting(qtbot, watch)
        assert abs(seconds_left(text) - watch.left_now(90065)) <= TOLERANCE, (text, watch.left_now(90065))
        wait_ended(qtbot, watch)

    assert watch.after_finished() == "⏱ waits 90065 s", f"not at once back to how long it waits: {watch.seen}"
    assert_usual_result(tab, "pause", "failed")
    assert "timed out" in tab.scene.nodes["pause"].status_text
    assert_nothing_left_counting(qtbot, tab)


def test_when_the_blocks_timeout_ends_an_attempt_and_a_retry_follows_the_line_stops_counting_until_the_next_attempt_waits(
        win, qtbot, tmp_path):
    """Spec 3 (the block's timeout ends the wait of an attempt and a retry follows: at once the line says how long it
    waits, and nothing counts down during the delay before the next attempt) and spec 2 (attempt 2 counts down again,
    from its own start)."""
    retry = {"policy": "fixed", "delay": "6s", "max_attempts": 2}
    tab = win.open_flow(flow_file(tmp_path / "retry.yaml", {"pause": wait(60, timeout="2s", retry=retry)}))
    with started(win, tab, "pause") as watch:
        wait_counting(qtbot, watch)
        poll(qtbot, lambda: "attempt 1/2 failed" in win.log.text.toPlainText(), 15,
             lambda: win.log.text.toPlainText())
        assert line(tab, "pause") == "⏱ waits 60 s", f"attempt 1 ended, yet: {drawn(tab, 'pause')}"
        during_delay = set()
        for _ in range(15):  # about 1.5 s of the 6 s delay before attempt 2
            qtbot.wait(100)
            during_delay.add(line(tab, "pause"))
        assert during_delay == {"⏱ waits 60 s"}, f"it counted down while nothing waits: {during_delay}"

        poll(qtbot, lambda: counting(line(tab, "pause")), 10,
             lambda: f"attempt 2 does not count down: {drawn(tab, 'pause')}")
        text = line(tab, "pause")  # (watch.started is now attempt 2's start)
        assert abs(seconds_left(text) - watch.left_now(60)) <= TOLERANCE, (text, watch.left_now(60))
        win.stop(tab)
        wait_ended(qtbot, watch)

    assert watch.after_finished() == "⏱ waits 60 s", f"not at once back to how long it waits: {watch.seen}"
    assert_usual_result(tab, "pause", "cancelled")
    assert_nothing_left_counting(qtbot, tab)


def test_when_the_time_is_up_the_line_says_how_long_it_waits_and_the_block_its_result(win, qtbot, tmp_path):
    """Spec 3 (time up: at once the line says how long it waits, the block shows its usual result; nothing left
    counting) and spec 2 (it counted down while it waited)."""
    tab = win.open_flow(flow_file(tmp_path / "short.yaml", {"pause": wait(3)}))
    with started(win, tab, "pause") as watch:
        text = wait_counting(qtbot, watch)
        wait_ended(qtbot, watch)

    assert abs(seconds_left(text) - 3) <= TOLERANCE, text
    assert watch.after_finished() == "⏱ waits 3 s", f"not at once back to how long it waits: {watch.seen}"
    assert_usual_result(tab, "pause", "success")
    assert_nothing_left_counting(qtbot, tab)


def test_when_the_run_fails_while_it_waits_the_countdown_ends_at_once(win, qtbot, tmp_path):
    """Spec 3 (the run fails while it waits: its process stops unexpectedly and the editor shows the block failed; at
    once the line says how long it waits; nothing left counting)."""
    tab = win.open_flow(flow_file(tmp_path / "crash.yaml", {"pause": wait(60)}))
    with started(win, tab, "pause") as watch:
        wait_counting(qtbot, watch)
        watch.run.proc.kill()
        wait_ended(qtbot, watch)

    last_event, shown = watch.seen[-1]
    assert last_event["event"] == "process ended" and shown == "⏱ waits 60 s", \
        f"the countdown outlived the run: {watch.seen}"
    assert_usual_result(tab, "pause", "failed")
    assert_nothing_left_counting(qtbot, tab)


def test_a_wait_of_0_seconds_shows_no_countdown(win, qtbot, tmp_path):
    """Spec 3 (a Wait of 0 seconds shows no countdown, at no moment of the run: after each of its events, and while
    the block after it runs; its line says how long it waits throughout)."""
    hold = {"type": "logic.python", "config": {"inputs": [], "outputs": [], "code": "import time\ntime.sleep(1.5)"}}
    tab = win.open_flow(flow_file(tmp_path / "zero.yaml", {"zero": wait(0), "hold": hold}))
    with started(win, tab, "zero") as watch:
        while_running = []
        deadline = time.monotonic() + 60
        while not watch.ended:
            assert time.monotonic() < deadline, f"the run did not end: {[e for e, _ in watch.seen]}"
            qtbot.wait(20)
            while_running.append(line(tab, "zero"))

    kinds = [(e.get("event"), e.get("block")) for e, _ in watch.seen]
    assert ("block_started", "zero") in kinds and ("block_finished", "zero") in kinds, kinds
    assert {shown for _, shown in watch.seen} == {"⏱ waits 0 s"}, watch.seen
    assert set(while_running) == {"⏱ waits 0 s"}, sorted(set(map(str, while_running)))
    assert_usual_result(tab, "zero", "success")
    assert_nothing_left_counting(qtbot, tab)


def test_the_time_left_stays_right_after_the_editor_was_busy(win, qtbot, tmp_path):
    """Spec 2 (computed from the end time the run gives: after the editor was busy for 5 s, the next value shown is
    the real time left, not one second less than before)."""
    tab = win.open_flow(flow_file(tmp_path / "busy.yaml", {"pause": wait(60)}))
    with started(win, tab, "pause") as watch:
        before = wait_counting(qtbot, watch)

        time.sleep(5)  # the editor is busy: it handles nothing meanwhile
        poll(qtbot, lambda: line(tab, "pause") != before, 3,
             lambda: f"the time left did not change after the editor was busy: {drawn(tab, 'pause')}")

        shown = seconds_left(line(tab, "pause"))
        assert abs(shown - watch.left_now(60)) <= TOLERANCE, (before, shown, round(watch.left_now(60), 2))
        win.stop(tab)
        wait_ended(qtbot, watch)


def test_a_wait_of_value_counts_down_the_seconds_that_arrived(win, qtbot, tmp_path):
    """Spec 2 (the user's {value} case: before the run the line says "⏱ waits {value} s"; while it waits it counts
    down the seconds that arrived at its value input) and spec 1 (the Log says those seconds)."""
    make = {"type": "logic.python", "config": {"inputs": [], "code": "output = 20"}}
    tab = win.open_flow(flow_file(tmp_path / "given.yaml", {"make": make, "pause": wait("{value}")},
                                  edges=["make.output -> pause.value"]))
    QApplication.processEvents()
    assert line(tab, "pause") == "⏱ waits {value} s", drawn(tab, "pause")
    with started(win, tab, "pause") as watch:
        text = wait_counting(qtbot, watch)
        assert abs(seconds_left(text) - watch.left_now(20)) <= TOLERANCE, (text, watch.left_now(20))
        assert_logged_once(win, "pause", "20", watch.started, 20)
        win.stop(tab)
        wait_ended(qtbot, watch)

    assert watch.after_finished() == "⏱ waits {value} s", watch.seen
    assert_nothing_left_counting(qtbot, tab)


def test_the_line_reads_well_in_light_and_in_dark_as_the_schedule_blocks_does(win, qtbot, tmp_path, monkeypatch):
    """Spec 2 (it reads well in the Light and the Dark theme of Tools > Settings > Theme, in the colour of the
    Schedule block's countdown line: when it says how long it waits, and while it counts down)."""
    # Save in Settings also writes, on Windows, the Startup script of "Start the scheduler service when I log in":
    # in this test's folder, never the real one.
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    app = QApplication.instance()
    style, sheet, palette, tips = app.style().name(), app.styleSheet(), QApplication.palette(), QToolTip.palette()
    nightly = {"type": "trigger.schedule", "config": {"cron": "0 7 * * *"}, "ui": {"x": 0, "y": 300}}
    tab = win.open_flow(flow_file(tmp_path / "themed.yaml", {"pause": wait(60)}, apart={"nightly": nightly}))

    def assert_readable(theme: str):
        schedule, found = clock_line(tab, "nightly"), clock_line(tab, "pause")
        assert schedule is not None and found is not None, (drawn(tab, "nightly"), drawn(tab, "pause"))
        assert found[2] == schedule[2], f"{theme}: not in the Schedule line's colour: {found} vs {schedule}"
        behind = background(tab, "pause")
        assert contrast(QColor(found[2]), behind) >= READABLE, (theme, found, behind.name())

    try:
        for theme in ("Light", "Dark"):
            choose(win, theme)
            assert_readable(theme)
        with started(win, tab, "pause") as watch:
            wait_counting(qtbot, watch)
            assert_readable("Dark, counting")
            choose(win, "Light")
            qtbot.wait(1100)  # (a tick in the new theme)
            assert counting(line(tab, "pause")), drawn(tab, "pause")
            assert_readable("Light, counting")
            win.stop(tab)
            wait_ended(qtbot, watch)
    finally:  # the application's look is the whole test session's
        if app.style().name() != style:
            QApplication.setStyle(style)
        if app.styleSheet() != sheet:
            app.setStyleSheet(sheet)
        QApplication.setPalette(palette)
        QToolTip.setPalette(tips)
        tab.doc.dirty = False


# --- spec 4: re-runs, and past runs ----------------------------------------------------------------------------------

@pytest.mark.parametrize("item", ["Run only this block, with the same inputs",
                                  "Run from this block on, with the same inputs"])
def test_a_rerun_of_the_wait_block_counts_down_too(win, qtbot, tmp_path, item):
    """Spec 4 (the block's menu: Run only this block / from this block on: the same countdown and Log line) and
    spec 3 (Stop ends it at once)."""
    tab = win.open_flow(flow_file(tmp_path / "again.yaml", {"pause": wait(0), "then": wait(0)}))
    with started(win, tab, "pause") as first:
        wait_ended(qtbot, first)
    tab.doc.set_block_value("pause", "seconds", 30)  # (saved before it runs again)
    try:
        QApplication.processEvents()
        assert line(tab, "pause") == "⏱ waits 30 s", drawn(tab, "pause")

        with started(win, tab, "pause", lambda: menu_action(win, tab, "pause", item).trigger()) as watch:
            text = wait_counting(qtbot, watch)
            assert abs(seconds_left(text) - watch.left_now(30)) <= TOLERANCE, (text, watch.left_now(30))
            assert_logged_once(win, "pause", "30", watch.started, 30)
            win.stop(tab)
            wait_ended(qtbot, watch)
    finally:
        tab.doc.dirty = False  # (a test that fails before the re-run saved it: closing the editor does not ask)

    assert watch.after_finished() == "⏱ waits 30 s", watch.seen
    assert_usual_result(tab, "pause", "cancelled")
    assert_nothing_left_counting(qtbot, tab)


def test_a_past_run_shows_no_countdown_even_when_its_wait_had_time_left(win, qtbot, tmp_path):
    """Spec 4 (runs from the run history show no countdown: a run started elsewhere, as the scheduler does, whose
    Wait of over a day its timeout ended, shown when the editor sees it and again from Run history) and spec 1 (its
    Log line is kept in the run history and shown with it)."""
    flow = flow_file(tmp_path / "past.yaml", {"pause": wait(90065, timeout="1s")})
    tab = win.open_flow(flow)

    run_id = run_elsewhere(win, flow)
    poll(qtbot, lambda: tab.scene.nodes["pause"].status == "failed", 15,
         lambda: f"the editor did not show the run started elsewhere: {drawn(tab, 'pause')}")

    assert_usual_result(tab, "pause", "failed")
    assert line(tab, "pause") == "⏱ waits 90065 s", drawn(tab, "pause")
    assert_nothing_left_counting(qtbot, tab)
    win.show_history_run(run_id, tab)
    assert_usual_result(tab, "pause", "failed")
    assert line(tab, "pause") == "⏱ waits 90065 s", drawn(tab, "pause")
    assert_nothing_left_counting(qtbot, tab)
    lines = waiting_lines(win, "90065")
    assert len(lines) == 1 and "[pause]" in lines[0], win.log.text.toPlainText()


# --- spec 6: README -------------------------------------------------------------------------------------------------

def test_readme_says_the_editor_shows_the_time_left_on_a_wait_block_while_it_waits():
    """Spec 6: README's line about the Wait block says the editor shows the time left on the block while it waits, as
    a Schedule block shows its next run."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    found = re.search(r"^- \*\*Wait\*\*.*?(?=^- |^\s*$)", text, re.MULTILINE | re.DOTALL)
    assert found, "README has no line about the Wait block (- **Wait** …)"
    said = " ".join(found.group().split()).lower()
    assert "editor" in said and "time left" in said and "schedule" in said, \
        f"README's Wait line does not say the editor shows the time left while it waits: {found.group()}"
