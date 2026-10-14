"""Runs a flow: blocks in topological order, with retries, timeouts, error ports and cancellation."""

from __future__ import annotations

import datetime as dt
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import params as P
from .block import AFTER, DONE, all_ports
from .flow import ERROR_PORT, Flow, FlowError, block_config, order, run_clock, validate
from .config import REVEALED
from .history import History
from .table import Table

# How long a block gets to stop on its own after a timeout or cancel before it is abandoned.
STOP_GRACE_SECONDS = 2.0


class Cancelled(Exception):
    pass


class SecretStr(str):
    """A string that never shows its value in logs, summaries or run history."""

    def __new__(cls, value):
        REVEALED.add(str.__str__(value))
        return super().__new__(cls, value)

    def __repr__(self):
        return "'***'"


def redact(value):
    """Replace every secret seen in this process, wherever it appears (also inside text that code built
    from it, like f"user:{password}"), before anything is logged, stored or printed."""
    if isinstance(value, str):
        for secret in REVEALED:
            if len(secret) >= 4 and secret in value:
                value = value.replace(secret, "***")
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class Context:
    """What a block's run() receives as `ctx`."""

    def __init__(self, *, block_id, attempt, params, functions, workspace, home, emit, run_cancel, attempt_cancel,
                 answers=None, registry=None):
        self.block_id = block_id
        self.attempt = attempt
        self.params = dict(params)
        self.functions = functions  # the expression functions (today(), bdays(), …) for this run
        self.workspace = workspace
        self.home = home
        self._emit = emit
        self._run_cancel = run_cancel
        self._attempt_cancel = attempt_cancel
        self._answers = answers
        self.registry = registry  # for blocks that run other flows

    def log(self, message: str, level: str = "INFO"):
        self._emit({"event": "log", "block": self.block_id, "level": level, "message": str(message)})

    def warn(self, message: str):
        self.log(message, "WARNING")

    def new_path(self, name: str) -> Path:
        """A path inside this attempt's private workspace folder."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        return self.workspace / name

    def connection(self, name: str) -> dict:
        conns = self.home.connections()
        if name not in conns:
            raise KeyError(f"no connection named '{name}' in connections.yaml")
        return self.home.resolve_secrets(conns[name])

    @property
    def can_ask(self) -> bool:
        """True when someone can answer questions (a manual run from the editor)."""
        return self._answers is not None

    def ask(self, prompt: str, kind: str = "text", options=None, default=None):
        """Ask the person running the flow and wait for the answer (None if they decline)."""
        if self._answers is None:
            raise RuntimeError("this run cannot ask questions")
        ask_id = uuid.uuid4().hex
        self._emit({"event": "ask", "block": self.block_id, "ask_id": ask_id, "prompt": prompt,
                    "kind": kind, "options": list(options or []), "default": default})
        return self._answers.wait(ask_id, self)

    @property
    def cancel_event(self) -> threading.Event:
        """Set when the whole run is cancelled; pass it to runs started from this block."""
        return self._run_cancel

    @property
    def cancelled(self) -> bool:
        return self._run_cancel.is_set() or self._attempt_cancel.is_set()

    def check_cancelled(self):
        if self.cancelled:
            raise Cancelled()

    def wait(self, seconds: float):
        """Sleep, but stop early (raising Cancelled) if the run is cancelled or times out."""
        end = time.monotonic() + seconds
        while (remaining := end - time.monotonic()) > 0:
            self.check_cancelled()
            self._attempt_cancel.wait(min(0.1, remaining))
        self.check_cancelled()


@dataclass
class RunResult:
    run_id: int
    status: str  # success | failed | cancelled
    outputs: dict  # (block_id, port) -> value


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")


def preview(value, limit: int = 4000) -> str:
    """A readable look at any value: a table's first rows, text, or a list/dict as JSON."""
    import json

    import polars as pl

    if isinstance(value, Table):
        with pl.Config(tbl_rows=20, tbl_cols=20, fmt_str_lengths=60, tbl_width_chars=200):
            return f"{value.summary()}\n{value.head(20)}"
    if isinstance(value, SecretStr):
        return "***"
    if isinstance(value, (dict, list, tuple)):
        return redact(json.dumps(_masked(value), indent=2, default=str))[:limit]  # scrub, then cut
    return redact(str(value))[:limit]


