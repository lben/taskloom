"""Logic blocks: Python code, conditions and waiting."""

from __future__ import annotations

import inspect
import io
import linecache
import os
import re
import sys
import types
import unicodedata
from pathlib import Path

import polars as pl
from rich.console import Console
from rich import traceback as rich_traceback
from rich.traceback import Traceback

from ..block import Block, fields, ports
from ..expr import evaluate
from ..engine import SecretStr, _masked, inline, preview, run_flow
from ..flow import load_flow
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
        "inputs": fields.Names(default=["input"], help="Variables your code receives."),
        "outputs": fields.Names(default=["output"], outputs=True,
                                help="Variables your code sets; unset ones are not produced."),
        "code": fields.Code("python"),
        "use_environment": fields.Bool(default=False, label="Run in my Python environment",
                                       help="Run in your own Python environment, with the packages "
                                            "you installed (Tools → Python environment)"),
    }
    # Only comments: as it is, the code runs and sets no output, in your environment and in Taskloom's Python.
    new_config = {"use_environment": True, "code": """\
# Each input is a variable named as in Inputs (by default `input`).
# One block connected: input is that block's output.
# Several blocks connected: input is a dict keyed by each block's name, e.g.
#   input = {"load_sales": <a table>, "ask_region": "North"}
#   sales, region = input["load_sales"], input["ask_region"]
# A table arrives as a pandas DataFrame (in your Python environment), e.g.
#   north = sales[sales["region"] == region]
# params holds the flow's parameters, e.g. params["day"]
# print() writes to the Log, e.g. print(len(north), "rows")
# Set output to pass a value on (a DataFrame comes out as a table), e.g.
#   output = north"""}

    @classmethod
    def ports(cls, config):
        # Inputs can take several edges (e.g. the errors of many blocks); then they receive a dict keyed
        # by the source blocks' names, so your code can tell the values apart.
        return ({n: ports.Any(required=False, any_of=True, by_source=True) for n in config["inputs"]},
                {n: ports.Any() for n in config["outputs"]})

    def run(self, ctx, **inputs):
        if self.config.use_environment:
            from ..pyenv import run_code

            values = {name: inputs.get(name) for name in self.config.inputs}
            return run_code(ctx, self.config.code, values, self.config.outputs)
        helpers = {"ctx": ctx, "params": ctx.params, "pl": pl, "Table": Table,
                   "print": _printer(ctx), "log": lambda *values: ctx.log(" ".join(map(str, values)))}
        namespace = {**helpers, **{name: inputs.get(name) for name in self.config.inputs}}
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
        # Outputs your code does not assign are not produced, so blocks after them are skipped.
        return {name: namespace[name] for name in self.config.outputs if name in namespace}


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
    config = {"condition": fields.Code("expression", help="e.g. len(value) > 0 or is_business_day(today())")}

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
    config = {"seconds": fields.Float()}

    def run(self, ctx, value=None):
        ctx.wait(self.config.seconds)
        return {"value": value}


class AskUser(Block):
    type_id = "logic.ask_user"
    title = "Ask User"
    description = 'Asks you a question while you run the flow from the editor (text, password, yes/no or a choice). In scheduled runs it uses the default answer you set.'
    category = "Logic"
    inputs = {"value": ports.Any(required=False)}
    outputs = {"answer": ports.Any()}
    config = {
        "prompt": fields.Text(),
        "kind": fields.Choice(["text", "password", "yes_no", "choice"], default="text"),
        "options": fields.List(default=[], help="Choices for kind 'choice'"),
        "default": fields.Text(required=False, help="Used when nobody can be asked (scheduled runs)"),
        "secret": fields.Text(required=False, help="Secret to use when nobody can be asked"),
    }

    def run(self, ctx, value=None):
        c = self.config
        if c.kind == "choice" and not c.options:
            raise ValueError("kind 'choice' needs options")
        if ctx.can_ask:
            answer = ctx.ask(c.prompt, c.kind, c.options, c.default)
            if answer is None:
                raise RuntimeError("no answer was given")
        elif c.secret:
            answer = ctx.home.get_secret(c.secret)
        elif c.default is not None:
            answer = c.default
        else:
            raise RuntimeError("nobody can answer in this run; set a default or a secret for unattended runs")
        if c.kind == "yes_no":
            answer = answer if isinstance(answer, bool) else str(answer).strip().lower() in ("yes", "y", "true", "1")
        elif c.kind == "choice" and answer not in c.options:
            raise ValueError(f"answer {answer!r} is not one of {c.options}")
        elif c.kind == "password" or c.secret:
            answer = SecretStr(answer)
        return {"answer": answer}


class ForEach(Block):
    type_id = "logic.for_each"
    title = "For Each"
    description = 'Runs another flow once for every item in a list, for example once per server or once per region.'
    category = "Logic"
    inputs = {"items": ports.Any()}
    outputs = {"results": ports.Any()}
    config = {
        "flow": fields.Path(help="The flow to run once per item"),
        "param": fields.Text(help="The parameter of that flow that receives the item"),
        "stop_on_failure": fields.Bool(default=False),
    }

    def run(self, ctx, items):
        if not isinstance(items, (list, tuple)):
            raise TypeError(f"input 'items' must be a list, got {type(items).__name__}")
        flow = load_flow(Path(self.config.flow).resolve())  # recorded as runs of that flow, as when it is run itself
        if self.config.param not in flow.params:
            raise ValueError(f"{self.config.flow} has no parameter '{self.config.param}'")
        results = []
        for item in items:
            ctx.check_cancelled()
            result = run_flow(flow, ctx.registry, ctx.home, {self.config.param: item},
                              cancel=ctx.cancel_event, as_of=ctx.as_of)
            ctx.log(f"{self.config.param}={item}: run #{result.run_id} {result.status}")
            results.append({"item": item, "run_id": result.run_id, "status": result.status})
            if result.status != "success" and self.config.stop_on_failure:
                break
        failed = [r for r in results if r["status"] != "success"]
        if failed:
            raise RuntimeError(f"{len(failed)} of {len(results)} run(s) did not succeed: "
                               + ", ".join(f"{r['item']} (run #{r['run_id']})" for r in failed[:10]))
        return {"results": results}


class Inspect(Block):
    type_id = "logic.inspect"
    title = "Inspect"
    description = ("For checking your flow: put it between two blocks to see the data passing through "
                   "(in the Log and in Data preview). It passes the data on unchanged.")
    category = "Debug"
    inputs = {"value": ports.Any(required=False, any_of=True)}
    outputs = {"value": ports.Any()}
    config = {
        "message": fields.Code("f-string", required=False,
                               help="Optional text written like a Python f-string, e.g. "
                                    "Healthy: {value['healthy']} {value['problems']}. Empty shows the whole value."),
    }

    def run(self, ctx, value=None):
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
            ctx.log(text)
        else:
            ctx.log(preview(value))
        return {"value": value}
