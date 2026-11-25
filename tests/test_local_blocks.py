"""Local reusable blocks in the flow file, as `taskloom run`, the engine, `taskloom validate`, `taskloom check` (Check
flows) and the scheduler service run and check them (stage 4, work package 3; the editor's side is work package 4).

The flow file, as these tests fix it:

    local_blocks:                      # top level: the flow's Local block definitions, by name
      shout:                           # its name: its copies are blocks of type local.shout
        title: Shout
        description: Says a word louder.
        inputs_help: 'word: a word. tail: what to add after it.'
        inputs: [word, tail]           # as a Python Code block's Inputs,
        outputs: [loud]                # Outputs,
        use_environment: false         # Run in my Python environment
        code: |                        # and Code
          loud = word.upper() + (tail or '!')
    blocks:
      greet_ana: {type: local.shout}   # a copy: no config; copies differ only by what is connected to them
    edges:
      - say_ana.output -> greet_ana.word

- A definition has the fields a Global reusable block has (logic.ReusableBlock, write_reusable_block): title,
  description, inputs_help, and the Python Code settings it runs with: inputs, outputs, use_environment, code. Every
  copy runs that code, as a Python Code block with those settings would, on what is connected to that copy (RB-2).
- engine.run_flow(flow, registry, home) and flow.validate(flow, registry, home), given the registry of the blocks
  folder (registry.discover(home.blocks_dir)) as `taskloom run`, `taskloom validate`, `taskloom check` and the scheduler
  service have it, know the flow's Local blocks from the flow itself: no file besides the flow is needed, and none is
  written to the blocks folder (RB-7).
- Validate (RB-9): a problem in a definition is one error, which names the definition and each of its copies; a copy
  whose definition is not in the flow file is an error that names the copy and says its definition is missing.

Every test runs with a temporary Taskloom folder whose blocks folder is empty, and the flow alone in its folder."""

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

from taskloom import engine
from taskloom.config import Home
from taskloom.flow import load_flow
from taskloom.history import History
from taskloom.registry import discover
from taskloom.scheduler import Scheduler
from tests.test_scheduler_promises import on

SHOUT = {
    "title": "Shout",
    "description": "Says a word louder: in capitals, with what to add after it (! when nothing is).",
    "inputs_help": "word: a word, as text. tail: what to add after it.",
    "inputs": ["word", "tail"],
    "outputs": ["loud"],
    "use_environment": False,
    "code": "loud = word.upper() + (tail or '!')\n",
}
WHISPER = {  # a second definition in the same flow: each copy runs its own definition's code
    "title": "Whisper",
    "description": "Says a word softly.",
    "inputs_help": "word: a word, as text.",
    "inputs": ["word"],
    "outputs": ["soft"],
    "use_environment": False,
    "code": "soft = word.lower() + '...'\n",
}
DEFINITIONS = {"shout": SHOUT, "whisper": WHISPER}
T0 = dt.datetime(2026, 1, 5, 6, 30)  # the scheduler service's first check (a Monday)


def shouted(word: str, tail: str | None = None) -> str:
    """What SHOUT's code gives."""
    return word.upper() + (tail or "!")


def whispered(word: str) -> str:
    """What WHISPER's code gives."""
    return word.lower() + "..."


# --- helpers ----------------------------------------------------------------------------------------------------------

@pytest.fixture
def env(env):
    """conftest's temporary Taskloom folder, with an empty blocks folder: there is no block file anywhere."""
    Home(env.home).blocks_dir.mkdir()
    return env


def python(code: str, inputs=(), outputs=("output",)) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code}}


def writer(out: Path, *inputs) -> dict:
    """A Python Code block that writes what each of its inputs got to the file `out`, as a JSON object."""
    got = ", ".join(f"{name!r}: {name}" for name in inputs)
    return python(f"import json\nopen({str(out)!r}, 'w', encoding='utf-8').write(json.dumps({{{got}}}))\n",
                  inputs, ())


