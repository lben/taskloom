"""Proof: Check Table, the first block of a new Checks category: it checks a table (its row count, also against the last
successful run, missing values, unique keys, columns and types, values in a range or a list, freshness, conditions
every row must meet) and passes it on unchanged only when its checks pass; each check fails the block or only warns.
How proofs are written: README.md here."""

import datetime as dt

import polars as pl
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QLabel, QStyle,
                               QStyleOptionButton, QToolTip)

from proofs.test_proof_api_request import (click_bottom_tab, in_window, open_flow, preview_columns, properties_row,
                                           scroll_to, span)
from proofs.test_proof_block_icons import list_row
from proofs.test_proof_line_failures import boxes_said, press
from proofs.test_proof_python_reusable_block import plain, run_now, tooltip
from proofs.test_proof_result_time import hover
from proofs.test_proof_run_output import click, setting
from proofs.test_proof_settings_wording import menu_action
from proofs.test_proof_tab_plus import set_theme
from proofs.test_proof_theme import theme_list
from taskloom.editor import theme
from tests.test_block_icons import rows
from tests.test_paths_editor_editing import clean, flow_file
from tests.test_theme import not_in_theme

ASKED = ("Add Checks to the queue after this release”, and then: “Yes to both extras. Also check for the data type of "
         "a column, sometimes I have found surprises of text data within a numeric column for instance, users may "
         "want to check if a column has null values as well.” — The two extras: the row count compared with the last "
         "successful run (e.g. within 30%), and a Check File block. Agreed with you: a new Checks category; each "
         "check is ticked to use it, and either fails the block (the default) or only warns in the Log.")
CHECK = "checks.check_table"
LOG_HEIGHT = 260  # the Log, under the canvas
CLOSE_UP = 740  # the widest close-up of the Log or of Data preview, in pixels (their lines go on to the right)
# Each check of Check Table, by its tick in Properties: the settings under it while it is ticked, If it fails last.
CHECKS = {
    "Row count": ["At least", "At most", "If it fails"],
    "Row count compared with the last successful run": ["Within %", "If it fails"],
    "No missing values": ["In columns", "Count empty text as missing", "Allowed", "If it fails"],
    "Unique": ["Key columns", "If it fails"],
    "Columns and types": ["Column types", "If it fails"],
    "Values within a range or a list": ["Allowed values", "If it fails"],
    "Freshness": ["Date column", "Latest at least", "If it fails"],
    "Conditions every row must meet": ["Conditions", "If it fails"],
}
TICKS = list(CHECKS)
ROW_COUNT, COMPARED, MISSING, UNIQUE, TYPES, VALUES, FRESHNESS, CONDITIONS = TICKS
NEEDS = [  # what Properties says the eight checks, ticked as a new block has them, still need
    "Unique: list the column, or the columns together, whose values must not repeat",
    "Columns and types: add a row Column: type for each column the table must have",
    "Values within a range or a list: add a row Column=min..max or Column=a, b, c",
    "Freshness: write the Date column whose latest value it checks",
    "Conditions every row must meet: write at least one condition, e.g. amount >= 0"]


