"""The Check File block (checks.check_file), in the Checks group: it checks that a file, or the files matching a pattern,
is there as expected. It is there (always checked; If none is there: fail the block, the default, or warn), Not empty
(ticked in a new block), Modified since an expression (default today()) and Size (a minimum and/or a maximum, with a
unit: B, KB, MB, GB, 1 KB = 1024 bytes); each ticked check fails the block (the default) or only warns in the Log. Its
outputs: `files` (the paths found, a list) and `results` (a table, a row per check), its error output carrying
`results` too; with the block's Retry set, a failed check is checked again later, so it can wait for a file that
arrives late. Through the real `taskloom run` on real files (their sizes and times set by each test), the run history
(history.db), a real SSH server, the offscreen editor (the Blocks list, the canvas, Properties), README.md and Help >
Documentation. Each test names the point of the specification (1-7, or the no-surprises rule) it protects.

What the tests take as given (the specification leaves it to the change):
- Settings are found by the labels Properties shows (block.setting_label): "Path on" (fields.Server) and "Path"
  exactly. "If none is there" is the one choice (fields.Choice) whose label has the word "none". Each other check is a
  tick (fields.Bool) whose label has the word "empty" (Not empty), "modified" (Modified since) or "size" (Size); a
  check's own settings are those shown only while its tick is ticked (fields.Field.only_if {tick: [True]}): its "If it
  fails" (a choice), for Modified since its expression (its one other setting), for Size its minimum (a label with
  min, minimum, least or smallest) and its maximum (max, maximum, most or largest). A choice's options are found by the
  words "fail" and "warn". Every tick is written in the flow files here (ticked or not), the rest left out.
- `results` has the columns check, outcome (passed, failed or warned) and details; a row's check names its check with
  the word "there", "empty", "modified" or "size". An unticked check has no row (it has no outcome).
- In the Log a check's line has its word and its outcome (pass, fail or warn) in it, a line of its own; a warned
  check's line is a warning.
- A time is shown as YYYY-MM-DD HH:MM (seconds may follow), in the flow's time zone when it has one, else this
  computer's. A size that breaks a Size limit is named by its exact number of bytes (thousands may be separated); the
  Log may show a size with a unit (7 B, 2 KB).
- The icon is Tabler's outline "file-check" (the specification's example)."""

import datetime as dt
import html
import itertools
import json
import os
import posixpath
import re
import sys
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QCheckBox  # noqa: E402

from taskloom.block import fields, setting_label  # noqa: E402
from taskloom.flow import BlockSpec, Retry, block_config  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_block_icons import canvas_icons, drawing, unlike  # noqa: E402
from tests.test_block_icons import rows as list_rows  # noqa: E402
from tests.test_editor import add_from_palette  # noqa: E402
from tests.test_paths_editor_editing import clean, field, flow_file, select_only, settle, taskloom, type_in  # noqa: E402
from tests.test_paths_engine import Running  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TYPE = "checks.check_file"
CHECKS = ("there", "empty", "modified", "size")
CSV = b"day,total\n1,10\n"
# Tabler Icons' "file-check" (outline), as in its SVG: a page with a folded corner and a check mark on it.
TABLER_FILE_CHECK = ('<path d="M14 3v4a1 1 0 0 0 1 1h4"/>'
                     '<path d="M17 21h-10a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2h7l5 5v11a2 2 0 0 1 -2 2z"/>'
                     '<path d="M9 15l2 2l4 -4"/>')
SERVER = pytest.mark.skipif(sys.platform == "win32", reason="the SSH test server maps its paths as POSIX paths")


# --- the block, found by what Properties calls its settings -----------------------------------------------------------

@cache
def check_class():
    cls = discover(None).get(TYPE)
    assert cls is not None, f"there is no {TYPE} block (Check File)"
    return cls


def label(key_: str) -> str:
    return setting_label(key_, check_class().config[key_])


def words(text) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def _one(found: list, what: str) -> str:
    shown = [label(k) for k in check_class().config]
    assert len(found) == 1, f"Check File should have one {what}: {[label(k) for k in found]} (its settings: {shown})"
    return found[0]


def key(shown: str) -> str:
    """The key, in the flow file, of the setting Properties shows as `shown`."""
    return _one([k for k in check_class().config if label(k) == shown], f"setting labelled {shown!r}")


def tick(check: str) -> str:
    """The tick of a check ("empty", "modified" or "size")."""
    config = check_class().config
    return _one([k for k, f in config.items() if isinstance(f, fields.Bool) and check in words(label(k))],
                f"tick whose label has {check!r}")


def own(tick_key: str) -> list[str]:
    """The settings shown only while the tick `tick_key` is ticked."""
    return [k for k, f in check_class().config.items()
            if True in (f.only_if.get(tick_key) or ()) and False not in (f.only_if.get(tick_key) or ())]


def if_fails(check: str) -> str:
    """The choice of what a check's failure does: If none is there, or the If it fails of a ticked check."""
    config = check_class().config
    if check == "there":
        return _one([k for k, f in config.items() if isinstance(f, fields.Choice) and "none" in words(label(k))],
                    'choice whose label has "none" (If none is there)')
    return _one([k for k in own(tick(check)) if isinstance(config[k], fields.Choice)],
                f"If it fails choice shown while {label(tick(check))} is ticked")


