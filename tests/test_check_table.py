"""The Check Table block (checks.check_table), the first block of a new Checks category: it checks a table and passes
it on unchanged only when its checks pass. Each check has a tick (only a ticked check runs) and its own If it fails
(fail the block, the default, or warn in the Log): the row count; the row count compared with the last successful
run; no missing values; unique; columns and types; values within a range or a list; freshness; conditions every row
must meet. Through the real `taskloom run` and `taskloom validate` (the table read by Read File, as a flow does), the
run history (history.db), what the blocks after it receive (written down by Python Code blocks), the offscreen
editor (the Blocks list, the canvas, Properties), README.md and Help > Documentation. Each test names the point of
the specification (1-8) it protects.

What the tests take as given (the specification leaves it to the change):
- Each check's tick is one of the block's true/false settings that are always shown (a fields.Bool without only_if):
  there are 8, one per check, and no other. A tick is told by the words of its label, as the specification names the
  checks: "last" (or previous, earlier, compared) for the row count compared with the last successful run, "missing",
  "unique", "type(s)", "range" (Values within a range or a list), "fresh(ness)", "condition(s)"; the one left (with
  row, rows or count) is the row count.
- A check's other settings are shown only while it is ticked (only_if {its tick: [True]}). One of them is its If it
  fails: a choice whose default fails the block (it has "fail" in it, not "warn") and that has one option with "warn"
  in it. The others: Row count's at least and at most (the words least/min/minimum and most/max/maximum of their
  labels), given as whole numbers; the comparison's percentage (its one other setting), given as a number; No
  missing values' columns (a list, fields.List), its Count empty text as missing (a tick, fields.Bool) and what it
  allows (the one left), given as text ("3" or "25%"); Unique's columns (a list); the rows of Columns and types and of
  Values (fields.KeyValues, the column as the key: {key: amount, value: float}, {key: amount, value: "0..50"});
  Freshness's column (its label has "column") and its expression (the other one); the conditions (a list).
- The `check` of a results row, and a check's line in the Log, name the check: with its tick's label (the longest
  one they have), or else with those words (e.g. Row count, No missing values, Unique).
- The type a column has is said with the type names the check takes (text, int, float, bool, date, datetime).
- Properties shows a tick as it shows a true/false setting (a row with its label and a check box, or a check box
  with the label as its text), the other settings as rows labelled as block.setting_label names them, and the line
  above each check as API Request's groups have one (a QFrame drawn as a horizontal line).
- The icon is Tabler's "list-check" (outline), the specification's example.
- README describes the block in a section under a heading naming Check Table (as API Request and Session have
  theirs), with the YAML example in it.
- A number in a message may have thousands separators (50,026)."""

import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter
from functools import cache
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QLineEdit  # noqa: E402

from taskloom.block import applies, fields, setting_label  # noqa: E402
from taskloom.editor.canvas import CATEGORY_COLORS, USER_COLOR  # noqa: E402
from taskloom.flow import BlockSpec, block_config  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_block_icons import NOTES, canvas_icons, drawing, row, unlike  # noqa: E402
from tests.test_block_icons import rows as list_rows  # noqa: E402
from tests.test_editor import add_from_palette  # noqa: E402
from tests.test_paths_editor_editing import (clean, error_shown, field, flow_file, save_as, saved,  # noqa: E402
                                             select_only, settle, type_in)

REPO = Path(__file__).resolve().parents[1]
CHECK = "checks.check_table"
NAMES = ("row_count", "compared", "missing", "unique", "types", "values", "freshness", "conditions")
# Which check a text names, by its words: the first of these with one of them (the row count: none of the others).
FOUND_BY = (("compared", {"last", "previous", "earlier", "compared"}), ("missing", {"missing"}),
            ("unique", {"unique"}), ("types", {"type", "types"}), ("values", {"range"}),
            ("freshness", {"fresh", "freshness"}), ("conditions", {"condition", "conditions"}),
            ("row_count", {"row", "rows", "count"}))
OUTCOMES = {"passed", "failed", "warned"}
# Tabler Icons' "list-check" (outline), as in its SVG: three ticks, each with a line right of it.
TABLER_LIST_CHECK = ('<path d="M3.5 5.5l1.5 1.5l2.5 -2.5"/><path d="M3.5 11.5l1.5 1.5l2.5 -2.5"/>'
                     '<path d="M3.5 17.5l1.5 1.5l2.5 -2.5"/><path d="M11 6l9 0"/><path d="M11 12l9 0"/>'
                     '<path d="M11 18l9 0"/>')
# Tabler Icons' "list" (outline): a list without ticks.
TABLER_LIST = ('<path d="M9 6l11 0"/><path d="M9 12l11 0"/><path d="M9 18l11 0"/><path d="M5 6l0 .01"/>'
               '<path d="M5 12l0 .01"/><path d="M5 18l0 .01"/>')


# --- the block, found by what Properties calls its settings -------------------------------------------------------------

def words(text) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def which(text) -> str | None:
    """The check a text names (a tick's label, a results row's check, a Log line), by its words."""
    found = words(text)
    return next((name for name, by in FOUND_BY if found & by), None)


@cache
def check_class():
    cls = discover(None).get(CHECK)
    assert cls is not None, f"there is no {CHECK} block (Check Table)"
    return cls


def label_of(key: str) -> str:
    return setting_label(key, check_class().config[key])


def named_check(text) -> str | None:
    """The check a text (a results row's check, a Log line) is about: the one whose tick's label it has (the longest:
    "Row count" is in "Row count compared with ..."), or else by its words."""
    labels = {label_of(key).lower(): name for name, key in ticks().items()}
    found = [label for label in labels if label in str(text).lower()]
    return labels[max(found, key=len)] if found else which(text)


@cache
def ticks() -> dict[str, str]:
    """Each check's tick: {check: its key in the flow file}."""
    cls, found = check_class(), {}
    for key, f in cls.config.items():
        if isinstance(f, fields.Bool) and not f.only_if:
            name = which(label_of(key))
            assert name is not None and name not in found, f"which check is the tick {label_of(key)!r}? ({found})"
            found[name] = key
    assert set(found) == set(NAMES), f"Check Table's ticks: {[label_of(k) for k in found.values()]}"
    return found


@cache
def settings_of(name: str) -> tuple[str, ...]:
    """A check's settings but its tick: those shown only while it is ticked."""
    cls, tick = check_class(), ticks()[name]
    off = {t: False for t in ticks().values()}
    found = tuple(k for k in cls.config
                  if k != tick and applies(cls, k, {**off, tick: True}) and not applies(cls, k, off))
    assert found, f"{label_of(tick)} has no settings shown only while it is ticked"
    return found


@cache
def if_it_fails(name: str) -> tuple[str, str]:
    """A check's If it fails: (its key, its option that only warns). Its default fails the block."""
    cls = check_class()
    found = [k for k in settings_of(name) if isinstance(cls.config[k], fields.Choice)
             and any("warn" in str(o).lower() for o in cls.config[k].options)]
    assert len(found) == 1, (f"{label_of(ticks()[name])}: which setting is its If it fails? "
                             f"{[label_of(k) for k in settings_of(name)]}")
    f = cls.config[found[0]]
    warn = [o for o in f.options if "warn" in str(o).lower()]
    assert len(warn) == 1, f.options
    assert "fail" in str(f.default).lower() and "warn" not in str(f.default).lower(), (
        f"{label_of(ticks()[name])}: If it fails should fail the block unless you choose to warn: {f.default!r}")
    return found[0], warn[0]


def own(name: str) -> list[str]:
    """A check's settings but its tick and its If it fails."""
    return [k for k in settings_of(name) if k != if_it_fails(name)[0]]


def labelled(name: str, *wanted: str) -> str:
    """The one setting of the check `name` (not its If it fails) whose label has one of these words."""
    found = [k for k in own(name) if words(label_of(k)) & set(wanted)]
    assert len(found) == 1, (f"{label_of(ticks()[name])}: one setting whose label has one of {wanted}: "
                             f"{[label_of(k) for k in own(name)]}")
    return found[0]


def only(name: str, but=()) -> str:
    """The one setting of the check `name` but its If it fails and `but`."""
    found = [k for k in own(name) if k not in but]
    assert len(found) == 1, f"{label_of(ticks()[name])}: one more setting: {[label_of(k) for k in found]}"
    return found[0]


