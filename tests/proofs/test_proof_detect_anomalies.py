"""Proof: Detect Anomalies, in the Data group: the values of a table of numbers over time that are unusual for their
weekday or hour of day, each with a reason, per group, with a chart of each series and its usual range. How proofs
are written: README.md here."""

import math
import random
import re
import statistics as pystats
from datetime import date, datetime, timedelta

import polars as pl
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QHelpEvent, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

from proofs.test_proof_api_request import click_bottom_tab, in_window, open_flow, preview_columns, properties_row
from proofs.test_proof_api_request import pick as open_and_click
from proofs.test_proof_block_icons import list_row
from proofs.test_proof_python_reusable_block import plain, run_now, tooltip
from proofs.test_proof_retention_per_start import click_tick, drag_up
from proofs.test_proof_run_output import click, setting, type_into
from proofs.test_proof_settings_wording import menu_action
from proofs.test_proof_statistics import (email_page, find, finished, light_png, lines, log_text, pick, picture_in_sight,
                                          picture_rects, preview_rows, run_only, scroll_log, shown_in, widen)
from proofs.test_proof_tab_plus import set_theme
from taskloom.editor import theme
from tests.test_block_icons import rows
from tests.test_paths_editor_editing import clean, flow_file
from tests.test_paths_reports_email import html_of, inline_images

ASKED = ("Ok add the detect anomalies block within a new Machine Learning category to the queue”, and then, on the "
         "design proposed: “sounds good but doesn't sound like Machine Learning probably we don't create the category "
         "and just add this block into the Data category instead?” — The design agreed with you: is today's number "
         "unusual? (daily row counts, job durations, file sizes, error counts); what is usual comes from the same "
         "weekday over the last few weeks (or the same hour of day), from the middle value and the typical spread, so "
         "one past spike does not skew it; Sensitivity low, medium or high; a date column, number columns, an optional "
         "group-by and a pattern (none, weekday, hour of day); fewer than 4 earlier comparable points says “not "
         "enough history”; Only the latest on by default; every flagged point with a reason; the table with expected, "
         "low, high, anomaly and reason, the anomalies, their count, and a chart shown in the Log; warnings for too "
         "few points, gaps and repeated dates, never a silent guess; no new library.")
DETECT = "data.detect_anomalies"
BLOCK = "unusual_counts"  # the Detect Anomalies block of the daily flow
HOURLY = "unusual_durations"  # ... and of the hourly one
SERVERS = ("server-a", "server-b", "server-c")
LATEST = date(2026, 11, 2)  # a Monday: the counts' latest day
FIRST = LATEST - timedelta(weeks=10)  # Monday 24 August
C_STARTS = LATEST - timedelta(weeks=3)  # server-c's first day
# Each server's usual counts: (Mondays, Tuesday to Friday, weekends); each day's count is within 3% of it.
LEVELS = {"server-a": (30000, 20000, 6000), "server-b": (5000, 3000, 1500), "server-c": (12000, 8000, 2500)}
SEED = 57  # the up to 3% up or down of each day's count, made up the same way on every computer
SPIKE_A = 61874  # server-a's count on the latest Monday
SPIKE_B = 5160  # server-b's: 2.5 spreads above its usual Monday, so unusual at high Sensitivity only
CALM_A = 29950  # server-a's count on the latest Monday, on a calm day
PAST = {(date(2026, 10, 7), "server-a"): 41250, (date(2026, 9, 25), "server-b"): 1020}  # far off, in the past
MISSING_DAY = date(2026, 10, 22)  # server-b has no row: its job did not load
REPEATED_DAY = date(2026, 10, 14)  # server-b has two rows: its job loaded twice
SPREADS = {"low": 4, "medium": 3, "high": 2}  # Sensitivity: how many spreads from the expected value is unusual
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
ADDED = ["rows_expected", "rows_low", "rows_high", "anomaly", "reason"]
LABELS = ["Date column", "Number columns", "Group by", "Pattern", "Compare with the last", "Sensitivity",
          "Only the latest"]  # its settings in Properties, in order
TEMPLATE = "<h2>Unusual row counts</h2>{anomalies}<br>{charts}"
NINE = [301, 296, 310, 305, 299, 318, 292, 307]  # the 09:00 run's seconds, on the 8 days before the latest
SLOW = 912  # its seconds on the latest day
JOB_FIRST, JOB_LATEST = datetime(2026, 10, 25, 9), datetime(2026, 11, 2, 9)  # (under 200 runs: Data preview shows 200)
DAILY_FLOW = ["start_by_hand", "read_counts", BLOCK, "any_unusual", "report", "email_report"]
BLOCKS_WIDTH, INSPECTOR_WIDTH = 180, 420  # the Blocks list and Properties, made narrower: a wider canvas
LOG_HEIGHT = 230  # the Log under the canvas, at first
TALL_LOG = 540  # dragged taller to read the summary and the charts
CLOSE_UP = 900  # the widest close-up of the Log's lines, in pixels (they go on to the right)


# --- the data, and what the proof works out from it itself ----------------------------------------------------------

def daily_counts(latest_a=SPIKE_A) -> list[tuple[date, str, int]]:
    """The rows of daily_counts.csv, in order: (day, server, rows)."""
    rng = random.Random(SEED)
    found = []
    for i in range((LATEST - FIRST).days + 1):
        day = FIRST + timedelta(days=i)
        for server in SERVERS:
            if server == "server-c" and day < C_STARTS:
                continue
            monday, weekday, weekend = LEVELS[server]
            usual_count = monday if day.weekday() == 0 else weekday if day.weekday() < 5 else weekend
            value = PAST.get((day, server), round(usual_count * (1 + rng.uniform(-0.03, 0.03))))
            if day == LATEST:
                value = {"server-a": latest_a, "server-b": SPIKE_B, "server-c": 12350}[server]
            if (day, server) == (MISSING_DAY, "server-b"):
                continue
            found.append((day, server, value))
            if (day, server) == (REPEATED_DAY, "server-b"):
                found.append((day, server, round(value * 0.98)))
    return found


def write_counts(path, counts):
    text = ["day,server,rows", *(f"{d.isoformat()},{s},{v}" for d, s, v in counts)]
    path.write_text("\n".join(text) + "\n", encoding="utf-8", newline="")
    return path


def durations() -> list[tuple[str, int]]:
    """The rows of durations.csv: (started_at, seconds), a run every hour from 25 October 09:00 to 2 November 09:00,
    each started 3 to 57 seconds after the hour; the 09:00 run takes about 300 seconds, the others about 60."""
    found, nine = [], iter(NINE)
    hour, i = JOB_FIRST, 0
    while hour <= JOB_LATEST:
        started = hour + timedelta(seconds=3 + i * 23 % 55)
        seconds = SLOW if hour == JOB_LATEST else next(nine) if hour.hour == 9 else 55 + i * 7 % 11
        found.append((f"{started:%Y-%m-%d %H:%M:%S}", seconds))
        hour, i = hour + timedelta(hours=1), i + 1
    return found


