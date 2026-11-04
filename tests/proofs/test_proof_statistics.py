"""Proof: Statistics, the most useful statistics of a table as numbers and charts (overview, numbers, text, dates,
correlations), per group, with statistics of your own in SQL and an extra input from a Python Code block. How proofs
are written: README.md here."""

import math
import re
import statistics as pystats

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QHelpEvent, QImage, QPalette, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QTableWidget, QTextBrowser, QToolTip

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_api_request import click_bottom_tab, in_window, open_flow, preview_columns, properties_row
from proofs.test_proof_api_request import scroll_to as scroll_properties
from proofs.test_proof_block_icons import list_row
from proofs.test_proof_inspect_log import area, rect_at
from proofs.test_proof_python_reusable_block import plain, run_now, tooltip
from proofs.test_proof_retention_per_start import click_tick, drag_up
from proofs.test_proof_run_output import click, setting, type_into
from proofs.test_proof_tab_plus import set_theme
from proofs.test_proof_theme import theme_list
from taskloom.editor import theme
from tests.test_block_icons import rows
from tests.test_inspect_log import images, size_of
from tests.test_paths_editor_editing import clean, flow_file
from tests.test_paths_reports_email import html_of, inline_images

ASKED = ("Lets also add a statistics block as well that shows (ideally using compelling graphs but also raw numbers) "
         "the most useful statistics of a table, probably the user can check which statistics to add by check "
         "marking those. Not sure if even allow the user to write a small python section to compute an obscure "
         "statistic or maybe leverage the existing python block somehow. — Agreed with you: groups of statistics to "
         "tick; the charts chosen for you, on a light background; stats (a row per column) and the charts as "
         "outputs, the report in the Log and the table in Data preview; exact by default, anything estimated marked "
         "≈; statistics of your own as a name and SQL, plus an optional extra input from a Python Code block; per "
         "group too.")
STATS = "data.statistics"
# The orders the proof writes to orders.csv: order_id, region, amount, quantity, day; None is an empty cell. Order
# 1012 is there twice (a fully repeated row); there are no orders on the weekend of 10 and 11 January.
ORDERS = [
    (1001, "north", 120.50, 3, "2026-01-05"), (1002, "south", 80.00, 2, "2026-01-05"),
    (1003, "north", 45.25, 1, "2026-01-05"), (1004, "east", 310.00, 4, "2026-01-06"),
    (1005, "west", 95.75, 5, "2026-01-06"), (1006, "north", 150.00, 4, "2026-01-07"),
    (1007, None, 60.00, 1, "2026-01-07"), (1008, "south", None, 3, "2026-01-07"),
    (1009, "north", 210.40, 2, "2026-01-08"), (1010, "east", 75.00, 2, "2026-01-08"),
    (1011, "south", 130.00, 6, "2026-01-09"), (1012, "north", 88.80, 2, "2026-01-09"),
    (1012, "north", 88.80, 2, "2026-01-09"), (1013, "west", 260.00, 6, "2026-01-12"),
    (1014, "north", 42.00, None, "2026-01-12"), (1015, "east", 175.50, 4, "2026-01-12"),
    (1016, "south", 99.90, 3, "2026-01-13"), (1017, "north", None, 1, "2026-01-13"),
    (1018, "west", 132.00, 3, None), (1019, None, 410.00, 7, "2026-01-14"),
    (1020, "north", 66.60, 2, "2026-01-14"), (1021, "east", 118.00, 1, "2026-01-15"),
    (1022, "south", 205.00, 5, "2026-01-15"), (1023, "north", 39.90, 1, "2026-01-16"),
    (1024, "west", 158.00, 4, "2026-01-16"),
]
NAMES = ("order_id", "region", "amount", "quantity", "day")
DESCRIBED = ("region", "amount", "quantity", "day")  # Columns, at first: order_id left out
PER_REGION = ("amount", "quantity", "day")  # Columns with Group by region
REGIONS = ("east", "north", "south", "west", None)  # the groups, in the order the block gives them (missing last)
AS_DATES = "select * replace (cast(day as date) as day) from input"
TOP_SHARE = ('amounts = orders.scan().select("amount").drop_nulls().collect()["amount"].sort(descending=True)\n'
             "top = amounts.head(max(1, len(amounts) // 5))\n"
             'output = {"orders with an amount": len(amounts),\n'
             '          "share of the total from the biggest 20%": f"{top.sum() / amounts.sum():.1%}"}')
TEMPLATE = "<h2>Order statistics</h2>{stats}<br>{charts}"
P95 = ("p95", "quantile_cont(amount, 0.95)")
NORTH_SHARE = ("north_share", "avg(case when region = 'north' then 1 else 0 end)")
BROKEN = ("south_share", "avg(case when region = 'south' then 1 else 0)")  # no end: not valid SQL
GROUPS = ("Overview", "Numbers", "Text", "Dates", "Correlations")
NUMBER_STATS = ("count", "missing", "distinct", "mean", "std", "min", "25%", "median", "75%", "max", "sum", "zeros",
                "negatives")  # the Numbers table's columns, after the column's name
LOG_HEIGHT = 230  # the Log under the canvas, at first
TALL_LOG = 540  # dragged taller to read the report: room for a chart (360 pixels high) and the lines around it
ENTRY = re.compile(r"\d\d:\d\d:\d\d \[order_stats\] ")  # a line of order_stats in the Log


# --- the orders, and what the proof works out from them itself ------------------------------------------------------