def of_type(name: str, kind) -> str:
    found = [k for k in own(name) if isinstance(check_class().config[k], kind)]
    assert len(found) == 1, (f"{label_of(ticks()[name])}: one {kind.__name__} setting: "
                             f"{[label_of(k) for k in own(name)]}")
    return found[0]


# --- the checks, as the flow file writes them ---------------------------------------------------------------------------

def ticked(name: str, config: dict, warn: bool) -> dict:
    config = {ticks()[name]: True, **config}
    if warn:
        key, option = if_it_fails(name)
        config[key] = option
    return config


def row_count(at_least=None, at_most=None, warn=False) -> dict:
    config = {}
    if at_least is not None:
        config[labelled("row_count", "least", "min", "minimum", "fewest")] = at_least
    if at_most is not None:
        config[labelled("row_count", "most", "max", "maximum")] = at_most
    return ticked("row_count", config, warn)


def compared_with_last_run(percent=None, warn=False) -> dict:
    return ticked("compared", {} if percent is None else {only("compared"): percent}, warn)


def no_missing(columns=None, allowed=None, empty_text=None, warn=False) -> dict:
    columns_key, empty_key = of_type("missing", fields.List), of_type("missing", fields.Bool)
    config = {}
    if columns is not None:
        config[columns_key] = list(columns)
    if empty_text is not None:
        config[empty_key] = empty_text
    if allowed is not None:
        config[only("missing", but=(columns_key, empty_key))] = allowed
    return ticked("missing", config, warn)


def unique(*columns, warn=False) -> dict:
    return ticked("unique", {only("unique"): list(columns)} if columns else {}, warn)


def key_values(pairs: dict) -> list[dict]:
    return [{"key": k, "value": v} for k, v in pairs.items()]


def columns_and_types(warn=False, **expected) -> dict:
    """Column: type rows, e.g. amount="float" (an empty type: only that it is there)."""
    return ticked("types", {only("types"): key_values(expected)} if expected else {}, warn)


def within(warn=False, **allowed) -> dict:
    """Column=min..max or Column=a, b, c rows, e.g. amount="0..50", region="north, south"."""
    return ticked("values", {only("values"): key_values(allowed)} if allowed else {}, warn)


def fresh(column=None, expression=None, warn=False) -> dict:
    column_key = labelled("freshness", "column")
    config = {}
    if column is not None:
        config[column_key] = column
    if expression is not None:
        config[only("freshness", but=(column_key,))] = expression
    return ticked("freshness", config, warn)


def every_row(*sql, warn=False) -> dict:
    return ticked("conditions", {only("conditions"): list(sql)} if sql else {}, warn)


def check_table(*parts: dict, x=0, y=0) -> dict:
    """A Check Table whose ticked checks are `parts` (each from the functions above); the others are unticked."""
    config = {key: False for key in ticks().values()}
    for part in parts:
        config.update(part)
    return {"type": CHECK, "config": config, "ui": {"x": x, "y": y}}


# --- the blocks around it, the flow and the run ---------------------------------------------------------------------

def read_file(path: Path, x=-700, y=0) -> dict:
    return {"type": "data.read_file", "config": {"path": path.as_posix()}, "ui": {"x": x, "y": y}}


def write_file(path: Path, x=700, y=0) -> dict:
    return {"type": "data.write_file", "config": {"path": path.as_posix()}, "ui": {"x": x, "y": y}}


KEEP = '''\
import json


def plain(value):
    if isinstance(value, Table):
        frame = value.scan().collect()
        return {"columns": frame.columns, "rows": frame.to_dicts()}
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


with open(%r, "w", encoding="utf-8") as f:
    json.dump(plain(input), f, default=str)
'''


def keep(filename: str, x=700, y=0) -> dict:
    """A Python Code block (in Taskloom's Python) that writes what reaches it to `filename` as JSON, a table as
    {"columns": [...], "rows": [{column: value}, ...]}."""
    return {"type": "logic.python", "config": {"inputs": ["input"], "outputs": [], "code": KEEP % filename},
            "ui": {"x": x, "y": y}}