def day(days_ago: int) -> str:
    return (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()


def arrived() -> list[tuple]:
    """The orders as they arrive: rows 2 and 9 have N/A as their amount, row 4 1,234.5 (a comma in the number); row 5
    has no amount; row 6 repeats order 1003 (row 3); row 7's region, central, is not one of the four; row 8's amount
    is below 0. They are from 6 to 3 days ago."""
    return [(1001, "north", "120.5", day(6)), (1002, "south", "N/A", day(6)), (1003, "east", "80", day(5)),
            (1004, "west", "1,234.5", day(5)), (1005, "north", None, day(4)), (1003, "south", "45", day(4)),
            (1007, "central", "60", day(4)), (1008, "east", "-15", day(3)), (1009, "west", "N/A", day(3)),
            (1010, "north", "30", day(3))]


def first_fix() -> list[tuple]:
    """The amounts that were text and the repeated order, fixed."""
    fixes = {2: (1002, "south", "52", day(6)), 4: (1004, "west", "1234.5", day(5)), 6: (1006, "south", "45", day(4)),
             9: (1009, "west", "18", day(3))}
    return [fixes.get(n, row) for n, row in enumerate(arrived(), 1)]


def second_fix() -> list[tuple]:
    """The missing and the negative amount, fixed too."""
    fixes = {5: (1005, "north", "64", day(4)), 8: (1008, "east", "15", day(3))}
    return [fixes.get(n, row) for n, row in enumerate(first_fix(), 1)]


def write_orders(path, orders):
    """A CSV file of orders: order_id, region, amount, day; None is an empty field, a value with a comma is quoted."""
    def cell(value):
        return "" if value is None else f'"{value}"' if "," in str(value) else str(value)

    lines = ["order_id,region,amount,day", *(",".join(cell(v) for v in row) for row in orders)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return path


# --- the flows ------------------------------------------------------------------------------------------------------

def start(x=0, y=0) -> dict:
    return {"type": "trigger.manual", "ui": {"x": x, "y": y}}


def read_file(name: str, x, y) -> dict:
    return {"type": "data.read_file", "config": {"path": name}, "ui": {"x": x, "y": y}}  # (in the flow's folder)


def check_table(x, y, **config) -> dict:
    return {"type": CHECK, "config": config, "ui": {"x": x, "y": y}}


def python(inputs: list, code: str, x, y) -> dict:
    return {"type": "logic.python", "config": {"inputs": inputs, "code": code}, "ui": {"x": x, "y": y}}


TOTALS = "select region, sum(amount) as total from orders group by region order by region"


def orders_flow(folder):
    """start_by_hand -> load_orders (Read File, orders.csv) -> check_orders (Check Table) -> totals (DuckDB SQL);
    check_orders' error output -> on_failed_checks (Python Code), which gives the results it carries."""
    return flow_file(folder / "orders.yaml", {
        "start_by_hand": start(0, 40),
        "load_orders": read_file("orders.csv", 230, 20),
        "check_orders": check_table(
            480, 0, row_count=True, at_least=1,
            missing=True, missing_fails="warn",
            unique=True, unique_columns=["order_id"],
            types=True, types_columns=[{"key": "amount", "value": "float"}],
            values=True, allowed_values=[{"key": "region", "value": "north, south, east, west"}], values_fails="warn",
            conditions=True, rules=["amount >= 0"]),
        "totals": {"type": "data.duckdb_sql", "config": {"inputs": ["orders"], "sql": TOTALS},
                   "ui": {"x": 760, "y": 0}},
        "on_failed_checks": python(["error"], "output = error['results']", 760, 190),
    }, ["start_by_hand.start -> load_orders.after", "load_orders.table -> check_orders.table",
        "check_orders.table -> totals.orders", "check_orders.error -> on_failed_checks.error"])


def one_check_flow(folder, name: str, data: str, block: str, **config):
    """start_by_hand -> load (Read File, `data`) -> `block` (Check Table with `config`) -> totals (DuckDB SQL)."""
    return flow_file(folder / name, {
        "start_by_hand": start(0, 40), "load": read_file(data, 230, 20), block: check_table(480, 0, **config),
        "totals": {"type": "data.duckdb_sql", "config": {"inputs": ["orders"], "sql": TOTALS},
                   "ui": {"x": 760, "y": 0}},
    }, ["start_by_hand.start -> load.after", f"load.table -> {block}.table", f"{block}.table -> totals.orders"])


def loop_flow(folder):
    """start_by_hand -> regions (Python Code) -> each_region (a Loop); inside it: load (Read File, after each item) ->
    check_region (Check Table, the row count compared with the last successful run)."""
    return flow_file(folder / "each-region.yaml", {
        "start_by_hand": start(0, 60),
        "regions": python([], "output = ['north', 'south', 'east', 'west']", 230, 40),
        "each_region": {"type": "logic.loop", "config": {}, "ui": {"x": 470, "y": 0, "width": 640, "height": 230}},
        "load": {**read_file("orders.csv", 540, 60), "inside": "each_region"},
        "check_region": {**check_table(800, 40, compared=True), "inside": "each_region"},
    }, ["start_by_hand.start -> regions.after", "regions.output -> each_region.items", "each_region.item -> load.after",
        "load.table -> check_region.table"])


# --- Properties -----------------------------------------------------------------------------------------------------

def tick(win, label: str) -> QCheckBox:
    """The check box of a check's tick in Properties."""
    box = setting(win, label)[1]
    assert isinstance(box, QCheckBox), f"{label} is not a tick"
    return box


def click_tick(win, label: str):
    """Click a check's tick in Properties, as you would (Properties is then shown again)."""
    box = tick(win, label)
    option = QStyleOptionButton()
    box.initStyleOption(option)
    at = box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box).center()  # (a tick without text)
    QTest.mouseClick(box, Qt.LeftButton, Qt.NoModifier, at)
    QTest.qWait(100)


def ticked(win) -> dict[str, bool]:
    return {label: tick(win, label).isChecked() for label in TICKS}


def y_in(win, widget) -> int:
    """How far down Properties a widget is."""
    return widget.mapTo(win.properties.widget(), QPoint(0, 0)).y()


def under_each_tick(win) -> dict[str, list[str]]:
    """The settings Properties shows under each check's tick, top to bottom, down to the next tick."""
    names = sorted((w for w in win.properties.widget().findChildren(QLabel) if w.isVisible() and w.text()),
                   key=lambda w: y_in(win, w))
    found, current = {}, None
    for name in (w.text() for w in names):
        if name in CHECKS:
            current = found.setdefault(name, [])
        elif current is not None and any(name in settings for settings in CHECKS.values()):
            current.append(name)
    return found


def lines_above_ticks(win) -> list[str]:
    """The checks whose tick has a line drawn above it, below the tick before it."""
    lines = [y_in(win, w) for w in win.properties.widget().findChildren(QFrame)
             if w.frameShape() == QFrame.HLine and w.isVisible()]
    ticks = {label: y_in(win, setting(win, label)[0]) for label in TICKS}
    order = sorted(ticks, key=ticks.get)
    return [label for i, label in enumerate(order)
            if any((ticks[order[i - 1]] if i else -1) < line < ticks[label] for line in lines)]


def row(win, label: str, base=None) -> QRect:
    """The setting `label` of Properties (its name and its field), in a screenshot of the window (or of `base`). An
    expression's box is under its name."""
    names = [w for w in win.properties.widget().findChildren(QLabel) if w.isVisible() and w.text() == label]
    assert len(names) == 1, f"Properties shows {len(names)} settings named {label}"
    if any(form.indexOf(names[0]) >= 0 for form in win.properties.widget().findChildren(QFormLayout)):
        return properties_row(win, label, base)
    column = win.properties.widget().layout()
    under = column.itemAt(column.indexOf(names[0]) + 1).widget()
    return in_window(base or win, names[0]) | in_window(base or win, under)


def if_it_fails_row(win, label: str) -> tuple:
    """The If it fails under a check's tick: its name and its choice."""
    below = y_in(win, setting(win, label)[0])
    found = [(name.widget(), form.itemAt(r, QFormLayout.FieldRole).widget())
             for form in win.properties.widget().findChildren(QFormLayout) for r in range(form.rowCount())
             if (name := form.itemAt(r, QFormLayout.LabelRole)) and name.widget().text() == "If it fails"
             and y_in(win, name.widget()) > below]
    assert found, f"no If it fails under {label}"
    return min(found, key=lambda each: y_in(win, each[0]))


def if_it_fails(win, label: str) -> QComboBox:
    return if_it_fails_row(win, label)[1]


def check_group(win, label: str, base=None) -> QRect:
    """A check in Properties: its tick and, while it is ticked, its settings down to its If it fails, in a screenshot
    of the window (or of `base`)."""
    area = row(win, label, base)
    if tick(win, label).isChecked():
        for name in CHECKS[label][:-1]:
            area |= row(win, name, base)
        for widget in if_it_fails_row(win, label):
            area |= in_window(base or win, widget)
    return area


def error_line(win) -> QLabel:
    """The red line at the top of Properties: what the block still needs."""
    return win.properties.error


def scroll_top(win):
    """Scroll Properties back to its top."""
    win.properties.verticalScrollBar().setValue(0)
    QTest.qWait(50)


# --- the canvas, the Log and Data preview -------------------------------------------------------------------------

def status(tab, block: str):
    return tab.scene.nodes[block].status


def error_of(tab, block: str) -> str:
    return (tab.block_info.get(block) or {}).get("error") or ""


def outputs_of(tab, block: str) -> dict:
    """The tables a block gave in the run the canvas shows: {output: its Parquet file}."""
    return (tab.block_info.get(block) or {}).get("tables") or {}


def check_lines(win, block: str, run) -> list[str]:
    """The Log's lines of a Check Table in `run`, a line per check: its level, then its message."""
    lines, current = [], False
    for e in win.log.entries:
        if e.get("header"):
            current = f"run #{run.run_id} · " in e.get("message", "")
        elif current and e.get("block") == block and e.get("message", "").startswith(tuple(TICKS)):
            lines.append(f"{e.get('level', 'INFO')} {e['message']}")
    return lines


def log_start(win, first: str, last: str) -> QRect:
    """The start of the Log's lines from the one that shows `first` to the one that shows `last`, in the window."""
    box = win.log.text
    top, bottom = span(win, box, first).top(), span(win, box, last).bottom()
    return QRect(in_window(win, box.viewport()).left(), top - 2, CLOSE_UP, bottom - top + 5)


def choose_output(win, name: str):
    """Open the list of outputs next to Data preview's line and click `name` in it, as you would."""
    view, places = theme_list(win.preview.ports)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier,
                     QPoint(view.viewport().width() // 2, places[name].center().y()))  # (its list may be narrow)
    QTest.qWait(100)
    assert win.preview.ports.currentText() == name, f"{name} was not chosen"


def log_line(win, text: str) -> QRect:
    """A line of the Log from its start to the end of `text` in it, in the window (less the margin an outline adds):
    lines of the Log outlined together, with their badge, in a close-up of the start of the Log's lines."""
    found = span(win, win.log.text, text)
    return QRect(QPoint(in_window(win, win.log.text.viewport()).left() + 4, found.top() + 5),
                 QPoint(found.right(), found.bottom() - 5))


def fit_columns(win):
    """Widen Data preview's columns as you would: the first ones to fit (double-clicking their edges), the last one
    dragged to the right side."""
    view = win.preview.view
    header = view.horizontalHeader()
    last = header.count() - 1
    for column in range(last):
        view.resizeColumnToContents(column)
    header.resizeSection(last, view.viewport().width() - sum(header.sectionSize(c) for c in range(last)))
    QTest.qWait(50)


def preview_rows(win) -> list[tuple]:
    """The rows of the table Data preview shows, as it shows them (an empty cell is missing)."""
    model = win.preview.view.model()
    return [tuple(model.data(model.index(r, c)) for c in range(model.columnCount())) for r in range(model.rowCount())]


def preview_start(win) -> QRect:
    """Data preview's table, from the line naming it to its last row, CLOSE_UP wide from its left, in the window."""
    view = win.preview.view
    model = view.model()
    last = view.visualRect(model.index(model.rowCount() - 1, 0))
    area = in_window(win, win.preview.label) | in_window(win, view.viewport(), last)
    return QRect(area.left(), area.top(), CLOSE_UP, area.height())


def as_text(rows_shown: list[tuple]) -> str:
    return "\n".join(" | ".join(cell or "(empty)" for cell in r) for r in rows_shown)


def hover_error(p, qtbot, win, tab, block: str, caption: str):
    """Rest the mouse on the line under `block`, and shoot the error it shows, with the block, close up."""
    tip = hover(qtbot, tab, block)
    p.shot(tip, caption, over=win, highlight=[tip], zoom=[tip, block])
    tip.hide()


def test_check_table_checks_a_table_before_the_flow_uses_it(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Check Table: is the table as expected?", ASKED)
    p.idea("Check Table, the first block of a new Checks group in the Blocks list, checks a table before the flow "
           "uses it, e.g. the one a Read File just read, and passes it on unchanged only when its checks pass. It "
           "has eight checks: the row count; the row count compared with the last successful run; no missing "
           "values; unique keys; columns and their types; values within a range or a list; freshness (the latest "
           "date); and conditions every row must meet.")
    p.idea("Each check is ticked to use it, and its settings show under its tick while it is ticked. Its If it fails "
           "either fails the block (the default) or only warns: a WARNING line in the Log, and the table goes on. A "
           "new Check Table has Row count ticked, at least 1 row: it checks the table is not empty.")
    p.idea("Every ticked check runs, also after one failed. When checks set to fail the block fail, the block fails "
           "with a message naming each of them, how many rows break it and up to 5 example rows, numbered from 1 in "
           "the table's order. For text in a column that is to hold numbers (the surprise you described), it lists "
           "the values that do not read as numbers, with how many rows have each. The Log has a line per check: "
           "passed, failed or warned.")
    p.idea("Its results output is a table with a row per ticked check: check, outcome, rows (how many rows break it) "
           "and details. The block's error output carries results too, so a block that handles the failure knows "
           "which checks failed.")
    p.why_simplest("One block holds every check of a table, each an ordinary setting with a tick. So the flow file, "
                   "{placeholders}, Validate, the Log, the run history and Data preview work for it as for every "
                   "block, and one run of it reports on all of its checks at once.")
    p.why_simplest("The checks are worked out on the table's Parquet file, the file in which every table goes from "
                   "one block to the next, by DuckDB, which comes with Taskloom. The table is never read into memory, "
                   "so tables of millions of rows are fine: the tests check a table of 5,000,000 rows; this proof "
                   "uses a small one, to be read.")
    p.why_simplest("Failing or warning uses what is there: a failed block goes out of the error output every block "
                   "has, and a warning is a WARNING line in the Log. The comparison with the last successful run "
                   "reads the row count the run history already keeps of the table the block passed on: nothing new "
                   "is stored.")
    p.alternative("A block for each check (Check Row Count, Check Unique…)",
                  "Checking one table would take a line of blocks, the table passing through each, and the first "
                  "failure would stop the line: you would learn of one problem per run. One block runs every check "
                  "and reports them all.")
    p.alternative("Writing the checks in a DuckDB SQL or Python Code block that fails",
                  "It works, but every flow would write its own SQL, and the counts, the example rows and the values "
                  "that do not read as numbers would only be there if each one wrote them. Check Table says them the "
                  "same way every time.")
    p.alternative("A data-checking library, such as Great Expectations or pandera",
                  "One more large package to ship and keep up to date, with its own way of writing checks to learn. "
                  "DuckDB is already in Taskloom, and works on the table's file without reading it into memory.")
    p.alternative("Having Read File read a column with N/A as numbers, N/A as missing",
                  "That would hide the very surprise you want to see. The column stays text, as it was written, and "
                  "Columns and types says which values do not read as numbers.")
    p.fits("Checks is a group of its own in the Blocks list, right after Data, with its own colour. Check Table and "
           "Check File, the other extra, show icons from the same set as the other blocks (Tabler's list-check and "
           "file-check), and are in Help > Documentation and the hover help, as every block. Both have an If it "
           "fails for each check, and a results table with a row per check that the error output carries too.")
    p.fits("Its checks are grouped in Properties under lines, as API Request's settings are. Properties and Validate "
           "say what a check still needs, as they do for every block, and refuse the comparison with the last run "
           "inside a Loop.")
    p.fits("Example rows are numbered from 1 in the table's order, as Data preview numbers them, so a row a check "
           "names can be found there.")
    p.fits("Latest at least, in Freshness, is an expression as an If's condition is: today(), days(), bdays() and "
           "the flow parameters, on the flow's clock.")
    win.resizeDocks([win.output], [LOG_HEIGHT], Qt.Vertical)
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    try:
        # --- 1. In the Blocks list -------------------------------------------------------------------------------
        p.step("Look at the Blocks list, on the left: the new group Checks, right after Data.")
        groups = rows(win.palette_panel)
        order = list(groups)
        checks = {item.text(0): item for item in groups.get("Checks", [])}
        tree = win.palette_panel.tree
        tree.scrollToItem(groups["Checks"][-1])
        QApplication.processEvents()
        p.check("The Blocks list has a group Checks right after Data, with Check File and Check Table in it",
                "Checks" in order and order.index("Checks") == order.index("Data") + 1
                and set(checks) == {"Check Table", "Check File"})
        p.check("Check Table and Check File each show an icon",
                all(not checks[name].icon(0).isNull() for name in ("Check Table", "Check File")))
        data_group = [list_row(win, groups["Data"][0].parent()), list_row(win, groups["Data"][-1])]
        checks_group = [list_row(win, groups["Checks"][0].parent()), list_row(win, groups["Checks"][-1])]
        p.shot(win, "① Data, and right after it ② Checks, the new group, with Check File (a page with a tick) and "
                    "Check Table (a list with ticks): each icon white on the Checks indigo.",
               highlight=[data_group, checks_group], badges=True, zoom=[*data_group, *checks_group])

        p.step("Rest the mouse on Check Table in the Blocks list.")
        viewport = tree.viewport()
        at = tree.visualItemRect(checks["Check Table"]).center()
        QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, at, viewport.mapToGlobal(at)))
        qtbot.waitUntil(lambda: tooltip() is not None, timeout=5_000)
        tip = tooltip()
        said = " ".join(plain(tip.text()).split())
        cls = win.registry.get(CHECK)
        p.shot(tip, "Its hover help says what it does: it checks a table before the flow uses it and passes it on "
                    "unchanged only when its checks pass; it names the checks, says each fails the block or only "
                    "warns, and that tables of millions of rows are fine.", over=win, highlight=[tip], zoom=[tip])
        tip.hide()
        p.check("The hover help has the block's name and says what it does",
                said.startswith("Check Table") and " ".join(cls.description.split()) in said)

        # --- 2. A new Check Table in Properties ------------------------------------------------------------------
        p.step("Choose File > New flow, double-click Check Table in the Blocks list, then click the new block.")
        menu_action(win, "&File", "New flow").trigger()
        QTest.qWait(100)
        tab = win.current
        item = checks["Check Table"]
        tree.scrollToItem(item)
        at = tree.visualItemRect(item).center()
        QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, at)  # a double-click: a click, then the
        QTest.mouseDClick(viewport, Qt.LeftButton, Qt.NoModifier, at)  # second one (QTest sends only that)
        QTest.qWait(100)
        added = [b for b, spec in tab.doc.blocks.items() if spec.get("type") == CHECK]
        p.check("A Check Table is added to the new flow", len(added) == 1)
        new_block = added[0]
        click(tab, new_block)
        win.right_tabs.setCurrentWidget(win.properties)
        panel = win.right_tabs
        p.shot(win, f"① {new_block} in Properties: Row count is ticked, at least 1 row, and its If it fails is fail "
                    "the block. ② The seven other checks are only their ticks, each under a line.",
               highlight=[check_group(win, ROW_COUNT), [row(win, label) for label in TICKS[1:]]],
               badges=True, zoom=[check_group(win, ROW_COUNT), row(win, CONDITIONS)])
        p.check("Only Row count is ticked", ticked(win) == {label: label == ROW_COUNT for label in TICKS})
        p.check("Under Row count: At least 1, At most empty, and If it fails: fail the block",
                setting(win, "At least")[1].text() == "1" and setting(win, "At most")[1].text() == ""
                and if_it_fails(win, ROW_COUNT).currentText() == "fail the block")
        p.check("The other checks show only their ticks: no setting is under them",
                under_each_tick(win) == {label: CHECKS[label] if label == ROW_COUNT else [] for label in TICKS})
        p.check("A line is drawn above each of the eight checks", lines_above_ticks(win) == TICKS)
        p.check("The red line at the top of Properties says nothing: the block has what it needs",
                error_line(win).isHidden())

        p.step("Click Row count's tick, to untick it.")
        click_tick(win, ROW_COUNT)
        p.shot(win, "① With no check ticked, the red line at the top of Properties says to tick at least one, naming "
                    "the eight.", highlight=[error_line(win)], badges=True, zoom=[error_line(win)])
        p.check("Properties says: “tick at least one check: Row count, Row count compared with the last successful "
                "run, No missing values, Unique, Columns and types, Values within a range or a list, Freshness, "
                "Conditions every row must meet”",
                error_line(win).isVisible()
                and error_line(win).text() == "tick at least one check: " + ", ".join(TICKS))

        p.step("Click each check's tick, from Row count to Conditions every row must meet.")
        for label in TICKS:
            click_tick(win, label)
        scroll_top(win)
        shown = under_each_tick(win)
        p.shot(panel, "Each check's settings now show under its tick. ① The red line says what the checks still "
                      "need: Unique a column, Columns and types and Values a row, Freshness its Date column, "
                      "Conditions a condition. ② Row count, with At least, At most and If it fails.",
               highlight=[error_line(win), check_group(win, ROW_COUNT, panel)], badges=True)
        p.check("All eight checks are ticked", all(ticked(win).values()))
        p.check("The red line says: “" + "”, “".join(NEEDS) + "”",
                error_line(win).isVisible() and error_line(win).text().splitlines() == NEEDS)
        p.check("Under Row count: At least, At most, If it fails", shown[ROW_COUNT] == CHECKS[ROW_COUNT])
        for labels in ((COMPARED, MISSING, UNIQUE), (TYPES, VALUES), (FRESHNESS, CONDITIONS)):
            scroll_to(win, labels[0])
            p.shot(panel, "Properties, scrolled down: " + "; ".join(
                       f"{chr(0x2460 + i)} {label}, with {', '.join(CHECKS[label][:-1])} and If it fails"
                       for i, label in enumerate(labels)) + ". A line is above each.",
                   highlight=[check_group(win, label, panel) for label in labels], badges=True)
            for label in labels:
                p.check(f"Under {label}: {', '.join(CHECKS[label])}", shown[label] == CHECKS[label])
        choices = [if_it_fails(win, label) for label in TICKS]
        p.check("Each of the eight If it fails offers fail the block or warn, and says fail the block",
                all([box.itemText(i) for i in range(box.count())] == ["fail the block", "warn"]
                    and box.currentText() == "fail the block" for box in choices))
        p.check("A line is still drawn above each check", lines_above_ticks(win) == TICKS)
        clean(win)

        # --- 3. The orders, checked --------------------------------------------------------------------------------
        folder = tmp_path / "flows"
        folder.mkdir()
        data = write_orders(folder / "orders.csv", arrived())
        p.step("Look at orders.csv, the orders as they arrive.")
        p.text("orders.csv", data.read_text(encoding="utf-8"))
        p.note(f"The orders are made up for this proof. In amount, rows 2 and 9 have N/A and row 4 has 1,234.5 (with a "
               f"comma); row 5 has no amount; row 6 repeats order 1003 (row 3); row 7's region is central; row 8's "
               f"amount is -15. The days are from {day(6)} to {day(3)}: 6 to 3 days before the proof ran.")

        p.step("Open the flow orders.yaml (File > Open…), in the same folder. load_orders reads orders.csv; "
               "check_orders checks it, then totals adds up the amounts per region; check_orders' error output goes "
               "to on_failed_checks, a Python Code block that gives the results it carries.")
        tab = orders_tab = open_flow(win, monkeypatch, orders_flow(folder))
        click(tab, "check_orders")
        blocks = ["start_by_hand", "load_orders", "check_orders", "totals", "on_failed_checks"]
        p.shot(win, "The flow: ① load_orders, ② check_orders, ③ totals after its table output, ④ on_failed_checks "
                    "after its error output.",
               highlight=blocks[1:], badges=True, zoom=blocks)
        scroll_top(win)
        p.shot(panel, "check_orders' Properties: ① Row count, at least 1, set to fail the block; ② No missing values "
                      "(no column listed: every column) set to warn; ③ Unique, on order_id, set to fail the block.",
               highlight=[check_group(win, label, panel) for label in (ROW_COUNT, MISSING, UNIQUE)], badges=True)
        scroll_to(win, TYPES)
        p.shot(panel, "Further down: ① Columns and types, amount: float, set to fail the block; ② Values within a "
                      "range or a list, region=north, south, east, west, set to warn; ③ Conditions every row must "
                      "meet, amount >= 0, set to fail the block.",
               highlight=[check_group(win, label, panel) for label in (TYPES, VALUES, CONDITIONS)], badges=True)
        p.check("check_orders has six checks ticked: Row count, Unique, Columns and types and Conditions every row "
                "must meet set to fail the block; No missing values and Values within a range or a list set to warn",
                {label: if_it_fails(win, label).currentText() for label, on in ticked(win).items() if on}
                == {ROW_COUNT: "fail the block", MISSING: "warn", UNIQUE: "fail the block", TYPES: "fail the block",
                    VALUES: "warn", CONDITIONS: "fail the block"})

        p.step("Press ▶ Run now.")
        run = run_now(win, qtbot, tab)
        error = error_of(tab, "check_orders")
        p.shot(win, "① check_orders failed. ② totals, after its table output, was skipped: the table was not passed "
                    "on. ③ on_failed_checks ran with what check_orders' error output carried, so the run itself "
                    "succeeded: the failure was handled.",
               highlight=["check_orders", "totals", "on_failed_checks"], badges=True, zoom=blocks[1:])
        p.check("check_orders failed, totals was skipped, on_failed_checks ran, and the run succeeded",
                (status(tab, "check_orders"), status(tab, "totals"), status(tab, "on_failed_checks"), run.status)
                == ("failed", "skipped", "success", "success"))
        unique_failed = "Unique: 1 value of order_id repeats (2 rows): order_id 1003 in 2 rows: rows 3, 6"
        types_failed = ("Columns and types: amount is text, and 3 rows have a value that does not read as float: "
                        "'N/A' (2 rows, the first row 2), '1,234.5' (1 row, the first row 4)")
        not_checked = "Conditions every row must meet: 'amount >= 0' is not a condition DuckDB can check on this table:"
        lines = error.splitlines()
        p.check(f"The error names Unique, with how many rows repeat a value and their row numbers, counted from 1: "
                f"“{unique_failed}”", unique_failed in lines)
        p.check(f"It names Columns and types, with the text values that do not read as numbers and how many rows have "
                f"each: “{types_failed}”", types_failed in lines)
        p.check("It names Conditions every row must meet, which could not be checked: amount is text here, and "
                "DuckDB cannot compare text with 0",
                len(lines) == 3 and lines[2].startswith(not_checked) and "VARCHAR" in lines[2])
        p.check("It does not name the checks set to warn, nor Row count, which passed",
                not any(name in error for name in (MISSING, VALUES, f"{ROW_COUNT}:")))
        p.note("amount has text in it, so Read File reads the whole column as text, and DuckDB says it cannot compare "
               "text (VARCHAR, in its words) with the number 0. A check that cannot be checked as it is set up fails "
               "the block, also when it is set to warn, as nothing was checked. Columns and types says why: the "
               "values that do not read as numbers.")

        p.step("Rest the mouse on the line under check_orders.")
        tip = hover(qtbot, tab, "check_orders")
        p.shot(tip, "Its whole error, a line per failed check: Unique, Columns and types, and Conditions every row "
                    "must meet (written out above).")
        tip.hide()
        p.text("check_orders' error", error)

        p.step("Click check_orders: the Log, under the canvas, shows only its lines.")
        click(tab, "check_orders")
        click_bottom_tab(win, win.log)
        said = check_lines(win, "check_orders", run)
        p.shot(win, "① The Log has a line per check: Row count passed (INFO); the two checks set to warn have "
                    "WARNING lines; the three that failed have ERROR lines, which say what the error says. The "
                    "close-up shows the start of each line; those that passed or warned are written out above.",
               highlight=[[log_line(win, f"{label}: {outcome}") for label, outcome in (
                   (ROW_COUNT, "passed"), (MISSING, "warned"), (UNIQUE, "failed"), (TYPES, "failed"),
                   (VALUES, "warned"), (CONDITIONS, "failed"))]],
               badges=True, zoom=[log_start(win, f"{ROW_COUNT}: passed", f"{CONDITIONS}: failed")])
        p.text("check_orders' lines in the Log that passed or warned",
               "\n".join(line for line in said if not line.startswith("ERROR ")))
        p.check("The Log says “Row count: passed: 10 rows (at least 1)”",
                f"INFO {ROW_COUNT}: passed: 10 rows (at least 1)" in said)
        p.check("A WARNING line: “No missing values: warned: amount: 1 missing (at most 0 allowed): row 5 (amount "
                "missing)”",
                f"WARNING {MISSING}: warned: amount: 1 missing (at most 0 allowed): row 5 (amount missing)" in said)
        p.check("A WARNING line: “Values within a range or a list: warned: region: 1 row not one of north, south, "
                "east, west: row 7 (region 'central')”",
                f"WARNING {VALUES}: warned: region: 1 row not one of north, south, east, west: row 7 (region "
                "'central')" in said)
        p.check("An ERROR line for each of the three that failed, with what the error says of it",
                [line for line in said if line.startswith("ERROR ")] == [
                    "ERROR " + line.replace(": ", ": failed: ", 1) for line in lines])

        # --- 4. The error output: results ----------------------------------------------------------------------
        p.step("Click on_failed_checks, then Data preview, under the canvas.")
        click(tab, "on_failed_checks")
        click_bottom_tab(win, win.preview)
        fit_columns(win)
        got = preview_rows(win)
        p.shot(win, "① on_failed_checks' output: the results check_orders' error output carried, a row per ticked "
                    "check: its check, its outcome (passed, warned or failed), rows (how many rows break it) and its "
                    "details, as the Log said them.",
               highlight=[[in_window(win, win.preview.label), in_window(win, win.preview.view)]], badges=True,
               zoom=[preview_start(win)])
        p.note("The proof widens the columns of Data preview, as you would by double-clicking the edges of their "
               "names, and by dragging the last one's edge to the right.")
        p.check("Data preview shows a table with the columns check, outcome, rows and details",
                preview_columns(win) == ["check", "outcome", "rows", "details"])
        p.check("A row per ticked check: Row count passed; No missing values warned (1 row); Unique failed (2 rows); "
                "Columns and types failed (3 rows); Values within a range or a list warned (1 row); Conditions every "
                "row must meet failed (no rows: it could not be checked)",
                [r[:3] for r in got] == [(ROW_COUNT, "passed", ""), (MISSING, "warned", "1"), (UNIQUE, "failed", "2"),
                                         (TYPES, "failed", "3"), (VALUES, "warned", "1"), (CONDITIONS, "failed", "")])

        # --- 5. Fixing the data ---------------------------------------------------------------------------------
        p.step("Fix the amounts that were text (N/A in rows 2 and 9, 1,234.5 in row 4) and the repeated order (row "
               "6 is order 1006), then press ▶ Run now.")
        write_orders(data, first_fix())
        run = run_now(win, qtbot, tab)
        error = error_of(tab, "check_orders")
        click(tab, "check_orders")
        breaks = f"{CONDITIONS}: amount >= 0: 2 rows break it: row 5 (amount missing); row 8 (amount -15.0)"
        hover_error(p, qtbot, win, tab, "check_orders",
                    "check_orders still fails, on one check only: amount is read as numbers now, so amount >= 0 is "
                    "checked, and 2 rows break it: row 5, whose amount is missing, and row 8, -15.")
        p.check(f"check_orders fails with “{breaks}”", status(tab, "check_orders") == "failed" and error == breaks)
        p.check("Columns and types and Unique passed",
                [line for line in check_lines(win, "check_orders", run) if line.startswith(("INFO " + UNIQUE,
                                                                                            "INFO " + TYPES))]
                == [f"INFO {UNIQUE}: passed: no value of order_id repeats",
                    f"INFO {TYPES}: passed: amount is float"])
        p.note("A missing amount breaks amount >= 0 too: it is not true of that row. Its help says so: to allow a "
               "missing amount, write amount is null or amount >= 0.")

        p.step("Fix the two amounts (row 5's is 64, row 8's 15), then press ▶ Run now.")
        write_orders(data, second_fix())
        run = run_now(win, qtbot, tab)
        p.shot(win, "① check_orders succeeded and passed the table on: ② totals ran. on_failed_checks did not: "
                    "nothing reached its error input.", highlight=["check_orders", "totals"], badges=True,
               zoom=blocks[1:])
        p.check("check_orders and totals succeeded", (status(tab, "check_orders"), status(tab, "totals"))
                == ("success", "success"))
        given = pl.read_parquet(outputs_of(tab, "load_orders")["table"])
        passed_on = pl.read_parquet(outputs_of(tab, "check_orders")["table"])
        p.check("check_orders passed on the table load_orders read, unchanged: the same 10 rows, columns and types",
                passed_on.height == 10 and passed_on.schema == given.schema and passed_on.equals(given))

        p.step("Click check_orders, then Data preview, and choose its results output in the list next to its name.")
        click(tab, "check_orders")
        click_bottom_tab(win, win.preview)
        choose_output(win, "results")
        fit_columns(win)
        got = preview_rows(win)
        p.shot(win, "① check_orders' results: five checks passed; Values within a range or a list only warned, "
                    "about row 7's region, central, so the table went on.",
               highlight=[[in_window(win, win.preview.label), in_window(win, win.preview.view)]], badges=True,
               zoom=[preview_start(win)])
        p.text("check_orders' results: check | outcome | rows | details", as_text(got))
        p.check("results: Row count, No missing values, Unique, Columns and types and Conditions every row must meet "
                "passed; Values within a range or a list warned (1 row)",
                [r[:3] for r in got] == [(ROW_COUNT, "passed", ""), (MISSING, "passed", "0"), (UNIQUE, "passed", "0"),
                                         (TYPES, "passed", "0"), (VALUES, "warned", "1"), (CONDITIONS, "passed", "0")])

        # --- 6. The row count compared with the last successful run --------------------------------------------
        daily = write_orders(folder / "daily.csv", second_fix())
        p.step("Open the flow daily-orders.yaml: load reads daily.csv, 10 orders; check_daily has only Row count "
               "compared with the last successful run ticked, Within % 30 (as it comes). Press ▶ Run now.")
        tab = open_flow(win, monkeypatch, one_check_flow(folder, "daily-orders.yaml", "daily.csv", "check_daily",
                                                         compared=True))
        run = run_now(win, qtbot, tab)
        first_daily = run.run_id
        click(tab, "check_daily")
        click_bottom_tab(win, win.log)
        said = check_lines(win, "check_daily", run)
        p.shot(win, "① check_daily succeeded: this flow has no earlier run in which it succeeded, so there is nothing "
                    "to compare with yet, as ② the Log says (written out above). ③ Within %: 30.",
               highlight=["check_daily", (win.log.text, f"{COMPARED}: passed"), row(win, "Within %")],
               badges=True, zoom=["check_daily", row(win, "Within %")])
        p.text("check_daily's line in the Log", "\n".join(said))
        p.check(f"check_daily succeeded, and the Log says “{COMPARED}: passed: 10 rows; no earlier run of this flow in "
                "which check_daily succeeded: nothing to compare with yet”",
                status(tab, "check_daily") == "success" and said == [
                    f"INFO {COMPARED}: passed: 10 rows; no earlier run of this flow in which check_daily succeeded: "
                    "nothing to compare with yet"])

        p.step("Keep only the first 5 orders in daily.csv, press ▶ Run now, and rest the mouse on the line under "
               "check_daily.")
        write_orders(daily, second_fix()[:5])
        run = run_now(win, qtbot, tab)
        error = error_of(tab, "check_daily")
        fewer = f"{COMPARED}: 5 rows, 50% fewer than the 10 rows of run #{first_daily} (within 30%)"
        hover_error(p, qtbot, win, tab, "check_daily",
                    f"check_daily failed: 5 rows is 50% fewer than the 10 rows of run #{first_daily}, the last run of "
                    "this flow in which it succeeded; within 30% was allowed.")
        p.check(f"check_daily fails with “{fewer}”: it names the earlier run and its row count",
                status(tab, "check_daily") == "failed" and error == fewer)

        # --- 7. Freshness -------------------------------------------------------------------------------------
        p.step("Open the flow fresh-orders.yaml: load reads orders.csv; check_fresh has only Freshness ticked, Date "
               "column day, Latest at least today() - days(1) (as it comes). Press ▶ Run now, and rest the mouse "
               "on the line under check_fresh.")
        p.note("Read File reads the days of a CSV file as text, as they are written. Freshness, and a range of dates "
               "in Values within a range or a list, read text written YYYY-MM-DD as dates.")
        tab = open_flow(win, monkeypatch, one_check_flow(folder, "fresh-orders.yaml", "orders.csv", "check_fresh",
                                                         freshness=True, fresh_column="day"))
        run = run_now(win, qtbot, tab)
        click(tab, "check_fresh")
        error = error_of(tab, "check_fresh")
        stale = f"{FRESHNESS}: the latest day is {day(3)}, before {day(1)} (today() - days(1))"
        hover_error(p, qtbot, win, tab, "check_fresh",
                    f"check_fresh failed: the latest day found is {day(3)}, before {day(1)}, the day today() - "
                    "days(1) gives (yesterday).")
        p.check(f"check_fresh fails with “{stale}”: the latest day found, and the one expected",
                status(tab, "check_fresh") == "failed" and error == stale)

        p.step("Add two orders of today to orders.csv, and press ▶ Run now.")
        write_orders(data, second_fix() + [(1011, "south", "22", day(0)), (1012, "west", "35", day(0))])
        run = run_now(win, qtbot, tab)
        said = check_lines(win, "check_fresh", run)
        p.shot(win, f"① check_fresh succeeded: the latest day is today, {day(0)}, as ② the Log says (written out "
                    "above).",
               highlight=["check_fresh", (win.log.text, f"{FRESHNESS}: passed")], badges=True,
               zoom=[log_start(win, f"run #{run.run_id} · ", f"DONE run #{run.run_id} ")])
        p.text("check_fresh's line in the Log", "\n".join(said))
        p.check(f"check_fresh succeeded, and the Log says “{FRESHNESS}: passed: the latest day is {day(0)}, at or "
                f"after {day(1)} (today() - days(1))”",
                status(tab, "check_fresh") == "success" and said == [
                    f"INFO {FRESHNESS}: passed: the latest day is {day(0)}, at or after {day(1)} (today() - days(1))"])

        # --- 8. Inside a Loop -----------------------------------------------------------------------------------
        p.step("Open the flow each-region.yaml and click check_region: a Check Table inside the Loop each_region, "
               "with Row count compared with the last successful run ticked.")
        tab = open_flow(win, monkeypatch, loop_flow(folder))
        click(tab, "check_region")
        refused = (f"{COMPARED}: not inside a Loop: each_region runs this block once per item, and its items have no "
                   "single earlier row count to compare with (untick it, or check the table outside the Loop)")
        p.shot(win, "① The Loop each_region runs load and ② check_region once per region.",
               highlight=["each_region", "check_region"], badges=True, zoom=["regions", "each_region"])
        p.shot(win, "① Properties says why the comparison cannot be: the Loop runs check_region once per item, and its "
                    "items have no single earlier row count to compare with.",
               highlight=[error_line(win)], badges=True, zoom=[error_line(win)])
        p.check(f"Properties says: “{refused}”", error_line(win).isVisible() and error_line(win).text() == refused)

        p.step("Press Validate in the toolbar (F7).")
        said = boxes_said(p, monkeypatch, lambda: press(win, win.validate_action),
                          "Validate refuses the flow, saying the same of check_region.")
        p.check("Validate refuses the flow, naming check_region and saying why",
                len(said) == 1 and f"check_region: {refused}" in said[0])
        p.check("No run was started", tab.runs == [])

        # --- 9. Dark ------------------------------------------------------------------------------------------
        p.step("Choose Tools > Settings…, set Theme to Dark and press Save. Then click the tab orders.yaml and "
               "check_orders.")
        set_theme(p, win, "Dark", shoot=False)
        win.tabs.setCurrentIndex(win.tabs.indexOf(orders_tab))
        QTest.qWait(100)
        click(orders_tab, "check_orders")
        scroll_top(win)
        p.shot(win, "In Dark: ① check_orders' Properties, its checks under their lines, the ticks and the choices "
                    "light on dark.", highlight=[win.properties], badges=True,
               zoom=[check_group(win, ROW_COUNT), check_group(win, UNIQUE)])
        p.check("The editor is dark", QApplication.palette().window().color().lightness() < 128)
        off = not_in_theme(win.properties, dark=True)
        p.check("Every part of Properties has a dark background, its text standing out from it"
                + (f" (not: {'; '.join(off)})" if off else ""), off == [])

        p.step("Set Theme back to System (Tools > Settings…) and press Save.")
        set_theme(p, win, "System", shoot=False)
        p.check("The Theme is System again, and the editor light",
                theme.chosen(win.settings) == "System" and QApplication.palette().window().color().lightness() >= 128)
    finally:  # no run left going; the Theme and the application's look as the proof found them
        for each in win.tabs_list():
            for running in each.runs:
                if running.running:
                    running.proc.kill()
                    running.proc.waitForFinished(5000)
        win.settings.setValue("theme", "System")
        theme.apply("System")
        if app.style().name() != as_found[0]:
            QApplication.setStyle(as_found[0])
        QApplication.setPalette(as_found[1])
        QToolTip.setPalette(as_found[2])
        QApplication.processEvents()
        clean(win)
