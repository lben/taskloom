"""Logic blocks: Python code, conditions, waiting, lines (Splitter, Merger) and the Loop."""

from __future__ import annotations

import datetime as dt
import inspect
import io
import linecache
import math
import os
import pickle
import re
import sys
import types
import unicodedata
from pathlib import Path

import polars as pl
from rich.console import Console
from rich import traceback as rich_traceback
from rich.traceback import Traceback

from ..block import Block, fields, has_placeholders, ports, setting_label
from ..expr import evaluate
from ..engine import Failed, SecretStr, _masked, inline, line_names, preview
from ..flow import (ALL_LINES, AS_FINISHED, AT_MOST, AT_ONCE, EACH_LINE, INDEX, ITEM, ITEMS, LINE_ORDER, LINES,
                    LINES_RUN, LOOP, MERGER, ONE_AFTER_ANOTHER, PASSES_ON, RESULT, RESULTS, RUNS_AGAIN, SAME_TIME,
                    SPLITTER)
from ..table import Table


class _Utf8Text(io.StringIO):
    encoding = "utf-8"  # so rich draws the same boxes and arrows on Windows


class CodeError(Exception):
    """An error in a Python Code block's code, with a readable report: the code around the
    failing line (a rich traceback) and the values of the variables at that moment."""

    def __init__(self, error: BaseException, filename: str, namespace: dict, helpers: set):
        tb = error.__traceback__
        while tb is not None and tb.tb_frame.f_code.co_filename != filename:
            tb = tb.tb_next  # start at the block's own code, not Taskloom's
        frames = []
        walk = tb
        while walk is not None:
            frames.append(walk)
            walk = walk.tb_next
        line = next((f.tb_lineno for f in reversed(frames) if f.tb_frame.f_code.co_filename == filename), None)
        super().__init__(f"{type(error).__name__}: {error}" + (f" (line {line})" if line else ""))
        console = Console(file=_Utf8Text(), width=110, color_system=None, record=True, legacy_windows=False)
        console.print(Traceback.from_exception(type(error), error, tb or error.__traceback__,
                                               extra_lines=2, word_wrap=True, show_locals=False))
        details = [console.export_text().rstrip()]
        details += _variables("Variables", {k: v for k, v in namespace.items() if k not in helpers})
        inner = frames[-1].tb_frame if frames else None
        if inner is not None and inner.f_code.co_name != "<module>":
            details += _variables(f"Variables in {inner.f_code.co_name}()", inner.f_locals)
        self.details = "\n".join(details)


def _variables(title: str, values: dict) -> list[str]:
    shown = {k: v for k, v in values.items() if not k.startswith("__") and not isinstance(v, types.ModuleType)
             and not inspect.isfunction(v) and not inspect.isclass(v)}
    if not shown:
        return []
    return [f"{title}:"] + [f"  {k} = {inline(v)}" for k, v in shown.items()]