def option(key_: str, word: str) -> str:
    options = getattr(check_class().config[key_], "options", None) or []
    return _one([o for o in options if word in str(o).lower()], f"option of {label(key_)} with {word!r}: {options}")


def expression() -> str:
    """The setting for Modified since's expression."""
    config = check_class().config
    return _one([k for k in own(tick("modified")) if not isinstance(config[k], fields.Choice)],
                "setting for the expression of Modified since (other than If it fails)")


def size_limit(which: set[str]) -> str:
    config = check_class().config
    return _one([k for k in own(tick("size")) if not isinstance(config[k], fields.Choice) and words(label(k)) & which],
                f"Size setting whose label has one of {sorted(which)}")


def minimum() -> str:
    return size_limit({"min", "minimum", "least", "smallest"})


def maximum() -> str:
    return size_limit({"max", "maximum", "most", "largest"})


def check_file(path: str, *, not_empty=False, modified=None, size=None, warn=(), server=None, **spec) -> dict:
    """A Check File block on `path`. modified: None (unticked), True (ticked, its expression left out) or the
    expression; size: (minimum, maximum), either None; warn: the checks set to only warn."""
    config = {key("Path"): path, tick("empty"): not_empty, tick("modified"): modified is not None,
              tick("size"): size is not None}
    if isinstance(modified, str):
        config[expression()] = modified
    if size is not None:
        for limit, value in zip((minimum(), maximum()), size):
            if value is not None:
                config[limit] = value
    for check in warn:
        config[if_fails(check)] = option(if_fails(check), "warn")
    if server is not None:
        config[key("Path on")] = server
    return {"type": TYPE, "config": config, **spec}


# --- the blocks around it, the flow, the files and the run history ------------------------------------------------------

KEEP = """\
import json

def plain(value):
    if isinstance(value, Table):
        return {"table": value.scan().collect().to_dicts()}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value if value is None or isinstance(value, (str, int, float, bool)) else repr(value)

with open(%r, "w", encoding="utf-8") as f:
    json.dump({%s}, f)
"""


def keep(name: str, *inputs: str) -> dict:
    """A Python Code block that writes what reaches its inputs to <name>.json, as {input: value}, a table as
    {"table": its rows}."""
    pairs = ", ".join(f"{i!r}: plain({i})" for i in inputs)
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": [], "code": KEEP % (f"{name}.json", pairs)}}


def kept(env, name: str) -> dict:
    return json.loads((env.root / f"{name}.json").read_text(encoding="utf-8"))


def read_file(path: str) -> dict:
    return {"type": "data.read_file", "config": {"path": path}}


def put(path: Path, data: bytes, when: float | None = None) -> Path:
    """A file with exactly these bytes, last changed at `when` (a timestamp) if given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if when is not None:
        os.utime(path, (when, when))
    return path


def local(*parts) -> float:
    """A time on this computer's clock, as a timestamp."""
    return dt.datetime(*parts).timestamp()


def run(env, blocks: dict, edges=(), as_of: str | None = None, **top):
    """`taskloom run` of the flow (next to the files it checks): its result, the run's number (None: no run began) and
    each block's status."""
    path = flow_file(env.root / "flow.yaml", blocks, edges, **top)
    result = env.taskloom("run", path, *(["--as-of", as_of] if as_of else []))
    found = env.query("SELECT id FROM runs ORDER BY id DESC LIMIT 1") if (env.home / "history.db").exists() else []
    run_id = found[0][0] if found else None
    return result, run_id, (env.block_status(run_id) if run_id is not None else {})


def told(result) -> str:
    return result.stdout[-3000:] + result.stderr[-3000:]


def error_of(env, run_id: int, block: str) -> str:
    return env.query("SELECT error FROM blocks WHERE run_id=? AND block=?", run_id, block)[0][0] or ""


def output(env, run_id: int, block: str, port: str):
    """What a block put out, as the run history (and the editor's Data preview) holds it."""
    (previews,), = env.query("SELECT previews FROM blocks WHERE run_id=? AND block=?", run_id, block)
    return json.loads(json.loads(previews)[port])


def attempts(env, run_id: int) -> dict:
    return dict(env.query("SELECT block, attempts FROM blocks WHERE run_id=?", run_id))


def logs_of(env, run_id: int, block: str) -> list[tuple[str, str]]:
    return env.query("SELECT level, message FROM logs WHERE run_id=? AND block=? ORDER BY rowid", run_id, block)


def same_files(root: Path, found: list, *expected: Path) -> bool:
    """Whether the paths a block found (a relative one is in the flow's folder, `root`) are these files, in order."""
    return len(found) == len(expected) and all(os.path.samefile(root / f, e) for f, e in zip(found, expected))


