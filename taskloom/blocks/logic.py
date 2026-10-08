"""Logic blocks: Python code, conditions and waiting."""

from __future__ import annotations

import polars as pl

from ..block import Block, fields, ports
from ..expr import evaluate
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