class PythonCode(Block):
    type_id = "logic.python"
    title = "Python Code"
    description = 'Runs a few lines of Python that you write. Use it when no other block does what you need.'
    category = "Logic"
    has_done = True  # it may set none of its outputs
    config = {
        "inputs": fields.Names(default=["input"], help="Variables your code receives.",
                               examples=["input", "sales, regions", "load_sales, ask_region"]),
        "outputs": fields.Names(default=["output"], outputs=True,
                                help="Variables your code sets; unset ones are not produced, but output, unless "
                                     "your code sets it, is a dict of every variable your code creates.",
                                examples=["output", "north, south", "result"]),
        "code": fields.Code("python", help="The Python your block runs. Each input is a variable named as in "
                                           "Inputs, params holds the flow's parameters and print() writes to the "
                                           "Log. Unless it sets output, output is a dict of every variable it "
                                           "creates; set output (or the other Outputs) to pass on something else.",
                            examples=["output = len(input)",
                                      'north = input[input["region"] == "North"]\noutput = north',
                                      'print(params["day"])\noutput = input'], script=True),
        "use_environment": fields.Bool(default=False, label="Run in my Python environment",
                                       help="Run in your own Python environment, with the packages "
                                            "you installed (Tools → Python environment)"),
    }
    # Only comments: as it is, the code runs and its output is {}, in your environment and in Taskloom's Python.
    new_config = {"use_environment": True, "code": """\
# Inputs are available as variables (default: `input`).
#
# ┌──────────────┐
# │  Load Sales  │─────► Python
# └──────────────┘
#
#   input = sales table
#
# ┌──────────────┐
# │  Load Sales  │──┐
# └──────────────┘  │
#                   ├──► Python
# ┌──────────────┐  │
# │  Ask Region  │──┘
# └──────────────┘
#
#   input = {
#       "load_sales": <table>,
#       "ask_region": "North"
#   }
#
#   sales = input["load_sales"]
#   region = input["ask_region"]
#
# Tables are pandas DataFrames:
#   north = sales[sales["region"] == region]
#
# Parameters:
#   day = params["day"]
#
# Log output:
#   print("Found", len(north), "rows")
#
# What goes out:
#   By default, every variable you create, as a dict:
#     output = {"sales": ..., "region": ..., "north": ...}
#   To send something else, set output yourself:
#     output = north"""}

    @classmethod
    def ports(cls, config):
        # Inputs can take several edges (e.g. the errors of many blocks); then they receive a dict keyed
        # by the source blocks' names, so your code can tell the values apart.
        return ({n: ports.Any(required=False, any_of=True, by_source=True) for n in config["inputs"]},
                {n: ports.Any() for n in config["outputs"]})

    def run(self, ctx, **inputs):
        values = {name: inputs.get(name) for name in self.config.inputs}
        if self.config.use_environment:
            from ..pyenv import run_code

            produced, left_out = run_code(ctx, self.config.code, values, self.config.outputs)
        else:
            produced, left_out = self._run_here(ctx, values)
        if left_out:  # never dropped silently
            ctx.warn("left out of output, as they cannot be passed on to the next blocks or kept with the run: "
                     + ", ".join(f"{name} ({' '.join(why.split())})" for name, why in left_out.items()))
        return produced

    def _run_here(self, ctx, values: dict) -> tuple[dict, dict]:
        """Run the code in Taskloom's own Python: (its outputs, {variable left out of output: why})."""
        helpers = {"ctx": ctx, "params": ctx.params, "pl": pl, "Table": Table,
                   "print": _printer(ctx), "log": lambda *values: ctx.log(" ".join(map(str, values)))}
        namespace = {**helpers, **values}
        given = set(namespace)
        # Registered with linecache under a short name, so error reports can show the code around the
        # failing line (rich looks the name up relative to the folder it was imported in).
        filename = f"{ctx.block_id}.py"
        lines = (len(self.config.code), None, self.config.code.splitlines(True), filename)
        for name in (filename, os.path.join(getattr(rich_traceback, "_IMPORT_CWD", os.getcwd()), filename)):
            linecache.cache[name] = lines
        try:
            exec(compile(self.config.code, filename, "exec"), namespace)
        except SystemExit as e:  # sys.exit() ends your code there, not the run
            if e.code not in (None, 0):
                raise CodeError(e, filename, namespace, set(helpers)) from None
        except Exception as e:
            raise CodeError(e, filename, namespace, set(helpers)) from None
        # Outputs your code does not assign are not produced, so blocks after them are skipped; but output, unless
        # it assigns it, is a dict of the variables it created.
        produced = {name: namespace[name] for name in self.config.outputs if name in namespace}
        left_out = {}
        if "output" in self.config.outputs and "output" not in namespace:
            produced["output"] = {}
            for name, value in namespace.items():
                if name in given or not _created(name, value):
                    continue
                try:  # as the run keeps it (_keep_outputs); its tables are kept as files of their own
                    _TablesAside(io.BytesIO()).dump(value)
                    produced["output"][name] = value
                except Exception as error:  # e.g. an open file or connection
                    left_out[name] = str(error) or type(error).__name__
        return produced, left_out