def write_orders(path):
    """orders.csv: a header line, then a line per order, an empty cell for a missing value."""
    lines = [",".join(NAMES)] + [",".join("" if v is None else f"{v:.2f}" if isinstance(v, float) else str(v)
                                          for v in row) for row in ORDERS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return path


def side_by_side(text: str) -> str:
    """A file's lines in two columns, the first half on the left: to fit a page."""
    lines = text.splitlines()
    half = (len(lines) + 1) // 2
    width = max(map(len, lines[:half])) + 6
    return "\n".join(left.ljust(width) + right for left, right in zip(lines[:half], lines[half:] + [""]))


def column(name: str, region="any") -> list:
    """The values of a column, of every order or of a region's (None: the orders without one)."""
    return [row[NAMES.index(name)] for row in ORDERS if region == "any" or row[1] == region]


def present(values) -> list:
    return [v for v in values if v is not None]


def quantile_cont(values, q: float) -> float:
    """The q quantile of `values`, between the two values around it, as DuckDB's quantile_cont works it out."""
    ordered = sorted(values)
    at = (len(ordered) - 1) * q
    low = math.floor(at)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (at - low)


def number_stats(name: str, region="any") -> list[str]:
    """A number column's statistics as the report's Numbers table writes them, worked out here from the orders
    (NUMBER_STATS, in order; numbers to 2 decimals, min and max as they are)."""
    values = column(name, region)
    there = present(values)
    return [f"{len(there):,}", f"{len(values) - len(there):,}", f"{len(set(there)):,}", f"{pystats.mean(there):,.2f}",
            f"{pystats.stdev(there):,.2f}", str(min(there)),
            *(f"{quantile_cont(there, q):,.2f}" for q in (0.25, 0.5, 0.75)), str(max(there)),
            f"{float(sum(there)):,.2f}", str(sum(v == 0 for v in there)), str(sum(v < 0 for v in there))]


def table_row(*cells) -> str:
    """A row of a Markdown table, as the report writes it."""
    return "| " + " | ".join(map(str, cells)) + " |"


def in_words(names, values) -> str:
    """Statistics as a line of words: count 23, missing 2, …"""
    return ", ".join(f"{n} {v}" for n, v in zip(names, values))


def most_common(name: str) -> str:
    counts = {v: column(name).count(v) for v in present(column(name))}
    return ", ".join(f"{v} ({k})" for v, k in sorted(counts.items(), key=lambda c: (-c[1], c[0])))


def correlation() -> float:
    pairs = [(a, q) for a, q in zip(column("amount"), column("quantity")) if a is not None and q is not None]
    return pystats.correlation([a for a, _ in pairs], [q for _, q in pairs])


def top_share() -> str:
    """What the Python Code block top_share works out: the share of the total amount from the biggest fifth of the
    orders that have an amount."""
    amounts = sorted(present(column("amount")), reverse=True)
    return f"{sum(amounts[:max(1, len(amounts) // 5)]) / sum(amounts):.1%}"


def missing_cells() -> int:
    return sum(v is None for row in ORDERS for v in row)


# --- the flow --------------------------------------------------------------------------------------------------

def block(type_id: str, x: int, y: int, **config) -> dict:
    return {"type": type_id, **({"config": config} if config else {}), "ui": {"x": x, "y": y}}


def write_flow(path, csv):
    """The flow, its blocks 260 apart (about 200 wide, so a gap between them), on two rows: top_share under
    as_dates, email_report under report_page."""
    return flow_file(path, {
        "start_by_hand": block("trigger.manual", 0, 20),
        "read_orders": block("data.read_file", 260, 0, path=csv.name),
        "as_dates": block("data.duckdb_sql", 520, 0, inputs=["input"], sql=AS_DATES),
        "top_share": block("logic.python", 520, 200, inputs=["orders"], code=TOP_SHARE),
        "order_stats": block(STATS, 780, 80),
        "report_page": block("reports.html_template", 1040, 0,
                             names={"order_stats.stats": "stats", "order_stats.charts": "charts"}, template=TEMPLATE),
        "email_report": block("email.send", 1040, 220, connection="mailer", to=["team@example.com"],
                              subject="Order statistics"),
    },["start_by_hand.start -> read_orders.after", "read_orders.table -> as_dates.input",
        "read_orders.table -> top_share.orders", "as_dates.result -> order_stats.table",
        "top_share.output -> order_stats.extra", "order_stats.stats -> report_page.data",
        "order_stats.charts -> report_page.data", "report_page.html -> email_report.body"])


# --- what the editor shows ---------------------------------------------------------------------------------------

def ticked(win) -> dict[str, bool]:
    """Each tick Properties shows, by the name of its setting: whether it is ticked."""
    return {label: setting(win, label)[1].isChecked() for label in (*GROUPS, "Exact")}


def custom_table(win) -> QTableWidget:
    return setting(win, "Custom statistics")[1].findChild(QTableWidget)


def custom_rows(win) -> list[tuple[bool, str, str]]:
    table = custom_table(win)
    return [(table.item(r, 0).checkState() == Qt.Checked, table.item(r, 1).text(), table.item(r, 2).text())
            for r in range(table.rowCount())]


def custom_box(win, base=None) -> QRect:
    """Custom statistics in Properties: its name, its table and the line to add a row, in the window (or `base`)."""
    base = base or win
    table = custom_table(win)
    add = setting(win, "Custom statistics")[1].findChild(QLineEdit)
    return properties_row(win, "Custom statistics", base) | in_window(base, table) | in_window(base, add)


def add_custom(win, name: str, expression: str):
    """Type name=expression in the line under Custom statistics and press Enter, as you would."""
    add = setting(win, "Custom statistics")[1].findChild(QLineEdit)
    QTest.mouseClick(add, Qt.LeftButton)
    QTest.keyClicks(add, f"{name}={expression}")
    QTest.keyClick(add, Qt.Key_Return)
    QTest.qWait(100)


def pick(combo, text: str):
    """Open a drop-down and click `text` in its list, as you would (near the item's left end: the list can be
    narrower than its items)."""
    view, places = theme_list(combo)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(12, places[text].center().y()))
    QTest.qWait(100)
    assert combo.currentText() == text, f"{text} was not chosen: {combo.currentText()}"


def finished(win, qtbot):
    """Wait for the foot of the window to say that no flow is running (it looks every 2 seconds)."""
    qtbot.waitUntil(lambda: win.running_flows.text() == "No flows running", timeout=10_000)


def centre(tab, block_id: str):
    """Drag the canvas with the middle mouse button, by the block's title bar, until the block is in its middle."""
    rect = tab.scene.nodes[block_id].sceneBoundingRect()
    viewport = tab.view.viewport()
    a = tab.view.mapFromScene(QPointF(rect.center().x(), rect.top() + 10))
    b = viewport.rect().center() - (tab.view.mapFromScene(rect.center()) - a)
    QTest.mousePress(viewport, Qt.MiddleButton, Qt.NoModifier, a)
    for i in range(1, 6):
        QTest.mouseMove(viewport, a + (b - a) * i / 5)
    QTest.mouseRelease(viewport, Qt.MiddleButton, Qt.NoModifier, b)
    QTest.qWait(100)


def run_only(p, win, qtbot, tab, block_id: str, look=None):
    """Right-click `block_id`, choose Run only this block, with the same inputs (`look(menu)` first, to shoot the
    menu), and wait for the run to end: the run."""
    count = len(tab.runs)

    def in_the_menu(menu):
        if look:
            look(menu)
        choose(menu, "Run only this block, with the same inputs")

    p.while_open(lambda: right_click(tab, block_id), in_the_menu)
    qtbot.waitUntil(lambda: len(tab.runs) == count + 1 and not tab.running and tab.runs[-1].status is not None,
                    timeout=120_000)
    finished(win, qtbot)
    return tab.runs[-1]