def write_durations(path):
    text = ["started_at,seconds", *(f"{t},{v}" for t, v in durations())]
    path.write_text("\n".join(text) + "\n", encoding="utf-8", newline="")
    return path


def in_columns(text: list[str], n: int = 3) -> str:
    """Lines in n columns, read down the first one, then the next: to fit a page."""
    height = math.ceil(len(text) / n)
    parts = [text[i:i + height] for i in range(0, len(text), height)]
    widths = [max(map(len, part)) + 8 for part in parts]
    return "\n".join("".join((part[r] if r < len(part) else "").ljust(w) for part, w in zip(parts, widths)).rstrip()
                     for r in range(height))


def usual(earlier: list, k: int = 3) -> tuple[float, float, float, float]:
    """What is usual, from the comparable earlier values: (expected, low, high, spread): their median; the spread,
    1.4826 × their median absolute deviation from it (or, when that is 0, 1.2533 × their mean absolute deviation);
    k spreads either side."""
    m = pystats.median(earlier)
    mad = pystats.median(abs(v - m) for v in earlier)
    spread = 1.4826 * mad if mad else 1.2533 * pystats.fmean(abs(v - m) for v in earlier)
    return m, m - k * spread, m + k * spread, spread


def mondays_before(counts, server: str, last: int = 8) -> list[int]:
    """The server's counts on the `last` Mondays before the latest."""
    return [v for d, s, v in counts if s == server and d.weekday() == 0 and d < LATEST][-last:]


def every_unusual(counts, k: int = 3) -> list[tuple[date, str, int]]:
    """Each count that is unusual against the latest 8 counts of the same server and weekday at earlier days (at
    least 4 of them), in the file's order."""
    found = []
    for day, server, value in counts:
        earlier = [v for d, s, v in counts if s == server and d.weekday() == day.weekday() and d < day][-8:]
        if len(earlier) >= 4:
            _, low, high, _ = usual(earlier, k)
            if value < low or value > high:
                found.append((day, server, value))
    return found


def number(x: float) -> str:
    """A number as the reasons write it: , between thousands, at most 2 decimals."""
    text = f"{x:,.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def band(m: float, low: float, high: float, *_) -> str:
    return f"{number(m)} ({number(low)}–{number(high)})"


def reason_of(server: str, value: int, earlier: list, flagged: bool = True) -> str:
    said = "a usual Monday is" if flagged else "within the usual range of a Monday:"
    return f"Mon {LATEST.isoformat()}, {server}: rows {number(value)}; {said} {band(*usual(earlier))}"


def close(a, b) -> bool:
    return a is not None and abs(a - b) <= 1e-6 * max(1.0, abs(b))


# --- the flows -------------------------------------------------------------------------------------------------------

def block(type_id: str, x: int, y: int, **config) -> dict:
    return {"type": type_id, **({"config": config} if config else {}), "ui": {"x": x, "y": y}}


def daily_flow(folder):
    """start_by_hand -> read_counts (daily_counts.csv) -> unusual_counts (by weekday, per server) -> its count into
    any_unusual (an If, value > 0) -> on true, report (an HTML Template of its anomalies and charts) -> email_report."""
    return flow_file(folder / "daily-counts.yaml", {
        "start_by_hand": block("trigger.manual", 0, 20),
        "read_counts": block("data.read_file", 230, 0, path="daily_counts.csv"),
        BLOCK: block(DETECT, 460, 0, date_column="day", number_columns=["rows"], group_by=["server"]),
        "any_unusual": block("logic.if", 690, 0, condition="value > 0"),
        "report": block("reports.html_template", 690, 230,
                        names={f"{BLOCK}.anomalies": "anomalies", f"{BLOCK}.charts": "charts"}, template=TEMPLATE),
        "email_report": block("email.send", 920, 230, connection="mailer", to=["team@example.com"],
                              subject="Unusual row counts"),
    }, ["start_by_hand.start -> read_counts.after", f"read_counts.table -> {BLOCK}.table",
        f"{BLOCK}.count -> any_unusual.value", "any_unusual.true -> report.after",
        f"{BLOCK}.anomalies -> report.data", f"{BLOCK}.charts -> report.data", "report.html -> email_report.body"])


def hourly_flow(folder):
    return flow_file(folder / "durations.yaml", {
        "start_by_hand": block("trigger.manual", 0, 20),
        "read_durations": block("data.read_file", 230, 0, path="durations.csv"),
        HOURLY: block(DETECT, 460, 0, date_column="started_at", number_columns=["seconds"], pattern="hour of day"),
    }, ["start_by_hand.start -> read_durations.after", f"read_durations.table -> {HOURLY}.table"])


# --- what the editor shows --------------------------------------------------------------------------------------------

def outputs_of(tab, block_id: str) -> dict:
    """The tables a block gave in the run the canvas shows: {output: its Parquet file}."""
    return (tab.block_info.get(block_id) or {}).get("tables") or {}


def table_of(tab, block_id: str = BLOCK, output: str = "table") -> pl.DataFrame:
    return pl.read_parquet(outputs_of(tab, block_id)[output])


def latest_rows(tab) -> dict:
    """unusual_counts' table: its rows at the latest Monday, by server."""
    return {r["server"]: r for r in table_of(tab).iter_rows(named=True) if r["day"] == LATEST.isoformat()}


def entries(win, run_id: int, block_id: str = BLOCK) -> list[dict]:
    """The Log's entries of `block_id` in run #`run_id`."""
    found, current = [], False
    for e in win.log.entries:
        if e.get("header") and "run #" in e.get("message", ""):
            current = f"run #{run_id} " in e["message"]
        elif current and e.get("block") == block_id:
            found.append(e)
    return found


def report_of(win, run_id: int, block_id: str = BLOCK) -> str:
    """The block's Markdown in the Log in that run, as it reads (its \\ before _ left out)."""
    return "\n\n".join(e["message"] for e in entries(win, run_id, block_id)
                       if e.get("show") == "markdown").replace("\\", "")


def warnings_of(win, run_id: int, block_id: str = BLOCK) -> list[str]:
    return [e["message"] for e in entries(win, run_id, block_id) if e.get("level") == "WARNING"]


def charts_of(win, run_id: int, block_id: str = BLOCK) -> list[str]:
    return [e["message"] for e in entries(win, run_id, block_id) if e.get("show") == "image"]