def _created(name: str, value) -> bool:
    """Whether a variable of your code's goes into its default output: not a module it imported, a function or class
    it defined, or a name starting with _."""
    return not (name.startswith("_") or isinstance(value, types.ModuleType) or inspect.isroutine(value)
                or inspect.isclass(value))


class _TablesAside(pickle.Pickler):
    """Pickles a value leaving its DataFrames aside (each is stored as a table file of its own)."""

    def persistent_id(self, value):
        return id(value) if Table.is_frame(value) else None


class ReusableBlock(PythonCode):
    """A Python Code block saved as a block of its own (in the editor: right-click it → Create Reusable block…), as
    a file in your blocks folder (write_reusable_block). It runs the code it was saved with and has no settings."""

    type_id = ""  # (each saved one has its own)
    config = {}
    new_config = {}
    # The Python Code block's settings it runs with: inputs, outputs, code and use_environment.
    python_code: dict = {}

    @classmethod
    def ports(cls, config):
        return super().ports(cls.python_code)

    def __init__(self, config):
        super().__init__(self.python_code)


def reusable_name(title: str) -> str:
    """The file name and block type (user.<name>) of a reusable block called `title`: its letters (é as e) and
    digits, the rest as _."""
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")


def write_reusable_block(folder: Path, title: str, description: str, inputs_help: str, python_code: dict) -> Path:
    """Save a ReusableBlock called `title` as a readable Python file in `folder` (made if missing): its code line
    by line."""
    name = reusable_name(title)
    code = "".join(f"            {line!r},\n" for line in python_code["code"].split("\n"))
    text = f'''\
# A block of your own, made from a Python Code block (in the editor: right-click it → Create Reusable block…).
# It runs the code below: to change what it does, edit this file.
from taskloom.blocks.logic import ReusableBlock


class Reusable(ReusableBlock):
    type_id = {f"user.{name}"!r}
    title = {title!r}
    category = 'User Created'
    description = {description!r}
    inputs_help = {inputs_help!r}
    python_code = {{
        'inputs': {python_code["inputs"]!r},
        'outputs': {python_code["outputs"]!r},
        'use_environment': {python_code["use_environment"]!r},
        'code': '\\n'.join([
{code}        ]),
    }}
'''
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.py"
    path.write_text(text, encoding="utf-8")
    return path


def _printer(ctx):
    """print() for your code: what it prints goes to the block's Log (and run history)."""

    def print_to_log(*values, sep=" ", end="\n", file=None, flush=False):
        if file is not None and file not in (sys.stdout, sys.stderr):
            print(*values, sep=sep, end=end, file=file, flush=flush)  # e.g. into a file you opened
        else:
            ctx.log(sep.join(map(str, values)).rstrip("\n"))

    return print_to_log


class If(Block):
    type_id = "logic.if"
    title = "If"
    description = "Checks a condition and sends the data down the 'true' path or the 'false' path, so different blocks run in each case."
    category = "Logic"
    inputs = {"value": ports.Any(required=False)}
    outputs = {"true": ports.Any(), "false": ports.Any()}
    has_done = True  # only one of its outputs arrives
    config = {"condition": fields.Code("expression", help="e.g. len(value) > 0 or is_business_day(today())",
                                       examples=["len(value) > 0", "is_business_day(today())",
                                                 "value['healthy']"])}
    # Lines starting with # are comments: a condition of only comments is as if left empty.
    new_config = {"condition": "# No need to delete this. Examples: value > 10, value is True, value == 'Option A'"}

    def run(self, ctx, value=None):
        result = evaluate(self.config.condition, {**ctx.functions, **ctx.params, "value": value})
        return {"true" if result else "false": value}