def outcomes(results) -> dict:
    """{check: outcome} of a `results` table as keep() wrote it, each check's row found by its word."""
    assert isinstance(results, dict) and "table" in results, f"results is not a table: {results!r}"
    found = {}
    for row in results["table"]:
        assert {"check", "outcome", "details"} <= set(row), f"results has no check, outcome and details: {row}"
        kinds = [c for c in CHECKS if c in words(row["check"])]
        assert len(kinds) == 1, f"which check is this row of results for? {row}"
        assert kinds[0] not in found, f"two rows of results for one check: {results['table']}"
        found[kinds[0]] = row["outcome"]
    return found


def details(results, check: str) -> str:
    [row] = [r for r in results["table"] if check in words(r["check"])]
    return str(row["details"])


def check_lines(lines: list[tuple[str, str]], expected: dict) -> dict:
    """Each check's line of the Log, {check: (level, message)}: one with the check's word and its outcome's (pass,
    fail, warn), a line of its own for each check; a warned check's is a warning."""
    stems = {"passed": "pass", "failed": "fail", "warned": "warn"}
    candidates = [[i for i, (level, message) in enumerate(lines)
                   if check in words(message) and stems[outcome] in message.lower()
                   and (outcome != "warned" or level == "WARNING")]
                  for check, outcome in expected.items()]
    for chosen in itertools.product(*candidates):
        if len(set(chosen)) == len(chosen):
            return dict(zip(expected, (lines[i] for i in chosen)))
    raise AssertionError(f"the Log has no line of its own for each check ({expected}): {lines}")


def size_in_bytes(n: int) -> str:
    """A regular expression for n bytes as an exact number (thousands may be separated by , . or a space)."""
    digits = f"{n:,}".split(",")
    return r"(?<![\d.,])" + r"[,.   ]?".join(digits) + r"(?![\d])"


def time_shown(when: str) -> str:
    """A regular expression for a time written YYYY-MM-DD HH:MM (a T between them, and seconds after, allowed)."""
    day, clock = when.split(" ")
    return rf"{day}[ T]{clock}"


# --- 1: the block, in the Checks group, with Tabler's file-check icon -----------------------------------------------------

def test_check_file_is_in_the_checks_group_and_explains_itself_on_hover(win):
    """Spec 1 (block type checks.check_file, title "Check File", category "Checks") and 7 (the hover help describes the
    block): the Blocks list shows Check File in the Checks group, and resting on it shows what it does."""
    cls = check_class()
    assert (cls.title, cls.category) == ("Check File", "Checks")
    groups = list_rows(win.palette_panel)
    checks = {item.text(0): item for item in groups.get("Checks", [])}
    assert "Check File" in checks, f"the Checks group of the Blocks list: {sorted(checks)} (groups: {sorted(groups)})"
    tip = html.unescape(re.sub(r"<[^>]+>", " ", checks["Check File"].toolTip(0)))
    assert cls.description and " ".join(cls.description.split()) in " ".join(tip.split())
    assert "file" in words(cls.description) or "files" in words(cls.description), cls.description


def test_its_icon_is_tablers_file_check_drawing(win, tmp_path):
    """Spec 1 (an icon from Tabler's outline set fitting a checked file, e.g. "file-check"): of every built-in block's
    icon on the canvas, Check File's is the most like Tabler's file-check drawing (a page with a check mark)."""
    from taskloom.editor.canvas import BLOCK_ICONS, CATEGORY_COLORS, USER_COLOR

    check_class()
    registry = win.registry
    colors = {t: CATEGORY_COLORS.get(cls.category, USER_COLOR) for t, cls in registry.blocks.items()
              if registry.sources[t] == "built-in" and (t == TYPE or t in BLOCK_ICONS)}
    icons = canvas_icons(win, tmp_path, colors)
    reference = drawing(TABLER_FILE_CHECK)
    closest = min(icons, key=lambda t: unlike(icons[t], reference))
    assert closest == TYPE, (f"Tabler's file-check drawing is most like the icon of {closest} "
                             f"({unlike(icons[closest], reference):.3f} against {unlike(icons[TYPE], reference):.3f})")


def test_a_check_file_added_in_the_editor_has_not_empty_ticked(win, tmp_path):
    """Spec 3b (Not empty, ticked in a new block) and 2 (Path): Check File added from the Blocks list shows Not empty
    ticked in Properties; with its Path typed there and the flow saved, its run fails on an empty file and passes once
    the file has something in it."""
    check_class()
    tab = win.new_flow()
    settle()
    try:
        add_from_palette(win, "Check File")
        settle()
        [block] = [b for b, spec in tab.doc.blocks.items() if spec.get("type") == TYPE]
        select_only(tab, block)
        box = field(win, label(tick("empty")))
        assert isinstance(box, QCheckBox) and box.isChecked(), f"{label(tick('empty'))} is not ticked in a new block"
        type_in(field(win, "Path"), "inbox/report.csv")
        tab.doc.connect("start", "start", block, "after")
        path = tmp_path / "new.yaml"
        tab.doc.save(path)
    finally:
        clean(win)

    put(tmp_path / "inbox" / "report.csv", b"")
    empty = taskloom(win, "run", path, cwd=tmp_path)
    put(tmp_path / "inbox" / "report.csv", CSV)
    filled = taskloom(win, "run", path, cwd=tmp_path)

    assert empty.returncode == 1, told(empty)
    assert "report.csv" in told(empty)
    assert filled.returncode == 0, told(filled)


