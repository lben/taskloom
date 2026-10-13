"""Logic blocks: Python code, conditions and waiting."""

from __future__ import annotations

import polars as pl

from ..block import Block, fields, ports
from ..expr import evaluate
from ..engine import SecretStr, _masked, preview, run_flow
from ..flow import load_flow
from ..table import Table


class PythonCode(Block):
    type_id = "logic.python"
    title = "Python Code"
    description = 'Runs a few lines of Python that you write. Use it when no other block does what you need.'
    category = "Logic"
    config = {
        "inputs": fields.Names(default=["input"], help="Variables your code receives."),
        "outputs": fields.Names(default=["output"], help="Variables your code sets; unset ones are not produced."),
        "code": fields.Code("python"),
        "use_environment": fields.Bool(default=False, help="Run in your own Python environment, with the packages "
                                                           "you installed (Tools → Python environment)"),
    }

    @classmethod
    def ports(cls, config):
        # Inputs can take several edges (e.g. the errors of many blocks); then they receive a list.
        return ({n: ports.Any(required=False, any_of=True) for n in config["inputs"]},
                {n: ports.Any() for n in config["outputs"]})

    def run(self, ctx, **inputs):
        if self.config.use_environment:
            from ..pyenv import run_code

            values = {name: inputs.get(name) for name in self.config.inputs}
            return run_code(ctx, self.config.code, values, self.config.outputs)
        namespace = {"ctx": ctx, "params": ctx.params, "pl": pl, "Table": Table}
        namespace.update({name: inputs.get(name) for name in self.config.inputs})
        exec(compile(self.config.code, f"<block {ctx.block_id}>", "exec"), namespace)
        # Outputs your code does not assign are not produced, so blocks after them are skipped.
        return {name: namespace[name] for name in self.config.outputs if name in namespace}


class If(Block):
    type_id = "logic.if"
    title = "If"
    description = "Checks a condition and sends the data down the 'true' path or the 'false' path, so different blocks run in each case."
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
        flow = load_flow(self.config.flow)
        if self.config.param not in flow.params:
            raise ValueError(f"{self.config.flow} has no parameter '{self.config.param}'")
        results = []
        for item in items:
            ctx.check_cancelled()
            result = run_flow(flow, ctx.registry, ctx.home, {self.config.param: item},
                              cancel=ctx.cancel_event, as_of=ctx.functions["now"]())
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
            try:
                text = eval(compile("f" + repr(self.config.message), f"<block {ctx.block_id}>", "eval"), namespace)
            except Exception as e:
                raise ValueError(f"the message could not be filled in: {type(e).__name__}: {e}") from None
            ctx.log(text)
        else:
            ctx.log(preview(value))
        return {"value": value}
