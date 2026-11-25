"""Typed values for inputs (docs/promises/typed-values.yaml): two gaps found after stage 4's first tests, under the
maintainer's rule "no surprises: the flow's behaviour must always be visible and expected; no hidden fallbacks; when
something is wrong, fail, and let the user decide". The flow file's contract is tests/test_typed_values.py's.

TV-3 says {{name}} in a typed text (or in an item of a typed list) is filled once, like any field, in every way of
running. tests/test_typed_values.py checks it in a full run; here it is checked in Run only this block (`taskloom run
--only`) and Run from this block on (`taskloom run --from`): a {{name}} is filled with a flow parameter as this run has
it, or with an input of the block that comes by a line (as it came in the run --inputs-from names, as --input gives it,
or as the block before sent it in this run); a parameter's value holding braces is not filled again; and a {{name}}
that names nothing, or another typed value, is refused before the run starts, naming the block and the input, with the
words `taskloom validate` uses.

TV-9 with TV-5: a typed value written by hand (the editor never offers one) on an input that takes none: Run Query's
params and Statistics' extra (they expect a dictionary), the Loop's result (fed from inside the Loop), a table input, an
input that collects many lines (a Merger's lines) and run after. Today Validate refuses some of them with one reason for
all ("it takes none (a table input, one that collects many lines, or run after)"), lets the others through, and a run
then fails in the block (params, extra) or drops the value without a word (the Loop's result: results [None, None]).
The contract these tests fix (the implementer follows it):
- `taskloom validate` reports it as one `error:` line naming the block and the input, saying "takes no typed value",
  with the reason the editor's menu gives for that input (canvas.takes_no_value: the words in REASONS) and not the
  reasons of other inputs;
- `taskloom run` refuses to start with the same line (exit code 2, no run recorded).

Through `taskloom` on the command line, on a temporary Taskloom folder; the editor is not driven here."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from taskloom.config import Home  # noqa: E402
from taskloom.editor.canvas import takes_no_value  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_typed_values import (as_recorded, flow_file, python, recorded, recorder, runs_recorded,  # noqa: E402
                                     said, typed)

# --- TV-3: {{name}} in typed values with --only and --from -----------------------------------------------------------

PARAMS = {"p": typed("text", "V7"), "q": typed("text", "Q9"), "braced": typed("text", "{{q}}")}
PREP_TEXT = "{{p}}/{{word}}/{{braced}}"  # prep's typed text: a parameter, its input word (by a line from src), and a
#                                          parameter whose value holds braces (put in once: {{q}} stays)
PREP_LIST = "{{p}}, {{word}}"  # prep's typed list: each item filled on its own
LABEL_TEXT = "{{p}}+{{from_prep}}"  # label's typed text: a parameter and its input from_prep (by a line from prep)


def chain(env, label_text: str = LABEL_TEXT, more=None):
    """flow.yaml: src gives 'W3' by a line into prep's word; prep (typed values PREP_TEXT at title, PREP_LIST at names)
    passes its word on by a line into label's from_prep; label has the typed text `label_text` at title (and `more`
    typed values). Each records what it got (prep.jsonl, label.jsonl). The flow and the two records."""
    out_prep, out_label = env.root / "prep.jsonl", env.root / "label.jsonl"
    flow = flow_file(env.root / "flow.yaml", {
        "start": {"type": "trigger.manual"},
        "src": python([], "output = 'W3'"),
        "prep": recorder(out_prep, ["word", "title", "names"],
                         {"title": typed("text", PREP_TEXT), "names": typed("list", PREP_LIST)}),
        "label": recorder(out_label, ["from_prep", "title", "other"], {"title": typed("text", label_text),
                                                                       **(more or {})})},
        ["start.start -> src.after", "src.output -> prep.word", "prep.output -> label.from_prep"], params=PARAMS)
    return flow, out_prep, out_label


def prep_got(p: str, word: str) -> dict:
    return as_recorded({"word": word, "title": f"{p}/{word}/{{{{q}}}}", "names": [p, word]})


def label_got(p: str, from_prep: str) -> dict:
    return as_recorded({"from_prep": from_prep, "title": f"{p}+{from_prep}", "other": None})


def full_run(env, flow) -> int:
    result = env.taskloom("run", flow)
    assert result.returncode == 0, said(result)
    run_id, _ = env.last_run()
    return run_id


@pytest.mark.invariant("TV-3")
def test_run_only_this_block_fills_double_braces_in_its_typed_text_and_list_once(env):
    """After a full run, the user runs only prep with the inputs it got then and the parameter p changed for this run
    (`taskloom run --only prep --inputs-from <that run> --param p=V8`): its typed text "{{p}}/{{word}}/{{braced}}"
    arrives as "V8/W3/{{q}}" (p as this run has it, word as its line brought it in that run, braced's value put in
    once) and its typed list "{{p}}, {{word}}" as ["V8", "W3"]. Then only label, with no earlier run and its input
    given (`--only label --input from_prep='Y2'`): its typed text "{{p}}+{{from_prep}}" arrives as "V7+Y2"."""
    flow, out_prep, out_label = chain(env)
    run_id = full_run(env, flow)

    again = env.taskloom("run", flow, "--only", "prep", "--inputs-from", run_id, "--param", "p=V8")
    alone = env.taskloom("run", flow, "--only", "label", "--input", "from_prep='Y2'")

    assert again.returncode == 0, said(again)
    assert alone.returncode == 0, said(alone)
    assert recorded(out_prep) == [prep_got("V7", "W3"), prep_got("V8", "W3")]
    assert recorded(out_label) == [label_got("V7", "W3"), label_got("V7", "Y2")]


@pytest.mark.invariant("TV-3")
@pytest.mark.parametrize("given, word", [((), "W3"), (("--input", "word='Z5'"), "Z5")],
                         ids=["its input as it came in that run", "its input given"])
def test_run_from_this_block_on_fills_double_braces_with_parameters_and_inputs_that_come_by_lines(env, given, word):
    """After a full run, the user runs from prep on with the parameter p changed (`taskloom run --from prep
    --inputs-from <that run> --param p=V8`, and in the second case `--input word='Z5'`): prep's typed text
    "{{p}}/{{word}}/{{braced}}" arrives as "V8/<word>/{{q}}" and its typed list as ["V8", <word>], word being what its
    line brought in that run (W3) or the value given (Z5); label, after it, gets its typed text "{{p}}+{{from_prep}}"
    as "V8+<word>", from_prep being what prep sent it by the line in this run."""
    flow, out_prep, out_label = chain(env)
    run_id = full_run(env, flow)

    again = env.taskloom("run", flow, "--from", "prep", "--inputs-from", run_id, "--param", "p=V8", *given)

    assert again.returncode == 0, said(again)
    assert recorded(out_prep) == [prep_got("V7", "W3"), prep_got("V8", word)]
    assert recorded(out_label) == [label_got("V7", "W3"), label_got("V8", word)]


NOT_FILLED = {  # (label's typed text, the name the error names, more typed values of label)
    "a name of nothing": ("{{nope}}", "nope", {}),
    "another typed value": ("{{other}}", "other", {"other": typed("text", "abc")}),
}


@pytest.mark.invariant("TV-3")
@pytest.mark.parametrize("mode, block", [("--only", "label"), ("--from", "prep")], ids=["--only", "--from"])
@pytest.mark.parametrize("text, name, more", list(NOT_FILLED.values()), ids=list(NOT_FILLED))
def test_run_only_and_run_from_refuse_a_typed_text_naming_nothing_before_the_run(env, mode, block, text, name, more):
    """After a full run, label's typed text is edited by hand to {{nope}} (no parameter or input of that name) or
    {{other}} (another typed value of label, which no line brings). Run only label (`--only label --inputs-from <that
    run>`) and Run from prep on (`--from prep`, label after it) are refused before they start (exit code 2, no run
    recorded), with the error `taskloom validate` gives, naming label, its input title and the name; neither block runs
    again."""
    flow, out_prep, out_label = chain(env)
    run_id = full_run(env, flow)
    chain(env, text, more)  # (the hand edit)

    validated = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow, mode, block, "--inputs-from", run_id)

    def naming(result) -> list[str]:
        return [line for line in result.stderr.splitlines()
                if line.startswith("error: ") and "label" in line and "title" in line and f"{{{{{name}}}}}" in line]

    assert validated.returncode == 2 and naming(validated), said(validated)
    assert ran.returncode == 2 and naming(ran) == naming(validated), said(ran)
    assert runs_recorded(env) == 1
    assert recorded(out_prep) == [prep_got("V7", "W3")] and recorded(out_label) == [label_got("V7", "W3")]


# --- TV-9 with TV-5: a typed value written by hand on an input that takes none ---------------------------------------

NO_TYPED = "takes no typed value"
REASONS = {  # why an input takes no typed value, as the editor's menu says it (canvas.takes_no_value), lower case
    "dictionary": "expects a dictionary",
    "inside the loop": "comes from the blocks inside the loop",
    "table": "takes a table from a line",
    "many lines": "collects many lines into a list",
    "run after": "only says when the block runs",
}
WAREHOUSE = 'warehouse: {kind: jdbc, driver: org.h2.Driver, url: "jdbc:h2:mem:nowhere", jars: []}\n'  # (never used)
TABLE = "import polars as pl\noutput = pl.DataFrame({'amount': [1, 2]})"
NO_VALUE = {  # (the blocks, the edges, the block and its input with the typed value, the reason)
    "Run Query's params": (
        {"query": {"type": "db.run_query", "config": {"connection": "warehouse", "sql": "select 1"},
                   "inputs": {"params": typed("text", "day_key=1")}}},
        [], "query", "params", "dictionary"),
    "Statistics' extra": (
        {"make": python([], TABLE), "stats": {"type": "data.statistics", "inputs": {"extra": typed("text", "a note")}}},
        ["make.output -> stats.table"], "stats", "extra", "dictionary"),
    "the Loop's result": (
        {"each": {"type": "logic.loop", "inputs": {"items": typed("list", "a, b"), "result": typed("text", "done")}},
         "work": {**python(["item"], "output = item"), "inside": "each"}},
        ["each.item -> work.item"], "each", "result", "inside the loop"),
    "a table input (Statistics' table)": (
        {"stats": {"type": "data.statistics", "inputs": {"table": typed("text", "sales.csv")}}},
        [], "stats", "table", "table"),
    "an input that collects many lines (a Merger's lines)": (
        {"join": {"type": "logic.merger", "inputs": {"lines": typed("text", "x")}}},
        [], "join", "lines", "many lines"),
    "run after": (
        {"step": python([], "output = 1", {"after": typed("text", "x")})},
        [], "step", "after", "run after"),
}


@pytest.mark.invariant("TV-9")
@pytest.mark.parametrize("blocks, edges, block, hole, reason", list(NO_VALUE.values()), ids=list(NO_VALUE))
def test_a_typed_value_written_by_hand_on_an_input_that_takes_none_is_refused_by_validate_and_the_run(
        env, blocks, edges, block, hole, reason):
    """A flow file edited by hand puts a typed value on an input whose menu in the editor offers none, and says why:
    Run Query's params and Statistics' extra (a dictionary), the Loop's result (fed from inside the Loop), Statistics'
    table (a table input), a Merger's lines (many lines) and run after. `taskloom validate` refuses the flow with one
    error naming the block and the input, saying it takes no typed value and why, with the reason the menu gives for
    that input (not another input's); `taskloom run` refuses to start with the same error: no run is recorded, the
    value is neither dropped nor sent to the block."""
    env.write("home/connections.yaml", WAREHOUSE)
    flow = flow_file(env.root / "flow.yaml", blocks, edges)
    menu_says = takes_no_value(discover(Home(env.home).blocks_dir), blocks[block], hole)
    assert menu_says and REASONS[reason] in menu_says.lower(), menu_says  # (what the editor's menu says today)

    validated, ran = env.taskloom("validate", flow), env.taskloom("run", flow)

    def naming(result) -> list[str]:
        return [line for line in result.stderr.splitlines()
                if line.startswith("error: ") and block in line and hole in line and NO_TYPED in line.lower()]

    found = naming(validated)
    assert validated.returncode == 2 and len(found) == 1, said(validated)
    said_why = found[0].lower()
    assert REASONS[reason] in said_why, found[0]
    assert not [why for key, why in REASONS.items() if key != reason and why in said_why], found[0]
    assert ran.returncode == 2 and naming(ran) == found, said(ran)
    assert runs_recorded(env) == 0