def shown_in(tab, block_id: str) -> tuple:
    """What the canvas shows of a block: its status and the run its result is from."""
    return tab.scene.nodes[block_id].status, (tab.block_info.get(block_id) or {}).get("run_id")


def run_entries(win, run_id: int, block_id: str = "order_stats") -> list[dict]:
    """The Log's entries of `block_id` in run #`run_id`."""
    found, current = [], False
    for e in win.log.entries:
        if e.get("header") and "run #" in e.get("message", ""):
            current = f"run #{run_id} " in e["message"]
        elif current and e.get("block") == block_id:
            found.append(e)
    return found


def report_of(win, run_id: int) -> str:
    """order_stats' Markdown in the Log in that run, in order."""
    return "\n\n".join(e["message"] for e in run_entries(win, run_id) if e.get("show") == "markdown")


def charts_of(win, run_id: int) -> list[str]:
    """The pictures order_stats showed in the Log in that run: their files."""
    return [e["message"] for e in run_entries(win, run_id) if e.get("show") == "image"]


def md(name: str) -> str:
    """A name as the report's Markdown writes it (a _ is written \\_, and shown as _)."""
    return name.replace("_", "\\_")


def log_text(win) -> str:
    return win.log.text.document().toPlainText()


def find(win, text: str, start: int = 0) -> int:
    """Where `text` is in the Log's text (the first from `start` on)."""
    at = log_text(win).find(text, start)
    assert at >= 0, f"the Log does not show {text!r} after {start}"
    return at


def scroll_log(win, pos: int, above: int = 4):
    """Scroll the Log so the line at `pos` of its text is at its top, as dragging its scroll bar does."""
    QTest.qWait(100)  # (laid out as shown)
    bar = win.log.text.verticalScrollBar()
    bar.setValue(bar.value() + rect_at(win.log.text, pos).top() - above)
    QTest.qWait(150)


def entry_at(win, text: str, start: int) -> tuple[int, int]:
    """Scroll the Log to order_stats' entry that shows `text` (the first from `start` on), its first line at the
    top. Where `text` is in the Log's text, and where its entry ends."""
    at = find(win, text, start)
    scroll_log(win, [m.start() for m in ENTRY.finditer(log_text(win), 0, at)][-1])
    return at, entry_end(win, at)


def entry_end(win, at: int) -> int:
    """Where the entry of the Log that shows its text at `at` ends: its last letter before the next entry's line."""
    shown = log_text(win)
    later = ENTRY.search(shown, at)
    return len(shown[:later.start() if later else len(shown)].rstrip()) - 1


def lines(win, start: int, end: int) -> QRect:
    """What the Log shows of its text from `start` to `end` (its lines, a table's cells too), in sight; less the room
    an outline leaves around it, so that the outline does not cover the lines above and below."""
    view = in_window(win, win.log.text.viewport()).adjusted(8, 8, -8, -8)
    return (area(win, start, end) & view).adjusted(4, 4, -4, -4)


def part_of_line(win, start: int, end: int) -> QRect:
    """What the Log shows of its text from `start` to `end`, both on one line: that part of the line only (a line of
    the Log is not wrapped: it can be wider than the window), less the room an outline leaves above and below it."""
    edit = win.log.text
    a, b = rect_at(edit, start), rect_at(edit, end)
    return (QRect(a.topLeft(), QPoint(b.right(), a.bottom())).translated(edit.viewport().mapTo(win, QPoint(0, 0)))
            .adjusted(-2, 4, 2, -4))


def picture_rects(edit, base) -> list[tuple[int, QRect]]:
    """Each picture a text box (the Log, an email) shows: its place in its text, and where it is in a screenshot of
    `base`, less the room an outline leaves around it."""
    found = []
    for pos, fmt in images(edit):
        width, height = size_of(fmt)
        at = rect_at(edit, pos)
        found.append((pos, QRect(at.x(), at.bottom() + 1 - height, width, height)
                      .translated(edit.viewport().mapTo(base, QPoint(0, 0))).adjusted(5, 5, -5, -5)))
    return found


def picture_in_sight(win, n: int, start: int) -> QRect:
    """Scroll the Log to the `n`th picture (from 0) after `start`, the line with its file just above it: where it
    is in the window."""
    pos = [q for q, _ in picture_rects(win.log.text, win) if q >= start][n]
    scroll_log(win, pos, 26)
    return dict(picture_rects(win.log.text, win))[pos]


def light_png(path: str) -> bool:
    """The picture is drawn on a light background: its four corners are white or nearly."""
    image = QImage(path)
    corners = [image.pixelColor(x, y) for x in (1, image.width() - 2) for y in (1, image.height() - 2)]
    return not image.isNull() and all(min(c.red(), c.green(), c.blue()) >= 235 for c in corners)


def preview_rows(win) -> list[dict]:
    """The rows of the table Data preview shows, each {column: what its cell shows}."""
    model, names = win.preview.view.model(), preview_columns(win)
    return [{n: model.index(r, c).data() for c, n in enumerate(names)} for r in range(model.rowCount())]


def preview_part(win, first_column: int, last_column: int, with_label: bool = True) -> QRect:
    """Data preview's table from `first_column` to `last_column`, its header and rows as far as they are in sight (with
    the line naming it), in the window."""
    view = win.preview.view
    model, header = view.model(), view.horizontalHeader()
    left = view.visualRect(model.index(0, first_column)).left()
    right = view.visualRect(model.index(0, last_column)).right()
    bottom = min(view.visualRect(model.index(model.rowCount() - 1, 0)).bottom(), view.viewport().height() - 1)
    part = (in_window(win, header.viewport(), QRect(left, 0, right - left + 1, header.height()))
            | in_window(win, view.viewport(), QRect(left, 0, right - left + 1, bottom + 1)))
    return part | in_window(win, win.preview.label) if with_label else part