class Wait(Block):
    type_id = "logic.wait"
    title = "Wait"
    description = 'Pauses for a number of seconds, then passes its input on unchanged.'
    category = "Logic"
    inputs = {"value": ports.Any(required=False)}
    outputs = {"value": ports.Any()}
    config = {"seconds": fields.Float(help="How long to wait, in seconds: a number (0.5 for half a second), {value} "
                                           "for the number arriving at its value input, or {name} for a flow "
                                           "parameter", examples=["5", "0.5", "90"])}

    def run(self, ctx, value=None):
        seconds = self.config.seconds
        if seconds > 0:  # the Log says once how long and until when; the editor shows the time left on the block
            until = dt.datetime.now() + dt.timedelta(seconds=seconds)
            ctx.log(f"waiting {written_seconds(seconds)} s, until {until:%H:%M:%S}")
            ctx.waiting_until(until)
            try:
                ctx.wait(seconds)
            finally:  # time up, stopped, or the block's timeout ended it (a retry may follow): no more counting
                ctx.waiting_until(None)
        return {"value": value}


_AT_MOST = fields.Int(required=False, minimum=1, label="At most at once", only_if={LINES_RUN: [SAME_TIME]},
                      help="How many of its lines run at once, at most: a whole number, 1 or more (empty: all of "
                           "them). The next line starts as soon as one ends, in line order. A limit protects the "
                           "database and the computer from too many lines at once. No {placeholders}: Validate "
                           "checks the lines with it before the run.",
                      examples=["2", "4", "8"])
_AT_MOST.template = False  # (see Splitter.problems)


class Splitter(Block):
    type_id = SPLITTER
    title = "Splitter"
    description = ("Starts lines: each connection leaving its lines output starts one line, which gets its own copy of "
                   "the value that arrived. A line is the block that connection goes to and the blocks after it, up "
                   "to a Merger, which joins the lines. The lines run at the same time (no more than At most at once "
                   "of them, if you set it) or one after another, top to bottom as on the canvas, each to its end "
                   "before the next starts. A line may use what another line makes; a failed line stops no other.")
    inputs_help = "value: anything (optional): each line gets its own copy of it."
    category = "Logic"
    inputs = {"value": ports.Any(required=False)}
    outputs = {LINES: ports.Any()}
    config = {
        LINES_RUN: fields.Choice([SAME_TIME, ONE_AFTER_ANOTHER], default=SAME_TIME,
                                 help="at the same time: the lines run together, each line's own blocks one after "
                                      "another; one after another: line 1 runs to its end, then line 2, and so on, "
                                      "top to bottom as on the canvas (the canvas numbers the connections)"),
        AT_MOST: _AT_MOST,
    }

    @classmethod
    def problems(cls, config, sources, params):
        if has_placeholders(config[AT_MOST]):
            return [f"{_AT_MOST.label}: write a whole number, 1 or more (not a placeholder: Validate checks the lines "
                    "with it before the run)"]
        return []

    def run(self, ctx, value=None):
        return {LINES: value}  # (the run gives each line its own copy, and writes in the Log how they run)