def _masked(value):
    """A copy with every secret, however deeply nested, replaced by ***."""
    if isinstance(value, SecretStr):
        return "***"
    if isinstance(value, dict):
        return {("***" if isinstance(k, SecretStr) else str(k)): _masked(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_masked(v) for v in value]
    return value


def inline(value, limit: int = 300) -> str:
    """A value on one line, for error reports."""
    return " ".join(preview(value).split())[:limit]


def _summary(produced: dict) -> str:
    parts = []
    for port, value in produced.items():
        text = value.summary() if isinstance(value, Table) else redact(repr(value))  # scrub, then cut
        parts.append(f"{port}: {text[:200]}")
    return "; ".join(parts)


def run_flow(flow: Flow, registry, home, overrides: dict | None = None, sinks=(), cancel: threading.Event | None = None,
             answers=None, as_of: dt.datetime | None = None, trigger: str | None = None) -> RunResult:
    """Run a flow. `answers` (with a wait(ask_id, ctx) method) lets blocks ask the user questions.

    `trigger` is the Schedule block that started a scheduled run; None means a manual run.
    """
    errors, warnings = validate(flow, registry, home)
    if errors:
        raise FlowError(errors)
    _, funcs = run_clock(flow, home, as_of)
    values = P.resolve(flow.params, overrides or {}, funcs)
    cancel = cancel or threading.Event()

    history = History(home.history_db)
    run_id = history.start_run(flow.name, str(flow.path), values, _now())
    all_sinks = (history, *sinks)

    def emit(event: dict):
        event = redact({"event": event.pop("event"), "run_id": run_id, "ts": _now(), **event})
        for sink in all_sinks:
            sink(event)

    try:
        emit({"event": "run_started", "flow": flow.name, "params": {k: str(v) for k, v in values.items()}})
        for message in registry.warnings + warnings:
            emit({"event": "log", "level": "WARNING", "message": message})
        runner = _Runner(flow, registry, home, values, funcs, emit, cancel, home.runs_dir / str(run_id), answers)
        runner.fired = _fired_triggers(flow, registry, trigger)
        try:
            status = runner.run()
        except BaseException:
            # Never leave the run recorded as "running" (e.g. a forced second Ctrl+C).
            emit({"event": "run_finished", "status": "cancelled" if cancel.is_set() else "failed"})
            raise
        emit({"event": "run_finished", "status": status})
        _apply_retention(home, history)
        history.prune(int(home.settings()["history_days"]))
    finally:
        history.close()
    return RunResult(run_id, status, runner.outputs)


class _Runner:
    def __init__(self, flow, registry, home, values, funcs, emit, cancel, workspace, answers):
        self.flow, self.registry, self.home, self.answers = flow, registry, home, answers
        self.values, self.funcs, self.emit, self.cancel, self.workspace = values, funcs, emit, cancel, workspace
        self.outputs: dict = {}
        self.blocked: set[str] = set()  # failed, or skipped because something before them failed

    def run(self) -> str:
        unhandled_failure = False
        for block_id in order(self.flow):
            cls = self.registry.get(self.flow.blocks[block_id].type)
            if cls is not None and cls.is_trigger and block_id not in self.fired:
                self.emit({"event": "block_finished", "block": block_id, "status": "skipped"})
                continue
            if self.cancel.is_set():
                self.emit({"event": "block_finished", "block": block_id, "status": "cancelled"})
                continue
            incoming = [e for e in self.flow.edges if e.dst == block_id]
            # Nothing downstream of a failure runs: an edge from a normal output of a failed block (or of
            # a block skipped because of a failure) stops this block. Error outputs are the way out.
            if any(e.src in self.blocked and e.src_port != ERROR_PORT for e in incoming):
                self.blocked.add(block_id)
                self.emit({"event": "block_finished", "block": block_id, "status": "skipped"})
                continue
            in_ports = self._input_ports(self.flow.blocks[block_id])
            inputs, ready, after_failure = {}, True, False
            for name in dict.fromkeys(e.dst_port for e in incoming):
                port = in_ports.get(name)
                edges = [e for e in incoming if e.dst_port == name]
                delivered = [self.outputs[(e.src, e.src_port)] for e in edges if (e.src, e.src_port) in self.outputs]
                missing = [e for e in edges if (e.src, e.src_port) not in self.outputs]
                # Usually every connected edge must deliver; an any_of input needs just one (paths that
                # meet after an If, or the error outputs of several blocks going to one handler).
                if not delivered or (missing and not getattr(port, "any_of", False)):
                    ready = False
                    after_failure |= any(e.src in self.blocked for e in missing)
                    continue
                # Several edges into one input give a list (in edge order), one edge gives the value.
                inputs[name] = delivered if getattr(port, "many", False) or len(edges) > 1 else delivered[0]
            if not ready:
                if after_failure:  # it did not get its input because something before it failed
                    self.blocked.add(block_id)
                self.emit({"event": "block_finished", "block": block_id, "status": "skipped"})
                continue
            status, produced = self._run_block(self.flow.blocks[block_id], inputs)
            if status == "failed":
                self.blocked.add(block_id)
            for port, value in produced.items():
                self.outputs[(block_id, port)] = value
            handled = any(e.src == block_id and e.src_port == ERROR_PORT for e in self.flow.edges)
            if status == "failed" and not handled:
                unhandled_failure = True
        if self.cancel.is_set():
            return "cancelled"
        return "failed" if unhandled_failure else "success"

    def _input_ports(self, spec) -> dict:
        cls = self.registry.get(spec.type)
        try:
            return all_ports(cls, block_config(cls, spec))[0]
        except Exception:
            return {}  # the block reports its config problem when it runs

    def _run_block(self, spec, inputs) -> tuple[str, dict]:
        cls = self.registry.get(spec.type)
        try:
            config = block_config(cls, spec)
            for name, f in cls.config.items():
                if f.template and isinstance(config[name], str):
                    config[name] = P.render(config[name], self.values)
            in_ports, out_ports = cls.ports(config)
            for name, port in in_ports.items():
                if port.kind == "table" and name in inputs and not isinstance(inputs[name], Table):
                    raise TypeError(f"input '{name}' expects a table, got {type(inputs[name]).__name__}")
        except Exception as e:
            return self._fail(spec, 0, e, inputs)

        retry = spec.retry
        for attempt in range(1, retry.max_attempts + 1):
            self.emit({"event": "block_started", "block": spec.id, "attempt": attempt})
            attempt_cancel = threading.Event()
            ctx = Context(
                block_id=spec.id, attempt=attempt, params=self.values, functions=self.funcs,
                workspace=self.workspace / spec.id / f"attempt-{attempt}", home=self.home,
                emit=self.emit, run_cancel=self.cancel, attempt_cancel=attempt_cancel, answers=self.answers,
                registry=self.registry,
            )
            started = time.monotonic()
            try:
                own = inputs if AFTER in in_ports else {k: v for k, v in inputs.items() if k != AFTER}
                result = self._attempt(cls(config), ctx, own, spec.timeout, attempt_cancel)
                produced = self._check_outputs(result, out_ports, ctx)
                summary = _summary(produced)
            except Cancelled:
                self.emit({"event": "block_finished", "block": spec.id, "status": "cancelled", "attempts": attempt})
                return "cancelled", {}
            except Exception as e:
                if attempt == retry.max_attempts:
                    return self._fail(spec, attempt, e, inputs)
                delay = retry.delay_after(attempt)
                self.emit({"event": "log", "block": spec.id, "level": "ERROR",
                           "message": f"attempt {attempt}/{retry.max_attempts} failed: {type(e).__name__}: {e}"})
                self.emit({"event": "log", "block": spec.id, "level": "RETRY",
                           "message": f"waiting {delay:.3g} s before attempt {attempt + 1}/{retry.max_attempts}"})
                if self.cancel.wait(delay):
                    self.emit({"event": "block_finished", "block": spec.id, "status": "cancelled", "attempts": attempt})
                    return "cancelled", {}
                continue
            self.emit({"event": "block_finished", "block": spec.id, "status": "success", "attempts": attempt,
                       "duration": round(time.monotonic() - started, 3), "summary": summary,
                       "tables": {p: str(v.path) for p, v in produced.items() if isinstance(v, Table)},
                       "previews": {p: preview(v) for p, v in produced.items()}})
            if not cls.is_trigger:
                produced[DONE] = True  # for blocks connected done -> after
            return "success", produced
        raise AssertionError("unreachable")

    def _fail(self, spec, attempts, error, inputs) -> tuple[str, dict]:
        block_id = spec.id
        cls = self.registry.get(spec.type)
        # Scrubbed here too: the error goes on to error handlers, which may email it.
        # Blocks can attach a clearer report (Python Code: the code around the line, its variables).
        details = getattr(error, "details", None)
        message = redact(str(error) if details else f"{type(error).__name__}: {error}")
        tb = redact(details or "".join(traceback.format_exception(error)))
        report = [f"{block_id} ({cls.title if cls else spec.type}) failed: {message}"]
        incoming = [e for e in self.flow.edges if e.dst == block_id]
        if incoming:
            report.append("Inputs:")
            for e in incoming:
                if (e.src, e.src_port) in self.outputs:
                    value = inline(self.outputs[(e.src, e.src_port)])
                else:
                    value = "(nothing arrived)"
                report.append(f"  {e.dst_port} ← {e.src}.{e.src_port}: {value}")
        self.emit({"event": "log", "block": block_id, "level": "ERROR", "message": redact("\n".join(report)) + "\n" + tb})
        self.emit({"event": "block_finished", "block": block_id, "status": "failed", "attempts": attempts, "error": message})
        return "failed", {ERROR_PORT: {"block": block_id, "error": message, "traceback": tb, "attempts": attempts}}

    def _attempt(self, block, ctx, inputs, timeout, attempt_cancel):
        """Run block.run in a worker thread so timeouts and cancellation stay responsive."""
        result: dict = {}
        done = threading.Event()

        def target():
            try:
                result["value"] = block.run(ctx, **inputs)
            except BaseException as e:  # handed back to the engine thread
                result["error"] = e
            finally:
                done.set()

        threading.Thread(target=target, name=f"block-{ctx.block_id}", daemon=True).start()
        deadline = time.monotonic() + timeout if timeout else None
        while not done.wait(0.05):
            if self.cancel.is_set():
                attempt_cancel.set()
                done.wait(STOP_GRACE_SECONDS)
                raise Cancelled()
            if deadline is not None and time.monotonic() > deadline:
                attempt_cancel.set()
                done.wait(STOP_GRACE_SECONDS)
                raise TimeoutError(f"timed out after {timeout:g} s")
        if "error" in result:
            raise result["error"]
        return result["value"]

    def _check_outputs(self, result, out_ports, ctx) -> dict:
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise TypeError(f"run() must return a dict of outputs, got {type(result).__name__}")
        produced = {}
        for port, value in result.items():
            if port not in out_ports:
                raise ValueError(f"run() returned unknown output '{port}' (outputs: {', '.join(out_ports) or 'none'})")
            if Table.is_frame(value):
                value = Table.from_frame(value, ctx.new_path(f"{port}.parquet"))
            if out_ports[port].kind == "table" and not isinstance(value, Table):
                raise TypeError(f"output '{port}' must be a table, got {type(value).__name__}")
            produced[port] = value
        return produced


def _fired_triggers(flow: Flow, registry, trigger: str | None) -> set[str]:
    """Start blocks that fire: the Schedule block of a scheduled run; Manual Start blocks of a manual
    run, or every start block when the flow has no Manual Start (so pressing Run always runs it)."""
    starts = {b: registry.get(s.type) for b, s in flow.blocks.items()}
    starts = {b: cls for b, cls in starts.items() if cls is not None and cls.is_trigger}
    if trigger is not None:
        return {trigger} & set(starts)
    manual = {b for b, cls in starts.items() if cls.type_id == "trigger.manual"}
    return manual or set(starts)


def _apply_retention(home, history: History):
    """Delete the workspace folders of old runs, keeping the newest `keep_runs`.

    Runs still in progress (possibly in other processes) are never touched.
    """
    keep = int(home.settings()["keep_runs"])
    if not home.runs_dir.is_dir():
        return
    runs = sorted((p for p in home.runs_dir.iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name))
    running = history.running_ids()  # read after listing, so a run that just started is included
    old = [p for p in runs[: max(len(runs) - keep, 0)] if int(p.name) not in running]
    for path in old:
        shutil.rmtree(path, ignore_errors=True)