def write_csv(path: Path, header: str, rows) -> Path:
    """A CSV file: `header`, then a line per row (a tuple); None is an empty field (read as missing), "" an empty
    text (written quoted, as is a text with spaces around it, a comma or a quote)."""
    def cell(value):
        if value is None:
            return ""
        text = str(value)
        if text == "" or text != text.strip() or any(c in text for c in ',"'):
            return '"' + text.replace('"', '""') + '"'
        return text

    lines = [header, *(",".join(cell(v) for v in r) for r in rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return path


ORDERS = [(1001, "2026-11-01", "north", 12.5), (1002, "2026-11-01", "south", 40), (1003, "2026-11-02", "north", None),
          (1004, "2026-11-02", "east", 7.25), (1005, "2026-11-03", "south", 100), (1006, "2026-11-03", "north", None),
          (1007, "2026-11-04", "south", 3), (1008, "2026-11-04", "north", 18), (1009, "2026-11-05", "south", 0),
          (1010, "2026-11-05", "north", 55.5)]


def orders(folder: Path) -> Path:
    """10 orders: amount missing in rows 3 and 6, region east in row 4, the others north or south."""
    return write_csv(folder / "orders.csv", "order_id,day,region,amount", ORDERS)


def checking(env, source: dict, checks: dict, name="flow", port="table", blocks=None, edges=(), **top) -> Path:
    """A flow file: `source` (named load; its output `port` gives the table) into each Check Table of `checks`
    ({name: block}); what one's results output gives is written to <name>.passed.json and what its error output gives
    to <name>.failed.json (both removed first). `blocks`, `edges`: more of them; `top`: e.g. params."""
    found, connected = {"load": source}, []
    for i, (block, spec) in enumerate(checks.items()):
        found[block] = {**spec, "ui": {"x": 0, "y": 320 * i}}
        found[f"{block}_passed"] = keep(f"{block}.passed.json", y=320 * i)
        found[f"{block}_failed"] = keep(f"{block}.failed.json", y=320 * i + 150)
        connected += [f"load.{port} -> {block}.table", f"{block}.results -> {block}_passed.input",
                      f"{block}.error -> {block}_failed.input"]
        for end in ("passed", "failed"):
            (env.root / f"{block}.{end}.json").unlink(missing_ok=True)
    return flow_file(env.root / f"{name}.yaml", {**found, **(blocks or {})}, [*connected, *edges], **top)


def run_path(env, path: Path, as_of=None):
    """`taskloom run` of a flow file: its result, the run's number (None: no run began) and each block's status."""
    result = env.taskloom("run", path, *(("--as-of", as_of) if as_of else ()))
    found = env.query("SELECT id FROM runs ORDER BY id DESC LIMIT 1") if (env.home / "history.db").exists() else []
    run_id = found[0][0] if found else None
    return result, run_id, (env.block_status(run_id) if run_id is not None else {})


def run_flow(env, blocks: dict, edges=(), name="flow", **top):
    return run_path(env, flow_file(env.root / f"{name}.yaml", blocks, edges, **top))


def said(result) -> str:
    return result.stdout[-3000:] + result.stderr[-3000:]


def outcome(env, block: str) -> dict:
    """How the Check Table `block` ended, from what reached the blocks after it: {"ended": "success" (its results
    output came) or "failed" (its error output came), "results": its results table, "error": its error message}."""
    passed, failed = env.root / f"{block}.passed.json", env.root / f"{block}.failed.json"
    assert passed.exists() != failed.exists(), (
        f"{block}: {'both' if passed.exists() else 'neither'} its results output and its error output came")
    if passed.exists():
        return {"ended": "success", "results": json.loads(passed.read_text(encoding="utf-8")), "error": ""}
    kept = json.loads(failed.read_text(encoding="utf-8"))
    assert isinstance(kept, dict) and isinstance(kept.get("results"), dict), (
        f"{block}: its error output carries no results table: {kept}")
    return {"ended": "failed", "results": kept["results"], "error": str(kept.get("error") or "")}


def results_for(found: dict, name: str) -> list[dict]:
    return [r for r in found["results"]["rows"] if named_check(r.get("check")) == name]


def row_for(found: dict, name: str) -> dict:
    """The results row of the check `name`."""
    rows_ = results_for(found, name)
    assert len(rows_) == 1, f"one results row for the {name} check: {found['results']}"
    assert rows_[0].get("outcome") in OUTCOMES, rows_[0]
    return rows_[0]


def details(env, block: str, name: str) -> str:
    return str(row_for(outcome(env, block), name).get("details") or "")


def logs_of(env, run_id: int, block: str) -> list[tuple[str, str]]:
    return env.query("SELECT level, message FROM logs WHERE run_id=? AND block=? ORDER BY rowid", run_id, block)


def error_of(env, run_id: int, block: str) -> str:
    found = env.query("SELECT error FROM blocks WHERE run_id=? AND block=?", run_id, block)
    return (found[0][0] or "") if found else ""


def has_number(text: str, n: int) -> bool:
    """Whether `text` has the number n on its own (not inside another number or a word; 50026 also as 50,026)."""
    return any(re.search(rf"(?<![\w.,-]){re.escape(form)}(?!\w|[.,]\d)", str(text)) for form in {str(n), f"{n:,}"})


def names_run(text: str, run_id: int, count: int) -> bool:
    """Whether a comparison's details name the run (#N) and its row count."""
    return bool(re.search(rf"#{run_id}(?!\d)", text)) and has_number(text, count)


def nothing_to_compare(text: str) -> bool:
    return bool(re.search(r"\bno(thing)?\b", text, re.I)) and "compar" in text.lower()


# --- Properties ---------------------------------------------------------------------------------------------------------

def shown_texts(win) -> list[tuple[str, object]]:
    """The labels and the check boxes' texts Properties shows, with their widgets."""
    body = win.properties.widget()
    return [(w.text(), w) for w in body.findChildren(QLabel) + body.findChildren(QCheckBox)
            if not w.isHidden() and w.text()]


def tick_box(win, name: str) -> QCheckBox:
    """The check box of a check's tick in Properties."""
    text = label_of(ticks()[name])
    boxes = [w for t, w in shown_texts(win) if t == text and isinstance(w, QCheckBox)]
    if boxes:
        return boxes[0]
    widget = field(win, text)
    box = widget if isinstance(widget, QCheckBox) else widget.findChild(QCheckBox)
    assert box is not None, f"the tick {text} has no check box in Properties"
    return box


def setting_labels() -> set[str]:
    return {label_of(k) for name in NAMES for k in settings_of(name)}


def shown_settings(win) -> Counter:
    """How many times Properties shows each label of a check's setting (If it fails is one for each check)."""
    labels = setting_labels()
    return Counter(text for text, _ in shown_texts(win) if text in labels)


def settings_for(ticked_: set) -> Counter:
    return Counter(label_of(k) for name in ticked_ for k in settings_of(name))


# --- 1: a Checks category right after Data, an icon of Tabler's ---------------------------------------------------------

def test_check_table_is_in_a_checks_group_right_after_data_with_its_own_colour_and_explains_itself_on_hover(win):
    """Spec 1 (block type checks.check_table, title "Check Table", category "Checks": a new category, listed right
    after Data in the Blocks list, with its own colour) and 8 (the hover help describes the block): the Blocks list
    shows a Checks group right after Data, holding Check Table; its group's square is painted in a colour no other
    group has; resting on Check Table shows what it does."""
    cls = check_class()
    assert (cls.title, cls.category) == ("Check Table", "Checks")
    groups = list_rows(win.palette_panel)
    order = list(groups)
    assert "Checks" in order and order.index("Checks") == order.index("Data") + 1, order
    listed = {item.text(0): item for item in groups["Checks"]}
    assert "Check Table" in listed, sorted(listed)

    colour = CATEGORY_COLORS.get("Checks")
    others = {c.lower() for group, c in CATEGORY_COLORS.items() if group != "Checks"} | {USER_COLOR.lower(),
                                                                                       NOTES.lower()}
    assert colour and colour.lower() not in others, (colour, sorted(others))
    tree = win.palette_panel.tree
    [folder] = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                if tree.topLevelItem(i).text(0).split("  (")[0] == "Checks"]
    painted = row(win.palette_panel, folder)
    assert (np.abs(painted - QColor(colour).getRgb()[:3]).max(axis=2) <= 10).sum() >= 50, (
        "the Checks group does not show a square of its colour")

    tip = html.unescape(re.sub(r"<[^>]+>", " ", listed["Check Table"].toolTip(0)))
    assert cls.description and " ".join(cls.description.split()) in " ".join(tip.split()), tip
    assert words(cls.description) & {"check", "checks", "checked"}, cls.description


def test_its_icon_is_tablers_list_check_drawing(win, tmp_path):
    """Spec 1 (an icon from Tabler's outline set fitting a checked list, e.g. "list-check", same style as the others):
    of every built-in block's icon on the canvas, the Check Table's is the most like Tabler's list-check drawing, and it
    is more like it than like Tabler's list without ticks."""
    check_class()
    colours = {t: CATEGORY_COLORS[cls.category] for t, cls in win.registry.blocks.items()
               if win.registry.sources[t] == "built-in"}
    icons = canvas_icons(win, tmp_path, colours)
    reference = drawing(TABLER_LIST_CHECK)
    closest = min(icons, key=lambda t: unlike(icons[t], reference))
    assert closest == CHECK, (f"Tabler's list-check drawing is most like the icon of {closest} "
                              f"({unlike(icons[closest], reference):.3f} against {unlike(icons[CHECK], reference):.3f})")
    assert unlike(icons[CHECK], reference) < unlike(icons[CHECK], drawing(TABLER_LIST)), "its icon is a list without ticks"


# --- 2: what goes in and out -------------------------------------------------------------------------------------------

def test_a_table_that_passes_its_checks_goes_on_unchanged_and_results_has_a_row_per_ticked_check(env):
    """Spec 2 (input table; outputs table, the same table unchanged, and results, a row per ticked check: check,
    outcome, rows (how many rows break it; empty when the check is not about rows) and details) and 3: Read File, then
    Check Table with three checks that pass; the table it passes on is the one Read File gave (rows, columns, types)."""
    path = checking(env, read_file(orders(env.root)),
                    {"check": check_table(row_count(at_least=1), no_missing(columns=["order_id", "region"]),
                                          unique("order_id"))},
                    blocks={"save": write_file(env.root / "out.parquet", y=600),
                            "save_given": write_file(env.root / "given.parquet", y=900)},
                    edges=["check.table -> save.table", "load.table -> save_given.table"])
    result, run_id, status = run_path(env, path)

    assert status.get("check") == "success" and status.get("save") == "success", said(result)
    passed_on, given = pl.read_parquet(env.root / "out.parquet"), pl.read_parquet(env.root / "given.parquet")
    assert passed_on.schema == given.schema and passed_on.equals(given)
    found = outcome(env, "check")
    assert found["results"]["columns"] == ["check", "outcome", "rows", "details"], found["results"]
    assert len(found["results"]["rows"]) == 3, found["results"]
    assert {n: row_for(found, n)["outcome"] for n in ("row_count", "missing", "unique")} == {
        "row_count": "passed", "missing": "passed", "unique": "passed"}
    assert row_for(found, "row_count")["rows"] is None, "the row count is not about rows: its rows is empty"
    assert row_for(found, "missing")["rows"] == 0 and row_for(found, "unique")["rows"] == 0, found["results"]


def test_every_check_runs_and_the_block_fails_naming_each_failed_check_with_its_details(env):
    """Spec 2 (table only when no check set to fail the block failed; the error output also carries results) and 4
    (the block fails with a message naming each failed check with its details: how many rows break it and up to 5
    example rows, numbered from 1; every check still runs, so the message and results cover all of them): at least
    1000 rows and no missing amount fail; unique order_id passes; region north or south only warns."""
    check = check_table(row_count(at_least=1000), no_missing(columns=["amount"]), unique("order_id"),
                        within(region="north, south", warn=True))
    path = checking(env, read_file(orders(env.root)), {"check": check},
                    blocks={"save": write_file(env.root / "out.parquet", y=600)}, edges=["check.table -> save.table"])
    result, run_id, status = run_path(env, path)

    assert status.get("check") == "failed", said(result)
    assert status.get("save") != "success" and not (env.root / "out.parquet").exists(), "the table was passed on"
    found = outcome(env, "check")
    assert found["ended"] == "failed"
    assert len(found["results"]["rows"]) == 4, found["results"]
    got = {n: (row_for(found, n)["outcome"], row_for(found, n)["rows"]) for n in ("row_count", "missing", "unique",
                                                                                  "values")}
    assert got == {"row_count": ("failed", None), "missing": ("failed", 2), "unique": ("passed", 0),
                   "values": ("warned", 1)}, found["results"]
    error = error_of(env, run_id, "check")
    assert "count" in words(error) and has_number(error, 10) and has_number(error, 1000), error
    assert "missing" in words(error) and "amount" in words(error) and has_number(error, 2), error
    assert has_number(error, 3) and has_number(error, 6), f"the example rows (3 and 6) are not named: {error}"


# --- 3: the checks: a tick, If it fails -----------------------------------------------------------------------------------

def test_only_ticked_checks_run_and_one_set_to_warn_warns_in_the_log_while_the_table_goes_on(env):
    """Spec 3 (only a ticked check runs; If it fails: warn writes a WARNING line in the Log and the block succeeds)
    and 2 (the table goes on): Unique set to warn finds order 701 twice; No missing values, filled in but not ticked,
    does not run."""
    data = write_csv(env.root / "repeats.csv", "order_id,amount", [(701, 1.5), (702, None), (701, 2.0), (703, None)])
    unticked = {**no_missing(columns=["amount"]), ticks()["missing"]: False}
    path = checking(env, read_file(data), {"check": check_table(unique("order_id", warn=True), unticked)},
                    blocks={"save": write_file(env.root / "out.parquet", y=600)}, edges=["check.table -> save.table"])
    result, run_id, status = run_path(env, path)

    assert status.get("check") == "success" and status.get("save") == "success", said(result)
    assert pl.read_parquet(env.root / "out.parquet")["order_id"].to_list() == [701, 702, 701, 703]
    rows_ = outcome(env, "check")["results"]["rows"]
    assert len(rows_) == 1 and named_check(rows_[0]["check"]) == "unique" and rows_[0]["outcome"] == "warned", rows_
    logs = logs_of(env, run_id, "check")
    assert any(level == "WARNING" and "unique" in words(m) and "701" in m for level, m in logs), logs


def test_a_new_check_table_starts_with_the_row_count_check_ticked_at_least_one_row(env, win, monkeypatch):
    """Spec 3 (a new Check Table starts with the row count check ticked, at least 1 row): added from the Blocks list,
    Properties shows only that check ticked, at least 1; the block as saved fails on a table without rows and passes
    on one with a row."""
    check_class()
    tab = win.new_flow()
    settle()
    add_from_palette(win, "Check Table")
    settle()
    [block] = [b for b, spec in tab.doc.blocks.items() if spec.get("type") == CHECK]
    select_only(tab, block)

    assert {n: tick_box(win, n).isChecked() for n in NAMES} == {n: n == "row_count" for n in NAMES}
    least = field(win, label_of(labelled("row_count", "least", "min", "minimum", "fewest")))
    shown = (least.text() or least.placeholderText()) if isinstance(least, QLineEdit) else str(least.value())
    assert shown == "1"
    path = env.root / "new.yaml"
    save_as(monkeypatch, path)
    assert win.save(tab)
    config = saved(path)["blocks"][block].get("config") or {}
    empty = write_csv(env.root / "empty.csv", "id,amount", [])
    one_row = write_csv(env.root / "one.csv", "id,amount", [(1, 2.5)])
    blocks = {"load_empty": read_file(empty), "on_empty": {"type": CHECK, "config": config, "ui": {"x": 0, "y": 0}},
              "load_one": read_file(one_row, y=300), "on_one": {"type": CHECK, "config": config,
                                                                "ui": {"x": 0, "y": 300}}}
    result, run_id, status = run_flow(env, blocks, ["load_empty.table -> on_empty.table",
                                                    "load_one.table -> on_one.table"])

    assert (status.get("on_empty"), status.get("on_one")) == ("failed", "success"), said(result)


# --- 3a: row count -------------------------------------------------------------------------------------------------------

def test_row_count_at_least_a_minimum_and_or_at_most_a_maximum(env):
    """Spec 3a (row count: at least a minimum and/or at most a maximum number of rows) and 4 (a failing check's
    details): on 10 rows; a failing one says the count and the bound."""
    checks = {"ten": check_table(row_count(at_least=10)), "eleven": check_table(row_count(at_least=11)),
              "at_most_ten": check_table(row_count(at_most=10)), "at_most_nine": check_table(row_count(at_most=9)),
              "between": check_table(row_count(at_least=5, at_most=20))}
    result, run_id, status = run_path(env, checking(env, read_file(orders(env.root)), checks))

    assert {b: status.get(b) for b in checks} == {"ten": "success", "eleven": "failed", "at_most_ten": "success",
                                                  "at_most_nine": "failed", "between": "success"}, said(result)
    for block, bound in (("eleven", 11), ("at_most_nine", 9)):
        found = outcome(env, block)
        failed = row_for(found, "row_count")
        assert failed["outcome"] == "failed" and failed["rows"] is None, failed
        assert has_number(failed["details"], 10) and has_number(failed["details"], bound), failed
        assert has_number(found["error"], 10) and has_number(found["error"], bound), found["error"]
    assert row_for(outcome(env, "between"), "row_count")["outcome"] == "passed"


# --- 3b: row count compared with the last successful run -------------------------------------------------------------------

def test_row_count_compared_with_the_latest_earlier_run_in_which_the_block_succeeded(env):
    """Spec 3b (the rows may differ by at most N% (default 30) from the row count this block passed on in the latest
    earlier run of the same flow in which this block succeeded; with no such run it passes, saying there is nothing to
    compare with yet; its details name that run (#N) and its row count): `usual` (30%) and `strict` (10%) on 100, 129,
    170, 140 then 90 rows. A run in which the block failed is not compared with, whatever the run's own status."""
    data = env.root / "daily.csv"
    checks = {"usual": check_table(compared_with_last_run()), "strict": check_table(compared_with_last_run(percent=10))}

    def run_on(n: int):
        write_csv(data, "id,amount", [(i, i + 0.5) for i in range(1, n + 1)])
        result, run_id, status = run_path(env, checking(env, read_file(data), checks, name="daily"))
        assert run_id is not None and all(status.get(b) in ("success", "failed") for b in checks), said(result)
        return run_id, {b: status[b] for b in checks}, {b: details(env, b, "compared") for b in checks}

    first, ended, told = run_on(100)
    assert ended == {"usual": "success", "strict": "success"}
    assert all(nothing_to_compare(d) for d in told.values()), told
    second, ended, told = run_on(129)  # 29% more than 100
    assert ended == {"usual": "success", "strict": "failed"}
    assert names_run(told["usual"], first, 100) and names_run(told["strict"], first, 100), told
    third, ended, told = run_on(170)  # usual: 32% more than 129 (run 2); strict: 70% more than 100 (run 1)
    assert ended == {"usual": "failed", "strict": "failed"}
    assert names_run(told["usual"], second, 129) and names_run(told["strict"], first, 100), told
    fourth, ended, told = run_on(140)  # usual: 9% more than 129 (run 3, in which it failed, does not count)
    assert ended["usual"] == "success" and names_run(told["usual"], second, 129), told
    fifth, ended, told = run_on(90)  # usual: 36% fewer than 140 (run 4)
    assert ended["usual"] == "failed" and names_run(told["usual"], fourth, 140), told


def test_row_count_compared_uses_the_runs_of_the_same_flow_only(env):
    """Spec 3b (the latest earlier run of the same flow): a block of the same name in another flow has runs of its own;
    neither flow compares with the other's."""
    daily, other = env.root / "daily.csv", env.root / "other.csv"
    checks = {"usual": check_table(compared_with_last_run())}
    write_csv(daily, "id", [(i,) for i in range(1, 101)])
    result, first, status = run_path(env, checking(env, read_file(daily), checks, name="daily"))
    assert status.get("usual") == "success", said(result)
    write_csv(other, "id", [(i,) for i in range(1, 1001)])
    result, other_run, status = run_path(env, checking(env, read_file(other), checks, name="other"))
    assert status.get("usual") == "success", said(result)
    assert nothing_to_compare(details(env, "usual", "compared")), details(env, "usual", "compared")
    write_csv(daily, "id", [(i,) for i in range(1, 111)])
    result, third, status = run_path(env, checking(env, read_file(daily), checks, name="daily"))

    assert status.get("usual") == "success", said(result)
    told = details(env, "usual", "compared")
    assert names_run(told, first, 100) and not re.search(rf"#{other_run}(?!\d)", told), told


def loop_flow(folder: Path, name: str, *parts: dict) -> Path:
    """make (two items) -> each (a Loop); inside each: load (Read File, after each item) -> check (Check Table)."""
    data = write_csv(folder / "part.csv", "id,amount", [(1, 2.0), (2, 3.0)])
    blocks = {"make": {"type": "logic.python", "config": {"inputs": [], "code": "output = ['north', 'south']"},
                       "ui": {"x": -900, "y": 0}},
              "each": {"type": "logic.loop", "config": {}, "ui": {"x": -400, "y": -100}},
              "load": {**read_file(data, x=-330, y=0), "inside": "each"},
              "check": {**check_table(*parts, x=-60, y=0), "inside": "each"}}
    return flow_file(folder / name, blocks, ["make.output -> each.items", "each.item -> load.after",
                                             "load.table -> check.table"])


def test_row_count_compared_inside_a_loop_is_refused_by_validate_and_properties(env, win):
    """Spec 3b (inside a Loop this check is refused: Properties and Validate say so): a Check Table inside a Loop with
    the comparison ticked is refused, saying it is the Loop; one with only the row count ticked is fine there."""
    refused = loop_flow(env.root, "refused.yaml", compared_with_last_run())
    fine = loop_flow(env.root, "fine.yaml", row_count(at_least=1))
    checked = env.taskloom("validate", refused)
    errors = [line for line in checked.stderr.splitlines() if line.startswith("error:")]

    assert checked.returncode != 0 and any("check" in line and "loop" in line.lower() for line in errors), (
        checked.stdout + checked.stderr)
    passed = env.taskloom("validate", fine)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    tab = win.open_flow(refused)
    settle()
    select_only(tab, "check")
    assert "loop" in error_shown(win).lower(), error_shown(win)
    clean(win)


# --- 3c: no missing values -------------------------------------------------------------------------------------------------

def test_no_missing_values_in_the_columns_listed_or_every_column_up_to_a_number_or_a_percentage(env):
    """Spec 3c (in the columns listed, none listed: every column, a null is missing, and an empty or blank text too
    when Count empty text as missing is ticked (unticked by default); allowed: at most a number (default 0) or a
    percentage of the rows, per column; its details name each column over the limit with its count): amount is
    missing in 3 of 10 rows (2, 5, 8), region in 2 (4, 9), and region is empty in row 6 and blank in row 7."""
    data = write_csv(env.root / "people.csv", "id,amount,region,note", [
        (1, 10, "north", "a"), (2, None, "south", "b"), (3, 7, "north", "c"), (4, 2, None, "d"), (5, None, "east", "e"),
        (6, 5, "", "f"), (7, 1, "   ", "g"), (8, None, "north", "h"), (9, 3, None, "i"), (10, 4, "south", "j")])
    checks = {"amount_only": check_table(no_missing(columns=["amount"])),
              "every_column": check_table(no_missing()),
              "three_allowed": check_table(no_missing(allowed="3")),
              "quarter_allowed": check_table(no_missing(allowed="25%")),
              "empty_counted": check_table(no_missing(columns=["region"], allowed="3", empty_text=True)),
              "empty_not_counted": check_table(no_missing(columns=["region"], allowed="3"))}
    result, run_id, status = run_path(env, checking(env, read_file(data), checks))

    assert {b: status.get(b) for b in checks} == {"amount_only": "failed", "every_column": "failed",
                                                  "three_allowed": "success", "quarter_allowed": "failed",
                                                  "empty_counted": "failed", "empty_not_counted": "success"}, said(result)

    def named(text: str, column: str, count: int) -> bool:
        return bool(re.search(rf"\b{column}\b[^0-9]{{0,40}}(?<![\w.,-]){count}(?!\w|[.,]\d)"
                              rf"|(?<![\w.,-]){count}(?!\w|[.,]\d)[^0-9]{{0,40}}\b{column}\b", text))

    amount_only = row_for(outcome(env, "amount_only"), "missing")
    assert amount_only["rows"] == 3 and named(amount_only["details"], "amount", 3), amount_only
    every = row_for(outcome(env, "every_column"), "missing")
    assert every["rows"] == 5, every
    assert named(every["details"], "amount", 3) and named(every["details"], "region", 2), every
    quarter = row_for(outcome(env, "quarter_allowed"), "missing")
    assert quarter["outcome"] == "failed" and named(quarter["details"], "amount", 3), quarter
    counted = row_for(outcome(env, "empty_counted"), "missing")
    assert counted["rows"] == 4 and named(counted["details"], "region", 4), counted
    assert row_for(outcome(env, "empty_not_counted"), "missing")["outcome"] == "passed"


# --- 3d: unique ----------------------------------------------------------------------------------------------------------------

def test_unique_on_a_column_or_on_columns_together_says_how_many_values_repeat_with_examples(env):
    """Spec 3d (the columns listed, together, have no value repeated, e.g. order_id, or day + region; its details say
    how many values repeat, with examples): order 701 is there 3 times and 909 twice; day repeats, but no day and
    region together; order_id and region together repeat once (701, south)."""
    data = write_csv(env.root / "orders.csv", "order_id,day,region", [
        (701, "2026-11-01", "north"), (702, "2026-11-01", "south"), (703, "2026-11-02", "north"),
        (701, "2026-11-02", "south"), (704, "2026-11-03", "north"), (701, "2026-11-03", "south"),
        (909, "2026-11-04", "north"), (909, "2026-11-04", "south"), (705, "2026-11-05", "north")])
    checks = {"order": check_table(unique("order_id")), "day": check_table(unique("day")),
              "day_region": check_table(unique("day", "region")),
              "order_region": check_table(unique("order_id", "region"))}
    result, run_id, status = run_path(env, checking(env, read_file(data), checks))

    assert {b: status.get(b) for b in checks} == {"order": "failed", "day": "failed", "day_region": "success",
                                                  "order_region": "failed"}, said(result)
    order = row_for(outcome(env, "order"), "unique")
    assert has_number(order["details"], 2) and "701" in order["details"] and "909" in order["details"], order
    assert order["rows"] in (3, 5), f"rows breaking it: the 3 repeats, or the 5 rows with a repeated value: {order}"
    pair = row_for(outcome(env, "order_region"), "unique")
    assert has_number(pair["details"], 1) and "701" in pair["details"] and "south" in pair["details"], pair
    assert row_for(outcome(env, "day_region"), "unique")["outcome"] == "passed"


# --- 3e: columns and types ---------------------------------------------------------------------------------------------------

def test_columns_and_types_a_missing_column_or_one_stored_with_another_type_fails_saying_so(env):
    """Spec 3e (the columns listed are present, a missing one fails it, naming it; the type may be left empty; types
    text, int, float (any number), bool, date, datetime; a column stored with another type fails it, saying the type
    it has): a Parquet file of each type; an int column passes as float, any whole-number or decimal width too."""
    path = env.root / "typed.parquet"
    pl.DataFrame({
        "id": [1, 2, 3], "qty": pl.Series([5, 6, 7], dtype=pl.Int32), "amount": [1.5, 2.0, 3.25],
        "ratio": pl.Series([0.5, 0.25, 1.0], dtype=pl.Float32), "flag": [True, False, True],
        "day": [dt.date(2026, 11, 1), dt.date(2026, 11, 2), dt.date(2026, 11, 3)],
        "ts": [dt.datetime(2026, 11, 1, 8), dt.datetime(2026, 11, 2, 9, 30), dt.datetime(2026, 11, 3, 10)],
        "name": ["alpha", "beta", "gamma"], "region": ["north", "south", "north"],
    }).write_parquet(path)
    checks = {"right": check_table(columns_and_types(id="int", qty="float", amount="float", ratio="float", flag="bool",
                                                     day="date", ts="datetime", name="text", region="")),
              "wrong": check_table(columns_and_types(amount="int", ts="date", flag="int", segment="", code="text"))}
    result, run_id, status = run_path(env, checking(env, read_file(path), checks))

    assert (status.get("right"), status.get("wrong")) == ("success", "failed"), said(result)
    told = details(env, "wrong", "types")
    assert "segment" in words(told) and "code" in words(told), f"the missing columns are not named: {told}"
    for column, has in (("amount", "float"), ("ts", "datetime"), ("flag", "bool")):
        assert column in words(told) and has in words(told), f"{column} has {has}: {told}"


def test_text_in_a_numeric_column_fails_listing_the_values_that_do_not_read_with_their_row_counts(env):
    """Spec 3e, the user's case ("surprises of text data within a numeric column"): a TEXT column expected as int,
    float, date or datetime passes only if every value (missing ones aside) reads as that type (numbers as Python and
    SQL write them, dates YYYY-MM-DD, date-times YYYY-MM-DD HH:MM[:SS]); otherwise it fails, listing the distinct values
    that do not read with how many rows have each (e.g. 'N/A' (12 rows), '1,234.5' (3 rows)), at most 5 of them,
    saying how many more. Read File reads these columns from the CSV as text."""
    bad = {"N/A": 12, "pending": 6, "1,234.5": 3, "TBD": 2, "$40": 1, "n.a.": 1, "12 EUR": 1}
    amounts = {2: "N/A", 3: "1,234.5", 5: "pending", 7: "TBD", 9: "$40", 11: "n.a.", 13: "12 EUR"}
    amounts.update({k: "N/A" for k in range(20, 31)})
    amounts.update({k: "pending" for k in range(31, 36)})
    amounts.update({36: "1,234.5", 37: "1,234.5", 38: "TBD", 40: None, 41: None})
    good = ["12.5", "100", "-3", "0.25", "7"]
    rows_ = []
    for k in range(1, 61):
        day = {4: "03/11/2026", 6: "03/11/2026", 8: "2026-13-01", 50: None}.get(k, f"2026-10-{k % 28 + 1:02d}")
        at = None if k == 51 else ("2026-11-03 08:15" if k % 2 else "2026-11-03 08:15:30")
        qty = {10: "12.5", 12: "N/A"}.get(k, "5")
        rows_.append((k, amounts.get(k, good[k % 5]), day, at, qty))
    data = write_csv(env.root / "orders.csv", "order_id,amount,day,at,qty", rows_)
    assert sum(1 for r in rows_ if r[1] in bad) == sum(bad.values())
    checks = {"amount_float": check_table(columns_and_types(amount="float")),
              "day_date": check_table(columns_and_types(day="date")),
              "at_datetime": check_table(columns_and_types(at="datetime", order_id="int")),
              "qty_int": check_table(columns_and_types(qty="int"))}
    result, run_id, status = run_path(env, checking(env, read_file(data), checks))

    assert {b: status.get(b) for b in checks} == {"amount_float": "failed", "day_date": "failed",
                                                  "at_datetime": "success", "qty_int": "failed"}, said(result)
    amount = details(env, "amount_float", "types")
    assert re.search(r"N/A['\"]?\W{0,3}12 rows", amount) and re.search(r"1,234\.5['\"]?\W{0,3}3 rows", amount), amount
    shown = [value for value in bad if value in amount]
    assert 2 <= len(shown) <= 5, f"at most 5 of the values that do not read: {shown} in {amount}"
    assert re.search(r"\b2 (more|other)", amount), f"it does not say 2 more: {amount}"
    assert "0.25" not in amount, f"a number that reads is listed: {amount}"
    day = details(env, "day_date", "types")
    assert re.search(r"03/11/2026['\"]?\W{0,3}2 rows", day) and re.search(r"2026-13-01['\"]?\W{0,3}1 rows?", day), day
    qty = details(env, "qty_int", "types")
    assert "12.5" in qty and "N/A" in qty, f"12.5 is no whole number, N/A no number: {qty}"


# --- 3f: values within a range or a list ------------------------------------------------------------------------------------

def test_values_within_a_range_or_a_list_numbers_and_dates_in_text_read_as_such_missing_ones_not_counted(env):
    """Spec 3f (rows Column=min..max, either side may be left empty, or Column=a, b, c; missing values are not counted;
    a range on a number column compares as numbers; on a text column a range of numbers or dates reads the values as
    numbers or dates, and a value that does not read breaks it): amount is text (it has an N/A), so 9 is within 0..50
    (as text it would sort after 50) and 100 and N/A are not."""
    data = write_csv(env.root / "scores.csv", "id,amount,region,day,score", [
        (1, "9", "north", "2026-01-15", 50), (2, "10", "south", "2026-03-01", 75.5), (3, "100", "north", "2026-06-30", None),
        (4, "N/A", "east", "2026-07-01", 101), (5, None, "south", "2025-12-31", -1), (6, "45", None, "2026-02-10", 0)])
    checks = {"amount_range": check_table(within(amount="0..50")),
              "region_list": check_table(within(region="north, south")),
              "day_range": check_table(within(day="2026-01-01..2026-06-30")),
              "score_from": check_table(within(score="0..")),
              "score_up_to": check_table(within(score="..100")),
              "all_within": check_table(within(region="north, south, east", score="-1..101"))}
    result, run_id, status = run_path(env, checking(env, read_file(data), checks))

    assert {b: status.get(b) for b in checks} == {
        "amount_range": "failed", "region_list": "failed", "day_range": "failed", "score_from": "failed",
        "score_up_to": "failed", "all_within": "success"}, said(result)
    broken = {b: row_for(outcome(env, b), "values")["rows"] for b in checks}
    assert broken == {"amount_range": 2, "region_list": 1, "day_range": 2, "score_from": 1, "score_up_to": 1,
                      "all_within": 0}, broken
    assert "N/A" in details(env, "amount_range", "values")
    assert "east" in details(env, "region_list", "values")


# --- 3g: freshness ---------------------------------------------------------------------------------------------------------

def test_freshness_the_latest_value_is_at_least_the_expression_with_the_flow_parameters(env):
    """Spec 3g (the latest value of a date or datetime column, or of a text column of YYYY-MM-DD dates read as dates,
    is at least an expression, default today() - days(1), evaluated as If's conditions are, with the flow parameters;
    its details say the latest value found and the one expected): run as of Monday 2026-11-02 09:00; the latest day is
    Friday 2026-10-30, the latest time 2026-10-31 10:00."""
    path = env.root / "loads.parquet"
    pl.DataFrame({
        "day": [dt.date(2026, 10, 28), dt.date(2026, 10, 30), None, dt.date(2026, 10, 29)],
        "ts": [dt.datetime(2026, 10, 30, 23), dt.datetime(2026, 10, 31, 10), dt.datetime(2026, 10, 29, 8), None],
        "day_text": ["2026-10-29", "2026-10-30", "2026-10-27", None],
    }).write_parquet(path)
    checks = {"day_default": check_table(fresh("day")), "day_bdays": check_table(fresh("day", "today() - bdays(1)")),
              "text_default": check_table(fresh("day_text")),
              "text_bdays": check_table(fresh("day_text", "today() - bdays(1)")),
              "ts_two_days": check_table(fresh("ts", "now() - days(2)")),
              "ts_one_day": check_table(fresh("ts", "now() - days(1)")),
              "since": check_table(fresh("day", "since")), "after_since": check_table(fresh("day", "since + days(1)"))}
    flow = checking(env, read_file(path), checks, params={"since": {"type": "date", "value": "2026-10-30"}})
    result, run_id, status = run_path(env, flow, as_of="2026-11-02 09:00")

    assert {b: status.get(b) for b in checks} == {
        "day_default": "failed", "day_bdays": "success", "text_default": "failed", "text_bdays": "success",
        "ts_two_days": "success", "ts_one_day": "failed", "since": "success", "after_since": "failed"}, said(result)
    for block in ("day_default", "text_default"):
        told = details(env, block, "freshness")
        assert "2026-10-30" in told and "2026-11-01" in told, f"{block}: {told}"
    told = details(env, "ts_one_day", "freshness")
    assert re.search(r"2026-10-31[ T]10:00", told) and re.search(r"2026-11-01[ T]09:00", told), told
    assert "2026-10-31" in details(env, "after_since", "freshness")


# --- 3h: conditions every row must meet -------------------------------------------------------------------------------------

def test_conditions_every_row_must_meet_each_checked_separately_a_missing_value_breaks_one(env):
    """Spec 3h (SQL conditions, each checked separately; a row where one is not true, false or unknown because of a
    missing value, breaks it; amount is null or amount >= 0 allows missing ones) and 4 (how many rows, example rows
    with their row numbers and values): row 2 has amount -5 and ends before it starts, row 3 has no amount, rows 5 and
    6 end before they start (the ids are not the row numbers): amount >= 0 is broken by 2 rows, the dates by 3."""
    data = write_csv(env.root / "periods.csv", "id,amount,start_date,end_date", [
        (71, 10, "2026-01-01", "2026-01-05"), (72, -5, "2026-01-02", "2026-01-01"),
        (73, None, "2026-01-03", "2026-01-04"), (74, 0, "2026-01-04", "2026-01-04"), (75, 7, "2026-01-05", "2026-01-03"),
        (76, 1, "2026-01-06", "2026-01-02")])
    checks = {"both": check_table(every_row("amount >= 0", "end_date >= start_date")),
              "allowing_missing": check_table(every_row("amount is null or amount >= 0")),
              "missing_breaks": check_table(every_row("amount >= -10")),
              "met": check_table(every_row("id > 0"))}
    result, run_id, status = run_path(env, checking(env, read_file(data), checks))

    assert {b: status.get(b) for b in checks} == {"both": "failed", "allowing_missing": "failed",
                                                  "missing_breaks": "failed", "met": "success"}, said(result)
    both = outcome(env, "both")
    for condition, count in (("amount >= 0", 2), ("end_date >= start_date", 3)):  # each with its own count
        number = rf"(?<![\w.,-]){count}(?!\w|[.,]\d)"
        assert re.search(rf"{re.escape(condition)}\D{{0,40}}{number}|{number}\D{{0,40}}{re.escape(condition)}",
                         both["error"]), f"{condition} with its {count} rows: {both['error']}"
    assert results_for(both, "conditions") and all(r["outcome"] == "failed" for r in results_for(both, "conditions"))
    allowing = row_for(outcome(env, "allowing_missing"), "conditions")
    assert allowing["rows"] == 1 and has_number(allowing["details"], 2) and "-5" in allowing["details"], allowing
    breaks = row_for(outcome(env, "missing_breaks"), "conditions")
    assert breaks["rows"] == 1 and has_number(breaks["details"], 3), breaks
    met = row_for(outcome(env, "met"), "conditions")
    assert (met["outcome"], met["rows"]) == ("passed", 0), met


def test_a_condition_that_is_not_valid_sql_fails_the_block_naming_it_even_when_set_to_warn(env):
    """Spec 3h (a condition that is not valid SQL fails the block clearly, naming it) and the no-surprises rule: also
    with If it fails set to warn, as no row was checked; a condition naming a column the table does not have too."""
    checks = {"syntax": check_table(every_row("amount >>= 0")),
              "unknown_column": check_table(every_row("amout >= 0", warn=True))}
    result, run_id, status = run_path(env, checking(env, read_file(orders(env.root)), checks))

    for block, condition in (("syntax", "amount >>= 0"), ("unknown_column", "amout >= 0")):
        told = result.stdout + result.stderr + (error_of(env, run_id, block) if run_id is not None else "")
        assert status.get(block) != "success" and condition in told, f"{block}: {status} {told[-3000:]}"


# --- 4: example rows ------------------------------------------------------------------------------------------------------

BAD_AT = (13, 50_026, 100_039, 150_052, 200_065, 220_078, 240_091)


def test_a_failing_checks_details_give_how_many_rows_break_it_and_up_to_5_example_rows_numbered_from_1(env):
    """Spec 4 (a failing check's details say how many rows break it and show up to 5 example rows: their row numbers,
    counting from 1 in the table's order, and the values of the columns concerned): 7 rows of 250,000 (spread over
    the table's Parquet row groups) have a code other than ok, each saying its row number (bad-at-13 is row 13)."""
    n = 250_000
    lines = ["code,amount"] + [f"{f'bad-at-{k}' if k in BAD_AT else 'ok'},{k % 89}.5" for k in range(1, n + 1)]
    data = env.root / "codes.csv"
    data.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    result, run_id, status = run_path(env, checking(env, read_file(data), {"check": check_table(within(code="ok"))}))

    assert status.get("check") == "failed", said(result)
    found = outcome(env, "check")
    broken = row_for(found, "values")
    assert broken["rows"] == 7 and has_number(broken["details"], 7), broken
    shown = [k for k in BAD_AT if f"bad-at-{k}" in broken["details"]]
    assert 1 <= len(shown) <= 5, f"up to 5 example rows: {broken['details']}"
    numbered = [k for k in shown if has_number(broken["details"], k)]
    assert numbered == shown, f"these examples are not given their row number: {set(shown) - set(numbered)}: {broken}"
    assert all(f"bad-at-{k}" in found["error"] and has_number(found["error"], k) for k in shown), found["error"]


# --- 5: the Log -------------------------------------------------------------------------------------------------------------

def test_the_log_has_a_line_per_check_saying_passed_failed_or_warned_with_its_details(env):
    """Spec 5 (the Log has a line per check: passed, failed or warned, with its details): at least 5 of 10 rows
    passes; region north or south warns (row 4 is east); no missing amount fails (rows 3 and 6)."""
    check = check_table(row_count(at_least=5), within(region="north, south", warn=True), no_missing(columns=["amount"]))
    result, run_id, status = run_path(env, checking(env, read_file(orders(env.root)), {"check": check}))

    assert status.get("check") == "failed", said(result)
    logs = [(level, m) for level, m in logs_of(env, run_id, "check") if not m.startswith("check (")]

    def lines(name: str) -> list[tuple[str, str]]:
        return [(level, m) for level, m in logs if named_check(m) == name]

    assert any("pass" in m.lower() and has_number(m, 10) for _, m in lines("row_count")), logs
    assert any(level == "WARNING" and "east" in m for level, m in lines("values")), logs
    assert any("fail" in m.lower() and "amount" in m for _, m in lines("missing")), logs


# --- 6: millions of rows --------------------------------------------------------------------------------------------------

MAXRSS = ("import os, resource, sys; from taskloom.cli import main; rc = main(sys.argv[1:]); "
          "print('MAXRSS', resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, flush=True); os._exit(rc)")


@pytest.mark.skipif(sys.platform == "win32", reason="measures memory with the resource module")
def test_millions_of_rows_are_checked_without_reading_the_table_into_memory(env):
    """Spec 6 (it runs on the table's Parquet file, never reading the whole table into memory: it works on tables of
    millions of rows): the same checks on 500,000 and on 5,000,000 rows (with two 64-character texts in each row:
    reading them all in would take more than a gigabyte more); the run's peak memory grows by less than 250 MB.
    The checks went through every row: half of the amounts are below 0."""
    def table(n: int) -> Path:
        path = env.root / f"rows_{n}.parquet"
        duckdb.sql(f"""COPY (SELECT range + 1 AS id,
              (CASE WHEN range % 2 = 0 THEN -(range % 1000) ELSE range % 1000 END)::DOUBLE AS amount,
              CASE WHEN range % 1000 = 7 THEN 'N/A' WHEN range % 5000 = 9 THEN '1,234.5'
                   ELSE (range % 997)::VARCHAR || '.25' END AS amount_text,
              ['north', 'south', 'east', 'west'][range % 4 + 1] AS region,
              strftime(DATE '2026-01-01' + (range % 300)::INTEGER, '%Y-%m-%d') AS day,
              md5(range::VARCHAR) || md5((range + 1)::VARCHAR) AS note,
              md5((range + 2)::VARCHAR) || md5((range + 3)::VARCHAR) AS remark
            FROM range({n})) TO '{path.as_posix().replace("'", "''")}' (FORMAT parquet, ROW_GROUP_SIZE 100000)""")
        return path

    def peak(n: int) -> float:
        code = f"from pathlib import Path\noutput = Table(Path({table(n).as_posix()!r}))"
        source = {"type": "logic.python", "config": {"inputs": [], "code": code}, "ui": {"x": -700, "y": 0}}
        check = check_table(row_count(at_least=1), no_missing(), columns_and_types(amount_text="float", warn=True),
                            within(amount="-1000..1000"), fresh("day", "since"), every_row("amount >= 0", warn=True))
        flow = checking(env, source, {"check": check}, port="output",
                        params={"since": {"type": "date", "value": "2026-01-01"}})
        proc = subprocess.run([sys.executable, "-c", MAXRSS, "run", str(flow)], env=env.env, cwd=env.root,
                              capture_output=True, text=True, timeout=600)
        assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
        found = outcome(env, "check")
        got = {name: row_for(found, name)["outcome"] for name in ("row_count", "missing", "types", "values",
                                                                 "freshness", "conditions")}
        assert got == {"row_count": "passed", "missing": "passed", "types": "warned", "values": "passed",
                       "freshness": "passed", "conditions": "warned"}, found["results"]
        assert row_for(found, "conditions")["rows"] == n // 2 - n // 1000, found["results"]
        rss = int(proc.stdout.split("MAXRSS")[1])
        return rss / (1024 * 1024 if sys.platform == "darwin" else 1024)  # (bytes on macOS, kilobytes on Linux)

    small, large = peak(500_000), peak(5_000_000)

    assert large - small < 250, f"peak memory: {small:.0f} MB for 500,000 rows, {large:.0f} MB for 5,000,000"


# --- 7: what Properties and Validate refuse --------------------------------------------------------------------------------

def test_validate_refuses_a_check_table_with_no_check_ticked_or_a_ticked_check_missing_what_it_needs(env):
    """Spec 7 (Properties and Validate refuse a Check Table with no check ticked, or a ticked check missing what it
    needs, e.g. Unique with no column, Freshness with no column, saying which): each such block is named, with the
    check; a Check Table that has what it needs is not."""
    blocks = {"load": read_file(orders(env.root)), "nothing": check_table(), "no_bounds": check_table(row_count()),
              "no_unique_column": check_table(unique()), "no_types": check_table(columns_and_types()),
              "no_ranges": check_table(within()), "no_fresh_column": check_table(fresh()),
              "no_conditions": check_table(every_row()), "fine": check_table(row_count(at_least=1))}
    edges = [f"load.table -> {b}.table" for b in blocks if b != "load"]
    checked = env.taskloom("validate", flow_file(env.root / "flow.yaml", blocks, edges))
    errors = [line for line in checked.stderr.splitlines() if line.startswith("error:")]

    assert checked.returncode != 0, checked.stdout + checked.stderr

    def about(block: str) -> set[str]:
        return words(" ".join(line for line in errors if line.startswith(f"error: {block}:")))

    assert "check" in about("nothing") or "checks" in about("nothing"), errors
    assert about("no_bounds") & {"least", "most", "min", "max", "minimum", "maximum"}, errors
    assert "unique" in about("no_unique_column") and about("no_unique_column") & {"column", "columns"}, errors
    assert about("no_types") & {"type", "types"} and about("no_types") & {"column", "columns"}, errors
    assert about("no_ranges") & {"range", "values", "value"}, errors
    assert about("no_fresh_column") & {"fresh", "freshness"} and "column" in about("no_fresh_column"), errors
    assert about("no_conditions") & {"condition", "conditions"}, errors
    assert about("fine") == set(), errors


def test_properties_says_what_a_check_table_needs_until_it_has_it(win, tmp_path):
    """Spec 7 in Properties: no check ticked is refused; ticking Unique, it says Unique needs a column; with the column
    typed in, nothing is refused."""
    data = orders(tmp_path)
    path = flow_file(tmp_path / "props.yaml", {"load": read_file(data), "check": check_table()},
                     ["load.table -> check.table"])
    tab = win.open_flow(path)
    settle()
    select_only(tab, "check")

    assert words(error_shown(win)) & {"check", "checks"}, error_shown(win)
    tick_box(win, "unique").setChecked(True)
    settle()
    told = error_shown(win)
    assert "unique" in words(told) and words(told) & {"column", "columns"}, told
    type_in(field(win, label_of(only("unique"))), "order_id")
    settle()
    assert error_shown(win) == ""
    clean(win)


def test_each_checks_settings_show_only_while_it_is_ticked_each_check_under_a_line(win, tmp_path):
    """Spec 3 (only a ticked check's settings show in Properties, each check under a line as API Request's groups):
    with only the row count ticked, only its settings show; ticking Freshness shows its settings, under its tick;
    unticking the row count takes its settings away. A line is above each of the eight checks."""
    data = orders(tmp_path)
    path = flow_file(tmp_path / "props.yaml", {"load": read_file(data), "check": check_table(row_count(at_least=1))},
                     ["load.table -> check.table"])
    tab = win.open_flow(path)
    settle()
    select_only(tab, "check")
    win.right_tabs.setCurrentWidget(win.properties)
    settle()

    assert shown_settings(win) == settings_for({"row_count"})
    tick_box(win, "freshness").setChecked(True)
    settle()
    assert shown_settings(win) == settings_for({"row_count", "freshness"})

    body = win.properties.widget()

    def y(widget) -> int:
        return widget.mapTo(body, QPoint(0, widget.height() // 2)).y()

    lines = [y(w) for w in body.findChildren(QFrame) if w.frameShape() == QFrame.HLine and not w.isHidden()]
    tick_labels = {label_of(key): name for name, key in ticks().items()}
    at = {}
    for text, widget in shown_texts(win):
        if text in tick_labels:
            at[tick_labels[text]] = min(at.get(tick_labels[text], y(widget)), y(widget))
    assert set(at) == set(NAMES), sorted(at)
    order = sorted(at, key=at.get)
    labels = setting_labels()
    for i, name in enumerate(order):
        above = at[order[i - 1]] if i else -10 ** 6
        below = at[order[i + 1]] if i + 1 < len(order) else 10 ** 6
        assert any(above < line < at[name] for line in lines), f"no line above the {name} check"
        under = Counter(text for text, widget in shown_texts(win) if text in labels and at[name] < y(widget) < below)
        assert under == settings_for({name} & {"row_count", "freshness"}), f"under the {name} check: {under}"

    tick_box(win, "row_count").setChecked(False)
    settle()
    assert shown_settings(win) == settings_for({"freshness"})
    clean(win)


# --- 8: README and Help > Documentation -----------------------------------------------------------------------------------

def test_the_readme_and_the_documentation_describe_the_block_and_each_check_with_an_example():
    """Spec 8 (Help > Documentation, the hover help and the README describe the block and each check, with an example:
    Read File, then Check Table, then the blocks that use the table) and 3h (the help says a missing value breaks a
    condition: write amount is null or amount >= 0 to allow it): README lists the block, and its section about Check
    Table (under a heading naming it, as API Request and Session have) speaks of each check and of warning, with a YAML
    example the block accepts; Help > Documentation has its entry on the Checks page, every check's tick with its
    hover help, and a guide page showing the example."""
    from taskloom.editor import docs

    cls = check_class()
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    listed = re.search(r"Built-in blocks:(.*?)\n\n", readme, re.S)
    assert listed and f"`{CHECK}`" in listed.group(1), "README's list of built-in blocks does not have Check Table"
    section, level, fenced = [], None, False
    for line in readme.splitlines():  # (a line starting with # inside a code block is no heading)
        heading = None if fenced or line.startswith("```") else re.match(r"(#{1,6}) ", line)
        fenced ^= line.startswith("```")
        if level is None:
            level = len(heading.group(1)) if heading and "Check Table" in line else None
        elif heading and len(heading.group(1)) <= level:
            break
        else:
            section.append(line)
    assert level is not None, "README has no heading naming Check Table"
    about = "\n".join(section)
    text = " ".join(about.lower().split())
    spoken = {"row_count": ("row count",), "compared": ("last successful run", "earlier run", "previous run"),
              "missing": ("missing",), "unique": ("unique",), "types": ("type",), "values": ("range",),
              "freshness": ("fresh",), "conditions": ("condition",)}
    assert [n for n, ways in spoken.items() if not any(w in text for w in ways)] == [], about[:3000]
    assert "warn" in text, about[:3000]
    examples = [yaml.safe_load(code) for code in re.findall(r"```ya?ml\n(.*?)```", about, re.S) if CHECK in code]

    def reads_checks_and_uses(flow) -> bool:
        if not isinstance(flow, dict) or not isinstance(flow.get("blocks"), dict):
            return False
        blocks, edges = flow["blocks"], [" ".join(str(e).split()) for e in flow.get("edges") or []]
        checks = [b for b, spec in blocks.items() if isinstance(spec, dict) and spec.get("type") == CHECK]
        for b in checks:
            block_config(cls, BlockSpec(id=b, type=CHECK, config=dict(blocks[b].get("config") or {})))
        reads = [b for b, spec in blocks.items() if isinstance(spec, dict) and spec.get("type") == "data.read_file"]
        return any(any(f"{r}.table -> {c}.table" in edges for r in reads)
                   and any(e.startswith(f"{c}.table -> ") for e in edges) for c in checks)

    assert any(reads_checks_and_uses(flow) for flow in examples), (
        "README has no YAML example of Read File, then Check Table, then a block using its table")

    pages = docs.pages(discover(None))
    page = pages.get("blocks-checks.html", "")
    assert f'id="{CHECK}"' in page and html.escape(cls.description) in page
    for name, key in ticks().items():
        assert cls.config[key].help.strip(), f"the tick {label_of(key)} has no hover help"
        assert f'id="{CHECK}.{key}"' in page, f"Help > Documentation does not describe {label_of(key)}"
    helps = " ".join(cls.config[k].help for k in (ticks()["conditions"], *settings_of("conditions")))
    assert "is null or" in " ".join(helps.split()), helps
    guides = [p for name, p in pages.items()
              if name not in ("index.html", "properties.html") and not name.startswith("blocks-") and CHECK in p]
    assert guides, "no guide page of Help > Documentation shows the Check Table example"