class Merger(Block):
    """Passes on all lines together here (run); each line as it finishes, the engine runs it
    (engine._Runner._run_per_line)."""

    type_id = MERGER
    title = "Merger"
    description = ("Joins the lines of a Splitter. Passes on all lines together (as it is added): it waits until every "
                   "line connected to it has delivered, then passes on one value, {line name: that line's output}, "
                   "named as Python Code names inputs from several blocks (the block's name; block.output when a "
                   "block connects several of its outputs). If a line failed, it fails, saying which; if a line "
                   "delivered nothing (e.g. an If sent it nowhere), it is skipped, saying which. Passes on each line "
                   "as it finishes: it does not wait for every line; as each line finishes, it passes on that line's "
                   "output (the value itself), and the blocks after it run once for that line, at the same time for "
                   "as many lines as At the same time says, up to the end of the flow or up to a Merger set to all "
                   "lines together, which waits for every one of those runs and passes on {line name: what that run "
                   "gave it}. A line that failed, or delivered nothing, has no run; a failed run stops no other. Once "
                   "every line has been dealt with, it fails if a line or a run failed, listing them. Right-click it > "
                   "Run from this block on runs again only the runs that failed or did not run. It works inside a "
                   "Loop's box, and in the runs of another Merger set to each line as it finishes.")
    inputs_help = "lines: the last block of each line (any number of connections)."
    category = "Logic"
    inputs = {LINES: ports.Any(many=True, by_source=True)}
    outputs = {"value": ports.Any()}
    config = {
        PASSES_ON: fields.Choice([ALL_LINES, EACH_LINE], default=ALL_LINES,
                                 help="all lines together: it waits for every line, then passes on {line name: that "
                                      "line's output} once; each line as it finishes: it passes on each line's output "
                                      "as soon as that line has finished, and the blocks after it run once for that "
                                      "line"),
        AT_ONCE: fields.Int(default=1, minimum=1, label="At the same time", only_if={PASSES_ON: [EACH_LINE]},
                            help="How many lines the blocks after it run for at once: a whole number, 1 or more (1: "
                                 "one after another). The next run starts as soon as one ends, in the order the lines "
                                 "finished.",
                            examples=["1", "2", "4"]),
        RUNS_AGAIN: fields.Choice([LINE_ORDER, AS_FINISHED], default=LINE_ORDER, label="Runs again in",
                                  only_if={PASSES_ON: [EACH_LINE]},
                                  help="Running again from it (Run from this block on, or Run only this block), the "
                                       "runs it does again go in line order (top to bottom, as on the canvas) or in "
                                       "the order the lines finished in the run it carries on from. A run from the "
                                       "start always goes in the order the lines finish."),
    }

    def run(self, ctx, lines):
        return {"value": dict(zip(line_names(list(lines)), lines.values()))}  # (each key "block.output")


def written_seconds(seconds) -> str:
    """A Wait block's Seconds as written: 90 (not 90.0), 0.5, a placeholder such as {value}."""
    return str(int(seconds)) if isinstance(seconds, float) and seconds.is_integer() else str(seconds)