# --- 2, 3a and 4: what is there, the files output and the results ----------------------------------------------------------

def test_a_file_that_is_there_passes_every_ticked_check_and_read_file_reads_it(env):
    """Spec 2 (Path: a file), 3 (each ticked check passes), 4 (files: the paths found, a list; results: a table, a row
    per check: check, outcome, details) and the use it is for (Check File before a Read File)."""
    sales = put(env.root / "inbox" / "sales.csv", CSV, local(2026, 10, 2, 8, 0))
    blocks = {"check": check_file("inbox/sales.csv", not_empty=True, modified=True, size=("1 B", "1 MB")),
              "read": read_file("inbox/sales.csv"), "keep": keep("checked", "files", "results"),
              "keep_read": keep("read", "table")}
    edges = ["check.files -> read.after", "check.files -> keep.files", "check.results -> keep.results",
             "read.table -> keep_read.table"]
    result, run_id, status = run(env, blocks, edges, as_of="2026-10-02 09:00")

    assert status == {b: "success" for b in blocks}, told(result)
    got = kept(env, "checked")
    assert isinstance(got["files"], list) and same_files(env.root, got["files"], sales), got["files"]
    assert outcomes(got["results"]) == {"there": "passed", "empty": "passed", "modified": "passed", "size": "passed"}
    assert kept(env, "read")["table"] == {"table": [{"day": 1, "total": 10}]}


def test_a_pattern_finds_only_the_matching_files_and_a_folder_does_not_count(env):
    """Spec 2 (a pattern in the last part of Path; a folder is not a file: it does not count as one found) and 4 (files:
    the paths found): only the files of that folder whose name matches are found, not a folder whose name matches,
    nor files in its subfolders; a Path naming a folder, or a pattern matching only folders, finds nothing there."""
    inbox = env.root / "inbox"
    first, second = put(inbox / "sales_1.csv", CSV), put(inbox / "sales_2.csv", CSV)
    put(inbox / "orders_1.csv", CSV)
    put(inbox / "sub" / "sales_3.csv", CSV)
    put(inbox / "sales_old.csv" / "inside.csv", CSV)  # a folder named like a file
    blocks = {"pattern": check_file("inbox/sales_*.csv", not_empty=True),
              "folder": check_file("inbox/sales_old.csv"),
              "only_folders": check_file("inbox/sales_old*")}
    result, run_id, status = run(env, blocks)

    assert status == {"pattern": "success", "folder": "failed", "only_folders": "failed"}, told(result)
    found = output(env, run_id, "pattern", "files")
    assert same_files(env.root, found, first, second), found
    assert "sales_old.csv" in error_of(env, run_id, "folder")
    assert "sales_old*" in error_of(env, run_id, "only_folders")


def test_nothing_there_fails_the_block_by_default_and_its_error_output_carries_the_results(env):
    """Spec 3a (It is there: always checked; If none is there: fail the block, the default) and 4 (the error output
    carries `results` too): nothing at the path or matching the pattern fails the block naming what it looked for,
    the block after it does not run, and the error handler gets the error and the results."""
    put(env.root / "inbox" / "notes.txt", b"notes")
    blocks = {"check": check_file("inbox/sales_*.csv", not_empty=True), "read": read_file("inbox/sales_1.csv"),
              "handle": keep("failure", "input"), "missing_file": check_file("inbox/sales.csv")}
    edges = ["check.files -> read.after", "check.error -> handle.input"]
    result, run_id, status = run(env, blocks, edges)

    assert result.returncode == 1, told(result)
    assert status == {"check": "failed", "read": "skipped", "handle": "success", "missing_file": "failed"}, told(result)
    assert "sales_*.csv" in error_of(env, run_id, "check")
    assert "sales.csv" in error_of(env, run_id, "missing_file")
    failure = kept(env, "failure")["input"]
    assert isinstance(failure, dict) and "sales_*.csv" in str(failure.get("error")), failure
    assert outcomes(failure.get("results")).get("there") == "failed", failure


def test_nothing_there_set_to_warn_only_warns_and_the_flow_goes_on(env):
    """Spec 3a (If none is there: warn), 4 (files: an empty list; results: It is there warned) and 5 (the Log has its
    line, a warning): the block succeeds, the next block runs."""
    put(env.root / "inbox" / "notes.txt", b"notes")
    blocks = {"check": check_file("inbox/sales_*.csv", not_empty=True, warn=("there",)),
              "keep": keep("after", "files", "results")}
    edges = ["check.files -> keep.files", "check.results -> keep.results"]
    result, run_id, status = run(env, blocks, edges)

    assert status == {"check": "success", "keep": "success"}, told(result)
    got = kept(env, "after")
    assert got["files"] == []
    assert outcomes(got["results"]).get("there") == "warned", got["results"]
    check_lines(logs_of(env, run_id, "check"), {"there": "warned"})


# --- 3b, 3c, 3d: the checks, on each file found ----------------------------------------------------------------------------