def write_flow(env, blocks: dict, edges, local_blocks=None) -> Path:
    """The flow, alone in a folder of its own (flows/flow.yaml); `local_blocks` None: the file has no such key."""
    folder = env.root / "flows"
    folder.mkdir(exist_ok=True)
    data = {} if local_blocks is None else {"local_blocks": local_blocks}
    data.update(blocks=blocks, edges=list(edges))
    path = folder / "flow.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def copies_flow(env, out: Path, words, start=None) -> tuple[Path, dict]:
    """A copy of shout for each word (greet_<word>), each given its word by a block of its own (say_<word>); the
    second copy also gets a tail ('?!'), the others none; a copy of whisper (hush) gets the first word. save writes
    what each copy gave to `out`. Returns the flow and what save writes, as the definitions' code gives it."""
    blocks = {"start": start or {"type": "trigger.manual"}, "tail": python("output = '?!'")}
    edges = ["start.start -> tail.after"]
    for word in words:
        blocks[f"say_{word}"] = python(f"output = {word!r}")
        blocks[f"greet_{word}"] = {"type": "local.shout"}
        edges += [f"start.start -> say_{word}.after", f"say_{word}.output -> greet_{word}.word",
                  f"greet_{word}.loud -> save.{word}"]
    edges.append(f"tail.output -> greet_{words[1]}.tail")
    blocks["hush"] = {"type": "local.whisper"}
    edges += [f"say_{words[0]}.output -> hush.word", "hush.soft -> save.hush"]
    blocks["save"] = writer(out, *words, "hush")
    expected = {word: shouted(word, "?!" if i == 1 else None) for i, word in enumerate(words)}
    expected["hush"] = whispered(words[0])
    return write_flow(env, blocks, edges, DEFINITIONS), expected


def no_block_files(env) -> list[str]:
    """What is in the blocks folder (RB-7: nothing, before a run and after it)."""
    return sorted(p.name for p in Home(env.home).blocks_dir.iterdir())


def only_the_flow(env, flow: Path):
    """RB-7: no file besides the flow: the blocks folder is empty and the flow is alone in its folder."""
    assert no_block_files(env) == []
    assert [p.name for p in flow.parent.iterdir()] == [flow.name]


def errors(result) -> list[str]:
    """What taskloom said was wrong (its lines `error: ...`), without the `error: `."""
    return [line[len("error: "):] for line in result.stderr.splitlines() if line.startswith("error: ")]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- RB-2, RB-7: every copy runs the definition's code, on the command line -------------------------------------------

@pytest.mark.invariant("RB-2")
@pytest.mark.invariant("RB-7")
@pytest.mark.parametrize("words", [["ana", "bo"], ["ana", "bo", "cy"]], ids=["two_copies", "three_copies"])
def test_every_copy_runs_its_definitions_code_on_what_is_connected_to_it(env, words):
    """Two, then three copies of one Local block, each given another word (and only the second a tail), and a copy of
    a second Local block of the same flow: `taskloom validate` finds nothing wrong, and `taskloom run` gives each copy
    what its own definition's code makes of what is connected to it, with no file besides the flow."""
    out = env.root / "results.json"
    flow, expected = copies_flow(env, out, words)
    only_the_flow(env, flow)

    checked = env.taskloom("validate", flow)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert ": OK" in checked.stdout, checked.stdout

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(out) == expected
    assert no_block_files(env) == []