def entry_start(win, text: str, start: int, block_id: str = BLOCK) -> int:
    """Where the Log's line of the entry of `block_id` that shows `text` (the first from `start` on) starts."""
    at = find(win, text, start)
    return [m.start() for m in re.finditer(rf"\d\d:\d\d:\d\d \[{block_id}\] ", log_text(win)[:at])][-1]


def left_part(rect: QRect, width: int = CLOSE_UP) -> QRect:
    """The left `width` pixels of `rect` (Log lines go on to the right, past what a close-up can show)."""
    return QRect(rect.left(), rect.top(), min(width, rect.width()), rect.height())


def show_lines_of(win, tab, block_id: str):
    """Click the block (the Log then shows only its lines) and the Log's tab."""
    click(tab, block_id)
    click_bottom_tab(win, win.log)


def drag_down(win, dock, by: int):
    """Drag the edge between the canvas and `dock`, the panels under it, down by `by` pixels, as you would."""
    at = QPoint(dock.geometry().center().x(), (win.centralWidget().geometry().bottom() + dock.geometry().top()) // 2)
    QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, at)
    for dy in range(10, by + 1, 10):
        QTest.mouseMove(win, at + QPoint(0, dy))
        QTest.qWait(20)  # the window moves the edge as the mouse goes
    QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, at + QPoint(0, by))


def in_sight(tab, blocks) -> bool:
    """The canvas shows these blocks whole."""
    view = tab.view.viewport().rect()
    return all(view.contains(tab.view.mapFromScene(tab.scene.nodes[b].sceneBoundingRect()).boundingRect())
               for b in blocks)


def choose(win, label: str, text: str):
    """Open the list of the setting `label` in Properties and click `text` in it, as you would."""
    open_and_click(setting(win, label)[1], text)
    assert setting(win, label)[1].currentText() == text, f"{label}: {text} was not chosen"


def red_line(win):
    """The red line at the top of Properties: what the block still needs (Properties is built again as it changes)."""
    return win.properties.error


def preview_bottom(win) -> QRect:
    """Data preview's table scrolled to its last rows: the line naming it, its header and the rows in sight, as far
    as its last column goes, in the window."""
    view = win.preview.view
    view.scrollToBottom()
    QTest.qWait(100)
    model, header = view.model(), view.horizontalHeader()
    right = view.visualRect(model.index(0, model.columnCount() - 1)).right()
    last = view.visualRect(model.index(model.rowCount() - 1, 0)).bottom()
    return (in_window(win, win.preview.label)
            | in_window(win, header.viewport(), QRect(0, 0, right + 1, header.height()))
            | in_window(win, view.viewport(), QRect(0, 0, right + 1, min(last + 1, view.viewport().height()))))


def fit_reason(win):
    """Double-click the right edge of the reason column's header, as you would: it becomes as wide as its reasons."""
    names = preview_columns(win)
    if "reason" in names:
        widen(win.preview.view, names.index("reason"))


def latest_part(win, n: int = 3, through: str = "anomaly") -> tuple[QRect, QRect]:
    """Data preview's table scrolled to its last rows, from its first column to `through`: (its header down to its
    last row, and its last n rows), in the window."""
    view = win.preview.view
    view.scrollToBottom()
    QTest.qWait(100)
    model, header = view.model(), view.horizontalHeader()
    right = view.visualRect(model.index(0, preview_columns(win).index(through))).right()
    top = view.visualRect(model.index(model.rowCount() - n, 0)).top()
    bottom = view.visualRect(model.index(model.rowCount() - 1, 0)).bottom()
    rows_ = in_window(win, view.viewport(), QRect(0, top, right + 1, bottom - top + 1))
    return in_window(win, header.viewport(), QRect(0, 0, right + 1, header.height())) | rows_, rows_.adjusted(4, 4, -4, -4)


def last_rows(win, n: int) -> list:
    """Data preview's last n rows, to outline them together."""
    count = win.preview.view.model().rowCount()
    return [(win.preview.view, r) for r in range(count - n, count)]