def test_not_empty_fails_naming_each_empty_file_with_its_size(env):
    """Spec 3b (Not empty: every file found has more than 0 bytes), 3 (with a pattern each file is checked; a failure
    names each file that breaks it with its size), 4 (results in the error output: a row per ticked check) and 5 (a
    line per check in the Log); unticked, the same files pass."""
    put(env.root / "inbox" / "first.csv", b"")
    put(env.root / "inbox" / "second.csv", CSV)
    put(env.root / "inbox" / "third.csv", b"")
    blocks = {"check": check_file("inbox/*.csv", not_empty=True), "handle": keep("failure", "input"),
              "unticked": check_file("inbox/*.csv", not_empty=False)}
    result, run_id, status = run(env, blocks, ["check.error -> handle.input"])

    assert status == {"check": "failed", "handle": "success", "unticked": "success"}, told(result)
    error = error_of(env, run_id, "check")
    assert "first.csv" in error and "third.csv" in error, error
    assert re.search(r"(?<![\d.,])0\s*(B|bytes?)\b", error, re.I), f"the empty files' size (0 bytes) is not told: {error}"
    results = kept(env, "failure")["input"].get("results")
    assert outcomes(results) == {"there": "passed", "empty": "failed"}, results
    said = details(results, "empty")
    assert "first.csv" in said and "third.csv" in said and "second.csv" not in said, said
    check_lines(logs_of(env, run_id, "check"), {"there": "passed", "empty": "failed"})


def test_modified_since_is_today_by_default_and_names_each_older_file_with_its_time(env):
    """Spec 3c (Modified since: every file found was last changed at or after an expression, default today()) and 3 (a
    failure names each file that breaks it with its time): at 09:00, a file changed at 00:30 today passes, one changed
    yesterday at 23:30 and one a few days ago fail, each named with its time; one changed at midnight exactly passes."""
    put(env.root / "inbox" / "today.csv", CSV, local(2026, 10, 2, 0, 30))
    put(env.root / "inbox" / "yesterday.csv", CSV, local(2026, 10, 1, 23, 30))
    put(env.root / "inbox" / "older.csv", CSV, local(2026, 9, 28, 12, 0))
    put(env.root / "edge" / "midnight.csv", CSV, local(2026, 10, 2, 0, 0))
    blocks = {"check": check_file("inbox/*.csv", modified=True), "today_only": check_file("inbox/today.csv", modified=True),
              "midnight": check_file("edge/midnight.csv", modified=True)}
    result, run_id, status = run(env, blocks, as_of="2026-10-02 09:00")

    assert status == {"check": "failed", "today_only": "success", "midnight": "success"}, told(result)
    error = error_of(env, run_id, "check")
    assert "yesterday.csv" in error and re.search(time_shown("2026-10-01 23:30"), error), error
    assert "older.csv" in error and re.search(time_shown("2026-09-28 12:00"), error), error
    assert "today.csv" not in error.replace("yesterday.csv", ""), error


def test_modified_since_takes_expressions_with_the_run_functions_and_the_flow_parameters(env):
    """Spec 3c (an expression, e.g. now() - days(1), today() - bdays(1), evaluated as If's conditions are, with the flow
    parameters): on Monday 2026-10-05 at 09:00, a day ago is Sunday 09:00, a business day ago is Friday, and a date
    parameter is that date; a file changed before each fails, one changed after passes."""
    put(env.root / "day" / "after.csv", CSV, local(2026, 10, 4, 10, 0))
    put(env.root / "day" / "before.csv", CSV, local(2026, 10, 4, 8, 0))
    put(env.root / "bday" / "friday.csv", CSV, local(2026, 10, 2, 7, 0))
    put(env.root / "bday" / "thursday.csv", CSV, local(2026, 10, 1, 22, 0))
    put(env.root / "param" / "on_the_day.csv", CSV, local(2026, 9, 30, 1, 0))
    put(env.root / "param" / "the_day_before.csv", CSV, local(2026, 9, 29, 23, 0))
    blocks = {"a_day": check_file("day/*.csv", modified="now() - days(1)"),
              "a_day_ok": check_file("day/after.csv", modified="now() - days(1)"),
              "a_bday": check_file("bday/*.csv", modified="today() - bdays(1)"),
              "a_bday_ok": check_file("bday/friday.csv", modified="today() - bdays(1)"),
              "a_param": check_file("param/*.csv", modified="since"),
              "a_param_ok": check_file("param/on_the_day.csv", modified="since")}
    result, run_id, status = run(env, blocks, as_of="2026-10-05 09:00",
                                 params={"since": {"type": "date", "value": dt.date(2026, 9, 30)}})

    assert status == {"a_day": "failed", "a_day_ok": "success", "a_bday": "failed", "a_bday_ok": "success",
                      "a_param": "failed", "a_param_ok": "success"}, told(result)
    assert "before.csv" in error_of(env, run_id, "a_day")
    assert "thursday.csv" in error_of(env, run_id, "a_bday")
    assert "the_day_before.csv" in error_of(env, run_id, "a_param")