@pytest.mark.invariant("RB-2")
@pytest.mark.invariant("RB-7")
def test_a_copy_inside_a_loop_runs_the_definitions_code_once_per_item(env):
    """A copy inside a Loop gets each item as its word and, from a block outside the Loop, a tail: it runs the
    definition's code once per item; another copy, outside the Loop, runs the same code on its own word."""
    out = env.root / "results.json"
    flow = write_flow(env, {
        "start": {"type": "trigger.manual"},
        "names": python("output = ['ana', 'bo', 'cy']"),
        "tail": python("output = '?!'"),
        "each": {"type": "logic.loop", "config": {}},
        "greet_each": {"type": "local.shout", "inside": "each"},
        "say_dee": python("output = 'dee'"),
        "greet_dee": {"type": "local.shout"},
        "save": writer(out, "results", "alone"),
    }, ["start.start -> names.after", "start.start -> tail.after", "start.start -> say_dee.after",
        "names.output -> each.items", "each.item -> greet_each.word", "tail.output -> greet_each.tail",
        "greet_each.loud -> each.result", "each.results -> save.results",
        "say_dee.output -> greet_dee.word", "greet_dee.loud -> save.alone"], {"shout": SHOUT})
    only_the_flow(env, flow)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr[-3000:]
    assert read_json(out) == {"results": [shouted(w, "?!") for w in ("ana", "bo", "cy")], "alone": shouted("dee")}
    assert no_block_files(env) == []


# --- RB-7: the same results on the command line, in the engine and under the scheduler service ------------------------

def on_the_command_line(env, flow: Path) -> str:
    result = env.taskloom("run", flow)
    assert result.returncode == 0, result.stderr[-3000:]
    return "success"


def in_the_engine(env, flow: Path) -> str:
    """As `taskloom run` runs it, in this process: the flow as loaded, the registry of the (empty) blocks folder."""
    home = Home(env.home)
    return engine.run_flow(load_flow(flow), discover(home.blocks_dir), home).status


def under_the_scheduler(env, flow: Path) -> str:
    """The scheduler service sees the flow's schedule, then starts its run when it is due (as `taskloom scheduler`
    does: the Taskloom folder's lock taken first)."""
    told = []
    History(env.home / "history.db").close()  # its tables exist before any run
    with on(env):
        home = Home(env.home)
        home.set_scheduled(flow, True)
        scheduler = Scheduler(home, lambda title, message: told.append((title, message)))
        scheduler.tick(T0)  # first sight of the schedule
        scheduler.tick(T0 + dt.timedelta(minutes=1))  # due: its run starts
        scheduler.wait_for_runs()
    statuses = [status for (status,) in env.query("SELECT status FROM runs ORDER BY id")]
    assert len(statuses) == 1, (statuses, told)
    return statuses[0]


@pytest.mark.invariant("RB-7")
@pytest.mark.parametrize("run", [on_the_command_line, in_the_engine, under_the_scheduler],
                         ids=["command_line", "engine", "scheduler"])
def test_a_flow_with_local_blocks_gives_the_same_results_however_it_is_run(env, run):
    """The same flow (its start block a Schedule, every minute), run with `taskloom run`, by the engine as
    `taskloom run` calls it, and by the scheduler service when its schedule is due: each copy gives what its
    definition's code makes of what is connected to it, the same every way, with no file besides the flow."""
    out = env.root / "results.json"
    flow, expected = copies_flow(env, out, ["ana", "bo"],
                                 start={"type": "trigger.schedule", "config": {"cron": "* * * * *"}})
    only_the_flow(env, flow)

    status = run(env, flow)

    assert status == "success"
    assert read_json(out) == expected
    assert no_block_files(env) == []


# --- RB-9: Validate on a definition with a problem, and on a copy whose definition is missing -------------------------

@pytest.mark.invariant("RB-9")
def test_validate_reports_a_problem_in_a_definition_once_naming_each_of_its_copies(env):
    """A definition whose Inputs hold something that is not a name ('first word'), with three copies: `taskloom
    validate` refuses the flow with one error about it, which names the definition and each of its three copies, not
    an error per copy."""
    copies = ["greet_ana", "greet_bo", "greet_cy"]
    blocks, edges = {"start": {"type": "trigger.manual"}}, []
    for copy in copies:
        blocks[f"say_{copy}"] = python("output = '?!'")
        blocks[copy] = {"type": "local.shout"}
        edges += [f"start.start -> say_{copy}.after", f"say_{copy}.output -> {copy}.tail"]
    flow = write_flow(env, blocks, edges, {"shout": {**SHOUT, "inputs": ["first word", "tail"]}})

    result = env.taskloom("validate", flow)

    assert result.returncode == 2, result.stdout + result.stderr
    found = errors(result)
    about_it = [line for line in found if "first word" in line]
    assert len(about_it) == 1, found
    assert "shout" in about_it[0] and all(copy in about_it[0] for copy in copies), about_it[0]
    assert found == about_it, f"more errors than the one about the definition: {found}"