def test_detect_anomalies_finds_the_unusual_values_of_a_table_over_time(proof, env, smtp, win, qtbot, tmp_path,
                                                                        monkeypatch):
    p = proof("Detect Anomalies: is today's number unusual?", ASKED)
    p.idea("Detect Anomalies, a new block under Data, tells you whether the latest values of a table of numbers over "
           "time are unusual: today's row count of a load, a job's duration, a file's size, an error count. Each "
           "unusual value comes with a reason, such as “rows 61,874; a usual Monday is 29,721 (28,055.3–31,386.7)”.")
    p.idea("What is usual for a value comes from the comparable earlier values of its series: by default the same "
           "weekday over the last 8 weeks (Mondays with Mondays), or, with Pattern hour of day, the same hour of the "
           "day. Their middle value (the median) is the expected value, and their typical spread says how far from it "
           "the values usually go. A value more than 3 spreads away is unusual; Sensitivity low and high make it 4 and "
           "2 spreads. With Group by (e.g. server), each server is its own series, judged against its own history.")
    p.idea("Only the latest, ticked at first, judges the values at the table's latest date: is today's value unusual? "
           "Unticked, every value is judged, to look back. A value with fewer than 4 comparable earlier values is "
           "never flagged: its reason says there is not enough history.")
    p.idea("Its outputs: table, the input with the expected value, low, high, anomaly and reason added to each row; "
           "anomalies, its unusual rows only; count, how many, for an If such as value > 0; and charts, a chart per "
           "series with its usual range shaded and its unusual values marked, also shown in the Log after a summary "
           "and the reasons. Too little history, missing dates and repeated dates are warned about in the Log.")
    p.why_simplest("The method fits in a sentence: the median of the last 8 comparable earlier values, and their "
                   "median absolute deviation from it, as the spread (×1.4826, which makes it the standard deviation "
                   "for numbers spread the usual bell-curve way; when it is 0, their mean absolute deviation ×1.2533). "
                   "One past spike moves neither, as it would move a mean and a standard deviation. When the earlier "
                   "values were all the same, any other value is unusual, and its reason says they were all that "
                   "value.")
    p.why_simplest("Nothing new to install: NumPy, Polars and DuckDB, which Taskloom already has, work it out, and "
                   "Matplotlib, already there for the Chart block, draws the charts. Every unusual value says why in "
                   "numbers you can check (its value, the expected one and the usual range), and nothing is guessed "
                   "silently: too little history, a missing date or a date twice is said in the Log.")
    p.alternative("A Machine Learning category, with a model such as Isolation Forest",
                  "As you said, it does not sound like Machine Learning: “is today's number unusual for a Monday?” is "
                  "a question of the same weekday's history. A model would need a new library and could not say why "
                  "in plain numbers. Isolation Forest is left for later, only if it is ever needed.")
    p.alternative("The mean and standard deviation of the history",
                  "One past spike (a reload, a holiday) would raise both for weeks, and hide the next spike. The median "
                  "and the median absolute deviation are not moved by it.")
    p.alternative("A fixed range you type for each number",
                  "Check Table can already do that (Values within a range or a list). It does not know that Mondays "
                  "are bigger than Sundays, and the right range drifts as the numbers grow; history knows both.")
    p.fits("It is in the Blocks list under Data, with its own icon (Tabler's activity, a line with a spike, from the "
           "same set as the other icons), in the hover help and in Help > Documentation, as every block.")
    p.fits("It reads dates and numbers written as text, as Check Table does, so a CSV from Read File needs no extra "
           "step; a value that does not read fails the block, listing such values with how many rows have each, in "
           "Check Table's words.")
    p.fits("Its charts are drawn as Statistics' are, on white also in the Dark theme, because they go into emails; its "
           "summary and reasons are Markdown in the Log, as Statistics' report is. count is a plain number for an "
           "If, and anomalies and charts go into an HTML Template as any table and any chart do.")

    win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)
    folder = tmp_path / "flows"
    folder.mkdir()
    counts = daily_counts()
    csv = write_counts(folder / "daily_counts.csv", counts)
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    try:
        # --- 1. In the Blocks list ---------------------------------------------------------------------------------
        p.step("Look at the Blocks list, on the left, under Data.")
        data = {item.text(0): item for item in rows(win.palette_panel)["Data"]}
        listed = data.get("Detect Anomalies")
        tree = win.palette_panel.tree
        tree.scrollToItem(listed)
        QApplication.processEvents()
        group = [list_row(win, listed.parent().child(i)) for i in range(listed.parent().childCount())]
        p.shot(win, "① Detect Anomalies, under Data (first there: the list is in the order of the names), with its "
                    "icon: a line with a spike, white on the Data teal.",
               highlight=[list_row(win, listed)], badges=True, zoom=[list_row(win, listed.parent()), *group])
        p.check("The Blocks list has Detect Anomalies under Data, with an icon; no group is called Machine Learning",
                listed is not None and not listed.icon(0).isNull()
                and not any("machine" in g.lower() for g in rows(win.palette_panel)))

        p.step("Rest the mouse on Detect Anomalies in the Blocks list.")
        viewport = tree.viewport()
        at = tree.visualItemRect(listed).center()
        QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, at, viewport.mapToGlobal(at)))
        qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
        tip = tooltip()
        said = " ".join(plain(tip.text()).split())
        cls = win.registry.get(DETECT)
        p.shot(tip, "Its hover help: what it marks and the question it answers, how it works out what is usual (the "
                    "median and typical spread of the same weekday), Sensitivity, per group, its four outputs, and "
                    "what is warned about; then its input, the table.", over=win, highlight=[tip], zoom=[tip])
        tip.hide()
        p.check("The hover help has the block's name, says what it does and names its input table",
                said.startswith("Detect Anomalies") and " ".join(cls.description.split()) in said
                and " ".join(cls.inputs_help.split()) in said)

        # --- 2. A new block's Properties ---------------------------------------------------------------------------
        p.step("Choose File > New flow, double-click Detect Anomalies in the Blocks list, then click the new block.")
        menu_action(win, "&File", "New flow").trigger()
        QTest.qWait(100)
        new_tab = win.current
        tree.scrollToItem(listed)
        at = tree.visualItemRect(listed).center()
        QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, at)  # a double-click: a click, then the
        QTest.mouseDClick(viewport, Qt.LeftButton, Qt.NoModifier, at)  # second one (QTest sends only that)
        QTest.qWait(100)
        added = [b for b, spec in new_tab.doc.blocks.items() if spec.get("type") == DETECT]
        p.check("A Detect Anomalies is added to the new flow", len(added) == 1)
        new_block = added[0]
        click(new_tab, new_block)
        win.right_tabs.setCurrentWidget(win.properties)
        settings = {label: setting(win, label)[1] for label in LABELS}
        error = red_line(win)
        p.shot(win, f"① {new_block} in Properties: Date column, Number columns and Group by, empty; Pattern weekday, "
                    "Compare with the last 8, Sensitivity medium, Only the latest ticked. ② The red line at the top: "
                    "the Date column is still needed.",
               highlight=[[properties_row(win, label) for label in LABELS], error], badges=True,
               zoom=[properties_row(win, LABELS[0]), properties_row(win, LABELS[-1]), error])
        p.check("Pattern is weekday, Compare with the last 8, Sensitivity medium, Only the latest ticked",
                (settings["Pattern"].currentText(), settings["Sensitivity"].currentText(),
                 settings["Only the latest"].isChecked()) == ("weekday", "medium", True)
                and (settings["Compare with the last"].text() or settings["Compare with the last"].placeholderText())
                == "8")
        p.check(f"The red line names the Date column: “{error.text()}”",
                error.isVisible() and "Date column" in error.text())

        p.step("Type day in Date column and press Enter, then rows in Number columns and Enter.")
        type_into(setting(win, "Date column")[1], "day")
        said_then = red_line(win).text() if red_line(win).isVisible() else ""
        type_into(setting(win, "Number columns")[1], "rows")
        p.check(f"After the Date column, the red line asks for Number columns: “{said_then}”",
                "Number columns" in said_then)
        p.check("With both, the red line is gone", red_line(win).isHidden())

        p.step("Type 3 in Compare with the last and press Enter.")
        type_into(setting(win, "Compare with the last")[1], "3")
        error = red_line(win)
        p.shot(win, "① Properties refuses it: the red line says Compare with the last must be 4 or more.",
               highlight=[error, properties_row(win, "Compare with the last")], badges=[1, 1],
               zoom=[error, properties_row(win, "Compare with the last")])
        p.check(f"The red line names Compare with the last: “{error.text()}”",
                error.isVisible() and "Compare with the last" in error.text() and "4" in error.text())
        clean(win)

        # --- 3. The daily counts, and the run ----------------------------------------------------------------------
        p.step("Look at daily_counts.csv, the file the flow reads: ten weeks of daily row counts per server, made by "
               "the proof.")
        written = csv.read_text(encoding="utf-8").splitlines()
        on = ["2026-10-12", REPEATED_DAY.isoformat(), "2026-10-21", MISSING_DAY.isoformat(), "2026-10-23"]
        excerpt = written[:4] + ["…"]
        for day in on:
            excerpt += [line for line in written if line.startswith(day)] + (["…"] if day in on[:2] else [])
        excerpt += ["…", *written[-3:]]
        p.text(f"daily_counts.csv, in part, in three columns (its dates are text, as a CSV has them): server-a and "
               f"server-b from Monday {FIRST.day} {FIRST:%B}; server-c from Monday {C_STARTS.day} {C_STARTS:%B}; "
               f"server-b twice on {REPEATED_DAY.day} {REPEATED_DAY:%B} and not on {MISSING_DAY.day} "
               f"{MISSING_DAY:%B}; the latest Monday, {LATEST.day} {LATEST:%B}, last", in_columns(excerpt))
        p.note("Each count is within 3% of its server's usual count for that day of the week: Mondays the biggest (the "
               "weekend's work), weekends the smallest. Two are far off, in the past: server-a's on Wednesday 7 "
               "October (41,250) and server-b's on Friday 25 September (1,020).")

        p.step("Open the flow daily-counts.yaml (File > Open…).")
        win.resizeDocks([win.blocks_dock, win.inspector], [BLOCKS_WIDTH, INSPECTOR_WIDTH], Qt.Horizontal)
        tab = daily_tab = open_flow(win, monkeypatch, daily_flow(folder))
        assert in_sight(tab, DAILY_FLOW), "the canvas does not show the whole flow"
        p.shot(win, "The flow: ① read_counts, a Read File, reads daily_counts.csv; ② unusual_counts, Detect "
                    "Anomalies; ③ any_unusual, an If on its count: value > 0; ④ on true, report, an HTML Template, "
                    "puts the anomalies and the charts into an email, which email_report sends.",
               highlight=["read_counts", BLOCK, "any_unusual", ["report", "email_report"]], badges=True,
               zoom=DAILY_FLOW)
        p.note("The proof makes the Blocks list and Properties a little narrower, as you would by dragging their edges: "
               "the canvas is then wide enough to show the flow at a size you can read.")

        p.step("Click unusual_counts.")
        click(tab, BLOCK)
        panel = win.right_tabs
        p.shot(panel, "Its Properties: ① Date column day, Number columns rows, Group by server: one series per "
                      "server. ② The rest as they come: Pattern weekday, Compare with the last 8, Sensitivity medium, "
                      "Only the latest ticked.",
               highlight=[[properties_row(win, label, panel) for label in LABELS[:3]],
                          [properties_row(win, label, panel) for label in LABELS[3:]]], badges=True)

        p.step("Press ▶ Run now.")
        run = run_now(win, qtbot, tab)
        finished(win, qtbot)
        first = run.run_id
        p.shot(win, "Every block ran: ① unusual_counts is green; ② any_unusual got its count, 1, and went down its "
                    "true path (its foot says true: 1), so ③ report and email_report ran.",
               highlight=[BLOCK, "any_unusual", ["report", "email_report"]], badges=True, zoom=DAILY_FLOW)
        p.check("The run succeeded: every block of the flow is green",
                run.status == "success" and all(shown_in(tab, b) == ("success", first) for b in tab.scene.nodes))
        p.check("count is 1: any_unusual got 1 and sent it down its true path",
                tab.scene.values.get(BLOCK, {}).get("count") == "1"
                and tab.scene.values.get("any_unusual", {}).get("true") == "1")

        p.step("Drag the edge above the Log up, to make the Log taller, and click unusual_counts: the Log shows its "
               "lines only.")
        drag_up(win, win.output, TALL_LOG - win.output.height())
        qtbot.waitUntil(lambda: win.output.height() >= TALL_LOG - 30, timeout=5_000)
        show_lines_of(win, tab, BLOCK)
        start = find(win, f"run #{first} · ")
        warned = warnings_of(win, first)
        at = entry_start(win, "WARNING", start)
        scroll_log(win, at, 30)
        warning_lines = left_part(lines(win, at, find(win, "not enough history, so not flagged", start)))
        p.shot(win, "① First, the warnings: server-b has a date twice (14 October) and a missing date (22 October); "
                    "server-c has not enough history for its latest Monday. The close-up shows the start of each line; "
                    "they are written out above.",
               highlight=[warning_lines], badges=True, zoom=[warning_lines])
        p.text("unusual_counts' warnings in the Log", "\n".join(warned))
        p.check("A warning says server-b has 2026-10-14 twice, that each row is judged on its own, and to add them up "
                "first if one value per date is meant",
                any(w.startswith("server-b: 1 date has more than one row: 2026-10-14; each row is judged on its own. "
                                 "If one value per date is meant, add them up first") for w in warned))
        p.check("A warning says server-b misses 2026-10-22, at its usual step of 1 day",
                "server-b: 1 date is missing between its first and last date at its usual step, 1 day: 2026-10-22"
                in warned)
        p.check("A warning says server-c's latest Monday has fewer than 4 earlier Mondays: not enough history",
                "rows (server-c): 1 point with fewer than 4 comparable earlier points: not enough history, so not "
                "flagged" in warned)
        p.check("No other warning: server-a has every day once, and server-c every day from its first",
                len(warned) == 3)
        p.note("Dates are compared in whole days for daily values (in whole hours for hourly ones): two rows on the "
               "same day are a repeated date, whatever their times. So a daily job that runs around midnight, "
               "sometimes just before and sometimes just after, can show both a repeated date and a missing day. Gaps "
               "are looked for at a series' usual step; for dates a month or more apart, or with no regular step, "
               "they are not checked, and the Log says so instead.")

        report = report_of(win, first)
        at = entry_start(win, " judged, ", start)
        scroll_log(win, at, 30)
        reasons_at = find(win, "Unusual points", at)
        summary = lines(win, at, reasons_at - 1)
        reasons = lines(win, reasons_at, find(win, "\n", find(win, "server-a: rows", reasons_at)) - 1)
        a_earlier, a_all = mondays_before(counts, "server-a"), mondays_before(counts, "server-a", 10)
        p.shot(win, "① The summary: 3 rows judged (the latest Monday of each server), 1 unusual, each against the "
                    "latest 8 earlier points on the same weekday, unusual more than 3 spreads from their median; a "
                    "row per series. ② The reasons of the unusual points, as a table: server-a's Monday, 61,874 rows, "
                    f"where a usual Monday is {band(*usual(a_earlier))}.",
               highlight=[summary, reasons], badges=True, zoom=[summary, reasons])
        p.check("The summary says 3 rows judged, 1 unusual, against the latest 8 earlier points on the same weekday, "
                "more than 3 spreads from their median (Sensitivity medium)",
                "**3 rows judged, 1 unusual**: each value against the latest 8 earlier points of its series on the "
                "same weekday, unusual more than 3 spreads from their median (Sensitivity medium)." in report)
        p.check("A row per series: rows (server-a) 1 judged, 1 unusual; rows (server-b) 1, 0; rows (server-c) 1, 0",
                all(f"| rows ({s}) | 1 | {int(s == 'server-a')} |" in report for s in SERVERS))
        wanted = reason_of("server-a", SPIKE_A, a_earlier)
        p.check(f"The one reason: “{wanted}”: the median and 3 spreads of server-a's 8 latest earlier Mondays "
                f"({', '.join(f'{v:,}' for v in a_earlier)}), as the proof works them out itself (all 10 earlier "
                f"Mondays would give {band(*usual(a_all))})",
                f"| {wanted} |" in report and band(*usual(a_all)) != band(*usual(a_earlier)))
        p.note(f"How the proof works it out: the median of the 8 Mondays is {number(usual(a_earlier)[0])}; their "
               f"median absolute deviation from it is "
               f"{number(pystats.median(abs(v - usual(a_earlier)[0]) for v in a_earlier))}, ×1.4826 makes the spread "
               f"{number(usual(a_earlier)[3])}, and 3 spreads either side give the range.")

        charts = charts_of(win, first)
        picture = picture_in_sight(win, 0, start)
        p.shot(win, "① Then a chart per series, server-a's first: its counts as a line over the days (Mondays the high "
                    "points, weekends the low ones), the usual range a pale band around the line, and the latest "
                    "Monday marked in red, far above it.",
               highlight=[picture], badges=True, zoom=[picture])
        p.check("The Log shows 3 charts after the summary, one per server, each a PNG picture on a light background",
                len(charts) == 3 and len([q for q, _ in picture_rects(win.log.text, win) if q >= start]) == 3
                and all(light_png(c) for c in charts))

        # --- 4. Data preview, and the email ------------------------------------------------------------------------
        p.step("Click Data preview, under the canvas, scroll its table down to its last rows, and double-click the "
               "right edge of the reason column's header to widen it.")
        click_bottom_tab(win, win.preview)
        fit_reason(win)
        names = preview_columns(win)
        part = preview_bottom(win)
        table = table_of(tab)
        p.shot(win, "① table: the file's columns, day, server and rows, then rows_expected, rows_low, rows_high, "
                    "anomaly and reason. ② The last three rows are the latest Monday: server-a's anomaly is True, "
                    "server-b's and server-c's False, each with its reason. The other rows have expected, low and "
                    "high, for the chart, but no anomaly.",
               highlight=[part, last_rows(win, 3)], badges=True, zoom=[part])
        p.check("Data preview shows unusual_counts.table with the columns day, server, rows, rows_expected, rows_low, "
                "rows_high, anomaly and reason",
                win.preview.label.text().startswith(f"{BLOCK}.table") and names == ["day", "server", "rows", *ADDED])
        p.check(f"It has every row of the file, {len(counts)}, in the file's order",
                table.height == len(counts)
                and table.select("day", "server", "rows").rows() == [(d.isoformat(), s, v) for d, s, v in counts])
        latest = latest_rows(tab)
        b_earlier = mondays_before(counts, "server-b")
        p.check(f"server-a's latest row: anomaly true; rows_expected, rows_low and rows_high are the proof's own "
                f"{', '.join(number(v) for v in usual(a_earlier)[:3])}",
                latest["server-a"]["anomaly"] is True
                and all(close(latest["server-a"][c], v) for c, v in zip(ADDED[:3], usual(a_earlier))))
        p.check(f"server-b's: anomaly false, its reason “{reason_of('server-b', SPIKE_B, b_earlier, flagged=False)}”",
                latest["server-b"]["anomaly"] is False
                and latest["server-b"]["reason"] == reason_of("server-b", SPIKE_B, b_earlier, flagged=False))
        p.check("server-c's: anomaly false, its reason “Mon 2026-11-02, server-c: rows: not enough history: 3 earlier "
                "Mondays, 4 needed”",
                latest["server-c"]["anomaly"] is False and latest["server-c"]["reason"]
                == "Mon 2026-11-02, server-c: rows: not enough history: 3 earlier Mondays, 4 needed")
        p.check("Every other row has no anomaly and no reason",
                all(r["anomaly"] is None and r["reason"] is None for r in table.iter_rows(named=True)
                    if r["day"] != LATEST.isoformat()))

        p.step("Choose anomalies in the drop-down beside the table's name.")
        pick(win.preview.ports, "anomalies")
        fit_reason(win)
        part = preview_bottom(win)
        p.shot(win, "① anomalies: server-a's latest Monday, the only unusual row, with its reason.",
               highlight=[part], badges=True, zoom=[part])
        p.check("anomalies holds server-a's latest row only",
                table_of(tab, output="anomalies").select("day", "server", "rows").rows()
                == [(LATEST.isoformat(), "server-a", SPIKE_A)] and len(preview_rows(win)) == 1)

        p.step("Look at the email email_report sent: report put unusual_counts' anomalies and charts into it, with the "
               f"template “{TEMPLATE}”.")
        p.note("The proof's mail server runs on this computer (127.0.0.1): nothing leaves it. The email is shown in Qt's "
               "HTML viewer, its pictures taken from the email itself, as an email program shows them.")
        [msg] = smtp.messages
        html, attached = html_of(msg), inline_images(msg)
        page = email_page(html, attached, width=1180)
        in_email = picture_rects(page, page)
        whole = page.size()
        page.resize(page.width(), in_email[0][1].top() - 8)  # its top: down to the first picture
        p.shot(page, "The top of the email: its heading, then anomalies as a table: server-a's latest Monday with its "
                     "expected value, low, high and reason.")
        page.resize(whole)
        QTest.qWait(100)
        p.shot(page, "All of the email: the table, then a chart per server; ① server-c's, close up: three weeks of "
                     "counts and no usual range yet, as no day of it has 4 earlier days of the same weekday.",
               highlight=[in_email[2][1]], badges=True, zoom=[in_email[2][1]])
        page.close()
        p.check("The email has anomalies as a table: its header and server-a's row only, with its 61,874 rows",
                all(re.search(rf"<th[^>]*>{h}</th>", html) for h in ("day", "server", "rows", "anomaly", "reason"))
                and ">server-a<" in html and "61,874" in html and ">server-b<" not in html)
        p.check("The email shows 3 pictures, one per chart, each sent with it",
                len(attached) == 3 and html.count('<img src="cid:') == 3 and len(in_email) == 3)
        p.check("server-c's chart has no usual range: its rows_expected is empty on every row",
                table.filter(pl.col("server") == "server-c")["rows_expected"].null_count() == 3 * 7 + 1)

        # --- 5. Only the latest unticked -----------------------------------------------------------------------------
        click_bottom_tab(win, win.log)
        p.step("Click unusual_counts, untick Only the latest, then right-click unusual_counts and choose “Run only "
               "this block, with the same inputs”: it runs again on the table it got in the first run.")
        click(tab, BLOCK)
        click_tick(setting(win, "Only the latest")[1])
        p.check("Only the latest is unticked", setting(win, "Only the latest")[1].isChecked() is False)
        run = run_only(p, win, qtbot, tab, BLOCK)
        every = run.run_id
        report = report_of(win, every)
        past = every_unusual(counts)
        start = find(win, f"run #{every} · ")
        at = entry_start(win, " judged, ", start)
        scroll_log(win, at, 30)
        shown_report = lines(win, at, find(win, "\n", find(win, f"{LATEST.isoformat()}, server-a", at)) - 1)
        shown_report = left_part(shown_report, 1000)
        p.shot(win, "① Every row is judged now (164; those with fewer than 4 earlier days of the same weekday as not "
                    "enough history), and the past ones are flagged too: server-b's Friday 25 September and "
                    "server-a's Wednesday 7 October, with the latest Monday. The close-up shows the start of the "
                    "summary's first line.",
               highlight=[shown_report], badges=True, zoom=[shown_report])
        p.check(f"The run succeeded, and anomalies holds {len(past)} rows: those the proof finds unusual itself, "
                "judging every count against the latest 8 earlier counts of the same server and weekday: "
                + "; ".join(f"{s} {d.isoformat()} ({v:,})" for d, s, v in past),
                run.status == "success" and table_of(tab, output="anomalies").select("day", "server", "rows").rows()
                == [(d.isoformat(), s, v) for d, s, v in past])
        p.check("They are the two far off in the past and the latest Monday's",
                sorted((d, s) for d, s, _ in past) == sorted([*PAST, (LATEST, "server-a")]))
        p.check("The Log gives a reason for each, in the table of unusual points",
                all(f"| {DAYS[d.weekday()][:3]} {d.isoformat()}, {s}: rows {number(v)}; a usual {DAYS[d.weekday()]} is"
                    in report for d, s, v in past))
        p.check("Per series: rows (server-a) 71 judged, 2 unusual; rows (server-b) 71, 1; rows (server-c) 22, 0",
                all(f"| rows ({s}) | {n} | {k} |" in report
                    for s, n, k in (("server-a", 71, 2), ("server-b", 71, 1), ("server-c", 22, 0))))

        picture = picture_in_sight(win, 1, start)
        p.shot(win, "① server-b's chart: the dip of Friday 25 September is marked in red. Its latest Monday, 5,160 "
                    "rows, is higher than usual but within its range, so it is not.",
               highlight=[picture], badges=True, zoom=[picture])
        p.check("server-b's latest Monday is not flagged: anomaly false",
                latest_rows(tab)["server-b"]["anomaly"] is False)

        # --- 6. Sensitivity high and low -----------------------------------------------------------------------------
        p.step("Click Only the latest's tick again, choose high in Sensitivity, and run unusual_counts only again.")
        click_tick(setting(win, "Only the latest")[1])
        choose(win, "Sensitivity", "high")
        run = run_only(p, win, qtbot, tab, BLOCK)
        high = latest_rows(tab)
        click_bottom_tab(win, win.preview)
        fit_reason(win)
        part, latest_cells = latest_part(win)
        bands = {level: {s: usual(mondays_before(counts, s), SPREADS[level]) for s in ("server-a", "server-b")}
                 for level in ("high", "low")}
        p.shot(win, "① High: the usual ranges are narrower, 2 spreads either side: server-b's latest Monday, "
                    f"{SPIKE_B:,} rows, is now above its range, {band(*bands['high']['server-b'])}, and flagged too "
                    "(anomaly True).",
               highlight=[latest_cells], badges=True, zoom=[part])
        p.check("High: server-a's and server-b's low and high are 2 spreads from the median of their 8 earlier "
                "Mondays: " + "; ".join(f"{s} {number(b[1])}–{number(b[2])}" for s, b in bands["high"].items()),
                all(close(high[s]["rows_low"], b[1]) and close(high[s]["rows_high"], b[2])
                    for s, b in bands["high"].items()))
        p.check("High flags server-a and server-b: count 2", run.status == "success"
                and (high["server-a"]["anomaly"], high["server-b"]["anomaly"]) == (True, True)
                and table_of(tab, output="anomalies").height == 2
                and tab.scene.values.get(BLOCK, {}).get("count") == "2")

        p.step("Choose low in Sensitivity, and run unusual_counts only again.")
        click_bottom_tab(win, win.log)
        click(tab, BLOCK)
        choose(win, "Sensitivity", "low")
        run = run_only(p, win, qtbot, tab, BLOCK)
        low = latest_rows(tab)
        click_bottom_tab(win, win.preview)
        fit_reason(win)
        part, latest_cells = latest_part(win)
        p.shot(win, "① Low: the ranges are wider, 4 spreads either side: server-b's is now "
                    f"{band(*bands['low']['server-b'])}, and only server-a's 61,874 is unusual.",
               highlight=[latest_cells], badges=True, zoom=[part])
        p.check("Low: the ranges are 4 spreads from the median: " + "; ".join(
                    f"{s} {number(b[1])}–{number(b[2])}" for s, b in bands["low"].items()),
                all(close(low[s]["rows_low"], b[1]) and close(low[s]["rows_high"], b[2])
                    for s, b in bands["low"].items()))
        p.check("Each range is narrower at high than at low, around the same expected value",
                all(high[s]["rows_high"] - high[s]["rows_low"] < low[s]["rows_high"] - low[s]["rows_low"]
                    and high[s]["rows_expected"] == low[s]["rows_expected"] for s in ("server-a", "server-b")))
        p.check("Low flags server-a only: count 1", run.status == "success"
                and (low["server-a"]["anomaly"], low["server-b"]["anomaly"]) == (True, False)
                and table_of(tab, output="anomalies").height == 1
                and tab.scene.values.get(BLOCK, {}).get("count") == "1")
        click_bottom_tab(win, win.log)
        click(tab, BLOCK)
        choose(win, "Sensitivity", "medium")
        p.note("After this run, the proof sets Sensitivity back to medium, as it comes.")

        # --- 7. Hourly job durations -------------------------------------------------------------------------------------
        write_durations(folder / "durations.csv")
        p.step("Open the flow durations.yaml, click unusual_durations and press ▶ Run now.")
        tab = open_flow(win, monkeypatch, hourly_flow(folder))
        click(tab, HOURLY)
        run = run_now(win, qtbot, tab)
        finished(win, qtbot)
        hourly = run.run_id
        p.shot(win, "① read_durations reads durations.csv, how long an hourly job took, in seconds; ② unusual_durations "
                    "has Date column started_at, Number columns seconds and Pattern hour of day: each run is compared "
                    "with the runs at the same hour on earlier days.",
               highlight=[["read_durations", HOURLY], [properties_row(win, label) for label in LABELS[:4]]],
               badges=True, zoom=[properties_row(win, label) for label in LABELS[:4]])

        p.step("Click read_durations, then Data preview, scroll its table down to its last rows and widen its "
               "started_at column (double-click the right edge of its header).")
        click(tab, "read_durations")
        click_bottom_tab(win, win.preview)
        widen(win.preview.view, 0)
        part = preview_bottom(win)
        shown = preview_rows(win)
        p.shot(win, "① The latest runs, as read from the file: each started a few seconds after the hour, at its own "
                    f"second (the dates are text); the 09:00 run usually takes about 300 seconds, and the latest one "
                    f"took {SLOW}.",
               highlight=[part], badges=True, zoom=[part])
        p.check(f"durations.csv has a run every hour from 25 October 09:00 to 2 November 09:00, {len(durations())} "
                "runs, each started 3 to 57 seconds after the hour",
                len(shown) == len(durations()) == 193 and [r["started_at"] for r in shown] == [t for t, _ in durations()]
                and len({t[-2:] for t, _ in durations()}) > 20)

        p.step("Click unusual_durations, then the Log.")
        show_lines_of(win, tab, HOURLY)
        start = find(win, f"run #{hourly} · ")
        at = entry_start(win, " judged, ", start, HOURLY)
        scroll_log(win, at - 1, 60)
        shown_report = lines(win, at, find(win, "\n", find(win, "seconds 912", at)) - 1)
        nine = usual(NINE)
        reason = table_of(tab, HOURLY).row(-1, named=True)["reason"]
        p.shot(win, f"① The slow 09:00 run is flagged: {SLOW} seconds, where a usual run at 09:00 takes "
                    f"{band(*nine)}. No warning above it: the runs' seconds after the hour are taken neither for gaps "
                    "nor for repeated hours.",
               highlight=[shown_report], badges=True, zoom=[left_part(shown_report, 1100)])
        p.check(f"The run succeeded; the latest run's reason is “{reason}”: against the 8 earlier runs at "
                f"09:00 ({', '.join(map(str, NINE))}), whose median and range the proof works out itself",
                run.status == "success"
                and reason == f"Mon {durations()[-1][0]}: seconds {SLOW}; a usual point at 09:00 is {band(*nine)}")
        p.check("Its anomaly is true and count is 1", table_of(tab, HOURLY).row(-1, named=True)["anomaly"] is True
                and tab.scene.values.get(HOURLY, {}).get("count") == "1")
        p.check("No warning: no gap and no repeated hour, though each run starts at its own second",
                warnings_of(win, hourly, HOURLY) == [])
        p.note("The dates of a series are taken in whole hours when they are mostly about an hour apart (in whole days "
               "when a day or more apart), so 09:00:08 is the 09:00 run, and the latest date the hour 09:00.")

        # --- 8. A calm day -----------------------------------------------------------------------------------------------
        p.step("Go back to daily-counts.yaml and drag the edge above the Log back down, so the whole flow is in sight.")
        win.tabs.setCurrentIndex(win.tabs.indexOf(daily_tab))
        QTest.qWait(100)
        tab = daily_tab
        drag_down(win, win.output, win.output.height() - LOG_HEIGHT)
        qtbot.waitUntil(lambda: in_sight(tab, DAILY_FLOW), timeout=5_000)
        p.step(f"In daily_counts.csv, make server-a's latest Monday {CALM_A:,} rows, a usual Monday, and press ▶ Run "
               "now.")
        calm = daily_counts(latest_a=CALM_A)
        write_counts(csv, calm)
        run = run_now(win, qtbot, tab)
        finished(win, qtbot)
        quiet = run.run_id
        p.shot(win, "① unusual_counts found nothing unusual, so its count is 0: ② any_unusual went down its false path "
                    "(its foot says false: 0), and ③ report and email_report were skipped: no email went.",
               highlight=[BLOCK, "any_unusual", ["report", "email_report"]], badges=True, zoom=DAILY_FLOW)
        p.check("The run succeeded; unusual_counts' anomalies is empty and its count 0, as the proof finds no unusual "
                "count at the latest Monday itself",
                run.status == "success" and table_of(tab, output="anomalies").height == 0
                and tab.scene.values.get(BLOCK, {}).get("count") == "0"
                and all(d < LATEST for d, _, _ in every_unusual(calm)))
        p.check("any_unusual sent 0 down its false path; report and email_report were skipped in this run, and the "
                "mail server got no second email",
                tab.scene.values.get("any_unusual", {}).get("false") == "0"
                and "true" not in tab.scene.values.get("any_unusual", {})
                and shown_in(tab, "report") == ("skipped", quiet) == shown_in(tab, "email_report")
                and len(smtp.messages) == 1)

        # --- 9. Dark ----------------------------------------------------------------------------------------------------
        p.step("Choose Tools > Settings…, set Theme to Dark and press Save; then drag the edge above the Log up again "
               "and run unusual_counts only again.")
        set_theme(p, win, "Dark", shoot=False)
        drag_up(win, win.output, TALL_LOG - win.output.height())
        qtbot.waitUntil(lambda: win.output.height() >= TALL_LOG - 30, timeout=5_000)
        run = run_only(p, win, qtbot, tab, BLOCK)
        dark = run.run_id
        show_lines_of(win, tab, BLOCK)
        start = find(win, f"run #{dark} · ")
        picture = picture_in_sight(win, 0, start)
        p.shot(win, "In Dark: ① the Log is dark, and server-a's chart is on white, as in an email.",
               highlight=[picture], badges=True, zoom=[picture])
        charts = charts_of(win, dark)
        p.check("The editor and the Log are dark",
                QApplication.palette().window().color().lightness() < 128
                and win.log.text.palette().color(QPalette.Base).lightness() < 128)
        p.check(f"Each of the {len(charts)} charts drawn in Dark is on a light background (its corners white)",
                run.status == "success" and len(charts) == 3 and all(light_png(c) for c in charts))

        p.step("Set Theme back to System (Tools > Settings…) and press Save.")
        set_theme(p, win, "System", shoot=False)
        p.check("The Theme is System again, and the editor light",
                theme.chosen(win.settings) == "System" and QApplication.palette().window().color().lightness() >= 128)
    finally:  # no run left going; the Theme and the application's look as the proof found them
        for t in win.tabs_list():
            for r in t.runs:
                if r.running:
                    r.proc.kill()
                    r.proc.waitForFinished(5000)
        win.settings.setValue("theme", "System")
        theme.apply("System")
        if app.style().name() != as_found[0]:
            QApplication.setStyle(as_found[0])
        QApplication.setPalette(as_found[1])
        QToolTip.setPalette(as_found[2])
        QApplication.processEvents()
        clean(win)