def test_modified_since_reads_the_times_in_the_flows_time_zone(env):
    """Spec 3c (evaluated with the flow's time zone): with the flow in a time zone that is not this computer's, today()
    starts at midnight there; a file changed an hour after it passes, one changed an hour before it fails, named with
    its time there."""
    zone = time_zone_not_this_computers(dt.datetime(2026, 10, 2, 0, 0, tzinfo=dt.timezone.utc))
    midnight = dt.datetime(2026, 10, 2, tzinfo=ZoneInfo(zone))
    put(env.root / "inbox" / "early.csv", CSV, (midnight + dt.timedelta(hours=1)).timestamp())
    put(env.root / "inbox" / "late.csv", CSV, (midnight - dt.timedelta(hours=1)).timestamp())
    blocks = {"check": check_file("inbox/*.csv", modified="today()"),
              "early_only": check_file("inbox/early.csv", modified="today()")}
    result, run_id, status = run(env, blocks, as_of=(midnight + dt.timedelta(hours=9)).isoformat(), timezone=zone)

    assert status == {"check": "failed", "early_only": "success"}, told(result)
    error = error_of(env, run_id, "check")
    assert "late.csv" in error and "early.csv" not in error, error
    assert re.search(time_shown("2026-10-01 23:00"), error), f"late.csv's time in {zone} is not told: {error}"


def time_zone_not_this_computers(at: dt.datetime) -> str:
    for zone in ("Asia/Tokyo", "America/Chicago"):
        if at.astimezone(ZoneInfo(zone)).utcoffset() != at.astimezone().utcoffset():
            return zone
    raise AssertionError("unreachable: this computer is in two time zones at once")


def test_size_has_a_minimum_and_a_maximum_with_units_and_1_kb_is_1024_bytes(env):
    """Spec 3d (Size: at least a minimum and/or at most a maximum, written with a unit: B, KB, MB, GB, 1 KB = 1024
    bytes) and 3 (a failure names each file that breaks it with its size): the limits are inclusive."""
    put(env.root / "kb" / "tiny.bin", b"x" * 1023)
    put(env.root / "kb" / "exact.bin", b"x" * 1024)
    put(env.root / "kb" / "double.bin", b"x" * 2048)
    put(env.root / "kb" / "over.bin", b"x" * 2049)
    put(env.root / "within" / "exact.bin", b"x" * 1024)
    put(env.root / "within" / "double.bin", b"x" * 2048)
    put(env.root / "mb" / "full.bin", b"x" * (1 << 20))
    put(env.root / "under_mb" / "short.bin", b"x" * ((1 << 20) - 1))
    put(env.root / "b" / "small.bin", b"x" * 101)
    blocks = {"at_least": check_file("kb/*.bin", size=("1 KB", None)),
              "at_most": check_file("kb/*.bin", size=(None, "2 KB")),
              "within": check_file("within/*.bin", size=("1 KB", "2 KB")),
              "one_mb": check_file("mb/full.bin", size=("1 MB", None)),
              "under_one_mb": check_file("under_mb/short.bin", size=("1 MB", None)),
              "bytes": check_file("b/small.bin", size=(None, "100 B")),
              "gb_max": check_file("kb/*.bin", size=(None, "1 GB")),
              "gb_min": check_file("b/small.bin", size=("1 GB", None))}
    result, run_id, status = run(env, blocks)

    assert status == {"at_least": "failed", "at_most": "failed", "within": "success", "one_mb": "success",
                      "under_one_mb": "failed", "bytes": "failed", "gb_max": "success", "gb_min": "failed"}, told(result)
    at_least, at_most = error_of(env, run_id, "at_least"), error_of(env, run_id, "at_most")
    assert "tiny.bin" in at_least and re.search(size_in_bytes(1023), at_least), at_least
    assert "over.bin" in at_most and re.search(size_in_bytes(2049), at_most), at_most
    under = error_of(env, run_id, "under_one_mb")
    assert "short.bin" in under and re.search(size_in_bytes((1 << 20) - 1), under), under
    assert "small.bin" in error_of(env, run_id, "bytes")


@pytest.mark.parametrize("check, which, value", [("size", "minimum", "ten MB"), ("size", "maximum", "5 TB"),
                                                 ("modified", "expression", "todya()"),
                                                 ("modified", "expression", "'last week'")])
def test_a_limit_written_wrong_fails_clearly_naming_the_setting(env, check, which, value):
    """The no-surprises rule (when something is wrong it fails clearly; no silent fallbacks), 3c and 3d: a size that is
    no size with a unit, or a Modified since that is no time, never lets the files pass: the run fails (or is refused)
    and says which setting and what was written."""
    put(env.root / "inbox" / "sales.csv", CSV)
    setting = {"minimum": minimum, "maximum": maximum, "expression": expression}[which]()
    block = check_file("inbox/sales.csv", modified=True if check == "modified" else None,
                       size=(None, None) if check == "size" else None)
    block["config"][setting] = value
    result, run_id, status = run(env, {"check": block})

    assert result.returncode != 0 and status.get("check") != "success", told(result)
    message = told(result) + (error_of(env, run_id, "check") if status.get("check") == "failed" else "")
    named = {label(setting).lower(), label(tick(check)).lower()}
    assert any(n in message.lower() for n in named), f"neither {named} is named: {message}"
    assert value.strip("'()") in message, message