def widen(view, column: int):
    """Double-click the right edge of a column's header, as you would: the column becomes as wide as what it shows."""
    header = view.horizontalHeader()
    edge = header.sectionViewportPosition(column) + header.sectionSize(column) - 1
    QTest.mouseDClick(header.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(edge, header.height() // 2))
    QTest.qWait(100)


def email_page(html: str, images_: dict, width: int = 1500):
    """The email's HTML in Qt's HTML viewer, its pictures taken from the email's own picture parts, all of it shown."""
    browser = QTextBrowser()
    for cid, data in images_.items():
        browser.document().addResource(QTextDocument.ImageResource, f"cid:{cid}", QImage.fromData(data))
    browser.setHtml(html)
    browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    browser.resize(width, 400)
    browser.show()
    QTest.qWait(100)
    browser.resize(width, round(browser.document().size().height()) + 2 * browser.frameWidth() + 4)
    QTest.qWait(100)
    return browser


def test_statistics_of_a_table_as_numbers_and_charts(proof, env, smtp, win, qtbot, tmp_path, monkeypatch):
    p = proof("Statistics: a table's most useful statistics, as numbers and charts", ASKED)
    p.idea("Statistics, a new block under Data, shows the most useful statistics of a table, as numbers and as "
           "charts. You tick the groups of statistics you want, and can have them per group and add your own.")
    p.idea("For each column it gives its type, how many values it has, how many are missing and how many are "
           "different; for numbers the mean, the spread (std), min, the quartiles and median, max, sum, zeros and "
           "negatives; for text the shortest and longest length and the most common values; for dates the first and "
           "the last; and how the number columns go together (correlations).")
    p.idea("The groups to tick are Overview, Numbers, Text, Dates and Correlations, all ticked in a new block. "
           "Columns limits it to the columns you list; Group by works it all out per group; Custom statistics adds "
           "your own, each a name and an SQL aggregate. A statistic SQL cannot say easily is worked out by a Python "
           "Code block, whose dict goes into the block's extra input and is shown in the report.")
    p.idea("What you get: the report in the Log, its numbers in tables, then a chart per column chosen by its type (a "
           "histogram for numbers, the most common values for text, the rows over time for dates) and a heatmap of "
           "the correlations, all on a light background. Three outputs: stats, a table with a row per column, which "
           "Data preview shows; charts, the pictures, for an HTML Template and an email; custom, your own "
           "statistics.")
    p.idea("Every statistic is exact unless you untick Exact: then distinct counts and percentiles are estimated, "
           "faster on very big tables, and each estimated value is marked ≈.")
    p.why_simplest("DuckDB, which Taskloom already has for the DuckDB SQL block, works the statistics out straight "
                   "from the table's file (Parquet, the file every table between blocks is kept in): one query gives "
                   "every column's counts and most of its statistics at once, a few more the most common values, the "
                   "percentiles and the charts' counts. The table is never read into memory as a whole.")
    p.why_simplest("Nothing new to learn to read the result: the report is Markdown, which the Log already draws for "
                   "Inspect, and the charts are pictures in the Log, as Inspect shows a Chart's. stats is an ordinary "
                   "table: Data preview, an HTML Template, a Write File or a DuckDB SQL block take it as any other.")
    p.why_simplest("Your own statistics are SQL, the language of the DuckDB SQL block, worked out by the same DuckDB, "
                   "per group too. For the rare one SQL cannot say easily, the Python Code block you already know does "
                   "it: there is no second Python editor inside Statistics.")
    p.alternative("Polars' describe(), which Taskloom already has",
                  "It gives one fixed list of numbers per column: no most common values, no correlations, nothing "
                  "per group, no charts and no statistics of your own. DuckDB works out all of these statistics, and "
                  "the counts the charts are drawn from, in SQL, the language your own statistics are written in; "
                  "the block then draws the charts from those counts.")
    p.alternative("A small Python box inside the Statistics block",
                  "A second Python editor, with its own inputs, errors and environment, beside the Python Code block "
                  "that already has them. Connecting a Python Code block to the extra input reuses it, as you "
                  "suggested (“leverage the existing python block”).")
    p.alternative("A tick for each statistic",
                  "About twenty ticks to go through, and a chart would belong to no single one. Five groups, each "
                  "with its statistics and its chart, is what we agreed.")
    p.alternative("Estimates by default, for speed",
                  "The numbers you read could then be off without your asking for it. Exact is the default; you untick "
                  "it for a very big table, and every estimated value says so with ≈.")
    p.alternative("Interactive charts (zoom, hover)",
                  "They need a web page that runs scripts: the Log shows pictures, and Outlook runs no scripts. A PNG "
                  "picture shows the same in the Log, an HTML Template and an email.")
    p.fits("It is in the Blocks list under Data, with its own icon (a clipboard with a chart, from the same Tabler set "
           "as the other icons), and in the hover help and Help > Documentation, as every block.")
    p.fits("Its charts are drawn in the colours and on the white background of the Chart block's, also in the Dark "
           "theme: they go into emails. stats and charts go into an HTML Template as any table and any Chart's "
           "picture do.")
    p.fits("Run only this block, with the same inputs, works for it as for every block: change a setting and look "
           "again, without reading the file again.")
    p.fits("Nothing is left out silently, as everywhere in Taskloom: each statistic is over the values present, and "
           "the count of the missing ones is next to it; a column that is not in the table, or a statistic of your "
           "own that is not valid SQL, fails the block, naming it.")

    win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)
    csv = write_orders(tmp_path / "orders.csv")
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    try:
        # --- 1. In the Blocks list -------------------------------------------------------------------------------
        p.step("Look at the Blocks list, on the left, under Data.")
        data = {item.text(0): item for item in rows(win.palette_panel)["Data"]}
        listed = data.get("Statistics")
        tree = win.palette_panel.tree
        tree.scrollToItem(listed)
        QApplication.processEvents()
        group = [list_row(win, listed.parent().child(i)) for i in range(listed.parent().childCount())]
        p.shot(win, "① Statistics, under Data, between Read File and Write File, with its icon: a clipboard with a "
                    "chart, white on the Data teal.",
               highlight=[list_row(win, listed)], badges=True, zoom=[list_row(win, listed.parent()), *group])
        p.check("The Blocks list has Statistics under Data", listed is not None and "DuckDB SQL" in data)

        p.step("Rest the mouse on Statistics in the Blocks list.")
        viewport = tree.viewport()
        at = tree.visualItemRect(listed).center()
        QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, at, viewport.mapToGlobal(at)))
        qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
        tip = tooltip()
        said = " ".join(plain(tip.text()).split())
        cls = win.registry.get(STATS)
        p.shot(tip, "Its hover help says what it shows (numbers and charts), what you choose (the groups to tick, "
                    "per group, your own statistics in SQL), where the results go, and that Exact can be unticked "
                    "for very big tables; then its inputs: the table, and the optional extra from a Python Code block.",
               over=win, highlight=[tip], zoom=[tip])
        tip.hide()
        p.check("The hover help has the block's name, says what it does and names its inputs table and extra",
                said.startswith("Statistics") and " ".join(cls.description.split()) in said
                and " ".join(cls.inputs_help.split()) in said)

        # --- 2. The orders, the flow and the block's Properties ----------------------------------------------------
        p.step("Look at orders.csv, the file the flow reads: 25 orders, made by the proof.")
        p.text("orders.csv, its lines in two columns: two orders with no region, two with no amount, one with no "
               "quantity and one with no day; order 1012 is there twice; no orders on Saturday 10 and Sunday 11 "
               "January", side_by_side(csv.read_text(encoding="utf-8")))

        flow = write_flow(tmp_path / "orders.yaml", csv)
        p.step("Open the flow orders.yaml (File > Open…).")
        tab = open_flow(win, monkeypatch, flow)
        p.shot(win, "The flow: ① read_orders, a Read File, reads orders.csv; ② as_dates, a DuckDB SQL block, makes "
                    "its day a date; ③ top_share, a Python Code block, works out a statistic of its own; ④ "
                    "order_stats, the Statistics block, takes the table and top_share's output (its extra input); ⑤ "
                    "report_page, an HTML Template, puts its stats and charts into an email, which email_report "
                    "sends.",
               highlight=["read_orders", "as_dates", "top_share", "order_stats", ["report_page", "email_report"]],
               badges=True, zoom=["read_orders", "top_share", "order_stats", "report_page", "email_report"])

        p.step("Click order_stats.")
        click(tab, "order_stats")
        props = win.right_tabs
        group_rows = [properties_row(win, g, props) for g in GROUPS]
        p.shot(props, "Its Properties: ① the five groups of statistics, Overview, Numbers, Text, Dates and "
                      "Correlations, all ticked; ② Columns and Group by, empty (every column, no groups); ③ Custom "
                      "statistics, a table with no rows yet, and the line to add one; ④ Exact, ticked.",
               highlight=[group_rows, [properties_row(win, "Columns", props), properties_row(win, "Group by", props)],
                          custom_box(win, props),
                          properties_row(win, "Exact", props)], badges=True)
        p.check("Overview, Numbers, Text, Dates, Correlations and Exact are shown ticked",
                ticked(win) == {label: True for label in (*GROUPS, "Exact")})
        p.check("Columns and Group by are empty; Custom statistics has no rows",
                setting(win, "Columns")[1].text() == "" and setting(win, "Group by")[1].text() == ""
                and custom_rows(win) == [])

        p.step(f"In Columns, type {', '.join(DESCRIBED)} and press Enter: order_id is left out, as the mean or the "
               "histogram of order numbers says nothing.")
        type_into(setting(win, "Columns")[1], ", ".join(DESCRIBED))
        p.shot(win, "① Columns lists the four columns to describe.", highlight=[properties_row(win, "Columns")],
               badges=True, zoom=[properties_row(win, "Name"), properties_row(win, "Exact")])
        p.check(f"The block's Columns are {', '.join(DESCRIBED)}",
                tab.doc.blocks["order_stats"]["config"].get("columns") == list(DESCRIBED))

        # --- 3. Run: the report in the Log ----------------------------------------------------------------------------
        p.step("Press ▶ Run now.")
        run = run_now(win, qtbot, tab)
        finished(win, qtbot)
        first = run.run_id
        foot = tab.scene.nodes["order_stats"].status_text
        p.shot(win, "Every block ran: ① order_stats is green, and its foot says what it gave: stats, a table of 4 rows "
                    "(a row per column) × 21 columns.",
               highlight=["order_stats"], badges=True, zoom=["as_dates", "order_stats", "top_share"])
        p.check("The run succeeded: every block of the flow is green",
                run.status == "success" and all(shown_in(tab, b) == ("success", first) for b in tab.scene.nodes))
        p.check("order_stats' foot says “stats: 4 rows × 21 columns”", "stats: 4 rows × 21 columns" in foot)
        p.note("A CSV file has no types: Read File reads its day column as text. So the flow makes it a date in "
               f"as_dates, with “{AS_DATES}”, and Statistics treats it as one. Read straight from the CSV, day would "
               "be described as text, by its most common values.")

        p.step("Drag the edge above the Log up, to make the Log taller. order_stats is still selected, so the Log "
               "shows its lines only; scroll up to where its report starts.")
        drag_up(win, win.output, TALL_LOG - win.output.height())
        qtbot.waitUntil(lambda: win.output.height() >= TALL_LOG - 30, timeout=5_000)
        click(tab, "order_stats")
        click_bottom_tab(win, win.log)
        report = report_of(win, first)
        start = find(win, f"run #{first} · ")
        at, end = entry_at(win, "Overview", start)
        overview = lines(win, at, end)
        p.shot(win, "① The overview: 25 rows and 5 columns, 6 missing cells of 125, and 1 fully repeated row (order "
                    "1012, there twice); then each column's type and its missing values. It is about the whole table, "
                    "order_id too.",
               highlight=[overview], badges=True, zoom=[overview])
        p.check(f"The overview says: Rows 25, Columns 5, Missing cells {missing_cells()} of 125 (4.80%), Fully "
                "repeated rows 1",
                all(f"**{k}:** {v}" in report for k, v in (("Rows", len(ORDERS)), ("Columns", len(NAMES)),
                                                           ("Missing cells", f"{missing_cells()} of 125 (4.80%)"),
                                                           ("Fully repeated rows", 1))))
        p.check("Its table gives each column's type and missing values: order_id Int64 0, region String 2, amount "
                "Float64 2, quantity Int64 1, day Date 1",
                all(table_row(md(n), t, m) in report for n, t, m in (
                    ("order_id", "Int64", 0), ("region", "String", 2), ("amount", "Float64", 2),
                    ("quantity", "Int64", 1), ("day", "Date", 1))))

        at, _ = entry_at(win, "Numbers", start)
        text_at, dates_at = find(win, "Text", at), find(win, "Dates", at)
        p.shot(win, "① Numbers: a row per number column, amount and quantity: the values present (count), the "
                    "missing ones, how many are different, then mean, std, min, the quartiles and median, max, sum, "
                    "zeros and negatives. ② Text: region's shortest and longest value, and its most common values "
                    "with their counts.",
               highlight=[lines(win, at, text_at - 1), lines(win, text_at, dates_at - 1)],
               badges=True, zoom=[lines(win, at, dates_at - 1)])
        for name in ("amount", "quantity"):
            p.check(f"Numbers, {name}: {in_words(NUMBER_STATS, number_stats(name))}, as the proof works them out "
                    "from the orders itself",
                    table_row(name, *number_stats(name)) in report)
        p.check(f"Text, region: count 23, missing 2, distinct 4, shortest 4, longest 5, most common "
                f"{most_common('region')}",
                table_row("region", 23, 2, 4, 4, 5, most_common("region")) in report)
        p.note("Each statistic is over the values present: amount's mean is that of its 23 amounts, and its 2 missing "
               "ones are counted next to them, never taken as 0.")

        at, end = entry_at(win, "Dates", start)
        r = correlation()
        correlations = find(win, "Correlations", at)
        p.shot(win, f"① Dates: day's first and last, 5 and 16 January, and its one missing value. ② Correlations: "
                    f"amount and quantity go together, r = {r:.2f}, over the 22 orders that have both.",
               highlight=[lines(win, at, correlations - 1), lines(win, correlations, end)],
               badges=True, zoom=[lines(win, at, end)])
        p.check("Dates, day: count 24, missing 1, distinct 10, first 2026-01-05, last 2026-01-16",
                table_row("day", 24, 1, 10, "2026-01-05", "2026-01-16") in report)
        p.check(f"Correlations: amount and quantity, {r:.2f} (Pearson's r, worked out by the proof itself)",
                table_row("amount", "1.00", f"{r:.2f}") in report
                and table_row("quantity", f"{r:.2f}", "1.00") in report)

        at, end = entry_at(win, "Extra input", start)
        extra = lines(win, at, end)
        p.shot(win, "① Extra input: the dict top_share made, under its own heading, a row per key: 23 orders have an "
                    f"amount, and the biggest fifth of them make {top_share()} of the total.",
               highlight=[extra], badges=True, zoom=[extra])
        p.check(f"Extra input, under its own heading: “orders with an amount” 23 and “share of the total from the "
                f"biggest 20%” {top_share()}",
                "## Extra input" in report and table_row("orders with an amount", 23) in report
                and table_row("share of the total from the biggest 20%", top_share()) in report)
        headings = [f"## {h}" for h in (*GROUPS, "Extra input")]
        p.check("The report comes in this order: Overview, Numbers, Text, Dates, Correlations, Extra input",
                all(h in report for h in headings) and [report.find(h) for h in headings]
                == sorted(report.find(h) for h in headings))

        # --- 4. The charts -------------------------------------------------------------------------------------------
        charts = charts_of(win, first)
        p.step("Scroll down the Log to the charts, under the report.")
        kinds = {0: "a histogram of amount: how many orders have an amount in each band (most are under 220)",
                 2: "the most common values of region: north, then south, then east and west",
                 3: "the rows per day of day: none on the weekend of 10 and 11 January",
                 4: f"the correlations between amount and quantity as a heatmap: red for r near 1, {r:.2f} between "
                    "them, 1 on the diagonal"}
        for n, kind in kinds.items():
            picture = picture_in_sight(win, n, start)
            p.shot(win, f"① Chart {n + 1} of {len(charts)}, {kind}. On white, as every chart.",
                   highlight=[picture], badges=True, zoom=[picture])
        p.check("The Log shows 5 charts after the report, as many as the 4 columns described and the correlations "
                "make, each a PNG picture on a light background",
                len(charts) == 5 and len([q for q, _ in picture_rects(win.log.text, win) if q >= start]) == 5
                and all(light_png(c) for c in charts))
        p.note("Chart 2, the histogram of quantity, is not shown here: it has a bar per quantity, 1 to 7, as "
               "quantities are whole numbers; the email below shows it. The proof cannot read the charts' titles back "
               "from the pictures: the block's own tests check how many charts each kind of column gets, and you see "
               "here which chart each one is.")

        # --- 5. Data preview: stats ----------------------------------------------------------------------------------
        p.step("Click Data preview, under the canvas.")
        click_bottom_tab(win, win.preview)
        names = preview_columns(win)
        shown = {row["column"]: row for row in preview_rows(win)}
        part = preview_part(win, 0, names.index("max"))
        p.shot(win, "① stats, a row per column of Columns: its name and type, then count, missing, missing %, "
                    "distinct, mean, std, min, the quartiles and median, max, and on to the right the rest (sum, "
                    "zeros, negatives, shortest, longest, most common, first, last).",
               highlight=[part], badges=True, zoom=[part])
        p.check("Data preview shows order_stats.stats: 4 rows, region, amount, quantity and day, with their types",
                win.preview.label.text().startswith("order_stats.stats: 4 rows")
                and [(n, row["type"]) for n, row in shown.items()]
                == [("region", "String"), ("amount", "Float64"), ("quantity", "Int64"), ("day", "Date")])
        expected = {n: (len(present(column(n))), len(column(n)) - len(present(column(n)))) for n in DESCRIBED}
        p.check("Each row's count and missing are the orders' own: region 23 and 2, amount 23 and 2, quantity 24 and "
                "1, day 24 and 1",
                {n: (int(row["count"]), int(row["missing"])) for n, row in shown.items()} == expected
                == {"region": (23, 2), "amount": (23, 2), "quantity": (24, 1), "day": (24, 1)})

        # --- 6. The email --------------------------------------------------------------------------------------------
        p.step("Look at the email email_report sent: report_page put order_stats' stats and charts into it, with the "
               f"template “{TEMPLATE}”.")
        p.note("The proof's mail server runs on this computer (127.0.0.1): nothing leaves it. The email is shown in "
               "Qt's HTML viewer, its pictures taken from the email itself, as an email program shows them.")
        [msg] = smtp.messages
        html, attached = html_of(msg), inline_images(msg)
        page = email_page(html, attached)
        in_email = picture_rects(page, page)
        whole = page.size()
        page.resize(page.width(), in_email[0][1].top() - 4)  # its top: down to the first picture
        p.shot(page, "The top of the email: its heading, then stats as a table, a row per column, as Data preview "
                     "showed it, its numbers on the right.")
        page.resize(whole)
        QTest.qWait(100)
        p.shot(page, "All of the email: the table, then the five charts, each a picture inside the email; ① the rows "
                     "per day, close up.", highlight=[in_email[3][1]], badges=True, zoom=[in_email[3][1]])
        page.close()
        p.check("The email has stats as a table: its header (column, type, count, missing, mean, …) and a row each for "
                "region, amount, quantity and day",
                all(re.search(rf"<th[^>]*>{h}</th>", html) for h in ("column", "type", "count", "missing", "mean"))
                and all(f">{n}<" in html for n in DESCRIBED))
        p.check("The email shows 5 pictures, one per chart, each sent with it",
                len(attached) == 5 and html.count('<img src="cid:') == 5 and len(in_email) == 5)

        # --- 7. Group by and statistics of your own ------------------------------------------------------------------
        click_bottom_tab(win, win.log)
        p.step(f"Click order_stats. In Columns, type {', '.join(PER_REGION)} (region becomes the group); in Group by, "
               f"region; and under Custom statistics type {P95[0]}={P95[1]} in the line to add a row and press Enter, "
               f"then {NORTH_SHARE[0]}={NORTH_SHARE[1]} and Enter.")
        click(tab, "order_stats")
        type_into(setting(win, "Columns")[1], ", ".join(PER_REGION))
        type_into(setting(win, "Group by")[1], "region")
        add_custom(win, *P95)
        add_custom(win, *NORTH_SHARE)
        scroll_properties(win, "Correlations")
        p.shot(win, "① Columns amount, quantity and day, and Group by region. ② Custom statistics: p95 (the amount "
                    "95% of orders stay under) and north_share (the share of orders from the north), each ticked.",
               highlight=[[properties_row(win, "Columns"), properties_row(win, "Group by")], custom_box(win)],
               badges=True, zoom=[properties_row(win, "Columns"), custom_box(win)])
        p.check("Custom statistics shows the two rows, ticked: p95 = quantile_cont(amount, 0.95) and north_share = "
                "avg(case when region = 'north' then 1 else 0 end)",
                custom_rows(win) == [(True, *P95), (True, *NORTH_SHARE)])

        p.step("Right-click order_stats and choose “Run only this block, with the same inputs”: it runs again on "
               "the table it got in the first run; the blocks before it do not run.")

        def look(menu):
            p.shot(menu, "① Run only this block, with the same inputs: those of run 1.", over=win,
                   highlight=[menu_item(menu, "Run only this block, with the same inputs")], badges=True,
                   zoom=[menu, "order_stats"])

        run = run_only(p, win, qtbot, tab, "order_stats", look)
        grouped = run.run_id
        report = report_of(win, grouped)
        p.check("It succeeded, and only order_stats ran: read_orders still shows its result from run 1",
                run.status == "success" and shown_in(tab, "order_stats") == ("success", grouped)
                and shown_in(tab, "read_orders") == ("success", first))
        start = find(win, f"run #{grouped} · ")
        at, end = entry_at(win, "region: north", start)
        north = lines(win, at, end)
        p.shot(win, "① In the report, a section per region; here north's: its 10 rows, then Numbers, Text (no text "
                    "column now: region is the group), Dates and Correlations, for north's orders only.",
               highlight=[north], badges=True, zoom=[north])
        sections = [f"## region: {'(missing)' if g is None else g}" for g in REGIONS]
        p.check("The report has a section per region, in this order: east, north, south, west, and (missing) for the "
                "2 orders with no region, each saying its rows: 4, 10, 5, 4, 2",
                [report.find(s) for s in sections] == sorted(report.find(s) for s in sections)
                and all(f"{s}\n\n{len(column('region', g))} rows" in report for s, g in zip(sections, REGIONS)))
        p.check(f"North's amount: {in_words(NUMBER_STATS, number_stats('amount', 'north'))}, as the proof works them "
                "out from north's orders itself",
                table_row("amount", *number_stats("amount", "north")) in report)
        p.check("The overview says: Groups 5, by region", "**Groups:** 5, by region" in report)

        at, end = entry_at(win, "Custom statistics", start)
        own = lines(win, at, end)
        p95s = {g: quantile_cont(present(column("amount", g)), 0.95) for g in REGIONS}
        p.shot(win, "① Custom statistics, a row per region, the orders with no region last: p95, and north_share, 1 "
                    "for north and 0 for the others, as it must be within a region.",
               highlight=[own], badges=True, zoom=[own])
        p.check("Custom statistics in the report: a row per region with its p95 (to 2 decimals) and north_share",
                all(table_row("" if g is None else g, f"{p95s[g]:,.2f}", "1.00" if g == "north" else "0.00")
                    in report for g in REGIONS))

        p.step("Click Data preview.")
        click_bottom_tab(win, win.preview)
        names = preview_columns(win)
        shown = preview_rows(win)
        part = preview_part(win, 0, names.index("distinct"))
        p.shot(win, "① stats now has region first, then a row per region and column: east's amount, quantity and "
                    "day, then north's, and so on, the orders with no region last (its day row is just below, "
                    "scrolled out of sight).",
               highlight=[part], badges=True, zoom=[part])
        wanted = [(g or "", n, len(present(column(n, g))), len(column(n, g)) - len(present(column(n, g))))
                  for g in REGIONS for n in PER_REGION]
        p.check("stats: region first, then column; 15 rows, one per region and column, each with the count and "
                "missing values of that region's orders",
                names[:2] == ["region", "column"]
                and [(r["region"], r["column"], int(r["count"]), int(r["missing"])) for r in shown] == wanted)

        p.step("Choose custom in the drop-down beside the table's name.")
        pick(win.preview.ports, "custom")
        shown = preview_rows(win)
        part = preview_part(win, 0, 2)
        p.shot(win, "① custom: a row per region, region first, then p95 and north_share.", highlight=[part],
               badges=True, zoom=[part])
        p.check("custom has the columns region, p95 and north_share, and a row per region whose p95 and north_share "
                "are those the proof works out from the orders itself",
                preview_columns(win) == ["region", "p95", "north_share"]
                and [r["region"] for r in shown] == [g or "" for g in REGIONS]
                and all(abs(float(r["p95"]) - p95s[g]) < 1e-9
                        and float(r["north_share"]) == (1.0 if g == "north" else 0.0)
                        for r, g in zip(shown, REGIONS)))

        # --- 8. Exact unticked ---------------------------------------------------------------------------------------
        p.step("Click Exact's tick, to untick it. Then right-click order_stats and choose “Run only this block, with "
               "the same inputs”.")
        click_bottom_tab(win, win.log)
        click(tab, "order_stats")
        click_tick(setting(win, "Exact")[1])
        p.check("Exact is unticked", setting(win, "Exact")[1].isChecked() is False
                and tab.doc.blocks["order_stats"]["config"].get("exact") is False)
        run = run_only(p, win, qtbot, tab, "order_stats")
        estimated = run.run_id
        report = report_of(win, estimated)
        start = find(win, f"run #{estimated} · ")
        at, _ = entry_at(win, "region: north", start)
        north = lines(win, at, find(win, "Text", at) - 1)
        p.shot(win, "① North's numbers: distinct, 25%, median and 75% are now estimated, each marked ≈. The rest "
                    "(count, missing, mean, std, min, max, sum) stays exact.",
               highlight=[north], badges=True, zoom=[north])
        exact_north = number_stats("amount", "north")
        row = re.search(r"\| amount \| " + r" \| ".join(
            [re.escape(exact_north[0]), re.escape(exact_north[1]), r"≈ (\d+)", *map(re.escape, exact_north[3:6]),
             r"≈ [\d,.]+", r"≈ [\d,.]+", r"≈ [\d,.]+", *map(re.escape, exact_north[9:])]) + r" \|", report)
        p.check(f"The report marks the estimated values with ≈: north's amount has ≈ before its distinct count, 25%, "
                f"median and 75%, and its count, missing, mean, std, min, max and sum are the exact ones "
                f"({in_words(('count', 'missing'), exact_north[:2])}, mean {exact_north[3]}, sum {exact_north[10]})",
                row is not None)
        repeated = re.search(r"\*\*Fully repeated rows:\*\* ≈ (\d+)", report)
        p.check("The overview's fully repeated rows are marked ≈ too", repeated is not None)
        p.note(f"On a table this small the estimates are visibly off: north has {exact_north[2]} different amounts, "
               f"shown as ≈ {row[1] if row else '?'}, and the 1 fully repeated row as ≈ "
               f"{repeated[1] if repeated else '?'}. Estimates pay off only on very big tables: that is why Exact is "
               "the default. Exact percentiles need a column's values at hand while they are worked out (one column "
               "at a time): for a table too big for that, untick Exact.")

        p.step("Click Data preview, scroll its table to the right, to its last column, estimated, and double-click "
               "the right edge of that column's header to widen it.")
        click_bottom_tab(win, win.preview)
        view = win.preview.view
        names = preview_columns(win)
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum())
        QTest.qWait(100)
        widen(view, names.index("estimated"))
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum())
        QTest.qWait(100)
        shown = preview_rows(win)
        part = preview_part(win, names.index("first"), names.index("estimated"), with_label=False)
        p.shot(win, "① stats has one more column, estimated: which statistics of the row were estimated. Its rows "
                    "come in the same order as before: east's amount, quantity and day, then north's, and so on.",
               highlight=[part], badges=True, zoom=[part])
        p.check("stats' estimated column says “distinct, 25%, median, 75%” on each amount and quantity row, and "
                "“distinct” on each day row",
                names[-1] == "estimated"
                and all(r["estimated"] == ("distinct" if r["column"] == "day" else "distinct, 25%, median, 75%")
                        for r in shown))

        # --- 9. A statistic of your own that is not valid SQL --------------------------------------------------------
        click_bottom_tab(win, win.log)
        click(tab, "order_stats")
        p.step(f"Under Custom statistics, add {BROKEN[0]}={BROKEN[1]}: its case has no end, so it is not valid SQL. "
               "Then run order_stats only again, and drag the canvas (middle mouse button) to bring order_stats to "
               "its middle.")
        add_custom(win, *BROKEN)
        run = run_only(p, win, qtbot, tab, "order_stats")
        broken = run.run_id
        centre(tab, "order_stats")
        error = next((e["message"] for e in run_entries(win, broken) if e.get("level") == "ERROR"), "")
        start = find(win, f"run #{broken} · ")
        said_at = find(win, f"Custom statistics: {BROKEN[0]} = ", start)
        scroll_log(win, said_at, 60)
        p.shot(win, "① order_stats failed: its foot says why in red, cut short to fit (“Custom statistics: "
                    "south_sh…”).",
               highlight=["order_stats"], badges=True, zoom=["order_stats"])
        failed_at = find(win, "failed: ", find(win, "[order_stats] ERROR", start))
        near = 'syntax error at or near ")"'
        failure = part_of_line(win, failed_at, find(win, near, said_at) + len(near))
        p.shot(win, f"① The Log says it in full, naming the statistic: “{BROKEN[0]} = {BROKEN[1]}: Parser Error: "
                    f"{near}” (the whole error is written out above).",
               highlight=[failure], badges=True, zoom=[failure])
        p.text("What the Log says of order_stats in this run", error.split("\nInputs:")[0])
        p.check("The run failed at order_stats, and its foot on the canvas names the statistic",
                run.status == "failed" and shown_in(tab, "order_stats") == ("failed", broken)
                and f"Custom statistics: {BROKEN[0]} = " in tab.scene.nodes["order_stats"].status_text)
        p.check(f"The error names it: “Custom statistics: {BROKEN[0]} = {BROKEN[1]}: Parser Error: syntax error …”, "
                "and says what each must be, with no traceback",
                f"Custom statistics: {BROKEN[0]} = {BROKEN[1]}: Parser Error: syntax error" in error
                and "each is an SQL aggregate over the table" in error and "Traceback" not in error)

        p.step(f"Click {BROKEN[0]}'s tick in Custom statistics, to untick it: only the ticked rows are worked out.")
        scroll_properties(win, "Group by")
        table = custom_table(win)
        QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier,
                         table.visualItemRect(table.item(2, 0)).center())
        QTest.qWait(100)
        scroll_properties(win, "Group by")
        p.shot(win, f"① {BROKEN[0]} is unticked: it stays in the table, and is not worked out.",
               highlight=[custom_box(win)], badges=True, zoom=[custom_box(win)])
        p.check(f"Custom statistics: p95 and north_share ticked, {BROKEN[0]} unticked",
                custom_rows(win) == [(True, *P95), (True, *NORTH_SHARE), (False, *BROKEN)])

        # --- 10. Dark ----------------------------------------------------------------------------------------------
        p.step("Choose Tools > Settings…, set Theme to Dark and press Save. Then run order_stats only again.")
        set_theme(p, win, "Dark", shoot=False)
        run = run_only(p, win, qtbot, tab, "order_stats")
        dark = run.run_id
        click(tab, "order_stats")
        start = find(win, f"run #{dark} · ")
        picture = picture_in_sight(win, 0, start)
        p.shot(win, "In Dark: ① the Log is dark, and the chart order_stats drew in this run is on white, as in an "
                    "email.",
               highlight=[picture], badges=True, zoom=[picture])
        charts = charts_of(win, dark)
        p.check("The editor and the Log are dark",
                QApplication.palette().window().color().lightness() < 128
                and win.log.text.palette().color(QPalette.Base).lightness() < 128)
        p.check("The run succeeded, south_share left out: custom has region, p95 and north_share",
                run.status == "success" and table_row("region", "p95", md("north_share")) in report_of(win, dark))
        p.check(f"Each of the {len(charts)} charts drawn in Dark is on a light background (its corners white)",
                len(charts) == 5 and all(light_png(c) for c in charts))

        p.step("Set Theme back to System (Tools > Settings…) and press Save.")
        set_theme(p, win, "System", shoot=False)
        p.check("The Theme is System again, and the editor light",
                theme.chosen(win.settings) == "System"
                and QApplication.palette().window().color().lightness() >= 128)
        p.note("Not part of this change: comparing a table's statistics with those of earlier runs, to warn when they "
               "change a lot. That is Detect Anomalies, a later release.")
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