@pytest.mark.invariant("RB-9")
@pytest.mark.parametrize("definitions", [{"shout": SHOUT}, None], ids=["another_definition_only", "no_local_blocks"])
def test_validate_names_a_copy_whose_definition_is_missing_from_the_flow_file(env, definitions):
    """A copy of whisper in a flow file (edited by hand) that holds no definition named whisper (only shout's, or no
    local_blocks at all): `taskloom validate` refuses the flow with an error that names the copy and says its
    definition is missing, not that its block type is unknown; a copy whose definition is there is not named."""
    blocks = {"start": {"type": "trigger.manual"}, "say_it": python("output = 'ana'"),
              "hush_bo": {"type": "local.whisper"}}
    edges = ["start.start -> say_it.after", "say_it.output -> hush_bo.word"]
    if definitions:
        blocks["greet_ana"] = {"type": "local.shout"}
        edges.append("say_it.output -> greet_ana.word")
    flow = write_flow(env, blocks, edges, definitions)

    result = env.taskloom("validate", flow)

    assert result.returncode == 2, result.stdout + result.stderr
    found = errors(result)
    named = [line for line in found if "hush_bo" in line]
    assert len(named) == 1, found
    said = named[0].lower()
    assert "whisper" in said and "definition" in said and "missing" in said, named[0]
    assert "unknown block type" not in said, named[0]
    assert not [line for line in found if "greet_ana" in line], found


# --- RB-7: Check flows on a flow with Local blocks --------------------------------------------------------------------

@pytest.mark.invariant("RB-7")
def test_check_flows_finds_nothing_to_fix_in_a_flow_with_local_blocks(env):
    """`taskloom check` (Check flows) on a flow with copies of two Local blocks: OK, nothing to fix, nothing that
    needs you."""
    flow, _ = copies_flow(env, env.root / "results.json", ["ana", "bo"])
    only_the_flow(env, flow)

    result = env.taskloom("check", flow)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{flow}: OK" in result.stdout.splitlines(), result.stdout
    assert no_block_files(env) == []


@pytest.mark.invariant("RB-7")
def test_check_flows_fixes_a_connection_into_an_input_a_definition_lacks_and_keeps_the_definitions(env):
    """A connection into an input the copy's definition does not have (left by a hand edit): Check flows offers to
    remove it, as for any block; `taskloom check --fix` removes it, keeps the flow's definitions as they were, and the
    flow then runs."""
    out = env.root / "results.json"
    flow, expected = copies_flow(env, out, ["ana", "bo"])
    data = yaml.safe_load(flow.read_text(encoding="utf-8"))
    data["edges"].append("say_bo.output -> greet_bo.colour")
    flow.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    result = env.taskloom("check", flow, "--fix")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "needs you" not in result.stdout, result.stdout
    assert [line for line in result.stdout.splitlines()
            if "can fix: remove the connection say_bo.output -> greet_bo.colour" in line], result.stdout
    fixed = yaml.safe_load(flow.read_text(encoding="utf-8"))
    assert fixed["local_blocks"] == DEFINITIONS
    assert "say_bo.output -> greet_bo.colour" not in fixed["edges"]
    ran = env.taskloom("run", flow)
    assert ran.returncode == 0, ran.stderr[-3000:]
    assert read_json(out) == expected
    assert no_block_files(env) == []
