"""Logic blocks: Python code, conditions and waiting."""

from __future__ import annotations

import polars as pl

from ..block import Block, fields, ports
from ..expr import evaluate
from ..engine import SecretStr
from ..table import Table


class PythonCode(Block):
    type_id = "logic.python"
    title = "Python Code"
    category = "Logic"
    config = {
        "inputs": fields.Names(default=["input"], help="Variables your code receives."),
        "outputs": fields.Names(default=["output"], help="Variables your code sets; unset ones are not produced."),
        "code": fields.Code("python"),
    }

    @classmethod
    def ports(cls, config):
        return ({n: ports.Any(required=False) for n in config["inputs"]},
                {n: ports.Any() for n in config["outputs"]})

    def run(self, ctx, **inputs):
        namespace = {"ctx": ctx, "params": ctx.params, "pl": pl, "Table": Table}
        namespace.update({name: inputs.get(name) for name in self.config.inputs})
        exec(compile(self.config.code, f"<block {ctx.block_id}>", "exec"), namespace)
        # Outputs your code does not assign are not produced, so blocks after them are skipped.
        return {name: namespace[name] for name in self.config.outputs if name in namespace}


class If(Block):
    type_id = "logic.if"
    title = "If"
    category = "Logic"
    inputs = {"value": ports.Any(required=False)}
    outputs = {"true": ports.Any(), "false": ports.Any()}
    config = {"condition": fields.Code("expression", help="e.g. len(value) > 0 or is_business_day(today())")}

    def run(self, ctx, value=None):
        result = evaluate(self.config.condition, {**ctx.functions, **ctx.params, "value": value})
        return {"true" if result else "false": value}


class Wait(Block):
    type_id = "logic.wait"
    title = "Wait"
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