# --- 5: the Log --------------------------------------------------------------------------------------------------------------

def test_the_log_lists_each_file_with_its_size_and_time_and_has_a_line_per_check(env):
    """Spec 5 (the Log lists each file found with its size and when it was last changed, and has a line per check:
    passed, failed or warned), 3 (If it fails: warn) and 4 (results: outcome warned): a Size set to warn that b.csv
    breaks warns, naming it, and the block succeeds."""
    put(env.root / "inbox" / "a.csv", b"x" * 7, local(2026, 10, 2, 8, 15))
    put(env.root / "inbox" / "b.csv", b"x" * 2048, local(2026, 10, 2, 8, 45))
    blocks = {"check": check_file("inbox/*.csv", not_empty=True, modified=True, size=(None, "1 KB"), warn=("size",)),
              "keep": keep("checked", "results")}
    result, run_id, status = run(env, blocks, ["check.results -> keep.results"], as_of="2026-10-02 09:00")

    assert status == {"check": "success", "keep": "success"}, told(result)
    lines = logs_of(env, run_id, "check")
    messages = [m for _, m in lines]
    seven = r"(?<![\d.,])7\s*(B|bytes?)\b"
    two_kb = size_in_bytes(2048) + r"|(?<![\d.,])2(\.0+)?\s*KB\b"
    assert any("a.csv" in m and re.search(seven, m, re.I) and re.search(time_shown("2026-10-02 08:15"), m)
               for m in messages), f"no line lists a.csv with its size (7 B) and time (2026-10-02 08:15): {messages}"
    assert any("b.csv" in m and re.search(two_kb, m, re.I) and re.search(time_shown("2026-10-02 08:45"), m)
               for m in messages), f"no line lists b.csv with its size (2 KB) and time (2026-10-02 08:45): {messages}"
    check_lines(lines, {"there": "passed", "empty": "passed", "modified": "passed", "size": "warned"})
    warnings = [m for level, m in lines if level == "WARNING"]
    assert any("b.csv" in m for m in warnings) and not any("a.csv" in m for m in warnings), warnings
    assert outcomes(kept(env, "checked")["results"]) == {"there": "passed", "empty": "passed", "modified": "passed",
                                                         "size": "warned"}


# --- 6: Retry ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("arrives", ["missing at first", "empty at first"])
def test_with_retry_it_waits_for_a_file_that_arrives_late(env, arrives):
    """Spec 6 (with the block's Retry set, a failed check is checked again later, so Check File can wait for a file that
    arrives late): the file is not there yet, or is there but still empty, when the first attempt checks; it arrives
    while the block waits to try again, and the next attempt passes, then Read File reads it."""
    target = env.root / "inbox" / "sales.csv"
    (env.root / "inbox").mkdir()
    if arrives == "empty at first":
        put(target, b"")
    blocks = {"check": check_file("inbox/sales.csv", not_empty=True,
                                  retry={"policy": "fixed", "delay": "1s", "max_attempts": 20}),
              "read": read_file("inbox/sales.csv"), "keep": keep("read", "table")}
    path = flow_file(env.root / "flow.yaml", blocks, ["check.files -> read.after", "read.table -> keep.table"])
    with Running(env, path, "--json-events") as running:
        running.wait_for('"level": "RETRY"', timeout=60)  # the first attempt failed: it waits to check again
        put(target, CSV)
        code = running.finish(timeout=60)

    assert code == 0, "".join(running.out)[-3000:] + "".join(running.err)[-3000:]
    run_id, _ = env.last_run()
    assert env.block_status(run_id) == {"check": "success", "read": "success", "keep": "success"}
    assert attempts(env, run_id)["check"] >= 2
    assert kept(env, "read")["table"] == {"table": [{"day": 1, "total": 10}]}


def test_a_check_set_to_warn_never_retries_and_a_check_that_fails_does(env):
    """Spec 6 (a check set to warn never retries): with Retry set, nothing there set to warn, and an empty file with Not
    empty set to warn, each succeed at the first attempt; with Not empty failing the block, every attempt is made."""
    put(env.root / "inbox" / "empty.csv", b"")
    slow = {"policy": "fixed", "delay": "3s", "max_attempts": 3}
    blocks = {"none_warns": check_file("inbox/missing_*.csv", warn=("there",), retry=slow),
              "empty_warns": check_file("inbox/empty.csv", not_empty=True, warn=("empty",), retry=slow),
              "empty_fails": check_file("inbox/empty.csv", not_empty=True,
                                        retry={"policy": "fixed", "delay": "100ms", "max_attempts": 3})}
    result, run_id, status = run(env, blocks)

    assert status == {"none_warns": "success", "empty_warns": "success", "empty_fails": "failed"}, told(result)
    assert attempts(env, run_id) == {"none_warns": 1, "empty_warns": 1, "empty_fails": 3}


# --- 2: on the server of an SSH connection -------------------------------------------------------------------------------------