class AskUser(Block):
    type_id = "logic.ask_user"
    title = "Ask User"
    description = ('Asks you a question while you run the flow from the editor: text, a number, a password, yes/no or '
                   'a choice. Runs nobody can answer (e.g. scheduled ones) use its Default, or for a password the '
                   'stored secret it names.')
    category = "Logic"
    inputs = {"value": ports.Any(required=False)}
    outputs = {"answer": ports.Any()}
    config = {
        "prompt": fields.Text(help="The question you are asked when you run the flow from the editor",
                              examples=["Which region?", "Send the report now?", "Password for the warehouse"]),
        "kind": fields.Choice(["text", "number", "password", "yes_no", "choice"], default="text",
                              help="The answer it takes: text; number (12 or 2.5, so that an If after it can compare "
                                   "it, e.g. value > 10); password (the only answer that is hidden: *** in the Log and "
                                   "the run history); yes_no (true or false); or choice (one of Options)"),
        "options": fields.List(default=[], only_if={"kind": ["choice"]}, help="The answers to choose from",
                               examples=["North, South", "small, medium, large"]),
        "default": fields.Text(required=False, only_if={"kind": ["text", "number", "yes_no", "choice"]},
                               help="The answer used when nobody can be asked (scheduled runs): for kind number a "
                                    "number, for yes_no yes or no, for choice one of Options",
                               examples=["North", "yes", "12"]),
        "secret": fields.Text(required=False, label="Stored secret (name)", only_if={"kind": ["password"]},
                              help="The name of a password saved in Taskloom's secret store (taskloom secret set "
                                   "NAME): runs nobody can answer (unattended, e.g. scheduled ones) use it as the "
                                   "answer. A name never saved makes the run fail.",
                              examples=["warehouse_password", "report_token"]),
    }

    @classmethod
    def problems(cls, config, sources, params):
        if config["kind"] == "number" and config["default"] is not None and not has_placeholders(config["default"]):
            try:  # (with placeholders: read as the block runs)
                as_number(config["default"])
            except ValueError as e:
                return [f"{setting_label('default', cls.config['default'])}: {e}; kind number needs a number, "
                        "e.g. 12 or 2.5"]
        return []

    def run(self, ctx, value=None):
        c = self.config  # (a setting that is not for its kind is left empty: block.applies)
        if c.kind == "choice" and not c.options:
            raise Failed("kind 'choice' needs options")
        if ctx.can_ask:
            answer = ctx.ask(c.prompt, c.kind, c.options, c.default)
            if answer is None:
                raise Failed("no answer was given")
            if answer == "" and c.default is not None and c.kind in ("text", "number"):  # the box left empty
                ctx.log(f"nothing was typed: the default {c.default!r} is the answer")
                answer = c.default
        elif c.kind == "password":  # never the Default
            if not c.secret:
                raise Failed("nobody can answer in this run; a password needs a stored secret for unattended runs: "
                             "set Stored secret (name)")
            try:
                answer = ctx.home.get_secret(c.secret)
            except KeyError as e:  # (it says how to save it)
                raise Failed(e.args[0]) from None
        elif c.default is not None:
            answer = c.default
        else:
            raise Failed("nobody can answer in this run; set a default for unattended runs")
        if c.kind == "yes_no":
            answer = as_yes_no(answer)
        elif c.kind == "number":
            try:
                answer = as_number(answer)
            except ValueError as e:
                raise Failed(str(e)) from None
        elif c.kind == "choice" and answer not in c.options:
            raise Failed(f"answer {answer!r} is not one of {c.options}")
        elif c.kind == "password":
            answer = SecretStr(answer)
        return {"answer": answer}


def as_yes_no(value) -> bool:
    """A yes_no answer (or Default) as true or false: yes, y, true or 1 (any case) is true, anything else false."""
    return value if isinstance(value, bool) else str(value).strip().lower() in ("yes", "y", "true", "1")


def as_number(value) -> int | float:
    """A number, or a text such as 12 or 2.5, as a number: an int when it is whole (also 4.0), else a float.
    ValueError if it is not one."""
    if isinstance(value, str):
        try:
            return int(value)  # (exact, however many digits)
        except ValueError:
            pass
        try:
            value = float(value)
        except ValueError:
            raise ValueError(f"{value!r} is not a number") from None
    if isinstance(value, float) and math.isfinite(value):
        return int(value) if value.is_integer() else value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"{value!r} is not a number")


class Loop(Block):
    """Its items are run by the engine (engine._Runner._run_loop): this class says what it is, its ports and its
    setting."""

    type_id = LOOP
    title = "Loop"
    description = ("A box on the canvas around the blocks it runs: drag blocks into it. The blocks inside run once for "
                   "every item of the list arriving at its items (a table: once per row, as a dict of its columns), "
                   "as a small flow of their own. Inside the box, item gives each run its item and index its position "
                   "(1 to N); what reaches result is what that item gives back, and results is every item's result, "
                   "in the items' order (not the order they finished). At the same time sets how many items run at "
                   "once; blocks outside may connect into blocks inside (each item gets its own copy), but blocks "
                   "inside connect out of it only through result. If an item fails (a block inside fails and no error "
                   "output inside handles it), no new item starts, the items running finish, and the Loop fails, "
                   "listing the failed items. Right-click the Loop > Run from this block on runs again only the items "
                   "that failed or did not run.")
    inputs_help = ("items: a list, or a table (one item per row, as a dict of its columns). Inside the box, result: "
                   "what each item gives back.")
    category = "Logic"
    has_done = True  # (its done fires once every item has run)
    inputs = {ITEMS: ports.Any(), RESULT: ports.Any(required=False)}
    outputs = {RESULTS: ports.Any(), ITEM: ports.Any(), INDEX: ports.Any()}
    config = {AT_ONCE: fields.Int(default=1, minimum=1, label="At the same time",
                                  help="How many items run at once: a whole number, 1 or more (1: one after another). "
                                       "The next item starts as soon as one ends, in item order.",
                                  examples=["1", "4", "8"])}


PICTURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"}


def _picture(value) -> Path | None:
    """The absolute path of the picture file `value` names (a relative one is in the run's folder, the flow's), or
    None if it names none: a text on one line or a path, with a picture's suffix, of a file that is there."""
    if isinstance(value, SecretStr) or not isinstance(value, (str, os.PathLike)):
        return None
    text = os.fspath(value)
    if not isinstance(text, str) or len(text.splitlines()) != 1 or Path(text).suffix.lower() not in PICTURE_SUFFIXES:
        return None
    try:
        return Path(text).absolute() if Path(text).is_file() else None
    except OSError:  # e.g. a name too long for the system
        return None


class Inspect(Block):
    type_id = "logic.inspect"
    title = "Inspect"
    description = ("For checking your flow: put it between two blocks to see the data passing through "
                   "(in the Log and in Data preview). Tables line up as in the hover tooltip, a path to a picture "
                   "(.png, .jpg, .svg…) shows the picture, and the message can be shown as Markdown, with its "
                   "Mermaid diagrams drawn. It passes the data on unchanged.")
    category = "Debug"
    inputs = {"value": ports.Any(required=False, any_of=True)}
    outputs = {"value": ports.Any()}
    config = {
        "message": fields.Code("f-string", required=False,
                               help="Optional text written like a Python f-string, e.g. "
                                    "Healthy: {value['healthy']} {value['problems']}. Empty shows the whole value.",
                               examples=["Healthy: {value['healthy']}", "{len(value)} rows",
                                         "Day {params['day']}: {value}"]),
        "markdown": fields.Bool(default=False, label="Show as Markdown",
                                help="Draw the message (or, with no message, a text value) as Markdown in the Log: "
                                     "# headings, **bold**, *italic*, lists, | tables |, `code` and ``` code blocks "
                                     "(spaces kept). Mermaid diagrams (```mermaid code blocks) are drawn as pictures. "
                                     "As in Markdown, lines next to each other join into one paragraph: leave a blank "
                                     "line between paragraphs. Off: the text is shown exactly as it is, every space "
                                     "and line kept."),
    }

    def run(self, ctx, value=None):
        markdown = "markdown" if self.config.markdown else None
        picture = _picture(value)
        if self.config.message:
            # Secrets show as *** here: this text goes to the log and the run history.
            namespace = {**ctx.functions, "value": _masked(value), "params": _masked(ctx.params)}
            # As written between the quotes of rf"""...""" (rf'''...''' if the text has """ in it): both kinds
            # of quote can be in the text and in the {...} parts, backslashes stay as typed. {''} keeps a quote
            # at the end from closing it early.
            quotes = "'''" if '"""' in self.config.message else '"""'
            source = "rf" + quotes + self.config.message + "{''}" + quotes
            try:
                text = eval(compile(source, f"<block {ctx.block_id}>", "eval"), namespace)
            except Exception as e:
                raise ValueError(f"the message could not be filled in: {type(e).__name__}: {e}") from None
            ctx.log(text, show=markdown)
        elif picture is None:
            text = isinstance(value, str) and not isinstance(value, SecretStr)
            ctx.log(preview(value), show=markdown if text else None)
        if picture is not None:
            ctx.log(str(picture), show="image")  # the Log shows the picture under its path
        return {"value": value}