@SERVER
def test_files_on_the_server_of_an_ssh_connection(env, server):
    """Spec 2 (Path on: the server of an SSH connection, as Copy Files' paths are; a pattern in the last part, ~ the
    login folder; a folder does not count), 3 (the checks on the server's files) and 5 (the Log lists each file with its
    size and time)."""
    exports = server.home_path / "exports"
    put(exports / "orders-1.parquet", b"x" * 100, local(2026, 10, 2, 8, 0))
    put(exports / "orders-2.parquet", b"", local(2026, 10, 2, 8, 0))
    put(exports / "orders-old.parquet" / "inside.parquet", b"x", local(2026, 10, 2, 8, 0))  # a folder
    blocks = {"found": check_file("~/exports/orders-1*.parquet", not_empty=True, modified=True, size=("1 B", "1 KB"),
                                  server="appserver"),
              "pattern": check_file("~/exports/orders-*.parquet", server="appserver"),
              "empty": check_file("~/exports/orders-*.parquet", not_empty=True, server="appserver"),
              "none": check_file("~/exports/missing-*.parquet", server="appserver")}
    result, run_id, status = run(env, blocks, as_of="2026-10-02 09:00")

    assert status == {"found": "success", "pattern": "success", "empty": "failed", "none": "failed"}, told(result)
    assert [posixpath.basename(p) for p in output(env, run_id, "found", "files")] == ["orders-1.parquet"]
    assert [posixpath.basename(p) for p in output(env, run_id, "pattern", "files")] == ["orders-1.parquet",
                                                                                         "orders-2.parquet"]
    assert "orders-2.parquet" in error_of(env, run_id, "empty")
    assert "missing-*.parquet" in error_of(env, run_id, "none")
    messages = [m for _, m in logs_of(env, run_id, "found")]
    assert any("orders-1.parquet" in m and re.search(r"(?<![\d.,])100\s*(B|bytes?)\b", m, re.I)
               and re.search(time_shown("2026-10-02 08:00"), m) for m in messages), messages


# --- 6 and 7: the help, Help > Documentation and README ---------------------------------------------------------------------------

def checks_page_section() -> str:
    """Check File's entry on the Checks page of Help > Documentation, as text."""
    from taskloom.editor import docs

    check_class()
    pages = docs.pages(discover(None))
    assert "blocks-checks.html" in pages, sorted(pages)
    page = pages["blocks-checks.html"]
    assert f'id="{TYPE}"' in page, "the Checks page has no entry for Check File"
    section = page.split(f'id="{TYPE}"', 1)[1].split("<h2 ", 1)[0]
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", section)).split())


def test_its_help_says_retry_waits_for_a_late_file_and_a_warning_never_retries():
    """Spec 6 (The help says so: with Retry set a failed check is checked again later; a check set to warn never
    retries) and 7 (Help > Documentation describes the block)."""
    text = checks_page_section()
    assert " ".join(check_class().description.split()) in text
    sentences = re.split(r"(?<=[.;!?])\s+", text.lower())
    assert any("retry" in s for s in sentences), f"Check File's help does not mention Retry: {text}"
    assert any("warn" in s and "retr" in s for s in sentences), f"its help does not say a warning never retries: {text}"


def test_the_readme_and_the_documentation_describe_the_block_with_an_example():
    """Spec 7 (Help > Documentation and the README describe the block, with an example: Check File, with Retry, before
    a Read File): the README has a YAML example with a Check File that has a retry and an output connected to a Read
    File, whose settings the block accepts; Help > Documentation's guide (made from README) shows the example."""
    from taskloom.editor import docs

    cls = check_class()
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "Check File" in readme
    examples = [text for text in re.findall(r"```ya?ml\n(.*?)```", readme, re.S) if TYPE in text]
    assert examples, f"README has no YAML example with a Check File ({TYPE})"
    shown = False
    for text in examples:
        flow = yaml.safe_load(text)
        blocks = (flow or {}).get("blocks") or {}
        checks = {b: spec for b, spec in blocks.items() if isinstance(spec, dict) and spec.get("type") == TYPE}
        for block, spec in checks.items():
            block_config(cls, BlockSpec(id=block, type=TYPE, config=dict(spec.get("config") or {})))
        reads = {b for b, spec in blocks.items() if isinstance(spec, dict) and spec.get("type") == "data.read_file"}
        lines = [re.fullmatch(r"\s*(\w+)\.(\w+)\s*->\s*(\w+)\.(\w+)\s*", str(e)) for e in (flow or {}).get("edges") or []]
        for block, spec in checks.items():
            if spec.get("retry") and any(m and m[1] == block and m[3] in reads for m in lines):
                assert Retry.parse(spec["retry"]).max_attempts > 1, spec["retry"]
                shown = True
    assert shown, f"no README example has a Check File with a retry connected to a Read File: {examples}"
    assert " ".join(cls.description.split()) in checks_page_section()
    pages = docs.pages(discover(None))
    guide = [name for name, page in pages.items()
             if name not in ("index.html", "properties.html") and not name.startswith("blocks-") and TYPE in page]
    assert guide, "no guide page of Help > Documentation shows the Check File example"
